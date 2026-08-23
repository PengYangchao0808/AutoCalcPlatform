"""
Remote File Lifecycle Management
================================

Retention-based cleanup of remote job directories and disk-pressure
housekeeping triggered before each job submission.

* :class:`RemoteCleanup` scans a node's ``remote_work_dir`` for job
  subdirectories older than ``retention_days`` and removes them
  recursively.
* :meth:`RemoteCleanup.pre_submit_housekeeping` is invoked by the
  :class:`~acp.scheduler.remote.runner.RemoteJobRunner` before every
  submission: when disk usage crosses the *cleanup* threshold (default
  90 %) it triggers a retention sweep; when it still exceeds the *skip*
  threshold (default 95 %) after the sweep the node is rejected so the
  runner can fail fast or pick another node.

Only **top-level directories** under ``remote_work_dir`` are managed.
Stray files at the top level are left untouched, and the ``remote_work_dir``
itself (plus ancestors such as ``/`` or ``/scratch``) is never removed.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import posixpath
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool

logger = logging.getLogger(__name__)

__all__ = [
    "CleanupReport",
    "DEFAULT_MAX_DIRS_PER_SWEEP",
    "DISK_CLEANUP_THRESHOLD",
    "DISK_SKIP_THRESHOLD",
    "HousekeepingDecision",
    "RemoteCleanup",
]

# Disk-usage thresholds (percent of the filesystem holding remote_work_dir).
# Above CLEANUP we run a retention sweep; above SKIP (even after sweep) we
# reject the node for submission.
DISK_CLEANUP_THRESHOLD = 90
DISK_SKIP_THRESHOLD = 95


# Cap on the number of directories removed in a single sweep.  Each dir
# costs two SSH round-trips (du + rm); without a cap a node with tens of
# thousands of expired job dirs would block submission for many minutes.
# Leftover dirs are cleaned on the next submission that triggers housekeeping.
DEFAULT_MAX_DIRS_PER_SWEEP = 100


@dataclass
class CleanupReport:
    """Outcome of a single retention sweep on one node.

    Attributes:
        node: Node name the sweep ran against.
        retention_days: Cut-off age (in days) used for the sweep.
        removed_dirs: Absolute remote paths that were (or would be) removed.
        skipped: Count of candidate dirs left alone (unknown mtime, etc.).
        errors: Human-readable error strings (per dir or global).
        freed_bytes_est: Estimated bytes reclaimed (sum of ``du -sb`` output).
            ``0`` means no measurement was taken (dry-run or du failure).
        dry_run: Whether this was a non-mutating dry run.
    """

    node: str
    retention_days: int
    removed_dirs: list[str] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    freed_bytes_est: int = 0
    dry_run: bool = False
    capped: bool = False

    @property
    def ok(self) -> bool:
        """True when no errors were recorded during the sweep."""
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "node": self.node,
            "retention_days": self.retention_days,
            "removed_dirs": list(self.removed_dirs),
            "skipped": self.skipped,
            "errors": list(self.errors),
            "freed_bytes_est": self.freed_bytes_est,
            "dry_run": self.dry_run,
            "capped": self.capped,
            "ok": self.ok,
        }


@dataclass
class HousekeepingDecision:
    """Result of :meth:`RemoteCleanup.pre_submit_housekeeping`.

    Attributes:
        node: Node name the check ran against.
        should_skip: When True the node is too full and must be rejected.
        disk_usage_before: Disk-usage percent before any cleanup.
        disk_usage_after: Disk-usage percent after cleanup (== before if
            no sweep was triggered).
        cleanup: The :class:`CleanupReport` if a sweep ran, else ``None``.
        reason: Short human-readable explanation of the decision.
    """

    node: str
    should_skip: bool
    disk_usage_before: int
    disk_usage_after: int
    cleanup: CleanupReport | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "node": self.node,
            "should_skip": self.should_skip,
            "disk_usage_before": self.disk_usage_before,
            "disk_usage_after": self.disk_usage_after,
            "cleanup": self.cleanup.to_dict() if self.cleanup else None,
            "reason": self.reason,
        }


class RemoteCleanup:
    """Retention-based cleanup + pre-submit disk-pressure housekeeping.

    Bound to the same :class:`SSHConnectionPool` + :class:`FileStager` as
    the :class:`~acp.scheduler.remote.runner.RemoteJobRunner` so no extra
    connections are opened.  Thread-safe via the underlying pool.
    """

    def __init__(
        self,
        ssh_pool: SSHConnectionPool,
        stager: FileStager,
        remote_config: RemoteExecutionConfig,
        monitor: RemoteJobMonitor | None = None,
        cleanup_threshold: int = DISK_CLEANUP_THRESHOLD,
        skip_threshold: int = DISK_SKIP_THRESHOLD,
    ) -> None:
        self._ssh = ssh_pool
        self._stager = stager
        self._config = remote_config
        self._monitor = monitor or RemoteJobMonitor(ssh_pool, stager)
        if cleanup_threshold > skip_threshold:
            raise ValueError(
                f"cleanup_threshold ({cleanup_threshold}) must not exceed "
                f"skip_threshold ({skip_threshold})"
            )
        self._cleanup_threshold = cleanup_threshold
        self._skip_threshold = skip_threshold

    # ------------------------------------------------------------------ #
    # Retention sweep
    # ------------------------------------------------------------------ #

    def cleanup_old_jobs(
        self,
        node: RemoteNode,
        retention_days: int | None = None,
        dry_run: bool = False,
        max_dirs_per_sweep: int = DEFAULT_MAX_DIRS_PER_SWEEP,
    ) -> CleanupReport:
        """Remove job directories under ``node.remote_work_dir`` older than retention.

        Scans the top-level entries of ``remote_work_dir``.  Any **directory**
        whose mtime is older than ``retention_days`` days is removed
        recursively (``rm -rf`` over SSH).  Files at the top level are left
        alone — only job subdirectories are managed.  The ``remote_work_dir``
        itself, its ancestors, and unsafe shallow paths are never removed.

        Args:
            node: Target remote node.
            retention_days: Override for
                :attr:`RemoteExecutionConfig.retention_days`.  If ``None``
                or ``<= 0``, the configured value is used.
            dry_run: When ``True``, populate the report without deleting.
            max_dirs_per_sweep: Cap on the number of directories removed in
                this call (each costs two SSH round-trips: ``du`` + ``rm``).
                ``<= 0`` means unlimited.  Leftover dirs are cleaned on the
                next submission that triggers housekeeping.

        Returns:
            A :class:`CleanupReport` describing what was (or would be)
            removed.  Errors are recorded per-directory rather than raised.
        """
        if retention_days is None or retention_days <= 0:
            retention_days = self._config.retention_days

        report = CleanupReport(node=node.name, retention_days=retention_days, dry_run=dry_run)

        base = node.remote_work_dir
        if not _is_safe_work_dir(base):
            report.errors.append(f"unsafe remote_work_dir: {base!r}")
            logger.error("Refusing cleanup on %s: unsafe remote_work_dir %r", node.name, base)
            return report

        try:
            entries = self._stager.list_remote_dir(node, base)
        except FileNotFoundError:
            logger.debug(
                "remote_work_dir %s does not exist on %s — nothing to clean",
                base,
                node.name,
            )
            return report
        except Exception as exc:
            report.errors.append(f"list_remote_dir failed: {exc}")
            logger.warning("Cleanup listing failed on %s:%s: %s", node.name, base, exc)
            return report

        cutoff = time.time() - retention_days * 86400
        norm_base = posixpath.normpath(base)

        for entry in entries:
            # Cap the sweep so a huge backlog cannot block submission for
            # many minutes (each dir = 2 SSH round-trips).  Leftover dirs
            # are revisited on the next housekeeping pass.
            if max_dirs_per_sweep > 0 and len(report.removed_dirs) >= max_dirs_per_sweep:
                report.capped = True
                logger.info(
                    "Cleanup on %s hit max_dirs_per_sweep=%d; remaining old dirs "
                    "deferred to next pass",
                    node.name,
                    max_dirs_per_sweep,
                )
                break

            if not entry.is_dir:
                continue
            if entry.mtime <= 0:
                # Unknown mtime — leave alone (safer).
                report.skipped += 1
                continue
            if entry.mtime > cutoff:
                continue  # fresh enough

            target = posixpath.join(base, entry.name)
            # Defense in depth: never delete the base dir itself.
            if posixpath.normpath(target) == norm_base:
                continue

            if dry_run:
                report.removed_dirs.append(target)
                report.freed_bytes_est += self._dir_size_bytes(node, target)
                continue

            # Measure size BEFORE deletion (du -sb needs the dir to exist).
            freed = self._dir_size_bytes(node, target)
            try:
                self._stager.remove_remote_dir(node, target)
            except Exception as exc:
                report.errors.append(f"{target}: {exc}")
                logger.warning("Cleanup failed to remove %s:%s: %s", node.name, target, exc)
                continue

            report.removed_dirs.append(target)
            report.freed_bytes_est += freed
            logger.info(
                "Cleanup: removed %s:%s (mtime=%d, age>=%dd)",
                node.name,
                target,
                int(entry.mtime),
                retention_days,
            )

        if report.removed_dirs:
            cap_note = " (capped)" if report.capped else ""
            mode = "dry-run" if dry_run else "reclaimed " + _format_bytes(report.freed_bytes_est)
            logger.info(
                "Cleanup on %s %s%s: removed %d dir(s), %d skipped, %d error(s)",
                node.name,
                mode,
                cap_note,
                len(report.removed_dirs),
                report.skipped,
                len(report.errors),
            )
        return report

    # ------------------------------------------------------------------ #
    # Pre-submit housekeeping
    # ------------------------------------------------------------------ #

    def pre_submit_housekeeping(self, node: RemoteNode) -> HousekeepingDecision:
        """Inspect disk pressure and act before submitting a job.

        Policy:

        * usage <= cleanup_threshold → proceed (no action).
        * cleanup_threshold < usage → run :meth:`cleanup_old_jobs`, re-check.
        * usage > skip_threshold (after cleanup, or if cleanup failed) →
          the node is rejected (``should_skip=True``).

        Failures while querying disk usage are treated as 0 % (fail-open) so
        a transient SSH hiccup does not block submission.  Failures *inside*
        the sweep are recorded in the report but do not abort housekeeping.

        Returns:
            A :class:`HousekeepingDecision`.  When ``should_skip`` is True
            the caller should reject the node.
        """
        before = self._safe_disk_usage(node)
        cleanup: CleanupReport | None = None

        if before > self._cleanup_threshold:
            logger.info(
                "Disk usage on %s is %d%% (> %d%%) — triggering retention cleanup",
                node.name,
                before,
                self._cleanup_threshold,
            )
            try:
                cleanup = self.cleanup_old_jobs(node)
            except Exception as exc:
                # Defensive: cleanup_old_jobs records per-dir errors but
                # should never raise.  If it does, capture and continue.
                logger.warning("Retention cleanup on %s raised: %s", node.name, exc)
                cleanup = CleanupReport(
                    node=node.name,
                    retention_days=self._config.retention_days,
                    errors=[f"cleanup raised: {exc}"],
                )
            after = self._safe_disk_usage(node)
        else:
            after = before

        # If the after-probe failed (returned 0) but we *knew* the disk was
        # under pressure (before > cleanup_threshold), be conservative: we
        # cannot confirm cleanup freed enough space, so assume it did not.
        # This prevents a flaky after-probe from masking a genuinely full
        # disk (P1 fix).
        if after <= 0 and before > self._cleanup_threshold:
            after = before

        if after > self._skip_threshold:
            suffix = f" (cleanup: before={before}%, after={after}%)" if cleanup else ""
            return HousekeepingDecision(
                node=node.name,
                should_skip=True,
                disk_usage_before=before,
                disk_usage_after=after,
                cleanup=cleanup,
                reason=f"disk usage {after}% exceeds skip threshold "
                f"{self._skip_threshold}%{suffix}",
            )

        if cleanup is not None:
            return HousekeepingDecision(
                node=node.name,
                should_skip=False,
                disk_usage_before=before,
                disk_usage_after=after,
                cleanup=cleanup,
                reason=f"ok after cleanup (before={before}%, after={after}%)",
            )

        return HousekeepingDecision(
            node=node.name,
            should_skip=False,
            disk_usage_before=before,
            disk_usage_after=after,
            cleanup=None,
            reason="ok (no cleanup needed)",
        )

    def delete_job_dirs(self, job_id: str, dir_names: list[str] | None = None) -> dict[str, Any]:
        """Remove the remote working directory for a single job from every node.

        *dir_names* lists the remote directory leaves to remove (v2
        ``task_dir_name`` for new jobs, plus the legacy ``job_id`` leaf for
        pre-migration jobs); when omitted only the legacy ``job_id`` leaf
        is removed.
        """
        leaves = [job_id] + (dir_names or [])
        return self.delete_project_dirs(project_id="job", job_ids=leaves)

    def delete_project_dirs(
        self,
        project_id: str,
        job_ids: list[str],
    ) -> dict[str, Any]:
        """Remove remote working directories for all jobs of a project.

        Iterates every configured node and attempts ``rm -rf`` on
        ``node.remote_work_dir / job_id`` for each supplied job id.
        Missing directories are ignored.  Errors are collected per node
        rather than raised so one unreachable node does not abort the
        whole operation.

        Returns:
            A dict with ``nodes`` (list of per-node reports) and a summary
            ``removed_count``.
        """
        report: dict[str, Any] = {"nodes": [], "removed_count": 0}
        for node in self._config.nodes:
            node_report: dict[str, Any] = {
                "node": node.name,
                "removed_dirs": [],
                "errors": [],
            }
            base = node.remote_work_dir
            if not _is_safe_work_dir(base):
                node_report["errors"].append(f"unsafe remote_work_dir: {base!r}")
                report["nodes"].append(node_report)
                continue
            for job_id in job_ids:
                target = posixpath.join(base, job_id)
                norm_target = posixpath.normpath(target)
                if norm_target == posixpath.normpath(base):
                    node_report["errors"].append(f"refusing to remove base dir: {target}")
                    continue
                try:
                    self._stager.remove_remote_dir(node, target)
                    node_report["removed_dirs"].append(target)
                    report["removed_count"] += 1
                except FileNotFoundError:
                    # Directory did not exist — this is expected for jobs
                    # that were never run remotely or already cleaned.
                    pass
                except Exception as exc:
                    err = f"{target}: {exc}"
                    node_report["errors"].append(err)
                    logger.warning("Project cleanup failed on %s:%s: %s", node.name, target, exc)
            if node_report["removed_dirs"] or node_report["errors"]:
                report["nodes"].append(node_report)
        return report

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _safe_disk_usage(self, node: RemoteNode) -> int:
        """Disk-usage percent for the node's work filesystem; never raises.

        Returns ``0`` on any failure so a transient SSH error fails open
        (submission proceeds) rather than spuriously rejecting the node.
        """
        try:
            return self._monitor.check_disk_usage(node, node.remote_work_dir)
        except Exception:
            logger.debug("disk usage query failed on %s", node.name, exc_info=True)
            return 0

    def _dir_size_bytes(self, node: RemoteNode, remote_path: str) -> int:
        """Estimate the recursive size of *remote_path* via ``du -sb``.

        Returns ``0`` if ``du`` is unavailable, exits non-zero, or the path
        no longer exists (e.g. already removed).  GNU coreutils ``-b``
        reports bytes; on systems without ``-b`` the parse falls back to ``0``.
        """
        cmd = f"du -sb {shlex.quote(remote_path)} 2>/dev/null"
        try:
            code, out, _err = self._ssh.execute(node, cmd, timeout=60)
        except Exception:
            logger.debug("du -sb SSH failed on %s:%s", node.name, remote_path, exc_info=True)
            return 0
        if code != 0:
            # du failed (path gone, permission, etc.) — stderr redirected
            # to /dev/null, so stdout is typically empty.
            return 0
        text = out.strip()
        if not text:
            return 0
        # Output format: "<bytes>\t<path>"
        first = text.split(None, 1)[0]
        try:
            value = int(first)
        except ValueError:
            return 0
        return max(value, 0)


def _is_safe_work_dir(path: str) -> bool:
    """Reject ``remote_work_dir`` values that must never be recursively deleted.

    Blocks root, home, relative paths, and overly shallow absolute paths
    (e.g. ``/scratch``) as a defense-in-depth against misconfiguration.
    """
    if not path:
        return False
    norm = posixpath.normpath(path)
    if norm in ("", "/", ".", "..", "~"):
        return False
    if not posixpath.isabs(norm):
        return False
    parts = [p for p in norm.split("/") if p]
    # Require at least 2 path components (e.g. /scratch/acp_jobs).
    if len(parts) < 2:
        return False
    return True


def _format_bytes(n: int) -> str:
    """Human-readable byte count (binary units)."""
    if n <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    size = float(n)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"
