# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Browser-level tests for the structure-viewer measure/edit pipeline.

These tests launch a real headless Chromium via Playwright and drive the
native-index pick path (``window.__acpTestPickAtom``), the
``ACPGeometryStore`` measurement state, and the ``ACPStructureEditor``
edit/undo/redo/reset transactions.  Unlike the framing suite they do NOT
depend on ResizeObserver settling, so they run in headless CI as-is.

Every test is marked ``@pytest.mark.slow`` (module-level ``pytestmark``);
Prerequisites (CI installs these on one matrix leg)::

    pip install -e '.[browser]'
    python -m playwright install --with-deps chromium
"""

from __future__ import annotations

import math
import re
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


# ---------------------------------------------------------------------------
# Test molecules
# ---------------------------------------------------------------------------

# n-butane, C0-C1-C2-C3 chain with C-C = 1.5 Å.  Hydrogens are placed so the
# inferred-adjacency graph has NO bridging H (an H within the 1.3*(rC+rH)
# cutoff of two carbons would fabricate a ring and make C0-C1 ring-blocked).
_BUTANE_XYZ = """\
14
butane
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

# Regular hexagon benzene, C-C = 1.39 Å, C-H = 1.09 Å.  Cutting C0-C1 leaves
# the ring connected, so the edit validator must answer "ring_bond".
_BENZENE_XYZ = """\
12
benzene
C   1.390000   0.000000   0.000000
C   0.695000   1.203775   0.000000
C  -0.695000   1.203775   0.000000
C  -1.390000   0.000000   0.000000
C  -0.695000  -1.203775   0.000000
C   0.695000  -1.203775   0.000000
H   2.480000   0.000000   0.000000
H   1.240000   2.147743   0.000000
H  -1.240000   2.147743   0.000000
H  -2.480000   0.000000   0.000000
H  -1.240000  -2.147743   0.000000
H   1.240000  -2.147743   0.000000
"""

_DISTANCE_TOL = 0.02  # Å


# ---------------------------------------------------------------------------
# Helpers
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


def _load_molecule(page: Page, xyz: str, entry_id: str = "measure_edit_test") -> int:
    """Load XYZ text through the production bridge and return the atom count."""
    result = page.evaluate(
        """([xyzText, entryId]) => {
            window._svLoadXyzToViewer(xyzText, entryId);
            return {
                symbolCount: window.ACPGeometryStore.state.symbols.length,
                renderedAtoms: currentModelAtoms.length,
                revision: window.ACPGeometryStore.state.revision,
            };
        }""",
        [xyz, entry_id],
    )
    assert result["symbolCount"] > 0, "store received no symbols from the XYZ bridge"
    assert result["renderedAtoms"] == result["symbolCount"], (
        f"canvas model has {result['renderedAtoms']} atoms, store has {result['symbolCount']}"
    )
    assert result["revision"] == 1, "fresh load must set revision 1"
    return int(result["symbolCount"])


def _arm_distance_measure(page: Page) -> None:
    """Enter measure mode with the distance tool selected."""
    page.evaluate("() => { setMode('measure'); molDoc.measureType = 'distance'; }")


def _pick(page: Page, index: int) -> dict:
    """Drive one real pick through the dispatch path; assert it succeeded."""
    result = page.evaluate("(i) => window.__acpTestPickAtom(i)", index)
    assert isinstance(result, dict), f"pick {index} returned {result!r}"
    assert result.get("ok") is True, f"pick {index} failed: {result}"
    return result


def _measurements(page: Page) -> list[dict]:
    """Return a copy of the store measurement list."""
    return page.evaluate("() => window.ACPGeometryStore.state.measurements.slice()")


def _coordinate_distance(page: Page, atom_a: int, atom_b: int) -> float:
    """Euclidean distance between two store atoms (Å)."""
    return page.evaluate(
        """([a, b]) => {
            const c = window.ACPGeometryStore.state.coordinates;
            const dx = c[a][0] - c[b][0];
            const dy = c[a][1] - c[b][1];
            const dz = c[a][2] - c[b][2];
            return Math.sqrt(dx * dx + dy * dy + dz * dz);
        }""",
        [atom_a, atom_b],
    )


