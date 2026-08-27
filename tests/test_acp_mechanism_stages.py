"""Mechanism stage-runner tests: handoff validation + S2/S3/S4 manifests (plan M4/M7)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.batch import (
    BatchCalculationItem,
    BatchCalculationManifest,
    BatchOptimizeEngine,
    BatchRunOutcome,
    BatchStructureItem,
)
from acp.calculations.batch.options import BatchMethodOptions
from acp.mechanism.providers.contracts import RefinementManifest
from acp.mechanism.stages import pes_search as pes_search_module
from acp.mechanism.stages.handoff import (
    ArtifactRefError,
    copy_handoff_payload,
    expected_source_kind,
    resolve_source_job_work_dir,
    validate_stage_artifact,
)
from acp.mechanism.stages.pes_search import S2_MANIFEST_NAME, normalize_strategy
from tests.conftest import FakeBackend


def _water_xyz(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n",
        encoding="utf-8",
    )
    return path


def _frequency_result(*, imaginary: bool = True, log_path: Path | None = None) -> QCResult:
    frequencies = [-1000.0, 100.0, 200.0] if imaginary else [100.0, 200.0, 300.0]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("fake frequency output\n", encoding="utf-8")
    return QCResult(
        success=True,
        energy=-76.5,
        coordinates=np.array([[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]]),
        symbols=["O", "H", "H"],
        frequencies=frequencies,
        has_frequencies=True,
        freq_log_file=log_path,
    )


def _confsearch_manifest(job_dir: Path) -> Path:
    conf_dir = job_dir / "RESULT" / "confsearch"
    conformers_dir = conf_dir / "conformers"
    _water_xyz(conformers_dir / "conf_0001.xyz")
    payload = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "protocol": "xtb-crest",
        "input": {"source": "CCO", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "conf_id": "conf_0001",
                "geometry": "conformers/conf_0001.xyz",
                "energy_hartree": -76.0,
                "boltzmann_weight": 1.0,
                "rank": 1,
            }
        ],
    }
    path = conf_dir / "confsearch_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- handoff (§8) ------------------------------------------------------------


def test_expected_source_kind_matrix() -> None:
    assert expected_source_kind("S2") == "confsearch_manifest"
    assert expected_source_kind("S3") == "s2_path_manifest"
    assert expected_source_kind("S4") == "s3_lowconfirm_manifest"


def test_validate_stage_artifact_happy_path(tmp_path: Path) -> None:
    manifest = _confsearch_manifest(tmp_path)
    path = validate_stage_artifact(
        source_job_id="job-1",
        relative_path="RESULT/confsearch/confsearch_manifest.json",
        sha256=None,
        kind="confsearch_manifest",
        stage="S2",
        work_dir=tmp_path,
    )
    assert path == manifest


def test_validate_stage_artifact_rejects_traversal(tmp_path: Path) -> None:
    _confsearch_manifest(tmp_path)
    with pytest.raises(ArtifactRefError, match="escapes"):
        validate_stage_artifact(
            source_job_id="job-1",
            relative_path="../../etc/passwd",
            sha256=None,
            kind="confsearch_manifest",
            stage="S2",
            work_dir=tmp_path,
        )


def test_validate_stage_artifact_rejects_wrong_kind(tmp_path: Path) -> None:
    _confsearch_manifest(tmp_path)
    with pytest.raises(ArtifactRefError, match="requires a 's2_path_manifest'"):
        validate_stage_artifact(
            source_job_id="job-1",
            relative_path="RESULT/confsearch/confsearch_manifest.json",
            sha256=None,
            kind="confsearch_manifest",
            stage="S3",
            work_dir=tmp_path,
        )
    # Wrong manifest CONTENT for the declared kind:
    with pytest.raises(ArtifactRefError, match="not a s2_path_manifest"):
        validate_stage_artifact(
            source_job_id="job-1",
            relative_path="RESULT/confsearch/confsearch_manifest.json",
            sha256=None,
            kind="s2_path_manifest",
            stage="S3",
            work_dir=tmp_path,
        )


def test_validate_stage_artifact_rejects_bad_sha(tmp_path: Path) -> None:
    _confsearch_manifest(tmp_path)
    with pytest.raises(ArtifactRefError, match="sha256 mismatch"):
        validate_stage_artifact(
            source_job_id="job-1",
            relative_path="RESULT/confsearch/confsearch_manifest.json",
            sha256="sha256:" + "0" * 64,
            kind="confsearch_manifest",
            stage="S2",
            work_dir=tmp_path,
        )


def test_resolve_source_job_via_job_json_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = tmp_path / "runs" / "ethanol_Confsearch_final"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": "20260823_001_Confsearch"}))
    monkeypatch.chdir(tmp_path)
    found = resolve_source_job_work_dir("20260823_001_Confsearch")
    assert found == job_dir.resolve()
    with pytest.raises(ArtifactRefError):
        resolve_source_job_work_dir("missing-job")


def test_copy_handoff_payload_carries_geometry_dirs(tmp_path: Path) -> None:
    manifest = _confsearch_manifest(tmp_path)
    target = tmp_path / "dest"
    copied = copy_handoff_payload(manifest, target)
    assert copied.is_file()
    assert (target / "conformers" / "conf_0001.xyz").is_file()


# --- strategy normalization (§6.2) -------------------------------------------


def test_normalize_strategy_accepts_plan_and_native_ids() -> None:
    assert normalize_strategy("reverse-peb") == "rph-reverse"
    assert normalize_strategy("guided-scan") == "guided-scan"
    assert normalize_strategy("direct-ts") == "direct-ts"
    assert normalize_strategy(None) == "guided-scan"
    assert normalize_strategy("endpoint-path") == "guided-scan"


# --- S2 runner (fake path strategy) ------------------------------------------


def _fake_path_result() -> SimpleNamespace:
    geometry = np.array([[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]])

    def point(pid: str, progress: float) -> SimpleNamespace:
        return SimpleNamespace(
            point_id=pid,
            progress=progress,
            geometry=geometry.copy(),
            energies_hartree={"xtb": -76.0 + progress},
            topology_valid=True,
            frame_index=None,
        )

    points = [point(f"p{i:03d}", i / 20) for i in range(21)]
    seeds = [
        SimpleNamespace(
            id="ts_seed_p010",
            kind="ts_seed",
            geometry=SimpleNamespace(path="point://p010"),
            rank=1,
            selection_mode="test",
            confidence="high",
            evidence={"point_id": "p010"},
        ),
        SimpleNamespace(
            id="int_seed_p005",
            kind="intermediate_seed",
            geometry=SimpleNamespace(path="point://p005"),
            rank=1,
            selection_mode="test",
            confidence="medium",
            evidence={"point_id": "p005"},
        ),
    ]
    return SimpleNamespace(
        points=points,
        candidates=[],
        seed_candidates=seeds,
        complete=True,
        strategy="guided-scan",
        symbols=["O", "H", "H"],
        point_by_id=lambda pid: next((p for p in points if p.point_id == pid), None),
    )


def test_run_pes_search_writes_s2_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from acp.mechanism.stages import pes_search as ps

    source_job = tmp_path / "source_job"
    manifest = _confsearch_manifest(source_job)
    monkeypatch.setattr(
        ps,
        "_build_path_strategy",
        lambda *a, **k: SimpleNamespace(search=lambda *a, **k: _fake_path_result()),
    )

    payload = ps.run_pes_search(
        from_manifest=manifest,
        output_dir=tmp_path / "pes",
        strategy="guided-scan",
        coordinate_plan={
            "coordinates": [
                {"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}
            ],
            "points": 21,
        },
        source_job_id="job-1",
    )

    out_root = tmp_path / "pes"
    assert payload["schema_version"] == "s2_path_v1"
    assert payload["workflow"] == "PESsearch"
    assert payload["stage"] == "S2"
    assert payload["gates"]["G2"] == "PASS"
    ids = [c["id"] for c in payload["candidates"]]
    assert ids[0] == "ts_guess_001"
    assert "int_guess_001" in ids
    ts_guess = out_root / "RESULT" / "mechanism" / "ts_guesses" / "ts_guess_001.xyz"
    assert ts_guess.is_file()
    assert (out_root / "RESULT" / "mechanism" / S2_MANIFEST_NAME).is_file()
    assert (out_root / "RESULT" / "mechanism" / "path" / "path_profile.json").is_file()
    assert payload["source"]["kind"] == "confsearch_manifest"
    assert payload["source"]["stage"] == "S1"


def test_run_pes_search_direct_ts_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """direct-ts strategy: no ImportError, produces ts_candidate_01."""
    from acp.mechanism.stages import pes_search as ps

    source_job = tmp_path / "source_job"
    manifest = _confsearch_manifest(source_job)

    ts_guess_path = tmp_path / "ts_guess.xyz"
    ts_guess_path.write_text(
        "3\nts guess\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n",
        encoding="utf-8",
    )

    # DirectTsStrategy doesn't populate symbols on PathResult;
    # _symbols_for falls back to point.symbols (also absent) → [].
    # Monkeypatch so _export_candidates can resolve symbols from the reactant.
    monkeypatch.setattr(ps, "_symbols_for", lambda pr, pt: ["O", "H", "H"])

    payload = ps.run_pes_search(
        from_manifest=manifest,
        output_dir=tmp_path / "pes",
        strategy="direct-ts",
        ts_guess=str(ts_guess_path),
        coordinate_plan={
            "coordinates": [
                {"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}
            ],
            "points": 2,
        },
        source_job_id="job-direct-ts",
    )

    assert payload["schema_version"] == "s2_path_v1"
    assert len(payload["candidates"]) > 0
    ts_ids = [c["id"] for c in payload["candidates"] if c["kind"] == "ts_seed"]
    assert ts_ids  # DirectTsStrategy produces ts_candidate_01


def test_run_pes_search_reverse_peb_requires_product(tmp_path: Path) -> None:
    from acp.mechanism.stages import pes_search as ps

    manifest = _confsearch_manifest(tmp_path / "source_job")
    with pytest.raises(ValueError, match="requires a product"):
        ps.run_pes_search(
            from_manifest=manifest,
            output_dir=tmp_path / "pes",
            strategy="reverse-peb",
            coordinate_plan={"coordinates": [], "points": 21},
        )


# --- S3/S4 runners (fake refinement provider) --------------------------------


class _FakeRefinementProvider:
    def __init__(self, energy: float = -76.5, imaginary: bool = True) -> None:
        self.energy = energy
        self.imaginary = imaginary

    def refine(self, requests, fidelity) -> object:
        from acp.mechanism.models import ArtifactRef, StationaryPoint, TsIdentity
        from acp.mechanism.providers.contracts import RefinementAttempt

        attempts = []
        points = []
        for request in requests:
            canonical = Path(request.input_geometry.path)
            out_dir = canonical.parent / f"{request.id}_s3s4"
            out_dir.mkdir(parents=True, exist_ok=True)
            canonical_copy = out_dir / "canonical.xyz"
            canonical_copy.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
            identity = None
            if request.kind == "ts":
                identity = TsIdentity(
                    imaginary_count=1 if self.imaginary else 0,
                    imaginary_frequency_cm1=-1000.0 if self.imaginary else None,
                    valid=self.imaginary,
                )
            point = StationaryPoint(
                point_id=request.id,
                role=request.role,
                kind=request.kind,
                geometry=ArtifactRef(path=str(canonical_copy), sha256="", kind="geometry"),
                charge=request.charge,
                multiplicity=request.multiplicity,
                energy_hartree=self.energy,
                identity=identity,
                metadata={
                    "opt_status": "complete",
                    "frequency_status": "complete",
                    "sp_status": "complete",
                    "thermochemistry": {"g_composite_hartree": self.energy - 0.01},
                    "canonical_xyz": str(canonical_copy),
                },
            )
            attempts.append(
                RefinementAttempt(
                    request_id=request.id,
                    status="success",
                    stationary_point=point,
                    evidence={
                        "canonical_xyz": str(canonical_copy),
                        "canonical_frequency_output": None,
                        "sp_output": None,
                    },
                )
            )
            points.append(point)
        winner = next((p for p in points if p.kind == "ts"), points[0] if points else None)
        return RefinementManifest(
            manifest_id="rm_test",
            canonical_winner=winner,
            attempts=attempts,
            manifest_hash="sha256:test",
            fidelity=str(getattr(fidelity, "name", fidelity)),
            metadata={},
        )


class _FakeEndpointProvider:
    def run_irc(self, ts, fidelity) -> SimpleNamespace:
        return SimpleNamespace(
            irc_id="irc_test",
            ts_id=ts.point_id,
            success=True,
            complete=True,
            forward_endpoint=SimpleNamespace(
                path=str(Path(ts.geometry.path).parent / "fwd.xyz"), sha256="", kind="irc"
            ),
            reverse_endpoint=SimpleNamespace(
                path=str(Path(ts.geometry.path).parent / "rev.xyz"), sha256="", kind="irc"
            ),
            evidence={},
        )


def _s2_manifest_with_ts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from acp.mechanism.stages import pes_search as ps

    source_job = tmp_path / "source_job"
    conf_manifest = _confsearch_manifest(source_job)
    monkeypatch.setattr(
        pes_search_module,
        "_build_path_strategy",
        lambda *a, **k: SimpleNamespace(search=lambda *a, **k: _fake_path_result()),
    )
    ps.run_pes_search(
        from_manifest=conf_manifest,
        output_dir=tmp_path / "pes",
        strategy="guided-scan",
        coordinate_plan={
            "coordinates": [
                {"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}
            ],
            "points": 21,
        },
    )
    return tmp_path / "pes" / "RESULT" / "mechanism" / S2_MANIFEST_NAME


def test_run_low_confirm_writes_s3_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    fake_backend.set_result("frequency", _frequency_result(log_path=tmp_path / "freq.log"))
    payload = lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001"],
        run_irc=False,
    )

    assert payload["schema_version"] == "s3_lowconfirm_v1"
    assert payload["workflow"] == "Lowconfirm"
    row = payload["candidates"][0]
    assert row["id"] == "ts_guess_001"
    assert row["status"] == "confirmed"
    assert row["frequency"]["n_imaginary"] == 1
    assert row["frequency"]["imaginary_frequency_cm1"] == -1000.0
    assert payload["gates"]["G3"] == "PASS"
    assert (tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json").is_file()
    assert (tmp_path / "low" / "RESULT" / "mechanism" / "optimized" / "ts_guess_001.xyz").is_file()


def test_lowconfirm_via_batchengine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    fake_backend.set_result("frequency", _frequency_result(log_path=tmp_path / "freq.log"))

    from acp.mechanism.stages import low_confirm as lc

    payload = lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001"],
        run_irc=False,
    )

    batch_manifest = json.loads(
        (tmp_path / "low" / "RESULT" / "mechanism" / "batch_calculation_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert batch_manifest["profile"] == "opt_freq"
    row = payload["candidates"][0]
    assert row["status"] == "confirmed"
    assert row["frequency"]["n_imaginary"] == 1
    assert row["frequency"]["imaginary_frequency_cm1"] == -1000.0
    assert payload["gates"]["G3"] == "PASS"


def test_lowconfirm_engine_error_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acp.calculations.batch import BatchOptimizeEngine
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)

    class _RaisingBatchEngine(BatchOptimizeEngine):
        def run(
            self,
            items: list[BatchStructureItem],
            *,
            profile: str,
            charge: int = 0,
            multiplicity: int = 1,
            workflow: str = "BatchOptimize",
            methods: BatchMethodOptions | None = None,
        ) -> BatchRunOutcome:
            del items, profile, charge, multiplicity, workflow, methods
            raise RuntimeError("batch engine exploded")

    monkeypatch.setattr(lc, "BatchOptimizeEngine", _RaisingBatchEngine)
    with pytest.raises(RuntimeError, match="batch engine exploded"):
        lc.run_low_confirm(
            from_manifest=s2_manifest,
            output_dir=tmp_path / "low",
            select=["ts_guess_001"],
            run_irc=False,
        )

    payload = json.loads(
        (tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "failed"
    assert payload["errors"] == ["batch engine exploded"]
    assert payload["candidates"][0]["status"] == "failed"
    assert payload["gates"]["G3"] == "FAIL"


def test_run_low_confirm_with_irc_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    fake_backend.set_result("frequency", _frequency_result(log_path=tmp_path / "freq.log"))
    payload = lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001"],
        run_irc=True,
        endpoint_provider=_FakeEndpointProvider(),
    )
    assert payload["irc"]["complete"] is True
    assert set(payload["irc"]["endpoints"]) == {"forward", "reverse"}


def test_run_high_confirm_writes_s4_and_mechanism_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    from acp.mechanism.stages import high_confirm as hc
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "acp.calculations.primitives.thermochemistry.run_shermo",
        lambda **_kwargs: {"g_sum": -76.51},
    )
    fake_backend.set_results(
        "frequency",
        [
            _frequency_result(log_path=tmp_path / "low_ts_freq.log"),
            _frequency_result(imaginary=False, log_path=tmp_path / "low_int_freq.log"),
            _frequency_result(log_path=tmp_path / "high_ts_freq.log"),
            _frequency_result(imaginary=False, log_path=tmp_path / "high_int_freq.log"),
        ],
    )
    low_payload = lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001", "int_guess_001"],
        run_irc=False,
    )
    s3_manifest = tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json"
    assert low_payload["gates"]["G3"] == "PASS"

    payload = hc.run_high_confirm(
        from_manifest=s3_manifest,
        output_dir=tmp_path / "high",
        select=["ts_guess_001", "int_guess_001"],
        run_irc=True,
        endpoint_provider=_FakeEndpointProvider(),
    )

    result_dir = tmp_path / "high" / "RESULT" / "mechanism"
    assert payload["schema_version"] == "s4_highconfirm_v1"
    assert payload["workflow"] == "Highconfirm"
    assert payload["s3_s4_consistency"] == []
    assert payload["irc"]["complete"] is True
    profile = json.loads((result_dir / "mechanism_profile.json").read_text(encoding="utf-8"))
    assert profile["transition_states"][0]["id"] == "ts_guess_001"
    assert profile["transition_states"][0]["imaginary_frequency_cm1"] == -1000.0
    # A single endpoint minimum → no barrier pair computable.
    assert profile["barriers"][0]["forward_barrier_kcal"] is None


def test_high_confirm_refuses_unconfirmed_s3_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    from acp.mechanism.stages import high_confirm as hc
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    fake_backend.set_result("frequency", _frequency_result(log_path=tmp_path / "freq.log"))
    lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001"],
        run_irc=False,
    )
    s3_path = tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json"
    s3_payload = json.loads(s3_path.read_text(encoding="utf-8"))
    s3_payload["candidates"][0]["status"] = "failed"
    s3_path.write_text(json.dumps(s3_payload), encoding="utf-8")

    with pytest.raises(ValueError, match="S3 manifest"):
        hc.run_high_confirm(
            from_manifest=s3_path,
            output_dir=tmp_path / "high",
            select=["ts_guess_001"],
        )


def test_high_confirm_rejects_s3_s4_hessian_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_backend: FakeBackend,
) -> None:
    from acp.mechanism.stages import high_confirm as hc
    from acp.mechanism.stages import low_confirm as lc

    s2_manifest = _s2_manifest_with_ts(tmp_path, monkeypatch)
    fake_backend.set_result("frequency", _frequency_result(log_path=tmp_path / "s3_freq.log"))
    lc.run_low_confirm(
        from_manifest=s2_manifest,
        output_dir=tmp_path / "low",
        select=["ts_guess_001"],
        run_irc=False,
    )
    s3_path = tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json"

    class _MismatchBatchEngine(BatchOptimizeEngine):
        def run(
            self,
            items: list[BatchStructureItem],
            *,
            profile: str,
            charge: int = 0,
            multiplicity: int = 1,
            workflow: str = "BatchOptimize",
            methods: BatchMethodOptions | None = None,
        ) -> BatchRunOutcome:
            del methods
            records: list[BatchCalculationItem] = []
            for item in items:
                record = BatchCalculationItem.from_item(item, charge, multiplicity)
                record.status = "completed"
                record.frequency = {"status": "completed", "frequencies": [100.0, 200.0]}
                record.single_point = {"status": "completed", "energy_hartree": -76.8}
                record.thermochemistry = {"gibbs_hartree": -76.81}
                records.append(record)
            manifest = BatchCalculationManifest(
                profile=profile,
                items=records,
                workflow=workflow,
            )
            return BatchRunOutcome(profile=profile, manifest=manifest)

    monkeypatch.setattr(hc, "BatchOptimizeEngine", _MismatchBatchEngine)
    payload = hc.run_high_confirm(
        from_manifest=s3_path,
        output_dir=tmp_path / "high",
        select=["ts_guess_001"],
    )

    assert payload["s3_s4_consistency"] == [
        "hessian_index not preserved: s3 n_imaginary=1 -> s4 n_imaginary=0"
    ]
    assert payload["gates"]["s3_s4_consistent"] is False
    assert payload["gates"]["G5"] == "FAIL"
    assert payload["candidates"][0]["status"] == "confirmed"
