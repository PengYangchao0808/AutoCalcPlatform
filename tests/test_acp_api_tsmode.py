"""Tests for the TS Mode API surface (frequency-sources + POST /jobs tsmode)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


def _make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path, monkeypatch) as c:
        yield c


def _seed_job(
    client: TestClient,
    tmp_path: Path,
    *,
    job_id: str = "ts-test-001",
    workflow: str = "Confsearch",
    status: JobStatus = JobStatus.COMPLETED,
) -> Path:
    manager = client.app.state.job_manager
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=job_id,
            project_id=manager.default_project_id,
        ),
        status=status,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    return work_dir


def _write_fake_freq_output(path: Path) -> None:
    """Write a minimal ORCA frequency output with VIBRATIONAL FREQUENCIES section."""
    lines = [
        "  ******************************",
        "  * Program Version 6.0.1      *",
        "  ******************************",
        "! r2SCAN-3c Freq",
        "",
        "* xyz 0 1",
        "C   0.00000000   0.00000000   0.00000000",
        "O   1.20000000   0.00000000   0.00000000",
        "H  -0.40000000   0.90000000   0.00000000",
        "*",
        "",
        "CARTESIAN COORDINATES (ANGSTROEM)",
        "---------------------------------",
        "     0  C    0.000000    0.000000    0.000000",
        "     1  O    1.200000    0.000000    0.000000",
        "     2  H   -0.400000    0.900000    0.000000",
        "",
        "VIBRATIONAL FREQUENCIES",
        "-----------------------",
        "   0:       -120.50 cm**-1 ***imaginary mode***",
        "   1:        450.23 cm**-1",
        "   2:        780.10 cm**-1",
        "   3:       1200.45 cm**-1",
        "   4:       1650.80 cm**-1",
        "   5:       3100.20 cm**-1",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_fake_hess(path: Path) -> None:
    """Write a minimal ORCA .hess file."""
    n_atoms = 3
    dim = 3 * n_atoms
    lines = [
        "$orca_hessian",
        "",
        "$atoms",
        f" {n_atoms}",
        " C  12.00000000",
        " O  15.99900000",
        " H   1.00800000",
        "$coords",
        f" {n_atoms}",
        " C   0.0000000000   0.0000000000   0.0000000000",
        " O   0.2268026916   0.0000000000   0.0000000000",
        " H  -0.0756008972   0.1701010464   0.0000000000",
        "$hessian",
        f" {dim}",
    ]
    flat = [0.5] * (dim * dim)
    for start in range(0, len(flat), 5):
        chunk = flat[start : start + 5]
        lines.append(" " + " ".join(f"{v:16.9E}" for v in chunk))
    lines.append("$end")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id}/frequency-sources
# ---------------------------------------------------------------------------


class TestFrequencySources:
    def test_404_unknown_job(self, client: TestClient) -> None:
        resp = client.get("/api/v1/jobs/nonexistent/frequency-sources")
        assert resp.status_code == 404

    def test_empty_when_no_freq_files(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        work_dir = _seed_job(client, tmp_path, job_id="no-freq-001")
        (work_dir / "WORK").mkdir()
        resp = client.get("/api/v1/jobs/no-freq-001/frequency-sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sources"] == []

    def test_discovers_freq_out_with_hess(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        work_dir = _seed_job(client, tmp_path, job_id="freq-001")
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)
        _write_fake_freq_output(freq_dir / "freq_calc.out")
        _write_fake_hess(freq_dir / "freq_calc.hess")

        resp = client.get("/api/v1/jobs/freq-001/frequency-sources")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["sources"]) == 1
        src = body["sources"][0]
        assert src["entry_id"] == "freq_calc"
        assert src["hessian_available"] is True
        assert src["imaginary_count"] == 1
        assert src["atom_count"] == 3
        assert src["mode_count"] == 6
        assert "WORK/04_FREQ/freq_calc.out" in src["output_path"]
        assert "WORK/04_FREQ/freq_calc.hess" in src["hess_path"]

    def test_discovers_freq_out_without_hess(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        work_dir = _seed_job(client, tmp_path, job_id="freq-nohess-001")
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)
        _write_fake_freq_output(freq_dir / "nohess.out")

        resp = client.get("/api/v1/jobs/freq-nohess-001/frequency-sources")
        assert resp.status_code == 200
        src = resp.json()["sources"][0]
        assert src["hessian_available"] is False
        assert src["hess_path"] is None

    def test_multiple_freq_dirs(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        work_dir = _seed_job(client, tmp_path, job_id="multi-001")
        for name in ("run1", "run2"):
            d = work_dir / "WORK" / name
            d.mkdir(parents=True)
            _write_fake_freq_output(d / "freq.out")
            _write_fake_hess(d / "freq.hess")

        resp = client.get("/api/v1/jobs/multi-001/frequency-sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 2

    def test_remote_job_absent_files_returns_pending(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job_id = "remote-001"
        manager = client.app.state.job_manager
        work_dir = tmp_path / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id=job_id,
            spec=JobSpec(
                workflow="Confsearch",
                name=job_id,
                project_id=manager.default_project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
            result={"node": "cn01", "remote_dir": "/home/user/runs/job"},
        )
        manager.store.create(record)
        import shutil

        shutil.rmtree(work_dir)

        resp = client.get(f"/api/v1/jobs/{job_id}/frequency-sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["sources"] == []
        assert "pending_fetch" in body["warnings"]


# ---------------------------------------------------------------------------
# POST /api/v1/jobs — tsmode branch
# ---------------------------------------------------------------------------


class TestTsmodeSubmit:
    def _setup_source_job(
        self, client: TestClient, tmp_path: Path
    ) -> tuple[Path, str]:
        """Create a source job with consistent freq .out + .hess, return (work_dir, job_id)."""
        from tests.tsmode_synthetic import make_consistent_pair

        job_id = "src-freq-001"
        work_dir = _seed_job(client, tmp_path, job_id=job_id)
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)
        out_path, hess_path, _, _, _ = make_consistent_pair(
            freq_dir,
            frequencies_cm1=[-120.5, 450.23, 780.10, 1200.45, 1650.80, 3100.20],
        )
        out_path.rename(freq_dir / "freq_calc.out")
        hess_path.rename(freq_dir / "freq_calc.hess")
        return work_dir, job_id

    def test_missing_source_job_id_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {"source_mode_index": 0},
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "frequency_source_incomplete"

    def test_unknown_source_job_404(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": "nonexistent",
                    "source_mode_index": 0,
                },
            },
        )
        assert resp.status_code == 404

    def test_no_freq_sources_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _seed_job(client, tmp_path, job_id="empty-src-001")
        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": "empty-src-001",
                    "source_mode_index": 0,
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "frequency_source_incomplete"

    def test_hessian_missing_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job_id = "nohess-src-001"
        work_dir = _seed_job(client, tmp_path, job_id=job_id)
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)
        _write_fake_freq_output(freq_dir / "freq.out")

        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": job_id,
                    "source_mode_index": 0,
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "hessian_missing"

    def test_bool_mode_index_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _work_dir, job_id = self._setup_source_job(client, tmp_path)
        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": job_id,
                    "source_mode_index": True,
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "target_mode_invalid"

    def test_successful_submit_rewrites_spec(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _work_dir, job_id = self._setup_source_job(client, tmp_path)

        from acp.calculations.tsmode.contracts import TargetResolution

        synthetic_resolution = TargetResolution(
            source_mode_index=0,
            source_frequency_cm1=-120.5,
            target_mode_id="tm_test123",
            optimizer_mode_index=0,
            status="resolved",
            mapping_method="test",
            mapping_version="v1",
            evidence={},
        )

        def _fake_resolve(bundle, mode_index, **kwargs):  # noqa: ANN001, ANN002
            return synthetic_resolution

        def _fake_gate(resolution, **kwargs):  # noqa: ANN001, ANN002
            return None

        monkeypatch.setattr(
            "acp.calculations.tsmode.mode_mapping.resolve_target_mode",
            _fake_resolve,
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.mode_mapping.enforce_launch_gate",
            _fake_gate,
        )

        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "name": "ts-test-submit",
                "input": {
                    "source_job_id": job_id,
                    "source_mode_index": 0,
                    "allow_unverified_mapping": True,
                },
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["workflow"] == "tsmode"
        created_id = body["job_id"]

        manager = client.app.state.job_manager
        record = manager.get(created_id)
        assert record is not None
        assert record.spec.input["source_type"] == "tsmode_bundle"
        assert record.spec.input["source_mode_index"] == 0
        assert record.spec.input["charge"] == 0
        assert record.spec.input["multiplicity"] == 1
        assert "frequency_out" in record.spec.input
        assert "hess" in record.spec.input
        assert record.spec.input["origin"]["target_mode_id"] == "tm_test123"

    def test_entry_id_selection(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.tsmode_synthetic import make_consistent_pair

        job_id = "multi-src-001"
        work_dir = _seed_job(client, tmp_path, job_id=job_id)
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)

        for name in ("alpha", "beta"):
            out_p, hess_p, _, _, _ = make_consistent_pair(
                freq_dir,
                frequencies_cm1=[-120.5, 450.23, 780.10, 1200.45, 1650.80, 3100.20],
            )
            out_p.rename(freq_dir / f"{name}.out")
            hess_p.rename(freq_dir / f"{name}.hess")

        from acp.calculations.tsmode.contracts import TargetResolution

        synthetic_resolution = TargetResolution(
            source_mode_index=0,
            source_frequency_cm1=-120.5,
            target_mode_id="tm_test456",
            optimizer_mode_index=0,
            status="resolved",
            mapping_method="test",
            mapping_version="v1",
            evidence={},
        )

        def _fake_resolve(bundle, mode_index, **kwargs):  # noqa: ANN001, ANN002
            return synthetic_resolution

        def _fake_gate(resolution, **kwargs):  # noqa: ANN001, ANN002
            return None

        monkeypatch.setattr(
            "acp.calculations.tsmode.mode_mapping.resolve_target_mode",
            _fake_resolve,
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.mode_mapping.enforce_launch_gate",
            _fake_gate,
        )

        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": job_id,
                    "entry_id": "beta",
                    "source_mode_index": 0,
                    "allow_unverified_mapping": True,
                },
            },
        )
        assert resp.status_code == 201
        manager = client.app.state.job_manager
        record = manager.get(resp.json()["job_id"])
        assert "beta" in record.spec.input["frequency_out"]

    def test_ambiguous_entry_id_422(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        job_id = "ambig-001"
        work_dir = _seed_job(client, tmp_path, job_id=job_id)
        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True)
        _write_fake_freq_output(freq_dir / "a.out")
        _write_fake_hess(freq_dir / "a.hess")
        _write_fake_freq_output(freq_dir / "b.out")
        _write_fake_hess(freq_dir / "b.hess")

        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "tsmode",
                "input": {
                    "source_job_id": job_id,
                    "source_mode_index": 0,
                },
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "frequency_source_incomplete"
