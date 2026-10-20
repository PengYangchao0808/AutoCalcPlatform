# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Real-Chromium frontend viewer tests (D08 NMR canvas lifecycle; D09 later).

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

Non-vacuity (QA only, never production): ``ACP_FRONTEND_NMR_JS=/path/to/copy.js``
re-points the page at a scratch copy of ``nmr.js``; reintroducing
``container.textContent = ""`` into the success path of that copy must make the
browser test FAIL on the canvas assertions. The production file is never
patched for this.

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
PAGE_URL = "http://acp.test/__acp_frontend_browser__.html"
_HARNESS_PATH = "/" + PAGE_URL.split("://", 1)[-1].split("/", 1)[-1]

# QA-only non-vacuity hook: serve a scratch copy of nmr.js at the production
# URL so the page bootstrap stays identical (never patch production code).
NMR_JS_OVERRIDE = os.environ.get("ACP_FRONTEND_NMR_JS", "")

# Real 3-atom geometry (water) injected at the network boundary.
WATER_XYZ = "3\nwater\nO 0 0 0\nH 0 0 0.96\nH 0 0.93 -0.24\n"

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
    "</div></body></html>"
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
        if path == "/" + NMR_JS and NMR_JS_OVERRIDE:
            override = Path(NMR_JS_OVERRIDE).resolve()
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
# Vibration (D09) — added by task 23 on this same harness.
# ---------------------------------------------------------------------------
