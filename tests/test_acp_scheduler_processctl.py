"""processctl unit tests — cross-restart process discovery + termination.

Covers: zombie semantics (never "alive"), /proc-based discovery by
cmdline/cwd, process-group termination handshake, and the PID-recycling
guard for recorded extra PIDs.

Linux/WSL only: every test is skipped when /proc is unavailable.
"""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from acp.scheduler.processctl import (
    find_task_processes,
    pid_is_alive,
    pid_is_zombie,
    process_references,
    terminate_task_processes,
)

pytestmark = pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires /proc (Linux/WSL)")


def _sleeper(cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S604
        ["sleep", "60"],
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )


def _wait_zombie(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_is_zombie(pid):
            return
        time.sleep(0.02)


def test_zombie_is_not_alive(tmp_path: Path) -> None:
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child branch
        os._exit(0)
    try:
        _wait_zombie(pid)
        assert pid_is_zombie(pid)
        assert pid_is_alive(pid) is False
        # Zombies are invisible to task discovery: they neither compute
        # nor hold resources, so they must never block a rerun.
        assert pid not in find_task_processes(tmp_path)
    finally:
        os.waitpid(pid, 0)


def test_pid_is_alive_rejects_self_and_invalid(tmp_path: Path) -> None:
    assert pid_is_alive(os.getpid()) is False
    assert pid_is_alive(-1) is False
    assert pid_is_alive(0) is False
    sleeper = _sleeper(tmp_path)
    try:
        assert pid_is_alive(sleeper.pid) is True
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


def test_process_references_matches_cwd_and_cmdline(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    sleeper = _sleeper(work)
    other = _sleeper(tmp_path)
    try:
        assert process_references(sleeper.pid, work) is True
        assert process_references(other.pid, work) is False
    finally:
        sleeper.kill()
        other.kill()
        sleeper.wait(timeout=10)
        other.wait(timeout=10)


def test_find_and_terminate_task_processes(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    orphan = _sleeper(work)
    outsider = _sleeper(tmp_path)
    try:
        assert find_task_processes(work) == [orphan.pid]
        killed = terminate_task_processes(work)
        assert killed == [orphan.pid]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and orphan.poll() is None:
            time.sleep(0.05)
        assert orphan.poll() is not None
        assert outsider.poll() is None
    finally:
        orphan.kill()
        outsider.kill()
        orphan.wait(timeout=10)
        outsider.wait(timeout=10)


def test_extra_pid_killed_only_when_referencing_task(tmp_path: Path) -> None:
    """PID-recycling guard: a recorded pid dies only if it references the task."""
    work = tmp_path / "task"
    work.mkdir()
    insider = _sleeper(work)
    outsider = _sleeper(tmp_path)
    try:
        killed = terminate_task_processes(work, extra_pids=[outsider.pid, insider.pid])
        assert killed == [insider.pid]
        assert outsider.poll() is None
        assert insider.poll() is not None
    finally:
        outsider.kill()
        insider.kill()
        outsider.wait(timeout=10)
        insider.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
