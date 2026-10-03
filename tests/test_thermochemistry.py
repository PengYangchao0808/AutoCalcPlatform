from __future__ import annotations

import math
from pathlib import Path

import pytest

import acp.calculations.primitives.thermochemistry as thermochemistry
import cccp.qc.shermo_adapter as shermo_adapter
from acp.backends import ExternalBackend
from cccp.backends import external_backend as external_backend_module
from acp.calculations.primitives.thermochemistry import ThermochemistryCalculator

# Frozen baseline: ideal-gas 1 atm -> 1 mol/L correction at 298.15 K
# (= standard_state_correction_kcal(298.15) kcal/mol / HARTREE_TO_KCAL).
_BASELINE_DELTA_HARTREE_298K = 0.003018804534102794
_BASELINE_DELTA_KCAL_298K = 1.894328445494146


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

    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)

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

    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)

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

    monkeypatch.setattr(shermo_adapter, "run_shermo", fail_run_shermo)

    with pytest.raises(ValueError, match="frequency log"):
        _ = ThermochemistryCalculator().compute(
            freq_log_path=tmp_path / "missing.log",
            sp_energy_hartree=-10.2,
            temperature=298.15,
            pressure=1.0,
            standard_state="1atm",
        )


def test_external_backend_delegates_to_shared_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")

    def fake_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        return {"h_sum": -9.9, "g_sum": -10.0, "s_total": 0.01}

    # The fake runner below stands in for the Shermo binary; without it the
    # pre-launch probe rejects the call (delta D3: BackendUnavailableError).
    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)
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


def test_both_entries_share_one_adapter_binding() -> None:
    """The legacy primitive and the external backend bind the SAME shared entry."""
    assert thermochemistry.execute_shermo is shermo_adapter.execute_shermo
    assert external_backend_module.execute_shermo is shermo_adapter.execute_shermo


def test_six_legacy_params_reach_runner_with_single_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run_shermo(**kwargs: object) -> dict[str, float]:
        calls.append(kwargs)
        return {"g_sum": -10.0, "h_sum": -9.9, "s_total": 0.01}

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)
    explicit = tmp_path / "thermo" / "custom.sum"
    result = ExternalBackend({}).thermochemistry(
        freq_log,
        output_dir=tmp_path / "thermo",
        output_file=explicit,
        sp_energy=-10.2,
        temperature_k=300.0,
        pressure_atm=1.5,
        standard_state="1M",
        scl_zpe=0.98,
        ilowfreq=1,
        imagreal=2,
        conc=0.05,
    )

    assert result.success is True
    assert len(calls) == 1, "one request must launch Shermo exactly once"
    kwargs = calls[0]
    # Six legacy parameters, spy-verified at the runner seam.
    assert kwargs["output_file"] == explicit
    assert kwargs["scl_zpe"] == 0.98
    assert kwargs["ilowfreq"] == 1
    assert kwargs["imagreal"] == 2
    assert kwargs["conc"] == 0.05
    # standard_state is handled through the shared path (metadata projection).
    assert result.metadata["standard_state"] == "1M"
    assert kwargs["sp_energy"] == -10.2
    assert kwargs["temperature_k"] == 300.0
    assert kwargs["pressure_atm"] == 1.5
    # Explicit output_file stays consistent across runner and metadata.
    assert result.output_file == explicit
    assert result.metadata["output_file"] == str(explicit)


def test_standard_state_drives_conc_default_at_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run_shermo(**kwargs: object) -> dict[str, float]:
        calls.append(kwargs)
        return {"g_sum": -10.0, "h_sum": -9.9, "s_total": 0.01}

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)
    backend = ExternalBackend({})
    _ = backend.thermochemistry(freq_log, output_dir=tmp_path / "a", standard_state="1atm")
    _ = backend.thermochemistry(freq_log, output_dir=tmp_path / "m", standard_state="1M")

    assert len(calls) == 2
    assert calls[0]["conc"] is None
    assert calls[1]["conc"] == 1.0


