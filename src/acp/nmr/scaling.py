# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Linear-regression scaling (DevDoc §5 stage 6 / §8.4).

Per-nucleus ordinary-least-squares fit ``δ_exp = slope · δ_calc + intercept``
with residuals ``r = δ_exp − δ_scaled`` (:func:`fit_regression`) or the
Goodman internal-scaling fit ``δ_calc = slope · δ_exp + intercept`` with
residuals ``r = δ_scaled − δ_exp`` (:func:`fit_scaling_goodman`). The
Goodman regression absorbs the constant TMS / solvent offset so the
downstream DP4/DP5 likelihood is insensitive to systematic shielding
offsets (Goodman InternalScaling).

Two goodness-of-fit numbers exist and must never be conflated
(:func:`regression_r_squared` / :func:`prediction_r_squared`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np

from acp.nmr.models import Assignment, RegressionResult, SignalGroup

logger = logging.getLogger(__name__)


def fit_regression(
    calc_ppm: list[float],
    exp_ppm: list[float],
    nucleus: str,
) -> tuple[RegressionResult, list[float], list[float]]:
    """Fit ``δ_exp = slope · δ_calc + intercept`` (DevDoc §8.4).

    Args:
        calc_ppm: Computed shifts (post TMS conversion).
        exp_ppm: Matched experimental shifts (same length).
        nucleus: Nucleus label for the :class:`RegressionResult`.

    Returns:
        ``(regression, scaled_ppm, residuals)``. On degenerate input
        (fewer than 2 pairs) the identity fit (slope=1, intercept=0) is
        returned so the caller still gets a usable residual vector.
    """
    if len(calc_ppm) != len(exp_ppm):
        raise ValueError(f"calc/exp length mismatch: {len(calc_ppm)} != {len(exp_ppm)}")

    n = len(calc_ppm)
    if n == 0:
        return (
            RegressionResult(nucleus=nucleus, slope=1.0, intercept=0.0, r_squared=0.0, mae=0.0),
            [],
            [],
        )

    x = np.asarray(calc_ppm, dtype=np.float64)
    y = np.asarray(exp_ppm, dtype=np.float64)

    if n < 2 or np.allclose(x, x[0]):
        # degenerate: no slope information — identity fit, residual = y - x
        residuals = (y - x).tolist()
        mae = float(np.mean(np.abs(residuals))) if residuals else 0.0
        scaled = x.tolist()
        return (
            RegressionResult(nucleus=nucleus, slope=1.0, intercept=0.0, r_squared=0.0, mae=mae),
            scaled,
            residuals,
        )

    slope, intercept = np.polyfit(x, y, 1).tolist()
    scaled = slope * x + intercept
    residuals = y - scaled

    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(residuals)))

    return (
        RegressionResult(
            nucleus=nucleus,
            slope=float(slope),
            intercept=float(intercept),
            r_squared=r_squared,
            mae=mae,
        ),
        scaled.tolist(),
        residuals.tolist(),
    )


def build_assignments(
    atom_labels: list[str],
    elements: list[str],
    exp_ppm: list[float],
    calc_ppm: list[float],
    scaled_ppm: list[float],
    residuals: list[float],
    signal_groups: Sequence[SignalGroup | None] | None = None,
) -> list[Assignment]:
    """Assemble :class:`Assignment` rows from parallel arrays.

    ``signal_groups`` is optional and additive (todo 34 / G08): when given
    it must be parallel to the other arrays and each row records the full
    signal definition behind its representative atom, so DP4 residuals and
    the DP5 per-conformer reconstruction share one definition. Legacy
    callers that omit it keep ``Assignment.signal_group`` as ``None``.
    """
    if not (len(atom_labels) == len(elements) == len(exp_ppm) == len(calc_ppm)):
        raise ValueError("parallel-array length mismatch")
    n = len(atom_labels)
    if signal_groups is not None and len(signal_groups) != n:
        raise ValueError(f"signal_groups length mismatch: {len(signal_groups)} != {n}")
    groups: list[SignalGroup | None] = (
        list(signal_groups) if signal_groups is not None else [None] * n
    )
    return [
        Assignment(
            atom_label=atom_labels[i],
            element=elements[i],
            exp_ppm=float(exp_ppm[i]),
            calc_ppm=float(calc_ppm[i]),
            scaled_ppm=float(scaled_ppm[i]),
            residual=float(residuals[i]),
            signal_group=groups[i],
        )
        for i in range(n)
    ]


def regression_r_squared(
    exp_ppm: list[float],
    calc_ppm: list[float],
) -> float:
    """Regression-correlation squared of the Goodman calc-on-exp OLS fit.

    Numerator and denominator both live in calc space (todo-25 correction —
    the historical code divided a *scaled-space* numerator
    ``Σ(scaled − exp)²`` by this calc-space denominator, matching neither
    definition):

    ``1 − Σ(calc − (slope·exp + intercept))² / Σ(calc − mean(calc))²``

    Equal to the squared Pearson correlation of (exp, calc) for an OLS fit
    with intercept. Degenerate input (fewer than 2 pairs or zero calc
    variance) returns ``0.0`` — the same fallback as
    :class:`RegressionResult` on degenerate fits.

    Args:
        exp_ppm: Experimental shifts.
        calc_ppm: Computed shifts (same length as *exp_ppm*).

    Returns:
        The coefficient of determination in ``[0, 1]`` up to float noise,
        or ``0.0`` when undefined.
    """
    if len(exp_ppm) != len(calc_ppm):
        raise ValueError(f"calc/exp length mismatch: {len(calc_ppm)} != {len(exp_ppm)}")
    if len(exp_ppm) < 2:
        return 0.0
    x = np.asarray(exp_ppm, dtype=np.float64)
    y = np.asarray(calc_ppm, dtype=np.float64)
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if not np.isfinite(ss_tot) or ss_tot <= 0:
        return 0.0
    slope, intercept = np.polyfit(x, y, 1)
    if not np.isfinite(slope) or not np.isfinite(intercept):
        return 0.0
    ss_res = float(np.sum((y - (slope * x + intercept)) ** 2))
    return float(1.0 - ss_res / ss_tot)


