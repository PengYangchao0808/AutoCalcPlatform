/*
 * T15 — consolidated scene-instance lifecycle matrix (cross-cutting rows).
 *
 * Runs the shared lifecycle matrix ONCE against the bounded instance, for the
 * dimensions every migrated scenario shares (main structure T8, energy T9,
 * wizard preview T10, reaction T11, NMR T12, vibration T13, structure
 * view/edit T14):
 *   1. repeated mount          (mount -> dispose -> remount; no residue)
 *   2. A -> B -> A task switch (model generations; stale callbacks die)
 *   3. hide / show             (pause ticking, keep playing state)
 *   4. pause / stop            (pause -> resume reuses the interval; stop halts)
 *   5. destroy + late callback (no onTick, no native call, no business tick)
 *   6. multi-instance camera + selection independence
 *   7. missing-3Dmol degradation (null, never throws)
 *   8. PNG export              (string while live; explicit null after dispose)
 *
 * Scenario-specific matrix rows live with their per-scenario harnesses
 * (energy_viewer / wizard_preview_viewer / nmr_viewer / reaction_viewer /
 * vibration_playback .test.js); this file is the cross-cutting aggregate.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---- recording-stub rig (viewer + native models + callback capture) ------
  function makeRig(viewValue) {
    const calls = [];
    const captured = { click: null, hoverIn: null, hoverOut: null };
    const view = viewValue || [1, 2, 3, 4, 5, 6, 7, 8];

    function nativeModel(label) {
      return {
        __nativeModel: true,
        label: label,
        setStyle(sel, style, add) { calls.push(["model.setStyle", label, sel, style, add]); return this; },
        setClickable(sel, val, cb) { calls.push(["model.setClickable", label, sel, val]); captured.click = cb; return this; },
        setHoverable(sel, val, cbIn, cbOut) { calls.push(["model.setHoverable", label, sel, val]); captured.hoverIn = cbIn; captured.hoverOut = cbOut; return this; },
        selectedAtoms() { calls.push(["model.selectedAtoms", label]); return [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }]; },
      };
    }

    const viewer = {
      __calls: calls,
      __lastModel: null,
      addModel(data, spec) { calls.push(["addModel", data, spec]); const m = nativeModel(String(data)); viewer.__lastModel = m; return m; },
      removeModel(m) { calls.push(["removeModel", m]); },
      removeAllModels() { calls.push(["removeAllModels"]); },
      setStyle() { calls.push(["setStyle"]); return viewer; },
      addStyle() { calls.push(["addStyle"]); return viewer; },
      setClickable() { calls.push(["setClickable"]); return viewer; },
      setHoverable() { calls.push(["setHoverable"]); return viewer; },
      selectedAtoms() { return []; },
      addSphere(spec) { calls.push(["addSphere", spec]); return { __nativeShape: true }; },
      addCylinder(spec) { calls.push(["addCylinder", spec]); return { __nativeShape: true }; },
      addArrow(spec) { calls.push(["addArrow", spec]); return { __nativeShape: true }; },
      removeShape(s) { calls.push(["removeShape", s]); },
      removeAllShapes() { calls.push(["removeAllShapes"]); },
      addLabel(text, opts, anchor) { calls.push(["addLabel", text, opts, anchor]); return { __nativeLabel: true }; },
      removeLabel(l) { calls.push(["removeLabel", l]); },
      removeAllLabels() { calls.push(["removeAllLabels"]); },
      setBackgroundColor(c) { calls.push(["setBackgroundColor", c]); return viewer; },
      getView() { calls.push(["getView"]); return view.slice(); },
      setView(v) { calls.push(["setView", v]); return viewer; },
      center() { calls.push(["center"]); return viewer; },
      zoomTo() { calls.push(["zoomTo"]); return viewer; },
      zoom() { calls.push(["zoom"]); return viewer; },
      render() { calls.push(["render"]); },
      resize() { calls.push(["resize"]); },
      clear() { calls.push(["clear"]); },
      pngURI() { calls.push(["pngURI"]); return "data:image/png;base64,matrix"; },
    };
    return { viewer, calls, captured };
  }

  function loadScene() {
    const sandbox = makeSandbox({});
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    loadScript("js/visualization/scene_instance.js", sandbox);
    return sandbox.window.ACPVisualization;
  }

  function count(calls, name) { return calls.filter((c) => c[0] === name).length; }

  // ------------------------------------------------------------------ 1 -----
  await test("matrix 1 (repeated mount): dispose silences mount A; remount is clean, no residue", async () => {
    const viz = loadScene();
    const rigA = makeRig();
    const a = viz.scene.create({ viewer: rigA.viewer });
    const ta = a.addModel("A", "xyz");
    let aStale = 0;
    ta.setClickable({}, true, () => { aStale++; });
    const stale = rigA.captured.click;
    let aTicks = 0;
    a.play(() => { aTicks++; }, 5);
    await sleep(16);
    assert(aTicks >= 1, "mount A ticks");
    a.dispose();
    const aAtDispose = aTicks;
    const aCalls = rigA.calls.length;

    // late business callbacks captured from mount A must never fire again
    if (stale) stale({ index: 0 }, {}, {});

    const rigB = makeRig();
    const b = viz.scene.create({ viewer: rigB.viewer });
    b.addModel("B", "xyz");
    let bTicks = 0;
    b.play(() => { bTicks++; }, 5);
    stale({ index: 0 }, {}, {});
    await sleep(24);
    assertEq(aTicks, aAtDispose, "disposed mount A never ticks again");
    assertEq(aStale, 0, "stale callback counter untouched");
    assert(bTicks >= 1, "remounted instance ticks");
    assertEq(rigA.calls.length, aCalls, "mount A stub stays quiet after dispose");
    assertEq(a.isDisposed(), true);
    assertEq(b.isDisposed(), false);
    b.dispose();
  });

  // ------------------------------------------------------------------ 2 -----
  await test("matrix 2 (A->B->A task switch): stale callbacks die on scene generation change", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });

    const a1 = sc.addModel("A", "xyz");
    let hitsA1 = 0;
    a1.setClickable({}, true, () => { hitsA1++; });
    const cbA1 = rig.captured.click;

    sc.removeAllModels();               // task A -> B (scene generation bump)
    const b1 = sc.addModel("B", "xyz");
    let hitsB = 0;
    b1.setClickable({}, true, () => { hitsB++; });
    const cbB = rig.captured.click;

    sc.removeAllModels();               // task B -> A (back to A)
    const a2 = sc.addModel("A2", "xyz");
    let hitsA2 = 0;
    a2.setClickable({}, true, () => { hitsA2++; });
    const cbA2 = rig.captured.click;

    cbA1({ index: 0 }, {}, {});
    cbB({ index: 0 }, {}, {});
    assertEq(hitsA1, 0, "task A(old) callback dead");
    assertEq(hitsB, 0, "task B callback dead");
    cbA2({ index: 0 }, {}, {});
    assertEq(hitsA2, 1, "current task A(new) callback live");
    assertEq(sc.getModelCount(), 1, "one live model after the switch");
  });

  // ------------------------------------------------------------------ 3 -----
  await test("matrix 3 (hide/show): hidden pauses ticking without losing the playing state", async () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    let n = 0;
    sc.play(() => { n++; }, 5);
    await sleep(16);
    assert(n >= 1, "ticking while visible");
    sc.setHidden(true);
    const hiddenAt = n;
    await sleep(20);
    assertEq(n, hiddenAt, "hidden pauses ticking");
    assertEq(sc.isDisposed(), false, "hide never disposes");
    sc.setHidden(false);
    await sleep(20);
    assert(n > hiddenAt, "unhide resumes while still playing");
    sc.stopPlayback();
    const stoppedAt = n;
    sc.setHidden(true);
    await sleep(15);
    sc.setHidden(false);
    await sleep(15);
    assertEq(n, stoppedAt, "setHidden does not start a stopped instance");
  });

  // ------------------------------------------------------------------ 4 -----
  await test("matrix 4 (pause/stop): resume reuses the interval; stop halts for good", async () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });

    let ticks = 0;
    sc.play(() => { ticks++; }, 5);
    sc.pausePlayback();
    const pausedAt = ticks;
    await sleep(18);
    assertEq(ticks, pausedAt, "paused instance does not tick");
    sc.resumePlayback();
    await sleep(18);
    assert(ticks > pausedAt, "resumePlayback resumes");

    sc.play(() => { ticks++; }, 5); // restart while running
    const seqRestartAt = ticks;
    await sleep(18);
    assert(ticks > seqRestartAt, "restarted interval ticks");
    assertEq(sc.getModelCount(), 0, "restart does not re-add models");

    sc.stopPlayback();
    const stoppedAt = ticks;
    await sleep(18);
    assertEq(ticks, stoppedAt, "stop halts ticking for good");
  });

  // ------------------------------------------------------------------ 5 -----
  await test("matrix 5 (destroy + late callback): no onTick, no native call, no business tick", async () => {
    const viz = loadScene();
    const rig = makeRig();
    const disposed = [];
    const sc = viz.scene.create({ viewer: rig.viewer, destroyImpl: (v) => disposed.push(v) });

    const t = sc.addModel("A", "xyz");
    let businessTicks = 0;
    t.setClickable({}, true, () => { businessTicks++; });
    t.setHoverable({}, true, () => { businessTicks++; }, () => { businessTicks++; });
    const lateClick = rig.captured.click;
    const lateHoverIn = rig.captured.hoverIn;
    const lateHoverOut = rig.captured.hoverOut;

    let playedTicks = 0;
    sc.play(() => { playedTicks++; }, 5);
    sc.dispose();
    sc.dispose(); // idempotent

    const callsAtDispose = rig.calls.length;
    const playedAtDispose = playedTicks;
    const businessAtDispose = businessTicks;

    // late callbacks after destroy must not tick (the red-able assertion)
    lateClick({ index: 0 }, {}, {});
    lateHoverIn({ index: 0 }, {}, {});
    lateHoverOut({ index: 0 }, {}, {});
    assertEq(businessTicks, businessAtDispose, "late business callbacks must not tick");

    sc.render();
    sc.addModel("x", "xyz");
    sc.pngURI();
    sc.frame();
    await sleep(24);
    assertEq(playedTicks, playedAtDispose, "no onTick fires after dispose (timer guard)");
    assertEq(rig.calls.length, callsAtDispose, "no native call after dispose");
    assertEq(disposed, [rig.viewer], "destroyImpl ran exactly once");
    assertEq(sc.isDisposed(), true);
  });

  // ------------------------------------------------------------------ 6 -----
  await test("matrix 6 (multi-instance): camera + selection are independent; tokens never cross", async () => {
    const viz = loadScene();
    const rigA = makeRig([1, 1, 1, 1, 1, 1, 1, 1]);
    const rigB = makeRig([2, 2, 2, 2, 2, 2, 2, 2]);
    const a = viz.scene.create({ viewer: rigA.viewer });
    const b = viz.scene.create({ viewer: rigB.viewer });

    // camera independence
    const va = a.getView();
    const vb = b.getView();
    assertEq(va, [1, 1, 1, 1, 1, 1, 1, 1], "A camera");
    assertEq(vb, [2, 2, 2, 2, 2, 2, 2, 2], "B camera");
    va[0] = 999;
    assertEq(a.getView()[0], 1, "A getView returns an independent copy");

    // selection independence
    const ta = a.addModel("A", "xyz");
    let hitsA = 0, hitsB = 0;
    ta.setClickable({}, true, () => { hitsA++; });
    const cbA = rigA.captured.click;
    const tb = b.addModel("B", "xyz");
    tb.setClickable({}, true, () => { hitsB++; });
    const cbB = rigB.captured.click;
    cbA({ index: 0 }, {}, {});
    cbB({ index: 0 }, {}, {});
    assertEq(hitsA, 1, "A selection fires on A");
    assertEq(hitsB, 1, "B selection fires on B");

    // cross-instance tokens rejected
    assertEq(a.removeModel(tb), false, "A rejects B's model token");
    assertEq(b.removeModel(tb), true, "B accepts its own token");
    const sa = a.addSphere({});
    assertEq(b.removeShape(sa), false, "B rejects A's shape token");
    assertEq(a.removeShape(sa), true);

    // disposing A leaves B untouched
    const bCalls = rigB.calls.length;
    a.dispose();
    b.render();
    assert(rigB.calls.length > bCalls, "B keeps working after A disposed");
    assertEq(b.isDisposed(), false);
    b.dispose();
  });

  // ------------------------------------------------------------------ 7 -----
  await test("matrix 7 (missing-3Dmol): every creation path degrades to null, never throws", () => {
    // bare sandbox: no ACPVisualization.mol3d namespace at all
    const bare = makeSandbox({});
    loadScript("js/visualization/scene_instance.js", bare);
    let threw = null;
    let out = "unset";
    try { out = bare.window.ACPVisualization.scene.create({ container: { id: "c" } }); } catch (e) { threw = e; }
    assertEq(threw, null, "no throw without the library");
    assertEq(out, null, "no container-with-library path -> null");

    // adapter present but $3Dmol undefined -> mol3d.create returns null
    const noLib = makeSandbox({});
    loadScript("js/visualization/framing.js", noLib);
    loadScript("js/visualization/mol3d_adapter.js", noLib);
    loadScript("js/visualization/scene_instance.js", noLib);
    assertEq(noLib.window.ACPVisualization.mol3d.isAvailable(), false, "library reported missing");
    threw = null;
    try { out = noLib.window.ACPVisualization.scene.create({ container: { id: "c" } }); } catch (e) { threw = e; }
    assertEq(threw, null, "no throw with the adapter but no library");
    assertEq(out, null, "mol3d.create null -> scene.create null");
  });

  // ------------------------------------------------------------------ 8 -----
  await test("matrix 8 (PNG export): string while live; explicit null + lastError after dispose", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    sc.addModel("A", "xyz");
    sc.render();
    const data = sc.pngURI();
    assert(typeof data === "string" && data.indexOf("data:image/png") === 0, "PNG data URI while live");
    assertEq(count(rig.calls, "pngURI"), 1, "pngURI routed to the native viewer once");
    sc.dispose();
    assertEq(sc.pngURI(), null, "disposed instance -> explicit null");
    assert(sc.lastError() && sc.lastError().method === "pngURI", "failure recorded after dispose");

    // unavailable pngURI on a live viewer also fails explicitly (no fake success)
    const bare = makeRig();
    delete bare.viewer.pngURI;
    const sc2 = viz.scene.create({ viewer: bare.viewer });
    assertEq(sc2.pngURI(), null, "missing native pngURI -> null");
    assert(sc2.lastError() && sc2.lastError().method === "pngURI", "missing pngURI recorded");
  });
};
