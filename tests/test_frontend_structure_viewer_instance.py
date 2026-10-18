"""T14: structure view/edit on the restricted scene instance — behavior
comparison for the measurement/edit bridges.

Coverage: IRC loader token-style dual path + per-frame recipe order, camera
save/restore instance error contract, view-state null-tolerant camera paths,
_removeOverlayModels token-handle teardown, renderOverlay model-A highlight
dual path, and the edit-push no-camera-jump contract on the STORE-owned
coordinates-update path (QA: the framing test FAILS if setView/center/zoomTo
is introduced there).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
FRONTEND_JS_DIR = REPO_ROOT / "frontend" / "js"
_SV_JS = FRONTEND_JS_DIR / "structure_viewer.js"
_SE_JS = FRONTEND_JS_DIR / "structure_editor.js"
_GS_JS = FRONTEND_JS_DIR / "geometry_store.js"
_MV_JS = FRONTEND_JS_DIR / "workbench" / "features" / "molecule-view.js"


def _run_node(script: str, name: str) -> None:
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (
        f"{name} failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Item 1: IRC loader — token setStyle dual path + todo-29 recipe order
# ---------------------------------------------------------------------------


def test_irc_loader_token_style_dual_path_and_recipe_order() -> None:
    """main-irc adapter: styles through the addModel token when it has
    setStyle, falls back to viewer-level setStyle for token-less stubs
    (test_irc_playback_node_logic's stub keeps passing), and the recipe stays
    getView -> removeAllModels -> addModel -> style -> setView(same view)
    -> render."""
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;
        var marker = { marker: 1 };
        ns._styleSpecImpl = function () { return marker; };

        // (a) token path: model.setStyle({}, spec); viewer.setStyle NEVER runs
        var calls = [];
        var theView = { seq: 1 };
        var modelToken = {
          setStyle: function (sel, spec) { calls.push(["modelStyle", sel, spec]); },
        };
        var fakeToken = {
          getView: function () { calls.push(["getView"]); return theView; },
          removeAllModels: function () { calls.push(["removeAllModels"]); },
          addModel: function (text, fmt) { calls.push(["addModel", text, fmt]); return modelToken; },
          setStyle: function (sel, spec) { calls.push(["viewerStyle", sel, spec]); },
          setView: function (v) { calls.push(["setView", v]); },
          render: function () { calls.push(["render"]); },
        };
        ns._mainViewerImpl = function () { return fakeToken; };
        var r = ns.sharedLoadGeometry("2\\nirc\\nH 0 0 0\\nH 0 0 1\\n",
            { canvasId: "main-irc", source: "irc_playback" });
        if (!r || !r.loaded) { console.error("FAIL: loader dispatch"); process.exit(1); }
        var kinds = calls.map(function (c) { return c[0]; });
        if (kinds.join(",") !== "getView,removeAllModels,addModel,modelStyle,setView,render") {
          console.error("FAIL: recipe order " + kinds.join(",")); process.exit(1);
        }
        if (calls[3][2] !== marker || calls[3][1] && calls[3][1].setStyle) {
          console.error("FAIL: modelStyle spec/selection"); process.exit(1);
        }
        if (calls[4][1] !== theView) {
          console.error("FAIL: setView must restore the SAME step view"); process.exit(1);
        }
        if (calls[2][2] !== "xyz") {
          console.error("FAIL: addModel format"); process.exit(1);
        }

        // (b) stub fallback: addModel returns no token -> viewer-level setStyle
        var calls2 = [];
        var fakeStub = {
          getView: function () { calls2.push(["getView"]); return { seq: 2 }; },
          removeAllModels: function () { calls2.push(["removeAllModels"]); },
          addModel: function (text, fmt) { calls2.push(["addModel", text, fmt]); },
          setStyle: function (sel, spec) { calls2.push(["viewerStyle", sel, spec]); },
          setView: function (v) { calls2.push(["setView", v]); },
          render: function () { calls2.push(["render"]); },
        };
        ns._mainViewerImpl = function () { return fakeStub; };
        var r2 = ns.sharedLoadGeometry("2\\nirc\\nH 0 0 0\\nH 0 0 1\\n",
            { canvasId: "main-irc", source: "irc_playback" });
        if (!r2 || !r2.loaded) { console.error("FAIL: stub loader dispatch"); process.exit(1); }
        var kinds2 = calls2.map(function (c) { return c[0]; });
        if (kinds2.join(",") !== "getView,removeAllModels,addModel,viewerStyle,setView,render") {
          console.error("FAIL: stub fallback path " + kinds2.join(",")); process.exit(1);
        }
        if (calls2[3][2] !== marker) {
          console.error("FAIL: fallback style spec"); process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS)))

    _run_node(script, "IRC loader dual-path node test")


# ---------------------------------------------------------------------------
# Item 2: saveCamera / restoreCamera — instance error contract
# ---------------------------------------------------------------------------


def test_save_restore_camera_instance_error_contract() -> None:
    """saveCamera returns false and stores nothing when the instance getView
    returns its explicit failure null; restoreCamera returns false when the
    instance setView returns false (render skipped), render is best-effort on
    the success path, and stub/native returns (array/undefined/throw) behave
    exactly as before."""
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;
        var renderCount = 0;
        function fake(getViewFn, setViewFn) {
          return {
            getView: getViewFn,
            setView: setViewFn,
            render: function () { renderCount += 1; },
          };
        }

        // instance getView failure (explicit null) -> false, never stored
        ns._canvasViewerImpl = function () {
          return fake(function () { return null; }, function () {});
        };
        if (ns.saveCamera("mini") !== false) {
          console.error("FAIL: null getView must fail saveCamera"); process.exit(1);
        }
        if ("mini" in ns.geometryStore.cameras) {
          console.error("FAIL: null getView must not store a camera"); process.exit(1);
        }

        // getView throw -> false (unchanged legacy behavior)
        ns._canvasViewerImpl = function () {
          return fake(function () { throw new Error("boom"); }, function () {});
        };
        if (ns.saveCamera("mini") !== false) {
          console.error("FAIL: getView throw"); process.exit(1);
        }

        // array getView -> true + stored
        ns._canvasViewerImpl = function () {
          return fake(function () { return [1, 2, 3]; }, function () {});
        };
        if (ns.saveCamera("mini") !== true ||
            ns.geometryStore.cameras.mini.join(",") !== "1,2,3") {
          console.error("FAIL: camera stored"); process.exit(1);
        }

        // instance setView false -> restoreCamera false, render NOT called
        var before = renderCount;
        ns._canvasViewerImpl = function () {
          return fake(function () { return [1, 2, 3]; }, function () { return false; });
        };
        if (ns.restoreCamera("mini") !== false) {
          console.error("FAIL: setView false must fail restoreCamera"); process.exit(1);
        }
        if (renderCount !== before) {
          console.error("FAIL: render must not run after failed setView"); process.exit(1);
        }

        // setView true -> restore true + best-effort render
        var seen = null;
        ns._canvasViewerImpl = function () {
          return fake(function () { return [1, 2, 3]; },
            function (v) { seen = v; return true; });
        };
        if (ns.restoreCamera("mini") !== true || renderCount !== before + 1) {
          console.error("FAIL: successful restore"); process.exit(1);
        }
        if (!seen || seen.join(",") !== "1,2,3") {
          console.error("FAIL: stored view passed to setView"); process.exit(1);
        }

        // setView throw -> false (unchanged)
        ns._canvasViewerImpl = function () {
          return fake(function () { return [1, 2, 3]; },
            function () { throw new Error("nope"); });
        };
        if (ns.restoreCamera("mini") !== false) {
          console.error("FAIL: setView throw"); process.exit(1);
        }

        // undefined setView return (native/stub) -> unchanged true
        ns._canvasViewerImpl = function () {
          return fake(function () { return [1, 2, 3]; }, function () {});
        };
        if (ns.restoreCamera("mini") !== true) {
          console.error("FAIL: stub setView return undefined"); process.exit(1);
        }

        // main canvas honors the same contract via _mainViewerImpl
        ns._mainViewerImpl = function () {
          return fake(function () { return null; }, function () {});
        };
        if (ns.saveCamera("main") !== false) {
          console.error("FAIL: main canvas null getView"); process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS)))

    _run_node(script, "camera save/restore instance contract node test")


# ---------------------------------------------------------------------------
# Item 3: saveViewState / restoreViewState — null-tolerant camera paths
# ---------------------------------------------------------------------------


def test_view_state_null_tolerant_camera_paths() -> None:
    """An instance getView returning its failure null saves a camera:null
    view state without throwing and restores without any setView; a setView
    returning false on restore is best-effort (payload still returned)."""
    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;

        var storageData = {};
        ns._storageImpl = {
          getItem: function (k) { return (k in storageData) ? storageData[k] : null; },
          setItem: function (k, v) { storageData[k] = String(v); },
          removeItem: function (k) { delete storageData[k]; },
        };
        var timers = [];
        ns._setTimeoutImpl = function (fn) { timers.push(fn); };

        // instance getView failure -> camera null in the payload, no throw
        var setViews = [];
        ns._mainViewerImpl = function () {
          return {
            getView: function () { return null; },
            setView: function (v) { setViews.push(v); },
            render: function () {},
          };
        };
        ns.state.jobId = "job1";
        ns.state.selectedEntryId = "e1";
        ns.state.displayedCoords = [[0, 0, 0], [1, 1, 1]];
        var saved = ns.saveViewState();
        if (!saved || saved.camera !== null) {
          console.error("FAIL: null getView -> camera null"); process.exit(1);
        }

        // restoring a null camera schedules NO setView at all
        timers.length = 0;
        var restored = ns.restoreViewState("job1", "e1");
        if (!restored || restored.camera !== null) {
          console.error("FAIL: null-camera restore payload"); process.exit(1);
        }
        if (timers.length !== 0) {
          console.error("FAIL: null camera must skip the deferred setView"); process.exit(1);
        }

        // setView returning false is best-effort: payload still returned
        storageData["acp.sv.view.job1:e2"] = JSON.stringify({
          version: 1, camera: [5, 6, 7], stylePreset: null,
          measurements: null, atomCount: 2, savedAt: "t",
        });
        ns._mainViewerImpl = function () {
          return {
            getView: function () { return [9]; },
            setView: function () { return false; },
            render: function () {},
          };
        };
        timers.length = 0;
        var restored2 = ns.restoreViewState("job1", "e2");
        if (!restored2 || restored2.camera.join(",") !== "5,6,7") {
          console.error("FAIL: payload must come back despite setView false");
          process.exit(1);
        }
        if (timers.length !== 1) {
          console.error("FAIL: camera restore deferred"); process.exit(1);
        }
        timers[0]();
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS)))

    _run_node(script, "view-state null-tolerant node test")


# ---------------------------------------------------------------------------
# Item 4: _removeOverlayModels — token handles converge on model A
# ---------------------------------------------------------------------------


def test_remove_overlay_models_token_handles_converge() -> None:
    """The teardown loop passes the getModel(1) handle to removeModel, never
    touches model A (index 0), converges to exactly one model, and is a
    no-op on stubs without getModelCount."""
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;

        // (a) token models [A, B, C]
        var A = { name: "A" }, B = { name: "B" }, C = { name: "C" };
        var models = [A, B, C];
        var removed = [];
        var renderCount = 0;
        var fakeToken = {
          getModelCount: function () { return models.length; },
          getModel: function (i) { return models[i]; },
          removeModel: function (h) {
            removed.push(h);
            var k = models.indexOf(h);
            if (k >= 0) models.splice(k, 1);
          },
          render: function () { renderCount += 1; },
        };
        ns._mainViewerImpl = function () { return fakeToken; };
        ns.overlayState.active = true;
        ns.clearOverlay();
        if (removed.length !== 2) {
          console.error("FAIL: exactly two extra models, got " + removed.length);
          process.exit(1);
        }
        if (removed[0] !== B) {
          console.error("FAIL: first removal must be the getModel(1) handle");
          process.exit(1);
        }
        if (removed[1] !== C) {
          console.error("FAIL: loop must converge on the next extra model");
          process.exit(1);
        }
        if (removed.indexOf(A) !== -1) {
          console.error("FAIL: model A (index 0) must never be removed");
          process.exit(1);
        }
        if (models.length !== 1 || models[0] !== A) {
          console.error("FAIL: must converge to exactly 1 model (A)");
          process.exit(1);
        }
        if (renderCount < 1) {
          console.error("FAIL: render after teardown"); process.exit(1);
        }

        // (b) stub without getModelCount -> no-op removal path
        var removedB = [];
        var renderBefore = renderCount;
        var fakeStub = {
          getModel: function () { return null; },
          removeModel: function (h) { removedB.push(h); },
          render: function () { renderCount += 1; },
        };
        ns._mainViewerImpl = function () { return fakeStub; };
        ns.overlayState.active = true;
        ns.clearOverlay();
        if (removedB.length !== 0) {
          console.error("FAIL: no getModelCount -> removal must be a no-op");
          process.exit(1);
        }
        if (renderCount !== renderBefore + 1) {
          console.error("FAIL: render is still attempted"); process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS)))

    _run_node(script, "_removeOverlayModels token teardown node test")


# ---------------------------------------------------------------------------
# Item 5: renderOverlay — model-A highlight dual path (T8 behavior, covered)
# ---------------------------------------------------------------------------


def test_render_overlay_model_a_highlight_dual_path() -> None:
    """modelB is styled through its addModel token; the max-displacement
    highlight goes to model A's getModel(0) token (instance-level addStyle
    would silently hit model B) with the {index, model: 0} fallback for
    token-less stubs."""
    script = textwrap.dedent("""\
        var window = { fetch: null };
        require(SV_PATH);
        var ns = window.ACPStructureViewer;

        // (a) token path: highlight on modelA.addStyle, viewer.addStyle unused
        var aCalls = [], bCalls = [], vCalls = [];
        var modelA = { addStyle: function (sel, spec) { aCalls.push([sel, spec]); } };
        var modelB = { setStyle: function (sel, spec) { bCalls.push([sel, spec]); } };
        var models = [modelA];
        var fakeToken = {
          getModelCount: function () { return models.length; },
          getModel: function (i) { return models[i]; },
          removeModel: function (h) {
            var k = models.indexOf(h);
            if (k >= 0) models.splice(k, 1);
          },
          addModel: function (text, fmt) { models.push(modelB); return modelB; },
          addStyle: function (sel, spec) { vCalls.push([sel, spec]); },
          render: function () {},
          getView: function () { return [0]; },
          setView: function () {},
        };
        ns._mainViewerImpl = function () { return fakeToken; };
        ns.overlayState.active = true;
        ns.overlayState.xyzB = "2\\nb\\nH 0 0 0\\nH 0 0 1\\n";
        ns.overlayState.data = { max_displacement: { i: 1, j: 1, distance: 0.2 } };
        ns.renderOverlay();
        if (bCalls.length !== 1 || !bCalls[0][1].stick) {
          console.error("FAIL: distinct modelB token style"); process.exit(1);
        }
        if (aCalls.length !== 1 || aCalls[0][0].index !== 1 || !aCalls[0][1].sphere) {
          console.error("FAIL: model-A highlight via its token"); process.exit(1);
        }
        if (vCalls.length !== 0) {
          console.error("FAIL: instance-level addStyle must not run (would hit B)");
          process.exit(1);
        }

        // (b) stub fallback: { index, model: 0 } on the viewer
        var vCalls2 = [];
        var fakeStub = {
          getModelCount: function () { return 1; },
          getModel: function () { return null; },
          removeModel: function () {},
          addModel: function () { return undefined; },
          addStyle: function (sel, spec) { vCalls2.push([sel, spec]); },
          render: function () {},
          getView: function () { return [0]; },
          setView: function () {},
        };
        ns._mainViewerImpl = function () { return fakeStub; };
        ns.renderOverlay();
        if (vCalls2.length !== 1 || vCalls2[0][0].index !== 1 ||
            vCalls2[0][0].model !== 0) {
          console.error("FAIL: fallback {index, model: 0} selection");
          process.exit(1);
        }
        console.log("PASS");
    """).replace("SV_PATH", json.dumps(str(_SV_JS)))

    _run_node(script, "renderOverlay highlight dual-path node test")


# ---------------------------------------------------------------------------
# Item 6: edit push — no camera jump on the STORE-owned coordinates path
# ---------------------------------------------------------------------------


def test_edit_push_bridge_call_and_no_camera_code_in_editor() -> None:
    """_pushModelToViewer keeps the window._svLoadXyzToViewer bridge call
    exactly and never touches the camera itself."""
    se = _SE_JS.read_text(encoding="utf-8")
    push = se.split("function _pushModelToViewer()", 1)[1].split("\n  function ", 1)[0]
    assert "window._svLoadXyzToViewer(" in push, "bridge call must be kept exactly"
    assert "_svLoadXyzToViewer(" in push
    for banned in ("setView", ".center(", "zoomTo(", ".zoom("):
        assert banned not in push, f"editor push path must not call {banned}"


def test_store_coordinates_dispatch_skips_framing() -> None:
    """molecule-view.js store subscription: the coordinates/preview branches
    sync WITHOUT zoomTo; only the load branch reframes. The coordinates
    branch must not introduce any direct camera call either."""
    mv = _MV_JS.read_text(encoding="utf-8")
    start = mv.index("store.subscribe(function(snap, change) {")
    end = mv.index("\n  });", start)
    sub = mv[start:end]

    assert sub.count("_svSyncGeometryToCanvas({ zoomTo: true })") == 1, (
        "only the load branch may reframe"
    )
    coord_branch = sub.split('change === "coordinates"', 1)[1].split("} else if", 1)[0]
    assert "_svSyncGeometryToCanvas()" in coord_branch, (
        "coordinates branch must sync through _svSyncGeometryToCanvas()"
    )
    for banned in ("zoomTo", "setView", ".center(", ".zoom("):
        assert banned not in coord_branch, (
            f"coordinates branch must not call {banned} (camera jump on edit push)"
        )
    body_start = mv.index("function _svSyncGeometryToCanvas(opts) {")
    body_end = mv.index("window._svSyncGeometryToCanvas = _svSyncGeometryToCanvas;")
    body = mv[body_start:body_end]
    assert "if (opts.zoomTo) scheduleViewerFraming(" in body, (
        "reframe must stay gated behind opts.zoomTo"
    )
    assert "setView" not in body and ".center(" not in body and "zoomTo(" not in body, (
        "_svSyncGeometryToCanvas must never invoke setView/center/zoomTo directly"
    )


def test_edit_push_no_camera_jump_on_coordinates_path() -> None:
    """Behavioral no-camera-jump contract (QA failure scenario): a real
    structure_editor edit on the STORE-owned route flows through
    updateCoordinates -> the coordinates dispatch -> the REAL
    _svSyncGeometryToCanvas (eval'd from molecule-view.js), and that path
    must issue ZERO setView/center/zoomTo/zoom calls and must not schedule
    framing. The framing test FAILS if any such call is introduced into the
    coordinates-update path. The non-store legacy route keeps the
    window._svLoadXyzToViewer bridge call."""
    script = textwrap.dedent("""\
        var fs = require("fs");
        var window = { fetch: null };
        require(GS_PATH);
        require(EDITOR_PATH);
        var ed = window.ACPStructureEditor;
        var store = window.ACPGeometryStore;

        // ---- real _svSyncGeometryToCanvas eval'd from molecule-view.js ----
        var src = fs.readFileSync(MV_PATH, "utf8");
        var start = src.indexOf("function _svSyncGeometryToCanvas(");
        var end = src.indexOf("window._svSyncGeometryToCanvas = _svSyncGeometryToCanvas;");
        if (start < 0 || end <= start) {
          console.error("FAIL: cannot extract _svSyncGeometryToCanvas");
          process.exit(1);
        }
        var fnText = src.slice(start, end);

        var cameraCalls = [];
        var modelCalls = [];
        var framed = 0;
        var fakeViewer = {
          setView: function (v) { cameraCalls.push(["setView", v]); },
          center: function () { cameraCalls.push(["center"]); },
          zoomTo: function () { cameraCalls.push(["zoomTo"]); },
          zoom: function () { cameraCalls.push(["zoom"]); },
          removeAllModels: function () { modelCalls.push("removeAllModels"); },
          removeAllShapes: function () { modelCalls.push("removeAllShapes"); },
          removeAllLabels: function () { modelCalls.push("removeAllLabels"); },
          addModel: function (text, fmt) {
            modelCalls.push("addModel");
            return { selectedAtoms: function () { return []; } };
          },
          setBackgroundColor: function () { modelCalls.push("bg"); },
          addStyle: function () { modelCalls.push("addStyle"); },
          render: function () { modelCalls.push("render"); },
        };

        var viewer = fakeViewer;
        var currentModel = null;
        var currentModelAtoms = [];
        var atomsToXYZ = function (atoms) {
          var out = String(atoms.length) + "\\n\\n";
          for (var i = 0; i < atoms.length; i++) {
            out += atoms[i].elem + " " + atoms[i].x + " " + atoms[i].y + " " +
              atoms[i].z + "\\n";
          }
          return out;
        };
        var molDoc = { bgColor: "#000", style: "ball-stick", frames: [{}], currentFrame: 0 };
        var applyStylePreset = function () { modelCalls.push("applyStylePreset"); };
        var getCurrentSphereScale = function () { return 0.3; };
        var setupClickableForCurrentModel = function () { modelCalls.push("clickable"); };
        var _svRenderSelectionOverlay = function () {};
        var _svRenderMeasurementOverlays = function () {};
        var scheduleViewerFraming = function () { framed += 1; };
        var document = { getElementById: function () { return null; } };
        var mainViewerFraming = {};
        eval(fnText);

        // store dispatch mirroring molecule-view.js's subscription mapping
        var pending = [];
        store.subscribe(function (snap, change) {
          if (change === "load") {
            pending.push(function () { _svSyncGeometryToCanvas({ zoomTo: true }); });
          } else if (change === "coordinates") {
            pending.push(function () { _svSyncGeometryToCanvas(); });
          }
        });

        // ---- STORE-owned edit push route ----
        var symbols = ["C", "C"];
        var coords = [[0, 0, 0], [1.5, 0, 0]];
        store.load({ entryId: "e1", symbols: symbols, coordinates: coords });
        window.ACPStructureViewer = {
          state: {
            dirty: false, displayedEntryId: "e1",
            displayedSymbols: symbols.slice(),
            displayedCoords: coords.map(function (r) { return r.slice(); }),
          },
          renderInspector: function () {},
        };
        var bridgeCalls = [];
        window._svLoadXyzToViewer = function (text, entryId) {
          bridgeCalls.push([text, entryId]);
        };

        // flush the load-route sync (allowed to reframe), then reset markers
        pending.splice(0).forEach(function (f) { f(); });
        framed = 0;
        cameraCalls.length = 0;
        modelCalls.length = 0;

        ed.bindEntry("e1", symbols, coords);
        var revBefore = store.state.revision;
        var res = ed.applyBondLengthEdit(0, 1, 1.2, null);
        if (!res.ok) {
          console.error("FAIL: edit rejected " + res.reason); process.exit(1);
        }
        if (bridgeCalls.length !== 0) {
          console.error("FAIL: store-owned route must not reload via the bridge");
          process.exit(1);
        }
        if (store.state.revision !== revBefore + 1) {
          console.error("FAIL: coordinates must flow through updateCoordinates");
          process.exit(1);
        }
        var dispatched = pending.splice(0);
        if (dispatched.length !== 1) {
          console.error("FAIL: exactly one coordinates dispatch");
          process.exit(1);
        }
        dispatched[0]();
        if (cameraCalls.length !== 0) {
          console.error("FAIL: camera jump on coordinates path: " +
            JSON.stringify(cameraCalls));
          process.exit(1);
        }
        if (framed !== 0) {
          console.error("FAIL: coordinates path must not schedule framing");
          process.exit(1);
        }
        if (modelCalls.indexOf("addModel") < 0 || modelCalls.indexOf("render") < 0) {
          console.error("FAIL: model must be rebuilt on the coordinates path");
          process.exit(1);
        }

        // load-route sanity: framing is ALLOWED there, camera still not
        // touched directly (the framing helper owns it)
        _svSyncGeometryToCanvas({ zoomTo: true });
        if (framed !== 1 || cameraCalls.length !== 0) {
          console.error("FAIL: load route reframe contract"); process.exit(1);
        }

        // ---- legacy (non-store) route keeps the bridge call exactly ----
        ed.resetEdits();
        store.reset();
        var symbols2 = ["C", "C"];
        var coords2 = [[0, 0, 0], [1.5, 0, 0]];
        window.ACPStructureViewer.state = {
          dirty: false, displayedEntryId: "legacy1",
          displayedSymbols: symbols2.slice(),
          displayedCoords: coords2.map(function (r) { return r.slice(); }),
        };
        bridgeCalls.length = 0;
        ed.bindEntry("legacy1", symbols2, coords2);
        var res2 = ed.applyBondLengthEdit(0, 1, 1.2, null);
        if (!res2.ok) {
          console.error("FAIL: legacy edit rejected " + res2.reason);
          process.exit(1);
        }
        if (bridgeCalls.length !== 1) {
          console.error("FAIL: bridge must fire exactly once off-store");
          process.exit(1);
        }
        if (bridgeCalls[0][1] !== "legacy1") {
          console.error("FAIL: bridge entry id"); process.exit(1);
        }
        var lines = String(bridgeCalls[0][0]).trim().split("\\n");
        if (lines[0] !== "2" || lines[2].indexOf("C") !== 0) {
          console.error("FAIL: bridge xyz text " + JSON.stringify(lines));
          process.exit(1);
        }
        console.log("PASS");
    """) \
        .replace("GS_PATH", json.dumps(str(_GS_JS))) \
        .replace("EDITOR_PATH", json.dumps(str(_SE_JS))) \
        .replace("MV_PATH", json.dumps(str(_MV_JS)))

    _run_node(script, "edit push no-camera-jump node test")
