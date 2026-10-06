"""Molecule-clustered bootstrap confidence intervals (todo 50 / gap §10.3).

Performance differences on a benchmark must be estimated by resampling
**molecules** (clusters), never atoms, signals or conformers: the multiple
observations of one molecule are not independent samples.  Every interval is
produced by a seeded, deterministic percentile bootstrap; paired method
comparisons draw one set of cluster resamples and evaluate both methods on
it, so the delta interval is a genuine paired estimate.

The statistic takes the pooled observation list of the resampled clusters
(``pooled_mean`` by default) and must return a finite float.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_BOOTSTRAP_SEED = 20261006
DEFAULT_N_RESAMPLES = 2000
DEFAULT_ALPHA = 0.05

Statistic = Callable[[Sequence[float]], float]


class BootstrapError(ValueError):
    """Typed failure for malformed clusters or unusable statistics."""


def pooled_mean(values: Sequence[float]) -> float:
    """Deterministic mean over the pooled observations (``math.fsum``)."""
    if not values:
        raise BootstrapError("statistic received no observations")
    return math.fsum(float(value) for value in values) / len(values)


@dataclass(frozen=True)
class BootstrapInterval:
    """Percentile bootstrap interval over molecule clusters."""

    point: float
    low: float
    high: float
    level: float
    n_resamples: int
    seed: int
    n_clusters: int
    n_observations: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "low": self.low,
            "high": self.high,
            "level": self.level,
            "n_resamples": self.n_resamples,
            "seed": self.seed,
            "n_clusters": self.n_clusters,
            "n_observations": self.n_observations,
            "unit": "molecule",
        }


@dataclass(frozen=True)
class PairedBootstrapResult:
    """Paired cluster bootstrap: one resample set shared by both methods."""

    a: BootstrapInterval
    b: BootstrapInterval
    delta: BootstrapInterval

    def as_dict(self) -> dict[str, Any]:
        return {
            "a": self.a.as_dict(),
            "b": self.b.as_dict(),
            "delta": self.delta.as_dict(),
        }


def _validate_clusters(clusters: Mapping[str, Sequence[float]]) -> None:
    if not clusters:
        raise BootstrapError("no molecule clusters supplied")
    for key, values in clusters.items():
        if not str(key):
            raise BootstrapError("cluster key must be a non-empty molecule id")
        if not values:
            raise BootstrapError(f"cluster {key!r} carries no observations")
        for value in values:
            if not math.isfinite(float(value)):
                raise BootstrapError(f"cluster {key!r} carries a non-finite observation: {value!r}")


def _validate_controls(n_resamples: int, alpha: float, seed: int) -> None:
    if n_resamples < 1:
        raise BootstrapError(f"n_resamples must be >= 1, got {n_resamples}")
    if not 0.0 < alpha < 1.0:
        raise BootstrapError(f"alpha must be in (0, 1), got {alpha}")
    if seed < 0:
        raise BootstrapError(f"seed must be >= 0, got {seed}")


def _percentile_interval(samples: Sequence[float], alpha: float) -> tuple[float, float]:
    array = np.asarray(samples, dtype=float)
    low, high = np.percentile(array, [100.0 * alpha / 2.0, 100.0 * (1.0 - alpha / 2.0)])
    return float(low), float(high)


def _pool_of(ordered_values: Sequence[Sequence[float]], indices: np.ndarray) -> list[float]:
    pool: list[float] = []
    for index in indices:
        pool.extend(ordered_values[int(index)])
    return pool


def _check_statistic(value: float, where: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise BootstrapError(f"statistic returned a non-finite value at {where}: {result!r}")
    return result


def clustered_bootstrap_ci(
    clusters: Mapping[str, Sequence[float]],
    *,
    statistic: Statistic = pooled_mean,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> BootstrapInterval:
    """Percentile bootstrap interval resampling MOLECULES with replacement.

    ``clusters`` maps ``molecule_id -> observations`` (residual magnitudes,
    correctness flags, probabilities, ...).  Each resample draws
    ``n_clusters`` molecules with replacement and pools their observations;
    atoms/conformers of one molecule are therefore never independent samples.
    """
    _validate_clusters(clusters)
    _validate_controls(n_resamples, alpha, seed)
    keys = sorted(clusters)
    ordered_values = [list(clusters[key]) for key in keys]
    n_clusters = len(keys)
    point = _check_statistic(
        statistic([value for values in ordered_values for value in values]), "point"
    )
    rng = np.random.default_rng(seed)
    samples: list[float] = []
    for _ in range(n_resamples):
        indices = rng.integers(0, n_clusters, size=n_clusters)
        samples.append(_check_statistic(statistic(_pool_of(ordered_values, indices)), "resample"))
    low, high = _percentile_interval(samples, alpha)
    return BootstrapInterval(
        point=point,
        low=min(low, point),
        high=max(high, point),
        level=1.0 - alpha,
        n_resamples=n_resamples,
        seed=seed,
        n_clusters=n_clusters,
        n_observations=sum(len(values) for values in ordered_values),
    )


def paired_clustered_bootstrap_ci(
    clusters_a: Mapping[str, Sequence[float]],
    clusters_b: Mapping[str, Sequence[float]],
    *,
    statistic: Statistic = pooled_mean,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    alpha: float = DEFAULT_ALPHA,
) -> PairedBootstrapResult:
    """Paired cluster bootstrap sharing one resample set between A and B.

    Both methods must cover the same molecules (same cluster keys); each
    resample draws molecule indices once and evaluates A, B and the delta on
    that same draw, so ``delta`` is a paired estimate.
    """
    _validate_clusters(clusters_a)
    _validate_clusters(clusters_b)
    if set(clusters_a) != set(clusters_b):
        missing_b = sorted(set(clusters_a) - set(clusters_b))
        missing_a = sorted(set(clusters_b) - set(clusters_a))
        raise BootstrapError(
            "paired bootstrap requires identical molecule clusters; "
            f"missing from B: {missing_b}, missing from A: {missing_a}"
        )
    _validate_controls(n_resamples, alpha, seed)
    keys = sorted(clusters_a)
    values_a = [list(clusters_a[key]) for key in keys]
    values_b = [list(clusters_b[key]) for key in keys]
    n_clusters = len(keys)

    def _point(ordered: Sequence[Sequence[float]]) -> float:
        return _check_statistic(
            statistic([value for values in ordered for value in values]), "point"
        )

    point_a = _point(values_a)
    point_b = _point(values_b)
    rng = np.random.default_rng(seed)
    samples_a: list[float] = []
    samples_b: list[float] = []
    samples_delta: list[float] = []
    for _ in range(n_resamples):
        indices = rng.integers(0, n_clusters, size=n_clusters)
        sample_a = _check_statistic(statistic(_pool_of(values_a, indices)), "resample")
        sample_b = _check_statistic(statistic(_pool_of(values_b, indices)), "resample")
        samples_a.append(sample_a)
        samples_b.append(sample_b)
        samples_delta.append(sample_a - sample_b)
    low_a, high_a = _percentile_interval(samples_a, alpha)
    low_b, high_b = _percentile_interval(samples_b, alpha)
    low_delta, high_delta = _percentile_interval(samples_delta, alpha)
    common = {
        "level": 1.0 - alpha,
        "n_resamples": n_resamples,
        "seed": seed,
        "n_clusters": n_clusters,
        "n_observations": sum(len(values) for values in values_a),
    }
    interval_a = BootstrapInterval(
        point=point_a,
        low=min(low_a, point_a),
        high=max(high_a, point_a),
        **common,
    )
    interval_b = BootstrapInterval(
        point=point_b,
        low=min(low_b, point_b),
        high=max(high_b, point_b),
        **common,
    )
    delta_point = point_a - point_b
    interval_delta = BootstrapInterval(
        point=delta_point,
        low=min(low_delta, delta_point),
        high=max(high_delta, delta_point),
        **common,
    )
    return PairedBootstrapResult(a=interval_a, b=interval_b, delta=interval_delta)


__all__ = [
    "BootstrapError",
    "BootstrapInterval",
    "DEFAULT_ALPHA",
    "DEFAULT_BOOTSTRAP_SEED",
    "DEFAULT_N_RESAMPLES",
    "PairedBootstrapResult",
    "clustered_bootstrap_ci",
    "paired_clustered_bootstrap_ci",
    "pooled_mean",
]
