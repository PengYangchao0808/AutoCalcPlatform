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
from typing import TYPE_CHECKING, Any

from acp.scheduler.jobs import is_deletion_eligible
from acp.scheduler.local_cleanup import _iso_to_epoch
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import RemoteJobMonitor
from acp.scheduler.remote.release import prune_releases
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool

if TYPE_CHECKING:  # pragma: no cover - typing only
    from acp.scheduler.store import JobStore

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


@dataclass(frozen=True)
class _RetentionCandidate:
    """One task-leaf directory proposed for reclamation (D01 nested layout).

    Attributes:
        target: Joined absolute remote path (``<base>/<rel>`` or the
            legacy flat ``<base>/<task leaf>``).
        leaf: Final path component (used for the ``.trash`` isolation name).
        job_id: DB row id (for the pre-delete requeue re-check).
        attempt: ``JobRecord.attempt`` at snapshot time.
        status / submit_state / cancel_state: lifecycle gate inputs.
        completed_at: ISO-8601 completion timestamp (age anchor).
        mtime: Directory mtime from the node listing (auxiliary age).
    """

    target: str
    leaf: str
    job_id: str
    attempt: int | None
    status: str | None
    submit_state: str | None
    cancel_state: str | None
    completed_at: str | None
    mtime: float


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
        job_store: JobStore | None = None,
    ) -> None:
        self._ssh = ssh_pool
        self._stager = stager
        self._config = remote_config
        self._monitor = monitor or RemoteJobMonitor(ssh_pool, stager)
        # DB handle for release GC (D03).  Nullable: without it the release
        # GC branch in cleanup_old_jobs is inert (fail-closed — prune never
        # runs against a missing/stale reference snapshot).
        self._job_store = job_store
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
        with_release_gc: bool = True,
    ) -> CleanupReport:
        """Reclaim expired TASK-LEAF job directories under ``node.remote_work_dir``.

        D01 nested layout (Oracle N2): deletion units are the persisted
        ``<project_leaf>/<task_leaf>`` mappings
        (``result["remote"]["relative"]``) — never a project leaf, never a
        whole project.  The legacy flat branch is kept: pre-D01 task leaves
        that sit directly under ``remote_work_dir`` are matched by
        ``record.id`` / ``spec.task_dir_name()`` / ``work_dir`` name.

        Deletion eligibility is DB-lifecycle driven (:func:
        `acp.scheduler.jobs.is_deletion_eligible`: terminal AND
        ``submit_state ∉ {intent, unconfirmed}`` AND cancellation
        confirmed).  mtime is auxiliary only (age), never a deletion
        qualifier — an over-aged dir whose RUNNING/PAUSED job keeps
        appending logs is kept (r16 P1).  Directories with no DB row are
        never touched (no lifecycle evidence).

        Eligibility and the isolation action share one attempt check: the
        row is re-read right before isolating to
        ``<base>/.trash/<leaf>.<ts>`` — if the job moved to a new attempt
        or became non-terminal (in-place requeue/continue landed during
        the sweep) the deletion is cancelled.  The joined target is
        validated with containment + :func:`_is_safe_work_dir`.

        When *with_release_gc* is True AND a ``job_store`` was injected,
        also runs :func:`~acp.scheduler.remote.release.prune_releases` for
        the node (release GC needs the DB to refresh references — decoupled
        from the DB-less ``pre_submit_housekeeping`` path by option (B) of
        the D03 plan: that caller passes ``with_release_gc=False``).

        Args:
            node: Target remote node.
            retention_days: Override for
                :attr:`RemoteExecutionConfig.retention_days`.  If ``None``
                or ``<= 0``, the configured value is used.
            dry_run: When ``True``, populate the report without deleting.
            max_dirs_per_sweep: Cap on the number of directories removed in
                this call (each costs SSH round-trips: ``du`` + rename +
                ``rm``).  ``<= 0`` means unlimited.
            with_release_gc: Gate for the release GC phase (default True).

        Returns:
            A :class:`CleanupReport` describing what was (or would be)
            removed.  Errors are recorded per-directory rather than raised.
        """
        if retention_days is None or retention_days <= 0:
            retention_days = self._config.retention_days

        report = CleanupReport(node=node.name, retention_days=retention_days, dry_run=dry_run)

        if with_release_gc and self._job_store is not None:
            # Release GC first: independent of the work-dir layout, needs
            # the DB handle, and must run even when remote_work_dir is
            # missing (production reachability, D03 plan (C)).
            try:
                prune_report = prune_releases(
                    node,
                    stager=self._stager,
                    ssh=self._ssh,
                    job_store=self._job_store,
                    retention_days=retention_days,
                    dry_run=dry_run,
                )
                if prune_report.pruned:
                    logger.info(
                        "Release GC on %s reclaimed %d release(s): %s",
                        node.name,
                        len(prune_report.pruned),
                        ", ".join(prune_report.pruned),
                    )
                for err in prune_report.errors:
                    report.errors.append(f"release gc: {err}")
            except Exception as exc:
                report.errors.append(f"release gc: {exc}")
                logger.warning("Release GC failed on %s: %s", node.name, exc)

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

        candidates = self._retention_candidates(node, base, entries, report)
        cutoff = time.time() - retention_days * 86400
        norm_base = posixpath.normpath(base)
        base_prefix = norm_base.rstrip("/") + "/"

        # Unmapped top-level dirs (no DB row, or project leaves handled via
        # their task candidates) are counted as skipped and NEVER deleted.
        flat_names = {posixpath.basename(c.target) for c in candidates}
        project_leaves = {
            posixpath.basename(posixpath.dirname(c.target))
            for c in candidates
            if posixpath.dirname(c.target) != norm_base
        }
        for entry in entries:
            if not entry.is_dir or entry.name.startswith("."):
                continue
            if entry.name in flat_names or entry.name in project_leaves:
                continue
            report.skipped += 1

        for cand in candidates:
            if max_dirs_per_sweep > 0 and len(report.removed_dirs) >= max_dirs_per_sweep:
                report.capped = True
                logger.info(
                    "Cleanup on %s hit max_dirs_per_sweep=%d; remaining old dirs "
                    "deferred to next pass",
                    node.name,
                    max_dirs_per_sweep,
                )
                break

            # Defense in depth: containment + safety on the JOINED target.
            if posixpath.normpath(cand.target) == norm_base or not cand.target.startswith(
                base_prefix
            ):
                report.errors.append(f"refusing to remove path outside work dir: {cand.target!r}")
                continue
            if not _is_safe_work_dir(cand.target):
                report.errors.append(f"unsafe target: {cand.target!r}")
                continue

            if cand.mtime <= 0:
                # Unknown mtime — leave alone (safer).
                report.skipped += 1
                continue

            # Gate: DB lifecycle first; age only afterwards (mtime is the
            # auxiliary clock for over-aged terminal jobs, never the gate).
            if not is_deletion_eligible(cand.status, cand.submit_state, cand.cancel_state):
                logger.debug(
                    "Cleanup on %s: keeping %s (gate: status=%s submit_state=%s cancel_state=%s)",
                    node.name,
                    cand.target,
                    cand.status,
                    cand.submit_state,
                    cand.cancel_state,
                )
                report.skipped += 1
                continue

            age_ref = cand.mtime
            completed_epoch = _iso_to_epoch(cand.completed_at) if cand.completed_at else None
            if completed_epoch is not None:
                age_ref = max(completed_epoch, cand.mtime)
            if age_ref > cutoff:
                continue  # fresh enough

            # Same critical section as the check: re-read the row before
            # isolating so a concurrent in-place requeue cancels deletion.
            if not self._requeue_recheck(cand):
                logger.info(
                    "Cleanup on %s: cancelled deletion of %s — job moved to a "
                    "new attempt or became non-terminal during the sweep",
                    node.name,
                    cand.target,
                )
                report.skipped += 1
                continue

            if dry_run:
                report.removed_dirs.append(cand.target)
                report.freed_bytes_est += self._dir_size_bytes(node, cand.target)
                continue

            # Measure size BEFORE isolation (du -sb needs the dir in place).
            freed = self._dir_size_bytes(node, cand.target)
            trash = posixpath.join(base, ".trash", f"{cand.leaf}.{int(time.time())}")
            try:
                self._stager.remote_rename(node, cand.target, trash, must_not_exist=True)
            except Exception as exc:
                # Isolation failed → the dir stays in place (never deleted
                # without isolation).
                report.errors.append(f"{cand.target}: isolate failed: {exc}")
                logger.warning("Cleanup failed to isolate %s:%s: %s", node.name, cand.target, exc)
                continue
            self._stager.remove_remote_dir(node, trash)

            report.removed_dirs.append(cand.target)
            report.freed_bytes_est += freed
            logger.info(
                "Cleanup: removed %s:%s (status=%s, age>=%dd)",
                node.name,
                cand.target,
                cand.status,
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

        D03 decoupling (plan option **(B)**): this DB-less entry calls
        ``cleanup_old_jobs(..., with_release_gc=False)`` — it performs
        space checks / job-dir retention only and NEVER runs release GC
        (which requires a live DB reference refresh), so no release can be
        reclaimed from this path.

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
                cleanup = self.cleanup_old_jobs(node, with_release_gc=False)
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

        *dir_names* lists the remote directories to remove — the persisted
        full relative dir (``project_leaf/task_leaf``, may include the
        ``__NN`` dedupe suffix) or an absolute node path, plus the legacy
        ``job_id`` leaf for pre-migration jobs); when omitted only the
        legacy ``job_id`` leaf is removed.  Targets are containment-checked
        by :meth:`delete_project_dirs`.
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
        ``node.remote_work_dir / dir`` for each supplied dir.  Entries may
        be multi-component relative dirs (``leaf`` or ``project_leaf/
        task_leaf``); the joined target is validated with
        ``posixpath.normpath`` containment (must stay strictly inside the
        base — equality rejected too) and then with ``_is_safe_work_dir``.
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
            norm_base = posixpath.normpath(base)
            base_prefix = norm_base.rstrip("/") + "/"
            for job_id in job_ids:
                if posixpath.isabs(job_id):
                    target = posixpath.normpath(job_id)
                else:
                    target = posixpath.normpath(posixpath.join(base, job_id))
                if target == norm_base or not target.startswith(base_prefix):
                    node_report["errors"].append(
                        f"refusing to remove path outside work dir: {job_id!r}"
                    )
                    continue
                if not _is_safe_work_dir(target):
                    node_report["errors"].append(f"unsafe target: {target!r}")
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

    def _retention_candidates(
        self,
        node: RemoteNode,
        base: str,
        base_entries: list[Any],
        report: CleanupReport,
    ) -> list[_RetentionCandidate]:
        """Build task-leaf deletion candidates from the persisted mapping.

        Walks every DB row (``job_store.list``) and resolves its deletion
        unit:

        * ``result["remote"]["relative"]`` set → ``<base>/<rel>`` (D01
          ``<project_leaf>/<task_leaf>``, descending into the project leaf
          for the task's mtime);
        * no mapping → legacy flat branch: match ``record.id`` /
          ``spec.task_dir_name()`` / ``work_dir`` name against the
          pre-D01 task leaves directly under ``base``.

        Rows without a resolvable remote directory (local jobs, already
        purged) yield no candidate.  A target that is a strict prefix of
        another candidate's target is a project leaf — it is dropped so a
        whole project can never be a deletion unit.
        """
        if self._job_store is None:
            return []
        try:
            records = self._job_store.list(limit=500000)
        except Exception as exc:
            report.errors.append(f"retention map build failed: {exc}")
            logger.warning("Retention map build failed on %s: %s", node.name, exc)
            return []

        norm_base = posixpath.normpath(base)
        base_prefix = norm_base.rstrip("/") + "/"
        base_index = {e.name: e for e in base_entries if getattr(e, "is_dir", False)}
        project_listing_cache: dict[str, dict[str, float]] = {}
        seen: set[str] = set()
        candidates: list[_RetentionCandidate] = []

        for record in records:
            remote = (record.result or {}).get("remote")
            remote = remote if isinstance(remote, dict) else {}
            rel = remote.get("relative")
            target: str | None = None
            mtime: float | None = None

            if isinstance(rel, str) and rel:
                rel_norm = posixpath.normpath(rel)
                if posixpath.isabs(rel_norm) or rel_norm.startswith(".."):
                    report.errors.append(
                        f"refusing retention target outside work dir: {record.id} -> {rel!r}"
                    )
                    continue
                target = posixpath.normpath(posixpath.join(norm_base, rel_norm))
                if target == norm_base or not target.startswith(base_prefix):
                    report.errors.append(
                        f"refusing retention target outside work dir: {record.id} -> {rel!r}"
                    )
                    continue
                parent, leaf = posixpath.split(target)
                if parent == norm_base:
                    # Single-component relative → flat leaf, mtime from base.
                    entry = base_index.get(leaf)
                    mtime = float(entry.mtime) if entry is not None else None
                else:
                    mtime = self._nested_leaf_mtime(node, parent, leaf, project_listing_cache)
            else:
                # Legacy flat branch (pre-D01 leaves directly under base).
                names = {record.id, record.spec.task_dir_name()}
                if record.work_dir:
                    names.add(posixpath.basename(record.work_dir.rstrip("/")))
                for name in names:
                    entry = base_index.get(name or "")
                    if entry is not None:
                        target = posixpath.normpath(posixpath.join(norm_base, name))
                        mtime = float(entry.mtime)
                        break
                if target is None:
                    continue

            if target in seen:
                continue
            seen.add(target)
            candidates.append(
                _RetentionCandidate(
                    target=target,
                    leaf=posixpath.basename(target),
                    job_id=record.id,
                    attempt=record.attempt,
                    status=record.status.value if record.status else None,
                    submit_state=remote.get("submit_state"),
                    cancel_state=remote.get("cancel_state"),
                    completed_at=record.completed_at,
                    mtime=mtime if mtime is not None else 0.0,
                )
            )

        # Never delete a project leaf: drop any candidate that STRICTLY
        # CONTAINS another candidate's target (the container would take
        # sibling tasks down with it).
        containers = {
            a.target
            for a in candidates
            for c in candidates
            if c.target != a.target and c.target.startswith(a.target + "/")
        }
        if containers:
            for target in sorted(containers):
                logger.info(
                    "Cleanup on %s: refusing project-leaf deletion unit %s",
                    node.name,
                    target,
                )
            candidates = [c for c in candidates if c.target not in containers]
        return candidates

    def _nested_leaf_mtime(
        self,
        node: RemoteNode,
        project_dir: str,
        leaf: str,
        cache: dict[str, dict[str, float]],
    ) -> float:
        """mtime of ``<project_dir>/<leaf>`` via one listing per project."""
        if project_dir not in cache:
            try:
                children = self._stager.list_remote_dir(node, project_dir)
            except FileNotFoundError:
                children = []
            except Exception as exc:
                logger.debug("nested listing failed on %s:%s: %s", node.name, project_dir, exc)
                children = []
            cache[project_dir] = {
                e.name: float(e.mtime) for e in children if getattr(e, "is_dir", False)
            }
        return cache[project_dir].get(leaf, 0.0)

    def _requeue_recheck(self, cand: _RetentionCandidate) -> bool:
        """Re-read the row right before isolation (requeue coordination).

        Returns ``False`` when the job vanished, moved to a new attempt
        (in-place continue/rerun), or became non-terminal after the
        eligibility snapshot — the caller must cancel the deletion.
        """
        if self._job_store is None:
            return False
        try:
            fresh = self._job_store.get(cand.job_id)
        except Exception:
            logger.debug("requeue re-check failed for %s", cand.job_id, exc_info=True)
            return False
        if fresh is None or fresh.attempt != cand.attempt:
            return False
        status = fresh.status.value if fresh.status else None
        remote = (fresh.result or {}).get("remote")
        remote = remote if isinstance(remote, dict) else {}
        return is_deletion_eligible(status, remote.get("submit_state"), remote.get("cancel_state"))

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
