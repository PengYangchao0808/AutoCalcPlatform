/* R1 behavior tests: task selection session controller (monotonic session tokens). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function setup(overrides) {
    const sandbox = makeSandbox(overrides);
    loadScript("js/workbench/controllers/task_session_controller.js", sandbox);
    return sandbox;
  }

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));

  /** Hook recorder: every hook appends an event; loadDetail/loadFiles are
   * replaced per-test with deferred-capable fakes. */
  function makeHooks() {
    const events = [];
    return {
      events,
      resetForSelection: (id) => events.push("reset:" + id),
      loadDetail: (id) => Promise.resolve({ job: { id, status: "running" } }),
      applyDetail: (detail) => { events.push("applyDetail:" + detail.job.id); return detail.job; },
      loadFiles: () => Promise.resolve({ files: [{ path: "RESULT", is_dir: true }], pinned: [] }),
      applyFiles: () => events.push("applyFiles"),
      afterPanels: () => events.push("afterPanels"),
      reloadViewerTab: () => events.push("viewerTab"),
      highlight: (id) => events.push("highlight:" + id),
      openEvents: (id) => events.push("openEvents:" + id),
      primeLogs: (id) => events.push("primeLogs:" + id),
      refreshList: () => events.push("refreshList"),
      onStructureCatalog: (id) => events.push("catalog:" + id),
      afterSelection: () => events.push("afterSelection"),
    };
  }

  const FULL_TAIL = [
    "applyFiles", "afterPanels", "viewerTab", "highlight:", "openEvents:",
    "primeLogs:", "refreshList", "catalog:", "afterSelection",
  ];

  await test("happy path runs the hooks in the former inline sequence order and reports the session", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    ctrl.init({ hooks });
    const result = await ctrl.select("job-A");
    assertEq(hooks.events, [
      "reset:job-A",
      "applyDetail:job-A",
      "applyFiles", "afterPanels", "viewerTab",
      "highlight:job-A", "openEvents:job-A", "primeLogs:job-A",
      "refreshList", "catalog:job-A", "afterSelection",
    ]);
    assertEq(result, { jobId: "job-A", token: result.token });
    assertEq(ctrl.current(), { jobId: "job-A", token: result.token });
    assert(ctrl.isCurrent(result));
    assertEq(ctrl.current(), ctrl.current(), "current() returns equal snapshots");
  });

  await test("A -> B -> A: superseded sessions' late detail/files/failure never touch newer panels", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    ctrl.init({ hooks });
    const detailA1 = deferred(), detailB = deferred(), detailA2 = deferred(), filesA2 = deferred();
    let aCalls = 0;
    hooks.loadDetail = (id) => {
      if (id === "job-A") { aCalls++; return aCalls === 1 ? detailA1.promise : detailA2.promise; }
      return detailB.promise;
    };
    hooks.loadFiles = () => filesA2.promise;
    hooks.applyDetail = (detail) => { hooks.events.push("applyDetail:" + detail.job.id + ":attempt" + detail.job.attempt); return detail.job; };

    const pA1 = ctrl.select("job-A");
    const pB = ctrl.select("job-B");
    const pA2 = ctrl.select("job-A");
    assertEq(hooks.events, ["reset:job-A", "reset:job-B", "reset:job-A"], "each select resets once");

    // Old A's detail arrives when A2 owns the session — must be dropped.
    detailA1.resolve({ job: { id: "job-A", attempt: 1 } });
    await tick();
    assert(!hooks.events.some((e) => e.startsWith("applyDetail:job-A:attempt1")), "old A detail rejected");

    // B resolves while superseded — dropped too.
    detailB.resolve({ job: { id: "job-B", attempt: 2 } });
    await tick();
    assert(!hooks.events.some((e) => e.startsWith("applyDetail:job-B")), "superseded B detail rejected");

    // The live A2 session completes fully.
    detailA2.resolve({ job: { id: "job-A", attempt: 3 } });
    await tick();
    filesA2.resolve({ files: [], pinned: [] });
    const rA1 = await pA1, rB = await pB, rA2 = await pA2;
    assertEq(rA1, null, "superseded session resolves null");
    assertEq(rB, null, "superseded session resolves null");
    assert(rA2 && rA2.jobId === "job-A");
    assertEq(hooks.events.filter((e) => e.startsWith("applyDetail")), ["applyDetail:job-A:attempt3"],
      "exactly the newest session's detail is applied");
    assert(hooks.events.indexOf("afterSelection") === hooks.events.length - 1, "A2 finished its full sequence");
  });

  await test("same-job reselect mints a new token even when the page re-inits between selects (rerun race)", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks1 = makeHooks();
    const hooks2 = makeHooks();
    ctrl.init({ hooks: hooks1 });
    const detail1 = deferred(), detail2 = deferred();
    hooks1.loadDetail = () => detail1.promise;
    hooks2.loadDetail = () => detail2.promise;

    const p1 = ctrl.select("job-A");
    const t1 = ctrl.current().token;
    // Page re-registers hooks for the second selection (init must NOT reset tokens).
    ctrl.init({ hooks: hooks2 });
    const p2 = ctrl.select("job-A");
    const t2 = ctrl.current().token;
    assert(t2 > t1, "token strictly increases across same-job reselect + re-init");
    assertEq(t2, t1 + 1);

    // Old in-flight detail resolves — old session (hooks1) must not apply it,
    // and it must keep using the captured hooks1, never the new hooks2.
    detail1.resolve({ job: { id: "job-A", attempt: 1 } });
    await tick();
    assertEq(hooks1.events, ["reset:job-A"], "old session stops right after its late detail");
    assertEq(hooks2.events, ["reset:job-A"], "new session untouched by the old response");

    detail2.resolve({ job: { id: "job-A", attempt: 2 } });
    const r1 = await p1, r2 = await p2;
    assertEq(r1, null);
    assert(r2 && r2.token === t2);
    assert(hooks2.events.indexOf("applyDetail:job-A") !== -1, "new session applies its own detail");
    assertEq(hooks1.events, ["reset:job-A"], "old session never applied anything");
  });

  await test("late old-session FAILURE is swallowed and leaves the newer session untouched", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    ctrl.init({ hooks });
    const detailA = deferred(), detailB = deferred();
    const filesByJob = {};
    let filesCalls = 0;
    hooks.loadDetail = (id) => (id === "job-A" ? detailA.promise : detailB.promise);
    // Each session's file listing is its own request (like the real page hook).
    hooks.loadFiles = (id) => {
      filesCalls++;
      if (!filesByJob[id]) filesByJob[id] = deferred();
      return filesByJob[id].promise;
    };

    const pA = ctrl.select("job-A");
    detailA.resolve({ job: { id: "job-A" } });
    await tick(); // A now awaits its files
    assertEq(filesCalls, 1);

    const pB = ctrl.select("job-B"); // supersedes A mid-files
    filesByJob["job-A"].reject(new Error("network gone")); // old session's files fail late
    const rA = await pA;
    assertEq(rA, null, "superseded failure resolves null, does not throw");
    assertEq(hooks.events.filter((e) => e === "applyFiles" || e === "afterPanels"), [],
      "no panels applied by the failed old session");

    detailB.resolve({ job: { id: "job-B" } });
    await tick();
    filesByJob["job-B"].resolve({ files: [], pinned: [] });
    const rB = await pB;
    assert(rB && rB.jobId === "job-B", "newer session completes normally after the old failure");
    assertEq(filesCalls, 2, "the newer session issued its own files request");
    assertEq(hooks.events.filter((e) => e === "applyFiles").length, 1, "exactly one (new) applyFiles");
  });

  await test("clear() kills the in-flight session; the next select continues the monotonic token", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    ctrl.init({ hooks });
    const detail = deferred();
    hooks.loadDetail = () => detail.promise;
    const p1 = ctrl.select("job-A");
    const t1 = ctrl.current().token;
    ctrl.clear();
    assertEq(ctrl.current(), null, "selection cleared");
    detail.resolve({ job: { id: "job-A" } });
    assertEq(await p1, null, "cleared session stops");
    assertEq(hooks.events, ["reset:job-A"], "cleared session applied nothing");

    const r2 = await ctrl.select("job-A");
    assertEq(r2.token, t1 + 2, "tokens stay monotonic across clear (t1+1 burned by clear)");
    assertEq(hooks.events[hooks.events.length - 1], "afterSelection");
  });

  await test("dispose() clears and detaches hooks; re-init revives with a still-monotonic token", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    ctrl.init({ hooks });
    const first = await ctrl.select("job-A");
    ctrl.dispose();
    assertEq(ctrl.current(), null);
    let caught = null;
    try { await ctrl.select("job-B"); } catch (e) { caught = e; }
    assert(caught && /init\(\{hooks\}\) required/.test(caught.message), "select without hooks throws");
    ctrl.init({ hooks });
    const second = await ctrl.select("job-B");
    assert(second.token > first.token, "token survives dispose/re-init");
  });

  await test("isCurrent is token-based and current() hands out copies", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    ctrl.init({ hooks: makeHooks() });
    const session = await ctrl.select("job-A");
    assert(ctrl.isCurrent(session));
    assert(!ctrl.isCurrent({ jobId: session.jobId, token: session.token + 1 }), "stale token is not current");
    assert(!ctrl.isCurrent(null) && !ctrl.isCurrent(undefined), "null-ish sessions are never current");
    assert(ctrl.current() !== session, "snapshot is a copy, not the internal session");
  });

  await test("applyDetail returning null (job gone from detail AND list) ends the session before files", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    hooks.applyDetail = () => null;
    ctrl.init({ hooks });
    const result = await ctrl.select("job-A");
    assertEq(result, null);
    assertEq(hooks.events, ["reset:job-A"], "no files/panels after an unresolvable job");
  });

  await test("current-session failure surfaces through onSelectionError and rethrows", async () => {
    const sandbox = setup();
    const ctrl = sandbox.window.ACPWorkbench.controllers.taskSession;
    const hooks = makeHooks();
    const errors = [];
    hooks.onSelectionError = (err, id) => errors.push(id + ":" + err.message);
    hooks.loadDetail = () => Promise.reject(new Error("HTTP 500"));
    ctrl.init({ hooks });
    let caught = null;
    try { await ctrl.select("job-A"); } catch (e) { caught = e; }
    assert(caught && caught.message === "HTTP 500", "current-session error propagates (legacy parity)");
    assertEq(errors, ["job-A:HTTP 500"]);
    assertEq(hooks.events, ["reset:job-A"]);
  });

  await test("controller purity: no DOM/window-global/network access in module source", () => {
    const fs = require("fs"), path = require("path");
    const src = fs.readFileSync(path.resolve(__dirname, "../workbench/controllers/task_session_controller.js"), "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["document.", "window.selectedJobId", "jobsCache =", "fetch(", "$3Dmol"]) {
      assert(code.indexOf(bad) === -1, "controller must not use " + bad);
    }
  });
};
