# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from __future__ import annotations

from pathlib import Path

import numpy as np

from cccp.utils.geometry_tools import LogParser


GAUSSIAN_TEXT = """Gaussian 16 Rev. C.01
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        0.000000    0.000000    0.000000
      2          1           0        1.000000    0.000000    0.000000
 ---------------------------------------------------------------------
 Rotational constants (GHZ): 1.0 2.0 3.0
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        2.000000    0.000000    0.000000
      2          8           0        0.000000    2.000000    0.000000
 ---------------------------------------------------------------------
 Rotational constants (GHZ): 4.0 5.0 6.0
"""

ORCA_TEXT = """Program Version 6.0.0 ORCA
 CARTESIAN COORDINATES (ANGSTROEM)
 -------------------
 H      0.000000    0.000000    0.000000
 C      1.000000    0.000000    0.000000
 -------------------
 CARTESIAN COORDINATES (ANGSTROEM)
 -------------------
 O      2.000000    0.000000    0.000000
 N      0.000000    2.000000    0.000000
 -------------------
"""


def test_gaussian_text_matches_path_api(tmp_path: Path) -> None:
    log_file = tmp_path / "calculation.log"
    _ = log_file.write_text(GAUSSIAN_TEXT, encoding="utf-8")

    text_result = LogParser.extract_from_text(GAUSSIAN_TEXT, "gaussian")
    path_result = LogParser.extract_last_converged_coords(log_file, "gaussian")

    text_coords, text_symbols, text_error = text_result
    path_coords, path_symbols, path_error = path_result
    assert text_coords is not None
    assert path_coords is not None
    np.testing.assert_allclose(text_coords, path_coords)
    assert text_symbols == path_symbols == ["C", "O"]
    assert text_error is None
    assert path_error is None


def test_orca_text_matches_path_api(tmp_path: Path) -> None:
    output_file = tmp_path / "calculation.out"
    _ = output_file.write_text(ORCA_TEXT, encoding="utf-8")

    text_result = LogParser.extract_from_text(ORCA_TEXT, "orca")
    path_result = LogParser.extract_last_converged_coords(output_file, "orca")

    text_coords, text_symbols, text_error = text_result
    path_coords, path_symbols, path_error = path_result
    assert text_coords is not None
    assert path_coords is not None
    np.testing.assert_allclose(text_coords, path_coords)
    assert text_symbols == path_symbols == ["O", "N"]
    assert text_error is None
    assert path_error is None


def test_auto_detection_selects_gaussian_from_text_and_log_path(tmp_path: Path) -> None:
    log_file = tmp_path / "calculation.log"
    _ = log_file.write_text(GAUSSIAN_TEXT, encoding="utf-8")

    text_coords, text_symbols, text_error = LogParser.extract_from_text(GAUSSIAN_TEXT)
    path_coords, path_symbols, path_error = LogParser.extract_last_converged_coords(log_file)

    assert text_coords is not None
    assert path_coords is not None
    np.testing.assert_allclose(text_coords, path_coords)
    assert text_symbols == path_symbols == ["C", "O"]
    assert text_error is None
    assert path_error is None


def test_auto_detection_selects_orca_from_text_and_out_path(tmp_path: Path) -> None:
    output_file = tmp_path / "calculation.out"
    _ = output_file.write_text(ORCA_TEXT, encoding="utf-8")

    text_coords, text_symbols, text_error = LogParser.extract_from_text(ORCA_TEXT)
    path_coords, path_symbols, path_error = LogParser.extract_last_converged_coords(output_file)

    assert text_coords is not None
    assert path_coords is not None
    np.testing.assert_allclose(text_coords, path_coords)
    assert text_symbols == path_symbols == ["O", "N"]
    assert text_error is None
    assert path_error is None


def test_text_parser_returns_last_gaussian_geometry_block() -> None:
    coordinates, symbols, error = LogParser.extract_from_text(GAUSSIAN_TEXT, "gaussian")

    assert coordinates is not None
    np.testing.assert_allclose(coordinates, [[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    assert symbols == ["C", "O"]
    assert error is None


def test_text_parser_returns_last_orca_geometry_block() -> None:
    coordinates, symbols, error = LogParser.extract_from_text(ORCA_TEXT, "orca")

    assert coordinates is not None
    np.testing.assert_allclose(coordinates, [[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    assert symbols == ["O", "N"]
    assert error is None


def test_orca_text_parser_falls_back_to_gaussian() -> None:
    coordinates, symbols, error = LogParser.extract_from_text(GAUSSIAN_TEXT, "orca")

    assert coordinates is not None
    np.testing.assert_allclose(coordinates, [[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    assert symbols == ["C", "O"]
    assert error is None


def test_unparseable_text_returns_error_tuple() -> None:
    result = LogParser.extract_from_text("not a quantum chemistry log")

    coordinates, symbols, error = result
    assert coordinates is None
    assert symbols is None
    assert error is not None
