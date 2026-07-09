"""Tests for the CREST legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from conformer_search.qc.interfaces.crest import CRESTInterface
from tests.conftest import requires_crest

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

CREST_ENSEMBLE = """1
conf-1
H 0.0000000000 0.0000000000 0.0000000000
1
conf-2
H 0.0000000000 0.0000000000 0.5000000000
"""


def test_crest_interface_instantiates_with_minimal_config(
    sample_config: dict[str, object],
) -> None:
    interface = CRESTInterface(sample_config)

    assert interface.exe_path == Path("crest")
    assert interface.xtb_path == Path("xtb")
    assert interface.gfn_level == 2
    assert interface.threads == 1


def test_crest_optimize_parses_mocked_run_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = CRESTInterface(sample_config)
    ensemble_xyz = tmp_path / "crest_conformers.xyz"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = ensemble_xyz.write_text(CREST_ENSEMBLE, encoding="utf-8")
        return subprocess.CompletedProcess(args="crest", returncode=0, stdout="done", stderr="")

    with patch(
        "conformer_search.qc.interfaces.crest.subprocess.run",
        side_effect=fake_run,
    ) as mock_run:
        result = interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    assert result.success is True
    assert result.coordinates is not None
    np.testing.assert_allclose(
        result.coordinates,
        np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.5]]),
    )
    assert result.symbols == SYMBOLS
    assert result.output_file == ensemble_xyz
    assert result.metadata["n_conformers"] == 2
    assert result.metadata["gfn_level"] == 2
    mock_run.assert_called_once()


@pytest.mark.slow
@pytest.mark.integration
@requires_crest
def test_crest_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = CRESTInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None
