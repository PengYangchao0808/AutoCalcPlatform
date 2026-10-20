# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Real-Chromium frontend viewer tests (D08 NMR canvas lifecycle; D09 vibration).

These are the FORMAL browser assertions for the architecture-acceptance
remediation — the diagnostics-only ``reports/architecture_acceptance_20261019/
browser_probe.py`` print probe is NEVER a pass and is not used as closure here.

Stack under test in a real headless Chromium page (no DOM/canvas/3Dmol stubs):

  * ``js/vendor/3Dmol-2.5.5.min.js``  — the repository vendor (untouched),
  * ``js/visualization/framing.js`` + ``mol3d_adapter.js`` + ``scene_instance.js``,
  * ``js/structure_viewer.js``       — the real ``sharedLoadGeometry`` path,
  * ``js/workbench/features/nmr.js`` — the production NMR loader.

Injected seams (outer-app glue only, never the canvas or the 3Dmol library):
``t``/``escapeHtml`` i18n helpers and the app's ``api``/``jobRemoteNodeName``
fetch wrapper, answered at the network boundary (route-faked origin, no
listening socket, no external network) with a real 3-atom XYZ geometry so the
production ``nmrFetchCandidateGeometry`` → ``nmrFetchGeometryFromPath`` →
``nmrGeometryText`` path runs unchanged.

``test_nmr_canvas_survives_success`` asserts the D08 contract end to end:

  * canvas count stays 1 and the canvas ``isConnected()`` is true after a
    successful load (the success path must never ``container.textContent = ""``
    the canvas parent again);
  * the atom snapshot holds 3 atoms (water XYZ) with labels;
  * the rendered view is non-empty (real pixels differing from the background);
  * PNG export through the restricted instance API returns a non-empty blob;
  * a candidate/container switch disposes the OLD instance and leaves exactly
    one registry entry; ``nmrPanelDispose()`` disposes everything.

Three-state rule: when Playwright/Chromium is unavailable the tests SKIP with a
``NOT_VERIFIED`` reason naming the exact missing prerequisite — a skipped
browser test is never a green pass. Not marked ``slow``: the targeted run
``python -m pytest tests/test_acp_frontend_browser.py -q -k nmr`` must execute
the real browser when the prerequisite is present (module-level importorskip
keeps CI's non-browser legs green).

The D09 vibration section (``test_vibration_*``) asserts the same contract on
the real ``js/vibration_viewer.js`` draw/animation lifecycle:

  * cold start with ``arrowState.enabled=false`` draws the displacement arrows
    from display intent alone (``_lastCount`` AND the real 3Dmol shape
    registry — both required, never just the flag);
  * animation changes real model frames, combo shows arrows + moving frames
    together, hide stops ticking / remount restores, two module instances
    (each on its own real $3Dmol canvas) keep independent state, and
    ``handleTeardown()`` clears every shape;
  * the frequency view exports through the existing instance ``pngURI()``
    path (non-empty PNG blob with real non-background pixels).

Non-vacuity (QA only, never production): ``ACP_FRONTEND_NMR_JS=/path/to/copy.js``
re-points the page at a scratch copy of ``nmr.js``; reintroducing
``container.textContent = ""`` into the success path of that copy must make the
browser test FAIL on the canvas assertions.  ``ACP_FRONTEND_VIBRATION_JS=...``
likewise serves a scratch copy of ``vibration_viewer.js`` — restoring the old
``_arrowsVisible`` gate (``arrowState.enabled && displayMode !== "animation"``)
there must make the cold-start test FAIL.  The production files are never
patched for this.  ``ACP_FRONTEND_VIBRATION_EXPORT_PNG=/path.png`` records the
exported frequency-view PNG artifact.

Shared harness (``browser`` fixture, ``frontend_page`` fixture,
``load_viewer_stack``) is reused by the D09 vibration section (task 23).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "playwright.sync_api",
    reason=(
        "NOT_VERIFIED: playwright (browser extra) not installed — "
        "pip install -e '.[browser]' then `python -m playwright install chromium`; "
        "skipped, never counted as a pass"
    ),
)
from playwright.sync_api import Browser, Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"

VENDOR_3DMOL = "js/vendor/3Dmol-2.5.5.min.js"
NMR_JS = "js/workbench/features/nmr.js"
VIBRATION_JS = "js/vibration_viewer.js"
PAGE_URL = "http://acp.test/__acp_frontend_browser__.html"
_HARNESS_PATH = "/" + PAGE_URL.split("://", 1)[-1].split("/", 1)[-1]

# QA-only non-vacuity hook: serve a scratch copy of nmr.js at the production
# URL so the page bootstrap stays identical (never patch production code).
NMR_JS_OVERRIDE = os.environ.get("ACP_FRONTEND_NMR_JS", "")
# QA-only non-vacuity hook for D09: scratch copy of vibration_viewer.js (e.g.
# with the old `_arrowsVisible` gate restored) served at the production URL.
VIBRATION_JS_OVERRIDE = os.environ.get("ACP_FRONTEND_VIBRATION_JS", "")
# When set, the frequency-view PNG produced by the export path is recorded
# there as an evidence artifact.
VIBRATION_EXPORT_PNG = os.environ.get("ACP_FRONTEND_VIBRATION_EXPORT_PNG", "")

