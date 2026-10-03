# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
"""XtbPathSearch scheduler glue tests (PES2TS → ACP work unit X1′-C2).

Covers the scheduler-side wiring only: argv builder (``--path-config`` form),
job submission acceptance, stage-plan registration, and scheduler markers —
not the workflow engine itself (``tests/test_acp_workflows_xtb_path.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.scheduler.jobs import (
    PATH_CONFIG_FILENAME,
    SCAN_CONFIG_FILENAME,
    SUPPORTED_WORKFLOWS,
    JobSpec,
    JobStatus,
)
from acp.scheduler.manager import JobManager
from acp.scheduler.runner import JobRunner, materialize_job_input
from acp.scheduler.stage_tasks import PlanCompiler, get_stage_plan
from acp.workflows.simple import _SCHEDULER_MARKERS
from acp.workflows.xtb_path import XTB_PATH_STAGES

_WORKFLOW_ID = "XtbPathSearch"


def _frozen_request() -> dict:
    return {
        "schema_version": "pes2ts_xtb_path_request_v1",
        "reaction_id": "rxn_demo",
        "source": {
            "source_type": "xyz_text_pair",
            "start_xyz": "2\nstart\nC 0.0 0.0 0.0\nH 1.0 0.0 0.0\n",
            "end_xyz": "2\nend\nC 0.0 0.0 0.0\nH 1.6 0.0 0.0\n",
            "charge": 0,
            "multiplicity": 1,
        },
        "recipe": {
            "path_inp_text": "$path\n 1 10\nend\n",
            "gfn_level": 2,
            "uhf": 0,
            "threads": 4,
            "timeout_seconds": 300,
            "seed": 7,
            "extra_args": ["--alpb", "water"],
        },
    }


def _spec(**overrides) -> JobSpec:
    payload: dict = {
        "workflow": _WORKFLOW_ID,
        "name": "path_test",
        "input": {"path_request": _frozen_request()},
        "resources": {"nproc": 4, "mem": "8GB"},
    }
    payload.update(overrides)
    return JobSpec(**payload)


# ---------------------------------------------------------------------------
# Argv builder
# ---------------------------------------------------------------------------


def test_xtb_path_argv_builder_writes_path_config_and_exact_argv(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    request = _frozen_request()
    work_dir = tmp_path / "mol_XtbPathSearch"
    spec = _spec(input={"path_request": request})

    cmd = runner._build_cmd(spec, work_dir)

    assert cmd[:5] == ["python", "-m", "acp.cli", "run", _WORKFLOW_ID]
    path_config_arg = cmd[cmd.index("--path-config") + 1]
    assert path_config_arg == (work_dir / PATH_CONFIG_FILENAME).as_posix()
    assert cmd[cmd.index("--output") + 1] == work_dir.as_posix()
    assert cmd[cmd.index("--nproc") + 1] == "4"
    assert cmd[cmd.index("--mem") + 1] == "8GB"
    assert "--config" not in cmd

    written = json.loads((work_dir / PATH_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert written == request


def test_xtb_path_argv_builder_includes_config_path(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = _spec(config_path="/etc/acp.yaml")
    cmd = runner._build_cmd(spec, tmp_path / "t")
    assert cmd[cmd.index("--config") + 1] == "/etc/acp.yaml"


def test_xtb_path_argv_builder_rejects_missing_request(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = _spec(input={})
    with pytest.raises(ValueError, match="path_request"):
        runner._build_cmd(spec, tmp_path / "t")


def test_xtb_path_builder_mirrors_scan_config_filename_convention() -> None:
    assert PATH_CONFIG_FILENAME == "path_config.json"
    assert SCAN_CONFIG_FILENAME == "scan_config.json"
    assert PATH_CONFIG_FILENAME != SCAN_CONFIG_FILENAME


def test_path_request_payload_skips_input_materialization(tmp_path: Path) -> None:
    spec = _spec()
    assert materialize_job_input(spec.input, tmp_path / "inputs", tmp_path) is None


def test_supported_workflows_includes_xtb_path_search() -> None:
    assert _WORKFLOW_ID in SUPPORTED_WORKFLOWS


# ---------------------------------------------------------------------------
# Stage plan
# ---------------------------------------------------------------------------


def test_stage_plan_matches_method_schema_stages() -> None:
    plan = get_stage_plan(JobSpec(workflow=_WORKFLOW_ID))
    assert [stage.stage_name for stage in plan] == list(XTB_PATH_STAGES)
    assert [stage.stage_name for stage in plan] == [
        "prepare",
        "run_path_search",
        "finalize",
    ]


def test_plancompiler_compiles_xtb_path_search() -> None:
    plan = PlanCompiler.compile(JobSpec(workflow=_WORKFLOW_ID))
    assert [stage.stage_name for stage in plan] == [
        "prepare",
        "run_path_search",
        "finalize",
    ]


# ---------------------------------------------------------------------------
# Scheduler markers (ANTI-PATTERN #11)
# ---------------------------------------------------------------------------


def test_scheduler_markers_cover_path_config_filename() -> None:
    assert PATH_CONFIG_FILENAME in _SCHEDULER_MARKERS


def test_resolve_output_dir_keeps_scheduler_dir_when_path_config_present(tmp_path: Path) -> None:
    from acp.workflows.simple import _resolve_output_dir

    work_dir = tmp_path / "task"
    work_dir.mkdir()
    for name in ("job.json", "task.json", "events.jsonl", PATH_CONFIG_FILENAME):
        (work_dir / name).write_text("{}", encoding="utf-8")
    resolved = _resolve_output_dir(work_dir)
    assert resolved == work_dir.resolve()
    assert not (work_dir.parent / f"{work_dir.name}_1").exists()


# ---------------------------------------------------------------------------
# Job submission acceptance
# ---------------------------------------------------------------------------


def test_job_manager_accepts_xtb_path_search_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    monkeypatch.setattr(mgr, "_start_submission_thread", lambda job_id, thread_name: True)
    try:
        record = mgr.submit(_spec())
        assert record.status == JobStatus.QUEUED
        assert record.spec.workflow == _WORKFLOW_ID
        request = record.spec.input["path_request"]
        assert request["schema_version"] == "pes2ts_xtb_path_request_v1"
        work_dir = Path(record.work_dir)
        assert work_dir.is_dir()
        assert (work_dir / "job.json").is_file()
        plan = get_stage_plan(record.spec)
        assert [stage.stage_name for stage in plan] == list(XTB_PATH_STAGES)
    finally:
        mgr.shutdown()


__all__ = [
    "test_job_manager_accepts_xtb_path_search_submission",
    "test_path_request_payload_skips_input_materialization",
    "test_plancompiler_compiles_xtb_path_search",
    "test_resolve_output_dir_keeps_scheduler_dir_when_path_config_present",
    "test_scheduler_markers_cover_path_config_filename",
    "test_stage_plan_matches_method_schema_stages",
    "test_supported_workflows_includes_xtb_path_search",
    "test_xtb_path_argv_builder_includes_config_path",
    "test_xtb_path_argv_builder_rejects_missing_request",
    "test_xtb_path_argv_builder_writes_path_config_and_exact_argv",
    "test_xtb_path_builder_mirrors_scan_config_filename_convention",
]
