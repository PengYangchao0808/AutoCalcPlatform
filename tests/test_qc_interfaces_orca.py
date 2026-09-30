"""Tests for the ORCA legacy QC interface."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from cccp.qc import keyword_registry
from cccp.qc.interfaces.orca import (
    NmrShieldingParser,
    ORCAInterface,
    _is_orca_gfn_xtb_method,
    _parse_frequencies,
)
from cccp.qc.keyword_registry import KeywordValueError
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


@pytest.mark.parametrize(
    "method,expected",
    [
        ("GFN2-xTB", True),
        ("GFN1-xTB", True),
        ("GFN0-xTB", True),
        ("GFN-FF", True),
        ("GFNFF", True),
        ("Native-GFN2-xTB", True),
        ("gfn2-xtb", True),
        ("  GFN-FF ", True),
        ("  native-gfn-ff ", True),
        ("B97-3c", False),
        ("r2SCAN-3c", False),
        ("PBE0", False),
        ("", False),
        (None, False),
    ],
)
def test_is_orca_gfn_xtb_method_registry_driven(method: str | None, expected: bool) -> None:
    """T3: GFN-FF/GFN0-xTB/GFN2-xTB classify via the keyword registry (case/space-insensitive)."""
    assert _is_orca_gfn_xtb_method(method) is expected


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


# ── T7: GFN solvent semantics (ALPB-only under ORCA) ───────────────────────
#
# The historical %cpcm-for-GFN path is deleted: GFN solvation rides the
# ALPB(<solvent>) route token (PLATFORM POLICY, decision Q1 {none, ALPB});
# GBSA/CPCM/SMD raise KeywordValueError and are never emitted. DFT solvent
# emission (the %cpcm block above) stays byte-identical.


def test_orca_build_input_blocks_gfn_alpb_solvent_emits_route_token_not_cpcm(
    sample_config: dict[str, object],
) -> None:
    interface = ORCAInterface(
        sample_config, method="GFN2-xTB", solvent="water", solvent_model="ALPB"
    )
    blocks, _ = interface._build_input_blocks("sp")
    route = blocks.splitlines()[0]
    assert "ALPB(Water)" in route.split()
    assert "%cpcm" not in blocks and "SMDsolvent" not in blocks


def test_orca_build_input_blocks_gfn_solvent_model_none_emits_nothing(
    sample_config: dict[str, object],
) -> None:
    interface = ORCAInterface(
        sample_config, method="GFN2-xTB", solvent="water", solvent_model="none"
    )
    blocks, _ = interface._build_input_blocks("sp")
    assert "ALPB" not in blocks
    assert "Water" not in blocks and "%cpcm" not in blocks


def test_orca_build_input_blocks_gfn_gbsa_rejected(
    sample_config: dict[str, object],
) -> None:
    interface = ORCAInterface(
        sample_config, method="GFN2-xTB", solvent="water", solvent_model="gbsa"
    )
    with pytest.raises(KeywordValueError):
        interface._build_input_blocks("sp")


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


ORCA_ERROR_TERMINATION_OUTPUT = """Working dir.: /tmp/frame_1_(TS)_irc/ORCA
STDERR:
sh: 1: Syntax error: "(" unexpected

ORCA finished by error termination in Startup
Calling Command: /opt/orca/orca_startup /tmp/frame_1_(TS)_irc/ORCA/irc.int.tmp
[file orca_tools/qcmsg.cpp, line 394]:
  .... aborting the run
