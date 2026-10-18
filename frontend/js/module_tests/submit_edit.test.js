/*
 * Behavior tests for the wizard S4 submit/edit-recalc slice
 * (frontend/js/workbench/features/wizard/submit-edit.js, plan todo 20).
 *
 * The slice is a browser IIFE whose bodies only touch the DOM at call time,
 * so it loads in the vm sandbox with globals stubbed per test. Two contracts
 * are pinned:
 *   - the EDIT-mode `POST /jobs` interception (compat/api.js:14-18) is the
 *     single edit-recalc chokepoint: while an editor session is active a
 *     submit branch must collect the payload — never create a new task;
 *   - the submit fan-out / validation helpers forward their exact payloads
 *     and errors (409/422 status+body preserved; a timeout is never resent).
 *
 * Red-ability: set ACP_SUBMIT_EDIT_API_JS to a mutated copy of compat/api.js
 * with the interception disabled; the "edit-mode POST /jobs is intercepted"
 * test must FAIL (see evidence task-20 QA).
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const API_JS = process.env.ACP_SUBMIT_EDIT_API_JS
  ? process.env.ACP_SUBMIT_EDIT_API_JS
  : path.resolve(__dirname, "..", "workbench", "compat", "api.js");
const MODULE_REL = "js/workbench/features/wizard/submit-edit.js";

/* Extract one top-level function declaration from a source file (brace-matched). */
function extractFunctionSource(file, marker) {
  const src = fs.readFileSync(file, "utf8");
  const start = src.indexOf(marker);
  if (start < 0) throw new Error(`marker not found in ${file}: ${marker}`);
  let depth = 0;
  for (let i = start + marker.length - 1; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`closing brace not found for ${marker}`);
}

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function buildApi(overrides) {
    const sandbox = makeSandbox(overrides || {});
    sandbox.API_BASE = "/api/v1";
    sandbox.invalidateTaskViewMetadata = function () {};
    sandbox.apiRequest = function (url, opts) {
      return sandbox.__transport(url, opts);
    };
    const apiSrc = extractFunctionSource(API_JS, "async function api(path, opts) {");
    const fn = vm.runInContext(
      "(" + apiSrc + ")",
      vm.createContext(sandbox),
      { filename: API_JS + "#api" }
    );
    return { api: fn, sb: sandbox, sandbox };
  }

  await test("submit-edit module exposes the S4 entry points", () => {
    const sandbox = loadScript(MODULE_REL, makeSandbox());
    const M = sandbox.window.ACPWizardSubmitEdit;
    assert(M, "window.ACPWizardSubmitEdit missing");
    for (const name of [
      "submitStageWorkflowJob", "jobFormValidationFailed", "submitJobBatch",
      "buildPESProtocolFromMethod", "submitPESsearchTask", "handleModalSubmit",
      "submitJobModal", "initializeMethodDefaults",
    ]) {
      assert(typeof M[name] === "function", `missing entry point: ${name}`);
    }
  });

  await test("edit-mode POST /jobs is INTERCEPTED — no new task, transport untouched", async () => {
    const captured = [];
    let transportCalls = 0;
    const body = {
      workflow: "BatchOptimize",
      molecule_name: "mol_1",
      input: { source_type: "xyz_text", source: "1\n\nH 0 0 0" },
      method: { profile_id: "opt_freq" },
      resources: { nproc: 4, mem: 8000 },
    };
    const { api, sb } = buildApi({
      __transport: () => { transportCalls++; return Promise.resolve({ job_id: "NEW-TASK" }); },
    });
    sb.ACPJobEditor = {
      isActive: () => true,
      interceptJobCreate: (payload) => { captured.push(payload); return { job_id: "__edit_collected__", status: "collected" }; },
    };
    const out = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    assertEq(captured.length, 1, "interceptJobCreate must be called exactly once");
    assertEq(captured[0], body, "collected payload must be byte-identical to the submit body");
    assertEq(out.job_id, "__edit_collected__", "edit mode returns the collecting receipt");
    assertEq(transportCalls, 0, "edit mode must NEVER create a task via the transport");
  });

  await test("edit-mode POST /jobs: non-JSON body collects {} (legacy parse fallback)", async () => {
    const captured = [];
    let transportCalls = 0;
    const sb = makeSandbox({
      __transport: () => { transportCalls++; return Promise.resolve({}); },
    });
    sb.API_BASE = "/api/v1";
    sb.invalidateTaskViewMetadata = function () {};
    sb.apiRequest = function (url, opts) { return sb.__transport(url, opts); };
    sb.ACPJobEditor = {
      isActive: () => true,
      interceptJobCreate: (payload) => { captured.push(payload); return {}; },
    };
    const apiSrc = extractFunctionSource(API_JS, "async function api(path, opts) {");
    const api = vm.runInContext("(" + apiSrc + ")", vm.createContext(sb), { filename: API_JS + "#api" });
    await api("/jobs", { method: "POST", body: "{not json" });
    assertEq(captured.length, 1);
    assertEq(captured[0], {}, "unparsable body must collect {} (catch(e){} preserved)");
    assertEq(transportCalls, 0);
  });

  await test("non-edit POST /jobs hits the transport once; 409 status+body propagate, no retry", async () => {
    let calls = 0;
    const sb = makeSandbox({
      __transport: () => {
        calls++;
        const err = new Error("name_revision_conflict");
        err.status = 409;
        err.body = { detail: { current: { name_revision: 4 } } };
        return Promise.reject(err);
      },
    });
    sb.API_BASE = "/api/v1";
    sb.invalidateTaskViewMetadata = function () {};
    sb.apiRequest = function (url, opts) { return sb.__transport(url, opts); };
    sb.ACPJobEditor = { isActive: () => false };
    const apiSrc = extractFunctionSource(API_JS, "async function api(path, opts) {");
    const api = vm.runInContext("(" + apiSrc + ")", vm.createContext(sb), { filename: API_JS + "#api" });

    let caught = null;
    await api("/jobs", { method: "POST", body: "{}" }).catch((e) => { caught = e; });
    assert(caught && caught.status === 409, "409 status must propagate");
    assertEq(caught.body.detail.current.name_revision, 4, "409 body must be preserved");
    assertEq(calls, 1, "a failed submit must not be retried");

    // 422 variant
    let calls422 = 0;
    const sb2 = makeSandbox({
      __transport: () => {
        calls422++;
        const err = new Error("validation failed");
        err.status = 422;
        err.body = { detail: [{ loc: ["body"], msg: "bad" }] };
        return Promise.reject(err);
      },
    });
    sb2.API_BASE = "/api/v1";
    sb2.invalidateTaskViewMetadata = function () {};
    sb2.apiRequest = function (url, opts) { return sb2.__transport(url, opts); };
    sb2.ACPJobEditor = { isActive: () => false };
    const api2 = vm.runInContext("(" + apiSrc + ")", vm.createContext(sb2), { filename: API_JS + "#api" });
    let caught2 = null;
    await api2("/jobs", { method: "POST", body: "{}" }).catch((e) => { caught2 = e; });
    assert(caught2 && caught2.status === 422, "422 status must propagate");
    assertEq(caught2.body.detail[0].msg, "bad", "422 body must be preserved");
    assertEq(calls422, 1);
  });

  await test("submit timeout (status 0): exactly one attempt, no automatic resend", async () => {
    let calls = 0;
    const sb = makeSandbox({
      __transport: () => {
        calls++;
        const err = new Error("The operation was aborted due to timeout");
        err.status = 0;
        return Promise.reject(err);
      },
    });
    sb.API_BASE = "/api/v1";
    sb.invalidateTaskViewMetadata = function () {};
    sb.apiRequest = function (url, opts) { return sb.__transport(url, opts); };
    sb.ACPJobEditor = { isActive: () => false };
    const apiSrc = extractFunctionSource(API_JS, "async function api(path, opts) {");
    const api = vm.runInContext("(" + apiSrc + ")", vm.createContext(sb), { filename: API_JS + "#api" });
    let caught = null;
    await api("/jobs", { method: "POST", body: "{}" }).catch((e) => { caught = e; });
    assert(caught, "timeout error surfaced to the caller");
    assertEq(calls, 1, "submit must not resend after a timeout");
  });

  await test("handleModalSubmit: editor-active routes to checkAndSubmit (single submit entry)", () => {
    const sandbox = makeSandbox();
    let checked = 0;
    let steps = [];
    sandbox.ACPJobEditor = { isActive: () => true, checkAndSubmit: () => { checked++; return "CHECKED"; } };
    sandbox.setCreateWizardStep = (s) => { steps.push(s); };
    sandbox.createWizardStep = 3;
    loadScript(MODULE_REL, sandbox);
    const out = sandbox.window.ACPWizardSubmitEdit.handleModalSubmit();
    assertEq(out, "CHECKED");
    assertEq(checked, 1);
    assertEq(steps, [], "editor-active path must not advance the create wizard");
  });

  await test("handleModalSubmit: create flow advances steps below step 3", () => {
    const sandbox = makeSandbox();
    let steps = [];
    sandbox.ACPJobEditor = { isActive: () => false };
    sandbox.setCreateWizardStep = (s) => { steps.push(s); };
    sandbox.createWizardStep = 1;
    loadScript(MODULE_REL, sandbox);
    sandbox.window.ACPWizardSubmitEdit.handleModalSubmit();
    assertEq(steps, [2], "step gate must advance 1 -> 2 before submitting");
  });

  await test("jobFormValidationFailed: collecting throws, otherwise alerts (P-8 preserved)", () => {
    const sandbox = makeSandbox();
    sandbox.ACPJobEditor = { isCollecting: () => true };
    loadScript(MODULE_REL, sandbox);
    let thrown = null;
    try { sandbox.window.ACPWizardSubmitEdit.jobFormValidationFailed("boom"); }
    catch (e) { thrown = e; }
    assert(thrown && thrown.message === "boom", "collecting mode must throw the message");

    const alerts = [];
    sandbox.ACPJobEditor = { isCollecting: () => false };
    sandbox.alert = (m) => { alerts.push(m); };
    sandbox.window.ACPWizardSubmitEdit.jobFormValidationFailed("please fix");
    assertEq(alerts, ["please fix"], "non-collecting mode must alert");
  });

  await test("submitJobBatch: one POST /jobs per body, exact payloads, concurrent fan-out", async () => {
    const sandbox = makeSandbox();
    const calls = [];
    sandbox.api = (path, opts) => {
      calls.push({ path, opts });
      return new Promise((resolve) => setTimeout(() => resolve({ ok: true }), 0));
    };
    loadScript(MODULE_REL, sandbox);
    const b1 = { workflow: "singlepoint", n: 1 };
    const b2 = { workflow: "optimize", n: 2 };
    await sandbox.window.ACPWizardSubmitEdit.submitJobBatch([b1, b2]);
    assertEq(calls.length, 2, "one request per body");
    for (const c of calls) {
      assertEq(c.path, "/jobs");
      assertEq(c.opts.method, "POST");
      assertEq(c.opts.headers, { "Content-Type": "application/json" });
    }
    const sent = calls.map((c) => JSON.parse(c.opts.body));
    assertEq(sent[0], b1, "batch body 1 verbatim");
    assertEq(sent[1], b2, "batch body 2 verbatim");
  });
};
