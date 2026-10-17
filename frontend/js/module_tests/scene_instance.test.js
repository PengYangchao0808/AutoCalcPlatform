/* Module tests for ACPVisualization.scene — R5 scene instance (module-only slice, plan §42.3 R5 / §42.4 实例生命周期). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const EXPECTED_KEYS = [
    "addCylinder", "addModel", "addSphere", "addStyle", "clearScene",
    "dispose", "frame", "isDisposed", "pausePlayback", "play", "pngURI",
    "removeAllLabels", "removeAllModels", "removeAllShapes", "render",
    "resize", "resumePlayback", "setBackgroundColor", "setHidden",
    "setStyle", "setView", "stopPlayback", "zoomTo",
  ];

  // Recording stub viewer: every native method is a spy pushing [name, ...args].
  function makeStubViewer() {
    const calls = [];
    const rec = (name) => (...args) => { calls.push([name, ...args]); };
    const viewer = {
      addModel: rec("addModel"),
      setStyle: rec("setStyle"),
      addStyle: rec("addStyle"),
      addSphere: rec("addSphere"),
      addCylinder: rec("addCylinder"),
      removeAllModels: rec("removeAllModels"),
      removeAllShapes: rec("removeAllShapes"),
      removeAllLabels: rec("removeAllLabels"),
      setBackgroundColor: rec("setBackgroundColor"),
      setView: rec("setView"),
      zoomTo: rec("zoomTo"),
      render: rec("render"),
      resize: rec("resize"),
      clear: rec("clear"),
      pngURI: () => { calls.push(["pngURI"]); return "data:image/png;base64,stub"; },
    };
    viewer.__calls = calls;
    return viewer;
  }

  function loadScene() {
    const sandbox = makeSandbox({});
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    loadScript("js/visualization/scene_instance.js", sandbox);
    return sandbox.window.ACPVisualization;
  }

  await test("scene: public key set is exactly the whitelist; no raw viewer leakage", () => {
    const viz = loadScene();
    assertEq(Object.keys(viz.scene).sort(), ["create"]);
    const viewer = makeStubViewer();
    const sc = viz.scene.create({ viewer });
    assertEq(Object.keys(sc).sort(), EXPECTED_KEYS, "instance key set drifted");
    for (const k of Object.keys(sc)) {
      assert(typeof sc[k] === "function", "key " + k + " is a function");
      assert(sc[k] !== viewer, "no instance property is the raw viewer");
    }
    assertEq(viz.scene.create({}), null, "viewer is required for this slice");
    assertEq(sc.isDisposed(), false);
  });

  await test("scene: delegation forwards arguments verbatim, once each", () => {
    const viz = loadScene();
    const viewer = makeStubViewer();
    const sc = viz.scene.create({ viewer });
    const sel = { model: 0 };
    const spec = { stick: { radius: 0.1 } };
    const sphere = { center: { x: 0, y: 0, z: 0 }, radius: 1.2, color: "red" };
    const view = [1, 0, 0, 0, 1, 0, 0, 0, 1];
    sc.addModel("C 0 0 0", "xyz");
    sc.setStyle(sel, spec);
    sc.addSphere(sphere);
    sc.setView(view);
    sc.render();
    sc.resize();
    assertEq(sc.pngURI(), "data:image/png;base64,stub");
    assertEq(viewer.__calls, [
      ["addModel", "C 0 0 0", "xyz"],
      ["setStyle", sel, spec],
      ["addSphere", sphere],
      ["setView", view],
      ["render"],
      ["resize"],
      ["pngURI"],
    ]);
  });

  await test("scene: clearScene clears models + shapes + labels", () => {
    const viz = loadScene();
    const viewer = makeStubViewer();
    const sc = viz.scene.create({ viewer });
    sc.clearScene();
    assertEq(viewer.__calls.map((c) => c[0]),
      ["removeAllModels", "removeAllShapes", "removeAllLabels"]);
  });

  await test("scene: play/stop/pause/resume + hidden-pause, one interval per instance", async () => {
    const viz = loadScene();
    const viewer = makeStubViewer();
    const sc = viz.scene.create({ viewer });

    // play fires onTick; stop halts it
    let ticks = 0;
    const id = sc.play(() => { ticks++; }, 5);
    assert(id !== null && id !== undefined, "play returns an id");
    await sleep(24);
    assert(ticks >= 2, "onTick fired repeatedly");
    sc.stopPlayback();
    const stoppedAt = ticks;
    await sleep(20);
    assertEq(ticks, stoppedAt, "stopPlayback halts ticking");

    // restarting play cancels the previous timer synchronously → no double firing
    let a = 0, b = 0;
    sc.play(() => { a++; }, 5);
    await sleep(18);
    const aAtRestart = a;
    sc.play(() => { b++; }, 5);
    await sleep(20);
    assertEq(a, aAtRestart, "restart cancels the previous interval (no double firing)");
    assert(b >= 1, "restarted interval ticks");
    sc.stopPlayback();

    // pause/resume keeps the interval, gates the ticks
    let n = 0;
    sc.play(() => { n++; }, 5);
    sc.pausePlayback();
    const nPaused = n;
    await sleep(20);
    assertEq(n, nPaused, "paused instance does not tick");
    sc.resumePlayback();
    await sleep(20);
    assert(n > nPaused, "resumePlayback resumes ticking");

    // hidden pauses ticking without losing the playing state
    sc.setHidden(true);
    const nHidden = n;
    await sleep(20);
    assertEq(n, nHidden, "hidden pauses ticking");
    sc.setHidden(false);
    await sleep(20);
    assert(n > nHidden, "unhide resumes while still playing");
    sc.stopPlayback();

    // setHidden on a stopped instance does not start it
    const nStopped = n;
    sc.setHidden(true);
    await sleep(15);
    sc.setHidden(false);
    await sleep(15);
    assertEq(n, nStopped, "setHidden does not start a stopped instance");
  });

  await test("scene: dispose idempotent; no late onTick/native calls; destroyImpl+onDispose once", async () => {
    const viz = loadScene();
    const viewer = makeStubViewer();
    const destroyed = [];
    const disposed = [];
    const sc = viz.scene.create({
      viewer,
      destroyImpl: (v) => destroyed.push(v),
      onDispose: () => disposed.push(1),
    });
    let ticks = 0;
    sc.play(() => { ticks++; }, 5);
    await sleep(16);
    assert(ticks >= 1, "ticking before dispose");
    sc.dispose();
    sc.dispose(); // idempotent
    const ticksAtDispose = ticks;
    const callsAtDispose = viewer.__calls.length;
    await sleep(30);
    assertEq(ticks, ticksAtDispose, "no onTick fires after dispose");
    sc.render();
    sc.addModel("x", "xyz");
    sc.pngURI();
    sc.frame();
    await sleep(15);
    assertEq(viewer.__calls.length, callsAtDispose, "no native calls after dispose");
    assertEq(destroyed, [viewer], "destroyImpl called exactly once with the viewer");
    assertEq(disposed, [1], "onDispose called exactly once");
    assertEq(sc.isDisposed(), true);
  });

  await test("scene: default destroy routes through mol3d.destroy; stub-tolerant", () => {
    const viz = loadScene();
    const viewer = makeStubViewer();
    const sc = viz.scene.create({ viewer });
    sc.dispose();
    sc.dispose();
    const names = viewer.__calls.map((c) => c[0]);
    assertEq(names.filter((x) => x === "removeAllModels").length, 1, "clearScene models once");
    assertEq(names.filter((x) => x === "removeAllShapes").length, 1, "clearScene shapes once");
    assertEq(names.filter((x) => x === "removeAllLabels").length, 1, "clearScene labels once");
    assertEq(names.filter((x) => x === "clear").length, 1, "mol3d destroy ran exactly once");
    // minimal stub without clear(): mol3d.destroy degrades to a no-op — dispose must not throw
    const bare = { render() {} };
    const sc2 = viz.scene.create({ viewer: bare });
    sc2.dispose();
    assertEq(sc2.isDisposed(), true);
  });

  await test("scene: two instances are independent; disposing A leaves B working", async () => {
    const viz = loadScene();
    const stubA = makeStubViewer();
    const stubB = makeStubViewer();
    const a = viz.scene.create({ viewer: stubA });
    const b = viz.scene.create({ viewer: stubB });
    let bTicks = 0;
    b.play(() => { bTicks++; }, 5);
    a.dispose();
    const aCallsAtDispose = stubA.__calls.length;
    b.render();
    b.addModel("B", "xyz");
    await sleep(20);
    assert(bTicks >= 2, "B still ticks after A disposed");
    assert(stubB.__calls.length >= 2, "B still renders after A disposed");
    assertEq(stubA.__calls.length, aCallsAtDispose, "A stays quiet; B unaffected");
    assertEq(b.isDisposed(), false);
    assertEq(a.isDisposed(), true);
    b.dispose();
  });

  await test("scene: frame() delegates to mol3d.frame (shared framing entry); unavailable → null", () => {
    const viz = loadScene();
    const calls = [];
    const viewer = {
      resize: () => calls.push("resize"),
      center: () => calls.push("center"),
      zoomTo: () => calls.push("zoomTo"),
      zoom: () => calls.push("zoom"),
      render: () => calls.push("render"),
      clear: () => calls.push("clear"),
    };
    const sc = viz.scene.create({ viewer });
    sc.frame();
    assertEq(calls, ["resize", "center", "zoomTo", "render"], "framing via the shared entry");
    sc.dispose();
    assertEq(sc.frame(), null, "disposed scene does not frame");

    const bareSandbox = makeSandbox({});
    loadScript("js/visualization/scene_instance.js", bareSandbox);
    const sc2 = bareSandbox.window.ACPVisualization.scene.create({ viewer });
    assertEq(sc2.frame(), null, "missing mol3d namespace → null, no throw");
  });

  await test("scene: source purity — no DOM/network/event-stream/raw-library literals", () => {
    const fs = require("fs");
    const path = require("path");
    const src = fs.readFileSync(path.join(__dirname, "..", "visualization", "scene_instance.js"), "utf8");
    assert(!src.includes("document."), "no document.* access");
    assert(!src.includes("fetch("), "no fetch");
    assert(!src.includes("EventSource"), "no EventSource");
    assert(!src.includes("$3Dmol"), "no raw viewer-library reference");
    assert(!src.includes("withNative"), "no raw-handle escape hatch");
  });
};
