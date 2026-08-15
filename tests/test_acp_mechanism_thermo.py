# pyright: reportMissingImports=false
"""Tests for mechanism thermochemistry providers."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from acp.core.models import HARTREE_TO_KCAL, Structure, StructureEnsemble, StructureRecord
from acp.mechanism.providers.thermo import (
    RPHCompositeProvider,
    ShermoProvider,
    ThermochemistryBatchItem,
    standard_state_correction_kcal,
    thermochemistry_result_to_legacy_dict,
)


def _ensemble(weight: float) -> StructureEnsemble:
    return StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="conf1",
                    symbols=["H"],
                    coordinates=[[0.0, 0.0, 0.0]],
                    metadata={},
                ),
                energy_hartree=-100.0,
                weight=weight,
            )
        ],
        metadata={"representative_weight": weight},
    )


def test_shermo_provider_passes_sp_energy_and_maps_result(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_runner(**kwargs: object) -> dict[str, float]:
        calls.append(dict(kwargs))
        return {
            "u_sum": -100.020000,
            "h_sum": -100.010000,
            "g_sum": -99.950000,
            "g_conc": -99.940000,
            "s_total": 0.012300,
        }

    freq_log = tmp_path / "freq.log"
    provider = ShermoProvider(
        {"thermo": {"path": "Shermo", "scl_zpe": 0.98, "shermo_ilowfreq": 2}},
        runner=fake_runner,
    )

    result = provider.compute(
        sp_energy=-100.123456789,
        freq_log=freq_log,
        ensemble=None,
        temperature=310.0,
        standard_state="1atm",
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["sp_energy"] == pytest.approx(-100.123456789)
    assert call["freq_output"] == freq_log
    assert call["temperature_k"] == pytest.approx(310.0)
    assert call["scl_zpe"] == pytest.approx(0.98)
    assert result.gibbs_hartree == pytest.approx(-99.94)
    assert result.enthalpy_hartree == pytest.approx(-100.01)
    assert result.entropy_au == pytest.approx(0.0123)
    assert result.corrections["selected_gibbs_source"] == "g_conc"
    assert result.corrections["qrrho"] is True

    legacy = thermochemistry_result_to_legacy_dict(result)
    assert legacy == {
        "u_sum": pytest.approx(-100.02),
        "h_sum": pytest.approx(-100.01),
        "g_sum": pytest.approx(-99.95),
        "g_conc": pytest.approx(-99.94),
        "s_total": pytest.approx(0.0123),
    }


def test_rph_composite_provider_applies_ensemble_and_standard_state(tmp_path: Path) -> None:
    def fake_runner(**_: object) -> dict[str, float | None]:
        return {
            "u_sum": -99.980000,
            "h_sum": -99.970000,
            "g_sum": -99.950000,
            "g_conc": None,
            "s_total": 0.011000,
        }

    provider = RPHCompositeProvider(runner=fake_runner)
    ensemble = _ensemble(0.25)
    result = provider.compute(
        sp_energy=-100.0,
        freq_log=tmp_path / "freq.log",
        ensemble=ensemble,
        temperature=298.15,
        standard_state="1M",
    )

    ensemble_delta = 3.166811563e-6 * 298.15 * math.log(0.25)
    standard_state_delta = standard_state_correction_kcal(298.15) / HARTREE_TO_KCAL
    expected = -99.95 + ensemble_delta + standard_state_delta

    assert result.gibbs_hartree == pytest.approx(expected, abs=1e-10)
    assert result.corrections["ensemble_delta_g_hartree"] == pytest.approx(ensemble_delta)
    assert result.corrections["representative_weight"] == pytest.approx(0.25)
    assert result.corrections["standard_state_delta_g_kcal_mol"] == pytest.approx(1.8938, abs=1e-3)
    assert result.corrections["gibbs_components_hartree"]["sp_plus_freq"] == pytest.approx(-99.95)


def test_compute_batch_rejects_shared_sp_energy_without_confirmation(tmp_path: Path) -> None:
    provider = ShermoProvider(
        runner=lambda **_: {
            "g_sum": -99.0,
            "h_sum": -99.0,
            "u_sum": -99.0,
            "g_conc": None,
            "s_total": 0.0,
        }
    )

    items = [
        ThermochemistryBatchItem(sp_energy=None, freq_log=tmp_path / "a.log"),
        ThermochemistryBatchItem(sp_energy=None, freq_log=tmp_path / "b.log"),
    ]

    with pytest.raises(ValueError, match="broadcast one sp_energy"):
        provider.compute_batch(items, shared_sp_energy=-100.0)


def test_standard_state_helper_reference_values() -> None:
    correction = standard_state_correction_kcal(298.15)

    assert correction == pytest.approx(1.8938, abs=1e-3)
    assert standard_state_correction_kcal(298.15, n_particles_change=-1.0) == pytest.approx(
        -correction
    )
    assert standard_state_correction_kcal(350.0) > correction

    with pytest.raises(ValueError, match="positive finite"):
        standard_state_correction_kcal(0.0)
