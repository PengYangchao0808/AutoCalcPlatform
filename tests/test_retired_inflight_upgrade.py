"""Tests for retired-workflow in-flight job sweep on manager startup (todo 42)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import _RETIRED_INFLIGHT_REASON, JobManager
from acp.scheduler.store import JobStore


def _make_record(
    job_id: str,
    workflow: str,
    status: JobStatus,
    work_dir: Path,
    *,
    pid: int | None = None,
    remote_job_id: str | None = None,
    result: dict | None = None,
) -> JobRecord:
    spec = JobSpec(workflow=workflow, name=f"test_{workflow}", input={"source": "CCO"})
    return JobRecord(
        id=job_id,
        spec=spec,
        status=status,
        work_dir=str(work_dir),
        pid=pid,
        remote_job_id=remote_job_id,
        result=result,
    )


def test_running_retired_swept_to_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "running_job"
    work_dir.mkdir()
    record = _make_record("job-running", "Lowconfirm", JobStatus.RUNNING, work_dir)
    store.create(record)
    assert store.get("job-running") is not None
    assert store.get("job-running").status == JobStatus.RUNNING

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        swept = store.get("job-running")
        assert swept is not None
        assert swept.status == JobStatus.FAILED
        assert swept.error == _RETIRED_INFLIGHT_REASON
        assert swept.completed_at is not None
    finally:
        mgr.shutdown()


def test_queued_retired_swept_to_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "queued_job"
    work_dir.mkdir()
    record = _make_record("job-queued", "mechanism", JobStatus.QUEUED, work_dir)
    store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        swept = store.get("job-queued")
        assert swept is not None
        assert swept.status == JobStatus.FAILED
        assert swept.error == _RETIRED_INFLIGHT_REASON
    finally:
        mgr.shutdown()


def test_local_paused_retired_swept_to_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "paused_local_job"
    work_dir.mkdir()
    record = _make_record("job-paused-local", "Highconfirm", JobStatus.PAUSED, work_dir, pid=99999)
    store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        swept = store.get("job-paused-local")
        assert swept is not None
        assert swept.status == JobStatus.FAILED
        assert swept.error == _RETIRED_INFLIGHT_REASON
    finally:
        mgr.shutdown()


def test_remote_paused_retired_swept_to_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "paused_remote_job"
    work_dir.mkdir()
    record = _make_record(
        "job-paused-remote",
        "optfreq",
        JobStatus.PAUSED,
        work_dir,
        remote_job_id="12345",
        result={"execution_kind": "remote", "node": "node1"},
    )
    store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        swept = store.get("job-paused-remote")
        assert swept is not None
        assert swept.status == JobStatus.FAILED
        assert swept.error == _RETIRED_INFLIGHT_REASON
    finally:
        mgr.shutdown()


def test_active_retired_jobs_are_purgeable(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    for i, wf in enumerate(("Lowconfirm", "Highconfirm", "mechanism")):
        work_dir = tmp_path / f"job_{i}"
        work_dir.mkdir(exist_ok=True)
        record = _make_record(f"job-{i}", wf, JobStatus.RUNNING, work_dir)
        store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        for i in range(3):
            swept = store.get(f"job-{i}")
            assert swept is not None
            assert swept.status == JobStatus.FAILED
            assert not swept.status.is_active

        report = mgr.purge_jobs(job_ids=["job-0", "job-1", "job-2"], delete_data=False)
        assert all(r["ok"] for r in report)
        assert all(r["action"] == "purged" for r in report)
        for i in range(3):
            assert store.get(f"job-{i}") is None
    finally:
        mgr.shutdown()


def test_active_workflows_not_swept(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "active_job"
    work_dir.mkdir()
    record = _make_record("job-active", "Confsearch", JobStatus.QUEUED, work_dir)
    store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        active = store.get("job-active")
        assert active is not None
        assert active.status == JobStatus.QUEUED
    finally:
        mgr.shutdown()


def test_completed_retired_not_swept(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "completed_job"
    work_dir.mkdir()
    record = _make_record("job-completed", "Lowconfirm", JobStatus.COMPLETED, work_dir)
    store.create(record)

    mgr = JobManager(run_root=tmp_path, store=store)
    try:
        completed = store.get("job-completed")
        assert completed is not None
        assert completed.status == JobStatus.COMPLETED
    finally:
        mgr.shutdown()


def test_write_methods_removed_from_store(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "test.db")
    assert not hasattr(store, "upsert_mechanism_study")
    assert not hasattr(store, "update_mechanism_study_reaction")
    assert not hasattr(store, "update_mechanism_study_plan")
    assert not hasattr(store, "upsert_decision_point")
    assert hasattr(store, "get_mechanism_study")
    assert hasattr(store, "list_mechanism_studies")
    assert hasattr(store, "get_decision_point")
    assert hasattr(store, "list_decision_points")
    assert hasattr(store, "purge_cascade")


def test_purge_cascade_still_works(tmp_path: Path) -> None:
    db_path = tmp_path / "acp_jobs.db"
    store = JobStore(db_path)
    work_dir = tmp_path / "purge_job"
    work_dir.mkdir()
    record = _make_record("job-purge", "Lowconfirm", JobStatus.FAILED, work_dir)
    store.create(record)
    assert store.get("job-purge") is not None

    store.purge_cascade("job-purge")
    assert store.get("job-purge") is None

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("SELECT * FROM mechanism_studies")
        conn.execute("SELECT * FROM decision_points")


def test_write_route_returns_410(tmp_path: Path) -> None:
    import os

    from fastapi.testclient import TestClient

    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    with TestClient(app) as client:
        response_create = client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "new-study"},
        )
        assert response_create.status_code == 410

        response_list = client.get("/api/v1/mechanism-studies")
        assert response_list.status_code == 200
