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
    # Post-isolation layout (2026-09-03): recommendations are audit-only and
    # the manifest carries zero structure products until manual review.
    (pes_dir / "pes_recommendations.json").write_text(
        json.dumps(
            {
                "schema_version": "pes_recommendations_v1",
                "workflow": "PESsearch",
                "scan_dir": "WORK/07_PATH/pes_scan_001",
                "ts": [],
                "intermediates": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "task_id": "",
                "workflow": "PESsearch",
                "status": "completed",
                "products": [
                    {
                        "id": "pes_profile",
                        "label": "PESsearch energy profile",
                        "path": "pes_search/pes_profile.json",
                        "kind": "pes_profile",
                    },
                    {
                        "id": "pes_recommendations",
                        "label": "PESsearch algorithm recommendations (audit only)",
                        "path": "pes_search/pes_recommendations.json",
                        "kind": "report",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
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
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "pending"
    assert body["review"] == {}
    assert body["backups"] == []


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


def test_get_review_lists_backups_and_restore_switches_round(
    client: TestClient, tmp_path: Path
) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)

    first = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 0, "role": "TS"}], "note": "round1"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 2, "role": "INT"}], "note": "round2"},
    )
    assert second.status_code == 200

    state = client.get(f"/api/v1/jobs/{job_id}/pes/review").json()
    assert [b["n"] for b in state["backups"]] == [1]
    assert state["backups"][0]["note"] == "round1"
    assert state["backups"][0]["selected_count"] == 1

    restored = client.post(
        f"/api/v1/jobs/{job_id}/pes/review/restore",
        json={"backup": 1, "expected_revision": 2},
    )
    assert restored.status_code == 200
    body = restored.json()
    assert body["restored_from"] == 1
    assert body["revision"] == 3
    assert body["candidates"][0]["candidate_id"] == "pes_ts_frame_000"

    manifest = json.loads(
        (work_dir / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    structures = [p for p in manifest["products"] if p["kind"] == "structure"]
    assert [p["metadata"]["candidate_id"] for p in structures] == ["pes_ts_frame_000"]


def test_restore_unknown_backup_404(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    client.post(f"/api/v1/jobs/{job_id}/pes/review", json={"candidates": []})
    response = client.post(f"/api/v1/jobs/{job_id}/pes/review/restore", json={"backup": 42})
    assert response.status_code == 404


def test_restore_revision_conflict_409(client: TestClient, tmp_path: Path) -> None:
    work_dir = _make_pes_task(tmp_path)
    job_id = _register_job(client, tmp_path, work_dir)
    client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 0, "role": "TS"}]},
    )
    client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 1, "role": "INT"}]},
    )
    response = client.post(
        f"/api/v1/jobs/{job_id}/pes/review/restore",
        json={"backup": 1, "expected_revision": 99},
    )
    assert response.status_code == 409


