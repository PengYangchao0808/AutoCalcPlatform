/* ACP 2.0 module test — task-explorer (UI-02b).
 *
 * Registers the module under test through the same classic-script contract
 * the Shell loader consumes (window.ACPNextModules["task-explorer"] = {
 * descriptor, factory }). Everything runs in the node vm harness with a fake
 * DOM and a fully mocked client — the sandbox `fetch` throws, so no test can
 * reach the network.
 *
 * Pinned contracts:
 *  1. descriptor shape (interfaceVersion 1, provides tasks.list, single
 *     instancing, error keys);
 *  2. mount + empty state (four-state renderer, zh copy + data-i18n);
 *  3. mocked list renders rows (groups/rows/badges/attempt chip);
 *  4. row click → context.setFocus receives taskRef; session token guard
 *     drops late A→B→A responses (never writes B);
 *  5. project switch supersedes the old project's late list response and
 *     publishes setFocus({projectId});
 *  6. two factory instances never cross-talk (own controller instances);
 *  7. dispose is thorough + idempotent: every store/context subscription and
 *     DOM listener is released, late responses write nothing;
 *  8. factory-ization compat: the legacy singletons (taskSession / taskList)
 *     keep their old API and the factories hand out independent instances.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/task-explorer/module.js";

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 8) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const counters = { added: 0, removed: 0 };
    const byId = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        innerHTML: "",
        value: "",
        hidden: false,
        disabled: false,
        attrs: {},
        listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") byId.set(String(v), this); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
          counters.added++;
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
          counters.removed++;
        },
        fire(type, ev) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this, preventDefault() {} }, ev || {})));
        },
        activeListeners() {
          let n = 0;
          listeners.forEach((s) => { n += s.size; });
          return n;
        },
      };
    }

    return {
      counters,
      byId,
      createElement: (tag) => makeEl(tag),
      getElementById: (id) => byId.get(id) || null,
    };
  }

  function walk(el, out = []) {
    (el.children || []).forEach((c) => { out.push(c); walk(c, out); });
    return out;
  }

  function findByAttr(el, name, value) {
    return walk(el).find((n) => n.getAttribute && n.getAttribute(name) === value) || null;
  }

  function findByClass(el, cls) {
    return walk(el).find((n) => String(n.className || "").split(/\s+/).indexOf(cls) !== -1) || null;
  }

  // ------------------------------------------------------- mock client ---

  function makeTaskViewMock() {
    const viewCalls = [];
    const summaryCalls = [];
    const subs = [];
    const mock = {
      queryKeyOf: (prefs, projectId) => "k:" + (projectId || "") + ":" + (prefs && prefs.q || ""),
      fetchView: (prefs, projectId, offset) => {
        const d = deferred();
        viewCalls.push({ prefs, projectId, offset, deferred: d });
        return d.promise;
      },
      fetchSummary: (jobId) => {
        const d = deferred();
        summaryCalls.push({ jobId: String(jobId), deferred: d });
        return d.promise;
      },
      subscribeView: (prefs, projectId, offset, listener) => {
        const sub = { prefs, projectId, offset, listener, removed: false };
        subs.push(sub);
        return () => { sub.removed = true; };
      },
      snapshotView: () => ({ status: undefined, data: undefined }),
      invalidateAll: () => {},
      viewCalls,
      summaryCalls,
      subs,
      unsubscribed: () => subs.filter((s) => s.removed).length,
    };
    return mock;
  }

  function makeClient(projects) {
    const taskView = makeTaskViewMock();
    return {
      taskView,
      request: (path) => {
        assertEq(path, "/projects", "project list must use the legacy /projects endpoint");
        return Promise.resolve({ projects: projects || [] });
      },
    };
  }

  function makeContext(initial) {
    let snap = Object.assign({ revision: 0, source: "test" }, initial || {});
    const subs = [];
    const ctx = {
      focusCalls: [],
      unsubCount: 0,
      getSnapshot: () => snap,
      subscribe(fn) { subs.push(fn); return () => { const i = subs.indexOf(fn); if (i >= 0) subs.splice(i, 1); ctx.unsubCount++; }; },
      setFocus(patch, source) {
        ctx.focusCalls.push({ patch, source });
        snap = Object.assign({}, snap, patch, { revision: snap.revision + 1, source });
        subs.slice().forEach((fn) => fn(ctx.getSnapshot()));
      },
      activeSubs: () => subs.length,
    };
    return ctx;
  }

  // ---------------------------------------------------------- fixtures ---

  function jobRow(over) {
    return Object.assign({
      id: "job-1", status: "completed", attempt: 1, revision: 1,
      molecule_name: "ethanol", task_name: "sp", workflow: "singlepoint",
      display_name: "ethanol_sp", project_id: null, error: null,
    }, over || {});
  }

  function bodyWith(jobsByGroup, over) {
    const groups = jobsByGroup.map((g, i) => ({
      key: g.key, display_name: g.key, count: g.jobs.length,
      truncated: false, jobs: g.jobs,
    }));
    const total = groups.reduce((n, g) => n + g.jobs.length, 0);
    return Object.assign({
      groups, total, counts: {}, facets: {},
      offset: 0, limit: 200, next_offset: null, truncated: false,
      query: { project_id: null },
    }, over || {});
  }

  const EMPTY_BODY = bodyWith([]);

  function setup(opts) {
    opts = opts || {};
    const dom = makeDom();
    const sandbox = makeSandbox({
      document: dom,
      fetch: function () { throw new Error("fetch is forbidden in task-explorer tests"); },
    });
    loadScript("js/workbench/contracts/task_view_contract.js", sandbox);
    loadScript("js/workbench/controllers/job_detail_controller.js", sandbox);
    loadScript("js/workbench/controllers/task_list_controller.js", sandbox);
    loadScript("js/workbench/controllers/task_session_controller.js", sandbox);
    loadScript(MODULE_PATH, sandbox);

    const host = dom.createElement("div");
    const client = makeClient(opts.projects);
    const context = makeContext(opts.snapshot);
    const commands = { openTaskCalls: [], openStructureCalls: [], openTask(ref, source) { this.openTaskCalls.push({ ref, source }); } };
    const entry = sandbox.window.ACPNextModules["task-explorer"];
    const instance = entry.factory({ context, commands, client });
    const hostDoc = { replaceChildrenCount: 0 };
    host.replaceChildren = function (...nodes) {
      this.children.forEach((c) => { c.parentNode = null; });
      this.children = nodes.slice();
      hostDoc.replaceChildrenCount++;
    };
    host.ownerDocument = dom;
    return { sandbox, dom, host, client, context, commands, entry, instance, hostDoc };
  }

  async function mountReady(ctx, body) {
    await ctx.instance.mount(ctx.host, ctx.context.getSnapshot());
    await flush();
    const call = ctx.client.taskView.viewCalls[0];
    assert(call, "mount must issue the initial task-view fetch");
    call.deferred.resolve(body);
    await flush();
    return call;
  }

  // -------------------------------------------------------------- tests --

  await test("descriptor shape matches the ACPNextModules contract", () => {
    const { entry } = setup();
    const d = entry.descriptor;
    assertEq(d.id, "task-explorer");
    assertEq(d.interfaceVersion, 1);
    assertEq(d.dependencies, []);
    assertEq(d.capabilities, { requires: [], provides: ["tasks.list"] });
    assertEq(d.hosts, ["acp-web"]);
    assertEq(d.supportedInstancing, "single");
    assertEq(d.configSchemaVersion, 0);
    assertEq(d.error, { titleKey: "next.tasks.errorTitle", messageKey: "next.tasks.errorMessage" });
    assert(typeof entry.factory === "function", "factory is a function");
  });

  await test("mount + empty state: four-state renderer shows the zh empty copy with data-i18n", async () => {
    const ctx = setup({ projects: [] });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    assert(root, "mount renders a root element into the host");
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    const empty = findByClass(root, "acp-mod-tasks-state--empty");
    assert(empty, "empty state container rendered");
    const label = findByClass(empty, "acp-mod-tasks-state-label");
    assertEq(label.textContent, "暂无任务");
    assertEq(label.getAttribute("data-i18n"), "next.tasks.empty");
    // Toolbar present: project select + refresh button.
    const refresh = findByClass(root, "acp-mod-tasks-refresh");
    assert(refresh, "refresh button rendered");
    assertEq(refresh.getAttribute("data-i18n"), "next.tasks.refresh");
    // Loading state existed before the body arrived (root flipped to empty).
    assertEq(ctx.client.taskView.viewCalls.length, 1, "exactly one explicit fetch, no polling");
  });

  await test("mocked client list renders rows with status badge and attempt chip", async () => {
    const ctx = setup();
    await mountReady(ctx, bodyWith([
      { key: "ethanol", jobs: [
        jobRow({ id: "job-A", display_name: "ethanol_sp", status: "completed", attempt: 1 }),
        jobRow({ id: "job-B", display_name: "ethanol_opt", status: "running", workflow: "optimize", attempt: 2 }),
      ] },
      { key: "benzene", jobs: [
        jobRow({ id: "job-C", display_name: "benzene_freq", status: "failed", workflow: "frequency", attempt: 3 }),
      ] },
    ]));
    const root = ctx.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const rowA = findByAttr(root, "data-job-id", "job-A");
    const rowB = findByAttr(root, "data-job-id", "job-B");
    const rowC = findByAttr(root, "data-job-id", "job-C");
    assert(rowA && rowB && rowC, "all three rows rendered");
    const nameA = walk(rowA).find((n) => String(n.className).indexOf("acp-mod-tasks-row-name") !== -1);
    assertEq(nameA.textContent, "ethanol_sp", "row shows the display name");
    const badgeA = walk(rowA).find((n) => String(n.className).indexOf("acp-mod-tasks-badge--completed") !== -1);
    assert(badgeA, "completed badge on row A");
    const badgeC = walk(rowC).find((n) => String(n.className).indexOf("acp-mod-tasks-badge--failed") !== -1);
    assert(badgeC, "failed badge on row C");
    assertEq(walk(rowA).some((n) => String(n.className).indexOf("acp-mod-tasks-attempt") !== -1), false,
      "no attempt chip for attempt 1");
    assertEq(walk(rowB).some((n) => String(n.className).indexOf("acp-mod-tasks-attempt") !== -1), true,
      "attempt chip for attempt > 1");
  });

  await test("row click setFocus carries taskRef; A→B→A token guard drops the late B write", async () => {
    const ctx = setup();
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [
        jobRow({ id: "job-A", display_name: "A", attempt: 1 }),
        jobRow({ id: "job-B", display_name: "B", attempt: 1 }),
      ] },
    ]));
    const root = ctx.host.children[0];
    const rowA = findByAttr(root, "data-job-id", "job-A");
    const rowB = findByAttr(root, "data-job-id", "job-B");

    rowA.fire("click"); // session 1 (A)
    rowB.fire("click"); // session 2 (B) supersedes
    rowA.fire("click"); // session 3 (A) supersedes B
    await flush();
    assertEq(ctx.client.taskView.summaryCalls.map((c) => c.jobId), ["job-A", "job-B", "job-A"]);

    // Late A#1 resolves while session 3 owns the selection → dropped.
    ctx.client.taskView.summaryCalls[0].deferred.resolve({ id: "job-A", status: "running", attempt: 1 });
    await flush();
    assertEq(ctx.context.focusCalls, [], "superseded A response must not publish focus");

    // Late B resolves → dropped too (this is the "never writes B" pin).
    ctx.client.taskView.summaryCalls[1].deferred.resolve({ id: "job-B", status: "queued", attempt: 1 });
    await flush();
    assertEq(ctx.context.focusCalls, [], "superseded B response must not publish focus");

    // Live A session completes → exactly one focus write.
    ctx.client.taskView.summaryCalls[2].deferred.resolve({ id: "job-A", status: "running", attempt: 4 });
    await flush();
    assertEq(ctx.context.focusCalls.length, 1, "exactly one setFocus");
    assertEq(ctx.context.focusCalls[0], {
      patch: { taskRef: { jobId: "job-A", attempt: 4 } },
      source: "task-explorer",
    });
    // Row selection marking follows the live session.
    assert(String(rowA.className).indexOf("is-selected") !== -1, "row A marked selected");
  });

  await test("project switch supersedes the old project's late list response", async () => {
    const ctx = setup({ projects: [{ project_id: "P1", name: "项目一" }, { project_id: "P2", name: "项目二" }] });
    await ctx.instance.mount(ctx.host, ctx.context.getSnapshot());
    await flush();
    const first = ctx.client.taskView.viewCalls[0];
    assertEq(first.projectId, null, "initial fetch is unscoped");

    // Project dropdown → P2 (publishes focus + local switch + new fetch).
    const select = findByClass(ctx.host.children[0], "acp-mod-tasks-project");
    assert(select, "project select rendered");
    assertEq(select.children.length, 3, "all-projects option + two projects");
    select.value = "P2";
    select.fire("change");
    await flush();
    assertEq(ctx.context.focusCalls[0], { patch: { projectId: "P2" }, source: "task-explorer" });
    const second = ctx.client.taskView.viewCalls[1];
    assert(second, "project switch triggers a new fetch");
    assertEq(second.projectId, "P2");

    // New project body lands first…
    second.deferred.resolve(bodyWith([
      { key: "g2", jobs: [jobRow({ id: "job-P2", display_name: "p2_task" })] },
    ], { query: { project_id: "P2" } }));
    await flush();
    assert(findByAttr(ctx.host.children[0], "data-job-id", "job-P2"), "P2 rows rendered");

    // …then the OLD project's late response arrives: must be discarded.
    first.deferred.resolve(bodyWith([
      { key: "g1", jobs: [jobRow({ id: "job-P1", display_name: "p1_task" })] },
    ], { query: { project_id: null } }));
    await flush();
    assert(!findByAttr(ctx.host.children[0], "data-job-id", "job-P1"),
      "old project's late rows never render");
    assert(findByAttr(ctx.host.children[0], "data-job-id", "job-P2"),
      "current project rows survive the late write");
  });

  await test("two factory instances never cross-talk (own controller instances)", async () => {
    const a = setup();
    const b = setup();
    await mountReady(a, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-A1", display_name: "A1" })] }]));
    await mountReady(b, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-B1", display_name: "B1" })] }]));

    // Rows render into each own host only.
    assert(findByAttr(a.host.children[0], "data-job-id", "job-A1"));
    assert(findByAttr(b.host.children[0], "data-job-id", "job-B1"));
    assert(!findByAttr(a.host.children[0], "data-job-id", "job-B1"));
    assert(!findByAttr(b.host.children[0], "data-job-id", "job-A1"));

    // Click in instance A publishes only to A's context; B's selection state
    // (and its in-flight session, if any) is untouched.
    findByAttr(a.host.children[0], "data-job-id", "job-A1").fire("click");
    a.client.taskView.summaryCalls[0].deferred.resolve({ id: "job-A1", status: "completed", attempt: 1 });
    await flush();
    assertEq(a.context.focusCalls.length, 1);
    assertEq(b.context.focusCalls, [], "instance B context untouched");
    assertEq(b.client.taskView.summaryCalls.length, 0, "instance B issued no detail fetch");
  });

  await test("dispose releases every subscription/listener, is idempotent, and kills late writes", async () => {
    const ctx = setup();
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-A" })] }]));
    // One pending detail fetch that will land AFTER dispose.
    findByAttr(ctx.host.children[0], "data-job-id", "job-A").fire("click");
    await flush();
    assertEq(ctx.client.taskView.summaryCalls.length, 1);

    // Pre-dispose: store subscription live (afterRender) + context subscription live.
    assert(ctx.client.taskView.unsubscribed() === 0, "store subscription active before dispose");
    assertEq(ctx.context.activeSubs(), 1, "context subscription active before dispose");
    const liveListeners = ctx.dom.counters.added - ctx.dom.counters.removed;
    assert(liveListeners > 0, "module attached DOM listeners while mounted");

    ctx.instance.dispose();
    await flush();
    assertEq(ctx.client.taskView.unsubscribed(), 1, "store subscription cancelled");
    assertEq(ctx.context.unsubCount, 1, "context subscription cancelled");
    assertEq(ctx.dom.counters.added, ctx.dom.counters.removed,
      "every DOM listener added was removed on dispose");
    assertEq(ctx.host.children.length, 0, "host DOM cleared");

    // Late summary response after dispose: writes nothing, throws nothing.
    ctx.client.taskView.summaryCalls[0].deferred.resolve({ id: "job-A", status: "failed", attempt: 1 });
    await flush();
    assertEq(ctx.context.focusCalls, [], "late response after dispose never publishes focus");

    // dispose() is idempotent.
    ctx.instance.dispose();
    ctx.instance.dispose();
    assertEq(ctx.client.taskView.unsubscribed(), 1, "no double-unsubscribe on repeated dispose");
    assertEq(ctx.context.unsubCount, 1);
  });

  await test("factory-ization compat: legacy singletons keep the old API, factories hand out independent instances", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/workbench/controllers/task_list_controller.js", sandbox);
    loadScript("js/workbench/controllers/task_session_controller.js", sandbox);
    const C = sandbox.window.ACPWorkbench.controllers;

    // Legacy singletons still exist with the exact old surface.
    const ts = C.taskSession;
    assert(ts && typeof ts.init === "function" && typeof ts.select === "function"
      && typeof ts.current === "function" && typeof ts.isCurrent === "function"
      && typeof ts.clear === "function" && typeof ts.dispose === "function",
      "taskSession singleton keeps init/select/current/isCurrent/clear/dispose");
    const tl = C.taskList;
    assert(tl && typeof tl.init === "function" && typeof tl.refresh === "function"
      && typeof tl.setOffset === "function" && typeof tl.invalidateMetadata === "function"
      && typeof tl.getState === "function" && typeof tl.isSilentCancel === "function"
      && typeof tl.dispose === "function",
      "taskList singleton keeps init/refresh/setOffset/invalidateMetadata/getState/isSilentCancel/dispose");

    // Singleton smoke: the old selection flow still works end to end.
    const events = [];
    ts.init({ hooks: {
      resetForSelection: (id) => events.push("reset:" + id),
      loadDetail: (id) => Promise.resolve({ job: { id } }),
      applyDetail: (d) => { events.push("apply:" + d.job.id); return d.job; },
    } });
    const session = await ts.select("job-legacy");
    assert(session && session.jobId === "job-legacy", "singleton select still resolves the session");
    assert(ts.isCurrent(session));
    assertEq(events, ["reset:job-legacy", "apply:job-legacy"]);
    ts.dispose();
    assertEq(ts.current(), null);

    // Factories exist and produce instances independent of the singletons
    // (per-instance state: one instance's select can't touch another).
    const i1 = C.createTaskSessionController();
    const i2 = C.createTaskSessionController();
    assert(i1 !== ts && i2 !== ts && i1 !== i2, "each factory call yields a fresh instance");
    const detailX = deferred();
    i1.init({ hooks: { resetForSelection: () => {}, loadDetail: () => detailX.promise } });
    i2.init({ hooks: {
      resetForSelection: () => {},
      loadDetail: (id) => Promise.resolve({ job: { id } }),
      applyDetail: (d) => d.job,
    } });
    const p1 = i1.select("job-X"); // stays in flight until detailX settles
    const s2 = await i2.select("job-Y");
    assertEq(s2.jobId, "job-Y", "instance 2 completes its own selection");
    assertEq(i1.current().jobId, "job-X", "instance 1's selection is untouched by instance 2");
    assert(i1.isCurrent(i1.current()), "instance 1's in-flight session is still current in instance 1");
    i1.dispose();
    detailX.resolve({ job: { id: "job-X" } }); // late response lands after dispose
    assertEq(await p1, null, "disposed instance's session dies at its isCurrent check");
    assertEq(i2.current().jobId, "job-Y", "instance 2 survives instance 1's dispose");
    i2.dispose();
    assertEq(i2.current(), null);

    const l1 = C.createTaskListController();
    const l2 = C.createTaskListController();
    assert(l1 !== tl && l2 !== tl && l1 !== l2, "list factories hand out fresh instances");
    assertEq(l1.getState(), { offset: 0, queryKey: null, hasBody: false });
    assertEq(l2.getState(), { offset: 0, queryKey: null, hasBody: false });
  });

  await test("read-only surface: no write-operation controls in the rendered module", async () => {
    const ctx = setup();
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] }]));
    const labels = walk(ctx.host.children[0])
      .filter((n) => n.tagName === "BUTTON")
      .map((n) => n.textContent);
    // P0 allows only navigation/refresh controls.
    assert(labels.indexOf("刷新") !== -1, "refresh button present");
    const forbidden = ["暂停", "取消", "删除", "重跑", "purge", "pause", "cancel", "rerun"];
    labels.forEach((text) => {
      forbidden.forEach((bad) => {
        assert(text.indexOf(bad) === -1, "P0 must not render write control: " + text);
      });
    });
  });
};
