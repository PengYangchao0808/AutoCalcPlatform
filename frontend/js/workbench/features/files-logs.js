/* ── R4 task-session discipline ─────────────────────────────────────────────
 * Folder listings and log tails are tied to the task-selection session
 * (ACPWorkbench.controllers.taskSession, R1): the session snapshot is
 * captured at request start and the response is applied ONLY if the session
 * is unchanged. Token compare when the session controller has a selection;
 * page-`selectedJobId` compare as the fallback when it does not. A late
 * /files/{path} or /logs response from a previous selection (A→B→A, same-job
 * rerun/reselect) can therefore never write the newer session's panel state.
 *
 * The accept/reject decision is a pure helper on window.ACPWorkbench.contracts
 * (module-tested in frontend/js/module_tests/files_session.test.js); the
 * wrappers below only read the live session/page globals.
 */
window.ACPWorkbench = window.ACPWorkbench || {};
window.ACPWorkbench.contracts = window.ACPWorkbench.contracts || {};
window.ACPWorkbench.contracts.filesSessionUnchanged = function filesSessionUnchanged(captured, now) {
  if (!captured || !now) return false;
  var capTok = captured.token == null ? null : captured.token;
  var nowTok = now.token == null ? null : now.token;
  // Token identity is authoritative in both directions: a missing token on
  // either side means the session cannot be proven current (e.g. cleared
  // selection) — reject rather than fall back to a weaker check.
  if (capTok != null || nowTok != null) return capTok != null && capTok === nowTok;
  // Fallback (no session controller selection on either side): the captured
  // jobId must still be the current selection.
  var capJob = String(captured.jobId == null ? "" : captured.jobId);
  var nowJob = String(now.jobId == null ? "" : now.jobId);
  return capJob !== "" && capJob === nowJob;
};

function _filesTaskSessionCurrent() {
  if (!(window.ACPWorkbench && ACPWorkbench.controllers && ACPWorkbench.controllers.taskSession)) return null;
  var cur = ACPWorkbench.controllers.taskSession.current();
  return cur || null;
}

/** Capture the session identity at request start (token when available). */
function filesSessionCapture(jobId) {
  var cur = _filesTaskSessionCurrent();
  var cap = { jobId: String(jobId == null ? "" : jobId), token: null };
  if (cur) cap.token = cur.token;
  return cap;
}

/** True only when the captured session is still the live one. */
function filesSessionAccepts(captured) {
  if (!captured) return false;
  var cur = _filesTaskSessionCurrent();
  var now = cur
    ? { jobId: cur.jobId, token: cur.token }
    : { jobId: String(selectedJobId == null ? "" : selectedJobId), token: null };
  return window.ACPWorkbench.contracts.filesSessionUnchanged(captured, now);
}

function buildTree(files) {
  const root = { type: "folder", name: "", children: {} };
  (files || []).forEach(function(file) {
    const parts = String(file.path || "").split("/");
    let cursor = root;
    parts.forEach(function(part, index) {
      if (!part) return;
      const isDir = file.is_dir || false;
      const isLast = index === parts.length - 1;
      if (!cursor.children[part]) {
        const type = isLast ? (isDir ? "folder" : "file") : "folder";
        cursor.children[part] = { type: type, name: part, path: parts.slice(0, index + 1).join("/"), size: file.size, modified: file.modified, children: {} };
      }
      cursor = cursor.children[part];
      if (isLast) {
        cursor.size = file.size;
        cursor.modified = file.modified;
      }
    });
  });
  return root;
}

