/* ACP 2.0 client event consumption: invalidation + authoritative refetch.
 *
 * T-16 / C10b. Runs in the zero-dependency node vm harness with stub
 * adapters. No network, no fetch: the sandbox `fetch` throws if any module
 * ever reaches for it. The final ACP2_EVENT_SUMMARY line is parsed by the
 * `client-fixtures` acceptance sub-row.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const SCOPE_A = { projectId: "proj-a", permission: "read" };
  const REF = { jobId: "job-1", attempt: 1 };
  const summary = { prepare_calls: 0, stale_revision_guard: false, replay_entry_points: 0 };

  function setup() {
    const fetchCalls = [];
    const sandbox = makeSandbox({
      fetch: function () {
        fetchCalls.push(Array.prototype.slice.call(arguments));
        throw new Error("fetch is forbidden in client event modules");
      },
    });
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/client/event-invalidation.js", sandbox);
    return {
      sandbox,
      E: sandbox.window.ACPClient.eventInvalidation,
      store: sandbox.window.ACPClient.store,
      fetchCalls,
    };
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  function viewBody(revision, overrides) {
    return Object.assign({
      snapshot_revision: revision,
      project_id: "proj-a",
      task_ref: { job_id: "job-1", attempt: 1 },
      manifest_generation: revision,
      artifacts: [],
      claims: [],
      clarifications: [],
      read_only: true,
    }, overrides || {});
  }

  await test("invalidation: an event triggers one authoritative refetch through the store", async () => {
    const { E, store, fetchCalls } = setup();
    const calls = [];
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: (context) => {
        calls.push(context);
        return viewBody(1);
      },
    });
    const decision = await watcher.deliver({
      type: "task.status",
      status: "completed",
      data: { status: "completed" },
      snapshot_revision: 1,
      seq: 1,
    });
    assertEq(calls.length, 1, "exactly one authoritative refetch");
    assertEq(calls[0].reason, "invalidate");
    assertEq(decision.action, "refreshed");
    assertEq(decision.refetch_reason, "invalidate");
    assertEq(decision.authority, false, "events are never the task authority");
    assertEq(store.snapshot(watcher.key).data.snapshot_revision, 1);
    assertEq(store.snapshot(watcher.key).status, "fresh");
    assertEq(fetchCalls.length, 0, "no global fetch");
  });

  await test("stale revision: an old event after a newer snapshot never overwrites the store", async () => {
    const { E, store } = setup();
    let calls = 0;
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: () => { calls += 1; return viewBody(2, { marker: "new" }); },
    });
    const seeded = await watcher.refresh("initial");
    assertEq(seeded.action, "refreshed");
    assertEq(store.snapshot(watcher.key).data.marker, "new");
    const decision = await watcher.deliver({ snapshot_revision: 1, seq: 2 });
    assertEq(decision.reason, "stale_revision");
    assertEq(decision.ignored, true);
    assertEq(calls, 1, "a stale event must not refetch");
    assertEq(store.snapshot(watcher.key).data.snapshot_revision, 2);
    assertEq(store.snapshot(watcher.key).data.marker, "new");
    summary.stale_revision_guard = decision.reason === "stale_revision";
  });

  await test("gap/resume: after_seq is a hint, a gap forces a full refresh (no replay)", async () => {
    const { E } = setup();
    const hint = E.resumeHint(3);
    assertEq(hint.replay, false, "no fake replay");
    assertEq(hint.durable, false);
    assertEq(hint.requiresAuthoritativeRefresh, true);

    const reasons = [];
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      afterSeq: 3,
      refetch: (context) => { reasons.push(context.reason); return viewBody(4); },
    });
    const decision = await watcher.deliver({ snapshot_revision: 4, seq: 4 });
    assertEq(decision.gap, true, "a resumed cursor always needs a full refresh");
    assertEq(decision.refetch_reason, "gap");
    assertEq(reasons, ["gap"]);
    assertEq(watcher.getCursor(), 4);
    assertEq(typeof E.replay, "undefined");

    const reasons2 = [];
    const reconnected = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      afterSeq: 9,
      refetch: (context) => { reasons2.push(context.reason); return viewBody(10); },
    });
    reconnected.noteReconnect();
    const second = await reconnected.deliver({ snapshot_revision: 10, seq: 10 });
    assertEq(second.gap, true);
    assertEq(reasons2, ["gap"]);
  });

  await test("cursor gap: a skipped seq number is detected as a gap", async () => {
    const { E } = setup();
    const reasons = [];
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: (context) => { reasons.push(context.reason); return viewBody(1); },
    });
    await watcher.deliver({ snapshot_revision: 1, seq: 1 });
    const decision = await watcher.deliver({ snapshot_revision: 1, seq: 5 });
    assertEq(decision.gap, true);
    assertEq(decision.refetch_reason, "gap");
    assertEq(reasons, ["invalidate", "gap"]);
  });

  await test("availability: four states map to a stable display and read only when readable", () => {
    const { E } = setup();
    const states = ["unprepared", "preparing", "readable", "failed"];
    assertEq(E.AVAILABILITY_STATES, states);
    assertEq(E.displayFor("unprepared").label, "Not prepared");
    assertEq(E.displayFor("preparing").label, "Preparing");
    assertEq(E.displayFor("readable").label, "Readable");
    assertEq(E.displayFor("failed").label, "Failed");
    assert(E.displayFor("readable") === E.AVAILABILITY_DISPLAY.readable,
      "display mapping must be stable (same frozen object)");
    assert(Object.isFrozen(E.AVAILABILITY_DISPLAY.readable), "mapping frozen");
    assertEq(E.actionsFor("readable"), ["read"]);
    assertEq(E.actionsFor("unprepared"), []);
    assertEq(E.actionsFor("preparing"), []);
    assertEq(E.actionsFor("failed"), []);
    assertEq(E.normalizeAvailability("bogus"), "failed");
    assertEq(E.displayFor("bogus").state, "failed");
  });

  await test("read action: non-readable artifacts reject and never touch an adapter", async () => {
    const { E } = setup();
    const readCalls = [];
    const prepareCalls = [];
    E.init({
      v2: (path, opts) => { readCalls.push({ path, opts }); return Promise.resolve({ ok: true }); },
      prepare: () => { prepareCalls.push(1); },
    });
    for (const availability of ["unprepared", "preparing", "failed"]) {
      let caught = null;
      try {
        await E.readArtifact({
          scope: SCOPE_A,
          taskRef: REF,
          artifact: { artifact_id: "art-1", availability, generation: 1 },
        });
      } catch (error) { caught = error; }
      assert(caught && caught.name === "ArtifactNotReadable", availability + " must reject");
    }
    assertEq(readCalls.length, 0, "no adapter call for a non-readable artifact");
    const result = await E.readArtifact({
      scope: SCOPE_A,
      taskRef: REF,
      artifact: {
        artifact_id: "art-1",
        availability: "readable",
        generation: 2,
        sha256: "ab".repeat(32),
      },
    });
    assertEq(result.ok, true);
    assertEq(readCalls.length, 1);
    assert(readCalls[0].path.indexOf("/artifacts/art-1/read") !== -1);
    assertEq(readCalls[0].opts.method, "GET");
    assertEq(prepareCalls.length, 0, "read never prepares");
  });

  await test("GET loader: view refresh is GET-only and never calls prepare or transfer", async () => {
    const { E, store } = setup();
    const v2Calls = [];
    const prepareCalls = [];
    E.init({
      v2: (path, opts) => {
        v2Calls.push({ path, opts });
        return Promise.resolve(viewBody(4));
      },
      prepare: () => { prepareCalls.push(1); },
    });
    const watcher = E.createWatcher({ scope: SCOPE_A, taskRef: REF });
    await watcher.refresh("initial");
    assertEq(v2Calls.length, 1);
    assertEq(v2Calls[0].opts.method, "GET");
    assert(v2Calls[0].path.indexOf("/view?project_id=proj-a") !== -1);
    assert(!/(prepare|transfer|submit|compute)/i.test(v2Calls[0].path),
      "loader path must stay read-only");
    assertEq(prepareCalls.length, 0, "prepare adapter never called");
    assertEq(store.snapshot(watcher.key).data.snapshot_revision, 4);
    summary.prepare_calls = prepareCalls.length;

    let caught = null;
    try { E.assertReadOnlyPath("/science/tasks/job-1/attempts/1/prepare?x=1"); }
    catch (error) { caught = error; }
    assert(caught && /never prepare/.test(caught.message), "prepare path must be rejected");
  });

  await test("error propagation: a rejected authoritative read surfaces and caches error", async () => {
    const { E, store } = setup();
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: () => Promise.reject(new Error("backend exploded")),
    });
    let caught = null;
    try { await watcher.deliver({ snapshot_revision: 1, seq: 1 }); }
    catch (error) { caught = error; }
    assert(caught && caught.message === "backend exploded", "rejection must propagate");
    assertEq(store.snapshot(watcher.key).status, "error");
    assertEq(store.snapshot(watcher.key).data, undefined);
  });

  await test("cross-project: an event for another project never touches this watcher", async () => {
    const { E, store } = setup();
    const aCalls = [];
    const bCalls = [];
    const a = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: () => { aCalls.push(1); return viewBody(1); },
    });
    const b = E.createWatcher({
      scope: { projectId: "proj-b", permission: "read" },
      taskRef: REF,
      refetch: () => { bCalls.push(1); return viewBody(1, { project_id: "proj-b" }); },
    });
    assert(a.key !== b.key, "cross-project store keys must differ");
    const ignored = await a.deliver({ project_id: "proj-b", snapshot_revision: 1, seq: 1 });
    assertEq(ignored.reason, "cross_project");
    assertEq(aCalls.length, 0);
    await b.deliver({ project_id: "proj-b", snapshot_revision: 1, seq: 1 });
    assertEq(bCalls.length, 1);
    assertEq(aCalls.length, 0);
    assertEq(store.snapshot(a.key).data, undefined);
    assertEq(store.snapshot(b.key).data.project_id, "proj-b");
  });

  await test("authority: an event payload cannot pose as task state or replay", async () => {
    const { E, store } = setup();
    const watcher = E.createWatcher({
      scope: SCOPE_A,
      taskRef: REF,
      refetch: () => viewBody(2, { status: "running" }),
    });
    const decision = await watcher.deliver({
      type: "job.completed",
      status: "completed",
      data: { status: "completed" },
      snapshot_revision: 2,
      seq: 1,
    });
    assertEq(decision.authority, false);
    assertEq(store.snapshot(watcher.key).data.status, "running",
      "the event's completed status must not be adopted");
    assertEq(typeof E.replay, "undefined", "no replay entry point exists");
    assertEq(Object.keys(E).filter((name) => /replay/i.test(name)).length, 0);
    summary.replay_entry_points = 0;
  });

  // Parsed by the `client-fixtures` acceptance sub-row.
  console.log("ACP2_EVENT_SUMMARY " + JSON.stringify(summary));
};
