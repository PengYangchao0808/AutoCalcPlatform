# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Assignment matching (DevDoc §5 stage 5 / §8.3), two-phase (todo 7 / G02).

* **Phase 1 — lock**: experimental peaks that carry one explicit atom label
  are paired to that computed signal unconditionally (subject to OMIT).
  Unknown labels and ambiguous candidate sets never enter matching; they are
  recorded as :class:`UnmatchedPeak` diagnostics.
* **Phase 2 — match**: the remaining unlabeled peaks are matched to the
  still-free signals via the Hungarian algorithm on the cost matrix
  ``C[g, p] = w_p · |δ_calc(g) − δ_exp(p)|`` (``w_p`` = per-peak intensity
  weight, multiplicity / max multiplicity; defaults to 1). Best-2
  alternatives and their cost deltas are kept in
  :attr:`AssignmentResult.near_optimal` (G11).

Every input peak ends up exactly once in ``pairs`` or ``unmatched`` —
peaks are never silently dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from scipy.optimize import linear_sum_assignment

from acp.nmr.models import (
    AtomShift,
    ExperimentalNmr,
    ExperimentalPeak,
    normalize_symbol,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnmatchedPeak:
    """An experimental peak that did not reach ``pairs`` — diagnostic record.

    Attributes:
        element: Peak element symbol (``"C"`` / ``"H"`` / ...).
        index: Per-element peak position (``ExperimentalPeak.index``).
        shift_ppm: Experimental chemical shift.
        reason: One of ``"unknown_label"`` (label has no computed atom),
            ``"omit_atom"`` (dropped via OMIT), ``"ambiguous_label"``
            (candidate set — never locked), ``"no_computed_signal"``
            (element has no computed signals) or ``"no_available_signal"``
            (phase-2 capacity shortfall).
        atom_label: Explicit label when relevant (unknown/omit), else ``None``.
        label_candidates: Candidate labels of an ambiguous peak.
    """

    element: str
    index: int | None
    shift_ppm: float
    reason: str
    atom_label: str | None = None
    label_candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class NearOptimalAlternative:
    """Second-best phase-2 assignment for one nucleus (G11 near-optimal set).

    Attributes:
        nucleus: Nucleus label (``"13C"``, ``"1H"``, ...).
        cost: Total weighted cost of the alternative assignment.
        delta: ``cost − best_cost`` (≥ 0; 0.0 means a tied optimum).
        assignment: ``(atom_label, element, peak_index)`` triples.
    """

    nucleus: str
    cost: float
    delta: float
    assignment: tuple[tuple[str, str, int | None], ...]


@dataclass(frozen=True)
class AssignmentResult:
    """Two-phase assignment outcome (todo 7 / G02).

    Attributes:
        pairs: ``{nucleus: [(signal, peak), ...]}`` — locked plus phase-2
            pairs, ready for :func:`collect_residual_inputs`.
        locked: Phase-1 subset of ``pairs`` (explicit-label locks only).
        unmatched: Peaks absent from ``pairs``; every input peak appears
            exactly once in ``pairs`` or ``unmatched``.
        near_optimal: Best-2 alternatives per phase-2 matching, with deltas.
        scale_iterations: Assignment↔calibration passes reflected by this
            result; the single-pass two-phase matcher reports 1 (iterative
            assignment grows this until convergence).
    """

    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]]
    locked: dict[str, list[tuple[AtomShift, ExperimentalPeak]]]
    unmatched: list[UnmatchedPeak]
    near_optimal: list[NearOptimalAlternative]
    scale_iterations: int = 1

    def peak_classification(self) -> dict[tuple[str, int | None], str]:
        """``{(element, peak_index): "locked" | "pair" | "unmatched"}``."""
        out: dict[tuple[str, int | None], str] = {}
        for group in self.locked.values():
            for _, peak in group:
                out[(peak.element, peak.index)] = "locked"
        for group in self.pairs.values():
            for _, peak in group:
                out.setdefault((peak.element, peak.index), "pair")
        for entry in self.unmatched:
            out[(entry.element, entry.index)] = "unmatched"
        return out


