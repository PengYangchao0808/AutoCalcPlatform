/*
 * ACP Next — exec-detail / evidence panel (UI-03 evidence slice, A-25…A-28).
 *
 * Read-only projection of ONE task's frozen identity and evidence:
 *   - identity: job id, workflow, status, attempt, plan_digest, submission_id,
 *     release_id, timestamps (the same fields the submit receipt carries);
 *   - evidence (A-28): the artifact manifest from GET /jobs/{id}/artifacts —
 *     type / relative path / size / checksum — plus a download link built ONLY
 *     from artifact_id (GET /api/v1/artifacts/{id}/download). Missing entries
 *     are an explicit empty/error state; a path is never turned into a URL;
 *   - stages (A-25): stage_index / stage_total / stage_detail / progress from
 *     the SAME GET /jobs/{id}/detail payload; unknown fields stay omitted and
 *     terminal/error states clear the strip instead of leaving stale progress;
 *   - logs (A-26): incremental polling of GET /jobs/{id}/logs (line cursor via
 *     stdout_offset/stderr_offset) plus GET /jobs/{id}/irc/log-tail (byte
 *     cursor) for IRC jobs. Only new lines are appended, the DOM keeps a tail,
 *     the cursor survives reconnect, polling pauses on suspend/hidden and every
 *     timer is cleared on dispose;
 *   - files (A-27): lazy, read-only tree from GET /jobs/{id}/files (local) or
 *     GET /jobs/{id}/remote-files (remote; relative paths are the requested
 *     directory PLUS the entry name the server returns). A directory is
 *     fetched only when it is first expanded; entries live in this selection's
 *     view only and are dropped on task switch/dispose. Text preview is gated
 *     by a hard byte/line cap BEFORE a request: files above the cap, binary /
 *     unsupported extensions and unknown sizes are refused explicitly and are
 *     never downloaded. Remote text goes through the bounded
 *     …/remote-files/{path}/preview endpoint; local text uses the existing
 *     …/files/{path} body only for files the server listing already proved
 *     small;
 *   - failure: error message + error_detail when the server has one;
 *   - handoff: open the task in the explorer or fall back to the classic page.
 *
 * Rules:
 *   - every value comes from GET /jobs/{id}/detail, GET /jobs/{id}/artifacts,
 *     the log endpoints and the file-tree/preview endpoints above; nothing is
 *     inferred, and a missing field renders as "—" (or is omitted entirely for
 *     stage/log/tree data);
 *   - one task at a time, guarded by (projectId, jobId, attempt): a late
 *     response for the previous selection can never paint over the current one;
 *   - no SSE, no write route, no second cache, no direct `fetch` — `jobDetail`
 *     owns the detail fetch and the injected `request`/transport owns artifacts,
 *     logs, tree listings and previews. The tree rows kept here are the current
 *     selection's view model (reset on switch), not a shared response cache;
 *   - preview never loads a file unbounded: size is checked against the cap
 *     first and rendered lines are capped again after decode.
 */
