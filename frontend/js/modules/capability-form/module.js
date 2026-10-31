/*
 * ACP 3.0 experiment-entry module — capability-form (Track A / A-59 + A-60, P23).
 *
 * Schema-driven form for ONE discovered capability: renders the frozen
 * `acp2.ui.form.v1` document served by
 * `ACPClient.science.fetchCapabilityForms` and validates / preflights /
 * submits it through injected clients. Classic script, no build, no ESM.
 *
 * CHOICE — compact module, NOT a wrapper of the page-coupled classic feature:
 *   `frontend/js/workbench/features/capability-form.js` (`ACPCapabilityForm`)
 *   mounts into the fixed `#capability-form-body` element, pulls its contract
 *   and `t()` from page globals, creates `window.ACPCapabilityForm` /
 *   `ACPWorkbench.features` and owns no module lifecycle (no four-state
 *   `data-acp-module-state`, no generation guard, no dispose). Loading it in
 *   `/next/` would therefore add page globals the shell does not own and still
 *   require rendering/lifecycle code around it. This module instead consumes
 *   the SAME form document and the SAME client-side UI-subset contract
 *   (`ACPWorkbench.contracts.uiSubset`, from
 *   `js/workbench/contracts/ui_subset.js`). It defines NO schema rules of its
 *   own: whitelist validation, value validation, the request body and the
 *   render model all come from that shared contract.
 *
 * Rules this module enforces:
 *   - rendering is createElement + textContent only; a remote title /
 *     description / error message can never become markup;
 *   - the served document is checked against the frozen local subset before it
 *     is interpreted (`ui_subset_drift`, unknown majors, per-form whitelist,
 *     digest match) — a form the UI cannot interpret is a typed error state,
 *     never an empty-but-submittable form;
 *   - unknown values and unbound fields are rejected explicitly
 *     (`ui_value_unknown_field`, `ui_binding_missing`) and reported per field;
 *     nothing is silently dropped;
 *   - preflight is required and must still match the current values; submit is
 *     refused locally when the form is invalid, the preflight is missing/stale,
 *     or no approved record is bound. The approval-required state is visible;
 *   - `submit` reports "submitted" only for a real TaskRef (job id + attempt);
 *     a missing/ambiguous/network outcome is visibly "unconfirmed", a definite
 *     refusal is "error" — never fake success;
 *   - A-60: typed submit failures are mapped to readable stable codes
 *     (`input_invalid`, `access_denied`, `not_found`, `revision_conflict`,
 *     `validation_failed`, `submit_unconfirmed`) and rendered with a stable
 *     `data-acp-submit-state` + `data-acp-error-code`; a 409 invalidates the
 *     plan/approval binding and refreshes the preflight, so a new review is
 *     required and the submit is never replayed automatically; a busy instance
 *     refuses a second preflight/submit locally, and dispose/generation drops
 *     late receipts;
 *   - four states (empty / loading / ready / error) live on
 *     `data-acp-module-state`; every async result is gated by the instance
 *     generation, and `dispose()` unsubscribes, bumps the generation and
 *     detaches the DOM.
 *
 * No fetch, no URL prefix, no second cache, no new page global besides
 * `window.ACPNextModules["capability-form"]`.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "capability-form";
  var VIEW_SCHEMA = "acp2.ui.capability-form.view.v1";

  /* Inline zh copy. Every static label also carries a data-i18n key so the
   * shell dictionary can take over; keys are listed in README.md. */
  var ZH = {
    "next.capabilityForm.state.empty": "选择项目后显示可用的能力表单。",
    "next.capabilityForm.state.no_forms": "当前项目没有可用的能力表单。",
    "next.capabilityForm.state.loading": "正在加载能力表单…",
    "next.capabilityForm.state.error": "能力表单加载失败",
    "next.capabilityForm.action.retry": "重试",
    "next.capabilityForm.action.preflight": "预检",
    "next.capabilityForm.action.preflighting": "预检中…",
    "next.capabilityForm.action.submit": "提交任务",
    "next.capabilityForm.action.submitting": "提交中…",
    "next.capabilityForm.capability": "能力",
    "next.capabilityForm.tag.read_only": "只读",
    "next.capabilityForm.tag.unavailable": "不可用",
    "next.capabilityForm.required": "必填",
    "next.capabilityForm.choose": "请选择",
    "next.capabilityForm.read_only": "该能力已停用，历史记录只读展示。",
    "next.capabilityForm.unavailable": "该能力表单不可用：{reason}",
    "next.capabilityForm.version": "版本",
    "next.capabilityForm.descriptor_digest": "描述符摘要",
    "next.capabilityForm.form_digest": "表单摘要",
    "next.capabilityForm.plan_digest": "计划摘要",
    "next.capabilityForm.preflight_fingerprint": "预检指纹",
    "next.capabilityForm.reason": "原因",
    "next.capabilityForm.approval.required": "需要审批：绑定已批准的记录后才能提交。",
    "next.capabilityForm.approval.not_approved": "审批状态为 {status}，不能提交。",
    "next.capabilityForm.approval.mismatch": "审批绑定的计划与当前预检不一致，请重新审批或重新预检。",
    "next.capabilityForm.approval.granted": "已绑定审批 {ref}",
    "next.capabilityForm.preflight.idle": "尚未预检。",
    "next.capabilityForm.preflight.loading": "预检中…",
    "next.capabilityForm.preflight.ok": "预检通过",
    "next.capabilityForm.preflight.stale": "配置已变更，请重新预检。",
    "next.capabilityForm.preflight.error": "预检未通过",
    "next.capabilityForm.preflight.feasible": "预计可行",
    "next.capabilityForm.preflight.infeasible": "预计不可行（仍以平台预检结果为准）",
    "next.capabilityForm.submit.idle": "预检通过并绑定审批后可提交。",
    "next.capabilityForm.submit.ok": "提交已完成",
    "next.capabilityForm.submit.submitting": "提交中…",
    "next.capabilityForm.submit.submitted": "已提交：任务 {jobId} · 第 {attempt} 次尝试",
    "next.capabilityForm.submit.unconfirmed": "提交结果未确认：{message}。请先查询任务状态，勿盲目重复提交。",
    "next.capabilityForm.submit.error": "提交被拒绝：{message}",
    "next.capabilityForm.submit.error_title": "提交未通过",
    "next.capabilityForm.submit.unconfirmed_title": "提交结果未确认",
    "next.capabilityForm.submit.conflict_title": "提交未生效：需要重新复核",
    "next.capabilityForm.submit.conflict": "预检计划或审批已在服务端变更（409）。已刷新预检并解除审批绑定；请重新预检、重新审批。",
    "next.capabilityForm.submit.conflict_hint": "不会自动重放本次提交；请复核当前计划摘要后重新提交。",
    "next.capabilityForm.problems": "需要修正 {count} 项",
    "next.capabilityForm.locked": "预检或提交进行中，输入暂时只读。",
    "next.capabilityForm.unit.second": "秒",
    "next.capabilityForm.unit.kelvin": "开尔文",
    "next.capabilityForm.unit.angstrom": "埃",
    "next.capabilityForm.unit.hartree": "哈特里",
    "next.capabilityForm.code.ui_value_required": "必填",
    "next.capabilityForm.code.ui_value_type": "类型不符",
    "next.capabilityForm.code.ui_value_enum": "不在允许取值内",
    "next.capabilityForm.code.ui_value_bounds": "超出允许范围",
    "next.capabilityForm.code.ui_value_length": "长度不符合要求",
    "next.capabilityForm.code.ui_value_pattern": "格式不符合要求",
    "next.capabilityForm.code.ui_value_format": "格式不符合要求",
    "next.capabilityForm.code.ui_value_unknown_field": "表单未声明该字段",
    "next.capabilityForm.code.ui_form_invalid": "表单校验未通过",
    "next.capabilityForm.code.ui_form_missing": "没有可用的能力表单",
    "next.capabilityForm.code.ui_form_malformed": "表单文档格式错误",
    "next.capabilityForm.code.ui_form_digest_mismatch": "表单摘要不匹配",
    "next.capabilityForm.code.ui_binding_missing": "字段缺少 TaskRequest 绑定",
    "next.capabilityForm.code.ui_binding_unknown": "字段绑定未知",
    "next.capabilityForm.code.ui_subset_unavailable": "UI 子集契约未加载",
    "next.capabilityForm.code.ui_subset_unknown_major": "表单子集版本未知",
    "next.capabilityForm.code.ui_subset_drift": "服务端表单子集与本地不一致",
    "next.capabilityForm.code.ui_keyword_rejected": "表单使用了未允许的关键字",
    "next.capabilityForm.code.ui_keyword_unknown": "表单使用了未知关键字",
    "next.capabilityForm.code.platform_transport_required": "缺少注入的数据客户端",
    "next.capabilityForm.code.capability_forms_unavailable": "能力表单加载失败",
    "next.capabilityForm.code.preflight_required": "需先完成预检",
    "next.capabilityForm.code.preflight_stale": "预检已过期",
    "next.capabilityForm.code.preflight_failed": "预检失败",
    "next.capabilityForm.code.approval_required": "需要审批",
    "next.capabilityForm.code.approval_not_granted": "审批未通过",
    "next.capabilityForm.code.approval_card_invalid": "审批记录无效",
    "next.capabilityForm.code.approved_plan_digest_mismatch": "审批计划摘要与当前预检不一致",
    "next.capabilityForm.code.submit_unconfirmed": "提交结果未确认",
    "next.capabilityForm.code.request_failed": "请求失败",
    "next.capabilityForm.code.input_invalid": "输入无效",
    "next.capabilityForm.code.access_denied": "当前身份无权提交该任务（服务端拒绝）",
    "next.capabilityForm.code.not_found": "目标项目或任务不存在，或当前身份不可见",
    "next.capabilityForm.code.revision_conflict": "预检计划或审批已在服务端变更，需要重新预检并重新审批",
    "next.capabilityForm.code.approval_conflict": "审批已在服务端变更，需要重新复核",
    "next.capabilityForm.code.validation_failed": "提交内容未通过服务端校验",
    "next.capabilityForm.code.submit_busy": "已有预检或提交正在进行中",
    "next.capabilityForm.code.preflight_busy": "已有预检或提交正在进行中",
    "next.capabilityForm.code.stale_result": "已过期的结果",
    "next.capabilityForm.code.module_disposed": "模块已释放"
  };

  /* Friendly fallbacks when neither the shell dictionary nor ZH has a copy. */
  var CODE_TEXT = {
    ui_value_required: "required value missing",
    ui_value_type: "wrong value type",
    ui_value_enum: "value outside the enum",
    ui_value_bounds: "value outside the bounds",
    ui_value_length: "value outside the length bounds",
    ui_value_pattern: "value does not match the pattern",
    ui_value_format: "value does not match the format",
    ui_value_unknown_field: "the form does not declare this field",
    ui_form_invalid: "form values are invalid",
    ui_form_missing: "no capability form is selected",
    ui_form_malformed: "the served form document is malformed",
    ui_form_digest_mismatch: "the served form digest does not match its body",
    ui_binding_missing: "a form field carries no TaskRequest binding",
    ui_binding_unknown: "a form field names an unknown binding",
    ui_subset_unavailable: "the UI-subset contract is not loaded",
    ui_subset_drift: "the served subset differs from this UI build",
    ui_keyword_rejected: "a keyword outside the frozen subset is used",
    ui_keyword_unknown: "an unknown keyword is used",
    platform_transport_required: "an injected client is required",
    capability_forms_unavailable: "capability forms could not be loaded",
    preflight_required: "preflight must run first",
    preflight_stale: "the configuration changed after the preflight",
    preflight_failed: "preflight failed",
    approval_required: "an approved record is required",
    approval_not_granted: "the bound approval is not granted",
    approval_card_invalid: "the approval record is invalid",
    submit_unconfirmed: "the submission outcome is unconfirmed",
    request_failed: "request failed",
    input_invalid: "invalid input",
    access_denied: "the current identity may not submit this task",
    not_found: "the project or task was not found",
    revision_conflict: "the plan or approval changed on the server",
    approval_conflict: "the approval changed on the server",
    validation_failed: "the submission failed server-side validation",
    submit_busy: "another preflight or submit is already running",
    preflight_busy: "another preflight or submit is already running",
    stale_result: "a superseded result was discarded",
    module_disposed: "the module has been disposed"
  };

  // ------------------------------------------------------------------
  // pure plumbing
  // ------------------------------------------------------------------

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function copy(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function failure(code, message, extra) {
    extra = extra || {};
    var detail = String(message == null ? "" : message);
    var err = new Error(code + ": " + detail);
    err.code = code;
    err.detail = detail;
    err.path = extra.path == null ? null : extra.path;
    err.problems = extra.problems || null;
    if (extra.status != null) {
      err.status = extra.status;
      err.httpStatus = extra.status;
    }
    if (extra.cause) err.cause = extra.cause;
    return err;
  }

  /** Typed projection of any thrown value: a coded error is kept, everything
   * else becomes an explicit fallback code (never an untyped string). */
  function typed(error, fallbackCode) {
    if (error && typeof error.code === "string" && error.code) return error;
    var wrapped = failure(fallbackCode || "request_failed",
      (error && error.message) || String(error == null ? "" : error));
    if (error && typeof error.status === "number") {      wrapped.status = error.status;
      wrapped.httpStatus = error.status;
    }
    if (error && typeof error.httpStatus === "number") wrapped.httpStatus = error.httpStatus;
    wrapped.cause = error;
    return wrapped;
  }

  function shellTranslate(key) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (!shell || typeof shell.t !== "function") return null;
    var value = shell.t(key);
    return value && value !== key ? value : null;
  }

  function translate(key, vars) {
    var template = shellTranslate(key);
    if (template == null) template = hasOwn(ZH, key) ? ZH[key] : key;
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        template = template.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return template;
  }

  function errorText(code, fallback) {
    if (!code) return fallback == null ? "" : String(fallback);
    var translated = shellTranslate("next.capabilityForm.code." + code);
    if (translated != null) return translated;
    if (hasOwn(ZH, "next.capabilityForm.code." + code)) {
      return ZH["next.capabilityForm.code." + code];
    }
    if (hasOwn(CODE_TEXT, code)) return CODE_TEXT[code];
    return fallback == null ? String(code) : String(fallback);
  }

  /** Readable remote message from a typed error / problem.
   *
   * ACPClient failures carry `detail` as the server detail object and embed the
   * code in `Error.message` ("code: message"); rendering either raw would show
   * "[object Object]" or repeat the machine code. Only the human sentence is
   * used here — the stable code is rendered separately. */
  function messageText(problem) {
    if (!problem) return "";
    var detail = problem.detail;
    var message = "";
    if (isObject(detail) && typeof detail.message === "string") {
      message = detail.message;
    } else if (detail != null && typeof detail !== "object") {
      message = String(detail);
    }
    if (!message && problem.message != null) message = String(problem.message);
    var code = typeof problem.code === "string" ? problem.code : "";
    if (code && message.indexOf(code + ": ") === 0) message = message.slice(code.length + 2);
    return message;
  }

  /** Human line for one validation problem / typed error. */
  function errorLine(problem) {
    if (!problem) return "";
    var code = typeof problem.code === "string" ? problem.code : "";
    var message = messageText(problem);
    var friendly = errorText(code, message);
    if (message && message !== friendly) return friendly + "：" + message;
    return friendly;
  }

  // ------------------------------------------------------------------
  // instance
  // ------------------------------------------------------------------

  function createInstance(deps) {
    deps = deps || {};

    var context = deps.context || null;
    var alive = false;
    var mounted = false;
    var suspended = false;
    var host = null;
    var root = null;
    var doc = null;
    var generation = 0;
    var actionToken = 0;
    var keySequence = 0;
    var unsubscribeContext = null;

    var state = {
      phase: "empty",
      emptyReason: "no_project",
      projectId: "",
      document: null,
      entries: [],
      selected: null,
      fields: [],
      values: {},
      valuesRevision: 0,
      problems: [],
      fieldErrors: {},
      validation: { ok: false, code: null },
      loadError: null,
      actionError: null,
      preflightState: "idle",
      preflight: null,
      preflightError: null,
      submitState: "idle",
      submitError: null,
      submitResult: null,
      taskRef: null,
      approval: isObject(deps.approval) ? deps.approval : null,
      idempotencyKey: null
    };

    // ---- external seams ------------------------------------------------

    function uiSubset() {
      var contracts = window.ACPWorkbench && window.ACPWorkbench.contracts;
      var subset = contracts && contracts.uiSubset;
      if (!subset) return null;
      if (typeof subset.validateValues !== "function" ||
          typeof subset.validateFormDocument !== "function" ||
          typeof subset.buildFormOutcome !== "function" ||
          typeof subset.fieldModel !== "function" ||
          typeof subset.tablesMatch !== "function") {
        return null;
      }
      return subset;
    }

    function scienceClient() {
      if (deps.science && typeof deps.science.fetchCapabilityForms === "function") return deps.science;
      if (window.ACPClient && window.ACPClient.science &&
          typeof window.ACPClient.science.fetchCapabilityForms === "function") {
        return window.ACPClient.science;
      }
      return null;
    }

    function commandsClient() {
      if (deps.commands && typeof deps.commands.submit === "function") return deps.commands;
      if (window.ACPClient && window.ACPClient.researchCommands &&
          typeof window.ACPClient.researchCommands.submit === "function") {
        return window.ACPClient.researchCommands;
      }
      return null;
    }

    function assertLive(operation) {
      if (alive && mounted) return;
      throw failure("module_disposed",
        "capability-form " + operation + "() requires a mounted instance (mount/dispose lifecycle)");
    }

    function noop() {
      /* an internal load/action failure is already rendered; the promise is
       * fire-and-forget only for the DOM event handlers. */
    }

    // ---- state helpers -------------------------------------------------

    function isBusy() {
      return state.preflightState === "loading" || state.submitState === "submitting";
    }

    function approvalStatus() {
      if (!state.approval) return "required";
      if (state.approval.status !== "approved") return "not_approved";
      var plan = preflightPlanDigest();
      var bound = approvalPlanDigest();
      if (plan && plan !== bound) return "mismatch";
      return "granted";
    }

    function capabilityIdOf(entry) {
      return entry && typeof entry.capability_id === "string" ? entry.capability_id : "";
    }

    /** Plan digest of the last successful preflight (the plan an approval must
     * bind); it is the SAME value the preflight/submit contract returned. */
    function preflightPlanDigest() {
      if (!state.preflight) return null;
      var projection = state.preflight.projection || {};
      if (isObject(projection.plan) && typeof projection.plan.plan_digest === "string") {
        return projection.plan.plan_digest;
      }
      if (typeof projection.plan_digest === "string") return projection.plan_digest;
      return null;
    }

    function approvalPlanDigest() {
      if (!state.approval) return null;
      if (typeof state.approval.plan_digest === "string") return state.approval.plan_digest;
      if (typeof state.approval.planDigest === "string") return state.approval.planDigest;
      return null;
    }

    function preflightStale() {
      if (!state.preflight) return false;
      if (state.preflight.valuesRevision !== state.valuesRevision) return true;
      return state.preflight.capabilityId !== capabilityIdOf(state.selected);
    }

    function currentPreflightState() {
      if (state.preflightState === "ok" && preflightStale()) return "stale";
      return state.preflightState;
    }

    function findEntry(capabilityId) {
      for (var i = 0; i < state.entries.length; i += 1) {
        if (state.entries[i].capability_id === capabilityId) return state.entries[i];
      }
      return null;
    }

    function fieldFor(name) {
      for (var i = 0; i < state.fields.length; i += 1) {
        if (state.fields[i].name === name) return state.fields[i];
      }
      return null;
    }

    function firstSelectable(entries) {
      var i;
      for (i = 0; i < entries.length; i += 1) {
        if (entries[i].state === "available") return entries[i];
      }
      for (i = 0; i < entries.length; i += 1) {
        if (entries[i].state === "read_only") return entries[i];
      }
      return entries.length ? entries[0] : null;
    }

    function resetSelection() {
      state.selected = null;
      state.fields = [];
      state.values = {};
      state.valuesRevision = 0;
      state.problems = [];
      state.fieldErrors = {};
      state.validation = { ok: false, code: null };
      state.actionError = null;
      state.preflightState = "idle";
      state.preflight = null;
      state.preflightError = null;
      state.submitState = "idle";
      state.submitError = null;
      state.submitResult = null;
      state.taskRef = null;
      state.idempotencyKey = null;
    }

    function resetLoaded() {
      state.document = null;
      state.entries = [];
      resetSelection();
    }

    function resetToEmpty() {
      resetLoaded();
      state.projectId = "";
      state.phase = "empty";
      state.emptyReason = "no_project";
      state.loadError = null;
    }

    function failLoad(projectId, error) {
      generation += 1;
      actionToken += 1;
      resetLoaded();
      state.projectId = projectId;
      state.phase = "error";
      state.emptyReason = null;
      state.loadError = error;
      render();
      return error;
    }

    // ---- document guard (shared subset contract only) -----------------

    function guardDocument(document_, subset) {
      if (!isObject(document_)) {
        throw failure("ui_form_malformed", "the capability-forms body is not an object");
      }
      if (document_.schema !== subset.FORMS_SCHEMA) {
        throw failure("ui_form_malformed",
          "schema " + JSON.stringify(String(document_.schema)) + " is not " + subset.FORMS_SCHEMA);
      }
      if (document_.subset_version !== subset.SUBSET_VERSION) {
        throw failure("ui_subset_unknown_major",
          "served subset " + JSON.stringify(String(document_.subset_version)) +
          " is not " + subset.SUBSET_VERSION);
      }
      if (!subset.tablesMatch(document_.subset)) {
        throw failure("ui_subset_drift",
          "the served subset tables differ from this UI build");
      }
      if (!Array.isArray(document_.forms) || !Array.isArray(document_.read_only)) {
        throw failure("ui_form_malformed", "forms/read_only must be arrays");
      }
      document_.forms.forEach(function (entry, index) {
        if (!isObject(entry) || capabilityIdOf(entry) === "") {
          throw failure("ui_form_malformed", "form entry " + index + " has no capability_id");
        }
        if (entry.state !== "available" && entry.state !== "unavailable") {
          throw failure("ui_form_malformed",
            entry.capability_id + ": unknown form state " + JSON.stringify(String(entry.state)));
        }
        if (entry.state !== "available") {
          if (!isObject(entry.error) || typeof entry.error.code !== "string") {
            throw failure("ui_form_malformed",
              entry.capability_id + ": an unavailable entry carries no typed error");
          }
          return;
        }
        var check = subset.validateFormDocument(entry.form);
        if (!check.ok) {
          var first = check.problems[0] || { code: "ui_form_malformed", path: "$", message: "invalid form" };
          throw failure(first.code, entry.capability_id + ": " + first.message, {
            path: first.path,
            problems: check.problems
          });
        }
        if (entry.form.capability_id !== entry.capability_id) {
          throw failure("ui_form_malformed",
            entry.capability_id + ": the form declares capability_id " +
            JSON.stringify(String(entry.form.capability_id)));
        }
        if (typeof entry.form_digest !== "string" ||
            entry.form_digest !== entry.form.form_digest) {
          throw failure("ui_form_digest_mismatch",
            entry.capability_id + ": form_digest does not match the form body");
        }
      });
      document_.read_only.forEach(function (entry, index) {
        if (!isObject(entry) || capabilityIdOf(entry) === "") {
          throw failure("ui_form_malformed", "read_only entry " + index + " has no capability_id");
        }
        if (entry.state !== "read_only") {
          throw failure("ui_form_malformed",
            entry.capability_id + ": read_only entry has state " +
            JSON.stringify(String(entry.state)));
        }
      });
      return document_;
    }

    function buildEntries(document_) {
      var entries = [];
      document_.forms.forEach(function (entry) {
        entries.push({
          capability_id: entry.capability_id,
          state: entry.state,
          form: entry.state === "available" ? entry.form : null,
          form_digest: typeof entry.form_digest === "string" ? entry.form_digest : null,
          descriptor_digest: typeof entry.descriptor_digest === "string"
            ? entry.descriptor_digest : null,
          form_origin: typeof entry.form_origin === "string" ? entry.form_origin : null,
          capability_source: typeof entry.capability_source === "string"
            ? entry.capability_source : null,
          error: isObject(entry.error) ? {
            code: entry.error.code,
            message: entry.error.message == null ? "" : String(entry.error.message),
            params: isObject(entry.error.params) ? copy(entry.error.params) : null
          } : null,
          version: null,
          reason: null
        });
      });
      document_.read_only.forEach(function (entry) {
        entries.push({
          capability_id: entry.capability_id,
          state: "read_only",
          form: null,
          form_digest: null,
          descriptor_digest: typeof entry.descriptor_digest === "string"
            ? entry.descriptor_digest : null,
          form_origin: null,
          capability_source: typeof entry.capability_source === "string"
            ? entry.capability_source : null,
          error: null,
          version: typeof entry.version === "string" ? entry.version : null,
          reason: typeof entry.reason === "string" ? entry.reason : null
        });
      });
      return entries;
    }

    // ---- selection -----------------------------------------------------

    function applySelection(entry) {
      resetSelection();
      state.selected = entry;
      if (!entry || entry.state !== "available") return;
      var subset = uiSubset();
      if (!subset) {
        throw failure("ui_subset_unavailable",
          "ACPWorkbench.contracts.uiSubset is not loaded (load js/workbench/contracts/ui_subset.js first)");
      }
      var fields = subset.fieldModel(entry.form);
      fields.forEach(function (field) {
        if (!field.binding) {
          throw failure("ui_binding_missing",
            entry.capability_id + ": field " + field.name + " carries no TaskRequest binding",
            { path: field.name });
        }
        if (field.default !== null && field.default !== undefined) {
          state.values[field.name] = copy(field.default);
        }
      });
      state.fields = fields;
    }

    function select(capabilityId) {
      assertLive("select");
      var entry = findEntry(String(capabilityId));
      if (!entry) {
        var missing = failure("ui_form_missing", "the document has no capability " + String(capabilityId));
        state.actionError = missing;
        render();
        throw missing;
      }
      generation += 1;
      actionToken += 1;
      try {
        applySelection(entry);
      } catch (error) {
        var invalid = typed(error, "ui_form_malformed");
        state.phase = "error";
        state.loadError = invalid;
        state.selected = null;
        render();
        throw invalid;
      }
      render();
      return getState();
    }

    // ---- load ----------------------------------------------------------

    function scopeProjectId(scope) {
      if (typeof scope === "string") return scope;
      if (scope && typeof scope.projectId === "string" && scope.projectId) return scope.projectId;
      if (scope && typeof scope.project_id === "string" && scope.project_id) return scope.project_id;
      return "";
    }

    function resolveProjectId(scope) {
      return scopeProjectId(scope) || state.projectId;
    }

    function load(scope) {
      assertLive("load");
      var projectId = resolveProjectId(scope);
      if (!projectId) {
        generation += 1;
        actionToken += 1;
        resetToEmpty();
        render();
        return Promise.resolve(getState());
      }
      var subset = uiSubset();
      if (!subset) {
        return Promise.reject(failLoad(projectId, failure("ui_subset_unavailable",
          "ACPWorkbench.contracts.uiSubset is not loaded " +
          "(load js/workbench/contracts/ui_subset.js before this module)")));
      }
      var client = scienceClient();
      if (!client) {
        return Promise.reject(failLoad(projectId, failure("platform_transport_required",
          "capability forms require an injected science client " +
          "(ACPClient.science.fetchCapabilityForms)")));
      }
      generation += 1;
      actionToken += 1;
      var gen = generation;
      resetLoaded();
      state.projectId = projectId;
      state.phase = "loading";
      state.emptyReason = null;
      state.loadError = null;
      render();

      return Promise.resolve().then(function () {
        return client.fetchCapabilityForms({ projectId: projectId }, { generation: String(gen) });
      }).then(function (document_) {
        if (!alive || gen !== generation) {
          throw failure("stale_result", "a superseded capability-forms result was discarded");
        }
        try {
          guardDocument(document_, subset);
          state.document = document_;
          state.entries = buildEntries(document_);
          state.phase = state.entries.length ? "ready" : "empty";
          state.emptyReason = state.entries.length ? null : "no_forms";
          var entry = firstSelectable(state.entries);
          if (entry) applySelection(entry);
          render();
          return getState();
        } catch (error) {
          var invalid = typed(error, "ui_form_malformed");
          state.document = null;
          state.entries = [];
          resetSelection();
          state.phase = "error";
          state.loadError = invalid;
          render();
          throw invalid;
        }
      }, function (error) {
        if (!alive || gen !== generation) {
          throw failure("stale_result", "a superseded capability-forms failure was discarded");
        }
        var failed = typed(error, "capability_forms_unavailable");
        state.document = null;
        state.entries = [];
        resetSelection();
        state.phase = "error";
        state.loadError = failed;
        render();
        throw failed;
      });
    }

    function setProject(projectId) {
      assertLive("setProject");
      if (projectId != null && typeof projectId !== "string") {
        return Promise.reject(failure("input_invalid", "projectId must be a string when present"));
      }
      var pid = projectId == null ? "" : projectId;
      if (!pid) {
        generation += 1;
        actionToken += 1;
        resetToEmpty();
        render();
        return Promise.resolve(getState());
      }
      if (pid === state.projectId && (state.phase === "ready" || state.phase === "loading")) {
        render();
        return Promise.resolve(getState());
      }
      return load({ projectId: pid });
    }

    function applySnapshot(snapshot) {
      if (!alive || !mounted) return;
      var pid = snapshot && typeof snapshot.projectId === "string" ? snapshot.projectId : "";
      if (!pid && !context && deps.projectId) pid = String(deps.projectId);
      if (pid === state.projectId) return;
      if (!pid) {
        generation += 1;
        actionToken += 1;
        resetToEmpty();
        render();
        return;
      }
      load({ projectId: pid }).catch(noop);
    }

    // ---- values + validation ------------------------------------------

    function setValue(name, value) {
      assertLive("setValue");
      var field = fieldFor(String(name));
      if (!field) {
        var unknown = failure("ui_value_unknown_field",
          "the form does not declare field " + JSON.stringify(String(name)), { path: name });
        state.fieldErrors = {};
        state.fieldErrors[name] = [{
          code: "ui_value_unknown_field",
          path: name,
          message: unknown.detail
        }];
        state.problems = [{
          code: "ui_value_unknown_field",
          path: String(name),
          message: unknown.detail
        }];
        state.validation = { ok: false, code: "ui_value_unknown_field" };
        state.actionError = unknown;
        render();
        throw unknown;
      }
      if (!field.binding) {
        var unbound = failure("ui_binding_missing",
          "field " + field.name + " carries no TaskRequest binding", { path: field.name });
        state.fieldErrors = {};
        state.fieldErrors[field.name] = [{
          code: "ui_binding_missing",
          path: field.name,
          message: unbound.detail
        }];
        state.problems = [{
          code: "ui_binding_missing",
          path: field.name,
          message: unbound.detail
        }];
        state.validation = { ok: false, code: "ui_binding_missing" };
        state.actionError = unbound;
        render();
        throw unbound;
      }
      state.values[field.name] = value;
      state.valuesRevision += 1;
      state.fieldErrors = {};
      state.problems = [];
      state.validation = { ok: false, code: null };
      state.actionError = null;
      if (state.submitState === "unconfirmed" || state.submitState === "error" ||
          state.submitState === "conflict") {
        state.submitState = "idle";
        state.submitError = null;
        state.submitResult = null;
      }
      render();
      return getState();
    }

    function mapProblems(problems) {
      var map = {};
      (problems || []).forEach(function (problem) {
        var path = String(problem.path == null ? "" : problem.path);
        var name = path.split(".")[0].replace(/\[.*$/, "");
        if (!name) name = "_form";
        (map[name] = map[name] || []).push({
          code: problem.code,
          path: problem.path,
          message: problem.message
        });
      });
      return map;
    }

    /** Validate values through the shared UI-subset validator.
     *
     * No argument validates the current values and records the errors on the
     * instance (so the next render maps them by field). An explicit values
     * object is a dry run: it still reports unknown field names
     * (`ui_value_unknown_field`) but does not mutate instance state. */
    function validate(values) {
      var subset = uiSubset();
      var entry = state.selected;
      if (!subset) {
        return {
          ok: false,
          code: "ui_subset_unavailable",
          problems: [{ code: "ui_subset_unavailable", path: null, message: "" }],
          errors: { _form: [{ code: "ui_subset_unavailable", path: null, message: "" }] },
          values: {}
        };
      }
      if (!entry || entry.state !== "available") {
        return {
          ok: false,
          code: "ui_form_missing",
          problems: [{ code: "ui_form_missing", path: null, message: "" }],
          errors: { _form: [{ code: "ui_form_missing", path: null, message: "" }] },
          values: {}
        };
      }
      var explicit = arguments.length > 0;
      var input = explicit && isObject(values) ? values : state.values;
      var result = subset.validateValues(entry.form, input);
      var mapped = mapProblems(result.problems);
      if (!explicit) {
        state.problems = result.problems.slice();
        state.fieldErrors = mapped;
        state.validation = { ok: result.ok, code: result.code };
      }
      return {
        ok: result.ok,
        code: result.code,
        problems: copy(result.problems),
        errors: explicit ? mapped : copy(state.fieldErrors),
        values: copy(result.values)
      };
    }

    function buildRequest() {
      assertLive("buildRequest");
      var subset = uiSubset();
      var entry = state.selected;
      if (!subset) {
        throw failure("ui_subset_unavailable",
          "ACPWorkbench.contracts.uiSubset is not loaded");
      }
      if (!entry || entry.state !== "available") {
        throw failure("ui_form_missing", "no active capability form is selected");
      }
      var check = validate();
      if (!check.ok) {
        var first = check.problems[0] || {
          code: "ui_form_invalid", path: null, message: "invalid form values"
        };
        var invalid = failure(first.code || "ui_form_invalid",
          first.message || "invalid form values",
          { problems: check.problems, path: first.path });
        state.actionError = invalid;
        render();
        throw invalid;
      }
      var built = subset.buildFormOutcome(entry.form, state.values);
      if (!built.ok) {
        var firstBuilt = built.problems[0] || {
          code: "ui_form_invalid", path: null, message: "invalid form values"
        };
        var builtError = failure(firstBuilt.code || "ui_form_invalid",
          firstBuilt.message || "invalid form values",
          { problems: built.problems, path: firstBuilt.path });
        state.actionError = builtError;
        render();
        throw builtError;
      }
      state.actionError = null;
      return built;
    }

    // ---- preflight -----------------------------------------------------

    /** Send ONE preflight request for an already built/validated body.
     *
     * `options.resetSubmit` (default true) is the normal public preflight
     * behaviour: a new preflight discards the previous submit receipt. The 409
     * conflict refresh passes `false`, so it can renew the plan digest without
     * erasing the visible `conflict` state that waits for re-review. */
    function requestPreflight(projectId, built, options) {
      options = options || {};
      var client = scienceClient();
      if (!client || typeof client.preflight !== "function") {
        var missing = failure("platform_transport_required",
          "preflight requires an injected science client (ACPClient.science.preflight)");
        state.preflightState = "error";
        state.preflightError = missing;
        render();
        return Promise.reject(missing);
      }
      var gen = generation;
      var token = ++actionToken;
      var revision = state.valuesRevision;
      var capabilityId = capabilityIdOf(state.selected);
      state.preflightState = "loading";
      state.preflightError = null;
      state.preflight = null;
      if (options.resetSubmit !== false) {
        state.idempotencyKey = null;
        state.submitState = "idle";
        state.submitError = null;
        state.submitResult = null;
        state.taskRef = null;
      }
      render();
      return Promise.resolve().then(function () {
        return client.preflight(built.body, { projectId: projectId }, { generation: String(gen) });
      }).then(function (projection) {
        if (!alive || gen !== generation || token !== actionToken) {
          throw failure("stale_result", "a superseded preflight result was discarded");
        }
        state.preflightState = "ok";
        state.preflight = {
          projection: isObject(projection) ? projection : {},
          valuesRevision: revision,
          capabilityId: capabilityId,
          projectId: projectId,
          body: copy(built.body),
          fields: copy(built.fields)
        };
        state.preflightError = null;
        state.idempotencyKey = newIdempotencyKey();
        render();
        return viewModel();
      }, function (error) {
        if (!alive || gen !== generation || token !== actionToken) {
          throw failure("stale_result", "a superseded preflight failure was discarded");
        }
        var failed = typed(error, "preflight_failed");
        state.preflightState = "error";
        state.preflightError = failed;
        state.preflight = null;
        render();
        throw failed;
      });
    }

    /** A-60 / 409: re-read the preflight for the CURRENT values after the server
     * refused the submission as a revision/approval conflict. The approval is
     * already cleared by the caller, so a fresh approved record is required
     * (re-review); this helper never touches the submit state and never re-sends
     * the failed submission. A refresh failure leaves the explicit preflight
     * error visible and keeps submit blocked. */
    function refreshAfterConflict(projectId) {
      var built;
      try {
        built = buildRequest();
      } catch (error) {
        state.preflightState = "error";
        state.preflightError = typed(error, "ui_form_invalid");
        render();
        return Promise.resolve(getState());
      }
      return requestPreflight(projectId, built, { resetSubmit: false }).then(function () {
        return getState();
      }, function () {
        return getState();
      });
    }

    function preflight(scope, opts) {
      assertLive("preflight");
      opts = opts || {};
      if (isBusy()) {
        // A second preflight would supersede the in-flight submit token and leave
        // the busy state stuck; refuse locally with the rendered button guard.
        return Promise.reject(failure("preflight_busy",
          "a preflight or submit is already running on this instance"));
      }
      var projectId = resolveProjectId(scope);
      if (!projectId) {
        var noProject = failure("input_invalid", "projectId is required for preflight");
        state.preflightState = "error";
        state.preflightError = noProject;
        render();
        return Promise.reject(noProject);
      }
      if (state.projectId && projectId !== state.projectId) {
        var mismatch = failure("input_invalid",
          "preflight project " + projectId + " differs from the loaded forms project " + state.projectId);
        state.preflightState = "error";
        state.preflightError = mismatch;
        render();
        return Promise.reject(mismatch);
      }
      var built;
      try {
        built = buildRequest();
      } catch (error) {
        state.actionError = null;
        state.preflightState = "error";
        state.preflightError = typed(error, "ui_form_invalid");
        render();
        return Promise.reject(state.preflightError);
      }
      return requestPreflight(projectId, built, { resetSubmit: true });
    }

    // ---- submit --------------------------------------------------------

    function realTaskRef(result) {
      if (!isObject(result)) return null;
      var ref = isObject(result.task_ref) ? result.task_ref : {};
      var jobId = typeof result.job_id === "string" && result.job_id
        ? result.job_id
        : (typeof ref.job_id === "string" && ref.job_id ? ref.job_id
          : (typeof ref.task_id === "string" && ref.task_id ? ref.task_id : ""));
      var attempt = result.attempt != null ? result.attempt : ref.attempt;
      if (!jobId) return null;
      if (typeof attempt !== "number" || !isFinite(attempt) ||
          Math.floor(attempt) !== attempt || attempt < 1) {
        return null;
      }
      return { jobId: jobId, attempt: attempt };
    }

    /** A-60: readable, stable submit error mapping.
     *
     * The raw transport/domain code is kept on the thrown error for programmatic
     * callers; the UI gets one of six stable `displayCode` values. HTTP status is
     * authoritative when present (a 500 carrying `input_invalid` is still an
     * unknown outcome, never a definite rejection), and a code-only failure is
     * classified by its vocabulary. Anything that is not a definite 4xx stays
     * `unconfirmed`: an unknown outcome is never success. */
    var SUBMIT_ACCESS_CODES = {
      access_denied: true,
      forbidden: true,
      unauthorized: true,
      permission_denied: true,
      entry_access_denied: true
    };
    var SUBMIT_NOT_FOUND_CODES = {
      not_found: true,
      resource_not_found: true,
      project_not_found: true,
      missing: true
    };
    var SUBMIT_CONFLICT_CODES = {
      revision_conflict: true,
      approval_conflict: true,
      conflict: true,
      plan_digest_mismatch: true,
      approved_plan_digest_mismatch: true,
      approved_proposal_mismatch: true,
      approved_proposal_digest_mismatch: true,
      approved_budget_mismatch: true
    };
    var SUBMIT_VALIDATION_CODES = {
      validation_failed: true,
      validation_error: true,
      input_validation_failed: true,
      invalid_value: true,
      invalid_request: true
    };

    function submitErrorStatus(error) {
      if (!error) return null;
      if (typeof error.httpStatus === "number") return error.httpStatus;
      if (typeof error.status === "number") return error.status;
      return null;
    }

    function mapSubmitFailure(error) {
      var status = submitErrorStatus(error);
      var code = error && typeof error.code === "string" ? error.code : "";
      if (typeof status === "number") {
        if (status === 400) {
          return { state: "error", code: "input_invalid", category: "input", status: status };
        }
        if (status === 401 || status === 403) {
          return { state: "error", code: "access_denied", category: "access", status: status };
        }
        if (status === 404) {
          return { state: "error", code: "not_found", category: "not_found", status: status };
        }
        if (status === 409) {
          return { state: "conflict", code: "revision_conflict", category: "conflict", status: status };
        }
        if (status === 422) {
          return { state: "error", code: "validation_failed", category: "validation", status: status };
        }
        if (status >= 500 && status <= 599) {
          return { state: "unconfirmed", code: "submit_unconfirmed", category: "network", status: status };
        }
        if (status >= 400 && status < 500) {
          return { state: "error", code: code || "request_failed", category: "http", status: status };
        }
      }
      if (code && hasOwn(SUBMIT_ACCESS_CODES, code)) {
        return { state: "error", code: "access_denied", category: "access", status: status };
      }
      if (code && hasOwn(SUBMIT_NOT_FOUND_CODES, code)) {
        return { state: "error", code: "not_found", category: "not_found", status: status };
      }
      if (code && hasOwn(SUBMIT_CONFLICT_CODES, code)) {
        return { state: "conflict", code: "revision_conflict", category: "conflict", status: status };
      }
      if (code && hasOwn(SUBMIT_VALIDATION_CODES, code)) {
        return { state: "error", code: "validation_failed", category: "validation", status: status };
      }
      if (code === "input_invalid") {
        return { state: "error", code: "input_invalid", category: "input", status: status };
      }
      if (code && (code.indexOf("ui_") === 0 || code.indexOf("approval") === 0 ||
          code.indexOf("approved_") === 0 || code === "preflight_required" ||
          code === "preflight_stale")) {
        // A local refusal that somehow reached the command client keeps its own
        // code; it is definite, but it is not one of the HTTP-mapped buckets.
        return { state: "error", code: code, category: "local", status: status };
      }
      return { state: "unconfirmed", code: "submit_unconfirmed", category: "network", status: status };
    }

    /** Attach the stable UI projection to a raw submit failure. */
    function decorateSubmitFailure(error, mapped) {
      var info = mapped || mapSubmitFailure(error);
      error.submitState = info.state;
      error.category = info.category;
      error.displayCode = info.code;
      if (typeof info.status === "number") {
        error.status = info.status;
        error.httpStatus = info.status;
      }
      return error;
    }

    function submitDisplayCode() {
      var error = state.submitError;
      if (!error) return "";
      return typeof error.displayCode === "string" && error.displayCode
        ? error.displayCode
        : (typeof error.code === "string" ? error.code : "");
    }

    function submitErrorLine() {
      var error = state.submitError;
      if (!error) return "";
      var code = submitDisplayCode() || "request_failed";
      var message = messageText(error);
      var friendly = errorText(code, message);
      if (message && message !== friendly && message !== String(code)) {
        return friendly + "：" + message;
      }
      return friendly;
    }

    function submit(scope, opts) {
      assertLive("submit");
      opts = opts || {};
      if (isBusy()) {
        // Never clobber the in-flight operation's state: refuse locally, and let
        // the rendered disabled button remain the visible guard.
        return Promise.reject(failure("submit_busy",
          "a preflight or submit is already running on this instance"));
      }
      var commands = commandsClient();
      if (!commands) {
        var missing = failure("platform_transport_required",
          "submit requires an injected research command client " +
          "(ACPClient.researchCommands.submit)");
        state.submitState = "error";
        state.submitError = missing;
        render();
        return Promise.reject(missing);
      }
      var projectId = resolveProjectId(scope);
      if (!projectId) {
        var noProject = failure("input_invalid", "projectId is required for submit");
        state.submitState = "error";
        state.submitError = noProject;
        render();
        return Promise.reject(noProject);
      }
      if (state.projectId && projectId !== state.projectId) {
        var mismatch = failure("input_invalid",
          "submit project " + projectId + " differs from the loaded forms project " + state.projectId);
        state.submitState = "error";
        state.submitError = mismatch;
        render();
        return Promise.reject(mismatch);
      }
      var built;
      try {
        built = buildRequest();
      } catch (error) {
        state.actionError = null;
        state.submitState = "error";
        state.submitError = typed(error, "ui_form_invalid");
        render();
        return Promise.reject(state.submitError);
      }
      if (state.preflightState !== "ok" || !state.preflight) {
        var noPreflight = failure("preflight_required", "run preflight before submitting");
        state.submitState = "error";
        state.submitError = noPreflight;
        render();
        return Promise.reject(noPreflight);
      }
      if (preflightStale() || state.preflight.projectId !== projectId) {
        var stale = failure("preflight_stale",
          "the preflight no longer matches the current values or project");
        state.submitState = "error";
        state.submitError = stale;
        render();
        return Promise.reject(stale);
      }
      var record = opts.approval || state.approval;
      if (!record) {
        var approvalRequired = failure("approval_required",
          "an approved record must be bound before submitting");
        state.submitState = "error";
        state.submitError = approvalRequired;
        render();
        return Promise.reject(approvalRequired);
      }
      if (record.status !== "approved") {
        var notGranted = failure("approval_not_granted",
          "the bound approval status is " + JSON.stringify(String(record.status)));
        state.submitState = "error";
        state.submitError = notGranted;
        render();
        return Promise.reject(notGranted);
      }
      var planDigest = preflightPlanDigest();
      var boundDigest = typeof record.plan_digest === "string" ? record.plan_digest
        : (typeof record.planDigest === "string" ? record.planDigest : null);
      if (planDigest && planDigest !== boundDigest) {
        var mismatchApproval = failure("approved_plan_digest_mismatch",
          "the approved plan digest differs from the current preflight plan");
        state.submitState = "error";
        state.submitError = mismatchApproval;
        render();
        return Promise.reject(mismatchApproval);
      }
      var payload = copy(built.body);
      if (planDigest) payload.plan_digest = planDigest;
      var gen = generation;
      var token = ++actionToken;
      state.submitState = "submitting";
      state.submitError = null;
      state.submitResult = null;
      state.taskRef = null;
      render();
      return Promise.resolve().then(function () {
        return commands.submit({ projectId: projectId }, payload, {
          approval: record,
          idempotencyKey: opts.idempotencyKey || state.idempotencyKey || newIdempotencyKey(),
          timeoutMs: opts.timeoutMs,
          fetchImpl: opts.fetchImpl
        });
      }).then(function (result) {
        if (!alive || gen !== generation || token !== actionToken) {
          throw failure("stale_result", "a superseded submit result was discarded");
        }
        var taskRef = realTaskRef(result);
        if (!taskRef) {
          var unconfirmed = failure("submit_unconfirmed",
            "the submit client returned no job_id + attempt TaskRef");
          state.submitState = "unconfirmed";
          state.submitError = unconfirmed;
          state.submitResult = copy(result);
          state.taskRef = null;
          render();
          throw unconfirmed;
        }
        state.submitState = "submitted";
        state.submitError = null;
        state.submitResult = copy(result);
        state.taskRef = taskRef;
        render();
        return copy(result);
      }, function (error) {
        if (!alive || gen !== generation || token !== actionToken) {
          throw failure("stale_result", "a superseded submit failure was discarded");
        }
        var failed = decorateSubmitFailure(typed(error, "submit_unconfirmed"));
        state.submitState = failed.submitState;
        state.submitError = failed;
        state.submitResult = null;
        state.taskRef = null;
        if (failed.category === "conflict") {
          // A-60 / 409: the plan or approval moved on the server. Drop the stale
          // binding and refresh the preflight so the digest shown is current; a
          // NEW approved record must be bound (re-review) before another submit.
          // The failed submission is never replayed automatically.
          state.approval = null;
          state.idempotencyKey = null;
          state.preflight = null;
          state.preflightState = "idle";
          state.preflightError = null;
          render();
          return refreshAfterConflict(projectId).then(function () {
            throw failed;
          });
        }
        render();
        throw failed;
      });
    }

    function approval(record) {
      assertLive("approval");
      if (record == null) {
        state.approval = null;
        if (state.submitState === "error" || state.submitState === "unconfirmed" ||
            state.submitState === "conflict") {
          state.submitState = "idle";
          state.submitError = null;
          state.submitResult = null;
        }
        render();
        return getState();
      }
      if (!isObject(record)) {
        var invalid = failure("approval_card_invalid", "an approval record must be an object");
        state.actionError = invalid;
        render();
        throw invalid;
      }
      state.approval = copy(record);
      if (state.submitState === "error" || state.submitState === "unconfirmed" ||
          state.submitState === "conflict") {
        state.submitState = "idle";
        state.submitError = null;
        state.submitResult = null;
      }
      render();
      return getState();
    }

    function newIdempotencyKey() {
      keySequence += 1;
      var capability = capabilityIdOf(state.selected) || "form";
      return "capform-" + capability + "-" + state.valuesRevision + "-" + keySequence;
    }

    // ---- view model + state projection ---------------------------------

    function approvalRef(record) {
      if (!record) return "—";
      var ref = record.approval_ref != null ? record.approval_ref : record.approvalRef;
      return ref == null || ref === "" ? "—" : String(ref);
    }

    function viewModel() {
      var entry = state.selected || {};
      var pre = state.preflight || {};
      var projection = pre.projection || {};
      var plan = isObject(projection.plan) ? projection.plan : {};
      var form = entry.form || {};
      return {
        schema: VIEW_SCHEMA,
        capability_id: entry.capability_id || null,
        form_digest: entry.form_digest || (typeof form.form_digest === "string" ? form.form_digest : null),
        descriptor_digest: entry.descriptor_digest || null,
        subset_version: state.document ? state.document.subset_version : null,
        subset_digest: state.document ? state.document.subset_digest : null,
        plan_digest: plan.plan_digest || (typeof projection.plan_digest === "string"
          ? projection.plan_digest : null),
        preflight_fingerprint: typeof projection.preflight_fingerprint === "string"
          ? projection.preflight_fingerprint : null,
        estimate: copy(projection.estimate || {}),
        values_revision: pre.valuesRevision == null ? null : pre.valuesRevision,
        request: {
          workflow: form.request && typeof form.request.workflow === "string"
            ? form.request.workflow : null,
          body: copy(pre.body || {}),
          fields: copy(pre.fields || [])
        }
      };
    }

    function submitBlockReason() {
      if (isBusy()) return "busy";
      if (state.submitState === "conflict") return "conflict";
      if (state.phase !== "ready" || !state.selected || state.selected.state !== "available") {
        return "form_unavailable";
      }
      if (currentPreflightState() !== "ok") return "preflight";
      if (approvalStatus() !== "granted") return "approval";
      return "";
    }

    function canPreflight() {
      return !isBusy() && state.phase === "ready" && !!state.selected &&
        state.selected.state === "available";
    }

    function getState() {
      var pre = state.preflight;
      var projection = pre ? pre.projection || {} : {};
      var plan = isObject(projection.plan) ? projection.plan : {};
      return {
        phase: state.phase,
        empty_reason: state.emptyReason,
        project_id: state.projectId,
        generation: generation,
        capability: capabilityIdOf(state.selected) || null,
        selected: capabilityIdOf(state.selected) || null,
        selected_state: state.selected ? state.selected.state : null,
        entries: state.entries.map(function (entry) {
          return {
            capability_id: entry.capability_id,
            state: entry.state,
            error_code: entry.error ? entry.error.code : null,
            form_digest: entry.form_digest,
            descriptor_digest: entry.descriptor_digest
          };
        }),
        fields: copy(state.fields),
        values: copy(state.values),
        values_revision: state.valuesRevision,
        validation: copy(state.validation),
        problems: copy(state.problems),
        errors: copy(state.fieldErrors),
        approval_status: approvalStatus(),
        approval: copy(state.approval),
        preflight_state: currentPreflightState(),
        preflight: pre ? {
          plan_digest: plan.plan_digest || (typeof projection.plan_digest === "string"
            ? projection.plan_digest : null),
          preflight_fingerprint: typeof projection.preflight_fingerprint === "string"
            ? projection.preflight_fingerprint : null,
          estimate: copy(projection.estimate || {}),
          values_revision: pre.valuesRevision,
          project_id: pre.projectId,
          form_digest: state.selected ? state.selected.form_digest : null,
          descriptor_digest: state.selected ? state.selected.descriptor_digest : null
        } : null,
        view_model: pre ? viewModel() : null,
        submit_state: state.submitState,
        submit_error: state.submitError ? {
          code: state.submitError.code,
          display_code: state.submitError.displayCode || state.submitError.code || null,
          category: state.submitError.category || "local",
          status: typeof state.submitError.httpStatus === "number"
            ? state.submitError.httpStatus : null,
          message: messageText(state.submitError)
        } : null,
        submit_blocked: submitBlockReason(),
        submit_result: copy(state.submitResult),
        task_ref: copy(state.taskRef),
        busy: isBusy(),
        error: state.phase === "error" && state.loadError ? {
          code: state.loadError.code,
          message: messageText(state.loadError)
        } : (state.actionError ? {
          code: state.actionError.code,
          message: messageText(state.actionError)
        } : null),
        subset_version: state.document ? state.document.subset_version : null,
        subset_digest: state.document ? state.document.subset_digest : null
      };
    }

    // ---- rendering (createElement + textContent only) ------------------

    function node(tag, className, text) {
      var el = doc.createElement(tag);
      if (className) el.className = className;
      if (text != null) el.textContent = String(text);
      return el;
    }

    function i18nNode(tag, className, key, vars) {
      var el = node(tag, className, translate(key, vars));
      el.setAttribute("data-i18n-key", key);
      if (!vars) el.setAttribute("data-i18n", key);
      return el;
    }

    function append(parent, child) {
      parent.appendChild(child);
      return child;
    }

    function clearChildren(target) {
      if (!target) return;
      if (typeof target.replaceChildren === "function") {
        target.replaceChildren();
        return;
      }
      if (target.children && typeof target.removeChild === "function") {
        while (target.children.length) target.removeChild(target.children[0]);
        return;
      }
      target.textContent = "";
    }

    function renderNotice(parent, tone, code, key, vars) {
      var box = append(parent, node("div", "acp-capf-notice is-" + tone));
      box.setAttribute("data-code", String(code || ""));
      box.setAttribute("data-acp-notice", tone);
      append(box, i18nNode("span", "acp-capf-notice-text", key, vars));
      return box;
    }

    function digestRow(parent, label, value) {
      append(parent, node("dt", null, label));
      append(parent, node("dd", null, value == null || value === "" ? "—" : String(value)));
    }

    function renderStateBox(parent) {
      var box = append(parent, node("div", "acp-capf-state"));
      box.setAttribute("data-acp-state", state.phase);
      if (state.phase === "loading") {
        append(box, node("span", "acp-capf-spinner", ""));
        append(box, i18nNode("p", "acp-capf-state-msg", "next.capabilityForm.state.loading"));
        return;
      }
      if (state.phase === "error") {
        append(box, i18nNode("p", "acp-capf-state-title", "next.capabilityForm.state.error"));
        append(box, node("p", "acp-capf-state-msg mono", errorLine(state.loadError)));
        var retry = i18nNode("button", "acp-capf-btn", "next.capabilityForm.action.retry");
        retry.type = "button";
        retry.setAttribute("data-acp-action", "retry");
        retry.disabled = !state.projectId;
        retry.addEventListener("click", function () {
          load({ projectId: state.projectId }).catch(noop);
        });
        append(box, retry);
        return;
      }
      var key = state.emptyReason === "no_forms"
        ? "next.capabilityForm.state.no_forms"
        : "next.capabilityForm.state.empty";
      append(box, i18nNode("p", "acp-capf-state-msg", key));
    }

    function renderPicker(parent) {
      if (!state.entries.length) return;
      var row = append(parent, node("div", "acp-capf-picker"));
      var label = i18nNode("label", "acp-capf-label", "next.capabilityForm.capability");
      label.setAttribute("for", "acp-capf-capability");
      append(row, label);
      var selectEl = node("select", "acp-capf-select");
      selectEl.id = "acp-capf-capability";
      selectEl.setAttribute("data-acp-capability", "");
      state.entries.forEach(function (entry) {
        var option = node("option", null, null);
        option.value = entry.capability_id;
        var text = entry.capability_id;
        if (entry.state === "read_only") {
          text += " · " + translate("next.capabilityForm.tag.read_only");
        } else if (entry.state !== "available") {
          text += " · " + translate("next.capabilityForm.tag.unavailable");
        }
        option.textContent = text;
        if (entry.state !== "available") option.disabled = entry.state !== "read_only";
        if (state.selected && state.selected.capability_id === entry.capability_id) {
          option.selected = true;
        }
        selectEl.appendChild(option);
      });
      selectEl.addEventListener("change", function (event) {
        var value = event && event.target && event.target.value != null
          ? event.target.value : selectEl.value;
        try {
          select(value);
        } catch (error) {
          state.actionError = typed(error, "ui_form_missing");
          render();
        }
      });
      append(row, selectEl);
    }

    function renderReadOnly(parent, entry) {
      renderNotice(parent, "info", "ui_form_read_only", "next.capabilityForm.read_only");
      var dl = append(parent, node("dl", "acp-capf-digests"));
      digestRow(dl, translate("next.capabilityForm.capability"), entry.capability_id);
      digestRow(dl, translate("next.capabilityForm.version"), entry.version);
      digestRow(dl, translate("next.capabilityForm.descriptor_digest"), entry.descriptor_digest);
      digestRow(dl, translate("next.capabilityForm.reason"), entry.reason || "capability_not_active");
    }

    function renderUnavailable(parent, entry) {
      var error = entry.error || {};
      var notice = renderNotice(parent, "error", error.code || "ui_form_missing",
        "next.capabilityForm.unavailable",
        { reason: errorText(error.code, error.message || error.code) });
      if (error.params && isObject(error.params)) {
        var list = append(parent, node("ul", "acp-capf-params"));
        Object.keys(error.params).forEach(function (key) {
          append(list, node("li", null, key + "=" + String(error.params[key])));
        });
      }
      return notice;
    }

    function renderNotices(parent) {
      var status = approvalStatus();
      var key;
      var tone;
      var vars = null;
      if (status === "granted") {
        key = "next.capabilityForm.approval.granted";
        tone = "info";
        vars = { ref: approvalRef(state.approval) };
      } else if (status === "mismatch") {
        key = "next.capabilityForm.approval.mismatch";
        tone = "warn";
      } else if (status === "not_approved") {
        key = "next.capabilityForm.approval.not_approved";
        tone = "warn";
        vars = { status: state.approval.status == null ? "unknown" : String(state.approval.status) };
      } else {
        key = "next.capabilityForm.approval.required";
        tone = "warn";
      }
      var notice = renderNotice(parent, tone,
        status === "granted" ? "approval_granted"
          : (status === "mismatch" ? "approval_mismatch"
            : (status === "not_approved" ? "approval_not_granted" : "approval_required")),
        key, vars);
      notice.setAttribute("data-acp-approval", status);
      if (currentPreflightState() === "stale") {
        var stale = renderNotice(parent, "warn", "preflight_stale",
          "next.capabilityForm.preflight.stale");
        stale.setAttribute("data-acp-preflight", "stale");
      }
      if (isBusy()) {
        renderNotice(parent, "info", "inputs_locked", "next.capabilityForm.locked");
      }
    }

    function enumValue(field, raw) {
      if (Array.isArray(field.enum)) {
        for (var i = 0; i < field.enum.length; i += 1) {
          if (String(field.enum[i]) === String(raw)) return field.enum[i];
        }
      }
      return raw;
    }

    function numberValue(field, input) {
      if (input.validity && input.validity.badInput) return "NaN";
      var raw = String(input.value == null ? "" : input.value).trim();
      if (raw === "") return "";
      var numeric = Number(raw);
      if (isFinite(numeric) &&
          (field.schema_type !== "integer" || Math.floor(numeric) === numeric)) {
        return numeric;
      }
      return raw;
    }

    function commitField(field, value) {
      try {
        setValue(field.name, value);
      } catch (error) {
        state.actionError = typed(error, "ui_value_unknown_field");
        render();
      }
    }

    function buildControl(field, inputId, invalid) {
      var current = state.values[field.name];
      var control;
      if (Array.isArray(field.enum)) {
        control = node("select", "acp-capf-input acp-capf-select");
        var blank = node("option", null, translate("next.capabilityForm.choose"));
        blank.value = "";
        append(control, blank);
        field.enum.forEach(function (item) {
          var option = node("option", null, String(item));
          option.value = String(item);
          append(control, option);
        });
        control.value = current === undefined || current === null ? "" : String(current);
        control.addEventListener("change", function () {
          commitField(field, enumValue(field, control.value));
        });
      } else if (field.widget === "checkbox") {
        control = node("input", "acp-capf-check");
        control.setAttribute("type", "checkbox");
        control.checked = current === true;
        control.addEventListener("change", function () {
          commitField(field, control.checked === true);
        });
      } else if (field.type === "number") {
        control = node("input", "acp-capf-input");
        control.setAttribute("type", "number");
        control.setAttribute("step", field.schema_type === "integer" ? "1" : "any");
        if (field.minimum !== null) control.setAttribute("min", String(field.minimum));
        if (field.maximum !== null) control.setAttribute("max", String(field.maximum));
        if (current !== undefined && current !== null) control.value = String(current);
        control.addEventListener("change", function () {
          commitField(field, numberValue(field, control));
        });
      } else if (field.widget === "textarea") {
        control = node("textarea", "acp-capf-input acp-capf-textarea");
        control.value = current === undefined || current === null ? "" : String(current);
        control.addEventListener("change", function () {
          commitField(field, control.value);
        });
      } else {
        control = node("input", "acp-capf-input");
        control.setAttribute("type", "text");
        if (current !== undefined && current !== null) control.value = String(current);
        control.addEventListener("change", function () {
          commitField(field, control.value);
        });
      }
      control.id = inputId;
      control.setAttribute("data-acp-input", field.name);
      if (field.binding) control.setAttribute("data-acp-binding", field.binding);
      if (invalid) control.setAttribute("aria-invalid", "true");
      control.disabled = isBusy();
      return control;
    }

    function renderField(parent, field) {
      var row = append(parent, node("div", "acp-capf-field"));
      row.setAttribute("data-acp-field", field.name);
      if (field.binding) row.setAttribute("data-acp-binding", field.binding);
      var errors = state.fieldErrors[field.name] || [];
      var inputId = "acp-capf-field-" + field.name;
      var label = node("label", "acp-capf-label", field.title);
      label.setAttribute("for", inputId);
      if (field.required) {
        label.appendChild(i18nNode("span", "acp-capf-required", "next.capabilityForm.required"));
      }
      append(row, label);
      append(row, buildControl(field, inputId, errors.length > 0));
      if (field.unit) {
        var unit = translate("next.capabilityForm.unit." + field.unit, null);
        if (unit === "next.capabilityForm.unit." + field.unit) unit = String(field.unit);
        append(row, node("span", "acp-capf-unit",
          field.unit_quantity ? unit + " (" + field.unit_quantity + ")" : unit));
      }
      if (field.description) append(row, node("p", "acp-capf-field-hint", field.description));
      var errorSlot = node("p", "acp-capf-field-error",
        errors.length ? errorText(errors[0].code, errors[0].message) : "");
      errorSlot.setAttribute("data-acp-field-error", field.name);
      if (errors.length) {
        errorSlot.setAttribute("data-acp-code", String(errors[0].code || ""));
        if (errors[0].message) errorSlot.setAttribute("title", String(errors[0].message));
      } else {
        errorSlot.hidden = true;
      }
      append(row, errorSlot);
      return row;
    }

    function renderActions(parent) {
      var bar = append(parent, node("div", "acp-capf-actions"));
      var preflightKey = state.preflightState === "loading"
        ? "next.capabilityForm.action.preflighting"
        : "next.capabilityForm.action.preflight";
      var preflightBtn = i18nNode("button", "acp-capf-btn", preflightKey);
      preflightBtn.type = "button";
      preflightBtn.setAttribute("data-acp-action", "preflight");
      preflightBtn.disabled = !canPreflight();
      preflightBtn.addEventListener("click", function () {
        preflight().catch(noop);
      });
      append(bar, preflightBtn);

      var submitKey = state.submitState === "submitting"
        ? "next.capabilityForm.action.submitting"
        : "next.capabilityForm.action.submit";
      var submitBtn = i18nNode("button", "acp-capf-btn acp-capf-btn-primary", submitKey);
      submitBtn.type = "button";
      submitBtn.setAttribute("data-acp-action", "submit");
      var blocked = submitBlockReason();
      submitBtn.disabled = !!blocked;
      if (blocked) submitBtn.setAttribute("data-acp-submit-blocked", blocked);
      else submitBtn.removeAttribute("data-acp-submit-blocked");
      submitBtn.addEventListener("click", function () {
        submit().catch(noop);
      });
      append(bar, submitBtn);
    }

    function renderPreflightResult(parent) {
      var box = append(parent, node("div", "acp-capf-result acp-capf-preflight"));
      var current = currentPreflightState();
      box.setAttribute("data-acp-preflight", current);
      var key = "next.capabilityForm.preflight.idle";
      if (current === "loading") key = "next.capabilityForm.preflight.loading";
      else if (current === "ok") key = "next.capabilityForm.preflight.ok";
      else if (current === "stale") key = "next.capabilityForm.preflight.stale";
      else if (current === "error") key = "next.capabilityForm.preflight.error";
      append(box, i18nNode("p", "acp-capf-result-title", key));
      if (current === "error") {
        var error = node("p", "acp-capf-result-error", errorLine(state.preflightError));
        error.setAttribute("data-acp-error-code",
          state.preflightError && state.preflightError.code ? String(state.preflightError.code) : "");
        append(box, error);
        return;
      }
      if (current !== "ok" && current !== "stale") return;
      var projection = state.preflight ? state.preflight.projection || {} : {};
      var plan = isObject(projection.plan) ? projection.plan : {};
      var entry = state.selected || {};
      var form = entry.form || {};
      var dl = append(box, node("dl", "acp-capf-digests"));
      digestRow(dl, translate("next.capabilityForm.plan_digest"),
        plan.plan_digest || projection.plan_digest);
      digestRow(dl, translate("next.capabilityForm.preflight_fingerprint"),
        projection.preflight_fingerprint);
      digestRow(dl, translate("next.capabilityForm.form_digest"),
        entry.form_digest || form.form_digest);
      digestRow(dl, translate("next.capabilityForm.descriptor_digest"), entry.descriptor_digest);
      var feasible = isObject(projection.estimate) ? projection.estimate.feasible : undefined;
      var feasibleText = feasible === false
        ? translate("next.capabilityForm.preflight.infeasible")
        : translate("next.capabilityForm.preflight.feasible");
      append(box, node("p", "acp-capf-result-note", feasibleText));
    }

    function renderSubmitResult(parent) {
      var box = append(parent, node("div", "acp-capf-result acp-capf-submit"));
      // `data-acp-submit` is the A-59 hook kept for compatibility; A-60's
      // stable machine hook is `data-acp-submit-state` (same vocabulary).
      box.setAttribute("data-acp-submit", state.submitState);
      box.setAttribute("data-acp-submit-state", state.submitState);
      var errorState = state.submitState === "unconfirmed" || state.submitState === "error" ||
        state.submitState === "conflict";
      if (errorState && submitDisplayCode()) {
        box.setAttribute("data-acp-error-code", submitDisplayCode());
        if (state.submitError && state.submitError.category) {
          box.setAttribute("data-acp-error-category", String(state.submitError.category));
        }
      }
      var key = "next.capabilityForm.submit.idle";
      if (state.submitState === "submitting") key = "next.capabilityForm.submit.submitting";
      else if (state.submitState === "submitted") key = "next.capabilityForm.submit.ok";
      else if (state.submitState === "unconfirmed") key = "next.capabilityForm.submit.unconfirmed_title";
      else if (state.submitState === "conflict") key = "next.capabilityForm.submit.conflict_title";
      else if (state.submitState === "error") key = "next.capabilityForm.submit.error_title";
      append(box, i18nNode("p", "acp-capf-result-title", key));
      if (state.submitState === "submitted" && state.taskRef) {
        var receipt = node("p", "acp-capf-receipt",
          translate("next.capabilityForm.submit.submitted", {
            jobId: state.taskRef.jobId,
            attempt: state.taskRef.attempt
          }));
        receipt.setAttribute("data-acp-job-id", state.taskRef.jobId);
        receipt.setAttribute("data-acp-attempt", String(state.taskRef.attempt));
        append(box, receipt);
        var result = state.submitResult || {};
        if (typeof result.status === "string" && result.status) {
          append(box, node("p", "acp-capf-result-note", "status: " + result.status));
        }
        if (typeof result.plan_digest === "string" && result.plan_digest) {
          var dl = append(box, node("dl", "acp-capf-digests"));
          digestRow(dl, translate("next.capabilityForm.plan_digest"), result.plan_digest);
        }
        return;
      }
      if (errorState) {
        var error = node("p", "acp-capf-result-error", submitErrorLine());
        error.setAttribute("data-acp-error-code", submitDisplayCode());
        if (state.submitError && state.submitError.category) {
          error.setAttribute("data-acp-error-category", String(state.submitError.category));
        }
        append(box, error);
        if (state.submitState === "conflict") {
          var conflict = append(box, i18nNode("p", "acp-capf-result-note",
            "next.capabilityForm.submit.conflict"));
          conflict.setAttribute("data-acp-submit-hint", "conflict");
          append(box, i18nNode("p", "acp-capf-result-note",
            "next.capabilityForm.submit.conflict_hint"));
        }
      }
    }

    function renderFeedback(parent) {
      var box = append(parent, node("div", "acp-capf-feedback"));
      if (state.problems && state.problems.length) {
        append(box, node("p", "acp-capf-feedback-title",
          translate("next.capabilityForm.problems", { count: state.problems.length })));
        var list = append(box, node("ul", "acp-capf-problems"));
        list.setAttribute("data-acp-problems", String(state.problems.length));
        state.problems.forEach(function (problem) {
          var item = node("li", "acp-capf-problem", errorLine(problem));
          item.setAttribute("data-acp-code", String(problem.code || ""));
          append(list, item);
        });
      }
      if (state.actionError) {
        var actionBox = append(box, node("div", "acp-capf-notice is-error"));
        actionBox.setAttribute("data-acp-action-error",
          String(state.actionError.code || ""));
        append(actionBox, node("span", "acp-capf-notice-text", errorLine(state.actionError)));
      }
      renderPreflightResult(box);
      renderSubmitResult(box);
    }

    function renderReady(parent) {
      var body = append(parent, node("div", "acp-capf-body"));
      renderPicker(body);
      var entry = state.selected;
      if (!entry) {
        append(body, i18nNode("p", "acp-capf-state-msg",
          "next.capabilityForm.state.no_forms"));
        return;
      }
      if (entry.state === "read_only") {
        renderReadOnly(body, entry);
        return;
      }
      if (entry.state !== "available") {
        renderUnavailable(body, entry);
        return;
      }
      if (entry.form && entry.form.title) {
        append(body, node("h4", "acp-capf-form-title", entry.form.title));
      }
      if (entry.form && entry.form.description) {
        append(body, node("p", "acp-capf-form-desc", entry.form.description));
      }
      renderNotices(body);
      var form = append(body, node("div", "acp-capf-form"));
      form.setAttribute("data-acp-form", entry.capability_id);
      state.fields.forEach(function (field) {
        renderField(form, field);
      });
      renderActions(body);
      renderFeedback(body);
    }

    function render() {
      if (!alive || !root || !doc) return;
      if (suspended) return;
      root.setAttribute("data-acp-module-state", state.phase);
      root.setAttribute("data-acp-generation", String(generation));
      // Stable A-60 hook: always present while mounted, even before a submit.
      root.setAttribute("data-acp-submit-state", state.submitState);
      if (state.projectId) root.setAttribute("data-acp-project", state.projectId);
      else root.removeAttribute("data-acp-project");
      if (state.phase === "error" && state.loadError) {
        root.setAttribute("data-acp-error-code", String(state.loadError.code || ""));
      } else if (state.phase !== "error" && state.submitState !== "idle" &&
          state.submitState !== "submitting" && state.submitState !== "submitted" &&
          submitDisplayCode()) {
        // A readable submit failure is also visible at the module root, so an
        // automation hook does not have to hunt for the result box.
        root.setAttribute("data-acp-error-code", submitDisplayCode());
      } else {
        root.removeAttribute("data-acp-error-code");
      }
      if (state.phase !== "error" && state.submitState !== "idle" &&
          state.submitState !== "submitting" && state.submitState !== "submitted" &&
          submitDisplayCode()) {
        root.setAttribute("data-acp-submit-error-code", submitDisplayCode());
      } else {
        root.removeAttribute("data-acp-submit-error-code");
      }
      clearChildren(root);
      if (state.phase !== "ready") {
        renderStateBox(root);
        return;
      }
      renderReady(root);
    }

    // ---- lifecycle -----------------------------------------------------

    function mount(hostEl, initial) {
      if (mounted) {
        return Promise.reject(new Error("capability-form: already mounted (dispose() first)"));
      }
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("capability-form mount: host must be a DOM element"));
      }
      doc = hostEl.ownerDocument ||
        (typeof document !== "undefined" ? document : null);
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new Error("capability-form mount: a document is required"));
      }
      alive = true;
      mounted = true;
      host = hostEl;
      root = node("div", "acp-capf");
      root.setAttribute("data-acp-module", MODULE_ID);
      root.setAttribute("role", "region");
      root.setAttribute("data-i18n-aria", "shell.module.capability_form");
      root.setAttribute("aria-label", translate("shell.module.capability_form"));
      host.appendChild(root);
      if (context && typeof context.subscribe === "function") {
        unsubscribeContext = context.subscribe(function (snapshot) {
          applySnapshot(snapshot);
        });
      }
      var snapshot = initial && typeof initial.projectId === "string" ? initial : null;
      if (!snapshot && context && typeof context.getSnapshot === "function") {
        snapshot = context.getSnapshot();
      }
      var projectId = snapshot && typeof snapshot.projectId === "string" ? snapshot.projectId : "";
      if (!projectId && !context && deps.projectId) projectId = String(deps.projectId);
      if (projectId) {
        load({ projectId: projectId }).catch(noop);
      } else {
        resetToEmpty();
        render();
      }
      return Promise.resolve();
    }

    function updateContext(snapshot) {
      applySnapshot(snapshot || null);
    }

    function suspend() {
      suspended = true;
    }

    function resume() {
      if (!alive) return;
      suspended = false;
      render();
    }

    function dispose() {
      if (!alive && !mounted && !root) return;
      alive = false;
      mounted = false;
      suspended = false;
      generation += 1;
      actionToken += 1;
      if (unsubscribeContext) {
        try {
          unsubscribeContext();
        } catch (error) {
          /* the context may already be gone; disposal must still complete */
        }
        unsubscribeContext = null;
      }
      if (root && root.parentNode && typeof root.parentNode.removeChild === "function") {
        try {
          root.parentNode.removeChild(root);
        } catch (error) {
          /* already detached */
        }
      }
      root = null;
      host = null;
      doc = null;
      resetToEmpty();
      state.approval = null;
      state.submitState = "idle";
      state.submitError = null;
      state.submitResult = null;
      state.taskRef = null;
      state.idempotencyKey = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      setProject: setProject,
      reload: function () {
        assertLive("reload");
        return load({ projectId: state.projectId });
      },
      load: load,
      select: select,
      setValue: setValue,
      validate: validate,
      buildRequest: buildRequest,
      preflight: preflight,
      submit: submit,
      approval: approval,
      render: render,
      getState: getState,
      suspend: suspend,
      resume: resume,
      dispose: dispose
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: {
      id: MODULE_ID,
      interfaceVersion: 1,
      version: "0.2.0",
      provides: ["research.capability-form"],
      binds: ["projectId"],
      deps: [],
      capabilities: { requires: [], provides: ["research.capability-form"] },
      hosts: ["acp-web"],
      supportedInstancing: "single",
      configSchemaVersion: 0,
      error: {
        titleKey: "next.capabilityForm.errorTitle",
        messageKey: "next.capabilityForm.errorMessage"
      }
    },
    factory: function (hostApi) {
      return createInstance(hostApi || {});
    }
  };
})();
