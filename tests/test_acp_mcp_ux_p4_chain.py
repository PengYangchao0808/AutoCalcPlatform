"""P4: the CENSO execution facts survive task → adapter → report."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cccp.calculation.energy_adapters import censo_refine_via_task
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult

_EXECUTION = {
    "argv": ["censo", "-i", "crest_conformers.xyz"],
    "returncode": 0,
    "final_part": "screening",
    "temperature_k": 298.15,
    "artifacts": {
        "json": {"path": "1_SCREENING.json", "sha256": "a" * 64},
        "xyz": {"path": "1_SCREENING.xyz", "sha256": "b" * 64},
    },
}


def _task_result() -> TaskResult:
    return TaskResult(
        task=TaskKind.CENSO_REFINE,
        metadata={
            "preset": "censo-light",
            "final_part": "screening",
            "temperature_k": 298.15,
            "execution": dict(_EXECUTION),
        },
    )


def test_adapter_forwards_execution_facts(tmp_path: Path) -> None:
    run_dir = tmp_path / "censo"
    run_dir.mkdir()
    (run_dir / "1_SCREENING.json").write_text("{}", encoding="utf-8")
    (run_dir / "1_SCREENING.xyz").write_text("1\n\nH 0 0 0\n", encoding="utf-8")
    record = SimpleNamespace(conf_id="CONF1", gtot=-1.0)
    with (
        patch(
            "cccp.calculation.energy_adapters.run_censo_refine",
            return_value=_task_result(),
        ),
        patch(
            "cccp.calculation.energy_adapters.CensoInterface.parse_censo_json",
            return_value=[record],
        ),
    ):
        result = censo_refine_via_task(
            {},
            tmp_path / "ensemble.xyz",
            run_dir,
            preset="censo-light",
            charge=0,
            multiplicity=1,
        )
    assert result.execution == _EXECUTION
    assert result.final_part == "screening"
