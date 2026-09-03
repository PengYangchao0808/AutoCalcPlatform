"""API tests for POST/GET /api/v1/jobs/{job_id}/pes/review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from tests.test_acp_api_v1 import make_client


def _make_pes_task(tmp_path: Path, *, legacy: bool = False) -> Path:
    """Write a minimal PES task tree (canonical pes_profile_v2 or legacy S2)."""
    root = tmp_path / "pes_task"
    if legacy:
        pes_dir = root / "RESULT" / "mechanism"
        pes_dir.mkdir(parents=True)
        (pes_dir / "s2_path_manifest.json").write_text(
            json.dumps({"schema_version": "pes_profile_v2", "frames": [], "status": "completed"}),
            encoding="utf-8",
        )
        return root
    scan_dir = root / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames"
    scan_dir.mkdir(parents=True)
    for index in range(3):
        (scan_dir / f"frame_{index:03d}.xyz").write_text(
            "3\nframe\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n",
            encoding="utf-8",
        )
    profile = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": "bond_length_scan",
        "status": "completed",
        "scan_dir": "WORK/07_PATH/pes_scan_001",
        "frames": [
            {
                "index": index,
                "target_coordinate": 1.0 + index * 0.1,
                "actual_coordinate": 1.0 + index * 0.1,
                "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
            }
            for index in range(3)
        ],
        "frames_count": 3,
        "ts_candidates": [],
        "int_candidates": [],
    }
    pes_dir = root / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True)
    (pes_dir / "pes_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return root


def _register_job(
    client: TestClient,
    tmp_path: Path,
    work_dir: Path,
    *,
    job_id: str = "20260903_001_PESsearch",
    workflow: str = "PESsearch",
    status: JobStatus = JobStatus.COMPLETED,
) -> str:
    manager = client.app.state.job_manager
    spec = JobSpec(
        workflow=workflow,
        name="pes_demo",
        input={"scan_request": {}},
        method={"mode": "bond_length_scan"},
    )
    record = JobRecord(id=job_id, spec=spec, status=status, work_dir=str(work_dir))
    manager.store.create(record)
    return job_id


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def test_get_review_pending_when_never_saved(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review")
    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "status": "pending", "review": {}}


def test_post_review_happy_path(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    response = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={
            "note": "Stepwise",
            "candidates": [
                {"frame_index": 1, "role": "TS"},
                {"frame_index": 2, "role": "INT"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["selected_count"] == 2
    assert body["revision"] == 1
    assert [c["candidate_id"] for c in body["candidates"]] == [
        "pes_ts_frame_001",
        "pes_int_frame_002",
    ]
    assert (work_dir / "RESULT" / "pes_search" / "pes_review.json").is_file()
    assert (work_dir / "RESULT" / "structures" / "pes_ts_frame_001.xyz").is_file()

    saved = client.get(f"/api/v1/jobs/{job_id}/pes/review")
    assert saved.status_code == 200
    assert saved.json()["status"] == "confirmed"
    assert saved.json()["review"]["note"] == "Stepwise"


def test_post_review_invalid_frame_422(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    response = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 42, "role": "TS"}]},
    )
    assert response.status_code == 422


def test_post_review_revision_conflict_409(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    first = client.post(f"/api/v1/jobs/{job_id}/pes/review", json={"candidates": []})
    assert first.status_code == 200
    conflict = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [], "expected_revision": 99},
    )
    assert conflict.status_code == 409


def test_post_review_non_pes_job_400(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir, workflow="BatchOptimize")
    response = client.post(f"/api/v1/jobs/{job_id}/pes/review", json={"candidates": []})
    assert response.status_code == 400


def test_post_review_uncompleted_job_409(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir, status=JobStatus.RUNNING)
    response = client.post(f"/api/v1/jobs/{job_id}/pes/review", json={"candidates": []})
    assert response.status_code == 409


def test_post_review_legacy_task_410(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path, legacy=True)
    job_id = _register_job(client, tmp_path, work_dir)
    response = client.post(f"/api/v1/jobs/{job_id}/pes/review", json={"candidates": []})
    assert response.status_code == 410


def test_old_s2_review_stays_gone(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    response = client.post(f"/api/v1/jobs/{job_id}/s2/review", json={"candidates": []})
    assert response.status_code == 410


def test_energy_graph_reflects_saved_review(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    saved = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 1, "role": "TS"}]},
    )
    assert saved.status_code == 200

    graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph").json()
    metadata = graph.get("metadata") or {}
    assert metadata.get("review", {}).get("status") == "confirmed"
    manual = [a for a in graph.get("annotations", []) if a.get("selection_source") == "manual"]
    assert len(manual) == 1
    assert manual[0]["candidate_id"] == "pes_ts_frame_001"
    assert manual[0]["saved"] is True
    assert manual[0]["frame_index"] == 1
