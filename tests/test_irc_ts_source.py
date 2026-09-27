"""IRC accepts only frequency-verified final TS products at their source level."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.calculations.irc.source import resolve_verified_ts_source
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager


def _source(tmp_path: Path, *, frequencies: list[float] | None = None) -> JobRecord:
    root = tmp_path / "source"
    structure = root / "RESULT" / "structures" / "item_001__TAG_TS__optimized.xyz"
    structure.parent.mkdir(parents=True)
    structure.write_text("2\nTAG: TS\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")
    optimized = root / "WORK" / "03_OPT" / "optimized.xyz"
    optimized.parent.mkdir(parents=True)
    optimized.write_text("2\noptimized\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")
    (root / "RESULT" / "result_manifest.json").write_text(
        json.dumps({"workflow": "BatchOptimize", "products": [{
            "id": "batch_item_001", "kind": "structure",
            "path": "structures/item_001__TAG_TS__optimized.xyz", "label": "TS result",
        }]}), encoding="utf-8",
    )
    runtime = root / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True)
    (runtime / "checkpoint.json").write_text(json.dumps({"items_state": {
        "item_001": {
            "status": "completed", "tag": "TS",
            "optimized_xyz": "WORK/03_OPT/optimized.xyz",
            "charge": 0, "multiplicity": 1,
            "frequency": {"status": "completed", "frequencies": frequencies if frequencies is not None else [-321.5, 44.0]},
        }
    }}), encoding="utf-8")
    (root / "RESULT" / "batch_provenance.json").write_text(json.dumps({"items": [{
        "item_id": "item_001", "role": "ts",
        "effective_config": {"method": "B97-3c", "basis": "def2-mTZVPP"},
    }]}), encoding="utf-8")
    return JobRecord(
        id="source-job", status=JobStatus.COMPLETED, work_dir=str(root),
        spec=JobSpec(workflow="BatchOptimize", method={"batch_roles": {
            "ts": {"method": "B97-3c", "basis": "def2-mTZVPP"},
            "int": {"method": "r2SCAN-3c", "basis": ""},
        }}),
    )


def test_irc_inherits_ts_level_not_int_or_single_point(tmp_path: Path) -> None:
    record = _source(tmp_path)
    resolved = resolve_verified_ts_source(record, "batch_item_001")
    assert resolved.method == "B97-3c"
    assert resolved.basis == "def2-mTZVPP"
    assert resolved.imaginary_frequency_cm1 == -321.5

    manager = JobManager.__new__(JobManager)
    manager.store = type("Store", (), {"get": lambda self, job_id: record})()
    spec = manager._verified_irc_spec(JobSpec(
        workflow="irc",
        input={"source_job_id": record.id, "source_product_id": "batch_item_001"},
        method={"maxpoints": 55, "step": 0.05},
    ))
    assert spec.method == {"method": "B97-3c", "basis": "def2-mTZVPP", "maxpoints": 55, "step": 0.05}
    assert spec.input["ts_source"]["geometry_sha256"] == resolved.geometry_sha256
    assert spec.input["charge"] == 0


@pytest.mark.parametrize("frequencies", [[], [10.0, 20.0], [-100.0, -20.0]])
def test_irc_rejects_unverified_frequency(tmp_path: Path, frequencies: list[float]) -> None:
    record = _source(tmp_path, frequencies=frequencies)
    with pytest.raises(ValueError, match="frequency|imaginary"):
        resolve_verified_ts_source(record, "batch_item_001")


def test_irc_rejects_method_override_and_other_products(tmp_path: Path) -> None:
    record = _source(tmp_path)
    manager = JobManager.__new__(JobManager)
    manager.store = type("Store", (), {"get": lambda self, job_id: record})()
    with pytest.raises(ValueError, match="must match"):
        manager._verified_irc_spec(JobSpec(
            workflow="irc",
            input={"source_job_id": record.id, "source_product_id": "batch_item_001"},
            method={"method": "r2SCAN-3c"},
        ))
    with pytest.raises(ValueError, match="charge must match"):
        manager._verified_irc_spec(JobSpec(
            workflow="irc",
            input={"source_job_id": record.id, "source_product_id": "batch_item_001",
                   "charge": 1},
        ))
    with pytest.raises(ValueError, match="solvent override"):
        manager._verified_irc_spec(JobSpec(
            workflow="irc",
            input={"source_job_id": record.id, "source_product_id": "batch_item_001"},
            method={"solvent": "water"},
        ))
    with pytest.raises(ValueError, match="final structure"):
        resolve_verified_ts_source(record, "unknown")


def test_irc_rejects_unreproducible_state_and_changed_evidence(tmp_path: Path) -> None:
    record = _source(tmp_path)
    manager = JobManager.__new__(JobManager)
    manager.store = type("Store", (), {"get": lambda self, job_id: record})()
    source = resolve_verified_ts_source(record, "batch_item_001")
    stale = source.provenance()
    stale["method"] = "r2SCAN-3c"
    with pytest.raises(ValueError, match="evidence changed"):
        manager._verified_irc_spec(JobSpec(
            workflow="irc",
            input={"source_job_id": record.id, "source_product_id": "batch_item_001",
                   "ts_source": stale},
        ))

    checkpoint_path = Path(record.work_dir) / "WORK" / "00_RUNTIME" / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["items_state"]["item_001"]["state_id"] = "broken_symmetry"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(ValueError, match="electronic-state"):
        resolve_verified_ts_source(record, "batch_item_001")


def test_irc_reads_remote_ts_evidence_on_demand(tmp_path: Path) -> None:
    record = _source(tmp_path)
    remote_root = Path(record.work_dir)
    record.result = {"node": "compute-1", "remote_dir": "/jobs/source-job"}
    record.work_dir = str(tmp_path / "local-empty-mirror")

    class Fetcher:
        def read_file(self, source_record: JobRecord, relative: str) -> bytes:
            assert source_record is record
            return (remote_root / relative).read_bytes()

    resolved = resolve_verified_ts_source(record, "batch_item_001", Fetcher())
    assert resolved.method == "B97-3c"
    assert resolved.job_id == "source-job"
