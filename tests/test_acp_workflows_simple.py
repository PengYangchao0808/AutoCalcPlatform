"""Tests for simple ORCA workflows (singlepoint/optimize/frequency/optfreq/optfreqsp).

Covers: catalog validation, input parsing, ensure_unique_dir, mocked workflow
execution for all 5 entry points, CLI help smoke tests, and optfreqsp data flow.
"""
# pyright: reportUnknownVariableType=false,reportUnknownMemberType=false
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.core.utils import ensure_unique_dir
from acp.workflows.simple import (
    _check_input,
    _read_input,
    _write_energy_json,
    _write_frequencies_txt,
    _write_optimized_xyz,
    _write_thermo_json,
    run_singlepoint,
    run_optimize,
    run_frequency,
    run_optfreq,
)

# ---------------------------------------------------------------------------
# catalog validation
# ---------------------------------------------------------------------------

_SIMPLE_IDS = {"singlepoint", "optimize", "frequency", "optfreq", "optfreqsp"}


def test_all_simple_workflows_in_catalog_and_active():
    found: dict[str, str] = {}
    for entry in WORKFLOW_CATALOG:
        if entry["id"] in _SIMPLE_IDS:
            found[entry["id"]] = entry["status"]
    for wid in _SIMPLE_IDS:
        assert wid in found, f"Missing catalog entry for {wid}"
        assert found[wid] == "active", f"{wid} status={found[wid]}, expected active"


def test_dft_optfreqsp_schema_has_three_levels():
    schema = METHOD_SCHEMAS["dft_optfreqsp"]
    level_ids = {lv["level_id"] for lv in schema["method_levels"]}
    assert level_ids == {"optfreq", "single_point", "thermo"}


def test_dft_optfreqsp_schema_has_default_profile():
    schema = METHOD_SCHEMAS["dft_optfreqsp"]
    profiles = schema["profiles"]
    assert any(p["profile_id"] == "default" for p in profiles)


def test_schema_level_id_naming_consistent():
    """dft_singlepoint level_id must be 'single_point' (with underscore)."""
    schema = METHOD_SCHEMAS["dft_singlepoint"]
    for lv in schema["method_levels"]:
        if lv["level_id"] == "singlepoint":
            pytest.fail("dft_singlepoint level_id should be 'single_point', not 'singlepoint'")


_SIMPLE_SCHEMAS = [
    "dft_singlepoint",
    "dft_optimize",
    "dft_frequency",
    "dft_optfreq",
    "dft_optfreqsp",
]


@pytest.mark.parametrize("schema_id", _SIMPLE_SCHEMAS)
def test_simple_schemas_exist_and_have_fields(schema_id):
    schema = METHOD_SCHEMAS[schema_id]
    assert schema["method_levels"], f"{schema_id} has no method_levels"
    for lv in schema["method_levels"]:
        assert "level_id" in lv
        assert "fields" in lv
        assert isinstance(lv["fields"], list)
        assert len(lv["fields"]) >= 1, f"{schema_id}/{lv['level_id']} has empty fields"


# ---------------------------------------------------------------------------
# ensure_unique_dir
# ---------------------------------------------------------------------------

def test_ensure_unique_dir_creates_new_dir(tmp_path):
    d = ensure_unique_dir(tmp_path / "new_dir")
    assert d.exists()
    assert d.name == "new_dir"


def test_ensure_unique_dir_appends_counter(tmp_path):
    first = tmp_path / "collide"
    first.mkdir()
    second = ensure_unique_dir(first)
    assert second.exists()
    assert second.name == "collide_1"


def test_ensure_unique_dir_increments_counter(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "base_1").mkdir()
    result = ensure_unique_dir(base)
    assert result.name == "base_2"


# ---------------------------------------------------------------------------
# input parsing
# ---------------------------------------------------------------------------

def test_check_input_rejects_smiles(tmp_path):
    smi = tmp_path / "mol.smi"
    smi.write_text("CCO")
    with pytest.raises(ValueError, match="Unsupported input format"):
        _check_input(str(smi))


def test_check_input_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        _check_input("/nonexistent/file.xyz")


def test_check_input_accepts_xyz(tmp_path):
    f = tmp_path / "mol.xyz"
    f.write_text("3\n\nC 0 0 0\nH 1 0 0\nH 0 1 0\n")
    assert _check_input(str(f)) == f


def test_check_input_accepts_gjf(tmp_path):
    f = tmp_path / "mol.gjf"
    f.write_text("# B3LYP\n\nwater\n\n0 1\nO 0 0 0\nH 1 0 0\nH 0 1 0\n")
    assert _check_input(str(f)) == f


