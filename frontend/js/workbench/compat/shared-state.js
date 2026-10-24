const API_BASE = "/api/v1";
const STRUCTURE_UPLOAD_EXTS = [".xyz", ".sdf", ".sd", ".mol", ".gjf", ".com", ".inp", ".log", ".out"];
const DEFAULT_TASK_VIEW = { groupBy: "molecule", sort: "created_desc", filters: { status: [], workflow: [], molecule: [], tag: [], batch: [], remark: [] }, q: "", archived: "exclude", runningFirst: false };
var taskViewPrefs = null;
function loadTaskViewPrefs(projectId) {
  var key = "acp.taskview." + (projectId || "__all__");
  try {
    var raw = localStorage.getItem(key);
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") { taskViewPrefs = parsed; return; }
    }
  } catch (e) {}
  taskViewPrefs = JSON.parse(JSON.stringify(DEFAULT_TASK_VIEW));
}
function saveTaskViewPrefs() {
  if (!taskViewPrefs) return;
  var key = "acp.taskview." + (selectedProjectId || "__all__");
  try { localStorage.setItem(key, JSON.stringify(taskViewPrefs)); } catch (e) {}
}
let currentLang = localStorage.getItem("acp-lang") || "zh-CN";
let selectedProjectId = "";
let selectedJobId = "";
let selectedJobIsRemote = false;
let lastRemoteNode = "";
let selectedFileKey = "";
let activeSSE = null;
let jobsPollTimer = null;
let jobsPollGeneration = 0;
let jobsPollInFlight = false;
let jobsCache = [];
let taskViewCache = null;
var taskViewOffset = 0;
var queueRowRenderContext = null;
var workbenchLoadErrors = {};
var projectsLoaded = false;
let projectsCache = [];
// Main-structure holder: read-only projection of the bounded scene instance
// (ACPVisualization.scene — never a native 3Dmol viewer). Consumers resolve
// this binding and use the restricted instance API only.
let viewer = null;
var mainViewerFraming = { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 };
let currentModel = null;
let currentModelAtoms = [];
let viewerModelLoaded = false;

// T12/T8: a job's execution node is exposed at the top level of the v1
// record (node_id/host, lifted from the jobs table). Read it first and fall
// back to result.node for older server responses; "local" is the local
// sentinel, not a remote node. Returns "" for local/unknown.
function jobRemoteNodeName(job) {
  if (!job) return "";
  var node = job.node_id || (job.result && job.result.node) || "";
  node = String(node || "").trim();
  return node && node !== "local" ? node : "";
}
// Q2: collapsed queue group_ids (persist across poll re-renders).
let queueCollapsedGroups = {};
// T6: module-level Intl.Collator for natural + CJK name sorting (TS2 < TS10).
const _taskCollator = new Intl.Collator("zh", { numeric: true, sensitivity: "variant" });

// Panel layout state (collapse + resize), persisted as one JSON key.
const LAYOUT_DEFAULTS = {
  left:   { width: 240, collapsed: false },
  right:  { width: 240, collapsed: false },
  panels: { file_tree: {collapsed: false}, log: {collapsed: false},
            queue: {collapsed: false}, info: {collapsed: false},
            science_question: {collapsed: false},
            scientific_evidence: {collapsed: false},
            research_approval: {collapsed: false} },
  heights: { left_log: 180, right_info: 220 }
};
let layoutState = loadLayoutState();
let layoutSaveTimer = null;

function loadLayoutState() {
  const s = JSON.parse(JSON.stringify(LAYOUT_DEFAULTS));
  try {
    const raw = localStorage.getItem("acp_layout_state");
    if (raw) {
      const parsed = JSON.parse(raw);
      // shallow-merge per top-level key (tolerate missing/partial)
      for (const k of ["left","right","panels","heights"]) {
        if (parsed[k] && typeof parsed[k] === "object") s[k] = Object.assign({}, s[k], parsed[k]);
      }
    }
  } catch (e) { /* ignore corrupt state */ }
  // legacy migration: old single-width key wins if no modern state saved
  try {
    const old = localStorage.getItem("acp_left_col_width");
    if (old !== null) {
      const w = parseInt(old, 10);
      if (!isNaN(w) && w >= 200 && w <= 500) s.left.width = w;
      localStorage.removeItem("acp_left_col_width");
    }
  } catch (e) { /* ignore */ }
  return s;
}
function saveLayoutStateNow() {
  try { localStorage.setItem("acp_layout_state", JSON.stringify(layoutState)); } catch (e) { /* ignore */ }
}
function saveLayoutState() {   // throttled 300ms
  if (layoutSaveTimer) clearTimeout(layoutSaveTimer);
  layoutSaveTimer = setTimeout(saveLayoutStateNow, 300);
}

