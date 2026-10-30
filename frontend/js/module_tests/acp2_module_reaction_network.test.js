/* ACP 2.0 module test �?reaction-network (UI-04, read-only).
 *
 * Pinned:
 *   - BFS depth layout (linear chain, cycle fallback, unknown edges dropped);
 *   - deterministic SVG: node labels/roles/energies, barrier labels, escaped
 *     text, data-node/data-edge hooks and selection classes;
 *   - module flow: no task �?empty; task without a study �?explicit "no study"
 *     empty state (the legacy-data situation); study report �?ready + SVG;
 *     node click publishes networkNodeId and shows the frozen role/energy;
 *     edge click shows barrier/fidelity/status; a server error is local;
 *   - dispose clears the host and ignores a late response.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const MODULE_PATH = "js/modules/reaction-network/module.js";

  const flush = async (n = 12) => {
    for (let i = 0; i < n; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < 4; i++) await Promise.resolve();
  };

  function makeDom() {
    const docListeners = new Map();
    function makeEl(tag) {
      const listeners = new Map();
      return {
        tagName: String(tag).toUpperCase(),
        children: [], parentNode: null, className: "", textContent: "", innerHTML: "",
        hidden: false, disabled: false, attrs: {}, listeners,
        setAttribute(k, v) { this.attrs[k] = String(v); },
        getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
        removeAttribute(k) { delete this.attrs[k]; },
        appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
        replaceChildren(...nodes) {
          this.children.forEach((c) => { c.parentNode = null; });
          this.children = nodes.slice();
          nodes.forEach((n) => { n.parentNode = this; });
        },
        addEventListener(type, fn) {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type).add(fn);
        },
        removeEventListener(type, fn) {
          if (listeners.has(type)) listeners.get(type).delete(fn);
        },
        fire(type, ev) {
          const set = listeners.get(type);
          if (set) [...set].forEach((fn) => fn(Object.assign({ type, target: this }, ev || {})));
        },
      };
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
      documentElement: makeEl("html"),
      activeDocListeners() {
        let n = 0;
        docListeners.forEach((s) => { n += s.size; });
        return n;
      },
    };
    return { document };
  }

  function walk(node, out = []) {
    (node.children || []).forEach((c) => { out.push(c); walk(c, out); });
    return out;
  }

  function find(root, attr, value) {
    return walk(root).find((n) => n.getAttribute && n.getAttribute(attr) === value) || null;
  }

  const NETWORK = {
    nodes: [
      { state_id: "R", role: "reactant", label: "reactant", energy_hartree: -154.1 },
      { state_id: "TS1", role: "ts", label: "TS1", energy_hartree: -154.05 },
      { state_id: "P", role: "product", label: "product", energy_hartree: -154.2 },
    ],
    edges: [
      { source_state_id: "R", sink_state_id: "TS1", step_id: "step1", barrier_forward_kcal: 12.5, fidelity: "s2", status: "confirmed" },
      { source_state_id: "TS1", sink_state_id: "P", step_id: "step2", barrier_forward_kcal: 3.2, fidelity: "s2", status: "confirmed" },
    ],
  };

  function load() {
    const dom = makeDom();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom };
  }

  function makeContext(projectId) {
    const subs = [];
    const focus = [];
    let snapshot = { projectId, taskRef: null, revision: 0 };
    return {
      focus,
      getSnapshot: () => snapshot,
      subscribe(fn) { subs.push(fn); return () => { const i = subs.indexOf(fn); if (i >= 0) subs.splice(i, 1); }; },
      setFocus(patch) { focus.push(patch); snapshot = Object.assign({}, snapshot, patch); return snapshot; },
      setTask(ref) {
        snapshot = Object.assign({}, snapshot, { taskRef: ref, revision: snapshot.revision + 1 });
        subs.forEach((fn) => fn(snapshot));
      },
      count: () => subs.length,
    };
  }

  function makeRequest(overrides) {
    overrides = overrides || {};
    const calls = [];
    return {
      calls,
      request(path, opts) {
        calls.push({ path, opts });
        if (overrides.handler) {
          const custom = overrides.handler(path, opts);
          if (custom !== undefined) return custom;
        }
        if (path.indexOf("/mechanism-studies?job_id=") !== -1) return Promise.resolve([{ study_id: "study-1" }]);
        if (path.indexOf("/mechanism-studies/study-1/report") !== -1) {
          return Promise.resolve({ study_id: "study-1", reaction_network: NETWORK });
        }
        return Promise.resolve({});
      },
    };
  }

  await test("reaction-network: BFS depth layout survives a cycle and drops unknown edges", () => {
    const { sandbox } = load();
    const lib = sandbox.ACPNextReactionNetwork;
    const linear = lib.layout(NETWORK);
    assertEq(linear.nodes.map((n) => n.id + ":" + n.depth), ["R:0", "TS1:1", "P:2"]);
    assertEq(linear.edges.length, 2);

    const cyclic = lib.layout({
      nodes: [{ state_id: "A" }, { state_id: "B" }],
      edges: [{ source_state_id: "A", sink_state_id: "B" }, { source_state_id: "B", sink_state_id: "A" }, { source_state_id: "A", sink_state_id: "ghost" }],
    });
    assertEq(cyclic.nodes.length, 2, "cycle does not duplicate nodes");
    assertEq(cyclic.edges.length, 2, "unknown target edge dropped");
    assert(cyclic.width > 0 && cyclic.height > 0, "finite canvas");
  });

  await test("reaction-network: SVG is deterministic, escaped and selection-aware", () => {
    const { sandbox } = load();
    const lib = sandbox.ACPNextReactionNetwork;
    const base = lib.buildSvg(NETWORK, {});
    assert(base.svg.indexOf("<svg") === 0, "svg root");
    assert(base.svg.indexOf('data-node="R"') !== -1, "node hook");
    assert(base.svg.indexOf('data-edge="0"') !== -1, "edge hook");
    assert(base.svg.indexOf("12.5 kcal/mol") !== -1, "barrier label");
    assert(base.svg.indexOf("is-reactant") !== -1 && base.svg.indexOf("is-ts") !== -1, "role classes");
    assert(base.svg.indexOf("is-selected") === -1, "nothing selected by default");

    const selected = lib.buildSvg(NETWORK, { selectedNodeId: "TS1" });
    assert(selected.svg.indexOf('class="acp-net-node is-ts is-selected"') !== -1, "selected node class");

    const hostile = lib.buildSvg({
      nodes: [{ state_id: "X", role: "reactant", label: "<script>alert(1)</script>" }],
      edges: [],
    }, {});
    assert(hostile.svg.indexOf("<script>") === -1, "label escaped");
    assert(hostile.svg.indexOf("&lt;script&gt;") !== -1, "escaped text present");
  });

  await test("reaction-network: no task and no study are explicit empty states", async () => {
    const { sandbox, dom } = load();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a");
    const request = makeRequest({ handler: (path) => (path.indexOf("/mechanism-studies?job_id=") !== -1 ? Promise.resolve([]) : undefined) });
    const instance = sandbox.ACPNextModules["reaction-network"].factory({ doc: dom.document, request: request.request, context });
    await instance.mount(host, { context });
    await flush();
    let root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");

    context.setTask({ jobId: "job-1", attempt: 1 });
    await flush();
    root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    const stateNode = find(host, "aria-live", "polite");
    assert(!!stateNode, "state line exists");
    assert(String(stateNode.textContent).indexOf("没有关联") !== -1, "no-study copy");
    instance.dispose();
  });

  await test("reaction-network: study report renders, node click publishes focus, edge click shows barriers", async () => {
    const { sandbox, dom } = load();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a");
    const request = makeRequest();
    const instance = sandbox.ACPNextModules["reaction-network"].factory({ doc: dom.document, request: request.request, context });
    await instance.mount(host, { context });
    context.setTask({ jobId: "job-1", attempt: 1 });
    await flush();

    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const svgHost = walk(root).find((n) => typeof n.innerHTML === "string" && n.innerHTML.indexOf("<svg") !== -1);
    assert(!!svgHost, "svg rendered into the canvas");
    assert(svgHost.innerHTML.indexOf("data-node=\"R\"") !== -1, "node markup");

    const fakeNode = { getAttribute: (k) => (k === "data-node" ? "R" : null) };
    canvasClick(svgHost, fakeNode);
    await flush();
    assertEq(context.focus.length, 1, "networkNodeId published");
    assertEq(context.focus[0].networkNodeId, "R");
    const detail = walk(root).find((n) => n.className === "acp-net-detail" && n.hidden === false);
    assert(!!detail, "node detail visible");

    const fakeEdge = { getAttribute: (k) => (k === "data-edge" ? "0" : null) };
    canvasClick(svgHost, fakeEdge);
    await flush();
    const text = JSON.stringify(walk(root).map((n) => n.textContent));
    assert(text.indexOf("正向能垒") !== -1, "barrier label row");
    assert(text.indexOf("12.5") !== -1, "barrier value rendered");
    instance.dispose();
  });

  function canvasClick(canvas, target) {
    canvas.fire("click", { target });
  }

  await test("reaction-network: server failure is a local error state with retry", async () => {
    const { sandbox, dom } = load();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a");
    const request = makeRequest({
      handler(path) {
        if (path.indexOf("/mechanism-studies?job_id=") !== -1) {
          const error = new Error("boom");
          error.status = 500;
          return Promise.reject(error);
        }
        return undefined;
      },
    });
    const instance = sandbox.ACPNextModules["reaction-network"].factory({ doc: dom.document, request: request.request, context });
    await instance.mount(host, { context });
    context.setTask({ jobId: "job-1", attempt: 1 });
    await flush();
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    const retry = walk(root).find((n) => n.tagName === "BUTTON");
    assert(!!retry, "retry control");
    instance.dispose();
  });

  await test("reaction-network: dispose clears the host, unsubscribes and drops a late response", async () => {
    const { sandbox, dom } = load();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a");
    let release;
    const request = makeRequest({
      handler(path) {
        if (path.indexOf("/mechanism-studies?job_id=") !== -1) {
          return new Promise((resolve) => { release = () => resolve([{ study_id: "study-1" }]); });
        }
        return undefined;
      },
    });
    const instance = sandbox.ACPNextModules["reaction-network"].factory({ doc: dom.document, request: request.request, context });
    await instance.mount(host, { context });
    context.setTask({ jobId: "job-1", attempt: 1 });
    await flush();
    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(context.count(), 0, "context unsubscribed");
    assertEq(dom.document.activeDocListeners(), 0, "language listener removed");
    if (release) release();
    await flush();
    assertEq(host.children.length, 0, "late response did not remount");
  });
};
