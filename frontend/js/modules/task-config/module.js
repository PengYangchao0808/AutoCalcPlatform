/*
 * ACP Next — task-config module (UI-03).
 *
 * Real configuration panel for the Next entry: structure intake, workflow +
 * method profile (catalog driven), charge/multiplicity with a parity check,
 * server-side method validation, the two-step `/preflight` → `POST /jobs`
 * submit, durable create-task drafts and the submit receipt.
 *
 * Contract (UI-00 recipe):
 *   window.ACPNextModules["task-config"] = { descriptor, factory }
 *   factory(hostApi) -> { mount(el, api), updateContext, suspend, resume, dispose }
 *
 * Ownership rules:
 *   - no science is invented here: workflows, schemas, profiles, field options,
 *     defaults and semantic locks all come from GET /workflow-catalog and
 *     GET /method-catalog; the server's `/validate-method` is the authority for
 *     method errors and `/preflight` is the authority for feasibility + digest;
 *   - the POST /jobs envelope is not re-owned: `model.buildEnvelope()` delegates
 *     to the wizard's frozen `ACPWizardSubmitEdit.buildStructureJobBody` when
 *     that classic slice is loaded;
 *   - every async result is guarded by the module generation and a per-action
 *     token, so a slow response can never overwrite a newer edit;
 *   - data flows through the shell client (ACPClient.transport.request); this
 *     module starts no timer and no polling loop.
 */
