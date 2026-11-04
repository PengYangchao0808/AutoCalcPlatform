/*
 * ACP Next — approval-card (A-57 / P22): pending research approval card.
 *
 * Read + decide surface for ONE persistent research approval bound to a frozen
 * scientific plan (B16 / M2-03a).  The card is a projection of the server
 * record and the three decisions are separate commands:
 *
 *   GET  /api/v2/research/approvals?status=pending[&project_id][&proposal_ref]
 *   GET  /api/v2/research/approvals/{approval_ref}
 *   POST /api/v2/research/approvals/{approval_ref}/approve
 *   POST /api/v2/research/approvals/{approval_ref}/reject
 *   POST /api/v2/research/approvals/{approval_ref}/revoke
 *
 * Hard rules (pinned by acp2_approval_card.test.js):
 *   - every displayed value comes from the server record; the module never
 *     computes a budget, a plan/proposal digest or an approval identity;
 *   - the decision body is ONLY { expected_revision, reason? } — the approver
 *     principal comes from the authenticated Controller channel, never from
 *     this payload (the server refuses approver fields with 403);
 *   - an approval is intent, not execution: this module never writes task
 *     state, never submits a task and never calls a task API endpoint;
 *   - one selection at a time, guarded by a generation/token pair: a late
 *     response (dispose, selection switch, superseded decision) never paints;
 *   - no direct fetch: the transport is injected (`deps.fetch`) or resolved
 *     from `deps.client.transport.request`; when neither exists the panel is
 *     in the explicit error state and makes no network call.
 *
 * Classic browser script: no build, no ESM, no globals besides ACPNextModules.
 */
