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


def test_stage_plan_unknown_workflow_returns_empty() -> None:
    assert get_stage_plan(JobSpec(workflow="unknown")) == []


def test_mechanism_stage_plan_uses_study_phases() -> None:
    plan = get_stage_plan(JobSpec(workflow="mechanism"))
    assert plan == []


import pytest

from acp.scheduler.stage_tasks import PlanCompiler


@pytest.mark.parametrize(
    "workflow,method,expected",
    [
        ("singlepoint", {}, ["single_point"]),
        ("optimize", {}, ["optimize"]),
        ("frequency", {}, ["frequency"]),
        ("scan", {}, ["scan"]),
        ("irc", {}, ["irc"]),
        ("xtb_optimize", {}, ["xtb_optimize"]),
        ("PESsearch", {"mode": "bond_length_scan"}, [
            "prepare", "materialize_input", "validate_coordinate",
            "run_relaxed_scan", "extract_frames", "run_single_points",
            "build_profile", "select_candidates", "finalize",
        ]),
        ("PESsearch", {"mode": "path"}, [
            "prepare", "path_search", "candidate_extract", "finalize",
        ]),
        ("BatchOptimize", {"profile": "opt_only"}, ["prepare", "optimize", "finalize"]),
        ("BatchOptimize", {"profile": "opt_freq"}, ["prepare", "optimize", "frequency", "finalize"]),
        ("BatchOptimize", {"profile": "opt_freq_sp"}, ["prepare", "optimize", "frequency", "single_point", "finalize"]),
        ("BatchOptimize", {"profile": "opt_freq_sp_thermo"}, ["prepare", "optimize", "frequency", "single_point", "thermochemistry", "finalize"]),
    ],
)
def test_plancompiler_expected_sequences(workflow: str, method: dict, expected: list[str]) -> None:
    plan = PlanCompiler.compile(JobSpec(workflow=workflow, method=method))
    assert [s.stage_name for s in plan] == expected


def test_plancompiler_batchoptimize_profile() -> None:
    plan = PlanCompiler.compile(JobSpec(workflow="BatchOptimize", method={"profile": "opt_freq_sp_thermo"}))
    names = [s.stage_name for s in plan]
    assert "thermochemistry" in names
    assert names[0] == "prepare"
    assert names[-1] == "finalize"


def test_plancompiler_rejects_retired() -> None:
    with pytest.raises(ValueError, match="retired"):
        PlanCompiler.compile(JobSpec(workflow="mechanism"))
    with pytest.raises(ValueError, match="retired"):
        PlanCompiler.compile(JobSpec(workflow="ensemble"))
    with pytest.raises(ValueError, match="retired"):
        PlanCompiler.compile(JobSpec(workflow="energy"))


def test_plancompiler_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="no mapping"):
        PlanCompiler.compile(JobSpec(workflow="unknown_workflow"))


def test_pessearch_bond_scan_stage_plan() -> None:
    plan = get_stage_plan(JobSpec(workflow="PESsearch", method={"mode": "bond_length_scan"}))
    names = [s.stage_name for s in plan]
    assert names[0] == "prepare"
    assert names[-1] == "finalize"
    assert "run_relaxed_scan" in names
    assert len(names) == 9


def test_pessearch_path_stage_plan() -> None:
    plan = get_stage_plan(JobSpec(workflow="PESsearch", method={"mode": "path"}))
    names = [s.stage_name for s in plan]
    assert names == ["prepare", "path_search", "candidate_extract", "finalize"]


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


def test_observer_mirrors_status_detail(tmp_path: Path) -> None:
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
                "status_detail": "cycle 3/10",
                "pid": 123,
                "started_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    observer.poll_and_mirror("job-1", work_dir)
    running = store.get(task.task_id)
    assert running is not None
    assert running.status_detail == "cycle 3/10"


