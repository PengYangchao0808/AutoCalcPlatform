# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedCallResult=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false, reportUnknownMemberType=false
"""Methane end-to-end tests for the ACP NMR workflow."""

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

_METHANE_REFERENCES = {"1H": 31.88, "13C": 186.10}
_METHANE_SHIELDINGS = {
    "conf_000": (193.20, 31.70),
    "conf_001": (192.20, 31.45),
    "conf_002": (191.20, 31.20),
}
_ELECTRONIC_ENERGIES = {
    "conf_000": -40.5000,
    "conf_001": -40.4999,
    "conf_002": -40.4998,
}
_FREE_ENERGIES = {
    "conf_000": -40.6000,
    "conf_001": -40.5988,
    "conf_002": -40.5976,
}
_EXPECTED_STAGES = [
    "select_conformers",
    "run_nmr_giao",
    "parse_shieldings",
    "calibrate_shifts",
    "average_shifts",
    "write_report",
]


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
            "references": dict(_METHANE_REFERENCES),
        },
    }


def _boltzmann_weights(energies: list[float], temperature: float = 298.15) -> list[float]:
    gas_constant_hartree = 8.314462618 / 2625500.0
    raw_weights = [
        math.exp(-(energy - min(energies)) / (gas_constant_hartree * temperature))
        for energy in energies
    ]
    total = sum(raw_weights)
    return [weight / total for weight in raw_weights]


def _methane_coordinates(variant: int) -> NDArray[np.float64]:
    bond_component = 1.089 / math.sqrt(3.0)
    coordinates = np.array(
        [
            [0.0, 0.0, 0.0],
            [bond_component, bond_component, bond_component],
            [-bond_component, -bond_component, bond_component],
            [-bond_component, bond_component, -bond_component],
            [bond_component, -bond_component, -bond_component],
        ],
        dtype=float,
    )
    if variant == 1:
        return np.asarray(
            coordinates
            + np.array(
            [
                [0.0, 0.0, 0.0],
                [0.015, -0.010, 0.006],
                [0.000, 0.009, -0.012],
                [-0.011, 0.004, 0.008],
                [-0.008, 0.012, -0.004],
            ],
            dtype=float,
            ),
            dtype=np.float64,
        )
    if variant == 2:
        return np.asarray(
            coordinates
            + np.array(
            [
                [0.0, 0.0, 0.0],
                [-0.012, 0.008, -0.009],
                [0.013, 0.010, -0.014],
                [-0.006, -0.011, 0.010],
                [0.009, -0.007, 0.012],
            ],
            dtype=float,
            ),
            dtype=np.float64,
        )
    return np.asarray(coordinates, dtype=np.float64)


def _make_ensemble() -> StructureEnsemble:
    return StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="conf_000",
                    symbols=["C", "H", "H", "H", "H"],
                    coordinates=_methane_coordinates(0),
                ),
                energy_hartree=_ELECTRONIC_ENERGIES["conf_000"],
                free_energy_hartree=_FREE_ENERGIES["conf_000"],
            ),
            StructureRecord(
                structure=Structure(
                    id="conf_001",
                    symbols=["C", "H", "H", "H", "H"],
                    coordinates=_methane_coordinates(1),
                ),
                energy_hartree=_ELECTRONIC_ENERGIES["conf_001"],
                free_energy_hartree=_FREE_ENERGIES["conf_001"],
            ),
            StructureRecord(
                structure=Structure(
                    id="conf_002",
                    symbols=["C", "H", "H", "H", "H"],
                    coordinates=_methane_coordinates(2),
                ),
                energy_hartree=_ELECTRONIC_ENERGIES["conf_002"],
                free_energy_hartree=_FREE_ENERGIES["conf_002"],
            ),
        ],
        metadata={"molecule_name": "methane"},
    )


def _orca_nucleus_block(
    atom_index: int,
    symbol: str,
    isotropic_ppm: float,
    anisotropy_ppm: float,
    spread: float,
) -> list[str]:
    xx = isotropic_ppm + spread
    yy = isotropic_ppm
    zz = isotropic_ppm - spread
    return [
        f"Nucleus   {atom_index:<1}{symbol}:   isotropic = {isotropic_ppm:8.4f}   anisotropy = {anisotropy_ppm:8.4f}",
        f"  XX= {xx:8.4f}  XY= 0.0000  XZ= 0.0000",
        f"  YX= 0.0000  YY= {yy:8.4f}  YZ= 0.0000",
        f"  ZX= 0.0000  ZY= 0.0000  ZZ= {zz:8.4f}",
        "",
    ]


def _write_fake_orca_log(path: Path, carbon_ppm: float, hydrogen_ppm: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "NMR SHIELDING TENSOR",
        "",
        *_orca_nucleus_block(1, "C", carbon_ppm, 0.1234, 0.1200),
    ]
    for atom_index in range(2, 6):
        lines.extend(_orca_nucleus_block(atom_index, "H", hydrogen_ppm, 0.0450, 0.0300))
    lines.append("Normal termination of ORCA.")
    _ = path.write_text("\n".join(lines), encoding="utf-8")


