"use strict";
/*
 * Module tests — schema-driven capability form (M3-06).
 *
 * Three layers, no network:
 *  1. the shared subset validator (contracts/ui_subset.js) against the frozen
 *     docs contract — whitelist parity, rejected keywords, unknown major,
 *     bindings/units, value coercion;
 *  2. the feature (features/capability-form.js) against a fake DOM + fake
 *     clients: safe rendering (textContent only), third-party form appearing as
 *     data with no frontend source change, read-only history, preflight →
 *     view-model → provenance chain;
 *  3. the real client guard (client/science-query.js::validateCapabilityForms)
 *     refusing a form the UI cannot interpret.
 */
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const CONTRACT = path.join(
  REPO_ROOT, "docs", "reference", "next-change", "acp2-contracts", "science",
  "ui-subset.v1.json"
);
const FORMS_CONTRACT = path.join(
  REPO_ROOT, "docs", "reference", "next-change", "acp2-contracts", "science",
  "ui-forms.v1.json"
);
const SUBSET_MODULE = "js/workbench/contracts/ui_subset.js";
const FEATURE = "js/workbench/features/capability-form.js";
const SCIENCE_CLIENT = "js/client/science-query.js";
const STORE_MODULE = "js/client/resource-store.js";

const PLUGIN_CAPABILITY = "acp2_external_native_singlepoint";

// ---------------------------------------------------------------------------
// fake DOM: every element records innerHTML-family writes so a test can assert
// the feature never injects markup.
// ---------------------------------------------------------------------------
const FORBIDDEN_WRITES = [];

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    listeners: {},
    attrs: {},
    className: "",
    id: "",
    style: {},
    disabled: false,
    checked: false,
    value: "",
    textContent: "",
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) {
      const index = this.children.indexOf(child);
      if (index !== -1) this.children.splice(index, 1);
      return child;
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    getAttribute(name) { return this.attrs[name] === undefined ? null : this.attrs[name]; },
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); },
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    click() { (this.listeners.click || []).forEach((fn) => fn({ target: this })); },
  };
  ["innerHTML", "outerHTML"].forEach((name) => {
    Object.defineProperty(el, name, {
      get() { return ""; },
      set(value) { FORBIDDEN_WRITES.push(this.tagName + "." + name + "=" + String(value)); },
    });
  });
  el.insertAdjacentHTML = (position, value) => {
    FORBIDDEN_WRITES.push(el.tagName + ".insertAdjacentHTML=" + String(value));
  };
  return el;
}

function makeDoc() {
  const byId = new Map();
  const ids = new Map();
  return {
    getElementById(id) {
      if (!byId.has(id)) {
        const el = makeEl("div");
        el.id = id;
        byId.set(id, el);
      }
      return byId.get(id);
    },
    createElement(tag) { return makeEl(tag); },
    querySelectorAll: () => [],
    addEventListener() {},
    _byId: byId,
    _ids: ids,
  };
}

function allNodes(el, out) {
  out = out || [];
  out.push(el);
  (el.children || []).forEach((child) => allNodes(child, out));
  return out;
}

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------
function formsDocument(uiSubset, overrides) {
  const base = {
    schema: uiSubset.FORMS_SCHEMA,
    schema_version: 1,
    subset_version: uiSubset.SUBSET_VERSION,
    subset_digest: "sha256:" + "a".repeat(64),
    subset: uiSubset.subsetTable(),
    catalog_etag: "sha256:" + "b".repeat(64),
    forms: [],
    read_only: [],
  };
  return Object.assign(base, overrides || {});
}

function builtinForm() {
  return {
    schema: "acp2.ui.form.v1",
    schema_version: 1,
    subset_version: "acp2.ui-subset.v1",
    capability_id: "electronic.energy",
    title: "Single-point energy",
    description: "Built-in form",
    request: {
      surface: "job_submit",
      workflow: "singlepoint",
      template: {
        input: { source_type: "xyz_text", source: "" },
        method: { levels: { single_point: { engine: "orca" } } },
        resources: {},
      },
    },
    form_schema: {
      type: "object",
      additionalProperties: false,
      required: ["source"],
      properties: {
        source: {
          type: "string",
          title: "Structure",
          minLength: 4,
          "x-acp": { binding: "task.structure.source", widget: "textarea" },
        },
        charge: {
          type: "integer",
          title: "Charge",
          default: 0,
          minimum: -10,
          maximum: 10,
          "x-acp": { binding: "task.charge", widget: "number" },
        },
      },
    },
    form_digest: "sha256:" + "c".repeat(64),
  };
}