(function () {
  "use strict";

  var MODULE_ID = "exec-detail";

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.4.0",
    provides: ["task.detail", "task.evidence"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["task.detail", "task.evidence"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.detail.errorTitle", messageKey: "next.detail.errorMessage" },
  };

  var ZH = {
    "next.detail.empty": "选择任务后查看状态与证据。",
    "next.detail.empty_hint": "证据来自任务详情与工件清单，只读展示。",
    "next.detail.loading": "正在读取任务详情…",
    "next.detail.error": "任务详情加载失败：{message}",
    "next.detail.retry": "重试",
    "next.detail.job": "任务",
    "next.detail.status": "状态",
    "next.detail.workflow": "工作流",
    "next.detail.attempt": "尝试",
    "next.detail.plan_digest": "计划摘要",
    "next.detail.submission": "提交记录",
    "next.detail.release": "执行版本",
    "next.detail.created": "创建",
    "next.detail.completed": "完成",
    "next.detail.artifacts": "证据工件",
    "next.detail.artifacts_empty": "暂无工件记录。",
    "next.detail.artifacts_error": "工件清单读取失败：{message}",
    "next.detail.artifacts_loading": "正在读取工件清单…",
    "next.detail.artifacts_unavailable": "工件清单端点不可用：{message}",
    "next.detail.artifacts_truncated": "仅显示前 {count} 项（共 {total} 项）。",
    "next.detail.artifact_checksum_missing": "无校验和",
    "next.detail.artifact_no_id": "缺少 artifact_id，没有下载链接。",
    "next.detail.download": "下载",
    "next.detail.files": "文件",
    "next.detail.files_local": "本地",
    "next.detail.files_remote": "远程 · {node}",
    "next.detail.files_loading": "正在读取文件列表…",
    "next.detail.files_empty": "暂无文件。",
    "next.detail.files_error": "文件列表读取失败：{message}",
    "next.detail.files_unavailable": "文件端点不可用：{message}",
    "next.detail.files_no_transport": "文件端点不可用：未注入请求通道。",
    "next.detail.files_retry": "重试",
    "next.detail.files_refresh": "刷新",
    "next.detail.files_truncated": "仅显示前 {count} 项（共 {total} 项）。",
    "next.detail.files_listing_truncated": "服务端文件清单已截断。",
    "next.detail.folder_empty": "此目录为空。",
    "next.detail.folder_loading": "读取中…",
    "next.detail.folder_error": "目录读取失败：{message}",
    "next.detail.preview": "文件预览",
    "next.detail.preview_close": "关闭",
    "next.detail.preview_loading": "正在读取文件预览…",
    "next.detail.preview_empty": "文件为空。",
    "next.detail.preview_meta": "{mode} · {lines} 行 · {size}",
    "next.detail.preview_no_ext": "无扩展名",
    "next.detail.preview_refused_large": "文件过大（{size}，预览上限 {limit}），未发起下载。",
    "next.detail.preview_refused_binary": "不支持预览该文件类型（{ext}），未发起下载。",
    "next.detail.preview_refused_unknown": "文件大小未知，拒绝预览以避免整文件下载。",
    "next.detail.preview_error": "文件预览失败：{message}",
    "next.detail.preview_truncated": "内容较长，仅显示前 {lines} 行。",
    "next.detail.preview_remote_truncated": "服务端已截断预览内容。",
    "next.detail.open_task": "在任务浏览中打开",
    "next.detail.open_classic": "经典工作台",
    "next.detail.wrong_project": "该任务不属于当前项目。",
    "next.detail.attempt_changed": "任务尝试次数已变化，请刷新。",
    "next.detail.failure": "失败原因",
    "next.detail.no_digest": "—",
    "next.detail.stages": "阶段进度",
    "next.detail.stage_step": "阶段 {index}",
    "next.detail.stage_position": "{current} / {total}",
    "next.detail.logs": "实时日志",
    "next.detail.log_loading": "正在读取日志…",
    "next.detail.log_empty": "暂无日志输出。",
    "next.detail.log_paused": "日志已暂停（面板不可见）。",
    "next.detail.log_reconnecting": "日志连接中断，保留最后输出，正在重试…",
    "next.detail.log_error": "日志读取失败：{message}",
    "next.detail.log_unavailable": "日志端点不可用：{message}",
    "next.detail.log_ended": "作业已结束，显示最终日志。",
    "next.detail.log_retry": "重试",
    "next.detail.log_no_transport": "日志端点不可用：未注入请求通道。",
    "next.detail.log_irc_empty": "ORCA 输出尚未生成。",
    "next.detail.log_irc_unavailable": "ORCA 日志端点不可用：{message}",
    "next.detail.log_state_loading": "读取中",
    "next.detail.log_state_live": "实时",
    "next.detail.log_state_paused": "已暂停",
    "next.detail.log_state_reconnecting": "重连中",
    "next.detail.log_state_unavailable": "不可用",
    "next.detail.log_state_ended": "已结束",
    "next.detail.log_state_error": "读取失败",
    "next.detail.errorTitle": "任务详情不可用",
    "next.detail.errorMessage": "exec-detail 模块加载失败，请查看控制台。",
  };

  var LOG_POLL_MS = 5000;
  var LOG_FETCH_LINES = 300;
  var LOG_LINE_LIMIT = 400;
  var LOG_CATCHUP_LIMIT = 5;
  var STAGE_SEGMENT_LIMIT = 64;
  var ARTIFACT_LIMIT = 40;
  var TREE_CHILD_LIMIT = 200;
  // A-27 hard caps. The byte cap is checked against the server-reported listing
  // size BEFORE any preview request, so a large/binary file is never downloaded;
  // the line cap is enforced again on the decoded text before it reaches the DOM.
  var PREVIEW_MAX_BYTES = 256 * 1024;
  var PREVIEW_MAX_LINES = 400;
  // Text-preview allowlist. Anything else (wavefunction, checkpoint, archive,
  // image, ...) is refused explicitly as binary/unsupported, and no request is
  // made. Structure text formats are previewable; the 3D viewer is not involved.
  var PREVIEW_TEXT_EXT = {
    ".log": true,
    ".out": true,
    ".txt": true,
    ".json": true,
    ".csv": true,
    ".gjf": true,
    ".com": true,
    ".inp": true,
    ".xyz": true,
    ".sdf": true,
    ".mol": true,
    ".yaml": true,
    ".yml": true,
    ".md": true,
  };

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function shortDigest(value) {
    var text = String(value || "");
    if (!text) return "";
    var hex = text.indexOf("sha256:") === 0 ? text.slice(7) : text;
    return hex.length > 16 ? hex.slice(0, 12) + "…" + hex.slice(-4) : hex;
  }

  function formatSize(bytes) {
    var n = Number(bytes);
    if (!isFinite(n) || n < 0) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function formatTime(value) {
    if (!value) return "—";
    try {
      var d = new Date(value);
      if (isNaN(d.getTime())) return String(value);
      return d.toLocaleString();
    } catch (e) {
      return String(value);
    }
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object" && detail.message) {
        return (detail.code ? detail.code + ": " : "") + detail.message;
      }
      if (typeof detail === "string") return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function statusKind(status) {
    var text = String(status || "").toLowerCase();
    if (text === "completed") return "ok";
    if (text === "failed" || text === "cancelled") return "bad";
    if (text === "running" || text === "queued" || text === "paused" || text === "waiting_review") return "run";
    return "idle";
  }

  var TERMINAL_STATUSES = {
    completed: true,
    failed: true,
    cancelled: true,
    skipped: true,
    expired: true,
  };

  function isTerminalStatus(status) {
    return Object.prototype.hasOwnProperty.call(TERMINAL_STATUSES, String(status || "").toLowerCase());
  }

  function finiteNumber(value) {
    if (value == null || value === "") return null;
    var n = typeof value === "number" ? value : Number(value);
    return isFinite(n) ? n : null;
  }

  function positiveInt(value) {
    var n = finiteNumber(value);
    if (n == null || Math.floor(n) !== n || n < 1) return null;
    return n;
  }

  function finite01(value) {
    var n = finiteNumber(value);
    if (n == null || n < 0 || n > 1) return null;
    return n;
  }

  function clamp01(value) {
    if (value < 0) return 0;
    if (value > 1) return 1;
    return value;
  }

  function isMissingEndpoint(error) {
    return !!error && (error.status === 404 || error.status === 405);
  }

  // A-27/A-28 helpers -------------------------------------------------------- //

  // Remote detection mirrors the legacy page helper
  // (workbench/compat/shared-state.js::jobRemoteNodeName): the top-level node
  // wins, result.node is the older fallback, and "local" is the local sentinel.
  function remoteNodeName(job) {
    if (!job) return "";
    var node = job.node_id || (job.result && job.result.node) || "";
    node = String(node || "").replace(/^\s+|\s+$/g, "");
    return node && node !== "local" ? node : "";
  }

  function isRemoteJob(job) {
    return !!remoteNodeName(job);
  }

  function fileNameOf(path) {
    var text = String(path || "");
    var index = text.lastIndexOf("/");
    return index >= 0 ? text.slice(index + 1) : text;
  }

  // Remote listings expose only `name`; the relative path is the directory that
  // was requested plus that name. No other path is invented.
  function joinRelativePath(parent, name) {
    var base = String(parent || "");
    var leaf = String(name || "");
    if (!base) return leaf;
    return base.replace(/\/+$/, "") + "/" + leaf.replace(/^\/+/, "");
  }

  // Encode one path segment at a time so the path separators stay literal for
  // the FastAPI `{file_path:path}` routes (the same shape the legacy page uses).
  function encodePathSegments(path) {
    return String(path || "")
      .split("/")
      .filter(function (part) { return part.length > 0; })
      .map(function (part) { return encodeURIComponent(part); })
      .join("/");
  }

  function extensionOf(name) {
    var text = String(name || fileNameOf(name));
    var dot = text.lastIndexOf(".");
    return dot > 0 ? text.slice(dot).toLowerCase() : "";
  }

  function previewableExtension(ext) {
    return Object.prototype.hasOwnProperty.call(PREVIEW_TEXT_EXT, ext);
  }

  function createExecDetail(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var jobDetail = deps.jobDetail ||
      (typeof window !== "undefined" && window.ACPClient ? window.ACPClient.jobDetail : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var commands = deps.commands || null;
    var timerSet = typeof deps.setTimeout === "function" ? deps.setTimeout
      : (typeof setTimeout === "function" ? setTimeout : null);
    var timerClear = typeof deps.clearTimeout === "function" ? deps.clearTimeout
      : (typeof clearTimeout === "function" ? clearTimeout : null);
    var lineLimit = Number(deps.logLineLimit);
    if (!isFinite(lineLimit) || lineLimit < 1) lineLimit = LOG_LINE_LIMIT;
    var pollMs = Number(deps.logPollMs);
    if (!isFinite(pollMs) || pollMs < 0) pollMs = LOG_POLL_MS;

    function newLogState() {
      return {
        session: 0,
        timer: null,
        inFlight: false,
        started: false,
        initialized: false,
        terminal: false,
        jobId: "",
        workflow: "",
        cursors: { stdout: 0, stderr: 0, irc: 0 },
        errors: 0,
        burst: 0,
        hasMore: false,
        ircEnabled: false,
        ircUnavailable: false,
        syncTimer: false,
        state: "idle",
      };
    }

    function newFilesState() {
      // One selection's tree view model. `dirs` holds only folders this
      // selection actually expanded (root is ""), never a cross-task response
      // cache; `session` + object identity drop late responses.
      return {
        session: 0,
        jobId: "",
        remote: false,
        node: "",
        state: "idle",
        dirs: {},
        expanded: {},
        preview: null,
      };
    }

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      snapshot: null,
      projectId: "",
      ref: null,
      currentKey: "",
      ui: {},
      token: 0,
      artifactToken: 0,
      log: newLogState(),
      files: newFilesState(),
    };

    var cleanup = [];
    var treeBindings = [];

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    // Tree rows are rebuilt on every expand/collapse; bind them to a separate
    // list that is released on each render (and on dispose) instead of growing
    // the component-wide cleanup list.
    function onTree(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      treeBindings.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function clearTreeBindings() {
      treeBindings.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* node already gone */ }
      });
    }

    function documentHidden() {
      if (!doc) return false;
      if (doc.hidden === true || doc.visibilityState === "hidden") return true;
      return false;
    }

    function pausedNow() {
      return state.suspended || documentHidden();
    }

    function buildDom() {
      var root = el(doc, "div", "acp-ev");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-module", MODULE_ID);

      var head = el(doc, "header", "acp-ev-head");
      head.appendChild(el(doc, "h3", "acp-ev-title", t("next.detail.empty")));
      var badge = el(doc, "span", "acp-ev-status", "");
      badge.hidden = true;
      head.appendChild(badge);
      head.appendChild(el(doc, "p", "acp-ev-sub", ""));
      root.appendChild(head);

      var body = el(doc, "div", "acp-ev-body");
      root.appendChild(body);

      // A-25: stage progress strip. Hidden until detail carries stage fields.
      var stageSection = el(doc, "section", "acp-ev-stages");
      stageSection.hidden = true;
      stageSection.setAttribute("data-acp-stage-state", "hidden");
      var stageHead = el(doc, "div", "acp-ev-stage-head");
      stageHead.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.stages")));
      var stageCount = el(doc, "span", "acp-ev-stage-count", "");
      stageCount.hidden = true;
      stageHead.appendChild(stageCount);
      stageSection.appendChild(stageHead);
      var stageBar = el(doc, "div", "acp-ev-stage-bar");
      stageBar.hidden = true;
      stageBar.setAttribute("role", "progressbar");
      stageBar.setAttribute("aria-valuemin", "0");
      stageBar.setAttribute("aria-valuemax", "100");
      var stageFill = el(doc, "span", "acp-ev-stage-fill", "");
      stageBar.appendChild(stageFill);
      stageSection.appendChild(stageBar);
      var stageDetail = el(doc, "p", "acp-ev-stage-detail", "");
      stageDetail.hidden = true;
      stageSection.appendChild(stageDetail);
      var stageStrip = el(doc, "ol", "acp-ev-stage-strip", "");
      stageSection.appendChild(stageStrip);
      body.appendChild(stageSection);

      var grid = el(doc, "dl", "acp-ev-grid");
      grid.hidden = true;
      body.appendChild(grid);

      var failure = el(doc, "p", "acp-ev-failure");
      failure.hidden = true;
      failure.setAttribute("role", "alert");
      body.appendChild(failure);

      var artifactSection = el(doc, "section", "acp-ev-artifacts");
      artifactSection.hidden = true;
      artifactSection.setAttribute("data-acp-artifact-state", "idle");
      var artifactHead = el(doc, "div", "acp-ev-section-head");
      artifactHead.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.artifacts")));
      artifactSection.appendChild(artifactHead);
      var artifactNote = el(doc, "p", "acp-ev-note", "");
      artifactNote.setAttribute("aria-live", "polite");
      artifactSection.appendChild(artifactNote);
      var artifactList = el(doc, "ul", "acp-ev-list");
      artifactSection.appendChild(artifactList);
      var artifactRetry = el(doc, "button", "acp-btn acp-btn--ghost acp-ev-inline-btn", t("next.detail.retry"));
      artifactRetry.type = "button";
      artifactRetry.setAttribute("data-action", "retry-artifacts");
      artifactRetry.hidden = true;
      artifactSection.appendChild(artifactRetry);
      body.appendChild(artifactSection);

      // A-27: read-only file tree + bounded text preview. Hidden until a task
      // is selected; the tree itself is loaded lazily per directory.
      var fileSection = el(doc, "section", "acp-ev-files");
      fileSection.hidden = true;
      fileSection.setAttribute("data-acp-files-state", "idle");
      var fileHead = el(doc, "div", "acp-ev-section-head");
      fileHead.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.files")));
      var fileSource = el(doc, "span", "acp-ev-files-source", "");
      fileSource.hidden = true;
      fileHead.appendChild(fileSource);
      var fileRefresh = el(doc, "button", "acp-btn acp-btn--ghost acp-ev-inline-btn", t("next.detail.files_refresh"));
      fileRefresh.type = "button";
      fileRefresh.setAttribute("data-action", "refresh-files");
      fileRefresh.hidden = true;
      fileHead.appendChild(fileRefresh);
      fileSection.appendChild(fileHead);
      var fileNote = el(doc, "p", "acp-ev-note acp-ev-file-note", "");
      fileNote.setAttribute("aria-live", "polite");
      fileNote.hidden = true;
      fileSection.appendChild(fileNote);
      var fileRetry = el(doc, "button", "acp-btn acp-btn--ghost acp-ev-inline-btn", t("next.detail.files_retry"));
      fileRetry.type = "button";
      fileRetry.setAttribute("data-action", "retry-files");
      fileRetry.hidden = true;
      fileSection.appendChild(fileRetry);
      var fileTree = el(doc, "ul", "acp-ev-tree");
      fileTree.setAttribute("role", "tree");
      fileSection.appendChild(fileTree);
      var preview = el(doc, "div", "acp-ev-preview");
      preview.hidden = true;
      preview.setAttribute("data-acp-preview-state", "idle");
      var previewHead = el(doc, "div", "acp-ev-preview-head");
      previewHead.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.preview")));
      var previewPath = el(doc, "span", "acp-ev-preview-path", "");
      previewPath.hidden = true;
      previewHead.appendChild(previewPath);
      var previewMeta = el(doc, "span", "acp-ev-preview-meta", "");
      previewMeta.hidden = true;
      previewHead.appendChild(previewMeta);
      var previewClose = el(doc, "button", "acp-btn acp-btn--ghost acp-ev-inline-btn", t("next.detail.preview_close"));
      previewClose.type = "button";
      previewClose.setAttribute("data-action", "close-preview");
      previewHead.appendChild(previewClose);
      preview.appendChild(previewHead);
      var previewBody = el(doc, "pre", "acp-ev-preview-body", "");
      preview.appendChild(previewBody);
      var previewNote = el(doc, "p", "acp-ev-note acp-ev-preview-note", "");
      previewNote.setAttribute("aria-live", "polite");
      previewNote.hidden = true;
      preview.appendChild(previewNote);
      fileSection.appendChild(preview);
      body.appendChild(fileSection);

      // A-26: real-time log tail (incremental cursor polling, no SSE).
      var logSection = el(doc, "section", "acp-ev-logs");
      logSection.hidden = true;
      logSection.setAttribute("data-acp-log-state", "idle");
      var logHead = el(doc, "div", "acp-ev-log-head");
      logHead.appendChild(el(doc, "h4", "acp-ev-section-title", t("next.detail.logs")));
      var logBadge = el(doc, "span", "acp-ev-log-badge", "");
      logBadge.hidden = true;
      logHead.appendChild(logBadge);
      logSection.appendChild(logHead);
      var logBody = el(doc, "div", "acp-ev-log-body");
      logBody.setAttribute("role", "log");
      logBody.setAttribute("aria-live", "polite");
      logSection.appendChild(logBody);
      var logIrcNote = el(doc, "p", "acp-ev-note acp-ev-log-irc-note", "");
      logIrcNote.hidden = true;
      logSection.appendChild(logIrcNote);
      var logNote = el(doc, "p", "acp-ev-note acp-ev-log-note", "");
      logNote.setAttribute("aria-live", "polite");
      logSection.appendChild(logNote);
      var logRetry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.detail.log_retry"));
      logRetry.type = "button";
      logRetry.setAttribute("data-action", "retry-logs");
      logRetry.hidden = true;
      logSection.appendChild(logRetry);
      body.appendChild(logSection);

      var stateNode = el(doc, "p", "acp-ev-note", t("next.detail.loading"));
      stateNode.hidden = true;
      stateNode.setAttribute("aria-live", "polite");
      body.appendChild(stateNode);

      var actions = el(doc, "div", "acp-ev-actions");
      var open = el(doc, "button", "acp-btn acp-btn--ghost", t("next.detail.open_task"));
      open.type = "button";
      open.setAttribute("data-action", "open-task");
      open.hidden = true;
      var classic = el(doc, "a", "acp-btn acp-btn--ghost", t("next.detail.open_classic"));
      classic.setAttribute("href", "/");
      actions.appendChild(open);
      actions.appendChild(classic);
      root.appendChild(actions);

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        title: head.children[0],
        badge: badge,
        sub: head.children[2],
        stageSection: stageSection,
        stageCount: stageCount,
        stageBar: stageBar,
        stageFill: stageFill,
        stageDetail: stageDetail,
        stageStrip: stageStrip,
        grid: grid,
        failure: failure,
        artifactSection: artifactSection,
        artifactNote: artifactNote,
        artifactList: artifactList,
        artifactRetry: artifactRetry,
        fileSection: fileSection,
        fileSource: fileSource,
        fileRefresh: fileRefresh,
        fileNote: fileNote,
        fileRetry: fileRetry,
        fileTree: fileTree,
        preview: preview,
        previewPath: previewPath,
        previewMeta: previewMeta,
        previewClose: previewClose,
        previewBody: previewBody,
        previewNote: previewNote,
        logSection: logSection,
        logBadge: logBadge,
        logBody: logBody,
        logIrcNote: logIrcNote,
        logNote: logNote,
        logRetry: logRetry,
        stateNode: stateNode,
        open: open,
        emptyHint: null,
      };
      on(open, "click", function () { openTask(); });
      on(logRetry, "click", function () { retryLogs(); });
      on(artifactRetry, "click", function () { retryArtifacts(); });
      on(fileRefresh, "click", function () { reloadFiles(); });
      on(fileRetry, "click", function () { reloadFiles(); });
      on(previewClose, "click", function () { closePreview(); });
    }

    function setState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function stopLogTimer() {
      var log = state.log;
      if (!log || log.timer == null) return;
      if (timerClear) {
        try { timerClear(log.timer); } catch (e) { /* timer already gone */ }
      }
      log.timer = null;
    }

    function clearStages() {
      var ui = state.ui;
      if (!ui || !ui.stageSection) return;
      ui.stageSection.hidden = true;
      ui.stageSection.setAttribute("data-acp-stage-state", "hidden");
      ui.stageSection.removeAttribute("data-acp-stage-current");
      ui.stageSection.removeAttribute("data-acp-stage-total");
      ui.stageSection.removeAttribute("data-acp-stage-progress");
      ui.stageCount.textContent = "";
      ui.stageCount.hidden = true;
      ui.stageDetail.textContent = "";
      ui.stageDetail.hidden = true;
      ui.stageBar.hidden = true;
      ui.stageFill.setAttribute("style", "");
      ui.stageBar.removeAttribute("aria-valuenow");
      ui.stageBar.removeAttribute("aria-valuetext");
      ui.stageStrip.replaceChildren();
    }

    function clearLogs() {
      var ui = state.ui;
      stopLogTimer();
      state.log = newLogState();
      if (!ui || !ui.logSection) return;
      ui.logSection.hidden = true;
      ui.logSection.setAttribute("data-acp-log-state", "idle");
      ui.logSection.removeAttribute("data-acp-log-lines");
      ui.logBody.replaceChildren();
      ui.logBadge.hidden = true;
      ui.logBadge.textContent = "";
      ui.logIrcNote.hidden = true;
      ui.logIrcNote.textContent = "";
      ui.logNote.textContent = "";
      ui.logNote.hidden = true;
      ui.logRetry.hidden = true;
    }

    function clearArtifacts() {
      var ui = state.ui;
      state.artifactToken += 1;
      if (!ui || !ui.artifactSection) return;
      ui.artifactSection.hidden = true;
      ui.artifactSection.setAttribute("data-acp-artifact-state", "idle");
      ui.artifactNote.textContent = "";
      ui.artifactNote.hidden = true;
      ui.artifactList.replaceChildren();
      ui.artifactRetry.hidden = true;
    }

    function clearFiles() {
      var ui = state.ui;
      clearTreeBindings();
      state.files = newFilesState();
      if (!ui || !ui.fileSection) return;
      ui.fileSection.hidden = true;
      ui.fileSection.setAttribute("data-acp-files-state", "idle");
      ui.fileSection.removeAttribute("data-acp-files-source");
      ui.fileSection.removeAttribute("data-acp-files-node");
      ui.fileSource.hidden = true;
      ui.fileSource.textContent = "";
      ui.fileRefresh.hidden = true;
      ui.fileNote.textContent = "";
      ui.fileNote.hidden = true;
      ui.fileRetry.hidden = true;
      ui.fileTree.replaceChildren();
      ui.preview.hidden = true;
      ui.preview.setAttribute("data-acp-preview-state", "idle");
      ui.previewPath.textContent = "";
      ui.previewPath.hidden = true;
      ui.previewMeta.textContent = "";
      ui.previewMeta.hidden = true;
      ui.previewBody.textContent = "";
      ui.previewNote.textContent = "";
      ui.previewNote.hidden = true;
    }

    function reset() {
      clearLogs();
      clearStages();
      clearArtifacts();
      clearFiles();
      state.ui.grid.hidden = true;
      state.ui.grid.replaceChildren();
      state.ui.failure.hidden = true;
      state.ui.failure.textContent = "";
      state.ui.stateNode.hidden = true;
      state.ui.open.hidden = true;
      state.ui.badge.hidden = true;
      state.ui.badge.textContent = "";
      state.ui.title.textContent = t("next.detail.empty");
      state.ui.sub.textContent = t("next.detail.empty_hint");
      setState("empty");
    }

    function renderEmpty() {
      reset();
    }

    function renderLoading() {
      reset();
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.detail.loading");
      setState("loading");
    }

    function renderError(error) {
      reset();
      state.ui.stateNode.hidden = false;
      state.ui.stateNode.textContent = t("next.detail.error", { message: describeError(error) });
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.detail.retry"));
      retry.type = "button";
      retry.setAttribute("data-action", "retry-detail");
      on(retry, "click", function () { refresh(); });
      state.ui.stateNode.appendChild(retry);
      setState("error");
    }

    function row(label, value, mono) {
      var dt = el(doc, "dt", "", label);
      var dd = el(doc, "dd", mono ? "acp-ev-mono" : "", value == null || value === "" ? "—" : String(value));
      if (mono && value) dd.setAttribute("title", String(value));
      state.ui.grid.appendChild(dt);
      state.ui.grid.appendChild(dd);
    }

    // ---------------------------------------------------------------- A-25 -- //
    // Stage progress is a projection of the /jobs/{id}/detail payload only:
    // job.stage_index / stage_total / stage_detail / stage_progress / progress
    // (with job.live_status as the same-payload fallback). A missing total
    // omits the strip; terminal jobs and jobs with an error clear it.

    function renderStageStrip(job, detail, total, current) {
      var entries = detail && Array.isArray(detail.stages) ? detail.stages : [];
      var order = Array.isArray(job.stage_order) ? job.stage_order : [];
      var useEntries = entries.length === total;
      var useOrder = !useEntries && order.length === total;
      for (var i = 0; i < total; i++) {
        var step = el(doc, "li", "acp-ev-stage-step", "");
        var name = "";
        var stepStatus = "";
        if (useEntries) {
          var entry = entries[i] || {};
          name = entry.label || entry.stage_name || "";
          stepStatus = String(entry.status || "");
        } else if (useOrder) {
          name = order[i] ? String(order[i]) : "";
        }
        if (!stepStatus && current != null) {
          stepStatus = i + 1 < current ? "completed" : (i + 1 === current ? "running" : "pending");
        }
        step.setAttribute("data-acp-stage-step", stepStatus || "unknown");
        if (current != null && i + 1 === current) step.setAttribute("data-acp-stage-current", "true");
        var label = name ? String(name) : t("next.detail.stage_step", { index: i + 1 });
        step.setAttribute("title", label);
        step.setAttribute("aria-label", label + (stepStatus ? " · " + stepStatus : ""));
        state.ui.stageStrip.appendChild(step);
      }
    }

    function renderStages(job, detail) {
      clearStages();
      var status = String(job.status || "").toLowerCase();
      var hasError = !!job.error || !!(detail && detail.error_detail &&
        (detail.error_detail.error || detail.error_detail.stderr_tail || detail.error_detail.failed_stage));
      if (isTerminalStatus(status) || hasError) {
        state.ui.stageSection.setAttribute("data-acp-stage-state", "cleared");
        return;
      }

      var total = positiveInt(job.stage_total);
      if (total == null && job.live_status) total = positiveInt(job.live_status.stage_total);
      if (total == null) return; // stage_total unknown -> omit the whole strip

      var current = positiveInt(job.stage_index);
      if (current == null && job.live_status) current = positiveInt(job.live_status.stage_index);
      if (current != null && current > total) current = null;

      state.ui.stageSection.hidden = false;
      state.ui.stageSection.setAttribute("data-acp-stage-state", "ready");
      state.ui.stageSection.setAttribute("data-acp-stage-total", String(total));
      if (current != null) {
        state.ui.stageSection.setAttribute("data-acp-stage-current", String(current));
        state.ui.stageCount.hidden = false;
        state.ui.stageCount.textContent = t("next.detail.stage_position", { current: current, total: total });
      }

      var detailText = typeof job.stage_detail === "string" ? job.stage_detail.replace(/^\s+|\s+$/g, "") : "";
      if (detailText) {
        state.ui.stageDetail.hidden = false;
        state.ui.stageDetail.textContent = detailText;
      }

      var progress = finite01(job.progress);
      var source = progress != null ? "progress" : null;
      if (progress == null) {
        var stageProgress = finite01(job.stage_progress);
        if (stageProgress != null && current != null) {
          progress = clamp01(((current - 1) + stageProgress) / total);
          source = "stage_progress";
        }
      }
      if (progress != null) {
        var percent = Math.round(progress * 1000) / 10;
        state.ui.stageBar.hidden = false;
        state.ui.stageFill.setAttribute("style", "width:" + percent + "%");
        state.ui.stageBar.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
        state.ui.stageBar.setAttribute("aria-valuetext",
          current != null ? current + " / " + total : percent + "%");
        state.ui.stageSection.setAttribute("data-acp-stage-progress", source);
      } else {
        state.ui.stageSection.setAttribute("data-acp-stage-progress", "none");
      }

      if (total <= STAGE_SEGMENT_LIMIT) renderStageStrip(job, detail, total, current);
    }

    // ---------------------------------------------------------------- A-26 -- //
    // Incremental log tail. Endpoints (existing, verified in
    // src/acp/api/v1_routes.py):
    //   GET /api/v1/jobs/{id}/logs?lines=N[&stdout_offset=X&stderr_offset=Y]
    //     -> { stdout_lines, stderr_lines, stdout_next_offset,
    //          stderr_next_offset, has_more, ... }
    //   GET /api/v1/jobs/{id}/irc/log-tail?offset=BYTES
    //     -> { available, lines, next_offset } (byte cursor, IRC only)
    // The SSE /events endpoint carries job status events, not log lines, so it
    // is deliberately not used here.

    function logDomCount() {
      var body = state.ui.logBody;
      return body && body.children ? body.children.length : 0;
    }

    function logSectionAttr(name, value) {
      if (!state.ui.logSection) return;
      if (value == null) state.ui.logSection.removeAttribute(name);
      else state.ui.logSection.setAttribute(name, String(value));
    }

    function setLogState(name) {
      if (state.log) state.log.state = name;
      if (state.ui.logSection) state.ui.logSection.setAttribute("data-acp-log-state", name);
      var badge = state.ui.logBadge;
      if (!badge) return;
      if (name === "idle" || name === "empty") {
        badge.hidden = true;
        badge.textContent = "";
        return;
      }
      var key = "next.detail.log_state_" + name;
      var text = t(key);
      if (!text || text === key) {
        badge.hidden = true;
        badge.textContent = "";
        return;
      }
      badge.hidden = false;
      badge.textContent = text;
    }

    function setLogNote(text, showRetry) {
      if (state.ui.logNote) {
        state.ui.logNote.textContent = text || "";
        state.ui.logNote.hidden = !text;
      }
      if (state.ui.logRetry) state.ui.logRetry.hidden = !showRetry;
    }

    function setIrcNote(text) {
      if (!state.ui.logIrcNote) return;
      state.ui.logIrcNote.textContent = text || "";
      state.ui.logIrcNote.hidden = !text;
    }

    function trimLogDom() {
      var body = state.ui.logBody;
      if (!body || !body.children) return;
      while (body.children.length > lineLimit) {
        var first = body.firstChild || body.children[0];
        if (!first) break;
        if (typeof body.removeChild === "function") {
          body.removeChild(first);
        } else if (Array.isArray(body.children)) {
          body.children.shift();
          first.parentNode = null;
        } else {
          break;
        }
      }
    }

    function appendLogLines(lines, stream) {
      if (!Array.isArray(lines) || !state.ui.logBody) return 0;
      var body = state.ui.logBody;
      var added = 0;
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line == null) continue;
        var text = String(line);
        if (!text) continue;
        var node = el(doc, "span", "acp-ev-log-line", text);
        node.setAttribute("data-stream", stream);
        body.appendChild(node);
        added++;
      }
      if (!added) return 0;
      trimLogDom();
      logSectionAttr("data-acp-log-lines", logDomCount());
      try {
        if (typeof body.scrollHeight === "number" && typeof body.scrollTop === "number") {
          body.scrollTop = body.scrollHeight;
        }
      } catch (e) { /* scroll is best-effort */ }
      return added;
    }

    function linesOf(resp, stream) {
      var array = resp ? resp[stream + "_lines"] : null;
      if (Array.isArray(array)) return array;
      var text = resp ? resp[stream] : null;
      if (typeof text === "string" && text) return text.split(/\r?\n/);
      return [];
    }

    function nextOffset(value) {
      var n = finiteNumber(value);
      if (n == null || n < 0) return null;
      return Math.floor(n);
    }

    function applyLogResponse(log, resp) {
      resp = resp || {};
      appendLogLines(linesOf(resp, "stdout"), "stdout");
      appendLogLines(linesOf(resp, "stderr"), "stderr");
      var stdoutNext = nextOffset(resp.stdout_next_offset);
      if (stdoutNext != null) log.cursors.stdout = stdoutNext;
      var stderrNext = nextOffset(resp.stderr_next_offset);
      if (stderrNext != null) log.cursors.stderr = stderrNext;
      log.initialized = true;
      log.hasMore = !!resp.has_more;
      var hasLines = logDomCount() > 0;
      if (hasLines) {
        setLogState(pausedNow() ? "paused" : "live");
        setLogNote(pausedNow() ? t("next.detail.log_paused") : "", false);
      } else {
        setLogState("empty");
        setLogNote(t("next.detail.log_empty"), false);
      }
    }

    function applyIrcResponse(log, resp) {
      resp = resp || {};
      var lines = Array.isArray(resp.lines) ? resp.lines : [];
      if (resp.available === false) {
        setIrcNote(t("next.detail.log_irc_empty"));
        return;
      }
      setIrcNote("");
      appendLogLines(lines, "orca");
      var ircNext = nextOffset(resp.next_offset);
      if (ircNext != null) log.cursors.irc = ircNext;
    }

    function applyIrcError(error) {
      if (isMissingEndpoint(error)) {
        log.ircUnavailable = true;
        setIrcNote(t("next.detail.log_irc_unavailable", { message: describeError(error) }));
        return;
      }
      setIrcNote(t("next.detail.log_irc_unavailable", { message: describeError(error) }));
    }

    function logIsCurrent(log, session, gen, key) {
      return !state.disposed && state.mounted && state.log === log &&
        log.session === session && state.generation === gen &&
        state.currentKey === key && !!state.currentRef;
    }

    function clearLogTimer(log) {
      var active = log || state.log;
      if (!active || active.timer == null) return;
      if (timerClear) {
        try { timerClear(active.timer); } catch (e) { /* timer already gone */ }
      }
      active.timer = null;
    }

    function scheduleLogPoll(log, session, gen, key, delay) {
      if (!timerSet || log.syncTimer) return;
      clearLogTimer(log);
      if (!logIsCurrent(log, session, gen, key) || pausedNow()) return;
      var fired = false;
      log.timer = timerSet(function () {
        fired = true;
        log.timer = null;
        if (!logIsCurrent(log, session, gen, key)) return;
        pollLogs(log, session, gen, key);
      }, delay);
      if (fired) {
        // Legacy Node harnesses inject a synchronous setTimeout shim. The poll
        // just ran inside the scheduling call; letting its response schedule
        // another synchronous timer would spin an unbounded microtask loop.
        // Production timers are async, so this only disables polling under a
        // synchronous shim (the next explicit resume/retry can still poll).
        log.syncTimer = true;
      }
    }

    function pollIrcTail(log, session, gen, key) {
      if (!log.ircEnabled || log.ircUnavailable || !requestFn) return Promise.resolve(false);
      var url = "/api/v1/jobs/" + encodeURIComponent(log.jobId) +
        "/irc/log-tail?offset=" + String(log.cursors.irc || 0);
      return Promise.resolve().then(function () {
        return requestFn(url, { method: "GET" });
      }).then(function (resp) {
        if (!logIsCurrent(log, session, gen, key)) return false;
        applyIrcResponse(log, resp);
        return true;
      }, function (error) {
        if (!logIsCurrent(log, session, gen, key)) return false;
        applyIrcError(error);
        return false;
      });
    }

    function finishLogTick(log, session, gen, key) {
      if (!logIsCurrent(log, session, gen, key)) return;
      if (pausedNow()) {
        setLogState("paused");
        setLogNote(t("next.detail.log_paused"), false);
        return;
      }
      var hasLines = logDomCount() > 0;
      if (log.terminal) {
        setLogState(hasLines ? "ended" : "empty");
        setLogNote(hasLines ? t("next.detail.log_ended") : t("next.detail.log_empty"), false);
        clearLogTimer(log);
        return;
      }
      var delay = pollMs;
      if (log.hasMore) {
        if (log.burst < LOG_CATCHUP_LIMIT) {
          log.burst += 1;
          delay = 0;
        } else {
          log.burst = 0;
        }
      } else {
        log.burst = 0;
      }
      scheduleLogPoll(log, session, gen, key, delay);
    }

    function handleLogError(log, session, gen, key, error) {
      if (!logIsCurrent(log, session, gen, key)) return;
      log.errors += 1;
      if (pausedNow()) {
        setLogState("paused");
        setLogNote(t("next.detail.log_paused"), false);
        return;
      }
      var message = describeError(error);
      if (isMissingEndpoint(error)) {
        setLogState("unavailable");
        setLogNote(t("next.detail.log_unavailable", { message: message }), true);
        clearLogTimer(log);
        return;
      }
      if (logDomCount() === 0) {
        setLogState("error");
        setLogNote(t("next.detail.log_error", { message: message }), true);
      } else {
        setLogState("reconnecting");
        setLogNote(t("next.detail.log_reconnecting"), false);
      }
      var delay = Math.min(pollMs * Math.pow(2, Math.min(log.errors - 1, 3)), 30000);
      scheduleLogPoll(log, session, gen, key, delay);
    }

    function pollLogs(log, session, gen, key) {
      if (!logIsCurrent(log, session, gen, key)) return;
      if (pausedNow()) {
        setLogState("paused");
        setLogNote(t("next.detail.log_paused"), false);
        return;
      }
      if (log.inFlight) return;
      if (log.terminal && log.initialized) {
        finishLogTick(log, session, gen, key);
        return;
      }
      if (!requestFn) {
        setLogState("unavailable");
        setLogNote(t("next.detail.log_no_transport"), false);
        return;
      }
      clearLogTimer(log);
      log.inFlight = true;
      var url = "/api/v1/jobs/" + encodeURIComponent(log.jobId) +
        "/logs?lines=" + String(LOG_FETCH_LINES);
      if (log.initialized && (log.cursors.stdout > 0 || log.cursors.stderr > 0)) {
        url += "&stdout_offset=" + String(log.cursors.stdout) +
          "&stderr_offset=" + String(log.cursors.stderr);
      }
      Promise.resolve().then(function () {
        return requestFn(url, { method: "GET" });
      }).then(function (resp) {
        if (!logIsCurrent(log, session, gen, key)) return undefined;
        log.errors = 0;
        applyLogResponse(log, resp);
        return pollIrcTail(log, session, gen, key);
      }).then(function () {
        if (!logIsCurrent(log, session, gen, key)) return;
        log.inFlight = false;
        finishLogTick(log, session, gen, key);
      }).catch(function (error) {
        if (!logIsCurrent(log, session, gen, key)) return;
        log.inFlight = false;
        handleLogError(log, session, gen, key, error);
      });
    }

    function startLogs(job) {
      if (!state.ui.logSection) return;
      clearLogTimer();
      var log = state.log;
      log.session += 1;
      log.started = true;
      log.jobId = String(job.id || (state.currentRef && state.currentRef.jobId) || "");
      var spec = job.spec || {};
      log.workflow = String(spec.workflow || job.workflow || "");
      log.terminal = isTerminalStatus(job.status);
      log.ircEnabled = log.workflow === "irc";
      state.ui.logSection.hidden = false;
      state.ui.logBody.replaceChildren();
      setIrcNote("");
      setLogState("loading");
      setLogNote(t("next.detail.log_loading"), false);
      logSectionAttr("data-acp-log-lines", 0);
      if (!log.jobId) {
        setLogState("unavailable");
        setLogNote(t("next.detail.log_unavailable", { message: "missing job id" }), false);
        return;
      }
      if (!requestFn) {
        setLogState("unavailable");
        setLogNote(t("next.detail.log_no_transport"), false);
        return;
      }
      pollLogs(log, log.session, state.generation, state.currentKey);
    }

    function retryLogs() {
      var log = state.log;
      if (!state.currentRef || !log || !log.started) return;
      log.errors = 0;
      setLogState("loading");
      setLogNote(t("next.detail.log_loading"), false);
      clearLogTimer(log);
      pollLogs(log, log.session, state.generation, state.currentKey);
    }

    function pauseLogs() {
      var log = state.log;
      if (!log || !log.started) return;
      clearLogTimer(log);
      if (log.terminal || log.state === "unavailable" || log.state === "ended") return;
      setLogState("paused");
      setLogNote(t("next.detail.log_paused"), false);
    }

    function resumeLogs() {
      var log = state.log;
      if (!log || !log.started || !state.mounted || state.disposed || pausedNow()) return;
      if (log.terminal) {
        // A terminal job has nothing more to fetch; settle the paused tail into
        // its final explicit state.
        if (log.initialized) finishLogTick(log, log.session, state.generation, state.currentKey);
        return;
      }
      if (log.state === "unavailable" || log.state === "ended") return;
      clearLogTimer(log);
      if (log.errors > 0) {
        setLogState("reconnecting");
        setLogNote(t("next.detail.log_reconnecting"), false);
      } else if (logDomCount() > 0) {
        setLogState("live");
        setLogNote("", false);
      } else {
        setLogState("loading");
        setLogNote(t("next.detail.log_loading"), false);
      }
      pollLogs(log, log.session, state.generation, state.currentKey);
    }

    function onVisibilityChange() {
      if (state.disposed || !state.mounted) return;
      if (documentHidden()) pauseLogs();
      else resumeLogs();
    }

    // ----------------------------------------------------------------- A-27 -- //
    // File tree + bounded text preview. Endpoints (verified in
    // src/acp/api/v1_routes.py):
    //   GET /api/v1/jobs/{id}/files[?path=DIR]              local, one level
    //   GET /api/v1/jobs/{id}/files/{path}                  local text body
    //   GET /api/v1/jobs/{id}/remote-files[?path=DIR]       remote, one level
    //   GET /api/v1/jobs/{id}/remote-files/{path}/preview   remote, bounded
    // There is no local preview endpoint, so the local body is only requested
    // for a file whose listing size already proved it <= PREVIEW_MAX_BYTES.
    // A directory is fetched only when it is first expanded; there is no timer
    // and no polling — refresh is an explicit user action.

    function filesIsCurrent(files, session, gen, key) {
      return !state.disposed && state.mounted && state.files === files &&
        files.session === session && state.generation === gen &&
        state.currentKey === key && !!state.currentRef;
    }

    function setFilesState(name) {
      if (!state.ui.fileSection) return;
      state.files.state = name;
      state.ui.fileSection.setAttribute("data-acp-files-state", name);
    }

    function setFilesNote(text, showRetry) {
      var ui = state.ui;
      if (ui.fileNote) {
        ui.fileNote.textContent = text || "";
        ui.fileNote.hidden = !text;
      }
      if (ui.fileRetry) ui.fileRetry.hidden = !showRetry;
    }

    function filesUrl(files, path) {
      var url = "/api/v1/jobs/" + encodeURIComponent(files.jobId) +
        (files.remote ? "/remote-files" : "/files");
      if (path) url += "?path=" + encodeURIComponent(path);
      return url;
    }

    function emptyDirRecord() {
      return { status: "idle", error: "", entries: [], truncated: false };
    }

    // Remote entries carry only `name` + the directory that was requested; the
    // relative path is that directory plus the name (never any other guess).
    function entryFromListing(raw, parentPath, remote) {
      if (!raw || typeof raw !== "object") return null;
      var name = remote ? String(raw.name || "") : "";
      var path = remote ? joinRelativePath(parentPath, name) : String(raw.path || "");
      if (!path) return null;
      if (!name) name = fileNameOf(path);
      return {
        name: name,
        path: path,
        isDir: !!raw.is_dir,
        size: finiteNumber(raw.size),
      };
    }

    function applyDirListing(files, path, resp) {
      var raw = resp && Array.isArray(resp.files) ? resp.files : [];
      var entries = [];
      for (var i = 0; i < raw.length; i++) {
        var entry = entryFromListing(raw[i], path, files.remote);
        if (entry) entries.push(entry);
      }
      // Folders first, then name; deterministic for the same listing and
      // independent of the order the server returned.
      entries.sort(function (a, b) {
        if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
        var left = String(a.name || "");
        var right = String(b.name || "");
        return left < right ? -1 : (left > right ? 1 : 0);
      });
      var dir = files.dirs[path];
      if (!dir) {
        dir = emptyDirRecord();
        files.dirs[path] = dir;
      }
      dir.entries = entries;
      dir.status = entries.length ? "ready" : "empty";
      dir.truncated = !!(resp && resp.truncated);
      if (resp && resp.node) files.node = String(resp.node);
      if (path) files.expanded[path] = true;
      return dir;
    }

    function loadDir(files, path, session, gen, key) {
      var dir = files.dirs[path];
      if (!dir) {
        dir = emptyDirRecord();
        files.dirs[path] = dir;
      }
      dir.status = "loading";
      dir.error = "";
      if (path) files.expanded[path] = true;
      renderTree();
      var url = filesUrl(files, path);
      Promise.resolve().then(function () {
        return requestFn(url, { method: "GET" });
      }).then(function (resp) {
        if (!filesIsCurrent(files, session, gen, key)) return;
        applyDirListing(files, path, resp);
        renderTree();
        if (path === "") {
          var root = files.dirs[""];
          if (root && root.entries.length) {
            setFilesState("ready");
            setFilesNote(root.truncated ? t("next.detail.files_listing_truncated") : "", false);
          } else {
            setFilesState("empty");
            setFilesNote(t("next.detail.files_empty"), false);
          }
        }
      }, function (error) {
        if (!filesIsCurrent(files, session, gen, key)) return;
        var failed = files.dirs[path];
        if (!failed) {
          failed = emptyDirRecord();
          files.dirs[path] = failed;
        }
        failed.status = "error";
        failed.error = describeError(error);
        renderTree();
        if (path === "") {
          var missing = isMissingEndpoint(error);
          setFilesState(missing ? "unavailable" : "error");
          setFilesNote(
            missing
              ? t("next.detail.files_unavailable", { message: failed.error })
              : t("next.detail.files_error", { message: failed.error }),
            true
          );
        }
      });
    }

    function toggleDir(path) {
      var files = state.files;
      var gen = state.generation;
      var key = state.currentKey;
      if (!filesIsCurrent(files, files.session, gen, key)) return;
      var dir = files.dirs[path];
      if (dir && dir.status === "error") {
        loadDir(files, path, files.session, gen, key);
        return;
      }
      if (files.expanded[path]) {
        files.expanded[path] = false;
        renderTree();
        return;
      }
      if (dir && dir.status === "loading") return;
      if (dir && (dir.status === "ready" || dir.status === "empty")) {
        files.expanded[path] = true;
        renderTree();
        return;
      }
      files.expanded[path] = true;
      loadDir(files, path, files.session, gen, key);
    }

    function buildTreeItem(files, entry) {
      var item = el(doc, "li", "acp-ev-tree-item");
      item.setAttribute("data-acp-path", entry.path);
      item.setAttribute("data-acp-tree-kind", entry.isDir ? "dir" : "file");
      var treeRow = el(doc, "div", "acp-ev-tree-row");
      treeRow.setAttribute("data-acp-tree-row", entry.path);
      treeRow.setAttribute("role", "button");
      treeRow.setAttribute("tabindex", "0");
      treeRow.setAttribute("title", entry.path);
      var marker = el(doc, "span", "acp-ev-tree-marker", "");
      treeRow.appendChild(marker);
      treeRow.appendChild(el(doc, "span", "acp-ev-tree-name", entry.name || fileNameOf(entry.path)));
      if (entry.isDir) {
        var dir = files.dirs[entry.path];
        var expanded = !!files.expanded[entry.path];
        marker.textContent = dir && dir.status === "loading" ? "…" : (expanded ? "▾" : "▸");
        treeRow.setAttribute("aria-expanded", expanded && dir && dir.status !== "loading" ? "true" : "false");
        treeRow.setAttribute("data-acp-tree-state", dir ? dir.status : "idle");
        onTree(treeRow, "click", function () { toggleDir(entry.path); });
        item.appendChild(treeRow);
        if (expanded && dir) {
          var branch = el(doc, "ul", "acp-ev-tree-children");
          if (dir.status === "loading") {
            branch.appendChild(el(doc, "li", "acp-ev-note", t("next.detail.folder_loading")));
          } else if (dir.status === "error") {
            branch.appendChild(el(doc, "li", "acp-ev-note acp-ev-note--bad",
              t("next.detail.folder_error", { message: dir.error || "—" })));
          } else if (!dir.entries.length) {
            branch.appendChild(el(doc, "li", "acp-ev-note", t("next.detail.folder_empty")));
          } else {
            dir.entries.slice(0, TREE_CHILD_LIMIT).forEach(function (child) {
              branch.appendChild(buildTreeItem(files, child));
            });
            if (dir.entries.length > TREE_CHILD_LIMIT) {
              branch.appendChild(el(doc, "li", "acp-ev-note", t("next.detail.files_truncated", {
                count: TREE_CHILD_LIMIT,
                total: dir.entries.length,
              })));
            }
          }
          item.appendChild(branch);
        }
      } else {
        treeRow.appendChild(el(doc, "span", "acp-ev-tree-size", formatSize(entry.size)));
        treeRow.setAttribute("data-acp-tree-state", "file");
        onTree(treeRow, "click", function () { openPreview(entry); });
        item.appendChild(treeRow);
      }
      return item;
    }

    function renderTree() {
      var ui = state.ui;
      if (!ui || !ui.fileTree) return;
      clearTreeBindings();
      ui.fileTree.replaceChildren();
      var root = state.files.dirs[""];
      if (!root || root.status === "error" || !root.entries.length) return;
      var shown = root.entries.slice(0, TREE_CHILD_LIMIT);
      shown.forEach(function (entry) {
        ui.fileTree.appendChild(buildTreeItem(state.files, entry));
      });
      if (root.entries.length > shown.length) {
        ui.fileTree.appendChild(el(doc, "li", "acp-ev-note", t("next.detail.files_truncated", {
          count: shown.length,
          total: root.entries.length,
        })));
      }
    }

    function startFiles(job) {
      if (!state.ui.fileSection) return;
      clearFiles();
      var files = state.files;
      files.session += 1;
      files.jobId = String(job.id || (state.currentRef && state.currentRef.jobId) || "");
      files.remote = isRemoteJob(job);
      files.node = remoteNodeName(job);
      state.ui.fileSection.hidden = false;
      state.ui.fileSection.setAttribute("data-acp-files-source", files.remote ? "remote" : "local");
      if (files.node) state.ui.fileSection.setAttribute("data-acp-files-node", files.node);
      state.ui.fileSource.hidden = false;
      state.ui.fileSource.textContent = files.remote
        ? t("next.detail.files_remote", { node: files.node || "—" })
        : t("next.detail.files_local");
      state.ui.fileRefresh.hidden = false;
      setFilesState("loading");
      setFilesNote(t("next.detail.files_loading"), false);
      files.dirs[""] = emptyDirRecord();
      if (!files.jobId) {
        setFilesState("unavailable");
        setFilesNote(t("next.detail.files_unavailable", { message: "missing job id" }), true);
        return;
      }
      if (!requestFn) {
        setFilesState("unavailable");
        setFilesNote(t("next.detail.files_no_transport"), true);
        return;
      }
      loadDir(files, "", files.session, state.generation, state.currentKey);
    }

    function reloadFiles() {
      var files = state.files;
      var ref = state.currentRef;
      if (!files.jobId || !ref || !ref.jobId || !requestFn) return;
      if (!filesIsCurrent(files, files.session, state.generation, state.currentKey)) return;
      var gen = state.generation;
      var key = state.currentKey;
      var jobId = files.jobId;
      var remote = files.remote;
      var node = files.node;
      // Keep only the selection identity; every loaded directory/preview is
      // dropped and the session bump invalidates an in-flight older load.
      files.session += 1;
      files.dirs = {};
      files.expanded = {};
      files.preview = null;
      files.jobId = jobId;
      files.remote = remote;
      files.node = node;
      files.dirs[""] = emptyDirRecord();
      state.ui.preview.hidden = true;
      state.ui.preview.setAttribute("data-acp-preview-state", "idle");
      state.ui.previewBody.textContent = "";
      state.ui.previewNote.textContent = "";
      state.ui.previewNote.hidden = true;
      state.ui.previewPath.hidden = true;
      state.ui.previewMeta.hidden = true;
      setFilesState("loading");
      setFilesNote(t("next.detail.files_loading"), false);
      renderTree();
      loadDir(files, "", files.session, gen, key);
    }

    function previewIsCurrent(files, preview, gen, key) {
      return filesIsCurrent(files, files.session, gen, key) && files.preview === preview;
    }

    function refusePreview(preview, reason, vars) {
      preview.state = "refused";
      preview.reason = reason;
      if (reason === "large") preview.note = t("next.detail.preview_refused_large", vars);
      else if (reason === "binary") preview.note = t("next.detail.preview_refused_binary", vars);
      else preview.note = t("next.detail.preview_refused_unknown");
      renderPreview();
    }

    function openPreview(entry) {
      var files = state.files;
      var gen = state.generation;
      var key = state.currentKey;
      if (!filesIsCurrent(files, files.session, gen, key)) return;
      var ext = extensionOf(entry.name || entry.path);
      var size = finiteNumber(entry.size);
      var preview = {
        path: entry.path,
        mode: "",
        text: "",
        lines: 0,
        size: size,
        truncated: false,
        reason: "",
        state: "idle",
        note: "",
      };
      files.preview = preview;
      if (!requestFn) {
        preview.state = "error";
        preview.note = t("next.detail.files_no_transport");
        renderPreview();
        return;
      }
      if (!previewableExtension(ext)) {
        refusePreview(preview, "binary", { ext: ext || t("next.detail.preview_no_ext") });
        return;
      }
      if (size == null) {
        refusePreview(preview, "unknown", {});
        return;
      }
      if (size > PREVIEW_MAX_BYTES) {
        refusePreview(preview, "large", {
          size: formatSize(size),
          limit: formatSize(PREVIEW_MAX_BYTES),
        });
        return;
      }
      if (size === 0) {
        preview.state = "ready";
        preview.note = t("next.detail.preview_empty");
        renderPreview();
        return;
      }
      preview.state = "loading";
      preview.note = t("next.detail.preview_loading");
      renderPreview();
      var url = files.remote
        ? "/api/v1/jobs/" + encodeURIComponent(files.jobId) + "/remote-files/" +
          encodePathSegments(entry.path) + "/preview?mode=auto&lines=" + String(PREVIEW_MAX_LINES)
        : "/api/v1/jobs/" + encodeURIComponent(files.jobId) + "/files/" +
          encodePathSegments(entry.path);
      Promise.resolve().then(function () {
        return requestFn(url, { method: "GET" });
      }).then(function (resp) {
        if (!previewIsCurrent(files, preview, gen, key)) return;
        applyPreviewResponse(preview, resp, files.remote);
        renderPreview();
      }, function (error) {
        if (!previewIsCurrent(files, preview, gen, key)) return;
        preview.state = "error";
        preview.note = t("next.detail.preview_error", { message: describeError(error) });
        renderPreview();
      });
    }

    function applyPreviewResponse(preview, resp, remote) {
      var text = "";
      var mode = "";
      var serverTruncated = false;
      if (remote) {
        var payload = resp && typeof resp === "object" ? resp : {};
        mode = typeof payload.mode === "string" ? payload.mode : "";
        serverTruncated = !!payload.truncated;
        var content = payload.content;
        if (typeof content === "string") text = content;
        else if (content && typeof content === "object") text = JSON.stringify(content, null, 2);
        var reportedSize = finiteNumber(payload.size);
        if (reportedSize != null) preview.size = reportedSize;
      } else if (typeof resp === "string") {
        text = resp;
      } else if (resp && typeof resp === "object") {
        text = JSON.stringify(resp, null, 2);
      }
      // Defensive second gate: even if the server ignored the client-side size
      // check, the decoded body/line list is capped before it reaches the DOM.
      var clientCapped = false;
      if (text.length > PREVIEW_MAX_BYTES) {
        text = text.slice(0, PREVIEW_MAX_BYTES);
        clientCapped = true;
      }
      var lines = text === "" ? [] : text.replace(/\r\n/g, "\n").split("\n");
      if (lines.length > PREVIEW_MAX_LINES) {
        lines = lines.slice(0, PREVIEW_MAX_LINES);
        clientCapped = true;
      }
      preview.text = lines.join("\n");
      preview.lines = lines.length;
      preview.mode = mode || "text";
      preview.truncated = serverTruncated || clientCapped;
      preview.state = "ready";
      preview.note = "";
      if (clientCapped) {
        preview.note = t("next.detail.preview_truncated", { lines: preview.lines });
      } else if (serverTruncated) {
        preview.note = t("next.detail.preview_remote_truncated");
      }
    }

    function renderPreview() {
      var ui = state.ui;
      var preview = state.files.preview;
      if (!ui || !ui.preview) return;
      if (!preview) {
        ui.preview.hidden = true;
        ui.preview.setAttribute("data-acp-preview-state", "idle");
        ui.preview.removeAttribute("data-acp-preview-refusal");
        ui.previewBody.textContent = "";
        ui.previewBody.setAttribute("data-acp-preview-lines", "0");
        return;
      }
      ui.preview.hidden = false;
      ui.preview.setAttribute("data-acp-preview-state", preview.state || "idle");
      if (preview.reason) ui.preview.setAttribute("data-acp-preview-refusal", preview.reason);
      else ui.preview.removeAttribute("data-acp-preview-refusal");
      ui.previewPath.hidden = false;
      ui.previewPath.textContent = preview.path;
      ui.previewPath.setAttribute("title", preview.path);
      ui.previewBody.textContent = preview.text || "";
      ui.previewBody.setAttribute("data-acp-preview-lines",
        String(preview.state === "ready" ? preview.lines || 0 : 0));
      if (preview.state === "ready") {
        ui.previewMeta.hidden = false;
        ui.previewMeta.textContent = t("next.detail.preview_meta", {
          mode: preview.mode || "text",
          lines: String(preview.lines || 0),
          size: formatSize(preview.size),
        });
      } else {
        ui.previewMeta.hidden = true;
        ui.previewMeta.textContent = "";
      }
      ui.previewNote.textContent = preview.note || "";
      ui.previewNote.hidden = !preview.note;
      ui.previewNote.setAttribute("data-acp-preview-note", preview.state || "idle");
    }

    function closePreview() {
      state.files.preview = null;
      renderPreview();
    }

    // ----------------------------------------------------------------- A-28 -- //
    // Artifact manifest: the list body is GET /jobs/{id}/artifacts (type /
    // relative path / size / checksum). A download URL exists only when the
    // server supplied artifact_id; `file_path` never becomes a URL. Empty and
    // failed reads are explicit local states so the rest of the panel stays
    // usable, with a retry for the artifact section alone.

    function artifactsIsCurrent(token, gen, key) {
      return !state.disposed && state.mounted && state.generation === gen &&
        state.currentKey === key && state.artifactToken === token && !!state.currentRef;
    }

    function setArtifactState(name) {
      if (!state.ui.artifactSection) return;
      state.ui.artifactSection.setAttribute("data-acp-artifact-state", name);
    }

    function setArtifactNote(text, showRetry) {
      var ui = state.ui;
      if (ui.artifactNote) {
        ui.artifactNote.textContent = text || "";
        ui.artifactNote.hidden = !text;
      }
      if (ui.artifactRetry) ui.artifactRetry.hidden = !showRetry;
    }

    function requestArtifacts(jobId) {
      if (!requestFn) return Promise.resolve({ ok: false, error: new Error("request unavailable") });
      return Promise.resolve().then(function () {
        return requestFn(
          "/api/v1/jobs/" + encodeURIComponent(String(jobId)) + "/artifacts",
          { method: "GET" }
        );
      }).then(function (resp) {
        return { ok: true, data: resp };
      }, function (error) {
        return { ok: false, error: error };
      });
    }

    function buildArtifactRow(artifact) {
      artifact = artifact || {};
      var item = el(doc, "li", "acp-ev-artifact");
      var id = artifact.artifact_id ? String(artifact.artifact_id) : "";
      if (id) item.setAttribute("data-acp-artifact-id", id);
      var type = artifact.artifact_type ? String(artifact.artifact_type) : "";
      item.appendChild(el(doc, "span", "acp-ev-artifact-type", type || "—"));
      var filePath = artifact.file_path ? String(artifact.file_path) : "";
      var path = el(doc, "span", "acp-ev-artifact-path", filePath || "—");
      if (filePath) path.setAttribute("title", filePath);
      item.appendChild(path);
      item.appendChild(el(doc, "span", "acp-ev-artifact-size", formatSize(artifact.size_bytes)));
      var checksum = artifact.checksum ? String(artifact.checksum) : "";
      var sum = el(doc, "span", "acp-ev-artifact-checksum" + (checksum ? " acp-ev-mono" : ""),
        checksum ? shortDigest(checksum) : "—");
      sum.setAttribute("data-acp-artifact-checksum", checksum ? "present" : "missing");
      sum.setAttribute("title",
        checksum || t("next.detail.artifact_checksum_missing"));
      item.appendChild(sum);
      if (id) {
        var link = el(doc, "a", "acp-ev-artifact-link", t("next.detail.download"));
        link.setAttribute("href", "/api/v1/artifacts/" + encodeURIComponent(id) + "/download");
        link.setAttribute("download", "");
        link.setAttribute("data-acp-artifact-download", id);
        item.appendChild(link);
      } else {
        var none = el(doc, "span", "acp-ev-artifact-nodl", "—");
        none.setAttribute("title", t("next.detail.artifact_no_id"));
        none.setAttribute("data-acp-artifact-download", "none");
        item.appendChild(none);
      }
      return item;
    }

    function renderArtifacts(result) {
      var ui = state.ui;
      if (!ui || !ui.artifactSection) return;
      ui.artifactSection.hidden = false;
      ui.artifactList.replaceChildren();
      if (!result || result.ok !== true) {
        var error = (result && result.error) || new Error("unknown error");
        var missing = isMissingEndpoint(error);
        setArtifactState(missing ? "unavailable" : "error");
        setArtifactNote(
          missing
            ? t("next.detail.artifacts_unavailable", { message: describeError(error) })
            : t("next.detail.artifacts_error", { message: describeError(error) }),
          true
        );
        return;
      }
      var resp = result.data || {};
      var list = Array.isArray(resp.artifacts) ? resp.artifacts : [];
      if (!list.length) {
        setArtifactState("empty");
        setArtifactNote(t("next.detail.artifacts_empty"), false);
        return;
      }
      setArtifactState("ready");
      var shown = list.slice(0, ARTIFACT_LIMIT);
      shown.forEach(function (artifact) {
        ui.artifactList.appendChild(buildArtifactRow(artifact));
      });
      setArtifactNote(
        list.length > shown.length
          ? t("next.detail.artifacts_truncated", { count: shown.length, total: list.length })
          : "",
        false
      );
    }

    function retryArtifacts() {
      var ref = state.currentRef;
      if (!ref || !ref.jobId || !requestFn) return;
      var gen = state.generation;
      var key = state.currentKey;
      var token = state.artifactToken + 1;
      state.artifactToken = token;
      setArtifactState("loading");
      setArtifactNote(t("next.detail.artifacts_loading"), false);
      state.ui.artifactList.replaceChildren();
      requestArtifacts(ref.jobId).then(function (result) {
        if (!artifactsIsCurrent(token, gen, key)) return;
        renderArtifacts(result);
      });
    }

    // ------------------------------------------------------------ lifecycle -- //

    function renderDetail(detail, artifactResult) {
      reset();
      var job = (detail && detail.job) || {};
      var ref = state.currentRef || {};
      if (state.projectId && job.project_id && String(state.projectId) !== String(job.project_id)) {
        state.ui.title.textContent = t("next.detail.wrong_project");
        setState("error");
        return;
      }
      var attempt = job.attempt != null ? job.attempt : ref.attempt;
      if (ref.attempt != null && attempt != null && Number(ref.attempt) !== Number(attempt)) {
        state.ui.title.textContent = t("next.detail.attempt_changed");
        setState("error");
        return;
      }
      var status = String(job.status || "unknown");
      var spec = job.spec || {};
      var workflow = spec.workflow || job.workflow || "";
      var displayName = job.custom_name || job.resolved_name || spec.name || job.name || job.id || ref.jobId;
      state.ui.title.textContent = String(displayName);
      state.ui.badge.hidden = false;
      state.ui.badge.textContent = status;
      state.ui.badge.setAttribute("data-status", statusKind(status));
      state.ui.sub.textContent = [workflow || "—", t("next.detail.attempt") + " " + String(attempt || 1)]
        .join(" · ");
      state.ui.grid.hidden = false;
      row(t("next.detail.job"), job.id || ref.jobId, true);
      row(t("next.detail.workflow"), workflow);
      row(t("next.detail.plan_digest"), job.plan_digest ? shortDigest(job.plan_digest) : "", true);
      row(t("next.detail.submission"), job.submission_id || "", true);
      row(t("next.detail.release"), job.release_id || "", true);
      row(t("next.detail.created"), formatTime(job.created_at));
      row(t("next.detail.completed"), formatTime(job.completed_at));

      var errorText = job.error || (detail && detail.error_detail && (detail.error_detail.message || detail.error_detail.summary));
      if (errorText) {
        state.ui.failure.hidden = false;
        state.ui.failure.textContent = t("next.detail.failure") + "：" + String(errorText);
      }

      renderArtifacts(artifactResult);
      renderStages(job, detail);
      startFiles(job);
      startLogs(job);
      state.ui.open.hidden = !state.currentRef;
      setState("ready");
    }

    function openTask() {
      if (!commands || typeof commands.openTask !== "function" || !state.currentRef) return;
      try {
        commands.openTask({
          jobId: String(state.currentRef.jobId),
          attempt: state.currentRef.attempt != null ? state.currentRef.attempt : null,
          source: MODULE_ID,
        });
      } catch (e) {
        if (typeof console !== "undefined" && console.error) console.error("openTask failed", e);
      }
    }

    function refresh() {
      var ref = state.currentRef;
      if (!ref || !ref.jobId) return;
      var key = String(state.projectId || "") + ":" + String(ref.jobId) + ":" + String(ref.attempt == null ? "" : ref.attempt);
      state.currentKey = key;
      var token = state.token + 1;
      state.token = token;
      var gen = state.generation;
      renderLoading();
      var detailPromise = jobDetail && typeof jobDetail.fetchDetail === "function"
        ? jobDetail.fetchDetail(String(ref.jobId))
        : Promise.reject(new Error("jobDetail.fetchDetail unavailable"));
      var artifactPromise = requestArtifacts(ref.jobId);
      Promise.all([detailPromise, artifactPromise]).then(function (results) {
        if (state.disposed || !state.mounted || state.generation !== gen || state.token !== token) return;
        if (state.currentKey !== key) return;
        renderDetail(results[0], results[1]);
      }).catch(function (error) {
        if (state.disposed || !state.mounted || state.generation !== gen || state.token !== token) return;
        if (state.currentKey !== key) return;
        renderError(error);
      });
    }

    function applySnapshot(snapshot) {
      state.snapshot = snapshot || null;
      state.projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var ref = snapshot && snapshot.taskRef ? snapshot.taskRef : null;
      if (!ref || !ref.jobId) {
        state.currentRef = null;
        state.currentKey = "";
        state.token += 1;
        if (state.mounted) renderEmpty();
        return;
      }
      var next = { jobId: String(ref.jobId), attempt: ref.attempt == null ? null : Number(ref.attempt), taskId: ref.taskId };
      var key = state.projectId + ":" + next.jobId + ":" + String(next.attempt == null ? "" : next.attempt);
      state.currentRef = next;
      if (key === state.currentKey) return;
      state.currentKey = key;
      refresh();
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("exec-detail: mount host missing");
      if (!doc) throw new Error("exec-detail: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      var context = api.context || deps.context || null;
      buildDom();
      renderEmpty();
      if (context && typeof context.getSnapshot === "function") applySnapshot(context.getSnapshot());
      if (context && typeof context.subscribe === "function") {
        var unsubscribe = context.subscribe(function (snapshot) {
          if (state.disposed) return;
          applySnapshot(snapshot);
        });
        cleanup.push(unsubscribe);
      }
      var langHandler = function () {
        if (state.disposed) return;
        if (state.currentRef) {
          // renderStages/startLogs consume the server payload; a language change
          // must not reuse stale translated log labels, so refresh the panel.
          refresh();
        } else {
          renderEmpty();
        }
      };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      on(doc, "visibilitychange", onVisibilityChange);
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
    }

    function suspend() {
      state.suspended = true;
      pauseLogs();
    }

    function resume() {
      state.suspended = false;
      resumeLogs();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.token += 1;
      state.artifactToken += 1;
      clearLogTimer();
      clearTreeBindings();
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.log = newLogState();
      state.files = newFilesState();
      state.currentRef = null;
      state.currentKey = "";
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
    };
  }

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createExecDetail,
  };
})();
