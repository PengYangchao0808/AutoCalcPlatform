"""Job-queue operations wave (2026-08-17) — plan §7 items 1-6.

Covers: PAUSED state machine, local SIGSTOP/SIGCONT pause, remote
bstop/bresume mapping, continue/rerun/purge job operations, cascade
deletion, and the rich GET /jobs/{id}/detail projection.

No real binaries / SSH / network: local processes use ``sleep``, the
remote monitor uses the phase-test FakeSSHClient pattern, and manager
re-dispatch is stubbed via ``_execute_submission``.
"""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportPossiblyUnboundVariable=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false, reportAny=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportMissingTypeArgument=false

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from acp.api.v1_routes import _compute_recovery, get_job_detail
from acp.api.v1_schemas import JobDiskState
from acp.scheduler.artifacts import Artifact, ArtifactRegistry
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.local_cleanup import LocalCleanup, RetentionPolicy
from acp.scheduler.manager import JobManager
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.monitor import _LSF_STATE_MAP, STATUS_PAUSED, RemoteJobMonitor
from acp.scheduler.remote.runner import RemoteJobRunner
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.stage_tasks import StageTask, StageTaskStore
from acp.scheduler.store import JobStore

# ====================================================================== #
# Helpers
# ====================================================================== #

PAUSED_RESTART_MARKER = "[RESTART_FAILED] paused job frozen at restart"
RESTART_HINT = "可尝试续算 (try continue)"
STAGE_WORKFLOWS = ("Confsearch", "PESsearch", "Lowconfirm", "Highconfirm")


def _make_manager(tmp_path: Path, **kw) -> JobManager:
    kw.setdefault("poll_interval", 30)
    return JobManager(run_root=tmp_path / "runs", **kw)


def _seed_job(
    store: JobStore,
    work_dir: Path,
    job_id: str,
    *,
    status: JobStatus = JobStatus.RUNNING,
    workflow: str = "fake",
    name: str | None = None,
    method: dict | None = None,
    project_id: str | None = None,
    output_dir: str | None = None,
    error: str | None = None,
    exit_code: int | None = None,
    completed_at: str | None = None,
    result: dict | None = None,
    remote_job_id: str | None = None,
    make_dir: bool = True,
) -> JobRecord:
    if make_dir:
        work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=name if name is not None else job_id,
            method=method or {},
            project_id=project_id,
            output_dir=output_dir,
        ),
        status=status,
        work_dir=str(work_dir),
        project_id=project_id,
        error=error,
        exit_code=exit_code,
        completed_at=completed_at,
        result=result,
        remote_job_id=remote_job_id,
    )
    store.create(record)
    return record


def _spawn_local_worker() -> subprocess.Popen[bytes]:
    """A real, long-running subprocess in its own process group."""
    return subprocess.Popen(["sleep", "60"], start_new_session=True)  # noqa: S604


def _proc_state(pid: int) -> str:
    """Kernel state char from /proc/<pid>/stat (T = stopped, S = sleeping)."""
    data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return data.rsplit(")", 1)[1].split()[0]


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)


class _StubRequest:
    """Minimal Request stub: only ``app.state`` is consulted by v1_routes."""

    def __init__(self, manager: JobManager) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                job_manager=manager,
                db_path=str(manager.store.db_path),
                run_root=str(manager.run_root),
            )
        )


class _OnceEvent(threading.Event):
    """Event whose first ``wait()`` returns False (run one loop iteration)."""

    def __init__(self) -> None:
        super().__init__()
        self._calls = 0

    def wait(self, timeout: float | None = None) -> bool:
        self._calls += 1
        if self._calls == 1:
            return False
        return True


class _FakeSSHClient:
    """Exec-command-only SSH client (monitor bstop/bresume/bjobs tests)."""

    def __init__(self) -> None:
        self._transport = MagicMock()
        self._transport.is_active.return_value = True
        self.executed: list[str] = []
        self.handler = None  # cmd -> (code, stdout, stderr)

    def set_missing_host_key_policy(self, policy: object) -> None:
        pass

    def connect(self, **kwargs: object) -> None:
        pass

    def get_transport(self) -> MagicMock:
        return self._transport

    def exec_command(self, command: str, timeout: float | None = None):
        self.executed.append(command)
        result = self.handler(command) if self.handler is not None else (0, "", "")
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.read.return_value = result[1].encode("utf-8")
        stderr.read.return_value = result[2].encode("utf-8")
        stdout.channel = MagicMock()
        stdout.channel.recv_exit_status.return_value = result[0]
        return stdin, stdout, stderr

    def close(self) -> None:
        pass


def _make_node(name: str = "compute-01") -> RemoteNode:
    return RemoteNode(
        name=name,
        host="10.0.0.1",
        username="testuser",
        remote_work_dir="/scratch/test/acp_jobs",
        remote_code_dir="/home/test/acp_code",
        max_concurrent_jobs=5,
        host_key_policy="auto_add",
    )


# ====================================================================== #
# (a) State machine: PAUSED flags, counts, list filters, poll exclusion,
#     startup triage (local/remote PAUSED, CANCELLING) + Q12 disk probe.
# ====================================================================== #


def test_paused_status_is_active_and_not_terminal() -> None:
    assert JobStatus.PAUSED.is_active is True
    assert JobStatus.PAUSED.is_terminal is False
    assert JobStatus.PAUSED.value == "paused"


