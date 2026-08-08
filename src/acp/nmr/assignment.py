# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Assignment matching (DevDoc §5 stage 5 / §8.3).

Two paths:

* **Assigned** — experimental peaks carry explicit atom labels; the
  (calc, exp) pairs are read off directly.
* **Unassigned** — peaks are matched to computed signals via the
  Hungarian algorithm on a cost matrix ``C[g, p] = w_p · |δ_calc(g) − δ_exp(p)|``
  where ``g`` indexes equivalence groups (signals) and ``p`` indexes
  experimental peaks. ``w_p`` is the per-peak intensity weight
  (multiplicity / max multiplicity); defaults to 1 (equal weight).
"""

from __future__ import annotations

import logging

from scipy.optimize import linear_sum_assignment

from acp.nmr.models import (
    AtomShift,
    ExperimentalNmr,
    ExperimentalPeak,
    normalize_symbol,
)

logger = logging.getLogger(__name__)


def match_assigned(
    atom_shifts: list[AtomShift],
    experiment: ExperimentalNmr,
) -> dict[str, list[tuple[AtomShift, ExperimentalPeak]]]:
    """Pair computed shifts with explicitly-assigned experimental peaks.

    Returns a dict ``{nucleus: [(shift, peak), ...]}``. Atom labels that
    appear in ``experiment.omit_atoms`` are dropped.
    """
    omit = {label for label in experiment.omit_atoms}
    by_label: dict[str, AtomShift] = {s.atom_label: s for s in atom_shifts}

    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]] = {}
    for element, peaks in experiment.peaks.items():
        nucleus = _nucleus_of_element(element)
        group: list[tuple[AtomShift, ExperimentalPeak]] = []
        for peak in peaks:
            if peak.atom_label is None:
                continue
            if peak.atom_label in omit:
                continue
            shift = by_label.get(peak.atom_label)
            if shift is None:
                logger.warning(
                    "Assigned label %s not found in computed atoms; skipping",
                    peak.atom_label,
                )
                continue
            group.append((shift, peak))
        if group:
            pairs[nucleus] = group
    return pairs


def match_unassigned(
    atom_shifts: list[AtomShift],
    experiment: ExperimentalNmr,
    use_intensity_weight: bool = True,
) -> dict[str, list[tuple[AtomShift, ExperimentalPeak]]]:
    """Match computed signals to experimental peaks via Hungarian assignment.

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
        group = _hungarian_match_one(signals, peaks, use_intensity_weight)
        if group:
            pairs[nucleus] = group
    return pairs


def _hungarian_match_one(
    signals: list[AtomShift],
    peaks: list[ExperimentalPeak],
    use_intensity_weight: bool,
) -> list[tuple[AtomShift, ExperimentalPeak]]:
    """Run the Hungarian algorithm on one nucleus' signals/peaks."""
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
    for r, c in zip(row_ind, col_ind):
        if r >= n_sig or c >= n_peak:
            continue  # dummy row/column
        if cost[r][c] >= big_cost:
            continue  # still a dummy pairing
        matched.append((signals[r], peaks[c]))
    return matched


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

    Returns ``{nucleus: {"labels", "elements", "calc", "exp"}}`` ready
    to feed :func:`acp.nmr.scaling.fit_regression`.
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
        }
    return out


__all__ = [
    "match_assigned",
    "match_unassigned",
    "collect_residual_inputs",
]
