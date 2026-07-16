"""Tests for the ACP API layer (status, backends, workflows, jobs lifecycle).

Uses FastAPI's TestClient with the lifespan-driven JobManager. The ``fake``
workflow runs in-process, so these tests need no external QC binaries.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    with TestClient(app) as c:
        yield c


def test_status_returns_full_schema(client: TestClient) -> None:
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    for field in (
        "service",
        "version",
        "status",
        "host",
        "port",
        "python",
        "run_root",
        "uptime_seconds",
        "queue",
        "wsl",
    ):
        assert field in body, f"missing {field}"
    for qfield in ("queued", "running", "completed", "failed", "cancelled"):
        assert qfield in body["queue"]


def test_backends_report_capability_dicts(client: TestClient) -> None:
    r = client.get("/api/backends")
    assert r.status_code == 200
    backends = r.json()["backends"]
    names = {b["name"] for b in backends}
    assert {"gaussian", "xtb", "crest", "orca"} <= names
    for b in backends:
        assert isinstance(b["available"], bool)
        assert isinstance(b["capabilities"], list)
        for cap in b["capabilities"]:
            assert {"name", "available"} <= set(cap.keys())


def test_backends_use_real_capability_matrix(client: TestClient) -> None:
    """P2#1: /api/backends must reflect real capabilities, not the old hardcoded matrix."""
    backends = client.get("/api/backends").json()["backends"]
    by_name = {b["name"]: b for b in backends}
    assert "xtb" in by_name
    xtb_caps = {c["name"]: c["available"] for c in by_name["xtb"]["capabilities"]}
    assert xtb_caps.get("conformer_search") is False, "xtb must not claim conformer_search"
    assert "crest" in by_name
    crest_caps = {c["name"]: c["available"] for c in by_name["crest"]["capabilities"]}
    assert "conformer_search" in crest_caps, "crest should expose conformer_search"


def test_workflows_and_protocols(client: TestClient) -> None:
    wf = client.get("/api/workflows").json()
    assert {"fake", "conformer", "nmr", "benchmark", "mechanism"} <= {
        w["name"] for w in wf["workflows"]
    }
    names = [w["name"] for w in wf["workflows"]]
    assert names == ["fake", "conformer", "nmr", "benchmark", "mechanism"]
    pr = client.get("/api/protocols").json()
    assert isinstance(pr["protocols"], list)


def test_workflows_are_driven_by_registry(client: TestClient) -> None:
    """Workflow metadata must come from the workflow registry, not a hardcoded list."""
    wf = client.get("/api/workflows").json()
    by_name = {w["name"]: w for w in wf["workflows"]}
    assert "nmr" in by_name
    assert by_name["nmr"]["label"] == "NMR"
    assert set(by_name["nmr"]["requires_binaries"]) == {"gaussian", "orca"}
    assert "mechanism" in by_name
    assert by_name["mechanism"]["label"] == "Mechanism / TS"


def test_frontend_index_served(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "ACP Molecular Workbench" in r.text


def test_legacy_frontend_index_served(client: TestClient) -> None:
    r = client.get("/legacy/")
    assert r.status_code == 200
    assert "ACP Workbench" in r.text


def test_create_job_rejects_unknown_workflow(client: TestClient) -> None:
    r = client.post("/api/jobs", json={"workflow": "bogus", "input": {"source": "CCO"}})
    assert r.status_code == 400


def test_missing_job_is_404(client: TestClient) -> None:
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/logs").status_code == 404


def test_fake_job_full_lifecycle(client: TestClient) -> None:
    r = client.post(
        "/api/jobs",
        json={
            "workflow": "fake",
            "name": "lifecycle",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
        },
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "queued"

    rec = client.get(f"/api/jobs/{job_id}").json()
    for _ in range(40):
        rec = client.get(f"/api/jobs/{job_id}").json()
        if rec["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)

    assert rec["status"] == "completed", rec
    assert rec["progress"] == 1.0
    assert rec["exit_code"] == 0

    listing = client.get("/api/jobs").json()
    assert any(j["id"] == job_id for j in listing["jobs"])
    assert listing["counts"]["completed"] >= 1

    files = client.get(f"/api/jobs/{job_id}/files").json()["files"]
    paths = {f["path"] for f in files}
    assert "state.json" in paths
    assert "events.jsonl" in paths

    logs = client.get(f"/api/jobs/{job_id}/logs").json()
    assert "stdout" in logs and "stderr" in logs


def test_cancel_queued_job(client: TestClient) -> None:
    import os as _os

    busy_dir = _os.environ["ACP_RUN_ROOT"] + "_busy"
    _os.environ["ACP_RUN_ROOT"] = busy_dir
    from acp.api.server import create_app

    app = create_app(run_root=busy_dir, max_running=1)
    with TestClient(app) as c:
        blocker_id = c.post(
            "/api/jobs", json={"workflow": "fake", "input": {"source": "X"}}
        ).json()["job_id"]
        second_id = c.post("/api/jobs", json={"workflow": "fake", "input": {"source": "Y"}}).json()[
            "job_id"
        ]
        cancelled = c.post(f"/api/jobs/{second_id}/cancel").json()
        assert cancelled["status"] in ("cancelling", "cancelled")
        for _ in range(30):
            rec = c.get(f"/api/jobs/{second_id}").json()
            if rec["status"] in ("cancelled", "completed", "failed"):
                break
            time.sleep(0.5)
        c.post(f"/api/jobs/{blocker_id}/cancel")
