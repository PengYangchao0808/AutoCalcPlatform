"""Wave 2 Todo 15: generic checkpoint continuation for local and remote jobs."""

# pyright: reportAny=false, reportExplicitAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedCallResult=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownLambdaType=false, reportUnannotatedClassAttribute=false

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.calculations import Checkpoint
from acp.calculations.checkpoint import write_checkpoint
from acp.calculations.contracts import JsonValue, StructureArtifact, StructureRole
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.workflows.irc import run_irc_workflow
from tests.conftest import FakeBackend


def _make_manager(tmp_path: Path) -> JobManager:
    return JobManager(run_root=tmp_path / "runs", poll_interval=30)


def _seed_job(
    manager: JobManager,
    work_dir: Path,
    job_id: str,
    *,
    workflow: str = "singlepoint",
    status: JobStatus = JobStatus.FAILED,
    result: dict[str, JsonValue] | None = None,
    remote_job_id: str | None = None,
) -> JobRecord:
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow=workflow, name=job_id),
        status=status,
        work_dir=str(work_dir),
        error="interrupted",
        exit_code=1,
        result=result,
        remote_job_id=remote_job_id,
    )
    manager.store.create(record)
    return record


def _checkpoint_bytes(tmp_path: Path, fingerprint: str, workflow: str = "singlepoint") -> bytes:
    checkpoint_dir = tmp_path / "remote-checkpoint" / "WORK" / "00_RUNTIME"
    write_checkpoint(
        checkpoint_dir,
        Checkpoint(
            task_id="executor",
            workflow=workflow,
            plan_fingerprint=fingerprint,
            step_states=[
                {
                    "index": 0,
                    "kind": "singlepoint",
                    "status": "completed",
                    "error": None,
                    "energy": -1.0,
                }
            ],
            items_state={},
            attempts=0,
        ),
    )
    return (checkpoint_dir / "checkpoint.json").read_bytes()


class _RemoteFetcher:
    """Minimal on-demand fetcher fake used to model SFTP read semantics."""

    def __init__(self, payload: bytes | None) -> None:
        self.payload: bytes | None = payload
        self.calls: list[tuple[str, str]] = []

    def read_file(self, record: JobRecord, filename: str) -> bytes:
        self.calls.append((record.id, filename))
        if self.payload is None:
            raise FileNotFoundError(filename)
        return self.payload


def _remote_result(fingerprint: str) -> dict[str, JsonValue]:
    return {
        "execution_kind": "remote",
        "node": "compute-01",
        "remote_dir": "/scratch/test/acp_jobs/singlepoint-task",
        "plan_fingerprint": fingerprint,
    }


