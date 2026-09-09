from __future__ import annotations

from unittest.mock import patch

from acp.intake import (
    detect_and_parse,
    detect_format,
    parse_qc_output_text,
    parse_smiles_list,
)

GAUSSIAN_OUTPUT = """Gaussian 16 Rev. C.01
 Charge = -1 Multiplicity = 2
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
       1          8           0        0.000000    0.000000    0.117000
       2          1           0        0.757000    0.000000   -0.469000
       3          1           0       -0.757000    0.000000   -0.469000
 ---------------------------------------------------------------------
 Rotational constants (GHZ): 1.0 2.0 3.0
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
       1          8           0        0.000000    0.000000    0.217000
       2          1           0        0.857000    0.000000   -0.369000
       3          1           0       -0.857000    0.000000   -0.369000
 ---------------------------------------------------------------------
 Rotational constants (GHZ): 4.0 5.0 6.0
"""

ORCA_OUTPUT = """Program Version 6.0.0 ORCA
 Total Charge        1
 Multiplicity        2
 CARTESIAN COORDINATES (ANGSTROEM)
 -------------------
 O      0.000000    0.000000    0.117000
 H      0.757000    0.000000   -0.469000
 H     -0.757000    0.000000   -0.469000
 -------------------
 CARTESIAN COORDINATES (ANGSTROEM)
 -------------------
 O      0.000000    0.000000    0.217000
 H      0.857000    0.000000   -0.369000
 H     -0.857000    0.000000   -0.369000
 -------------------
"""

ORCA_OUTPUT_WITHOUT_CHARGE = ORCA_OUTPUT.replace(" Total Charge        1\n", "").replace(
    " Multiplicity        2\n", ""
)


def test_qc_output_is_detected_before_xyz() -> None:
    # Given: QC output contains a geometry section and atom-like lines.
    # When: the content detector evaluates the output.
    # Then: it identifies the unified log format before XYZ.
    assert detect_format("", GAUSSIAN_OUTPUT) == "log"
    assert detect_format("", ORCA_OUTPUT) == "log"


def test_log_and_out_extensions_map_to_log() -> None:
    # Given: a valid QC output supplied under either supported output suffix.
    # When: the extension fast path detects its format.
    # Then: both suffixes use the single log format key.
    assert detect_format("calculation.log", GAUSSIAN_OUTPUT) == "log"
    assert detect_format("calculation.out", ORCA_OUTPUT) == "log"


def test_gaussian_output_builds_asset_from_last_geometry() -> None:
    # Given: Gaussian output with two standard-orientation geometries.
    # When: the unified QC parser consumes the text.
    # Then: the asset uses the last geometry and parsed molecular metadata.
    result = parse_qc_output_text(GAUSSIAN_OUTPUT, "water.log")

    assert result.ok is True
    asset = result.structures[0]
    assert asset.original_format == "log"
    assert asset.atom_count == 3
    assert asset.formula == "H2O"
    assert asset.charge == -1
    assert asset.multiplicity == 2
    assert asset.xyz is not None
    assert "0.217000" in asset.xyz
    assert "0.117000" not in asset.xyz
    assert asset.warnings == []


def test_orca_output_builds_asset_from_last_geometry() -> None:
    # Given: ORCA output with two Cartesian geometries.
    # When: auto detection and parsing run end to end.
    # Then: the returned asset has the last frame and ORCA charge state.
    detected_format, result = detect_and_parse(ORCA_OUTPUT, "water.out")

    assert detected_format == "log"
    assert result.ok is True
    asset = result.structures[0]
    assert asset.original_format == "log"
    assert asset.atom_count == 3
    assert asset.formula == "H2O"
    assert asset.charge == 1
    assert asset.multiplicity == 2
    assert asset.xyz is not None
    assert "0.217000" in asset.xyz
    assert "0.117000" not in asset.xyz


def test_qc_output_defaults_charge_multiplicity_with_warning() -> None:
    # Given: QC output with geometry but no charge or multiplicity markers.
    # When: the parser creates a structure asset.
    # Then: it applies the schema defaults and records a warning.
    result = parse_qc_output_text(ORCA_OUTPUT_WITHOUT_CHARGE, "water.out")

    assert result.ok is True
    asset = result.structures[0]
    assert (asset.charge, asset.multiplicity) == (0, 1)
    assert asset.warnings
    assert any("default" in warning.lower() for warning in asset.warnings)


def test_qc_output_without_geometry_reports_error() -> None:
    # Given: text with QC branding but no geometry section.
    # When: the parser attempts extraction.
    # Then: it returns a failed parse result with a coordinate error.
    result = parse_qc_output_text("Gaussian 16 Rev. C.01\nSCF Done: no geometry\n", "bad.log")

    assert result.ok is False
    assert result.structures == []
    assert any("coordinate" in error.lower() for error in result.errors)


# ---------------------------------------------------------------------------
# S3 hardening: SMILES fallback bounds + RuntimeError isolation
# ---------------------------------------------------------------------------


def test_smiles_list_rejects_oversized_content() -> None:
    # Given: content that exceeds the 100KB SMILES size limit.
    # When: parse_smiles_list is called.
    # Then: it returns a controlled error without importing RDKit.
    huge = "C" * 200_000
    result = parse_smiles_list(huge)

    assert result.ok is False
    assert result.structures == []
    assert any("too large" in e.lower() for e in result.errors)


def test_smiles_list_rejects_too_many_lines() -> None:
    # Given: content with more than 500 SMILES lines.
    # When: parse_smiles_list is called.
    # Then: it returns a controlled error without importing RDKit.
    many_lines = "\n".join(["CCO"] * 600)
    result = parse_smiles_list(many_lines)

    assert result.ok is False
    assert result.structures == []
    assert any("too many lines" in e.lower() for e in result.errors)


def test_smiles_list_rdkit_runtime_error_is_caught() -> None:
    # Given: RDKit raises RuntimeError for a malformed SMILES.
    # When: parse_smiles_list processes the row.
    # Then: the error is caught and reported per-row, not leaked as 500.
    with patch("rdkit.Chem.MolFromSmiles", side_effect=RuntimeError("corrupted SMILES")):
        result = parse_smiles_list("CCO\ninvalid\nCC")

    assert result.ok is False
    assert result.structures == []
    assert any("RDKit error" in e for e in result.errors)
    assert all("RuntimeError" not in e for e in result.errors)


def test_smiles_list_valid_input_still_works() -> None:
    # Given: a normal SMILES list.
    # When: parse_smiles_list is called.
    # Then: all valid SMILES are parsed successfully.
    result = parse_smiles_list("CCO\nCC")

    assert result.ok is True
    assert len(result.structures) == 2
    assert result.structures[0].formula == "C2H6O"
    assert result.structures[1].formula == "C2H6"


def test_qc_output_still_parsed_before_smiles_fallback() -> None:
    # Given: valid QC output content.
    # When: detect_and_parse runs.
    # Then: it uses the log parser, not the SMILES fallback.
    detected_format, result = detect_and_parse(GAUSSIAN_OUTPUT, "water.log")

    assert detected_format == "log"
    assert result.ok is True
    assert result.structures[0].original_format == "log"
