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

import logging
import sqlite3
import threading
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
)

#: Mirrors the SQL column defaults for keys absent (or None) in the payload.
_COLUMN_DEFAULTS: dict[str, Any] = {
    "status": "pending",
    "storage_mode": "local",
    "layout_version": 2,
}


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

    def _init_schema(self) -> None:
        self._run(_TASKS_SCHEMA)
        if self.db_path is not None:
            migrate(self.db_path)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def upsert(self, record: dict[str, Any]) -> None:
        """Insert or replace a task row keyed by ``task_id``.

        Expects §9.1 field names as dict keys; ``None``/missing values are
        coerced to ``''`` (or the SQL column default for the defaulted
        columns ``status``/``storage_mode``/``layout_version``).
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
        self._run(
            f"INSERT OR REPLACE INTO tasks ({columns}) VALUES ({placeholders})",
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
        remote (``sftp``) and local execution paths.
        """
        remote = bool(record.remote_job_id)
        self.upsert(
            {
                "task_id": record.id,
                "job_id": record.id,
                "project_id": record.project_id or record.spec.project_id,
                "molecule_name": record.spec.molecule_name,
                "task_name": record.spec.task_name,
                "remark": record.spec.remark,
                # Keep the task index aligned with the physical directory;
                # historical JobRecords may still carry a legacy spec.name.
                "display_name": Path(record.work_dir).name if record.work_dir else record.spec.name,
                "workflow": record.spec.workflow,
                "task_dir_name": Path(record.work_dir).name if record.work_dir else "",
                "status": record.status.value,
                "node_id": "remote" if remote else "local",
                "node_path": record.work_dir,
                "input_hash": record.input_hash or record.spec.input_hash,
                "result_manifest_path": None,
                "current_stage": record.current_stage,
                "storage_mode": "sftp" if remote else "local",
                "layout_version": layout_version,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
        )


__all__ = ["TaskIndex"]
