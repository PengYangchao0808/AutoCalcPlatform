"""
Remote Job Monitor
==================

Monitors remote LSF (OpenLAVA) jobs via SSH ``bjobs`` queries, SFTP
log tailing, and optional ``state.json`` polling for fine-grained progress.
Status detection relies on ``bjobs`` state codes, the ``.exit_code``
sentinel file written by the LSF script, and the workflow ``state.json``.

LSF state mapping follows :class:`LSFClusterAdapter.get_status`:

    PEND → pending, RUN → running, PSUSP/SSUSP/USUSP → paused,
    DONE → done, EXIT → failed,
    UNKWN → unknown, (empty / not found) → not_found

Author: QCcalc Team
"""

from __future__ import annotations

import json
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
STATUS_PAUSED: Final[str] = "paused"
STATUS_DONE: Final[str] = "done"
STATUS_FAILED: Final[str] = "failed"
STATUS_UNKNOWN: Final[str] = "unknown"
STATUS_NOT_FOUND: Final[str] = "not_found"

# Map raw LSF stat codes to our normalised strings.  PSUSP (suspended
# while pending), SSUSP (suspended while running) and USUSP (suspended
# by the user / ``bstop``) all map to the non-terminal paused status.
_LSF_STATE_MAP: dict[str, str] = {
    "PEND": STATUS_PENDING,
    "RUN": STATUS_RUNNING,
    "PSUSP": STATUS_PAUSED,
    "SSUSP": STATUS_PAUSED,
    "USUSP": STATUS_PAUSED,
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
        :data:`STATUS_PAUSED` (suspended via ``bstop`` / LSF scheduler),
        :data:`STATUS_DONE`, :data:`STATUS_FAILED`,
        :data:`STATUS_UNKNOWN`, or :data:`STATUS_NOT_FOUND` (when the job
        has been purged from the LSF queue, typically after completion).

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
    # Workflow state (state.json + .stage_* files)
    # ------------------------------------------------------------------ #

    def read_state_json(self, node: RemoteNode, remote_job_dir: str) -> dict | None:
        """Read ``state.json`` from the remote job directory and parse as JSON.

        Returns the decoded dict, or ``None`` if the file does not exist
        or cannot be parsed (job still in early startup or already purged).
        """
        path = posixpath.join(remote_job_dir, "state.json")
        try:
            content = self._stager.read_remote_text(node, path)
        except (FileNotFoundError, OSError):
            return None
        except Exception:
            logger.debug("Failed reading state.json on %s", node.name, exc_info=True)
            return None
        if not content.strip():
            return None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.debug("Invalid state.json on %s:%s", node.name, path)
            return None
        return data if isinstance(data, dict) else None

    def find_remote_state_json(self, node: RemoteNode, remote_job_dir: str) -> dict | None:
        """Read ``state.json`` from the remote dir, falling back to nested locations.

        Real workflows nest ``state.json`` one level under the output dir; this
        mirrors :func:`acp.scheduler.runner.find_workflow_state` logic.
        """
        data = self.read_state_json(node, remote_job_dir)
        if data is not None:
            return data
        try:
            entries = self._stager.list_remote_dir(node, remote_job_dir)
        except (OSError, PermissionError) as exc:
            logger.debug("Cannot list %s:%s: %s", node.name, remote_job_dir, exc)
            return None
        except Exception:
            return None
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_dir:
                continue
            sub_path = posixpath.join(remote_job_dir, entry.name)
            data = self.read_state_json(node, sub_path)
            if data is not None:
                return data
        return None

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
        """Send ``bkill`` to cancel a remote LSF job, with SSH ``pkill`` fallback.

        Returns ``True`` if the cancellation signal was delivered successfully
        (either ``bkill`` exited 0 or the ``pkill`` fallback succeeded),
        ``False`` otherwise.  Failures are logged but never raise —
        cancellation is best-effort.
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
            "bkill %s returned non-zero (code=%d) on %s: %s — trying pkill fallback",
            lsf_job_id,
            code,
            node.name,
            out.strip(),
        )
        return self._pkill_acp(node, lsf_job_id)

    def _pkill_acp(self, node: RemoteNode, lsf_job_id: str) -> bool:
        """SSH fallback: kill ``acp run`` processes matching a LSF job ID on the node.

        Searches for ``acp.cli run`` processes whose command line contains
        the job ID and sends SIGTERM followed by SIGKILL to their process groups.
        """
        # Find PIDs of acp run processes tied to this job ID.
        find_cmd = shlex.quote(
            f"pgrep -f 'acp.cli run.*{lsf_job_id}'"
        )
        try:
            code, out, _err = self._ssh.execute(
                node, f"{find_cmd} 2>/dev/null || true", timeout=15
            )
        except SSHExecutionError as exc:
            logger.warning("pkill fallback SSH error on %s: %s", node.name, exc)
            return False

        if code != 0 or not out.strip():
            logger.info("No matching acp processes found for job %s on %s", lsf_job_id, node.name)
            return False

        pids = out.strip().split()
        logger.info("Killing %d acp processes for job %s on %s", len(pids), lsf_job_id, node.name)
        kill_cmd = shlex.quote(f"kill -TERM {' '.join(pids)} 2>/dev/null; sleep 5; "
                               f"kill -KILL {' '.join(pids)} 2>/dev/null || true")
        try:
            code, out2, _err = self._ssh.execute(node, kill_cmd, timeout=15)
            if code == 0:
                logger.info("pkill fallback succeeded for job %s on %s", lsf_job_id, node.name)
                return True
            logger.warning("pkill fallback exited %d for job %s on %s: %s",
                           code, lsf_job_id, node.name, out2.strip())
        except SSHExecutionError as exc:
            logger.warning("pkill fallback SSH error on %s: %s", node.name, exc)
        return False

    # ------------------------------------------------------------------ #
    # Suspension (pause / resume)
    # ------------------------------------------------------------------ #

    def bstop_job(self, node: RemoteNode, lsf_job_id: str) -> bool:
        """Send ``bstop`` to suspend a remote LSF job.

        Returns ``True`` if the suspension signal was delivered
        successfully, ``False`` otherwise.  Failures are logged but
        never raise — suspension is best-effort, like cancellation.
        """
        cmd = f"bstop {lsf_job_id} 2>&1"
        try:
            code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError as exc:
            logger.warning("bstop %s failed on %s: %s (SSH error)", lsf_job_id, node.name, exc)
            return False
        if code == 0:
            logger.info("Suspended LSF job %s on %s", lsf_job_id, node.name)
            return True

        logger.warning(
            "bstop %s returned non-zero (code=%d) on %s: %s",
            lsf_job_id,
            code,
            node.name,
            out.strip(),
        )
        return False

    def bresume_job(self, node: RemoteNode, lsf_job_id: str) -> bool:
        """Send ``bresume`` to resume a suspended remote LSF job.

        Returns ``True`` if the resume signal was delivered successfully,
        ``False`` otherwise.  Failures are logged but never raise.
        """
        cmd = f"bresume {lsf_job_id} 2>&1"
        try:
            code, out, _err = self._ssh.execute(node, cmd, timeout=30)
        except SSHExecutionError as exc:
            logger.warning("bresume %s failed on %s: %s (SSH error)", lsf_job_id, node.name, exc)
            return False
        if code == 0:
            logger.info("Resumed LSF job %s on %s", lsf_job_id, node.name)
            return True

        logger.warning(
            "bresume %s returned non-zero (code=%d) on %s: %s",
            lsf_job_id,
            code,
            node.name,
            out.strip(),
        )
        return False
