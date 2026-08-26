from __future__ import annotations

import math
from pathlib import Path

import pytest

import acp.calculations.primitives.thermochemistry as thermochemistry
from acp.backends import ExternalBackend
from acp.calculations.primitives.thermochemistry import ThermochemistryCalculator


def test_compute_full_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")
    calls: list[tuple[Path, float, Path, float, float, float | None]] = []

    def fake_run_shermo(
        *,
        freq_output: Path,
        sp_energy: float,
        output_dir: Path,
        temperature_k: float,
        pressure_atm: float,
        scl_zpe: float,
        conc: float | None,
        **_: str | int | float | Path | None,
    ) -> dict[str, float]:
        assert scl_zpe == 0.9905
        calls.append((freq_output, sp_energy, output_dir, temperature_k, pressure_atm, conc))
        return {
            "u_sum": -99.90,
            "h_sum": -99.88,
            "g_sum": -99.95,
            "g_conc": -99.94,
            "s_total": 0.0123,
        }

    monkeypatch.setattr(thermochemistry, "run_shermo", fake_run_shermo)

    result = ThermochemistryCalculator().compute(
        freq_log_path=freq_log,
        sp_energy_hartree=-100.0,
        temperature=298.15,
        pressure=1.0,
        standard_state="1atm",
    )

    assert result.status == "completed"
    assert result.energy == -100.0
    gibbs = result.metadata["gibbs_hartree"]
    enthalpy = result.metadata["enthalpy_hartree"]
    entropy = result.metadata["entropy_au"]
    assert isinstance(gibbs, float)
    assert isinstance(enthalpy, float)
    assert isinstance(entropy, float)
    assert math.isclose(gibbs, -99.94)
    assert math.isclose(enthalpy, -99.88)
    assert math.isclose(entropy, 0.0123)
    assert calls == [(freq_log, -100.0, tmp_path, 298.15, 1.0, None)]


def test_compute_roundtrip_applies_one_molar_standard_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")

    def fake_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        return {"g_sum": -10.0, "h_sum": -9.9, "s_total": 0.01}

    monkeypatch.setattr(thermochemistry, "run_shermo", fake_run_shermo)

    result = ThermochemistryCalculator().compute(freq_log, -10.2, 298.15, 1.0, "1M")

    assert result.metadata["standard_state"] == "1M"
    gibbs = result.metadata["gibbs_hartree"]
    standard_delta = result.metadata["standard_state_delta_g_hartree"]
    assert isinstance(gibbs, float)
    assert isinstance(standard_delta, float)
    assert gibbs > -10.0
    assert standard_delta > 0.0


def test_missing_freqlog_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        pytest.fail("Shermo must not run without a frequency log")

    monkeypatch.setattr(thermochemistry, "run_shermo", fail_run_shermo)

    with pytest.raises(ValueError, match="frequency log"):
        _ = ThermochemistryCalculator().compute(
            freq_log_path=tmp_path / "missing.log",
            sp_energy_hartree=-10.2,
            temperature=298.15,
            pressure=1.0,
            standard_state="1atm",
        )


def test_external_backend_delegates_to_calculator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")

    def fake_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        return {"h_sum": -9.9, "g_sum": -10.0, "s_total": 0.01}

    monkeypatch.setattr(thermochemistry, "run_shermo", fake_run_shermo)
    result = ExternalBackend({}).thermochemistry(
        freq_log,
        output_dir=tmp_path / "thermo",
        sp_energy=-10.2,
        temperature_k=300.0,
        pressure_atm=1.0,
    )

    assert result.success is True
    gibbs = result.gibbs
    enthalpy = result.enthalpy
    entropy = result.entropy
    assert gibbs is not None
    assert enthalpy is not None
    assert entropy is not None
    assert math.isclose(gibbs, -10.0)
    assert math.isclose(enthalpy, -9.9)
    assert math.isclose(entropy, 0.01)
