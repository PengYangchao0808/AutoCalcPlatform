# pyright: reportAny=false, reportUnusedCallResult=false
"""
Scheduler Task Index
====================

Server-side SQLite index of v2 task metadata (design doc
``ACP_Project_Task_Storage_Design_v2.md`` §9.1 fields + §9.3 node-path
mapping).  One row per scheduler job (``task_id == job_id``), written at
submit time and refreshed on status transitions — heavy files stay on the
compute node.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord
from acp.scheduler.migrations import migrate
from acp.scheduler.store import row_to_record

logger = logging.getLogger(__name__)

#: Batch size B for one task-projection reconcile scan. Drifted rows leave the
#: drift set once repaired, so N drifted rows converge in <= ceil(N/B) scans.
PROJECTION_RECONCILE_BATCH: int = 200

#: Authoritative projection columns compared by the drift query. ``updated_at``
#: and ``last_activity_at`` are deliberately excluded: they change on every sync
#: and are not part of the state that must agree with jobs.
_PROJECTION_DRIFT_SQL = """
    SELECT j.* FROM jobs j
    LEFT JOIN tasks t ON t.task_id = j.id
    WHERE t.task_id IS NULL
       OR COALESCE(t.status, '') <> COALESCE(j.status, '')
       OR COALESCE(t.current_stage, '') <> COALESCE(j.current_stage, '')
       OR COALESCE(t.progress, -1.0) <> COALESCE(j.progress, -1.0)
    ORDER BY j.id
    LIMIT ?
"""

_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    project_id TEXT,
    molecule_name TEXT NOT NULL DEFAULT '',
    task_name TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    task_dir_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    node_id TEXT,
    node_path TEXT,
    input_hash TEXT,
    result_manifest_path TEXT,
    current_stage TEXT,
    storage_mode TEXT NOT NULL DEFAULT 'local',
    layout_version INTEGER NOT NULL DEFAULT 2,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    custom_name TEXT,
    name_revision INTEGER NOT NULL DEFAULT 0,
    name_updated_at TEXT
)
"""

#: §9.1 fields + §9.3 mapping fields, in column order.
_TASK_COLUMNS: tuple[str, ...] = (
    "task_id",
    "job_id",
    "project_id",
    "molecule_name",
    "task_name",
    "remark",
    "display_name",
    "workflow",
    "task_dir_name",
    "status",
    "node_id",
    "node_path",
    "input_hash",
    "result_manifest_path",
    "current_stage",
    "storage_mode",
    "layout_version",
    "created_at",
    "updated_at",
    # T1 org columns
    "molecule_key",
    "tags",
    "archived",
    "batch_id",
    "last_activity_at",
    "started_at",
    "completed_at",
    "group_id",
    "progress",
    # custom-name columns (Wave 1)
    "custom_name",
    "name_revision",
    "name_updated_at",
)

#: Mirrors the SQL column defaults for keys absent (or None) in the payload.
_COLUMN_DEFAULTS: dict[str, Any] = {
    "status": "pending",
    "storage_mode": "local",
    "layout_version": 2,
    "molecule_key": "",
    "tags": "[]",
    "archived": 0,
    "batch_id": None,
    "last_activity_at": None,
    "started_at": None,
    "completed_at": None,
    "group_id": None,
    "progress": None,
    "custom_name": None,
    "name_revision": 0,
    "name_updated_at": None,
}