def test_observe_state_syncs_current_stage_to_status_detail(tmp_path: Path) -> None:
    """``JobRunner._observe_state`` mirrors the running stage onto stage_tasks."""
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.runner import JobRunner

    store = StageTaskStore(tmp_path / "jobs.db")
    observer = StageTaskObserver(store)
    runner = JobRunner(stage_task_observer=observer)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "running",
                "current_stage": "compute",
                "stages": {
                    "init": {"status": "completed"},
                    "compute": {"status": "running"},
                    "finalize": {"status": "pending"},
                },
            }
        ),
        encoding="utf-8",
    )
    spec = JobSpec(workflow="fake", name="sync", input={"source": "CCO"})
    observer.initialize_job_stages("job-1", spec)
    record = JobRecord(
        id="job-1",
        spec=spec,
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
    )
    event_log = JobEventLog(work_dir / "events.jsonl")
    runner._observe_state(record, event_log, work_dir / "state.json", set())

    tasks = {task.stage_name: task for task in store.list_by_job("job-1")}
    assert tasks["compute"].status_detail == "compute"
    assert tasks["init"].status_detail is None
    assert record.current_stage == "compute"


# ── ⑥ script_gen mechanism-cleanup tests ────────────────────────────────


def test_script_gen_no_mechanism_config() -> None:
    from acp.scheduler.remote import script_gen

    assert not hasattr(script_gen, "MECHANISM_CONFIG_FILENAME")
    import inspect

    source = inspect.getsource(script_gen)
    assert "MECHANISM_CONFIG_FILENAME" not in source


def test_script_gen_no_mechanism_flag() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="singlepoint",
        input={"source": "CCO", "source_type": "smiles"},
        method={},
        resources={},
    )
    argv = build_remote_cli_command(spec)
    assert "--mechanism-config" not in argv


def test_script_gen_no_role_materialization() -> None:
    from acp.scheduler.remote import script_gen

    assert not hasattr(script_gen, "_mechanism_role_source")
    assert not hasattr(script_gen, "materialized_role_paths")


def test_script_gen_no_mechanism_branch() -> None:
    import pytest
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(workflow="mechanism", input={"source": "CCO"}, method={}, resources={})
    with pytest.raises(ValueError, match="No remote subprocess mapping"):
        build_remote_cli_command(spec)


def test_script_gen_no_stage_artifact_names() -> None:
    import inspect
    from acp.scheduler.remote import script_gen

    source = inspect.getsource(script_gen)
    assert "s2_path_manifest.json" not in source
    assert "s3_lowconfirm_manifest.json" not in source


def test_remote_runner_layout_via_compat() -> None:
    import inspect
    from acp.scheduler.remote import runner as remote_runner

    source = inspect.getsource(remote_runner)
    assert "find_reaction_json" not in source
    assert "find_study_layout" not in source


# ── ⑧ remote tail mapping tests ────────────────────────────────────────


def test_script_gen_remote_tail_scan() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="scan",
        input={"source": "CCO", "source_type": "smiles", "coordinate": "0,1,1.0,3.0"},
        method={"levels": {"scan": {"functional": "r2SCAN-3c"}}, "scan_coordinates": "0,1,1.0,3.0"},
        resources={"nproc": 4},
    )
    argv = build_remote_cli_command(spec, input_path="input.xyz")
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "scan"]
    assert "--nproc" in argv
    assert "4" in argv


def test_script_gen_remote_tail_irc() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="irc",
        input={"source": "CCO", "source_type": "smiles", "input_role": "transition_state"},
        method={"method": "r2SCAN-3c"},
        resources={},
    )
    argv = build_remote_cli_command(spec, input_path="input.xyz")
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "irc"]
    assert "--input-role" in argv
    assert "--method" in argv


def test_script_gen_remote_tail_batchoptimize() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="BatchOptimize",
        input={"from_artifact": "/tmp/test.json"},
        method={"profile": "opt_freq_sp_thermo"},
        resources={},
    )
    argv = build_remote_cli_command(spec)
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "BatchOptimize"]
    assert "--profile" in argv
    assert "opt_freq_sp_thermo" in argv
    assert "--from-artifact" in argv


def test_script_gen_remote_tail_pessearch() -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command

    spec = JobSpec(
        workflow="PESsearch",
        input={"from": "/tmp/manifest.json"},
        method={"strategy": "direct"},
        resources={},
    )
    argv = build_remote_cli_command(spec)
    assert argv[:5] == ["python", "-m", "acp.cli", "run", "PESsearch"]
    assert "--strategy" in argv
