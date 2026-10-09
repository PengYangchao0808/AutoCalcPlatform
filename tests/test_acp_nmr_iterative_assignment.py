"""Iterative assignment counterexample matrix (todo 46 / G11).

Every counterexample named by the plan gets a determinate, pinned outcome:

* overall offset (external initial calibration vs identity seed),
* near overlap (explicit overlap region reporting),
* methyl (multi-atom EQ group consumes peak integral capacity),
* missing peaks (unmatched signal diagnostic, never a forced wrong match),
* wrong candidate (mismatch diagnostics, degenerate — no chemistry claim),
* convergence + near-optimal listing with honest per-alternative calibration,
* anti-overfit guard (free re-permutation may not reuse the old calibration),
* determinism (same input / shuffled input order → identical outcome).

The counterfactual against the old one-shot matcher is asserted explicitly in
:func:`test_overall_offset_identity_seed_iteration_recovers_after_crossed_first_pass`
— the iterative engine must climb out of the crossed absolute-difference
optimum, not reproduce it.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from acp.nmr.assignment import match_assigned
from acp.nmr.iterative_assignment import (
    ASSIGNMENT_STATUSES,
    CALIBRATION_EXTERNAL_SEED,
    CALIBRATION_INSUFFICIENT,
    CALIBRATION_INTERNAL_REFIT,
    CalibrationReuseError,
    CalibrationSeed,
    IterativeAssignmentConfig,
    PeakConstraint,
    assignment_fingerprint,
    refit_calibration,
    run_iterative_assignment,
    score_assignment,
)
from acp.nmr.models import AtomShift, ExperimentalNmr, ExperimentalPeak, SignalGroup

# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _exp(entries: dict[str, list[tuple[float, int]]]) -> ExperimentalNmr:
    """Build an ExperimentalNmr from ``{element: [(shift, multiplicity), ...]}``."""
    peaks = {
        element: [
            ExperimentalPeak(shift_ppm=shift, element=element, multiplicity=mult, index=index)
            for index, (shift, mult) in enumerate(items)
        ]
        for element, items in entries.items()
    }
    return ExperimentalNmr(peaks=peaks)


def _carbon(label: str, shift: float, index: int = 0) -> AtomShift:
    return AtomShift(index, "C", "13C", 0.0, shift, label)


def _proton(
    label: str,
    shift: float,
    index: int = 0,
    group: SignalGroup | None = None,
) -> AtomShift:
    return AtomShift(index, "H", "1H", 0.0, shift, label, group)


def _methyl_group(symbols: tuple[str, ...] = ("H1", "H2", "H3")) -> SignalGroup:
    n = len(symbols)
    return SignalGroup(
        atom_uids=symbols,
        coefficients=tuple(1.0 / n for _ in symbols),
        equivalence_basis="explicit",
        peak_capacity=3,
    )


# ---------------------------------------------------------------------------
# overall offset: external initial calibration starts the iteration (G11)
# ---------------------------------------------------------------------------

_OFFSET_CALC = [10.0, 18.0, 26.0, 34.0]
_OFFSET_EXP = [(10.0, 1), (20.0, 1), (30.0, 1), (40.0, 1)]
_TRUE_MAP = {"C1": 0, "C2": 1, "C3": 2, "C4": 3}


def _offset_shifts() -> list[AtomShift]:
    return [
        _carbon("C1", _OFFSET_CALC[0], 0),
        _carbon("C2", _OFFSET_CALC[1], 1),
        _carbon("C3", _OFFSET_CALC[2], 2),
        _carbon("C4", _OFFSET_CALC[3], 3),
    ]


def test_overall_offset_external_seed_converges_to_true_mapping() -> None:
    """External seed (slope=0.8, intercept=2) inverts the compression exactly."""
    out = run_iterative_assignment(
        _offset_shifts(),
        _exp({"C": _OFFSET_EXP}),
        seed=[CalibrationSeed("13C", slope=0.8, intercept=2.0)],
    )

    assert out.status == "converged"
    assert out.assignment_map() == _TRUE_MAP
    assert out.iterations == 2
    assert out.refit_performed is True
    cal = out.calibration_for("13C")
    assert cal.source == CALIBRATION_INTERNAL_REFIT
    assert cal.slope == pytest.approx(0.8, abs=1e-9)
    assert cal.intercept == pytest.approx(2.0, abs=1e-9)
    assert cal.fitted_on == assignment_fingerprint(out.pairs)
    assert out.total_cost == pytest.approx(0.0, abs=1e-9)


def test_overall_offset_one_shot_tie_is_offset_dominated_seeded_iteration_unique() -> None:
    """A systematic +100 ppm offset makes every raw permutation an exact tie.

    The uncalibrated one-shot matcher therefore cannot decide the assignment
    from chemistry (its raw total is ``Σcalc − Σexp`` for every permutation);
    the externally seeded iteration is unique and exact.
    """
    calc = [101.0, 101.5, 102.0]
    table = [(1.0, 1), (1.5, 1), (2.0, 1)]
    shifts = [_carbon(f"C{i + 1}", value, i) for i, value in enumerate(calc)]
    experiment = _exp({"C": table})

    one_shot = match_assigned(shifts, experiment)
    one_shot_map = {
        shift.atom_label: peak.index for group in one_shot.pairs.values() for shift, peak in group
    }
    assert set(one_shot_map.values()) == {0, 1, 2}  # a full matching...
    raw_cost = sum(
        abs(shift.shift_ppm - peak.shift_ppm)
        for group in one_shot.pairs.values()
        for shift, peak in group
    )
    reversed_cost = (
        abs(calc[0] - table[2][0]) + abs(calc[1] - table[1][0]) + abs(calc[2] - table[0][0])
    )
    assert raw_cost == pytest.approx(300.0, abs=1e-9)
    assert reversed_cost == pytest.approx(raw_cost, abs=1e-9)  # ...but not a decision

    out = run_iterative_assignment(
        shifts, experiment, seed=[CalibrationSeed("13C", slope=1.0, intercept=100.0)]
    )
    assert out.status == "converged"
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}
    assert out.iterations == 2
    assert out.total_cost == pytest.approx(0.0, abs=1e-9)
    cal = out.calibration_for("13C")
    assert cal.source == CALIBRATION_INTERNAL_REFIT
    assert cal.slope == pytest.approx(1.0, abs=1e-9)
    assert cal.intercept == pytest.approx(100.0, abs=1e-9)


# ---------------------------------------------------------------------------
# near overlap: explicit overlap region reporting
# ---------------------------------------------------------------------------


def test_near_overlap_regions_reported_and_assignment_stable() -> None:
    shifts = [_carbon("C1", 20.05, 0), _carbon("C2", 20.20, 1), _carbon("C3", 60.05, 2)]
    experiment = _exp({"C": [(20.00, 1), (20.10, 1), (60.00, 1)]})

    out = run_iterative_assignment(
        shifts, experiment, config=IterativeAssignmentConfig(overlap_window_ppm=0.15)
    )

    assert out.status == "converged"
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}
    assert len(out.near_overlap_regions) == 1
    region = out.near_overlap_regions[0]
    assert region.element == "C"
    assert region.indices == (0, 1)
    assert region.width_ppm == pytest.approx(0.10, abs=1e-12)

    in_region = {pair.atom_label: pair.in_overlap_region for pair in out.pairs}
    assert in_region == {"C1": True, "C2": True, "C3": False}


def test_overlap_window_zero_disables_region_reporting() -> None:
    shifts = [_carbon("C1", 20.05, 0), _carbon("C2", 20.20, 1)]
    experiment = _exp({"C": [(20.00, 1), (20.10, 1)]})
    out = run_iterative_assignment(
        shifts, experiment, config=IterativeAssignmentConfig(overlap_window_ppm=0.0)
    )
    assert out.status == "converged"
    assert out.near_overlap_regions == ()


# ---------------------------------------------------------------------------
# methyl: multi-atom EQ group consumes peak integral capacity
# ---------------------------------------------------------------------------


def test_methyl_group_capacity_forces_multi_atom_signal_to_integral_peak() -> None:
    """The CH3 signal's nearest peak (mult 1) is capacity-infeasible (3 > 1)."""
    shifts = [
        _proton("H1", 1.24, 0, _methyl_group()),
        _proton("H4", 3.55, 3),
        _proton("H5", 7.20, 4),
    ]
    experiment = _exp({"H": [(1.25, 1), (1.35, 3), (3.60, 1), (7.15, 1)]})

    out = run_iterative_assignment(shifts, experiment)

    assert out.status == "converged"
    assert out.assignment_map() == {"H1": 1, "H4": 2, "H5": 3}

    methyl = next(pair for pair in out.pairs if pair.atom_label == "H1")
    assert methyl.atom_count == 3
    assert methyl.peak_capacity == 3
    assert methyl.signal_capacity == 3
    assert methyl.exp_ppm == pytest.approx(1.35, abs=1e-12)
    # residual is reported in CALIBRATED space (the final refit absorbs the offset)
    assert abs(methyl.residual) < 0.01

    # the near-but-too-small peak is diagnosed, not silently re-matched
    unused = [peak for peak in out.unmatched_peaks if peak.index == 0]
    assert len(unused) == 1
    assert unused[0].reason == "no_signal"

    # counterfactual: the capacity-blind one-shot matcher puts the 3-atom
    # methyl signal on the nearest multiplicity-1 peak
    one_shot = match_assigned(shifts, experiment)
    one_shot_map = {
        shift.atom_label: peak.index for group in one_shot.pairs.values() for shift, peak in group
    }
    assert one_shot_map == {"H1": 0, "H4": 2, "H5": 3}


