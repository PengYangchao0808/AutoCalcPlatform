from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
FRONTEND = REPO_ROOT / "frontend" / "ACP_Workbench_v2.html"
SERVER = REPO_ROOT / "src" / "acp" / "api" / "server.py"


def test_default_workbench_keeps_original_v2_frontend_and_v1_contract() -> None:
    html = FRONTEND.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")

    assert 'html_path = _FRONTEND_DIR / "ACP_Workbench_v2.html"' in server
    assert 'ACP_Workbench_minimal.html' not in server
    assert 'const API_BASE = "/api/v1"' in html
    for feature in ("workflow-catalog", "method-catalog", "/uploads", "/structures/parse"):
        assert feature in html
    for action in ("pause", "unpause", "continue", "rerun", "purge"):
        assert f'"/jobs/" + encodeURIComponent(jobId) + "/{action}"' in html or action in html
    assert "/jobs/" in html and "/detail" in html
    assert html.count("function updateSrPickButtons()") == 1
    assert 'serviceStatus === "ok"' in html
    assert 'if (!resp.ok) throw new Error("HTTP " + resp.status + " " + resp.statusText);' in html
    assert 'typeof $3Dmol === "undefined"' in html
    assert "</html>\n;\n</script>" not in html
    assert 'id="mc-profile-select"' in html
    assert 'id="batch-optimize-profile"' not in html
    assert 'id="batch-optimize-profile-summary"' in html
    assert "methodPayload.optimization_method" in html
    assert "methodPayload.single_point_method" in html
    assert "window.wizardStructures" not in html
    assert "function applyBatchOptimizeMethodFields(methodPayload)" in html
    assert "applyBatchOptimizeMethodFields(methodPayload);" in html
    assert "mc-number-with-unit" in html
    assert "mc-number-unit" in html
    # PES manual selections are persisted independently; BatchOptimize is
    # intentionally started from the new-task flow rather than from the
    # energy viewer.
    assert 'data-energy-action="toggle-selection-lock"' in html
    assert "function energyGraphToggleSelectionLock()" in html
    assert "选点已锁定" in html
    assert "选点可编辑" in html
    assert "energyGraphConfirmAndBatch" not in html
    assert 'data-energy-action="to-batch"' not in html
    # BatchOptimize keeps one scheduler task per parsed structure.  The
    # frontend may group those tasks with batch_id, but must not submit one
    # batch_structures payload as a single scheduler job.
    assert "for (var i = 0; i < wizardStructures.length; i++)" in html
    assert "batch_id: batchId" in html
    assert "await submitJobBatch(batchBodies);" in html
    assert "var batchBody = {" not in html


def test_minimal_frontend_is_not_the_default_page() -> None:
    server = SERVER.read_text(encoding="utf-8")

    assert "ACP_Workbench_minimal.html" not in server


def test_energy_chart_axes_cannot_scroll_out_of_viewport() -> None:
    """Regression guard for docs/ACP_Energy_Graph_Axis_Rendering_Issue_Report.md.

    The axis band lives at the bottom of the fixed 1040x520 viewBox.  The
    layout contract must guarantee the SVG always fits the chart viewport so
    the axes stay visible: no vertical scrolling, no min-height forcing
    overflow, and no full-replacement redraw leaving the axes below the fold.
    """
    html = FRONTEND.read_text(encoding="utf-8")

    # Energy chart: viewport must never scroll vertically; SVG must adapt.
    assert ".energy-chart-scroll { min-height: 0; overflow-x: auto; overflow-y: hidden;" in html
    assert ".energy-chart-scroll { min-height: 0; overflow: auto;" not in html
    svg_rule = ".energy-chart-svg { display: block; width: 100%; min-width: 640px; height: 100%; }"
    assert svg_rule in html
    assert ".energy-chart-svg { display: block; width: 100%; min-width: 640px; min-height: 360px; height: 100%; }" not in html

    # Optimization chart: same contract — SVG fills the flex column, no fixed
    # pixel height that can push the axis band out of the card.
    assert ".optimization-chart-svg { display: block; width: 100%; min-width: 620px; height: 100%; }" in html
    assert "min-width: 620px; height: 238px;" not in html
    assert ".optimization-chart-scroll { flex: 1 1 0; min-height: 0; overflow: hidden;" in html
    assert ".optimization-chart-card { display: flex; flex-direction: column;" in html

    # Axes are still generated and appended inside the SVG viewBox.
    assert "function energyGraphAxesMarkup(xDom, yDom)" in html
    assert "svg += energyGraphAxesMarkup(xDom, yDom);" in html
