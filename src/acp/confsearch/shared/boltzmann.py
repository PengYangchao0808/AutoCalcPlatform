"""Boltzmann weighting and relative-energy helpers for Confsearch."""

from __future__ import annotations

import math

HARTREE_TO_KCAL = 627.5094740631
_KCAL_PER_HARTREE = HARTREE_TO_KCAL
_RGAS_KCAL_MOL_K = 0.001987204258


def boltzmann_weights(
    energies: list[float | None],
    temperature_k: float = 298.15,
) -> list[float | None]:
    """Boltzmann weights from energies (Hartree); ``None`` propagates as 0.

    A numerically stable softmax over ``-E/kT``.
    """
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
