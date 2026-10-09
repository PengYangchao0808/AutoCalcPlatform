"""processctl unit tests — cross-restart process discovery + termination.

Covers: zombie semantics (never "alive"), /proc-based discovery by
cmdline/cwd, process-group termination handshake, the PID-recycling
guard for recorded extra PIDs, and the boundary-safe cmdline matcher
regression matrix (same-prefix tasks ``X`` vs ``X__02`` must never see
each other — a bare substring match once SIGTERMed a sibling task).

Linux/WSL only: every test is skipped when /proc is unavailable.
"""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from acp.scheduler import processctl
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.processctl import (
    _cmdline_contains_path,
    find_task_processes,
    pid_is_alive,
    pid_is_zombie,
    process_references,
    terminate_task_processes,
)
from acp.scheduler.runner import JobRunner

pytestmark = pytest.mark.skipif(not Path("/proc").is_dir(), reason="requires /proc (Linux/WSL)")

_FAKE_PID = 424242


def _sleeper(cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S604
        ["sleep", "60"],
        cwd=str(cwd) if cwd is not None else None,
        start_new_session=True,
    )


def _argv_path_sleeper(path_arg: Path, cwd: Path) -> subprocess.Popen[bytes]:
    """Live process whose NUL-joined /proc cmdline carries *path_arg* in argv.

    ``cwd`` is deliberately outside every task directory so only the cmdline
    probe can match — this is the exact probe that once false-positived on
    same-prefix sibling task directories.
    """
    return subprocess.Popen(  # noqa: S604
        [sys.executable, "-c", "import time; time.sleep(60)", str(path_arg)],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _seed_cmdline(monkeypatch: pytest.MonkeyPatch, cmdline: str) -> None:
    """Replace the /proc probes with a seeded cmdline (machine-PID independent)."""
    monkeypatch.setattr(processctl, "read_cwd", lambda pid: "")
    monkeypatch.setattr(processctl, "read_cmdline", lambda pid: cmdline)


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


# ---------------------------------------------------------------------- #
# Same-prefix sibling boundary matrix (BUG-2, IS-2): a rerun/kill of task
# /…/X must never signal processes of /…/X__02 — observed cross-task SIGTERM.
# ---------------------------------------------------------------------- #


def test_process_references_rejects_sibling_prefix_cmdline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X's needle must not match X__02's argv (observed signal -15)."""
    work = (tmp_path / "X").resolve()
    sibling_argv = f"python -m acp.cli run scan --output {work.parent}/X__02/run.sh"
    _seed_cmdline(monkeypatch, sibling_argv)
    assert process_references(_FAKE_PID, work) is False


def test_process_references_needle_sibling_rejects_shorter_task_cmdline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bidirectional: X__02's needle must not match X's argv either."""
    work = (tmp_path / "X__02").resolve()
    shorter_argv = f"python -m acp.cli run scan --output {work.parent}/X/run.sh"
    _seed_cmdline(monkeypatch, shorter_argv)
    assert process_references(_FAKE_PID, work) is False


def test_process_references_accepts_own_task_argv_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """True positives: every argv form of the task's own path still match."""
    work = (tmp_path / "X").resolve()
    forms = [
        f"python -m acp.cli run scan --output={work}",  # --output=/p
        f"python -m acp.cli run scan --output {work}",  # --output /p
        f"python -m acp.cli run scan --output {work}/sub",  # subpath /p/sub
        # multi-argument argv with the path at the tail
        f"python -m acp.cli run scan --input in.xyz --coordinate 3,4,1.0,3.0 --output {work}",
        f"python -c 'run({work})'",  # quote + paren
        f"tool({work}, 2)",  # comma
        f'orca -i input.inp --output "{work}"',  # double quote
    ]
    for cmdline in forms:
        _seed_cmdline(monkeypatch, cmdline)
        assert process_references(_FAKE_PID, work) is True, cmdline


def test_find_task_processes_separates_same_prefix_tasks(tmp_path: Path) -> None:
    """Discovery level: a live X__02 process is invisible to the X scan."""
    work = (tmp_path / "X").resolve()
    work.mkdir()
    sibling = work.parent / "X__02"
    sibling.mkdir()
    proc = _argv_path_sleeper(sibling / "task.sh", cwd=work.parent)
    try:
        # Pre-fix, the bare substring match made …/X discover …/X__02's
        # argv — the rerun/kill path then SIGTERMed the sibling task.
        assert find_task_processes(work) == []
        assert proc.pid in find_task_processes(sibling)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_guard_single_execution_not_blocked_by_same_prefix_sibling(tmp_path: Path) -> None:
    """IS-2 third clause: a live X__02 sibling must not block starting X."""
    work = (tmp_path / "X").resolve()
    work.mkdir()
    sibling = work.parent / "X__02"
    sibling.mkdir()
    record = JobRecord(
        id="20261007_sibling_guard",
        spec=JobSpec(
            workflow="scan",
            name="sibling-guard",
            input={"source_type": "smiles", "source": "CCO"},
        ),
        status=JobStatus.QUEUED,
        work_dir=str(work),
    )
    sibling_proc = _argv_path_sleeper(sibling / "task.sh", cwd=work.parent)
    runner = JobRunner()
    try:
        runner._guard_single_execution(record)
        assert runner._foreign_task_alive(record) is False
        assert find_task_processes(work) == []
        assert sibling_proc.pid in find_task_processes(sibling)
    finally:
        sibling_proc.kill()
        sibling_proc.wait(timeout=10)


def test_guard_single_execution_still_blocks_own_live_process(tmp_path: Path) -> None:
    """True positive rail: an own live task process keeps refusing a second start."""
    work = (tmp_path / "Y").resolve()
    work.mkdir()
    record = JobRecord(
        id="20261007_own_guard",
        spec=JobSpec(
            workflow="scan",
            name="own-guard",
            input={"source_type": "smiles", "source": "CCO"},
        ),
        status=JobStatus.QUEUED,
        work_dir=str(work),
    )
    own = _sleeper(work)
    try:
        with pytest.raises(RuntimeError, match="still has live process"):
            JobRunner()._guard_single_execution(record)
    finally:
        own.kill()
        own.wait(timeout=10)


def test_extra_pid_not_signalled_when_cmdline_only_references_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extra_pids path inherits the boundary fix (PID-recycling guard intact)."""
    work = (tmp_path / "X").resolve()
    work.mkdir()
    proc = _sleeper(tmp_path)
    sibling_argv = f"python -m acp.cli run scan --output {work.parent}/X__02/run.sh"
    monkeypatch.setattr(processctl, "read_cwd", lambda pid: "")
    monkeypatch.setattr(
        processctl,
        "read_cmdline",
        lambda pid: sibling_argv if pid == proc.pid else "",
    )
    try:
        killed = terminate_task_processes(work, extra_pids=[proc.pid])
        assert killed == []
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


# ---------------------------------------------------------------------- #
# Direct boundary-matrix against _cmdline_contains_path (the single
# predicate behind process_references / find_task_processes / extra_pids).
# ---------------------------------------------------------------------- #


def test_cmdline_contains_path_accepts_all_argv_forms() -> None:
    p = "/runs/proj/p"
    assert _cmdline_contains_path(f"python -m acp.cli run scan --output={p}", p)
    assert _cmdline_contains_path(f"python -m acp.cli run scan --output {p}", p)
    assert _cmdline_contains_path(f"python -m acp.cli run --output {p}/sub", p)
    assert _cmdline_contains_path(
        f"python -m acp.cli run scan --input in.xyz --coordinate 3,4,1.0,3.0 --output {p}", p
    )
    assert _cmdline_contains_path(f"chdir({p})", p)
    assert _cmdline_contains_path(f"tool({p}, 2)", p)
    assert _cmdline_contains_path(f'--output "{p}"', p)
    assert _cmdline_contains_path(f"--output '{p}'", p)
    assert _cmdline_contains_path(p, p)
    assert _cmdline_contains_path(f"{p}\tnext", p)


def test_cmdline_contains_path_rejects_same_prefix_and_path_continuations() -> None:
    assert not _cmdline_contains_path("python --output /runs/X__02/run.sh", "/runs/X")
    assert not _cmdline_contains_path("python --output /runs/X/run.sh", "/runs/X__02")
    assert not _cmdline_contains_path("tool --output /p2", "/p")
    assert not _cmdline_contains_path("tool --output /p_2", "/p")
    assert not _cmdline_contains_path("tool --output /pX", "/p")
    assert not _cmdline_contains_path("dir/p", "/p")
    assert not _cmdline_contains_path("", "/p")
    assert not _cmdline_contains_path("tool --output /p", "")


def test_cmdline_contains_path_continues_past_partial_match() -> None:
    assert _cmdline_contains_path("/runs/X__02 /runs/X", "/runs/X")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
