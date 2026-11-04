/*
 * ACP 3.0 module tests — capability-form (Track A / A-59 + A-60, P23).
 *
 * Node vm harness, no network, no browser. The REAL shared UI-subset contract
 * (`js/workbench/contracts/ui_subset.js`) is loaded next to the module, so the
 * value rules / request-body builder exercised here are the ones the product
 * uses. Only the client seams (science / research commands), the DOM and the
 * WorkspaceContext are fakes.
 *
 * Covered: rendering from the served document, four states, required / type /
 * bounds / enum rejection with per-field mapping, unknown-value and
 * unbound-field refusal, preflight flow + failure, approval-required, submit
 * unconfirmed/error (never fake success), generation guard, dispose/remount,
 * the "no fetch / no second schema evaluator / single global" source guards,
 * and A-60: 400/401/403/404/409/422/5xx/network error mapping, the 409
 * preflight refresh + re-review without replay, real-receipt-only success,
 * busy submit refusal and late-receipt dropping.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const MODULE = "js/modules/capability-form/module.js";
const SUBSET = "js/workbench/contracts/ui_subset.js";
const CSS = "css/modules/capability-form.css";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const flush = async () => { await sleep(0); await sleep(0); };

  // ------------------------------------------------------------------
  // fake DOM (records only what the module uses)
  // ------------------------------------------------------------------
  function makeEl(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      children: [],
      attributes: {},
      parentNode: null,
      className: "",
      id: "",
      textContent: "",
      value: "",
      checked: false,
      disabled: false,
      hidden: false,
      selected: false,
      type: "",
      style: {},
      validity: { badInput: false },
      _listeners: {},
      appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = el;
        el.children.push(child);
        return child;
      },
      removeChild(child) {
        const index = el.children.indexOf(child);
        if (index !== -1) {
          el.children.splice(index, 1);
          child.parentNode = null;
        }
        return child;
      },
      replaceChildren() {
        el.children.slice().forEach((child) => { child.parentNode = null; });
        el.children = [];
      },
      setAttribute(name, value) { el.attributes[name] = String(value); },
      getAttribute(name) { return name in el.attributes ? el.attributes[name] : null; },
      removeAttribute(name) { delete el.attributes[name]; },
      addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
      removeEventListener(type, fn) {
        const list = el._listeners[type] || [];
        const index = list.indexOf(fn);
        if (index !== -1) list.splice(index, 1);
      },
      dispatch(type, event) {
        (el._listeners[type] || []).slice().forEach((fn) => fn(event || { type }));
      },
      click() {
        if (el.disabled) return;
        el.dispatch("click", { type: "click", target: el });
      },
      querySelector() { return null; },
      querySelectorAll() { return []; }
    };
    return el;
  }

  function makeDoc() {
    return { createElement: (tag) => makeEl(tag) };
  }

  function collect(el, predicate, out) {
    out = out || [];
    if (!el) return out;
    if (predicate(el)) out.push(el);
    (el.children || []).forEach((child) => collect(child, predicate, out));
    return out;
  }

  function findByAttr(el, name, value) {
    return collect(el, (node) => typeof node.getAttribute === "function" &&
      node.getAttribute(name) === String(value))[0] || null;
  }

  function findAllByAttr(el, name, value) {
    return collect(el, (node) => typeof node.getAttribute === "function" &&
      node.getAttribute(name) === String(value));
  }

  function findByTag(el, tag) {
    return collect(el, (node) => node.tagName === String(tag).toUpperCase())[0] || null;
  }

  function findAllByTag(el, tag) {
    return collect(el, (node) => node.tagName === String(tag).toUpperCase());
  }

  function textOf(el) {
    if (!el) return "";
    let out = String(el.textContent == null ? "" : el.textContent);
    (el.children || []).forEach((child) => { out += " " + textOf(child); });
    return out;
  }

  function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function makeContext(projectId) {
    let snapshot = { projectId: projectId == null ? null : projectId, revision: 0 };
    const listeners = [];
    return {
      getSnapshot: () => snapshot,
      subscribe(fn) {
        listeners.push(fn);
        return () => {
          const index = listeners.indexOf(fn);
          if (index !== -1) listeners.splice(index, 1);
        };
      },
      emit(next) {
        snapshot = Object.assign({}, snapshot, next);
        listeners.slice().forEach((fn) => fn(snapshot));
      },
      listenerCount: () => listeners.length
    };
  }

  // ------------------------------------------------------------------
  // served document fixtures (validated by the REAL subset contract)
  // ------------------------------------------------------------------
  function makeForm(overrides) {
    const form = {
      schema: "acp2.ui.form.v1",
      schema_version: 1,
      subset_version: "acp2.ui-subset.v1",
      capability_id: "electronic.energy",
      title: "单点能（能力表单）",
      description: "平台下发的 acp2.ui.form.v1 文档",
      request: {
        surface: "job_submit",
        workflow: "singlepoint",
        template: {
          input: { source_type: "xyz_text", source: "" },
          method: { levels: { single_point: { engine: "orca" } } },
          resources: {}
        }
      },
      form_schema: {
        type: "object",
        additionalProperties: false,
        required: ["source"],
        properties: {
          source: {
            type: "string",
            title: "结构",
            minLength: 4,
            "x-acp": { binding: "task.structure.source", widget: "textarea" }
          },
          method: {
            type: "string",
            title: "方法",
            enum: ["B3LYP", "wB97X-D4"],
            "x-acp": { binding: "task.level.method", widget: "select" }
          },
          charge: {
            type: "integer",
            title: "电荷",
            default: 0,
            minimum: -10,
            maximum: 10,
            "x-acp": { binding: "task.charge", widget: "number" }
          },
          nproc: {
            type: "integer",
            title: "核数",
            default: 1,
            minimum: 1,
            maximum: 128,
            "x-acp": { binding: "task.resources.nproc", widget: "number" }
          }
        }
      },
      form_digest: "sha256:" + "c".repeat(64)
    };
    if (overrides) Object.assign(form, overrides);
    return form;
  }

  function entryFor(form) {
    return {
      capability_id: form.capability_id,
      state: "available",
      capability_source: "builtin",
      descriptor_digest: "sha256:" + "9".repeat(64),
      form_digest: form.form_digest,
      form_origin: "platform",
      form: form
    };
  }

  function makeFormsDoc(subset, forms, readOnly) {
    return {
      schema: subset.FORMS_SCHEMA,
      schema_version: 1,
      subset_version: subset.SUBSET_VERSION,
      subset_digest: "sha256:" + "a".repeat(64),
      subset: subset.subsetTable(),
      catalog_etag: "sha256:" + "b".repeat(64),
      forms: forms || [],
      read_only: readOnly || []
    };
  }

  function preflightProjection(overrides) {
    return Object.assign({
      schema_version: 1,
      executed: false,
      metering_consumed: false,
      transfers_attempted: false,
      plan: { workflow: "singlepoint", plan_digest: "sha256:" + "d".repeat(64) },
      preflight_fingerprint: "sha256:" + "d".repeat(64),
      estimate: { feasible: true, nproc: 2 }
    }, overrides || {});
  }

  function makeScience(fetchForms, preflightImpl) {
    const calls = { forms: [], preflight: [] };
    return {
      calls,
      fetchCapabilityForms(scope, opts) {
        calls.forms.push({ scope, opts });
        return fetchForms(scope, opts);
      },
      preflight(body, scope, opts) {
        calls.preflight.push({ body, scope, opts });
        if (preflightImpl) return preflightImpl(body, scope, opts);
        return Promise.resolve(preflightProjection());
      }
    };
  }

  function makeCommands(impl) {
    const calls = { submit: [] };
    return {
      calls,
      submit(scope, body, opts) {
        calls.submit.push({ scope, body, opts });
        if (impl) return impl(scope, body, opts);
        return Promise.resolve({
          job_id: "job-1",
          attempt: 1,
          task_ref: { job_id: "job-1", attempt: 1 },
          status: "QUEUED",
          plan_digest: opts && opts.approval ? opts.approval.plan_digest : null
        });
      }
    };
  }

  function setup(options) {
    options = options || {};
    const document_ = makeDoc();
    const sandbox = makeSandbox({
      document: document_,
      console: { log() {}, warn() {}, error() {} },
      location: { search: "" }
    });
    if (options.contract !== false) loadScript(SUBSET, sandbox);
    loadScript(MODULE, sandbox);
    const subset = sandbox.ACPWorkbench ? sandbox.ACPWorkbench.contracts.uiSubset : null;
    const science = options.science || makeScience(
      () => Promise.resolve(makeFormsDoc(subset, [entryFor(makeForm())]))
    );
    const commands = options.commands || makeCommands();
    const context = "context" in options ? options.context : makeContext("proj-1");
    const instance = sandbox.ACPNextModules["capability-form"].factory({
      context: context,
      science: science,
      commands: commands
    });
    const hostEl = makeEl("div");
    hostEl.ownerDocument = document_;
    return {
      doc: document_,
      sandbox,
      subset,
      science,
      commands,
      context,
      instance,
      host: hostEl
    };
  }

  async function mount(env, initial) {
    await env.instance.mount(env.host, initial);
    await flush();
  }

  function fillValid(instance) {
    instance.setValue("source", "O 0 0 0");
    instance.setValue("method", "B3LYP");
    instance.setValue("charge", 2);
  }

  function approvedRecord(overrides) {
    return Object.assign({
      status: "approved",
      approval_ref: "ap-1",
      revision: 2,
      plan_digest: "sha256:" + "d".repeat(64)
    }, overrides || {});
  }

  // ------------------------------------------------------------------
  // 1. rendering
  // ------------------------------------------------------------------
  await test("capability-form: registers beside ACPNextModules and renders the served schema", async () => {
    const env = setup();
    const entry = env.sandbox.ACPNextModules["capability-form"];
    assert(entry && entry.descriptor && typeof entry.factory === "function", "module registered");
    assertEq(entry.descriptor.id, "capability-form");
    assertEq(entry.descriptor.interfaceVersion, 1);
    const acpGlobals = Object.keys(env.sandbox).filter((key) => /^ACP/.test(key)).sort();
    assertEq(acpGlobals, ["ACPNextModules", "ACPWorkbench"],
      "no page global besides the module table + shared contract namespace");
    assert(env.sandbox.ACPCapabilityForm === undefined,
      "the classic page feature is not instantiated");

    await mount(env);
    assertEq(env.instance.getState().phase, "ready");
    assertEq(env.host.children[0].getAttribute("data-acp-module-state"), "ready");
    assertEq(env.instance.getState().fields.map((field) => field.name),
      ["source", "method", "charge", "nproc"]);

    const sourceRow = findByAttr(env.host, "data-acp-field", "source");
    assertEq(sourceRow.getAttribute("data-acp-binding"), "task.structure.source");
    const textarea = findByTag(sourceRow, "TEXTAREA");
    assert(textarea, "text widget rendered from x-acp.widget");
    assertEq(textarea.getAttribute("data-acp-input"), "source");
    assert(textOf(sourceRow).indexOf("必填") !== -1, "required marker rendered");

    const chargeRow = findByAttr(env.host, "data-acp-field", "charge");
    const chargeInput = findByTag(chargeRow, "INPUT");
    assertEq(chargeInput.getAttribute("type"), "number");
    assertEq(chargeInput.getAttribute("min"), "-10");
    assertEq(chargeInput.getAttribute("max"), "10");
    assertEq(chargeInput.getAttribute("step"), "1");
    assertEq(chargeInput.value, "0", "schema default applied");

    const methodRow = findByAttr(env.host, "data-acp-field", "method");
    const methodSelect = findByTag(methodRow, "SELECT");
    assertEq(methodSelect.children.map((option) => option.value), ["", "B3LYP", "wB97X-D4"]);

    assert(textOf(env.host).indexOf("单点能（能力表单）") !== -1,
      "form title rendered as text from the served document");
    assertEq(env.science.calls.forms.length, 1);
    assertEq(env.science.calls.forms[0].scope.projectId, "proj-1");
  });

  // ------------------------------------------------------------------
  // 2. four states
  // ------------------------------------------------------------------
  await test("capability-form: empty / loading / ready / error(+retry) four states", async () => {
    const empty = setup({ context: makeContext(null) });
    await empty.instance.mount(empty.host);
    await flush();
    assertEq(empty.instance.getState().phase, "empty");
    assertEq(empty.instance.getState().empty_reason, "no_project");
    assertEq(empty.science.calls.forms.length, 0, "no fetch without a project");
    assertEq(empty.host.children[0].getAttribute("data-acp-module-state"), "empty");

    const noForms = setup();
    noForms.science.fetchCapabilityForms = () =>
      Promise.resolve(makeFormsDoc(noForms.subset, [], []));
    await mount(noForms);
    assertEq(noForms.instance.getState().phase, "empty");
    assertEq(noForms.instance.getState().empty_reason, "no_forms");

    const gate = deferred();
    const loading = setup({ science: makeScience(() => gate.promise) });
    await loading.instance.mount(loading.host);
    assertEq(loading.instance.getState().phase, "loading");
    assertEq(loading.host.children[0].getAttribute("data-acp-module-state"), "loading");
    gate.resolve(makeFormsDoc(loading.subset, [entryFor(makeForm())]));
    await flush();
    assertEq(loading.instance.getState().phase, "ready");

    let attempt = 0;
    const failed = setup({
      science: makeScience(() => {
        attempt += 1;
        if (attempt === 1) {
          return Promise.reject({ code: "capability_forms_unavailable", message: "service down" });
        }
        return Promise.resolve(makeFormsDoc(failed.subset, [entryFor(makeForm())]));
      })
    });
    await mount(failed);
    assertEq(failed.instance.getState().phase, "error");
    assertEq(failed.instance.getState().error.code, "capability_forms_unavailable");
    assertEq(failed.host.children[0].getAttribute("data-acp-error-code"),
      "capability_forms_unavailable");
    const retry = findByAttr(failed.host, "data-acp-action", "retry");
    assert(retry, "retry button rendered in the error state");
    retry.click();
    await flush();
    assertEq(failed.instance.getState().phase, "ready");
    assertEq(attempt, 2, "retry really re-runs the loader");
  });

  // ------------------------------------------------------------------
  // 3. value validation (shared ui-subset rules + per-field mapping)
  // ------------------------------------------------------------------
  await test("capability-form: required / type / bounds / enum errors are mapped by field", async () => {
    const env = setup();
    await mount(env);

    let check = env.instance.validate();
    assertEq(check.ok, false);
    assertEq(check.code, "ui_value_required");
    assertEq(check.errors.source[0].code, "ui_value_required");

    env.instance.setValue("source", "O 0 0 0");
    env.instance.setValue("charge", 1.5);
    check = env.instance.validate();
    assertEq(check.errors.charge[0].code, "ui_value_type", "integer type");

    env.instance.setValue("charge", 11);
    env.instance.validate();
    assertEq(env.instance.getState().errors.charge[0].code, "ui_value_bounds", "maximum");
    env.instance.setValue("charge", -11);
    assertEq(env.instance.validate().errors.charge[0].code, "ui_value_bounds", "minimum");

    env.instance.setValue("charge", 0);
    env.instance.setValue("method", "HF");
    assertEq(env.instance.validate().errors.method[0].code, "ui_value_enum", "enum");

    env.instance.setValue("method", "B3LYP");
    env.instance.setValue("source", "O");
    assertEq(env.instance.validate().errors.source[0].code, "ui_value_length", "minLength");
    env.instance.setValue("source", "O 0 0 0");

    check = env.instance.validate();
    assert(check.ok, JSON.stringify(check.problems));
    assertEq(check.values.nproc, 1, "optional schema default applied by the shared validator");
    assertEq(env.instance.getState().validation.ok, true);
    assertEq(env.instance.getState().errors, {});
  });

  await test("capability-form: an invalid form never reaches preflight and renders field errors", async () => {
    const env = setup();
    await mount(env);

    let caught = null;
    try {
      await env.instance.preflight({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "ui_value_required", "typed local refusal");
    assertEq(env.science.calls.preflight.length, 0, "no preflight request for an invalid form");
    assertEq(env.instance.getState().preflight_state, "error");
    assertEq(env.instance.getState().preflight, null);

    const fieldError = findByAttr(env.host, "data-acp-field-error", "source");
    assertEq(fieldError.getAttribute("data-acp-code"), "ui_value_required");
    assert(textOf(fieldError).length > 0, "field error text rendered");
    const result = findByAttr(env.host, "data-acp-preflight", "error");
    assert(result, "preflight error state visible");
    assertEq(findByAttr(env.host, "data-acp-action", "submit").disabled, true);
  });

  // ------------------------------------------------------------------
  // 4. unknown values / unbound fields are explicit refusals
  // ------------------------------------------------------------------
  await test("capability-form: unknown values and unbound fields are rejected, not dropped", async () => {
    const env = setup();
    await mount(env);

    let caught = null;
    try {
      env.instance.setValue("magic", 1);
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "ui_value_unknown_field");
    assertEq(Object.prototype.hasOwnProperty.call(env.instance.getState().values, "magic"), false,
      "unknown value is not stored");
    assertEq(env.instance.getState().errors.magic[0].code, "ui_value_unknown_field");
    assert(findByAttr(env.host, "data-acp-action-error", "ui_value_unknown_field"),
      "unknown field refusal is visible");

    const dry = env.instance.validate({ source: "O 0 0 0", magic: 1 });
    assertEq(dry.ok, false);
    assertEq(dry.code, "ui_value_unknown_field");
    assertEq(dry.errors.magic[0].code, "ui_value_unknown_field",
      "unknown names are reported by path, never dropped");

    const unbound = setup();
    const badForm = makeForm();
    delete badForm.form_schema.properties.charge["x-acp"];
    unbound.science.fetchCapabilityForms = () =>
      Promise.resolve(makeFormsDoc(unbound.subset, [entryFor(badForm)]));
    await mount(unbound);
    assertEq(unbound.instance.getState().phase, "error");
    assertEq(unbound.instance.getState().error.code, "ui_binding_missing");
    assertEq(findAllByAttr(unbound.host, "data-acp-input").length, 0,
      "a form with an unbound field is not rendered as inputs");

    const keyword = setup();
    const badKeyword = makeForm();
    badKeyword.form_schema.oneOf = [{ type: "object" }];
    keyword.science.fetchCapabilityForms = () =>
      Promise.resolve(makeFormsDoc(keyword.subset, [entryFor(badKeyword)]));
    await mount(keyword);
    assertEq(keyword.instance.getState().phase, "error");
    assertEq(keyword.instance.getState().error.code, "ui_keyword_rejected");
  });

  // ------------------------------------------------------------------
  // 5. preflight
  // ------------------------------------------------------------------
  await test("capability-form: preflight carries bound values/digests and edits make it stale", async () => {
    const env = setup();
    await mount(env);
    fillValid(env.instance);

    const view = await env.instance.preflight({ projectId: "proj-1" });
    assertEq(env.science.calls.preflight.length, 1);
    const sent = env.science.calls.preflight[0];
    assertEq(sent.scope.projectId, "proj-1");
    assertEq(sent.body.workflow, "singlepoint");
    assertEq(sent.body.input.source, "O 0 0 0");
    assertEq(sent.body.input.charge, 2);
    assertEq(sent.body.method.levels.single_point.engine, "orca");
    assertEq(sent.body.method.levels.single_point.functional, "B3LYP");
    assertEq(view.plan_digest, "sha256:" + "d".repeat(64));
    assertEq(view.form_digest, "sha256:" + "c".repeat(64));
    assertEq(view.request.fields.map((field) => field.binding).sort(),
      ["task.charge", "task.level.method", "task.resources.nproc", "task.structure.source"]);

    let state = env.instance.getState();
    assertEq(state.preflight_state, "ok");
    assertEq(state.preflight.plan_digest, "sha256:" + "d".repeat(64));
    assert(findByAttr(env.host, "data-acp-preflight", "ok"), "preflight ok visible");

    env.instance.setValue("charge", 3);
    state = env.instance.getState();
    assertEq(state.preflight_state, "stale");
    assert(findByAttr(env.host, "data-acp-preflight", "stale"), "stale preflight visible");
    assertEq(findByAttr(env.host, "data-acp-action", "submit")
      .getAttribute("data-acp-submit-blocked"), "preflight");

    let caught = null;
    try {
      await env.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "preflight_stale");
    assertEq(env.commands.calls.submit.length, 0, "a stale preflight never submits");
  });

  await test("capability-form: a failed preflight is visible and keeps submit blocked", async () => {
    const env = setup();
    env.science.preflight = () => Promise.reject({
      code: "preflight_failed",
      message: "budget rejected"
    });
    await mount(env);
    fillValid(env.instance);
    env.instance.approval(approvedRecord());

    let caught = null;
    try {
      await env.instance.preflight({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "preflight_failed");
    assertEq(env.instance.getState().preflight_state, "error");
    assertEq(env.instance.getState().preflight, null);
    assert(findByAttr(env.host, "data-acp-preflight", "error"), "preflight error visible");
    assertEq(findByAttr(env.host, "data-acp-action", "submit").disabled, true);

    try {
      await env.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "preflight_required", "no submit without a good preflight");
    assertEq(env.commands.calls.submit.length, 0);
  });

  // ------------------------------------------------------------------
  // 6. approval + submit
  // ------------------------------------------------------------------
  await test("capability-form: approval-required is visible; submit needs preflight + approved record", async () => {
    const env = setup();
    await mount(env);
    fillValid(env.instance);
    await env.instance.preflight({ projectId: "proj-1" });

    let state = env.instance.getState();
    assertEq(state.approval_status, "required");
    const notice = findByAttr(env.host, "data-acp-approval", "required");
    assert(notice, "approval-required notice rendered");
    assert(textOf(notice).indexOf("需要审批") !== -1, "approval copy is explicit");
    const submitButton = findByAttr(env.host, "data-acp-action", "submit");
    assertEq(submitButton.disabled, true);
    assertEq(submitButton.getAttribute("data-acp-submit-blocked"), "approval");

    let caught = null;
    try {
      await env.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "approval_required");
    assertEq(env.commands.calls.submit.length, 0, "no submit without an approval");

    env.instance.approval({ status: "pending", approval_ref: "ap-1", revision: 1 });
    assertEq(env.instance.getState().approval_status, "not_approved");
    assertEq(findByAttr(env.host, "data-acp-approval", "not_approved") !== null, true);
    try {
      await env.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "approval_not_granted");
    assertEq(env.commands.calls.submit.length, 0);

    env.instance.approval(approvedRecord({ plan_digest: "sha256:" + "e".repeat(64) }));
    assertEq(env.instance.getState().approval_status, "mismatch",
      "an approval bound to another plan is not granted");
    assert(findByAttr(env.host, "data-acp-approval", "mismatch"),
      "plan-digest mismatch is visible");
    try {
      await env.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "approved_plan_digest_mismatch");
    assertEq(env.commands.calls.submit.length, 0,
      "a mismatched approval never reaches the submit client");

    env.instance.approval(approvedRecord());
    assertEq(env.instance.getState().approval_status, "granted");
    assertEq(findByAttr(env.host, "data-acp-action", "submit").disabled, false);

    const result = await env.instance.submit({ projectId: "proj-1" }, { idempotencyKey: "k-1" });
    assertEq(env.commands.calls.submit.length, 1);
    const sent = env.commands.calls.submit[0];
    assertEq(sent.scope.projectId, "proj-1");
    assertEq(sent.body.workflow, "singlepoint");
    assertEq(sent.body.input.source, "O 0 0 0");
    assertEq(sent.body.plan_digest, "sha256:" + "d".repeat(64),
      "the preflight plan digest is attached for the client binding check");
    assertEq(sent.opts.approval.status, "approved");
    assertEq(sent.opts.idempotencyKey, "k-1");
    assertEq(result.job_id, "job-1");

    state = env.instance.getState();
    assertEq(state.submit_state, "submitted");
    assertEq(state.task_ref.jobId, "job-1");
    assertEq(state.task_ref.attempt, 1);
    assert(findByAttr(env.host, "data-acp-job-id", "job-1"), "receipt rendered from the real TaskRef");
    assertEq(findByAttr(env.host, "data-acp-submit", "submitted") !== null, true);
  });

  await test("capability-form: submit shows unconfirmed/error instead of fake success", async () => {
    // 1) The command resolves without a real TaskRef: never "submitted".
    const noRef = setup({ commands: makeCommands(() => Promise.resolve({ job_id: null })) });
    await mount(noRef);
    fillValid(noRef.instance);
    await noRef.instance.preflight({ projectId: "proj-1" });
    noRef.instance.approval(approvedRecord());
    let caught = null;
    try {
      await noRef.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "submit_unconfirmed");
    assertEq(noRef.instance.getState().submit_state, "unconfirmed");
    assertEq(noRef.instance.getState().task_ref, null);
    const unconfirmed = findByAttr(noRef.host, "data-acp-submit", "unconfirmed");
    assert(unconfirmed, "unconfirmed state visible");
    assert(textOf(unconfirmed).indexOf("未确认") !== -1);

    // 2) A transport failure with unknown outcome is unconfirmed, not success.
    const unknown = setup({
      commands: makeCommands(() => Promise.reject({
        code: "submit_unconfirmed",
        message: "socket closed"
      }))
    });
    await mount(unknown);
    fillValid(unknown.instance);
    await unknown.instance.preflight({ projectId: "proj-1" });
    unknown.instance.approval(approvedRecord());
    try {
      await unknown.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "submit_unconfirmed");
    assertEq(unknown.instance.getState().submit_state, "unconfirmed");

    // 3) A definite 4xx refusal is an error, not unconfirmed.
    const refused = setup({
      commands: makeCommands(() => Promise.reject({
        code: "input_invalid",
        message: "bad body",
        status: 422
      }))
    });
    await mount(refused);
    fillValid(refused.instance);
    await refused.instance.preflight({ projectId: "proj-1" });
    refused.instance.approval(approvedRecord());
    try {
      await refused.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "input_invalid");
    assertEq(refused.instance.getState().submit_state, "error");
    assert(findByAttr(refused.host, "data-acp-submit", "error"), "error state visible");

    // 4) Submit before preflight is refused locally.
    const early = setup();
    await mount(early);
    fillValid(early.instance);
    early.instance.approval(approvedRecord());
    try {
      await early.instance.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "preflight_required");
    assertEq(early.commands.calls.submit.length, 0);
  });

  // ------------------------------------------------------------------
  // 6b. A-60: submit error mapping / read re-review / busy / receipt
  // ------------------------------------------------------------------
  async function submitAgainst(rejection) {
    const env = setup({ commands: makeCommands(() => Promise.reject(rejection)) });
    await mount(env);
    fillValid(env.instance);
    await env.instance.preflight({ projectId: "proj-1" });
    env.instance.approval(approvedRecord());
    const caught = await env.instance.submit({ projectId: "proj-1" }).catch((error) => error);
    return { env, caught };
  }

  function submitResultBox(env, state) {
    return collect(env.host, (node) => typeof node.getAttribute === "function" &&
      node.getAttribute("data-acp-submit-state") === String(state) &&
      String(node.className || "").indexOf("acp-capf-submit") !== -1)[0] || null;
  }

  function assertSubmitBox(env, state, displayCode, category) {
    const box = submitResultBox(env, state);
    assert(box, "submit result carries data-acp-submit-state=" + state);
    assertEq(box.getAttribute("data-acp-submit"), state, "legacy data-acp-submit stays in sync");
    assertEq(box.getAttribute("data-acp-error-code"), displayCode);
    if (category) assertEq(box.getAttribute("data-acp-error-category"), category);
    assertEq(env.host.children[0].getAttribute("data-acp-submit-state"), state,
      "module root exposes the stable submit state");
    assertEq(env.host.children[0].getAttribute("data-acp-error-code"), displayCode,
      "module root mirrors the readable submit error code");
    if (displayCode) {
      assert(findAllByAttr(env.host, "data-acp-error-code", displayCode).indexOf(box) !== -1,
        "the readable error code sits on the submit result");
    }
    return box;
  }

  await test("capability-form: submit maps 400 input_invalid and 422 validation to readable errors", async () => {
    const cases = [
      {
        rejection: { code: "input_invalid", status: 400, message: "bad body" },
        display: "input_invalid", category: "input", text: "输入无效"
      },
      {
        rejection: { code: "input_invalid", status: 422, message: "field not allowed" },
        display: "validation_failed", category: "validation", text: "校验"
      }
    ];
    for (const item of cases) {
      const { env, caught } = await submitAgainst(item.rejection);
      assertEq(caught.code, item.rejection.code, "the raw code stays on the thrown error");
      assertEq(caught.displayCode, item.display);
      const state = env.instance.getState();
      assertEq(state.submit_state, "error");
      assertEq(state.submit_error.code, item.rejection.code);
      assertEq(state.submit_error.display_code, item.display);
      assertEq(state.submit_error.category, item.category);
      assertEq(state.submit_error.status, item.rejection.status);
      assertEq(state.task_ref, null, "a definite refusal never creates a TaskRef");
      assertEq(env.commands.calls.submit.length, 1, "a definite refusal is not retried");
      const box = assertSubmitBox(env, "error", item.display, item.category);
      assert(textOf(box).indexOf(item.text) !== -1, "readable copy for " + item.display);
    }
  });

  await test("capability-form: submit maps 401/403 to access_denied and 404 to not_found", async () => {
    const cases = [
      {
        rejection: { status: 401, message: "authentication required" },
        display: "access_denied", category: "access", text: "无权", status: 401
      },
      {
        rejection: {
          code: "access_denied", status: 403,
          message: "access_denied: principal not allowed",
          detail: { code: "access_denied", message: "principal not allowed" }
        },
        display: "access_denied", category: "access", text: "无权",
        status: 403, detailText: "principal not allowed"
      },
      {
        rejection: { code: "not_found", status: 404, message: "project not found" },
        display: "not_found", category: "not_found", text: "不存在", status: 404
      }
    ];
    for (const item of cases) {
      const { env, caught } = await submitAgainst(item.rejection);
      assertEq(caught.displayCode, item.display);
      const state = env.instance.getState();
      assertEq(state.submit_state, "error");
      assertEq(state.submit_error.display_code, item.display);
      assertEq(state.submit_error.category, item.category);
      assertEq(state.submit_error.status, item.status);
      assertEq(state.task_ref, null);
      assertEq(env.commands.calls.submit.length, 1);
      const box = assertSubmitBox(env, "error", item.display, item.category);
      assert(textOf(box).indexOf(item.text) !== -1, "readable copy for " + item.display);
      assert(textOf(box).indexOf("[object Object]") === -1,
        "a structured detail never leaks as a stringified object");
      if (item.detailText) {
        assert(textOf(box).indexOf(item.detailText) !== -1,
          "the server detail sentence is shown to the user");
        assertEq(state.submit_error.message, item.detailText,
          "getState exposes the readable message, not the detail object");
      }
    }
  });

  await test("capability-form: 409 refreshes preflight, clears approval, requires re-review and never replays", async () => {
    const env = setup({
      commands: makeCommands(() => Promise.reject({
        code: "approval_conflict", status: 409, message: "approval changed on the server"
      }))
    });
    await mount(env);
    fillValid(env.instance);
    await env.instance.preflight({ projectId: "proj-1" });
    env.instance.approval(approvedRecord());
    const before = env.science.calls.preflight.length;

    const caught = await env.instance.submit({ projectId: "proj-1" }).catch((error) => error);
    assertEq(caught.code, "approval_conflict");
    assertEq(caught.displayCode, "revision_conflict");
    assertEq(caught.submitState, "conflict");

    let state = env.instance.getState();
    assertEq(state.submit_state, "conflict");
    assertEq(state.submit_error.display_code, "revision_conflict");
    assertEq(state.submit_error.category, "conflict");
    assertEq(state.submit_error.status, 409);
    assertEq(state.approval, null, "the stale approved record is dropped");
    assertEq(state.approval_status, "required", "a fresh review is required");
    assertEq(state.preflight_state, "ok", "the preflight was refreshed for the current plan");
    assertEq(env.science.calls.preflight.length, before + 1, "exactly one preflight refresh");
    assertEq(env.commands.calls.submit.length, 1, "the failed submit is never replayed");

    const box = assertSubmitBox(env, "conflict", "revision_conflict", "conflict");
    assert(textOf(box).indexOf("重新审批") !== -1, "conflict copy asks for re-approval");
    assert(findByAttr(box, "data-acp-submit-hint", "conflict"), "explicit no-replay hint");
    const button = findByAttr(env.host, "data-acp-action", "submit");
    assertEq(button.disabled, true);
    assertEq(button.getAttribute("data-acp-submit-blocked"), "conflict");
    await flush();
    assertEq(env.commands.calls.submit.length, 1, "still no replay after a tick");

    // A fresh approved record re-reviews the refreshed plan; this is a NEW action.
    env.instance.approval(approvedRecord());
    state = env.instance.getState();
    assertEq(state.approval_status, "granted");
    assertEq(state.submit_state, "idle", "re-review clears the conflict notice");
    assertEq(submitResultBox(env, "conflict"), null);
    assertEq(findByAttr(env.host, "data-acp-action", "submit").disabled, false);
  });

  await test("capability-form: 5xx and network failures stay unconfirmed, never success", async () => {
    const cases = [
      {
        rejection: { code: "request_failed", status: 503, message: "controller unavailable" },
        raw: "request_failed"
      },
      {
        // The HTTP status is authoritative: a 500 is never a definite rejection,
        // even when the body claims input_invalid.
        rejection: { code: "input_invalid", status: 500, message: "server exploded" },
        raw: "input_invalid"
      },
      { rejection: new TypeError("Failed to fetch"), raw: "submit_unconfirmed" }
    ];
    for (const item of cases) {
      const { env, caught } = await submitAgainst(item.rejection);
      assertEq(caught.code, item.raw);
      assertEq(caught.displayCode, "submit_unconfirmed");
      const state = env.instance.getState();
      assertEq(state.submit_state, "unconfirmed");
      assertEq(state.submit_error.display_code, "submit_unconfirmed");
      assertEq(state.submit_error.category, "network");
      assertEq(state.task_ref, null, "an unknown outcome never creates a TaskRef");
      assertEq(state.submit_result, null);
      assertEq(env.commands.calls.submit.length, 1);
      const box = assertSubmitBox(env, "unconfirmed", "submit_unconfirmed", "network");
      assert(textOf(box).indexOf("未确认") !== -1, "unconfirmed copy is explicit");
    }
  });

  await test("capability-form: submitted renders only for a real job_id + attempt/TaskRef", async () => {
    const cases = [
      { result: { job_id: "job-1", attempt: 1, status: "QUEUED",
        task_ref: { job_id: "job-1", attempt: 1 } }, submitted: true, jobId: "job-1" },
      { result: { attempt: 1, task_ref: { task_id: "task-9", attempt: 1 } },
        submitted: true, jobId: "task-9" },
      { result: { job_id: "job-1", attempt: 0 }, submitted: false },
      { result: { job_id: "job-1" }, submitted: false },
      { result: null, submitted: false }
    ];
    for (const item of cases) {
      const env = setup({ commands: makeCommands(() => Promise.resolve(item.result)) });
      await mount(env);
      fillValid(env.instance);
      await env.instance.preflight({ projectId: "proj-1" });
      env.instance.approval(approvedRecord());
      const outcome = await env.instance.submit({ projectId: "proj-1" }).catch((error) => error);
      const state = env.instance.getState();
      if (!item.submitted) {
        assert(outcome && outcome.code === "submit_unconfirmed", "no fake success");
        assertEq(state.submit_state, "unconfirmed");
        assertEq(state.task_ref, null);
        assertEq(findByAttr(env.host, "data-acp-job-id", "job-1"), null);
        assert(submitResultBox(env, "unconfirmed"), "unconfirmed state visible");
        continue;
      }
      assertEq(state.submit_state, "submitted");
      assertEq(state.task_ref.jobId, item.jobId);
      assertEq(state.task_ref.attempt, 1);
      const receipt = findByAttr(env.host, "data-acp-job-id", item.jobId);
      assert(receipt, "receipt rendered with the real TaskRef");
      assertEq(receipt.getAttribute("data-acp-attempt"), "1");
      const box = assertSubmitBox(env, "submitted", null, null);
      assertEq(box.getAttribute("data-acp-error-code"), null, "success carries no error code");
    }
  });

  await test("capability-form: busy submit is disabled/refused and dispose drops a late receipt", async () => {
    const gate = deferred();
    const env = setup({ commands: makeCommands(() => gate.promise) });
    await mount(env);
    fillValid(env.instance);
    await env.instance.preflight({ projectId: "proj-1" });
    env.instance.approval(approvedRecord());

    const pending = env.instance.submit({ projectId: "proj-1" });
    assertEq(env.instance.getState().submit_state, "submitting");
    await flush();
    assertEq(env.commands.calls.submit.length, 1);
    assertEq(env.instance.getState().busy, true);
    assertEq(env.host.children[0].getAttribute("data-acp-submit-state"), "submitting");
    const button = findByAttr(env.host, "data-acp-action", "submit");
    assertEq(button.disabled, true, "busy disables submit");
    assertEq(button.getAttribute("data-acp-submit-blocked"), "busy");

    const second = await env.instance.submit({ projectId: "proj-1" }).catch((error) => error);
    assertEq(second.code, "submit_busy", "a busy instance refuses a second submit locally");
    assertEq(env.commands.calls.submit.length, 1, "and never reaches the command client");
    const busyPreflight = await env.instance.preflight({ projectId: "proj-1" })
      .catch((error) => error);
    assertEq(busyPreflight.code, "preflight_busy");
    assertEq(env.science.calls.preflight.length, 1, "a busy instance sends no second preflight");

    env.instance.dispose();
    gate.resolve({ job_id: "job-late", attempt: 1,
      task_ref: { job_id: "job-late", attempt: 1 } });
    const stale = await pending.catch((error) => error);
    assertEq(stale.code, "stale_result", "a late receipt is dropped");
    assertEq(env.instance.getState().submit_state, "idle");
    assertEq(env.instance.getState().task_ref, null);
    assertEq(env.host.children.length, 0, "nothing is painted after dispose");

    // A generation change (selection switch) also drops a late submit receipt.
    const two = setup();
    two.science.fetchCapabilityForms = () => Promise.resolve(makeFormsDoc(two.subset, [
      entryFor(makeForm()),
      entryFor(makeForm({ capability_id: "vibration.analyze", title: "振动分析" }))
    ]));
    await mount(two);
    fillValid(two.instance);
    await two.instance.preflight({ projectId: "proj-1" });
    two.instance.approval(approvedRecord());
    const selectionGate = deferred();
    two.commands.submit = () => selectionGate.promise;
    const lateSubmit = two.instance.submit({ projectId: "proj-1" });
    await flush();
    two.instance.select("vibration.analyze");
    selectionGate.resolve({ job_id: "job-late-2", attempt: 1,
      task_ref: { job_id: "job-late-2", attempt: 1 } });
    const dropped = await lateSubmit.catch((error) => error);
    assertEq(dropped.code, "stale_result", "a generation change drops the receipt");
    assertEq(two.instance.getState().submit_state, "idle");
    assertEq(two.instance.getState().task_ref, null);
    assertEq(findByAttr(two.host, "data-acp-job-id", "job-late-2"), null,
      "no stale receipt element after the selection switch");
  });

  // ------------------------------------------------------------------
  // 7. generation guard + dispose
  // ------------------------------------------------------------------
  await test("capability-form: generation guard discards late documents and preflights", async () => {
    const docA = deferred();
    const docB = deferred();
    const env = setup({ context: makeContext("proj-a") });
    env.science.fetchCapabilityForms = (scope) =>
      scope.projectId === "proj-a" ? docA.promise : docB.promise;
    await env.instance.mount(env.host);
    assertEq(env.instance.getState().phase, "loading");

    const switched = env.instance.setProject("proj-b");
    assertEq(env.instance.getState().phase, "loading");
    docB.resolve(makeFormsDoc(env.subset, [
      entryFor(makeForm({ capability_id: "geometry.optimize", title: "几何优化" }))
    ]));
    await switched;
    await flush();
    assertEq(env.instance.getState().project_id, "proj-b");
    assertEq(env.instance.getState().selected, "geometry.optimize");
    assertEq(env.instance.getState().phase, "ready");

    docA.resolve(makeFormsDoc(env.subset, [entryFor(makeForm())]));
    await flush();
    assertEq(env.instance.getState().project_id, "proj-b", "late project-a document discarded");
    assertEq(env.instance.getState().selected, "geometry.optimize");
    assertEq(env.instance.getState().phase, "ready");

    // A late preflight for a previous selection is discarded too.
    const two = setup();
    two.science.fetchCapabilityForms = () => Promise.resolve(makeFormsDoc(two.subset, [
      entryFor(makeForm()),
      entryFor(makeForm({ capability_id: "vibration.analyze", title: "振动分析" }))
    ]));
    await mount(two);
    fillValid(two.instance);
    const gate = deferred();
    two.science.preflight = () => gate.promise;
    const pending = two.instance.preflight({ projectId: "proj-1" });
    two.instance.select("vibration.analyze");
    gate.resolve(preflightProjection());
    const stale = await pending.catch((error) => error);
    assertEq(stale.code, "stale_result");
    assertEq(two.instance.getState().selected, "vibration.analyze");
    assertEq(two.instance.getState().preflight_state, "idle");
  });

  await test("capability-form: dispose unsubscribes, detaches, drops late results and allows remount", async () => {
    const env = setup();
    await mount(env);
    assertEq(env.context.listenerCount(), 1);
    fillValid(env.instance);

    const gate = deferred();
    env.science.preflight = () => gate.promise;
    const pending = env.instance.preflight({ projectId: "proj-1" });
    env.instance.dispose();
    assertEq(env.context.listenerCount(), 0, "context subscription removed");
    assertEq(env.host.children.length, 0, "module root detached from the host");
    assertEq(env.instance.getState().phase, "empty");

    gate.resolve(preflightProjection());
    const stale = await pending.catch((error) => error);
    assertEq(stale.code, "stale_result");
    assertEq(env.instance.getState().preflight_state, "idle");

    // A document that arrives after dispose never renders.
    const late = setup();
    const loadGate = deferred();
    late.science.fetchCapabilityForms = () => loadGate.promise;
    await late.instance.mount(late.host);
    late.instance.dispose();
    loadGate.resolve(makeFormsDoc(late.subset, [entryFor(makeForm())]));
    await flush();
    assertEq(late.instance.getState().phase, "empty");

    // Remount starts a fresh lifecycle.
    await env.instance.mount(env.host);
    await flush();
    assertEq(env.instance.getState().phase, "ready");
    assertEq(env.context.listenerCount(), 1);

    // WorkspaceContext project changes reload the forms.
    env.context.emit({ projectId: "proj-2" });
    await flush();
    assertEq(env.instance.getState().project_id, "proj-2");
    assertEq(env.instance.getState().phase, "ready");

    env.instance.dispose();
    env.instance.dispose();
  });

  // ------------------------------------------------------------------
  // 8. read-only history + unavailable + missing contract
  // ------------------------------------------------------------------
  await test("capability-form: read-only history and unavailable forms stay inert", async () => {
    const env = setup();
    env.science.fetchCapabilityForms = () => Promise.resolve(makeFormsDoc(env.subset, [], [{
      capability_id: "acp2_external_native_singlepoint",
      state: "read_only",
      capability_source: "science_plugin",
      version: "1.0.0",
      descriptor_digest: "sha256:" + "f".repeat(64),
      enabled: false,
      reason: "capability_not_active"
    }]));
    await mount(env);
    assertEq(env.instance.getState().phase, "ready");
    assertEq(env.instance.getState().selected_state, "read_only");
    assertEq(findAllByTag(env.host, "INPUT").length, 0, "read-only renders no input");
    assertEq(findAllByTag(env.host, "TEXTAREA").length, 0, "read-only renders no textarea");
    assertEq(findByAttr(env.host, "data-acp-action", "preflight"), null);
    assertEq(findByAttr(env.host, "data-acp-action", "submit"), null);
    assert(textOf(env.host).indexOf("sha256:" + "f".repeat(64)) !== -1,
      "frozen descriptor digest shown");

    const unavailable = setup();
    unavailable.science.fetchCapabilityForms = () => Promise.resolve(makeFormsDoc(
      unavailable.subset,
      [{
        capability_id: "plugin.capability",
        state: "unavailable",
        capability_source: "science_plugin",
        error: {
          code: "ui_keyword_rejected",
          message: "form_schema: keyword 'oneOf' is outside acp2.ui-subset.v1",
          params: { keyword: "oneOf", path: "form_schema" }
        }
      }],
      []
    ));
    await mount(unavailable);
    assertEq(unavailable.instance.getState().selected_state, "unavailable");
    assert(findByAttr(unavailable.host, "data-code", "ui_keyword_rejected"),
      "typed unavailable reason visible");
    assertEq(findAllByTag(unavailable.host, "INPUT").length, 0);
    assertEq(findByAttr(unavailable.host, "data-acp-action", "submit"), null);
  });

  await test("capability-form: a missing shared UI-subset contract is a typed error", async () => {
    const env = setup({ contract: false });
    await env.instance.mount(env.host);
    await flush();
    assertEq(env.instance.getState().phase, "error");
    assertEq(env.instance.getState().error.code, "ui_subset_unavailable");
    assertEq(env.science.calls.forms.length, 0,
      "no document is fetched while the shared contract is missing");
    assertEq(env.host.children[0].getAttribute("data-acp-error-code"), "ui_subset_unavailable");
  });

  // ------------------------------------------------------------------
  // 9. source guards: one global, no fetch, no second evaluator
  // ------------------------------------------------------------------
  await test("capability-form: one module global, no fetch, no second schema evaluator", () => {
    const source = fs.readFileSync(path.join(REPO_ROOT, "frontend", MODULE), "utf8");
    assert(!/\bfetch\s*\(/.test(source), "module must not call fetch");
    assertEq(source.indexOf("XMLHttpRequest"), -1, "module must not use XHR");
    assertEq(source.indexOf("innerHTML"), -1, "module must not write markup");
    assertEq(source.indexOf("insertAdjacentHTML"), -1, "module must not write markup");
    assertEq(source.indexOf("document.addEventListener"), -1,
      "module must not do page-level wiring");
    assert(!/\bACPCapabilityForm\s*\./.test(source),
      "module must not call the classic page feature");
    assert(!/\bACPWorkbench\.features\s*\./.test(source),
      "module must not call into the classic feature table");

    const writes = source.match(/window\.[A-Za-z_$][\w$]*\s*=/g) || [];
    assertEq(writes.map((write) => write.replace(/\s*=$/, "")), ["window.ACPNextModules"],
      "the only page global is the module table");

    ["tablesMatch", "validateFormDocument", "validateValues", "buildFormOutcome", "fieldModel"]
      .forEach((name) => {
        assert(source.indexOf(name) !== -1,
          "module reuses the shared uiSubset." + name + " implementation");
      });
    assertEq(source.indexOf("REJECTED_KEYWORDS"), -1, "no local keyword whitelist");
    assertEq(source.indexOf("additionalProperties"), -1, "no local schema walker");
    assertEq(source.indexOf("/api/"), -1, "no URL prefix in the module");

    const css = fs.readFileSync(path.join(REPO_ROOT, "frontend", CSS), "utf8");
    assert(css.indexOf(".acp-capf") !== -1, "module CSS is scoped by the acp-capf prefix");
  });
};
