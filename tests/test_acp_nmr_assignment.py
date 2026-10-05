"""Tests for assignment matching (DevDoc §5 stage 5 / §8.3)."""

from __future__ import annotations

from acp.nmr.assignment import (
    AssignmentResult,
    collect_residual_inputs,
    match_assigned,
    match_unassigned,
)
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import AtomShift, ConformerShielding, ExperimentalNmr, NmrConfig


def _shieldings(symbols_shielding: dict[int, tuple[str, float]]) -> dict[int, dict[str, object]]:
    return {idx: {"symbol": sym, "isotropic": iso} for idx, (sym, iso) in symbols_shielding.items()}


def _one_conformer_shieldings() -> dict[int, dict[str, object]]:
    return _shieldings(
        {
            0: ("C", 150.0),
            1: ("H", 28.0),
            2: ("H", 29.0),
            3: ("H", 31.0),
            4: ("H", 32.0),
        }
    )


def _shifts_for(symbols: list[str], shielding: dict[int, dict[str, object]]) -> list:
    cs = [ConformerShielding("c0", 1.0, shielding)]
    return boltzmann_average_shieldings(cs, symbols, NmrConfig())


def test_assigned_passthrough() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)")
    pairs = match_assigned(shifts, exp).pairs
    assert set(pairs) == {"13C", "1H"}
    assert len(pairs["1H"]) == 4
    assert pairs["1H"][0][0].atom_label == "H1"


def test_assigned_omits_dropped_atoms() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)\nOMIT: H4")
    pairs = match_assigned(shifts, exp).pairs
    labels = [s.atom_label for s, _ in pairs["1H"]]
    assert "H4" not in labels
    assert len(labels) == 3


def test_unassigned_hungarian_full_match() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    # peaks deliberately shuffled to confirm Hungarian minimizes total cost
    exp = parse_experimental_nmr("C: 36.0\nH: 3.5, 2.5, 0.5, -0.5")
    pairs = match_unassigned(shifts, exp)
    h_group = pairs["1H"]
    assert len(h_group) == 4
    # each peak matched exactly once
    peaks = [p for _, p in h_group]
    assert len({p.shift_ppm for p in peaks}) == 4


def test_unassigned_intensity_weighting() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    # one peak has multiplicity 3 (CH3-like)
    exp = parse_experimental_nmr("C: 36.0\nH: 3.5, 2.5, 0.5, -0.5(3)")
    pairs_weighted = match_unassigned(shifts, exp, use_intensity_weight=True)
    assert len(pairs_weighted["1H"]) == 4


def test_unassigned_more_signals_than_peaks() -> None:
    # 4 H signals, only 2 peaks → 2 dummies dropped, 2 real matches
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("H: 3.0, 1.0")
    pairs = match_unassigned(shifts, exp)
    assert len(pairs["1H"]) == 2


def test_collect_residual_inputs_shape() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)")
    pairs = match_assigned(shifts, exp).pairs
    ri = collect_residual_inputs(pairs)
    assert "1H" in ri
    assert len(ri["1H"]["calc"]) == 4
    assert len(ri["1H"]["exp"]) == 4
    assert ri["1H"]["labels"] == ["H1", "H2", "H3", "H4"]


# --- two-phase assignment (G02/G11, todo 7) ---------------------------------

MIXED_TEXT = "C: 10(C1), 20\nH: 1, 2"


def _mixed_shifts() -> list[AtomShift]:
    """Synthetic signals where C1's shift sits FAR from its own exp peak.

    A free (unlocked) Hungarian therefore steals peak C[0] for computed C2 —
    exactly the known-pair violation the two-phase matcher must prevent.
    """
    return [
        AtomShift(0, "C", "13C", 170.0, 20.5, "C1"),
        AtomShift(1, "C", "13C", 180.0, 10.4, "C2"),
        AtomShift(2, "H", "1H", 31.0, 1.05, "H1"),
        AtomShift(3, "H", "1H", 30.0, 2.04, "H2"),
    ]


def _all_peak_keys(exp: ExperimentalNmr) -> set[tuple[str, int]]:
    return {(element, p.index) for element, group in exp.peaks.items() for p in group}


def test_mixed_input_two_phase_classifies_all_four_peaks() -> None:
    """G02 repro: all 4 peaks land in pairs or unmatched — never dropped."""
    exp = parse_experimental_nmr(MIXED_TEXT)
    result = match_assigned(_mixed_shifts(), exp)

    assert isinstance(result, AssignmentResult)
    classification = result.peak_classification()
    assert set(classification) == _all_peak_keys(exp)
    assert len(classification) == 4

    # Phase 1: the uniquely labeled peak locks to its label, unconditionally.
    assert classification[("C", 0)] == "locked"
    locked_group = result.locked.get("13C", [])
    assert len(locked_group) == 1
    locked_shift, locked_peak = locked_group[0]
    assert locked_shift.atom_label == "C1" and locked_peak.shift_ppm == 10.0

    # Phase 2: the 3 unlabeled peaks are matched (no silent drop).
    assert classification[("C", 1)] == "pair"
    assert classification[("H", 0)] == "pair"
    assert classification[("H", 1)] == "pair"

    # Known pair preserved in the final pairs: no labeled peak re-paired.
    for group in result.pairs.values():
        for shift, peak in group:
            if peak.atom_label is not None:
                assert shift.atom_label == peak.atom_label, (
                    f"known pair violated: peak {peak.element}[{peak.index}] "
                    f"label {peak.atom_label} -> {shift.atom_label}"
                )

    assert result.unmatched == []
    assert result.scale_iterations == 1


