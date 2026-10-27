/*
 * ACP Workbench — schema-driven capability form (M3-06 / G16).
 *
 * Owner: frontend/js/workbench/features/**. One panel renders the form of ONE
 * discovered capability, built from two platform facts and nothing else:
 *
 *   * the capability descriptor (discovery: `GET /api/v2/science/capabilities`);
 *   * its `acp2.ui-subset.v1` form (`GET /api/v2/science/capability-forms`),
 *     which the platform validated against the frozen keyword whitelist and
 *     binding table (`ACPWorkbench.contracts.uiSubset`).
 *
 * Contract rules this module enforces:
 *   * no markup from the network: every remote string (title, description,
 *     error message, capability id) is written through `textContent` on a
 *     `createElement` node. `innerHTML`/`outerHTML`/`insertAdjacentHTML` are
 *     never used, so a plugin-authored form can never inject DOM;
 *   * no open JSON Schema evaluation: values are validated by the shared
 *     subset validator, and a form that uses `$ref`, a combinator or an
 *     unknown major is a typed `unavailable` state (never rendered as an
 *     empty-but-submittable form);
 *   * no local authority: preflight goes through `ACPClient.science`, submit
 *     and approval through `ACPClient.researchCommands`. This module owns no
 *     fetch, no URL prefix and no approval state — without an approved record
 *     the submit stays disabled and says so;
 *   * the descriptor/form digests and every human edit are recorded in the
 *     proposal provenance (`human_edits` conforms to proposal.schema.json), so
 *     an approval binds the UI that produced the request;
 *   * a capability that is not active (plugin disabled/uninstalled/upgraded) is
 *     rendered read-only from the served frozen descriptor digest — the form
 *     is never re-enabled locally.
 *
 * Version: v20261027.1
 */