def test_singleton_counterfactual_takes_the_near_peak() -> None:
    """Without group membership the same label takes the mult-1 peak: capacity is the cause."""
    experiment = _exp({"H": [(1.25, 1), (1.35, 3), (3.60, 1), (7.15, 1)]})
    plain = [_proton("H1", 1.24, 0), _proton("H4", 3.55, 3), _proton("H5", 7.20, 4)]

    out = run_iterative_assignment(plain, experiment)

    assert out.assignment_map()["H1"] == 0
    assert out.assignment_map() == {"H1": 0, "H4": 2, "H5": 3}


def test_explicit_peak_constraint_overrides_multiplicity_capacity() -> None:
    """Peak capacity is a first-class input, not fixed to the parsed integral."""
    shifts = [_proton("H1", 1.24, 0, _methyl_group()), _proton("H4", 3.55, 3)]
    experiment = _exp({"H": [(1.25, 1), (3.60, 1)]})

    default = run_iterative_assignment(shifts, experiment)
    assert "H1" in {u.atom_label for u in default.unmatched_signals}

    overridden = run_iterative_assignment(
        shifts,
        experiment,
        peak_constraints=[PeakConstraint("H", 0, atom_capacity=3)],
    )
    assert overridden.assignment_map() == {"H1": 0, "H4": 1}
    assert overridden.status == "converged"