# Real 3-atom geometry (water) injected at the network boundary.
WATER_XYZ = "3\nwater\nO 0 0 0\nH 0 0 0.96\nH 0 0.93 -0.24\n"
# The same geometry as a coordinate/symbol pair (structure-viewer state).
WATER_COORDS: list[list[float]] = [
    [0.0, 0.0, 0.0],
    [0.0, 0.0, 0.96],
    [0.0, 0.93, -0.24],
]
WATER_SYMBOLS: list[str] = ["O", "H", "H"]
# One imaginary mode's per-atom displacement vectors (3 non-zero rows -> 3
# arrows) plus a real positive mode for the mode list.  Norms vary on purpose:
# computeArrowEndpoints normalizes per atom (scientific convention unchanged).
VIB_VECTORS: list[list[float]] = [
    [0.13, -0.02, 0.28],
    [-0.21, 0.44, -0.09],
    [-0.18, -0.41, -0.11],
]
VIBRATIONS_JSON: dict[str, Any] = {
    "available": True,
    "reason": None,
    "geometry_product_id": None,
    "modes": [
        {
            "mode_index": 6,
            "frequency_cm1": -797.72,
            "imaginary": True,
            "ir_intensity": 12.3,
            "vectors": VIB_VECTORS,
        },
        {
            "mode_index": 7,
            "frequency_cm1": 1594.05,
            "imaginary": False,
            "ir_intensity": 44.1,
            "vectors": VIB_VECTORS,
        },
    ],
}

# Conservative floors for "the view is non-empty" (observed ≫ these on a real
# render: hundreds of non-background pixels and dozens of colors).
MIN_NONBG_PIXELS = 20
MIN_UNIQUE_COLORS = 4
MIN_PNG_BYTES = 1000

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".map": "application/json",
}

_HARNESS_HTML = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>d08 browser</title>'
    "</head><body>"
    '<div id="nmr-report-body">'
    '<div data-nmr-structure-viewer="0" class="nmr-structure-viewer empty"'
    ' style="width:400px;height:300px"></div>'
    '<div data-nmr-structure-viewer="1" class="nmr-structure-viewer empty"'
    ' style="width:400px;height:300px"></div>'
    "</div>"
    '<div id="structure-inspector-vibrations" style="width:420px"></div>'
    "</body></html>"
)

