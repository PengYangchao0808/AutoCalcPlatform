"""Tests for DP4/DP5 probability (DevDoc §8.5/§8.6)."""

from __future__ import annotations

import math

import pytest

from acp.nmr.error_model import (
    GoodmanErrorModel,
    NonFiniteResidualError,
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


# todo 11: stable log-CDF — log(2) + log_ndtr(-|z|) ≡ log(erfc(z/√2)) (golden branch).


def test_goodman_log_cdf_matches_erfc_branch() -> None:
    """log(2)+log_ndtr(-z) ≡ log(erfc(z/√2)) on the official fixed branch."""
    from scipy.special import log_ndtr

    for z in (0.0, 0.1, 0.5, 1.0, 2.0, 3.0, 5.0):
        stable = math.log(2.0) + float(log_ndtr(-z))
        legacy = math.log(math.erfc(z / math.sqrt(2.0)))
        assert stable == pytest.approx(legacy, rel=0.0, abs=1e-10)


def test_goodman_regular_residuals_match_legacy_formula() -> None:
    """Regular residuals stay numerically identical to the official branch."""
    em = GoodmanErrorModel()
    for r in (0.0, 0.1, 0.5, 1.0, 2.0):
        for nucleus in ("13C", "1H"):
            z = abs(r / em.SIGMA[nucleus])
            legacy = math.log(math.erfc(z / math.sqrt(2.0)))
            got = em.log_likelihood([r], nucleus)
            assert got == pytest.approx(legacy, rel=0.0, abs=1e-10)


def test_goodman_100ppm_carbon_residual_is_finite() -> None:
    """A 100 ppm carbon residual must return finite, not raise math domain error."""
    from scipy.special import log_ndtr

    em = GoodmanErrorModel()
    ll = em.log_likelihood([100.0], "13C")
    assert math.isfinite(ll)
    assert ll < 0.0
    z = abs(100.0 / em.SIGMA["13C"])
    expected = math.log(2.0) + float(log_ndtr(-z))  # ≈ log(2·Φ(-44))
    assert ll == pytest.approx(expected, rel=0.0, abs=1e-10)


def test_goodman_nonfinite_residuals_raise_typed_error() -> None:
    """NaN/Inf residuals are typed failures — never silent NaN totals."""
    em = GoodmanErrorModel()
    assert issubclass(NonFiniteResidualError, ValueError)
    with pytest.raises(NonFiniteResidualError) as excinfo:
        em.log_likelihood([float("nan")], "13C")
    assert "13C" in str(excinfo.value)
    assert "nan" in str(excinfo.value)
    with pytest.raises(NonFiniteResidualError) as excinfo:
        em.log_likelihood([float("inf")], "1H")
    assert "1H" in str(excinfo.value)
    assert "inf" in str(excinfo.value)
    with pytest.raises(NonFiniteResidualError):
        em.log_likelihood([1.0, float("nan")], "13C")


def test_goodman_empty_and_unknown_nucleus_semantics_unchanged() -> None:
    """σ lookup semantics: empty list → 0.0, unknown nucleus → 0.0."""
    em = GoodmanErrorModel()
    assert em.log_likelihood([], "13C") == 0.0
    assert em.log_likelihood([1.0], "31P") == 0.0