(function () {
  "use strict";

  var MODULE_ID = "approval-card";
  var API = "/api/v2/research/approvals";

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "1.0.0",
    provides: ["research.approval"],
    binds: ["researchRef"],
    capabilities: { requires: [], provides: ["research.approval"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.approval.errorTitle", messageKey: "next.approval.errorMessage" },
  };

  var ZH = {
    "next.approval.title": "研究审批",
    "next.approval.empty": "暂无待审批的研究审批。",
    "next.approval.empty_hint": "审批记录来自服务端；批准、拒绝、撤销都不会写执行状态或提交任务。",
    "next.approval.loading": "正在读取审批记录…",
    "next.approval.error": "审批记录加载失败：{message}",
    "next.approval.retry": "重试",
    "next.approval.refresh": "刷新",
    "next.approval.approve": "批准",
    "next.approval.reject": "拒绝",
    "next.approval.revoke": "撤销",
    "next.approval.reason_label": "审批意见（可选）",
    "next.approval.reason_placeholder": "填写审批意见，留空则不带原因",
    "next.approval.pending_count": "待审批 {count} 条",
    "next.approval.notice.approved": "已批准：版本 {revision}。",
    "next.approval.notice.rejected": "已拒绝：版本 {revision}。",
    "next.approval.notice.revoked": "已撤销：版本 {revision}。",
    "next.approval.notice.conflict": "审批已在服务端变更：已刷新到版本 {revision}，请复核后再决定。",
    "next.approval.notice.conflict_unknown": "审批已在服务端变更：已刷新，请复核后再决定。",
    "next.approval.notice.access": "当前身份无权执行该审批操作（服务端拒绝）。",
    "next.approval.notice.not_found": "审批记录不存在或当前身份不可见。",
    "next.approval.notice.no_transport": "未配置请求通道：无法读取或决定审批。",
    "next.approval.notice.action_failed": "审批操作失败：{message}",
    "next.approval.notice.invalid_record": "服务端返回的审批记录缺少审批单号或版本，已拒绝操作。",
    "next.approval.status.draft": "草稿",
    "next.approval.status.pending": "待批准",
    "next.approval.status.approved": "已批准",
    "next.approval.status.rejected": "已拒绝",
    "next.approval.status.expired": "已过期",
    "next.approval.status.revoked": "已撤销",
    "next.approval.binding.full": "科学计划 + 执行计划",
    "next.approval.binding.execution-only": "仅执行计划",
    "next.approval.binding.execution_only": "仅执行计划",
    "next.approval.label.approval_ref": "审批单号",
    "next.approval.label.proposal_ref": "提案",
    "next.approval.label.project_id": "项目",
    "next.approval.label.status": "状态",
    "next.approval.label.revision": "版本",
    "next.approval.label.requester": "申请人",
    "next.approval.label.approver": "审批人",
    "next.approval.label.expires": "到期",
    "next.approval.label.requested": "申请时间",
    "next.approval.label.decided": "决定时间",
    "next.approval.label.binding": "绑定范围",
    "next.approval.label.plan_digest": "计划摘要",
    "next.approval.label.science_digest": "科学摘要",
    "next.approval.label.proposal_digest": "提案摘要",
    "next.approval.label.budget_digest": "预算摘要",
    "next.approval.label.reason": "决定原因",
    "next.approval.label.budget": "预算（服务端值）",
    "next.approval.label.plan": "执行计划摘要",
    "next.approval.label.science": "科学计划摘要",
    "next.approval.label.recipe_digest": "配方摘要",
    "next.approval.label.change_range": "允许变更范围",
    "next.approval.label.task_ref": "已绑定任务（证据）",
    "next.approval.label.science_budget": "科学计划预算（服务端值）",
    "next.approval.budget_empty": "服务端未提供预算。",
    "next.approval.plan_empty": "服务端未提供执行计划正文。",
    "next.approval.science_empty": "该审批仅绑定执行计划（无科学摘要）。",
    "next.approval.expired": "（已过期）",
    "next.approval.errorTitle": "研究审批不可用",
    "next.approval.errorMessage": "approval-card 模块加载失败，请查看控制台。",
    "next.approval.budget.model_requests": "模型请求",
    "next.approval.budget.input_tokens": "输入 tokens",
    "next.approval.budget.output_tokens": "输出 tokens",
    "next.approval.budget.task_refs": "任务引用",
    "next.approval.budget.recipe_steps": "配方步骤",
    "next.approval.budget.qc_invocations": "QC 调用",
    "next.approval.budget.wall_seconds": "墙钟秒",
    "next.approval.budget.queue_seconds": "排队秒",
    "next.approval.budget.unit_basis": "计费基准",
    "next.approval.budget.notes": "预算备注",
    "next.approval.budget.unit": "单位",
    "next.approval.budget.count": "数量",
    "next.approval.budget.timeout_s": "超时（秒）",
    "next.approval.plan.workflow": "工作流",
    "next.approval.plan.input": "输入",
    "next.approval.plan.method": "方法",
    "next.approval.plan.resources": "资源",
    "next.approval.plan.steps": "步骤",
    "next.approval.science.method": "方法",
    "next.approval.science.mapping": "反应映射",
    "next.approval.science.charge": "电荷",
    "next.approval.science.multiplicity": "自旋多重度",
    "next.approval.science.capability_steps": "能力步骤",
    "next.approval.science.initial_inputs": "初始输入",
    "next.approval.science.allowed_substitutions": "允许替换",
  };

  var ACTION_NOTICE = {
    approve: "next.approval.notice.approved",
    reject: "next.approval.notice.rejected",
    revoke: "next.approval.notice.revoked",
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
    } catch (e) {
      return "[unserializable]";
    }
  }

  function formatTime(value) {
    if (!value) return "—";
    try {
      var date = new Date(String(value));
      if (isNaN(date.getTime())) return String(value);
      return date.toLocaleString();
    } catch (e) {
      return String(value);
    }
  }

  function statusOf(record) {
    if (!record) return "";
    return String(record.status || record.state || "").toLowerCase();
  }

  function recordRef(record) {
    if (!record || typeof record !== "object") return "";
    return typeof record.approval_ref === "string" && record.approval_ref ? record.approval_ref : "";
  }

  function unwrapApproval(payload) {
    if (!payload || typeof payload !== "object") return null;
    if (payload.approval && typeof payload.approval === "object") return payload.approval;
    return payload;
  }

  function errorInfo(error) {
    var info = { status: null, code: "", rule: "", message: "" };
    if (!error) { info.message = "unknown error"; return info; }
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
    return info.code ? info.code + ": " + text : text;
  }

  function createApprovalCard(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var clock = typeof deps.now === "function" ? deps.now : function () { return Date.now(); };

    function transport() {
      if (typeof deps.fetch === "function") return deps.fetch;
      var client = deps.client || (typeof window !== "undefined" ? window.ACPClient : null);
      if (client && client.transport && typeof client.transport.request === "function") {
        return function (path, opts) { return client.transport.request(path, opts); };
      }
      return null;
    }

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      context: null,
      snapshot: null,
      key: "",
      projectId: "",
      proposalRef: "",
      ref: "",
      record: null,
      pending: [],
      loading: false,
      busy: false,
      notice: null,
      errorText: "",
      readToken: 0,
      actionToken: 0,
      pendingEpoch: 0,
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

    // ── lifecycle guards ────────────────────────────────────────────────

    function isReadCurrent(gen, token) {
      return !state.disposed && state.mounted &&
        state.generation === gen && state.readToken === token;
    }

    function isActionCurrent(gen, token) {
      return !state.disposed && state.mounted &&
        state.generation === gen && state.actionToken === token;
    }

    // ── DOM ─────────────────────────────────────────────────────────────

    function buildDom() {
      var root = el(doc, "div", "acp-ac");
      root.setAttribute("data-acp-module-state", "empty");
      root.setAttribute("data-module", MODULE_ID);

      var head = el(doc, "header", "acp-ac-head");
      var title = i18nEl(doc, "h3", "acp-ac-title", "next.approval.title");
      var badge = el(doc, "span", "acp-ac-badge", "");
      badge.hidden = true;
      var sub = el(doc, "p", "acp-ac-sub", "");
      head.appendChild(title);
      head.appendChild(badge);
      head.appendChild(sub);
      root.appendChild(head);

      var body = el(doc, "div", "acp-ac-body");
      root.appendChild(body);

      var stateNode = el(doc, "p", "acp-ac-state", "");
      stateNode.hidden = true;
      stateNode.setAttribute("aria-live", "polite");
      body.appendChild(stateNode);

      var emptyHint = el(doc, "p", "acp-ac-note", "");
      emptyHint.hidden = true;
      body.appendChild(emptyHint);

      var retry = i18nEl(doc, "button", "acp-btn acp-btn--ghost acp-ac-retry", "next.approval.retry");
      retry.type = "button";
      retry.setAttribute("data-action", "retry");
      retry.hidden = true;
      body.appendChild(retry);
      on(retry, "click", function () {
        if (state.busy || state.loading) return;
        startLoad();
      });

      var card = el(doc, "section", "acp-ac-card");
      card.hidden = true;
      body.appendChild(card);

      var grid = el(doc, "dl", "acp-ac-grid acp-ac-meta");
      card.appendChild(grid);

      var budgetSection = el(doc, "section", "acp-ac-section");
      budgetSection.appendChild(i18nEl(doc, "h4", "acp-ac-section-title", "next.approval.label.budget"));
      var budgetList = el(doc, "dl", "acp-ac-grid");
      var budgetNote = el(doc, "p", "acp-ac-note", "");
      budgetNote.hidden = true;
      budgetSection.appendChild(budgetList);
      budgetSection.appendChild(budgetNote);
      card.appendChild(budgetSection);

      var planSection = el(doc, "section", "acp-ac-section");
      planSection.appendChild(i18nEl(doc, "h4", "acp-ac-section-title", "next.approval.label.plan"));
      var planList = el(doc, "dl", "acp-ac-grid");
      var planNote = el(doc, "p", "acp-ac-note", "");
      planNote.hidden = true;
      planSection.appendChild(planList);
      planSection.appendChild(planNote);
      card.appendChild(planSection);

      var scienceSection = el(doc, "section", "acp-ac-section");
      scienceSection.appendChild(i18nEl(doc, "h4", "acp-ac-section-title", "next.approval.label.science"));
      var scienceList = el(doc, "dl", "acp-ac-grid");
      var scienceNote = el(doc, "p", "acp-ac-note", "");
      scienceNote.hidden = true;
      scienceSection.appendChild(scienceList);
      scienceSection.appendChild(scienceNote);
      card.appendChild(scienceSection);

      var scienceBudgetSection = el(doc, "section", "acp-ac-section");
      scienceBudgetSection.appendChild(i18nEl(doc, "h4", "acp-ac-section-title", "next.approval.label.science_budget"));
      var scienceBudgetList = el(doc, "dl", "acp-ac-grid");
      scienceBudgetSection.appendChild(scienceBudgetList);
      card.appendChild(scienceBudgetSection);

      var reasonLabel = i18nEl(doc, "label", "acp-ac-reason-label", "next.approval.reason_label");
      reasonLabel.setAttribute("for", "acp-ac-reason");
      var reason = el(doc, "input", "acp-ac-reason");
      reason.type = "text";
      reason.setAttribute("id", "acp-ac-reason");
      reason.setAttribute("data-acp-role", "reason");
      reason.setAttribute("data-i18n-ph", "next.approval.reason_placeholder");
      reason.setAttribute("placeholder", t("next.approval.reason_placeholder"));
      card.appendChild(reasonLabel);
      card.appendChild(reason);

      var notice = el(doc, "p", "acp-ac-notice", "");
      notice.hidden = true;
      notice.setAttribute("role", "alert");
      card.appendChild(notice);

      var actions = el(doc, "div", "acp-ac-actions");
      var approve = i18nEl(doc, "button", "acp-btn acp-btn--primary acp-ac-approve", "next.approval.approve");
      approve.type = "button";
      approve.setAttribute("data-action", "approve");
      var reject = i18nEl(doc, "button", "acp-btn acp-ac-reject", "next.approval.reject");
      reject.type = "button";
      reject.setAttribute("data-action", "reject");
      var revoke = i18nEl(doc, "button", "acp-btn acp-ac-revoke", "next.approval.revoke");
      revoke.type = "button";
      revoke.setAttribute("data-action", "revoke");
      var refresh = i18nEl(doc, "button", "acp-btn acp-btn--ghost", "next.approval.refresh");
      refresh.type = "button";
      refresh.setAttribute("data-action", "refresh");
      actions.appendChild(approve);
      actions.appendChild(reject);
      actions.appendChild(revoke);
      actions.appendChild(refresh);
      card.appendChild(actions);

      var listSection = el(doc, "section", "acp-ac-pending");
      listSection.hidden = true;
      var listTitle = el(doc, "p", "acp-ac-pending-title", "");
      var list = el(doc, "div", "acp-ac-pending-list");
      listSection.appendChild(listTitle);
      listSection.appendChild(list);
      body.appendChild(listSection);

      on(approve, "click", function () { decide("approve"); });
      on(reject, "click", function () { decide("reject"); });
      on(revoke, "click", function () { decide("revoke"); });
      on(refresh, "click", function () {
        if (state.busy || state.loading) return;
        startLoad();
      });
      on(list, "click", function (event) {
        var node = event && event.target;
        while (node && node !== list) {
          if (node.getAttribute) {
            var ref = node.getAttribute("data-approval-ref");
            if (ref) { selectPending(ref); return; }
          }
          node = node.parentNode;
        }
      });

      state.host.replaceChildren(root);
      state.ui = {
        root: root,
        title: title,
        badge: badge,
        sub: sub,
        stateNode: stateNode,
        emptyHint: emptyHint,
        retry: retry,
        card: card,
        grid: grid,
        budgetSection: budgetSection,
        budgetList: budgetList,
        budgetNote: budgetNote,
        planSection: planSection,
        planList: planList,
        planNote: planNote,
        scienceSection: scienceSection,
        scienceList: scienceList,
        scienceNote: scienceNote,
        scienceBudgetSection: scienceBudgetSection,
        scienceBudgetList: scienceBudgetList,
        reason: reason,
        notice: notice,
        actions: actions,
        approve: approve,
        reject: reject,
        revoke: revoke,
        refresh: refresh,
        listSection: listSection,
        listTitle: listTitle,
        list: list,
      };
    }

    function reset() {
      var ui = state.ui;
      if (!ui || !ui.root) return;
      ui.grid.replaceChildren();
      ui.budgetList.replaceChildren();
      ui.planList.replaceChildren();
      ui.scienceList.replaceChildren();
      ui.scienceBudgetList.replaceChildren();
      ui.budgetNote.hidden = true;
      ui.budgetNote.textContent = "";
      ui.planNote.hidden = true;
      ui.planNote.textContent = "";
      ui.scienceNote.hidden = true;
      ui.scienceNote.textContent = "";
      ui.budgetSection.hidden = true;
      ui.planSection.hidden = true;
      ui.scienceSection.hidden = true;
      ui.scienceBudgetSection.hidden = true;
      ui.card.hidden = true;
      ui.listSection.hidden = true;
      ui.list.replaceChildren();
      ui.notice.hidden = true;
      ui.notice.textContent = "";
      ui.notice.removeAttribute("data-kind");
      ui.retry.hidden = true;
      ui.stateNode.hidden = true;
      ui.stateNode.textContent = "";
      ui.emptyHint.hidden = true;
      ui.emptyHint.textContent = "";
      ui.badge.hidden = true;
      ui.badge.textContent = "";
      ui.badge.removeAttribute("data-status");
      ui.sub.textContent = "";
      if (ui.reason) {
        ui.reason.value = "";
        ui.reason.disabled = false;
      }
      ui.actions.setAttribute("data-busy", "false");
    }

    function setState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function rowInto(listEl, field, labelText, labelKey, value, opts) {
      opts = opts || {};
      var dt = el(doc, "dt", "", labelText);
      if (labelKey) dt.setAttribute("data-i18n", labelKey);
      var text = opts.text != null ? opts.text : displayValue(value);
      var dd = el(doc, "dd", opts.mono ? "acp-ac-mono" : "", text);
      dd.setAttribute("data-field", field);
      var valueText = opts.valueText;
      if (valueText == null && value != null &&
          (typeof value === "string" || typeof value === "number" || typeof value === "boolean")) {
        valueText = value;
      }
      if (valueText != null) dd.setAttribute("data-value", String(valueText));
      if (opts.title && value != null) dd.setAttribute("title", String(value));
      listEl.appendChild(dt);
      listEl.appendChild(dd);
      return dd;
    }

    function labelOr(key, fallback) {
      return hasOwn(ZH, key) ? t(key) : fallback;
    }

    function statusLabel(status) {
      var key = "next.approval.status." + String(status || "").toLowerCase();
      return hasOwn(ZH, key) ? t(key) : String(status || "");
    }

    function bindingLabel(record) {
      var binding = String(record && record.binding_status ? record.binding_status : "").toLowerCase();
      if (!binding) binding = record && record.science_digest ? "full" : "execution-only";
      var key = "next.approval.binding." + binding;
      return hasOwn(ZH, key) ? t(key) : binding;
    }

    function budgetLabel(key) {
      var i18nKey = "next.approval.budget." + key;
      return hasOwn(ZH, i18nKey) ? t(i18nKey) : String(key);
    }

    function planLabel(key) {
      var i18nKey = "next.approval.plan." + key;
      return hasOwn(ZH, i18nKey) ? t(i18nKey) : String(key);
    }

    function scienceLabel(key) {
      var i18nKey = "next.approval.science." + key;
      return hasOwn(ZH, i18nKey) ? t(i18nKey) : String(key);
    }

    function isExpired(record) {
      if (!record || !record.expires_at) return false;
      var ts = new Date(String(record.expires_at)).getTime();
      if (isNaN(ts)) return false;
      return clock() > ts;
    }

    function expiryText(record) {
      if (!record || !record.expires_at) return "—";
      var text = formatTime(record.expires_at);
      return isExpired(record) ? text + t("next.approval.expired") : text;
    }

    // ── render ──────────────────────────────────────────────────────────

    function renderEmpty() {
      reset();
      state.errorText = "";
      setState("empty");
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = t("next.approval.empty");
      ui.emptyHint.hidden = false;
      ui.emptyHint.textContent = t("next.approval.empty_hint");
      applyI18n(ui.root);
    }

    function renderLoading() {
      reset();
      state.errorText = "";
      setState("loading");
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = t("next.approval.loading");
      applyI18n(ui.root);
    }

    function renderError(message) {
      reset();
      state.record = null;
      state.errorText = String(message || t("next.approval.error", { message: "unknown" }));
      setState("error");
      var ui = state.ui;
      ui.stateNode.hidden = false;
      ui.stateNode.textContent = state.errorText;
      ui.retry.hidden = false;
      applyActions();
      applyI18n(ui.root);
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

    function renderBudget(record) {
      var ui = state.ui;
      ui.budgetSection.hidden = false;
      var budget = record && record.budget;
      if (!budget || typeof budget !== "object" || Array.isArray(budget) || !Object.keys(budget).length) {
        ui.budgetNote.hidden = false;
        ui.budgetNote.textContent = t("next.approval.budget_empty");
        return;
      }
      Object.keys(budget).sort().forEach(function (key) {
        var i18nKey = "next.approval.budget." + key;
        rowInto(ui.budgetList, "budget." + key, budgetLabel(key),
          hasOwn(ZH, i18nKey) ? i18nKey : null, budget[key]);
      });
    }

    function renderPlan(record) {
      var ui = state.ui;
      ui.planSection.hidden = false;
      var plan = record && record.plan_definition;
      if (!plan || typeof plan !== "object" || Array.isArray(plan) || !Object.keys(plan).length) {
        ui.planNote.hidden = false;
        ui.planNote.textContent = t("next.approval.plan_empty");
        return;
      }
      Object.keys(plan).sort().forEach(function (key) {
        var i18nKey = "next.approval.plan." + key;
        rowInto(ui.planList, "plan_definition." + key, planLabel(key),
          hasOwn(ZH, i18nKey) ? i18nKey : null, plan[key]);
      });
    }

    function renderScience(record) {
      var ui = state.ui;
      ui.scienceSection.hidden = false;
      var projection = record && record.science_projection;
      var hasProjection = !!projection && typeof projection === "object" &&
        !Array.isArray(projection) && Object.keys(projection).length > 0;
      if (!record || (!record.science_digest && !hasProjection)) {
        ui.scienceNote.hidden = false;
        ui.scienceNote.textContent = t("next.approval.science_empty");
        ui.scienceBudgetSection.hidden = true;
        return;
      }
      if (hasProjection && projection.recipe_digest) {
        rowInto(ui.scienceList, "science.recipe_digest", t("next.approval.label.recipe_digest"),
          "next.approval.label.recipe_digest", projection.recipe_digest, {
            mono: true,
            title: true,
          });
      }
      var science = hasProjection && projection.science && typeof projection.science === "object"
        ? projection.science : null;
      if (science) {
        ["method", "mapping", "charge", "multiplicity"].forEach(function (key) {
          if (!hasOwn(science, key)) return;
          var i18nKey = "next.approval.science." + key;
          rowInto(ui.scienceList, "science." + key, scienceLabel(key),
            hasOwn(ZH, i18nKey) ? i18nKey : null, science[key]);
        });
        ["capability_steps", "initial_inputs", "allowed_substitutions"].forEach(function (key) {
          if (!hasOwn(science, key)) return;
          var i18nKey = "next.approval.science." + key;
          var value = science[key];
          var text = Array.isArray(value) ? String(value.length) + " 项" : displayValue(value);
          var valueText = Array.isArray(value) ? value.length : null;
          rowInto(ui.scienceList, "science." + key, scienceLabel(key),
            hasOwn(ZH, i18nKey) ? i18nKey : null, value, { text: text, valueText: valueText });
        });
        Object.keys(science).sort().forEach(function (key) {
          if (["method", "mapping", "charge", "multiplicity", "capability_steps",
            "initial_inputs", "allowed_substitutions"].indexOf(key) !== -1) return;
          var i18nKey = "next.approval.science." + key;
          rowInto(ui.scienceList, "science." + key, scienceLabel(key),
            hasOwn(ZH, i18nKey) ? i18nKey : null, science[key]);
        });
      }
      var scienceBudget = hasProjection && projection.budget && typeof projection.budget === "object"
        ? projection.budget : null;
      if (scienceBudget && Object.keys(scienceBudget).length) {
        ui.scienceBudgetSection.hidden = false;
        Object.keys(scienceBudget).sort().forEach(function (key) {
          var i18nKey = "next.approval.budget." + key;
          rowInto(ui.scienceBudgetList, "science_budget." + key, budgetLabel(key),
            hasOwn(ZH, i18nKey) ? i18nKey : null, scienceBudget[key]);
        });
      } else {
        ui.scienceBudgetSection.hidden = true;
      }
    }

    function actionDisabled(action) {
      if (!transport()) return true;
      if (state.busy || state.loading) return true;
      var status = statusOf(state.record);
      if (action === "approve" || action === "reject") return status !== "pending";
      if (action === "revoke") return status !== "approved";
      return false;
    }

    function applyActions() {
      var ui = state.ui;
      if (!ui.actions) return;
      ui.approve.disabled = actionDisabled("approve");
      ui.reject.disabled = actionDisabled("reject");
      ui.revoke.disabled = actionDisabled("revoke");
      ui.refresh.disabled = state.busy || state.loading || !transport();
      ui.actions.setAttribute("data-busy", state.busy ? "true" : "false");
      if (ui.reason) ui.reason.disabled = state.busy;
    }

    function renderPending() {
      var ui = state.ui;
      if (!ui.list) return;
      ui.list.replaceChildren();
      if (!state.pending || state.pending.length < 2) {
        ui.listSection.hidden = true;
        return;
      }
      ui.listSection.hidden = false;
      ui.listTitle.textContent = t("next.approval.pending_count", { count: state.pending.length });
      var currentRef = recordRef(state.record);
      state.pending.forEach(function (record) {
        var ref = recordRef(record);
        if (!ref) return;
        var button = el(doc, "button", "acp-btn acp-btn--ghost acp-ac-pending-item", ref);
        button.type = "button";
        button.setAttribute("data-approval-ref", String(ref));
        button.setAttribute("data-active", String(currentRef === ref));
        ui.list.appendChild(button);
      });
    }

    function renderReady(record) {
      if (!record || !recordRef(record)) {
        state.record = null;
        renderError(t("next.approval.notice.invalid_record"));
        return;
      }
      state.record = record;
      state.ref = recordRef(record) || state.ref;
      state.errorText = "";
      reset();
      setState("ready");
      var ui = state.ui;
      var status = statusOf(record);
      ui.badge.hidden = false;
      ui.badge.textContent = statusLabel(status);
      ui.badge.setAttribute("data-status", status || "unknown");
      ui.sub.textContent = [String(record.proposal_ref || ""),
        t("next.approval.label.revision") + " " + String(record.revision == null ? "—" : record.revision)]
        .filter(function (part) { return !!part; }).join(" · ");
      ui.card.hidden = false;

      rowInto(ui.grid, "approval_ref", t("next.approval.label.approval_ref"),
        "next.approval.label.approval_ref", recordRef(record), { mono: true });
      rowInto(ui.grid, "proposal_ref", t("next.approval.label.proposal_ref"),
        "next.approval.label.proposal_ref", record.proposal_ref, { mono: true });
      rowInto(ui.grid, "project_id", t("next.approval.label.project_id"),
        "next.approval.label.project_id", record.project_id, { mono: true });
      rowInto(ui.grid, "status", t("next.approval.label.status"),
        "next.approval.label.status", status, { text: statusLabel(status), valueText: status });
      rowInto(ui.grid, "revision", t("next.approval.label.revision"),
        "next.approval.label.revision", record.revision);
      rowInto(ui.grid, "requester_ref", t("next.approval.label.requester"),
        "next.approval.label.requester", record.requester_ref, { mono: true });
      if (record.approver_ref) {
        rowInto(ui.grid, "approver_ref", t("next.approval.label.approver"),
          "next.approval.label.approver", record.approver_ref, { mono: true });
      }
      rowInto(ui.grid, "expires_at", t("next.approval.label.expires"),
        "next.approval.label.expires", record.expires_at, {
          text: expiryText(record),
          valueText: record.expires_at || "",
        });
      rowInto(ui.grid, "requested_at", t("next.approval.label.requested"),
        "next.approval.label.requested", record.requested_at || record.created_at, {
          text: formatTime(record.requested_at || record.created_at),
          valueText: record.requested_at || record.created_at || "",
        });
      if (record.decided_at) {
        rowInto(ui.grid, "decided_at", t("next.approval.label.decided"),
          "next.approval.label.decided", record.decided_at, {
            text: formatTime(record.decided_at),
            valueText: record.decided_at,
          });
      }
      rowInto(ui.grid, "binding_status", t("next.approval.label.binding"),
        "next.approval.label.binding", record.binding_status || "", {
          text: bindingLabel(record),
          valueText: record.binding_status || "",
        });
      rowInto(ui.grid, "plan_digest", t("next.approval.label.plan_digest"),
        "next.approval.label.plan_digest", record.plan_digest, { mono: true, title: true });
      if (record.science_digest) {
        rowInto(ui.grid, "science_digest", t("next.approval.label.science_digest"),
          "next.approval.label.science_digest", record.science_digest, { mono: true, title: true });
      }
      if (record.proposal_digest) {
        rowInto(ui.grid, "proposal_digest", t("next.approval.label.proposal_digest"),
          "next.approval.label.proposal_digest", record.proposal_digest, { mono: true, title: true });
      }
      if (record.budget_digest) {
        rowInto(ui.grid, "budget_digest", t("next.approval.label.budget_digest"),
          "next.approval.label.budget_digest", record.budget_digest, { mono: true, title: true });
      }
      if (record.allowed_change_range && typeof record.allowed_change_range === "object" &&
          Object.keys(record.allowed_change_range).length) {
        rowInto(ui.grid, "allowed_change_range", t("next.approval.label.change_range"),
          "next.approval.label.change_range", record.allowed_change_range);
      }
      if (record.reason) {
        rowInto(ui.grid, "reason", t("next.approval.label.reason"),
          "next.approval.label.reason", record.reason);
      }
      if (record.task_ref && typeof record.task_ref === "object") {
        var taskRef = record.task_ref;
        var taskText = [taskRef.job_id || taskRef.task_id || "",
          taskRef.attempt == null ? "" : "#" + String(taskRef.attempt)]
          .filter(function (part) { return !!part; }).join("");
        if (taskText) {
          rowInto(ui.grid, "task_ref", t("next.approval.label.task_ref"),
            "next.approval.label.task_ref", taskText, { mono: true });
        }
      }

      renderBudget(record);
      renderPlan(record);
      renderScience(record);
      renderNotice();
      applyActions();
      renderPending();
      applyI18n(ui.root);
    }

    function rerender() {
      if (state.disposed || !state.mounted) return;
      if (state.record) renderReady(state.record);
      else if (state.loading) renderLoading();
      else if (state.errorText) renderError(state.errorText);
      else renderEmpty();
    }

    // ── requests ────────────────────────────────────────────────────────

    function pendingPath() {
      var path = API + "?status=pending";
      if (state.projectId) path += "&project_id=" + encodeURIComponent(state.projectId);
      if (state.proposalRef) path += "&proposal_ref=" + encodeURIComponent(state.proposalRef);
      return path;
    }

    function readRecord(ref) {
      var fetchFn = transport();
      if (!fetchFn) return Promise.reject(missingTransport());
      return Promise.resolve()
        .then(function () {
          return fetchFn(API + "/" + encodeURIComponent(String(ref)), { method: "GET" });
        })
        .then(function (payload) {
          var record = unwrapApproval(payload);
          if (!record || !recordRef(record)) {
            throw new Error(t("next.approval.notice.invalid_record"));
          }
          return { mode: "record", record: record };
        });
    }

    function readPending() {
      var fetchFn = transport();
      if (!fetchFn) return Promise.reject(missingTransport());
      return Promise.resolve()
        .then(function () { return fetchFn(pendingPath(), { method: "GET" }); })
        .then(function (payload) {
          var approvals = payload && Array.isArray(payload.approvals) ? payload.approvals : [];
          return {
            mode: "list",
            pending: approvals.filter(function (record) { return !!recordRef(record); }),
          };
        });
    }

    function missingTransport() {
      var error = new Error(t("next.approval.notice.no_transport"));
      error.code = "transport_missing";
      return error;
    }

    function startLoad() {
      if (state.disposed || !state.mounted || state.busy) return Promise.resolve();
      var fetchFn = transport();
      state.loading = true;
      state.notice = null;
      state.errorText = "";
      state.readToken += 1;
      state.pendingEpoch += 1;
      var token = state.readToken;
      var gen = state.generation;
      renderLoading();
      if (!fetchFn) {
        state.loading = false;
        renderError(t("next.approval.notice.no_transport"));
        return Promise.resolve();
      }
      var ref = state.ref || recordRef(state.record);
      var request = ref ? readRecord(ref) : readPending();
      return request.then(function (result) {
        if (!isReadCurrent(gen, token)) return;
        state.loading = false;
        if (result.mode === "record") {
          state.record = result.record;
          state.ref = recordRef(result.record) || ref;
          renderReady(state.record);
          refreshPendingList();
          return;
        }
        state.pending = result.pending;
        if (state.pending.length) {
          state.record = state.pending[0];
          state.ref = recordRef(state.record);
          renderReady(state.record);
        } else {
          state.record = null;
          state.ref = "";
          renderEmpty();
        }
      }).catch(function (error) {
        if (!isReadCurrent(gen, token)) return;
        state.loading = false;
        renderError(t("next.approval.error", { message: describeError(error) }));
      });
    }

    function refreshPendingList() {
      var fetchFn = transport();
      if (!fetchFn) return Promise.resolve();
      var gen = state.generation;
      state.pendingEpoch += 1;
      var epoch = state.pendingEpoch;
      return readPending().then(function (result) {
        if (state.disposed || !state.mounted) return;
        if (state.generation !== gen || state.pendingEpoch !== epoch) return;
        state.pending = result.pending;
        renderPending();
      }).catch(function () {
        /* the authoritative record is already on screen; a list read is optional */
      });
    }

    function selectPending(ref) {
      var found = null;
      state.pending.forEach(function (record) {
        if (recordRef(record) === ref) found = record;
      });
      if (!found) return;
      state.record = found;
      state.ref = ref;
      state.notice = null;
      renderReady(found);
    }

    function decisionBody() {
      var record = state.record;
      if (!record) return null;
      var revision = Number(record.revision);
      if (!Number.isFinite(revision) || Math.floor(revision) !== revision || revision < 1) {
        return null;
      }
      var body = { expected_revision: revision };
      var reasonInput = state.ui.reason;
      var reason = reasonInput && typeof reasonInput.value === "string" ? reasonInput.value.trim() : "";
      if (reason) body.reason = reason;
      return body;
    }

    function refreshAfterError(info, gen, token, fallbackNotice) {
      var ref = state.ref || recordRef(state.record);
      var jobs = [];
      if (ref) jobs.push(readRecord(ref).catch(function () { return null; }));
      jobs.push(readPending().catch(function () { return null; }));
      return Promise.all(jobs).then(function (results) {
        if (!isActionCurrent(gen, token)) return;
        var recordResult = null;
        var listResult = null;
        results.forEach(function (result) {
          if (!result) return;
          if (result.mode === "record") recordResult = result;
          if (result.mode === "list") listResult = result;
        });
        if (listResult) state.pending = listResult.pending;
        var adopted = false;
        if (recordResult) {
          state.record = recordResult.record;
          state.ref = recordRef(state.record) || ref;
          adopted = true;
        } else if (listResult && ref) {
          // The single-record read is not always visible to this principal
          // (cross-principal reads share the same 404 shape); fall back to the
          // record the bounded list did return instead of keeping a stale one.
          listResult.pending.forEach(function (candidate) {
            if (recordRef(candidate) === ref) {
              state.record = candidate;
              adopted = true;
            }
          });
        }
        var conflict = !!info && (info.status === 409 || info.code === "revision_conflict");
        if (conflict) {
          var revision = adopted && state.record && state.record.revision != null
            ? state.record.revision : "";
          state.notice = {
            kind: "conflict",
            text: revision === ""
              ? t("next.approval.notice.conflict_unknown")
              : t("next.approval.notice.conflict", { revision: revision }),
          };
        } else if (!state.notice) {
          state.notice = fallbackNotice || null;
        }
        var gone = !!info && (info.status === 404 || info.code === "not_found") && !adopted;
        if (state.record && !gone) {
          renderReady(state.record);
        } else {
          renderError(state.notice ? state.notice.text : t("next.approval.notice.not_found"));
        }
      });
    }

    function decide(action) {
      if (state.disposed || !state.mounted || state.busy || state.loading) return Promise.resolve();
      var record = state.record;
      if (!record || actionDisabled(action)) return Promise.resolve();
      var body = decisionBody();
      if (!body) {
        state.notice = { kind: "error", text: t("next.approval.notice.invalid_record") };
        renderReady(record);
        return Promise.resolve();
      }
      var fetchFn = transport();
      if (!fetchFn) return Promise.resolve();
      var ref = recordRef(record) || state.ref;
      if (!ref) return Promise.resolve();
      var gen = state.generation;
      state.actionToken += 1;
      var token = state.actionToken;
      state.busy = true;
      state.notice = null;
      state.pendingEpoch += 1;
      renderReady(record);
      var path = API + "/" + encodeURIComponent(String(ref)) + "/" + action;
      return Promise.resolve()
        .then(function () {
          return fetchFn(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
        })
        .then(function (payload) {
          if (!isActionCurrent(gen, token)) return;
          var updated = unwrapApproval(payload);
          if (!updated || !recordRef(updated)) {
            throw new Error(t("next.approval.notice.invalid_record"));
          }
          state.busy = false;
          state.record = updated;
          state.ref = recordRef(updated);
          state.notice = {
            kind: "ok",
            text: t(ACTION_NOTICE[action] || "next.approval.notice.action_failed",
              { revision: updated.revision == null ? "—" : updated.revision }),
          };
          renderReady(updated);
          refreshPendingList();
        })
        .catch(function (error) {
          if (!isActionCurrent(gen, token)) return;
          state.busy = false;
          var info = errorInfo(error);
          var fallback = null;
          if (info.status === 409 || info.code === "revision_conflict") {
            fallback = { kind: "conflict", text: t("next.approval.notice.conflict_unknown") };
          } else if (info.status === 401 || info.status === 403 || info.code === "access_denied") {
            fallback = { kind: "error", text: t("next.approval.notice.access") };
          } else if (info.status === 404 || info.code === "not_found") {
            fallback = { kind: "error", text: t("next.approval.notice.not_found") };
          } else {
            state.notice = {
              kind: "error",
              text: t("next.approval.notice.action_failed", { message: describeError(error) }),
            };
            renderReady(state.record);
            return;
          }
          state.notice = fallback;
          var shouldRefresh = info.status === 409 || info.status === 401 ||
            info.status === 403 || info.status === 404 ||
            info.code === "revision_conflict" || info.code === "access_denied" ||
            info.code === "not_found";
          if (!shouldRefresh) {
            renderReady(state.record);
            return;
          }
          return refreshAfterError(info, gen, token, fallback);
        });
    }

    // ── context selection ───────────────────────────────────────────────

    function selectionFrom(snapshot) {
      var ref = "";
      var proposalRef = "";
      if (typeof deps.approvalRef === "string" && deps.approvalRef) ref = deps.approvalRef;
      if (snapshot && typeof snapshot === "object") {
        if (!ref && typeof snapshot.approvalRef === "string") ref = snapshot.approvalRef;
        var research = snapshot.researchRef;
        if (research && typeof research === "object") {
          if (!ref && typeof research.approvalRef === "string") ref = research.approvalRef;
          if (typeof research.proposalId === "string") proposalRef = research.proposalId;
        }
      }
      return { ref: ref, proposalRef: proposalRef };
    }

    function applySnapshot(snapshot) {
      if (state.disposed || !state.mounted) return;
      state.snapshot = snapshot || null;
      var projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      if (!projectId && deps.projectId) projectId = String(deps.projectId);
      var selection = selectionFrom(snapshot);
      var key = projectId + "|" + selection.ref + "|" + selection.proposalRef;
      state.projectId = projectId;
      state.proposalRef = selection.proposalRef || "";
      if (key === state.key) return;
      state.key = key;
      state.ref = selection.ref || "";
      state.record = null;
      state.pending = [];
      state.notice = null;
      state.errorText = "";
      startLoad();
    }

    // ── public lifecycle ────────────────────────────────────────────────

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("approval-card: mount host missing");
      if (!doc) throw new Error("approval-card: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.busy = false;
      state.loading = false;
      state.key = "";
      var context = api.context || deps.context || null;
      state.context = context;
      buildDom();
      renderEmpty();
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

    function suspend() { state.suspended = true; }

    function resume() {
      state.suspended = false;
      rerender();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.generation += 1;
      state.readToken += 1;
      state.actionToken += 1;
      state.busy = false;
      state.loading = false;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.record = null;
      state.pending = [];
      state.context = null;
      state.snapshot = null;
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
    factory: createApprovalCard,
  };
})();
