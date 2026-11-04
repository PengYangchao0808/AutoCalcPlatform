/*
 * ACP Next — nmr-report (A-52 / A-53, P18 NMR report).
 *
 * Read-only DP4/DP5 report viewer for one NMR task.  The report is the
 * server-produced `nmr_report.json` product (schema v2, written by
 * `cccp.analysis.nmr.report.write_json_report` through
 * `acp.workflows.nmr`); this module NEVER computes or re-ranks a DP4/DP5
 * probability.  Every displayed score is the server value, and a missing value
 * is rendered as an em dash — never as 0 and never as a locally derived number.
 *
 * Data contract (existing endpoints only; no new route is invented):
 *   local  GET {API_BASE}/jobs/{job_id}/files/{RESULT/reports/nmr_report.json}
 *   remote GET {API_BASE}/jobs/{job_id}/remote-files/{…}/preview?mode=report
 *   local  GET {API_BASE}/jobs/{job_id}/files/{WORK/02_SEARCH/<label>/conformers/input.xyz}
 *   remote GET {API_BASE}/jobs/{job_id}/remote-files/{…}/preview?mode=structure
 * The report file remains the historical fallback chain
 * (`RESULT/reports/…` → `reports/…` → root `nmr_report.json`).  The remote
 * envelope is unwrapped exactly like the classic reader:
 * `content.type === "json_report"` → `content.report`.
 *
 * A-52 requirements pinned here:
 *   - candidate list + metric values + assignment table + assignment
 *     highlight (row ↔ observation ↔ atom/signal-group linkage);
 *   - no report / no candidates / no DP4-DP5 probability / no spectrum data
 *     are explicit EMPTY states (never a fabricated score or a fake winner);
 *   - `summary.winner` from the server is the only source of the winner badge
 *     — the module never ranks candidates itself;
 *   - the report is frozen in memory (no mutation) and the module exposes the
 *     report bytes it was given unchanged.
 *
 * A-53 requirements pinned here:
 *   - candidate switching is a read-only view change over the already loaded
 *     report: no refetch, no recomputation, no mutation;
 *   - a generation + select-token guard makes A→B→A and any late report /
 *     geometry response unable to paint into the newer selection;
 *   - the active tab and the scroll position are preserved locally across a
 *     candidate switch.
 *
 * Data access is strictly injected:
 *   1. `deps.request`                         request-like `(path, opts) → body`
 *   2. `deps.client.transport.request`        the Shell transport chokepoint
 *   3. `window.ACPClient.transport.request`   page-level fallback
 * There is no direct `fetch`, no XHR and no second request stack.  When none
 * exists the module renders an explicit error and performs no network call.
 *
 * `deps.jobDetail.fetchDetail(jobId)` (or `window.ACPClient.jobDetail`) is an
 * OPTIONAL reader used only to learn `spec.workflow` and `node_id` (remote or
 * local).  It is the existing client wrapper for `GET /jobs/{id}`; when it is
 * absent the module treats the job as local and still reads the report.
 *
 * Classic browser script: no build, no ESM, no globals besides
 * `window.ACPNextModules`.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "nmr-report";
  var DEFAULT_API_BASE = "/api/v1";
  var NMR_WORKFLOW = "nmr";

  /* Physical layout written by acp.workflows.nmr (`RESULT/reports/…`) first;
   * the two older spellings stay readable for historical jobs. */
  var REPORT_PATHS = [
    "RESULT/reports/nmr_report.json",
    "reports/nmr_report.json",
    "nmr_report.json",
  ];
  var GEOMETRY_DIR = "WORK/02_SEARCH";
  var GEOMETRY_SUFFIX = "/conformers/input.xyz";
  var GEOMETRY_FALLBACK = "input.xyz";

  var TABS = ["assignment", "peaks", "metrics", "structure"];
  var MAX_LISTED_CANDIDATES = 8;
  var MAX_WARNINGS = 8;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["nmr.report"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["nmr.report"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.nmr.errorTitle", messageKey: "next.nmr.errorMessage" },
  };

  /* Inline zh source of truth.  The Shell dictionary is consulted first; its
   * `t()` echoes the key when a translation is missing, so these literals are
   * the real fallback until the `next.nmr.*` block lands in
   * frontend/js/shell/i18n.js (see README integration steps). */
  var ZH = {
    "next.nmr.title": "NMR 报告（DP4/DP5）",
    "next.nmr.subtitle": "只读 · 服务端 DP4/DP5 报告与归属",
    "next.nmr.badge.dp": "DP4/DP5",
    "next.nmr.badge.readonly": "只读",
    "next.nmr.badge.warnings": "数据不全（{count}）",
    "next.nmr.refresh": "刷新",
    "next.nmr.loading": "正在读取 NMR 报告…",
    "next.nmr.error": "NMR 报告加载失败：{message}",
    "next.nmr.retry": "重试",
    "next.nmr.no_transport": "未注入请求通道（deps.request / client.transport.request），无法读取 NMR 报告。",
    "next.nmr.forbidden": "当前身份无权读取该任务的 NMR 报告。",
    "next.nmr.invalid_report": "nmr_report.json 不是有效的 JSON 报告。",
    "next.nmr.read_failed": "读取 nmr_report.json 失败：{message}",
    "next.nmr.no_job": "未选择任务。",
    "next.nmr.no_job_hint": "在任务列表或任务详情中选中一个 NMR 任务后，本面板会读取该任务的 nmr_report.json。",
    "next.nmr.not_nmr": "当前任务不是 NMR 任务（{workflow}）。",
    "next.nmr.not_nmr_hint": "nmr-report 只消费 workflow=nmr 的任务产物；不会为其它工作流显示或推测 DP4/DP5 分数。",
    "next.nmr.no_report": "未找到 nmr_report.json。",
    "next.nmr.no_report_hint": "任务可能未完成、报告已过期或尚未发布。本模块不会估算、补造或本地计算 DP4/DP5 分数。",
    "next.nmr.no_candidates": "报告中没有候选。",
    "next.nmr.no_candidates_hint": "nmr_report.json 的 candidates 为空；没有候选就没有可展示的 DP4/DP5 或归属。",
    "next.nmr.no_dp4": "报告没有 DP4/DP5 概率。",
    "next.nmr.no_dp4_hint": "报告含 {count} 个候选（{labels}），但服务端没有给出 DP4/DP5 概率。原因：{reasons}。本模块不会本地计算或补造分数。",
    "next.nmr.no_spectrum": "报告没有谱图/归属数据。",
    "next.nmr.no_spectrum_hint": "report 中没有 processing_quality、归属行或 DP4/DP5 概率；没有谱图数据时保持空态，不会伪造峰表或分数。",
    "next.nmr.empty": "没有可显示的 NMR 报告。",
    "next.nmr.legacy_note": "历史报告：没有 schema_version，验证状态未知；本模块只读显示，不会自动升级报告。",
    "next.nmr.unknown_schema": "报告 schema_version={version}（当前读取器按 v2 显示）；未知字段只显示不解释。",
    "next.nmr.placeholder_note": "报告标注 DP4/DP5 使用占位误差模型参数，仅具相对意义。",
    "next.nmr.summary.title": "报告摘要（服务端值）",
    "next.nmr.winner_title": "服务端胜出候选",
    "next.nmr.winner_none": "服务端未给出可排名的胜出候选",
    "next.nmr.winner_tag": "胜出",
    "next.nmr.not_rankable": "服务端未给出可排名胜出候选（不会本地排名）",
    "next.nmr.label.job": "任务",
    "next.nmr.label.report": "报告路径",
    "next.nmr.label.candidates": "候选数",
    "next.nmr.label.winner": "胜出候选",
    "next.nmr.label.winner_dp4": "胜出候选 DP4",
    "next.nmr.label.winner_dp5": "胜出候选 DP5",
    "next.nmr.label.nuclei": "核素",
    "next.nmr.label.ranking": "DP4 排名",
    "next.nmr.label.error_model": "误差模型",
    "next.nmr.label.dp5_mode": "DP5 模式",
    "next.nmr.label.protocol": "协议指纹",
    "next.nmr.label.status": "状态",
    "next.nmr.label.dp4": "DP4",
    "next.nmr.label.dp5": "DP5",
    "next.nmr.label.probability": "概率",
    "next.nmr.label.calibration": "校准",
    "next.nmr.label.mode": "模式",
    "next.nmr.label.model": "模型",
    "next.nmr.label.reasons": "原因",
    "next.nmr.label.analysis_status": "分析状态",
    "next.nmr.label.conformers": "构象数",
    "next.nmr.label.coverage": "覆盖",
    "next.nmr.label.assignment_count": "归属行数",
    "next.nmr.label.expected": "期望信号",
    "next.nmr.label.matched": "匹配信号",
    "next.nmr.label.selected": "已选构象",
    "next.nmr.label.successful": "成功构象",
    "next.nmr.label.population": "布居",
    "next.nmr.label.flags": "质量标记",
    "next.nmr.label.nucleus": "核素",
    "next.nmr.label.element": "元素",
    "next.nmr.label.slope": "斜率",
    "next.nmr.label.intercept": "截距",
    "next.nmr.label.r2_regression": "R²（回归）",
    "next.nmr.label.r2_prediction": "R²（预测）",
    "next.nmr.label.mae": "MAE",
    "next.nmr.label.observation": "观测峰",
    "next.nmr.label.claim_atoms": "归属原子",
    "next.nmr.label.signal_group": "信号组",
    "next.nmr.label.conflict": "冲突",
    "next.nmr.label.atom": "原子",
    "next.nmr.label.exp_ppm": "实验 δ/ppm",
    "next.nmr.label.calc_ppm": "计算 δ/ppm",
    "next.nmr.label.scaled_ppm": "缩放 δ/ppm",
    "next.nmr.label.residual": "残差（scaled − exp）",
    "next.nmr.label.reference_method": "参考方法",
    "next.nmr.label.reference_ppm": "参考 δ/ppm",
    "next.nmr.label.formal_usable": "正式可用",
    "next.nmr.label.n_claims": "认领数",
    "next.nmr.label.candidate_indices": "候选索引",
    "next.nmr.label.diagnostics": "诊断计数",
    "next.nmr.tab.assignment": "归属",
    "next.nmr.tab.peaks": "观测峰",
    "next.nmr.tab.metrics": "指标",
    "next.nmr.tab.structure": "结构（只读）",
    "next.nmr.assignment.title": "归属表（服务端）",
    "next.nmr.assignment.hint": "点击归属行或观测峰可高亮同观测/同信号组的归属；高亮仅为本地视图状态。",
    "next.nmr.assignment.empty": "该候选没有归属数据（服务端 assignment 为空）。",
    "next.nmr.assignment.clear": "清除高亮",
    "next.nmr.assignment.none_selected": "未选择归属；点击行或原子标签进行关联高亮。",
    "next.nmr.assignment.highlight_atom": "原子 {atom}",
    "next.nmr.assignment.highlight_observation": "观测峰 {observation}",
    "next.nmr.assignment.highlight_related": "关联 {count} 行",
    "next.nmr.assignment.highlight_none": "当前视图没有匹配的归属行",
    "next.nmr.peaks.title": "观测峰认领（服务端 claims）",
    "next.nmr.peaks.hint": "来自诊断冲突矩阵 claims；没有 diagnostics 时按归属行的 observation_id 归并显示。",
    "next.nmr.peaks.empty": "没有观测峰认领数据。",
    "next.nmr.peaks.summary": "{n} 个观测峰 · 冲突 {conflicts} · 留一信号胜出翻转 {flips}",
    "next.nmr.peaks.current_none": "当前候选未认领该观测峰。",
    "next.nmr.metrics.title": "候选指标（服务端值）",
    "next.nmr.metrics.probability": "概率状态",
    "next.nmr.metrics.coverage": "覆盖与构象",
    "next.nmr.metrics.evidence": "证据门",
    "next.nmr.metrics.regression": "线性回归",
    "next.nmr.metrics.ensemble": "构象集合质量",
    "next.nmr.metrics.processing": "谱图处理",
    "next.nmr.metrics.diagnostics": "风险诊断计数",
    "next.nmr.metrics.no_data": "服务端未提供该项数据。",
    "next.nmr.metrics.no_regression": "该候选没有回归结果。",
    "next.nmr.metrics.no_processing": "报告没有谱图处理记录（processing_quality 为空）。",
    "next.nmr.metrics.no_diagnostics": "报告没有诊断块（diagnostics 为空）。",
    "next.nmr.metrics.no_probability": "服务端未提供类型化概率块（probability 为空）。",
    "next.nmr.structure.title": "结构几何（只读）",
    "next.nmr.structure.hint": "几何来自任务文件；本面板不渲染 3D，只显示原子数与元素组成，用于归属原子标签校验。",
    "next.nmr.structure.loading": "正在读取候选几何…",
    "next.nmr.structure.unavailable": "未找到候选几何文件（报告与归属仍可查看）。",
    "next.nmr.structure.error": "候选几何读取失败：{message}",
    "next.nmr.structure.ready": "原子 {count} · {composition}",
    "next.nmr.structure.missing_labels": "以下归属标签未在几何中找到：{labels}",
    "next.nmr.structure.load": "读取结构几何",
    "next.nmr.structure.reload": "重新读取",
    "next.nmr.warn.title": "数据不全",
    "next.nmr.warn.legacy_schema": "报告没有 schema_version：历史报告，验证状态未知。",
    "next.nmr.warn.unknown_schema": "报告 schema_version={version}，读取器按 v2 显示。",
    "next.nmr.warn.missing_summary": "报告没有 summary 块。",
    "next.nmr.warn.missing_nuclei": "报告 summary.nuclei 为空。",
    "next.nmr.warn.missing_processing": "报告没有 processing_quality（谱图处理）记录。",
    "next.nmr.warn.missing_diagnostics": "报告没有 diagnostics（峰认领/风险）记录。",
    "next.nmr.warn.missing_assignments": "该候选没有归属行。",
    "next.nmr.warn.missing_regression": "该候选没有回归结果。",
    "next.nmr.warn.missing_coverage": "该候选缺少 coverage 覆盖计数。",
    "next.nmr.warn.missing_probability": "该候选没有类型化概率块。",
    "next.nmr.warn.dp4_unavailable": "DP4 概率不可用（{status}{reasons}）。",
    "next.nmr.warn.dp5_unavailable": "DP5 概率不可用（{status}{reasons}）。",
    "next.nmr.warn.not_rankable": "报告没有胜出候选；DP4 排名不作为本地计算。",
    "next.nmr.warn.analysis_status": "候选分析状态：{status}。",
    "next.nmr.warn.placeholder_dp5": "DP5 为占位/诊断值，不作为概率。",
    "next.nmr.warn.geometry_missing_labels": "归属标签未在几何中找到：{labels}。",
    "next.nmr.status.valid": "有效",
    "next.nmr.status.invalid": "无效",
    "next.nmr.status.evidence_insufficient": "证据不足",
    "next.nmr.status.unavailable": "不可用",
    "next.nmr.status.not_applicable": "不适用",
    "next.nmr.status.placeholder": "占位",
    "next.nmr.status.ok": "正常",
    "next.nmr.status.degraded": "降级",
    "next.nmr.status.unknown": "未知",
    "next.nmr.ranking.normal": "正常（服务端）",
    "next.nmr.ranking.not_applicable": "不适用（候选不足）",
    "next.nmr.errorTitle": "NMR 报告不可用",
    "next.nmr.errorMessage": "nmr-report 模块加载失败，请查看控制台。",
  };

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var value = hasOwn(ZH, key) ? ZH[key] : key;
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

  function i18nEl(doc, tag, cls, key, vars) {
    var node = el(doc, tag, cls, t(key, vars));
    node.setAttribute("data-i18n", key);
    return node;
  }

  function applyI18n(root) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.applyI18n === "function" && root) {
      try { shell.applyI18n(root); } catch (e) { /* zh literals stay as the default */ }
    }
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function asString(value) {
    return typeof value === "string" ? value : "";
  }

  function asFiniteNumber(value) {
    if (value == null || value === "") return null;
    var n = Number(value);
    return isFinite(n) ? n : null;
  }

  function fmtNumber(value, digits) {
    var n = asFiniteNumber(value);
    if (n == null) return "—";
    var places = typeof digits === "number" && digits >= 0 ? digits : 3;
    return n.toFixed(places);
  }

  function fmtProbability(value) {
    return fmtNumber(value, 3);
  }

  function rawText(value) {
    if (value == null) return "null";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    try { return JSON.stringify(value); } catch (e) { return String(value); }
  }

  function displayValue(value) {
    if (value == null) return "—";
    if (typeof value === "string") return value || "—";
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (Array.isArray(value)) return listText(value);
    try { return truncate(JSON.stringify(value), 160) || "—"; } catch (e) { return "—"; }
  }

  function listText(value, limit) {
    if (!Array.isArray(value) || !value.length) return "—";
    var max = typeof limit === "number" && limit > 0 ? limit : value.length;
    var parts = value.slice(0, max).map(function (item) {
      return item == null ? "" : String(item);
    }).filter(function (item) { return item !== ""; });
    if (!parts.length) return "—";
    return parts.join(", ") + (value.length > max ? " …" : "");
  }

  function truncate(value, length) {
    var text = asString(value);
    if (!text) return "";
    return text.length > length ? text.slice(0, length - 1) + "…" : text;
  }

  function normalizeSymbol(raw) {
    var text = asString(raw).trim();
    if (!text) return "";
    return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
  }

  function errorInfo(error) {
    var info = { status: null, code: "", message: "", rule: "" };
    if (!error) { info.message = "unknown error"; return info; }
    if (typeof error.status === "number") info.status = error.status;
    else if (typeof error.httpStatus === "number") info.status = error.httpStatus;
    else if (typeof error.status === "string" && error.status.trim() !== "") {
      var parsed = Number(error.status);
      if (isFinite(parsed)) info.status = parsed;
    }
    var body = error.body;
    var detail = isPlainObject(body) ? body.detail : null;
    if (typeof detail === "string") {
      info.message = detail;
    } else if (isPlainObject(detail)) {
      if (detail.code) info.code = String(detail.code);
      if (detail.message) info.message = String(detail.message);
      if (isPlainObject(detail.params) && detail.params.rule) info.rule = String(detail.params.rule);
    }
    if (!info.message && error.message) info.message = String(error.message);
    if (!info.code && typeof error.code === "string") info.code = error.code;
    return info;
  }

  function describeError(error) {
    var info = errorInfo(error);
    return info.code ? info.code + ": " + info.message : info.message;
  }

  function isNotFound(error) {
    var info = errorInfo(error);
    return info.status === 404 || info.status === 405 || info.code === "not_found";
  }

  function isForbidden(error) {
    var info = errorInfo(error);
    return info.status === 401 || info.status === 403 || info.code === "access_denied";
  }

  function remoteNodeName(job) {
    if (!job || typeof job !== "object") return "";
    var node = job.node_id || (isPlainObject(job.result) ? job.result.node : "") || "";
    node = String(node || "").trim();
    return node && node !== "local" ? node : "";
  }

  function encodePath(path) {
    return String(path || "").split("/").map(encodeURIComponent).join("/");
  }

  function deepEqual(a, b) {
    try { return JSON.stringify(a) === JSON.stringify(b); } catch (e) { return a === b; }
  }

  function createNmrReport(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);

    function requestFn() {
      if (typeof deps.request === "function") return deps.request;
      var client = deps.client || (typeof window !== "undefined" ? window.ACPClient : null);
      if (client && client.transport && typeof client.transport.request === "function") {
        return function (path, opts) { return client.transport.request(path, opts); };
      }
      return null;
    }

    function jobDetailReader() {
      if (deps.jobDetail && typeof deps.jobDetail.fetchDetail === "function") return deps.jobDetail;
      var client = deps.client || (typeof window !== "undefined" ? window.ACPClient : null);
      if (client && client.jobDetail && typeof client.jobDetail.fetchDetail === "function") {
        return client.jobDetail;
      }
      return null;
    }

    function apiBase() {
      if (typeof deps.apiBase === "string" && deps.apiBase) return deps.apiBase;
      if (typeof window !== "undefined" && typeof window.API_BASE === "string" && window.API_BASE) {
        return window.API_BASE;
      }
      return DEFAULT_API_BASE;
    }

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      snapshot: null,
      projectId: "",
      jobId: "",
      jobName: "",
      workflow: "",
      jobKey: "",
      isRemote: false,
      report: null,
      reportPath: "",
      normalized: null,
      reportWarnings: [],
      candidates: [],
      selectedPos: -1,
      selectedCandidateIndex: null,
      previousSelectedPos: -1,
      previousSelectedIndex: null,
      selection: null,
      activeTab: "assignment",
      loading: false,
      errorText: "",
      errorCode: "",
      emptyReason: "no_job",
      emptyDetail: "",
      loadToken: 0,
      selectToken: 0,
      geometryToken: 0,
      geometry: {
        status: "idle",
        candidatePos: -1,
        path: "",
        atoms: [],
        elements: [],
        warnings: [],
        error: "",
      },
      ui: {},
    };

    var cleanup = [];

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    // ── guards ──────────────────────────────────────────────────────────

    function isCurrent(gen, token) {
      return !state.disposed && state.mounted && state.generation === gen && state.loadToken === token;
    }

    function isSelectionCurrent(gen, selectToken) {
      return !state.disposed && state.mounted && state.generation === gen && state.selectToken === selectToken;
    }

    function isGeometryCurrent(gen, selectToken, token, candidatePos) {
      return !state.disposed && state.mounted && state.generation === gen &&
        state.selectToken === selectToken && state.geometryToken === token &&
        state.selectedPos === candidatePos;
    }

    // ── generic DOM helpers ─────────────────────────────────────────────

    function setState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function setRootAttr(name, value) {
      if (!state.ui.root) return;
      if (value == null || value === "") state.ui.root.removeAttribute(name);
      else state.ui.root.setAttribute(name, String(value));
    }

    function metaRow(dl, field, labelKey, valueText, opts) {
      opts = opts || {};
      var dt = i18nEl(doc, "dt", "", labelKey);
      var dd = el(doc, "dd", opts.mono ? "acp-nmr-mono" : "", valueText);
      dd.setAttribute("data-field", field);
      if (opts.value != null) dd.setAttribute("data-value", rawText(opts.value));
      if (opts.title && valueText) dd.setAttribute("title", String(valueText));
      dl.appendChild(dt);
      dl.appendChild(dd);
      return dd;
    }

    function statusLabel(status) {
      var key = "next.nmr.status." + String(status || "unknown").toLowerCase();
      return hasOwn(ZH, key) ? t(key) : String(status || "—");
    }

    function rankingLabel(value) {
      var key = "next.nmr.ranking." + String(value || "not_applicable").toLowerCase();
      return hasOwn(ZH, key) ? t(key) : String(value || "—");
    }

    function warning(code, key, vars) {
      return { code: code, key: key, vars: vars || null };
    }

    // ── static DOM ──────────────────────────────────────────────────────

    function buildDom() {
      var root = el(doc, "div", "acp-nmr");
      root.setAttribute("data-module", MODULE_ID);
      root.setAttribute("data-acp-module-state", "empty");

      var head = el(doc, "header", "acp-nmr-head");
      var top = el(doc, "div", "acp-nmr-head-top");
      var title = i18nEl(doc, "h3", "acp-nmr-title", "next.nmr.title");
      var dpBadge = i18nEl(doc, "span", "acp-nmr-badge", "next.nmr.badge.dp");
      dpBadge.setAttribute("data-kind", "dp");
      var readBadge = i18nEl(doc, "span", "acp-nmr-badge acp-nmr-badge--muted", "next.nmr.badge.readonly");
      readBadge.setAttribute("data-kind", "readonly");
      var warnBadge = el(doc, "span", "acp-nmr-badge acp-nmr-badge--warn", "");
      warnBadge.hidden = true;
      warnBadge.setAttribute("data-acp-nmr", "warning-badge");
      var refresh = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-nmr-refresh", "next.nmr.refresh");
      refresh.type = "button";
      refresh.setAttribute("data-action", "refresh");
      top.appendChild(title);
      top.appendChild(dpBadge);
      top.appendChild(readBadge);
      top.appendChild(warnBadge);
      top.appendChild(refresh);
      head.appendChild(top);
      var sub = el(doc, "p", "acp-nmr-sub", "");
      head.appendChild(sub);
      root.appendChild(head);

      var body = el(doc, "div", "acp-nmr-body");
      root.appendChild(body);

      var stateNode = el(doc, "p", "acp-nmr-state", "");
      stateNode.hidden = true;
      stateNode.setAttribute("aria-live", "polite");
      body.appendChild(stateNode);

      var emptyHint = el(doc, "p", "acp-nmr-empty-hint", "");
      emptyHint.hidden = true;
      body.appendChild(emptyHint);

      var retry = i18nEl(doc, "button", "acp-btn acp-nmr-retry", "next.nmr.retry");
      retry.type = "button";
      retry.setAttribute("data-action", "retry");
      retry.hidden = true;
      body.appendChild(retry);

      var ready = el(doc, "section", "acp-nmr-ready");
      ready.hidden = true;
      body.appendChild(ready);

      var summary = el(doc, "section", "acp-nmr-summary");
      summary.appendChild(i18nEl(doc, "h4", "acp-nmr-section-title", "next.nmr.summary.title"));
      var summaryList = el(doc, "dl", "acp-nmr-meta");
      summary.appendChild(summaryList);
      var legacyNote = el(doc, "p", "acp-nmr-note acp-nmr-note--warn", "");
      legacyNote.hidden = true;
      legacyNote.setAttribute("data-acp-nmr", "legacy-note");
      summary.appendChild(legacyNote);
      var notice = el(doc, "p", "acp-nmr-note acp-nmr-note--notice", "");
      notice.hidden = true;
      notice.setAttribute("data-acp-nmr", "notice");
      summary.appendChild(notice);
      var warnings = el(doc, "ul", "acp-nmr-warnings");
      warnings.hidden = true;
      warnings.setAttribute("data-acp-nmr", "warnings");
      summary.appendChild(warnings);
      ready.appendChild(summary);

      var candidateList = el(doc, "nav", "acp-nmr-candidates");
      candidateList.setAttribute("role", "tablist");
      candidateList.setAttribute("data-acp-nmr", "candidates");
      ready.appendChild(candidateList);

      var detail = el(doc, "section", "acp-nmr-detail");
      var detailHead = el(doc, "div", "acp-nmr-detail-head");
      var detailTitle = el(doc, "h4", "acp-nmr-detail-title", "");
      var detailMeta = el(doc, "span", "acp-nmr-detail-meta", "");
      detailHead.appendChild(detailTitle);
      detailHead.appendChild(detailMeta);
      detail.appendChild(detailHead);

      var tabBar = el(doc, "div", "acp-nmr-tabs");
      tabBar.setAttribute("role", "tablist");
      var tabButtons = {};
      TABS.forEach(function (tab) {
        var button = i18nEl(doc, "button", "acp-nmr-tab", "next.nmr.tab." + tab);
        button.type = "button";
        button.setAttribute("role", "tab");
        button.setAttribute("data-tab", tab);
        button.setAttribute("aria-selected", "false");
        tabBar.appendChild(button);
        tabButtons[tab] = button;
      });
      detail.appendChild(tabBar);

      var scroll = el(doc, "div", "acp-nmr-scroll");
      detail.appendChild(scroll);

      var assignmentPanel = el(doc, "section", "acp-nmr-panel");
      assignmentPanel.setAttribute("data-panel", "assignment");
      var highlightBar = el(doc, "div", "acp-nmr-highlight-bar");
      var highlightSummary = el(doc, "p", "acp-nmr-highlight-summary", "");
      highlightSummary.setAttribute("data-acp-nmr", "highlight-summary");
      var clearHighlight = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-nmr-clear", "next.nmr.assignment.clear");
      clearHighlight.type = "button";
      clearHighlight.setAttribute("data-action", "clear-highlight");
      clearHighlight.hidden = true;
      highlightBar.appendChild(highlightSummary);
      highlightBar.appendChild(clearHighlight);
      assignmentPanel.appendChild(highlightBar);
      var assignmentBody = el(doc, "div", "acp-nmr-table-wrap");
      assignmentBody.setAttribute("data-acp-nmr", "assignment-body");
      assignmentPanel.appendChild(assignmentBody);
      scroll.appendChild(assignmentPanel);

      var peaksPanel = el(doc, "section", "acp-nmr-panel");
      peaksPanel.setAttribute("data-panel", "peaks");
      peaksPanel.hidden = true;
      var peaksHint = i18nEl(doc, "p", "acp-nmr-note", "next.nmr.peaks.hint");
      peaksPanel.appendChild(peaksHint);
      var peaksBody = el(doc, "div", "acp-nmr-table-wrap");
      peaksBody.setAttribute("data-acp-nmr", "peaks-body");
      peaksPanel.appendChild(peaksBody);
      scroll.appendChild(peaksPanel);

      var metricsPanel = el(doc, "section", "acp-nmr-panel");
      metricsPanel.setAttribute("data-panel", "metrics");
      metricsPanel.hidden = true;
      var metricsBody = el(doc, "div", "acp-nmr-metrics");
      metricsBody.setAttribute("data-acp-nmr", "metrics-body");
      metricsPanel.appendChild(metricsBody);
      scroll.appendChild(metricsPanel);

      var structurePanel = el(doc, "section", "acp-nmr-panel");
      structurePanel.setAttribute("data-panel", "structure");
      structurePanel.hidden = true;
      var structureTitle = i18nEl(doc, "h4", "acp-nmr-section-title", "next.nmr.structure.title");
      structurePanel.appendChild(structureTitle);
      var structureHint = i18nEl(doc, "p", "acp-nmr-note", "next.nmr.structure.hint");
      structurePanel.appendChild(structureHint);
      var structureBody = el(doc, "div", "acp-nmr-structure");
      structureBody.setAttribute("data-acp-nmr", "structure-body");
      structurePanel.appendChild(structureBody);
      scroll.appendChild(structurePanel);
      ready.appendChild(detail);

      var panels = {
        assignment: assignmentPanel,
        peaks: peaksPanel,
        metrics: metricsPanel,
        structure: structurePanel,
      };

      // Static event wiring (stable nodes survive every inner re-render).
      on(refresh, "click", function () { refreshReport(); });
      on(retry, "click", function () { refreshReport(); });
      on(candidateList, "click", function (event) {
        var node = event && event.target;
        while (node && node !== candidateList) {
          if (node.getAttribute) {
            var pos = node.getAttribute("data-candidate-pos");
            if (pos != null && pos !== "") { selectCandidate(Number(pos)); return; }
          }
          node = node.parentNode;
        }
      });
      on(tabBar, "click", function (event) {
        var node = event && event.target;
        while (node && node !== tabBar) {
          if (node.getAttribute) {
            var tab = node.getAttribute("data-tab");
            if (tab) { setTab(tab); return; }
          }
          node = node.parentNode;
        }
      });
      on(assignmentPanel, "click", function (event) {
        var row = nearestAttribute(event && event.target, assignmentPanel, "data-nmr-row-index");
        if (row) selectAssignmentRow(Number(row.getAttribute("data-nmr-row-index")));
      });
      on(assignmentPanel, "keydown", function (event) {
        if (!event || (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar")) return;
        var row = nearestAttribute(event.target, assignmentPanel, "data-nmr-row-index");
        if (row) {
          if (event.preventDefault) event.preventDefault();
          selectAssignmentRow(Number(row.getAttribute("data-nmr-row-index")));
        }
      });
      on(peaksPanel, "click", function (event) {
        var row = nearestAttribute(event && event.target, peaksPanel, "data-nmr-peak-index");
        if (row) selectPeakRow(Number(row.getAttribute("data-nmr-peak-index")));
      });
      on(peaksPanel, "keydown", function (event) {
        if (!event || (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar")) return;
        var row = nearestAttribute(event.target, peaksPanel, "data-nmr-peak-index");
        if (row) {
          if (event.preventDefault) event.preventDefault();
          selectPeakRow(Number(row.getAttribute("data-nmr-peak-index")));
        }
      });
      on(structureBody, "click", function (event) {
        var node = event && event.target;
        while (node && node !== structureBody) {
          if (node.getAttribute) {
            if (node.getAttribute("data-action") === "load-geometry") { ensureGeometry(true); return; }
            var label = node.getAttribute("data-nmr-atom-chip");
            if (label != null) { selectAtomLabel(label); return; }
          }
          node = node.parentNode;
        }
      });
      on(clearHighlight, "click", function () { clearSelection(); });

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        sub: sub,
        warnBadge: warnBadge,
        refresh: refresh,
        stateNode: stateNode,
        emptyHint: emptyHint,
        retry: retry,
        ready: ready,
        summaryList: summaryList,
        legacyNote: legacyNote,
        notice: notice,
        warnings: warnings,
        candidateList: candidateList,
        detailTitle: detailTitle,
        detailMeta: detailMeta,
        tabButtons: tabButtons,
        panels: panels,
        scroll: scroll,
        assignmentPanel: assignmentPanel,
        assignmentBody: assignmentBody,
        peaksPanel: peaksPanel,
        peaksBody: peaksBody,
        metricsBody: metricsBody,
        structureBody: structureBody,
        highlightSummary: highlightSummary,
        clearHighlight: clearHighlight,
        assignmentRows: [],
        peakRows: [],
        candidateButtons: [],
      };
    }

    function nearestAttribute(node, stop, attribute) {
      while (node && node !== stop) {
        if (node.getAttribute && node.getAttribute(attribute) != null) return node;
        node = node.parentNode;
      }
      return null;
    }

    // ── reset / states ──────────────────────────────────────────────────

    function reset() {
      var ui = state.ui;
      if (!ui || !ui.root) return;
      ui.ready.hidden = true;
      ui.summaryList.replaceChildren();
      ui.legacyNote.hidden = true;
      ui.legacyNote.textContent = "";
      ui.notice.hidden = true;
      ui.notice.textContent = "";
      ui.warnings.replaceChildren();
      ui.warnings.hidden = true;
      ui.warnBadge.hidden = true;
      ui.warnBadge.textContent = "";
      ui.candidateList.replaceChildren();
      ui.detailTitle.textContent = "";
      ui.detailMeta.textContent = "";
      ui.assignmentBody.replaceChildren();
      ui.peaksBody.replaceChildren();
      ui.metricsBody.replaceChildren();
      ui.structureBody.replaceChildren();
      ui.highlightSummary.textContent = "";
      ui.clearHighlight.hidden = true;
      ui.stateNode.hidden = true;
      ui.stateNode.textContent = "";
      ui.emptyHint.hidden = true;
      ui.emptyHint.textContent = "";
      ui.retry.hidden = true;
      ui.refresh.disabled = false;
      ui.assignmentRows = [];
      ui.peakRows = [];
      ui.candidateButtons = [];
      setRootAttr("data-acp-nmr-empty-reason", "");
      setRootAttr("data-acp-nmr-error-code", "");
    }

    function resetReport() {
      state.report = null;
      state.reportPath = "";
      state.normalized = null;
      state.reportWarnings = [];
      state.candidates = [];
      state.selectedPos = -1;
      state.selectedCandidateIndex = null;
      state.selection = null;
      state.geometry = {
        status: "idle",
        candidatePos: -1,
        path: "",
        atoms: [],
        elements: [],
        warnings: [],
        error: "",
      };
      state.geometryToken += 1;
    }

    function renderEmpty(reason, detail) {
      reset();
      state.loading = false;
      state.errorText = "";
      state.errorCode = "";
      state.emptyReason = reason || "no_report";
      state.emptyDetail = detail || "";
      state.selection = null;
      setState("empty");
      setRootAttr("data-acp-nmr-empty-reason", state.emptyReason);
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = emptyTitle(state.emptyReason);
      ui.emptyHint.hidden = false;
      ui.emptyHint.textContent = emptyHint(state.emptyReason, state.emptyDetail);
      applyI18n(ui.root);
    }

    function renderLoading() {
      reset();
      state.loading = true;
      setState("loading");
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = t("next.nmr.loading");
      ui.refresh.disabled = true;
      if (ui.scroll) ui.scroll.scrollTop = 0;
      applyI18n(ui.root);
    }

    function renderError(message, code) {
      reset();
      state.loading = false;
      state.errorText = String(message || t("next.nmr.read_failed", { message: "unknown" }));
      state.errorCode = code || "read_failed";
      state.selection = null;
      setState("error");
      setRootAttr("data-acp-nmr-error-code", state.errorCode);
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = state.errorText;
      ui.retry.hidden = false;
      if (ui.scroll) ui.scroll.scrollTop = 0;
      applyI18n(ui.root);
    }

    function emptyTitle(reason) {
      var key = {
        no_job: "next.nmr.no_job",
        not_nmr: "next.nmr.not_nmr",
        no_report: "next.nmr.no_report",
        no_candidates: "next.nmr.no_candidates",
        no_dp4_data: "next.nmr.no_dp4",
        no_spectrum_data: "next.nmr.no_spectrum",
      }[reason];
      if (!key) return t("next.nmr.empty");
      if (reason === "not_nmr") return t(key, { workflow: state.workflow || "—" });
      return t(key);
    }

    function emptyHint(reason, detail) {
      var key = {
        no_job: "next.nmr.no_job_hint",
        not_nmr: "next.nmr.not_nmr_hint",
        no_report: "next.nmr.no_report_hint",
        no_candidates: "next.nmr.no_candidates_hint",
        no_dp4_data: "next.nmr.no_dp4_hint",
        no_spectrum_data: "next.nmr.no_spectrum_hint",
      }[reason];
      if (!key) return "";
      if (reason === "no_dp4_data" && detail) return detail;
      return t(key);
    }

    // ── report normalization ────────────────────────────────────────────

    function normalizeReport(report) {
      var candidates = Array.isArray(report.candidates)
        ? report.candidates.filter(isPlainObject)
        : [];
      var summary = isPlainObject(report.summary) ? report.summary : {};
      var winner = isPlainObject(summary.winner) ? summary.winner : null;
      var nuclei = Array.isArray(summary.nuclei) ? summary.nuclei : [];
      var nCandidates = asFiniteNumber(summary.n_candidates);
      var warnings = [];
      if (report.schema_version == null) {
        warnings.push(warning("legacy_schema", "next.nmr.warn.legacy_schema"));
      } else if (asFiniteNumber(report.schema_version) !== 2) {
        warnings.push(warning("unknown_schema", "next.nmr.warn.unknown_schema", {
          version: rawText(report.schema_version),
        }));
      }
      if (!isPlainObject(report.summary)) {
        warnings.push(warning("missing_summary", "next.nmr.warn.missing_summary"));
      }
      if (!nuclei.length) {
        warnings.push(warning("missing_nuclei", "next.nmr.warn.missing_nuclei"));
      }
      if (report.processing_quality == null ||
          (Array.isArray(report.processing_quality) && !report.processing_quality.length)) {
        warnings.push(warning("missing_processing", "next.nmr.warn.missing_processing"));
      }
      if (report.diagnostics == null || !isPlainObject(report.diagnostics)) {
        warnings.push(warning("missing_diagnostics", "next.nmr.warn.missing_diagnostics"));
      }
      var hasDp = candidates.some(function (candidate) {
        return asFiniteNumber(candidate.dp4_probability) != null ||
          asFiniteNumber(candidate.dp5_probability) != null;
      }) || (winner && (asFiniteNumber(winner.dp4) != null || asFiniteNumber(winner.dp5) != null));
      if (hasDp && !winner) {
        warnings.push(warning("not_rankable", "next.nmr.warn.not_rankable"));
      }
      var assignmentCount = 0;
      candidates.forEach(function (candidate) {
        if (Array.isArray(candidate.assignment)) assignmentCount += candidate.assignment.length;
      });
      return {
        candidates: candidates,
        summary: summary,
        winner: winner,
        nuclei: nuclei,
        nCandidates: nCandidates == null ? candidates.length : nCandidates,
        hasDp: !!hasDp,
        assignmentCount: assignmentCount,
        hasProcessing: Array.isArray(report.processing_quality) && report.processing_quality.length > 0,
        warnings: warnings,
      };
    }

    function candidateLabel(candidate, pos) {
      var label = asString(candidate && candidate.label);
      if (label) return label;
      var index = candidate && candidate.index != null ? candidate.index : pos;
      return "candidate_" + String(index);
    }

    function collectCandidateReasons(candidates) {
      var reasons = [];
      candidates.forEach(function (candidate) {
        if (candidate.evidence && Array.isArray(candidate.evidence.exclusion_reasons)) {
          candidate.evidence.exclusion_reasons.forEach(function (reason) {
            var text = asString(reason);
            if (text && reasons.indexOf(text) === -1) reasons.push(text);
          });
        }
        var p4 = candidate.probability && candidate.probability.dp4;
        if (p4 && Array.isArray(p4.reasons)) {
          p4.reasons.forEach(function (reason) {
            var text = asString(reason);
            if (text && reasons.indexOf(text) === -1) reasons.push(text);
          });
        }
      });
      if (!reasons.length) {
        candidates.forEach(function (candidate) {
          var status = asString(candidate.analysis_status);
          if (status && reasons.indexOf(status) === -1) reasons.push(status);
        });
      }
      return reasons.length ? reasons.join(", ") : "—";
    }

    function candidateWarnings(candidate) {
      if (!candidate) return [];
      var warnings = [];
      var p4 = candidate.probability && candidate.probability.dp4;
      var p5 = candidate.probability && candidate.probability.dp5;
      var p4Status = p4 && p4.status ? String(p4.status) : (candidate.analysis_status || "unavailable");
      var p5Status = p5 && p5.status ? String(p5.status) : (candidate.dp5_probability == null ? "unavailable" : "valid");
      if (asFiniteNumber(candidate.dp4_probability) == null) {
        var p4Reasons = p4 && Array.isArray(p4.reasons) && p4.reasons.length
          ? "：" + p4.reasons.join(", ") : "";
        warnings.push(warning("dp4_unavailable", "next.nmr.warn.dp4_unavailable", {
          status: statusLabel(p4Status),
          reasons: p4Reasons,
        }));
      }
      if (asFiniteNumber(candidate.dp5_probability) == null && p5Status !== "not_applicable") {
        var p5Reasons = p5 && Array.isArray(p5.reasons) && p5.reasons.length
          ? "：" + p5.reasons.join(", ") : "";
        warnings.push(warning("dp5_unavailable", "next.nmr.warn.dp5_unavailable", {
          status: statusLabel(p5Status),
          reasons: p5Reasons,
        }));
      }
      if (p5 && p5.status === "placeholder") {
        warnings.push(warning("placeholder_dp5", "next.nmr.warn.placeholder_dp5"));
      }
      var analysisStatus = asString(candidate.analysis_status);
      if (analysisStatus === "invalid" || analysisStatus === "evidence_insufficient") {
        warnings.push(warning("analysis_status", "next.nmr.warn.analysis_status", {
          status: statusLabel(analysisStatus),
        }));
      }
      if (!candidate.probability) {
        warnings.push(warning("missing_probability", "next.nmr.warn.missing_probability"));
      }
      if (!Array.isArray(candidate.assignment) || !candidate.assignment.length) {
        warnings.push(warning("missing_assignments", "next.nmr.warn.missing_assignments"));
      }
      if (!candidate.regression || !Object.keys(candidate.regression).length) {
        warnings.push(warning("missing_regression", "next.nmr.warn.missing_regression"));
      }
      var coverage = candidate.coverage;
      if (!isPlainObject(coverage) || coverage.matched_signals == null) {
        warnings.push(warning("missing_coverage", "next.nmr.warn.missing_coverage"));
      }
      var seen = {};
      return warnings.filter(function (item) {
        if (seen[item.code]) return false;
        seen[item.code] = true;
        return true;
      });
    }

    function currentWarnings() {
      var list = state.reportWarnings.slice();
      var candidate = currentCandidate();
      if (candidate) list = list.concat(candidateWarnings(candidate));
      var seen = {};
      return list.filter(function (item) {
        if (seen[item.code]) return false;
        seen[item.code] = true;
        return true;
      });
    }

    // ── rendering: ready ────────────────────────────────────────────────

    function renderReady() {
      if (state.suspended) return;
      var previousScroll = state.ui.scroll ? state.ui.scroll.scrollTop : 0;
      reset();
      state.loading = false;
      state.errorText = "";
      state.errorCode = "";
      setState("ready");
      var ui = state.ui;
      ui.ready.hidden = false;
      renderSummary();
      renderCandidateList();
      renderWarnings();
      applyTab();
      renderDetail();
      if (ui.scroll) ui.scroll.scrollTop = previousScroll;
      ui.refresh.disabled = false;
      applyI18n(ui.root);
    }

    function renderSummary() {
      var ui = state.ui;
      var normalized = state.normalized;
      var report = state.report;
      if (!normalized || !report) return;
      var winner = normalized.winner;
      var jobText = state.jobId + (state.jobName ? " · " + state.jobName : "");
      metaRow(ui.summaryList, "job", "next.nmr.label.job", jobText, { mono: true });
      metaRow(ui.summaryList, "report", "next.nmr.label.report", state.reportPath || "—", {
        mono: true,
        title: true,
      });
      metaRow(ui.summaryList, "candidates", "next.nmr.label.candidates",
        String(normalized.nCandidates), { value: normalized.nCandidates });
      metaRow(ui.summaryList, "winner", "next.nmr.label.winner",
        winner ? (asString(winner.label) || "candidate_" + winner.index) : t("next.nmr.winner_none"));
      if (winner) {
        metaRow(ui.summaryList, "winner_dp4", "next.nmr.label.winner_dp4",
          fmtProbability(winner.dp4), { value: winner.dp4 });
        metaRow(ui.summaryList, "winner_dp5", "next.nmr.label.winner_dp5",
          fmtProbability(winner.dp5), { value: winner.dp5 });
      }
      metaRow(ui.summaryList, "nuclei", "next.nmr.label.nuclei", listText(normalized.nuclei));
      metaRow(ui.summaryList, "ranking", "next.nmr.label.ranking",
        rankingLabel(normalized.summary.dp4_ranking),
        { value: normalized.summary.dp4_ranking });
      metaRow(ui.summaryList, "error_model", "next.nmr.label.error_model",
        asString(report.error_model) || "—");
      metaRow(ui.summaryList, "dp5_mode", "next.nmr.label.dp5_mode",
        asString(report.dp5_mode) || "—");
      metaRow(ui.summaryList, "protocol", "next.nmr.label.protocol",
        asString(report.protocol_id) || "—", { mono: true, title: true });

      var schemaVersion = report.schema_version;
      var legacy = schemaVersion == null
        ? t("next.nmr.legacy_note")
        : (asFiniteNumber(schemaVersion) !== 2
          ? t("next.nmr.unknown_schema", { version: rawText(schemaVersion) })
          : "");
      ui.legacyNote.hidden = !legacy;
      ui.legacyNote.textContent = legacy;

      var note = asString(report.note);
      ui.notice.hidden = !note;
      ui.notice.textContent = note;
    }

    function renderCandidateList() {
      var ui = state.ui;
      ui.candidateList.replaceChildren();
      ui.candidateButtons = [];
      var winner = state.normalized ? state.normalized.winner : null;
      var winnerIndex = winner && winner.index != null ? String(winner.index) : null;
      state.candidates.forEach(function (candidate, pos) {
        var isWinner = winnerIndex != null && String(candidate.index) === winnerIndex;
        var button = el(doc, "button", "acp-nmr-candidate", "");
        button.type = "button";
        button.setAttribute("role", "tab");
        button.setAttribute("data-candidate-pos", String(pos));
        button.setAttribute("data-candidate-index", rawText(candidate.index));
        button.setAttribute("data-winner", isWinner ? "true" : "false");
        button.setAttribute("data-dp4", rawText(candidate.dp4_probability));
        button.setAttribute("data-dp5", rawText(candidate.dp5_probability));
        button.setAttribute("aria-selected", "false");

        var label = el(doc, "span", "acp-nmr-candidate-label",
          candidateLabel(candidate, pos));
        button.appendChild(label);

        var dp4Row = el(doc, "span", "acp-nmr-candidate-metric", "");
        var dp4Label = i18nEl(doc, "span", "acp-nmr-candidate-metric-label", "next.nmr.label.dp4");
        var dp4Value = el(doc, "span", "acp-nmr-candidate-metric-value",
          fmtProbability(candidate.dp4_probability));
        dp4Row.appendChild(dp4Label);
        dp4Row.appendChild(dp4Value);
        var dp4Bar = el(doc, "span", "acp-nmr-bar", "");
        var dp4Fill = el(doc, "span", "acp-nmr-bar-fill", "");
        dp4Fill.style.width = barWidth(candidate.dp4_probability) + "%";
        if (asFiniteNumber(candidate.dp4_probability) == null) dp4Fill.setAttribute("data-empty", "true");
        dp4Bar.appendChild(dp4Fill);
        dp4Row.appendChild(dp4Bar);
        button.appendChild(dp4Row);

        var dp5Row = el(doc, "span", "acp-nmr-candidate-metric", "");
        var dp5Label = i18nEl(doc, "span", "acp-nmr-candidate-metric-label", "next.nmr.label.dp5");
        var dp5Value = el(doc, "span", "acp-nmr-candidate-metric-value",
          fmtProbability(candidate.dp5_probability));
        dp5Row.appendChild(dp5Label);
        dp5Row.appendChild(dp5Value);
        var dp5Bar = el(doc, "span", "acp-nmr-bar acp-nmr-bar--dp5", "");
        var dp5Fill = el(doc, "span", "acp-nmr-bar-fill", "");
        dp5Fill.style.width = barWidth(candidate.dp5_probability) + "%";
        if (asFiniteNumber(candidate.dp5_probability) == null) dp5Fill.setAttribute("data-empty", "true");
        dp5Bar.appendChild(dp5Fill);
        dp5Row.appendChild(dp5Bar);
        button.appendChild(dp5Row);

        var status = analysisStatusOf(candidate);
        var statusNode = el(doc, "span", "acp-nmr-candidate-status", statusLabel(status));
        statusNode.setAttribute("data-status", status);
        button.appendChild(statusNode);

        if (isWinner) {
          var winnerTag = i18nEl(doc, "span", "acp-nmr-winner-tag", "next.nmr.winner_tag");
          button.appendChild(winnerTag);
        }

        ui.candidateList.appendChild(button);
        ui.candidateButtons.push(button);
      });
      applyCandidateSelection();
    }

    function analysisStatusOf(candidate) {
      var status = asString(candidate.analysis_status);
      if (status) return status;
      if (candidate.probability && candidate.probability.dp4 && candidate.probability.dp4.status) {
        return String(candidate.probability.dp4.status);
      }
      return "unknown";
    }

    function barWidth(value) {
      var n = asFiniteNumber(value);
      if (n == null) return 0;
      var clamped = Math.max(0, Math.min(1, n));
      return Math.round(clamped * 1000) / 10;
    }

    function applyCandidateSelection() {
      var ui = state.ui;
      if (!ui.candidateButtons) return;
      ui.candidateButtons.forEach(function (button) {
        var pos = Number(button.getAttribute("data-candidate-pos"));
        var selected = pos === state.selectedPos;
        button.setAttribute("aria-selected", selected ? "true" : "false");
        button.setAttribute("data-selected", selected ? "true" : "false");
      });
    }

    function renderWarnings() {
      var ui = state.ui;
      var list = currentWarnings();
      ui.warnings.replaceChildren();
      ui.warnings.hidden = !list.length;
      ui.warnBadge.hidden = !list.length;
      ui.warnBadge.textContent = list.length
        ? t("next.nmr.badge.warnings", { count: list.length }) : "";
      if (!list.length) return;
      var capped = list.slice(0, MAX_WARNINGS);
      capped.forEach(function (item) {
        var li = el(doc, "li", "acp-nmr-warning", t(item.key, item.vars));
        li.setAttribute("data-acp-warning-code", item.code);
        ui.warnings.appendChild(li);
      });
      if (list.length > capped.length) {
        var more = el(doc, "li", "acp-nmr-warning acp-nmr-warning--more",
          "… +" + String(list.length - capped.length));
        ui.warnings.appendChild(more);
      }
    }

    // ── rendering: detail ───────────────────────────────────────────────

    function renderDetail() {
      var ui = state.ui;
      var candidate = currentCandidate();
      if (!candidate) return;
      var previousScroll = ui.scroll ? ui.scroll.scrollTop : 0;
      ui.assignmentBody.replaceChildren();
      ui.peaksBody.replaceChildren();
      ui.metricsBody.replaceChildren();
      ui.structureBody.replaceChildren();
      ui.assignmentRows = [];
      ui.peakRows = [];
      ui.detailTitle.textContent = candidateLabel(candidate, state.selectedPos);
      ui.detailMeta.textContent = [
        "index " + displayValue(candidate.index),
        t("next.nmr.label.conformers") + " " + displayValue(candidate.n_conformers),
        t("next.nmr.label.assignment_count") + " " +
          String(Array.isArray(candidate.assignment) ? candidate.assignment.length : 0),
      ].join(" · ");
      renderAssignment(candidate);
      renderPeaks();
      renderMetrics(candidate);
      renderStructure(candidate);
      if (state.selection && state.selection.pos !== state.selectedPos) {
        state.selection = null;
      }
      applyTab();
      applySelection();
      if (ui.scroll) ui.scroll.scrollTop = previousScroll;
      if (state.activeTab === "structure") ensureGeometry(false);
    }

    function currentCandidate() {
      if (state.selectedPos < 0 || state.selectedPos >= state.candidates.length) return null;
      return state.candidates[state.selectedPos];
    }

    function cell(docRef, text, cls, value) {
      var td = el(docRef, "td", cls || "", text);
      if (value !== undefined) td.setAttribute("data-value", rawText(value));
      return td;
    }

    function tableHeader(docRef, columns) {
      var thead = el(docRef, "thead", "", "");
      var tr = el(docRef, "tr", "", "");
      columns.forEach(function (item) {
        var th = i18nEl(docRef, "th", "", item.key);
        th.setAttribute("scope", "col");
        if (item.cls) th.className = item.cls;
        tr.appendChild(th);
      });
      thead.appendChild(tr);
      return thead;
    }

    function renderAssignment(candidate) {
      var ui = state.ui;
      ui.assignmentBody.replaceChildren();
      var rows = Array.isArray(candidate.assignment) ? candidate.assignment : [];
      if (!rows.length) {
        ui.assignmentBody.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.assignment.empty")));
        return;
      }
      var table = el(doc, "table", "acp-nmr-table");
      table.appendChild(tableHeader(doc, [
        { key: "next.nmr.label.atom" },
        { key: "next.nmr.label.element" },
        { key: "next.nmr.label.exp_ppm", cls: "acp-nmr-num" },
        { key: "next.nmr.label.calc_ppm", cls: "acp-nmr-num" },
        { key: "next.nmr.label.scaled_ppm", cls: "acp-nmr-num" },
        { key: "next.nmr.label.residual", cls: "acp-nmr-num" },
        { key: "next.nmr.label.observation" },
        { key: "next.nmr.label.claim_atoms" },
      ]));
      var tbody = el(doc, "tbody", "", "");
      rows.forEach(function (assignment) {
        if (!isPlainObject(assignment)) return;
        var rowIndex = ui.assignmentRows.length;
        var atom = assignment.atom == null ? "" : String(assignment.atom);
        var observation = assignment.observation_id == null ? "" : String(assignment.observation_id);
        var groupAtoms = [];
        if (isPlainObject(assignment.signal_group) && Array.isArray(assignment.signal_group.atom_uids)) {
          assignment.signal_group.atom_uids.forEach(function (label) {
            var text = asString(label);
            if (text && groupAtoms.indexOf(text) === -1) groupAtoms.push(text);
          });
        }
        var atoms = groupAtoms.slice();
        if (atom && atoms.indexOf(atom) === -1) atoms.push(atom);
        var tr = el(doc, "tr", "acp-nmr-row", "");
        tr.setAttribute("data-nmr-assignment-row", String(rowIndex));
        tr.setAttribute("data-nmr-row-index", String(rowIndex));
        tr.setAttribute("data-nmr-candidate", rawText(candidate.index));
        tr.setAttribute("data-nmr-atom", atom);
        tr.setAttribute("data-nmr-observation", observation);
        tr.setAttribute("data-nmr-claim-atoms", atoms.join(","));
        tr.setAttribute("tabindex", "0");
        tr.appendChild(cell(doc, atom || "—", "acp-nmr-atom", atom || null));
        tr.appendChild(cell(doc, assignment.element == null ? "—" : String(assignment.element),
          "", assignment.element));
        tr.appendChild(cell(doc, fmtNumber(assignment.exp_ppm, 4), "acp-nmr-num", assignment.exp_ppm));
        tr.appendChild(cell(doc, fmtNumber(assignment.calc_ppm, 4), "acp-nmr-num", assignment.calc_ppm));
        tr.appendChild(cell(doc, fmtNumber(assignment.scaled_ppm, 4), "acp-nmr-num", assignment.scaled_ppm));
        var residualValue = assignment.residual;
        var residualNumber = asFiniteNumber(residualValue);
        var residualClass = "acp-nmr-num acp-nmr-residual";
        if (residualNumber != null) residualClass += residualNumber < 0 ? " acp-nmr-residual--neg" : " acp-nmr-residual--pos";
        tr.appendChild(cell(doc, fmtNumber(residualValue, 4), residualClass, residualValue));
        tr.appendChild(cell(doc, observation || "—", "acp-nmr-mono", observation || null));
        tr.appendChild(cell(doc, atoms.length ? atoms.join(", ") : "—",
          "acp-nmr-claim-atoms", atoms.join(",")));
        tbody.appendChild(tr);
        ui.assignmentRows.push({
          node: tr,
          rowIndex: rowIndex,
          atom: atom,
          observationId: observation,
          atoms: atoms,
        });
      });
      table.appendChild(tbody);
      ui.assignmentBody.appendChild(table);
    }

    function renderPeaks() {
      var ui = state.ui;
      var rows = buildPeakRows(state.report, state.candidates);
      if (!rows.length) {
        ui.peaksBody.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.peaks.empty")));
        return;
      }
      var conflicts = rows.filter(function (row) { return row.conflict; }).length;
      var flips = peakFlipCount(state.report);
      var summary = el(doc, "p", "acp-nmr-note", t("next.nmr.peaks.summary", {
        n: rows.length,
        conflicts: conflicts,
        flips: flips,
      }));
      summary.setAttribute("data-acp-nmr", "peaks-summary");
      ui.peaksBody.appendChild(summary);

      var table = el(doc, "table", "acp-nmr-table");
      table.appendChild(tableHeader(doc, [
        { key: "next.nmr.label.observation" },
        { key: "next.nmr.label.nucleus" },
        { key: "next.nmr.label.exp_ppm", cls: "acp-nmr-num" },
        { key: "next.nmr.label.n_claims", cls: "acp-nmr-num" },
        { key: "next.nmr.label.claim_atoms" },
        { key: "next.nmr.label.candidate_indices" },
        { key: "next.nmr.label.conflict" },
      ]));
      var tbody = el(doc, "tbody", "", "");
      rows.forEach(function (row, rowIndex) {
        var tr = el(doc, "tr", "acp-nmr-row acp-nmr-peak-row", "");
        tr.setAttribute("data-nmr-peak-row", String(rowIndex));
        tr.setAttribute("data-nmr-peak-index", String(rowIndex));
        tr.setAttribute("data-nmr-observation", row.observationId);
        tr.setAttribute("data-nmr-claim-atoms", row.atomLabels.join(","));
        tr.setAttribute("tabindex", "0");
        tr.appendChild(cell(doc, row.observationId || "—", "acp-nmr-mono", row.observationId));
        tr.appendChild(cell(doc, row.nucleus || "—", "", row.nucleus));
        tr.appendChild(cell(doc, fmtNumber(row.expPpm, 4), "acp-nmr-num", row.expPpm));
        tr.appendChild(cell(doc, row.nClaims == null ? "—" : String(row.nClaims), "acp-nmr-num", row.nClaims));
        tr.appendChild(cell(doc, row.atomLabels.length ? row.atomLabels.join(", ") : "—",
          "acp-nmr-claim-atoms", row.atomLabels.join(",")));
        tr.appendChild(cell(doc, row.candidateIndices.length ? row.candidateIndices.join(", ") : "—",
          "acp-nmr-mono", row.candidateIndices.join(",")));
        var conflictCell = cell(doc, row.conflict ? t("next.nmr.label.conflict") : "—", "", row.conflict ? "true" : "false");
        if (row.conflict) conflictCell.className = "acp-nmr-conflict";
        tr.appendChild(conflictCell);
        tbody.appendChild(tr);
        ui.peakRows.push({
          node: tr,
          rowIndex: rowIndex,
          observationId: row.observationId,
          atomLabels: row.atomLabels,
          candidateIndices: row.candidateIndices,
        });
      });
      table.appendChild(tbody);
      ui.peaksBody.appendChild(table);
    }

    function peakFlipCount(report) {
      var diag = report && report.diagnostics;
      if (!isPlainObject(diag)) return "—";
      var loo = diag.leave_one_signal_out;
      if (isPlainObject(loo) && asFiniteNumber(loo.n_winner_flips) != null) {
        return String(asFiniteNumber(loo.n_winner_flips));
      }
      return "—";
    }

    function buildPeakRows(report, candidates) {
      var claims = report && report.diagnostics && report.diagnostics.conflict_matrix &&
        Array.isArray(report.diagnostics.conflict_matrix.claims)
        ? report.diagnostics.conflict_matrix.claims : [];
      var rows = [];
      claims.forEach(function (claim) {
        if (!isPlainObject(claim) || claim.observation_id == null) return;
        rows.push({
          observationId: String(claim.observation_id),
          nucleus: asString(claim.nucleus),
          expPpm: claim.exp_ppm,
          atomLabels: Array.isArray(claim.atom_labels)
            ? claim.atom_labels.map(function (item) { return String(item); }) : [],
          candidateIndices: Array.isArray(claim.candidate_indices)
            ? claim.candidate_indices.map(function (item) { return item; }) : [],
          nClaims: asFiniteNumber(claim.n_claims),
          conflict: claim.conflict === true,
        });
      });
      if (rows.length) return rows;

      // Fallback display aggregation (no score computation): group the
      // server-provided assignment rows by their observation_id.
      var byId = {};
      var order = [];
      candidates.forEach(function (candidate) {
        (Array.isArray(candidate.assignment) ? candidate.assignment : []).forEach(function (assignment) {
          if (!isPlainObject(assignment) || assignment.observation_id == null) return;
          var id = String(assignment.observation_id);
          if (!byId[id]) {
            byId[id] = {
              observationId: id,
              nucleus: asString(assignment.element),
              expPpm: assignment.exp_ppm,
              atomLabels: [],
              candidateIndices: [],
              nClaims: 0,
              conflict: false,
            };
            order.push(id);
          }
          var row = byId[id];
          if (assignment.atom != null && row.atomLabels.indexOf(String(assignment.atom)) === -1) {
            row.atomLabels.push(String(assignment.atom));
          }
          if (candidate.index != null && row.candidateIndices.indexOf(candidate.index) === -1) {
            row.candidateIndices.push(candidate.index);
          }
        });
      });
      order.forEach(function (id) {
        var row = byId[id];
        row.nClaims = row.atomLabels.length;
        row.conflict = row.candidateIndices.length > 1;
        rows.push(row);
      });
      return rows;
    }

    // ── rendering: metrics ──────────────────────────────────────────────

    function metricSection(parent, titleKey, rows) {
      var section = el(doc, "section", "acp-nmr-metric-section");
      section.appendChild(i18nEl(doc, "h4", "acp-nmr-section-title", titleKey));
      if (!rows || !rows.length) {
        section.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.metrics.no_data")));
        parent.appendChild(section);
        return section;
      }
      var dl = el(doc, "dl", "acp-nmr-meta");
      rows.forEach(function (row) {
        if (!row) return;
        metaRow(dl, row.field, row.label, row.text, {
          mono: !!row.mono,
          value: row.value,
          title: !!row.title,
        });
      });
      section.appendChild(dl);
      parent.appendChild(section);
      return section;
    }

    function probabilityRows(candidate) {
      var rows = [];
      var p4 = candidate.probability && candidate.probability.dp4;
      var p5 = candidate.probability && candidate.probability.dp5;
      rows.push({
        field: "dp4_probability",
        label: "next.nmr.label.dp4",
        text: fmtProbability(candidate.dp4_probability),
        value: candidate.dp4_probability,
      });
      rows.push({
        field: "dp5_probability",
        label: "next.nmr.label.dp5",
        text: fmtProbability(candidate.dp5_probability),
        value: candidate.dp5_probability,
      });
      if (p4) {
        rows.push({ field: "dp4.status", label: "next.nmr.label.status", text: statusLabel(p4.status), value: p4.status });
        rows.push({ field: "dp4.probability", label: "next.nmr.label.probability", text: fmtProbability(p4.probability), value: p4.probability });
        rows.push({ field: "dp4.calibration_status", label: "next.nmr.label.calibration", text: asString(p4.calibration_status) || "—", value: p4.calibration_status });
        rows.push({ field: "dp4.mode", label: "next.nmr.label.mode", text: asString(p4.mode) || "—", value: p4.mode });
        rows.push({ field: "dp4.model_id", label: "next.nmr.label.model", text: asString(p4.model_id) || "—", value: p4.model_id, mono: true });
        rows.push({ field: "dp4.reasons", label: "next.nmr.label.reasons", text: listText(p4.reasons), value: listText(p4.reasons) });
      }
      if (p5) {
        rows.push({ field: "dp5.status", label: "next.nmr.label.status", text: statusLabel(p5.status), value: p5.status });
        rows.push({ field: "dp5.probability", label: "next.nmr.label.probability", text: fmtProbability(p5.probability), value: p5.probability });
        rows.push({ field: "dp5.calibration_status", label: "next.nmr.label.calibration", text: asString(p5.calibration_status) || "—", value: p5.calibration_status });
        rows.push({ field: "dp5.mode", label: "next.nmr.label.mode", text: asString(p5.mode) || "—", value: p5.mode });
        rows.push({ field: "dp5.model_id", label: "next.nmr.label.model", text: asString(p5.model_id) || "—", value: p5.model_id, mono: true });
        rows.push({ field: "dp5.reasons", label: "next.nmr.label.reasons", text: listText(p5.reasons), value: listText(p5.reasons) });
      }
      return rows;
    }

    function coverageRows(candidate) {
      var coverage = isPlainObject(candidate.coverage) ? candidate.coverage : {};
      return [
        { field: "coverage.expected_signals", label: "next.nmr.label.expected", text: displayValue(coverage.expected_signals), value: coverage.expected_signals },
        { field: "coverage.matched_signals", label: "next.nmr.label.matched", text: displayValue(coverage.matched_signals), value: coverage.matched_signals },
        { field: "coverage.n_conformers_selected", label: "next.nmr.label.selected", text: displayValue(coverage.n_conformers_selected), value: coverage.n_conformers_selected },
        { field: "coverage.n_conformers_successful", label: "next.nmr.label.successful", text: displayValue(coverage.n_conformers_successful), value: coverage.n_conformers_successful },
        { field: "coverage.selected_population", label: "next.nmr.label.population", text: fmtNumber(coverage.selected_population, 6), value: coverage.selected_population },
        { field: "coverage.successful_population", label: "next.nmr.label.population", text: fmtNumber(coverage.successful_population, 6), value: coverage.successful_population },
      ];
    }

    function evidenceRows(candidate) {
      var evidence = isPlainObject(candidate.evidence) ? candidate.evidence : {};
      var observationCount = Array.isArray(evidence.observation_ids) ? evidence.observation_ids.length : null;
      return [
        { field: "evidence.status", label: "next.nmr.label.status", text: statusLabel(evidence.status), value: evidence.status },
        { field: "evidence.total_matched", label: "next.nmr.label.matched", text: displayValue(evidence.total_matched), value: evidence.total_matched },
        { field: "evidence.exclusion_reasons", label: "next.nmr.label.reasons", text: listText(evidence.exclusion_reasons), value: listText(evidence.exclusion_reasons) },
        { field: "evidence.observation_ids", label: "next.nmr.label.observation", text: displayValue(observationCount), value: observationCount },
      ];
    }

    function ensembleRows(candidate) {
      var quality = isPlainObject(candidate.ensemble_quality) ? candidate.ensemble_quality : null;
      if (!quality) return [];
      return [
        { field: "ensemble_quality.quality_status", label: "next.nmr.label.status", text: statusLabel(quality.quality_status), value: quality.quality_status },
        { field: "ensemble_quality.n_discovered", label: "next.nmr.label.conformers", text: displayValue(quality.n_discovered), value: quality.n_discovered },
        { field: "ensemble_quality.n_selected", label: "next.nmr.label.selected", text: displayValue(quality.n_selected), value: quality.n_selected },
        { field: "ensemble_quality.n_successful", label: "next.nmr.label.successful", text: displayValue(quality.n_successful), value: quality.n_successful },
        { field: "ensemble_quality.selected_population", label: "next.nmr.label.population", text: fmtNumber(quality.selected_population, 6), value: quality.selected_population },
        { field: "ensemble_quality.successful_population", label: "next.nmr.label.population", text: fmtNumber(quality.successful_population, 6), value: quality.successful_population },
        { field: "ensemble_quality.quality_flags", label: "next.nmr.label.flags", text: listText(quality.quality_flags), value: listText(quality.quality_flags) },
      ];
    }

    function diagnosticsRows(candidate) {
      var diag = state.report && isPlainObject(state.report.diagnostics) ? state.report.diagnostics : null;
      var candDiag = isPlainObject(candidate.diagnostics) ? candidate.diagnostics : null;
      var rows = [];
      if (diag && isPlainObject(diag.conflict_matrix)) {
        var claimCount = Array.isArray(diag.conflict_matrix.claims) ? diag.conflict_matrix.claims.length : null;
        var conflictCount = Array.isArray(diag.conflict_matrix.conflicting_observation_ids)
          ? diag.conflict_matrix.conflicting_observation_ids.length : null;
        rows.push({ field: "diagnostics.claims", label: "next.nmr.label.n_claims", text: displayValue(claimCount), value: claimCount });
        rows.push({ field: "diagnostics.conflicts", label: "next.nmr.label.conflict", text: displayValue(conflictCount), value: conflictCount });
      }
      if (diag && isPlainObject(diag.leave_one_signal_out)) {
        rows.push({ field: "diagnostics.loo_cases", label: "next.nmr.label.diagnostics", text: "LOO " + displayValue(diag.leave_one_signal_out.n_cases), value: diag.leave_one_signal_out.n_cases });
        rows.push({ field: "diagnostics.loo_flips", label: "next.nmr.label.conflict", text: "flips " + displayValue(diag.leave_one_signal_out.n_winner_flips), value: diag.leave_one_signal_out.n_winner_flips });
      }
      if (candDiag) {
        var signalCount = Array.isArray(candDiag.signals) ? candDiag.signals.length : null;
        var oodCount = Array.isArray(candDiag.atom_support)
          ? candDiag.atom_support.filter(function (record) { return record && record.out_of_domain === true; }).length
          : null;
        rows.push({ field: "diagnostics.signals", label: "next.nmr.label.diagnostics", text: "signals " + displayValue(signalCount), value: signalCount });
        rows.push({ field: "diagnostics.ood_atoms", label: "next.nmr.label.diagnostics", text: "OOD " + displayValue(oodCount), value: oodCount });
      }
      return rows;
    }

    function renderMetrics(candidate) {
      var body = state.ui.metricsBody;
      var rows = probabilityRows(candidate);
      if (!candidate.probability) {
        var section = metricSection(body, "next.nmr.metrics.probability", rows);
        section.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.metrics.no_probability")));
      } else {
        metricSection(body, "next.nmr.metrics.probability", rows);
      }
      metricSection(body, "next.nmr.metrics.coverage", coverageRows(candidate));
      metricSection(body, "next.nmr.metrics.evidence", evidenceRows(candidate));
      renderRegression(body, candidate);
      metricSection(body, "next.nmr.metrics.ensemble", ensembleRows(candidate));
      renderProcessing(body);
      metricSection(body, "next.nmr.metrics.diagnostics", diagnosticsRows(candidate));
    }

    function renderRegression(parent, candidate) {
      var section = el(doc, "section", "acp-nmr-metric-section");
      section.appendChild(i18nEl(doc, "h4", "acp-nmr-section-title", "next.nmr.metrics.regression"));
      var regression = isPlainObject(candidate.regression) ? candidate.regression : {};
      var nuclei = Object.keys(regression);
      if (!nuclei.length) {
        section.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.metrics.no_regression")));
        parent.appendChild(section);
        return;
      }
      var table = el(doc, "table", "acp-nmr-table acp-nmr-table--compact");
      table.appendChild(tableHeader(doc, [
        { key: "next.nmr.label.nucleus" },
        { key: "next.nmr.label.slope", cls: "acp-nmr-num" },
        { key: "next.nmr.label.intercept", cls: "acp-nmr-num" },
        { key: "next.nmr.label.r2_regression", cls: "acp-nmr-num" },
        { key: "next.nmr.label.r2_prediction", cls: "acp-nmr-num" },
        { key: "next.nmr.label.mae", cls: "acp-nmr-num" },
      ]));
      var tbody = el(doc, "tbody", "", "");
      nuclei.forEach(function (nucleus) {
        var record = isPlainObject(regression[nucleus]) ? regression[nucleus] : {};
        var r2Regression = record.r2_regression != null ? record.r2_regression : record.r_squared;
        var tr = el(doc, "tr", "acp-nmr-row", "");
        tr.appendChild(cell(doc, nucleus, "acp-nmr-mono", nucleus));
        tr.appendChild(cell(doc, fmtNumber(record.slope, 6), "acp-nmr-num", record.slope));
        tr.appendChild(cell(doc, fmtNumber(record.intercept, 6), "acp-nmr-num", record.intercept));
        tr.appendChild(cell(doc, fmtNumber(r2Regression, 6), "acp-nmr-num", r2Regression));
        tr.appendChild(cell(doc, fmtNumber(record.r2_prediction, 6), "acp-nmr-num", record.r2_prediction));
        tr.appendChild(cell(doc, fmtNumber(record.mae, 6), "acp-nmr-num", record.mae));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      section.appendChild(table);
      parent.appendChild(section);
    }

    function renderProcessing(parent) {
      var section = el(doc, "section", "acp-nmr-metric-section");
      section.appendChild(i18nEl(doc, "h4", "acp-nmr-section-title", "next.nmr.metrics.processing"));
      var records = state.report && Array.isArray(state.report.processing_quality)
        ? state.report.processing_quality : [];
      if (!records.length) {
        section.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.metrics.no_processing")));
        parent.appendChild(section);
        return;
      }
      var table = el(doc, "table", "acp-nmr-table acp-nmr-table--compact");
      table.appendChild(tableHeader(doc, [
        { key: "next.nmr.label.nucleus" },
        { key: "next.nmr.label.element" },
        { key: "next.nmr.label.status" },
        { key: "next.nmr.label.formal_usable" },
        { key: "next.nmr.label.reference_method" },
        { key: "next.nmr.label.reference_ppm", cls: "acp-nmr-num" },
      ]));
      var tbody = el(doc, "tbody", "", "");
      records.forEach(function (record) {
        if (!isPlainObject(record)) return;
        var tr = el(doc, "tr", "acp-nmr-row", "");
        tr.appendChild(cell(doc, asString(record.nucleus) || "—", "acp-nmr-mono", record.nucleus));
        tr.appendChild(cell(doc, asString(record.element) || "—", "", record.element));
        tr.appendChild(cell(doc, statusLabel(record.status), "", record.status));
        tr.appendChild(cell(doc, record.formal_usable === true ? "true" : "false", "", record.formal_usable));
        tr.appendChild(cell(doc, asString(record.reference_method) || "—", "", record.reference_method));
        tr.appendChild(cell(doc, fmtNumber(record.reference_ppm, 4), "acp-nmr-num", record.reference_ppm));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      section.appendChild(table);
      parent.appendChild(section);
    }

    // ── rendering: structure ────────────────────────────────────────────

    function renderStructure(candidate) {
      var ui = state.ui;
      var body = ui.structureBody;
      body.replaceChildren();
      var geometry = state.geometry;
      var current = geometry.candidatePos === state.selectedPos;
      if (!current) geometry = { status: "idle", candidatePos: state.selectedPos, atoms: [], elements: [], warnings: [], error: "", path: "" };
      var panel = ui.panels.structure;
      panel.setAttribute("data-acp-nmr-geometry", geometry.status);

      if (geometry.status === "idle") {
        body.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.structure.hint")));
        var load = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-nmr-structure-load",
          "next.nmr.structure.load");
        load.type = "button";
        load.setAttribute("data-action", "load-geometry");
        body.appendChild(load);
        return;
      }
      if (geometry.status === "loading") {
        body.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.structure.loading")));
        return;
      }
      if (geometry.status === "unavailable") {
        body.appendChild(el(doc, "p", "acp-nmr-note", t("next.nmr.structure.unavailable")));
        appendGeometryReload(body);
        return;
      }
      if (geometry.status === "error") {
        body.appendChild(el(doc, "p", "acp-nmr-note acp-nmr-note--warn",
          t("next.nmr.structure.error", { message: geometry.error || "unknown" })));
        appendGeometryReload(body);
        return;
      }
      if (geometry.status !== "ready") return;

      var composition = geometry.elements.map(function (item) {
        return item.symbol + "×" + String(item.count);
      }).join(" ");
      body.appendChild(el(doc, "p", "acp-nmr-structure-summary",
        t("next.nmr.structure.ready", { count: geometry.atoms.length, composition: composition || "—" })));
      var chips = el(doc, "div", "acp-nmr-atom-chips");
      geometry.atoms.forEach(function (atom) {
        var chip = el(doc, "button", "acp-nmr-atom-chip", atom.label);
        chip.type = "button";
        chip.setAttribute("data-nmr-atom-chip", atom.label);
        chip.setAttribute("data-element", atom.symbol);
        chips.appendChild(chip);
      });
      body.appendChild(chips);
      var assignedLabels = assignmentAtomLabels(candidate);
      var missing = assignedLabels.filter(function (label) {
        return !geometry.atoms.some(function (atom) { return atom.label === label; });
      });
      if (missing.length) {
        var note = el(doc, "p", "acp-nmr-note acp-nmr-note--warn",
          t("next.nmr.structure.missing_labels", { labels: missing.slice(0, MAX_LISTED_CANDIDATES).join(", ") }));
        note.setAttribute("data-acp-nmr", "geometry-missing-labels");
        body.appendChild(note);
      }
      appendGeometryReload(body);
    }

    function appendGeometryReload(body) {
      var reload = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-nmr-structure-load",
        "next.nmr.structure.reload");
      reload.type = "button";
      reload.setAttribute("data-action", "load-geometry");
      body.appendChild(reload);
    }

    function assignmentAtomLabels(candidate) {
      var labels = [];
      (Array.isArray(candidate.assignment) ? candidate.assignment : []).forEach(function (assignment) {
        if (!isPlainObject(assignment)) return;
        var atom = asString(assignment.atom);
        if (atom && labels.indexOf(atom) === -1) labels.push(atom);
        if (isPlainObject(assignment.signal_group) && Array.isArray(assignment.signal_group.atom_uids)) {
          assignment.signal_group.atom_uids.forEach(function (item) {
            var label = asString(item);
            if (label && labels.indexOf(label) === -1) labels.push(label);
          });
        }
      });
      return labels;
    }

    // ── selection / highlight ───────────────────────────────────────────

    function intersects(a, b) {
      if (!Array.isArray(a) || !Array.isArray(b)) return false;
      for (var i = 0; i < a.length; i++) {
        if (b.indexOf(a[i]) !== -1) return true;
      }
      return false;
    }

    function selectAssignmentRow(rowIndex) {
      if (!state.ui.assignmentRows) return;
      var row = state.ui.assignmentRows[rowIndex];
      if (!row) return;
      var current = state.selection;
      if (current && current.pos === state.selectedPos && current.rowIndex === rowIndex) {
        clearSelection();
        return;
      }
      state.selection = {
        pos: state.selectedPos,
        rowIndex: rowIndex,
        atom: row.atom,
        observationId: row.observationId,
        atoms: row.atoms.slice(),
      };
      applySelection();
    }

    function selectPeakRow(rowIndex) {
      if (!state.ui.peakRows) return;
      var row = state.ui.peakRows[rowIndex];
      if (!row) return;
      var current = state.selection;
      if (current && current.pos === state.selectedPos &&
          current.observationId === row.observationId && current.atom == null) {
        clearSelection();
        return;
      }
      state.selection = {
        pos: state.selectedPos,
        rowIndex: null,
        atom: null,
        observationId: row.observationId,
        atoms: row.atomLabels.slice(),
      };
      applySelection();
      var candidate = currentCandidate();
      var claimed = candidate && row.candidateIndices.some(function (index) {
        return String(index) === String(candidate.index);
      });
      if (candidate && !claimed) {
        state.ui.highlightSummary.textContent =
          t("next.nmr.assignment.highlight_observation", { observation: row.observationId }) +
          " · " + t("next.nmr.peaks.current_none");
      }
    }

    function selectAtomLabel(label) {
      var text = asString(label);
      if (!text) return;
      var current = state.selection;
      if (current && current.pos === state.selectedPos && current.atom === text && current.observationId == null) {
        clearSelection();
        return;
      }
      state.selection = {
        pos: state.selectedPos,
        rowIndex: null,
        atom: text,
        observationId: null,
        atoms: [text],
      };
      setTab("assignment");
      applySelection();
    }

    function clearSelection() {
      state.selection = null;
      applySelection();
    }

    function applySelection() {
      var ui = state.ui;
      if (!ui.assignmentRows) return;
      var selection = state.selection && state.selection.pos === state.selectedPos ? state.selection : null;
      var activeCount = 0;
      var relatedCount = 0;
      ui.assignmentRows.forEach(function (row) {
        var direct = !!selection && (
          (selection.atom != null && row.atom === selection.atom) ||
          (selection.observationId != null && row.observationId === selection.observationId)
        );
        var related = !!selection && !direct && intersects(row.atoms, selection.atoms);
        if (direct) activeCount += 1;
        if (related) relatedCount += 1;
        row.node.setAttribute("data-acp-nmr-highlight", direct ? "active" : (related ? "related" : ""));
        row.node.className = "acp-nmr-row" +
          (direct ? " acp-nmr-row--active" : (related ? " acp-nmr-row--related" : ""));
      });
      (ui.peakRows || []).forEach(function (row) {
        var direct = !!selection && selection.observationId != null &&
          row.observationId === selection.observationId;
        var related = !!selection && !direct && intersects(row.atomLabels, selection.atoms);
        row.node.setAttribute("data-acp-nmr-highlight", direct ? "active" : (related ? "related" : ""));
        row.node.className = "acp-nmr-row acp-nmr-peak-row" +
          (direct ? " acp-nmr-row--active" : (related ? " acp-nmr-row--related" : ""));
      });
      var chips = ui.structureBody ? ui.structureBody.children : [];
      for (var i = 0; i < chips.length; i++) {
        var chipsNode = chips[i];
        if (!chipsNode || !chipsNode.children) continue;
        for (var j = 0; j < chipsNode.children.length; j++) {
          var chip = chipsNode.children[j];
          if (!chip || !chip.getAttribute || chip.getAttribute("data-nmr-atom-chip") == null) continue;
          var label = chip.getAttribute("data-nmr-atom-chip");
          var active = !!selection && selection.atoms.indexOf(label) !== -1;
          chip.setAttribute("data-active", active ? "true" : "false");
          chip.className = "acp-nmr-atom-chip" + (active ? " acp-nmr-atom-chip--active" : "");
        }
      }
      ui.clearHighlight.hidden = !selection;
      if (!selection) {
        ui.highlightSummary.textContent = t("next.nmr.assignment.none_selected");
        return;
      }
      var parts = [];
      if (selection.atom != null) parts.push(t("next.nmr.assignment.highlight_atom", { atom: selection.atom }));
      if (selection.observationId != null) {
        parts.push(t("next.nmr.assignment.highlight_observation", { observation: selection.observationId }));
      }
      if (activeCount) parts.push(t("next.nmr.assignment.highlight_related", { count: activeCount }));
      else if (relatedCount) parts.push(t("next.nmr.assignment.highlight_related", { count: relatedCount }));
      else parts.push(t("next.nmr.assignment.highlight_none"));
      ui.highlightSummary.textContent = parts.join(" · ");
    }

    // ── tabs / scroll ───────────────────────────────────────────────────

    function applyTab() {
      var ui = state.ui;
      if (!TABS.length) return;
      if (TABS.indexOf(state.activeTab) === -1) state.activeTab = "assignment";
      TABS.forEach(function (tab) {
        var active = tab === state.activeTab;
        var button = ui.tabButtons[tab];
        if (button) {
          button.setAttribute("aria-selected", active ? "true" : "false");
          button.setAttribute("data-active", active ? "true" : "false");
        }
        var panel = ui.panels[tab];
        if (panel) panel.hidden = !active;
      });
    }

    function setTab(tab) {
      if (TABS.indexOf(tab) === -1) return;
      var changed = tab !== state.activeTab;
      state.activeTab = tab;
      applyTab();
      if (tab === "structure") ensureGeometry(false);
      if (changed && state.selection) applySelection();
    }

    function preserveScroll(fn) {
      var scroll = state.ui.scroll;
      var top = scroll ? scroll.scrollTop : 0;
      fn();
      if (scroll) scroll.scrollTop = top;
    }

    // ── candidate switching (A-53) ──────────────────────────────────────

    function selectCandidate(pos) {
      if (state.disposed || !state.mounted) return;
      if (!isFinite(pos) || pos < 0 || pos >= state.candidates.length) return;
      if (pos === state.selectedPos) return;
      var gen = state.generation;
      state.selectToken += 1;
      state.geometryToken += 1;
      state.selectedPos = pos;
      state.selectedCandidateIndex = state.candidates[pos].index;
      state.selection = null;
      state.geometry = {
        status: "idle",
        candidatePos: pos,
        path: "",
        atoms: [],
        elements: [],
        warnings: [],
        error: "",
      };
      preserveScroll(function () {
        applyCandidateSelection();
        renderDetail();
        renderWarnings();
      });
      if (!isSelectionCurrent(gen, state.selectToken)) return;
      if (state.activeTab === "structure") ensureGeometry(false);
    }

    // ── async: report ───────────────────────────────────────────────────

    function jobFromDetail(detail) {
      if (!detail) return null;
      if (isPlainObject(detail.job)) return detail.job;
      if (isPlainObject(detail) && (detail.id || detail.spec)) return detail;
      return null;
    }

    function resolveJobDetail(jobId) {
      var reader = jobDetailReader();
      if (!reader) return Promise.resolve(null);
      return Promise.resolve()
        .then(function () { return reader.fetchDetail(jobId); })
        .then(jobFromDetail)
        .catch(function () { return null; });
    }

    function urlFor(path, mode) {
      var encoded = encodePath(path);
      var job = encodeURIComponent(state.jobId);
      if (state.isRemote) {
        return apiBase() + "/jobs/" + job + "/remote-files/" + encoded +
          "/preview?mode=" + encodeURIComponent(mode);
      }
      return apiBase() + "/jobs/" + job + "/files/" + encoded;
    }

    function requestReport(path) {
      var request = requestFn();
      if (!request) return Promise.reject(new Error(t("next.nmr.no_transport")));
      return Promise.resolve()
        .then(function () { return request(urlFor(path, "report"), { method: "GET" }); })
        .then(function (payload) { return unwrapReport(payload, state.isRemote); });
    }

    function probeReport(paths) {
      var index = 0;
      function next() {
        if (index >= paths.length) return Promise.resolve(null);
        var path = paths[index++];
        return requestReport(path).then(function (report) {
          if (report) return { report: report, path: path };
          return next();
        }, function (error) {
          if (isNotFound(error)) return next();
          throw error;
        });
      }
      return next();
    }

    function unwrapReport(payload, remote) {
      if (payload == null) return null;
      var value = payload;
      if (typeof value === "string") {
        var text = value.trim();
        if (!text) return null;
        try {
          value = JSON.parse(text);
        } catch (e) {
          throw invalidReportError();
        }
      }
      if (!isPlainObject(value)) return null;
      if (remote) {
        var content = value.content;
        if (typeof content === "string") {
          var contentText = content.trim();
          if (!contentText) return null;
          try {
            content = JSON.parse(contentText);
          } catch (e) {
            throw invalidReportError();
          }
        }
        if (isPlainObject(content)) {
          if (content.type === "unknown_report") return null;
          if (isPlainObject(content.report)) return content.report;
          if (Array.isArray(content.candidates)) return content;
        }
        if (isPlainObject(value.report)) return value.report;
        if (Array.isArray(value.candidates)) return value;
        return null;
      }
      if (Array.isArray(value.candidates)) return value;
      if (isPlainObject(value.report)) return value.report;
      return null;
    }

    function invalidReportError() {
      var error = new Error(t("next.nmr.invalid_report"));
      error.code = "invalid_report";
      return error;
    }

    function loadJob() {
      if (state.disposed || !state.mounted) return Promise.resolve();
      if (!state.jobId) {
        renderEmpty("no_job");
        return Promise.resolve();
      }
      var gen = state.generation;
      var token = ++state.loadToken;
      state.previousSelectedPos = state.selectedPos;
      state.previousSelectedIndex = state.selectedCandidateIndex;
      resetReport();
      state.loading = true;
      state.errorText = "";
      state.errorCode = "";
      state.emptyReason = "";
      state.emptyDetail = "";
      renderLoading();
      if (!requestFn()) {
        renderError(t("next.nmr.no_transport"), "no_transport");
        return Promise.resolve();
      }
      return resolveJobDetail(state.jobId).then(function (job) {
        if (!isCurrent(gen, token)) return { stop: true };
        if (job) {
          state.jobName = asString(job.resolved_name) || (job.spec && asString(job.spec.name)) || "";
          var workflow = job.spec && asString(job.spec.workflow);
          state.workflow = workflow;
          var remote = remoteNodeName(job);
          if (remote) state.isRemote = true;
          if (workflow && workflow !== NMR_WORKFLOW) {
            renderEmpty("not_nmr");
            return { stop: true };
          }
        }
        return probeReport(REPORT_PATHS).then(function (found) {
          return { stop: false, found: found };
        });
      }).then(function (result) {
        if (!result || result.stop) return;
        if (!isCurrent(gen, token)) return;
        if (!result.found) {
          renderEmpty("no_report");
          return;
        }
        acceptReport(result.found.report, result.found.path);
      }).catch(function (error) {
        if (!isCurrent(gen, token)) return;
        var code = isForbidden(error) ? "forbidden" : (error && error.code === "invalid_report" ? "invalid_report" : "read_failed");
        var message = code === "forbidden"
          ? t("next.nmr.forbidden")
          : (code === "invalid_report"
            ? t("next.nmr.invalid_report")
            : t("next.nmr.read_failed", { message: describeError(error) }));
        renderError(message, code);
      });
    }

    function acceptReport(report, path) {
      var normalized = normalizeReport(report);
      state.report = report;
      state.reportPath = path || "";
      state.normalized = normalized;
      state.reportWarnings = normalized.warnings;
      state.candidates = normalized.candidates;
      state.loading = false;
      state.errorText = "";
      state.errorCode = "";
      if (!normalized.candidates.length) {
        renderEmpty("no_candidates");
        return;
      }
      if (!normalized.hasDp) {
        var labels = normalized.candidates.slice(0, MAX_LISTED_CANDIDATES).map(function (candidate, pos) {
          return candidateLabel(candidate, pos);
        }).join(", ");
        var detail = t("next.nmr.no_dp4_hint", {
          count: normalized.nCandidates,
          labels: labels || "—",
          reasons: collectCandidateReasons(normalized.candidates),
        });
        if (!normalized.assignmentCount && !normalized.hasProcessing) {
          renderEmpty("no_spectrum_data");
        } else {
          renderEmpty("no_dp4_data", detail);
        }
        return;
      }
      var previousPos = state.previousSelectedPos;
      var previousIndex = state.previousSelectedIndex;
      state.previousSelectedPos = -1;
      state.previousSelectedIndex = null;
      state.selectedPos = pickInitialPos(previousPos, previousIndex);
      state.selectedCandidateIndex = state.candidates[state.selectedPos].index;
      state.selection = null;
      state.geometry = {
        status: "idle",
        candidatePos: state.selectedPos,
        path: "",
        atoms: [],
        elements: [],
        warnings: [],
        error: "",
      };
      renderReady();
    }

    function pickInitialPos(previousPos, previousIndex) {
      if (previousIndex != null) {
        for (var i = 0; i < state.candidates.length; i++) {
          if (String(state.candidates[i].index) === String(previousIndex)) return i;
        }
      }
      if (previousPos != null && previousPos >= 0 && previousPos < state.candidates.length) {
        return previousPos;
      }
      var winner = state.normalized && state.normalized.winner;
      if (winner && winner.index != null) {
        for (var j = 0; j < state.candidates.length; j++) {
          if (String(state.candidates[j].index) === String(winner.index)) return j;
        }
      }
      return 0;
    }

    // ── async: candidate geometry (read-only) ───────────────────────────

    function geometryPaths(candidate) {
      var paths = [];
      var label = asString(candidate && candidate.label);
      if (label) paths.push(GEOMETRY_DIR + "/" + label + GEOMETRY_SUFFIX);
      paths.push(GEOMETRY_FALLBACK);
      return paths;
    }

    function requestGeometry(path) {
      var request = requestFn();
      if (!request) return Promise.reject(new Error(t("next.nmr.no_transport")));
      return Promise.resolve()
        .then(function () { return request(urlFor(path, "structure"), { method: "GET" }); })
        .then(function (payload) { return unwrapGeometry(payload, state.isRemote); });
    }

    function probeGeometry(paths) {
      var index = 0;
      function next() {
        if (index >= paths.length) return Promise.resolve(null);
        var path = paths[index++];
        return requestGeometry(path).then(function (text) {
          if (text) return { text: text, path: path };
          return next();
        }, function (error) {
          if (isNotFound(error)) return next();
          throw error;
        });
      }
      return next();
    }

    function unwrapGeometry(payload, remote) {
      if (typeof payload === "string") return payload.trim() ? payload : null;
      if (!isPlainObject(payload)) return null;
      if (typeof payload.xyz === "string" && payload.xyz.trim()) return payload.xyz;
      if (typeof payload.text === "string" && payload.text.trim()) return payload.text;
      var content = payload.content;
      if (typeof content === "string" && content.trim()) return content;
      if (isPlainObject(content)) {
        if (typeof content.xyz === "string" && content.xyz.trim()) return content.xyz;
        if (typeof content.text === "string" && content.text.trim()) return content.text;
      }
      if (!remote && typeof payload.content === "string" && payload.content.trim()) return payload.content;
      return null;
    }

    function ensureGeometry(force) {
      if (state.disposed || !state.mounted) return Promise.resolve();
      var candidate = currentCandidate();
      if (!candidate) return Promise.resolve();
      var geometry = state.geometry;
      if (!force && geometry.candidatePos === state.selectedPos &&
          (geometry.status === "loading" || geometry.status === "ready" ||
           geometry.status === "unavailable" || geometry.status === "error")) {
        return Promise.resolve();
      }
      var gen = state.generation;
      var selectToken = state.selectToken;
      var candidatePos = state.selectedPos;
      var token = ++state.geometryToken;
      state.geometry = {
        status: "loading",
        candidatePos: candidatePos,
        path: "",
        atoms: [],
        elements: [],
        warnings: [],
        error: "",
      };
      renderStructure(candidate);
      if (!requestFn()) {
        state.geometry.status = "error";
        state.geometry.error = t("next.nmr.no_transport");
        renderStructure(candidate);
        return Promise.resolve();
      }
      return probeGeometry(geometryPaths(candidate)).then(function (found) {
        if (!isGeometryCurrent(gen, selectToken, token, candidatePos)) return;
        if (!found) {
          state.geometry.status = "unavailable";
          state.geometry.candidatePos = candidatePos;
          renderStructure(candidate);
          return;
        }
        var parsed = parseXyz(found.text);
        if (!parsed.atoms.length) {
          state.geometry.status = "error";
          state.geometry.error = "no atoms in xyz";
          state.geometry.candidatePos = candidatePos;
          renderStructure(candidate);
          return;
        }
        state.geometry = {
          status: "ready",
          candidatePos: candidatePos,
          path: found.path,
          atoms: parsed.atoms,
          elements: parsed.elements,
          warnings: parsed.warnings,
          error: "",
        };
        renderStructure(currentCandidate());
        applySelection();
        renderWarnings();
      }).catch(function (error) {
        if (!isGeometryCurrent(gen, selectToken, token, candidatePos)) return;
        state.geometry.status = "error";
        state.geometry.candidatePos = candidatePos;
        state.geometry.error = describeError(error);
        renderStructure(currentCandidate());
      });
    }

    function parseXyz(text) {
      var lines = String(text || "").split(/\r?\n/);
      var atoms = [];
      var warnings = [];
      var first = -1;
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].trim()) { first = i; break; }
      }
      if (first < 0) return { atoms: [], elements: [], warnings: ["empty xyz"] };
      var declaredText = lines[first].trim();
      var declared = Number(declaredText);
      var declaredValid = isFinite(declared) && declared > 0 && Math.floor(declared) === declared &&
        String(declared) === declaredText.replace(/^\+/, "");
      var start = first;
      if (declaredValid) {
        // Standard XYZ has a comment line after the count; tolerate a file
        // that omits it by detecting an atom line immediately below.
        var afterCount = first + 1 < lines.length ? parseAtomLine(lines[first + 1]) : null;
        start = afterCount ? first + 1 : first + 2;
        for (var k = 0; k < declared; k++) {
          var lineIndex = start + k;
          if (lineIndex >= lines.length) break;
          var atom = parseAtomLine(lines[lineIndex]);
          if (!atom) break;
          atoms.push(atom);
        }
        if (atoms.length !== declared) {
          warnings.push("xyz atom count mismatch");
        }
      } else {
        for (var m = 0; m < lines.length; m++) {
          var parsed = parseAtomLine(lines[m]);
          if (parsed) atoms.push(parsed);
        }
      }
      var counters = {};
      atoms.forEach(function (atom, index) {
        var symbol = atom.symbol;
        counters[symbol] = (counters[symbol] || 0) + 1;
        atom.index = index;
        atom.label = symbol + String(counters[symbol]);
      });
      var elements = [];
      atoms.forEach(function (atom) {
        var found = null;
        for (var n = 0; n < elements.length; n++) {
          if (elements[n].symbol === atom.symbol) { found = elements[n]; break; }
        }
        if (found) found.count += 1;
        else elements.push({ symbol: atom.symbol, count: 1 });
      });
      return { atoms: atoms, elements: elements, warnings: warnings };
    }

    function parseAtomLine(line) {
      var parts = String(line || "").trim().split(/\s+/);
      if (parts.length < 4) return null;
      var symbol = normalizeSymbol(parts[0]);
      if (!/^[A-Z][a-z]?$/.test(symbol)) return null;
      var x = Number(parts[1]);
      var y = Number(parts[2]);
      var z = Number(parts[3]);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) return null;
      return { symbol: symbol, x: x, y: y, z: z };
    }

    // ── lifecycle ───────────────────────────────────────────────────────

    function applySnapshot(snapshot) {
      if (state.disposed || !state.mounted) return;
      state.snapshot = snapshot || null;
      var projectId = snapshot && snapshot.projectId != null
        ? String(snapshot.projectId)
        : (deps.projectId != null ? String(deps.projectId) : "");
      var ref = snapshot && snapshot.taskRef ? snapshot.taskRef : null;
      var jobId = ref && ref.jobId != null
        ? String(ref.jobId)
        : (deps.jobId != null ? String(deps.jobId) : "");
      var attempt = ref && ref.attempt != null ? String(ref.attempt) : "";
      var explicitRemote = false;
      if (deps.isRemote === true || deps.remote === true) explicitRemote = true;
      if (ref && (ref.isRemote === true || ref.remote === true)) explicitRemote = true;
      if (ref && (ref.nodeId || ref.node_id) && String(ref.nodeId || ref.node_id) !== "local") {
        explicitRemote = true;
      }
      if (snapshot && snapshot.isRemote === true) explicitRemote = true;
      if (deps.job && remoteNodeName(deps.job)) explicitRemote = true;

      var key = projectId + "\u0000" + jobId + "\u0000" + attempt;
      state.projectId = projectId;
      if (key === state.jobKey) return;
      state.jobKey = key;
      state.jobId = jobId;
      state.isRemote = explicitRemote;
      state.jobName = "";
      state.workflow = "";
      state.generation += 1;
      state.loadToken += 1;
      state.selectToken += 1;
      state.geometryToken += 1;
      state.activeTab = "assignment";
      resetReport();
      if (!jobId) {
        renderEmpty("no_job");
        return;
      }
      loadJob();
    }

    function refreshReport() {
      if (state.disposed || !state.mounted) return;
      if (!state.jobId) { renderEmpty("no_job"); return; }
      state.generation += 1;
      state.loadToken += 1;
      state.selectToken += 1;
      state.geometryToken += 1;
      loadJob();
    }

    function rerender() {
      if (state.disposed || !state.mounted) return;
      if (state.report && state.normalized && state.normalized.hasDp) renderReady();
      else if (state.loading) renderLoading();
      else if (state.errorText) renderError(state.errorText, state.errorCode);
      else renderEmpty(state.emptyReason, state.emptyDetail);
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("nmr-report: mount host missing");
      if (!doc) throw new Error("nmr-report: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.loadToken += 1;
      state.selectToken += 1;
      state.geometryToken += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.jobKey = "";
      state.activeTab = "assignment";
      buildDom();
      renderEmpty("no_job");
      var context = api.context || deps.context || null;
      if (context && typeof context.getSnapshot === "function") {
        applySnapshot(context.getSnapshot());
      } else {
        applySnapshot(null);
      }
      if (context && typeof context.subscribe === "function") {
        var unsubscribe = context.subscribe(function (snapshot) {
          if (state.disposed || !state.mounted) return;
          applySnapshot(snapshot);
        });
        cleanup.push(unsubscribe);
      }
      var langHandler = function () { rerender(); };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
    }

    function suspend() {
      state.suspended = true;
    }

    function resume() {
      state.suspended = false;
      rerender();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.loadToken += 1;
      state.selectToken += 1;
      state.geometryToken += 1;
      state.loading = false;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.report = null;
      state.normalized = null;
      state.candidates = [];
      state.reportWarnings = [];
      state.selection = null;
      state.snapshot = null;
      state.jobKey = "";
      state.jobId = "";
      state.projectId = "";
      state.jobName = "";
      state.workflow = "";
      state.emptyReason = "";
      state.emptyDetail = "";
      state.errorText = "";
      state.errorCode = "";
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
      refresh: refreshReport,
      // Test/inspection seam: a stable snapshot of the loaded report and the
      // current selection.  It is read-only data; the report object itself is
      // never mutated by the module.
      snapshot: function () {
        return {
          report: state.report,
          reportPath: state.reportPath,
          selectedPos: state.selectedPos,
          selectedIndex: state.selectedCandidateIndex,
          activeTab: state.activeTab,
          selection: state.selection ? {
            pos: state.selection.pos,
            atom: state.selection.atom,
            observationId: state.selection.observationId,
            atoms: state.selection.atoms.slice(),
          } : null,
          emptyReason: state.emptyReason,
          errorCode: state.errorCode,
          loading: state.loading,
        };
      },
      _internals: {
        parseXyz: parseXyz,
        buildPeakRows: buildPeakRows,
        barWidth: barWidth,
      },
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createNmrReport,
  };
})();
