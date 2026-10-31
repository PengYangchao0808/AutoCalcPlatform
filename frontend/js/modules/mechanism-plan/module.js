/*
 * ACP 3.0 module — mechanism-plan (Track A / A-49 · P17 “机理计划装配（步骤 / 角色）”).
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["mechanism-plan"]`); the Shell loader resolves the
 * factory and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (A-49 only):
 *   - load a historical mechanism study through the EXISTING read routes
 *       GET /api/v1/mechanism-studies?job_id=&limit=          (study picker)
 *       GET /api/v1/mechanism-studies/{id}                    (detail + study_json)
 *       GET /api/v1/mechanism-studies/{id}/report             (network/profile/provenance)
 *       GET /api/v1/mechanism-studies/{id}/reaction           (locked reaction definition)
 *     and display steps, roles, reactants/products/intermediates, reaction lock
 *     status and provenance;
 *   - assemble a reaction-coordinate plan from the study's own elementary-step /
 *     route coordinate plans (never from guessed chemistry), assign each
 *     coordinate a drive/freeze/monitor role, and confirm it through the EXISTING
 *       POST /api/v1/mechanism-studies/{id}/mechanism/plan
 *     with the frozen body `{plan, strategy, fidelity}`. There is no plan
 *     preview endpoint: this module shows a local plan preview + local
 *     fingerprint, and the server's `plan_hash` after confirmation.
 *
 * The plan-confirm route is currently RETIRED on the backend (the handler
 * answers 410 "该机制研究端点已退役，历史只读"). This module still calls that
 * one existing route — it does not invent a replacement, does not fall back to
 * the task-submission route, and surfaces the 410 detail as an explicit error
 * state instead of a fake success. The reaction preview/confirm routes are also
 * 410 and are deliberately NOT called.
 *
 * Out of scope (do not add here): A-50 structure path view/edit (no 3D scene,
 * no measurements, no geometry editing) and A-51 PES/Batch hand-off (no
 * candidate publication, no task submission). The only POST in this module is
 * the mechanism-plan confirmation above.
 *
 * Data flows through the injected client seam only
 * (`deps.request` -> `deps.client.transport.request` ->
 * `window.ACPClient.transport.request`); this module performs no direct network
 * call of its own and never reads a global network primitive. No filesystem path
 * is built: the API URLs are the four route constants plus encodeURIComponent(id),
 * and the study snapshot's local directory field is never read or rendered.
 *
 * Four states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error (study data state). Command busy state is
 * separate (`data-acp-busy` = none | load | confirm); plan command state is
 * `data-acp-plan-state` = idle | blocked | confirming | confirmed | error.
 * Every async result is guarded by the mount generation plus a per-request
 * token, so dispose / a newer study / a newer confirmation can never paint into
 * the DOM. dispose() is idempotent and removes the DOM it created, its context
 * subscription and its language listener.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "mechanism-plan";
  var API = "/api/v1";
  var STUDIES_PATH = API + "/mechanism-studies";
  var LIST_LIMIT = 50;
  var STRATEGIES = ["guided-scan", "rph-reverse", "direct-ts"];
  var FIDELITIES = ["s3", "s4"];
  var COORDINATE_ROLES = ["drive", "freeze", "monitor"];
  var COORDINATE_KINDS = ["distance", "angle", "dihedral"];
  var KIND_ATOMS = { distance: 2, angle: 3, dihedral: 4 };
  var START_FROM_VALUES = ["reactant", "product", "custom"];
  var MIN_POINTS = 2;
  var DEFAULT_POINTS = 21;
  var MAX_PROVENANCE_RECORDS = 20;
  var INTEGER_RE = /^[+-]?\d+$/;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["mechanism.plan"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["mechanism.plan"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.mechplan.errorTitle", messageKey: "next.mechplan.errorMessage" }
  };

  /* Local zh copy. The Shell dictionary is consulted first; `ACPShell.i18n.t()`
   * echoes the key when no translation exists, so these values are the real
   * fallback until the next shell i18n batch adds `next.mechplan.*` (README §7
   * lists the full key set). Static chrome also carries data-i18n. */
  var ZH = {
    "next.mechplan.title": "机理计划装配",
    "next.mechplan.subtitle": "步骤 / 角色 / 反应坐标计划 · 只读研究 + 既有计划确认端点",
    "next.mechplan.study_title": "1 · 机理研究",
    "next.mechplan.study_hint": "按研究 ID 载入，或从列表选择；上下文任务会自动列出其关联研究。",
    "next.mechplan.study_id": "研究 ID",
    "next.mechplan.study_id_ph": "例如 study-20261031-001",
    "next.mechplan.load_study": "载入研究",
    "next.mechplan.list_studies": "浏览全部研究",
    "next.mechplan.study_select": "研究列表",
    "next.mechplan.study_select_ph": "选择研究…",
    "next.mechplan.list_loading": "正在载入研究列表…",
    "next.mechplan.list_empty": "没有可用的机理研究。",
    "next.mechplan.list_ready": "共 {count} 个研究{scope}。",
    "next.mechplan.list_scope_job": "（任务 {jobId}）",
    "next.mechplan.list_error": "研究列表加载失败：{message}",
    "next.mechplan.summary_title": "2 · 研究概览",
    "next.mechplan.summary_id": "研究 ID",
    "next.mechplan.summary_job": "任务",
    "next.mechplan.summary_status": "研究状态",
    "next.mechplan.summary_unified": "统一状态",
    "next.mechplan.summary_plan": "计划状态",
    "next.mechplan.summary_updated": "更新时间",
    "next.mechplan.summary_decisions": "决策点",
    "next.mechplan.steps_title": "3 · 步骤与角色",
    "next.mechplan.steps_hint": "步骤来自研究报告/研究快照；角色按 stable state 与驻点记录显示，不在此推断化学。",
    "next.mechplan.steps_empty": "该研究没有可展示的基元步骤。",
    "next.mechplan.step_col": "步骤",
    "next.mechplan.step_source": "起点状态",
    "next.mechplan.step_sink": "终点状态",
    "next.mechplan.step_ts": "TS",
    "next.mechplan.step_barrier_f": "正向能垒",
    "next.mechplan.step_barrier_r": "逆向能垒",
    "next.mechplan.step_fidelity": "保真度",
    "next.mechplan.step_strategy": "路径策略",
    "next.mechplan.step_status": "状态",
    "next.mechplan.roles_title": "角色",
    "next.mechplan.role_reactant": "反应物",
    "next.mechplan.role_product": "产物",
    "next.mechplan.role_intermediate": "中间体",
    "next.mechplan.role_ts": "过渡态",
    "next.mechplan.role_unknown": "未分类",
    "next.mechplan.role_empty": "—",
    "next.mechplan.role_item": "{id} · {role}",
    "next.mechplan.role_energy": "{energy} Ha",
    "next.mechplan.reaction_title": "4 · 反应定义（只读）",
    "next.mechplan.reaction_hint": "来自 GET /mechanism-studies/{id}/reaction；仅展示已锁定/历史反应定义。",
    "next.mechplan.reaction_unavailable": "该研究没有可读的反应定义。",
    "next.mechplan.reaction_reactants": "反应物",
    "next.mechplan.reaction_products": "产物",
    "next.mechplan.reaction_item": "{smiles} · 电荷 {charge} · 多重度 {multiplicity}",
    "next.mechplan.reaction_mapping": "原子映射 {count} 对",
    "next.mechplan.reaction_hash": "反应配置指纹（config_hash）：{hash}",
    "next.mechplan.reaction_confirmed_by": "确认来源：{value}",
    "next.mechplan.reaction_status": "反应状态：{status}",
    "next.mechplan.provenance_title": "5 · 溯源",
    "next.mechplan.provenance_hint": "来自研究报告 provenance.json；模型叙述不作为证据。",
    "next.mechplan.provenance_empty": "研究报告未包含 provenance。",
    "next.mechplan.provenance_summary": "记录 {count} 条 · 输入签名 {signature}",
    "next.mechplan.provenance_more": "仅展示前 {shown}/{total} 条记录。",
    "next.mechplan.plan_title": "6 · 计划装配与确认",
    "next.mechplan.plan_hint": "复用研究自带的坐标计划；可调整步骤与每个坐标的角色。确认调用既有端点 POST /mechanism-studies/{id}/mechanism/plan，不提交任务。",
    "next.mechplan.plan_strategy": "路径策略",
    "next.mechplan.plan_fidelity": "保真度",
    "next.mechplan.plan_points": "扫描点数",
    "next.mechplan.plan_start_from": "起点语义",
    "next.mechplan.start_from_reactant": "反应物",
    "next.mechplan.start_from_product": "产物",
    "next.mechplan.start_from_custom": "自定义",
    "next.mechplan.strategy_guided": "导向扫描（guided-scan）",
    "next.mechplan.strategy_rph": "RPH 反向（rph-reverse）",
    "next.mechplan.strategy_direct": "直接 TS（direct-ts）",
    "next.mechplan.candidates_title": "可选步骤 / 路线",
    "next.mechplan.candidates_empty": "该研究没有可复用的反应坐标计划。",
    "next.mechplan.candidates_empty_direct": "该研究没有坐标计划；direct-ts 不需要坐标，可直接确认。",
    "next.mechplan.candidate_step": "步骤 {step}：{source} → {sink}（{strategy} · {fidelity}）",
    "next.mechplan.candidate_route": "路线 {route}（{strategy} · {fidelity}）",
    "next.mechplan.coords_title": "计划坐标",
    "next.mechplan.coords_col_id": "坐标",
    "next.mechplan.coords_col_kind": "类型",
    "next.mechplan.coords_col_atoms": "原子（1 基显示）",
    "next.mechplan.coords_col_start": "起点",
    "next.mechplan.coords_col_end": "终点",
    "next.mechplan.coords_col_role": "角色",
    "next.mechplan.coords_empty": "未选择坐标。",
    "next.mechplan.kind_distance": "距离",
    "next.mechplan.kind_angle": "角度",
    "next.mechplan.kind_dihedral": "二面角",
    "next.mechplan.role_drive": "驱动",
    "next.mechplan.role_freeze": "冻结",
    "next.mechplan.role_monitor": "监控",
    "next.mechplan.preview_title": "计划预览",
    "next.mechplan.preview_fingerprint": "本地指纹（fnv1a，仅用于装配预览；服务端 plan_hash 才是权威）：{hash}",
    "next.mechplan.preview_note": "坐标原子按 0 基发送（与 CoordinateSpec 契约一致），界面按 1 基显示。",
    "next.mechplan.confirm": "确认机理计划",
    "next.mechplan.confirming": "正在确认…",
    "next.mechplan.confirm_disabled": "仅当研究已载入、计划通过本地装配校验且无进行中请求时可确认。",
    "next.mechplan.confirm_idle": "尚未确认。确认后服务端返回 plan_hash。",
    "next.mechplan.confirm_blocked": "计划未通过本地装配校验，未发送确认。",
    "next.mechplan.confirm_ok": "计划已确认：{status} · plan_hash {hash}",
    "next.mechplan.confirm_error": "确认失败：{message}",
    "next.mechplan.confirm_retired": "该端点已退役（HTTP 410）：{detail}",
    "next.mechplan.confirm_not_locked": "研究反应未锁定（HTTP 409）：{detail}",
    "next.mechplan.invalid_response": "服务端返回了无法识别的确认响应。",
    "next.mechplan.receipt_title": "确认回执",
    "next.mechplan.receipt_status": "计划状态：{status}",
    "next.mechplan.receipt_hash": "服务端 plan_hash：{hash}",
    "next.mechplan.receipt_error": "确认错误：{message}",
    "next.mechplan.plan_status_confirmed": "已确认（{status}）",
    "next.mechplan.plan_status_reaction_locked": "反应已锁定，计划待确认（{status}）",
    "next.mechplan.plan_status_pending": "未确认（{status}）",
    "next.mechplan.plan_status_unknown": "未确认",
    "next.mechplan.empty_title": "尚未载入机理研究",
    "next.mechplan.empty_hint": "输入研究 ID 载入，或在有任务上下文时从研究列表选择。",
    "next.mechplan.loading_title": "正在载入机理研究…",
    "next.mechplan.error_title": "机理研究加载失败",
    "next.mechplan.errorTitle": "机理计划装配不可用",
    "next.mechplan.errorMessage": "mechanism-plan 模块加载失败，请查看控制台。",
    "next.mechplan.retry": "重试",
    "next.mechplan.notices": "部分数据不可用",
    "next.mechplan.report_unavailable": "研究报告不可用：{message}",
    "next.mechplan.reaction_unavailable_detail": "反应定义读取失败：{message}",
    "next.mechplan.err.client_missing": "客户端未注入（ACPClient.transport），无法读取或确认。",
    "next.mechplan.err.study_id_required": "请先输入研究 ID。",
    "next.mechplan.err.plan_invalid": "计划必须是对象。",
    "next.mechplan.err.strategy_invalid": "路径策略无效。",
    "next.mechplan.err.fidelity_invalid": "保真度无效。",
    "next.mechplan.err.points_invalid": "扫描点数必须是 ≥ {min} 的整数。",
    "next.mechplan.err.coupling_invalid": "耦合方式必须是 synchronous。",
    "next.mechplan.err.start_from_invalid": "起点语义无效。",
    "next.mechplan.err.coordinates_invalid": "计划坐标必须是数组。",
    "next.mechplan.err.coordinates_missing": "非 direct-ts 计划至少需要一个反应坐标。",
    "next.mechplan.err.coordinate_invalid": "坐标 {index} 不是对象。",
    "next.mechplan.err.coordinate_id_missing": "坐标 {index} 缺少 id。",
    "next.mechplan.err.coordinate_kind_invalid": "坐标 {id} 的类型无效。",
    "next.mechplan.err.coordinate_atoms_invalid": "坐标 {id} 需要 {count} 个整数原子索引。",
    "next.mechplan.err.coordinate_role_invalid": "坐标 {id} 的角色无效。",
    "next.mechplan.err.drive_start_end": "驱动坐标 {id} 需要有限的起点与终点。",
    "next.mechplan.err.freeze_start": "冻结坐标 {id} 需要有限的起点。",
    "next.mechplan.err.drive_missing": "计划至少需要一个 drive 坐标。"
  };

  /* ── shared helpers ─────────────────────────────────────────────── */

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    var value = key;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
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

  function has(node, key) {
    return Object.prototype.hasOwnProperty.call(node, key);
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function textOf(value) {
    return value == null ? "" : String(value);
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") node.replaceChildren();
    else node.textContent = "";
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function intOrNull(value) {
    var raw = textOf(value).trim();
    if (!INTEGER_RE.test(raw)) return null;
    return parseInt(raw, 10);
  }

  function numberOrNull(value) {
    if (value == null || value === "") return null;
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : null;
  }

  /* Typed text -> number for plan coordinates: "" means "not provided" (null),
   * anything non-numeric stays NaN so validatePlanBody can flag it instead of
   * silently dropping the user's value. */
  function numericText(value) {
    var raw = textOf(value).trim();
    if (raw === "") return null;
    return Number(raw);
  }

  function isIntegerAtom(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value && value >= 0;
  }

  function formatNumber(value, digits) {
    var n = Number(value);
    if (!isFinite(n)) return "—";
    return n.toFixed(digits == null ? 2 : digits);
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object") {
        var code = detail.code ? String(detail.code) : "";
        var message = detail.message ? String(detail.message) : "";
        if (code || message) return (code ? code + ": " : "") + (message || JSON.stringify(detail));
      }
      if (typeof detail === "string" && detail) return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function errorStatus(error) {
    return error && typeof error.status === "number" ? error.status : null;
  }

  function errorText(error) {
    if (!error || !error.code) return "";
    var key = "next.mechplan.err." + error.code;
    if (!has(ZH, key)) return String(error.code);
    return t(key, error.vars || null);
  }

  /* ── route helpers (existing routes only) ───────────────────────── */

  function studyPath(studyId) {
    return STUDIES_PATH + "/" + encodeURIComponent(textOf(studyId));
  }

  function listQuery(jobId) {
    var query = "?limit=" + String(LIST_LIMIT);
    if (jobId) query = "?job_id=" + encodeURIComponent(String(jobId)) + "&limit=" + String(LIST_LIMIT);
    return query;
  }

  /* ── wire normalisation (pure) ──────────────────────────────────── */

  function normalizeStudyList(payload) {
    var raw = Array.isArray(payload)
      ? payload
      : (isObject(payload) && Array.isArray(payload.studies) ? payload.studies : []);
    var out = [];
    raw.forEach(function (item) {
      if (!isObject(item)) return;
      var id = textOf(item.id || item.study_id);
      if (!id) return;
      out.push({
        id: id,
        jobId: item.job_id == null ? "" : String(item.job_id),
        status: textOf(item.status),
        unifiedStatus: textOf(item.unified_status),
        createdAt: textOf(item.created_at),
        updatedAt: textOf(item.updated_at),
        nStates: intOrNull(item.n_states) || 0,
        nEdges: intOrNull(item.n_edges) || 0,
        nDecisionsPending: intOrNull(item.n_decisions_pending) || 0
      });
    });
    return out;
  }

  function normalizeDecision(item) {
    return {
      id: textOf(item && item.id),
      status: textOf(item && item.status),
      resolution: textOf(item && item.resolution),
      createdAt: textOf(item && item.created_at)
    };
  }

  function normalizeDetail(payload) {
    var body = isObject(payload) ? payload : {};
    return {
      id: textOf(body.id || body.study_id),
      jobId: body.job_id == null ? "" : String(body.job_id),
      status: textOf(body.status),
      unifiedStatus: textOf(body.unified_status),
      createdAt: textOf(body.created_at),
      updatedAt: textOf(body.updated_at),
      studyJson: isObject(body.study_json) ? body.study_json : {},
      decisions: asArray(body.decisions).filter(isObject).map(normalizeDecision)
    };
  }

  function normalizeReport(payload) {
    var body = isObject(payload) ? payload : {};
    return {
      reactionNetwork: isObject(body.reaction_network) ? body.reaction_network : null,
      mechanismProfile: isObject(body.mechanism_profile) ? body.mechanism_profile : null,
      stationaryPoints: isObject(body.stationary_points) ? body.stationary_points : null,
      qualityGates: isObject(body.quality_gates) ? body.quality_gates : null,
      provenance: isObject(body.provenance) ? body.provenance : null
    };
  }

  function normalizeReaction(payload) {
    var body = isObject(payload) ? payload : {};
    var reaction = isObject(body.reaction) ? body.reaction : null;
    return {
      available: !!reaction,
      status: textOf(body.status),
      schemaVersion: reaction ? intOrNull(reaction.schema_version) : null,
      reactants: reaction ? asArray(reaction.reactants).filter(isObject) : [],
      products: reaction ? asArray(reaction.products).filter(isObject) : [],
      mapping: reaction ? asArray(reaction.mapping) : [],
      configHash: reaction ? textOf(reaction.config_hash) : "",
      confirmedBy: reaction ? textOf(reaction.confirmed_by) : ""
    };
  }

  function normalizeCoordinatePlan(plan) {
    if (!isObject(plan) || !Array.isArray(plan.coordinates)) return null;
    var coordinates = [];
    plan.coordinates.forEach(function (coordinate, index) {
      if (!isObject(coordinate)) return;
      coordinates.push({
        id: textOf(coordinate.id) || "coord-" + String(index + 1),
        kind: textOf(coordinate.kind),
        atoms: asArray(coordinate.atoms).map(function (atom) { return Number(atom); }),
        role: textOf(coordinate.role) || "drive",
        start: numberOrNull(coordinate.start),
        end: numberOrNull(coordinate.end),
        forceConstant: numberOrNull(coordinate.force_constant)
      });
    });
    if (!coordinates.length) return null;
    return {
      coordinates: coordinates,
      points: intOrNull(plan.points),
      coupling: textOf(plan.coupling) || "synchronous",
      startFrom: textOf(plan.start_from) || "reactant"
    };
  }

  function validStrategy(value) {
    return STRATEGIES.indexOf(textOf(value)) !== -1 ? textOf(value) : "";
  }

  function validFidelity(value) {
    return FIDELITIES.indexOf(textOf(value)) !== -1 ? textOf(value) : "";
  }

  /* Role classification is display-only. It never invents a role: values that
   * are not one of the persisted vocabulary land in "unknown". */
  function classifyRole(role) {
    var value = textOf(role).trim().toLowerCase();
    if (!value) return "unknown";
    if (value.indexOf("transition") !== -1 || value === "ts" || value.indexOf("ts_") === 0 ||
        value.indexOf("_ts") !== -1) return "ts";
    if (value.indexOf("react") !== -1 || value.indexOf("start") !== -1) return "reactant";
    if (value.indexOf("prod") !== -1 || value.indexOf("end") !== -1) return "product";
    if (value.indexOf("inter") !== -1) return "intermediate";
    return "unknown";
  }

  function roleItem(id, role, source, extra) {
    extra = extra || {};
    return {
      id: textOf(id),
      role: textOf(role),
      bucket: classifyRole(role),
      source: source,
      kind: textOf(extra.kind),
      charge: numberOrNull(extra.charge),
      multiplicity: numberOrNull(extra.multiplicity),
      energyHartree: numberOrNull(extra.energyHartree)
    };
  }

  function collectRoleBuckets(detail, report) {
    var buckets = { reactant: [], product: [], intermediate: [], ts: [], unknown: [] };
    var seen = Object.create(null);

    function add(item) {
      if (!item.id) return;
      var key = item.bucket + "|" + item.id;
      if (seen[key]) return;
      seen[key] = true;
      buckets[item.bucket].push(item);
    }

    if (report && report.reactionNetwork) {
      asArray(report.reactionNetwork.nodes).forEach(function (node) {
        if (!isObject(node)) return;
        add(roleItem(node.state_id, node.role, "report.network", {
          charge: node.charge,
          multiplicity: node.multiplicity,
          energyHartree: node.canonical_energy_hartree
        }));
      });
    }
    if (detail && isObject(detail.studyJson)) {
      asArray(detail.studyJson.stable_states).forEach(function (state) {
        if (!isObject(state)) return;
        add(roleItem(state.state_id, state.role, "study.stable_states", {
          charge: state.charge,
          multiplicity: state.multiplicity
        }));
      });
    }
    if (report && report.stationaryPoints) {
      asArray(report.stationaryPoints.stationary_points).forEach(function (point) {
        if (!isObject(point)) return;
        add(roleItem(textOf(point.state_id) || point.point_id,
          point.kind === "ts" ? "transition_state" : point.role, "report.stationary_points", {
            kind: point.kind,
            charge: point.charge,
            multiplicity: point.multiplicity,
            energyHartree: point.energy_hartree
          }));
      });
    }
    if (detail && isObject(detail.studyJson)) {
      asArray(detail.studyJson.stationary_points).forEach(function (point) {
        if (!isObject(point)) return;
        add(roleItem(textOf(point.state_id) || point.point_id,
          point.kind === "ts" ? "transition_state" : point.role, "study.stationary_points", {
            kind: point.kind,
            charge: point.charge,
            multiplicity: point.multiplicity,
            energyHartree: point.energy_hartree
          }));
      });
    }
    return buckets;
  }

  function stepFromRecord(record, source) {
    if (!isObject(record)) return null;
    var stepId = textOf(record.step_id);
    if (!stepId) return null;
    return {
      stepId: stepId,
      sourceState: textOf(record.source_state_id != null ? record.source_state_id : record.source),
      sinkState: textOf(record.sink_state_id != null ? record.sink_state_id : record.sink),
      tsId: textOf(record.ts_id),
      barrierForward: numberOrNull(record.barrier_forward),
      barrierReverse: numberOrNull(record.barrier_reverse),
      fidelity: textOf(record.fidelity),
      status: textOf(record.status),
      pathStrategy: textOf(record.path_strategy),
      coordinatePlan: normalizeCoordinatePlan(record.coordinate_plan),
      origin: source
    };
  }

  /**
   * Steps for display. The report projection is preferred because it carries the
   * published compact fields; the study snapshot supplies the coordinate plan
   * that the report projection drops. Falls back to study_json when the report
   * is unavailable.
   */
  function collectSteps(detail, report) {
    var study = detail && isObject(detail.studyJson) ? detail.studyJson : {};
    var studyById = Object.create(null);
    asArray(study.elementary_steps).forEach(function (step) {
      var normalized = stepFromRecord(step, "study.elementary_steps");
      if (normalized) studyById[normalized.stepId] = normalized;
    });
    if (isObject(study.network)) {
      asArray(study.network.edges).forEach(function (step) {
        var normalized = stepFromRecord(step, "study.network.edges");
        if (normalized && !studyById[normalized.stepId]) studyById[normalized.stepId] = normalized;
      });
    }

    var steps = [];
    var seen = Object.create(null);
    function push(step) {
      if (!step || seen[step.stepId]) return;
      seen[step.stepId] = true;
      steps.push(step);
    }

    if (report && report.reactionNetwork) {
      asArray(report.reactionNetwork.edges).forEach(function (edge) {
        var normalized = stepFromRecord(edge, "report.network");
        if (!normalized) return;
        var extra = studyById[normalized.stepId];
        if (extra) {
          normalized.coordinatePlan = extra.coordinatePlan;
          normalized.pathStrategy = normalized.pathStrategy || extra.pathStrategy;
          normalized.sourceState = normalized.sourceState || extra.sourceState;
          normalized.sinkState = normalized.sinkState || extra.sinkState;
        }
        push(normalized);
      });
    }
    if (!steps.length) {
      Object.keys(studyById).forEach(function (stepId) { push(studyById[stepId]); });
    }
    return steps;
  }

  function candidateFromRecord(record, kind, index) {
    if (!isObject(record)) return null;
    var id = kind === "step"
      ? (textOf(record.step_id) || "step-" + String(index + 1))
      : (textOf(record.route_id) || "route-" + String(index + 1));
    var plan = normalizeCoordinatePlan(record.coordinate_plan);
    if (!plan) return null;
    return {
      key: kind + ":" + id,
      kind: kind,
      id: id,
      sourceState: textOf(record.source_state_id),
      sinkState: textOf(record.sink_state_id),
      tsId: textOf(record.ts_id),
      strategy: validStrategy(record.path_strategy) || "guided-scan",
      fidelity: validFidelity(record.fidelity) || "s3",
      coordinatePlan: plan
    };
  }

  /**
   * Coordinate-plan candidates come from the study's OWN persisted plans:
   * elementary steps first (the current shape), then routes, then the network
   * edges. No coordinate is ever invented from a node/state.
   */
  function buildCandidates(detail) {
    var study = detail && isObject(detail.studyJson) ? detail.studyJson : {};
    var source = asArray(study.elementary_steps);
    var kind = "step";
    if (!source.length && isObject(study.network)) {
      source = asArray(study.network.edges);
      kind = "step";
    }
    var candidates = [];
    source.forEach(function (record, index) {
      var candidate = candidateFromRecord(record, kind, index);
      if (candidate) candidates.push(candidate);
    });
    if (!candidates.length) {
      asArray(study.routes).forEach(function (record, index) {
        var candidate = candidateFromRecord(record, "route", index);
        if (candidate) candidates.push(candidate);
      });
    }
    return candidates;
  }

  function selectedCandidates(candidates, selection) {
    return asArray(candidates).filter(function (candidate) {
      return selection && selection[candidate.key] === true;
    });
  }

  function overrideValue(overrides, id) {
    return overrides && has(overrides, id) ? overrides[id] : undefined;
  }

  /**
   * Flatten the selected candidates' coordinate plans into the plan body
   * coordinates. Ids are namespaced with the owning step/route id so the plan
   * keeps traceability without adding a non-contract `steps` field.
   */
  function buildPlanCoordinates(candidates, planState) {
    planState = planState || {};
    var coordinates = [];
    var usedIds = Object.create(null);
    selectedCandidates(candidates, planState.selection).forEach(function (candidate) {
      candidate.coordinatePlan.coordinates.forEach(function (coordinate, index) {
        var sourceId = textOf(coordinate.id) || "coord-" + String(index + 1);
        var id = candidate.id + "/" + sourceId;
        var base = id;
        var suffix = 2;
        while (usedIds[id]) {
          id = base + "#" + String(suffix);
          suffix += 1;
        }
        usedIds[id] = true;

        var roleOverride = overrideValue(planState.roleOverrides, id);
        var startOverride = overrideValue(planState.startOverrides, id);
        var endOverride = overrideValue(planState.endOverrides, id);
        var role = roleOverride === undefined ? coordinate.role : textOf(roleOverride);
        var start = startOverride === undefined ? coordinate.start : numericText(startOverride);
        var end = endOverride === undefined ? coordinate.end : numericText(endOverride);

        var item = {
          id: id,
          kind: coordinate.kind,
          atoms: coordinate.atoms.slice(),
          role: role,
          start: start,
          end: end
        };
        if (coordinate.forceConstant != null) item.force_constant = coordinate.forceConstant;
        coordinates.push(item);
      });
    });
    return coordinates;
  }

  function buildPlanBody(candidates, planState) {
    planState = planState || {};
    return {
      plan: {
        coordinates: buildPlanCoordinates(candidates, planState),
        points: intOrNull(planState.points),
        coupling: "synchronous",
        start_from: textOf(planState.startFrom)
      },
      strategy: textOf(planState.strategy),
      fidelity: textOf(planState.fidelity)
    };
  }

  /**
   * Client-side assembly validation mirroring the historical plan-request
   * requirements (`_validate_plan_request` + `CoordinateSpec`): coordinates for
   * every strategy except direct-ts, one of the three coordinate kinds with its
   * atom count, drive/freeze numeric requirements, and at least one drive. The
   * server remains authoritative; this only prevents an obviously invalid POST.
   */
  function validatePlanBody(body) {
    var errors = [];
    if (!isObject(body) || !isObject(body.plan)) {
      return { ok: false, errors: [{ code: "plan_invalid", field: "plan" }] };
    }
    var strategy = textOf(body.strategy);
    var fidelity = textOf(body.fidelity);
    if (STRATEGIES.indexOf(strategy) === -1) errors.push({ code: "strategy_invalid", field: "strategy" });
    if (FIDELITIES.indexOf(fidelity) === -1) errors.push({ code: "fidelity_invalid", field: "fidelity" });

    var plan = body.plan;
    var points = intOrNull(plan.points);
    if (points === null || points < MIN_POINTS) {
      errors.push({ code: "points_invalid", field: "points", vars: { min: MIN_POINTS } });
    }
    if (plan.coupling !== "synchronous") errors.push({ code: "coupling_invalid", field: "coupling" });
    if (START_FROM_VALUES.indexOf(textOf(plan.start_from)) === -1) {
      errors.push({ code: "start_from_invalid", field: "start_from" });
    }
    if (!Array.isArray(plan.coordinates)) {
      errors.push({ code: "coordinates_invalid", field: "coordinates" });
      return { ok: errors.length === 0, errors: errors };
    }

    var coordinates = plan.coordinates;
    if (!coordinates.length && strategy !== "direct-ts") {
      errors.push({ code: "coordinates_missing", field: "coordinates" });
    }

    var driveCount = 0;
    coordinates.forEach(function (coordinate, index) {
      if (!isObject(coordinate)) {
        errors.push({ code: "coordinate_invalid", field: "coordinates", vars: { index: index } });
        return;
      }
      var id = textOf(coordinate.id);
      if (!id) errors.push({ code: "coordinate_id_missing", field: "coordinates", vars: { index: index } });
      var kind = textOf(coordinate.kind);
      if (COORDINATE_KINDS.indexOf(kind) === -1) {
        errors.push({ code: "coordinate_kind_invalid", field: "coordinates", vars: { id: id || String(index) } });
      } else if (!Array.isArray(coordinate.atoms) || coordinate.atoms.length !== KIND_ATOMS[kind] ||
                 !coordinate.atoms.every(isIntegerAtom)) {
        errors.push({
          code: "coordinate_atoms_invalid",
          field: "coordinates",
          vars: { id: id || String(index), count: KIND_ATOMS[kind] }
        });
      }
      var role = textOf(coordinate.role);
      if (COORDINATE_ROLES.indexOf(role) === -1) {
        errors.push({ code: "coordinate_role_invalid", field: "coordinates", vars: { id: id || String(index) } });
        return;
      }
      if (role === "drive") {
        driveCount += 1;
        if (!isFiniteNumber(coordinate.start) || !isFiniteNumber(coordinate.end)) {
          errors.push({ code: "drive_start_end", field: "coordinates", vars: { id: id || String(index) } });
        }
      } else if (role === "freeze") {
        if (!isFiniteNumber(coordinate.start)) {
          errors.push({ code: "freeze_start", field: "coordinates", vars: { id: id || String(index) } });
        }
      }
    });
    if (strategy !== "direct-ts" && coordinates.length && !driveCount) {
      errors.push({ code: "drive_missing", field: "coordinates" });
    }
    return { ok: errors.length === 0, errors: errors };
  }

  function planKeyOf(body) {
    try {
      return JSON.stringify(body);
    } catch (error) {
      return "";
    }
  }

  /* Local, deterministic preview fingerprint (FNV-1a 32-bit). This is NOT a
   * server digest and is labelled as local in the UI; the authoritative
   * confirmation fingerprint is the server's plan_hash in the confirm receipt. */
  function localFingerprint(text) {
    var source = textOf(text);
    var hash = 0x811c9dc5;
    for (var i = 0; i < source.length; i += 1) {
      hash ^= source.charCodeAt(i);
      hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
    }
    var hex = hash.toString(16);
    while (hex.length < 8) hex = "0" + hex;
    return "fnv1a:" + hex;
  }

  function planStatusInfo(detail, receipt) {
    if (receipt && receipt.status) {
      return {
        state: "confirmed",
        confirmed: true,
        status: textOf(receipt.status),
        hash: textOf(receipt.planHash),
        labelKey: "next.mechplan.plan_status_confirmed"
      };
    }
    var status = detail ? textOf(detail.status) : "";
    var unified = detail ? textOf(detail.unifiedStatus) : "";
    if (status === "plan_confirmed" || unified === "PLAN_CONFIRMED") {
      return {
        state: "confirmed",
        confirmed: true,
        status: status || unified,
        hash: "",
        labelKey: "next.mechplan.plan_status_confirmed"
      };
    }
    if (status === "reaction_confirmed" || unified === "RESUMABLE") {
      return {
        state: "reaction_locked",
        confirmed: false,
        status: status || unified,
        hash: "",
        labelKey: "next.mechplan.plan_status_reaction_locked"
      };
    }
    return {
      state: "pending",
      confirmed: false,
      status: status || unified,
      hash: "",
      labelKey: status || unified ? "next.mechplan.plan_status_pending" : "next.mechplan.plan_status_unknown"
    };
  }

  function formatAtoms(atoms) {
    return asArray(atoms).map(function (atom) {
      return isIntegerAtom(atom) ? String(atom + 1) : "?";
    }).join(", ");
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;
    var cleanup = [];

    function requestFnFor() {
      if (typeof deps.request === "function") return deps.request;
      if (deps.client && deps.client.transport && typeof deps.client.transport.request === "function") {
        return function (path, opts) { return deps.client.transport.request(path, opts); };
      }
      if (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport &&
          typeof window.ACPClient.transport.request === "function") {
        return function (path, opts) { return window.ACPClient.transport.request(path, opts); };
      }
      return null;
    }

    var requestFn = requestFnFor();

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      root: null,
      ui: {},
      focusJobId: "__init__",
      projectId: "",
      list: { status: "idle", items: [], token: 0, error: null, jobId: "" },
      study: {
        status: "empty", id: "", detail: null, report: null, reaction: null,
        token: 0, error: null, notices: [], retry: null
      },
      planCandidates: [],
      plan: {
        selection: {},
        roleOverrides: {},
        startOverrides: {},
        endOverrides: {},
        strategy: "guided-scan",
        fidelity: "s3",
        points: String(DEFAULT_POINTS),
        startFrom: "reactant",
        startFromTouched: false,
        status: "idle",
        receipt: null,
        receiptKey: "",
        error: "",
        token: 0
      },
      busy: "none",
      retry: null,
      evaluation: null
    };

    function alive(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function listen(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return function () {};
      node.addEventListener(type, handler);
      return function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      };
    }

    function trackListener(node, type, handler) {
      var off = listen(node, type, handler);
      cleanup.push(off);
      return off;
    }

    /* ── DOM construction ────────────────────────────────────────── */

    function section(headingKey, className) {
      var node = el(doc, "section", className || "acp-mechplan-section");
      node.appendChild(i18nEl(doc, "h3", "acp-mechplan-heading", headingKey));
      return node;
    }

    function labeledField(labelKey, control) {
      var label = i18nEl(doc, "label", "acp-mechplan-label", labelKey);
      label.appendChild(control);
      return label;
    }

    function buildDom() {
      var root = el(doc, "div", "acp-mechplan");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-plan-state", "idle");
      root.setAttribute("data-acp-plan-status", "pending");
      root.setAttribute("data-acp-study", "");

      var head = el(doc, "div", "acp-mechplan-head");
      head.appendChild(i18nEl(doc, "h2", "acp-mechplan-title", "next.mechplan.title"));
      head.appendChild(i18nEl(doc, "p", "acp-mechplan-sub", "next.mechplan.subtitle"));
      root.appendChild(head);

      var body = el(doc, "div", "acp-mechplan-body");
      root.appendChild(body);

      /* 1 — study picker */
      var picker = section("next.mechplan.study_title");
      picker.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.study_hint"));

      var pickerRow = el(doc, "div", "acp-mechplan-row");
      var idLabel = i18nEl(doc, "label", "acp-mechplan-label", "next.mechplan.study_id");
      var studyInput = el(doc, "input", "acp-mechplan-input");
      studyInput.type = "text";
      studyInput.setAttribute("data-role", "study-id");
      studyInput.setAttribute("placeholder", t("next.mechplan.study_id_ph"));
      idLabel.appendChild(studyInput);
      state.ui.studyInput = studyInput;
      pickerRow.appendChild(idLabel);
      var loadBtn = el(doc, "button", "acp-mechplan-btn", t("next.mechplan.load_study"));
      loadBtn.type = "button";
      loadBtn.setAttribute("data-action", "load-study");
      loadBtn.setAttribute("data-i18n", "next.mechplan.load_study");
      trackListener(loadBtn, "click", onLoadStudyClick);
      pickerRow.appendChild(loadBtn);
      var listBtn = el(doc, "button", "acp-mechplan-btn", t("next.mechplan.list_studies"));
      listBtn.type = "button";
      listBtn.setAttribute("data-action", "list-studies");
      listBtn.setAttribute("data-i18n", "next.mechplan.list_studies");
      trackListener(listBtn, "click", onListStudiesClick);
      pickerRow.appendChild(listBtn);
      picker.appendChild(pickerRow);

      var selectLabel = i18nEl(doc, "label", "acp-mechplan-label", "next.mechplan.study_select");
      var studySelect = el(doc, "select", "acp-mechplan-select");
      studySelect.setAttribute("data-role", "study-select");
      studySelect.hidden = true;
      trackListener(studySelect, "change", onStudySelectChange);
      selectLabel.appendChild(studySelect);
      picker.appendChild(selectLabel);
      state.ui.studySelect = studySelect;

      var listStatus = el(doc, "p", "acp-mechplan-status");
      listStatus.setAttribute("data-role", "list-status");
      listStatus.setAttribute("role", "status");
      picker.appendChild(listStatus);
      state.ui.listStatus = listStatus;
      body.appendChild(picker);

      /* 2 — summary */
      var summary = section("next.mechplan.summary_title");
      summary.hidden = true;
      summary.setAttribute("data-role", "summary-section");
      var summaryGrid = el(doc, "div", "acp-mechplan-summary");
      var summaryFields = [
        ["summary-id", "next.mechplan.summary_id"],
        ["summary-job", "next.mechplan.summary_job"],
        ["summary-status", "next.mechplan.summary_status"],
        ["summary-unified", "next.mechplan.summary_unified"],
        ["summary-plan", "next.mechplan.summary_plan"],
        ["summary-updated", "next.mechplan.summary_updated"],
        ["summary-decisions", "next.mechplan.summary_decisions"]
      ];
      state.ui.summaryFields = {};
      summaryFields.forEach(function (entry) {
        var row = el(doc, "div", "acp-mechplan-summary-row");
        row.appendChild(i18nEl(doc, "span", "acp-mechplan-summary-label", entry[1]));
        var value = el(doc, "strong", "acp-mechplan-summary-value", "—");
        value.setAttribute("data-role", entry[0]);
        row.appendChild(value);
        summaryGrid.appendChild(row);
        state.ui.summaryFields[entry[0]] = value;
      });
      summary.appendChild(summaryGrid);
      var roleChips = el(doc, "div", "acp-mechplan-chips");
      state.ui.roleCounts = {};
      [
        ["reactant", "next.mechplan.role_reactant"],
        ["product", "next.mechplan.role_product"],
        ["intermediate", "next.mechplan.role_intermediate"],
        ["ts", "next.mechplan.role_ts"],
        ["unknown", "next.mechplan.role_unknown"]
      ].forEach(function (entry) {
        var chip = el(doc, "span", "acp-mechplan-chip");
        chip.appendChild(i18nEl(doc, "span", "acp-mechplan-chip-label", entry[1]));
        var count = el(doc, "strong", "acp-mechplan-chip-value", "0");
        count.setAttribute("data-role", "role-count-" + entry[0]);
        chip.appendChild(count);
        roleChips.appendChild(chip);
        state.ui.roleCounts[entry[0]] = count;
      });
      summary.appendChild(roleChips);
      var notices = el(doc, "ul", "acp-mechplan-notices");
      notices.setAttribute("data-role", "study-notices");
      notices.hidden = true;
      summary.appendChild(notices);
      state.ui.studyNotices = notices;
      body.appendChild(summary);
      state.ui.summarySection = summary;

      /* 3 — steps + roles */
      var stepsSection = section("next.mechplan.steps_title");
      stepsSection.hidden = true;
      stepsSection.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.steps_hint"));
      var stepsEmpty = i18nEl(doc, "p", "acp-mechplan-empty", "next.mechplan.steps_empty");
      stepsEmpty.setAttribute("data-role", "steps-empty");
      stepsSection.appendChild(stepsEmpty);
      var tableWrap = el(doc, "div", "acp-mechplan-table-wrap");
      var stepsTable = el(doc, "table", "acp-mechplan-table");
      stepsTable.setAttribute("data-role", "steps-table");
      var stepsHead = el(doc, "thead");
      var stepsHeadRow = el(doc, "tr");
      [
        "next.mechplan.step_col",
        "next.mechplan.step_source",
        "next.mechplan.step_sink",
        "next.mechplan.step_ts",
        "next.mechplan.step_barrier_f",
        "next.mechplan.step_barrier_r",
        "next.mechplan.step_fidelity",
        "next.mechplan.step_strategy",
        "next.mechplan.step_status"
      ].forEach(function (key) {
        var th = i18nEl(doc, "th", "acp-mechplan-th", key);
        stepsHeadRow.appendChild(th);
      });
      stepsHead.appendChild(stepsHeadRow);
      stepsTable.appendChild(stepsHead);
      var stepsBody = el(doc, "tbody");
      stepsBody.setAttribute("data-role", "steps-body");
      stepsTable.appendChild(stepsBody);
      tableWrap.appendChild(stepsTable);
      stepsSection.appendChild(tableWrap);
      var rolesTitle = i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.roles_title");
      stepsSection.appendChild(rolesTitle);
      var roleLists = el(doc, "div", "acp-mechplan-roles");
      state.ui.roleLists = {};
      [
        ["reactant", "next.mechplan.role_reactant"],
        ["product", "next.mechplan.role_product"],
        ["intermediate", "next.mechplan.role_intermediate"],
        ["ts", "next.mechplan.role_ts"],
        ["unknown", "next.mechplan.role_unknown"]
      ].forEach(function (entry) {
        var group = el(doc, "div", "acp-mechplan-role-group");
        group.appendChild(i18nEl(doc, "h5", "acp-mechplan-role-heading", entry[1]));
        var list = el(doc, "ul", "acp-mechplan-role-list");
        list.setAttribute("data-role", "role-list-" + entry[0]);
        group.appendChild(list);
        roleLists.appendChild(group);
        state.ui.roleLists[entry[0]] = list;
      });
      stepsSection.appendChild(roleLists);
      body.appendChild(stepsSection);
      state.ui.stepsSection = stepsSection;
      state.ui.stepsEmpty = stepsEmpty;
      state.ui.stepsTableWrap = tableWrap;
      state.ui.stepsBody = stepsBody;

      /* 4 — reaction definition */
      var reactionSection = section("next.mechplan.reaction_title");
      reactionSection.hidden = true;
      reactionSection.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.reaction_hint"));
      var reactionEmpty = i18nEl(doc, "p", "acp-mechplan-empty", "next.mechplan.reaction_unavailable");
      reactionEmpty.setAttribute("data-role", "reaction-empty");
      reactionSection.appendChild(reactionEmpty);
      var reactionBody = el(doc, "div", "acp-mechplan-reaction");
      reactionBody.setAttribute("data-role", "reaction-body");
      reactionBody.hidden = true;
      var reactionGrid = el(doc, "div", "acp-mechplan-reaction-grid");
      var reactantsGroup = el(doc, "div", "acp-mechplan-reaction-group");
      reactantsGroup.appendChild(i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.reaction_reactants"));
      var reactantsList = el(doc, "ul", "acp-mechplan-species");
      reactantsList.setAttribute("data-role", "reaction-reactants");
      reactantsGroup.appendChild(reactantsList);
      var productsGroup = el(doc, "div", "acp-mechplan-reaction-group");
      productsGroup.appendChild(i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.reaction_products"));
      var productsList = el(doc, "ul", "acp-mechplan-species");
      productsList.setAttribute("data-role", "reaction-products");
      productsGroup.appendChild(productsList);
      reactionGrid.appendChild(reactantsGroup);
      reactionGrid.appendChild(productsGroup);
      reactionBody.appendChild(reactionGrid);
      var reactionMeta = el(doc, "div", "acp-mechplan-meta");
      var mappingText = el(doc, "p", "acp-mechplan-status", "");
      mappingText.setAttribute("data-role", "reaction-mapping");
      var hashText = el(doc, "p", "acp-mechplan-mono", "");
      hashText.setAttribute("data-role", "reaction-hash");
      var confirmedByText = el(doc, "p", "acp-mechplan-status", "");
      confirmedByText.setAttribute("data-role", "reaction-confirmed-by");
      var reactionStatusText = el(doc, "p", "acp-mechplan-status", "");
      reactionStatusText.setAttribute("data-role", "reaction-status");
      reactionMeta.appendChild(mappingText);
      reactionMeta.appendChild(hashText);
      reactionMeta.appendChild(confirmedByText);
      reactionMeta.appendChild(reactionStatusText);
      reactionBody.appendChild(reactionMeta);
      reactionSection.appendChild(reactionBody);
      body.appendChild(reactionSection);
      state.ui.reactionSection = reactionSection;
      state.ui.reactionEmpty = reactionEmpty;
      state.ui.reactionBody = reactionBody;
      state.ui.reactionReactants = reactantsList;
      state.ui.reactionProducts = productsList;
      state.ui.reactionMapping = mappingText;
      state.ui.reactionHash = hashText;
      state.ui.reactionConfirmedBy = confirmedByText;
      state.ui.reactionStatus = reactionStatusText;

      /* 5 — provenance */
      var provenanceSection = section("next.mechplan.provenance_title");
      provenanceSection.hidden = true;
      provenanceSection.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.provenance_hint"));
      var provenanceSummary = el(doc, "p", "acp-mechplan-status", "");
      provenanceSummary.setAttribute("data-role", "provenance-summary");
      provenanceSection.appendChild(provenanceSummary);
      var provenanceEmpty = i18nEl(doc, "p", "acp-mechplan-empty", "next.mechplan.provenance_empty");
      provenanceEmpty.setAttribute("data-role", "provenance-empty");
      provenanceSection.appendChild(provenanceEmpty);
      var provenancePre = el(doc, "pre", "acp-mechplan-pre");
      provenancePre.setAttribute("data-role", "provenance-json");
      provenanceSection.appendChild(provenancePre);
      var provenanceNotes = el(doc, "ul", "acp-mechplan-notices");
      provenanceNotes.setAttribute("data-role", "provenance-notes");
      provenanceNotes.hidden = true;
      provenanceSection.appendChild(provenanceNotes);
      body.appendChild(provenanceSection);
      state.ui.provenanceSection = provenanceSection;
      state.ui.provenanceSummary = provenanceSummary;
      state.ui.provenanceEmpty = provenanceEmpty;
      state.ui.provenancePre = provenancePre;
      state.ui.provenanceNotes = provenanceNotes;

      /* 6 — plan */
      var planSection = section("next.mechplan.plan_title");
      planSection.hidden = true;
      planSection.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.plan_hint"));

      var controls = el(doc, "div", "acp-mechplan-grid");
      var strategySelect = el(doc, "select", "acp-mechplan-select");
      strategySelect.setAttribute("data-role", "strategy");
      [
        ["guided-scan", "next.mechplan.strategy_guided"],
        ["rph-reverse", "next.mechplan.strategy_rph"],
        ["direct-ts", "next.mechplan.strategy_direct"]
      ].forEach(function (entry) {
        var option = el(doc, "option", "", t(entry[1]));
        option.value = entry[0];
        strategySelect.appendChild(option);
      });
      trackListener(strategySelect, "change", onStrategyChange);
      controls.appendChild(labeledField("next.mechplan.plan_strategy", strategySelect));

      var fidelitySelect = el(doc, "select", "acp-mechplan-select");
      fidelitySelect.setAttribute("data-role", "fidelity");
      [["s3", "S3"], ["s4", "S4"]].forEach(function (entry) {
        var option = el(doc, "option", "", entry[1]);
        option.value = entry[0];
        fidelitySelect.appendChild(option);
      });
      trackListener(fidelitySelect, "change", onFidelityChange);
      controls.appendChild(labeledField("next.mechplan.plan_fidelity", fidelitySelect));

      var pointsInput = el(doc, "input", "acp-mechplan-input");
      pointsInput.type = "text";
      pointsInput.setAttribute("inputmode", "numeric");
      pointsInput.setAttribute("data-role", "points");
      trackListener(pointsInput, "input", onPointsChange);
      trackListener(pointsInput, "change", onPointsChange);
      controls.appendChild(labeledField("next.mechplan.plan_points", pointsInput));

      var startFromSelect = el(doc, "select", "acp-mechplan-select");
      startFromSelect.setAttribute("data-role", "start-from");
      [
        ["reactant", "next.mechplan.start_from_reactant"],
        ["product", "next.mechplan.start_from_product"],
        ["custom", "next.mechplan.start_from_custom"]
      ].forEach(function (entry) {
        var option = el(doc, "option", "", t(entry[1]));
        option.value = entry[0];
        startFromSelect.appendChild(option);
      });
      trackListener(startFromSelect, "change", onStartFromChange);
      controls.appendChild(labeledField("next.mechplan.plan_start_from", startFromSelect));
      planSection.appendChild(controls);
      state.ui.strategySelect = strategySelect;
      state.ui.fidelitySelect = fidelitySelect;
      state.ui.pointsInput = pointsInput;
      state.ui.startFromSelect = startFromSelect;

      planSection.appendChild(i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.candidates_title"));
      var candidates = el(doc, "div", "acp-mechplan-candidates");
      candidates.setAttribute("data-role", "candidates");
      planSection.appendChild(candidates);
      state.ui.candidates = candidates;

      planSection.appendChild(i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.coords_title"));
      var coordsTable = el(doc, "table", "acp-mechplan-table");
      coordsTable.setAttribute("data-role", "coords-table");
      var coordsHead = el(doc, "thead");
      var coordsHeadRow = el(doc, "tr");
      [
        "next.mechplan.coords_col_id",
        "next.mechplan.coords_col_kind",
        "next.mechplan.coords_col_atoms",
        "next.mechplan.coords_col_start",
        "next.mechplan.coords_col_end",
        "next.mechplan.coords_col_role"
      ].forEach(function (key) {
        coordsHeadRow.appendChild(i18nEl(doc, "th", "acp-mechplan-th", key));
      });
      coordsHead.appendChild(coordsHeadRow);
      coordsTable.appendChild(coordsHead);
      var coordsBody = el(doc, "tbody");
      coordsBody.setAttribute("data-role", "coords-body");
      coordsTable.appendChild(coordsBody);
      planSection.appendChild(coordsTable);
      state.ui.coordsBody = coordsBody;

      planSection.appendChild(i18nEl(doc, "h4", "acp-mechplan-subheading", "next.mechplan.preview_title"));
      var planPreview = el(doc, "pre", "acp-mechplan-pre");
      planPreview.setAttribute("data-role", "plan-preview");
      planSection.appendChild(planPreview);
      var planFingerprint = el(doc, "p", "acp-mechplan-mono", "");
      planFingerprint.setAttribute("data-role", "plan-fingerprint");
      planSection.appendChild(planFingerprint);
      planSection.appendChild(i18nEl(doc, "p", "acp-mechplan-hint", "next.mechplan.preview_note"));
      var planErrors = el(doc, "ul", "acp-mechplan-errors");
      planErrors.setAttribute("data-role", "plan-errors");
      planSection.appendChild(planErrors);
      var confirmStatus = el(doc, "p", "acp-mechplan-status", "");
      confirmStatus.setAttribute("data-role", "confirm-status");
      confirmStatus.setAttribute("role", "status");
      planSection.appendChild(confirmStatus);
      var confirmBtn = el(doc, "button", "acp-mechplan-btn acp-mechplan-btn--primary", t("next.mechplan.confirm"));
      confirmBtn.type = "button";
      confirmBtn.setAttribute("data-action", "confirm-plan");
      confirmBtn.setAttribute("data-i18n", "next.mechplan.confirm");
      confirmBtn.disabled = true;
      trackListener(confirmBtn, "click", onConfirmClick);
      planSection.appendChild(confirmBtn);

      var receipt = el(doc, "div", "acp-mechplan-receipt");
      receipt.setAttribute("data-role", "plan-receipt");
      receipt.setAttribute("role", "status");
      receipt.hidden = true;
      receipt.appendChild(i18nEl(doc, "h4", "acp-mechplan-receipt-title", "next.mechplan.receipt_title"));
      var receiptBody = el(doc, "p", "acp-mechplan-receipt-body", "");
      receiptBody.setAttribute("data-role", "receipt-body");
      var receiptHash = el(doc, "p", "acp-mechplan-mono", "");
      receiptHash.setAttribute("data-role", "receipt-hash");
      receiptHash.hidden = true;
      receipt.appendChild(receiptBody);
      receipt.appendChild(receiptHash);
      planSection.appendChild(receipt);
      body.appendChild(planSection);
      state.ui.planSection = planSection;
      state.ui.planPreview = planPreview;
      state.ui.planFingerprint = planFingerprint;
      state.ui.planErrors = planErrors;
      state.ui.confirmStatus = confirmStatus;
      state.ui.confirmBtn = confirmBtn;
      state.ui.receipt = receipt;
      state.ui.receiptBody = receiptBody;
      state.ui.receiptHash = receiptHash;

      var emptyHint = el(doc, "div", "acp-mechplan-empty-state");
      emptyHint.setAttribute("data-role", "empty-state");
      emptyHint.appendChild(i18nEl(doc, "p", "acp-mechplan-empty-title", "next.mechplan.empty_title"));
      emptyHint.appendChild(i18nEl(doc, "p", "acp-mechplan-empty", "next.mechplan.empty_hint"));
      root.appendChild(emptyHint);
      state.ui.emptyState = emptyHint;

      var overlay = el(doc, "div", "acp-mechplan-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = true;
      var box = el(doc, "div", "acp-mechplan-box");
      var overlayMessage = el(doc, "p", "acp-mechplan-message", "");
      overlayMessage.setAttribute("data-role", "overlay-message");
      overlayMessage.setAttribute("aria-live", "polite");
      var overlayDetail = el(doc, "p", "acp-mechplan-detail", "");
      overlayDetail.setAttribute("data-role", "overlay-detail");
      overlayDetail.hidden = true;
      var retryBtn = el(doc, "button", "acp-mechplan-btn", t("next.mechplan.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.mechplan.retry");
      retryBtn.hidden = true;
      trackListener(retryBtn, "click", onRetryClick);
      box.appendChild(overlayMessage);
      box.appendChild(overlayDetail);
      box.appendChild(retryBtn);
      overlay.appendChild(box);
      root.appendChild(overlay);
      state.ui.overlay = overlay;
      state.ui.overlayMessage = overlayMessage;
      state.ui.overlayDetail = overlayDetail;
      state.ui.retryBtn = retryBtn;

      state.root = root;
      state.host.appendChild(root);
    }

    /* ── request helpers ─────────────────────────────────────────── */

    function clientMissingError() {
      return new Error(t("next.mechplan.err.client_missing"));
    }

    function safeGet(path) {
      if (!requestFn) {
        return Promise.resolve({ ok: false, error: clientMissingError() });
      }
      return Promise.resolve(requestFn(path, { method: "GET" })).then(function (value) {
        return { ok: true, value: value };
      }).catch(function (error) {
        return { ok: false, error: error };
      });
    }

    /* ── study list ─────────────────────────────────────────────── */

    function resetStudyState() {
      state.study.status = "empty";
      state.study.id = "";
      state.study.detail = null;
      state.study.report = null;
      state.study.reaction = null;
      state.study.token += 1;
      state.study.error = null;
      state.study.notices = [];
      state.study.retry = null;
      state.planCandidates = [];
      state.plan.selection = {};
      state.plan.roleOverrides = {};
      state.plan.startOverrides = {};
      state.plan.endOverrides = {};
      state.plan.status = "idle";
      state.plan.receipt = null;
      state.plan.receiptKey = "";
      state.plan.error = "";
      state.plan.token += 1;
      if (state.busy === "confirm") state.busy = "none";
    }

    function loadStudyList(options) {
      options = options || {};
      var jobId = options.jobId != null ? String(options.jobId) : textOf(state.focusJobId === "__init__" ? "" : state.focusJobId);
      var token = state.list.token + 1;
      state.list.token = token;
      state.list.status = "loading";
      state.list.error = null;
      state.list.jobId = jobId;
      state.retry = null;
      render();

      if (!requestFn) {
        state.list.status = "error";
        state.list.error = t("next.mechplan.err.client_missing");
        state.list.items = [];
        state.list.token += 1;
        state.retry = null;
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }
      var gen = state.generation;
      return Promise.resolve(requestFn(STUDIES_PATH + listQuery(jobId), { method: "GET" }))
        .then(function (payload) {
          if (!alive(gen) || state.list.token !== token) return { ok: false, stale: true };
          state.list.items = normalizeStudyList(payload);
          state.list.status = "ready";
          state.retry = null;
          if (options.autoLoad && state.list.items.length) {
            loadStudy(state.list.items[0].id);
          }
          render();
          return { ok: true, items: state.list.items };
        })
        .catch(function (error) {
          if (!alive(gen) || state.list.token !== token) return { ok: false, stale: true };
          state.list.items = [];
          state.list.status = "error";
          state.list.error = describeError(error);
          state.retry = function () { loadStudyList({ jobId: jobId, autoLoad: true }); };
          render();
          return { ok: false, error: state.list.error };
        });
    }

    /* ── study detail + report + reaction ───────────────────────── */

    function resetPlanForStudy(detail) {
      state.planCandidates = buildCandidates(detail);
      var candidates = state.planCandidates;
      var selection = {};
      candidates.forEach(function (candidate) { selection[candidate.key] = true; });
      state.plan.selection = selection;
      state.plan.roleOverrides = {};
      state.plan.startOverrides = {};
      state.plan.endOverrides = {};

      var strategy = candidates.length ? candidates[0].strategy : "guided-scan";
      var fidelity = candidates.length ? candidates[0].fidelity : "s3";
      state.plan.strategy = STRATEGIES.indexOf(strategy) === -1 ? "guided-scan" : strategy;
      state.plan.fidelity = FIDELITIES.indexOf(fidelity) === -1 ? "s3" : fidelity;
      state.plan.points = String(defaultPoints(candidates));
      state.plan.startFrom = state.plan.strategy === "rph-reverse" ? "product" : "reactant";
      state.plan.startFromTouched = false;
      state.plan.status = "idle";
      state.plan.receipt = null;
      state.plan.receiptKey = "";
      state.plan.error = "";
      state.plan.token += 1;
    }

    function defaultPoints(candidates) {
      var max = 0;
      asArray(candidates).forEach(function (candidate) {
        var points = candidate.coordinatePlan ? candidate.coordinatePlan.points : null;
        if (isFiniteNumber(points) && points >= MIN_POINTS) max = Math.max(max, points);
      });
      return max || DEFAULT_POINTS;
    }

    function loadStudy(rawId) {
      var id = textOf(rawId).trim();
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (!id) {
        state.study.status = "error";
        state.study.error = t("next.mechplan.err.study_id_required");
        state.study.id = "";
        state.study.retry = null;
        state.busy = "none";
        render();
        return Promise.resolve({ ok: false, reason: "id" });
      }
      if (!requestFn) {
        state.study.status = "error";
        state.study.error = t("next.mechplan.err.client_missing");
        state.study.id = id;
        state.study.retry = function () { loadStudy(id); };
        state.busy = "none";
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }

      var token = state.study.token + 1;
      var gen = state.generation;
      state.study.token = token;
      state.study.status = "loading";
      state.study.id = id;
      state.study.detail = null;
      state.study.report = null;
      state.study.reaction = null;
      state.study.error = null;
      state.study.notices = [];
      state.study.retry = null;
      state.plan.token += 1;
      state.plan.status = "idle";
      state.plan.receipt = null;
      state.plan.receiptKey = "";
      state.plan.error = "";
      state.busy = "load";
      render();

      var detailPath = studyPath(id);
      return Promise.resolve(requestFn(detailPath, { method: "GET" }))
        .then(function (payload) {
          if (!alive(gen) || state.study.token !== token) return { ok: false, stale: true };
          var detail = normalizeDetail(payload);
          return Promise.all([
            safeGet(detailPath + "/report"),
            safeGet(detailPath + "/reaction")
          ]).then(function (results) {
            if (!alive(gen) || state.study.token !== token) return { ok: false, stale: true };
            var report = results[0];
            var reaction = results[1];
            state.study.detail = detail;
            state.study.report = report.ok ? normalizeReport(report.value) : null;
            state.study.reaction = reaction.ok ? normalizeReaction(reaction.value) : null;
            state.study.notices = [];
            if (!report.ok) {
              state.study.notices.push(t("next.mechplan.report_unavailable", {
                message: describeError(report.error)
              }));
            }
            if (!reaction.ok) {
              state.study.notices.push(t("next.mechplan.reaction_unavailable_detail", {
                message: describeError(reaction.error)
              }));
            }
            state.study.status = "ready";
            state.study.error = null;
            state.study.retry = null;
            state.busy = "none";
            resetPlanForStudy(detail);
            render();
            return { ok: true, detail: detail };
          });
        })
        .catch(function (error) {
          if (!alive(gen) || state.study.token !== token) return { ok: false, stale: true };
          state.study.status = "error";
          state.study.error = describeError(error);
          state.study.retry = function () { loadStudy(id); };
          state.busy = "none";
          render();
          return { ok: false, error: state.study.error };
        });
    }

    /* ── plan assembly / confirmation ────────────────────────────── */

    function evaluatePlan() {
      var body = buildPlanBody(state.planCandidates, state.plan);
      var check = validatePlanBody(body);
      return {
        body: body,
        check: check,
        key: planKeyOf(body),
        selected: selectedCandidates(state.planCandidates, state.plan.selection)
      };
    }

    function touchPlan() {
      if (state.busy === "confirm") return;
      state.plan.token += 1;
      if (state.plan.status !== "confirming") state.plan.status = "idle";
      state.plan.receipt = null;
      state.plan.receiptKey = "";
      state.plan.error = "";
    }

    function currentPlanStatusInfo() {
      var detail = state.study.detail;
      return planStatusInfo(detail, state.plan.receipt);
    }

    function canConfirm(evaluation) {
      evaluation = evaluation || evaluatePlan();
      if (!state.mounted || state.disposed) return false;
      if (!requestFn) return false;
      if (state.study.status !== "ready" || !state.study.id) return false;
      if (state.busy !== "none") return false;
      if (!evaluation.check.ok) return false;
      if (state.plan.status === "confirming") return false;
      if (state.plan.receiptKey && state.plan.receiptKey === evaluation.key) return false;
      return true;
    }

    function confirmPlan() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      var evaluation = evaluatePlan();
      if (!evaluation.check.ok) {
        state.plan.status = "blocked";
        state.plan.error = "";
        render();
        return Promise.resolve({ ok: false, reason: "invalid" });
      }
      if (!canConfirm(evaluation)) {
        render();
        return Promise.resolve({ ok: false, reason: "blocked" });
      }
      var studyId = state.study.id;
      var token = state.plan.token + 1;
      var gen = state.generation;
      state.plan.token = token;
      state.plan.status = "confirming";
      state.plan.error = "";
      state.busy = "confirm";
      render();

      var body = evaluation.body;
      var bodyText = JSON.stringify(body);
      return Promise.resolve(requestFn(studyPath(studyId) + "/mechanism/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: bodyText
      })).then(function (payload) {
        if (!alive(gen) || state.plan.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        if (!isObject(payload) || typeof payload.status !== "string") {
          state.plan.status = "error";
          state.plan.error = t("next.mechplan.invalid_response");
          render();
          return { ok: false, error: state.plan.error };
        }
        state.plan.status = "confirmed";
        state.plan.receipt = {
          status: textOf(payload.status),
          planHash: textOf(payload.plan_hash)
        };
        state.plan.receiptKey = evaluation.key;
        state.plan.error = "";
        render();
        return { ok: true, receipt: state.plan.receipt };
      }).catch(function (error) {
        if (!alive(gen) || state.plan.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        var status = errorStatus(error);
        if (status === 410) {
          state.plan.status = "error";
          state.plan.error = t("next.mechplan.confirm_retired", { detail: describeError(error) });
        } else if (status === 409) {
          state.plan.status = "error";
          state.plan.error = t("next.mechplan.confirm_not_locked", { detail: describeError(error) });
        } else {
          state.plan.status = "error";
          state.plan.error = describeError(error);
        }
        render();
        return { ok: false, error: state.plan.error };
      });
    }

    /* ── events ─────────────────────────────────────────────────── */

    function onLoadStudyClick() {
      if (!state.mounted || state.disposed) return;
      loadStudy(state.ui.studyInput && state.ui.studyInput.value);
    }

    function onListStudiesClick() {
      if (!state.mounted || state.disposed) return;
      loadStudyList({ jobId: "", autoLoad: false });
    }

    function onStudySelectChange() {
      if (!state.mounted || state.disposed) return;
      var id = state.ui.studySelect ? state.ui.studySelect.value : "";
      if (id) loadStudy(id);
    }

    function onStrategyChange() {
      if (!state.mounted || state.disposed) return;
      state.plan.strategy = textOf(state.ui.strategySelect && state.ui.strategySelect.value);
      if (!state.plan.startFromTouched) {
        state.plan.startFrom = state.plan.strategy === "rph-reverse" ? "product" : "reactant";
      }
      touchPlan();
      render();
    }

    function onFidelityChange() {
      if (!state.mounted || state.disposed) return;
      state.plan.fidelity = textOf(state.ui.fidelitySelect && state.ui.fidelitySelect.value);
      touchPlan();
      render();
    }

    function onPointsChange() {
      if (!state.mounted || state.disposed) return;
      state.plan.points = textOf(state.ui.pointsInput && state.ui.pointsInput.value).trim();
      touchPlan();
      render();
    }

    function onStartFromChange() {
      if (!state.mounted || state.disposed) return;
      state.plan.startFrom = textOf(state.ui.startFromSelect && state.ui.startFromSelect.value);
      state.plan.startFromTouched = true;
      touchPlan();
      render();
    }

    function onConfirmClick() {
      if (!state.mounted || state.disposed) return;
      var evaluation = evaluatePlan();
      if (!evaluation.check.ok) {
        state.plan.status = "blocked";
        state.plan.error = "";
        render();
        return;
      }
      if (!canConfirm(evaluation)) return;
      confirmPlan();
    }

    function onRetryClick() {
      if (!state.mounted || state.disposed) return;
      if (state.study.status === "error" && typeof state.study.retry === "function") {
        state.study.retry();
        return;
      }
      if (typeof state.retry === "function") state.retry();
    }

    /* ── rendering ─────────────────────────────────────────────── */

    function setText(node, value) {
      if (node) node.textContent = value == null ? "" : String(value);
    }

    function renderStateChrome() {
      if (!state.root) return;
      var rootState = state.study.status;
      if (rootState === "empty" && state.list.status === "loading") rootState = "loading";
      else if (rootState === "empty" && state.list.status === "error") rootState = "error";
      state.root.setAttribute("data-acp-module-state", rootState);
      state.root.setAttribute("data-acp-busy", state.busy);
      state.root.setAttribute("data-acp-plan-state", state.plan.status);
      state.root.setAttribute("data-acp-plan-status", currentPlanStatusInfo().state);
      state.root.setAttribute("data-acp-study", state.study.id || "");
    }

    function renderList() {
      if (!state.ui.studySelect) return;
      var select = state.ui.studySelect;
      clearChildren(select);
      var placeholder = el(doc, "option", "", t("next.mechplan.study_select_ph"));
      placeholder.value = "";
      select.appendChild(placeholder);
      state.list.items.forEach(function (item) {
        var option = el(doc, "option", "", item.id + (item.status ? " · " + item.status : ""));
        option.value = item.id;
        select.appendChild(option);
      });
      select.hidden = state.list.items.length === 0;
      if (state.list.items.length) {
        var known = state.list.items.some(function (item) { return item.id === state.study.id; });
        select.value = known ? state.study.id : "";
      } else {
        select.value = "";
      }

      var text = "";
      if (state.list.status === "loading") text = t("next.mechplan.list_loading");
      else if (state.list.status === "error") text = t("next.mechplan.list_error", { message: state.list.error || "" });
      else if (state.list.status === "ready") {
        if (!state.list.items.length) text = t("next.mechplan.list_empty");
        else {
          text = t("next.mechplan.list_ready", {
            count: state.list.items.length,
            scope: state.list.jobId
              ? t("next.mechplan.list_scope_job", { jobId: state.list.jobId })
              : ""
          });
        }
      }
      setText(state.ui.listStatus, text);
    }

    function renderSummary() {
      var detail = state.study.detail;
      var buckets = collectRoleBuckets(detail, state.study.report);
      Object.keys(state.ui.roleCounts || {}).forEach(function (bucket) {
        setText(state.ui.roleCounts[bucket], String((buckets[bucket] || []).length));
      });
      var fields = state.ui.summaryFields || {};
      var info = currentPlanStatusInfo();
      setText(fields["summary-id"], detail ? detail.id : "—");
      setText(fields["summary-job"], detail && detail.jobId ? detail.jobId : "—");
      setText(fields["summary-status"], detail && detail.status ? detail.status : "—");
      setText(fields["summary-unified"], detail && detail.unifiedStatus ? detail.unifiedStatus : "—");
      setText(fields["summary-updated"], detail && detail.updatedAt ? detail.updatedAt : "—");
      setText(fields["summary-decisions"], detail ? String(detail.decisions.length) : "0");
      if (fields["summary-plan"]) {
        var label = info.labelKey === "next.mechplan.plan_status_unknown"
          ? t(info.labelKey)
          : t(info.labelKey, { status: info.status || "—" });
        fields["summary-plan"].textContent = label;
        fields["summary-plan"].setAttribute("data-acp-plan-status", info.state);
      }

      var notices = state.ui.studyNotices;
      if (notices) {
        clearChildren(notices);
        var list = state.study.notices || [];
        list.forEach(function (line) {
          var item = el(doc, "li", "acp-mechplan-notice", line);
          item.setAttribute("data-notice", "1");
          notices.appendChild(item);
        });
        notices.hidden = list.length === 0;
      }
    }

    function renderRoleLists() {
      var buckets = collectRoleBuckets(state.study.detail, state.study.report);
      Object.keys(state.ui.roleLists || {}).forEach(function (bucket) {
        var list = state.ui.roleLists[bucket];
        clearChildren(list);
        var items = buckets[bucket] || [];
        if (!items.length) {
          var empty = el(doc, "li", "acp-mechplan-role-empty", t("next.mechplan.role_empty"));
          empty.setAttribute("data-role", "role-item-empty-" + bucket);
          list.appendChild(empty);
          return;
        }
        items.forEach(function (item) {
          var li = el(doc, "li", "acp-mechplan-role-item");
          li.setAttribute("data-role", "role-item-" + bucket);
          li.setAttribute("data-state-id", item.id);
          var main = t("next.mechplan.role_item", { id: item.id, role: item.role || "—" });
          if (item.energyHartree != null) {
            main += " · " + t("next.mechplan.role_energy", {
              energy: formatNumber(item.energyHartree, 4)
            });
          }
          if (item.charge != null) main += " · q=" + String(item.charge);
          if (item.multiplicity != null) main += " · 2S+1=" + String(item.multiplicity);
          li.textContent = main;
          list.appendChild(li);
        });
      });
    }

    function renderSteps() {
      var steps = collectSteps(state.study.detail, state.study.report);
      var body = state.ui.stepsBody;
      if (!body) return;
      clearChildren(body);
      steps.forEach(function (step) {
        var row = el(doc, "tr", "acp-mechplan-step-row");
        row.setAttribute("data-step-id", step.stepId);
        [
          step.stepId,
          step.sourceState || "—",
          step.sinkState || "—",
          step.tsId || "—",
          step.barrierForward != null ? formatNumber(step.barrierForward, 2) : "—",
          step.barrierReverse != null ? formatNumber(step.barrierReverse, 2) : "—",
          step.fidelity || "—",
          step.pathStrategy || "—",
          step.status || "—"
        ].forEach(function (value) {
          row.appendChild(el(doc, "td", "acp-mechplan-td", String(value)));
        });
        body.appendChild(row);
      });
      if (state.ui.stepsTableWrap) state.ui.stepsTableWrap.hidden = steps.length === 0;
      if (state.ui.stepsEmpty) state.ui.stepsEmpty.hidden = steps.length !== 0;
    }

    function speciesText(item) {
      return t("next.mechplan.reaction_item", {
        smiles: textOf(item && item.smiles) || "—",
        charge: item && item.charge != null ? item.charge : "—",
        multiplicity: item && item.multiplicity != null ? item.multiplicity : "—"
      });
    }

    function renderReaction() {
      var reaction = state.study.reaction;
      var available = !!(reaction && reaction.available);
      if (state.ui.reactionBody) state.ui.reactionBody.hidden = !available;
      if (state.ui.reactionEmpty) state.ui.reactionEmpty.hidden = available;
      if (!available) {
        setText(state.ui.reactionMapping, "");
        setText(state.ui.reactionHash, "");
        setText(state.ui.reactionConfirmedBy, "");
        setText(state.ui.reactionStatus, "");
        return;
      }
      var reactants = state.ui.reactionReactants;
      var products = state.ui.reactionProducts;
      clearChildren(reactants);
      clearChildren(products);
      (reaction.reactants.length ? reaction.reactants : [null]).forEach(function (item) {
        reactants.appendChild(el(doc, "li", "acp-mechplan-species-item", speciesText(item)));
      });
      (reaction.products.length ? reaction.products : [null]).forEach(function (item) {
        products.appendChild(el(doc, "li", "acp-mechplan-species-item", speciesText(item)));
      });
      setText(state.ui.reactionMapping, t("next.mechplan.reaction_mapping", {
        count: reaction.mapping.length
      }));
      setText(state.ui.reactionHash, t("next.mechplan.reaction_hash", {
        hash: reaction.configHash || "—"
      }));
      setText(state.ui.reactionConfirmedBy, t("next.mechplan.reaction_confirmed_by", {
        value: reaction.confirmedBy || "—"
      }));
      setText(state.ui.reactionStatus, t("next.mechplan.reaction_status", {
        status: reaction.status || "—"
      }));
    }

    function renderProvenance() {
      var provenance = state.study.report ? state.study.report.provenance : null;
      var hasProvenance = isObject(provenance);
      if (state.ui.provenanceEmpty) state.ui.provenanceEmpty.hidden = hasProvenance;
      if (state.ui.provenanceSummary) state.ui.provenanceSummary.hidden = !hasProvenance;
      if (state.ui.provenanceNotes) {
        clearChildren(state.ui.provenanceNotes);
        state.ui.provenanceNotes.hidden = true;
      }
      if (!hasProvenance) {
        setText(state.ui.provenancePre, "");
        return;
      }
      var records = asArray(provenance.records);
      var count = isFiniteNumber(provenance.count) ? provenance.count : records.length;
      setText(state.ui.provenanceSummary, t("next.mechplan.provenance_summary", {
        count: count,
        signature: textOf(provenance.study_input_signature) || "—"
      }));
      var shown = records.slice(0, MAX_PROVENANCE_RECORDS);
      var payload = {
        study_input_signature: textOf(provenance.study_input_signature),
        count: count,
        records: shown
      };
      try {
        setText(state.ui.provenancePre, JSON.stringify(payload, null, 2));
      } catch (error) {
        setText(state.ui.provenancePre, "");
      }
      if (records.length > shown.length) {
        var more = el(doc, "li", "acp-mechplan-notice", t("next.mechplan.provenance_more", {
          shown: shown.length,
          total: records.length
        }));
        more.setAttribute("data-role", "provenance-more");
        state.ui.provenanceNotes.appendChild(more);
      }
      var notes = asArray(provenance.notes);
      notes.forEach(function (note) {
        if (note == null) return;
        state.ui.provenanceNotes.appendChild(el(doc, "li", "acp-mechplan-notice", String(note)));
      });
      state.ui.provenanceNotes.hidden = state.ui.provenanceNotes.children.length === 0;
    }

    function renderCandidates() {
      if (!state.ui.candidates) return;
      clearChildren(state.ui.candidates);
      if (!state.planCandidates.length) {
        var key = state.plan.strategy === "direct-ts"
          ? "next.mechplan.candidates_empty_direct"
          : "next.mechplan.candidates_empty";
        var empty = i18nEl(doc, "p", "acp-mechplan-empty", key);
        empty.setAttribute("data-role", "candidates-empty");
        state.ui.candidates.appendChild(empty);
        return;
      }
      state.planCandidates.forEach(function (candidate) {
        var label = el(doc, "label", "acp-mechplan-candidate");
        var checkbox = el(doc, "input");
        checkbox.type = "checkbox";
        checkbox.setAttribute("data-role", "plan-step");
        checkbox.setAttribute("data-step-key", candidate.key);
        checkbox.value = candidate.key;
        checkbox.checked = state.plan.selection[candidate.key] === true;
        checkbox.disabled = state.busy !== "none";
        checkbox.addEventListener("change", function () {
          if (!state.mounted || state.disposed || state.busy === "confirm") return;
          state.plan.selection[candidate.key] = !!checkbox.checked;
          touchPlan();
          render();
        });
        label.appendChild(checkbox);
        var vars = {
          step: candidate.id,
          route: candidate.id,
          source: candidate.sourceState || "—",
          sink: candidate.sinkState || "—",
          strategy: candidate.strategy,
          fidelity: candidate.fidelity
        };
        var textKey = candidate.kind === "step"
          ? "next.mechplan.candidate_step"
          : "next.mechplan.candidate_route";
        label.appendChild(el(doc, "span", "acp-mechplan-candidate-text", t(textKey, vars)));
        state.ui.candidates.appendChild(label);
      });
    }

    function effectiveRole(coordinateId, fallback) {
      var value = overrideValue(state.plan.roleOverrides, coordinateId);
      return value === undefined ? fallback : textOf(value);
    }

    function roleLabel(role) {
      if (role === "drive") return t("next.mechplan.role_drive");
      if (role === "freeze") return t("next.mechplan.role_freeze");
      if (role === "monitor") return t("next.mechplan.role_monitor");
      return role;
    }

    function kindLabel(kind) {
      if (kind === "distance") return t("next.mechplan.kind_distance");
      if (kind === "angle") return t("next.mechplan.kind_angle");
      if (kind === "dihedral") return t("next.mechplan.kind_dihedral");
      return kind;
    }

    function renderCoordinates() {
      if (!state.ui.coordsBody) return;
      clearChildren(state.ui.coordsBody);
      var selected = selectedCandidates(state.planCandidates, state.plan.selection);
      if (!selected.length) {
        var row = el(doc, "tr", "acp-mechplan-coord-empty-row");
        var cell = el(doc, "td", "acp-mechplan-td", t("next.mechplan.coords_empty"));
        cell.setAttribute("colspan", "6");
        row.appendChild(cell);
        state.ui.coordsBody.appendChild(row);
        return;
      }
      selected.forEach(function (candidate) {
        candidate.coordinatePlan.coordinates.forEach(function (coordinate, index) {
          var sourceId = textOf(coordinate.id) || "coord-" + String(index + 1);
          var id = candidate.id + "/" + sourceId;
          var role = effectiveRole(id, coordinate.role);
          var startText = has(state.plan.startOverrides, id)
            ? textOf(state.plan.startOverrides[id])
            : (coordinate.start != null ? String(coordinate.start) : "");
          var endText = has(state.plan.endOverrides, id)
            ? textOf(state.plan.endOverrides[id])
            : (coordinate.end != null ? String(coordinate.end) : "");

          var row = el(doc, "tr", "acp-mechplan-coord-row");
          row.setAttribute("data-role", "coord-row");
          row.setAttribute("data-coord-id", id);
          row.appendChild(el(doc, "td", "acp-mechplan-td acp-mechplan-mono", id));
          row.appendChild(el(doc, "td", "acp-mechplan-td", kindLabel(coordinate.kind)));
          row.appendChild(el(doc, "td", "acp-mechplan-td", formatAtoms(coordinate.atoms)));

          var startCell = el(doc, "td", "acp-mechplan-td");
          var startInput = el(doc, "input", "acp-mechplan-input acp-mechplan-input--num");
          startInput.type = "text";
          startInput.setAttribute("inputmode", "decimal");
          startInput.setAttribute("data-role", "coord-start");
          startInput.setAttribute("data-coord-id", id);
          startInput.value = startText;
          startInput.disabled = state.busy === "confirm";
          startInput.addEventListener("change", function () {
            if (!state.mounted || state.disposed || state.busy === "confirm") return;
            state.plan.startOverrides[id] = startInput.value;
            touchPlan();
            render();
          });
          startCell.appendChild(startInput);
          row.appendChild(startCell);

          var endCell = el(doc, "td", "acp-mechplan-td");
          var endInput = el(doc, "input", "acp-mechplan-input acp-mechplan-input--num");
          endInput.type = "text";
          endInput.setAttribute("inputmode", "decimal");
          endInput.setAttribute("data-role", "coord-end");
          endInput.setAttribute("data-coord-id", id);
          endInput.value = endText;
          endInput.disabled = state.busy === "confirm";
          endInput.addEventListener("change", function () {
            if (!state.mounted || state.disposed || state.busy === "confirm") return;
            state.plan.endOverrides[id] = endInput.value;
            touchPlan();
            render();
          });
          endCell.appendChild(endInput);
          row.appendChild(endCell);

          var roleCell = el(doc, "td", "acp-mechplan-td");
          var roleSelect = el(doc, "select", "acp-mechplan-select");
          roleSelect.setAttribute("data-role", "coord-role");
          roleSelect.setAttribute("data-coord-id", id);
          COORDINATE_ROLES.forEach(function (optionRole) {
            var option = el(doc, "option", "", roleLabel(optionRole));
            option.value = optionRole;
            roleSelect.appendChild(option);
          });
          roleSelect.value = COORDINATE_ROLES.indexOf(role) === -1 ? "drive" : role;
          roleSelect.disabled = state.busy === "confirm";
          roleSelect.addEventListener("change", function () {
            if (!state.mounted || state.disposed || state.busy === "confirm") return;
            state.plan.roleOverrides[id] = roleSelect.value;
            touchPlan();
            render();
          });
          roleCell.appendChild(roleSelect);
          row.appendChild(roleCell);

          state.ui.coordsBody.appendChild(row);
        });
      });
    }

    function renderPlanControls() {
      if (!state.ui.strategySelect) return;
      var disabled = state.study.status !== "ready" || state.busy !== "none";
      state.ui.strategySelect.value = state.plan.strategy;
      state.ui.fidelitySelect.value = state.plan.fidelity;
      state.ui.pointsInput.value = state.plan.points;
      state.ui.startFromSelect.value = state.plan.startFrom;
      state.ui.strategySelect.disabled = disabled;
      state.ui.fidelitySelect.disabled = disabled;
      state.ui.pointsInput.disabled = disabled;
      state.ui.startFromSelect.disabled = disabled;
    }

    function renderPlan() {
      var evaluation = evaluatePlan();
      state.evaluation = evaluation;
      if (state.ui.planPreview) {
        try {
          state.ui.planPreview.textContent = JSON.stringify(evaluation.body, null, 2);
        } catch (error) {
          state.ui.planPreview.textContent = "";
        }
      }
      if (state.ui.planFingerprint) {
        state.ui.planFingerprint.textContent = t("next.mechplan.preview_fingerprint", {
          hash: localFingerprint(JSON.stringify(evaluation.body))
        });
      }
      if (state.ui.planErrors) {
        clearChildren(state.ui.planErrors);
        evaluation.check.errors.forEach(function (error) {
          var item = el(doc, "li", "acp-mechplan-error", errorText(error));
          item.setAttribute("data-error", error.code);
          state.ui.planErrors.appendChild(item);
        });
        state.ui.planErrors.hidden = evaluation.check.errors.length === 0;
      }

      if (state.ui.confirmStatus) {
        var statusText = "";
        if (state.plan.status === "confirming") statusText = t("next.mechplan.confirming");
        else if (state.plan.status === "confirmed" && state.plan.receipt) {
          statusText = t("next.mechplan.confirm_ok", {
            status: state.plan.receipt.status || "—",
            hash: state.plan.receipt.planHash || "—"
          });
        } else if (state.plan.status === "error") {
          statusText = t("next.mechplan.confirm_error", { message: state.plan.error || "—" });
        } else if (state.plan.status === "blocked") {
          statusText = t("next.mechplan.confirm_blocked");
        } else {
          statusText = t("next.mechplan.confirm_idle");
        }
        state.ui.confirmStatus.textContent = statusText;
      }

      if (state.ui.confirmBtn) {
        var enabled = canConfirm(evaluation);
        state.ui.confirmBtn.disabled = !enabled;
        state.ui.confirmBtn.textContent = state.plan.status === "confirming"
          ? t("next.mechplan.confirming")
          : t("next.mechplan.confirm");
        state.ui.confirmBtn.setAttribute("title", enabled ? "" : t("next.mechplan.confirm_disabled"));
      }

      if (state.ui.receipt) {
        var receipt = state.plan.receipt;
        var showReceipt = state.plan.status === "confirmed" && !!receipt;
        var showError = state.plan.status === "error" && !!state.plan.error;
        state.ui.receipt.hidden = !showReceipt && !showError;
        if (showReceipt) {
          state.ui.receipt.setAttribute("data-acp-receipt-state", "confirmed");
          setText(state.ui.receiptBody, t("next.mechplan.receipt_status", {
            status: receipt.status || "—"
          }));
          setText(state.ui.receiptHash, t("next.mechplan.receipt_hash", {
            hash: receipt.planHash || "—"
          }));
          state.ui.receiptHash.hidden = false;
        } else if (showError) {
          state.ui.receipt.setAttribute("data-acp-receipt-state", "error");
          setText(state.ui.receiptBody, t("next.mechplan.receipt_error", {
            message: state.plan.error
          }));
          setText(state.ui.receiptHash, "");
          state.ui.receiptHash.hidden = true;
        }
      }
    }

    function renderOverlay() {
      if (!state.ui.overlay) return;
      var studyStatus = state.study.status;
      var showLoading = studyStatus === "loading";
      var showError = studyStatus === "error";
      if (studyStatus === "empty" && state.list.status === "error") showError = true;
      if (studyStatus === "empty" && state.list.status === "loading") showLoading = true;
      var show = showLoading || showError;
      state.ui.overlay.hidden = !show;
      if (!show) return;
      if (showError) {
        state.ui.overlayMessage.textContent = t("next.mechplan.error_title");
        var detail = state.study.status === "error" ? state.study.error : state.list.error;
        state.ui.overlayDetail.textContent = textOf(detail) || "";
        state.ui.overlayDetail.hidden = !detail;
      } else {
        state.ui.overlayMessage.textContent = t("next.mechplan.loading_title");
        state.ui.overlayDetail.textContent = "";
        state.ui.overlayDetail.hidden = true;
      }
      var retry = state.study.status === "error" ? state.study.retry : state.retry;
      state.ui.retryBtn.hidden = typeof retry !== "function" || !showError;
    }

    function render() {
      if (!state.root) return;
      renderStateChrome();
      renderList();
      var ready = state.study.status === "ready" && !!state.study.detail;
      if (state.ui.summarySection) state.ui.summarySection.hidden = !ready;
      if (state.ui.stepsSection) state.ui.stepsSection.hidden = !ready;
      if (state.ui.reactionSection) state.ui.reactionSection.hidden = !ready;
      if (state.ui.provenanceSection) state.ui.provenanceSection.hidden = !ready;
      if (state.ui.planSection) state.ui.planSection.hidden = !ready;
      if (state.ui.emptyState) {
        state.ui.emptyState.hidden = ready || state.study.status === "loading" ||
          state.study.status === "error" || state.list.status === "loading" ||
          state.list.status === "error";
      }
      if (ready) {
        renderSummary();
        renderRoleLists();
        renderSteps();
        renderReaction();
        renderProvenance();
        renderPlanControls();
        renderCandidates();
        renderCoordinates();
        renderPlan();
      }
      renderOverlay();
    }

    /* ── context / lifecycle ────────────────────────────────────── */

    function applySnapshot(snapshot, options) {
      if (!state.mounted || state.disposed) return;
      options = options || {};
      snapshot = isObject(snapshot) ? snapshot : {};
      var taskRef = isObject(snapshot.taskRef) ? snapshot.taskRef : null;
      var jobId = taskRef && taskRef.jobId ? String(taskRef.jobId) : "";
      var projectId = snapshot.projectId ? String(snapshot.projectId) : "";
      state.projectId = projectId;
      if (state.focusJobId !== jobId) {
        state.focusJobId = jobId;
        resetStudyState();
        state.list.items = [];
        state.list.status = "idle";
        state.list.error = null;
        state.list.jobId = jobId;
        state.list.token += 1;
        state.retry = null;
        if (jobId && !options.skipLoad) {
          loadStudyList({ jobId: jobId, autoLoad: true });
        } else {
          render();
        }
        return;
      }
      render();
    }

    function onContextNotify() {
      if (!state.mounted || state.disposed || !context || typeof context.getSnapshot !== "function") return;
      try {
        applySnapshot(context.getSnapshot());
      } catch (error) { /* malformed snapshot never breaks the panel */ }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("mechanism-plan mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("mechanism-plan: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("mechanism-plan: already mounted (dispose() first)"));
      }
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.busy = "none";
      state.focusJobId = "__init__";
      state.projectId = "";
      state.list = { status: "idle", items: [], token: 0, error: null, jobId: "" };
      state.study = {
        status: "empty", id: "", detail: null, report: null, reaction: null,
        token: 0, error: null, notices: [], retry: null
      };
      state.planCandidates = [];
      state.plan = {
        selection: {},
        roleOverrides: {},
        startOverrides: {},
        endOverrides: {},
        strategy: "guided-scan",
        fidelity: "s3",
        points: String(DEFAULT_POINTS),
        startFrom: "reactant",
        startFromTouched: false,
        status: "idle",
        receipt: null,
        receiptKey: "",
        error: "",
        token: 0
      };
      state.retry = null;
      state.evaluation = null;
      cleanup.length = 0;

      if (api.context) context = api.context;
      buildDom();
      render();

      var snapshot = null;
      if (context && typeof context.getSnapshot === "function") {
        try { snapshot = context.getSnapshot(); } catch (error) { snapshot = null; }
      }
      if (typeof deps.initialStudyId === "string" && deps.initialStudyId) {
        applySnapshot(snapshot, { skipLoad: true });
        loadStudy(deps.initialStudyId);
      } else if (snapshot) {
        applySnapshot(snapshot);
      }
      if (context && typeof context.subscribe === "function") {
        var unsubscribe = context.subscribe(onContextNotify);
        cleanup.push(function () {
          try { unsubscribe(); } catch (error) { /* subscription already gone */ }
        });
      }
      if (doc && typeof doc.addEventListener === "function") {
        var langHandler = function () {
          if (!state.mounted || state.disposed) return;
          render();
        };
        doc.addEventListener("acp:langchange", langHandler);
        cleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      if (!state.mounted || state.disposed) return;
      applySnapshot(snapshot || (context && typeof context.getSnapshot === "function"
        ? context.getSnapshot()
        : {}));
    }

    function suspend() {
      state.suspended = true;
    }

    function resume() {
      state.suspended = false;
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.list.token += 1;
      state.study.token += 1;
      state.plan.token += 1;
      state.busy = "none";
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.root && state.root.parentNode &&
          typeof state.root.parentNode.removeChild === "function") {
        try { state.root.parentNode.removeChild(state.root); } catch (error) { /* detached */ }
      }
      state.root = null;
      state.host = null;
      state.ui = {};
      state.retry = null;
      state.evaluation = null;
      state.planCandidates = [];
      state.study.detail = null;
      state.study.report = null;
      state.study.reaction = null;
      state.study.notices = [];
      state.study.retry = null;
      state.list.items = [];
      state.plan.selection = {};
      state.plan.roleOverrides = {};
      state.plan.startOverrides = {};
      state.plan.endOverrides = {};
      state.plan.receipt = null;
      state.plan.receiptKey = "";
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: function (deps) { return createInstance(deps || {}); },
    _internals: {
      normalizeStudyList: normalizeStudyList,
      normalizeDetail: normalizeDetail,
      normalizeReport: normalizeReport,
      normalizeReaction: normalizeReaction,
      classifyRole: classifyRole,
      collectRoleBuckets: collectRoleBuckets,
      collectSteps: collectSteps,
      buildCandidates: buildCandidates,
      buildPlanBody: buildPlanBody,
      buildPlanCoordinates: buildPlanCoordinates,
      validatePlanBody: validatePlanBody,
      planKeyOf: planKeyOf,
      localFingerprint: localFingerprint,
      planStatusInfo: planStatusInfo,
      formatAtoms: formatAtoms,
      studyPath: studyPath,
      listQuery: listQuery,
      STRATEGIES: STRATEGIES,
      FIDELITIES: FIDELITIES,
      COORDINATE_ROLES: COORDINATE_ROLES,
      COORDINATE_KINDS: COORDINATE_KINDS,
      MIN_POINTS: MIN_POINTS,
      DEFAULT_POINTS: DEFAULT_POINTS,
      LIST_LIMIT: LIST_LIMIT,
      STUDIES_PATH: STUDIES_PATH,
      i18n: ZH
    }
  };
})();