# ---------------------------------------------------------------------------
# missing peaks: diagnosed, never a forced wrong match
# ---------------------------------------------------------------------------


def test_missing_experimental_peak_leaves_signal_unmatched() -> None:
    shifts = [_carbon(f"C{i}", 10.0 * i, i - 1) for i in range(1, 5)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1)]})

    out = run_iterative_assignment(shifts, experiment)

    assert out.status == "converged"
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}
    assert len(out.unmatched_signals) == 1
    missing = out.unmatched_signals[0]
    assert missing.atom_label == "C4"
    assert missing.reason == "no_available_peak"
    assert out.coverage() == {"13C": (3, 4)}
    assert out.unmatched_peaks == ()


# ---------------------------------------------------------------------------
# wrong candidate: mismatch diagnostics, no chemistry claim
# ---------------------------------------------------------------------------


def test_wrong_candidate_is_degenerate_with_explicit_mismatch_diagnostics() -> None:
    shifts = [_carbon("C1", 50.0, 0), _carbon("C2", 60.0, 1), _carbon("C3", 70.0, 2)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1)]})

    out = run_iterative_assignment(
        shifts, experiment, config=IterativeAssignmentConfig(mismatch_limit_ppm=5.0)
    )

    # a wrong candidate produces ZERO evidence pairs — never a smaller-objective
    # assignment presented as chemistry
    assert out.status == "degenerate"
    assert out.pairs == ()
    assert out.assignment_map() == {}
    assert out.calibration_for("13C").source == CALIBRATION_EXTERNAL_SEED
    assert out.refit_performed is False
    assert out.mismatch_count == 3
    assert {u.reason for u in out.unmatched_signals} == {"mismatch_limit"}
    assert {u.atom_label for u in out.unmatched_signals} == {"C1", "C2", "C3"}
    assert {p.reason for p in out.unmatched_peaks} == {"no_signal"}
    assert out.near_optimal == ()
    assert out.total_cost == 0.0


