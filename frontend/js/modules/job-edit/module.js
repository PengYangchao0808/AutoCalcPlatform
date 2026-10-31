/*
 * ACP 3.0 module — job-edit (Track A / A-10 · A-11 · A-12 · P03 修改参数后重算).
 *
 * Classic script, no build, no ESM. Registers exactly one global key
 * (`window.ACPNextModules["job-edit"]`); the Shell loader resolves the factory
 * and the returned instance is mounted into an arbitrary host element.
 *
 * Scope (P03 / A-10, A-11, A-12):
 *   A-10 — load `GET /api/v1/jobs/{job_id}/edit-draft` and backfill the panel
 *          from the task's REAL frozen parameters.  Every field rendered here
 *          comes from `editable_spec` / `input_refs` / `effective_config` /
 *          `capabilities` of that response; nothing is defaulted locally.  A
 *          parameter the server did not record stays blank ("未设置") and is
 *          never written into a request.
 *   A-11 — `POST /api/v1/jobs/{job_id}/edit-recalculate/preview`: server-side
 *          validation + diff, zero execution.  The panel renders the server
 *          change list (`diff`), `warnings`, `blocking_reasons` and the
 *          `preview_fingerprint` digest.
 *   A-12 — `POST /api/v1/jobs/{job_id}/edit-recalculate` with
 *          `expected_source_revision` + `request_id` (+ the preview
 *          fingerprint) exactly as the route requires.  The receipt shows the
 *          new attempt (in-place rerun = attempt+1, same task directory
 *          contract), the replay flag and the edit-coverage warning.
 *
 * Editing surface (deliberately narrow — the wizard owns create-task forms):
 *   - mode (in_place / new_job), gated by the server's `capabilities`;
 *   - `method` and `resources` raw JSON editors, pre-filled verbatim with the
 *     frozen objects (`JSON.stringify(..., null, 2)`), so types such as
 *     `nproc: 8` or `mem: "16GB"` survive an untouched round-trip;
 *   - `input.charge` / `input.multiplicity`, pre-filled only when recorded;
 *   - `task_name` / `remark` / `tags`.
 *   Only CHANGED sections are sent (`_build_edited_spec` merges supplied
 *   sections), so an untouched draft produces an empty server diff.
 *   Workflow switching, structure-source replacement (P04/A-13) and
 *   project/target-node changes are out of scope; they are rendered read-only.
 *
 * Data flow: the injected client seam only (`deps.request` ->
 * `deps.client.transport.request` -> `window.ACPClient.transport.request`).
 * The module performs no direct network call, reads no page globals, builds no
 * filesystem path (no task-directory reference), keeps no second task cache
 * (state lives in this instance) and never calls any other task mutation
 * endpoint.
 *
 * Four states live on the module root `data-acp-module-state` =
 * empty | loading | ready | error (the draft lifecycle).  Command state is
 * separate: `data-acp-busy` = none | draft | preview | execute, plus
 * `data-acp-preview-state` (idle | invalid | running | ok | blocked | error),
 * `data-acp-receipt-state` (idle | running | submitted | error),
 * `data-acp-draft-stale`, `data-acp-coverage` (editable | blocked) and
 * `data-acp-dirty`.  Every async result is guarded by the mount generation and
 * a per-request token, so dispose / a newer draft / a newer preview can never
 * write into the DOM.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "job-edit";
  var API = "/api/v1";
  var MODES = ["in_place", "new_job"];
  var MAX_STRUCTURE_ROWS = 20;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["job.edit"],
    binds: ["taskRef"],
    capabilities: { requires: [], provides: ["job.edit"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.jobedit.errorTitle", messageKey: "next.jobedit.errorMessage" }
  };

  /* Local zh dictionary (module copy). The Shell dictionary is consulted first;
   * `ACPShell.i18n.t()` echoes the key when no translation exists, so these
   * values are the real fallback until the A-61 batch adds `next.jobedit.*`
   * (README §7 lists the full key set). Static chrome also carries data-i18n. */
  var ZH = {
    "next.jobedit.title": "编辑重算",
    "next.jobedit.subtitle": "载入任务真实冻结参数 · 预览变更清单 · 原地 attempt+1",
    "next.jobedit.errorTitle": "编辑重算不可用",
    "next.jobedit.errorMessage": "job-edit 模块加载失败，请查看控制台。",
    "next.jobedit.source_title": "任务与草稿",
    "next.jobedit.job_ref": "当前任务：{jobId}",
    "next.jobedit.job_no_ref": "未选择任务：请在任务面板选中一个任务后再打开编辑重算。",
    "next.jobedit.load": "载入编辑草稿",
    "next.jobedit.reload": "重新载入草稿",
    "next.jobedit.loading": "正在载入编辑草稿…",
    "next.jobedit.empty": "尚未载入编辑草稿",
    "next.jobedit.empty_hint": "选中任务后模块会自动载入编辑草稿；也可点击「载入编辑草稿」。",
    "next.jobedit.draft_ready": "草稿已载入：工作流 {workflow} · 任务状态 {status} · 第 {attempt} 次尝试",
    "next.jobedit.draft_stale": "源任务配置已变化：请重新载入草稿后再预览或执行。",
    "next.jobedit.draft_error": "编辑草稿载入失败",
    "next.jobedit.retry": "重试",
    "next.jobedit.value_unset": "未设置",
    "next.jobedit.yes": "是",
    "next.jobedit.no": "否",
    "next.jobedit.coverage_title": "编辑覆盖",
    "next.jobedit.coverage_editable": "工作流 {workflow}（{status}）已登记通用编辑重算。",
    "next.jobedit.coverage_blocked": "工作流 {workflow}（{status}）未登记通用编辑重算。",
    "next.jobedit.coverage_reason": "限制：{reason}",
    "next.jobedit.coverage_hint": "迁移建议：{hint}",
    "next.jobedit.coverage_missing": "提交依赖缺失：{fields}",
    "next.jobedit.coverage_preserved": "保留方法字段 {count} 个。",
    "next.jobedit.notes_title": "草稿提示",
    "next.jobedit.original_title": "原始提交参数（只读）",
    "next.jobedit.field_workflow": "工作流",
    "next.jobedit.field_workflow_status": "工作流状态",
    "next.jobedit.field_job_status": "任务状态",
    "next.jobedit.field_attempt": "当前 attempt",
    "next.jobedit.field_source_revision": "源版本",
    "next.jobedit.field_molecule_name": "分子名",
    "next.jobedit.field_project_id": "项目",
    "next.jobedit.field_execution_mode": "执行模式",
    "next.jobedit.field_target_node": "目标节点",
    "next.jobedit.field_config_path": "配置路径",
    "next.jobedit.field_tags": "标签",
    "next.jobedit.input_title": "冻结输入",
    "next.jobedit.input_kind": "输入类型",
    "next.jobedit.input_charge": "电荷",
    "next.jobedit.input_multiplicity": "多重度",
    "next.jobedit.input_items": "结构条目数",
    "next.jobedit.input_source_job": "来源任务",
    "next.jobedit.input_artifact": "来源产物",
    "next.jobedit.input_materialized": "输入已物化",
    "next.jobedit.input_structures": "可复用结构 {count} 个",
    "next.jobedit.input_structure_item": "{name} · {atoms} 原子 · {state}",
    "next.jobedit.input_more": "另有 {count} 个结构未展开",
    "next.jobedit.input_available": "可用",
    "next.jobedit.input_missing": "几何缺失",
    "next.jobedit.input_outputs": "可复用结构产物 {count} 个",
    "next.jobedit.input_outputs_state": "产物状态：{state}",
    "next.jobedit.effective_title": "生效配置",
    "next.jobedit.effective_state": "{status} · 来源 {source}",
    "next.jobedit.effective_unavailable": "不可用（草稿仅按 spec 回填）",
    "next.jobedit.edit_title": "可编辑参数",
    "next.jobedit.mode": "编辑模式",
    "next.jobedit.mode_in_place": "原地重算（同一任务）",
    "next.jobedit.mode_new_job": "创建新任务",
    "next.jobedit.mode_hint_in_place": "复用同一任务目录与任务身份；提交后 attempt+1，旧 attempt 回执归档。",
    "next.jobedit.mode_hint_new_job": "创建一个新任务并记录来源关系；原任务保持不变。",
    "next.jobedit.method": "方法 / 计算参数（JSON，原样回填）",
    "next.jobedit.method_hint": "以下 JSON 逐字来自任务真实冻结参数；仅修改需要变更的字段。",
    "next.jobedit.resources": "资源（JSON，原样回填）",
    "next.jobedit.resources_hint": "仅包含任务真实记录的资源字段；缺失的键不会补默认值。",
    "next.jobedit.charge": "电荷",
    "next.jobedit.multiplicity": "自旋多重度",
    "next.jobedit.task_name": "任务名",
    "next.jobedit.remark": "备注",
    "next.jobedit.tags": "标签（逗号分隔）",
    "next.jobedit.projection": "服务端方法投影（只读）",
    "next.jobedit.reset_form": "放弃修改并回填",
    "next.jobedit.form_notice_no_changes": "未检测到参数变更；预览将返回空变更清单。",
    "next.jobedit.form_notice_task_name": "服务端在任务名为空时会沿用原值，因此无法通过本面板清空任务名。",
    "next.jobedit.form_notice_stale": "草稿已过期，请重新载入后再编辑。",
    "next.jobedit.err.draft_missing": "尚未载入编辑草稿。",
    "next.jobedit.err.workflow_blocked": "该工作流未登记通用编辑重算。",
    "next.jobedit.err.mode_invalid": "请选择编辑模式。",
    "next.jobedit.err.mode_unavailable": "当前任务不支持该编辑模式（{mode}）。",
    "next.jobedit.err.method_json": "方法 JSON 解析失败：{message}",
    "next.jobedit.err.method_object": "方法必须是 JSON 对象。",
    "next.jobedit.err.resources_json": "资源 JSON 解析失败：{message}",
    "next.jobedit.err.resources_object": "资源必须是 JSON 对象。",
    "next.jobedit.err.charge_integer": "电荷必须是整数。",
    "next.jobedit.err.multiplicity_integer": "自旋多重度必须是整数。",
    "next.jobedit.err.multiplicity_positive": "自旋多重度必须 ≥ 1。",
    "next.jobedit.err.unknown": "参数未通过本地校验。",
    "next.jobedit.preview_title": "预览变更",
    "next.jobedit.preview": "预览变更",
    "next.jobedit.previewing": "预览中…",
    "next.jobedit.preview_idle": "尚未预览。预览由服务端零副作用执行，不会提交计算。",
    "next.jobedit.preview_ok": "预览通过：{count} 项变更",
    "next.jobedit.preview_blocked": "预览被阻塞（{count} 项）：不能执行",
    "next.jobedit.preview_error": "预览失败：{message}",
    "next.jobedit.preview_need_valid": "参数未通过本地校验，未发送预览。",
    "next.jobedit.preview_need_draft": "请先载入编辑草稿。",
    "next.jobedit.preview_digest": "预览指纹：{digest}",
    "next.jobedit.diff_title": "变更清单（服务端计算）",
    "next.jobedit.diff_row": "{path} · {kind}：{old} → {new}",
    "next.jobedit.diff_empty": "无变更（服务端 diff 为空）。",
    "next.jobedit.warnings_title": "预览提示",
    "next.jobedit.blocking_title": "阻塞原因",
    "next.jobedit.execute_title": "执行编辑重算",
    "next.jobedit.execute": "执行编辑重算",
    "next.jobedit.executing": "执行中…",
    "next.jobedit.execute_idle": "预览通过后才会启用执行；执行会创建一个 attempt。",
    "next.jobedit.execute_error": "执行失败：{message}",
    "next.jobedit.receipt": "执行回执",
    "next.jobedit.receipt_body": "任务 {jobId} · 第 {attempt} 次尝试 · 状态 {status} · 模式 {operation}",
    "next.jobedit.operation_in_place": "原地重算",
    "next.jobedit.operation_new_job": "创建新任务",
    "next.jobedit.receipt_attempt_ok": "同目录原地重跑：原第 {from} 次 → 第 {to} 次（attempt+1）；旧 attempt 回执归档。",
    "next.jobedit.receipt_attempt_new": "新任务从第 1 次尝试开始；原任务第 {from} 次不受影响。",
    "next.jobedit.receipt_attempt_mismatch": "注意：服务端返回 attempt {attempt}，预期 {expected}，请核对任务历史。",
    "next.jobedit.receipt_coverage": "编辑覆盖：工作流 {workflow}（{status}）；保留方法字段 {preserved} 个；缺字段 {missing} 个。",
    "next.jobedit.receipt_diff": "本次提交变更 {count} 项。",
    "next.jobedit.receipt_replay": "幂等重放：返回的是同一 request_id 的既有回执（未产生第二次执行）。",
    "next.jobedit.open_task": "在任务面板打开",
    "next.jobedit.execute_need_preview": "预览通过后才能执行。",
    "next.jobedit.err.load": "编辑草稿载入失败：{message}",
    "next.jobedit.err.draft_payload": "编辑草稿响应缺少必需字段（job_id / workflow / editable_spec / source_revision / attempt / capabilities）。",
    "next.jobedit.err.preview_payload": "预览响应缺少必需字段（preview_fingerprint / source_revision）。",
    "next.jobedit.err.receipt_payload": "执行回执缺少必需字段（job_id / attempt / operation / status）。",
    "next.jobedit.err.not_found": "任务不存在（可能已被清除）。",
    "next.jobedit.err.forbidden": "没有权限执行该操作。",
    "next.jobedit.err.source_revision_conflict": "源任务配置已变化，请重新载入编辑草稿。",
    "next.jobedit.err.preview_fingerprint_conflict": "提交配置与预览不一致，请重新预览。",
    "next.jobedit.err.conflict": "冲突：{message}",
    "next.jobedit.err.invalid_spec": "参数未通过服务端校验：{message}",
    "next.jobedit.err.bad_request": "请求被拒绝：{message}",
    "next.jobedit.err.server": "服务端错误：{message}",
    "next.jobedit.err.network": "网络错误：{message}",
    "next.jobedit.err.client_missing": "客户端未注入（ACPClient.transport），无法读取或提交。"
  };

  /* Route composition single point. The three documented edit routes are the
   * only URLs this module ever builds; the job id is encoded, no filesystem
   * path (work dir / run root / task directory) is assembled here. */
  var ROUTES = {
    draft: function (jobId) {
      return API + "/jobs/" + encodeURIComponent(String(jobId)) + "/edit-draft";
    },
    preview: function (jobId) {
      return API + "/jobs/" + encodeURIComponent(String(jobId)) + "/edit-recalculate/preview";
    },
    execute: function (jobId) {
      return API + "/jobs/" + encodeURIComponent(String(jobId)) + "/edit-recalculate";
    }
  };

  /* ── shared helpers ─────────────────────────────────────────────── */

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

  function i18nEl(doc, tag, cls, key, vars) {
    var node = el(doc, tag, cls, t(key, vars));
    node.setAttribute("data-i18n", key);
    return node;
  }

  function textOf(value) {
    return value == null ? "" : String(value);
  }

  function isPlainObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function clearChildren(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") node.replaceChildren();
    else node.textContent = "";
  }

  /* Order-insensitive canonical JSON for local change detection. Undefined
   * normalises to null so "absent" and "null" compare equal (the server's wire
   * contract treats them identically). */
  function canonicalJson(value) {
    if (value === undefined) return "null";
    if (value === null || typeof value !== "object") {
      var encoded = JSON.stringify(value);
      return encoded === undefined ? "null" : encoded;
    }
    if (Array.isArray(value)) {
      return "[" + value.map(canonicalJson).join(",") + "]";
    }
    var keys = Object.keys(value).sort();
    return "{" + keys.map(function (key) {
      return JSON.stringify(key) + ":" + canonicalJson(value[key]);
    }).join(",") + "}";
  }

  function formatValue(value) {
    if (value === undefined) return "—";
    var text;
    if (typeof value === "string") text = value;
    else {
      try { text = JSON.stringify(value); } catch (error) { text = String(value); }
    }
    if (text == null) text = String(value);
    if (text.length > 120) text = text.slice(0, 117) + "…";
    return text;
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (isPlainObject(body)) {
      var detail = body.detail;
      if (isPlainObject(detail)) {
        var code = detail.error ? String(detail.error) : "";
        var message = detail.message ? String(detail.message) : "";
        if (message) return message;
        if (code) return code;
      }
      if (typeof detail === "string" && detail) return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  /* FastAPI structured 409 detail: {"error": "<code>", "message": "..."} */
  function errorCodeOf(error) {
    var body = error && error.body;
    var detail = body && body.detail;
    return isPlainObject(detail) && detail.error ? String(detail.error) : "";
  }

  function newRequestId() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "jobedit-" + crypto.randomUUID();
      }
    } catch (error) { /* fall through to the deterministic fallback */ }
    return "jobedit-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function parseTags(text) {
    return textOf(text).split(",").map(function (part) {
      return part.trim();
    }).filter(function (part) { return !!part; });
  }

  function integerOrNull(value) {
    var raw = textOf(value).trim();
    if (!/^[+-]?\d+$/.test(raw)) return null;
    return parseInt(raw, 10);
  }

  function numberOrNull(value) {
    if (typeof value === "number" && isFinite(value)) return value;
    if (typeof value === "string" && /^[+-]?\d+$/.test(value.trim())) {
      return parseInt(value.trim(), 10);
    }
    return null;
  }

  /* ── draft projection (pure) ────────────────────────────────────── */

  /**
   * Project an edit-draft response into the panel's read-back view model.
   * Every value is copied from the payload; nothing is defaulted.
   *
   * @param {object} draft GET /jobs/{id}/edit-draft body.
   * @returns {object} view model consumed by the render functions.
   */
  function readBackfill(draft) {
    draft = draft || {};
    var spec = isPlainObject(draft.editable_spec) ? draft.editable_spec : {};
    var caps = isPlainObject(draft.capabilities) ? draft.capabilities : {};
    var refs = isPlainObject(draft.input_refs) ? draft.input_refs : {};
    var original = isPlainObject(refs.original) ? refs.original : {};
    var items = Array.isArray(refs.structure_items) ? refs.structure_items : [];
    var outputs = Array.isArray(refs.previous_outputs) ? refs.previous_outputs : [];
    var effective = isPlainObject(draft.effective_config) ? draft.effective_config : {};
    return {
      jobId: textOf(draft.job_id),
      workflow: textOf(draft.workflow || spec.workflow),
      workflowStatus: textOf(draft.workflow_status),
      jobStatus: textOf(draft.job_status),
      attempt: Number(draft.attempt),
      sourceRevision: textOf(draft.source_revision),
      spec: spec,
      input: isPlainObject(spec.input) ? spec.input : {},
      method: isPlainObject(spec.method) ? spec.method : {},
      resources: isPlainObject(spec.resources) ? spec.resources : {},
      methodProjection: isPlainObject(draft.method_projection) ? draft.method_projection : {},
      capability: {
        canEdit: caps.can_edit === true,
        canInPlace: caps.can_in_place === true,
        canNewJob: caps.can_new_job === true,
        reasons: Array.isArray(caps.disabled_reasons) ? caps.disabled_reasons.map(String) : []
      },
      preservedFields: Array.isArray(draft.preserved_fields) ? draft.preserved_fields.map(String) : [],
      missingFields: Array.isArray(draft.missing_fields) ? draft.missing_fields.map(String) : [],
      migrationHint: draft.migration_hint ? String(draft.migration_hint) : "",
      notes: Array.isArray(draft.notes) ? draft.notes.map(String) : [],
      inputRefs: {
        mode: textOf(refs.mode),
        kind: textOf(original.kind),
        charge: original.charge,
        multiplicity: original.multiplicity,
        itemsCount: original.items_count,
        sourceJobId: textOf(original.source_job_id),
        fromArtifact: textOf(original.from_artifact),
        materialized: original.materialized_input_xyz === true,
        items: items,
        outputsCount: outputs.length,
        outputsAvailability: textOf(refs.outputs_availability)
      },
      effective: {
        status: textOf(effective.status),
        source: textOf(effective.source),
        config: effective.config == null ? null : effective.config
      }
    };
  }

  /** Capability-derived default mode: in_place first, new_job second, else "". */
  function defaultMode(view) {
    if (!view) return "";
    if (view.capability.canInPlace) return "in_place";
    if (view.capability.canNewJob) return "new_job";
    return "";
  }

  function jsonText(value) {
    try {
      return JSON.stringify(value == null ? {} : value, null, 2);
    } catch (error) {
      return "{}";
    }
  }

  /** Backfill form values verbatim from the draft (never a guessed default). */
  function formFromDraft(draft) {
    var payload = draft || {};
    var spec = isPlainObject(payload.editable_spec) ? payload.editable_spec : {};
    var inp = isPlainObject(spec.input) ? spec.input : {};
    var view = readBackfill(payload);
    return {
      mode: defaultMode(view),
      methodText: jsonText(spec.method),
      resourcesText: jsonText(spec.resources),
      charge: inp.charge == null ? "" : textOf(inp.charge),
      multiplicity: inp.multiplicity == null ? "" : textOf(inp.multiplicity),
      taskName: textOf(spec.task_name),
      remark: textOf(spec.remark),
      tagsText: Array.isArray(spec.tags) ? spec.tags.join(", ") : ""
    };
  }

  function emptyForm() {
    return {
      mode: "",
      methodText: "{}",
      resourcesText: "{}",
      charge: "",
      multiplicity: "",
      taskName: "",
      remark: "",
      tagsText: ""
    };
  }

  function formSnapshot(form) {
    return canonicalJson({
      mode: form.mode,
      methodText: form.methodText,
      resourcesText: form.resourcesText,
      charge: form.charge,
      multiplicity: form.multiplicity,
      taskName: form.taskName,
      remark: form.remark,
      tagsText: form.tagsText
    });
  }

  /**
   * Validate the edit form locally. The server preview stays authoritative
   * for chemistry/workflow validation; this only blocks requests that cannot
   * be meaningful (bad JSON, non-integer state, unavailable mode).
   *
   * @returns {{ok:boolean, errors:Array, method:object|null,
   *            resources:object|null, charge:(number|null),
   *            multiplicity:(number|null)}}
   */
  function validateForm(view, form) {
    form = form || emptyForm();
    var errors = [];
    if (!view) {
      return {
        ok: false,
        errors: [{ code: "draft_missing" }],
        method: null,
        resources: null,
        charge: null,
        multiplicity: null
      };
    }
    if (!view.capability.canEdit) errors.push({ code: "workflow_blocked" });
    if (MODES.indexOf(form.mode) < 0) {
      errors.push({ code: "mode_invalid" });
    } else if (form.mode === "in_place" && !view.capability.canInPlace) {
      errors.push({ code: "mode_unavailable", vars: { mode: t("next.jobedit.mode_in_place") } });
    } else if (form.mode === "new_job" && !view.capability.canNewJob) {
      errors.push({ code: "mode_unavailable", vars: { mode: t("next.jobedit.mode_new_job") } });
    }

    var method = null;
    try {
      method = JSON.parse(form.methodText);
      if (!isPlainObject(method)) {
        errors.push({ code: "method_object" });
        method = null;
      }
    } catch (error) {
      errors.push({
        code: "method_json",
        vars: { message: error && error.message ? error.message : "invalid JSON" }
      });
    }

    var resources = null;
    try {
      resources = JSON.parse(form.resourcesText);
      if (!isPlainObject(resources)) {
        errors.push({ code: "resources_object" });
        resources = null;
      }
    } catch (error) {
      errors.push({
        code: "resources_json",
        vars: { message: error && error.message ? error.message : "invalid JSON" }
      });
    }

    var charge = null;
    var chargeText = textOf(form.charge).trim();
    if (chargeText) {
      charge = integerOrNull(chargeText);
      if (charge === null) errors.push({ code: "charge_integer" });
    }

    var multiplicity = null;
    var multiplicityText = textOf(form.multiplicity).trim();
    if (multiplicityText) {
      multiplicity = integerOrNull(multiplicityText);
      if (multiplicity === null) errors.push({ code: "multiplicity_integer" });
      else if (multiplicity < 1) errors.push({ code: "multiplicity_positive" });
    }

    return {
      ok: errors.length === 0,
      errors: errors,
      method: method,
      resources: resources,
      charge: charge,
      multiplicity: multiplicity
    };
  }

  /**
   * Build the patch of CHANGED sections only. `_build_edited_spec` merges the
   * supplied sections into the stored spec, so an untouched draft sends no
   * section at all and previews to an empty diff.
   */
  function buildPatch(view, form, check) {
    var patch = {};
    if (!view) return patch;
    check = check || validateForm(view, form);
    if (!check.ok) return patch;

    var inputPatch = {};
    var originalCharge = numberOrNull(view.input.charge);
    var originalMultiplicity = numberOrNull(view.input.multiplicity);
    if (check.charge !== null && check.charge !== originalCharge) inputPatch.charge = check.charge;
    if (check.multiplicity !== null && check.multiplicity !== originalMultiplicity) {
      inputPatch.multiplicity = check.multiplicity;
    }
    if (Object.keys(inputPatch).length) patch.input = inputPatch;

    if (check.method !== null && canonicalJson(check.method) !== canonicalJson(view.method)) {
      patch.method = check.method;
    }
    if (check.resources !== null &&
        canonicalJson(check.resources) !== canonicalJson(view.resources)) {
      patch.resources = check.resources;
    }
    if (textOf(form.taskName) !== textOf(view.spec.task_name)) {
      patch.task_name = textOf(form.taskName);
    }
    if (textOf(form.remark) !== textOf(view.spec.remark)) {
      patch.remark = textOf(form.remark);
    }
    var originalTags = Array.isArray(view.spec.tags) ? view.spec.tags : [];
    var tags = parseTags(form.tagsText);
    if (canonicalJson(tags) !== canonicalJson(originalTags)) patch.tags = tags;
    return patch;
  }

  /**
   * Frozen preview body. `expected_source_revision` binds the request to the
   * draft; only changed sections are added (see buildPatch).
   */
  function buildPreviewBody(view, form) {
    var check = validateForm(view, form);
    if (!check.ok) return { ok: false, errors: check.errors, body: null, patch: {} };
    var body = { mode: form.mode, workflow: view.workflow };
    if (view.sourceRevision) body.expected_source_revision = view.sourceRevision;
    var patch = buildPatch(view, form, check);
    Object.keys(patch).forEach(function (key) { body[key] = patch[key]; });
    return { ok: true, errors: [], body: body, patch: patch, check: check };
  }

  /**
   * Execute body = the previewed body + `request_id` + `preview_fingerprint`.
   * The server's `_edit_request_payload` hash excludes exactly these three
   * fields, so a replayed body reuses the same operation record.
   */
  function buildExecuteBody(view, form, requestId, fingerprint) {
    var built = buildPreviewBody(view, form);
    if (!built.ok) return built;
    built.body.request_id = textOf(requestId);
    if (fingerprint) built.body.preview_fingerprint = textOf(fingerprint);
    return built;
  }

  function expectedAttempt(view, operation) {
    if (!view) return null;
    var base = Number(view.attempt);
    if (!isFinite(base) || base < 1) return null;
    return operation === "in_place" ? base + 1 : 1;
  }

  function isDraftPayload(payload) {
    return isPlainObject(payload) &&
      typeof payload.job_id === "string" && !!payload.job_id &&
      typeof payload.workflow === "string" && !!payload.workflow &&
      typeof payload.source_revision === "string" && !!payload.source_revision &&
      isPlainObject(payload.editable_spec) &&
      Number.isInteger(payload.attempt) &&
      isPlainObject(payload.capabilities);
  }

  function isPreviewPayload(payload) {
    return isPlainObject(payload) &&
      typeof payload.preview_fingerprint === "string" && !!payload.preview_fingerprint &&
      typeof payload.source_revision === "string" && !!payload.source_revision;
  }

  function isReceiptPayload(payload) {
    return isPlainObject(payload) &&
      typeof payload.job_id === "string" && !!payload.job_id &&
      Number.isInteger(payload.attempt) && payload.attempt >= 1 &&
      typeof payload.operation === "string" && !!payload.operation &&
      typeof payload.status === "string" && !!payload.status;
  }

  function mapError(error) {
    var status = error && error.status;
    var code = errorCodeOf(error);
    var message = describeError(error);
    if (status === 404) {
      return { message: t("next.jobedit.err.not_found"), staleDraft: true };
    }
    if (status === 401 || status === 403) {
      return { message: t("next.jobedit.err.forbidden") };
    }
    if (status === 409 && code === "source_revision_conflict") {
      return { message: t("next.jobedit.err.source_revision_conflict"), staleDraft: true };
    }
    if (status === 409 && code === "preview_fingerprint_conflict") {
      return {
        message: t("next.jobedit.err.preview_fingerprint_conflict"),
        invalidatePreview: true
      };
    }
    if (status === 409) {
      return {
        message: t("next.jobedit.err.conflict", { message: message }),
        resetRequestId: true
      };
    }
    if (status === 422) {
      return { message: t("next.jobedit.err.invalid_spec", { message: message }) };
    }
    if (status === 400) {
      return { message: t("next.jobedit.err.bad_request", { message: message }) };
    }
    if (status) {
      return { message: t("next.jobedit.err.server", { message: message }) };
    }
    return { message: t("next.jobedit.err.network", { message: message }) };
  }

  var FORM_ERROR_KEYS = {
    draft_missing: "err.draft_missing",
    workflow_blocked: "err.workflow_blocked",
    mode_invalid: "err.mode_invalid",
    mode_unavailable: "err.mode_unavailable",
    method_json: "err.method_json",
    method_object: "err.method_object",
    resources_json: "err.resources_json",
    resources_object: "err.resources_object",
    charge_integer: "err.charge_integer",
    multiplicity_integer: "err.multiplicity_integer",
    multiplicity_positive: "err.multiplicity_positive"
  };

  function formErrorText(error) {
    var key = FORM_ERROR_KEYS[error && error.code] || "err.unknown";
    return t("next.jobedit." + key, (error && error.vars) || null);
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;
    var commands = deps.commands || null;
    var explicitJobId = deps.jobId ? String(deps.jobId) : "";

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

    function idlePreview() {
      return {
        status: "idle",
        diff: [],
        warnings: [],
        blocking: [],
        fingerprint: null,
        sourceRevision: null,
        bodyKey: "",
        error: null,
        token: 0,
        revision: -1
      };
    }

    function idleExecute() {
      return {
        status: "idle",
        receipt: null,
        error: null,
        token: 0,
        requestId: null,
        requestKey: "",
        completedKey: "",
        attemptExpected: null,
        attemptMatches: null
      };
    }

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      root: null,
      snapshot: null,
      taskRef: null,
      projectId: "",
      explicitJobId: explicitJobId,
      jobId: explicitJobId,
      focusKey: explicitJobId ? explicitJobId + ":" : "",
      draft: { status: "empty", data: null, view: null, error: null, token: 0, stale: false },
      form: emptyForm(),
      baseline: "",
      formRevision: 0,
      formErrors: [],
      preview: idlePreview(),
      execute: idleExecute(),
      busy: "none",
      retry: null,
      ui: {},
      cleanup: [],
      unsubscribe: null
    };

    function alive(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function listen(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return function () {};
      node.addEventListener(type, handler);
      return function () {
        if (typeof node.removeEventListener === "function") {
          node.removeEventListener(type, handler);
        }
      };
    }

    /* ── DOM ─────────────────────────────────────────────────────── */

    function definitionRow(doc, field, key, role) {
      var row = el(doc, "div", "acp-jobedit-row");
      row.setAttribute("data-field", field);
      row.appendChild(i18nEl(doc, "dt", "acp-jobedit-term", key));
      var value = el(doc, "dd", "acp-jobedit-value");
      value.setAttribute("data-role", role + "-" + field);
      row.appendChild(value);
      return { row: row, value: value };
    }

    function textField(doc, role, key, kind) {
      var wrap = el(doc, "div", "acp-jobedit-field");
      var label = i18nEl(doc, "label", "acp-jobedit-label", key);
      var input = el(doc, "input", "acp-jobedit-input");
      input.type = "text";
      input.setAttribute("data-role", role);
      if (kind) input.setAttribute("inputmode", kind);
      label.appendChild(input);
      wrap.appendChild(label);
      return { wrap: wrap, input: input };
    }

    function buildDom() {
      var root = el(doc, "div", "acp-jobedit");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-acp-busy", "none");
      root.setAttribute("data-acp-mode", "none");
      root.setAttribute("data-acp-editable", "false");
      root.setAttribute("data-acp-coverage", "blocked");
      root.setAttribute("data-acp-preview-state", "idle");
      root.setAttribute("data-acp-receipt-state", "idle");
      root.setAttribute("data-acp-draft-stale", "false");
      root.setAttribute("data-acp-dirty", "false");

      var head = el(doc, "div", "acp-jobedit-head");
      head.appendChild(i18nEl(doc, "h2", "acp-jobedit-title", "next.jobedit.title"));
      head.appendChild(i18nEl(doc, "p", "acp-jobedit-sub", "next.jobedit.subtitle"));
      root.appendChild(head);

      var body = el(doc, "div", "acp-jobedit-body");
      root.appendChild(body);

      /* 1 — job focus + draft load */
      var sourceSection = el(doc, "section", "acp-jobedit-section");
      sourceSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.source_title"));
      var jobRef = el(doc, "p", "acp-jobedit-mono");
      jobRef.setAttribute("data-role", "job-ref");
      jobRef.textContent = t("next.jobedit.job_no_ref");
      sourceSection.appendChild(jobRef);
      state.ui.jobRef = jobRef;

      var loadBtn = el(doc, "button", "acp-jobedit-btn", t("next.jobedit.load"));
      loadBtn.type = "button";
      loadBtn.setAttribute("data-action", "load-draft");
      loadBtn.setAttribute("data-i18n", "next.jobedit.load");
      cleanupPush(listen(loadBtn, "click", function () { loadDraft(); }));
      state.ui.loadBtn = loadBtn;
      sourceSection.appendChild(loadBtn);

      var draftStatus = el(doc, "p", "acp-jobedit-status");
      draftStatus.setAttribute("data-role", "draft-status");
      draftStatus.setAttribute("role", "status");
      draftStatus.textContent = t("next.jobedit.empty");
      sourceSection.appendChild(draftStatus);
      state.ui.draftStatus = draftStatus;
      body.appendChild(sourceSection);

      /* 2 — edit-coverage registry projection */
      var coverageSection = el(doc, "section", "acp-jobedit-section");
      coverageSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.coverage_title"));
      var coverageState = el(doc, "p", "acp-jobedit-status");
      coverageState.setAttribute("data-role", "coverage-state");
      coverageSection.appendChild(coverageState);
      state.ui.coverageState = coverageState;
      var coverageReasons = el(doc, "ul", "acp-jobedit-list acp-jobedit-list--warn");
      coverageReasons.setAttribute("data-role", "coverage-reasons");
      coverageSection.appendChild(coverageReasons);
      state.ui.coverageReasons = coverageReasons;
      var preserved = el(doc, "p", "acp-jobedit-hint");
      preserved.setAttribute("data-role", "coverage-preserved");
      coverageSection.appendChild(preserved);
      state.ui.coveragePreserved = preserved;
      var migrationHint = el(doc, "p", "acp-jobedit-hint");
      migrationHint.setAttribute("data-role", "migration-hint");
      migrationHint.hidden = true;
      coverageSection.appendChild(migrationHint);
      state.ui.migrationHint = migrationHint;
      var missingFields = el(doc, "p", "acp-jobedit-hint acp-jobedit-hint--warn");
      missingFields.setAttribute("data-role", "missing-fields");
      missingFields.hidden = true;
      coverageSection.appendChild(missingFields);
      state.ui.missingFields = missingFields;
      var notesLabel = i18nEl(doc, "p", "acp-jobedit-subheading", "next.jobedit.notes_title");
      notesLabel.setAttribute("data-role", "notes-label");
      notesLabel.hidden = true;
      coverageSection.appendChild(notesLabel);
      state.ui.notesLabel = notesLabel;
      var notes = el(doc, "ul", "acp-jobedit-list");
      notes.setAttribute("data-role", "notes");
      notes.hidden = true;
      coverageSection.appendChild(notes);
      state.ui.notes = notes;
      body.appendChild(coverageSection);

      /* 3 — original submission parameters (read-only backfill) */
      var readbackSection = el(doc, "section", "acp-jobedit-section");
      readbackSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.original_title"));
      var fields = el(doc, "dl", "acp-jobedit-dl");
      state.ui.readback = {};
      [
        ["workflow", "next.jobedit.field_workflow"],
        ["workflow_status", "next.jobedit.field_workflow_status"],
        ["job_status", "next.jobedit.field_job_status"],
        ["attempt", "next.jobedit.field_attempt"],
        ["source_revision", "next.jobedit.field_source_revision"],
        ["molecule_name", "next.jobedit.field_molecule_name"],
        ["project_id", "next.jobedit.field_project_id"],
        ["execution_mode", "next.jobedit.field_execution_mode"],
        ["target_node", "next.jobedit.field_target_node"],
        ["config_path", "next.jobedit.field_config_path"],
        ["tags", "next.jobedit.field_tags"]
      ].forEach(function (entry) {
        var built = definitionRow(doc, entry[0], entry[1], "field");
        state.ui.readback[entry[0]] = built.value;
        fields.appendChild(built.row);
      });
      readbackSection.appendChild(fields);

      readbackSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.input_title"));
      var inputFields = el(doc, "dl", "acp-jobedit-dl");
      state.ui.inputs = {};
      [
        ["kind", "next.jobedit.input_kind"],
        ["charge", "next.jobedit.input_charge"],
        ["multiplicity", "next.jobedit.input_multiplicity"],
        ["items_count", "next.jobedit.input_items"],
        ["source_job_id", "next.jobedit.input_source_job"],
        ["from_artifact", "next.jobedit.input_artifact"],
        ["materialized", "next.jobedit.input_materialized"]
      ].forEach(function (entry) {
        var built = definitionRow(doc, entry[0], entry[1], "input");
        state.ui.inputs[entry[0]] = built.value;
        inputFields.appendChild(built.row);
      });
      readbackSection.appendChild(inputFields);

      var structuresText = el(doc, "p", "acp-jobedit-hint");
      structuresText.setAttribute("data-role", "structures-count");
      readbackSection.appendChild(structuresText);
      state.ui.structuresCount = structuresText;
      var structureItems = el(doc, "ul", "acp-jobedit-list");
      structureItems.setAttribute("data-role", "structure-items");
      readbackSection.appendChild(structureItems);
      state.ui.structureItems = structureItems;

      var outputsText = el(doc, "p", "acp-jobedit-hint");
      outputsText.setAttribute("data-role", "outputs-count");
      readbackSection.appendChild(outputsText);
      state.ui.outputsCount = outputsText;

      var effectiveText = el(doc, "p", "acp-jobedit-hint");
      effectiveText.setAttribute("data-role", "effective-config");
      readbackSection.appendChild(effectiveText);
      state.ui.effectiveConfig = effectiveText;
      var effectiveJson = el(doc, "pre", "acp-jobedit-pre");
      effectiveJson.setAttribute("data-role", "effective-config-json");
      effectiveJson.hidden = true;
      readbackSection.appendChild(effectiveJson);
      state.ui.effectiveJson = effectiveJson;
      body.appendChild(readbackSection);

      /* 4 — editable parameters */
      var editSection = el(doc, "section", "acp-jobedit-section");
      editSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.edit_title"));

      var modeField = el(doc, "div", "acp-jobedit-field");
      modeField.appendChild(i18nEl(doc, "span", "acp-jobedit-label", "next.jobedit.mode"));
      var modeRow = el(doc, "div", "acp-jobedit-radios");
      state.ui.modeInputs = [];
      [
        ["in_place", "next.jobedit.mode_in_place"],
        ["new_job", "next.jobedit.mode_new_job"]
      ].forEach(function (option) {
        var label = el(doc, "label", "acp-jobedit-radio");
        var input = el(doc, "input");
        input.type = "radio";
        input.name = "acp-jobedit-mode";
        input.value = option[0];
        input.setAttribute("data-role", "mode");
        input.checked = false;
        label.appendChild(input);
        label.appendChild(i18nEl(doc, "span", "acp-jobedit-radio-text", option[1]));
        modeRow.appendChild(label);
        state.ui.modeInputs.push(input);
        cleanupPush(listen(input, "change", onFormInput));
      });
      modeField.appendChild(modeRow);
      var modeHint = el(doc, "p", "acp-jobedit-hint");
      modeHint.setAttribute("data-role", "mode-hint");
      modeField.appendChild(modeHint);
      state.ui.modeHint = modeHint;
      editSection.appendChild(modeField);

      var methodField = el(doc, "div", "acp-jobedit-field");
      var methodLabel = i18nEl(doc, "label", "acp-jobedit-label", "next.jobedit.method");
      var methodText = el(doc, "textarea", "acp-jobedit-text");
      methodText.setAttribute("data-role", "method-text");
      methodText.setAttribute("rows", "10");
      methodText.setAttribute("spellcheck", "false");
      methodLabel.appendChild(methodText);
      methodField.appendChild(methodLabel);
      methodField.appendChild(i18nEl(doc, "p", "acp-jobedit-hint", "next.jobedit.method_hint"));
      editSection.appendChild(methodField);
      state.ui.methodText = methodText;
      cleanupPush(listen(methodText, "input", onFormInput));

      var resourcesField = el(doc, "div", "acp-jobedit-field");
      var resourcesLabel = i18nEl(doc, "label", "acp-jobedit-label", "next.jobedit.resources");
      var resourcesText = el(doc, "textarea", "acp-jobedit-text");
      resourcesText.setAttribute("data-role", "resources-text");
      resourcesText.setAttribute("rows", "4");
      resourcesText.setAttribute("spellcheck", "false");
      resourcesLabel.appendChild(resourcesText);
      resourcesField.appendChild(resourcesLabel);
      resourcesField.appendChild(i18nEl(doc, "p", "acp-jobedit-hint", "next.jobedit.resources_hint"));
      editSection.appendChild(resourcesField);
      state.ui.resourcesText = resourcesText;
      cleanupPush(listen(resourcesText, "input", onFormInput));

      var editGrid = el(doc, "div", "acp-jobedit-grid");
      [
        { role: "charge", key: "next.jobedit.charge", kind: "numeric" },
        { role: "multiplicity", key: "next.jobedit.multiplicity", kind: "numeric" },
        { role: "task-name", key: "next.jobedit.task_name" },
        { role: "remark", key: "next.jobedit.remark" },
        { role: "tags", key: "next.jobedit.tags" }
      ].forEach(function (field) {
        var built = textField(doc, field.role, field.key, field.kind);
        editGrid.appendChild(built.wrap);
        if (field.role === "charge") state.ui.charge = built.input;
        if (field.role === "multiplicity") state.ui.multiplicity = built.input;
        if (field.role === "task-name") state.ui.taskName = built.input;
        if (field.role === "remark") state.ui.remark = built.input;
        if (field.role === "tags") state.ui.tagsText = built.input;
        cleanupPush(listen(built.input, "input", onFormInput));
        cleanupPush(listen(built.input, "change", onFormInput));
      });
      editSection.appendChild(editGrid);

      var formNotices = el(doc, "ul", "acp-jobedit-list acp-jobedit-list--warn");
      formNotices.setAttribute("data-role", "form-notices");
      editSection.appendChild(formNotices);
      state.ui.formNotices = formNotices;

      var formErrors = el(doc, "ul", "acp-jobedit-errors");
      formErrors.setAttribute("data-role", "errors");
      editSection.appendChild(formErrors);
      state.ui.errors = formErrors;

      var resetBtn = el(doc, "button", "acp-jobedit-btn", t("next.jobedit.reset_form"));
      resetBtn.type = "button";
      resetBtn.setAttribute("data-action", "reset-form");
      resetBtn.setAttribute("data-i18n", "next.jobedit.reset_form");
      cleanupPush(listen(resetBtn, "click", function () { resetForm(); }));
      editSection.appendChild(resetBtn);
      state.ui.resetBtn = resetBtn;

      editSection.appendChild(i18nEl(doc, "p", "acp-jobedit-subheading", "next.jobedit.projection"));
      var projection = el(doc, "pre", "acp-jobedit-pre");
      projection.setAttribute("data-role", "projection");
      editSection.appendChild(projection);
      state.ui.projection = projection;
      body.appendChild(editSection);

      /* 5 — preview (no execution) */
      var previewSection = el(doc, "section", "acp-jobedit-section");
      previewSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.preview_title"));
      var previewBtn = el(doc, "button", "acp-jobedit-btn", t("next.jobedit.preview"));
      previewBtn.type = "button";
      previewBtn.setAttribute("data-action", "preview");
      previewBtn.setAttribute("data-i18n", "next.jobedit.preview");
      cleanupPush(listen(previewBtn, "click", function () { runPreview(); }));
      previewSection.appendChild(previewBtn);
      state.ui.previewBtn = previewBtn;

      var previewStatus = el(doc, "p", "acp-jobedit-status");
      previewStatus.setAttribute("data-role", "preview-status");
      previewStatus.setAttribute("role", "status");
      previewStatus.textContent = t("next.jobedit.preview_idle");
      previewSection.appendChild(previewStatus);
      state.ui.previewStatus = previewStatus;

      var previewDigest = el(doc, "p", "acp-jobedit-mono");
      previewDigest.setAttribute("data-role", "preview-digest");
      previewSection.appendChild(previewDigest);
      state.ui.previewDigest = previewDigest;

      previewSection.appendChild(i18nEl(doc, "p", "acp-jobedit-subheading", "next.jobedit.diff_title"));
      var diffList = el(doc, "ul", "acp-jobedit-diff");
      diffList.setAttribute("data-role", "diff");
      previewSection.appendChild(diffList);
      state.ui.diff = diffList;
      var diffEmpty = el(doc, "p", "acp-jobedit-hint");
      diffEmpty.setAttribute("data-role", "diff-empty");
      diffEmpty.hidden = true;
      previewSection.appendChild(diffEmpty);
      state.ui.diffEmpty = diffEmpty;

      var warningsLabel = i18nEl(doc, "p", "acp-jobedit-subheading", "next.jobedit.warnings_title");
      warningsLabel.setAttribute("data-role", "warnings-label");
      warningsLabel.hidden = true;
      previewSection.appendChild(warningsLabel);
      state.ui.warningsLabel = warningsLabel;
      var warnings = el(doc, "ul", "acp-jobedit-list acp-jobedit-list--warn");
      warnings.setAttribute("data-role", "warnings");
      warnings.hidden = true;
      previewSection.appendChild(warnings);
      state.ui.warnings = warnings;

      var blockingLabel = i18nEl(doc, "p", "acp-jobedit-subheading", "next.jobedit.blocking_title");
      blockingLabel.setAttribute("data-role", "blocking-label");
      blockingLabel.hidden = true;
      previewSection.appendChild(blockingLabel);
      state.ui.blockingLabel = blockingLabel;
      var blocking = el(doc, "ul", "acp-jobedit-errors");
      blocking.setAttribute("data-role", "blocking");
      blocking.hidden = true;
      previewSection.appendChild(blocking);
      state.ui.blocking = blocking;
      body.appendChild(previewSection);

      /* 6 — execute + receipt */
      var executeSection = el(doc, "section", "acp-jobedit-section");
      executeSection.appendChild(i18nEl(doc, "h3", "acp-jobedit-heading", "next.jobedit.execute_title"));
      var executeBtn = el(doc, "button", "acp-jobedit-btn acp-jobedit-btn--primary", t("next.jobedit.execute"));
      executeBtn.type = "button";
      executeBtn.disabled = true;
      executeBtn.setAttribute("data-action", "execute");
      executeBtn.setAttribute("data-i18n", "next.jobedit.execute");
      cleanupPush(listen(executeBtn, "click", function () { runExecute(); }));
      executeSection.appendChild(executeBtn);
      state.ui.executeBtn = executeBtn;

      var executeStatus = el(doc, "p", "acp-jobedit-status");
      executeStatus.setAttribute("data-role", "execute-status");
      executeStatus.setAttribute("role", "status");
      executeStatus.textContent = t("next.jobedit.execute_idle");
      executeSection.appendChild(executeStatus);
      state.ui.executeStatus = executeStatus;

      var receipt = el(doc, "div", "acp-jobedit-receipt");
      receipt.setAttribute("data-role", "receipt");
      receipt.setAttribute("role", "status");
      receipt.hidden = true;
      receipt.appendChild(i18nEl(doc, "h4", "acp-jobedit-receipt-title", "next.jobedit.receipt"));
      var receiptBody = el(doc, "p", "acp-jobedit-receipt-body");
      receiptBody.setAttribute("data-role", "receipt-body");
      receipt.appendChild(receiptBody);
      state.ui.receiptBody = receiptBody;
      var receiptAttempt = el(doc, "p", "acp-jobedit-receipt-line");
      receiptAttempt.setAttribute("data-role", "receipt-attempt");
      receipt.appendChild(receiptAttempt);
      state.ui.receiptAttempt = receiptAttempt;
      var receiptCoverage = el(doc, "p", "acp-jobedit-receipt-line");
      receiptCoverage.setAttribute("data-role", "receipt-coverage");
      receipt.appendChild(receiptCoverage);
      state.ui.receiptCoverage = receiptCoverage;
      var receiptDiff = el(doc, "p", "acp-jobedit-receipt-line");
      receiptDiff.setAttribute("data-role", "receipt-diff");
      receipt.appendChild(receiptDiff);
      state.ui.receiptDiff = receiptDiff;
      var receiptReplay = el(doc, "p", "acp-jobedit-hint", t("next.jobedit.receipt_replay"));
      receiptReplay.setAttribute("data-role", "receipt-replay");
      receiptReplay.hidden = true;
      receipt.appendChild(receiptReplay);
      state.ui.receiptReplay = receiptReplay;
      var openTaskBtn = el(doc, "button", "acp-jobedit-btn", t("next.jobedit.open_task"));
      openTaskBtn.type = "button";
      openTaskBtn.setAttribute("data-action", "open-task");
      openTaskBtn.setAttribute("data-i18n", "next.jobedit.open_task");
      openTaskBtn.hidden = true;
      cleanupPush(listen(openTaskBtn, "click", function () { openReceiptTask(); }));
      receipt.appendChild(openTaskBtn);
      state.ui.openTaskBtn = openTaskBtn;
      executeSection.appendChild(receipt);
      state.ui.receipt = receipt;
      body.appendChild(executeSection);

      var emptyHint = el(doc, "p", "acp-jobedit-empty");
      emptyHint.setAttribute("data-role", "empty-hint");
      emptyHint.textContent = t("next.jobedit.empty_hint");
      root.appendChild(emptyHint);
      state.ui.emptyHint = emptyHint;

      var overlay = el(doc, "div", "acp-jobedit-overlay");
      overlay.setAttribute("data-role", "overlay");
      overlay.hidden = true;
      var box = el(doc, "div", "acp-jobedit-box");
      var overlayMessage = el(doc, "p", "acp-jobedit-message");
      overlayMessage.setAttribute("data-role", "overlay-message");
      overlayMessage.setAttribute("aria-live", "polite");
      box.appendChild(overlayMessage);
      var overlayDetail = el(doc, "p", "acp-jobedit-detail");
      overlayDetail.setAttribute("data-role", "overlay-detail");
      overlayDetail.hidden = true;
      box.appendChild(overlayDetail);
      var retryBtn = el(doc, "button", "acp-jobedit-btn", t("next.jobedit.retry"));
      retryBtn.type = "button";
      retryBtn.setAttribute("data-action", "retry");
      retryBtn.setAttribute("data-i18n", "next.jobedit.retry");
      retryBtn.hidden = true;
      cleanupPush(listen(retryBtn, "click", function () { retryDraft(); }));
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

    function cleanupPush(fn) {
      if (typeof fn === "function") state.cleanup.push(fn);
    }

    /* ── form handling ──────────────────────────────────────────── */

    function readForm() {
      var ui = state.ui;
      var mode = state.form.mode;
      (ui.modeInputs || []).forEach(function (input) {
        if (input && input.checked) mode = input.value;
      });
      return {
        mode: mode,
        methodText: textOf(ui.methodText && ui.methodText.value),
        resourcesText: textOf(ui.resourcesText && ui.resourcesText.value),
        charge: textOf(ui.charge && ui.charge.value).trim(),
        multiplicity: textOf(ui.multiplicity && ui.multiplicity.value).trim(),
        taskName: textOf(ui.taskName && ui.taskName.value),
        remark: textOf(ui.remark && ui.remark.value),
        tagsText: textOf(ui.tagsText && ui.tagsText.value)
      };
    }

    function setInputValue(node, value) {
      if (node && textOf(node.value) !== value) node.value = value;
    }

    function syncForm() {
      var ui = state.ui;
      var view = state.draft.view;
      var mode = state.form.mode;
      (ui.modeInputs || []).forEach(function (input) {
        var allowed = view
          ? (input.value === "in_place" ? view.capability.canInPlace : view.capability.canNewJob)
          : false;
        input.checked = input.value === mode;
        input.disabled = !allowed || !view || !view.capability.canEdit || state.busy !== "none";
      });
      if (ui.modeHint) {
        if (mode === "new_job") ui.modeHint.textContent = t("next.jobedit.mode_hint_new_job");
        else if (mode === "in_place") ui.modeHint.textContent = t("next.jobedit.mode_hint_in_place");
        else ui.modeHint.textContent = "";
      }
      setInputValue(ui.methodText, state.form.methodText);
      setInputValue(ui.resourcesText, state.form.resourcesText);
      setInputValue(ui.charge, state.form.charge);
      setInputValue(ui.multiplicity, state.form.multiplicity);
      setInputValue(ui.taskName, state.form.taskName);
      setInputValue(ui.remark, state.form.remark);
      setInputValue(ui.tagsText, state.form.tagsText);
      if (ui.resetBtn) ui.resetBtn.disabled = !view || state.busy !== "none";
    }

    function refreshFormErrors() {
      if (!state.draft.view) {
        state.formErrors = [];
        return;
      }
      state.formErrors = validateForm(state.draft.view, state.form).errors;
    }

    function onFormInput() {
      if (!state.mounted || state.disposed) return;
      state.form = readForm();
      state.formRevision += 1;
      invalidatePreview();
      resetExecute();
      refreshFormErrors();
      render();
    }

    function resetForm() {
      if (!state.mounted || state.disposed || !state.draft.data) return;
      state.form = formFromDraft(state.draft.data);
      state.formRevision += 1;
      state.baseline = formSnapshot(state.form);
      invalidatePreview();
      resetExecute();
      refreshFormErrors();
      render();
    }

    function isDirty() {
      if (!state.draft.view) return false;
      return formSnapshot(state.form) !== state.baseline;
    }

    function invalidatePreview() {
      state.preview.token += 1;
      state.preview.status = "idle";
      state.preview.error = null;
      state.preview.diff = [];
      state.preview.warnings = [];
      state.preview.blocking = [];
      state.preview.fingerprint = null;
      state.preview.sourceRevision = null;
      state.preview.bodyKey = "";
      state.preview.revision = -1;
    }

    function resetExecute() {
      state.execute.token += 1;
      state.execute.status = "idle";
      state.execute.receipt = null;
      state.execute.error = null;
      state.execute.requestId = null;
      state.execute.requestKey = "";
      state.execute.completedKey = "";
      state.execute.attemptExpected = null;
      state.execute.attemptMatches = null;
    }

    /* ── draft lifecycle ────────────────────────────────────────── */

    function resetContentState() {
      state.draft = {
        status: "empty",
        data: null,
        view: null,
        error: null,
        token: state.draft.token,
        stale: false
      };
      state.form = emptyForm();
      state.baseline = "";
      state.formRevision = 0;
      state.formErrors = [];
      state.preview = idlePreview();
      state.execute = idleExecute();
      state.busy = "none";
      state.retry = null;
    }

    function applyDraft(payload) {
      var view = readBackfill(payload);
      state.draft = {
        status: "ready",
        data: payload,
        view: view,
        error: null,
        token: state.draft.token,
        stale: false
      };
      state.form = formFromDraft(payload);
      state.baseline = formSnapshot(state.form);
      state.formRevision += 1;
      state.formErrors = [];
      state.preview = idlePreview();
      state.execute = idleExecute();
      state.busy = "none";
      state.retry = loadDraft;
      refreshFormErrors();
      render();
    }

    function loadDraft() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (state.busy === "preview" || state.busy === "execute") {
        return Promise.resolve({ ok: false, reason: "busy" });
      }
      var jobId = state.jobId;
      if (!jobId) {
        resetContentState();
        render();
        return Promise.resolve({ ok: false, reason: "no-job" });
      }
      if (!requestFn) {
        state.draft.status = "error";
        state.draft.error = t("next.jobedit.err.client_missing");
        state.busy = "none";
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }
      var token = state.draft.token + 1;
      state.draft.token = token;
      var gen = state.generation;
      state.draft.status = "loading";
      state.draft.error = null;
      state.draft.stale = false;
      state.busy = "draft";
      state.retry = loadDraft;
      render();
      return Promise.resolve(requestFn(ROUTES.draft(jobId), { method: "GET" })).then(function (payload) {
        if (!alive(gen) || state.draft.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        if (!isDraftPayload(payload)) {
          state.draft.status = "error";
          state.draft.error = t("next.jobedit.err.draft_payload");
          render();
          return { ok: false, error: state.draft.error };
        }
        applyDraft(payload);
        return { ok: true };
      }).catch(function (error) {
        if (!alive(gen) || state.draft.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        var mapped = mapError(error);
        state.draft.status = "error";
        state.draft.error = mapped.staleDraft
          ? mapped.message
          : t("next.jobedit.err.load", { message: mapped.message });
        render();
        return { ok: false, error: state.draft.error };
      });
    }

    function retryDraft() {
      if (typeof state.retry === "function") state.retry();
      else loadDraft();
    }

    /* ── preview ────────────────────────────────────────────────── */

    function canPreview() {
      if (!state.draft.view || !requestFn) return false;
      if (state.busy !== "none") return false;
      if (state.draft.stale) return false;
      var caps = state.draft.view.capability;
      if (!caps.canEdit) return false;
      if (!caps.canInPlace && !caps.canNewJob) return false;
      if (MODES.indexOf(state.form.mode) < 0) return false;
      if (state.form.mode === "in_place" && !caps.canInPlace) return false;
      if (state.form.mode === "new_job" && !caps.canNewJob) return false;
      return true;
    }

    function runPreview() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (state.busy !== "none") return Promise.resolve({ ok: false, reason: "busy" });
      var view = state.draft.view;
      if (!view) {
        state.preview.status = "error";
        state.preview.error = t("next.jobedit.preview_need_draft");
        render();
        return Promise.resolve({ ok: false, reason: "no-draft" });
      }
      if (state.draft.stale) {
        state.preview.status = "error";
        state.preview.error = t("next.jobedit.draft_stale");
        render();
        return Promise.resolve({ ok: false, reason: "stale" });
      }
      var built = buildPreviewBody(view, state.form);
      state.formErrors = built.errors || [];
      if (!built.ok) {
        state.preview.status = "invalid";
        state.preview.error = null;
        state.preview.diff = [];
        state.preview.warnings = [];
        state.preview.blocking = [];
        state.preview.fingerprint = null;
        state.preview.revision = -1;
        state.preview.bodyKey = "";
        render();
        return Promise.resolve({ ok: false, reason: "invalid" });
      }
      if (!requestFn) {
        state.preview.status = "error";
        state.preview.error = t("next.jobedit.err.client_missing");
        render();
        return Promise.resolve({ ok: false, reason: "client" });
      }
      var bodyKey = canonicalJson(built.body);
      var token = state.preview.token + 1;
      state.preview.token = token;
      var gen = state.generation;
      state.preview.status = "running";
      state.preview.error = null;
      state.preview.diff = [];
      state.preview.warnings = [];
      state.preview.blocking = [];
      state.preview.fingerprint = null;
      state.preview.revision = -1;
      state.preview.bodyKey = "";
      state.busy = "preview";
      render();
      return Promise.resolve(requestFn(ROUTES.preview(view.jobId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(built.body)
      })).then(function (payload) {
        if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        if (!isPreviewPayload(payload)) {
          state.preview.status = "error";
          state.preview.error = t("next.jobedit.err.preview_payload");
          state.preview.fingerprint = null;
          state.preview.revision = -1;
          state.preview.bodyKey = "";
          render();
          return { ok: false, error: state.preview.error };
        }
        state.preview.diff = Array.isArray(payload.diff) ? payload.diff.filter(isPlainObject) : [];
        state.preview.warnings = Array.isArray(payload.warnings) ? payload.warnings.map(String) : [];
        state.preview.blocking = Array.isArray(payload.blocking_reasons)
          ? payload.blocking_reasons.map(String)
          : [];
        state.preview.fingerprint = String(payload.preview_fingerprint);
        state.preview.sourceRevision = String(payload.source_revision);
        state.preview.bodyKey = bodyKey;
        state.preview.revision = state.formRevision;
        state.preview.status = (payload.ok === true && state.preview.blocking.length === 0)
          ? "ok"
          : "blocked";
        if (state.preview.sourceRevision !== view.sourceRevision) {
          // Defensive: the expected revision was supplied, so the route would
          // have answered 409. Never trust a mismatched echo.
          state.draft.stale = true;
          state.preview.status = "blocked";
        }
        resetExecute();
        render();
        return {
          ok: state.preview.status === "ok",
          fingerprint: state.preview.fingerprint,
          diffCount: state.preview.diff.length
        };
      }).catch(function (error) {
        if (!alive(gen) || state.preview.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        var mapped = mapError(error);
        state.preview.status = "error";
        state.preview.error = mapped.message;
        state.preview.fingerprint = null;
        state.preview.sourceRevision = null;
        state.preview.revision = -1;
        state.preview.bodyKey = "";
        if (mapped.staleDraft) state.draft.stale = true;
        if (mapped.invalidatePreview) invalidatePreview();
        render();
        return { ok: false, error: mapped.message };
      });
    }

    /* ── execute ────────────────────────────────────────────────── */

    function canExecute() {
      if (!state.draft.view || !requestFn) return false;
      if (state.draft.stale) return false;
      if (state.busy !== "none") return false;
      if (state.preview.status !== "ok" || !state.preview.fingerprint) return false;
      if (state.preview.revision !== state.formRevision) return false;
      if (state.preview.bodyKey !== canonicalJson(buildPreviewBody(state.draft.view, state.form).body)) {
        return false;
      }
      var check = validateForm(state.draft.view, state.form);
      if (!check.ok) return false;
      var key = String(state.preview.fingerprint) + "|" + state.preview.bodyKey;
      if (state.execute.completedKey && state.execute.completedKey === key) return false;
      return true;
    }

    function runExecute() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (!canExecute()) {
        render();
        return Promise.resolve({ ok: false, reason: "blocked" });
      }
      var view = state.draft.view;
      var built = buildExecuteBody(view, state.form, "", state.preview.fingerprint);
      if (!built.ok) {
        state.formErrors = built.errors || [];
        render();
        return Promise.resolve({ ok: false, reason: "invalid" });
      }
      var bodyKey = canonicalJson((function () {
        var copy = {};
        Object.keys(built.body).forEach(function (key) {
          if (key !== "request_id" && key !== "preview_fingerprint") copy[key] = built.body[key];
        });
        return copy;
      })());
      if (bodyKey !== state.preview.bodyKey) {
        invalidatePreview();
        refreshFormErrors();
        render();
        return Promise.resolve({ ok: false, reason: "stale-preview" });
      }
      var key = String(state.preview.fingerprint) + "|" + bodyKey;
      if (!state.execute.requestId || state.execute.requestKey !== key) {
        state.execute.requestId = newRequestId();
        state.execute.requestKey = key;
      }
      var execute = buildExecuteBody(
        view,
        state.form,
        state.execute.requestId,
        state.preview.fingerprint
      );
      var token = state.execute.token + 1;
      state.execute.token = token;
      var gen = state.generation;
      state.execute.status = "running";
      state.execute.error = null;
      state.execute.receipt = null;
      state.busy = "execute";
      render();
      return Promise.resolve(requestFn(ROUTES.execute(view.jobId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(execute.body)
      })).then(function (payload) {
        if (!alive(gen) || state.execute.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        if (!isReceiptPayload(payload)) {
          state.execute.status = "error";
          state.execute.error = t("next.jobedit.err.receipt_payload");
          render();
          return { ok: false, error: state.execute.error };
        }
        var operation = String(payload.operation) === "new_job" ? "new_job" : "in_place";
        var expected = expectedAttempt(view, operation);
        state.execute.status = "ok";
        state.execute.receipt = {
          jobId: String(payload.job_id),
          attempt: Number(payload.attempt),
          operation: operation,
          status: String(payload.status),
          replayed: payload.replayed === true,
          diffCount: Array.isArray(payload.diff_summary) ? payload.diff_summary.length : 0
        };
        state.execute.completedKey = key;
        state.execute.attemptExpected = expected;
        state.execute.attemptMatches = expected === null
          ? null
          : Number(payload.attempt) === expected;
        if (operation === "in_place") state.draft.stale = true;
        render();
        deliverReceipt(state.execute.receipt);
        return { ok: true, receipt: state.execute.receipt };
      }).catch(function (error) {
        if (!alive(gen) || state.execute.token !== token) return { ok: false, stale: true };
        state.busy = "none";
        var mapped = mapError(error);
        state.execute.status = "error";
        state.execute.error = mapped.message;
        state.execute.receipt = null;
        if (mapped.staleDraft) state.draft.stale = true;
        if (mapped.invalidatePreview) invalidatePreview();
        if (mapped.resetRequestId) {
          state.execute.requestId = null;
          state.execute.requestKey = "";
        }
        render();
        return { ok: false, error: mapped.message };
      });
    }

    function deliverReceipt(receipt) {
      if (!commands || typeof commands.openTask !== "function" || !receipt || !receipt.jobId) return;
      try {
        commands.openTask({
          jobId: receipt.jobId,
          attempt: receipt.attempt,
          source: MODULE_ID
        });
      } catch (error) {
        if (typeof console !== "undefined" && console && console.error) {
          console.error("job-edit: openTask after execute failed", error);
        }
      }
    }

    function openReceiptTask() {
      if (state.execute.receipt) deliverReceipt(state.execute.receipt);
    }

    /* ── rendering ──────────────────────────────────────────────── */

    function renderJobRef() {
      if (state.ui.jobRef) {
        state.ui.jobRef.textContent = state.jobId
          ? t("next.jobedit.job_ref", { jobId: state.jobId })
          : t("next.jobedit.job_no_ref");
      }
      if (state.ui.loadBtn) {
        state.ui.loadBtn.disabled = !state.jobId || state.busy !== "none";
        state.ui.loadBtn.textContent = state.draft.status === "empty"
          ? t("next.jobedit.load")
          : t("next.jobedit.reload");
      }
    }

    function renderDraftStatus() {
      if (!state.ui.draftStatus) return;
      var status = state.draft.status;
      var view = state.draft.view;
      var text;
      if (status === "ready" && view) {
        text = t("next.jobedit.draft_ready", {
          workflow: view.workflow,
          status: view.jobStatus || view.workflowStatus,
          attempt: view.attempt
        });
        if (state.draft.stale) text += " " + t("next.jobedit.draft_stale");
      } else if (status === "loading") {
        text = t("next.jobedit.loading");
      } else if (status === "error") {
        text = t("next.jobedit.draft_error");
      } else {
        text = t("next.jobedit.empty");
      }
      state.ui.draftStatus.textContent = text;
    }

    function renderCoverage() {
      var view = state.draft.view;
      var caps = view ? view.capability : null;
      clearChildren(state.ui.coverageReasons);
      clearChildren(state.ui.notes);
      if (!view || !caps) {
        if (state.ui.coverageState) state.ui.coverageState.textContent = "";
        if (state.ui.coveragePreserved) state.ui.coveragePreserved.textContent = "";
        if (state.ui.migrationHint) state.ui.migrationHint.hidden = true;
        if (state.ui.missingFields) state.ui.missingFields.hidden = true;
        if (state.ui.notesLabel) state.ui.notesLabel.hidden = true;
        if (state.ui.notes) state.ui.notes.hidden = true;
        return;
      }
      if (state.ui.coverageState) {
        state.ui.coverageState.textContent = caps.canEdit
          ? t("next.jobedit.coverage_editable", {
            workflow: view.workflow,
            status: view.workflowStatus || view.jobStatus
          })
          : t("next.jobedit.coverage_blocked", {
            workflow: view.workflow,
            status: view.workflowStatus || view.jobStatus
          });
      }
      caps.reasons.forEach(function (reason) {
        var item = doc.createElement("li");
        item.setAttribute("data-coverage-reason", "1");
        item.textContent = t("next.jobedit.coverage_reason", { reason: reason });
        state.ui.coverageReasons.appendChild(item);
      });
      if (state.ui.coveragePreserved) {
        state.ui.coveragePreserved.textContent = t("next.jobedit.coverage_preserved", {
          count: view.preservedFields.length
        });
      }
      if (state.ui.migrationHint) {
        state.ui.migrationHint.textContent = view.migrationHint
          ? t("next.jobedit.coverage_hint", { hint: view.migrationHint })
          : "";
        state.ui.migrationHint.hidden = !view.migrationHint;
      }
      if (state.ui.missingFields) {
        state.ui.missingFields.textContent = view.missingFields.length
          ? t("next.jobedit.coverage_missing", { fields: view.missingFields.join(", ") })
          : "";
        state.ui.missingFields.hidden = view.missingFields.length === 0;
      }
      var showNotes = view.notes.length > 0;
      if (state.ui.notesLabel) state.ui.notesLabel.hidden = !showNotes;
      if (state.ui.notes) {
        state.ui.notes.hidden = !showNotes;
        view.notes.forEach(function (note) {
          var item = doc.createElement("li");
          item.setAttribute("data-note", "1");
          item.textContent = note;
          state.ui.notes.appendChild(item);
        });
      }
    }

    function unsetText(value) {
      return value == null || value === "" ? t("next.jobedit.value_unset") : String(value);
    }

    function renderReadback() {
      var view = state.draft.view;
      var fields = state.ui.readback || {};
      var inputs = state.ui.inputs || {};
      var spec = view ? view.spec : {};
      var readValues = view ? {
        workflow: view.workflow,
        workflow_status: view.workflowStatus,
        job_status: view.jobStatus,
        attempt: view.attempt,
        source_revision: view.sourceRevision,
        molecule_name: spec.molecule_name,
        project_id: spec.project_id,
        execution_mode: spec.execution_mode,
        target_node: spec.target_node,
        config_path: spec.config_path,
        tags: Array.isArray(spec.tags) ? spec.tags.join(", ") : ""
      } : {};
      Object.keys(fields).forEach(function (key) {
        fields[key].textContent = unsetText(readValues[key]);
      });

      var refs = view ? view.inputRefs : {};
      var inputValues = view ? {
        kind: refs.kind,
        charge: refs.charge,
        multiplicity: refs.multiplicity,
        items_count: refs.itemsCount,
        source_job_id: refs.sourceJobId,
        from_artifact: refs.fromArtifact,
        materialized: refs.materialized ? t("next.jobedit.yes") : t("next.jobedit.no")
      } : {};
      Object.keys(inputs).forEach(function (key) {
        inputs[key].textContent = unsetText(inputValues[key]);
      });

      if (state.ui.structuresCount) {
        state.ui.structuresCount.textContent = view
          ? t("next.jobedit.input_structures", { count: refs.items.length })
          : "";
      }
      if (state.ui.structureItems) {
        clearChildren(state.ui.structureItems);
        var items = view ? refs.items.slice(0, MAX_STRUCTURE_ROWS) : [];
        items.forEach(function (item) {
          var available = item && item.geometry_status === "available";
          var li = doc.createElement("li");
          li.setAttribute("data-structure-item", "1");
          li.setAttribute("data-atom-count", textOf(item && item.atom_count));
          li.setAttribute("data-geometry-status", textOf(item && item.geometry_status));
          li.textContent = t("next.jobedit.input_structure_item", {
            name: textOf(item && (item.name || item.item_id || item.source_id)),
            atoms: textOf(item && item.atom_count),
            state: available ? t("next.jobedit.input_available") : t("next.jobedit.input_missing")
          });
          state.ui.structureItems.appendChild(li);
        });
        var rest = view ? refs.items.length - items.length : 0;
        if (rest > 0) {
          var more = doc.createElement("li");
          more.setAttribute("data-structure-more", "1");
          more.textContent = t("next.jobedit.input_more", { count: rest });
          state.ui.structureItems.appendChild(more);
        }
      }
      if (state.ui.outputsCount) {
        var text = view ? t("next.jobedit.input_outputs", { count: refs.outputsCount }) : "";
        if (view && refs.outputsAvailability) {
          text += " · " + t("next.jobedit.input_outputs_state", { state: refs.outputsAvailability });
        }
        state.ui.outputsCount.textContent = text;
      }
      if (state.ui.effectiveConfig) {
        if (!view) {
          state.ui.effectiveConfig.textContent = "";
        } else if (view.effective.status === "unavailable" || !view.effective.status) {
          state.ui.effectiveConfig.textContent = t("next.jobedit.effective_unavailable");
        } else {
          state.ui.effectiveConfig.textContent = t("next.jobedit.effective_state", {
            status: view.effective.status,
            source: view.effective.source || "—"
          });
        }
      }
      if (state.ui.effectiveJson) {
        var config = view && view.effective.config != null ? view.effective.config : null;
        state.ui.effectiveJson.textContent = config == null
          ? ""
          : (function () {
            try { return JSON.stringify(config, null, 2); } catch (error) { return ""; }
          })();
        state.ui.effectiveJson.hidden = config == null;
      }
      if (state.ui.projection) {
        state.ui.projection.textContent = view
          ? (function () {
            try { return JSON.stringify(view.methodProjection, null, 2); } catch (error) { return ""; }
          })()
          : "";
      }
    }

    function renderFormErrors() {
      if (state.ui.errors) {
        clearChildren(state.ui.errors);
        state.formErrors.forEach(function (error) {
          var item = doc.createElement("li");
          item.setAttribute("data-error", textOf(error && error.code));
          item.textContent = formErrorText(error);
          state.ui.errors.appendChild(item);
        });
        state.ui.errors.hidden = state.formErrors.length === 0;
      }
    }

    function renderFormNotices() {
      if (!state.ui.formNotices) return;
      clearChildren(state.ui.formNotices);
      var view = state.draft.view;
      if (!view) return;
      var notices = [];
      if (state.draft.stale) notices.push(t("next.jobedit.form_notice_stale"));
      var check = validateForm(view, state.form);
      if (check.ok && Object.keys(buildPatch(view, state.form, check)).length === 0) {
        notices.push(t("next.jobedit.form_notice_no_changes"));
      }
      var originalTaskName = textOf(view.spec.task_name);
      if (originalTaskName && !textOf(state.form.taskName).trim()) {
        notices.push(t("next.jobedit.form_notice_task_name"));
      }
      notices.forEach(function (line) {
        var item = doc.createElement("li");
        item.setAttribute("data-notice", "1");
        item.textContent = line;
        state.ui.formNotices.appendChild(item);
      });
      state.ui.formNotices.hidden = notices.length === 0;
    }

    function renderPreview() {
      var status = state.preview.status;
      var text;
      if (status === "ok") {
        text = t("next.jobedit.preview_ok", { count: state.preview.diff.length });
      } else if (status === "blocked") {
        text = t("next.jobedit.preview_blocked", { count: state.preview.blocking.length });
      } else if (status === "running") {
        text = t("next.jobedit.previewing");
      } else if (status === "invalid") {
        text = t("next.jobedit.preview_need_valid");
      } else if (status === "error") {
        text = t("next.jobedit.preview_error", {
          message: state.preview.error || t("next.jobedit.err.unknown")
        });
      } else {
        text = t("next.jobedit.preview_idle");
      }
      if (state.ui.previewStatus) state.ui.previewStatus.textContent = text;
      if (state.ui.previewDigest) {
        state.ui.previewDigest.textContent = state.preview.fingerprint
          ? t("next.jobedit.preview_digest", { digest: state.preview.fingerprint })
          : "";
      }
      if (state.ui.diff) {
        clearChildren(state.ui.diff);
        state.preview.diff.forEach(function (entry) {
          var item = doc.createElement("li");
          item.setAttribute("data-diff-path", textOf(entry.path));
          item.setAttribute("data-diff-kind", textOf(entry.kind));
          item.textContent = t("next.jobedit.diff_row", {
            path: textOf(entry.path),
            kind: textOf(entry.kind),
            old: formatValue(entry.old),
            new: formatValue(entry.new)
          });
          state.ui.diff.appendChild(item);
        });
      }
      var showEmpty = (status === "ok" || status === "blocked") && state.preview.diff.length === 0;
      if (state.ui.diffEmpty) {
        state.ui.diffEmpty.hidden = !showEmpty;
        state.ui.diffEmpty.textContent = showEmpty ? t("next.jobedit.diff_empty") : "";
      }
      if (state.ui.warnings) {
        clearChildren(state.ui.warnings);
        var warnings = state.preview.warnings || [];
        warnings.forEach(function (line) {
          var item = doc.createElement("li");
          item.setAttribute("data-preview-warning", "1");
          item.textContent = line;
          state.ui.warnings.appendChild(item);
        });
        state.ui.warnings.hidden = warnings.length === 0;
        if (state.ui.warningsLabel) state.ui.warningsLabel.hidden = warnings.length === 0;
      }
      if (state.ui.blocking) {
        clearChildren(state.ui.blocking);
        var blocking = state.preview.blocking || [];
        blocking.forEach(function (line) {
          var item = doc.createElement("li");
          item.setAttribute("data-preview-blocking", "1");
          item.textContent = line;
          state.ui.blocking.appendChild(item);
        });
        state.ui.blocking.hidden = blocking.length === 0;
        if (state.ui.blockingLabel) state.ui.blockingLabel.hidden = blocking.length === 0;
      }
      if (state.ui.previewBtn) {
        state.ui.previewBtn.disabled = !canPreview();
        state.ui.previewBtn.textContent = status === "running"
          ? t("next.jobedit.previewing")
          : t("next.jobedit.preview");
      }
      if (state.ui.executeBtn) {
        state.ui.executeBtn.disabled = !canExecute();
        state.ui.executeBtn.textContent = state.execute.status === "running"
          ? t("next.jobedit.executing")
          : t("next.jobedit.execute");
        state.ui.executeBtn.setAttribute("title",
          canExecute() ? "" : t("next.jobedit.execute_need_preview"));
      }
    }

    function renderExecute() {
      if (state.ui.executeStatus) {
        if (state.execute.status === "error") {
          state.ui.executeStatus.textContent = t("next.jobedit.execute_error", {
            message: state.execute.error || t("next.jobedit.err.unknown")
          });
        } else if (state.execute.status === "running") {
          state.ui.executeStatus.textContent = t("next.jobedit.executing");
        } else if (state.execute.status === "ok") {
          state.ui.executeStatus.textContent = t("next.jobedit.receipt");
        } else if (state.preview.status === "ok") {
          state.ui.executeStatus.textContent = t("next.jobedit.execute_idle");
        } else {
          state.ui.executeStatus.textContent = t("next.jobedit.execute_need_preview");
        }
      }
      if (!state.ui.receipt) return;
      if (state.execute.status === "error" && state.execute.error) {
        state.ui.receipt.hidden = false;
        state.ui.receipt.setAttribute("data-acp-receipt-state", "error");
        state.ui.receiptBody.textContent = state.execute.error;
        state.ui.receiptAttempt.textContent = "";
        state.ui.receiptCoverage.textContent = "";
        state.ui.receiptDiff.textContent = "";
        state.ui.receiptReplay.hidden = true;
        if (state.ui.openTaskBtn) state.ui.openTaskBtn.hidden = true;
        return;
      }
      var receipt = state.execute.receipt;
      if (state.execute.status !== "ok" || !receipt) {
        state.ui.receipt.hidden = true;
        return;
      }
      state.ui.receipt.hidden = false;
      state.ui.receipt.setAttribute("data-acp-receipt-state", "submitted");
      state.ui.receiptBody.textContent = t("next.jobedit.receipt_body", {
        jobId: receipt.jobId,
        attempt: receipt.attempt,
        status: receipt.status,
        operation: t("next.jobedit.operation_" + receipt.operation)
      });
      var attemptText;
      if (receipt.operation === "new_job") {
        attemptText = t("next.jobedit.receipt_attempt_new", {
          from: state.draft.view ? state.draft.view.attempt : "—"
        });
      } else if (state.execute.attemptMatches === false) {
        attemptText = t("next.jobedit.receipt_attempt_mismatch", {
          attempt: receipt.attempt,
          expected: state.execute.attemptExpected
        });
      } else {
        attemptText = t("next.jobedit.receipt_attempt_ok", {
          from: state.draft.view ? state.draft.view.attempt : "—",
          to: receipt.attempt
        });
      }
      state.ui.receiptAttempt.textContent = attemptText;
      state.ui.receiptCoverage.textContent = state.draft.view
        ? t("next.jobedit.receipt_coverage", {
          workflow: state.draft.view.workflow,
          status: state.draft.view.workflowStatus || state.draft.view.jobStatus,
          preserved: state.draft.view.preservedFields.length,
          missing: state.draft.view.missingFields.length
        })
        : "";
      state.ui.receiptDiff.textContent = t("next.jobedit.receipt_diff", {
        count: receipt.diffCount
      });
      state.ui.receiptReplay.hidden = !receipt.replayed;
      if (state.ui.openTaskBtn) {
        state.ui.openTaskBtn.hidden = !(commands && typeof commands.openTask === "function");
      }
    }

    function renderOverlay() {
      if (!state.ui.overlay) return;
      var status = state.draft.status;
      var show = status === "loading" || status === "error";
      state.ui.overlay.hidden = !show;
      if (state.ui.emptyHint) state.ui.emptyHint.hidden = status !== "empty";
      if (!show) return;
      var isError = status === "error";
      state.ui.overlayMessage.textContent = isError
        ? t("next.jobedit.draft_error")
        : t("next.jobedit.loading");
      state.ui.overlayDetail.textContent = isError ? textOf(state.draft.error) : "";
      state.ui.overlayDetail.hidden = !isError || !state.draft.error;
      state.ui.retryBtn.hidden = !isError || typeof state.retry !== "function";
    }

    function render() {
      if (!state.root || state.suspended) return;
      var root = state.root;
      var view = state.draft.view;
      var caps = view ? view.capability : null;
      root.setAttribute("data-acp-module-state", state.draft.status);
      root.setAttribute("data-acp-busy", state.busy);
      root.setAttribute("data-acp-mode", state.form.mode || "none");
      root.setAttribute("data-acp-editable", caps && caps.canEdit ? "true" : "false");
      root.setAttribute("data-acp-coverage", caps && caps.canEdit ? "editable" : "blocked");
      root.setAttribute("data-acp-preview-state", state.preview.status);
      root.setAttribute("data-acp-draft-stale", state.draft.stale ? "true" : "false");
      root.setAttribute("data-acp-dirty", isDirty() ? "true" : "false");
      root.setAttribute("data-acp-receipt-state",
        state.execute.status === "ok"
          ? "submitted"
          : (state.execute.status === "error"
            ? "error"
            : (state.execute.status === "running" ? "running" : "idle")));
      renderJobRef();
      renderDraftStatus();
      renderCoverage();
      renderReadback();
      syncForm();
      renderFormErrors();
      renderFormNotices();
      renderPreview();
      renderExecute();
      renderOverlay();
    }

    /* ── context ────────────────────────────────────────────────── */

    function applySnapshot(snapshot) {
      if (!state.mounted || state.disposed) return;
      snapshot = snapshot || {};
      state.snapshot = snapshot;
      state.projectId = snapshot.projectId ? String(snapshot.projectId) : "";
      var ref = snapshot.taskRef || null;
      state.taskRef = ref;
      var nextJobId = ref && ref.jobId ? String(ref.jobId) : state.explicitJobId;
      // The workspace focus identity is (job, attempt): a rerun that lands a new
      // attempt must reload the draft (its source_revision/attempt changed),
      // exactly like exec-detail keys its detail request.
      var nextAttempt = ref && ref.attempt != null ? String(ref.attempt) : "";
      var nextFocusKey = nextJobId ? nextJobId + ":" + nextAttempt : "";
      if (nextFocusKey === state.focusKey) {
        if (nextJobId && state.draft.status === "empty" && state.busy === "none") {
          loadDraft();
        }
        return;
      }
      state.focusKey = nextFocusKey;
      state.jobId = nextJobId;
      state.draft.token += 1;
      state.preview.token += 1;
      state.execute.token += 1;
      resetContentState();
      render();
      if (nextJobId) loadDraft();
    }

    function onContextNotify() {
      if (!state.mounted || state.disposed) return;
      if (!context || typeof context.getSnapshot !== "function") return;
      try {
        applySnapshot(context.getSnapshot());
      } catch (error) { /* a malformed snapshot never breaks the panel */ }
    }

    /* ── lifecycle ──────────────────────────────────────────────── */

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("job-edit mount: host 必须是 DOM 元素"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("job-edit: document 不可用"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("job-edit: already mounted (dispose() first)"));
      }
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.cleanup.length = 0;
      state.jobId = state.explicitJobId;
      state.focusKey = state.explicitJobId ? state.explicitJobId + ":" : "";
      resetContentState();

      var apiContext = api.context || null;
      if (!context && apiContext) context = apiContext;
      var apiCommands = api.commands || null;
      if (!commands && apiCommands) commands = apiCommands;

      buildDom();
      render();

      var snapshot = null;
      if (context && typeof context.getSnapshot === "function") {
        try { snapshot = context.getSnapshot(); } catch (error) { snapshot = null; }
      }
      if (snapshot) applySnapshot(snapshot);
      else if (state.jobId) loadDraft();

      if (context && typeof context.subscribe === "function") {
        state.unsubscribe = context.subscribe(onContextNotify);
      }
      if (doc && typeof doc.addEventListener === "function") {
        var langHandler = function () {
          if (!state.mounted || state.disposed) return;
          render();
        };
        doc.addEventListener("acp:langchange", langHandler);
        state.cleanup.push(function () {
          doc.removeEventListener("acp:langchange", langHandler);
        });
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
      // Painting pauses; in-flight results still land in state and are painted
      // by resume() (no result is silently dropped, no request is cancelled).
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
      state.suspended = false;
      state.generation += 1;
      state.draft.token += 1;
      state.preview.token += 1;
      state.execute.token += 1;
      state.busy = "none";
      state.cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.unsubscribe) {
        try { state.unsubscribe(); } catch (error) { /* context already gone */ }
        state.unsubscribe = null;
      }
      if (state.root && state.root.parentNode &&
          typeof state.root.parentNode.removeChild === "function") {
        try { state.root.parentNode.removeChild(state.root); } catch (error) { /* detached */ }
      }
      state.root = null;
      state.host = null;
      state.ui = {};
      state.snapshot = null;
      state.taskRef = null;
      state.jobId = state.explicitJobId;
      state.focusKey = state.explicitJobId ? state.explicitJobId + ":" : "";
      resetContentState();
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
      ROUTES: ROUTES,
      MODES: MODES,
      readBackfill: readBackfill,
      defaultMode: defaultMode,
      formFromDraft: formFromDraft,
      formSnapshot: formSnapshot,
      validateForm: validateForm,
      buildPatch: buildPatch,
      buildPreviewBody: buildPreviewBody,
      buildExecuteBody: buildExecuteBody,
      expectedAttempt: expectedAttempt,
      canonicalJson: canonicalJson,
      parseTags: parseTags,
      integerOrNull: integerOrNull,
      formatValue: formatValue,
      isDraftPayload: isDraftPayload,
      isPreviewPayload: isPreviewPayload,
      isReceiptPayload: isReceiptPayload,
      newRequestId: newRequestId,
      MAX_STRUCTURE_ROWS: MAX_STRUCTURE_ROWS
    }
  };
})();
