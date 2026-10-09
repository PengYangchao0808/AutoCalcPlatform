# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Iterative assignment with calibrated convergence (todo 46 / G11).

The one-shot absolute-difference Hungarian matcher
(:func:`acp.nmr.assignment.match_assigned`) first matches on *uncalibrated*
shifts and then fits the internal calibration once.  A systematic offset or
compression can make the first pass pick a chemically wrong permutation, and
nothing afterwards revisits the assignment.  This module implements the
Goodman DP4-AI style alternative (gap investigation G11; upstream
``Carbon_assignment.py`` / ``Proton_assignment.py``, Goodman-lab/DP5 pinned
commit ``b6cf559``, indexed in gap doc §12.2 — not vendored in this repo):

1. the iteration is seeded by an **external initial calibration**
   (``CalibrationSeed``; identity when the caller supplies none);
2. **assignment and internal recalibration alternate** until the assignment
   stops changing (fixed point), bounded by ``max_iterations`` with the
   ``calibration_delta`` convergence tolerance;

and it models, as first-class inputs (never post-hoc filters):

* **peak capacity** — an experimental peak carries at most its integral
  (multiplicity) in nuclei; ``PeakConstraint`` can override it explicitly;
* **EQ-group atom counts** — a signal spanning ``len(SignalGroup.atom_uids)``
  equivalent atoms only fits a peak whose capacity can hold all of them
  (a methyl's 3 atoms never land on a multiplicity-1 peak);
* **overlap** — ``PeakConstraint.slots`` declares that a peak may host
  several signals; the summed atom count is enforced against the peak
  capacity by a bounded, deterministic repair inside the solver, and shared
  peaks are reported (``OverlapReport``); peaks closer than
  ``overlap_window_ppm`` are reported as uncertainty regions
  (``NearOverlapRegion``) and the signals matched into them are flagged;
* **mismatch** — edges beyond ``mismatch_limit_ppm`` in calibrated space are
  infeasible; every unmatched signal/peak is diagnosed with a closed reason;
* **exchangeable hydrogens** — labels passed via ``exchangeable_atoms`` that
  end unmatched are reported as ``exchangeable_unobserved`` and are excluded
  from the mismatch count (an OH/NH proton that exchanged away is not
  evidence against a candidate).

Near-optimal solutions (``top_k``, default 3) keep alternative assignments
with their cost deltas and the swapped labels.  Every alternative must
re-pair exactly the same matched signals, refit **its own calibration**, and
keep every calibration slope positive (an inverted shielding/shift relation
is not an admissible basin); alternatives that drop or substitute evidence
are discarded (shrinking the objective by assigning fewer signals is not
near-optimal), and :func:`score_assignment` refuses to score a re-permuted
assignment against a calibration that was fitted on different pairs
(:class:`CalibrationReuseError`) — a smaller objective value is never
presented as chemical correctness.

Determinism / tie-breaking
--------------------------
Signals are canonicalised by ``(nucleus, calc_ppm, atom_label)`` and peaks by
``(nucleus, index, shift_ppm)``; the Hungarian cost gets a tie-break epsilon
(``tie_break_epsilon``) whose lexicographic term prefers lower signal/column
indices.  The capacity repair always releases the worst-fitting edge (largest
absolute calibrated residual, ties by label).  Near-optima are sorted by
``(cost, assignment)`` and deduplicated.  Same input ⇒ same outcome.

A signal is assigned to at most one peak; splitting one signal across several
experimental peaks (``SignalGroup.peak_capacity > 1``) is deliberately not
performed — the declared value is carried into the diagnostics as
``signal_capacity`` instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from scipy.optimize import linear_sum_assignment

from acp.nmr.models import (
    AtomShift,
    ExperimentalNmr,
    normalize_symbol,
    nucleus_label,
)
from acp.nmr.scaling import fit_scaling_goodman

logger = logging.getLogger(__name__)

AssignmentStatus = Literal["converged", "max_iterations", "degenerate"]
ASSIGNMENT_STATUSES: tuple[str, ...] = ("converged", "max_iterations", "degenerate")

CALIBRATION_EXTERNAL_SEED = "external_seed"
CALIBRATION_INTERNAL_REFIT = "internal_refit"
CALIBRATION_INSUFFICIENT = "insufficient_pairs"
CALIBRATION_SOURCES: tuple[str, ...] = (
    CALIBRATION_EXTERNAL_SEED,
    CALIBRATION_INTERNAL_REFIT,
    CALIBRATION_INSUFFICIENT,
)


class IterativeAssignmentError(ValueError):
    """Typed error for malformed iterative-assignment input."""


class CalibrationReuseError(IterativeAssignmentError):
    """A re-permuted assignment tried to reuse a calibration fit on other pairs.

    Raised by :func:`score_assignment` when the calibration's ``fitted_on``
    fingerprint does not match the supplied assignment: the caller must refit
    (or re-run the iteration) before scoring.
    """


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationSeed:
    """External initial calibration for one nucleus (starts the iteration).

    ``scaled = (calc_ppm − intercept) / slope`` — the Goodman internal-scaling
    convention (``fit_scaling_goodman``).  Defaults to the identity.
    """

    nucleus: str
    slope: float = 1.0
    intercept: float = 0.0

    def __post_init__(self) -> None:
        if not self.nucleus.strip():
            raise ValueError("CalibrationSeed.nucleus must be non-blank")
        if not math.isfinite(self.slope) or self.slope == 0.0:
            raise ValueError(
                f"CalibrationSeed.slope must be finite and non-zero, got {self.slope!r}"
            )
        if not math.isfinite(self.intercept):
            raise ValueError(f"CalibrationSeed.intercept must be finite, got {self.intercept!r}")

    def to_dict(self) -> dict[str, object]:
        return {"nucleus": self.nucleus, "slope": self.slope, "intercept": self.intercept}


@dataclass(frozen=True)
class NucleusCalibration:
    """Per-nucleus calibration state actually used by the iteration.

    Attributes:
        nucleus: Nucleus label.
        slope / intercept: Goodman ``calc = slope·exp + intercept`` parameters.
        source: :data:`CALIBRATION_EXTERNAL_SEED`,
            :data:`CALIBRATION_INTERNAL_REFIT` or
            :data:`CALIBRATION_INSUFFICIENT` (< 2 pairs — identity kept).
        fitted_on: :func:`assignment_fingerprint` of the pairs this
            calibration was fitted on; consistent scoring requires the
            fingerprint to match the assignment being scored.
    """

    nucleus: str
    slope: float
    intercept: float
    source: str
    fitted_on: str

    def __post_init__(self) -> None:
        if self.source not in CALIBRATION_SOURCES:
            raise ValueError(
                f"unknown calibration source {self.source!r}; expected one of {CALIBRATION_SOURCES}"
            )

    def scaled(self, calc_ppm: float) -> float:
        """Map a raw computed shift into experimental (scaled) space."""
        return (calc_ppm - self.intercept) / self.slope

    def to_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "slope": self.slope,
            "intercept": self.intercept,
            "source": self.source,
            "fitted_on": self.fitted_on,
        }


