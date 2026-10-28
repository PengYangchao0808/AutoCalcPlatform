/*
 * Behavior tests for the wizard preview viewer (plan todo 10, T10): the
 * preview canvas runs on a bounded ACPVisualization.scene instance created by
 * wizard.js `initPreviewViewer`, and `previewModel` is the opaque model token.
 *
 * Pins:
 *  - initPreviewViewer creates via ACPVisualization.scene.create with the
 *    frozen options, reuses a live instance, recreates only after dispose, and
 *    leaves a null handle when 3Dmol is missing (no throw);
 *  - renderPreviewStructure3D (S2 module) rebuilds through the instance:
 *    removeAllModels -> addModel token -> setStyle -> attach (bind after
 *    addModel) -> framing getter returns the instance;
 *  - missing 3Dmol: the preview empty state is preserved (overlay untouched,
 *    no model, no throw); a no-3D structure still shows its reason text;
 *  - the real canPickPESAtoms/canPickScanAtoms gates keep BOTH guards:
 *    previewModel truthiness and previewModelStructureKey — a stale truthy
 *    token with a mismatched structure key must never enable picking;
 *  - PES selection shapes go through instance tokens (addSphere/addCylinder
 *    -> token; removeShape(token) -> native removal), never native handles;
 *  - atom labels anchor on {serial} fields only — never a model handle.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", "..");

function readSource(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

function extractFn(src, name) {
  const marker = "function " + name + "(";
  const start = src.indexOf(marker);
  if (start < 0) throw new Error("missing function " + name);
  const open = src.indexOf("{", start);
  let depth = 0;
  for (let j = open; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") {
      depth--;
      if (depth === 0) return src.slice(start, j + 1);
    }
  }
  throw new Error("unbalanced function " + name);
}

function runScript(sandbox, code) {
  vm.createContext(sandbox);
  return vm.runInContext(code, sandbox);
}

function makeEl(id) {
  return {
    id,
    style: {},
    className: "",
    textContent: "",
    innerHTML: "",
    title: "",
    value: "",
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    setAttribute() {},
    querySelectorAll() { return []; },
  };
}

function makeNativeViewer() {
  const log = {
    addModel: [], modelSetStyle: [], addSphere: [], addCylinder: [],
    removeShape: [], addLabel: [], removeAllLabels: 0, removeAllModels: 0, render: 0, models: [],
  };
  const model = {
    setStyle(sel, style) { log.modelSetStyle.push({ sel: sel, style: style }); },
    addStyle() {},
    setClickable(sel, val, cb) { log.clickable = (log.clickable || []).concat([{ val: val, cb: cb }]); },
    setHoverable() {},
    selectedAtoms() {
      return [
        { serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 },
        { serial: 2, index: 1, elem: "O", x: 1, y: 0, z: 0 },
      ];
    },
  };
  log.models.push(model);
  const native = {
    log: log,
    removeAllModels() { log.removeAllModels++; },
    addModel(data, spec) { log.addModel.push({ data: data, spec: spec }); return model; },
    addStyle() {},
    render() { log.render++; },
    resize() {},
    center() {},
    zoom() {},
    zoomTo() {},
    getView() { return [0, 0, 0, 0, 0, 0, 1, 0]; },
    addSphere(spec) { log.addSphere.push(spec); return { nativeShape: "sphere" }; },
    addCylinder(spec) { log.addCylinder.push(spec); return { nativeShape: "cylinder" }; },
    addArrow() { return { nativeShape: "arrow" }; },
    removeShape(shape) { log.removeShape.push(shape); },
    removeAllShapes() {},
    addLabel(text, opts, anchor) {
      log.addLabel.push({ text: text, opts: opts, anchor: anchor });
      return { nativeLabel: "label" };
    },
    removeLabel() {},
    removeAllLabels() { log.removeAllLabels++; },
  };
  return native;
}

const WIZARD_SRC = readSource("js/workbench/features/wizard.js");
const PREVIEW_SRC = readSource("js/workbench/features/wizard/preview-validate.js");

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  const XYZ = "2\nwater\nO 0 0 0\nH 0 0 1\n";

  function baseSandbox(extra) {
    const els = {
      "structure-preview-3d": makeEl("structure-preview-3d"),
      "preview-3d-empty": makeEl("preview-3d-empty"),
      "preview-structure-title": makeEl("preview-structure-title"),
      "source-coordinate-text": makeEl("source-coordinate-text"),
    };
    const sandbox = makeSandbox(Object.assign({
      document: {
        getElementById: (id) => els[id] || null,
        querySelectorAll: () => [],
      },
      t: (k) => k,
      escapeHtml: (s) => String(s),
      currentLang: "en-US",
      previewViewer: null,
      previewModel: null,
      previewModelStructureKey: "",
      previewViewerFraming: { pending: false, settled: false, epoch: 0 },
      PREVIEW_ZOOM_FACTOR: 0.82,
      wizardStructures: [],
      wizardSelectedStructureIndex: 0,
      wizardInputMode: "structure",
      wizardAtomLabelsOn: false,
      wizardState: { workflow: null, method: { stages: {} } },
    }, extra || {}));
    sandbox.ACPWizardPreviewValidate = undefined;
    return { sandbox: sandbox, els: els };
  }

  function loadScene(sandbox, native) {
    sandbox.ACPVisualization = {
      mol3d: {
        create: function (container, options) {
          sandbox.__created = (sandbox.__created || []).concat([{ container: container, options: options }]);
          return native || null;
        },
        destroy: function () {},
      },
    };
    loadScript("js/visualization/scene_instance.js", sandbox);
    return sandbox.ACPVisualization.scene;
  }

  function installWizardFns(sandbox, names) {
    const code = names.map((n) => extractFn(WIZARD_SRC, n)).join("\n");
    runScript(sandbox, code);
    return sandbox;
  }

  await test("T10 source: no raw $3Dmol / mol3d.create left in the wizard preview slices", () => {
    for (const rel of [
      "js/workbench/features/wizard.js",
      "js/workbench/features/wizard/preview-validate.js",
    ]) {
      const src = readSource(rel);
      for (const banned of ["$3Dmol", "mol3d.create", "mol3d.destroy", "createViewer"]) {
        assert(src.indexOf(banned) === -1, rel + " still references " + banned);
      }
    }
    assert(WIZARD_SRC.indexOf("ACPVisualization.scene.create(") >= 0,
      "initPreviewViewer must create through the scene namespace");
    assert(WIZARD_SRC.indexOf("previewViewer = ACPVisualization.scene.create(") >= 0,
      "previewViewer creation is the bounded scene instance");
    assert(PREVIEW_SRC.indexOf("previewViewer = ACPVisualization.mol3d.create(") === -1,
      "creation must not return to the S2 module");
  });

  await test("initPreviewViewer: scene.create options, reuse while live, recreate after dispose", () => {
    const native = makeNativeViewer();
    const { sandbox } = baseSandbox();
    loadScene(sandbox, native);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    sandbox.initPreviewViewer();
    assertEq(sandbox.__created.length, 1, "one creation");
    assert(sandbox.__created[0].container === sandbox.document.getElementById("structure-preview-3d"),
      "creation container is structure-preview-3d");
    assertEq(sandbox.__created[0].options, { backgroundColor: "0x0e1419", antialias: true },
      "frozen creation options");
    const first = sandbox.previewViewer;
    assert(first && typeof first.addModel === "function" && typeof first.isDisposed === "function",
      "previewViewer is the restricted instance");
    sandbox.initPreviewViewer();
    assertEq(sandbox.__created.length, 1, "live instance is reused");
    assert(sandbox.previewViewer === first, "same instance kept");
    sandbox.previewViewer.dispose();
    sandbox.initPreviewViewer();
    assertEq(sandbox.__created.length, 2, "disposed instance is replaced");
    assert(sandbox.previewViewer !== first, "fresh instance after dispose");
  });

  await test("initPreviewViewer: missing 3Dmol leaves the null handle (empty state preserved)", () => {
    const { sandbox } = baseSandbox();
    loadScene(sandbox, null);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    sandbox.initPreviewViewer();
    assertEq(sandbox.previewViewer, null, "no viewer without the library");
    sandbox.initPreviewViewer();
    assertEq(sandbox.previewViewer, null, "still null, no throw");
    const bare = baseSandbox();
    bare.sandbox.ACPVisualization = {};
    loadScript("js/visualization/scene_instance.js", bare.sandbox);
    installWizardFns(bare.sandbox, ["initPreviewViewer"]);
    bare.sandbox.initPreviewViewer();
    assertEq(bare.sandbox.previewViewer, null, "no mol3d namespace -> null handle");
  });

  await test("renderPreviewStructure3D: instance rebuild — token previewModel, style, attach, framing", () => {
    const native = makeNativeViewer();
    const calls = { attach: [], clearPES: 0, clearScan: 0, framing: [] };
    const { sandbox, els } = baseSandbox({
      attachPESPreviewSelection: function () {
        calls.attach.push(sandbox.previewModel);
        if (sandbox.previewModel) sandbox.previewModel.setClickable({}, true, function () {});
      },
      clearPESPreviewSelectionShapes: function () { calls.clearPES++; },
      clearScanPreviewSelectionShapes: function () { calls.clearScan++; },
      scheduleViewerFraming: function (container, framingState, getViewer, options) {
        calls.framing.push({ container: container, getViewer: getViewer, options: options });
      },
    });
    loadScene(sandbox, native);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    loadScript("js/workbench/features/wizard/preview-validate.js", sandbox);
    const mod = sandbox.ACPWizardPreviewValidate;
    mod.renderPreviewStructure3D({ name: "mol", xyz: XYZ, has_3d: true });
    assertEq(native.log.removeAllModels, 1, "scene reset before rebuild");
    assertEq(native.log.addModel.length, 1, "one model added");
    assertEq(native.log.addModel[0].spec, "xyz", "xyz format");
    assertEq(native.log.modelSetStyle.length, 1, "instance style routed to the current model");
    const token = sandbox.previewModel;
    assert(token && typeof token.setStyle === "function" && typeof token.setClickable === "function",
      "previewModel is the opaque model token");
    assert(token !== native.log.models[0], "native model never exposed");
    assertEq(calls.attach.length, 1, "selection attach after addModel");
    assert(calls.attach[0] === token, "T12 lesson: click bind runs on the token after addModel");
    assertEq(calls.clearPES, 1, "PES shapes cleared");
    assertEq(calls.clearScan, 1, "scan shapes cleared");
    assertEq(sandbox.previewModelStructureKey, XYZ.trim(), "structure key pinned to the shown xyz");
    assertEq(calls.framing.length, 1, "framing scheduled");
    assert(calls.framing[0].getViewer() === sandbox.previewViewer,
      "framing getter returns the instance");
    assertEq(calls.framing[0].options, { zoomFactor: 0.82 }, "preview zoom factor preserved");
    assertEq(els["preview-3d-empty"].style.display, "none", "empty overlay hidden with 3D shown");
  });

  await test("renderPreviewStructure3D: missing 3Dmol keeps the preview empty state", () => {
    const calls = { attach: 0 };
    const { sandbox, els } = baseSandbox({
      attachPESPreviewSelection: function () { calls.attach++; },
      clearPESPreviewSelectionShapes: function () {},
      clearScanPreviewSelectionShapes: function () {},
      scheduleViewerFraming: function () {},
    });
    loadScene(sandbox, null);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    loadScript("js/workbench/features/wizard/preview-validate.js", sandbox);
    const mod = sandbox.ACPWizardPreviewValidate;
    els["preview-3d-empty"].style.display = "grid";
    els["preview-3d-empty"].textContent = "parse.no_3d_waiting";
    mod.renderPreviewStructure3D({ name: "mol", xyz: XYZ, has_3d: true });
    assertEq(sandbox.previewViewer, null, "degraded to the null handle");
    assertEq(sandbox.previewModel, null, "no model without a viewer");
    assertEq(calls.attach, 0, "no selection bind without a model");
    assertEq(els["preview-3d-empty"].style.display, "grid", "empty overlay stays visible");
    assertEq(els["preview-3d-empty"].textContent, "parse.no_3d_waiting", "empty state text preserved");
  });

  await test("renderPreviewStructure3D: no-3D structure shows the reason and clears the token", () => {
    const native = makeNativeViewer();
    const { sandbox, els } = baseSandbox({
      attachPESPreviewSelection: function () {},
      clearPESPreviewSelectionShapes: function () {},
      clearScanPreviewSelectionShapes: function () {},
      scheduleViewerFraming: function () {},
    });
    loadScene(sandbox, native);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    loadScript("js/workbench/features/wizard/preview-validate.js", sandbox);
    const mod = sandbox.ACPWizardPreviewValidate;
    mod.renderPreviewStructure3D({ name: "mol", xyz: XYZ, has_3d: true });
    assert(sandbox.previewModel, "token present after a real rebuild");
    mod.renderPreviewStructure3D({
      name: "flat", xyz: "", has_3d: false, errors: ["no 3D coordinates"],
    });
    assertEq(sandbox.previewModel, null, "token cleared — truthiness guard input reset");
    assertEq(sandbox.previewModelStructureKey, "", "structure key reset");
    assertEq(els["preview-3d-empty"].style.display, "grid", "empty overlay shown");
    assertEq(els["preview-3d-empty"].textContent, "no 3D coordinates", "reason text shown");
  });

  await test("preview gates: truthiness + structure-key guards (real canPickPESAtoms/canPickScanAtoms)", () => {
    const gates = ["canPickPESAtoms", "canPickScanAtoms"].map((n) => extractFn(WIZARD_SRC, n)).join("\n");
    const harness = `
      const modal = { style: { display: "flex" } };
      const document = { getElementById: (id) => (id === "job-modal" ? modal : null) };
      let createWizardStep = 2;
      const wizardStructures = [{ has_3d: true, xyz: ${JSON.stringify(XYZ)} }];
      const wizardState = { workflow: { id: "PESsearch" } };
      let previewModel = { setClickable() {} };
      let previewModelStructureKey = wizardStructures[0].xyz.trim();
      const s2scanState = { atoms: [{}, {}, {}] };
      function currentPESStructure() { return wizardStructures[0]; }
      function scanWizardCurrentStructure() { return wizardStructures[0]; }
      function isPESsearchTask() { return wizardState.workflow.id === "PESsearch"; }
    `;
    const checks = `
      const out = {};
      out.pesTokenMatch = canPickPESAtoms();
      previewModel = null;
      out.pesNoToken = canPickPESAtoms();
      previewModel = { setClickable() {} };
      previewModelStructureKey = "2\\nother\\nH 0 0 0\\n";
      out.pesStaleKey = canPickPESAtoms();
      out.scanStaleKey = canPickScanAtoms();
      wizardState.workflow = { id: "scan" };
      out.scanStaleKeyScanFlow = canPickScanAtoms();
      previewModelStructureKey = wizardStructures[0].xyz.trim();
      out.scanTokenMatch = canPickScanAtoms();
      out;
    `;
    const out = runScript(baseSandbox().sandbox, harness + gates + checks);
    assertEq(out.pesTokenMatch, true, "token + matching key enables PES picking");
    assertEq(out.pesNoToken, false, "truthiness guard: no token, no picking");
    assertEq(out.pesStaleKey, false,
      "structure-key guard: a stale truthy token with a mismatched key must not enable picking");
    assertEq(out.scanStaleKeyScanFlow, false,
      "structure-key guard holds on the scan flow too (must fail if the key guard is bypassed)");
    assertEq(out.scanTokenMatch, true, "scan picking enabled on token + matching key");
  });

  await test("PES selection shapes: instance tokens in, removeShape(token) out", () => {
    const native = makeNativeViewer();
    const { sandbox } = baseSandbox();
    loadScene(sandbox, native);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    sandbox.initPreviewViewer();
    sandbox.isPESsearchTask = function () { return true; };
    sandbox.s2scanState = { atoms: [
      { elem: "O", x: 0, y: 0, z: 0 },
      { elem: "H", x: 1, y: 0, z: 0 },
    ] };
    runScript(sandbox, "var pesSelectionState = { selectionKind: 'bond_stretch', selectedAtoms: [0, 1], shapes: [] };");
    installWizardFns(sandbox, ["clearPESPreviewSelectionShapes", "renderPESPreviewSelection"]);
    sandbox.renderPESPreviewSelection();
    assertEq(native.log.addSphere.length, 2, "one sphere per selected atom");
    assertEq(native.log.addCylinder.length, 1, "one dashed cylinder for the pair");
    assertEq(sandbox.pesSelectionState.shapes.length, 3, "three markers tracked");
    for (const shape of sandbox.pesSelectionState.shapes) {
      assert(shape && typeof shape === "object" && shape.nativeShape === undefined,
        "shape handles are opaque instance tokens, never native shapes");
    }
    sandbox.clearPESPreviewSelectionShapes();
    assertEq(native.log.removeShape.length, 3, "each token removed through removeShape(token)");
    assertEq(sandbox.pesSelectionState.shapes.length, 0, "token list drained");
    assert(native.log.removeShape.every((s) => s && s.nativeShape),
      "native removal receives the mapped native shapes");
  });

  await test("atom labels: addLabel anchors on {serial} — never a model handle", () => {
    const native = makeNativeViewer();
    const { sandbox } = baseSandbox({
      attachPESPreviewSelection: function () {},
      clearPESPreviewSelectionShapes: function () {},
      clearScanPreviewSelectionShapes: function () {},
      scheduleViewerFraming: function () {},
    });
    loadScene(sandbox, native);
    installWizardFns(sandbox, ["initPreviewViewer"]);
    loadScript("js/workbench/features/wizard/preview-validate.js", sandbox);
    const mod = sandbox.ACPWizardPreviewValidate;
    mod.renderPreviewStructure3D({ name: "mol", xyz: XYZ, has_3d: true });
    assert(sandbox.previewModel, "token present");
    const recorded = [];
    sandbox.previewViewer = {
      removeAllLabels() { recorded.push(["removeAllLabels"]); },
      addLabel(text, opts, anchor) { recorded.push(["addLabel", text, opts, anchor]); return {}; },
      render() {},
    };
    sandbox.wizardAtomLabelsOn = true;
    mod._togglePreviewAtomLabels();
    assertEq(recorded[0][0], "removeAllLabels", "labels reset before redraw");
    const labels = recorded.filter((r) => r[0] === "addLabel");
    assertEq(labels.length, 2, "one label per atom");
    assertEq(labels.map((l) => l[3].serial), [1, 2], "labels anchor on serial");
    for (const l of labels) {
      assert(l[3].model === undefined, "business anchors never carry a model handle");
    }
    assertEq(labels.map((l) => l[1]), ["C", "O"], "label text from the element");
  });
};
