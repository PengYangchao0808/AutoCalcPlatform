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
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from acp_services.contracts.events import EventScope, OwnerType
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.runtime.idempotency import (
    IDENTITY_VERSION,
    MIGRATION_STATE_NATIVE,
    MIGRATION_STATE_PENDING,
    MIGRATION_STATE_VERIFIED,
    REQUEST_DIGEST_VERSION,
    SCOPE_ORIGIN_UNSPECIFIED,
    SCOPE_ORIGIN_VERIFIED,
    ClaimState,
    IdempotencyClaim,
    IdempotencyConflictError,
    IdempotencyLeaseError,
    IdempotencyOutcome,
    OutcomeKind,
    split_scope_id,
    task_ref_for,
)
from acp_services.runtime.outbox import OutboxEventSpec, OutboxRecord
from acp_services.store.migrations import migrate
from acp_services.store.records import JobRecord, JobSpec, JobStatus

logger = logging.getLogger(__name__)


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

# Transactional outbox (B04-2, todo 23): one row per state-change fact, written
# in the SAME connection/transaction as the ``jobs`` UPDATE that produced it.
# ``delivered_at`` IS NULL == committed-but-not-yet-delivered; the row is acked
# (``delivered_at`` set, ``delivered_seq`` recorded) after the durable JSONL
# append succeeds.  A crash anywhere before the single ``commit()`` rolls the
# row back with the state, so no event can exist for an uncommitted state.
_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_outbox (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    owner_type TEXT NOT NULL DEFAULT 'job',
    scope TEXT NOT NULL DEFAULT 'task',
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    dedup_key TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER,
    emitted_at TEXT NOT NULL,
    delivered_at TEXT,
    delivered_seq INTEGER,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_outbox_pending ON job_outbox(delivered_at);
CREATE INDEX IF NOT EXISTS idx_job_outbox_job ON job_outbox(job_id, delivered_at);
"""


@dataclass(frozen=True)
class CursorSnapshot:
    """A job's revision read together with its event cursor and pending count.

    ``cursor`` is the ``after_seq``-compatible sequence of the last delivered
    event; ``pending`` counts committed-but-undelivered facts.  A subscriber
    replays from ``cursor`` and will receive any later event exactly once, so a
    snapshot taken before subscribing cannot lose a committed update.
    """

    record: JobRecord
    cursor: int
    pending: int


class JobStore:
    """Thread-safe SQLite persistence for :class:`JobRecord`."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Test-only crash-injection seam: a callable invoked at named points
        # inside the transition transaction.  Raising from it models a crash at
        # that point (the open SQLite transaction rolls back).  ``None`` in
        # production.
        self._fault_point_hook: Callable[[str], None] | None = None
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            conn.executescript(_OUTBOX_SCHEMA)
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
        outbox: OutboxEventSpec | Sequence[OutboxEventSpec] | None = None,
        **fields: Any,
    ) -> JobRecord:
        """CAS single-row state update: one UPDATE guarded by revision/status/attempt.

        Only whitelisted fields are written (never ``spec_json``); ``revision``
        increments and the fresh record is read back on the same connection.
        Raises :class:`JobStateConflictError` when the row does not match the
        expectation, and :class:`ValueError` for unknown or forbidden fields.

        When ``outbox`` is given, the event row(s) are inserted on the **same
        connection before the single ``commit()``** as the state UPDATE — so a
        failed CAS guard, or a crash before the commit, rolls both back together
        and no event can exist for an uncommitted state.  Delivery of committed
        rows is a separate post-commit step (see
        :mod:`acp_services.runtime.outbox`).
        """
        unknown = sorted(set(fields) - self._TRANSITION_FIELDS)
        if unknown:
            raise ValueError(f"transition does not allow field(s): {', '.join(unknown)}")

        outbox_specs = _normalize_outbox(outbox)
        new_revision = expected_revision + 1

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
            self._fault_point("transition.after_update")
            if outbox_specs:
                self._insert_outbox_rows(conn, job_id, outbox_specs, new_revision)
            self._fault_point("transition.before_commit")
            conn.commit()
            self._fault_point("transition.after_commit")
            return _row_to_record(row)

    def _fault_point(self, name: str) -> None:
        """Invoke the test-only crash-injection hook at *name* (no-op when unset)."""
        hook = self._fault_point_hook
        if hook is not None:
            hook(name)

    def _insert_outbox_rows(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        specs: Sequence[OutboxEventSpec],
        new_revision: int,
    ) -> None:
        """INSERT the staged events on *conn* (same transaction as the UPDATE)."""
        now = _utc_now_iso()
        for spec in specs:
            dedup_key = spec.dedup_key or f"{job_id}:{new_revision}:{spec.event_type}"
            event_id = spec.event_id or dedup_key
            conn.execute(
                """INSERT OR IGNORE INTO job_outbox (
                       event_id, job_id, owner_type, scope, event_type, payload_json,
                       dedup_key, revision, attempt, emitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    job_id,
                    spec.owner_type.value,
                    spec.scope.value,
                    spec.event_type,
                    json.dumps(dict(spec.payload), default=str, sort_keys=True),
                    dedup_key,
                    new_revision,
                    spec.attempt,
                    spec.emitted_at or now,
                ),
            )

    def pending_outbox(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> list[OutboxRecord]:
        """Committed-but-undelivered outbox rows, oldest first."""
        clauses = ["delivered_at IS NULL"]
        params: list[Any] = []
        if job_id is not None:
            clauses.append("job_id=?")
            params.append(job_id)
        query = f"SELECT * FROM job_outbox WHERE {' AND '.join(clauses)} ORDER BY rowid"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_outbox_row_to_record(row) for row in rows]

    def has_pending_outbox(self, *, job_id: str | None = None) -> bool:
        """True when at least one committed fact is still awaiting delivery."""
        clause = "delivered_at IS NULL"
        params: tuple[Any, ...] = ()
        if job_id is not None:
            clause += " AND job_id=?"
            params = (job_id,)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM job_outbox WHERE {clause} LIMIT 1", params
            ).fetchone()
        return row is not None

    def get_outbox_event(self, event_id: str) -> OutboxRecord | None:
        """Read one outbox row by its stable id (delivery diagnostics/tests)."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM job_outbox WHERE event_id=?", (event_id,)).fetchone()
        return _outbox_row_to_record(row) if row is not None else None

    def mark_outbox_delivered(
        self, event_id: str, *, seq: int, delivered_at: str | None = None
    ) -> bool:
        """Ack one delivered row; idempotent (True only for the first ack)."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE job_outbox SET delivered_at=?, delivered_seq=?, last_error=NULL "
                "WHERE event_id=? AND delivered_at IS NULL",
                (delivered_at or _utc_now_iso(), seq, event_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        """Record a delivery failure; the row stays pending for the next sweep."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE job_outbox SET delivery_attempts=delivery_attempts+1, last_error=? "
                "WHERE event_id=? AND delivered_at IS NULL",
                (error, event_id),
            )
            conn.commit()

    def snapshot_with_cursor(self, job_id: str) -> CursorSnapshot | None:
        """Read a job's revision, delivered-event cursor and pending count together.

        Both SQLite reads happen on one connection, so a subscriber that replays
        from ``cursor`` after this snapshot can never lose a committed update:
        any later transition is a durable outbox row that will be delivered at a
        sequence strictly greater than ``cursor``.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return None
            aggregate = conn.execute(
                "SELECT COALESCE(MAX(delivered_seq), 0) AS cursor, "
                "SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS pending "
                "FROM job_outbox WHERE job_id=?",
                (job_id,),
            ).fetchone()
        cursor = int(aggregate["cursor"] or 0) if aggregate is not None else 0
        pending = int(aggregate["pending"] or 0) if aggregate is not None else 0
        return CursorSnapshot(record=_row_to_record(row), cursor=cursor, pending=pending)

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

    def update_execution_identity(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_attempt: int | None = None,
        result: dict[str, Any],
        node_id: str | None,
        host: str | None,
    ) -> JobRecord:
        """CAS narrow identity write: ``node_id``/``host`` + ``result`` payload.

        ``node_id``/``host`` sit outside :meth:`transition`'s progress
        whitelist, so execution-target provenance (manager
        ``_record_execution_target``) gets this dedicated conditional API
        instead of a whole-row update (plan todo 3).
        """
        assignments = [
            "revision=revision+1",
            "updated_at=?",
            "result_json=?",
            "node_id=?",
            "host=?",
        ]
        params: list[Any] = [_utc_now_iso(), _encode_field("result", result), node_id, host]
        where = "id=? AND revision=?"
        args: list[Any] = [job_id, expected_revision]
        expected: dict[str, Any] = {"revision": expected_revision}
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
            raise TypeError(f"requeue_with_spec requires a JobSpec, got {type(new_spec).__name__}")
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

    def referenced_code_releases(self) -> set[str]:
        """Release ids referenced by jobs in **any** status (D03).

        Scans every ``result_json`` for a ``remote.code_release`` binding
        (written pre-bsub by the submit-intent path).  Used by
        ``prune_releases`` *inside* the node coordination lock so the
        eligibility check never runs against a stale caller snapshot — a
        release bound to a QUEUED/FAILED/RUNNING/... job must never be
        reclaimed while that binding exists.
        """
        out: set[str] = set()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT result_json FROM jobs WHERE result_json IS NOT NULL"
            ).fetchall()
        for r in rows:
            raw = r["result_json"]
            if not raw:
                continue
            try:
                result = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(result, dict):
                continue
            top = result.get("code_release")
            if isinstance(top, str) and top:
                out.add(top)
            remote = result.get("remote")
            if isinstance(remote, dict):
                release = remote.get("code_release")
                if isinstance(release, str) and release:
                    out.add(release)
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

    def claim_idempotency(
        self,
        scope_id: str,
        action: str,
        key: str,
        request_digest: str,
        *,
        lease_seconds: float = 60.0,
        now: str | None = None,
        principal_id: str | None = None,
        project_id: str | None = None,
        identity_version: str = IDENTITY_VERSION,
        request_digest_version: str = REQUEST_DIGEST_VERSION,
    ) -> IdempotencyOutcome:
        """Claim one ``(scope, action, key)`` or report the durable outcome.

        The lookup, the cross-scope guard, the insert/takeover CAS and the
        commit all happen in **one short** ``BEGIN IMMEDIATE`` transaction.
        A live claim owned by someone else yields ``in_progress`` (never a
        second reservation); an expired/released claim is taken over with an
        epoch CAS that replaces ``owner_token``.  A committed claim replays
        its stored TaskRef; a digest/scope mismatch is a conflict.

        ``principal_id``/``project_id`` attribution (M0-04): they are the
        verified identity behind ``scope_id`` and are persisted on the row for
        reconciliation.  When omitted they are split from the scope id, so a
        caller that only knows the scope id still records a truthful
        attribution.

        Legacy rows (``identity_version = "1"``, written before M0-04) are
        never adopted silently: an unattributable row is a typed ``conflict``
        (reconcile, do not resubmit with a new key), while a verifiable row
        replays/takes over under the normal same-scope rules.
        """
        scope = str(scope_id or "").strip()
        act = str(action or "").strip()
        idem_key = str(key or "").strip()
        digest = str(request_digest or "").strip()
        if not scope or not act or not idem_key or not digest:
            raise ValueError("scope_id, action, key and request_digest are required")
        scope_principal, scope_project = split_scope_id(scope)
        row_principal = str(principal_id or "").strip() or scope_principal
        row_project = str(project_id or "").strip() or scope_project
        ver = str(identity_version or IDENTITY_VERSION)
        digest_ver = str(request_digest_version or REQUEST_DIGEST_VERSION)
        now_iso = now or _utc_now_iso()
        expires = _lease_expiry(now_iso, lease_seconds)
        origin = SCOPE_ORIGIN_VERIFIED if row_principal else SCOPE_ORIGIN_UNSPECIFIED

        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM idempotency_records "
                    "WHERE scope_id=? AND action=? AND idempotency_key=?",
                    (scope, act, idem_key),
                ).fetchone()
                if row is None:
                    other = conn.execute(
                        "SELECT scope_id FROM idempotency_records "
                        "WHERE action=? AND idempotency_key=? LIMIT 1",
                        (act, idem_key),
                    ).fetchone()
                    if other is not None:
                        conn.execute("ROLLBACK")
                        return IdempotencyOutcome(
                            OutcomeKind.CONFLICT.value,
                            detail=(
                                "idempotency key was already used under a different verified scope"
                            ),
                        )
                    claim_id = "idem_" + uuid.uuid4().hex
                    owner_token = uuid.uuid4().hex
                    conn.execute(
                        """INSERT INTO idempotency_records (
                               claim_id, scope_id, action, idempotency_key,
                               request_digest, state, owner_token, lease_epoch,
                               lease_expires_at, task_id, operation_id,
                               created_at, updated_at, principal_id, project_id,
                               identity_version, request_digest_version,
                               scope_origin, migration_state)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                                   ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            claim_id,
                            scope,
                            act,
                            idem_key,
                            digest,
                            ClaimState.CLAIMED.value,
                            owner_token,
                            0,
                            expires,
                            now_iso,
                            now_iso,
                            row_principal,
                            row_project,
                            ver,
                            digest_ver,
                            origin,
                            MIGRATION_STATE_NATIVE,
                        ),
                    )
                    fresh = conn.execute(
                        "SELECT * FROM idempotency_records WHERE claim_id=?",
                        (claim_id,),
                    ).fetchone()
                    conn.commit()
                    return IdempotencyOutcome(
                        OutcomeKind.CREATED.value,
                        claim=_row_to_idempotency_claim(fresh),
                    )

                claim = _row_to_idempotency_claim(row)
                if claim.request_digest_version != digest_ver:
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.CONFLICT.value,
                        claim=claim,
                        detail=(
                            "idempotency claim was recorded under request digest version "
                            f"{claim.request_digest_version!r}; reconcile instead of resubmitting"
                        ),
                    )
                if claim.is_legacy and claim.migration_state not in {
                    MIGRATION_STATE_VERIFIED,
                    MIGRATION_STATE_PENDING,
                }:
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.CONFLICT.value,
                        claim=claim,
                        detail=(
                            "legacy idempotency claim cannot be attributed to a verified "
                            "principal; reconcile instead of resubmitting"
                        ),
                    )
                if claim.request_digest != digest:
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.CONFLICT.value,
                        claim=claim,
                        detail="idempotency key reused with a different request payload",
                    )
                if claim.state == ClaimState.COMMITTED.value:
                    task_ref = _task_ref_from_connection(conn, claim.task_id)
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.REPLAY.value,
                        claim=claim,
                        task_ref=task_ref,
                    )
                if claim.state == ClaimState.REJECTED.value:
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.CONFLICT.value,
                        claim=claim,
                        detail="idempotency claim was rejected by admission",
                    )
                if claim.state == ClaimState.CLAIMED.value and _lease_is_live(
                    claim.lease_expires_at, now_iso
                ):
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.IN_PROGRESS.value,
                        claim=_claim_without_owner(claim),
                        retryable=True,
                        detail="another owner holds a live idempotency lease",
                    )

                previous_epoch = claim.lease_epoch
                owner_token = uuid.uuid4().hex
                cursor = conn.execute(
                    """UPDATE idempotency_records
                       SET state=?, owner_token=?, lease_epoch=?, lease_expires_at=?,
                           updated_at=?
                       WHERE claim_id=? AND state=? AND lease_epoch=?""",
                    (
                        ClaimState.CLAIMED.value,
                        owner_token,
                        previous_epoch + 1,
                        expires,
                        now_iso,
                        claim.claim_id,
                        claim.state,
                        previous_epoch,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    return IdempotencyOutcome(
                        OutcomeKind.IN_PROGRESS.value,
                        claim=_claim_without_owner(claim),
                        retryable=True,
                        detail="idempotency takeover lost the epoch CAS",
                    )
                taken = conn.execute(
                    "SELECT * FROM idempotency_records WHERE claim_id=?",
                    (claim.claim_id,),
                ).fetchone()
                conn.commit()
                return IdempotencyOutcome(
                    OutcomeKind.CREATED.value,
                    claim=_row_to_idempotency_claim(taken),
                    detail="takeover",
                )
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def finalize_idempotent_job(
        self,
        record: JobRecord,
        claim_id: str,
        owner_token: str,
        lease_epoch: int,
        *,
        now: str | None = None,
    ) -> IdempotencyOutcome:
        """Commit the claim and insert the QUEUED job row in one short transaction.

        The claim row is re-checked (state, owner token, epoch, live lease)
        before the job INSERT.  A stale owner or an expired lease raises
        :class:`IdempotencyLeaseError`; nothing is written.  The committed
        ``QUEUED`` row is the durable dispatch intent.
        """
        now_iso = now or _utc_now_iso()
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM idempotency_records WHERE claim_id=?",
                    (str(claim_id),),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError(f"unknown idempotency claim {claim_id!r}")
                claim = _row_to_idempotency_claim(row)
                if claim.state == ClaimState.COMMITTED.value:
                    if claim.task_id == record.id:
                        task_ref = _task_ref_from_connection(conn, claim.task_id)
                        conn.execute("ROLLBACK")
                        return IdempotencyOutcome(
                            OutcomeKind.REPLAY.value,
                            claim=claim,
                            task_ref=task_ref,
                        )
                    conn.execute("ROLLBACK")
                    raise IdempotencyConflictError(
                        "idempotency claim is already committed to another task"
                    )
                if claim.state != ClaimState.CLAIMED.value:
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError(
                        f"idempotency claim is not owned (state={claim.state})"
                    )
                if claim.owner_token != str(owner_token):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError("stale owner token for idempotency claim")
                if int(claim.lease_epoch) != int(lease_epoch):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError(
                        "stale lease epoch for idempotency claim "
                        f"({claim.lease_epoch} != {lease_epoch})"
                    )
                if not _lease_is_live(claim.lease_expires_at, now_iso):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError("idempotency lease expired before finalize")
                if conn.execute("SELECT 1 FROM jobs WHERE id=?", (record.id,)).fetchone():
                    conn.execute("ROLLBACK")
                    raise IdempotencyConflictError(
                        f"job {record.id!r} already exists; idempotent finalize refuses"
                    )

                conn.execute(
                    """INSERT INTO jobs (id, workflow, name, status, work_dir, spec_json,
                           created_at, updated_at, started_at, completed_at, project_id,
                           input_hash, current_stage, progress, error, pid, exit_code,
                           remote_job_id, group_id, result_json, revision, attempt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _record_to_row(record),
                )
                cursor = conn.execute(
                    """UPDATE idempotency_records
                       SET state=?, task_id=?, updated_at=?
                       WHERE claim_id=? AND owner_token=? AND lease_epoch=? AND state=?""",
                    (
                        ClaimState.COMMITTED.value,
                        record.id,
                        now_iso,
                        claim.claim_id,
                        str(owner_token),
                        int(lease_epoch),
                        ClaimState.CLAIMED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError(
                        "idempotency claim lost the finalize CAS (stale owner/epoch)"
                    )
                committed = conn.execute(
                    "SELECT * FROM idempotency_records WHERE claim_id=?",
                    (claim.claim_id,),
                ).fetchone()
                conn.commit()
                committed_claim = _row_to_idempotency_claim(committed)
                return IdempotencyOutcome(
                    OutcomeKind.CREATED.value,
                    claim=committed_claim,
                    task_ref=task_ref_for(committed_claim, attempt=record.attempt),
                )
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def release_idempotency(
        self,
        claim_id: str,
        *,
        owner_token: str | None = None,
        lease_epoch: int | None = None,
        state: str = ClaimState.RELEASED.value,
        now: str | None = None,
    ) -> bool:
        """Release/reject/expire a *claimed* row with owner fencing.

        Only ``claimed`` rows transition.  A committed row is immutable (the
        job lifecycle owns it).  When an owner token/epoch is supplied it must
        match, so a stale owner can never release the new owner's claim.
        """
        if state not in {
            ClaimState.RELEASED.value,
            ClaimState.REJECTED.value,
            ClaimState.EXPIRED.value,
        }:
            raise ValueError(f"invalid release state {state!r}")
        now_iso = now or _utc_now_iso()
        clauses = ["claim_id=?", "state=?"]
        params: list[Any] = [str(claim_id), ClaimState.CLAIMED.value]
        if owner_token is not None:
            clauses.append("owner_token=?")
            params.append(str(owner_token))
        if lease_epoch is not None:
            clauses.append("lease_epoch=?")
            params.append(int(lease_epoch))
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT state, owner_token, lease_epoch, lease_expires_at "
                    "FROM idempotency_records WHERE claim_id=?",
                    (str(claim_id),),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return False
                if row["state"] == ClaimState.COMMITTED.value:
                    conn.execute("ROLLBACK")
                    return False
                if row["state"] != ClaimState.CLAIMED.value:
                    conn.execute("ROLLBACK")
                    return False
                if owner_token is None and _lease_is_live(row["lease_expires_at"], now_iso):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError(
                        "owner token is required while the idempotency lease is live"
                    )
                if owner_token is not None and row["owner_token"] != str(owner_token):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError("stale owner token for idempotency release")
                if lease_epoch is not None and int(row["lease_epoch"]) != int(lease_epoch):
                    conn.execute("ROLLBACK")
                    raise IdempotencyLeaseError("stale lease epoch for idempotency release")
                cursor = conn.execute(
                    f"""UPDATE idempotency_records SET state=?, updated_at=?
                        WHERE {" AND ".join(clauses)}""",
                    (state, now_iso, *params),
                )
                conn.commit()
                return cursor.rowcount == 1
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def expire_stale_idempotency_claims(self, *, now: str | None = None) -> list[IdempotencyClaim]:
        """CAS-expire every ``claimed`` row whose lease has elapsed.

        Returns the expired claims so the caller can remove the claim-keyed
        staging directory and sweep its orphan reservation exactly once.
        """
        now_iso = now or _utc_now_iso()
        expired: list[IdempotencyClaim] = []
        with self._lock, self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT * FROM idempotency_records WHERE state=?",
                    (ClaimState.CLAIMED.value,),
                ).fetchall()
                for row in rows:
                    claim = _row_to_idempotency_claim(row)
                    if _lease_is_live(claim.lease_expires_at, now_iso):
                        continue
                    cursor = conn.execute(
                        """UPDATE idempotency_records SET state=?, updated_at=?
                           WHERE claim_id=? AND state=? AND lease_epoch=?""",
                        (
                            ClaimState.EXPIRED.value,
                            now_iso,
                            claim.claim_id,
                            ClaimState.CLAIMED.value,
                            claim.lease_epoch,
                        ),
                    )
                    if cursor.rowcount == 1:
                        expired.append(
                            replace(claim, state=ClaimState.EXPIRED.value, updated_at=now_iso)
                        )
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return expired

    def get_idempotency_claim(self, claim_id: str) -> IdempotencyClaim | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE claim_id=?",
                (str(claim_id),),
            ).fetchone()
        return None if row is None else _row_to_idempotency_claim(row)

    def find_idempotency_claim(
        self, scope_id: str, action: str, key: str
    ) -> IdempotencyClaim | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records "
                "WHERE scope_id=? AND action=? AND idempotency_key=?",
                (str(scope_id), str(action), str(key)),
            ).fetchone()
        return None if row is None else _row_to_idempotency_claim(row)

    def find_idempotency_claim_any_scope(self, action: str, key: str) -> IdempotencyClaim | None:
        """Return the first claim for ``(action, key)`` across every scope."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records "
                "WHERE action=? AND idempotency_key=? ORDER BY created_at LIMIT 1",
                (str(action), str(key)),
            ).fetchone()
        return None if row is None else _row_to_idempotency_claim(row)

    def get_idempotency_claim_for_task(self, task_id: str) -> IdempotencyClaim | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE task_id=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(task_id),),
            ).fetchone()
        return None if row is None else _row_to_idempotency_claim(row)

    def list_idempotency_claims(
        self, *, state: str | None = None, limit: int = 1000
    ) -> list[IdempotencyClaim]:
        query = "SELECT * FROM idempotency_records"
        params: list[Any] = []
        if state is not None:
            query += " WHERE state=?"
            params.append(str(state))
        query += " ORDER BY created_at, claim_id LIMIT ?"
        params.append(int(limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_idempotency_claim(row) for row in rows]


def _lease_expiry(now_iso: str, seconds: float) -> str:
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    return (now + timedelta(seconds=float(seconds))).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _lease_is_live(expires_at: str | None, now_iso: str) -> bool:
    expires = _parse_iso(expires_at)
    now = _parse_iso(now_iso)
    if expires is None or now is None:
        return False
    return expires > now


def _claim_without_owner(claim: IdempotencyClaim) -> IdempotencyClaim:
    """A non-owner view of an in-progress claim (never leaks the owner token)."""
    return replace(claim, owner_token=None)


def _row_to_idempotency_claim(row: sqlite3.Row) -> IdempotencyClaim:
    keys = row.keys()

    def _optional(name: str, default: Any) -> Any:
        return row[name] if name in keys and row[name] is not None else default

    return IdempotencyClaim(
        claim_id=row["claim_id"],
        scope_id=row["scope_id"],
        action=row["action"],
        idempotency_key=row["idempotency_key"],
        request_digest=row["request_digest"],
        state=row["state"],
        owner_token=row["owner_token"],
        lease_epoch=int(row["lease_epoch"] or 0),
        lease_expires_at=row["lease_expires_at"],
        task_id=row["task_id"],
        operation_id=row["operation_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        principal_id=_optional("principal_id", None),
        project_id=_optional("project_id", None),
        identity_version=str(_optional("identity_version", IDENTITY_VERSION)),
        request_digest_version=str(_optional("request_digest_version", REQUEST_DIGEST_VERSION)),
        scope_origin=str(_optional("scope_origin", SCOPE_ORIGIN_UNSPECIFIED)),
        migration_state=str(_optional("migration_state", MIGRATION_STATE_NATIVE)),
    )


def _task_ref_from_connection(
    conn: sqlite3.Connection, task_id: str | None
) -> dict[str, Any] | None:
    """Build the durable TaskRef from the committed jobs row."""
    if not task_id:
        return None
    row = conn.execute(
        "SELECT id, attempt, result_json FROM jobs WHERE id=?", (str(task_id),)
    ).fetchone()
    if row is None:
        return {"task_id": str(task_id), "job_id": str(task_id), "attempt": 1}
    ref: dict[str, Any] = {
        "task_id": row["id"],
        "job_id": row["id"],
        "attempt": int(row["attempt"] or 1),
    }
    raw = row["result_json"]
    if raw:
        try:
            result = json.loads(raw)
        except (TypeError, ValueError):
            result = None
        release = result.get("release") if isinstance(result, dict) else None
        if isinstance(release, dict) and release.get("release_id"):
            ref["release"] = {
                "release_id": release.get("release_id"),
                "release_digest": release.get("release_digest"),
            }
    return ref


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
        from acp_services.store.legacy_method import normalize_legacy_method

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
        revision=int(row["revision"])
        if "revision" in columns and row["revision"] is not None
        else 0,
        attempt=int(row["attempt"]) if "attempt" in columns and row["attempt"] is not None else 1,
    )


def _spec_to_json(spec: JobSpec) -> str:
    return json.dumps(spec.to_dict())


def row_to_record(row: sqlite3.Row) -> JobRecord:
    """Decode one ``jobs`` row into a :class:`JobRecord`.

    Public companion to :meth:`JobStore.get` for readers that own a
    connection to the scheduler DB (the task projection projects from the
    authoritative jobs row without opening a second connection).
    """
    return _row_to_record(row)


def _normalize_outbox(
    outbox: OutboxEventSpec | Sequence[OutboxEventSpec] | None,
) -> tuple[OutboxEventSpec, ...]:
    if outbox is None:
        return ()
    if isinstance(outbox, OutboxEventSpec):
        return (outbox,)
    specs = tuple(outbox)
    for spec in specs:
        if not isinstance(spec, OutboxEventSpec):
            raise TypeError(f"outbox entries must be OutboxEventSpec, got {type(spec).__name__}")
    return specs


def _outbox_row_to_record(row: sqlite3.Row) -> OutboxRecord:
    columns = set(row.keys())
    payload_raw = row["payload_json"] if "payload_json" in columns else None
    payload: dict[str, Any] = {}
    if payload_raw:
        decoded = json.loads(payload_raw)
        if isinstance(decoded, dict):
            payload = decoded
    return OutboxRecord(
        event_id=row["event_id"],
        owner_id=row["job_id"],
        owner_type=OwnerType(row["owner_type"] if "owner_type" in columns else OwnerType.JOB.value),
        scope=EventScope(row["scope"] if "scope" in columns else EventScope.TASK.value),
        event_type=row["event_type"],
        payload=payload,
        dedup_key=row["dedup_key"],
        revision=int(row["revision"] or 0) if "revision" in columns else 0,
        attempt=row["attempt"] if "attempt" in columns else None,
        emitted_at=(row["emitted_at"] if "emitted_at" in columns else "") or "",
        delivered_at=row["delivered_at"] if "delivered_at" in columns else None,
        delivered_seq=row["delivered_seq"] if "delivered_seq" in columns else None,
        delivery_attempts=(
            int(row["delivery_attempts"] or 0) if "delivery_attempts" in columns else 0
        ),
        last_error=(row["last_error"] if "last_error" in columns else "") or "",
    )


__all__ = ["CursorSnapshot", "JobStateConflictError", "JobStore", "row_to_record"]
