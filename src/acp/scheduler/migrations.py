# pyright: reportAny=false, reportUnusedCallResult=false
"""SQLite schema migrations for the ACP scheduler."""

from __future__ import annotations

import logging
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
    {
        "id": "007",
        "description": "add reaction/plan columns to mechanism_studies",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "008",
        "description": "add group_id to jobs (queue grouping / rerun lineage)",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "009",
        "description": "add status_detail to stage_tasks (phase sub-step progress)",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "011",
        "description": "unique index on lower(name) for projects (v2 project dir naming)",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "010",
        "description": "create tasks table (v2 task index §9.1/§9.3)",
        "sql": """
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
);
CREATE INDEX IF NOT EXISTS idx_tasks_job_id ON tasks(job_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
""",
    },
    {
        "id": "012",
        "description": "create mechanism_projects table (design §9)",
        "sql": """
CREATE TABLE IF NOT EXISTS mechanism_projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    reaction_definition_hash TEXT NOT NULL DEFAULT '',
    charge INTEGER NOT NULL DEFAULT 0,
    multiplicity INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'created',
    s1_job_id TEXT,
    s2_job_id TEXT,
    s3_job_id TEXT,
    s4_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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


def _apply_mechanism_studies_columns(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "mechanism_studies"):
        return False
    if not _column_exists(conn, "mechanism_studies", "reaction_json"):
        conn.execute("ALTER TABLE mechanism_studies ADD COLUMN reaction_json TEXT")
    if not _column_exists(conn, "mechanism_studies", "mechanism_plan_json"):
        conn.execute("ALTER TABLE mechanism_studies ADD COLUMN mechanism_plan_json TEXT")
    if not _column_exists(conn, "mechanism_studies", "config_hash"):
        conn.execute("ALTER TABLE mechanism_studies ADD COLUMN config_hash TEXT")
    if not _column_exists(conn, "mechanism_studies", "cycle_index"):
        conn.execute("ALTER TABLE mechanism_studies ADD COLUMN cycle_index INTEGER DEFAULT 0")
    if not _column_exists(conn, "mechanism_studies", "consumed_cycle"):
        conn.execute("ALTER TABLE mechanism_studies ADD COLUMN consumed_cycle INTEGER")
    return True


def _apply_jobs_group_id_column(conn: sqlite3.Connection) -> bool:
    """Add ``group_id`` to jobs (self-rooted by default) plus an index.

    ``group_id`` is the queue-grouping / rerun-lineage key. Every job is
    its own group root unless it was cloned by ``rerun_job`` (which
    inherits the original's group id), so historical rows backfill to
    ``group_id = id``.
    """
    if not _table_exists(conn, "jobs"):
        return False
    if not _column_exists(conn, "jobs", "group_id"):
        conn.execute("ALTER TABLE jobs ADD COLUMN group_id TEXT")
    conn.execute("UPDATE jobs SET group_id = id WHERE group_id IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_group_id ON jobs(group_id)")
    return True


def _apply_stage_tasks_status_detail_column(conn: sqlite3.Connection) -> bool:
    """Add ``status_detail`` to stage_tasks (human phase sub-step, e.g. ``scan 7/24``)."""
    if not _table_exists(conn, "stage_tasks"):
        return False
    if not _column_exists(conn, "stage_tasks", "status_detail"):
        conn.execute("ALTER TABLE stage_tasks ADD COLUMN status_detail TEXT")
    return True


def _apply_projects_name_unique_index(conn: sqlite3.Connection) -> bool:
    """Index lower(name) on projects; tolerated when legacy duplicates exist."""
    if not _table_exists(conn, "projects"):
        return False
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name_ci ON projects(lower(name))"
        )
    except sqlite3.IntegrityError:
        logging.getLogger(__name__).warning(
            "projects table has duplicate names; skipping unique index (v2 dir naming "
            "still enforced at creation time by ProjectManager)"
        )
    return True


def _apply_migration(conn: sqlite3.Connection, migration: dict[str, str]) -> bool:
    migration_id = migration["id"]
    if migration_id == "002":
        return _apply_jobs_column_migration(conn)
    if migration_id == "005":
        return _apply_remote_job_id_column(conn)
    if migration_id == "007":
        return _apply_mechanism_studies_columns(conn)
    if migration_id == "008":
        return _apply_jobs_group_id_column(conn)
    if migration_id == "009":
        return _apply_stage_tasks_status_detail_column(conn)
    if migration_id == "011":
        return _apply_projects_name_unique_index(conn)
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