function sortedEntries(node) {
  return Object.keys(node.children || {}).map(function(key) {
    return node.children[key];
  }).sort(function(a, b) {
    if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function folderToggleSvg(open) {
  return open
    ? '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.8"><path d="M5 7l5 6l5-6"/></svg>'
    : '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.8"><path d="M7 5l6 5l-6 5"/></svg>';
}

function fileActionSvg() {
  return '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.5"><path d="M6 4h5l3 3v9H6z"/><path d="M11 4v3h3"/></svg>';
}

function downloadSvg() {
  return '<svg viewBox="0 0 20 20" width="14" height="14" stroke="currentColor" fill="none" stroke-width="1.5"><path d="M10 14V4M6 8l4 4 4-4M3 16v2a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2"/></svg>';
}

function folderIconSvg() {
  return '<svg class="folder-icon" viewBox="0 0 20 20" width="14" height="14" fill="currentColor"><path d="M2 5a2 2 0 0 1 2-2h3l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z"/></svg>';
}

function fileIconSvg() {
  return '<svg class="file-icon" viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 4h5l3 3v9H6z"/><path d="M11 4v3h3"/></svg>';
}

function spinnerSvg() {
  return '<svg class="spinner" viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 2a8 8 0 0 1 8 8" stroke-linecap="round"/></svg>';
}

function getAllLoadedFiles() {
  var all = [];
  Object.keys(folderCache).forEach(function(key) {
    all = all.concat(folderCache[key] || []);
  });
  return all;
}

function renderFileTree(files, jobId) {
  updateFileTreeTaskName(jobId);
  var allFiles = getAllLoadedFiles();
  if (allFiles.length === 0) allFiles = files || [];
  lastFileListing = allFiles;
  renderFileTreeInto(document.getElementById("file-tree-panel"), allFiles, jobId);
  renderFileTreeInto(document.getElementById("mobile-file-tree-panel"), allFiles, jobId);
}

// Q1: pinned result products (from the files response's `pinned` array) as a
// quick-access block above the raw tree. Rows reuse the .file-row pattern and
// open through the same openJobFile path as regular files.
function renderPinnedBlock(container, jobId) {
  if (!jobId || !lastPinnedItems.length) return;
  const wrap = document.createElement("div");
  wrap.className = "pinned-block";
  const head = document.createElement("div");
  head.className = "pinned-head";
  head.textContent = "📌 " + t("files.pinned");
  wrap.appendChild(head);
  lastPinnedItems.forEach(function(item) {
    if (!item || !item.path) return;
    const row = document.createElement("div");
    row.className = "file-row pinned-row";
    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.innerHTML = fileIconSvg();
    const label = document.createElement("span");
    label.className = "file-name";
    label.textContent = item.label || String(item.path).split("/").pop();
    label.title = String(item.path);
    const kind = document.createElement("span");
    kind.className = "pinned-kind";
    kind.textContent = item.kind || "";
    row.appendChild(icon);
    row.appendChild(label);
    row.appendChild(kind);
    row.addEventListener("click", function() {
      openJobFile(jobId, { type: "file", path: item.path, name: String(item.path).split("/").pop(), size: 0 });
    });
    wrap.appendChild(row);
  });
  container.appendChild(wrap);
}

function renderFileTreeInto(container, files, jobId) {
  container.innerHTML = "";
  renderPinnedBlock(container, jobId);
  if (!jobId || !files.length) {
    if (lastPinnedItems.length && jobId) return; // pinned block alone is enough
    const empty = document.createElement("div");
    empty.className = "placeholder";
    empty.textContent = t("files.empty");
    container.appendChild(empty);
    return;
  }
  if (selectedJobIsRemote) {
    const header = document.createElement("div");
    header.className = "file-tree-header";
    const nodeInfo = document.createElement("span");
    nodeInfo.className = "node-info";
    nodeInfo.textContent = (t("nodes.title") || "Node") + ": " + (lastRemoteNode || "--");
    const archiveBtn = document.createElement("button");
    archiveBtn.className = "tree-btn";
    archiveBtn.textContent = t("files.download_all") || "下载全部";
    archiveBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      downloadRemoteArchive(jobId);
    });
    header.appendChild(nodeInfo);
    header.appendChild(archiveBtn);
    container.appendChild(header);
  }
  const root = buildTree(files);
  const list = document.createElement("ul");
  list.className = "file-tree-root";
  sortedEntries(root).forEach(function(child) {
    list.appendChild(renderTreeNode(child, jobId));
  });
  container.appendChild(list);
}

async function fetchAndExpand(jobId, folderPath) {
  folderCache[folderPath] = null;
  renderFileTree(lastFileListing, jobId);
  var session = filesSessionCapture(jobId);
  try {
    var url;
    if (selectedJobIsRemote) {
      url = "/jobs/" + encodeURIComponent(jobId) + "/remote-files?path=" + encodeURIComponent(folderPath);
    } else {
      url = "/jobs/" + encodeURIComponent(jobId) + "/files?path=" + encodeURIComponent(folderPath);
    }
    var resp = selectedJobIsRemote ? await apiRemote(url) : await api(url);
    if (!filesSessionAccepts(session)) return;
    var files;
    if (selectedJobIsRemote) {
      files = (resp.files || []).map(function(f) {
        return { path: f.name, size: f.size, modified: f.mtime, is_dir: f.is_dir };
      });
    } else {
      files = resp.files || [];
    }
    folderCache[folderPath] = files;
    folderState[folderPath] = true;
  } catch (e) {
    if (!filesSessionAccepts(session)) return;
    folderCache[folderPath] = [];
    folderState[folderPath] = true;
  }
  renderFileTree(lastFileListing, jobId);
}

// T5: live-sync — re-fetch every already-loaded folder of the selected job
// while it runs. folderCache entries are replaced in place, folderState is
// left untouched so expansion is preserved; a failed per-folder fetch keeps
// the previous cache entry (no empty flash). Remote jobs reuse the same
// /remote-files response mapping as fetchAndExpand.
async function refreshLoadedFolders(jobId) {
  if (!jobId) return;
  var startSession = filesSessionCapture(jobId);
  var paths = Object.keys(folderCache).filter(function(p) { return folderCache[p] !== null; });
  if (paths.indexOf("") === -1) paths.unshift("");
  await Promise.all(paths.map(async function(p) {
    var session = filesSessionCapture(jobId);
    var url;
    if (selectedJobIsRemote) {
      url = "/jobs/" + encodeURIComponent(jobId) + "/remote-files?path=" + encodeURIComponent(p);
    } else {
      url = "/jobs/" + encodeURIComponent(jobId) + "/files?path=" + encodeURIComponent(p);
    }
    try {
      var resp = selectedJobIsRemote ? await apiRemote(url) : await api(url);
      if (!filesSessionAccepts(session)) return;
      var files;
      if (selectedJobIsRemote) {
        files = (resp.files || []).map(function(f) {
          return { path: f.name, size: f.size, modified: f.mtime, is_dir: f.is_dir };
        });
      } else {
        files = resp.files || [];
      }
      folderCache[p] = files;
    } catch (e) {
      // keep old cache for this folder (current session only)
      if (!filesSessionAccepts(session)) return;
    }
  }));
  if (!filesSessionAccepts(startSession)) return;
  renderFileTree(getAllLoadedFiles(), jobId);
}

// T5/T6: called from the poll tick. Only acts while the selected job is
// pending/running; throttled to every 3rd tick (15s) locally and every 6th
// tick (30s) for remote jobs. For mechanism jobs it also force-refreshes the
// S0–S4 study status chips (queue rows + mechanism panel).
async function refreshFileTreeIfActive() {
  if (!selectedJobId) return;
  var job = null;
  for (var i = 0; i < jobsCache.length; i++) {
    if (String(jobsCache[i].id) === String(selectedJobId)) { job = jobsCache[i]; break; }
  }
  if (!job) return;
  var status = String(job.status || "");
  if (status !== "pending" && status !== "running") return;
  treeRefreshCounter = (treeRefreshCounter || 0) + 1;
  var interval = selectedJobIsRemote ? 6 : 3;
  if (treeRefreshCounter % interval !== 0) return;
  await refreshLoadedFolders(selectedJobId);
  if (job.spec && job.spec.workflow === "mechanism") {
    await fetchMechanismStudiesForJob(selectedJobId, true);
    var studies = mechanismStudiesByJob[String(selectedJobId)] || [];
    await Promise.all(studies.map(function(study) {
      var sid = study && String(study.id || study.study_id || "");
      if (sid && mechanismStudyDetailsById[sid]) return fetchMechanismStudyDetail(sid, true);
      return Promise.resolve(null);
    }));
    renderQueue();
    if (activeViewerTab === "path") {
      renderMechanismTabFromCache(activeViewerTab);
    }
  }
}

// T7: expand a nested path one segment at a time (e.g. a mechanism job's
// mechanism_study/<study_id>/calc). Stops silently when a folder fetch fails
// so historical jobs without a calc/ directory simply stay at the top level.
async function expandPath(jobId, path) {
  if (!jobId || !path) return;
  var session = filesSessionCapture(jobId);
  var segments = String(path).split("/").filter(function(s) { return s.length > 0; });
  var prefix = "";
  for (var i = 0; i < segments.length; i++) {
    if (!filesSessionAccepts(session)) return;
    prefix = prefix ? prefix + "/" + segments[i] : segments[i];
    try {
      await fetchAndExpand(jobId, prefix);
    } catch (e) {
      return;
    }
  }
}

function isFolderLoaded(folderPath) {
  return folderCache.hasOwnProperty(folderPath) && folderCache[folderPath] !== null;
}

function isFolderLoading(folderPath) {
  return folderCache[folderPath] === null;
}

// Q1: render-only display names for well-known mechanism/workflow directory
// names. node.path and node.name stay raw — the map is applied ONLY to the
// visible label (raw name kept as the tooltip).
const FOLDER_DISPLAY_NAMES = {
  "mechanism_study": "Mechanism Study", "calc": "Calculations",
  "s1": "S1 · Conformers", "s1_xtbfast": "S1 · xTB-Fast", "s2": "S2 · Path Search",
  "s2_peb": "S2 · PEB", "s3s4": "S3/S4 · Refinement",
  "cycles": "SR Cycles", "routes": "Routes", "refinements": "Refinements",
  "inputs": "Inputs", "work": "Work", "results": "Results",
  "WORK": "Work", "RESULT": "Results",
  "00_RUNTIME": "00 · Runtime", "01_PREPARE": "01 · Preparation",
  "02_SEARCH": "02 · Conformer Search", "03_OPT": "03 · Optimization",
  "04_FREQ": "04 · Frequencies", "05_SP": "05 · Single Point",
  "06_THERMO": "06 · Thermochemistry", "07_PATH": "07 · Reaction Path",
  "08_ANALYSIS": "08 · Analysis",
  "structures": "Structures", "energies": "Energies", "frequencies": "Frequencies",
  "trajectories": "Trajectories", "ensembles": "Ensembles",
  "mechanism": "Mechanism", "reports": "Reports"
};

function renderTreeNode(node, jobId) {
  if (node.type === "folder") {
    const li = document.createElement("li");
    li.className = "tree-folder";
    const row = document.createElement("div");
    row.className = "folder-row";
    const toggle = document.createElement("button");
    const isOpen = folderState[node.path] === true;
    const isLoading = isFolderLoading(node.path);
    toggle.className = "folder-toggle" + (isOpen ? " open" : "") + (isLoading ? " loading" : "");
    toggle.innerHTML = isLoading ? spinnerSvg() : folderToggleSvg(isOpen);
    const name = document.createElement("span");
    name.className = "folder-name";
    const displayName = FOLDER_DISPLAY_NAMES[node.name] || node.name;
    name.innerHTML = folderIconSvg() + escapeHtml(displayName);
    if (displayName !== node.name) name.setAttribute("title", node.name);
    const spacer = document.createElement("span");
    spacer.className = "file-size";
    if (isLoading) {
      const badge = document.createElement("span");
      badge.className = "loading-badge";
      badge.textContent = "...";
      spacer.appendChild(badge);
    }
    row.appendChild(toggle);
    row.appendChild(name);
    row.appendChild(spacer);
    const toggleFolder = function() {
      var path = node.path;
      if (isFolderLoading(path)) return;
      if (!isOpen) {
        fetchAndExpand(jobId, path);
        return;
      }
      folderState[node.path] = !isOpen;
      renderFileTree(lastFileListing, jobId);
    };
    toggle.addEventListener("click", function(e) { e.stopPropagation(); toggleFolder(); });
    row.addEventListener("click", function(e) { if (e.target.closest(".folder-toggle")) return; toggleFolder(); });
    li.appendChild(row);
    if (isOpen) {
      const branch = document.createElement("ul");
      branch.className = "file-tree-branch";
      sortedEntries(node).forEach(function(child) { branch.appendChild(renderTreeNode(child, jobId)); });
      li.appendChild(branch);
    }
    return li;
  }
  const li = document.createElement("li");
  li.className = "tree-file";
  const row = document.createElement("div");
  row.className = "file-row" + (selectedFileKey === String(jobId) + "::" + node.path ? " active" : "");
  const icon = document.createElement("span");
  icon.className = "file-icon";
  icon.innerHTML = fileIconSvg();
  const name = document.createElement("span");
  const ext = node.name.includes(".") ? node.name.split(".").pop().toLowerCase() : "";
  name.className = "file-name" + (ext ? " ext-" + ext : "");
  name.textContent = node.name;
  const size = document.createElement("span");
  size.className = "file-size";
  size.textContent = formatBytes(node.size);

  const actions = document.createElement("span");
  actions.className = "file-actions";
  const previewBtn = document.createElement("button");
  previewBtn.className = "file-action";
  previewBtn.innerHTML = fileActionSvg();
  previewBtn.setAttribute("title", t("files.open"));
  previewBtn.addEventListener("click", function(e) {
    e.stopPropagation();
    openJobFile(jobId, node);
  });
  const downloadBtn = document.createElement("button");
  downloadBtn.className = "file-action";
  downloadBtn.innerHTML = downloadSvg();
  downloadBtn.setAttribute("title", t("files.download"));
  downloadBtn.addEventListener("click", function(e) {
    e.stopPropagation();
    if (selectedJobIsRemote) {
      downloadRemoteFile(jobId, node.path);
    } else {
      downloadLocalFile(jobId, node.path);
    }
  });
  actions.appendChild(previewBtn);
  actions.appendChild(downloadBtn);
  actions.appendChild(size);
  row.appendChild(icon);
  row.appendChild(name);
  row.appendChild(actions);
  row.addEventListener("click", function(e) { if (e.target.closest(".file-action")) return; openJobFile(jobId, node); });
  li.appendChild(row);
  return li;
}

function parseEnergyValue(value) {
  if (typeof value === "number" && isFinite(value)) return value;
  if (!value || typeof value !== "object") return null;
  const keys = ["energy", "final_energy", "electronic_energy", "free_energy", "gibbs_energy"];
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i];
    if (typeof value[key] === "number" && isFinite(value[key])) return value[key];
  }
  for (const key in value) {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      const nested = parseEnergyValue(value[key]);
      if (nested !== null) return nested;
    }
  }
  return null;
}


