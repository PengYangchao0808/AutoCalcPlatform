"""Tests for linear-regression scaling (DevDoc §8.4)."""

from __future__ import annotations

import math

import pytest

from acp.nmr.scaling import (
    build_assignments,
    fit_regression,
    fit_scaling_goodman,
    prediction_r_squared,
    regression_r_squared,
)


def test_perfect_linear_fit() -> None:
    # y = 2x + 1
    calc = [1.0, 2.0, 3.0, 4.0]
    exp = [3.0, 5.0, 7.0, 9.0]
    reg, scaled, residuals = fit_regression(calc, exp, "1H")
    assert reg.slope == pytest.approx(2.0)
    assert reg.intercept == pytest.approx(1.0)
    assert reg.r_squared == pytest.approx(1.0, abs=1e-9)
    assert reg.mae == pytest.approx(0.0, abs=1e-9)
    for r in residuals:
        assert abs(r) < 1e-9


def test_fit_with_noise() -> None:
    calc = [10.0, 20.0, 30.0, 40.0]
    exp = [12.0, 22.0, 28.0, 42.0]
    reg, scaled, residuals = fit_regression(calc, exp, "13C")
    assert 0 < reg.slope < 2
    assert 0 <= reg.r_squared <= 1.0
    assert len(residuals) == 4


def test_degenerate_returns_identity_fit() -> None:
    # only one point → no slope information
    reg, scaled, residuals = fit_regression([5.0], [5.0], "1H")
    assert reg.slope == 1.0
    assert reg.intercept == 0.0
    assert residuals == [0.0]


def test_constant_calc_returns_identity_with_residuals() -> None:
    # all calc identical → degenerate; residual = exp - calc
    reg, scaled, residuals = fit_regression([3.0, 3.0, 3.0], [4.0, 5.0, 6.0], "1H")
    assert reg.slope == 1.0
    assert reg.intercept == 0.0
    assert residuals == [1.0, 2.0, 3.0]
    assert reg.mae == pytest.approx(2.0)


def test_build_assignments_parallel_arrays() -> None:
    labels = ["C1", "C2"]
    elements = ["C", "C"]
    exp = [10.0, 20.0]
    calc = [11.0, 21.0]
    scaled = [10.5, 20.5]
    residuals = [-0.5, -0.5]
    assignments = build_assignments(labels, elements, exp, calc, scaled, residuals)
    assert len(assignments) == 2
    assert assignments[0].atom_label == "C1"
    assert assignments[0].residual == pytest.approx(-0.5)


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        fit_regression([1.0, 2.0], [1.0], "1H")


def test_empty_returns_empty() -> None:
    reg, scaled, residuals = fit_regression([], [], "1H")
    assert reg.slope == 1.0
    assert scaled == []
    assert residuals == []
    assert math.isfinite(reg.mae) or reg.mae == 0.0


# ---------------------------------------------------------------------------
# todo 25: Goodman R² split — regression-correlation squared vs
# prediction-space goodness of fit (calc-on-exp direction unchanged).
#
# BEFORE (raw capture in
# .omo/evidence/acp-nmr-goodman-gap-remediation/task-25-before-r2-and-label.txt):
# scaling.py:164-166 divided a scaled-space numerator sum((scaled-exp)^2)
# by a calc-space denominator sum((calc-mean(calc))^2) — matching NEITHER
# definition (fixture below reported 0.724220 vs 0.907228 / 0.897741).
# ---------------------------------------------------------------------------

# Fixture pinned in the BEFORE repro: slope ≈ 0.58 calc-on-exp with scatter,
# so the old mixed-space value, the regression r² and the prediction r² are
# three clearly distinct numbers.
_R2_EXP = [10.0, 20.0, 30.0, 40.0, 50.0]
_R2_CALC = [3.0, 12.0, 11.0, 24.0, 26.0]


