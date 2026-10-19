"""B41b defect remediation regressions (plan todo 79).

Covers the measured defects in ``docs/reference/next-change/
ACP_Services_Standalone_Recipe.md`` §2.1:

* D1 — an untracked RUNNING row whose worker finished in another controller's
  lifetime must finalize from durable completion evidence (never ``failed``);
  a cross-process CANCELLING row finalizes CANCELLED.
* D2 — ``acp task recover`` carries the persisted method + merged executable
  config into the plan/execution context (no ``! default`` placeholder, no
  GNOME Orca PATH fallback).
* D3 — a startup-window SIGKILL orphan is adopted (or stopped) instead of
  failing the job while the worker keeps running.
* S4c — an explicitly configured but invalid executable path is FAIL, never a
  PATH-fallback PASS; PATH binaries pass a QC sanity check.
* S4 — a local/standalone deployment with zero remote nodes reports local
  capability and no longer exits 1 solely for missing remote nodes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from acp import cli
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.runner import JobRunner
from acp.storage.layout import TaskStorage, runtime_file
from acp_services.store.store import JobStore

_XYZ_TEXT = "3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.93 -0.24\n"


def _spec(workflow: str = "singlepoint") -> JobSpec:
    return JobSpec(
        workflow=workflow,
        name="b41b",
        input={"source": "CCO", "source_type": "smiles"},
    )


def _seed(
    run_root: Path,
    work_dir: Path,
    job_id: str,
    status: str,
    *,
    workflow: str = "singlepoint",
    attempt: int = 1,
) -> JobRecord:
    work_dir.mkdir(parents=True, exist_ok=True)
    TaskStorage(work_dir).ensure_layout()
    record = JobRecord(
        id=job_id,
        spec=_spec(workflow),
        status=JobStatus(status),
        work_dir=str(work_dir),
        attempt=attempt,
    )
    JobStore(run_root / "acp_jobs.db").create(record)
    return record


def _write_state(work_dir: Path, status: str, *, attempt: int | None = None) -> None:
    payload = {
        "version": "1.0",
        "job_name": "b41b",
        "status": status,
        "current_stage": None,
        "stages": {"single_point": {"status": "completed" if status == "completed" else "failed"}},
        "completed_at": "2026-10-19T00:00:00+00:00" if status == "completed" else None,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    (work_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _live_worker(work_dir: Path) -> subprocess.Popen[bytes]:
    work_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)", "acp-worker"],
        cwd=str(work_dir),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), 15)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _manager(run_root: Path) -> JobManager:
    return JobManager(run_root=run_root, poll_interval=3600, reconnect_local_workers=True)


def _startup_manager(run_root: Path) -> JobManager:
    return JobManager(run_root=run_root, poll_interval=3600, reconnect_local_workers=False)


# --------------------------------------------------------------------------- #
# D1 — durable completion / cancel evidence for untracked rows
# --------------------------------------------------------------------------- #


def test_runner_untracked_completed_evidence_returns_success(tmp_path: Path) -> None:
    runner = JobRunner()
    work_dir = tmp_path / "job"
    TaskStorage(work_dir).ensure_layout()
    _write_state(work_dir, "completed")
    record = JobRecord(
        id="job-evidence", spec=_spec(), status=JobStatus.RUNNING, work_dir=str(work_dir)
    )

    assert runner.poll(record) == (True, 0)


def test_runner_untracked_failed_evidence_returns_failure(tmp_path: Path) -> None:
    runner = JobRunner()
    work_dir = tmp_path / "job"
    TaskStorage(work_dir).ensure_layout()
    _write_state(work_dir, "failed")
    record = JobRecord(
        id="job-failed-evidence", spec=_spec(), status=JobStatus.RUNNING, work_dir=str(work_dir)
    )

    assert runner.poll(record) == (True, 1)


def test_runner_untracked_cancelling_returns_cancel_code(tmp_path: Path) -> None:
    runner = JobRunner()
    work_dir = tmp_path / "job"
    TaskStorage(work_dir).ensure_layout()
    _write_state(work_dir, "completed")
    record = JobRecord(
        id="job-cancel-evidence",
        spec=_spec(),
        status=JobStatus.CANCELLING,
        work_dir=str(work_dir),
    )

    assert runner.poll(record) == (True, 130)


def test_cross_process_completed_row_finalizes_completed(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-done", "running")
    _write_state(work_dir, "completed")

    manager = _manager(run_root)
    try:
        manager._poll_job("job-done")
        fresh = manager.store.get("job-done")
        assert fresh is not None
        assert fresh.status is JobStatus.COMPLETED
        assert fresh.exit_code == 0
        assert fresh.progress == 1.0
    finally:
        manager.shutdown()


def test_cross_process_cancelling_row_finalizes_cancelled(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-cancelling", "cancelling")
    _write_state(work_dir, "completed")

    manager = _manager(run_root)
    try:
        manager._poll_job("job-cancelling")
        fresh = manager.store.get("job-cancelling")
        assert fresh is not None
        assert fresh.status is JobStatus.CANCELLED
    finally:
        manager.shutdown()


def test_fresh_controller_finalizes_unobserved_local_completion(tmp_path: Path) -> None:
    """F3 D1: worker finished with no controller and no ``.exit_code`` marker.

    The next controller's startup recovery must read the durable
    ``state.json`` completion and finalize COMPLETED instead of stamping
    ``[RESTART_FAILED]`` over a true success.
    """
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-unobserved", "running")
    _write_state(work_dir, "completed")
    assert not (work_dir / ".exit_code").exists()

    manager = _startup_manager(run_root)
    try:
        fresh = manager.store.get("job-unobserved")
        assert fresh is not None
        assert fresh.status is JobStatus.COMPLETED
        assert fresh.exit_code == 0
        assert "[RESTART_FAILED]" not in (fresh.error or "")
    finally:
        manager.shutdown()


def test_fresh_controller_accepts_archived_attempt_completion(tmp_path: Path) -> None:
    """F3 D1: the current attempt's receipt was archived by contract B."""
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-archived", "running", attempt=2)
    archive = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "2"
    archive.mkdir(parents=True)
    _write_state(archive, "completed", attempt=2)

    manager = _startup_manager(run_root)
    try:
        fresh = manager.store.get("job-archived")
        assert fresh is not None
        assert fresh.status is JobStatus.COMPLETED
        assert fresh.exit_code == 0
        assert "[RESTART_FAILED]" not in (fresh.error or "")
    finally:
        manager.shutdown()


