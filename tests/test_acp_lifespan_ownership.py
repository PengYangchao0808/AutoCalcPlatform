"""Lifespan ownership matrix for the scheduler (plan todo 7 / R1).

Proves: import starts no scheduler threads; ``create_app`` is scheduler-less;
a second concurrent lifespan entry for one run_root is refused; construction
failure rolls back with no residue and a retry succeeds; shutdown stops workers
before releasing ownership (slow-thread case); duplicate shutdown is safe;
relative/symlink aliases share one ownership key; different run_roots run
independently; sequential start/stop/start works.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.manager import (
    _RUN_ROOT_CLAIMS,
    JobManager,
    resolve_ownership_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _acp_thread_counts() -> Counter[str]:
    return Counter(t.name for t in threading.enumerate() if t.name.startswith("acp-"))


def test_import_starts_no_scheduler_threads(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["ACP_RUN_ROOT"] = str(tmp_path / "runs")
    code = (
        "import threading, acp.api.server as s;"
        "print('THREADS=' + '|'.join(sorted(t.name for t in threading.enumerate())));"
        "print('MANAGER=' + str(hasattr(s.app.state, 'job_manager')))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    threads_line = next(line for line in proc.stdout.splitlines() if line.startswith("THREADS="))
    names = threads_line.split("=", 1)[1]
    assert "acp-poller" not in names
    assert "acp-reconciler" not in names
    assert "MANAGER=False" in proc.stdout


def test_create_app_builds_scheduler_less_app(tmp_path: Path) -> None:
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path / "runs")
    assert not hasattr(app.state, "job_manager")
    assert not (tmp_path / "runs" / ".manager.lock").exists()


def test_second_concurrent_lifespan_is_refused(tmp_path: Path) -> None:
    from acp.api.server import create_app

    run_root = tmp_path / "runs"
    app1 = create_app(run_root=run_root)
    app2 = create_app(run_root=run_root)
    with TestClient(app1) as client:
        assert client.get("/api/status").status_code == 200
        with pytest.raises(RuntimeError, match="already owned"):
            with TestClient(app2):
                pass
    with TestClient(app2) as client:
        assert client.get("/api/status").status_code == 200


def test_concurrent_lifespan_race_exactly_one_owner(tmp_path: Path) -> None:
    from acp.api.server import create_app

    run_root = tmp_path / "runs"
    barrier = threading.Barrier(2)
    release = threading.Event()
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def run(app) -> None:
        try:
            barrier.wait(timeout=15)
            with TestClient(app) as client:
                assert client.get("/api/status").status_code == 200
                with outcomes_lock:
                    outcomes.append("ok")
                release.wait(timeout=15)
        except RuntimeError as exc:
            with outcomes_lock:
                outcomes.append(f"refused:{exc}")

    apps = [create_app(run_root=run_root), create_app(run_root=run_root)]
    threads = [threading.Thread(target=run, args=(app,)) for app in apps]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with outcomes_lock:
            if outcomes:
                break
        time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("ok") == 1, outcomes
    assert sum(1 for outcome in outcomes if outcome.startswith("refused:")) == 1, outcomes


def test_init_failure_leaves_no_residue_and_retry_succeeds(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    key = resolve_ownership_key(run_root)
    before = _acp_thread_counts()

    with patch.object(JobManager, "_rebuild_reservations", side_effect=RuntimeError("init boom")):
        with pytest.raises(RuntimeError, match="init boom"):
            JobManager(run_root=run_root, poll_interval=30)

    assert not (run_root / ".manager.lock").exists()
    assert key not in _RUN_ROOT_CLAIMS
    assert _acp_thread_counts() == before

    manager = JobManager(run_root=run_root, poll_interval=30)
    try:
        assert key in _RUN_ROOT_CLAIMS
    finally:
        manager.shutdown()
    assert key not in _RUN_ROOT_CLAIMS


def test_shutdown_stops_workers_before_releasing_lock(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    lock_path = run_root / ".manager.lock"
    observed: list[bool] = []

    def slow_poll(self: JobManager) -> None:
        while not self._poll_stop.wait(0.02):
            pass
        observed.append(lock_path.exists())
        time.sleep(0.5)
        observed.append(lock_path.exists())

    with patch.object(JobManager, "_poll_loop", slow_poll):
        manager = JobManager(run_root=run_root, poll_interval=30)
        manager.shutdown()

    assert observed == [True, True]
    assert not lock_path.exists()
    assert resolve_ownership_key(run_root) not in _RUN_ROOT_CLAIMS


def test_duplicate_shutdown_is_safe(tmp_path: Path) -> None:
    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    manager.shutdown()
    manager.shutdown()


def test_relative_and_symlink_aliases_share_ownership_key(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    relative = os.path.relpath(real, Path.cwd())

    assert resolve_ownership_key(real) == resolve_ownership_key(link)
    assert resolve_ownership_key(real) == resolve_ownership_key(relative)

    manager = JobManager(run_root=real, poll_interval=30)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            JobManager(run_root=link, poll_interval=30)
    finally:
        manager.shutdown()


def test_different_run_roots_run_independently(tmp_path: Path) -> None:
    manager_a = JobManager(run_root=tmp_path / "a", poll_interval=30)
    manager_b = JobManager(run_root=tmp_path / "b", poll_interval=30)
    try:
        assert (tmp_path / "a" / ".manager.lock").exists()
        assert (tmp_path / "b" / ".manager.lock").exists()
    finally:
        manager_a.shutdown()
        manager_b.shutdown()


def test_sequential_start_stop_start(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    for _ in range(2):
        manager = JobManager(run_root=run_root, poll_interval=30)
        assert (run_root / ".manager.lock").exists()
        manager.shutdown()
        assert not (run_root / ".manager.lock").exists()


def test_shutdown_refuses_late_worker_registration(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    lock_path = run_root / ".manager.lock"
    key = resolve_ownership_key(run_root)
    manager = JobManager(run_root=run_root, poll_interval=30)
    real_background_threads = manager._background_threads
    late_result: list[bool] = []
    calls = {"n": 0}

    def racing_background_threads() -> list[threading.Thread]:
        snapshot = real_background_threads()
        if calls["n"] == 0:
            calls["n"] += 1
            late_result.append(manager._start_submission_thread("late-job", "acp-late-late-job"))
        return snapshot

    with patch.object(manager, "_background_threads", racing_background_threads):
        manager.shutdown()

    assert late_result == [False]
    assert "late-job" not in manager._submission_threads
    assert not any(
        thread.name == "acp-late-late-job" and thread.is_alive() for thread in threading.enumerate()
    )
    assert not lock_path.exists()
    assert key not in _RUN_ROOT_CLAIMS


def test_shutdown_refuses_late_catalog_prefetch_worker(tmp_path: Path) -> None:
    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    try:
        assert manager._catalog_prefetch_thread is None
        with patch.object(manager, "_remote_fetcher", object()):
            manager.shutdown()
            manager._queue_catalog_prefetch("late-prefetch")
        assert manager._catalog_prefetch_thread is None
        assert not any(
            thread.name == "acp-catalog-prefetch" and thread.is_alive()
            for thread in threading.enumerate()
        )
    finally:
        manager.shutdown()
