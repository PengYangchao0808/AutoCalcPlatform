# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Symmetry-equivalence detection (DevDoc §5/§8.3).

For unassigned spectra the workflow must detect topologically equivalent
atoms (CH3 hydrogens, CH2 hydrogens, symmetric carbons, ...) and average
their computed shieldings into one "signal" before Hungarian matching.
RDKit's :func:`CanonicalRankAtoms` with ``breakTies=False`` returns a
canonical rank per atom; atoms sharing a rank are symmetry-equivalent.

The module also accepts explicit equivalence groups from the experimental
input (``EQ:`` lines) — these take precedence when present.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from acp.nmr.models import normalize_symbol

if TYPE_CHECKING:
    from rdkit import Chem

logger = logging.getLogger(__name__)


def _mol_from_symbols(symbols: list[str]) -> Chem.Mol:
    """Build a minimal RDKit :class:`Mol` from element symbols only.

    The NMR workflow already has optimized 3D coordinates, but RDKit's
    symmetry ranking only needs the connectivity. We build a single-atom
    graph per element and add zero-order bonds; canonical ranking then
    collapses only atoms of identical element + environment. Because we
    lack connectivity, we use the element-only graph: atoms of the same
    element collapse into one equivalence group.

    This is intentionally conservative — for real connectivity-driven
    equivalence (e.g. distinguishing two inequivalent CH3 groups) supply
    ``EQ:`` groups in the experimental input or pass a bonded RDKit Mol.
    """
    from rdkit import Chem

    mol = Chem.RWMol()
    for symbol in symbols:
        sym = normalize_symbol(symbol)
        try:
            atomic_num = Chem.GetPeriodicTable().GetAtomicNumber(sym)
        except (RuntimeError, ValueError, AttributeError):
            atomic_num = 0
        atom = Chem.Atom(atomic_num if atomic_num > 0 else 0)
        mol.AddAtom(atom)
    return mol.GetMol()


def detect_equivalence_groups(
    symbols: list[str],
    mol: Chem.Mol | None = None,
) -> list[list[int]]:
    """Return symmetry-equivalent atom-index groups (0-based).

    When *mol* is provided with full connectivity, RDKit's
    :func:`CanonicalRankAtoms` derives true topological equivalence.
    Otherwise a fallback groups atoms by element only (a coarse
    over-approximation suitable for the simplest symmetric molecules).

    Args:
        symbols: Element symbols (length N).
        mol: Optional RDKit :class:`Mol` with the same atom ordering.

    Returns:
        List of equivalence groups (each a list of 0-based atom indices).
        Singletons are included (every atom belongs to exactly one group).
    """
    n_atoms = len(symbols)
    if n_atoms == 0:
        return []

    if mol is None:
        mol = _mol_from_symbols(symbols)

    try:
        from rdkit import Chem
    except ImportError:  # pragma: no cover - rdkit is a hard dependency
        logger.warning("RDKit unavailable; falling back to element-only equivalence")
        return _element_groups(symbols)

    # CanonicalRankAtoms requires a sanitized, bonded molecule. When we
    # only have element symbols (no connectivity) the call either raises
    # or dumps a pre-condition violation to stderr; the element-only
    # fallback is the correct coarse approximation either way.
    try:
        mol.UpdatePropertyCache(strict=False)
        Chem.GetSymmSSSR(mol)  # populate ring info; no-op on unbonded mol
        ranks = Chem.CanonicalRankAtoms(mol, breakTies=False)
    except Exception as exc:
        logger.debug("CanonicalRankAtoms unavailable (%s); using element groups", exc)
        return _element_groups(symbols)

    by_rank: dict[int, list[int]] = {}
    for atom_idx, rank in enumerate(ranks):
        rank_int = int(rank) if rank is not None else atom_idx
        by_rank.setdefault(rank_int, []).append(atom_idx)

    # split ranks by element so H and C never merge
    groups: list[list[int]] = []
    for rank_group in by_rank.values():
        by_elem: dict[str, list[int]] = {}
        for atom_idx in rank_group:
            sym = normalize_symbol(symbols[atom_idx])
            by_elem.setdefault(sym, []).append(atom_idx)
        groups.extend(by_elem.values())
    return groups


def _element_groups(symbols: list[str]) -> list[list[int]]:
    """Fallback: group atoms strictly by element."""
    by_elem: dict[str, list[int]] = {}
    for atom_idx, symbol in enumerate(symbols):
        by_elem.setdefault(normalize_symbol(symbol), []).append(atom_idx)
    return list(by_elem.values())


def merge_explicit_and_detected(
    explicit: list[list[str]],
    detected: list[list[int]],
    symbols: list[str],
) -> list[list[int]]:
    """Merge explicit ``EQ:`` groups (atom labels) with detected groups.

    Explicit groups from the experimental input take precedence: atoms in
    an explicit group are removed from detected groups, and detected groups
    that collapse to a single atom (or empty) are dropped.
    """
    if not explicit:
        return detected

    label_to_idx = _build_label_index(symbols)
    claimed: set[int] = set()
    merged: list[list[int]] = []

    for group in explicit:
        idx_group = [label_to_idx[label] for label in group if label in label_to_idx]
        idx_group = [i for i in idx_group if i is not None]
        if idx_group:
            merged.append(idx_group)
            claimed.update(idx_group)

    for det in detected:
        remaining = [i for i in det if i not in claimed]
        if len(remaining) > 1:
            merged.append(remaining)
        elif len(remaining) == 1:
            merged.append(remaining)
    return merged


def _build_label_index(symbols: list[str]) -> dict[str, int]:
    """Map ``"C1"``/``"H4"``-style labels to 0-based atom indices.

    Convention: label prefix matches the element, the trailing number is
    the 1-based index among atoms of that element (so ``"C1"`` is the
    first carbon, ``"H3"`` the third hydrogen). The workflow emits these
    same labels for :class:`AtomShift`.
    """
    counters: dict[str, int] = {}
    label_to_idx: dict[str, int] = {}
    for atom_idx, symbol in enumerate(symbols):
        sym = normalize_symbol(symbol)
        counters[sym] = counters.get(sym, 0) + 1
        label_to_idx[f"{sym}{counters[sym]}"] = atom_idx
    return label_to_idx


def build_label_for_atom(atom_index: int, symbols: list[str]) -> str:
    """Return the canonical atom label (``"C1"``, ``"H4"``) for an index."""
    sym = normalize_symbol(symbols[atom_index])
    count = 0
    for i in range(atom_index + 1):
        if normalize_symbol(symbols[i]) == sym:
            count += 1
    return f"{sym}{count}"


def build_all_labels(symbols: list[str]) -> list[str]:
    """Return the canonical label per atom (parallel to *symbols*)."""
    return [build_label_for_atom(i, symbols) for i in range(len(symbols))]


__all__ = [
    "detect_equivalence_groups",
    "merge_explicit_and_detected",
    "build_label_for_atom",
    "build_all_labels",
]
