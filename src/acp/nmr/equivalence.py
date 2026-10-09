# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Symmetry-equivalence detection (DevDoc §5/§8.3).

For unassigned spectra the workflow must detect topologically equivalent
atoms (CH3 hydrogens, CH2 hydrogens, symmetric carbons, ...) and average
their computed shieldings into one "signal" before Hungarian matching.
RDKit's :func:`CanonicalRankAtoms` with ``breakTies=False`` returns a
canonical rank per atom; atoms sharing a rank are symmetry-equivalent.

The module also accepts explicit equivalence groups from the experimental
input (``EQ:`` lines) — these take precedence when present.

G01 contract: without a bonded molecular graph atoms are NEVER merged —
each atom stays a singleton group with basis ``"unknown"`` (surfaced via
``EquivalenceResult.equivalence_unknown``); strict mode raises
:class:`EquivalenceError` instead.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from acp.nmr.models import (
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    SIGNAL_GROUP_BASES,
    normalize_symbol,
)

if TYPE_CHECKING:
    from rdkit import Chem

logger = logging.getLogger(__name__)

# Closed, JSON-serializable vocabulary for group equivalence bases —
# defined once in acp.nmr.models (the leaf module) and re-exported here so
# SignalGroup validation and equivalence detection cannot drift apart.
_EQ_BASIS_VALUES = frozenset(SIGNAL_GROUP_BASES)


class EquivalenceError(ValueError):
    """Strict mode: equivalence cannot be established without a molecular graph."""


