"""Boundary tests for the real ORCA ``$hessian`` block decoder (T04).

Positives: a Fortran ``D``-exponent token and a width-5 column block whose
final block is a 3-column tail must decode to exact values.  Negatives:
missing row, duplicate row index, out-of-range index, truncated block
(values missing mid-file), and a non-finite value each raise
:class:`HessFileError` with a message identifying the defect.

Inputs are hand-built here; ``tests/tsmode_synthetic.py`` deliberately
emits the legacy flat layout and is not used for real-format cases.
"""

from __future__ import annotations

import numpy as np
import pytest

from cccp.qc.interfaces.hess_file import HessFileError, parse_orca_hess_file


def _atoms_section() -> list[str]:
    return [
        "$atoms",
        "1",
        " H      1.00800      0.000000000000     0.000000000000     0.100000000000",
    ]


def _write(tmp_path, lines: list[str], name: str = "case.hess"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tiny_block_body() -> list[str]:
    """Complete 3x3 single-block body: header ``0 1 2`` + rows 0..2."""
    return [
        "$hessian",
        "3",
        "       0        1        2",
        "   0      1.0000000000E+00   2.0000000000E+00   3.0000000000E+00",
        "   1      2.0000000000E+00   4.0000000000E+00   5.0000000000E+00",
        "   2      3.0000000000E+00   5.0000000000E+00   6.0000000000E+00",
        "$end",
    ]


def test_parses_d_exponent_token(tmp_path) -> None:
    """A ``D``-exponent value token decodes (Fortran ``D`` → ``E``)."""
    lines = _atoms_section() + [
        "$hessian",
        "3",
        "       0        1        2",
        "   0      1.5D+02            0.0000000000E+00   0.0000000000E+00",
        "   1      0.0000000000E+00   2.5000000000E+00   0.0000000000E+00",
        "   2      0.0000000000E+00   0.0000000000E+00   3.0000000000E+00",
        "$end",
    ]
    data = parse_orca_hess_file(_write(tmp_path, lines))
    assert data.dimension == 3
    assert data.hessian[0, 0] == 150.0
    np.testing.assert_array_equal(data.hessian, np.diag([150.0, 2.5, 3.0]))


def test_tail_block_narrower_than_block_width(tmp_path) -> None:
    """Block width 5 over a 3N=18 matrix ends in a 3-column tail block.

    The builder tiles columns as 5 + 5 + 5 + 3; every cell must land at its
    (row, column) position with full precision.
    """
    rng = np.random.default_rng(42)
    raw = rng.normal(scale=0.3, size=(18, 18))
    matrix = 0.5 * (raw + raw.T)

    lines = ["$atoms", "6"]
    for i in range(6):
        lines.append(f" C    {12.0 + i:.5f}    {i * 0.1:.12f}    {i * -0.2:.12f}    {i * 0.3:.12f}")
    lines.append("$hessian")
    lines.append("18")
    for col0 in range(0, 18, 5):
        cols = list(range(col0, min(col0 + 5, 18)))
        lines.append("".join(f"{c:>8d}" for c in cols))
        for row in range(18):
            values = " ".join(f"{matrix[row, c]:.16E}" for c in cols)
            lines.append(f"{row:>4d} {values}")
    lines.append("$end")

    data = parse_orca_hess_file(_write(tmp_path, lines))
    assert data.dimension == 18
    assert data.n_atoms == 6
    np.testing.assert_allclose(data.hessian, matrix, rtol=1e-12, atol=1e-15)
    # The tail block really is 3 columns wide (15..17), not padded/rejected.
    assert data.hessian[17, 15] == pytest.approx(matrix[17, 15], rel=1e-12)
    assert data.hessian[15, 17] == pytest.approx(matrix[15, 17], rel=1e-12)


def test_missing_row_rejected(tmp_path) -> None:
    """A block that ends before ``dimension`` rows reports the shortfall."""
    lines = _atoms_section() + _tiny_block_body()[:5] + ["$end"]
    with pytest.raises(HessFileError, match="expected 3 rows, got 2"):
        parse_orca_hess_file(_write(tmp_path, lines))


def test_duplicate_row_index_rejected(tmp_path) -> None:
    """The same row index twice in one block is rejected."""
    body = _tiny_block_body()
    body[5] = "   1      3.0000000000E+00   5.0000000000E+00   6.0000000000E+00"
    with pytest.raises(HessFileError, match="duplicate row index 1"):
        parse_orca_hess_file(_write(tmp_path, _atoms_section() + body))


def test_out_of_range_row_index_rejected(tmp_path) -> None:
    """A row index outside ``[0, dimension)`` is rejected."""
    body = _tiny_block_body()
    body[5] = "   3      3.0000000000E+00   5.0000000000E+00   6.0000000000E+00"
    with pytest.raises(HessFileError, match="row index 3 out of range"):
        parse_orca_hess_file(_write(tmp_path, _atoms_section() + body))


def test_truncated_block_values_rejected(tmp_path) -> None:
    """A row missing trailing values mid-file (block truncation) is rejected."""
    body = _tiny_block_body()
    body[4] = "   1      2.0000000000E+00"
    with pytest.raises(HessFileError, match="expected 3 values"):
        parse_orca_hess_file(_write(tmp_path, _atoms_section() + body))


def test_non_finite_value_rejected(tmp_path) -> None:
    """A ``nan`` entry in the block body is rejected with a clear message."""
    body = _tiny_block_body()
    body[4] = "   1      2.0000000000E+00   nan                   5.0000000000E+00"
    with pytest.raises(HessFileError, match="non-finite values"):
        parse_orca_hess_file(_write(tmp_path, _atoms_section() + body))
