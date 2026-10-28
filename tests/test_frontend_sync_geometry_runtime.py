"""Frontend sync page tests — geometry_runtime area.

Split from tests/test_frontend_sync.py (T29, 2026-10-18). Every test keeps
its original name and body; the old -> new mapping is
tests/test_frontend_sync.py::<name> -> tests/test_frontend_sync_geometry_runtime.py
::<name>. Shared helpers live in tests/_frontend_sync_shared.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap

import pytest

from tests._frontend_sync_shared import (
    _EN_BLOCK_RE,
    _SV_JS_PATH,
    _VIB_JS,
    _ZH_BLOCK_RE,
    FRONTEND_CSS_DIR,
    FRONTEND_JS_DIR,
    _extract_structure_keys,
    read_workbench_content,
    viewer_creation_site_count,
)

# ---------------------------------------------------------------------------
# Wave 7 / todo 38: shared geometry loader + style/camera store (phase D)
# ---------------------------------------------------------------------------


def test_shared_geometry_loader_contract() -> None:
    """Phase-D lock: ONE shared loader; energy viewer keeps its canvas but
    loses its duplicate parse/style/load logic; no new viewer instances;
    reaction/preview/s2scan viewers untouched."""
    html = read_workbench_content()
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    for name in (
        "geometryStore",
        "sharedLoadGeometry",
        "registerCanvasLoader",
        "saveCamera",
        "restoreCamera",
        "setStylePreset",
        "getStylePreset",
        "_resolveStyleSpec",
    ):
        assert f"{name}: {name}," in sv, f"{name} missing from structure_viewer.js namespace"

    assert 'sharedLoadGeometry(xyzText, { canvasId: "main"' in sv, (
        "loadSelectedGeometry/injectManualEntry must route through the shared loader"
    )

    load_fn = sv.split("function loadSelectedGeometry()", 1)[1].split("\n  function ", 1)[0]
    assert 'sharedLoadGeometry(xyzText, { canvasId: "main"' in load_fn

    # Energy region: the delegation block + energyGraphLoadFrameGeometry
    region = html.split("Shared-loader delegation", 1)[1].split("Fetch Frame XYZ", 1)[0]
    assert 'registerCanvasLoader("energy-mini"' in region
    assert "sphere: { scale: 0.26 }" not in region, (
        "energy region must not re-declare its old hardcoded style"
    )

    frame_fn = html.split("async function energyGraphLoadFrameGeometry", 1)[1]
    frame_fn = frame_fn.split("Fetch Frame XYZ", 1)[0]
    assert "sharedLoadGeometry" in frame_fn and '"energy-mini"' in frame_fn
    for gone in ("addModel", "setStyle", "createViewer"):
        assert gone not in frame_fn, (
            f"energyGraphLoadFrameGeometry must not contain {gone} — "
            "loading is delegated to the shared loader"
        )

    # The energy mini canvas keeps its own element + instance lifecycle
    assert 'id="energy-structure-viewer"' in html
    assert "function energyGraphDestroyViewer()" in html

    # Viewer-instance budget pin: the only instance beyond the Phase-D set is
    # the NMR candidate structure canvas, which must route through the shared
    # loader (registerCanvasLoader/sharedLoadGeometry + scheduleViewerFraming).
    # Adding another instance requires a deliberate update here.
    # FE-04 slice 1: legacy S2 modal viewers (s2scanState.viewer/resultViewer)
    # removed with the dead s2scan panels — budget dropped 8 -> 5.
    # T9: energy preview migrated to the bounded scene instance — 5 -> 4.
    # T8/T12: main-structure + NMR canvases migrated — 4 -> 2 (wizard +
    # reaction-builder remain for T10/T11).
    # T11: reaction-builder migrated — 2 -> 1 (wizard remains for T10).
    # T10: wizard preview migrated — 1 -> 0 (creation lives only in
    # scene_instance.js via the adapter; features/ never create viewers).
    assert viewer_creation_site_count() == 0, "viewer instance count changed"

    # reaction viewer (T11) and wizard preview (T10) creation both go through
    # the bounded scene instance; creation options stay pinned by
    # module_tests/reaction_viewer.test.js and module_tests/wizard_preview_viewer.test.js.
    assert "reactionViewer = ACPVisualization.scene.create(" in html
    assert "previewViewer = ACPVisualization.scene.create(" in html
    # 2 = preview + PES selection renderer resets; the 3rd is the scan
    # selection renderer's identical per-render reset (WS3, deliberate).
    assert html.count("previewViewer.setStyle") == 3, "preview viewer styling must stay untouched"


def test_shared_geometry_loader_node_logic() -> None:
    """Node logic: sharedLoadGeometry dispatches per canvas id, applies the
    store's style preset, saves/restores cameras per canvas id, and updates
    displayedCoords only on the main canvas."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var mainLoads = [];
        window._svLoadXyzToViewer = function (text, entryId) { mainLoads.push([text, entryId]); };

        var miniCalls = [];
        ns.registerCanvasLoader("energy-mini", function (text, spec) {
          miniCalls.push([text, spec]);
          return true;
        });

        var marker = { marker: true };
        ns._styleSpecImpl = function (name) { return name === "test-preset" ? marker : null; };
        ns.setStylePreset("test-preset");
        if (ns.getStylePreset() !== "test-preset") {
          console.error("FAIL: preset store"); process.exit(1);
        }
        if (ns.geometryStore.stylePreset !== "test-preset") {
          console.error("FAIL: store field"); process.exit(1);
        }
        if (ns.geometryStore.loaderVersion !== 0) {
          console.error("FAIL: initial loaderVersion"); process.exit(1);
        }

        var xyzMini = "2\\nmini\\nC 0 0 0\\nH 1 0 0\\n";
        var r1 = ns.sharedLoadGeometry(xyzMini, {
          canvasId: "energy-mini", source: "energy_graph"
        });
        if (!r1 || !r1.loaded) { console.error("FAIL: mini load result"); process.exit(1); }
        if (miniCalls.length !== 1 || miniCalls[0][0] !== xyzMini || miniCalls[0][1] !== marker) {
          console.error("FAIL: mini loader call or style spec from store"); process.exit(1);
        }
        if (mainLoads.length !== 0) {
          console.error("FAIL: main bridge must not fire for mini"); process.exit(1);
        }
        if (ns.state.displayedCoords !== null) {
          console.error("FAIL: displayedCoords must stay null for mini"); process.exit(1);
        }
        if (ns.geometryStore.currentXyz !== xyzMini || ns.geometryStore.loaderVersion !== 1) {
          console.error("FAIL: store tracking"); process.exit(1);
        }

        var xyzMain = "1\\nmain\\nO 0 0 0\\n";
        ns.state.selectedEntryId = "conf_0001";
        var r2 = ns.sharedLoadGeometry(xyzMain, { canvasId: "main", source: "structure_list" });
        if (!r2 || !r2.loaded || mainLoads.length !== 1 || mainLoads[0][1] !== "conf_0001") {
          console.error("FAIL: main load via bridge"); process.exit(1);
        }
        if (!r2.parsed || r2.parsed.symbols[0] !== "O") {
          console.error("FAIL: main parsed"); process.exit(1);
        }
        if (!ns.state.displayedCoords || ns.state.displayedCoords.length !== 1) {
          console.error("FAIL: displayedCoords on main"); process.exit(1);
        }
        if (ns.state.displayedEntryId !== "conf_0001") {
          console.error("FAIL: displayedEntryId"); process.exit(1);
        }
        if (ns.geometryStore.loaderVersion !== 2) {
          console.error("FAIL: version bump"); process.exit(1);
        }

        if (ns.sharedLoadGeometry("", { canvasId: "main" }) !== null ||
            ns.sharedLoadGeometry("1\\nx\\nH 0 0 0\\n", { canvasId: "nope" }) !== null) {
          console.error("FAIL: empty/unknown canvas must return null"); process.exit(1);
        }

        var setViews = [];
        var fakeViewer = {
          getView: function () { return [1, 2, 3, 4]; },
          setView: function (v) { setViews.push(v); },
          render: function () {},
        };
        ns._canvasViewerImpl = function (id) { return id === "energy-mini" ? fakeViewer : null; };
        if (ns.saveCamera("energy-mini") !== true) {
          console.error("FAIL: saveCamera"); process.exit(1);
        }
        if (ns.restoreCamera("energy-mini") !== true) {
          console.error("FAIL: restoreCamera"); process.exit(1);
        }
        if (setViews.length !== 1 || setViews[0].join(",") !== "1,2,3,4") {
          console.error("FAIL: camera roundtrip"); process.exit(1);
        }
        if (ns.saveCamera("main") !== false || ns.restoreCamera("ghost") !== false) {
          console.error("FAIL: missing-viewer guards"); process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node shared-loader test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_shared_loader_selection_token_stale_guard() -> None:
    """Regression (todos 15/18): after the shared-loader refactor the single
    selectionToken still drops stale geometry loads on both sides."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;
        var loads = [];
        window._svLoadXyzToViewer = function (text, entryId) { loads.push(entryId); };

        function mkEntry(id) {
          return { id: id, geometry: { endpoint: "/geo/" + id, format: "xyz" },
                   badges: [], vibrations: { available: false } };
        }
        ns.state.jobId = "job-1";
        ns.state.payload = { entries: [mkEntry("e1"), mkEntry("e2")], default_entry_id: "e1" };

        var pending = [];
        ns._fetchImpl = function (url) {
          return new Promise(function (resolve) {
            pending.push({ url: url, go: function () {
              resolve({ ok: true, text: function () { return "1\\nt\\nH 0 0 0\\n"; } });
            } });
          });
        };

        var t1 = ns.selectEntry("e1", "user");
        var t2 = ns.selectEntry("e2", "user");
        if (t2 !== t1 + 1) { console.error("FAIL: token monotonic"); process.exit(1); }
        if (pending.length !== 2) { console.error("FAIL: two fetches"); process.exit(1); }

        pending[0].go();
        setTimeout(function () {
          if (loads.length !== 0) { console.error("FAIL: stale load leaked"); process.exit(1); }
          pending[1].go();
          setTimeout(function () {
            if (loads.length !== 1 || loads[0] !== "e2") {
              console.error("FAIL: fresh load missing"); process.exit(1);
            }
            if (ns.state.displayedEntryId !== "e2" || !ns.state.displayedCoords) {
              console.error("FAIL: shared-loader displayed state"); process.exit(1);
            }
            console.log("PASS");
          }, 0);
        }, 0);
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node stale-token guard test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 7 / todo 39: IRC frame playback + projection contract
# ---------------------------------------------------------------------------


def test_irc_playback_contract() -> None:
    """Todo-39 lock: playIrcPath/stopIrcPlayback exist, teardown stops playback,
    playback goes through the shared loader (main-irc adapter), buttons +
    i18n present in both locales."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in ("playIrcPath", "stopIrcPlayback", "isIrcPlaying"):
        assert f"{name}: {name}," in sv, f"{name} missing from namespace"

    # Playback loads through the SHARED loader via the main-irc adapter
    assert 'registerCanvasLoader("main-irc"' in sv
    assert 'canvasId: "main-irc"' in sv
    assert 'source: "irc_playback"' in sv
    # Camera preservation recipe inside the adapter (todo-29 pattern)
    adapter = sv.split('registerCanvasLoader("main-irc"', 1)[1].split("});", 1)[0]
    for piece in ("getView", "removeAllModels", "addModel", "setView", "render"):
        assert piece in adapter

    # Teardown wiring: job switch + entry switch stop playback; per-step
    # self-stop when the structure tab is hidden
    on_job = sv.split("function onJobSelected(", 1)[1].split("\n  function ", 1)[0]
    assert "stopIrcPlayback()" in on_job
    select_fn = sv.split("function selectEntry(", 1)[1].split("\n  function ", 1)[0]
    assert "stopIrcPlayback()" in select_fn
    step_fn = sv.split("function _ircStep()", 1)[1].split("\n  function ", 1)[0]
    assert "sv-layout" in step_fn

    # Playback bar: renders into the todo-16 container with both buttons + stop
    assert 'getElementById("structure-playback-bar")' in sv
    assert "structure.irc.play_forward" in sv and "structure.irc.play_reverse" in sv
    assert "structure.irc.stop" in sv
    assert 'id="structure-playback-bar"' in html

    # i18n completeness: all 6 structure.irc.* keys in BOTH locales
    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)
    expected = {
        "structure.irc.title",
        "structure.irc.play_forward",
        "structure.irc.play_reverse",
        "structure.irc.stop",
        "structure.irc.playing",
        "structure.irc.loading",
    }
    assert expected <= zh_keys, f"missing zh keys: {sorted(expected - zh_keys)}"
    assert expected <= en_keys, f"missing en keys: {sorted(expected - en_keys)}"


