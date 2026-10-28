/*
 * M3-06 real-Controller driver (Node).
 *
 * Loads the REAL Workbench modules — the shared cache, the ui-subset validator,
 * the science client and the capability-form feature — and injects a REAL HTTP
 * transport built on Node's global fetch, pointed at a live uvicorn Controller
 * started by tests/test_acp2_m306_capability_form_live.py.  Nothing is faked:
 * capability-form discovery and preflight cross a socket, and the third-party
 * form comes from the installed (fixture) distribution's dist-info through the
 * platform's own metadata-only reader.
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
  now: () => "2026-10-27T00:00:00.000Z",
  editorRef: "m306-live-driver",
});

function typed(error) {
  return {
    ok: false,
    code: error && error.code ? error.code : null,
    status: error && (error.httpStatus || error.status) ? (error.httpStatus || error.status) : null,
    message: String((error && error.message) || error),
    stack: error && error.stack
      ? String(error.stack).split("\n").slice(0, 5).join(" | ") : null,
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
  throw new Error(message);
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
  const builtin = discovery.forms.filter((entry) => entry.capability_source !== "science_plugin");
  if (builtin.length < 3) fail("expected the built-in capability forms, got " + builtin.length);
  const pluginEntry = discovery.forms.filter((entry) => entry.capability_id === capability)[0];
  if (!pluginEntry) fail("the third-party capability form is not discovered");
  if (pluginEntry.state !== "available") fail("plugin form state: " + pluginEntry.state);
  if (String(pluginEntry.form_origin || "").indexOf("plugin:") !== 0) {
    fail("plugin form origin: " + pluginEntry.form_origin);
  }
  if (pluginEntry.form_digest !== scenario.form_digest) {
    fail("plugin form digest: " + pluginEntry.form_digest);
  }

  form.select(capability);
  const selected = form.getState();
  if (selected.selected !== capability) fail("select did not stick");
  if (!selected.fields.length) fail("the third-party form has no fields");
  const bindings = selected.fields.map((field) => field.binding).sort();
  if (bindings.join(",") !== scenario.expected_bindings.join(",")) {
    fail("plugin form bindings: " + bindings.join(","));
  }

  form.setValue("source", scenario.structure_text);
  form.setValue("charge", 1);
  form.setValue("nproc", 2);
  steps.push({
    name: "form.values",
    ok: true,
    value: { values: form.getState().values },
  });

  const viewModel = await step("preflight", () => form.preflight(scope));
  if (viewModel.request.body.workflow !== scenario.expected_workflow) {
    fail("preflight workflow: " + viewModel.request.body.workflow);
  }
  if (viewModel.request.body.input.charge !== 1) {
    fail("preflight body charge: " + JSON.stringify(viewModel.request.body.input));
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(String(viewModel.plan_digest || ""))) {
    fail("preflight plan digest: " + viewModel.plan_digest);
  }
  if (viewModel.plan_digest !== viewModel.preflight_fingerprint) {
    fail("plan digest and fingerprint diverged");
  }
  if (viewModel.form_digest !== scenario.form_digest) fail("view model form digest");
  if (viewModel.subset_digest !== scenario.subset_digest) fail("view model subset digest");
  const edited = viewModel.provenance.human_edits.map((edit) => edit.field_path).sort();
  if (edited.join(",") !== scenario.expected_edits.join(",")) {
    fail("human edits: " + edited.join(","));
  }
  if (viewModel.provenance.human_edits[0].note.indexOf("descriptor_digest=") === -1) {
    fail("human edit note carries no descriptor digest");
  }
  steps.push({
    name: "view_model",
    ok: true,
    value: {
      plan_digest: viewModel.plan_digest,
      form_digest: viewModel.form_digest,
      descriptor_digest: viewModel.descriptor_digest,
      fields: viewModel.request.fields.map((field) => field.binding + "=" + field.task_request),
      human_edits: viewModel.provenance.human_edits,
    },
  });

  // The descriptor discovery surface still answers with the same capability, so
  // the form and the descriptor come from the same catalog build.
  const capabilities = await step("discovery.capabilities", () =>
    science.fetchCapabilities(scope, { generation: "m306-live" }));
  const declared = (capabilities.capabilities.descriptors.capabilities || [])
    .filter((entry) => entry.capability_id === capability);
  if (declared.length !== 1) fail("descriptor discovery lost the plugin capability");
  if (declared[0].descriptor_digest !== pluginEntry.descriptor_digest) {
    fail("descriptor digest differs between the two discovery surfaces");
  }
  steps.push({
    name: "descriptor_cross_check",
    ok: true,
    value: { descriptor_digest: declared[0].descriptor_digest },
  });

  console.log(JSON.stringify({ ok: true, steps: steps }));
}

main().catch((error) => {
  console.log(JSON.stringify({ ok: false, steps: steps, error: typed(error) }));
  process.exitCode = 1;
});
