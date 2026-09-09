"""Tests for the v2 server-side task index (scheduler ``tasks`` table + TaskIndex)."""

# pyright: reportPrivateUsage=false, reportUnusedCallResult=false

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex

_EXPECTED_COLUMNS = {
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
}


def _table_columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {row[1] for row in rows}


def _make_record(tmp_path: Path, *, remote_job_id: str | None = None) -> JobRecord:
    return JobRecord(
        id="20260822_001_demo",
        spec=JobSpec(
            workflow="energy",
            name="demo-job",
            input={"source": "CCO"},
            molecule_name="ethanol",
            task_name="sp",
            remark="r1",
            project_id="proj-1",
            input_hash="abc123",
        ),
        status=JobStatus.RUNNING,
        work_dir=str(tmp_path / "runs" / "ethanol_sp_r1"),
        project_id="proj-1",
        input_hash="abc123",
        current_stage="dft_opt",
        remote_job_id=remote_job_id,
    )


def test_job_store_migration_creates_tasks_table(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    JobStore(db)
    assert _table_columns(db, "tasks") == _EXPECTED_COLUMNS

    # Idempotent: a second store/index construction applies zero migrations.
    assert JobStore(db) is not None
    assert _table_columns(db, "tasks") == _EXPECTED_COLUMNS


def test_upsert_get_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    index = TaskIndex(store.db_path)

    assert index.get("missing") is None

    payload = {
        "task_id": "t1",
        "job_id": "t1",
        "project_id": "p1",
        "molecule_name": "ethanol",
        "task_name": "sp",
        "remark": None,
        "workflow": "energy",
        "status": "queued",
        "node_path": "/tmp/runs/ethanol_sp",
        "created_at": "2026-08-22T00:00:00+00:00",
        "updated_at": "2026-08-22T00:00:00+00:00",
    }
    index.upsert(payload)
    row = index.get("t1")
    assert row is not None
    assert row["task_id"] == "t1"
    assert row["job_id"] == "t1"
    assert row["project_id"] == "p1"
    assert row["remark"] == ""  # None coerced to ''
    assert row["status"] == "queued"
    assert row["storage_mode"] == "local"  # column default on missing key
    assert row["layout_version"] == 2

    payload["status"] = "running"
    index.upsert(payload)
    assert index.get("t1")["status"] == "running"


def test_task_index_accepts_shared_connection(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    JobStore(db)
    conn = sqlite3.connect(str(db))
    try:
        index = TaskIndex(conn)
        index.upsert(
            {"task_id": "t-conn", "job_id": "t-conn", "created_at": "x", "updated_at": "x"}
        )
        row = index.get("t-conn")
    finally:
        conn.close()
    assert row is not None
    assert row["task_id"] == "t-conn"


def test_sync_from_job_local(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    record = _make_record(tmp_path)

    index.sync_from_job(record)
    row = index.get(record.id)
    assert row is not None
    assert row["task_id"] == record.id
    assert row["job_id"] == record.id
    assert row["task_dir_name"] == "ethanol_sp_r1"
    assert row["node_id"] == "local"
    assert row["storage_mode"] == "local"
    assert row["node_path"] == record.work_dir
    assert row["status"] == "running"
    assert row["workflow"] == "energy"
    assert row["molecule_name"] == "ethanol"
    assert row["task_name"] == "sp"
    assert row["remark"] == "r1"
    assert row["display_name"] == "ethanol_sp_r1"
    assert row["input_hash"] == "abc123"
    assert row["current_stage"] == "dft_opt"
    assert row["layout_version"] == 2


def test_sync_from_job_remote_maps_sftp(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    record = _make_record(tmp_path, remote_job_id="12345")

    index.sync_from_job(record)
    row = index.get(record.id)
    assert row is not None
    assert row["node_id"] == "remote"
    assert row["storage_mode"] == "sftp"


def test_sync_from_job_node_id_uses_real_node_name(tmp_path: Path) -> None:
    """A dispatched remote job indexes under its real execution node name."""
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    record = _make_record(tmp_path, remote_job_id="12345")
    record.result = {"node": "comp-01", "host": "comp-01.example.com"}

    index.sync_from_job(record)
    row = index.get(record.id)
    assert row is not None
    assert row["node_id"] == "comp-01"
    assert row["storage_mode"] == "sftp"


def test_sync_from_job_local_execution_target_stays_local(tmp_path: Path) -> None:
    """A dispatched local job keeps ``node_id == "local"`` even when its
    result carries ``execution_target`` provenance."""
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    record = _make_record(tmp_path)
    record.result = {"execution_target": "local", "execution_kind": "local"}

    index.sync_from_job(record)
    row = index.get(record.id)
    assert row is not None
    assert row["node_id"] == "local"
    assert row["storage_mode"] == "local"


def test_update_status(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    record = _make_record(tmp_path)
    record.status = JobStatus.QUEUED
    record.current_stage = None
    index.sync_from_job(record)

    index.update_status(
        record.id, "running", "s1_conformers", updated_at="2026-08-22T01:00:00+00:00"
    )
    row = index.get(record.id)
    assert row is not None
    assert row["status"] == "running"
    assert row["current_stage"] == "s1_conformers"
    assert row["updated_at"] == "2026-08-22T01:00:00+00:00"

    # Without current_stage the stage is preserved; missing rows are a no-op.
    index.update_status(record.id, "completed")
    row = index.get(record.id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["current_stage"] == "s1_conformers"
    assert row["updated_at"] != "2026-08-22T01:00:00+00:00"
    index.update_status("no-such-task", "running")


def test_list_by_project(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    index = TaskIndex(store.db_path)
    for i in range(3):
        index.upsert(
            {
                "task_id": f"t{i}",
                "job_id": f"t{i}",
                "project_id": "p1" if i < 2 else "p2",
                "created_at": f"2026-08-22T00:0{i}:00+00:00",
                "updated_at": f"2026-08-22T00:0{i}:00+00:00",
            }
        )

    rows = index.list_by_project("p1")
    assert [r["task_id"] for r in rows] == ["t1", "t0"]  # newest first
    assert index.list_by_project("p2")[0]["task_id"] == "t2"
    assert index.list_by_project("p1", limit=1) == [rows[0]]
    assert index.list_by_project("p-empty") == []


def test_manager_submit_indexes_task(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    # Freeze dispatch so the row is deterministically still "queued"
    # (same instance-attribute patching style as the _poll_job tests).
    mgr._execute_submission = lambda job_id: None  # type: ignore[method-assign]

    record = mgr.submit(
        JobSpec(
            workflow="fake",
            name="idx-demo",
            input={"source": "CCO"},
            molecule_name="ethanol",
            task_name="sp",
            remark="r1",
        )
    )

    assert mgr.tasks is not None
    row = mgr.tasks.get(record.id)
    assert row is not None
    assert row["task_id"] == record.id
    assert row["job_id"] == record.id
    assert row["status"] == "queued"
    assert row["workflow"] == "fake"
    assert row["node_id"] == "local"
    assert row["storage_mode"] == "local"
    assert row["project_id"] == mgr.default_project_id
    assert row["task_dir_name"]

    mgr.tasks.update_status(record.id, "running", "stage-1")
    assert mgr.tasks.get(record.id)["status"] == "running"
    mgr.shutdown()


def test_manager_survives_broken_task_index(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    mgr._execute_submission = lambda job_id: None  # type: ignore[method-assign]
    mgr.tasks = None  # simulate disabled index

    record = mgr.submit(JobSpec(workflow="fake", name="no-index", input={"source": "CCO"}))
    assert mgr.store.get(record.id) is not None
    mgr.shutdown()


try:  # paramiko not installed — remote-config tests skip
    from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
except ImportError:  # pragma: no cover
    RemoteExecutionConfig = None
    RemoteNode = None

_requires_remote_config = pytest.mark.skipif(
    RemoteNode is None, reason="paramiko not installed"
)


def _real_node(name: str, max_jobs: int = 8) -> RemoteNode:
    return RemoteNode(
        name=name,
        host=f"{name}.example.com",
        username="qc",
        remote_work_dir="/scratch/qc/acp",
        remote_code_dir="/home/qc/acp_code",
        max_concurrent_jobs=max_jobs,
        enabled=True,
    )


class _NodeAwareRemoteRunner:
    """Stands in for ``RemoteJobRunner.submit_remote``: records the node."""

    def submit_remote(self, record, event_log, target_node=None) -> str:
        result = dict(record.result or {})
        result["node"] = target_node or "comp-01"
        result["lsf_job_id"] = "424242"
        record.result = result
        return "424242"

    def poll_remote(self, record, event_log, cancel_event):
        return (False, None)


@_requires_remote_config
def test_manager_remote_submit_syncs_real_node(tmp_path: Path) -> None:
    """After a successful remote submit the task row and jobs columns carry
    the real execution node name, not the pre-dispatch ``"local"`` value."""
    from types import SimpleNamespace

    node = _real_node("comp-01")
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        mgr.registry.status_provider = lambda name: SimpleNamespace(
            name=name, status="ready", running_jobs=0, disk_usage_pct=0
        )
        mgr.remote_runner = _NodeAwareRemoteRunner()  # type: ignore[assignment]

        work_dir = tmp_path / "sub8"
        work_dir.mkdir(parents=True, exist_ok=True)
        mgr.store.create(
            JobRecord(
                id="sub8",
                spec=JobSpec(
                    workflow="Confsearch",
                    name="demo",
                    method={"protocol": "xtb-crest", "refinement_policy": "screen"},
                    execution_mode="remote",
                    target_node="comp-01",
                ),
                status=JobStatus.QUEUED,
                work_dir=str(work_dir),
            )
        )

        assert mgr._submit_job("sub8") is True

        stored = mgr.store.get("sub8")
        assert stored is not None
        assert stored.node_id == "comp-01"
        assert stored.host == "comp-01.example.com"
        assert stored.result is not None and stored.result["node"] == "comp-01"

        assert mgr.tasks is not None
        row = mgr.tasks.get("sub8")
        assert row is not None
        assert row["node_id"] == "comp-01"
        assert row["storage_mode"] == "sftp"
    finally:
        mgr.shutdown()
