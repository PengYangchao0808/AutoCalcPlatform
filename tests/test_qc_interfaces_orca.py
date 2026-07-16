"""Tests for the ORCA legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from conformer_search.qc.interfaces.orca import ORCAInterface
from tests.conftest import requires_orca

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

ORCA_OPT_OUTPUT = """FINAL SINGLE POINT ENERGY      -200.654321
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.2000000000
-------------------
"""

ORCA_NMR_OUTPUT = """FINAL SINGLE POINT ENERGY      -200.654321
CARTESIAN COORDINATES (ANGSTROEM)
-------------------
H      0.0000000000    0.0000000000    0.2000000000
-------------------

                       NMR SHIELDING TENSOR (PPM)

  Nucleus   1H:     isotropic=    28.9012   anisotropy=     2.3456
  XX=  30.0000   YX=   0.0000   ZX=   0.0000
  XY=   0.0000   YY=  27.0000   ZY=   0.0000
  XZ=   0.0000   YZ=   0.0000   ZZ=  29.0000

****ORCA-CHEMISTRY JOB DONE****
"""


def test_orca_interface_instantiates_with_minimal_config(
    sample_config: dict[str, object],
) -> None:
    interface = ORCAInterface(sample_config)

    assert interface.exe_path == Path("orca")
    assert interface.method == "M062X"
    assert interface.basis == "def2-TZVPP"
    assert interface.nproc == 1


def test_orca_optimize_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = ORCAInterface(sample_config)
    output_name = "orca_opt"

    completed = subprocess.CompletedProcess(
        args=["orca", "orca_opt.inp"],
        returncode=0,
        stdout=ORCA_OPT_OUTPUT,
        stderr="",
    )

    with patch(
        "conformer_search.qc.interfaces.orca.subprocess.run",
        return_value=completed,
    ) as mock_run:
        result = interface.optimize(
            COORDINATES,
            SYMBOLS,
            output_dir=tmp_path,
            output_name=output_name,
        )

    assert result.success is True
    assert result.converged is True
    assert result.energy is not None
    assert result.coordinates is not None
    assert result.energy == pytest.approx(-200.654321)
    np.testing.assert_allclose(result.coordinates, np.array([[0.0, 0.0, 0.2]]))
    assert result.symbols == SYMBOLS
    assert result.output_file == tmp_path / f"{output_name}.inp"
    assert result.log_file == tmp_path / f"{output_name}.out"
    mock_run.assert_called_once()


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
def test_orca_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = ORCAInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None


def test_orca_nmr_shielding_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = ORCAInterface(sample_config)
    output_name = "orca_nmr"

    completed = subprocess.CompletedProcess(
        args=["orca", "orca_nmr.inp"],
        returncode=0,
        stdout=ORCA_NMR_OUTPUT,
        stderr="",
    )

    with patch(
        "conformer_search.qc.interfaces.orca.subprocess.run",
        return_value=completed,
    ) as mock_run:
        result = interface.nmr_shielding(
            COORDINATES,
            SYMBOLS,
            output_dir=tmp_path,
            output_name=output_name,
            method="B3LYP",
            basis="def2-TZVP",
        )

    assert result.success is True
    assert result.converged is True
    assert result.energy is not None
    assert result.output_file == tmp_path / f"{output_name}.inp"
    assert result.log_file == tmp_path / f"{output_name}.out"
    mock_run.assert_called_once()
    input_text = result.output_file.read_text(encoding="utf-8")
    assert "NMR" in input_text
    assert "B3LYP" in input_text
    assert "def2-TZVP" in input_text