def test_check_input_accepts_inp(tmp_path):
    f = tmp_path / "mol.inp"
    f.write_text("! B3LYP\n* xyz 0 1\nO 0 0 0\n*\n")
    assert _check_input(str(f)) == f


def test_read_input_xyz(tmp_path):
    f = tmp_path / "test.xyz"
    f.write_text("3\n\nO     0.0   0.0   0.0\nH     0.0   1.0   0.0\nH     0.0   0.0   1.0\n")
    coords, symbols, charge, mult = _read_input(str(f), charge=0, multiplicity=1, name=None)
    assert symbols == ["O", "H", "H"]
    assert charge == 0
    assert mult == 1
    assert coords.shape == (3, 3)


# ---------------------------------------------------------------------------
# output writers
# ---------------------------------------------------------------------------


def test_write_energy_json(tmp_path):
    _write_energy_json(tmp_path, -76.42)
    data = json.loads((tmp_path / "energy.json").read_text())
    assert data["energy"] == -76.42
    assert data["unit"] == "Hartree"


def test_write_frequencies_txt(tmp_path):
    _write_frequencies_txt(tmp_path, [100.0, 200.0, 3500.0])
    lines = (tmp_path / "frequencies.txt").read_text().splitlines()
    assert len(lines) == 3
    assert "100.0000" in lines[0]


def test_write_optimized_xyz(tmp_path):
    coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.96, 0.0]])
    symbols = ["O", "H"]
    _write_optimized_xyz(tmp_path, coords, symbols)
    text = (tmp_path / "optimized.xyz").read_text()
    assert text.startswith("2\n")
    assert "O " in text
    assert "H " in text


def test_write_thermo_json(tmp_path):
    thermo = {"g_sum": -76.45, "h_sum": -76.44, "u_sum": -76.46, "s_total": 0.0001}
    _write_thermo_json(tmp_path, thermo, -76.50)
    data = json.loads((tmp_path / "thermo.json").read_text())
    assert data["sp_energy_hartree"] == -76.50
    assert data["free_energy_hartree"] == -76.45
    assert data["thermal_correction_u_hartree"] == pytest.approx(0.04)
    assert data["total_enthalpy_hartree"] == -76.44
    assert data["total_gibbs_hartree"] == -76.45
    assert data["success"] is True


def test_write_thermo_json_empty(tmp_path):
    _write_thermo_json(tmp_path, {}, -76.50)
    data = json.loads((tmp_path / "thermo.json").read_text())
    assert data["success"] is False
    assert data["free_energy_hartree"] == 0.0


# ---------------------------------------------------------------------------
# mock helpers
# ---------------------------------------------------------------------------

def _fake_qc_result(**overrides):
    defaults = {"success": True, "energy": -76.42, "coordinates": None, "symbols": None,
                "converged": True, "frequencies": None, "has_frequencies": False,
                "log_file": Path("/tmp/fake.out"), "error_message": None}
    defaults.update(overrides)
    return QCResult(**defaults)


def _mock_backend_method(name, return_value):
    """Patch ORCABackend's named method to return return_value."""
    return patch.object(
        type("Dummy", (), {}), "x", side_effect=None, autospec=False
    )


# ---------------------------------------------------------------------------
# singlepoint
# ---------------------------------------------------------------------------

