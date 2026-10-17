// ── Q9: job detail projection (GET /jobs/{id}/detail) ─────────────────────
// The detail endpoint may not exist on older servers; every consumer must
// degrade gracefully to the legacy GET /jobs/{id} shape (stages /
// artifacts_summary / error_detail / disk_state / recovery stay null and the
// UI falls back to status-based defaults).

function getJobDetail(jobId) {
  return jobDetailCache[String(jobId || "")] || null;
}

// GAP-9: frozen execution-version identity from task 5 — jobs-authoritative
// (job_id, attempt, revision) exposed by both the list and detail payloads.
// name_revision is organization naming only and must NEVER participate here.
// R1b: the pure logic is single-sourced in ACPWorkbench.controllers.jobDetail
// (loaded before this file); this page entry point stays same-name.
function jobExecutionIdentity(job) {
  // Frozen triple (job_id, attempt, revision) — delegated to the controller.
  return ACPWorkbench.controllers.jobDetail.jobExecutionIdentity(job);
}

function findJobRowInCache(jobId) {
  const key = String(jobId || "");
  if (!key) return null;
  for (var i = 0; i < jobsCache.length; i += 1) {
    if (String(jobsCache[i].id || jobsCache[i].job_id || "") === key) return jobsCache[i];
  }
  return null;
}

// True when a cached detail disagrees with the current list row on status or
// on the execution-version identity (same status but a different attempt /
// revision — e.g. a rerun completed between two polls).
// R1b: single-sourced in the controller as isDetailStaleFor.
function jobDetailCacheStaleFor(detail, row) {
  return ACPWorkbench.controllers.jobDetail.isDetailStaleFor(detail, row);
}

// Drop any cached detail whose status/attempt/revision disagrees with the
// fresh list row.  Rows absent from the current view (filtered out) are left
// alone — they cannot be compared.  Never refetches: poll ticks stay free of
// per-row detail requests; only user actions refetch (bounded at one each).
// R1b: the comparison/prune logic lives in the controller; the page wrapper
// keeps the side effect (recovery-cache revision bump) exactly where it is.
function pruneJobDetailCacheAgainstList() {
  var changed = ACPWorkbench.controllers.jobDetail.pruneAgainstList(jobDetailCache, jobsCache);
  if (changed) jobDetailCacheRevision += 1;
}

// The recovery matrix lives outside the list rows: any cache mutation that
// can change resolveJobRecovery() output must re-render the queue rows.
function renderQueueRowsForRecoveryChange() {
  var counts = (taskViewCache && taskViewCache.counts) || (lastStatus && lastStatus.queue) || {};
  renderQueue(counts);
}

// GAP-9: called by EVERY row action and batch operation (not only for the
// selected job).  Bumps the request generation so in-flight detail responses
// started before the action are rejected as late, and drops the cached entry.
function invalidateJobDetail(jobId) {
  var key = String(jobId || "");
  if (!key) return;
  jobDetailGenerations[key] = (jobDetailGenerations[key] || 0) + 1;
  if (window.ACPClient && window.ACPClient.jobDetail) {
    ACPClient.jobDetail.invalidateDetail(key);
  }
  if (jobDetailCache[key]) {
    delete jobDetailCache[key];
    jobDetailCacheRevision += 1;
  }
}

function getJobRecovery(job) {
  const id = job && (job.id || job.job_id) ? String(job.id || job.job_id) : "";
  const detail = getJobDetail(id);
  const recovery = detail ? detail.recovery : null;
  return isPlainObject(recovery) ? recovery : null;
}

// Status-based fallback mirroring the plan §4.4 matrix for when the server
// provides no recovery object (older backend / legacy /jobs/{id} path).
function isTerminalJobStatus(status) {
  const s = String(status || "").toLowerCase();
  return s === "completed" || s === "failed" || s === "cancelled";
}

// R1b: status-based default recovery is single-sourced in the controller;
// same-name page entry point kept.
function computeDefaultRecovery(job) {
  return ACPWorkbench.controllers.jobDetail.computeDefaultRecovery(job);
}

// Server recovery matrix wins; any missing boolean field falls back to the
// status-based default so a partial server payload never hides a button.
// R1b: the merge rules are single-sourced in the controller; the page only
// resolves the detail snapshot (its own projection) and delegates. Call sites
// (queue.js row actions, openDetailDrawer, job_editor.js) are unchanged.
function resolveJobRecovery(job) {
  const detail = getJobDetail(job && (job.id || job.job_id));
  return ACPWorkbench.controllers.jobDetail.resolveJobRecovery(job, detail);
}

function _acceptJobDetail(key, payload, generation) {
  if ((jobDetailGenerations[key] || 0) !== generation) return null;
  const row = findJobRowInCache(key);
  if (row && jobDetailCacheStaleFor(payload, row)) return null;
  const prev = jobDetailCache[key];
  jobDetailCache[key] = payload;
  const prevRec = prev && isPlainObject(prev.recovery) ? JSON.stringify(prev.recovery) : "";
  const nextRec = isPlainObject(payload.recovery) ? JSON.stringify(payload.recovery) : "";
  if (prevRec !== nextRec) {
    jobDetailCacheRevision += 1;
    renderQueueRowsForRecoveryChange();
  }
  return payload;
}

// Fetch the rich detail projection through the Client resource
// (ACPClient.jobDetail): GET /jobs/{id}/detail, with the legacy GET
// /jobs/{id} record wrap on HTTP 404/405 (the legacy-endpoint pin decision
// and the single jobDetailEndpointMissing write live in the client —
// compat/api.js wires onLegacyPin). R1: the client is the only fetch path;
// jobDetailCache below is a projection filled from client results only.
// Throws only when the job itself cannot be fetched (deleted / offline).
// GAP-9: returns null when the response belongs to an older request
// generation (late response released after a row action / rerun), when it
// disagrees with the current list row, or when the fetch failed — stale data
// is dropped, never cached and never returned to callers.
async function fetchJobDetail(jobId) {
  if (!jobId) return null;
  if (!(window.ACPClient && window.ACPClient.jobDetail)) return null;
  const key = String(jobId);
  const generation = jobDetailGenerations[key] || 0;
  const staleNow = function() { return (jobDetailGenerations[key] || 0) !== generation; };
  let detail = null;
  try {
    detail = await ACPClient.jobDetail.fetchDetail(key);
  } catch (e) {
    return null;
  }
  if (staleNow()) return null;
  return _acceptJobDetail(key, detail, generation);
}

