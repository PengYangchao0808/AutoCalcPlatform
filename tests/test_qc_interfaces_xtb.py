"""Tests for the xTB legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from conformer_search.qc.interfaces.crest import XTBInterface
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


def test_xtb_optimize_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = XTBInterface(sample_config)
    output_file = tmp_path / "xtb_output.xyz"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = output_file.write_text(XTB_OUTPUT_XYZ, encoding="utf-8")
        return subprocess.CompletedProcess(
            args="xtb",
            returncode=0,
            stdout=XTB_STDOUT,
            stderr="",
        )

    with patch(
        "conformer_search.qc.interfaces.crest.subprocess.run",
        side_effect=fake_run,
    ) as mock_run:
        result = interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    assert result.success is True
    assert result.energy is not None
    assert result.coordinates is not None
    assert result.energy == pytest.approx(-7.654321)
    np.testing.assert_allclose(result.coordinates, np.array([[0.0, 0.0, 0.3]]))
    assert result.symbols == SYMBOLS
    assert result.output_file == output_file
    assert result.log_file == tmp_path / "xtb.log"
    mock_run.assert_called_once()


@pytest.mark.slow
@pytest.mark.integration
@requires_xtb
def test_xtb_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = XTBInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None