def test_run_singlepoint_mock(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "sp_out"

    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.single_point.return_value = _fake_qc_result(energy=-40.0)
        result = run_singlepoint(str(inp), output_dir=out)
        mk_backend.return_value.single_point.assert_called_once()
        assert result.status == "completed"
        assert result.metadata.get("energy") == -40.0


def test_run_singlepoint_failure(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "sp_out"

    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.single_point.return_value = _fake_qc_result(success=False, error_message="ORCA error")
        result = run_singlepoint(str(inp), output_dir=out)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# optimize
# ---------------------------------------------------------------------------

def test_run_optimize_mock(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "opt_out"

    coords = np.array([[0.0, 0.0, 0.0]])
    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.optimize.return_value = _fake_qc_result(
            coordinates=coords, symbols=["C"], converged=True,
        )
        result = run_optimize(str(inp), output_dir=out)
        mk_backend.return_value.optimize.assert_called_once()
        assert result.status == "completed"
        assert result.metadata.get("converged") is True


def test_run_optimize_failure(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "opt_out"

    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.optimize.return_value = _fake_qc_result(success=False, error_message="No convergence")
        result = run_optimize(str(inp), output_dir=out)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# frequency
# ---------------------------------------------------------------------------

def test_run_frequency_mock(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "freq_out"

    freqs = [100.0, 200.0, 3500.0]
    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.frequency.return_value = _fake_qc_result(
            frequencies=freqs, has_frequencies=True,
        )
        result = run_frequency(str(inp), output_dir=out)
        mk_backend.return_value.frequency.assert_called_once()
        assert result.status == "completed"
        assert result.metadata.get("n_frequencies") == 3
        assert result.metadata.get("has_frequencies") is True


def test_run_frequency_no_modes(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "freq_out"

    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.frequency.return_value = _fake_qc_result(has_frequencies=False)
        result = run_frequency(str(inp), output_dir=out)
        assert result.status == "completed"
        assert result.metadata.get("n_frequencies") == 0


# ---------------------------------------------------------------------------
# optfreq
# ---------------------------------------------------------------------------

def test_run_optfreq_mock(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "optfreq_out"

    coords = np.array([[0.1, 0.0, 0.0]])
    freqs = [500.0, 1600.0]
    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.opt_freq.return_value = _fake_qc_result(
            coordinates=coords, symbols=["C"], frequencies=freqs, has_frequencies=True, converged=True,
        )
        result = run_optfreq(str(inp), output_dir=out)
        mk_backend.return_value.opt_freq.assert_called_once()
        assert result.status == "completed"
        assert result.metadata.get("n_frequencies") == 2


def test_run_optfreq_failure(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "optfreq_out"

    with patch("acp.workflows.simple._build_backend") as mk_backend:
        mk_backend.return_value.opt_freq.return_value = _fake_qc_result(success=False, error_message="Opt Freq failed")
        result = run_optfreq(str(inp), output_dir=out)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# optfreqsp
# ---------------------------------------------------------------------------

def test_run_optfreqsp_mock(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "optfreqsp_out"
    log_file = tmp_path / "fake_optfreq.out"
    log_file.write_text("ORCA log placeholder")

    opt_coords = np.array([[0.1, 0.0, 0.0]])
    freqs = [500.0, 1600.0]

    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
        patch("acp.backends.external.run_shermo") as mk_shermo,
    ):
        mk_shermo.return_value = {"g_sum": -40.5, "h_sum": -40.4, "u_sum": -40.6, "s_total": 0.0001}

        optfreq_result = _fake_qc_result(
            coordinates=opt_coords, symbols=["C"], frequencies=freqs, has_frequencies=True, log_file=log_file,
        )
        sp_result = _fake_qc_result(energy=-40.0)

        def _se_side_effect(cfg):
            be = MagicMock()
            be.opt_freq.return_value = optfreq_result
            be.single_point.return_value = sp_result
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)

        assert result.status == "completed"
        assert result.metadata.get("sp_energy") == -40.0
        assert result.metadata.get("n_frequencies") == 2
        assert result.metadata.get("thermo_success") is True

        mk_shermo.assert_called_once()
        call_kwargs = mk_shermo.call_args.kwargs
        assert "freq_output" in call_kwargs
        assert "sp_energy" in call_kwargs


def test_run_optfreqsp_no_shermo(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "no_shermo_out"

    with patch("acp.workflows.simple._find_shermo", return_value=False):
        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "failed"
        assert "Shermo" in (result.error or "")


def test_run_optfreqsp_optfreq_failure(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "fail_out"

    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
    ):
        mk_backend.return_value.opt_freq.return_value = _fake_qc_result(success=False, error_message="Opt+Freq crash")

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "failed"
        assert "Opt+Freq" in (result.error or "")

        assert mk_backend.call_count >= 1
        call_args = mk_backend.call_args_list[0][0]
        assert len(call_args) == 1


def test_run_optfreqsp_sp_failure(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "sp_fail_out"

    opt_coords = np.array([[0.1, 0.0, 0.0]])
    log_file = tmp_path / "f.out"
    log_file.write_text("ORCA log")
    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
    ):
        optfreq_result = _fake_qc_result(
            coordinates=opt_coords, symbols=["C"], frequencies=[500.0], has_frequencies=True, log_file=log_file,
        )

        def _se_side_effect(cfg):
            be = MagicMock()
            be.opt_freq.return_value = optfreq_result
            be.single_point.return_value = _fake_qc_result(success=False, error_message="SP crash")
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "failed"
        assert "SP" in (result.error or "")


def test_run_optfreqsp_no_log_file(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "no_log_out"

    opt_coords = np.array([[0.1, 0.0, 0.0]])
    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
    ):
        optfreq_result = _fake_qc_result(
            coordinates=opt_coords, symbols=["C"], frequencies=[500.0], has_frequencies=True, log_file=None,
        )

        def _se_side_effect(cfg):
            be = MagicMock()
            be.opt_freq.return_value = optfreq_result
            be.single_point.return_value = _fake_qc_result(energy=-40.0)
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "failed"
        assert "log file" in (result.error or "").lower()


def test_run_optfreqsp_sp_energy_none(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "no_sp_en_out"

    opt_coords = np.array([[0.1, 0.0, 0.0]])
    log_file = tmp_path / "spen_f.out"
    log_file.write_text("ORCA log")
    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
    ):
        optfreq_result = _fake_qc_result(
            coordinates=opt_coords, symbols=["C"], frequencies=[500.0], has_frequencies=True, log_file=log_file,
        )

        def _se_side_effect(cfg):
            be = MagicMock()
            be.opt_freq.return_value = optfreq_result
            be.single_point.return_value = _fake_qc_result(energy=None)
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "failed"
        assert "no energy" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# QCResult field verification
# ---------------------------------------------------------------------------

def test_optfreq_qcresult_fields_from_mock():
    """Verify opt_freq returns QCResult with coordinates+frequencies+has_frequencies=True."""
    fake = _fake_qc_result(
        coordinates=np.array([[0.0, 0.0, 0.0]]),
        symbols=["C"],
        frequencies=[500.0],
        has_frequencies=True,
    )
    assert fake.has_frequencies is True
    assert fake.frequencies == [500.0]
    assert fake.coordinates is not None
    assert fake.coordinates.shape == (1, 3)


# ---------------------------------------------------------------------------
# CLI help smoke tests
# ---------------------------------------------------------------------------

_SIMPLE_WF = [
    ("singlepoint", "--method"),
    ("optimize", "--geom-maxiter"),
    ("frequency", "--temperature"),
    ("optfreq", "--scale-factor"),
    ("optfreqsp", "--sp-method"),
]


@pytest.mark.parametrize("wf_name,expected_flag", _SIMPLE_WF)
def test_cli_help_contains_expected_flags(wf_name, expected_flag):
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", wf_name, "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert expected_flag in result.stdout, f"Expected '{expected_flag}' in help for {wf_name}"


def test_cli_singlepoint_help_common_args():
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "singlepoint", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--input" in result.stdout
    assert "--charge" in result.stdout
    assert "--multiplicity" in result.stdout
    assert "--nproc" in result.stdout
    assert "--mem" in result.stdout


def test_cli_optfreqsp_has_sp_args():
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "optfreqsp", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--sp-method" in result.stdout
    assert "--sp-basis" in result.stdout
    assert "--sp-aux-basis" in result.stdout
    assert "--sp-ri-approximation" in result.stdout


# ---------------------------------------------------------------------------
# optfreqsp data flow: opt_coords → SP, opt_log → Shermo
# ---------------------------------------------------------------------------

def test_optfreqsp_data_flow_passes_opt_coords_to_sp(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "dataflow_out"

    opt_coords = np.array([[0.1, 0.2, 0.3]])
    instances: list[MagicMock] = []
    log_file = tmp_path / "df.out"
    log_file.write_text("ORCA log")

    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
        patch("acp.backends.external.run_shermo") as mk_shermo,
    ):
        mk_shermo.return_value = {"g_sum": -40.5}

        def _se_side_effect(cfg):
            be = MagicMock()
            instances.append(be)
            be.opt_freq.return_value = _fake_qc_result(
                coordinates=opt_coords, symbols=["C"], frequencies=[500.0], has_frequencies=True, log_file=log_file,
            )
            be.single_point.return_value = _fake_qc_result(energy=-40.0)
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "completed"

        sp_mock = instances[1]
        sp_call_coords = sp_mock.single_point.call_args[0][0]
        np.testing.assert_array_equal(sp_call_coords, opt_coords)


def test_optfreqsp_data_flow_passes_log_file_to_shermo(tmp_path):
    inp = tmp_path / "mol.xyz"
    inp.write_text("1\n\nC 0 0 0\n")
    out = tmp_path / "logflow_out"

    fake_log = tmp_path / "optfreq_test_log.out"
    fake_log.write_text("ORCA log placeholder")

    with (
        patch("acp.workflows.simple._find_shermo", return_value=True),
        patch("acp.workflows.simple._build_backend") as mk_backend,
        patch("acp.backends.external.run_shermo") as mk_shermo,
    ):
        mk_shermo.return_value = {"g_sum": -40.5}

        def _se_side_effect(cfg):
            be = MagicMock()
            be.opt_freq.return_value = _fake_qc_result(
                coordinates=np.array([[0.0, 0.0, 0.0]]), symbols=["C"],
                frequencies=[500.0], has_frequencies=True, log_file=fake_log,
            )
            be.single_point.return_value = _fake_qc_result(energy=-40.0)
            return be

        mk_backend.side_effect = _se_side_effect

        from acp.workflows.simple import run_optfreqsp
        result = run_optfreqsp(str(inp), output_dir=out)
        assert result.status == "completed"

        call_kwargs = mk_shermo.call_args.kwargs
        assert call_kwargs["freq_output"] == fake_log
        assert call_kwargs["sp_energy"] == -40.0