// ── Log rendering & streaming ──
function renderLogPlaceholder() {
  logEntries = [];
  _logTruncated = false;
  var line = '<span class="text-panel">' + escapeHtml(t("logs.empty")) + '</span>';
  logEntries.push({ type: "__placeholder__", html: line });
  _flushLogPanel(true);
}

function _flushLogPanel(forceFull) {
  _logFlushScheduled = false;
  var panel = _logMediaQuery.matches ? "mobile" : "desktop";
  var other  = panel === "mobile" ? "desktop" : "mobile";
  var state  = _logPanelState[panel];
  var otherState = _logPanelState[other];

  // Lazily resolve element refs
  if (!state.el) state.el = document.getElementById(panel === "mobile" ? "mobile-log-panel" : "log-panel");
  if (!otherState.el) otherState.el = document.getElementById(other === "mobile" ? "mobile-log-panel" : "log-panel");
  if (!state.el) return;

  // Clear the hidden panel to release DOM nodes
  if (otherState.el && otherState.el.childNodes.length) {
    otherState.el.innerHTML = "";
    otherState.lastRenderedIndex = 0;
  }

  var fullRender = forceFull || state.lastRenderedIndex === 0 || state.lastRenderedIndex > logEntries.length;
  var start = fullRender ? 0 : state.lastRenderedIndex;
  var wasAtBottom = state.el.scrollTop + state.el.clientHeight >= state.el.scrollHeight - 40;

  if (fullRender) {
    state.el.innerHTML = "";
    // Prepend truncation indicator if store was capped
    if (_logTruncated) {
      var indicator = document.createElement("span");
      indicator.className = "log-line type-log";
      indicator.style.cssText = "opacity:.45;font-style:italic;display:block;margin-bottom:2px";
      indicator.textContent = t("logs.earlier_collapsed");
      state.el.appendChild(indicator);
    }
  }

  if (start < logEntries.length) {
    var frag = document.createDocumentFragment();
    for (var i = start; i < logEntries.length; i++) {
      var tmp = document.createElement("div");
      tmp.innerHTML = logEntries[i].html;
      while (tmp.firstChild) frag.appendChild(tmp.firstChild);
    }
    state.el.appendChild(frag);
    state.lastRenderedIndex = logEntries.length;
  }

  // Auto-scroll (once per flush per panel)
  if (wasAtBottom) {
    state.el.scrollTop = state.el.scrollHeight;
  }
}

