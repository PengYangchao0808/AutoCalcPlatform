"""
Local Disk Protection
=====================

Retention-based lifecycle management for the ACP Server's local
``run_root`` filesystem.  This is the local counterpart to the remote
:class:`~acp.scheduler.remote.cleanup.RemoteCleanup` (Phase 5) and
mirrors its design while replacing every SSH/SFTP round-trip with a
local filesystem operation.

* :class:`LocalCleanup` scans ``run_root/<project_id>/<job_id>`` job
  directories and removes those older than the configured retention
  period (separate windows for completed / failed / cancelled jobs).
* :meth:`LocalCleanup.cleanup_old_db_records` prunes SQLite rows whose
  ``completed_at`` exceeds the (typically longer) DB retention window.
  DB cleanup is **independent** from work_dir cleanup — work_dir is
  deleted first (30/90 days) while DB records persist for audit (365
  days).
* :meth:`LocalCleanup.pre_submit_housekeeping` is invoked by the local
  :class:`~acp.scheduler.runner.JobRunner` before every local
  submission: when disk usage crosses the *cleanup* threshold (default
  90 %) it runs a **deletion-free** retention survey (the submission
  path must never delete task dirs); when usage still exceeds the *skip*
  threshold (default 95 %) after the survey the submission is rejected.

Safety:
    * Only ``run_root/<project>/<job_id>`` three-level structures are
      removed.  ``run_root`` itself, project directories, and
      ``acp_jobs.db`` are **never** deleted.
    * :func:`_is_safe_run_root` rejects ``/``, ``/tmp``, ``/home`` and
      shallow paths before any deletion.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from acp.scheduler.jobs import is_deletion_eligible
from acp.scheduler.store import JobStore

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_DIRS_PER_SWEEP",
    "DISK_CLEANUP_THRESHOLD",
    "DISK_SKIP_THRESHOLD",
    "LocalCleanupReport",
    "LocalCleanup",
    "LocalHousekeepingDecision",
    "RetentionPolicy",
    "_format_bytes",
    "_is_safe_run_root",
    "ORPHAN_DAYS_MULTIPLIER",
]

# Disk-usage thresholds (percent of the filesystem holding run_root).
# Above CLEANUP we run a retention sweep; above SKIP (even after sweep)
# we reject submission.  Mirrors RemoteCleanup defaults.
DISK_CLEANUP_THRESHOLD = 90
DISK_SKIP_THRESHOLD = 95

# Cap on the number of directories removed in a single sweep.  Local
# operations are fast (no SSH), so we can afford a larger cap than the
# remote default (100).  Leftover dirs are cleaned on the next sweep.
DEFAULT_MAX_DIRS_PER_SWEEP = 200

# Jobs marked FAILED by a server restart get a shorter retention window
# (their work_dir holds no useful partial results).  We detect them via
# the ``[RESTART_FAILED]`` prefix that ``_requeue_active_on_startup``
# writes — but the legacy prefix is ``interrupted by server restart``;
# we match either to be safe (risk 5 mitigation).
_RESTART_FAILED_MARKERS = ("[RESTART_FAILED]", "interrupted by server restart")

# Orphan dirs (no DB row) get an explicit LONGER independent window:
# ``orphan_days = ORPHAN_DAYS_MULTIPLIER * completed_days``.  An orphan
# has no lifecycle evidence, so it must outlive any recorded job window
# before reclamation — and only when it carries ownership evidence
# (``task.json`` present); otherwise it is kept (plan todo 9, r17).
ORPHAN_DAYS_MULTIPLIER = 3

#: Ownership evidence: a scheduler task dir always carries ``task.json``.
_ORPHAN_OWNERSHIP_MARKER = "task.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RetentionPolicy:
    """Retention windows + cleanup behaviour for :class:`LocalCleanup`.

    Attributes:
        completed_days: Retention (days) for completed / cancelled jobs.
        failed_days: Retention (days) for failed jobs (kept longer for
            post-mortem investigation).
        cancelled_days: Retention (days) for cancelled jobs.
        db_record_days: Retention (days) for SQLite rows.  Independent
            from work_dir retention — usually much longer for audit.
        vacuum_after_db_cleanup: Run ``VACUUM`` after deleting rows to
            reclaim space (locks the whole DB; off by default).
    """

    completed_days: int = 30
    failed_days: int = 90
    cancelled_days: int = 30
    db_record_days: int = 365
    vacuum_after_db_cleanup: bool = False


@dataclass
class LocalCleanupReport:
    """Outcome of a single local retention sweep.

    Attributes:
        work_dirs_removed: Absolute paths of job dirs removed (or that
            would be removed in dry-run).
        db_records_removed: Number of SQLite rows deleted.
        db_vacuumed: True when ``VACUUM`` ran after DB cleanup.
        freed_bytes_est: Estimated bytes reclaimed (sum of file sizes
            measured before deletion; ``0`` when not measured).
        errors: Human-readable per-dir or global error strings.
        dry_run: Whether this was a non-mutating dry run.
        capped: True when ``max_dirs_per_sweep`` truncated the sweep.
        disk_usage_before: Disk-usage percent before the sweep.
        disk_usage_after: Disk-usage percent after the sweep.
    """

    work_dirs_removed: list[str] = field(default_factory=list)
    db_records_removed: int = 0
    db_vacuumed: bool = False
    freed_bytes_est: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    capped: bool = False
    disk_usage_before: int = 0
    disk_usage_after: int = 0

    @property
    def ok(self) -> bool:
        """True when no errors were recorded."""
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "work_dirs_removed": list(self.work_dirs_removed),
            "work_dirs_removed_count": len(self.work_dirs_removed),
            "db_records_removed": self.db_records_removed,
            "db_vacuumed": self.db_vacuumed,
            "freed_bytes_est": self.freed_bytes_est,
            "freed_human": _format_bytes(self.freed_bytes_est),
            "errors": list(self.errors),
            "dry_run": self.dry_run,
            "capped": self.capped,
            "disk_usage_before": self.disk_usage_before,
            "disk_usage_after": self.disk_usage_after,
            "ok": self.ok,
        }


@dataclass
class LocalHousekeepingDecision:
    """Result of :meth:`LocalCleanup.pre_submit_housekeeping`.

    Attributes:
        should_skip: When True the local disk is too full and the job
            submission must be rejected.
        disk_usage_before: Disk-usage percent before any cleanup.
        disk_usage_after: Disk-usage percent after cleanup (== before if
            no sweep was triggered).
        cleanup: The :class:`LocalCleanupReport` if a sweep ran.
        reason: Short human-readable explanation of the decision.
    """

    should_skip: bool
    disk_usage_before: int
    disk_usage_after: int
    cleanup: LocalCleanupReport | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "should_skip": self.should_skip,
            "disk_usage_before": self.disk_usage_before,
            "disk_usage_after": self.disk_usage_after,
            "cleanup": self.cleanup.to_dict() if self.cleanup else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StatusEntry:
    """DB lifecycle snapshot used by the work-dir retention gate.

    Attributes:
        job_id: ``JobRecord.id`` (for the pre-delete requeue re-check).
        status: ``JobRecord.status`` value (``None`` when unknown).
        completed_at: ISO-8601 completion timestamp (age anchor).
        error: ``JobRecord.error`` (restart-marker detection).
        submit_state: ``result["remote"]["submit_state"]``.
        cancel_state: ``result["remote"]["cancel_state"]``.
        attempt: ``JobRecord.attempt`` at snapshot time (requeue check).
    """

    job_id: str | None = None
    status: str | None = None
    completed_at: str | None = None
    error: str | None = None
    submit_state: str | None = None
    cancel_state: str | None = None
    attempt: int | None = None


class LocalCleanup:
    """Local run_root lifecycle manager (work_dir + DB retention).

    Construction is cheap; all scans are performed on demand.  The
    instance reads the SQLite store once per sweep via
    :meth:`JobStore.list` and does **not** hold a long-lived DB
    connection.
    """

    def __init__(
        self,
        run_root: Path | str,
        store: JobStore,
        policy: RetentionPolicy | None = None,
        cleanup_threshold: int = DISK_CLEANUP_THRESHOLD,
        skip_threshold: int = DISK_SKIP_THRESHOLD,
        max_dirs_per_sweep: int = DEFAULT_MAX_DIRS_PER_SWEEP,
    ) -> None:
        self.run_root = Path(run_root)
        self.store = store
        self.policy = policy or RetentionPolicy()
        if cleanup_threshold > skip_threshold:
            raise ValueError(
                f"cleanup_threshold ({cleanup_threshold}) must not exceed "
                f"skip_threshold ({skip_threshold})"
            )
        self._cleanup_threshold = cleanup_threshold
        self._skip_threshold = skip_threshold
        self._max_dirs_per_sweep = max_dirs_per_sweep

    # ------------------------------------------------------------------ #
    # Disk usage
    # ------------------------------------------------------------------ #

    def check_disk_usage(self) -> int:
        """Return disk-usage percent (0–100) for the run_root filesystem.

        Uses :func:`shutil.disk_usage` (no external process, no SSH).
        Never raises — any failure returns ``0`` (fail-open) so a
        transient stat error does not spuriously block submission.
        """
        try:
            usage = shutil.disk_usage(self.run_root)
        except OSError:
            logger.debug("disk_usage query failed", exc_info=True)
            return 0
        if usage.total <= 0:
            return 0
        return int(round((usage.used / usage.total) * 100))

    def disk_usage_detail(self) -> dict[str, float | int]:
        """Detailed disk-usage breakdown for the maintenance API."""
        try:
            usage = shutil.disk_usage(self.run_root)
        except OSError:
            logger.debug("disk_usage detail query failed", exc_info=True)
            return {
                "total_bytes": 0.0,
                "used_bytes": 0.0,
                "free_bytes": 0.0,
                "percent_used": 0.0,
                "job_count": 0,
            }
        pct = (usage.used / usage.total) * 100 if usage.total > 0 else 0.0
        try:
            job_count = self.store.counts()
            total_jobs = sum(job_count.values())
        except Exception:
            logger.debug("job count query failed", exc_info=True)
            total_jobs = 0
        return {
            "total_bytes": float(usage.total),
            "used_bytes": float(usage.used),
            "free_bytes": float(usage.free),
            "percent_used": round(pct, 2),
            "job_count": total_jobs,
        }

    # ------------------------------------------------------------------ #
    # Work-dir retention sweep
    # ------------------------------------------------------------------ #

    def cleanup_old_work_dirs(
        self,
        dry_run: bool = False,
        max_dirs_per_sweep: int | None = None,
        allow_task_dir_deletion: bool = True,
    ) -> LocalCleanupReport:
        """Remove expired job directories under ``run_root/<project>/<job>``.

        Deletion eligibility is decided by the DB lifecycle FIRST
        (:func:`acp.scheduler.jobs.is_deletion_eligible`: terminal status
        AND ``submit_state`` settled AND cancellation confirmed); age is
        only consulted afterwards (mtime is auxiliary — never a deletion
        qualifier on its own, r16 P1).  The retention window for an
        eligible dir:

        * ``failed``            → :attr:`RetentionPolicy.failed_days`
        * ``cancelled``         → :attr:`RetentionPolicy.cancelled_days`
        * ``completed``         → :attr:`RetentionPolicy.completed_days`
        * restart-marked FAILED → :data:`_RESTART_DAYS` (shorter)
        * orphan (no DB row)    → ``ORPHAN_DAYS_MULTIPLIER × completed_days``
          AND ``task.json`` ownership evidence; otherwise kept.

        Right before an actual deletion the row is re-read so an in-place
        requeue/continue (attempt bump or non-terminal transition) that
        landed during the sweep cancels the deletion.

        Args:
            dry_run: Populate the report without deleting.
            max_dirs_per_sweep: Override the instance cap. ``<= 0`` means
                unlimited.  ``None`` uses the instance default.
            allow_task_dir_deletion: When ``False`` the sweep never calls
                ``shutil.rmtree`` (pre-submission path: space check only —
                the submission path must not delete task dirs).

        Returns:
            A :class:`LocalCleanupReport`.  Errors are recorded per-dir
            rather than raised (fail-open for the sweep itself).
        """
        report = LocalCleanupReport(dry_run=dry_run)
        cap = self._max_dirs_per_sweep if max_dirs_per_sweep is None else max_dirs_per_sweep

        if not _is_safe_run_root(self.run_root):
            report.errors.append(f"unsafe run_root: {self.run_root!s}")
            logger.error("Refusing local cleanup: unsafe run_root %r", str(self.run_root))
            return report

        if not self.run_root.is_dir():
            logger.debug("run_root %s does not exist — nothing to clean", self.run_root)
            return report

        # 1. Build {job_dir_name: StatusEntry} map (one read).
        status_map = self._build_status_map()

        now = time.time()
        norm_root = self.run_root.resolve()

        # 2. Walk run_root/<project>/<job_id>/.
        try:
            project_dirs = [p for p in self.run_root.iterdir() if p.is_dir()]
        except OSError as exc:
            report.errors.append(f"iterdir(run_root) failed: {exc}")
            logger.warning("Local cleanup listing failed: %s", exc)
            return report

        for project_dir in project_dirs:
            try:
                job_dirs = [p for p in project_dir.iterdir() if p.is_dir()]
            except OSError as exc:
                report.errors.append(f"iterdir({project_dir}) failed: {exc}")
                logger.debug("Local cleanup listing failed: %s", exc)
                continue

            for job_dir in job_dirs:
                # Cap the sweep so a huge backlog cannot block submission
                # or tie up the background thread for a long time.
                if cap > 0 and len(report.work_dirs_removed) >= cap:
                    report.capped = True
                    logger.info(
                        "Local cleanup hit max_dirs_per_sweep=%d; remaining dirs "
                        "deferred to next sweep",
                        cap,
                    )
                    return report

                target = str(job_dir.resolve())
                # Defense in depth: never delete run_root itself, a
                # project dir, or anything that resolves above run_root.
                # Normal layout is run_root/<project>/<job_id>/ so job_dir
                # is always two levels under the root.
                try:
                    rel = job_dir.resolve().relative_to(norm_root)
                except ValueError:
                    report.errors.append(f"{target}: escapes run_root, skipped")
                    continue
                if len(rel.parts) < 2:
                    # This is run_root itself or a project dir — leave it.
                    continue

                entry = status_map.get(job_dir.name)
                if entry is None:
                    candidate = self._orphan_candidate(job_dir, now)
                else:
                    # Gate 1: DB lifecycle (terminal + submit settled +
                    # cancel confirmed).  Non-terminal / pending-submission
                    # / unconfirmed-cancel dirs are NEVER deleted.
                    if not is_deletion_eligible(
                        entry.status, entry.submit_state, entry.cancel_state
                    ):
                        logger.debug(
                            "Local cleanup: keeping %s (gate: status=%s "
                            "submit_state=%s cancel_state=%s)",
                            target,
                            entry.status,
                            entry.submit_state,
                            entry.cancel_state,
                        )
                        continue
                    retention = self._retention_for(entry.status, entry.error)
                    age_ref = self._age_reference(entry.completed_at, job_dir)
                    if age_ref is None:
                        report.errors.append(f"{job_dir}: no mtime/timestamp to judge age")
                        continue
                    if now - age_ref <= retention * 86400:
                        continue  # fresh enough
                    candidate = (retention, entry.status)

                if candidate is None:
                    continue
                retention, status_label = candidate

                if not allow_task_dir_deletion:
                    # Pre-submission path: space check only, task dirs are
                    # protected here (rmtree spy must stay 0).
                    continue

                freed = self._dir_size_bytes(job_dir)
                if dry_run:
                    report.work_dirs_removed.append(target)
                    report.freed_bytes_est += freed
                    continue

                if entry is not None and not self._requeue_recheck(entry):
                    logger.info(
                        "Local cleanup: cancelled deletion of %s — job moved "
                        "to a new attempt or became non-terminal during the sweep",
                        target,
                    )
                    continue

                try:
                    shutil.rmtree(job_dir)
                except OSError as exc:
                    report.errors.append(f"{target}: {exc}")
                    logger.warning("Local cleanup failed to remove %s: %s", target, exc)
                    continue

                report.work_dirs_removed.append(target)
                report.freed_bytes_est += freed
                logger.info(
                    "Local cleanup: removed %s (status=%s, age>=%dd)",
                    target,
                    status_label or "orphan",
                    retention,
                )

        if report.work_dirs_removed:
            cap_note = " (capped)" if report.capped else ""
            mode = "dry-run" if dry_run else "reclaimed " + _format_bytes(report.freed_bytes_est)
            logger.info(
                "Local cleanup %s%s: removed %d dir(s), %d error(s)",
                mode,
                cap_note,
                len(report.work_dirs_removed),
                len(report.errors),
            )
        return report

    def _orphan_candidate(self, job_dir: Path, now: float) -> tuple[int, str] | None:
        """Age/ownership check for a dir with no DB row.

        Returns ``(retention_days, "orphan")`` when the dir is eligible
        for reclamation, ``None`` when it must be kept.  An orphan needs
        BOTH the longer independent window
        (``ORPHAN_DAYS_MULTIPLIER × completed_days``) AND ownership
        evidence (``task.json``) — otherwise keep + event.
        """
        if not (job_dir / _ORPHAN_OWNERSHIP_MARKER).exists():
            logger.info(
                "Local cleanup: kept orphan %s (no %s ownership evidence)",
                job_dir,
                _ORPHAN_OWNERSHIP_MARKER,
            )
            return None
        retention = self.policy.completed_days * ORPHAN_DAYS_MULTIPLIER
        age_ref = self._age_reference(None, job_dir)
        if age_ref is None or now - age_ref <= retention * 86400:
            return None
        return retention, "orphan"

    def _requeue_recheck(self, entry: StatusEntry) -> bool:
        """Re-read the row right before deletion (requeue coordination).

        Returns ``False`` when the job vanished, moved to a new attempt
        (in-place continue/rerun), or became non-terminal after the
        eligibility snapshot — the caller must cancel the deletion.
        """
        if not entry.job_id:
            return True
        try:
            fresh = self.store.get(entry.job_id)
        except Exception:
            logger.debug("requeue re-check failed for %s", entry.job_id, exc_info=True)
            return False
        if fresh is None:
            return False
        if fresh.attempt != entry.attempt:
            return False
        status = fresh.status.value if fresh.status else None
        remote = (fresh.result or {}).get("remote")
        remote = remote if isinstance(remote, dict) else {}
        return is_deletion_eligible(status, remote.get("submit_state"), remote.get("cancel_state"))

    # ------------------------------------------------------------------ #
    # DB record cleanup
    # ------------------------------------------------------------------ #

    def cleanup_old_db_records(self, dry_run: bool = False) -> LocalCleanupReport:
        """Delete SQLite job rows older than :attr:`RetentionPolicy.db_record_days`.

        Deletion cascades to the job's dependent rows (stage_tasks,
        artifacts, mechanism_studies, decision_points) via
        :meth:`JobStore.purge_cascade`.

        Independent of :meth:`cleanup_old_work_dirs` — DB rows persist
        longer so historical queries remain available after a job's
        work_dir is deleted.

        Args:
            dry_run: Count candidates without deleting.

        Returns:
            A :class:`LocalCleanupReport` (only ``db_records_removed`` /
            ``db_vacuumed`` / ``errors`` populated).
        """
        report = LocalCleanupReport(dry_run=dry_run)
        cutoff = _utc_now_iso_from(time.time() - self.policy.db_record_days * 86400)
        rows = self._list_db_rows_older_than(cutoff)
        if not rows:
            return report

        if dry_run:
            report.db_records_removed = len(rows)
            logger.info("Local DB cleanup (dry-run): %d row(s) eligible", len(rows))
            return report

        deleted = 0
        for job_id in rows:
            try:
                self.store.purge_cascade(job_id)
                deleted += 1
            except Exception as exc:
                report.errors.append(f"db:{job_id}: {exc}")
                logger.debug("Local DB cleanup failed to delete %s: %s", job_id, exc)
        report.db_records_removed = deleted

        if self.policy.vacuum_after_db_cleanup and deleted > 0:
            try:
                self._vacuum()
                report.db_vacuumed = True
            except Exception as exc:
                report.errors.append(f"vacuum: {exc}")
                logger.warning("Local DB VACUUM failed: %s", exc)

        logger.info(
            "Local DB cleanup: removed %d row(s), vacuumed=%s",
            deleted,
            report.db_vacuumed,
        )
        return report

    # ------------------------------------------------------------------ #
    # Combined / pre-submit housekeeping
    # ------------------------------------------------------------------ #

    def full_cleanup(self, dry_run: bool = False) -> LocalCleanupReport:
        """Run work_dir + DB cleanup in one pass.

        Work_dir sweep runs first (reclaims disk), then DB pruning
        (cheap, independent).  Both reports are merged into one.  Disk
        usage is measured before and after.
        """
        merged = LocalCleanupReport(dry_run=dry_run)
        merged.disk_usage_before = self.check_disk_usage()

        work_report = self.cleanup_old_work_dirs(dry_run=dry_run)
        merged.work_dirs_removed.extend(work_report.work_dirs_removed)
        merged.freed_bytes_est += work_report.freed_bytes_est
        merged.errors.extend(work_report.errors)
        merged.capped = merged.capped or work_report.capped

        db_report = self.cleanup_old_db_records(dry_run=dry_run)
        merged.db_records_removed = db_report.db_records_removed
        merged.db_vacuumed = db_report.db_vacuumed
        merged.errors.extend(db_report.errors)

        merged.disk_usage_after = self.check_disk_usage()
        return merged

    def pre_submit_housekeeping(self) -> LocalHousekeepingDecision:
        """Inspect local disk pressure before submitting a job.

        Policy (mirrors :meth:`RemoteCleanup.pre_submit_housekeeping`):

        * usage <= cleanup_threshold → proceed (no action).
        * usage > cleanup_threshold → run a DELETION-FREE retention
          survey (``cleanup_old_work_dirs(allow_task_dir_deletion=False)``),
          re-check.
        * usage > skip_threshold (after survey, or if it failed) →
          ``should_skip=True``.

        D03 decoupling (plan todo 9 option **(B)**): the submission path
        must not delete task dirs, so this entry never removes anything
        (``shutil.rmtree`` spy stays 0 here) — it only measures whether
        existing retention headroom would relieve the pressure.  The
        reason is submission-path safety, NOT a missing DB handle
        (:class:`LocalCleanup` holds ``store``).

        Failures while querying disk usage fail-open (return 0 %) so a
        transient ``OSError`` does not block submission.  Survey failures
        are recorded in the report but do not abort housekeeping.

        Returns:
            A :class:`LocalHousekeepingDecision`.  When ``should_skip``
            the caller should reject submission (return exit_code=1).
        """
        before = self._safe_disk_usage()
        cleanup: LocalCleanupReport | None = None

        if before > self._cleanup_threshold:
            logger.info(
                "Local disk usage is %d%% (> %d%%) — triggering retention cleanup",
                before,
                self._cleanup_threshold,
            )
            try:
                cleanup = self.cleanup_old_work_dirs(allow_task_dir_deletion=False)
            except Exception as exc:
                logger.warning("Local retention cleanup raised: %s", exc)
                cleanup = LocalCleanupReport(errors=[f"cleanup raised: {exc}"])
            after = self._safe_disk_usage()
        else:
            after = before

        # Conservative fallback: if the after-probe failed (returned 0)
        # but we *knew* the disk was under pressure, keep the before
        # value so a flaky stat cannot mask a genuinely full disk.
        if after <= 0 < before:
            after = before

        if after > self._skip_threshold:
            suffix = f" (cleanup: before={before}%, after={after}%)" if cleanup else ""
            return LocalHousekeepingDecision(
                should_skip=True,
                disk_usage_before=before,
                disk_usage_after=after,
                cleanup=cleanup,
                reason=f"disk usage {after}% exceeds skip threshold "
                f"{self._skip_threshold}%{suffix}",
            )

        if cleanup is not None:
            return LocalHousekeepingDecision(
                should_skip=False,
                disk_usage_before=before,
                disk_usage_after=after,
                cleanup=cleanup,
                reason=f"ok after cleanup (before={before}%, after={after}%)",
            )

        return LocalHousekeepingDecision(
            should_skip=False,
            disk_usage_before=before,
            disk_usage_after=after,
            cleanup=None,
            reason="ok (no cleanup needed)",
        )

    def _safe_disk_usage(self) -> int:
        """Disk-usage percent, never raising (fail-open returns 0).

        Mirrors the remote ``_safe_disk_usage`` wrapper so a broken
        :meth:`check_disk_usage` (or any unexpected error) degrades to
        "0 %" — submission proceeds rather than spuriously failing.
        """
        try:
            return self.check_disk_usage()
        except Exception:
            logger.debug("local disk usage query failed", exc_info=True)
            return 0

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_status_map(self) -> dict[str, StatusEntry]:
        """One-shot read of DB: {job_dir_name: StatusEntry}.

        job_dir_name is the last path component of ``record.work_dir``
        (falling back to ``record.id``) so it matches ``job_dir.name``
        during the filesystem walk.  ``submit_state``/``cancel_state``
        come from ``record.result["remote"]`` (D02/D05 protocols) — the
        retention gate needs them alongside the status.
        """
        out: dict[str, StatusEntry] = {}
        try:
            records = self.store.list(limit=500000)
        except Exception as exc:
            logger.debug("status map build failed: %s", exc)
            return out
        for record in records:
            key = Path(record.work_dir).name if record.work_dir else record.id
            remote = (record.result or {}).get("remote")
            remote = remote if isinstance(remote, dict) else {}
            out[key] = StatusEntry(
                job_id=record.id,
                status=record.status.value if record.status else None,
                completed_at=record.completed_at,
                error=record.error,
                submit_state=remote.get("submit_state"),
                cancel_state=remote.get("cancel_state"),
                attempt=record.attempt,
            )
        return out

    def _retention_for(self, status: str | None, error_text: str | None) -> int:
        """Pick the retention window (days) for a job of given status.

        Only ever called AFTER :func:`is_deletion_eligible` passed (so
        ``status`` is terminal); the completed-window fallback below is
        defensive only and no longer reachable for RUNNING/PAUSED/QUEUED
        rows (r16 P1 — non-terminal dirs are gated out first).
        """
        if error_text and any(m in error_text for m in _RESTART_FAILED_MARKERS):
            return _RESTART_DAYS
        if status == "failed":
            return self.policy.failed_days
        if status == "cancelled":
            return self.policy.cancelled_days
        # terminal completed (or defensive unknown) → completed window.
        return self.policy.completed_days

    @staticmethod
    def _age_reference(completed_at: str | None, job_dir: Path) -> float | None:
        """Newer of ``completed_at`` epoch and the dir mtime.

        Returns ``None`` when neither is usable (no completed_at AND
        mtime unreadable).  Caller then leaves the dir alone.
        """
        mtime_epoch: float | None = None
        try:
            mtime_epoch = job_dir.stat().st_mtime
        except OSError:
            logger.debug("stat failed for %s", job_dir, exc_info=True)
        if completed_at:
            ts = _iso_to_epoch(completed_at)
            if ts is not None:
                if mtime_epoch is None:
                    return ts
                return max(ts, mtime_epoch)
        return mtime_epoch

    @staticmethod
    def _dir_size_bytes(path: Path) -> int:
        """Recursive byte size of *path*; 0 on any failure."""
        total = 0
        try:
            for entry in path.rglob("*"):
                try:
                    # is_file(follow_symlinks=...) needs Python 3.13+;
                    # equivalent: exclude symlinks, then test regular file.
                    if not entry.is_symlink() and entry.is_file():
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        except OSError:
            logger.debug("size scan failed for %s", path, exc_info=True)
            return 0
        return total

    def _list_db_rows_older_than(self, cutoff_iso: str) -> list[str]:
        """Return job IDs whose ``completed_at`` predates *cutoff_iso*.

        Only terminal-state jobs with a populated ``completed_at`` are
        eligible.  Uses a single read-only connection.
        """
        rows: list[str] = []
        try:
            conn = sqlite3.connect(str(self.store.db_path))
            try:
                cur = conn.execute(
                    """SELECT id FROM jobs
                       WHERE completed_at IS NOT NULL
                         AND completed_at != ''
                         AND completed_at < ?
                         AND status IN ('completed', 'failed', 'cancelled')""",
                    (cutoff_iso,),
                )
                rows = [r[0] for r in cur.fetchall()]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.debug("DB row scan failed: %s", exc)
        return rows

    def _vacuum(self) -> None:
        conn = sqlite3.connect(str(self.store.db_path))
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()


# Jobs re-marked FAILED by a server restart retain a shorter window
# (their work_dir has no useful partial results — the workflow never
# ran to completion in this process lifetime).
_RESTART_DAYS = 7


def _iso_to_epoch(iso: str) -> float | None:
    """Parse an ISO-8601 timestamp (``datetime.isoformat``) to epoch.

    Returns ``None`` on any parse failure.  Tolerant of trailing ``Z``.
    """
    if not iso:
        return None
    text = iso.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, TypeError):
        return None


def _utc_now_iso_from(epoch: float) -> str:
    """ISO-8601 UTC string for an epoch value (inverse of _iso_to_epoch)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _is_safe_run_root(path: Path | str) -> bool:
    """Reject ``run_root`` values that must never be recursively deleted.

    Mirrors the remote :func:`_is_safe_work_dir` design but uses
    :mod:`pathlib`.  Rejects:

    * Empty / relative paths.
    * Literal dangerous roots: ``/``, ``/tmp``, ``/home``, ``/var``,
      ``/var/tmp``, ``/usr``, ``/root``, ``/run``, ``.``, ``..``.
    * Overly shallow absolute paths (fewer than 2 components after the
      leading ``/``) — e.g. ``/scratch``.

    Note that a path *under* a dangerous root (e.g. ``/tmp/acp_runs``)
    is accepted once it has enough depth, matching the remote behaviour
    so pytest tmpdirs (``/tmp/pytest-xxx/run_root``) work.
    """
    if path is None or str(path) == "":
        return False
    raw = Path(str(path))
    if not raw.is_absolute():
        return False
    norm = raw.resolve(strict=False)
    norm_str = str(norm)
    parts = [p for p in norm.parts if p != "/"]
    # Require at least 2 components (e.g. /var/acp, /scratch/acp_jobs).
    if len(parts) < 2:
        return False
    dangerous_roots = {
        "/tmp",
        "/var/tmp",
        "/home",
        "/var",
        "/usr",
        "/root",
        "/run",
        "/dev",
        "/proc",
        "/sys",
        "/etc",
        "/opt",
        "/mnt",
        "/media",
    }
    if norm_str in dangerous_roots:
        return False
    return True


def _format_bytes(n: int) -> str:
    """Human-readable byte count (binary units). Mirrors remote helper."""
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
