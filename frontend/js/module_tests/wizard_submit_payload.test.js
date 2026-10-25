/*
 * D12 default-page-to-payload behavior (module slice).
 *
 * Drives the REAL wizard submit path (draft-form.js state/defaults +
 * submit-edit.js payload builder) with a catalog fixture derived at runtime
 * from acp.catalog.METHOD_SCHEMAS / FIELD_DEFINITIONS — never a duplicated
 * field list. Contracts pinned here (mirrored by the real-page assembly test
 * in tests/test_acp_wizard_browser.py; the module test alone is NOT
 * sufficient):
 *   (a) an xtbmd_censo_energy body forwards every field of the catalog
 *       control levels xtb_md / xtb_opt / isostat, including explicit 0/false;
 *   (b) energy and xtbmd_censo_energy get different rank1-only defaults
 *       (real openMethodConfig assignment, draft-form.js);
 *   (c) an active workflow (singlepoint) body does not drop catalog fields;
 *   (d) retired defaults only affect the payload in the historical-compat
 *       branch (explicit retired id with the workflow catalog unavailable);
 *       active selections resolve from the live catalog and a retired id
 *       self-heals instead of winning.
 *
 * Historical-compat note: with the live workflow catalog loaded,
 * ensureWizardWorkflowValid() self-heals any retired workflow id to the live
 * default, so the retired energyLike branch is reachable only via the
 * historical state (restored draft / edit of historical data) while the
 * workflow catalog is unavailable — that is the branch under test for (a)/(b).
 *
 * Negative control (QA only, never production): ACP_WIZARD_SUBMIT_MUTATION=
 *   drop_isostat_edis        drop the isostat.edis field from the fixture
 *   undef_zero_md_seed       replace the explicit 0 md_seed with undefined
 *   retired_default_for_active  resolve the active drive from the retired
 *                              energy schema instead of the live catalog
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const FRONTEND = path.resolve(__dirname, "..", "..");
const REPO = path.resolve(FRONTEND, "..");
const MUTATION = process.env.ACP_WIZARD_SUBMIT_MUTATION || "";

function resolvePython() {
  const candidates = process.env.ACP_PYTHON
    ? [process.env.ACP_PYTHON]
    : ["python3.11", "python3", "python"];
  for (const py of candidates) {
    try {
      execFileSync(py, ["-c", "import acp.catalog"], { cwd: REPO, stdio: "pipe" });
      return py;
    } catch (_e) {
      continue;
    }
  }
  throw new Error(
    "no acp-importable Python interpreter found (tried: " + candidates.join(", ") + ")"
  );
}

const DUMP_CATALOG_PY = [
  "import json",
  "from acp.catalog import (FIELD_DEFINITIONS, METHOD_SCHEMAS,",
  "    get_method_catalog, get_workflow_catalog)",
  "print(json.dumps({",
  "    'workflows': get_workflow_catalog(),",
  "    'method_catalog': get_method_catalog(),",
  "    'schemas': METHOD_SCHEMAS,",
  "    'field_definitions': FIELD_DEFINITIONS,",
  "}))",
].join("\n");

let _catalog = null;
function loadCatalog() {
  if (_catalog) return _catalog;
  const out = execFileSync(resolvePython(), ["-c", DUMP_CATALOG_PY], {
    cwd: REPO,
    maxBuffer: 1 << 26,
  });
  _catalog = JSON.parse(out.toString());
  return _catalog;
}

const CONTROL_LEVEL_IDS = ["xtb_md", "xtb_opt", "isostat"];
const RETIRED_SCHEMA_IDS = ["censo_energy", "censo_ensemble", "xtbmd_censo_energy"];
const ENERGY_LIKE_KEYS = ["rank1_only", "threshold", "no_opt", "preset"];

function controlFieldSet(schemas) {
  const schema = schemas.xtbmd_censo_energy || {};
  const keys = [];
  (schema.method_levels || []).forEach((lv) => {
    if (CONTROL_LEVEL_IDS.indexOf(lv.level_id) < 0) return;
    (lv.fields || []).forEach((f) => {
      if (keys.indexOf(f) < 0) keys.push(f);
    });
  });
  return keys;
}

function fixtureValue(field, fieldDefs) {
  const type = ((fieldDefs || {})[field] || {}).type;
  if (type === "bool") return false;
  if (type === "int" || type === "float") return 0;
  if (type === "module") return { mode: "fix_" + field };
  return "fix_" + field;
}

function buildXtbmdStages(catalog) {
  const schema = catalog.schemas.xtbmd_censo_energy;
  const profile =
    (schema.profiles || []).find((p) => p.profile_id === "censo-light") ||
    (schema.profiles || [])[0];
  const stages = JSON.parse(JSON.stringify(profile.levels));
  (schema.method_levels || []).forEach((lv) => {
    if (CONTROL_LEVEL_IDS.indexOf(lv.level_id) < 0) return;
    if (!stages[lv.level_id]) {
      stages[lv.level_id] = { engine: (lv.allowed_engines || [""])[0] };
    }
    (lv.fields || []).forEach((f) => {
      stages[lv.level_id][f] = fixtureValue(f, catalog.field_definitions);
    });
  });
  if (MUTATION === "drop_isostat_edis") delete stages.isostat.edis;
  if (MUTATION === "undef_zero_md_seed") stages.xtb_md.md_seed = undefined;
  return stages;
}

function buildActiveStages(catalog, workflowId, schemas) {
  const live = catalog.workflows.find((w) => w.id === workflowId && w.status === "active");
  let schemaId = live.method_schema_id;
  let schema = schemas[schemaId];
  if (MUTATION === "retired_default_for_active") {
    schemaId = "censo_energy";
    schema = schemas[schemaId];
  }
  const stages = {};
  (schema.method_levels || []).forEach((lv) => {
    const stage = { engine: (lv.allowed_engines || [""])[0] };
    (lv.fields || []).forEach((f) => {
      stage[f] = fixtureValue(f, catalog.field_definitions);
    });
    stages[lv.level_id] = stage;
  });
  return { stages, schemaId, schema };
}

function extractIifeSource(file, marker) {
  const src = fs.readFileSync(file, "utf8");
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("marker not found in " + file + ": " + marker);
  const open = src.indexOf("{", start + marker.length - 1);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1) + ")();";
    }
  }
  throw new Error("closing brace not found for " + marker);
}

function makeEl(override) {
  const el = {
    style: {},
    className: "",
    textContent: "",
    innerHTML: "",
    value: "",
    title: "",
    checked: false,
    hidden: false,
    dataset: {},
    children: [],
    type: "text",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, f) {
        if (f === undefined) { if (this._s.has(c)) this._s.delete(c); else this._s.add(c); }
        else if (f) this._s.add(c); else this._s.delete(c);
      },
      contains(c) { return this._s.has(c); },
    },
    setAttribute(k, v) { this[k] = v; },
    removeAttribute(k) { delete this[k]; },
    appendChild(c) { this.children.push(c); return c; },
    prepend(c) { this.children.unshift(c); return c; },
    replaceChildren() { this.children = []; },
    replaceWith() {},
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    removeEventListener() {},
    remove() {},
    focus() {},
  };
  return Object.assign(el, override || {});
}

function buildSandbox(catalog, opts) {
  opts = opts || {};
  const els = {};
  const captured = [];
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    Date, Math, JSON, URLSearchParams, AbortController, Promise,
    Error, Object, Array, String, Number, Boolean, Map, Set, encodeURIComponent,
  };
  sandbox.window = sandbox;
  sandbox.document = {
    getElementById(id) {
      if (!els[id]) els[id] = makeEl({ id });
      return els[id];
    },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    createElement() { return makeEl(); },
    addEventListener() {},
    body: makeEl(),
    documentElement: makeEl(),
  };
  ["modal-nproc", "modal-mem", "modal-mem-unit", "modal-remark", "modal-task-name",
    "modal-parallelism", "modal-charge", "modal-mult", "modal-project-select",
    "mc-levels-container", "mc-profile-select", "mc-modal-title", "mc-modal-subtitle",
    "method-config-modal", "job-modal"].forEach((id) => {
    els[id] = makeEl({ id, value: "" });
  });
  els["modal-nproc"].value = "4";
  els["modal-mem"].value = "8";
  els["modal-mem-unit"].value = "GB";
  els["modal-charge"].value = "0";
  els["modal-mult"].value = "1";

  sandbox.t = (k) => k;
  sandbox.currentLang = "en-US";
  sandbox.alert = () => {};
  sandbox.confirm = () => true;
  sandbox.ACPMemoryInput = { read: () => 8000 };
  sandbox.wizardStructures = [{ name: "mol_1", xyz: "1\n\nH 0 0 0", source_type: "xyz_text" }];
  sandbox.wizardSelectedStructureIndex = 0;
  sandbox.wizardInputMode = "structure";
  sandbox.refreshJobs = () => Promise.resolve();
  sandbox.renderLoadedStructuresList = () => {};
  sandbox.applyNodeSelectionToBody = (body) => body;
  sandbox.structureMoleculeName = (s) => s.name;
  sandbox.formatSubmitError = (e) => String((e && e.message) || e);
  sandbox.invalidateTaskViewMetadata = () => {};
  sandbox.setWorkbenchLoadError = () => {};
  sandbox.resolveCurrentProfileLabel = () => "label";
  sandbox.updateInputModeVisibility = () => {};
  sandbox.updateScanKindButtons = () => {};
  sandbox.scheduleNodeMatchingRefresh = () => {};
  sandbox.hookMechanismBuilder = () => {};
  sandbox.createReactionDefinitionState = () => ({});
  sandbox.cloneMechanismCoordinateSpec = (x) => JSON.parse(JSON.stringify(x));
  sandbox.ACPWizardUploadParse = {
    setLastParsedStructureInput: () => {},
    setLastParsedUploadName: () => {},
  };
  sandbox.ACPWizardPreviewValidate = {
    abortBatchPreviews: () => {},
    createValidation: () => ({
      schedule: () => {},
      validate: () => Promise.resolve({ ok: true, issues: [] }),
      refresh: () => {},
    }),
    createHessianPreview: () => ({ schedule: () => {} }),
    createBatchConfigPreview: () => ({
      fetch: () => Promise.resolve({}),
      renderServer: () => {},
      renderFailure: () => {},
      renderLocal: () => {},
    }),
  };
  sandbox.apiRequest = () => Promise.resolve({});
  sandbox.api = (p, o) => {
    if (o && o.body) captured.push({ path: p, body: JSON.parse(o.body) });
    if (p === "/workflow-catalog") {
      if (opts.workflowCatalogFails) return Promise.reject(new Error("HTTP 503"));
      return Promise.resolve({ workflows: catalog.workflows });
    }
    if (p === "/method-catalog") return Promise.resolve(catalog.method_catalog);
    return Promise.resolve({});
  };

  const catalogUtilsSrc = extractIifeSource(
    path.join(FRONTEND, "js", "workbench", "features", "wizard.js"),
    "var CatalogUtils = (function() {"
  );
  vm.createContext(sandbox);
  vm.runInContext(
    catalogUtilsSrc.slice("var ".length),
    sandbox,
    { filename: "wizard.js#CatalogUtils" }
  );
  sandbox.lookupCI = sandbox.CatalogUtils.lookupCI;

  const load = (rel) => {
    const file = path.join(FRONTEND, rel);
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
  };
  load("js/workbench/features/wizard/draft-form.js");
  const DF = sandbox.window.ACPWizardDraftForm;
  Object.keys(DF).forEach((n) => {
    sandbox[n] = function () { return DF[n].apply(null, arguments); };
  });
  load("js/workbench/features/wizard/submit-edit.js");
  const SE = sandbox.window.ACPWizardSubmitEdit;
  Object.keys(SE).forEach((n) => {
    sandbox[n] = function () { return SE[n].apply(null, arguments); };
  });

  return {
    sandbox,
    captured,
    DF,
    M: SE,
  };
}

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;
  const catalog = loadCatalog();
  const schemas = catalog.schemas;
  const ctrlFields = controlFieldSet(schemas);
  assert(ctrlFields.length >= 20, "METHOD_SCHEMAS control field set too small: " + ctrlFields.length);

  async function loadCatalogs(env) {
    await env.DF.loadWorkflowCatalog();
    await env.DF.loadMethodCatalog();
  }

  async function driveAndSubmit(env, workflow, schemaId, profileId, stages, rank1) {
    const sb = env.sandbox;
    sb.wizardState.workflow = { id: workflow, label: workflow, schema_id: schemaId };
    sb.wizardState.method.profile_id = profileId;
    sb.wizardState.method.stages = stages;
    if (rank1 !== undefined) {
      sb.wizardState.method.rank1_only = rank1;
      sb.wizardState.method.rank1_wf = workflow;
    } else {
      delete sb.wizardState.method.rank1_only;
      delete sb.wizardState.method.rank1_wf;
    }
    env.captured.length = 0;
    await env.M.submitJobModal();
    assertEq(env.captured.length, 1, "expected exactly one captured /jobs body");
    return env.captured[0].body;
  }

  await test("wizard_submit (a): xtbmd body forwards every METHOD_SCHEMAS control field incl explicit 0/false", async () => {
    const env = buildSandbox(catalog, { workflowCatalogFails: true });
    await loadCatalogs(env);
    const stages = buildXtbmdStages(catalog);
    const body = await driveAndSubmit(
      env, "xtbmd_censo_energy", "xtbmd_censo_energy", "censo-light", stages, false
    );
    assertEq(body.workflow, "xtbmd_censo_energy", "historical-compat body must carry the retired workflow id");
    assertEq(body.method.schema_id, "xtbmd_censo_energy");
    ctrlFields.forEach((f) => {
      assert(f in body.method, "control field missing from body.method: " + f);
    });
    if (MUTATION !== "drop_isostat_edis") {
      assertEq(body.method.edis, stages.isostat.edis, "isostat.edis not forwarded verbatim");
    }
    ctrlFields.forEach((f) => {
      const want = (stages.xtb_md || {})[f] !== undefined ? stages.xtb_md[f]
        : (stages.xtb_opt || {})[f] !== undefined ? stages.xtb_opt[f]
        : stages.isostat[f];
      assertEq(body.method[f], want, "control field value changed in transit: " + f);
    });
    assertEq(body.method.md_seed, 0, "explicit int 0 must survive serialization");
    assertEq(body.method.max_frames, 0, "explicit int 0 must survive serialization");
    assertEq(body.method.edis, 0, "explicit float 0 must survive serialization");
    assertEq(body.method.md_shake, false, "explicit false must survive serialization");
    assertEq(body.method.md_nvt, false, "explicit false must survive serialization");
    assertEq(body.method.resume, false, "explicit false must survive serialization");
    assertEq(body.method.conv_check, false, "explicit false must survive serialization");
  });

  await test("wizard_submit (b): energy vs xtbmd_censo_energy get different rank1-only defaults", async () => {
    const env = buildSandbox(catalog, { workflowCatalogFails: true });
    await loadCatalogs(env);
    const sb = env.sandbox;

    sb.wizardState.workflow = { id: "energy", label: "energy", schema_id: "censo_energy" };
    delete sb.wizardState.method.rank1_only;
    delete sb.wizardState.method.rank1_wf;
    await sb.openMethodConfig();
    const energyDefault = sb.wizardState.method.rank1_only;
    assertEq(energyDefault, true, "energy rank1-only default must be ON");

    sb.wizardState.workflow = { id: "xtbmd_censo_energy", label: "xtbmd", schema_id: "xtbmd_censo_energy" };
    delete sb.wizardState.method.rank1_only;
    delete sb.wizardState.method.rank1_wf;
    await sb.openMethodConfig();
    const xtbmdDefault = sb.wizardState.method.rank1_only;
    assertEq(xtbmdDefault, false, "xtbmd_censo_energy rank1-only default must be the full ensemble");
    assert(energyDefault !== xtbmdDefault, "rank1-only defaults must differ per workflow");

    const energySchema = schemas.censo_energy;
    const energyProfile = (energySchema.profiles || [])[0];
    const energyBody = await driveAndSubmit(
      env, "energy", "censo_energy", energyProfile.profile_id,
      JSON.parse(JSON.stringify(energyProfile.levels)), energyDefault
    );
    const xtbmdStages = buildXtbmdStages(catalog);
    const xtbmdBody = await driveAndSubmit(
      env, "xtbmd_censo_energy", "xtbmd_censo_energy", "censo-light", xtbmdStages, xtbmdDefault
    );
    assertEq(energyBody.method.rank1_only, true, "energy body must forward rank1-only default ON");
    assertEq(xtbmdBody.method.rank1_only, false, "xtbmd body must forward rank1-only default OFF");
  });

  await test("wizard_submit (c): active singlepoint body keeps every catalog field", async () => {
    const env = buildSandbox(catalog, {});
    await loadCatalogs(env);
    const active = buildActiveStages(catalog, "singlepoint", schemas);
    const body = await driveAndSubmit(
      env, "singlepoint", active.schemaId, "default", active.stages, undefined
    );
    assertEq(body.workflow, "singlepoint");
    const live = catalog.workflows.find((w) => w.id === "singlepoint");
    assertEq(body.method.schema_id, live.method_schema_id, "active body schema must come from the live catalog");
    (active.schema.method_levels || []).forEach((lv) => {
      const got = (body.method.levels || {})[lv.level_id];
      assert(got, "active body dropped catalog level: " + lv.level_id);
      (lv.fields || []).forEach((f) => {
        assert(f in got, "active body dropped catalog field: " + lv.level_id + "." + f);
        assertEq(got[f], active.stages[lv.level_id][f], "catalog field changed in transit: " + lv.level_id + "." + f);
      });
    });
    ENERGY_LIKE_KEYS.forEach((k) => {
      assert(!(k in body.method), "retired energyLike default leaked into active body: " + k);
    });
  });

  await test("wizard_submit (d): retired defaults confined to historical-compat; active selections come from the live catalog", async () => {
    const env = buildSandbox(catalog, {});
    await loadCatalogs(env);
    const sb = env.sandbox;

    const activeIds = catalog.workflows.filter((w) => w.status === "active").map((w) => w.id);
    ["energy", "xtbmd_censo_energy", "ensemble"].forEach((retiredId) => {
      const resolved = sb.resolveDefaultWorkflow(retiredId);
      assert(resolved, "resolveDefaultWorkflow must resolve once the live catalog is loaded");
      assert(resolved.id !== retiredId, "retired default literal must not win for " + retiredId);
      assert(activeIds.indexOf(resolved.id) >= 0, "resolution must come from the live active catalog: " + resolved.id);
    });
    const activeResolved = sb.resolveDefaultWorkflow("singlepoint");
    assertEq(activeResolved.id, "singlepoint");
    assertEq(activeResolved.method_schema_id, "dft_singlepoint");

    const xtbmdStages = buildXtbmdStages(catalog);
    const healedBody = await driveAndSubmit(
      env, "xtbmd_censo_energy", "xtbmd_censo_energy", "censo-light", xtbmdStages, false
    );
    assert(healedBody.workflow !== "xtbmd_censo_energy",
      "live catalog must self-heal a retired selection instead of submitting it");
    assert(activeIds.indexOf(healedBody.workflow) >= 0, "healed body must target a live active workflow");
    assertEq(healedBody.method.schema_id,
      (sb.resolveDefaultWorkflow("xtbmd_censo_energy") || {}).method_schema_id);
    RETIRED_SCHEMA_IDS.forEach((sid) => {
      assert(healedBody.method.schema_id !== sid, "retired schema leaked into healed body: " + sid);
    });

    const histEnv = buildSandbox(catalog, { workflowCatalogFails: true });
    await loadCatalogs(histEnv);
    const histStages = buildXtbmdStages(catalog);
    const histBody = await driveAndSubmit(
      histEnv, "xtbmd_censo_energy", "xtbmd_censo_energy", "censo-light", histStages, false
    );
    assertEq(histBody.workflow, "xtbmd_censo_energy",
      "retired workflow payload must only appear via the historical-compat branch");
  });

  await test("wizard_submit runner guard: empty/unregistered module runs are rejected", async () => {
    const res = require("child_process").spawnSync(
      process.execPath,
      [path.join(__dirname, "run_all.js"), "__no_such_module__"],
      { encoding: "utf8" }
    );
    assert(res.status !== 0, "0-collected run must exit non-zero (got " + res.status + ")");
    assert(/0 passed, 0 failed/.test(res.stdout), "runner must still report the 0/0 tally: " + res.stdout);
    assert(/REJECTED/.test(res.stdout), "runner must print an explicit rejection");
  });
};