def assignment_fingerprint(pairs: Sequence[MatchedSignal]) -> str:
    """Stable identity of a matched set (pairing only — not calibration-space values).

    The fingerprint covers ``(nucleus, atom_label, peak_index, calc_ppm,
    exp_ppm)``; ``scaled_ppm``/``residual`` are derived from the calibration
    and therefore excluded, so refitting a calibration does not invalidate a
    fingerprint of the same pairing.
    """
    rows: list[list[object]] = [
        [
            pair.nucleus,
            pair.atom_label,
            pair.peak_index,
            round(pair.calc_ppm, 9),
            round(pair.exp_ppm, 9),
        ]
        for pair in pairs
    ]
    rows.sort(key=lambda row: (str(row[0]), str(row[1]), -1 if row[2] is None else row[2]))
    payload = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def refit_calibration(nucleus: str, pairs: Sequence[MatchedSignal]) -> NucleusCalibration:
    """Refit the Goodman internal calibration on *pairs* (never reuses another set).

    Fewer than two pairs cannot identify a slope: the identity fit is kept and
    the source is :data:`CALIBRATION_INSUFFICIENT`.  Otherwise the OLS
    ``calc = slope·exp + intercept`` of :func:`fit_scaling_goodman` is used
    and the source is :data:`CALIBRATION_INTERNAL_REFIT`.
    """
    pair_tuple = tuple(pairs)
    fingerprint = assignment_fingerprint(pair_tuple)
    if len(pair_tuple) < 2:
        return NucleusCalibration(
            nucleus=nucleus,
            slope=1.0,
            intercept=0.0,
            source=CALIBRATION_INSUFFICIENT,
            fitted_on=fingerprint,
        )
    regression, _scaled, _residuals = fit_scaling_goodman(
        [pair.calc_ppm for pair in pair_tuple],
        [pair.exp_ppm for pair in pair_tuple],
        nucleus,
    )
    return NucleusCalibration(
        nucleus=nucleus,
        slope=float(regression.slope),
        intercept=float(regression.intercept),
        source=CALIBRATION_INTERNAL_REFIT,
        fitted_on=fingerprint,
    )


# ---------------------------------------------------------------------------
# configuration + inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeakConstraint:
    """Explicit per-peak capacity / overlap declaration (first-class input).

    Attributes:
        element: Element symbol the peak belongs to.
        index: ``ExperimentalPeak.index`` the constraint applies to;
            ``None`` applies to every peak of the element.
        atom_capacity: Maximum number of nuclei the peak can carry; ``None``
            keeps the parsed integral (multiplicity, minimum 1).
        slots: Number of signals that may share the peak (overlap); the
            combined atom count must still fit *atom_capacity*.
    """

    element: str
    index: int | None
    atom_capacity: int | None = None
    slots: int = 1

    def __post_init__(self) -> None:
        if not self.element.strip():
            raise ValueError("PeakConstraint.element must be non-blank")
        if self.index is not None and (
            isinstance(self.index, bool) or not isinstance(self.index, int) or self.index < 0
        ):
            raise ValueError("PeakConstraint.index must be a non-negative int or None")
        if self.atom_capacity is not None and (
            isinstance(self.atom_capacity, bool)
            or not isinstance(self.atom_capacity, int)
            or self.atom_capacity < 1
        ):
            raise ValueError("PeakConstraint.atom_capacity must be an int >= 1 or None")
        if isinstance(self.slots, bool) or not isinstance(self.slots, int) or self.slots < 1:
            raise ValueError(f"PeakConstraint.slots must be an int >= 1, got {self.slots!r}")


@dataclass(frozen=True)
class IterativeAssignmentConfig:
    """Termination + modelling parameters (all defaults sane, all bounded).

    Attributes:
        max_iterations: Hard bound on assignment↔refit alternations per nucleus.
        calibration_delta: |Δslope|/|Δintercept| tolerance: a refit that moves
            the calibration by no more than this counts as numerically
            unchanged and (after at least one full alternation) converges.
        top_k: Number of near-optimal alternatives to keep (0 disables).
        mismatch_limit_ppm: Maximum |scaled − exp| (calibrated space) for an
            edge to be feasible; ``None`` means no explicit limit (calibration
            absorbs global offsets).
        overlap_window_ppm: Peaks closer than this are reported as a
            near-overlap uncertainty region (0 disables the report).
        tie_break_epsilon: Lexicographic cost perturbation for deterministic
            tie resolution; far below any meaningful ppm difference.
    """

    max_iterations: int = 25
    calibration_delta: float = 1e-9
    top_k: int = 3
    mismatch_limit_ppm: float | None = None
    overlap_window_ppm: float = 0.10
    tie_break_epsilon: float = 1e-9

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations < 1
        ):
            raise ValueError(f"max_iterations must be an int >= 1, got {self.max_iterations!r}")
        if not math.isfinite(self.calibration_delta) or self.calibration_delta < 0:
            raise ValueError("calibration_delta must be a finite non-negative number")
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int) or self.top_k < 0:
            raise ValueError(f"top_k must be an int >= 0, got {self.top_k!r}")
        if self.mismatch_limit_ppm is not None and (
            not math.isfinite(self.mismatch_limit_ppm) or self.mismatch_limit_ppm <= 0
        ):
            raise ValueError("mismatch_limit_ppm must be a positive finite number or None")
        if not math.isfinite(self.overlap_window_ppm) or self.overlap_window_ppm < 0:
            raise ValueError("overlap_window_ppm must be a finite non-negative number")
        if not math.isfinite(self.tie_break_epsilon) or self.tie_break_epsilon < 0:
            raise ValueError("tie_break_epsilon must be a finite non-negative number")


