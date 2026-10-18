/* T12: NMR per-candidate structure viewers migrate to bounded scene instances
 * (nmrPanelState.viewers holds instances, never raw 3Dmol handles).
 * Stack under test: framing.js + mol3d_adapter.js + scene_instance.js +
 * workbench/features/nmr.js against a recording $3Dmol stub, so creation,
 * per-load model/shape/teardown access and the candidate-switch lifecycle run
 * through the REAL instance. Failure case: candidate switch must dispose the
 * replaced instance — the stub counter catches every leaked viewer. */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;
  const fs = require("fs");
  const path = require("path");

  function makeNativeViewer(calls, id) {
    const model = {
      __id: id + "/model",
      setStyle: (sel, style, add) => calls.push(["model.setStyle", sel, style, add]),
      setClickable: (sel, val, cb) => { calls.push(["model.setClickable", sel, val]); model.__click = cb; },
      selectedAtoms: (sel) => { calls.push(["model.selectedAtoms", sel]); return model.__atoms; },
    };
    const viewer = {
      __id: id,
      __clearCount: 0,
      __model: model,
      clear: () => { viewer.__clearCount += 1; calls.push(["clear", id]); },
      addModel: (data, spec) => { calls.push(["addModel", data, spec]); return model; },
      removeAllModels: () => calls.push(["removeAllModels"]),
      setStyle: (...a) => { calls.push(["setStyle", ...a]); return viewer; },
      addSphere: (spec) => { calls.push(["addSphere", spec]); return { __shape: true }; },
      removeAllShapes: () => calls.push(["removeAllShapes"]),
      selectedAtoms: (sel) => calls.push(["viewer.selectedAtoms", sel]),
      render: () => calls.push(["render"]),
      resize: () => calls.push(["resize"]),
      center: (...a) => calls.push(["center", ...a]),
      zoomTo: (...a) => calls.push(["zoomTo", ...a]),
      zoom: (...a) => calls.push(["zoom", ...a]),
    };
    return viewer;
  }

  function loadNmr(opts) {
    opts = opts || {};
    const created = [];
    const nativeCalls = [];
    const registered = {};
    const sandbox = makeSandbox({
      requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
    });
    sandbox._containers = {};
    function makeContainer(key) {
      const c = {
        key,
        _hasCanvas: false,
        innerHTML: "loading",
        textContent: "loading",
        className: "nmr-structure-viewer empty",
        isConnected: true,
        clientWidth: 300,
        clientHeight: 200,
        querySelector: (sel) => (c._hasCanvas && sel === "canvas" ? {} : null),
      };
      sandbox._containers[key] = c;
      return c;
    }
    makeContainer("0");
    makeContainer("1");
    sandbox.document = {
      getElementById: () => null,
      querySelector: (sel) => {
        const m = /\[data-nmr-structure-viewer="(\d+)"\]/.exec(sel);
        return m ? (sandbox._containers[m[1]] || null) : null;
      },
    };
    sandbox.t = (k) => k;
    if (opts.with3Dmol !== false) {
      sandbox.$3Dmol = {
        createViewer: (container, options) => {
          const v = makeNativeViewer(nativeCalls, "v" + created.length);
          created.push({ container, options, viewer: v });
          container._hasCanvas = true;
          return v;
        },
      };
    }
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    if (!opts.skipSceneModule) {
      loadScript("js/visualization/scene_instance.js", sandbox);
    }
    sandbox.scheduleViewerFraming = sandbox.ACPVisualization.framing.scheduleViewerFraming;
    sandbox.disposeViewerFraming = sandbox.ACPVisualization.framing.disposeViewerFraming;
    sandbox.ACPStructureViewer = {
      registerCanvasLoader: (id, fn) => { registered[id] = fn; },
      sharedLoadGeometry: (xyz, o) => {
        const fn = registered[o && o.canvasId];
        if (typeof fn !== "function") return null;
        return { loaded: !!fn(xyz, { sphere: { scale: 0.3 } }) };
      },
    };
    loadScript("js/workbench/features/nmr.js", sandbox);
    return { sandbox, registered, created, nativeCalls };
  }

  await test("nmr viewers are scene instances: exact creation options; registry holds the instance", () => {
    const { sandbox, created } = loadNmr({});
    assertEq(sandbox.NMR_STRUCTURE_VIEWER_LIMIT, 8, "per-candidate viewer cap unchanged");
    const info = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    assert(info && info.instance, "registry entry holds an instance");
    assertEq(created.length, 1, "exactly one viewer creation");
    assert(created[0].container === sandbox._containers["0"], "creation container");
    assertEq(created[0].options, { backgroundColor: "0x0e1419", antialias: true });
    assertEq(sandbox._containers["0"].innerHTML, "", "container cleared before creation");
    for (const m of ["dispose", "isDisposed", "selectedAtoms", "addModel", "removeAllModels",
      "addSphere", "removeAllShapes", "render", "setClickable"]) {
      assert(typeof info.instance[m] === "function", "instance." + m + " must exist for consumers");
    }
    assert(!("viewer" in info.instance), "the instance never exposes a raw viewer handle");
    assert(sandbox.nmrPanelState.viewers[0] === info, "nmrPanelState.viewers holds the entry");
  });

  await test("registry reuse: same container + canvas keeps the same instance (no re-creation)", () => {
    const { sandbox, created } = loadNmr({});
    const first = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    const again = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    assertEq(created.length, 1, "no second creation on reuse");
    assert(again === first, "same entry returned");
    assert(first.instance.isDisposed() === false, "instance stays live on reuse");
  });

  await test("failure: candidate switch disposes the OLD instance — stub counter catches leaks", () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    const info1 = sandbox.nmrEnsureViewer(0, container);
    const inst1 = info1.instance;
    const v1 = created[0].viewer;
    // Live post-load reality (probed with real 3Dmol): container.textContent = ""
    // detaches the canvas, so the reuse check fails and the entry is replaced.
    container._hasCanvas = false;
    const info2 = sandbox.nmrEnsureViewer(0, container);
    const inst2 = info2.instance;
    const v2 = created[1].viewer;

    assert(inst1 !== inst2, "a fresh instance replaces the old one");
    assert(inst1.isDisposed() === true, "OLD instance disposed on switch");
    assert(inst2.isDisposed() === false, "new instance live");
    assertEq(v1.__clearCount, 1, "old native viewer released exactly once (leak counter)");
    assertEq(v2.__clearCount, 0, "new native viewer untouched");
    assertEq(created.length, 2, "stub creation counter: 2 created, 1 disposed, 1 live");
    assertEq(Object.keys(sandbox.nmrPanelState.viewers).length, 1, "registry holds one entry per candidate");
    assert(sandbox.nmrPanelState.viewers[0].instance === inst2, "registry points at the live instance");
    const leak = created.reduce((n, c) => n + c.viewer.__clearCount, 0);
    assertEq(leak, created.length - 1, "every replaced instance is released (only the live one remains)");
  });

  await test("nmrPanelDispose disposes every instance exactly once and empties the registry", () => {
    const { sandbox, created } = loadNmr({});
    sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    sandbox.nmrEnsureViewer(1, sandbox._containers["1"]);
    assertEq(created.length, 2);
    const token = sandbox.nmrPanelDispose();
    assertEq(token, sandbox.nmrPanelState.token, "dispose bumps the load token");
    assertEq(created[0].viewer.__clearCount, 1, "candidate 0 released exactly once");
    assertEq(created[1].viewer.__clearCount, 1, "candidate 1 released exactly once");
    assertEq(sandbox.nmrPanelState.viewers, {}, "registry emptied");
    sandbox.nmrPanelDispose();
    assertEq(created[0].viewer.__clearCount, 1, "idempotent re-entry: no double release");
    assertEq(created[1].viewer.__clearCount, 1, "idempotent re-entry: no double release");
  });

  await test("load path through the instance: clear/addModel/style + 3D pick on the model token", async () => {
    const { sandbox, registered, created, nativeCalls } = loadNmr({});
    const info = sandbox.nmrEnsureViewer(0, sandbox._containers["0"]);
    info.labels = ["C1", "C2", "H1"];
    sandbox.nmrPanelState.loadingCandidate = 0;
    const ok = registered["nmr-structure"]("3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n", { sphere: { scale: 0.3 } });
    sandbox.nmrPanelState.loadingCandidate = null;
    assertEq(ok, true);
    const kinds = nativeCalls.map((c) => c[0]);
    assertEq(kinds[0], "removeAllModels", "reuse load clears through the instance");
    assertEq(kinds[1], "addModel", "then adds the geometry model");
    assertEq(nativeCalls[1][2], "xyz", "xyz spec kept");
    assertEq(kinds[2], "model.setStyle", "style applied to the current model");
    const v = created[0].viewer;
    assert(typeof v.__model.__click === "function", "3D pick bound on the live model token");
    v.__model.__click({ index: 1, elem: "C", serial: 2, x: 1, y: 0, z: 0 });
    assertEq(sandbox.nmrPanelState.selectedAtom, "C2", "pick resolves label via the shared linkage state");
    assertEq(sandbox.nmrPanelState.selectedCandidate, 0);
  });

  await test("load flow: selectedAtoms snapshot fills labels; failure falls back to []", async () => {
    const { sandbox, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "3\nx\nC 0 0 0\nC 1 0 0\nH 0 1 0\n";
    created.length = 0;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    let info = sandbox.nmrPanelState.viewers[0];
    assert(info && info.instance, "viewer instance created through the load flow");
    created[0].viewer.__model.__atoms = [
      { serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 },
      { serial: 2, index: 1, elem: "C", x: 1, y: 0, z: 0 },
      { serial: 3, index: 2, elem: "H", x: 0, y: 1, z: 0 },
    ];
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    info = sandbox.nmrPanelState.viewers[0];
    assertEq(info.atoms.length, 3, "atom snapshot through instance.selectedAtoms");
    assertEq(info.labels, ["C1", "C2", "H1"], "element+count labels kept");
    // Failure contract: restricted selectedAtoms returns null -> [] fallback.
    created[0].viewer.__model.__atoms = null;
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    info = sandbox.nmrPanelState.viewers[0];
    assertEq(info.atoms, [], "null selectedAtoms falls back to []");
    assertEq(info.labels, [], "labels derived from the [] fallback");
  });

  await test("framing goes through the instance; highlight spheres are instance shape tokens", async () => {
    const { sandbox, nativeCalls, created } = loadNmr({});
    const container = sandbox._containers["0"];
    sandbox.nmrFetchCandidateGeometry = async () => "1\nx\nC 0 0 0\n";
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    created[0].viewer.__model.__atoms = [{ serial: 1, index: 0, elem: "C", x: 0, y: 0, z: 0 }];
    await sandbox.nmrLoadCandidateGeometry({ id: "job1" }, { index: 0 }, 0);
    await new Promise((r) => setTimeout(r, 30));
    const kinds = nativeCalls.map((c) => c[0]);
    for (const k of ["resize", "center", "zoomTo", "render"]) {
      assert(kinds.includes(k), "framing pass must drive instance." + k);
    }
    nativeCalls.length = 0;
    sandbox.nmrHighlightAtoms({ C1: true }, "C1");
    assertEq(nativeCalls[0][0], "removeAllShapes", "highlights reset through the instance");
    const sphere = nativeCalls.find((c) => c[0] === "addSphere");
    assert(sphere, "highlight sphere drawn");
    assertEq(sphere[1].center, { x: 0, y: 0, z: 0 }, "sphere centered on the snapshot atom");
    assertEq(sphere[1].color, "#4ea1ff", "active atom color");
    assertEq(nativeCalls[nativeCalls.length - 1][0], "render", "scene refreshed");
    assert(container._hasCanvas === true, "canvas still attached on the reuse path");
  });

  await test("failure: missing 3Dmol / missing scene module / failed create degrade to null without throwing", () => {
    const noLib = loadNmr({ with3Dmol: false });
    let threw = null;
    let info = "unset";
    try { info = noLib.sandbox.nmrEnsureViewer(0, noLib.sandbox._containers["0"]); } catch (e) { threw = e; }
    assertEq(threw, null, "must not throw when 3Dmol is missing");
    assertEq(info, null, "ensure reports unavailable");
    assertEq(noLib.sandbox.nmrPanelState.viewers, {}, "no stale registry entry");

    const noScene = loadNmr({ skipSceneModule: true });
    threw = null;
    try { info = noScene.sandbox.nmrEnsureViewer(0, noScene.sandbox._containers["0"]); } catch (e) { threw = e; }
    assertEq(threw, null, "must not throw when the scene module is missing");
    assertEq(info, null);
    assertEq(noScene.sandbox.nmrPanelState.viewers, {}, "no stale registry entry");
  });

  await test("source pin: nmr.js holds no raw viewer handles or second adapter", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "workbench", "features", "nmr.js"), "utf8");
    for (const banned of ["ACPVisualization.mol3d.create(", "ACPVisualization.mol3d.destroy(", "$3Dmol", "getModel(", "info.viewer"]) {
      assert(src.indexOf(banned) < 0, "nmr.js must not reference " + banned);
    }
    assert(src.indexOf("ACPVisualization.scene") >= 0, "creation goes through the scene namespace");
    assert(src.indexOf(".dispose()") >= 0, "per-candidate cleanup goes through dispose()");
    assert(src.indexOf("selectedAtoms({})") >= 0, "atom reads use the restricted selectedAtoms call");
  });
};
