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
from dataclasses import replace
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.migrations import migrate

logger = logging.getLogger(__name__)

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
                       remote_job_id, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _record_to_row(record),
            )
            conn.commit()

    def update(self, record: JobRecord) -> None:
        record.touch()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, current_stage=?, progress=?, error=?,
                       pid=?, exit_code=?, remote_job_id=?, started_at=?, completed_at=?, updated_at=?,
                       result_json=?, spec_json=?, project_id=?, input_hash=? WHERE id=?""",
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
                    record.id,
                ),
            )
            conn.commit()

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_record(row) if row else None

    def list(self, status: str | None = None, limit: int = 200) -> list[JobRecord]:
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_by_project(self, project_id: str, limit: int = 200) -> list[JobRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {s.value: 0 for s in JobStatus}
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        for r in rows:
            out[r["status"]] = r["n"]
        return out

    def delete(self, job_id: str) -> None:
        with self._lock, self._connect() as conn:
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

    def update_project_id_and_work_dir(
        self, job_id: str, project_id: str, work_dir: str
    ) -> None:
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
        json.dumps(record.result) if record.result is not None else None,
    )


def _row_to_record(row: sqlite3.Row) -> JobRecord:
    spec_raw = json.loads(row["spec_json"])
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
        result=result,
    )


def _spec_to_json(spec: JobSpec) -> str:
    return json.dumps(spec.to_dict())


__all__ = ["JobStore"]