def _apply_bond_length_edit(page: Page, measurement_id: str, target: float) -> dict:
    """Apply a bond-length edit on C0-C1 with the store measurement id."""
    return page.evaluate(
        """([mid, value]) => window.ACPStructureEditor.applyMeasuredEdit(
            "bond_length", [0, 1], value, mid
        )""",
        [measurement_id, target],
    )


def _has_cjk(text: str | None) -> bool:
    """True when the text contains at least one CJK character."""
    return bool(text) and re.search(r"[\u4e00-\u9fff]", text or "") is not None


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

    tmp_root = Path(tempfile.mkdtemp(prefix="acp_measure_edit_test_"))

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

    Skips (rather than fails) when Chromium binaries or the 3Dmol CDN are
    unavailable; uncaught page errors are collected into ``browser_errors``
    and asserted at the end of each test via ``_assert_no_page_errors``.
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

    context = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
    page = context.new_page()
    page.on("pageerror", lambda exc: browser_errors.append(str(exc)))

    page.goto(server_url, wait_until="domcontentloaded")

    try:
        page.wait_for_function("typeof $3Dmol !== 'undefined'", timeout=15_000)
    except Exception:
        error_text = page.evaluate("document.getElementById('viewer-empty')?.textContent || ''")
        context.close()
        browser.close()
        pw_ctx.stop()
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
        "typeof window.ACPGeometryStore !== 'undefined' && "
        "typeof window._svLoadXyzToViewer === 'function' && "
        "typeof window.__acpTestPickAtom === 'function' && "
        "typeof window.ACPStructureEditor !== 'undefined'",
        timeout=30_000,
    )

    yield page

    context.close()
    browser.close()
    pw_ctx.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pick_advances_status_and_creates_measurement(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """First pick selects one atom (status shows 1/2); the second pick creates
    a measurement and clears the pick buffer."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    _arm_distance_measure(page)

    first = _pick(page, 0)
    assert first["canonicalId"] == 0
    assert first["selectionLength"] == 1
    assert first["measurementCount"] == 0
    status = page.locator("#measure-mode-status").text_content() or ""
    assert "1/2" in status, f"measure status {status!r} must show '1/2' after one pick"

    second = _pick(page, 1)
    assert second["measurementCount"] == 1, "second pick must create the distance measurement"
    assert second["selectionLength"] == 0, "pick buffer must clear after measurement completion"

    measurements = _measurements(page)
    assert len(measurements) == 1
    assert measurements[0]["atoms"] == [0, 1]
    assert abs(measurements[0]["value"] - 1.5) <= _DISTANCE_TOL

    _assert_no_page_errors(browser_errors)


def test_measurement_editable_for_bond_and_not_for_nonbond(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """A bonded pair is editable; a non-bonded pair yields editable=false,
    reason "no_bond" and a Chinese explanation message."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    _arm_distance_measure(page)

    _pick(page, 0)
    _pick(page, 1)
    bond = _measurements(page)[0]
    assert bond["editable"] is True, (
        f"C0-C1 must be editable, got reason={bond['reason']!r} message={bond['message']!r}"
    )

    page.evaluate("() => window.ACPGeometryStore.clearMeasurements()")
    _pick(page, 0)
    _pick(page, 3)
    nonbond = _measurements(page)[0]
    assert nonbond["editable"] is False
    assert nonbond["reason"] == "no_bond"
    assert _has_cjk(nonbond["message"]), (
        f"non-bond reason must surface a Chinese message, got {nonbond['message']!r}"
    )

    _assert_no_page_errors(browser_errors)


def test_ring_bond_blocks_edit(browser_page: Page, browser_errors: list[str]) -> None:
    """In benzene, cutting C0-C1 leaves the ring connected: editable=false
    with reason "ring_bond"."""
    page = browser_page
    _load_molecule(page, _BENZENE_XYZ)
    _arm_distance_measure(page)

    _pick(page, 0)
    _pick(page, 1)
    measurement = _measurements(page)[0]
    assert measurement["editable"] is False
    assert measurement["reason"] == "ring_bond", (
        f"expected ring_bond, got {measurement['reason']!r}"
    )
    assert _has_cjk(measurement["message"])

    _assert_no_page_errors(browser_errors)


def test_pick_uses_native_index_without_custom_property(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """3Dmol atoms carry no custom acpId property; the pick still resolves the
    canonical id from the native atom.index through _svDispatchPick."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    page.evaluate("() => setMode('select')")

    polluted = page.evaluate("() => 'acpId' in currentModelAtoms[0]")
    assert polluted is False, "currentModelAtoms must not carry a custom acpId property"

    result = _pick(page, 0)
    assert result["canonicalId"] == 0
    assert result["selectionLength"] == 1
    selection = page.evaluate("() => window.ACPGeometryStore.state.selection.slice()")
    assert selection == [0]

    diag = page.evaluate("() => window.__acpPickDiag.dump().slice(-1)[0]")
    assert diag["canonicalId"] == 0 and diag["outcome"] == "toggled", (
        f"pick dispatch diagnostics missing/wrong: {diag!r}"
    )

    _assert_no_page_errors(browser_errors)


def test_edit_preserves_measurements_and_recomputes(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """applyMeasuredEdit("bond_length") routes through store.updateCoordinates:
    the measurement survives, its value recomputes to the target, it is marked
    applied, and the live coordinates actually move to the target distance."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    _arm_distance_measure(page)
    _pick(page, 0)
    _pick(page, 1)

    before = _measurements(page)[0]
    assert abs(before["value"] - 1.5) <= _DISTANCE_TOL
    assert before["_applied"] is False

    result = _apply_bond_length_edit(page, before["id"], 1.6)
    assert result.get("ok") is True, f"applyMeasuredEdit failed: {result!r}"

    measurements = _measurements(page)
    assert len(measurements) == 1, "edit must not drop the measurement"
    after = measurements[0]
    assert abs(after["value"] - 1.6) <= _DISTANCE_TOL, (
        f"measurement value must recompute to 1.6, got {after['value']}"
    )
    assert after["_applied"] is True, "successful edit must mark the measurement applied"
    assert abs(_coordinate_distance(page, 0, 1) - 1.6) <= _DISTANCE_TOL

    _assert_no_page_errors(browser_errors)


def test_undo_redo_reset_preserve_measurements(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """undoEdit restores the original geometry, redoEdit re-applies it, and
    resetEdits returns to the binding snapshot — measurements survive all three."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    _arm_distance_measure(page)
    _pick(page, 0)
    _pick(page, 1)

    measurement_id = _measurements(page)[0]["id"]
    assert _apply_bond_length_edit(page, measurement_id, 1.6).get("ok") is True

    undo = page.evaluate("() => window.ACPStructureEditor.undoEdit()")
    assert undo.get("ok") is True, f"undoEdit failed: {undo!r}"
    measurements = _measurements(page)
    assert len(measurements) == 1, "undo must not drop measurements"
    assert abs(measurements[0]["value"] - 1.5) <= _DISTANCE_TOL
    assert abs(_coordinate_distance(page, 0, 1) - 1.5) <= _DISTANCE_TOL

    redo = page.evaluate("() => window.ACPStructureEditor.redoEdit()")
    assert redo.get("ok") is True, f"redoEdit failed: {redo!r}"
    measurements = _measurements(page)
    assert len(measurements) == 1, "redo must not drop measurements"
    assert abs(measurements[0]["value"] - 1.6) <= _DISTANCE_TOL

    reset = page.evaluate("() => window.ACPStructureEditor.resetEdits()")
    assert reset.get("ok") is True, f"resetEdits failed: {reset!r}"
    measurements = _measurements(page)
    assert len(measurements) == 1, "reset must not drop measurements"
    assert abs(measurements[0]["value"] - 1.5) <= _DISTANCE_TOL

    _assert_no_page_errors(browser_errors)


def test_consecutive_measurements_are_not_lost(
    browser_page: Page, browser_errors: list[str]
) -> None:
    """Two consecutive distance measurements (C0-C1 then C1-C2) coexist."""
    page = browser_page
    _load_molecule(page, _BUTANE_XYZ)
    _arm_distance_measure(page)

    _pick(page, 0)
    _pick(page, 1)
    _pick(page, 1)
    _pick(page, 2)

    measurements = _measurements(page)
    assert len(measurements) == 2, f"expected 2 measurements, got {len(measurements)}"
    assert measurements[0]["atoms"] == [0, 1]
    assert measurements[1]["atoms"] == [1, 2]
    for measurement in measurements:
        assert abs(measurement["value"] - 1.5) <= _DISTANCE_TOL
        assert math.isfinite(measurement["value"])

    _assert_no_page_errors(browser_errors)