// Schedule a coalesced rAF flush — multiple appends in the same frame produce
// one DOM mutation batch via DocumentFragment.
function _scheduleLogFlush() {
  if (!_logFlushScheduled) {
    _logFlushScheduled = true;
    requestAnimationFrame(function() { _flushLogPanel(false); });
  }
}

function appendLogHtml(html) {
  logEntries.push({ type: "__html__", html: html });
  // Enforce cap: drop oldest in chunks of 200
  if (logEntries.length > MAX_LOG_LINES) {
    var drop = logEntries.length - MAX_LOG_LINES + 200;
    logEntries.splice(0, drop);
    // Shift rendered index back; mark as truncated
    _logPanelState.desktop.lastRenderedIndex = Math.max(0, _logPanelState.desktop.lastRenderedIndex - drop);
    _logPanelState.mobile.lastRenderedIndex  = Math.max(0, _logPanelState.mobile.lastRenderedIndex - drop);
    _logTruncated = true;
  }
  _scheduleLogFlush();
}

function resetLogs() {
  logEntries = [];
  _logTruncated = false;
  _logPanelState.desktop.lastRenderedIndex = 0;
  _logPanelState.mobile.lastRenderedIndex  = 0;
  _flushLogPanel(true);
}

function appendLogLine(type, message) {
  var cssType = String(type || "log").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
  appendLogHtml('<span class="log-line type-' + escapeHtml(cssType) + '">' + escapeHtml(message) + '</span>');
}

