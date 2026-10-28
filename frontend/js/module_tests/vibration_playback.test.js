/* Module tests for vibration playback lifecycle on the scene instance (T13):
 * playback routes through instance play/pausePlayback/resumePlayback/setHidden/
 * stopPlayback (one interval per instance), hidden pauses ticking without
 * losing the playing state, and arrows draw through instance shape tokens
 * with a consistent UI count. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const COORDS = [[0, 0, 0], [1, 1, 1], [2, 2, 2]];
  const SYMBOLS = ["C", "O", "C"];
  const VECTORS = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

  function makeNative() {
    const calls = [];
    let addModels = 0;
    const native = {
      __calls: calls,
      addModelCount: () => addModels,
      addModel(data, spec) {
        addModels += 1;
        calls.push(["addModel", data, spec]);
        return { setStyle() { return this; }, addStyle() { return this; } };
      },
      removeModel() {},
      removeAllModels() { calls.push(["removeAllModels"]); },
      setStyle() { return native; },
      addStyle() { return native; },
      setClickable() { return native; },
      setHoverable() { return native; },
      selectedAtoms() { return []; },
      addSphere() { return { __shape: true }; },
      addCylinder() { return { __shape: true }; },
      addArrow(spec) { calls.push(["addArrow", spec]); return { __shape: true, spec }; },
      removeShape() {},
      removeAllShapes() { calls.push(["removeAllShapes"]); },
      addLabel() { return { __label: true }; },
      removeLabel() {},
      removeAllLabels() {},
      getView() { calls.push(["getView"]); return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]; },
      setView(v) { calls.push(["setView", v]); return native; },
      center() { return native; },
      zoom() { return native; },
      zoomTo() { return native; },
      render() { calls.push(["render"]); },
      resize() {},
      clear() { calls.push(["clear"]); },
      pngURI() { return "data:image/png;base64,stub"; },
    };
    return native;
  }

  function seedGeometry(ns) {
    ns.state.jobId = "job1";
    ns.state.entryId = "e1";
    ns.state.data = {
      available: true,
      reason: null,
      modes: [{
        mode_index: 6, frequency_cm1: -797.72, imaginary: true,
        ir_intensity: null, vectors: VECTORS,
      }],
    };
    ns.state.selectedModeIndex = 6;
    ns.arrowState.displayMode = "arrows";
    ns.animationState.phase = 0;
    ns.animationState.playing = false;
    ns.animationState.paused = false;
    ns.animationState.playSeq = null;
    ns.animationState._active = false;
    ns.animationState._instance = null;
    ns.animationState.lastFrameTs = null;
    ns.animationState.savedView = null;
    ns.animationState.equilibriumXyz = null;
  }

  function loadRig() {
    const sandbox = makeSandbox({});
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    loadScript("js/visualization/scene_instance.js", sandbox);
    loadScript("js/vibration_viewer.js", sandbox);
    const ns = sandbox.window.ACPVibrationViewer;
    sandbox.window.ACPStructureViewer = {
      state: {
        displayedCoords: COORDS,
        displayedSymbols: SYMBOLS,
        displayedEntryId: "e1",
      },
    };
    return { sandbox, ns, viz: sandbox.window.ACPVisualization };
  }

  function makeStubInstance() {
    const rec = {
      play: 0, pause: 0, resume: 0, stop: 0, hidden: [],
      onTick: null, playMs: null, seq: 0,
      addArrowTokens: [], arrowFail: false, clears: 0,
    };
    const inst = {
      __rec: rec,
      play(onTick, ms) {
        rec.play += 1;
        rec.onTick = onTick;
        rec.playMs = ms;
        rec.seq += 1;
        return rec.seq;
      },
      pausePlayback() { rec.pause += 1; },
      resumePlayback() { rec.resume += 1; },
      stopPlayback() { rec.stop += 1; rec.onTick = null; },
      setHidden(v) { rec.hidden.push(!!v); },
      getView() { return { __view: rec.seq }; },
      setView() { return true; },
      removeAllModels() { return true; },
      addModel() { return { setStyle() { return true; }, addStyle() { return true; } }; },
      addStyle() { return true; },
      removeAllShapes() { rec.clears += 1; return true; },
      addArrow(spec) {
        if (rec.arrowFail) return null;
        const token = { __shapeToken: true, spec };
        rec.addArrowTokens.push(token);
        return token;
      },
      render() { return true; },
    };
    return inst;
  }

  await test("vibration: playback routes to the instance API — one interval per session", () => {
    const { ns } = loadRig();
    seedGeometry(ns);
    const inst = makeStubInstance();
    ns._viewerImpl = () => inst;

    ns.playAnimation();
    assert(ns.isAnimationActive(), "playing after playAnimation");
    assertEq(inst.__rec.play, 1, "instance play() called once");
    assertEq(inst.__rec.playMs, 33, "interval is the FRAME_MIN_MS throttle");
    assertEq(ns.animationState.playSeq, 1, "session seq recorded");

    ns.playAnimation();
    assertEq(inst.__rec.play, 1, "re-play while playing never opens a second interval");

    ns.pauseAnimation();
    assertEq(inst.__rec.pause, 1, "pause routes to pausePlayback");
    assertEq(ns.isAnimationActive(), false, "paused is not active");

    ns.togglePlay();
    assertEq(inst.__rec.resume, 1, "toggle from pause resumes via resumePlayback");
    assertEq(inst.__rec.play, 1, "resume reuses the same interval — play() not called again");
    assert(ns.isAnimationActive(), "resumed session is active");

    ns.setHidden(true);
    assertEq(inst.__rec.hidden, [true], "hide forwards to instance setHidden");
    ns.setHidden(false);
    assertEq(inst.__rec.hidden, [true, false], "unhide forwards too");

    ns.stopAnimation();
    assertEq(inst.__rec.stop, 1, "stop routes to stopPlayback");
    assertEq(ns.animationState.playSeq, null, "session seq cleared");
    assertEq(ns.isAnimationActive(), false, "stopped");
    ns.stopAnimation();
    assertEq(inst.__rec.stop, 1, "second stop is a no-op (idempotent)");
  });

  await test("vibration: hidden tick must stop — setHidden pauses ticking without losing playing state", async () => {
    const { sandbox, ns, viz } = loadRig();
    seedGeometry(ns);
    ns.arrowState.displayMode = "combo";
    const native = makeNative();
    const sc = viz.scene.create({ viewer: native });
    assert(sc !== null, "scene instance created");
    ns._viewerImpl = () => sc;

    let created = 0;
    const realSetInterval = sandbox.setInterval;
    sandbox.setInterval = function (fn, ms) {
      created += 1;
      return realSetInterval(fn, ms);
    };

    ns.playAnimation();
    assertEq(created, 1, "exactly one interval is created — never a second animation");
    assert(ns.isAnimationActive(), "playing");
    await sleep(140);
    assert(native.addModelCount() >= 1, "frames tick while visible");

    ns.setHidden(true);
    const atHide = native.addModelCount();
    await sleep(160);
    assertEq(native.addModelCount(), atHide, "hidden tick must stop");
    assert(ns.isAnimationActive(), "hidden does not lose the playing state");

    ns.setHidden(false);
    await sleep(140);
    assert(native.addModelCount() > atHide, "unhide resumes ticking while still playing");

    ns.stopAnimation();
    const atStop = native.addModelCount();
    await sleep(100);
    assertEq(native.addModelCount(), atStop, "stop halts ticking for good");
    assertEq(created, 1, "still only one interval was ever created");
    sandbox.setInterval = realSetInterval;
  });

  await test("vibration: arrows draw through instance shape tokens; count >0 and UI count consistent", () => {
    const { ns } = loadRig();
    seedGeometry(ns);
    const inst = makeStubInstance();
    ns._viewerImpl = () => inst;

    const endpoints = ns.computeArrowEndpoints(COORDS, VECTORS, 0.25);
    const count = ns.applyArrows(inst, endpoints, SYMBOLS, { color: "#4ea1ff" });
    assertEq(count, 3, "applyArrows via instance returns >0 (all rows drawn)");
    assertEq(inst.__rec.addArrowTokens.length, 3, "draws go through instance shape tokens");

    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 3, "UI arrow count matches the arrows drawn");
    assertEq(inst.__rec.addArrowTokens.length, 6, "refresh drew 3 more through the instance");

    inst.__rec.arrowFail = true; // instance explicit failure (null token)
    const failed = ns.applyArrows(inst, endpoints, SYMBOLS, {});
    assertEq(failed, 0, "explicit instance failures are not counted");
    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 0, "UI count stays consistent with failed draws");

    inst.__rec.arrowFail = false;
    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 3, "recovery restores the consistent count");
  });

  await test("vibration: D09 cold-start static arrows draw 3 from display intent — enabled is an output, never a gate", () => {
    const { ns } = loadRig();
    seedGeometry(ns); // leaves arrowState.enabled at the module default (false)
    ns.arrowState.displayMode = "arrows";
    const inst = makeStubInstance();
    ns._viewerImpl = () => inst;
    assertEq(ns.arrowState.enabled, false, "cold start: nothing drawn yet, enabled=false");
    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 3,
      "first refresh draws the 3 arrows with NO manual enabled=true (regression for the old chicken-and-egg gate)");
    assertEq(inst.__rec.addArrowTokens.length, 3, "3 arrow shapes actually created on the canvas");
    assertEq(ns.arrowState.enabled, true, "enabled flips true as an OUTPUT of the draw");
    assertEq(ns.arrowState._hint, null, "no hint when the draw succeeds");
  });

  await test("vibration: D09 display intent — animation hides arrows, switching back redraws", () => {
    const { ns } = loadRig();
    seedGeometry(ns);
    const inst = makeStubInstance();
    ns._viewerImpl = () => inst;

    ns.setDisplayMode("arrows");
    assertEq(ns.arrowState._lastCount, 3, "arrows mode draws 3");
    assertEq(inst.__rec.addArrowTokens.length, 3, "3 shapes on the canvas");
    assertEq(inst.__rec.clears, 1, "one clear+redraw cycle");

    ns.setDisplayMode("animation");
    assertEq(ns.arrowState._lastCount, 0, "animation hides the arrows");
    assertEq(inst.__rec.addArrowTokens.length, 3, "no new shapes while in animation mode");
    assertEq(inst.__rec.clears, 2, "the old arrows were cleared off the canvas");
    assertEq(ns.arrowState.enabled, false, "enabled output false while hidden");

    ns.setDisplayMode("combo");
    assertEq(ns.arrowState._lastCount, 3, "combo redraws the arrows");
    assertEq(inst.__rec.addArrowTokens.length, 6, "3 fresh shapes after the redraw");
    assertEq(ns.arrowState.enabled, true, "enabled output true again");

    ns.setDisplayMode("arrows");
    assertEq(ns.arrowState._lastCount, 3, "back to plain arrows still draws 3");
  });

  await test("vibration: D09 missing geometry shows a hint; later valid geometry recovers", () => {
    const { sandbox, ns } = loadRig();
    seedGeometry(ns);
    const inst = makeStubInstance();
    ns._viewerImpl = () => inst;

    sandbox.window.ACPStructureViewer.state.displayedEntryId = "other";
    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 0, "no arrows without matching geometry");
    assert(ns.arrowState._hint, "a hint explains the missing geometry");
    assertEq(ns.arrowState.enabled, false, "enabled output false while unavailable");
    assertEq(inst.__rec.addArrowTokens.length, 0, "nothing drawn");

    sandbox.window.ACPStructureViewer.state.displayedEntryId = "e1";
    ns.refreshArrows();
    assertEq(ns.arrowState._lastCount, 3, "recovery redraws once the geometry is valid again");
    assertEq(ns.arrowState._hint, null, "hint cleared on recovery");
    assertEq(ns.arrowState.enabled, true, "enabled output true again");
    assertEq(inst.__rec.addArrowTokens.length, 3, "3 shapes drawn after recovery");
  });

  await test("vibration: D09 two instances keep independent draw counts", () => {
    const a = loadRig();
    const b = loadRig();
    seedGeometry(a.ns);
    seedGeometry(b.ns);
    const instA = makeStubInstance();
    const instB = makeStubInstance();
    a.ns._viewerImpl = () => instA;
    b.ns._viewerImpl = () => instB;
    assert(a.ns.arrowState !== b.ns.arrowState, "per-instance arrowState, not shared");

    a.ns.refreshArrows();
    assertEq(a.ns.arrowState._lastCount, 3, "instance A draws 3");

    b.sandbox.window.ACPStructureViewer.state.displayedEntryId = "other";
    b.ns.refreshArrows();
    assertEq(b.ns.arrowState._lastCount, 0, "instance B draws 0 — its own geometry is missing");
    assertEq(a.ns.arrowState._lastCount, 3, "A's count is untouched by B's refresh");

    b.sandbox.window.ACPStructureViewer.state.displayedEntryId = "e1";
    b.ns.refreshArrows();
    assertEq(b.ns.arrowState._lastCount, 3, "B recovers independently");
    assertEq(a.ns.arrowState._lastCount, 3, "A still 3 after B's recovery");
  });

  await test("vibration: frame recipe runs on the instance — camera preserved across rebuilds", async () => {
    const { sandbox, ns, viz } = loadRig();
    seedGeometry(ns);
    ns.arrowState.displayMode = "combo";
    const native = makeNative();
    const sc = viz.scene.create({ viewer: native });
    ns._viewerImpl = () => sc;

    ns.playAnimation();
    await sleep(140);
    ns.stopAnimation();

    const names = native.__calls.map((c) => c[0]);
    const idx = (tag) => names.indexOf(tag);
    assert(idx("getView") >= 0 && idx("addModel") >= 0, "frame rebuild ran on the instance");
    assert(idx("getView") < idx("addModel"), "camera captured before the rebuild");
    assert(idx("setView") > idx("addModel"), "camera restored after the rebuild");
    assert(idx("addArrow") >= 0, "combo frame arrows drawn through the instance");
    const views = native.__calls.filter((c) => c[0] === "setView");
    assert(views.length >= 1 && views[0][1] !== null, "setView received the saved view");
  });
};