function availableEntry(capabilityId, form) {
  return {
    capability_id: capabilityId,
    state: "available",
    capability_source: "builtin",
    descriptor_digest: null,
    descriptor_digest_pin: null,
    form_digest: form.form_digest,
    form_origin: "platform",
    form: form,
  };
}

function pluginFormFromDocs() {
  const document_ = JSON.parse(fs.readFileSync(FORMS_CONTRACT, "utf8"));
  return document_.plugin_example_forms[0].form;
}

function fakeScience(overrides) {
  return Object.assign({
    fetchCapabilityForms: () => Promise.resolve(null),
    preflight: () => Promise.resolve({
      schema_version: 1,
      executed: false,
      metering_consumed: false,
      transfers_attempted: false,
      plan: { workflow: "singlepoint", plan_digest: "sha256:" + "d".repeat(64) },
      preflight_fingerprint: "sha256:" + "d".repeat(64),
      estimate: { feasible: true, nproc: 2 },
    }),
    fetchView: () => Promise.resolve({ task_ref: { job_id: "j1", attempt: 1 } }),
  }, overrides || {});
}

function makeDom() {
  return {
    document: makeDoc(),
    console: { log() {}, warn() {}, error() {} },
    t: (key) => key,
  };
}

function loadForm(h, science, commands, doc) {
  const sandbox = h.makeSandbox(makeDom());
  h.loadScript(SUBSET_MODULE, sandbox);
  h.loadScript(FEATURE, sandbox);
  const feature = sandbox.ACPCapabilityForm;
  feature.init({
    science: science || fakeScience(),
    commands: commands || null,
    now: () => "2026-10-27T00:00:00.000Z",
    editorRef: "principal-workbench",
  });
  feature.mount(doc || sandbox.document);
  return { feature: feature, sandbox: sandbox };
}

