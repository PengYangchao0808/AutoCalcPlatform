# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Browser-level tests for 3Dmol viewer framing in ACP Workbench v2.

These tests launch a real headless Chromium via Playwright to verify that the
3Dmol viewers center the rendered molecule on the container.  The bug being
guarded: ``zoomTo`` fired before the container reached its final size, causing
the molecule to render off-center.

All tests are marked ``@pytest.mark.slow`` — they are skipped in the default
``pytest -m "not slow"`` run and require ``--run-slow`` to execute.

Prerequisites (CI installs these automatically on one matrix leg)::

    pip install -e '.[browser]'
    python -m playwright install --with-deps chromium
"""

from __future__ import annotations

import math
import socket
import threading
import time
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Guard: skip the entire module if playwright is not installed
# ---------------------------------------------------------------------------
_playwright = pytest.importorskip(
    "playwright",
    reason="playwright not installed — pip install -e '.[browser]'",
)

try:
    from playwright.sync_api import sync_playwright, Page, Browser  # noqa: F401
except ImportError:  # pragma: no cover — old playwright without sync_api
    pytest.skip(
        "playwright.sync_api unavailable — upgrade playwright >= 1.40",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Asymmetric7-atom molecule offset far from origin (center ≈ 30, -20, 15).
# An off-center camera is easily detectable because the bounding-box centroid
# is far from (0,0,0).
_OFFSET_XYZ = """\
7
test asymmetric molecule
C   30.000000  -20.000000   15.000000
H   31.500000  -20.000000   15.000000
H   30.000000  -18.500000   15.000000
H   30.000000  -20.000000   16.500000
O   28.000000  -21.000000   14.000000
N   32.000000  -22.000000   16.000000
C   29.000000  -18.000000   13.000000
"""

# Expected bounding-box centroid (computed from the XYZ above).
_EXPECTED_CX = (28.0 + 32.0) / 2  # 30.0
_EXPECTED_CY = (-22.0 + -18.0) / 2  # -20.0
_EXPECTED_CZ = (13.0 + 16.5) / 2  # 14.75


def _find_free_port() -> int:
    """Return an OS-assigned ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chromium_launchable(playwright_instance) -> bool:
    """Try to launch Chromium headless; return False if binaries are missing."""
    try:
        browser = playwright_instance.chromium.launch(headless=True)
        browser.close()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    """Boot the real FastAPI app on an ephemeral port for the test session.

    The server runs in a daemon thread; ``create_app`` is called with a
    temporary ``run_root`` so no persistent state is written.
    """
    import os
    import tempfile
    from pathlib import Path

    import uvicorn

    tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))

    # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
    # has a module-level ``app = create_app()`` that reads this env var.
    os.environ["ACP_RUN_ROOT"] = str(tmp_root)

    # Import the app factory — this is the canonical entry point.
    from acp.api.server import create_app

    app = create_app(run_root=tmp_root)
    port = _find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to accept connections (up to 15 s).
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.fail(f"Server did not start within 15 s on {url}")

    yield url

    # Teardown: signal the server to stop.
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def browser_page(server_url: str) -> Generator[Page, None, None]:
    """Launch headless Chromium and navigate to the ACP Workbench.

    Skips the test (rather than failing) when Chromium binaries are not
    installed — the error message tells the developer how to install them.
    """
    pw_ctx = sync_playwright().start()
    try:
        if not _chromium_launchable(pw_ctx):
            pytest.skip(
                "Chromium binaries not installed — "
                "run 'python -m playwright install --with-deps chromium'"
            )
        browser: Browser = pw_ctx.chromium.launch(headless=True)
    except Exception as exc:
        pw_ctx.stop()
        pytest.skip(
            f"Cannot launch Chromium ({exc}) — "
            "run 'python -m playwright install --with-deps chromium'"
        )

    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        device_scale_factor=1,
    )
    page = context.new_page()

    # Navigate and wait for the page to be ready.
    page.goto(server_url, wait_until="domcontentloaded")

    # 3Dmol is loaded from CDN (https://3Dmol.org/build/3Dmol-min.js).
    # If the CDN is unreachable, skip with a clear reason.
    try:
        page.wait_for_function(
            "typeof $3Dmol !== 'undefined'",
            timeout=15_000,
        )
    except Exception:
        # Check if it's a CDN issue by looking for the error message the
        # frontend shows when 3Dmol fails to load.
        error_text = page.evaluate(
            "document.getElementById('viewer-empty')?.textContent || ''"
        )
        browser.close()
        pw_ctx.stop()
        pytest.skip(
            f"3Dmol CDN unreachable (frontend shows: '{error_text.strip()}'). "
            "Tests require network access to https://3Dmol.org/build/3Dmol-min.js"
        )

    # Wait for the app's viewer globals to be defined.
    page.wait_for_function(
        "typeof initViewer === 'function' && typeof renderMolDoc === 'function'",
        timeout=10_000,
    )

    # Initialize the viewer (equivalent to the frontend's DOMContentLoaded).
    page.evaluate("initViewer()")

    yield page

    # Teardown: close browser cleanly.
    context.close()
    browser.close()
    pw_ctx.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestMainViewerFraming:
    """Verify the main 3D viewer centers molecules after first load."""

    def test_main_viewer_first_load_centers_model(
        self, browser_page: Page
    ) -> None:
        """After loading a molecule, ``frame3DViewer`` should center the
        camera on the bounding-box center (not at the origin).

        Contract tested:
        - ``mainViewerFraming.settled`` becomes ``true``
        - ``viewer.getView()`` look-at (first 3 entries) ≈ bbox center
        - Canvas pixel size matches container layout size
        """
        page = browser_page

        # Inject the offset molecule into molDoc.frames and trigger render.
        page.evaluate(
            """(xyz) => {
                const frames = parseMultiFrameXYZ(xyz);
                if (!frames.length) throw new Error('XYZ parse returned 0 frames');
                molDoc.frames = frames;
                molDoc.currentFrame = 0;
                renderMolDoc({ zoomTo: true });
            }""",
            _OFFSET_XYZ,
        )

        # Wait for the framing to settle (mainViewerFraming.settled === true).
        # If the parallel frontend agent hasn't landed frame3DViewer/
        # scheduleViewerFraming yet, this will time out — that's expected and
        # the test will fail with a clear message.
        try:
            page.wait_for_function(
                "typeof mainViewerFraming !== 'undefined' && "
                "mainViewerFraming.settled === true",
                timeout=10_000,
            )
        except Exception:
            pytest.fail(
                "mainViewerFraming.settled never became true within 10 s. "
                "Either the framing helpers are not present (parallel agent "
                "hasn't landed) or the container never stabilised."
            )

        # --- Assertion (a): view center ≈ molecule bbox center ---
        view = page.evaluate("viewer.getView()")
        assert isinstance(view, list) and len(view) >= 3, (
            f"viewer.getView() returned unexpected shape: {view}"
        )

        # Compute bbox center from the rendered model (ground truth).
        bbox = page.evaluate(
            """() => {
                const atoms = currentModel.selectedAtoms({});
                if (!atoms.length) return null;
                let minX = Infinity, maxX = -Infinity;
                let minY = Infinity, maxY = -Infinity;
                let minZ = Infinity, maxZ = -Infinity;
                atoms.forEach(a => {
                    if (a.x < minX) minX = a.x; if (a.x > maxX) maxX = a.x;
                    if (a.y < minY) minY = a.y; if (a.y > maxY) maxY = a.y;
                    if (a.z < minZ) minZ = a.z; if (a.z > maxZ) maxZ = a.z;
                });
                return {
                    cx: (minX + maxX) / 2,
                    cy: (minY + maxY) / 2,
                    cz: (minZ + maxZ) / 2
                };
            }"""
        )
        assert bbox is not None, "currentModel.selectedAtoms({}) returned 0 atoms"

        # The view translation (first 3 entries of getView()) should be
        # approximately the negative of the bbox center, because center()
        # translates the model group so the bbox centroid sits at the origin.
        # Tolerance: ≤ 1.0 Å per axis.
        TOL = 1.0
        view_x, view_y, view_z = view[0], view[1], view[2]
        assert math.fabs(view_x - (-bbox["cx"])) <= TOL, (
            f"view X {view_x:.3f} != -bbox.cx {-bbox['cx']:.3f} (tol {TOL})"
        )
        assert math.fabs(view_y - (-bbox["cy"])) <= TOL, (
            f"view Y {view_y:.3f} != -bbox.cy {-bbox['cy']:.3f} (tol {TOL})"
        )
        assert math.fabs(view_z - (-bbox["cz"])) <= TOL, (
            f"view Z {view_z:.3f} != -bbox.cz {-bbox['cz']:.3f} (tol {TOL})"
        )

        # --- Assertion (b): canvas CSS size ≈ container size ---
        # 3Dmol renders a 2x internal drawing buffer (canvas.width == 2 * CSS
        # width even at devicePixelRatio=1); resize() only guarantees the CSS
        # size, so compare style.width/style.height against the container.
        sizes = page.evaluate(
            """() => {
                const canvas = document.querySelector('#viewer-3d canvas');
                const container = document.getElementById('viewer-3d');
                if (!canvas || !container) return null;
                return {
                    canvasW: parseFloat(canvas.style.width),
                    canvasH: parseFloat(canvas.style.height),
                    containerW: container.clientWidth,
                    containerH: container.clientHeight
                };
            }"""
        )
        assert sizes is not None, "Canvas or container element not found"
        SIZE_TOL = 4.0  # pixels
        assert math.fabs(sizes["canvasW"] - sizes["containerW"]) <= SIZE_TOL, (
            f"Canvas width {sizes['canvasW']:.1f} != container {sizes['containerW']:.1f} "
            f"(tol {SIZE_TOL}px)"
        )
        assert math.fabs(sizes["canvasH"] - sizes["containerH"]) <= SIZE_TOL, (
            f"Canvas height {sizes['canvasH']:.1f} != container {sizes['containerH']:.1f} "
            f"(tol {SIZE_TOL}px)"
        )

    def test_main_viewer_resize_after_settle_preserves_user_camera(
        self, browser_page: Page
    ) -> None:
        """After framing settles, a manual camera change followed by a
        container resize must NOT re-frame — the user's camera is preserved.

        Contract tested:
        - Post-settle ResizeObserver does resize+render only (no center/zoomTo)
        - ``viewer.getView()`` after resize matches the user-adjusted view
        """
        page = browser_page

        # Load molecule and wait for framing to settle.
        page.evaluate(
            """(xyz) => {
                const frames = parseMultiFrameXYZ(xyz);
                molDoc.frames = frames;
                molDoc.currentFrame = 0;
                renderMolDoc({ zoomTo: true });
            }""",
            _OFFSET_XYZ,
        )

        try:
            page.wait_for_function(
                "typeof mainViewerFraming !== 'undefined' && "
                "mainViewerFraming.settled === true",
                timeout=10_000,
            )
        except Exception:
            pytest.fail(
                "mainViewerFraming.settled never became true — "
                "cannot test post-settle resize behaviour."
            )

        # Record the post-framing view.
        view_before = page.evaluate("viewer.getView().slice()")
        assert isinstance(view_before, list) and len(view_before) >= 3

        # Apply a manual camera offset (user pans the view) via setView;
        # translate() does not move the getView() position in this 3Dmol build.
        page.evaluate(
            "var v = viewer.getView().slice(); v[0] += 5; v[1] += 5; "
            "viewer.setView(v); viewer.render();"
        )
        view_after_pan = page.evaluate("viewer.getView().slice()")

        # Sanity: the view must have changed after the pan.
        changed = any(
            math.fabs(view_after_pan[i] - view_before[i]) > 0.01
            for i in range(min(len(view_before), len(view_after_pan), 3))
        )
        assert changed, "setView pan did not change the view — 3Dmol API may differ"

        # Resize the viewport to trigger the ResizeObserver.
        page.set_viewport_size({"width": 960, "height": 600})

        # Wait for the debounced observer to fire (100 ms timeout in JS + margin).
        page.wait_for_timeout(500)

        # The view must match the user-adjusted view, NOT the original framing.
        view_after_resize = page.evaluate("viewer.getView().slice()")
        CAM_TOL = 0.5
        for i in range(min(len(view_after_pan), len(view_after_resize), 3)):
            assert math.fabs(view_after_resize[i] - view_after_pan[i]) <= CAM_TOL, (
                f"Axis {i}: view after resize {view_after_resize[i]:.3f} != "
                f"user view {view_after_pan[i]:.3f} (tol {CAM_TOL}). "
                "Post-settle resize re-framed the camera — this is the bug."
            )