"""


def test_run_orca_treats_error_termination_as_failure(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    completed = subprocess.CompletedProcess(
        args=["orca", "irc.inp"],
        returncode=0,
        stdout=ORCA_ERROR_TERMINATION_OUTPUT,
        stderr='sh: 1: Syntax error: "(" unexpected\n',
    )
    with (
        patch("cccp.qc.interfaces.orca.subprocess.run", return_value=completed),
        patch("cccp.qc.interfaces.orca.resolve_executable", return_value=Path("/fake/orca")),
    ):
        interface = ORCAInterface(sample_config)
        ok = interface._run_orca(tmp_path / "irc.inp", tmp_path / "irc.out")

    assert ok is False


def test_run_orca_streaming_treats_error_termination_as_failure(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO(ORCA_ERROR_TERMINATION_OUTPUT)
            self.stderr = io.StringIO('sh: 1: Syntax error: "(" unexpected\n')

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

    with (
        patch("cccp.qc.interfaces.orca.subprocess.Popen", return_value=_FakeProcess()),
        patch("cccp.qc.interfaces.orca.resolve_executable", return_value=Path("/fake/orca")),
    ):
        interface = ORCAInterface(sample_config)
        ok = interface._run_orca(
            tmp_path / "irc.inp",
            tmp_path / "irc.out",
            output_callback=lambda _line: None,
        )

    assert ok is False


def test_run_orca_zero_exit_with_normal_termination_succeeds(
    sample_config: dict[str, object], tmp_path: Path
) -> None:
    completed = subprocess.CompletedProcess(
        args=["orca", "opt.inp"],
        returncode=0,
        stdout="FINAL SINGLE POINT ENERGY      -1.0\n****ORCA TERMINATED NORMALLY****\n",
        stderr="",
    )
    with (
        patch("cccp.qc.interfaces.orca.subprocess.run", return_value=completed),
        patch("cccp.qc.interfaces.orca.resolve_executable", return_value=Path("/fake/orca")),
    ):
        interface = ORCAInterface(sample_config)
        ok = interface._run_orca(tmp_path / "opt.inp", tmp_path / "opt.out")

    assert ok is True


# ── T8: GFN NMR default reject + reserved allow switch ─────────────────────
#
# The historical implicit ``6-311G(d)`` fill for GFN is removed. GFN+NMR is
# rejected by default through the registry calculation policy (T22 case 11:
# rc=0 but ZERO parsed shielding tensors — not artifact-level evidence). The
# module-level ``GFN_NMR_DEFAULT_ALLOWED`` switch opens the path; when open
# the GFN NMR solvent route is ALPB-only. DFT NMR emission is unchanged.


def _bare_nmr_interface(method: str, basis: str = "") -> ORCAInterface:
    interface = ORCAInterface.__new__(ORCAInterface)
    interface.method = method
    interface.basis = basis
    interface.solvent = None
    interface.solvent_model = "none"
    interface.maxcore = 1000
    interface.nproc = 1
    return interface


def test_nmr_gfn_rejected_by_default_with_actionable_message(tmp_path: Path) -> None:
    interface = _bare_nmr_interface("GFN2-xTB")
    with pytest.raises(KeywordValueError) as exc:
        interface._write_nmr_input(tmp_path / "nmr.inp", COORDINATES, SYMBOLS, 0, 1)
    message = str(exc.value)
    assert "NMR 仅支持 DFT/复合方法" in message
    assert "GFN2-xTB" in message
    # The policy gate runs before any write — no half-written input exists.
    assert not (tmp_path / "nmr.inp").exists()


def test_nmr_dft_implicit_basis_unchanged(tmp_path: Path) -> None:
    interface = _bare_nmr_interface("mPW1PW91")
    interface._write_nmr_input(tmp_path / "nmr.inp", COORDINATES, SYMBOLS, 0, 1)
    lines = (tmp_path / "nmr.inp").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "! mPW1PW91 6-311G(d) TightSCF"
    assert lines[1] == "%eprnmr"


def test_nmr_gfn_allow_switch_no_implicit_basis_and_alpb_solvent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keyword_registry, "GFN_NMR_DEFAULT_ALLOWED", True)
    interface = _bare_nmr_interface("GFN2-xTB")
    interface._write_nmr_input(
        tmp_path / "nmr.inp",
        COORDINATES,
        SYMBOLS,
        0,
        1,
        solvent="water",
        solvent_model="ALPB",
    )
    text = (tmp_path / "nmr.inp").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "! GFN2-xTB TightSCF"
    assert "6-311G(d)" not in text
    assert "! ALPB(Water)" in lines
    assert "CPCM" not in text and "SMD" not in text


def test_nmr_gfn_allow_switch_rejects_cpcm_smd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keyword_registry, "GFN_NMR_DEFAULT_ALLOWED", True)
    interface = _bare_nmr_interface("GFN2-xTB")
    for model in ("SMD", "CPCM"):
        with pytest.raises(KeywordValueError):
            interface._write_nmr_input(
                tmp_path / "nmr.inp",
                COORDINATES,
                SYMBOLS,
                0,
                1,
                solvent="water",
                solvent_model=model,
            )
    assert not (tmp_path / "nmr.inp").exists()

