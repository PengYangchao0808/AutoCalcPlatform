/* Behavior tests for R3 jobCommands and R4 candidateReview modules. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setupEnvironment(overrides) {
    const sandbox = makeSandbox(overrides);
    loadScript("js/client/transport.js", sandbox);
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/client/job-commands.js", sandbox);
    loadScript("js/workbench/controllers/candidate_review_controller.js", sandbox);
    return sandbox;
  }

  await test("jobCommands: pause, unpause, continue, cancel, purge invoke correct endpoints and trigger invalidation", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const calls = [];
    const invalidated = [];

    W.ACPClient.jobCommands.init({
      v1: (path, opts) => {
        calls.push({ path, opts });
        return Promise.resolve({ ok: true });
      },
      v2: (path, opts) => {
        calls.push({ path, opts });
        return Promise.resolve({ ok: true });
      },
      onInvalidate: (id) => invalidated.push(id),
    });

    await W.ACPClient.jobCommands.pause("job-1");
    assertEq(calls[0].path, "/jobs/job-1/pause");
    assertEq(calls[0].opts.method, "POST");
    assertEq(invalidated, ["job-1"]);

    await W.ACPClient.jobCommands.unpause("job-1");
    assertEq(calls[1].path, "/jobs/job-1/unpause");

    await W.ACPClient.jobCommands.continueJob("job-1", "node-gpu");
    assertEq(calls[2].path, "/jobs/job-1/continue");
    assertEq(JSON.parse(calls[2].opts.body).target_node, "node-gpu");

    await W.ACPClient.jobCommands.cancel("job-1");
    assertEq(calls[3].path, "/jobs/job-1/cancel");

    await W.ACPClient.jobCommands.purge(["job-1", "job-2"]);
    assertEq(calls[4].path, "/jobs/purge");
    assert(invalidated.includes("job-2"));

    await W.ACPClient.jobCommands.rename("job-1", "new-name", 3);
    assertEq(calls[5].path, "/tasks/job-1");
    assertEq(calls[5].opts.method, "PATCH");
    const patchBody = JSON.parse(calls[5].opts.body);
    assertEq(patchBody.custom_name, "new-name");
    assertEq(patchBody.expected_name_revision, 3);
  });

  await test("jobCommands: rerun deduplicates concurrent calls in flight", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    let resolveRerun = null;
    let rerunCalls = 0;

    W.ACPClient.jobCommands.init({
      v1: (path) => {
        if (path.includes("/rerun")) {
          rerunCalls++;
          return new Promise((resolve) => { resolveRerun = resolve; });
        }
        return Promise.resolve({});
      },
    });

    const p1 = W.ACPClient.jobCommands.rerun("job-rerun");
    assert(W.ACPClient.jobCommands.isRerunInFlight("job-rerun"), "flagged in flight");

    let rejected = false;
    try {
      await W.ACPClient.jobCommands.rerun("job-rerun");
    } catch (e) {
      rejected = true;
    }
    assert(rejected, "second concurrent rerun was rejected");
    assertEq(rerunCalls, 1);

    resolveRerun({ status: "queued" });
    await p1;
    assert(!W.ACPClient.jobCommands.isRerunInFlight("job-rerun"), "cleared after completion");
  });

  await test("jobCommands: purge body carries job_ids + delete_data; batch-ops body exact with payload default", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const calls = [];
    const invalidated = [];

    W.ACPClient.jobCommands.init({
      v1: (path, opts) => { calls.push({ path, opts }); return Promise.resolve({ purged: 1 }); },
      v2: (path, opts) => { calls.push({ path, opts }); return Promise.resolve({ updated: 1 }); },
      onInvalidate: (id) => invalidated.push(id),
    });

    await W.ACPClient.jobCommands.purge({ jobIds: ["job-a", "job-b"], deleteData: true });
    assertEq(calls[0].path, "/jobs/purge");
    assertEq(calls[0].opts.method, "POST");
    assertEq(JSON.parse(calls[0].opts.body), { job_ids: ["job-a", "job-b"], delete_data: true });
    assertEq(invalidated, ["job-a", "job-b"]);

    await W.ACPClient.jobCommands.purge({ jobIds: ["job-c"] });
    assertEq(JSON.parse(calls[1].opts.body), { job_ids: ["job-c"], delete_data: false });

    invalidated.length = 0;
    await W.ACPClient.jobCommands.batchOp({ taskIds: ["t-1", "t-2"], op: "add_tags", payload: { tags: ["x"] } });
    assertEq(calls[2].path, "/tasks/batch-ops");
    assertEq(calls[2].opts.method, "POST");
    assertEq(JSON.parse(calls[2].opts.body), { task_ids: ["t-1", "t-2"], op: "add_tags", payload: { tags: ["x"] } });
    assertEq(invalidated, ["t-1", "t-2"]);

    invalidated.length = 0;
    await W.ACPClient.jobCommands.batchOp({ taskIds: ["t-3"], op: "archive" });
    assertEq(JSON.parse(calls[3].opts.body), { task_ids: ["t-3"], op: "archive", payload: {} });
    assertEq(invalidated, ["t-3"]);
  });

  await test("jobCommands: rename sends custom_name (null preserved) and expected_name_revision verbatim via v2", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const calls = [];
    const invalidated = [];

    W.ACPClient.jobCommands.init({
      v2: (path, opts) => { calls.push({ path, opts }); return Promise.resolve({ ok: true }); },
      onInvalidate: (id) => invalidated.push(id),
    });

    // revision 0 must be sent verbatim (falsy trap — the live modal seeds
    // _renameState.revision with `job.name_revision || 0`)
    await W.ACPClient.jobCommands.rename("job-r", "new-name", 0);
    assertEq(calls[0].path, "/tasks/job-r");
    assertEq(calls[0].opts.method, "PATCH");
    assertEq(JSON.parse(calls[0].opts.body), { custom_name: "new-name", expected_name_revision: 0 });
    assertEq(invalidated, ["job-r"]);

    // clearing a custom name still sends the field as null
    invalidated.length = 0;
    await W.ACPClient.jobCommands.rename("job-r", null, 7);
    assertEq(JSON.parse(calls[1].opts.body), { custom_name: null, expected_name_revision: 7 });
    assertEq(invalidated, ["job-r"]);
  });

  await test("jobCommands: move/clone POST project_id to /jobs/{id}/move|clone via v1", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const calls = [];
    const invalidated = [];

    W.ACPClient.jobCommands.init({
      v1: (path, opts) => { calls.push({ path, opts }); return Promise.resolve({ ok: true }); },
      onInvalidate: (id) => invalidated.push(id),
    });

    await W.ACPClient.jobCommands.move("job-m", "proj-1");
    assertEq(calls[0].path, "/jobs/job-m/move");
    assertEq(calls[0].opts.method, "POST");
    assertEq(JSON.parse(calls[0].opts.body), { project_id: "proj-1" });
    assertEq(invalidated, ["job-m"]);

    await W.ACPClient.jobCommands.clone("job-c", "proj-2");
    assertEq(calls[1].path, "/jobs/job-c/clone");
    assertEq(calls[1].opts.method, "POST");
    assertEq(JSON.parse(calls[1].opts.body), { project_id: "proj-2" });
    assertEq(invalidated, ["job-m", "job-c"]);
  });

  await test("jobCommands: onInvalidate only after success; 409/422 propagate with status+body; timeout never retried", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const invalidated = [];

    // 409 on rename PATCH — status/body intact, no invalidation
    let calls = 0;
    W.ACPClient.jobCommands.init({
      v2: () => {
        calls++;
        const err = new Error("name_revision_conflict");
        err.status = 409;
        err.body = { detail: { current: { name_revision: 4 } } };
        return Promise.reject(err);
      },
      onInvalidate: (id) => invalidated.push(id),
    });

    let caught = null;
    try {
      await W.ACPClient.jobCommands.rename("job-1", "x", 3);
    } catch (e) { caught = e; }
    assert(caught && caught.status === 409, "409 status propagated");
    assertEq(caught.body.detail.current.name_revision, 4, "409 body preserved");
    assertEq(calls, 1);
    assertEq(invalidated, []);

    // 422 on batch-ops — status/body intact, no invalidation
    calls = 0;
    W.ACPClient.jobCommands.init({
      v2: () => {
        calls++;
        const err = new Error("validation failed");
        err.status = 422;
        err.body = { detail: "bad op payload" };
        return Promise.reject(err);
      },
      onInvalidate: (id) => invalidated.push(id),
    });
    caught = null;
    try {
      await W.ACPClient.jobCommands.batchOp({ taskIds: ["t-1"], op: "add_tags", payload: {} });
    } catch (e) { caught = e; }
    assert(caught && caught.status === 422, "422 status propagated");
    assertEq(caught.body.detail, "bad op payload");
    assertEq(calls, 1);
    assertEq(invalidated, []);

    // timeout/abort — exactly one adapter call, no automatic retry (§42.4)
    calls = 0;
    W.ACPClient.jobCommands.init({
      v1: () => {
        calls++;
        const err = new Error("The operation was aborted due to timeout");
        err.status = 0;
        return Promise.reject(err);
      },
      onInvalidate: (id) => invalidated.push(id),
    });
    caught = null;
    try {
      await W.ACPClient.jobCommands.pause("job-t");
    } catch (e) { caught = e; }
    assert(caught, "timeout error surfaced");
    assertEq(calls, 1, "no retry after timeout");
    assertEq(invalidated, []);

    // success path invalidates exactly once, after the receipt
    calls = 0;
    W.ACPClient.jobCommands.init({
      v1: () => { calls++; return Promise.resolve({ ok: true }); },
      onInvalidate: (id) => invalidated.push(id),
    });
    await W.ACPClient.jobCommands.pause("job-ok");
    assertEq(calls, 1);
    assertEq(invalidated, ["job-ok"]);
  });

  await test("jobCommands: rerun failure clears in-flight, does not invalidate, and a later rerun goes through", async () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const invalidated = [];
    let calls = 0;
    let fail = true;

    W.ACPClient.jobCommands.init({
      v1: (path) => {
        if (!path.includes("/rerun")) return Promise.resolve({});
        calls++;
        if (fail) {
          const err = new Error("rerun rejected");
          err.status = 409;
          return Promise.reject(err);
        }
        return Promise.resolve({ ok: true });
      },
      onInvalidate: (id) => invalidated.push(id),
    });

    let caught = null;
    try {
      await W.ACPClient.jobCommands.rerun("job-x");
    } catch (e) { caught = e; }
    assert(caught && caught.status === 409, "rerun failure propagated");
    assert(!W.ACPClient.jobCommands.isRerunInFlight("job-x"), "flag cleared after failure");
    assertEq(invalidated, []);

    fail = false;
    await W.ACPClient.jobCommands.rerun("job-x");
    assertEq(calls, 2);
    assertEq(invalidated, ["job-x"]);
  });

  await test("candidateReview: state machine, toggle, locking and snapshot load", () => {
    const sandbox = setupEnvironment();
    const W = sandbox.window;
    const ctrl = W.ACPWorkbench.controllers.candidateReview;

    ctrl.reset();
    let state = ctrl.getState();
    assertEq(state.selectedTs.length, 0);
    assertEq(state.dirty, false);

    ctrl.toggleCandidate("c-1", "ts");
    state = ctrl.getState();
    assertEq(state.selectedTs, ["c-1"]);
    assertEq(state.dirty, true);

    // Switching role to intermediate removes from TS
    ctrl.toggleCandidate("c-1", "int");
    state = ctrl.getState();
    assertEq(state.selectedTs.length, 0);
    assertEq(state.selectedInt, ["c-1"]);

    // Lock prevents modification
    ctrl.setLock(true);
    const toggled = ctrl.toggleCandidate("c-2", "ts");
    assertEq(toggled, false);
    state = ctrl.getState();
    assertEq(state.selectedTs.length, 0);

    ctrl.setLock(false);
    ctrl.loadFromSnapshot({
      selected_ts: ["ts-1"],
      selected_int: ["int-1"],
      review_confirmed: true,
      selection_locked: true,
      review_status: "approved",
    });
    state = ctrl.getState();
    assertEq(state.selectedTs, ["ts-1"]);
    assertEq(state.selectedInt, ["int-1"]);
    assertEq(state.reviewConfirmed, true);
    assertEq(state.selectionLocked, true);
    assertEq(state.dirty, false);
  });
};
