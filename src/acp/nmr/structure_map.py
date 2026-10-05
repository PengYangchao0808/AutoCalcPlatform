# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false
"""Stable atom identity and label-scheme mapping for NMR structures (G03).

Bridges the numbering spaces of the NMR pipeline — input (source) atom
order, candidate/conformer atom order (the mol handed to QC), RDKit
canonical ranks, and display/assignment labels — so user labels, computed
indices and Goodman-format text never share one implicit convention
(gap investigation G03: ``equivalence._build_label_index`` must not stay
the only label source).

``atom_uid`` scheme
-------------------
Every atom carries ``atom_uid = f"{element}:{rank}"`` where *rank* is the
RDKit canonical rank from
``Chem.CanonicalRankAtoms(mol, breakTies=True, includeChirality=True)``
evaluated on the **source-ordered** copy of the molecule (atoms renumbered
so that position == ``source_atom_index``), then scattered back to the
mol's own order.

Guarantees (locked by tests):

* Within one map uids are unique — ``breakTies=True`` ranks form a
  0..n-1 permutation.
* Rebuilding from an atom-shuffled copy of the *same* source structure
  with the same ``source_atom_indices`` provenance reproduces the
  identical ``atom_uid ↔ atom`` binding, hence identical labels and
  consistent ``atom_uid → shift`` joins.
* Even without provenance, ``atom_uid → element`` is invariant under atom
  reordering: canonical tie-breaking only permutes ranks inside a
  symmetry class, and class members always share the element.
* Across *different* source representations (SMILES vs SDF atom order)
  only the class-level binding is guaranteed — symmetry-equivalent atoms
  may exchange ranks. Their equivalence-averaged shieldings are equal, so
  ``atom_uid → shift`` on averaged values stays consistent.

Label schemes
-------------
* ``per-element`` — element + 1-based ordinal within the element, counted
  over **source** order (``C1``/``H1``; identical to
  ``equivalence.build_all_labels`` for identity-provenance structures).
* ``goodman`` — element + 1-based ordinal over the **whole** source atom
  sequence (``a+1``): for ``['C','O','C','H']`` the second carbon is
  ``C3`` (Goodman ``NMR.py`` convention).

Labels follow the source (input) numbering; mol/QC indices follow the
structure order shieldings come back in. Both directions are queryable.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from acp.nmr.models import normalize_symbol

if TYPE_CHECKING:
    from rdkit import Chem

logger = logging.getLogger(__name__)

LABEL_SCHEME_PER_ELEMENT = "per-element"
LABEL_SCHEME_GOODMAN = "goodman"
LABEL_SCHEMES: tuple[str, ...] = (LABEL_SCHEME_PER_ELEMENT, LABEL_SCHEME_GOODMAN)

_LABEL_RE = re.compile(r"^([A-Z][a-z]?)(\d+)$")
_ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$")
_AMBIGUOUS_SPLIT_RE = re.compile(r"\s+or\s+", re.IGNORECASE)
_TOKEN_SPLIT_RE = re.compile(r"[\s,/]+")


class StructureMapError(ValueError):
    """Typed error: malformed labels, unknown schemes/elements, bad ranks/indices."""


def _check_scheme(label_scheme: str) -> str:
    if label_scheme not in LABEL_SCHEMES:
        raise StructureMapError(
            f"unknown label scheme {label_scheme!r}; expected one of {', '.join(LABEL_SCHEMES)}"
        )
    return label_scheme


def parse_ambiguous_labels(text: str) -> tuple[str, ...]:
    """Parse an ambiguous label spec into its candidate labels.

    Accepts a single label (``"H32"`` → ``("H32",)``), ``"or"``-joined
    ambiguity (``"H32 or H33"`` → ``("H32", "H33")``, case-insensitive),
    and comma/slash/whitespace-joined lists. Every resulting token must
    be a well-formed ``element+ordinal`` label; anything else raises
    :class:`StructureMapError` — tokens are never silently dropped.

    Args:
        text: Raw label expression from experimental input or Goodman text.

    Returns:
        Candidate labels in the order written.

    Raises:
        StructureMapError: Empty spec, dangling ``or``, or malformed token.
    """
    if not isinstance(text, str) or not text.strip():
        raise StructureMapError(f"empty label specification: {text!r}")
    groups = _AMBIGUOUS_SPLIT_RE.split(text.strip())
    tokens: list[str] = []
    for group in groups:
        group_tokens = [t for t in _TOKEN_SPLIT_RE.split(group) if t]
        if not group_tokens:
            raise StructureMapError(f"malformed label specification {text!r} (dangling 'or')")
        tokens.extend(group_tokens)
    if not tokens:
        raise StructureMapError(f"empty label specification: {text!r}")
    for token in tokens:
        if not _LABEL_RE.match(token):
            raise StructureMapError(
                f"malformed label token {token!r} in {text!r} "
                "(expected element + ordinal, e.g. 'H32')"
            )
    return tuple(tokens)


@dataclass(frozen=True)
class AtomIdentity:
    """Stable identity of one atom under one label scheme."""

    atom_uid: str
    source_atom_index: int
    element: str
    label: str
    label_scheme: str


@dataclass(frozen=True)
class NmrStructureMap:
    """Bidirectional map across source index ↔ mol/QC index ↔ rank ↔ labels.

    Built with :meth:`from_mol` (RDKit mol + optional provenance order) or
    :meth:`from_elements` (element list, no RDKit required). All public
    lookups raise :class:`StructureMapError` on bad input — nothing is
    silently dropped or clamped.
    """

    elements: tuple[str, ...]
    """Element symbol per atom, in mol (= QC output) order."""
    source_atom_indices: tuple[int, ...]
    """Source (input structure) index of each mol atom — a 0..n-1 permutation."""
    canonical_ranks: tuple[int, ...]
    """Canonical rank of each mol atom (unique, non-negative)."""
    atom_uids: tuple[str, ...] = field(init=False, repr=True, compare=False)
    """Stable uid per mol atom; derived as ``f"{element}:{rank}"``."""

    _source_to_mol: dict[int, int] = field(init=False, repr=False, compare=False)
    _uid_to_mol: dict[str, int] = field(init=False, repr=False, compare=False)
    _rank_to_mol: dict[int, int] = field(init=False, repr=False, compare=False)
    _element_set: frozenset[str] = field(init=False, repr=False, compare=False)
    _labels: dict[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)
    _label_index: dict[str, dict[str, int]] = field(init=False, repr=False, compare=False)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_mol(
        cls,
        mol: Chem.Mol,
        *,
        source_atom_indices: Sequence[int] | None = None,
    ) -> NmrStructureMap:
        """Build a map from an RDKit mol.

        Canonical ranks are computed on the source-ordered copy of *mol*
        (see module docstring) so uids are stable when downstream stages
        renumber atoms but provenance is supplied.

        Args:
            mol: Candidate/conformer/QC structure (any atom order).
            source_atom_indices: ``source_atom_indices[i]`` = source index
                of mol atom *i*; must be a permutation of ``0..n-1``.
                Defaults to the mol's own order (identity provenance).

        Returns:
            The initialized map.

        Raises:
            StructureMapError: Empty mol, invalid provenance, or ranking failure.
        """
        from rdkit import Chem

        n = mol.GetNumAtoms()
        if n == 0:
            raise StructureMapError("cannot build NmrStructureMap from an empty molecule")
        elements = tuple(atom.GetSymbol() for atom in mol.GetAtoms())
        src = tuple(range(n)) if source_atom_indices is None else tuple(source_atom_indices)
        if len(src) != n or sorted(src) != list(range(n)):
            raise StructureMapError(
                "source_atom_indices must be a permutation of "
                f"0..{n - 1} for {n} atoms, got {list(src)!r}"
            )
        # Renumber into source order: position s holds source atom s.
        order = [0] * n
        for mol_idx, source_idx in enumerate(src):
            order[source_idx] = mol_idx
        ordered = mol if order == list(range(n)) else Chem.RenumberAtoms(mol, order)
        try:
            ranks_ordered = Chem.CanonicalRankAtoms(ordered, breakTies=True, includeChirality=True)
        except (ValueError, RuntimeError) as exc:
            raise StructureMapError(f"canonical ranking failed: {exc}") from exc
        # ranks_ordered[s] = rank of source atom s; scatter to mol order.
        ranks = tuple(int(ranks_ordered[src[i]]) for i in range(n))
        logger.debug(
            "built NmrStructureMap from mol: %d atoms, provenance=%s",
            n,
            source_atom_indices is not None,
        )
        return cls(elements=elements, source_atom_indices=src, canonical_ranks=ranks)

    @classmethod
    def from_elements(
        cls,
        elements: Sequence[str],
        *,
        source_atom_indices: Sequence[int] | None = None,
        ranks: Sequence[int] | None = None,
    ) -> NmrStructureMap:
        """Build a map from a plain element list — no RDKit required.

        Args:
            elements: Element symbols in mol order.
            source_atom_indices: Provenance permutation; identity by default.
            ranks: Unique non-negative canonical rank per atom. When omitted,
                ranks default to the mol positions — deterministic but
                **order-dependent**; pass real ``CanonicalRankAtoms`` ranks
                (or use :meth:`from_mol`) for shuffle-stable uids.

        Returns:
            The initialized map.

        Raises:
            StructureMapError: Empty/invalid elements, bad provenance or ranks.
        """
        n = len(elements)
        if n == 0:
            raise StructureMapError("cannot build NmrStructureMap from an empty element list")
        src = tuple(range(n)) if source_atom_indices is None else tuple(source_atom_indices)
        rank_tuple = tuple(range(n)) if ranks is None else tuple(int(r) for r in ranks)
        return cls(
            elements=tuple(elements),
            source_atom_indices=src,
            canonical_ranks=rank_tuple,
        )

    # ------------------------------------------------------------------
    # Validation + derivation
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        n = len(self.elements)
        if n == 0:
            raise StructureMapError("NmrStructureMap requires at least one atom")
        if len(self.source_atom_indices) != n or len(self.canonical_ranks) != n:
            raise StructureMapError(
                "field length mismatch: "
                f"elements={n}, source_atom_indices={len(self.source_atom_indices)}, "
                f"canonical_ranks={len(self.canonical_ranks)}"
            )
        elements: list[str] = []
        for raw in self.elements:
            symbol = normalize_symbol(raw) if isinstance(raw, str) else ""
            if not symbol or not _ELEMENT_RE.match(symbol):
                raise StructureMapError(f"invalid element symbol {raw!r}")
            elements.append(symbol)
        if any(not isinstance(i, int) for i in self.source_atom_indices):
            raise StructureMapError(
                f"source_atom_indices must be integers, got {self.source_atom_indices!r}"
            )
        if sorted(self.source_atom_indices) != list(range(n)):
            raise StructureMapError(
                "source_atom_indices must be a permutation of "
                f"0..{n - 1}, got {self.source_atom_indices!r}"
            )
        if any(not isinstance(r, int) or r < 0 for r in self.canonical_ranks):
            raise StructureMapError(
                f"canonical_ranks must be non-negative integers, got {self.canonical_ranks!r}"
            )
        if len(set(self.canonical_ranks)) != n:
            raise StructureMapError(
                "canonical_ranks must be unique per atom "
                "(use CanonicalRankAtoms with breakTies=True), "
                f"got {self.canonical_ranks!r}"
            )

        object.__setattr__(self, "elements", tuple(elements))
        atom_uids = tuple(f"{elements[i]}:{self.canonical_ranks[i]}" for i in range(n))
        object.__setattr__(self, "atom_uids", atom_uids)

        source_to_mol = {self.source_atom_indices[i]: i for i in range(n)}
        labels: dict[str, tuple[str, ...]] = {}
        per_element: list[str] = []
        goodman: list[str] = []
        counts: dict[str, int] = {}
        for source_idx in range(n):
            symbol = elements[source_to_mol[source_idx]]
            counts[symbol] = counts.get(symbol, 0) + 1
            per_element.append(f"{symbol}{counts[symbol]}")
            goodman.append(f"{symbol}{source_idx + 1}")
        labels[LABEL_SCHEME_PER_ELEMENT] = tuple(per_element)
        labels[LABEL_SCHEME_GOODMAN] = tuple(goodman)

        object.__setattr__(self, "_source_to_mol", source_to_mol)
        object.__setattr__(self, "_uid_to_mol", {atom_uids[i]: i for i in range(n)})
        object.__setattr__(
            self,
            "_rank_to_mol",
            {self.canonical_ranks[i]: i for i in range(n)},
        )
        object.__setattr__(self, "_element_set", frozenset(elements))
        object.__setattr__(self, "_labels", labels)
        object.__setattr__(
            self,
            "_label_index",
            {
                scheme: {label: idx for idx, label in enumerate(scheme_labels)}
                for scheme, scheme_labels in labels.items()
            },
        )

    def __len__(self) -> int:
        return len(self.elements)

    # ------------------------------------------------------------------
    # Index conversions
    # ------------------------------------------------------------------
    def _check_source(self, source_atom_index: int) -> int:
        if not isinstance(source_atom_index, int) or not 0 <= source_atom_index < len(self):
            raise StructureMapError(
                f"source atom index {source_atom_index!r} out of range 0..{len(self) - 1}"
            )
        return source_atom_index

    def _check_mol(self, mol_index: int) -> int:
        if not isinstance(mol_index, int) or not 0 <= mol_index < len(self):
            raise StructureMapError(
                f"mol/QC atom index {mol_index!r} out of range 0..{len(self) - 1}"
            )
        return mol_index

    def mol_index_for_source(self, source_atom_index: int) -> int:
        """Return the mol/QC index of source atom *source_atom_index*."""
        return self._source_to_mol[self._check_source(source_atom_index)]

    def source_index_for_mol(self, mol_index: int) -> int:
        """Return the source index of the atom at mol/QC position *mol_index*."""
        return self.source_atom_indices[self._check_mol(mol_index)]

    def canonical_rank_for_source(self, source_atom_index: int) -> int:
        """Return the canonical rank of source atom *source_atom_index*."""
        return self.canonical_ranks[self.mol_index_for_source(source_atom_index)]

    def source_index_for_rank(self, rank: int) -> int:
        """Return the source index holding canonical *rank*."""
        mol_index = self._rank_to_mol.get(rank)
        if mol_index is None:
            raise StructureMapError(
                f"canonical rank {rank!r} out of range; "
                f"known ranks for this map: {sorted(self._rank_to_mol)}"
            )
        return self.source_atom_indices[mol_index]

    # ------------------------------------------------------------------
    # Stable uids
    # ------------------------------------------------------------------
    def atom_uid_for_source(self, source_atom_index: int) -> str:
        """Return the stable uid of source atom *source_atom_index*."""
        return self.atom_uids[self.mol_index_for_source(source_atom_index)]

    def atom_uid_for_mol(self, mol_index: int) -> str:
        """Return the stable uid of the atom at mol/QC position *mol_index*."""
        return self.atom_uids[self._check_mol(mol_index)]

    def mol_index_for_atom_uid(self, atom_uid: str) -> int:
        """Return the mol/QC index (shielding array position) of *atom_uid*."""
        mol_index = self._uid_to_mol.get(atom_uid)
        if mol_index is None:
            raise StructureMapError(f"unknown atom_uid {atom_uid!r}")
        return mol_index

    def source_index_for_atom_uid(self, atom_uid: str) -> int:
        """Return the source index of *atom_uid*."""
        return self.source_atom_indices[self.mol_index_for_atom_uid(atom_uid)]

    def element_for_atom_uid(self, atom_uid: str) -> str:
        """Return the element of *atom_uid* (cross-format uid→element join)."""
        return self.elements[self.mol_index_for_atom_uid(atom_uid)]

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def label_for_source(
        self,
        source_atom_index: int,
        label_scheme: str = LABEL_SCHEME_PER_ELEMENT,
    ) -> str:
        """Return the label of source atom *source_atom_index* under *label_scheme*."""
        _check_scheme(label_scheme)
        return self._labels[label_scheme][self._check_source(source_atom_index)]

    def labels_by_source(self, label_scheme: str = LABEL_SCHEME_PER_ELEMENT) -> tuple[str, ...]:
        """Return all labels under *label_scheme*, indexed by source atom index."""
        _check_scheme(label_scheme)
        return self._labels[label_scheme]

    def source_index_for_label(
        self,
        label: str,
        label_scheme: str = LABEL_SCHEME_PER_ELEMENT,
    ) -> int:
        """Resolve a single *label* to its source atom index.

        Raises:
            StructureMapError: Unknown scheme, malformed label, unknown
                element prefix, or a label with no matching atom.
        """
        _check_scheme(label_scheme)
        match = _LABEL_RE.match(label) if isinstance(label, str) else None
        if match is None:
            raise StructureMapError(
                f"malformed label {label!r} (expected element + ordinal, e.g. 'H32')"
            )
        element = match.group(1)
        if element not in self._element_set:
            raise StructureMapError(
                f"unknown element prefix {element!r} in label {label!r} "
                f"(structure contains {sorted(self._element_set)})"
            )
        source_idx = self._label_index[label_scheme].get(label)
        if source_idx is None:
            raise StructureMapError(
                f"missing label {label!r} for scheme {label_scheme!r} "
                f"in structure of {len(self)} atoms"
            )
        return source_idx

    def resolve_labels(
        self,
        spec: str | Sequence[str],
        label_scheme: str = LABEL_SCHEME_PER_ELEMENT,
    ) -> tuple[int, ...]:
        """Resolve a (possibly ambiguous) label spec to source atom indices.

        A ``str`` is parsed with :func:`parse_ambiguous_labels`
        (``"H32 or H33"`` → both candidates); a sequence is taken as-is.
        Any unresolvable label raises — candidates are never silently
        dropped.

        Returns:
            Source atom indices, one per candidate label, in input order.

        Raises:
            StructureMapError: Empty spec, malformed/unresolvable label,
                or unknown scheme.
        """
        _check_scheme(label_scheme)
        if isinstance(spec, str):
            labels = parse_ambiguous_labels(spec)
        else:
            labels = tuple(spec)
        if not labels:
            raise StructureMapError(f"empty label specification: {spec!r}")
        return tuple(self.source_index_for_label(label, label_scheme) for label in labels)

    def identity_for_source(
        self,
        source_atom_index: int,
        label_scheme: str = LABEL_SCHEME_PER_ELEMENT,
    ) -> AtomIdentity:
        """Return the :class:`AtomIdentity` of one source atom under *label_scheme*."""
        _check_scheme(label_scheme)
        source_idx = self._check_source(source_atom_index)
        mol_idx = self._source_to_mol[source_idx]
        return AtomIdentity(
            atom_uid=self.atom_uids[mol_idx],
            source_atom_index=source_idx,
            element=self.elements[mol_idx],
            label=self._labels[label_scheme][source_idx],
            label_scheme=label_scheme,
        )

    def identities(self, label_scheme: str = LABEL_SCHEME_PER_ELEMENT) -> tuple[AtomIdentity, ...]:
        """Return identities for every atom, in source order."""
        _check_scheme(label_scheme)
        return tuple(
            self.identity_for_source(source_idx, label_scheme) for source_idx in range(len(self))
        )


__all__ = [
    "AtomIdentity",
    "LABEL_SCHEME_GOODMAN",
    "LABEL_SCHEME_PER_ELEMENT",
    "LABEL_SCHEMES",
    "NmrStructureMap",
    "StructureMapError",
    "parse_ambiguous_labels",
]