(function () {
  "use strict";

  var MODULE_ID = "task-config";
  var API = "/api/v1";
  var VALIDATE_DEBOUNCE_MS = 400;
  var DRAFT_DEBOUNCE_MS = 1500;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["task.config"],
    binds: ["projectId"],
    capabilities: { requires: [], provides: ["task.config"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 1,
    error: { titleKey: "next.config.errorTitle", messageKey: "next.config.errorMessage" },
  };

  /* zh fallbacks: used only when the shell dictionary has no entry yet. The
   * static chrome also carries data-i18n so a language switch retranslates it. */
  var ZH = {
    "next.config.title": "任务配置",
    "next.config.structure": "1 · 结构",
    "next.config.structure_placeholder": "粘贴 SMILES、XYZ 或 molfile 文本，或上传文件",
    "next.config.parse": "解析结构",
    "next.config.upload": "上传文件",
    "next.config.parsing": "正在解析结构…",
    "next.config.structure_hint": "尚未载入结构。支持 SMILES / XYZ / SDF / MOL / GJF / ORCA 输入。",
    "next.config.structure_summary": "{formula} · {atoms} 个原子 · 电荷 {charge} · 自旋 {multiplicity}",
    "next.config.structure_error": "结构解析失败：{message}",
    "next.config.workflow": "2 · 计算类型与方法",
    "next.config.workflow_label": "计算类型",
    "next.config.workflow_unsupported": "（需经典向导）",
    "next.config.profile_label": "方法预设",
    "next.config.profile_fields": "字段默认值",
    "next.config.advanced": "高级参数",
    "next.config.state": "3 · 体系与资源",
    "next.config.charge": "电荷",
    "next.config.multiplicity": "自旋多重度",
    "next.config.molecule_name": "分子名",
    "next.config.project": "项目",
    "next.config.nproc": "核数",
    "next.config.mem": "内存",
    "next.config.review": "4 · 预检与提交",
    "next.config.summary": "配置摘要",
    "next.config.summary_empty": "载入结构并选择计算类型后显示摘要。",
    "next.config.validate": "预检",
    "next.config.preflighting": "预检中…",
    "next.config.preflight_ok": "预检通过：{digest}",
    "next.config.preflight_failed": "预检未通过",
    "next.config.preflight_retry": "重试预检",
    "next.config.submit": "提交任务",
    "next.config.submitting": "提交中…",
    "next.config.submit_blocked": "预检通过后才能提交",
    "next.config.receipt": "已提交",
    "next.config.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
    "next.config.receipt_digest": "计划摘要 {digest}",
    "next.config.open_task": "在任务面板打开",
    "next.config.open_classic": "在经典工作台打开",
    "next.config.open_classic_hint": "此计算类型需要额外输入（源任务、坐标或专用文件），请用经典向导完成配置。",
    "next.config.validate_running": "校验方法参数…",
    "next.config.validate_ok": "方法参数有效",
    "next.config.validate_invalid": "方法参数有误",
    "next.config.validate_error": "方法校验请求失败：{message}",
    "next.config.warnings": "警告",
    "next.config.errors": "需要修正",
    "next.config.draft_found": "发现保存的草稿（{when}）",
    "next.config.draft_restore": "恢复",
    "next.config.draft_discard": "丢弃",
    "next.config.draft_saving": "正在保存草稿…",
    "next.config.draft_saved": "草稿已保存",
    "next.config.draft_conflict": "草稿已在其他地方更新",
    "next.config.draft_reload": "载入最新",
    "next.config.draft_fork": "另存为新草稿",
    "next.config.draft_error": "草稿保存失败：{message}",
    "next.config.errorTitle": "任务配置不可用",
    "next.config.errorMessage": "task-config 模块加载失败，请查看控制台。",
    "next.config.load_error": "目录加载失败：{message}",
    "next.config.retry": "重试",
    "next.config.no_projects": "全部项目",
    "next.config.note.composite_ri": "复合方法使用内置 RI 近似，RI/辅助基组由方法决定。",
    "next.config.note.builtin_dispersion": "该方法已内置 {dispersion} 色散校正，色散项固定为 none。",
    "next.config.error.structure_required": "请先载入结构。",
    "next.config.error.workflow_required": "请选择计算类型。",
    "next.config.error.workflow_unsupported": "该计算类型需要经典向导的专用输入。",
    "next.config.error.schema_missing": "目录中缺少该方法 schema。",
    "next.config.error.schema_empty": "该方法 schema 没有可配置层级。",
    "next.config.error.level_engine": "层级 {level} 缺少 engine。",
    "next.config.error.charge_integer": "电荷必须是整数。",
    "next.config.error.multiplicity_integer": "自旋多重度必须是 ≥1 的整数。",
    "next.config.error.multiplicity_parity": "电子数为 {electrons}，自旋多重度奇偶不匹配。",
    "next.config.error.nproc": "核数必须是 1–256 的整数。",
    "next.config.error.mem": "请填写内存（例如 2000MB）。",
    "next.config.warning.structure": "结构提示：{message}",
    "next.config.stale": "配置已修改，请重新预检。",
    "next.config.submit_failed": "提交失败：{message}",
    "next.config.plan_stale": "预检之后配置发生变化，请重新预检。",
    "next.config.forbidden": "当前身份无权提交任务。",
    "next.config.unsupported_selected": "已选择需要经典向导的计算类型：{workflow}",
  };

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

  function lang() {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.getLang === "function") return shell.getLang() === "en-US" ? "en" : "zh";
    return "zh";
  }

  function nowIso() {
    try { return new Date().toISOString(); } catch (e) { return ""; }
  }

  function formatWhen(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString();
    } catch (e) {
      return String(iso);
    }
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
      if (typeof detail === "string") return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function isForbidden(error) {
    return !!error && (error.status === 401 || error.status === 403);
  }

  function newIdempotencyKey() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "next-" + crypto.randomUUID();
      }
    } catch (e) { /* fall through */ }
    return "next-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function createTaskConfig(deps) {
    deps = deps || {};
    var model = deps.model || (typeof window !== "undefined" ? window.ACPNextTaskConfigModel : null);
    var requestFn = deps.request || (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport
      ? function (path, opts) { return window.ACPClient.transport.request(path, opts); }
      : null);
    var doc = deps.doc || (typeof document !== "undefined" ? document : null);
    var context = deps.context || null;
    var commands = deps.commands || null;

    var state = {
      generation: 0,
      mounted: false,
      suspended: false,
      disposed: false,
      host: null,
      snapshot: null,
      projectId: "",
      catalogLoading: false,
      catalogError: null,
      workflows: [],
      methodCatalog: null,
      entry: null,
      schema: null,
      profileId: "",
      levels: {},
      asset: null,
      structureText: "",
      parseStatus: "idle",
      parseError: null,
      charge: 0,
      multiplicity: 1,
      nproc: 4,
      mem: "2000MB",
      moleculeName: "",
      configRevision: 0,
      notes: [],
      validation: { status: "idle", errors: [], warnings: [], token: 0, revision: -1 },
      preflight: { status: "idle", fingerprint: null, planKey: null, reasons: [], token: 0, revision: -1 },
      submit: { status: "idle", receipt: null, error: null, idempotencyKey: null, keyPlan: null, token: 0 },
      draft: { id: null, revision: 0, status: "idle", savedAt: null, error: null, conflict: false, timer: null, banner: null, name: "" },
      ui: {},
    };

    var listenerCleanup = [];

    /* ── DOM ─────────────────────────────────────────────────────────── */

    function buildDom() {
      var root = el(doc, "div", "acp-cfg");
      root.setAttribute("data-acp-module-state", "loading");
      root.setAttribute("data-module", MODULE_ID);

      var banner = el(doc, "div", "acp-cfg-banner");
      banner.hidden = true;
      banner.setAttribute("role", "status");
      root.appendChild(banner);
      state.ui.banner = banner;

      var body = el(doc, "div", "acp-cfg-body");
      root.appendChild(body);

      body.appendChild(buildStructureSection());
      body.appendChild(buildWorkflowSection());
      body.appendChild(buildStateSection());
      body.appendChild(buildReviewSection());

      var actions = el(doc, "div", "acp-cfg-actions");
      var preflightBtn = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.validate"));
      preflightBtn.type = "button";
      preflightBtn.setAttribute("data-action", "preflight");
      preflightBtn.setAttribute("data-i18n", "next.config.validate");
      var submitBtn = el(doc, "button", "acp-btn acp-btn--primary", t("next.config.submit"));
      submitBtn.type = "button";
      submitBtn.setAttribute("data-action", "submit");
      submitBtn.setAttribute("data-i18n", "next.config.submit");
      submitBtn.disabled = true;
      actions.appendChild(preflightBtn);
      actions.appendChild(submitBtn);
      root.appendChild(actions);
      state.ui.preflightBtn = preflightBtn;
      state.ui.submitBtn = submitBtn;

      var draftStatus = el(doc, "p", "acp-cfg-draft-status");
      draftStatus.setAttribute("data-role", "draft-status");
      draftStatus.setAttribute("aria-live", "polite");
      root.appendChild(draftStatus);
      state.ui.draftStatus = draftStatus;

      state.ui.classicLink = el(doc, "a", "acp-cfg-classic-link", t("next.config.open_classic"));
      state.ui.classicLink.setAttribute("href", "/#new-calculation");
      state.ui.classicLink.setAttribute("data-i18n", "next.config.open_classic");
      state.ui.classicLink.hidden = true;
      root.appendChild(state.ui.classicLink);

      state.host.replaceChildren(root);
      state.ui.root = root;

      on(preflightBtn, "click", function () { runPreflight(); });
      on(submitBtn, "click", function () { submit(); });
    }

    function buildStructureSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "structure");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.structure"));

      var textarea = el(doc, "textarea", "acp-cfg-text");
      textarea.setAttribute("rows", "4");
      textarea.setAttribute("data-role", "structure-text");
      textarea.setAttribute("placeholder", t("next.config.structure_placeholder"));
      textarea.setAttribute("data-i18n-ph", "next.config.structure_placeholder");
      textarea.value = state.structureText;
      section.appendChild(textarea);
      state.ui.structureText = textarea;

      var row = el(doc, "div", "acp-cfg-row");
      var parseBtn = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.parse"));
      parseBtn.type = "button";
      parseBtn.setAttribute("data-action", "parse");
      parseBtn.setAttribute("data-i18n", "next.config.parse");
      var fileLabel = el(doc, "label", "acp-cfg-file");
      fileLabel.appendChild(el(doc, "span", "", t("next.config.upload")));
      var fileInput = el(doc, "input", "");
      fileInput.type = "file";
      fileInput.setAttribute("data-role", "structure-file");
      fileLabel.appendChild(fileInput);
      row.appendChild(parseBtn);
      row.appendChild(fileLabel);
      section.appendChild(row);
      state.ui.parseBtn = parseBtn;
      state.ui.fileInput = fileInput;

      var summary = el(doc, "p", "acp-cfg-note", t("next.config.structure_hint"));
      summary.setAttribute("data-i18n", "next.config.structure_hint");
      summary.setAttribute("data-role", "structure-summary");
      summary.setAttribute("aria-live", "polite");
      section.appendChild(summary);
      state.ui.structureSummary = summary;

      on(parseBtn, "click", function () { parseStructure(); });
      on(textarea, "input", function () {
        state.structureText = textarea.value;
        markDirty();
      });
      on(fileInput, "change", function () { parseFile(fileInput); });
      return section;
    }

    function buildWorkflowSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "workflow");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.workflow"));

      section.appendChild(i18nEl(doc, "label", "acp-cfg-label", "next.config.workflow_label"));
      var workflowSelect = el(doc, "select", "acp-cfg-select");
      workflowSelect.setAttribute("data-role", "workflow");
      section.appendChild(workflowSelect);
      state.ui.workflowSelect = workflowSelect;
      on(workflowSelect, "change", function () {
        var entry = model.findWorkflow(state.workflows, workflowSelect.value);
        selectWorkflow(entry, { keepStructure: true });
      });

      var desc = el(doc, "p", "acp-cfg-note");
      desc.setAttribute("data-role", "workflow-desc");
      section.appendChild(desc);
      state.ui.workflowDesc = desc;

      section.appendChild(i18nEl(doc, "label", "acp-cfg-label", "next.config.profile_label"));
      var profileSelect = el(doc, "select", "acp-cfg-select");
      profileSelect.setAttribute("data-role", "profile");
      section.appendChild(profileSelect);
      state.ui.profileSelect = profileSelect;
      on(profileSelect, "change", function () { selectProfile(profileSelect.value); });

      var fields = el(doc, "div", "acp-cfg-fields");
      section.appendChild(fields);
      state.ui.fields = fields;
      state.ui.fieldInputs = {};

      var notes = el(doc, "ul", "acp-cfg-notes");
      notes.setAttribute("data-role", "semantic-notes");
      section.appendChild(notes);
      state.ui.semanticNotes = notes;
      return section;
    }

    function buildStateSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "state");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.state"));

      var grid = el(doc, "div", "acp-cfg-grid");

      grid.appendChild(numberField("next.config.charge", "charge", "1"));
      grid.appendChild(numberField("next.config.multiplicity", "multiplicity", "1"));
      grid.appendChild(numberField("next.config.nproc", "nproc", "1"));
      grid.appendChild(textField("next.config.mem", "mem"));
      var nameField = textField("next.config.molecule_name", "moleculeName");
      nameField.className = "acp-cfg-field acp-cfg-field--wide";
      grid.appendChild(nameField);
      section.appendChild(grid);

      var hint = el(doc, "p", "acp-cfg-note");
      hint.setAttribute("data-role", "parity-hint");
      section.appendChild(hint);
      state.ui.parityHint = hint;
      return section;
    }

    function numberField(key, role, step) {
      var wrap = el(doc, "label", "acp-cfg-field");
      wrap.appendChild(i18nEl(doc, "span", "acp-cfg-label", key));
      var input = el(doc, "input", "acp-cfg-input");
      input.type = "number";
      input.setAttribute("step", step);
      input.setAttribute("data-role", role);
      wrap.appendChild(input);
      state.ui[role] = input;
      on(input, "input", function () {
        var value = Number(input.value);
        if (role === "charge") state.charge = isFinite(value) ? value : 0;
        else if (role === "multiplicity") state.multiplicity = isFinite(value) ? value : 1;
        else if (role === "nproc") state.nproc = isFinite(value) ? value : 1;
        markDirty();
      });
      return wrap;
    }

    function textField(key, role) {
      var wrap = el(doc, "label", "acp-cfg-field");
      wrap.appendChild(i18nEl(doc, "span", "acp-cfg-label", key));
      var input = el(doc, "input", "acp-cfg-input");
      input.type = "text";
      input.setAttribute("data-role", role);
      wrap.appendChild(input);
      state.ui[role] = input;
      on(input, "input", function () {
        if (role === "mem") state.mem = input.value;
        else if (role === "moleculeName") state.moleculeName = input.value;
        markDirty();
      });
      return wrap;
    }

    function buildReviewSection() {
      var section = el(doc, "section", "acp-cfg-section");
      section.setAttribute("data-section", "review");
      section.appendChild(i18nEl(doc, "h3", "acp-cfg-heading", "next.config.review"));

      var summary = el(doc, "dl", "acp-cfg-summary");
      summary.setAttribute("data-role", "summary");
      section.appendChild(summary);
      state.ui.summary = summary;

      var status = el(doc, "p", "acp-cfg-status");
      status.setAttribute("data-role", "validation-status");
      status.setAttribute("aria-live", "polite");
      section.appendChild(status);
      state.ui.validationStatus = status;

      var errors = el(doc, "ul", "acp-cfg-errors");
      errors.setAttribute("data-role", "errors");
      section.appendChild(errors);
      state.ui.errors = errors;

      var preflightStatus = el(doc, "p", "acp-cfg-status");
      preflightStatus.setAttribute("data-role", "preflight-status");
      preflightStatus.setAttribute("aria-live", "polite");
      section.appendChild(preflightStatus);
      state.ui.preflightStatus = preflightStatus;

      var reasons = el(doc, "ul", "acp-cfg-errors");
      reasons.setAttribute("data-role", "preflight-reasons");
      section.appendChild(reasons);
      state.ui.preflightReasons = reasons;

      var receipt = el(doc, "div", "acp-cfg-receipt");
      receipt.hidden = true;
      receipt.setAttribute("role", "status");
      receipt.setAttribute("data-role", "receipt");
      section.appendChild(receipt);
      state.ui.receipt = receipt;
      return section;
    }

    /* ── helpers ─────────────────────────────────────────────────────── */

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      listenerCleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function guard(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function bumpRevision() {
      state.configRevision += 1;
      state.validation = { status: "idle", errors: [], warnings: [], token: state.validation.token + 1, revision: -1 };
      state.preflight = { status: "idle", fingerprint: null, planKey: null, reasons: [], token: state.preflight.token + 1, revision: -1 };
    }

    function markDirty() {
      bumpRevision();
      renderReview();
      renderActions();
      scheduleDraftSave();
    }

    function setModuleState(name) {
      if (state.ui.root) state.ui.root.setAttribute("data-acp-module-state", name);
    }

    function empty(node) {
      if (node) node.replaceChildren();
    }

    function listItem(node, cls, text) {
      var li = el(doc, "li", cls, text);
      node.appendChild(li);
      return li;
    }

    /* ── catalog + defaults ──────────────────────────────────────────── */

    function loadCatalogs() {
      var gen = state.generation;
      state.catalogLoading = true;
      state.catalogError = null;
      setModuleState("loading");
      renderBanner();
      return Promise.all([
        requestFn(API + "/workflow-catalog", { method: "GET" }),
        requestFn(API + "/method-catalog", { method: "GET" }),
      ]).then(function (results) {
        if (!guard(gen)) return;
        state.workflows = model.filterWorkflows(results[0]);
        state.methodCatalog = results[1] || {};
        state.catalogLoading = false;
        if (!state.workflows.length) throw new Error("workflow catalog is empty");
        var current = model.findWorkflow(state.workflows, state.entry && state.entry.id);
        var entry = current || model.pickDefaultWorkflow(state.workflows);
        selectWorkflow(entry, { keepStructure: true, silent: true });
        setModuleState("ready");
        renderAll();
        return loadDraftPointer();
      }).catch(function (error) {
        if (!guard(gen)) return;
        state.catalogLoading = false;
        state.catalogError = error;
        setModuleState("error");
        renderBanner();
        renderCatalogError(error);
      });
    }

    function renderCatalogError(error) {
      if (!state.ui.errors) return;
      empty(state.ui.errors);
      listItem(state.ui.errors, "acp-cfg-error", t("next.config.load_error", { message: describeError(error) }));
      var retry = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.retry"));
      retry.type = "button";
      retry.setAttribute("data-i18n", "next.config.retry");
      retry.setAttribute("data-action", "retry-catalog");
      on(retry, "click", function () { resetAndReload(); });
      state.ui.errors.appendChild(retry);
    }

    function resetAndReload() {
      if (!state.host) return;
      buildDom();
      loadCatalogs();
    }

    function selectWorkflow(entry, options) {
      options = options || {};
      state.entry = entry || null;
      state.schema = entry ? model.schemaFor(state.methodCatalog, entry.schemaId) : null;
      state.notes = [];
      if (!entry) {
        state.levels = {};
        state.profileId = "";
      } else if (state.schema) {
        var profile = model.pickDefaultProfile(state.schema);
        state.profileId = profile ? String(profile.profile_id || "") : "";
        state.levels = model.buildDefaultLevels(state.schema, state.methodCatalog, entry, state.profileId);
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
      } else {
        state.levels = {};
        state.profileId = "";
      }
      bumpRevision();
      if (!options.silent) {
        renderAll();
        scheduleDraftSave();
      }
    }

    function selectProfile(profileId) {
      if (!state.schema) return;
      state.profileId = String(profileId || "");
      state.levels = model.buildDefaultLevels(state.schema, state.methodCatalog, state.entry, state.profileId);
      var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
      state.levels = locked.levels;
      state.notes = locked.notes;
      bumpRevision();
      renderMethod();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
    }

    /* ── structure intake ────────────────────────────────────────────── */

    function parseStructure() {
      var content = String((state.ui.structureText && state.ui.structureText.value) || "").trim();
      if (!content) return;
      state.structureText = content;
      state.parseStatus = "loading";
      state.parseError = null;
      renderStructure();
      var gen = state.generation;
      requestFn(API + "/structures/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: content, format: "auto", filename: "" }),
        timeoutMs: 0,
      }).then(function (body) {
        if (!guard(gen)) return;
        if (!body || body.ok === false || !Array.isArray(body.structures) || !body.structures.length) {
          var detail = body && body.errors && body.errors.length ? body.errors[0] : "no structure parsed";
          throw new Error(detail);
        }
        applyAsset(body.structures[0]);
      }).catch(function (error) {
        if (!guard(gen)) return;
        state.parseStatus = "error";
        state.parseError = error;
        renderStructure();
        renderReview();
      });
    }

    function parseFile(input) {
      var file = input && input.files && input.files[0];
      if (!file) return;
      var gen = state.generation;
      var read = function (text) {
        if (!guard(gen)) return;
        state.ui.structureText.value = text;
        state.structureText = text;
        parseStructure();
      };
      try {
        if (typeof file.text === "function") {
          file.text().then(read).catch(function () { parseStructure(); });
        } else if (typeof FileReader !== "undefined") {
          var reader = new FileReader();
          reader.onload = function () { read(String(reader.result || "")); };
          reader.readAsText(file);
        }
      } catch (e) {
        state.parseStatus = "error";
        state.parseError = e;
        renderStructure();
      }
    }

    function applyAsset(asset) {
      state.asset = asset || null;
      state.parseStatus = "ok";
      state.parseError = null;
      if (asset) {
        if (asset.formula) state.moleculeName = state.moleculeName || String(asset.molecule_name || asset.name || "");
        if (asset.charge != null) state.charge = Number(asset.charge) || 0;
        if (asset.multiplicity != null) state.multiplicity = Number(asset.multiplicity) || 1;
      }
      bumpRevision();
      renderStructure();
      renderStateInputs();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
      publishStructure();
    }

    /** Hand the parsed geometry to the shared viewer (UI-05 assembly). */
    function publishStructure() {
      if (!context || typeof context.setFocus !== "function") return;
      var xyz = state.asset && state.asset.xyz ? String(state.asset.xyz) : "";
      if (!xyz) return;
      try {
        context.setFocus({ structureRef: { kind: "inline-xyz", text: xyz } }, MODULE_ID);
      } catch (e) { /* read-only host: the viewer simply keeps its own focus */ }
    }

    /* ── validation + preflight ──────────────────────────────────────── */

    function scheduleValidation() {
      if (state.validation.timer) clearTimeout(state.validation.timer);
      var gen = state.generation;
      state.validation.timer = setTimeout(function () {
        state.validation.timer = null;
        if (!guard(gen)) return;
        validateMethod();
      }, VALIDATE_DEBOUNCE_MS);
    }

    function methodPayload() {
      return { schema_id: (state.entry && state.entry.schemaId) || "", levels: model.buildLevels(state.levels, state.schema, state.methodCatalog) };
    }

    function validateMethod() {
      if (!state.schema || !state.entry) return Promise.resolve({ ok: false });
      var payload = methodPayload();
      if (!payload.schema_id || !Object.keys(payload.levels).length) return Promise.resolve({ ok: false });
      var gen = state.generation;
      var token = state.validation.token + 1;
      state.validation.token = token;
      state.validation.status = "validating";
      renderValidation();
      return requestFn(API + "/validate-method", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 0,
      }).then(function (body) {
        if (!guard(gen) || state.validation.token !== token) return { ok: false };
        var errors = (body && body.errors) || [];
        var warnings = (body && body.warnings) || [];
        applyNormalizedLevels(body && body.normalized_levels);
        state.validation.status = errors.length ? "invalid" : "ok";
        state.validation.errors = errors.map(function (text) { return { text: String(text) }; });
        state.validation.warnings = warnings.map(function (text) { return { text: String(text) }; });
        state.validation.revision = state.configRevision;
        renderValidation();
        renderReview();
        renderActions();
        return { ok: state.validation.status === "ok" };
      }).catch(function (error) {
        if (!guard(gen) || state.validation.token !== token) return { ok: false };
        state.validation.status = "error";
        state.validation.errors = [{ text: t("next.config.validate_error", { message: describeError(error) }) }];
        state.validation.revision = state.configRevision;
        renderValidation();
        renderActions();
        return { ok: false };
      });
    }

    /** Sparse write-back: only fill keys the user has not set. Never overwrite. */
    function applyNormalizedLevels(normalized) {
      if (!normalized || typeof normalized !== "object") return;
      Object.keys(normalized).forEach(function (levelId) {
        var target = state.levels[levelId];
        var source = normalized[levelId];
        if (!target || !source || typeof source !== "object") return;
        Object.keys(source).forEach(function (key) {
          var value = source[key];
          if (value === undefined || value === null || typeof value === "object") return;
          if (target[key] === undefined || target[key] === null || target[key] === "") target[key] = value;
        });
      });
      refreshFieldInputs();
    }

    /** Update bound inputs in place: never rebuild the form under the caret. */
    function refreshFieldInputs() {
      var bound = state.ui.fieldInputs || {};
      Object.keys(bound).forEach(function (levelId) {
        var level = state.levels[levelId] || {};
        var fields = bound[levelId] || {};
        Object.keys(fields).forEach(function (field) {
          var input = fields[field];
          var value = level[field];
          if (!input || value === undefined || value === null) return;
          var text = String(value);
          if (input.value !== text) input.value = text;
        });
      });
    }

    function clientValidation() {
      return model.validateConfig({
        asset: state.asset,
        entry: state.entry,
        schema: state.schema,
        levels: state.levels,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
      });
    }

    function currentRequest() {
      return model.buildRequest({
        entry: state.entry,
        schema: state.schema,
        workflowId: state.entry && state.entry.id,
        profileId: state.profileId,
        levels: state.levels,
        methodCatalog: state.methodCatalog,
        asset: state.asset,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
        moleculeName: state.moleculeName,
        projectId: state.projectId,
      });
    }

    function runPreflight() {
      if (state.preflight.status === "running" || state.submit.status === "submitting") return;
      var check = clientValidation();
      renderClientErrors(check.errors);
      if (!check.ok) {
        state.preflight.status = "blocked";
        renderPreflight();
        renderActions();
        return Promise.resolve({ ok: false });
      }
      var validated = state.validation.status === "ok" && state.validation.revision === state.configRevision
        ? Promise.resolve({ ok: true })
        : validateMethod();
      return validated.then(function (methodResult) {
        if (!methodResult || !methodResult.ok) {
          state.preflight.status = "blocked";
          renderPreflight();
          renderActions();
          return { ok: false };
        }
        var request = currentRequest();
        var planKey = model.planIdentity(request);
        var gen = state.generation;
        var token = state.preflight.token + 1;
        state.preflight.token = token;
        state.preflight.status = "running";
        state.preflight.reasons = [];
        renderPreflight();
        renderActions();
        return requestFn(API + "/preflight", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          timeoutMs: 0,
        }).then(function (body) {
          if (!guard(gen) || state.preflight.token !== token) return { ok: false };
          var digest = body && body.plan_digest;
          if (!body || body.feasible !== true || !digest) {
            state.preflight.status = "infeasible";
            state.preflight.reasons = ((body && body.reasons) || []).map(function (reason) {
              return String((reason && (reason.message || reason.code)) || reason);
            });
            if (!state.preflight.reasons.length) state.preflight.reasons = [t("next.config.preflight_failed")];
            renderPreflight();
            renderActions();
            return { ok: false };
          }
          state.preflight.status = "ok";
          state.preflight.fingerprint = String(digest);
          state.preflight.planKey = planKey;
          state.preflight.revision = state.configRevision;
          state.preflight.reasons = [];
          renderPreflight();
          renderActions();
          return { ok: true, fingerprint: digest };
        }).catch(function (error) {
          if (!guard(gen) || state.preflight.token !== token) return { ok: false };
          state.preflight.status = "error";
          state.preflight.reasons = [describeError(error)];
          renderPreflight();
          renderActions();
          return { ok: false };
        });
      });
    }

    function canSubmit() {
      if (!state.asset || !state.entry || !state.entry.supported) return false;
      if (state.submit.status === "submitting" || state.preflight.status === "running") return false;
      if (state.preflight.status !== "ok" || !state.preflight.fingerprint) return false;
      if (state.preflight.revision !== state.configRevision) return false;
      try {
        return state.preflight.planKey === model.planIdentity(currentRequest());
      } catch (e) {
        return false;
      }
    }

    function submit() {
      if (!canSubmit() || state.submit.status === "submitting") return Promise.resolve({ ok: false });
      var request = currentRequest();
      var planKey = model.planIdentity(request);
      if (!state.submit.idempotencyKey || state.submit.keyPlan !== planKey) {
        state.submit.idempotencyKey = newIdempotencyKey();
        state.submit.keyPlan = planKey;
      }
      var body = model.buildEnvelope(request, { projectId: state.projectId });
      body.preflight_fingerprint = state.preflight.fingerprint;
      body.schema_version = model.SCHEMA_VERSION;
      var gen = state.generation;
      var token = state.submit.token + 1;
      state.submit.token = token;
      state.submit.status = "submitting";
      state.submit.error = null;
      renderActions();
      renderSubmitState();
      return requestFn(API + "/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": state.submit.idempotencyKey,
        },
        body: JSON.stringify(body),
        timeoutMs: 0,
      }).then(function (receipt) {
        if (!guard(gen) || state.submit.token !== token) return { ok: false };
        state.submit.status = "submitted";
        state.submit.receipt = receipt || {};
        state.submit.idempotencyKey = null;
        state.submit.keyPlan = null;
        discardDraft(true);
        renderSubmitState();
        renderActions();
        deliverReceipt(receipt || {});
        return { ok: true, receipt: receipt || {} };
      }).catch(function (error) {
        if (!guard(gen) || state.submit.token !== token) return { ok: false };
        state.submit.status = "error";
        var status = error && error.status;
        if (status === 400 && error.body && error.body.detail && error.body.detail.code === "preflight_stale") {
          state.submit.error = t("next.config.plan_stale");
          state.preflight = { status: "idle", fingerprint: null, planKey: null, reasons: [], token: state.preflight.token + 1, revision: -1 };
        } else if (status === 403 || status === 401) {
          state.submit.error = t("next.config.forbidden");
        } else {
          state.submit.error = t("next.config.submit_failed", { message: describeError(error) });
        }
        renderSubmitState();
        renderPreflight();
        renderActions();
        return { ok: false, error: error };
      });
    }

    function deliverReceipt(receipt) {
      if (!commands || typeof commands.openTask !== "function" || !receipt.job_id) return;
      try {
        commands.openTask({ jobId: String(receipt.job_id), attempt: Number(receipt.attempt || 1), source: MODULE_ID });
      } catch (e) {
        if (typeof console !== "undefined" && console.error) console.error("openTask after submit failed", e);
      }
    }

    /* ── drafts (server-side /drafts) ────────────────────────────────── */

    function pointerKey() {
      return model.draftPointerKey(state.projectId);
    }

    function readPointer() {
      try {
        var raw = window.localStorage && window.localStorage.getItem(pointerKey());
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        return parsed && parsed.draft_id ? parsed : null;
      } catch (e) {
        return null;
      }
    }

    function writePointer(value) {
      try {
        if (!window.localStorage) return;
        if (value) window.localStorage.setItem(pointerKey(), JSON.stringify(value));
        else window.localStorage.removeItem(pointerKey());
      } catch (e) { /* storage unavailable: drafts still work, restore hint is lost */ }
    }

    function loadDraftPointer() {
      state.draft.banner = null;
      renderBanner();
      var pointer = readPointer();
      if (!pointer) return Promise.resolve();
      var gen = state.generation;
      return requestFn(API + "/drafts/" + encodeURIComponent(pointer.draft_id), { method: "GET" })
        .then(function (draft) {
          if (!guard(gen) || !draft || !draft.snapshot) return;
          state.draft.banner = {
            draftId: String(draft.draft_id),
            revision: Number(draft.revision) || 1,
            workflow: String(draft.workflow || ""),
            updatedAt: String(draft.updated_at || ""),
            snapshot: draft.snapshot,
          };
          state.draft.id = state.draft.banner.draftId;
          state.draft.revision = state.draft.banner.revision;
          renderBanner();
        })
        .catch(function () {
          writePointer(null);
          state.draft.banner = null;
          renderBanner();
        });
    }

    function restoreDraft() {
      var banner = state.draft.banner;
      if (!banner) return;
      var restored = model.draftToState(banner.snapshot);
      if (!restored) {
        state.draft.banner = null;
        renderBanner();
        return;
      }
      var entry = model.findWorkflow(state.workflows, restored.workflowId);
      if (entry) {
        state.entry = entry;
        state.schema = model.schemaFor(state.methodCatalog, entry.schemaId);
        state.profileId = restored.profileId;
        state.levels = restored.levels;
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
      }
      state.charge = restored.charge;
      state.multiplicity = restored.multiplicity;
      state.nproc = restored.nproc;
      state.mem = restored.mem;
      state.moleculeName = restored.moleculeName;
      state.asset = restored.asset;
      state.structureText = restored.asset && restored.asset.xyz ? String(restored.asset.xyz) : "";
      state.parseStatus = restored.asset ? "ok" : "idle";
      state.draft.id = banner.draftId;
      state.draft.revision = banner.revision;
      state.draft.savedAt = banner.updatedAt;
      state.draft.banner = null;
      bumpRevision();
      renderAll();
      scheduleValidation();
      publishStructure();
      return;
    }

    function discardDraft(quiet) {
      var draftId = state.draft.id || (state.draft.banner && state.draft.banner.draftId);
      writePointer(null);
      state.draft.banner = null;
      state.draft.id = null;
      state.draft.revision = 0;
      state.draft.savedAt = null;
      state.draft.conflict = false;
      if (state.draft.timer) { clearTimeout(state.draft.timer); state.draft.timer = null; }
      if (!draftId) {
        if (!quiet) renderBanner();
        return Promise.resolve();
      }
      var gen = state.generation;
      return requestFn(API + "/drafts/" + encodeURIComponent(draftId), { method: "DELETE" })
        .catch(function () { return null; })
        .then(function () {
          if (!quiet && guard(gen)) renderBanner();
        });
    }

    function scheduleDraftSave() {
      if (state.disposed || !state.asset) return;
      if (state.draft.timer) clearTimeout(state.draft.timer);
      state.draft.timer = setTimeout(function () {
        state.draft.timer = null;
        saveDraft();
      }, DRAFT_DEBOUNCE_MS);
    }

    function saveDraft() {
      if (state.disposed || !state.asset || state.draft.conflict) return Promise.resolve();
      var snapshot = model.draftSnapshot({
        entry: state.entry,
        profileId: state.profileId,
        levels: state.levels,
        charge: state.charge,
        multiplicity: state.multiplicity,
        nproc: state.nproc,
        mem: state.mem,
        moleculeName: state.moleculeName,
        asset: state.asset,
      });
      var payload = {
        snapshot: snapshot,
        name: model.draftName(state.entry),
        project_id: state.projectId || "",
        workflow: (state.entry && state.entry.id) || "",
      };
      var gen = state.generation;
      state.draft.status = "saving";
      renderDraftStatus();
      var useUpdate = !!state.draft.id;
      var path = useUpdate ? API + "/drafts/" + encodeURIComponent(state.draft.id) : API + "/drafts";
      var method = useUpdate ? "PUT" : "POST";
      if (useUpdate) payload.expected_revision = state.draft.revision;
      return requestFn(path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 0,
      }).then(function (summary) {
        if (!guard(gen) || !summary) return;
        state.draft.id = String(summary.draft_id);
        state.draft.revision = Number(summary.revision) || 1;
        state.draft.savedAt = String(summary.updated_at || nowIso());
        state.draft.status = "saved";
        state.draft.error = null;
        writePointer({
          draft_id: state.draft.id,
          revision: state.draft.revision,
          project_id: state.projectId || "",
          workflow: (state.entry && state.entry.id) || "",
          updated_at: state.draft.savedAt,
        });
        renderDraftStatus();
      }).catch(function (error) {
        if (!guard(gen)) return;
        if (error && error.status === 409) {
          state.draft.conflict = true;
          state.draft.status = "conflict";
          renderBanner();
          return;
        }
        state.draft.status = "error";
        state.draft.error = describeError(error);
        renderDraftStatus();
      });
    }

    /* ── rendering ───────────────────────────────────────────────────── */

    function renderAll() {
      renderBanner();
      renderWorkflowOptions();
      renderProfileOptions();
      renderMethodValues();
      renderStructure();
      renderStateInputs();
      renderReview();
      renderValidation();
      renderPreflight();
      renderSubmitState();
      renderActions();
      renderDraftStatus();
      renderSemanticNotes();
    }

    function renderBanner() {
      var banner = state.ui.banner;
      if (!banner) return;
      empty(banner);
      if (state.draft.conflict) {
        banner.hidden = false;
        banner.appendChild(el(doc, "span", "acp-cfg-banner-text", t("next.config.draft_conflict")));
        var reload = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_reload"));
        reload.type = "button";
        on(reload, "click", function () {
          state.draft.conflict = false;
          state.draft.banner = null;
          loadDraftPointer().then(function () {
            if (state.draft.banner) restoreDraft();
          });
        });
        var fork = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_fork"));
        fork.type = "button";
        on(fork, "click", function () {
          state.draft.conflict = false;
          writePointer(null);
          state.draft.id = null;
          state.draft.revision = 0;
          saveDraft();
          renderBanner();
        });
        banner.appendChild(reload);
        banner.appendChild(fork);
        return;
      }
      if (!state.draft.banner) {
        banner.hidden = true;
        return;
      }
      banner.hidden = false;
      banner.appendChild(el(doc, "span", "acp-cfg-banner-text", t("next.config.draft_found", { when: formatWhen(state.draft.banner.updatedAt) })));
      var restore = el(doc, "button", "acp-btn acp-btn--primary", t("next.config.draft_restore"));
      restore.type = "button";
      restore.setAttribute("data-action", "draft-restore");
      on(restore, "click", function () { restoreDraft(); });
      var discard = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.draft_discard"));
      discard.type = "button";
      discard.setAttribute("data-action", "draft-discard");
      on(discard, "click", function () { discardDraft(false); });
      banner.appendChild(restore);
      banner.appendChild(discard);
    }

    function renderDraftStatus() {
      var label = state.ui.draftStatus;
      if (!label) return;
      if (state.draft.status === "saving") label.textContent = t("next.config.draft_saving");
      else if (state.draft.status === "saved") label.textContent = t("next.config.draft_saved");
      else if (state.draft.status === "error") label.textContent = t("next.config.draft_error", { message: state.draft.error || "" });
      else label.textContent = "";
    }

    function renderWorkflowOptions() {
      var select = state.ui.workflowSelect;
      if (!select) return;
      var previous = (state.entry && state.entry.id) || select.value;
      empty(select);
      state.workflows.forEach(function (entry) {
        var option = el(doc, "option", "", languageLabel(entry) + (entry.supported ? "" : " " + t("next.config.workflow_unsupported")));
        option.value = entry.id;
        option.disabled = !entry.supported;
        option.setAttribute("data-supported", entry.supported ? "true" : "false");
        select.appendChild(option);
      });
      select.value = previous || (state.entry && state.entry.id) || "";
      if (state.ui.workflowDesc) {
        var entry = state.entry;
        var text = entry ? (lang() === "en" ? entry.description : (entry.descriptionZh || entry.description)) : "";
        state.ui.workflowDesc.textContent = text || "";
        state.ui.workflowDesc.hidden = !text;
      }
      if (state.ui.classicLink) {
        var unsupported = !!(state.entry && !state.entry.supported);
        state.ui.classicLink.hidden = !unsupported;
        if (unsupported) state.ui.classicLink.textContent = t("next.config.open_classic_hint");
      }
    }

    function languageLabel(entry) {
      return lang() === "en" ? (entry.label || entry.id) : (entry.labelZh || entry.label || entry.id);
    }

    function renderProfileOptions() {
      var select = state.ui.profileSelect;
      if (!select) return;
      empty(select);
      var profiles = model.profilesOf(state.schema);
      if (!profiles.length) {
        var option = el(doc, "option", "", t("next.config.profile_fields"));
        option.value = "";
        select.appendChild(option);
        select.value = "";
        select.disabled = true;
        return;
      }
      select.disabled = false;
      profiles.forEach(function (profile) {
        var label = lang() === "en" ? (profile.label || profile.profile_id) : (profile.label_zh || profile.label || profile.profile_id);
        var opt = el(doc, "option", "", label + (profile.summary ? " — " + profile.summary : ""));
        opt.value = profile.profile_id;
        select.appendChild(opt);
      });
      select.value = state.profileId || "";
    }

    function renderMethodValues() {
      var container = state.ui.fields;
      if (!container) return;
      empty(container);
      state.ui.fieldInputs = {};
      if (!state.schema) return;
      var ids = model.levelIds(state.schema);
      ids.forEach(function (levelId) {
        var card = el(doc, "div", "acp-cfg-level");
        var levelLabel = levelId;
        var defs = model.levelsOf(state.schema);
        for (var i = 0; i < defs.length; i++) {
          if (defs[i].level_id === levelId) {
            levelLabel = lang() === "en" ? (defs[i].label || levelId) : (defs[i].label_zh || defs[i].label || levelId);
            break;
          }
        }
        card.appendChild(el(doc, "h4", "acp-cfg-level-title", levelLabel));
        var split = model.editableFields(state.schema, levelId);
        split.primary.forEach(function (field) {
          card.appendChild(buildFieldRow(levelId, field));
        });
        if (split.secondary.length) {
          var details = el(doc, "details", "acp-cfg-advanced");
          var summary = el(doc, "summary", "", t("next.config.advanced"));
          summary.setAttribute("data-i18n", "next.config.advanced");
          details.appendChild(summary);
          split.secondary.forEach(function (field) { details.appendChild(buildFieldRow(levelId, field)); });
          card.appendChild(details);
        }
        container.appendChild(card);
      });
    }

    function buildFieldRow(levelId, field) {
      var def = model.fieldDefinition(state.methodCatalog, field);
      var level = state.levels[levelId] || {};
      var engine = level.engine || "orca";
      var wrap = el(doc, "label", "acp-cfg-field");
      var label = el(doc, "span", "acp-cfg-label", model.fieldLabel(def, field, lang()));
      wrap.appendChild(label);
      var options = model.fieldOptions(state.methodCatalog, field, engine);
      var current = level[field] === undefined || level[field] === null ? "" : String(level[field]);
      var input;
      if (options.length && field !== "max_steps") {
        input = el(doc, "select", "acp-cfg-select");
        if (current === "" || options.indexOf(current) === -1) {
          var blank = el(doc, "option", "", current ? current : "—");
          blank.value = current;
          input.appendChild(blank);
        }
        options.forEach(function (value) {
          var opt = el(doc, "option", "", String(value));
          opt.value = String(value);
          input.appendChild(opt);
        });
        input.value = current;
        on(input, "change", function () { setLevelValue(levelId, field, input.value); });
      } else {
        input = el(doc, "input", "acp-cfg-input");
        input.type = field === "max_steps" ? "number" : "text";
        input.value = current;
        on(input, "input", function () {
          var raw = input.value;
          setLevelValue(levelId, field, field === "max_steps" ? Number(raw) || 0 : raw);
        });
      }
      input.setAttribute("data-field", field);
      input.setAttribute("data-level", levelId);
      wrap.appendChild(input);
      if (!state.ui.fieldInputs[levelId]) state.ui.fieldInputs[levelId] = {};
      state.ui.fieldInputs[levelId][field] = input;
      var help = model.fieldHelp(def, lang());
      if (help) {
        var helpNode = el(doc, "small", "acp-cfg-help", help);
        wrap.appendChild(helpNode);
      }
      return wrap;
    }

    function setLevelValue(levelId, field, value) {
      if (!state.levels[levelId]) state.levels[levelId] = {};
      state.levels[levelId][field] = value;
      if (field === "functional") {
        var locked = model.applySemanticLocks(state.levels, state.methodCatalog);
        state.levels = locked.levels;
        state.notes = locked.notes;
        renderMethodValues();
        renderSemanticNotes();
      }
      bumpRevision();
      renderReview();
      renderActions();
      scheduleDraftSave();
      scheduleValidation();
    }

    function renderSemanticNotes() {
      var list = state.ui.semanticNotes;
      if (!list) return;
      empty(list);
      (state.notes || []).forEach(function (note) {
        var text = t(note.key, note.vars || {});
        listItem(list, "acp-cfg-note-item", text);
      });
      list.hidden = !(state.notes && state.notes.length);
    }

    function renderStructure() {
      var summary = state.ui.structureSummary;
      if (!summary) return;
      if (state.parseStatus === "loading") {
        summary.textContent = t("next.config.parsing");
        summary.setAttribute("data-i18n", "next.config.parsing");
        return;
      }
      if (state.parseStatus === "error") {
        summary.textContent = t("next.config.structure_error", { message: describeError(state.parseError) });
        summary.setAttribute("role", "alert");
        return;
      }
      summary.removeAttribute && summary.removeAttribute("role");
      if (state.asset) {
        summary.textContent = t("next.config.structure_summary", {
          formula: state.asset.formula || "?",
          atoms: state.asset.atom_count || 0,
          charge: state.charge,
          multiplicity: state.multiplicity,
        });
        return;
      }
      summary.textContent = t("next.config.structure_hint");
      summary.setAttribute("data-i18n", "next.config.structure_hint");
    }

    function renderStateInputs() {
      if (state.ui.charge) state.ui.charge.value = String(state.charge);
      if (state.ui.multiplicity) state.ui.multiplicity.value = String(state.multiplicity);
      if (state.ui.nproc) state.ui.nproc.value = String(state.nproc);
      if (state.ui.mem) state.ui.mem.value = String(state.mem);
      if (state.ui.moleculeName) state.ui.moleculeName.value = String(state.moleculeName || "");
      if (state.ui.parityHint) {
        var check = model.validateState(state.charge, state.multiplicity, state.asset);
        var parityError = check.errors.filter(function (item) { return item.key === "next.config.error.multiplicity_parity"; })[0];
        state.ui.parityHint.textContent = parityError ? t(parityError.key, parityError.vars) : "";
        state.ui.parityHint.hidden = !parityError;
      }
    }

    function renderReview() {
      var summary = state.ui.summary;
      if (!summary) return;
      empty(summary);
      if (!state.asset || !state.entry) {
        summary.appendChild(el(doc, "dd", "acp-cfg-summary-empty", t("next.config.summary_empty")));
        return;
      }
      summaryRow(summary, "next.config.workflow_label", languageLabel(state.entry));
      var methodText = describeMethod();
      if (methodText) summaryRow(summary, "next.config.profile_label", methodText);
      summaryRow(summary, "next.config.charge", String(state.charge));
      summaryRow(summary, "next.config.multiplicity", String(state.multiplicity));
      summaryRow(summary, "next.config.nproc", String(state.nproc) + " · " + String(state.mem || ""));
      if (state.projectId) summaryRow(summary, "next.config.project", state.projectId);
    }

    function summaryRow(list, key, value) {
      var dt = el(doc, "dt", "", t(key));
      dt.setAttribute("data-i18n", key);
      var dd = el(doc, "dd", "", String(value == null ? "" : value));
      list.appendChild(dt);
      list.appendChild(dd);
    }

    function describeMethod() {
      if (!state.schema) return "";
      var ids = model.levelIds(state.schema);
      var parts = [];
      ids.forEach(function (levelId) {
        var level = state.levels[levelId] || {};
        var bits = [];
        if (level.functional) bits.push(String(level.functional));
        if (level.basis) bits.push(String(level.basis));
        if (level.gfn) bits.push(String(level.gfn));
        if (level.opt_level) bits.push(String(level.opt_level));
        if (bits.length) parts.push(bits.join(" / "));
      });
      return parts.join(" · ");
    }

    function renderValidation() {
      var status = state.ui.validationStatus;
      if (!status) return;
      var value = "";
      if (state.validation.status === "validating") value = t("next.config.validate_running");
      else if (state.validation.status === "ok") value = t("next.config.validate_ok");
      else if (state.validation.status === "invalid") value = t("next.config.validate_invalid");
      else if (state.validation.status === "error") value = state.validation.errors[0] ? state.validation.errors[0].text : "";
      status.textContent = value;
      status.hidden = !value;
      if (!state.ui.errors) return;
      empty(state.ui.errors);
      if (state.validation.status === "invalid" || state.validation.status === "error") {
        state.validation.errors.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-error", item.text); });
        state.validation.warnings.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-warning", item.text); });
      }
    }

    function renderClientErrors(errors) {
      if (!state.ui.errors) return;
      if (!errors || !errors.length) return;
      empty(state.ui.errors);
      errors.forEach(function (item) { listItem(state.ui.errors, "acp-cfg-error", t(item.key, item.vars || {})); });
    }

    function renderPreflight() {
      var status = state.ui.preflightStatus;
      var reasons = state.ui.preflightReasons;
      if (!status || !reasons) return;
      empty(reasons);
      if (state.preflight.status === "running") {
        status.textContent = t("next.config.preflighting");
        status.hidden = false;
        return;
      }
      if (state.preflight.status === "ok") {
        status.textContent = t("next.config.preflight_ok", { digest: String(state.preflight.fingerprint || "").slice(0, 23) });
        status.hidden = false;
        return;
      }
      if (state.preflight.status === "infeasible" || state.preflight.status === "error") {
        status.textContent = t("next.config.preflight_failed");
        status.hidden = false;
        (state.preflight.reasons || []).forEach(function (text) { listItem(reasons, "acp-cfg-error", String(text)); });
        return;
      }
      if (state.preflight.status === "blocked") {
        status.textContent = t("next.config.submit_blocked");
        status.hidden = false;
        return;
      }
      status.textContent = "";
      status.hidden = true;
    }

    function renderSubmitState() {
      var receipt = state.ui.receipt;
      if (!receipt) return;
      empty(receipt);
      if (state.submit.status === "error" && state.submit.error) {
        receipt.hidden = false;
        receipt.appendChild(el(doc, "p", "acp-cfg-error", state.submit.error));
        return;
      }
      if (state.submit.status !== "submitted" || !state.submit.receipt) {
        receipt.hidden = true;
        return;
      }
      receipt.hidden = false;
      var data = state.submit.receipt;
      receipt.appendChild(el(doc, "h4", "acp-cfg-receipt-title", t("next.config.receipt")));
      receipt.appendChild(el(doc, "p", "", t("next.config.receipt_body", {
        jobId: String(data.job_id || ""),
        status: String(data.status || ""),
        attempt: Number(data.attempt || 1),
      })));
      if (data.plan_digest) {
        receipt.appendChild(el(doc, "p", "acp-cfg-mono", t("next.config.receipt_digest", { digest: String(data.plan_digest) })));
      }
      var open = el(doc, "button", "acp-btn acp-btn--ghost", t("next.config.open_task"));
      open.type = "button";
      open.setAttribute("data-i18n", "next.config.open_task");
      on(open, "click", function () { deliverReceipt(data); });
      receipt.appendChild(open);
    }

    function renderActions() {
      if (!state.ui.preflightBtn || !state.ui.submitBtn) return;
      var busy = state.preflight.status === "running" || state.submit.status === "submitting";
      state.ui.preflightBtn.disabled = busy || !state.entry || !state.entry.supported;
      state.ui.submitBtn.disabled = !canSubmit() || busy;
      state.ui.preflightBtn.textContent = state.preflight.status === "running"
        ? t("next.config.preflighting")
        : t("next.config.validate");
      state.ui.submitBtn.textContent = state.submit.status === "submitting"
        ? t("next.config.submitting")
        : t("next.config.submit");
    }

    /* ── lifecycle ───────────────────────────────────────────────────── */

    function applySnapshot(snapshot) {
      state.snapshot = snapshot || null;
      var projectId = snapshot && snapshot.projectId ? String(snapshot.projectId) : "";
      var changed = projectId !== state.projectId;
      state.projectId = projectId;
      if (changed && !state.catalogLoading && state.methodCatalog) {
        loadDraftPointer().then(renderBanner);
      }
      var ref = snapshot && snapshot.structureRef;
      if (ref && ref.kind === "inline-xyz" && ref.text && state.parseStatus !== "ok") {
        state.structureText = String(ref.text);
        if (state.ui.structureText) state.ui.structureText.value = state.structureText;
        parseStructure();
      }
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl) throw new Error("task-config: mount host missing");
      if (!model) throw new Error("task-config: ACPNextTaskConfigModel missing");
      if (!requestFn) throw new Error("task-config: ACPClient.transport missing");
      if (!doc) throw new Error("task-config: document missing");
      state.host = hostEl;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      var contextApi = api.context || context;
      buildDom();
      if (contextApi && typeof contextApi.getSnapshot === "function") applySnapshot(contextApi.getSnapshot());
      if (contextApi && typeof contextApi.subscribe === "function") {
        var unsubscribe = contextApi.subscribe(function (snapshot) {
          if (state.disposed) return;
          applySnapshot(snapshot);
          renderReview();
        });
        listenerCleanup.push(unsubscribe);
      }
      var langHandler = function () { renderAll(); };
      if (doc.addEventListener) {
        doc.addEventListener("acp:langchange", langHandler);
        listenerCleanup.push(function () { doc.removeEventListener("acp:langchange", langHandler); });
      }
      renderAll();
      return loadCatalogs();
    }

    function updateContext(snapshot) {
      if (state.disposed || !state.mounted) return;
      applySnapshot(snapshot);
      renderReview();
    }

    function suspend() { state.suspended = true; }

    function resume() { state.suspended = false; renderAll(); }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      if (state.validation.timer) { clearTimeout(state.validation.timer); state.validation.timer = null; }
      if (state.draft.timer) { clearTimeout(state.draft.timer); state.draft.timer = null; }
      listenerCleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (e) { /* listener already gone */ }
      });
      if (state.host && typeof state.host.replaceChildren === "function") {
        try { state.host.replaceChildren(); } catch (e) { /* host gone */ }
      }
      state.host = null;
      state.ui = {};
      state.asset = null;
      state.levels = {};
      state.workflows = [];
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
    factory: createTaskConfig,
  };
})();
