/*
 * M4-05 headless Workbench driver (Node).
 *
 * Loads the REAL Workbench modules — the shared cache, the ui-subset contract,
 * the science client and the capability-form feature — and injects a REAL HTTP
 * transport built on Node's global `fetch`, pointed at the real Controller
 * started by `scripts/acceptance/acp2_m405.py` from the isolated plugin
 * environment.  Nothing is faked except the DOM: the Node process has no
 * browser, so a minimal document shim provides the panel root the feature
 * mounts into.  The shim only implements the DOM subset the feature itself
 * uses (`createElement` / `textContent` / `appendChild` / `addEventListener`),
 * so the real renderer builds the panel and the real value handlers run.
 *
 * The third-party form comes from the installed fixture distribution's
 * `.dist-info` through the platform's metadata-only reader; discovery,
 * preflight and the descriptor cross-check all cross a socket.
 *
 * Output: one JSON document on stdout with every step result.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const scenario = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const steps = [];

function load(relative) {
  vm.runInThisContext(fs.readFileSync(scenario.repo_root + "/" + relative, "utf8"),
    { filename: relative });
}

global.window = global;
load(scenario.store_module);
load(scenario.experience_module);
load(scenario.subset_module);
load(scenario.science_module);
load(scenario.feature_module);

/* ------------------------------------------------------------------ */
/* minimal DOM (only the subset the capability-form feature uses)      */
/* ------------------------------------------------------------------ */

class ShimNode {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.attributes = Object.create(null);
    this.listeners = Object.create(null);
    this.parentNode = null;
    this.id = "";
    this.className = "";
    this.type = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.selected = false;
    this._text = "";
  }

  set textContent(value) {
    this.children = [];
    this._text = value == null ? "" : String(value);
  }

  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
    return this._text;
  }

  appendChild(child) {
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  setAttribute(name, value) {
    this.attributes[String(name)] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name] : null;
  }

  addEventListener(type, handler) {
    const key = String(type);
    (this.listeners[key] = this.listeners[key] || []).push(handler);
  }

  dispatch(type, event) {
    const handlers = this.listeners[String(type)] || [];
    const payload = event || { target: this, currentTarget: this };
    handlers.forEach((handler) => handler(payload));
    return handlers.length;
  }
}

class ShimDocument {
  constructor() {
    this.byId = Object.create(null);
  }

  createElement(tag) {
    return new ShimNode(tag);
  }

  getElementById(id) {
    const key = String(id);
    if (this.byId[key]) return this.byId[key];
    const root = this.byId["capability-form-body"];
    if (!root) return null;
    const found = walk(root, []).filter((node) => node.id === key);
    return found.length ? found[0] : null;
  }

  addEventListener() {
    /* the page wiring is exercised explicitly by mount(); no DOMContentLoaded */
  }
}

function walk(node, out) {
  out.push(node);
  node.children.forEach((child) => walk(child, out));
  return out;
}

function snapshot(doc) {
  const root = doc.getElementById("capability-form-body");
  const nodes = root ? walk(root, []) : [];
  const inputs = nodes
    .filter((node) => node.tagName === "INPUT" || node.tagName === "TEXTAREA" ||
      node.tagName === "SELECT")
    .map((node) => ({
      id: node.id,
      tag: node.tagName,
      binding: node.getAttribute("data-binding"),
      value: node.value === undefined || node.value === null ? null : String(node.value),
      options: node.children.filter((child) => child.tagName === "OPTION")
        .map((child) => ({ value: child.value, text: child.textContent })),
    }));
  const buttons = nodes
    .filter((node) => node.tagName === "BUTTON")
    .map((node) => ({ id: node.id, text: node.textContent, disabled: node.disabled === true }));
  return {
    text: root ? root.textContent : "",
    ids: nodes.map((node) => node.id).filter((id) => !!id),
    classes: nodes.map((node) => node.className).filter((name) => !!name),
    inputs: inputs,
    buttons: buttons,
  };
}

const doc = new ShimDocument();
const panel = doc.createElement("div");
panel.setAttribute("id", "capability-form-body");
doc.byId["capability-form-body"] = panel;

/* ------------------------------------------------------------------ */
/* real HTTP transport                                                 */
/* ------------------------------------------------------------------ */

