# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Boltzmann + equivalence averaging (DevDoc §5 stage 4 / §8.1)."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from acp.nmr.equivalence import EquivalenceResult, build_all_labels, build_label_for_atom
from acp.nmr.models import (
    AtomShift,
    ConformerShielding,
    NmrConfig,
    element_of_nucleus,
    normalize_symbol,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _isotropic_value(shielding: dict[str, object] | None) -> float | None:
    """Finite isotropic shielding value, or ``None`` when absent/malformed."""
    if not shielding or "isotropic" not in shielding:
        return None
    try:
        value = float(shielding["isotropic"])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _required_atom_indices(
    symbols: Sequence[str],
    config: NmrConfig,
    omit_atom_indices: Sequence[int] | None = None,
) -> list[int]:
    """Atoms whose configured nucleus must be present for a conformer to count."""
    omit_set = set(omit_atom_indices or [])
    required: list[int] = []
    for atom_idx, symbol in enumerate(symbols):
        if atom_idx in omit_set:
            continue
        if _nucleus_for_element(normalize_symbol(symbol), config) is not None:
            required.append(atom_idx)
    return required


def incomplete_conformer_ids(
    conformers: Sequence[ConformerShielding],
    symbols: Sequence[str],
    config: NmrConfig,
    omit_atom_indices: Sequence[int] | None = None,
) -> list[str]:
    """Ids of conformers missing at least one required-nucleus shielding.

    Required = every non-omitted atom whose element has a configured
    nucleus. A conformer missing any of them is unusable as a whole (G09) —
    it is never partially averaged into a subset of atoms.
    """
    required = _required_atom_indices(symbols, config, omit_atom_indices)
    incomplete: list[str] = []
    for conf in conformers:
        if any(_isotropic_value(conf.shieldings.get(atom_idx)) is None for atom_idx in required):
            incomplete.append(conf.conformer_id)
    return incomplete


def boltzmann_average_shieldings(
    conformers: list[ConformerShielding],
    symbols: list[str],
    config: NmrConfig,
    equivalence_groups: Sequence[Sequence[int]] | EquivalenceResult | None = None,
    omit_atom_indices: list[int] | None = None,
) -> list[AtomShift]:
    """Average per-conformer shieldings into per-atom shifts.

    Steps (DevDoc §8.1 / §8.2):

    1. Conformer completeness gate (G09): conformers missing any required
       nucleus shielding are excluded WHOLE — every atom then averages the
       same conformer subset with one shared renormalization (the former
       per-atom denominator silently averaged different ensembles per atom).
    2. ``σ_avg(atom) = Σ_i w_i · σ_i(atom)`` over the complete conformers.
    3. Equivalence-group averaging: members of a group are replaced by
       their mean. Atoms without an equivalence group are singletons.
    4. TMS conversion: ``δ_calc = σ_TMS − σ_avg`` using the configured
       reference for the atom's nucleus.

    Args:
        conformers: Per-conformer shieldings + Boltzmann weights.
        symbols: Element symbols (length N).
        config: NMR configuration (TMS references, nuclei).
        equivalence_groups: Optional equivalence groups (0-based indices).
        omit_atom_indices: Atoms to exclude from the result.

    Returns:
        List of :class:`AtomShift` (one per non-omitted atom / group
        representative). When equivalence groups are present, only one
        representative per group is emitted (the lowest-indexed member).
        Empty when no conformer is complete.
    """
    omit_set = set(omit_atom_indices or [])
    n_atoms = len(symbols)
    if n_atoms == 0 or not conformers:
        return []

    required = _required_atom_indices(symbols, config, omit_atom_indices)
    complete = [
        conf
        for conf in conformers
        if all(_isotropic_value(conf.shieldings.get(atom_idx)) is not None for atom_idx in required)
    ]
    if not complete:
        logger.warning("No conformer carries complete required-nuclei shieldings")
        return []
    total_weight = sum(float(conf.boltzmann_weight) for conf in complete)
    if total_weight > 0:
        weights = [float(conf.boltzmann_weight) / total_weight for conf in complete]
    else:
        weights = [1.0 / len(complete)] * len(complete)

    # raw Boltzmann-weighted shielding per atom — one shared conformer subset
    # and one shared weight normalization for every atom
    avg_shielding: dict[int, float] = {}
    for atom_idx in range(n_atoms):
        if atom_idx in omit_set:
            continue
        if _nucleus_for_element(normalize_symbol(symbols[atom_idx]), config) is None:
            continue
        total = 0.0
        for conf, weight in zip(complete, weights, strict=True):
            value = _isotropic_value(conf.shieldings.get(atom_idx))
            if value is None:
                continue
            total += weight * value
        avg_shielding[atom_idx] = total

    # equivalence averaging — replace each member's value with the group mean
    if equivalence_groups:
        group_of: dict[int, int] = {}
        group_means: dict[int, float] = {}
        for g_idx, group in enumerate(equivalence_groups):
            members = [i for i in group if i in avg_shielding]
            if not members:
                continue
            mean = sum(avg_shielding[i] for i in members) / len(members)
            group_means[g_idx] = mean
            for m in members:
                group_of[m] = g_idx

        representatives: set[int] = set()
        for g_idx, group in enumerate(equivalence_groups):
            members = [i for i in group if i in avg_shielding]
            if members:
                representatives.add(min(members))
        # singletons (not in any group) stay as their own representative
        for atom_idx in avg_shielding:
            if atom_idx not in group_of:
                representatives.add(atom_idx)

        result_atoms = sorted(representatives)
        shifts: list[AtomShift] = []
        for atom_idx in result_atoms:
            sym = normalize_symbol(symbols[atom_idx])
            nucleus = _nucleus_for_element(sym, config)
            if nucleus is None:
                continue
            g_idx = group_of.get(atom_idx)
            shielding = group_means[g_idx] if g_idx is not None else avg_shielding[atom_idx]
            shift = _shielding_to_shift(shielding, nucleus, config)
            shifts.append(
                AtomShift(
                    atom_index=atom_idx,
                    symbol=sym,
                    nucleus=nucleus,
                    shielding_ppm=shielding,
                    shift_ppm=shift,
                    atom_label=build_label_for_atom(atom_idx, symbols),
                )
            )
        return shifts

    # no equivalence grouping — one shift per atom
    shifts = []
    for atom_idx in sorted(avg_shielding):
        sym = normalize_symbol(symbols[atom_idx])
        nucleus = _nucleus_for_element(sym, config)
        if nucleus is None:
            continue
        shielding = avg_shielding[atom_idx]
        shift = _shielding_to_shift(shielding, nucleus, config)
        shifts.append(
            AtomShift(
                atom_index=atom_idx,
                symbol=sym,
                nucleus=nucleus,
                shielding_ppm=shielding,
                shift_ppm=shift,
                atom_label=build_label_for_atom(atom_idx, symbols),
            )
        )
    return shifts


def _nucleus_for_element(element: str, config: NmrConfig) -> str | None:
    """Return the configured nucleus label for *element* (or None)."""
    for nucleus in config.nuclei:
        if element_of_nucleus(nucleus).lower() == element.lower():
            return nucleus
    return None


def _shielding_to_shift(shielding: float, nucleus: str, config: NmrConfig) -> float:
    """Convert shielding → chemical shift via the TMS reference.

    Uses Goodman's corrected formula (NMR.py:392):
    ``δ = (σ_TMS − σ) / (1 − σ_TMS/10⁶)``.

    The ``(1 − σ_TMS/10⁶)`` denominator is a relativistic correction
    (~0.019 % for ¹³C); the simpler ``δ = σ_TMS − σ`` differs by a
    constant factor absorbed by the internal-scaling regression, so DP4/DP5
    probabilities are unaffected either way.
    """
    ref = config.tms_for(nucleus)
    if ref is None:
        return 0.0
    ref = float(ref)
    sigma = float(shielding)
    return (ref - sigma) / (1.0 - ref / 1e6)


def labels_for_atoms(symbols: list[str]) -> list[str]:
    """Re-export :func:`build_all_labels` for convenience."""
    return build_all_labels(symbols)


__all__ = ["boltzmann_average_shieldings", "incomplete_conformer_ids", "labels_for_atoms"]