def test_irc_playback_node_logic() -> None:
    """Node logic: playback steps frames in FILE ORDER through the shared
    loader, camera is preserved per frame, stop is idempotent, and selection
    changes stop playback."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var viewerCalls = [];
        var viewSeq = 0;
        var currentView = null;
        var fakeViewer = {
          getView: function () { currentView = { seq: ++viewSeq }; return currentView; },
          setView: function (v) { viewerCalls.push(["setView", v]); },
          removeAllModels: function () { viewerCalls.push(["removeAllModels"]); },
          addModel: function (text, fmt) { viewerCalls.push(["addModel", text]); },
          setStyle: function (sel, spec) { viewerCalls.push(["setStyle", spec]); },
          render: function () { viewerCalls.push(["render"]); },
        };
        ns._mainViewerImpl = function () { return fakeViewer; };
        ns._styleSpecImpl = function (name) { return name === "p1" ? { marker: 1 } : null; };
        ns.setStylePreset("p1");

        function ircEntry(direction, i) {
          return {
            id: "irc_" + direction + "_" + i,
            group_id: "irc_" + direction,
            label: "f" + i,
            geometry: { endpoint: "/api/f/" + direction + "/" + i, format: "xyz" },
            source: { kind: "formal_result", frame_index: i, geometry_ref: "x" },
            badges: [], vibrations: { available: false },
          };
        }
        ns.state.jobId = "job-irc";
        ns.state.payload = {
          entries: [
            ircEntry("forward", 1), ircEntry("forward", 0), ircEntry("forward", 2),
            ircEntry("reverse", 0),
            { id: "other", group_id: "g", geometry: { endpoint: "/o" }, badges: [] },
          ],
          default_entry_id: "irc_forward_0",
          groups: [{ id: "irc_forward", label: "F", kind: "irc" }],
        };
        ns._fetchImpl = function (url) {
          return Promise.resolve({ ok: true, text: function () {
            return "1\\nf\\nH 0 0 " + url.slice(-1) + "\\n";
          } });
        };

        var intervals = [];
        ns._setIntervalImpl = function (fn, ms) { intervals.push(fn); return intervals.length; };
        var cleared = [];
        ns._clearIntervalImpl = function (h) { cleared.push(h); };

        if (ns.playIrcPath("sideways") !== false) {
          console.error("FAIL: invalid direction must return false"); process.exit(1);
        }
        if (ns.playIrcPath("forward") !== true) {
          console.error("FAIL: play forward"); process.exit(1);
        }
        if (!ns.isIrcPlaying()) { console.error("FAIL: playing flag"); process.exit(1); }

        setTimeout(function () {
          if (!intervals.length) { console.error("FAIL: interval scheduled"); process.exit(1); }
          var step = intervals[0];
          var models = function () {
            return viewerCalls.filter(function (c) { return c[0] === "addModel"; })
              .map(function (c) { return c[1]; });
          };
          // frame 0 stepped immediately after prefetch
          if (models().length !== 1) { console.error("FAIL: first frame"); process.exit(1); }
          step(); step();
          if (models().length !== 3) { console.error("FAIL: stepped 3 frames"); process.exit(1); }
          // FILE ORDER by frame_index (0,1,2) — payload listed them shuffled
          var last = models()[2].trim().split("\\n");
          if (last[2].indexOf("2") < 0) {
            console.error("FAIL: order by frame_index"); process.exit(1);
          }
          // end of path auto-stops
          step();
          if (ns.isIrcPlaying()) { console.error("FAIL: auto-stop at end"); process.exit(1); }
          var versAtStop = ns.geometryStore.loaderVersion;

          // camera preserved: every setView got the view from the SAME step
          var views = viewerCalls.filter(function (c) { return c[0] === "setView"; });
          if (views.length !== 3 || views.some(function (v) { return !v[1] || !v[1].seq; })) {
            console.error("FAIL: camera setView per frame"); process.exit(1);
          }
          var styles = viewerCalls.filter(function (c) { return c[0] === "setStyle"; });
          if (styles.length !== 3 || styles[0][1].marker !== 1) {
            console.error("FAIL: shared style preset applied per frame"); process.exit(1);
          }

          // idempotent stop — no throw, no extra clearInterval
          ns.stopIrcPlayback(); ns.stopIrcPlayback();

          // selection change stops an active playback
          ns._fetchImpl = function () {
            return Promise.resolve({ ok: false, text: function () { return ""; } });
          };
          ns.playIrcPath("reverse");
          ns.selectEntry("other", "user");
          if (ns.isIrcPlaying()) {
            console.error("FAIL: selection must stop playback"); process.exit(1);
          }
          if (ns.geometryStore.loaderVersion !== versAtStop) {
            console.error("FAIL: no loads after stop"); process.exit(1);
          }
          console.log("PASS");
        }, 0);
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node IRC playback test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 7 / todo 40: structure overlay + RMSD
# ---------------------------------------------------------------------------


def test_overlay_contract() -> None:
    """Todo-40 lock: overlay API surface, distinct second-model style, teardown
    clears, unproven-mapping note, i18n both locales."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in ("loadOverlay", "renderOverlay", "clearOverlay", "overlayMeasurementsBlocked"):
        assert f"{name}: {name}," in sv, f"{name} missing from namespace"
    assert "overlayState: overlayState," in sv

    # Second model gets a DISTINCT style on the EXISTING viewer (no new viewer)
    assert "OVERLAY_STYLE_B" in sv
    assert "cyanCarbon" in sv
    assert "addModel" in sv.split("function renderOverlay()", 1)[1].split("\n  function ", 1)[0]

    # Overlay extras are removed without touching model A / the viewer
    assert "_removeOverlayModels" in sv
    assert "getModelCount" in sv and "removeModel" in sv

    # Max-displacement atom highlight mirrors applySelectionHighlight's pattern
    overlay_fn = sv.split("function renderOverlay()", 1)[1].split("\n  function ", 1)[0]
    assert "addStyle" in overlay_fn and "sphere" in overlay_fn

    # Teardown: entry switch, job switch, and IRC playback all clear the overlay
    select_fn = sv.split("function selectEntry(", 1)[1].split("\n  function ", 1)[0]
    assert "clearOverlay()" in select_fn
    on_job = sv.split("function onJobSelected(", 1)[1].split("\n  function ", 1)[0]
    assert "clearOverlay()" in on_job
    play_fn = sv.split("function playIrcPath(", 1)[1].split("\n  function ", 1)[0]
    assert "clearOverlay()" in play_fn

    # Unproven mapping: measurements section blocked + note (never kept)
    assert "sv-measurements-blocked" in sv
    assert "structure.overlay.unproven" in sv
    blocked_fn = sv.split("function overlayMeasurementsBlocked()", 1)[1]
    blocked_fn = blocked_fn.split("\n  function ", 1)[0]
    assert '"unproven"' in blocked_fn

    # List rows gain the overlay action (does NOT change selection)
    assert "sv-overlay-btn" in sv
    assert "stopPropagation" in sv

    # i18n completeness: all 8 structure.overlay.* keys in BOTH locales
    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)
    expected = {
        "structure.overlay.title",
        "structure.overlay.rmsd",
        "structure.overlay.mapped_count",
        "structure.overlay.source_identity",
        "structure.overlay.source_mcs",
        "structure.overlay.unproven",
        "structure.overlay.clear",
        "structure.overlay.max_atom",
    }
    assert expected <= zh_keys, f"missing zh keys: {sorted(expected - zh_keys)}"
    assert expected <= en_keys, f"missing en keys: {sorted(expected - en_keys)}"


