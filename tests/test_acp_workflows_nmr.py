# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedCallResult=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false, reportUnknownMemberType=false
"""Tests for the ACP NMR workflow."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

import acp.workflows.nmr as nmr_workflow
from acp.backends.base import QCResult
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.workflow import WorkflowResult
from acp.nmr.models import NMRConformerResult


def _make_config() -> dict[str, Any]:
    return {
        "resources": {"nproc": 1, "mem": "1GB"},
        "theory": {
            "nmr": {
                "engine": "orca",
                "method": "B3LYP",
                "basis": "def2-TZVPP",
                "solvent": "chloroform",
                "solvent_model": "smd",
            }
        },
        "nmr": {
            "temperature_k": 298.15,
            "energy_window_kcal": 3.0,
            "max_conformers": 10,
            "references": {
                "1H": 32.0,
                "13C": 190.0,
            },
        },
    }


def _make_ensemble() -> StructureEnsemble:
    return StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="conf_000",
                    symbols=["C", "H"],
                    coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
                ),
                energy_hartree=-100.0005,
                free_energy_hartree=-100.1005,
            ),
            StructureRecord(
                structure=Structure(
                    id="conf_001",
                    symbols=["C", "H"],
                    coordinates=np.array([[0.1, 0.0, 0.0], [0.0, 0.1, 1.0]]),
                ),
                energy_hartree=-100.0000,
                free_energy_hartree=-100.1000,
            ),
        ]
    )


def _write_fake_orca_nmr_log(path: Path, carbon_ppm: float, hydrogen_ppm: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        "\n".join(
            [
                "* Single Point Calculation *",
                "",
                "                       NMR SHIELDING TENSOR (PPM)",
                "",
                f"  Nucleus   1C:     isotropic=   {carbon_ppm:8.4f}   anisotropy=    10.0000",
                "  XX= 155.0000   YX=  0.0000   ZX=  0.0000",
                "  XY=   0.0000   YY=145.0000   ZY=  0.0000",
                "  XZ=   0.0000   YZ=  0.0000   ZZ=150.0000",
                f"  Nucleus   2H:     isotropic=   {hydrogen_ppm:8.4f}   anisotropy=     5.0000",
                "  XX= 30.0000   YX=  0.0000   ZX=  0.0000",
                "  XY=  0.0000   YY=27.0000   ZY=  0.0000",
                "  XZ=  0.0000   YZ=  0.0000   ZZ=29.0000",
                "",
                "****ORCA-CHEMISTRY JOB DONE****",
            ]
        ),
        encoding="utf-8",
    )


def test_get_nmr_stages_returns_expected_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nmr_workflow, "load_config", lambda *args, **kwargs: _make_config())

    stages = nmr_workflow.get_nmr_stages(config=_make_config())

    assert [stage.name for stage in stages] == [
        "select_conformers",
        "run_nmr_giao",
        "parse_shieldings",
        "calibrate_shifts",
        "average_shifts",
        "write_report",
    ]


def test_run_nmr_calculation_integrates_conformer_backend_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config()
    conformer_calls: dict[str, Any] = {}
    backend_requests: list[str] = []
    backend_instances: list[_FakeOrcaBackend] = []

    monkeypatch.setattr(nmr_workflow, "load_config", lambda *args, **kwargs: config)

    def _fake_run_conformer_search(
        input_source: str,
        output_dir: str | Path = "./conformer_output",
        protocol: str = "ext",
        config: dict[str, Any] | None = None,
        name: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
    ) -> WorkflowResult:
        conformer_calls.update(
            {
                "input_source": input_source,
                "output_dir": Path(output_dir),
                "protocol": protocol,
                "name": name,
                "charge": charge,
                "multiplicity": multiplicity,
            }
        )
        return WorkflowResult(status="completed", ensemble=_make_ensemble())

    class _FakeOrcaBackend:
        def __init__(self, backend_config: dict[str, Any]) -> None:
            self.config = backend_config
            self.calls: list[dict[str, Any]] = []
            backend_instances.append(self)

        def nmr_shielding(
            self,
            coordinates: NDArray[np.float64],
            symbols: list[str],
            charge: int = 0,
            multiplicity: int = 1,
            output_dir: Path | None = None,
            output_name: str = "nmr",
            **kwargs: Any,
        ) -> QCResult:
            target_dir = Path(output_dir or tmp_path)
            target_dir.mkdir(parents=True, exist_ok=True)
            log_file = target_dir / f"{output_name}.out"
            input_file = target_dir / f"{output_name}.inp"
            if target_dir.name == "conf_000":
                carbon_ppm, hydrogen_ppm = 180.0, 30.0
            else:
                carbon_ppm, hydrogen_ppm = 170.0, 31.0
            _write_fake_orca_nmr_log(log_file, carbon_ppm, hydrogen_ppm)
            input_file.write_text("! B3LYP def2-TZVPP NMR\n", encoding="utf-8")
            self.calls.append(
                {
                    "symbols": symbols,
                    "charge": charge,
                    "multiplicity": multiplicity,
                    "output_dir": target_dir,
                    "kwargs": kwargs,
                }
            )
            return QCResult(success=True, energy=-200.0, output_file=input_file, log_file=log_file)

    def _fake_get_backend(name: str):
        backend_requests.append(name)
        return _FakeOrcaBackend

    monkeypatch.setattr(nmr_workflow, "run_conformer_search", _fake_run_conformer_search)
    monkeypatch.setattr(nmr_workflow, "get_backend", _fake_get_backend)

    result = nmr_workflow.run_nmr_calculation(
        "CCO",
        output_dir=tmp_path,
        conformer_protocol="ext",
        config=config,
        name="ethanol",
        backend_name="orca",
        references={"1H": 32.0, "13C": 190.0},
    )

    assert result.status == "completed"
    assert result.ensemble is not None
    assert result.stages_completed == [
        "select_conformers",
        "run_nmr_giao",
        "parse_shieldings",
        "calibrate_shifts",
        "average_shifts",
        "write_report",
    ]
    assert backend_requests == ["orca"]
    assert conformer_calls == {
        "input_source": "CCO",
        "output_dir": tmp_path / "conformer",
        "protocol": "ext",
        "name": "ethanol",
        "charge": None,
        "multiplicity": None,
    }

    report_path = Path(result.metadata["nmr_report"])
    xlsx_path = Path(result.metadata["nmr_report_xlsx"])
    assert report_path.exists()
    assert xlsx_path.exists()
    assert result.ensemble.metadata["nmr_report"] == str(report_path)
    assert result.metadata["selected_conformers"] == 2

    gas_constant_hartree = 8.314462618 / 2625500.0
    weight_0 = math.exp(-((-100.1005) - (-100.1005)) / (gas_constant_hartree * 298.15))
    weight_1 = math.exp(-((-100.1000) - (-100.1005)) / (gas_constant_hartree * 298.15))
    normalized_weight_0 = weight_0 / (weight_0 + weight_1)
    normalized_weight_1 = weight_1 / (weight_0 + weight_1)
    expected_carbon_shielding = 180.0 * normalized_weight_0 + 170.0 * normalized_weight_1

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["molecule_name"] == "ethanol"
    assert payload["backend"] == "orca"
    assert len(payload["conformers"]) == 2
    carbon_result = next(atom for atom in payload["averaged_atoms"] if atom["symbol"] == "C")
    assert carbon_result["averaged_shielding_ppm"] == pytest.approx(expected_carbon_shielding)
    assert carbon_result["averaged_shift_ppm"] == pytest.approx(190.0 - expected_carbon_shielding)

    for record in result.ensemble.records:
        assert isinstance(record.properties["nmr"], NMRConformerResult)
        assert len(record.properties["nmr"].shieldings) == 2
        assert len(record.properties["nmr"].shifts) == 2

    assert len(backend_instances) == 1
    assert len(backend_instances[0].calls) == 2
    assert backend_instances[0].calls[0]["kwargs"]["method"] == "B3LYP"
    assert backend_instances[0].calls[0]["kwargs"]["basis"] == "def2-TZVPP"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