window.ACPCapabilityForm = (function () {
  "use strict";

  var PROVENANCE_SCHEMA = "acp2.ui.capability-form.provenance.v1";
  var VIEW_SCHEMA = "acp2.ui.capability-form.view.v1";
  var STATE = { AVAILABLE: "available", READ_ONLY: "read_only", UNAVAILABLE: "unavailable" };
  var PLATFORM_TRANSPORT_REQUIRED = "platform_transport_required";

  var deps = {
    science: null,
    commands: null,
    now: null,
    editorRef: null,
    i18n: null,
  };
  var domDoc = null;
  var rootEl = null;
  var state = {
    mounted: false,
    projectId: "",
    formsDoc: null,
    forms: [],
    readOnly: [],
    selected: null,
    values: {},
    edits: [],
    approval: null,
    preflight: null,
    viewModel: null,
    taskRef: null,
    view: null,
    busy: false,
    error: null,
  };

  // ------------------------------------------------------------------
  // plumbing
  // ------------------------------------------------------------------

  function init(next) {
    next = next || {};
    if (next.science) deps.science = next.science;
    if (next.commands) deps.commands = next.commands;
    if (typeof next.now === "function") deps.now = next.now;
    if (typeof next.editorRef === "string" && next.editorRef) deps.editorRef = next.editorRef;
    if (next.i18n) deps.i18n = next.i18n;
    return api;
  }

  function failure(code, message, extra) {
    extra = extra || {};
    var err = new Error(code + ": " + String(message == null ? "" : message));
    err.code = code;
    err.path = extra.path == null ? null : extra.path;
    err.problems = extra.problems || null;
    if (extra.cause) err.cause = extra.cause;
    return err;
  }

  function contract() {
    return (window.ACPWorkbench && ACPWorkbench.contracts &&
      ACPWorkbench.contracts.uiSubset) || null;
  }

  function requireContract() {
    var uiSubset = contract();
    if (!uiSubset) {
      throw failure("ui_subset_unknown_major", "the uiSubset contract is unavailable");
    }
    return uiSubset;
  }

  function tr(key, fallback, vars) {
    var fn = null;
    if (deps.i18n && typeof deps.i18n === "function") fn = deps.i18n;
    else if (typeof t === "function") fn = t;
    var value = fallback == null ? key : fallback;
    if (!fn) return value;
    var translated = fn(key, vars);
    if (translated === key) return value;
    var text = String(translated);
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        text = text.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return text;
  }

  function isoNow() {
    if (typeof deps.now === "function") {
      var value = deps.now();
      if (typeof value === "string") return value;
      if (value instanceof Date) return value.toISOString();
    }
    return new Date().toISOString();
  }

  function editorRef() {
    if (typeof deps.editorRef === "string" && deps.editorRef) return deps.editorRef;
    return "workbench-local";
  }

  function copy(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  /** Typed error projection: a platform error keeps its code, anything else is
   * an explicit `request_failed` instead of an untyped string. */
  function typed(error, fallbackCode) {
    if (error && typeof error.code === "string" && error.code) return error;
    var code = fallbackCode || "request_failed";
    var wrapped = failure(code, (error && error.message) || "request failed");
    wrapped.cause = error;
    return wrapped;
  }

  // ------------------------------------------------------------------
  // discovery
  // ------------------------------------------------------------------

  function availableForms() {
    return state.forms.filter(function (entry) { return entry.state === STATE.AVAILABLE; });
  }

  function formEntry(capabilityId) {
    for (var i = 0; i < state.forms.length; i += 1) {
      if (state.forms[i].capability_id === capabilityId) return state.forms[i];
    }
    return null;
  }

  function readOnlyEntry(capabilityId) {
    for (var i = 0; i < state.readOnly.length; i += 1) {
      if (state.readOnly[i].capability_id === capabilityId) return state.readOnly[i];
    }
    return null;
  }

  /** Load the capability-form discovery document (real HTTP client). */
  function load(scope, opts) {
    if (!deps.science || typeof deps.science.fetchCapabilityForms !== "function") {
      return Promise.reject(failure(PLATFORM_TRANSPORT_REQUIRED,
        "capability forms require the injected science client " +
        "(ACPCapabilityForm.init({science: ACPClient.science}))"));
    }
    return deps.science.fetchCapabilityForms(scope, opts).then(function (document_) {
      try {
        var uiSubset = requireContract();
        if (document_.subset_version !== uiSubset.SUBSET_VERSION) {
          throw failure("ui_subset_unknown_major", String(document_.subset_version));
        }
        if (!uiSubset.tablesMatch(document_.subset)) {
          throw failure("ui_subset_drift",
            "the served subset tables differ from this UI build");
        }
        state.formsDoc = document_;
        state.forms = document_.forms.slice();
        state.readOnly = document_.read_only.slice();
        state.projectId = (scope && scope.projectId) || "";
        state.error = null;
        state.preflight = null;
        state.viewModel = null;
        state.taskRef = null;
        state.view = null;
        var first = availableForms()[0];
        if (first) select(first.capability_id);
        else state.selected = null;
        render();
        return getState();
      } catch (error) {
        var invalid = typed(error, "ui_form_malformed");
        state.error = invalid;
        render();
        throw invalid;
      }
    }, function (error) {
      var typedError = typed(error, "capability_forms_unavailable");
      state.error = typedError;
      render();
      throw typedError;
    });
  }

  /** Select one capability from the served document (available or read-only). */
  function select(capabilityId) {
    var entry = formEntry(capabilityId);
    var historical = entry ? null : readOnlyEntry(capabilityId);
    if (!entry && !historical) {
      throw failure("ui_form_missing", String(capabilityId));
    }
    state.selected = entry || historical;
    state.values = entry ? defaultsFor(entry.form) : {};
    state.edits = [];
    state.preflight = null;
    state.viewModel = null;
    state.taskRef = null;
    state.view = null;
    state.error = null;
    render();
    return getState();
  }

  function defaultsFor(form) {
    var uiSubset = requireContract();
    var values = {};
    uiSubset.fieldModel(form).forEach(function (field) {
      if (field.default !== null && field.default !== undefined) values[field.name] = field.default;
    });
    return values;
  }

  function fields() {
    if (!state.selected || state.selected.state !== STATE.AVAILABLE) return [];
    return requireContract().fieldModel(state.selected.form);
  }

  function fieldByName(name) {
    var list = fields();
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].name === name) return list[i];
    }
    return null;
  }

  // ------------------------------------------------------------------
  // values + provenance
  // ------------------------------------------------------------------

  /** Set one value from the UI, recording the human edit immediately. */
  function setValue(name, value) {
    var field = fieldByName(name);
    if (!field) throw failure("ui_value_unknown_field", String(name), { path: String(name) });
    state.values[name] = value;
    recordEdit(field);
    return getState();
  }

  function recordEdit(field) {
    var index = -1;
    for (var i = 0; i < state.edits.length; i += 1) {
      if (state.edits[i].field_path === field.binding) { index = i; break; }
    }
    var entry = {
      edited_at: isoNow(),
      field_path: field.binding,
      editor_ref: editorRef(),
      note: noteFor(field),
    };
    if (index === -1) state.edits.push(entry);
    else state.edits[index] = entry;
  }

  function noteFor(field) {
    var entry = state.selected || {};
    return [
      "capability=" + String(entry.capability_id || ""),
      "task_request=" + String(field.task_request || ""),
      "descriptor_digest=" + String(entry.descriptor_digest || ""),
      "form_digest=" + String(entry.form_digest || ""),
    ].join("; ");
  }

  function humanEdits() {
    return state.edits.map(function (entry) {
      return {
        edited_at: entry.edited_at,
        field_path: entry.field_path,
        editor_ref: entry.editor_ref,
        note: entry.note,
      };
    });
  }

  /** Descriptor/form digests + human edits, for the proposal provenance.
   *
   * `human_edits` is the only place proposal.schema.json allows (it is
   * excluded from plan_definition_digest on purpose); the digest block is
   * returned alongside so a caller that owns a richer provenance document can
   * persist it verbatim.
   */
  function provenance() {
    var entry = state.selected || {};
    var document_ = state.formsDoc || {};
    return {
      schema: PROVENANCE_SCHEMA,
      capability_id: entry.capability_id || null,
      capability_source: entry.capability_source || null,
      capability_version: (entry.plugin && entry.plugin.version) || entry.version || null,
      descriptor_digest: entry.descriptor_digest || null,
      form_digest: entry.form_digest || null,
      form_origin: entry.form_origin || null,
      subset_version: document_.subset_version || null,
      subset_digest: document_.subset_digest || null,
      catalog_etag: document_.catalog_etag || null,
      edited_fields: state.edits.map(function (edit) { return edit.field_path; }),
      human_edits: humanEdits(),
    };
  }

  function approval(record) {
    state.approval = record || null;
    render();
    return getState();
  }

  // ------------------------------------------------------------------
  // preflight + submit (existing client chain)
  // ------------------------------------------------------------------

  function buildRequest() {
    var uiSubset = requireContract();
    if (!state.selected || state.selected.state !== STATE.AVAILABLE) {
      throw failure("ui_form_missing", "no active capability form is selected");
    }
    var built = uiSubset.buildRequestBody(state.selected.form, state.values);
    if (!built.ok) {
      throw failure(built.code || "ui_form_malformed",
        built.problems.length ? built.problems[0].message : "invalid form values",
        { problems: built.problems, path: built.problems.length ? built.problems[0].path : null });
    }
    return built;
  }

  function viewModelOf(preflight, built) {
    var entry = state.selected || {};
    var plan = (preflight && preflight.plan) || {};
    return {
      schema: VIEW_SCHEMA,
      capability_id: entry.capability_id || null,
      descriptor_digest: entry.descriptor_digest || null,
      form_digest: entry.form_digest || null,
      subset_version: (state.formsDoc || {}).subset_version || null,
      subset_digest: (state.formsDoc || {}).subset_digest || null,
      read_only: false,
      request: {
        workflow: (entry.form && entry.form.request && entry.form.request.workflow) || null,
        body: copy(built.body),
        fields: copy(built.fields),
      },
      plan_digest: plan.plan_digest || null,
      preflight_fingerprint: (preflight && preflight.preflight_fingerprint) || null,
      estimate: copy((preflight && preflight.estimate) || {}),
      provenance: provenance(),
    };
  }

  /** Zero-side-effect preflight of the built request, then the view model. */
  function preflight(scope, opts) {
    if (!deps.science || typeof deps.science.preflight !== "function") {
      return Promise.reject(failure(PLATFORM_TRANSPORT_REQUIRED,
        "preflight requires the injected science client"));
    }
    var built;
    try {
      built = buildRequest();
    } catch (error) {
      state.error = error;
      render();
      return Promise.reject(error);
    }
    state.busy = true;
    render();
    return deps.science.preflight(built.body, scope, opts).then(function (projection) {
      state.busy = false;
      state.preflight = projection;
      state.viewModel = viewModelOf(projection, built);
      state.error = null;
      render();
      return state.viewModel;
    }, function (error) {
      state.busy = false;
      state.error = typed(error, "preflight_failed");
      render();
      throw state.error;
    });
  }

  /** Submit the same normalised body through the approved-plan command chain.
   *
   * Only a real TaskRef is reported as submitted (research-commands.js owns
   * that check); without an approved record the modules refuse before any
   * request leaves the browser.
   */
  function submit(scope, opts) {
    opts = opts || {};
    if (!deps.commands || typeof deps.commands.submit !== "function") {
      return Promise.reject(failure(PLATFORM_TRANSPORT_REQUIRED,
        "submit requires the injected research commands " +
        "(ACPCapabilityForm.init({commands: ACPClient.researchCommands}))"));
    }
    var built;
    try {
      built = buildRequest();
    } catch (error) {
      state.error = error;
      render();
      return Promise.reject(error);
    }
    var record = opts.approval || state.approval;
    state.busy = true;
    render();
    return deps.commands.submit(scope, built.body, {
      approval: record,
      idempotencyKey: opts.idempotencyKey,
      timeoutMs: opts.timeoutMs,
      fetchImpl: opts.fetchImpl,
    }).then(function (result) {
      state.busy = false;
      state.taskRef = result;
      state.error = null;
      render();
      return result;
    }, function (error) {
      state.busy = false;
      state.error = typed(error, "submit_unconfirmed");
      render();
      throw state.error;
    });
  }

  /** Read the attempt view model of the submitted task (existing client). */
  function loadView(scope, opts) {
    if (!deps.science || typeof deps.science.fetchView !== "function") {
      return Promise.reject(failure(PLATFORM_TRANSPORT_REQUIRED,
        "view loading requires the injected science client"));
    }
    var taskRef = state.taskRef && state.taskRef.task_ref ? state.taskRef.task_ref : null;
    if (!taskRef) {
      return Promise.reject(failure("submit_unconfirmed", "no TaskRef is bound yet"));
    }
    var ref = {
      jobId: taskRef.job_id || taskRef.jobId || taskRef.task_id,
      attempt: taskRef.attempt || (state.taskRef && state.taskRef.attempt),
    };
    return deps.science.fetchView(ref, scope, opts).then(function (view) {
      state.view = view;
      render();
      return view;
    });
  }

  // ------------------------------------------------------------------
  // rendering (createElement + textContent only — never innerHTML)
  // ------------------------------------------------------------------

  function textNode(tag, text, className) {
    var node = domDoc.createElement(tag);
    if (className) node.className = className;
    node.textContent = text == null ? "" : String(text);
    return node;
  }

  function append(parent, child) {
    parent.appendChild(child);
    return child;
  }

  function renderNotice(parent, tone, code, message) {
    var box = append(parent, textNode("div", "", "capability-form-notice " + tone));
    box.setAttribute("data-code", String(code || ""));
    append(box, textNode("span", message, "capability-form-notice-text"));
    return box;
  }

  function renderSelect(parent) {
    var entries = state.forms.concat(state.readOnly);
    if (!entries.length) return;
    var wrap = append(parent, textNode("div", "", "capability-form-row"));
    append(wrap, textNode("label",
      tr("capabilityForm.capability", "Capability"), "capability-form-label"));
    var selectEl = domDoc.createElement("select");
    selectEl.id = "capability-form-capability";
    selectEl.className = "capability-form-select";
    entries.forEach(function (entry) {
      var option = domDoc.createElement("option");
      option.value = String(entry.capability_id);
      var label = String(entry.capability_id);
      if (entry.state === STATE.READ_ONLY) {
        label += " — " + tr("capabilityForm.read_only_short", "read-only");
      } else if (entry.state === STATE.UNAVAILABLE) {
        label += " — " + tr("capabilityForm.unavailable_short", "unavailable");
      }
      option.textContent = label;
      if (entry.state !== STATE.AVAILABLE) option.disabled = true;
      if (state.selected && state.selected.capability_id === entry.capability_id) {
        option.selected = true;
      }
      selectEl.appendChild(option);
    });
    selectEl.addEventListener("change", function (event) {
      try {
        select(String(event.target.value));
      } catch (error) {
        state.error = typed(error, "ui_form_missing");
        render();
      }
    });
    append(wrap, selectEl);
  }

  function renderReadOnly(parent, entry) {
    renderNotice(parent, "warn", "ui_form_read_only",
      tr("capabilityForm.read_only_note",
        "This capability is not active; the historical record is shown read-only."));
    var table = append(parent, textNode("dl", "", "capability-form-digests"));
    [
      [tr("capabilityForm.capability", "Capability"), entry.capability_id],
      [tr("capabilityForm.version", "Version"), entry.version || "—"],
      [tr("capabilityForm.descriptor_digest", "Descriptor digest"),
        entry.descriptor_digest || "—"],
    ].forEach(function (row) {
      append(table, textNode("dt", row[0]));
      append(table, textNode("dd", row[1]));
    });
  }

  function renderUnavailable(parent, entry) {
    var error = entry.error || {};
    renderNotice(parent, "error", error.code || "ui_form_missing",
      tr("capabilityForm.unavailable_note",
        "This capability's form is unavailable: {reason}",
        { reason: error.message || error.code || "ui_form_missing" }));
    var list = append(parent, textNode("ul", "", "capability-form-problems"));
    (error.params ? Object.keys(error.params) : []).forEach(function (key) {
      append(list, textNode("li", key + "=" + String(error.params[key])));
    });
  }

  function renderField(parent, field) {
    var row = append(parent, textNode("div", "", "capability-form-row"));
    var label = append(row, textNode("label",
      field.title + (field.required ? " *" : ""), "capability-form-label"));
    label.setAttribute("for", "capability-form-field-" + field.name);
    var input;
    if (Array.isArray(field.enum)) {
      input = domDoc.createElement("select");
      field.enum.forEach(function (value) {
        var option = domDoc.createElement("option");
        option.value = String(value);
        option.textContent = String(value);
        input.appendChild(option);
      });
    } else if (field.widget === "textarea") {
      input = domDoc.createElement("textarea");
    } else if (field.widget === "checkbox") {
      input = domDoc.createElement("input");
      input.setAttribute("type", "checkbox");
    } else {
      input = domDoc.createElement("input");
      input.setAttribute("type", field.type === "number" ? "number" : "text");
      if (field.minimum !== null) input.setAttribute("min", String(field.minimum));
      if (field.maximum !== null) input.setAttribute("max", String(field.maximum));
    }
    input.id = "capability-form-field-" + field.name;
    input.className = "capability-form-input";
    input.setAttribute("data-binding", field.binding || "");
    var current = state.values[field.name];
    if (field.widget === "checkbox") {
      input.checked = current === true;
    } else if (current !== undefined && current !== null) {
      input.value = String(current);
    }
    var commit = function (value) {
      try {
        setValue(field.name, value);
      } catch (error) {
        state.error = typed(error, "ui_value_type");
      }
      render();
    };
    if (field.widget === "checkbox") {
      input.addEventListener("change", function () {
        commit(input.checked === true);
      });
    } else {
      input.addEventListener("change", function () { commit(input.value); });
    }
    append(row, input);
    if (field.unit) {
      append(row, textNode("span",
        tr("capabilityForm.unit." + field.unit, field.unit) +
        (field.unit_quantity ? " (" + field.unit_quantity + ")" : ""),
        "capability-form-unit"));
    }
    if (field.description) {
      append(row, textNode("p", field.description, "capability-form-hint"));
    }
    return row;
  }

  function renderActions(parent) {
    var bar = append(parent, textNode("div", "", "capability-form-actions"));
    var preflightBtn = domDoc.createElement("button");
    preflightBtn.type = "button";
    preflightBtn.id = "capability-form-preflight";
    preflightBtn.className = "capability-form-button";
    preflightBtn.textContent = tr("capabilityForm.preflight", "Preflight");
    preflightBtn.disabled = state.busy;
    preflightBtn.addEventListener("click", function () {
      if (!state.projectId) return;
      Promise.resolve(preflight({ projectId: state.projectId })).catch(function () {});
    });
    append(bar, preflightBtn);

    var submitBtn = domDoc.createElement("button");
    submitBtn.type = "button";
    submitBtn.id = "capability-form-submit";
    submitBtn.className = "capability-form-button capability-form-submit";
    submitBtn.textContent = tr("capabilityForm.submit", "Submit");
    submitBtn.disabled = state.busy || !state.approval;
    submitBtn.addEventListener("click", function () {
      if (!state.approval || !state.projectId) return;
      Promise.resolve(submit({ projectId: state.projectId })).catch(function () {});
    });
    append(bar, submitBtn);
    if (!state.approval) {
      append(bar, textNode("span",
        tr("capabilityForm.approval_required",
          "An approved plan is required before submit"),
        "capability-form-hint"));
    }
  }

  function renderResult(parent) {
    var model = state.viewModel;
    if (!model) return;
    var box = append(parent, textNode("div", "", "capability-form-result"));
    var table = append(box, textNode("dl", "", "capability-form-digests"));
    [
      [tr("capabilityForm.plan_digest", "Plan digest"), model.plan_digest || "—"],
      [tr("capabilityForm.form_digest", "Form digest"), model.form_digest || "—"],
      [tr("capabilityForm.descriptor_digest", "Descriptor digest"),
        model.descriptor_digest || "—"],
      [tr("capabilityForm.subset", "UI subset"),
        String(model.subset_version || "") + " · " + String(model.subset_digest || "")],
      [tr("capabilityForm.feasible", "Feasible"),
        model.estimate && model.estimate.feasible === false ? "false" : "true"],
    ].forEach(function (row) {
      append(table, textNode("dt", row[0]));
      append(table, textNode("dd", row[1]));
    });
    var bindings = append(box, textNode("ul", "", "capability-form-fields"));
    (model.request.fields || []).forEach(function (field) {
      append(bindings, textNode("li",
        field.field + " → " + field.task_request +
        (field.unit ? " [" + field.unit + "]" : "")));
    });
  }

  function render() {
    if (!state.mounted || !rootEl) return;
    rootEl.textContent = "";
    if (state.error) {
      renderNotice(rootEl, "error", state.error.code || "request_failed",
        tr("capabilityForm.error." + String(state.error.code || "request_failed"),
          String(state.error.message || state.error.code || "request failed")));
    }
    if (!state.formsDoc) {
      if (!state.error) {
        renderNotice(rootEl, "muted", "idle",
          tr("capabilityForm.idle", "Load a project to discover capability forms"));
      }
      return;
    }
    renderSelect(rootEl);
    var entry = state.selected;
    if (!entry) {
      renderNotice(rootEl, "muted", "ui_form_missing",
        tr("capabilityForm.empty", "This project discovers no capability form"));
      return;
    }
    if (entry.state === STATE.READ_ONLY) {
      renderReadOnly(rootEl, entry);
      return;
    }
    if (entry.state === STATE.UNAVAILABLE) {
      renderUnavailable(rootEl, entry);
      return;
    }
    if (entry.form.title) {
      append(rootEl, textNode("h3", entry.form.title, "capability-form-title"));
    }
    if (entry.form.description) {
      append(rootEl, textNode("p", entry.form.description, "capability-form-description"));
    }
    fields().forEach(function (field) { renderField(rootEl, field); });
    renderActions(rootEl);
    renderResult(rootEl);
  }

  function mount(document_) {
    domDoc = document_ || (typeof document !== "undefined" ? document : null);
    if (!domDoc || typeof domDoc.getElementById !== "function") {
      throw failure("ui_form_malformed", "mount requires a document");
    }
    state.mounted = true;
    rootEl = domDoc.getElementById("capability-form-body");
    render();
    return api;
  }

  function dispose() {
    state.mounted = false;
    rootEl = null;
    domDoc = null;
  }

  /** Page integration: (re)load the forms of the selected project. */
  function setProject(projectId, opts) {
    if (typeof projectId !== "string" || !projectId) {
      return Promise.reject(failure("input_invalid", "projectId is required"));
    }
    return load({ projectId: projectId }, opts);
  }

  function getState() {
    return {
      mounted: state.mounted,
      projectId: state.projectId,
      forms: state.forms.map(function (entry) {
        return {
          capability_id: entry.capability_id,
          state: entry.state,
          capability_source: entry.capability_source,
          descriptor_digest: entry.descriptor_digest || null,
          form_digest: entry.form_digest || null,
          form_origin: entry.form_origin || null,
          error_code: entry.error ? entry.error.code : null,
        };
      }),
      read_only: state.readOnly.map(function (entry) { return copy(entry); }),
      selected: state.selected ? state.selected.capability_id : null,
      selected_state: state.selected ? state.selected.state : null,
      values: copy(state.values),
      fields: fields().map(function (field) { return copy(field); }),
      edits: humanEdits(),
      approval_bound: !!state.approval,
      preflight: state.preflight ? copy(state.preflight) : null,
      viewModel: state.viewModel ? copy(state.viewModel) : null,
      taskRef: state.taskRef ? copy(state.taskRef) : null,
      view: state.view ? copy(state.view) : null,
      busy: state.busy,
      error: state.error
        ? { code: state.error.code, message: String(state.error.message) }
        : null,
      subset_version: state.formsDoc ? state.formsDoc.subset_version : null,
      subset_digest: state.formsDoc ? state.formsDoc.subset_digest : null,
      catalog_etag: state.formsDoc ? state.formsDoc.catalog_etag : null,
    };
  }

  var api = {
    PROVENANCE_SCHEMA: PROVENANCE_SCHEMA,
    VIEW_SCHEMA: VIEW_SCHEMA,
    STATE: STATE,
    PLATFORM_TRANSPORT_REQUIRED: PLATFORM_TRANSPORT_REQUIRED,
    init: init,
    mount: mount,
    load: load,
    setProject: setProject,
    select: select,
    setValue: setValue,
    fields: fields,
    buildRequest: buildRequest,
    preflight: preflight,
    submit: submit,
    loadView: loadView,
    provenance: provenance,
    humanEdits: humanEdits,
    approval: approval,
    render: render,
    dispose: dispose,
    getState: getState,
  };
  return api;
})();

window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.features = ACPWorkbench.features || {};
ACPWorkbench.features.capabilityForm = window.ACPCapabilityForm;

// Page wiring: the same v1/v2 chokepoints every other feature uses. Nothing is
// fetched here — the project selector calls ACPCapabilityForm.setProject().
if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
  document.addEventListener("DOMContentLoaded", function () {
    try {
      if (!document.getElementById || !document.getElementById("capability-form-body")) return;
      if (window.ACPClient && window.ACPClient.science &&
          typeof apiV2 === "function" && typeof api === "function") {
        ACPClient.science.init({ v1: api, v2: apiV2 });
      }
      var next = {};
      if (window.ACPClient && window.ACPClient.science) next.science = ACPClient.science;
      if (window.ACPClient && window.ACPClient.researchCommands) {
        next.commands = ACPClient.researchCommands;
      }
      if (typeof t === "function") next.i18n = t;
      ACPCapabilityForm.init(next).mount(document);
    } catch (error) {
      if (typeof console !== "undefined" && console.warn) {
        console.warn("capability-form mount skipped:", error);
      }
    }
  });
}