def test_unknown_label_never_enters_matching_but_is_diagnosed() -> None:
    exp = parse_experimental_nmr("C: 99(C9), 20\nH: 1, 2")
    result = match_assigned(_mixed_shifts(), exp)

    classification = result.peak_classification()
    assert set(classification) == _all_peak_keys(exp)

    # C9 has no computed atom: it must NOT appear in pairs (nor in phase 2).
    assert classification[("C", 0)] == "unmatched"
    for group in result.pairs.values():
        assert all(peak.atom_label != "C9" for _, peak in group)

    diagnosed = [u for u in result.unmatched if u.atom_label == "C9"]
    assert len(diagnosed) == 1
    assert diagnosed[0].reason == "unknown_label"
    assert diagnosed[0].element == "C" and diagnosed[0].shift_ppm == 99.0

    # The unlabeled peaks still match (phase 2 unaffected by the bad label).
    assert classification[("C", 1)] == "pair"
    assert classification[("H", 0)] == "pair"
    assert classification[("H", 1)] == "pair"


def test_ambiguous_label_is_not_locked_but_recorded() -> None:
    exp = parse_experimental_nmr("C: 10(C1 or C2), 20\nH: 1, 2")
    result = match_assigned(_mixed_shifts(), exp)

    classification = result.peak_classification()
    assert set(classification) == _all_peak_keys(exp)
    assert classification[("C", 0)] == "unmatched"
    assert all(
        peak.atom_label != "C1" or peak.shift_ppm != 10.0
        for group in result.locked.values()
        for _, peak in group
    )

    ambiguous = [u for u in result.unmatched if u.reason == "ambiguous_label"]
    assert len(ambiguous) == 1
    assert ambiguous[0].label_candidates == ("C1", "C2")
    assert ambiguous[0].shift_ppm == 10.0

    # The genuinely unlabeled peaks still match.
    assert classification[("C", 1)] == "pair"
    assert classification[("H", 1)] == "pair"


def test_omit_atom_peak_diagnosed_not_matched() -> None:
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)\nOMIT: H4")
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    result = match_assigned(shifts, exp)

    classification = result.peak_classification()
    assert set(classification) == _all_peak_keys(exp)
    assert classification[("H", 3)] == "unmatched"
    omitted = [u for u in result.unmatched if u.reason == "omit_atom"]
    assert len(omitted) == 1 and omitted[0].atom_label == "H4"


def test_capacity_shortfall_peaks_recorded_not_dropped() -> None:
    exp = parse_experimental_nmr("H: 1.0, 2.0")
    shifts = [AtomShift(2, "H", "1H", 31.0, 1.05, "H1")]
    result = match_assigned(shifts, exp)

    classification = result.peak_classification()
    assert set(classification) == {("H", 0), ("H", 1)}
    assert classification[("H", 0)] == "pair"
    assert classification[("H", 1)] == "unmatched"
    assert [u.reason for u in result.unmatched] == ["no_available_signal"]


def test_near_optimal_delta_recorded_for_phase2() -> None:
    exp = parse_experimental_nmr("C: 10.0, 20.0")
    shifts = [
        AtomShift(0, "C", "13C", 170.0, 10.2, "C1"),
        AtomShift(1, "C", "13C", 180.0, 20.4, "C2"),
    ]
    result = match_assigned(shifts, exp)

    assert len(result.near_optimal) == 1
    alt = result.near_optimal[0]
    assert alt.nucleus == "13C"
    # best  = |10.0-10.2| + |20.0-20.4| = 0.6
    # alt   = |10.0-20.4| + |20.0-10.2| = 20.2  -> delta 19.6
    assert abs(alt.delta - 19.6) < 1e-9
    assert abs(alt.cost - 20.2) < 1e-9
    assert len(alt.assignment) == 2
    assert {entry[0] for entry in alt.assignment} == {"C1", "C2"}
    assert result.scale_iterations == 1


def test_fully_assigned_two_phase_keeps_exact_pairs() -> None:
    """Fully assigned input: phase 2 is empty — same pairs as the old passthrough."""
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)")
    result = match_assigned(shifts, exp)

    assert set(result.pairs) == {"13C", "1H"}
    assert result.pairs == result.locked
    assert result.unmatched == []
    assert result.near_optimal == []
    assert [s.atom_label for s, _ in result.pairs["1H"]] == ["H1", "H2", "H3", "H4"]
