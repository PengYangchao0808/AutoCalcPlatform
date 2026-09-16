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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord
from acp.scheduler.migrations import migrate

logger = logging.getLogger(__name__)

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
    updated_at TEXT NOT NULL
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
}


#: Columns that sync_from_job / sync_job_transition own (updated on conflict).
_SYNC_COLUMNS: tuple[str, ...] = (
    "display_name", "task_dir_name", "workflow", "status",
    "current_stage", "node_id", "node_path", "storage_mode",
    "layout_version", "input_hash", "result_manifest_path", "updated_at",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskIndex:
    """Thread-safe SQLite index of task rows over the scheduler DB file.

    Mirrors the :class:`~acp.scheduler.jobs.JobStore` connection pattern
    (per-call connections guarded by a lock); a shared connection may be
    supplied instead of a path.
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

    def query_rows(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
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

    def upsert(self, record: dict[str, Any]) -> None:
        """Insert or update a task row keyed by ``task_id``.

        First-write-wins columns (project_id, molecule_name, task_name,
        remark, molecule_key, tags, archived, batch_id, created_at) are
        only set on INSERT.  Sync-owned columns are updated on conflict.
        """
        row: dict[str, Any] = {}
        for col in _TASK_COLUMNS:
            value = record.get(col)
            row[col] = _COLUMN_DEFAULTS.get(col, "") if value is None else value
        try:
            row["layout_version"] = int(row["layout_version"])
        except (TypeError, ValueError):
            row["layout_version"] = 2
        columns = ", ".join(_TASK_COLUMNS)
        placeholders = ", ".join("?" for _ in _TASK_COLUMNS)

        update_parts = ", ".join(f"{c}=excluded.{c}" for c in _SYNC_COLUMNS)
        self._run(
            f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(task_id) DO UPDATE SET {update_parts}",
            tuple(row[col] for col in _TASK_COLUMNS),
        )

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
        remote = bool(record.remote_job_id)
        result = record.result if isinstance(record.result, dict) else {}
        node = result.get("node") or result.get("execution_target")
        if not isinstance(node, str) or not node:
            node = "remote" if remote else "local"

        tags_list = record.spec.tags or []
        tags_json = json.dumps(tags_list)
        batch_id = (
            record.spec.resources.get("batch_id")
            if isinstance(record.spec.resources, dict) else None
        )
        last_activity_at = record.completed_at or record.started_at or record.created_at
        project_id = record.project_id or record.spec.project_id
        self.upsert(
            {
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
                "molecule_key": self.compute_molecule_key(
                    project_id, record.spec.molecule_name,
                ),
                "tags": tags_json,
                "archived": 0,
                "batch_id": batch_id,
                "last_activity_at": last_activity_at,
                "started_at": record.started_at,
                "completed_at": record.completed_at,
                "group_id": record.group_id or record.id,
                "progress": record.progress,
            }
        )

    def sync_job_transition(self, record: JobRecord) -> None:
        """Sync status transition with compare-before-write optimization.

        If the task row does not exist, falls back to sync_from_job.
        Only writes when status/stage/progress actually changed.
        """
        rows = self._query(
            "SELECT status, current_stage, progress FROM tasks WHERE task_id=?",
            (record.id,),
        )
        if not rows:
            self.sync_from_job(record)
            return

        stored = rows[0]
        now = _utc_now_iso()

        status_changed = stored["status"] != record.status.value
        stage_changed = (stored["current_stage"] or "") != (record.current_stage or "")

        if status_changed or stage_changed:
            terminal = record.status.is_terminal
            if terminal and record.completed_at is not None:
                ca_sql = "completed_at=?"
                ca_param: tuple[Any, ...] = (record.completed_at,)
            else:
                ca_sql = "completed_at=completed_at"
                ca_param = ()
            self._run(
                f"UPDATE tasks SET status=?, current_stage=?, "
                f"started_at=COALESCE(started_at,?), "
                f"{ca_sql}, last_activity_at=?, updated_at=? "
                f"WHERE task_id=?",
                (
                    record.status.value,
                    record.current_stage,
                    record.started_at,
                    *ca_param,
                    now,
                    now,
                    record.id,
                ),
            )
            return

        stored_progress = stored["progress"]
        new_progress = record.progress
        if (
            new_progress is not None
            and stored_progress != new_progress
        ):
            self._run(
                "UPDATE tasks SET progress=?, updated_at=? WHERE task_id=?",
                (new_progress, now, record.id),
            )

    def delete(self, task_id: str) -> None:
        """Remove a task row. No-op if absent."""
        self._run("DELETE FROM tasks WHERE task_id=?", (task_id,))

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
            params.append(self.compute_molecule_key(
                existing.get("project_id"), molecule_name,
            ))

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

    # ------------------------------------------------------------------ #
    # Molecule key resolution (alias-aware)
    # ------------------------------------------------------------------ #

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


__all__ = ["TaskIndex"]