function syncSidebarButtonGlyphs() {
  const lb = document.getElementById("btn-toggle-left");
  const rb = document.getElementById("btn-toggle-right");
  if (lb) lb.textContent = layoutState.left.collapsed ? "▶" : "◀";
  if (rb) rb.textContent = layoutState.right.collapsed ? "◀" : "▶";
}

function setHVar(name, v) {
  const style = document.documentElement.style;
  if (v == null) style.removeProperty(name);
  else style.setProperty(name, v + "px");
}

function applyLayout(opts) {
  const wb = document.querySelector(".workbench");
  if (!wb) return;
  // At mobile widths, let the CSS media query control the grid layout
  const vw = window.innerWidth;
  const isMobile = vw <= 1023;
  if (!isMobile) {
    let left = layoutState.left.collapsed ? 0 : layoutState.left.width;
    let right = layoutState.right.collapsed ? 0 : layoutState.right.width;
    // Transition zone (1024–1279px): clamp sidebars so center >= ~480px.
    // Do NOT mutate layoutState — only the rendered values are clamped.
    if (vw < 1280) {
      const minCenter = 480;
      const maxSide = Math.max(200, Math.floor((vw - minCenter) / 2));
      left  = Math.min(left, maxSide);
      right = Math.min(right, maxSide);
      // If both together exceed budget, split evenly respecting floors
      if (left + right > vw - minCenter) {
        const budget = vw - minCenter;
        left  = Math.min(left,  Math.floor(budget / 2));
        right = Math.min(right, budget - left);
      }
    }
    wb.style.gridTemplateColumns = left + "px 1fr " + right + "px";
  } else {
    wb.style.gridTemplateColumns = "";
  }
  document.documentElement.style.setProperty("--left-log-h", layoutState.heights.left_log + "px");
  document.documentElement.style.setProperty("--right-info-h", layoutState.heights.right_info + "px");
  const lz = document.getElementById("left-col-zone");
  const rz = document.getElementById("right-col-zone");
  if (lz) lz.classList.toggle("collapsed", layoutState.left.collapsed);
  if (rz) rz.classList.toggle("collapsed", layoutState.right.collapsed);
  if (rz) rz.classList.toggle("queue-collapsed", !!(layoutState.panels.queue && layoutState.panels.queue.collapsed));
  // collapse-state classes on panel sections
  document.querySelectorAll("[data-panel-key]").forEach(function(el) {
    const key = String(el.getAttribute("data-panel-key") || "");
    const st = layoutState.panels[key];
    if (st) el.classList.toggle("collapsed", !!st.collapsed);
    if (el.classList.contains("panel-collapse-btn")) el.textContent = st && st.collapsed ? "+" : "−";
  });
  // file-tree ↔ log flex handoff: when file_tree collapsed, log fills the column
  const ft = layoutState.panels.file_tree && layoutState.panels.file_tree.collapsed;
  const lc = document.querySelector(".left-col");
  if (lc) lc.classList.toggle("file-tree-collapsed", !!ft);
  syncSidebarButtonGlyphs();
  updateRightColScrollCue();
  if (!(opts && opts.skipViewer) && viewer && typeof viewer.resize === "function") {
    if (mainViewerFraming.pending) return;
    viewer.resize();
    viewer.render();
  }
}

function toggleSidebar(side) {
  layoutState[side].collapsed = !layoutState[side].collapsed;
  saveLayoutState();
  applyLayout();
}

// Visible scroll affordance for the right column: a sticky bottom cue that
// shows while the column has more content below, hides at the end.
function updateRightColScrollCue() {
  const col = document.querySelector(".right-col");
  if (!col) return;
  let cue = document.getElementById("right-col-scroll-cue");
  if (!cue) {
    cue = document.createElement("div");
    cue.id = "right-col-scroll-cue";
    cue.className = "right-col-scroll-cue";
    cue.setAttribute("data-i18n", "layout.scroll_more");
    col.appendChild(cue);
    col.addEventListener("scroll", function() {
      if (typeof requestAnimationFrame === "function") {
        requestAnimationFrame(updateRightColScrollCue);
      } else {
        updateRightColScrollCue();
      }
    }, { passive: true });
  }
  cue.textContent = typeof t === "function" ? t("layout.scroll_more") : "↓";
  const overflow = col.scrollHeight - col.clientHeight;
  const atEnd = col.scrollTop + col.clientHeight >= col.scrollHeight - 4;
  cue.hidden = !(overflow > 8 && !atEnd);
}

