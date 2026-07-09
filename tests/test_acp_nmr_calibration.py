# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""Tests for ACP NMR calibration and averaging helpers."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr import (
    NMRAtomShielding,
    NMRAtomShift,
    NMRConformerResult,
    assign_nucleus,
    average_atom_results,
    calibrate_shifts,
    select_conformers,
)


def _record(record_id: str, energy_hartree: float) -> StructureRecord:
    return StructureRecord(
        structure=Structure(
            id=record_id,
            symbols=["C"],
            coordinates=np.array([[0.0, 0.0, 0.0]]),
        ),
        energy_hartree=energy_hartree,
    )


def _boltzmann_weights(energies: list[float], temperature: float = 298.15) -> list[float]:
    gas_constant_hartree = 8.314462618 / 2625500.0
    raw_weights = [
        math.exp(-(energy - min(energies)) / (gas_constant_hartree * temperature))
        for energy in energies
    ]
    total = sum(raw_weights)
    return [weight / total for weight in raw_weights]


def _methane_shieldings(carbon_ppm: float, hydrogen_ppm: float) -> list[NMRAtomShielding]:
    return [
        NMRAtomShielding(atom_index=1, symbol="C", isotropic_ppm=carbon_ppm, anisotropy_ppm=0.1234),
        *[
            NMRAtomShielding(
                atom_index=index,
                symbol="H",
                isotropic_ppm=hydrogen_ppm,
                anisotropy_ppm=0.0450,
            )
            for index in range(2, 6)
        ],
    ]


def _methane_shifts(carbon_ppm: float, hydrogen_ppm: float) -> list[NMRAtomShift]:
    return [
        NMRAtomShift(
            atom_index=1,
            symbol="C",
            nucleus="13C",
            shielding_ppm=carbon_ppm,
            reference_ppm=186.10,
            shift_ppm=186.10 - carbon_ppm,
            anisotropy_ppm=0.1234,
        ),
        *[
            NMRAtomShift(
                atom_index=index,
                symbol="H",
                nucleus="1H",
                shielding_ppm=hydrogen_ppm,
                reference_ppm=31.88,
                shift_ppm=31.88 - hydrogen_ppm,
                anisotropy_ppm=0.0450,
            )
            for index in range(2, 6)
        ],
    ]


def test_select_conformers_applies_energy_window_and_limit() -> None:
    ensemble = StructureEnsemble(
        records=[
            _record("conf_000", -10.0000),
            _record("conf_001", -9.9980),
            _record("conf_002", -9.9960),
            _record("conf_003", -9.9900),
        ]
    )

    selected = select_conformers(ensemble, energy_window_kcal=3.0, max_conformers=2)

    assert [record.id for record in selected] == ["conf_000", "conf_001"]


@pytest.mark.parametrize(
    ("symbol", "expected_nucleus"),
    [("H", "1H"), ("c", "13C"), ("Cl", "35Cl"), ("Xx", None)],
)
def test_assign_nucleus_maps_supported_symbols(
    symbol: str,
    expected_nucleus: str | None,
) -> None:
    assert assign_nucleus(symbol) == expected_nucleus


def test_calibrate_shifts_uses_reference_minus_shielding_and_handles_null_reference() -> None:
    shieldings = [
        NMRAtomShielding(atom_index=1, symbol="C", isotropic_ppm=180.0, anisotropy_ppm=9.0),
        NMRAtomShielding(atom_index=2, symbol="H", isotropic_ppm=30.0, anisotropy_ppm=2.0),
    ]

    shifts = calibrate_shifts(shieldings, references={"13C": 190.0, "1H": None})

    assert shifts[0] == NMRAtomShift(
        atom_index=1,
        symbol="C",
        nucleus="13C",
        shielding_ppm=180.0,
        reference_ppm=190.0,
        shift_ppm=10.0,
        anisotropy_ppm=9.0,
    )
    assert shifts[1].nucleus == "1H"
    assert shifts[1].reference_ppm is None
    assert shifts[1].shift_ppm is None


def test_calibrate_shifts_methane_uses_default_hydrogen_and_carbon_references() -> None:
    shieldings = _methane_shieldings(carbon_ppm=192.6845, hydrogen_ppm=31.6551)

    shifts = calibrate_shifts(shieldings, references={"13C": 186.10, "1H": 31.88})

    assert len(shifts) == 5
    assert shifts[0].nucleus == "13C"
    assert shifts[0].shift_ppm == pytest.approx(186.10 - 192.6845)
    for shift in shifts[1:]:
        assert shift.nucleus == "1H"
        assert shift.reference_ppm == pytest.approx(31.88)
        assert shift.shift_ppm == pytest.approx(31.88 - 31.6551)


