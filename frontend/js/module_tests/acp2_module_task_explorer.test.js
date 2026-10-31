/* ACP 2.0 module test — task-explorer (UI-02b + A-17…A-23).
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
 *     keep their old API and the factories hand out independent instances;
 *  9. A-17: the visible status filter's chip vocabulary is derived from the
 *     frozen task-view contract (+ server counts drift), filters ride
 *     prefs.filters.status through the owned list controller (query-key
 *     change resets pagination), status badges are translated;
 * 10. A-18: row actions are state-aware — pause only RUNNING, unpause only
 *     PAUSED, continue only FAILED/CANCELLED; PAUSED stays active
 *     (cancel present, rerun absent) and is never pausable;
 * 11. A-19: rerun only terminal, cancel only non-terminal; every command goes
 *     through the injected client.jobCommands, exposes busy/error states and
 *     refreshes via the existing task-view/store path on success;
 * 12. absent client.jobCommands → zero write actions (no fallback command
 *     client); a partial client disables only the missing action.
 * 13. A-20: per-job and multi-select batch purge are TWO-STEP (a dialog is
 *     the second confirmation), call only client.jobCommands.purge({jobIds,
 *     deleteData}), render the server's per-job report text verbatim (the
 *     authoritative terminal/active semantics), and never DELETE/fetch;
 * 14. A-21: clone/move/rename call only client.jobCommands.clone/.move/
 *     .rename; rename sends the row name_revision as expected_name_revision
 *     and retries a 409 conflict with the server revision; move/clone pick a
 *     target from the already-loaded project list; success refreshes through
 *     the owned list controller;
 * 15. A-20/A-21 keep the generation/dispose guards: a late purge/rename/
 *     clone/move receipt after a project switch or dispose writes nothing,
 *     and one row never receives a second concurrent command.
 * 16. A-22: project list/create/rename/delete ride the existing v1 project
 *     endpoints only (GET/POST /projects, PATCH/DELETE /projects/{id}); the
 *     delete dialog shows the server dependency count from the existing
 *     GET /projects/{id}/jobs before the second confirmation, server
 *     refusals are rendered verbatim, and every success re-reads the same
 *     project list / refreshes through the owned list controller (no second
 *     project cache, no constructed path);
 * 17. A-23: job tags are labels only (never path components); add/remove go
 *     through the existing client.jobCommands.batchOp add_tags/remove_tags
 *     ops, per-task server refusals and transport errors are shown inline
 *     without a refresh, and success refreshes through the task-view/store
 *     path. Missing jobCommands hides the editor; a missing batchOp only
 *     disables it.
 * 18. A-24: the recycle bin is the SAME owned list controller switched to the
 *     EXISTING recoverable (archived) scope — prefs.archived = "only" rides
 *     ACPClient.taskView.queryKeyOf, so no second cache and no direct fetch.
 *     There is NO /jobs/trash endpoint and purged tasks are not listable
 *     (store.purge_cascade removes the rows): the trash lists archived
 *     (soft-deleted) tasks, restores them through the existing batch-ops
 *     "unarchive" op, and permanently deletes through the existing purge
 *     cascade command behind a second confirmation dialog. Batch selection
 *     only ever targets rendered trash rows and is cleared on enter/leave;
 *     the normal queue keeps its A-20 surface unchanged; a client without
 *     jobCommands keeps the trash readable but actionless.
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
      queryKeyOf: (prefs, projectId) => "k:" + (projectId || "") + ":"
        + ((prefs && prefs.q) || "") + ":"
        + (((prefs && prefs.filters && prefs.filters.status) || []).join(",")),
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

  /** Fake ACPClient.jobCommands — records each command and hands back a
   * deferred so a test controls busy → success/error transitions. */
  function makeJobCommandsMock(opts) {
    opts = opts || {};
    const omitted = opts.omit || [];
    const calls = [];
    const mock = { calls };
    const record = (name, entry) => {
      const d = deferred();
      calls.push(Object.assign({ name, deferred: d }, entry));
      return d.promise;
    };
    ["pause", "unpause"].forEach((name) => {
      if (omitted.indexOf(name) >= 0) return;
      mock[name] = (jobId) => record(name, { jobId: String(jobId) });
    });
    if (omitted.indexOf("continueJob") < 0) {
      mock.continueJob = (jobId) => record("continueJob", { jobId: String(jobId) });
    }
    ["rerun", "cancel"].forEach((name) => {
      if (omitted.indexOf(name) >= 0) return;
      mock[name] = (jobId) => record(name, { jobId: String(jobId) });
    });
    // A-20 purge takes the canonical { jobIds, deleteData } request object.
    if (omitted.indexOf("purge") < 0) {
      mock.purge = (request) => record("purge", {
        request: request,
        jobIds: request && request.jobIds ? request.jobIds.map(String) : null,
        deleteData: !!(request && request.deleteData),
      });
    }
    // A-21 clone/move take (jobId, projectId); rename takes the name revision.
    if (omitted.indexOf("clone") < 0) {
      mock.clone = (jobId, projectId) => record("clone", {
        jobId: String(jobId),
        projectId: projectId == null ? null : String(projectId),
      });
    }
    if (omitted.indexOf("move") < 0) {
      mock.move = (jobId, projectId) => record("move", {
        jobId: String(jobId),
        projectId: projectId == null ? null : String(projectId),
      });
    }
    if (omitted.indexOf("rename") < 0) {
      mock.rename = (jobId, customName, revision) => record("rename", {
        jobId: String(jobId),
        customName: customName,
        revision: revision,
      });
    }
    // A-23 tags ride the existing batch-ops command (add_tags/remove_tags).
    if (omitted.indexOf("batchOp") < 0) {
      mock.batchOp = (request) => record("batchOp", {
        request: request,
        taskIds: request && request.taskIds ? request.taskIds.map(String) : null,
        op: request && request.op,
        payload: request && request.payload,
      });
    }
    mock.isRerunInFlight = (jobId) => calls.some((c) => c.name === "rerun" && c.jobId === String(jobId));
    return mock;
  }

  /** Fake ACPClient.
   *
   * Project traffic (A-22) goes through client.request(path, opts) — the same
   * seam the toolbar always used for GET /projects. Every call is recorded;
   * GET /projects resolves from the shared `projectsData` array (tests may
   * mutate it to model the server), every other request auto-resolves unless
   * opts.onRequest defers it (`entry.deferred` then controls settle).
   */
  function makeClient(projects, jobCommands, clientOptions) {
    clientOptions = clientOptions || {};
    const taskView = makeTaskViewMock();
    const projectsData = projects || [];
    const requests = [];
    const client = { taskView, requests, projectsData };
    const handle = (path, opts) => {
      const method = (opts && opts.method) || "GET";
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      const entry = { path, method, body, opts: opts || {} };
      requests.push(entry);
      if (typeof clientOptions.onRequest === "function") {
        const custom = clientOptions.onRequest(entry, deferred);
        if (custom !== undefined) return custom;
      }
      if (method === "GET" && path === "/projects") {
        return Promise.resolve({ projects: projectsData });
      }
      if (method === "GET") return Promise.resolve({ jobs: [] });
      return Promise.resolve({});
    };
    if (!clientOptions.omitRequest) client.request = handle;
    if (clientOptions.transport) client.transport = clientOptions.transport;
    if (jobCommands) client.jobCommands = jobCommands;
    return client;
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

  // Whole-scope status counts (task_views contract: counts ignore filters).
  const STATUS_COUNTS = {
    queued: 1, starting: 0, pending: 0, running: 2, paused: 1, cancelling: 0,
    waiting_review: 0, completed: 3, failed: 1, cancelled: 1,
  };
  const CONTRACT_STATUSES = [
    "queued", "starting", "pending", "running", "paused", "cancelling",
    "waiting_review", "completed", "failed", "cancelled",
  ];

  const EMPTY_BODY = bodyWith([]);

  function actionButtons(row) {
    return walk(row).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-action"));
  }

  function actionNames(row) {
    return actionButtons(row).map((b) => b.getAttribute("data-acp-action"));
  }

  function messageOf(row) {
    return walk(row).find((n) => String(n.className).indexOf("acp-mod-tasks-row-msg") !== -1) || null;
  }

  function filterChips(root) {
    const filters = findByClass(root, "acp-mod-tasks-filters");
    return filters
      ? walk(filters).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-status"))
      : [];
  }

  function chipLabel(chip) {
    return walk(chip).find((n) => String(n.className).indexOf("acp-mod-tasks-filter-chip-label") !== -1) || null;
  }

  // -------------------------------- A-20/A-21 surface helpers (new) ------

  function lifecycleButtons(row) {
    return walk(row).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-lifecycle"));
  }

  function lifecycleButton(row, action) {
    return lifecycleButtons(row).find((b) => b.getAttribute("data-acp-lifecycle") === action) || null;
  }

  function dialogHost(root) {
    return findByClass(root, "acp-mod-tasks-modal");
  }

  function dialogOpen(root) {
    const host = dialogHost(root);
    return !!host && host.hidden === false && String(host.getAttribute("data-acp-dialog-state")) !== "closed";
  }

  function dialogCards(root) {
    const host = dialogHost(root);
    return host ? walk(host).filter((n) => String(n.className).split(/\s+/).indexOf("acp-mod-tasks-modal-card") !== -1) : [];
  }

  function dialogCard(root) {
    return dialogCards(root)[0] || null;
  }

  function dialogKind(root) {
    const host = dialogHost(root);
    return host ? host.getAttribute("data-acp-dialog") : null;
  }

  function dialogConfirm(root) {
    return walk(root).find((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-dialog-confirm")) || null;
  }

  function dialogCancel(root) {
    return walk(root).find((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-dialog-cancel")) || null;
  }

  function dialogErrorEl(root) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute("data-acp-dialog-error") === "") || null;
  }

  function dialogInput(root) {
    return walk(root).find((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-dialog-input") === "") || null;
  }

  function dialogDeleteData(root) {
    return walk(root).find((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-dialog-delete-data") === "") || null;
  }

  function dialogProjectSelect(root) {
    return walk(root).find((n) => n.tagName === "SELECT" && n.getAttribute("data-acp-dialog-project") === "") || null;
  }

  function rowCheckbox(root, jobId) {
    return walk(root).find((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-select") === jobId) || null;
  }

  function batchBar(root) {
    return findByClass(root, "acp-mod-tasks-batch");
  }

  function batchButton(root, name) {
    return walk(root).find((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-batch") === name) || null;
  }

  function batchMessage(root) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute("data-acp-batch-msg") === "") || null;
  }

  /** Open a dialog through its row button (first click = confirmation request,
   * NOT the command). */
  function clickLifecycle(root, jobId, action) {
    const btn = lifecycleButton(findByAttr(root, "data-job-id", jobId), action);
    assert(btn, "lifecycle button " + action + " rendered for " + jobId);
    btn.fire("click");
    return btn;
  }

  // -------------------------------- A-22/A-23 surface helpers (new) ------

  function manageProjectsButton(root) {
    return walk(root).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-projects-manage") !== null) || null;
  }

  function projectRows(root) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute("data-acp-project-row"));
  }

  function projectRow(root, projectId) {
    return findByAttr(root, "data-acp-project-row", projectId);
  }

  function projectRowButton(row, attr, projectId) {
    return walk(row).find((n) => n.tagName === "BUTTON"
      && n.getAttribute(attr) === projectId) || null;
  }

  function projectCreateInput(root) {
    return walk(root).find((n) => n.tagName === "INPUT"
      && n.getAttribute("data-acp-project-new") === "") || null;
  }

  function projectCreateButton(root) {
    return walk(root).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-project-create") === "") || null;
  }

  function projectRenameInput(root) {
    return walk(root).find((n) => n.tagName === "INPUT"
      && n.getAttribute("data-acp-project-input") === "") || null;
  }

  function projectDeps(root) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute("data-acp-project-deps")) || null;
  }

  function dialogNotice(root) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute("data-acp-dialog-notice") === "") || null;
  }

  function tagsButton(row, jobId) {
    return walk(row).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-tags") === jobId) || null;
  }

  function rowTagChips(row) {
    return walk(row).filter((n) => n.getAttribute && n.getAttribute("data-acp-tag"));
  }

  function dialogTagChips(root) {
    return walk(root).filter((n) => n.getAttribute && n.getAttribute("data-acp-tag-chip"));
  }

  function dialogTagInput(root) {
    return walk(root).find((n) => n.tagName === "INPUT"
      && n.getAttribute("data-acp-tag-input") === "") || null;
  }

  function dialogTagAdd(root) {
    return walk(root).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-tag-add") === "") || null;
  }

  function dialogTagRemove(root, tag) {
    return walk(root).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-tag-remove") === tag) || null;
  }

  function requestsOf(ctx, method, path) {
    return ctx.client.requests.filter((r) => r.method === method && r.path === path);
  }

  function requestsMatching(ctx, method, prefix) {
    return ctx.client.requests.filter((r) => r.method === method && r.path.indexOf(prefix) === 0);
  }

  // -------------------------------- A-24 trash surface helpers ------

  function trashToggle(root) {
    return walk(root).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-trash-toggle") !== null) || null;
  }

  function trashNote(root) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute("data-acp-trash-note") !== null) || null;
  }

  function trashReadonly(root) {
    return walk(root).find((n) => String(n.className).indexOf("acp-mod-tasks-trash-readonly") !== -1) || null;
  }

  function trashRows(root) {
    return walk(root).filter((n) => n.tagName === "DIV"
      && n.getAttribute && n.getAttribute("data-acp-trash-row") === "true");
  }

  function trashRow(root, jobId) {
    const row = findByAttr(root, "data-job-id", jobId);
    return row && row.getAttribute("data-acp-trash-row") === "true" ? row : null;
  }

  function trashAction(row, name) {
    return walk(row).find((n) => n.tagName === "BUTTON"
      && n.getAttribute("data-acp-trash-action") === name) || null;
  }

  function trashActions(root) {
    return walk(root).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-trash-action"));
  }

  function trashBody(jobs, over) {
    return bodyWith([{ key: "trash", jobs: jobs.map((j) => jobRow(Object.assign({ archived: true }, j))) }], over);
  }

  /** Defer the A-22 project traffic: the initial GET /projects auto-resolves
   * (mount needs it) while the dependency probe and POST/PATCH/DELETE stay
   * pending until the test settles `entry.deferred`. */
  function deferProjectMutations(entry) {
    if (entry.method === "GET" && entry.path === "/projects") return undefined;
    const d = deferred();
    entry.deferred = d;
    return d.promise;
  }

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
    const client = opts.client || makeClient(opts.projects, opts.jobCommands, opts.clientOptions);
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
    return { sandbox, dom, host, client, context, commands, entry, instance, hostDoc, jobCommands: opts.jobCommands };
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

  // ------------------------------------------------- A-17 filter/status --

  await test("A-17 status filter chips mirror the contract vocabulary and filter through the controller", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ], { counts: STATUS_COUNTS }));
    const root = ctx.host.children[0];

    const filters = findByClass(root, "acp-mod-tasks-filters");
    assert(filters, "visible status filter rendered");
    assertEq(filters.getAttribute("data-acp-filters"), "status");
    assertEq(filters.getAttribute("data-i18n-aria"), "next.tasks.filter_status");
    assertEq(findByClass(filters, "acp-mod-tasks-filters-label").textContent, "状态筛选",
      "the filter carries a visible label, not only an aria label");
    const chips = filterChips(root);
    assertEq(chips.map((c) => c.getAttribute("data-acp-status")), CONTRACT_STATUSES,
      "chip vocabulary comes from the frozen task-view contract (no local list)");

    const paused = chips.find((c) => c.getAttribute("data-acp-status") === "paused");
    assertEq(chipLabel(paused).textContent, "已暂停");
    assertEq(chipLabel(paused).getAttribute("data-i18n"), "next.tasks.status.paused");
    const running = chips.find((c) => c.getAttribute("data-acp-status") === "running");
    const runningCount = walk(running)
      .find((n) => String(n.className).indexOf("acp-mod-tasks-filter-chip-count") !== -1);
    assertEq(runningCount.textContent, "2", "chip count comes from the whole-scope body.counts");
    assertEq(running.getAttribute("aria-pressed"), "false");

    // Clicking the already-active "全部" chip must not issue a redundant fetch.
    const beforeClicks = ctx.client.taskView.viewCalls.length;
    findByAttr(root, "data-acp-filter", "all").fire("click");
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, beforeClicks,
      "activating the all-status chip again is a no-op");

    // Toggle → prefs.filters.status → owned controller refresh (single cache).
    running.fire("click");
    await flush();
    const filtered = ctx.client.taskView.viewCalls[1];
    assert(filtered, "chip toggle refreshes through the task-view controller");
    assertEq(filtered.prefs.filters.status, ["running"]);
    assertEq(filtered.offset, 0);
    assertEq(running.getAttribute("aria-pressed"), "true");
    assertEq(findByAttr(root, "data-acp-filter", "all").getAttribute("aria-pressed"), "false");
    filtered.deferred.resolve(bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ], { counts: STATUS_COUNTS }));
    await flush();

    // Server vocabulary drift: a counted status the frozen contract does not
    // know yet stays visible as an extra chip (never silently unfilterable).
    findByClass(root, "acp-mod-tasks-refresh").fire("click");
    await flush();
    const refetch = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    refetch.deferred.resolve(bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "retrying" })] },
    ], { counts: Object.assign({}, STATUS_COUNTS, { retrying: 2 }) }));
    await flush();
    const drift = filterChips(root).find((c) => c.getAttribute("data-acp-status") === "retrying");
    assert(drift, "server-only status still gets a chip");
    assertEq(chipLabel(drift).textContent, "retrying", "unknown status label falls back verbatim");

    // Selecting that drift chip and losing the server count must not orphan the
    // filter: the active value keeps its chip so the user can toggle it off.
    drift.fire("click");
    await flush();
    const driftFetch = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(driftFetch.prefs.filters.status, ["running", "retrying"]);
    driftFetch.deferred.resolve(bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ], { counts: STATUS_COUNTS }));
    await flush();
    const driftAgain = filterChips(root).find((c) => c.getAttribute("data-acp-status") === "retrying");
    assert(driftAgain, "an active filter value never loses its chip");
    assertEq(driftAgain.getAttribute("aria-pressed"), "true");
  });

  await test("A-17 status filter resets pagination through the controller query key", async () => {
    const ctx = setup({ jobCommands: makeJobCommandsMock() });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ], { total: 300, limit: 200, counts: STATUS_COUNTS }));
    const root = ctx.host.children[0];

    const nextBtn = walk(root).find((n) => n.tagName === "BUTTON" && n.textContent === "下一页");
    assert(nextBtn, "pagination next button rendered for total > limit");
    nextBtn.fire("click");
    await flush();
    const page2 = ctx.client.taskView.viewCalls[1];
    assertEq(page2.offset, 200, "pagination moved the owned controller offset");
    page2.deferred.resolve(bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ], { total: 300, limit: 200, offset: 200, counts: STATUS_COUNTS }));
    await flush();

    filterChips(root).find((c) => c.getAttribute("data-acp-status") === "paused").fire("click");
    await flush();
    const filtered = ctx.client.taskView.viewCalls[2];
    assert(filtered, "filter change triggers a refresh");
    assertEq(filtered.offset, 0, "query-key change resets pagination (controller rule)");
    assertEq(filtered.prefs.filters.status, ["paused"]);
  });

  // ---------------------------------------------- A-18/A-19 row commands --

  await test("A-18/A-19 row actions are state-aware — PAUSED is active, never terminal or pausable", async () => {
    const ctx = setup({ jobCommands: makeJobCommandsMock() });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-run", status: "running" }),
      jobRow({ id: "job-paused", status: "paused" }),
      jobRow({ id: "job-queued", status: "queued" }),
      jobRow({ id: "job-review", status: "waiting_review" }),
      jobRow({ id: "job-completed", status: "completed" }),
      jobRow({ id: "job-failed", status: "failed" }),
      jobRow({ id: "job-cancelled", status: "cancelled" }),
    ] }], { counts: STATUS_COUNTS }));
    const root = ctx.host.children[0];
    const row = (id) => findByAttr(root, "data-job-id", id);

    assertEq(actionNames(row("job-run")), ["pause", "cancel"], "RUNNING → pause + cancel");
    assertEq(actionNames(row("job-paused")), ["unpause", "cancel"], "PAUSED → unpause + cancel");
    assertEq(actionNames(row("job-queued")), ["cancel"], "QUEUED is active → cancel only");
    assertEq(actionNames(row("job-review")), ["cancel"], "WAITING_REVIEW is active → cancel only");
    assertEq(actionNames(row("job-completed")), ["rerun"], "COMPLETED is terminal → rerun only");
    assertEq(actionNames(row("job-failed")), ["continue", "rerun"], "FAILED → checkpoint continue + rerun");
    assertEq(actionNames(row("job-cancelled")), ["continue", "rerun"], "CANCELLED → continue + rerun");

    // PAUSED is active/non-terminal: no pause, no terminal-only rerun.
    assertEq(row("job-paused").getAttribute("data-acp-status"), "paused");
    assert(actionNames(row("job-paused")).indexOf("pause") === -1, "never show pause for PAUSED");
    assert(actionNames(row("job-paused")).indexOf("rerun") === -1, "never treat PAUSED as terminal");
    // Translated status badge (A-17) carries the raw status separately.
    const badge = walk(row("job-paused"))
      .find((n) => String(n.className).indexOf("acp-mod-tasks-badge--paused") !== -1);
    assertEq(badge.textContent, "已暂停");
    assertEq(badge.getAttribute("data-i18n"), "next.tasks.status.paused");
    assertEq(badge.getAttribute("data-acp-status"), "paused");
  });

  await test("A-18 pause runs through client.jobCommands with busy state and refreshes via the list controller", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ]));
    const root = ctx.host.children[0];
    const pauseBtn = actionButtons(findByAttr(root, "data-job-id", "job-A"))
      .find((b) => b.getAttribute("data-acp-action") === "pause");

    pauseBtn.fire("click");
    assertEq(jc.calls.map((c) => c.name + ":" + c.jobId), ["pause:job-A"]);
    const row = findByAttr(root, "data-job-id", "job-A");
    assertEq(row.getAttribute("data-acp-action-state"), "busy");
    assertEq(pauseBtn.disabled, true, "all row actions are disabled while busy");
    const msg = messageOf(row);
    assertEq(msg.hidden, false);
    assertEq(msg.getAttribute("data-acp-action-state"), "busy");
    assertEq(msg.textContent, "暂停中…");
    const before = ctx.client.taskView.viewCalls.length;
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, before, "nothing refreshes before the command settles");

    // Busy state is data-driven, not a one-off DOM flag: a re-render keeps it.
    ctx.instance.resize();
    const rowAgain = findByAttr(root, "data-job-id", "job-A");
    assertEq(rowAgain.getAttribute("data-acp-action-state"), "busy");
    assertEq(actionButtons(rowAgain).every((b) => b.disabled === true), true);

    jc.calls[0].deferred.resolve({ id: "job-A", status: "paused" });
    await flush();
    assert(ctx.client.taskView.viewCalls.length > before,
      "success refreshes the list through the task-view/store path");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(reload.prefs.filters.status, [], "refresh keeps the current filter prefs");
    assertEq(reload.projectId, null, "refresh keeps the current project scope");
    reload.deferred.resolve(bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "paused" })] },
    ]));
    await flush();
    const pausedRow = findByAttr(root, "data-job-id", "job-A");
    assertEq(pausedRow.getAttribute("data-acp-action-state"), "idle");
    assertEq(actionNames(pausedRow), ["unpause", "cancel"],
      "refreshed row swaps pause for unpause (PAUSED stays active)");
  });

  await test("A-18 unpause and continueJob are wired to the injected command client", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-paused", status: "paused" }),
      jobRow({ id: "job-failed", status: "failed" }),
    ] }]));
    const root = ctx.host.children[0];

    actionButtons(findByAttr(root, "data-job-id", "job-paused"))
      .find((b) => b.getAttribute("data-acp-action") === "unpause").fire("click");
    assertEq(jc.calls[0].name, "unpause");
    assertEq(jc.calls[0].jobId, "job-paused");
    jc.calls[0].deferred.resolve({});
    await flush();

    actionButtons(findByAttr(root, "data-job-id", "job-failed"))
      .find((b) => b.getAttribute("data-acp-action") === "continue").fire("click");
    assertEq(jc.calls[1].name, "continueJob", "continue maps to client.jobCommands.continueJob");
    assertEq(jc.calls[1].jobId, "job-failed");
    jc.calls[1].deferred.resolve({});
    await flush();
    assert(ctx.client.taskView.viewCalls.length >= 3,
      "each successful command refreshes the list exactly through the controller");
  });

  await test("A-19 rerun is terminal-aware, cancel is active-aware, both go through jobCommands", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-done", status: "completed" }),
      jobRow({ id: "job-run", status: "running" }),
    ] }]));
    const root = ctx.host.children[0];

    actionButtons(findByAttr(root, "data-job-id", "job-done"))
      .find((b) => b.getAttribute("data-acp-action") === "rerun").fire("click");
    assertEq(jc.calls[0].name, "rerun");
    assertEq(jc.calls[0].jobId, "job-done");
    jc.calls[0].deferred.resolve({});
    await flush();

    actionButtons(findByAttr(root, "data-job-id", "job-run"))
      .find((b) => b.getAttribute("data-acp-action") === "cancel").fire("click");
    assertEq(jc.calls[1].name, "cancel");
    assertEq(jc.calls[1].jobId, "job-run");
    jc.calls[1].deferred.resolve({});
    await flush();

    assertEq(actionNames(findByAttr(root, "data-job-id", "job-done")).indexOf("cancel"), -1,
      "terminal rows never expose cancel");
    assertEq(actionNames(findByAttr(root, "data-job-id", "job-run")).indexOf("rerun"), -1,
      "active rows never expose rerun");
    assert(ctx.client.taskView.viewCalls.length >= 3,
      "each success refreshes via the existing task-view path");
  });

  await test("A-18/A-19 real ACPClient.jobCommands wiring posts the documented v1 endpoints", async () => {
    const ctx = setup();
    loadScript("js/client/job-commands.js", ctx.sandbox);
    const posts = [];
    const respond = (path, opts) => {
      posts.push({ path, method: opts && opts.method });
      return Promise.resolve({});
    };
    ctx.sandbox.window.ACPClient.jobCommands.init({ v1: respond, v2: respond });
    ctx.client.jobCommands = ctx.sandbox.window.ACPClient.jobCommands;
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-run", status: "running" }),
      jobRow({ id: "job-paused", status: "paused" }),
      jobRow({ id: "job-failed", status: "failed" }),
      jobRow({ id: "job-done", status: "completed" }),
      jobRow({ id: "job-queued", status: "queued" }),
    ] }]));
    const root = ctx.host.children[0];
    const click = (jobId, action) => {
      actionButtons(findByAttr(root, "data-job-id", jobId))
        .find((b) => b.getAttribute("data-acp-action") === action).fire("click");
    };

    click("job-run", "pause");
    await flush();
    click("job-paused", "unpause");
    await flush();
    click("job-failed", "continue");
    await flush();
    click("job-done", "rerun");
    await flush();
    click("job-queued", "cancel");
    await flush();

    assertEq(posts.map((p) => p.method + " " + p.path), [
      "POST /jobs/job-run/pause",
      "POST /jobs/job-paused/unpause",
      "POST /jobs/job-failed/continue",
      "POST /jobs/job-done/rerun",
      "POST /jobs/job-queued/cancel",
    ], "module command keys map onto the frozen job-commands methods/endpoints");
  });

  await test("A-19 command failure surfaces an inline error, skips refresh, and stays retryable", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ]));
    const root = ctx.host.children[0];
    const row = findByAttr(root, "data-job-id", "job-A");
    const pauseBtn = actionButtons(row).find((b) => b.getAttribute("data-acp-action") === "pause");
    const before = ctx.client.taskView.viewCalls.length;

    pauseBtn.fire("click");
    await flush();
    jc.calls[0].deferred.reject(new Error("pause_job requires RUNNING status; got paused"));
    await flush();

    assertEq(row.getAttribute("data-acp-action-state"), "error");
    const msg = messageOf(row);
    assertEq(msg.hidden, false);
    assertEq(msg.getAttribute("data-acp-action-state"), "error");
    assertEq(msg.getAttribute("role"), "alert");
    assertEq(msg.textContent.indexOf("暂停失败"), 0, "error text is prefixed with the zh action label");
    assert(msg.textContent.indexOf("pause_job requires RUNNING status") !== -1,
      "server guidance text is surfaced verbatim");
    assertEq(pauseBtn.disabled, false, "the action is retryable");
    assertEq(ctx.client.taskView.viewCalls.length, before, "a failed command never refreshes the list");

    // Retry: the error clears, the same injected method is called again.
    pauseBtn.fire("click");
    assertEq(jc.calls.length, 2);
    assertEq(row.getAttribute("data-acp-action-state"), "busy");
    assertEq(messageOf(row).getAttribute("data-acp-action-state"), "busy");
  });

  await test("absent client.jobCommands hides every write action — no fallback command client", async () => {
    const ctx = setup(); // client has taskView but no jobCommands
    const stray = makeJobCommandsMock();
    // A page-global command client must never be picked up by the module.
    ctx.sandbox.window.ACPClient = { jobCommands: stray };
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "running" }),
      jobRow({ id: "job-B", status: "failed" }),
    ] }]));
    const root = ctx.host.children[0];

    assertEq(walk(root).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-action")).length, 0,
      "no write action buttons without client.jobCommands");
    ["job-A", "job-B"].forEach((id) => {
      assertEq(findByAttr(root, "data-job-id", id).getAttribute("data-acp-actions"), "unavailable");
    });
    assertEq(stray.calls.length, 0, "module never reaches a separate command client");
    assertEq(ctx.client.taskView.viewCalls.length, 1, "the read path is unchanged (one explicit fetch)");
    // Navigation/refresh controls still render.
    assert(walk(root).some((n) => n.tagName === "BUTTON" && n.textContent === "刷新"));
  });

  await test("a partial client.jobCommands disables only the missing action", async () => {
    const jc = makeJobCommandsMock({ omit: ["cancel"] });
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ]));
    const root = ctx.host.children[0];
    const row = findByAttr(root, "data-job-id", "job-A");
    const pauseBtn = actionButtons(row).find((b) => b.getAttribute("data-acp-action") === "pause");
    const cancelBtn = actionButtons(row).find((b) => b.getAttribute("data-acp-action") === "cancel");

    assertEq(pauseBtn.disabled, false, "implemented action stays enabled");
    assertEq(cancelBtn.disabled, true, "missing action renders disabled");
    assertEq(cancelBtn.getAttribute("data-acp-action-unavailable"), "true");
    assertEq(cancelBtn.getAttribute("title"), "当前客户端不支持该操作");
    cancelBtn.fire("click");
    await flush();
    assertEq(jc.calls.length, 0, "a disabled action never reaches the command client");
  });

  await test("a late command receipt after a project switch writes nothing (generation guard)", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ projects: [{ project_id: "P1", name: "项目一" }], jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ]));
    const root = ctx.host.children[0];
    actionButtons(findByAttr(root, "data-job-id", "job-A"))
      .find((b) => b.getAttribute("data-acp-action") === "pause").fire("click");

    const select = findByClass(root, "acp-mod-tasks-project");
    select.value = "P1";
    select.fire("change");
    await flush();
    const afterSwitch = ctx.client.taskView.viewCalls.length;
    assertEq(afterSwitch, 2, "project switch issues one new list fetch");

    jc.calls[0].deferred.resolve({});
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, afterSwitch,
      "the superseded generation never triggers a refresh for the new project");
    assertEq(walk(root).filter((n) => n.getAttribute
      && n.getAttribute("data-acp-action-state")
      && n.getAttribute("data-acp-action-state") !== "idle").length, 0,
      "no busy/error state leaks into the new project's DOM");
  });

  await test("success refreshes the selected job's summary without re-publishing focus", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([
      { key: "g", jobs: [jobRow({ id: "job-A", status: "running" })] },
    ]));
    const root = ctx.host.children[0];
    findByAttr(root, "data-job-id", "job-A").fire("click");
    await flush();
    ctx.client.taskView.summaryCalls[0].deferred.resolve({
      id: "job-A", status: "running", attempt: 1, project_id: null,
    });
    await flush();
    assertEq(ctx.context.focusCalls.length, 1);

    actionButtons(findByAttr(root, "data-job-id", "job-A"))
      .find((b) => b.getAttribute("data-acp-action") === "pause").fire("click");
    jc.calls[0].deferred.resolve({});
    await flush();
    assertEq(ctx.client.taskView.summaryCalls.length, 2,
      "the selected job's summary is re-fetched through client.taskView");
    assertEq(ctx.context.focusCalls.length, 1, "a command never re-publishes focus");

    ctx.client.taskView.summaryCalls[1].deferred.resolve({
      id: "job-A", status: "paused", attempt: 1, project_id: null,
    });
    await flush();
    const detail = findByClass(root, "acp-mod-tasks-detail");
    const badge = walk(detail)
      .find((n) => String(n.className).indexOf("acp-mod-tasks-badge--paused") !== -1);
    assert(badge, "detail summary reflects the post-command status");
    assertEq(badge.textContent, "已暂停");
  });

  // ---------------------------------------------- A-20 purge (two-step) --

  await test("A-20 per-job purge is a two-step dialog confirmation through client.jobCommands.purge", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", display_name: "ethanol_sp", status: "completed", name_revision: 2 }),
    ] }]));
    const root = ctx.host.children[0];

    // First click only opens the confirmation dialog: nothing is purged yet.
    clickLifecycle(root, "job-A", "purge");
    await flush();
    assertEq(jc.calls.length, 0, "a single click never purges");
    assert(dialogOpen(root), "confirmation dialog opened");
    assertEq(dialogKind(root), "purge");
    assertEq(dialogCard(root).getAttribute("role"), "dialog");
    assertEq(dialogCard(root).getAttribute("aria-modal"), "true");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "确认清除任务");
    const text = findByClass(root, "acp-mod-tasks-modal-text").textContent;
    assert(text.indexOf("ethanol_sp") !== -1, "the dialog names the task being purged");
    assertEq(dialogDeleteData(root).checked, false, "delete_data is opt-in");
    const confirm = dialogConfirm(root);
    assertEq(confirm.disabled, false);
    assertEq(dialogCancel(root).textContent, "取消");

    // Second confirmation = the actual injected command.
    confirm.fire("click");
    assertEq(jc.calls.map((c) => c.name), ["purge"], "exactly one purge command");
    assertEq(jc.calls[0].jobIds, ["job-A"]);
    assertEq(jc.calls[0].deleteData, false);
    assertEq(jc.calls[0].request, { jobIds: ["job-A"], deleteData: false });
    const busyRow = findByAttr(root, "data-job-id", "job-A");
    assertEq(busyRow.getAttribute("data-acp-action-state"), "busy");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    assertEq(dialogConfirm(root).disabled, true);
    const before = ctx.client.taskView.viewCalls.length;
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, before, "nothing refreshes before the receipt");

    // The server's per-job report is the receipt; success refreshes the list
    // through the owned controller (single cache).
    jc.calls[0].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged", error: null }] });
    await flush();
    assert(!dialogOpen(root), "dialog closes after the receipt");
    assert(dialogHost(root).hidden, "dialog host is hidden again");
    assert(ctx.client.taskView.viewCalls.length > before, "success refreshes through the controller");
    assertEq(batchMessage(root).hidden, false);
    assertEq(batchMessage(root).textContent, "已清除 1 个任务");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(reload.prefs.filters.status, [], "refresh keeps the current filter prefs");
    assertEq(reload.projectId, null, "refresh keeps the current project scope");
    reload.deferred.resolve(bodyWith([]));
    await flush();
  });

  await test("A-20 batch purge sends one purge for the selection and renders the server's per-job refusal", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", display_name: "A", status: "completed" }),
      jobRow({ id: "job-B", display_name: "B", status: "running" }),
    ] }]));
    const root = ctx.host.children[0];
    assert(batchBar(root).hidden, "batch bar hidden without a selection");

    // Multi-select rides the module-owned selection map (never a page global).
    rowCheckbox(root, "job-A").checked = true;
    rowCheckbox(root, "job-A").fire("change");
    rowCheckbox(root, "job-B").checked = true;
    rowCheckbox(root, "job-B").fire("change");
    assertEq(batchBar(root).hidden, false);
    assertEq(findByClass(root, "acp-mod-tasks-batch-count").textContent, "已选 2 项");
    assertEq(findByAttr(root, "data-job-id", "job-A").getAttribute("data-acp-selected"), "true");

    // The batch button is still only the FIRST step.
    batchButton(root, "purge").fire("click");
    await flush();
    assertEq(jc.calls.length, 0, "batch purge also waits for the dialog confirmation");
    assertEq(dialogKind(root), "purge");
    assertEq(findByClass(root, "acp-mod-tasks-modal-text").textContent,
      "将清除 2 个任务的记录，此操作不可撤销。");

    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 1, "one purge call covers the whole selection");
    assertEq(jc.calls[0].jobIds, ["job-A", "job-B"]);
    const before = ctx.client.taskView.viewCalls.length;

    // Server is authoritative: one purged, one refused because it is active.
    jc.calls[0].deferred.resolve({ results: [
      { job_id: "job-A", ok: true, action: "purged", error: null },
      { job_id: "job-B", ok: false, action: "skipped_active",
        error: "job is active (status=running); use force_cancel to cancel it first" },
    ] });
    await flush();
    assert(!dialogOpen(root));
    assertEq(batchMessage(root).textContent, "已清除 1 个任务，1 个失败");
    assertEq(batchBar(root).hidden, true, "selection cleared after the report");
    assert(ctx.client.taskView.viewCalls.length > before, "the report refreshes the list");

    // Resolve the refresh so the data-driven error paints on the fresh row.
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", display_name: "A", status: "completed" }),
      jobRow({ id: "job-B", display_name: "B", status: "running" }),
    ] }]));
    await flush();
    const rowB = findByAttr(root, "data-job-id", "job-B");
    assertEq(rowB.getAttribute("data-acp-action-state"), "error");
    const msg = messageOf(rowB);
    assertEq(msg.getAttribute("data-acp-action-state"), "error");
    assertEq(msg.getAttribute("role"), "alert");
    assert(msg.textContent.indexOf("job is active (status=running)") !== -1,
      "the server's terminal/active refusal text is rendered verbatim");
  });

  await test("A-20 purge forwards delete_data only when the confirmation checkbox is checked", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];

    clickLifecycle(root, "job-A", "purge");
    const box = dialogDeleteData(root);
    box.checked = true;
    box.fire("change");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 1);
    assertEq(jc.calls[0].deleteData, true);
    assertEq(jc.calls[0].jobIds, ["job-A"]);
    jc.calls[0].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged" }] });
    await flush();
    assert(!dialogOpen(root));
  });

  await test("A-20 purge failure keeps the dialog open, skips refresh, and stays retryable", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];
    const before = ctx.client.taskView.viewCalls.length;

    clickLifecycle(root, "job-A", "purge");
    dialogConfirm(root).fire("click");
    jc.calls[0].deferred.reject({ status: 500, body: { detail: "purge_cascade unavailable" } });
    await flush();

    assert(dialogOpen(root), "the confirmation dialog stays open for an explicit retry");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "error");
    const err = dialogErrorEl(root);
    assert(err, "dialog error line rendered");
    assertEq(err.getAttribute("role"), "alert");
    assertEq(err.textContent.indexOf("清除失败"), 0);
    assert(err.textContent.indexOf("purge_cascade unavailable") !== -1,
      "the server error text is surfaced verbatim");
    assertEq(ctx.client.taskView.viewCalls.length, before, "a failed purge never refreshes the list");
    const row = findByAttr(root, "data-job-id", "job-A");
    assertEq(row.getAttribute("data-acp-action-state"), "error");
    assert(messageOf(row).textContent.indexOf("purge_cascade unavailable") !== -1);
    assertEq(dialogConfirm(root).disabled, false, "the confirmation is retryable");

    // Retry from the still-open dialog.
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 2);
    assertEq(jc.calls[1].jobIds, ["job-A"]);
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    jc.calls[1].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged" }] });
    await flush();
    assert(!dialogOpen(root), "a successful retry closes the dialog");
    assert(ctx.client.taskView.viewCalls.length > before);
  });

  // ------------------------------------- A-21 clone / move / rename --

  await test("A-21 clone and move pick a target from the loaded project list and refresh", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({
      projects: [{ project_id: "P1", name: "项目一" }, { project_id: "P2", name: "项目二" }],
      jobCommands: jc,
    });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];

    clickLifecycle(root, "job-A", "clone");
    assertEq(dialogKind(root), "clone");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "复制任务到项目");
    const select = dialogProjectSelect(root);
    assert(select, "project select rendered");
    assertEq(select.children.map((o) => o.getAttribute("value")), ["P1", "P2"],
      "targets come from the existing GET /projects list (no constructed path)");
    assertEq(select.value, "P1");
    const beforeClone = ctx.client.taskView.viewCalls.length;
    select.value = "P2";
    select.fire("change");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[0].name, "clone");
    assertEq(jc.calls[0].jobId, "job-A");
    assertEq(jc.calls[0].projectId, "P2");
    assertEq(findByAttr(root, "data-job-id", "job-A").getAttribute("data-acp-action-state"), "busy");
    jc.calls[0].deferred.resolve({ id: "job-clone" });
    await flush();
    assert(!dialogOpen(root), "clone closes the dialog on success");
    assert(ctx.client.taskView.viewCalls.length > beforeClone,
      "clone success refreshes through the owned controller");

    clickLifecycle(root, "job-A", "move");
    assertEq(dialogKind(root), "move");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "移动任务到项目");
    const beforeMove = ctx.client.taskView.viewCalls.length;
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[1].name, "move");
    assertEq(jc.calls[1].jobId, "job-A");
    assertEq(jc.calls[1].projectId, "P1");
    jc.calls[1].deferred.resolve({});
    await flush();
    assert(!dialogOpen(root));
    assert(ctx.client.taskView.viewCalls.length > beforeMove,
      "move success refreshes through the owned controller");
  });

  await test("A-21 rename sends the row name_revision and can restore the default name", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", display_name: "ethanol_sp", status: "completed",
        custom_name: "", name_revision: 3 }),
    ] }]));
    const root = ctx.host.children[0];

    clickLifecycle(root, "job-A", "rename");
    assertEq(dialogKind(root), "rename");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "重命名任务");
    const input = dialogInput(root);
    assert(input, "rename input rendered");
    assertEq(input.value, "", "an empty custom_name prefills an empty input");
    assertEq(dialogConfirm(root).disabled, true, "an unchanged name is not saveable");

    input.value = "  新名字  ";
    input.fire("input");
    assertEq(dialogConfirm(root).disabled, false);
    const before = ctx.client.taskView.viewCalls.length;
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[0].name, "rename");
    assertEq(jc.calls[0].jobId, "job-A");
    assertEq(jc.calls[0].customName, "新名字", "the name is trimmed");
    assertEq(jc.calls[0].revision, 3,
      "expected_name_revision comes from the task-view row's name_revision");
    jc.calls[0].deferred.resolve({ custom_name: "新名字", name_revision: 4, resolved_name: "新名字" });
    await flush();
    assert(!dialogOpen(root), "rename closes the dialog on success");
    assert(ctx.client.taskView.viewCalls.length > before);
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", display_name: "新名字", status: "completed",
        custom_name: "新名字", name_revision: 4 }),
    ] }]));
    await flush();

    // Empty input on a row that HAS a custom name = restore the default name
    // (custom_name null), exactly like the legacy rename dialog.
    clickLifecycle(root, "job-A", "rename");
    const input2 = dialogInput(root);
    assertEq(input2.value, "新名字");
    input2.value = "";
    input2.fire("input");
    assertEq(dialogConfirm(root).disabled, false, "clearing a custom name restores the default");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[1].customName, null);
    assertEq(jc.calls[1].revision, 4, "the refreshed name revision is used");
    jc.calls[1].deferred.resolve({ custom_name: null, name_revision: 5 });
    await flush();
  });

  await test("A-21 rename conflict adopts the server revision and retries from the dialog", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", custom_name: "old", name_revision: 2 }),
    ] }]));
    const root = ctx.host.children[0];

    clickLifecycle(root, "job-A", "rename");
    const input = dialogInput(root);
    input.value = "next";
    input.fire("input");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[0].revision, 2);
    const before = ctx.client.taskView.viewCalls.length;
    jc.calls[0].deferred.reject({ status: 409, body: { detail: {
      error: "name_revision_conflict",
      current: { name_revision: 5, custom_name: "other" },
    } } });
    await flush();
    assert(dialogOpen(root), "a conflict keeps the dialog open for retry");
    const err = dialogErrorEl(root);
    assert(err, "conflict error line rendered");
    assert(err.textContent.indexOf("名称已被修改") !== -1);
    assert(ctx.client.taskView.viewCalls.length > before,
      "a conflict re-reads the list through the owned controller");

    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", custom_name: "other", name_revision: 5 }),
    ] }]));
    await flush();

    // Retry without editing: the revision is the fresh/server one.
    dialogConfirm(root).fire("click");
    assertEq(jc.calls[1].name, "rename");
    assertEq(jc.calls[1].customName, "next");
    assertEq(jc.calls[1].revision, 5, "retry uses the revision the server returned");
    jc.calls[1].deferred.resolve({ custom_name: "next", name_revision: 6 });
    await flush();
    assert(!dialogOpen(root), "the retried rename closes the dialog");
  });

  // ------------------------------ A-20/A-21 client availability + guards --

  await test("A-20/A-21 absent client.jobCommands hides lifecycle actions and batch selection", async () => {
    const ctx = setup(); // client has taskView but no jobCommands
    // A page-global command client must never be picked up by the module.
    ctx.sandbox.window.ACPClient = { jobCommands: makeJobCommandsMock() };
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", name_revision: 1 }),
    ] }]));
    const root = ctx.host.children[0];
    assertEq(walk(root).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-lifecycle")).length, 0,
      "no purge/clone/move/rename buttons without client.jobCommands");
    assertEq(walk(root).filter((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-select")).length, 0,
      "no batch selection surface without client.jobCommands");
    assert(batchBar(root).hidden);
    assertEq(findByAttr(root, "data-job-id", "job-A").getAttribute("data-acp-actions"), "unavailable");
    assertEq(ctx.client.taskView.viewCalls.length, 1, "the read path is unchanged");
  });

  await test("A-20/A-21 a partial client disables only the missing lifecycle action; rename needs a revision", async () => {
    const jc = makeJobCommandsMock({ omit: ["purge", "rename"] });
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", name_revision: 2 }),
    ] }]));
    const root = ctx.host.children[0];
    const rowA = findByAttr(root, "data-job-id", "job-A");
    assertEq(lifecycleButton(rowA, "purge").disabled, true, "missing purge is disabled");
    assertEq(lifecycleButton(rowA, "rename").disabled, true, "missing rename is disabled");
    assertEq(lifecycleButton(rowA, "clone").disabled, false, "present clone stays enabled");
    assertEq(lifecycleButton(rowA, "move").disabled, false, "present move stays enabled");
    assertEq(lifecycleButton(rowA, "purge").getAttribute("title"), "当前客户端不支持该操作");
    assertEq(walk(root).filter((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-select")).length, 0,
      "batch purge disappears with its missing command");
    // A disabled purge never opens a confirmation dialog.
    lifecycleButton(rowA, "purge").fire("click");
    await flush();
    assert(!dialogOpen(root));
    assertEq(jc.calls.length, 0);

    // Full client, row without name_revision: ONLY rename is disabled.
    const ctxFull = setup({ jobCommands: makeJobCommandsMock() });
    await mountReady(ctxFull, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-N", status: "completed" }),
    ] }]));
    const rootFull = ctxFull.host.children[0];
    const rowN = findByAttr(rootFull, "data-job-id", "job-N");
    const renameN = lifecycleButton(rowN, "rename");
    assertEq(renameN.disabled, true, "rename needs the name revision");
    assertEq(renameN.getAttribute("data-acp-action-unavailable"), "true");
    assertEq(renameN.getAttribute("title"), "缺少名称版本（name_revision），无法重命名");
    assertEq(lifecycleButton(rowN, "purge").disabled, false);
    assertEq(lifecycleButton(rowN, "clone").disabled, false);
    assertEq(lifecycleButton(rowN, "move").disabled, false);
  });

  await test("A-20/A-21 one row never receives a second concurrent command", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", name_revision: 2 }),
    ] }]));
    const root = ctx.host.children[0];

    // A double confirm cannot issue a second purge.
    clickLifecycle(root, "job-A", "purge");
    const confirm = dialogConfirm(root);
    confirm.fire("click");
    confirm.fire("click");
    assertEq(jc.calls.length, 1, "the busy dialog refuses a second purge");
    jc.calls[0].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged" }] });
    await flush();
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "running", name_revision: 2 }),
    ] }]));
    await flush();

    // While pause is in flight, the lifecycle surface refuses to open.
    actionButtons(findByAttr(root, "data-job-id", "job-A"))
      .find((b) => b.getAttribute("data-acp-action") === "pause").fire("click");
    assertEq(jc.calls[1].name, "pause");
    lifecycleButton(findByAttr(root, "data-job-id", "job-A"), "rename").fire("click");
    lifecycleButton(findByAttr(root, "data-job-id", "job-A"), "purge").fire("click");
    await flush();
    assert(!dialogOpen(root), "a busy row never opens a second command surface");
    assertEq(jc.calls.length, 2, "no extra command from the refused clicks");
    jc.calls[1].deferred.resolve({});
    await flush();

    // A dialog that was already open cannot start a command on a row that
    // became busy in the meantime; once the row is free the dialog retries.
    const jc2 = makeJobCommandsMock();
    const ctx2 = setup({
      projects: [{ project_id: "P1", name: "项目一" }],
      jobCommands: jc2,
    });
    await mountReady(ctx2, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-C", status: "running", custom_name: "", name_revision: 1 }),
    ] }]));
    const root2 = ctx2.host.children[0];
    clickLifecycle(root2, "job-C", "clone");
    actionButtons(findByAttr(root2, "data-job-id", "job-C"))
      .find((b) => b.getAttribute("data-acp-action") === "pause").fire("click");
    assertEq(jc2.calls[0].name, "pause");
    dialogConfirm(root2).fire("click");
    assertEq(jc2.calls.length, 1, "the dialog cannot start a command while the row is busy");
    assert(dialogOpen(root2), "the dialog stays open for a retry");
    assert(dialogErrorEl(root2).textContent.indexOf("该任务已有命令在执行") !== -1);
    jc2.calls[0].deferred.resolve({});
    await flush();
    dialogConfirm(root2).fire("click");
    assertEq(jc2.calls[1].name, "clone", "the retry runs once the row is free");
    assertEq(jc2.calls[1].projectId, "P1");
    jc2.calls[1].deferred.resolve({});
    await flush();
  });

  await test("A-20/A-21 dispose while a command is in flight writes nothing and leaves no listener residue", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];
    clickLifecycle(root, "job-A", "purge");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 1);
    const before = ctx.client.taskView.viewCalls.length;

    ctx.instance.dispose();
    await flush();
    assertEq(ctx.host.children.length, 0, "host DOM cleared");
    assertEq(ctx.dom.counters.added, ctx.dom.counters.removed,
      "every dialog/row listener was removed on dispose");
    jc.calls[0].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged" }] });
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, before,
      "a late purge receipt after dispose never refreshes");

    // A late rejection after dispose is equally inert (no rethrow).
    const jc2 = makeJobCommandsMock();
    const ctx2 = setup({ jobCommands: jc2 });
    await mountReady(ctx2, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-B", status: "completed", custom_name: "old", name_revision: 1 }),
    ] }]));
    const root2 = ctx2.host.children[0];
    clickLifecycle(root2, "job-B", "rename");
    const input = dialogInput(root2);
    input.value = "next";
    input.fire("input");
    dialogConfirm(root2).fire("click");
    assertEq(jc2.calls.length, 1);
    ctx2.instance.dispose();
    await flush();
    jc2.calls[0].deferred.reject({ status: 500, body: { detail: "late failure" } });
    await flush();
    assertEq(ctx2.dom.counters.added, ctx2.dom.counters.removed,
      "late rejection writes no dialog/error DOM after dispose");
  });

  await test("A-20 a late purge receipt after a project switch writes nothing", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ projects: [{ project_id: "P1", name: "项目一" }], jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];
    clickLifecycle(root, "job-A", "purge");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 1);

    const select = findByClass(root, "acp-mod-tasks-project");
    select.value = "P1";
    select.fire("change");
    await flush();
    assert(!dialogOpen(root), "a project switch closes the dialog");
    const afterSwitch = ctx.client.taskView.viewCalls.length;
    assertEq(afterSwitch, 2, "the switch issues exactly one new list fetch");

    jc.calls[0].deferred.resolve({ results: [{ job_id: "job-A", ok: true, action: "purged" }] });
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, afterSwitch,
      "the superseded generation never refreshes the new project");
    assertEq(batchBar(root).hidden, true, "no selection/batch bar leaks into the new project");
    assertEq(walk(root).filter((n) => n.getAttribute
      && n.getAttribute("data-acp-action-state")
      && n.getAttribute("data-acp-action-state") !== "idle").length, 0,
      "no busy/error state leaks into the new project's DOM");
  });

  await test("A-20/A-21 real ACPClient.jobCommands wiring posts purge/clone/move/rename bodies", async () => {
    const ctx = setup({
      projects: [{ project_id: "P1", name: "项目一" }, { project_id: "P2", name: "项目二" }],
    });
    loadScript("js/client/job-commands.js", ctx.sandbox);
    const calls = [];
    const respond = (path, opts) => {
      calls.push({
        path,
        method: opts && opts.method,
        body: opts && opts.body ? JSON.parse(opts.body) : null,
      });
      return Promise.resolve({ results: [] });
    };
    ctx.sandbox.window.ACPClient.jobCommands.init({ v1: respond, v2: respond });
    ctx.client.jobCommands = ctx.sandbox.window.ACPClient.jobCommands;
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", custom_name: "", name_revision: 7 }),
    ] }]));
    const root = ctx.host.children[0];

    clickLifecycle(root, "job-A", "purge");
    dialogConfirm(root).fire("click");
    await flush();
    assertEq(calls[0], {
      path: "/jobs/purge",
      method: "POST",
      body: { job_ids: ["job-A"], delete_data: false },
    }, "purge goes through client.jobCommands.purge → POST /jobs/purge");

    clickLifecycle(root, "job-A", "rename");
    const input = dialogInput(root);
    input.value = "renamed";
    input.fire("input");
    dialogConfirm(root).fire("click");
    await flush();
    assertEq(calls[1], {
      path: "/tasks/job-A",
      method: "PATCH",
      body: { custom_name: "renamed", expected_name_revision: 7 },
    }, "rename carries expected_name_revision through the v2 PATCH body");

    clickLifecycle(root, "job-A", "clone");
    dialogConfirm(root).fire("click");
    await flush();
    assertEq(calls[2], {
      path: "/jobs/job-A/clone",
      method: "POST",
      body: { project_id: "P1" },
    });

    clickLifecycle(root, "job-A", "move");
    dialogConfirm(root).fire("click");
    await flush();
    assertEq(calls[3], {
      path: "/jobs/job-A/move",
      method: "POST",
      body: { project_id: "P1" },
    });

    assertEq(calls.filter((c) => c.method === "DELETE").length, 0,
      "the module never issues DELETE directly");
    assertEq(calls.length, 4, "exactly one command per confirmed action");
  });

  // ------------------------------------------------- A-22 project CRUD --

  await test("A-22 project manager lists the loaded projects and creates one through POST /projects", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];

    const manage = manageProjectsButton(root);
    assert(manage, "project management entry rendered in the toolbar");
    assertEq(manage.disabled, false, "a request transport enables project management");
    manage.fire("click");
    await flush();
    assert(dialogOpen(root), "the project manager dialog opened");
    assertEq(dialogKind(root), "projects");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "项目管理");
    assertEq(projectRows(root).map((r) => r.getAttribute("data-acp-project-row")), ["P1"],
      "rows come from the SAME already-loaded GET /projects list");
    assertEq(walk(projectRow(root, "P1")).find(
      (n) => n.className === "acp-mod-tasks-project-name").textContent, "项目一");
    assertEq(projectCreateButton(root).disabled, true, "an empty name cannot be created");
    assertEq(dialogConfirm(root), null, "the manager list has no generic confirm");

    const input = projectCreateInput(root);
    input.value = "新项目";
    input.fire("input");
    assertEq(projectCreateButton(root).disabled, false);
    projectCreateButton(root).fire("click");
    const posts = requestsOf(ctx, "POST", "/projects");
    assertEq(posts.length, 1, "exactly one create request");
    assertEq(posts[0].body, { name: "新项目" });
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    projectCreateButton(root).fire("click");
    assertEq(requestsOf(ctx, "POST", "/projects").length, 1,
      "a busy dialog never issues a second create");

    // Server created P2 → the next GET /projects returns it.
    ctx.client.projectsData.push({ project_id: "P2", name: "新项目" });
    posts[0].deferred.resolve({ project_id: "P2", name: "新项目" });
    await flush();

    assert(!dialogOpen(root), "create closes the manager and switches scope");
    const select = findByClass(root, "acp-mod-tasks-project");
    assertEq(select.children.map((o) => o.getAttribute("value")), ["", "P1", "P2"],
      "the project selector was re-read from GET /projects");
    assertEq(select.value, "P2");
    assertEq(ctx.context.focusCalls[ctx.context.focusCalls.length - 1],
      { patch: { projectId: "P2" }, source: "task-explorer" });
    const lastView = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(lastView.projectId, "P2",
      "the task list refreshes through the owned list controller for the new scope");
  });

  await test("A-22 closing the project dialog mid-flight still re-reads the selector without switching scope", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    manageProjectsButton(root).fire("click");
    await flush();
    const input = projectCreateInput(root);
    input.value = "新项目";
    input.fire("input");
    projectCreateButton(root).fire("click");
    const posts = requestsOf(ctx, "POST", "/projects");
    assertEq(posts.length, 1);

    dialogCancel(root).fire("click");
    assert(!dialogOpen(root), "cancel closes the manager immediately");
    ctx.client.projectsData.push({ project_id: "P2", name: "新项目" });
    posts[0].deferred.resolve({ project_id: "P2", name: "新项目" });
    await flush();

    assert(!dialogOpen(root), "a late create success never resurrects a closed dialog");
    const select = findByClass(root, "acp-mod-tasks-project");
    assertEq(select.children.map((o) => o.getAttribute("value")), ["", "P1", "P2"],
      "the selector still re-reads the created project");
    assertEq(select.value, "", "no implicit scope jump after the dialog was closed");
    assertEq(ctx.client.taskView.viewCalls.length, 1, "no task refetch without a scope change");
  });

  await test("A-22 rename uses PATCH /projects/{id} and re-reads the selector without refetching tasks", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    const viewCallsBefore = ctx.client.taskView.viewCalls.length;

    manageProjectsButton(root).fire("click");
    await flush();
    projectRowButton(projectRow(root, "P1"), "data-acp-project-rename", "P1").fire("click");
    await flush();
    assertEq(dialogKind(root), "project-rename");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "重命名项目");
    const input = projectRenameInput(root);
    assertEq(input.value, "项目一", "the current project name prefills the input");
    assertEq(dialogConfirm(root).disabled, true, "an unchanged name is not saveable");

    input.value = "项目甲";
    input.fire("input");
    assertEq(dialogConfirm(root).disabled, false);
    dialogConfirm(root).fire("click");
    const patches = requestsOf(ctx, "PATCH", "/projects/P1");
    assertEq(patches.length, 1);
    assertEq(patches[0].body, { name: "项目甲" });
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");

    ctx.client.projectsData[0] = { project_id: "P1", name: "项目甲" };
    patches[0].deferred.resolve({ project_id: "P1", name: "项目甲" });
    await flush();

    assertEq(dialogKind(root), "projects", "rename returns to the manager list");
    assert(dialogNotice(root), "success notice rendered");
    assert(dialogNotice(root).textContent.indexOf("已重命名项目") !== -1);
    assertEq(projectRows(root).length, 1);
    const select = findByClass(root, "acp-mod-tasks-project");
    assertEq(select.children[1].textContent, "项目甲",
      "the selector reflects the re-read GET /projects data");
    assertEq(ctx.client.taskView.viewCalls.length, viewCallsBefore,
      "renaming a project does not refetch the task list");
  });

  await test("A-22 delete shows the server dependency count, then deletes via DELETE after confirmation", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }, { project_id: "P2", name: "项目二" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-A" })] }]));
    const root = ctx.host.children[0];

    // Scope to P1 so the delete exercises the "current project disappeared" path.
    const select = findByClass(root, "acp-mod-tasks-project");
    select.value = "P1";
    select.fire("change");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(
      bodyWith([{ key: "g", jobs: [jobRow({ id: "job-A", project_id: "P1" })] }],
        { query: { project_id: "P1" } }));
    await flush();

    manageProjectsButton(root).fire("click");
    await flush();
    projectRowButton(projectRow(root, "P1"), "data-acp-project-delete", "P1").fire("click");
    await flush();
    assertEq(dialogKind(root), "project-delete");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "删除项目");
    const depsCalls = requestsMatching(ctx, "GET", "/projects/P1/jobs");
    assertEq(depsCalls.length, 1, "the dependency count comes from the existing project jobs endpoint");
    assertEq(depsCalls[0].path, "/projects/P1/jobs?limit=1000");
    assertEq(projectDeps(root).getAttribute("data-acp-project-deps"), "loading");
    assert(findByClass(root, "acp-mod-tasks-modal-warning").textContent.indexOf("移动到默认项目") !== -1,
      "the delete warning explains the non-destructive server semantics");

    depsCalls[0].deferred.resolve({ jobs: [
      { id: "j1", status: "completed" },
      { id: "j2", status: "running" },
      { id: "j3", status: "paused" },
    ], counts: { completed: 1, running: 1, paused: 1 } });
    await flush();
    assertEq(projectDeps(root).getAttribute("data-acp-project-deps"), "ready");
    assertEq(projectDeps(root).textContent, "该项目包含 3 个任务，其中 2 个进行中。",
      "the server dependency count is shown before the second confirmation");
    assertEq(requestsOf(ctx, "DELETE", "/projects/P1").length, 0,
      "opening the dialog never deletes anything");

    dialogConfirm(root).fire("click");
    const deletes = requestsOf(ctx, "DELETE", "/projects/P1");
    assertEq(deletes.length, 1, "the second confirmation issues the DELETE");
    assertEq(deletes[0].body, null, "delete carries no JSON body");
    ctx.client.projectsData.splice(0, 1); // server removed P1
    deletes[0].deferred.resolve({ deleted: true, project_id: "P1" });
    await flush();

    assertEq(dialogKind(root), "projects", "delete returns to the manager list");
    assert(dialogNotice(root).textContent.indexOf("已删除项目") !== -1);
    const selectAfter = findByClass(root, "acp-mod-tasks-project");
    assertEq(selectAfter.children.map((o) => o.getAttribute("value")), ["", "P2"],
      "the selector was refreshed through the owned project list path");
    assertEq(ctx.context.focusCalls[ctx.context.focusCalls.length - 1],
      { patch: { projectId: null }, source: "task-explorer" },
      "a deleted current scope falls back to all projects");
    const lastView = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(lastView.projectId, null,
      "the task list refreshes through the owned list controller after the delete");
  });

  await test("A-22 delete refusal is shown inline, skips the refresh, and stays retryable", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    const before = ctx.client.taskView.viewCalls.length;

    manageProjectsButton(root).fire("click");
    await flush();
    projectRowButton(projectRow(root, "P1"), "data-acp-project-delete", "P1").fire("click");
    await flush();
    requestsMatching(ctx, "GET", "/projects/P1/jobs")[0].deferred.resolve({ jobs: [] });
    await flush();
    assertEq(projectDeps(root).textContent, "该项目包含 0 个任务。");

    dialogConfirm(root).fire("click");
    const deletes = requestsOf(ctx, "DELETE", "/projects/P1");
    deletes[0].deferred.reject({ status: 400, body: {
      detail: "Cannot delete project with active jobs: ['job-1']",
    } });
    await flush();

    assert(dialogOpen(root), "a refused delete keeps the dialog open for a retry");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "error");
    const err = dialogErrorEl(root);
    assert(err, "server refusal rendered inline");
    assertEq(err.getAttribute("role"), "alert");
    assertEq(err.textContent.indexOf("删除项目失败"), 0);
    assert(err.textContent.indexOf("Cannot delete project with active jobs") !== -1,
      "the server dependency refusal text is surfaced verbatim");
    assertEq(ctx.client.taskView.viewCalls.length, before, "a failed delete never refreshes the list");
    assertEq(dialogConfirm(root).disabled, false, "the delete is retryable");

    dialogConfirm(root).fire("click");
    assertEq(requestsOf(ctx, "DELETE", "/projects/P1").length, 2);
    requestsOf(ctx, "DELETE", "/projects/P1")[1].deferred.resolve({ deleted: true });
    await flush();
    assertEq(dialogKind(root), "projects");
  });

  await test("A-22 absent request transport disables project management without a dead dialog", async () => {
    const client = makeClient([], null, { omitRequest: true });
    const ctx = setup({ client });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    const manage = manageProjectsButton(root);
    assert(manage, "the entry stays visible (a silent missing entry is worse)");
    assertEq(manage.disabled, true);
    assertEq(manage.getAttribute("data-acp-project-unavailable"), "true");
    assertEq(manage.getAttribute("title"), "当前客户端不支持项目管理");
    manage.fire("click");
    await flush();
    assert(!dialogOpen(root), "no project dialog without a request transport");
    assertEq(ctx.client.requests.length, 0);
  });

  await test("A-22 real ACPClient wiring uses transport.request on the documented /api/v1 project paths", async () => {
    const transportCalls = [];
    const transport = {
      request(path, opts) {
        const method = (opts && opts.method) || "GET";
        transportCalls.push({ path, method });
        if (method === "GET" && path === "/api/v1/projects") {
          return Promise.resolve({ projects: [] });
        }
        if (method === "POST") return Promise.resolve({ project_id: "P9", name: "新项目" });
        return Promise.resolve({});
      },
    };
    const client = makeClient([], null, { omitRequest: true, transport });
    const ctx = setup({ client });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    assertEq(transportCalls[0], { path: "/api/v1/projects", method: "GET" },
      "the list keeps using the existing /api/v1/projects endpoint");

    manageProjectsButton(root).fire("click");
    await flush();
    const input = projectCreateInput(root);
    input.value = "新项目";
    input.fire("input");
    projectCreateButton(root).fire("click");
    await flush();
    const post = transportCalls.find((c) => c.method === "POST");
    assert(post, "create issues a POST through the injected transport");
    assertEq(post.path, "/api/v1/projects",
      "create posts to the same existing endpoint (no constructed path)");
    const lastView = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(lastView.projectId, "P9", "the created project id from the server drives the scope");
  });

  await test("A-22 a late project mutation after dispose writes nothing and leaves no listener residue", async () => {
    const projects = [{ project_id: "P1", name: "项目一" }];
    const ctx = setup({ projects, clientOptions: { onRequest: deferProjectMutations } });
    await mountReady(ctx, EMPTY_BODY);
    const root = ctx.host.children[0];
    manageProjectsButton(root).fire("click");
    await flush();
    const input = projectCreateInput(root);
    input.value = "未完成";
    input.fire("input");
    projectCreateButton(root).fire("click");
    const posts = requestsOf(ctx, "POST", "/projects");
    assertEq(posts.length, 1);
    const viewBefore = ctx.client.taskView.viewCalls.length;

    ctx.instance.dispose();
    await flush();
    assertEq(ctx.host.children.length, 0, "host DOM cleared");
    assertEq(ctx.dom.counters.added, ctx.dom.counters.removed,
      "every project dialog listener was removed on dispose");
    posts[0].deferred.resolve({ project_id: "P2", name: "未完成" });
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, viewBefore,
      "a late create receipt after dispose never refreshes the list");
  });

  // -------------------------------------------------- A-23 tag management --

  await test("A-23 tag dialog shows the current tags and adds one through client.jobCommands.batchOp", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["alpha", "beta"] }),
    ] }]));
    const root = ctx.host.children[0];
    const row = findByAttr(root, "data-job-id", "job-A");
    assertEq(rowTagChips(row).map((c) => c.getAttribute("data-acp-tag")), ["alpha", "beta"],
      "task-view tags render verbatim as labels");
    const btn = tagsButton(row, "job-A");
    assert(btn, "tag management button rendered");
    assertEq(btn.disabled, false);

    btn.fire("click");
    await flush();
    assertEq(dialogKind(root), "tags");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "管理标签");
    assertEq(dialogTagChips(root).map((c) => c.getAttribute("data-acp-tag-chip")), ["alpha", "beta"]);
    assertEq(dialogTagAdd(root).disabled, true, "an empty tag input cannot add");
    assertEq(dialogConfirm(root), null, "the tag editor has no generic confirm");

    const input = dialogTagInput(root);
    input.value = " gamma ";
    input.fire("input");
    assertEq(dialogTagAdd(root).disabled, false);
    dialogTagAdd(root).fire("click");
    const call = jc.calls[0];
    assertEq(call.name, "batchOp", "tags go through the existing batch-ops command");
    assertEq(call.request, { taskIds: ["job-A"], op: "add_tags", payload: { tags: ["gamma"] } },
      "the tag is trimmed and sent as a label value only");
    const busyRow = findByAttr(root, "data-job-id", "job-A");
    assertEq(busyRow.getAttribute("data-acp-action-state"), "busy");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    const before = ctx.client.taskView.viewCalls.length;
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, before, "nothing refreshes before the receipt");

    call.deferred.resolve({ results: [{ task_id: "job-A", ok: true }], updated: 1 });
    await flush();
    assertEq(dialogTagChips(root).map((c) => c.getAttribute("data-acp-tag-chip")),
      ["alpha", "beta", "gamma"], "the server-confirmed tag appears in the editor");
    assert(ctx.client.taskView.viewCalls.length > before,
      "success refreshes through the task-view/store path");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["alpha", "beta", "gamma"] }),
    ] }]));
    await flush();
    assertEq(rowTagChips(findByAttr(root, "data-job-id", "job-A")).map((c) => c.getAttribute("data-acp-tag")),
      ["alpha", "beta", "gamma"], "row chips follow the re-read task-view row");
  });

  await test("A-23 remove uses the remove_tags op and updates the row after the refresh", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["alpha", "beta"] }),
    ] }]));
    const root = ctx.host.children[0];
    tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A").fire("click");
    await flush();

    const remove = dialogTagRemove(root, "alpha");
    assert(remove, "each tag chip has a remove control");
    assertEq(remove.getAttribute("aria-label"), "移除标签 alpha");
    remove.fire("click");
    const call = jc.calls[0];
    assertEq(call.request, { taskIds: ["job-A"], op: "remove_tags", payload: { tags: ["alpha"] } });
    const before = ctx.client.taskView.viewCalls.length;
    call.deferred.resolve({ results: [{ task_id: "job-A", ok: true }], updated: 1 });
    await flush();
    assertEq(dialogTagChips(root).map((c) => c.getAttribute("data-acp-tag-chip")), ["beta"]);
    assert(ctx.client.taskView.viewCalls.length > before);
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    reload.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["beta"] }),
    ] }]));
    await flush();
    assertEq(rowTagChips(findByAttr(root, "data-job-id", "job-A")).map((c) => c.getAttribute("data-acp-tag")),
      ["beta"]);
  });

  await test("A-23 a server per-task tag refusal is shown inline, skips the refresh, and stays retryable", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: [] }),
    ] }]));
    const root = ctx.host.children[0];
    tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A").fire("click");
    await flush();
    const input = dialogTagInput(root);
    input.value = "gamma";
    input.fire("input");
    dialogTagAdd(root).fire("click");
    const before = ctx.client.taskView.viewCalls.length;
    jc.calls[0].deferred.resolve({ results: [{
      task_id: "job-A", ok: false, error: "tags must contain at most 20 items",
    }], updated: 0 });
    await flush();

    assert(dialogOpen(root), "a per-task refusal is not a dialog close");
    const err = dialogErrorEl(root);
    assert(err, "server refusal rendered inline in the editor");
    assertEq(err.getAttribute("role"), "alert");
    assertEq(err.textContent.indexOf("更新标签失败"), 0);
    assert(err.textContent.indexOf("tags must contain at most 20 items") !== -1,
      "the server tag rule text is surfaced verbatim");
    assertEq(ctx.client.taskView.viewCalls.length, before, "a refused tag op never refreshes the list");
    const row = findByAttr(root, "data-job-id", "job-A");
    assertEq(row.getAttribute("data-acp-action-state"), "error");
    assert(messageOf(row).textContent.indexOf("tags must contain at most 20 items") !== -1);
    assertEq(dialogTagAdd(root).disabled, false, "the add is retryable");

    dialogTagAdd(root).fire("click");
    assertEq(jc.calls.length, 2, "the retry issues exactly one new command");
    assert(jc.calls[1].request);
    assertEq(jc.calls[1].request.payload, { tags: ["gamma"] });
  });

  await test("A-23 a transport rejection keeps the dialog open with the server error text", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: [] }),
    ] }]));
    const root = ctx.host.children[0];
    tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A").fire("click");
    await flush();
    const input = dialogTagInput(root);
    input.value = "x";
    input.fire("input");
    dialogTagAdd(root).fire("click");
    const before = ctx.client.taskView.viewCalls.length;
    jc.calls[0].deferred.reject({ status: 422, body: { detail: "Unsupported op: add_tags" } });
    await flush();

    assertEq(dialogKind(root), "tags", "the tag editor stays open");
    const err = dialogErrorEl(root);
    assert(err && err.textContent.indexOf("Unsupported op: add_tags") !== -1,
      "the transport error detail is surfaced verbatim");
    assertEq(ctx.client.taskView.viewCalls.length, before, "a failed tag op never refreshes the list");
    assertEq(dialogTagInput(root).value, "x", "the pending input survives the failure");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "error");
  });

  await test("A-23 absent jobCommands hides tags; a missing batchOp disables only the tag editor", async () => {
    // No command client at all: no tag editor (the chips stay readable).
    const noClient = setup();
    await mountReady(noClient, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["alpha"] }),
    ] }]));
    const rootNo = noClient.host.children[0];
    const rowNo = findByAttr(rootNo, "data-job-id", "job-A");
    assertEq(walk(rootNo).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-tags")).length, 0,
      "no tag write surface without client.jobCommands");
    assertEq(rowTagChips(rowNo).map((c) => c.getAttribute("data-acp-tag")), ["alpha"],
      "tags remain visible labels even without the command client");

    // Command client without batchOp: only the tag button is disabled.
    const jc = makeJobCommandsMock({ omit: ["batchOp"] });
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: ["alpha"] }),
    ] }]));
    const root = ctx.host.children[0];
    const btn = tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A");
    assert(btn, "the tag entry renders disabled instead of disappearing");
    assertEq(btn.disabled, true);
    assertEq(btn.getAttribute("data-acp-action-unavailable"), "true");
    assertEq(btn.getAttribute("title"), "当前客户端不支持标签管理");
    btn.fire("click");
    await flush();
    assert(!dialogOpen(root));
    assertEq(jc.calls.length, 0);
  });

  await test("A-23 a late tag receipt after a project switch writes nothing", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ projects: [{ project_id: "P1", name: "项目一" }], jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: [] }),
    ] }]));
    const root = ctx.host.children[0];
    tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A").fire("click");
    await flush();
    const input = dialogTagInput(root);
    input.value = "x";
    input.fire("input");
    dialogTagAdd(root).fire("click");
    assertEq(jc.calls.length, 1);

    const select = findByClass(root, "acp-mod-tasks-project");
    select.value = "P1";
    select.fire("change");
    await flush();
    assert(!dialogOpen(root), "a project switch closes the tag editor");
    const afterSwitch = ctx.client.taskView.viewCalls.length;
    jc.calls[0].deferred.resolve({ results: [{ task_id: "job-A", ok: true }], updated: 1 });
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, afterSwitch,
      "the superseded generation never refreshes the new project");
    assertEq(walk(root).filter((n) => n.getAttribute
      && n.getAttribute("data-acp-action-state")
      && n.getAttribute("data-acp-action-state") !== "idle").length, 0,
      "no busy/error state leaks into the new project's DOM");
  });

  await test("A-23 real ACPClient.jobCommands wiring posts the documented batch-ops tag body", async () => {
    const ctx = setup();
    loadScript("js/client/job-commands.js", ctx.sandbox);
    const calls = [];
    const respond = (path, opts) => {
      calls.push({
        path,
        method: opts && opts.method,
        body: opts && opts.body ? JSON.parse(opts.body) : null,
      });
      return Promise.resolve({ results: [{ task_id: "job-A", ok: true }], updated: 1 });
    };
    ctx.sandbox.window.ACPClient.jobCommands.init({ v1: respond, v2: respond });
    ctx.client.jobCommands = ctx.sandbox.window.ACPClient.jobCommands;
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-A", status: "completed", tags: [] }),
    ] }]));
    const root = ctx.host.children[0];
    tagsButton(findByAttr(root, "data-job-id", "job-A"), "job-A").fire("click");
    await flush();
    const input = dialogTagInput(root);
    input.value = "gamma";
    input.fire("input");
    dialogTagAdd(root).fire("click");
    await flush();

    assertEq(calls[0], {
      path: "/tasks/batch-ops",
      method: "POST",
      body: { task_ids: ["job-A"], op: "add_tags", payload: { tags: ["gamma"] } },
    }, "tags go through client.jobCommands.batchOp → POST /tasks/batch-ops");
    assertEq(calls.filter((c) => c.method === "DELETE").length, 0,
      "tag management never issues DELETE");
    assertEq(calls.length, 1, "exactly one tag command per add");
  });

  // ------------------------------------------------- A-24 recycle bin --

  await test("A-24 trash view lists the archived recoverable scope through the owned controller", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-Q", display_name: "queue_task", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];

    // Normal queue scope is unchanged: archived rows stay excluded.
    assertEq(ctx.client.taskView.viewCalls[0].prefs.archived, "exclude");
    const toggle = trashToggle(root);
    assert(toggle, "the toolbar exposes the trash toggle");
    assertEq(toggle.getAttribute("aria-pressed"), "false");
    assertEq(toggle.textContent, "回收站");
    assertEq(trashNote(root).hidden, true, "the trash note is hidden in the queue");

    // Enter the trash: ONE query through the OWNED controller (no second
    // cache, no direct fetch) on the existing archived=only scope.
    toggle.fire("click");
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "loading", "the switch renders the loading state");
    assertEq(root.getAttribute("data-acp-trash"), "on");
    assertEq(toggle.getAttribute("aria-pressed"), "true");
    assertEq(toggle.textContent, "返回任务列表");
    assertEq(trashNote(root).hidden, false);
    assertEq(ctx.client.taskView.viewCalls.length, 2, "exactly one new list query");
    const trashCall = ctx.client.taskView.viewCalls[1];
    assertEq(trashCall.prefs.archived, "only", "the trash rides the existing prefs.archived query key");
    assertEq(trashCall.projectId, null);
    assertEq(ctx.client.requests.filter((r) => r.method !== "GET").length, 0,
      "entering the trash issues no write/transport request");

    // Ready: archived rows render as trash rows with ONLY trash commands.
    trashCall.deferred.resolve(trashBody([
      { id: "job-T", display_name: "archived_task", status: "completed" },
    ]));
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const rowT = trashRow(root, "job-T");
    assert(rowT, "archived rows carry data-acp-trash-row");
    assertEq(rowT.getAttribute("data-acp-archived"), "true");
    assertEq(findByAttr(root, "data-job-id", "job-Q"), null,
      "the queue's rows are replaced by the trash scope, never merged");
    assertEq(trashAction(rowT, "restore").textContent, "恢复");
    assertEq(trashAction(rowT, "delete").textContent, "彻底删除");
    assertEq(actionButtons(rowT).length, 0, "no A-18/A-19 status commands inside the trash");
    assertEq(lifecycleButtons(rowT).length, 0, "no A-20/A-21 lifecycle commands inside the trash");
    assertEq(tagsButton(rowT, "job-T"), null, "no tag editor inside the trash");
    assert(rowCheckbox(root, "job-T"), "trash rows are selectable for the trash batch bar");
    assertEq(batchBar(root).getAttribute("data-acp-batch-bar"), "trash");

    // Leave the trash: the queue renders again, unchanged.
    toggle.fire("click");
    await flush();
    assertEq(toggle.getAttribute("aria-pressed"), "false");
    assertEq(root.getAttribute("data-acp-trash"), "off");
    assertEq(trashNote(root).hidden, true);
    const queueCall = ctx.client.taskView.viewCalls[2];
    assertEq(queueCall.prefs.archived, "exclude");
    queueCall.deferred.resolve(bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-Q", display_name: "queue_task", status: "completed" }),
    ] }]));
    await flush();
    const rowQ = findByAttr(root, "data-job-id", "job-Q");
    assert(rowQ, "the queue row is back");
    assertEq(rowQ.getAttribute("data-acp-trash-row"), null, "queue rows are never trash rows");
    assertEq(trashAction(rowQ, "restore"), null, "queue rows never expose trash actions");
    assert(lifecycleButton(rowQ, "purge"), "the A-20 queue lifecycle surface is unchanged");
    assertEq(batchBar(root).getAttribute("data-acp-batch-bar"), "purge");

    // Empty trash renders the trash-specific empty copy, never the queue's.
    toggle.fire("click");
    await flush();
    ctx.client.taskView.viewCalls[3].deferred.resolve(bodyWith([]));
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    const emptyLabel = findByClass(root, "acp-mod-tasks-state-label");
    assertEq(emptyLabel.textContent, "回收站为空");
    assertEq(emptyLabel.getAttribute("data-i18n"), "next.tasks.trash.empty");
    assertEq(jc.calls.length, 0, "reading the trash never issues a command");
  });

  await test("A-24 trash view error keeps the trash scope and retries through the controller", async () => {
    const ctx = setup({ jobCommands: makeJobCommandsMock() });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];

    trashToggle(root).fire("click");
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, 2);
    ctx.client.taskView.viewCalls[1].deferred.reject({ status: 500, body: { detail: "task-view unavailable" } });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assertEq(findByClass(root, "acp-mod-tasks-state-label").textContent, "任务列表加载失败");
    // The trash chrome survives the error: the view never silently falls back
    // to the queue scope.
    assertEq(root.getAttribute("data-acp-trash"), "on");
    assertEq(trashNote(root).hidden, false);

    const retry = findByClass(root, "acp-mod-tasks-retry");
    assert(retry, "the error state offers a retry");
    retry.fire("click");
    await flush();
    const retryCall = ctx.client.taskView.viewCalls[2];
    assert(retryCall, "the retry issues one query");
    assertEq(retryCall.prefs.archived, "only", "the retry stays in the trash scope");
    retryCall.deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assert(trashRow(root, "job-T"));
  });

  await test("A-24 restore sends the existing batch-ops unarchive op and refreshes the trash", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];

    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([
      { id: "job-T", display_name: "archived_task" },
    ]));
    await flush();
    const rowT = trashRow(root, "job-T");
    const before = ctx.client.taskView.viewCalls.length;

    trashAction(rowT, "restore").fire("click");
    await flush();
    assertEq(jc.calls.length, 1, "one restore command");
    assertEq(jc.calls[0].name, "batchOp");
    assertEq(jc.calls[0].op, "unarchive");
    assertEq(jc.calls[0].taskIds, ["job-T"]);
    assertEq(jc.calls[0].request, { taskIds: ["job-T"], op: "unarchive", payload: {} });
    assertEq(rowT.getAttribute("data-acp-action-state"), "busy");
    assertEq(trashAction(rowT, "restore").disabled, true);
    assertEq(trashAction(rowT, "delete").disabled, true);
    assertEq(ctx.client.taskView.viewCalls.length, before, "nothing refreshes before the receipt");

    jc.calls[0].deferred.resolve({ results: [{ task_id: "job-T", ok: true }], updated: 1 });
    await flush();
    assert(ctx.client.taskView.viewCalls.length > before,
      "restore success refreshes through the owned controller");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(reload.prefs.archived, "only", "the refresh stays in the trash scope");
    assertEq(reload.projectId, null);
    assertEq(batchMessage(root).hidden, false);
    assertEq(batchMessage(root).textContent, "已恢复 1 个任务");
    assertEq(batchMessage(root).getAttribute("data-acp-batch-tone"), "ok");
    reload.deferred.resolve(bodyWith([]));
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
  });

  await test("A-24 permanent delete is a two-step trash confirmation through the purge cascade", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];

    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([
      { id: "job-T", display_name: "archived_task" },
    ]));
    await flush();
    const rowT = trashRow(root, "job-T");

    // First click only opens the confirmation: nothing is deleted.
    trashAction(rowT, "delete").fire("click");
    await flush();
    assertEq(jc.calls.length, 0, "a single click never permanently deletes");
    assert(dialogOpen(root), "the trash delete confirmation opened");
    assertEq(dialogKind(root), "trash-delete");
    assertEq(dialogCard(root).getAttribute("role"), "dialog");
    assertEq(dialogCard(root).getAttribute("aria-modal"), "true");
    assertEq(findByClass(root, "acp-mod-tasks-modal-title").textContent, "确认彻底删除");
    const text = findByClass(root, "acp-mod-tasks-modal-text").textContent;
    assert(text.indexOf("archived_task") !== -1, "the dialog names the trashed task");
    assert(text.indexOf("purge_cascade") !== -1, "the dialog names the cascade semantics");
    assertEq(dialogDeleteData(root).checked, false, "delete_data stays opt-in");
    assertEq(dialogConfirm(root).getAttribute("data-acp-dialog-confirm"), "trash-delete");
    // Cancelling is an explicit opt-out: still nothing was sent.
    dialogCancel(root).fire("click");
    await flush();
    assert(!dialogOpen(root));
    assertEq(jc.calls.length, 0);

    // Second confirmation = the EXISTING purge command ({jobIds, deleteData}).
    trashAction(rowT, "delete").fire("click");
    const box = dialogDeleteData(root);
    box.checked = true;
    box.fire("change");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 1);
    assertEq(jc.calls[0].name, "purge");
    assertEq(jc.calls[0].jobIds, ["job-T"]);
    assertEq(jc.calls[0].deleteData, true);
    assertEq(jc.calls[0].request, { jobIds: ["job-T"], deleteData: true });
    assertEq(rowT.getAttribute("data-acp-action-state"), "busy");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    const before = ctx.client.taskView.viewCalls.length;

    jc.calls[0].deferred.resolve({ results: [
      { job_id: "job-T", ok: true, action: "purged", error: null },
    ] });
    await flush();
    assert(!dialogOpen(root), "the dialog closes on the server receipt");
    assert(ctx.client.taskView.viewCalls.length > before, "the delete refreshes through the controller");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(reload.prefs.archived, "only", "the refresh stays in the trash scope");
    assertEq(batchMessage(root).textContent, "已彻底删除 1 个任务");
    reload.deferred.resolve(bodyWith([]));
    await flush();
    // The module never issues a raw DELETE: the whole flow is command-only.
    assertEq(ctx.client.requests.filter((r) => r.method === "DELETE").length, 0);
  });

  await test("A-24 permanent delete failure keeps the dialog open, skips refresh, and stays retryable", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];

    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    const before = ctx.client.taskView.viewCalls.length;

    trashAction(trashRow(root, "job-T"), "delete").fire("click");
    dialogConfirm(root).fire("click");
    jc.calls[0].deferred.reject({ status: 500, body: { detail: "purge_cascade unavailable" } });
    await flush();

    assert(dialogOpen(root), "the confirmation stays open for an explicit retry");
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "error");
    const err = dialogErrorEl(root);
    assert(err, "dialog error line rendered");
    assertEq(err.getAttribute("role"), "alert");
    assertEq(err.textContent.indexOf("彻底删除失败"), 0);
    assert(err.textContent.indexOf("purge_cascade unavailable") !== -1,
      "the server error text is surfaced verbatim");
    assertEq(ctx.client.taskView.viewCalls.length, before,
      "a failed permanent delete never refreshes the list");
    const row = trashRow(root, "job-T");
    assertEq(row.getAttribute("data-acp-action-state"), "error");
    assert(messageOf(row).textContent.indexOf("彻底删除失败") !== -1);
    assertEq(dialogConfirm(root).disabled, false, "the confirmation is retryable");

    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 2);
    assertEq(dialogHost(root).getAttribute("data-acp-dialog-state"), "busy");
    jc.calls[1].deferred.resolve({ results: [{ job_id: "job-T", ok: true, action: "purged" }] });
    await flush();
    assert(!dialogOpen(root), "a successful retry closes the dialog");
    assert(ctx.client.taskView.viewCalls.length > before);
  });

  await test("A-24 batch selection is trash-scoped: restore and permanent delete cover only trash rows", async () => {
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [
      jobRow({ id: "job-Q", status: "completed" }),
    ] }]));
    const root = ctx.host.children[0];

    // A queue selection exists before entering the trash…
    rowCheckbox(root, "job-Q").checked = true;
    rowCheckbox(root, "job-Q").fire("change");
    assertEq(batchBar(root).hidden, false);
    assertEq(batchBar(root).getAttribute("data-acp-batch-bar"), "purge");

    // …and it is dropped on entering: the trash batch never targets the queue.
    trashToggle(root).fire("click");
    await flush();
    assertEq(batchBar(root).hidden, true, "the queue selection never leaks into the trash");
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([
      { id: "job-T", display_name: "T" },
      { id: "job-U", display_name: "U" },
      { id: "job-V", display_name: "V" },
    ]));
    await flush();
    assertEq(trashRows(root).map((r) => r.getAttribute("data-job-id")),
      ["job-T", "job-U", "job-V"]);

    // Select both trash rows: the bar switches to the trash commands.
    rowCheckbox(root, "job-T").checked = true;
    rowCheckbox(root, "job-T").fire("change");
    rowCheckbox(root, "job-U").checked = true;
    rowCheckbox(root, "job-U").fire("change");
    assertEq(batchBar(root).hidden, false);
    assertEq(batchBar(root).getAttribute("data-acp-batch-bar"), "trash");
    assertEq(findByClass(root, "acp-mod-tasks-batch-count").textContent, "已选 2 项");
    assertEq(batchButton(root, "purge"), null, "the queue purge entry never appears in the trash");
    assert(batchButton(root, "restore"), "batch restore entry");
    assert(batchButton(root, "trash-delete"), "batch permanent-delete entry");

    // Batch restore = ONE unarchive call for the selection.
    const before = ctx.client.taskView.viewCalls.length;
    batchButton(root, "restore").fire("click");
    await flush();
    assertEq(jc.calls.length, 1);
    assertEq(jc.calls[0].op, "unarchive");
    assertEq(jc.calls[0].taskIds, ["job-T", "job-U"]);
    assertEq(ctx.client.taskView.viewCalls.length, before);

    // Server is authoritative: one restored, one refused.
    jc.calls[0].deferred.resolve({ results: [
      { task_id: "job-T", ok: true },
      { task_id: "job-U", ok: false, error: "Task not found: job-U" },
    ], updated: 1 });
    await flush();
    assertEq(batchMessage(root).textContent, "已恢复 1 个任务，1 个失败");
    assertEq(batchMessage(root).getAttribute("data-acp-batch-tone"), "error");
    assertEq(batchBar(root).hidden, true, "selection cleared after the report");
    assert(ctx.client.taskView.viewCalls.length > before, "the report refreshes the trash");
    const reload = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(reload.prefs.archived, "only");
    reload.deferred.resolve(trashBody([
      { id: "job-U", display_name: "U" },
      { id: "job-V", display_name: "V" },
    ]));
    await flush();
    const rowU = trashRow(root, "job-U");
    assertEq(rowU.getAttribute("data-acp-action-state"), "error");
    assertEq(messageOf(rowU).getAttribute("role"), "alert");
    assert(messageOf(rowU).textContent.indexOf("Task not found: job-U") !== -1,
      "the server's per-task refusal text is rendered verbatim");

    // Batch permanent delete: one dialog confirmation, one purge for both.
    rowCheckbox(root, "job-U").checked = true;
    rowCheckbox(root, "job-U").fire("change");
    rowCheckbox(root, "job-V").checked = true;
    rowCheckbox(root, "job-V").fire("change");
    assertEq(findByClass(root, "acp-mod-tasks-batch-count").textContent, "已选 2 项");
    batchButton(root, "trash-delete").fire("click");
    await flush();
    assertEq(jc.calls.length, 1, "the batch button only opens the confirmation");
    assertEq(dialogKind(root), "trash-delete");
    assertEq(findByClass(root, "acp-mod-tasks-modal-text").textContent,
      "将彻底删除回收站中的 2 个任务，服务端会级联删除任务记录（purge_cascade），此操作不可恢复。");
    dialogConfirm(root).fire("click");
    assertEq(jc.calls.length, 2);
    assertEq(jc.calls[1].name, "purge");
    assertEq(jc.calls[1].jobIds, ["job-U", "job-V"]);
    assertEq(jc.calls[1].deleteData, false);
    jc.calls[1].deferred.resolve({ results: [
      { job_id: "job-U", ok: true, action: "purged" },
      { job_id: "job-V", ok: true, action: "purged" },
    ] });
    await flush();
    assert(!dialogOpen(root));
    assertEq(batchMessage(root).textContent, "已彻底删除 2 个任务");
    assertEq(batchBar(root).hidden, true);
    const last = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    last.deferred.resolve(bodyWith([]));
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
  });

  await test("A-24 absent/partial client: the trash stays readable but its actions follow the command client", async () => {
    // No client.jobCommands at all: the trash view is readable, actionless.
    const ctx = setup();
    // A page-global command client must never be picked up by the module.
    ctx.sandbox.window.ACPClient = { jobCommands: makeJobCommandsMock() };
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];
    const toggle = trashToggle(root);
    assert(toggle, "the trash view stays reachable without a command client");
    toggle.fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    assert(trashRow(root, "job-T"), "archived rows still render");
    assertEq(trashActions(root).length, 0, "no trash write actions without client.jobCommands");
    assertEq(walk(root).filter((n) => n.tagName === "INPUT" && n.getAttribute("data-acp-select")).length, 0,
      "no trash batch selection without a command client");
    assert(batchBar(root).hidden);
    assertEq(walk(root).filter((n) => n.tagName === "BUTTON" && n.getAttribute("data-acp-batch")).length, 0);
    assertEq(trashReadonly(root).hidden, false, "the read-only note is explicit");
    assertEq(trashReadonly(root).textContent, "当前客户端不支持回收站操作，回收站为只读。");

    // Command client without batchOp: only restore is disabled; permanent
    // delete (purge) and the trash batch stay usable.
    const jc = makeJobCommandsMock({ omit: ["batchOp"] });
    const ctx2 = setup({ jobCommands: jc });
    await mountReady(ctx2, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root2 = ctx2.host.children[0];
    trashToggle(root2).fire("click");
    await flush();
    ctx2.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    const row2 = trashRow(root2, "job-T");
    assertEq(trashAction(row2, "restore").disabled, true, "missing batchOp disables restore");
    assertEq(trashAction(row2, "restore").getAttribute("data-acp-action-unavailable"), "true");
    assertEq(trashAction(row2, "restore").getAttribute("title"), "当前客户端不支持该回收站操作");
    assertEq(trashAction(row2, "delete").disabled, false, "purge stays available");
    assert(rowCheckbox(root2, "job-T"), "the trash batch stays selectable");
    assertEq(trashReadonly(root2).hidden, true);
    assertEq(jc.calls.length, 0);
  });

  await test("A-24 late receipts and late trash lists never cross scopes or survive dispose", async () => {
    // (a) dispose while a restore is in flight: nothing writes afterwards.
    const jc = makeJobCommandsMock();
    const ctx = setup({ jobCommands: jc });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];
    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    trashAction(trashRow(root, "job-T"), "restore").fire("click");
    assertEq(jc.calls.length, 1);
    const before = ctx.client.taskView.viewCalls.length;

    ctx.instance.dispose();
    await flush();
    assertEq(ctx.host.children.length, 0, "host DOM cleared");
    assertEq(ctx.dom.counters.added, ctx.dom.counters.removed,
      "every trash listener was removed on dispose");
    jc.calls[0].deferred.resolve({ results: [{ task_id: "job-T", ok: true }] });
    await flush();
    assertEq(ctx.client.taskView.viewCalls.length, before,
      "a late restore receipt after dispose never refreshes");

    // A late rejection is equally inert (no rethrow, no DOM write).
    const jc2 = makeJobCommandsMock();
    const ctx2 = setup({ jobCommands: jc2 });
    await mountReady(ctx2, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root2 = ctx2.host.children[0];
    trashToggle(root2).fire("click");
    await flush();
    ctx2.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();
    trashAction(trashRow(root2, "job-T"), "delete").fire("click");
    dialogConfirm(root2).fire("click");
    assertEq(jc2.calls.length, 1);
    ctx2.instance.dispose();
    await flush();
    jc2.calls[0].deferred.reject({ status: 500, body: { detail: "late purge failure" } });
    await flush();
    assertEq(ctx2.host.children.length, 0);
    assertEq(ctx2.dom.counters.added, ctx2.dom.counters.removed,
      "a late delete rejection writes no dialog/error DOM after dispose");

    // (b) a late trash list response after leaving the trash never renders.
    const ctx3 = setup({ jobCommands: makeJobCommandsMock() });
    await mountReady(ctx3, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root3 = ctx3.host.children[0];
    trashToggle(root3).fire("click"); // trash query #2 (still pending)
    await flush();
    trashToggle(root3).fire("click"); // back to the queue: query #3
    await flush();
    assertEq(ctx3.client.taskView.viewCalls.length, 3);
    const staleTrashCall = ctx3.client.taskView.viewCalls[1];
    const queueCall = ctx3.client.taskView.viewCalls[2];
    assertEq(queueCall.prefs.archived, "exclude");
    staleTrashCall.deferred.resolve(trashBody([{ id: "job-stale" }]));
    await flush();
    assertEq(root3.getAttribute("data-acp-trash"), "off");
    assertEq(findByAttr(root3, "data-job-id", "job-stale"), null,
      "a stale trash response never renders into the queue");
    queueCall.deferred.resolve(bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    await flush();
    assert(findByAttr(root3, "data-job-id", "job-Q"), "the live queue query wins");
  });

  await test("A-24 the trash scope survives a project switch and keeps its chrome", async () => {
    const ctx = setup({
      projects: [{ project_id: "P1", name: "项目一" }],
      jobCommands: makeJobCommandsMock(),
    });
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];
    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T" }]));
    await flush();

    const select = findByClass(root, "acp-mod-tasks-project");
    select.value = "P1";
    select.fire("change");
    await flush();
    // The project switch bumps the generation and re-queries through the owned
    // controller, but the trash scope stays active (archived:"only").
    const switched = ctx.client.taskView.viewCalls[ctx.client.taskView.viewCalls.length - 1];
    assertEq(switched.projectId, "P1");
    assertEq(switched.prefs.archived, "only");
    assertEq(root.getAttribute("data-acp-trash"), "on");
    assertEq(trashNote(root).hidden, false);
    assertEq(trashToggle(root).getAttribute("aria-pressed"), "true");
    switched.deferred.resolve(trashBody([{ id: "job-P1T" }]));
    await flush();
    assert(trashRow(root, "job-P1T"), "the new project's archived rows render in the trash");
  });

  await test("A-24 real ACPClient.jobCommands wiring restores via batch-ops and deletes via the purge cascade", async () => {
    const ctx = setup();
    loadScript("js/client/job-commands.js", ctx.sandbox);
    const calls = [];
    const respond = (path, opts) => {
      calls.push({
        path,
        method: opts && opts.method,
        body: opts && opts.body ? JSON.parse(opts.body) : null,
      });
      if (path === "/jobs/purge") {
        return Promise.resolve({ results: [{ job_id: "job-T", ok: true, action: "purged" }] });
      }
      return Promise.resolve({ results: [{ task_id: "job-T", ok: true }], updated: 1 });
    };
    ctx.sandbox.window.ACPClient.jobCommands.init({ v1: respond, v2: respond });
    ctx.client.jobCommands = ctx.sandbox.window.ACPClient.jobCommands;
    await mountReady(ctx, bodyWith([{ key: "g", jobs: [jobRow({ id: "job-Q" })] }]));
    const root = ctx.host.children[0];

    trashToggle(root).fire("click");
    await flush();
    ctx.client.taskView.viewCalls[1].deferred.resolve(trashBody([{ id: "job-T", display_name: "T" }]));
    await flush();

    // Restore → the documented batch-ops body (unarchive op).
    trashAction(trashRow(root, "job-T"), "restore").fire("click");
    await flush();
    assertEq(calls[0], {
      path: "/tasks/batch-ops",
      method: "POST",
      body: { task_ids: ["job-T"], op: "unarchive", payload: {} },
    }, "restore goes through client.jobCommands.batchOp → POST /tasks/batch-ops");
    ctx.client.taskView.viewCalls[2].deferred.resolve(trashBody([{ id: "job-T", display_name: "T" }]));
    await flush();

    // Permanent delete → the documented purge body (purge_cascade server-side).
    trashAction(trashRow(root, "job-T"), "delete").fire("click");
    dialogConfirm(root).fire("click");
    await flush();
    assertEq(calls[1], {
      path: "/jobs/purge",
      method: "POST",
      body: { job_ids: ["job-T"], delete_data: false },
    }, "permanent delete goes through client.jobCommands.purge → POST /jobs/purge");
    assertEq(calls.filter((c) => c.method === "DELETE").length, 0,
      "the trash never issues a raw DELETE");
    assertEq(calls.length, 2, "exactly one command per confirmed trash action");
  });
};
