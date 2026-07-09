"""Tests for the scheduler core (store, manager, runner) without external binaries."""

from __future__ import annotations

import time
from pathlib import Path

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.store import JobStore


def test_store_roundtrip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    from acp.scheduler.jobs import JobRecord

    record = JobRecord(
        id="job-1",
        spec=JobSpec(workflow="fake", name="t", input={"source": "CCO"}),
        status=JobStatus.QUEUED,
        work_dir=str(tmp_path / "job-1"),
    )
    store.create(record)
    fetched = store.get("job-1")
    assert fetched is not None
    assert fetched.spec.workflow == "fake"
    assert fetched.status == JobStatus.QUEUED

    fetched.status = JobStatus.RUNNING
    fetched.pid = 4242
    store.update(fetched)
    assert store.get("job-1").pid == 4242

    store.create(JobRecord(
        id="job-2",
        spec=JobSpec(workflow="fake"),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path / "job-2"),
    ))
    counts = store.counts()
    assert counts["running"] == 1
    assert counts["completed"] == 1
    assert len(store.list()) == 2


def test_store_reload_preserves_history(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    JobStore(db).create(JobRecord(
        id="persisted",
        spec=JobSpec(workflow="fake", name="p"),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path / "p"),
    ))
    reopened = JobStore(db)
    assert reopened.get("persisted") is not None
    assert reopened.counts()["completed"] == 1


def test_manager_runs_fake_job_to_completion(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=2)
    record = mgr.submit(JobSpec(workflow="fake", name="demo", input={"source": "CCO"}))
    assert record.status == JobStatus.QUEUED

    for _ in range(40):
        cur = mgr.get(record.id)
        assert cur is not None
        if cur.status.is_terminal:
            break
        time.sleep(0.5)

    assert cur.status == JobStatus.COMPLETED
    assert cur.exit_code == 0
    events = mgr.event_log(record.id).read_all()
    types = [e["type"] for e in events]
    assert "job.created" in types
    assert "stage.started" in types
    assert "job.completed" in types
    mgr.shutdown()


def test_manager_rejects_unknown_workflow(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        import pytest

        with pytest.raises(ValueError):
            mgr.submit(JobSpec(workflow="nonexistent"))
    finally:
        mgr.shutdown()


def test_manager_marks_interrupted_jobs_on_startup(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    from acp.scheduler.jobs import JobRecord

    store.create(JobRecord(
        id="was-running",
        spec=JobSpec(workflow="fake"),
        status=JobStatus.RUNNING,
        work_dir=str(tmp_path / "r"),
    ))
    mgr = JobManager(run_root=tmp_path, store=store, max_running=1)
    interrupted = mgr.get("was-running")
    assert interrupted.status == JobStatus.FAILED
    assert "interrupted" in (interrupted.error or "")
    mgr.shutdown()


def test_event_log_append_read(tmp_path: Path) -> None:
    log = JobEventLog(tmp_path / "events.jsonl")
    log.append("job.started", job_id="x")
    log.append("log", line="hello")
    records = log.read_all()
    assert len(records) == 2
    assert records[0]["type"] == "job.started"
    assert records[1]["line"] == "hello"
    assert "timestamp" in records[0]


def test_benchmark_command_is_top_level(tmp_path: Path) -> None:
    """P1#2: benchmark must invoke `acp.cli benchmark`, not `acp.cli run benchmark`."""
    from acp.scheduler.runner import JobRunner

    runner = JobRunner()
    spec = JobSpec(
        workflow="benchmark",
        input={"source": "mol.xyz"},
        method={"benchmark_level": "quick"},
    )
    cmd = runner._build_cmd(spec, tmp_path)
    assert cmd[3] == "benchmark", f"benchmark must not use 'run' subcommand: {cmd}"
    assert cmd[3:4] == ["benchmark"]
    assert "--benchmark-level" in cmd and "quick" in cmd


def test_find_state_file_prefers_shallowest(tmp_path: Path) -> None:
    """P1#1: nested workflows (NMR) write multiple state.json; shallowest wins."""
    import json

    from acp.scheduler.runner import find_workflow_state

    root = tmp_path / "work"
    (root / "jobA").mkdir(parents=True)
    (root / "jobA" / "state.json").write_text(json.dumps({"current_stage": "nmr"}))
    (root / "conformer" / "mol").mkdir(parents=True)
    (root / "conformer" / "mol" / "state.json").write_text(json.dumps({"current_stage": "crest"}))

    found = find_workflow_state(root)
    assert found is not None
    assert found.relative_to(root) == Path("jobA/state.json")

    single = tmp_path / "single"
    (single / "mol_CCO_1234").mkdir(parents=True)
    (single / "mol_CCO_1234" / "state.json").write_text("{}")
    found2 = find_workflow_state(single)
    assert found2 is not None
    assert found2.relative_to(single) == Path("mol_CCO_1234/state.json")

    assert find_workflow_state(tmp_path / "empty") is None


def test_queued_job_cancel_is_immediate(tmp_path: Path) -> None:
    """P1#3: cancelling a not-yet-started job must reach CANCELLED without waiting."""
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        blocker = mgr.submit(JobSpec(workflow="fake", name="blocker", input={"source": "X"}))
        second = mgr.submit(JobSpec(workflow="fake", name="second", input={"source": "Y"}))
        time.sleep(0.5)
        assert mgr.get(blocker.id).status == JobStatus.RUNNING
        assert mgr.get(second.id).status == JobStatus.QUEUED

        cancelled = mgr.cancel(second.id)
        assert cancelled.status == JobStatus.CANCELLED
        time.sleep(0.3)
        assert mgr.get(second.id).status == JobStatus.CANCELLED
    finally:
        mgr.shutdown()


def test_resolve_safe_rejects_traversal(tmp_path: Path) -> None:
    """Sprint 0: resolve_safe() must reject path-traversal attempts."""
    from acp.scheduler.files import resolve_safe

    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("password")

    (work / "ok.txt").write_text("data")
    ok = resolve_safe(work, "ok.txt")
    assert ok is not None and ok.name == "ok.txt"

    assert resolve_safe(work, "../secret.txt") is None
    assert resolve_safe(work, "../../etc/passwd") is None
    assert resolve_safe(work, "../../../secret.txt") is None

    assert resolve_safe(work, "missing.txt") is None


def test_output_dir_outside_run_root_is_clamped(tmp_path: Path) -> None:
    """P2#2: an output_dir escaping run_root must be ignored (clamped under run_root)."""
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        escape = tmp_path.parent / "evil_elsewhere"
        record = mgr.submit(
            JobSpec(workflow="fake", name="clamp", input={"source": "X"}, output_dir=str(escape))
        )
        work_dir = Path(record.work_dir).resolve()
        run_root = tmp_path.resolve()
        assert run_root in work_dir.parents or work_dir == run_root, (
            f"work_dir {work_dir} escaped run_root {run_root}"
        )
        assert "evil_elsewhere" not in record.work_dir
    finally:
        mgr.shutdown()