# ---------------------------------------------------------------------------
# outcome records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchedSignal:
    """One computed signal matched to one experimental peak under a calibration."""

    nucleus: str
    atom_label: str
    element: str
    peak_index: int | None
    calc_ppm: float
    exp_ppm: float
    scaled_ppm: float
    residual: float
    atom_count: int
    peak_capacity: int
    signal_capacity: int | None
    overlap: bool = False
    in_overlap_region: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "atom_label": self.atom_label,
            "element": self.element,
            "peak_index": self.peak_index,
            "calc_ppm": self.calc_ppm,
            "exp_ppm": self.exp_ppm,
            "scaled_ppm": self.scaled_ppm,
            "residual": self.residual,
            "atom_count": self.atom_count,
            "peak_capacity": self.peak_capacity,
            "signal_capacity": self.signal_capacity,
            "overlap": self.overlap,
            "in_overlap_region": self.in_overlap_region,
        }


@dataclass(frozen=True)
class UnmatchedSignal:
    """A computed signal with no acceptable peak, with a closed-vocabulary reason."""

    nucleus: str
    atom_label: str
    element: str
    calc_ppm: float
    atom_count: int
    reason: str
    exchangeable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "atom_label": self.atom_label,
            "element": self.element,
            "calc_ppm": self.calc_ppm,
            "atom_count": self.atom_count,
            "reason": self.reason,
            "exchangeable": self.exchangeable,
        }


@dataclass(frozen=True)
class UnmatchedPeakRecord:
    """An experimental peak that received no signal."""

    nucleus: str
    element: str
    index: int | None
    shift_ppm: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "element": self.element,
            "index": self.index,
            "shift_ppm": self.shift_ppm,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OverlapReport:
    """One experimental peak hosting several signals (explicit overlap)."""

    nucleus: str
    element: str
    peak_index: int | None
    shift_ppm: float
    atom_labels: tuple[str, ...]
    total_atoms: int
    atom_capacity: int
    slots: int

    def to_dict(self) -> dict[str, object]:
        return {
            "nucleus": self.nucleus,
            "element": self.element,
            "peak_index": self.peak_index,
            "shift_ppm": self.shift_ppm,
            "atom_labels": list(self.atom_labels),
            "total_atoms": self.total_atoms,
            "atom_capacity": self.atom_capacity,
            "slots": self.slots,
        }


@dataclass(frozen=True)
class NearOverlapRegion:
    """Consecutive experimental peaks closer than ``overlap_window_ppm``."""

    element: str
    indices: tuple[int | None, ...]
    shifts: tuple[float, ...]
    width_ppm: float

    def to_dict(self) -> dict[str, object]:
        return {
            "element": self.element,
            "indices": list(self.indices),
            "shifts": list(self.shifts),
            "width_ppm": self.width_ppm,
        }


@dataclass(frozen=True)
class NearOptimalSolution:
    """One near-optimal alternative, honestly calibrated on its own pairs.

    ``cost`` is the total absolute calibrated residual under
    ``calibrations``; ``delta`` is ``cost − best_cost`` and may be negative
    when a different basin scores lower — the outcome never swaps its best
    solution based on that (a smaller objective is not chemical correctness).
    """

    pairs: tuple[MatchedSignal, ...]
    swapped: tuple[str, ...]
    cost: float
    delta: float
    calibrations: tuple[NucleusCalibration, ...]
    iterations: int
    forbidden: tuple[str, int | None]

    @property
    def assignment(self) -> tuple[tuple[str, str, int | None], ...]:
        return tuple(
            (pair.atom_label, pair.element, pair.peak_index)
            for pair in sorted(self.pairs, key=lambda p: (p.nucleus, p.atom_label))
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pairs": [pair.to_dict() for pair in self.pairs],
            "swapped": list(self.swapped),
            "cost": self.cost,
            "delta": self.delta,
            "calibrations": [cal.to_dict() for cal in self.calibrations],
            "iterations": self.iterations,
            "forbidden": [self.forbidden[0], self.forbidden[1]],
        }


