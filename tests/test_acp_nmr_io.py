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


# ---------------------------------------------------------------------------
# Per-peak assignment state + parse errors (G02/G03 remediation, todo 6)
# ---------------------------------------------------------------------------

_MIXED = "C: 10(C1), 20\nH: 1, 2"


def test_mixed_assignment_retains_all_peaks_with_per_peak_state() -> None:
    """G02 repro: 4 peaks, exactly 1 labeled, 3 unassigned, no global flip."""
    exp = parse_experimental_nmr(_MIXED)
    all_peaks = [p for pl in exp.peaks.values() for p in pl]
    assert len(all_peaks) == 4

    labeled = [p for p in all_peaks if p.assigned]
    assert len(labeled) == 1
    assert labeled[0].atom_label == "C1"
    assert labeled[0].label_candidates == ("C1",)

    unlabeled = [p for p in all_peaks if not p.assigned]
    assert len(unlabeled) == 3
    assert all(p.atom_label is None and p.label_candidates is None for p in unlabeled)

    # The global switch must NOT flip to True on a single labeled peak.
    assert exp.assigned is False

    # Per-element assigned state (authoritative).
    counts = exp.assignment_counts()
    assert counts["C"] == (1, 2)
    assert counts["H"] == (0, 2)
    assert exp.assigned_nuclei == ["C"]

    # Stable per-element identity for downstream matching (T7).
    assert [p.index for p in exp.peaks_for("C")] == [0, 1]
    assert [p.index for p in exp.peaks_for("H")] == [0, 1]

    # Parse errors exist and are human-readable.
    assert exp.parse_errors
    missing = [i for i in exp.parse_errors if i.code == "missing_label"]
    assert len(missing) == 1
    assert "20" in missing[0].detail
    for issue in exp.parse_errors:
        text = str(issue)
        assert issue.code in text
        assert issue.detail
    assert exp.parse_errors[0].to_dict()["code"] == exp.parse_errors[0].code


def test_unknown_token_recorded_not_silently_dropped() -> None:
    exp = parse_experimental_nmr("C: 10(C1), oops, 20\n")
    # Positional peaks that DO parse are retained; the bad token is visible.
    assert [p.shift_ppm for p in exp.peaks_for("C")] == [10.0, 20.0]
    unknown = [i for i in exp.parse_errors if i.code == "unknown_token"]
    assert unknown
    assert any(i.token == "oops" for i in unknown)


def test_unrecognized_line_recorded_as_unknown_token() -> None:
    exp = parse_experimental_nmr("C: 50.0(C1)\nrandom garbage line\n")
    assert any(
        i.code == "unknown_token" and "random garbage line" in i.token for i in exp.parse_errors
    )


def test_duplicate_label_recorded() -> None:
    exp = parse_experimental_nmr("C: 10(C1), 20(C1)\n")
    assert len(exp.peaks_for("C")) == 2  # peaks still retained
    dups = [i for i in exp.parse_errors if i.code == "duplicate_label"]
    assert dups
    assert "C1" in dups[0].detail


def test_missing_label_recorded_only_for_partial_nucleus() -> None:
    exp = parse_experimental_nmr(_MIXED)
    missing = [i for i in exp.parse_errors if i.code == "missing_label"]
    assert len(missing) == 1  # only C is partially assigned; H is deliberate

    # A fully unassigned spectrum must not flood missing_label noise.
    plain = parse_experimental_nmr("C: 10, 20\nH: 1, 2")
    assert plain.assigned is False
    assert not [i for i in plain.parse_errors if i.code == "missing_label"]


def test_ambiguous_label_kept_as_candidate_set() -> None:
    """`H32 or H33` must stay a candidate set — never collapsed to one."""
    exp = parse_experimental_nmr("H: 7.18(H32 or H33), 1.02\n")
    peaks = exp.peaks_for("H")
    assert len(peaks) == 2  # the ambiguous peak is not dropped
    amb = peaks[0]
    assert amb.label_candidates == ("H32", "H33")
    assert amb.atom_label is None
    assert amb.assigned is False
    assert amb.ambiguous is True
    assert any(i.code == "ambiguous_label" for i in exp.parse_errors)
    assert exp.assigned is False


def test_comma_form_ambiguous_label_kept_as_candidates() -> None:
    exp = parse_experimental_nmr("H: 7.18(H32,H33)\n")
    peak = exp.peaks_for("H")[0]
    assert peak.label_candidates == ("H32", "H33")
    assert peak.atom_label is None


def test_unmatched_peak_and_atom_recorded() -> None:
    exp = parse_experimental_nmr("C: 10(H5)\nOMIT: F3\n")
    codes = {i.code for i in exp.parse_errors}
    assert "unmatched_peak" in codes  # H label on a C nucleus line
    assert "unmatched_atom" in codes  # F3 but no F: section was parsed
    # the peak itself is still retained with its (mismatched) label visible
    assert exp.peaks_for("C")[0].atom_label == "H5"