def test_store_counts_include_paused(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed_job(store, tmp_path / "p1", "paused-job", status=JobStatus.PAUSED)
    _seed_job(store, tmp_path / "p2", "running-job", status=JobStatus.RUNNING)

    counts = store.counts()
    assert counts["paused"] == 1
    assert counts["running"] == 1
    # Every JobStatus key is present so consumers can index blindly.
    assert set(counts) == {s.value for s in JobStatus}


def test_store_list_filters(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed_job(
        store,
        tmp_path / "a",
        "old-done-p1",
        status=JobStatus.COMPLETED,
        project_id="p1",
        completed_at="2020-01-01T00:00:00+00:00",
    )
    _seed_job(
        store,
        tmp_path / "b",
        "failed-p1",
        status=JobStatus.FAILED,
        project_id="p1",
        completed_at=None,
    )
    _seed_job(
        store,
        tmp_path / "c",
        "future-done-p2",
        status=JobStatus.COMPLETED,
        project_id="p2",
        completed_at="2099-01-01T00:00:00+00:00",
    )

    ids = lambda records: {r.id for r in records}  # noqa: E731

    assert ids(store.list(status="completed")) == {"old-done-p1", "future-done-p2"}
    assert ids(store.list(project_id="p1")) == {"old-done-p1", "failed-p1"}
    # completed_before keeps only non-empty completed_at strictly before cutoff.
    assert ids(store.list(completed_before="2021-01-01T00:00:00+00:00")) == {"old-done-p1"}
    assert ids(store.list(status="failed", project_id="p2")) == set()
    assert ids(store.list(status="paused")) == set()


def test_poll_loop_excludes_paused_jobs(tmp_path: Path) -> None:
    """One real poll iteration: RUNNING is polled, PAUSED/WAITING_REVIEW are not.

    A PAUSED job whose process died while frozen is therefore never
    finalized by the poller — only unpause (back to RUNNING) re-exposes it.
    """
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "job-running", status=JobStatus.RUNNING)
        _seed_job(mgr.store, tmp_path / "runs/j2", "job-paused", status=JobStatus.PAUSED)
        _seed_job(mgr.store, tmp_path / "runs/j3", "job-review", status=JobStatus.WAITING_REVIEW)

        seen: list[str] = []
        mgr._poll_job = lambda job_id: seen.append(job_id)  # type: ignore[method-assign]
        mgr._poll_stop = _OnceEvent()
        mgr._poll_loop()

        assert seen == ["job-running"]
    finally:
        mgr.shutdown()


def test_startup_marks_paused_local_job_failed_with_marker(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed_job(store, tmp_path / "runs/j1", "frozen-local", status=JobStatus.PAUSED)

    mgr = _make_manager(tmp_path, store=store)
    try:
        rec = mgr.get("frozen-local")
        assert rec is not None
        assert rec.status == JobStatus.FAILED
        assert PAUSED_RESTART_MARKER in (rec.error or "")
        assert rec.completed_at is not None
    finally:
        mgr.shutdown()


def test_startup_keeps_paused_remote_job_paused_when_recovered(tmp_path: Path) -> None:
    """Remote PAUSED jobs are re-adopted via recover_job_state and stay PAUSED."""
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "frozen-remote",
            status=JobStatus.PAUSED,
            remote_job_id="4242",
        )
        recovered: list[str] = []
        mgr.remote_runner = SimpleNamespace(
            recover_job_state=lambda record: recovered.append(record.id) or True
        )
        mgr._requeue_active_on_startup()

        assert recovered == ["frozen-remote"]
        rec = mgr.get("frozen-remote")
        assert rec is not None
        assert rec.status == JobStatus.PAUSED
        assert rec.error is None
    finally:
        mgr.shutdown()


def test_startup_paused_remote_without_recovery_falls_back_to_failed(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "frozen-remote-dead",
            status=JobStatus.PAUSED,
            remote_job_id="9999",
        )
        mgr.remote_runner = SimpleNamespace(recover_job_state=lambda record: False)
        mgr._requeue_active_on_startup()

        rec = mgr.get("frozen-remote-dead")
        assert rec is not None
        assert rec.status == JobStatus.FAILED
        assert PAUSED_RESTART_MARKER in (rec.error or "")
    finally:
        mgr.shutdown()


def test_startup_marks_interrupted_cancelling_job_cancelled(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed_job(store, tmp_path / "runs/j1", "mid-cancel", status=JobStatus.CANCELLING)

    mgr = _make_manager(tmp_path, store=store)
    try:
        rec = mgr.get("mid-cancel")
        assert rec is not None
        assert rec.status == JobStatus.CANCELLED
        assert "[RESTART_FAILED]" in (rec.error or "")
    finally:
        mgr.shutdown()


def test_startup_disk_probe_recovers_completed_race(tmp_path: Path) -> None:
    """Q12: RUNNING + complete state.json + .exit_code 0 → COMPLETED + result."""
    store = JobStore(tmp_path / "jobs.db")
    work = tmp_path / "runs/j1"
    state = {
        "current_stage": "freq",
        "stages": {
            "opt": {"status": "completed"},
            "freq": {"status": "completed"},
            "skipped_extra": {"status": "skipped"},
        },
    }
    _seed_job(store, work, "raced-done", status=JobStatus.RUNNING)
    (work / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (work / ".exit_code").write_text("0\n", encoding="utf-8")

    mgr = _make_manager(tmp_path, store=store)
    try:
        rec = mgr.get("raced-done")
        assert rec is not None
        assert rec.status == JobStatus.COMPLETED
        assert rec.exit_code == 0
        assert rec.completed_at is not None
        assert rec.result is not None
        assert rec.result["state"]["current_stage"] == "freq"
        assert rec.progress == 1.0
    finally:
        mgr.shutdown()


def test_startup_disk_probe_without_exit_code_fails_with_continue_hint(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    work = tmp_path / "runs/j1"
    state = {"current_stage": "opt", "stages": {"opt": {"status": "completed"}}}
    _seed_job(store, work, "no-exit-code", status=JobStatus.RUNNING)
    (work / "state.json").write_text(json.dumps(state), encoding="utf-8")
    # No .exit_code marker — the wrapper sentinel never ran.

    mgr = _make_manager(tmp_path, store=store)
    try:
        rec = mgr.get("no-exit-code")
        assert rec is not None
        assert rec.status == JobStatus.FAILED
        assert "[RESTART_FAILED]" in (rec.error or "")
        assert RESTART_HINT in (rec.error or "")
    finally:
        mgr.shutdown()


def test_startup_disk_probe_nonzero_exit_code_stays_failed(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    work = tmp_path / "runs/j1"
    state = {"stages": {"opt": {"status": "completed"}}}
    _seed_job(store, work, "failed-exit", status=JobStatus.RUNNING)
    (work / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (work / ".exit_code").write_text("1\n", encoding="utf-8")

    mgr = _make_manager(tmp_path, store=store)
    try:
        rec = mgr.get("failed-exit")
        assert rec is not None
        assert rec.status == JobStatus.FAILED
    finally:
        mgr.shutdown()


def test_disk_shows_completed_rejects_incomplete_or_partial_state(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        work = tmp_path / "runs/probe"
        work.mkdir(parents=True)
        (work / ".exit_code").write_text("0\n", encoding="utf-8")

        # No state.json at all.
        assert mgr._disk_shows_completed(work) is False

        # A stage still running → not complete.
        (work / "state.json").write_text(
            json.dumps({"stages": {"opt": {"status": "completed"}, "freq": {"status": "running"}}}),
            encoding="utf-8",
        )
        assert mgr._disk_shows_completed(work) is False

        # All stages done + sentinel 0 → complete.
        (work / "state.json").write_text(
            json.dumps({"stages": {"opt": {"status": "completed"}, "freq": {"status": "skipped"}}}),
            encoding="utf-8",
        )
        assert mgr._disk_shows_completed(work) is True
    finally:
        mgr.shutdown()


# ====================================================================== #
# (b) Local pause / unpause (SIGSTOP / SIGCONT) + cancel-of-paused defense.
# ====================================================================== #


def test_pause_and_unpause_local_job(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    proc = _spawn_local_worker()
    pgid = os.getpgid(proc.pid)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "sleepy", status=JobStatus.RUNNING)
        mgr.runner._processes["sleepy"] = proc

        paused = mgr.pause_job("sleepy")
        assert paused.status == JobStatus.PAUSED
        assert mgr.get("sleepy").status == JobStatus.PAUSED  # type: ignore[union-attr]
        # Process group alive but frozen (kernel state T).
        os.killpg(pgid, 0)  # raises if the group vanished
        _wait_for(lambda: _proc_state(proc.pid) == "T", timeout=5.0)
        assert proc.poll() is None
        # _processes entry retained so the poller re-adopts after unpause.
        assert "sleepy" in mgr.runner._processes
        events = mgr.event_log("sleepy")
        assert events is not None
        assert events.read_all()[-1]["type"] == "job.paused"
        assert events.read_all()[-1]["mode"] == "sigstop"

        resumed = mgr.unpause_job("sleepy")
        assert resumed.status == JobStatus.RUNNING
        assert _proc_state(proc.pid) != "T"
        assert proc.poll() is None
        assert mgr.get("sleepy").status == JobStatus.RUNNING  # type: ignore[union-attr]
        events = mgr.event_log("sleepy")
        assert events is not None
        assert events.read_all()[-1]["type"] == "job.resumed"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        mgr.shutdown()


def test_pause_job_requires_running_status(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "queued-job", status=JobStatus.QUEUED)
        with pytest.raises(ValueError, match="requires RUNNING"):
            mgr.pause_job("queued-job")
        with pytest.raises(KeyError):
            mgr.pause_job("missing-job")
    finally:
        mgr.shutdown()


def test_pause_job_without_live_process_raises(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "ghost", status=JobStatus.RUNNING)
        # No process registered in runner._processes.
        with pytest.raises(ValueError, match="no live local process"):
            mgr.pause_job("ghost")
        # Status untouched — the poller still owns finalization.
        assert mgr.get("ghost").status == JobStatus.RUNNING  # type: ignore[union-attr]
    finally:
        mgr.shutdown()


def test_unpause_requires_paused_status(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "running-job", status=JobStatus.RUNNING)
        with pytest.raises(ValueError, match="requires PAUSED"):
            mgr.unpause_job("running-job")
    finally:
        mgr.shutdown()


def test_unpause_without_tracked_process_raises_runtimeerror(tmp_path: Path) -> None:
    """A PAUSED record that survived a restart has nothing to SIGCONT."""
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "stale-paused", status=JobStatus.PAUSED)
        with pytest.raises(RuntimeError, match="no longer tracked"):
            mgr.unpause_job("stale-paused")
        # Status stays PAUSED — never flips to RUNNING with nothing to poll.
        assert mgr.get("stale-paused").status == JobStatus.PAUSED  # type: ignore[union-attr]
    finally:
        mgr.shutdown()


def test_cancel_paused_job_terminates_group_without_orphan(tmp_path: Path) -> None:
    """Cancelling a SIGSTOP-frozen job must SIGCONT first, then SIGTERM."""
    mgr = _make_manager(tmp_path)
    proc = _spawn_local_worker()
    pgid = os.getpgid(proc.pid)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "frozen", status=JobStatus.RUNNING)
        mgr.runner._processes["frozen"] = proc
        mgr._cancel_events["frozen"] = threading.Event()
        assert mgr.pause_job("frozen").status == JobStatus.PAUSED
        _wait_for(lambda: _proc_state(proc.pid) == "T", timeout=5.0)

        cancelled = mgr.cancel("frozen")
        assert cancelled is not None
        assert cancelled.status == JobStatus.CANCELLING

        # Drive the poller's finalization step synchronously.
        mgr._poll_job("frozen")
        final = mgr.get("frozen")
        assert final is not None
        assert final.status == JobStatus.CANCELLED
        assert final.completed_at is not None

        # Reaped, killed by SIGTERM (proves the SIGCONT defense revived the
        # frozen group — a still-stopped process would only die via SIGKILL),
        # and no orphan remains in the process group.
        assert proc.poll() is not None
        assert proc.returncode == -signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
        assert "frozen" not in mgr.runner._processes
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        mgr.shutdown()


def test_runner_pause_local_edge_cases() -> None:
    from acp.scheduler.runner import JobRunner

    runner = JobRunner()
    assert runner.pause_local("nobody") is False
    assert runner.resume_local("nobody") is False

    dead = subprocess.Popen(["true"])
    dead.wait(timeout=10)
    runner._processes["dead"] = dead
    # pause: already-exited process → False (let the poller finalize);
    # resume: exited-while-paused → True (job still tracked).
    assert runner.pause_local("dead") is False
    assert runner.resume_local("dead") is True


def test_pause_remote_job_invokes_bstop(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "remote-running",
            status=JobStatus.RUNNING,
            remote_job_id="4242",
        )
        calls: list[object] = []
        mgr.remote_runner = SimpleNamespace()
        mgr._remote_monitor = SimpleNamespace(
            bstop_job=lambda lsf_id: calls.append(("bstop", lsf_id)) or True
        )

        paused = mgr.pause_job("remote-running")
        assert paused.status == JobStatus.PAUSED
        assert calls == [("bstop", "4242")]
        events = mgr.event_log("remote-running")
        assert events is not None
        assert events.read_all()[-1]["mode"] == "bstop"
    finally:
        mgr.shutdown()


def test_unpause_remote_job_invokes_bresume(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "remote-paused",
            status=JobStatus.PAUSED,
            remote_job_id="4242",
        )
        calls: list[object] = []
        mgr.remote_runner = SimpleNamespace()
        mgr._remote_monitor = SimpleNamespace(
            bresume_job=lambda lsf_id: calls.append(("bresume", lsf_id)) or True
        )

        resumed = mgr.unpause_job("remote-paused")
        assert resumed.status == JobStatus.RUNNING
        assert calls == [("bresume", "4242")]
    finally:
        mgr.shutdown()


def test_remote_pause_failure_leaves_status_untouched(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "remote-bad",
            status=JobStatus.RUNNING,
            remote_job_id="777",
        )
        mgr.remote_runner = SimpleNamespace()
        mgr._remote_monitor = SimpleNamespace(bstop_job=lambda lsf_id: False)
        with pytest.raises(RuntimeError, match="remote pause failed"):
            mgr.pause_job("remote-bad")
        assert mgr.get("remote-bad").status == JobStatus.RUNNING  # type: ignore[union-attr]
    finally:
        mgr.shutdown()


# ====================================================================== #
# (c) continue_job — checkpoint re-entry matrix.
# ====================================================================== #


def _wait_submission(calls: list[str], job_id: str) -> None:
    _wait_for(lambda: job_id in calls, timeout=5.0)
    assert job_id in calls


def test_continue_mechanism_failed_job_requeues(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "mech-failed",
            workflow="mechanism",
            status=JobStatus.FAILED,
            error="S2 exploded",
            exit_code=1,
            completed_at="2026-08-17T00:00:00+00:00",
        )
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        rec = mgr.continue_job("mech-failed")
        assert rec.status == JobStatus.QUEUED
        assert rec.error is None
        assert rec.exit_code is None
        assert rec.completed_at is None
        assert rec.result is not None
        assert rec.result["attempts"] == 2  # default 1 + 1
        assert rec.result["continued_from"] == "failed"
        _wait_submission(calls, "mech-failed")

        events = mgr.event_log("mech-failed")
        assert events is not None
        continued = [e for e in events.read_all() if e["type"] == "job.continued"]
        assert continued and continued[-1]["workflow"] == "mechanism"
    finally:
        mgr.shutdown()


def test_continue_cancelled_mechanism_increments_attempts(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "mech-cancelled",
            workflow="mechanism",
            status=JobStatus.CANCELLED,
            result={"attempts": 3},
        )
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        rec = mgr.continue_job("mech-cancelled")
        assert rec.status == JobStatus.QUEUED
        assert rec.result is not None
        assert rec.result["attempts"] == 4
        assert rec.result["continued_from"] == "cancelled"
        _wait_submission(calls, "mech-cancelled")
    finally:
        mgr.shutdown()


def test_continue_xtbmd_persists_resume_flag(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "xtbmd-failed",
            workflow="xtbmd_censo_energy",
            status=JobStatus.FAILED,
            method={"md_temp": 400},
            error="isostat died",
        )
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        mgr.continue_job("xtbmd-failed")
        _wait_submission(calls, "xtbmd-failed")

        fresh = mgr.store.get("xtbmd-failed")
        assert fresh is not None
        assert fresh.spec.method["resume"] is True
        # Existing method keys are preserved alongside the injected flag.
        assert fresh.spec.method["md_temp"] == 400
        assert fresh.status == JobStatus.QUEUED
    finally:
        mgr.shutdown()


def test_continue_simple_workflow_rejected_with_guidance(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "simple-failed",
            workflow="singlepoint",
            status=JobStatus.FAILED,
        )
        with pytest.raises(ValueError, match="不支持断点续算"):
            mgr.continue_job("simple-failed")
        # Same for ensemble/energy/nmr-style workflows.
        _seed_job(
            mgr.store,
            tmp_path / "runs/j2",
            "energy-failed",
            workflow="energy",
            status=JobStatus.FAILED,
        )
        with pytest.raises(ValueError):
            mgr.continue_job("energy-failed")
    finally:
        mgr.shutdown()


@pytest.mark.parametrize("workflow", STAGE_WORKFLOWS)
def test_continue_stage_workflow_rejected_with_rerun_guidance(
    tmp_path: Path, workflow: str
) -> None:
    mgr = _make_manager(tmp_path)
    try:
        source = _seed_job(
            mgr.store,
            tmp_path / "runs" / f"{workflow.lower()}-failed",
            f"{workflow.lower()}-failed",
            workflow=workflow,
            status=JobStatus.FAILED,
            project_id=mgr.default_project_id,
        )

        with pytest.raises(ValueError, match="不支持断点续算.*rerun"):
            mgr.continue_job(source.id)

        unchanged = mgr.get(source.id)
        assert unchanged is not None
        assert unchanged.status == JobStatus.FAILED
        assert unchanged.spec.workflow == workflow
    finally:
        mgr.shutdown()


def test_continue_requires_terminal_status(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/j1", "still-running", status=JobStatus.RUNNING)
        with pytest.raises(ValueError, match="FAILED or CANCELLED"):
            mgr.continue_job("still-running")
        with pytest.raises(KeyError):
            mgr.continue_job("missing")
    finally:
        mgr.shutdown()


def test_continue_rejects_live_zombie_process(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    proc = _spawn_local_worker()
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "zombie-guard",
            workflow="mechanism",
            status=JobStatus.FAILED,
        )
        mgr.runner._processes["zombie-guard"] = proc

        with pytest.raises(ValueError, match="live process"):
            mgr.continue_job("zombie-guard")
        rec = mgr.get("zombie-guard")
        assert rec is not None
        assert rec.status == JobStatus.FAILED  # untouched
    finally:
        proc.kill()
        proc.wait(timeout=10)
        mgr.shutdown()


# ====================================================================== #
# (d) rerun_job — in-place full rerun.
# ====================================================================== #


def test_rerun_preserves_job_identity_and_archives_prior_attempt(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        source = _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "source",
            status=JobStatus.FAILED,
            name="foo_copy",
            output_dir=str(tmp_path / "runs/explicit_out"),
            project_id=mgr.default_project_id,
        )
        work_dir = Path(source.work_dir)
        (work_dir / "WORK" / "02_SEARCH").mkdir(parents=True)
        (work_dir / "WORK" / "02_SEARCH" / "failed.out").write_text("failed")
        (work_dir / "RESULT").mkdir(parents=True)
        (work_dir / "RESULT" / "result.json").write_text("{}")
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        rerun = mgr.rerun_job("source")
        assert rerun is not None
        assert rerun.id == source.id == "source"
        assert rerun.work_dir == source.work_dir
        assert rerun.spec.name == source.spec.name
        assert rerun.spec.output_dir == source.spec.output_dir
        assert rerun.group_id == source.group_id
        assert rerun.status == JobStatus.QUEUED
        assert rerun.result is not None
        assert rerun.result["attempts"] == 2
        assert Path(rerun.work_dir) == work_dir
        assert (work_dir / "_attempts" / "attempt_001" / "WORK").is_dir()
        assert (work_dir / "_attempts" / "attempt_001" / "RESULT").is_dir()
        assert len(mgr.store.list(limit=20)) == 1
        _wait_submission(calls, source.id)
    finally:
        mgr.shutdown()


def test_rerun_cannot_change_project(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        project = mgr.projects.create_project("RerunTarget")
        source = _seed_job(
            mgr.store,
            tmp_path / "runs/j1",
            "plain",
            status=JobStatus.COMPLETED,
            name="foo",
            project_id=mgr.default_project_id,
        )
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="不能切换项目"):
            mgr.rerun_job("plain", project_id="no-such-project")
        assert mgr.get(source.id).status == JobStatus.COMPLETED  # type: ignore[union-attr]
        assert calls == []
        assert project["project_id"] != source.project_id
    finally:
        mgr.shutdown()


def test_rerun_unknown_job_returns_none(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        assert mgr.rerun_job("missing-job") is None
    finally:
        mgr.shutdown()


@pytest.mark.parametrize("workflow", STAGE_WORKFLOWS)
def test_rerun_stage_workflow_reuses_original_task(tmp_path: Path, workflow: str) -> None:
    mgr = _make_manager(tmp_path)
    try:
        source = _seed_job(
            mgr.store,
            tmp_path / "runs" / f"{workflow.lower()}-source",
            f"{workflow.lower()}-task",
            workflow=workflow,
            status=JobStatus.FAILED,
            project_id=mgr.default_project_id,
        )
        calls: list[str] = []
        mgr._execute_submission = lambda job_id: calls.append(job_id)  # type: ignore[method-assign]

        rerun = mgr.rerun_job(source.id)

        assert rerun is not None
        assert rerun.id == source.id
        assert rerun.work_dir == source.work_dir
        assert rerun.group_id == source.group_id
        assert rerun.spec.workflow == workflow
        assert rerun.spec.name == source.spec.name
        _wait_submission(calls, source.id)
    finally:
        mgr.shutdown()


@pytest.mark.parametrize("workflow", STAGE_WORKFLOWS)
def test_pause_unpause_stage_workflow_lifecycle(tmp_path: Path, workflow: str) -> None:
    mgr = _make_manager(tmp_path)
    proc = _spawn_local_worker()
    job_id = f"{workflow.lower()}-running"
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs" / job_id,
            job_id,
            workflow=workflow,
            status=JobStatus.RUNNING,
        )
        mgr.runner._processes[job_id] = proc

        paused = mgr.pause_job(job_id)
        assert paused.status == JobStatus.PAUSED

        resumed = mgr.unpause_job(job_id)
        assert resumed.status == JobStatus.RUNNING
        assert proc.poll() is None

        events = mgr.event_log(job_id)
        assert events is not None
        assert [event["type"] for event in events.read_all()][-2:] == [
            "job.paused",
            "job.resumed",
        ]
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        mgr.shutdown()


# ====================================================================== #
# (e) purge — cascade deletion, purge_jobs report, delete_job/local_cleanup
#     cascade routing.
# ====================================================================== #


def _seed_cascade_children(
    db: Path,
    job_id: str,
    *,
    stage_id: str,
    artifact_id: str,
    study_id: str,
    decision_id: str,
) -> None:
    stage_store = StageTaskStore(db)
    artifacts = ArtifactRegistry(db)
    stage_store.create(
        StageTask(task_id=stage_id, job_id=job_id, stage_name="S1", state="completed")
    )
    artifacts.register(
        Artifact(
            artifact_id=artifact_id,
            task_id=None,
            job_id=job_id,
            artifact_type="xyz",
            file_path=f"{job_id}/final.xyz",
            checksum=None,
            size_bytes=128,
        )
    )
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mechanism_studies "
            "(id, job_id, study_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                study_id,
                job_id,
                json.dumps({"study_id": study_id}),
                "completed",
                "2026-08-17T00:00:00+00:00",
                "2026-08-17T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO decision_points "
            "(id, study_id, status, payload, resolution, created_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                study_id,
                "resolved",
                "{}",
                "ok",
                "2026-08-17T00:00:00+00:00",
                "2026-08-17T00:01:00+00:00",
            ),
        )
        conn.commit()


def _table_count(db: Path, table: str, where: str, param: tuple) -> int:
    with sqlite3.connect(str(db)) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", param).fetchone()[0])


def test_store_purge_cascade_removes_children_keeps_unrelated(tmp_path: Path) -> None:
    db = tmp_path / "cascade.db"
    store = JobStore(db)
    _seed_job(store, tmp_path / "victim", "victim", status=JobStatus.COMPLETED)
    _seed_job(store, tmp_path / "other", "bystander", status=JobStatus.COMPLETED)
    _seed_cascade_children(
        db,
        "victim",
        stage_id="st-victim",
        artifact_id="art-victim",
        study_id="study-victim",
        decision_id="dec-victim",
    )
    _seed_cascade_children(
        db,
        "bystander",
        stage_id="st-other",
        artifact_id="art-other",
        study_id="study-other",
        decision_id="dec-other",
    )

    store.purge_cascade("victim")

    assert store.get("victim") is None
    assert store.get("bystander") is not None
    assert _table_count(db, "stage_tasks", "job_id=?", ("victim",)) == 0
    assert _table_count(db, "stage_tasks", "job_id=?", ("bystander",)) == 1
    assert _table_count(db, "artifacts", "job_id=?", ("victim",)) == 0
    assert _table_count(db, "artifacts", "job_id=?", ("bystander",)) == 1
    # decision_points link via mechanism_studies (no job_id column).
    assert _table_count(db, "decision_points", "id=?", ("dec-victim",)) == 0
    assert _table_count(db, "decision_points", "id=?", ("dec-other",)) == 1
    assert _table_count(db, "mechanism_studies", "job_id=?", ("victim",)) == 0
    assert _table_count(db, "mechanism_studies", "job_id=?", ("bystander",)) == 1


def test_purge_jobs_report_actions(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        db = mgr.store.db_path
        _seed_job(mgr.store, tmp_path / "runs/done", "done-job", status=JobStatus.COMPLETED)
        _seed_cascade_children(
            db,
            "done-job",
            stage_id="st-1",
            artifact_id="art-1",
            study_id="study-1",
            decision_id="dec-1",
        )
        _seed_job(mgr.store, tmp_path / "runs/active", "active-job", status=JobStatus.RUNNING)

        report = mgr.purge_jobs(["done-job", "active-job", "no-such-job"])
        by_id = {entry["job_id"]: entry for entry in report}

        assert by_id["done-job"] == {
            "job_id": "done-job",
            "ok": True,
            "action": "purged",
            "error": None,
        }
        assert by_id["active-job"]["ok"] is False
        assert by_id["active-job"]["action"] == "skipped_active"
        assert "force_cancel" in (by_id["active-job"]["error"] or "")
        assert by_id["no-such-job"]["ok"] is False
        assert by_id["no-such-job"]["action"] == "error"
        assert by_id["no-such-job"]["error"] == "job not found"

        # Cascade actually ran for the purged job.
        assert mgr.get("done-job") is None
        assert _table_count(db, "stage_tasks", "job_id=?", ("done-job",)) == 0
        assert _table_count(db, "decision_points", "id=?", ("dec-1",)) == 0
        # Active job untouched.
        assert mgr.get("active-job") is not None
    finally:
        mgr.shutdown()


def test_purge_jobs_force_cancel_path(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/force", "force-job", status=JobStatus.RUNNING)
        cancel_calls: list[str] = []

        original_cancel = mgr.cancel

        def fake_cancel(job_id: str) -> JobRecord | None:
            cancel_calls.append(job_id)
            rec = original_cancel(job_id)
            # Simulate the poller finalizing the CANCELLING record.
            cur = mgr.store.get(job_id)
            assert cur is not None
            cur.status = JobStatus.CANCELLED
            cur.completed_at = "2026-08-17T00:00:00+00:00"
            mgr.store.update(cur)
            return rec

        monkeypatch_holder = patch.object(mgr, "cancel", fake_cancel)
        await_patched = patch.object(mgr, "_await_terminal", lambda job_id, timeout=30.0: True)
        with monkeypatch_holder, await_patched:
            report = mgr.purge_jobs(["force-job"], force_cancel=True)

        assert cancel_calls == ["force-job"]
        assert report == [{"job_id": "force-job", "ok": True, "action": "purged", "error": None}]
        assert mgr.get("force-job") is None
    finally:
        mgr.shutdown()


def test_purge_jobs_filter_by_status(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/c1", "completed-1", status=JobStatus.COMPLETED)
        _seed_job(mgr.store, tmp_path / "runs/f1", "failed-1", status=JobStatus.FAILED)

        report = mgr.purge_jobs(status="failed")
        assert {entry["job_id"] for entry in report} == {"failed-1"}
        assert mgr.get("failed-1") is None
        assert mgr.get("completed-1") is not None
    finally:
        mgr.shutdown()


def test_delete_job_cascades_to_stage_tasks(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        db = mgr.store.db_path
        _seed_job(mgr.store, tmp_path / "runs/gone", "deleteme", status=JobStatus.COMPLETED)
        StageTaskStore(db).create(
            StageTask(task_id="st-del", job_id="deleteme", stage_name="opt", state="completed")
        )

        assert mgr.delete_job("deleteme") is True
        assert mgr.get("deleteme") is None
        assert _table_count(db, "stage_tasks", "job_id=?", ("deleteme",)) == 0
        # Active jobs still refuse deletion.
        _seed_job(mgr.store, tmp_path / "runs/live", "live-job", status=JobStatus.RUNNING)
        with pytest.raises(ValueError, match="active"):
            mgr.delete_job("live-job")
    finally:
        mgr.shutdown()


def test_local_cleanup_db_records_cascade(tmp_path: Path) -> None:
    run_root = tmp_path / "runs2"
    run_root.mkdir()
    db = run_root / "acp_jobs.db"
    store = JobStore(db)
    _seed_job(
        store,
        tmp_path / "old-job",
        "old-job",
        status=JobStatus.COMPLETED,
        completed_at="2020-01-01T00:00:00+00:00",
    )
    _seed_job(
        store,
        tmp_path / "recent-job",
        "recent-job",
        status=JobStatus.COMPLETED,
        completed_at="2099-01-01T00:00:00+00:00",
    )
    _seed_cascade_children(
        db,
        "old-job",
        stage_id="st-old",
        artifact_id="art-old",
        study_id="study-old",
        decision_id="dec-old",
    )

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(db_record_days=365))
    report = cleanup.cleanup_old_db_records()

    assert report.db_records_removed == 1
    assert store.get("old-job") is None
    assert store.get("recent-job") is not None
    assert _table_count(db, "stage_tasks", "job_id=?", ("old-job",)) == 0
    assert _table_count(db, "decision_points", "id=?", ("dec-old",)) == 0


# ====================================================================== #
# (f) GET /jobs/{id}/detail — aggregation, disk backfill, recovery matrix.
# ====================================================================== #


@pytest.mark.parametrize(
    ("status", "workflow", "disk_exists", "expected"),
    [
        (
            JobStatus.RUNNING,
            "fake",
            True,
            {
                "can_pause": True,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": False,
                "can_cancel": True,
                "continue_mode": "",
            },
        ),
        (
            JobStatus.PAUSED,
            "fake",
            True,
            {
                "can_pause": False,
                "can_unpause": True,
                "can_continue": False,
                "can_rerun": False,
                "can_cancel": True,
                "continue_mode": "",
            },
        ),
        (
            JobStatus.FAILED,
            "mechanism",
            True,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": True,
                "can_rerun": True,
                "can_cancel": False,
                "continue_mode": "checkpoint",
            },
        ),
        (
            JobStatus.CANCELLED,
            "mechanism",
            True,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": True,
                "can_rerun": True,
                "can_cancel": False,
                "continue_mode": "checkpoint",
            },
        ),
        (
            JobStatus.FAILED,
            "xtbmd_censo_energy",
            True,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": True,
                "can_rerun": True,
                "can_cancel": False,
                "continue_mode": "checkpoint",
            },
        ),
        (
            JobStatus.FAILED,
            "singlepoint",
            True,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": True,
                "can_cancel": False,
                "continue_mode": "",
            },
        ),
        # Continue requires the work_dir to still exist on disk.
        (
            JobStatus.FAILED,
            "mechanism",
            False,
            {"can_continue": False, "can_rerun": True},
        ),
        (
            JobStatus.COMPLETED,
            "energy",
            True,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": True,
                "can_cancel": False,
                "continue_mode": "",
            },
        ),
        (
            JobStatus.QUEUED,
            "fake",
            False,
            {
                "can_pause": False,
                "can_unpause": False,
                "can_continue": False,
                "can_rerun": False,
                "can_cancel": True,
                "continue_mode": "",
            },
        ),
    ],
)
def test_compute_recovery_matrix(status, workflow, disk_exists, expected) -> None:
    record = JobRecord(
        id="x",
        spec=JobSpec(workflow=workflow),
        status=status,
        work_dir="/some/dir" if disk_exists else "",
    )
    recovery = _compute_recovery(record, JobDiskState(work_dir_exists=disk_exists))
    for key, value in expected.items():
        assert getattr(recovery, key) is value, f"{status}/{workflow}/{key}"


def test_compute_recovery_notes_per_workflow() -> None:
    def notes_for(status: JobStatus, workflow: str) -> str:
        record = JobRecord(id="x", spec=JobSpec(workflow=workflow), status=status, work_dir="/d")
        return _compute_recovery(record, JobDiskState(work_dir_exists=True)).continue_notes

    assert "mechanism" in notes_for(JobStatus.FAILED, "mechanism")
    assert "指纹" in notes_for(JobStatus.FAILED, "xtbmd_censo_energy")
    assert "不支持断点续算" in notes_for(JobStatus.FAILED, "singlepoint")
    assert "不支持断点续算" in notes_for(JobStatus.COMPLETED, "energy")
    assert notes_for(JobStatus.RUNNING, "fake") == ""


def test_detail_endpoint_recovery_and_disk_state(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/mech-detail",
            "mech-detail",
            workflow="mechanism",
            status=JobStatus.FAILED,
            error="S3 TS opt crashed",
        )
        # study.json checkpoint for the mechanism stage fallback + disk probe.
        study_dir = tmp_path / "runs/mech-detail/mechanism_study/study-1"
        study_dir.mkdir(parents=True)
        (study_dir / "study.json").write_text(
            json.dumps({"study_id": "study-1", "phase_fingerprints": {"S0": {}, "S1": {}}}),
            encoding="utf-8",
        )

        detail = get_job_detail("mech-detail", _StubRequest(mgr))
        assert detail.job.id == "mech-detail"
        assert detail.recovery.can_continue is True
        assert detail.recovery.continue_mode == "checkpoint"
        assert detail.recovery.can_rerun is True
        assert detail.recovery.can_pause is False
        assert detail.disk_state.work_dir_exists is True
        assert detail.disk_state.has_state_json is False
        assert detail.disk_state.has_study_checkpoint is True
        assert detail.disk_state.size_bytes > 0
        # Mechanism fallback stages from phase fingerprints: S0/S1 completed,
        # S2 failed (record failed), the rest untouched.
        stage_map = {s.stage_name: s.status for s in detail.stages}
        assert stage_map.get("S0") == "completed"
        assert stage_map.get("S1") == "completed"
        assert stage_map.get("S2") == "failed"
        assert detail.error_detail is not None
        assert detail.error_detail.error == "S3 TS opt crashed"
        assert detail.error_detail.failed_stage == "S2"
    finally:
        mgr.shutdown()


def test_detail_endpoint_running_job_can_pause(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/live", "live-detail", status=JobStatus.RUNNING)
        detail = get_job_detail("live-detail", _StubRequest(mgr))
        assert detail.recovery.can_pause is True
        assert detail.recovery.can_unpause is False
        assert detail.recovery.can_cancel is True
        assert detail.recovery.can_rerun is False
    finally:
        mgr.shutdown()


def test_detail_endpoint_paused_job_can_unpause(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(mgr.store, tmp_path / "runs/hold", "paused-detail", status=JobStatus.PAUSED)
        detail = get_job_detail("paused-detail", _StubRequest(mgr))
        assert detail.recovery.can_pause is False
        assert detail.recovery.can_unpause is True
        assert detail.recovery.can_cancel is True
    finally:
        mgr.shutdown()


def test_detail_endpoint_backfills_result_from_disk(tmp_path: Path) -> None:
    """R1: terminal job with result_json null gets a display-only backfill."""
    mgr = _make_manager(tmp_path)
    try:
        work = tmp_path / "runs/backfill"
        _seed_job(mgr.store, work, "backfill", workflow="energy", status=JobStatus.FAILED)
        assert mgr.get("backfill").result is None  # type: ignore[union-attr]
        (work / "state.json").write_text(
            json.dumps(
                {"current_stage": "single_point", "stages": {"opt": {"status": "completed"}}}
            ),
            encoding="utf-8",
        )

        detail = get_job_detail("backfill", _StubRequest(mgr))
        assert detail.job.result is not None
        assert detail.job.result["state"]["current_stage"] == "single_point"
        # Backfill is display-only — nothing persisted.
        assert mgr.get("backfill").result is None  # type: ignore[union-attr]
    finally:
        mgr.shutdown()


def test_detail_endpoint_stage_column_mapping(tmp_path: Path) -> None:
    """stage_tasks ``state`` → status, ``stderr_summary`` → error."""
    mgr = _make_manager(tmp_path)
    try:
        db = mgr.store.db_path
        _seed_job(
            mgr.store,
            tmp_path / "runs/staged",
            "staged",
            workflow="energy",
            status=JobStatus.FAILED,
            error="workflow exited with code 1",
        )
        stage_store = StageTaskStore(db)
        stage_store.create(
            StageTask(task_id="t-run", job_id="staged", stage_name="crest", state="running")
        )
        stage_store.create(
            StageTask(
                task_id="t-fail",
                job_id="staged",
                stage_name="dft_optimize",
                state="failed",
                stderr_summary="ORCA out of memory",
            )
        )

        detail = get_job_detail("staged", _StubRequest(mgr))
        by_name = {s.stage_name: s for s in detail.stages}
        assert by_name["crest"].status == "running"
        assert by_name["crest"].error is None
        assert by_name["dft_optimize"].status == "failed"
        assert by_name["dft_optimize"].error == "ORCA out of memory"
        assert detail.error_detail is not None
        assert detail.error_detail.failed_stage == "dft_optimize"
    finally:
        mgr.shutdown()


def test_detail_endpoint_error_detail_none_for_never_failed(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        _seed_job(
            mgr.store,
            tmp_path / "runs/ok",
            "happy",
            status=JobStatus.COMPLETED,
            result={"final_energy_hartree": -154.0},
        )
        detail = get_job_detail("happy", _StubRequest(mgr))
        assert detail.error_detail is None
        assert detail.job.result == {"final_energy_hartree": -154.0}
        assert detail.stages == []
    finally:
        mgr.shutdown()


def test_detail_endpoint_stderr_tail_capped_at_40_lines(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        work = tmp_path / "runs/noisy"
        _seed_job(mgr.store, work, "noisy", status=JobStatus.FAILED, error="boom")
        lines = [f"stderr line {i:03d}" for i in range(100)]
        (work / "stderr.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

        detail = get_job_detail("noisy", _StubRequest(mgr))
        assert detail.error_detail is not None
        tail = detail.error_detail.stderr_tail.splitlines()
        assert len(tail) == 40
        assert tail[0] == "stderr line 060"
        assert tail[-1] == "stderr line 099"
    finally:
        mgr.shutdown()


def test_detail_endpoint_artifacts_summary(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        work = tmp_path / "runs/arts"
        _seed_job(mgr.store, work, "arts", status=JobStatus.COMPLETED)
        payload = work / "final.xyz"
        payload.write_text("3\nxyz\nC 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")
        ArtifactRegistry(mgr.store.db_path).register(
            Artifact(
                artifact_id="art-x",
                task_id=None,
                job_id="arts",
                artifact_type="xyz",
                file_path="final.xyz",
                checksum=None,
                size_bytes=payload.stat().st_size,
            )
        )

        detail = get_job_detail("arts", _StubRequest(mgr))
        assert len(detail.artifacts_summary) == 1
        entry = detail.artifacts_summary[0]
        assert entry.type == "xyz"
        assert entry.path == "final.xyz"
        assert entry.size == payload.stat().st_size
    finally:
        mgr.shutdown()


def test_detail_endpoint_unknown_job_404(tmp_path: Path) -> None:
    mgr = _make_manager(tmp_path)
    try:
        with pytest.raises(HTTPException) as excinfo:
            get_job_detail("missing", _StubRequest(mgr))
        assert excinfo.value.status_code == 404
    finally:
        mgr.shutdown()


# ====================================================================== #
# Remote LSF suspension mapping (monitor + poll_remote).
# ====================================================================== #


def test_lsf_state_map_suspensions_to_paused() -> None:
    for code in ("PSUSP", "SSUSP", "USUSP"):
        assert _LSF_STATE_MAP[code] == STATUS_PAUSED, code
    assert _LSF_STATE_MAP["RUN"] == "running"
    assert _LSF_STATE_MAP["PEND"] == "pending"
    assert RemoteJobMonitor.is_terminal(STATUS_PAUSED) is False


@pytest.mark.parametrize("stat_code", ["PSUSP", "SSUSP", "USUSP"])
def test_monitor_get_lsf_status_parses_suspensions(stat_code: str) -> None:
    node = _make_node()
    pool = SSHConnectionPool()
    client = _FakeSSHClient()
    client.handler = lambda cmd: (
        0,
        "JOBID   USER    STAT   QUEUE    FROM_HOST  EXEC_HOST  JOB_NAME  SUBMIT_TIME\n"
        f"12345   u1      {stat_code}  normal   h1         e1         jobname   Aug 17 10:00\n",
        "",
    )
    monitor = RemoteJobMonitor(pool, FileStager(pool))
    try:
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
            assert monitor.get_lsf_status(node, "12345") == STATUS_PAUSED
    finally:
        pool.close()


def test_monitor_bstop_and_bresume(tmp_path: Path) -> None:
    node = _make_node()
    pool = SSHConnectionPool()
    client = _FakeSSHClient()
    client.handler = lambda cmd: (0, "", "")
    monitor = RemoteJobMonitor(pool, FileStager(pool))
    try:
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
            assert monitor.bstop_job(node, "12345") is True
            assert monitor.bresume_job(node, "12345") is True
        assert any("bstop 12345" in c for c in client.executed)
        assert any("bresume 12345" in c for c in client.executed)

        # Non-zero exit → False (never raises).
        client.handler = lambda cmd: (255, "Job <12345> not found", "")
        with patch.object(ssh_mod, "_create_client", side_effect=lambda n, timeout=30: client):
            assert monitor.bstop_job(node, "12345") is False
            assert monitor.bresume_job(node, "12345") is False
    finally:
        pool.close()


def _poll_remote_runner(
    lsf_status: str, tmp_dir: Path
) -> tuple[RemoteJobRunner, JobRecord, JobEventLog]:
    monitor = MagicMock()
    monitor.get_exit_code.return_value = None
    monitor.get_lsf_status.return_value = lsf_status
    monitor.find_remote_state_json.return_value = None
    monitor.tail_stdout.return_value = ("", 0)
    monitor.tail_stderr.return_value = ("", 0)
    runner = RemoteJobRunner(
        ssh_pool=MagicMock(),
        remote_config=MagicMock(),
        stager=MagicMock(),
        monitor=monitor,
        code_syncer=MagicMock(),
        poll_interval=0,
    )
    runner._job_states["remote-1"] = {
        "node": MagicMock(),
        "remote_job_dir": "/scratch/acp/remote-1",
        "lsf_job_id": "777",
        "stdout_offset": 0,
        "stderr_offset": 0,
        "poll_cycle": 0,
        "seen_stages": set(),
    }
    log = JobEventLog(tmp_dir / "events.jsonl")
    record = JobRecord(
        id="remote-1",
        spec=JobSpec(workflow="energy", name="remote"),
        status=JobStatus.RUNNING,
        work_dir=str(tmp_dir),
        remote_job_id="777",
    )
    return runner, record, log


def test_poll_remote_transitions_running_to_paused(tmp_path: Path) -> None:
    runner, record, log = _poll_remote_runner(STATUS_PAUSED, tmp_path)
    is_terminal, exit_code = runner.poll_remote(record, log, threading.Event())

    assert is_terminal is False
    assert exit_code is None
    assert record.status == JobStatus.PAUSED
    assert record.completed_at is None


def test_poll_remote_transitions_paused_back_to_running(tmp_path: Path) -> None:
    runner, record, log = _poll_remote_runner("running", tmp_path)
    record.status = JobStatus.PAUSED
    is_terminal, _exit = runner.poll_remote(record, log, threading.Event())

    assert is_terminal is False
    assert record.status == JobStatus.RUNNING


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
