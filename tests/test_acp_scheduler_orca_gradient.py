# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
"""OrcaGradient scheduler glue tests (work unit X4′-A).

Covers the scheduler-side wiring only: argv builder (``--gradient-config``
form), job submission acceptance, stage-plan registration, and scheduler
markers — not the workflow engine itself
(``tests/test_acp_workflows_orca_gradient.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.scheduler.jobs import (
    GRADIENT_CONFIG_FILENAME,
    PATH_CONFIG_FILENAME,
    SUPPORTED_WORKFLOWS,
    JobSpec,
    JobStatus,
)
from acp.scheduler.manager import JobManager
from acp.scheduler.runner import JobRunner, materialize_job_input
from acp.scheduler.stage_tasks import PlanCompiler, get_stage_plan
from acp.workflows.orca_gradient import ORCA_GRADIENT_STAGES
from acp.workflows.simple import _SCHEDULER_MARKERS

_WORKFLOW_ID = "OrcaGradient"


def _frozen_request() -> dict:
    return {
        "schema_version": "pes2ts_orca_gradient_request_v1",
        "xyz": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
        "method": "GFN2-xTB",
        "basis": "",
        "charge": 0,
        "multiplicity": 1,
        "route_extras": [],
        "timeout_seconds": 600,
        "nproc": 2,
    }


def _spec(**overrides) -> JobSpec:
    payload: dict = {
        "workflow": _WORKFLOW_ID,
        "name": "grad_test",
        "input": {"gradient_request": _frozen_request()},
        "resources": {"nproc": 4, "mem": "8GB"},
    }
    payload.update(overrides)
    return JobSpec(**payload)


def test_orca_gradient_argv_builder_writes_gradient_config_and_exact_argv(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    request = _frozen_request()
    work_dir = tmp_path / "mol_OrcaGradient"
    spec = _spec(input={"gradient_request": request})

    cmd = runner._build_cmd(spec, work_dir)

    assert cmd[:5] == ["python", "-m", "acp.cli", "run", _WORKFLOW_ID]
    gradient_config_arg = cmd[cmd.index("--gradient-config") + 1]
    assert gradient_config_arg == (work_dir / GRADIENT_CONFIG_FILENAME).as_posix()
    assert cmd[cmd.index("--output") + 1] == work_dir.as_posix()
    assert cmd[cmd.index("--nproc") + 1] == "4"
    assert cmd[cmd.index("--mem") + 1] == "8GB"
    assert "--config" not in cmd

    written = json.loads((work_dir / GRADIENT_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert written == request


def test_orca_gradient_argv_builder_includes_config_path(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = _spec(config_path="/etc/acp.yaml")
    cmd = runner._build_cmd(spec, tmp_path / "t")
    assert cmd[cmd.index("--config") + 1] == "/etc/acp.yaml"


def test_orca_gradient_argv_builder_rejects_missing_request(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = _spec(input={})
    with pytest.raises(ValueError, match="gradient_request"):
        runner._build_cmd(spec, tmp_path / "t")


def test_orca_gradient_builder_mirrors_config_filename_convention() -> None:
    assert GRADIENT_CONFIG_FILENAME == "gradient_config.json"
    assert PATH_CONFIG_FILENAME == "path_config.json"
    assert GRADIENT_CONFIG_FILENAME != PATH_CONFIG_FILENAME


def test_gradient_request_payload_skips_input_materialization(tmp_path: Path) -> None:
    spec = _spec()
    assert materialize_job_input(spec.input, tmp_path / "inputs", tmp_path) is None


def test_supported_workflows_includes_orca_gradient() -> None:
    assert _WORKFLOW_ID in SUPPORTED_WORKFLOWS


def test_stage_plan_matches_method_schema_stages() -> None:
    plan = get_stage_plan(JobSpec(workflow=_WORKFLOW_ID))
    assert [stage.stage_name for stage in plan] == list(ORCA_GRADIENT_STAGES)
    assert [stage.stage_name for stage in plan] == [
        "prepare",
        "run_gradient",
        "finalize",
    ]


def test_plancompiler_compiles_orca_gradient() -> None:
    plan = PlanCompiler.compile(JobSpec(workflow=_WORKFLOW_ID))
    assert [stage.stage_name for stage in plan] == [
        "prepare",
        "run_gradient",
        "finalize",
    ]


def test_scheduler_markers_cover_gradient_config_filename() -> None:
    assert GRADIENT_CONFIG_FILENAME in _SCHEDULER_MARKERS


def test_resolve_output_dir_keeps_scheduler_dir_when_gradient_config_present(
    tmp_path: Path,
) -> None:
    from acp.workflows.simple import _resolve_output_dir

    work_dir = tmp_path / "task"
    work_dir.mkdir()
    for name in ("job.json", "task.json", "events.jsonl", GRADIENT_CONFIG_FILENAME):
        (work_dir / name).write_text("{}", encoding="utf-8")
    resolved = _resolve_output_dir(work_dir)
    assert resolved == work_dir.resolve()
    assert not (work_dir.parent / f"{work_dir.name}_1").exists()


def test_job_manager_accepts_orca_gradient_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    monkeypatch.setattr(mgr, "_start_submission_thread", lambda job_id, thread_name: True)
    try:
        record = mgr.submit(_spec())
        assert record.status == JobStatus.QUEUED
        assert record.spec.workflow == _WORKFLOW_ID
        request = record.spec.input["gradient_request"]
        assert request["schema_version"] == "pes2ts_orca_gradient_request_v1"
        work_dir = Path(record.work_dir)
        assert work_dir.is_dir()
        assert (work_dir / "job.json").is_file()
        plan = get_stage_plan(record.spec)
        assert [stage.stage_name for stage in plan] == list(ORCA_GRADIENT_STAGES)
    finally:
        mgr.shutdown()


__all__ = [
    "test_gradient_request_payload_skips_input_materialization",
    "test_job_manager_accepts_orca_gradient_submission",
    "test_orca_gradient_argv_builder_includes_config_path",
    "test_orca_gradient_argv_builder_rejects_missing_request",
    "test_orca_gradient_argv_builder_writes_gradient_config_and_exact_argv",
    "test_orca_gradient_builder_mirrors_config_filename_convention",
    "test_plancompiler_compiles_orca_gradient",
    "test_resolve_output_dir_keeps_scheduler_dir_when_gradient_config_present",
    "test_scheduler_markers_cover_gradient_config_filename",
    "test_stage_plan_matches_method_schema_stages",
    "test_supported_workflows_includes_orca_gradient",
]
