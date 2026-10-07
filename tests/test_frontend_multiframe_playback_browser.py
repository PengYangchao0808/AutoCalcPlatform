# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Browser-level regression test for the multi-frame playback bar.

Reproduces the reported bug: after opening a multi-frame XYZ, the bottom
playback bar (``#frame-controller``) used to vanish on the first playback
tick because ``updateFrameController()`` gated its visibility on the retired
``data-tab="3d"`` id instead of the live ``"structure"`` id.  The test loads
a 2-frame XYZ through the production bridge
(``window._svLoadXyzToViewer``), switches to the structure tab exactly like
the real file-open flow (``openJobFile`` → ``setViewerTab("structure")``),
clicks Play, waits event-driven for the frame counter to advance, and
asserts the bar is STILL visible.

Every test is marked ``@pytest.mark.slow`` (module-level ``pytestmark``).
Prerequisites (CI installs these on one matrix leg)::

    pip install -e '.[browser]'
    python -m playwright install chromium
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Generator

import pytest

_playwright = pytest.importorskip(
    "playwright",
    reason="playwright not installed — pip install -e '.[browser]'",
)

try:
    from playwright.sync_api import Browser, Page, sync_playwright  # noqa: F401
except ImportError:  # pragma: no cover — old playwright without sync_api
    pytest.skip(
        "playwright.sync_api unavailable — upgrade playwright >= 1.40",
        allow_module_level=True,
    )

pytestmark = pytest.mark.slow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Test molecule
# ---------------------------------------------------------------------------

# Frame 1: the n-butane geometry used across the browser suites (14 atoms).
# Frame 2: the same 14 atoms with the terminal methyl (C3 and its hydrogens,
# indices 3/10/11/12/13) translated by +0.2 Å along x, so the XYZ parses to
# exactly 2 frames of identical atom count.
_BUTANE_FRAME1 = """\
14
butane frame 1
C   0.000000   0.000000   0.000000
C   1.500000   0.000000   0.000000
C   2.250000   1.299038   0.000000
C   3.750000   1.299038   0.000000
H  -0.500000   0.890000   0.000000
H  -0.500000  -0.890000   0.000000
H   0.000000   0.890000   0.890000
H   2.000000  -0.890000   0.000000
H   1.750000   1.299038   0.890000
H   2.250000   2.189038   0.890000
H   4.250000   1.299038   0.890000
H   4.250000   1.299038  -0.890000
H   3.750000   0.409038   0.890000
H   3.750000   2.189038   0.890000
"""

_METHYL_ATOMS = (3, 10, 11, 12, 13)


def _translate_methyl(frame: str, delta: float) -> str:
    """Return the frame with the terminal methyl atoms shifted by ``delta`` Å in x."""
    lines = frame.splitlines()
    head, atom_lines = lines[:2], lines[2:]
    shifted: list[str] = []
    for index, line in enumerate(atom_lines):
        if index not in _METHYL_ATOMS:
            shifted.append(line)
            continue
        elem, x, y, z = line.split()
        shifted.append(f"{elem}  {float(x) + delta:.6f}  {float(y)}  {float(z)}")
    return "\n".join(head + shifted) + "\n"


_FRAME2 = _translate_methyl(_BUTANE_FRAME1, 0.2)
TWO_FRAME_XYZ = _BUTANE_FRAME1 + "\n" + _FRAME2


# ---------------------------------------------------------------------------
# Helpers (same semantics as the measure/edit browser suite)
# ---------------------------------------------------------------------------


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


def _assert_no_page_errors(errors: list[str]) -> None:
    """Fail the test when any uncaught page error was captured."""
    assert not errors, "Uncaught page errors:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def server_url() -> Generator[str, None, None]:
    """Boot the real FastAPI app on an ephemeral port for the test session."""
    import os
    import tempfile
    from pathlib import Path

    import uvicorn

    tmp_root = Path(tempfile.mkdtemp(prefix="acp_multiframe_playback_test_"))

    # server.py reads ACP_RUN_ROOT at import time for its module-level app;
    # the explicit run_root below is still the authoritative one.
    os.environ["ACP_RUN_ROOT"] = str(tmp_root)

    from acp.api.server import create_app

    app = create_app(run_root=tmp_root)
    port = _find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

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

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def browser_errors() -> list[str]:
    """Accumulator for uncaught page errors observed during one test."""
    return []


@pytest.fixture()
def browser_page(server_url: str, browser_errors: list[str]) -> Generator[Page, None, None]:
    """Launch headless Chromium, open the Workbench and initialize the viewer.

    Skips (rather than fails) when Chromium binaries or the 3Dmol library are
    unavailable; uncaught page errors are collected into ``browser_errors``
    and asserted at the end of each test via ``_assert_no_page_errors``.

    Teardown is guaranteed by a ``finally`` block: any failure after
    ``sync_playwright().start()`` still runs the cleanup chain (a leaked
    sync Playwright context makes every later test in the process die with
    "Sync API inside the asyncio loop").
    """
    pw_ctx = sync_playwright().start()
    browser: Browser | None = None
    context = None
    try:
        try:
            if not _chromium_launchable(pw_ctx):
                pytest.skip(
                    "Chromium binaries not installed — run 'python -m playwright install chromium'"
                )
            browser = pw_ctx.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(
                f"Cannot launch Chromium ({exc}) — run 'python -m playwright install chromium'"
            )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, device_scale_factor=1
        )
        # Hermetic: abort any 3Dmol CDN request (local vendor is authoritative
        # in ACP_Workbench_v2.html) so the suite never depends on network
        # access and a CDN regression cannot block DOMContentLoaded.
        context.route("https://3Dmol.org/**", lambda route: route.abort())
        page = context.new_page()
        page.on("pageerror", lambda exc: browser_errors.append(str(exc)))

        # The Workbench bootstrap (DOMContentLoaded handler) awaits several network
        # loads BEFORE calling setupEventListeners() (ACP_Workbench_v2.html:25352),
        # which wires the frame-play/prev/next/slider listeners. Clicking before
        # that silently no-ops, so mark the moment the listeners exist and wait
        # for it below.
        context.add_init_script(
            """
        (() => {
          window.__acpEventListenersReady = false;
          const timer = setInterval(() => {
            if (performance.now() > 120000) { clearInterval(timer); return; }
            if (typeof setupEventListeners === 'function' && !setupEventListeners.__acpWrapped) {
              clearInterval(timer);
              const orig = setupEventListeners;
              window.setupEventListeners = function () {
                window.__acpEventListenersReady = true;
                return orig.apply(this, arguments);
              };
              window.setupEventListeners.__acpWrapped = true;
            }
          }, 5);
        })();
        """
        )

        page.goto(server_url, wait_until="domcontentloaded")

        try:
            page.wait_for_function("typeof $3Dmol !== 'undefined'", timeout=15_000)
        except Exception:
            error_text = page.evaluate("document.getElementById('viewer-empty')?.textContent || ''")
            pytest.skip(
                f"3Dmol CDN unreachable (frontend shows: '{error_text.strip()}'). "
                "Tests require network access to https://3Dmol.org/build/3Dmol-min.js"
            )

        page.wait_for_function(
            "typeof initViewer === 'function' && typeof renderMolDoc === 'function'",
            timeout=120_000,
        )

        initialized = page.evaluate("() => initViewer() === true")
        assert initialized, "initViewer() failed — 3Dmol viewer could not be created"

        page.wait_for_function(
            "typeof window._svLoadXyzToViewer === 'function'",
            timeout=30_000,
        )

        # Frame controls are unusable (clicks no-op) until the bootstrap wired them.
        page.wait_for_function("window.__acpEventListenersReady === true", timeout=60_000)

        yield page
    finally:
        # Guaranteed teardown: each step guarded so a raising close cannot
        # skip pw_ctx.stop() — stop is the LAST and MUST-run step.
        try:
            if context is not None:
                context.close()
        except Exception as exc:
            logger.debug("browser_page teardown: context.close() failed: %s", exc)
        try:
            if browser is not None:
                browser.close()
        except Exception as exc:
            logger.debug("browser_page teardown: browser.close() failed: %s", exc)
        try:
            pw_ctx.stop()
        except Exception as exc:
            logger.debug("browser_page teardown: pw_ctx.stop() failed: %s", exc)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_multiframe_playback_bar_stays_visible_during_play(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """Multi-frame XYZ: the playback bar is visible before AND after Play
    advances at least one frame; Stop restores the idle state."""
    page = browser_page

    page.evaluate("([xyz]) => window._svLoadXyzToViewer(xyz, 'playback_test')", [TWO_FRAME_XYZ])
    # Mirror the real file-open flow (openJobFile -> setViewerTab("structure")).
    page.evaluate("async () => { await setViewerTab('structure'); }")

    assert page.is_visible("#frame-controller"), (
        "frame controller must be visible on the structure tab with >1 frames"
    )
    assert page.locator("#frame-total").text_content() == "2", (
        "multi-frame XYZ must parse to exactly 2 frames"
    )

    before = page.evaluate("() => molDoc.currentFrame")
    assert before == 0, f"fresh load must start at frame 0, got {before!r}"

    page.click("#frame-play")
    assert page.locator("#frame-play").text_content() == "Stop", (
        "Play button must switch to Stop while playback is running"
    )

    # Event-driven wait for the first playback tick (900 ms interval) — no
    # sleeps. The predicate returns the frame at the passing instant, so the
    # "frames advanced" proof cannot race the 2-frame 0<->1 wrap-around.
    frame_at_tick = page.wait_for_function(
        "() => molDoc.currentFrame >= 1 ? molDoc.currentFrame : false",
        timeout=5000,
    ).json_value()
    assert frame_at_tick >= 1, f"frames must have advanced during playback, got {frame_at_tick!r}"

    # THE regression: updateFrameController() runs on every tick (setFrame)
    # and must keep the bar visible on the structure tab.
    assert page.is_visible("#frame-controller"), (
        "frame controller must STAY visible while playback advances frames"
    )
    post_play = page.evaluate(
        "() => ({ display: document.getElementById('frame-controller').style.display, "
        "stopLabel: document.getElementById('frame-play').textContent === 'Stop', "
        "playing: framePlayTimer !== null })"
    )
    assert post_play["display"] == "flex", (
        "frame controller inline display must stay flex during playback, "
        f"got {post_play['display']!r}"
    )
    assert post_play["stopLabel"], "Play button must still read Stop while playback is running"
    assert post_play["playing"], "playback interval must still be armed after the tick"

    page.click("#frame-play")
    timer = page.evaluate("() => framePlayTimer === null")
    assert timer, "playback interval must be cleared after clicking Stop"
    label = page.locator("#frame-play").text_content() or ""
    assert label != "Stop", "Play button must leave the running state after clicking Stop"

    _assert_no_page_errors(browser_errors)