def test_run_nmr_calculation_completes_methane_pipeline_with_mock_backends(
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
            carbon_ppm, hydrogen_ppm = _METHANE_SHIELDINGS[target_dir.name]
            _write_fake_orca_log(log_file, carbon_ppm, hydrogen_ppm)
            _ = input_file.write_text("! B3LYP def2-TZVPP NMR\n", encoding="utf-8")
            self.calls.append(
                {
                    "coordinates": coordinates,
                    "symbols": symbols,
                    "charge": charge,
                    "multiplicity": multiplicity,
                    "output_dir": target_dir,
                    "kwargs": kwargs,
                }
            )
            return QCResult(success=True, energy=-40.0, output_file=input_file, log_file=log_file)

    def _fake_get_backend(name: str):
        backend_requests.append(name)
        return _FakeOrcaBackend

    monkeypatch.setattr(nmr_workflow, "run_conformer_search", _fake_run_conformer_search)
    monkeypatch.setattr(nmr_workflow, "get_backend", _fake_get_backend)

    result = nmr_workflow.run_nmr_calculation(
        "C",
        output_dir=tmp_path,
        conformer_protocol="ext",
        config=config,
        name="methane",
        backend_name="orca",
        references=dict(_METHANE_REFERENCES),
    )

    assert result.status == "completed"
    assert result.ensemble is not None
    assert result.stages_completed == _EXPECTED_STAGES
    assert backend_requests == ["orca"]
    assert conformer_calls == {
        "input_source": "C",
        "output_dir": tmp_path / "conformer",
        "protocol": "ext",
        "name": "methane",
        "charge": None,
        "multiplicity": None,
    }

    report_path = Path(result.metadata["nmr_report"])
    xlsx_path = Path(result.metadata["nmr_report_xlsx"])
    assert report_path.exists()
    assert xlsx_path.exists()
    assert result.ensemble.metadata["nmr_report"] == str(report_path)
    assert result.ensemble.metadata["nmr_report_xlsx"] == str(xlsx_path)
    assert result.metadata["selected_conformers"] == 3

    conformer_ids = ["conf_000", "conf_001", "conf_002"]
    free_energy_weights = _boltzmann_weights([_FREE_ENERGIES[record_id] for record_id in conformer_ids])
    electronic_energy_weights = _boltzmann_weights(
        [_ELECTRONIC_ENERGIES[record_id] for record_id in conformer_ids]
    )
    expected_carbon_shielding = sum(
        _METHANE_SHIELDINGS[record_id][0] * weight
        for record_id, weight in zip(conformer_ids, free_energy_weights, strict=True)
    )
    expected_hydrogen_shielding = sum(
        _METHANE_SHIELDINGS[record_id][1] * weight
        for record_id, weight in zip(conformer_ids, free_energy_weights, strict=True)
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["molecule_name"] == "methane"
    assert payload["backend"] == "orca"
    assert payload["method"] == "B3LYP"
    assert payload["basis"] == "def2-TZVPP"
    assert len(payload["conformers"]) == 3
    assert [conformer["weight"] for conformer in payload["conformers"]] == pytest.approx(
        free_energy_weights
    )
    assert sum(conformer["weight"] for conformer in payload["conformers"]) == pytest.approx(1.0)
    assert abs(payload["conformers"][0]["weight"] - electronic_energy_weights[0]) > 0.3

    carbon_result = next(atom for atom in payload["averaged_atoms"] if atom["nucleus"] == "13C")
    hydrogen_results = [atom for atom in payload["averaged_atoms"] if atom["nucleus"] == "1H"]
    assert carbon_result["averaged_shielding_ppm"] == pytest.approx(expected_carbon_shielding)
    assert carbon_result["averaged_shift_ppm"] == pytest.approx(
        _METHANE_REFERENCES["13C"] - expected_carbon_shielding
    )
    assert len(hydrogen_results) == 4
    for hydrogen_result in hydrogen_results:
        assert hydrogen_result["averaged_shielding_ppm"] == pytest.approx(expected_hydrogen_shielding)
        assert hydrogen_result["averaged_shift_ppm"] == pytest.approx(
            _METHANE_REFERENCES["1H"] - expected_hydrogen_shielding
        )

    for record, expected_weight in zip(result.ensemble.records, free_energy_weights, strict=True):
        conformer_result = record.properties["nmr"]
        assert isinstance(conformer_result, NMRConformerResult)
        assert conformer_result.weight == pytest.approx(expected_weight)
        assert len(conformer_result.shieldings) == 5
        assert len(conformer_result.shifts) == 5
        assert record.files["nmr_log"].exists()
        assert record.files["nmr_input"].exists()
        carbon_ppm, hydrogen_ppm = _METHANE_SHIELDINGS[record.id]
        assert conformer_result.shifts[0].shift_ppm == pytest.approx(_METHANE_REFERENCES["13C"] - carbon_ppm)
        for hydrogen_shift in conformer_result.shifts[1:]:
            assert hydrogen_shift.nucleus == "1H"
            assert hydrogen_shift.shift_ppm == pytest.approx(_METHANE_REFERENCES["1H"] - hydrogen_ppm)

    assert len(backend_instances) == 1
    assert len(backend_instances[0].calls) == 3
    assert backend_instances[0].calls[0]["symbols"] == ["C", "H", "H", "H", "H"]
    assert backend_instances[0].calls[0]["kwargs"]["method"] == "B3LYP"
    assert backend_instances[0].calls[0]["kwargs"]["basis"] == "def2-TZVPP"
    assert backend_instances[0].calls[0]["kwargs"]["solvent"] == "chloroform"
    assert backend_instances[0].calls[0]["kwargs"]["solvent_model"] == "smd"