def match_assigned(
    atom_shifts: list[AtomShift],
    experiment: ExperimentalNmr,
    use_intensity_weight: bool = True,
) -> AssignmentResult:
    """Two-phase assignment: lock explicit labels, Hungarian-match the rest.

    Phase 1 locks every uniquely labeled peak to its computed atom signal
    unconditionally (OMIT labels are diagnosed instead of matched); unknown
    labels and ambiguous candidate sets never enter matching and are recorded
    in ``unmatched``. Phase 2 runs the Hungarian algorithm — same weighted
    cost as :func:`match_unassigned` — on the remaining unlabeled peaks
    against the still-free signals; leftover peaks (missing signals or
    capacity shortfall) are recorded, never dropped.

    Args:
        atom_shifts: Computed shifts (one per atom / equivalence rep).
        experiment: Experimental peaks — any mix of labeled and unlabeled.
        use_intensity_weight: Phase-2 intensity weighting (see
            :func:`match_unassigned`). Defaults to ``True``.

    Returns:
        The full :class:`AssignmentResult` (pairs, locked, unmatched,
        near_optimal, scale_iterations).
    """
    omit = set(experiment.omit_atoms)
    by_label: dict[str, AtomShift] = {s.atom_label: s for s in atom_shifts}

    locked: dict[str, list[tuple[AtomShift, ExperimentalPeak]]] = {}
    locked_labels: dict[str, set[str]] = {}
    remaining: dict[str, list[ExperimentalPeak]] = {}
    unmatched: list[UnmatchedPeak] = []

    for element, peaks in experiment.peaks.items():
        nucleus = _nucleus_of_element(element)
        rest: list[ExperimentalPeak] = []
        for peak in peaks:
            candidates = tuple(peak.label_candidates or ())
            if len(candidates) > 1:
                unmatched.append(
                    UnmatchedPeak(
                        element=element,
                        index=peak.index,
                        shift_ppm=peak.shift_ppm,
                        reason="ambiguous_label",
                        label_candidates=candidates,
                    )
                )
                continue
            if peak.atom_label is None:
                rest.append(peak)
                continue
            if peak.atom_label in omit:
                unmatched.append(
                    UnmatchedPeak(
                        element=element,
                        index=peak.index,
                        shift_ppm=peak.shift_ppm,
                        reason="omit_atom",
                        atom_label=peak.atom_label,
                    )
                )
                continue
            shift = by_label.get(peak.atom_label)
            if shift is None or shift.nucleus != nucleus:
                logger.warning(
                    "Assigned label %s does not resolve to a computed %s atom; "
                    "peak %s[%s] recorded as unmatched",
                    peak.atom_label,
                    nucleus,
                    element,
                    peak.index,
                )
                unmatched.append(
                    UnmatchedPeak(
                        element=element,
                        index=peak.index,
                        shift_ppm=peak.shift_ppm,
                        reason="unknown_label",
                        atom_label=peak.atom_label,
                    )
                )
                continue
            locked.setdefault(nucleus, []).append((shift, peak))
            locked_labels.setdefault(nucleus, set()).add(shift.atom_label)
        if rest:
            remaining[nucleus] = rest

    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]] = {
        nucleus: list(group) for nucleus, group in locked.items()
    }
    near_optimal: list[NearOptimalAlternative] = []
    for nucleus, rest_peaks in remaining.items():
        free_labels = locked_labels.get(nucleus, set())
        signals = [
            s for s in atom_shifts if s.nucleus == nucleus and s.atom_label not in free_labels
        ]
        if not signals:
            unmatched.extend(
                UnmatchedPeak(
                    element=p.element,
                    index=p.index,
                    shift_ppm=p.shift_ppm,
                    reason="no_computed_signal",
                )
                for p in rest_peaks
            )
            continue
        matched, missed, alternatives = _hungarian_solve(
            signals, rest_peaks, use_intensity_weight, nucleus
        )
        if matched:
            pairs.setdefault(nucleus, []).extend(matched)
        unmatched.extend(
            UnmatchedPeak(
                element=p.element,
                index=p.index,
                shift_ppm=p.shift_ppm,
                reason="no_available_signal",
            )
            for p in missed
        )
        near_optimal.extend(alternatives)

    return AssignmentResult(
        pairs=pairs,
        locked=locked,
        unmatched=unmatched,
        near_optimal=near_optimal,
        scale_iterations=1,
    )


