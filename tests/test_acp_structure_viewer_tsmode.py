"""Tests for acp.results.structure_viewer — tsmode resolver."""

from __future__ import annotations

import json
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.results.structure_viewer import build_structure_viewer_payload


def test_module_imports():
    from acp.results.structure_viewer import (  # noqa: F401
        StructureViewerEntry,
        build_structure_viewer_payload,
        tsmode_entry_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_XYZ = (
    "3\n"
    "TS optimized structure comment\n"
    "C 0.0 0.0 0.0\nH 1.0 0.0 0.0\nH -1.0 0.0 0.0\n"
)

_SAMPLE_NORMAL_MODES = {
    "schema_version": "normal_modes_v1",
    "modes": [
        {"index": 1, "frequency_cm": 1500.0, "ir_intensity": 10.0, "displacements": [[0.1, 0.0, 0.0]]},
    ],
}


def _make_tsmode_task(
    tmp_path: Path,
    *,
    optimized_xyz: str | None = None,
    normal_modes: dict | None = None,
    source_xyz: str | None = None,
    result_manifest: dict | None = None,
) -> Path:
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")

    if result_manifest is not None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps(result_manifest), encoding="utf-8"
        )

    if optimized_xyz is not None or normal_modes is not None:
        tsmode_dir = tmp_path / "RESULT" / "tsmode"
        tsmode_dir.mkdir(parents=True, exist_ok=True)
        if optimized_xyz is not None:
            (tsmode_dir / "optimized.xyz").write_text(optimized_xyz, encoding="utf-8")
        if normal_modes is not None:
            (tsmode_dir / "normal_modes.json").write_text(
                json.dumps(normal_modes), encoding="utf-8"
            )

    if source_xyz is not None:
        src_dir = tmp_path / "INPUT" / "tsmode"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "source.xyz").write_text(source_xyz, encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Entry-id helper tests
# ---------------------------------------------------------------------------


class TestTsmodeEntryId:
    def test_basic(self):
        from acp.results.structure_viewer import tsmode_entry_id
        assert tsmode_entry_id("optimized") == "tsmode_optimized"

    def test_source(self):
        from acp.results.structure_viewer import tsmode_entry_id
        assert tsmode_entry_id("source") == "tsmode_source"

    def test_exported(self):
        import acp.results.structure_viewer as sv
        assert "tsmode_entry_id" in sv.__all__


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------


class TestTsmodeResolver:
    def test_optimized_xyz_only(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "tsmode_optimized"
        assert entry.role == "transition_state"
        assert entry.status == "completed"
        assert entry.source.kind == "formal_result"
        assert entry.source.geometry_ref == "RESULT/tsmode/optimized.xyz"
        assert payload.default_entry_id == "tsmode_optimized"

    def test_xyz_label_from_comment(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].label == "TS optimized structure comment"

    def test_xyz_empty_comment_fallback(self, tmp_path: Path):
        xyz = "3\n\nC 0 0 0\nH 1 0 0\nH -1 0 0\n"
        task = _make_tsmode_task(tmp_path, optimized_xyz=xyz)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].label == "TS Mode 优化结果"

    def test_vibrations_available_with_normal_modes(self, tmp_path: Path):
        task = _make_tsmode_task(
            tmp_path,
            optimized_xyz=_SAMPLE_XYZ,
            normal_modes=_SAMPLE_NORMAL_MODES,
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        vib = payload.entries[0].vibrations
        assert vib.available is True
        assert "j1" in vib.endpoint
        assert "tsmode_optimized" in vib.endpoint
        assert vib.source == "product"

    def test_vibrations_unavailable_without_normal_modes(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].vibrations.available is False

    def test_source_entry_when_source_xyz_present(self, tmp_path: Path):
        task = _make_tsmode_task(
            tmp_path,
            optimized_xyz=_SAMPLE_XYZ,
            source_xyz="2\nsource\nC 0 0 0\nH 1 0 0\n",
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 2
        source_entry = next(e for e in payload.entries if e.id == "tsmode_source")
        assert source_entry.label == "Source structure"
        assert source_entry.role == "minimum"
        assert source_entry.source.kind == "calculation_input"
        assert source_entry.source.geometry_ref == "INPUT/tsmode/source.xyz"

    def test_empty_dir_warning(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert any("tsmode results not found" in w for w in payload.warnings)

    def test_no_warning_when_optimized_exists(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert not any("tsmode results not found" in w for w in payload.warnings)

    def test_geometry_endpoint_uses_job_id(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="my_job", workflow="tsmode", job_status="completed"
        )
        assert "my_job" in payload.entries[0].geometry.endpoint
        assert "tsmode_optimized" in payload.entries[0].geometry.endpoint

    def test_manifest_absent_still_works(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].id == "tsmode_optimized"

    def test_dispatch_table_registered(self):
        from acp.results.structure_viewer import _DISPATCH_TABLE
        assert "tsmode" in _DISPATCH_TABLE


# ---------------------------------------------------------------------------
# Engine-produced consumer projection (plan todo 12 — R5 acceptance part B)
# ---------------------------------------------------------------------------

_ENGINE_JOB_ID = "t12b_engine_001"


def _run_engine_resumed(synth_dir: Path, task_root: Path) -> dict[str, int]:
    """Fresh engine run + resume run with mocked QC; returns QC call counters."""
    import numpy as np

    from acp.calculations.contracts import ArtifactRef, CalculationResult
    from acp.calculations.tsmode.contracts import TsmodeOptimizationSettings, TsmodeRequest
    from acp.calculations.tsmode.engine import TsmodeEngine
    from acp.calculations.tsmode.source import load_bundle_from_files
    from tests.tsmode_synthetic import make_consistent_pair, write_out_file

    synth_dir.mkdir(parents=True, exist_ok=True)
    out_path, hess_path, _coords, freqs_by_native, cart_modes_by_native = make_consistent_pair(
        synth_dir
    )
    bundle = load_bundle_from_files(out_path, hess_path)
    optimized = np.asarray(bundle.coordinates_angstrom) + 0.02

    log_path = synth_dir / "freq_final.log"
    write_out_file(log_path, optimized, freqs_by_native, cart_modes_by_native)
    frequencies = [freqs_by_native[index] for index in sorted(freqs_by_native)]
    calls = {"optimize": 0, "frequency": 0}

    def fake_optimize(req):  # noqa: ANN001
        calls["optimize"] += 1
        return CalculationResult(
            energy=-100.5,
            coords=[[float(v) for v in row] for row in optimized],
            status="completed",
            artifacts=[
                ArtifactRef(path=Path("ts_opt.out"), type="output"),
                ArtifactRef(path=Path("ts_opt.log"), type="log"),
            ],
        )

    def fake_frequency(req):  # noqa: ANN001
        calls["frequency"] += 1
        return CalculationResult(
            energy=-100.6,
            frequencies=list(frequencies),
            status="completed",
            artifacts=[ArtifactRef(path=log_path, type="log")],
        )

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr("acp.calculations.tsmode.engine.run_optimize", fake_optimize)
        mp.setattr("acp.calculations.tsmode.engine.run_frequency", fake_frequency)
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / "job.json").write_text("{}")
        (task_root / "task.json").write_text("{}")
        request = TsmodeRequest(
            source={"kind": "test"},
            source_mode_index=8,
            optimization=TsmodeOptimizationSettings(require_verified_mapping=False),
            resources={},
            request_id="req_t12b_consumer",
        )
        engine = TsmodeEngine(config={})
        engine.run(request, bundle, task_root)
        engine.run(request, bundle, task_root)
    finally:
        mp.undo()
    assert calls == {"optimize": 1, "frequency": 1}, "resume must not repeat QC"
    return calls


@pytest.fixture(scope="module")
def engine_task_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Resumed engine-produced task root shared read-only across tests."""
    base = tmp_path_factory.mktemp("t12b_engine")
    task_root = base / "runs" / _ENGINE_JOB_ID
    _run_engine_resumed(base / "synthetic", task_root)
    return task_root


@pytest.fixture()
def engine_viewer_client(
    engine_task_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, Path], None, None]:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    task_root = run_root / _ENGINE_JOB_ID
    shutil.copytree(engine_task_root, task_root)
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))

    from acp.api.server import create_app
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    with TestClient(create_app(run_root=run_root, max_running=2)) as client:
        manager = client.app.state.job_manager
        record = JobRecord(
            id=_ENGINE_JOB_ID,
            spec=JobSpec(
                workflow="tsmode",
                name=_ENGINE_JOB_ID,
                project_id=manager.default_project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(task_root),
            project_id=manager.default_project_id,
        )
        manager.store.create(record)
        yield client, task_root


def _canonical_normal_modes(task_root: Path) -> dict:
    return json.loads(
        (task_root / "RESULT" / "tsmode" / "normal_modes.json").read_text(encoding="utf-8")
    )


def _get_vibrations(client: TestClient) -> dict:
    resp = client.get(
        f"/api/v1/jobs/{_ENGINE_JOB_ID}/structure-viewer/entries/tsmode_optimized/vibrations"
    )
    assert resp.status_code == 200
    return resp.json()


class TestEngineProducedConsumerProjection:
    def test_catalog_projects_resumed_engine_vibrations(
        self, engine_viewer_client: tuple[TestClient, Path]
    ) -> None:
        """Catalog entry for a resumed engine root advertises product vibrations."""
        client, task_root = engine_viewer_client
        resp = client.get(f"/api/v1/jobs/{_ENGINE_JOB_ID}/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_entry_id"] == "tsmode_optimized"
        entry = next(e for e in body["entries"] if e["id"] == "tsmode_optimized")
        vib = entry["vibrations"]
        assert vib["available"] is True
        assert vib["source"] == "product"
        assert vib["endpoint"] == (
            f"/api/v1/jobs/{_ENGINE_JOB_ID}/structure-viewer"
            "/entries/tsmode_optimized/vibrations"
        )

        canonical = _canonical_normal_modes(task_root)
        assert canonical["schema_version"] == "normal_modes_v1"
        assert len(canonical["modes"]) == 9
        assert all(m["vectors"] for m in canonical["modes"])

    def test_vibrations_endpoint_resolves_entry_and_stays_200(
        self, engine_viewer_client: tuple[TestClient, Path]
    ) -> None:
        """Endpoint resolves the catalog's tsmode entry and never returns 500."""
        client, _task_root = engine_viewer_client
        catalog = client.get(f"/api/v1/jobs/{_ENGINE_JOB_ID}/structure-viewer")
        assert catalog.status_code == 200
        default_id = catalog.json()["default_entry_id"]
        assert default_id == "tsmode_optimized"

        body = _get_vibrations(client)
        assert set(body) >= {
            "available", "reason", "threshold_cm1", "threshold_source",
            "modes", "atom_count", "geometry_product_id", "imaginary_count",
        }
        assert isinstance(body["modes"], list)
        assert body["reason"] in (None, "no_normal_modes")

    def test_vibrations_endpoint_serves_canonical_tsmode_modes(
        self, engine_viewer_client: tuple[TestClient, Path]
    ) -> None:
        """Endpoint payload equals the canonical RESULT/tsmode/normal_modes.json."""
        client, task_root = engine_viewer_client
        canonical = _canonical_normal_modes(task_root)

        catalog = client.get(f"/api/v1/jobs/{_ENGINE_JOB_ID}/structure-viewer")
        assert catalog.status_code == 200
        entry = next(
            e for e in catalog.json()["entries"] if e["id"] == "tsmode_optimized"
        )
        assert entry["vibrations"]["available"] is True

        body = _get_vibrations(client)
        assert body["available"] is True
        assert body["reason"] is None
        assert body["source"] == "product"
        assert body["atom_count"] == canonical["atom_count"]
        assert body["geometry_product_id"] == "tsmode_optimized"

        canonical_by_index = {m["mode_index"]: m for m in canonical["modes"]}
        modes = body["modes"]
        assert len(modes) == len(canonical_by_index) == 9
        assert {m["mode_index"] for m in modes} == set(canonical_by_index)
        for mode in modes:
            expected = canonical_by_index[mode["mode_index"]]
            assert mode["frequency_cm1"] == pytest.approx(expected["frequency_cm1"])
            assert mode["vectors"], f"mode {mode['mode_index']} lost its vectors"
            assert len(mode["vectors"]) == canonical["atom_count"]
            assert mode["vectors"] == expected["vectors"]
