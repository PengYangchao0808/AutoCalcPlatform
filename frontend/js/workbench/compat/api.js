
// Fast default budget for local endpoints; SFTP-backed remote endpoints
// routinely exceed it (Pi/remote-cluster round trips), so they opt into the
// remote budget via apiRemote().
var API_TIMEOUT_MS = 8000;
var API_REMOTE_TIMEOUT_MS = 60000;

async function api(path, opts) {
  opts = opts || {};
  // Edit mode intercepts job creation at this single chokepoint: every
  // submit branch (simple/batch/stage/PES/NMR) funnels POST /jobs through
  // here, so ACPJobEditor can collect the built payload and run the
  // preview/summary flow instead of creating a duplicate task.
  if (path === "/jobs" && opts && opts.method === "POST" &&
      window.ACPJobEditor && ACPJobEditor.isActive()) {
    var _intercepted = {};
    try { _intercepted = JSON.parse(opts.body || "{}"); } catch (e) {}
    return ACPJobEditor.interceptJobCreate(_intercepted);
  }
  var result = await apiRequest(API_BASE + path, opts);
  if (opts.method && opts.method !== "GET") invalidateTaskViewMetadata();
  return result;
}

ACPClient.transport.init({
  defaultTimeoutMs: API_TIMEOUT_MS,
  remoteTimeoutMs: API_REMOTE_TIMEOUT_MS,
  translate: t,
});
if (window.ACPClient && window.ACPClient.events) {
  // R2: single URL-base injection point — the client event-stream module never
  // hardcodes an API prefix; the page owns API_BASE (compat/shared-state.js).
  ACPClient.events.init({ baseUrl: API_BASE });
}
if (window.ACPClient && window.ACPClient.systemEndpoints) {
  // T22: single init chokepoint for the System & Resources facade. `v1` owns
  // API_BASE (/api/v1); `direct` is the transport (literal /api/backends).
  ACPClient.systemEndpoints.init({ v1: api, direct: apiRequest, baseUrl: API_BASE });
}
if (window.ACPClient && window.ACPClient.taskView) ACPClient.taskView.init({ v1: api, v2: apiV2 });
if (window.ACPClient && window.ACPClient.jobDetail) {
  // R1: single write point for the legacy-endpoint pin — the client decides
  // on structured err.status (404/405 only) and syncs the page global here.
  ACPClient.jobDetail.init({
    v1: api,
    v1Remote: apiRemote,
    onLegacyPin: function () { jobDetailEndpointMissing = true; },
  });
}
// NOTE: ACPClient.jobCommands has exactly ONE init owner — features/queue.js
// (R3a, loads after this file). v1 command receipts still invalidate task-view
// metadata through the api()/apiV2() non-GET chokepoints below, because the
// client commands run through the page api adapters injected by queue.js.

async function apiRequest(url, opts) {
  return ACPClient.transport.request(url, opts);
}

function apiRemote(path, opts) {
  opts = opts || {};
  if (opts.timeoutMs == null) opts.timeoutMs = API_REMOTE_TIMEOUT_MS;
  return api(path, opts);
}

// T4: v2 API helper — same error handling as api(), fetches "/api/v2" + path.
// api() and API_BASE are NOT changed (40+ v1 call sites depend on them).
// opts.timeoutMs overrides the 8s local default; apiRequest converts an own
// timeout into a localized Error, never a raw AbortError (root ANTI #28).
async function apiV2(path, opts) {
  var result = await apiRequest("/api/v2" + path, opts);
  if (opts && opts.method && opts.method !== "GET") invalidateTaskViewMetadata();
  return result;
}

// T6 (R6): version-aware remote wrapper for v2 reads that may trigger a
// server-side on-demand remote fetch (/results, /tree, /files/*,
// /structures/*, /frequencies/*). Keeps the /api/v2 namespace — never routed
// through the v1-pinned apiRemote() — and shares apiRequest's error
// conversion with the 60s remote budget (root ANTI #28).
function apiV2Remote(path, opts) {
  return apiV2(path, Object.assign({}, opts || {}, { timeoutMs: API_REMOTE_TIMEOUT_MS }));
}


// ── Status and formatting helpers ──
function formatDateTime(value) {
  if (!value) return t("common.none");
  try {
    return new Date(value).toLocaleString(currentLang === "zh-CN" ? "zh-CN" : "en-US");
  } catch (e) {
    return String(value);
  }
}

function getStatusClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "completed") return "completed";
  if (s === "failed") return "failed";
  if (s === "running") return "running";
  if (s === "waiting_review") return "waiting_review";
  if (s === "queued" || s === "starting" || s === "cancelling" || s === "pending") return s;
  if (s === "paused") return "paused";
  if (s === "cancelled") return "cancelled";
  return "idle";
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  badge.className = "badge " + getStatusClass(status);
  badge.textContent = translateStatus(status);
  return badge;
}

function normalizedProgress(job) {
  if (!job) return 0;
  if (typeof job.progress === "number") return Math.max(0, Math.min(100, Math.round(job.progress * 100)));
  var status = String(job.status || "").toLowerCase();
  if (status === "completed") return 100;
  return 0; // indeterminate — callers must check isProgressKnown()
}

function isProgressKnown(job) {
  return job && job.progress_state !== "indeterminate" && typeof job.progress === "number" && isFinite(job.progress);
}