// Switch log rendering when media query changes (desktop ↔ mobile).
_logMediaQuery.addEventListener("change", function() {
  // Force full re-render into the newly visible panel
  _logPanelState.desktop.lastRenderedIndex = 0;
  _logPanelState.mobile.lastRenderedIndex  = 0;
  _flushLogPanel(true);
});

// Q3-P0: poll the real stdout tail (GET /jobs/{id}/logs) into the live-log
// panel. Uses cursor-based incremental fetching — tracks stdout/stderr offsets
// per job so only new lines are appended without destroying SSE-appended lines.
// Remote-only jobs can 404 — those errors are swallowed here and by the caller.
// R4 session discipline: the token is session-scoped — a task switch (taskSession
// token change, or a jobId change when no session selection exists) bumps
// logPollToken BEFORE any new request, so a pending tail response from the old
// task/session is dropped by the token check even if no newer poll was issued.
var logSessionToken = null;
async function pollLogTail(jobId) {
  if (!jobId) return;
  var jid = String(jobId);
  var cur = _filesTaskSessionCurrent();
  var sessionToken = cur ? cur.token : null;
  if (logCursor.jobId !== jid || sessionToken !== logSessionToken) {
    logPollToken++; // invalidate any in-flight tail from the previous session/task
    logCursor = { stdout: 0, stderr: 0, irc: 0, jobId: jid };
    logSessionToken = sessionToken;
  }
  var token = ++logPollToken;
  var session = filesSessionCapture(jid);
  var url = "/jobs/" + encodeURIComponent(jid) + "/logs?lines=300";
  if (logCursor.stdout > 0 || logCursor.stderr > 0) {
    url += "&stdout_offset=" + logCursor.stdout + "&stderr_offset=" + logCursor.stderr;
  }
  var resp = null;
  try {
    resp = await api(url);
  } catch (e) {
    return; // remote log tail can 404 — ignore
  }
  if (token !== logPollToken) return;
  if (!filesSessionAccepts(session)) return;
  if (String(selectedJobId || "") !== jid) return;

  // Extract lines: prefer new array fields, fall back to splitting legacy strings
  var stdoutLines = Array.isArray(resp.stdout_lines) ? resp.stdout_lines
    : (typeof resp.stdout === "string" && resp.stdout ? resp.stdout.split(/\r?\n/) : []);
  var stderrLines = Array.isArray(resp.stderr_lines) ? resp.stderr_lines
    : (typeof resp.stderr === "string" && resp.stderr ? resp.stderr.split(/\r?\n/) : []);

  // Update cursors
  if (typeof resp.stdout_next_offset === "number") logCursor.stdout = resp.stdout_next_offset;
  if (typeof resp.stderr_next_offset === "number") logCursor.stderr = resp.stderr_next_offset;

  // Append stdout lines
  stdoutLines.forEach(function(line) {
    if (line) appendLogLine("log", line);
  });
  // Append stderr lines with distinct styling
  stderrLines.forEach(function(line) {
    if (line) appendLogLine("stderr", line);
  });
}

async function pollIrcOutputTail(jobId) {
  if (!lastSelectedJob || String((lastSelectedJob.spec || {}).workflow || lastSelectedJob.workflow || "") !== "irc") return;
  var jid = String(jobId || "");
  if (!jid || String(selectedJobId || "") !== jid) return;
  var session = filesSessionCapture(jid);
  if (logCursor.jobId !== jid) logCursor = { stdout: 0, stderr: 0, irc: 0, jobId: jid };
  var resp = await api("/jobs/" + encodeURIComponent(jid) + "/irc/log-tail?offset=" + Number(logCursor.irc || 0));
  if (!filesSessionAccepts(session)) return;
  if (String(selectedJobId || "") !== jid || !resp) return;
  if (typeof resp.next_offset === "number") logCursor.irc = resp.next_offset;
  (Array.isArray(resp.lines) ? resp.lines : []).forEach(function(line) {
    if (line) appendLogLine("orca", "[ORCA] " + line);
  });
}

