/* T9: energy preview viewer migration — the energy mini canvas holds a
 * bounded ACPVisualization.scene instance (never a raw 3Dmol handle).
 * Stack under test: framing.js + mol3d_adapter.js + scene_instance.js +
 * workbench/features/energy.js against a recording $3Dmol stub, so the
 * creation/style/teardown contract is verified through the REAL instance.
 * Failure case: missing 3Dmol / missing scene module must degrade to a
 * false loader result WITHOUT throwing (stub construction). */
"use strict";

module.exports.register = async function (h) {
  const { test, assert, assertEq, loadScript, makeSandbox } = h;

  function makeNativeViewer(calls) {
    const model = {
      setStyle: (sel, style) => calls.push(["model.setStyle", sel, style]),
      addStyle: () => calls.push(["model.addStyle"]),
    };
    return {
      clear: () => calls.push(["clear"]),
      addModel: (data, spec) => { calls.push(["addModel", data, spec]); return model; },
      removeAllModels: () => calls.push(["removeAllModels"]),
      removeAllShapes: () => calls.push(["removeAllShapes"]),
      removeAllLabels: () => calls.push(["removeAllLabels"]),
      getView: () => [1, 2, 3],
      setView: () => calls.push(["setView"]),
      render: () => calls.push(["render"]),
      resize: () => calls.push(["resize"]),
      pngURI: () => "data:image/png;base64,energy",
    };
  }

  function loadEnergy(opts, withD3Dmol) {
    opts = opts || {};
    const created = [];
    const nativeCalls = [];
    const sandbox = makeSandbox({
      requestAnimationFrame: (fn) => { setImmediate(fn); return 1; },
    });
    sandbox._hasCanvas = false;
    sandbox._container = {
      innerHTML: "stale",
      isConnected: true,
      clientWidth: 300,
      clientHeight: 200,
      querySelector: () => (sandbox._hasCanvas ? {} : null),
    };
    sandbox.document = {
      getElementById: (id) => (id === "energy-structure-viewer" ? sandbox._container : null),
    };
    if (withD3Dmol) {
      sandbox.$3Dmol = {
        createViewer: (container, options) => {
          created.push({ container, options });
          sandbox._hasCanvas = true;
          return makeNativeViewer(nativeCalls);
        },
      };
    }
    loadScript("js/visualization/framing.js", sandbox);
    loadScript("js/visualization/mol3d_adapter.js", sandbox);
    if (!opts.skipSceneModule) {
      loadScript("js/visualization/scene_instance.js", sandbox);
    }
    // page-level bare-global delegators (from the HTML inline script)
    sandbox.scheduleViewerFraming = sandbox.ACPVisualization.framing.scheduleViewerFraming;
    sandbox.disposeViewerFraming = sandbox.ACPVisualization.framing.disposeViewerFraming;
    sandbox.energyGraphState = {
      viewer: null,
      requestToken: 0,
      loadedFrameIndex: null,
      loadedFrameId: "",
      structureFraming: { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 },
      resizeObserver: null,
      structureAbort: null,
    };
    const registered = {};
    sandbox.ACPStructureViewer = {
      registerCanvasLoader: (id, fn) => { registered[id] = fn; },
    };
    loadScript("js/workbench/features/energy.js", sandbox);
    return { sandbox, registered, created, nativeCalls };
  }

  await test("energy loader: creates a bounded scene instance (exact options); consumers get the instance", () => {
    const { sandbox, registered, created } = loadEnergy({}, true);
    const loader = registered["energy-mini"];
    assert(typeof loader === "function", "energy-mini loader must be registered");
    const ok = loader("2\nx\nH 0 0 0\nH 0 0 1\n", { sphere: { scale: 0.3 } });
    assertEq(ok, true);
    assertEq(created.length, 1, "exactly one viewer creation");
    assert(created[0].container === sandbox._container, "creation container");
    assertEq(created[0].options, { backgroundColor: "0x0e1419", antialias: true });
    assertEq(sandbox._container.innerHTML, "", "container cleared before creation");

    const inst = sandbox.energyGraphState.viewer;
    assert(inst && typeof inst.dispose === "function", "state holds the scene instance");
    for (const m of ["pngURI", "frame", "getView", "setView", "resize", "render", "removeAllModels"]) {
      assert(typeof inst[m] === "function", "instance." + m + " must exist for consumers/framing");
    }
    assertEq(sandbox.ACPStructureViewer._canvasViewerImpl("energy-mini"), inst,
      "_canvasViewerImpl hands out the instance (camera/PNG consumers)");
    assertEq(sandbox.ACPStructureViewer._canvasViewerImpl("other"), null);
  });

  await test("energy loader: reuse path clears models via the instance, no second creation", () => {
    const { sandbox, registered, created, nativeCalls } = loadEnergy({}, true);
    const loader = registered["energy-mini"];
    loader("1\nx\nH 0 0 0\n", {});
    const first = sandbox.energyGraphState.viewer;
    nativeCalls.length = 0;
    loader("1\nx\nH 0 0 1\n", { sphere: { scale: 0.3 } });
    assertEq(created.length, 1, "no re-creation on reload");
    assertEq(sandbox.energyGraphState.viewer, first, "same instance reused");
    assertEq(nativeCalls[0][0], "removeAllModels", "reuse path clears through the instance");
    assertEq(nativeCalls[1][0], "addModel", "then adds the new frame model");
    assertEq(nativeCalls[2][0], "model.setStyle", "style applied to the current model");
  });

  await test("energyGraphDestroyViewer: routes to dispose(); idempotent teardown (native clear exactly once)", () => {
    const { sandbox, registered, created, nativeCalls } = loadEnergy({}, true);
    const loader = registered["energy-mini"];
    loader("1\nx\nH 0 0 0\n", {});
    const inst = sandbox.energyGraphState.viewer;
    nativeCalls.length = 0;
    sandbox.energyGraphDestroyViewer();
    assertEq(sandbox.energyGraphState.viewer, null, "reference dropped");
    assert(inst.isDisposed() === true, "instance disposed");
    assertEq(nativeCalls.filter((c) => c[0] === "clear").length, 1, "native clear runs exactly once");
    sandbox.energyGraphDestroyViewer();
    assert(inst.isDisposed() === true, "re-entry safe");
    assertEq(nativeCalls.filter((c) => c[0] === "clear").length, 1, "no double release");
    assertEq(created.length, 1);
  });

  await test("failure: missing 3Dmol degrades to false WITHOUT throwing (stub construction)", () => {
    const { sandbox, registered } = loadEnergy({}, false);
    const loader = registered["energy-mini"];
    let result = "unset";
    let threw = null;
    try { result = loader("1\nx\nH 0 0 0\n", {}); } catch (e) { threw = e; }
    assertEq(threw, null, "loader must not throw when 3Dmol is missing");
    assertEq(result, false, "loader reports unavailable");
    assertEq(sandbox.energyGraphState.viewer, null, "no viewer instance is held");
  });

  await test("failure: missing scene module degrades to false WITHOUT throwing", () => {
    const { sandbox, registered } = loadEnergy({ skipSceneModule: true }, true);
    const loader = registered["energy-mini"];
    let threw = null;
    let result = "unset";
    try { result = loader("1\nx\nH 0 0 0\n", {}); } catch (e) { threw = e; }
    assertEq(threw, null, "loader must not throw when the scene module is absent");
    assertEq(result, false);
    assertEq(sandbox.energyGraphState.viewer, null);
    sandbox.energyGraphDestroyViewer();
    assertEq(sandbox.energyGraphState.viewer, null);
  });
};
