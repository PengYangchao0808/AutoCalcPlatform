/* ACP 2.0 module test — reaction-network (UI-04, read-only).
 *
 * Pinned:
 *   - component-aware fallback layout, deterministic isolated-node grid, ELK
 *     right-hand layered options (LEFT → RIGHT), local UMD script order and Cytoscape
 *     pan/zoom/fit/re-layout controls with click navigation;
 *   - deterministic SVG: node labels/roles/energies, barrier labels, escaped
 *     text, data-node/data-edge hooks and selection classes;
 *   - project chemistry network (contract §4/§6): the default read hits
 *     GET /api/v2/projects/{id}/chemistry-network?limit=200, a pluggable
 *     `networkSource` adapter replaces it with the same wire payload, and the
 *     wire→graph mapping keeps identity honest (node_id only, closed relation
 *     enum, no speculative edges, extra fields ignored);
 *   - identity banner: structure_content_v1 never says 分子网络, molecule_v1
 *     does, `mixed` says 分子网络（含未识别结构） plus both identity counts, an
 *     unknown identity_kind is shown raw;
 *   - molecule-identity levels (batch B1d-frontend): per-node `identity_kind`,
 *     `scheme`/`canonical_smiles`/`inchikey`/`multiplicity` carried through,
 *     `moleculeIdentityKey` on a molecule node vs `contentChecksum` on a
 *     checksum node, and the canonical-SMILES detail row;
 *   - legacy mechanism fallback fires ONLY without a projectId and is labelled;
 *   - four states: 未连接 (transport/503), 空态, 就绪, 错误+重试 — a rejected
 *     read never renders as an empty success, retry replays the same query;
 *   - node click → commands.openStructure, lineage edge click →
 *     commands.openTask (guarded, never throws);
 *   - read-only: every recorded request is a GET on a read path (no writes);
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

  function text(node) {
    let out = node.textContent || "";
    (node.children || []).forEach((c) => { out += text(c); });
    return out;
  }

  function banner(root) {
    return walk(root).find((n) => String(n.className || "").indexOf("acp-net-source") !== -1) || null;
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

  /** Project chemistry-network wire payload (contract §4). */
  const WIRE = {
    project_id: "proj-a",
    identity_kind: "structure_content_v1",
    nodes: [
      {
        node_id: "sha256:aaa",
        identity_kind: "structure_content_v1",
        task_ids: ["job-1", "job-11"],
        molecule_names: ["caffeine"],
        formula: "C8H10N4O2",
        charge: 0,
        atom_count: 24,
        unpromised_field: "ignored",
      },
      { node_id: "sha256:bbb", task_ids: ["job-2"], molecule_names: [], formula: "C8H10N4O2" },
      { node_id: "", task_ids: ["job-3"] },
      { task_ids: ["job-4"] },
    ],
    edges: [
      { relation: "lineage", from_task: "job-1", to_task: "job-2", depth: 1 },
      { relation: "shared_structure", from_task: "job-1", to_task: "job-2" },
      { relation: "guessed_by_ui", from_task: "job-1", to_task: "job-2" },
      { relation: "lineage", from_task: "job-99", to_task: "job-2" },
    ],
    skipped: [{ reason: "missing_structure_hash", task_id: "job-3" }],
    truncated: true,
    next_cursor: "cursor-1",
  };

  function load() {
    const dom = makeDom();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    return { sandbox, dom };
  }

  function makeContext(projectId, projectName) {
    const subs = [];
    const focus = [];
    let snapshot = { projectId: projectId || null, projectName: projectName || null, taskRef: null, revision: 0 };
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
        if (path.indexOf("/chemistry-network") !== -1) return Promise.resolve(WIRE);
        if (path.indexOf("/mechanism-studies?job_id=") !== -1) return Promise.resolve([{ study_id: "study-1" }]);
        if (path.indexOf("/mechanism-studies/study-1/report") !== -1) {
          return Promise.resolve({ study_id: "study-1", reaction_network: NETWORK });
        }
        return Promise.resolve({});
      },
    };
  }

  function makeCommands() {
    const calls = [];
    return {
      calls,
      openStructure(ref) { calls.push({ command: "openStructure", ref }); return { ok: true }; },
      openTask(ref) { calls.push({ command: "openTask", ref }); return { ok: true }; },
    };
  }

  function makeCytoscape() {
    const instances = [];
    const uses = [];
    function cytoscape(options) {
      const handlers = new Map();
      const elements = (options.elements || []).map((raw) => {
        let position = Object.assign({}, raw.position || { x: 0, y: 0 });
        let selected = false;
        return {
          raw,
          id: () => raw.data.id,
          data: (key) => raw.data[key],
          position(next) { if (next) position = { x: next.x, y: next.y }; return Object.assign({}, position); },
          select() { selected = true; },
          unselect() { selected = false; },
          selected: () => selected,
        };
      });
      const cy = {
        options,
        layouts: [],
        fitCalls: [],
        zoomCalls: [],
        zoomLevel: 1,
        resizeCalls: 0,
        destroyed: false,
      };
      function collection(items) {
        return {
          items,
          length: items.length,
          filter(fn) { return collection(items.filter(fn)); },
          add(other) {
            const merged = items.slice();
            other.items.forEach((item) => { if (merged.indexOf(item) === -1) merged.push(item); });
            return collection(merged);
          },
          unselect() { items.forEach((item) => item.unselect()); return this; },
          boundingBox() {
            const points = items.map((item) => item.position());
            return {
              x1: Math.min(...points.map((p) => p.x - 10)),
              y1: Math.min(...points.map((p) => p.y - 10)),
              x2: Math.max(...points.map((p) => p.x + 10)),
              y2: Math.max(...points.map((p) => p.y + 10)),
            };
          },
          layout(layoutOptions) {
            const record = { items, options: layoutOptions, stopped: false, runCalls: 0 };
            cy.layouts.push(record);
            return {
              stop() { record.stopped = true; },
              run() {
                record.runCalls += 1;
                Promise.resolve().then(() => { if (!record.stopped && layoutOptions.stop) layoutOptions.stop(); });
                return this;
              },
            };
          },
        };
      }
      cy.nodes = () => collection(elements.filter((e) => e.raw.group === "nodes"));
      cy.edges = () => collection(elements.filter((e) => e.raw.group === "edges"));
      cy.elements = () => collection(elements);
      cy.getElementById = (id) => elements.find((e) => e.id() === id) || null;
      cy.on = (event, selector, fn) => { handlers.set(event + ":" + selector, fn); };
      cy.fire = (selector, target) => {
        const fn = handlers.get("tap:" + selector);
        if (fn) fn({ target });
      };
      cy.resize = () => { cy.resizeCalls += 1; };
      cy.fit = (eles, padding) => { cy.fitCalls.push({ eles, padding }); };
      cy.zoom = (arg) => {
        if (arg == null) return cy.zoomLevel;
        cy.zoomLevel = arg.level;
        cy.zoomCalls.push(arg);
        return cy.zoomLevel;
      };
      cy.width = () => 800;
      cy.height = () => 420;
      cy.destroy = () => { cy.destroyed = true; };
      instances.push(cy);
      return cy;
    }
    cytoscape.use = (extension) => { uses.push(extension); };
    cytoscape.instances = instances;
    cytoscape.uses = uses;
    return cytoscape;
  }

  async function mountWith(deps, context, host) {
    const instance = deps.sandbox.ACPNextModules["reaction-network"].factory({
      doc: deps.dom.document,
      request: deps.request.request,
      context,
      commands: deps.commands,
      networkSource: deps.networkSource,
      cytoscape: deps.cytoscape,
      cytoscapeElk: deps.cytoscapeElk,
    });
    await instance.mount(host, { context });
    await flush();
    return instance;
  }

  function canvasOf(root) {
    return walk(root).find((n) => String(n.className || "").indexOf("acp-net-canvas") !== -1) || null;
  }

  function canvasClick(canvas, target) {
    canvas.fire("click", { target });
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

  await test("reaction-network: isolated nodes use a deterministic grid and components stay separate", () => {
    const { sandbox } = load();
    const lib = sandbox.ACPNextReactionNetwork;
    const isolated = lib.layout({
      nodes: [{ state_id: "A" }, { state_id: "B" }, { state_id: "C" }, { state_id: "D" }],
      edges: [],
    });
    assertEq(isolated.isolated, ["A", "B", "C", "D"]);
    assertEq(isolated.isolatedGrid.columns, 2, "four isolates form a 2x2 grid, not one column");
    assertEq(isolated.nodes.map((n) => n.x + "," + n.y), ["24,24", "168,24", "24,108", "168,108"]);
    assertEq(lib.layout({ nodes: [{ state_id: "A" }, { state_id: "B" }, { state_id: "C" }, { state_id: "D" }], edges: [] }).nodes,
      isolated.nodes, "same input has byte-stable positions");

    const mixed = lib.layout({
      nodes: [{ state_id: "A" }, { state_id: "B" }, { state_id: "C" }, { state_id: "D" }, { state_id: "I1" }, { state_id: "I2" }],
      edges: [
        { source_state_id: "A", sink_state_id: "B" },
        { source_state_id: "C", sink_state_id: "D" },
        { source_state_id: "A", sink_state_id: "ghost" },
      ],
    });
    assertEq(mixed.nodes.find((n) => n.id === "A").component, 0);
    assertEq(mixed.nodes.find((n) => n.id === "C").component, 1);
    assert(mixed.nodes.find((n) => n.id === "C").y > mixed.nodes.find((n) => n.id === "A").y,
      "separate components occupy separate bands");
    assertEq(mixed.edges.length, 2, "no component bridge or unknown-target edge is invented");
    assertEq(mixed.isolated, ["I1", "I2"]);
    assert(mixed.nodes.find((n) => n.id === "I1").y > mixed.nodes.find((n) => n.id === "D").y,
      "isolated grid is below connected components");

    const options = lib.elkOptions();
    assertEq(options["elk.algorithm"], "layered");
    assertEq(options["elk.direction"], "RIGHT");
    assertEq(options["elk.separateConnectedComponents"], true);
    assertEq(options["elk.randomSeed"], 1);
    const elements = lib.cytoscapeElements({
      nodes: [{ state_id: "A", role: "structure", label: "A" }, { state_id: "B", role: "structure", label: "B" }],
      edges: [{ source_state_id: "A", sink_state_id: "B" }],
    });
    assertEq(elements.filter((e) => e.group === "nodes").length, 2);
    assertEq(elements.filter((e) => e.group === "edges").length, 1);
    assertEq(elements[0].data.kind, "unknown", "structure role is not promoted to a chemical TS/INT role");
  });

  await test("reaction-network: index loads pinned local UMD dependencies before the module", () => {
    const fs = require("fs");
    const path = require("path");
    const html = fs.readFileSync(path.join(__dirname, "..", "..", "next", "index.html"), "utf8");
    const assets = [
      "/js/vendor/cytoscape-3.34.4.min.js",
      "/js/vendor/elk-0.12.1.bundled.js",
      "/js/vendor/cytoscape-elk-2.3.0.js",
      "/js/modules/reaction-network/module.js",
    ];
    const offsets = assets.map((src) => html.indexOf('src="' + src + '"'));
    offsets.forEach((offset, index) => assert(offset >= 0, "missing script: " + assets[index]));
    assertEq(offsets, offsets.slice().sort((a, b) => a - b), "cytoscape → ELK → adapter → module order");
    assert(html.indexOf('id="viewer-library"') < offsets[0], "existing async 3Dmol wiring remains independent");
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

  await test("reaction-network: wire payload maps to the graph — identity_kind, closed enum, no invented identity", () => {
    const { sandbox } = load();
    const lib = sandbox.ACPNextReactionNetwork;
    const graph = lib.mapWireNetwork(WIRE);

    assertEq(graph.identity_kind, "structure_content_v1");
    assertEq(graph.nodes.length, 2, "nodes without node_id are dropped");
    assertEq(graph.nodes[0].state_id, "sha256:aaa");
    assertEq(graph.nodes[0].label, "caffeine", "molecule name is display only");
    assertEq(graph.nodes[0].task_ids.join(","), "job-1,job-11");
    assertEq(graph.nodes[0].formula, "C8H10N4O2");
    assertEq(graph.nodes[0].atom_count, 24);
    assertEq(graph.nodes[0].identity_kind, "structure_content_v1");
    assert(!("unpromised_field" in graph.nodes[0]), "extra wire fields are not trusted");
    assertEq(graph.nodes[1].label, "C8H10N4O2", "formula fallback when no name");

    assertEq(graph.edges.length, 1, "only persisted lineage edges between rendered nodes survive");
    assertEq(graph.edges[0].relation, "lineage");
    assertEq(graph.edges[0].source_state_id, "sha256:aaa");
    assertEq(graph.edges[0].sink_state_id, "sha256:bbb");
    assertEq(graph.edges[0].to_task, "job-2", "to_task kept for openTask navigation");

    assertEq(graph.truncated, true);
    assertEq(graph.next_cursor, "cursor-1");
    assertEq(graph.skipped.length, 1);

    const empty = lib.mapWireNetwork(null);
    assertEq(empty.nodes.length, 0, "null payload maps to an empty graph, not a crash");
  });

  await test("reaction-network: default source reads the project endpoint; adapter replaces it", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a", "Caffeine study");

    // 1. default HTTP read
    const request = makeRequest();
    const first = await mountWith({ sandbox: (() => {
      const s = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
      loadScript(MODULE_PATH, s);
      return s;
    })(), dom, request, commands: makeCommands() }, context, host);
    assertEq(request.calls.length, 1, "one read, no follow-up page fetch");
    assertEq(request.calls[0].path, "/api/v2/projects/proj-a/chemistry-network?limit=200");
    assertEq(request.calls[0].opts.method, "GET");
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-acp-net-state"), "ready");
    first.dispose();

    // 2. pluggable adapter: same wire payload, no HTTP read
    const host2 = dom.document.createElement("div");
    const adapterCalls = [];
    const networkSource = {
      fetchNetwork(params) { adapterCalls.push(params); return Promise.resolve(WIRE); },
    };
    const request2 = makeRequest();
    const sandbox2 = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox2);
    const second = await mountWith({ sandbox: sandbox2, dom, request: request2, commands: makeCommands(), networkSource }, context, host2);
    assertEq(adapterCalls.length, 1, "adapter called exactly once");
    assertEq(adapterCalls[0].projectId, "proj-a");
    assertEq(adapterCalls[0].projectName, "Caffeine study");
    assertEq(adapterCalls[0].limit, 200);
    assertEq(adapterCalls[0].cursor, null);
    assertEq(request2.calls.length, 0, "adapter owns the read");
    assertEq(host2.children[0].getAttribute("data-acp-module-state"), "ready");
    second.dispose();
  });

  await test("reaction-network: identity banner is honest about the identity level", async () => {
    async function bannerFor(identityKind) {
      const dom = makeDom();
      const host = dom.document.createElement("div");
      const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
      loadScript(MODULE_PATH, sandbox);
      const wire = Object.assign({}, WIRE, { identity_kind: identityKind });
      const instance = await mountWith({
        sandbox, dom,
        request: makeRequest({ handler: (p) => (p.indexOf("/chemistry-network") !== -1 ? Promise.resolve(wire) : undefined) }),
        commands: makeCommands(),
      }, makeContext("proj-a", "P"), host);
      const root = host.children[0];
      const node = banner(root);
      const result = { text: node ? text(node) : "", hidden: node ? node.hidden : true, panel: text(root) };
      instance.dispose();
      return result;
    }

    const structural = await bannerFor("structure_content_v1");
    assert(!structural.hidden, "structure-version banner is visible");
    assertEq(structural.text, "结构版本网络 · 未做分子归集");
    assert(structural.text.indexOf("分子网络") === -1, "must not call a structure-version graph a molecule network");
    assert(structural.panel.indexOf("分子网络") === -1, "the whole panel must not claim a molecule network");

    const molecular = await bannerFor("molecule_v1");
    assertEq(molecular.text, "分子网络");
    assert(molecular.text.indexOf("未做分子归集") === -1, "molecule identity needs no disclaimer");

    const unknown = await bannerFor("molecule_v2_experimental");
    assertEq(unknown.text, "molecule_v2_experimental", "an unknown identity_kind is shown raw, never guessed");
  });

  await test("reaction-network: 空态 vs 未连接 vs 错误+重试 are distinct; retry replays the query", async () => {
    const dom = makeDom();

    // 空态: a successful read with zero nodes
    const emptyHost = dom.document.createElement("div");
    const emptySandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, emptySandbox);
    const emptyInstance = await mountWith({
      sandbox: emptySandbox, dom,
      request: makeRequest({ handler: (p) => (p.indexOf("/chemistry-network") !== -1
        ? Promise.resolve({ identity_kind: "structure_content_v1", nodes: [], edges: [] })
        : undefined) }),
      commands: makeCommands(),
    }, makeContext("proj-empty"), emptyHost);
    const emptyRoot = emptyHost.children[0];
    assertEq(emptyRoot.getAttribute("data-acp-module-state"), "empty");
    assertEq(emptyRoot.getAttribute("data-acp-net-state"), "empty");
    assert(text(emptyRoot).indexOf("该项目暂无结构") !== -1, "empty-project copy");
    assertEq(banner(emptyRoot).hidden, false, "identity banner survives an empty project");
    emptyInstance.dispose();

    // 未连接: 503 from the Controller
    const downHost = dom.document.createElement("div");
    const downSandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, downSandbox);
    let downCalls = 0;
    const downRequest = makeRequest({
      handler(p) {
        if (p.indexOf("/chemistry-network") === -1) return undefined;
        downCalls += 1;
        if (downCalls === 1) {
          const error = new Error("controller unavailable");
          error.status = 503;
          return Promise.reject(error);
        }
        return Promise.resolve(WIRE);
      },
    });
    const downInstance = await mountWith({ sandbox: downSandbox, dom, request: downRequest, commands: makeCommands() },
      makeContext("proj-a", "P"), downHost);
    let downRoot = downHost.children[0];
    assertEq(downRoot.getAttribute("data-acp-net-state"), "disconnected", "503 is 未连接, not 空态");
    assert(text(downRoot).indexOf("未连接") !== -1, "disconnected copy");
    const retryDown = walk(downRoot).find((n) => n.tagName === "BUTTON");
    assert(!!retryDown, "retry control rendered");
    retryDown.fire("click", {});
    await flush();
    downRoot = downHost.children[0];
    assertEq(downCalls, 2, "retry re-issued the read");
    assertEq(downRequest.calls[1].path, downRequest.calls[0].path, "retry replays the exact same query");
    assertEq(downRoot.getAttribute("data-acp-net-state"), "ready", "recovered on the second read");
    downInstance.dispose();

    // 错误: a 500 rejection is an error, never an empty success
    const badHost = dom.document.createElement("div");
    const badSandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, badSandbox);
    let badCalls = 0;
    const badRequest = makeRequest({
      handler(p) {
        if (p.indexOf("/chemistry-network") === -1) return undefined;
        badCalls += 1;
        if (badCalls === 1) {
          const error = new Error("boom");
          error.status = 500;
          return Promise.reject(error);
        }
        return Promise.resolve(WIRE);
      },
    });
    const badInstance = await mountWith({ sandbox: badSandbox, dom, request: badRequest, commands: makeCommands() },
      makeContext("proj-a", "P"), badHost);
    let badRoot = badHost.children[0];
    assertEq(badRoot.getAttribute("data-acp-module-state"), "error");
    assertEq(badRoot.getAttribute("data-acp-net-state"), "error");
    assert(text(badRoot).indexOf("反应网络加载失败") !== -1, "error copy carries the reason");
    const retryBad = walk(badRoot).find((n) => n.tagName === "BUTTON");
    assert(!!retryBad, "retry control rendered");
    retryBad.fire("click", {});
    await flush();
    badRoot = badHost.children[0];
    assertEq(badCalls, 2);
    assertEq(badRequest.calls[1].path, badRequest.calls[0].path, "retry replays the same query");
    assertEq(badRoot.getAttribute("data-acp-net-state"), "ready");
    badInstance.dispose();
  });

  await test("reaction-network: node click navigates to the structure; lineage edge click opens the task", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a", "P");
    const commands = makeCommands();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    const instance = await mountWith({ sandbox, dom, request: makeRequest(), commands }, context, host);

    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const canvas = canvasOf(root);
    assert(!!canvas, "canvas present");
    assert(canvas.innerHTML.indexOf('data-node="sha256:aaa"') !== -1, "node markup keyed by node_id");

    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "sha256:aaa" : null) });
    await flush();
    assertEq(context.focus.length >= 1, true, "focus published on node click");
    assertEq(context.focus[0].networkNodeId, "sha256:aaa");
    const structureCall = commands.calls.find((c) => c.command === "openStructure");
    assert(!!structureCall, "openStructure called");
    assertEq(structureCall.ref.nodeId, "sha256:aaa");
    assertEq(structureCall.ref.jobId, "job-1", "first referencing task");
    assertEq(structureCall.ref.projectId, "proj-a");
    assertEq(structureCall.ref.source, "reaction-network", "source tag");
    // commands.openStructure wraps the whole argument as `structureRef`
    // (shell/commands.js:81-87); a nested key would land at
    // structureRef.structureRef and be read by nobody.
    assert(!("structureRef" in structureCall.ref), "no nested structureRef key");
    assert(!("entryId" in structureCall.ref) && !("kind" in structureCall.ref),
      "no entry id or structureRef kind is invented");

    canvasClick(canvas, { getAttribute: (k) => (k === "data-edge" ? "0" : null) });
    await flush();
    const taskCall = commands.calls.find((c) => c.command === "openTask");
    assert(!!taskCall, "openTask called on a lineage edge");
    assertEq(taskCall.ref.taskId, "job-2", "downstream task delivered as camelCase taskId");
    assertEq(taskCall.ref.jobId, "job-2", "jobId is what commands.openTask requires");
    assertEq(taskCall.ref.source, "reaction-network");

    // A legacy-shaped edge (no to_task) navigates nowhere — and never throws.
    canvasClick(canvas, { getAttribute: (k) => (k === "data-edge" ? "9" : null) });
    await flush();
    assertEq(commands.calls.filter((c) => c.command === "openTask").length, 1, "no navigation without a task ref");
    instance.dispose();
  });

  await test("reaction-network: Cytoscape uses ELK, an isolated grid, controls and real click navigation", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a", "P");
    const commands = makeCommands();
    const cytoscape = makeCytoscape();
    const extension = function cytoscapeElkStub() {};
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    const wire = Object.assign({}, WIRE, {
      nodes: WIRE.nodes.concat([
        { node_id: "sha256:isolated-a", task_ids: ["job-i1"], molecule_names: ["isolated A"] },
        { node_id: "sha256:isolated-b", task_ids: ["job-i2"], molecule_names: ["isolated B"] },
      ]),
    });
    const request = makeRequest({ handler: (p) => (p.indexOf("/chemistry-network") !== -1 ? Promise.resolve(wire) : undefined) });
    const instance = await mountWith({ sandbox, dom, request, commands, cytoscape, cytoscapeElk: extension }, context, host);
    await flush();

    const root = host.children[0];
    const cy = cytoscape.instances[0];
    assert(!!cy, "Cytoscape instance created");
    assertEq(root.getAttribute("data-net-renderer"), "cytoscape");
    assertEq(root.getAttribute("data-net-layout"), "elk");
    assertEq(cytoscape.uses.length, 1, "cytoscape-elk extension registered once");
    assertEq(cy.layouts.length, 1);
    assertEq(cy.layouts[0].options.name, "elk");
    assertEq(cy.layouts[0].runCalls, 1, "Cytoscape layout object is explicitly run");
    assertEq(cy.layouts[0].options.elk["elk.direction"], "RIGHT");
    assertEq(cy.layouts[0].options.elk["elk.separateConnectedComponents"], true);
    const laidOutIds = cy.layouts[0].items.map((e) => e.id());
    assert(laidOutIds.indexOf("sha256:isolated-a") === -1 && laidOutIds.indexOf("sha256:isolated-b") === -1,
      "zero-degree nodes are excluded from ELK");
    assert(laidOutIds.indexOf("edge_0") !== -1, "real lineage edge is supplied to ELK");
    const isolatedA = cy.getElementById("sha256:isolated-a").position();
    const isolatedB = cy.getElementById("sha256:isolated-b").position();
    assert(isolatedA.y === isolatedB.y && isolatedA.x !== isolatedB.x, "two isolates form a row in the grid");
    assert(cy.fitCalls.length >= 1, "initial graph fits the canvas");

    find(root, "data-net-action", "fit").fire("click", {});
    assertEq(cy.fitCalls.length >= 2, true, "fit control calls Cytoscape fit");
    find(root, "data-net-action", "zoom-in").fire("click", {});
    assert(cy.zoomLevel > 1, "zoom control changes zoom around the viewport centre");
    find(root, "data-net-action", "relayout").fire("click", {});
    await flush();
    assertEq(cy.layouts.length, 2, "re-layout runs ELK again");

    cy.fire("node", cy.getElementById("sha256:aaa"));
    await flush();
    assertEq(cytoscape.instances.length, 1, "selection does not rebuild the graph");
    assertEq(context.focus[0].networkNodeId, "sha256:aaa");
    assert(commands.calls.some((c) => c.command === "openStructure" && c.ref.nodeId === "sha256:aaa"));
    assert(cy.getElementById("sha256:aaa").selected(), "Cytoscape selection synced");

    cy.fire("edge", cy.getElementById("edge_0"));
    await flush();
    assert(commands.calls.some((c) => c.command === "openTask" && c.ref.jobId === "job-2"));
    assert(cy.getElementById("edge_0").selected(), "edge selection synced");
    instance.dispose();
    assert(cy.destroyed, "dispose destroys Cytoscape");
  });

  await test("reaction-network: an all-isolated Cytoscape graph skips ELK and fits a 2x2 grid", async () => {
    const cytoscape = makeCytoscape();
    const wire = {
      project_id: "proj-grid",
      identity_kind: "structure_content_v1",
      nodes: ["A", "B", "C", "D"].map((id) => ({
        node_id: "sha256:" + id, task_ids: ["job-" + id], molecule_names: [id],
      })),
      edges: [], skipped: [], truncated: false, next_cursor: null,
    };
    const mounted = await mountWire(wire, { cytoscape, cytoscapeElk: function extension() {} });
    await flush();
    const cy = cytoscape.instances[0];
    assertEq(mounted.root.getAttribute("data-net-layout"), "grid");
    assertEq(cy.layouts.length, 0, "ELK is unnecessary when there are no edges");
    const points = ["A", "B", "C", "D"].map((id) => cy.getElementById("sha256:" + id).position());
    assertEq(points.map((p) => p.x + "," + p.y), ["0,0", "144,0", "0,84", "144,84"]);
    assert(cy.fitCalls.length >= 1, "grid is fitted into the stable viewport");
    mounted.instance.dispose();
  });

  await test("reaction-network: legacy mechanism fallback only without a projectId, and labelled", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const context = makeContext(null);
    const request = makeRequest();
    const commands = makeCommands();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    const instance = await mountWith({ sandbox, dom, request, commands }, context, host);

    // no project + no task
    let root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    assertEq(request.calls.length, 0, "no read before there is anything to read");

    context.setTask({ jobId: "job-1", attempt: 1 });
    await flush();
    root = host.children[0];
    assertEq(request.calls[0].path, "/api/v1/mechanism-studies?job_id=job-1", "legacy read");
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    const badge = banner(root);
    assertEq(text(badge), "遗留机理记录", "legacy source is labelled");
    assertEq(badge.getAttribute("data-identity-kind"), "legacy_mechanism");
    assertEq(request.calls.filter((c) => c.path.indexOf("/api/v2/projects/") !== -1).length, 0,
      "the project endpoint is never reached without a projectId");

    // legacy node without task_ids still publishes focus (no invented task ref)
    const canvas = canvasOf(root);
    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "R" : null) });
    await flush();
    const structureCall = commands.calls.find((c) => c.command === "openStructure");
    assert(!!structureCall, "node click still navigates");
    assertEq(structureCall.ref.nodeId, "R");
    assert(!("jobId" in structureCall.ref), "no task id invented for a legacy node");
    instance.dispose();
  });

  await test("reaction-network: the module only ever issues GET reads (no write endpoint)", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const request = makeRequest();
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    const instance = await mountWith({ sandbox, dom, request, commands: makeCommands() }, makeContext("proj-a", "P"), host);

    const root = host.children[0];
    const canvas = canvasOf(root);
    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "sha256:aaa" : null) });
    canvasClick(canvas, { getAttribute: (k) => (k === "data-edge" ? "0" : null) });
    await flush();

    assertEq(request.calls.length, 1, "clicks do not fetch");
    request.calls.forEach((call) => {
      assertEq(call.opts && call.opts.method, "GET", "read-only method: " + call.path);
      assert(call.path.indexOf("/chemistry-network") !== -1, "read path: " + call.path);
      assert(!/[?&](submit|approve|write|candidate)/.test(call.path), "no write query: " + call.path);
    });
    instance.dispose();
  });

  /* ── molecule-identity levels (contract §4/§6, batch B1d-frontend) ───── */

  /** One molecule-identity node + one pure content-hash node in one graph. */
  const MIXED_WIRE = {
    project_id: "proj-a",
    identity_kind: "mixed",
    identified_nodes: 1,
    unidentified_nodes: 1,
    nodes: [
      {
        node_id: "mol_abc123",
        identity_kind: "molecule_v1",
        scheme: "mol_identity_v1",
        canonical_smiles: "C1=CC=CC=C1",
        inchikey: "UHOVQNZJYSORNB-UHFFFAOYSA-N",
        multiplicity: 1,
        task_ids: ["job-m"],
        molecule_names: ["benzene"],
        formula: "C6H6",
        charge: 0,
        atom_count: 12,
      },
      {
        node_id: "sha256:ccc",
        identity_kind: "structure_content_v1",
        task_ids: ["job-s"],
        molecule_names: [],
        formula: "C6H6",
        charge: 0,
        multiplicity: 1,
        atom_count: 12,
      },
    ],
    edges: [{ relation: "lineage", from_task: "job-m", to_task: "job-s", depth: 1 }],
    skipped: [],
    truncated: false,
    next_cursor: null,
  };

  function wireRequest(wire) {
    return makeRequest({
      handler: (p) => (p.indexOf("/chemistry-network") !== -1 ? Promise.resolve(wire) : undefined),
    });
  }

  async function mountWire(wire, options) {
    options = options || {};
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    if (options.i18n) {
      loadScript("js/shell/i18n.js", sandbox);
      if (options.lang) sandbox.ACPShell.i18n.setLang(options.lang);
    }
    loadScript(MODULE_PATH, sandbox);
    const commands = makeCommands();
    const context = makeContext("proj-a", "P");
    const instance = await mountWith({
      sandbox, dom, request: wireRequest(wire), commands,
      cytoscape: options.cytoscape, cytoscapeElk: options.cytoscapeElk,
    }, context, host);
    return { dom, host, sandbox, commands, context, instance, root: host.children[0] };
  }

  await test("reaction-network: mixed identity banner carries the mixed copy AND both counts", async () => {
    const zh = await mountWire(MIXED_WIRE);
    const zhBanner = banner(zh.root);
    assert(!!zhBanner && !zhBanner.hidden, "mixed banner is visible");
    assertEq(zhBanner.getAttribute("data-identity-kind"), "mixed");
    const zhText = text(zhBanner);
    assert(zhText.indexOf("分子网络（含未识别结构）") !== -1, "mixed copy, got: " + zhText);
    assert(zhText.indexOf("1 个已识别") !== -1, "identified_nodes rendered, got: " + zhText);
    assert(zhText.indexOf("1 个未识别") !== -1, "unidentified_nodes rendered, got: " + zhText);
    assert(zhText !== "分子网络", "mixed must never read as a plain molecule network");
    assertEq(zhBanner.getAttribute("data-identified-nodes"), "1");
    assertEq(zhBanner.getAttribute("data-unidentified-nodes"), "1");
    zh.instance.dispose();

    // en-US copy, same response fields (dictionary-driven, not hardcoded).
    const en = await mountWire(MIXED_WIRE, { i18n: true, lang: "en-US" });
    const enText = text(banner(en.root));
    assert(enText.indexOf("Molecule network · some structures unidentified") !== -1, "en mixed copy, got: " + enText);
    assert(enText.indexOf("1 identified") !== -1, "en identified count, got: " + enText);
    assert(enText.indexOf("1 unidentified") !== -1, "en unidentified count, got: " + enText);
    assert(enText !== "Molecule network", "en mixed is never a plain molecule network either");
    assertEq(find(en.root, "data-net-action", "fit").textContent, "Fit to canvas");
    assertEq(find(en.root, "data-net-action", "relayout").textContent, "Re-layout");
    en.instance.dispose();
  });

  await test("reaction-network: identity_kind decides the banner — molecule claims, structure never does, unknown stays raw", async () => {
    const molecular = await mountWire(Object.assign({}, WIRE, {
      identity_kind: "molecule_v1",
      identified_nodes: 2,
      unidentified_nodes: 0,
    }));
    const moleculeText = text(banner(molecular.root));
    assertEq(moleculeText, "分子网络");
    assert(moleculeText.indexOf("未识别") === -1, "a fully identified graph reports no unidentified remainder");
    molecular.instance.dispose();

    const structural = await mountWire(Object.assign({}, WIRE, { identity_kind: "structure_content_v1" }));
    const structuralText = text(banner(structural.root));
    assertEq(structuralText, "结构版本网络 · 未做分子归集");
    assert(structuralText.indexOf("分子网络") === -1, "structure version must not claim a molecule network");
    assert(text(structural.root).indexOf("分子网络") === -1, "nor anywhere else in the panel");
    structural.instance.dispose();

    const unknown = await mountWire(Object.assign({}, WIRE, { identity_kind: "molecule_v2_experimental" }));
    assertEq(text(banner(unknown.root)), "molecule_v2_experimental", "unknown identity_kind is shown raw");
    unknown.instance.dispose();
  });

  await test("reaction-network: per-node identity survives mapping; node click passes the right identity key", async () => {
    const mounted = await mountWire(MIXED_WIRE);
    const lib = mounted.sandbox.ACPNextReactionNetwork;
    const graph = lib.mapWireNetwork(MIXED_WIRE);
    assertEq(graph.identity_kind, "mixed");
    assertEq(graph.identified_nodes, 1);
    assertEq(graph.unidentified_nodes, 1);
    assertEq(graph.nodes.length, 2, "both identity levels render");
    assertEq(graph.nodes[0].identity_kind, "molecule_v1");
    assertEq(graph.nodes[0].scheme, "mol_identity_v1");
    assertEq(graph.nodes[0].canonical_smiles, "C1=CC=CC=C1");
    assertEq(graph.nodes[0].inchikey, "UHOVQNZJYSORNB-UHFFFAOYSA-N");
    assertEq(graph.nodes[0].multiplicity, 1);
    assertEq(graph.nodes[0].label, "benzene", "display name wins over SMILES");
    assertEq(graph.nodes[1].identity_kind, "structure_content_v1");
    assertEq(graph.nodes[1].canonical_smiles, "", "no molecule metadata invented for a checksum node");

    const canvas = canvasOf(mounted.root);
    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "mol_abc123" : null) });
    await flush();
    const moleculeCall = mounted.commands.calls.find((c) => c.command === "openStructure");
    assert(!!moleculeCall, "molecule node click navigates");
    assertEq(moleculeCall.ref.nodeId, "mol_abc123");
    assertEq(moleculeCall.ref.moleculeIdentityKey, "mol_abc123", "molecule identity passed through");
    assert(!("contentChecksum" in moleculeCall.ref), "a molecule node has no content checksum");
    assertEq(moleculeCall.ref.jobId, "job-m");

    mounted.commands.calls.length = 0;
    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "sha256:ccc" : null) });
    await flush();
    const checksumCall = mounted.commands.calls.find((c) => c.command === "openStructure");
    assert(!!checksumCall, "checksum node click navigates");
    assertEq(checksumCall.ref.contentChecksum, "sha256:ccc", "checksum node keeps its content checksum");
    assert(!("moleculeIdentityKey" in checksumCall.ref), "no molecule identity invented for a checksum node");
    mounted.instance.dispose();
  });

  await test("reaction-network: canonical_smiles is shown for a molecule node and omitted when empty", async () => {
    const wire = {
      project_id: "proj-a",
      identity_kind: "molecule_v1",
      identified_nodes: 2,
      unidentified_nodes: 0,
      nodes: [
        {
          node_id: "mol_smiles",
          identity_kind: "molecule_v1",
          scheme: "mol_identity_v1",
          canonical_smiles: "CCO",
          task_ids: ["job-1"],
          molecule_names: [],
          formula: "",
          atom_count: 9,
        },
        {
          node_id: "mol_formula",
          identity_kind: "molecule_v1",
          scheme: "mol_identity_v1",
          canonical_smiles: "",
          task_ids: ["job-2"],
          molecule_names: [],
          formula: "C6H6",
        },
      ],
      edges: [],
      skipped: [],
    };
    const mounted = await mountWire(wire);
    const lib = mounted.sandbox.ACPNextReactionNetwork;
    const graph = lib.mapWireNetwork(wire);
    assertEq(graph.nodes[0].label, "CCO", "SMILES is the label before the raw node id");
    assertEq(graph.nodes[1].label, "C6H6", "formula still outranks SMILES");

    const canvas = canvasOf(mounted.root);
    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "mol_smiles" : null) });
    await flush();
    let detail = text(mounted.root);
    assert(detail.indexOf("规范 SMILES") !== -1, "SMILES label row rendered");
    assert(detail.indexOf("CCO") !== -1, "SMILES value rendered");

    canvasClick(canvas, { getAttribute: (k) => (k === "data-node" ? "mol_formula" : null) });
    await flush();
    detail = text(mounted.root);
    assert(detail.indexOf("规范 SMILES") === -1, "empty canonical_smiles renders no row");
    mounted.instance.dispose();
  });

  await test("reaction-network: dispose clears the host, unsubscribes and drops a late response", async () => {
    const dom = makeDom();
    const host = dom.document.createElement("div");
    const context = makeContext("proj-a", "P");
    let release;
    const request = makeRequest({
      handler(path) {
        if (path.indexOf("/chemistry-network") !== -1) {
          return new Promise((resolve) => { release = () => resolve(WIRE); });
        }
        return undefined;
      },
    });
    const sandbox = makeSandbox({ document: dom.document, setTimeout: (fn) => { fn(); return 1; }, clearTimeout: () => {} });
    loadScript(MODULE_PATH, sandbox);
    const instance = await mountWith({ sandbox, dom, request, commands: makeCommands() }, context, host);
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "loading");
    instance.dispose();
    assertEq(host.children.length, 0, "host cleared");
    assertEq(context.count(), 0, "context unsubscribed");
    assertEq(dom.document.activeDocListeners(), 0, "language listener removed");
    if (release) release();
    await flush();
    assertEq(host.children.length, 0, "late response did not remount");
  });
};
