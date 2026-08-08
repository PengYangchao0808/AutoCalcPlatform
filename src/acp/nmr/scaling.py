# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Linear-regression scaling (DevDoc §5 stage 6 / §8.4).

Per-nucleus ordinary-least-squares fit ``δ_exp = slope · δ_calc + intercept``
with residuals ``r = δ_exp − δ_scaled``. The regression absorbs the
constant TMS / solvent offset so the downstream DP4/DP5 likelihood is
insensitive to systematic shielding offsets (Goodman InternalScaling).
"""

from __future__ import annotations

import logging

import numpy as np

from acp.nmr.models import Assignment, RegressionResult

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
) -> list[Assignment]:
    """Assemble :class:`Assignment` rows from parallel arrays."""
    if not (len(atom_labels) == len(elements) == len(exp_ppm) == len(calc_ppm)):
        raise ValueError("parallel-array length mismatch")
    return [
        Assignment(
            atom_label=atom_labels[i],
            element=elements[i],
            exp_ppm=float(exp_ppm[i]),
            calc_ppm=float(calc_ppm[i]),
            scaled_ppm=float(scaled_ppm[i]),
            residual=float(residuals[i]),
        )
        for i in range(len(atom_labels))
    ]


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


__all__ = ["fit_regression", "fit_scaling_goodman", "build_assignments"]
