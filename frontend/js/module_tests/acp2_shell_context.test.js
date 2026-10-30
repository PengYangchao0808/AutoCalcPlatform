/* ACP 2.0 Next Shell — WorkspaceContext (UI-01).
 *
 * Pins ACPShell.context:
 *   1. setFocus merges atomically and bumps revision exactly once per
 *      effective change; getSnapshot() returns a frozen copy (taskRef cloned);
 *   2. subscribe delivers change notifications; unsubscribe stops them; a
 *      throwing subscriber never breaks the others;
 *   3. a setFocus whose values all equal the current snapshot does NOT bump
 *      revision and does NOT notify (write-back loop guard);
 *   4. a projectId change CLEARS taskRef/structureRef/researchRef/
 *      networkNodeId (cross-project refs are never carried over).
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup() {
    const sandbox = makeSandbox({
      fetch: function () { throw new Error("fetch is forbidden in shell context"); },
    });
    loadScript("js/shell/workspace-context.js", sandbox);
    return sandbox.window.ACPShell.context;
  }

  await test("acp2_shell_context: atomic merge bumps revision once; snapshot is frozen", async () => {
    const ctx = setup();
    assertEq(ctx.getSnapshot().revision, 0);
    assertEq(ctx.getSnapshot().source, "init");

    const snap = ctx.setFocus({ structureRef: { name: "caffeine" }, taskRef: { jobId: "job-1", attempt: 1 } }, "test:set");
    assertEq(snap.revision, 1, "one effective change → one bump");
    assertEq(snap.source, "test:set");
    assertEq(snap.structureRef.name, "caffeine");
    assertEq(snap.taskRef.jobId, "job-1");

    const next = ctx.setFocus({ networkNodeId: "node-7" }, "test:node");
    assertEq(next.revision, 2);
    assertEq(next.structureRef.name, "caffeine", "unrelated fields survive the merge");
    assertEq(next.taskRef.jobId, "job-1");
    assertEq(next.networkNodeId, "node-7");

    // Frozen: mutations either throw (strict) or silently fail; both mean the
    // store itself is untouched.
    const frozen = ctx.getSnapshot();
    assertEq(Object.isFrozen(frozen), true, "snapshot is frozen");
    try { frozen.revision = 999; } catch (e) { /* frozen in strict consumers */ }
    assertEq(ctx.getSnapshot().revision, 2, "store not mutated through the snapshot");

    // taskRef handed out is a CLONE — mutating it cannot touch the store.
    const withTask = ctx.setFocus({ taskRef: { jobId: "job-2", attempt: 3 } }, "test:2");
    withTask.taskRef.jobId = "mutated";
    assertEq(ctx.getSnapshot().taskRef.jobId, "job-2", "taskRef clone isolates the store");
  });

  await test("acp2_shell_context: subscribe/unsubscribe + throwing subscriber isolation", async () => {
    const ctx = setup();
    const seen = [];
    const unsub = ctx.subscribe((snap) => { seen.push(snap.revision); });
    const unsubThrowing = ctx.subscribe(() => { throw new Error("subscriber bug"); });
    const unsubB = ctx.subscribe((snap) => { seen.push("b:" + snap.revision); });

    ctx.setFocus({ structureRef: { name: "a" } }, "s1");
    assertEq(seen, [1, "b:1"], "both healthy subscribers notified despite the thrower");

    unsub();
    unsubThrowing();
    ctx.setFocus({ structureRef: { name: "b" } }, "s2");
    assertEq(seen, [1, "b:1", "b:2"], "unsubscribed listeners no longer fire");

    unsubB();
    ctx.setFocus({ structureRef: { name: "c" } }, "s3");
    assertEq(seen, [1, "b:1", "b:2"], "all unsubscribed → no notifications");
    assertEq(ctx.getSnapshot().revision, 3, "revision still advances without subscribers");
  });

  await test("acp2_shell_context: same-value setFocus does not bump or notify", async () => {
    const ctx = setup();
    const taskRef = { jobId: "job-1", attempt: 2 };
    ctx.setFocus({ structureRef: { name: "mol" }, taskRef: taskRef }, "seed");

    let notified = 0;
    ctx.subscribe(() => { notified += 1; });

    // Echoing the exact same values (as a follow-focus panel would) → no-op.
    const res = ctx.setFocus({ structureRef: { name: "mol" }, taskRef: { jobId: "job-1", attempt: 2 } }, "echo");
    assertEq(res.revision, 1, "revision unchanged");
    assertEq(notified, 0, "no notification for a no-op echo");
    assertEq(ctx.getSnapshot().source, "seed", "source unchanged on no-op");

    // Partial no-op: only the equal fields → still no bump.
    ctx.setFocus({ structureRef: { name: "mol" } }, "echo2");
    assertEq(ctx.getSnapshot().revision, 1);

    // One field differs → exactly one bump, other fields retained.
    const changed = ctx.setFocus({ structureRef: { name: "mol2" } }, "real");
    assertEq(changed.revision, 2);
    assertEq(notified, 1);
    assertEq(changed.taskRef.jobId, "job-1", "taskRef retained across a structure-only change");
  });

  await test("acp2_shell_context: projectId change clears all child refs", async () => {
    const ctx = setup();
    ctx.setFocus({
      projectId: "proj-a",
      researchRef: { recordId: "res-1" },
      taskRef: { jobId: "job-a", attempt: 1 },
      structureRef: { name: "conf1" },
      networkNodeId: "net-1",
    }, "seed");

    const switched = ctx.setFocus({ projectId: "proj-b" }, "project-switch");
    assertEq(switched.revision, 2);
    assertEq(switched.projectId, "proj-b");
    assertEq(switched.researchRef, null, "researchRef cleared");
    assertEq(switched.taskRef, null, "taskRef cleared");
    assertEq(switched.structureRef, null, "structureRef cleared");
    assertEq(switched.networkNodeId, null, "networkNodeId cleared");

    // Setting the SAME projectId again is a no-op (even with child refs in the
    // patch being null) — the clear-once semantics do not double-fire.
    const before = ctx.getSnapshot().revision;
    const same = ctx.setFocus({ projectId: "proj-b" }, "same-project");
    assertEq(same.revision, before, "same projectId → no bump");

    // A patch that changes projectId AND seeds a new taskRef in one atomic
    // write keeps the seeded taskRef (clear happens, then patch applies).
    const seeded = ctx.setFocus({ projectId: "proj-c", taskRef: { jobId: "job-c", attempt: 0 } }, "switch+seed");
    assertEq(seeded.projectId, "proj-c");
    assertEq(seeded.taskRef.jobId, "job-c", "explicit taskRef in the same patch survives");
    assertEq(seeded.structureRef, null);
  });
};