#: Columns that sync_from_job / sync_job_transition own (updated on conflict).
_SYNC_COLUMNS: tuple[str, ...] = (
    "display_name",
    "task_dir_name",
    "workflow",
    "status",
    "current_stage",
    "node_id",
    "node_path",
    "storage_mode",
    "layout_version",
    "input_hash",
    "result_manifest_path",
    "updated_at",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NameRevisionConflictError(Exception):
    """Raised when an update_custom_name call has a stale expected_name_revision."""

    def __init__(self, current_projection: dict[str, Any]) -> None:
        self.current_projection = current_projection
        rev = current_projection.get("name_revision")
        super().__init__(f"name_revision conflict — current is {rev}")


def validate_custom_name(value: str | None) -> str | None:
    """Validate and normalise a custom task name.

    ``None`` passes through (restore default).  Otherwise: strip, reject
    empty, reject length > 200, reject control characters (Unicode Cc).
    Returns the stripped value or raises ``ValueError``.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("custom_name must not be empty after trimming")
    if len(stripped) > 200:
        raise ValueError(f"custom_name exceeds 200 characters (got {len(stripped)})")
    for ch in stripped:
        if unicodedata.category(ch) == "Cc":
            raise ValueError(f"custom_name contains control character: {ch!r}")
    return stripped


def resolve_task_names(row_or_dict: dict[str, Any]) -> dict[str, Any]:
    """Produce a name projection from a task row.

    Returns ``default_name`` (display_name), ``resolved_name``
    (custom_name or display_name), ``custom_name``, ``name_revision``,
    and ``name_updated_at``.
    """
    display_name = row_or_dict.get("display_name") or ""
    custom_name = row_or_dict.get("custom_name")
    return {
        "default_name": display_name,
        "resolved_name": custom_name if custom_name else display_name,
        "custom_name": custom_name,
        "name_revision": row_or_dict.get("name_revision") or 0,
        "name_updated_at": row_or_dict.get("name_updated_at"),
    }


def jobs_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True when the scheduler ``jobs`` table exists in *conn*.

    Shared probe for ghost-entry guards: the task index normally lives in
    the scheduler DB next to ``jobs`` (so task rows can be validated
    against it), but a standalone index DB has no ``jobs`` table and
    cannot be validated.
    """
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
    return row is not None


class TaskIndex:
    """Thread-safe SQLite index of task rows over the scheduler DB file.

    Mirrors the :class:`~acp.scheduler.jobs.JobStore` connection pattern
    (per-call connections guarded by a lock); a shared connection may be
    supplied instead of a path.

    Lock discipline: ``_lock`` is a **non-reentrant** :class:`threading.Lock`.
    Any helper called while it is held must use the caller's already-open
    connection and must never call ``query_rows``/``_run``/``upsert``/
    ``_query``/``writer_connection`` (or anything else that acquires the lock).
    ``_project_transition``/``_payload_from_record`` therefore receive the
    held ``conn`` explicitly; re-entering the lock in the same thread is a
    permanent self-deadlock.
    """

    def __init__(self, conn_or_path: sqlite3.Connection | Path | str):
        if isinstance(conn_or_path, sqlite3.Connection):
            self._shared_conn: sqlite3.Connection | None = conn_or_path
            self.db_path: Path | None = None
        else:
            self._shared_conn = None
            self.db_path = Path(conn_or_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            self._shared_conn.row_factory = sqlite3.Row
            return self._shared_conn
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _run(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Execute one write statement under the lock and commit."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                if self._shared_conn is None:
                    conn.close()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(sql, params).fetchall()
            finally:
                if self._shared_conn is None:
                    conn.close()

    # ------------------------------------------------------------------ #
    # Public accessors (used by molecule_groups / task_views)
    # ------------------------------------------------------------------ #

    def query_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Execute a read query and return all rows (thread-safe)."""
        return self._query(sql, params)

    @contextmanager
    def writer_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection for batch writes; closes only if not shared."""
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                if self._shared_conn is None:
                    conn.close()

    def _init_schema(self) -> None:
        self._run(_TASKS_SCHEMA)
        if self.db_path is not None:
            migrate(self.db_path)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def _normalize_row(self, record: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for col in _TASK_COLUMNS:
            value = record.get(col)
            row[col] = _COLUMN_DEFAULTS.get(col, "") if value is None else value
        try:
            row["layout_version"] = int(row["layout_version"])
        except (TypeError, ValueError):
            row["layout_version"] = 2
        return row

    def _upsert_conn(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        row = self._normalize_row(record)
        columns = ", ".join(_TASK_COLUMNS)
        placeholders = ", ".join("?" for _ in _TASK_COLUMNS)
        update_parts = ", ".join(f"{c}=excluded.{c}" for c in _SYNC_COLUMNS)
        conn.execute(
            f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(task_id) DO UPDATE SET {update_parts}",
            tuple(row[col] for col in _TASK_COLUMNS),
        )

    def upsert(self, record: dict[str, Any]) -> None:
        """Insert or update a task row keyed by ``task_id``.

        First-write-wins columns (project_id, molecule_name, task_name,
        remark, molecule_key, tags, archived, batch_id, created_at) are
        only set on INSERT.  Sync-owned columns are updated on conflict.
        """
        with self._lock:
            conn = self._connect()
            try:
                self._upsert_conn(conn, record)
                conn.commit()
            finally:
                if self._shared_conn is None:
                    conn.close()

    def get(self, task_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        return dict(rows[0]) if rows else None

    def list_by_project(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
        return [dict(r) for r in rows]

    def update_status(
        self,
        task_id: str,
        status: str,
        current_stage: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        """Refresh ``status`` (and optionally ``current_stage``); no-op if absent."""
        ts = updated_at or _utc_now_iso()
        if current_stage is None:
            self._run(
                "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
                (status, ts, task_id),
            )
        else:
            self._run(
                "UPDATE tasks SET status=?, current_stage=?, updated_at=? WHERE task_id=?",
                (status, current_stage, ts, task_id),
            )

    # ------------------------------------------------------------------ #
    # JobRecord mirroring
    # ------------------------------------------------------------------ #

    def _payload_from_record(
        self,
        record: JobRecord,
        layout_version: int = 2,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Build the projection payload for *record*.

        When *conn* is supplied the caller already holds ``self._lock`` and an
        open connection (the ``sync_job_transition``/``reconcile_projection``
        path); the alias-aware ``molecule_key`` is then resolved through that
        connection so no lock-taking helper is re-entered. ``_lock`` is
        non-reentrant — see the :class:`TaskIndex` lock-discipline note.
        """
        remote = bool(record.remote_job_id)
        result = record.result if isinstance(record.result, dict) else {}
        node = result.get("node") or result.get("execution_target")
        if not isinstance(node, str) or not node:
            node = "remote" if remote else "local"

        tags_list = record.spec.tags or []
        tags_json = json.dumps(tags_list)
        batch_id = (
            record.spec.resources.get("batch_id")
            if isinstance(record.spec.resources, dict)
            else None
        )
        last_activity_at = record.completed_at or record.started_at or record.created_at
        project_id = record.project_id or record.spec.project_id
        return {
            "task_id": record.id,
            "job_id": record.id,
            "project_id": project_id,
            "molecule_name": record.spec.molecule_name,
            "task_name": record.spec.task_name,
            "remark": record.spec.remark,
            "display_name": Path(record.work_dir).name if record.work_dir else record.spec.name,
            "workflow": record.spec.workflow,
            "task_dir_name": Path(record.work_dir).name if record.work_dir else "",
            "status": record.status.value,
            "node_id": node,
            "node_path": record.work_dir,
            "input_hash": record.input_hash or record.spec.input_hash,
            "result_manifest_path": None,
            "current_stage": record.current_stage,
            "storage_mode": "sftp" if remote else "local",
            "layout_version": layout_version,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "molecule_key": (
                self._molecule_key_with_conn(conn, project_id, record.spec.molecule_name)
                if conn is not None
                else self.compute_molecule_key(project_id, record.spec.molecule_name)
            ),
            "tags": tags_json,
            "archived": 0,
            "batch_id": batch_id,
            "last_activity_at": last_activity_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "group_id": record.group_id or record.id,
            "progress": record.progress,
            "custom_name": getattr(record, "custom_name", None)
            or getattr(record.spec, "custom_name", None),
        }

    def sync_from_job(self, record: JobRecord, layout_version: int = 2) -> None:
        """Derive and upsert a task row from a :class:`JobRecord`.

        ``task_id == job_id`` (existing jobs are indexed as-is); the node
        mapping follows §9.3 — ``node_id``/``storage_mode`` distinguish the
        remote (``sftp``) and local execution paths.  ``node_id`` carries
        the real execution-node name when dispatch already recorded one
        (``result["node"]`` or ``result["execution_target"]``); the
        fallback chain (``remote_job_id`` present → ``"remote"``, else
        ``"local"``) keeps pre-dispatch and historical rows indexed.
        """
        payload = self._payload_from_record(record, layout_version)
        with self._lock:
            conn = self._connect()
            try:
                self._upsert_conn(conn, payload)
                conn.commit()
            finally:
                if self._shared_conn is None:
                    conn.close()

    def _read_jobs_row(self, conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        if not jobs_table_exists(conn):
            return None
        return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def _project_transition(
        self,
        conn: sqlite3.Connection,
        job_id: str,
        record: JobRecord,
        job_row: sqlite3.Row | None,
    ) -> None:
        """Project one transition from the current jobs row onto the tasks row.

        The passed ``record`` is only a fallback for a standalone index DB
        without a ``jobs`` table; when the jobs row is present it is the
        authority, so a stale snapshot can never overwrite a newer state.
        Only projection-owned columns are written (never org fields).
        """
        source = row_to_record(job_row) if job_row is not None else record
        stored = conn.execute(
            "SELECT status, current_stage, progress FROM tasks WHERE task_id=?",
            (job_id,),
        ).fetchone()
        if stored is None:
            # Lock discipline: pass the held ``conn`` so the payload's
            # molecule_key resolution never re-acquires ``self._lock``.
            self._upsert_conn(conn, self._payload_from_record(source, conn=conn))
            return

        now = _utc_now_iso()
        status_changed = stored["status"] != source.status.value
        stage_changed = (stored["current_stage"] or "") != (source.current_stage or "")
        progress_changed = stored["progress"] != source.progress

        if status_changed or stage_changed:
            terminal = source.status.is_terminal
            if terminal and source.completed_at is not None:
                ca_sql = "completed_at=?"
                ca_param: tuple[Any, ...] = (source.completed_at,)
            else:
                ca_sql = "completed_at=completed_at"
                ca_param = ()
            conn.execute(
                f"UPDATE tasks SET status=?, current_stage=?, progress=?, "
                f"started_at=COALESCE(started_at,?), "
                f"{ca_sql}, last_activity_at=?, updated_at=? "
                f"WHERE task_id=?",
                (
                    source.status.value,
                    source.current_stage,
                    # NULL is a real jobs-authoritative value (rerun clears
                    # jobs.progress); skipping it strands the row in the
                    # drift set forever (D-T10-1).
                    source.progress,
                    source.started_at,
                    *ca_param,
                    now,
                    now,
                    job_id,
                ),
            )
            return

        if progress_changed:
            conn.execute(
                "UPDATE tasks SET progress=?, updated_at=? WHERE task_id=?",
                (source.progress, now, job_id),
            )

    def sync_job_transition(self, record: JobRecord) -> None:
        """Project a status transition from the CURRENT jobs row.

        Compare-before-write is preserved (no write when status/stage/progress
        are unchanged), but the compared values come from the authoritative
        jobs row read inside this same SQLite connection — never from the
        passed-in ``record``. A missing tasks row is created from the jobs row.
        """
        with self._lock:
            conn = self._connect()
            try:
                job_row = self._read_jobs_row(conn, record.id)
                self._project_transition(conn, record.id, record, job_row)
                conn.commit()
            finally:
                if self._shared_conn is None:
                    conn.close()

    def find_projection_drift(self, limit: int = PROJECTION_RECONCILE_BATCH) -> list[str]:
        """Return up to *limit* drifted job ids, oldest first, in one query.

        Drift = a missing tasks row or a status/current_stage/progress
        mismatch against the authoritative jobs row. ``ORDER BY j.id`` keeps
        the page stable; repaired rows leave the set, so the next scan makes
        progress and no old row starves.
        """
        with self._lock:
            conn = self._connect()
            try:
                if not jobs_table_exists(conn):
                    return []
                rows = conn.execute(_PROJECTION_DRIFT_SQL, (limit,)).fetchall()
                return [row["id"] for row in rows]
            finally:
                if self._shared_conn is None:
                    conn.close()

    def reconcile_projection(self, limit: int = PROJECTION_RECONCILE_BATCH) -> int:
        """Repair up to *limit* drifted task rows from the jobs authority.

        One paged drift query (no per-job query) plus a bounded batch of
        projections on a single connection. With batch size B, N drifted rows
        converge within ``ceil(N/B)`` scans: each repair removes its row from
        the drift set, so the next scan reaches the next-oldest drift.
        """
        with self._lock:
            conn = self._connect()
            try:
                if not jobs_table_exists(conn):
                    return 0
                rows = conn.execute(_PROJECTION_DRIFT_SQL, (limit,)).fetchall()
                if not rows:
                    return 0
                for job_row in rows:
                    source = row_to_record(job_row)
                    self._project_transition(conn, job_row["id"], source, None)
                conn.commit()
                return len(rows)
            finally:
                if self._shared_conn is None:
                    conn.close()

    def delete(self, task_id: str) -> None:
        """Remove a task row. No-op if absent."""
        self._run("DELETE FROM tasks WHERE task_id=?", (task_id,))

    def find_orphan_task_ids(self) -> list[str]:
        """Ghost index entries: task rows whose ``jobs`` row is gone.

        Produced by historical non-cascading deletes of the ``jobs`` row.
        Returns ``[]`` when the ``jobs`` table is absent from the index DB
        (standalone index) — orphans are only definable against the
        scheduler schema.
        """
        with self._lock:
            conn = self._connect()
            try:
                if not jobs_table_exists(conn):
                    return []
                rows = conn.execute(
                    "SELECT t.task_id FROM tasks t "
                    "WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = t.job_id) "
                    "ORDER BY t.created_at"
                ).fetchall()
                return [row["task_id"] for row in rows]
            finally:
                if self._shared_conn is None:
                    conn.close()

    def update_project(self, task_id: str, project_id: str) -> None:
        """Update project_id for an existing task row. No-op if absent."""
        self._run(
            "UPDATE tasks SET project_id=?, updated_at=? WHERE task_id=?",
            (project_id, _utc_now_iso(), task_id),
        )

    def rewrite_tags(
        self,
        project_id: str,
        transform: Callable[[list[str]], list[str]],
    ) -> int:
        """Apply *transform* to the tags list of every task in *project_id*.

        Runs under a single lock + connection: SELECT all rows, apply
        *transform*, write back changed rows, commit once.  Returns the
        number of rows whose tags were actually modified.
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT task_id, tags FROM tasks WHERE project_id=?",
                    (project_id,),
                ).fetchall()
                updated = 0
                for row in rows:
                    raw = row["tags"]
                    try:
                        current: list[str] = json.loads(raw) if raw else []
                    except (json.JSONDecodeError, TypeError):
                        current = []
                    if not isinstance(current, list):
                        current = []
                    new_tags = transform(current)
                    # Dedupe preserving order
                    seen: set[str] = set()
                    deduped: list[str] = []
                    for t in new_tags:
                        if t not in seen:
                            seen.add(t)
                            deduped.append(t)
                    if deduped != current:
                        conn.execute(
                            "UPDATE tasks SET tags=?, updated_at=? WHERE task_id=?",
                            (json.dumps(deduped), _utc_now_iso(), row["task_id"]),
                        )
                        updated += 1
                conn.commit()
                return updated
            finally:
                if self._shared_conn is None:
                    conn.close()

    def update_display_fields(
        self,
        task_id: str,
        *,
        molecule_name: str | None = None,
        task_name: str | None = None,
        remark: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Update only the user-editable display columns.

        When *molecule_name* is provided the ``molecule_key`` is recomputed.
        *tags* are stored as a JSON-serialised list.  Never touches the
        ``jobs`` table or ``spec_json``.

        Returns ``True`` when the row existed and was updated.
        """
        existing = self.get(task_id)
        if existing is None:
            return False

        sets: list[str] = []
        params: list[Any] = []

        if molecule_name is not None:
            sets.append("molecule_name=?")
            params.append(molecule_name)
            sets.append("molecule_key=?")
            params.append(
                self.compute_molecule_key(
                    existing.get("project_id"),
                    molecule_name,
                )
            )

        if task_name is not None:
            sets.append("task_name=?")
            params.append(task_name)

        if remark is not None:
            sets.append("remark=?")
            params.append(remark)

        if tags is not None:
            sets.append("tags=?")
            params.append(json.dumps(tags))

        if not sets:
            return False

        sets.append("updated_at=?")
        params.append(_utc_now_iso())
        params.append(task_id)
        self._run(
            f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?",
            tuple(params),
        )
        return True

    def update_custom_name(
        self,
        task_id: str,
        custom_name: str | None,
        expected_name_revision: int,
    ) -> dict[str, Any]:
        """Set or clear a task's custom name within one transaction.

        *custom_name* ``None`` restores the default.  Validates the name,
        checks the revision for optimistic concurrency, bumps the revision,
        and writes an audit row to ``organization_events`` — all atomically.

        Returns a name projection dict.  Raises ``LookupError`` if the task
        does not exist, ``NameRevisionConflictError`` on stale revision, or
        ``ValueError`` on validation failure.
        """
        validated = validate_custom_name(custom_name)
        now = _utc_now_iso()

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchall()
                if not rows:
                    raise LookupError(f"task {task_id!r} not found")

                current = dict(rows[0])
                current_rev = current["name_revision"]
                if expected_name_revision != current_rev:
                    raise NameRevisionConflictError(resolve_task_names(current))

                if validated == current["custom_name"]:
                    return resolve_task_names(current)

                new_rev = current_rev + 1
                action = "restore_default_name" if validated is None else "rename"
                old_value = current["custom_name"]
                new_value = validated

                conn.execute(
                    "UPDATE tasks SET custom_name=?, name_revision=?, "
                    "name_updated_at=?, updated_at=? WHERE task_id=?",
                    (validated, new_rev, now, now, task_id),
                )
                conn.execute(
                    "INSERT INTO organization_events "
                    "(object_type, object_id, action, old_value, new_value, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "task",
                        task_id,
                        action,
                        json.dumps(old_value),
                        json.dumps(new_value),
                        now,
                    ),
                )
                try:
                    conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now, task_id))
                except sqlite3.OperationalError:
                    pass
                conn.commit()

                # Persist custom name to on-disk task.json if available
                node_path = current.get("node_path")
                if node_path:
                    try:
                        task_json_file = Path(node_path) / "task.json"
                        if task_json_file.is_file():
                            raw_payload = json.loads(task_json_file.read_text(encoding="utf-8"))
                            raw_payload["custom_name"] = validated
                            raw_payload["name_revision"] = new_rev
                            raw_payload["name_updated_at"] = now
                            raw_payload["updated_at"] = now
                            tmp_file = task_json_file.with_suffix(".tmp")
                            tmp_file.write_text(json.dumps(raw_payload, indent=2), encoding="utf-8")
                            tmp_file.replace(task_json_file)
                    except Exception:
                        pass

                return {
                    "default_name": current["display_name"] or "",
                    "resolved_name": validated if validated else current["display_name"] or "",
                    "custom_name": validated,
                    "name_revision": new_rev,
                    "name_updated_at": now,
                }
            finally:
                if self._shared_conn is None:
                    conn.close()

    # ------------------------------------------------------------------ #
    # Name projections (batch, for v1 enrichment)
    # ------------------------------------------------------------------ #

    def get_name_projections_by_job_ids(self, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return ``{job_id: name_projection}`` for the given job IDs.

        Each projection contains ``custom_name``, ``resolved_name``,
        ``default_name``, ``name_revision``, and ``name_updated_at``.
        Missing task rows are silently omitted.
        """
        if not job_ids:
            return {}
        placeholders = ", ".join("?" for _ in job_ids)
        rows = self._query(
            f"SELECT task_id, display_name, custom_name, name_revision, name_updated_at "
            f"FROM tasks WHERE task_id IN ({placeholders})",
            tuple(job_ids),
        )
        return {row["task_id"]: resolve_task_names(dict(row)) for row in rows}

    # ------------------------------------------------------------------ #
    # Molecule key resolution (alias-aware)
    # ------------------------------------------------------------------ #

    def _molecule_key_with_conn(
        self,
        conn: sqlite3.Connection,
        project_id: str | None,
        molecule_name: str,
    ) -> str:
        """Resolve ``molecule_key`` through an already-open connection.

        Lock discipline: the caller holds ``self._lock``, so this must not call
        any method that re-acquires it. ``resolve_molecule_key`` accepts a raw
        connection for exactly this purpose (the same path migrations use).
        """
        if not project_id:
            from acp.scheduler.naming import molecule_group_key

            return molecule_group_key(molecule_name)
        from acp.scheduler.molecule_groups import resolve_molecule_key

        return resolve_molecule_key(conn, project_id, molecule_name)

    def compute_molecule_key(self, project_id: str | None, molecule_name: str) -> str:
        """Resolve *molecule_name* to the effective ``molecule_key``.

        Uses the two-tier alias lookup when *project_id* is available;
        falls back to bare ``molecule_group_key`` when project is empty.
        """
        if not project_id:
            from acp.scheduler.naming import molecule_group_key

            return molecule_group_key(molecule_name)
        from acp.scheduler.molecule_groups import resolve_molecule_key

        return resolve_molecule_key(self, project_id, molecule_name)


__all__ = [
    "NameRevisionConflictError",
    "PROJECTION_RECONCILE_BATCH",
    "TaskIndex",
    "jobs_table_exists",
    "resolve_task_names",
    "validate_custom_name",
]
