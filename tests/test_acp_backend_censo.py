"""Tests for CensoBackend — mocked subprocess and output parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from acp.backends import (
    CAPABILITY_MATRIX,
    CensoBackend,
    list_capabilities,
    supports,
)
from acp.backends.base import ConformerSearcher
from acp.backends.censo_backend import (
    CensoConformerRecord,
    CensoError,
    CensoExecutionError,
    CensoNotAvailableError,
    CensoParseError,
    CensoRunResult,
)
from acp.backends.registry import get_backend, require_backend
from cccp.qc.interfaces.censo import CensoInterface


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "executables": {
            "censo": {"path": "censo"},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
        },
        "resources": {"nproc": 4},
        "censo": {
            "preset": "censo-light",
            "temperature": 298.15,
        },
    }
    config.update(overrides)
    return config


def _make_mock_censo_script(tmp_path: Path, exit_code: int = 0) -> Path:
    """Create a mock censo executable that writes predictable output files."""
    xyz_content = "\n".join([
        "3",
        "CONF1",
        "C    0.000000    0.000000    0.000000",
        "H    0.000000    0.000000    1.089000",
        "H    1.026719    0.000000   -0.362999",
        "3",
        "CONF2",
        "C    0.000000    0.000000    0.000000",
        "H    0.000000    0.000000    1.089000",
        "H   -1.026719    0.000000   -0.362999",
    ])

    json_data = {
        "part_name": "screening",
        "settings": {"prog": "orca", "func": "b97-3c", "threshold": 6.0},
        "data": {
            "CONF1": {
                "energy": -154.912345,
                "gsolv": -0.004521,
                "grrho": 0.082341,
                "gtot": -154.834525,
            },
            "CONF2": {
                "energy": -154.911876,
                "gsolv": -0.004612,
                "grrho": 0.082455,
                "gtot": -154.834033,
            },
        },
    }

    out_content = (
        "  Conf    E(DFT)        ΔGsolv        GmRRHO        Gtot"
        "        ΔGtot      Boltzmann weight\n"
        " CONF1  -154.912345   -0.004521     0.082341    -154.834525"
        "     0.000000      0.645321\n"
        " CONF2  -154.911876   -0.004612     0.082455    -154.834033"
        "     0.000492      0.354679\n"
    )

    json_str = json.dumps(json_data, indent=2)

    script = tmp_path / "mock_censo.sh"
    lines = [
        "#!/bin/bash",
        'echo "Mock CENSO called with: $@" >&2',
        'cat > "1_SCREENING.json" << JSONEOF',
        json_str,
        "JSONEOF",
        'cat > "1_SCREENING.xyz" << XYZEOF',
        xyz_content,
        "XYZEOF",
        'cat > "1_SCREENING.out" << OUTEOF',
        out_content,
        "OUTEOF",
        f"exit {exit_code}",
    ]
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return script


def _make_censo_script_with_crash(tmp_path: Path) -> Path:
    """Create a mock censo that exits non-zero with a CRASH_DUMP."""
    crash_data = {
        "error": "some_censo_error",
        "details": "ORCA failed for CONF5",
    }
    crash_json = json.dumps(crash_data, indent=2)
    script = tmp_path / "mock_censo_crash.sh"
    lines = [
        "#!/bin/bash",
        'cat > "CRASH_DUMP.json" << JSONEOF',
        crash_json,
        "JSONEOF",
        "echo 'CENSO error: something went wrong' >&2",
        "exit 1",
    ]
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return script


# ---------------------------------------------------------------------------
# Backend capability tests
# ---------------------------------------------------------------------------


def test_censo_backend_registered() -> None:
    assert get_backend("censo") is CensoBackend


def test_censo_implements_conformer_searcher() -> None:
    assert issubclass(CensoBackend, ConformerSearcher)


def test_censo_capability_matrix() -> None:
    assert "censo" in CAPABILITY_MATRIX
    assert supports("censo", "conformer_search") is True
    assert supports("censo", "geometry_optimization") is False
    assert supports("censo", "single_point") is False
    assert supports("censo", "frequency") is False


def test_censo_backend_is_available_checks_binary() -> None:
    config = _make_config()

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=Path("/usr/bin/censo")):
        backend = CensoBackend(config)
        assert backend.is_available() is True

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=None):
        backend = CensoBackend(config)
        assert backend.is_available() is False


def test_censo_backend_versions_works() -> None:
    config = _make_config()

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=Path("/usr/bin/censo")):
        backend = CensoBackend(config)

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "3.0.8"
            mock_run.return_value = mock_proc

            version = backend.get_version()
            assert version == "3.0.8"


# ---------------------------------------------------------------------------
# Preset resolution
# ---------------------------------------------------------------------------


def test_default_preset_is_censo_light() -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset(None)
    assert preset["name"] == "censo-light"
    assert "prescreening" in preset["parts"]
    assert "screening" in preset["parts"]


def test_censo_default_preset() -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-default")
    assert preset["name"] == "censo-default"
    assert "optimization" in preset["parts"]
    assert "refinement" in preset["parts"]


def test_censo_zero_preset() -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-zero")
    assert preset["name"] == "censo-zero"


def test_unknown_preset_raises() -> None:
    config = _make_config()
    interface = CensoInterface(config)
    with pytest.raises(ValueError, match="Unknown CENSO preset"):
        interface.resolve_preset("nonexistent")


# ---------------------------------------------------------------------------
# rcfile generation
# ---------------------------------------------------------------------------


def test_rcfile_contains_paths(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(preset, tmp_path, charge=0, multiplicity=1, solvent=None)

    content = rcfile.read_text(encoding="utf-8")
    assert "[general]" in content
    assert "[paths]" in content
    assert "orca = orca" in content
    assert "xtb = xtb" in content
    assert "charge = 0" in content
    assert "evaluate_rrho = True" in content
    assert "gas_phase = True" in content


def test_rcfile_includes_solvent(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(preset, tmp_path, charge=0, multiplicity=1, solvent="dcm")

    content = rcfile.read_text(encoding="utf-8")
    assert "solvent = dcm" in content
    assert "gas_phase = False" in content
    # CENSO v3.0.8 defaults sm=COSMORS for screening; we override to avoid
    # requiring the cosmotherm binary (which is not available). When no
    # solvent_model is explicitly configured, the override falls back to
    # the default ("none" = gas phase solvation model for individual parts).
    assert "sm = none" in content


def test_rcfile_solvent_model_smd(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(
        preset, tmp_path, charge=0, multiplicity=1, solvent="ethanol",
        solvent_model="smd",
    )

    content = rcfile.read_text(encoding="utf-8")
    assert "sm = smd" in content


def test_rcfile_no_sm_when_gas_phase(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(preset, tmp_path, charge=0, multiplicity=1, solvent=None)

    content = rcfile.read_text(encoding="utf-8")
    assert "gas_phase = True" in content
    # No solvent → sm not written (gas-phase CENSO skips cosmotherm check)
    assert "sm =" not in content


def test_rcfile_has_preset_sections(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-default")
    rcfile = interface.generate_rcfile(preset, tmp_path, charge=0, multiplicity=1, solvent=None)

    content = rcfile.read_text(encoding="utf-8")
    assert "[prescreening]" in content
    assert "[screening]" in content
    assert "[optimization]" in content
    assert "[refinement]" in content


def test_rcfile_correct_uhf_from_multiplicity(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    preset = interface.resolve_preset("censo-light")
    rcfile = interface.generate_rcfile(preset, tmp_path, charge=0, multiplicity=3, solvent=None)

    content = rcfile.read_text(encoding="utf-8")
    assert "uhf = 2" in content


# ---------------------------------------------------------------------------
# CLI construction
# ---------------------------------------------------------------------------


def test_build_cli_parts(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH  0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=Path("/usr/bin/censo")):
        interface = CensoInterface(config)
        preset = interface.resolve_preset("censo-light")
        cmd = interface.build_cli(
            input_xyz, rcfile, preset,
            nproc=4, temperature=298.15, solvent=None,
        )

    assert "censo" in cmd[0]
    assert "--prescreening" in cmd
    assert "--screening" in cmd
    assert "--optimization" not in cmd
    assert "--refinement" not in cmd
    assert "--maxcores" in cmd
    assert "4" in cmd[cmd.index("--maxcores") + 1]
    assert "--gas-phase" in cmd
    assert "--evaluate-rrho" in cmd

    # charge/unpaired default to closed-shell singlet (0 unpaired electrons)
    assert "-c" in cmd
    assert cmd[cmd.index("-c") + 1] == "0"
    assert "-u" in cmd
    assert cmd[cmd.index("-u") + 1] == "0"


def test_build_cli_with_solvent(tmp_path: Path) -> None:
    config = _make_config()
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH  0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=Path("/usr/bin/censo")):
        interface = CensoInterface(config)
        preset = interface.resolve_preset("censo-default")
        cmd = interface.build_cli(
            input_xyz, rcfile, preset,
            nproc=8, temperature=298.15, solvent="dcm",
        )

    assert "--optimization" in cmd
    assert "--refinement" in cmd
    assert "--solvent" in cmd
    solv_idx = cmd.index("--solvent")
    assert cmd[solv_idx + 1] == "dcm"
    assert "--gas-phase" not in cmd


def test_build_cli_open_shell_charge_and_multiplicity(tmp_path: Path) -> None:
    # Regression: CENSO reads charge/unpaired ONLY from -c/-u CLI flags, never
    # from the rcfile [general] section. Without these flags an open-shell /
    # charged molecule is silently run as a neutral closed-shell singlet.
    config = _make_config()
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH  0 0 0\n")
    rcfile = tmp_path / "censo2rc"
    rcfile.write_text("")

    with patch("cccp.qc.interfaces.censo.resolve_executable", return_value=Path("/usr/bin/censo")):
        interface = CensoInterface(config)
        preset = interface.resolve_preset("censo-light")
        cmd = interface.build_cli(
            input_xyz, rcfile, preset,
            nproc=4, temperature=298.15, solvent=None,
            charge=2, multiplicity=5,
        )

    assert "-c" in cmd
    assert cmd[cmd.index("-c") + 1] == "2"
    assert "-u" in cmd
    # multiplicity 5 -> 4 unpaired electrons
    assert cmd[cmd.index("-u") + 1] == "4"


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def test_parse_censo_json(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)

    json_path = tmp_path / "1_SCREENING.json"
    xyz_path = tmp_path / "1_SCREENING.xyz"

    xyz_content = (
        "3\nCONF1\n"
        "C    0.000000    0.000000    0.000000\n"
        "H    0.000000    0.000000    1.089000\n"
        "H    1.026719    0.000000   -0.362999\n"
        "3\nCONF2\n"
        "C    0.000000    0.000000    0.000000\n"
        "H    0.000000    0.000000    1.089000\n"
        "H   -1.026719    0.000000   -0.362999\n"
    )
    xyz_path.write_text(xyz_content)

    json_data = {
        "part_name": "screening",
        "data": {
            "CONF1": {
                "energy": -154.912345,
                "gsolv": -0.004521,
                "grrho": 0.082341,
                "gtot": -154.834525,
            },
            "CONF2": {
                "energy": -154.911876,
                "gsolv": -0.004612,
                "grrho": 0.082455,
                "gtot": -154.834033,
            },
        },
    }
    json_path.write_text(json.dumps(json_data, indent=2))

    records = interface.parse_censo_json(json_path, xyz_path)
    assert len(records) == 2

    r1 = records[0]
    assert r1.conf_id == "CONF1"
    assert r1.frame_index == 0
    assert r1.energy == pytest.approx(-154.912345)
    assert r1.gtot == pytest.approx(-154.834525)
    assert r1.coordinates.shape == (3, 3)

    r2 = records[1]
    assert r2.conf_id == "CONF2"
    assert r2.frame_index == 1


def test_parse_censo_json_gtot_equality(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)

    json_path = tmp_path / "1_SCREENING.json"
    xyz_path = tmp_path / "1_SCREENING.xyz"
    xyz_path.write_text("3\nC\nH 0 0 0\nH 0 0 0\nH 0 0 0\n")

    json_data = {
        "part_name": "screening",
        "data": {
            "CONF1": {
                "energy": -154.912345,
                "gsolv": -0.004521,
                "grrho": 0.082341,
                "gtot": -154.834525,
            },
        },
    }
    json_path.write_text(json.dumps(json_data))

    records = interface.parse_censo_json(json_path, xyz_path)
    assert len(records) == 1
    r1 = records[0]
    computed = r1.energy + r1.gsolv + r1.grrho
    assert abs(computed - r1.gtot) < 1e-6


def test_parse_censo_json_missing_xyz(tmp_path: Path) -> None:
    config = _make_config()
    interface = CensoInterface(config)
    json_path = tmp_path / "1_SCREENING.json"
    xyz_path = tmp_path / "1_SCREENING.xyz"

    json_path.write_text('{"data": {"CONF1": {"energy": 0.0, "gsolv": 0.0, "grrho": 0.0, "gtot": 0.0}}}')

    with pytest.raises(CensoParseError, match="XYZ not found"):
        interface.parse_censo_json(json_path, xyz_path)


# ---------------------------------------------------------------------------
# CensoRunResult / Boltzmann weights
# ---------------------------------------------------------------------------


def test_boltzmann_weights_single_conf() -> None:
    record = CensoConformerRecord(
        conf_id="CONF1", frame_index=0,
        energy=-154.9, gsolv=0.0, grrho=0.0, gtot=-154.9,
        coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
    )
    result = CensoRunResult(
        preset="censo-light",
        records=[record],
        final_part="screening",
        temperature=298.15,
    )
    weights = result.boltzmann_weights()
    assert "CONF1" in weights
    assert weights["CONF1"] == pytest.approx(1.0, abs=1e-10)


def test_boltzmann_weights_two_confs() -> None:
    r1 = CensoConformerRecord(
        conf_id="CONF1", frame_index=0,
        energy=-154.9, gsolv=0.0, grrho=0.0, gtot=-154.9,
        coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
    )
    r2 = CensoConformerRecord(
        conf_id="CONF2", frame_index=1,
        energy=-154.8, gsolv=0.0, grrho=0.0, gtot=-154.8,
        coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
    )
    result = CensoRunResult(
        preset="censo-light",
        records=[r1, r2],
        final_part="screening",
        temperature=298.15,
    )
    weights = result.boltzmann_weights()
    assert "CONF1" in weights
    assert "CONF2" in weights
    assert weights["CONF1"] > weights["CONF2"]
    assert abs(sum(weights.values()) - 1.0) < 1e-10


def test_run_result_sort_by_gtot() -> None:
    r1 = CensoConformerRecord(
        conf_id="B", frame_index=1,
        energy=0.0, gsolv=0.0, grrho=0.0, gtot=-10.0,
        coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
    )
    r2 = CensoConformerRecord(
        conf_id="A", frame_index=0,
        energy=0.0, gsolv=0.0, grrho=0.0, gtot=-20.0,
        coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
    )
    result = CensoRunResult(preset="test", records=[r1, r2])
    result.sort_by_gtot()
    assert result.records[0].conf_id == "A"
    assert result.records[1].conf_id == "B"


# ---------------------------------------------------------------------------
# refine_ensemble — mocked subprocess
# ---------------------------------------------------------------------------


def test_refine_ensemble_success(tmp_path: Path) -> None:
    input_xyz = tmp_path / "crest_conformers.xyz"
    input_xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nFrame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    mock_script = _make_mock_censo_script(tmp_path, exit_code=0)

    config = _make_config(**{"executables": {"censo": {"path": str(mock_script)},
                                              "orca": {"path": "orca"},
                                              "xtb": {"path": "xtb"}},
                              "resources": {"nproc": 4},
                              "censo": {"preset": "censo-light", "temperature": 298.15}})
    backend = CensoBackend(config)

    with patch("shutil.which", return_value=str(mock_script)):
        result = backend.refine_ensemble(
            input_xyz, tmp_path / "censo",
            preset="censo-light", charge=0, multiplicity=1,
        )

    assert result.final_part == "screening"
    assert len(result.records) == 2
    assert result.records[0].conf_id == "CONF1"
    assert result.records[1].conf_id == "CONF2"


def test_refine_ensemble_censo_not_available(tmp_path: Path) -> None:
    config = _make_config()
    backend = CensoBackend(config)
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")

    with patch("shutil.which", return_value=None):
        with pytest.raises(CensoNotAvailableError):
            backend.refine_ensemble(input_xyz, tmp_path)


def test_refine_ensemble_censo_execution_error(tmp_path: Path) -> None:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n")

    mock_script = _make_censo_script_with_crash(tmp_path)

    config = _make_config(**{"executables": {"censo": {"path": str(mock_script)},
                                              "orca": {"path": "orca"},
                                              "xtb": {"path": "xtb"}},
                              "resources": {"nproc": 4}})
    backend = CensoBackend(config)

    with patch("shutil.which", return_value=str(mock_script)):
        with pytest.raises(CensoExecutionError, match="CENSO exited with code 1"):
            backend.refine_ensemble(input_xyz, tmp_path / "censo_crash")


def test_refine_ensemble_missing_input(tmp_path: Path) -> None:
    config = _make_config()
    backend = CensoBackend(config)
    with pytest.raises(FileNotFoundError):
        backend.refine_ensemble(
            tmp_path / "nonexistent.xyz",
            tmp_path,
        )


# ---------------------------------------------------------------------------
# search() — ConformerSearcher protocol
# ---------------------------------------------------------------------------


def test_search_returns_xyz_path(tmp_path: Path) -> None:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(
        "3\nFrame 0\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nFrame 1\nC 0 0 0\nH 0 0 1.089\nH -1.027 0 -0.363\n"
    )

    mock_script = _make_mock_censo_script(tmp_path, exit_code=0)

    config = _make_config(**{"executables": {"censo": {"path": str(mock_script)},
                                              "orca": {"path": "orca"},
                                              "xtb": {"path": "xtb"}},
                              "resources": {"nproc": 4},
                              "censo": {"preset": "censo-light", "temperature": 298.15}})
    backend = CensoBackend(config)

    with patch("shutil.which", return_value=str(mock_script)):
        result_xyz = backend.search(input_xyz, output_dir=tmp_path / "search_out")

    assert result_xyz.exists()
    assert "SCREENING" in result_xyz.name


# ---------------------------------------------------------------------------
# CensoConformerRecord validation
# ---------------------------------------------------------------------------


def test_conformer_record_validates_finite_values() -> None:
    with pytest.raises(ValueError, match="Non-finite energy"):
        CensoConformerRecord(
            conf_id="CONF1", frame_index=0,
            energy=float("nan"), gsolv=0.0, grrho=0.0, gtot=0.0,
            coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
        )

    with pytest.raises(ValueError, match="Non-finite gtot"):
        CensoConformerRecord(
            conf_id="CONF1", frame_index=0,
            energy=0.0, gsolv=0.0, grrho=0.0, gtot=float("inf"),
            coordinates=np.zeros((3, 3)), symbols=["C", "H", "H"],
        )


# ---------------------------------------------------------------------------
# Capability matrix integration
# ---------------------------------------------------------------------------


def test_capability_matrix_includes_censo() -> None:
    caps = list_capabilities("censo")
    assert caps["conformer_search"] is CAPABILITY_MATRIX["censo"]["conformer_search"]


# ---------------------------------------------------------------------------
# Regression test: non-sequential conformer IDs
# ---------------------------------------------------------------------------


def _make_mock_censo_script_nonsequential(tmp_path: Path) -> Path:
    """Create a mock CENSO executable with non-sequential conformer IDs.

    Simulates CENSO's real behavior where some conformers are filtered out
    during prescreening/screening, leaving gaps in the numbering (e.g.,
    CONF1, CONF3, CONF7 — CONF2, CONF4-6 were discarded).
    """
    xyz_content = "\n".join([
        "3",
        "CONF1",
        "C    0.000000    0.000000    0.000000",
        "H    0.000000    0.000000    1.089000",
        "H    1.026719    0.000000   -0.362999",
        "3",
        "CONF3",
        "C    0.100000    0.000000    0.000000",
        "H    0.100000    0.000000    1.089000",
        "H    1.126719    0.000000   -0.362999",
        "3",
        "CONF7",
        "C    0.200000    0.000000    0.000000",
        "H    0.200000    0.000000    1.089000",
        "H    1.226719    0.000000   -0.362999",
    ])

    json_data = {
        "part_name": "screening",
        "settings": {"prog": "orca", "func": "b97-3c", "threshold": 6.0},
        "data": {
            "CONF1": {
                "energy": -154.912345,
                "gsolv": -0.004521,
                "grrho": 0.082341,
                "gtot": -154.834525,
            },
            "CONF3": {
                "energy": -154.911876,
                "gsolv": -0.004612,
                "grrho": 0.082455,
                "gtot": -154.834033,
            },
            "CONF7": {
                "energy": -154.910500,
                "gsolv": -0.004700,
                "grrho": 0.082500,
                "gtot": -154.832700,
            },
        },
    }

    out_content = (
        "  Conf    E(DFT)        ΔGsolv        GmRRHO        Gtot"
        "        ΔGtot      Boltzmann weight\n"
        " CONF1  -154.912345   -0.004521     0.082341    -154.834525"
        "     0.000000      0.500000\n"
        " CONF3  -154.911876   -0.004612     0.082455    -154.834033"
        "     0.000492      0.300000\n"
        " CONF7  -154.910500   -0.004700     0.082500    -154.832700"
        "     0.001825      0.200000\n"
    )

    json_str = json.dumps(json_data, indent=2)

    script = tmp_path / "mock_censo_nonseq.sh"
    lines = [
        "#!/bin/bash",
        'echo "Mock CENSO (non-sequential) called with: $@" >&2',
        'cat > "1_SCREENING.json" << JSONEOF',
        json_str,
        "JSONEOF",
        'cat > "1_SCREENING.xyz" << XYZEOF',
        xyz_content,
        "XYZEOF",
        'cat > "1_SCREENING.out" << OUTEOF',
        out_content,
        "OUTEOF",
        "exit 0",
    ]
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return script


def test_refine_ensemble_nonsequential_ids(tmp_path: Path) -> None:
    """Regression test: CENSO output with non-sequential conformer IDs.

    CENSO preserves original conformer numbering from the CREST input ensemble.
    When some conformers are filtered out during screening, gaps appear in the
    numbering (e.g., CONF1, CONF3, CONF7 — CONF2/4/5/6 were discarded).

    This test verifies that:
    1. Validation passes (JSON keys match XYZ frame titles)
    2. Parsing correctly maps each conf_id to its frame position
    3. Coordinates are extracted from the correct frames
    """
    input_xyz = tmp_path / "crest_conformers.xyz"
    input_xyz.write_text(
        "3\nCONF1\nC 0 0 0\nH 0 0 1.089\nH 1.027 0 -0.363\n"
        "3\nCONF2\nC 0.05 0 0\nH 0.05 0 1.089\nH 1.077 0 -0.363\n"
        "3\nCONF3\nC 0.1 0 0\nH 0.1 0 1.089\nH 1.127 0 -0.363\n"
    )

    mock_script = _make_mock_censo_script_nonsequential(tmp_path)

    config = _make_config(**{
        "executables": {
            "censo": {"path": str(mock_script)},
            "orca": {"path": "orca"},
            "xtb": {"path": "xtb"},
        },
        "resources": {"nproc": 4},
        "censo": {"preset": "censo-light", "temperature": 298.15},
    })
    backend = CensoBackend(config)

    with patch("shutil.which", return_value=str(mock_script)):
        result = backend.refine_ensemble(
            input_xyz, tmp_path / "censo",
            preset="censo-light", charge=0, multiplicity=1,
        )

    assert result.final_part == "screening"
    assert len(result.records) == 3

    conf_ids = [r.conf_id for r in result.records]
    assert "CONF1" in conf_ids
    assert "CONF3" in conf_ids
    assert "CONF7" in conf_ids

    rec1 = next(r for r in result.records if r.conf_id == "CONF1")
    rec3 = next(r for r in result.records if r.conf_id == "CONF3")
    rec7 = next(r for r in result.records if r.conf_id == "CONF7")

    assert rec1.frame_index == 0
    assert rec3.frame_index == 1
    assert rec7.frame_index == 2

    assert rec1.coordinates.shape == (3, 3)
    assert rec3.coordinates.shape == (3, 3)
    assert rec7.coordinates.shape == (3, 3)

    assert abs(rec1.coordinates[0, 0] - 0.0) < 1e-6
    assert abs(rec3.coordinates[0, 0] - 0.1) < 1e-6
    assert abs(rec7.coordinates[0, 0] - 0.2) < 1e-6
