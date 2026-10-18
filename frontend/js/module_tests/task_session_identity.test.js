/* R5/T23 behavior tests: task-session identity for the energy candidate/result
 * write paths (energy.js). Covers the pure identity helper, the in-flight
 * invalidation used by the page selection reset, the source wiring of every
 * late-response guard, and the "candidate save never creates a task" contract.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadEnergy(currentFn) {
    const sandbox = makeSandbox({ requestAnimationFrame: () => 1 });
    sandbox.document = { getElementById: () => null };
    sandbox.energyGraphState = {
      jobId: "job-A",
      requestToken: 5,
      abortController: null,
      structureAbort: null,
    };
    sandbox.window.ACPWorkbench = { controllers: { taskSession: currentFn ? { current: currentFn } : null } };
    loadScript("js/workbench/features/energy.js", sandbox);
    return sandbox;
  }

  await test("result-session identity: token authoritative; jobId fallback; A→B and same-job reselect reject", () => {
    let current = { jobId: "job-A", token: 7 };
    const sandbox = loadEnergy(() => current);
    const session = sandbox.energyGraphResultSession(sandbox.energyGraphState.jobId);
    assertEq(session, { jobId: "job-A", token: 7 });
    assertEq(sandbox.energyGraphResultSessionCurrent(session), true, "same session accepted");

    current = { jobId: "job-B", token: 8 };
    assertEq(sandbox.energyGraphResultSessionCurrent(session), false, "A→B rejected");

    current = { jobId: "job-A", token: 9 };
    assertEq(sandbox.energyGraphResultSessionCurrent(session), false, "same-job reselect rejected");

    current = null;
    assertEq(sandbox.energyGraphResultSessionCurrent(session), false, "cleared selection rejects a tokened capture");

    sandbox.energyGraphState.jobId = "job-B";
    current = { jobId: "job-B", token: 10 };
    assertEq(sandbox.energyGraphResultSessionCurrent({ jobId: "job-A", token: 10 }), false,
      "displayed jobId mismatch rejected even with a matching token");
  });

  await test("result-session identity: no-controller fallback accepts only the same displayed jobId", () => {
    let current = null;
    const sandbox = loadEnergy(() => current);
    const session = sandbox.energyGraphResultSession("job-A");
    assertEq(session, { jobId: "job-A", token: null });
    assertEq(sandbox.energyGraphResultSessionCurrent(session), true, "fallback same job accepted");
    sandbox.energyGraphState.jobId = "job-B";
    assertEq(sandbox.energyGraphResultSessionCurrent(session), false, "fallback switch rejected");
  });

  await test("invalidate-in-flight bumps the requestToken and aborts both fetches", () => {
    const aborts = { workspace: 0, geometry: 0 };
    const sandbox = loadEnergy(() => ({ jobId: "job-A", token: 7 }));
    sandbox.energyGraphState.abortController = { abort: () => { aborts.workspace++; } };
    sandbox.energyGraphState.structureAbort = { abort: () => { aborts.geometry++; } };
    const captured = sandbox.energyGraphState.requestToken;

    sandbox.energyGraphInvalidateInFlight();

    assertEq(sandbox.energyGraphState.requestToken, captured + 1, "token advances");
    assert(captured !== sandbox.energyGraphState.requestToken, "a captured pre-reset token can never match again");
    assertEq(aborts, { workspace: 1, geometry: 1 }, "both in-flight requests are aborted");
    assertEq(sandbox.energyGraphState.abortController, null);
    assertEq(sandbox.energyGraphState.structureAbort, null);
  });

  await test("energy write paths guard their late responses and never POST a task-creating URL", () => {
    const fs = require("fs");
    const path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/features/energy.js"), "utf8");
    const codeOnly = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");

    const guards = codeOnly.match(/energyGraphResultSessionCurrent\(/g) || [];
    assert(guards.length >= 5, "every candidate/review response applies the session guard (found " + guards.length + ")");

    const postRe = /method:\s*"POST"/g;
    let match = null;
    let posts = 0;
    while ((match = postRe.exec(codeOnly))) {
      posts++;
      const context = codeOnly.slice(Math.max(0, match.index - 260), match.index);
      assert(/\/frame-candidate|\/pes\/review/.test(context),
        "POST #" + posts + " must target a candidate/review endpoint — no implicit task creation");
    }
    assert(posts >= 3, "all three candidate/review POSTs present (found " + posts + ")");
  });
};
