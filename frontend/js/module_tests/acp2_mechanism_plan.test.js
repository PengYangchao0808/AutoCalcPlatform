/* ACP 3.0 module test — mechanism-plan (Track A / A-49 · P17 mechanism plan).
 *
 * Loads the module as the classic script the Shell loader consumes
 * (window.ACPNextModules["mechanism-plan"] = { descriptor, factory, _internals })
 * inside the zero-dependency node vm harness with a fake DOM. Every request goes
 * through an injected request seam, so no test can reach the network (the
 * sandbox has no fetch/XMLHttpRequest at all).
 *
 * Pinned contracts:
 *  1. exactly one ACPNextModules key, Shell-valid descriptor, no extra global;
 *  2. four states (empty/loading/ready/error) + separate busy/plan-state
 *     attributes; list and detail errors are retryable;
 *  3. study view: steps, roles (reactant/product/intermediate/TS), reactants /
 *     products from the read-only reaction route, provenance, plan status;
 *  4. plan assembly from the study's own coordinate plans, role editing,
 *     validation, direct-ts, exact `{plan, strategy, fidelity}` POST body;
 *  5. confirmation success -> plan_hash receipt; 410 (retired) / 409 / 422 /
 *     500 / malformed response -> explicit error, never a fake success;
 *  6. busy double-click guard, generation/dispose guards, focus changes;
 *  7. only the five existing mechanism-study routes are ever requested and the
 *     only POST is the plan confirmation (no task submit, no invented route);
 *  8. source pins: no direct network primitive, no filesystem path build, no
 *     A-50/A-51 surfaces, no unsafe HTML.
 */
"use strict";