async function request(url, opts) {
  const response = await fetch(url, opts);
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch (error) {
    body = text;
  }
  if (!response.ok) {
    const httpError = new Error("HTTP " + response.status);
    httpError.status = response.status;
    httpError.body = body;
    httpError.code = body && body.detail && body.detail.code ? body.detail.code : null;
    throw httpError;
  }
  return body;
}

const v1 = (suffix, opts) => request(scenario.base_url + "/api/v1" + suffix, opts);
const v2 = (suffix, opts) => request(scenario.base_url + "/api/v2" + suffix, opts);

const science = ACPClient.science;
science.init({ v1: v1, v2: v2 });
const form = ACPCapabilityForm;
form.init({
  science: science,
  now: () => scenario.now || "2026-10-28T00:00:00.000Z",
  editorRef: "m405-headless-driver",
});
form.mount(doc);

function typed(error) {
  return {
    ok: false,
    code: (error && error.code) || null,
    status: error && (error.httpStatus || error.status) ? (error.httpStatus || error.status) : null,
    message: String((error && error.message) || error),
    problems: (error && error.problems) || null,
    body: error && error.body !== undefined ? error.body : null,
  };
}

async function step(name, run) {
  try {
    const value = await run();
    steps.push({ name: name, ok: true, value: value });
    return value;
  } catch (error) {
    steps.push(Object.assign({ name: name }, typed(error)));
    throw error;
  }
}

function fail(message) {
  throw new Error("assertion: " + message);
}