def prediction_r_squared(
    exp_ppm: list[float],
    scaled_ppm: list[float],
) -> float:
    """Prediction-space goodness of fit in Goodman residual coordinates.

    ``1 − Σ(scaled − exp)² / Σ(exp − mean(exp))²`` — numerator and
    denominator both live in the scaled/exp (prediction) space, using the
    signed Goodman residual ``r = scaled − exp``. This is generally NOT
    equal to :func:`regression_r_squared` (they coincide only for slope ≈ 1
    perfect fits).

    Args:
        exp_ppm: Experimental shifts.
        scaled_ppm: Back-transformed shifts (same length as *exp_ppm*).

    Returns:
        The coefficient of determination (can be negative for fits worse
        than the exp mean), or ``0.0`` when undefined (fewer than 2 pairs
        or zero exp variance).
    """
    if len(exp_ppm) != len(scaled_ppm):
        raise ValueError(f"exp/scaled length mismatch: {len(scaled_ppm)} != {len(exp_ppm)}")
    if len(exp_ppm) < 2:
        return 0.0
    x = np.asarray(exp_ppm, dtype=np.float64)
    s = np.asarray(scaled_ppm, dtype=np.float64)
    ss_tot = float(np.sum((x - np.mean(x)) ** 2))
    if not np.isfinite(ss_tot) or ss_tot <= 0:
        return 0.0
    ss_res = float(np.sum((s - x) ** 2))
    return float(1.0 - ss_res / ss_tot)


def fit_scaling_goodman(
    calc_ppm: list[float],
    exp_ppm: list[float],
    nucleus: str,
) -> tuple[RegressionResult, list[float], list[float]]:
    """Fit Goodman's internal-scaling regression (DevDoc §8.4, verified).

    Goodman regresses ``calc = slope·exp + intercept`` (OLS of calc-on-exp,
    DP4.py:151 / DP5.py:332) then computes ``scaled = (calc - intercept)/slope``
    and residuals ``r = scaled - exp``. The DP4/DP5 error models are trained
    on this convention; using the reverse regression (exp-on-calc) would
    produce different residuals and invalidate the trained σ values.

    ``RegressionResult.r_squared`` is the regression-correlation squared of
    this calc-on-exp fit (:func:`regression_r_squared`); the prediction-space
    goodness of fit over the returned residuals is available separately as
    :func:`prediction_r_squared` (todo 25 — the two must not be conflated).

    Args:
        calc_ppm: Computed shifts (post TMS conversion).
        exp_ppm: Matched experimental shifts (same length).
        nucleus: Nucleus label for the :class:`RegressionResult`.

    Returns:
        ``(regression, scaled_ppm, residuals)`` where residuals follow
        Goodman's ``scaled - exp`` sign convention. Degenerate inputs
        (fewer than 2 pairs) fall back to the identity fit.
    """
    if len(calc_ppm) != len(exp_ppm):
        raise ValueError(f"calc/exp length mismatch: {len(calc_ppm)} != {len(exp_ppm)}")

    n = len(calc_ppm)
    if n == 0:
        return (
            RegressionResult(nucleus=nucleus, slope=1.0, intercept=0.0, r_squared=0.0, mae=0.0),
            [],
            [],
        )

    x = np.asarray(exp_ppm, dtype=np.float64)  # exp = x (Goodman convention)
    y = np.asarray(calc_ppm, dtype=np.float64)  # calc = y

    if n < 2 or np.allclose(x, x[0]):
        # degenerate: scaled = calc, residual = calc - exp
        residuals = (y - x).tolist()
        mae = float(np.mean(np.abs(residuals))) if residuals else 0.0
        return (
            RegressionResult(nucleus=nucleus, slope=1.0, intercept=0.0, r_squared=0.0, mae=mae),
            y.tolist(),
            residuals,
        )

    # OLS: calc = slope·exp + intercept  (calc-on-exp, matches DP4.py:151)
    slope, intercept = np.polyfit(x, y, 1).tolist()
    if slope == 0 or not np.isfinite(slope):
        slope = 1.0
        intercept = 0.0
    scaled = (y - intercept) / slope  # scaled ≈ exp
    residuals = scaled - x  # Goodman: scaled - exp

    # todo 25 numerator/denominator correction: the reported r² is the
    # regression-correlation squared — numerator AND denominator in calc
    # space (Σ(calc − fit)² / Σ(calc − mean)²). The previous code mixed a
    # scaled-space numerator Σ(scaled − exp)² with that calc-space
    # denominator, matching neither the regression nor the prediction
    # definition; the prediction-space value is prediction_r_squared().
    r2_regression = regression_r_squared(exp_ppm, calc_ppm)
    r_squared = r2_regression
    mae = float(np.mean(np.abs(residuals)))

    return (
        RegressionResult(
            nucleus=nucleus,
            slope=float(slope),
            intercept=float(intercept),
            r_squared=r_squared,
            mae=mae,
        ),
        scaled.tolist(),
        residuals.tolist(),
    )


__all__ = [
    "build_assignments",
    "fit_regression",
    "fit_scaling_goodman",
    "prediction_r_squared",
    "regression_r_squared",
]
