/* R4 behavior tests: candidate review controller — single owner of the PES/
 * Scan candidate review state machine (formerly inline in s2scanState).
 * Covers: toggle exclusive roles + lock rejection, dirty semantics
 * (setConfirmed/setCandidateName), snapshot load clears dirty, full reset,
 * working-set mutations (setFrameRole/upsert/remove/move), server snapshot
 * sync + review-result apply, subscribe/notify, snapshot isolation, purity.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(overrides) {
    const sandbox = makeSandbox(Object.assign({
      console: { log: () => {}, error: () => {}, warn: () => {} },
    }, overrides || {}));
    loadScript("js/workbench/controllers/candidate_review_controller.js", sandbox);
    return sandbox.window.ACPWorkbench.controllers.candidateReview;
  }

  function entry(state, frameIndex) {
    return state.candidates.find((c) => Number(c.frame_index) === Number(frameIndex)) || null;
  }

  await test("toggleCandidate: exclusive roles — moving between roles and toggling off", () => {
    const cr = setup();
    cr.reset();
    assertEq(cr.toggleCandidate("f1", "ts"), true);
    assertEq(cr.getState().selectedTs, ["f1"]);
    assertEq(cr.getState().selectedInt, []);
    // Same id in the other role moves it (exclusive)
    assertEq(cr.toggleCandidate("f1", "intermediate"), true);
    assertEq(cr.getState().selectedTs, []);
    assertEq(cr.getState().selectedInt, ["f1"]);
    // Toggle off
    assertEq(cr.toggleCandidate("f1", "intermediate"), true);
    assertEq(cr.getState().selectedInt, []);
    // Two ids can coexist in one role
    cr.toggleCandidate("f1", "ts");
    cr.toggleCandidate("f2", "ts");
    assertEq(cr.getState().selectedTs, ["f1", "f2"]);
    assert(cr.getState().dirty, "toggling marks dirty");
  });

  await test("toggleCandidate: rejected while locked (no state change)", () => {
    const cr = setup();
    cr.reset();
    cr.setLock(true);
    assertEq(cr.toggleCandidate("f1", "ts"), false);
    assertEq(cr.getState().selectedTs, []);
    assertEq(cr.getState().selectionLocked, true);
    assertEq(cr.isLocked(), true);
  });

  await test("setConfirmed and setCandidateName mark dirty; rename honors lock and existence", () => {
    const cr = setup();
    cr.reset();
    cr.setConfirmed(true);
    let st = cr.getState();
    assertEq(st.reviewConfirmed, true);
    assertEq(st.dirty, true, "setConfirmed marks dirty");

    cr.clearDirty();
    assertEq(cr.upsertCandidate(3, { role: "ts" }), true, "upsert creates working entry");
    st = cr.getState();
    assertEq(st.dirty, true, "upsert marks dirty");
    cr.clearDirty();

    assertEq(cr.setCandidateName(3, "frame_003__TS_x"), true, "rename existing entry");
    st = cr.getState();
    assertEq(st.candidates[0].name, "frame_003__TS_x");
    assertEq(st.candidates[0].saved, false, "rename unsaves the entry");
    assertEq(st.dirty, true, "rename marks dirty");

    cr.clearDirty();
    assertEq(cr.setCandidateName(99, "nope"), false, "rename without entry is a no-op");
    assertEq(cr.getState().dirty, false, "no-op rename leaves dirty alone");

    cr.setLock(true);
    assertEq(cr.setCandidateName(3, "locked"), false, "rename rejected while locked");
    assertEq(entry(cr.getState(), 3).name, "frame_003__TS_x", "locked rename changed nothing");
  });

  await test("loadFromSnapshot applies server fields and clears dirty", () => {
    const cr = setup();
    cr.reset();
    cr.setConfirmed(true); // dirty = true
    cr.loadFromSnapshot({
      selected_ts: ["a"],
      selected_int: ["b"],
      review_confirmed: true,
      selection_locked: true,
      review_status: "confirmed",
      saved_at: "2026-10-17T10:00:00",
      revision: 4,
    });
    const st = cr.getState();
    assertEq(st.selectedTs, ["a"]);
    assertEq(st.selectedInt, ["b"]);
    assertEq(st.reviewConfirmed, true);
    assertEq(st.selectionLocked, true);
    assertEq(st.reviewStatus, "confirmed");
    assertEq(st.savedAt, "2026-10-17T10:00:00");
    assertEq(st.revision, 4);
    assertEq(st.dirty, false, "server snapshot clears dirty");
  });

  await test("reset clears every review field including revision", () => {
    const cr = setup();
    cr.setConfirmed(true);
    cr.setRevision(9);
    cr.toggleCandidate("f1", "ts");
    cr.reset();
    assertEq(cr.getState(), {
      selectedTs: [], selectedInt: [],
      reviewConfirmed: false, selectionLocked: false, selectionEditMode: false,
      candidates: [], dirty: false, savedAt: null, reviewStatus: "", revision: 0,
    });
  });

  await test("setFrameRole: create / role change / none-removes / same-role no-op / lock reject", () => {
    const cr = setup();
    cr.reset();
    assertEq(cr.setFrameRole(2, "ts"), true, "create");
    assertEq(entry(cr.getState(), 2).role, "ts");
    assertEq(cr.getState().dirty, true);
    cr.clearDirty();

    assertEq(cr.setFrameRole(2, "ts"), false, "same role is a no-op");
    assertEq(cr.getState().dirty, false, "no-op does not dirty");

    assertEq(cr.setFrameRole(2, "intermediate"), true, "role change");
    assertEq(entry(cr.getState(), 2).role, "intermediate");
    assertEq(entry(cr.getState(), 2).saved, false);

    assertEq(cr.setFrameRole(2, "none"), true, "none removes");
    assertEq(cr.getState().candidates, []);
    assertEq(cr.setFrameRole(2, "none"), false, "none with no entry is a no-op");

    cr.setLock(true);
    assertEq(cr.setFrameRole(2, "ts"), false, "locked role set rejected");
    assertEq(cr.getState().candidates, []);
  });

  await test("upsert/remove/move: former page semantics, occupied detection is pure", () => {
    const cr = setup();
    cr.reset();
    cr.setCandidates([
      { candidate_id: "c1", frame_index: 1, role: "ts", name: "", saved: true },
    ]);
    cr.clearDirty();

    // upsert onto occupied frame updates in place
    assertEq(cr.upsertCandidate(1, { role: "intermediate", name: "renamed" }), true);
    let st = cr.getState();
    assertEq(st.candidates.length, 1);
    assertEq(st.candidates[0].role, "intermediate");
    assertEq(st.candidates[0].saved, false);
    assertEq(st.dirty, true);
    cr.clearDirty();

    // upsert role "none" removes; second call is a no-op
    assertEq(cr.upsertCandidate(1, { role: "none" }), true);
    assertEq(cr.getState().candidates, []);
    assertEq(cr.upsertCandidate(1, { role: "none" }), false);

    // move: no entry / same frame
    cr.setCandidates([{ candidate_id: null, frame_index: 1, role: "ts", name: "", saved: false }]);
    assertEq(cr.moveCandidate(7, 8), { moved: false, occupied: null });
    assertEq(cr.moveCandidate(1, 1), { moved: false, occupied: null });
    // move onto occupied frame reports the occupant, changes nothing
    cr.setCandidates([
      { candidate_id: null, frame_index: 1, role: "ts", name: "", saved: false },
      { candidate_id: null, frame_index: 2, role: "intermediate", name: "", saved: false },
    ]);
    cr.clearDirty();
    const res = cr.moveCandidate(1, 2);
    assertEq(res.moved, false);
    assertEq(res.occupied && res.occupied.frame_index, 2);
    assertEq(cr.getState().dirty, false, "occupied move leaves state untouched");
    // free move
    assertEq(cr.moveCandidate(1, 5), { moved: true, occupied: null });
    st = cr.getState();
    assertEq(entry(st, 5).frame_index, 5);
    assertEq(entry(st, 5).saved, false);
    assertEq(st.dirty, true);

    // remove
    assertEq(cr.removeCandidate(5), true);
    assertEq(cr.removeCandidate(5), false);
    cr.setLock(true);
    assertEq(cr.removeCandidate(2), false, "locked remove rejected");
  });

  await test("syncFromServerSnapshot: annotation mapping, confirmed-empty rule, dirty cleared", () => {
    const cr = setup();
    cr.reset();
    cr.setConfirmed(true); // dirty
    cr.syncFromServerSnapshot(
      { status: "confirmed", decided_at: "2026-10-17T09:30:00", revision: 7 },
      [
        { type: "ts", frame_index: 3, candidate_id: "c3", name: "TS3", saved: true },
        { type: "intermediate", frame_index: 4, active: false, saved: true }, // inactive skipped
        { type: "intermediate", frame_index: 5, saved: false }, // unconfirmed review would keep, confirmed drops
        { type: "frame", frame_index: 6, saved: true }, // non-candidate type skipped
        { type: "ts", frame_index: null, saved: true }, // no frame skipped
      ],
    );
    let st = cr.getState();
    assertEq(st.candidates.map((c) => c.frame_index), [3], "confirmed empty review not repopulated");
    assertEq(st.reviewStatus, "confirmed");
    assertEq(st.reviewConfirmed, true);
    assertEq(st.selectionLocked, true);
    assertEq(st.savedAt, "2026-10-17T09:30:00");
    assertEq(st.revision, 7);
    assertEq(st.dirty, false, "server snapshot clears dirty");

    // pending review keeps algorithmic recommendations
    cr.reset();
    cr.syncFromServerSnapshot(
      { status: "pending", revision: 0 },
      [{ type: "intermediate", frame_index: 5, saved: false }],
    );
    st = cr.getState();
    assertEq(st.candidates.length, 1);
    assertEq(st.candidates[0].selection_source, "algorithm");
    assertEq(st.selectionLocked, false);
    assertEq(st.reviewConfirmed, false);
  });

  await test("applyReviewResult: full server response locks+confirms, maps roles, clears dirty", () => {
    const cr = setup();
    cr.reset();
    cr.upsertCandidate(3, { role: "ts" });
    cr.applyReviewResult({
      candidates: [
        { candidate_id: "c9", frame_index: 3, role: "TS", name: "ts3" },
        { candidate_id: "c10", frame_index: 4, role: "INT" },
      ],
      status: "confirmed",
      confirmed_at: "2026-10-17T11:00:00",
      revision: 12,
      selected_count: 2,
    });
    const st = cr.getState();
    assertEq(st.candidates.length, 2);
    assertEq(st.candidates[0].role, "ts");
    assertEq(st.candidates[0].saved, true);
    assertEq(st.candidates[1].role, "intermediate");
    assertEq(st.dirty, false);
    assertEq(st.reviewConfirmed, true);
    assertEq(st.selectionLocked, true);
    assertEq(st.selectionEditMode, false);
    assertEq(st.reviewStatus, "confirmed");
    assertEq(st.savedAt, "2026-10-17T11:00:00");
    assertEq(st.revision, 12);

    // No candidates array: every working entry is marked saved, state still locks
    cr.setLock(false);
    cr.setCandidates([{ candidate_id: "c1", frame_index: 1, role: "ts", name: "", saved: false }]);
    cr.applyReviewResult({ status: "confirmed", revision: 13 });
    const st2 = cr.getState();
    assertEq(st2.candidates[0].saved, true, "markAllSaved branch");
    assertEq(st2.revision, 13);
    assertEq(st2.selectionLocked, true);
  });

  await test("getState returns snapshots — external mutation never reaches the owner", () => {
    const cr = setup();
    cr.reset();
    cr.setCandidates([{ candidate_id: "c1", frame_index: 1, role: "ts", name: "n", saved: true }]);
    const st = cr.getState();
    st.candidates.push({ candidate_id: "x", frame_index: 99, role: "ts" });
    st.candidates[0].name = "mutated";
    st.selectedTs.push("zz");
    assertEq(cr.getState().candidates.length, 1, "array push did not leak");
    assertEq(cr.getState().candidates[0].name, "n", "entry mutation did not leak");
    assertEq(cr.getState().selectedTs, []);
  });

  await test("subscribe/notify: listeners get state snapshots, unsubscribe stops, listener errors isolated", () => {
    const cr = setup();
    cr.reset();
    const seen = [];
    const unsub = cr.subscribe((st) => seen.push(st));
    cr.setRevision(5);
    assertEq(seen.length, 1);
    assertEq(seen[0].revision, 5);
    const failing = () => { throw new Error("listener boom"); };
    const unsub2 = cr.subscribe(failing);
    cr.setRevision(6); // failing listener must not break the healthy one
    assertEq(seen.length, 2);
    assertEq(seen[1].revision, 6);
    unsub();
    unsub2();
    cr.setRevision(7);
    assertEq(seen.length, 2, "unsubscribed listener no longer called");
  });

  await test("granular setters: setSelectionMode/setSavedAt/setReviewStatus/setCandidates/markAllCandidatesSaved", () => {
    const cr = setup();
    cr.reset();
    cr.setSelectionMode(true);
    assertEq(cr.getState().selectionEditMode, true);
    assertEq(cr.getState().dirty, false, "edit mode alone is not dirty");
    cr.setSavedAt("2026-10-17T12:00:00");
    cr.setReviewStatus("confirmed");
    assertEq(cr.getState().savedAt, "2026-10-17T12:00:00");
    assertEq(cr.getState().reviewStatus, "confirmed");
    cr.setCandidates([
      { candidate_id: "c1", frame_index: 1, role: "ts", name: "", saved: false },
      { candidate_id: "c2", frame_index: 2, role: "intermediate", name: "", saved: false },
    ]);
    cr.markAllCandidatesSaved();
    const st = cr.getState();
    assert(st.candidates.every((c) => c.saved), "markAllCandidatesSaved flips every entry");
    cr.clearDirty();
    assertEq(cr.isDirty(), false);
  });

  await test("A→B→A selection resets candidate review through the taskSession hook — no leak", async () => {
    const sandbox = makeSandbox({ console: { log: () => {}, error: () => {}, warn: () => {} } });
    loadScript("js/workbench/controllers/task_session_controller.js", sandbox);
    loadScript("js/workbench/controllers/candidate_review_controller.js", sandbox);
    const ts = sandbox.window.ACPWorkbench.controllers.taskSession;
    const cr = sandbox.window.ACPWorkbench.controllers.candidateReview;
    ts.init({
      hooks: {
        resetForSelection: function () { cr.reset(); },
        loadDetail: function () { return Promise.resolve(null); },
        applyDetail: function () { return null; },
      },
    });

    async function selectWithCandidates(jobId, frameIndex) {
      await ts.select(jobId);
      cr.upsertCandidate(frameIndex, { role: "ts" });
      cr.setRevision(frameIndex);
      cr.setLock(true);
      cr.setConfirmed(true);
    }
    function assertReset(msg) {
      const st = cr.getState();
      assertEq(st.candidates, [], msg + ": candidates cleared");
      assertEq(st.dirty, false, msg + ": dirty cleared");
      assertEq(st.selectionLocked, false, msg + ": lock released");
      assertEq(st.reviewConfirmed, false, msg + ": confirmation cleared");
      assertEq(st.revision, 0, msg + ": revision reset");
    }

    await selectWithCandidates("job-A", 1);
    assertEq(cr.getState().candidates.length, 1, "A holds its own working candidate");
    const tokenA1 = ts.current().token;

    await ts.select("job-B");
    assertReset("A→B");
    const tokenB = ts.current().token;
    cr.upsertCandidate(2, { role: "intermediate" });
    cr.setRevision(9);
    cr.setLock(true);

    await ts.select("job-A");
    const tokenA2 = ts.current().token;
    assert(tokenA2 > tokenB && tokenB > tokenA1, "each selection mints a fresh monotonic token");
    assertReset("A→B→A");
    assertEq(cr.getState().candidates, [], "returning to A never rehydrates the first A's candidates");
  });

  await test("reaction-builder resetForSelection owns the resets (candidate review + energy in-flight)", () => {
    const fs = require("fs");
    const path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/features/reaction-builder.js"), "utf8");
    const start = raw.indexOf("resetForSelection: function");
    const end = raw.indexOf("loadDetail: function", start);
    assert(start !== -1 && end > start, "resetForSelection hook present in selectJob");
    const region = raw.slice(start, end);
    assert(region.indexOf("candidateReview.reset()") !== -1, "candidate review reset wired");
    assert(region.indexOf("energyGraphInvalidateInFlight()") !== -1, "energy in-flight result invalidation wired");
  });

  await test("purity + ownership pin: no DOM/fetch/3Dmol/s2scanState in controller source", () => {
    const fs = require("fs");
    const path = require("path");
    const raw = fs.readFileSync(path.resolve(__dirname, "../workbench/controllers/candidate_review_controller.js"), "utf8");
    const codeOnly = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["document.", "fetch(", "$3Dmol", "s2scanState", "XMLHttpRequest", "window.alert"]) {
      assert(codeOnly.indexOf(bad) === -1, "forbidden reference in controller code: " + bad);
    }
    assert(raw.indexOf("Version: v20261017.1") !== -1, "version header pinned");
  });
};
