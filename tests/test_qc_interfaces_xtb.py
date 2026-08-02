"""Tests for the xTB legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc.interfaces.xtb import XTBInterface
from tests.conftest import requires_xtb

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

XTB_OUTPUT_XYZ = """1
optimized
H 0.0000000000 0.0000000000 0.3000000000
"""

XTB_STDOUT = """summary line
| TOTAL ENERGY -7.654321
"""


def test_xtb_interface_instantiates_with_minimal_config(
    sample_config: dict[str, object],
) -> None:
    interface = XTBInterface(sample_config)

    assert interface.exe_path == Path("xtb")
    assert interface.gfn_level == 2
    assert interface.nproc == 1


def test_xtb_interface_nproc_never_zero_or_negative(
    sample_config: dict[str, object],
) -> None:
    """Invalid nproc values must fall back to 1 — OMP_NUM_THREADS=0 would
    otherwise let the BLAS runtime pick the whole node's threads."""
    interface = XTBInterface(sample_config, nproc=0)
    assert interface.nproc == 1

    interface = XTBInterface(sample_config, nproc=-4)
    assert interface.nproc == 1

    bad_config = {"executables": {"xtb": {"path": "xtb"}}, "resources": {"nproc": 0}}
    interface = XTBInterface(bad_config)
    assert interface.nproc == 1


def test_xtb_optimize_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = XTBInterface(sample_config)
    captured_env: dict[str, str] | None = None

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal captured_env
        captured_env = _kwargs.get("env")
        _ = (tmp_path / "xtbopt.xyz").write_text(XTB_OUTPUT_XYZ, encoding="utf-8")
        return subprocess.CompletedProcess(
            args="xtb",
            returncode=0,
            stdout=XTB_STDOUT,
            stderr="",
        )

    with patch(
        "cccp.qc.interfaces.xtb.subprocess.run",
        side_effect=fake_run,
    ) as mock_run:
        result = interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    assert result.success is True
    assert result.energy is not None
    assert result.coordinates is not None
    assert result.energy == pytest.approx(-7.654321)
    np.testing.assert_allclose(result.coordinates, np.array([[0.0, 0.0, 0.3]]))
    assert result.symbols == SYMBOLS
    assert result.output_file == tmp_path / "xtbopt.xyz"
    assert result.log_file == tmp_path / "xtb.log"
    # Thread env pinned to nproc (sample_config resources.nproc=1).
    assert captured_env is not None
    assert captured_env["OMP_NUM_THREADS"] == "1"
    assert captured_env["MKL_NUM_THREADS"] == "1"
    assert captured_env["OPENBLAS_NUM_THREADS"] == "1"
    mock_run.assert_called_once()


@pytest.mark.slow
@pytest.mark.integration
@requires_xtb
def test_xtb_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = XTBInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None