def test_overlay_node_logic() -> None:
    """Node logic: overlay state transitions, second model styled distinctly,
    unproven mapping blocks measurements, clear/entry-switch teardown."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var viewerCalls = [];
        var models = [];
        var count = 1;
        var fakeViewer = {
          getModelCount: function () { return count; },
          getModel: function (i) { return models[i]; },
          removeModel: function (m) {
            viewerCalls.push(["removeModel"]);
            count -= 1;
          },
          addModel: function (text, fmt) {
            viewerCalls.push(["addModel", text]);
            count += 1;
            var rec = { styledWith: null, setStyle: function (sel, spec) {
              viewerCalls.push(["modelStyle", spec]);
              rec.styledWith = spec;
            } };
            models.push(rec);
            return rec;
          },
          addStyle: function (sel, spec) { viewerCalls.push(["addStyle", sel, spec]); },
          render: function () { viewerCalls.push(["render"]); },
          getView: function () { return [0]; },
          setView: function () {},
        };
        ns._mainViewerImpl = function () { return fakeViewer; };

        ns.state.jobId = "job-ov";
        ns.state.selectedEntryId = "conf_0001";
        ns.state.payload = {
          entries: [
            { id: "conf_0001", group_id: "g", geometry: { endpoint: "/geo/1" },
              source: {}, badges: [], vibrations: { available: false } },
            { id: "conf_0002", group_id: "g", geometry: { endpoint: "/geo/2" },
              source: {}, badges: [], vibrations: { available: false } },
          ],
          default_entry_id: "conf_0001",
          groups: [],
        };

        var overlayPayload = null;
        ns._fetchImpl = function (url) {
          if (url.indexOf("overlay?") >= 0) {
            return Promise.resolve({ ok: true, json: function () { return overlayPayload; } });
          }
          return Promise.resolve({ ok: true, text: function () {
            return "2\\nb\\nH 0 0 0\\nH 0 0 1\\n";
          } });
        };

        overlayPayload = {
          ok: true, reason: "identity", rmsd: 0.123,
          mapping: [[0, 0], [1, 1]], n_mapped: 2,
          max_displacement: { i: 1, j: 1, distance: 0.2 },
        };

        ns.loadOverlay("conf_0001", "conf_0001").then(function (sameFlag) {
          if (sameFlag !== false) {
            console.error("FAIL: same-id guard"); process.exit(1);
          }
          return ns.loadOverlay("conf_0001", "conf_0002");
        }).then(function (okFlag) {
          if (!okFlag) {
            console.error("FAIL: loadOverlay ok"); process.exit(1);
          }
          if (!ns.overlayState.active) {
            console.error("FAIL: active"); process.exit(1);
          }
          if (ns.overlayState.data.reason !== "identity") {
            console.error("FAIL: data"); process.exit(1);
          }
          if (ns.overlayMeasurementsBlocked()) {
            console.error("FAIL: proven must not block"); process.exit(1);
          }
          var adds = viewerCalls.filter(function (c) { return c[0] === "addModel"; });
          if (adds.length !== 1) {
            console.error("FAIL: second model added once"); process.exit(1);
          }
          var styleCalls = viewerCalls.filter(function (c) { return c[0] === "modelStyle"; });
          if (styleCalls.length !== 1 || !styleCalls[0][1].stick.colorscheme) {
            console.error("FAIL: distinct second-model style"); process.exit(1);
          }
          var highlights = viewerCalls.filter(function (c) { return c[0] === "addStyle"; });
          if (highlights.length !== 1 || highlights[0][1].index !== 1) {
            console.error("FAIL: max-displacement highlight"); process.exit(1);
          }

          // clearOverlay removes the extra model and resets state
          ns.clearOverlay();
          if (ns.overlayState.active || ns.overlayMeasurementsBlocked()) {
            console.error("FAIL: clear state"); process.exit(1);
          }
          if (!viewerCalls.some(function (c) { return c[0] === "removeModel"; })) {
            console.error("FAIL: removeModel on clear"); process.exit(1);
          }
          ns.clearOverlay();

          // unproven mapping -> measurements blocked
          overlayPayload = { ok: true, reason: "unproven", mapping: null,
                             rmsd: null, n_mapped: 0, max_displacement: null };
          ns.loadOverlay("conf_0001", "conf_0002").then(function (ok2) {
            if (!ok2) { console.error("FAIL: unproven load ok"); process.exit(1); }
            if (!ns.overlayMeasurementsBlocked()) {
              console.error("FAIL: unproven must block measurements"); process.exit(1);
            }
            // entry switch tears the overlay down (stale-selection guard path)
            ns.selectEntry("conf_0002", "user");
            if (ns.overlayState.active) {
              console.error("FAIL: entry switch must clear overlay"); process.exit(1);
            }
            if (ns.overlayMeasurementsBlocked()) {
              console.error("FAIL: blocked flag after clear"); process.exit(1);
            }
            console.log("PASS");
          });
        }).catch(function (e) {
          console.error("FAIL: unexpected", e); process.exit(1);
        });
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node overlay test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# Wave 7 / todo 41: performance thresholds + degradation
# ---------------------------------------------------------------------------


def test_perf_threshold_contract() -> None:
    """Todo-41 lock: the four named thresholds with exact values, degrade
    branch in the shared loader, sampling in playback/list, notice keys."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name, value in (
        ("LIST_VIRTUALIZE_THRESHOLD", "100"),
        ("TRAJECTORY_SAMPLE_THRESHOLD", "500"),
        ("TRAJECTORY_SAMPLE_TARGET", "200"),
        ("LARGE_SYSTEM_ATOM_THRESHOLD", "200"),
    ):
        assert f"{name}: {name}," in sv, f"{name} missing from namespace"
        assert f"var {name} = {value};" in sv, f"{name} must be exactly {value}"

    # Large-system branch lives INSIDE the shared loader
    loader = sv.split("function sharedLoadGeometry(", 1)[1].split("\n  function ", 1)[0]
    assert "LARGE_SYSTEM_ATOM_THRESHOLD" in loader
    assert '"wireframe"' in loader
    assert "userPresetChosen" in loader
    assert "lastLoadDegraded" in loader
    # Explicit preset choice is honored (never degraded)
    preset_fn = sv.split("function setStylePreset(", 1)[1].split("\n  function ", 1)[0]
    assert "userPresetChosen = true" in preset_fn

    # Virtualization + sampling wired into list rendering and IRC playback
    list_fn = sv.split("function renderStructureViewer()", 1)[1].split("\n  function ", 1)[0]
    assert "virtualizeEntries" in list_fn and "LIST_VIRTUALIZE_THRESHOLD" in list_fn
    assert "structure.perf.partial_list" in list_fn
    assert "sampleFrames" in list_fn and "structure.perf.sampled" in list_fn
    play_fn = sv.split("function playIrcPath(", 1)[1].split("\n  function ", 1)[0]
    assert "sampleFrames" in play_fn and "TRAJECTORY_SAMPLE_THRESHOLD" in play_fn

    # Visible degradation notice + marker styles
    assert "sv-notice-degrade" in sv
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    assert "sv-notice-degrade" in css and "sv-list-marker" in css

    # Three notice keys in BOTH locales
    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)
    expected = {
        "structure.perf.degrade_notice",
        "structure.perf.partial_list",
        "structure.perf.sampled",
    }
    assert expected <= zh_keys, f"missing zh keys: {sorted(expected - zh_keys)}"
    assert expected <= en_keys, f"missing en keys: {sorted(expected - en_keys)}"


