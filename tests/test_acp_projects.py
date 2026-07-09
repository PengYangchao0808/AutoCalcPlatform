"""Tests for scheduler migrations and project management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from acp.scheduler.jobs import JobSpec
from acp.scheduler.manager import JobManager
from acp.scheduler.migrations import migrate
from acp.scheduler.store import JobStore


def test_migration_creates_projects_table(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    JobStore(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()

    assert row is not None


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    JobStore(db_path)

    assert migrate(db_path) == 0
    assert migrate(db_path) == 0


def test_default_project_auto_created(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        project = mgr.projects.get_project(mgr.default_project_id)
        assert project is not None
        assert project["name"] == "Uncategorized"
        assert Path(project["run_root"]).exists()
    finally:
        mgr.shutdown()


def test_job_without_project_gets_default(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        record = mgr.submit(JobSpec(workflow="fake", name="demo", input={"source": "CCO"}))
        assert record.project_id == mgr.default_project_id
        assert record.spec.project_id == mgr.default_project_id
        assert record.input_hash is not None
        assert Path(record.work_dir).parent.name == mgr.default_project_id
    finally:
        mgr.shutdown()


def test_project_crud(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        created = mgr.projects.create_project(
            "Alpha",
            description="first",
            tags=["x", "y"],
            settings={"color": "blue"},
        )
        fetched = mgr.projects.get_project(created["project_id"])
        assert fetched == created

        listed = {project["project_id"] for project in mgr.projects.list_projects()}
        assert created["project_id"] in listed

        updated = mgr.projects.update_project(
            created["project_id"],
            name="Alpha-2",
            tags=["z"],
            settings={"color": "green"},
        )
        assert updated is not None
        assert updated["name"] == "Alpha-2"
        assert updated["tags"] == ["z"]
        assert updated["settings"] == {"color": "green"}

        assert mgr.projects.delete_project(created["project_id"], delete_data=True) is True
        assert mgr.projects.get_project(created["project_id"]) is None
        assert not Path(created["run_root"]).exists()
    finally:
        mgr.shutdown()


def test_job_list_by_project(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        alpha = mgr.projects.create_project("Alpha")
        beta = mgr.projects.create_project("Beta")

        first = mgr.submit(
            JobSpec(workflow="fake", input={"source": "A"}, project_id=alpha["project_id"])
        )
        second = mgr.submit(
            JobSpec(workflow="fake", input={"source": "B"}, project_id=beta["project_id"])
        )

        alpha_jobs = mgr.list_jobs_by_project(alpha["project_id"])
        beta_jobs = mgr.list_jobs_by_project(beta["project_id"])

        assert [job.id for job in alpha_jobs] == [first.id]
        assert [job.id for job in beta_jobs] == [second.id]
    finally:
        mgr.shutdown()