async function main() {
  const scope = { projectId: scenario.project_id };
  const capability = scenario.capability_id;

  const discovery = await step("discovery.capability_forms", () => form.load(scope));
  if (discovery.subset_version !== scenario.subset_version) {
    fail("subset version: " + discovery.subset_version);
  }
  if (discovery.subset_digest !== scenario.subset_digest) {
    fail("subset digest: " + discovery.subset_digest);
  }
  const pluginEntry = discovery.forms.filter((entry) => entry.capability_id === capability)[0];
  if (!pluginEntry) fail("the third-party capability form is not discovered");
  if (pluginEntry.state !== "available") fail("plugin form state: " + pluginEntry.state);
  if (String(pluginEntry.form_origin || "").indexOf("plugin:") !== 0) {
    fail("plugin form origin: " + pluginEntry.form_origin);
  }
  if (pluginEntry.form_digest !== scenario.form_digest) {
    fail("plugin form digest: " + pluginEntry.form_digest);
  }
  steps.push({
    name: "discovery.form_entry",
    ok: true,
    value: {
      capability_id: pluginEntry.capability_id,
      state: pluginEntry.state,
      form_origin: pluginEntry.form_origin,
      form_digest: pluginEntry.form_digest,
      descriptor_digest: pluginEntry.descriptor_digest,
      fields: (pluginEntry.form && pluginEntry.form.form_schema
        ? Object.keys(pluginEntry.form.form_schema.properties) : []),
    },
  });

  form.select(capability);
  const rendered = snapshot(doc);
  if (!rendered.ids.includes("capability-form-field-source")) fail("source field not rendered");
  if (!rendered.ids.includes("capability-form-field-charge")) fail("charge field not rendered");
  if (!rendered.ids.includes("capability-form-preflight")) fail("preflight button not rendered");
  if (!rendered.ids.includes("capability-form-submit")) fail("submit button not rendered");
  if (rendered.text.indexOf(scenario.expected_title) === -1) {
    fail("form title not rendered: " + rendered.text.slice(0, 200));
  }
  steps.push({
    name: "form.render",
    ok: true,
    value: {
      title_present: true,
      ids: rendered.ids,
      select_options: (rendered.inputs.filter((item) => item.tag === "SELECT")[0] || {})
        .options || [],
      buttons: rendered.buttons,
      text_head: rendered.text.slice(0, 400),
    },
  });

  // 1. A human types the required string field through the rendered control:
  //    the feature's own change handler records the edit.
  const source = doc.getElementById("capability-form-field-source");
  source.value = scenario.structure_text;
  if (source.dispatch("change") < 1) fail("the source field has no change handler");
  const stateAfterSource = form.getState();
  if (stateAfterSource.values.source !== scenario.structure_text) {
    fail("source value did not reach the form state");
  }

  // 2. A numeric edit through a rendered control is stored verbatim, so the
  //    ui-subset type check refuses a string where an integer is declared.
  //    The refusal is typed (never silently coerced or ignored); the row then
  //    restores the typed default so the preflight can proceed.
  const charge = doc.getElementById("capability-form-field-charge");
  charge.value = scenario.numeric_edit;
  charge.dispatch("change");
  const numericState = form.getState();
  steps.push({
    name: "form.numeric_edit_stored_verbatim",
    ok: true,
    value: { charge: numericState.values.charge, type: typeof numericState.values.charge },
  });
  let numericRefusal = null;
  try {
    await form.preflight(scope);
  } catch (error) {
    numericRefusal = typed(error);
  }
  if (!numericRefusal) fail("a string typed into an integer field was accepted");
  if (!String(numericRefusal.message).includes("ui_value_type")) {
    fail("numeric edit refusal is not ui_value_type: " + numericRefusal.message);
  }
  steps.push({ name: "preflight.numeric_edit_refused", ok: true, value: numericRefusal });
  form.setValue("charge", Number(scenario.numeric_restore));

  const viewModel = await step("preflight", () => form.preflight(scope));
  if (viewModel.request.body.workflow !== scenario.expected_workflow) {
    fail("preflight workflow: " + viewModel.request.body.workflow);
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(String(viewModel.plan_digest || ""))) {
    fail("preflight plan digest: " + viewModel.plan_digest);
  }
  if (viewModel.plan_digest !== viewModel.preflight_fingerprint) {
    fail("plan digest and fingerprint diverged");
  }
  if (viewModel.form_digest !== scenario.form_digest) fail("view model form digest");
  if (viewModel.descriptor_digest !== pluginEntry.descriptor_digest) {
    fail("view model descriptor digest");
  }
  if (viewModel.subset_digest !== scenario.subset_digest) fail("view model subset digest");
  const edited = viewModel.provenance.human_edits.map((edit) => edit.field_path).sort();
  if (edited.join(",") !== scenario.expected_edits.join(",")) {
    fail("human edits: " + edited.join(","));
  }

  // The rendered panel displays the preflight ViewModel: digests, bound fields
  // and the fact that submit needs an approved record.
  const displayed = snapshot(doc);
  if (displayed.text.indexOf(viewModel.plan_digest) === -1) {
    fail("the rendered panel does not display the plan digest");
  }
  if (displayed.text.indexOf(viewModel.form_digest) === -1) {
    fail("the rendered panel does not display the form digest");
  }
  if (displayed.text.indexOf("structure.source") === -1) {
    fail("the rendered panel does not display the bound field table");
  }
  const submit = displayed.buttons.filter((button) => button.id === "capability-form-submit")[0];
  if (!submit || submit.disabled !== true) fail("submit must be disabled without an approval");
  if (displayed.text.indexOf(scenario.approval_hint) === -1) {
    fail("the disabled submit states no reason");
  }
  steps.push({
    name: "view_model",
    ok: true,
    value: {
      plan_digest: viewModel.plan_digest,
      form_digest: viewModel.form_digest,
      descriptor_digest: viewModel.descriptor_digest,
      subset_version: viewModel.subset_version,
      subset_digest: viewModel.subset_digest,
      fields: viewModel.request.fields.map(
        (field) => field.binding + "=" + field.task_request),
      human_edits: viewModel.provenance.human_edits,
      rendered_digests: {
        plan: displayed.text.indexOf(viewModel.plan_digest) !== -1,
        form: displayed.text.indexOf(viewModel.form_digest) !== -1,
        fields: displayed.text.indexOf("structure.source") !== -1,
      },
      submit: submit,
      approval_hint_present: displayed.text.indexOf(scenario.approval_hint) !== -1,
    },
  });

  const capabilities = await step("discovery.capabilities", () =>
    science.fetchCapabilities(scope, { generation: "m405-live" }));
  const declared = (capabilities.capabilities.descriptors.capabilities || [])
    .filter((entry) => entry.capability_id === capability);
  if (declared.length !== 1) fail("descriptor discovery lost the plugin capability");
  if (declared[0].descriptor_digest !== pluginEntry.descriptor_digest) {
    fail("descriptor digest differs between the two discovery surfaces");
  }
  steps.push({
    name: "descriptor_cross_check",
    ok: true,
    value: {
      descriptor_digest: declared[0].descriptor_digest,
      distribution_digest: (declared[0].distribution_digest || {}).digest || null,
      plugin: declared[0].plugin || null,
    },
  });

  console.log(JSON.stringify({ ok: true, steps: steps }));
}

main().catch((error) => {
  console.log(JSON.stringify({ ok: false, steps: steps, error: typed(error) }));
  process.exitCode = 1;
});
