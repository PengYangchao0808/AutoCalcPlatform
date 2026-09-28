"""Boltzmann weighting and relative-energy helpers for Confsearch."""

from __future__ import annotations

import math
from typing import Literal

HARTREE_TO_KCAL = 627.5094740631
_KCAL_PER_HARTREE = HARTREE_TO_KCAL
_RGAS_KCAL_MOL_K = 0.001987204258


def boltzmann_weights(
    energies: list[float | None],
    temperature_k: float = 298.15,
    *,
    missing: Literal["zero", "none"] = "zero",
) -> list[float | None]:
    """Boltzmann weights from energies (Hartree).

    A numerically stable softmax over ``-E/kT``.

    Args:
        energies: Energies in Hartree; ``None``/non-finite entries are missing.
        temperature_k: Temperature in Kelvin.
        missing: Policy for missing entries. ``"zero"`` (historical default)
            keeps them in the output with weight 0.0 while they stay in the
            normalization denominator. ``"none"`` maps them to ``None`` and
            excludes them from the denominator, so the finite entries
            normalize to sum 1.0 (all-missing input returns all ``None``).
    """
    if missing == "none":
        return _boltzmann_weights_none(energies, temperature_k)
    finite = [float(e) for e in energies if e is not None and math.isfinite(float(e))]
    if not finite:
        return [0.0 if e is None else None for e in energies]
    beta = 1.0 / (_RGAS_KCAL_MOL_K / _KCAL_PER_HARTREE * temperature_k)
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


def _boltzmann_weights_none(
    energies: list[float | None],
    temperature_k: float,
) -> list[float | None]:
    """Weights with missing entries as ``None``, excluded from normalization."""
    finite = [float(e) for e in energies if e is not None and math.isfinite(float(e))]
    if not finite:
        return [None] * len(energies)
    beta = 1.0 / (_RGAS_KCAL_MOL_K / _KCAL_PER_HARTREE * temperature_k)
    floor = min(finite)
    exps: list[float | None] = []
    for value in energies:
        if value is None or not math.isfinite(float(value)):
            exps.append(None)
        else:
            exps.append(math.exp(-beta * (float(value) - floor)))
    total = sum(v for v in exps if v is not None)
    if total <= 0.0:
        return [None] * len(energies)
    return [None if value is None else value / total for value in exps]


def relative_energies_kcal(
    energies: list[float | None],
) -> list[float | None]:
    """Relative energies in kcal/mol against the lowest finite entry."""
    finite = [float(e) for e in energies if e is not None and math.isfinite(float(e))]
    if not finite:
        return [None] * len(energies)
    floor = min(finite)
    return [None if e is None else (float(e) - floor) * _KCAL_PER_HARTREE for e in energies]


__all__ = ["HARTREE_TO_KCAL", "boltzmann_weights", "relative_energies_kcal"]
