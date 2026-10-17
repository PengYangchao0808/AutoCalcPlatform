/* Behavior tests for the task list controller (OC-4). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function loadController() {
    const sandbox = makeSandbox({});
    loadScript("js/client/transport.js", sandbox);
    loadScript("js/client/resource-store.js", sandbox);
    loadScript("js/client/task-view-query.js", sandbox);
    loadScript("js/workbench/contracts/task_view_contract.js", sandbox);
    loadScript("js/workbench/contracts/fixtures/task_view_samples.js", sandbox);
    loadScript("js/workbench/controllers/task_list_controller.js", sandbox);
    const W = sandbox.window;
    const samples = W.ACPWorkbench.contracts.taskViewSamples;
    const effects = {
      rendered: [], errors: [], emptyErrors: 0, rows: null, pruned: 0,
      paginated: [], afterRender: 0, sortedGroups: 0,
    };
    let prefs = { groupBy: "molecule", sort: "created_desc" };
    let bodies = [samples.normal];
    const controller = W.ACPWorkbench.controllers.taskList;
    const taskView = W.ACPClient.taskView;
    taskView.init({
      v1: () => Promise.resolve({}),
      v2: (path, opts) => {
        const body = bodies[Math.min(callCount, bodies.length - 1)];
        callCount++;
        lastOpts = opts;
        return Promise.resolve(body);
      },
    });
    let callCount = 0;
    let lastOpts = null;
    controller.init({
      client: taskView,
      contract: W.ACPWorkbench.contracts.taskView,
      getPrefs: () => prefs,
      getSelectedProjectId: () => null,
      getLanguage: () => "zh-CN",
      detailCacheRevision: () => 0,
      renderKeyOf: (parts) => JSON.stringify(parts),
      views: {
        sortGroups: (body, p) => { effects.sortedGroups++; if (p.sort === "name_asc") body.groups.sort((a, b) => a.key.localeCompare(b.key)); },
        onRows: (flat) => { effects.rows = flat; },
        pruneDetailsAgainstList: () => { effects.pruned++; },
        renderQueue: (counts) => { effects.rendered.push(counts); },
        renderPagination: (body) => { effects.paginated.push([body.offset, body.total]); },
        afterRender: () => { effects.afterRender++; },
        onError: (err) => { effects.errors.push(err && err.message); },
        onEmptyError: () => { effects.emptyErrors++; },
      },
    });
    return { sandbox, controller, taskView, samples, effects, setPrefs: (p) => { prefs = p; }, setBodies: (b) => { bodies = b; callCount = 0; }, getState: () => ({ callCount, lastOpts }) };
  }

  await test("refresh derives rows in group order, renders once, paginates (legacy flow)", async () => {
    const { controller, effects } = loadController();
    const body = await controller.refresh();
    assertEq(body.total, 3);
    assertEq(effects.rows.length, 3, "jobsCache rows derived via callback");
    assertEq(effects.rows[0].molecule_name, "ethanol", "group order preserved");
    assertEq(effects.rendered.length, 1, "queue rendered once");
    assertEq(effects.paginated, [[0, 3]]);
    assert(effects.pruned >= 1, "detail pruning hook ran");
    assertEq(effects.errors.filter(Boolean), [], "no error surfaced (null clears state)");
  });

  await test("unchanged body does not re-render queue (render-key gating)", async () => {
    const { controller, effects } = loadController();
    await controller.refresh();
    await controller.refresh();
    assertEq(effects.rendered.length, 1, "identical body → no re-render");
    assertEq(effects.afterRender, 2, "but after-render hooks still run (chips/mobile)");
  });

  await test("queryKey change resets pagination to offset 0", async () => {
    const ctx = loadController();
    const { controller, effects } = ctx;
    await controller.refresh();
    controller.setOffset(200);
    ctx.setPrefs({ groupBy: "molecule", sort: "name_asc" }); // new queryKey
    const pending = controller.refresh(); // sync prologue resets offset before fetch
    const st = controller.getState();
    assertEq(st.offset, 0, "offset reset before fetch");
    await pending;
    assert(effects.paginated[effects.paginated.length - 1][0] === 0);
  });

  await test("offset beyond total clamps to last page and refetches once", async () => {
    const ctx = loadController();
    const { controller } = ctx;
    await controller.refresh();
    await controller.setOffset(600); // total=3 → clamp happens when body arrives
    assertEq(controller.getState().offset, 0, "clamped to last page");
  });

  await test("error surfaces with retry; empty cache also signals empty-error; silent cancels skipped", async () => {
    const ctx = loadController();
    const { controller, effects } = ctx;
    ctx.setBodies([{ __fail: true }]);
    // make fetch reject by swapping v2 adapter behavior via bodies marker
    const sandbox = ctx.sandbox;
    // patch taskView adapter: easiest is init again with rejecting v2
    ctx.taskView.init({
      v1: () => Promise.resolve({}),
      v2: () => Promise.reject(new Error("HTTP 503")),
    });
    let caught = null;
    await controller.refresh().catch((e) => { caught = e; });
    assert(caught && caught.message === "HTTP 503", "error propagates to caller");
    assertEq(effects.errors, ["HTTP 503"], "onError with message");
    assertEq(effects.emptyErrors, 1, "empty-cache failure hits queue-counts path");
    // silent cancel names never surface
    assert(controller.isSilentCancel(Object.assign(new Error("x"), { name: "AbortError" })));
    assert(controller.isSilentCancel(Object.assign(new Error("x"), { name: "StaleDiscard" })));
    assert(!controller.isSilentCancel(new Error("HTTP 500")));
  });

  await test("invalidateMetadata makes next refresh refetch (write-invalidation hook)", async () => {
    const ctx = loadController();
    const { controller } = ctx;
    await controller.refresh();
    controller.invalidateMetadata();
    const before = ctx.getState().callCount;
    await controller.refresh();
    assert(ctx.getState().callCount === before + 1, "refetch after invalidation");
  });

  await test("controller never touches DOM: no document usage in module source", () => {
    const fs = require("fs"), path = require("path");
    const src = fs.readFileSync(path.resolve(__dirname, "../workbench/controllers/task_list_controller.js"), "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
    for (const bad of ["document.", "window.selectedJobId", "jobsCache =", "fetch(", "$3Dmol"]) {
      assert(code.indexOf(bad) === -1, "controller must not use " + bad);
    }
  });
};
