# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportUnusedCallResult=false
"""
Tests for task-index organization (T1): migration 014, backfill, overwrite-safe
sync, compare-before-write, purge cascade, move_job wiring.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.migrations import migrate
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex


def _make_spec(
    *,
    workflow: str = "Confsearch",
    molecule_name: str = "CCO",
    task_name: str = "search",
    remark: str = "test",
    tags: list[str] | None = None,
    resources: dict | None = None,
) -> JobSpec:
    return JobSpec(
        workflow=workflow,
        name=f"{molecule_name}_{task_name}",
        molecule_name=molecule_name,
        task_name=task_name,
        remark=remark,
        tags=tags or [],
        resources=resources or {},
        input={"molecule_name": molecule_name},
    )


def _make_record(
    *,
    job_id: str = "job001",
    status: JobStatus = JobStatus.COMPLETED,
    project_id: str = "proj_a",
    work_dir: str = "/tmp/proj_a/CCO_search",
    remote_job_id: str | None = None,
    group_id: str | None = None,
    spec: JobSpec | None = None,
    current_stage: str | None = None,
) -> JobRecord:
    spec = spec or _make_spec()
    return JobRecord(
        id=job_id,
        spec=spec,
        status=status,
        work_dir=work_dir,
        project_id=project_id,
        started_at="2026-01-01T10:00:00" if status != JobStatus.QUEUED else None,
        completed_at="2026-01-01T11:00:00" if status.is_terminal else None,
        current_stage=current_stage,
        progress=1.0 if status == JobStatus.COMPLETED else None,
        remote_job_id=remote_job_id,
        group_id=group_id or job_id,
    )


# ---------------------------------------------------------------------------
# Baseline characterization tests
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_existing_migrations_apply_twice(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        v1 = migrate(db)
        assert v1 >= 1
        conn = sqlite3.connect(str(db))
        schema_v1 = conn.execute(
            "SELECT COUNT(*) AS n FROM _schema_migrations"
        ).fetchone()[0]
        conn.close()
        v2 = migrate(db)
        assert v2 == 0
        conn2 = sqlite3.connect(str(db))
        schema_v2 = conn2.execute(
            "SELECT COUNT(*) AS n FROM _schema_migrations"
        ).fetchone()[0]
        conn2.close()
        assert schema_v1 == schema_v2

    def test_sync_from_job_creates_row(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        idx = TaskIndex(db)
        record = _make_record(job_id="j1", project_id="p1")
        idx.sync_from_job(record)
        row = idx.get("j1")
        assert row is not None
        assert row["task_id"] == "j1"
        assert row["project_id"] == "p1"
        assert row["molecule_name"] == "CCO"
        assert row["workflow"] == "Confsearch"
        assert row["status"] == "completed"

    def test_purge_cascade_removes_jobs_and_children(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = JobStore(db)
        record = _make_record(job_id="j1")
        store.create(record)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO stage_tasks (task_id, job_id, stage_name, state, updated_at) "
            "VALUES ('st1', 'j1', 'S1', 'done', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO artifacts (artifact_id, job_id, artifact_type, file_path, created_at) "
            "VALUES ('a1', 'j1', 'xyz', '/tmp/a.xyz', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        store.purge_cascade("j1")
        conn2 = sqlite3.connect(str(db))
        assert conn2.execute("SELECT COUNT(*) FROM jobs WHERE id='j1'").fetchone()[0] == 0
        assert conn2.execute(
            "SELECT COUNT(*) FROM stage_tasks WHERE job_id='j1'"
        ).fetchone()[0] == 0
        assert conn2.execute(
            "SELECT COUNT(*) FROM artifacts WHERE job_id='j1'"
        ).fetchone()[0] == 0
        conn2.close()


# ---------------------------------------------------------------------------
# New behavior tests (T1 acceptance criteria)
# ---------------------------------------------------------------------------


class TestMigration014:
    def _setup_legacy_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript("""
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
);
        """)
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
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_job_id ON tasks(job_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
        """)
        conn.commit()
        conn.close()
        return db

    def _insert_legacy_job(
        self,
        conn: sqlite3.Connection,
        *,
        job_id: str,
        spec_json: str | None = None,
        status: str = "completed",
        work_dir: str = "/tmp/proj/job",
        project_id: str = "proj1",
        group_id: str | None = None,
        remote_job_id: str | None = None,
        started_at: str | None = "2026-01-01T10:00:00",
        completed_at: str | None = "2026-01-01T11:00:00",
        input_hash: str | None = "abc123",
    ) -> None:
        if spec_json is None:
            spec_json = json.dumps(
                {
                    "workflow": "Confsearch",
                    "name": "CCO_search__01",
                    "input": {"molecule_name": "CCO"},
                    "method": {},
                    "resources": {},
                    "tags": [],
                    "project_id": project_id,
                    "molecule_name": "CCO",
                    "task_name": "search",
                    "remark": "",
                }
            )
        if group_id is None:
            group_id = job_id
        conn.execute(
            """INSERT INTO jobs (id, workflow, name, status, work_dir, spec_json,
                   created_at, updated_at, started_at, completed_at, project_id,
                   input_hash, current_stage, progress, error, pid, exit_code,
                   remote_job_id, group_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)""",
            (
                job_id,
                "Confsearch",
                "CCO_search",
                status,
                work_dir,
                spec_json,
                "2026-01-01T09:00:00",
                "2026-01-01T11:00:00",
                started_at,
                completed_at,
                project_id,
                input_hash,
                remote_job_id,
                group_id,
            ),
        )

    def test_014_idempotent(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        v1 = migrate(db)
        assert v1 >= 1
        v2 = migrate(db)
        assert v2 == 0

    def test_014_adds_columns(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        migrate(db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        for expected in [
            "molecule_key", "tags", "archived", "batch_id",
            "last_activity_at", "started_at", "completed_at",
            "group_id", "progress",
        ]:
            assert expected in cols, f"Missing column: {expected}"
        conn.close()

    def test_014_adds_indexes(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        migrate(db)
        conn = sqlite3.connect(str(db))
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        for expected in [
            "idx_tasks_project_archived",
            "idx_tasks_molecule_key",
            "idx_tasks_batch_id",
        ]:
            assert expected in indexes, f"Missing index: {expected}"
        conn.close()

    def test_014_backfill_fills_tasks(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        conn = sqlite3.connect(str(db))
        self._insert_legacy_job(conn, job_id="j1")
        self._insert_legacy_job(
            conn,
            job_id="j2",
            spec_json=json.dumps(
                {
                    "workflow": "Confsearch",
                    "name": "BCB_search__01",
                    "input": {"molecule_name": "BCB-Allene"},
                    "method": {},
                    "resources": {"batch_id": "batch_shared_001"},
                    "tags": ["chem-16"],
                    "project_id": "proj1",
                    "molecule_name": "BCB-Allene",
                    "task_name": "search",
                    "remark": "test batch",
                }
            ),
        )
        self._insert_legacy_job(
            conn,
            job_id="j3",
            spec_json=json.dumps(
                {
                    "workflow": "BatchOptimize",
                    "name": "MeOH_opt__01",
                    "input": {"molecule_name": "MeOH"},
                    "method": {},
                    "resources": {"batch_id": "batch_shared_001"},
                    "tags": [],
                    "project_id": "proj1",
                    "molecule_name": "MeOH",
                    "task_name": "opt",
                    "remark": "",
                }
            ),
        )
        self._insert_legacy_job(conn, job_id="j4", group_id="root_999")
        self._insert_legacy_job(conn, job_id="j5")
        conn.commit()
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = {r["task_id"]: dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()}
        assert len(rows) == 5

        assert rows["j2"]["batch_id"] == "batch_shared_001"
        assert rows["j3"]["batch_id"] == "batch_shared_001"
        assert rows["j1"]["batch_id"] is None
        assert rows["j4"]["batch_id"] is None
        assert rows["j5"]["batch_id"] is None

        assert rows["j4"]["group_id"] == "root_999"

        j2_gid = conn.execute(
            "SELECT group_id FROM jobs WHERE id='j2'"
        ).fetchone()["group_id"]
        assert rows["j2"]["group_id"] == j2_gid
        conn.close()

    def test_014_corrupt_spec_json_yields_defaults(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        conn = sqlite3.connect(str(db))
        self._insert_legacy_job(conn, job_id="j_corrupt", spec_json="NOT_VALID_JSON{{{")
        self._insert_legacy_job(conn, job_id="j_ok")
        conn.commit()
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = dict(
            conn.execute("SELECT * FROM tasks WHERE task_id='j_corrupt'").fetchone()
        )
        assert row["molecule_key"] == ""
        assert row["tags"] == "[]"
        assert row["task_id"] == "j_corrupt"
        row_ok = dict(
            conn.execute("SELECT * FROM tasks WHERE task_id='j_ok'").fetchone()
        )
        assert row_ok["molecule_name"] == "CCO"
        conn.close()

    def test_014_idempotent_backfill_no_duplicates(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        conn = sqlite3.connect(str(db))
        self._insert_legacy_job(conn, job_id="j1")
        conn.commit()
        conn.close()

        migrate(db)
        migrate(db)

        conn = sqlite3.connect(str(db))
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert count == 1
        conn.close()

    def test_014_existing_task_rows_not_overwritten(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        idx = TaskIndex(db)
        record = _make_record(
            job_id="j1",
            spec=_make_spec(molecule_name="CustomMol", remark="user-edit"),
        )
        idx.sync_from_job(record)
        idx._run(
            "UPDATE tasks SET tags='[\"user-tag\"]', remark='user-edit',"
            " molecule_name='CustomMol' WHERE task_id='j1'"
        )

        migrate(db)

        row = idx.get("j1")
        assert row is not None
        assert row["tags"] == '["user-tag"]'
        assert row["remark"] == "user-edit"
        assert row["molecule_name"] == "CustomMol"

    def test_014_idempotent_backfill_fieldwise_identical(self, tmp_path: Path) -> None:
        db = self._setup_legacy_db(tmp_path)
        conn = sqlite3.connect(str(db))
        self._insert_legacy_job(conn, job_id="j1")
        self._insert_legacy_job(
            conn,
            job_id="j2",
            spec_json=json.dumps(
                {
                    "workflow": "Confsearch",
                    "name": "BCB_search__01",
                    "input": {"molecule_name": "BCB-Allene"},
                    "method": {},
                    "resources": {"batch_id": "batch_shared_001"},
                    "tags": ["chem-16"],
                    "project_id": "proj1",
                    "molecule_name": "BCB-Allene",
                    "task_name": "search",
                    "remark": "test batch",
                }
            ),
        )
        self._insert_legacy_job(conn, job_id="j3")
        self._insert_legacy_job(conn, job_id="j4", group_id="root_999")
        self._insert_legacy_job(conn, job_id="j5")
        conn.commit()
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows_after_first = {
            r["task_id"]: dict(r)
            for r in conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        }
        conn.close()

        migrate(db)

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows_after_second = {
            r["task_id"]: dict(r)
            for r in conn.execute("SELECT * FROM tasks ORDER BY task_id").fetchall()
        }
        conn.close()

        assert rows_after_first.keys() == rows_after_second.keys()
        for task_id in rows_after_first:
            assert rows_after_first[task_id] == rows_after_second[task_id], (
                f"Field-by-field mismatch for {task_id} after second migrate()"
            )


class TestOverwriteProtection:
    def _setup_with_task(self, tmp_path: Path) -> TaskIndex:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        record = _make_record(
            job_id="j1",
            spec=_make_spec(remark="original", tags=["orig-tag"]),
        )
        idx.sync_from_job(record)
        return idx

    def test_sync_from_job_preserves_user_edits(self, tmp_path: Path) -> None:
        idx = self._setup_with_task(tmp_path)
        idx._run(
            "UPDATE tasks SET remark='user-remark', tags='[\"custom\"]',"
            " archived=1, molecule_name='UserMol' WHERE task_id='j1'"
        )
        record = _make_record(
            job_id="j1",
            spec=_make_spec(remark="new-remark", tags=["new-tag"], molecule_name="CCO"),
            work_dir="/tmp/proj_a/CCO_search__01",
        )
        idx.sync_from_job(record)
        row = idx.get("j1")
        assert row["remark"] == "user-remark"
        assert row["tags"] == '["custom"]'
        assert row["archived"] == 1
        assert row["molecule_name"] == "UserMol"

    def test_sync_from_job_updates_display_fields(self, tmp_path: Path) -> None:
        idx = self._setup_with_task(tmp_path)
        record = _make_record(job_id="j1", work_dir="/tmp/new_dir")
        idx.sync_from_job(record)
        row = idx.get("j1")
        assert row["display_name"] == "new_dir"
        assert row["task_dir_name"] == "new_dir"

    def test_sync_job_transition_preserves_user_edits(self, tmp_path: Path) -> None:
        idx = self._setup_with_task(tmp_path)
        idx._run(
            "UPDATE tasks SET remark='user-remark', tags='[\"x\"]', archived=1 WHERE task_id='j1'"
        )
        record = _make_record(
            job_id="j1",
            status=JobStatus.RUNNING,
            spec=_make_spec(remark="new-remark", tags=["y"]),
        )
        idx.sync_job_transition(record)
        row = idx.get("j1")
        assert row["remark"] == "user-remark"
        assert row["tags"] == '["x"]'
        assert row["archived"] == 1


class TestCompareBeforeWrite:
    def _setup_running_task(self, tmp_path: Path) -> TaskIndex:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        record = _make_record(
            job_id="j1",
            status=JobStatus.RUNNING,
            current_stage="S1",
        )
        idx.sync_from_job(record)
        return idx

    def test_no_write_when_unchanged(self, tmp_path: Path) -> None:
        idx = self._setup_running_task(tmp_path)
        row1 = idx.get("j1")
        ts1_updated = row1["updated_at"]
        ts1_activity = row1["last_activity_at"]

        record = _make_record(
            job_id="j1",
            status=JobStatus.RUNNING,
            current_stage="S1",
        )
        idx.sync_job_transition(record)
        row2 = idx.get("j1")
        assert row2["updated_at"] == ts1_updated
        assert row2["last_activity_at"] == ts1_activity

        idx.sync_job_transition(record)
        row3 = idx.get("j1")
        assert row3["updated_at"] == ts1_updated
        assert row3["last_activity_at"] == ts1_activity

    def test_status_change_updates_last_activity(self, tmp_path: Path) -> None:
        idx = self._setup_running_task(tmp_path)
        prior_activity = idx.get("j1")["last_activity_at"]
        record = _make_record(
            job_id="j1",
            status=JobStatus.COMPLETED,
            current_stage="S2",
        )
        idx.sync_job_transition(record)
        row = idx.get("j1")
        assert row["status"] == "completed"
        assert row["current_stage"] == "S2"
        assert row["last_activity_at"] is not None
        assert row["last_activity_at"] != prior_activity

    def test_started_at_set_once(self, tmp_path: Path) -> None:
        idx = self._setup_running_task(tmp_path)
        row_before = idx.get("j1")
        original_started = row_before["started_at"]

        record = _make_record(job_id="j1", status=JobStatus.COMPLETED)
        idx.sync_job_transition(record)
        row_after = idx.get("j1")
        assert row_after["started_at"] == original_started

    def test_completed_at_written_on_terminal(self, tmp_path: Path) -> None:
        idx = self._setup_running_task(tmp_path)
        record = _make_record(job_id="j1", status=JobStatus.COMPLETED)
        idx.sync_job_transition(record)
        row = idx.get("j1")
        assert row["completed_at"] is not None

    def test_progress_only_change_updates_progress_not_activity(self, tmp_path: Path) -> None:
        idx = self._setup_running_task(tmp_path)
        row_before = idx.get("j1")
        old_activity = row_before["last_activity_at"]

        record = _make_record(
            job_id="j1",
            status=JobStatus.RUNNING,
            current_stage="S1",
        )
        record.progress = 0.5
        idx.sync_job_transition(record)
        row = idx.get("j1")
        assert row["progress"] == 0.5
        assert row["last_activity_at"] == old_activity


class TestPurgeCascadeTasks:
    def test_purge_removes_tasks(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        record = _make_record(job_id="j1")
        idx.sync_from_job(record)
        assert idx.get("j1") is not None

        store = JobStore(db)
        store.create(record)
        store.purge_cascade("j1")

        assert idx.get("j1") is None

    def test_purge_no_tasks_row_no_error(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        store = JobStore(db)
        record = _make_record(job_id="j1")
        store.create(record)
        store.purge_cascade("j1")


class TestUpdateProject:
    def test_update_project(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        record = _make_record(job_id="j1", project_id="p1")
        idx.sync_from_job(record)
        idx.update_project("j1", "p2")
        row = idx.get("j1")
        assert row["project_id"] == "p2"

    def test_update_project_noop_missing(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        idx.update_project("nonexistent", "p2")


class TestDelete:
    def test_delete_existing(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        record = _make_record(job_id="j1")
        idx.sync_from_job(record)
        idx.delete("j1")
        assert idx.get("j1") is None

    def test_delete_missing_noop(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        idx.delete("nonexistent")


class TestMoveJobWiring:
    def test_move_job_updates_tasks_project(self, tmp_path: Path) -> None:
        from acp.scheduler.projects import ProjectManager

        db = tmp_path / "test.db"
        migrate(db)
        store = JobStore(db)
        projects = ProjectManager(store, tmp_path)
        p1 = projects.create_project("Project1", str(tmp_path / "p1"))
        p2 = projects.create_project("Project2", str(tmp_path / "p2"))
        p1_id = p1["project_id"]
        p2_id = p2["project_id"]

        idx = TaskIndex(db)
        record = _make_record(
            job_id="j1",
            project_id=p1_id,
            work_dir=str(tmp_path / "p1" / "task"),
        )
        store.create(record)
        idx.sync_from_job(record)

        (tmp_path / "p1" / "task").mkdir(parents=True, exist_ok=True)

        idx.update_project("j1", p2_id)
        row = idx.get("j1")
        assert row["project_id"] == p2_id

    def test_move_job_via_manager_wiring(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from acp.scheduler.manager import JobManager

        runner = MagicMock()
        runner.poll.return_value = (False, None)
        mgr = JobManager(run_root=tmp_path, runner=runner, poll_interval=30)
        try:
            p1 = mgr.projects.create_project("Source", str(tmp_path / "src"))
            p2 = mgr.projects.create_project("Target", str(tmp_path / "tgt"))
            p1_id = p1["project_id"]
            p2_id = p2["project_id"]

            record = mgr.submit(
                JobSpec(
                    workflow="Confsearch",
                    name="demo",
                    input={"source": "CCO"},
                    molecule_name="ethanol",
                    task_name="opt",
                    remark="test",
                    project_id=p1_id,
                )
            )
            job_id = record.id
            mgr.cancel(job_id)
            assert mgr.tasks is not None
            task_row = mgr.tasks.get(job_id)
            assert task_row is not None
            assert task_row["project_id"] == p1_id

            mgr.move_job(job_id, p2_id)

            task_row2 = mgr.tasks.get(job_id)
            assert task_row2 is not None
            assert task_row2["project_id"] == p2_id
        finally:
            mgr.shutdown()


class TestMoleculeGroupKey:
    def test_basic(self) -> None:
        from acp.scheduler.naming import molecule_group_key
        assert molecule_group_key("CCO") == "CCO"

    def test_strips_whitespace(self) -> None:
        from acp.scheduler.naming import molecule_group_key
        assert molecule_group_key("  C C O  ") == "C C O"

    def test_collapses_internal_whitespace(self) -> None:
        from acp.scheduler.naming import molecule_group_key
        assert molecule_group_key("C   O") == "C O"

    def test_empty_stays_empty(self) -> None:
        from acp.scheduler.naming import molecule_group_key
        assert molecule_group_key("") == ""