const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const MODULE_PATH = "js/modules/mechanism-plan/module.js";
  const API = "/api/v1";
  const STUDIES = API + "/mechanism-studies";
  const DIGEST = "sha256:" + "a".repeat(64);
  const HASH_2 = "sha256:" + "b".repeat(64);

  function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function flush(n = 12) {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await tick();
    for (let i = 0; i < 8; i++) await Promise.resolve();
  }

  // ------------------------------------------------------------ fake DOM --

  function makeDom() {
    const docListeners = new Map();

    function makeEl(tag) {
      const listeners = new Map();
      const node = {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        className: "",
        textContent: "",
        hidden: false,
        disabled: false,
        value: "",
        checked: false,
        type: "",
        name: "",
        id: "",
        attrs: {},
        listeners,
        setAttribute(key, value) { this.attrs[key] = String(value); },
        getAttribute(key) { return key in this.attrs ? this.attrs[key] : null; },
        removeAttribute(key) { delete this.attrs[key]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        removeChild(child) {
          const index = this.children.indexOf(child);
          if (index >= 0) this.children.splice(index, 1);
          child.parentNode = null;
          return child;
        },
        replaceChildren(...nodes) {
          this.children.forEach((child) => { child.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((child) => { child.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, event) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: node }, event || {})));
        }
      };
      return node;
    }

    const document = {
      createElement: makeEl,
      addEventListener(type, fn) {
        if (!docListeners.has(type)) docListeners.set(type, new Set());
        docListeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (docListeners.has(type)) docListeners.get(type).delete(fn);
      },
      dispatchEvent(event) {
        const set = docListeners.get(event && event.type);
        if (set) [...set].forEach((fn) => fn(event));
        return true;
      },
      documentElement: makeEl("html"),
      activeDocListeners() {
        let count = 0;
        docListeners.forEach((set) => { count += set.size; });
        return count;
      }
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((child) => { out.push(child); walk(child, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((node) => node.getAttribute && node.getAttribute(attr) === value) || null;
  }

  function findAll(root, attr, value) {
    return walk(root).filter((node) => node.getAttribute && node.getAttribute(attr) === value);
  }

  function collectAttr(node, attr) {
    const out = [];
    walk(node).forEach((child) => {
      const value = child.getAttribute && child.getAttribute(attr);
      if (value != null) out.push(value);
    });
    return out;
  }

  function subtreeText(node) {
    let text = node.textContent || "";
    (node.children || []).forEach((child) => { text += " " + subtreeText(child); });
    return text;
  }

  // ------------------------------------------------------------ fixtures --

  function summary(id, jobId) {
    return {
      id: id,
      job_id: jobId || "job-1",
      status: "reaction_confirmed",
      created_at: "2026-10-31T00:00:00Z",
      updated_at: "2026-10-31T00:05:00Z",
      n_states: 3,
      n_edges: 1,
      n_decisions_pending: 1,
      unified_status: "RESUMABLE"
    };
  }

  function coordinatePlan(overrides) {
    return Object.assign({
      coordinates: [
        { id: "rc1", kind: "distance", atoms: [0, 1], role: "drive", start: 1.2, end: 2.4 },
        { id: "rc2", kind: "angle", atoms: [0, 1, 2], role: "monitor", start: 110.0 }
      ],
      points: 21,
      coupling: "synchronous",
      start_from: "reactant"
    }, overrides || {});
  }

  function elementaryStep(overrides) {
    return Object.assign({
      step_id: "step-1",
      source_state_id: "s1",
      sink_state_id: "s2",
      ts_id: "ts1",
      path_strategy: "guided-scan",
      coordinate_plan: coordinatePlan(),
      irc_connectivity: {},
      barrier_forward: 12.5,
      barrier_reverse: 3.1,
      fidelity: "s3",
      status: "confirmed"
    }, overrides || {});
  }

  function detailPayload(id) {
    const studyId = id || "study-1";
    return {
      id: studyId,
      job_id: "job-1",
      status: "reaction_confirmed",
      created_at: "2026-10-31T00:00:00Z",
      updated_at: "2026-10-31T00:05:00Z",
      unified_status: "RESUMABLE",
      study_json: {
        study_id: studyId,
        status: "reaction_confirmed",
        reactant_id: "s1",
        product_id: "s3",
        ts_guess_id: "ts1",
        stable_states: [
          { state_id: "s1", role: "reactant", charge: 0, multiplicity: 1 },
          { state_id: "s2", role: "intermediate", charge: 0, multiplicity: 1 },
          { state_id: "s3", role: "product", charge: 0, multiplicity: 1 }
        ],
        stationary_points: [
          {
            point_id: "ts1", role: "transition_state", kind: "ts",
            charge: 0, multiplicity: 1, energy_hartree: -100.1
          }
        ],
        elementary_steps: [elementaryStep()],
        routes: [{
          route_id: "route-1",
          path_strategy: "guided-scan",
          fidelity: "s3",
          coordinate_plan: coordinatePlan({ points: 17 })
        }],
        network: {
          nodes: [
            { state_id: "s1", charge: 0, multiplicity: 1 },
            { state_id: "s2", charge: 0, multiplicity: 1 }
          ],
          edges: [elementaryStep()]
        },
        // Must never be read or rendered: this module builds no filesystem path.
        study_dir: "C:/secret/mechanism_study/should-not-render",
        decisions: []
      },
      decisions: [
        { id: "dec-1", status: "waiting", resolution: null, created_at: "2026-10-31T00:01:00Z" }
      ]
    };
  }

  function reportPayload(id) {
    const studyId = id || "study-1";
    return {
      study_id: studyId,
      job_id: "job-1",
      reaction_network: {
        nodes: [
          {
            state_id: "s1", role: "reactant", charge: 0, multiplicity: 1,
            canonical_energy_hartree: -100.5
          },
          {
            state_id: "s2", role: "intermediate", charge: 0, multiplicity: 1,
            canonical_energy_hartree: -100.2
          },
          {
            state_id: "s3", role: "product", charge: 0, multiplicity: 1,
            canonical_energy_hartree: -101.0
          }
        ],
        edges: [{
          step_id: "step-1", source: "s1", sink: "s2", ts_id: "ts1",
          barrier_forward: 12.5, barrier_reverse: 3.1,
          fidelity: "s3", status: "confirmed", path_strategy: "guided-scan"
        }]
      },
      mechanism_profile: { routes: [{ route_id: "route-1", status: "completed" }] },
      stationary_points: {
        stationary_points: [{
          point_id: "ts1", role: "transition_state", kind: "ts",
          charge: 0, multiplicity: 1, energy_hartree: -100.1
        }]
      },
      quality_gates: { quality_gates: [] },
      provenance: {
        study_id: studyId,
        study_input_signature: "sig-abc",
        count: 2,
        records: [
          {
            provider: "xtb", provider_version: "6.6.0", strategy: "guided-scan",
            profile_id: "default", input_signature: "sig-a"
          },
          {
            provider: "orca", provider_version: "5.0.4", strategy: "guided-scan",
            profile_id: "high", input_signature: "sig-b"
          }
        ],
        notes: ["report note"]
      }
    };
  }

  function reactionPayload(id) {
    const studyId = id || "study-1";
    return {
      status: "reaction_confirmed",
      reaction: {
        schema_version: 2,
        study_id: studyId,
        reactants: [{ smiles: "CC", charge: 0, multiplicity: 1 }],
        products: [{ smiles: "C=C", charge: 0, multiplicity: 1 }],
        mapping: [{ reactant_index: 0, product_index: 0 }],
        config_hash: "sha256:deadbeef",
        confirmed_by: "user"
      }
    };
  }

  function httpError(status, detail) {
    const error = new Error("HTTP " + status);
    error.status = status;
    error.body = typeof detail === "string" ? { detail: detail } : detail;
    return error;
  }

  function defaultResponse(pathname, opts) {
    const method = opts && opts.method ? opts.method : "GET";
    if (method === "POST" && /\/mechanism\/plan$/.test(pathname)) {
      return { status: "plan_confirmed", plan_hash: DIGEST };
    }
    if (pathname.indexOf(STUDIES + "?") === 0) return [summary("study-1", "job-1")];
    const match = pathname.match(/^\/api\/v1\/mechanism-studies\/([^/]+)(\/report|\/reaction)?$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      if (match[2] === "/report") return reportPayload(id);
      if (match[2] === "/reaction") return reactionPayload(id);
      return detailPayload(id);
    }
    throw httpError(404, "unexpected path: " + pathname);
  }

  function makeClient(responder) {
    const calls = [];
    function request(pathname, opts) {
      calls.push({ path: pathname, opts: opts || {} });
      try {
        return Promise.resolve((responder || defaultResponse)(pathname, opts, calls.length));
      } catch (error) {
        return Promise.reject(error);
      }
    }
    return { calls, request };
  }

  function makeContext(jobId) {
    const listeners = new Set();
    let snapshot = {
      projectId: null,
      taskRef: jobId ? { jobId: jobId, attempt: 1 } : null,
      structureRef: null,
      networkNodeId: null,
      revision: 1
    };
    return {
      getSnapshot() { return snapshot; },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      setFocus(patch) {
        snapshot = Object.assign({}, snapshot, patch, { revision: snapshot.revision + 1 });
        listeners.forEach((fn) => fn(snapshot));
      },
      listenerCount() { return listeners.size; }
    };
  }

  function loadModule(sandbox) {
    loadScript(MODULE_PATH, sandbox);
    return sandbox.ACPNextModules["mechanism-plan"];
  }

  function setup(options) {
    options = options || {};
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const client = options.client || makeClient(options.responder);
    const context = options.context === undefined ? null : options.context;
    const host = dom.document.createElement("div");
    const deps = { doc: dom.document, request: client.request };
    if (context) deps.context = context;
    Object.assign(deps, options.deps || {});
    const instance = mod.factory(deps);
    return { dom, sandbox, mod, client, context, host, deps, instance };
  }

  async function mount(ctx) {
    await ctx.instance.mount(ctx.host, { context: ctx.context });
    await flush();
  }

  function root(ctx) {
    return ctx.host.children[0] || null;
  }

  function stateOf(ctx) {
    return root(ctx).getAttribute("data-acp-module-state");
  }

  function attr(ctx, name) {
    return root(ctx).getAttribute(name);
  }

  function field(ctx, role) {
    return find(ctx.host, "data-role", role);
  }

  function action(ctx, name) {
    return find(ctx.host, "data-action", name);
  }

  function getCalls(ctx) {
    return ctx.client.calls.filter((call) => !call.opts || call.opts.method !== "POST");
  }

  function postCalls(ctx) {
    return ctx.client.calls.filter((call) => call.opts && call.opts.method === "POST");
  }

  async function loadViaUi(ctx, studyId) {
    const input = field(ctx, "study-id");
    input.value = studyId;
    action(ctx, "load-study").fire("click");
    await flush();
  }

  async function loadReady(options) {
    const ctx = setup(options);
    await mount(ctx);
    await loadViaUi(ctx, "study-1");
    return ctx;
  }

  function planPreview(ctx) {
    return JSON.parse(field(ctx, "plan-preview").textContent);
  }

  function coordRole(ctx, coordId) {
    return findAll(ctx.host, "data-role", "coord-role")
      .find((node) => node.getAttribute("data-coord-id") === coordId) || null;
  }

  function coordInput(ctx, role, coordId) {
    return findAll(ctx.host, "data-role", role)
      .find((node) => node.getAttribute("data-coord-id") === coordId) || null;
  }

  // ------------------------------------------------------------- tests --

  await test("module: one ACPNextModules key, Shell-valid descriptor, no legacy global", async () => {
    const sandbox = makeSandbox({});
    loadScript("js/shell/module-registry.js", sandbox);
    const mod = loadModule(sandbox);
    assertEq(Object.keys(sandbox.ACPNextModules), ["mechanism-plan"], "exactly one module key");
    assertEq(typeof mod.factory, "function", "factory present");
    assertEq(sandbox.ACPShell.registry.validateDescriptor(mod.descriptor), null,
      "descriptor must pass the Shell registry validator");
    assertEq(mod.descriptor.id, "mechanism-plan");
    assertEq(mod.descriptor.interfaceVersion, 1);
    assertEq(mod.descriptor.hosts, ["acp-web"]);
    assertEq(mod.descriptor.binds, ["taskRef"]);
    assertEq(mod.descriptor.provides, ["mechanism.plan"]);
    assertEq(mod.descriptor.error.titleKey, "next.mechplan.errorTitle");
    assert(!("mechanismPlanState" in sandbox), "legacy page global mechanismPlanState must not exist");
    assert(!("acpMechanismPlan" in sandbox), "legacy helper must not be redefined");
    assertEq(typeof mod._internals.validatePlanBody, "function");
    assertEq(typeof mod._internals.buildPlanBody, "function");
    assertEq(typeof mod._internals.collectRoleBuckets, "function");
    assertEq(typeof mod._internals.i18n, "object");
  });

  await test("mount: empty state, picker rendered, confirm disabled, zero requests", async () => {
    const ctx = setup();
    await mount(ctx);
    assertEq(root(ctx).className, "acp-mechplan");
    assertEq(stateOf(ctx), "empty");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-plan-state"), "idle");
    assertEq(attr(ctx, "data-acp-plan-status"), "pending");
    assertEq(field(ctx, "study-select").hidden, true);
    assertEq(subtreeText(field(ctx, "empty-state")).indexOf("尚未载入机理研究") !== -1, true);
    assertEq(subtreeText(root(ctx)).indexOf("机理计划装配") !== -1, true, "zh title fallback");
    assertEq(action(ctx, "confirm-plan").disabled, true);
    assertEq(ctx.client.calls.length, 0, "mount performs no network call");
  });

  await test("manual load: loading then ready, exact detail/report/reaction GET paths", async () => {
    const detailDeferred = deferred();
    let detailCalls = 0;
    const client = makeClient((pathname, opts) => {
      if (pathname === STUDIES + "/study-1") {
        detailCalls += 1;
        if (detailCalls === 1) return detailDeferred.promise;
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client });
    await mount(ctx);
    const input = field(ctx, "study-id");
    input.value = "study-1";
    action(ctx, "load-study").fire("click");
    await flush(2);

    assertEq(stateOf(ctx), "loading");
    assertEq(attr(ctx, "data-acp-busy"), "load");
    assertEq(field(ctx, "overlay").hidden, false);
    assertEq(field(ctx, "overlay-message").textContent.indexOf("正在载入") !== -1, true);
    assertEq(field(ctx, "summary-section").hidden, true, "summary section only appears after ready");

    detailDeferred.resolve(detailPayload("study-1"));
    await flush();

    assertEq(stateOf(ctx), "ready");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(field(ctx, "overlay").hidden, true);
    assertEq(ctx.client.calls.map((call) => call.path), [
      STUDIES + "/study-1",
      STUDIES + "/study-1/report",
      STUDIES + "/study-1/reaction"
    ]);
    assertEq(subtreeText(field(ctx, "summary-id")), "study-1");
    assertEq(subtreeText(field(ctx, "summary-job")), "job-1");
    assertEq(subtreeText(field(ctx, "summary-status")), "reaction_confirmed");
    assertEq(subtreeText(field(ctx, "summary-unified")), "RESUMABLE");
    assertEq(subtreeText(field(ctx, "summary-decisions")), "1");
    assertEq(subtreeText(field(ctx, "summary-plan")).indexOf("反应已锁定") !== -1, true);
    assertEq(attr(ctx, "data-acp-study"), "study-1");
  });

  await test("list from task context: job-scoped query, options rendered, first study auto-loads", async () => {
    const context = makeContext("job-42");
    const client = makeClient((pathname, opts) => {
      if (pathname.indexOf(STUDIES + "?") === 0) {
        return [summary("study-1", "job-42"), summary("study-2", "job-42")];
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client, context });
    await mount(ctx);

    assertEq(ctx.client.calls[0].path, STUDIES + "?job_id=job-42&limit=50");
    assert(ctx.client.calls.some((call) => call.path === STUDIES + "/study-1"));
    assert(!ctx.client.calls.some((call) => call.path === STUDIES + "/study-2"),
      "only the first study is auto-loaded");
    const select = field(ctx, "study-select");
    assertEq(select.hidden, false);
    assertEq(select.children.map((option) => option.value), ["", "study-1", "study-2"]);
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "list-status")).indexOf("共 2 个研究") !== -1, true);
    assertEq(subtreeText(field(ctx, "list-status")).indexOf("job-42") !== -1, true);

    select.value = "study-2";
    select.fire("change");
    await flush();
    assertEq(subtreeText(field(ctx, "summary-id")), "study-2");
    assert(ctx.client.calls.some((call) => call.path === STUDIES + "/study-2/report"));
  });

  await test("list empty: empty state with an explicit no-study message, no detail fetch", async () => {
    const context = makeContext("job-empty");
    const client = makeClient((pathname, opts) => {
      if (pathname.indexOf(STUDIES + "?") === 0) return [];
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client, context });
    await mount(ctx);
    assertEq(stateOf(ctx), "empty");
    assertEq(field(ctx, "overlay").hidden, true);
    assertEq(field(ctx, "empty-state").hidden, false);
    assertEq(subtreeText(field(ctx, "list-status")).indexOf("没有可用的机理研究") !== -1, true);
    assertEq(field(ctx, "study-select").hidden, true);
    assert(!ctx.client.calls.some((call) => call.path.indexOf("/mechanism-studies/") === 0),
      "empty list must not fetch a study");
  });

  await test("list error: error state + retry reissues the same list query", async () => {
    const context = makeContext("job-err");
    let listCalls = 0;
    const client = makeClient((pathname, opts) => {
      if (pathname.indexOf(STUDIES + "?") === 0) {
        listCalls += 1;
        if (listCalls === 1) throw httpError(500, "list boom");
        return [];
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client, context });
    await mount(ctx);
    assertEq(stateOf(ctx), "error");
    assertEq(field(ctx, "overlay").hidden, false);
    assertEq(field(ctx, "overlay-detail").textContent.indexOf("list boom") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);
    assertEq(subtreeText(field(ctx, "list-status")).indexOf("加载失败") !== -1, true);

    action(ctx, "retry").fire("click");
    await flush();
    assertEq(listCalls, 2);
    assertEq(stateOf(ctx), "empty");
    assertEq(field(ctx, "overlay").hidden, true);
    assertEq(subtreeText(field(ctx, "list-status")).indexOf("没有可用的机理研究") !== -1, true);
  });

  await test("detail error: error state + retry reissues the detail request", async () => {
    let detailCalls = 0;
    const client = makeClient((pathname, opts) => {
      if (pathname === STUDIES + "/nope") {
        detailCalls += 1;
        if (detailCalls === 1) throw httpError(404, "Mechanism study not found: nope");
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client });
    await mount(ctx);
    await loadViaUi(ctx, "nope");
    assertEq(stateOf(ctx), "error");
    assertEq(field(ctx, "overlay").hidden, false);
    assertEq(field(ctx, "overlay-detail").textContent.indexOf("not found") !== -1, true);
    assertEq(action(ctx, "retry").hidden, false);
    assertEq(attr(ctx, "data-acp-busy"), "none");

    action(ctx, "retry").fire("click");
    await flush();
    assertEq(detailCalls, 2);
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "summary-id")), "nope");
  });

  await test("ready view: steps, roles, reactants/products, provenance, plan status", async () => {
    const ctx = await loadReady();
    const stepRow = find(ctx.host, "data-step-id", "step-1");
    assert(!!stepRow, "step row rendered");
    const stepText = subtreeText(stepRow);
    ["step-1", "s1", "s2", "ts1", "12.50", "3.10", "s3", "confirmed", "guided-scan"]
      .forEach((value) => assert(stepText.indexOf(value) !== -1, "step row missing " + value));

    assertEq(subtreeText(field(ctx, "role-count-reactant")), "1");
    assertEq(subtreeText(field(ctx, "role-count-product")), "1");
    assertEq(subtreeText(field(ctx, "role-count-intermediate")), "1");
    assertEq(subtreeText(field(ctx, "role-count-ts")), "1");
    assertEq(subtreeText(field(ctx, "role-count-unknown")), "0");
    assertEq(findAll(ctx.host, "data-role", "role-item-reactant").length, 1);
    assertEq(findAll(ctx.host, "data-role", "role-item-product").length, 1);
    assertEq(findAll(ctx.host, "data-role", "role-item-intermediate").length, 1);
    assertEq(findAll(ctx.host, "data-role", "role-item-ts").length, 1);
    assertEq(subtreeText(findAll(ctx.host, "data-role", "role-item-ts")[0]).indexOf("ts1") !== -1, true);
    assertEq(subtreeText(findAll(ctx.host, "data-role", "role-item-reactant")[0]).indexOf("s1") !== -1, true);

    assertEq(subtreeText(field(ctx, "reaction-reactants")).indexOf("CC") !== -1, true);
    assertEq(subtreeText(field(ctx, "reaction-products")).indexOf("C=C") !== -1, true);
    assertEq(subtreeText(field(ctx, "reaction-mapping")).indexOf("1 对") !== -1, true);
    assertEq(subtreeText(field(ctx, "reaction-hash")).indexOf("sha256:deadbeef") !== -1, true);
    assertEq(subtreeText(field(ctx, "reaction-confirmed-by")).indexOf("user") !== -1, true);
    assertEq(subtreeText(field(ctx, "reaction-status")).indexOf("reaction_confirmed") !== -1, true);

    assertEq(subtreeText(field(ctx, "provenance-summary")).indexOf("记录 2 条") !== -1, true);
    assertEq(subtreeText(field(ctx, "provenance-summary")).indexOf("sig-abc") !== -1, true);
    assertEq(field(ctx, "provenance-json").textContent.indexOf("xtb") !== -1, true);
    assertEq(subtreeText(field(ctx, "provenance-notes")).indexOf("report note") !== -1, true);
    assertEq(field(ctx, "steps-empty").hidden, true);
    assertEq(field(ctx, "provenance-empty").hidden, true);
    assertEq(subtreeText(root(ctx)).indexOf("secret") === -1, true,
      "the study directory field is never rendered");
    assertEq(subtreeText(root(ctx)).indexOf("should-not-render") === -1, true);
  });

  await test("report unavailable: ready with a notice, steps fall back to study_json", async () => {
    const client = makeClient((pathname, opts) => {
      if (/\/report$/.test(pathname)) throw httpError(503, "report offline");
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    assertEq(stateOf(ctx), "ready");
    const notices = field(ctx, "study-notices");
    assertEq(notices.hidden, false);
    assertEq(subtreeText(notices).indexOf("研究报告不可用") !== -1, true);
    assertEq(subtreeText(notices).indexOf("report offline") !== -1, true);
    assert(!!find(ctx.host, "data-step-id", "step-1"), "study_json elementary step is the fallback");
    assertEq(field(ctx, "provenance-empty").hidden, false);
    assertEq(subtreeText(field(ctx, "reaction-reactants")).indexOf("CC") !== -1, true,
      "reaction data remains available");
    assertEq(attr(ctx, "data-acp-busy"), "none");
  });

  await test("reaction unavailable: ready with a notice and an explicit empty reaction state", async () => {
    const client = makeClient((pathname, opts) => {
      if (/\/reaction$/.test(pathname)) throw httpError(404, "no reaction");
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(field(ctx, "study-notices")).indexOf("反应定义读取失败") !== -1, true);
    assertEq(field(ctx, "reaction-empty").hidden, false);
    assertEq(field(ctx, "reaction-body").hidden, true);
    assert(!!find(ctx.host, "data-step-id", "step-1"));
  });

  await test("plan defaults: candidates from study steps, 0-based wire atoms, preview + local fingerprint", async () => {
    const ctx = await loadReady();
    const candidates = findAll(ctx.host, "data-role", "plan-step");
    assertEq(candidates.length, 1, "one elementary step candidate");
    assertEq(candidates[0].checked, true, "candidate selected by default");
    assertEq(candidates[0].getAttribute("data-step-key"), "step:step-1");

    const rows = findAll(ctx.host, "data-role", "coord-row");
    assertEq(rows.length, 2, "two coordinates from the study plan");
    const firstRow = rows[0];
    assertEq(firstRow.getAttribute("data-coord-id"), "step-1/rc1");
    const firstText = subtreeText(firstRow);
    ["step-1/rc1", "距离", "1, 2"].forEach((value) => {
      assert(firstText.indexOf(value) !== -1, "coordinate row missing " + value);
    });
    assertEq(coordInput(ctx, "coord-start", "step-1/rc1").value, "1.2");
    assertEq(coordInput(ctx, "coord-end", "step-1/rc1").value, "2.4");
    assertEq(coordRole(ctx, "step-1/rc1").value, "drive");
    assertEq(coordRole(ctx, "step-1/rc2").value, "monitor");

    const preview = planPreview(ctx);
    assertEq(preview.strategy, "guided-scan");
    assertEq(preview.fidelity, "s3");
    assertEq(preview.plan.points, 21);
    assertEq(preview.plan.coupling, "synchronous");
    assertEq(preview.plan.start_from, "reactant");
    assertEq(preview.plan.coordinates.length, 2);
    assertEq(preview.plan.coordinates[0].id, "step-1/rc1");
    assertEq(preview.plan.coordinates[0].kind, "distance");
    assertEq(preview.plan.coordinates[0].atoms, [0, 1], "wire atoms stay 0-based");
    assertEq(preview.plan.coordinates[0].role, "drive");
    assertEq(preview.plan.coordinates[0].start, 1.2);
    assertEq(preview.plan.coordinates[0].end, 2.4);
    assertEq(preview.plan.coordinates[1].role, "monitor");
    assertEq(subtreeText(firstRow).indexOf("1, 2") !== -1, true, "UI shows 1-based atoms");

    const fingerprint = subtreeText(field(ctx, "plan-fingerprint"));
    assert(fingerprint.indexOf("fnv1a:") !== -1, "local fingerprint rendered");
    assert(fingerprint.indexOf("服务端 plan_hash") !== -1, "local fingerprint is labelled non-authoritative");
    assertEq(action(ctx, "confirm-plan").disabled, false);
    assertEq(postCalls(ctx).length, 0, "rendering never confirms");
  });

  await test("plan role edit: monitor -> drive flags missing values, then accepts edited start/end", async () => {
    const ctx = await loadReady();
    const roleSelect = coordRole(ctx, "step-1/rc2");
    roleSelect.value = "drive";
    roleSelect.fire("change");
    await flush();

    assertEq(attr(ctx, "data-acp-plan-state"), "idle");
    assertEq(findAll(ctx.host, "data-error", "drive_start_end").length, 1,
      "drive without start/end is rejected");
    assertEq(action(ctx, "confirm-plan").disabled, true);
    action(ctx, "confirm-plan").fire("click");
    await flush();
    assertEq(postCalls(ctx).length, 0, "invalid plan must not be POSTed");
    assertEq(attr(ctx, "data-acp-plan-state"), "blocked");

    const start = coordInput(ctx, "coord-start", "step-1/rc2");
    start.value = "95.5";
    start.fire("change");
    await flush();
    const end = coordInput(ctx, "coord-end", "step-1/rc2");
    end.value = "140.25";
    end.fire("change");
    await flush();

    assertEq(findAll(ctx.host, "data-error", "drive_start_end").length, 0);
    assertEq(action(ctx, "confirm-plan").disabled, false);
    const preview = planPreview(ctx);
    const rc2 = preview.plan.coordinates.find((coordinate) => coordinate.id === "step-1/rc2");
    assertEq(rc2.role, "drive");
    assertEq(rc2.start, 95.5);
    assertEq(rc2.end, 140.25);
    assertEq(rc2.atoms, [0, 1, 2], "source atoms are preserved");
  });

  await test("plan step toggle: unchecking removes coordinates and blocks confirmation", async () => {
    const ctx = await loadReady();
    const checkbox = findAll(ctx.host, "data-role", "plan-step")[0];
    checkbox.checked = false;
    checkbox.fire("change");
    await flush();

    assertEq(findAll(ctx.host, "data-role", "coord-row").length, 0);
    assertEq(findAll(ctx.host, "data-error", "coordinates_missing").length, 1);
    assertEq(action(ctx, "confirm-plan").disabled, true);
    action(ctx, "confirm-plan").fire("click");
    await flush();
    assertEq(postCalls(ctx).length, 0);
    assertEq(attr(ctx, "data-acp-plan-state"), "blocked");

    const checkbox2 = findAll(ctx.host, "data-role", "plan-step")[0];
    checkbox2.checked = true;
    checkbox2.fire("change");
    await flush();
    assertEq(findAll(ctx.host, "data-role", "coord-row").length, 2);
    assertEq(action(ctx, "confirm-plan").disabled, false);
  });

  await test("direct-ts: coordinates are not required, switch enables confirmation", async () => {
    const ctx = await loadReady();
    const checkbox = findAll(ctx.host, "data-role", "plan-step")[0];
    checkbox.checked = false;
    checkbox.fire("change");
    await flush();

    const strategy = field(ctx, "strategy");
    strategy.value = "direct-ts";
    strategy.fire("change");
    await flush();

    assertEq(findAll(ctx.host, "data-error", "coordinates_missing").length, 0);
    assertEq(action(ctx, "confirm-plan").disabled, false);
    const preview = planPreview(ctx);
    assertEq(preview.strategy, "direct-ts");
    assertEq(preview.plan.coordinates, []);
    assertEq(preview.plan.points, 21);

    action(ctx, "confirm-plan").fire("click");
    await flush();
    const posts = postCalls(ctx);
    assertEq(posts.length, 1);
    const body = JSON.parse(posts[0].opts.body);
    assertEq(body.strategy, "direct-ts");
    assertEq(body.plan.coordinates, []);
    assertEq(attr(ctx, "data-acp-plan-state"), "confirmed");
  });

  await test("confirm: exact body, busy double-click guard, plan_hash receipt", async () => {
    const postDeferred = deferred();
    const client = makeClient((pathname, opts) => {
      if (opts && opts.method === "POST") return postDeferred.promise;
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });

    action(ctx, "confirm-plan").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-busy"), "confirm");
    assertEq(attr(ctx, "data-acp-plan-state"), "confirming");
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf("正在确认") !== -1, true);
    assertEq(action(ctx, "confirm-plan").disabled, true);
    action(ctx, "confirm-plan").fire("click");
    await flush(2);
    assertEq(postCalls(ctx).length, 1, "second click while confirming is ignored");

    const post = postCalls(ctx)[0];
    assertEq(post.path, STUDIES + "/study-1/mechanism/plan");
    assertEq(post.opts.method, "POST");
    assertEq(post.opts.headers["Content-Type"], "application/json");
    const body = JSON.parse(post.opts.body);
    assertEq(Object.keys(body).sort(), ["fidelity", "plan", "strategy"]);
    assertEq(body.strategy, "guided-scan");
    assertEq(body.fidelity, "s3");
    assertEq(Object.keys(body.plan).sort(), ["coordinates", "coupling", "points", "start_from"]);
    assertEq(body.plan.points, 21);
    assertEq(body.plan.coupling, "synchronous");
    assertEq(body.plan.start_from, "reactant");
    assertEq(body.plan.coordinates.length, 2);
    assertEq(body.plan.coordinates[0].id, "step-1/rc1");
    assertEq(body.plan.coordinates[0].atoms, [0, 1]);
    assertEq(body.plan.coordinates[0].role, "drive");
    assertEq(body.plan.coordinates[0].start, 1.2);
    assertEq(body.plan.coordinates[0].end, 2.4);

    postDeferred.resolve({ status: "plan_confirmed", plan_hash: DIGEST });
    await flush();
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-plan-state"), "confirmed");
    assertEq(attr(ctx, "data-acp-plan-status"), "confirmed");
    assertEq(field(ctx, "plan-receipt").hidden, false);
    assertEq(field(ctx, "plan-receipt").getAttribute("data-acp-receipt-state"), "confirmed");
    assertEq(subtreeText(field(ctx, "receipt-body")).indexOf("plan_confirmed") !== -1, true);
    assertEq(subtreeText(field(ctx, "receipt-hash")).indexOf(DIGEST) !== -1, true);
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf("计划已确认") !== -1, true);
    assertEq(subtreeText(field(ctx, "summary-plan")).indexOf("已确认") !== -1, true);
    assertEq(action(ctx, "confirm-plan").disabled, true, "same plan is not confirmed twice");
    assertEq(postCalls(ctx).length, 1);
  });

  await test("confirm 410: retired route is surfaced, never shown as success", async () => {
    const RETIRED = "该机制研究端点已退役，历史只读";
    const client = makeClient((pathname, opts) => {
      if (opts && opts.method === "POST") throw httpError(410, RETIRED);
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    action(ctx, "confirm-plan").fire("click");
    await flush();

    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(attr(ctx, "data-acp-plan-state"), "error");
    assertEq(attr(ctx, "data-acp-plan-status"), "reaction_locked",
      "a failed confirmation does not flip the study plan status");
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf("已退役") !== -1, true);
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf("410") !== -1, true);
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf(RETIRED) !== -1, true);
    assertEq(field(ctx, "plan-receipt").hidden, false);
    assertEq(field(ctx, "plan-receipt").getAttribute("data-acp-receipt-state"), "error");
    assertEq(subtreeText(field(ctx, "receipt-body")).indexOf(RETIRED) !== -1, true);
    assertEq(subtreeText(root(ctx)).indexOf("计划已确认") === -1, true, "no fake success");
    assertEq(postCalls(ctx).length, 1);
  });

  await test("confirm errors: 409 / 422 / 500 all clear busy and keep an error surface", async () => {
    const cases = [
      [409, "reaction is not locked", "未锁定"],
      [422, "coordinate 0 is invalid", "coordinate 0 is invalid"],
      [500, "server boom", "server boom"]
    ];
    for (const [status, detail, expected] of cases) {
      const client = makeClient((pathname, opts) => {
        if (opts && opts.method === "POST") throw httpError(status, detail);
        return defaultResponse(pathname, opts);
      });
      const ctx = await loadReady({ client });
      action(ctx, "confirm-plan").fire("click");
      await flush();
      assertEq(attr(ctx, "data-acp-busy"), "none", "busy clears for " + status);
      assertEq(attr(ctx, "data-acp-plan-state"), "error", "error state for " + status);
      assertEq(subtreeText(field(ctx, "confirm-status")).indexOf(expected) !== -1, true,
        "detail surfaced for " + status);
      assertEq(field(ctx, "plan-receipt").getAttribute("data-acp-receipt-state"), "error");
      assertEq(postCalls(ctx).length, 1);
    }
  });

  await test("confirm malformed response: explicit error, never confirmed", async () => {
    const client = makeClient((pathname, opts) => {
      if (opts && opts.method === "POST") return {};
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    action(ctx, "confirm-plan").fire("click");
    await flush();
    assertEq(attr(ctx, "data-acp-plan-state"), "error");
    assertEq(attr(ctx, "data-acp-busy"), "none");
    assertEq(subtreeText(field(ctx, "confirm-status")).indexOf("无法识别") !== -1, true);
    assertEq(attr(ctx, "data-acp-plan-status"), "reaction_locked");
  });

  await test("generation/dispose: late detail result cannot paint, dispose is idempotent, remount works", async () => {
    const firstDetail = deferred();
    let detailCalls = 0;
    const client = makeClient((pathname, opts) => {
      if (pathname === STUDIES + "/study-1") {
        detailCalls += 1;
        if (detailCalls === 1) return firstDetail.promise;
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client });
    await mount(ctx);
    field(ctx, "study-id").value = "study-1";
    action(ctx, "load-study").fire("click");
    await flush(2);
    assertEq(stateOf(ctx), "loading");

    ctx.instance.dispose();
    assertEq(ctx.host.children.length, 0, "dispose removes its DOM");
    assertEq(ctx.dom.document.activeDocListeners(), 0, "dispose removes the language listener");
    assertEq(ctx.context == null ? 0 : ctx.context.listenerCount(), 0);
    firstDetail.resolve(detailPayload("study-1"));
    await flush();
    assertEq(ctx.host.children.length, 0, "late detail result creates no DOM");
    ctx.instance.dispose();
    ctx.instance.dispose();

    await ctx.instance.mount(ctx.host, { context: null });
    await flush();
    assertEq(stateOf(ctx), "empty");
    assertEq(root(ctx).className, "acp-mechplan");
    await loadViaUi(ctx, "study-1");
    assertEq(stateOf(ctx), "ready");
    assertEq(detailCalls, 2);
  });

  await test("generation/dispose: late confirmation result is dropped after dispose", async () => {
    const postDeferred = deferred();
    const client = makeClient((pathname, opts) => {
      if (opts && opts.method === "POST") return postDeferred.promise;
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    action(ctx, "confirm-plan").fire("click");
    await flush(2);
    assertEq(attr(ctx, "data-acp-plan-state"), "confirming");
    ctx.instance.dispose();
    postDeferred.resolve({ status: "plan_confirmed", plan_hash: DIGEST });
    await flush();
    assertEq(ctx.host.children.length, 0);
    assertEq(ctx.dom.document.activeDocListeners(), 0);
  });

  await test("task focus change: new job clears the old study, loads its list, resets the plan", async () => {
    const context = makeContext("job-1");
    const client = makeClient((pathname, opts) => {
      if (pathname.indexOf(STUDIES + "?") === 0) {
        if (pathname.indexOf("job_id=job-1") !== -1) return [summary("study-1", "job-1")];
        if (pathname.indexOf("job_id=job-2") !== -1) return [summary("study-2", "job-2")];
        return [];
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client, context });
    await mount(ctx);
    assertEq(subtreeText(field(ctx, "summary-id")), "study-1");

    context.setFocus({ taskRef: { jobId: "job-2", attempt: 1 } });
    await flush();
    assert(ctx.client.calls.some((call) => call.path === STUDIES + "?job_id=job-2&limit=50"));
    assertEq(subtreeText(field(ctx, "summary-id")), "study-2");
    assertEq(attr(ctx, "data-acp-study"), "study-2");
    const listCalls = ctx.client.calls.filter((call) => call.path.indexOf(STUDIES + "?") === 0);
    assertEq(listCalls.length, 2);

    context.setFocus({ taskRef: null });
    await flush();
    assertEq(stateOf(ctx), "empty");
    assertEq(field(ctx, "summary-section") == null || field(ctx, "summary-section").hidden, true);
    assertEq(attr(ctx, "data-acp-study"), "");
    assertEq(ctx.client.calls.filter((call) => call.path.indexOf(STUDIES + "?") === 0).length, 2,
      "clearing the focus does not issue another list request");
  });

  await test("study id is encoded into the existing route, never concatenated as a path", async () => {
    const ctx = setup();
    await mount(ctx);
    await loadViaUi(ctx, "study 1/x");
    assertEq(ctx.client.calls[0].path, STUDIES + "/study%201%2Fx");
    assertEq(ctx.client.calls[1].path, STUDIES + "/study%201%2Fx/report");
    assertEq(ctx.client.calls[2].path, STUDIES + "/study%201%2Fx/reaction");
  });

  await test("empty study: ready with explicit empty steps/roles/reaction/provenance and a blocked plan", async () => {
    const client = makeClient((pathname, opts) => {
      if (pathname === STUDIES + "/empty-study") {
        return {
          id: "empty-study", job_id: null, status: "draft",
          created_at: "", updated_at: "", unified_status: "DRAFT",
          study_json: {}, decisions: []
        };
      }
      if (/\/report$/.test(pathname)) return {};
      if (/\/reaction$/.test(pathname)) return { reaction: null, status: "draft" };
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client });
    await mount(ctx);
    await loadViaUi(ctx, "empty-study");

    assertEq(stateOf(ctx), "ready");
    assertEq(field(ctx, "steps-empty").hidden, false);
    assertEq(field(ctx, "steps-table").parentNode.hidden, true);
    assertEq(subtreeText(field(ctx, "role-count-reactant")), "0");
    assertEq(field(ctx, "reaction-empty").hidden, false);
    assertEq(field(ctx, "provenance-empty").hidden, false);
    assertEq(findAll(ctx.host, "data-role", "plan-step").length, 0);
    assertEq(subtreeText(field(ctx, "candidates")).indexOf("没有可复用的反应坐标计划") !== -1, true);
    assertEq(findAll(ctx.host, "data-error", "coordinates_missing").length, 1);
    assertEq(action(ctx, "confirm-plan").disabled, true);
    assertEq(planPreview(ctx).plan.coordinates, []);
    assertEq(attr(ctx, "data-acp-plan-status"), "pending");
    assertEq(subtreeText(field(ctx, "summary-plan")).indexOf("未确认") !== -1, true);
  });

  await test("plan_confirmed study: summary shows an already-confirmed plan", async () => {
    const client = makeClient((pathname, opts) => {
      if (pathname === STUDIES + "/study-1") {
        return Object.assign(detailPayload("study-1"), {
          status: "plan_confirmed",
          unified_status: "PLAN_CONFIRMED"
        });
      }
      return defaultResponse(pathname, opts);
    });
    const ctx = await loadReady({ client });
    assertEq(attr(ctx, "data-acp-plan-status"), "confirmed");
    assertEq(subtreeText(field(ctx, "summary-plan")).indexOf("已确认") !== -1, true);
    assertEq(subtreeText(field(ctx, "summary-plan")).indexOf("plan_confirmed") !== -1, true);
  });

  await test("no client seam: an explicit error is shown and no request is attempted", async () => {
    const dom = makeDom();
    const sandbox = makeSandbox({});
    const mod = loadModule(sandbox);
    const host = dom.document.createElement("div");
    const instance = mod.factory({ doc: dom.document });
    await instance.mount(host, {});
    await flush();
    const input = find(host, "data-role", "study-id");
    input.value = "study-1";
    find(host, "data-action", "load-study").fire("click");
    await flush();
    const rootNode = host.children[0];
    assertEq(rootNode.getAttribute("data-acp-module-state"), "error");
    assertEq(find(host, "data-role", "overlay-detail").textContent.indexOf("客户端未注入") !== -1, true);
    assertEq(find(host, "data-action", "retry").hidden, false);
    instance.dispose();
  });

  await test("pure internals: normalisers, roles, steps fallback, plan validation matrix", async () => {
    const internals = setup().mod._internals;
    assertEq(internals.normalizeStudyList([{ id: "a" }, { study_id: "b" }]).map((item) => item.id), ["a", "b"]);
    assertEq(internals.normalizeStudyList({ studies: [{ id: "c" }] })[0].id, "c");
    assertEq(internals.normalizeStudyList(null), []);

    assertEq(internals.classifyRole("reactant"), "reactant");
    assertEq(internals.classifyRole("product"), "product");
    assertEq(internals.classifyRole("intermediate"), "intermediate");
    assertEq(internals.classifyRole("transition_state"), "ts");
    assertEq(internals.classifyRole("TS"), "ts");
    assertEq(internals.classifyRole(""), "unknown");
    assertEq(internals.classifyRole("something-else"), "unknown");

    const detail = internals.normalizeDetail(detailPayload("study-1"));
    assertEq(detail.id, "study-1");
    assertEq(detail.decisions.length, 1);
    const buckets = internals.collectRoleBuckets(detail, null);
    assertEq(buckets.reactant.length, 1);
    assertEq(buckets.product.length, 1);
    assertEq(buckets.intermediate.length, 1);
    assertEq(buckets.ts.length, 1);
    assertEq(internals.collectRoleBuckets(null, null).unknown.length, 0);

    const steps = internals.collectSteps(detail, null);
    assertEq(steps.length, 1);
    assertEq(steps[0].sourceState, "s1");
    assertEq(steps[0].sinkState, "s2");
    assertEq(steps[0].coordinatePlan.coordinates.length, 2);

    const candidates = internals.buildCandidates(detail);
    assertEq(candidates.length, 1);
    assertEq(candidates[0].key, "step:step-1");
    assertEq(candidates[0].strategy, "guided-scan");
    assertEq(candidates[0].fidelity, "s3");
    const routeOnly = internals.buildCandidates({
      studyJson: {
        routes: [{ route_id: "route-9", path_strategy: "rph-reverse", fidelity: "s4", coordinate_plan: coordinatePlan() }]
      }
    });
    assertEq(routeOnly.length, 1);
    assertEq(routeOnly[0].key, "route:route-9");
    assertEq(routeOnly[0].strategy, "rph-reverse");
    assertEq(routeOnly[0].fidelity, "s4");

    const selection = {};
    candidates.forEach((candidate) => { selection[candidate.key] = true; });
    const baseState = {
      selection: selection, points: "21", strategy: "guided-scan", fidelity: "s3",
      startFrom: "reactant", roleOverrides: {}, startOverrides: {}, endOverrides: {}
    };
    const body = internals.buildPlanBody(candidates, baseState);
    assertEq(internals.validatePlanBody(body).ok, true);
    assertEq(body.plan.coordinates[0].id, "step-1/rc1");
    assertEq(body.plan.coordinates[0].atoms, [0, 1]);
    assertEq(body.plan.coordinates[0].start, 1.2);
    assertEq(body.plan.coordinates[0].end, 2.4);
    assertEq(body.plan.coupling, "synchronous");

    const roleBody = internals.buildPlanBody(candidates, Object.assign({}, baseState, {
      roleOverrides: { "step-1/rc2": "freeze" },
      startOverrides: { "step-1/rc2": "120" }
    }));
    assertEq(internals.validatePlanBody(roleBody).ok, true);
    assertEq(roleBody.plan.coordinates[1].role, "freeze");
    assertEq(roleBody.plan.coordinates[1].start, 120);

    function codes(overrides) {
      const next = JSON.parse(JSON.stringify(body));
      overrides(next);
      return internals.validatePlanBody(next).errors.map((error) => error.code);
    }
    assertEq(codes((next) => { next.strategy = "bogus"; }), ["strategy_invalid"]);
    assertEq(codes((next) => { next.fidelity = "s5"; }), ["fidelity_invalid"]);
    assertEq(codes((next) => { next.plan.points = 1; }), ["points_invalid"]);
    assertEq(codes((next) => { next.plan.points = "x"; }), ["points_invalid"]);
    assertEq(codes((next) => { next.plan.coupling = "serial"; }), ["coupling_invalid"]);
    assertEq(codes((next) => { next.plan.start_from = "bogus"; }), ["start_from_invalid"]);
    assertEq(codes((next) => { next.plan.coordinates = []; }), ["coordinates_missing"]);
    assertEq(codes((next) => { next.plan.coordinates = "x"; }), ["coordinates_invalid"]);
    assertEq(codes((next) => { next.plan.coordinates = [null]; }), ["coordinate_invalid", "drive_missing"]);
    assertEq(codes((next) => { next.plan.coordinates[0].id = ""; }), ["coordinate_id_missing"]);
    assertEq(codes((next) => { next.plan.coordinates[0].kind = "torsion"; }), ["coordinate_kind_invalid"]);
    assertEq(codes((next) => { next.plan.coordinates[0].atoms = [0]; }), ["coordinate_atoms_invalid"]);
    assertEq(codes((next) => { next.plan.coordinates[0].role = "push"; }),
      ["coordinate_role_invalid", "drive_missing"]);
    assertEq(codes((next) => { next.plan.coordinates[0].end = null; }), ["drive_start_end"]);
    assertEq(codes((next) => {
      next.plan.coordinates[1].role = "freeze";
      next.plan.coordinates[1].start = null;
    }), ["freeze_start"]);
    const monitorOnly = codes((next) => { next.plan.coordinates[0].role = "monitor"; });
    assertEq(monitorOnly, ["drive_missing"]);
    const direct = JSON.parse(JSON.stringify(body));
    direct.strategy = "direct-ts";
    direct.plan.coordinates = [];
    assertEq(internals.validatePlanBody(direct).ok, true, "direct-ts requires no coordinates");

    assertEq(internals.formatAtoms([0, 2]), "1, 3");
    assertEq(internals.studyPath("a b/c"), API + "/mechanism-studies/a%20b%2Fc");
    assertEq(internals.listQuery("job 1"), "?job_id=job%201&limit=50");
    assertEq(internals.listQuery(""), "?limit=50");
    assertEq(internals.localFingerprint("abc"), internals.localFingerprint("abc"));
    assert(internals.localFingerprint("abc") !== internals.localFingerprint("abd"));
    assertEq(internals.planStatusInfo(detail, { status: "plan_confirmed", planHash: DIGEST }).hash, DIGEST);
    assertEq(internals.planStatusInfo({ status: "reaction_confirmed", unifiedStatus: "RESUMABLE" }, null).state,
      "reaction_locked");
  });

  await test("i18n: every next.mechplan literal exists in the local dictionary, data-i18n keys resolve", async () => {
    const ctx = await loadReady();
    const source = fs.readFileSync(path.resolve(__dirname, "..", "modules", "mechanism-plan", "module.js"), "utf8");
    const i18n = ctx.mod._internals.i18n;
    const literalRe = /"(next\.mechplan\.[A-Za-z0-9_.]+)"/g;
    const keys = new Set();
    let match;
    while ((match = literalRe.exec(source)) != null) {
      if (!match[1].endsWith(".")) keys.add(match[1]);
    }
    assert(keys.size > 80, "expected a substantial key set, got " + keys.size);
    keys.forEach((key) => {
      assert(Object.prototype.hasOwnProperty.call(i18n, key), "missing local i18n key: " + key);
    });
    Object.keys(i18n).forEach((key) => {
      assert(key.indexOf("next.mechplan.") === 0, "unexpected namespace: " + key);
      assert(typeof i18n[key] === "string" && i18n[key].length > 0, "empty zh value: " + key);
    });
    const domKeys = collectAttr(root(ctx), "data-i18n");
    assert(domKeys.length > 10, "static chrome carries data-i18n");
    domKeys.forEach((key) => {
      assert(Object.prototype.hasOwnProperty.call(i18n, key), "mounted data-i18n key missing: " + key);
    });
    assertEq(subtreeText(root(ctx)).indexOf("研究概览") !== -1, true);

    ctx.dom.document.dispatchEvent({ type: "acp:langchange" });
    await flush(1);
    assertEq(stateOf(ctx), "ready");
    assertEq(subtreeText(root(ctx)).indexOf("机理计划装配") !== -1, true, "zh fallback survives langchange");
  });

  await test("only existing mechanism-study routes are requested; the only POST is the plan confirm", async () => {
    const context = makeContext("job-9");
    const client = makeClient((pathname, opts) => {
      if (pathname.indexOf(STUDIES + "?") === 0) return [summary("study-1", "job-9")];
      return defaultResponse(pathname, opts);
    });
    const ctx = setup({ client, context });
    await mount(ctx);
    action(ctx, "confirm-plan").fire("click");
    await flush();

    const allowed = [
      new RegExp("^" + STUDIES.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\?limit=50$"),
      new RegExp("^" + STUDIES.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\?job_id=[^&]+&limit=50$"),
      new RegExp("^" + STUDIES.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "/[^/]+$"),
      new RegExp("^" + STUDIES.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "/[^/]+/(report|reaction)$"),
      new RegExp("^" + STUDIES.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "/[^/]+/mechanism/plan$")
    ];
    ctx.client.calls.forEach((call) => {
      assert(allowed.some((re) => re.test(call.path)), "unexpected route: " + call.path);
      assert(call.path.indexOf("/api/v2") === -1, "no v2 route");
      assert(call.path.indexOf("/jobs") === -1, "no task route");
      assert(call.path.indexOf("/preflight") === -1, "no preflight route");
      assert(call.path.indexOf("/s2/") === -1, "no s2 route");
      assert(call.path.indexOf("/pes/") === -1, "no PES route");
      assert(call.path.indexOf("/batch") === -1, "no batch route");
    });
    const methods = ctx.client.calls.map((call) => (call.opts && call.opts.method) || "GET");
    assertEq(methods.filter((method) => method !== "GET"), ["POST"]);
    const posts = postCalls(ctx);
    assertEq(posts.length, 1);
    assert(posts[0].path.endsWith("/mechanism/plan"));
    assertEq(JSON.parse(posts[0].opts.body).plan.coordinates.length, 2);
  });

  await test("source pins: no direct network/filesystem/A-50/A-51 surface, only ACPNextModules is written", async () => {
    const source = fs.readFileSync(path.resolve(__dirname, "..", "modules", "mechanism-plan", "module.js"), "utf8");
    const forbidden = [
      "fetch(", "window.fetch", "XMLHttpRequest", "sendBeacon", "navigator.",
      "study_dir", "mechanism_study/", "path.join", "require(", "import(",
      "innerHTML", "document.write", "jobCommands",
      "/jobs", "/preflight", "/s2/", "/pes/", "/batch",
      "$3Dmol", "ACPVisualization", "scene_instance", "createViewer", "canvas",
      "ACPGeometryStore", "structure_editor", "geometry_store", "ACPWorkBench"
    ];
    forbidden.forEach((token) => {
      assert(source.indexOf(token) === -1, "forbidden token in module source: " + token);
    });
    const writes = [];
    const writeRe = /window\.([A-Za-z_$][\w$]*)\s*=(?!=)/g;
    let match;
    while ((match = writeRe.exec(source)) != null) writes.push(match[1]);
    assertEq([...new Set(writes)], ["ACPNextModules"], "the module writes exactly one global");
    assert(source.indexOf("data-acp-module-state") !== -1);
    assert(source.indexOf("data-acp-busy") !== -1);
    assert(source.indexOf("data-acp-plan-state") !== -1);
  });
};
