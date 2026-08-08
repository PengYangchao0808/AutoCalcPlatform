# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""DP4 / DP5 probability (DevDoc §5 stage 7 / §8.5 / §8.6).

* **DP4** — candidate-set-normalized probability: assumes one of the K
  candidates is correct. Likelihood ``L_k = Π_i f(r_{k,i})`` (independent
  residuals), normalized across candidates: ``P(DP4, k) = L_k / Σ_j L_j``.
* **DP5** — independent probability: ``P(DP5, k)`` does not assume the
  candidate set contains the true structure. Goodman's reference uses a
  KDE on folded residuals ``|r|`` (bandwidth 0.025). The placeholder
  model approximates this with a half-Student-t so the public API is
  stable when P1b swaps in a trained KDE.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from acp.nmr.error_model import ErrorModel

logger = logging.getLogger(__name__)


def compute_dp4(
    per_nucleus_residuals: dict[str, list[float]],
    error_model: ErrorModel,
) -> float:
    """Return the (unnormalized) DP4 log-likelihood for one candidate.

    The caller normalizes across candidates via :func:`normalize_dp4`.
    Returning the log keeps the product numerically stable for many
    residuals.
    """
    return sum(
        error_model.log_likelihood(residuals, nucleus)
        for nucleus, residuals in per_nucleus_residuals.items()
        if residuals
    )


def normalize_dp4(log_likelihoods: list[float]) -> list[float]:
    """Normalize log-likelihoods across candidates → DP4 probabilities.

    Uses the softmax / log-sum-exp trick to avoid underflow.
    """
    if not log_likelihoods:
        return []
    max_ll = max(log_likelihoods)
    if math.isinf(max_ll) and max_ll < 0:
        return [0.0 for _ in log_likelihoods]
    exps = [math.exp(ll - max_ll) for ll in log_likelihoods]
    total = sum(exps)
    if total <= 0:
        return [0.0 for _ in log_likelihoods]
    return [e / total for e in exps]


def compute_dp5(
    per_nucleus_residuals: dict[str, list[float]],
    error_model: ErrorModel,
    kde_bandwidth: float = 0.025,
) -> float:
    """Return a placeholder DP5 log-probability for one candidate.

    **P1a placeholder** — when the caller passes a placeholder error model,
    this computes a coarse log-probability from folded residuals so DP5
    stays comparable across candidates. The real Goodman DP5 (KDE +
    Rescale_DP5) is computed by :func:`compute_dp5_goodman` instead.

    Args:
        per_nucleus_residuals: Scaled residuals per nucleus.
        error_model: Trained (or placeholder) error distribution.
        kde_bandwidth: Reference bandwidth (provenance only).
    """
    folded: dict[str, list[float]] = {
        nucleus: [abs(r) for r in residuals]
        for nucleus, residuals in per_nucleus_residuals.items()
        if residuals
    }
    ll = sum(error_model.log_likelihood(rs, nucleus) for nucleus, rs in folded.items())
    _ = kde_bandwidth
    return ll


def compute_dp5_goodman(
    per_nucleus_residuals: dict[str, list[float]],
    dp5_model: Any,
) -> float:
    """Compute the real Goodman DP5 probability (P1b).

    Uses :class:`acp.nmr.error_model.GoodmanDP5Model` to run the full
    KDE + geometric-mean + Bayesian-rescale pipeline (DP5.py:73-383).
    This averaged-residual entry point always uses the unweighted KDE
    fallback (no per-conformer geometry). Use
    :meth:`GoodmanDP5Model.probability_per_conformer[_fchl]` for the
    Goodman-faithful per-conformer path.

    **Parity note (audit 2026-08-07):** Goodman's DP5 is **Carbon-only**
    (DP5.py:307-327 — the proton scaling block is commented out). The
    ``folded_scaled_errors`` training data and the ``c_w_kde``/``i_w_kde``
    rescale KDEs are all trained on ¹³C residuals. Passing ¹H residuals
    (σ≈0.19 ppm, vs ¹³C σ≈2.27 ppm) would corrupt the KDE. This function
    therefore uses **only the ¹³C residuals**.

    Args:
        per_nucleus_residuals: Scaled residuals per nucleus (Goodman
            convention: ``scaled - exp``). Only ``"13C"`` is consumed.
        dp5_model: A loaded :class:`GoodmanDP5Model`.

    Returns:
        DP5 probability in ``[0, 1]``, or ``0.0`` when no ¹³C residuals.
    """
    # Goodman DP5 is 13C-only (DP5.py proton code commented out). The KDE
    # training data is carbon-specific; mixing in 1H residuals would be
    # scientifically invalid.
    carbon_residuals = per_nucleus_residuals.get("13C", [])
    carbon_errors = [float(r) for r in carbon_residuals]
    if not carbon_errors:
        return 0.0
    return float(dp5_model.probability(carbon_errors))


def dp5_log_to_probability(log_prob: float) -> float:
    """Convert a placeholder DP5 log-probability to ``[0, 1]`` (sigmoid).

    Only used by the placeholder path. The real Goodman DP5 (via
    :func:`compute_dp5_goodman`) already returns a probability in ``[0, 1]``.
    """
    return 1.0 / (1.0 + math.exp(-log_prob))


__all__ = [
    "compute_dp4",
    "normalize_dp4",
    "compute_dp5",
    "compute_dp5_goodman",
    "dp5_log_to_probability",
]