def test_average_atom_results_uses_boltzmann_weights_from_free_energy() -> None:
    conformer_results = [
        NMRConformerResult(
            record_id="conf_000",
            energy_hartree=-100.0,
            free_energy_hartree=-100.1005,
            weight=None,
            log_file=Path("conf_000.log"),
            shifts=[
                NMRAtomShift(
                    atom_index=1,
                    symbol="C",
                    nucleus="13C",
                    shielding_ppm=180.0,
                    reference_ppm=190.0,
                    shift_ppm=10.0,
                )
            ],
        ),
        NMRConformerResult(
            record_id="conf_001",
            energy_hartree=-100.0,
            free_energy_hartree=-100.1000,
            weight=None,
            log_file=Path("conf_001.log"),
            shifts=[
                NMRAtomShift(
                    atom_index=1,
                    symbol="C",
                    nucleus="13C",
                    shielding_ppm=170.0,
                    reference_ppm=190.0,
                    shift_ppm=20.0,
                )
            ],
        ),
    ]

    averaged = average_atom_results(conformer_results, temperature=298.15)

    gas_constant_hartree = 8.314462618 / 2625500.0
    weight_0 = math.exp(-((-100.1005) - (-100.1005)) / (gas_constant_hartree * 298.15))
    weight_1 = math.exp(-((-100.1000) - (-100.1005)) / (gas_constant_hartree * 298.15))
    normalized_weight_0 = weight_0 / (weight_0 + weight_1)
    normalized_weight_1 = weight_1 / (weight_0 + weight_1)
    expected_shielding = 180.0 * normalized_weight_0 + 170.0 * normalized_weight_1

    assert len(averaged) == 1
    assert averaged[0].atom_index == 1
    assert averaged[0].nucleus == "13C"
    assert averaged[0].averaged_shielding_ppm == pytest.approx(expected_shielding)
    assert averaged[0].reference_ppm == pytest.approx(190.0)
    assert averaged[0].averaged_shift_ppm == pytest.approx(190.0 - expected_shielding)


def test_average_atom_results_methane_uses_free_energies_for_all_atoms() -> None:
    conformer_results = [
        NMRConformerResult(
            record_id="conf_000",
            energy_hartree=-40.5000,
            free_energy_hartree=-40.6000,
            weight=None,
            log_file=Path("conf_000.log"),
            shifts=_methane_shifts(carbon_ppm=193.20, hydrogen_ppm=31.70),
        ),
        NMRConformerResult(
            record_id="conf_001",
            energy_hartree=-40.4999,
            free_energy_hartree=-40.5988,
            weight=None,
            log_file=Path("conf_001.log"),
            shifts=_methane_shifts(carbon_ppm=192.20, hydrogen_ppm=31.45),
        ),
        NMRConformerResult(
            record_id="conf_002",
            energy_hartree=-40.4998,
            free_energy_hartree=-40.5976,
            weight=None,
            log_file=Path("conf_002.log"),
            shifts=_methane_shifts(carbon_ppm=191.20, hydrogen_ppm=31.20),
        ),
    ]

    averaged = average_atom_results(conformer_results, temperature=298.15)

    free_energy_weights = _boltzmann_weights([-40.6000, -40.5988, -40.5976])
    electronic_energy_weights = _boltzmann_weights([-40.5000, -40.4999, -40.4998])
    expected_carbon_shielding = sum(
        shielding * weight
        for shielding, weight in zip([193.20, 192.20, 191.20], free_energy_weights, strict=True)
    )
    expected_hydrogen_shielding = sum(
        shielding * weight
        for shielding, weight in zip([31.70, 31.45, 31.20], free_energy_weights, strict=True)
    )

    assert sum(free_energy_weights) == pytest.approx(1.0)
    assert abs(free_energy_weights[0] - electronic_energy_weights[0]) > 0.3
    assert len(averaged) == 5
    carbon_result = next(atom for atom in averaged if atom.nucleus == "13C")
    hydrogen_results = [atom for atom in averaged if atom.nucleus == "1H"]
    assert carbon_result.averaged_shielding_ppm == pytest.approx(expected_carbon_shielding)
    assert carbon_result.averaged_shift_ppm == pytest.approx(186.10 - expected_carbon_shielding)
    assert len(hydrogen_results) == 4
    for hydrogen_result in hydrogen_results:
        assert hydrogen_result.averaged_shielding_ppm == pytest.approx(expected_hydrogen_shielding)
        assert hydrogen_result.averaged_shift_ppm == pytest.approx(31.88 - expected_hydrogen_shielding)
