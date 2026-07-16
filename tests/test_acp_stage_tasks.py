"""Tests for stage task planning and observation."""

from __future__ import annotations

import json
import time
from pathlib import Path

from acp.scheduler.jobs import JobSpec
from acp.scheduler.manager import JobManager
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore, get_stage_plan


def test_stage_plan_provider_fake() -> None:
    plan = get_stage_plan(JobSpec(workflow="fake"))
    assert [stage.stage_name for stage in plan] == ["init", "compute", "finalize"]


def test_stage_plan_provider_conformer() -> None:
    plan = get_stage_plan(JobSpec(workflow="conformer"))
    assert [stage.stage_name for stage in plan] == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
        "dft_optimize",
        "frequency",
        "single_point",
        "shermo_thermo",
    ]


def test_stage_plan_provider_conformer_zero() -> None:
    plan = get_stage_plan(JobSpec(workflow="conformer", method={"protocol": "zero"}))
    assert [stage.stage_name for stage in plan] == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
        "single_point",
    ]


def test_stage_plan_unknown_workflow_returns_empty() -> None:
    assert get_stage_plan(JobSpec(workflow="unknown")) == []


def test_observer_initializes_pending_tasks(tmp_path: Path) -> None:
    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)

    tasks = observer.initialize_job_stages("job-1", JobSpec(workflow="fake"))

    assert len(tasks) == 3
    assert [task.state for task in tasks] == ["pending", "pending", "pending"]


def test_observer_mirrors_lifecycle_files(tmp_path: Path) -> None:
    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)
    task = observer.initialize_job_stages("job-1", JobSpec(workflow="fake"))[0]
    work_dir = tmp_path / "work"
    stage_dir = work_dir / "stage_tasks" / task.stage_name
    stage_dir.mkdir(parents=True)

    started_path = stage_dir / f"{task.task_id}.started.json"
    started_path.write_text(
        json.dumps(
            {
                "task_id": task.task_id,
                "stage": task.stage_name,
                "status": "running",
                "pid": 123,
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    observer.poll_and_mirror("job-1", work_dir)
    running = store.get(task.task_id)
    assert running is not None
    assert running.state == "running"
    assert running.pid == 123

    completed_path = stage_dir / f"{task.task_id}.completed.json"
    completed_path.write_text(
        json.dumps(
            {
                "task_id": task.task_id,
                "stage": task.stage_name,
                "status": "completed",
                "pid": 123,
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    observer.poll_and_mirror("job-1", work_dir)
    completed = store.get(task.task_id)
    assert completed is not None
    assert completed.state == "completed"
    assert completed.exit_status == 0


def test_observer_finalizes_unfinished(tmp_path: Path) -> None:
    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)
    observer.initialize_job_stages("job-1", JobSpec(workflow="fake"))

    observer.finalize_job("job-1", "failed")

    assert {task.state for task in store.list_by_job("job-1")} == {"failed"}


def test_fake_job_creates_stage_tasks(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        record = mgr.submit(JobSpec(workflow="fake", name="stages", input={"source": "CCO"}))

        current = mgr.get(record.id)
        for _ in range(40):
            current = mgr.get(record.id)
            assert current is not None
            if current.status.is_terminal:
                break
            time.sleep(0.5)

        assert current is not None
        assert current.status.value == "completed"
        tasks = mgr.stage_tasks.list_by_job(record.id)
        assert [task.stage_name for task in tasks] == ["init", "compute", "finalize"]
        assert {task.state for task in tasks} == {"completed"}
        for task in tasks:
            stage_dir = Path(record.work_dir) / "stage_tasks" / task.stage_name
            assert (stage_dir / f"{task.task_id}.started.json").exists()
            assert (stage_dir / f"{task.task_id}.completed.json").exists()
    finally:
        mgr.shutdown()
