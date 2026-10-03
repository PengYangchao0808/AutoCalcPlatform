# ruff: noqa: E501
"""ORCA backend single-point gradient tests (work unit X4′-A).

Uses a FAKE ORCA binary + patched ``subprocess.run`` (pattern from
``tests/test_acp_workflows_xtb_path.py`` / ``tests/test_qc_interfaces_orca.py``);
no real ORCA is executed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.orca import (
    BOHR_ANGSTROM,
    ORCA_GRADIENT_CONVENTION,
    ORCA_GRADIENT_UNIT,
    ORCABackend,
    SinglePointGradientResult,
    _parse_cartesian_gradient_block,
    _parse_engrad_file,
)

SYMBOLS = ["H", "H"]
COORDS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]], dtype=np.float64)
ENERGY = -1.166
GRADIENT = np.array(
    [[0.01, 0.0, 0.0], [-0.01, 0.0, 0.0]],
    dtype=np.float64,
)

ENGRAAD_OK = """#
# Number of atoms
#
        2
#
# The current total energy in Eh
#
    -1.166000000000
#
# The current gradient in Eh/bohr
#
       0.010000000000
       0.000000000000
       0.000000000000
      -0.010000000000
       0.000000000000
       0.000000000000
#
# Geometry (Bohr)
#
        1    0.0000000000    0.0000000000    0.0000000000
        1    0.0000000000    0.0000000000    1.3986818850
"""

ENGRAAD_WRONG_ENERGY = ENGRAAD_OK.replace("-1.166000000000", "-9.999000000000")

STDOUT_OK = """Some ORCA banner
FINAL SINGLE POINT ENERGY      -1.166000000
---------------------------------
CARTESIAN GRADIENT
---------------------------------

   1   H   :    0.010000000    0.000000000    0.000000000
   2   H   :   -0.010000000    0.000000000    0.000000000

------------------------------
                             ****ORCA TERMINATED NORMALLY****
"""

STDOUT_ENERGY_ONLY = """Some ORCA banner
FINAL SINGLE POINT ENERGY      -1.166000000
                             ****ORCA TERMINATED NORMALLY****
