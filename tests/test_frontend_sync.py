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
