/* R4 behavior tests: files/logs session discipline.
 *
 * files-logs.js is a page script (top-level functions over shared page
 * globals), so the module under test here is the extracted pure session-guard
 * decision `window.ACPWorkbench.contracts.filesSessionUnchanged` plus the
 * capture/accept wrappers it backs (both live as page globals after load).
 * Covered: same-session acceptance, token mismatch (A→B AND same-job A→A
 * reselect), jobId mismatch fallback, cleared-selection rejection, page-global
 * fallback when no task session exists, wiring pins, source purity.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(pageOverrides) {
    const sandbox = makeSandbox(Object.assign({
      // files-logs.js subscribes to the shared media query at load time.
      _logMediaQuery: { matches: false, addEventListener: function () {}, removeEventListener: function () {} },
      selectedJobId: "",
    }, pageOverrides || {}));
    loadScript("js/workbench/features/files-logs.js", sandbox);
    return sandbox;
  }

  function withTaskSession(sandbox, current) {
    sandbox.window.ACPWorkbench = sandbox.window.ACPWorkbench || {};
    sandbox.window.ACPWorkbench.controllers = sandbox.window.ACPWorkbench.controllers || {};
    sandbox.window.ACPWorkbench.controllers.taskSession = { current: current };
  }

  await test("module loads in a page-shaped sandbox and exposes the pure guard on ACPWorkbench.contracts", () => {
    const sandbox = setup();
    const contracts = sandbox.window.ACPWorkbench.contracts;
    assert(contracts && typeof contracts.filesSessionUnchanged === "function", "guard exported");
    assert(typeof sandbox.filesSessionCapture === "function", "capture wrapper is a page global");
    assert(typeof sandbox.filesSessionAccepts === "function", "accept wrapper is a page global");
  });

  await test("guard accepts the same session (token identity)", () => {
    const { filesSessionUnchanged } = setup().window.ACPWorkbench.contracts;
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: 7 }, { jobId: "job-A", token: 7 }), true);
  });

  await test("guard rejects token mismatch — A→B and same-job A→A reselect/rerun", () => {
    const { filesSessionUnchanged } = setup().window.ACPWorkbench.contracts;
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: 7 }, { jobId: "job-B", token: 8 }), false, "A→B");
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: 7 }, { jobId: "job-A", token: 9 }), false,
      "same job, fresh session token (reselect/rerun) must not inherit the old response");
  });

  await test("guard falls back to jobId compare only when BOTH tokens are absent", () => {
    const { filesSessionUnchanged } = setup().window.ACPWorkbench.contracts;
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: null }, { jobId: "job-A", token: null }), true, "fallback same job");
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: null }, { jobId: "job-B", token: null }), false, "fallback job switch");
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: 7 }, { jobId: "job-A", token: null }), false,
      "captured token but session now absent → cannot prove currency → reject");
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: null }, { jobId: "job-A", token: 7 }), false,
      "session appeared after capture without one → reject");
  });

  await test("guard rejects missing snapshots and empty jobId fallback", () => {
    const { filesSessionUnchanged } = setup().window.ACPWorkbench.contracts;
    assertEq(filesSessionUnchanged(null, { jobId: "job-A", token: 1 }), false);
    assertEq(filesSessionUnchanged({ jobId: "job-A", token: 1 }, null), false, "selection cleared");
    assertEq(filesSessionUnchanged({ jobId: "", token: null }, { jobId: "", token: null }), false, "empty ids prove nothing");
  });

  await test("capture/accept wrappers: live task session token flow (accept → reject on switch/reselect/clear)", () => {
    let current = { jobId: "job-A", token: 7 };
    const sandbox = setup({ selectedJobId: "job-A" });
    withTaskSession(sandbox, () => current);
    const cap = sandbox.filesSessionCapture("job-A");
    assertEq(cap, { jobId: "job-A", token: 7 });
    assertEq(sandbox.filesSessionAccepts(cap), true, "same session accepted");

    current = { jobId: "job-B", token: 8 };
    assertEq(sandbox.filesSessionAccepts(cap), false, "old-session response rejected after A→B");

    current = { jobId: "job-A", token: 9 };
    assertEq(sandbox.filesSessionAccepts(cap), false, "A→A reselect: stale token rejected");

    current = null;
    assertEq(sandbox.filesSessionAccepts(cap), false, "cleared selection rejects tokened capture");
  });

  await test("capture/accept wrappers: jobId fallback when no task session selection exists", () => {
    const sandbox = setup({ selectedJobId: "job-A" });
    // No taskSession controller registered at all
    const cap = sandbox.filesSessionCapture("job-A");
    assertEq(cap, { jobId: "job-A", token: null });
    assertEq(sandbox.filesSessionAccepts(cap), true, "fallback: same page selection");

    sandbox.selectedJobId = "job-B";
    assertEq(sandbox.filesSessionAccepts(cap), false, "fallback: page selection moved on");

    // Session appears later with a token — a tokenless capture cannot prove currency
    withTaskSession(sandbox, () => ({ jobId: "job-B", token: 3 }));
    assertEq(sandbox.filesSessionAccepts(cap), false, "tokenless capture vs tokened session → reject");
  });

  await test("wiring pins: every folder load, expand loop and log tail guards its response", () => {
    const fs = require("fs");
    const path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/features/files-logs.js"), "utf8");
    const codeOnly = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const fn of ["fetchAndExpand", "refreshLoadedFolders", "expandPath", "pollLogTail", "pollIrcOutputTail"]) {
      const start = codeOnly.indexOf("async function " + fn + "(");
      assert(start !== -1, "function present: " + fn);
      const body = codeOnly.slice(start, codeOnly.indexOf("\n}", start));
      assert(body.indexOf("filesSessionCapture(") !== -1, fn + " captures the session");
      assert(body.indexOf("filesSessionAccepts(") !== -1, fn + " guards its response with the session");
    }
    assert(codeOnly.indexOf("logPollToken++;") !== -1, "logPollToken is bumped on session/task switch, not only per poll");
    assert(codeOnly.indexOf("logSessionToken") !== -1, "log tails track the task-session token");
  });

  await test("source purity: page script keeps transport adapters only — no raw fetch/XHR/3Dmol", () => {
    const fs = require("fs");
    const path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/features/files-logs.js"), "utf8");
    const codeOnly = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["fetch(", "XMLHttpRequest", "$3Dmol", "localStorage"]) {
      assert(codeOnly.indexOf(bad) === -1, "forbidden reference in files-logs.js: " + bad);
    }
    assert(codeOnly.indexOf("contracts.filesSessionUnchanged") !== -1, "decision exported on the contracts namespace");
  });
};