// GAP-9: shared post-action refresh for row actions.  Invalidates the acted
// job's cached detail (also for unselected rows), refreshes the list, then
// refetches exactly ONE fresh detail so the server recovery matrix keeps
// deciding capability.  Poll ticks never call this — per-poll detail stays
// bounded (only the acted job is fetched, once per user action).
async function refreshJobDetailAfterAction(jobId) {
  invalidateJobDetail(jobId);
  await refreshJobs();
  if (String(selectedJobId || "") === String(jobId)) {
    await refreshSelectedJobDetail({ force: true });
  } else {
    try { await fetchJobDetail(jobId); } catch (e) { /* row falls back to status defaults until refetched */ }
  }
}

// R3 fix: refetch the selected job's detail on poll ticks / SSE terminal
// events. Re-renders only when something visible changed (status /
// updated_at / progress / current_stage) so the panel does not flicker.
async function refreshSelectedJobDetail(opts) {
  if (!selectedJobId) return;
  const jobId = String(selectedJobId);
  let detail = null;
  try {
    detail = await fetchJobDetail(jobId);
  } catch (e) {
    detail = null;
  }
  if (String(selectedJobId || "") !== jobId) return;
  if (!detail || !detail.job) {
    // Stale selection: job gone from the server AND from the visible queue
    // list (list membership alone is filtered by project) → clear the panel.
    const stillListed = jobsCache.some(function(entry) { return String(entry.id) === String(jobId); });
    if (!stillListed) clearStaleJobSelection();
    return;
  }
  if (String(selectedJobId || "") !== jobId) return; // selection moved mid-flight
  const job = detail.job;
  const prev = lastSelectedJob || {};
  const statusChanged = String(prev.status || "") !== String(job.status || "");
  let changed = !!(opts && opts.force);
  if (!changed) {
    changed = statusChanged ||
      String(prev.updated_at || "") !== String(job.updated_at || "") ||
      String(prev.progress || "") !== String(job.progress || "") ||
      String(prev.current_stage || "") !== String(job.current_stage || "") ||
      String(prev.stage_index || "") !== String(job.stage_index || "") ||
      String(prev.stage_total || "") !== String(job.stage_total || "") ||
      String(prev.stage_detail || "") !== String(job.stage_detail || "") ||
      String(prev.latest_event || "") !== String(job.latest_event || "");
  }
  if (!changed) return;
  lastRemoteNode = jobRemoteNodeName(job);
  selectedJobIsRemote = !!lastRemoteNode;
  updateInfoPanel(job);
  if (statusChanged) refreshNmrReport(job);
}

// Lightweight summary poll (GET /api/v1/jobs/{id}/summary). Used by the fast
// polling lane instead of the full /detail endpoint to reduce server load.
// The summary object has the same schema as one item of the /jobs list.
var _summaryPollInFlight = false;
async function refreshSelectedJobSummary() {
  if (!selectedJobId || _summaryPollInFlight) return;
  _summaryPollInFlight = true;
  try {
    var jobId = String(selectedJobId);
    var summary = null;
    try {
      summary = await ACPClient.taskView.fetchSummary(jobId);
    } catch (e) {
      // 404 or network error — ignore silently
      return;
    }
    if (!summary || String(selectedJobId || "") !== jobId) return;
    var contract = ACPWorkbench.contracts.taskView;
    if (contract.isOlderRow(summary, findJobRowInCache(jobId)) ||
        contract.isOlderRow(summary, lastSelectedJob)) return;
    // Merge into lastSelectedJob for change detection
    var prev = lastSelectedJob || {};
    var statusChanged = String(prev.status || "") !== String(summary.status || "");
    var changed = statusChanged ||
      String(prev.updated_at || "") !== String(summary.updated_at || "") ||
      String(prev.progress || "") !== String(summary.progress || "") ||
      String(prev.current_stage || "") !== String(summary.current_stage || "") ||
      String(prev.stage_index || "") !== String(summary.stage_index || "") ||
      String(prev.stage_total || "") !== String(summary.stage_total || "") ||
      String(prev.stage_detail || "") !== String(summary.stage_detail || "") ||
      String(prev.latest_event || "") !== String(summary.latest_event || "") ||
      String(prev.display_method || "") !== String(summary.display_method || "") ||
      JSON.stringify(prev.live_status || null) !== JSON.stringify(summary.live_status || null);
    lastSelectedJob = summary;
    if (changed) {
      lastRemoteNode = jobRemoteNodeName(summary);
      selectedJobIsRemote = !!lastRemoteNode;
      updateInfoPanel(summary);
      if (statusChanged) refreshNmrReport(summary);
    }
  } catch (e) {
    // ignore
  } finally {
    _summaryPollInFlight = false;
  }
}

function clearStaleJobSelection() {
  // R1: end the selection session so an in-flight select() stops at its next
  // token check instead of repopulating the cleared panels.
  if (window.ACPWorkbench && window.ACPWorkbench.controllers && window.ACPWorkbench.controllers.taskSession) {
    ACPWorkbench.controllers.taskSession.clear();
  }
  selectedJobId = "";
  selectedJobIsRemote = false;
  lastRemoteNode = "";
  lastSelectedJob = null;
  lastFileListing = [];
  folderCache = {};
  folderState = {};
  lastPinnedItems = [];
  logCursor = { stdout: 0, stderr: 0, irc: 0, jobId: "" };
  updateInfoPanel(null);
  renderFileTree([], "");
  renderLogPlaceholder();
  clearViewer();
  if (activeSSE) {
    activeSSE.close();
    activeSSE = null;
  }
  highlightQueueRow("");
}

// Transient inline message in the info panel (success feedback or 409
// guidance text returned by pause/unpause/continue/rerun). Auto-expires.
function setDetailActionMessage(jobId, text, kind) {
  detailActionMessage = { jobId: String(jobId || ""), kind: kind || "info", text: String(text || ""), at: Date.now() };
  if (lastSelectedJob) updateInfoPanel(lastSelectedJob);
  setTimeout(function() {
    if (detailActionMessage.jobId && Date.now() - detailActionMessage.at >= 9500) {
      detailActionMessage = { jobId: "", kind: "", text: "", at: 0 };
      if (lastSelectedJob) updateInfoPanel(lastSelectedJob);
    }
  }, 10000);
}

// Distinguish "endpoint missing on this server" (older backend) from a real
// operation failure so the guidance shown to the user stays meaningful.
function describeJobOpError(err) {
  const msg = String(err && err.message || "");
  if (/not found|http 404|http 405/i.test(msg)) return t("job.op_unavailable");
  return msg || t("common.unknown");
}

