# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportUnusedCallResult=false
"""
Scheduler Job Store
===================

SQLite-backed job index with per-job directory layout. SQLite holds queryable
metadata; each job also owns a directory with ``job.json``, ``state.json``,
``events.jsonl``, ``stdout.log``, and ``stderr.log``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.migrations import migrate

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_clause(
    expected_status: Collection[JobStatus] | JobStatus | str | None,
) -> tuple[str, list[Any]]:
    """SQL fragment + params for an ``expected_status`` precondition."""
    if expected_status is None:
        return "", []
    if isinstance(expected_status, (JobStatus, str)):
        value = expected_status.value if isinstance(expected_status, JobStatus) else expected_status
        return " AND status=?", [value]
    statuses = list(expected_status)
    if not statuses:
        raise ValueError("expected_status collection must not be empty")
    placeholders = ",".join("?" for _ in statuses)
    values = [s.value if isinstance(s, JobStatus) else str(s) for s in statuses]
    return f" AND status IN ({placeholders})", values


def _encode_field(name: str, value: Any) -> Any:
    if name == "result":
        return json.dumps(value) if value is not None else None
    if name == "status":
        return value.value if isinstance(value, JobStatus) else str(value)
    return value


def _conflict_state(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    columns = set(row.keys())
    state: dict[str, Any] = {}
    for key in ("status", "revision", "attempt"):
        if key in columns:
            state[key] = row[key]
    return state


class JobStateConflictError(Exception):
    """CAS precondition failed: the jobs row is not in the expected state."""

    def __init__(self, job_id: str, expected: dict[str, Any], actual: dict[str, Any] | None):
        super().__init__(f"job {job_id} state conflict: expected {expected}, actual {actual}")
        self.job_id = job_id
        self.expected = expected
        self.actual = actual

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    work_dir TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    project_id TEXT,
    input_hash TEXT,
    current_stage TEXT,
    progress REAL,
    error TEXT,
    pid INTEGER,
    exit_code INTEGER,
    remote_job_id TEXT,
    group_id TEXT,
    node_id TEXT,
    host TEXT,
    result_json TEXT
)
"""


