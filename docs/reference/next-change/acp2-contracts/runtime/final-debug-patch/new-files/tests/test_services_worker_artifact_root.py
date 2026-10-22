"""A worker launched elsewhere must never send scientific outputs to its CWD."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from acp_services.runtime.worker import CccpTaskExecutor, ExecutionScope, FrozenPlan
from cccp.calculation.requests import StructureInput, TaskRequest
from cccp.calculation.results import TaskResult
from tests.test_services_chain_switch import _envelope


@pytest.mark.parametrize("output", [None, ".", "WORK/03_OPT"])
def test_workers_separate_artifacts_without_changing_frozen_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str | None
) -> None:
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)

    def scientific_runner(request, *, context):
        destination = request.output_dir or Path.cwd()
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "scientific-output").write_text(str(context.workdir))
        return TaskResult(task=request.task, status="completed", complete=True, energy_hartree=-1.0)

    payload = TaskRequest(
        task="singlepoint",
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("He",)),
    ).to_dict()
    if output is not None:
        payload["output_dir"] = output
    frozen = dict(payload)
    with patch("cccp.calculation.run_singlepoint", scientific_runner):
        for name in ("first", "second"):
            work = tmp_path / name
            scope = ExecutionScope(_envelope(), FrozenPlan(payload=payload), work)
            assert CccpTaskExecutor().execute(scope).state == "completed"
            target = work / (output or ".") / "scientific-output"
            assert target.read_text() == str(work)
    assert not list(launch.iterdir())
    assert payload == frozen
