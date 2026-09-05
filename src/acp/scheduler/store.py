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
    group_id TEXT,
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
                       remote_job_id, group_id, result_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _record_to_row(record),
            )
            conn.commit()

    def update(self, record: JobRecord) -> None:
        record.touch()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status=?, current_stage=?, progress=?, error=?,
                       pid=?, exit_code=?, remote_job_id=?, started_at=?,
                       completed_at=?, updated_at=?,
                       result_json=?, spec_json=?, project_id=?, input_hash=?, group_id=?
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
                    record.id,
                ),
            )
            conn.commit()

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_record(row) if row else None

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

    def list_by_project(self, project_id: str, limit: int = 200) -> list[JobRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

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

    def purge_cascade(self, job_id: str) -> None:
        """Delete a job row plus every dependent row, in one connection.

        No FK cascades exist in the schema, so children are removed
        explicitly in dependency order: ``stage_tasks`` and ``artifacts``
        by ``job_id``; ``decision_points`` via ``mechanism_studies``
        subselect (it has no ``job_id`` column); then ``mechanism_studies``
        and finally the ``jobs`` row itself.
        """
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM stage_tasks WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM artifacts WHERE job_id=?", (job_id,))
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
        result=result,
    )


def _spec_to_json(spec: JobSpec) -> str:
    return json.dumps(spec.to_dict())


__all__ = ["JobStore"]