def test_fresh_controller_ignores_other_attempt_archived_receipt(tmp_path: Path) -> None:
    """Attempt fence: an archived receipt for a different attempt is never adopted."""
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-stale-archive", "running", attempt=2)
    stale = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
    stale.mkdir(parents=True)
    _write_state(stale, "completed", attempt=1)

    manager = _startup_manager(run_root)
    try:
        fresh = manager.store.get("job-stale-archive")
        assert fresh is not None
        assert fresh.status is JobStatus.FAILED
        assert "[RESTART_FAILED]" in (fresh.error or "")
    finally:
        manager.shutdown()


def test_fresh_controller_cancelling_local_carries_exit_130_without_restart_marker(
    tmp_path: Path,
) -> None:
    """F3 D2: a local CANCELLING row is confirmed-stopped then CANCELLED/130."""
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    _seed(run_root, work_dir, "job-cancel-startup", "cancelling")

    manager = _startup_manager(run_root)
    try:
        fresh = manager.store.get("job-cancel-startup")
        assert fresh is not None
        assert fresh.status is JobStatus.CANCELLED
        assert fresh.exit_code == 130
        assert "[RESTART_FAILED]" not in (fresh.error or "")
    finally:
        manager.shutdown()


# --------------------------------------------------------------------------- #
# D3 — startup-window SIGKILL orphan adoption / confirmed stop
# --------------------------------------------------------------------------- #