def test_perf_lazy_geometry_contract() -> None:
    """Lazy geometry (todos 15/17): the catalog fetch carries NO geometry
    bytes — geometry text only enters the store on explicit selection loads."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var catalog = {
          schema_version: "structure_viewer_v1", job_id: "j", workflow: "scan",
          availability: "ready", revision: "r1", default_entry_id: "scan_frame_0",
          groups: [], warnings: [],
          entries: [
            { id: "scan_frame_0", group_id: "", label: "f0", role: "minimum",
              status: "completed",
              geometry: { endpoint: "/geo/0", format: "xyz" },
              source: { kind: "formal_result", frame_index: 0, geometry_ref: "x" },
              badges: [], vibrations: { available: false } },
          ],
        };
        ns._fetchImpl = function () {
          return Promise.resolve({ ok: true, json: function () { return catalog; } });
        };
        ns.loadStructureViewer("j").then(function () {
          var e = ns.state.payload.entries[0];
          if (!e.geometry || !e.geometry.endpoint || e.geometry.endpoint.indexOf("/geo/") < 0) {
            console.error("FAIL: endpoint missing"); process.exit(1);
          }
          for (var key in e) {
            if (/xyz|content|text|coords/i.test(key)) {
              console.error("FAIL: geometry bytes in catalog: " + key); process.exit(1);
            }
          }
          for (var gk in e.geometry) {
            if (/xyz|content|text/i.test(gk)) {
              console.error("FAIL: geometry field carries bytes: " + gk); process.exit(1);
            }
          }
          if (ns.geometryStore.currentXyz !== null) {
            console.error("FAIL: store must stay empty before selection load"); process.exit(1);
          }
          console.log("PASS");
        });
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node lazy-geometry test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_perf_virtualize_and_sample_logic() -> None:
    """Node logic: virtualizeEntries windows only above threshold with
    forced ids included; sampleFrames is even, first+last, bounded by target
    and returns the SAME reference at or below threshold."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        function mk(n) {
          var out = [];
          for (var i = 0; i < n; i++) out.push({ id: "e" + i });
          return out;
        }

        var small = ns.virtualizeEntries(mk(99), ns.LIST_VIRTUALIZE_THRESHOLD);
        if (small.windowed !== false || small.total !== 99 || small.visible.length !== 99) {
          console.error("FAIL: 99 entries must render fully"); process.exit(1);
        }

        var big = ns.virtualizeEntries(mk(150), ns.LIST_VIRTUALIZE_THRESHOLD, ["e120", "e75"]);
        if (big.windowed !== true || big.total !== 150) {
          console.error("FAIL: 150 must window"); process.exit(1);
        }
        if (big.visible.length >= big.total) {
          console.error("FAIL: visible must be < total"); process.exit(1);
        }
        var ids = {};
        big.visible.forEach(function (e) { ids[e.id] = true; });
        if (!ids.e0 || !ids.e149) { console.error("FAIL: head+tail edges"); process.exit(1); }
        if (!ids.e120 || !ids.e75) { console.error("FAIL: forced ids included"); process.exit(1); }
        // head(0-49) + tail(100-149) covers e120; only e75 is force-appended
        if (big.visible.length !== 50 + 1 + 50) {
          console.error("FAIL: marker/window count " + big.visible.length); process.exit(1);
        }

        var mid = ns.virtualizeEntries(mk(150), ns.LIST_VIRTUALIZE_THRESHOLD, ["e10"]);
        if (mid.visible.length !== 100) {
          console.error("FAIL: in-window forced id must not duplicate"); process.exit(1);
        }

        var frames600 = [];
        for (var f = 0; f < 600; f++) frames600.push("f" + f);
        var s600 = ns.sampleFrames(
          frames600, ns.TRAJECTORY_SAMPLE_THRESHOLD, ns.TRAJECTORY_SAMPLE_TARGET
        );
        if (s600 === frames600 || s600.length > ns.TRAJECTORY_SAMPLE_TARGET) {
          console.error("FAIL: 600 must sample to <= 200"); process.exit(1);
        }
        if (s600[0] !== "f0" || s600[s600.length - 1] !== "f599") {
          console.error("FAIL: first+last kept"); process.exit(1);
        }
        if (s600.length < 190) { console.error("FAIL: ~200 expected"); process.exit(1); }

        var frames501 = [];
        for (var f2 = 0; f2 < 501; f2++) frames501.push("g" + f2);
        var s501 = ns.sampleFrames(
          frames501, ns.TRAJECTORY_SAMPLE_THRESHOLD, ns.TRAJECTORY_SAMPLE_TARGET
        );
        if (s501[0] !== "g0" || s501[s501.length - 1] !== "g500" || s501.length > 200) {
          console.error("FAIL: 501 sampling"); process.exit(1);
        }

        var frames400 = [];
        for (var f3 = 0; f3 < 400; f3++) frames400.push("h" + f3);
        if (ns.sampleFrames(frames400, ns.TRAJECTORY_SAMPLE_THRESHOLD, 200) !== frames400) {
          console.error("FAIL: 400 must be returned unchanged (same ref)"); process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node virtualize/sample test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_perf_large_system_loader_logic() -> None:
    """Node logic: >200-atom loads degrade to wireframe (per-load, notice
    flag); <=200 loads keep the default preset byte-identically; an explicit
    user preset survives even for large systems."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var specs = [];
        ns._styleSpecImpl = function (name) {
          return name === "wireframe" ? { wire: true } : { preset: name };
        };
        ns.registerCanvasLoader("probe", function (text, spec) {
          specs.push(spec);
          return true;
        });

        function bigXyz(n) {
          var lines = [String(n), "big"];
          for (var i = 0; i < n; i++) lines.push("H 0 0 " + i);
          return lines.join("\\n") + "\\n";
        }

        // 250 atoms -> wireframe spec + degraded flag
        ns.sharedLoadGeometry(bigXyz(250), { canvasId: "probe", source: "test" });
        if (!specs.length || !specs[0].wire) {
          console.error("FAIL: 250 atoms must load wireframe spec"); process.exit(1);
        }
        if (ns.geometryStore.lastLoadDegraded !== true) {
          console.error("FAIL: degrade flag"); process.exit(1);
        }
        if (ns.geometryStore.stylePreset !== "ball-stick") {
          console.error("FAIL: degrade must not persist as global preset"); process.exit(1);
        }

        // 50 atoms -> default preset spec, NOT degraded (byte-identical path)
        specs.length = 0;
        ns.sharedLoadGeometry(bigXyz(50), { canvasId: "probe", source: "test" });
        if (!specs[0] || specs[0].preset !== "ball-stick" || specs[0].wire) {
          console.error("FAIL: 50 atoms keep default preset"); process.exit(1);
        }
        if (ns.geometryStore.lastLoadDegraded !== false) {
          console.error("FAIL: small load must clear degrade"); process.exit(1);
        }

        // explicit user preset survives even for large systems
        ns.setStylePreset("publication");
        if (ns.geometryStore.userPresetChosen !== true) {
          console.error("FAIL: userPresetChosen flag"); process.exit(1);
        }
        specs.length = 0;
        ns.sharedLoadGeometry(bigXyz(250), { canvasId: "probe", source: "test" });
        if (specs[0].preset !== "publication" || specs[0].wire) {
          console.error("FAIL: explicit preset must survive large system"); process.exit(1);
        }
        if (ns.geometryStore.lastLoadDegraded !== false) {
          console.error("FAIL: user choice means no degrade"); process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node large-system test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_view_state_contract() -> None:
    """Todo-42 lock: viewStateStore API + namespace + version, save/restore
    wiring, listbox ARIA + keyboard, aria-labels/live, storage NEVER touches
    result data."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")
    html = read_workbench_content()

    for name in ("viewStateStore", "saveViewState", "restoreViewState", "clearViewState"):
        assert f"{name}: {name}," in sv, f"{name} missing from namespace"
    assert 'NS: "acp.sv.view."' in sv
    assert "VERSION: 1" in sv and "version: viewStateStore.VERSION" in sv

    # wiring: save on entry switch + job teardown, restore after geometry load
    select_fn = sv.split("function selectEntry(", 1)[1].split("\n  function ", 1)[0]
    assert "saveViewState()" in select_fn
    on_job = sv.split("function onJobSelected(", 1)[1].split("\n  function ", 1)[0]
    assert "saveViewState()" in on_job
    load_fn = sv.split("function loadSelectedGeometry()", 1)[1].split("\n  function ", 1)[0]
    assert "restoreViewState(" in load_fn

    # storage ONLY for view state — never results/manifests, namespace-prefixed
    assert "result_manifest" not in sv
    assert sv.count("localStorage") <= 2  # guarded default only
    assert "storage.setItem" in sv  # all writes go through _storageSet

    # a11y: listbox pattern + scoped keyboard handler
    assert 'setAttribute("role", "listbox")' in sv
    assert 'setAttribute("role", "option")' in sv
    assert '"aria-selected"' in sv
    assert '"aria-activedescendant"' in sv
    assert '"ArrowDown"' in sv and '"ArrowUp"' in sv and '"Enter"' in sv
    assert 'listBody.addEventListener("keydown"' in sv
    assert "window.addEventListener" not in sv
    dock_drag_uses = sv.count("document.addEventListener")
    assert dock_drag_uses <= 2, (
        "document.addEventListener allowed only for dock drag (mousemove/mouseup)"
    )

    # a11y: inspector groups + button labels + live notice + real buttons
    assert "_ariaGroup" in sv and 'setAttribute("aria-label"' in sv
    assert '"aria-live"' in sv and '"polite"' in sv
    vib = _VIB_JS.read_text(encoding="utf-8")
    assert 'createElement("button")' in vib
    css = (FRONTEND_CSS_DIR / "structure_viewer.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css and ".sv-option-focus" in css
    assert 'id="structure-playback-bar" role="toolbar"' in html

    # restored measurements record + its i18n key in both locales
    assert "restoredMeasurements" in sv
    assert "structure.view.restored_measurements" in sv
    zh_keys = _extract_structure_keys(html, _ZH_BLOCK_RE)
    en_keys = _extract_structure_keys(html, _EN_BLOCK_RE)
    assert "structure.view.restored_measurements" in zh_keys
    assert "structure.view.restored_measurements" in en_keys


def test_view_state_node_logic() -> None:
    """Node logic with fake storage: save->restore round-trip (camera +
    preset + measurements + atomCount guard), corrupted/missing -> defaults,
    version guard, clear works."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = { fetch: null };
        var AbortController = class { constructor() { this.signal = null; } abort() {} };
        var molDoc = { measures: [{ type: "distance", atoms: [1, 2], value: 1.5 }] };
        require(JS_PATH);
        var ns = window.ACPStructureViewer;

        var storageData = {};
        ns._storageImpl = {
          getItem: function (k) { return (k in storageData) ? storageData[k] : null; },
          setItem: function (k, v) { storageData[k] = String(v); },
          removeItem: function (k) { delete storageData[k]; },
        };
        var setViews = [];
        ns._mainViewerImpl = function () {
          return { getView: function () { return [7, 8, 9]; },
                   setView: function (v) { setViews.push(v); },
                   render: function () {} };
        };
        var timers = [];
        ns._setTimeoutImpl = function (fn) { timers.push(fn); };

        ns.state.jobId = "job1";
        ns.state.selectedEntryId = "e1";
        ns.state.displayedCoords = [[0,0,0],[1,1,1],[2,2,2]];
        ns.geometryStore.stylePreset = "publication";

        var saved = ns.saveViewState();
        if (!saved || saved.version !== 1) { console.error("FAIL: payload"); process.exit(1); }
        if (saved.atomCount !== 3 || saved.camera.join(",") !== "7,8,9") {
          console.error("FAIL: camera/atomCount"); process.exit(1);
        }
        if (!saved.measurements || saved.measurements[0].type !== "distance") {
          console.error("FAIL: measurements persisted"); process.exit(1);
        }
        var raw = storageData["acp.sv.view.job1:e1"];
        if (!raw || JSON.parse(raw).stylePreset !== "publication") {
          console.error("FAIL: storage key/preset"); process.exit(1);
        }

        ns.state.restoredMeasurements = null;
        var restored = ns.restoreViewState("job1", "e1");
        if (!restored || restored.version !== 1) {
          console.error("FAIL: restore"); process.exit(1);
        }
        if (ns.geometryStore.stylePreset !== "publication") {
          console.error("FAIL: preset restored"); process.exit(1);
        }
        if (!ns.state.restoredMeasurements || ns.state.restoredMeasurements.length !== 1) {
          console.error("FAIL: restoredMeasurements"); process.exit(1);
        }
        if (timers.length !== 1) { console.error("FAIL: deferred camera"); process.exit(1); }
        timers[0]();
        if (setViews.length !== 1 || setViews[0].join(",") !== "7,8,9") {
          console.error("FAIL: camera setView"); process.exit(1);
        }
        if (molDoc.measures.length !== 1 || molDoc.measures[0].atoms[0] !== 1) {
          console.error("FAIL: measurement replay"); process.exit(1);
        }

        // atomCount mismatch -> camera skipped
        timers.length = 0;
        ns.state.displayedCoords = [[0,0,0],[1,1,1],[2,2,2],[3,3,3]];
        ns.restoreViewState("job1", "e1");
        if (timers.length !== 0) {
          console.error("FAIL: mismatch must skip camera"); process.exit(1);
        }

        // corrupted / missing / wrong-version -> silent null
        storageData["acp.sv.view.job1:bad"] = "{not json";
        if (ns.restoreViewState("job1", "bad") !== null) {
          console.error("FAIL: corrupted must be null"); process.exit(1);
        }
        if (ns.restoreViewState("job1", "ghost") !== null) {
          console.error("FAIL: missing must be null"); process.exit(1);
        }
        storageData["acp.sv.view.job1:old"] = JSON.stringify({ version: 99, camera: [1] });
        if (ns.restoreViewState("job1", "old") !== null) {
          console.error("FAIL: version guard"); process.exit(1);
        }

        if (ns.clearViewState("job1", "e1") !== true || "acp.sv.view.job1:e1" in storageData) {
          console.error("FAIL: clear"); process.exit(1);
        }
        console.log("PASS");
    """).replace("JS_PATH", json.dumps(str(_SV_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node view-state test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_render_measure_drawer_reads_store_measurements() -> None:
    """_renderMeasureDrawer is store-first: it reads ACPGeometryStore
    measurements via _storeSnapshot(), shows `editable === false` measurements
    with the store's Chinese verdict message, and applies edits through
    applyMeasuredEdit with the store measurement id (markApplied on success)."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    measure_fn = sv.split("function _renderMeasureDrawer(body)", 1)[1]
    measure_fn = measure_fn[: measure_fn.index("function _renderMoreDrawer(")]
    assert "_storeSnapshot()" in measure_fn, (
        "_renderMeasureDrawer must read the store via _storeSnapshot()"
    )
    assert "snap.measurements" in measure_fn, (
        "_renderMeasureDrawer must read the ACPGeometryStore measurements"
    )
    assert "m.editable === false" in measure_fn, (
        "non-editable measurements must render without an edit control"
    )
    assert "m.message" in measure_fn, (
        "non-editable measurements must show the store verdict message"
    )
    # The store verdict carries a Chinese message; the drawer keeps a Chinese
    # fallback ("当前测量不可编辑") for verdicts without one.
    assert "\\u5f53\\u524d\\u6d4b\\u91cf\\u4e0d\\u53ef\\u7f16\\u8f91" in measure_fn, (
        "Chinese fallback message for non-editable measurements missing"
    )
    assert "applyMeasuredEdit" in measure_fn, "_renderMeasureDrawer must call applyMeasuredEdit"
    assert "m.id" in measure_fn, "applyMeasuredEdit must receive the store measurement id"
    assert "markApplied" in measure_fn, (
        "successful apply must be written back to the store (markApplied)"
    )
    assert "bond_length" in measure_fn, "type mapping bond_length missing"
    assert "bond_angle" in measure_fn, "type mapping bond_angle missing"
    assert "dihedral" in measure_fn, "type mapping dihedral missing"
    assert "_applied" in measure_fn, "_applied flag detection missing for applied measurements"
    assert "_svMeasureAppliedIds = {}" in sv, (
        "applied map must be reset on successful apply to prevent index reuse"
    )
    assert "[-180, 180]" in sv, "dihedral range must be [-180, 180] for negative angles"


def test_apply_measured_edit_call_site_in_viewer() -> None:
    """A production call site of applyMeasuredEdit exists in sv_js (viewer),
    not only in structure_editor.js/tests. Index-based _appliedSet is removed;
    applied state is on the re-seeded object only."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    assert "applyMeasuredEdit" in sv, "applyMeasuredEdit must be referenced in structure_viewer.js"
    assert "ACPStructureEditor.applyMeasuredEdit" in sv, (
        "viewer must call ACPStructureEditor.applyMeasuredEdit"
    )
    assert "bond_length" in sv and "bond_angle" in sv, "type mapping tokens must appear in viewer"
    measure_fn = sv.split("function _renderMeasureDrawer(body)", 1)[1]
    measure_fn = measure_fn[: measure_fn.index("function _renderMoreDrawer(")]
    assert "_appliedSet" not in measure_fn, (
        "index-based _appliedSet must be removed; use _applied flag on object"
    )


def test_edit_panel_in_measure_drawer_only() -> None:
    """The edit panel is rendered ONLY in the measure drawer (not source/more)."""
    sv = _SV_JS_PATH.read_text(encoding="utf-8")

    source_fn = sv.split("function _renderSourceDrawer(body)", 1)[1]
    source_fn = source_fn[: source_fn.index("function _renderVibrationDrawer(")]
    assert "_renderEditPanel" not in source_fn, (
        "_renderEditPanel must NOT be in _renderSourceDrawer"
    )

    more_fn = sv.split("function _renderMoreDrawer(body)", 1)[1]
    more_fn = more_fn[: more_fn.index("function toggleListDrawer(")]
    assert "_renderEditPanel" not in more_fn, "_renderEditPanel must NOT be in _renderMoreDrawer"

    measure_fn = sv.split("function _renderMeasureDrawer(body)", 1)[1]
    measure_fn = measure_fn[: measure_fn.index("function _renderMoreDrawer(")]
    assert "_renderEditPanel" in measure_fn, "_renderEditPanel MUST be in _renderMeasureDrawer"


def test_handle_measure_click_opens_drawer_and_updates_status() -> None:
    """handleMeasureClick resolves the atom via _svResolveAtomId, routes the
    pick through ACPGeometryStore (addToSelection / addMeasurement /
    clearSelection), updates measure-status, and opens the drawer — WITHOUT a
    synchronous renderMolDoc() model rebuild in the pick callback."""
    html = read_workbench_content()

    handler = html.split("function handleMeasureClick(atom)", 1)[1]
    handler = handler[: handler.index("// ── Mechanism Builder")]
    assert "_svResolveAtomId" in handler, (
        "handleMeasureClick must resolve the native 3Dmol atom index"
    )
    assert "ACPGeometryStore" in handler and "window.ACPGeometryStore" in handler, (
        "handleMeasureClick must read the geometry store"
    )
    assert "store.addToSelection" in handler, (
        "handleMeasureClick must append the pick to the store selection"
    )
    assert "store.addMeasurement" in handler, (
        "handleMeasureClick must create the measurement on the store"
    )
    assert "store.clearSelection" in handler, (
        "handleMeasureClick must reset the store selection after a completed measurement"
    )
    assert "renderMolDoc(" not in handler, (
        "pick callback must not synchronously rebuild the model (renderMolDoc)"
    )
    assert 'openDrawer("measure"' in handler, "handleMeasureClick must auto-open measure drawer"
    assert "_updateMeasureStatus" in handler, "handleMeasureClick must call _updateMeasureStatus"
    assert "renderMeasurementList" in handler, (
        "handleMeasureClick must call renderMeasurementList after measurement"
    )
    update_calls = handler.count("_updateMeasureStatus")
    assert update_calls >= 2, (
        f"_updateMeasureStatus must be called at least twice (before+after completion), got {update_calls}"
    )
    after_reset = handler.split("store.clearSelection()", 1)[1]
    assert "_updateMeasureStatus" in after_reset, (
        "_updateMeasureStatus must be called after the selection reset"
    )


def test_measure_status_element_and_update_function() -> None:
    """measure-mode-status span exists and _updateMeasureStatus is store-based:
    it reads the ACPGeometryStore selection length (not the removed
    measureBuffer) and the active measureType."""
    html = read_workbench_content()

    assert 'id="measure-mode-status"' in html, "measure-mode-status span missing"
    assert "function _updateMeasureStatus()" in html, "_updateMeasureStatus function missing"

    update_fn = html.split("function _updateMeasureStatus()", 1)[1]
    update_fn = update_fn[: update_fn.index("function setMode(")]
    assert "measure-mode-status" in update_fn, (
        "_updateMeasureStatus must reference the status element"
    )
    assert "ACPGeometryStore" in update_fn, "_updateMeasureStatus must read the geometry store"
    assert "store.state.selection.length" in update_fn, (
        "_updateMeasureStatus must read the store selection length"
    )
    assert "measureBuffer" not in update_fn, (
        "_updateMeasureStatus must not read the removed measureBuffer"
    )
    assert "measureType" in update_fn, "_updateMeasureStatus must read measureType"


def test_set_mode_calls_update_measure_status() -> None:
    """setMode calls _updateMeasureStatus."""
    html = read_workbench_content()

    setmode_fn = html.split("function setMode(mode)", 1)[1]
    setmode_fn = setmode_fn[: setmode_fn.index("function updateFrameController")]
    assert "_updateMeasureStatus" in setmode_fn, "setMode must call _updateMeasureStatus"


# ---------------------------------------------------------------------------
# ACPGeometryStore (frontend/js/geometry_store.js) node unit contracts
# ---------------------------------------------------------------------------


def test_geometry_store_node_load_and_selection_order() -> None:
    """load() resets the store (revision 1, fresh geometry, empty
    selection/measurements); addToSelection preserves pick order and dedupes;
    setSelection dedupes while keeping first-occurrence order."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = {};
        require(GEOMETRY_PATH);

        var store = window.ACPGeometryStore;
        if (!store) { console.error("FAIL: window.ACPGeometryStore missing"); process.exit(1); }

        var snap = store.load({ entryId: "e1", symbols: ["C", "C", "C"], coordinates: [[0,0,0],[1.5,0,0],[3,0,0]] });
        if (snap.revision !== 1 || store.state.revision !== 1) { console.error("FAIL: revision must be 1 after load"); process.exit(1); }
        if (store.state.entryId !== "e1") { console.error("FAIL: entryId not stored"); process.exit(1); }
        if (JSON.stringify(store.state.symbols) !== JSON.stringify(["C", "C", "C"])) { console.error("FAIL: symbols"); process.exit(1); }
        if (JSON.stringify(store.state.coordinates) !== JSON.stringify([[0,0,0],[1.5,0,0],[3,0,0]])) { console.error("FAIL: coordinates"); process.exit(1); }
        if (store.state.selection.length !== 0 || store.state.measurements.length !== 0) { console.error("FAIL: load must clear selection+measurements"); process.exit(1); }

        store.addToSelection(2);
        store.addToSelection(0);
        store.addToSelection(1);
        store.addToSelection(2);
        if (JSON.stringify(store.state.selection) !== JSON.stringify([2, 0, 1])) { console.error("FAIL: pick order/dedupe, got " + JSON.stringify(store.state.selection)); process.exit(1); }

        store.setSelection([5, 3, 5, 1]);
        if (JSON.stringify(store.state.selection) !== JSON.stringify([5, 3, 1])) { console.error("FAIL: setSelection dedupe, got " + JSON.stringify(store.state.selection)); process.exit(1); }

        console.log("PASS");
    """).replace("GEOMETRY_PATH", json.dumps(str(_GEOMETRY_STORE_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node geometry-store load/selection test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_geometry_store_node_update_coordinates_preserves_state() -> None:
    """updateCoordinates() bumps the revision, KEEPS selection + measurements,
    and recomputes every measurement value from the new coordinates."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = {};
        require(GEOMETRY_PATH);

        var store = window.ACPGeometryStore;
        store.load({ entryId: "e1", symbols: ["C", "C"], coordinates: [[0,0,0],[1.5,0,0]] });
        store.addToSelection(0);
        var m = store.addMeasurement("distance", [0, 1]);
        if (!m || Math.abs(m.value - 1.5) > 1e-9) { console.error("FAIL: initial measurement value"); process.exit(1); }

        var snap = store.updateCoordinates([[0,0,0],[1.6,0,0]]);
        if (snap.revision !== 2 || store.state.revision !== 2) { console.error("FAIL: revision must bump to 2"); process.exit(1); }
        if (JSON.stringify(store.state.selection) !== JSON.stringify([0])) { console.error("FAIL: selection lost on updateCoordinates"); process.exit(1); }
        if (store.state.measurements.length !== 1) { console.error("FAIL: measurements lost on updateCoordinates"); process.exit(1); }
        if (Math.abs(store.state.measurements[0].value - 1.6) > 1e-9) { console.error("FAIL: measurement value not recomputed, got " + store.state.measurements[0].value); process.exit(1); }

        console.log("PASS");
    """).replace("GEOMETRY_PATH", json.dumps(str(_GEOMETRY_STORE_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node geometry-store updateCoordinates test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_geometry_store_node_add_measurement_validation() -> None:
    """addMeasurement() returns a measurement for valid arity/ids, null for
    wrong arity / unknown type / duplicate / malformed ids, and copies the
    ACPStructureEditor.validateEditableCoordinate verdict (editable/reason/message)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = {};
        require(GEOMETRY_PATH);

        var store = window.ACPGeometryStore;
        store.load({ entryId: "e1", symbols: ["C", "C", "C"], coordinates: [[0,0,0],[1.5,0,0],[3,1,0]] });

        var m = store.addMeasurement("distance", [0, 1]);
        if (!m || m.type !== "distance" || JSON.stringify(m.atoms) !== JSON.stringify([0, 1])) { console.error("FAIL: valid measurement not created"); process.exit(1); }
        if (Math.abs(m.value - 1.5) > 1e-9) { console.error("FAIL: measurement value"); process.exit(1); }

        if (store.addMeasurement("distance", [0]) !== null) { console.error("FAIL: wrong arity must return null"); process.exit(1); }
        if (store.addMeasurement("bogus", [0, 1]) !== null) { console.error("FAIL: unknown type must return null"); process.exit(1); }
        if (store.addMeasurement("distance", [0, 0]) !== null) { console.error("FAIL: duplicate ids must return null"); process.exit(1); }
        if (store.addMeasurement("distance", [0, -1]) !== null) { console.error("FAIL: malformed id must return null"); process.exit(1); }

        window.ACPStructureEditor = {
            validateEditableCoordinate: function () {
                return { editable: false, reason: "ring_bond", message: "X" };
            }
        };
        var blocked = store.addMeasurement("distance", [1, 2]);
        if (!blocked) { console.error("FAIL: blocked measurement must still be created"); process.exit(1); }
        if (blocked.editable !== false || blocked.reason !== "ring_bond" || blocked.message !== "X") {
            console.error("FAIL: verdict not copied, got " + JSON.stringify(blocked));
            process.exit(1);
        }
        if (store.state.measurements.length !== 2) { console.error("FAIL: blocked measurement must be stored"); process.exit(1); }

        console.log("PASS");
    """).replace("GEOMETRY_PATH", json.dumps(str(_GEOMETRY_STORE_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node geometry-store addMeasurement test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_geometry_store_node_compute_measurement_values() -> None:
    """computeMeasurementValue: distance 3.0 A, right angle 90 deg, anti
    dihedral 180 deg (all within 1e-6)."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = {};
        require(GEOMETRY_PATH);

        var store = window.ACPGeometryStore;
        var dist = store.computeMeasurementValue("distance", [[0,0,0],[3,0,0]], [0, 1]);
        if (Math.abs(dist - 3) > 1e-6) { console.error("FAIL: distance " + dist); process.exit(1); }

        var angle = store.computeMeasurementValue("angle", [[1,0,0],[0,0,0],[0,1,0]], [0, 1, 2]);
        if (Math.abs(angle - 90) > 1e-6) { console.error("FAIL: angle " + angle); process.exit(1); }

        var dihedral = store.computeMeasurementValue("dihedral", [[1,0,0],[0,0,0],[0,1,0],[-1,1,0]], [0, 1, 2, 3]);
        if (Math.abs(dihedral - 180) > 1e-6) { console.error("FAIL: dihedral " + dihedral); process.exit(1); }

        console.log("PASS");
    """).replace("GEOMETRY_PATH", json.dumps(str(_GEOMETRY_STORE_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node geometry-store computeMeasurementValue test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_geometry_store_node_subscribe_change_tokens() -> None:
    """subscribe(fn) receives the change tokens in mutation order
    ("load", "selection", "measurement", "coordinates", "preview") and the
    returned unsubscriber stops delivery."""
    if not shutil.which("node"):
        pytest.skip("node not available")

    script = textwrap.dedent("""\
        var window = {};
        require(GEOMETRY_PATH);

        var store = window.ACPGeometryStore;
        var seen = [];
        var unsubscribe = store.subscribe(function (_snap, change) { seen.push(change); });

        store.load({ entryId: "e1", symbols: ["C", "C"], coordinates: [[0,0,0],[1.5,0,0]] });
        store.addToSelection(1);
        store.addMeasurement("distance", [0, 1]);
        store.updateCoordinates([[0,0,0],[1.6,0,0]]);
        store.setEditPreview({ coordinates: [[0,0,0],[1.7,0,0]] });

        var expected = ["load", "selection", "measurement", "coordinates", "preview"];
        if (JSON.stringify(seen) !== JSON.stringify(expected)) { console.error("FAIL: tokens " + JSON.stringify(seen)); process.exit(1); }

        unsubscribe();
        var countBefore = seen.length;
        store.clearSelection();
        store.updateCoordinates([[0,0,0],[1.8,0,0]]);
        if (seen.length !== countBefore) { console.error("FAIL: unsubscriber did not stop delivery"); process.exit(1); }

        console.log("PASS");
    """).replace("GEOMETRY_PATH", json.dumps(str(_GEOMETRY_STORE_JS_PATH)))

    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"Node geometry-store subscribe test failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout


def test_measure_i18n_keys_in_both_locales() -> None:
    """New i18n keys exist in both zh-CN and en-US locale blocks."""
    html = read_workbench_content()

    for zh, en in (
        ('"structure.vib.btn": "振动模式"', '"structure.vib.btn": "Vibrations"'),
        (
            '"structure.vib.imaginary_btn": "虚频 {count}"',
            '"structure.vib.imaginary_btn": "Imaginary {count}"',
        ),
        ('"structure.measure.btn": "测量"', '"structure.measure.btn": "Measure"'),
        (
            '"structure.measure.type_distance": "距离"',
            '"structure.measure.type_distance": "Distance"',
        ),
        ('"structure.measure.type_angle": "角度"', '"structure.measure.type_angle": "Angle"'),
        (
            '"structure.measure.type_dihedral": "二面角"',
            '"structure.measure.type_dihedral": "Dihedral"',
        ),
        ('"structure.measure.apply": "应用修改"', '"structure.measure.apply": "Apply Edit"'),
        ('"structure.measure.applied": "已应用"', '"structure.measure.applied": "Applied"'),
        (
            '"structure.measure.range_warn": "值超出范围"',
            '"structure.measure.range_warn": "Value out of range"',
        ),
        (
            '"structure.measure.status_label": "测量："',
            '"structure.measure.status_label": "Measure: "',
        ),
        (
            '"structure.measure.status_selected": "已选"',
            '"structure.measure.status_selected": "selected"',
        ),
    ):
        assert zh in html, f"zh-CN key missing: {zh}"
        assert en in html, f"en-US key missing: {en}"


_GEOMETRY_STORE_JS_PATH = FRONTEND_JS_DIR / "geometry_store.js"
