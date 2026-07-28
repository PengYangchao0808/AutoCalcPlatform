"""Tests for the ACP mechanism workflow."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from acp.core.models import Structure
from acp.scheduler.jobs import JobSpec, SUPPORTED_WORKFLOWS
from acp.scheduler.runner import JobRunner
from acp.scheduler.stage_tasks import get_stage_plan
import acp.workflows.mechanism as mechanism_workflow
from cccp.config import _get_default_config


def test_get_mechanism_stages_returns_7_stages() -> None:
    stages = mechanism_workflow.get_mechanism_stages()

    assert [stage.name for stage in stages] == [
        "reactant_optimize",
        "product_optimize",
        "ts_guess",
        "ts_optimize",
        "irc_forward",
        "irc_reverse",
        "energy_analysis",
    ]


def test_mechanism_stage_plan_provider() -> None:
    plan = get_stage_plan(JobSpec(workflow="mechanism"))

    assert [stage.stage_name for stage in plan] == [
        "reactant_optimize",
        "product_optimize",
        "ts_guess",
        "ts_optimize",
        "irc_forward",
        "irc_reverse",
        "energy_analysis",
    ]


def test_mechanism_in_supported_workflows() -> None:
    assert "mechanism" in SUPPORTED_WORKFLOWS


def test_mechanism_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "mechanism", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--input" in result.stdout
    assert "--output" in result.stdout
    assert "placeholder" not in result.stdout.lower()


def test_mechanism_runner_cmd(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    spec = JobSpec(
        workflow="mechanism",
        name="rxn",
        input={"source": "CCO", "charge": 1, "multiplicity": 2},
        resources={"nproc": 8, "mem": "16GB"},
        config_path="mechanism.yaml",
    )

    cmd = runner._build_cmd(spec, tmp_path)

    assert cmd == [
        "python",
        "-m",
        "acp.cli",
        "run",
        "mechanism",
        "--input",
        "CCO",
        "--output",
        str(tmp_path),
        "--name",
        "rxn",
        "--config",
        "mechanism.yaml",
        "--nproc",
        "8",
        "--mem",
        "16GB",
        "--charge",
        "1",
        "--multiplicity",
        "2",
    ]


def test_run_mechanism_analysis_creates_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mechanism_workflow,
        "load_config",
        lambda *args, **kwargs: _get_default_config(),
    )
    monkeypatch.setattr(
        mechanism_workflow.StructureReader,
        "read",
        lambda self, source, charge=None, multiplicity=None: Structure(
            id="mechanism_input",
            charge=charge or 0,
            multiplicity=multiplicity or 1,
            symbols=["C", "H", "H", "H", "H"],
            coordinates=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [0.6, 0.6, 0.6],
                    [-0.6, -0.6, 0.6],
                    [-0.6, 0.6, -0.6],
                    [0.6, -0.6, -0.6],
                ]
            ),
            metadata={"smiles": "C"},
        ),
    )

    result = mechanism_workflow.run_mechanism_analysis(
        "C",
        output_dir=tmp_path,
        name="methane",
    )

    assert result.status == "completed"
    assert result.ensemble is not None
    assert result.stages_completed == [
        "reactant_optimize",
        "product_optimize",
        "ts_guess",
        "ts_optimize",
        "irc_forward",
        "irc_reverse",
        "energy_analysis",
    ]
    assert result.metadata["molecule_name"] == "methane"
    assert result.metadata["n_structures"] == 4
    assert result.metadata["energy_profile"]["reactant_id"] == "methane"

    state_path = tmp_path / "methane" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert list(state["stages"].keys()) == result.stages_completed
    assert state["stages"]["energy_analysis"]["result"]["profile"]["product_id"] == "methane_product"