function formatDuration(fromIso, toIso) {
  if (!fromIso) return "";
  const start = new Date(fromIso).getTime();
  if (isNaN(start)) return "";
  const end = toIso ? new Date(toIso).getTime() : Date.now();
  if (isNaN(end) || end < start) return "";
  let sec = Math.round((end - start) / 1000);
  if (sec < 60) return sec + "s";
  let min = Math.floor(sec / 60);
  sec = sec % 60;
  if (min < 60) return min + "m " + sec + "s";
  let hours = Math.floor(min / 60);
  min = min % 60;
  if (hours < 24) return hours + "h " + min + "m";
  const days = Math.floor(hours / 24);
  hours = hours % 24;
  return days + "d " + hours + "h";
}

function buildDetailCard(titleText) {
  const card = document.createElement("div");
  card.className = "detail-card";
  if (titleText) {
    const title = document.createElement("div");
    title.className = "detail-card-title";
    title.textContent = titleText;
    card.appendChild(title);
  }
  return card;
}

// ── Horizontal workflow timeline (todo 13) ──
// Renders into a container (the .v-timeline-slot placeholder).
// Data: job.live_status.stage_index (1-based), stage_total, current_stage.
// Stage names from detail cache → t("stage."+key) → generic "阶段 N".
function renderWorkflowTimeline(container, job) {
  container.innerHTML = "";
  if (!job) return;
  var ls = job.live_status || null;
  var stageTotal = (ls && ls.stage_total) || job.stage_total || 0;
  if (!stageTotal || stageTotal < 1) return; // hide when stage_total missing

  var stageIndex = (ls && ls.stage_index) || job.stage_index || 0;
  var currentKey = job.current_stage || "";

  // ── Resolve stage names: detail cache → t("stage.key") → "阶段 N" ──
  var stageNames = [];
  var detail = getJobDetail(job.id || job.job_id || "");
  var detailStages = (detail && Array.isArray(detail.stages)) ? detail.stages : [];
  var stageOrder = Array.isArray(job.stage_order) ? job.stage_order.slice() : [];
  if (!stageOrder.length && detailStages.length !== stageTotal && job.spec && job.spec.workflow === "BatchOptimize") {
    stageOrder = detailStages.filter(function(stage) { return stage.stage_name !== "prepare" && stage.stage_name !== "finalize"; }).map(function(stage) { return stage.stage_name; });
  }
  for (var i = 0; i < stageTotal; i++) {
    var name = "";
    var stageId = stageOrder[i];
    var detailStage = stageId ? detailStages.find(function(stage) { return (stage.stage_name || stage.name) === stageId; }) : detailStages[i];
    if (detailStage) {
      name = detailStage.label || translateStageName(detailStage.stage_name || detailStage.name, "");
    }
    if (!name && stageId) name = translateStageName(stageId, "");
    if (!name && i === stageIndex - 1 && currentKey) {
      name = translateStageName(currentKey, (ls && ls.stage_label) || "");
    }
    if (!name) {
      // Try using stage_index-based heuristic (not reliable; last resort)
      name = t("common.stage") + " " + (i + 1);
    }
    stageNames.push(name);
  }
  // Current stage label for below-node display
  var currentLabel = "";
  if (stageIndex >= 1 && stageIndex <= stageTotal) {
    currentLabel = stageNames[stageIndex - 1];
  }

  // ── Build header: title + arrows ──
  var header = document.createElement("div");
  header.className = "stage-timeline-header";
  var title = document.createElement("div");
  title.className = "stage-timeline-title";
  title.innerHTML = t("timeline.workflow") + " <span class=\"tl-idx\">" + stageIndex + " / " + stageTotal + "</span>";
  header.appendChild(title);
  var arrows = document.createElement("div");
  arrows.className = "stage-timeline-arrows";
  var leftBtn = document.createElement("button");
  leftBtn.type = "button";
  leftBtn.textContent = "\u2039";
  leftBtn.title = t("timeline.scroll_left");
  leftBtn.setAttribute("aria-label", t("timeline.scroll_left"));
  var rightBtn = document.createElement("button");
  rightBtn.type = "button";
  rightBtn.textContent = "\u203A";
  rightBtn.title = t("timeline.scroll_right");
  rightBtn.setAttribute("aria-label", t("timeline.scroll_right"));
  arrows.appendChild(leftBtn);
  arrows.appendChild(rightBtn);
  header.appendChild(arrows);
  container.appendChild(header);

  // ── Build viewport > track ──
  var viewport = document.createElement("div");
  viewport.className = "stage-timeline-viewport";
  var track = document.createElement("div");
  track.className = "stage-timeline-track";

  // Status labels for aria
  var statusLabels = {
    completed: t("status.completed"),
    running: t("status.running"),
    paused: t("status.paused"),
    failed: t("status.failed"),
    pending: t("status.pending")
  };

  for (var i = 0; i < stageTotal; i++) {
    // Connector (before every node except first)
    if (i > 0) {
      var conn = document.createElement("div");
      conn.className = "tl-connector";
      if (i < stageIndex) conn.classList.add("completed");
      else if (i === stageIndex) conn.classList.add("running");
      track.appendChild(conn);
    }
    // Node
    var node = document.createElement("div");
    node.className = "tl-node";
    var nodeStatus;
    if (i + 1 < stageIndex) {
      nodeStatus = "completed";
    } else if (i + 1 === stageIndex) {
      var jobCls = getStatusClass(job.status);
      nodeStatus = jobCls === "paused" ? "paused" : (jobCls === "completed" ? "completed" : "running");
    } else {
      nodeStatus = "pending";
    }
    // Override with actual terminal states
    if (job.status === "failed" && i + 1 === stageIndex) nodeStatus = "failed";
    node.classList.add(nodeStatus);

    var dot = document.createElement("div");
    dot.className = "tl-dot";
    node.appendChild(dot);

    // Current stage label below node
    if (i + 1 === stageIndex && currentLabel) {
      var lbl = document.createElement("div");
      lbl.className = "tl-label-below";
      lbl.textContent = currentLabel;
      lbl.title = currentLabel;
      node.appendChild(lbl);
    }

    // Accessibility
    var ariaStatus = statusLabels[nodeStatus] || nodeStatus;
    node.setAttribute("aria-label", stageNames[i] + ", " + ariaStatus);
    node.setAttribute("title", stageNames[i] + " — " + ariaStatus);

    track.appendChild(node);
  }
  viewport.appendChild(track);
  container.appendChild(viewport);

  // ── Scroll positioning: center current node at ~60% of viewport ──
  requestAnimationFrame(function() {
    var currentNode = track.querySelectorAll(".tl-node")[stageIndex - 1];
    if (!currentNode || !viewport.offsetWidth) return;
    var nodeCenter = currentNode.offsetLeft + currentNode.offsetWidth / 2;
    var targetScroll = nodeCenter - viewport.offsetWidth * 0.6;
    viewport.scrollLeft = Math.max(0, targetScroll);
  });

  // ── Arrow button handlers ──
  leftBtn.addEventListener("click", function() {
    viewport.scrollBy({ left: -120, behavior: "smooth" });
  });
  rightBtn.addEventListener("click", function() {
    viewport.scrollBy({ left: 120, behavior: "smooth" });
  });
}