def test_default_output_paths_preserved_per_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def fake_run_shermo(**kwargs: object) -> dict[str, float]:
        calls.append(kwargs)
        return {"g_sum": -10.0, "h_sum": -9.9, "s_total": 0.01}

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)

    _ = ThermochemistryCalculator().compute(freq_log, -10.2, 298.15, 1.0, "1atm")
    assert calls[-1]["output_dir"] == tmp_path
    assert calls[-1]["output_file"] == tmp_path / "Shermo.sum", "primitive default is Shermo.sum"

    _ = ExternalBackend({}).thermochemistry(freq_log, output_dir=tmp_path / "thermo")
    assert calls[-1]["output_dir"] == tmp_path / "thermo"
    assert calls[-1]["output_file"] == tmp_path / "thermo" / "frequency.sum", (
        "backend default is <stem>.sum"
    )

    explicit = tmp_path / "explicit" / "named.sum"
    _ = ThermochemistryCalculator(output_file=explicit).compute(
        freq_log, -10.2, 298.15, 1.0, "1atm"
    )
    assert calls[-1]["output_file"] == explicit

    assert len(calls) == 3, "each request launches Shermo exactly once"


def test_one_atm_and_one_molar_baseline_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")

    def fake_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        return {"u_sum": -99.9, "h_sum": -9.9, "g_sum": -10.0, "s_total": 0.01}

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fake_run_shermo)

    # Legacy primitive entry: 1atm selects g_sum as-is; 1M adds the frozen
    # standard-state correction baseline.
    atm = ThermochemistryCalculator().compute(freq_log, -10.2, 298.15, 1.0, "1atm")
    assert atm.metadata["gibbs_hartree"] == pytest.approx(-10.0, abs=1e-12)
    assert atm.metadata["selected_gibbs_source"] == "g_sum"
    assert atm.metadata["standard_state_delta_g_hartree"] is None

    molar = ThermochemistryCalculator().compute(freq_log, -10.2, 298.15, 1.0, "1M")
    assert molar.metadata["gibbs_hartree"] == pytest.approx(
        -10.0 + _BASELINE_DELTA_HARTREE_298K, abs=1e-12
    )
    assert molar.metadata["selected_gibbs_source"] == "g_sum_plus_standard_state"
    assert molar.metadata["standard_state_delta_g_hartree"] == pytest.approx(
        _BASELINE_DELTA_HARTREE_298K, abs=1e-12
    )
    assert molar.metadata["standard_state_delta_g_kcal_mol"] == pytest.approx(
        _BASELINE_DELTA_KCAL_298K, abs=1e-9
    )

    # Backend entry: identical baselines through QCResult.
    batm = ExternalBackend({}).thermochemistry(
        freq_log, output_dir=tmp_path / "a", standard_state="1atm"
    )
    assert batm.gibbs == pytest.approx(-10.0, abs=1e-12)
    assert batm.metadata["selected_gibbs_source"] == "g_sum"

    bmolar = ExternalBackend({}).thermochemistry(
        freq_log, output_dir=tmp_path / "m", standard_state="1M"
    )
    assert bmolar.gibbs == pytest.approx(-10.0 + _BASELINE_DELTA_HARTREE_298K, abs=1e-12)
    assert bmolar.metadata["selected_gibbs_source"] == "g_sum_plus_standard_state"


def test_runtime_failure_semantics_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freq_log = tmp_path / "frequency.log"
    _ = freq_log.write_text("frequency output", encoding="utf-8")

    def fail_run_shermo(**_: str | int | float | Path | None) -> dict[str, float] | None:
        return None

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fail_run_shermo)

    primitive = ThermochemistryCalculator().compute(freq_log, -10.2, 298.15, 1.0, "1atm")
    assert primitive.status == "failed"
    assert primitive.errors == ["Shermo returned no thermochemistry data"]

    backend = ExternalBackend({}).thermochemistry(freq_log, output_dir=tmp_path / "thermo")
    assert backend.success is False
    assert backend.error_message == "Shermo returned no thermochemistry data"


def test_backend_input_error_semantics_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run_shermo(**_: str | int | float | Path | None) -> dict[str, float]:
        pytest.fail("Shermo must not run on invalid input")

    monkeypatch.setattr(ExternalBackend, "is_shermo_available", lambda self: True)
    monkeypatch.setattr(shermo_adapter, "run_shermo", fail_run_shermo)

    result = ExternalBackend({}).thermochemistry(
        tmp_path / "missing.log",
        output_dir=tmp_path / "thermo",
    )
    assert result.success is False
    assert result.error_message is not None
    assert "frequency log" in result.error_message
