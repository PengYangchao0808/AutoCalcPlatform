"""Tests for the experimental-NMR text-format parser (DevDoc §6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.nmr.io import parse_experimental_nmr


def test_parse_assigned_spectrum_with_eq_and_omit() -> None:
    text = """
# 13C
C: 167.33(C1), 59.58(C2), 24.50(C3), 157.42(C8)

# 1H
H: 4.81(H4), 7.18(H5), 3.09(H6)

EQ: C10,C12
EQ: H15,H16
OMIT: H19,H51
"""
    exp = parse_experimental_nmr(text)
    assert exp.assigned is True
    assert exp.nuclei() == ["C", "H"]
    assert [p.shift_ppm for p in exp.peaks_for("C")] == [167.33, 59.58, 24.50, 157.42]
    assert [p.atom_label for p in exp.peaks_for("C")] == ["C1", "C2", "C3", "C8"]
    assert exp.equivalence_groups == [["C10", "C12"], ["H15", "H16"]]
    assert exp.omit_atoms == ["H19", "H51"]


def test_parse_unassigned_with_multiplicity() -> None:
    text = """C: 167.33, 59.58
H: 4.81, 7.18, 3.09, 2.95(3), 3.41(2)"""
    exp = parse_experimental_nmr(text)
    assert exp.assigned is False
    h_peaks = exp.peaks_for("H")
    assert [p.atom_label for p in h_peaks] == [None, None, None, None, None]
    assert [p.multiplicity for p in h_peaks] == [1, 1, 1, 3, 2]


def test_parse_from_file(tmp_path: Path) -> None:
    path = tmp_path / "exp.txt"
    path.write_text("C: 100.0(C1)\nH: 5.0(H1)\n", encoding="utf-8")
    exp = parse_experimental_nmr(path)
    assert exp.assigned is True
    assert exp.peaks_for("C")[0].shift_ppm == 100.0


def test_parse_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_experimental_nmr("# just a comment\n")


def test_parse_ignores_garbage_lines() -> None:
    exp = parse_experimental_nmr("C: 50.0(C1)\nrandom garbage line\n")
    assert exp.peaks_for("C")[0].shift_ppm == 50.0


def test_parse_case_insensitive_keywords() -> None:
    exp = parse_experimental_nmr("C: 50.0(C1)\neq: H1,H2\nomit: H3")
    assert exp.equivalence_groups == [["H1", "H2"]]
    assert exp.omit_atoms == ["H3"]
