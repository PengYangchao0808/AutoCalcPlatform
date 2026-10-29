/* Behavior tests for visualization framing + mol3d adapter (OC-3). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const ROCls = class {
    constructor(cb) { this.cb = cb; ROCls.instances.push(this); }
    observe() { this.observed = true; }
    disconnect() { this.disconnected = true; }
  };
  ROCls.instances = [];

  function loadViz(extra) {
    const sandbox = makeSandbox(Object.assign({
      requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
      ResizeObserver: ROCls,
    }, extra || {}));
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    return { sandbox, viz: sandbox.window.ACPVisualization, RO: ROCls };
  }

  await test("framing: frame3DViewer calls resize/center/zoomTo/render in order, swallow errors", () => {
    const { viz } = loadViz();
    const calls = [];
    const viewer = {
      resize: () => calls.push("resize"),
      center: () => calls.push("center"),
      zoomTo: () => calls.push("zoomTo"),
      zoom: () => calls.push("zoom"),
      render: () => calls.push("render"),
    };
    viz.framing.frame3DViewer(viewer, { zoomFactor: 1.2 });
    assertEq(calls, ["resize", "center", "zoomTo", "zoom", "render"]);
    viz.framing.frame3DViewer(null); // no throw
    viz.framing.frame3DViewer({ resize() { throw new Error("x"); } }); // swallowed
  });

  await test("framing: preserveView skips center/zoomTo/zoom", () => {
    const { viz } = loadViz();
    const calls = [];
    viz.framing.frame3DViewer({
      resize: () => calls.push("resize"), center: () => calls.push("center"),
      zoomTo: () => calls.push("zoomTo"), zoom: () => calls.push("zoom"), render: () => calls.push("render"),
    }, { preserveView: true });
    assertEq(calls, ["resize", "render"]);
  });

  await test("framing: epoch guard — superseded schedule never frames", async () => {
    const { viz } = loadViz();
    const framed = [];
    const container = { isConnected: true, clientWidth: 300, clientHeight: 200 };
    const state = { epoch: 0 }; // real page framing states initialize epoch
    const getViewer = () => ({ resize() {}, center() {}, zoomTo() {}, zoom() {}, render() { framed.push(1); } });
    viz.framing.scheduleViewerFraming(container, state, getViewer);
    // immediately schedule a newer epoch before rAF ticks run
    viz.framing.scheduleViewerFraming(container, state, getViewer);
    await new Promise((r) => setTimeout(r, 50));
    assertEq(framed.length, 1, "only latest epoch frames");
    assert(state.settled === true && state.pending === false);
    assert(state.observer && state.observer.observed, "resize observer attached");
  });

  await test("framing: dispose disconnects observer and invalidates pending framing", async () => {
    const { viz } = loadViz();
    const framed = [];
    const container = { isConnected: true, clientWidth: 300, clientHeight: 200 };
    const state = {};
    const getViewer = () => ({ resize() {}, center() {}, zoomTo() {}, zoom() {}, render() { framed.push(1); } });
    viz.framing.scheduleViewerFraming(container, state, getViewer);
    viz.framing.disposeViewerFraming(state);
    await new Promise((r) => setTimeout(r, 30));
    assertEq(framed.length, 0, "disposed before stable -> no frame");
    assert(state.observer === null || state.observer.disconnected, "observer disconnected");
    assertEq(state.pending, false);
  });

  await test("mol3d: create returns null without $3Dmol; no throw (library-missing safe)", () => {
    const { viz } = loadViz();
    assertEq(viz.mol3d.isAvailable(), false);
    assertEq(viz.mol3d.create({ clientWidth: 10 }), null);
  });

  await test("mol3d: create delegates to $3Dmol.createViewer with exact options; destroy idempotent", () => {
    const created = [];
    const { viz } = loadViz({
      $3Dmol: {
        createViewer: (container, options) => {
          created.push({ container, options });
          return { clear() { cleared.push(1); } };
        },
      },
    });
    const cleared = [];
    const container = { id: "c1" };
    const opts = { backgroundColor: "0x0e1419", antialias: true };
    assertEq(viz.mol3d.isAvailable(), true);
    const v = viz.mol3d.create(container, opts);
    assertEq(created.length, 1);
    assertEq(created[0].container, container);
    assertEq(created[0].options, opts);
    viz.mol3d.destroy(v);
    viz.mol3d.destroy(v); // idempotent
    viz.mol3d.destroy(null);
    assertEq(cleared.length, 1);
  });

  await test("mol3d: frame() delegates to framing.frame3DViewer (single implementation)", () => {
    const calls = [];
    const { viz } = loadViz();
    const v = { resize() { calls.push("resize"); }, render() { calls.push("render"); } };
    viz.mol3d.frame(v, { preserveView: true });
    assertEq(calls, ["resize", "render"]);
  });

  await test("framing: observer resize skips unchanged size, applies on real change (F09)", async () => {
    const { viz } = loadViz();
    const calls = [];
    const container = { isConnected: true, clientWidth: 300, clientHeight: 200 };
    const state = { epoch: 0 };
    const getViewer = () => ({
      resize() { calls.push("resize"); },
      center() {}, zoomTo() {}, zoom() {},
      render() { calls.push("render"); },
    });
    viz.framing.scheduleViewerFraming(container, state, getViewer);
    await new Promise((r) => setTimeout(r, 60)); // settle pass; lastW/lastH recorded
    assert(state.settled === true, "framing settled before observer fires");
    const resizeCount = () => calls.filter((c) => c === "resize").length;
    assertEq(resizeCount(), 1, "settle framed exactly once");

    // Spurious observer fire (e.g. 3Dmol's own canvas swap) with an unchanged
    // container size must NOT trigger another native resize (audit F09).
    state.observer.cb();
    await new Promise((r) => setTimeout(r, 170));
    assertEq(resizeCount(), 1, "unchanged size → no extra native resize");

    container.clientWidth = 512;
    state.observer.cb();
    await new Promise((r) => setTimeout(r, 170));
    assertEq(resizeCount(), 2, "real size change → resize applied once");
    assertEq(state.lastW, 512, "lastW tracks the applied size");
  });

  await test("modules register public API only; no auto viewer mounting at load", () => {
    const { sandbox } = loadViz({ $3Dmol: { createViewer: () => { throw new Error("must not be called at load"); } } });
    assertEq(Object.keys(sandbox.window.ACPVisualization.framing).sort(),
      ["disposeViewerFraming", "frame3DViewer", "scheduleViewerFraming", "waitForContainerStable"]);
    // Restricted instance API is scene_instance.js (ACPVisualization.scene);
    // the adapter must NOT grow a raw-handle escape hatch (§42 R5).
    assertEq(Object.keys(sandbox.window.ACPVisualization.mol3d).sort(),
      ["create", "destroy", "frame", "isAvailable"]);
  });

  await test("scene: two instances playing independently never share or cancel each other's interval", async () => {
    const { sandbox, viz } = loadViz();
    loadScript("js/visualization/scene_instance.js", sandbox);
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const stub = () => ({
      addModel() { return { setStyle() { return this; }, addStyle() { return this; } }; },
      removeAllModels() {}, removeAllShapes() {}, removeAllLabels() {},
      getView() { return [1, 0, 0, 0]; }, setView() {}, render() {}, clear() {},
    });
    const a = viz.scene.create({ viewer: stub() });
    const b = viz.scene.create({ viewer: stub() });
    let aTicks = 0, bTicks = 0;
    a.play(() => { aTicks++; }, 5);
    b.play(() => { bTicks++; }, 5);
    await sleep(24);
    assert(aTicks >= 2, "instance A ticks on its own interval");
    assert(bTicks >= 2, "instance B ticks on its own interval");

    a.stopPlayback();
    const aAtStop = aTicks, bAtStop = bTicks;
    await sleep(22);
    assertEq(aTicks, aAtStop, "stopping A cancels only A's interval");
    assert(bTicks > bAtStop, "B keeps ticking — intervals are never shared");

    b.pausePlayback();
    const bPaused = bTicks;
    await sleep(16);
    assertEq(bTicks, bPaused, "pausing B leaves only B paused");
    b.stopPlayback();
    a.dispose();
    b.dispose();
  });
};
