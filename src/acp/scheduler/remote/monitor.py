"""
Remote Job Monitor
==================

Monitors remote LSF (OpenLAVA) jobs via SSH ``bjobs`` queries and SFTP
log tailing.  Status detection relies primarily on ``bjobs`` state codes
and the ``.exit_code`` sentinel file written by the LSF script — it does
**not** depend on ``state.json``.

LSF state mapping follows :class:`LSFClusterAdapter.get_status`:

    PEND → pending, RUN → running, DONE → done, EXIT → failed,
    UNKWN → unknown, (empty / not found) → not_found

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import posixpath
import shlex
from typing import Final

from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError

logger = logging.getLogger(__name__)

__all__ = ["RemoteJobMonitor"]

# Normalised status strings (lowercase, workflow-agnostic).
STATUS_PENDING: Final[str] = "pending"
STATUS_RUNNING: Final[str] = "running"
STATUS_DONE: Final[str] = "done"
STATUS_FAILED: Final[str] = "failed"
STATUS_UNKNOWN: Final[str] = "unknown"
STATUS_NOT_FOUND: Final[str] = "not_found"

# Map raw LSF stat codes to our normalised strings.
_LSF_STATE_MAP: dict[str, str] = {
    "PEND": STATUS_PENDING,
    "RUN": STATUS_RUNNING,
    "DONE": STATUS_DONE,
    "EXIT": STATUS_FAILED,
    "UNKWN": STATUS_UNKNOWN,
}

# Terminal LSF states (job will not change further).
_TERMINAL_LSF_STATUSES = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_NOT_FOUND})


class RemoteJobMonitor:
    """SSH/SFTP-based monitor for remote LSF jobs.

    Bound to a :class:`SSHConnectionPool` and :class:`FileStager`.  Each
    method borrows a connection transiently, so the monitor is safe to
    share across worker threads.
    """

    def __init__(self, ssh_pool: SSHConnectionPool, stager: FileStager) -> None:
        self._ssh = ssh_pool
        self._stager = stager

    # ------------------------------------------------------------------ #
    # LSF status
    # ------------------------------------------------------------------ #

    def get_lsf_status(self, node: RemoteNode, lsf_job_id: str) -> str:
        """Query ``bjobs`` and return a normalised status string.

        Returns one of :data:`STATUS_PENDING`, :data:`STATUS_RUNNING`,
        :data:`STATUS_DONE`, :data:`STATUS_FAILED`, :data:`STATUS_UNKNOWN`,
        or :data:`STATUS_NOT_FOUND` (when the job has been purged from the
        LSF queue, typically after completion).

        Uses plain ``bjobs <id>`` for compatibility with both IBM LSF and
        OpenLAVA 2.0 (which does not support ``-o`` / ``-noheader``).
        """
        cmd = f"bjobs {shlex.quote(lsf_job_id)} 2>&1"
        try:
            _code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError:
            logger.warning("bjobs query failed for job %s on %s", lsf_job_id, node.name)
            return STATUS_UNKNOWN

        raw = out.strip()
        if not raw:
            return STATUS_NOT_FOUND

        lower = raw.lower()
        # "Job <1234> is not found" (OpenLAVA) or "not submitted"
        if "not found" in lower or "not submitted" in lower or "no unfinished" in lower:
            return STATUS_NOT_FOUND

        # Parse the table: find the line whose first column matches the job ID,
        # then extract the STAT column (3rd column).  OpenLAVA prints a table
        # header followed by one data line per job (which may be followed by
        # continuation lines listing multi-host execution hosts).
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == lsf_job_id:
                stat = parts[2].upper()
                mapped = _LSF_STATE_MAP.get(stat)
                if mapped is not None:
                    return mapped
                break

        # Fallback: scan for any recognised LSF stat code in the output.
        for stat_code, mapped in _LSF_STATE_MAP.items():
            if stat_code in raw.upper():
                return mapped

        logger.debug("Unrecognised bjobs output for %s: %r", lsf_job_id, raw)
        return STATUS_UNKNOWN

    @staticmethod
    def is_terminal(status: str) -> bool:
        """True for LSF states that will not transition further."""
        return status in _TERMINAL_LSF_STATUSES

    # ------------------------------------------------------------------ #
    # Exit code
    # ------------------------------------------------------------------ #

    def get_exit_code(self, node: RemoteNode, remote_job_dir: str) -> int | None:
        """Read ``.exit_code`` from the remote job directory.

        Returns the integer exit code, or ``None`` if the file does not
        exist yet (job still running or not yet finished writing).
        """
        path = posixpath.join(remote_job_dir, ".exit_code")
        try:
            content = self._stager.read_remote_text(node, path)
        except FileNotFoundError:
            return None
        except OSError:
            return None
        except Exception:
            logger.debug("Failed reading .exit_code on %s", node.name, exc_info=True)
            return None

        text = content.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            logger.warning("Invalid .exit_code content on %s: %r", node.name, text)
            return None

    # ------------------------------------------------------------------ #
    # Log tailing
    # ------------------------------------------------------------------ #

    def tail_stdout(
        self,
        node: RemoteNode,
        remote_job_dir: str,
        offset: int = 0,
    ) -> tuple[str, int]:
        """Incrementally read ``stdout.log`` from *offset*."""
        return self._tail(node, remote_job_dir, "stdout.log", offset)

    def tail_stderr(
        self,
        node: RemoteNode,
        remote_job_dir: str,
        offset: int = 0,
    ) -> tuple[str, int]:
        """Incrementally read ``stderr.log`` from *offset*."""
        return self._tail(node, remote_job_dir, "stderr.log", offset)

    def _tail(
        self,
        node: RemoteNode,
        remote_job_dir: str,
        filename: str,
        offset: int,
    ) -> tuple[str, int]:
        path = posixpath.join(remote_job_dir, filename)
        try:
            return self._stager.tail_log_text(node, path, offset=offset)
        except Exception:
            logger.debug("tail_log_text failed for %s on %s", filename, node.name, exc_info=True)
            return "", offset

    # ------------------------------------------------------------------ #
    # Capacity & disk
    # ------------------------------------------------------------------ #

    def get_running_job_count(self, node: RemoteNode) -> int:
        """Return the number of RUN-state LSF jobs for the node user.

        Uses ``bjobs -r -u <user>`` and counts lines starting with a digit
        (job IDs).  Compatible with both IBM LSF and OpenLAVA 2.0.
        """
        cmd = f"bjobs -r -u {shlex.quote(node.username)} 2>&1 | grep -c '^[0-9]'"
        try:
            _code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError:
            return 0
        try:
            return int(out.strip())
        except ValueError:
            return 0

    def check_disk_usage(self, node: RemoteNode, remote_path: str) -> int:
        """Return disk-usage percentage (0–100) for the filesystem holding *remote_path*."""
        cmd = f"df -P {shlex.quote(remote_path)} 2>/dev/null | tail -1 | awk '{{print $5}}'"
        try:
            _code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError:
            return 0
        text = out.strip().rstrip("%").strip()
        try:
            return int(text)
        except ValueError:
            return 0

    # ------------------------------------------------------------------ #
    # Cancellation
    # ------------------------------------------------------------------ #

    def cancel_job(self, node: RemoteNode, lsf_job_id: str) -> bool:
        """Send ``bkill`` and check the return value.

        Returns ``True`` if ``bkill`` exited 0, ``False`` otherwise (the
        job may have already finished).  Failures are logged but never
        raise — cancellation is best-effort.
        """
        cmd = f"bkill {lsf_job_id} 2>&1"
        try:
            code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError as exc:
            logger.warning("bkill %s failed on %s: %s (SSH error)", lsf_job_id, node.name, exc)
            return False
        if code == 0:
            logger.info("Cancelled LSF job %s on %s", lsf_job_id, node.name)
            return True
        logger.warning(
            "bkill %s returned non-zero (code=%d) on %s: %s",
            lsf_job_id,
            code,
            node.name,
            out.strip(),
        )
        return False