def test_simple_workflow_resume_skips_done(
    fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A completed executor step is not dispatched again after continue."""
    manager = _make_manager(tmp_path)
    input_path = tmp_path / "molecule.xyz"
    _ = input_path.write_text("1\n\nC 0.0 0.0 0.0\n", encoding="utf-8")
    work_dir = tmp_path / "runs" / "singlepoint-task"
    (work_dir / "job.json").parent.mkdir(parents=True, exist_ok=True)
    _ = (work_dir / "job.json").write_text("{}", encoding="utf-8")
    _ = (work_dir / "task.json").write_text("{}", encoding="utf-8")
    record = _seed_job(manager, work_dir, "simple-interrupted")

    try:
        # Given: todo 14's simple adapter has completed its executor step and
        # persisted the scheduler-compatible checkpoint.
        from acp.workflows.simple import run_singlepoint

        first = run_singlepoint(str(input_path), output_dir=work_dir)
        assert first.status == "completed"
        checkpoint_path = work_dir / "WORK" / "00_RUNTIME" / "checkpoint.json"
        assert checkpoint_path.is_file()
        calls_before_continue = len(fake_backend.calls)

        # Simulate: the scheduler observed an interrupted subprocess after
        # the completed checkpoint was written.
        persisted = manager.get(record.id)
        assert persisted is not None
        persisted.status = JobStatus.FAILED
        manager.store.update(persisted)
        submissions: list[str] = []
        monkeypatch.setattr(
            manager,
            "_start_submission_thread",
            lambda job_id, thread_name: submissions.append(job_id) or True,
        )

        # When: the failed job is continued and the simple executor is
        # re-entered against the same task root.
        continued = manager.continue_job(record.id)
        resumed = run_singlepoint(str(input_path), output_dir=work_dir)

        # Then: only the unfinished work would be dispatched; this checkpoint
        # has no unfinished steps, so the fake backend call count is unchanged.
        assert continued.status == JobStatus.QUEUED
        assert submissions == [record.id]
        assert resumed.status == "completed"
        assert len(fake_backend.calls) == calls_before_continue
    finally:
        manager.shutdown()


def test_irc_pause_and_unpause_local_job(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        _seed_job(
            manager,
            tmp_path / "runs" / "irc-task",
            "irc-paused",
            status=JobStatus.RUNNING,
            workflow="irc",
        )
        manager.runner._processes["irc-paused"] = process

        paused = manager.pause_job("irc-paused")
        assert paused.status == JobStatus.PAUSED
        time.sleep(0.05)
        assert process.poll() is None

        resumed = manager.unpause_job("irc-paused")
        assert resumed.status == JobStatus.RUNNING
        assert process.poll() is None
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), 15)
            process.wait(timeout=10)
        manager.shutdown()


def test_irc_interruption_leaves_checkpoint_for_continue(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "ts.xyz"
    input_path.write_text("2\nTS\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")
    output_root = tmp_path / "irc-task"
    artifact = StructureArtifact(
        path=input_path,
        role=StructureRole.TRANSITION_STATE,
    )

    with patch("acp.workflows.irc.run_irc", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run_irc_workflow(artifact, output_dir=output_root)

    checkpoint = output_root / "WORK" / "00_RUNTIME" / "checkpoint.json"
    payload = checkpoint.read_text(encoding="utf-8")
    assert '"workflow": "irc"' in payload
    assert '"status": "running"' in payload


def test_irc_continue_requeues_from_generic_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(tmp_path)
    work_dir = tmp_path / "runs" / "irc-task"
    record = _seed_job(manager, work_dir, "irc-interrupted", workflow="irc")
    write_checkpoint(
        work_dir / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id="irc",
            workflow="irc",
            plan_fingerprint="irc-fingerprint",
            step_states=[
                {
                    "index": 0,
                    "kind": "irc",
                    "status": "running",
                    "error": None,
                }
            ],
            items_state={},
            attempts=0,
        ),
    )
    submissions: list[str] = []
    monkeypatch.setattr(
        manager,
        "_start_submission_thread",
        lambda job_id, thread_name: submissions.append(job_id) or True,
    )

    try:
        continued = manager.continue_job(record.id)

        assert continued.status == JobStatus.QUEUED
        assert continued.result is not None
        assert continued.result["continued_from"] == "failed"
        assert submissions == [record.id]
    finally:
        manager.shutdown()


def test_batchoptimize_continue_requeues_from_generic_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(tmp_path)
    work_dir = tmp_path / "runs" / "batch-optimize-task"
    record = _seed_job(
        manager,
        work_dir,
        "batch-optimize-interrupted",
        workflow="BatchOptimize",
    )
    write_checkpoint(
        work_dir / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id="batch",
            workflow="BatchOptimize",
            plan_fingerprint="batch-fingerprint",
            step_states=[],
            items_state={"candidate_001": {"status": "completed"}},
            attempts=0,
        ),
    )
    submissions: list[str] = []
    monkeypatch.setattr(
        manager,
        "_start_submission_thread",
        lambda job_id, thread_name: submissions.append(job_id) or True,
    )

    try:
        continued = manager.continue_job(record.id)

        assert continued.status == JobStatus.QUEUED
        assert continued.result is not None
        assert continued.result["continued_from"] == "failed"
        assert submissions == [record.id]
    finally:
        manager.shutdown()


def test_missing_checkpoint_rejected(tmp_path: Path) -> None:
    """A generic failed workflow without a checkpoint keeps the old error."""
    manager = _make_manager(tmp_path)
    try:
        record = _seed_job(manager, tmp_path / "runs" / "missing", "missing-checkpoint")

        with pytest.raises(ValueError, match="该工作流不支持断点续算"):
            _ = manager.continue_job(record.id)
    finally:
        manager.shutdown()


def test_remote_checkpoint_three_states(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Remote continuation distinguishes missing, matching, and stale state."""
    checkpoint_path = "WORK/00_RUNTIME/checkpoint.json"

    # Given: the remote checkpoint is absent.
    missing_manager = _make_manager(tmp_path / "missing")
    try:
        record = _seed_job(
            missing_manager,
            tmp_path / "missing" / "runs" / "remote-missing",
            "remote-missing",
            result=_remote_result("expected"),
            remote_job_id="4242",
        )
        fetcher = _RemoteFetcher(None)
        missing_manager._remote_fetcher = fetcher

        # When/Then: a missing remote file is rejected using the existing
        # no-checkpoint guidance.
        with pytest.raises(ValueError, match="该工作流不支持断点续算"):
            _ = missing_manager.continue_job(record.id)
        assert fetcher.calls == [(record.id, checkpoint_path)]
    finally:
        missing_manager.shutdown()

    # Given: the remote checkpoint exists and its stored fingerprint matches
    # the fingerprint supplied in the persisted job metadata.
    matching_manager = _make_manager(tmp_path / "matching")
    try:
        record = _seed_job(
            matching_manager,
            tmp_path / "matching" / "runs" / "remote-matching",
            "remote-matching",
            result=_remote_result("expected"),
            remote_job_id="4243",
        )
        fetcher = _RemoteFetcher(_checkpoint_bytes(tmp_path / "matching", "expected"))
        matching_manager._remote_fetcher = fetcher
        submissions: list[str] = []
        monkeypatch.setattr(
            matching_manager,
            "_start_submission_thread",
            lambda job_id, thread_name: submissions.append(job_id) or True,
        )

        # When: the remote job is continued.
        continued = matching_manager.continue_job(record.id)

        # Then: it is requeued and submitted once.
        assert continued.status == JobStatus.QUEUED
        assert continued.result is not None
        assert continued.result["attempts"] == 2
        assert submissions == [record.id]
    finally:
        matching_manager.shutdown()

    # Given: the remote checkpoint exists but is stale relative to job.json's
    # stored plan fingerprint.
    stale_manager = _make_manager(tmp_path / "stale")
    try:
        record = _seed_job(
            stale_manager,
            tmp_path / "stale" / "runs" / "remote-stale",
            "remote-stale",
            result=_remote_result("expected"),
            remote_job_id="4244",
        )
        stale_manager._remote_fetcher = _RemoteFetcher(
            _checkpoint_bytes(tmp_path / "stale", "stale")
        )

        # When/Then: the manager refuses the stale checkpoint before requeue.
        with pytest.raises(ValueError, match="fingerprint mismatch"):
            _ = stale_manager.continue_job(record.id)
        unchanged = stale_manager.get(record.id)
        assert unchanged is not None
        assert unchanged.status == JobStatus.FAILED
    finally:
        stale_manager.shutdown()


# ── §19.6 PESsearch matrix ─────────────────────────────────────────────


def test_pessearch_pause_and_unpause_local_job(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    try:
        _seed_job(
            manager,
            tmp_path / "runs" / "pes-task",
            "pes-paused",
            status=JobStatus.RUNNING,
            workflow="PESsearch",
        )
        manager.runner._processes["pes-paused"] = process

        paused = manager.pause_job("pes-paused")
        assert paused.status == JobStatus.PAUSED
        time.sleep(0.05)
        assert process.poll() is None

        resumed = manager.unpause_job("pes-paused")
        assert resumed.status == JobStatus.RUNNING
        assert process.poll() is None
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), 15)
            process.wait(timeout=10)
        manager.shutdown()


def test_pessearch_continue_requeues_from_generic_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager = _make_manager(tmp_path)
    work_dir = tmp_path / "runs" / "pes-task"
    record = _seed_job(manager, work_dir, "pes-interrupted", workflow="PESsearch")
    write_checkpoint(
        work_dir / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id="pes",
            workflow="PESsearch",
            plan_fingerprint="pes-fingerprint",
            step_states=[
                {
                    "index": 0,
                    "kind": "scan",
                    "status": "completed",
                    "error": None,
                    "energy": -1.0,
                }
            ],
            items_state={},
            attempts=0,
        ),
    )
    submissions: list[str] = []
    monkeypatch.setattr(
        manager,
        "_start_submission_thread",
        lambda job_id, thread_name: submissions.append(job_id) or True,
    )

    try:
        continued = manager.continue_job(record.id)

        assert continued.status == JobStatus.QUEUED
        assert continued.result is not None
        assert continued.result["continued_from"] == "failed"
        assert submissions == [record.id]
    finally:
        manager.shutdown()