def test_unconfirmed_pes_batch_source_surfaces_confirmation_hint(
    client: TestClient, tmp_path: Path
) -> None:
    """structure-sources must not leak guesses; the batch error names pes_review."""
    work_dir = _make_pes_task(tmp_path)
    _register_job(client, tmp_path, work_dir)
    sources = client.get("/api/v1/structure-sources/recent?limit=50").json()
    assert all("pes_task" not in str(src.get("source_id", "")) for src in sources["sources"])
    assert (work_dir / "RESULT" / "pes_search" / "pes_recommendations.json").is_file()
    manifest = json.loads(
        (work_dir / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    assert not [p for p in manifest["products"] if p["kind"] == "structure"]


# ---------------------------------------------------------------------------
# Remote PES jobs (LSF): read projections resolve through the remote cache
# ---------------------------------------------------------------------------


class _RemoteTreeFetcher:
    """Serves a cached remote task tree; records requested relative paths."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[str] = []

    def read_file(self, record: object, filename: str) -> bytes:
        self.calls.append(filename)
        if filename in self.files:
            return self.files[filename]
        raise FileNotFoundError(filename)


def _remote_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def _register_remote_job(
    client: TestClient,
    tmp_path: Path,
    *,
    job_id: str = "20260928_213910_002_PESsearch",
    status: JobStatus = JobStatus.COMPLETED,
) -> tuple[str, Path]:
    """Seed a remote PESsearch job whose results live only on the remote node."""
    manager = client.app.state.job_manager
    local_work_dir = tmp_path / "local_remote_pes"
    local_work_dir.mkdir(parents=True, exist_ok=True)
    spec = JobSpec(
        workflow="PESsearch",
        name="pes_remote",
        input={"scan_request": {}},
        method={"mode": "bond_length_scan"},
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=status,
        work_dir=str(local_work_dir),
        remote_job_id="42",
        result={
            "node": "node1",
            "remote_dir": f"/remote/{job_id}",
            "lsf_job_id": "42",
        },
    )
    manager.store.create(record)
    return job_id, local_work_dir


def _inject_remote_tree(client: TestClient, tmp_path: Path) -> _RemoteTreeFetcher:
    remote_root = _make_pes_task(tmp_path / "remote")
    fetcher = _RemoteTreeFetcher(_remote_files(remote_root))
    manager = client.app.state.job_manager
    manager._remote_fetcher = fetcher  # type: ignore[assignment]
    return fetcher


def test_remote_energy_graph_resolves_profile_from_remote_cache(
    client: TestClient, tmp_path: Path
) -> None:
    """The reported 2026-09-28 regression: remote PES energy graph must load."""
    fetcher = _inject_remote_tree(client, tmp_path)
    job_id, local_work_dir = _register_remote_job(client, tmp_path)

    response = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view_type"] == "scan"
    assert body["source"] == "RESULT/pes_search/pes_profile.json"
    assert body["nodes"]
    assert "RESULT/pes_search/pes_profile.json" in fetcher.calls
    assert not (local_work_dir / "RESULT").exists()


def test_remote_s2_profile_and_candidates_load(client: TestClient, tmp_path: Path) -> None:
    _inject_remote_tree(client, tmp_path)
    job_id, _local = _register_remote_job(client, tmp_path)

    profile = client.get(f"/api/v1/jobs/{job_id}/s2/profile")
    assert profile.status_code == 200, profile.text
    assert len(profile.json()["frames"]) == 3

    candidates = client.get(f"/api/v1/jobs/{job_id}/s2/candidates")
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["mode"] == "bond_length_scan"


def test_remote_s2_frame_fetches_geometry_lazily(client: TestClient, tmp_path: Path) -> None:
    fetcher = _inject_remote_tree(client, tmp_path)
    job_id, _local = _register_remote_job(client, tmp_path)

    frame = client.get(f"/api/v1/jobs/{job_id}/s2/frame/1")
    assert frame.status_code == 200, frame.text
    assert frame.json()["xyz"].strip()
    assert "WORK/07_PATH/pes_scan_001/scan_frames/frame_001.xyz" in fetcher.calls


def test_remote_pes_review_reads_cache_and_rejects_write(
    client: TestClient, tmp_path: Path
) -> None:
    _inject_remote_tree(client, tmp_path)
    job_id, _local = _register_remote_job(client, tmp_path)

    read = client.get(f"/api/v1/jobs/{job_id}/pes/review")
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "pending"

    write = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={"candidates": [{"frame_index": 1, "role": "TS"}]},
    )
    assert write.status_code == 501
    assert "远程" in write.json()["detail"]


def test_remote_pes_without_cached_files_keeps_404(client: TestClient, tmp_path: Path) -> None:
    """Fetch failures degrade to the original clear 404, never a 500."""
    manager = client.app.state.job_manager
    manager._remote_fetcher = _RemoteTreeFetcher({})  # type: ignore[assignment]
    job_id, _local = _register_remote_job(client, tmp_path)

    response = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
    assert response.status_code == 404
    assert "No PES profile" in response.json()["detail"]