@dataclass(frozen=True)
class IterativeAssignmentOutcome:
    """Typed result of :func:`run_iterative_assignment`."""

    status: AssignmentStatus
    iterations: int
    refit_performed: bool
    pairs: tuple[MatchedSignal, ...]
    calibrations: tuple[NucleusCalibration, ...]
    unmatched_signals: tuple[UnmatchedSignal, ...]
    unmatched_peaks: tuple[UnmatchedPeakRecord, ...]
    overlaps: tuple[OverlapReport, ...]
    near_overlap_regions: tuple[NearOverlapRegion, ...]
    near_optimal: tuple[NearOptimalSolution, ...]
    mismatch_count: int
    exchangeable_unobserved: int
    total_cost: float
    expected_signal_counts: tuple[tuple[str, int], ...] = ()

    def calibration_for(self, nucleus: str) -> NucleusCalibration:
        """Return the final calibration for *nucleus* (or raise)."""
        for calibration in self.calibrations:
            if calibration.nucleus == nucleus:
                return calibration
        raise IterativeAssignmentError(f"no calibration recorded for nucleus {nucleus!r}")

    def pairs_for(self, nucleus: str) -> tuple[MatchedSignal, ...]:
        """Return the final matched pairs of *nucleus*."""
        return tuple(pair for pair in self.pairs if pair.nucleus == nucleus)

    def assignment_map(self) -> dict[str, int | None]:
        """``{atom_label: peak_index}`` over the final assignment."""
        return {pair.atom_label: pair.peak_index for pair in self.pairs}

    def coverage(self) -> dict[str, tuple[int, int]]:
        """Per-nucleus ``(matched, total)`` signal coverage."""
        matched: dict[str, int] = {}
        for pair in self.pairs:
            matched[pair.nucleus] = matched.get(pair.nucleus, 0) + 1
        return {
            nucleus: (matched.get(nucleus, 0), total)
            for nucleus, total in self.expected_signal_counts
        }

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (all collections sorted for determinism)."""
        return {
            "status": self.status,
            "iterations": self.iterations,
            "refit_performed": self.refit_performed,
            "total_cost": self.total_cost,
            "mismatch_count": self.mismatch_count,
            "exchangeable_unobserved": self.exchangeable_unobserved,
            "calibrations": [cal.to_dict() for cal in self.calibrations],
            "pairs": [pair.to_dict() for pair in self.pairs],
            "unmatched_signals": [entry.to_dict() for entry in self.unmatched_signals],
            "unmatched_peaks": [entry.to_dict() for entry in self.unmatched_peaks],
            "overlaps": [entry.to_dict() for entry in self.overlaps],
            "near_overlap_regions": [entry.to_dict() for entry in self.near_overlap_regions],
            "near_optimal": [entry.to_dict() for entry in self.near_optimal],
            "coverage": {nucleus: [m, t] for nucleus, (m, t) in self.coverage().items()},
        }


@dataclass(frozen=True)
class AssignmentScore:
    """Guard-approved honest score of an assignment + its own calibration."""

    total_cost: float
    mae: float
    per_nucleus: tuple[tuple[str, float], ...]
    calibrations: tuple[NucleusCalibration, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_cost": self.total_cost,
            "mae": self.mae,
            "per_nucleus": {nucleus: cost for nucleus, cost in self.per_nucleus},
            "calibrations": [cal.to_dict() for cal in self.calibrations],
        }


def score_assignment(
    pairs: Sequence[MatchedSignal],
    calibrations: Sequence[NucleusCalibration],
) -> AssignmentScore:
    """Score *pairs* against *calibrations*, refusing stale reuse (anti-overfit).

    Every nucleus present in *pairs* must carry a calibration whose
    ``fitted_on`` fingerprint equals :func:`assignment_fingerprint` of that
    nucleus' pairs — otherwise :class:`CalibrationReuseError` is raised.  A
    re-permuted assignment must be refit (or re-converged) first; it may
    never be scored against the calibration of the assignment it replaced.
    """
    by_nucleus: dict[str, list[MatchedSignal]] = {}
    for pair in pairs:
        by_nucleus.setdefault(pair.nucleus, []).append(pair)
    calibration_by_nucleus = {cal.nucleus: cal for cal in calibrations}

    per_nucleus: list[tuple[str, float]] = []
    total = 0.0
    count = 0
    for nucleus in sorted(by_nucleus):
        group = by_nucleus[nucleus]
        calibration = calibration_by_nucleus.get(nucleus)
        if calibration is None:
            raise CalibrationReuseError(f"no calibration supplied for nucleus {nucleus!r}")
        fingerprint = assignment_fingerprint(group)
        if calibration.fitted_on != fingerprint:
            raise CalibrationReuseError(
                f"calibration for {nucleus!r} was fit on a different assignment "
                f"(fitted_on={calibration.fitted_on!r} != {fingerprint!r}); refit the "
                "calibration inside the iteration before scoring a re-permuted assignment"
            )
        cost = sum(abs(calibration.scaled(pair.calc_ppm) - pair.exp_ppm) for pair in group)
        per_nucleus.append((nucleus, cost))
        total += cost
        count += len(group)

    return AssignmentScore(
        total_cost=total,
        mae=(total / count if count else 0.0),
        per_nucleus=tuple(per_nucleus),
        calibrations=tuple(calibration_by_nucleus[nucleus] for nucleus in sorted(by_nucleus)),
    )


# ---------------------------------------------------------------------------
# internal nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SignalNode:
    atom_label: str
    element: str
    nucleus: str
    calc_ppm: float
    atom_count: int
    signal_capacity: int | None
    exchangeable: bool
    order: int


@dataclass(frozen=True)
class _PeakNode:
    element: str
    nucleus: str
    index: int | None
    shift_ppm: float
    multiplicity: int
    atom_capacity: int
    slots: int
    order: int


@dataclass(frozen=True)
class _SolveResult:
    matches: tuple[tuple[_SignalNode, _PeakNode], ...]
    unmatched_signals: tuple[_SignalNode, ...]
    forbidden: frozenset[tuple[str, int | None]]


@dataclass
class _NucleusResult:
    nucleus: str
    status: AssignmentStatus
    iterations: int
    matches: tuple[tuple[_SignalNode, _PeakNode], ...]
    unmatched_signals: tuple[_SignalNode, ...]
    forbidden: frozenset[tuple[str, int | None]]
    calibration: NucleusCalibration
    seed: CalibrationSeed
    signals: tuple[_SignalNode, ...]
    peaks: tuple[_PeakNode, ...]


def _peak_key(index: int | None) -> int:
    """Sort key that keeps ``None`` last and never collapses index 0."""
    return -1 if index is None else index


def _build_signals(
    atom_shifts: Iterable[AtomShift], exchangeable_atoms: frozenset[str]
) -> tuple[_SignalNode, ...]:
    nodes: list[_SignalNode] = []
    for order, shift in enumerate(atom_shifts):
        group = shift.signal_group
        atom_count = len(group.atom_uids) if group is not None else 1
        signal_capacity = group.peak_capacity if group is not None else None
        exchangeable = shift.atom_label in exchangeable_atoms or (
            group is not None and any(uid in exchangeable_atoms for uid in group.atom_uids)
        )
        nodes.append(
            _SignalNode(
                atom_label=shift.atom_label,
                element=normalize_symbol(shift.symbol),
                nucleus=shift.nucleus,
                calc_ppm=float(shift.shift_ppm),
                atom_count=atom_count,
                signal_capacity=signal_capacity,
                exchangeable=exchangeable,
                order=order,
            )
        )
    nodes.sort(key=lambda node: (node.nucleus, node.calc_ppm, node.atom_label, node.order))
    return tuple(nodes)


def _build_peaks(
    experiment: ExperimentalNmr,
    constraints: Sequence[PeakConstraint],
) -> tuple[_PeakNode, ...]:
    exact: dict[tuple[str, int], PeakConstraint] = {}
    element_wide: dict[str, PeakConstraint] = {}
    for constraint in constraints:
        element = normalize_symbol(constraint.element)
        if constraint.index is None:
            element_wide[element] = constraint
        else:
            exact[(element, constraint.index)] = constraint

    nodes: list[_PeakNode] = []
    order = 0
    for element, peaks in experiment.peaks.items():
        symbol = normalize_symbol(element)
        for position, peak in enumerate(peaks):
            index = peak.index if peak.index is not None else position
            constraint = exact.get((symbol, index), element_wide.get(symbol))
            atom_capacity = (
                constraint.atom_capacity
                if constraint is not None and constraint.atom_capacity is not None
                else max(1, int(peak.multiplicity))
            )
            slots = constraint.slots if constraint is not None else 1
            nodes.append(
                _PeakNode(
                    element=symbol,
                    nucleus=nucleus_label(symbol),
                    index=index,
                    shift_ppm=float(peak.shift_ppm),
                    multiplicity=int(peak.multiplicity),
                    atom_capacity=atom_capacity,
                    slots=slots,
                    order=order,
                )
            )
            order += 1
    nodes.sort(key=lambda node: (node.nucleus, _peak_key(node.index), node.shift_ppm, node.order))
    return tuple(nodes)


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------


def _calibrated_delta(
    signal: _SignalNode, peak: _PeakNode, calibration: NucleusCalibration
) -> float:
    return calibration.scaled(signal.calc_ppm) - peak.shift_ppm


def _edge_feasible(
    signal: _SignalNode,
    peak: _PeakNode,
    calibration: NucleusCalibration,
    config: IterativeAssignmentConfig,
    forbidden: frozenset[tuple[str, int | None]],
) -> bool:
    if (signal.atom_label, peak.index) in forbidden:
        return False
    if signal.atom_count > peak.atom_capacity:
        return False
    if config.mismatch_limit_ppm is not None:
        return abs(_calibrated_delta(signal, peak, calibration)) <= config.mismatch_limit_ppm
    return True


def _hungarian_matches(
    signals: Sequence[_SignalNode],
    peaks: Sequence[_PeakNode],
    calibration: NucleusCalibration,
    config: IterativeAssignmentConfig,
    forbidden: frozenset[tuple[str, int | None]],
) -> tuple[tuple[_SignalNode, _PeakNode], ...]:
    n_sig = len(signals)
    columns: list[_PeakNode] = [peak for peak in peaks for _ in range(peak.slots)]
    n_col = len(columns)
    size = max(n_sig, n_col)
    big_cost = 1.0e9
    cost = [[big_cost] * size for _ in range(size)]
    eps = config.tie_break_epsilon
    scale = float(max(1, n_sig * n_col))

    for i, signal in enumerate(signals):
        for j, peak in enumerate(columns):
            if not _edge_feasible(signal, peak, calibration, config, forbidden):
                continue
            cost[i][j] = abs(_calibrated_delta(signal, peak, calibration)) + eps * (
                (i * n_col + j) / scale
            )

    rows, cols = linear_sum_assignment(cost)
    matches: list[tuple[_SignalNode, _PeakNode]] = []
    for row, col in zip(rows, cols):
        if row >= n_sig or col >= n_col:
            continue
        if cost[row][col] >= big_cost:
            continue
        matches.append((signals[row], columns[col]))
    return tuple(matches)


def _solve_nucleus(
    signals: Sequence[_SignalNode],
    peaks: Sequence[_PeakNode],
    calibration: NucleusCalibration,
    config: IterativeAssignmentConfig,
    forbidden: frozenset[tuple[str, int | None]] = frozenset(),
) -> _SolveResult:
    """Capacity-aware assignment with bounded atom-budget repair.

    Overlap slots may let several signals target one peak; if the summed atom
    count exceeds the peak capacity, the worst-fitting assigned edge on that
    peak (largest absolute calibrated residual, ties by label) is forbidden
    and the assignment is re-solved — a bounded, deterministic cutting-plane
    loop inside the solver, not a post-hoc filter.
    """
    budget = len(signals) * max([peak.slots for peak in peaks], default=1) + len(signals) + 1
    active_forbidden = set(forbidden)
    matches = _hungarian_matches(signals, peaks, calibration, config, frozenset(active_forbidden))
    for _ in range(budget):
        load: dict[tuple[str, int | None], int] = {}
        peak_by_key: dict[tuple[str, int | None], _PeakNode] = {}
        for signal, peak in matches:
            key = (peak.element, peak.index)
            load[key] = load.get(key, 0) + signal.atom_count
            peak_by_key[key] = peak
        overloaded = [key for key, total in load.items() if total > peak_by_key[key].atom_capacity]
        if not overloaded:
            break
        changed = False
        for key in overloaded:
            peak = peak_by_key[key]
            assigned = [entry for entry in matches if (entry[1].element, entry[1].index) == key]
            worst = max(
                assigned,
                key=lambda entry: (
                    abs(_calibrated_delta(entry[0], peak, calibration)),
                    entry[0].atom_label,
                ),
            )
            edge = (worst[0].atom_label, peak.index)
            if edge not in active_forbidden:
                active_forbidden.add(edge)
                changed = True
        if not changed:
            break
        matches = _hungarian_matches(
            signals, peaks, calibration, config, frozenset(active_forbidden)
        )
    matched_labels = {signal.atom_label for signal, _ in matches}
    unmatched = tuple(signal for signal in signals if signal.atom_label not in matched_labels)
    return _SolveResult(
        matches=tuple(matches),
        unmatched_signals=unmatched,
        forbidden=frozenset(active_forbidden),
    )


def _match_records(
    matches: Sequence[tuple[_SignalNode, _PeakNode]],
    calibration: NucleusCalibration,
    in_overlap_region: frozenset[tuple[str, int | None]],
) -> tuple[MatchedSignal, ...]:
    per_peak: dict[tuple[str, int | None], int] = {}
    for _signal, peak in matches:
        key = (peak.element, peak.index)
        per_peak[key] = per_peak.get(key, 0) + 1
    records = []
    for signal, peak in matches:
        key = (peak.element, peak.index)
        scaled = calibration.scaled(signal.calc_ppm)
        records.append(
            MatchedSignal(
                nucleus=signal.nucleus,
                atom_label=signal.atom_label,
                element=signal.element,
                peak_index=peak.index,
                calc_ppm=signal.calc_ppm,
                exp_ppm=peak.shift_ppm,
                scaled_ppm=scaled,
                residual=scaled - peak.shift_ppm,
                atom_count=signal.atom_count,
                peak_capacity=peak.atom_capacity,
                signal_capacity=signal.signal_capacity,
                overlap=per_peak[key] > 1,
                in_overlap_region=key in in_overlap_region,
            )
        )
    records.sort(
        key=lambda record: (record.nucleus, record.atom_label, _peak_key(record.peak_index))
    )
    return tuple(records)


def _unmatched_reason(
    signal: _SignalNode,
    peaks: Sequence[_PeakNode],
    calibration: NucleusCalibration,
    config: IterativeAssignmentConfig,
    forbidden: frozenset[tuple[str, int | None]],
) -> str:
    if signal.exchangeable:
        return "exchangeable_unobserved"
    if not peaks:
        return "no_feasible_peak"
    within_limit = [
        peak
        for peak in peaks
        if config.mismatch_limit_ppm is None
        or abs(_calibrated_delta(signal, peak, calibration)) <= config.mismatch_limit_ppm
    ]
    if not within_limit:
        return "mismatch_limit"
    capacity_ok = [peak for peak in within_limit if signal.atom_count <= peak.atom_capacity]
    if not capacity_ok:
        return "capacity_mismatch"
    if any((signal.atom_label, peak.index) in forbidden for peak in capacity_ok):
        return "capacity_overlap_exceeded"
    return "no_available_peak"


def _near_overlap_regions(
    peaks: Sequence[_PeakNode], window_ppm: float
) -> tuple[NearOverlapRegion, ...]:
    if window_ppm <= 0 or len(peaks) < 2:
        return ()
    ordered = sorted(peaks, key=lambda peak: (peak.shift_ppm, peak.order))
    regions: list[NearOverlapRegion] = []
    current: list[_PeakNode] = [ordered[0]]
    for peak in ordered[1:]:
        if abs(peak.shift_ppm - current[-1].shift_ppm) <= window_ppm:
            current.append(peak)
            continue
        if len(current) > 1:
            regions.append(_region_of(current))
        current = [peak]
    if len(current) > 1:
        regions.append(_region_of(current))
    return tuple(regions)


def _region_of(cluster: Sequence[_PeakNode]) -> NearOverlapRegion:
    shifts = tuple(peak.shift_ppm for peak in cluster)
    return NearOverlapRegion(
        element=cluster[0].element,
        indices=tuple(peak.index for peak in cluster),
        shifts=shifts,
        width_ppm=max(shifts) - min(shifts),
    )


# ---------------------------------------------------------------------------
# iteration
# ---------------------------------------------------------------------------


def _converge_nucleus(
    nucleus: str,
    signals: Sequence[_SignalNode],
    peaks: Sequence[_PeakNode],
    seed: CalibrationSeed,
    config: IterativeAssignmentConfig,
    forbidden: frozenset[tuple[str, int | None]] = frozenset(),
) -> _NucleusResult:
    """Alternate assignment and internal recalibration until fixed point."""
    if not signals or not peaks:
        # nothing to solve; the seed calibration is all we can honestly report
        calibration = NucleusCalibration(
            nucleus=nucleus,
            slope=seed.slope,
            intercept=seed.intercept,
            source=CALIBRATION_EXTERNAL_SEED,
            fitted_on=assignment_fingerprint(()),
        )
        return _NucleusResult(
            nucleus=nucleus,
            status="converged",
            iterations=0,
            matches=(),
            unmatched_signals=tuple(signals),
            forbidden=frozenset(forbidden),
            calibration=calibration,
            seed=seed,
            signals=tuple(signals),
            peaks=tuple(peaks),
        )

    calibration = NucleusCalibration(
        nucleus=nucleus,
        slope=seed.slope,
        intercept=seed.intercept,
        source=CALIBRATION_EXTERNAL_SEED,
        fitted_on="",
    )
    solution = _solve_nucleus(signals, peaks, calibration, config, forbidden)
    previous_edges: frozenset[tuple[str, int | None]] | None = None
    iterations = 1
    status: AssignmentStatus = "max_iterations"
    while True:
        edges = frozenset((signal.atom_label, peak.index) for signal, peak in solution.matches)
        if edges == previous_edges:
            status = "converged"
            break
        if iterations >= config.max_iterations:
            status = "max_iterations"
            break
        if solution.matches:
            records = _match_records(solution.matches, calibration, frozenset())
            new_calibration = refit_calibration(nucleus, records)
            moved = max(
                abs(new_calibration.slope - calibration.slope),
                abs(new_calibration.intercept - calibration.intercept),
            )
            calibration = new_calibration
            # after at least one full alternation, a calibration that no longer
            # moves counts as numerically converged (secondary tolerance stop)
            if iterations >= 2 and moved <= config.calibration_delta:
                status = "converged"
                break
        previous_edges = edges
        iterations += 1
        solution = _solve_nucleus(signals, peaks, calibration, config, forbidden)

    return _NucleusResult(
        nucleus=nucleus,
        status=status,
        iterations=iterations,
        matches=solution.matches,
        unmatched_signals=solution.unmatched_signals,
        forbidden=solution.forbidden,
        calibration=calibration,
        seed=seed,
        signals=tuple(signals),
        peaks=tuple(peaks),
    )


def _finalize_calibration_consistency(result: _NucleusResult) -> NucleusCalibration:
    """Guarantee the returned calibration is fit on the returned pairs."""
    return refit_calibration(
        result.nucleus, _match_records(result.matches, result.calibration, frozenset())
    )


# ---------------------------------------------------------------------------
# near-optima
# ---------------------------------------------------------------------------


def _near_optima(
    results: dict[str, _NucleusResult],
    pairs: tuple[MatchedSignal, ...],
    calibrations: tuple[NucleusCalibration, ...],
    config: IterativeAssignmentConfig,
) -> tuple[NearOptimalSolution, ...]:
    """Single-forbidden-edge alternatives, each re-converged with its own refit."""
    if config.top_k <= 0 or len(pairs) < 2:
        return ()
    best_map = {pair.atom_label: pair.peak_index for pair in pairs}
    best_cost = sum(abs(pair.residual) for pair in pairs)
    calibration_by_nucleus = {cal.nucleus: cal for cal in calibrations}
    seen: set[tuple[tuple[str, str, int | None], ...]] = {
        tuple(sorted((pair.nucleus, pair.atom_label, pair.peak_index) for pair in pairs))
    }
    candidates: list[NearOptimalSolution] = []
    for pair in pairs:
        result = results[pair.nucleus]
        alternative = _converge_nucleus(
            result.nucleus,
            result.signals,
            result.peaks,
            result.seed,
            config,
            forbidden=frozenset({(pair.atom_label, pair.peak_index)}),
        )
        if {signal.atom_label for signal, _ in alternative.matches} != {
            signal.atom_label for signal, _ in result.matches
        }:
            # coverage substitution is not a near-optimal assignment: dropping
            # or adding evidence would change which signals carry the fit
            continue
        if alternative.matches:
            alternative.calibration = _finalize_calibration_consistency(alternative)

        merged: list[MatchedSignal] = [other for other in pairs if other.nucleus != result.nucleus]
        merged.extend(_match_records(alternative.matches, alternative.calibration, frozenset()))
        merged.sort(
            key=lambda record: (record.nucleus, record.atom_label, _peak_key(record.peak_index))
        )
        key = tuple(
            sorted((record.nucleus, record.atom_label, record.peak_index) for record in merged)
        )
        if key in seen:
            continue
        seen.add(key)
        new_map = {record.atom_label: record.peak_index for record in merged}
        changed_labels = set(best_map) | set(new_map)
        swapped = tuple(
            sorted(label for label in changed_labels if best_map.get(label) != new_map.get(label))
        )
        alternative_calibrations = tuple(
            (
                alternative.calibration
                if record_nucleus == result.nucleus
                else calibration_by_nucleus[record_nucleus]
            )
            for record_nucleus in sorted({record.nucleus for record in merged})
        )
        if any(
            not math.isfinite(cal.slope) or cal.slope <= 0.0 for cal in alternative_calibrations
        ):
            # a non-positive slope inverts the NMR shielding/ shift relation —
            # an inadmissible basin, never a near-optimal assignment
            continue
        cost = sum(abs(record.residual) for record in merged)
        candidates.append(
            NearOptimalSolution(
                pairs=tuple(merged),
                swapped=swapped,
                cost=cost,
                delta=cost - best_cost,
                calibrations=alternative_calibrations,
                iterations=alternative.iterations,
                forbidden=(pair.atom_label, pair.peak_index),
            )
        )
    candidates.sort(key=lambda candidate: (candidate.cost, candidate.assignment))
    return tuple(candidates[: config.top_k])


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def run_iterative_assignment(
    atom_shifts: Iterable[AtomShift],
    experiment: ExperimentalNmr,
    *,
    seed: Iterable[CalibrationSeed] = (),
    peak_constraints: Sequence[PeakConstraint] = (),
    exchangeable_atoms: Iterable[str] = (),
    config: IterativeAssignmentConfig | None = None,
) -> IterativeAssignmentOutcome:
    """Run the seeded assignment↔calibration iteration (G11, todo 46).

    Args:
        atom_shifts: Computed signals (one per atom / equivalence group); a
            signal's ``SignalGroup.atom_uids`` define its atom count and
            ``peak_capacity`` its declared peak budget.
        experiment: Experimental peaks with integral multiplicities.
        seed: External initial calibration per nucleus (identity when absent).
        peak_constraints: Explicit capacity/overlap declarations per peak.
        exchangeable_atoms: Atom labels/uids expected to be unobservable
            (OH/NH/SH); unmatched ones are diagnosed, not counted as mismatch.
        config: Termination and modelling parameters.

    Returns:
        Typed :class:`IterativeAssignmentOutcome` — status ``converged`` /
        ``max_iterations`` / ``degenerate`` (no pairs at all), the matched
        pairs with calibrated residuals, per-nucleus calibrations, unmatched
        signal/peak diagnostics, overlap and near-overlap reports, and the
        near-optimal alternatives (each with its own refit calibration).
    """
    cfg = config if config is not None else IterativeAssignmentConfig()
    seeds: dict[str, CalibrationSeed] = {}
    for entry in seed:
        if not isinstance(entry, CalibrationSeed):
            raise TypeError(f"seed entries must be CalibrationSeed, got {type(entry).__name__}")
        seeds[entry.nucleus] = entry

    signals = _build_signals(atom_shifts, frozenset(exchangeable_atoms))
    peaks = _build_peaks(experiment, tuple(peak_constraints))

    nuclei = sorted({node.nucleus for node in signals} | {peak.nucleus for peak in peaks})
    results: dict[str, _NucleusResult] = {}
    for nucleus in nuclei:
        nucleus_signals = tuple(node for node in signals if node.nucleus == nucleus)
        nucleus_peaks = tuple(peak for peak in peaks if peak.nucleus == nucleus)
        seed_calibration = seeds.get(nucleus, CalibrationSeed(nucleus))
        results[nucleus] = _converge_nucleus(
            nucleus, nucleus_signals, nucleus_peaks, seed_calibration, cfg
        )

    # final calibration consistency: refit on the final pairs so the guard in
    # score_assignment() always accepts the engine's own outcome
    for result in results.values():
        if result.matches:
            result.calibration = _finalize_calibration_consistency(result)

    region_keys: dict[str, frozenset[tuple[str, int | None]]] = {}
    regions: list[NearOverlapRegion] = []
    for nucleus in nuclei:
        nucleus_regions = _near_overlap_regions(results[nucleus].peaks, cfg.overlap_window_ppm)
        regions.extend(nucleus_regions)
        keys: set[tuple[str, int | None]] = set()
        for region in nucleus_regions:
            keys.update((region.element, index) for index in region.indices)
        region_keys[nucleus] = frozenset(keys)

    pairs: list[MatchedSignal] = []
    unmatched_signals: list[UnmatchedSignal] = []
    unmatched_peaks: list[UnmatchedPeakRecord] = []
    overlaps: list[OverlapReport] = []
    iterations = 0
    status: AssignmentStatus = "converged"

    for nucleus in nuclei:
        result = results[nucleus]
        iterations = max(iterations, result.iterations)
        if result.status == "max_iterations":
            status = "max_iterations"
        records = _match_records(result.matches, result.calibration, region_keys[nucleus])
        pairs.extend(records)

        matched_keys = {(record.element, record.peak_index) for record in records}
        load: dict[tuple[str, int | None], list[str]] = {}
        for record in records:
            load.setdefault((record.element, record.peak_index), []).append(record.atom_label)
        for (element, index), labels in sorted(
            load.items(), key=lambda item: (item[0][0], _peak_key(item[0][1]))
        ):
            if len(labels) <= 1:
                continue
            peak_node = next(
                peak for peak in result.peaks if (peak.element, peak.index) == (element, index)
            )
            matching_records = [
                record
                for record in records
                if (record.element, record.peak_index) == (element, index)
            ]
            overlaps.append(
                OverlapReport(
                    nucleus=nucleus,
                    element=element,
                    peak_index=index,
                    shift_ppm=peak_node.shift_ppm,
                    atom_labels=tuple(sorted(labels)),
                    total_atoms=sum(record.atom_count for record in matching_records),
                    atom_capacity=peak_node.atom_capacity,
                    slots=peak_node.slots,
                )
            )

        for signal in result.unmatched_signals:
            reason = _unmatched_reason(
                signal, result.peaks, result.calibration, cfg, result.forbidden
            )
            unmatched_signals.append(
                UnmatchedSignal(
                    nucleus=signal.nucleus,
                    atom_label=signal.atom_label,
                    element=signal.element,
                    calc_ppm=signal.calc_ppm,
                    atom_count=signal.atom_count,
                    reason=reason,
                    exchangeable=signal.exchangeable,
                )
            )
        for peak in result.peaks:
            if (peak.element, peak.index) not in matched_keys:
                unmatched_peaks.append(
                    UnmatchedPeakRecord(
                        nucleus=nucleus,
                        element=peak.element,
                        index=peak.index,
                        shift_ppm=peak.shift_ppm,
                        reason="no_signal",
                    )
                )

    pairs.sort(key=lambda record: (record.nucleus, record.atom_label, _peak_key(record.peak_index)))
    unmatched_signals.sort(key=lambda entry: (entry.nucleus, entry.atom_label))
    unmatched_peaks.sort(key=lambda entry: (entry.nucleus, _peak_key(entry.index)))
    overlaps.sort(key=lambda entry: (entry.nucleus, _peak_key(entry.peak_index)))
    regions.sort(key=lambda region: (region.element, region.indices))
    calibrations = tuple(
        sorted((result.calibration for result in results.values()), key=lambda cal: cal.nucleus)
    )
    pair_tuple = tuple(pairs)
    total_cost = sum(abs(record.residual) for record in pair_tuple)
    mismatches = sum(1 for entry in unmatched_signals if not entry.exchangeable)
    exchangeable_unobserved = sum(1 for entry in unmatched_signals if entry.exchangeable)
    expected_counts = tuple(
        (nucleus, sum(1 for node in signals if node.nucleus == nucleus)) for nucleus in nuclei
    )

    near_optimal = _near_optima(results, pair_tuple, calibrations, cfg)

    if not pair_tuple:
        status = "degenerate"

    return IterativeAssignmentOutcome(
        status=status,
        iterations=iterations,
        refit_performed=any(cal.source == CALIBRATION_INTERNAL_REFIT for cal in calibrations),
        pairs=pair_tuple,
        calibrations=calibrations,
        unmatched_signals=tuple(unmatched_signals),
        unmatched_peaks=tuple(unmatched_peaks),
        overlaps=tuple(overlaps),
        near_overlap_regions=tuple(regions),
        near_optimal=near_optimal,
        mismatch_count=mismatches,
        exchangeable_unobserved=exchangeable_unobserved,
        total_cost=total_cost,
        expected_signal_counts=expected_counts,
    )


__all__ = [
    "ASSIGNMENT_STATUSES",
    "AssignmentScore",
    "AssignmentStatus",
    "CALIBRATION_EXTERNAL_SEED",
    "CALIBRATION_INSUFFICIENT",
    "CALIBRATION_INTERNAL_REFIT",
    "CALIBRATION_SOURCES",
    "CalibrationReuseError",
    "CalibrationSeed",
    "IterativeAssignmentConfig",
    "IterativeAssignmentError",
    "IterativeAssignmentOutcome",
    "MatchedSignal",
    "NearOptimalSolution",
    "NearOverlapRegion",
    "NucleusCalibration",
    "OverlapReport",
    "PeakConstraint",
    "UnmatchedPeakRecord",
    "UnmatchedSignal",
    "assignment_fingerprint",
    "refit_calibration",
    "run_iterative_assignment",
    "score_assignment",
]