// Three-layer status panel (task-12 rewrite).
// Section 1: task summary (name, badge, method, command, remark).
// Section 2: live computation status (metrics, progress, elapsed).
// Section 3: workflow timeline (task-13).
function renderInfoInto(container, job) {
  container.innerHTML = "";
  if (!job) {
    var empty = document.createElement("div");
    empty.className = "placeholder";
    empty.textContent = t("info.empty");
    container.appendChild(empty);
    return;
  }
  var jobId = String(job.id || job.job_id || "");
  var liveStatus = job.live_status || null;

  // ── Section 1: Task Summary ──
  var sec1 = document.createElement("div");
  sec1.className = "v-panel-section v-summary-section";
  sec1.setAttribute("data-testid", "summary-section");

  // Task name with rename button
  var taskNameWrap = document.createElement("div");
  taskNameWrap.className = "v-task-name-wrap";
  taskNameWrap.style.cssText = "display:flex;align-items:center;gap:6px;";
  var taskName = document.createElement("div");
  taskName.className = "v-task-name";
  taskName.id = "v-task-name";
  taskName.textContent = displayTaskName(job) || String(job.id || "");
  taskName.title = taskName.textContent;
  taskNameWrap.appendChild(taskName);
  var renameBtn = document.createElement("button");
  renameBtn.className = "mini-btn icon-btn btn-rename-task";
  renameBtn.textContent = "\u270e";
  renameBtn.title = t("queue.rename");
  renameBtn.setAttribute("aria-label", t("queue.rename"));
  renameBtn.addEventListener("click", function(event) {
    event.stopPropagation();
    openTaskRenameModal(job);
  });
  taskNameWrap.appendChild(renameBtn);
  sec1.appendChild(taskNameWrap);

  // Summary grid: status / method / command / remark
  var grid = document.createElement("div");
  grid.className = "v-summary-grid";

  // Status badge
  var statusRow = document.createElement("div");
  statusRow.className = "v-summary-row";
  var statusLbl = document.createElement("span");
  statusLbl.className = "v-summary-label";
  statusLbl.textContent = t("info.status");
  var statusVal = document.createElement("span");
  statusVal.className = "v-summary-value";
  statusVal.appendChild(createStatusBadge(job.status));
  statusRow.appendChild(statusLbl);
  statusRow.appendChild(statusVal);
  grid.appendChild(statusRow);

  // Method line: display_method → fallback spec.method → unspecified
  var methodText = "";
  if (job.display_method) {
    methodText = String(job.display_method);
  } else if (job.spec && job.spec.method) {
    var m = job.spec.method;
    if (m.method) methodText = String(m.method);
    if (m.basis) methodText += " / " + String(m.basis);
  }
  if (!methodText) methodText = t("common.unspecified");
  // Append stage_label parenthetical
  var stageParen = "";
  if (liveStatus && liveStatus.stage_label) {
    stageParen = String(liveStatus.stage_label);
  } else if (job.current_stage) {
    stageParen = translateStageName(job.current_stage, job.stage_label);
  }
  if (stageParen) methodText += " (" + stageParen + ")";

  var methodRow = document.createElement("div");
  methodRow.className = "v-summary-row";
  var methodLbl = document.createElement("span");
  methodLbl.className = "v-summary-label";
  methodLbl.textContent = t("summary.method");
  var methodVal = document.createElement("span");
  methodVal.className = "v-summary-value";
  methodVal.textContent = methodText;
  methodRow.appendChild(methodLbl);
  methodRow.appendChild(methodVal);
  grid.appendChild(methodRow);

  // Run command with copy button (only in non-mobile / detail context)
  if (job.run_command) {
    var cmdRow = document.createElement("div");
    cmdRow.className = "v-summary-row";
    var cmdLbl = document.createElement("span");
    cmdLbl.className = "v-summary-label";
    cmdLbl.textContent = t("summary.command");
    var cmdVal = document.createElement("span");
    cmdVal.className = "v-summary-value v-summary-command";
    cmdVal.textContent = String(job.run_command);
    cmdVal.title = String(job.run_command);
    var copyBtn = document.createElement("button");
    copyBtn.className = "v-copy-btn";
    copyBtn.textContent = "\u2398";
    copyBtn.title = t("summary.copy");
    copyBtn.addEventListener("click", function() {
      try { navigator.clipboard.writeText(String(job.run_command)); copyBtn.textContent = "\u2713"; setTimeout(function() { copyBtn.textContent = "\u2398"; }, 1500); } catch (e) { /* ignore */ }
    });
    cmdRow.appendChild(cmdLbl);
    cmdRow.appendChild(cmdVal);
    cmdRow.appendChild(copyBtn);
    grid.appendChild(cmdRow);
  }

  // Remark
  var remarkText = "";
  if (job.spec && job.spec.remark) remarkText = String(job.spec.remark).trim();
  if (remarkText) {
    var remRow = document.createElement("div");
    remRow.className = "v-summary-row";
    var remLbl = document.createElement("span");
    remLbl.className = "v-summary-label";
    remLbl.textContent = t("summary.remark");
    var remVal = document.createElement("span");
    remVal.className = "v-summary-value";
    remVal.textContent = remarkText;
    remRow.appendChild(remLbl);
    remRow.appendChild(remVal);
    grid.appendChild(remRow);
  }

  sec1.appendChild(grid);
  container.appendChild(sec1);

  // ── Section 2: Live Computation Status ──
  var sec2 = document.createElement("div");
  sec2.className = "v-panel-section v-live-section";
  sec2.setAttribute("data-testid", "live-section");

  if (liveStatus && liveStatus.metrics && liveStatus.metrics.length > 0) {
    // Metrics row: ≤3 metrics, vertical separator, first item prominent
    var metricsRow = document.createElement("div");
    metricsRow.className = "v-metrics-row";
    var metricList = liveStatus.metrics.slice(0, 3);
    metricList.forEach(function(metric, idx) {
      if (idx > 0) {
        var sep = document.createElement("span");
        sep.className = "v-metric-sep";
        metricsRow.appendChild(sep);
      }
      var metricEl = document.createElement("div");
      metricEl.className = "v-metric-big" + (idx === 0 ? " v-metric-primary" : "");
      var labelKey = metric.label_key;
      var labelStr = labelKey ? t(labelKey) : (metric.label || metric.key);
      var valueStr = String(metric.value || "");
      var labelSpan = document.createElement("span");
      labelSpan.className = "v-metric-label";
      labelSpan.textContent = labelStr;
      var valueSpan = document.createElement("span");
      valueSpan.className = "v-metric-value";
      valueSpan.textContent = valueStr;
      metricEl.appendChild(labelSpan);
      metricEl.appendChild(valueSpan);
      metricsRow.appendChild(metricEl);
    });
    sec2.appendChild(metricsRow);

    // Stage position and completion fraction have different meanings.
    if (liveStatus.stage_index && liveStatus.stage_total) {
      var pct = normalizedProgress(job);
      var shell = document.createElement("div");
      shell.className = "v-progress-shell";
      var bar = document.createElement("div");
      bar.className = "v-progress-bar" + (isProgressIndeterminate(job) ? " indeterminate" : "");
      bar.style.width = isProgressIndeterminate(job) ? "30%" : pct + "%";
      shell.appendChild(bar);
      sec2.appendChild(shell);
      var progLabel = document.createElement("div");
      progLabel.className = "v-progress-label";
      progLabel.textContent = liveStatus.stage_index + " / " + liveStatus.stage_total;
      sec2.appendChild(progLabel);
    }

    // Elapsed
    var elapsedStr = formatDuration(job.started_at, job.completed_at || null);
    if (elapsedStr) {
      var elapsedEl = document.createElement("div");
      elapsedEl.className = "v-elapsed";
      elapsedEl.textContent = t("overview.elapsed") + ": " + elapsedStr;
      sec2.appendChild(elapsedEl);
    }
  } else {
    // Fallback: stage name or "no live details"
    var fallbackEl = document.createElement("div");
    fallbackEl.className = "v-live-fallback";
    var stageName = translateStageName(job.current_stage, job.stage_label);
    fallbackEl.textContent = stageName || t("live.empty");
    sec2.appendChild(fallbackEl);
  }

  container.appendChild(sec2);

  // ── Section 3: Workflow Timeline ──
  var sec3 = document.createElement("div");
  sec3.className = "v-timeline-slot";
  sec3.setAttribute("data-testid", "timeline-slot");
  renderWorkflowTimeline(sec3, job);
  container.appendChild(sec3);
}