# Populated by the browser fixture; embedded in assertion messages so any
# failure records the exact browser/vendor identity under test.
BROWSER_FACTS: dict[str, str] = {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


VENDOR_3DMOL_SHA256 = _sha256(FRONTEND / VENDOR_3DMOL)


def _identity() -> str:
    return (
        f"browser={BROWSER_FACTS.get('chromium', '?')} "
        f"vendor={VENDOR_3DMOL}:{VENDOR_3DMOL_SHA256[:16]}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    """Launch real headless Chromium or SKIP as NOT_VERIFIED.

    Launch falls back to ``--no-sandbox`` (root/container environments); if
    both attempts fail the exact prerequisite error is recorded in the skip
    reason — never a pass.
    """
    with sync_playwright() as pw:
        instance: Browser | None = None
        launch_errors: list[str] = []
        for args in ([], ["--no-sandbox"]):
            try:
                instance = pw.chromium.launch(headless=True, args=args)
                break
            except Exception as exc:  # noqa: BLE001 — record, then NOT_VERIFIED
                launch_errors.append(f"{type(exc).__name__}: {exc}")
        if instance is None:
            pytest.skip(
                "NOT_VERIFIED: Chromium unavailable — install with "
                "`python -m playwright install chromium` (playwright extra: "
                f"pip install -e '.[browser]'); launch errors: {launch_errors}; "
                "skipped, never counted as a pass"
            )
        BROWSER_FACTS["chromium"] = instance.version
        try:
            yield instance
        finally:
            instance.close()


class _Harness:
    """Route-faked origin: real frontend files from disk, zero network.

    Every request is fulfilled locally — static assets come from ``frontend/``,
    the harness page supplies the minimal DOM, and ``/jobs/<id>/files/<path>``
    answers the production geometry fetch with the real XYZ payload.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.page_errors: list[str] = []
        self.console_errors: list[str] = []

    def attach(self) -> None:
        self.page.on("pageerror", lambda err: self.page_errors.append(str(err)))
        self.page.on(
            "console",
            lambda msg: self.console_errors.append(msg.text) if msg.type == "error" else None,
        )
        self.page.route("**/*", self._handle)

    def _handle(self, route: Any) -> None:
        request = route.request
        path = "/" + request.url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
        if path in ("/", _HARNESS_PATH):
            route.fulfill(status=200, body=_HARNESS_HTML, content_type=_CONTENT_TYPES[".html"])
            return
        if path.startswith("/jobs/"):
            route.fulfill(
                status=200,
                content_type=_CONTENT_TYPES[".json"],
                body=json.dumps({"xyz": WATER_XYZ}),
            )
            return
        if path.startswith("/api/v1/jobs/") and path.endswith("/vibrations"):
            route.fulfill(
                status=200,
                content_type=_CONTENT_TYPES[".json"],
                body=json.dumps(VIBRATIONS_JSON),
            )
            return
        if path == "/" + NMR_JS and NMR_JS_OVERRIDE:
            override = Path(NMR_JS_OVERRIDE).resolve()
            route.fulfill(
                status=200,
                body=override.read_bytes(),
                content_type=_CONTENT_TYPES[".js"],
            )
            return
        if path == "/" + VIBRATION_JS and VIBRATION_JS_OVERRIDE:
            override = Path(VIBRATION_JS_OVERRIDE).resolve()
            route.fulfill(
                status=200,
                body=override.read_bytes(),
                content_type=_CONTENT_TYPES[".js"],
            )
            return
        candidate = (FRONTEND / path.lstrip("/")).resolve()
        if FRONTEND.resolve() in candidate.parents and candidate.is_file():
            route.fulfill(
                status=200,
                body=candidate.read_bytes(),
                content_type=_CONTENT_TYPES.get(candidate.suffix, "application/octet-stream"),
            )
            return
        route.fulfill(status=404, body=b"")


@pytest.fixture()
def frontend_page(browser: Browser) -> Generator[_Harness, None, None]:
    """Open the harness page on the route-faked origin (per-test context)."""
    context = browser.new_context(viewport={"width": 1000, "height": 700})
    page = context.new_page()
    harness = _Harness(page)
    harness.attach()
    page.goto(PAGE_URL, wait_until="load")
    try:
        yield harness
    finally:
        context.close()


def load_viewer_stack(harness: _Harness, extra_scripts: tuple[str, ...] = ()) -> None:
    """Inject outer-app glue, then the REAL vendor + viewer modules + features.

    ``extra_scripts`` lets the later D09 vibration section load
    ``js/vibration_viewer.js`` on the same stack.
    """
    harness.page.evaluate(
        """() => {
        window.t = (key) => key;
        window.escapeHtml = (s) => String(s);
        // Outer-app fetch glue only — geometry bytes come from the route
        // boundary, the production nmrFetchCandidateGeometry path runs real.
        window.jobRemoteNodeName = () => null;
        window.api = async (url) => (await (await fetch(url)).json());
        window.apiRemote = window.api;
    }"""
    )
    scripts = (
        VENDOR_3DMOL,
        "js/visualization/framing.js",
        "js/visualization/mol3d_adapter.js",
        "js/visualization/scene_instance.js",
        "js/structure_viewer.js",
        NMR_JS,
        *extra_scripts,
    )
    for rel in scripts:
        harness.page.add_script_tag(url="http://acp.test/" + rel)
    harness.page.evaluate(
        """() => {
        window.scheduleViewerFraming = window.ACPVisualization.framing.scheduleViewerFraming;
        window.disposeViewerFraming = window.ACPVisualization.framing.disposeViewerFraming;
    }"""
    )


# ---------------------------------------------------------------------------
# NMR (D08)
# ---------------------------------------------------------------------------


def test_nmr_canvas_survives_success(frontend_page: _Harness) -> None:
    """D08/GAP-8: the live canvas survives a successful load in real Chromium."""
    harness = frontend_page
    page = harness.page
    load_viewer_stack(harness)

    facts = page.evaluate(
        """async () => {
        const facts = {
            vendor_loaded: typeof $3Dmol !== "undefined"
                && typeof $3Dmol.createViewer === "function",
            shared_loader: !!(window.ACPStructureViewer
                && window.ACPStructureViewer.sharedLoadGeometry),
        };
        const c0 = document.querySelector('[data-nmr-structure-viewer="0"]');
        const info1 = nmrEnsureViewer(0, c0);
        facts.first_instance_created = !!(info1 && info1.instance);
        await nmrLoadCandidateGeometry({ id: "audit" }, { index: 0 }, nmrPanelState.token);

        // ── success contract ────────────────────────────────────────────
        facts.canvas_count_after_success = c0.querySelectorAll("canvas").length;
        const canvas1 = c0.querySelector("canvas");
        facts.canvas_connected_after_success = canvas1 ? canvas1.isConnected : null;
        const entry1 = nmrPanelState.viewers[0];
        facts.atoms = entry1 ? entry1.atoms.length : -1;
        facts.labels = entry1 ? entry1.labels : null;
        facts.status_nodes_after_success = c0.querySelectorAll(".nmr-viewer-status").length;
        facts.container_class_after_success = c0.className;

        // Framing settle, then PNG export through the restricted API.
        const settleDeadline = Date.now() + 2000;
        while (Date.now() < settleDeadline
               && !(nmrPanelState.viewers[0] && nmrPanelState.viewers[0].framing
                    && nmrPanelState.viewers[0].framing.settled)) {
            await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
        }
        facts.framing_settled = !!(nmrPanelState.viewers[0] && nmrPanelState.viewers[0].framing
                                   && nmrPanelState.viewers[0].framing.settled);
        const inst1 = nmrPanelState.viewers[0].instance;
        inst1.render();
        const uri = inst1.pngURI();
        facts.png_is_data_url = typeof uri === "string"
            && uri.indexOf("data:image/png;base64,") === 0;
        facts.png_bytes = facts.png_is_data_url ? atob(uri.split(",")[1]).length : 0;
        facts.png_nonbg_pixels = 0;
        facts.png_unique_colors = 0;
        if (facts.png_is_data_url) {
            const img = new Image();
            await new Promise((resolve, reject) => {
                img.onload = () => resolve(null);
                img.onerror = () => reject(new Error("png decode failed"));
                img.src = uri;
            });
            const buf = document.createElement("canvas");
            buf.width = img.width;
            buf.height = img.height;
            const ctx = buf.getContext("2d");
            ctx.drawImage(img, 0, 0);
            const data = ctx.getImageData(0, 0, buf.width, buf.height).data;
            const seen = new Set();
            let nonbg = 0;
            for (let i = 0; i < data.length; i += 4) {
                seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
                // scene background is 0x0e1419
                if (Math.abs(data[i] - 14) > 6 || Math.abs(data[i + 1] - 20) > 6
                    || Math.abs(data[i + 2] - 25) > 6) nonbg += 1;
            }
            facts.png_nonbg_pixels = nonbg;
            facts.png_unique_colors = seen.size;
            facts.png_dims = [img.width, img.height];
        }

        // ── reopen: reuse guard keeps the SAME live instance and canvas ─
        await nmrLoadCandidateGeometry({ id: "audit" }, { index: 0 }, nmrPanelState.token);
        facts.reopen_same_instance = nmrPanelState.viewers[0].instance === inst1;
        facts.canvas_count_after_reopen = c0.querySelectorAll("canvas").length;

        // ── candidate/container switch: OLD instance is disposed ────────
        const c0b = document.createElement("div");
        c0b.setAttribute("data-nmr-structure-viewer", "0");
        c0b.className = "nmr-structure-viewer empty";
        c0b.style.width = "400px";
        c0b.style.height = "300px";
        c0.replaceWith(c0b);
        const info2 = nmrEnsureViewer(0, c0b);
        facts.switch_created_new_instance = !!(info2 && info2.instance && info2.instance !== inst1);
        facts.old_instance_disposed_on_switch = inst1.isDisposed() === true;
        facts.registry_size_after_switch = Object.keys(nmrPanelState.viewers).length;
        facts.registry_holds_new_after_switch = !!(nmrPanelState.viewers[0]
            && info2 && nmrPanelState.viewers[0].instance === info2.instance);
        const canvas2 = c0b.querySelector("canvas");
        facts.canvas_count_after_switch = c0b.querySelectorAll("canvas").length;
        facts.canvas_connected_after_switch = canvas2 ? canvas2.isConnected : null;

        // ── dispose: every instance released, registry empty ────────────
        const inst2 = info2.instance;
        nmrPanelDispose();
        facts.registry_size_after_dispose = Object.keys(nmrPanelState.viewers).length;
        facts.first_instance_disposed = inst1.isDisposed() === true;
        facts.new_instance_disposed = inst2.isDisposed() === true;
        return facts;
    }"""
    )
    identity = _identity()

    assert facts["vendor_loaded"], f"real vendor $3Dmol missing ({identity}): {facts}"
    assert facts["shared_loader"], f"real sharedLoadGeometry missing ({identity}): {facts}"
    assert facts["first_instance_created"], f"scene instance not created ({identity}): {facts}"
    assert not harness.page_errors, f"page JS errors ({identity}): {harness.page_errors}"

    # D08 core: the success path must not wipe the canvas parent.
    assert facts["canvas_count_after_success"] == 1, (
        f"D08 regression: canvas count after success must be 1 — got "
        f"{facts['canvas_count_after_success']} ({identity}); "
        f"container.textContent wipe detaches the live canvas. facts={facts}"
    )
    assert facts["canvas_connected_after_success"] is True, (
        f"D08 regression: canvas must stay connected after success ({identity}); facts={facts}"
    )
    assert facts["atoms"] == 3, f"atom snapshot must hold 3 water atoms ({identity}): {facts}"
    assert facts["labels"] == ["O1", "H1", "H2"], (
        f"element+count labels from the real model ({identity}): {facts}"
    )
    assert facts["status_nodes_after_success"] == 0, (
        f"success removes ONLY the status node ({identity}): {facts}"
    )
    assert facts["container_class_after_success"] == "nmr-structure-viewer", (
        f"success path marks the container loaded ({identity}): {facts}"
    )

    # Real rendered view: non-empty pixels and a non-empty PNG blob.
    assert facts["png_is_data_url"], f"pngURI() must return a PNG data URL ({identity}): {facts}"
    assert facts["png_bytes"] > MIN_PNG_BYTES, (
        f"PNG export blob must be non-empty ({identity}): {facts}"
    )
    assert facts["png_nonbg_pixels"] >= MIN_NONBG_PIXELS, (
        f"rendered view must be non-empty — {facts['png_nonbg_pixels']} non-background "
        f"pixels < {MIN_NONBG_PIXELS} ({identity}); facts={facts}"
    )
    assert facts["png_unique_colors"] >= MIN_UNIQUE_COLORS, (
        f"rendered view must be non-empty — {facts['png_unique_colors']} unique colors "
        f"< {MIN_UNIQUE_COLORS} ({identity}); facts={facts}"
    )

    # Reopen keeps the same live instance (no duplicate creation / leak).
    assert facts["reopen_same_instance"], f"reopen must reuse the instance ({identity}): {facts}"
    assert facts["canvas_count_after_reopen"] == 1, (
        f"canvas count stays 1 across reopen ({identity}): {facts}"
    )

    # Candidate switch removes the old instance.
    assert facts["switch_created_new_instance"], (
        f"container switch must create a fresh instance ({identity}): {facts}"
    )
    assert facts["old_instance_disposed_on_switch"], (
        f"candidate switch must dispose the OLD instance ({identity}): {facts}"
    )
    assert facts["registry_size_after_switch"] == 1, (
        f"registry must hold exactly one entry per candidate ({identity}): {facts}"
    )
    assert facts["registry_holds_new_after_switch"], (
        f"registry must point at the live instance ({identity}): {facts}"
    )
    assert facts["canvas_count_after_switch"] == 1, (
        f"new container holds exactly one canvas ({identity}): {facts}"
    )
    assert facts["canvas_connected_after_switch"] is True, (
        f"new canvas must be connected ({identity}): {facts}"
    )

    # Dispose removes every instance.
    assert facts["registry_size_after_dispose"] == 0, (
        f"nmrPanelDispose must empty the registry ({identity}): {facts}"
    )
    assert facts["first_instance_disposed"] is True, (
        f"dispose must release the replaced instance ({identity}): {facts}"
    )
    assert facts["new_instance_disposed"] is True, (
        f"dispose must release the live instance ({identity}): {facts}"
    )

    print(  # noqa: T201 — evidence surface (visible with -s / on failure)
        "D08 browser facts: "
        + json.dumps({"identity": identity, "facts": facts}, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Vibration (D09) — task 23: real-browser arrow/animation assertions.
# ---------------------------------------------------------------------------

_VIB_GEOM = json.dumps(
    {
        "coords": WATER_COORDS,
        "symbols": WATER_SYMBOLS,
        "xyz": WATER_XYZ,
        "entryId": "e1",
    }
)

# Boundary instrumentation (test-side only): capture the REAL $3Dmol viewers
# the production mol3d adapter creates so assertions can read the real shape
# registry (`viewer.shapes`).  The library, canvases and drawing code stay
# untouched — nothing is faked.
_VIB_SETUP_JS = (
    """() => {
    const GEOM = """
    + _VIB_GEOM
    + """;
    const natives = [];
    window.__vibNatives = natives;
    const mol3d = window.ACPVisualization.mol3d;
    const origCreate = mol3d.create;
    mol3d.create = function (container, options) {
        const viewer = origCreate.call(mol3d, container, options);
        if (viewer) natives.push(viewer);
        return viewer;
    };

    const svState = window.ACPStructureViewer.state;
    svState.displayedCoords = GEOM.coords;
    svState.displayedSymbols = GEOM.symbols;
    svState.displayedEntryId = GEOM.entryId;

    const container = document.createElement("div");
    container.id = "vib-viewer-main";
    container.style.width = "400px";
    container.style.height = "300px";
    document.body.appendChild(container);
    const inst = window.ACPVisualization.scene.create({
        container: container,
        options: { backgroundColor: "0x0e1419", antialias: true },
    });
    const native = natives.length ? natives[natives.length - 1] : null;
    if (inst) {
        inst.addModel(GEOM.xyz, "xyz");
        inst.addStyle({}, { sphere: { scale: 0.22 }, stick: { radius: 0.12 } });
        inst.frame({});
    }
    window.ACPVibrationViewer._viewerImpl = () => inst;
    window.__vib = { inst: inst, native: native, container: container };
    return {
        vendor_loaded: typeof $3Dmol !== "undefined"
            && typeof $3Dmol.createViewer === "function",
        instance_created: !!(inst && typeof inst.addArrow === "function"),
        native_captured: !!native,
        enabled_before: window.ACPVibrationViewer.arrowState.enabled,
        last_count_before: window.ACPVibrationViewer.arrowState._lastCount,
    };
}"""
)

_VIB_SHAPE_JS = """(native) => (native.shapes || []).filter((s) => !!s).length"""

_VIB_COLD_START_JS = (
    """async () => {
    const GEOM = """
    + _VIB_GEOM
    + """;
    const ns = window.ACPVibrationViewer;
    const { inst, native } = window.__vib;
    const realShapes = () => (native.shapes || []).filter((s) => !!s).length;
    const facts = { enabled_before_load: ns.arrowState.enabled };

    // Production first draw: fetch -> parse -> defaultModeIndex -> refreshArrows.
    // No enabled=true seeding happens anywhere before this call.
    await ns.loadVibrations("job-vib", "e1");
    facts.available = !!(ns.state.data && ns.state.data.available === true);
    facts.selected_mode_index = ns.state.selectedModeIndex;
    facts.last_count_cold = ns.arrowState._lastCount;
    facts.real_shapes_cold = realShapes();
    facts.enabled_after_cold = ns.arrowState.enabled;
    facts.hint_cold = ns.arrowState._hint;

    inst.render();
    const uri = inst.pngURI();
    facts.export_is_data_url = typeof uri === "string"
        && uri.indexOf("data:image/png;base64,") === 0;
    facts.png_bytes = facts.export_is_data_url ? atob(uri.split(",")[1]).length : 0;
    facts.png_nonbg_pixels = 0;
    facts.png_unique_colors = 0;
    if (facts.export_is_data_url) {
        const img = new Image();
        await new Promise((resolve, reject) => {
            img.onload = () => resolve(null);
            img.onerror = () => reject(new Error("png decode failed"));
            img.src = uri;
        });
        const buf = document.createElement("canvas");
        buf.width = img.width;
        buf.height = img.height;
        const ctx = buf.getContext("2d");
        ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, buf.width, buf.height).data;
        const seen = new Set();
        let nonbg = 0;
        for (let i = 0; i < data.length; i += 4) {
            seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
            if (Math.abs(data[i] - 14) > 6 || Math.abs(data[i + 1] - 20) > 6
                || Math.abs(data[i + 2] - 25) > 6) nonbg += 1;
        }
        facts.png_nonbg_pixels = nonbg;
        facts.png_unique_colors = seen.size;
        facts.png_dims = [img.width, img.height];
    }

    window.ACPStructureViewer.state.displayedEntryId = "other";
    ns.refreshArrows();
    facts.last_count_missing = ns.arrowState._lastCount;
    facts.real_shapes_missing = realShapes();
    facts.enabled_missing = ns.arrowState.enabled;
    facts.hint_missing = ns.arrowState._hint;

    window.ACPStructureViewer.state.displayedEntryId = GEOM.entryId;
    ns.refreshArrows();
    facts.last_count_recovered = ns.arrowState._lastCount;
    facts.real_shapes_recovered = realShapes();
    facts.enabled_recovered = ns.arrowState.enabled;

    ns.setDisplayMode("animation");
    facts.last_count_animation = ns.arrowState._lastCount;
    facts.real_shapes_animation = realShapes();
    ns.setDisplayMode("combo");
    facts.last_count_combo = ns.arrowState._lastCount;
    facts.real_shapes_combo = realShapes();
    ns.setDisplayMode("arrows");
    facts.last_count_arrows = ns.arrowState._lastCount;
    facts.real_shapes_arrows = realShapes();
    return { facts: facts, export_uri: facts.export_is_data_url ? uri : null };
}"""
)

_VIB_ANIMATION_JS = (
    """async () => {
    const GEOM = """
    + _VIB_GEOM
    + """;
    const ns = window.ACPVibrationViewer;
    const { inst, native, container } = window.__vib;
    const realShapes = () => (native.shapes || []).filter((s) => !!s).length;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const sample = () => (inst.selectedAtoms({}) || [])
        .map((a) => [a.x, a.y, a.z]);
    const maxDelta = (a, b) => {
        let m = 0;
        for (let i = 0; i < a.length; i++) {
            for (let j = 0; j < 3; j++) m = Math.max(m, Math.abs(a[i][j] - b[i][j]));
        }
        return m;
    };
    const watchFrames = async (samples, gapMs) => {
        const out = [sample()];
        for (let i = 1; i < samples; i++) {
            await sleep(gapMs);
            out.push(sample());
        }
        let delta = 0;
        for (let i = 1; i < out.length; i++) delta = Math.max(delta, maxDelta(out[i - 1], out[i]));
        let disp = 0;
        for (let i = 0; i < out.length; i++) disp = Math.max(disp, maxDelta(out[i], GEOM.coords));
        return { delta: delta, displacement: disp };
    };
    const facts = {};
    await ns.loadVibrations("job-vib", "e1");

    ns.setDisplayMode("animation");
    facts.shapes_animation_static = realShapes();
    ns.playAnimation();
    facts.playing_after_play = ns.isAnimationActive();
    const pure = await watchFrames(6, 200);
    facts.pure_animation_frame_delta = pure.delta;
    facts.pure_animation_displacement = pure.displacement;
    facts.pure_animation_shapes_during_play = realShapes();
    ns.stopAnimation();
    facts.shapes_after_stop_in_animation = realShapes();
    facts.stop_delta_vs_equilibrium = maxDelta(sample(), GEOM.coords);

    ns.setDisplayMode("combo");
    facts.shapes_combo_static = realShapes();
    ns.playAnimation();
    const combo = await watchFrames(6, 200);
    facts.combo_frame_delta = combo.delta;
    facts.combo_displacement = combo.displacement;
    facts.combo_shapes_during_play = realShapes();
    facts.combo_playing_during_play = ns.isAnimationActive();

    ns.setHidden(true);
    facts.playing_after_hide = ns.isAnimationActive();
    const hidden = await watchFrames(3, 200);
    facts.hidden_frame_delta = hidden.delta;
    ns.setHidden(false);
    const unhide = await watchFrames(4, 200);
    facts.unhide_frame_delta = unhide.delta;
    facts.playing_after_unhide = ns.isAnimationActive();

    ns.stopAnimation();
    facts.stop2_delta_vs_equilibrium = maxDelta(sample(), GEOM.coords);
    facts.shapes_after_combo_stop = realShapes();
    facts.last_count_after_combo_stop = ns.arrowState._lastCount;

    container.remove();
    ns.setHidden(true);
    facts.shapes_while_hidden = realShapes();
    document.body.appendChild(container);
    ns.setHidden(false);
    ns.refreshArrows();
    facts.last_count_after_remount = ns.arrowState._lastCount;
    facts.real_shapes_after_remount = realShapes();
    facts.enabled_after_remount = ns.arrowState.enabled;

    ns.handleTeardown();
    facts.shapes_after_teardown = realShapes();
    facts.last_count_after_teardown = ns.arrowState._lastCount;
    facts.enabled_after_teardown = ns.arrowState.enabled;
    facts.playing_after_teardown = ns.isAnimationActive();
    return facts;
}"""
)

_VIB_TWO_INSTANCES_JS = (
    """async () => {
    const GEOM = """
    + _VIB_GEOM
    + """;
    const nsA = window.ACPVibrationViewer;
    // Second REAL module instance: the same production source evaluated
    // against a second namespace object (mirrors the Node test's second
    // sandbox).  Both instances draw through their OWN real $3Dmol scene
    // instance on their own real canvas.
    const src = await (await fetch("js/vibration_viewer.js")).text();
    const win2 = {};
    new Function("window", "document", src)(win2, document);
    const nsB = win2.ACPVibrationViewer;
    win2.ACPStructureViewer = {
        state: {
            displayedCoords: GEOM.coords,
            displayedSymbols: GEOM.symbols,
            displayedEntryId: GEOM.entryId,
        },
    };

    const containerB = document.createElement("div");
    containerB.id = "vib-viewer-b";
    containerB.style.width = "400px";
    containerB.style.height = "300px";
    document.body.appendChild(containerB);
    const instB = window.ACPVisualization.scene.create({
        container: containerB,
        options: { backgroundColor: "0x0e1419", antialias: true },
    });
    const nativeB = window.__vibNatives[window.__vibNatives.length - 1];
    if (instB) {
        instB.addModel(GEOM.xyz, "xyz");
        instB.addStyle({}, { sphere: { scale: 0.22 }, stick: { radius: 0.12 } });
        instB.frame({});
    }
    nsB._viewerImpl = () => instB;
    nsB._fetchImpl = (url, opts) => fetch(url, opts);

    const nativeA = window.__vib.native;
    const shapesOf = (n) => (n.shapes || []).filter((s) => !!s).length;
    const facts = { states_distinct: nsA.arrowState !== nsB.arrowState };

    await nsA.loadVibrations("job-vib", "e1");
    await nsB.loadVibrations("job-vib", "e1");
    facts.a_count = nsA.arrowState._lastCount;
    facts.b_count = nsB.arrowState._lastCount;
    facts.a_shapes = shapesOf(nativeA);
    facts.b_shapes = shapesOf(nativeB);

    win2.ACPStructureViewer.state.displayedEntryId = "other";
    nsB.refreshArrows();
    facts.b_count_missing = nsB.arrowState._lastCount;
    facts.b_shapes_missing = shapesOf(nativeB);
    facts.a_count_after_b_missing = nsA.arrowState._lastCount;
    facts.a_shapes_after_b_missing = shapesOf(nativeA);

    win2.ACPStructureViewer.state.displayedEntryId = GEOM.entryId;
    nsB.refreshArrows();
    facts.b_count_recovered = nsB.arrowState._lastCount;
    facts.b_shapes_recovered = shapesOf(nativeB);
    facts.a_count_after_b_recovered = nsA.arrowState._lastCount;
    facts.a_shapes_after_b_recovered = shapesOf(nativeA);
    return facts;
}"""
)


def _setup_vibration_viewer(harness: _Harness) -> dict[str, Any]:
    """Load the real vendor + viewer modules + vibration_viewer.js and mount a
    real scene instance on a real canvas (cold: arrowState.enabled stays at the
    module default false — never seeded)."""
    load_viewer_stack(harness, extra_scripts=(VIBRATION_JS,))
    return harness.page.evaluate(_VIB_SETUP_JS)


def test_vibration_cold_start_arrows_in_real_browser(frontend_page: _Harness) -> None:
    """D09/GAP-9: cold start (enabled=false) draws real3Dmol arrows from display intent."""
    harness = frontend_page
    page = harness.page
    identity = _identity()

    setup = _setup_vibration_viewer(harness)
    assert setup["vendor_loaded"], f"real vendor $3Dmol missing ({identity}): {setup}"
    assert setup["instance_created"], f"scene instance not created ({identity}): {setup}"
    assert setup["native_captured"], f"real $3Dmol viewer not captured ({identity}): {setup}"
    assert setup["enabled_before"] is False, (
        f"cold start must observe the module default enabled=false, nothing seeded "
        f"({identity}): {setup}"
    )
    assert not harness.page_errors, f"page JS errors ({identity}): {harness.page_errors}"

    result = page.evaluate(_VIB_COLD_START_JS)
    facts = result["facts"]
    export_uri = result["export_uri"]

    # D09 core: the FIRST production draw must place arrows on the real canvas
    # with NO manual enabled=true seeding — _lastCount AND the real 3Dmol
    # shape registry, both required.
    assert facts["enabled_before_load"] is False, (
        f"D09 regression: enabled must still be false at load time ({identity}): {facts}"
    )
    assert facts["selected_mode_index"] == 6, (
        f"defaultModeIndex must select the most-negative imaginary mode ({identity}): {facts}"
    )
    assert facts["last_count_cold"] > 0, (
        f"D09 regression: cold-start _lastCount must be > 0 — the old _arrowsVisible "
        f"gate blocked the first draw ({identity}); facts={facts}"
    )
    assert facts["last_count_cold"] == 3, (
        f"3 non-zero vectors must draw 3 arrows ({identity}): {facts}"
    )
    assert facts["real_shapes_cold"] > 0, (
        f"D09 regression: real 3Dmol shape count must be > 0 on cold start "
        f"({identity}); facts={facts}"
    )
    assert facts["real_shapes_cold"] == 3, (
        f"real shape registry must hold exactly the 3 drawn arrows ({identity}): {facts}"
    )
    assert facts["enabled_after_cold"] is True, (
        f"enabled is an OUTPUT of the successful draw ({identity}): {facts}"
    )
    assert facts["hint_cold"] is None, f"no hint when the draw succeeds ({identity}): {facts}"

    # Export the frequency view through the existing instance pngURI() path.
    assert facts["export_is_data_url"] and export_uri, (
        f"existing export path (instance pngURI) must return the frequency view "
        f"({identity}): {facts}"
    )
    assert facts["png_bytes"] > MIN_PNG_BYTES, (
        f"exported frequency-view PNG must be non-empty ({identity}): {facts}"
    )
    assert facts["png_nonbg_pixels"] >= MIN_NONBG_PIXELS, (
        f"exported frequency view must be non-empty — {facts['png_nonbg_pixels']} "
        f"non-background pixels < {MIN_NONBG_PIXELS} ({identity}); facts={facts}"
    )
    assert facts["png_unique_colors"] >= MIN_UNIQUE_COLORS, (
        f"exported frequency view must be non-empty — {facts['png_unique_colors']} "
        f"unique colors < {MIN_UNIQUE_COLORS} ({identity}); facts={facts}"
    )
    if VIBRATION_EXPORT_PNG:
        import base64

        out = Path(VIBRATION_EXPORT_PNG).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(export_uri.split(",", 1)[1]))
        facts["export_artifact"] = str(out)

    # Missing geometry -> hint + clear; a later valid geometry recovers.
    assert facts["last_count_missing"] == 0 and facts["real_shapes_missing"] == 0, (
        f"missing geometry must clear every arrow ({identity}): {facts}"
    )
    assert facts["enabled_missing"] is False, (
        f"enabled output false while unavailable ({identity}): {facts}"
    )
    assert facts["hint_missing"], f"a hint explains the missing geometry ({identity}): {facts}"
    assert facts["last_count_recovered"] == 3 and facts["real_shapes_recovered"] == 3, (
        f"recovery must redraw the 3 arrows ({identity}): {facts}"
    )

    # Display intent: animation hides the static arrows; combo/arrows redraw.
    assert facts["last_count_animation"] == 0 and facts["real_shapes_animation"] == 0, (
        f"animation mode must hide the static arrows ({identity}): {facts}"
    )
    assert facts["last_count_combo"] == 3 and facts["real_shapes_combo"] == 3, (
        f"combo must redraw the arrows ({identity}): {facts}"
    )
    assert facts["last_count_arrows"] == 3 and facts["real_shapes_arrows"] == 3, (
        f"plain arrows must redraw the arrows ({identity}): {facts}"
    )
    assert not harness.page_errors, f"page JS errors ({identity}): {harness.page_errors}"

    print(  # noqa: T201 — evidence surface (visible with -s / on failure)
        "D09 cold-start browser facts: "
        + json.dumps({"identity": identity, "facts": facts}, ensure_ascii=False)
    )


def test_vibration_animation_frames_combo_hide_remount_teardown(
    frontend_page: _Harness,
) -> None:
    """D09/GAP-9: animation changes real frames; combo shows both; hide/remount
    restores; teardown clears every shape."""
    harness = frontend_page
    page = harness.page
    identity = _identity()

    setup = _setup_vibration_viewer(harness)
    assert setup["instance_created"] and setup["native_captured"], (
        f"real scene instance required ({identity}): {setup}"
    )
    facts = page.evaluate(_VIB_ANIMATION_JS)

    # Pure animation: real model frames change and no static arrows.
    assert facts["shapes_animation_static"] == 0, (
        f"animation mode must not keep static arrows ({identity}): {facts}"
    )
    assert facts["playing_after_play"] is True, f"play starts the session ({identity}): {facts}"
    assert facts["pure_animation_frame_delta"] > 1e-3, (
        f"animation must change real model frames — frame delta "
        f"{facts['pure_animation_frame_delta']} ({identity}); facts={facts}"
    )
    assert facts["pure_animation_displacement"] > 1e-3, (
        f"animated frames must be displaced from equilibrium ({identity}): {facts}"
    )
    assert facts["pure_animation_shapes_during_play"] == 0, (
        f"pure animation shows no arrows ({identity}): {facts}"
    )
    assert facts["shapes_after_stop_in_animation"] == 0, (
        f"stop in animation mode restores equilibrium without arrows ({identity}): {facts}"
    )
    assert facts["stop_delta_vs_equilibrium"] < 1e-4, (
        f"stop must restore the exact equilibrium geometry ({identity}): {facts}"
    )

    # Combo shows BOTH: real moving frames and real arrows on the canvas.
    assert facts["shapes_combo_static"] == 3, (
        f"combo redraws the static arrows before play ({identity}): {facts}"
    )
    assert facts["combo_frame_delta"] > 1e-3, (
        f"combo animation must change real frames ({identity}): {facts}"
    )
    assert facts["combo_displacement"] > 1e-3, (
        f"combo frames must be displaced ({identity}): {facts}"
    )
    assert facts["combo_shapes_during_play"] > 0, (
        f"combo must show arrows AND animation together — real shapes during play "
        f"must be > 0 ({identity}); facts={facts}"
    )
    assert facts["combo_shapes_during_play"] == 3, (
        f"combo frames carry the 3 displaced arrows ({identity}): {facts}"
    )
    assert facts["combo_playing_during_play"] is True, (
        f"combo keeps the session playing ({identity}): {facts}"
    )

    # Hide stops ticking without losing the playing state; unhide resumes.
    assert facts["playing_after_hide"] is True, (
        f"hide must not lose the playing state ({identity}): {facts}"
    )
    assert facts["hidden_frame_delta"] < 1e-9, (
        f"hidden ticks must stop — frames changed while hidden ({identity}): {facts}"
    )
    assert facts["unhide_frame_delta"] > 1e-3, f"unhide must resume ticking ({identity}): {facts}"
    assert facts["playing_after_unhide"] is True, (
        f"session survives hide/unhide ({identity}): {facts}"
    )

    # Combo stop restores equilibrium + re-applies the static arrows.
    assert facts["stop2_delta_vs_equilibrium"] < 1e-4, (
        f"stop restores the equilibrium geometry ({identity}): {facts}"
    )
    assert facts["shapes_after_combo_stop"] == 3 and facts["last_count_after_combo_stop"] == 3, (
        f"stop in combo re-applies the static arrows ({identity}): {facts}"
    )

    # Hide/remount restores the arrows.
    assert facts["shapes_while_hidden"] == 3, (
        f"hiding must not wipe the drawn arrows ({identity}): {facts}"
    )
    assert facts["last_count_after_remount"] == 3 and facts["real_shapes_after_remount"] == 3, (
        f"remount must restore the 3 arrows ({identity}): {facts}"
    )
    assert facts["enabled_after_remount"] is True, (
        f"remount draw reports enabled output true ({identity}): {facts}"
    )

    # Teardown clears every shape and resets the state.
    assert facts["shapes_after_teardown"] == 0, (
        f"handleTeardown must clear every real shape ({identity}): {facts}"
    )
    assert facts["last_count_after_teardown"] == 0, (
        f"teardown resets the UI count ({identity}): {facts}"
    )
    assert facts["enabled_after_teardown"] is False, (
        f"teardown resets enabled ({identity}): {facts}"
    )
    assert facts["playing_after_teardown"] is False, (
        f"teardown stops any session ({identity}): {facts}"
    )
    assert not harness.page_errors, f"page JS errors ({identity}): {harness.page_errors}"

    print(  # noqa: T201 — evidence surface (visible with -s / on failure)
        "D09 animation/teardown browser facts: "
        + json.dumps({"identity": identity, "facts": facts}, ensure_ascii=False)
    )


def test_vibration_two_instances_do_not_share_state(frontend_page: _Harness) -> None:
    """D09/GAP-9: two real module instances keep independent draw state."""
    harness = frontend_page
    page = harness.page
    identity = _identity()

    setup = _setup_vibration_viewer(harness)
    assert setup["instance_created"] and setup["native_captured"], (
        f"real scene instance required ({identity}): {setup}"
    )
    facts = page.evaluate(_VIB_TWO_INSTANCES_JS)

    assert facts["states_distinct"], (
        f"the two module instances must own distinct arrowState objects ({identity}): {facts}"
    )
    assert facts["a_count"] == 3 and facts["a_shapes"] == 3, (
        f"instance A draws 3 arrows on its own canvas ({identity}): {facts}"
    )
    assert facts["b_count"] == 3 and facts["b_shapes"] == 3, (
        f"instance B draws 3 arrows on its own canvas ({identity}): {facts}"
    )
    assert facts["b_count_missing"] == 0 and facts["b_shapes_missing"] == 0, (
        f"B losing its geometry draws nothing ({identity}): {facts}"
    )
    assert facts["a_count_after_b_missing"] == 3 and facts["a_shapes_after_b_missing"] == 3, (
        f"A's arrows are untouched by B's refresh ({identity}): {facts}"
    )
    assert facts["b_count_recovered"] == 3 and facts["b_shapes_recovered"] == 3, (
        f"B recovers independently ({identity}): {facts}"
    )
    assert facts["a_count_after_b_recovered"] == 3 and facts["a_shapes_after_b_recovered"] == 3, (
        f"A still holds its 3 arrows after B's recovery ({identity}): {facts}"
    )
    assert not harness.page_errors, f"page JS errors ({identity}): {harness.page_errors}"

    print(  # noqa: T201 — evidence surface (visible with -s / on failure)
        "D09 two-instance browser facts: "
        + json.dumps({"identity": identity, "facts": facts}, ensure_ascii=False)
    )
