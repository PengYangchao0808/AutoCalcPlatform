# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Boltzmann + equivalence averaging (DevDoc §5 stage 4 / §8.1)."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

from acp.nmr.equivalence import (
    EQ_BASIS_UNKNOWN,
    EquivalenceResult,
    build_all_labels,
    build_label_for_atom,
)
from acp.nmr.models import (
    AtomShift,
    ConformerShielding,
    NmrConfig,
    SignalGroup,
    element_of_nucleus,
    normalize_symbol,
)
from acp.nmr.structure_map import NmrStructureMap

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
    structure_map: NmrStructureMap | None = None,
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

    Every emitted :class:`AtomShift` carries its full :class:`SignalGroup`
    (todo 34 / G08): member uids, averaging coefficients and equivalence
    basis — not just the representative label. Membership uids come from
    *structure_map* when supplied, else from element + 1-based label uids.

    Args:
        conformers: Per-conformer shieldings + Boltzmann weights.
        symbols: Element symbols (length N).
        config: NMR configuration (TMS references, nuclei).
        equivalence_groups: Optional equivalence groups (0-based indices),
            as a plain sequence (basis ``unknown``) or an
            :class:`EquivalenceResult` (per-group basis preserved).
        omit_atom_indices: Atoms to exclude from the result.
        structure_map: Optional stable atom identity map aligned with
            *symbols* (same atom order); when omitted, member uids use the
            per-element label fallback.

    Returns:
        List of :class:`AtomShift` (one per signal). When equivalence groups
        are present, one representative per group is emitted (the
        lowest-indexed member) carrying the full group membership. Empty
        when no conformer is complete.
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

    basis_by_atom: dict[int, str] = {}
    if isinstance(equivalence_groups, EquivalenceResult):
        for eq_group in equivalence_groups:
            for atom_idx in eq_group.indices:
                basis_by_atom[atom_idx] = eq_group.basis

    membership: list[tuple[object | None, list[int]]] = []
    if equivalence_groups:
        for eq_group in equivalence_groups:
            members = sorted(atom_idx for atom_idx in eq_group if atom_idx in avg_shielding)
            if members:
                membership.append((eq_group, members))
        covered = {atom_idx for _, members in membership for atom_idx in members}
    else:
        covered = set()
    for atom_idx in sorted(avg_shielding):
        if atom_idx not in covered:
            membership.append((None, [atom_idx]))

    shifts: list[AtomShift] = []
    for eq_group, members in sorted(membership, key=lambda entry: entry[1][0]):
        representative = members[0]
        sym = normalize_symbol(symbols[representative])
        nucleus = _nucleus_for_element(sym, config)
        if nucleus is None:
            continue
        explicit = _explicit_group_coefficients(eq_group, len(members))
        if explicit is None:
            coefficients = tuple(1.0 / len(members) for _ in members)
            shielding = sum(avg_shielding[i] for i in members) / len(members)
        else:
            coefficients = explicit
            shielding = sum(
                coeff * avg_shielding[i] for coeff, i in zip(explicit, members, strict=True)
            )
        signal_group = SignalGroup(
            atom_uids=tuple(_atom_uid_for(member, symbols, structure_map) for member in members),
            coefficients=coefficients,
            equivalence_basis=basis_by_atom.get(representative, EQ_BASIS_UNKNOWN),
        )
        shifts.append(
            AtomShift(
                atom_index=representative,
                symbol=sym,
                nucleus=nucleus,
                shielding_ppm=shielding,
                shift_ppm=_shielding_to_shift(shielding, nucleus, config),
                atom_label=build_label_for_atom(representative, symbols),
                signal_group=signal_group,
            )
        )
    return shifts


def _atom_uid_for(
    atom_index: int,
    symbols: Sequence[str],
    structure_map: NmrStructureMap | None,
) -> str:
    """Stable member uid: structure-map uid when available, else label uid."""
    if structure_map is not None:
        return structure_map.atom_uid_for_mol(atom_index)
    return build_label_for_atom(atom_index, list(symbols))


def _explicit_group_coefficients(
    group: object | None,
    n_members: int,
) -> tuple[float, ...] | None:
    """Explicit averaging coefficients carried by *group*, or ``None``.

    Today's :class:`~acp.nmr.equivalence.EquivalenceGroup` carries no
    coefficients, so equal weights (``1/n``) are the default. A richer
    group object exposing a ``coefficients`` sequence is honored only when
    it matches the surviving member count and is finite; anything else
    falls back to equal weights with a warning — never a silent partial
    weighting.
    """
    raw = getattr(group, "coefficients", None)
    if raw is None:
        return None
    try:
        values = tuple(float(c) for c in raw)
    except (TypeError, ValueError):
        logger.warning("group carries malformed coefficients %r; using equal weights", raw)
        return None
    if len(values) != n_members or any(not math.isfinite(c) for c in values):
        logger.warning(
            "group coefficients %r unusable for %d surviving members; using equal weights",
            raw,
            n_members,
        )
        return None
    return values


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
