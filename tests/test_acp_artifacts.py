"""Tests for scheduler artifact capture and registry persistence."""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from acp.scheduler.artifacts import (
    Artifact,
    ArtifactRegistry,
    ParserStatus,
    capture_stage_artifacts,
    compute_checksum,
    infer_artifact_type,
)
from acp.scheduler.jobs import JobSpec, JobStatus
from acp.scheduler.manager import JobManager


def test_compute_checksum(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    payload = b"artifact-content\n"
    path.write_bytes(payload)

    assert compute_checksum(path) == "sha256:" + hashlib.sha256(payload).hexdigest()


def test_artifact_registry_crud(tmp_path: Path) -> None:
    registry = ArtifactRegistry(tmp_path / "jobs.db")
    artifact = Artifact(
        artifact_id=str(uuid.uuid4()),
        task_id="task-1",
        job_id="job-1",
        artifact_type="xyz",
        file_path="results/mol.xyz",
        checksum="sha256:abc123",
        size_bytes=12,
        parser_status=ParserStatus.PENDING.value,
        metadata={"stage_dir": "results"},
        created_at="2026-01-01T00:00:00+00:00",
    )

    registry.register(artifact)
    fetched = registry.get(artifact.artifact_id)

    assert fetched is not None
    assert fetched.file_path == artifact.file_path
    assert fetched.metadata == artifact.metadata
    assert [item.artifact_id for item in registry.list_by_job("job-1")] == [artifact.artifact_id]
    assert [item.artifact_id for item in registry.list_by_task("task-1")] == [artifact.artifact_id]
    assert [
        item.artifact_id for item in registry.list_by_job_and_type("job-1", "xyz")
    ] == [artifact.artifact_id]
    assert registry.delete(artifact.artifact_id) is True
    assert registry.get(artifact.artifact_id) is None


def test_capture_stage_artifacts_detects_new_files(tmp_path: Path) -> None:
    work_dir = tmp_path / "job"
    stage_dir = work_dir / "results"
    stage_dir.mkdir(parents=True)
    (stage_dir / "old.xyz").write_text("old", encoding="utf-8")
    snapshot_before = {str(path.relative_to(work_dir)) for path in stage_dir.rglob("*") if path.is_file()}

    (stage_dir / "new.xyz").write_text("new", encoding="utf-8")
    (stage_dir / "report.json").write_text("{}", encoding="utf-8")

    registry = ArtifactRegistry(tmp_path / "jobs.db")
    captured = capture_stage_artifacts(
        registry=registry,
        job_id="job-1",
        task_id="task-1",
        work_dir=work_dir,
        stage_dir=stage_dir,
        snapshot_before=snapshot_before,
    )

    assert {artifact.file_path for artifact in captured} == {"results/new.xyz", "results/report.json"}
    assert {artifact.file_path for artifact in registry.list_by_job("job-1")} == {
        "results/new.xyz",
        "results/report.json",
    }


def test_capture_ignores_scratch_files(tmp_path: Path) -> None:
    work_dir = tmp_path / "job"
    stage_dir = work_dir / "results"
    cache_dir = stage_dir / "__pycache__"
    cache_dir.mkdir(parents=True)
    (stage_dir / "keep.xyz").write_text("ok", encoding="utf-8")
    (stage_dir / "scratch.tmp").write_text("tmp", encoding="utf-8")
    (stage_dir / "compiled.pyc").write_bytes(b"pyc")
    (cache_dir / "ignored.pyc").write_bytes(b"pyc")

    registry = ArtifactRegistry(tmp_path / "jobs.db")
    captured = capture_stage_artifacts(
        registry=registry,
        job_id="job-1",
        task_id="task-1",
        work_dir=work_dir,
        stage_dir=stage_dir,
    )

    assert [artifact.file_path for artifact in captured] == ["results/keep.xyz"]


def test_extension_type_mapping() -> None:
    assert infer_artifact_type(Path("molecule.xyz")) == "xyz"
    assert infer_artifact_type(Path("input.gjf")) == "gaussian_input"
    assert infer_artifact_type(Path("output.log")) == "gaussian_log"
    assert infer_artifact_type(Path("table.csv")) == "csv"
    assert infer_artifact_type(Path("input_preview.xyz")) == "xyz"


def test_fake_job_produces_artifacts(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        record = mgr.submit(JobSpec(workflow="fake", name="artifact-demo", input={"source": "CCO"}))

        current = mgr.get(record.id)
        for _ in range(40):
            current = mgr.get(record.id)
            assert current is not None
            if current.status.is_terminal:
                break
            time.sleep(0.5)

        assert current is not None
        assert current.status == JobStatus.COMPLETED
        registry = ArtifactRegistry(mgr.store.db_path)
        artifacts = registry.list_by_job(record.id)
        assert artifacts
        assert any(artifact.file_path == "results/input_preview.xyz" for artifact in artifacts)
        assert all(artifact.parser_status == ParserStatus.PENDING.value for artifact in artifacts)

        preview_path = Path(record.work_dir) / "results" / "input_preview.xyz"
        preview_text = preview_path.read_text(encoding="utf-8")
        lines = preview_text.strip().splitlines()
        assert int(lines[0].strip()) == 9
        symbols = [line.split()[0] for line in lines[2:11]]
        assert sorted(symbols) == sorted(["C", "C", "O", "H", "H", "H", "H", "H", "H"])
    finally:
        mgr.shutdown()
