# pyright: reportAny=false, reportUnusedCallResult=false
"""SQLite schema migrations for the ACP scheduler."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    {
        "id": "013",
        "description": "add node_id and host to jobs (persisted execution target)",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility",
    },
    {
        "id": "014",
        "description": "add org columns to tasks (molecule_key, tags, archived, batch_id, etc.)",
        "sql": "-- handled in Python for SQLite ALTER TABLE compatibility + backfill",
    },
    {
        "id": "015",
        "description": "create molecule_groups and molecule_aliases tables (P2 aliases/merge)",
        "sql": """
CREATE TABLE IF NOT EXISTS molecule_groups (
    project_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, group_key)
);
CREATE TABLE IF NOT EXISTS molecule_aliases (
    project_id TEXT NOT NULL,
    alias_key TEXT NOT NULL,
    group_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, alias_key)
);
""",
    },
    {
        "id": "016",
        "description": "refresh molecule_key to case-preserving (alias rows keep target)",
        "sql": "-- handled in Python: recomputes molecule_key case-preserving",
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


def _apply_jobs_node_columns(conn: sqlite3.Connection) -> bool:
    """Add ``node_id`` / ``host`` to jobs (persisted execution target).

    NULL for historical rows — read side falls back to ``result["node"]`` /
    ``spec.target_node``.  Deliberately no backfill (plan locked).
    """
    if not _table_exists(conn, "jobs"):
        return False
    if not _column_exists(conn, "jobs", "node_id"):
        conn.execute("ALTER TABLE jobs ADD COLUMN node_id TEXT")
    if not _column_exists(conn, "jobs", "host"):
        conn.execute("ALTER TABLE jobs ADD COLUMN host TEXT")
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


def _apply_tasks_org_columns(conn: sqlite3.Connection) -> bool:
    """Add organization columns to tasks + indexes + backfill from jobs."""
    if not _table_exists(conn, "tasks"):
        return False

    _org_columns = [
        ("molecule_key", "TEXT NOT NULL DEFAULT ''"),
        ("tags", "TEXT NOT NULL DEFAULT '[]'"),
        ("archived", "INTEGER NOT NULL DEFAULT 0"),
        ("batch_id", "TEXT"),
        ("last_activity_at", "TEXT"),
        ("started_at", "TEXT"),
        ("completed_at", "TEXT"),
        ("group_id", "TEXT"),
        ("progress", "REAL"),
    ]
    for col_name, col_def in _org_columns:
        if not _column_exists(conn, "tasks", col_name):
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_archived ON tasks(project_id, archived)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_molecule_key ON tasks(molecule_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_batch_id ON tasks(batch_id)"
    )

    _backfill_tasks_from_jobs(conn)
    return True


def _backfill_tasks_from_jobs(conn: sqlite3.Connection) -> None:
    """Populate task rows from jobs for any jobs not yet indexed.

    Idempotent: only INSERTs missing rows (task_id == jobs.id).
    """
    if not _table_exists(conn, "tasks") or not _table_exists(conn, "jobs"):
        return

    log = logging.getLogger(__name__)

    existing_task_ids = {
        row[0]
        for row in conn.execute("SELECT task_id FROM tasks").fetchall()
    }

    for job_row in conn.execute("SELECT * FROM jobs").fetchall():
        job_id = job_row["id"]
        if job_id in existing_task_ids:
            continue

        spec_raw: dict[str, Any] = {}
        spec_json_str = job_row["spec_json"]
        try:
            spec_raw = json.loads(spec_json_str)
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "Corrupt spec_json for job %s — backfilling with defaults", job_id
            )

        molecule_name = spec_raw.get("molecule_name", "") if isinstance(spec_raw, dict) else ""
        task_name = spec_raw.get("task_name", "") if isinstance(spec_raw, dict) else ""
        remark = spec_raw.get("remark", "") if isinstance(spec_raw, dict) else ""
        workflow = spec_raw.get("workflow", "") if isinstance(spec_raw, dict) else ""
        tags_list = spec_raw.get("tags", []) if isinstance(spec_raw, dict) else []
        if not isinstance(tags_list, list):
            tags_list = []
        tags_json = json.dumps(tags_list)

        resources = spec_raw.get("resources", {}) if isinstance(spec_raw, dict) else {}
        batch_id = resources.get("batch_id") if isinstance(resources, dict) else None

        group_id = job_row["group_id"] if "group_id" in job_row.keys() else job_id

        work_dir = job_row["work_dir"] or ""
        display_name = Path(work_dir).name if work_dir else ""
        task_dir_name = Path(work_dir).name if work_dir else ""

        from acp.scheduler.naming import molecule_group_key

        molecule_key = molecule_group_key(molecule_name)

        remote_job_id = job_row["remote_job_id"] if "remote_job_id" in job_row.keys() else None
        storage_mode = "sftp" if remote_job_id else "local"
        node_path = work_dir
        node_id = "remote" if remote_job_id else "local"

        status = job_row["status"] if "status" in job_row.keys() else "pending"
        started_at = job_row["started_at"] if "started_at" in job_row.keys() else None
        completed_at = job_row["completed_at"] if "completed_at" in job_row.keys() else None
        progress = job_row["progress"] if "progress" in job_row.keys() else None
        created_at = job_row["created_at"] if "created_at" in job_row.keys() else ""
        updated_at = job_row["updated_at"] if "updated_at" in job_row.keys() else ""

        input_hash = job_row["input_hash"] if "input_hash" in job_row.keys() else None

        last_activity_at = completed_at or started_at or created_at

        conn.execute(
            """INSERT INTO tasks (
                task_id, job_id, project_id, molecule_name, task_name, remark,
                display_name, workflow, task_dir_name, status, node_id, node_path,
                input_hash, result_manifest_path, current_stage, storage_mode,
                layout_version, created_at, updated_at,
                molecule_key, tags, archived, batch_id,
                last_activity_at, started_at, completed_at, group_id, progress
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL, NULL, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )""",
            (
                job_id,
                job_id,
                job_row["project_id"] if "project_id" in job_row.keys() else None,
                molecule_name,
                task_name,
                remark,
                display_name,
                workflow,
                task_dir_name,
                status,
                node_id,
                node_path,
                input_hash,
                storage_mode,
                created_at,
                updated_at,
                molecule_key,
                tags_json,
                0,
                batch_id,
                last_activity_at,
                started_at,
                completed_at,
                group_id,
                progress,
            ),
        )


def _apply_case_preserving_molecule_key_refresh(conn: sqlite3.Connection) -> bool:
    """Refresh molecule_key values to case-preserving form.

    Rows whose alias_key maps to a group_key via molecule_aliases keep
    their merge-target key (alias resolution is consulted first).  All
    other rows get the new case-preserving molecule_group_key(molecule_name).
    """
    if not _table_exists(conn, "tasks"):
        return False

    from acp.scheduler.molecule_groups import resolve_molecule_key

    rows = conn.execute(
        "SELECT task_id, project_id, molecule_name, molecule_key FROM tasks"
    ).fetchall()

    now = _utc_now_iso()
    for row in rows:
        project_id = row["project_id"]
        if not project_id:
            continue
        new_key = resolve_molecule_key(conn, project_id, row["molecule_name"])
        if new_key != row["molecule_key"]:
            conn.execute(
                "UPDATE tasks SET molecule_key=?, updated_at=? WHERE task_id=?",
                (new_key, now, row["task_id"]),
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
    if migration_id == "013":
        return _apply_jobs_node_columns(conn)
    if migration_id == "014":
        return _apply_tasks_org_columns(conn)
    if migration_id == "016":
        return _apply_case_preserving_molecule_key_refresh(conn)
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