function updateInfoPanel(job) {
  lastSelectedJob = job || null;
  renderInfoInto(document.getElementById("info-panel"), job || null);
  renderInfoInto(document.getElementById("mobile-info-panel"), job || null);
  updateMobileStatusBar();
}

function updateMobileStatusBar() {
  var bar = document.getElementById("mobile-status-bar");
  if (!bar) return;
  // Find the job to display: selectedJobId first, else most recently updated active job
  var job = null;
  if (selectedJobId) {
    for (var i = 0; i < jobsCache.length; i++) {
      if (String(jobsCache[i].id) === String(selectedJobId)) { job = jobsCache[i]; break; }
    }
  }
  if (!job) {
    // Find most recently updated active job
    var best = null;
    for (var j = 0; j < jobsCache.length; j++) {
      var s = String(jobsCache[j].status || "").toLowerCase();
      if (s === "running" || s === "paused") {
        if (!best || (jobsCache[j].updated_at || "") > (best.updated_at || "")) best = jobsCache[j];
      }
    }
    job = best;
  }
  if (!job) { bar.textContent = ""; bar.style.display = "none"; return; }
  bar.style.display = "";
  var parts = [];
  var wf = (job.spec && job.spec.workflow) ? String(job.spec.workflow) : "";
  if (wf) {
    var wfSpan = document.createElement("span");
    wfSpan.className = "msb-workflow";
    wfSpan.textContent = wf;
    bar.innerHTML = "";
    bar.appendChild(wfSpan);
    parts.push(translateStatus(job.status));
    var stageLabel = translateStageName(job.current_stage, job.stage_label);
    if (stageLabel) {
      var stagePart = stageLabel;
      if (job.stage_detail) stagePart += " " + job.stage_detail;
      else if (job.stage_index && job.stage_total) stagePart += " " + job.stage_index + "/" + job.stage_total;
      parts.push(stagePart);
    }
    if (!isProgressIndeterminate(job)) {
      parts.push(t("common.percent", { value: normalizedProgress(job) }));
    }
    parts.forEach(function(p, idx) {
      if (idx > 0) {
        var sep = document.createElement("span");
        sep.className = "msb-sep";
        sep.textContent = " \u00b7 ";
        bar.appendChild(sep);
      }
      var sp = document.createElement("span");
      sp.textContent = p;
      bar.appendChild(sp);
    });
    bar.title = bar.textContent;
  }
  // Click handler: switch to info tab on mobile
  bar.onclick = function() {
    if (!selectedJobId && job) selectJob(job.id);
    document.querySelectorAll(".mobile-tab").forEach(function(tab) {
      var target = tab.getAttribute("data-mobile-panel");
      tab.classList.toggle("active", target === "info");
    });
    document.querySelectorAll(".mobile-panel-body").forEach(function(body) {
      body.classList.toggle("active", body.getAttribute("data-mobile-body") === "info");
    });
  };
}

// ── P2c: BatchOptimize effective config display in job detail ──
function buildJobDetailEffectiveConfig(container, effectiveConfig) {
  if (!effectiveConfig || !isPlainObject(effectiveConfig)) return;
  if (effectiveConfig.schema !== "batch_optimize_effective_v1") return;
  var ecBlock = document.createElement("div");
  ecBlock.className = "mc-eff-detail-block";
  var ecTitle = document.createElement("div");
  ecTitle.className = "mc-eff-detail-title";
  ecTitle.textContent = currentLang === "zh-CN" ? "\u751f\u6548\u914d\u7f6e" : "Effective Config";
  ecBlock.appendChild(ecTitle);
  var roleOrder = ["int", "ts"];
  var hasRoles = false;
  roleOrder.forEach(function(role) {
    var chips = ((effectiveConfig.orca_summary || {})[role]) || [];
    if (!chips.length) return;
    hasRoles = true;
    var row = document.createElement("div");
    row.className = "mc-eff-detail-row";
    var lbl = document.createElement("span");
    lbl.className = "mc-eff-role-label";
    lbl.textContent = role.toUpperCase() + ": ";
    row.appendChild(lbl);
    chips.forEach(function(chipText) {
      var chip = document.createElement("span");
      chip.className = "mc-eff-chip";
      chip.textContent = chipText;
      row.appendChild(chip);
    });
    ecBlock.appendChild(row);
  });
  if (!hasRoles) return;
  container.appendChild(ecBlock);
}