function uiSubsetOf(h) {
  const sandbox = h.makeSandbox(makeDom());
  h.loadScript(SUBSET_MODULE, sandbox);
  return sandbox.ACPWorkbench.contracts.uiSubset;
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  // ------------------------------------------------------------------
  // 1. subset contract parity + validator
  // ------------------------------------------------------------------
  await test("ui-subset: whitelist/bindings match the frozen contract document", () => {
    const uiSubset = uiSubsetOf(h);
    const contract = JSON.parse(fs.readFileSync(CONTRACT, "utf8"));
    assertEq(contract.subset_version, uiSubset.SUBSET_VERSION, "subset version");
    assertEq(Object.keys(contract.keywords).sort(), uiSubset.ALLOWED_KEYWORDS.slice().sort(),
      "keyword whitelist");
    assertEq(contract.rejected_keywords.slice().sort(), uiSubset.REJECTED_KEYWORDS.slice().sort(),
      "rejected keyword list");
    assertEq(contract.formats.slice().sort(), uiSubset.FORMATS.slice().sort(), "formats");
    assertEq(contract.type_names.slice().sort(), uiSubset.TYPE_NAMES.slice().sort(), "type names");
    assertEq(contract.widgets.slice().sort(), uiSubset.WIDGETS.slice().sort(), "widgets");
    const projectRows = (rows) => rows.map((row) => ({
      binding: row.binding,
      task_request: row.task_request,
      request_body: row.request_body,
      value_type: row.value_type,
      unit: row.unit,
      required: row.required,
    }));
    assertEq(projectRows(contract.bindings), projectRows(uiSubset.BINDINGS), "binding table");
    assertEq(Object.entries(contract.units).sort(), Object.entries(uiSubset.UNITS).sort(),
      "units");
  });

  await test("ui-subset: $ref / combinators / unknown keywords are typed refusals", () => {
    const uiSubset = uiSubsetOf(h);
    const base = builtinForm();
    const withRef = JSON.parse(JSON.stringify(base));
    withRef.form_schema.properties.charge = { "$ref": "#/$defs/charge" };
    assertEq(uiSubset.validateFormDocument(withRef).code, "ui_keyword_rejected", "$ref");
    ["allOf", "anyOf", "oneOf", "not", "if"].forEach((keyword) => {
      const doc = JSON.parse(JSON.stringify(base));
      doc.form_schema[keyword] = [{ type: "object" }];
      assertEq(uiSubset.validateFormDocument(doc).code, "ui_keyword_rejected", keyword);
    });
    const unknown = JSON.parse(JSON.stringify(base));
    unknown.form_schema.properties.charge.transform = ["trim"];
    assertEq(uiSubset.validateFormDocument(unknown).code, "ui_keyword_unknown", "unknown keyword");
    assert(uiSubset.validateFormDocument(base).ok, "the built-in form must validate");
  });

  await test("ui-subset: unknown major/version and unbound fields are refused", () => {
    const uiSubset = uiSubsetOf(h);
    const major = JSON.parse(JSON.stringify(builtinForm()));
    major.subset_version = "acp2.ui-subset.v2";
    assertEq(uiSubset.validateFormDocument(major).code, "ui_subset_unknown_major", "subset major");
    const formMajor = JSON.parse(JSON.stringify(builtinForm()));
    formMajor.schema_version = 2;
    assertEq(uiSubset.validateFormDocument(formMajor).code, "ui_form_unknown_major", "form major");
    const unbound = JSON.parse(JSON.stringify(builtinForm()));
    delete unbound.form_schema.properties.charge["x-acp"];
    assertEq(uiSubset.validateFormDocument(unbound).code, "ui_binding_missing", "unbound field");
    const unknownBinding = JSON.parse(JSON.stringify(builtinForm()));
    unknownBinding.form_schema.properties.charge["x-acp"].binding = "task.options.magic";
    assertEq(uiSubset.validateFormDocument(unknownBinding).code, "ui_binding_unknown",
      "unknown binding");
    const unit = JSON.parse(JSON.stringify(builtinForm()));
    unit.form_schema.properties.charge["x-acp"].unit = "kelvin";
    assertEq(uiSubset.validateFormDocument(unit).code, "ui_unit_mismatch", "unit mismatch");
    const duplicate = JSON.parse(JSON.stringify(builtinForm()));
    duplicate.form_schema.properties.extra = {
      type: "integer",
      "x-acp": { binding: "task.charge" },
    };
    assertEq(uiSubset.validateFormDocument(duplicate).code, "ui_binding_duplicate",
      "duplicate binding");
  });

  await test("ui-subset: values are validated with whitelist keywords only", () => {
    const uiSubset = uiSubsetOf(h);
    const form = builtinForm();
    assertEq(uiSubset.validateValues(form, {}).code, "ui_value_required", "required");
    assertEq(uiSubset.validateValues(form, { source: "O 0 0 0", charge: 1.5 }).code,
      "ui_value_type", "integer type");
    assertEq(uiSubset.validateValues(form, { source: "O 0 0 0", charge: 11 }).code,
      "ui_value_bounds", "maximum");
    assertEq(uiSubset.validateValues(form, { source: "O", charge: 0 }).code,
      "ui_value_length", "minLength");
    assertEq(uiSubset.validateValues(form, { source: "O 0 0 0", magic: 1 }).code,
      "ui_value_unknown_field", "unknown field");
    const ok = uiSubset.validateValues(form, { source: "O 0 0 0", charge: 2 });
    assert(ok.ok, "valid values accepted");
    assertEq(ok.values.charge, 2, "value kept");
  });

  await test("ui-subset: request body is built only from the binding table", () => {
    const uiSubset = uiSubsetOf(h);
    const form = builtinForm();
    const built = uiSubset.buildFormOutcome(form, { source: "O 0 0 0", charge: 2 });
    assert(built.ok, JSON.stringify(built.problems));
    assertEq(built.body.workflow, "singlepoint", "workflow from the form request");
    assertEq(built.body.input.source, "O 0 0 0", "bound source");
    assertEq(built.body.input.charge, 2, "bound charge");
    assertEq(built.body.method.levels.single_point.engine, "orca", "template preserved");
    assertEq(built.fields.map((row) => row.binding).sort(),
      ["task.charge", "task.structure.source"], "bound field table");
    assertEq(built.fields[0].task_request, "structure.source", "TaskRequest path recorded");
  });

  await test("ui-subset: tablesMatch refuses a drifted served subset", () => {
    const uiSubset = uiSubsetOf(h);
    assert(uiSubset.tablesMatch(uiSubset.subsetTable()), "own table matches");
    const drifted = uiSubset.subsetTable();
    drifted.bindings = drifted.bindings.slice(0, 2);
    assert(!uiSubset.tablesMatch(drifted), "drift detected");
    const keywordDrift = uiSubset.subsetTable();
    keywordDrift.keywords = Object.assign({}, keywordDrift.keywords, { oneOf: true });
    assert(!uiSubset.tablesMatch(keywordDrift), "keyword drift detected");
  });

  function assertEqualDigest(digest) {
    assert(/^sha256:[0-9a-f]{64}$/.test(String(digest)), "form digest shape: " + digest);
  }

  await test("ui-subset: the third-party fixture form (docs) validates and keeps its digest", () => {
    const uiSubset = uiSubsetOf(h);
    const form = pluginFormFromDocs();
    const check = uiSubset.validateFormDocument(form);
    assert(check.ok, JSON.stringify(check.problems));
    assertEq(form.capability_id, PLUGIN_CAPABILITY, "fixture capability");
    assertEqualDigest(form.form_digest);
  });

  // ------------------------------------------------------------------
  // 2. feature: rendering + chain
  // ------------------------------------------------------------------
  await test("capability-form: third-party form appears from data only (no frontend edit)", async () => {
    FORBIDDEN_WRITES.length = 0;
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const pluginForm = pluginFormFromDocs();
    const pluginEntry = {
      capability_id: PLUGIN_CAPABILITY,
      state: "available",
      capability_source: "science_plugin",
      descriptor_digest: "sha256:" + "e".repeat(64),
      form_digest: pluginForm.form_digest,
      form_origin: "plugin:acp2-external-science-validator",
      form: pluginForm,
    };
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, {
        forms: [availableEntry("electronic.energy", builtinForm()), pluginEntry],
      })),
    });
    const { feature } = loadForm(h, science, null, doc);
    const loaded = await feature.load({ projectId: "proj-1" });
    assertEq(loaded.forms.map((entry) => entry.capability_id),
      ["electronic.energy", PLUGIN_CAPABILITY], "both capability forms discovered");
    feature.select(PLUGIN_CAPABILITY);
    const state = feature.getState();
    assertEq(state.selected, PLUGIN_CAPABILITY, "selected");
    assertEq(state.fields.map((field) => field.binding).sort(),
      ["task.charge", "task.multiplicity", "task.resources.nproc", "task.structure.source"],
      "fields come from the plugin form");
    // The plugin capability id must not appear in the feature source: the form
    // is data, so adding a third-party capability needs no frontend change.
    const source = fs.readFileSync(path.join(REPO_ROOT, "frontend", FEATURE), "utf8");
    assert(source.indexOf(PLUGIN_CAPABILITY) === -1, "feature source names no plugin capability");
    assertEq(FORBIDDEN_WRITES, [], "no innerHTML/outerHTML/insertAdjacentHTML writes");
  });

  await test("capability-form: remote text is rendered as text, never as markup", async () => {
    FORBIDDEN_WRITES.length = 0;
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const hostile = JSON.parse(JSON.stringify(builtinForm()));
    hostile.title = "<img src=x onerror=alert(1)>";
    hostile.form_schema.properties.source.title = "<script>alert(2)</script>";
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, {
        forms: [availableEntry("electronic.energy", hostile)],
      })),
    });
    const { feature } = loadForm(h, science, null, doc);
    await feature.load({ projectId: "proj-1" });
    const nodes = allNodes(doc.getElementById("capability-form-body"));
    const texts = nodes.map((node) => String(node.textContent));
    assert(texts.some((text) => text.indexOf("<script>alert(2)</script>") !== -1),
      "field title markup kept as text");
    assert(texts.some((text) => text.indexOf("<img src=x onerror=alert(1)>") !== -1),
      "form title markup kept as text");
    assertEq(FORBIDDEN_WRITES, [], "no markup write");
  });

  await test("capability-form: read-only history renders digests and no inputs", async () => {
    FORBIDDEN_WRITES.length = 0;
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, {
        forms: [],
        read_only: [{
          capability_id: PLUGIN_CAPABILITY,
          state: "read_only",
          capability_source: "science_plugin",
          version: "1.0.0",
          descriptor_digest: "sha256:" + "f".repeat(64),
          enabled: false,
          frozen_descriptor_schema: "acp2.plugin.capability-descriptor.v1",
          reason: "capability_not_active",
        }],
      })),
    });
    const { feature } = loadForm(h, science, null, doc);
    await feature.load({ projectId: "proj-1" });
    feature.select(PLUGIN_CAPABILITY);
    const nodes = allNodes(doc.getElementById("capability-form-body"));
    const codes = nodes.map((node) => node.getAttribute("data-code")).filter(Boolean);
    assert(codes.indexOf("ui_form_read_only") !== -1, "read-only notice shown: " + codes.join(","));
    const tags = nodes.map((node) => node.tagName);
    assert(tags.indexOf("INPUT") === -1, "read-only view renders no input");
    assert(tags.indexOf("TEXTAREA") === -1, "read-only view renders no textarea");
    const texts = nodes.map((node) => node.textContent);
    assert(texts.indexOf("sha256:" + "f".repeat(64)) !== -1, "frozen descriptor digest shown");
    const state = feature.getState();
    assertEq(state.selected_state, "read_only", "state is read-only");
  });

  await test("capability-form: unavailable form is refused, not ignored", async () => {
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, {
        forms: [{
          capability_id: "electronic.energy",
          state: "unavailable",
          capability_source: "builtin",
          error: {
            code: "ui_keyword_rejected",
            message: "form_schema: keyword 'oneOf' is outside acp2.ui-subset.v1",
            params: { keyword: "oneOf", path: "form_schema" },
          },
        }],
      })),
    });
    const { feature } = loadForm(h, science, null, doc);
    await feature.load({ projectId: "proj-1" });
    feature.select("electronic.energy");
    const nodes = allNodes(doc.getElementById("capability-form-body"));
    const codes = nodes.map((node) => node.getAttribute("data-code")).filter(Boolean);
    assert(codes.indexOf("ui_keyword_rejected") !== -1, "typed unavailable shown");
    let caught = null;
    try {
      feature.buildRequest();
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "ui_form_missing", "unavailable form cannot be built");
  });

  await test("capability-form: preflight chain carries digests + human edits to provenance", async () => {
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const entry = availableEntry("electronic.energy", builtinForm());
    entry.descriptor_digest = "sha256:" + "9".repeat(64);
    let seen = null;
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, { forms: [entry] })),
      preflight: (body, scope) => {
        seen = { body: body, scope: scope };
        return Promise.resolve({
          schema_version: 1,
          executed: false,
          metering_consumed: false,
          transfers_attempted: false,
          plan: { workflow: "singlepoint", plan_digest: "sha256:" + "d".repeat(64) },
          preflight_fingerprint: "sha256:" + "d".repeat(64),
          estimate: { feasible: true, nproc: 2 },
        });
      },
    });
    const { feature } = loadForm(h, science, null, doc);
    await feature.load({ projectId: "proj-1" });
    feature.setValue("source", "O 0 0 0");
    feature.setValue("charge", 2);
    const viewModel = await feature.preflight({ projectId: "proj-1" });
    assertEq(seen.body.workflow, "singlepoint", "preflight body workflow");
    assertEq(seen.body.input.charge, 2, "preflight body carries the edited value");
    assertEq(seen.scope.projectId, "proj-1", "scope forwarded");
    assertEq(viewModel.plan_digest, "sha256:" + "d".repeat(64), "plan digest from preflight");
    assertEq(viewModel.descriptor_digest, "sha256:" + "9".repeat(64), "descriptor digest");
    assertEq(viewModel.form_digest, "sha256:" + "c".repeat(64), "form digest");
    assertEq(viewModel.provenance.human_edits.map((edit) => edit.field_path).sort(),
      ["task.charge", "task.structure.source"], "human edits recorded");
    const note = viewModel.provenance.human_edits[0].note;
    assert(note.indexOf("descriptor_digest=sha256:" + "9".repeat(64)) !== -1,
      "descriptor digest in provenance note");
    assert(note.indexOf("form_digest=sha256:" + "c".repeat(64)) !== -1,
      "form digest in provenance note");
    assertEq(viewModel.provenance.human_edits[0].edited_at, "2026-10-27T00:00:00.000Z",
      "edit timestamp from the injected clock");
    assertEq(viewModel.provenance.human_edits[0].editor_ref, "principal-workbench",
      "editor ref");
  });

  await test("capability-form: submit needs an approved record and a real TaskRef", async () => {
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    let submitted = null;
    const commands = {
      submit: (scope, body, opts) => {
        submitted = { body: body, opts: opts, scope: scope };
        if (!opts.approval) {
          const error = new Error("approval_required: no approved record");
          error.code = "approval_required";
          return Promise.reject(error);
        }
        return Promise.resolve({ job_id: "job-1", attempt: 1, task_ref: { job_id: "job-1", attempt: 1 }, submitted: true });
      },
    };
    const science = fakeScience({
      fetchCapabilityForms: () => Promise.resolve(formsDocument(uiSubset, {
        forms: [availableEntry("electronic.energy", builtinForm())],
      })),
    });
    const { feature } = loadForm(h, science, commands, doc);
    await feature.load({ projectId: "proj-1" });
    feature.setValue("source", "O 0 0 0");
    let caught = null;
    try {
      await feature.submit({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "approval_required", "submit refused without approval");
    feature.approval({ status: "approved", plan_digest: "sha256:" + "d".repeat(64) });
    const result = await feature.submit({ projectId: "proj-1" }, { idempotencyKey: "k-1" });
    assertEq(result.job_id, "job-1", "submitted task");
    assertEq(submitted.body.workflow, "singlepoint", "submit body is the preflight body");
    assertEq(submitted.opts.idempotencyKey, "k-1", "idempotency forwarded");
    assertEq(submitted.opts.approval.status, "approved", "approved record attached");
  });

  await test("capability-form: a drifted served subset is refused before rendering", async () => {
    const uiSubset = uiSubsetOf(h);
    const doc = makeDoc();
    const driven = formsDocument(uiSubset, { forms: [availableEntry("electronic.energy", builtinForm())] });
    driven.subset = uiSubset.subsetTable();
    driven.subset.bindings = driven.subset.bindings.slice(0, 1);
    const science = fakeScience({ fetchCapabilityForms: () => Promise.resolve(driven) });
    const { feature } = loadForm(h, science, null, doc);
    let caught = null;
    try {
      await feature.load({ projectId: "proj-1" });
    } catch (error) {
      caught = error;
    }
    assert(caught && caught.code === "ui_subset_drift", "drift is a typed refusal");
    const nodes = allNodes(doc.getElementById("capability-form-body"));
    const codes = nodes.map((node) => node.getAttribute("data-code")).filter(Boolean);
    assert(codes.indexOf("ui_subset_drift") !== -1, "refusal rendered");
  });

  // ------------------------------------------------------------------
  // 3. real client guard
  // ------------------------------------------------------------------
  await test("science client: validateCapabilityForms refuses a non-subset form", () => {
    const sandbox = makeSandbox(makeDom());
    loadScript(STORE_MODULE, sandbox);
    loadScript(SUBSET_MODULE, sandbox);
    loadScript(SCIENCE_CLIENT, sandbox);
    const validate = sandbox.ACPClient.science.validateCapabilityForms;
    const uiSubset = sandbox.ACPWorkbench.contracts.uiSubset;
    const good = formsDocument(uiSubset, { forms: [availableEntry("electronic.energy", builtinForm())] });
    assertEq(validate(good).schema, uiSubset.FORMS_SCHEMA, "valid document accepted");
    const bad = JSON.parse(JSON.stringify(good));
    bad.forms[0].form.form_schema.oneOf = [{ type: "object" }];
    let caught = null;
    try {
      validate(bad);
    } catch (error) {
      caught = error;
    }
    assert(caught && /ui_keyword_rejected/.test(caught.message), "oneOf refused by the client");
    const drift = JSON.parse(JSON.stringify(good));
    drift.subset.bindings = drift.subset.bindings.slice(0, 1);
    let driftError = null;
    try {
      validate(drift);
    } catch (error) {
      driftError = error;
    }
    assert(driftError && /ui_subset_drift/.test(driftError.message), "drift refused by the client");
    const digestMismatch = JSON.parse(JSON.stringify(good));
    digestMismatch.forms[0].form_digest = "sha256:" + "0".repeat(64);
    let digestError = null;
    try {
      validate(digestMismatch);
    } catch (error) {
      digestError = error;
    }
    assert(digestError && /ui_form_digest_mismatch/.test(digestError.message),
      "digest mismatch refused by the client");
  });

  await test("capability-form: no fetch in the feature/client science module", () => {
    [FEATURE, SCIENCE_CLIENT, SUBSET_MODULE].forEach((relative) => {
      const source = fs.readFileSync(path.join(REPO_ROOT, "frontend", relative), "utf8");
      assert(!/\bfetch\s*\(/.test(source), relative + " must not call fetch");
      assert(source.indexOf("XMLHttpRequest") === -1, relative + " must not use XHR");
    });
  });
};