# ---------------------------------------------------------------------------
# convergence + near-optimal listing
# ---------------------------------------------------------------------------


def test_converged_outcome_lists_near_optimal_alternatives_honestly() -> None:
    shifts = [_carbon("C1", 10.0, 0), _carbon("C2", 20.2, 1), _carbon("C3", 30.0, 2)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1)]})

    out = run_iterative_assignment(shifts, experiment, config=IterativeAssignmentConfig(top_k=3))

    assert out.status == "converged"
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}
    assert 1 <= len(out.near_optimal) <= 3
    assert any(alt.swapped for alt in out.near_optimal)

    costs = [alt.cost for alt in out.near_optimal]
    assert costs == sorted(costs)
    for alt in out.near_optimal:
        assert alt.delta == pytest.approx(alt.cost - out.total_cost, abs=1e-12)
        assert alt.delta > 0
        assert set(alt.swapped) <= {"C1", "C2", "C3"}
        assert len(alt.swapped) >= 2  # a single forbidden edge releases a 2-swap
        # anti-overfit: the alternative carries ITS OWN refit calibration...
        best_fp = out.calibration_for("13C").fitted_on
        for cal in alt.calibrations:
            assert cal.fitted_on == assignment_fingerprint(alt.pairs)
            assert cal.fitted_on != best_fp
        # ...and it still scores after refit (guard accepts a matching calibration)
        scored = score_assignment(alt.pairs, alt.calibrations)
        assert scored.total_cost == pytest.approx(alt.cost, abs=1e-9)


def test_top_k_zero_lists_no_alternatives() -> None:
    shifts = [_carbon("C1", 10.0, 0), _carbon("C2", 20.0, 1)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1)]})
    out = run_iterative_assignment(shifts, experiment, config=IterativeAssignmentConfig(top_k=0))
    assert out.status == "converged"
    assert out.near_optimal == ()


def test_near_optimal_alternatives_preserve_signal_sets_and_slope_sign() -> None:
    shifts = [
        _carbon("C1", 10.0, 0),
        _carbon("C2", 20.2, 1),
        _carbon("C3", 30.0, 2),
        _proton("H1", 1.24, 3, _methyl_group()),
        _proton("H4", 3.55, 6),
        _proton("H6", 2.00, 7),
    ]
    experiment = _exp(
        {
            "C": [(10.0, 1), (20.0, 1), (30.0, 1)],
            "H": [(1.25, 1), (1.35, 3), (3.60, 1)],
        }
    )

    out = run_iterative_assignment(shifts, experiment, config=IterativeAssignmentConfig(top_k=5))

    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2, "H1": 1, "H4": 2, "H6": 0}
    assert out.near_optimal
    # a legitimate carbon re-pairing is listed...
    assert any(set(alt.swapped) == {"C2", "C3"} for alt in out.near_optimal)
    # ...while dropping the methyl (for the OH signal) and any negative-slope
    # re-pairing are not near-optimal assignments
    assert all("H1" not in alt.swapped for alt in out.near_optimal)
    assert all(len(alt.pairs) == len(out.pairs) for alt in out.near_optimal)
    assert all(cal.slope > 0 for alt in out.near_optimal for cal in alt.calibrations)


def test_max_iterations_is_a_typed_outcome_not_a_silent_success() -> None:
    shifts = [
        _carbon("C1", 6.0, 0),
        _carbon("C2", 27.8, 1),
        _carbon("C3", 28.1, 2),
        _carbon("C4", 32.4, 3),
    ]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1), (40.0, 1)]})

    out = run_iterative_assignment(
        shifts, experiment, config=IterativeAssignmentConfig(max_iterations=1)
    )

    assert out.status == "max_iterations"
    assert out.iterations == 1
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2, "C4": 3}
    # even a bounded stop reports an honestly calibrated best-so-far
    assert out.calibration_for("13C").fitted_on == assignment_fingerprint(out.pairs)


