/* Module tests for ACPVisualization.scene — bounded instance API v2
 * (plan §42.3 R5 module slice + T7; module-only, page wiring is T8). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const EXPECTED_KEYS = [
    "addArrow", "addCylinder", "addLabel", "addModel", "addSphere", "addStyle",
    "center", "clearError", "clearScene", "dispose", "frame", "getModel",
    "getModelCount", "getView", "isDisposed", "lastError", "pausePlayback",
    "play", "pngURI", "removeAllLabels", "removeAllModels", "removeAllShapes",
    "removeLabel", "removeModel", "removeShape", "render", "resize",
    "resumePlayback", "selectedAtoms", "setBackgroundColor", "setClickable",
    "setHidden", "setHoverable", "setStyle", "setView", "stopPlayback", "zoom",
    "zoomTo",
  ];

  const MODEL_TOKEN_KEYS = ["addStyle", "selectedAtoms", "setClickable", "setHoverable", "setStyle"];

  // ---- recording-stub rig: viewer + native models + callback capture ------
  function makeRig() {
    const calls = [];
    const captured = { click: null, hoverIn: null, hoverOut: null };

    function nativeModel(label) {
      // Vendor-accurate GLModel surface: NO native addStyle; the additive flag
      // is the third argument of setStyle(sel, style, add).
      return {
        __nativeModel: true,
        label: label,
        setStyle(sel, style, add) { calls.push(["model.setStyle", label, sel, style, add]); return this; },
        setClickable(sel, val, cb) { calls.push(["model.setClickable", label, sel, val]); captured.click = cb; return this; },
        setHoverable(sel, val, cbIn, cbOut) { calls.push(["model.setHoverable", label, sel, val]); captured.hoverIn = cbIn; captured.hoverOut = cbOut; return this; },
        selectedAtoms(sel) { calls.push(["model.selectedAtoms", label, sel]); return [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }]; },
      };
    }

    const viewer = {
      __calls: calls,
      __lastModel: null,
      __lastShape: null,
      __lastLabel: null,
      addModel(data, spec) { calls.push(["addModel", data, spec]); const m = nativeModel(String(data)); viewer.__lastModel = m; return m; },
      removeModel(m) { calls.push(["removeModel", m]); },
      removeAllModels() { calls.push(["removeAllModels"]); },
      setStyle(...a) { calls.push(["setStyle", ...a]); return viewer; }, // native chain -> this (A1)
      addStyle(...a) { calls.push(["addStyle", ...a]); return viewer; },
      setClickable(...a) { calls.push(["setClickable", ...a]); return viewer; },
      setHoverable(...a) { calls.push(["setHoverable", ...a]); return viewer; },
      selectedAtoms(sel) { calls.push(["selectedAtoms", sel]); return [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }]; },
      addSphere(spec) { calls.push(["addSphere", spec]); const s = { __nativeShape: true, secret: 1 }; viewer.__lastShape = s; return s; },
      addCylinder(spec) { calls.push(["addCylinder", spec]); const s = { __nativeShape: true, secret: 2 }; viewer.__lastShape = s; return s; },
      addArrow(spec) { calls.push(["addArrow", spec]); const s = { __nativeShape: true, secret: 3 }; viewer.__lastShape = s; return s; },
      removeShape(s) { calls.push(["removeShape", s]); },
      removeAllShapes() { calls.push(["removeAllShapes"]); },
      addLabel(text, opts, anchor) { calls.push(["addLabel", text, opts, anchor]); const l = { __nativeLabel: true, secret: 4 }; viewer.__lastLabel = l; return l; },
      removeLabel(l) { calls.push(["removeLabel", l]); },
      removeAllLabels() { calls.push(["removeAllLabels"]); },
      setBackgroundColor(c) { calls.push(["setBackgroundColor", c]); return viewer; },
      getView() { calls.push(["getView"]); return [1, 2, 3, 4, 5, 6, 7, 8]; },
      setView(v) { calls.push(["setView", v]); return viewer; },
      center(sel, dur) { calls.push(["center", sel, dur]); return viewer; },
      zoomTo(sel, dur) { calls.push(["zoomTo", sel, dur]); return viewer; },
      zoom(f, d) { calls.push(["zoom", f, d]); return viewer; },
      render() { calls.push(["render"]); },
      resize() { calls.push(["resize"]); },
      clear() { calls.push(["clear"]); },
      pngURI() { calls.push(["pngURI"]); return "data:image/png;base64,stub"; },
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

  function callsNamed(calls, name) { return calls.filter((c) => c[0] === name); }

  // ------------------------------------------------------------------------
  await test("scene: public key set is exactly the whitelist; no raw viewer leakage", () => {
    const viz = loadScene();
    assertEq(Object.keys(viz.scene).sort(), ["create"]);
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    assertEq(Object.keys(sc).sort(), EXPECTED_KEYS, "instance key set drifted");
    for (const k of Object.keys(sc)) {
      assert(typeof sc[k] === "function", "key " + k + " is a function");
      assert(sc[k] !== rig.viewer, "no instance property is the raw viewer");
    }
    assertEq(viz.scene.create({}), null, "no container and no viewer -> null");
    assertEq(sc.isDisposed(), false);
  });

  await test("scene: create builds and holds the viewer via mol3d.create (no external viewer)", () => {
    const sandbox = makeSandbox({});
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    const rig = makeRig();
    const created = [];
    sandbox.window.ACPVisualization.mol3d.create = function (container, options) {
      created.push([container, options]);
      return rig.viewer;
    };
    loadScript("js/visualization/scene_instance.js", sandbox);
    const container = { id: "viewer-3d" };
    const sc = sandbox.window.ACPVisualization.scene.create({ container, options: { backgroundColor: "0x0e1419" } });
    assert(sc !== null, "instance created from container+options");
    assertEq(created.length, 1, "mol3d.create called exactly once");
    assert(created[0][0] === container, "container forwarded");
    assertEq(created[0][1], { backgroundColor: "0x0e1419" }, "options forwarded");
    sc.addModel("C 0 0 0", "xyz");
    assertEq(callsNamed(rig.calls, "addModel"), [["addModel", "C 0 0 0", "xyz"]], "internal viewer receives addModel");
  });

  await test("scene: library-missing / no canvas degrades to null (no throw)", () => {
    const bare = makeSandbox({});
    loadScript("js/visualization/scene_instance.js", bare);
    assertEq(bare.window.ACPVisualization.scene.create({ container: {} }), null, "missing mol3d namespace -> null");
    assertEq(bare.window.ACPVisualization.scene.create({}), null, "no container -> null");
    const rig = makeRig();
    const bareLib = makeSandbox({});
    loadScript("js/visualization/framing.js", bareLib);
    loadScript("js/visualization/mol3d_adapter.js", bareLib);
    bareLib.window.ACPVisualization.mol3d.create = function () { return null; };
    loadScript("js/visualization/scene_instance.js", bareLib);
    assertEq(bareLib.window.ACPVisualization.scene.create({ container: {} }), null, "mol3d.create returning null -> null");
    assertEq(rig.calls.length, 0);
  });

  // ---- model tokens ------------------------------------------------------
  await test("scene: addModel returns an opaque model token — no native escape", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const token = sc.addModel("A", "xyz");
    const native = rig.viewer.__lastModel;
    assert(token && typeof token === "object", "token is an object");
    assert(token !== native, "token is not the native model");
    assertEq(Object.keys(token).sort(), MODEL_TOKEN_KEYS, "model token key set");
    assert(!("__nativeModel" in token), "native marker not leaked on token");
    assert(!("native" in token), "no native property on token");
    assert(typeof native.selectedAtoms === "function", "(sanity) native model has methods");
    assertEq(token.setStyle({}, { stick: {} }), true, "token mutator returns boolean");
    assertEq(callsNamed(rig.calls, "model.setStyle"), [["model.setStyle", "A", {}, { stick: {} }, null]]);
  });

  await test("scene: instance-level same-named methods act on the current model", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    sc.addModel("A", "xyz");
    assertEq(sc.setStyle({}, { s: 1 }), true);
    assertEq(sc.addStyle({}, { a: 1 }), true);
    assertEq(sc.setClickable({}, true, () => {}), true);
    assertEq(sc.setHoverable({}, true, () => {}, () => {}), true);
    assertEq(sc.selectedAtoms({}), [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }]);
    const names = rig.calls.map((c) => c[0]);
    assert(names.includes("model.setStyle"), "setStyle routed to the model");
    const additive = rig.calls.find((c) => c[0] === "model.setStyle" && c[4] === true);
    assertEq(additive, ["model.setStyle", "A", {}, { a: 1 }, true],
      "addStyle routed to model.setStyle(sel, style, true) — vendor GLModel has no addStyle");
    assert(names.includes("model.setClickable"), "setClickable routed to the model");
    assert(names.includes("model.setHoverable"), "setHoverable routed to the model");
    assert(!names.includes("setStyle"), "viewer.setStyle not used");
    assert(!names.includes("addStyle"), "viewer.addStyle not used");
    assert(!names.includes("model.addStyle"), "model.addStyle never assumed (not in vendor)");
  });

  await test("scene: removeModel(handle?) removes current/specified model; token dies", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const t1 = sc.addModel("A", "xyz");
    const native1 = rig.viewer.__lastModel;
    assertEq(sc.getModelCount(), 1);
    assertEq(sc.removeModel(t1), true, "remove specified model");
    assertEq(sc.getModelCount(), 0);
    assertEq(callsNamed(rig.calls, "removeModel"), [["removeModel", native1]], "native removeModel called with native model");
    assertEq(t1.selectedAtoms({}), null, "removed token: selectedAtoms -> null");
    assertEq(t1.setStyle({}, {}), false, "removed token: setStyle -> false");
    assertEq(sc.removeModel(t1), false, "already-removed handle rejected");

    const t2 = sc.addModel("B", "xyz");
    assertEq(sc.removeModel(), true, "removeModel() removes the current model");
    assertEq(sc.getModelCount(), 0);
    // parse-cleanup recipe: add temporary model, read atoms, remove it
    const t3 = sc.addModel("temp", "xyz");
    assertEq(t3.selectedAtoms({}).length, 1);
    assertEq(sc.removeModel(t3), true);
    assertEq(sc.getModelCount(), 0);
  });

  await test("scene: getModelCount/getModel read the instance registry — never forwarded to vendor", () => {
    const viz = loadScene();
    const rig = makeRig();
    delete rig.viewer.getModelCount; // vendor 3Dmol has none; must still work
    const sc = viz.scene.create({ viewer: rig.viewer });
    assertEq(sc.getModelCount(), 0);
    const a = sc.addModel("A", "xyz");
    const b = sc.addModel("B", "xyz");
    assertEq(sc.getModelCount(), 2);
    const gotA = sc.getModel(0);
    const gotB = sc.getModel(1);
    assert(gotA !== null && gotB !== null && gotA !== gotB, "distinct restricted tokens");
    assert(!("__nativeModel" in gotA), "getModel returns a restricted token");
    assertEq(sc.removeModel(gotA), true, "getModel token removes that model");
    assertEq(sc.getModelCount(), 1);
    assertEq(sc.getModel(5), null, "out-of-range index -> null");
    assertEq(sc.getModel(-1), null, "negative index -> null");
  });

  await test("scene: overlay models are independent — B's style never touches A", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const a = sc.addModel("A", "xyz");
    const b = sc.addModel("B", "xyz");
    a.setStyle({}, { aStyle: true });
    b.setStyle({}, { bStyle: true });
    assertEq(callsNamed(rig.calls, "model.setStyle"), [
      ["model.setStyle", "A", {}, { aStyle: true }, null],
      ["model.setStyle", "B", {}, { bStyle: true }, null],
    ], "each token styles only its own native model");
  });

  // ---- A1 boundary substitutes -------------------------------------------
  await test("scene: A1 — native chained returns (native `this`) never pass through", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const m = sc.addModel("A", "xyz");
    assertEq(sc.setStyle({}, {}), true, "instance setStyle -> boolean, not viewer");
    assertEq(sc.addStyle({}, {}), true, "instance addStyle -> boolean, not viewer");
    assertEq(sc.setBackgroundColor("0x000"), true, "instance setBackgroundColor -> boolean");
    assertEq(sc.setView([1, 2, 3, 4, 5, 6, 7, 8]), true, "instance setView -> boolean");
    assertEq(m.setStyle({}, {}), true, "token setStyle -> boolean, not native model");
    assert(sc.setStyle({}, {}) !== rig.viewer, "no native viewer leaked");
    assert(m.setStyle({}, {}) !== rig.viewer.__lastModel, "no native model leaked");
  });

  await test("scene: A1 — native shape return becomes an opaque token", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const st = sc.addSphere({ center: {} });
    const nativeShape = rig.viewer.__lastShape;
    assert(st !== nativeShape, "shape token is not the native shape");
    assertEq(Object.keys(st), [], "shape token is opaque (no properties)");
    assert(!("__nativeShape" in st), "native shape marker not leaked");
    assertEq(sc.removeShape(st), true, "shape token removes its native shape");
    assertEq(callsNamed(rig.calls, "removeShape"), [["removeShape", nativeShape]], "native removeShape got the native shape");
    assertEq(sc.removeShape(st), false, "already-removed shape handle rejected");
  });

  // ---- shapes / labels ----------------------------------------------------
  await test("scene: shape tokens for sphere/cylinder/arrow + removeAllShapes", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const sph = sc.addSphere({});
    const cyl = sc.addCylinder({});
    const arr = sc.addArrow({});
    assert(sph !== null && cyl !== null && arr !== null, "all shape adds return tokens");
    assertEq(Object.keys(sph), []);
    assertEq(sc.removeShape(sph), true);
    assertEq(sc.removeShape(cyl), true);
    assertEq(sc.removeShape(arr), true);
    assertEq(callsNamed(rig.calls, "removeShape").length, 3);
    sc.addSphere({});
    sc.addCylinder({});
    assertEq(sc.removeAllShapes(), true);
    assertEq(callsNamed(rig.calls, "removeAllShapes").length, 1);
  });

  await test("scene: label tokens; anchor {serial} injects the internal model; no native model escapes", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    sc.addModel("A", "xyz");
    const nativeModel = rig.viewer.__lastModel;
    const lt = sc.addLabel("1", { fontSize: 10 }, { serial: 3 });
    const nativeLabel = rig.viewer.__lastLabel;
    assert(lt !== nativeLabel, "label token is not the native label");
    assertEq(Object.keys(lt), []);
    assert(!("__nativeLabel" in lt), "native label marker not leaked");
    const addCalls = callsNamed(rig.calls, "addLabel");
    assertEq(addCalls[0][1], "1");
    assertEq(addCalls[0][2], { fontSize: 10 });
    assert(addCalls[0][3].serial === 3, "serial anchor preserved");
    assert(addCalls[0][3].model === nativeModel, "instance injected the internal native model");

    // a business-supplied native model reference is stripped and replaced
    const fake = { __nativeModel: true };
    sc.addLabel("2", {}, { serial: 4, model: fake });
    const addCalls2 = callsNamed(rig.calls, "addLabel");
    assert(addCalls2[1][3].model === nativeModel, "business native model reference replaced");
    assert(addCalls2[1][3].model !== fake, "business native model reference not forwarded");

    assertEq(sc.removeLabel(lt), true, "label token removes its native label");
    assertEq(callsNamed(rig.calls, "removeLabel"), [["removeLabel", nativeLabel]]);
    assertEq(sc.removeLabel(lt), false, "already-removed label handle rejected");
    sc.addLabel("3", {});
    assertEq(sc.removeAllLabels(), true);
    assertEq(callsNamed(rig.calls, "removeAllLabels").length, 1);
  });

  // ---- selectedAtoms snapshot --------------------------------------------
  await test("scene: selectedAtoms returns a whitelisted independent snapshot", () => {
    const viz = loadScene();
    const rig = makeRig();
    rig.viewer.addModel = function () {
      return {
        selectedAtoms() {
          return [{ serial: 5, index: 2, elem: "H", x: 1, y: 2, z: 3, extra: "nope", bonds: [1, 2] }];
        },
      };
    };
    const sc = viz.scene.create({ viewer: rig.viewer });
    const t = sc.addModel("x", "xyz");
    const atoms = t.selectedAtoms({});
    assertEq(atoms, [{ serial: 5, index: 2, elem: "H", x: 1, y: 2, z: 3 }], "whitelist fields only");
    atoms[0].x = 999;
    atoms[0].serial = 42;
    const atoms2 = t.selectedAtoms({});
    assertEq(atoms2[0].x, 1, "mutating the snapshot does not change internals");
    assertEq(atoms2[0].serial, 5, "serial unchanged");
    // failure paths
    assertEq(sc.selectedAtoms({}) !== null, true, "instance-level selectedAtoms works");
  });

  // ---- click / hover callback conversion ---------------------------------
  await test("scene: click/hover callbacks receive data snapshot + restricted instance + whitelisted event", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const t = sc.addModel("A", "xyz");
    let got = null;
    assertEq(t.setClickable({}, true, (atom, viewerSlot, event) => { got = { atom, viewerSlot, event }; }), true);
    const nativeAtom = { serial: 1, index: 0, elem: "C", x: 1.5, y: 2, z: 3, __native: true, bonds: [] };
    const nativeViewer = { __nativeViewer: true };
    rig.captured.click(nativeAtom, nativeViewer, { x: 10, y: 20, button: 0, target: { tagName: "DIV" } });
    assert(got !== null, "business callback fired");
    assert(got.atom !== nativeAtom, "atom is a snapshot, not the native atom");
    assertEq(got.atom, { serial: 1, index: 0, elem: "C", x: 1.5, y: 2, z: 3 }, "atom whitelist");
    assert(got.viewerSlot === sc, "viewer slot is the restricted instance");
    assert(got.viewerSlot !== nativeViewer, "native viewer never passed");
    assert(got.event !== undefined, "event forwarded");
    assertEq(got.event, { button: 0, x: 10, y: 20 }, "event whitelist (no DOM target)");

    // hover pair
    let h = null;
    t.setHoverable({}, true, (a, v) => { h = { a, v }; }, () => {});
    rig.captured.hoverIn({ index: 7, elem: "N", x: 0, y: 0, z: 0 }, nativeViewer, undefined);
    assertEq(h.a, { index: 7, elem: "N", x: 0, y: 0, z: 0 }, "hover atom snapshot");
    assert(h.v === sc, "hover viewer slot restricted");
  });

  await test("scene: stale callbacks die on scene/model generation change", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    // scene-level change: removeAllModels (task switch) invalidates all
    const a = sc.addModel("A", "xyz");
    let hitsA = 0;
    a.setClickable({}, true, () => { hitsA++; });
    const cbA = rig.captured.click;
    sc.removeAllModels(); // scene/task generation bump
    sc.addModel("B", "xyz");
    cbA({ index: 0 }, {}, {});
    assertEq(hitsA, 0, "old scene callback cannot modify new task state");

    // model-level change: removing one model kills only its own callbacks
    const m1 = sc.addModel("M1", "xyz");
    let n1 = 0;
    m1.setClickable({}, true, () => { n1++; });
    const cb1 = rig.captured.click;
    const m2 = sc.addModel("M2", "xyz");
    let n2 = 0;
    m2.setClickable({}, true, () => { n2++; });
    const cb2 = rig.captured.click;
    sc.removeModel(m1);
    cb1({ index: 0 }, {}, {});
    assertEq(n1, 0, "removed model callback dead");
    cb2({ index: 0 }, {}, {});
    assertEq(n2, 1, "sibling model callback still live");
  });

  await test("scene: cross-instance tokens are rejected", () => {
    const viz = loadScene();
    const rigA = makeRig();
    const rigB = makeRig();
    const a = viz.scene.create({ viewer: rigA.viewer });
    const b = viz.scene.create({ viewer: rigB.viewer });
    const ta = a.addModel("A", "xyz");
    const tb = b.addModel("B", "xyz");
    assertEq(a.removeModel(tb), false, "foreign model token rejected by A");
    assertEq(b.removeModel(tb), true, "own model token accepted by B");
    assertEq(a.getModelCount(), 1);
    const sa = a.addSphere({});
    assertEq(b.removeShape(sa), false, "foreign shape token rejected");
    assertEq(a.removeShape(sa), true);
    const la = a.addLabel("x", {});
    assertEq(b.removeLabel(la), false, "foreign label token rejected");
    assertEq(a.removeLabel(la), true);
  });

  // ---- A2 registry --------------------------------------------------------
  await test("scene: A2 registry — loop add/remove incl. overlay returns to baseline, no residue", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const baseline = sc.getModelCount();
    for (let i = 0; i < 5; i++) {
      const t = sc.addModel("m" + i, "xyz");
      sc.removeModel(t);
    }
    assertEq(sc.getModelCount(), baseline, "add/remove loop leaves no residue");
    const a = sc.addModel("A", "xyz");
    const b = sc.addModel("B", "xyz");
    assertEq(sc.getModelCount(), baseline + 2, "overlay adds counted");
    assertEq(sc.removeAllModels(), true);
    assertEq(sc.getModelCount(), baseline, "removeAllModels returns to baseline");
    assertEq(sc.getModel(0), null, "no stale token left in registry");
    assertEq(sc.clearScene(), true);
    assertEq(sc.getModelCount(), baseline);
  });

  // ---- camera / render / error contract ----------------------------------
  await test("scene: getView returns an independent copy; center/zoom forward", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    const v = sc.getView();
    assertEq(v, [1, 2, 3, 4, 5, 6, 7, 8]);
    v.push(9);
    const v2 = sc.getView();
    assertEq(v2.length, 8, "getView returned an independent copy");
    assertEq(sc.center({}, 0), true);
    assertEq(sc.zoom(1.5, 0), true);
    assertEq(callsNamed(rig.calls, "center"), [["center", {}, 0]]);
    assertEq(callsNamed(rig.calls, "zoom"), [["zoom", 1.5, 0]]);
  });

  await test("scene: error contract — explicit failures; same-method success clears; render does not", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    assertEq(sc.lastError(), null, "no error initially");

    const noAdd = makeRig();
    delete noAdd.viewer.addModel;
    const broken = viz.scene.create({ viewer: noAdd.viewer });
    assertEq(broken.addModel("x", "xyz"), null, "unavailable native -> null");
    assert(broken.lastError() && broken.lastError().method === "addModel", "failure recorded");
    assertEq(broken.render(), true, "render succeeds");
    assert(broken.lastError() && broken.lastError().method === "addModel",
      "render success does NOT clear parse-class failure");
    noAdd.viewer.addModel = function () { return { m: 1 }; };
    assert(broken.addModel("y", "xyz") !== null, "same method later succeeds");
    assertEq(broken.lastError(), null, "same-method success clears its record");

    // explicit clear + disposed failure
    const rig2 = makeRig();
    const sc2 = viz.scene.create({ viewer: rig2.viewer });
    delete rig2.viewer.render;
    assertEq(sc2.render(), false, "missing native method -> false");
    assert(sc2.lastError() !== null, "failure recorded");
    assertEq(sc2.clearError(), true);
    assertEq(sc2.lastError(), null, "clearError resets");

    const sc3 = viz.scene.create({ viewer: makeRig().viewer });
    sc3.dispose();
    assertEq(sc3.render(), false, "disposed -> explicit false");
    assertEq(sc3.lastError().method, "render");
    assertEq(sc3.addModel("x", "xyz"), null, "disposed -> explicit null");
    assertEq(sc3.selectedAtoms({}), null, "disposed -> explicit null");
  });

  // ---- purity -------------------------------------------------------------
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

  // ---- preserved lifecycle behaviour --------------------------------------
  await test("scene: clearScene clears models + shapes + labels and resets registry", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    sc.addModel("A", "xyz");
    sc.clearScene();
    assertEq(callsNamed(rig.calls, "removeAllModels").length, 1);
    assertEq(callsNamed(rig.calls, "removeAllShapes").length, 1);
    assertEq(callsNamed(rig.calls, "removeAllLabels").length, 1);
    assertEq(sc.getModelCount(), 0, "registry cleared with the scene");
  });

  await test("scene: play/stop/pause/resume + hidden-pause, one interval per instance", async () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });

    let ticks = 0;
    const id = sc.play(() => { ticks++; }, 5);
    assert(id !== null && id !== undefined, "play returns an id");
    await sleep(24);
    assert(ticks >= 2, "onTick fired repeatedly");
    sc.stopPlayback();
    const stoppedAt = ticks;
    await sleep(20);
    assertEq(ticks, stoppedAt, "stopPlayback halts ticking");

    let a = 0, b = 0;
    sc.play(() => { a++; }, 5);
    await sleep(18);
    const aAtRestart = a;
    sc.play(() => { b++; }, 5);
    await sleep(20);
    assertEq(a, aAtRestart, "restart cancels the previous interval (no double firing)");
    assert(b >= 1, "restarted interval ticks");
    sc.stopPlayback();

    let n = 0;
    sc.play(() => { n++; }, 5);
    sc.pausePlayback();
    const nPaused = n;
    await sleep(20);
    assertEq(n, nPaused, "paused instance does not tick");
    sc.resumePlayback();
    await sleep(20);
    assert(n > nPaused, "resumePlayback resumes ticking");

    sc.setHidden(true);
    const nHidden = n;
    await sleep(20);
    assertEq(n, nHidden, "hidden pauses ticking");
    sc.setHidden(false);
    await sleep(20);
    assert(n > nHidden, "unhide resumes while still playing");
    sc.stopPlayback();

    const nStopped = n;
    sc.setHidden(true);
    await sleep(15);
    sc.setHidden(false);
    await sleep(15);
    assertEq(n, nStopped, "setHidden does not start a stopped instance");
  });

  await test("scene: dispose idempotent; no late onTick/native calls; destroyImpl+onDispose once", async () => {
    const viz = loadScene();
    const rig = makeRig();
    const destroyed = [];
    const disposed = [];
    const sc = viz.scene.create({
      viewer: rig.viewer,
      destroyImpl: (v) => destroyed.push(v),
      onDispose: () => disposed.push(1),
    });
    sc.addModel("A", "xyz");
    let ticks = 0;
    sc.play(() => { ticks++; }, 5);
    await sleep(16);
    assert(ticks >= 1, "ticking before dispose");
    sc.dispose();
    sc.dispose(); // idempotent
    const ticksAtDispose = ticks;
    const callsAtDispose = rig.viewer.__calls.length;
    await sleep(30);
    assertEq(ticks, ticksAtDispose, "no onTick fires after dispose");
    sc.render();
    sc.addModel("x", "xyz");
    sc.pngURI();
    sc.frame();
    await sleep(15);
    assertEq(rig.viewer.__calls.length, callsAtDispose, "no native calls after dispose");
    assertEq(destroyed, [rig.viewer], "destroyImpl called exactly once with the viewer");
    assertEq(disposed, [1], "onDispose called exactly once");
    assertEq(sc.isDisposed(), true);
  });

  await test("scene: default destroy routes through mol3d.destroy; stub-tolerant", () => {
    const viz = loadScene();
    const rig = makeRig();
    const sc = viz.scene.create({ viewer: rig.viewer });
    sc.dispose();
    sc.dispose();
    const names = rig.viewer.__calls.map((c) => c[0]);
    assertEq(names.filter((x) => x === "removeAllModels").length, 1, "clearScene models once");
    assertEq(names.filter((x) => x === "removeAllShapes").length, 1, "clearScene shapes once");
    assertEq(names.filter((x) => x === "removeAllLabels").length, 1, "clearScene labels once");
    assertEq(names.filter((x) => x === "clear").length, 1, "mol3d destroy ran exactly once");
    const bare = { render() {} };
    const sc2 = viz.scene.create({ viewer: bare });
    sc2.dispose();
    assertEq(sc2.isDisposed(), true);
  });

  await test("scene: two instances are independent; disposing A leaves B working", async () => {
    const viz = loadScene();
    const stubA = makeRig();
    const stubB = makeRig();
    const a = viz.scene.create({ viewer: stubA.viewer });
    const b = viz.scene.create({ viewer: stubB.viewer });
    let bTicks = 0;
    b.play(() => { bTicks++; }, 5);
    a.dispose();
    const aCallsAtDispose = stubA.viewer.__calls.length;
    b.render();
    b.addModel("B", "xyz");
    await sleep(20);
    assert(bTicks >= 2, "B still ticks after A disposed");
    assert(stubB.viewer.__calls.length >= 2, "B still renders after A disposed");
    assertEq(stubA.viewer.__calls.length, aCallsAtDispose, "A stays quiet; B unaffected");
    assertEq(b.isDisposed(), false);
    assertEq(a.isDisposed(), true);
    b.dispose();
  });

  await test("scene: repeated mount/destroy — disposed instance never ticks again; remount is clean", async () => {
    const viz = loadScene();
    const rigA = makeRig();
    const a = viz.scene.create({ viewer: rigA.viewer });
    let aTicks = 0;
    a.play(() => { aTicks++; }, 5);
    await sleep(14);
    assert(aTicks >= 1, "mount A ticks");
    a.dispose();
    const aAtDispose = aTicks;

    const rigB = makeRig();
    const b = viz.scene.create({ viewer: rigB.viewer });
    let bTicks = 0;
    b.play(() => { bTicks++; }, 5);
    await sleep(25);
    assertEq(aTicks, aAtDispose, "old mount's callback never ticks after dispose");
    assert(bTicks >= 1, "remounted instance ticks");

    b.dispose();
    const bAtDispose = bTicks;
    await sleep(20);
    assertEq(bTicks, bAtDispose, "second destroy silences the remounted instance too");
    assertEq(rigA.viewer.__calls.length > 0, true, "mount A actually touched its stub");
    assertEq(a.isDisposed(), true);
    assertEq(b.isDisposed(), true);
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
};