def _ref_regression_r2(exp: list[float], calc: list[float]) -> float:
    """Independent reference: squared Pearson r == OLS R² (calc-on-exp)."""
    n = len(exp)
    mx = sum(exp) / n
    my = sum(calc) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(exp, calc))
    sxx = sum((x - mx) ** 2 for x in exp)
    syy = sum((y - my) ** 2 for y in calc)
    return (sxy * sxy) / (sxx * syy)


def _ref_prediction_r2(exp: list[float], scaled: list[float]) -> float:
    """Independent reference: 1 - Σ(scaled-exp)² / Σ(exp-mean(exp))²."""
    mx = sum(exp) / len(exp)
    ss_res = sum((s - e) ** 2 for e, s in zip(exp, scaled))
    ss_tot = sum((e - mx) ** 2 for e in exp)
    return 1.0 - ss_res / ss_tot


def test_r2_split_definitions_differ_on_pinned_fixture() -> None:
    """The two R² definitions are distinct and each hits its own reference."""
    reg, scaled, _residuals = fit_scaling_goodman(_R2_CALC, _R2_EXP, "13C")
    r2_regression = regression_r_squared(_R2_EXP, _R2_CALC)
    r2_prediction = prediction_r_squared(_R2_EXP, scaled)

    # each definition matches an independent reference implementation
    assert r2_regression == pytest.approx(_ref_regression_r2(_R2_EXP, _R2_CALC), abs=1e-12)
    assert r2_prediction == pytest.approx(_ref_prediction_r2(_R2_EXP, scaled), abs=1e-12)
    # RegressionResult.r_squared IS the regression definition (documented map)
    assert reg.r_squared == pytest.approx(r2_regression, abs=1e-12)
    # and the two definitions are distinguishable on this fixture
    assert r2_regression != pytest.approx(r2_prediction, abs=1e-6)
    assert abs(r2_regression - r2_prediction) > 1e-3


def test_fit_scaling_goodman_direction_and_signed_residuals_unchanged() -> None:
    """calc-on-exp fit direction and the signed scaled-exp residuals stay."""
    # exact linear calc = 2·exp + 1 → scaled recovers exp exactly
    exp = [1.0, 2.0, 3.0, 4.0]
    calc = [2.0 * e + 1.0 for e in exp]
    reg, scaled, residuals = fit_scaling_goodman(calc, exp, "1H")
    assert reg.slope == pytest.approx(2.0)
    assert reg.intercept == pytest.approx(1.0)
    for s in scaled:
        assert s == pytest.approx(exp[scaled.index(s)], abs=1e-9)
    for r in residuals:
        assert abs(r) < 1e-9

    # noisy fixture: residuals stay SIGNED and equal scaled - exp elementwise
    reg, scaled, residuals = fit_scaling_goodman(_R2_CALC, _R2_EXP, "13C")
    assert residuals == pytest.approx([s - e for s, e in zip(scaled, _R2_EXP)], abs=1e-12)
    # both R² definitions are in [0, 1] for this OLS fit
    assert 0.0 <= reg.r_squared <= 1.0
    assert 0.0 <= prediction_r_squared(_R2_EXP, scaled) <= 1.0


def test_r2_helpers_degenerate_inputs_return_zero() -> None:
    """Empty/constant inputs cannot define either R² → 0.0 (fit fallback)."""
    assert regression_r_squared([], []) == 0.0
    assert prediction_r_squared([], []) == 0.0
    assert regression_r_squared([1.0], [2.0]) == 0.0
    assert prediction_r_squared([1.0], [2.0]) == 0.0
    # constant exp → no prediction-space variance
    assert prediction_r_squared([5.0, 5.0], [4.0, 6.0]) == 0.0
    # constant calc → no regression-space variance
    assert regression_r_squared([1.0, 2.0], [7.0, 7.0]) == 0.0


def test_prediction_r_squared_perfect_scaled_is_one() -> None:
    exp = [0.5, 1.5, 9.0]
    assert prediction_r_squared(exp, list(exp)) == pytest.approx(1.0)