@pytest.mark.slow
class TestPreviewViewerFraming:
    """Verify the new-task modal preview viewer centres molecules."""

    def test_preview_viewer_modal_centers_model(
        self, browser_page: Page
    ) -> None:
        """Open the new-task modal, load a structure into the preview viewer,
        and verify that the camera centres on the molecule.

        Contract tested:
        - ``previewViewerFraming.settled`` becomes ``true``
        - ``previewViewer.getView()`` look-at ≈ bbox center

        NOTE: ``previewViewerFraming`` is introduced by the parallel framing
        agent.  If it hasn't landed, this test will fail at the
        ``wait_for_function`` step — the failure message explains the
        dependency.  The modal open + viewer injection path is validated
        independently of the framing assertion.
        """
        page = browser_page

        # Open the new-task modal.
        opened = page.evaluate(
            """() => {
                if (typeof openModal !== 'function') return false;
                openModal();
                return document.getElementById('job-modal')?.style.display !== 'none';
            }"""
        )
        if not opened:
            pytest.skip(
                "openModal() function not found or modal did not open — "
                "cannot test preview viewer framing."
            )

        # Ensure the preview viewer container exists and is visible.
        container_visible = page.evaluate(
            """() => {
                const c = document.getElementById('structure-preview-3d');
                return !!(c && c.offsetParent !== null);
            }"""
        )
        if not container_visible:
            # The preview container may be hidden until structures are parsed.
            # Try to make it visible by triggering a parse with inline XYZ.
            page.evaluate(
                """(xyz) => {
                    if (typeof renderPreviewStructure3D === 'function') {
                        renderPreviewStructure3D({ has_3d: true, xyz: xyz });
                    }
                }""",
                _OFFSET_XYZ,
            )

        # Call renderPreviewStructure3D directly with the offset molecule.
        # This is the best-effort path: if the modal cannot be opened
        # standalone (requires backend data), we still exercise the viewer.
        page.evaluate(
            """(xyz) => {
                if (typeof renderPreviewStructure3D !== 'function') return;
                renderPreviewStructure3D({ has_3d: true, xyz: xyz });
            }""",
            _OFFSET_XYZ,
        )

        # Wait for previewViewerFraming to settle.
        # CONTRACT ASSUMPTION: The parallel framing agent introduces
        # previewViewerFraming as a global with the same shape as
        # mainViewerFraming.  If this variable doesn't exist, the test
        # fails with a clear explanation.
        try:
            page.wait_for_function(
                "typeof previewViewerFraming !== 'undefined' && "
                "previewViewerFraming.settled === true",
                timeout=10_000,
            )
        except Exception:
            # Close the modal before failing to avoid leaking state.
            page.evaluate(
                "() => { if (typeof closeModal === 'function') closeModal(); }"
            )
            pytest.fail(
                "previewViewerFraming.settled never became true within 10 s. "
                "Either previewViewerFraming is not defined (parallel framing "
                "agent hasn't landed) or the preview container never stabilised."
            )

        # Assert: view center ≈ bbox center.
        result = page.evaluate(
            """() => {
                if (!previewViewer || !previewModel) return null;
                const atoms = previewModel.selectedAtoms({});
                if (!atoms.length) return null;
                let minX = Infinity, maxX = -Infinity;
                let minY = Infinity, maxY = -Infinity;
                let minZ = Infinity, maxZ = -Infinity;
                atoms.forEach(a => {
                    if (a.x < minX) minX = a.x; if (a.x > maxX) maxX = a.x;
                    if (a.y < minY) minY = a.y; if (a.y > maxY) maxY = a.y;
                    if (a.z < minZ) minZ = a.z; if (a.z > maxZ) maxZ = a.z;
                });
                const view = previewViewer.getView();
                return {
                    view: view,
                    cx: (minX + maxX) / 2,
                    cy: (minY + maxY) / 2,
                    cz: (minZ + maxZ) / 2
                };
            }"""
        )

        # Close the modal to clean up.
        page.evaluate(
            "() => { if (typeof closeModal === 'function') closeModal(); }"
        )

        assert result is not None, (
            "previewViewer or previewModel not available after framing settled"
        )

        TOL = 1.0
        vx, vy, vz = result["view"][0], result["view"][1], result["view"][2]
        assert math.fabs(vx - (-result["cx"])) <= TOL, (
            f"Preview view X {vx:.3f} != -bbox.cx {-result['cx']:.3f} (tol {TOL})"
        )
        assert math.fabs(vy - (-result["cy"])) <= TOL, (
            f"Preview view Y {vy:.3f} != -bbox.cy {-result['cy']:.3f} (tol {TOL})"
        )
        assert math.fabs(vz - (-result["cz"])) <= TOL, (
            f"Preview view Z {vz:.3f} != -bbox.cz {-result['cz']:.3f} (tol {TOL})"
        )