def match_unassigned(
    atom_shifts: list[AtomShift],
    experiment: ExperimentalNmr,
    use_intensity_weight: bool = True,
) -> dict[str, list[tuple[AtomShift, ExperimentalPeak]]]:
    """Match computed signals to experimental peaks via Hungarian assignment.

    Legacy single-phase API (kept for existing callers); it returns only the
    matched pairs and reports no diagnostics — prefer :func:`match_assigned`,
    which locks known labels first and records every unmatched peak.

    Each computed ``AtomShift`` is treated as one "signal" (the caller
    has already collapsed equivalence groups, so each group is
    represented once). The cost matrix is::

        C[i, j] = w_j · |δ_calc[i] − δ_exp[j]|

    where ``w_j`` is the per-peak intensity weight (multiplicity divided
    by the max multiplicity across peaks of the same element). When the
    matrix is rectangular, dummy rows/columns with a large cost are
    appended so :func:`linear_sum_assignment` always produces a full
    bijection; dummy pairs are dropped from the result.

    Args:
        atom_shifts: Computed shifts (one per atom / equivalence rep).
        experiment: Experimental peaks (without atom labels).
        use_intensity_weight: When ``True``, weight by peak multiplicity
            (CH3 ≈ 3×). Defaults to ``True`` per DevDoc §8.3.
    """
    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]] = {}
    for element, peaks in experiment.peaks.items():
        nucleus = _nucleus_of_element(element)
        signals = [s for s in atom_shifts if s.nucleus == nucleus]
        if not signals or not peaks:
            continue
        group, _missed, _near = _hungarian_solve(signals, peaks, use_intensity_weight, nucleus)
        if group:
            pairs[nucleus] = group
    return pairs


def _hungarian_solve(
    signals: list[AtomShift],
    peaks: list[ExperimentalPeak],
    use_intensity_weight: bool,
    nucleus: str,
) -> tuple[
    list[tuple[AtomShift, ExperimentalPeak]],
    list[ExperimentalPeak],
    list[NearOptimalAlternative],
]:
    """Run the Hungarian algorithm on one nucleus' signals/peaks.

    Returns ``(matched, missed_peaks, near_optimal)``: ``matched`` holds the
    (signal, peak) pairs, ``missed_peaks`` are peaks that only won dummy
    columns (capacity shortfall), and ``near_optimal`` carries the best-2
    alternative assignment with its cost delta.
    """
    n_sig = len(signals)
    n_peak = len(peaks)
    size = max(n_sig, n_peak)

    if use_intensity_weight:
        max_mult = max((p.multiplicity for p in peaks), default=1) or 1
        weights = [(p.multiplicity / max_mult) for p in peaks]
    else:
        weights = [1.0] * n_peak

    big_cost = 1.0e6
    cost = [[big_cost] * size for _ in range(size)]
    for i, signal in enumerate(signals):
        for j, peak in enumerate(peaks):
            cost[i][j] = weights[j] * abs(signal.shift_ppm - peak.shift_ppm)

    row_ind, col_ind = linear_sum_assignment(cost)

    matched: list[tuple[AtomShift, ExperimentalPeak]] = []
    real_edges: list[tuple[int, int]] = []
    used_cols: set[int] = set()
    for r, c in zip(row_ind, col_ind):
        if r >= n_sig or c >= n_peak:
            continue  # dummy row/column
        if cost[r][c] >= big_cost:
            continue  # still a dummy pairing
        matched.append((signals[r], peaks[c]))
        real_edges.append((r, c))
        used_cols.add(c)

    missed = [peaks[c] for c in range(n_peak) if c not in used_cols]
    near = _second_best_alternative(cost, signals, peaks, real_edges, big_cost, nucleus)
    return matched, missed, near