@dataclass(frozen=True)
class EquivalenceGroup:
    """Atom-index group with its serializable equivalence basis."""

    indices: tuple[int, ...]
    basis: str

    def __post_init__(self) -> None:
        if self.basis not in _EQ_BASIS_VALUES:
            raise ValueError(f"unknown equivalence basis {self.basis!r}")

    def __iter__(self) -> Iterator[int]:
        return iter(self.indices)

    def __len__(self) -> int:
        return len(self.indices)

    def __contains__(self, item: object) -> bool:
        return item in self.indices

    def __getitem__(self, item: int | slice) -> tuple[int, ...]:
        return self.indices[item]

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable provenance record (report evidence)."""
        return {"indices": list(self.indices), "basis": self.basis}


@dataclass(frozen=True)
class EquivalenceResult:
    """Partition of atoms into groups, each carrying its equivalence basis.

    Iterates as plain groups so existing consumers keep working.
    """

    groups: tuple[EquivalenceGroup, ...]

    def __iter__(self) -> Iterator[EquivalenceGroup]:
        return iter(self.groups)

    def __len__(self) -> int:
        return len(self.groups)

    def __getitem__(self, item: int) -> EquivalenceGroup:
        return self.groups[item]

    @property
    def equivalence_unknown(self) -> bool:
        """True when any group is not justified by a molecular graph."""
        return any(g.basis == EQ_BASIS_UNKNOWN for g in self.groups)

    @property
    def basis(self) -> str:
        """Overall basis: one shared value, ``"mixed"``, or ``"empty"``."""
        distinct = {g.basis for g in self.groups}
        if not distinct:
            return "empty"
        if len(distinct) == 1:
            return next(iter(distinct))
        return "mixed"

    @classmethod
    def singletons(cls, n_atoms: int, basis: str) -> EquivalenceResult:
        """One single-atom group per atom, all carrying *basis*."""
        return cls(tuple(EquivalenceGroup((i,), basis) for i in range(n_atoms)))


def detect_equivalence_groups(
    symbols: Sequence[str],
    mol: Chem.Mol | None = None,
    *,
    strict: bool = False,
) -> EquivalenceResult:
    """Return symmetry-equivalent atom-index groups (0-based).

    A bonded *mol* yields true topological equivalence (RDKit
    :func:`CanonicalRankAtoms`, ``breakTies=False``) with basis
    ``"topology"``. Without a graph — or when ranking fails — every atom
    becomes a singleton with basis ``"unknown"`` and
    ``result.equivalence_unknown`` is True: same-element atoms are never
    merged without proof of equivalence (G01).

    Args:
        symbols: Element symbols (length N).
        mol: Optional RDKit :class:`Mol` with the same atom ordering.
        strict: Reject instead of degrading when no topology is usable.

    Returns:
        :class:`EquivalenceResult` — iterates as groups; singletons are
        included (every atom belongs to exactly one group).

    Raises:
        EquivalenceError: *strict* and no usable molecular topology.
    """
    n_atoms = len(symbols)
    if n_atoms == 0:
        return EquivalenceResult(())
    if mol is None:
        return _degraded(
            n_atoms,
            strict,
            "no bonded molecular graph; strict_equivalence refuses to merge atoms",
        )
    if mol.GetNumAtoms() != n_atoms:
        return _degraded(
            n_atoms,
            strict,
            f"molecular graph has {mol.GetNumAtoms()} atoms but {n_atoms} symbols",
        )

    try:
        from rdkit import Chem
    except ImportError:  # pragma: no cover - rdkit is a hard dependency
        return _degraded(n_atoms, strict, "RDKit unavailable")

    try:
        mol.UpdatePropertyCache(strict=False)
        Chem.GetSymmSSSR(mol)  # populate ring info
        ranks = Chem.CanonicalRankAtoms(mol, breakTies=False)
    except (RuntimeError, ValueError) as exc:
        return _degraded(n_atoms, strict, f"canonical ranking failed: {exc}")

    by_rank: dict[int, list[int]] = {}
    for atom_idx, rank in enumerate(ranks):
        rank_int = int(rank) if rank is not None else atom_idx
        by_rank.setdefault(rank_int, []).append(atom_idx)

    # split ranks by element so H and C never merge
    groups: list[EquivalenceGroup] = []
    for rank_group in by_rank.values():
        by_elem: dict[str, list[int]] = {}
        for atom_idx in rank_group:
            sym = normalize_symbol(symbols[atom_idx])
            by_elem.setdefault(sym, []).append(atom_idx)
        for indices in by_elem.values():
            groups.append(EquivalenceGroup(tuple(indices), EQ_BASIS_TOPOLOGY))
    return EquivalenceResult(tuple(groups))


def _degraded(n_atoms: int, strict: bool, reason: str) -> EquivalenceResult:
    """Singletons with unknown basis, or the strict-mode rejection."""
    if strict:
        raise EquivalenceError(reason)
    logger.debug("equivalence degraded to singletons: %s", reason)
    return EquivalenceResult.singletons(n_atoms, EQ_BASIS_UNKNOWN)


def merge_explicit_and_detected(
    explicit: Sequence[Sequence[str]],
    detected: EquivalenceResult | Sequence[Sequence[int]],
    symbols: Sequence[str],
) -> EquivalenceResult:
    """Merge explicit ``EQ:`` groups (atom labels) with detected groups.

    Explicit groups from the experimental input take precedence: their
    atoms are claimed out of the detected groups, and every remaining
    atom keeps its detected basis. Atoms the explicit groups do not
    cover are never re-merged by element (G01 partial-EQ failure mode).
    Plain-sequence *detected* inputs are treated as basis ``"unknown"`` —
    provenance is never over-claimed.
    """
    if not explicit:
        return _as_result(detected)

    label_to_idx = _build_label_index(list(symbols))
    claimed: set[int] = set()
    merged: list[EquivalenceGroup] = []

    for group in explicit:
        idx_group = [label_to_idx[label] for label in group if label in label_to_idx]
        if idx_group:
            merged.append(EquivalenceGroup(tuple(idx_group), EQ_BASIS_EXPLICIT))
            claimed.update(idx_group)

    for det in _iter_groups(detected):
        remaining = tuple(i for i in det if i not in claimed)
        if remaining:
            merged.append(EquivalenceGroup(remaining, det.basis))
    return EquivalenceResult(tuple(merged))


def _as_result(detected: EquivalenceResult | Sequence[Sequence[int]]) -> EquivalenceResult:
    if isinstance(detected, EquivalenceResult):
        return detected
    return EquivalenceResult(tuple(_iter_groups(detected)))


def _iter_groups(
    detected: EquivalenceResult | Sequence[Sequence[int]],
) -> Iterator[EquivalenceGroup]:
    if isinstance(detected, EquivalenceResult):
        yield from detected.groups
        return
    for group in detected:
        yield EquivalenceGroup(tuple(int(i) for i in group), EQ_BASIS_UNKNOWN)


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
    "EQ_BASIS_EXPLICIT",
    "EQ_BASIS_TOPOLOGY",
    "EQ_BASIS_UNKNOWN",
    "EquivalenceError",
    "EquivalenceGroup",
    "EquivalenceResult",
    "detect_equivalence_groups",
    "merge_explicit_and_detected",
    "build_label_for_atom",
    "build_all_labels",
]