async function openDetailDrawer(job) {
  var overlay = document.getElementById("detail-drawer-overlay");
  var drawer = document.getElementById("detail-drawer");
  var body = document.getElementById("detail-drawer-body");
  if (!overlay || !drawer || !body) return;
  body.innerHTML = "";
  // Fetch full detail (stages, artifacts, recovery) — not available from the
  // lightweight summary endpoint used by the polling lane.
  var jobId = String(job.id || job.job_id || "");
  var detail = null;
  try { detail = await fetchJobDetail(jobId); } catch (e) { detail = getJobDetail(jobId); }
  if (!detail) detail = getJobDetail(jobId);
  // Full stage timeline (larger)
  var stages = detail && Array.isArray(detail.stages)
    ? detail.stages.filter(function(st) { return st && typeof st === "object"; })
    : [];
  if (stages.length) {
    var stCard = buildDetailCard(t("detail.stages"));
    var stepper = document.createElement("div");
    stepper.className = "stage-stepper";
    stages.forEach(function(st) {
      var stStatus = String(st.status || "").toLowerCase();
      var step = document.createElement("div");
      step.className = "stage-step " + getStatusClass(stStatus);
      var dot = document.createElement("span");
      dot.className = "stage-dot";
      var name = document.createElement("span");
      name.className = "stage-name";
      name.textContent = translateStageName(st.stage_name || st.name || st.stage, st.label);
      name.title = String(st.stage_name || st.name || st.stage || "");
      if (st.error) name.title += " — " + String(st.error);
      var dur = document.createElement("span");
      dur.className = "stage-duration";
      var durText = formatDuration(st.started_at, st.completed_at);
      dur.textContent = (st.retry_count ? t("detail.retry_count", { count: st.retry_count }) + " \u00b7 " : "") + (durText || "--");
      step.appendChild(dot);
      step.appendChild(name);
      step.appendChild(dur);
      step.appendChild(createStatusBadge(st.status));
      stepper.appendChild(step);
    });
    stCard.appendChild(stepper);
    body.appendChild(stCard);
  }
  // ── Action bar ──
  var recovery = resolveJobRecovery(job);
  // GAP-9 floor (queue-row parity): terminal rows never show active-only
  // actions; a fresh server recovery still decides rerun/continue capability.
  var activeOnly = !isTerminalJobStatus(job && job.status);
  var drawerActions = document.createElement("div");
  drawerActions.className = "info-actions-grouped";
  var drawerRunGroup = document.createElement("div");
  drawerRunGroup.className = "action-group";
  if (activeOnly && recovery.can_pause) {
    var dPauseBtn = document.createElement("button");
    dPauseBtn.className = "btn";
    dPauseBtn.textContent = t("job.pause");
    dPauseBtn.title = t("job.pause_hint");
    dPauseBtn.addEventListener("click", function() { pauseJob(jobId); });
    drawerRunGroup.appendChild(dPauseBtn);
  }
  if (activeOnly && recovery.can_unpause) {
    var dResumeBtn = document.createElement("button");
    dResumeBtn.className = "btn";
    dResumeBtn.textContent = t("job.resume");
    dResumeBtn.title = t("job.resume_hint");
    dResumeBtn.addEventListener("click", function() { unpauseJob(jobId); });
    drawerRunGroup.appendChild(dResumeBtn);
  }
  if (activeOnly && recovery.can_cancel) {
    var dCancelBtn = document.createElement("button");
    dCancelBtn.className = "btn";
    dCancelBtn.textContent = t("job.cancel");
    dCancelBtn.addEventListener("click", function() { cancelJob(jobId); });
    drawerRunGroup.appendChild(dCancelBtn);
  }
  if (recovery.can_continue) {
    var dContinueBtn = document.createElement("button");
    dContinueBtn.className = "btn";
    dContinueBtn.textContent = t("job.continue");
    dContinueBtn.title = recovery.continue_notes || t("job.continue_hint");
    dContinueBtn.addEventListener("click", function() { continueJob(jobId); });
    drawerRunGroup.appendChild(dContinueBtn);
    // T12: optional "continue to node" override; blank = back to source.
    var dContinueNodeSel = document.createElement("select");
    dContinueNodeSel.className = "modal-select node-select";
    dContinueNodeSel.id = "detail-continue-node-select";
    dContinueNodeSel.title = t("nodes.continue_to_hint");
    populateContinueNodeOptions(dContinueNodeSel);
    drawerRunGroup.appendChild(dContinueNodeSel);
    // The node list may not be loaded yet (sysres drawer never opened):
    // fetch it once and rebuild the options in place when it arrives.
    if (!nodesCache.length) {
      loadNodes().then(function() {
        populateContinueNodeOptions(document.getElementById("detail-continue-node-select"));
      });
    }
  }
  if (recovery.can_rerun) {
    var dRerunBtn = document.createElement("button");
    dRerunBtn.className = "btn";
    dRerunBtn.textContent = t("job.rerun");
    dRerunBtn.title = t("job.rerun_hint");
    dRerunBtn.addEventListener("click", function() { openRerunActionMenu(job, dRerunBtn); });
    drawerRunGroup.appendChild(dRerunBtn);
  }
  if (drawerRunGroup.childNodes.length) drawerActions.appendChild(drawerRunGroup);
  var drawerMgrGroup = document.createElement("div");
  drawerMgrGroup.className = "action-group";
  var dMoveBtn = document.createElement("button");
  dMoveBtn.className = "btn";
  dMoveBtn.textContent = t("move.button");
  dMoveBtn.addEventListener("click", function() { openTargetProjectModal("move", job); });
  drawerMgrGroup.appendChild(dMoveBtn);
  var dCloneBtn = document.createElement("button");
  dCloneBtn.className = "btn";
  dCloneBtn.textContent = t("clone.button");
  dCloneBtn.addEventListener("click", function() { openTargetProjectModal("clone", job); });
  drawerMgrGroup.appendChild(dCloneBtn);
  drawerActions.appendChild(drawerMgrGroup);
  var drawerDiv = document.createElement("div");
  drawerDiv.className = "action-divider";
  drawerActions.appendChild(drawerDiv);
  var drawerDangerGroup = document.createElement("div");
  drawerDangerGroup.className = "action-group";
  var dDeleteBtn = document.createElement("button");
  dDeleteBtn.className = "btn danger";
  dDeleteBtn.textContent = t("job.delete");
  dDeleteBtn.addEventListener("click", function() { openDeleteJobModal(job); });
  drawerDangerGroup.appendChild(dDeleteBtn);
  drawerActions.appendChild(drawerDangerGroup);
  body.appendChild(drawerActions);

  // ── Error card ──
  var errDetail = detail && isPlainObject(detail.error_detail) ? detail.error_detail : null;
  var stderrTail = errDetail && typeof errDetail.stderr_tail === "string" ? errDetail.stderr_tail.trim() : "";
  var errDetailText = errDetail && typeof errDetail.error === "string" && errDetail.error
    ? errDetail.error
    : (errDetail && typeof errDetail.message === "string" && errDetail.message ? errDetail.message : "");
  var failedStage = errDetail && errDetail.failed_stage ? String(errDetail.failed_stage) : "";
  var hasError = job.error || errDetailText || stderrTail || failedStage;
  if (hasError) {
    var drawerErrCard = document.createElement("div");
    drawerErrCard.className = "detail-error-card";
    var drawerErrLabel = document.createElement("div");
    drawerErrLabel.className = "detail-error-label";
    drawerErrLabel.textContent = t("detail.error_title");
    drawerErrCard.appendChild(drawerErrLabel);
    if (job.error) {
      var drawerErrText = document.createElement("div");
      drawerErrText.className = "detail-error-text";
      drawerErrText.textContent = String(job.error);
      drawerErrCard.appendChild(drawerErrText);
    }
    if (failedStage) {
      var drawerFailStage = document.createElement("div");
      drawerFailStage.className = "detail-meta-row";
      drawerFailStage.textContent = t("detail.failed_stage") + ": " + failedStage;
      drawerErrCard.appendChild(drawerFailStage);
    }
    if (errDetailText && errDetailText !== String(job.error || "")) {
      var drawerErrDetailText = document.createElement("div");
      drawerErrDetailText.className = "detail-error-text";
      drawerErrDetailText.textContent = errDetailText;
      drawerErrCard.appendChild(drawerErrDetailText);
    }
    if (stderrTail) {
      var drawerStderrLabel = document.createElement("div");
      drawerStderrLabel.className = "detail-stderr-label";
      drawerStderrLabel.textContent = t("detail.stderr_tail");
      var drawerStderrPre = document.createElement("pre");
      drawerStderrPre.className = "detail-stderr";
      drawerStderrPre.textContent = stderrTail;
      drawerErrCard.appendChild(drawerStderrLabel);
      drawerErrCard.appendChild(drawerStderrPre);
    }
    body.appendChild(drawerErrCard);
  }

  // ── Latest event ──
  var latestEvent = (detail && detail.job && detail.job.latest_event) ? String(detail.job.latest_event) : (job.latest_event ? String(job.latest_event) : "");
  if (latestEvent) {
    var evRow = document.createElement("div");
    evRow.className = "detail-meta-row";
    evRow.textContent = t("overview.latest_event") + ": " + latestEvent;
    body.appendChild(evRow);
  }

  // ── Lineage panel (T12) ──
  var lineageSection = document.createElement("div");
  lineageSection.className = "lineage-section";
  var lineageHeader = document.createElement("div");
  lineageHeader.className = "lineage-header";
  var lineageChevron = document.createElement("span");
  lineageChevron.className = "lineage-chevron";
  lineageChevron.textContent = "▸";
  lineageHeader.appendChild(lineageChevron);
  var lineageLabel = document.createElement("span");
  lineageLabel.textContent = t("queue.view.lineage_section");
  lineageHeader.appendChild(lineageLabel);
  lineageSection.appendChild(lineageHeader);
  var lineageBody = document.createElement("div");
  lineageBody.className = "lineage-body";
  lineageBody.id = "lineage-body";
  lineageBody.style.display = "none";
  lineageSection.appendChild(lineageBody);
  var lineageLoaded = false;
  lineageHeader.addEventListener("click", function() {
    var isOpen = lineageBody.style.display !== "none";
    if (isOpen) {
      lineageBody.style.display = "none";
      lineageChevron.classList.remove("open");
    } else {
      lineageBody.style.display = "";
      lineageChevron.classList.add("open");
      if (!lineageLoaded) {
        lineageLoaded = true;
        _loadAndRenderLineage(jobId);
      }
    }
  });
  body.appendChild(lineageSection);

  // ── Work dir + size + disk state ──
  var drawerAdvItems = [];
  var drawerWorkDir = (detail && detail.disk_state && detail.disk_state.work_dir) || "";
  if (drawerWorkDir) drawerAdvItems.push(["Work Dir", drawerWorkDir]);
  if (detail && detail.disk_state && typeof detail.disk_state.size_bytes === "number" && detail.disk_state.size_bytes >= 0) {
    drawerAdvItems.push(["Size", formatBytes(detail.disk_state.size_bytes)]);
  }
  if (detail && isPlainObject(detail.disk_state)) {
    var dsBits = [];
    if (typeof detail.disk_state.work_dir_exists === "boolean") dsBits.push(detail.disk_state.work_dir_exists ? t("detail.workdir_exists") : t("detail.workdir_missing"));
    if (typeof detail.disk_state.size_bytes === "number" && detail.disk_state.size_bytes >= 0) dsBits.push(formatBytes(detail.disk_state.size_bytes));
    if (dsBits.length) {
      var dsChipRow = document.createElement("div");
      dsChipRow.className = "detail-meta-row";
      dsChipRow.textContent = dsBits.join(" \u00b7 ");
      body.appendChild(dsChipRow);
    }
  }

  // ── Advanced params grid ──
  var drawerAdvToggle = document.createElement("details");
  drawerAdvToggle.className = "info-details-toggle";
  var drawerAdvSummary = document.createElement("summary");
  drawerAdvSummary.textContent = t("actions.advanced");
  drawerAdvToggle.appendChild(drawerAdvSummary);
  var drawerMethodStr = "";
  if (job.spec && job.spec.method) {
    var dm = job.spec.method;
    if (dm.method) drawerMethodStr = String(dm.method);
    if (dm.basis) drawerMethodStr += " / " + String(dm.basis);
    if (dm.solvent) drawerMethodStr += " (" + String(dm.solvent) + ")";
  }
  if (drawerMethodStr) drawerAdvItems.push([t("info.workflow") + " / " + (t("info.protocol") || "Method"), drawerMethodStr]);
  if (job.spec && job.spec.workflow) drawerAdvItems.push([t("info.workflow"), String(job.spec.workflow)]);
  if (job.spec && job.spec.method && job.spec.method.protocol) drawerAdvItems.push([t("info.protocol"), String(job.spec.method.protocol)]);
  drawerAdvItems.push([t("info.created_at"), formatDateTime(job.created_at)]);
  drawerAdvItems.push([t("info.started_at"), formatDateTime(job.started_at)]);
  if (job.completed_at) drawerAdvItems.push([t("info.completed_at"), formatDateTime(job.completed_at)]);
  if (molDoc.meta.source) drawerAdvItems.push([t("info.source"), molDoc.meta.source]);
  if (molDoc.meta.formula) drawerAdvItems.push([t("info.formula"), molDoc.meta.formula]);
  if (molDoc.frames.length) drawerAdvItems.push([t("info.frame"), String(molDoc.currentFrame + 1) + " / " + String(molDoc.frames.length)]);
  if (window.ACPGeometryStore && window.ACPGeometryStore.state.selection.length) drawerAdvItems.push([t("info.selection"), formatSelectionIds()]);
  if (job.pid) drawerAdvItems.push(["PID", String(job.pid)]);
  if (job.input_source) drawerAdvItems.push([t("info.source"), String(job.input_source)]);
  var drawerAdvGrid = document.createElement("div");
  drawerAdvGrid.className = "info-grid";
  drawerAdvItems.forEach(function(entry) {
    if (!entry[1] || entry[1] === "--" || entry[1] === "null") return;
    var item = document.createElement("div");
    item.className = "info-item";
    var label = document.createElement("span");
    label.className = "info-label";
    label.textContent = entry[0];
    var value = document.createElement("div");
    value.className = "info-value";
    value.textContent = entry[1];
    item.appendChild(label);
    item.appendChild(value);
    drawerAdvGrid.appendChild(item);
  });
  drawerAdvToggle.appendChild(drawerAdvGrid);

  // Ensemble total Gibbs
  var thermo = (job.result && job.result.ensemble_thermo) || null;
  if (thermo && typeof thermo.total_gibbs_kcal_mol === "number") {
    var dGibbsCard = document.createElement("div");
    dGibbsCard.className = "info-total-gibbs";
    var dGibbsLabel = document.createElement("div");
    dGibbsLabel.className = "info-total-gibbs-label";
    dGibbsLabel.textContent = t("info.total_gibbs");
    var dGibbsValue = document.createElement("div");
    dGibbsValue.className = "info-total-gibbs-value";
    dGibbsValue.innerHTML = escapeHtml(String(thermo.total_gibbs_kcal_mol.toFixed(4))) + '<span class="info-total-gibbs-unit">kcal/mol</span>';
    dGibbsCard.appendChild(dGibbsLabel);
    dGibbsCard.appendChild(dGibbsValue);
    var dGibbsMeta = [];
    if (typeof thermo.total_gibbs_hartree === "number") dGibbsMeta.push("G_total <b>" + thermo.total_gibbs_hartree.toFixed(8) + "</b> Ha");
    if (typeof thermo.rank1_weight === "number") dGibbsMeta.push("p\u2081 <b>" + thermo.rank1_weight.toFixed(4) + "</b>");
    if (typeof thermo.t_s_mix_kcal_per_mol === "number") dGibbsMeta.push("T\u00b7S_mix <b>" + thermo.t_s_mix_kcal_per_mol.toFixed(4) + "</b> kcal/mol");
    if (typeof thermo.population_coverage === "number") dGibbsMeta.push("coverage <b>" + (thermo.population_coverage * 100).toFixed(1) + "%</b>");
    if (dGibbsMeta.length) {
      var dGibbsMetaEl = document.createElement("div");
      dGibbsMetaEl.className = "info-total-gibbs-meta";
      dGibbsMetaEl.innerHTML = dGibbsMeta.join("");
      dGibbsCard.appendChild(dGibbsMetaEl);
    }
    drawerAdvToggle.appendChild(dGibbsCard);
  }
  body.appendChild(drawerAdvToggle);

  // ── P2c: effective config block for BatchOptimize ──
  if (detail) buildJobDetailEffectiveConfig(body, detail.effective_config);

  if (detail && detail.structure_summary) {
    var summary = detail.structure_summary;
    var resultCard = buildDetailCard("可复用结构与人工选点");
    var message = document.createElement("div"); message.textContent = summary.message || "";
    resultCard.appendChild(message);
    (summary.structures || []).forEach(function(structure) {
      var row = document.createElement("div");
      var state = structure.optimization_status === "converged" ? " · OPT 已收敛" : "";
      if (structure.frequency_status === "failed") state += " · FREQ 失败";
      if (structure.frequency && structure.frequency.ts_validation === "failed") state += " · TS 验证未通过";
      row.textContent = structure.label + state;
      resultCard.appendChild(row);
    });
    body.appendChild(resultCard);
  }

  // ── Artifacts ──
  var artifacts = detail && Array.isArray(detail.artifacts_summary)
    ? detail.artifacts_summary.filter(function(a) { return a && typeof a === "object"; })
    : [];
  if (artifacts.length) {
    var artCard = buildDetailCard(t("detail.artifacts"));
    artifacts.forEach(function(artifact) {
      var row = document.createElement("div");
      row.className = "detail-artifact";
      var atype = document.createElement("span");
      atype.className = "artifact-type";
      atype.textContent = String(artifact.type || "file");
      var apath = document.createElement("span");
      apath.className = "artifact-path";
      apath.textContent = String(artifact.path || "");
      var asize = document.createElement("span");
      asize.className = "artifact-size";
      asize.textContent = typeof artifact.size === "number" ? formatBytes(artifact.size) : "";
      row.appendChild(atype);
      row.appendChild(apath);
      row.appendChild(asize);
      artCard.appendChild(row);
    });
    body.appendChild(artCard);
  }

  // Recovery notes
  if (recovery && recovery.continue_notes) {
    var hint = document.createElement("div");
    hint.className = "detail-hint";
    hint.textContent = recovery.continue_notes;
    body.appendChild(hint);
  }
  // Show drawer
  overlay.classList.add("open");
  drawer.classList.add("open");
}

function closeDetailDrawer() {
  var overlay = document.getElementById("detail-drawer-overlay");
  var drawer = document.getElementById("detail-drawer");
  if (overlay) overlay.classList.remove("open");
  if (drawer) drawer.classList.remove("open");
}

async function openSysresDrawer() {
  var overlay = document.getElementById("sysres-drawer-overlay");
  var drawer = document.getElementById("sysres-drawer");
  if (!overlay || !drawer) return;
  overlay.classList.add("open");
  drawer.classList.add("open");
  await Promise.all([loadNodes(), loadBackends()]);
  var closeBtn = document.getElementById("sysres-drawer-close");
  if (closeBtn) closeBtn.focus();
}

function closeSysresDrawer() {
  var overlay = document.getElementById("sysres-drawer-overlay");
  var drawer = document.getElementById("sysres-drawer");
  if (overlay) overlay.classList.remove("open");
  if (drawer) drawer.classList.remove("open");
}

function isSysresDrawerOpen() {
  var drawer = document.getElementById("sysres-drawer");
  return !!(drawer && drawer.classList.contains("open"));
}

