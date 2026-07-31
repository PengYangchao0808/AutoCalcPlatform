"""Tests for the ORCA legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc.interfaces.orca import ORCAInterface, _parse_frequencies
from tests.conftest import requires_orca

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

REAL_FREQ_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "orca_optfreq_real_sections.txt"
)

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
        "cccp.qc.interfaces.orca.subprocess.run",
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
        "cccp.qc.interfaces.orca.subprocess.run",
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


def test_orca_build_input_blocks_with_smd(sample_config: dict[str, object]) -> None:
    interface = ORCAInterface(
        sample_config, method="wB97X-D4", basis="def2-TZVPP", solvent="toluene", solvent_model="smd"
    )
    blocks, _ = interface._build_input_blocks("sp")
    assert "%cpcm" in blocks
    assert "smd true" in blocks
    assert 'SMDsolvent "Toluene"' in blocks


def test_orca_build_input_blocks_with_cpcm(sample_config: dict[str, object]) -> None:
    interface = ORCAInterface(
        sample_config,
        method="wB97X-D4",
        basis="def2-TZVPP",
        solvent="toluene",
        solvent_model="cpcm",
    )
    blocks, _ = interface._build_input_blocks("sp")
    assert "%cpcm" in blocks
    assert "smd true" not in blocks
    assert 'SMDsolvent "Toluene"' in blocks


def test_orca_build_input_blocks_with_no_solvent(sample_config: dict[str, object]) -> None:
    interface = ORCAInterface(
        sample_config, method="wB97X-D4", basis="def2-TZVPP", solvent=None, solvent_model="none"
    )
    blocks, _ = interface._build_input_blocks("sp")
    assert "%cpcm" not in blocks
    assert "SMDsolvent" not in blocks


def test_orca_build_input_blocks_uppercase_solvent_model(sample_config: dict[str, object]) -> None:
    interface = ORCAInterface(
        sample_config, method="wB97X-D4", basis="def2-TZVPP", solvent="toluene", solvent_model="SMD"
    )
    blocks, _ = interface._build_input_blocks("sp")
    assert "smd true" in blocks


def test_parse_frequencies_real_orca_format_takes_last_section(
    tmp_path: Path,
) -> None:
    """Real ORCA 5.x format is ``N: value cm**-1`` — the parser must take
    the last VIBRATIONAL FREQUENCIES section only (intermediate Hessian
    steps carry imaginary modes) and drop the six zero T/R modes."""
    freq_file = tmp_path / "orca.out"
    freq_file.write_text(REAL_FREQ_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    frequencies = _parse_frequencies(freq_file)

    assert frequencies == [1615.84, 3795.36, 3896.58]


def test_parse_frequencies_real_section_with_imaginary_modes(
    tmp_path: Path,
) -> None:
    """Imaginary modes carry a ``***imaginary mode***`` suffix; zeros are
    filtered, negative values are preserved."""
    freq_file = tmp_path / "orca.out"
    freq_file.write_text(
        """VIBRATIONAL FREQUENCIES
-----------------------

Scaling factor for frequencies =  1.000000000  (already applied!)

   0:         0.00 cm**-1
   1:         0.00 cm**-1
   2:         0.00 cm**-1
   3:         0.00 cm**-1
   4:         0.00 cm**-1
   5:         0.00 cm**-1
   6:      -797.72 cm**-1 ***imaginary mode***
   7:      -791.36 cm**-1 ***imaginary mode***
   8:      1411.55 cm**-1
""",
        encoding="utf-8",
    )

    assert _parse_frequencies(freq_file) == [-797.72, -791.36, 1411.55]


def test_parse_frequencies_no_section_returns_empty(tmp_path: Path) -> None:
    freq_file = tmp_path / "orca.out"
    freq_file.write_text("FINAL SINGLE POINT ENERGY      -200.0\n", encoding="utf-8")

    assert _parse_frequencies(freq_file) == []


def test_parse_frequencies_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_frequencies(tmp_path / "does_not_exist.out") == []


def test_opt_freq_parses_real_output_into_qcresult(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    """End-to-end: mocked ORCA subprocess emitting real-format output must
    yield a QCResult carrying the final-section frequencies."""
    interface = ORCAInterface(sample_config)
    output_name = "orca_optfreq"

    completed = subprocess.CompletedProcess(
        args=["orca", f"{output_name}.inp"],
        returncode=0,
        stdout=ORCA_OPT_OUTPUT + "\n" + REAL_FREQ_FIXTURE.read_text(encoding="utf-8"),
        stderr="",
    )

    with patch(
        "cccp.qc.interfaces.orca.subprocess.run",
        return_value=completed,
    ) as mock_run:
        result = interface.opt_freq(
            COORDINATES,
            SYMBOLS,
            output_dir=tmp_path,
            output_name=output_name,
        )

    assert result.success is True
    assert result.frequencies == [1615.84, 3795.36, 3896.58]
    assert result.has_frequencies is True
    mock_run.assert_called_once()
