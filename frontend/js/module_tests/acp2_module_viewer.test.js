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
  // A-45 hand-computed geometry. Atoms 0..3: A(-1,0,0) B(0,0,0) C(0,1,0)
  // D(0,1,+1) → IUPAC torsion +90° (D above the A-B-C plane); D at z=-1
  // mirrors it to -90°. 0→1 distance is exactly 1 Å; C0-B1-C2 angle is 90°.
  const XYZ_MEASURE =
    "4\nmeasure +90\nC -1.000000 0.000000 0.000000\nC 0.000000 0.000000 0.000000\n" +
    "C 0.000000 1.000000 0.000000\nH 0.000000 1.000000 1.000000\n";
  const XYZ_MEASURE_NEG =
    "4\nmeasure -90\nC -1.000000 0.000000 0.000000\nC 0.000000 0.000000 0.000000\n" +
    "C 0.000000 1.000000 0.000000\nH 0.000000 1.000000 -1.000000\n";
  const XYZ_MEASURE_8 =
    "8\nmeasure pair\n" +
    "C -1.000000 0.000000 0.000000\nC 0.000000 0.000000 0.000000\n" +
    "C 0.000000 1.000000 0.000000\nH 0.000000 1.000000 1.000000\n" +
    "C -3.000000 0.000000 0.000000\nC -2.000000 0.000000 0.000000\n" +
    "C -2.000000 1.000000 0.000000\nH -2.000000 1.000000 -1.000000\n";

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
    let clickable = null;
    let atoms = [];
    let renderHook = null;
    const viewer = {
      addModel(data, spec) {
        calls.push(["addModel", data, spec]);
        const model = {
          setStyle(sel, style, add) { calls.push(["model.setStyle", sel, style, add]); return this; },
          addStyle(sel, style) { calls.push(["model.addStyle", sel, style]); return this; },
          setClickable(sel, val, cb) {
            calls.push(["model.setClickable", sel, val]);
            clickable = cb;
            return this;
          },
          selectedAtoms() { return atoms; },
        };
        return model;
      },
      removeModel() { calls.push(["removeModel"]); clickable = null; },
      removeAllModels() { calls.push(["removeAllModels"]); clickable = null; },
      addArrow(spec) { calls.push(["addArrow", spec]); return {}; },
      addSphere(spec) { calls.push(["addSphere", spec]); return {}; },
      addCylinder(spec) { calls.push(["addCylinder", spec]); return {}; },
      addLabel(text, opts, anchor) { calls.push(["addLabel", text, opts, anchor]); return {}; },
      removeShape(shape) { calls.push(["removeShape", shape]); },
      removeLabel(label) { calls.push(["removeLabel", label]); },
      removeAllShapes() { calls.push(["removeAllShapes"]); },
      removeAllLabels() { calls.push(["removeAllLabels"]); },
      setBackgroundColor(c) { calls.push(["setBackgroundColor", c]); return viewer; },
      center(sel, dur) { calls.push(["center", sel, dur]); return viewer; },
      zoomTo(sel, dur) { calls.push(["zoomTo", sel, dur]); return viewer; },
      zoom(f, d) { calls.push(["zoom", f, d]); return viewer; },
      render() {
        calls.push(["render"]);
        if (typeof renderHook === "function") {
          const hook = renderHook;
          renderHook = null; // one shot: a reentrant render never loops the test hook
          hook();
        }
      },
      resize() { calls.push(["resize"]); },
      getView() { return [1, 2, 3, 4, 5, 6, 7, 8]; },
      setView(v) { calls.push(["setView", v]); return viewer; },
      clear() { calls.push(["clear"]); },
    };
    return {
      viewer,
      calls,
      setAtoms(next) { atoms = next || []; },
      setRenderHook(fn) { renderHook = fn; },
      hasClickable() { return typeof clickable === "function"; },
      click(atom, event) {
        if (typeof clickable !== "function") throw new Error("no clickable callback bound to the current model");
        clickable(atom, viewer, event || { type: "click" });
      },
    };
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

  // ---- injected client request seam (A-47 asset save) ----------------------
  function makeRequest() {
    const calls = [];
    const queue = [];
    function requestMock(url, opts) {
      calls.push({ url, opts });
      return new Promise((resolve, reject) => { queue.push({ url, resolve, reject }); });
    }
    requestMock.calls = calls;
    requestMock.queue = queue;
    requestMock.resolveAt = (i, body) => { queue[i].resolve(body); };
    requestMock.rejectAt = (i, error) => { queue[i].reject(error); };
    return requestMock;
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
      location: options.location || { search: "" },
      localStorage: options.localStorage || null,
      innerWidth: options.width || 1280,
      requestAnimationFrame: (fn) => setTimeout(fn, 0),
      cancelAnimationFrame: (id) => clearTimeout(id),
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
    loadScript("js/visualization/view_model.js", sandbox);
    loadScript("js/visualization/view_source.js", sandbox);
    loadScript("js/visualization/fixtures/views/structure.js", sandbox);
    loadScript("js/visualization/fixtures/views/energy.js", sandbox);
    loadScript("js/visualization/fixtures/views/catalog.js", sandbox);
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
      request: options.request,
      ircFrameMs: options.ircFrameMs,
      vibrationFrameMs: options.vibrationFrameMs,
    });
    return Object.assign(env, { ctx, host, instance });
  }

  const named = (calls, name) => calls.filter((c) => c[0] === name);

  function findByClass(root, className) {
    if (!root) return null;
    if (String(root.className || "").split(/\s+/).indexOf(className) !== -1) return root;
    for (const child of root.children || []) {
      const found = findByClass(child, className);
      if (found) return found;
    }
    return null;
  }

  function makeStorage() {
    const data = {};
    return {
      getItem(key) { return key in data ? data[key] : null; },
      setItem(key, value) { data[key] = String(value); },
    };
  }

  function findByAttr(root, name, value) {
    if (!root) return null;
    if (typeof root.getAttribute === "function" && root.getAttribute(name) === String(value)) return root;
    for (const child of root.children || []) {
      const found = findByAttr(child, name, value);
      if (found) return found;
    }
    return null;
  }

  function textOf(el) {
    if (!el) return "";
    let out = String(el.textContent || "");
    for (const child of el.children || []) out += " " + textOf(child);
    return out;
  }

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

  await test("?view=fixture drives the viewer offline and shows the visible fixture badge", async () => {
    const t = makeInstance({ location: { search: "?view=fixture" } });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready", "fixture view reached ready");
    assertEq(root.getAttribute("data-acp-fixture"), "true", "fixture provenance is visible on the root");
    assertEq(t.fetchMock.calls.length, 0, "fixture mode must not call the network");
    assertEq(root.children[2].hidden, false, "fixture badge rendered");
    assertEq(root.children[2].textContent, "演示数据（fixture）");
    const adds = named(t.rig.calls, "addModel");
    assertEq(adds.length, 1, "fixture structure rendered once");
    assert(adds[0][1].indexOf("O 0.000000 0.000000 0.000000") !== -1, "fixture XYZ text reached the viewer");
    assertEq(t.store.snapshot().symbols, ["O", "H", "H"], "geometry store synced from the fixture");
    t.instance.dispose();
  });

  await test("structure catalog: lazy load, grouped entries, entry geometry and store identity", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({
      taskRef: { jobId: "job-cat", attempt: 1 },
      revision: 1,
      source: "shell",
    });
    await flush();
    const root = t.host.children[0];
    const toggle = root.children[4];
    assertEq(toggle.hidden, false, "catalog toggle visible when a task is bound");
    assertEq(t.fetchMock.calls.length, 0, "catalog fetch is lazy (toggle not pressed)");

    toggle.click();
    await flush();
    assertEq(t.fetchMock.calls.length, 1, "opening the catalog fetches it once");
    assert(t.fetchMock.calls[0].url === "/api/v1/jobs/job-cat/structure-viewer",
      "catalog uses the structure-viewer endpoint, got " + t.fetchMock.calls[0].url);
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        schema_version: "structure_viewer_v1",
        job_id: "job-cat",
        workflow: "optimize",
        job_status: "COMPLETED",
        availability: "ready",
        revision: "r1",
        groups: [
          { id: "formal", label: "正式结果", kind: "formal_result" },
          { id: "input", label: "输入结构", kind: "calculation_input" },
        ],
        entries: [
          {
            id: "cat-opt", group_id: "formal", label: "优化结果", role: "minimum",
            status: "completed", relative_energy_kcal: 0,
            energy: { value: -76.4, unit: "hartree", kind: "electronic" },
            source: { kind: "formal_result", geometry_ref: "RESULT/opt.xyz", frame_index: 0 },
          },
          {
            id: "cat-input", group_id: "input", label: "输入", role: "input",
            status: "completed",
            source: { kind: "calculation_input", geometry_ref: "input.xyz", frame_index: 0 },
          },
        ],
      }),
    });
    await flush();
    const catalog = root.children[5];
    assertEq(catalog.hidden, false, "catalog panel opened");
    const body = catalog.children[2];
    assertEq(body.children.length, 2, "two groups rendered");
    assertEq(body.children[0].children[0].textContent, "正式结果");
    assertEq(body.children[0].children[1].getAttribute("data-entry-id"), "cat-opt");
    assertEq(body.children[1].children[0].textContent, "输入结构");

    body.children[0].children[1].click();
    await flush();
    assertEq(t.fetchMock.calls.length, 2, "entry geometry fetched");
    assert(t.fetchMock.calls[1].url ===
      "/api/v1/jobs/job-cat/structure-viewer/entries/cat-opt/geometry",
      "entry geometry uses the catalog endpoint, got " + t.fetchMock.calls[1].url);
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(t.store.snapshot().entryId, "job-cat:entry:cat-opt",
      "store identity is namespaced by entry id, not a path guess");
    t.instance.dispose();
  });

  await test("structure catalog: ?view=fixture renders grouped entries and entry geometry offline", async () => {
    const t = makeInstance({
      location: { search: "?view=fixture" },
      contextInitial: { taskRef: { jobId: "fixture-structure" }, revision: 1 },
    });
    await t.instance.mount(t.host, t.ctx.getSnapshot());
    await flush();
    const root = t.host.children[0];
    const toggle = root.children[4];
    assertEq(toggle.hidden, false, "catalog toggle visible for the fixture task");
    toggle.click();
    await flush();
    const catalog = root.children[5];
    const body = catalog.children[2];
    assertEq(t.fetchMock.calls.length, 0, "fixture catalog never calls the network");
    assertEq(body.children.length, 2, "fixture groups rendered");
    assertEq(body.children[0].children[1].textContent.indexOf("优化结果") === 0, true);
    body.children[0].children[1].click();
    await flush();
    assertEq(t.fetchMock.calls.length, 0, "fixture entry geometry stays offline");
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(t.store.snapshot().entryId, "fixture-structure:entry:fixture-opt");
    t.instance.dispose();
  });

  await test("structure catalog: pending_fetch is an explicit empty warning and HTTP failure is an error", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-empty" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    const toggle = root.children[4];
    const status = root.children[5].children[1];
    toggle.click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "pending_fetch", entries: [], groups: [], warnings: ["pending_fetch"],
      }),
    });
    await flush();
    assertEq(status.hidden, false, "catalog status is visible");
    assert(/尚未取回/.test(status.textContent), "pending_fetch has an explicit empty warning");

    t.ctx.emit({ taskRef: { jobId: "job-err" }, revision: 2, source: "shell" });
    await flush();
    toggle.click();
    await flush();
    assertEq(t.fetchMock.calls.length, 2, "new task triggers a new catalog fetch");
    t.fetchMock.respondAt(1, { ok: false, status: 500, statusText: "Server exploded" });
    await flush();
    assertEq(status.hidden, false);
    assert(/HTTP 500 Server exploded/.test(status.textContent), "catalog error is surfaced");
    t.instance.dispose();
  });

  await test("structure catalog: external geometryRef highlights the matching entry (energy → catalog link)", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-cat" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "cat-opt", group_id: "formal", label: "优化结果", role: "minimum",
          source: { kind: "formal_result", geometry_ref: "RESULT/opt.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    const body = root.children[5].children[2];
    assertEq(body.children[0].children[1].className.indexOf("is-selected"), -1, "not selected before the external ref");
    t.ctx.emit({
      taskRef: { jobId: "job-cat" },
      structureRef: { kind: "job-file", jobId: "job-cat", path: "RESULT/opt.xyz" },
      revision: 2,
      source: "energy-profile",
    });
    await flush();
    assert(body.children[0].children[1].className.indexOf("is-selected") !== -1,
      "matching geometryRef highlights the catalog entry");
    assertEq(t.fetchMock.calls.length, 2, "the external ref also loads the geometry");
    t.instance.dispose();
  });

  await test("A-35: overlay renders second model + RMSD and blocks measurements on unproven mapping", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-overlay" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [
          { id: "entry-a", group_id: "formal", label: "Structure A", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 } },
          { id: "entry-b", group_id: "formal", label: "Structure B", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/b.xyz", frame_index: 0 } },
        ],
      }),
    });
    await flush();
    const body = root.children[5].children[2];
    body.children[0].children[1].click(); // select A (h3, entry A, overlay A, entry B, overlay B)
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");

    findByAttr(root, "data-overlay-entry-id", "entry-b").click();
    await flush();
    assertEq(t.fetchMock.calls.length, 3, "overlay request issued");
    assert(t.fetchMock.calls[2].url ===
      "/api/v1/jobs/job-overlay/structure-viewer/overlay?entry_a=entry-a&entry_b=entry-b",
      "overlay URL, got " + t.fetchMock.calls[2].url);
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        ok: true, mapping: [[0, 0], [1, 1]], rmsd: 0.123,
        max_displacement: { i: 0, j: 1, distance: 0.5 }, n_mapped: 2, reason: "identity",
      }),
    });
    await flush();
    assertEq(t.fetchMock.calls.length, 4, "overlay geometry request issued");
    t.fetchMock.respondXyzAt(3, XYZ_B);
    await flush();
    assertEq(root.getAttribute("data-acp-overlay-state"), "ready");
    assertEq(root.getAttribute("data-acp-overlay-measurements"), "allowed");
    assert(/RMSD 0\.123/.test(findByClass(root, "acp-mod-viewer-overlay-status").textContent));
    assertEq(named(t.rig.calls, "addModel").length, 2, "A + B models rendered");
    const styles = named(t.rig.calls, "model.setStyle");
    assert(styles[styles.length - 1][2].stick.colorscheme === "cyanCarbon", "overlay style is cyan");
    assert(named(t.rig.calls, "model.addStyle").length >= 1, "max displacement highlighted on A");

    findByAttr(root, "data-overlay-clear", "1").click();
    await flush();
    assertEq(root.getAttribute("data-acp-overlay-state"), "idle");
    assertEq(named(t.rig.calls, "removeModel").length >= 1, true, "overlay model removed");

    findByAttr(root, "data-overlay-entry-id", "entry-b").click();
    await flush();
    t.fetchMock.respondAt(4, {
      ok: true,
      json: () => Promise.resolve({
        ok: false, mapping: null, rmsd: null, max_displacement: null,
        n_mapped: 0, reason: "unproven",
      }),
    });
    await flush();
    t.fetchMock.respondXyzAt(5, XYZ_B);
    await flush();
    assertEq(root.getAttribute("data-acp-overlay-state"), "blocked");
    assertEq(root.getAttribute("data-acp-overlay-measurements"), "blocked");
    assert(/映射未证实/.test(findByClass(root, "acp-mod-viewer-overlay-status").textContent));
    t.instance.dispose();
  });

  await test("A-34: style preset and camera view persist per entry across remount", async () => {
    const storage = makeStorage();
    const env = setup({ localStorage: storage });
    const ref = { kind: "inline-xyz", text: XYZ_A };
    const inst1 = env.entry.factory({ context: makeContext(), commands: {}, fetch: env.fetchMock });
    const host1 = makeHost();
    await inst1.mount(host1, { structureRef: ref, revision: 1, source: "test" });
    await flush();
    const style1 = findByClass(host1.children[0], "acp-mod-viewer-style");
    assert(style1, "style selector mounted");
    style1.value = "spacefill";
    style1.dispatch("change");
    await flush();
    const applied = named(env.rig.calls, "model.setStyle");
    assertEq(applied[applied.length - 1][2], { sphere: { scale: 1.0 } }, "spacefill preset applied");
    inst1.dispose();

    const inst2 = env.entry.factory({ context: makeContext(), commands: {}, fetch: env.fetchMock });
    const host2 = makeHost();
    await inst2.mount(host2, { structureRef: ref, revision: 1, source: "test" });
    await flush();
    const style2 = findByClass(host2.children[0], "acp-mod-viewer-style");
    assertEq(style2.value, "spacefill", "style restored from localStorage");
    const views = named(env.rig.calls, "setView");
    assert(views.length >= 1, "saved camera view restored through scene.setView");
    assertEq(views[views.length - 1][1], [1, 2, 3, 4, 5, 6, 7, 8]);
    inst2.dispose();
  });

  await test("A-36: IRC playback follows catalog frame order and stops at the end", async () => {
    const t = makeInstance({ ircFrameMs: 20 });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-irc" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    findByAttr(root, "data-irc-play", "forward").click();
    await flush();
    assertEq(t.fetchMock.calls.length, 1, "IRC click loads the catalog on demand");
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "irc_forward", label: "Forward" }, { id: "irc_reverse", label: "Reverse" }],
        entries: [
          { id: "f0", group_id: "irc_forward", label: "f0", source: { kind: "irc", frame_index: 0 } },
          { id: "f1", group_id: "irc_forward", label: "f1", source: { kind: "irc", frame_index: 1 } },
          { id: "r0", group_id: "irc_reverse", label: "r0", source: { kind: "irc", frame_index: 0 } },
          { id: "r1", group_id: "irc_reverse", label: "r1", source: { kind: "irc", frame_index: 1 } },
        ],
      }),
    });
    await flush();
    assertEq(t.fetchMock.calls.length, 3, "two forward frames fetched");
    t.fetchMock.respondXyzAt(1, XYZ_A);
    t.fetchMock.respondXyzAt(2, XYZ_B);
    await sleep(120);
    const forwardModels = named(t.rig.calls, "addModel").map((call) => call[1]);
    assertEq(forwardModels.length, 2);
    assert(forwardModels[0].indexOf("O 1.200000") !== -1, "forward frame 0 first");
    assert(forwardModels[1].indexOf("O 1.700000") !== -1, "forward frame 1 second");
    assertEq(root.getAttribute("data-acp-irc-state"), "idle");
    assert(/播放结束/.test(findByClass(root, "acp-mod-viewer-irc-status").textContent));

    findByAttr(root, "data-irc-play", "reverse").click();
    await flush();
    assertEq(t.fetchMock.calls.length, 5, "two reverse frames fetched");
    t.fetchMock.respondXyzAt(3, XYZ_B);
    t.fetchMock.respondXyzAt(4, XYZ_A);
    await sleep(120);
    const reverseModels = named(t.rig.calls, "addModel").map((call) => call[1]).slice(-2);
    assert(reverseModels[0].indexOf("O 1.700000") !== -1, "reverse file order starts at r0");
    assert(reverseModels[1].indexOf("O 1.200000") !== -1, "reverse file order then r1");
    t.instance.dispose();
  });

  await test("A-37: narrow mode is single-column and restores split at width >720", async () => {
    const t = makeInstance({ width: 390 });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-viewer-narrow"), "true");
    assertEq(root.getAttribute("data-acp-viewer-layout"), "single");

    t.sandbox.window.innerWidth = 1280;
    t.instance.resize();
    assertEq(root.getAttribute("data-acp-viewer-narrow"), "false");
    assertEq(root.getAttribute("data-acp-viewer-layout"), "split");

    t.sandbox.window.innerWidth = 390;
    t.instance.resize();
    assertEq(root.getAttribute("data-acp-viewer-narrow"), "true");

    const fs = require("fs");
    const path = require("path");
    const css = fs.readFileSync(path.resolve(__dirname, "../../css/modules/molecule-viewer.css"), "utf8");
    assert(css.indexOf('[data-acp-viewer-narrow="true"]') !== -1, "attribute-driven narrow layout present");
    assert(css.indexOf("@media (max-width: 720px)") !== -1, "media fallback present");
    assert(css.indexOf(".acp-mod-viewer-catalog") !== -1, "catalog bottom-sheet selector present");
    t.instance.dispose();
  });

  await test("A-42: vibration drawer loads modes negatives-first and selects by native mode_index", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-vib" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A", role: "minimum",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    const body = root.children[5].children[2];
    body.children[0].children[1].click();
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();

    findByAttr(root, "data-vibration-toggle", "1").click();
    await flush();
    assertEq(t.fetchMock.calls.length, 3, "vibrations request issued");
    assertEq(t.fetchMock.calls[2].url,
      "/api/v1/jobs/job-vib/structure-viewer/entries/entry-a/vibrations");
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        available: true, atom_count: 2, imaginary_count: 1, source: "normal_modes",
        modes: [
          { mode_index: 100, frequency_cm1: 100.0, imaginary: false, ir_intensity: 2.5,
            vectors: [[0, 0, 1], [0, 1, 0]] },
          { mode_index: 5, frequency_cm1: -50.0, imaginary: true, ir_intensity: null,
            vectors: [[0, 1, 0], [0, 0, 1]] },
          { mode_index: 200, frequency_cm1: 200.0, imaginary: false, ir_intensity: null,
            vectors: [[1, 0, 0], [0, 0, 1]] },
        ],
      }),
    });
    await flush();
    const drawer = findByClass(root, "acp-mod-viewer-vibration-drawer");
    assertEq(drawer.hidden, false);
    const list = drawer.children[2];
    assertEq(list.children.length, 3);
    assertEq(list.children[0].getAttribute("data-vibration-mode"), "5", "imaginary mode sorted first");
    assertEq(list.children[0].children[1].textContent, "-50.00 cm⁻¹");
    assertEq(list.children[0].children[2].textContent, "虚频");
    assertEq(list.children[1].getAttribute("data-vibration-mode"), "100");
    assertEq(list.children[2].getAttribute("data-vibration-mode"), "200");
    assert(list.children[0].className.indexOf("is-selected") !== -1, "default mode is the most-negative mode");
    findByAttr(root, "data-vibration-mode", "200").click();
    assert(findByAttr(root, "data-vibration-mode", "200").className.indexOf("is-selected") !== -1);
    assertEq(root.getAttribute("data-acp-vibration-state"), "ready");
    t.instance.dispose();
  });

  await test("A-42: vibration drawer reports unavailable data without fabricating modes", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-vib-empty" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    root.children[5].children[2].children[0].children[1].click();
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    findByAttr(root, "data-vibration-toggle", "1").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        available: false, reason: "no_normal_modes", modes: [], atom_count: 0,
      }),
    });
    await flush();
    const drawer = findByClass(root, "acp-mod-viewer-vibration-drawer");
    assertEq(drawer.children[2].children.length, 0, "no fake mode rows");
    assert(/无振动模式数据/.test(drawer.children[1].textContent));
    assertEq(root.getAttribute("data-acp-vibration-state"), "empty");
    t.instance.dispose();
  });

  await test("A-43: vibration arrows draw per atom and animation stops without residual frames", async () => {
    const t = makeInstance({ vibrationFrameMs: 30 });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-vib-anim" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    root.children[5].children[2].children[0].children[1].click();
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    findByAttr(root, "data-vibration-toggle", "1").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        available: true, atom_count: 2, imaginary_count: 1, source: "normal_modes",
        modes: [
          { mode_index: 5, frequency_cm1: -50.0, imaginary: true, ir_intensity: null,
            vectors: [[0, 1, 0], [1, 0, 0]] },
        ],
      }),
    });
    await flush();
    assert(named(t.rig.calls, "addArrow").length >= 2, "one arrow per atom");
    assert(named(t.rig.calls, "removeAllShapes").length >= 1, "previous shapes cleared before draw");

    const play = findByAttr(root, "data-vibration-play", "1");
    const beforeModels = named(t.rig.calls, "addModel").length;
    play.click();
    await sleep(150);
    assert(named(t.rig.calls, "addModel").length > beforeModels, "animation rebuilds the model");
    assertEq(play.textContent, "停止");

    play.click();
    await flush();
    const afterStop = named(t.rig.calls, "addModel").length;
    await sleep(120);
    assertEq(named(t.rig.calls, "addModel").length, afterStop, "stop leaves no residual animation frame");
    assertEq(play.textContent, "播放");

    play.click();
    await sleep(60);
    t.ctx.emit({ taskRef: { jobId: "job-vib-other" }, revision: 2, source: "shell" });
    await flush();
    const afterSwitch = named(t.rig.calls, "addModel").length;
    await sleep(120);
    assertEq(named(t.rig.calls, "addModel").length, afterSwitch,
      "task switch cancels the animation (no late frames)");
    t.instance.dispose();
  });

  await test("A-44: thermochemistry summary renders finite ensemble values", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-thermo" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
        ensemble_thermo: {
          total_gibbs_kcal_mol: -12.5, total_gibbs_hartree: -0.004,
          rank1_weight: 0.7, t_s_mix_kcal_per_mol: 1.2,
          population_coverage: 0.95, temperature_k: 298.15,
        },
      }),
    });
    await flush();
    assertEq(root.getAttribute("data-acp-thermo-state"), "ready");
    const thermo = findByClass(root, "acp-mod-viewer-vibration-thermo");
    assert(thermo, "thermo section mounted");
    const text = textOf(thermo);
    assert(/总 Gibbs/.test(text));
    assert(/-12\.5000 kcal\/mol/.test(text));
    assert(/0\.7000/.test(text));
    assert(/95\.0%/.test(text));
    root.children[5].children[2].children[0].children[1].click();
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_A);
    await flush();
    findByAttr(root, "data-vibration-toggle", "1").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({ available: false, reason: "no_normal_modes", modes: [] }),
    });
    await flush();
    assertEq(root.getAttribute("data-acp-thermo-state"), "ready", "thermo survives an empty vibration view");
    t.instance.dispose();
  });

  await test("A-44: missing thermochemistry is a visible warning, never zero-filled", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-no-thermo" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    assertEq(root.getAttribute("data-acp-thermo-state"), "empty");
    assert(/无热化学数据/.test(textOf(findByClass(root, "acp-mod-viewer-vibration-thermo"))));
    t.instance.dispose();
  });

  function approx(actual, expected, tol, msg) {
    assert(typeof actual === "number" && isFinite(actual) &&
      Math.abs(actual - expected) <= (tol == null ? 1e-9 : tol),
      (msg || "approx") + ": expected " + expected + ", got " + actual);
  }

  function openMeasureDrawer(root) {
    const drawer = findByAttr(root, "data-acp-measure-drawer", "1");
    if (!drawer || drawer.hidden) findByAttr(root, "data-measure-toggle", "1").click();
    return drawer;
  }

  function clickMeasurementAtom(t, index, elem) {
    t.rig.click({ index: index, serial: index + 1, elem: elem || "C" });
  }

  function measureStatus(root) {
    return findByClass(root, "acp-mod-viewer-measure-status");
  }

  // ---- A-46 helpers --------------------------------------------------------
  function editStatus(root) {
    return findByClass(root, "acp-mod-viewer-edit-status");
  }

  function measureList(root) {
    return findByAttr(root, "data-acp-measure-list", "1");
  }

  function measureRow(root, index) {
    const list = measureList(root);
    return list && list.children ? list.children[index || 0] : null;
  }

  function lastModelText(t) {
    const adds = named(t.rig.calls, "addModel");
    return adds.length ? adds[adds.length - 1][1] : null;
  }

  async function addDistanceMeasurement(t, root, a, b) {
    findByAttr(root, "data-measure-type", "distance").click();
    clickMeasurementAtom(t, a);
    clickMeasurementAtom(t, b);
    await flush();
    const list = measureList(root);
    return list.children[list.children.length - 1];
  }

  async function addAngleMeasurement(t, root, a, b, c) {
    findByAttr(root, "data-measure-type", "angle").click();
    clickMeasurementAtom(t, a);
    clickMeasurementAtom(t, b);
    clickMeasurementAtom(t, c);
    await flush();
    const list = measureList(root);
    return list.children[list.children.length - 1];
  }

  function targetInput(root, itemId) {
    return findByAttr(root, "data-measure-target", itemId);
  }

  function setTarget(root, itemId, value) {
    const input = targetInput(root, itemId);
    input.value = value;
    return input;
  }

  function applyTarget(root, itemId, value) {
    setTarget(root, itemId, value);
    findByAttr(root, "data-measure-apply", itemId).click();
  }

  function editButton(root, name) {
    return findByAttr(root, "data-edit-" + name, "1");
  }

  async function mountReady(options) {
    const t = makeInstance(options);
    await t.instance.mount(t.host, options && options.contextInitial
      ? t.ctx.getSnapshot()
      : { structureRef: { kind: "inline-xyz", text: XYZ_MEASURE }, revision: 1, source: "test" });
    await flush();
    return t;
  }

  async function commitTarget(t, root, itemId, value) {
    applyTarget(root, itemId, value);
    await flush();
    editButton(root, "commit").click();
    await flush();
  }

  // ---- A-47 helpers --------------------------------------------------------
  function assetStatus(root) {
    return findByAttr(root, "data-acp-asset-status", "1");
  }

  function assetReceipt(root) {
    return findByAttr(root, "data-acp-asset-receipt", "1");
  }

  function assetSaveButton(root) {
    return findByAttr(root, "data-acp-asset-save", "1");
  }

  function assetCancelButton(root) {
    return findByAttr(root, "data-acp-asset-cancel", "1");
  }

  function assetNameInput(root) {
    return findByAttr(root, "data-acp-asset-name", "1");
  }

  function assetNoteInput(root) {
    return findByAttr(root, "data-acp-asset-note", "1");
  }

  function setAssetField(el, value) {
    el.value = value;
    el.dispatch("input", { type: "input", target: el });
  }

  function clickAssetSave(root) {
    assetSaveButton(root).click();
  }

  /** Confirmation click → actual request click (two-step contract). */
  async function requestAssetSave(root) {
    clickAssetSave(root);
    await flush();
    clickAssetSave(root);
    await flush();
  }

  async function mountAssetReady(options) {
    options = options || {};
    const requestMock = options.request || makeRequest();
    const t = makeInstance(Object.assign({}, options, { request: requestMock }));
    await t.instance.mount(t.host, {
      projectId: "proj-asset",
      taskRef: { jobId: "job-asset" },
      structureRef: { kind: "structure-entry", jobId: "job-asset", entryId: "entry-1" },
      revision: 1,
      source: "test",
    });
    await flush();
    if (t.fetchMock.queue.length) {
      t.fetchMock.respondXyzAt(0, options.xyz || XYZ_MEASURE);
      await flush();
    }
    t.requestMock = requestMock;
    return t;
  }

  await test("A-45 pure math: distance/angle/dihedral match hand-computed IUPAC values", () => {
    const { entry } = setup();
    const p = entry._internals;
    assertEq(p.MEASUREMENT_ARITY, { distance: 2, angle: 3, dihedral: 4 });
    const origin = { x: 0, y: 0, z: 0 };
    approx(p.measurementDistance(origin, { x: 3, y: 0, z: 0 }), 3, 1e-12, "distance 3 Å");
    approx(p.measurementDistance(origin, { x: 1, y: 2, z: 2 }), 3, 1e-12, "distance 3-4-5");
    assertEq(p.measurementDistance(origin, null), null, "distance needs both points");

    approx(p.measurementAngle({ x: 1, y: 0, z: 0 }, origin, { x: 0, y: 1, z: 0 }), 90, 1e-9, "90° angle");
    approx(p.measurementAngle({ x: 1, y: 0, z: 0 }, origin, { x: -1, y: 0, z: 0 }), 180, 1e-9, "180° angle");
    approx(p.measurementAngle({ x: 1, y: 0, z: 0 }, origin, { x: 0.5, y: Math.sqrt(3) / 2, z: 0 }),
      60, 1e-9, "60° angle");
    assertEq(p.measurementAngle({ x: 1, y: 0, z: 0 }, origin, origin), null, "degenerate angle centre");

    // Hand-computed IUPAC torsions: A(-1,0,0) B(0,0,0) C(0,1,0) with D fixed
    // relative to the B→C (+y) axis: +z → +90°, -z → -90°, cis → 0°, anti → 180°.
    const a = { x: -1, y: 0, z: 0 }, b = origin, c = { x: 0, y: 1, z: 0 };
    approx(p.measurementDihedral(a, b, c, { x: 0, y: 1, z: 1 }), 90, 1e-9, "+90 dihedral");
    approx(p.measurementDihedral(a, b, c, { x: 0, y: 1, z: -1 }), -90, 1e-9, "-90 mirror dihedral");
    approx(p.measurementDihedral(a, b, c, { x: -1, y: 1, z: 0 }), 0, 1e-9, "cis dihedral");
    approx(p.measurementDihedral(a, b, c, { x: 1, y: 1, z: 0 }), 180, 1e-9, "trans dihedral normalizes to +180");
    approx(p.measurementDihedral(a, b, c, { x: Math.SQRT1_2, y: 1, z: Math.SQRT1_2 }),
      135, 1e-9, "+135 dihedral");
    assertEq(p.measurementDihedral(a, b, b, { x: 0, y: 1, z: 1 }), null, "zero central bond");
    assertEq(p.normalizeDihedral(-180), 180, "-180 normalizes to +180");
    assertEq(p.normalizeDihedral(540), 180);
    assertEq(p.normalizeDihedral(-270), 90);
    assertEq(p.normalizeDihedral(NaN), null);

    const atoms = [
      { id: 0, elem: "C", x: -1, y: 0, z: 0 },
      { id: 1, elem: "C", x: 0, y: 0, z: 0 },
      { id: 2, elem: "C", x: 0, y: 1, z: 0 },
      { id: 3, elem: "H", x: 0, y: 1, z: 1 },
    ];
    approx(p.measurementValue("distance", atoms, [0, 1]), 1, 1e-12);
    approx(p.measurementValue("angle", atoms, [0, 1, 2]), 90, 1e-9);
    approx(p.measurementValue("dihedral", atoms, [0, 1, 2, 3]), 90, 1e-9);
    assertEq(p.measurementValue("dihedral", atoms, [0, 1, 2]), null, "arity mismatch -> null");
    assertEq(p.measurementValue("distance", atoms, [0, 99]), null, "missing atom -> null");
    assertEq(p.measurementValue("bogus", atoms, [0, 1]), null, "unknown type -> null");
    assertEq(p.formatMeasurementValue("distance", 1.23456), "1.235 Å");
    assertEq(p.formatMeasurementValue("angle", 90), "90.0°");
    assertEq(p.formatMeasurementValue("dihedral", -90), "-90.0°");
  });

  await test("A-45 panel: opened with no structure shows an explicit local empty message", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-measurement-state"), "empty");
    assertEq(root.getAttribute("data-acp-measurement-count"), "0");
    const drawer = openMeasureDrawer(root);
    assertEq(drawer.hidden, false, "measurement drawer opened");
    assertEq(measureStatus(root).textContent, "暂无结构，先加载结构再测量");
    assertEq(measureStatus(root).className.indexOf("is-error"), -1, "empty is informative, not an error");
    assertEq(findByAttr(root, "data-measure-pick", "1").disabled, true, "picking disabled without a structure");
    assertEq(findByAttr(root, "data-acp-measure-list", "1").children.length, 0, "no fabricated measurement rows");
    findByAttr(root, "data-measure-close", "1").click();
    assertEq(drawer.hidden, true, "close hides the drawer");
    t.instance.dispose();
  });

  await test("A-45 distance: 2-atom click sequence shows progress, Å result, lines and label", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, {
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE },
      revision: 1, source: "test",
    });
    await flush();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    openMeasureDrawer(root);
    findByAttr(root, "data-measure-type", "distance").click();
    assertEq(root.getAttribute("data-acp-measurement-state"), "picking");
    assert(t.rig.hasClickable(), "picking binds the bounded scene click callback");

    clickMeasurementAtom(t, 0);
    assertEq(root.getAttribute("data-acp-measurement-state"), "picking");
    assert(/已选 1\/2/.test(measureStatus(root).textContent), "selection progress is visible");
    assert(named(t.rig.calls, "addSphere").length >= 1, "pick progress highlight uses scene.addSphere");

    clickMeasurementAtom(t, 1);
    await flush();
    const list = findByAttr(root, "data-acp-measure-list", "1");
    assertEq(list.children.length, 1, "one measurement row");
    assert(/距离/.test(textOf(list.children[0])));
    assert(/C1—C2/.test(textOf(list.children[0])), "atom labels use 1-based element+index");
    assert(/1\.000 Å/.test(textOf(list.children[0])), "distance result carries the Å unit");
    assertEq(root.getAttribute("data-acp-measurement-count"), "1");
    assert(named(t.rig.calls, "addCylinder").length >= 1, "distance line drawn through the scene");
    assert(named(t.rig.calls, "addLabel").some((call) => /1\.000 Å/.test(call[1])),
      "value label rendered through scene.addLabel");
    // No second geometry cache / no stored-geometry mutation.
    assertEq(t.store.snapshot().measurements.length, 0, "measurement UI never writes the geometry store");
    assertEq(t.store.snapshot().coordinates[0], [-1, 0, 0], "stored coordinates untouched");
    t.instance.dispose();
  });

  await test("A-45 angle: 3-atom click sequence renders 90° at the centre atom", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, {
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE },
      revision: 1, source: "test",
    });
    await flush();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    findByAttr(root, "data-measure-type", "angle").click();
    clickMeasurementAtom(t, 0);
    clickMeasurementAtom(t, 1);
    assert(/已选 2\/3/.test(measureStatus(root).textContent), "2/3 progress");
    clickMeasurementAtom(t, 2);
    await flush();
    const list = findByAttr(root, "data-acp-measure-list", "1");
    assertEq(list.children.length, 1);
    assert(/角度/.test(textOf(list.children[0])));
    assert(/C1-C2-C3/.test(textOf(list.children[0])), "angle joins three atoms in pick order");
    assert(/90\.0°/.test(textOf(list.children[0])), "angle result carries the ° unit");
    assertEq(t.store.snapshot().measurements.length, 0);
    t.instance.dispose();
  });

  await test("A-45 dihedral: 4-atom picks keep the IUPAC sign (+90 / -90) across two sites", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, {
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE_8 },
      revision: 1, source: "test",
    });
    await flush();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    findByAttr(root, "data-measure-type", "dihedral").click();
    [0, 1, 2, 3].forEach((index) => clickMeasurementAtom(t, index));
    await flush();
    [4, 5, 6, 7].forEach((index) => clickMeasurementAtom(t, index));
    await flush();
    const list = findByAttr(root, "data-acp-measure-list", "1");
    assertEq(list.children.length, 2, "two dihedral measurements");
    assert(/H4/.test(textOf(list.children[0])) && /\+?90\.0°/.test(textOf(list.children[0])),
      "first site is +90.0°, got " + textOf(list.children[0]));
    assert(/-90\.0°/.test(textOf(list.children[1])),
      "mirror site keeps the negative sign, got " + textOf(list.children[1]));
    assert(/C1-C2-C3-H4/.test(textOf(list.children[0])), "dihedral joins four atoms in pick order");
    t.instance.dispose();
  });

  await test("A-45 invalid selection, delete and clear all stay local and explicit", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, {
      structureRef: { kind: "inline-xyz", text: XYZ_A }, // only 2 atoms
      revision: 1, source: "test",
    });
    await flush();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    findByAttr(root, "data-measure-type", "dihedral").click();
    assert(/需要 4 个原子/.test(measureStatus(root).textContent),
      "insufficient atom count is an explicit local message");
    assertEq(root.getAttribute("data-acp-measurement-count"), "0");

    findByAttr(root, "data-measure-type", "distance").click();
    clickMeasurementAtom(t, 9); // out-of-range click snapshot
    assert(/无法定位点击的原子/.test(measureStatus(root).textContent));
    assertEq(root.getAttribute("data-acp-measurement-count"), "0");

    clickMeasurementAtom(t, 0);
    clickMeasurementAtom(t, 1);
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "1");
    const list = findByAttr(root, "data-acp-measure-list", "1");
    assert(/1\.200 Å/.test(textOf(list.children[0])));
    list.children[0].children[1].click(); // 删除
    assertEq(root.getAttribute("data-acp-measurement-count"), "0");
    const afterDelete = findByAttr(root, "data-acp-measure-list", "1");
    assert(/暂无测量/.test(textOf(afterDelete)), "deleting the last row shows the local empty message");
    assertEq(afterDelete.children.filter((child) => child.getAttribute("data-measure-id")).length, 0);

    clickMeasurementAtom(t, 0);
    clickMeasurementAtom(t, 1);
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "1");
    findByAttr(root, "data-measure-clear", "1").click();
    assertEq(root.getAttribute("data-acp-measurement-count"), "0");
    const afterClear = findByAttr(root, "data-acp-measure-list", "1");
    assert(/暂无测量/.test(textOf(afterClear)));
    assertEq(afterClear.children.filter((child) => child.getAttribute("data-measure-id")).length, 0);
    assert(named(t.rig.calls, "removeLabel").length >= 1, "overlay labels removed on clear");
    assert(named(t.rig.calls, "removeShape").length >= 1, "pick/line shapes removed on clear");
    t.instance.dispose();
  });

  await test("A-45 task switch and structure switch clear measurements + overlays", async () => {
    const t = makeInstance({
      contextInitial: {
        taskRef: { jobId: "job-measure-a" },
        structureRef: { kind: "inline-xyz", text: XYZ_MEASURE },
        revision: 1,
      },
    });
    await t.instance.mount(t.host, t.ctx.getSnapshot());
    await flush();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    findByAttr(root, "data-measure-type", "distance").click();
    clickMeasurementAtom(t, 0);
    clickMeasurementAtom(t, 1);
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "1");
    assert(named(t.rig.calls, "addLabel").length >= 1);

    t.ctx.emit({
      taskRef: { jobId: "job-measure-b" },
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE },
      revision: 2,
      source: "shell",
    });
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "0", "task switch clears measurements");
    assertEq(root.getAttribute("data-acp-measurement-state"), "empty");
    assert(named(t.rig.calls, "removeLabel").length >= 1, "task switch clears overlay labels");

    findByAttr(root, "data-measure-type", "distance").click();
    clickMeasurementAtom(t, 0);
    clickMeasurementAtom(t, 1);
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "1");
    const modelsBefore = named(t.rig.calls, "addModel").length;
    t.ctx.emit({
      taskRef: { jobId: "job-measure-b" },
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE_NEG },
      revision: 3,
      source: "shell",
    });
    await flush();
    assertEq(root.getAttribute("data-acp-measurement-count"), "0", "structure switch clears measurements");
    assert(named(t.rig.calls, "addModel").length > modelsBefore, "new structure still renders");
    assertEq(t.store.snapshot().measurements.length, 0, "store measurements never populated by the UI");
    t.instance.dispose();
  });

  await test("A-45 overlay blocked: unproven mapping disables measuring with the legacy message", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-block" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click(); // catalog toggle
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [
          { id: "entry-a", group_id: "formal", label: "A", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 } },
          { id: "entry-b", group_id: "formal", label: "B", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/b.xyz", frame_index: 0 } },
        ],
      }),
    });
    await flush();
    root.children[5].children[2].children[0].children[1].click(); // select A
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_MEASURE);
    await flush();
    findByAttr(root, "data-overlay-entry-id", "entry-b").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        ok: false, mapping: null, rmsd: null, max_displacement: null,
        n_mapped: 0, reason: "unproven",
      }),
    });
    await flush();
    t.fetchMock.respondXyzAt(3, XYZ_MEASURE);
    await flush();
    assertEq(root.getAttribute("data-acp-overlay-measurements"), "blocked");
    openMeasureDrawer(root);
    assertEq(root.getAttribute("data-acp-measurement-state"), "blocked");
    assert(/映射未证实/.test(measureStatus(root).textContent));
    assertEq(findByAttr(root, "data-measure-pick", "1").disabled, true,
      "picking disabled while the mapping is unproven");
    findByAttr(root, "data-measure-type", "distance").click();
    assert(/映射未证实/.test(measureStatus(root).textContent), "type click keeps the explicit blocked message");
    t.instance.dispose();
  });

  await test("A-46 pure: target validation enforces Å/° ranges and rejects NaN/blank", () => {
    const { entry } = setup();
    const p = entry._internals;
    assertEq(p.EDIT_TYPES, ["distance", "angle", "dihedral"]);
    assertEq(p.editRangeText("distance"), "0.4–5.0 Å");
    assertEq(p.editRangeText("angle"), "1–179°");
    assertEq(p.editRangeText("dihedral"), "-180–180°");
    assertEq(p.editRangeText("bogus"), "");

    assertEq(p.validateEditTarget("distance", 1.2), { ok: true, value: 1.2 });
    assertEq(p.validateEditTarget("distance", "1.5"), { ok: true, value: 1.5 });
    assertEq(p.validateEditTarget("distance", " 2.0 "), { ok: true, value: 2.0 });
    assertEq(p.validateEditTarget("distance", 5), { ok: true, value: 5 });
    assertEq(p.validateEditTarget("distance", 0.39).reason, "out_of_range");
    assert(/0\.4–5\.0 Å/.test(p.validateEditTarget("distance", 9).message));
    assertEq(p.validateEditTarget("distance", "abc").reason, "non_finite");
    assertEq(p.validateEditTarget("distance", "").reason, "missing");
    assertEq(p.validateEditTarget("distance", "   ").reason, "missing");
    assertEq(p.validateEditTarget("distance", NaN).reason, "non_finite");
    assertEq(p.validateEditTarget("distance", Infinity).reason, "non_finite");
    assertEq(p.validateEditTarget("distance", null).reason, "missing");
    assertEq(p.validateEditTarget("angle", 90), { ok: true, value: 90 });
    assertEq(p.validateEditTarget("angle", 0).reason, "out_of_range");
    assertEq(p.validateEditTarget("angle", 180).reason, "out_of_range");
    assert(/1–179°/.test(p.validateEditTarget("angle", 0).message));
    assertEq(p.validateEditTarget("dihedral", -180), { ok: true, value: -180 });
    assertEq(p.validateEditTarget("dihedral", 180), { ok: true, value: 180 });
    assertEq(p.validateEditTarget("dihedral", 180.1).reason, "out_of_range");
    assertEq(p.validateEditTarget("bogus", 1).reason, "invalid_type");
    assertEq(p.formatEditInputValue("distance", 1.23456), "1.235");
    assertEq(p.formatEditInputValue("angle", 90), "90.0");
    assertEq(p.formatEditInputValue("dihedral", -45.06), "-45.1");
    assertEq(p.formatEditInputValue("distance", NaN), "");
  });

  await test("A-46 pure: displacement moves only the intended atom and hits the target", () => {
    const { entry } = setup();
    const p = entry._internals;
    const atoms = [
      { elem: "C", x: -1, y: 0, z: 0 },
      { elem: "C", x: 0, y: 0, z: 0 },
      { elem: "C", x: 0, y: 1, z: 0 },
      { elem: "H", x: 0, y: 1, z: 1 },
    ];
    const frozen = JSON.stringify(atoms);

    const distance = p.displaceGeometry("distance", atoms, [0, 1], 1.5);
    assertEq(distance.ok, true);
    assertEq(distance.movedAtomIds, [1]);
    assertEq(distance.beforeValue, 1);
    approx(distance.afterValue, 1.5, 1e-12);
    approx(p.measurementValue("distance", distance.atoms, [0, 1]), 1.5, 1e-12);
    assertEq(distance.atoms[0], atoms[0], "atom 1 is untouched");
    assertEq(distance.atoms[2], atoms[2]);
    assertEq(distance.atoms[3], atoms[3]);
    assertEq(distance.atoms[1], { elem: "C", x: 0.5, y: 0, z: 0 });
    assertEq(JSON.stringify(atoms), frozen, "input atoms are never mutated");
    approx(p.measurementValue("distance", atoms, [0, 1]), 1, 1e-12, "original frame still measures 1");

    const angle = p.displaceGeometry("angle", atoms, [0, 1, 2], 60);
    assertEq(angle.ok, true);
    assertEq(angle.movedAtomIds, [2]);
    approx(p.measurementValue("angle", angle.atoms, [0, 1, 2]), 60, 1e-9);
    approx(p.measurementDistance(angle.atoms[1], angle.atoms[2]), 1, 1e-12,
      "the vertex bond length is preserved by the rigid rotation");
    assertEq(angle.atoms[0], atoms[0]);
    assertEq(angle.atoms[1], atoms[1]);
    assertEq(angle.atoms[3], atoms[3]);

    const dihedral = p.displaceGeometry("dihedral", atoms, [0, 1, 2, 3], -45);
    assertEq(dihedral.ok, true);
    assertEq(dihedral.movedAtomIds, [3]);
    approx(p.measurementValue("dihedral", dihedral.atoms, [0, 1, 2, 3]), -45, 1e-9);
    approx(p.measurementDistance(dihedral.atoms[2], dihedral.atoms[3]), 1, 1e-12,
      "the terminal bond length is preserved");
    assertEq(dihedral.atoms[0], atoms[0]);
    assertEq(dihedral.atoms[1], atoms[1]);
    assertEq(dihedral.atoms[2], atoms[2]);

    const straight = p.displaceGeometry("dihedral", atoms, [0, 1, 2, 3], 180);
    approx(p.measurementValue("dihedral", straight.atoms, [0, 1, 2, 3]), 180, 1e-9,
      "a 180° target lands on the IUPAC +180 normalization");

    // Arity / type / index / duplicate / range / degeneracy guards.
    assertEq(p.displaceGeometry("distance", atoms, [0], 1).reason, "invalid_arity");
    assertEq(p.displaceGeometry("angle", atoms, [0, 1], 60).reason, "invalid_arity");
    assertEq(p.displaceGeometry("dihedral", atoms, [0, 1, 2], 90).reason, "invalid_arity");
    assertEq(p.displaceGeometry("bogus", atoms, [0, 1], 1).reason, "invalid_type");
    assertEq(p.displaceGeometry("distance", atoms, [0, 9], 1).reason, "invalid_atom");
    assertEq(p.displaceGeometry("distance", atoms, [-1, 1], 1).reason, "invalid_atom");
    assertEq(p.displaceGeometry("distance", atoms, [0, "x"], 1).reason, "invalid_atom");
    assertEq(p.displaceGeometry("distance", atoms, [0, 0], 1).reason, "duplicate_atom");
    assertEq(p.displaceGeometry("distance", atoms, [0, 1], 6).reason, "out_of_range");
    assertEq(p.displaceGeometry("distance", atoms, [0, 1], "abc").reason, "non_finite");
    assertEq(p.displaceGeometry("distance", [], [0, 1], 1).reason, "no_atoms");
    const collinear = [
      { elem: "C", x: 0, y: 0, z: 0 },
      { elem: "C", x: 1, y: 0, z: 0 },
      { elem: "C", x: 2, y: 0, z: 0 },
    ];
    assertEq(p.displaceGeometry("angle", collinear, [0, 1, 2], 60).reason, "degenerate");
    const coincident = [
      { elem: "C", x: 0, y: 0, z: 0 },
      { elem: "C", x: 0, y: 0, z: 0 },
    ];
    assertEq(p.displaceGeometry("distance", coincident, [0, 1], 1).reason, "degenerate");
    const collinearDihedral = [
      { elem: "C", x: -1, y: 0, z: 0 },
      { elem: "C", x: 0, y: 0, z: 0 },
      { elem: "C", x: 0, y: 1, z: 0 },
      { elem: "H", x: 0, y: 2, z: 0 },
    ];
    assertEq(p.displaceGeometry("dihedral", collinearDihedral, [0, 1, 2, 3], 90).reason,
      "degenerate", "a torsion with a zero projection is not editable");
  });

  await test("A-46 transaction: preview → apply → undo restores the exact original frame", async () => {
    const t = await mountReady({ contextInitial: undefined });
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(root.getAttribute("data-acp-edit-busy"), "false");
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");
    const originalModel = lastModelText(t);
    assert(originalModel.indexOf("C -1.000000 0.000000 0.000000") !== -1, "original frame rendered");
    const storeBefore = t.store.snapshot();
    assertEq(targetInput(root, itemId).value, "1.000");
    assertEq(targetInput(root, itemId).getAttribute("min"), "0.4");
    assertEq(targetInput(root, itemId).getAttribute("max"), "5");

    applyTarget(root, itemId, "1.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "preview");
    assertEq(root.getAttribute("data-acp-edit-count"), "0", "preview is not a committed transaction");
    assert(/1\.000 Å/.test(editStatus(root).textContent) &&
      /1\.500 Å/.test(editStatus(root).textContent), "preview shows before → target");
    assert(/尚未应用/.test(editStatus(root).textContent));
    const previewModel = lastModelText(t);
    assert(previewModel.indexOf("C 0.500000 0.000000 0.000000") !== -1,
      "only the second atom was rigidly translated");
    assert(previewModel.indexOf("C -1.000000 0.000000 0.000000") !== -1, "the fixed side is untouched");
    assert(/1\.500 Å/.test(textOf(measureRow(root))), "measurement row follows the displayed geometry");
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates, "store coordinates never mutated");
    assertEq(t.store.snapshot().revision, storeBefore.revision, "store revision never bumped by an edit");
    assertEq(editButton(root, "commit").hidden, false);
    assertEq(editButton(root, "cancel").hidden, false);
    assertEq(editButton(root, "undo").disabled, true, "no committed transaction yet");

    editButton(root, "commit").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "applied");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    assertEq(editButton(root, "undo").disabled, false);
    assert(/已应用/.test(editStatus(root).textContent));
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    assertEq(t.store.snapshot().revision, storeBefore.revision);
    assertEq(t.store.snapshot().measurements.length, 0, "no store measurement rows either");

    editButton(root, "undo").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(lastModelText(t), originalModel, "undo restores the exact original frame");
    assert(/1\.000 Å/.test(textOf(measureRow(root))), "measurement value returns to the original");
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    assertEq(t.store.snapshot().revision, storeBefore.revision);
    assertEq(editButton(root, "redo").disabled, false);

    editButton(root, "redo").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "applied");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    assertEq(lastModelText(t), previewModel, "redo restores the edited frame exactly");

    // A second transaction stacks on the first; two undos restore the original.
    applyTarget(root, itemId, "2.0");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "preview");
    editButton(root, "commit").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "2");
    assert(lastModelText(t).indexOf("C 1.000000 0.000000 0.000000") !== -1,
      "the second edit is measured from the first edited geometry");
    editButton(root, "undo").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    assertEq(lastModelText(t), previewModel, "undoing the second edit restores the first result");
    editButton(root, "undo").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(lastModelText(t), originalModel, "undoing the last edit restores the exact original frame");

    editButton(root, "abandon").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(lastModelText(t), originalModel, "abandon restores the original frame exactly");
    assert(/已恢复原始结构/.test(editStatus(root).textContent));
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    assertEq(t.fetchMock.calls.length, 0, "local edits never call the backend");
    t.instance.dispose();
  });

  await test("A-46 validation UI: out-of-range/NaN/blank/same targets never commit", async () => {
    const t = await mountReady({ contextInitial: undefined });
    const root = t.host.children[0];
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");
    const models = named(t.rig.calls, "addModel").length;

    applyTarget(root, itemId, "9.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assert(/0\.4–5\.0 Å/.test(editStatus(root).textContent), "distance range is explicit");
    assertEq(named(t.rig.calls, "addModel").length, models, "invalid target never rebuilds the model");

    applyTarget(root, itemId, "abc");
    await flush();
    assert(/有限数值/.test(editStatus(root).textContent));
    assertEq(root.getAttribute("data-acp-edit-count"), "0");

    applyTarget(root, itemId, "");
    await flush();
    assert(/请输入目标值/.test(editStatus(root).textContent));
    assertEq(root.getAttribute("data-acp-edit-count"), "0");

    applyTarget(root, itemId, "1.000");
    await flush();
    assert(/相同/.test(editStatus(root).textContent), "an unchanged target is an explicit no-op");
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");

    const angleRow = await addAngleMeasurement(t, root, 0, 1, 2);
    const angleId = angleRow.getAttribute("data-measure-id");
    applyTarget(root, angleId, "0");
    await flush();
    assert(/1–179°/.test(editStatus(root).textContent), "angle range is explicit");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(t.store.snapshot().coordinates[0], [-1, 0, 0]);
    t.instance.dispose();
  });

  await test("A-46 preview gate: one transaction at a time; cancel/abandon restore geometry", async () => {
    const t = await mountReady({ contextInitial: undefined });
    const root = t.host.children[0];
    openMeasureDrawer(root);
    await addDistanceMeasurement(t, root, 0, 1);
    // Picking stays active after a completed measurement: add a second row.
    clickMeasurementAtom(t, 2);
    clickMeasurementAtom(t, 3);
    await flush();
    const itemA = measureRow(root, 0).getAttribute("data-measure-id");
    const itemB = measureRow(root, 1).getAttribute("data-measure-id");
    const originalModel = lastModelText(t);

    applyTarget(root, itemA, "1.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "preview");
    assertEq(findByAttr(root, "data-measure-apply", itemB).disabled, true,
      "a second edit cannot start while a preview is pending");
    assertEq(targetInput(root, itemB).disabled, true);
    assertEq(editButton(root, "undo").disabled, true);
    assertEq(editButton(root, "redo").disabled, true);
    assertEq(editButton(root, "abandon").disabled, false);

    editButton(root, "cancel").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(lastModelText(t), originalModel, "cancel preview restores the displayed geometry");

    applyTarget(root, itemA, "1.5");
    await flush();
    editButton(root, "abandon").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(lastModelText(t), originalModel);
    assert(/已恢复原始结构/.test(editStatus(root).textContent));

    findByAttr(root, "data-measure-clear", "1").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(t.store.snapshot().coordinates[0], [-1, 0, 0]);
    t.instance.dispose();
  });

  await test("A-46 switch: structure/task switch and dispose clear the transaction", async () => {
    const initial = {
      taskRef: { jobId: "job-edit-a" },
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE },
      revision: 1,
    };
    const t = await mountReady({ contextInitial: initial });
    const root = t.host.children[0];
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    await commitTarget(t, root, row.getAttribute("data-measure-id"), "1.5");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    const storeAfterEdit = t.store.snapshot();
    assertEq(storeAfterEdit.coordinates[1], [0, 0, 0], "canonical store keeps the original atom 2");

    t.ctx.emit({
      taskRef: { jobId: "job-edit-a" },
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE_NEG },
      revision: 2,
      source: "shell",
    });
    await flush();
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-acp-edit-state"), "idle", "structure switch clears the transaction");
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assert(lastModelText(t).indexOf("H 0.000000 1.000000 -1.000000") !== -1,
      "the switched structure is the one on screen");
    const negModel = lastModelText(t);

    openMeasureDrawer(root);
    const row2 = await addDistanceMeasurement(t, root, 0, 1);
    await commitTarget(t, root, row2.getAttribute("data-measure-id"), "1.5");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");

    t.ctx.emit({
      taskRef: { jobId: "job-edit-b" },
      structureRef: { kind: "inline-xyz", text: XYZ_MEASURE_NEG },
      revision: 3,
      source: "shell",
    });
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "0", "task switch clears the transaction");
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    assertEq(lastModelText(t), negModel, "task switch restores the immutable frame on screen");

    t.instance.dispose();
    t.instance.dispose(); // idempotent with an applied transaction history
    assertEq(t.store.snapshot().entryId, null);
  });

  await test("A-46 blocked: unproven overlay mapping disables editing with an explicit message", async () => {
    const t = makeInstance();
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-edit-block" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click(); // catalog toggle
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [
          { id: "entry-a", group_id: "formal", label: "A", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 } },
          { id: "entry-b", group_id: "formal", label: "B", role: "minimum",
            source: { kind: "formal_result", geometry_ref: "RESULT/b.xyz", frame_index: 0 } },
        ],
      }),
    });
    await flush();
    root.children[5].children[2].children[0].children[1].click(); // select A
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_MEASURE);
    await flush();
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");

    findByAttr(root, "data-overlay-entry-id", "entry-b").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        ok: false, mapping: null, rmsd: null, max_displacement: null,
        n_mapped: 0, reason: "unproven",
      }),
    });
    await flush();
    t.fetchMock.respondXyzAt(3, XYZ_MEASURE);
    await flush();
    assertEq(root.getAttribute("data-acp-overlay-measurements"), "blocked");
    assertEq(root.getAttribute("data-acp-edit-state"), "blocked");
    assert(/映射未证实 · 编辑已禁用/.test(editStatus(root).textContent));
    assertEq(findByAttr(root, "data-measure-apply", itemId).disabled, true);
    assertEq(targetInput(root, itemId).disabled, true);
    assertEq(editButton(root, "undo").disabled, true);

    // A click on the disabled control must stay inert (explicit message, no transaction).
    applyTarget(root, itemId, "1.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(root.getAttribute("data-acp-edit-state"), "blocked");
    assertEq(t.store.snapshot().coordinates[1], [0, 0, 0]);
    t.instance.dispose();
  });

  await test("A-46 blocked: IRC playback pauses editing with an explicit message", async () => {
    const t = makeInstance({ ircFrameMs: 1000 });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-edit-irc" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    findByAttr(root, "data-irc-play", "forward").click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "irc_forward", label: "Forward" }],
        entries: [
          { id: "f0", group_id: "irc_forward", label: "f0", source: { kind: "irc", frame_index: 0 } },
          { id: "f1", group_id: "irc_forward", label: "f1", source: { kind: "irc", frame_index: 1 } },
        ],
      }),
    });
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_MEASURE);
    t.fetchMock.respondXyzAt(2, XYZ_MEASURE_NEG);
    await sleep(30);
    assertEq(root.getAttribute("data-acp-irc-state"), "playing");
    assertEq(root.getAttribute("data-acp-edit-state"), "blocked");
    assert(/IRC 播放中 · 编辑已暂停/.test(editStatus(root).textContent));
    assertEq(editButton(root, "undo").disabled, true);
    findByAttr(root, "data-irc-stop", "1").click();
    await flush();
    t.instance.dispose();
  });

  await test("A-46 blocked: vibration animation discards the local edit and pauses editing", async () => {
    const t = makeInstance({ vibrationFrameMs: 1000 });
    await t.instance.mount(t.host, { revision: 0, source: "test" });
    t.ctx.emit({ taskRef: { jobId: "job-edit-vib" }, revision: 1, source: "shell" });
    await flush();
    const root = t.host.children[0];
    root.children[4].click();
    await flush();
    t.fetchMock.respondAt(0, {
      ok: true,
      json: () => Promise.resolve({
        availability: "ready",
        groups: [{ id: "formal", label: "正式结果" }],
        entries: [{
          id: "entry-a", group_id: "formal", label: "Structure A",
          source: { kind: "formal_result", geometry_ref: "RESULT/a.xyz", frame_index: 0 },
        }],
      }),
    });
    await flush();
    root.children[5].children[2].children[0].children[1].click();
    await flush();
    t.fetchMock.respondXyzAt(1, XYZ_MEASURE);
    await flush();
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    await commitTarget(t, root, row.getAttribute("data-measure-id"), "1.5");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");

    findByAttr(root, "data-vibration-toggle", "1").click();
    await flush();
    t.fetchMock.respondAt(2, {
      ok: true,
      json: () => Promise.resolve({
        available: true, atom_count: 4, imaginary_count: 1, source: "normal_modes",
        modes: [{
          mode_index: 5, frequency_cm1: -50.0, imaginary: true, ir_intensity: null,
          vectors: [[0, 1, 0], [0, 1, 0], [0, 1, 0], [0, 1, 0]],
        }],
      }),
    });
    await flush();
    findByAttr(root, "data-vibration-play", "1").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "0",
      "playback switches away from local edit geometry: the transaction is dropped");
    assertEq(root.getAttribute("data-acp-edit-state"), "blocked");
    assert(/振动动画播放中 · 编辑已暂停/.test(editStatus(root).textContent));
    assertEq(findByAttr(root, "data-vibration-play", "1").textContent, "停止");
    t.instance.dispose();
  });

  await test("A-46 busy: a reentrant apply during commit is ignored (one transaction only)", async () => {
    const t = await mountReady({ contextInitial: undefined });
    const root = t.host.children[0];
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");
    applyTarget(root, itemId, "1.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "preview");

    let busyDuringRender = null;
    let hookRuns = 0;
    t.rig.setRenderHook(() => {
      hookRuns += 1;
      busyDuringRender = root.getAttribute("data-acp-edit-busy");
      findByAttr(root, "data-edit-commit", "1").click(); // reentrant click
    });
    editButton(root, "commit").click();
    await flush();
    assertEq(hookRuns, 1, "the commit render ran exactly once");
    assertEq(busyDuringRender, "true", "busy state is visible while the transaction commits");
    assertEq(root.getAttribute("data-acp-edit-count"), "1", "the reentrant apply was ignored");
    assertEq(root.getAttribute("data-acp-edit-state"), "applied");
    assertEq(root.getAttribute("data-acp-edit-busy"), "false");
    t.instance.dispose();
  });

  await test("A-47 save: committed edit → confirmation → exact structure-assets body + receipt", async () => {
    const t = await mountAssetReady();
    const root = t.host.children[0];
    assertEq(root.getAttribute("data-acp-module-state"), "ready");
    assertEq(root.getAttribute("data-acp-edit-state"), "idle");
    openMeasureDrawer(root);
    assertEq(root.getAttribute("data-acp-asset-state"), "idle",
      "no committed edit → no savable asset");
    assertEq(assetSaveButton(root).disabled, true);
    assert(/应用一次本地编辑/.test(assetStatus(root).textContent));
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 0, "a gated save never reaches the backend");

    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");
    await commitTarget(t, root, itemId, "1.5");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    assertEq(root.getAttribute("data-acp-asset-state"), "ready");
    assertEq(assetSaveButton(root).disabled, false);
    const editedModel = lastModelText(t);
    const storeBefore = t.store.snapshot();

    setAssetField(assetNameInput(root), "my edited asset");
    setAssetField(assetNoteInput(root), "A-47 note");
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 0, "first click is a confirmation, not a request");
    assertEq(root.getAttribute("data-acp-asset-confirm"), "true");
    assert(/确认将当前显示几何保存为结构资产/.test(assetStatus(root).textContent));
    assert(/my edited asset/.test(assetStatus(root).textContent));
    assertEq(assetCancelButton(root).hidden, false);

    assetCancelButton(root).click();
    await flush();
    assertEq(root.getAttribute("data-acp-asset-confirm"), "false");
    assertEq(assetCancelButton(root).hidden, true);
    assertEq(t.requestMock.calls.length, 0, "cancelling the confirmation never writes");

    clickAssetSave(root);
    await flush();
    assertEq(root.getAttribute("data-acp-asset-confirm"), "true");
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 1, "exactly one asset write");
    const call = t.requestMock.calls[0];
    assertEq(call.url, "/api/v1/structure-assets");
    assertEq(call.opts.method, "POST");
    assertEq(call.opts.headers["Content-Type"], "application/json");
    const body = JSON.parse(call.opts.body);
    assertEq(body.name, "my edited asset");
    assertEq(body.parent_job_id, "job-asset");
    assertEq(body.parent_entry_id, "entry-1");
    assertEq(body.project_id, "proj-asset");
    assertEq(body.edit_operations.length, 1);
    assertEq(body.edit_operations[0].type, "bond_length");
    assertEq(body.edit_operations[0].atom_ids, [0, 1]);
    assertEq(body.edit_operations[0].moved_atom_ids, [1]);
    assertEq(body.edit_operations[0].collision_warnings, []);
    assertEq(body.edit_operations[0].before.length, 4);
    assertEq(body.edit_operations[0].after[1], [0.5, 0, 0]);
    assertEq(body.provenance.op_count, 1);
    assertEq(body.provenance.note, "A-47 note");
    assert(/job-asset/.test(body.provenance.comment) &&
      /entry-1/.test(body.provenance.comment), "provenance names the parent ids");
    assert(body.xyz_text.indexOf("4\n") === 0, "saved text is a 4-atom XYZ");
    assert(body.xyz_text.indexOf("C 0.500000 0.000000 0.000000") !== -1,
      "saved XYZ is the displayed edited geometry");
    assert(/ACP molecule-viewer edit/.test(body.xyz_text.split("\n")[1]),
      "XYZ comment records the edit provenance");
    assertEq(root.getAttribute("data-acp-asset-state"), "saving");
    assertEq(root.getAttribute("data-acp-asset-busy"), "true");
    assert(/保存中/.test(assetStatus(root).textContent));
    assertEq(editButton(root, "undo").disabled, true, "the transaction is frozen while saving");
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates,
      "saving never writes canonical store coordinates");

    t.requestMock.resolveAt(0, {
      ok: true,
      asset_id: "asset-abc",
      name: "my edited asset",
      atom_count: 4,
      formula: "C3H",
      asset_path: "proj-asset/_uploads/asset-abc/asset-abc_normalized.xyz",
      metadata: { parent_job_id: "job-asset", op_count: 1 },
    });
    await flush();
    assertEq(root.getAttribute("data-acp-asset-state"), "saved");
    assertEq(root.getAttribute("data-acp-asset-id"), "asset-abc");
    assertEq(root.getAttribute("data-acp-asset-path"),
      "proj-asset/_uploads/asset-abc/asset-abc_normalized.xyz");
    assertEq(root.getAttribute("data-acp-asset-busy"), "false");
    assert(/已保存为资产：asset-abc/.test(assetStatus(root).textContent));
    assert(/asset-abc/.test(assetReceipt(root).textContent));
    assert(/4 原子/.test(assetReceipt(root).textContent));
    assertEq(assetSaveButton(root).disabled, true, "no duplicate save of the same edit");
    assertEq(root.getAttribute("data-acp-edit-count"), "1", "the local transaction stays intact");
    assertEq(root.getAttribute("data-acp-edit-busy"), "false");
    assertEq(lastModelText(t), editedModel, "the displayed edit is unchanged by the save");
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    assertEq(t.store.snapshot().revision, storeBefore.revision);
    assertEq(t.store.snapshot().measurements.length, 0);
    assertEq(t.fetchMock.calls.length, 1, "the save used the client request seam, not deps.fetch");
    t.instance.dispose();
  });

  await test("A-47 failure: backend error keeps the whole edit + retry succeeds", async () => {
    const t = await mountAssetReady();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    await commitTarget(t, root, row.getAttribute("data-measure-id"), "1.5");
    const editedModel = lastModelText(t);
    const storeBefore = t.store.snapshot();
    assertEq(root.getAttribute("data-acp-edit-count"), "1");

    await requestAssetSave(root);
    assertEq(t.requestMock.calls.length, 1);
    t.requestMock.rejectAt(0, new Error("HTTP 500 server exploded"));
    await flush();
    assertEq(root.getAttribute("data-acp-asset-state"), "ready");
    assertEq(root.getAttribute("data-acp-asset-error"), "true");
    assertEq(root.getAttribute("data-acp-asset-id"), "");
    assert(/保存失败/.test(assetStatus(root).textContent));
    assert(/HTTP 500 server exploded/.test(assetStatus(root).textContent));
    assert(/可重试/.test(assetStatus(root).textContent));
    assertEq(root.getAttribute("data-acp-edit-count"), "1",
      "a failed save must not drop the committed transaction");
    assertEq(root.getAttribute("data-acp-edit-state"), "applied");
    assertEq(root.getAttribute("data-acp-edit-busy"), "false");
    assertEq(lastModelText(t), editedModel, "the edited geometry is still displayed");
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    assertEq(assetSaveButton(root).disabled, false, "the failed save stays retryable");

    await requestAssetSave(root);
    assertEq(t.requestMock.calls.length, 2, "retry issues a second explicit request");
    t.requestMock.resolveAt(1, { ok: true, asset_id: "asset-retry", asset_path: "p/a.xyz", atom_count: 4 });
    await flush();
    assertEq(root.getAttribute("data-acp-asset-state"), "saved");
    assertEq(root.getAttribute("data-acp-asset-id"), "asset-retry");
    assertEq(root.getAttribute("data-acp-edit-count"), "1");
    assertEq(lastModelText(t), editedModel);
    assertEq(t.store.snapshot().coordinates, storeBefore.coordinates);
    t.instance.dispose();
  });

  await test("A-47 no-save gates: original, preview and undo-all never write", async () => {
    const t = await mountAssetReady();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    assertEq(root.getAttribute("data-acp-asset-state"), "idle");
    assertEq(assetSaveButton(root).disabled, true);
    assert(/不提供保存原始结构/.test(assetStatus(root).textContent),
      "saving the untouched original is explicitly not offered");
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 0);

    const row = await addDistanceMeasurement(t, root, 0, 1);
    const itemId = row.getAttribute("data-measure-id");
    applyTarget(root, itemId, "1.5");
    await flush();
    assertEq(root.getAttribute("data-acp-edit-state"), "preview");
    assert(/尚未提交/.test(assetStatus(root).textContent),
      "a pending preview is an explicit no-save gate, got " + assetStatus(root).textContent);
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 0, "a pending preview cannot be saved");

    await flush();
    editButton(root, "commit").click();
    await flush();
    assertEq(root.getAttribute("data-acp-asset-state"), "ready");
    assertEq(assetSaveButton(root).disabled, false);

    editButton(root, "undo").click();
    await flush();
    assertEq(root.getAttribute("data-acp-edit-count"), "0");
    assertEq(root.getAttribute("data-acp-asset-state"), "idle");
    assertEq(assetSaveButton(root).disabled, true);
    assertEq(root.getAttribute("data-acp-asset-id"), "",
      "no receipt exists when nothing was ever saved");
    clickAssetSave(root);
    await flush();
    assertEq(t.requestMock.calls.length, 0, "undoing every edit leaves nothing to save");
    t.instance.dispose();
  });

  await test("A-47 busy/dispose/late: one in-flight save, and stale results never write", async () => {
    const t = await mountAssetReady();
    const root = t.host.children[0];
    openMeasureDrawer(root);
    const row = await addDistanceMeasurement(t, root, 0, 1);
    await commitTarget(t, root, row.getAttribute("data-measure-id"), "1.5");
    await requestAssetSave(root);
    assertEq(t.requestMock.calls.length, 1);
    assertEq(root.getAttribute("data-acp-asset-state"), "saving");
    assertEq(root.getAttribute("data-acp-asset-busy"), "true");
    clickAssetSave(root); // reentrant click while the request is pending
    await flush();
    assertEq(t.requestMock.calls.length, 1, "a busy save never issues a second request");
    assertEq(root.getAttribute("data-acp-edit-count"), "1",
      "the local transaction is frozen, not dropped, while saving");

    t.instance.dispose();
    t.requestMock.resolveAt(0, { ok: true, asset_id: "late-after-dispose" });
    await flush();
    assertEq(t.host.children.length, 0, "dispose removed the module DOM");
    assertEq(t.store.snapshot().entryId, null, "dispose still resets the owned store");

    const t2 = await mountAssetReady();
    const root2 = t2.host.children[0];
    openMeasureDrawer(root2);
    const row2 = await addDistanceMeasurement(t2, root2, 0, 1);
    await commitTarget(t2, root2, row2.getAttribute("data-measure-id"), "1.5");
    await requestAssetSave(root2);
    assertEq(root2.getAttribute("data-acp-asset-state"), "saving");

    // A structure switch invalidates the in-flight save before it can record.
    t2.ctx.emit({
      projectId: "proj-asset",
      taskRef: { jobId: "job-asset" },
      structureRef: { kind: "inline-xyz", text: XYZ_B },
      revision: 2,
      source: "shell",
    });
    await flush();
    assertEq(root2.getAttribute("data-acp-module-state"), "ready");
    assertEq(root2.getAttribute("data-acp-edit-count"), "0");
    assertEq(root2.getAttribute("data-acp-asset-id"), "");
    assertEq(root2.getAttribute("data-acp-asset-state"), "idle");
    t2.requestMock.resolveAt(0, { ok: true, asset_id: "late-after-switch" });
    await flush();
    assertEq(root2.getAttribute("data-acp-asset-id"), "",
      "a late success from the old structure is discarded");
    assertEq(root2.getAttribute("data-acp-asset-state"), "idle");
    assertEq(assetReceipt(root2).hidden, true);
    t2.instance.dispose();
  });

  await test("A-47 pure: name sanitation, payload body and response receipt validation", () => {
    const { entry } = setup();
    const p = entry._internals;
    assertEq(p.STRUCTURE_ASSETS_PATH, "/structure-assets");
    assertEq(p.sanitizeAssetName(" a/b:c* ", "fallback"), "a b c");
    assertEq(p.sanitizeAssetName("", "fallback"), "fallback");
    assertEq(p.sanitizeAssetName("../", "fallback"), "fallback");
    assertEq(p.sanitizeAssetName("x".repeat(120), "fallback").length, 80);
    assertEq(p.EDIT_OPERATION_TYPES.distance, "bond_length");
    assertEq(p.EDIT_OPERATION_TYPES.angle, "bond_angle");

    const atoms = [
      { elem: "C", x: -1, y: 0, z: 0 },
      { elem: "C", x: 0, y: 0, z: 0 },
      { elem: "C", x: 0, y: 1, z: 0 },
      { elem: "H", x: 0, y: 1, z: 1 },
    ];
    const displaced = p.displaceGeometry("distance", atoms, [0, 1], 1.5);
    const transaction = {
      id: "edit-1",
      type: "distance",
      atomIds: [0, 1],
      beforeAtoms: atoms,
      afterAtoms: displaced.atoms,
      beforeValue: 1,
      afterValue: 1.5,
      movedAtomIds: [1],
    };
    const built = p.buildStructureAssetPayload({
      atoms: displaced.atoms,
      transactions: [transaction],
      name: "a/b",
      note: " note ",
      parentJobId: "job-1",
      parentEntryId: "entry-1",
      projectId: "proj-1",
      fallbackName: "fallback",
      createdAt: "2026-01-01T00:00:00.000Z",
    });
    assertEq(built.ok, true);
    assertEq(built.body.name, "a b");
    assertEq(built.body.parent_job_id, "job-1");
    assertEq(built.body.parent_entry_id, "entry-1");
    assertEq(built.body.project_id, "proj-1");
    assertEq(built.body.provenance.created_at, "2026-01-01T00:00:00.000Z");
    assertEq(built.body.provenance.source, "molecule-viewer");
    assertEq(built.body.provenance.op_count, 1);
    assertEq(built.body.provenance.note, "note");
    assertEq(built.body.edit_operations[0].type, "bond_length");
    assertEq(built.body.edit_operations[0].after[1], [0.5, 0, 0]);
    assertEq(built.body.xyz_text.split("\n")[0], "4");
    assertEq(built.body.xyz_text.split("\n")[1].indexOf("ACP molecule-viewer edit"), 0);

    assertEq(p.buildStructureAssetPayload({ atoms: atoms, transactions: [], name: "x" }).reason,
      "no_committed_edit");
    assertEq(p.buildStructureAssetPayload({ atoms: [], transactions: [transaction], name: "x" }).reason,
      "no_structure");
    assertEq(p.buildStructureAssetPayload({
      atoms: [{ elem: "C", x: "not-a-number", y: 0, z: 0 }],
      transactions: [transaction],
      name: "x",
    }).reason, "invalid_geometry");

    const receipt = p.assetReceiptFromResponse({
      ok: true, asset_id: "asset-1", asset_path: "p/a.xyz", atom_count: 4, formula: "C3H",
    });
    assertEq(receipt.ok, true);
    assertEq(receipt.receipt.assetId, "asset-1");
    assertEq(receipt.receipt.assetPath, "p/a.xyz");
    assertEq(receipt.receipt.atomCount, 4);
    assertEq(p.assetReceiptFromResponse({ ok: true }).ok, false);
    assertEq(p.assetReceiptFromResponse({ ok: false, errors: ["bad xyz"] }).message, "bad xyz");
    assertEq(p.assetReceiptFromResponse(null).ok, false);
  });

  await test("A-45/A-46/A-47 source pin: bounded scene, no vendor literal, no store writes, no task commands", () => {
    const fs = require("fs");
    const path = require("path");
    const src = fs.readFileSync(
      path.resolve(__dirname, "../modules/molecule-viewer/module.js"), "utf8");
    assert(src.indexOf("$" + "3Dmol") === -1, "module never references the vendor library literal");
    assertEq((src.match(/ACPVisualization\.scene\.create/g) || []).length, 1,
      "exactly one scene creation station");
    assert(src.indexOf("modelToken.setClickable(") >= 0,
      "picking goes through the bounded model token setClickable");
    assert(src.indexOf("scene.addLabel(") >= 0, "labels go through the bounded scene addLabel");
    assert(src.indexOf('"addCylinder"') >= 0, "measurement lines go through the bounded scene");
    assert(src.indexOf("ACPGeometryStore.addMeasurement") === -1,
      "module never writes measurements into the geometry store (single owner stays the store's own UI)");
    assert(src.indexOf("store.updateCoordinates") === -1,
      "A-46 edits never write canonical store coordinates");
    assert(src.indexOf("store.setEditPreview") === -1,
      "A-46 keeps its preview inside the module (no store preview)");
    assert(src.indexOf("store.markApplied") === -1, "no store measurement bookkeeping from the module");
    assert(src.indexOf("applyMeasuredEdit") === -1,
      "the module keeps its own local transaction (no legacy editor call)");
    assert(src.indexOf("displaceGeometry(") >= 0, "A-46 local displacement math is present");
    assert(src.indexOf("data-acp-edit-state") >= 0, "four local edit states are mirrored on the root");
    assert(src.indexOf("currentFrame.atoms =") === -1, "the stored frame is never mutated by edits");
    assert(src.indexOf("fetch(") === -1, "no direct network access in the module");
    assert(src.indexOf("submit") === -1, "no task submission path in the module");
    // A-47: the save goes through the injected client request seam and records
    // only the returned asset id/path; no second geometry store or XHR path.
    assert((src.match(/\/structure-assets/g) || []).length === 1,
      "exactly one structure-assets endpoint constant");
    assert(src.indexOf("window.ACPClient") >= 0 && src.indexOf("transport.request") >= 0,
      "the fallback asset transport is the page client stack");
    assert(src.indexOf("XMLHttpRequest") === -1, "no XMLHttpRequest path");
    assert(src.indexOf("sendBeacon") === -1, "no sendBeacon path");
    assert(src.indexOf("data-acp-asset-state") >= 0, "four asset states are mirrored on the root");
    assert(src.indexOf("data-acp-asset-id") >= 0, "the returned asset id is recorded in the UI");
    assert(src.indexOf("data-acp-asset-path") >= 0, "the returned asset ref is recorded in the UI");
    assert(src.indexOf("assetState.receipt") >= 0,
      "the save receipt is module-local metadata, not a geometry copy");
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