def test_calibration_delta_converges_after_one_full_alternation() -> None:
    shifts = [
        _carbon("C1", 6.0, 0),
        _carbon("C2", 27.8, 1),
        _carbon("C3", 28.1, 2),
        _carbon("C4", 32.4, 3),
    ]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1), (40.0, 1)]})

    tolerant = run_iterative_assignment(
        shifts, experiment, config=IterativeAssignmentConfig(calibration_delta=1e9)
    )
    strict = run_iterative_assignment(shifts, experiment)

    assert tolerant.status == "converged"
    assert tolerant.iterations == 2
    assert tolerant.assignment_map() == strict.assignment_map()
    assert tolerant.calibration_for("13C").fitted_on == assignment_fingerprint(tolerant.pairs)


# ---------------------------------------------------------------------------
# anti-overfit guard: a free re-permutation may not reuse the old calibration
# ---------------------------------------------------------------------------


def test_calibration_reuse_guard_rejects_free_repermutation() -> None:
    shifts = [_carbon("C1", 10.0, 0), _carbon("C2", 20.4, 1), _carbon("C3", 30.0, 2)]
    experiment = _exp({"C": [(10.0, 1), (20.1, 1), (30.2, 1)]})
    out = run_iterative_assignment(shifts, experiment)
    assert out.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}
    assert out.refit_performed is True

    # a free re-permutation that keeps the ORIGINAL calibration is rejected
    first, second = out.pairs[0], out.pairs[1]
    permuted = (
        replace(first, peak_index=second.peak_index, exp_ppm=second.exp_ppm),
        replace(second, peak_index=first.peak_index, exp_ppm=first.exp_ppm),
        out.pairs[2],
    )
    with pytest.raises(CalibrationReuseError, match="refit"):
        score_assignment(permuted, out.calibrations)

    # refitting inside the iteration makes the alternative admissible and honest
    refit = refit_calibration("13C", permuted)
    assert refit.source == CALIBRATION_INTERNAL_REFIT
    assert refit.fitted_on == assignment_fingerprint(permuted)
    scored = score_assignment(permuted, (refit,))
    assert scored.total_cost > 0.0
    # the original assignment keeps scoring under its own calibration
    original = score_assignment(out.pairs, out.calibrations)
    assert original.total_cost == pytest.approx(out.total_cost, abs=1e-12)


def test_score_assignment_rejects_missing_calibration() -> None:
    shifts = [_carbon("C1", 10.2, 0)]
    out = run_iterative_assignment(shifts, _exp({"C": [(10.0, 1)]}))
    with pytest.raises(CalibrationReuseError, match="calibration"):
        score_assignment(out.pairs, ())


# ---------------------------------------------------------------------------
# degenerate / insufficient-calibration corners
# ---------------------------------------------------------------------------


def test_single_pair_keeps_seed_calibration_and_marks_insufficient() -> None:
    out = run_iterative_assignment([_carbon("C1", 10.2, 0)], _exp({"C": [(10.0, 1)]}))

    assert out.status == "converged"
    assert out.assignment_map() == {"C1": 0}
    cal = out.calibration_for("13C")
    assert cal.source == CALIBRATION_INSUFFICIENT
    assert cal.slope == 1.0 and cal.intercept == 0.0
    assert out.refit_performed is False
    assert out.total_cost == pytest.approx(0.2, abs=1e-9)


def test_no_computed_signals_is_degenerate() -> None:
    out = run_iterative_assignment([], _exp({"C": [(10.0, 1)]}))
    assert out.status == "degenerate"
    assert out.pairs == ()
    assert out.unmatched_peaks[0].reason == "no_signal"


# ---------------------------------------------------------------------------
# exchangeable hydrogens: explicitly modeled, not a mismatch
# ---------------------------------------------------------------------------


def test_exchangeable_hydrogen_unobserved_is_not_counted_as_mismatch() -> None:
    shifts = [_proton("H1", 3.5, 0), _proton("H6", 2.0, 1)]

    unobserved = run_iterative_assignment(
        shifts, _exp({"H": [(3.5, 1)]}), exchangeable_atoms={"H6"}
    )
    assert unobserved.status == "converged"
    assert unobserved.assignment_map() == {"H1": 0}
    assert [(u.atom_label, u.reason) for u in unobserved.unmatched_signals] == [
        ("H6", "exchangeable_unobserved")
    ]
    assert unobserved.mismatch_count == 0
    assert unobserved.exchangeable_unobserved == 1

    observed = run_iterative_assignment(
        shifts, _exp({"H": [(3.5, 1), (2.0, 1)]}), exchangeable_atoms={"H6"}
    )
    assert observed.assignment_map() == {"H1": 0, "H6": 1}
    assert observed.exchangeable_unobserved == 0


