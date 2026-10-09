"""
Scheduler Process Control
=========================

Cross-restart discovery and termination of task-owned processes.

Author: QCcalc Team

After a service restart the runner's in-memory ``_processes`` table is empty,
but the workflow subprocess (and its ORCA/xTB children, which share its
process group) may still be alive.  This module discovers them through
``/proc`` — matching the task work directory against each process' command
line and working directory — and terminates whole process groups with the
SIGTERM → wait → SIGKILL handshake.  Zombie entries are never treated as
live: a reaped-but-not-yet-joined child must not block a rerun nor be
counted as an active computation.

Linux/WSL only: when ``/proc`` is unavailable every lookup returns empty
and termination falls back to the recorded PID alone.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "find_task_processes",
    "pid_is_alive",
    "pid_is_zombie",
    "process_references",
    "read_cmdline",
    "read_cwd",
    "terminate_process",
    "terminate_task_processes",
]

_PROC = Path("/proc")
_DEFAULT_TERM_TIMEOUT = 8.0
_DEFAULT_KILL_TIMEOUT = 5.0
# Path characters: a needle may not be preceded by any of these — otherwise
# ``/…/X`` would match inside ``/…/YX``-style longer path segments.
_PATH_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/")
# Allowed characters immediately after a matched needle: subpath separator,
# argv separator, quoting, punctuation, or end-of-string.
_BOUNDARY_AFTER = frozenset("/\"'),")


def _proc_dir(pid: int) -> Path:
    return _PROC / str(pid)


def read_cmdline(pid: int) -> str:
    """Return the NUL-separated ``/proc/<pid>/cmdline`` as a spaced string."""
    try:
        raw = (_proc_dir(pid) / "cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


def read_cwd(pid: int) -> str:
    """Return the resolved ``/proc/<pid>/cwd`` symlink target ("" when unreadable)."""
    try:
        return os.readlink(_proc_dir(pid) / "cwd")
    except OSError:
        return ""


def pid_is_zombie(pid: int) -> bool:
    """True when ``/proc/<pid>/stat`` reports state ``Z`` (exited, unreaped)."""
    try:
        data = (_proc_dir(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # The comm field may contain spaces and parentheses; the state char
    # follows the final ')' as the first whitespace-separated token.
    try:
        return data.rsplit(")", 1)[1].split()[0] == "Z"
    except (IndexError, ValueError):
        return False


def pid_is_alive(pid: int) -> bool:
    """True when *pid* exists and is not a zombie.

    Zombies are deliberately reported as dead: their process image is gone
    and only the exit status awaits reaping, so they neither compute nor
    hold task resources.
    """
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists but owned by another user — still "alive"
    except OSError:
        return False
    return not pid_is_zombie(pid)


def _cmdline_contains_path(cmdline: str, needle: str) -> bool:
    """Boundary-safe *needle*-in-*cmdline* path test.

    After the needle the next character must be ``/``, whitespace, a quote,
    ``)``, ``,`` or the end of the string; the character before the needle
    must not be a path character (``[A-Za-z0-9._-/]``).  A bare substring
    test lets ``/…/X`` match ``/…/X__02``'s argv, so a rerun/kill of task
    ``X`` SIGTERMed the running sibling task (observed signal -15).
    """
    if not needle or not cmdline:
        return False
    start = 0
    while True:
        idx = cmdline.find(needle, start)
        if idx < 0:
            return False
        before = cmdline[idx - 1] if idx else ""
        after = cmdline[idx + len(needle) :]
        if before not in _PATH_CHARS and (
            not after or after[0].isspace() or after[0] in _BOUNDARY_AFTER
        ):
            return True
        start = idx + 1


def process_references(pid: int, work_dir: Path) -> bool:
    """True when *pid*'s cmdline or working directory points into *work_dir*.

    Workflow subprocesses are started with ``cwd=work_dir`` and ORCA children
    inherit it, so the working-directory probe is the reliable anchor; the
    command-line probe covers helpers that chdir away but still carry the
    task path in argv.  The cmdline probe is boundary-safe (see
    :func:`_cmdline_contains_path`) so a shorter task directory never
    matches a longer same-prefix sibling's argv.
    """
    try:
        target = work_dir.resolve()
    except OSError:
        target = work_dir
    needle = target.as_posix()
    if not needle or needle == "/":
        return False
    cwd = read_cwd(pid)
    if cwd and (cwd == needle or cwd.startswith(needle + "/")):
        return True
    cmdline = read_cmdline(pid)
    return _cmdline_contains_path(cmdline, needle)


def find_task_processes(
    work_dir: Path | str,
    exclude_pids: frozenset[int] | set[int] | None = None,
) -> list[int]:
    """Scan ``/proc`` for live, non-zombie processes bound to *work_dir*.

    The current process is always excluded; pass extra *exclude_pids* for
    known-unrelated PIDs (e.g. the runner-tracked subprocess that the caller
    handles through :class:`subprocess.Popen` instead).
    """
    if not _PROC.is_dir():
        return []
    excluded = {os.getpid(), *(exclude_pids or set())}
    found: list[int] = []
    try:
        entries = list(_PROC.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in excluded:
            continue
        if pid_is_zombie(pid):
            continue
        if process_references(pid, Path(work_dir)):
            found.append(pid)
    return sorted(found)


def _wait_gone(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_is_alive(pid)


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        logger.debug("killpg(%s, %s) failed", pgid, sig, exc_info=True)


def terminate_process(pid: int, timeout: float = _DEFAULT_TERM_TIMEOUT) -> bool:
    """Terminate *pid* and its process group: SIGCONT → SIGTERM → wait → SIGKILL.

    The defensive SIGCONT revives a SIGSTOP-frozen group (a paused job
    orphaned by a restart cannot act on SIGTERM while stopped).  Killing the
    whole process group takes down ORCA/xTB children that share it.

    Returns:
        True when the process is gone (or was already dead/zombie).
    """
    if not pid_is_alive(pid):
        return True
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        pgid = pid
    except OSError:
        return _wait_gone(pid, 1.0)
    # Never signal our own process group — a reparented stray whose pgid
    # equals ours is killed individually instead.
    group = pgid if pgid != os.getpgrp() else None
    if group is not None:
        _signal_group(group, signal.SIGCONT)
        _signal_group(group, signal.SIGTERM)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if _wait_gone(pid, timeout):
        return True
    if group is not None:
        _signal_group(group, signal.SIGKILL)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    gone = _wait_gone(pid, _DEFAULT_KILL_TIMEOUT)
    if not gone:
        logger.warning("Process %s (pgid=%s) survived SIGKILL wait window", pid, pgid)
    return gone


def terminate_task_processes(
    work_dir: Path | str,
    extra_pids: list[int] | tuple[int, ...] = (),
    timeout: float = _DEFAULT_TERM_TIMEOUT,
) -> list[int]:
    """Find and terminate every live process bound to *work_dir*.

    Args:
        work_dir: Task directory whose processes must die.
        extra_pids: Recorded PIDs (e.g. ``record.pid``) to terminate as well
            when they are still alive *and* actually reference the task
            directory — PID recycling must never cause collateral kills.
        timeout: Seconds to wait after SIGTERM before escalating to SIGKILL.

    Returns:
        The PIDs that were signalled (successfully gone afterwards, except
        for processes that survive even SIGKILL — logged as warnings).
    """
    directory = Path(work_dir)
    referenced = set(find_task_processes(directory))
    for pid in extra_pids:
        if pid and pid > 0 and pid not in referenced and pid_is_alive(pid):
            if process_references(pid, directory):
                referenced.add(pid)
    killed: list[int] = []
    for pid in sorted(referenced):
        if terminate_process(pid, timeout=timeout):
            killed.append(pid)
        else:
            logger.warning("Could not terminate task process %s for %s", pid, directory)
    return killed
