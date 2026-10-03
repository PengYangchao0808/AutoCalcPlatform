# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportUnusedCallResult=false
"""
CLI Job Registration
====================

X1′-D: bind an already-finished CLI run directory into the scheduler job
store so the Workbench job list and ``GET /api/v1/jobs/{id}/s2/profile``
can resolve it without a scheduler submission.

Why not ``JobManager``: its constructor claims the run_root single-instance
lock and runs restart-recovery + dispatch — a CLI process must never boot a
second manager against a live server's run_root.  This module therefore
reuses only the *store-layer* APIs that ``JobManager.submit`` itself uses
(:meth:`JobStore.create`, :meth:`TaskIndex.sync_from_job`,
:meth:`ProjectManager.ensure_default_project`) so a registered row is
indistinguishable from a scheduler-persisted one.

Invariants (root AGENTS.md):
- ANTI-PATTERN #15 — the job id is never placed on disk.  ``work_dir``
  binds to the caller's existing output directory (the CLI ``--output``);
  the id lives only in the DB PK / ``job.json`` / task index.
- ANTI-PATTERN #13 — persistence goes through :class:`JobStore`, never
  hand-written SQL.
- Status semantics: a registered CLI run is ``JobStatus.COMPLETED`` with
  ``progress=1.0`` / ``exit_code=0``; it is terminal, so pause/resume/
  continue never touch it, and ``purge_cascade`` removes it like any job.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.core.paths import resolve_run_root
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.projects import ProjectManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex
from acp.storage.layout import runtime_file

logger = logging.getLogger(__name__)

__all__ = [
    "CliJobRegistrationError",
    "register_completed_cli_job",
]


class CliJobRegistrationError(RuntimeError):
    """A CLI run could not be registered as a completed scheduler job."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_job_name(raw_name: str) -> str:
    """Mirror :meth:`JobManager.submit`'s job-id name sanitisation."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in raw_name)[:40]


def _mint_job_id(store: JobStore, safe_name: str) -> str:
    """Mint ``{ts}_{seq:03d}_{safe_name}`` — same shape as ``JobManager.submit``.

    Uses :meth:`JobStore.get` to pick the first free sequence number for the
    current second; collisions with a concurrent writer surface as
    ``sqlite3.IntegrityError`` from :meth:`JobStore.create` and are retried
    by the caller.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for seq in range(1, 1000):
        job_id = f"{ts}_{seq:03d}_{safe_name}"
        if store.get(job_id) is None:
            return job_id
    raise CliJobRegistrationError(f"could not mint a free job id for {safe_name!r}")


def register_completed_cli_job(
    *,
    workflow: str,
    work_dir: Path | str,
    run_root: Path | str | None = None,
    name: str = "",
    input_payload: dict[str, Any] | None = None,
    method: dict[str, Any] | None = None,
    resources: dict[str, Any] | None = None,
    config_path: str | None = None,
    result: dict[str, Any] | None = None,
    required_files: tuple[str, ...] = (),
) -> JobRecord:
    """Register an already-completed CLI run directory as a scheduler job.

    The run must have finished successfully *and* left every path in
    ``required_files`` (relative to ``work_dir``) on disk — e.g. the
    XtbPathSearch ``/s2/profile`` contract requires
    ``RESULT/pes_search/pes_profile.json``.  Missing files abort with
    :class:`CliJobRegistrationError` so a job that the Workbench could not
    visualize is never persisted.

    Args:
        workflow: Catalog workflow id (e.g. ``"XtbPathSearch"``).
        work_dir: Existing CLI output directory (``--output``).  Bound
            verbatim (resolved) as ``JobRecord.work_dir`` — never renamed,
            never suffixed with the job id.
        run_root: Explicit data root; defaults to ``ACP_RUN_ROOT`` env or
            the platform default (same resolution as the ACP server).
        name: Task label; defaults to the ``work_dir`` leaf name.
        input_payload: Stored as ``JobSpec.input`` (e.g. the frozen
            ``path_request`` payload).
        method: Stored as ``JobSpec.method``.
        resources: ``JobSpec.resources`` (nproc/mem as passed on the CLI).
        config_path: Optional YAML config path for provenance.
        result: Extra ``JobRecord.result`` metadata (workflow output paths).
        required_files: Work-dir-relative files that must exist before the
            job is persisted.

    Returns:
        The persisted :class:`JobRecord` (status ``completed``).

    Raises:
        CliJobRegistrationError: work dir missing, required file missing,
            or the store rejected the row.
    """
    root = Path(work_dir).expanduser().resolve()
    if not root.is_dir():
        raise CliJobRegistrationError(f"work dir does not exist: {root}")
    missing = [rel for rel in required_files if not (root / rel).is_file()]
    if missing:
        raise CliJobRegistrationError(
            f"cannot register {workflow} run at {root}: missing {', '.join(missing)}"
        )

    data_root = resolve_run_root(run_root)
    store = JobStore(data_root / "acp_jobs.db")
    project_id = ProjectManager(store, data_root).ensure_default_project()

    effective_name = name or root.name or workflow
    spec = JobSpec(
        workflow=workflow,
        name=effective_name,
        input=dict(input_payload or {}),
        method=dict(method or {}),
        resources=dict(resources or {}),
        config_path=config_path,
        project_id=project_id,
    )
    spec = replace(spec, input_hash=compute_input_hash(spec))
    now = _utc_now_iso()
    safe_name = _safe_job_name(effective_name)

    last_error: sqlite3.IntegrityError | None = None
    for _attempt in range(3):
        job_id = _mint_job_id(store, safe_name)
        record = JobRecord(
            id=job_id,
            spec=spec,
            status=JobStatus.COMPLETED,
            work_dir=str(root),
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now,
            progress=1.0,
            exit_code=0,
            project_id=project_id,
            input_hash=spec.input_hash,
            group_id=job_id,
            result=dict(result) if result else None,
        )
        try:
            store.create(record)
            break
        except sqlite3.IntegrityError as exc:
            last_error = exc
    else:
        raise CliJobRegistrationError(
            f"store rejected job row for {workflow} at {root}: {last_error}"
        ) from last_error

    # Mirror JobManager.submit's persistence side-effects (best-effort: a
    # broken index or marker write must never undo a successful registration).
    try:
        TaskIndex(store.db_path).sync_from_job(record)
    except (OSError, sqlite3.Error, TypeError, ValueError):
        logger.warning("Task index sync failed for registered job %s", record.id, exc_info=True)
    try:
        (root / "job.json").write_text(
            json.dumps(record.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
    except OSError:
        logger.debug("job.json write failed for %s", record.id, exc_info=True)
    try:
        JobEventLog(runtime_file(root, "events.jsonl")).append(
            "job.registered",
            job_id=record.id,
            workflow=workflow,
            source="cli",
            work_dir=str(root),
        )
    except OSError:
        logger.debug("events.jsonl append failed for %s", record.id, exc_info=True)

    logger.info(
        "Registered CLI job %s (workflow=%s status=%s work_dir=%s)",
        record.id,
        workflow,
        record.status.value,
        record.work_dir,
    )
    return record
