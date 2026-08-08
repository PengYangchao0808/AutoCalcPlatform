"""Tests for DP4/DP5 probability (DevDoc §8.5/§8.6)."""

from __future__ import annotations

import math

import pytest

from acp.nmr.error_model import (
    PlaceholderStudentTErrorModel,
    load_error_model,
    validate_error_model_binding,
)
from acp.nmr.models import NmrConfig
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    dp5_log_to_probability,
    normalize_dp4,
)


def test_dp4_normalizes_to_one() -> None:
    em = PlaceholderStudentTErrorModel()
    ll = [
        compute_dp4({"1H": [0.05, 0.1]}, em),
        compute_dp4({"1H": [1.0, 0.8]}, em),
    ]
    probs = normalize_dp4(ll)
    assert len(probs) == 2
    assert sum(probs) == pytest.approx(1.0)
    # tiny residuals → much higher probability
    assert probs[0] > probs[1]
    assert probs[0] > 0.99


def test_dp4_equal_residuals_equal_probability() -> None:
    em = PlaceholderStudentTErrorModel()
    ll = [
        compute_dp4({"13C": [0.5]}, em),
        compute_dp4({"13C": [0.5]}, em),
        compute_dp4({"13C": [0.5]}, em),
    ]
    probs = normalize_dp4(ll)
    for p in probs:
        assert p == 1.0 / 3


def test_dp4_empty_returns_empty() -> None:
    assert normalize_dp4([]) == []


def test_dp5_independent_probability_in_range() -> None:
    em = PlaceholderStudentTErrorModel()
    log_p = compute_dp5({"1H": [0.1, 0.2], "13C": [1.0]}, em)
    p = dp5_log_to_probability(log_p)
    assert 0.0 <= p <= 1.0


def test_dp5_lower_for_worse_residuals() -> None:
    em = PlaceholderStudentTErrorModel()
    good = dp5_log_to_probability(compute_dp5({"1H": [0.05]}, em))
    bad = dp5_log_to_probability(compute_dp5({"1H": [2.0]}, em))
    assert good > bad


def test_load_placeholder_model() -> None:
    em = load_error_model("placeholder-student-t")
    assert isinstance(em, PlaceholderStudentTErrorModel)
    # goodman-legacy now loads the real Gaussian model (P1b)
    em2 = load_error_model("goodman-legacy")
    assert type(em2).__name__ == "GoodmanErrorModel"


def test_goodman_gaussian_dp4_matches_source() -> None:
    """Goodman DP4 uses Gaussian P = 2·Φ(-|r/σ|) (verified DP4.py:190)."""
    from acp.nmr.error_model import GoodmanErrorModel
    import math

    em = GoodmanErrorModel()
    # σ values match DP4.py:20-21
    assert em.SIGMA["13C"] == pytest.approx(2.269372270818724)
    assert em.SIGMA["1H"] == pytest.approx(0.18731058105269952)
    # zero residual → P=1 → log P = 0
    assert em.log_likelihood([0.0], "1H") == pytest.approx(0.0, abs=1e-9)
    # large residual → very negative log P
    ll_small = em.log_likelihood([0.1], "1H")
    ll_large = em.log_likelihood([2.0], "1H")
    assert ll_small > ll_large
    # sanity: P(1σ) = 2·Φ(-1) ≈ 0.317 → log ≈ -1.15
    p_1sigma = math.exp(em.log_likelihood([em.SIGMA["1H"]], "1H"))
    assert p_1sigma == pytest.approx(2 * 0.5 * math.erfc(1 / math.sqrt(2)), abs=1e-6)


def test_tms_lookup_returns_goodman_values() -> None:
    """TMS table has mPW1PW91/6-311G(d)/chloroform from Goodman TMSdata."""
    from acp.nmr.models import lookup_tms_shieldings

    c, h = lookup_tms_shieldings("mPW1PW91", "6-311G(d)", "chloroform")
    assert c == pytest.approx(188.452125, abs=1e-4)
    assert h == pytest.approx(32.1243166667, abs=1e-4)
    # gas-phase fallback for unknown solvent
    c2, h2 = lookup_tms_shieldings("mPW1PW91", "6-311G(d)", "unknownsolvent")
    assert c2 is not None  # falls back to "none"


def test_dp5_goodman_model_loads_and_scales() -> None:
    """Goodman DP5 model loads, and good residuals give higher DP5 than bad."""
    from acp.nmr.error_model import dp5_model_available, load_dp5_model

    if not dp5_model_available():
        pytest.skip("Goodman DP5 model files not present")
    model = load_dp5_model()
    good = model.probability([0.3, 0.5, 0.2])  # small residuals
    bad = model.probability([5.0, 4.0, 6.0])  # large residuals
    assert 0.0 <= good <= 1.0
    assert 0.0 <= bad <= 1.0
    assert good >= bad


def test_validate_binding_rejects_mismatched_level() -> None:
    cfg = NmrConfig(
        nmr_method="wB97X-D4",  # wrong level for goodman-legacy
        nmr_basis="def2-TZVPPD",
        error_model="goodman-legacy",
    )
    try:
        validate_error_model_binding(cfg)
        raise AssertionError("expected ValueError for mismatched level")
    except ValueError as exc:
        assert "mPW1PW91" in str(exc)


def test_validate_binding_accepts_goodman_level() -> None:
    cfg = NmrConfig(
        nmr_method="mPW1PW91",
        nmr_basis="6-311G(d)",
        error_model="goodman-legacy",
    )
    validate_error_model_binding(cfg)  # should not raise


def test_validate_binding_allows_placeholder() -> None:
    cfg = NmrConfig(error_model="placeholder-student-t")
    validate_error_model_binding(cfg)  # should not raise


def test_normalize_dp4_handles_underflow() -> None:
    # extremely negative log-likelihoods must not error
    probs = normalize_dp4([-1000.0, -1001.0])
    assert sum(probs) == 1.0
    assert probs[0] > probs[1]
    assert math.isfinite(probs[0])