function makeColResizeHandle(handle, side) {
  // side: "left" | "right" — which column the handle resizes.
  if (!handle) return;
  const MIN_W = 200, MAX_W = 500, DEFAULT_W = 240;
  let isResizing = false, startX = 0, startW = DEFAULT_W;
  function width() { return layoutState[side].width; }
  function setWidth(w) {
    layoutState[side].width = Math.min(MAX_W, Math.max(MIN_W, w));
    applyLayout({ skipViewer: true });
  }
  handle.addEventListener("mousedown", function(e) {
    e.preventDefault();
    if (layoutState[side].collapsed) return;
    isResizing = true;
    startX = e.clientX;
    startW = width();
    handle.classList.add("active");
    document.body.classList.add("resizing");
  });
  window.addEventListener("mousemove", function(e) {
    if (!isResizing) return;
    const dx = e.clientX - startX;
    setWidth(side === "left" ? startW + dx : startW - dx);
  });
  window.addEventListener("mouseup", function() {
    if (!isResizing) return;
    isResizing = false;
    handle.classList.remove("active");
    document.body.classList.remove("resizing");
    saveLayoutState();
    applyLayout();
  });
  handle.addEventListener("dblclick", function() {
    layoutState[side].width = DEFAULT_W;
    saveLayoutState();
    applyLayout();
  });
}

// Vertical splitters. sign: -1 resizes the LOWER panel (drag up grows it),
// +1 resizes the UPPER panel named by target (drag down grows it).
const V_SPLITTER_DEFS = {
  left_log:   { sign: -1, target: null,             min: 120, max: 500 },
  right_info: { sign: -1, target: null,             min: 160, max: 600 },
};

