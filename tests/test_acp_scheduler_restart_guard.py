"""Regression tests for the 2026-09-05 restart-recovery incident.

A second server process constructed a JobManager against the live run_root
and its startup recovery killed a healthy RUNNING PESsearch job (273
processes) with a bogus ``server_restart`` classification. These tests pin
the four defenses added afterwards:

1. run_root single-instance lock — a live foreign ACP owner blocks boot.
2. restart-recovery skips jobs whose ``run.lock`` owner is a live peer.
3. the poller does not fail untracked jobs whose task process is alive.
4. stage events fire once per transition, not once per poll tick.
"""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false, reportAny=false

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.runner import JobRunner
from acp.scheduler.store import JobStore
from acp.storage.layout import TaskStorage, runtime_file


def _spec() -> JobSpec:
    return JobSpec(
        workflow="PESsearch",
        name="incident-job",
        input={"source_type": "smiles", "source": "CCO"},
    )


def _record(job_id: str, work_dir: Path) -> JobRecord:
    return JobRecord(id=job_id, spec=_spec(), status=JobStatus.RUNNING, work_dir=str(work_dir))


def _acp_looking_process(seconds: int = 60) -> subprocess.Popen[bytes]:
    """Live process whose /proc cmdline contains an ACP marker string."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})", "acp-standin"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_run_lock(work_dir: Path, job_id: str, owner_pid: int, task_pid: int | None) -> None:
    runtime_file(work_dir, "run.lock").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "owner_pid": owner_pid,
                "task_pid": task_pid,
                "acquired_at": "2026-09-05T06:02:14.150144+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_manager_lock_rejects_live_foreign_owner(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    mgr = JobManager(run_root=run_root, poll_interval=30)
    try:
        mgr2 = JobManager(run_root=run_root, poll_interval=30)
        mgr2.shutdown()
    finally:
        mgr.shutdown()

    peer = _acp_looking_process()
    try:
        (run_root / ".manager.lock").write_text(
            json.dumps({"pid": peer.pid, "cmdline": "standin"}), encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="already owned"):
            JobManager(run_root=run_root, poll_interval=30)
    finally:
        peer.kill()
        peer.wait()


def test_recovery_skips_job_owned_by_live_peer(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")
    work_dir = run_root / "incident_PESsearch_test"
    TaskStorage(work_dir).ensure_layout()
    record = _record("20260905_incident_peer", work_dir)
    store.create(record)

    peer = _acp_looking_process()
    task = subprocess.Popen(["sleep", "60"], cwd=work_dir)
    _write_run_lock(work_dir, record.id, owner_pid=peer.pid, task_pid=task.pid)
    try:
        manager = JobManager(run_root=run_root, poll_interval=30)
        try:
            recovered = manager.store.get(record.id)
            assert recovered is not None
            assert recovered.status == JobStatus.RUNNING
            events_text = runtime_file(work_dir, "events.jsonl").read_text(encoding="utf-8")
            assert "manager.peer_conflict" in events_text
            assert "process.cleaned" not in events_text
            assert "job.failed" not in events_text
        finally:
            manager.shutdown()
    finally:
        peer.kill()
        peer.wait()
        task.kill()
        task.wait()


def test_recovery_fails_orphan_and_removes_run_lock(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")
    work_dir = run_root / "orphan_PESsearch_test"
    TaskStorage(work_dir).ensure_layout()
    record = _record("20260905_incident_orphan", work_dir)
    store.create(record)

    dead = subprocess.Popen(["true"])
    dead.wait()
    _write_run_lock(work_dir, record.id, owner_pid=dead.pid, task_pid=dead.pid)

    manager = JobManager(run_root=run_root, poll_interval=30)
    try:
        recovered = manager.store.get(record.id)
        assert recovered is not None
        assert recovered.status == JobStatus.FAILED
        assert "[RESTART_FAILED]" in (recovered.error or "")
        assert not runtime_file(work_dir, "run.lock").exists()
    finally:
        manager.shutdown()


def test_poll_keeps_untracked_job_with_live_task_process(tmp_path: Path) -> None:
    runner = JobRunner()
    work_dir = tmp_path / "untracked_job"
    TaskStorage(work_dir).ensure_layout()
    record = _record("20260905_untracked", work_dir)
    task = subprocess.Popen(["sleep", "60"], cwd=work_dir)
    _write_run_lock(work_dir, record.id, owner_pid=task.pid, task_pid=task.pid)
    events_path = runtime_file(work_dir, "events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch()
    runner._event_logs[record.id] = JobEventLog(events_path)
    try:
        is_terminal, exit_code = runner.poll(record)
        assert is_terminal is False
        assert exit_code is None
        events_text = runtime_file(work_dir, "events.jsonl").read_text(encoding="utf-8")
        assert "job.failed" not in events_text
    finally:
        task.kill()
        task.wait()

    is_terminal, exit_code = runner.poll(record)
    assert is_terminal is True
    assert exit_code == 1
    events_text = runtime_file(work_dir, "events.jsonl").read_text(encoding="utf-8")
    assert "job.failed" in events_text


def test_stage_events_emit_once_across_poll_ticks(tmp_path: Path) -> None:
    runner = JobRunner()
    work_dir = tmp_path / "dedup_job"
    work_dir.mkdir()
    record = _record("20260905_dedup", work_dir)
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "status": "running",
                "current_stage": "run_relaxed_scan",
                "overall_progress": 0.3,
                "stages": {
                    "prepare": {
                        "status": "completed",
                        "started_at": "2026-09-05T06:02:16.040166+00:00",
                        "completed_at": "2026-09-05T06:02:16.040770+00:00",
                    },
                    "run_relaxed_scan": {
                        "status": "running",
                        "started_at": "2026-09-05T06:02:16.043889+00:00",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    class _RunningProc:
        pid = 424242

        def poll(self) -> int | None:
            return None

    runner._processes[record.id] = _RunningProc()  # type: ignore[assignment]
    event_log = JobEventLog(runtime_file(work_dir, "events.jsonl"))
    runner._event_logs[record.id] = event_log

    for _ in range(3):
        is_terminal, _ = runner.poll(record)
        assert is_terminal is False

    events = [
        json.loads(line)
        for line in runtime_file(work_dir, "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    completed = [e for e in events if e.get("type") == "stage.completed"]
    started = [e for e in events if e.get("type") == "stage.started"]
    assert [e["stage"] for e in completed] == ["prepare"]
    assert [e["stage"] for e in started] == ["run_relaxed_scan"]
