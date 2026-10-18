/* T11: reaction-builder's reaction preview viewer is a bounded
 * ACPVisualization.scene instance (never a raw 3Dmol handle); the hover halo
 * and the cylinder hover/click overlays run through the restricted API.
 * Stack under test: framing.js + mol3d_adapter.js + scene_instance.js +
 * workbench/features/reaction-builder.js against a recording $3Dmol stub.
 * Failure case: hidden/destroyed viewer -> stale callbacks must NOT tick —
 * the stub call log (hidden/wizard-close), instance.lastError (dispose) and
 * the reaction-panel render counter (model click) each catch leaks.
 */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const fs = require("fs");
  const path = require("path");
  const vm = require("vm");

  const XYZ = "2\ncomment\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\n";

  function makeNativeViewer(calls, id) {
    const model = {
      __id: id + "/model",
      setStyle: (sel, style, add) => calls.push(["model.setStyle", sel, style, add]),
      setClickable: (sel, val, cb) => { calls.push(["model.setClickable", sel, val]); model.__click = cb; },
      setHoverable: (sel, val, cbIn, cbOut) => {
        calls.push(["model.setHoverable", sel, val]);
        model.__hoverIn = cbIn;
        model.__hoverOut = cbOut;
      },
      selectedAtoms: (sel) => { calls.push(["model.selectedAtoms", sel]); return []; },
    };
    const viewer = {
      __id: id,
      __model: model,
      clear: () => calls.push(["clear", id]),
      addModel: (data, spec) => { calls.push(["addModel", data, spec]); return model; },
      removeAllModels: () => calls.push(["removeAllModels"]),
      setStyle: (...a) => { calls.push(["viewer.setStyle", ...a]); return viewer; },
      addSphere: (spec) => { calls.push(["addSphere", spec]); return { __shape: "s" + calls.length }; },
      addCylinder: (spec) => { calls.push(["addCylinder", spec]); return { __shape: "c" + calls.length }; },
      addArrow: (spec) => { calls.push(["addArrow", spec]); return { __shape: "a" + calls.length }; },
      addLabel: (text, opts) => { calls.push(["addLabel", text, opts]); return { __label: "l" + calls.length }; },
      removeLabel: (handle) => calls.push(["removeLabel", handle]),
      removeShape: (handle) => calls.push(["removeShape", handle]),
      removeAllShapes: () => calls.push(["removeAllShapes"]),
      removeAllLabels: () => calls.push(["removeAllLabels"]),
      render: () => calls.push(["render"]),
      resize: () => calls.push(["resize"]),
      center: (...a) => calls.push(["center", ...a]),
      zoomTo: (...a) => calls.push(["zoomTo", ...a]),
      zoom: (...a) => calls.push(["zoom", ...a]),
      getView: () => [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 5],
      setView: (v) => calls.push(["setView", v]),
    };
    return viewer;
  }

  function makeEl(id) {
    return {
      id,
      style: {},
      innerHTML: "",
      textContent: "",
      className: "",
      isConnected: true,
      clientWidth: 0,
      clientHeight: 0,
      disabled: false,
      dataset: {},
      classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      getAttribute: () => null,
      setAttribute() {},
      appendChild() {},
    };
  }

  function loadReaction(opts) {
    opts = opts || {};
    const created = [];
    const calls = [];
    const elById = {};
    function el(id) {
      if (!elById[id]) elById[id] = makeEl(id);
      return elById[id];
    }
    const sandbox = makeSandbox({
      requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
      document: {
        getElementById: (id) => el(id),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
      },
      t: (k) => k,
      escapeHtml: (s) => String(s),
    });
    if (opts.with3Dmol !== false) {
      sandbox.$3Dmol = {
        createViewer: (container, options) => {
          const v = makeNativeViewer(calls, "v" + created.length);
          created.push({ container, options, viewer: v });
          return v;
        },
      };
    }
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    loadScript("js/visualization/scene_instance.js", sandbox);
    loadScript("js/workbench/features/reaction-builder.js", sandbox);
    sandbox.scheduleViewerFraming = sandbox.ACPVisualization.framing.scheduleViewerFraming;
    sandbox.frame3DViewer = sandbox.ACPVisualization.framing.frame3DViewer;
    sandbox.disposeViewerFraming = sandbox.ACPVisualization.framing.disposeViewerFraming;

    function setPreviewState(extra) {
      const preview = Object.assign({
        status: "ok",
        mapping_status: "unique",
        manual_mode: false,
        bond_changes: [{
          reactant_atoms: [0, 1],
          product_atoms: [0, 1],
          change_type: "form",
          confidence: 0.5,
          distance_before: 1.0,
          distance_after: 1.2,
          bond_order_before: 1,
          bond_order_after: 2,
        }],
        unmatched_reactant_atoms: [],
        unmatched_product_atoms: [],
      }, (extra && extra.preview) || {});
      vm.runInContext(
        "reactionDefinition = " + JSON.stringify({
          reactant: { xyz: XYZ },
          product: { xyz: XYZ },
          tsGuess: null,
          locked: false,
          lockData: null,
          previewData: preview,
          configHash: "",
          draftCreated: false,
          selectedCandidate: null,
          previewHash: "",
          showCandidatePicker: false,
          lockedPayload: null,
          editMode: true,
          manualEdits: (extra && extra.manualEdits) || [],
          allowZeroChanges: false,
        }) + ";",
        sandbox
      );
    }

    return {
      sandbox,
      created,
      calls,
      el,
      container: el("mech-reaction-3d"),
      bodyEl: el("mech-reaction-body"),
      setPreviewState,
      evalJs: (code) => vm.runInContext(code, sandbox),
      callNames: () => calls.map((c) => c[0]),
      cylinderSpecs: () => calls.filter((c) => c[0] === "addCylinder").map((c) => c[1]),
    };
  }

  function renderWith(env, extra) {
    env.setPreviewState(extra);
    env.sandbox.renderReactionChanges3D();
  }

  function capturedCallbacks(env) {
    const out = [];
    const model = env.created[0].viewer.__model;
    if (model.__click) out.push(["model.click", model.__click]);
    if (model.__hoverIn) out.push(["model.hoverIn", model.__hoverIn]);
    if (model.__hoverOut) out.push(["model.hoverOut", model.__hoverOut]);
    for (const spec of env.cylinderSpecs()) {
      if (spec.hover_callback) out.push(["shape.hover", spec.hover_callback]);
      if (spec.unhover_callback) out.push(["shape.unhover", spec.unhover_callback]);
      if (spec.callback) out.push(["shape.click", spec.callback]);
    }
    return out;
  }

  function invokeAll(entries, env) {
    const atom = { index: 0, elem: "H", x: 0, y: 0, z: 0, serial: 1 };
    for (const [name, fn] of entries) {
      if (name === "model.click") fn(atom, null, { type: "click" });
      else if (name === "model.hoverIn") fn(atom, null, { type: "hover" });
      else fn();
    }
  }

  await test("reaction viewer is a scene instance: exact creation options; click/hover bound on the model token AFTER addModel (T12 lesson)", () => {
    const env = loadReaction({});
    renderWith(env);
    assertEq(env.created.length, 1, "exactly one viewer creation");
    assert(env.created[0].container === env.container, "creation container is mech-reaction-3d");
    assertEq(env.created[0].options, { backgroundColor: "0x0e1419", antialias: true });
    const inst = env.evalJs("reactionViewer");
    assert(inst && typeof inst.dispose === "function", "reactionViewer holds a scene instance");
    assert(!("viewer" in inst), "the instance never exposes a raw viewer handle");
    assert(typeof env.evalJs("reactionModel").setClickable === "function", "reactionModel is an opaque model token");
    const names = env.callNames();
    const addModelAt = names.indexOf("addModel");
    const clickAt = names.indexOf("model.setClickable");
    const hoverAt = names.indexOf("model.setHoverable");
    assert(addModelAt >= 0, "addModel recorded");
    assert(clickAt > addModelAt, "setClickable binds on the model token AFTER addModel");
    assert(hoverAt > addModelAt, "setHoverable binds on the model token AFTER addModel");
    assertEq(names.indexOf("viewer.setClickable"), -1, "no viewer-level setClickable (silent no-op in 3Dmol-2.5.5)");
    assertEq(names.indexOf("viewer.setHoverable"), -1, "no viewer-level setHoverable");
  });

  await test("live overlay callbacks tick: cylinder hover/unhover labels + model hover halo", () => {
    const env = loadReaction({});
    renderWith(env);
    const model = env.created[0].viewer.__model;
    const before = env.calls.length;
    model.__hoverIn({ index: 0, elem: "H", x: 0, y: 0, z: 0, serial: 1 }, null, {});
    let names = env.callNames().slice(before);
    assert(names.indexOf("addSphere") >= 0, "hover start draws the halo sphere");
    assert(names.indexOf("render") >= 0, "hover start renders");
    model.__hoverOut(null, null, {});
    names = env.callNames().slice(env.calls.length - 4);
    assert(names.indexOf("removeShape") >= 0, "hover end removes the halo shape");
    const spec = env.cylinderSpecs()[0];
    const t0 = env.calls.length;
    spec.hover_callback();
    names = env.callNames().slice(t0);
    assert(names.indexOf("addLabel") >= 0, "cylinder hover shows the tooltip label");
    const t1 = env.calls.length;
    spec.unhover_callback();
    names = env.callNames().slice(t1);
    assert(names.indexOf("removeLabel") >= 0, "cylinder unhover removes the tooltip label");
  });

  await test("framing goes through scheduleViewerFraming on the instance", async () => {
    const env = loadReaction({});
    env.container.clientWidth = 400;
    env.container.clientHeight = 300;
    renderWith(env);
    await new Promise((resolve) => setTimeout(resolve, 30));
    const names = env.callNames();
    for (const m of ["resize", "center", "zoomTo", "render"]) {
      assert(names.indexOf(m) >= 0, "framing pass calls " + m + " via the instance");
    }
  });

  await test("failure: dispose kills stale callbacks — they must not tick", () => {
    const env = loadReaction({});
    renderWith(env);
    const inst = env.evalJs("reactionViewer");
    const stale = capturedCallbacks(env);
    assert(stale.length >= 5, "captured model + shape callbacks");
    env.evalJs("reactionViewer.dispose()");
    assert(inst.isDisposed(), "instance disposed");
    const callsAtDispose = env.calls.length;
    const panel = env.el("mech-reaction-body");
    panel.innerHTML = "";
    const panelBefore = panel.innerHTML;
    invokeAll(stale, env);
    assertEq(env.calls.length, callsAtDispose, "no native call after dispose (stale callbacks must not tick)");
    assertEq(inst.lastError(), null, "not even attempted: disposed instance records no method call");
    assertEq(panel.innerHTML, panelBefore, "model click callback must not re-render the reaction panel after dispose");
  });

  await test("failure: hidden (clearReactionViewerContents) kills stale render callbacks — they must not tick", () => {
    const env = loadReaction({});
    renderWith(env);
    const stale = capturedCallbacks(env);
    env.evalJs("clearReactionViewerContents()");
    assertEq(env.evalJs("reactionModel"), null, "hidden path drops the model token");
    const callsAtClear = env.calls.length;
    invokeAll(stale, env);
    const fresh = env.callNames().slice(callsAtClear);
    for (const m of ["addSphere", "addLabel", "removeLabel", "removeShape"]) {
      assertEq(fresh.indexOf(m), -1, "stale callback ticked after clear: " + m);
    }
    assertEq(env.evalJs("reactionViewer.lastError()"), null, "not even attempted: cleared scene records no method call");
  });

  await test("failure: wizard-close removeAll* kills stale render callbacks — they must not tick", () => {
    const env = loadReaction({});
    renderWith(env);
    const stale = capturedCallbacks(env);
    env.evalJs("reactionViewer.removeAllModels(); reactionViewer.removeAllShapes(); reactionViewer.removeAllLabels(); reactionViewer.render();");
    const callsAtClose = env.calls.length;
    invokeAll(stale, env);
    const fresh = env.callNames().slice(callsAtClose);
    for (const m of ["addSphere", "addLabel", "removeLabel", "removeShape"]) {
      assertEq(fresh.indexOf(m), -1, "stale callback ticked after wizard close: " + m);
    }
    assertEq(env.evalJs("reactionViewer.lastError()"), null, "not even attempted: emptied scene records no method call");
  });

  await test("manual-edit overlay: cylinder click cycles the edit only while its render is live", () => {
    const env = loadReaction({});
    renderWith(env, {
      preview: { manual_mode: true, bond_changes: [] },
      manualEdits: [{ atoms: [0, 1], side: "reactant", type: "break" }],
    });
    const specs = env.cylinderSpecs();
    assertEq(specs.length, 1, "one manual overlay cylinder");
    assert(typeof specs[0].callback === "function", "manual cylinder carries a click callback");
    const t0 = env.calls.length;
    specs[0].callback();
    assert(env.calls.length > t0, "live manual click re-renders through the instance");
    const stale = capturedCallbacks(env);
    env.evalJs("reactionViewer.dispose()");
    const callsAtDispose = env.calls.length;
    invokeAll(stale, env);
    assertEq(env.calls.length, callsAtDispose, "stale manual click must not tick after dispose");
    assertEq(env.evalJs("reactionViewer.lastError()"), null, "no attempt recorded on the disposed instance");
  });

  await test("failure: missing 3Dmol degrades to a falsy handle without throwing", () => {
    const env = loadReaction({ with3Dmol: false });
    env.setPreviewState();
    env.sandbox.renderReactionChanges3D();
    assertEq(env.evalJs("reactionViewer"), null, "no viewer handle without the library");
    assertEq(env.created.length, 0, "nothing created");
  });

  await test("source pin: reaction-builder.js holds no raw viewer handle or adapter create", () => {
    const src = fs.readFileSync(
      path.join(__dirname, "..", "workbench", "features", "reaction-builder.js"),
      "utf8"
    );
    assert(src.indexOf("$3Dmol") < 0, "no direct $3Dmol reference");
    assert(src.indexOf("createViewer(") < 0, "no raw createViewer call");
    assert(src.indexOf("ACPVisualization.mol3d") < 0, "creation leaves the mol3d adapter for the scene instance");
    assert(src.indexOf("ACPVisualization.scene.create(") >= 0, "creation goes through the scene namespace");
    assert(src.indexOf("reactionModel.setClickable(") >= 0, "click binding lives on the model token");
    assert(src.indexOf("reactionModel.setHoverable(") >= 0, "hover binding lives on the model token");
  });
};