class JobStore:
    """Thread-safe SQLite persistence for :class:`JobRecord`."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            conn.commit()
        migrate(self.db_path)

    def create(self, record: JobRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (id, workflow, name, status, work_dir, spec_json,
                       created_at, updated_at, started_at, completed_at, project_id,
                       input_hash, current_stage, progress, error, pid, exit_code,
                       remote_job_id, group_id, result_json, revision, attempt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _record_to_row(record),
            )
            conn.commit()

    def update(self, record: JobRecord) -> None:
        """Persist a whole record unconditionally (bare UPDATE, no CAS).

        Deprecated: prefer :meth:`transition`, :meth:`update_progress`, or
        :meth:`requeue_with_spec` — a stale whole-row write can clobber
        concurrent state changes.  Kept until the manager write sites are
        migrated; does not touch ``revision`` / ``attempt``.
        """
        record.touch()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, current_stage=?, progress=?, error=?,
                       pid=?, exit_code=?, remote_job_id=?, started_at=?,
                       completed_at=?, updated_at=?,
                       result_json=?, spec_json=?, project_id=?, input_hash=?, group_id=?,
                       node_id=?, host=?
                       WHERE id=?""",
                (
                    record.status.value,
                    record.current_stage,
                    record.progress,
                    record.error,
                    record.pid,
                    record.exit_code,
                    record.remote_job_id,
                    record.started_at,
                    record.completed_at,
                    record.updated_at,
                    json.dumps(record.result) if record.result is not None else None,
                    _spec_to_json(record.spec),
                    record.project_id or record.spec.project_id,
                    record.input_hash or record.spec.input_hash,
                    record.group_id,
                    record.node_id,
                    record.host,
                    record.id,
                ),
            )
            conn.commit()

    _TRANSITION_FIELDS = frozenset(
        {
            "status",
            "current_stage",
            "progress",
            "error",
            "pid",
            "exit_code",
            "remote_job_id",
            "started_at",
            "completed_at",
            "result",
        }
    )

    def transition(
        self,
        job_id: str,
        *,
        expected_status: Collection[JobStatus] | JobStatus | None,
        expected_revision: int,
        expected_attempt: int | None = None,
        **fields: Any,
    ) -> JobRecord:
        """CAS single-row state update: one UPDATE guarded by revision/status/attempt.

        Only whitelisted fields are written (never ``spec_json``); ``revision``
        increments and the fresh record is read back on the same connection.
        Raises :class:`JobStateConflictError` when the row does not match the
        expectation, and :class:`ValueError` for unknown or forbidden fields.
        """
        unknown = sorted(set(fields) - self._TRANSITION_FIELDS)
        if unknown:
            raise ValueError(f"transition does not allow field(s): {', '.join(unknown)}")

        assignments = ["revision=revision+1", "updated_at=?"]
        params: list[Any] = [_utc_now_iso()]
        for name, value in fields.items():
            column = "result_json" if name == "result" else name
            assignments.append(f"{column}=?")
            params.append(_encode_field(name, value))

        status_sql, status_params = _status_clause(expected_status)
        where = "id=? AND revision=?" + status_sql
        args: list[Any] = [job_id, expected_revision, *status_params]
        expected: dict[str, Any] = {"revision": expected_revision}
        if expected_status is not None:
            if isinstance(expected_status, (JobStatus, str)):
                expected["status"] = (
                    expected_status.value
                    if isinstance(expected_status, JobStatus)
                    else expected_status
                )
            else:
                expected["status"] = sorted(
                    s.value if isinstance(s, JobStatus) else str(s) for s in expected_status
                )
        if expected_attempt is not None:
            where += " AND attempt=?"
            args.append(expected_attempt)
            expected["attempt"] = expected_attempt

        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE {where}",
                (*params, *args),
            )
            if cursor.rowcount != 1:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                raise JobStateConflictError(job_id, expected, _conflict_state(row))
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobStateConflictError(job_id, expected, None)
            conn.commit()
            return _row_to_record(row)

    def update_progress(
        self,
        job_id: str,
        *,
        expected_revision: int,
        progress: float | None = None,
        current_stage: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        pid: int | None = None,
        exit_code: int | None = None,
    ) -> JobRecord:
        """CAS narrow progress write: only the non-None arguments are persisted.

        Never touches ``status`` or ``spec_json``; ``revision`` increments on
        success and :class:`JobStateConflictError` guards stale writers.
        """
        provided: dict[str, Any] = {}
        if progress is not None:
            provided["progress"] = progress
        if current_stage is not None:
            provided["current_stage"] = current_stage
        if result is not None:
            provided["result"] = result
        if error is not None:
            provided["error"] = error
        if pid is not None:
            provided["pid"] = pid
        if exit_code is not None:
            provided["exit_code"] = exit_code

        assignments = ["revision=revision+1", "updated_at=?"]
        params: list[Any] = [_utc_now_iso()]
        for name, value in provided.items():
            column = "result_json" if name == "result" else name
            assignments.append(f"{column}=?")
            params.append(_encode_field(name, value))

        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id=? AND revision=?",
                (*params, job_id, expected_revision),
            )
            if cursor.rowcount != 1:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                raise JobStateConflictError(
                    job_id, {"revision": expected_revision}, _conflict_state(row)
                )
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobStateConflictError(job_id, {"revision": expected_revision}, None)
            conn.commit()
            return _row_to_record(row)

    _REQUEUE_RESET_DEFAULTS: dict[str, Any] = {
        "started_at": None,
        "completed_at": None,
        "current_stage": None,
        "progress": None,
        "error": None,
        "pid": None,
        "exit_code": None,
        "remote_job_id": None,
    }

    def requeue_with_spec(
        self,
        job_id: str,
        *,
        new_spec: JobSpec,
        expected_revision: int,
        expected_attempt: int,
        expected_status: Collection[JobStatus] | JobStatus | None,
        **reset_fields: Any,
    ) -> JobRecord:
        """CAS requeue in one transaction: new spec + attempt+1 + QUEUED.

        The only legal spec-changing entry point.  Runtime fields reset to
        NULL by default (``reset_fields`` may override them plus ``result``
        and ``input_hash``); ``result_json`` is untouched unless passed.
        Raises :class:`TypeError` for a non-:class:`JobSpec` ``new_spec`` and
        :class:`ValueError` for forbidden ``reset_fields``.
        """
        if not isinstance(new_spec, JobSpec):
            raise TypeError(
                f"requeue_with_spec requires a JobSpec, got {type(new_spec).__name__}"
            )
        allowed = set(self._REQUEUE_RESET_DEFAULTS) | {"result", "input_hash"}
        unknown = sorted(set(reset_fields) - allowed)
        if unknown:
            raise ValueError(f"requeue_with_spec does not allow field(s): {', '.join(unknown)}")

        values: dict[str, Any] = dict(self._REQUEUE_RESET_DEFAULTS)
        for name in self._REQUEUE_RESET_DEFAULTS:
            if name in reset_fields:
                values[name] = reset_fields[name]

        assignments = [
            "spec_json=?",
            "attempt=attempt+1",
            "revision=revision+1",
            "updated_at=?",
            "status=?",
        ]
        params: list[Any] = [_spec_to_json(new_spec), _utc_now_iso(), JobStatus.QUEUED.value]
        for name, value in values.items():
            assignments.append(f"{name}=?")
            params.append(value)
        if "result" in reset_fields:
            assignments.append("result_json=?")
            params.append(_encode_field("result", reset_fields["result"]))
        if "input_hash" in reset_fields:
            assignments.append("input_hash=?")
            params.append(reset_fields["input_hash"])

        status_sql, status_params = _status_clause(expected_status)
        where = "id=? AND revision=? AND attempt=?" + status_sql
        args: list[Any] = [job_id, expected_revision, expected_attempt, *status_params]

        expected: dict[str, Any] = {"revision": expected_revision, "attempt": expected_attempt}
        if expected_status is not None:
            if isinstance(expected_status, (JobStatus, str)):
                expected["status"] = (
                    expected_status.value
                    if isinstance(expected_status, JobStatus)
                    else expected_status
                )
            else:
                expected["status"] = sorted(
                    s.value if isinstance(s, JobStatus) else str(s) for s in expected_status
                )

        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE {where}",
                (*params, *args),
            )
            if cursor.rowcount != 1:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                raise JobStateConflictError(job_id, expected, _conflict_state(row))
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise JobStateConflictError(job_id, expected, None)
            conn.commit()
            return _row_to_record(row)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_record(row) if row else None

    def get_many(self, job_ids: list[str]) -> dict[str, JobRecord]:
        """Read a bounded batch of job records without per-row connections."""
        if not job_ids:
            return {}
        records: dict[str, JobRecord] = {}
        with self._lock, self._connect() as conn:
            for start in range(0, len(job_ids), 500):
                batch = job_ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE id IN ({placeholders})", batch
                ).fetchall()
                records.update((row["id"], _row_to_record(row)) for row in rows)
        return records

    def list_terminal_jobs_paged(
        self,
        offset: int = 0,
        limit: int = 25,
    ) -> list[JobRecord]:
        """Page through all terminal jobs, ordered by terminal timestamp desc."""
        query = (
            "SELECT * FROM jobs "
            "WHERE status IN (?, ?, ?) "
            "ORDER BY COALESCE(completed_at, updated_at, created_at) DESC "
            "LIMIT ? OFFSET ?"
        )
        params: tuple[Any, ...] = (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            limit,
            offset,
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def count_terminal_jobs(self) -> int:
        """Count all terminal (COMPLETED/FAILED/CANCELLED) jobs."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM jobs WHERE status IN (?, ?, ?)",
                (
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                ),
            ).fetchone()
        return row["n"] if row else 0

    def list(
        self,
        status: str | None = None,
        limit: int = 200,
        *,
        project_id: str | None = None,
        completed_before: str | None = None,
    ) -> list[JobRecord]:
        """List jobs, newest first, with optional filters combined via AND.

        Args:
            status: Exact status value (``JobStatus.value``).
            limit: Maximum rows returned.
            project_id: Restrict to one project.
            completed_before: ISO cutoff — only rows with a non-empty
                ``completed_at`` strictly older than this timestamp.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        if completed_before is not None:
            clauses.append("completed_at IS NOT NULL AND completed_at != '' AND completed_at<?")
            params.append(completed_before)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_recent_completed(
        self,
        limit: int = 20,
        *,
        project_id: str | None = None,
        workflow: str | None = None,
        completed_after: str | None = None,
    ) -> list[JobRecord]:
        """List COMPLETED jobs, most recently completed first.

        Args:
            limit: Maximum rows returned.
            project_id: Restrict to one project.
            workflow: Restrict to one workflow id.
            completed_after: ISO cutoff — only rows with ``completed_at``
                at or after this timestamp.
        """
        clauses: list[str] = ["status=?"]
        params: list[Any] = [JobStatus.COMPLETED.value]
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        if workflow is not None:
            clauses.append("workflow=?")
            params.append(workflow)
        if completed_after is not None:
            clauses.append("completed_at IS NOT NULL AND completed_at != '' AND completed_at>=?")
            params.append(completed_after)
        where = " AND ".join(clauses)
        query = f"SELECT * FROM jobs WHERE {where} ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_recent_terminal(
        self,
        limit: int = 20,
        *,
        project_id: str | None = None,
        workflow: str | None = None,
    ) -> list[JobRecord]:
        """List terminal jobs, newest terminal update first.

        Unlike :meth:`list_recent_completed`, this includes failed and
        cancelled jobs.  Callers must still apply their own artifact policy:
        a terminal job is not, by itself, evidence that every file in its
        working directory is safe to reuse.
        """
        clauses: list[str] = ["status IN (?, ?, ?)"]
        params: list[Any] = [
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        ]
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        if workflow is not None:
            clauses.append("workflow=?")
            params.append(workflow)
        query = (
            f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(completed_at, updated_at, created_at) DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_by_project(self, project_id: str, limit: int = 200) -> list[JobRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def project_name_map(self) -> dict[str, str]:
        """Map ``project_id → display name`` for source summaries.

        Returns an empty dict when the projects table does not exist yet
        (a fresh database that only JobStore has initialised).
        """
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute("SELECT project_id, name FROM projects").fetchall()
        except sqlite3.Error:
            return {}
        return {str(r["project_id"]): str(r["name"]) for r in rows if r["project_id"]}

    def list_enriched(
        self,
        status: str | None = None,
        limit: int = 200,
        *,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List jobs newest-first with owner projection (project name + study linkage).

        Each returned dict carries the plain :class:`JobRecord` under ``"record"``
        plus ``project_name``, ``study_id`` and ``study_status`` from a LEFT JOIN
        against ``projects`` and ``mechanism_studies``. Used by the v1 job-list
        endpoint so the UI can group by project/group without extra round-trips.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("j.status=?")
            params.append(status)
        if project_id is not None:
            clauses.append("j.project_id=?")
            params.append(project_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT j.*, p.name AS project_name, ms.id AS study_id, ms.status AS study_status
            FROM jobs j
            LEFT JOIN projects p ON p.project_id = j.project_id
            LEFT JOIN mechanism_studies ms ON ms.job_id = j.id
            {where}
            ORDER BY j.created_at DESC LIMIT ?
        """
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            columns = set(r.keys())
            record = _row_to_record(r)
            out.append(
                {
                    "record": record,
                    "project_name": r["project_name"] if "project_name" in columns else None,
                    "study_id": r["study_id"] if "study_id" in columns else None,
                    "study_status": (r["study_status"] if "study_status" in columns else None),
                    "group_id": (
                        r["group_id"] if "group_id" in columns else record.group_id or record.id
                    ),
                }
            )
        return out

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in JobStatus}
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    def delete(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()

    def has_job_dependents(self, job_id: str) -> bool:
        """Return True when any child table still holds rows for *job_id*.

        Covers exactly the tables :meth:`purge_cascade` cleans
        (``tasks`` / ``stage_tasks`` / ``artifacts`` /
        ``mechanism_studies``).  Used to distinguish "job never existed"
        from ghost index entries left behind by a non-cascading delete of
        the ``jobs`` row.
        """
        with self._lock, self._connect() as conn:
            for table in ("tasks", "stage_tasks", "artifacts", "mechanism_studies"):
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE job_id=? LIMIT 1", (job_id,)
                ).fetchone()
                if row is not None:
                    return True
        return False

    def purge_cascade(self, job_id: str) -> None:
        """Delete a job row plus every dependent row, in one connection.

        No FK cascades exist in the schema, so children are removed
        explicitly in dependency order: ``stage_tasks``, ``artifacts``,
        and ``tasks`` by ``job_id``; ``decision_points`` via
        ``mechanism_studies`` subselect (it has no ``job_id`` column);
        then ``mechanism_studies`` and finally the ``jobs`` row itself.
        """
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM stage_tasks WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM artifacts WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM tasks WHERE job_id=?", (job_id,))
            conn.execute(
                "DELETE FROM decision_points WHERE study_id IN "
                "(SELECT id FROM mechanism_studies WHERE job_id=?)",
                (job_id,),
            )
            conn.execute("DELETE FROM mechanism_studies WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()

    def update_project_id(self, job_id: str, project_id: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            record = _row_to_record(row)
            record.project_id = project_id
            record.spec = replace(record.spec, project_id=project_id)
            record.touch()
            conn.execute(
                "UPDATE jobs SET project_id=?, spec_json=?, updated_at=? WHERE id=?",
                (project_id, _spec_to_json(record.spec), record.updated_at, job_id),
            )
            conn.commit()

    def update_project_id_and_work_dir(self, job_id: str, project_id: str, work_dir: str) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            record = _row_to_record(row)
            record.project_id = project_id
            record.work_dir = work_dir
            record.spec = replace(record.spec, project_id=project_id)
            record.touch()
            conn.execute(
                "UPDATE jobs SET project_id=?, work_dir=?, spec_json=?, updated_at=? WHERE id=?",
                (project_id, work_dir, _spec_to_json(record.spec), record.updated_at, job_id),
            )
            conn.commit()

    def update_work_dir_and_name(self, record: JobRecord) -> None:
        """Persist a task-directory rename and its canonical name together."""
        record.touch()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET work_dir=?, name=?, spec_json=?, updated_at=? WHERE id=?",
                (
                    record.work_dir,
                    record.spec.name,
                    _spec_to_json(record.spec),
                    record.updated_at,
                    record.id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(record.id)
            conn.commit()

    def get_mechanism_study(self, study_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mechanism_studies WHERE id=?",
                (study_id,),
            ).fetchone()
        if row is None:
            return None
        return _mechanism_study_row(row)

    def list_mechanism_studies(
        self, limit: int = 200, job_id: str | None = None
    ) -> list[dict[str, Any]]:
        if job_id is None:
            query = (
                "SELECT * FROM mechanism_studies ORDER BY updated_at DESC, created_at DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (limit,)
        else:
            query = (
                "SELECT * FROM mechanism_studies WHERE job_id=? "
                "ORDER BY updated_at DESC, created_at DESC LIMIT ?"
            )
            params = (job_id, limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_mechanism_study_row(row) for row in rows]

    def get_decision_point(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_points WHERE id=?",
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "study_id": row["study_id"],
            "status": row["status"],
            "payload": row["payload"],
            "resolution": row["resolution"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    def list_decision_points(self, study_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM decision_points
                WHERE study_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (study_id, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "study_id": row["study_id"],
                "status": row["status"],
                "payload": row["payload"],
                "resolution": row["resolution"],
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]


def _record_to_row(record: JobRecord) -> tuple[Any, ...]:
    return (
        record.id,
        record.spec.workflow,
        record.spec.name,
        record.status.value,
        record.work_dir,
        _spec_to_json(record.spec),
        record.created_at,
        record.updated_at,
        record.started_at,
        record.completed_at,
        record.project_id or record.spec.project_id,
        record.input_hash or record.spec.input_hash,
        record.current_stage,
        record.progress,
        record.error,
        record.pid,
        record.exit_code,
        record.remote_job_id,
        record.group_id,
        json.dumps(record.result) if record.result is not None else None,
        record.revision,
        record.attempt,
    )


def _mechanism_study_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "study_json": row["study_json"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "reaction_json": row["reaction_json"] if "reaction_json" in row.keys() else None,
        "mechanism_plan_json": (
            row["mechanism_plan_json"] if "mechanism_plan_json" in row.keys() else None
        ),
        "config_hash": row["config_hash"] if "config_hash" in row.keys() else None,
        "cycle_index": row["cycle_index"] if "cycle_index" in row.keys() else 0,
        "consumed_cycle": row["consumed_cycle"] if "consumed_cycle" in row.keys() else None,
    }


def _row_to_record(row: sqlite3.Row) -> JobRecord:
    spec_raw = json.loads(row["spec_json"])

    if isinstance(spec_raw.get("method"), dict):
        from acp.catalog import normalize_legacy_method

        spec_raw["method"] = normalize_legacy_method(spec_raw["method"])

    columns = set(row.keys())
    project_id = row["project_id"] if "project_id" in columns else spec_raw.get("project_id")
    input_hash = row["input_hash"] if "input_hash" in columns else spec_raw.get("input_hash")
    spec = JobSpec(
        workflow=spec_raw["workflow"],
        name=spec_raw.get("name", ""),
        input=spec_raw.get("input", {}),
        method=spec_raw.get("method", {}),
        resources=spec_raw.get("resources", {}),
        output_dir=spec_raw.get("output_dir"),
        config_path=spec_raw.get("config_path"),
        tags=spec_raw.get("tags", []),
        project_id=spec_raw.get("project_id", project_id),
        input_hash=spec_raw.get("input_hash", input_hash),
        execution_mode=spec_raw.get("execution_mode"),
        target_node=spec_raw.get("target_node"),
        node_tags=spec_raw.get("node_tags", []),
        molecule_name=spec_raw.get("molecule_name", ""),
        task_name=spec_raw.get("task_name", ""),
        remark=spec_raw.get("remark", ""),
    )
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return JobRecord(
        id=row["id"],
        spec=spec,
        status=JobStatus(row["status"]),
        work_dir=row["work_dir"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        current_stage=row["current_stage"],
        progress=row["progress"],
        error=row["error"],
        project_id=project_id,
        input_hash=input_hash,
        pid=row["pid"],
        exit_code=row["exit_code"],
        remote_job_id=row["remote_job_id"] if "remote_job_id" in columns else None,
        group_id=row["group_id"] if "group_id" in columns else None,
        node_id=row["node_id"] if "node_id" in columns else None,
        host=row["host"] if "host" in columns else None,
        result=result,
        revision=int(row["revision"]) if "revision" in columns and row["revision"] is not None else 0,
        attempt=int(row["attempt"]) if "attempt" in columns and row["attempt"] is not None else 1,
    )


def _spec_to_json(spec: JobSpec) -> str:
    return json.dumps(spec.to_dict())


__all__ = ["JobStateConflictError", "JobStore"]
