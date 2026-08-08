"""Tests for symmetry-equivalence detection (DevDoc §8.3)."""

from __future__ import annotations

from acp.nmr.equivalence import (
    build_all_labels,
    build_label_for_atom,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)


def test_detect_groups_single_element() -> None:
    # 4 hydrogens, no connectivity → element-only fallback collapses all 4
    groups = detect_equivalence_groups(["H", "H", "H", "H"])
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2, 3]


def test_detect_groups_mixed_elements() -> None:
    groups = detect_equivalence_groups(["C", "H", "H", "C", "H"])
    by_first = {min(g): sorted(g) for g in groups}
    assert by_first[0] == [0, 3]  # the two carbons
    assert by_first[1] == [1, 2, 4]  # the three hydrogens


def test_label_assignment_is_per_element_one_indexed() -> None:
    symbols = ["C", "H", "H", "C", "H"]
    labels = build_all_labels(symbols)
    assert labels == ["C1", "H1", "H2", "C2", "H3"]
    assert build_label_for_atom(3, symbols) == "C2"
    assert build_label_for_atom(4, symbols) == "H3"


def test_merge_explicit_takes_precedence() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    detected = detect_equivalence_groups(symbols)
    merged = merge_explicit_and_detected([["H1", "H2"]], detected, symbols)
    # explicit group survives; the remaining hydrogens form their own group
    flat = {tuple(sorted(g)) for g in merged}
    assert ("H1", "H2") in flat or (1, 2) in flat or any(set(g) == {1, 2} for g in merged)
    # all atoms covered
    all_idx = sorted(i for g in merged for i in g)
    assert all_idx == [0, 1, 2, 3, 4]


def test_merge_no_explicit_returns_detected_unchanged() -> None:
    symbols = ["C", "H", "H"]
    detected = detect_equivalence_groups(symbols)
    merged = merge_explicit_and_detected([], detected, symbols)
    assert merged == detected