function makeVSplitter(handle, key) {
  if (!handle) return;
  const def = V_SPLITTER_DEFS[key] || { sign: -1, target: null, min: 120 };
  let isResizing = false, startY = 0, startH = 180, maxH = def.max || 400;
  function height() { return layoutState.heights[key]; }
  function setHeight(h) {
    layoutState.heights[key] = Math.min(maxH, Math.max(def.min, h));
    applyLayout({ skipViewer: true });
  }
  handle.addEventListener("mousedown", function(e) {
    e.preventDefault();
    if (def.target) {
      const targetEl = document.querySelector(def.target);
      if (targetEl && targetEl.classList.contains("collapsed")) return; // collapsed panel not draggable
    }
    isResizing = true;
    startY = e.clientY;
    startH = height();
    if (startH == null && def.target) {
      const targetEl = document.querySelector(def.target);
      if (targetEl) {
        startH = Math.round(targetEl.getBoundingClientRect().height);
        layoutState.heights[key] = startH; // seed: content-sized → fixed, no jump
      } else {
        startH = 180;
      }
    }
    if (startH == null) startH = 180;
    const col = handle.closest("aside");
    if (col) {
      if (def.sign > 0) {
        // upper-panel splitters: space available = what the queue can surrender
        const q = document.getElementById("queue-panel");
        maxH = startH + (q ? Math.max(0, Math.round(q.getBoundingClientRect().height) - 120) : 0);
      } else {
        maxH = Math.max(160, Math.round(col.getBoundingClientRect().height - 120));
      }
    }
    document.body.classList.add("resizing-v");
    handle.classList.add("active");
  });
  window.addEventListener("mousemove", function(e) {
    if (!isResizing) return;
    setHeight(startH + def.sign * (e.clientY - startY));
  });
  window.addEventListener("mouseup", function() {
    if (!isResizing) return;
    isResizing = false;
    document.body.classList.remove("resizing-v");
    handle.classList.remove("active");
    saveLayoutState();
    applyLayout();
  });
  handle.addEventListener("dblclick", function() {
    layoutState.heights[key] = LAYOUT_DEFAULTS.heights[key] == null ? null : LAYOUT_DEFAULTS.heights[key];
    saveLayoutState();
    applyLayout();
  });
}
let folderState = {};
let lastStatus = null;
let lastFileListing = [];
let folderCache = {};
// T5: tick counter used to throttle automatic file-tree refreshes while a
// job is pending/running (local every 3 ticks, remote every 6 ticks).
let treeRefreshCounter = 0;
// Q3-P0: real stdout tail polling for the live-log panel. logPollToken makes
// only the latest /logs fetch render; logCursor tracks per-stream offsets for
// incremental cursor-based fetching (replaces lastLogTailText diff key).
let logPollToken = 0;
var logCursor = { stdout: 0, stderr: 0, irc: 0, jobId: "" };
// Log store: single source of truth for log rendering. Capped at MAX_LOG_LINES;
// oldest entries dropped in chunks when exceeded. Flushed to DOM via
// requestAnimationFrame so multiple appends in the same frame produce one
// DOM mutation batch.
var logEntries = [];
var MAX_LOG_LINES = 1000;
var _logFlushScheduled = false;
var _logMediaQuery = window.matchMedia("(max-width: 1023px)");
var _logPanelState = {
  desktop: { el: null, lastRenderedIndex: 0 },
  mobile:  { el: null, lastRenderedIndex: 0 }
};
var _logTruncated = false;
// Q1: pinned result products from GET /jobs/{id}/files (only present when the
// job wrote result_summary.json). Rendered on top of the file tree.
let lastPinnedItems = [];
let lastSelectedJob = null;
let activeViewerTab = "structure";
let mechanismStudiesByJob = {};
let mechanismStudyDetailsById = {};
let mechanismStudyReportsById = {};
let mechanismViewerState = {
  jobId: "",
  studyId: "",
  pathDetail: "",
  energyDetail: "",
};
let energyGraphState = {
  jobId: "",
  data: null,
  dataRevision: "",
  seriesId: "",
  energySeriesId: "relative_energy",
  liveFollow: true,
  selectedNodeId: "",
  // "none" = just opened, no explicit pick; "user" = user picked a cycle;
  // "follow" = auto-following the latest cycle.  Only "user" activates the
  // selected-cycle convergence panel; "none"/"follow" show the task status.
  selectionOrigin: "none",
  selectedAnnotationId: "",
  annotationMode: "all",
  viewMode: "",
  xDomain: null,
  yDomain: null,
  currentDomain: null,
  viewer: null,
  loading: false,
  requestToken: 0,
  status: "idle",
  layout: null,
  loadedFrameIndex: null,
  loadedFrameId: "",
  lastFetchedJobStatus: "",
  abortController: null,
  structureAbort: null,
  structureFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
  priorViewMode: "",
  resizeObserver: null,
  viewSize: { width: 0, height: 0 },
  itemId: "",
  optimizationView: "energy",
  optimizationTool: "pan",
  optimizationDomains: {},
  optimizationPlot: null,
  optimizationViewSize: { width: 0, height: 0 },
  frameCandidates: { revision: 0, list: [] },
  jobStatus: "",
  pendingCardError: null,
  selection: null,
  hover: null,
};
let samplingState = {
  activeSubview: "energy",
  xAxis: "time_ps",
  yAxis: "energy_potential",
  jobId: "",
};
let bgIndex = 0;
let labelMode = "none";
let framePlayTimer = null;
let initialView = null;
let targetProjectAction = "move";
let targetProjectJobId = "";
let deleteJobId = "";
// Q9: job-detail cache (GET /jobs/{id}/detail projection) + queue-op state.
let jobDetailCache = {};              // jobId -> {job, stages, artifacts_summary, error_detail, disk_state, recovery, legacy?}
let jobDetailEndpointMissing = false; // /detail 404s while /jobs/{id} works → legacy server; stop retrying /detail
// GAP-9 (T13): detail freshness.  Each jobId carries a request generation;
// a response is accepted only when the generation captured at request start
// is still current, so a late response from before a row action / rerun can
// never overwrite newer state.  jobDetailCacheRevision bumps on every cache
// mutation (add / drop / recovery change) so queue rows re-render when the
// recovery matrix changes without the list rows changing.
let jobDetailGenerations = {};
let jobDetailCacheRevision = 0;
let detailActionMessage = { jobId: "", kind: "", text: "", at: 0 }; // transient feedback inside the info panel
let queueSelectedJobs = new Set();    // Q10: batch purge multi-select (job ids)
let nodesCache = [];
let backendsCache = [];
let softwareDiscoveryCache = {}; // name -> discovery entry; {} while endpoint unavailable
const BG_COLORS = ["0x0e1419", "white", "0x1a1a2e"];
const molDoc = {
  frames: [],
  currentFrame: 0,
  style: "ball-stick",
  bgColor: "0x0e1419",
  mode: "view",
  measureType: "distance",
  meta: { source: "", formula: "", charge: 0, multiplicity: 1 }
};

const STYLE_PRESETS = {
  "ball-stick": { style: { stick: { radius: 0.15 }, sphere: { scale: 0.28 } } },
  "stick": { style: { stick: { radius: 0.15 } } },
  "spacefill": { style: { sphere: { scale: 1.0 } } },
  "wireframe": { style: { line: {} } },
  "publication": { style: { stick: { radius: 0.12 }, sphere: { scale: 0.22 } } }
};