# ---------------------------------------------------------------------------
# overlap slots: explicit sharing + atom-budget repair
# ---------------------------------------------------------------------------


def test_overlap_slots_allow_shared_peak_within_atom_budget() -> None:
    shifts = [_proton("H1", 10.00, 0), _proton("H2", 10.05, 1)]
    experiment = _exp({"H": [(10.00, 2)]})

    out = run_iterative_assignment(
        shifts, experiment, peak_constraints=[PeakConstraint("H", 0, slots=2)]
    )

    assert out.status == "converged"
    assert out.assignment_map() == {"H1": 0, "H2": 0}
    assert all(pair.overlap for pair in out.pairs)
    assert len(out.overlaps) == 1
    overlap = out.overlaps[0]
    assert overlap.element == "H"
    assert overlap.peak_index == 0
    assert overlap.atom_labels == ("H1", "H2")
    assert overlap.total_atoms == 2
    assert overlap.atom_capacity == 2
    assert overlap.slots == 2


def test_overlap_atom_budget_repair_never_exceeds_capacity() -> None:
    """A 2-atom group plus a proton cannot both live on a capacity-2 peak."""
    shifts = [
        _proton("H1", 10.00, 0, _methyl_group(("H1", "H2", "H3"))),
        _proton("H4", 10.05, 3),
        _proton("H5", 10.10, 4),
    ]
    # the group is 3 atoms -> only the capacity-3 constraint makes it feasible
    experiment = _exp({"H": [(10.00, 3)]})

    out = run_iterative_assignment(
        shifts, experiment, peak_constraints=[PeakConstraint("H", 0, slots=2)]
    )

    # 3 atoms + 1 atom > 3 atoms: the excess signals are released, not packed
    assert out.assignment_map() == {"H1": 0}
    assert {u.atom_label for u in out.unmatched_signals} == {"H4", "H5"}
    assert {u.reason for u in out.unmatched_signals} == {"capacity_overlap_exceeded"}
    assert out.overlaps == ()


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_same_input_produces_identical_outcome() -> None:
    shifts = [_carbon("C1", 10.2, 0), _carbon("C2", 20.1, 1), _proton("H1", 3.5, 2)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1)], "H": [(3.6, 1)]})

    first = run_iterative_assignment(shifts, experiment)
    second = run_iterative_assignment(shifts, experiment)

    assert first.to_dict() == second.to_dict()
    assert json.loads(json.dumps(first.to_dict())) == first.to_dict()


def test_input_order_does_not_change_the_solution() -> None:
    shifts = [_carbon("C1", 10.2, 0), _carbon("C2", 20.1, 1), _carbon("C3", 30.0, 2)]
    experiment = _exp({"C": [(10.0, 1), (20.0, 1), (30.0, 1)]})

    baseline = run_iterative_assignment(shifts, experiment)
    shuffled = run_iterative_assignment(list(reversed(shifts)), experiment)

    assert baseline.to_dict() == shuffled.to_dict()
    assert baseline.assignment_map() == {"C1": 0, "C2": 1, "C3": 2}


# ---------------------------------------------------------------------------
# typed vocabulary + validation
# ---------------------------------------------------------------------------


def test_status_vocabulary_is_closed() -> None:
    assert ASSIGNMENT_STATUSES == ("converged", "max_iterations", "degenerate")


def test_seed_validation_rejects_degenerate_slope() -> None:
    with pytest.raises(ValueError):
        CalibrationSeed("13C", slope=0.0, intercept=0.0)
    with pytest.raises(ValueError):
        CalibrationSeed("13C", slope=float("nan"), intercept=0.0)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        IterativeAssignmentConfig(max_iterations=0)
    with pytest.raises(ValueError):
        IterativeAssignmentConfig(top_k=-1)
    with pytest.raises(ValueError):
        IterativeAssignmentConfig(overlap_window_ppm=-0.1)
