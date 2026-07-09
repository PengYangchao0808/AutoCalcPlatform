"""Tests for the Gaussian legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from conformer_search.qc.interfaces.gaussian import GaussianInterface
from tests.conftest import requires_gaussian

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

GAUSSIAN_OPT_LOG = """ SCF Done:  E(RB3LYP) =  -100.123456789     A.U. after   10 cycles
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          1           0        0.0000000000    0.0000000000    0.1000000000
 ---------------------------------------------------------------------
 Rotational constants (GHZ):
"""


def test_gaussian_interface_instantiates_with_minimal_config(
    sample_config: dict[str, object],
) -> None:
    interface = GaussianInterface(sample_config)

    assert interface.exe_path == Path("g16")
    assert interface.method == "B3LYP"
    assert interface.basis == "def2-SVP"
    assert interface.nprocshared == 1
    assert interface.mem_str == "1GB"


def test_gaussian_optimize_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = GaussianInterface(sample_config)
    output_name = "gaussian_opt"
    log_file = tmp_path / f"{output_name}.log"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = log_file.write_text(GAUSSIAN_OPT_LOG, encoding="utf-8")
        return subprocess.CompletedProcess(args="g16", returncode=0, stdout="", stderr="")

    with patch(
        "conformer_search.qc.interfaces.gaussian.subprocess.run",
        side_effect=fake_run,
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
    assert result.energy == pytest.approx(-100.123456789)
    np.testing.assert_allclose(result.coordinates, np.array([[0.0, 0.0, 0.1]]))
    assert result.symbols == SYMBOLS
    assert result.output_file == tmp_path / f"{output_name}.gjf"
    assert result.log_file == log_file
    mock_run.assert_called_once()


@pytest.mark.slow
@pytest.mark.integration
@requires_gaussian
def test_gaussian_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = GaussianInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None