function isProgressIndeterminate(job) {
  if (!job) return false;
  var status = String(job.status || "").toLowerCase();
  var active = status === "running" || status === "starting" || status === "pending" || status === "paused";
  return active && !isProgressKnown(job);
}

function isActiveJobStatus(status) {
  const s = String(status || "").toLowerCase();
  return s === "queued" || s === "starting" || s === "running" || s === "cancelling" || s === "pending" || s === "waiting_review" || s === "paused";
}


// ── Polling and disk usage ──
function startPolling(skipInitialJobs) {
  if (jobsPollTimer) { clearTimeout(jobsPollTimer); jobsPollTimer = null; }
  if (window._jobsPollTimer) { clearTimeout(window._jobsPollTimer); window._jobsPollTimer = null; }
  if (window._slowPollTimer) { clearTimeout(window._slowPollTimer); window._slowPollTimer = null; }
  if (window._logPollTimer) { clearTimeout(window._logPollTimer); window._logPollTimer = null; }
  if (window._midPollTimer) { clearTimeout(window._midPollTimer); window._midPollTimer = null; }
  jobsPollGeneration++;

  if (window.ACPClient && window.ACPClient.polling) {
    ACPClient.polling.init({
      pollJobsList: function () { return Promise.allSettled([loadStatus(), refreshJobs()]); },
      pollSelectedSummary: function () { return refreshSelectedJobSummary(); },
      pollEnergyGraph: function (lastJob) {
        var graphTaskActive = lastJob && (!lastJob.status || isActiveJobStatus(lastJob.status));
        var graphJobStatus = String((lastJob && lastJob.status) || "").toLowerCase();
        var graphNeedsFinal = lastJob && !graphTaskActive && energyGraphState.lastFetchedJobStatus !== graphJobStatus;
        if (activeViewerTab === "energy" && lastJob && (graphTaskActive || graphNeedsFinal) && !energyGraphState.loading) {
          return renderEnergyGraphWorkspace(lastJob, { silent: true });
        }
      },
      pollMechanismSection: function () {
        if (appSection === "mechanism") return refreshMechSectionStudies(true);
      },
      pollStructureViewer: function () {
        if (window.ACPStructureViewer && window.ACPStructureViewer.refreshIfChanged) {
          window.ACPStructureViewer.refreshIfChanged();
        }
      },
      pollLogTail: function (id) {
        return Promise.allSettled([pollLogTail(id), pollIrcOutputTail(id)]);
      },
      pollDiskUsage: function () { return loadDiskUsage(); },
      pollNodes: function () { return ACPWorkbench.controllers.systemResources.loadNodes(); },
      pollBackends: function () { return ACPWorkbench.controllers.systemResources.loadBackends(); },
      pollFileTree: function () { return refreshFileTreeIfActive(); },
    }, {
      isHidden: function () { return document.hidden; },
      getSelectedJobId: function () { return selectedJobId; },
      isDetailDrawerOpen: function () {
        var drawer = document.getElementById("detail-drawer");
        return !!(drawer && drawer.classList.contains("open"));
      },
      isSysresDrawerOpen: function () { return isSysresDrawerOpen(); },
      getActiveViewerTab: function () { return activeViewerTab; },
      getAppSection: function () { return appSection; },
      getLastSelectedJob: function () { return lastSelectedJob; },
      hasActiveJobs: function () {
        var scopeCounts = taskViewCache ? taskViewCache.counts : ((lastStatus && lastStatus.queue) || {});
        return Object.keys(scopeCounts || {}).some(function (status) {
          return isActiveJobStatus(status) && scopeCounts[status] > 0;
        });
      },
    });
    ACPClient.polling.start(skipInitialJobs);
  } else {
    // R2: polling has a single owner (§42.2) — a missing module is a script
    // load-order bug, not a reason to stop refreshing silently. Fail loudly
    // instead of resurrecting a legacy lane.
    console.error(
      "startPolling: window.ACPClient.polling is missing — " +
      "verify js/client/polling-strategy.js loads before js/workbench/compat/api.js. " +
      "All four refresh lanes are stopped."
    );
  }
}

// Phase 5B: local disk-usage indicator in the topbar.
async function loadDiskUsage() {
  var pill = document.getElementById("disk-pill");
  if (!pill) return;
  try {
    var data = await api("/maintenance/disk-usage");
    var pct = Math.round(data.percent_used || 0);
    pill.style.display = "";
    pill.textContent = "DISK " + pct + "%";
    var cls = "status-pill disk";
    if (pct >= 95) cls += " err";
    else if (pct >= 90) cls += " warn";
    pill.className = cls;
    var human = data.free_bytes != null ? formatBytes(data.free_bytes) : "--";
    pill.title = "本地磁盘使用率（Phase 5B）\n"
      + "使用 " + pct + "% · 剩余 " + human + "\n"
      + "作业数 " + (data.job_count || 0)
      + (data.cleanup_enabled ? "\n清理：已启用" : "\n清理：未启用");
  } catch (e) {
    pill.style.display = "none";
  }
}

function formatBytes(n) {
  if (!n || n <= 0) return "0 B";
  var units = ["B", "KiB", "MiB", "GiB", "TiB"];
  var s = n, i = 0;
  while (s >= 1024 && i < units.length - 1) { s /= 1024; i++; }
  return (i === 0 ? Math.round(s) : s.toFixed(1)) + " " + units[i];
}