"""


def _write_fake_orca(tmp_path: Path) -> Path:
    exe = tmp_path / "fake_orca"
    exe.write_text("#!/bin/sh\necho fake-orca\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _install_fake_orca_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = STDOUT_OK,
    engrad_text: str | None = ENGRAAD_OK,
    engrad_name: str = "grad.engrad",
) -> list[dict[str, Any]]:
    """Patch ``cccp.qc.interfaces.orca.subprocess.run``; record + write artifacts."""
    captured: list[dict[str, Any]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        input_file = Path(cmd[-1])
        cwd = Path(kwargs.get("cwd") or input_file.parent)
        captured.append({"cmd": list(cmd), "cwd": cwd, "input_file": input_file})
        if engrad_text is not None:
            (cwd / engrad_name).write_text(engrad_text, encoding="utf-8")
        return subprocess.CompletedProcess(args=list(cmd), returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("cccp.qc.interfaces.orca.subprocess.run", _fake_run)
    return captured


def _backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, Any],
) -> ORCABackend:
    fake_exe = _write_fake_orca(tmp_path)
    config = dict(sample_config)
    executables = dict(config.get("executables") or {})
    executables["orca"] = {"path": str(fake_exe)}
    config["executables"] = executables
    monkeypatch.setattr("cccp.qc.interfaces.orca.resolve_executable", lambda *a, **k: fake_exe)
    return ORCABackend(config)


# ---------------------------------------------------------------------------
# Parser units (real ORCA formats captured from ACP_runs outputs)
# ---------------------------------------------------------------------------


def test_parse_engrad_file_reads_native_layout(tmp_path: Path) -> None:
    path = tmp_path / "grad.engrad"
    path.write_text(ENGRAAD_OK, encoding="utf-8")
    parsed = _parse_engrad_file(path, 2)
    assert parsed is not None
    energy, gradient = parsed
    assert energy == pytest.approx(ENERGY)
    np.testing.assert_allclose(gradient, GRADIENT, atol=1e-12)


def test_parse_engrad_file_rejects_wrong_atom_count(tmp_path: Path) -> None:
    path = tmp_path / "grad.engrad"
    path.write_text(ENGRAAD_OK, encoding="utf-8")
    assert _parse_engrad_file(path, 3) is None


def test_parse_cartesian_gradient_block_parses_colon_rows() -> None:
    gradient = _parse_cartesian_gradient_block(STDOUT_OK, SYMBOLS)
    assert gradient is not None
    np.testing.assert_allclose(gradient, GRADIENT, atol=1e-9)


def test_parse_cartesian_gradient_block_missing_returns_none() -> None:
    assert _parse_cartesian_gradient_block(STDOUT_ENERGY_ONLY, SYMBOLS) is None


def test_parse_cartesian_gradient_block_symbol_mismatch_returns_none() -> None:
    assert _parse_cartesian_gradient_block(STDOUT_OK, ["H", "C"]) is None


# ---------------------------------------------------------------------------
# Backend delegation
# ---------------------------------------------------------------------------


def test_single_point_gradient_delegates_engrad_route_and_parses_engrad_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    captured = _install_fake_orca_run(monkeypatch)

    result = backend.single_point_gradient(
        COORDS,
        SYMBOLS,
        charge=0,
        multiplicity=1,
        output_dir=output_dir,
        method="GFN2-xTB",
        basis="",
    )

    assert isinstance(result, SinglePointGradientResult)
    assert result.success is True
    assert result.energy == pytest.approx(ENERGY)
    assert result.gradient is not None
    np.testing.assert_allclose(result.gradient, GRADIENT, atol=1e-9)
    assert result.gradient_unit == ORCA_GRADIENT_UNIT == "hartree/bohr"
    assert result.gradient_convention == ORCA_GRADIENT_CONVENTION == "energy_gradient_dE_dX"
    assert result.gradient_source == "engrad_file:grad.engrad"
    assert result.symbols == SYMBOLS
    assert result.metadata["charge"] == 0
    assert result.metadata["multiplicity"] == 1
    angstrom = np.asarray(result.metadata["gradient_hartree_per_angstrom"])
    np.testing.assert_allclose(angstrom, GRADIENT / BOHR_ANGSTROM, atol=1e-9)

    assert len(captured) == 1
    input_file = captured[0]["input_file"]
    assert input_file.is_file()
    route_line = next(
        line for line in input_file.read_text(encoding="utf-8").splitlines() if line.startswith("!")
    )
    assert "EnGrad" in route_line
    assert "GFN2-xTB" in route_line


def test_single_point_gradient_prepends_engrad_to_extra_route_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    captured = _install_fake_orca_run(monkeypatch)

    result = backend.single_point_gradient(
        COORDS,
        SYMBOLS,
        output_dir=output_dir,
        method="GFN2-xTB",
        basis="",
        route_extras=["VeryTightSCF"],
    )

    assert result.success is True
    assert result.metadata["route_extras"] == ["EnGrad", "VeryTightSCF"]
    route_line = next(
        line
        for line in captured[0]["input_file"].read_text(encoding="utf-8").splitlines()
        if line.startswith("!")
    )
    assert "EnGrad" in route_line
    assert "VeryTightSCF" in route_line


def test_single_point_gradient_falls_back_to_output_block_when_engrad_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    _install_fake_orca_run(monkeypatch, engrad_text=None)

    result = backend.single_point_gradient(
        COORDS, SYMBOLS, output_dir=output_dir, method="GFN2-xTB", basis=""
    )

    assert result.success is True
    assert result.gradient is not None
    np.testing.assert_allclose(result.gradient, GRADIENT, atol=1e-9)
    assert result.gradient_source == "output_block:CARTESIAN GRADIENT"


def test_single_point_gradient_rejects_engrad_energy_mismatch_and_falls_back_to_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    _install_fake_orca_run(monkeypatch, engrad_text=ENGRAAD_WRONG_ENERGY, engrad_name="grad.engrad")

    result = backend.single_point_gradient(
        COORDS, SYMBOLS, output_dir=output_dir, method="GFN2-xTB", basis=""
    )

    assert result.success is True
    assert result.gradient_source == "output_block:CARTESIAN GRADIENT"
    np.testing.assert_allclose(result.gradient, GRADIENT, atol=1e-9)


def test_single_point_gradient_typed_failure_when_engrad_mismatch_and_no_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    _install_fake_orca_run(
        monkeypatch,
        stdout=STDOUT_ENERGY_ONLY,
        engrad_text=ENGRAAD_WRONG_ENERGY,
        engrad_name="grad.engrad",
    )

    result = backend.single_point_gradient(
        COORDS, SYMBOLS, output_dir=output_dir, method="GFN2-xTB", basis=""
    )

    assert result.success is False
    assert result.gradient is None
    assert result.gradient_source is None
    assert result.error_message is not None
    assert "gradient missing" in result.error_message


def test_single_point_gradient_typed_failure_when_gradient_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"
    _install_fake_orca_run(monkeypatch, stdout=STDOUT_ENERGY_ONLY, engrad_text=None)

    result = backend.single_point_gradient(
        COORDS, SYMBOLS, output_dir=output_dir, method="GFN2-xTB", basis=""
    )

    assert result.success is False
    assert result.gradient is None
    assert result.energy == pytest.approx(ENERGY)
    assert result.error_message is not None
    assert "gradient missing" in result.error_message


def test_single_point_gradient_typed_failure_when_scf_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, Any]
) -> None:
    backend = _backend(tmp_path, monkeypatch, sample_config)
    output_dir = tmp_path / "run"

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=1, stdout="SCF FAILED\n", stderr=""
        )

    monkeypatch.setattr("cccp.qc.interfaces.orca.subprocess.run", _fake_run)

    result = backend.single_point_gradient(
        COORDS, SYMBOLS, output_dir=output_dir, method="GFN2-xTB", basis=""
    )

    assert result.success is False
    assert result.gradient is None
    assert result.energy is None
    assert result.error_message is not None
