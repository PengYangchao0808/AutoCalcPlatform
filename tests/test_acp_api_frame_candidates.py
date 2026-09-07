"""API tests for frame-candidate CRUD, sampling frame, and energy-graph view param.

Tests cover:
- POST /jobs/{job_id}/frame-candidate (round-trip, 400, 404, 409)
- GET /jobs/{job_id}/frame-candidates
- DELETE /jobs/{job_id}/frame-candidate/{candidate_id}
- GET /jobs/{job_id}/sampling/frame/{frame_index}
- GET /jobs/{job_id}/energy-graph?view=sampling
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from tests.test_acp_api_v1 import make_client

# ---------------------------------------------------------------------------
# Fixtures: task trees
# ---------------------------------------------------------------------------

_XYZ_3ATOM = "3\ncomment\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"


def _make_optimization_task(tmp_path: Path) -> Path:
    """Write a minimal optimization task tree.

    The trajectory lives at WORK/03_OPT/optimization_trajectory.json
    and geometry_ref is relative to the trajectory's parent directory.
    """
    root = tmp_path / "opt_task"
    opt_dir = root / "WORK" / "03_OPT"
    opt_dir.mkdir(parents=True, exist_ok=True)
    # Cycle xyz files at WORK/03_OPT/cycles/ (relative to trajectory)
    cycles_dir = opt_dir / "cycles"
    cycles_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (cycles_dir / f"cycle_{i:04d}.xyz").write_text(
            _XYZ_3ATOM.replace("comment", f"cycle {i}"), encoding="utf-8"
        )
    trajectory = {
        "item_id": "item_0",
        "cycles": [
            {
                "cycle": i,
                "energy_hartree": -76.0 + i * 0.001,
                "relative_energy_kcal_mol": i * 0.6,
                "delta_energy_kcal_mol": 0.6 if i > 0 else 0.0,
                "rms_gradient": 0.01 - i * 0.002,
                "max_gradient": 0.02 - i * 0.004,
                "rms_displacement": 0.005 - i * 0.001,
                "max_displacement": 0.01 - i * 0.002,
                "scf_iterations": 10 + i,
                "geometry_ref": f"cycles/cycle_{i:04d}.xyz",
            }
            for i in range(3)
        ],
    }
    (opt_dir / "optimization_trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    (root / "RESULT").mkdir(parents=True, exist_ok=True)
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "", "products": []}), encoding="utf-8"
    )
    return root


def _make_conformer_task(tmp_path: Path) -> Path:
    """Write a minimal Confsearch task tree.

    The energy_graph builder reads RESULT/confsearch/confsearch_manifest.json
    and normalises via _normalise_confsearch_records which requires
    free_energy_hartree or relative_energy_kcal.
    """
    root = tmp_path / "conf_task"
    result_dir = root / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (result_dir / f"conf_{i + 1}.xyz").write_text(
            _XYZ_3ATOM.replace("comment", f"conformer {i + 1}"), encoding="utf-8"
        )
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [
            {
                "rank": i + 1,
                "conf_id": f"conf_{i + 1}",
                "free_energy_hartree": -76.0 + i * 0.002,
                "relative_energy_kcal": i * 1.254,
                "geometry": f"conf_{i + 1}.xyz",
            }
            for i in range(3)
        ],
        "selected_conformers": [],
    }
    (result_dir / "confsearch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "", "products": []}), encoding="utf-8"
    )
    return root


def _make_sampling_task(tmp_path: Path) -> Path:
    """Write a minimal Confsearch task with sampling_history + traj.xyz."""
    root = tmp_path / "sampling_task"
    result_dir = root / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True, exist_ok=True)
    work_traj_dir = root / "WORK" / "02_SEARCH" / "xTB"
    work_traj_dir.mkdir(parents=True, exist_ok=True)

    frames_xyz = []
    for i in range(3):
        frames_xyz.append(
            f"3\nmd: {0.5 * (i + 1):.1f} {-100.0 + i * 0.5:.2f} (kcal/mol) -300.0\n"
            "O 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"
        )
    (work_traj_dir / "traj.xyz").write_text("".join(frames_xyz), encoding="utf-8")

    history = {
        "schema_version": "sampling_history_v1",
        "protocol": "xtb-md",
        "source_trajectory": str(work_traj_dir / "traj.xyz"),
        "n_frames_raw": 3,
        "n_frames_used": 3,
        "equilibration_cut": 0,
        "frames": [
            {
                "index": i,
                "time_ps": 0.5 * (i + 1),
                "step": i,
                "energy_kcal_mol": -100.0 + i * 0.5,
                "relative_energy_kcal_mol": i * 0.5,
                "basin_id": 0,
                "is_new_basin": i == 0,
                "mds": [0.0, 0.0],
            }
            for i in range(3)
        ],
        "basins": [
            {
                "basin_id": 0,
                "first_seen_index": 0,
                "first_seen_ps": 0.5,
                "visit_count": 3,
                "min_energy": -100.0,
                "representative_frame": 0,
            }
        ],
        "saturation": {
            "unique_clusters": 1,
            "new_clusters_last_20pct": 0,
            "last_new_basin_ps": 0.5,
            "revisit_ratio": 0.666,
            "energy_window_kcal_mol": 1.0,
            "level": "HIGH",
            "cumulative_unique": [
                {"time_ps": 0.5, "unique": 1},
                {"time_ps": 1.0, "unique": 1},
                {"time_ps": 1.5, "unique": 1},
            ],
        },
        "computed_at": "2026-09-07T00:00:00Z",
        "subsampled": False,
        "subsample_stride": 1,
    }
    (result_dir / "sampling_history.json").write_text(json.dumps(history), encoding="utf-8")
    # Also need a valid conformer manifest for the default view
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [
            {
                "rank": 1,
                "conf_id": "conf_1",
                "free_energy_hartree": -100.0,
                "relative_energy_kcal": 0.0,
                "geometry": "conf_1.xyz",
            }
        ],
        "selected_conformers": [],
    }
    (result_dir / "confsearch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (result_dir / "conf_1.xyz").write_text(
        _XYZ_3ATOM.replace("comment", "conf 1"), encoding="utf-8"
    )
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "", "products": []}), encoding="utf-8"
    )
    return root


def _make_pes_task(tmp_path: Path) -> Path:
    """Write a minimal PESsearch task tree (for PES rejection test)."""
    root = tmp_path / "pes_task"
    pes_dir = root / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True, exist_ok=True)
    (pes_dir / "pes_profile.json").write_text(
        json.dumps(
            {
                "schema_version": "pes_profile_v2",
                "mode": "bond_length_scan",
                "status": "completed",
                "scan_dir": "WORK/07_PATH/pes_scan_001",
                "frames": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "", "products": []}), encoding="utf-8"
    )
    return root


def _register_job(
    client: TestClient,
    tmp_path: Path,
    work_dir: Path,
    *,
    job_id: str,
    workflow: str,
    status: JobStatus = JobStatus.COMPLETED,
    method: dict | None = None,
) -> str:
    manager = client.app.state.job_manager
    spec = JobSpec(
        workflow=workflow,
        name="test_job",
        input={},
        method=method or {},
    )
    record = JobRecord(id=job_id, spec=spec, status=status, work_dir=str(work_dir))
    manager.store.create(record)
    return job_id


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


class TestFrameCandidateCRUD:
    """POST -> GET -> DELETE round-trip tests."""

    def test_optimization_save_list_delete(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_001", workflow="optimize")

        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 1, "role": "TS", "name": "my-ts"},
        )
        assert resp.status_code == 200, f"POST failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["revision"] == 1
        assert body["candidate"] is not None
        assert body["candidate"]["candidate_id"] == "opt_ts_frame_001"
        assert body["candidate"]["role"] == "TS"

        xyz_path = work_dir / "RESULT" / "structures" / "opt_ts_frame_001.xyz"
        assert xyz_path.is_file()
        xyz_content = xyz_path.read_text(encoding="utf-8")
        assert "candidate_id=opt_ts_frame_001" in xyz_content

        resp = client.get(f"/api/v1/jobs/{job_id}/frame-candidates")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["candidate_id"] == "opt_ts_frame_001"

        resp = client.delete(f"/api/v1/jobs/{job_id}/frame-candidate/opt_ts_frame_001")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["candidates"]) == 0

    def test_conformer_save(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_conformer_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="conf_001", workflow="Confsearch")

        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "conformer", "frame_index": 0, "role": "INT"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["candidate"]["candidate_id"] == "conf_int_frame_000"

    def test_idempotent_resave(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_idem", workflow="optimize")

        resp1 = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS"},
        )
        assert resp1.status_code == 200
        rev1 = resp1.json()["revision"]

        resp2 = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS", "name": "updated"},
        )
        assert resp2.status_code == 200
        rev2 = resp2.json()["revision"]
        assert rev2 == rev1 + 1
        # Same candidate_id (opt_ts_frame_000) so it replaces the previous entry
        assert len(resp2.json()["candidates"]) == 1
        assert resp2.json()["candidate"]["name"] == "updated"

    def test_expected_revision_conflict(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_rev", workflow="optimize")

        client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS"},
        )
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={
                "view_type": "optimization",
                "frame_index": 0,
                "role": "INT",
                "expected_revision": 0,
            },
        )
        assert resp.status_code == 409

    def test_delete_expected_revision_conflict(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_delrev", workflow="optimize")

        client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS"},
        )
        resp = client.delete(
            f"/api/v1/jobs/{job_id}/frame-candidate/opt_ts_frame_000?expected_revision=999",
        )
        assert resp.status_code == 409


class TestFrameCandidateErrors:
    """400/404/409 error paths."""

    def test_job_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/jobs/nonexistent/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS"},
        )
        assert resp.status_code == 404

    def test_job_not_completed(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(
            client,
            tmp_path,
            work_dir,
            job_id="opt_running",
            workflow="optimize",
            status=JobStatus.RUNNING,
        )
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "TS"},
        )
        assert resp.status_code == 409

    def test_pessearch_rejected(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_pes_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="pes_001", workflow="PESsearch")
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "scan", "frame_index": 0, "role": "TS"},
        )
        assert resp.status_code == 400
        assert "/pes/review" in resp.json()["detail"]

    def test_invalid_role(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="opt_badrole", workflow="optimize"
        )
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 0, "role": "INVALID"},
        )
        assert resp.status_code == 400

    def test_invalid_view_type(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="opt_badview", workflow="optimize"
        )
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "bogus", "frame_index": 0, "role": "TS"},
        )
        assert resp.status_code == 400

    def test_frame_out_of_range(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_oob", workflow="optimize")
        resp = client.post(
            f"/api/v1/jobs/{job_id}/frame-candidate",
            json={"view_type": "optimization", "frame_index": 999, "role": "TS"},
        )
        assert resp.status_code == 404

    def test_delete_nonexistent_candidate(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_nofind", workflow="optimize")
        resp = client.delete(f"/api/v1/jobs/{job_id}/frame-candidate/nonexistent_xyz_000")
        assert resp.status_code == 404

    def test_list_empty(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_optimization_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="opt_empty", workflow="optimize")
        resp = client.get(f"/api/v1/jobs/{job_id}/frame-candidates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["candidates"] == []
        assert body["revision"] == 0


class TestSamplingFrameEndpoint:
    """GET /jobs/{job_id}/sampling/frame/{frame_index} tests."""

    def test_sampling_frame_200(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_sampling_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="samp_001", workflow="Confsearch")
        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["frame_index"] == 1
        assert body["time_ps"] == 1.0
        assert body["step"] == 1
        assert body["energy_kcal_mol"] == -99.5
        assert body["relative_energy_kcal_mol"] == 0.5
        assert "O" in body["xyz"]

    def test_sampling_frame_404_unreadable_trajectory(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        work_dir = _make_sampling_task(tmp_path)
        (work_dir / "WORK" / "02_SEARCH" / "xTB" / "traj.xyz").unlink()
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="samp_missing_traj", workflow="Confsearch"
        )

        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/1")

        assert resp.status_code == 404
        assert "trajectory file is unreadable" in resp.json()["detail"]

    def test_sampling_frame_404_no_history(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_conformer_task(tmp_path)
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="conf_nosamp", workflow="Confsearch"
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/0")
        assert resp.status_code == 404

    def test_sampling_frame_404_out_of_range(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_sampling_task(tmp_path)
        job_id = _register_job(client, tmp_path, work_dir, job_id="samp_oob", workflow="Confsearch")
        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/999")
        assert resp.status_code == 404

    def test_sampling_frame_job_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/jobs/nonexistent/sampling/frame/0")
        assert resp.status_code == 404

    def test_sampling_frame_basin_with_equilibration_cut(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Regression: basin lookup must use positional index, not trajectory index.

        When equilibration_cut > 0, trajectory indices (2, 3, 4) diverge from
        positional indices (0, 1, 2) in history.frames / basin_ids.  The old
        code used basin_ids[frame_index] which returned None (or IndexError).
        """
        work_dir = _make_sampling_task_with_equilibration_cut(tmp_path)
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="samp_cut", workflow="Confsearch"
        )
        # frame_index=3 is at positional index 1 (indices 2,3,4 -> positions 0,1,2)
        # basin_ids[1] == 20 (distinct from basin_ids[0]=10 and basin_ids[2]=30)
        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["frame_index"] == 3
        assert body["basin_id"] == 20

    def test_sampling_frame_beyond_basin_ids_len(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Trajectory index 4 > len(basin_ids)=3 but positional index 2 < len.

        The old buggy code checked ``frame_index < len(basin_ids)`` which
        returned None for frame_index=4.  The fix uses the positional index.
        """
        work_dir = _make_sampling_task_with_equilibration_cut(tmp_path)
        job_id = _register_job(
            client, tmp_path, work_dir, job_id="samp_cut3", workflow="Confsearch"
        )
        # frame_index=4 -> positional index 2 -> basin_ids[2] == 30
        # Old code: 4 < len(basin_ids)=3 is False -> None (WRONG)
        # Fixed code: pos=2 < 3 -> basin_ids[2] == 30 (CORRECT)
        resp = client.get(f"/api/v1/jobs/{job_id}/sampling/frame/4")
        assert resp.status_code == 200
        body = resp.json()
        assert body["frame_index"] == 4
        assert body["basin_id"] == 30


def _make_sampling_task_with_equilibration_cut(tmp_path: Path) -> Path:
    """Sampling task where equilibration_cut=2 makes traj indices != positions.

    Trajectory has 5 frames (indices 0-4); after cutting the first 2,
    history.frames contains indices [2, 3, 4] at positions [0, 1, 2].
    basin_ids = [10, 20, 30] -- distinct basins to verify positional lookup.
    """
    root = tmp_path / "sampling_cut_task"
    result_dir = root / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True, exist_ok=True)
    work_traj_dir = root / "WORK" / "02_SEARCH" / "xTB"
    work_traj_dir.mkdir(parents=True, exist_ok=True)

    # Write a 5-frame trajectory
    frames_xyz = []
    for i in range(5):
        frames_xyz.append(
            f"3\nmd: {0.5 * (i + 1):.1f} {-100.0 + i * 0.5:.2f} (kcal/mol) -300.0\n"
            "O 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"
        )
    (work_traj_dir / "traj.xyz").write_text("".join(frames_xyz), encoding="utf-8")

    # After equilibration_cut=2, only frames with index 2,3,4 remain
    history = {
        "schema_version": "sampling_history_v1",
        "protocol": "xtb-md",
        "source_trajectory": str(work_traj_dir / "traj.xyz"),
        "n_frames_raw": 5,
        "n_frames_used": 3,
        "equilibration_cut": 2,
        "frames": [
            {
                "index": i + 2,  # trajectory indices start at 2
                "time_ps": 0.5 * (i + 3),
                "step": i + 2,
                "energy_kcal_mol": -100.0 + (i + 2) * 0.5,
                "relative_energy_kcal_mol": i * 0.5,
                "basin_id": [10, 20, 30][i],
                "is_new_basin": i == 0,
                "mds": [0.0, float(i)],
            }
            for i in range(3)
        ],
        "basins": [
            {
                "basin_id": 10,
                "first_seen_index": 0,
                "first_seen_ps": 1.5,
                "visit_count": 1,
                "min_energy": -99.0,
                "representative_frame": 0,
            },
            {
                "basin_id": 20,
                "first_seen_index": 1,
                "first_seen_ps": 2.0,
                "visit_count": 1,
                "min_energy": -98.5,
                "representative_frame": 1,
            },
            {
                "basin_id": 30,
                "first_seen_index": 2,
                "first_seen_ps": 2.5,
                "visit_count": 1,
                "min_energy": -98.0,
                "representative_frame": 2,
            },
        ],
        "saturation": {
            "unique_clusters": 3,
            "new_clusters_last_20pct": 0,
            "last_new_basin_ps": 2.5,
            "revisit_ratio": 0.0,
            "energy_window_kcal_mol": 1.0,
            "level": "HIGH",
            "cumulative_unique": [
                {"time_ps": 1.5, "unique": 1},
                {"time_ps": 2.0, "unique": 2},
                {"time_ps": 2.5, "unique": 3},
            ],
        },
        "computed_at": "2026-09-07T00:00:00Z",
        "subsampled": False,
        "subsample_stride": 1,
    }
    (result_dir / "sampling_history.json").write_text(json.dumps(history), encoding="utf-8")

    # Conformer manifest for default view
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [
            {
                "rank": 1,
                "conf_id": "conf_1",
                "free_energy_hartree": -100.0,
                "relative_energy_kcal": 0.0,
                "geometry": "conf_1.xyz",
            }
        ],
        "selected_conformers": [],
    }
    (result_dir / "confsearch_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (result_dir / "conf_1.xyz").write_text(
        _XYZ_3ATOM.replace("comment", "conf 1"), encoding="utf-8"
    )
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"version": 2, "task_id": "", "products": []}), encoding="utf-8"
    )
    return root


class TestEnergyGraphViewParam:
    """GET /jobs/{job_id}/energy-graph?view=sampling tests."""

    def test_view_sampling(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_sampling_task(tmp_path)
        job_id = _register_job(
            client,
            tmp_path,
            work_dir,
            job_id="eg_samp",
            workflow="Confsearch",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/energy-graph?view=sampling")
        assert resp.status_code == 200
        body = resp.json()
        assert body["view_type"] == "sampling"

    def test_view_bogus_falls_back_to_default(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_conformer_task(tmp_path)
        job_id = _register_job(
            client,
            tmp_path,
            work_dir,
            job_id="eg_bogus",
            workflow="Confsearch",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/energy-graph?view=bogus")
        assert resp.status_code == 200
        body = resp.json()
        assert body["view_type"] == "conformer"

    def test_view_none_defaults_to_conformer(self, client: TestClient, tmp_path: Path) -> None:
        work_dir = _make_conformer_task(tmp_path)
        job_id = _register_job(
            client,
            tmp_path,
            work_dir,
            job_id="eg_none",
            workflow="Confsearch",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["view_type"] == "conformer"
