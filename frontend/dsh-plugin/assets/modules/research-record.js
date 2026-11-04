/* file: js/modules/research-record/module.js */
/*
 * ACP Next — research-record (A-54 / A-55 / A-56).
 *
 * Three surfaces in one module, all backed by existing platform routes:
 *
 *   A-54  manual research question entry — a human writes a question/claim and
 *         cites evidence refs, then creates a research *record* (the existing
 *         `POST /api/v2/research/records`) or a research *proposal* (the
 *         existing `POST /api/v2/research/proposals`).  No model is involved.
 *   A-55  scientific evidence claim states — the server `claims[]` (or a
 *         `claim_statuses` map returned by the evidence-prepare route) are
 *         rendered verbatim, with execution facts / verification kept apart
 *         from explanation / limitations.  The module never derives, merges or
 *         upgrades a claim status.
 *   A-56  platform evidence projection — the read-only
 *         `GET /api/v2/research/records/{research_id}/evidence` view, showing
 *         the attempt binding, report digest, projection/provenance digests,
 *         claim states and digest-bound evidence refs.  There is no second
 *         evidence model and no local digest computation.
 *
 * Request bodies are the inspected route bodies (see README §"Request bodies"):
 *
 *   POST /api/v2/research/proposals
 *     { project_id?, session_seed, proposal{proposal_id, project_id, question,
 *       hypothesis, question_ref?, question_revision?, round, recipe_digest,
 *       science, budget, decision:"proposed"}, plan{workflow,input,method,
 *       resources}, budget?, expires_at?, idempotency_key? }
 *
 *   POST /api/v2/research/records
 *     { project_id?, session_seed, research_id?, proposal_ref, approval_ref,
 *       plan_definition_digest, round, decision, task_refs:[{project_id,
 *       job_id, attempt, release_id}], evidence_refs:[{evidence_id, kind,
 *       sha256, relative_path}], evidence_view?, human_edits? }
 *
 * Hard rules:
 *   - no direct `fetch` / XHR / second transport: the request adapter is
 *     injected (`deps.request`, `deps.client.transport.request`, or the page
 *     `ACPClient.transport.request`); with none the module renders an explicit
 *     error and performs no network call;
 *   - no task submission, no `/jobs` call, no job mutation: a research
 *     decision is not a task status;
 *   - no local claim adjudication: `supported|refuted|inconclusive|
 *     not_evaluated` are opaque server strings.  The module copies them and
 *     never computes an overall/combined verdict;
 *   - no local digest: every sha256 shown or submitted is either server text
 *     (evidence refs copied from the projection) or explicit human input;
 *   - no invented identity/science: the frozen binding (proposal/plan/science/
 *     budget/approval/task ref) comes from the host or an explicitly pasted
 *     JSON binding; a missing value is a readable refusal, never a default;
 *   - one generation + per-action token set: a late evidence read / submit
 *     after dispose, a selection switch or a newer request never paints.
 *
 * Classic browser script: no build, no ESM, no globals besides
 * `window.ACPNextModules`.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "research-record";
  var API_BASE = "/api/v2";
  var RECORDS_PATH = API_BASE + "/research/records";
  var PROPOSALS_PATH = API_BASE + "/research/proposals";

  /*: The frozen record decision vocabulary (``acp2.experience.research-record.v1``). */
  var DECISIONS = ["accept", "revise", "reject", "defer", "escalate"];
  /*: The server claim vocabulary (display only — never a local adjudicator). */
  var CLAIM_STATUSES = ["supported", "refuted", "inconclusive", "not_evaluated"];
  /*: C's plan-definition science fields (the platform digest requires all). */
  var SCIENCE_FIELDS = [
    "capability_steps",
    "initial_inputs",
    "method",
    "mapping",
    "charge",
    "multiplicity",
    "allowed_substitutions",
  ];
  /*: C's plan-definition budget fields. */
  var BUDGET_FIELDS = [
    "model_requests",
    "input_tokens",
    "output_tokens",
    "task_refs",
    "recipe_steps",
    "qc_invocations",
    "wall_seconds",
    "queue_seconds",
  ];
  var PROPOSAL_ID_RE = /^prop-[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$/;
  var DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "1.0.0",
    provides: ["research.record", "research.evidence"],
    binds: ["researchRef", "taskRef"],
    capabilities: { requires: [], provides: ["research.record", "research.evidence"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.record.errorTitle", messageKey: "next.record.errorMessage" },
  };

  /* Inline zh source of truth.  The Shell dictionary is consulted first; its
   * `t()` echoes the key when a translation is missing, so these literals are
   * the real fallback until the `next.record.*` block lands in
   * frontend/js/shell/i18n.js (see README integration steps). */
  var ZH = {
    "next.record.title": "研究记录（手工提问与证据）",
    "next.record.subtitle": "人工提问 → 研究记录/提案；平台证据投影只读，不提交任务",
    "next.record.badge.readonly": "只读证据",
    "next.record.badge.proposal": "目标：提案",
    "next.record.badge.record": "目标：记录",
    "next.record.section.evidence": "平台证据投影（只读）",
    "next.record.section.entry": "手工研究提问",
    "next.record.section.verification": "验证事实（服务端值）",
    "next.record.section.explanation": "解释与限制（不构成验证结论）",
    "next.record.section.refs": "证据引用（摘要绑定）",
    "next.record.section.binding": "任务与版本绑定",
    "next.record.section.claims": "命题状态（服务端原值）",
    "next.record.section.warnings": "数据提示",
    "next.record.empty": "尚未读取研究记录证据。",
    "next.record.empty_hint": "输入研究记录 ID 与会话种子后读取平台证据投影；没有记录时保持空态，绝不伪造证据。",
    "next.record.no_research_id": "未提供研究记录 ID（research_id）。",
    "next.record.no_project": "缺少项目范围（project_id），无法读取证据投影。",
    "next.record.loading": "正在读取平台证据投影…",
    "next.record.error": "证据投影加载失败：{message}",
    "next.record.errorTitle": "研究记录不可用",
    "next.record.errorMessage": "research-record 模块加载失败，请查看控制台。",
    "next.record.retry": "重试",
    "next.record.refresh": "刷新证据",
    "next.record.load_evidence": "读取证据",
    "next.record.source.research_id": "研究记录",
    "next.record.source.session_seed": "会话种子",
    "next.record.source.round": "轮次（可选）",
    "next.record.source.project": "项目",
    "next.record.source.hint": "GET /api/v2/research/records/{research_id}/evidence",
    "next.record.no_transport": "未注入请求通道（deps.request / client.transport.request），无法读取或创建研究记录。",
    "next.record.invalid_view": "服务端证据投影不完整。",
    "next.record.invalid_view_hint": "缺少字段：{fields}；本面板不会补造证据。",
    "next.record.no_claims": "服务端未返回命题状态。",
    "next.record.claims.note": "命题状态原样来自服务端；本面板不判定、不升级、不合并命题结论。",
    "next.record.claims.unknown": "未知状态（服务端原值：{status}）",
    "next.record.limitations.empty": "服务端未给出报告限制。",
    "next.record.blocking.empty": "没有阻塞性缺失检查。",
    "next.record.explanation.model_note": "模型解释不属于证据；本面板只显示服务端验证事实与限制。",
    "next.record.refs.empty": "没有摘要绑定的证据引用。",
    "next.record.refs.source": "来源：服务端证据投影",
    "next.record.label.research_id": "研究记录",
    "next.record.label.project_id": "项目",
    "next.record.label.round": "轮次",
    "next.record.label.principal": "主体（服务端）",
    "next.record.label.session_id": "会话",
    "next.record.label.execution": "执行状态",
    "next.record.label.evidence_status": "证据完整度",
    "next.record.label.qualification": "worker 限定",
    "next.record.label.overall_claim_status": "总体命题状态（服务端）",
    "next.record.label.normal_reuse": "正常复用",
    "next.record.label.report_id": "报告",
    "next.record.label.report_digest": "报告摘要",
    "next.record.label.report_schema": "报告 schema",
    "next.record.label.subject_id": "报告主体",
    "next.record.label.projection_digest": "投影摘要",
    "next.record.label.provenance_digest": "溯源摘要",
    "next.record.label.task_id": "任务",
    "next.record.label.attempt": "attempt",
    "next.record.label.release_id": "发布版本",
    "next.record.label.release_digest": "发布摘要",
    "next.record.label.claim_id": "命题",
    "next.record.label.claim_status": "状态",
    "next.record.label.validator": "校验器",
    "next.record.label.source_refs": "来源引用",
    "next.record.label.blocking": "阻塞缺失检查",
    "next.record.label.limitations": "限制",
    "next.record.label.claim_statement": "命题陈述",
    "next.record.label.binding": "绑定范围",
    "next.record.label.proposal_id": "提案",
    "next.record.label.plan_workflow": "工作流",
    "next.record.label.plan_definition_digest": "计划摘要",
    "next.record.label.science": "science 块",
    "next.record.label.budget": "budget 块",
    "next.record.claim.supported": "supported（支持）",
    "next.record.claim.refuted": "refuted（反驳）",
    "next.record.claim.inconclusive": "inconclusive（不确定）",
    "next.record.claim.not_evaluated": "not_evaluated（未评估）",
    "next.record.claim.unknown": "未知（{status}）",
    "next.record.decision.accept": "accept（接受）",
    "next.record.decision.revise": "revise（修订）",
    "next.record.decision.reject": "reject（拒绝）",
    "next.record.decision.defer": "defer（延后）",
    "next.record.decision.escalate": "escalate（上报）",
    "next.record.value.yes": "是",
    "next.record.value.no": "否",
    "next.record.value.none": "—",
    "next.record.entry.note": "本入口不需要模型：问题/主张由人工填写，证据引用来自服务端投影或人工输入。",
    "next.record.entry.question": "研究问题",
    "next.record.entry.claim": "主张 / 假设",
    "next.record.entry.question_placeholder": "用一句话描述要研究的问题",
    "next.record.entry.claim_placeholder": "可证伪的主张；作为提案的 hypothesis",
    "next.record.entry.target": "提交目标",
    "next.record.entry.target_proposal": "研究提案（/research/proposals）",
    "next.record.entry.target_record": "研究记录（/research/records）",
    "next.record.entry.decision": "研究决定",
    "next.record.entry.decision_placeholder": "选择本轮研究决定",
    "next.record.entry.evidence": "证据引用",
    "next.record.entry.evidence_hint": "每条引用必须带服务端摘要（sha256:…）；研究记录至少一条。",
    "next.record.entry.evidence_id": "引用 ID",
    "next.record.entry.kind": "类型",
    "next.record.entry.sha256": "sha256 摘要",
    "next.record.entry.relative_path": "相对路径（可选）",
    "next.record.entry.add_evidence": "添加证据引用",
    "next.record.entry.remove": "移除",
    "next.record.entry.use_projection": "使用证据投影中的引用",
    "next.record.entry.use_projection_empty": "当前证据投影没有可复制的引用。",
    "next.record.entry.binding_title": "冻结绑定（服务端计划 / 轮次上下文）",
    "next.record.entry.binding_hint": "绑定由宿主注入（deps.binding 或 context.researchBinding），也可在下方高级 JSON 中显式提供；缺失即拒绝，不使用默认值。",
    "next.record.entry.binding_json": "高级：绑定 JSON",
    "next.record.entry.apply_binding": "应用绑定",
    "next.record.entry.binding_applied": "绑定已应用。",
    "next.record.entry.binding_invalid": "绑定 JSON 无效：{message}",
    "next.record.entry.binding_not_object": "绑定 JSON 必须是对象。",
    "next.record.entry.submit_proposal": "创建研究提案",
    "next.record.entry.submit_record": "创建研究记录",
    "next.record.entry.submitting": "提交中…",
    "next.record.entry.note_proposal": "提案契约不包含证据引用；证据引用只在“研究记录”目标提交。",
    "next.record.entry.note_record": "研究记录契约不保存问题/主张正文；问题/主张作为 human_edits 溯源（需要服务端主体身份）。",
    "next.record.entry.note_no_binding": "缺少冻结绑定：请由宿主注入或应用绑定 JSON；不会使用默认值。",
    "next.record.entry.binding_mismatch": "当前证据投影与绑定不一致（{fields}）；提交时不携带该投影。",
    "next.record.entry.question_required": "请填写研究问题。",
    "next.record.entry.claim_required": "请填写主张/假设（提案的 hypothesis）。",
    "next.record.entry.no_binding": "缺少冻结绑定（proposal/plan/science/budget/task ref）。",
    "next.record.entry.project_required": "绑定缺少项目范围（project_id）。",
    "next.record.entry.session_required": "绑定缺少会话种子（session_seed）。",
    "next.record.entry.proposal_id_required": "绑定缺少提案 ID（prop-…）。",
    "next.record.entry.proposal_id_invalid": "提案 ID 必须是 prop-<id>。",
    "next.record.entry.recipe_digest_required": "绑定缺少配方摘要（recipe_digest:sha256:…）。",
    "next.record.entry.plan_required": "绑定缺少执行计划（plan.workflow）。",
    "next.record.entry.science_required": "绑定缺少完整的 science 块：{fields}。",
    "next.record.entry.budget_required": "绑定缺少完整的 budget 块：{fields}。",
    "next.record.entry.round_required": "绑定缺少轮次（round 为 ≥0 的整数）。",
    "next.record.entry.approval_required": "绑定缺少审批引用（approval_ref）。",
    "next.record.entry.plan_digest_required": "绑定缺少计划摘要（plan_definition_digest:sha256:…）。",
    "next.record.entry.task_ref_required": "绑定缺少任务引用（task_ref: project_id/job_id/attempt/release_id）。",
    "next.record.entry.decision_required": "请选择本轮研究决定。",
    "next.record.entry.decision_invalid": "研究决定不在冻结枚举内：{decision}。",
    "next.record.entry.evidence_required": "研究记录至少需要一条带 sha256 的证据引用。",
    "next.record.entry.evidence_view_refs_uncited": "服务端证据投影中有 {count} 条摘要未出现在证据引用里；请使用“使用证据投影中的引用”，否则记录无法绑定该投影。",
    "next.record.entry.evidence_id_required": "证据引用缺少 evidence_id。",
    "next.record.entry.kind_required": "证据引用缺少 kind。",
    "next.record.entry.digest_invalid": "{field} 必须是 sha256:<64 hex>。",
    "next.record.entry.binding_not_serializable": "绑定无法序列化为 JSON。",
    "next.record.notice.proposal_created": "研究提案已创建：{resource_id} · 版本 {revision} · 摘要 {digest}。",
    "next.record.notice.record_created": "研究记录已创建：{resource_id} · 版本 {revision} · 摘要 {digest}。",
    "next.record.notice.conflict": "服务端拒绝了变更（revision_conflict）：{message}。请复核证据后再提交。",
    "next.record.notice.access": "当前身份无权执行该操作（服务端拒绝）。",
    "next.record.notice.not_found": "目标记录不存在或当前身份不可见。",
    "next.record.notice.failed": "请求失败：{message}",
    "next.record.notice.retryable": "（可按重试）",
    "next.record.warning.claim_status_unknown": "命题 {claim_id} 的状态未知：{status}（服务端原值）。",
    "next.record.warning.projection_digest_missing": "服务端投影缺少 projection_digest。",
    "next.record.warning.evidence_refs_empty": "服务端投影没有证据引用。",
  };

  /* ── small helpers ─────────────────────────────────────────────────── */

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function str(value) {
    return value == null ? "" : String(value);
  }

  function trim(value) {
    return str(value).trim();
  }

  function intOrNull(value) {
    if (value === "" || value == null || typeof value === "boolean") return null;
    var number = Number(value);
    return Number.isInteger(number) ? number : null;
  }

  function isDigest(value) {
    return DIGEST_RE.test(str(value));
  }

  function cloneJson(value) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (error) {
      return null;
    }
  }

  function jsonKey(value) {
    try {
      return JSON.stringify(value) || "";
    } catch (error) {
      return "";
    }
  }

  function stringArray(value) {
    if (!Array.isArray(value)) return [];
    return value.map(function (item) { return str(item); });
  }

  /** DOM `children` is an HTMLCollection in browsers (no Array#forEach). */
  function childrenOf(node) {
    var out = [];
    if (!node || !node.children) return out;
    for (var index = 0; index < node.children.length; index += 1) {
      out.push(node.children[index]);
    }
    return out;
  }

  function displayValue(value) {
    if (value == null || value === "") return "—";
    var type = typeof value;
    if (type === "string" || type === "number" || type === "boolean") return String(value);
    if (Array.isArray(value)) {
      if (!value.length) return "—";
      var scalars = value.every(function (item) {
        var itemType = typeof item;
        return item == null || itemType === "string" || itemType === "number" || itemType === "boolean";
      });
      if (scalars) {
        return value.map(function (item) { return item == null ? "" : String(item); }).join(", ");
      }
      return truncatedJson(value);
    }
    if (type === "object") return truncatedJson(value);
    return String(value);
  }

  function truncatedJson(value) {
    try {
      var text = JSON.stringify(value);
      if (!text) return "—";
      return text.length > 160 ? text.slice(0, 157) + "…" : text;
    } catch (error) {
      return "[unserializable]";
    }
  }

  function errorInfo(error) {
    var info = { status: null, code: "", rule: "", message: "", retryable: false };
    if (!error) {
      info.message = "unknown error";
      return info;
    }
    if (typeof error.status === "number") info.status = error.status;
    else if (typeof error.httpStatus === "number") info.status = error.httpStatus;
    var body = error.body;
    var detail = body && typeof body === "object" ? body.detail : null;
    if (typeof detail === "string") {
      info.message = detail;
      return info;
    }
    if (detail && typeof detail === "object") {
      if (detail.code) info.code = String(detail.code);
      if (detail.message) info.message = String(detail.message);
      if (detail.retryable === true) info.retryable = true;
      if (detail.params && typeof detail.params === "object" && detail.params.rule) {
        info.rule = String(detail.params.rule);
      }
    }
    if (!info.message && error.message) info.message = String(error.message);
    if (!info.code && typeof error.code === "string") info.code = error.code;
    return info;
  }

  function describeError(error) {
    var info = errorInfo(error);
    var text = info.message || "unknown error";
    if (info.code) text = info.code + ": " + text;
    if (info.rule && info.rule !== info.code) text += "（rule: " + info.rule + "）";
    if (info.retryable) text += "（可重试）";
    return text;
  }

  function interpolate(value, vars) {
    if (!vars) return value;
    Object.keys(vars).forEach(function (name) {
      value = value.split("{" + name + "}").join(String(vars[name]));
    });
    return value;
  }

  /* ── i18n / DOM helpers ────────────────────────────────────────────── */

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    var raw = hasOwn(ZH, key) ? ZH[key] : key;
    return interpolate(raw, vars);
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
      try { shell.applyI18n(root); } catch (error) { /* zh literals stay as the default */ }
    }
  }

  /* ── pure request-body builders (the inspected route bodies) ───────── */

  function problem(code, field, vars) {
    return { code: code, field: field || "", vars: vars || {} };
  }

  function projectOf(binding) {
    if (!isObject(binding)) return "";
    if (trim(binding.projectId)) return trim(binding.projectId);
    if (isObject(binding.taskRef) && trim(binding.taskRef.project_id)) {
      return trim(binding.taskRef.project_id);
    }
    return "";
  }

  function taskRefOf(binding) {
    var value = isObject(binding) && isObject(binding.taskRef) ? binding.taskRef : null;
    if (!value) return null;
    return {
      project_id: trim(value.project_id),
      job_id: trim(value.job_id || value.task_id),
      attempt: intOrNull(value.attempt),
      release_id: trim(value.release_id),
    };
  }

  function taskRefProblem(taskRef) {
    if (!taskRef) return problem("task_ref_required", "taskRef");
    if (!taskRef.project_id || !taskRef.job_id || taskRef.attempt == null ||
        taskRef.attempt < 1 || !taskRef.release_id) {
      return problem("task_ref_required", "taskRef");
    }
    return null;
  }

  function missingScienceFields(science) {
    var missing = [];
    if (!isObject(science)) return SCIENCE_FIELDS.slice();
    SCIENCE_FIELDS.forEach(function (field) {
      if (!hasOwn(science, field)) missing.push(field);
    });
    if (hasOwn(science, "capability_steps") &&
        (!Array.isArray(science.capability_steps) || science.capability_steps.length === 0)) {
      missing.push("capability_steps(minItems 1)");
    }
    if (hasOwn(science, "initial_inputs") &&
        (!Array.isArray(science.initial_inputs) || science.initial_inputs.length === 0)) {
      missing.push("initial_inputs(minItems 1)");
    }
    if (hasOwn(science, "allowed_substitutions") && !Array.isArray(science.allowed_substitutions)) {
      missing.push("allowed_substitutions(array)");
    }
    if (hasOwn(science, "charge") && intOrNull(science.charge) == null) missing.push("charge(integer)");
    if (hasOwn(science, "multiplicity") &&
        (intOrNull(science.multiplicity) == null || intOrNull(science.multiplicity) < 1)) {
      missing.push("multiplicity(integer>=1)");
    }
    if (hasOwn(science, "method") && !trim(science.method)) missing.push("method(text)");
    if (hasOwn(science, "mapping") && !trim(science.mapping)) missing.push("mapping(text)");
    return missing;
  }

  function missingBudgetFields(budget) {
    var missing = [];
    if (!isObject(budget)) return BUDGET_FIELDS.slice();
    BUDGET_FIELDS.forEach(function (field) {
      if (!hasOwn(budget, field) || intOrNull(budget[field]) == null || intOrNull(budget[field]) < 0) {
        missing.push(field);
      }
    });
    return missing;
  }

  function buildProposalBody(binding, form, options) {
    options = options || {};
    var problems = [];
    var question = trim(form && form.question);
    var claim = trim(form && form.claim);
    if (!question) problems.push(problem("question_required", "question"));
    if (!claim) problems.push(problem("claim_required", "claim"));
    if (!isObject(binding)) {
      problems.push(problem("no_binding", "binding"));
      return { ok: false, problems: problems };
    }
    var project = projectOf(binding);
    if (!project) problems.push(problem("project_required", "projectId"));
    var proposalId = trim(binding.proposalId);
    if (!proposalId) problems.push(problem("proposal_id_required", "proposalId"));
    else if (!PROPOSAL_ID_RE.test(proposalId)) {
      problems.push(problem("proposal_id_invalid", "proposalId"));
    }
    var round = intOrNull(binding.round);
    if (round == null || round < 0) problems.push(problem("round_required", "round"));
    if (!isDigest(binding.recipeDigest)) {
      problems.push(problem("recipe_digest_required", "recipeDigest"));
    }
    if (!isObject(binding.plan) || !trim(binding.plan.workflow)) {
      problems.push(problem("plan_required", "plan"));
    }
    var missingScience = missingScienceFields(binding.science);
    if (missingScience.length) {
      problems.push(problem("science_required", "science", { fields: missingScience.join(", ") }));
    }
    var missingBudget = missingBudgetFields(binding.budget);
    if (missingBudget.length) {
      problems.push(problem("budget_required", "budget", { fields: missingBudget.join(", ") }));
    }
    if (problems.length) return { ok: false, problems: problems };

    var proposal = {
      proposal_id: proposalId,
      project_id: project,
      question: question,
      hypothesis: claim,
      round: round,
      recipe_digest: trim(binding.recipeDigest),
      science: binding.science,
      budget: binding.budget,
      decision: "proposed",
    };
    if (trim(binding.questionRef)) proposal.question_ref = trim(binding.questionRef);
    if (intOrNull(binding.questionRevision) != null) {
      proposal.question_revision = intOrNull(binding.questionRevision);
    }
    var body = {
      session_seed: trim(binding.sessionSeed),
      proposal: proposal,
      plan: binding.plan,
    };
    if (project) body.project_id = project;
    if (isObject(binding.requestBudget)) body.budget = binding.requestBudget;
    if (trim(binding.expiresAt)) body.expires_at = trim(binding.expiresAt);
    if (trim(binding.idempotencyKey)) body.idempotency_key = trim(binding.idempotencyKey);
    var cloned = cloneJson(body);
    if (cloned === null) {
      return { ok: false, problems: [problem("binding_not_serializable", "binding")] };
    }
    return { ok: true, body: cloned, problems: problems };
  }

  function buildRecordBody(binding, form, refs, view, options) {
    options = options || {};
    var problems = [];
    var question = trim(form && form.question);
    var claim = trim(form && form.claim);
    var decision = trim(form && form.decision);
    if (!question) problems.push(problem("question_required", "question"));
    if (decision && DECISIONS.indexOf(decision) === -1) {
      problems.push(problem("decision_invalid", "decision", { decision: decision }));
    } else if (!decision) {
      problems.push(problem("decision_required", "decision"));
    }
    if (!isObject(binding)) {
      problems.push(problem("no_binding", "binding"));
      return { ok: false, problems: problems };
    }
    var project = projectOf(binding);
    if (!project) problems.push(problem("project_required", "projectId"));
    var session = trim(binding.sessionSeed);
    if (!session) problems.push(problem("session_required", "sessionSeed"));
    var proposalId = trim(binding.proposalId);
    if (!proposalId) problems.push(problem("proposal_id_required", "proposalId"));
    else if (!PROPOSAL_ID_RE.test(proposalId)) {
      problems.push(problem("proposal_id_invalid", "proposalId"));
    }
    if (!trim(binding.approvalRef)) problems.push(problem("approval_required", "approvalRef"));
    if (!isDigest(binding.planDefinitionDigest)) {
      problems.push(problem("plan_digest_required", "planDefinitionDigest"));
    }
    var round = intOrNull(binding.round);
    if (round == null || round < 0) problems.push(problem("round_required", "round"));
    var taskRef = taskRefOf(binding);
    if (taskRef && !taskRef.project_id) taskRef.project_id = project;
    var taskProblem = taskRefProblem(taskRef);
    if (taskProblem) problems.push(taskProblem);

    var cleanRefs = [];
    (Array.isArray(refs) ? refs : []).forEach(function (row, index) {
      var ref = {
        evidence_id: trim(row && row.evidence_id),
        kind: trim(row && row.kind),
        sha256: trim(row && row.sha256),
        relative_path: trim(row && row.relative_path) || null,
      };
      if (!ref.evidence_id && !ref.kind && !ref.sha256 && !ref.relative_path) return;
      if (!ref.evidence_id) problems.push(problem("evidence_id_required", "evidence_refs[" + index + "]"));
      if (!ref.kind) problems.push(problem("kind_required", "evidence_refs[" + index + "]"));
      if (!isDigest(ref.sha256)) {
        problems.push(problem("digest_invalid", "evidence_refs[" + index + "].sha256",
          { field: "evidence_refs[" + index + "].sha256" }));
      }
      cleanRefs.push(ref);
    });
    if (!cleanRefs.length) {
      problems.push(problem("evidence_required", "evidence_refs"));
    }
    if (problems.length) return { ok: false, problems: problems };

    var body = {
      session_seed: session,
      proposal_ref: proposalId,
      approval_ref: trim(binding.approvalRef),
      plan_definition_digest: trim(binding.planDefinitionDigest),
      round: round,
      decision: decision,
      task_refs: [{
        project_id: taskRef.project_id,
        job_id: taskRef.job_id,
        attempt: taskRef.attempt,
        release_id: taskRef.release_id,
      }],
      evidence_refs: cleanRefs,
    };
    if (project) body.project_id = project;
    if (trim(binding.researchId)) body.research_id = trim(binding.researchId);

    var matchedView = viewMatchesBinding(view, binding, project, round, taskRef);
    if (matchedView) {
      var cited = {};
      cleanRefs.forEach(function (ref) {
        if (ref.evidence_id) cited[ref.evidence_id] = ref.sha256;
      });
      var uncited = (Array.isArray(matchedView.evidence_refs) ? matchedView.evidence_refs : [])
        .filter(function (ref) {
          return cited[String(ref.evidence_id || "")] !== String(ref.sha256 || "");
        });
      if (uncited.length) {
        problems.push(problem("evidence_view_refs_uncited", "evidence_refs",
          { count: uncited.length }));
        return { ok: false, problems: problems };
      }
      body.evidence_view = matchedView.raw || matchedView;
    }

    var editorRef = trim(options.editorRef) ||
      (matchedView && matchedView.research ? trim(matchedView.research.principal_id) : "");
    if (editorRef) {
      var editedAt = trim(options.nowIso) || new Date().toISOString();
      var edits = [];
      if (question) {
        edits.push({
          edited_at: editedAt,
          field_path: "research.question",
          editor_ref: editorRef,
          note: question,
        });
      }
      if (claim) {
        edits.push({
          edited_at: editedAt,
          field_path: "research.claim",
          editor_ref: editorRef,
          note: claim,
        });
      }
      if (edits.length) body.human_edits = edits;
    }
    var cloned = cloneJson(body);
    if (cloned === null) {
      return { ok: false, problems: [problem("binding_not_serializable", "binding")] };
    }
    return { ok: true, body: cloned, problems: problems, viewMatched: !!matchedView };
  }

  function viewPotentialMismatches(view, binding, project, round, taskRef) {
    var fields = [];
    if (!isObject(view)) return fields;
    var research = isObject(view.research) ? view.research : {};
    var task = isObject(view.task_ref) ? view.task_ref : {};
    var release = isObject(task.release) ? task.release : {};
    if (trim(research.project_id) !== project) fields.push("research.project_id");
    if (trim(binding.researchId) && trim(research.research_id) !== trim(binding.researchId)) {
      fields.push("research.research_id");
    }
    if (intOrNull(research.round) !== round) fields.push("research.round");
    if (trim(task.project_id) !== project) fields.push("task_ref.project_id");
    if (trim(task.job_id || task.task_id) !== taskRef.job_id) fields.push("task_ref.job_id");
    if (intOrNull(task.attempt) !== taskRef.attempt) fields.push("task_ref.attempt");
    if (trim(release.release_id) !== taskRef.release_id) fields.push("task_ref.release.release_id");
    return fields;
  }

  function viewMatchesBinding(view, binding, project, round, taskRef) {
    if (!isObject(view) || !taskRef) return null;
    return viewPotentialMismatches(view, binding, project, round, taskRef).length ? null : view;
  }

  /* ── evidence projection normalization (display only) ──────────────── */

  function claimRows(payload) {
    var rows = [];
    if (Array.isArray(payload.claims) && payload.claims.length) {
      payload.claims.forEach(function (item) {
        if (!isObject(item)) return;
        rows.push({
          claim_id: str(item.claim_id),
          status: str(item.status),
          statement: str(item.statement),
          validator_id: str(item.validator_id),
          validator_version: str(item.validator_version),
          source_ref_count: intOrNull(item.source_ref_count),
          blocking_missing_checks: stringArray(item.blocking_missing_checks),
        });
      });
      return rows;
    }
    if (isObject(payload.claim_statuses)) {
      Object.keys(payload.claim_statuses).forEach(function (claimId) {
        rows.push({
          claim_id: String(claimId),
          status: str(payload.claim_statuses[claimId]),
          statement: "",
          validator_id: "",
          validator_version: "",
          source_ref_count: null,
          blocking_missing_checks: [],
        });
      });
    }
    return rows;
  }

  function normalizeEvidenceRefs(value) {
    if (!Array.isArray(value)) return [];
    var refs = [];
    value.forEach(function (item) {
      if (!isObject(item)) return;
      refs.push({
        evidence_id: str(item.evidence_id),
        kind: str(item.kind),
        sha256: str(item.sha256),
        relative_path: item.relative_path == null ? null : str(item.relative_path),
      });
    });
    return refs;
  }

  function normalizeEvidence(payload) {
    if (!isObject(payload)) {
      return { ok: false, problems: ["view_not_object"] };
    }
    var problems = [];
    if (str(payload.schema) && str(payload.schema) !== "acp2.runtime.evidence-projection.v1") {
      problems.push("schema");
    }
    if (!isObject(payload.research)) problems.push("research");
    if (!isObject(payload.task_ref)) problems.push("task_ref");
    if (!isObject(payload.report)) problems.push("report");
    if (!Array.isArray(payload.claims) && !isObject(payload.claim_statuses)) {
      problems.push("claims|claim_statuses");
    }
    if (problems.length) return { ok: false, problems: problems };

    var research = payload.research;
    var task = payload.task_ref;
    var report = payload.report;
    var release = isObject(task.release) ? task.release : {};
    var rows = claimRows(payload);
    var warnings = [];
    rows.forEach(function (row) {
      if (row.status && CLAIM_STATUSES.indexOf(row.status) === -1) {
        warnings.push({
          code: "claim_status_unknown",
          vars: { claim_id: row.claim_id, status: row.status },
        });
      }
    });
    if (!str(payload.projection_digest)) {
      warnings.push({ code: "projection_digest_missing", vars: {} });
    }
    var refs = normalizeEvidenceRefs(payload.evidence_refs);
    if (!refs.length) warnings.push({ code: "evidence_refs_empty", vars: {} });
    return {
      ok: true,
      warnings: warnings,
      view: {
        schema: str(payload.schema),
        contract_version: str(payload.contract_version),
        research: {
          principal_id: str(research.principal_id),
          project_id: str(research.project_id),
          research_id: str(research.research_id),
          session_id: str(research.session_id),
          round: intOrNull(research.round),
        },
        task_ref: {
          project_id: str(task.project_id),
          task_id: str(task.task_id),
          job_id: str(task.job_id || task.task_id),
          attempt: intOrNull(task.attempt),
          release: {
            release_id: str(release.release_id),
            release_digest: str(release.release_digest),
          },
        },
        report: {
          report_id: str(report.report_id),
          digest: str(report.digest),
          schema_version: intOrNull(report.schema_version),
          subject_id: str(report.subject_id),
        },
        execution_terminal_state: str(payload.execution_terminal_state),
        qualification: str(payload.qualification),
        evidence_status: str(payload.evidence_status),
        claims: rows,
        overall_claim_status: str(payload.overall_claim_status),
        blocking_missing_checks: stringArray(payload.blocking_missing_checks),
        evidence_refs: refs,
        limitations: stringArray(payload.limitations),
        provenance_digest: str(payload.provenance_digest),
        normal_reuse: payload.normal_reuse === true,
        projection_digest: str(payload.projection_digest),
        claim_statuses: isObject(payload.claim_statuses) ? cloneJson(payload.claim_statuses) : null,
        raw: payload,
      },
    };
  }

  function evidencePath(researchId, projectId, sessionSeed, round) {
    var query = [];
    if (trim(projectId)) query.push("project_id=" + encodeURIComponent(trim(projectId)));
    if (trim(sessionSeed)) query.push("session_seed=" + encodeURIComponent(trim(sessionSeed)));
    if (intOrNull(round) != null) query.push("round=" + encodeURIComponent(String(intOrNull(round))));
    return RECORDS_PATH + "/" + encodeURIComponent(trim(researchId)) + "/evidence" +
      (query.length ? "?" + query.join("&") : "");
  }

  /* ── module instance ───────────────────────────────────────────────── */

  function createResearchRecord(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);

    var state = {
      generation: 0,
      readToken: 0,
      submitToken: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      context: null,
      snapshot: null,
      binding: null,
      bindingOverride: null,
      key: "",
      projectId: "",
      researchId: "",
      sessionSeed: "",
      evidenceRound: "",
      targetTouched: false,
      evidence: {
        status: "empty",
        view: null,
        warnings: [],
        error: "",
        errorInfo: null,
      },
      form: { question: "", claim: "", target: "proposal", decision: "" },
      refs: [],
      submitBusy: false,
      submitBusyToken: 0,
      errors: [],
      notice: null,
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

    function transport() {
      if (typeof deps.request === "function") {
        return function (path, options) { return deps.request(path, options); };
      }
      var client = deps.client || (typeof window !== "undefined" ? window.ACPClient : null);
      if (client && client.transport && typeof client.transport.request === "function") {
        var transportImpl = client.transport;
        return function (path, options) { return transportImpl.request(path, options); };
      }
      return null;
    }

    function nowIso() {
      var millis = typeof deps.now === "function" ? deps.now() : Date.now();
      var date = new Date(millis);
      return isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
    }

    function current(gen, token) {
      return !state.disposed && state.mounted && state.generation === gen && state.readToken === token;
    }

    function submitCurrent(gen, token) {
      return !state.disposed && state.mounted && state.generation === gen &&
        state.submitToken === token;
    }

    function setState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function problemText(item) {
      return t("next.record.entry." + item.code, item.vars || {});
    }

    function warningText(item) {
      return t("next.record.warning." + item.code, item.vars || {});
    }

    /* ── DOM ─────────────────────────────────────────────────────────── */

    function buildDom() {
      var root = el(doc, "div", "acp-rr");
      root.setAttribute("data-module", MODULE_ID);
      root.setAttribute("data-acp-module-state", "empty");

      var head = el(doc, "header", "acp-rr-head");
      var headTop = el(doc, "div", "acp-rr-head-top");
      var title = i18nEl(doc, "h3", "acp-rr-title", "next.record.title");
      var readonlyBadge = i18nEl(doc, "span", "acp-rr-badge", "next.record.badge.readonly");
      readonlyBadge.setAttribute("data-kind", "readonly");
      var targetBadge = el(doc, "span", "acp-rr-badge", "");
      targetBadge.setAttribute("data-kind", "target");
      headTop.appendChild(title);
      headTop.appendChild(readonlyBadge);
      headTop.appendChild(targetBadge);
      var sub = i18nEl(doc, "p", "acp-rr-sub", "next.record.subtitle");
      head.appendChild(headTop);
      head.appendChild(sub);
      root.appendChild(head);

      var body = el(doc, "div", "acp-rr-body");
      root.appendChild(body);

      var stateNode = el(doc, "p", "acp-rr-state", "");
      stateNode.hidden = true;
      stateNode.setAttribute("data-role", "state");
      stateNode.setAttribute("aria-live", "polite");
      body.appendChild(stateNode);

      var emptyHint = el(doc, "p", "acp-rr-empty-hint", "");
      emptyHint.hidden = true;
      body.appendChild(emptyHint);

      var retry = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-rr-retry", "next.record.retry");
      retry.type = "button";
      retry.setAttribute("data-action", "retry");
      retry.setAttribute("data-role", "retry");
      retry.hidden = true;
      body.appendChild(retry);
      on(retry, "click", function () { loadEvidence(); });

      /* ---- A-56 evidence projection --------------------------------- */
      var evidenceSection = el(doc, "section", "acp-rr-section acp-rr-evidence");
      evidenceSection.setAttribute("data-acp-section", "evidence");
      evidenceSection.appendChild(i18nEl(doc, "h4", "acp-rr-heading", "next.record.section.evidence"));

      var source = el(doc, "div", "acp-rr-source");
      var sourceHint = el(doc, "p", "acp-rr-note acp-rr-source-hint", t("next.record.source.hint"));
      sourceHint.setAttribute("data-role", "source-hint");
      source.appendChild(sourceHint);
      var sourceGrid = el(doc, "div", "acp-rr-source-grid");
      var researchField = field(doc, "researchId", "next.record.source.research_id", "text");
      var seedField = field(doc, "sessionSeed", "next.record.source.session_seed", "text");
      var roundField = field(doc, "evidenceRound", "next.record.source.round", "text");
      sourceGrid.appendChild(researchField.wrap);
      sourceGrid.appendChild(seedField.wrap);
      sourceGrid.appendChild(roundField.wrap);
      source.appendChild(sourceGrid);
      var sourceActions = el(doc, "div", "acp-rr-actions");
      var loadButton = i18nEl(doc, "button", "acp-btn acp-btn--primary acp-rr-load", "next.record.load_evidence");
      loadButton.type = "button";
      loadButton.setAttribute("data-action", "load-evidence");
      loadButton.setAttribute("data-role", "load-evidence");
      sourceActions.appendChild(loadButton);
      var refreshButton = i18nEl(doc, "button", "acp-btn acp-btn--ghost", "next.record.refresh");
      refreshButton.type = "button";
      refreshButton.setAttribute("data-action", "refresh-evidence");
      refreshButton.setAttribute("data-role", "refresh-evidence");
      sourceActions.appendChild(refreshButton);
      source.appendChild(sourceActions);
      on(loadButton, "click", function () { loadEvidence(); });
      on(refreshButton, "click", function () { loadEvidence(); });
      evidenceSection.appendChild(source);

      var evidenceBody = el(doc, "div", "acp-rr-evidence-body");
      evidenceBody.setAttribute("data-role", "evidence-body");
      evidenceBody.hidden = true;
      evidenceSection.appendChild(evidenceBody);

      var warningList = el(doc, "ul", "acp-rr-warnings");
      warningList.setAttribute("data-role", "evidence-warnings");
      warningList.hidden = true;
      evidenceBody.appendChild(warningList);

      var verification = el(doc, "div", "acp-rr-verification");
      verification.setAttribute("data-acp-section", "verification");
      verification.appendChild(i18nEl(doc, "h5", "acp-rr-subheading", "next.record.section.verification"));
      var metaGrid = el(doc, "dl", "acp-rr-grid");
      metaGrid.setAttribute("data-role", "evidence-meta");
      verification.appendChild(metaGrid);

      var bindSection = el(doc, "div", "acp-rr-bind");
      bindSection.appendChild(i18nEl(doc, "h6", "acp-rr-minor-heading", "next.record.section.binding"));
      var evidenceBindGrid = el(doc, "dl", "acp-rr-grid");
      evidenceBindGrid.setAttribute("data-role", "evidence-binding");
      bindSection.appendChild(evidenceBindGrid);
      verification.appendChild(bindSection);

      var claimsSection = el(doc, "div", "acp-rr-claims");
      claimsSection.setAttribute("data-acp-claims-source", "server");
      claimsSection.appendChild(i18nEl(doc, "h6", "acp-rr-minor-heading", "next.record.section.claims"));
      var claimsNote = i18nEl(doc, "p", "acp-rr-note", "next.record.claims.note");
      claimsSection.appendChild(claimsNote);
      var claimList = el(doc, "ul", "acp-rr-claim-list");
      claimList.setAttribute("data-role", "claims");
      claimList.setAttribute("data-acp-adjudication", "server-only");
      claimList.setAttribute("data-acp-claims-source", "server");
      claimsSection.appendChild(claimList);
      verification.appendChild(claimsSection);
      evidenceBody.appendChild(verification);

      var explanation = el(doc, "div", "acp-rr-explanation");
      explanation.setAttribute("data-acp-section", "explanation");
      explanation.appendChild(i18nEl(doc, "h5", "acp-rr-subheading", "next.record.section.explanation"));
      explanation.appendChild(i18nEl(doc, "p", "acp-rr-note acp-rr-model-note", "next.record.explanation.model_note"));
      explanation.appendChild(i18nEl(doc, "h6", "acp-rr-minor-heading", "next.record.label.limitations"));
      var limitations = el(doc, "ul", "acp-rr-list");
      limitations.setAttribute("data-role", "limitations");
      explanation.appendChild(limitations);
      explanation.appendChild(i18nEl(doc, "h6", "acp-rr-minor-heading", "next.record.label.blocking"));
      var blocking = el(doc, "ul", "acp-rr-list");
      blocking.setAttribute("data-role", "blocking");
      explanation.appendChild(blocking);
      evidenceBody.appendChild(explanation);

      var refsSection = el(doc, "div", "acp-rr-refs");
      refsSection.setAttribute("data-acp-section", "refs");
      refsSection.appendChild(i18nEl(doc, "h5", "acp-rr-subheading", "next.record.section.refs"));
      var refsList = el(doc, "ul", "acp-rr-ref-list");
      refsList.setAttribute("data-role", "evidence-refs");
      refsSection.appendChild(refsList);
      evidenceBody.appendChild(refsSection);
      body.appendChild(evidenceSection);

      /* ---- A-54 manual entry ---------------------------------------- */
      var entrySection = el(doc, "section", "acp-rr-section acp-rr-entry");
      entrySection.setAttribute("data-acp-section", "entry");
      entrySection.appendChild(i18nEl(doc, "h4", "acp-rr-heading", "next.record.section.entry"));
      entrySection.appendChild(i18nEl(doc, "p", "acp-rr-note", "next.record.entry.note"));

      var questionLabel = i18nEl(doc, "label", "acp-rr-label", "next.record.entry.question");
      var question = el(doc, "textarea", "acp-rr-textarea");
      question.setAttribute("data-role", "question");
      question.setAttribute("rows", "3");
      question.setAttribute("data-i18n-ph", "next.record.entry.question_placeholder");
      question.setAttribute("placeholder", t("next.record.entry.question_placeholder"));
      questionLabel.setAttribute("for", "acp-rr-question");
      question.setAttribute("id", "acp-rr-question");
      entrySection.appendChild(questionLabel);
      entrySection.appendChild(question);

      var claimLabel = i18nEl(doc, "label", "acp-rr-label", "next.record.entry.claim");
      var claim = el(doc, "textarea", "acp-rr-textarea");
      claim.setAttribute("data-role", "claim");
      claim.setAttribute("rows", "2");
      claim.setAttribute("data-i18n-ph", "next.record.entry.claim_placeholder");
      claim.setAttribute("placeholder", t("next.record.entry.claim_placeholder"));
      claimLabel.setAttribute("for", "acp-rr-claim");
      claim.setAttribute("id", "acp-rr-claim");
      entrySection.appendChild(claimLabel);
      entrySection.appendChild(claim);

      var selectRow = el(doc, "div", "acp-rr-select-row");
      var targetWrap = el(doc, "div", "acp-rr-field");
      var targetLabel = i18nEl(doc, "label", "acp-rr-label", "next.record.entry.target");
      var target = el(doc, "select", "acp-rr-select");
      target.setAttribute("data-role", "target");
      [
        { value: "proposal", key: "next.record.entry.target_proposal" },
        { value: "record", key: "next.record.entry.target_record" },
      ].forEach(function (option) {
        var node = el(doc, "option", "", t(option.key));
        node.setAttribute("value", option.value);
        node.setAttribute("data-i18n", option.key);
        target.appendChild(node);
      });
      target.value = "proposal";
      targetWrap.appendChild(targetLabel);
      targetWrap.appendChild(target);
      selectRow.appendChild(targetWrap);

      var decisionWrap = el(doc, "div", "acp-rr-field");
      var decisionLabel = i18nEl(doc, "label", "acp-rr-label", "next.record.entry.decision");
      var decision = el(doc, "select", "acp-rr-select");
      decision.setAttribute("data-role", "decision");
      var placeholder = el(doc, "option", "", t("next.record.entry.decision_placeholder"));
      placeholder.setAttribute("value", "");
      decision.appendChild(placeholder);
      DECISIONS.forEach(function (value) {
        var node = el(doc, "option", "", t("next.record.decision." + value));
        node.setAttribute("value", value);
        node.setAttribute("data-i18n", "next.record.decision." + value);
        decision.appendChild(node);
      });
      decision.value = "";
      decisionWrap.appendChild(decisionLabel);
      decisionWrap.appendChild(decision);
      selectRow.appendChild(decisionWrap);
      entrySection.appendChild(selectRow);
      on(target, "change", function () {
        state.targetTouched = true;
        state.form.target = target.value;
        renderEntry();
        renderNotice();
      });
      on(decision, "change", function () { state.form.decision = decision.value; });

      var refsFieldset = el(doc, "fieldset", "acp-rr-refs-editor");
      refsFieldset.appendChild(i18nEl(doc, "legend", "acp-rr-legend", "next.record.entry.evidence"));
      refsFieldset.appendChild(i18nEl(doc, "p", "acp-rr-note", "next.record.entry.evidence_hint"));
      var refsRows = el(doc, "div", "acp-rr-ref-rows");
      refsRows.setAttribute("data-role", "evidence-ref-rows");
      refsFieldset.appendChild(refsRows);
      var refActions = el(doc, "div", "acp-rr-actions");
      var addRef = i18nEl(doc, "button", "acp-btn acp-btn--ghost", "next.record.entry.add_evidence");
      addRef.type = "button";
      addRef.setAttribute("data-action", "add-evidence");
      addRef.setAttribute("data-role", "add-evidence");
      var useProjection = i18nEl(doc, "button", "acp-btn acp-btn--ghost", "next.record.entry.use_projection");
      useProjection.type = "button";
      useProjection.setAttribute("data-action", "use-projection-refs");
      useProjection.setAttribute("data-role", "use-projection-refs");
      refActions.appendChild(addRef);
      refActions.appendChild(useProjection);
      refsFieldset.appendChild(refActions);
      var refsNote = el(doc, "p", "acp-rr-note", "");
      refsNote.hidden = true;
      refsNote.setAttribute("data-role", "refs-note");
      refsFieldset.appendChild(refsNote);
      entrySection.appendChild(refsFieldset);
      on(addRef, "click", function () { addEvidenceRow({}); renderRefs(); });
      on(useProjection, "click", function () {
        var view = state.evidence.status === "ready" ? state.evidence.view : null;
        var refs = view && view.evidence_refs ? view.evidence_refs : [];
        if (!refs.length) {
          refsNote.hidden = false;
          refsNote.textContent = t("next.record.entry.use_projection_empty");
          return;
        }
        refsNote.hidden = true;
        state.refs = refs.map(function (item) {
          return {
            evidence_id: str(item.evidence_id),
            kind: str(item.kind),
            sha256: str(item.sha256),
            relative_path: item.relative_path == null ? "" : str(item.relative_path),
          };
        });
        renderRefs();
      });

      var targetNote = el(doc, "p", "acp-rr-note acp-rr-target-note", "");
      targetNote.setAttribute("data-role", "target-note");
      entrySection.appendChild(targetNote);

      var bindWrap = el(doc, "div", "acp-rr-binding");
      bindWrap.appendChild(i18nEl(doc, "h5", "acp-rr-subheading", "next.record.entry.binding_title"));
      bindWrap.appendChild(i18nEl(doc, "p", "acp-rr-note", "next.record.entry.binding_hint"));
      var entryBindGrid = el(doc, "dl", "acp-rr-grid");
      entryBindGrid.setAttribute("data-role", "binding-grid");
      bindWrap.appendChild(entryBindGrid);
      var bindNote = el(doc, "p", "acp-rr-note", "");
      bindNote.setAttribute("data-role", "binding-note");
      bindNote.hidden = true;
      bindWrap.appendChild(bindNote);

      var bindJsonLabel = i18nEl(doc, "label", "acp-rr-label", "next.record.entry.binding_json");
      var bindJson = el(doc, "textarea", "acp-rr-textarea acp-rr-json");
      bindJson.setAttribute("data-role", "binding-json");
      bindJson.setAttribute("rows", "6");
      bindJson.setAttribute("spellcheck", "false");
      bindJsonLabel.setAttribute("for", "acp-rr-binding-json");
      bindJson.setAttribute("id", "acp-rr-binding-json");
      bindWrap.appendChild(bindJsonLabel);
      bindWrap.appendChild(bindJson);
      var applyBind = i18nEl(doc, "button", "acp-btn acp-btn--ghost", "next.record.entry.apply_binding");
      applyBind.type = "button";
      applyBind.setAttribute("data-action", "apply-binding");
      applyBind.setAttribute("data-role", "apply-binding");
      bindWrap.appendChild(applyBind);
      on(applyBind, "click", function () {
        var text = trim(bindJson.value);
        if (!text) {
          state.bindingOverride = null;
          state.binding = initialBinding();
          render();
          return;
        }
        var parsed;
        try {
          parsed = JSON.parse(text);
        } catch (error) {
          state.notice = {
            kind: "error",
            text: t("next.record.entry.binding_invalid", { message: String(error && error.message || error) }),
          };
          renderNotice();
          return;
        }
        if (!isObject(parsed)) {
          state.notice = { kind: "error", text: t("next.record.entry.binding_not_object") };
          renderNotice();
          return;
        }
        var cloned = cloneJson(parsed) || parsed;
        state.bindingOverride = cloned;
        state.binding = cloned;
        if (!trim(state.projectId)) state.projectId = projectOf(cloned);
        if (state.ui.researchId && trim(cloned.researchId)) {
          state.ui.researchId.value = trim(cloned.researchId);
        }
        if (state.ui.sessionSeed && trim(cloned.sessionSeed)) {
          state.ui.sessionSeed.value = trim(cloned.sessionSeed);
        }
        if (state.ui.evidenceRound && intOrNull(cloned.round) != null) {
          state.ui.evidenceRound.value = String(intOrNull(cloned.round));
        }
        autoTarget();
        prefillFromBinding();
        state.notice = { kind: "ok", text: t("next.record.entry.binding_applied") };
        state.evidence = { status: "empty", view: null, warnings: [], error: "", errorInfo: null };
        state.readToken += 1;
        state.submitToken += 1;
        render();
        if (trim(state.ui.researchId.value)) loadEvidence();
      });
      entrySection.appendChild(bindWrap);

      var errors = el(doc, "ul", "acp-rr-errors");
      errors.setAttribute("data-role", "form-errors");
      errors.hidden = true;
      entrySection.appendChild(errors);

      var actions = el(doc, "div", "acp-rr-actions acp-rr-submit-actions");
      var submit = i18nEl(doc, "button", "acp-btn acp-btn--primary acp-rr-submit", "next.record.entry.submit_proposal");
      submit.type = "button";
      submit.setAttribute("data-action", "submit");
      submit.setAttribute("data-role", "submit");
      actions.appendChild(submit);
      entrySection.appendChild(actions);
      on(submit, "click", function () { submitRecord(); });

      var notice = el(doc, "p", "acp-rr-notice", "");
      notice.hidden = true;
      notice.setAttribute("role", "alert");
      notice.setAttribute("data-role", "notice");
      entrySection.appendChild(notice);
      body.appendChild(entrySection);

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        title: title,
        readonlyBadge: readonlyBadge,
        targetBadge: targetBadge,
        sub: sub,
        stateNode: stateNode,
        emptyHint: emptyHint,
        retry: retry,
        evidenceSection: evidenceSection,
        sourceHint: sourceHint,
        researchId: researchField.input,
        sessionSeed: seedField.input,
        evidenceRound: roundField.input,
        loadButton: loadButton,
        refreshButton: refreshButton,
        evidenceBody: evidenceBody,
        warnings: warningList,
        metaGrid: metaGrid,
        evidenceBindGrid: evidenceBindGrid,
        claimList: claimList,
        limitations: limitations,
        blocking: blocking,
        refsList: refsList,
        question: question,
        claim: claim,
        target: target,
        decision: decision,
        decisionWrap: decisionWrap,
        refsRows: refsRows,
        refsNote: refsNote,
        targetNote: targetNote,
        bindGrid: entryBindGrid,
        bindNote: bindNote,
        bindJson: bindJson,
        errors: errors,
        submit: submit,
        notice: notice,
      };
      addEvidenceRow({});
      var initial = initialBinding();
      if (initial) {
        state.binding = initial;
        prefillFromBinding();
      }
      render();
    }

    function field(docRef, role, labelKey, type) {
      var wrap = el(docRef, "div", "acp-rr-field");
      var label = i18nEl(docRef, "label", "acp-rr-label", labelKey);
      var input = el(docRef, "input", "acp-rr-input");
      input.type = type || "text";
      input.setAttribute("data-role", role);
      label.setAttribute("for", "acp-rr-" + role);
      input.setAttribute("id", "acp-rr-" + role);
      wrap.appendChild(label);
      wrap.appendChild(input);
      return { wrap: wrap, input: input };
    }

    /* ── binding ────────────────────────────────────────────────────── */

    function initialBinding() {
      var candidates = [deps.binding];
      var snapshot = state.snapshot || null;
      if (snapshot) {
        candidates.push(snapshot.researchBinding);
        candidates.push(snapshot.binding);
      }
      if (state.context) candidates.push(state.context.binding);
      for (var i = 0; i < candidates.length; i += 1) {
        if (isObject(candidates[i])) return cloneJson(candidates[i]) || candidates[i];
      }
      return null;
    }

    function currentBinding() {
      return state.bindingOverride || state.binding || null;
    }

    function prefillFromBinding() {
      var binding = currentBinding();
      if (!binding || !state.ui.researchId) return;
      if (!trim(state.ui.researchId.value) && trim(binding.researchId)) {
        state.ui.researchId.value = trim(binding.researchId);
      }
      if (!trim(state.ui.sessionSeed.value) && trim(binding.sessionSeed)) {
        state.ui.sessionSeed.value = trim(binding.sessionSeed);
      }
      if (!trim(state.ui.evidenceRound.value) && intOrNull(binding.round) != null) {
        state.ui.evidenceRound.value = String(intOrNull(binding.round));
      }
      if (!trim(state.ui.bindJson.value)) {
        var cloned = cloneJson(binding);
        if (cloned !== null) state.ui.bindJson.value = JSON.stringify(cloned, null, 2);
      }
    }

    function autoTarget() {
      if (state.targetTouched) return;
      var binding = currentBinding();
      var recordReady = isObject(binding) && trim(binding.approvalRef) &&
        isDigest(binding.planDefinitionDigest) && isObject(binding.taskRef);
      state.form.target = recordReady ? "record" : "proposal";
      if (state.ui.target) state.ui.target.value = state.form.target;
    }

    function bindingRows(binding, target) {
      if (!isObject(binding)) {
        return [{ field: "binding", labelKey: "next.record.entry.binding_json", value: "—", ok: false }];
      }
      var project = projectOf(binding);
      var session = trim(binding.sessionSeed);
      var proposalId = trim(binding.proposalId);
      if (target === "proposal") {
        var scienceMissing = missingScienceFields(binding.science);
        var budgetMissing = missingBudgetFields(binding.budget);
        return [
          row("projectId", "next.record.label.project_id", project, !!project),
          row("sessionSeed", "next.record.source.session_seed", session, !!session),
          row("proposalId", "next.record.label.proposal_id", proposalId, PROPOSAL_ID_RE.test(proposalId)),
          row("round", "next.record.label.round", intOrNull(binding.round), intOrNull(binding.round) != null && intOrNull(binding.round) >= 0),
          row("recipeDigest", "next.record.entry.sha256", trim(binding.recipeDigest), isDigest(binding.recipeDigest)),
          row("plan.workflow", "next.record.label.plan_workflow", isObject(binding.plan) ? trim(binding.plan.workflow) : "", isObject(binding.plan) && !!trim(binding.plan.workflow)),
          row("science", "next.record.label.science", scienceMissing.length ? scienceMissing.join(", ") : "complete", scienceMissing.length === 0),
          row("budget", "next.record.label.budget", budgetMissing.length ? budgetMissing.join(", ") : "complete", budgetMissing.length === 0),
        ];
      }
      var task = taskRefOf(binding);
      return [
        row("projectId", "next.record.label.project_id", project, !!project),
        row("sessionSeed", "next.record.source.session_seed", session, !!session),
        row("researchId", "next.record.label.research_id", trim(binding.researchId), !!trim(binding.researchId)),
        row("proposalId", "next.record.label.proposal_id", proposalId, PROPOSAL_ID_RE.test(proposalId)),
        row("approvalRef", "next.record.label.binding", trim(binding.approvalRef), !!trim(binding.approvalRef)),
        row("planDefinitionDigest", "next.record.label.plan_definition_digest", trim(binding.planDefinitionDigest), isDigest(binding.planDefinitionDigest)),
        row("round", "next.record.label.round", intOrNull(binding.round), intOrNull(binding.round) != null && intOrNull(binding.round) >= 0),
        row("taskRef", "next.record.label.task_id", task ? task.job_id + "#" + task.attempt : "", !!task && !taskRefProblem(task)),
      ];
    }

    function row(fieldName, labelKey, value, ok) {
      return { field: fieldName, labelKey: labelKey, value: value, ok: !!ok };
    }

    /* ── rendering ──────────────────────────────────────────────────── */

    function render() {
      if (state.disposed || !state.mounted) return;
      renderState();
      renderEvidence();
      renderEntry();
      renderRefs();
      renderErrors();
      renderNotice();
      applyI18n(state.ui.root);
    }

    function renderState() {
      var status = state.evidence.status;
      setState(status === "ready" ? "ready" : status);
      var ui = state.ui;
      if (!ui.root) return;
      var showState = status === "loading" || status === "error" || status === "empty";
      ui.stateNode.hidden = !showState;
      ui.emptyHint.hidden = !(status === "empty");
      ui.retry.hidden = status !== "error";
      if (status === "loading") {
        ui.stateNode.textContent = t("next.record.loading");
      } else if (status === "error") {
        ui.stateNode.textContent = state.evidence.error || t("next.record.error", { message: "unknown" });
      } else if (status === "empty") {
        ui.stateNode.textContent = t("next.record.empty");
        ui.emptyHint.textContent = trim(state.evidence.error) || t("next.record.empty_hint");
      } else {
        ui.stateNode.textContent = "";
        ui.emptyHint.textContent = "";
      }
    }

    function clearList(node) {
      if (!node) return;
      if (typeof node.replaceChildren === "function") node.replaceChildren();
    }

    function metaRow(grid, fieldName, labelKey, value, options) {
      options = options || {};
      var dt = i18nEl(doc, "dt", "", labelKey);
      var text = options.text != null ? options.text : displayValue(value);
      var dd = el(doc, "dd", options.mono ? "acp-rr-mono" : "", text);
      dd.setAttribute("data-field", fieldName);
      if (value != null && (typeof value === "string" || typeof value === "number" || typeof value === "boolean")) {
        dd.setAttribute("data-value", String(value));
      }
      if (options.title && value != null) dd.setAttribute("title", String(value));
      grid.appendChild(dt);
      grid.appendChild(dd);
      return dd;
    }

    function renderWarnings() {
      var ui = state.ui;
      if (!ui.warnings) return;
      clearList(ui.warnings);
      var warnings = state.evidence.warnings || [];
      ui.warnings.hidden = warnings.length === 0;
      if (!warnings.length) return;
      warnings.forEach(function (item) {
        var li = el(doc, "li", "acp-rr-warning", warningText(item));
        li.setAttribute("data-warning", item.code);
        ui.warnings.appendChild(li);
      });
    }

    function renderEvidence() {
      var ui = state.ui;
      if (!ui.evidenceBody) return;
      var ready = state.evidence.status === "ready" && isObject(state.evidence.view);
      ui.evidenceBody.hidden = !ready;
      renderWarnings();
      if (!ready) return;
      var view = state.evidence.view;

      clearList(ui.metaGrid);
      metaRow(ui.metaGrid, "research.research_id", "next.record.label.research_id", view.research.research_id, { mono: true });
      metaRow(ui.metaGrid, "research.project_id", "next.record.label.project_id", view.research.project_id, { mono: true });
      metaRow(ui.metaGrid, "research.round", "next.record.label.round", view.research.round);
      metaRow(ui.metaGrid, "research.principal_id", "next.record.label.principal", view.research.principal_id, { mono: true });
      metaRow(ui.metaGrid, "research.session_id", "next.record.label.session_id", view.research.session_id, { mono: true });
      metaRow(ui.metaGrid, "execution_terminal_state", "next.record.label.execution", view.execution_terminal_state);
      metaRow(ui.metaGrid, "evidence_status", "next.record.label.evidence_status", view.evidence_status);
      metaRow(ui.metaGrid, "qualification", "next.record.label.qualification", view.qualification);
      metaRow(ui.metaGrid, "overall_claim_status", "next.record.label.overall_claim_status", view.overall_claim_status);
      metaRow(ui.metaGrid, "normal_reuse", "next.record.label.normal_reuse",
        view.normal_reuse ? t("next.record.value.yes") : t("next.record.value.no"));
      metaRow(ui.metaGrid, "report.report_id", "next.record.label.report_id", view.report.report_id, { mono: true });
      metaRow(ui.metaGrid, "report.digest", "next.record.label.report_digest", view.report.digest, { mono: true, title: true });
      metaRow(ui.metaGrid, "report.schema_version", "next.record.label.report_schema", view.report.schema_version);
      metaRow(ui.metaGrid, "report.subject_id", "next.record.label.subject_id", view.report.subject_id, { mono: true });
      metaRow(ui.metaGrid, "projection_digest", "next.record.label.projection_digest", view.projection_digest, { mono: true, title: true });
      metaRow(ui.metaGrid, "provenance_digest", "next.record.label.provenance_digest", view.provenance_digest, { mono: true, title: true });

      clearList(ui.evidenceBindGrid);
      metaRow(ui.evidenceBindGrid, "task_ref.task_id", "next.record.label.task_id",
        view.task_ref.task_id || view.task_ref.job_id, { mono: true });
      metaRow(ui.evidenceBindGrid, "task_ref.attempt", "next.record.label.attempt", view.task_ref.attempt);
      metaRow(ui.evidenceBindGrid, "task_ref.release.release_id", "next.record.label.release_id",
        view.task_ref.release.release_id, { mono: true });
      metaRow(ui.evidenceBindGrid, "task_ref.release.release_digest", "next.record.label.release_digest",
        view.task_ref.release.release_digest, { mono: true, title: true });

      clearList(ui.claimList);
      if (!view.claims.length) {
        var emptyClaims = el(doc, "li", "acp-rr-empty-item", t("next.record.no_claims"));
        emptyClaims.setAttribute("data-role", "claims-empty");
        ui.claimList.appendChild(emptyClaims);
      } else {
        view.claims.forEach(function (claimRow) {
          ui.claimList.appendChild(claimNode(claimRow));
        });
      }

      clearList(ui.limitations);
      if (!view.limitations.length) {
        var noLimits = el(doc, "li", "acp-rr-empty-item", t("next.record.limitations.empty"));
        noLimits.setAttribute("data-role", "limitations-empty");
        ui.limitations.appendChild(noLimits);
      } else {
        view.limitations.forEach(function (item, index) {
          var li = el(doc, "li", "acp-rr-list-item", str(item));
          li.setAttribute("data-limitation-index", String(index));
          ui.limitations.appendChild(li);
        });
      }

      clearList(ui.blocking);
      if (!view.blocking_missing_checks.length) {
        var noBlocking = el(doc, "li", "acp-rr-empty-item", t("next.record.blocking.empty"));
        noBlocking.setAttribute("data-role", "blocking-empty");
        ui.blocking.appendChild(noBlocking);
      } else {
        view.blocking_missing_checks.forEach(function (item, index) {
          var li = el(doc, "li", "acp-rr-list-item", str(item));
          li.setAttribute("data-blocking-index", String(index));
          ui.blocking.appendChild(li);
        });
      }

      clearList(ui.refsList);
      if (!view.evidence_refs.length) {
        var noRefs = el(doc, "li", "acp-rr-empty-item", t("next.record.refs.empty"));
        noRefs.setAttribute("data-role", "refs-empty");
        ui.refsList.appendChild(noRefs);
      } else {
        view.evidence_refs.forEach(function (ref, index) {
          var li = el(doc, "li", "acp-rr-ref", "");
          li.setAttribute("data-evidence-index", String(index));
          li.setAttribute("data-evidence-id", ref.evidence_id);
          var head = el(doc, "div", "acp-rr-ref-head", "");
          var id = el(doc, "span", "acp-rr-mono", ref.evidence_id);
          id.setAttribute("data-field", "evidence_refs[" + index + "].evidence_id");
          var kind = el(doc, "span", "acp-rr-tag", ref.kind);
          kind.setAttribute("data-field", "evidence_refs[" + index + "].kind");
          head.appendChild(id);
          head.appendChild(kind);
          li.appendChild(head);
          var digest = el(doc, "div", "acp-rr-mono acp-rr-ref-digest", ref.sha256);
          digest.setAttribute("data-field", "evidence_refs[" + index + "].sha256");
          digest.setAttribute("title", ref.sha256);
          li.appendChild(digest);
          if (ref.relative_path) {
            var path = el(doc, "div", "acp-rr-ref-path", ref.relative_path);
            path.setAttribute("data-field", "evidence_refs[" + index + "].relative_path");
            li.appendChild(path);
          }
          ui.refsList.appendChild(li);
        });
      }

      var mismatchFields = bindingViewMismatches(view);
      if (ui.sourceHint) {
        ui.sourceHint.setAttribute("data-acp-binding-match", mismatchFields.length ? "false" : "true");
        if (mismatchFields.length) {
          ui.sourceHint.textContent = t("next.record.entry.binding_mismatch",
            { fields: mismatchFields.join(", ") });
        } else {
          ui.sourceHint.textContent = t("next.record.source.hint");
        }
      }
    }

    function claimNode(claimRow) {
      var li = el(doc, "li", "acp-rr-claim", "");
      li.setAttribute("data-claim-id", claimRow.claim_id);
      li.setAttribute("data-claim-status", claimRow.status);
      li.setAttribute("data-acp-claim-source", "server");

      var head = el(doc, "div", "acp-rr-claim-head", "");
      var id = el(doc, "span", "acp-rr-claim-id acp-rr-mono", claimRow.claim_id);
      head.appendChild(id);
      var status = el(doc, "span", "acp-rr-badge acp-rr-claim-status", claimLabel(claimRow.status));
      status.setAttribute("data-status",
        CLAIM_STATUSES.indexOf(claimRow.status) !== -1 ? claimRow.status : "unknown");
      head.appendChild(status);
      li.appendChild(head);

      if (claimRow.statement) {
        var statementLabel = i18nEl(doc, "span", "acp-rr-claim-statement-label", "next.record.label.claim_statement");
        var statement = el(doc, "p", "acp-rr-claim-statement", claimRow.statement);
        li.appendChild(statementLabel);
        li.appendChild(statement);
      }

      var meta = el(doc, "dl", "acp-rr-grid acp-rr-claim-meta", "");
      if (claimRow.validator_id) {
        meta.appendChild(i18nEl(doc, "dt", "", "next.record.label.validator"));
        var validator = el(doc, "dd", "acp-rr-mono",
          claimRow.validator_id + (claimRow.validator_version ? " v" + claimRow.validator_version : ""));
        validator.setAttribute("data-field", "claims[" + claimRow.claim_id + "].validator");
        meta.appendChild(validator);
      }
      if (claimRow.source_ref_count != null) {
        meta.appendChild(i18nEl(doc, "dt", "", "next.record.label.source_refs"));
        var count = el(doc, "dd", "", String(claimRow.source_ref_count));
        count.setAttribute("data-field", "claims[" + claimRow.claim_id + "].source_ref_count");
        meta.appendChild(count);
      }
      if (claimRow.blocking_missing_checks.length) {
        meta.appendChild(i18nEl(doc, "dt", "", "next.record.label.blocking"));
        var checks = el(doc, "dd", "", claimRow.blocking_missing_checks.join(", "));
        checks.setAttribute("data-field", "claims[" + claimRow.claim_id + "].blocking_missing_checks");
        meta.appendChild(checks);
      }
      li.appendChild(meta);
      return li;
    }

    function claimLabel(status) {
      if (CLAIM_STATUSES.indexOf(status) !== -1) {
        return t("next.record.claim." + status);
      }
      if (!status) return t("next.record.claim.unknown", { status: "—" });
      return t("next.record.claim.unknown", { status: status });
    }

    function bindingViewMismatches(view) {
      var binding = currentBinding();
      if (!isObject(binding) || !isObject(view)) return [];
      var project = projectOf(binding);
      var round = intOrNull(binding.round);
      var taskRef = taskRefOf(binding);
      var fields = [];
      if (trim(view.research.project_id) !== project) fields.push("research.project_id");
      if (trim(binding.researchId) && trim(view.research.research_id) !== trim(binding.researchId)) {
        fields.push("research.research_id");
      }
      if (round != null && intOrNull(view.research.round) !== round) fields.push("research.round");
      if (!taskRef || taskRefProblem(taskRef)) return fields;
      if (trim(view.task_ref.job_id || view.task_ref.task_id) !== taskRef.job_id) fields.push("task_ref.job_id");
      if (intOrNull(view.task_ref.attempt) !== taskRef.attempt) fields.push("task_ref.attempt");
      if (trim(view.task_ref.release.release_id) !== taskRef.release_id) {
        fields.push("task_ref.release.release_id");
      }
      return fields;
    }

    function renderEntry() {
      var ui = state.ui;
      if (!ui.target || !ui.decisionWrap) return;
      var target = state.form.target === "record" ? "record" : "proposal";
      ui.target.value = target;
      ui.decisionWrap.hidden = target !== "record";
      ui.decision.disabled = state.submitBusy || target !== "record";
      ui.target.disabled = state.submitBusy;
      ui.submit.textContent = state.submitBusy
        ? t("next.record.entry.submitting")
        : t(target === "proposal" ? "next.record.entry.submit_proposal" : "next.record.entry.submit_record");
      ui.submit.setAttribute("data-i18n",
        target === "proposal" ? "next.record.entry.submit_proposal" : "next.record.entry.submit_record");
      ui.submit.disabled = state.submitBusy;
      ui.targetBadge.textContent = t(target === "proposal" ? "next.record.badge.proposal" : "next.record.badge.record");
      ui.targetBadge.setAttribute("data-target", target);
      ui.targetNote.textContent = t(target === "proposal"
        ? "next.record.entry.note_proposal" : "next.record.entry.note_record");
      ui.targetNote.setAttribute("data-target", target);
      if (ui.root) ui.root.setAttribute("data-acp-target", target);
      ui.root.setAttribute("data-acp-busy", state.submitBusy ? "submit" : "none");

      clearList(ui.bindGrid);
      var binding = currentBinding();
      var rows = bindingRows(binding, target);
      rows.forEach(function (item) {
        ui.bindGrid.appendChild(i18nEl(doc, "dt", "", item.labelKey));
        var dd = el(doc, "dd", "acp-rr-mono", displayValue(item.value));
        dd.setAttribute("data-field", item.field);
        dd.setAttribute("data-ok", item.ok ? "true" : "false");
        ui.bindGrid.appendChild(dd);
      });
      ui.bindNote.hidden = !!binding;
      ui.bindNote.textContent = binding ? "" : t("next.record.entry.note_no_binding");
    }

    function renderRefs() {
      var ui = state.ui;
      if (!ui.refsRows) return;
      clearList(ui.refsRows);
      if (!state.refs.length) state.refs = [{ evidence_id: "", kind: "", sha256: "", relative_path: "" }];
      state.refs.forEach(function (ref, index) {
        var rowEl = el(doc, "div", "acp-rr-ref-row", "");
        rowEl.setAttribute("data-evidence-row", String(index));
        var specs = [
          { key: "evidence_id", labelKey: "next.record.entry.evidence_id", value: ref.evidence_id },
          { key: "kind", labelKey: "next.record.entry.kind", value: ref.kind },
          { key: "sha256", labelKey: "next.record.entry.sha256", value: ref.sha256 },
          { key: "relative_path", labelKey: "next.record.entry.relative_path", value: ref.relative_path },
        ];
        specs.forEach(function (spec) {
          var wrap = el(doc, "div", "acp-rr-field", "");
          var label = i18nEl(doc, "label", "acp-rr-label", spec.labelKey);
          var input = el(doc, "input", "acp-rr-input");
          input.type = "text";
          input.value = str(spec.value);
          input.setAttribute("data-ref-field", spec.key);
          input.setAttribute("data-ref-index", String(index));
          label.setAttribute("for", "acp-rr-ref-" + index + "-" + spec.key);
          input.setAttribute("id", "acp-rr-ref-" + index + "-" + spec.key);
          input.addEventListener("input", function (event) {
            state.refs[index][spec.key] = String(event.target.value || "");
          });
          wrap.appendChild(label);
          wrap.appendChild(input);
          rowEl.appendChild(wrap);
        });
        var remove = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-rr-remove", "next.record.entry.remove");
        remove.type = "button";
        remove.setAttribute("data-action", "remove-evidence");
        remove.setAttribute("data-ref-index", String(index));
        remove.disabled = state.refs.length <= 1;
        remove.addEventListener("click", function () {
          if (state.refs.length <= 1) return;
          state.refs.splice(index, 1);
          renderRefs();
        });
        rowEl.appendChild(remove);
        ui.refsRows.appendChild(rowEl);
      });
      syncRefsFromDom();
    }

    function addEvidenceRow(values) {
      state.refs.push({
        evidence_id: str(values.evidence_id),
        kind: str(values.kind),
        sha256: str(values.sha256),
        relative_path: str(values.relative_path),
      });
    }

    function syncRefsFromDom() {
      childrenOf(state.ui.refsRows).forEach(function (rowEl) {
        var index = Number(rowEl.getAttribute("data-evidence-row"));
        if (isNaN(index) || !state.refs[index]) return;
        childrenOf(rowEl).forEach(function (wrap) {
          childrenOf(wrap).forEach(function (input) {
            var fieldName = input.getAttribute && input.getAttribute("data-ref-field");
            if (!fieldName) return;
            state.refs[index][fieldName] = String(input.value || "");
          });
        });
      });
    }

    function renderErrors() {
      var ui = state.ui;
      if (!ui.errors) return;
      clearList(ui.errors);
      ui.errors.hidden = state.errors.length === 0;
      state.errors.forEach(function (item) {
        var li = el(doc, "li", "acp-rr-error", problemText(item));
        li.setAttribute("data-problem-code", item.code);
        if (item.field) li.setAttribute("data-problem-field", item.field);
        ui.errors.appendChild(li);
      });
    }

    function renderNotice() {
      var ui = state.ui;
      if (!ui.notice) return;
      if (!state.notice) {
        ui.notice.hidden = true;
        ui.notice.textContent = "";
        ui.notice.removeAttribute("data-kind");
        return;
      }
      ui.notice.hidden = false;
      ui.notice.textContent = state.notice.text;
      ui.notice.setAttribute("data-kind", state.notice.kind || "error");
    }

    /* ── requests ───────────────────────────────────────────────────── */

    function formFromDom() {
      var ui = state.ui;
      state.form.question = ui.question ? ui.question.value : state.form.question;
      state.form.claim = ui.claim ? ui.claim.value : state.form.claim;
      state.form.target = ui.target ? ui.target.value : state.form.target;
      state.form.decision = ui.decision ? ui.decision.value : state.form.decision;
      return state.form;
    }

    function refsFromDom() {
      syncRefsFromDom();
      return state.refs.map(function (ref) {
        return {
          evidence_id: trim(ref.evidence_id),
          kind: trim(ref.kind),
          sha256: trim(ref.sha256),
          relative_path: trim(ref.relative_path),
        };
      });
    }

    function loadEvidence() {
      if (state.disposed || !state.mounted) return Promise.resolve();
      var ui = state.ui;
      var researchId = trim(ui.researchId ? ui.researchId.value : state.researchId);
      var sessionSeed = trim(ui.sessionSeed ? ui.sessionSeed.value : state.sessionSeed);
      var roundText = trim(ui.evidenceRound ? ui.evidenceRound.value : state.evidenceRound);
      state.researchId = researchId;
      state.sessionSeed = sessionSeed;
      state.evidenceRound = roundText;
      if (!researchId) {
        state.evidence = {
          status: "empty",
          view: null,
          warnings: [],
          error: t("next.record.no_research_id"),
          errorInfo: null,
        };
        render();
        return Promise.resolve();
      }
      if (!trim(state.projectId)) state.projectId = projectOf(currentBinding());
      if (!trim(state.projectId)) {
        state.evidence = {
          status: "error",
          view: null,
          warnings: [],
          error: t("next.record.no_project"),
          errorInfo: null,
        };
        render();
        return Promise.resolve();
      }
      var request = transport();
      if (!request) {
        state.evidence = {
          status: "error",
          view: null,
          warnings: [],
          error: t("next.record.no_transport"),
          errorInfo: null,
        };
        render();
        return Promise.resolve();
      }
      var round = intOrNull(roundText);
      state.readToken += 1;
      var token = state.readToken;
      var gen = state.generation;
      state.evidence = { status: "loading", view: null, warnings: [], error: "", errorInfo: null };
      render();
      var path = evidencePath(researchId, state.projectId, sessionSeed, round);
      return Promise.resolve()
        .then(function () { return request(path, { method: "GET" }); })
        .then(function (payload) {
          if (!current(gen, token)) return;
          var normalized = normalizeEvidence(payload);
          if (!normalized.ok) {
            state.evidence = {
              status: "error",
              view: null,
              warnings: [],
              error: t("next.record.invalid_view") + " " +
                t("next.record.invalid_view_hint", { fields: normalized.problems.join(", ") }),
              errorInfo: null,
            };
            render();
            return;
          }
          state.evidence = {
            status: "ready",
            view: normalized.view,
            warnings: normalized.warnings || [],
            error: "",
            errorInfo: null,
          };
          if (!state.projectId && normalized.view.research.project_id) {
            state.projectId = normalized.view.research.project_id;
          }
          render();
        })
        .catch(function (error) {
          if (!current(gen, token)) return;
          var info = errorInfo(error);
          state.evidence = {
            status: "error",
            view: null,
            warnings: [],
            error: t("next.record.error", { message: describeError(error) }),
            errorInfo: info,
          };
          render();
        });
    }

    function submitRecord() {
      if (state.disposed || !state.mounted || state.submitBusy) return Promise.resolve();
      var form = formFromDom();
      var target = form.target === "record" ? "record" : "proposal";
      var binding = currentBinding();
      var view = state.evidence.status === "ready" ? state.evidence.view : null;
      var built = target === "proposal"
        ? buildProposalBody(binding, form, { nowIso: nowIso() })
        : buildRecordBody(binding, form, refsFromDom(), view, {
          nowIso: nowIso(),
          editorRef: binding && binding.editorRef,
        });
      if (!built.ok) {
        state.errors = built.problems;
        state.notice = null;
        render();
        return Promise.resolve();
      }
      var request = transport();
      if (!request) {
        state.errors = [];
        state.notice = { kind: "error", text: t("next.record.no_transport") };
        render();
        return Promise.resolve();
      }
      state.errors = [];
      state.notice = null;
      state.submitBusy = true;
      state.submitToken += 1;
      var token = state.submitToken;
      state.submitBusyToken = token;
      var gen = state.generation;
      render();
      var path = target === "proposal" ? PROPOSALS_PATH : RECORDS_PATH;
      return Promise.resolve()
        .then(function () {
          return request(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(built.body),
          });
        })
        .then(function (payload) {
          if (state.submitBusyToken === token) {
            state.submitBusy = false;
            state.submitBusyToken = 0;
          }
          if (!submitCurrent(gen, token)) {
            render();
            return;
          }
          var resourceId = payload && payload.resource_id ? String(payload.resource_id) : "—";
          var revision = payload && payload.revision != null ? String(payload.revision) : "—";
          var digest = payload && payload.digest ? String(payload.digest) : "—";
          var key = target === "proposal"
            ? "next.record.notice.proposal_created" : "next.record.notice.record_created";
          state.notice = {
            kind: "ok",
            text: t(key, { resource_id: resourceId, revision: revision, digest: digest }),
          };
          if (target === "record" && payload && isObject(payload.document) &&
              trim(payload.document.research_id) && state.ui.researchId &&
              !trim(state.ui.researchId.value)) {
            state.ui.researchId.value = trim(payload.document.research_id);
            state.researchId = trim(payload.document.research_id);
          }
          render();
        })
        .catch(function (error) {
          if (state.submitBusyToken === token) {
            state.submitBusy = false;
            state.submitBusyToken = 0;
          }
          if (!submitCurrent(gen, token)) {
            render();
            return;
          }
          var info = errorInfo(error);
          var text;
          var kind = "error";
          if (info.status === 409 || info.code === "revision_conflict") {
            kind = "conflict";
            text = t("next.record.notice.conflict", { message: describeError(error) });
          } else if (info.status === 401 || info.status === 403 || info.code === "access_denied") {
            text = t("next.record.notice.access");
          } else if (info.status === 404 || info.code === "not_found") {
            text = t("next.record.notice.not_found");
          } else if (info.status === 400 || info.status === 422) {
            text = t("next.record.notice.failed", { message: describeError(error) });
          } else if (info.status >= 500 || !info.status) {
            text = t("next.record.notice.failed", { message: describeError(error) });
          } else {
            text = t("next.record.notice.failed", { message: describeError(error) });
          }
          state.notice = { kind: kind, text: text };
          render();
          if (kind === "conflict") {
            // A 409/conflict means the record/evidence binding moved: re-read the
            // projection (never replay the submit); the notice survives the read.
            return loadEvidence();
          }
        });
    }

    /* ── lifecycle ──────────────────────────────────────────────────── */

    function applySnapshot(snapshot) {
      if (state.disposed || !state.mounted) return;
      state.snapshot = snapshot || null;
      var project = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      if (project) state.projectId = project;
      if (!trim(state.projectId)) state.projectId = projectOf(currentBinding());
      if (trim(project) && state.ui.root) {
        state.ui.root.setAttribute("data-project", project);
      }
      var researchRef = snapshot && snapshot.researchRef;
      var researchId = researchRef && (researchRef.recordId || researchRef.researchId)
        ? String(researchRef.recordId || researchRef.researchId) : "";
      if (researchId) {
        state.researchId = researchId;
        if (state.ui.researchId) {
          state.ui.researchId.value = researchId;
        }
      }
      var taskRef = snapshot && snapshot.taskRef;
      if (taskRef && state.binding && isObject(state.binding.taskRef)) {
        if (!trim(state.binding.taskRef.job_id) && taskRef.jobId != null) {
          state.binding.taskRef.job_id = String(taskRef.jobId);
        }
        if (intOrNull(state.binding.taskRef.attempt) == null && intOrNull(taskRef.attempt) != null) {
          state.binding.taskRef.attempt = intOrNull(taskRef.attempt);
        }
        if (!trim(state.binding.taskRef.project_id) && trim(project)) {
          state.binding.taskRef.project_id = trim(project);
        }
      }
      var contextual = snapshot && (snapshot.researchBinding || snapshot.binding);
      if (!state.bindingOverride && !isObject(state.binding) && isObject(contextual)) {
        state.binding = cloneJson(contextual) || contextual;
        prefillFromBinding();
      }
      autoTarget();
      var key = [
        state.projectId,
        state.researchId,
        state.sessionSeed,
        state.evidenceRound,
        state.form.target,
        jsonKey(state.binding),
      ].join("|");
      if (key !== state.key) {
        state.key = key;
        // A new selection/identity invalidates an in-flight submit: its receipt
        // belongs to the previous round and must never paint into this one.
        state.submitToken += 1;
        if (state.researchId) {
          loadEvidence();
        } else {
          state.evidence = { status: "empty", view: null, warnings: [], error: "", errorInfo: null };
          render();
        }
      } else {
        render();
      }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("research-record: mount host missing");
      if (!doc) throw new Error("research-record: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.readToken += 1;
      state.submitToken += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.submitBusy = false;
      state.submitBusyToken = 0;
      state.key = "";
      state.targetTouched = false;
      state.form = { question: "", claim: "", target: "proposal", decision: "" };
      state.refs = [];
      state.errors = [];
      state.notice = null;
      state.evidence = { status: "empty", view: null, warnings: [], error: "", errorInfo: null };
      state.context = api.context || deps.context || null;
      state.binding = initialBinding();
      if (!trim(state.projectId)) state.projectId = projectOf(state.binding);
      if (!trim(state.researchId) && state.binding) {
        state.researchId = trim(state.binding.researchId);
        state.sessionSeed = trim(state.binding.sessionSeed);
        if (intOrNull(state.binding.round) != null) {
          state.evidenceRound = String(intOrNull(state.binding.round));
        }
      }
      autoTarget();
      buildDom();
      render();
      var context = state.context;
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
      var langHandler = function () { render(); };
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
      render();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.readToken += 1;
      state.submitToken += 1;
      state.submitBusy = false;
      state.submitBusyToken = 0;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (error) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.evidence = { status: "empty", view: null, warnings: [], error: "", errorInfo: null };
      state.binding = null;
      state.bindingOverride = null;
      state.snapshot = null;
      state.context = null;
      state.refs = [];
      state.errors = [];
      state.notice = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose,
      refresh: loadEvidence,
      snapshot: function () {
        return {
          state: state.evidence.status,
          evidence: state.evidence.view ? state.evidence.view.raw : null,
          view: state.evidence.view,
          binding: currentBinding(),
          target: state.form.target,
          notice: state.notice,
          errors: state.errors.slice(),
          busy: state.submitBusy,
        };
      },
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: function (deps) { return createResearchRecord(deps || {}); },
    _internals: {
      DECISIONS: DECISIONS,
      CLAIM_STATUSES: CLAIM_STATUSES,
      SCIENCE_FIELDS: SCIENCE_FIELDS,
      BUDGET_FIELDS: BUDGET_FIELDS,
      buildProposalBody: buildProposalBody,
      buildRecordBody: buildRecordBody,
      normalizeEvidence: normalizeEvidence,
      claimRows: claimRows,
      evidencePath: evidencePath,
      viewMatchesBinding: viewMatchesBinding,
      errorInfo: errorInfo,
      isDigest: isDigest,
      ZH: ZH,
    },
  };
})();

