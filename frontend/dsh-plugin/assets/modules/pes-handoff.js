/* file: js/modules/pes-handoff/module.js */
/*
 * ACP 3.0 module — pes-handoff (Track A / A-51 · P17 "与 PES / Batch 的衔接").
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["pes-handoff"]`); the Shell loader resolves the factory
 * and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (A-51 only):
 *   - read the already-confirmed PES selections (`GET /jobs/{id}/pes/review`) or
 *     the saved scan frame candidates (`GET /jobs/{id}/frame-candidates`), join
 *     the server-side energy graph for energies, and display the server
 *     rank/label/provenance fields (no client-side science ranking);
 *   - create the BatchOptimize hand-off through the EXISTING preview/submit
 *     routes: `POST /batch-optimize/config-preview` (server effective config),
 *     `POST /preflight` (zero-side-effect feasibility + plan_digest) and
 *     `POST /jobs` with an `Idempotency-Key` (per-candidate receipt). No new
 *     backend route is invented.
 *
 * Server layout constraint (verified in code, not guessed):
 *   `src/acp/api/submission_contract.py` refuses >1 `batch_structures` item
 *   ("layout_limit") and `src/acp/calculations/batch/engine.py` raises for
 *   `single_flat` + `len(items) != 1`; the scheduler pins `--layout-mode
 *   single_flat` for every BatchOptimize dispatch. Therefore the hand-off is
 *   ONE BatchOptimize task per selected candidate, each carrying exactly one
 *   structure (`source_type:"xyz_text"`) plus `selected_ids:[candidateId]` and
 *   `source_job_id` provenance. The body uses only keys accepted by
 *   `submission_contract.WORKFLOW_CONTRACTS["BatchOptimize"]`.
 *
 * Candidate geometry is read through the existing file route
 * `GET /jobs/{id}/files/RESULT/structures/{candidateId}.xyz` — the same
 * documented "confirmed candidate" artifact both writers produce
 * (`calculations/pes/review.py` and `results/frame_candidates.py`). The module
 * never builds a filesystem path and never guesses a directory.
 *
 * Data flows through the injected client seam only
 * (`deps.request` -> `deps.client.transport.request` ->
 * `window.ACPClient.transport.request`); this module performs no direct network
 * call. API paths are the route constants below plus `encodeURIComponent(id)`.
 *
 * Four data states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error. Command surfaces are separate:
 * `data-acp-busy` = none | load | preview | submit, `data-acp-preview-state` =
 * idle | previewing | previewed | stale | error, `data-acp-handoff-state` =
 * idle | submitting | submitted | error, `data-acp-confirm-open`. Every async
 * result is guarded by the mount generation plus a per-action token, so dispose
 * / a newer job / a newer preview can never paint into the DOM.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "pes-handoff";
  var API = "/api/v1";
  var ROUTES = {
    jobs: API + "/jobs/",
    pesReview: "/pes/review",
    frameCandidates: "/frame-candidates",
    energyGraph: "/energy-graph",
    files: "/files/",
    methodCatalog: API + "/method-catalog",
    configPreview: API + "/batch-optimize/config-preview",
    preflight: API + "/preflight",
    submit: API + "/jobs"
  };
  var DEFAULT_PROFILE = "opt_freq";
  var REQUEST_TIMEOUT_MS = 30000;
  var MAX_TEXT = 400;
  var CONFIRMED_ROLES = { ts: "ts", int: "intermediate", intermediate: "intermediate" };

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["batch.handoff"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["batch.handoff"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.handoff.errorTitle", messageKey: "next.handoff.errorMessage" }
  };

  /* Local zh copy. The Shell dictionary is consulted first; `ACPShell.i18n.t()`
   * echoes the key when no translation exists, so these values are the real
   * fallback until the shell i18n batch adds `next.handoff.*` (README §8 lists
   * the full key set). Static chrome also carries `data-i18n`. */
  var ZH = {
    "next.handoff.title": "路径 → Batch 衔接",
    "next.handoff.subtitle": "PES / 扫描候选 → 既有 BatchOptimize 预览与提交 · 显式确认 + 逐项回执",
    "next.handoff.source_title": "1 · 候选来源",
    "next.handoff.source_hint": "自动跟随当前任务；也可输入任务 ID。候选只来自服务端已确认记录。",
    "next.handoff.job_id": "任务 ID",
    "next.handoff.job_id_ph": "例如 20261031_001_PESsearch",
    "next.handoff.load": "载入候选",
    "next.handoff.reload": "重新载入",
    "next.handoff.summary_title": "2 · 候选与排序",
    "next.handoff.summary_job": "任务",
    "next.handoff.summary_workflow": "工作流",
    "next.handoff.summary_status": "状态",
    "next.handoff.summary_source": "候选来源",
    "next.handoff.summary_review": "选点修订",
    "next.handoff.summary_confirmed": "确认时间",
    "next.handoff.summary_count": "候选数",
    "next.handoff.summary_selected": "已选",
    "next.handoff.candidates_hint": "排名只显示服务端字段；缺失显示 —，本模块不排序、不计算科学排名。能量来自既有 energy-graph。",
    "next.handoff.candidates_empty": "该任务没有可衔接的已确认候选。",
    "next.handoff.candidates_pending": "该 PES 任务尚未确认选点（pes/review=pending）；先在能量查看器确认选点。",
    "next.handoff.rank_none": "—（服务端未提供）",
    "next.handoff.rank_value": "第 {rank}",
    "next.handoff.label_value": "标签 {label}（服务端序 {index}）",
    "next.handoff.energy_none": "—（能量图未提供）",
    "next.handoff.select_all": "全选",
    "next.handoff.select_none": "清空",
    "next.handoff.selection_summary": "已选 {selected} / 共 {total}",
    "next.handoff.profile_hint": "profile 列表来自服务端方法目录（GET /method-catalog），不硬编码。",
    "next.handoff.profile_label": "优化 profile",
    "next.handoff.preview_title": "3 · 提交预览",
    "next.handoff.preview": "预览 Batch 提交",
    "next.handoff.preview_hint": "预览只读：读取候选结构文本 → 既有 batch-optimize 配置预览 → POST /preflight。预览不创建任务。",
    "next.handoff.previewing": "正在预览…",
    "next.handoff.preview_idle": "尚未预览。预览不会创建任务。",
    "next.handoff.preview_ok": "预览通过：{count} 个候选，profile={profile}。",
    "next.handoff.preview_error": "预览失败：{message}",
    "next.handoff.preview_config": "服务端生效配置 config_key：{key}",
    "next.handoff.preview_roles": "角色生效配置 — INT: {int}；TS: {ts}",
    "next.handoff.preview_item": "候选 {candidate}：{feasible} · plan_digest {digest}",
    "next.handoff.preview_reasons": "阻塞项：{reasons}",
    "next.handoff.preview_advisories": "提示：{advisories}",
    "next.handoff.preview_stale": "选择或 profile 已变化，请重新预览。",
    "next.handoff.handoff_title": "4 · 确认与回执",
    "next.handoff.confirm_hint": "每个候选提交一个独立的 BatchOptimize 任务（服务端 single_flat 布局限制）；必须显式确认。",
    "next.handoff.request_confirm": "确认并提交…",
    "next.handoff.confirm_title": "确认提交",
    "next.handoff.confirm_body": "将提交 {count} 个 BatchOptimize 任务：{ids}（profile={profile}）。确认后不可撤回；回执按任务返回。",
    "next.handoff.confirm_submit": "确认提交",
    "next.handoff.confirm_cancel": "取消",
    "next.handoff.submitting": "正在提交…",
    "next.handoff.receipt_title": "回执",
    "next.handoff.receipt_empty": "尚未提交。",
    "next.handoff.receipt_item": "候选 {candidate} → 任务 {jobId} · 状态 {status} · 第 {attempt} 次 · plan_digest {digest}",
    "next.handoff.receipt_replay": "幂等重放（返回既有任务）。",
    "next.handoff.receipt_error": "提交失败：{message}",
    "next.handoff.receipt_invalid": "服务端返回缺少 job_id，未视为回执。",
    "next.handoff.open_task": "打开任务",
    "next.handoff.submitted": "已提交 {count} 个任务；逐项回执见上。",
    "next.handoff.submit_partial": "部分候选未提交：{message}",
    "next.handoff.empty_title": "尚未载入候选",
    "next.handoff.empty_hint": "输入任务 ID 载入，或在有任务上下文时跟随当前任务。",
    "next.handoff.loading_title": "正在载入候选…",
    "next.handoff.error_title": "候选加载失败",
    "next.handoff.errorTitle": "路径 → Batch 衔接不可用",
    "next.handoff.errorMessage": "pes-handoff 模块加载失败，请查看控制台。",
    "next.handoff.retry": "重试",
    "next.handoff.partial": "部分数据不可用",
    "next.handoff.partial_detail": "部分数据不可用：{message}",
    "next.handoff.err.client_missing": "客户端未注入（ACPClient.transport），无法读取或提交。",
    "next.handoff.err.candidates_endpoint": "候选端点不可用（{endpoint} HTTP {status}）：{message}；未提交任何任务。",
    "next.handoff.err.no_selection": "请先选择至少一个候选。",
    "next.handoff.err.no_profile": "服务端方法目录未提供 BatchOptimize profile，无法提交。",
    "next.handoff.err.catalog_endpoint": "方法目录端点不可用（{status}）：{message}；未提交任何任务。",
    "next.handoff.err.preview_endpoint": "批处理预览端点不可用（{status}）：{message}；未提交任何任务。",
    "next.handoff.err.preflight_endpoint": "预检端点不可用（{status}）：{message}；未提交任何任务。",
    "next.handoff.err.submit_endpoint": "任务提交端点不可用（{status}）：{message}；未提交任何任务。",
    "next.handoff.err.xyz_read": "无法读取候选结构 {candidate}：{message}；未提交任何任务。",
    "next.handoff.err.xyz_invalid": "候选结构不是有效 XYZ：{candidate}；未提交任何任务。",
    "next.handoff.err.xyz_unbound": "候选结构注释缺少 candidate_id={candidate}，无法绑定选择；未提交任何任务。",
    "next.handoff.err.preflight_rejected": "候选 {candidate} 预检未通过：{message}；未提交任何任务。",
    "next.handoff.err.submit_unconfirmed": "提交结果未确认（{message}）：请先在任务队列核对，勿盲目重复提交。",
    "next.handoff.err.submit_aborted": "提交过程中预览/选择已变化；已完成的任务回执保留，其余未提交。",
    "next.handoff.err.focus_locked": "提交进行中：已忽略任务焦点变化；请等待回执完成后再切换。",
    "next.handoff.err.submit_conflict": "提交冲突（HTTP 409）：{message}",
    "next.handoff.err.submit_stale": "预览已过期（preflight_stale）：请重新预览后再提交。",
    "next.handoff.err.submit_forbidden": "无提交权限（HTTP {status}）：{message}",
    "next.handoff.err.submit_invalid": "提交请求被拒绝（HTTP {status}）：{message}",
    "next.handoff.source_pes_review": "PES 人工确认（pes/review）",
    "next.handoff.source_frame_candidate": "帧候选（frame-candidates）",
    "next.handoff.note_layout": "服务端布局限制：BatchOptimize 每次只执行一个结构（runner --layout-mode single_flat），因此按候选逐个提交。",
    "next.handoff.feasible_yes": "可提交",
    "next.handoff.feasible_no": "不可提交"
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

  function trimText(value) {
    return textOf(value).trim();
  }

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function integerOf(value) {
    return finiteNumber(value) && value >= 0 ? Math.floor(value) : null;
  }

  function positiveInt(value) {
    return finiteNumber(value) && value >= 1 ? Math.floor(value) : null;
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

  function errorDetail(error) {
    if (!error || !isObject(error.body)) return null;
    return isObject(error.body.detail) ? error.body.detail : null;
  }

  function errorCode(error) {
    var detail = errorDetail(error);
    if (!detail) return "";
    return textOf(detail.code) || textOf(detail.error);
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

  function pesReviewPath(jobId) {
    return jobPath(jobId) + ROUTES.pesReview;
  }

  function frameCandidatesPath(jobId) {
    return jobPath(jobId) + ROUTES.frameCandidates;
  }

  function energyPath(jobId) {
    return jobPath(jobId) + ROUTES.energyGraph;
  }

  function structureFilePath(jobId, candidateId) {
    return jobPath(jobId) + ROUTES.files + "RESULT/structures/" + encodeId(candidateId) + ".xyz";
  }

  function shortText(text, limit) {
    var value = textOf(text);
    var max = limit || MAX_TEXT;
    return value.length > max ? value.slice(0, max) + "…" : value;
  }

  /* ── pure normalization (server data -> panel model) ─────────────── */

  function normalizeJob(payload) {
    if (!isObject(payload)) return null;
    var spec = isObject(payload.spec) ? payload.spec : {};
    var input = isObject(spec.input) ? spec.input : {};
    return {
      id: textOf(payload.id) || textOf(payload.job_id),
      workflow: textOf(spec.workflow),
      status: textOf(payload.status),
      attempt: finiteNumber(payload.attempt) && payload.attempt >= 1
        ? Math.floor(payload.attempt) : 1,
      moleculeName: textOf(payload.molecule_name) || textOf(spec.molecule_name),
      charge: finiteNumber(input.charge) && Math.floor(input.charge) === input.charge
        ? input.charge : null,
      multiplicity: finiteNumber(input.multiplicity) && Math.floor(input.multiplicity) === input.multiplicity
        ? input.multiplicity : null,
      projectId: textOf(payload.project_id)
    };
  }

  function normalizeMethodCatalog(payload) {
    var schemas = isObject(payload) && isObject(payload.method_schemas)
      ? payload.method_schemas : {};
    var batch = isObject(schemas.batch_optimize) ? schemas.batch_optimize : null;
    var profiles = [];
    asArray(batch && batch.profiles).forEach(function (raw) {
      if (!isObject(raw)) return;
      var id = textOf(raw.profile_id) || textOf(raw.id);
      if (!id) return;
      profiles.push({
        id: id,
        label: textOf(raw.label_zh) || textOf(raw.label) || id,
        summary: textOf(raw.summary)
      });
    });
    return { profiles: profiles };
  }

  function defaultProfileFor(profiles) {
    var ids = asArray(profiles).map(function (profile) { return profile.id; });
    if (ids.indexOf(DEFAULT_PROFILE) !== -1) return DEFAULT_PROFILE;
    return ids.length ? ids[0] : "";
  }

  function normalizeEnergyGraph(payload) {
    if (!isObject(payload)) {
      return { viewType: "", defaultSeries: null, frames: [], annotations: [], provenance: {} };
    }
    var series = asArray(payload.series).filter(isObject).map(function (raw, index) {
      return {
        id: textOf(raw.id) || ("series-" + index),
        label: textOf(raw.label) || textOf(raw.id) || ("series-" + index),
        unit: textOf(raw.unit),
        values: asArray(raw.values)
      };
    });
    var defaultSeries = null;
    var requested = textOf(payload.default_series);
    series.forEach(function (item) {
      if (!defaultSeries && requested && item.id === requested) defaultSeries = item;
    });
    if (!defaultSeries) {
      series.forEach(function (item) {
        if (defaultSeries) return;
        if (item.values.some(function (value) { return finiteNumber(value); })) defaultSeries = item;
      });
    }
    if (!defaultSeries && series.length) defaultSeries = series[0];
    var frames = asArray(payload.nodes).filter(isObject).map(function (node, index) {
      var frameIndex = integerOf(node.frame_index);
      return {
        index: frameIndex == null ? index : frameIndex,
        label: textOf(node.label) || ("Frame " + (index + 1)),
        energy: finiteNumber(node.energy) ? node.energy : null,
        status: textOf(node.status),
        x: finiteNumber(node.x) ? node.x : null,
        geometryRef: textOf(node.geometry_ref)
      };
    });
    var annotations = asArray(payload.annotations).filter(isObject).map(function (raw) {
      return {
        candidateId: textOf(raw.candidate_id) || textOf(raw.candidateId),
        frameIndex: integerOf(raw.frame_index) != null ? integerOf(raw.frame_index)
          : integerOf(raw.frameIndex),
        y: finiteNumber(raw.y) ? raw.y : null,
        selectionSource: textOf(raw.selection_source) || textOf(raw.selectionSource),
        saved: raw.saved === true,
        confidence: raw.confidence == null ? "" : textOf(raw.confidence),
        reason: textOf(raw.reason)
      };
    });
    return {
      viewType: textOf(payload.view_type),
      defaultSeries: defaultSeries ? {
        id: defaultSeries.id, label: defaultSeries.label, unit: defaultSeries.unit
      } : null,
      frames: frames,
      annotations: annotations,
      provenance: isObject(payload.provenance) ? payload.provenance : {}
    };
  }

  function energyForFrame(energy, frameIndex, candidateId) {
    if (!energy) return { value: null, unit: "", label: "", status: "", source: "none" };
    var unit = energy.defaultSeries ? energy.defaultSeries.unit : "";
    var label = energy.defaultSeries ? energy.defaultSeries.label : "";
    for (var i = 0; i < energy.frames.length; i++) {
      if (energy.frames[i].index === frameIndex && energy.frames[i].energy != null) {
        return {
          value: energy.frames[i].energy,
          unit: unit,
          label: label,
          status: energy.frames[i].status,
          source: "node"
        };
      }
    }
    for (var j = 0; j < energy.annotations.length; j++) {
      var annotation = energy.annotations[j];
      if (annotation.candidateId && annotation.candidateId === candidateId && annotation.y != null) {
        return {
          value: annotation.y,
          unit: unit,
          label: label,
          status: "",
          source: "annotation"
        };
      }
    }
    return { value: null, unit: unit, label: label, status: "", source: "none" };
  }

  function roleToken(raw) {
    var value = trimText(raw).toLowerCase();
    if (value === "ts") return "ts";
    if (value === "int" || value === "intermediate" || value === "endpoint") return "intermediate";
    return "";
  }

  function normalizePesCandidates(reviewPayload, energy) {
    if (!isObject(reviewPayload)) {
      return { candidates: [], status: "pending", revision: 0, confirmedAt: "", source: {}, warnings: [] };
    }
    var review = isObject(reviewPayload.review) ? reviewPayload.review : {};
    var source = isObject(review.source) ? review.source : {};
    var warnings = [];
    var candidates = [];
    asArray(review.selected).forEach(function (raw) {
      if (!isObject(raw)) return;
      var candidateId = textOf(raw.candidate_id);
      if (!candidateId) {
        warnings.push("pes/review 选点缺少 candidate_id，已跳过");
        return;
      }
      var frameIndex = integerOf(raw.frame_index);
      var energyInfo = energyForFrame(energy, frameIndex, candidateId);
      candidates.push({
        candidateId: candidateId,
        role: roleToken(raw.role) || "intermediate",
        rawRole: textOf(raw.role),
        frameIndex: frameIndex,
        name: textOf(raw.name) || candidateId,
        structurePath: textOf(raw.structure_path),
        source: "pes_review",
        selectionSource: textOf(raw.selection_source) || "manual",
        savedAt: textOf(review.confirmed_at),
        revision: finiteNumber(review.revision) ? Math.floor(review.revision) : 0,
        frameConverged: raw.frame_converged === true,
        rank: finiteNumber(raw.rank) ? raw.rank : null,
        displayLabel: "",
        roleIndex: null,
        viewType: "",
        itemId: null,
        energy: energyInfo
      });
    });
    return {
      candidates: candidates,
      status: textOf(reviewPayload.status) || textOf(review.status) || "pending",
      revision: finiteNumber(review.revision) ? Math.floor(review.revision) : 0,
      confirmedAt: textOf(review.confirmed_at),
      source: {
        taskStatus: textOf(source.task_status),
        frameSource: textOf(source.frame_source),
        scanComplete: typeof source.scan_complete === "boolean" ? source.scan_complete : null,
        dataSource: textOf(source.data_source)
      },
      warnings: warnings
    };
  }

  function normalizeFrameCandidates(payload, energy) {
    if (!isObject(payload)) return { candidates: [], revision: 0, warnings: [] };
    var warnings = [];
    var candidates = [];
    asArray(payload.candidates).forEach(function (raw) {
      if (!isObject(raw)) return;
      var candidateId = textOf(raw.candidate_id);
      if (!candidateId) {
        warnings.push("frame-candidates 条目缺少 candidate_id，已跳过");
        return;
      }
      var frameIndex = integerOf(raw.frame_index);
      var viewType = textOf(raw.view_type);
      var energyInfo = energyForFrame(energy, frameIndex, candidateId);
      candidates.push({
        candidateId: candidateId,
        role: roleToken(raw.role) || "intermediate",
        rawRole: textOf(raw.role),
        frameIndex: frameIndex,
        name: textOf(raw.name) || textOf(raw.display_label) || candidateId,
        structurePath: textOf(raw.structure_path),
        source: "frame_candidate",
        selectionSource: "manual_frame",
        savedAt: textOf(raw.saved_at),
        revision: finiteNumber(payload.revision) ? Math.floor(payload.revision) : 0,
        frameConverged: null,
        rank: finiteNumber(raw.rank) ? raw.rank : null,
        displayLabel: textOf(raw.display_label),
        roleIndex: finiteNumber(raw.role_index) && raw.role_index >= 1
          ? Math.floor(raw.role_index) : null,
        viewType: viewType,
        itemId: textOf(raw.item_id) || null,
        energy: energyInfo
      });
    });
    return {
      candidates: candidates,
      revision: finiteNumber(payload.revision) ? Math.floor(payload.revision) : 0,
      warnings: warnings
    };
  }

  function selectedCandidates(candidates, selection) {
    return asArray(candidates).filter(function (candidate) {
      return selection && selection[candidate.candidateId] === true;
    });
  }

  function selectionKey(jobId, profile, candidates) {
    var ids = asArray(candidates).map(function (candidate) { return candidate.candidateId; });
    return jobId + "|" + profile + "|" + ids.join(",");
  }

  function validateXyz(text, candidateId) {
    var value = textOf(text);
    var lines = value.trim().split(/\r?\n/);
    if (!lines.length) return { ok: false, reason: "empty", atomCount: 0 };
    var count = parseInt(trimText(lines[0]), 10);
    if (isNaN(count) || count < 1) return { ok: false, reason: "atom_count", atomCount: 0 };
    if (lines.length < count + 2) return { ok: false, reason: "truncated", atomCount: count };
    var comment = lines[1] || "";
    var bound = false;
    if (candidateId) {
      var tokens = comment.split(/[\s|,;]+/);
      for (var i = 0; i < tokens.length; i++) {
        if (tokens[i] === "candidate_id=" + candidateId) { bound = true; break; }
      }
    }
    return { ok: true, reason: "", atomCount: count, bound: bound };
  }

  /**
   * Build one BatchOptimize body per selected candidate (server order).
   * Uses only keys accepted by `WORKFLOW_CONTRACTS["BatchOptimize"]`:
   * input.source_type/source/selected_ids/source_job_id/charge/multiplicity,
   * method.profile. No filesystem path and no client-side science value.
   */
  function buildHandoffBodies(input) {
    input = input || {};
    var job = input.job || null;
    var profile = trimText(input.profile);
    var xyzByCandidate = input.xyzByCandidate || {};
    var bodies = [];
    asArray(input.candidates).forEach(function (candidate) {
      if (!candidate || !candidate.candidateId) return;
      var xyz = xyzByCandidate[candidate.candidateId];
      if (typeof xyz !== "string" || !xyz.trim()) return;
      var payload = {
        schema_version: 1,
        workflow: "BatchOptimize",
        input: {
          source_type: "xyz_text",
          source: xyz,
          selected_ids: [candidate.candidateId]
        },
        method: { profile: profile }
      };
      if (job && job.id) payload.input.source_job_id = job.id;
      if (job && job.charge != null) payload.input.charge = job.charge;
      if (job && job.multiplicity != null) payload.input.multiplicity = job.multiplicity;
      bodies.push({ candidateId: candidate.candidateId, body: payload });
    });
    return bodies;
  }

  function buildSubmitBody(body, digest) {
    if (!isObject(body) || !trimText(digest)) return null;
    var clone = JSON.parse(JSON.stringify(body));
    clone.preflight_fingerprint = trimText(digest);
    return clone;
  }

  function normalizePreflight(payload) {
    if (!isObject(payload)) {
      return { feasible: false, digest: "", reasons: [{ code: "E_SHAPE", message: "预检响应不是对象", field: "", rule: "" }], advisories: [] };
    }
    var reasons = asArray(payload.reasons).filter(isObject).map(function (raw) {
      var params = isObject(raw.params) ? raw.params : {};
      return {
        code: textOf(raw.code),
        message: textOf(raw.message) || textOf(raw.code),
        field: textOf(raw.field) || textOf(params.field),
        rule: textOf(raw.rule) || textOf(params.rule)
      };
    });
    var advisories = asArray(payload.advisories).map(function (item) {
      if (isObject(item)) return textOf(item.message) || textOf(item.code) || shortText(JSON.stringify(item));
      return textOf(item);
    }).filter(function (item) { return !!item; });
    return {
      feasible: payload.feasible === true,
      digest: textOf(payload.plan_digest),
      reasons: reasons,
      advisories: advisories,
      wouldCreateTask: payload.would_create_task === true,
      wouldReserve: payload.would_reserve === true
    };
  }

  function normalizeReceipt(payload) {
    if (!isObject(payload)) return null;
    var jobId = trimText(payload.job_id);
    if (!jobId) return null;
    var attempt = positiveInt(payload.attempt);
    return {
      jobId: jobId,
      status: textOf(payload.status),
      attempt: attempt == null ? 1 : attempt,
      planDigest: textOf(payload.plan_digest),
      replay: payload.idempotent_replay === true,
      idempotencyKey: textOf(payload.idempotency_key)
    };
  }

  function mapSubmitError(error) {
    var status = errorStatus(error);
    var detail = errorDetail(error);
    var code = errorCode(error);
    var message = detail ? textOf(detail.message) : "";
    if (!message) message = describeError(error);
    var out = {
      status: status,
      code: code,
      message: message,
      key: "next.handoff.err.submit_invalid",
      vars: { status: status == null ? "?" : String(status), message: message },
      retryable: false
    };
    if (status == null) {
      out.key = "next.handoff.err.submit_unconfirmed";
      out.vars = { message: describeError(error) };
      out.retryable = true;
    } else if (status === 400 && code === "preflight_stale") {
      out.key = "next.handoff.err.submit_stale";
      out.vars = {};
    } else if (status === 401 || status === 403) {
      out.key = "next.handoff.err.submit_forbidden";
      out.vars = { status: String(status), message: message };
    } else if (status === 409) {
      out.key = "next.handoff.err.submit_conflict";
      out.vars = { message: message };
    } else if (status === 404) {
      out.key = "next.handoff.err.submit_endpoint";
      out.vars = { status: String(status), message: message };
    }
    out.text = t(out.key, out.vars);
    return out;
  }

  function idempotencyKeyFor(jobId, candidateId, digest) {
    return "pes-handoff:" + jobId + ":" + candidateId + ":" + trimText(digest);
  }

  function unwrapText(value) {
    if (typeof value === "string") return Promise.resolve(value);
    if (value == null) return Promise.resolve("");
    if (typeof value.text === "function") {
      return Promise.resolve(value.text()).then(function (text) { return textOf(text); });
    }
    if (typeof value.content === "string") return Promise.resolve(value.content);
    if (typeof value.xyz === "string") return Promise.resolve(value.xyz);
    return Promise.resolve("");
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
      jobInputValue: "",
      token: 0,
      status: "empty",
      busy: "none",
      error: "",
      retry: null,
      job: null,
      candidateSource: "",
      candidates: [],
      energy: null,
      review: null,
      warnings: [],
      catalog: { status: "idle", profiles: [], error: "" },
      profile: "",
      selection: {},
      emptyReason: "",
      previewSeq: 0,
      preview: {
        status: "idle", token: 0, key: "", items: [], config: null,
        error: "", notices: []
      },
      handoff: { status: "idle", confirmOpen: false, receipts: [], error: "" }
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
      var node = el(doc, "section", className || "acp-handoff-section");
      node.appendChild(i18nEl(doc, "h3", "acp-handoff-heading", headingKey));
      return node;
    }

    function summaryRow(labelKey, role) {
      var row = el(doc, "div", "acp-handoff-kv");
      row.appendChild(i18nEl(doc, "span", "acp-handoff-k", labelKey));
      var value = el(doc, "span", "acp-handoff-v", "—");
      value.setAttribute("data-role", role);
      row.appendChild(value);
      return row;
    }

    function buildDom() {
      var root = el(doc, "div", "acp-handoff");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-preview-state", "idle");
      root.setAttribute("data-acp-handoff-state", "idle");
      root.setAttribute("data-acp-config-state", "idle");
      root.setAttribute("data-acp-candidate-count", "0");
      root.setAttribute("data-acp-selection-count", "0");
      root.setAttribute("data-acp-receipt-count", "0");
      root.setAttribute("data-acp-warning-count", "0");
      root.setAttribute("data-acp-profile", "");
      root.setAttribute("data-acp-confirm-open", "false");

      var head = el(doc, "div", "acp-handoff-head");
      head.appendChild(i18nEl(doc, "h2", "acp-handoff-title", "next.handoff.title"));
      head.appendChild(i18nEl(doc, "p", "acp-handoff-sub", "next.handoff.subtitle"));
      root.appendChild(head);

      var body = el(doc, "div", "acp-handoff-body");
      root.appendChild(body);

      /* 1 — source */
      var source = section("next.handoff.source_title");
      source.appendChild(i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.source_hint"));
      var sourceRow = el(doc, "div", "acp-handoff-row");
      var idLabel = i18nEl(doc, "label", "acp-handoff-label", "next.handoff.job_id");
      var jobInput = el(doc, "input", "acp-handoff-input");
      jobInput.type = "text";
      jobInput.setAttribute("data-role", "job-id");
      jobInput.setAttribute("placeholder", t("next.handoff.job_id_ph"));
      idLabel.appendChild(jobInput);
      state.ui.jobInput = jobInput;
      sourceRow.appendChild(idLabel);
      var loadBtn = el(doc, "button", "acp-handoff-btn", t("next.handoff.load"));
      loadBtn.type = "button";
      loadBtn.setAttribute("data-action", "load-job");
      loadBtn.setAttribute("data-i18n", "next.handoff.load");
      loadBtn.setAttribute("data-variant", "primary");
      trackListener(loadBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        loadJob(state.ui.jobInput ? state.ui.jobInput.value : "");
      });
      state.ui.loadBtn = loadBtn;
      sourceRow.appendChild(loadBtn);
      var reloadBtn = el(doc, "button", "acp-handoff-btn ghost", t("next.handoff.reload"));
      reloadBtn.type = "button";
      reloadBtn.setAttribute("data-action", "reload");
      reloadBtn.setAttribute("data-i18n", "next.handoff.reload");
      trackListener(reloadBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        var id = state.job ? state.job.id : trimText(state.ui.jobInput && state.ui.jobInput.value);
        if (id) loadJob(id);
      });
      state.ui.reloadBtn = reloadBtn;
      sourceRow.appendChild(reloadBtn);
      source.appendChild(sourceRow);
      body.appendChild(source);

      /* empty placeholder */
      var emptyBox = el(doc, "section", "acp-handoff-empty-box");
      emptyBox.setAttribute("data-role", "empty-state");
      emptyBox.hidden = true;
      emptyBox.appendChild(i18nEl(doc, "h3", "acp-handoff-heading", "next.handoff.empty_title"));
      var emptyHint = i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.empty_hint");
      emptyHint.setAttribute("data-role", "empty-hint");
      emptyBox.appendChild(emptyHint);
      state.ui.emptyBox = emptyBox;
      state.ui.emptyHint = emptyHint;
      body.appendChild(emptyBox);

      /* 2 — candidates */
      var candidatesSection = section("next.handoff.summary_title");
      var grid = el(doc, "div", "acp-handoff-grid");
      var summaryFields = {};
      [
        ["next.handoff.summary_job", "summary-job"],
        ["next.handoff.summary_workflow", "summary-workflow"],
        ["next.handoff.summary_status", "summary-status"],
        ["next.handoff.summary_source", "summary-source"],
        ["next.handoff.summary_review", "summary-review"],
        ["next.handoff.summary_confirmed", "summary-confirmed"],
        ["next.handoff.summary_count", "summary-count"],
        ["next.handoff.summary_selected", "summary-selected"]
      ].forEach(function (pair) {
        var row = summaryRow(pair[0], pair[1]);
        summaryFields[pair[1]] = row.children ? row.children[row.children.length - 1] : null;
        grid.appendChild(row);
      });
      candidatesSection.appendChild(grid);
      state.ui.summaryFields = summaryFields;

      candidatesSection.appendChild(i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.candidates_hint"));

      var profileWrap = el(doc, "div", "acp-handoff-profile");
      var profileLabel = i18nEl(doc, "label", "acp-handoff-label", "next.handoff.profile_label");
      var profileSelect = el(doc, "select", "acp-handoff-select");
      profileSelect.setAttribute("data-role", "profile");
      profileLabel.appendChild(profileSelect);
      state.ui.profileSelect = profileSelect;
      profileWrap.appendChild(profileLabel);
      profileWrap.appendChild(i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.profile_hint"));
      candidatesSection.appendChild(profileWrap);
      trackListener(profileSelect, "change", function () {
        if (!state.mounted || state.disposed) return;
        onProfileChange(profileSelect.value);
      });

      var tools = el(doc, "div", "acp-handoff-row");
      var selectAll = el(doc, "button", "acp-handoff-btn small ghost", t("next.handoff.select_all"));
      selectAll.type = "button";
      selectAll.setAttribute("data-action", "select-all");
      selectAll.setAttribute("data-i18n", "next.handoff.select_all");
      trackListener(selectAll, "click", function () {
        if (!state.mounted || state.disposed) return;
        setAllSelected(true);
      });
      var selectNone = el(doc, "button", "acp-handoff-btn small ghost", t("next.handoff.select_none"));
      selectNone.type = "button";
      selectNone.setAttribute("data-action", "select-none");
      selectNone.setAttribute("data-i18n", "next.handoff.select_none");
      trackListener(selectNone, "click", function () {
        if (!state.mounted || state.disposed) return;
        setAllSelected(false);
      });
      tools.appendChild(selectAll);
      tools.appendChild(selectNone);
      var selectionSummary = el(doc, "span", "acp-handoff-selection", "");
      selectionSummary.setAttribute("data-role", "selection-summary");
      tools.appendChild(selectionSummary);
      candidatesSection.appendChild(tools);
      state.ui.selectionSummary = selectionSummary;

      state.ui.candidates = el(doc, "div", "acp-handoff-list");
      state.ui.candidates.setAttribute("data-role", "candidates");
      candidatesSection.appendChild(state.ui.candidates);

      var candidatesEmpty = i18nEl(doc, "p", "acp-handoff-empty", "next.handoff.candidates_empty");
      candidatesEmpty.setAttribute("data-role", "candidates-empty");
      candidatesEmpty.hidden = true;
      state.ui.candidatesEmpty = candidatesEmpty;
      candidatesSection.appendChild(candidatesEmpty);
      body.appendChild(candidatesSection);

      /* 3 — preview */
      var previewSection = section("next.handoff.preview_title");
      previewSection.appendChild(i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.preview_hint"));
      var previewRow = el(doc, "div", "acp-handoff-row");
      var previewBtn = el(doc, "button", "acp-handoff-btn", t("next.handoff.preview"));
      previewBtn.type = "button";
      previewBtn.setAttribute("data-action", "preview");
      previewBtn.setAttribute("data-i18n", "next.handoff.preview");
      trackListener(previewBtn, "click", function () {
        if (!state.mounted || state.disposed) return;
        runPreview();
      });
      state.ui.previewBtn = previewBtn;
      previewRow.appendChild(previewBtn);
      var previewStatus = el(doc, "span", "acp-handoff-status", t("next.handoff.preview_idle"));
      previewStatus.setAttribute("data-role", "preview-status");
      previewRow.appendChild(previewStatus);
      state.ui.previewStatus = previewStatus;
      previewSection.appendChild(previewRow);

      var configBox = el(doc, "div", "acp-handoff-config");
      configBox.setAttribute("data-role", "config-box");
      configBox.hidden = true;
      var configLine = el(doc, "p", "acp-handoff-note", "");
      configLine.setAttribute("data-role", "config-key");
      var rolesLine = el(doc, "p", "acp-handoff-note", "");
      rolesLine.setAttribute("data-role", "config-roles");
      configBox.appendChild(configLine);
      configBox.appendChild(rolesLine);
      state.ui.configBox = configBox;
      state.ui.configKey = configLine;
      state.ui.configRoles = rolesLine;
      previewSection.appendChild(configBox);

      var previewList = el(doc, "div", "acp-handoff-list");
      previewList.setAttribute("data-role", "preview");
      state.ui.previewList = previewList;
      previewSection.appendChild(previewList);

      var previewError = el(doc, "p", "acp-handoff-error", "");
      previewError.setAttribute("data-role", "preview-error");
      previewError.hidden = true;
      state.ui.previewError = previewError;
      previewSection.appendChild(previewError);
      body.appendChild(previewSection);

      /* 4 — confirm + receipts */
      var handoffSection = section("next.handoff.handoff_title");
      handoffSection.appendChild(i18nEl(doc, "p", "acp-handoff-hint", "next.handoff.confirm_hint"));
      handoffSection.appendChild(i18nEl(doc, "p", "acp-handoff-note", "next.handoff.note_layout"));

      var handoffRow = el(doc, "div", "acp-handoff-row");
      var requestConfirm = el(doc, "button", "acp-handoff-btn", t("next.handoff.request_confirm"));
      requestConfirm.type = "button";
      requestConfirm.setAttribute("data-action", "request-confirm");
      requestConfirm.setAttribute("data-i18n", "next.handoff.request_confirm");
      requestConfirm.disabled = true;
      trackListener(requestConfirm, "click", function () {
        if (!state.mounted || state.disposed) return;
        onRequestConfirm();
      });
      state.ui.requestConfirm = requestConfirm;
      handoffRow.appendChild(requestConfirm);
      var handoffStatus = el(doc, "span", "acp-handoff-status", "");
      handoffStatus.setAttribute("data-role", "handoff-status");
      handoffRow.appendChild(handoffStatus);
      state.ui.handoffStatus = handoffStatus;
      handoffSection.appendChild(handoffRow);

      var confirmBox = el(doc, "div", "acp-handoff-confirm");
      confirmBox.setAttribute("data-role", "confirm-box");
      confirmBox.setAttribute("data-confirm-box", "true");
      confirmBox.hidden = true;
      confirmBox.appendChild(i18nEl(doc, "h4", "acp-handoff-heading", "next.handoff.confirm_title"));
      var confirmBody = el(doc, "p", "acp-handoff-note", "");
      confirmBody.setAttribute("data-role", "confirm-body");
      confirmBox.appendChild(confirmBody);
      state.ui.confirmBody = confirmBody;
      var confirmRow = el(doc, "div", "acp-handoff-row");
      var confirmSubmit = el(doc, "button", "acp-handoff-btn danger", t("next.handoff.confirm_submit"));
      confirmSubmit.type = "button";
      confirmSubmit.setAttribute("data-action", "confirm-submit");
      confirmSubmit.setAttribute("data-i18n", "next.handoff.confirm_submit");
      trackListener(confirmSubmit, "click", function () {
        if (!state.mounted || state.disposed) return;
        runSubmit();
      });
      state.ui.confirmSubmit = confirmSubmit;
      confirmRow.appendChild(confirmSubmit);
      var confirmCancel = el(doc, "button", "acp-handoff-btn ghost", t("next.handoff.confirm_cancel"));
      confirmCancel.type = "button";
      confirmCancel.setAttribute("data-action", "confirm-cancel");
      confirmCancel.setAttribute("data-i18n", "next.handoff.confirm_cancel");
      trackListener(confirmCancel, "click", function () {
        if (!state.mounted || state.disposed) return;
        state.handoff.confirmOpen = false;
        render();
      });
      confirmRow.appendChild(confirmCancel);
      confirmBox.appendChild(confirmRow);
      state.ui.confirmBox = confirmBox;
      handoffSection.appendChild(confirmBox);

      handoffSection.appendChild(i18nEl(doc, "h4", "acp-handoff-subheading", "next.handoff.receipt_title"));
      var receipts = el(doc, "div", "acp-handoff-list");
      receipts.setAttribute("data-role", "receipts");
      state.ui.receipts = receipts;
      handoffSection.appendChild(receipts);
      var receiptEmpty = i18nEl(doc, "p", "acp-handoff-empty", "next.handoff.receipt_empty");
      receiptEmpty.setAttribute("data-role", "receipt-empty");
      state.ui.receiptEmpty = receiptEmpty;
      handoffSection.appendChild(receiptEmpty);
      body.appendChild(handoffSection);

      /* notices */
      var notices = el(doc, "section", "acp-handoff-section");
      notices.appendChild(i18nEl(doc, "h3", "acp-handoff-heading", "next.handoff.partial"));
      var warningList = el(doc, "ul", "acp-handoff-warnings");
      warningList.setAttribute("data-role", "warnings");
      notices.appendChild(warningList);
      state.ui.warnings = warningList;
      notices.hidden = true;
      state.ui.notices = notices;
      body.appendChild(notices);

      /* overlay */
      var overlay = el(doc, "div", "acp-handoff-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = true;
      var overlayText = el(doc, "p", "acp-handoff-overlay-text", "");
      overlayText.setAttribute("data-role", "overlay-message");
      overlay.appendChild(overlayText);
      var retryBtn = el(doc, "button", "acp-handoff-btn", t("next.handoff.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.handoff.retry");
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
    }

    /* ── rendering ──────────────────────────────────────────────── */

    function setText(node, value) {
      if (node) node.textContent = value == null ? "" : String(value);
    }

    function formatNumber(value) {
      if (!finiteNumber(value)) return "—";
      var abs = Math.abs(value);
      var digits = abs !== 0 && abs < 0.01 ? 6 : (abs < 100 ? 4 : 3);
      return value.toFixed(digits);
    }

    function candidateEnergyText(candidate) {
      var info = candidate.energy || {};
      if (!finiteNumber(info.value)) return t("next.handoff.energy_none");
      var unit = info.unit || "";
      return (info.label ? info.label + " " : "") + formatNumber(info.value) + (unit ? " " + unit : "");
    }

    function candidateProvenanceText(candidate) {
      var parts = [];
      if (candidate.source === "pes_review") parts.push(t("next.handoff.source_pes_review"));
      else if (candidate.source === "frame_candidate") parts.push(t("next.handoff.source_frame_candidate"));
      if (candidate.savedAt) parts.push(candidate.savedAt);
      if (candidate.viewType) parts.push("view=" + candidate.viewType);
      if (candidate.itemId) parts.push("item=" + candidate.itemId);
      if (candidate.selectionSource) parts.push("source=" + candidate.selectionSource);
      if (candidate.frameConverged === true) parts.push("converged=true");
      if (candidate.frameConverged === false) parts.push("converged=false");
      return parts.join(" · ") || "—";
    }

    function renderStateChrome() {
      if (!state.root) return;
      var selected = selectedCandidates(state.candidates, state.selection).length;
      state.root.setAttribute("data-acp-module-state", state.status);
      state.root.setAttribute("data-acp-busy", state.busy);
      state.root.setAttribute("data-acp-preview-state", state.preview.status);
      state.root.setAttribute("data-acp-handoff-state", state.handoff.status);
      state.root.setAttribute("data-acp-config-state", state.catalog.status);
      state.root.setAttribute("data-acp-candidate-count", String(state.candidates.length));
      state.root.setAttribute("data-acp-selection-count", String(selected));
      state.root.setAttribute("data-acp-receipt-count", String(state.handoff.receipts.length));
      state.root.setAttribute("data-acp-warning-count", String(state.warnings.length));
      state.root.setAttribute("data-acp-profile", state.profile || "");
      state.root.setAttribute("data-acp-confirm-open", state.handoff.confirmOpen ? "true" : "false");
      if (state.ui.loadBtn) state.ui.loadBtn.disabled = state.busy !== "none";
      if (state.ui.reloadBtn) state.ui.reloadBtn.disabled = state.busy !== "none";
      if (state.ui.jobInput) state.ui.jobInput.disabled = state.busy !== "none";
      if (state.ui.profileSelect) {
        state.ui.profileSelect.disabled = state.busy !== "none" || state.catalog.status !== "ready";
      }
      if (state.ui.previewBtn) {
        state.ui.previewBtn.disabled = state.busy !== "none" || state.status !== "ready";
      }
    }

    function renderSummary() {
      var fields = state.ui.summaryFields || {};
      var show = state.status === "ready" || state.status === "empty";
      ["summary-job", "summary-workflow", "summary-status", "summary-source",
        "summary-review", "summary-confirmed", "summary-count", "summary-selected"
      ].forEach(function (name) {
        var row = fields[name] && fields[name].parentNode ? fields[name].parentNode : null;
        if (row) row.hidden = !show;
      });
      if (!show || !state.job) return;
      setText(fields["summary-job"], state.job.id || "—");
      setText(fields["summary-workflow"], state.job.workflow || "—");
      setText(fields["summary-status"], state.job.status || "—");
      setText(fields["summary-source"], state.candidateSource === "pes_review"
        ? t("next.handoff.source_pes_review")
        : (state.candidateSource === "frame_candidate"
          ? t("next.handoff.source_frame_candidate") : "—"));
      if (state.review) {
        setText(fields["summary-review"], "revision=" + state.review.revision +
          (state.review.source && state.review.source.frameSource
            ? " · " + state.review.source.frameSource : ""));
        setText(fields["summary-confirmed"], state.review.confirmedAt || "—");
      } else {
        setText(fields["summary-review"], "—");
        setText(fields["summary-confirmed"], "—");
      }
      setText(fields["summary-count"], String(state.candidates.length));
      setText(fields["summary-selected"], String(
        selectedCandidates(state.candidates, state.selection).length));
    }

    function renderEmpty() {
      if (!state.ui.emptyBox) return;
      var show = state.status === "empty";
      state.ui.emptyBox.hidden = !show;
      if (!show) return;
      var key = state.emptyReason || "next.handoff.empty_hint";
      setText(state.ui.emptyHint, t(key));
      state.ui.emptyHint.setAttribute("data-i18n", key);
    }

    function renderEmptyReasonKey() {
      if (state.job && state.job.workflow === "PESsearch" &&
          state.review && state.review.status !== "confirmed") {
        return "next.handoff.candidates_pending";
      }
      return "next.handoff.candidates_empty";
    }

    function renderProfile() {
      var select = state.ui.profileSelect;
      if (!select) return;
      clearChildren(select);
      var placeholder = el(doc, "option", "", "—");
      placeholder.value = "";
      select.appendChild(placeholder);
      state.catalog.profiles.forEach(function (profile) {
        var option = el(doc, "option", "", profile.label + " (" + profile.id + ")");
        option.value = profile.id;
        select.appendChild(option);
      });
      select.value = state.profile || "";
    }

    function candidateRow(candidate) {
      var row = el(doc, "div", "acp-handoff-candidate");
      row.setAttribute("data-candidate-id", candidate.candidateId);
      row.setAttribute("data-candidate-role", candidate.role);
      row.setAttribute("data-candidate-frame", candidate.frameIndex == null ? "" : String(candidate.frameIndex));
      row.setAttribute("data-candidate-source", candidate.source);

      var checkbox = el(doc, "input", "acp-handoff-check");
      checkbox.type = "checkbox";
      checkbox.checked = state.selection[candidate.candidateId] === true;
      checkbox.setAttribute("data-role", "select");
      checkbox.setAttribute("data-candidate-id", candidate.candidateId);
      checkbox.disabled = state.busy !== "none";
      trackListener(checkbox, "change", function () {
        if (!state.mounted || state.disposed) return;
        setSelected(candidate.candidateId, checkbox.checked);
      });
      row.appendChild(checkbox);

      var role = el(doc, "span", "acp-handoff-badge acp-handoff-badge--" + candidate.role,
        candidate.role === "ts" ? "TS" : "INT");
      role.setAttribute("data-role", "candidate-role");
      row.appendChild(role);

      var label = el(doc, "span", "acp-handoff-candidate-label", candidate.name || candidate.candidateId);
      label.setAttribute("data-role", "candidate-label");
      row.appendChild(label);

      var frame = el(doc, "span", "acp-handoff-cell", candidate.frameIndex == null ? "—" : String(candidate.frameIndex));
      frame.setAttribute("data-role", "candidate-frame");
      row.appendChild(frame);

      var energy = el(doc, "span", "acp-handoff-cell", candidateEnergyText(candidate));
      energy.setAttribute("data-role", "candidate-energy");
      energy.setAttribute("data-energy-value", finiteNumber(candidate.energy && candidate.energy.value)
        ? String(candidate.energy.value) : "");
      energy.setAttribute("data-energy-unit", candidate.energy && candidate.energy.unit
        ? candidate.energy.unit : "");
      row.appendChild(energy);

      var rank = el(doc, "span", "acp-handoff-cell", candidate.rank == null
        ? t("next.handoff.rank_none")
        : t("next.handoff.rank_value", { rank: String(Math.floor(candidate.rank)) }));
      rank.setAttribute("data-role", "candidate-rank");
      rank.setAttribute("data-rank", candidate.rank == null ? "" : String(candidate.rank));
      row.appendChild(rank);

      var provenance = el(doc, "span", "acp-handoff-cell acp-handoff-cell--source",
        candidateProvenanceText(candidate));
      provenance.setAttribute("data-role", "candidate-provenance");
      row.appendChild(provenance);

      if (candidate.displayLabel || candidate.roleIndex != null) {
        var serverLabel = el(doc, "span", "acp-handoff-cell acp-handoff-cell--source",
          t("next.handoff.label_value", {
            label: candidate.displayLabel || "—",
            index: candidate.roleIndex == null ? "—" : String(candidate.roleIndex)
          }));
        serverLabel.setAttribute("data-role", "candidate-server-label");
        row.appendChild(serverLabel);
      }
      return row;
    }

    function renderCandidates() {
      if (!state.ui.candidates) return;
      clearChildren(state.ui.candidates);
      state.ui.candidates.hidden = !state.candidates.length;
      state.ui.candidatesEmpty.hidden = !!state.candidates.length || state.status === "empty";
      state.candidates.forEach(function (candidate) {
        state.ui.candidates.appendChild(candidateRow(candidate));
      });
      var selected = selectedCandidates(state.candidates, state.selection).length;
      setText(state.ui.selectionSummary, t("next.handoff.selection_summary", {
        selected: String(selected), total: String(state.candidates.length)
      }));
    }

    function renderConfig() {
      var preview = state.preview;
      var show = !!preview.config;
      state.ui.configBox.hidden = !show;
      if (!show) {
        setText(state.ui.configKey, "");
        setText(state.ui.configRoles, "");
        return;
      }
      setText(state.ui.configKey, t("next.handoff.preview_config", {
        key: shortText(preview.config.configKey || "—", 240)
      }));
      var roles = preview.config.roles || {};
      setText(state.ui.configRoles, t("next.handoff.preview_roles", {
        int: shortText(roles.int || "—", 160),
        ts: shortText(roles.ts || "—", 160)
      }));
    }

    function renderPreview() {
      var preview = state.preview;
      var select = state.ui.profileSelect;
      if (select && state.profile && select.value !== state.profile) select.value = state.profile;
      var text = "";
      if (preview.status === "previewing") text = t("next.handoff.previewing");
      else if (preview.status === "previewed") {
        text = t("next.handoff.preview_ok", {
          count: String(preview.items.length), profile: state.profile
        });
      } else if (preview.status === "error" || preview.status === "stale") {
        text = t("next.handoff.preview_error", { message: preview.error || "" });
      } else {
        text = t("next.handoff.preview_idle");
      }
      setText(state.ui.previewStatus, text);
      state.ui.previewStatus.setAttribute("data-status", preview.status);
      state.ui.previewError.hidden = !preview.error ||
        !(preview.status === "error" || preview.status === "stale");
      setText(state.ui.previewError, preview.status === "error" || preview.status === "stale"
        ? t("next.handoff.preview_error", { message: preview.error || "" }) : "");

      clearChildren(state.ui.previewList);
      preview.items.forEach(function (item) {
        var row = el(doc, "div", "acp-handoff-preview-item");
        row.setAttribute("data-preview-candidate", item.candidateId);
        row.setAttribute("data-plan-digest", item.digest || "");
        row.setAttribute("data-feasible", item.feasible ? "true" : "false");
        var head = el(doc, "p", "acp-handoff-preview-head",
          t("next.handoff.preview_item", {
            candidate: item.candidateId,
            feasible: item.feasible ? t("next.handoff.feasible_yes") : t("next.handoff.feasible_no"),
            digest: shortText(item.digest || "—", 80)
          }));
        row.appendChild(head);
        if (item.reasons && item.reasons.length) {
          var reasons = item.reasons.map(function (reason) {
            return (reason.field ? reason.field + ": " : "") + reason.message;
          }).join("；");
          row.appendChild(el(doc, "p", "acp-handoff-error",
            t("next.handoff.preview_reasons", { reasons: shortText(reasons) })));
        }
        if (item.advisories && item.advisories.length) {
          row.appendChild(el(doc, "p", "acp-handoff-note",
            t("next.handoff.preview_advisories", { advisories: shortText(item.advisories.join("；")) })));
        }
        state.ui.previewList.appendChild(row);
      });
    }

    function renderHandoff() {
      var handoff = state.handoff;
      var preview = state.preview;
      var ready = preview.status === "previewed" && preview.items.length > 0 &&
        preview.key === currentPreviewKey() && state.status === "ready" &&
        state.handoff.status !== "submitted";
      if (state.ui.requestConfirm) {
        state.ui.requestConfirm.disabled = state.busy !== "none" || !ready || handoff.confirmOpen;
      }
      var statusText = "";
      if (handoff.status === "submitting") statusText = t("next.handoff.submitting");
      else if (handoff.status === "submitted") {
        statusText = t("next.handoff.submitted", { count: String(handoff.receipts.length) });
      } else if (handoff.status === "error") {
        statusText = t("next.handoff.submit_partial", { message: handoff.error || "" });
      }
      setText(state.ui.handoffStatus, statusText);
      state.ui.handoffStatus.setAttribute("data-status", handoff.status);

      state.ui.confirmBox.hidden = !handoff.confirmOpen;
      if (handoff.confirmOpen) {
        var ids = preview.items.map(function (item) { return item.candidateId; }).join(", ");
        setText(state.ui.confirmBody, t("next.handoff.confirm_body", {
          count: String(preview.items.length),
          ids: ids,
          profile: state.profile
        }));
        if (state.ui.confirmSubmit) state.ui.confirmSubmit.disabled = state.busy !== "none";
      }

      clearChildren(state.ui.receipts);
      handoff.receipts.forEach(function (receipt) {
        var row = el(doc, "div", "acp-handoff-receipt");
        row.setAttribute("data-receipt-candidate", receipt.candidateId);
        row.setAttribute("data-receipt-job-id", receipt.jobId);
        var text = t("next.handoff.receipt_item", {
          candidate: receipt.candidateId,
          jobId: receipt.jobId,
          status: receipt.status || "—",
          attempt: String(receipt.attempt == null ? 1 : receipt.attempt),
          digest: shortText(receipt.planDigest || "—", 64)
        });
        if (receipt.replay) text += " " + t("next.handoff.receipt_replay");
        row.appendChild(el(doc, "p", "acp-handoff-receipt-text", text));
        if (deps.commands && typeof deps.commands.openTask === "function") {
          var open = el(doc, "button", "acp-handoff-btn small ghost", t("next.handoff.open_task"));
          open.type = "button";
          open.setAttribute("data-action", "open-task");
          open.setAttribute("data-receipt-job-id", receipt.jobId);
          trackListener(open, "click", function () {
            if (!state.mounted || state.disposed) return;
            try {
              deps.commands.openTask({
                source: "pes-handoff",
                jobId: receipt.jobId,
                attempt: receipt.attempt || 1
              });
            } catch (error) { /* navigation failure must not break the receipt view */ }
          });
          row.appendChild(open);
        }
        state.ui.receipts.appendChild(row);
      });
      state.ui.receiptEmpty.hidden = !!handoff.receipts.length;
    }

    function renderWarnings() {
      clearChildren(state.ui.warnings);
      state.ui.notices.hidden = !state.warnings.length;
      state.warnings.forEach(function (message) {
        var item = el(doc, "li", "acp-handoff-warning", message);
        item.setAttribute("data-role", "warning");
        state.ui.warnings.appendChild(item);
      });
    }

    function renderOverlay() {
      var loading = state.status === "loading";
      var failed = state.status === "error";
      state.ui.overlay.hidden = !(loading || failed);
      state.ui.retry.hidden = !failed;
      if (loading) {
        setText(state.ui.overlayMessage, t("next.handoff.loading_title"));
        state.ui.overlay.setAttribute("data-overlay-kind", "loading");
      } else if (failed) {
        setText(state.ui.overlayMessage, state.error || t("next.handoff.error_title"));
        state.ui.overlay.setAttribute("data-overlay-kind", "error");
      } else {
        setText(state.ui.overlayMessage, "");
        state.ui.overlay.removeAttribute("data-overlay-kind");
      }
    }

    function render() {
      if (!state.mounted || state.disposed || !state.root) return;
      renderStateChrome();
      renderEmpty();
      renderSummary();
      renderProfile();
      renderCandidates();
      renderConfig();
      renderPreview();
      renderHandoff();
      renderWarnings();
      renderOverlay();
    }

    /* ── selection / profile ────────────────────────────────────── */

    function currentPreviewKey() {
      if (!state.job) return "";
      return selectionKey(state.job.id, state.profile,
        selectedCandidates(state.candidates, state.selection));
    }

    function invalidatePreview(reasonKey) {
      if (state.preview.status === "previewed" || state.preview.status === "previewing") {
        state.preview.status = "stale";
        state.preview.error = t(reasonKey || "next.handoff.preview_stale");
        state.preview.items = [];
        state.preview.config = null;
        state.preview.token += 1;
        state.handoff.confirmOpen = false;
      }
    }

    function setSelected(candidateId, selected) {
      if (selected) state.selection[candidateId] = true;
      else delete state.selection[candidateId];
      invalidatePreview("next.handoff.preview_stale");
      render();
    }

    function setAllSelected(selected) {
      state.selection = {};
      if (selected) {
        state.candidates.forEach(function (candidate) { state.selection[candidate.candidateId] = true; });
      }
      invalidatePreview("next.handoff.preview_stale");
      render();
    }

    function onProfileChange(value) {
      state.profile = trimText(value);
      invalidatePreview("next.handoff.preview_stale");
      render();
    }

    /* ── loading ────────────────────────────────────────────────── */

    function resetData(keepInput) {
      state.job = null;
      state.candidates = [];
      state.candidateSource = "";
      state.energy = null;
      state.review = null;
      state.warnings = [];
      state.emptyReason = "";
      state.error = "";
      state.retry = null;
      state.selection = {};
      state.preview = { status: "idle", token: 0, key: "", items: [], config: null, error: "", notices: [] };
      state.handoff = { status: "idle", confirmOpen: false, receipts: [], error: "" };
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

    function loadMethodCatalog(gen) {
      if (!requestFn) return Promise.resolve();
      return settle(requestFn(ROUTES.methodCatalog, { method: "GET" }))
        .then(function (result) {
          if (!alive(gen)) return;
          if (!result.ok) {
            var status = errorStatus(result.error);
            state.catalog = {
              status: "error",
              profiles: [],
              error: t("next.handoff.err.catalog_endpoint", {
                status: status == null ? "?" : String(status),
                message: describeError(result.error)
              })
            };
            return;
          }
          var normalized = normalizeMethodCatalog(result.value);
          state.catalog = {
            status: normalized.profiles.length ? "ready" : "error",
            profiles: normalized.profiles,
            error: normalized.profiles.length ? "" : t("next.handoff.err.no_profile")
          };
          if (!state.profile) state.profile = defaultProfileFor(normalized.profiles);
        });
    }

    function finishLoad(gen, token) {
      if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
      state.busy = "none";
      if (state.candidateError) {
        var failure = state.candidateError;
        delete state.candidateError;
        state.status = "error";
        state.error = failure;
        state.retry = function () { loadJob(state.job ? state.job.id : ""); };
        render();
        return { ok: false, error: state.error };
      }
      state.warnings = state.warnings.filter(function (message) { return !!message; });
      if (state.candidates.length) {
        state.status = "ready";
        state.error = "";
        state.emptyReason = "";
        if (!state.preview.items.length && state.preview.status !== "previewed") {
          state.preview.status = "idle";
        }
      } else {
        state.status = "empty";
        state.error = "";
        state.emptyReason = renderEmptyReasonKey();
      }
      render();
      return { ok: true };
    }

    function loadSecondary(job, gen, token) {
      var isPes = job.workflow === "PESsearch";
      var catalogPromise = state.catalog.status === "ready"
        ? Promise.resolve() : loadMethodCatalog(gen);
      var candidatesPromise = settle(requestFn(
        isPes ? pesReviewPath(job.id) : frameCandidatesPath(job.id), { method: "GET" }));
      var energyPromise = settle(requestFn(energyPath(job.id), { method: "GET" }));
      return Promise.all([catalogPromise, candidatesPromise, energyPromise]).then(function (results) {
        if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
        var candidatesResult = results[1];
        var energyResult = results[2];
        if (energyResult.ok) {
          state.energy = normalizeEnergyGraph(energyResult.value);
        } else {
          state.energy = null;
          state.warnings.push(t("next.handoff.partial_detail", {
            message: "energy-graph: " + describeError(energyResult.error)
          }));
        }
        if (!candidatesResult.ok) {
          var status = errorStatus(candidatesResult.error);
          var detail = errorDetail(candidatesResult.error);
          var message = detail ? textOf(detail.message) : describeError(candidatesResult.error);
          if (status === 404 || status === 405) {
            state.candidateError = t("next.handoff.err.candidates_endpoint", {
              endpoint: isPes ? "pes/review" : "frame-candidates",
              status: String(status),
              message: message
            });
          } else {
            state.candidateError = (isPes
              ? t("next.handoff.error_title") + " (pes/review HTTP "
              : t("next.handoff.error_title") + " (frame-candidates HTTP ") +
              (status == null ? "?" : String(status)) + "): " + message;
          }
          return finishLoad(gen, token);
        }
        if (isPes) {
          var normalized = normalizePesCandidates(candidatesResult.value, state.energy);
          state.review = normalized;
          state.candidates = normalized.candidates;
          state.candidateSource = "pes_review";
          state.warnings = state.warnings.concat(normalized.warnings);
        } else {
          var frames = normalizeFrameCandidates(candidatesResult.value, state.energy);
          state.review = null;
          state.candidates = frames.candidates;
          state.candidateSource = "frame_candidate";
          state.warnings = state.warnings.concat(frames.warnings);
        }
        state.selection = {};
        state.candidates.forEach(function (candidate) {
          state.selection[candidate.candidateId] = true;
        });
        if (!state.profile) {
          state.profile = state.catalog.status === "ready"
            ? defaultProfileFor(state.catalog.profiles) : "";
        }
        return finishLoad(gen, token);
      });
    }

    function loadJob(rawId) {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (state.busy === "submit") return Promise.resolve({ ok: false, busy: true });
      var id = trimText(rawId);
      if (!id) {
        resetData(true);
        state.status = "empty";
        render();
        return Promise.resolve({ ok: false, reason: "id" });
      }
      if (!requestFn) {
        resetData(true);
        state.status = "error";
        state.error = t("next.handoff.err.client_missing");
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
      render();
      return settle(requestFn(jobPath(id), { method: "GET" })).then(function (result) {
        if (!alive(gen) || state.token !== token) return { ok: false, stale: true };
        if (!result.ok) {
          state.busy = "none";
          state.status = "error";
          state.error = t("next.handoff.error_title") + "：" + describeError(result.error);
          state.retry = function () { loadJob(id); };
          render();
          return { ok: false, error: state.error };
        }
        var job = normalizeJob(result.value);
        if (!job) {
          state.busy = "none";
          state.status = "error";
          state.error = t("next.handoff.error_title");
          state.retry = function () { loadJob(id); };
          render();
          return { ok: false, error: state.error };
        }
        if (!job.id) job.id = id;
        state.job = job;
        return loadSecondary(job, gen, token);
      });
    }

    /* ── preview ────────────────────────────────────────────────── */

    function previewFailure(messageKey, vars) {
      state.busy = "none";
      state.preview.status = "error";
      state.preview.error = t(messageKey, vars || {});
      state.preview.items = [];
      state.preview.config = null;
      state.handoff.confirmOpen = false;
      render();
      return { ok: false, error: state.preview.error };
    }

    function fetchCandidateXyz(candidate, gen, token) {
      return settle(requestFn(structureFilePath(state.job.id, candidate.candidateId), {
        method: "GET",
        timeoutMs: REQUEST_TIMEOUT_MS
      })).then(function (result) {
        if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
        if (!result.ok) {
          return {
            ok: false,
            key: "next.handoff.err.xyz_read",
            vars: { candidate: candidate.candidateId, message: describeError(result.error) }
          };
        }
        return unwrapText(result.value).then(function (text) {
          if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
          var check = validateXyz(text, candidate.candidateId);
          if (!check.ok) {
            return {
              ok: false,
              key: "next.handoff.err.xyz_invalid",
              vars: { candidate: candidate.candidateId }
            };
          }
          if (!check.bound) {
            return {
              ok: false,
              key: "next.handoff.err.xyz_unbound",
              vars: { candidate: candidate.candidateId }
            };
          }
          return { ok: true, text: text };
        });
      });
    }

    function settleStalePreview(gen) {
      if (!alive(gen)) return;
      if (state.busy === "preview") state.busy = "none";
      if (state.preview.status === "previewing") {
        state.preview.status = "stale";
        state.preview.error = t("next.handoff.preview_stale");
      }
      render();
    }

    function runPreview() {
      if (!state.mounted || state.disposed || state.busy !== "none") {
        return Promise.resolve({ ok: false, busy: true });
      }
      if (!state.job) return Promise.resolve({ ok: false, reason: "job" });
      var selected = selectedCandidates(state.candidates, state.selection);
      if (!selected.length) {
        return previewFailure("next.handoff.err.no_selection");
      }
      if (state.catalog.status !== "ready" || !state.profile) {
        if (state.catalog.status === "error" && state.catalog.error) {
          state.busy = "none";
          state.preview.status = "error";
          state.preview.error = state.catalog.error;
          state.preview.items = [];
          state.preview.config = null;
          state.handoff.confirmOpen = false;
          render();
          return Promise.resolve({ ok: false, error: state.preview.error });
        }
        return previewFailure("next.handoff.err.no_profile");
      }
      if (!requestFn) return previewFailure("next.handoff.err.client_missing");

      var gen = state.generation;
      var token = state.previewSeq + 1;
      state.previewSeq = token;
      state.preview.token = token;
      state.preview.status = "previewing";
      state.preview.error = "";
      state.preview.items = [];
      state.preview.config = null;
      state.handoff.confirmOpen = false;
      state.handoff.receipts = [];
      state.handoff.status = "idle";
      state.handoff.error = "";
      state.busy = "preview";
      render();

      var xyzByCandidate = {};
      var chain = Promise.resolve({ ok: true });
      selected.forEach(function (candidate) {
        chain = chain.then(function (previous) {
          if (!previous.ok) return previous;
          if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
          return fetchCandidateXyz(candidate, gen, token).then(function (result) {
            if (result.ok) xyzByCandidate[candidate.candidateId] = result.text;
            return result;
          });
        });
      });

      return chain.then(function (result) {
        if (!alive(gen) || state.preview.token !== token) {
          settleStalePreview(gen);
          return { ok: false, stale: true };
        }
        if (result.stale) {
          settleStalePreview(gen);
          return { ok: false, stale: true };
        }
        if (!result.ok) return previewFailure(result.key, result.vars);
        var bodies = buildHandoffBodies({
          job: state.job,
          candidates: selected,
          profile: state.profile,
          xyzByCandidate: xyzByCandidate
        });
        if (bodies.length !== selected.length) {
          return previewFailure("next.handoff.err.xyz_invalid", {
            candidate: selected.filter(function (candidate) {
              return !xyzByCandidate[candidate.candidateId];
            }).map(function (candidate) { return candidate.candidateId; }).join(", ") || "—"
          });
        }
        return settle(requestFn(ROUTES.configPreview, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ method: { profile: state.profile } }),
          timeoutMs: REQUEST_TIMEOUT_MS
        })).then(function (configResult) {
          if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
          if (!configResult.ok) {
            var status = errorStatus(configResult.error);
            return previewFailure("next.handoff.err.preview_endpoint", {
              status: status == null ? "?" : String(status),
              message: describeError(configResult.error)
            });
          }
          var configPayload = isObject(configResult.value) ? configResult.value : {};
          var configRoles = isObject(configPayload.roles) ? configPayload.roles : {};
          state.preview.config = {
            schema: textOf(configPayload.schema),
            configKey: textOf(configPayload.config_key),
            roles: {
              int: shortText(JSON.stringify(isObject(configRoles.int && configRoles.int.effective)
                ? configRoles.int.effective : {}), 160),
              ts: shortText(JSON.stringify(isObject(configRoles.ts && configRoles.ts.effective)
                ? configRoles.ts.effective : {}), 160)
            },
            orcaSummary: isObject(configPayload.orca_summary) ? configPayload.orca_summary : {}
          };
          return runPreflights(bodies, gen, token);
        });
      });
    }

    function runPreflights(bodies, gen, token) {
      var items = [];
      var chain = Promise.resolve({ ok: true });
      bodies.forEach(function (entry) {
        chain = chain.then(function (previous) {
          if (!previous.ok) return previous;
          if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
          return settle(requestFn(ROUTES.preflight, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(entry.body),
            timeoutMs: REQUEST_TIMEOUT_MS
          })).then(function (result) {
            if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
            if (!result.ok) {
              var status = errorStatus(result.error);
              return {
                ok: false,
                key: "next.handoff.err.preflight_endpoint",
                vars: { status: status == null ? "?" : String(status), message: describeError(result.error) }
              };
            }
            var preflight = normalizePreflight(result.value);
            if (!preflight.feasible || !preflight.digest) {
              var reasonText = preflight.reasons.map(function (reason) {
                return (reason.field ? reason.field + ": " : "") + reason.message;
              }).join("；") || "不可提交";
              return {
                ok: false,
                key: "next.handoff.err.preflight_rejected",
                vars: { candidate: entry.candidateId, message: shortText(reasonText) }
              };
            }
            items.push({
              candidateId: entry.candidateId,
              body: entry.body,
              digest: preflight.digest,
              feasible: true,
              reasons: preflight.reasons,
              advisories: preflight.advisories,
              idempotencyKey: idempotencyKeyFor(state.job.id, entry.candidateId, preflight.digest)
            });
            return { ok: true };
          });
        });
      });
      return chain.then(function (result) {
        if (!alive(gen) || state.preview.token !== token) {
          settleStalePreview(gen);
          return { ok: false, stale: true };
        }
        if (result.stale) {
          settleStalePreview(gen);
          return { ok: false, stale: true };
        }
        if (!result.ok) return previewFailure(result.key, result.vars);
        state.busy = "none";
        state.preview.status = "previewed";
        state.preview.items = items;
        state.preview.key = selectionKey(state.job.id, state.profile,
          selectedCandidates(state.candidates, state.selection));
        state.preview.error = "";
        state.handoff.status = "idle";
        state.handoff.confirmOpen = false;
        render();
        return { ok: true, items: items };
      });
    }

    /* ── confirm + submit ───────────────────────────────────────── */

    function onRequestConfirm() {
      if (!state.mounted || state.disposed || state.busy !== "none") return;
      if (state.preview.status !== "previewed" || !state.preview.items.length) return;
      if (state.preview.key !== currentPreviewKey()) {
        state.preview.status = "stale";
        state.preview.error = t("next.handoff.preview_stale");
        render();
        return;
      }
      state.handoff.confirmOpen = true;
      state.handoff.error = "";
      render();
    }

    function runSubmit() {
      if (!state.mounted || state.disposed || state.busy !== "none") {
        return Promise.resolve({ ok: false, busy: true });
      }
      if (!state.handoff.confirmOpen || state.preview.status !== "previewed" ||
          state.preview.key !== currentPreviewKey() || !state.preview.items.length) {
        return Promise.resolve({ ok: false, reason: "preview" });
      }
      if (!requestFn) return Promise.resolve({ ok: false, reason: "client" });
      var gen = state.generation;
      var token = state.preview.token;
      var items = state.preview.items.slice();
      var receipts = [];
      state.handoff.confirmOpen = false;
      state.handoff.status = "submitting";
      state.handoff.receipts = [];
      state.handoff.error = "";
      state.busy = "submit";
      render();

      var chain = Promise.resolve({ ok: true });
      items.forEach(function (item) {
        chain = chain.then(function (previous) {
          if (!previous.ok) return previous;
          if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
          var body = buildSubmitBody(item.body, item.digest);
          if (!body) {
            return {
              ok: false,
              mapped: {
                text: t("next.handoff.receipt_invalid"),
                retryable: false
              }
            };
          }
          return settle(requestFn(ROUTES.submit, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": item.idempotencyKey
            },
            body: JSON.stringify(body),
            timeoutMs: REQUEST_TIMEOUT_MS
          })).then(function (result) {
            if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
            if (!result.ok) {
              return { ok: false, mapped: mapSubmitError(result.error), status: errorStatus(result.error) };
            }
            var receipt = normalizeReceipt(result.value);
            if (!receipt) {
              return { ok: false, mapped: { text: t("next.handoff.receipt_invalid"), retryable: false } };
            }
            receipt.candidateId = item.candidateId;
            receipts.push(receipt);
            state.handoff.receipts = receipts.slice();
            render();
            return { ok: true };
          });
        });
      });

      return chain.then(function (result) {
        if (!alive(gen)) return { ok: false, stale: true };
        if (state.preview.token !== token || result.stale) {
          state.busy = "none";
          state.handoff.status = "error";
          state.handoff.error = t("next.handoff.err.submit_aborted");
          state.handoff.receipts = receipts.slice();
          render();
          return { ok: false, stale: true };
        }
        state.busy = "none";
        if (!result.ok) {
          state.handoff.status = "error";
          var message = result.mapped ? result.mapped.text : t("next.handoff.receipt_error", {
            message: "unknown error"
          });
          state.handoff.error = message;
          state.handoff.receipts = receipts.slice();
          if (result.status === 400 && result.mapped &&
              result.mapped.key === "next.handoff.err.submit_stale") {
            state.preview.status = "stale";
            state.preview.error = result.mapped.text;
          }
          render();
          return { ok: false, receipts: receipts.slice(), error: message };
        }
        state.handoff.status = receipts.length === items.length ? "submitted" : "error";
        state.handoff.receipts = receipts.slice();
        if (state.handoff.status === "error") {
          state.handoff.error = t("next.handoff.receipt_error", {
            message: t("next.handoff.receipt_invalid")
          });
        }
        render();
        return { ok: state.handoff.status === "submitted", receipts: receipts.slice() };
      });
    }

    /* ── context / lifecycle ────────────────────────────────────── */

    function applySnapshot(snapshot, options) {
      if (!state.mounted || state.disposed) return;
      options = options || {};
      snapshot = isObject(snapshot) ? snapshot : {};
      var taskRef = isObject(snapshot.taskRef) ? snapshot.taskRef : null;
      var jobId = taskRef && taskRef.jobId ? String(taskRef.jobId) : "";
      if (state.focusJobId !== jobId) {
        if (state.busy === "submit") {
          var lockMessage = t("next.handoff.err.focus_locked");
          if (state.warnings.indexOf(lockMessage) === -1) state.warnings.push(lockMessage);
          render();
          return;
        }
        state.focusJobId = jobId;
        if (jobId) {
          if (state.ui.jobInput) state.ui.jobInput.value = jobId;
          loadJob(jobId);
        } else {
          resetData(true);
          state.status = "empty";
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
        return Promise.reject(new TypeError("pes-handoff mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("pes-handoff: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("pes-handoff: already mounted (dispose() first)"));
      }
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.focusJobId = "__init__";
      state.jobInputValue = "";
      state.token = 0;
      state.status = "empty";
      state.busy = "none";
      state.error = "";
      state.retry = null;
      state.job = null;
      state.candidateSource = "";
      state.candidates = [];
      state.energy = null;
      state.review = null;
      state.warnings = [];
      state.catalog = { status: "idle", profiles: [], error: "" };
      state.profile = "";
      state.selection = {};
      state.previewSeq = 0;
      state.preview = { status: "idle", token: 0, key: "", items: [], config: null, error: "", notices: [] };
      state.handoff = { status: "idle", confirmOpen: false, receipts: [], error: "" };
      state.emptyReason = "";
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
      state.preview.token += 1;
      state.previewSeq += 1;
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
      state.candidates = [];
      state.candidateSource = "";
      state.energy = null;
      state.review = null;
      state.warnings = [];
      state.selection = {};
      state.preview = { status: "idle", token: 0, key: "", items: [], config: null, error: "", notices: [] };
      state.handoff = { status: "idle", confirmOpen: false, receipts: [], error: "" };
      state.retry = null;
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
      normalizeJob: normalizeJob,
      normalizeMethodCatalog: normalizeMethodCatalog,
      defaultProfileFor: defaultProfileFor,
      normalizeEnergyGraph: normalizeEnergyGraph,
      energyForFrame: energyForFrame,
      normalizePesCandidates: normalizePesCandidates,
      normalizeFrameCandidates: normalizeFrameCandidates,
      selectedCandidates: selectedCandidates,
      selectionKey: selectionKey,
      validateXyz: validateXyz,
      buildHandoffBodies: buildHandoffBodies,
      buildSubmitBody: buildSubmitBody,
      normalizePreflight: normalizePreflight,
      normalizeReceipt: normalizeReceipt,
      mapSubmitError: mapSubmitError,
      idempotencyKeyFor: idempotencyKeyFor,
      roleToken: roleToken,
      jobPath: jobPath,
      pesReviewPath: pesReviewPath,
      frameCandidatesPath: frameCandidatesPath,
      energyPath: energyPath,
      structureFilePath: structureFilePath,
      ROUTES: ROUTES,
      DEFAULT_PROFILE: DEFAULT_PROFILE,
      i18n: ZH
    }
  };
})();

