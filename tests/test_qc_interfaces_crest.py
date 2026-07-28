"""Tests for the CREST legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc.interfaces.crest import CRESTInterface
from cccp.qc.interfaces.xtb import XTBInterface
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


@pytest.mark.slow
@pytest.mark.integration
@requires_crest
def test_crest_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = CRESTInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None


def test_crest_run_uses_alpb_with_solvent_model_alpb(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = CRESTInterface(sample_config, solvent="toluene", solvent_model="alpb")

    completed = subprocess.CompletedProcess(
        args=["crest", str(tmp_path / "crest_input.xyz")],
        returncode=0,
        stdout=CREST_ENSEMBLE,
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.crest.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.run_conformer_search(
            COORDINATES, SYMBOLS, output_dir=tmp_path, output_name="crest_ensemble"
        )

    args = mock_run.call_args[0][0]
    assert "--alpb" in args
    assert "toluene" in args
    assert "--gbsa" not in args


def test_crest_run_uses_gbsa_with_solvent_model_gbsa(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = CRESTInterface(sample_config, solvent="toluene", solvent_model="gbsa")

    completed = subprocess.CompletedProcess(
        args=["crest", str(tmp_path / "crest_input.xyz")],
        returncode=0,
        stdout=CREST_ENSEMBLE,
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.crest.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.run_conformer_search(
            COORDINATES, SYMBOLS, output_dir=tmp_path, output_name="crest_ensemble"
        )

    args = mock_run.call_args[0][0]
    assert "--gbsa" in args
    assert "toluene" in args
    assert "--alpb" not in args


def test_crest_run_no_solvent_with_solvent_model_none(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = CRESTInterface(sample_config, solvent="toluene", solvent_model="none")

    completed = subprocess.CompletedProcess(
        args=["crest", str(tmp_path / "crest_input.xyz")],
        returncode=0,
        stdout=CREST_ENSEMBLE,
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.crest.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.run_conformer_search(
            COORDINATES, SYMBOLS, output_dir=tmp_path, output_name="crest_ensemble"
        )

    args = mock_run.call_args[0][0]
    assert "--alpb" not in args
    assert "--gbsa" not in args


def test_xtb_optimize_uses_alpb_with_solvent_model_alpb(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = XTBInterface(sample_config, solvent="toluene", solvent_model="alpb")

    completed = subprocess.CompletedProcess(
        args=["xtb", str(tmp_path / "xtb_input.xyz")],
        returncode=0,
        stdout="TOTAL ENERGY -1.0\n",
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.xtb.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    args = mock_run.call_args[0][0]
    assert "--alpb" in args
    assert "toluene" in args
    assert "--gbsa" not in args


def test_xtb_optimize_uses_gbsa_with_solvent_model_gbsa(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = XTBInterface(sample_config, solvent="toluene", solvent_model="gbsa")

    completed = subprocess.CompletedProcess(
        args=["xtb", str(tmp_path / "xtb_input.xyz")],
        returncode=0,
        stdout="TOTAL ENERGY -1.0\n",
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.xtb.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    args = mock_run.call_args[0][0]
    assert "--gbsa" in args
    assert "toluene" in args
    assert "--alpb" not in args


def test_xtb_optimize_no_solvent_with_solvent_model_none(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    interface = XTBInterface(sample_config, solvent="toluene", solvent_model="none")

    completed = subprocess.CompletedProcess(
        args=["xtb", str(tmp_path / "xtb_input.xyz")],
        returncode=0,
        stdout="TOTAL ENERGY -1.0\n",
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.xtb.subprocess.run",
        return_value=completed,
    ) as mock_run:
        interface.optimize(COORDINATES, SYMBOLS, output_dir=tmp_path)

    args = mock_run.call_args[0][0]
    assert "--alpb" not in args
    assert "--gbsa" not in args
