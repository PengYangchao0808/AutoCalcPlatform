"""Compatibility checks for the decomposed page and its actual module boundaries."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def test_default_page_loads_modules_in_dependency_order() -> None:
    html = (FRONTEND / "ACP_Workbench_v2.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
    assert len(scripts) == len(set(scripts)), "Each module must be loaded once"
    for source in scripts:
        assert (FRONTEND / source.split("?")[0]).is_file(), source
    for dependency, consumer in (
        ("js/workbench/contracts/batch_role_defaults.js", "js/job_editor.js"),
        ("js/workbench/contracts/batch_role_defaults.js", "js/workbench/features/wizard.js"),
        ("js/client/transport.js", "js/workbench/compat/api.js"),
        ("js/client/resource-store.js", "js/client/task-view-query.js"),
        ("js/client/resource-store.js", "js/client/job-detail-query.js"),
        ("js/workbench/contracts/task_view_contract.js", "js/client/task-view-query.js"),
        ("js/client/polling-strategy.js", "js/workbench/compat/api.js"),
        ("js/client/job-commands.js", "js/workbench/features/queue.js"),
        ("js/client/wizard-endpoints.js", "js/workbench/features/wizard.js"),
        ("js/workbench/features/wizard/submit-edit.js", "js/workbench/features/wizard.js"),
        ("js/client/transport.js", "js/client/system-endpoints.js"),
        ("js/client/system-endpoints.js", "js/workbench/controllers/system_resources_controller.js"),
        ("js/client/system-endpoints.js", "js/workbench/compat/api.js"),
        ("js/workbench/controllers/system_resources_controller.js", "js/workbench/features/reaction-builder.js"),
        ("js/workbench/controllers/task_list_controller.js", "js/workbench/features/queue.js"),
        ("js/workbench/controllers/job_detail_controller.js", "js/workbench/features/job-detail.js"),
        ("js/client/job-detail-query.js", "js/workbench/features/job-detail.js"),
        ("js/workbench/controllers/task_session_controller.js", "js/workbench/features/reaction-builder.js"),
        ("js/visualization/framing.js", "js/visualization/mol3d_adapter.js"),
        ("js/visualization/mol3d_adapter.js", "js/visualization/scene_instance.js"),
        ("js/visualization/scene_instance.js", "js/workbench/features/molecule-view.js"),
        ("js/visualization/energy_chart_renderer.js", "js/workbench/features/energy.js"),
        ("js/workbench/features/scan-s2.js", "js/workbench/bootstrap.js"),
    ):
        assert scripts.index(dependency) < scripts.index(consumer)


@pytest.mark.skipif(shutil.which("node") is None, reason="NOT_VERIFIED: Node unavailable")
def test_frontend_module_behavior_suite() -> None:
    result = subprocess.run(
        ["node", str(FRONTEND / "js/module_tests/run_all.js")],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed" in result.stdout


def test_existing_api_serves_page_assets_and_task_contract(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path / "runs")) as client:
        response = client.get("/")
        assert response.status_code == 200
        html = response.text
        assets = re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html)
        for asset in assets:
            result = client.get("/" + asset)
            assert result.status_code == 200, asset
            assert result.headers.get("cache-control") == "no-cache", asset
        assert client.get("/legacy/").status_code == 200
        result = client.get("/api/v2/task-view?offset=0&max_total=200")
        assert result.status_code == 200
        body = result.json()
        assert isinstance(body["groups"], list)
        assert body["total"] == 0
        if shutil.which("node"):
            # Validate the real service response with the exact browser contract.
            program = """
const fs = require('fs'), vm = require('vm');
global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const result = ACPWorkbench.contracts.taskView.validateBody(JSON.parse(fs.readFileSync(0, 'utf8')));
if (!result.ok) { console.error(result.problems); process.exit(1); }
"""
            checked = subprocess.run(
                ["node", "-e", program, str(FRONTEND / "js/workbench/contracts/task_view_contract.js")],
                input=json.dumps(body), capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            assert checked.returncode == 0, checked.stdout + checked.stderr
