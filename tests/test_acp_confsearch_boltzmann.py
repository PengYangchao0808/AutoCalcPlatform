"""Unit tests for the canonical Boltzmann helper missing-policy (task 1).

Pins two contracts:

* ``missing="zero"`` (default) keeps the historical behaviour exactly —
  missing/non-finite entries get weight 0.0 and stay in the normalization
  denominator.
* ``missing="none"`` maps missing/non-finite entries to ``None`` and excludes
  them from the denominator, so the finite entries normalize to sum 1.0.
"""

from __future__ import annotations

import math

from acp.confsearch.shared.boltzmann import boltzmann_weights

_HARTREE_TO_KCAL = 627.5094740631
_RGAS_KCAL_MOL_K = 0.001987204258
_T = 298.15


def _reference_zero_policy(
    energies: list[float | None],
    temperature_k: float,
) -> list[float | None]:
    """Independent re-statement of the historical (pre-change) formula."""
    finite = [float(e) for e in energies if e is not None and math.isfinite(float(e))]
    if not finite:
        return [0.0 if e is None else None for e in energies]
    beta = 1.0 / (_RGAS_KCAL_MOL_K / _HARTREE_TO_KCAL * temperature_k)
    floor = min(finite)
    exps: list[float] = []
    for value in energies:
        if value is None or not math.isfinite(float(value)):
            exps.append(0.0)
        else:
            exps.append(math.exp(-beta * (float(value) - floor)))
    total = sum(exps)
    if total <= 0.0:
        n = len(exps)
        return [1.0 / n] * n
    return [value / total for value in exps]


def test_default_call_matches_explicit_zero_policy() -> None:
    energies = [0.0, 0.001, None, -0.002]
    assert boltzmann_weights(energies, _T) == boltzmann_weights(energies, _T, missing="zero")


def test_zero_policy_matches_historical_formula_exactly() -> None:
    """misleading_success_output probe: exact numeric output, not call counts."""
    energies = [0.0, 0.001, None, -0.002, math.nan, math.inf]
    expected = _reference_zero_policy(energies, _T)
    assert boltzmann_weights(energies, _T, missing="zero") == expected
    # Pin concrete numbers too (equal energies -> exact 0.5 split).
    assert boltzmann_weights([0.0, 0.0], _T, missing="zero") == [0.5, 0.5]
    # Missing entries carry 0.0 and remain in the denominator.
    assert boltzmann_weights([0.0, None], _T, missing="zero") == [1.0, 0.0]


def test_none_policy_excludes_missing_from_denominator() -> None:
    energies = [0.0, 0.001, None, -0.002]
    weights = boltzmann_weights(energies, _T, missing="none")
    assert weights[2] is None
    finite = [w for w in weights if w is not None]
    assert math.isclose(sum(finite), 1.0, rel_tol=0.0, abs_tol=1e-12)
    # Exact pin: equal finite energies split 0.5/0.5, missing stays None.
    assert boltzmann_weights([0.0, 0.0, None], _T, missing="none") == [0.5, 0.5, None]
    # A lone finite entry normalizes to exactly 1.0.
    assert boltzmann_weights([0.0, None], _T, missing="none") == [1.0, None]


def test_none_policy_matches_softmax_on_finite_subset() -> None:
    energies = [0.0, 0.001, None, -0.002]
    weights = boltzmann_weights(energies, _T, missing="none")
    finite_energies = [0.0, 0.001, -0.002]
    expected_finite = _reference_zero_policy(finite_energies, _T)
    got_finite = [w for w in weights if w is not None]
    assert got_finite == expected_finite


def test_all_missing_returns_all_none_under_none_policy() -> None:
    assert boltzmann_weights([None, None, None], _T, missing="none") == [None, None, None]


def test_all_missing_zero_policy_keeps_historical_quirk() -> None:
    assert boltzmann_weights([None, None], _T, missing="zero") == [0.0, 0.0]


def test_empty_list() -> None:
    assert boltzmann_weights([], _T, missing="zero") == []
    assert boltzmann_weights([], _T, missing="none") == []


def test_non_finite_entries_treated_as_missing() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        assert boltzmann_weights([0.0, bad], _T, missing="none") == [1.0, None]
        # zero policy: non-finite gets 0.0 when finite entries exist.
        assert boltzmann_weights([0.0, bad], _T, missing="zero") == [1.0, 0.0]


def test_temperature_changes_weights() -> None:
    energies = [0.0, 0.002]
    hot = boltzmann_weights(energies, 1000.0, missing="none")
    cold = boltzmann_weights(energies, 100.0, missing="none")
    assert hot[0] is not None and cold[0] is not None
    assert hot[0] < cold[0]  # higher T flattens the distribution
