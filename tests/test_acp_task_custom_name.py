# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportUnusedCallResult=false
"""
Tests for task custom-name foundation (Wave 1): migration 017,
validate_custom_name, update_custom_name, resolve_task_names, audit rows.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from acp.scheduler.migrations import migrate
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import (
    NameRevisionConflictError,
    TaskIndex,
    resolve_task_names,
    validate_custom_name,
)


def _make_task(
    idx: TaskIndex,
    task_id: str = "t1",
    display_name: str = "ethanol_sp",
) -> None:
    idx.upsert(
        {
            "task_id": task_id,
            "job_id": task_id,
            "project_id": "p1",
            "molecule_name": "ethanol",
            "task_name": "sp",
            "remark": "",
            "display_name": display_name,
            "workflow": "energy",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
    )


# ---------------------------------------------------------------------------
# validate_custom_name
# ---------------------------------------------------------------------------


class TestValidateCustomName:
    def test_none_passthrough(self) -> None:
        assert validate_custom_name(None) is None

    def test_strips_whitespace(self) -> None:
        assert validate_custom_name("  My Task  ") == "My Task"

    def test_chinese_and_punctuation(self) -> None:
        result = validate_custom_name("TS2 路径搜索（DFT优化）")
        assert result == "TS2 路径搜索（DFT优化）"

    def test_empty_after_strip_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_custom_name("   ")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_custom_name("")

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValueError, match="200 characters"):
            validate_custom_name("x" * 201)

    def test_exactly_200_ok(self) -> None:
        result = validate_custom_name("a" * 200)
        assert len(result) == 200

    def test_newline_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_custom_name("Task\nName")

    def test_carriage_return_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_custom_name("Task\rName")

    def test_tab_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_custom_name("Task\tName")

    def test_null_char_rejected(self) -> None:
        with pytest.raises(ValueError, match="control character"):
            validate_custom_name("Task\x00Name")

    def test_regular_spaces_ok(self) -> None:
        assert validate_custom_name("Task Name") == "Task Name"

    def test_unicode_nbsp_ok(self) -> None:
        result = validate_custom_name("Task\u00a0Name")
        assert result == "Task\u00a0Name"


# ---------------------------------------------------------------------------
# resolve_task_names
# ---------------------------------------------------------------------------


class TestResolveTaskNames:
    def test_custom_name_takes_priority(self) -> None:
        result = resolve_task_names(
            {
                "display_name": "ethanol_sp",
                "custom_name": "My Custom",
                "name_revision": 1,
                "name_updated_at": "2026-01-01",
            }
        )
        assert result["resolved_name"] == "My Custom"
        assert result["default_name"] == "ethanol_sp"
        assert result["custom_name"] == "My Custom"
        assert result["name_revision"] == 1

    def test_no_custom_name_falls_back(self) -> None:
        result = resolve_task_names(
            {
                "display_name": "ethanol_sp",
                "custom_name": None,
                "name_revision": 0,
                "name_updated_at": None,
            }
        )
        assert result["resolved_name"] == "ethanol_sp"
        assert result["custom_name"] is None

    def test_empty_display_name(self) -> None:
        result = resolve_task_names(
            {"display_name": "", "custom_name": None, "name_revision": 0, "name_updated_at": None}
        )
        assert result["resolved_name"] == ""
        assert result["default_name"] == ""


# ---------------------------------------------------------------------------
# Migration 017
# ---------------------------------------------------------------------------


class TestMigration017:
    def _setup_old_schema_db(self, tmp_path: Path) -> Path:
        """Create a DB with tasks table but WITHOUT the custom_name columns."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
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
    molecule_key TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    archived INTEGER NOT NULL DEFAULT 0,
    batch_id TEXT,
    last_activity_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    group_id TEXT,
    progress REAL
);
        """)
        conn.commit()
        conn.close()
        return db

    def test_017_adds_task_columns(self, tmp_path: Path) -> None:
        db = self._setup_old_schema_db(tmp_path)
        migrate(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "custom_name" in cols
        assert "name_revision" in cols
        assert "name_updated_at" in cols
        conn.close()

    def test_017_creates_organization_events(self, tmp_path: Path) -> None:
        db = self._setup_old_schema_db(tmp_path)
        migrate(db)
        conn = sqlite3.connect(str(db))
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_events'"
            ).fetchone()
            is not None
        )
        conn.close()

    def test_017_creates_index(self, tmp_path: Path) -> None:
        db = self._setup_old_schema_db(tmp_path)
        migrate(db)
        conn = sqlite3.connect(str(db))
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert "idx_org_events_object" in indexes
        conn.close()

    def test_017_idempotent(self, tmp_path: Path) -> None:
        db = self._setup_old_schema_db(tmp_path)
        v1 = migrate(db)
        assert v1 >= 1
        v2 = migrate(db)
        assert v2 == 0

    def test_017_fresh_db_has_columns(self, tmp_path: Path) -> None:
        db = tmp_path / "fresh.db"
        migrate(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "custom_name" in cols
        assert "name_revision" in cols
        assert "name_updated_at" in cols
        conn.close()

    def test_017_fresh_db_has_organization_events(self, tmp_path: Path) -> None:
        db = tmp_path / "fresh.db"
        migrate(db)
        conn = sqlite3.connect(str(db))
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_events'"
            ).fetchone()
            is not None
        )
        conn.close()

    def test_017_existing_data_preserved(self, tmp_path: Path) -> None:
        db = self._setup_old_schema_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO tasks (task_id, job_id, display_name, created_at, updated_at) "
            "VALUES ('t1', 't1', 'old_name', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE task_id='t1'").fetchone()
        assert row["display_name"] == "old_name"
        assert row["custom_name"] is None
        assert row["name_revision"] == 0
        assert row["name_updated_at"] is None
        conn.close()


# ---------------------------------------------------------------------------
# sync_from_job does NOT clobber custom_name
# ---------------------------------------------------------------------------


class TestSyncFromJobDoesNotClobberCustomName:
    def test_custom_name_survives_sync(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)

        idx.upsert(
            {
                "task_id": "j1",
                "job_id": "j1",
                "project_id": "p1",
                "molecule_name": "CCO",
                "task_name": "search",
                "remark": "",
                "display_name": "CCO_search",
                "workflow": "Confsearch",
                "status": "running",
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            }
        )

        result = idx.update_custom_name("j1", "My Custom Name", 0)
        assert result["custom_name"] == "My Custom Name"
        assert result["name_revision"] == 1

        from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

        record = JobRecord(
            id="j1",
            spec=JobSpec(
                workflow="Confsearch",
                name="CCO_search",
                input={"molecule_name": "CCO"},
                molecule_name="CCO",
                task_name="search",
            ),
            status=JobStatus.RUNNING,
            work_dir="/tmp/CCO_search",
            project_id="p1",
        )
        idx.sync_from_job(record)

        row = idx.get("j1")
        assert row["custom_name"] == "My Custom Name"
        assert row["name_revision"] == 1


# ---------------------------------------------------------------------------
# update_custom_name — happy path
# ---------------------------------------------------------------------------


class TestUpdateCustomName:
    def test_set_custom_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        result = idx.update_custom_name("t1", "My Rename", 0)
        assert result["custom_name"] == "My Rename"
        assert result["resolved_name"] == "My Rename"
        assert result["name_revision"] == 1
        assert result["name_updated_at"] is not None
        assert result["default_name"] == "ethanol_sp"

    def test_clear_custom_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "Custom", 0)
        result = idx.update_custom_name("t1", None, 1)
        assert result["custom_name"] is None
        assert result["resolved_name"] == "ethanol_sp"
        assert result["name_revision"] == 2

    def test_revision_increments(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        r1 = idx.update_custom_name("t1", "Name1", 0)
        r2 = idx.update_custom_name("t1", "Name2", r1["name_revision"])
        r3 = idx.update_custom_name("t1", "Name3", r2["name_revision"])
        assert r1["name_revision"] == 1
        assert r2["name_revision"] == 2
        assert r3["name_revision"] == 3


# ---------------------------------------------------------------------------
# update_custom_name — no-op
# ---------------------------------------------------------------------------


class TestUpdateCustomNameNoOp:
    def test_same_value_noop(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "Custom", 0)
        result = idx.update_custom_name("t1", "Custom", 1)
        assert result["name_revision"] == 1
        row = idx.get("t1")
        assert row["name_revision"] == 1

    def test_both_none_noop(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        result = idx.update_custom_name("t1", None, 0)
        assert result["name_revision"] == 0
        row = idx.get("t1")
        assert row["name_revision"] == 0

    def test_noop_no_audit_row(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "Custom", 0)
        idx.update_custom_name("t1", "Custom", 1)

        conn = sqlite3.connect(str(db))
        count = conn.execute(
            "SELECT COUNT(*) FROM organization_events WHERE object_id='t1'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


# ---------------------------------------------------------------------------
# update_custom_name — validation errors
# ---------------------------------------------------------------------------


class TestUpdateCustomNameValidation:
    def test_empty_string_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        with pytest.raises(ValueError, match="empty"):
            idx.update_custom_name("t1", "", 0)

    def test_too_long_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        with pytest.raises(ValueError, match="200"):
            idx.update_custom_name("t1", "x" * 201, 0)

    def test_control_char_rejected(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        with pytest.raises(ValueError, match="control character"):
            idx.update_custom_name("t1", "Task\nName", 0)


# ---------------------------------------------------------------------------
# update_custom_name — revision conflict
# ---------------------------------------------------------------------------


class TestUpdateCustomNameRevisionConflict:
    def test_stale_revision_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "First", 0)

        with pytest.raises(NameRevisionConflictError) as exc_info:
            idx.update_custom_name("t1", "Second", 0)
        assert exc_info.value.current_projection["name_revision"] == 1

    def test_missing_task_raises_lookup_error(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)

        with pytest.raises(LookupError, match="not found"):
            idx.update_custom_name("nonexistent", "Name", 0)


# ---------------------------------------------------------------------------
# Audit rows in organization_events
# ---------------------------------------------------------------------------


class TestAuditRows:
    def test_rename_writes_audit(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "Renamed", 0)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM organization_events WHERE object_id='t1'").fetchall()
        conn.close()
        assert len(rows) == 1
        event = dict(rows[0])
        assert event["object_type"] == "task"
        assert event["action"] == "rename"
        assert json.loads(event["old_value"]) is None
        assert json.loads(event["new_value"]) == "Renamed"
        assert event["created_at"] is not None

    def test_restore_default_writes_audit(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")

        idx.update_custom_name("t1", "Custom", 0)
        idx.update_custom_name("t1", None, 1)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM organization_events WHERE object_id='t1' ORDER BY id"
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert dict(rows[1])["action"] == "restore_default_name"
        assert json.loads(dict(rows[1])["old_value"]) == "Custom"
        assert json.loads(dict(rows[1])["new_value"]) is None

    def test_multiple_tasks_independent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1", display_name="task1")
        _make_task(idx, task_id="t2", display_name="task2")

        idx.update_custom_name("t1", "Custom1", 0)
        idx.update_custom_name("t2", "Custom2", 0)

        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM organization_events").fetchone()[0]
        conn.close()
        assert count == 2


# ---------------------------------------------------------------------------
# Fresh DB schema columns match
# ---------------------------------------------------------------------------


class TestFreshDBSchema:
    def test_task_index_columns_include_custom_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        JobStore(db)
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        conn.close()
        assert "custom_name" in cols
        assert "name_revision" in cols
        assert "name_updated_at" in cols

    def test_custom_name_default_null(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        JobStore(db)
        idx = TaskIndex(db)
        _make_task(idx, task_id="t1")
        row = idx.get("t1")
        assert row["custom_name"] is None
        assert row["name_revision"] == 0
        assert row["name_updated_at"] is None