def _second_best_alternative(
    cost: list[list[float]],
    signals: list[AtomShift],
    peaks: list[ExperimentalPeak],
    real_edges: list[tuple[int, int]],
    big_cost: float,
    nucleus: str,
) -> list[NearOptimalAlternative]:
    """Best-2 phase-2 assignment: min total cost excluding one optimal edge.

    The second-best assignment must differ from the optimum in at least one
    edge, so it is the min over forbidding each optimal edge of the re-solved
    optimum. Solutions that lose real pairings (capacity changes) are
    discarded; returns a single entry, or ``[]`` when no alternative exists.
    """
    if len(real_edges) < 2:
        return []
    best_cost = sum(cost[r][c] for r, c in real_edges)
    best_alt: tuple[float, list[tuple[int, int]]] | None = None
    for forbidden_row, forbidden_col in real_edges:
        saved = cost[forbidden_row][forbidden_col]
        cost[forbidden_row][forbidden_col] = 2.0 * big_cost
        rows, cols = linear_sum_assignment(cost)
        cost[forbidden_row][forbidden_col] = saved
        edges = [
            (r, c)
            for r, c in zip(rows, cols)
            if r < len(signals)
            and c < len(peaks)
            and (r, c) != (forbidden_row, forbidden_col)
            and cost[r][c] < big_cost
        ]
        if len(edges) != len(real_edges):
            continue
        total = sum(cost[r][c] for r, c in edges)
        if best_alt is None or total < best_alt[0]:
            best_alt = (total, edges)
    if best_alt is None:
        return []
    assignment = tuple(
        (signals[r].atom_label, peaks[c].element, peaks[c].index) for r, c in sorted(best_alt[1])
    )
    return [
        NearOptimalAlternative(
            nucleus=nucleus,
            cost=best_alt[0],
            delta=best_alt[0] - best_cost,
            assignment=assignment,
        )
    ]


def _nucleus_of_element(element: str) -> str:
    """Return the canonical nucleus label for an element symbol.

    A 2-letter / 1-letter element symbol maps to the most common NMR
    nucleus: H→1H, C→13C, N→15N, F→19F, P→31P.
    """
    sym = normalize_symbol(element)
    defaults = {"H": "1H", "C": "13C", "N": "15N", "F": "19F", "P": "31P"}
    return defaults.get(sym, f"1{sym}")


def collect_residual_inputs(
    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]],
) -> dict[str, dict[str, list]]:
    """Flatten matched pairs into parallel arrays per nucleus.

    Returns ``{nucleus: {"labels", "elements", "calc", "exp",
    "signal_groups"}}`` ready to feed :func:`acp.nmr.scaling.fit_regression`.
    ``signal_groups`` is parallel to the other arrays — one
    :class:`~acp.nmr.models.SignalGroup` per row (``None`` for legacy
    hand-built shifts) so DP4 and DP5 share one signal definition per group
    (todo 34 / G08).
    """
    out: dict[str, dict[str, list]] = {}
    for nucleus, group in pairs.items():
        if not group:
            continue
        out[nucleus] = {
            "labels": [s.atom_label for s, _ in group],
            "elements": [s.symbol for s, _ in group],
            "calc": [s.shift_ppm for s, _ in group],
            "exp": [p.shift_ppm for _, p in group],
            "signal_groups": [s.signal_group for s, _ in group],
        }
    return out


__all__ = [
    "AssignmentResult",
    "NearOptimalAlternative",
    "UnmatchedPeak",
    "match_assigned",
    "match_unassigned",
    "collect_residual_inputs",
]
