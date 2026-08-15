# pyright: reportAny=false, reportUnusedCallResult=false
"""SQLite schema migrations for the ACP scheduler."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS _schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


_MIGRATIONS: list[dict[str, str]] = [
    {
        "id": "001",
        "description": "create projects table",
        "sql": """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    run_root TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
""",
    },
    {
        "id": "002",
        "description": "add project_id and input_hash to jobs",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "003",
        "description": "create stage_tasks table",
        "sql": """
CREATE TABLE IF NOT EXISTS stage_tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    task_type TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    exit_status INTEGER,
    retry_count INTEGER DEFAULT 0,
    pid INTEGER,
    stderr_summary TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    result_json TEXT,
    provenance_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage_tasks_job_id ON stage_tasks(job_id);
""",
    },
    {
        "id": "004",
        "description": "create artifacts table",
        "sql": """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT,
    job_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    checksum TEXT,
    size_bytes INTEGER,
    parser_status TEXT NOT NULL DEFAULT 'pending',
    mime_type TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);
""",
    },
    {
        "id": "005",
        "description": "add remote_job_id to jobs",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "006",
        "description": "create mechanism study review tables",
        "sql": """
CREATE TABLE IF NOT EXISTS mechanism_studies (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    study_json TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS decision_points (
    id TEXT PRIMARY KEY,
    study_id TEXT,
    status TEXT,
    payload TEXT,
    resolution TEXT,
    created_at TEXT,
    resolved_at TEXT
);
""",
    },
]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_MIGRATIONS_SQL)


def get_schema_version(conn: sqlite3.Connection) -> int:
    _ensure_meta_table(conn)
    row = conn.execute("SELECT COUNT(*) AS n FROM _schema_migrations").fetchone()
    return int(row["n"]) if row is not None else 0


def _apply_jobs_column_migration(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "jobs"):
        return False
    if not _column_exists(conn, "jobs", "project_id"):
        conn.execute("ALTER TABLE jobs ADD COLUMN project_id TEXT")
    if not _column_exists(conn, "jobs", "input_hash"):
        conn.execute("ALTER TABLE jobs ADD COLUMN input_hash TEXT")
    return True


def _apply_remote_job_id_column(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "jobs"):
        return False
    if not _column_exists(conn, "jobs", "remote_job_id"):
        conn.execute("ALTER TABLE jobs ADD COLUMN remote_job_id TEXT")
    return True


def _apply_migration(conn: sqlite3.Connection, migration: dict[str, str]) -> bool:
    migration_id = migration["id"]
    if migration_id == "002":
        return _apply_jobs_column_migration(conn)
    if migration_id == "005":
        return _apply_remote_job_id_column(conn)
    sql = migration["sql"].strip()
    if sql:
        conn.executescript(sql)
    return True


def migrate(db_path: Path | str) -> int:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    applied = 0
    with _connect(path) as conn:
        _ensure_meta_table(conn)
        existing = {
            row["id"] for row in conn.execute("SELECT id FROM _schema_migrations").fetchall()
        }
        for migration in _MIGRATIONS:
            if migration["id"] in existing:
                continue
            if not _apply_migration(conn, migration):
                continue
            conn.execute(
                "INSERT INTO _schema_migrations (id, applied_at) VALUES (?, ?)",
                (migration["id"], _utc_now_iso()),
            )
            applied += 1
        conn.commit()
    return applied


__all__ = ["get_schema_version", "migrate"]
