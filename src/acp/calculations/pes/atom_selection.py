"""Generic atom-selection parsing for PES coordinate scans.

The frontend may only provide a sequence of selected atom indices.  This
module turns that sequence into an explicit scan function and validates it
against the connectivity perceived from the input geometry.  Keeping this
logic in the calculation layer makes the GUI, API, CLI, and scheduler share
the same semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from acp.calculations.irc.validation import perceive_connectivity

SelectionKind = Literal["bond_stretch", "angle", "dihedral", "double_bond_scan"]

_ALIASES: dict[str, SelectionKind] = {
    "bond_stretch": "bond_stretch",
    "bond_length": "bond_stretch",
    "distance": "bond_stretch",
    "stretch": "bond_stretch",
    "angle": "angle",
    "bond_angle": "angle",
    "dihedral": "dihedral",
    "torsion": "dihedral",
    "double_bond_scan": "double_bond_scan",
    "double_bond": "double_bond_scan",
    "double": "double_bond_scan",
}


@dataclass(frozen=True)
class FunctionalAtomSelection:
    """A validated selection and its derived topological relationships.

    ``atoms`` preserves the user's order because order is meaningful for
    angles and dihedrals.  ``groups`` and ``bond_pairs`` are explicit so
    downstream writers do not need to infer the meaning a second time.
    """

    kind: SelectionKind
    atoms: tuple[int, ...]
    groups: tuple[tuple[int, int], ...] = ()
    bond_pairs: tuple[tuple[int, int], ...] = ()
    adjacency: tuple[tuple[int, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "kind": self.kind,
            "atoms": list(self.atoms),
            "groups": [list(group) for group in self.groups],
            "bond_pairs": [list(pair) for pair in self.bond_pairs],
            "adjacency": [list(pair) for pair in self.adjacency],
        }


def normalize_selection_kind(value: Any) -> SelectionKind:
    """Normalize GUI/API aliases to the four supported scan functions."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    try:
        return _ALIASES[normalized]
    except KeyError as exc:
        choices = "bond_stretch, angle, dihedral, double_bond_scan"
        raise ValueError(f"selection.kind must be one of {choices} (got {value!r})") from exc


def parse_functional_atom_selection(
    kind: Any,
    atoms: Sequence[int],
    symbols: Sequence[str],
    coordinates: NDArray[np.float64] | Sequence[Sequence[float]],
) -> FunctionalAtomSelection:
    """Parse and validate one of the four supported atom-selection forms.

    Connectivity is inferred from the input geometry, not trusted from the
    browser payload.  Thus a request cannot silently turn a non-bonded pair
    into a bond or a connected four-atom chain into a double-bond scan.
    """
    if isinstance(atoms, (str, bytes)):
        raise ValueError("selected atoms must be a sequence of integer indices")
    selection_kind = normalize_selection_kind(kind)
    selected = tuple(_coerce_atom_index(atom) for atom in atoms)
    expected = {
        "bond_stretch": 2,
        "angle": 3,
        "dihedral": 4,
        "double_bond_scan": 4,
    }[selection_kind]
    if len(selected) != expected:
        raise ValueError(
            f"{selection_kind} requires exactly {expected} selected atoms (got {len(selected)})"
        )
    if len(set(selected)) != len(selected):
        raise ValueError("selected atoms must be unique")

    normalized_symbols = [str(symbol) for symbol in symbols]
    if len(normalized_symbols) != len(coordinates):
        raise ValueError("Atom count mismatch between symbols and coordinates")
    for atom in selected:
        if atom < 0 or atom >= len(normalized_symbols):
            raise ValueError(
                f"selected atom index {atom} is out of range "
                f"(structure has {len(normalized_symbols)} atoms)"
            )

    edges = perceive_connectivity(normalized_symbols, coordinates)
    ordered_pairs = tuple(_ordered_pair(selected[i], selected[i + 1]) for i in range(expected - 1))

    if selection_kind == "bond_stretch":
        _require_bond(ordered_pairs[0], edges, "two selected atoms must be adjacent")
        groups = (ordered_pairs[0],)
    elif selection_kind == "angle":
        _require_chain(ordered_pairs, edges, "three selected atoms must form A-B-C")
        groups = ()
    elif selection_kind == "dihedral":
        _require_chain(ordered_pairs, edges, "four selected atoms must form A-B-C-D")
        groups = ()
    else:
        first = _ordered_pair(selected[0], selected[1])
        second = _ordered_pair(selected[2], selected[3])
        _require_bond(first, edges, "the first double-scan group must be an adjacent pair")
        _require_bond(second, edges, "the second double-scan group must be an adjacent pair")
        first_atoms = set(first)
        second_atoms = set(second)
        if first_atoms & second_atoms:
            raise ValueError("double-bond scan groups must not share atoms")
        if any(
            _ordered_pair(left, right) in edges for left in first_atoms for right in second_atoms
        ):
            raise ValueError("double-bond scan groups must be non-adjacent to each other")
        groups = (first, second)

    return FunctionalAtomSelection(
        kind=selection_kind,
        atoms=selected,
        groups=groups,
        bond_pairs=ordered_pairs if selection_kind != "double_bond_scan" else groups,
        adjacency=tuple(sorted(edges.intersection(set(ordered_pairs)))),
    )


def _coerce_atom_index(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"atom index must be an integer (got {value!r})")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"atom index must be an integer (got {value!r})") from exc
    if str(value).strip() != str(index) and not isinstance(value, int):
        raise ValueError(f"atom index must be an integer (got {value!r})")
    return index


def _ordered_pair(left: int, right: int) -> tuple[int, int]:
    return (min(left, right), max(left, right))


def _require_bond(pair: tuple[int, int], edges: set[tuple[int, int]], message: str) -> None:
    if pair not in edges:
        raise ValueError(message + f" (pair {pair[0]}, {pair[1]} is not a bond)")


def _require_chain(
    pairs: Sequence[tuple[int, int]],
    edges: set[tuple[int, int]],
    message: str,
) -> None:
    missing = [pair for pair in pairs if pair not in edges]
    if missing:
        raise ValueError(message + f" (missing bond {missing[0][0]}-{missing[0][1]})")


__all__ = [
    "FunctionalAtomSelection",
    "SelectionKind",
    "normalize_selection_kind",
    "parse_functional_atom_selection",
]
