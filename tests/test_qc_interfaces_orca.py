"""Tests for the ORCA legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc.interfaces.orca import NmrShieldingParser, ORCAInterface, _parse_frequencies
from tests.conftest import requires_orca

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

REAL_FREQ_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orca_optfreq_real_sections.txt"

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
    output_name = "orca_opt"

    completed = subprocess.CompletedProcess(
        args=["orca", "orca_opt.inp"],
        returncode=0,
        stdout=ORCA_OPT_OUTPUT,
        stderr="",
    )

    with (
        patch(
            "cccp.qc.interfaces.orca.subprocess.run",
            return_value=completed,
        ) as mock_run,
        patch(
            "cccp.qc.interfaces.orca.resolve_executable",
            return_value=Path("/fake/orca"),
        ),
    ):
        interface = ORCAInterface(sample_config)
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
    output_name = "orca_nmr"

    completed = subprocess.CompletedProcess(
        args=["orca", "orca_nmr.inp"],
        returncode=0,
        stdout=ORCA_NMR_OUTPUT,
        stderr="",
    )

    with (
        patch(
            "cccp.qc.interfaces.orca.subprocess.run",
            return_value=completed,
        ) as mock_run,
        patch(
            "cccp.qc.interfaces.orca.resolve_executable",
            return_value=Path("/fake/orca"),
        ),
    ):
        interface = ORCAInterface(sample_config)
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
    # %eprnmr block drives GIAO (not the simple NMR route keyword).
    assert "%eprnmr" in input_text
    assert "B3LYP" in input_text
    assert "def2-TZVP" in input_text
    assert "TightSCF" in input_text
    # Parsed shieldings ride on metadata (0-based atom index → descriptor).
    shieldings = result.metadata.get("shieldings")
    assert isinstance(shieldings, dict)
    assert 0 in shieldings
    assert shieldings[0]["symbol"] == "H"
    assert shieldings[0]["isotropic"] == pytest.approx(28.9012)
    assert shieldings[0]["anisotropy"] == pytest.approx(2.3456)
    # tensor components parsed from the XX=/YY=/ZZ= lines
    assert shieldings[0]["tensor_components"]["XX"] == pytest.approx(30.0000)
    assert shieldings[0]["tensor_components"]["ZZ"] == pytest.approx(29.0000)


def test_nmr_shielding_parser_handles_multi_atom_tensor_block(
    tmp_path: Path,
) -> None:
    """Multi-nucleus ORCA NMR output parses to 0-based indices + tensors."""
    log = tmp_path / "multi.out"
    log.write_text(
        """
Some preamble...

                       NMR SHIELDING TENSOR (PPM)

  Nucleus   1C:     isotropic=   140.5000   anisotropy=    10.0000
  XX= 145.0000   YX=   0.0000   ZX=   0.0000
  XY=   0.0000   YY= 138.0000   ZY=   0.0000
  XZ=   0.0000   YZ=   0.0000   ZZ= 138.5000

  Nucleus   2H:     isotropic=    30.1000   anisotropy=     1.5000
  XX=  31.0000   YX=   0.0000   ZX=   0.0000
  XY=   0.0000   YY=  29.5000   ZY=   0.0000
  XZ=   0.0000   YZ=   0.0000   ZZ=  29.8000

****ORCA-CHEMISTRY JOB DONE****
""",
        encoding="utf-8",
    )
    parsed = NmrShieldingParser.parse(log, expected_symbols=["C", "H"])
    assert set(parsed) == {0, 1}
    assert parsed[0]["symbol"] == "C"
    assert parsed[0]["isotropic"] == pytest.approx(140.5)
    assert parsed[1]["symbol"] == "H"
    assert parsed[1]["isotropic"] == pytest.approx(30.1)
    assert parsed[1]["tensor_components"]["YY"] == pytest.approx(29.5)


def test_nmr_shielding_parser_falls_back_to_summary_table(
    tmp_path: Path,
) -> None:
    """The compact CHEMICAL SHIELDING SUMMARY table is parsed when present.

    ORCA 5.x summary-table Nucleus column is 0-based (starts at 0), unlike
    the TENSOR block's 1-based ``Nucleus N El:`` labels. Real ORCA example
    (ORCA manual §9.10):
        Nucleus   Element   Isotropic(ppm)
           0         6 C       45.230
           1         1 H       28.453
    """
    log = tmp_path / "summary.out"
    log.write_text(
        """
--------------------
CHEMICAL SHIELDING SUMMARY (ppm)
--------------------
 Nucleus   Element   Isotropic(ppm)
   0         6 C       140.230
   1         1 H        30.453
--------------------
""",
        encoding="utf-8",
    )
    parsed = NmrShieldingParser.parse(log)
    assert parsed[0]["symbol"] == "C"
    assert parsed[0]["isotropic"] == pytest.approx(140.230)
    assert parsed[1]["symbol"] == "H"


def test_nmr_shielding_parser_summary_table_0based_validation(
    tmp_path: Path,
) -> None:
    """0-based summary table parses to contiguous 0..N-1 indices.

    This guards the exact off-by-one regression: an earlier version
    subtracted 1 (treating the 0-based ORCA summary as 1-based), which
    would map atom 0 → index -1 and fail _validate_symbols.
    """
    log = tmp_path / "summary0.out"
    log.write_text(
        """
CHEMICAL SHIELDING SUMMARY (ppm)
 Nucleus   Element   Isotropic(ppm)
   0         6 C       140.230
   1         1 H        30.453
""",
        encoding="utf-8",
    )
    parsed = NmrShieldingParser.parse(log, expected_symbols=["C", "H"])
    assert set(parsed) == {0, 1}
    assert parsed[0]["symbol"] == "C"
    assert parsed[0]["isotropic"] == pytest.approx(140.230)
    assert parsed[1]["symbol"] == "H"


def test_resolve_nmr_nuclei_unsupported_falls_back_to_molecule(
    sample_config: dict[str, object],
) -> None:
    """F3 fix: --nuclei with only unsupported elements must not produce a
    GIAO-less plain SP job. Falls back to the molecule's NMR-active elements
    with a warning."""
    interface = ORCAInterface(sample_config)
    resolved = interface._resolve_nmr_nuclei(["Si"], ["C", "H", "O"])
    assert resolved == ["C", "H"]
    # supported elements are preserved as-is (order kept, de-duplicated)
    assert interface._resolve_nmr_nuclei(["H", "C", "H"], ["C", "H"]) == ["H", "C"]
    # no explicit nuclei → molecule-derived
    assert interface._resolve_nmr_nuclei(None, ["H", "C"]) == ["H", "C"]
    # no active elements in molecule → empty (caller handles)
    assert interface._resolve_nmr_nuclei(None, ["Si", "Ge"]) == []


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

