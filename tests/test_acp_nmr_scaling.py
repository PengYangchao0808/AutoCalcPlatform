"""Tests for linear-regression scaling (DevDoc §8.4)."""

from __future__ import annotations

import math

import pytest

from acp.nmr.scaling import build_assignments, fit_regression


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
