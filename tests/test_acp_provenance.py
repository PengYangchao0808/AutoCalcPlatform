"""Tests for scheduler provenance and result schema helpers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.provenance import (
    ParserRegistry,
    Provenance,
    ResultSchema,
    build_provenance_for_job,
    compute_input_hash,
)


def test_compute_input_hash_is_stable() -> None:
    spec = JobSpec(
        workflow="conformer",
        input={"source": "CCO", "charge": 0, "multiplicity": 1},
        method={"protocol": "ext", "backend": "gaussian"},
        resources={"nproc": 4, "mem": "8GB"},
    )

    assert compute_input_hash(spec) == compute_input_hash(spec)


def test_compute_input_hash_differs_on_method_change() -> None:
    base = JobSpec(workflow="conformer", input={"source": "CCO"}, method={"protocol": "ext"})
    changed = replace(base, method={"protocol": "reference-sp"})

    assert compute_input_hash(base) != compute_input_hash(changed)


def test_compute_input_hash_excludes_runtime_data() -> None:
    base = JobSpec(
        workflow="nmr",
        name="baseline",
        input={"source": "mol.xyz"},
        method={"protocol": "default", "backend": "gaussian"},
        resources={"nproc": 8},
        output_dir="/tmp/out-a",
        config_path="a.yaml",
        tags=["alpha"],
        project_id="project-a",
    )
    variant = replace(
        base,
        name="renamed",
        output_dir="/tmp/out-b",
        config_path="b.yaml",
        tags=["beta"],
        project_id="project-b",
    )

    assert compute_input_hash(base) == compute_input_hash(variant)


def test_provenance_dataclass_defaults() -> None:
    provenance = Provenance(input_hash="sha256:test")

    assert provenance.backend_name == ""
    assert provenance.command_line == ""
    assert provenance.schema_version == "1.0"
    assert provenance.memory_gb is None


def test_result_schema_dataclass() -> None:
    result = ResultSchema(success=True, return_value=[1.2, 3.4], properties={"units": "kcal/mol"})

    assert result.success is True
    assert result.exit_status == 0
    assert result.return_value == [1.2, 3.4]
    assert result.properties == {"units": "kcal/mol"}
    assert result.schema_name == "acp_result"


def test_build_provenance_for_job() -> None:
    spec = JobSpec(
        workflow="fake",
        input={"source": "CCO"},
        method={"protocol": "ext", "basis": "6-31G*", "solvent": "water"},
        resources={"nproc": 4, "mem": "8GB"},
    )
    record = JobRecord(
        id="job-1",
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir="/tmp/job-1",
        input_hash=compute_input_hash(spec),
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:02+00:00",
        result={"command_line": "fake --demo", "creator": "pytest"},
    )

    provenance = build_provenance_for_job(spec, record)

    assert provenance.input_hash == compute_input_hash(spec)
    assert provenance.acp_version
    assert provenance.backend_name == "fake"
    assert provenance.method == "demo"
    assert provenance.basis == "6-31G*"
    assert provenance.solvent == "water"
    assert provenance.command_line == "fake --demo"
    assert provenance.hostname
    assert provenance.ncores == 4
    assert provenance.memory_gb == 8.0
    assert provenance.wall_time_seconds == 2.0
    assert provenance.routine == "fake"
    assert provenance.creator == "pytest"


def test_parser_registry(tmp_path: Path) -> None:
    registry = ParserRegistry()
    sample = tmp_path / "result.json"
    sample.write_text('{"ok": true}', encoding="utf-8")

    registry.register("json", lambda path: {"path": path.name, "size": path.stat().st_size})

    assert registry.has_parser("json") is True
    assert registry.parse("json", sample) == {"path": "result.json", "size": sample.stat().st_size}
