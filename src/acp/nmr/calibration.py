# pyright: reportMissingTypeStubs=false
"""NMR conformer selection, calibration, and averaging helpers."""

from __future__ import annotations

import logging
import math

from acp.core.models import StructureEnsemble, StructureRecord
from acp.nmr.models import (
    NMRAveragedAtomResult,
    NMRAtomShielding,
    NMRAtomShift,
    NMRConformerResult,
)

logger = logging.getLogger(__name__)

_GAS_CONSTANT_HARTREE = 8.314462618 / 2625500.0
_NUCLEUS_BY_SYMBOL = {
    "B": "11B",
    "C": "13C",
    "Cl": "35Cl",
    "F": "19F",
    "H": "1H",
    "N": "15N",
    "O": "17O",
    "P": "31P",
    "Si": "29Si",
    "S": "33S",
}


def _normalize_symbol(symbol: str) -> str:
    """Return a normalized element symbol."""
    normalized = symbol.strip()
    if not normalized:
        return normalized
    return normalized[:1].upper() + normalized[1:].lower()


def _energy_for_weighting(conformer: NMRConformerResult) -> float | None:
    """Return the preferred energy for Boltzmann weighting."""
    if conformer.free_energy_hartree is not None:
        return conformer.free_energy_hartree
    return conformer.energy_hartree


def _iter_atom_shifts(conformer: NMRConformerResult) -> list[NMRAtomShift]:
    """Return calibrated shifts or synthesize shift shells from shieldings."""
    if conformer.shifts:
        return list(conformer.shifts)

    return [
        NMRAtomShift(
            atom_index=shielding.atom_index,
            symbol=shielding.symbol,
            nucleus=assign_nucleus(shielding.symbol),
            shielding_ppm=shielding.isotropic_ppm,
            reference_ppm=None,
            shift_ppm=None,
            anisotropy_ppm=shielding.anisotropy_ppm,
        )
        for shielding in conformer.shieldings
    ]


def _boltzmann_weights(
    conformer_results: list[NMRConformerResult],
    temperature: float,
) -> list[float]:
    """Return normalized Boltzmann weights from conformer energies."""
    if temperature <= 0.0:
        raise ValueError("temperature must be positive")

    energies = [_energy_for_weighting(conformer) for conformer in conformer_results]
    valid_energies = [energy for energy in energies if energy is not None]
    if not valid_energies:
        raise ValueError("Cannot average NMR results without conformer energies")

    min_energy = min(valid_energies)
    raw_weights: list[float] = []
    for energy in energies:
        if energy is None:
            raw_weights.append(0.0)
            continue
        raw_weights.append(
            math.exp(-(energy - min_energy) / (_GAS_CONSTANT_HARTREE * temperature))
        )

    total = sum(raw_weights)
    if total <= 0.0:
        equal_weight = 1.0 / len(conformer_results)
        return [equal_weight] * len(conformer_results)

    return [weight / total for weight in raw_weights]


def select_conformers(
    ensemble: StructureEnsemble,
    energy_window_kcal: float = 3.0,
    max_conformers: int = 10,
) -> list[StructureRecord]:
    """Select low-energy conformers for downstream NMR evaluation."""
    if max_conformers <= 0:
        return []
    return ensemble.window_select(energy_window_kcal=energy_window_kcal)[:max_conformers]


def assign_nucleus(symbol: str) -> str | None:
    """Map an element symbol onto the default NMR-active nucleus label."""
    return _NUCLEUS_BY_SYMBOL.get(_normalize_symbol(symbol))


def calibrate_shifts(
    shieldings: list[NMRAtomShielding],
    references: dict[str, float | None],
) -> list[NMRAtomShift]:
    """Convert isotropic shieldings into referenced chemical shifts."""
    shifts: list[NMRAtomShift] = []
    for shielding in shieldings:
        nucleus = assign_nucleus(shielding.symbol)
        reference_ppm = references.get(nucleus) if nucleus is not None else None
        shift_ppm = (
            reference_ppm - shielding.isotropic_ppm
            if reference_ppm is not None
            else None
        )
        shifts.append(
            NMRAtomShift(
                atom_index=shielding.atom_index,
                symbol=shielding.symbol,
                nucleus=nucleus,
                shielding_ppm=shielding.isotropic_ppm,
                reference_ppm=reference_ppm,
                shift_ppm=shift_ppm,
                anisotropy_ppm=shielding.anisotropy_ppm,
            )
        )
    return shifts


def average_atom_results(
    conformer_results: list[NMRConformerResult],
    temperature: float = 298.15,
) -> list[NMRAveragedAtomResult]:
    """Boltzmann-average atom shieldings and referenced shifts across conformers."""
    if not conformer_results:
        return []

    weights = _boltzmann_weights(conformer_results, temperature)
    grouped: dict[tuple[int, str, str | None], list[tuple[NMRAtomShift, float]]] = {}

    for conformer, weight in zip(conformer_results, weights, strict=True):
        for shift in _iter_atom_shifts(conformer):
            key = (shift.atom_index, shift.symbol, shift.nucleus)
            grouped.setdefault(key, []).append((shift, weight))

    averaged: list[NMRAveragedAtomResult] = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1], item[2] or "")):
        entries = grouped[key]
        total_weight = sum(weight for _, weight in entries)
        if total_weight <= 0.0:
            continue

        reference_values = {
            shift.reference_ppm
            for shift, _ in entries
            if shift.reference_ppm is not None
        }
        if len(reference_values) > 1:
            raise ValueError(
                f"Inconsistent NMR references for atom {key[0]} {key[1]} {key[2]}: {sorted(reference_values)}"
            )

        reference_ppm = next(iter(reference_values), None)
        averaged_shielding_ppm = sum(
            shift.shielding_ppm * (weight / total_weight)
            for shift, weight in entries
        )
        averaged_shift_ppm = (
            reference_ppm - averaged_shielding_ppm
            if reference_ppm is not None
            else None
        )

        averaged.append(
            NMRAveragedAtomResult(
                atom_index=key[0],
                symbol=key[1],
                nucleus=key[2],
                averaged_shielding_ppm=averaged_shielding_ppm,
                reference_ppm=reference_ppm,
                averaged_shift_ppm=averaged_shift_ppm,
            )
        )

    logger.debug("Averaged %d atom NMR results", len(averaged))
    return averaged


__all__ = [
    "select_conformers",
    "assign_nucleus",
    "calibrate_shifts",
    "average_atom_results",
]
