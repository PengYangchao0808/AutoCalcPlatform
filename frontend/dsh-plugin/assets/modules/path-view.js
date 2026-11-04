/* file: js/modules/path-view/module.js */
/*
 * ACP 3.0 module — path-view (Track A / A-50 · P17 "路径结构视图").
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["path-view"]`); the Shell loader resolves the factory
 * and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (A-50 only):
 *   - read a job path (PESsearch / scan / other terminal jobs) or a historical
 *     mechanism study path and list its stationary structures
 *     (reactant / TS / intermediate / product) plus the frame/step sequence and
 *     the server-side provenance;
 *   - selecting a structure emits the frozen canonical `structureRef` through
 *     the injected Shell command `commands.openStructure` so the EXISTING
 *     molecule-viewer renders it. This module owns no 3D scene, no measurement
 *     and no geometry-editing control: A-45 (measurement) / A-46 (edit
 *     transaction) / A-47 (asset save) live in molecule-viewer and are never
 *     duplicated here.
 *
 * Existing read routes only (no new backend, no invented route):
 *   GET /api/v1/jobs/{jobId}                                  (path identity)
 *   GET /api/v1/jobs/{jobId}/structure-viewer                 (stationary entries)
 *   GET /api/v1/jobs/{jobId}/energy-graph                     (frames + energies)
 *   GET /api/v1/jobs/{jobId}/pes/review                       (PESsearch only)
 *   GET /api/v1/mechanism-studies?job_id=&limit=              (mechanism study)
 *   GET /api/v1/mechanism-studies/{studyId}/report            (mechanism nodes/steps)
 *
 * Data flows through the injected client seam only
 * (`deps.request` -> `deps.client.transport.request` ->
 * `window.ACPClient.transport.request`); this module performs no direct network
 * call and never stores/reads a filesystem path. API paths are the six route
 * constants above plus `encodeURIComponent(id)`.
 *
 * Four states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error. Command busy state is separate
 * (`data-acp-busy` = none | load); structure selection state is
 * `data-acp-selection-state` = none | sent | unavailable | error. Every async
 * result is guarded by the mount generation plus a per-load token, so dispose /
 * a newer job / a newer load can never paint into the DOM.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "path-view";
  var API = "/api/v1";
  var ROUTES = {
    jobs: API + "/jobs/",
    catalog: "/structure-viewer",
    energy: "/energy-graph",
    pesReview: "/pes/review",
    studies: API + "/mechanism-studies",
    report: "/report"
  };
  var STUDY_LIMIT = 5;
  var MAX_PROVENANCE_TEXT = 320;
  var STRUCTURE_KINDS = ["structure-entry", "job-file", "job-remote-file", "inline-xyz"];
  var ROLE_KEYS = {
    ts: "next.pathview.role.ts",
    reactant: "next.pathview.role.reactant",
    product: "next.pathview.role.product",
    intermediate: "next.pathview.role.intermediate",
    minimum: "next.pathview.role.minimum",
    unknown: "next.pathview.role.unknown"
  };

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["path.structures"],
    binds: ["taskRef", "structureRef"],
    capabilities: { requires: [], provides: ["path.structures"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.pathview.errorTitle", messageKey: "next.pathview.errorMessage" }
  };

  /* Local zh copy. The Shell dictionary is consulted first; `ACPShell.i18n.t()`
   * echoes the key when no translation exists, so these values are the real
   * fallback until the shell i18n batch adds `next.pathview.*` (README lists the
   * full key set). Static chrome also carries `data-i18n`. */
  var ZH = {
    "next.pathview.title": "路径结构视图",
    "next.pathview.subtitle": "机理 / PES 路径的驻点结构、帧与溯源 · 选中结构交给现有查看器（测量与编辑由 A-45/A-46 提供）",
    "next.pathview.source_title": "1 · 路径来源",
    "next.pathview.source_hint": "自动跟随当前任务；也可手动输入任务 ID 载入。只读。",
    "next.pathview.job_id": "任务 ID",
    "next.pathview.job_id_ph": "例如 20261031_001_PESsearch",
    "next.pathview.load": "载入路径",
    "next.pathview.reload": "重新载入",
    "next.pathview.summary_title": "2 · 路径概览",
    "next.pathview.summary_kind": "路径类型",
    "next.pathview.summary_job": "任务",
    "next.pathview.summary_workflow": "工作流",
    "next.pathview.summary_status": "任务状态",
    "next.pathview.summary_attempt": "尝试",
    "next.pathview.summary_catalog": "结构目录",
    "next.pathview.summary_catalog_revision": "目录修订",
    "next.pathview.summary_review": "PES 选点",
    "next.pathview.summary_series": "能量序列",
    "next.pathview.structures_title": "3 · 驻点结构",
    "next.pathview.structures_hint": "角色来自服务端目录 / 研究报告，本模块只做词形归一，不推断化学。",
    "next.pathview.structures_empty": "该路径没有可展示的驻点结构。",
    "next.pathview.steps_title": "4 · 帧 / 步骤",
    "next.pathview.steps_hint": "任务路径显示能量图节点帧序；机理路径显示报告中的基元步骤。",
    "next.pathview.steps_empty": "该路径没有可展示的帧或步骤。",
    "next.pathview.provenance_title": "5 · 溯源",
    "next.pathview.provenance_hint": "逐项列出服务端事实；模型叙述不作为证据。",
    "next.pathview.provenance_empty": "服务端未提供溯源字段。",
    "next.pathview.selection_title": "选中结构",
    "next.pathview.selection_none": "尚未选中结构。选择一行由现有查看器打开；本面板不渲染 3D、不提供测量/编辑。",
    "next.pathview.selection_sent": "已请求打开 {ref}（{via}）。测量与内坐标编辑在查看器中（A-45/A-46）。",
    "next.pathview.selection_unavailable": "未发送查看请求：{message}",
    "next.pathview.selection_error": "查看命令失败：{message}",
    "next.pathview.measure_note": "本模块不提供测量 / 编辑控件（A-45/A-46 专属）；也不创建第二个 3D 查看器。",
    "next.pathview.open_structure": "在查看器中打开",
    "next.pathview.ref_missing": "该条目没有可用的结构引用，未发送查看请求。",
    "next.pathview.no_commands": "Shell 未注入 commands.openStructure，无法定位查看器；未发送任何查看请求。",
    "next.pathview.empty_title": "尚未载入路径",
    "next.pathview.empty_hint": "输入任务 ID 载入，或在有任务上下文时跟随当前任务。",
    "next.pathview.empty_no_structures": "服务端没有为这个任务返回驻点结构或帧数据。",
    "next.pathview.empty_pes_pending": "该 PES 任务尚未确认选点（pes/review 为 pending）；先在能量查看器确认选点后再查看路径结构。",
    "next.pathview.empty_no_study": "该机制的只读研究报告没有可用的结构 / 步骤数据。",
    "next.pathview.loading_title": "正在载入路径…",
    "next.pathview.error_title": "路径加载失败",
    "next.pathview.errorTitle": "路径结构视图不可用",
    "next.pathview.errorMessage": "path-view 模块加载失败，请查看控制台。",
    "next.pathview.retry": "重试",
    "next.pathview.partial": "部分数据不可用",
    "next.pathview.partial_detail": "部分数据不可用：{message}",
    "next.pathview.err.client_missing": "客户端未注入（ACPClient.transport），无法读取路径。",
    "next.pathview.err.catalog_endpoint": "结构目录端点不可用（{status}）：{message}；未使用任何替代端点。",
    "next.pathview.err.invalid_catalog": "结构目录响应无法识别。",
    "next.pathview.kind_pes": "PES 路径",
    "next.pathview.kind_scan": "扫描路径",
    "next.pathview.kind_mechanism": "机理路径",
    "next.pathview.kind_job": "任务路径",
    "next.pathview.kind_unknown": "未知",
    "next.pathview.role.ts": "TS",
    "next.pathview.role.reactant": "反应物",
    "next.pathview.role.product": "产物",
    "next.pathview.role.intermediate": "中间体",
    "next.pathview.role.minimum": "最低点",
    "next.pathview.role.unknown": "未分类",
    "next.pathview.none": "—",
    "next.pathview.no_energy": "—",
    "next.pathview.unit_hartree": "Ha",
    "next.pathview.unit_kcal": "kcal/mol",
    "next.pathview.source_manual_review": "人工确认 (pes/review)",
    "next.pathview.source_formal_result": "正式结果",
    "next.pathview.source_mechanism": "机理研究报告",
    "next.pathview.source_other": "服务端目录",
    "next.pathview.ref_missing_title": "服务端未提供结构引用",
    "next.pathview.prov_job": "任务",
    "next.pathview.prov_workflow": "工作流",
    "next.pathview.prov_status": "状态",
    "next.pathview.prov_attempt": "尝试",
    "next.pathview.prov_catalog": "结构目录",
    "next.pathview.prov_energy": "能量图",
    "next.pathview.prov_review": "PES 选点",
    "next.pathview.prov_review_source": "选点来源",
    "next.pathview.prov_study": "机理研究",
    "next.pathview.prov_report": "研究报告溯源"
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

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function textOf(value) {
    return typeof value === "string" ? value : (value == null ? "" : String(value));
  }

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function integerOf(value) {
    return finiteNumber(value) && value >= 0 ? Math.floor(value) : null;
  }

  function describeError(error) {
    if (!error) return "unknown error";
    if (typeof error === "string") return error;
    if (typeof error.message === "string" && error.message) return error.message;
    return String(error);
  }

  function errorStatus(error) {
    if (!error) return null;
    if (typeof error.status === "number") return error.status;
    var match = describeError(error).match(/\bHTTP\s+(\d{3})\b/);
    return match ? parseInt(match[1], 10) : null;
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") {
      node.replaceChildren();
      return;
    }
    while (node.children && node.children.length) {
      node.removeChild(node.children[node.children.length - 1]);
    }
    node.textContent = "";
  }

  function encodeId(value) {
    return encodeURIComponent(String(value));
  }

  function jobPath(jobId) {
    return ROUTES.jobs + encodeId(jobId);
  }

  function catalogPath(jobId) {
    return jobPath(jobId) + ROUTES.catalog;
  }

  function energyPath(jobId) {
    return jobPath(jobId) + ROUTES.energy;
  }

  function pesReviewPath(jobId) {
    return jobPath(jobId) + ROUTES.pesReview;
  }

  function studiesQuery(jobId) {
    return ROUTES.studies + "?job_id=" + encodeId(jobId) + "&limit=" + STUDY_LIMIT;
  }

  function studyReportPath(studyId) {
    return ROUTES.studies + "/" + encodeId(studyId) + ROUTES.report;
  }

  /* ── pure normalization (server data -> panel model) ─────────────── */

  /** Word-form only role normalization; never infers chemistry. */
  function classifyRole(raw) {
    var value = textOf(raw).trim().toLowerCase();
    if (!value) return "unknown";
    if (value === "ts" || value === "transition_state" || value.indexOf("transition") !== -1 ||
        value.indexOf("saddle") !== -1) {
      return "ts";
    }
    if (value.indexOf("react") !== -1 || value.indexOf("start") !== -1) return "reactant";
    if (value.indexOf("prod") !== -1 || value.indexOf("end") !== -1) return "product";
    if (value === "int" || value === "endpoint" || value === "minimum" ||
        value.indexOf("inter") !== -1) {
      return "intermediate";
    }
    return "unknown";
  }

  function roleLabelKey(role) {
    return ROLE_KEYS[role] || ROLE_KEYS.unknown;
  }

  function normalizeJob(payload) {
    if (!isObject(payload)) return null;
    var spec = isObject(payload.spec) ? payload.spec : {};
    return {
      id: textOf(payload.id) || textOf(payload.job_id),
      workflow: textOf(spec.workflow),
      status: textOf(payload.status),
      attempt: finiteNumber(payload.attempt) && payload.attempt >= 1 ? Math.floor(payload.attempt) : 1,
      taskName: textOf(spec.task_name),
      moleculeName: textOf(payload.molecule_name) || textOf(spec.molecule_name),
      projectId: textOf(payload.project_id)
    };
  }

  function normalizeCatalogEntry(raw) {
    if (!isObject(raw)) return null;
    var id = textOf(raw.id);
    if (!id) return null;
    var source = isObject(raw.source) ? raw.source : {};
    var rawEnergy = isObject(raw.energy) ? raw.energy : null;
    return {
      id: id,
      groupId: textOf(raw.group_id),
      label: textOf(raw.label) || id,
      role: textOf(raw.role),
      status: textOf(raw.status),
      energy: rawEnergy ? {
        value: finiteNumber(rawEnergy.value) ? rawEnergy.value : null,
        unit: textOf(rawEnergy.unit),
        kind: textOf(rawEnergy.kind)
      } : null,
      relativeEnergyKcal: finiteNumber(raw.relative_energy_kcal)
        ? raw.relative_energy_kcal : null,
      geometryRef: textOf(source.geometry_ref),
      frameIndex: integerOf(source.frame_index) != null
        ? integerOf(source.frame_index) : integerOf(raw.frame_index),
      sourceKind: textOf(source.kind),
      confirmed: source.confirmed === true,
      badges: asArray(raw.badges).filter(function (badge) { return typeof badge === "string"; })
    };
  }

  function normalizeCatalog(payload) {
    if (!isObject(payload)) {
      return { availability: "error", revision: "", workflow: "", jobStatus: "", groups: [], entries: [], warnings: [] };
    }
    var entries = [];
    asArray(payload.entries).forEach(function (raw) {
      var entry = normalizeCatalogEntry(raw);
      if (entry) entries.push(entry);
    });
    var groups = asArray(payload.groups).filter(isObject).map(function (raw) {
      return { id: textOf(raw.id), label: textOf(raw.label), kind: textOf(raw.kind) };
    }).filter(function (group) { return !!group.id; });
    var availability = textOf(payload.availability) ||
      (entries.length ? "ready" : "empty");
    return {
      availability: availability,
      revision: textOf(payload.revision),
      workflow: textOf(payload.workflow),
      jobStatus: textOf(payload.job_status),
      groups: groups,
      entries: entries,
      warnings: asArray(payload.warnings).map(function (item) { return textOf(item); })
        .filter(function (item) { return !!item; })
    };
  }

  function normalizeEnergyGraph(payload) {
    if (!isObject(payload)) {
      return { viewType: "", provenance: {}, defaultSeries: null, series: [], frames: [], annotations: [], warnings: [] };
    }
    var series = asArray(payload.series).filter(isObject).map(function (raw, index) {
      return {
        id: textOf(raw.id) || ("series-" + index),
        label: textOf(raw.label) || textOf(raw.id) || ("series-" + index),
        unit: textOf(raw.unit),
        values: asArray(raw.values),
        xValues: asArray(raw.x_values)
      };
    });
    var defaultSeries = null;
    var requested = textOf(payload.default_series);
    series.forEach(function (item) {
      if (defaultSeries) return;
      if (requested && item.id === requested) defaultSeries = item;
    });
    if (!defaultSeries) {
      series.forEach(function (item) {
        if (defaultSeries) return;
        var drawable = item.values.some(function (value) { return finiteNumber(value); });
        if (drawable) defaultSeries = item;
      });
    }
    if (!defaultSeries && series.length) defaultSeries = series[0];

    var frames = [];
    asArray(payload.nodes).filter(isObject).forEach(function (node, index) {
      var frameIndex = integerOf(node.frame_index);
      frames.push({
        index: frameIndex == null ? index : frameIndex,
        label: textOf(node.label) || ("Frame " + (frameIndex == null ? index + 1 : frameIndex + 1)),
        energy: finiteNumber(node.energy) ? node.energy : null,
        status: textOf(node.status),
        x: finiteNumber(node.x) ? node.x : null,
        geometryRef: textOf(node.geometry_ref)
      });
    });

    var annotations = asArray(payload.annotations).filter(isObject).map(function (raw, index) {
      return {
        id: textOf(raw.id) || ("annotation-" + index),
        candidateId: textOf(raw.candidate_id) || textOf(raw.candidateId),
        type: textOf(raw.type),
        label: textOf(raw.label),
        frameIndex: integerOf(raw.frame_index) != null
          ? integerOf(raw.frame_index) : integerOf(raw.frameIndex),
        x: finiteNumber(raw.x) ? raw.x : null,
        y: finiteNumber(raw.y) ? raw.y : null,
        saved: raw.saved === true,
        selectionSource: textOf(raw.selection_source) || textOf(raw.selectionSource),
        confidence: raw.confidence == null ? "" : textOf(raw.confidence),
        reason: textOf(raw.reason),
        status: textOf(raw.status)
      };
    });

    return {
      viewType: textOf(payload.view_type),
      provenance: isObject(payload.provenance) ? payload.provenance : {},
      defaultSeries: defaultSeries ? {
        id: defaultSeries.id, label: defaultSeries.label, unit: defaultSeries.unit
      } : null,
      series: series,
      frames: frames,
      annotations: annotations,
      warnings: []
    };
  }

  function normalizePesReview(payload) {
    if (!isObject(payload)) {
      return { status: "pending", revision: 0, confirmedAt: "", note: "", profileSha256: "", source: {}, selected: [], backups: [] };
    }
    var review = isObject(payload.review) ? payload.review : {};
    var source = isObject(review.source) ? review.source : {};
    var selected = asArray(review.selected).filter(isObject).map(function (row) {
      return {
        candidateId: textOf(row.candidate_id),
        role: classifyRole(row.role),
        rawRole: textOf(row.role),
        frameIndex: integerOf(row.frame_index),
        name: textOf(row.name) || textOf(row.candidate_id),
        structurePath: textOf(row.structure_path),
        frameConverged: row.frame_converged === true,
        selectionSource: textOf(row.selection_source)
      };
    }).filter(function (row) { return !!row.candidateId || row.frameIndex != null; });
    var backups = asArray(payload.backups).filter(isObject).map(function (raw) {
      return {
        n: finiteNumber(raw.n) ? Math.floor(raw.n) : null,
        confirmedAt: textOf(raw.confirmed_at),
        note: textOf(raw.note),
        selectedCount: finiteNumber(raw.selected_count) ? Math.floor(raw.selected_count) : 0
      };
    });
    return {
      status: textOf(payload.status) || "pending",
      revision: finiteNumber(review.revision) ? Math.floor(review.revision) : 0,
      confirmedAt: textOf(review.confirmed_at),
      note: textOf(review.note),
      profileSha256: textOf(review.profile_sha256),
      source: {
        taskStatus: textOf(source.task_status),
        frameSource: textOf(source.frame_source),
        scanComplete: typeof source.scan_complete === "boolean" ? source.scan_complete : null,
        dataSource: textOf(source.data_source),
        dataSha256: textOf(source.data_sha256)
      },
      selected: selected,
      backups: backups
    };
  }

  function normalizeStudyList(payload) {
    var rows = Array.isArray(payload) ? payload : asArray(isObject(payload) ? payload.studies : null);
    return rows.filter(isObject).map(function (raw) {
      return {
        id: textOf(raw.id) || textOf(raw.study_id),
        jobId: textOf(raw.job_id),
        status: textOf(raw.status),
        unifiedStatus: textOf(raw.unified_status)
      };
    }).filter(function (row) { return !!row.id; });
  }

  function canonicalRef(raw) {
    if (!isObject(raw)) return null;
    var kind = textOf(raw.kind);
    if (STRUCTURE_KINDS.indexOf(kind) < 0) return null;
    var frameIndex = integerOf(raw.frameIndex) != null ? integerOf(raw.frameIndex)
      : (integerOf(raw.frame_index) != null ? integerOf(raw.frame_index) : 0);
    if (kind === "inline-xyz") {
      var text = textOf(raw.text);
      if (!text.trim()) return null;
      return { kind: kind, text: text, frameIndex: frameIndex };
    }
    var jobId = textOf(raw.jobId) || textOf(raw.job_id);
    if (!jobId) return null;
    if (kind === "structure-entry") {
      var entryId = textOf(raw.entryId) || textOf(raw.entry_id);
      if (!entryId) return null;
      return { kind: kind, jobId: jobId, entryId: entryId, frameIndex: frameIndex };
    }
    var path = textOf(raw.path);
    if (!path) return null;
    return { kind: kind, jobId: jobId, path: path, frameIndex: frameIndex };
  }

  function mechanismNode(raw) {
    if (!isObject(raw)) return null;
    var id = textOf(raw.state_id) || textOf(raw.point_id) || textOf(raw.id);
    if (!id) return null;
    var energy = null;
    if (finiteNumber(raw.canonical_energy_hartree)) energy = raw.canonical_energy_hartree;
    else if (finiteNumber(raw.energy_hartree)) energy = raw.energy_hartree;
    else if (finiteNumber(raw.energy)) energy = raw.energy;
    return {
      id: id,
      label: textOf(raw.label) || id,
      role: classifyRole(raw.role || raw.kind || raw.stationary_point_type),
      energy: energy,
      ref: canonicalRef(raw.structure_ref || raw.structureRef),
      entryId: textOf(raw.entry_id) || textOf(raw.entryId),
      charge: finiteNumber(raw.charge) ? raw.charge : null,
      multiplicity: finiteNumber(raw.multiplicity) ? raw.multiplicity : null
    };
  }

  function mechanismStep(raw, index) {
    if (!isObject(raw)) return null;
    return {
      id: textOf(raw.step_id) || textOf(raw.id) || ("step-" + (index + 1)),
      source: textOf(raw.source_state_id) || textOf(raw.source) || textOf(raw.from),
      sink: textOf(raw.sink_state_id) || textOf(raw.sink) || textOf(raw.to),
      ts: textOf(raw.ts_id) || textOf(raw.ts) || textOf(raw.transition_state_id),
      barrierForward: finiteNumber(raw.barrier_forward) ? raw.barrier_forward
        : (finiteNumber(raw.barrierForward) ? raw.barrierForward : null),
      barrierReverse: finiteNumber(raw.barrier_reverse) ? raw.barrier_reverse
        : (finiteNumber(raw.barrierReverse) ? raw.barrierReverse : null),
      fidelity: textOf(raw.fidelity),
      strategy: textOf(raw.path_strategy) || textOf(raw.strategy),
      status: textOf(raw.status)
    };
  }

  function normalizeMechanismReport(payload) {
    if (!isObject(payload)) {
      return { studyId: "", jobId: "", nodes: [], steps: [], provenance: null, hasNetwork: false, hasStationaryPoints: false, hasProvenance: false };
    }
    var network = isObject(payload.reaction_network) ? payload.reaction_network : null;
    var stationaryRaw = payload.stationary_points;
    var stationaryRows = Array.isArray(stationaryRaw) ? stationaryRaw
      : (isObject(stationaryRaw) ? asArray(stationaryRaw.stationary_points) : []);
    var byId = {};
    var order = [];
    function merge(node) {
      if (!node) return;
      var existing = byId[node.id];
      if (!existing) {
        byId[node.id] = node;
        order.push(node.id);
        return;
      }
      if (existing.role === "unknown" && node.role !== "unknown") existing.role = node.role;
      if (existing.energy == null && node.energy != null) existing.energy = node.energy;
      if (!existing.ref && node.ref) existing.ref = node.ref;
      if (!existing.entryId && node.entryId) existing.entryId = node.entryId;
      if (!existing.label || existing.label === existing.id) existing.label = node.label;
    }
    asArray(network && network.nodes).forEach(function (raw) { merge(mechanismNode(raw)); });
    stationaryRows.forEach(function (raw) { merge(mechanismNode(raw)); });
    var nodes = order.map(function (id) { return byId[id]; });

    var steps = [];
    asArray(network && network.edges).forEach(function (raw, index) {
      var step = mechanismStep(raw, index);
      if (step) steps.push(step);
    });

    return {
      studyId: textOf(payload.study_id),
      jobId: textOf(payload.job_id),
      nodes: nodes,
      steps: steps,
      provenance: isObject(payload.provenance) ? payload.provenance : null,
      hasNetwork: !!network,
      hasStationaryPoints: stationaryRows.length > 0,
      hasProvenance: isObject(payload.provenance)
    };
  }

  function structureRefForEntry(entry, jobId) {
    if (!entry || typeof entry.id !== "string" || !entry.id || !jobId) return null;
    return {
      kind: "structure-entry",
      jobId: jobId,
      entryId: entry.id,
      frameIndex: integerOf(entry.frameIndex) == null ? 0 : integerOf(entry.frameIndex)
    };
  }

  function structureRefForNode(node, jobId) {
    if (!node) return null;
    if (node.ref) return node.ref;
    if (node.entryId && jobId) {
      return { kind: "structure-entry", jobId: jobId, entryId: node.entryId, frameIndex: 0 };
    }
    return null;
  }

  function refKey(ref) {
    if (!isObject(ref)) return "";
    if (ref.kind === "inline-xyz") {
      return ["inline-xyz", String(ref.frameIndex == null ? 0 : ref.frameIndex), textOf(ref.text)].join("|");
    }
    if (ref.kind === "structure-entry") {
      return ["structure-entry", textOf(ref.jobId), textOf(ref.entryId),
        String(ref.frameIndex == null ? 0 : ref.frameIndex)].join("|");
    }
    return [textOf(ref.kind), textOf(ref.jobId), textOf(ref.path),
      String(ref.frameIndex == null ? 0 : ref.frameIndex)].join("|");
  }

  function shortJson(value, limit) {
    if (value == null) return "";
    var text;
    try {
      text = JSON.stringify(value);
    } catch (error) {
      text = String(value);
    }
    if (!text) return "";
    var max = limit || MAX_PROVENANCE_TEXT;
    return text.length > max ? text.slice(0, max) + "…" : text;
  }

  /**
   * Join the six read surfaces into the panel model. Pure: no DOM, no request.
   * `structures` keep the SERVER order (catalog order then mechanism-only nodes);
   * this function never sorts or ranks them.
   */
  function buildPathModel(input) {
    input = input || {};
    var job = isObject(input.job) ? input.job : null;
    var catalog = input.catalog && typeof input.catalog === "object" ? input.catalog : null;
    var energy = input.energy && typeof input.energy === "object" ? input.energy : null;
    var review = input.review && typeof input.review === "object" ? input.review : null;
    var report = input.report && typeof input.report === "object" ? input.report : null;
    var warnings = [];

    (asArray(input.warnings)).forEach(function (item) {
      if (item) warnings.push(textOf(item));
    });
    (catalog && catalog.warnings ? catalog.warnings : []).forEach(function (item) {
      if (item) warnings.push(item);
    });

    var jobId = job ? job.id : "";
    var workflow = job && job.workflow ? job.workflow : (catalog ? catalog.workflow : "");
    var hasReportPath = !!(report && (report.nodes.length || report.steps.length));
    var kind = "job";
    if (hasReportPath) kind = "mechanism";
    else if (workflow === "PESsearch") kind = "pes";
    else if (workflow === "scan") kind = "scan";

    var energyByFrame = {};
    var annotationsById = {};
    var annotationsByFrame = {};
    if (energy) {
      energy.frames.forEach(function (frame) {
        if (frame.index != null) energyByFrame[frame.index] = frame;
      });
      energy.annotations.forEach(function (annotation) {
        if (annotation.candidateId) annotationsById[annotation.candidateId] = annotation;
        if (annotation.frameIndex != null && !annotationsByFrame[annotation.frameIndex]) {
          annotationsByFrame[annotation.frameIndex] = annotation;
        }
      });
    }
    var reviewByFrame = {};
    var reviewByCandidate = {};
    if (review) {
      review.selected.forEach(function (row) {
        if (row.frameIndex != null) reviewByFrame[row.frameIndex] = row;
        if (row.candidateId) reviewByCandidate[row.candidateId] = row;
      });
    }

    var structures = [];
    var seen = {};
    function pushStructure(item) {
      if (!item || seen[item.key]) return;
      seen[item.key] = true;
      structures.push(item);
    }

    (catalog ? catalog.entries : []).forEach(function (entry) {
      var frame = entry.frameIndex;
      var frameEnergy = frame != null ? energyByFrame[frame] : null;
      var reviewRow = frame != null ? reviewByFrame[frame] : null;
      var annotation = null;
      if (reviewRow && reviewRow.candidateId) annotation = annotationsById[reviewRow.candidateId];
      if (!annotation && frame != null) annotation = annotationsByFrame[frame] || null;
      var value = null;
      var unit = "";
      var energyKind = "";
      if (entry.energy && finiteNumber(entry.energy.value)) {
        value = entry.energy.value;
        unit = entry.energy.unit;
        energyKind = entry.energy.kind;
      } else if (frameEnergy && finiteNumber(frameEnergy.energy)) {
        value = frameEnergy.energy;
        unit = energy && energy.defaultSeries ? energy.defaultSeries.unit : "";
        energyKind = energy && energy.defaultSeries ? energy.defaultSeries.id : "";
      } else if (annotation && finiteNumber(annotation.y)) {
        value = annotation.y;
        unit = energy && energy.defaultSeries ? energy.defaultSeries.unit : "";
        energyKind = energy && energy.defaultSeries ? energy.defaultSeries.id : "";
      }
      var role = classifyRole(reviewRow ? reviewRow.role : entry.role);
      var ref = structureRefForEntry(entry, jobId);
      pushStructure({
        key: "catalog:" + entry.id,
        entryId: entry.id,
        id: reviewRow && reviewRow.candidateId ? reviewRow.candidateId : entry.id,
        label: reviewRow && reviewRow.name ? reviewRow.name : entry.label,
        role: role,
        rawRole: reviewRow ? reviewRow.rawRole : entry.role,
        status: entry.status,
        frameIndex: frame,
        energyValue: value,
        energyUnit: unit,
        energyKind: energyKind,
        relativeEnergyKcal: entry.relativeEnergyKcal,
        sourceKind: entry.sourceKind || (reviewRow ? "manual_review" : "catalog"),
        candidateId: reviewRow && reviewRow.candidateId ? reviewRow.candidateId : "",
        selectionSource: annotation ? annotation.selectionSource
          : (reviewRow ? reviewRow.selectionSource : ""),
        confidence: annotation ? annotation.confidence : "",
        reason: annotation ? annotation.reason : "",
        confirmed: entry.confirmed || !!reviewRow,
        ref: ref,
        refProblem: ref ? "" : "next.pathview.ref_missing"
      });
    });

    (report ? report.nodes : []).forEach(function (node) {
      var ref = structureRefForNode(node, jobId);
      pushStructure({
        key: "mechanism:" + node.id,
        entryId: "",
        id: node.id,
        label: node.label,
        role: classifyRole(node.role),
        rawRole: node.role,
        status: "",
        frameIndex: null,
        energyValue: node.energy,
        energyUnit: "hartree",
        energyKind: "electronic",
        relativeEnergyKcal: null,
        sourceKind: "mechanism_report",
        candidateId: "",
        selectionSource: "",
        confidence: "",
        reason: "",
        confirmed: false,
        ref: ref,
        refProblem: ref ? "" : "next.pathview.ref_missing_title",
        charge: node.charge,
        multiplicity: node.multiplicity
      });
    });

    var steps = [];
    if (report && report.steps.length) {
      report.steps.forEach(function (step, index) {
        steps.push({
          key: "step:" + (step.id || index),
          index: index,
          label: step.id || ("step-" + (index + 1)),
          source: step.source,
          sink: step.sink,
          ts: step.ts,
          barrierForward: step.barrierForward,
          barrierReverse: step.barrierReverse,
          status: step.status,
          kind: "mechanism"
        });
      });
    } else if (energy) {
      energy.frames.forEach(function (frame) {
        steps.push({
          key: "frame:" + frame.index,
          index: frame.index,
          label: frame.label,
          energy: frame.energy,
          status: frame.status,
          x: frame.x,
          geometryRef: frame.geometryRef,
          kind: "frame"
        });
      });
    }

    var provenance = [];
    if (job) {
      provenance.push({ key: "next.pathview.prov_job", value: job.id || "—" });
      provenance.push({ key: "next.pathview.prov_workflow", value: job.workflow || "—" });
      provenance.push({ key: "next.pathview.prov_status", value: job.status || "—" });
      provenance.push({ key: "next.pathview.prov_attempt", value: String(job.attempt || 1) });
    }
    if (catalog) {
      provenance.push({
        key: "next.pathview.prov_catalog",
        value: "availability=" + (catalog.availability || "unknown") +
          (catalog.revision ? " · revision=" + catalog.revision : "")
      });
    }
    if (energy && (energy.viewType || energy.defaultSeries)) {
      provenance.push({
        key: "next.pathview.prov_energy",
        value: (energy.viewType ? "view=" + energy.viewType : "view=—") +
          (energy.defaultSeries
            ? " · " + energy.defaultSeries.label + " [" + (energy.defaultSeries.unit || "—") + "]"
            : " · 序列=—")
      });
    }
    if (review && review.status === "confirmed") {
      provenance.push({
        key: "next.pathview.prov_review",
        value: "revision=" + review.revision +
          (review.confirmedAt ? " · confirmed_at=" + review.confirmedAt : "") +
          " · selected=" + review.selected.length
      });
      provenance.push({
        key: "next.pathview.prov_review_source",
        value: [
          "task_status=" + (review.source.taskStatus || "—"),
          "frame_source=" + (review.source.frameSource || "—"),
          "scan_complete=" + (review.source.scanComplete == null ? "—" : String(review.source.scanComplete)),
          "data_source=" + (review.source.dataSource || "—")
        ].join(" · ")
      });
    }
    if (report) {
      provenance.push({
        key: "next.pathview.prov_study",
        value: (report.studyId || "—") + (report.jobId ? " · job=" + report.jobId : "")
      });
      if (report.hasProvenance) {
        provenance.push({
          key: "next.pathview.prov_report",
          value: shortJson(report.provenance)
        });
      }
    }

    var dedupedWarnings = [];
    warnings.forEach(function (message) {
      if (dedupedWarnings.indexOf(message) < 0) dedupedWarnings.push(message);
    });

    return {
      kind: kind,
      jobId: jobId,
      workflow: workflow,
      jobStatus: job ? job.status : (catalog ? catalog.jobStatus : ""),
      attempt: job ? job.attempt : 1,
      catalogRevision: catalog ? catalog.revision : "",
      catalogAvailability: catalog ? catalog.availability : "",
      reviewStatus: review ? review.status : "",
      review: review,
      energy: energy,
      structures: structures,
      steps: steps,
      provenance: provenance,
      warnings: dedupedWarnings
    };
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;
    var cleanup = [];

    function requestFnFor() {
      if (typeof deps.request === "function") return deps.request;
      if (deps.client && deps.client.transport &&
          typeof deps.client.transport.request === "function") {
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
      focusEntryId: "",
      jobInputValue: "",
      token: 0,
      status: "empty",
      busy: "none",
      job: null,
      catalog: null,
      energy: null,
      review: null,
      studies: [],
      report: null,
      model: null,
      warnings: [],
      error: "",
      retry: null,
      selection: { key: "", ref: null, state: "none", message: "", via: "", target: "" },
      emptyReason: ""
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
      var node = el(doc, "section", className || "acp-pathview-section");
      node.appendChild(i18nEl(doc, "h3", "acp-pathview-heading", headingKey));
      return node;
    }

    function summaryRow(labelKey, role) {
      var row = el(doc, "div", "acp-pathview-kv");
      row.appendChild(i18nEl(doc, "span", "acp-pathview-k", labelKey));
      var value = el(doc, "span", "acp-pathview-v", "—");
      value.setAttribute("data-role", role);
      row.appendChild(value);
      return row;
    }

    function buildDom() {
      var root = el(doc, "div", "acp-pathview");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-path-kind", "");
      root.setAttribute("data-acp-structure-count", "0");
      root.setAttribute("data-acp-step-count", "0");
      root.setAttribute("data-acp-warning-count", "0");
      root.setAttribute("data-acp-selection-state", "none");
      root.setAttribute("data-acp-selected-ref", "");

      var head = el(doc, "div", "acp-pathview-head");
      head.appendChild(i18nEl(doc, "h2", "acp-pathview-title", "next.pathview.title"));
      head.appendChild(i18nEl(doc, "p", "acp-pathview-sub", "next.pathview.subtitle"));
      root.appendChild(head);

      var body = el(doc, "div", "acp-pathview-body");
      root.appendChild(body);

      /* 1 — source */
      var source = section("next.pathview.source_title");
      source.appendChild(i18nEl(doc, "p", "acp-pathview-hint", "next.pathview.source_hint"));
      var sourceRow = el(doc, "div", "acp-pathview-row");
      var idLabel = i18nEl(doc, "label", "acp-pathview-label", "next.pathview.job_id");
      var jobInput = el(doc, "input", "acp-pathview-input");
      jobInput.type = "text";
      jobInput.setAttribute("data-role", "job-id");
      jobInput.setAttribute("placeholder", t("next.pathview.job_id_ph"));
      idLabel.appendChild(jobInput);
      state.ui.jobInput = jobInput;
      sourceRow.appendChild(idLabel);
      var loadBtn = el(doc, "button", "acp-pathview-btn", t("next.pathview.load"));
      loadBtn.type = "button";
      loadBtn.setAttribute("data-action", "load-job");
      loadBtn.setAttribute("data-i18n", "next.pathview.load");
      trackListener(loadBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        loadJob(state.ui.jobInput ? state.ui.jobInput.value : "");
      });
      state.ui.loadBtn = loadBtn;
      sourceRow.appendChild(loadBtn);
      var reloadBtn = el(doc, "button", "acp-pathview-btn ghost", t("next.pathview.reload"));
      reloadBtn.type = "button";
      reloadBtn.setAttribute("data-action", "reload");
      reloadBtn.setAttribute("data-i18n", "next.pathview.reload");
      trackListener(reloadBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        var id = state.job ? state.job.id : textOf(state.ui.jobInput && state.ui.jobInput.value);
        if (id) loadJob(id);
      });
      state.ui.reloadBtn = reloadBtn;
      sourceRow.appendChild(reloadBtn);
      source.appendChild(sourceRow);
      body.appendChild(source);

      /* empty placeholder (hidden unless the loaded path has no data) */
      var emptyBox = el(doc, "section", "acp-pathview-empty-box");
      emptyBox.setAttribute("data-role", "empty-state");
      emptyBox.hidden = true;
      emptyBox.appendChild(i18nEl(doc, "h3", "acp-pathview-heading", "next.pathview.empty_title"));
      var emptyHint = i18nEl(doc, "p", "acp-pathview-hint", "next.pathview.empty_hint");
      emptyHint.setAttribute("data-role", "empty-hint");
      emptyBox.appendChild(emptyHint);
      state.ui.emptyBox = emptyBox;
      state.ui.emptyHint = emptyHint;
      body.appendChild(emptyBox);

      /* 2 — summary */
      var summary = section("next.pathview.summary_title");
      summary.setAttribute("data-role", "summary-section");
      var grid = el(doc, "div", "acp-pathview-grid");
      var summaryFields = {};
      [
        ["next.pathview.summary_kind", "summary-kind"],
        ["next.pathview.summary_job", "summary-job"],
        ["next.pathview.summary_workflow", "summary-workflow"],
        ["next.pathview.summary_status", "summary-status"],
        ["next.pathview.summary_attempt", "summary-attempt"],
        ["next.pathview.summary_catalog", "summary-catalog"],
        ["next.pathview.summary_catalog_revision", "summary-revision"],
        ["next.pathview.summary_review", "summary-review"],
        ["next.pathview.summary_series", "summary-series"]
      ].forEach(function (pair) {
        var row = summaryRow(pair[0], pair[1]);
        summaryFields[pair[1]] = row.children ? row.children[row.children.length - 1] : null;
        grid.appendChild(row);
      });
      summary.appendChild(grid);
      state.ui.summary = summary;
      state.ui.summaryFields = summaryFields;
      body.appendChild(summary);

      /* 3 — structures */
      var structures = section("next.pathview.structures_title");
      structures.appendChild(i18nEl(doc, "p", "acp-pathview-hint", "next.pathview.structures_hint"));
      state.ui.structures = el(doc, "div", "acp-pathview-table");
      state.ui.structures.setAttribute("data-role", "structures");
      state.ui.structuresEmpty = i18nEl(doc, "p", "acp-pathview-empty", "next.pathview.structures_empty");
      state.ui.structuresEmpty.setAttribute("data-role", "structures-empty");
      structures.appendChild(state.ui.structures);
      structures.appendChild(state.ui.structuresEmpty);
      body.appendChild(structures);

      /* 4 — steps */
      var steps = section("next.pathview.steps_title");
      steps.appendChild(i18nEl(doc, "p", "acp-pathview-hint", "next.pathview.steps_hint"));
      state.ui.steps = el(doc, "div", "acp-pathview-table");
      state.ui.steps.setAttribute("data-role", "steps");
      state.ui.stepsEmpty = i18nEl(doc, "p", "acp-pathview-empty", "next.pathview.steps_empty");
      state.ui.stepsEmpty.setAttribute("data-role", "steps-empty");
      steps.appendChild(state.ui.steps);
      steps.appendChild(state.ui.stepsEmpty);
      body.appendChild(steps);

      /* 5 — provenance */
      var provenance = section("next.pathview.provenance_title");
      provenance.appendChild(i18nEl(doc, "p", "acp-pathview-hint", "next.pathview.provenance_hint"));
      state.ui.provenance = el(doc, "dl", "acp-pathview-provenance");
      state.ui.provenance.setAttribute("data-role", "provenance");
      state.ui.provenanceEmpty = i18nEl(doc, "p", "acp-pathview-empty", "next.pathview.provenance_empty");
      state.ui.provenanceEmpty.setAttribute("data-role", "provenance-empty");
      provenance.appendChild(state.ui.provenance);
      provenance.appendChild(state.ui.provenanceEmpty);
      body.appendChild(provenance);

      /* selection + warnings */
      var tail = section("next.pathview.selection_title");
      state.ui.selection = el(doc, "p", "acp-pathview-selection", t("next.pathview.selection_none"));
      state.ui.selection.setAttribute("data-role", "selection");
      state.ui.selection.setAttribute("aria-live", "polite");
      tail.appendChild(state.ui.selection);
      tail.appendChild(i18nEl(doc, "p", "acp-pathview-note", "next.pathview.measure_note"));
      state.ui.warnings = el(doc, "ul", "acp-pathview-warnings");
      state.ui.warnings.setAttribute("data-role", "warnings");
      tail.appendChild(state.ui.warnings);
      body.appendChild(tail);

      /* overlay */
      var overlay = el(doc, "div", "acp-pathview-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = true;
      var overlayText = el(doc, "p", "acp-pathview-overlay-text", "");
      overlayText.setAttribute("data-role", "overlay-message");
      overlay.appendChild(overlayText);
      var retryBtn = el(doc, "button", "acp-pathview-btn", t("next.pathview.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.pathview.retry");
      retryBtn.hidden = true;
      trackListener(retryBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        if (typeof state.retry === "function") state.retry();
      });
      overlay.appendChild(retryBtn);
      state.ui.overlay = overlay;
      state.ui.overlayMessage = overlayText;
      state.ui.retry = retryBtn;
      body.appendChild(overlay);

      state.root = root;
      state.ui.body = body;
      state.ui.summaryGrid = grid;
    }

    /* ── rendering ──────────────────────────────────────────────── */

    function setText(node, value) {
      if (node) node.textContent = value == null ? "" : String(value);
    }

    function formatNumber(value) {
      if (!finiteNumber(value)) return t("next.pathview.none");
      var abs = Math.abs(value);
      var digits = abs !== 0 && abs < 0.01 ? 6 : (abs < 100 ? 4 : 3);
      return value.toFixed(digits);
    }

    function formatEnergy(value, unit, kind) {
      if (!finiteNumber(value)) return t("next.pathview.no_energy");
      var unitText = unit || (kind === "relative_energy" ? t("next.pathview.unit_kcal")
        : (kind ? t("next.pathview.unit_hartree") : ""));
      return formatNumber(value) + (unitText ? " " + unitText : "");
    }

    function kindLabel(kind) {
      var key = {
        pes: "next.pathview.kind_pes",
        scan: "next.pathview.kind_scan",
        mechanism: "next.pathview.kind_mechanism",
        job: "next.pathview.kind_job"
      }[kind];
      return t(key || "next.pathview.kind_unknown");
    }

    function sourceLabel(structure) {
      if (structure.sourceKind === "manual_review") return t("next.pathview.source_manual_review");
      if (structure.sourceKind === "formal_result") return t("next.pathview.source_formal_result");
      if (structure.sourceKind === "mechanism_report") return t("next.pathview.source_mechanism");
      return t("next.pathview.source_other");
    }

    function renderStateChrome() {
      if (!state.root) return;
      var model = state.model;
      state.root.setAttribute("data-acp-module-state", state.status);
      state.root.setAttribute("data-acp-busy", state.busy);
      state.root.setAttribute("data-acp-path-kind", model ? model.kind : "");
      state.root.setAttribute("data-acp-structure-count", String(model ? model.structures.length : 0));
      state.root.setAttribute("data-acp-step-count", String(model ? model.steps.length : 0));
      state.root.setAttribute("data-acp-warning-count", String(model ? model.warnings.length : 0));
      state.root.setAttribute("data-acp-selection-state", state.selection.state);
      state.root.setAttribute("data-acp-selected-ref", state.selection.ref
        ? JSON.stringify(state.selection.ref) : "");
      state.root.setAttribute("data-acp-focus", state.focusEntryId || "");
      if (state.ui.loadBtn) state.ui.loadBtn.disabled = state.busy !== "none";
      if (state.ui.reloadBtn) state.ui.reloadBtn.disabled = state.busy !== "none";
      if (state.ui.jobInput) state.ui.jobInput.disabled = state.busy !== "none";
    }

    function renderSummary() {
      var model = state.model;
      var fields = state.ui.summaryFields || {};
      var show = state.status === "ready";
      if (state.ui.summary) state.ui.summary.hidden = !show;
      if (!show || !model) return;
      setText(fields["summary-kind"], kindLabel(model.kind));
      setText(fields["summary-job"], model.jobId || "—");
      setText(fields["summary-workflow"], model.workflow || "—");
      setText(fields["summary-status"], model.jobStatus || "—");
      setText(fields["summary-attempt"], String(model.attempt || 1));
      setText(fields["summary-catalog"], model.catalogAvailability || "—");
      setText(fields["summary-revision"], model.catalogRevision || "—");
      setText(fields["summary-review"], model.reviewStatus ||
        (model.kind === "pes" ? "pending" : "—"));
      var series = model.energy && model.energy.defaultSeries;
      setText(fields["summary-series"], series
        ? (series.label + (series.unit ? " [" + series.unit + "]" : ""))
        : "—");
    }

    function structureRow(structure) {
      var row = el(doc, "div", "acp-pathview-row-item");
      row.setAttribute("data-structure-key", structure.key);
      row.setAttribute("data-structure-id", structure.id || "");
      row.setAttribute("data-structure-role", structure.role || "unknown");
      row.setAttribute("data-ref-state", structure.ref ? "ready" : "missing");
      if (state.focusEntryId && structure.entryId === state.focusEntryId) {
        row.setAttribute("data-focus", "true");
      }

      var role = i18nEl(doc, "span", "acp-pathview-badge acp-pathview-badge--" + (structure.role || "unknown"),
        roleLabelKey(structure.role));
      role.setAttribute("data-role", "structure-role");
      row.appendChild(role);

      var label = el(doc, "span", "acp-pathview-structure-label", structure.label || structure.id || "—");
      label.setAttribute("data-role", "structure-label");
      row.appendChild(label);

      var frame = el(doc, "span", "acp-pathview-cell", structure.frameIndex == null
        ? "—" : String(structure.frameIndex));
      frame.setAttribute("data-role", "structure-frame");
      row.appendChild(frame);

      var energy = el(doc, "span", "acp-pathview-cell",
        formatEnergy(structure.energyValue, structure.energyUnit, structure.energyKind));
      energy.setAttribute("data-role", "structure-energy");
      energy.setAttribute("data-energy-value",
        structure.energyValue == null ? "" : String(structure.energyValue));
      energy.setAttribute("data-energy-unit", structure.energyUnit || "");
      if (structure.relativeEnergyKcal != null) {
        energy.setAttribute("data-relative-energy-kcal", String(structure.relativeEnergyKcal));
      }
      row.appendChild(energy);

      var source = el(doc, "span", "acp-pathview-cell acp-pathview-cell--source", sourceLabel(structure));
      source.setAttribute("data-role", "structure-source");
      if (structure.candidateId) source.setAttribute("data-candidate-id", structure.candidateId);
      if (structure.selectionSource) source.setAttribute("data-selection-source", structure.selectionSource);
      row.appendChild(source);

      var action = el(doc, "button", "acp-pathview-btn small", t("next.pathview.open_structure"));
      action.type = "button";
      action.setAttribute("data-action", "open-structure");
      action.setAttribute("data-structure-key", structure.key);
      action.setAttribute("data-i18n", "next.pathview.open_structure");
      if (!structure.ref) {
        action.disabled = true;
        action.setAttribute("title", t(structure.refProblem || "next.pathview.ref_missing"));
        action.setAttribute("data-ref-problem", structure.refProblem || "next.pathview.ref_missing");
      }
      trackListener(action, "click", function () {
        onOpenStructure(structure.key);
      });
      row.appendChild(action);
      return row;
    }

    function renderStructures() {
      if (!state.ui.structures) return;
      clearChildren(state.ui.structures);
      var model = state.model;
      var rows = model ? model.structures : [];
      state.ui.structures.hidden = !rows.length;
      state.ui.structuresEmpty.hidden = !!rows.length || state.status !== "ready";
      rows.forEach(function (structure) {
        state.ui.structures.appendChild(structureRow(structure));
      });
    }

    function stepRow(step) {
      var row = el(doc, "div", "acp-pathview-row-item acp-pathview-row-item--step");
      row.setAttribute("data-step-key", step.key);
      row.setAttribute("data-step-kind", step.kind || "");
      var label = el(doc, "span", "acp-pathview-structure-label", step.label || "—");
      label.setAttribute("data-role", "step-label");
      row.appendChild(label);
      if (step.kind === "mechanism") {
        [["step-source", step.source], ["step-sink", step.sink], ["step-ts", step.ts]].forEach(function (pair) {
          var cell = el(doc, "span", "acp-pathview-cell", pair[1] || "—");
          cell.setAttribute("data-role", pair[0]);
          row.appendChild(cell);
        });
        var barriers = el(doc, "span", "acp-pathview-cell",
          formatNumber(step.barrierForward) + " / " + formatNumber(step.barrierReverse));
        barriers.setAttribute("data-role", "step-barriers");
        row.appendChild(barriers);
        var status = el(doc, "span", "acp-pathview-cell", step.status || "—");
        status.setAttribute("data-role", "step-status");
        row.appendChild(status);
      } else {
        var frame = el(doc, "span", "acp-pathview-cell", String(step.index));
        frame.setAttribute("data-role", "frame-index");
        row.appendChild(frame);
        var energy = el(doc, "span", "acp-pathview-cell", formatEnergy(step.energy, "", ""));
        energy.setAttribute("data-role", "frame-energy");
        energy.setAttribute("data-energy-value", step.energy == null ? "" : String(step.energy));
        row.appendChild(energy);
        var frameStatus = el(doc, "span", "acp-pathview-cell", step.status || "—");
        frameStatus.setAttribute("data-role", "frame-status");
        row.appendChild(frameStatus);
        var geometry = el(doc, "span", "acp-pathview-cell acp-pathview-cell--source",
          step.geometryRef || "—");
        geometry.setAttribute("data-role", "frame-geometry");
        row.appendChild(geometry);
      }
      return row;
    }

    function renderSteps() {
      if (!state.ui.steps) return;
      clearChildren(state.ui.steps);
      var model = state.model;
      var rows = model ? model.steps : [];
      state.ui.steps.hidden = !rows.length;
      state.ui.stepsEmpty.hidden = !!rows.length || state.status !== "ready";
      rows.forEach(function (step) {
        state.ui.steps.appendChild(stepRow(step));
      });
    }

    function renderProvenance() {
      if (!state.ui.provenance) return;
      clearChildren(state.ui.provenance);
      var model = state.model;
      var rows = model ? model.provenance : [];
      state.ui.provenance.hidden = !rows.length;
      state.ui.provenanceEmpty.hidden = !!rows.length || state.status !== "ready";
      rows.forEach(function (item) {
        var wrap = el(doc, "div", "acp-pathview-kv");
        var key = i18nEl(doc, "dt", "acp-pathview-k", item.key);
        var value = el(doc, "dd", "acp-pathview-v", item.value == null ? "—" : String(item.value));
        value.setAttribute("data-role", "provenance-value");
        wrap.appendChild(key);
        wrap.appendChild(value);
        state.ui.provenance.appendChild(wrap);
      });
    }

    function renderSelection() {
      if (!state.ui.selection) return;
      var text = "";
      if (state.selection.state === "sent") {
        text = t("next.pathview.selection_sent", {
          ref: state.selection.ref ? (state.selection.ref.kind + ":" +
            (state.selection.ref.entryId || state.selection.ref.path || "inline")) : "—",
          via: state.selection.via || "context"
        });
      } else if (state.selection.state === "unavailable") {
        text = t("next.pathview.selection_unavailable", { message: state.selection.message || "" });
      } else if (state.selection.state === "error") {
        text = t("next.pathview.selection_error", { message: state.selection.message || "" });
      } else {
        text = t("next.pathview.selection_none");
      }
      setText(state.ui.selection, text);
      state.ui.selection.setAttribute("data-acp-selection-state", state.selection.state);
    }

    function renderWarnings() {
      if (!state.ui.warnings) return;
      clearChildren(state.ui.warnings);
      var model = state.model;
      var warnings = model ? model.warnings : [];
      state.ui.warnings.hidden = !warnings.length;
      if (!warnings.length) return;
      var heading = el(doc, "li", "acp-pathview-warning-heading", t("next.pathview.partial"));
      state.ui.warnings.appendChild(heading);
      warnings.forEach(function (message) {
        var item = el(doc, "li", "acp-pathview-warning", message);
        item.setAttribute("data-role", "warning");
        state.ui.warnings.appendChild(item);
      });
    }

    function renderOverlay() {
      if (!state.ui.overlay) return;
      var loading = state.status === "loading";
      var failed = state.status === "error";
      state.ui.overlay.hidden = !(loading || failed);
      state.ui.retry.hidden = !failed;
      if (loading) {
        setText(state.ui.overlayMessage, t("next.pathview.loading_title"));
        state.ui.overlay.setAttribute("data-overlay-kind", "loading");
      } else if (failed) {
        setText(state.ui.overlayMessage, state.error || t("next.pathview.error_title"));
        state.ui.overlay.setAttribute("data-overlay-kind", "error");
      } else {
        setText(state.ui.overlayMessage, "");
        state.ui.overlay.removeAttribute("data-overlay-kind");
      }
    }

    function renderEmpty() {
      if (!state.ui.emptyBox) return;
      var show = state.status === "empty";
      state.ui.emptyBox.hidden = !show;
      if (!show) return;
      var hintKey = state.emptyReason || "next.pathview.empty_hint";
      setText(state.ui.emptyHint, t(hintKey));
      state.ui.emptyHint.setAttribute("data-i18n", hintKey);
    }

    function render() {
      if (!state.mounted || state.disposed || !state.root) return;
      renderStateChrome();
      renderEmpty();
      renderSummary();
      renderStructures();
      renderSteps();
      renderProvenance();
      renderSelection();
      renderWarnings();
      renderOverlay();
    }

    /* ── selection -> existing viewer ───────────────────────────── */

    function findStructure(key) {
      var model = state.model;
      if (!model) return null;
      for (var i = 0; i < model.structures.length; i++) {
        if (model.structures[i].key === key) return model.structures[i];
      }
      return null;
    }

    function onOpenStructure(key) {
      if (!state.mounted || state.disposed || state.busy !== "none") return;
      var structure = findStructure(key);
      if (!structure) return;
      if (!structure.ref) {
        state.selection = {
          key: key, ref: null, state: "unavailable",
          message: t(structure.refProblem || "next.pathview.ref_missing"),
          via: "", target: ""
        };
        render();
        return;
      }
      var commands = deps.commands || null;
      if (!commands || typeof commands.openStructure !== "function") {
        state.selection = {
          key: key, ref: structure.ref, state: "unavailable",
          message: t("next.pathview.no_commands"), via: "", target: ""
        };
        render();
        return;
      }
      var payload = Object.assign({ source: "path-view" }, structure.ref);
      var result = null;
      try {
        result = commands.openStructure(payload);
      } catch (error) {
        state.selection = {
          key: key, ref: structure.ref, state: "error",
          message: describeError(error), via: "", target: ""
        };
        render();
        return;
      }
      state.selection = {
        key: key,
        ref: structure.ref,
        state: "sent",
        message: "",
        via: result && typeof result.via === "string" ? result.via : "context",
        target: result && result.targetInstanceId ? String(result.targetInstanceId) : ""
      };
      render();
    }

    /* ── loading ────────────────────────────────────────────────── */

    function resetData(keepInput) {
      state.job = null;
      state.catalog = null;
      state.energy = null;
      state.review = null;
      state.studies = [];
      state.report = null;
      state.model = null;
      state.warnings = [];
      state.emptyReason = "";
      state.selection = { key: "", ref: null, state: "none", message: "", via: "", target: "" };
      state.retry = null;
      if (!keepInput) state.jobInputValue = "";
      state.token += 1;
    }

    function settle(promise) {
      return Promise.resolve(promise).then(function (value) {
        return { ok: true, value: value };
      }, function (error) {
        return { ok: false, error: error };
      });
    }

    function finishLoad(gen, token) {
      if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
      state.busy = "none";
      var catalog = state.catalog;
      var catalogFailed = state.catalogError;
      delete state.catalogError;
      if (catalogFailed) {
        state.status = "error";
        state.error = t("next.pathview.err.catalog_endpoint", {
          status: catalogFailed.status == null ? "?" : String(catalogFailed.status),
          message: catalogFailed.message || ""
        });
        state.retry = function () { loadJob(state.job ? state.job.id : ""); };
        state.model = null;
        render();
        return { ok: false, error: state.error };
      }
      var review = state.review;
      var model = buildPathModel({
        job: state.job,
        catalog: catalog,
        energy: state.energy,
        review: review,
        report: state.report,
        warnings: state.warnings
      });
      state.model = model;
      if (model.structures.length || model.steps.length) {
        state.status = "ready";
        state.error = "";
        state.emptyReason = "";
      } else {
        state.status = "empty";
        state.error = "";
        if (review && review.status !== "confirmed" && model.workflow === "PESsearch") {
          state.emptyReason = "next.pathview.empty_pes_pending";
        } else if (model.kind === "mechanism") {
          state.emptyReason = "next.pathview.empty_no_study";
        } else {
          state.emptyReason = "next.pathview.empty_no_structures";
        }
      }
      state.retry = function () { loadJob(state.job ? state.job.id : ""); };
      render();
      return { ok: true, model: model };
    }

    function loadMechanism(jobId, gen, token) {
      if (state.job && state.job.workflow !== "mechanism") return Promise.resolve();
      return settle(requestFn(studiesQuery(jobId), { method: "GET" }))
        .then(function (result) {
          if (!alive(gen) || state.token !== token) return;
          if (!result.ok) {
            state.warnings.push(t("next.pathview.partial_detail", {
              message: "mechanism-studies: " + describeError(result.error)
            }));
            return;
          }
          state.studies = normalizeStudyList(result.value);
          if (!state.studies.length) return;
          var studyId = state.studies[0].id;
          return settle(requestFn(studyReportPath(studyId), { method: "GET" }))
            .then(function (reportResult) {
              if (!alive(gen) || state.token !== token) return;
              if (!reportResult.ok) {
                state.warnings.push(t("next.pathview.partial_detail", {
                  message: "mechanism report: " + describeError(reportResult.error)
                }));
                return;
              }
              state.report = normalizeMechanismReport(reportResult.value);
            });
        });
    }

    function loadSecondary(jobId, gen, token) {
      var workflow = state.job ? state.job.workflow : "";
      var isPes = workflow === "PESsearch";
      var catalogRequest = settle(requestFn(catalogPath(jobId), { method: "GET" }));
      var jobs = [
        catalogRequest,
        settle(requestFn(energyPath(jobId), { method: "GET" }))
      ];
      if (isPes) {
        jobs.push(settle(requestFn(pesReviewPath(jobId), { method: "GET" })));
      }
      return Promise.all(jobs).then(function (results) {
        if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
        if (!results[0].ok) {
          state.catalogError = {
            status: errorStatus(results[0].error),
            message: describeError(results[0].error)
          };
          return finishLoad(gen, token);
        }
        var catalog = normalizeCatalog(results[0].value);
        if (catalog.availability === "error" && !catalog.entries.length) {
          state.catalogError = {
            status: 200,
            message: t("next.pathview.err.invalid_catalog")
          };
          return finishLoad(gen, token);
        }
        state.catalog = catalog;
        if (results[1].ok) {
          state.energy = normalizeEnergyGraph(results[1].value);
          if (state.energy.warnings.length) {
            state.energy.warnings.forEach(function (message) { state.warnings.push(message); });
          }
        } else {
          state.warnings.push(t("next.pathview.partial_detail", {
            message: "energy-graph: " + describeError(results[1].error)
          }));
        }
        if (isPes) {
          var reviewResult = results[2];
          if (reviewResult && reviewResult.ok) {
            state.review = normalizePesReview(reviewResult.value);
          } else if (reviewResult) {
            state.warnings.push(t("next.pathview.partial_detail", {
              message: "pes/review: " + describeError(reviewResult.error)
            }));
          }
        }
        return loadMechanism(jobId, gen, token).then(function () {
          return finishLoad(gen, token);
        });
      });
    }

    function loadJob(rawId) {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      var id = textOf(rawId).trim();
      if (!id) {
        resetData(true);
        state.status = "empty";
        state.error = "";
        state.emptyReason = "";
        render();
        return Promise.resolve({ ok: false, reason: "id" });
      }
      if (!requestFn) {
        resetData(true);
        state.status = "error";
        state.error = t("next.pathview.err.client_missing");
        state.retry = function () { loadJob(id); };
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }
      resetData(true);
      state.jobInputValue = id;
      var token = state.token;
      var gen = state.generation;
      state.status = "loading";
      state.busy = "load";
      state.error = "";
      render();
      return settle(requestFn(jobPath(id), { method: "GET" })).then(function (result) {
        if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
        if (!result.ok) {
          state.busy = "none";
          state.status = "error";
          state.error = t("next.pathview.error_title") + "：" + describeError(result.error);
          state.retry = function () { loadJob(id); };
          render();
          return { ok: false, error: state.error };
        }
        var job = normalizeJob(result.value);
        if (!job) {
          state.busy = "none";
          state.status = "error";
          state.error = t("next.pathview.error_title");
          state.retry = function () { loadJob(id); };
          render();
          return { ok: false, error: state.error };
        }
        if (!job.id) job.id = id;
        state.job = job;
        return loadSecondary(job.id, gen, token);
      });
    }

    /* ── context / lifecycle ────────────────────────────────────── */

    function applySnapshot(snapshot, options) {
      if (!state.mounted || state.disposed) return;
      options = options || {};
      snapshot = isObject(snapshot) ? snapshot : {};
      var taskRef = isObject(snapshot.taskRef) ? snapshot.taskRef : null;
      var jobId = taskRef && taskRef.jobId ? String(taskRef.jobId) : "";
      var structureRef = isObject(snapshot.structureRef) ? snapshot.structureRef : null;
      state.focusEntryId = structureRef && structureRef.kind === "structure-entry"
        ? textOf(structureRef.entryId) : "";
      if (state.focusJobId !== jobId) {
        state.focusJobId = jobId;
        if (jobId) {
          if (state.ui.jobInput) state.ui.jobInput.value = jobId;
          loadJob(jobId);
        } else {
          resetData(true);
          state.status = "empty";
          state.error = "";
          state.emptyReason = "";
          render();
        }
        return;
      }
      if (!options.skipRender) render();
    }

    function onContextNotify() {
      if (!state.mounted || state.disposed || !context ||
          typeof context.getSnapshot !== "function") return;
      try {
        applySnapshot(context.getSnapshot());
      } catch (error) { /* malformed snapshot never breaks the panel */ }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("path-view mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("path-view: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("path-view: already mounted (dispose() first)"));
      }
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.status = "empty";
      state.busy = "none";
      state.focusJobId = "__init__";
      state.focusEntryId = "";
      state.jobInputValue = "";
      state.token = 0;
      state.job = null;
      state.catalog = null;
      state.energy = null;
      state.review = null;
      state.studies = [];
      state.report = null;
      state.model = null;
      state.warnings = [];
      state.error = "";
      state.retry = null;
      state.emptyReason = "";
      state.selection = { key: "", ref: null, state: "none", message: "", via: "", target: "" };
      state.ui = {};
      cleanup.length = 0;

      if (api.context) context = api.context;
      buildDom();
      hostEl.appendChild(state.root);
      render();

      var snapshot = null;
      if (context && typeof context.getSnapshot === "function") {
        try { snapshot = context.getSnapshot(); } catch (error) { snapshot = null; }
      }
      if (typeof deps.initialJobId === "string" && deps.initialJobId) {
        applySnapshot(snapshot || {}, { skipRender: true });
        if (state.ui.jobInput) state.ui.jobInput.value = deps.initialJobId;
        loadJob(deps.initialJobId);
      } else if (snapshot) {
        applySnapshot(snapshot, { skipRender: true });
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
      render();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.token += 1;
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
      state.job = null;
      state.catalog = null;
      state.energy = null;
      state.review = null;
      state.studies = [];
      state.report = null;
      state.model = null;
      state.warnings = [];
      state.retry = null;
      state.selection = { key: "", ref: null, state: "none", message: "", via: "", target: "" };
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
      classifyRole: classifyRole,
      normalizeJob: normalizeJob,
      normalizeCatalog: normalizeCatalog,
      normalizeEnergyGraph: normalizeEnergyGraph,
      normalizePesReview: normalizePesReview,
      normalizeStudyList: normalizeStudyList,
      normalizeMechanismReport: normalizeMechanismReport,
      canonicalRef: canonicalRef,
      structureRefForEntry: structureRefForEntry,
      structureRefForNode: structureRefForNode,
      refKey: refKey,
      buildPathModel: buildPathModel,
      jobPath: jobPath,
      catalogPath: catalogPath,
      energyPath: energyPath,
      pesReviewPath: pesReviewPath,
      studiesQuery: studiesQuery,
      studyReportPath: studyReportPath,
      roleLabelKey: roleLabelKey,
      STRUCTURE_KINDS: STRUCTURE_KINDS,
      STUDY_LIMIT: STUDY_LIMIT,
      ROUTES: ROUTES,
      i18n: ZH
    }
  };
})();

