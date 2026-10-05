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
from collections.abc import Sequence
from typing import Any

from acp.nmr.error_model import ErrorModel, NonFiniteResidualError

logger = logging.getLogger(__name__)


class ProbabilityInputError(ValueError):
    """Invalid statistical input reaching the DP4/DP5 probability functions.

    Distinct from :class:`acp.nmr.error_model.NonFiniteResidualError`
    (raised for non-finite *residuals*): this error flags bad parameters
    or bad log-likelihood arrays handed to the normalization stage.
    """


def _require_finite_residuals(
    per_nucleus_residuals: dict[str, list[float]],
    where: str,
) -> None:
    """Reject non-finite residuals before any likelihood math runs.

    Args:
        per_nucleus_residuals: Scaled residuals grouped by nucleus.
        where: Calling function name, embedded in the error message.

    Raises:
        NonFiniteResidualError: Any residual is NaN or ±Inf.
    """
    for nucleus, residuals in per_nucleus_residuals.items():
        for r in residuals:
            r_val = float(r)
            if not math.isfinite(r_val):
                raise NonFiniteResidualError(f"non-finite {nucleus} residual {r_val!r} in {where}")


def _require_finite_log_likelihoods(
    log_likelihoods: Sequence[float],
    where: str,
) -> None:
    """Reject non-finite log-likelihoods before the softmax runs.

    Args:
        log_likelihoods: Per-candidate DP4 log-likelihoods.
        where: Calling function name, embedded in the error message.

    Raises:
        ProbabilityInputError: Any entry is NaN or ±Inf.
    """
    for i, ll in enumerate(log_likelihoods):
        if not math.isfinite(ll):
            raise ProbabilityInputError(
                f"non-finite log-likelihood at index {i} ({where}): {float(ll)!r}"
            )


def compute_dp4(
    per_nucleus_residuals: dict[str, list[float]],
    error_model: ErrorModel,
) -> float:
    """Return the (unnormalized) DP4 log-likelihood for one candidate.

    The caller normalizes across candidates via :func:`normalize_dp4`.
    Returning the log keeps the product numerically stable for many
    residuals.

    Raises:
        NonFiniteResidualError: Any residual is NaN or ±Inf (validated
            here so the guarantee does not depend on the error model —
            the placeholder Student-t previously let NaN through).
    """
    _require_finite_residuals(per_nucleus_residuals, "compute_dp4")
    return sum(
        error_model.log_likelihood(residuals, nucleus)
        for nucleus, residuals in per_nucleus_residuals.items()
        if residuals
    )


def normalize_dp4(log_likelihoods: list[float]) -> list[float]:
    """Normalize log-likelihoods across candidates → DP4 probabilities.

    Uses the softmax / log-sum-exp trick to avoid underflow.

    Raises:
        ProbabilityInputError: Any log-likelihood is NaN or ±Inf —
            rejected up front instead of softmax-ing NaN into the result.
    """
    if not log_likelihoods:
        return []
    _require_finite_log_likelihoods(log_likelihoods, "normalize_dp4")
    max_ll = max(log_likelihoods)
    if math.isinf(max_ll) and max_ll < 0:
        return [0.0 for _ in log_likelihoods]
    exps = [math.exp(ll - max_ll) for ll in log_likelihoods]
    total = sum(exps)
    if total <= 0:
        return [0.0 for _ in log_likelihoods]
    return [e / total for e in exps]


def normalize_dp4_gated(
    log_likelihoods: Sequence[float],
    statuses: Sequence[str],
) -> list[float | None]:
    """Evidence gate (G05): normalize only the ``valid`` candidates.

    Excluded candidates (``invalid`` / ``evidence_insufficient``) receive
    ``None`` instead of a probability — critically, the empty-evidence
    candidate whose raw log-likelihood of ``0.0`` would otherwise beat every
    real (negative) log-likelihood in the softmax. The DP4 math itself is
    untouched: the valid subset runs through :func:`normalize_dp4`, so a
    lone valid candidate gets ``1.0`` and no valid candidate at all yields
    all ``None``.

    Args:
        log_likelihoods: Per-candidate DP4 log-likelihoods (parallel to
            *statuses*).
        statuses: Per-candidate evidence statuses; only ``"valid"`` entries
            are normalized.

    Returns:
        One entry per candidate: the normalized probability for valid
        candidates, ``None`` where the gate excludes the candidate.

    Raises:
        ProbabilityInputError: Length mismatch between the two sequences,
            or any log-likelihood (valid or excluded) is NaN/±Inf.
    """
    if len(log_likelihoods) != len(statuses):
        raise ProbabilityInputError(
            f"length mismatch: {len(log_likelihoods)} log-likelihoods vs {len(statuses)} statuses"
        )
    _require_finite_log_likelihoods(log_likelihoods, "normalize_dp4_gated")
    valid_ll = [ll for ll, status in zip(log_likelihoods, statuses) if status == "valid"]
    normalized = iter(normalize_dp4(valid_ll))
    return [next(normalized) if status == "valid" else None for status in statuses]


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

    Raises:
        NonFiniteResidualError: Any residual is NaN or ±Inf.
        ProbabilityInputError: ``kde_bandwidth`` is not finite and > 0.
    """
    if not math.isfinite(kde_bandwidth) or kde_bandwidth <= 0:
        raise ProbabilityInputError(f"kde_bandwidth must be finite and > 0, got {kde_bandwidth!r}")
    _require_finite_residuals(per_nucleus_residuals, "compute_dp5")
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

    Raises:
        NonFiniteResidualError: Any residual (in any nucleus) is NaN/±Inf —
            rejected even for nuclei the carbon-only path does not consume,
            so broken upstream input never passes silently.
    """
    # Goodman DP5 is 13C-only (DP5.py proton code commented out). The KDE
    # training data is carbon-specific; mixing in 1H residuals would be
    # scientifically invalid.
    _require_finite_residuals(per_nucleus_residuals, "compute_dp5_goodman")
    carbon_residuals = per_nucleus_residuals.get("13C", [])
    carbon_errors = [float(r) for r in carbon_residuals]
    if not carbon_errors:
        return 0.0
    return float(dp5_model.probability(carbon_errors))


def dp5_log_to_probability(log_prob: float) -> float:
    """Convert a placeholder DP5 log-probability to ``[0, 1]`` (sigmoid).

    Only used by the placeholder path. The real Goodman DP5 (via
    :func:`compute_dp5_goodman`) already returns a probability in ``[0, 1]``.

    Numerically stable sign branch: for ``log_prob >= 0`` the exponential
    of the non-positive value ``-log_prob`` can only underflow (→ ``1.0``);
    for ``log_prob < 0`` ``exp(log_prob)`` underflows to ``0.0``. Neither
    branch overflows, so ``dp5_log_to_probability(-1000)`` is ``0.0`` instead
    of raising ``OverflowError`` (gap §12.1 / G14).

    Returns:
        Finite probability in ``[0, 1]``; ``±inf`` maps to ``0.0``/``1.0``.

    Raises:
        ProbabilityInputError: ``log_prob`` is NaN — no implicit NaN success.
    """
    x = float(log_prob)
    if math.isnan(x):
        raise ProbabilityInputError(f"log_prob must not be NaN, got {x!r}")
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


__all__ = [
    "ProbabilityInputError",
    "compute_dp4",
    "normalize_dp4",
    "normalize_dp4_gated",
    "compute_dp5",
    "compute_dp5_goodman",
    "dp5_log_to_probability",
]
