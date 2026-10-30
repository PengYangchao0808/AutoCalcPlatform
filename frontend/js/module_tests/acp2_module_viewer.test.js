/* ACP 2.0 module: molecule-viewer (UI-02a) — mount/context/generation/dispose
 * contract in the node vm harness. No network: the sandbox fetch throws and
 * every load goes through the injected deps.fetch seam. Rendering runs the
 * REAL framing.js + scene_instance.js stack over a recording viewer rig.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const flush = async () => { await sleep(0); await sleep(0); };

  const XYZ_A = "2\nenergy -1.000\nC 0.000000 0.000000 0.000000\nO 1.200000 0.000000 0.000000\n";
  const XYZ_B = "3\nenergy -2.500\nC 0.500000 0.000000 0.000000\nO 1.700000 0.000000 0.000000\nH 0.000000 1.000000 0.000000\n";
  const XYZ_MULTI = XYZ_A + "\n" + XYZ_B;

  // ---- minimal DOM rig -----------------------------------------------------
  function makeEl(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      children: [],
      parentNode: null,
      attributes: {},
      style: {},
      dataset: {},
      className: "",
      textContent: "",
      hidden: false,
      type: "",
      clientWidth: 320,
      clientHeight: 200,
      _listeners: {},
      get isConnected() {
        let node = el;
        while (node) {
          if (node.__isDocumentRoot) return true;
          node = node.parentNode;
        }
        return false;
      },
      appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = el;
        el.children.push(child);
        return child;
      },
      removeChild(child) {
        const i = el.children.indexOf(child);
        if (i === -1) throw new Error("removeChild: not a child");
        el.children.splice(i, 1);
        child.parentNode = null;
        return child;
      },
      remove() { if (el.parentNode) el.parentNode.removeChild(el); },
      replaceChildren() {
        el.children.slice().forEach((c) => { c.parentNode = null; });
        el.children = [];
      },
      setAttribute(k, v) { el.attributes[k] = String(v); },
      getAttribute(k) { return k in el.attributes ? el.attributes[k] : null; },
      removeAttribute(k) { delete el.attributes[k]; },
      addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
      removeEventListener(type, fn) {
        const l = el._listeners[type] || [];
        const i = l.indexOf(fn);
        if (i !== -1) l.splice(i, 1);
      },
      dispatch(type, event) { (el._listeners[type] || []).slice().forEach((fn) => fn(event || { type })); },
      click() { el.dispatch("click", { type: "click", target: el }); },
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
    return el;
  }

  function makeHost() {
    const host = makeEl("div");
    host.__isDocumentRoot = true;
    return host;
  }

  function makeDocument() {
    return {
      createElement: (tag) => makeEl(tag),
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
    };
  }

  function makeResizeObserverClass() {
    const instances = [];
    class FakeResizeObserver {
      constructor(cb) { this.cb = cb; this.targets = []; this.disconnected = false; instances.push(this); }
      observe(el) { this.targets.push(el); }
      unobserve() {}
      disconnect() { this.disconnected = true; }
    }
    FakeResizeObserver.instances = instances;
    return FakeResizeObserver;
  }

  // ---- recording viewer rig (fed through the REAL scene_instance) ----------
  function makeViewerRig() {
    const calls = [];
    const viewer = {
      addModel(data, spec) {
        calls.push(["addModel", data, spec]);
        return {
          setStyle(sel, style, add) { calls.push(["model.setStyle", sel, style, add]); return this; },
          selectedAtoms() { return []; },
        };
      },
      removeModel() { calls.push(["removeModel"]); },
      removeAllModels() { calls.push(["removeAllModels"]); },
      removeAllShapes() { calls.push(["removeAllShapes"]); },
      removeAllLabels() { calls.push(["removeAllLabels"]); },
      setBackgroundColor(c) { calls.push(["setBackgroundColor", c]); return viewer; },
      center(sel, dur) { calls.push(["center", sel, dur]); return viewer; },
      zoomTo(sel, dur) { calls.push(["zoomTo", sel, dur]); return viewer; },
      zoom(f, d) { calls.push(["zoom", f, d]); return viewer; },
      render() { calls.push(["render"]); },
      resize() { calls.push(["resize"]); },
      getView() { return [1, 2, 3, 4, 5, 6, 7, 8]; },
      setView(v) { calls.push(["setView", v]); return viewer; },
      clear() { calls.push(["clear"]); },
    };
    return { viewer, calls };
  }

  // ---- injected fetch ------------------------------------------------------
  function makeFetch() {
    const calls = [];
    const queue = [];
    function fetchMock(url, opts) {
      calls.push({ url, opts });
      return new Promise((resolve, reject) => { queue.push({ url, resolve, reject }); });
    }
    fetchMock.calls = calls;
    fetchMock.queue = queue;
    fetchMock.respondAt = (i, body) => { queue[i].resolve(body); };
    fetchMock.respondXyzAt = (i, text) => {
      queue[i].resolve({ ok: true, status: 200, text: () => Promise.resolve(text) });
    };
    return fetchMock;
  }

  function makeContext(initial) {
    let snap = initial || { revision: 0, source: "test" };
    const listeners = [];
    return {
      getSnapshot: () => snap,
      subscribe(fn) {
        listeners.push(fn);
        return () => { const i = listeners.indexOf(fn); if (i !== -1) listeners.splice(i, 1); };
      },
      emit(next) { snap = next; listeners.slice().forEach((fn) => fn(next)); },
      listenerCount: () => listeners.length,
    };
  }

  // ---- sandbox + module load ----------------------------------------------
  function setup(options) {
    options = options || {};
    const rig = makeViewerRig();
    const fetchMock = makeFetch();
    const FakeRO = makeResizeObserverClass();
    const sandbox = makeSandbox({
      document: makeDocument(),
      fetch: options.globalFetch === "keep" ? fetchMock : function () { throw new Error("global fetch is forbidden"); },
      requestAnimationFrame: (fn) => setTimeout(fn, 0),
      ResizeObserver: FakeRO,
      console: { log() {}, warn() {}, error() {} },
      addEventListener() {},
      removeEventListener() {},
    });
    loadScript("js/geometry_store.js", sandbox);
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    if (options.skipVisualization) {
      // deliberately NOT loading scene_instance.js → ACPVisualization.scene absent
    } else {
      loadScript("js/visualization/scene_instance.js", sandbox);
      sandbox.window.ACPVisualization.mol3d.create = () => rig.viewer;
    }
    loadScript("js/modules/molecule-viewer/module.js", sandbox);
    const entry = sandbox.window.ACPNextModules["molecule-viewer"];
    return { sandbox, rig, fetchMock, FakeRO, entry, store: sandbox.window.ACPGeometryStore };
  }

  function makeInstance(options) {
    options = options || {};
    const env = setup(options);
    const ctx = makeContext(options.contextInitial);
    const host = makeHost();
    const instance = env.entry.factory({
      context: ctx,
      commands: { openTask() {}, openEvidence() {} },
      fetch: env.fetchMock,
    });
    return Object.assign(env, { ctx, host, instance });
  }

  const named = (calls, name) => calls.filter((c) => c[0] === name);

  // ------------------------------------------------------------------------
  await test("descriptor + instance surface match the module contract", () => {
    const { entry } = setup();
    assert(entry, "module registered on ACPNextModules");
    const d = entry.descriptor;
    assertEq(d.id, "molecule-viewer");
    assertEq(d.interfaceVersion, 1);
    assertEq(d.dependencies, []);
    assertEq(d.capabilities, { requires: [], provides: ["structure.render"] });
    assertEq(d.hosts, ["acp-web"]);
    assertEq(d.supportedInstancing, "single");
    assertEq(d.configSchemaVersion, 0);
    assertEq(d.error, { titleKey: "next.viewer.errorTitle", messageKey: "next.viewer.errorMessage" });
    const inst = entry.factory({ context: makeContext(), commands: {} });
    ["mount", "updateContext", "resize", "suspend", "resume", "dispose"].forEach((m) => {
      assertEq(typeof inst[m], "function", m + " must be a function");
    });
  });

  await test("mount: attaches to any host div and shows the empty state (no fetch)", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    assertEq(t.host.children.length, 1, "module rendered its root into the host");
    const root = t.host.children[0];
    assertEq(root.className, "acp-mod-viewer");
    assertEq(root.getAttribute("data-acp-module-state"), "empty");
    const msg = root.children[1].children[0].children[0];
    assertEq(msg.textContent, "等待结构加载");
    assertEq(msg.getAttribute("data-i18n"), "next.viewer.empty");
    assertEq(root.children[1].hidden, false, "overlay visible in empty state");
    assertEq(t.fetchMock.calls.length, 0, "empty mount must not fetch");
    assertEq(named(t.rig.calls, "addModel").length, 0, "no model rendered");
    t.instance.dispose();
  });

  await test("structureRef update triggers fetch on the legacy v1 endpoint + scene render + geometry store", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({
      projectId: "proj-a",
      taskRef: { jobId: "job-1", attempt: 1 },
      structureRef: { kind: "job-file", jobId: "job-1", path: "RESULT/optimized.xyz" },
      revision: 1, source: "shell",
    });
    await flush();
    assertEq(t.fetchMock.calls.length, 1, "one load per ref change");
    assert(t.fetchMock.calls[0].url === "/api/v1/jobs/job-1/files/RESULT/optimized.xyz",
      "reuses the legacy files endpoint, got " + t.fetchMock.calls[0].url);
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "loading");
    t.fetchMock.respondXyzAt(0, XYZ_A);
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.children[1].hidden, true, "overlay hidden when ready");
    const adds = named(t.rig.calls, "addModel");
    assertEq(adds.length, 1, "exactly one model added");
    assertEq(adds[0][2], "xyz", "model spec is xyz");
    assert(named(t.rig.calls, "model.setStyle").length >= 1, "ball-stick style applied");
    assert(named(t.rig.calls, "render").length >= 1, "scene rendered");
    // framing went through the shared scheduler (ResizeObserver + frame pass)
    assert(t.FakeRO.instances.length >= 1, "framing observer created");
    assert(t.FakeRO.instances.some((o) => o.targets.indexOf(root.children[0]) !== -1), "observer watches the canvas");
    await sleep(30);
    assert(named(t.rig.calls, "resize").length >= 1 && named(t.rig.calls, "zoomTo").length >= 1,
      "shared framing pass ran (resize+center+zoomTo)");
    // geometry store synced with module-owned entryId
    const snap = t.store.snapshot();
    assertEq(snap.entryId, "job-1:RESULT/optimized.xyz");
    assertEq(snap.symbols, ["C", "O"]);
    assertEq(snap.coordinates[1], [1.2, 0, 0]);
    t.instance.dispose();
  });

  await test("remote structureRef uses the remote-files preview envelope; inline-xyz needs no fetch", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({
      structureRef: { kind: "job-remote-file", jobId: "job-r", path: "WORK/xtb_opt.xyz" },
      revision: 1, source: "shell",
    });
    await flush();
    assert(t.fetchMock.calls[0].url ===
      "/api/v1/jobs/job-r/remote-files/WORK/xtb_opt.xyz/preview?mode=structure",
      "reuses the remote preview endpoint, got " + t.fetchMock.calls[0].url);
    t.fetchMock.respondAt(0, { ok: true, status: 200, json: () => Promise.resolve({ content: XYZ_A }) });
    await flush();
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "ready");

    // inline-xyz renders from text alone
    t.ctx.emit({
      structureRef: { kind: "inline-xyz", text: XYZ_MULTI, frameIndex: 1 },
      revision: 2, source: "shell",
    });
    await flush();
    assertEq(t.fetchMock.calls.length, 1, "inline-xyz must not fetch");
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "ready");
    const adds = named(t.rig.calls, "addModel");
    assert(adds[adds.length - 1][1].indexOf("3\n") === 0, "frameIndex 1 selected the 3-atom frame");
    assertEq(t.store.snapshot().symbols, ["C", "O", "H"]);
    t.instance.dispose();
  });

  await test("generation: A→B→A and late responses never cross (no stale DOM/store writes)", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    const refA = { kind: "job-file", jobId: "job-A", path: "a.xyz" };
    const refB = { kind: "job-file", jobId: "job-B", path: "b.xyz" };
    t.ctx.emit({ structureRef: refA, revision: 1, source: "shell" });
    t.ctx.emit({ structureRef: refB, revision: 2, source: "shell" });
    t.ctx.emit({ structureRef: refA, revision: 3, source: "shell" });
    await flush();
    assertEq(t.fetchMock.calls.length, 3, "one fetch per distinct change");
    // B resolves late (its generation is stale) → must be discarded entirely
    t.fetchMock.respondXyzAt(1, XYZ_B);
    await flush();
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "loading", "stale B must not settle the UI");
    assertEq(t.store.snapshot().entryId, null, "stale B must not write the geometry store");
    // current A resolves → renders
    t.fetchMock.respondXyzAt(2, XYZ_A);
    await flush();
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "ready");
    assertEq(t.store.snapshot().entryId, "job-A:a.xyz");
    const addsBefore = named(t.rig.calls, "addModel").length;
    // original A response arrives even later → still discarded
    t.fetchMock.respondXyzAt(0, "1\n\nHe 9.0 9.0 9.0\n");
    await flush();
    assertEq(t.store.snapshot().entryId, "job-A:a.xyz", "late original A must not overwrite");
    assertEq(named(t.rig.calls, "addModel").length, addsBefore, "no extra model from the late response");
    assert(named(t.rig.calls, "addModel").every((c) => c[1].indexOf("He") === -1), "discarded XYZ never rendered");
    t.instance.dispose();
  });

  await test("dispose: idempotent, removes DOM + subscriptions, disposes scene, resets owned store", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ structureRef: { kind: "inline-xyz", text: XYZ_A }, revision: 1, source: "shell" });
    await flush();
    assertEq(t.store.snapshot().entryId, "inline-xyz");
    assertEq(t.ctx.listenerCount(), 1);
    const addsBefore = named(t.rig.calls, "addModel").length;

    t.instance.dispose();
    t.instance.dispose(); // idempotent: no throw, no double cleanup
    assertEq(t.host.children.length, 0, "module DOM removed from host");
    assertEq(t.ctx.listenerCount(), 0, "context subscription released");
    assert(t.FakeRO.instances.every((o) => o.disconnected), "all framing observers disconnected");
    assert(named(t.rig.calls, "clear").length >= 1, "scene teardown cleared the viewer");
    assertEq(t.store.snapshot().entryId, null, "module-owned store entry reset");
    assertEq(t.store.snapshot().revision, 0);

    // late async result after dispose must be inert
    t.ctx.emit({ structureRef: { kind: "job-file", jobId: "job-late", path: "x.xyz" }, revision: 2, source: "shell" });
    assertEq(t.fetchMock.calls.length, 0, "context after dispose is ignored");
    t.instance.updateContext({ structureRef: { kind: "inline-xyz", text: XYZ_B }, revision: 3, source: "shell" });
    await flush();
    assertEq(named(t.rig.calls, "addModel").length, addsBefore, "no render after dispose");
    t.instance.dispose(); // still safe
  });

  await test("mount failure stays local: broken context → error box in host, no exception escapes", async () => {
    const t = makeInstance({
      contextInitial: undefined,
    });
    // make getSnapshot blow up at mount time
    t.ctx.getSnapshot = () => { throw new Error("context exploded"); };
    await t.instance.mount(t.host, null); // resolves — never rejects
    assertEq(t.host.children.length, 1, "local error surface mounted");
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(root.children[1].children[0].children[2].hidden === false, "retry button offered");
    assert(/context exploded/.test(root.children[1].children[0].children[1].textContent), "detail surfaced");
    assertEq(t.ctx.listenerCount(), 0, "failed mount leaves no subscription behind");
    assertEq(named(t.rig.calls, "addModel").length, 0, "no scene work after failed mount");
    t.instance.dispose();
    assertEq(t.host.children.length, 0, "dispose cleans the error box too");
  });

  await test("mount failure: invalid host rejects; missing visualization scripts degrade locally", async () => {
    const t = makeInstance();
    let caught = null;
    try { await t.instance.mount(null, { revision: 0, source: "test" }); } catch (e) { caught = e; }
    assert(caught && caught.name === "TypeError", "invalid host must reject with TypeError");
    assertEq(t.host.children.length, 0, "nothing mounted");

    const env = setup({ skipVisualization: true });
    const ctx = makeContext({ structureRef: { kind: "inline-xyz", text: XYZ_A }, revision: 1, source: "test" });
    const host = makeHost();
    const inst = env.entry.factory({ context: ctx, commands: {}, fetch: env.fetchMock });
    await inst.mount(host, { revision: 1, source: "test" }); // resolves, local error
    assertEq(host.children.length, 1, "error box rendered inside the host");
    assertEq(host.children[0].getAttribute("data-acp-module-state"), "error");
    assert(/ACPVisualization/.test(host.children[0].children[1].children[0].children[1].textContent),
      "actionable local message");
    inst.dispose();
    assertEq(host.children.length, 0);
  });

  await test("suspend/resume: hidden tears the framing observer down, resume re-frames; resize guarded", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { structureRef: { kind: "inline-xyz", text: XYZ_A }, revision: 1, source: "test" });
    await flush();
    assertEq(t.host.children[0].getAttribute("data-acp-module-state"), "ready");
    const observersBefore = t.FakeRO.instances.length;
    assert(observersBefore >= 1, "framing observer present while visible");

    t.instance.suspend();
    t.instance.suspend(); // idempotent
    assert(t.FakeRO.instances.slice(observersBefore).every((o) => o.disconnected) &&
      t.FakeRO.instances.every((o) => o.disconnected), "suspend disconnects the ResizeObserver");
    const rendersBefore = named(t.rig.calls, "render").length;
    t.instance.resize(); // guarded while suspended
    assertEq(named(t.rig.calls, "render").length, rendersBefore, "no render while suspended");

    t.instance.resume();
    t.instance.resume(); // idempotent
    assert(t.FakeRO.instances.length > observersBefore, "resume re-schedules framing (new observer)");
    assert(t.FakeRO.instances.some((o) => !o.disconnected), "live observer after resume");
    t.instance.dispose();
  });

  await test("fetch seam: no fetch anywhere → local error state, no crash; retry re-loads", async () => {
    const env = setup();
    // factory without deps.fetch; sandbox global fetch throws on purpose
    const ctx = makeContext({ revision: 0, source: "test" });
    const host = makeHost();
    const inst = env.entry.factory({ context: ctx, commands: {} });
    await inst.mount(host, { revision: 0, source: "test" });
    ctx.emit({ structureRef: { kind: "job-file", jobId: "job-1", path: "a.xyz" }, revision: 1, source: "shell" });
    await flush();
    const root = host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error", "missing fetch surface is a local error");
    assert(/fetch/.test(root.children[1].children[0].children[1].textContent), "actionable detail");

    // invalid structureRef shape → local error with reason
    ctx.emit({ structureRef: { kind: "nope" }, revision: 2, source: "shell" });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(/未知 structureRef\.kind/.test(root.children[1].children[0].children[1].textContent));

    // garbage inline text → parse failure surfaced locally
    ctx.emit({ structureRef: { kind: "inline-xyz", text: "garbage" }, revision: 3, source: "shell" });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    inst.dispose();
  });

  await test("retry button re-drives the current load after an error", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ structureRef: { kind: "job-file", jobId: "job-1", path: "a.xyz" }, revision: 1, source: "shell" });
    await flush();
    t.fetchMock.respondAt(0, { ok: false, status: 500, statusText: "Server exploded" });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "error");
    assert(/HTTP 500/.test(root.children[1].children[0].children[1].textContent));
    root.children[1].children[0].children[2].click(); // 重试
    await flush();
    assertEq(t.fetchMock.calls.length, 2, "retry re-fetched");
    assertEq(root.getAttribute("data-acp-module-state"), "loading");
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    t.instance.dispose();
  });

  await test("molecule-view.js whitelist: pure parsers callable in a shared-state-free sandbox", () => {
    const sandbox = makeSandbox({
      document: makeDocument(),
      fetch: function () { throw new Error("fetch forbidden"); },
      console: { log() {}, warn() {}, error() {} },
      addEventListener() {},
      removeEventListener() {},
    });
    // loads WITHOUT workbench/compat/shared-state.js, geometry_store, or any page globals
    loadScript("js/workbench/features/molecule-view.js", sandbox);
    assertEq(typeof sandbox.parseMultiFrameXYZ, "function", "whitelist function exposed as a global");
    assertEq(typeof sandbox.parseXYZBlock, "function");
    assertEq(typeof sandbox.atomsToXYZ, "function");
    assertEq(typeof sandbox.countFormula, "function");
    const frames = sandbox.parseMultiFrameXYZ(XYZ_MULTI);
    assertEq(frames.length, 2, "multi-frame parse works without shared-state");
    assertEq(frames[0].atoms.length, 2);
    assertEq(frames[1].atoms.length, 3);
    assertEq(frames[0].atoms[0].elem, "C");
    assertEq(frames[0].energy, -1.0);
    const block = sandbox.parseXYZBlock(XYZ_A);
    assertEq(block.length, 2);
    assertEq(sandbox.countFormula(frames[0].atoms), "C1O1");
    // conclusion pinned: the mol/sdf helper is page-coupled (needs the legacy
    // `viewer` global + DOM init), so the module ships its own parser instead.
    assertEq(typeof sandbox.parseMolTextToFrame, "function");
  });

  await test("module parser: multi-frame + frameIndex clamp behave like the frozen contract", () => {
    const { entry } = setup();
    const p = entry._internals;
    const frames = p.parseMultiFrameXyz(XYZ_MULTI);
    assertEq(frames.length, 2);
    assertEq(p.refKey({ kind: "job-file", jobId: "j", path: "p" }),
      p.refKey({ kind: "job-file", path: "p", jobId: "j" }), "refKey is field-order stable");
    assertEq(p.refKey(null), null);
    const norm = p.normalizeRef(JSON.stringify({ kind: "inline-xyz", text: XYZ_A }));
    assertEq(norm.kind, "inline-xyz");
    assertEq(p.normalizeRef("not json").kind, "invalid");
    assertEq(p.normalizeRef({ kind: "job-file", jobId: "j" }).kind, "invalid", "path required");
    assertEq(p.buildUrl({ kind: "job-file", jobId: "a b", path: "x dir/f.xyz" }),
      "/api/v1/jobs/a%20b/files/x%20dir/f.xyz", "segments encoded like the legacy page");
    const out = p.atomsToXyz(frames[0].atoms);
    assertEq(out.split("\n")[0], "2");
    assertEq(out.split("\n")[2].split(/\s+/)[0], "C");
  });
};