def test_startup_window_orphan_is_adopted_then_completed(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    worker = _live_worker(work_dir)
    try:
        _seed(run_root, work_dir, "job-orphan", "starting")
        manager = _manager(run_root)
        try:
            adopted = manager.store.get("job-orphan")
            assert adopted is not None
            assert adopted.status is JobStatus.RUNNING
            assert adopted.pid == worker.pid
            assert worker.poll() is None
            events = runtime_file(work_dir, "events.jsonl").read_text(encoding="utf-8")
            assert "process.adopted" in events

            _write_state(work_dir, "completed")
            worker.kill()
            worker.wait(timeout=10)
            manager._poll_job("job-orphan")
            final = manager.store.get("job-orphan")
            assert final is not None
            assert final.status is JobStatus.COMPLETED
        finally:
            manager.shutdown()
    finally:
        _stop(worker)


def test_queued_orphan_is_adopted_without_double_dispatch(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    manager = _manager(run_root)
    worker: subprocess.Popen[bytes] | None = None
    try:
        _seed(run_root, work_dir, "job-queued", "queued")
        worker = _live_worker(work_dir)
        assert manager._submit_job("job-queued") is True
        adopted = manager.store.get("job-queued")
        assert adopted is not None
        assert adopted.status is JobStatus.RUNNING
        assert adopted.pid == worker.pid
        assert worker.poll() is None
    finally:
        manager.shutdown()
        if worker is not None:
            _stop(worker)


def test_cross_process_cancel_stops_untracked_orphan(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    work_dir = run_root / "task"
    worker = _live_worker(work_dir)
    try:
        _seed(run_root, work_dir, "job-cancel", "running")
        runtime_file(work_dir, "run.lock").write_text(
            json.dumps(
                {
                    "job_id": "job-cancel",
                    "owner_pid": os.getpid(),
                    "task_pid": worker.pid,
                    "attempt": 1,
                }
            ),
            encoding="utf-8",
        )
        manager = _manager(run_root)
        try:
            record = manager.cancel("job-cancel")
            assert record is not None
            assert record.status is JobStatus.CANCELLING
            deadline = time.monotonic() + 15
            while worker.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert worker.poll() is not None

            manager._poll_job("job-cancel")
            final = manager.store.get("job-cancel")
            assert final is not None
            assert final.status is JobStatus.CANCELLED
        finally:
            manager.shutdown()
    finally:
        _stop(worker)


# --------------------------------------------------------------------------- #
# D2 — recover carries persisted method + executable config
# --------------------------------------------------------------------------- #


def test_build_task_plan_carries_real_method_and_executable_config(tmp_path: Path) -> None:
    from acp.calculations.executor import _extract_method
    from cccp.config import load_config

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "input.xyz").write_text(_XYZ_TEXT, encoding="utf-8")
    fake_orca = tmp_path / "orca"
    fake_orca.write_bytes(b"\x7fELF" + b"\0" * 64)
    fake_orca.chmod(0o755)
    config_path = tmp_path / "cccp.yaml"
    config_path.write_text(f"executables:\n  orca:\n    path: {fake_orca}\n", encoding="utf-8")
    config = load_config(config_path=config_path)

    plan = cli._build_task_plan(task_dir, "optimize", "r2SCAN-3c", "default", config=config)

    step = plan.steps[0]
    assert isinstance(step.spec, dict)
    assert step.spec["method"] == "r2SCAN-3c"
    assert step.spec["config"]["executables"]["orca"]["path"] == str(fake_orca)
    assert _extract_method(step.spec, "default") == "r2SCAN-3c"


def test_recover_uses_persisted_method_not_placeholder(
    tmp_path: Path, fake_backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "runs"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "input.xyz").write_text(_XYZ_TEXT, encoding="utf-8")
    (task_dir / "job.json").write_text(
        json.dumps(
            {
                "spec": {
                    "workflow": "singlepoint",
                    "method": {"method": "B3LYP", "basis": "def2-SVP"},
                    "resources": {},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))

    assert cli.main(["task", "recover", "--task-dir", str(task_dir)]) == 0

    methods = [call.kwargs.get("method") for call in fake_backend.calls]
    assert "B3LYP" in methods
    assert "default" not in methods


# --------------------------------------------------------------------------- #
# S4c / S4 — truthful doctor semantics
# --------------------------------------------------------------------------- #


def test_doctor_configured_invalid_orca_is_never_a_path_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = {
        "deployment": {"usage": "local", "tier": "local-single"},
        "cluster": {},
        "executables": {"orca": {"path": "/nonexistent/orca"}},
    }
    monkeypatch.setattr("cccp.config.load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "已配置路径不可用" in out
    assert "/usr/bin/orca（source=" not in out


def test_doctor_path_binary_sanity_rejects_python_orca(tmp_path: Path) -> None:
    fake = tmp_path / "orca"
    fake.write_text("#!/usr/bin/env python3\nprint('orca')\n", encoding="utf-8")
    fake.chmod(0o755)

    assert cli._doctor_binary_sanity("orca", fake) is not None
    assert cli._doctor_binary_sanity("orca", Path(sys.executable)) is None


def test_local_only_doctor_does_not_fail_for_missing_remote_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = {
        "deployment": {"usage": "local", "tier": "local-single"},
        "cluster": {},
        "executables": {},
    }
    monkeypatch.setattr("cccp.config.load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Local capability report" in out
    assert "No remote nodes configured (cluster.nodes is empty)." not in out


def test_non_local_config_keeps_remote_only_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = {"deployment": {"usage": "connect"}, "cluster": {}, "executables": {}}
    monkeypatch.setattr("cccp.config.load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "No remote nodes configured (cluster.nodes is empty)." in out


def test_remote_node_doctor_behavior_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Node:
        name = "compute-01"
        host = "compute-01.example"

    class _RemoteCfg:
        nodes = [_Node()]
        enabled_nodes = [_Node()]

        def get_node(self, name: str) -> Any:
            return _Node() if name == "compute-01" else None

    class _Python:
        python_executable = "/usr/bin/python3.11"
        version = "3.11.9"

    class _Report:
        node = "compute-01"
        host = "compute-01.example"
        reachable = True
        python = _Python()
        software = {"orca": {"resolved": "/opt/orca/orca", "version": "6.0.1"}}
        symlinks: dict[str, str] = {}
        error = None

    cfg = {
        "cluster": {"nodes": [{"name": "compute-01", "host": "compute-01.example"}]},
        "executables": {},
    }
    monkeypatch.setattr("cccp.config.load_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(
        "acp.scheduler.remote.config.RemoteExecutionConfig.from_config_dict",
        classmethod(lambda cls, cluster: _RemoteCfg()),
    )
    monkeypatch.setattr(
        "acp.scheduler.remote.node_manager.doctor_node",
        lambda pool, node: _Report(),
    )

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "== node compute-01 (compute-01.example) ==" in out
    assert "orca     -> /opt/orca/orca [version: 6.0.1]" in out
    assert "Doctor finished: all checks passed" in out
