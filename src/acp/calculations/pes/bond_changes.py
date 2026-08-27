"""Bond-change classification and coordinate-plan suggestion helpers.

Migrated from ``mechanism/bond_changes.py``.
Algorithms unchanged — mechanism semantics stripped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.utils.geometry_tools import GeometryUtils

from .atom_mapping import (
    AtomMapCandidate,
    _build_mol_graph,
    _normalize_coordinates,
    _normalize_symbols,
)

_BOND_ORDER_TOLERANCE = 0.25
_MAX_DRIVE_COORDINATES = 4
_MAX_MONITOR_COORDINATES = 4
_MANUAL_CHANGE_TYPES = ("break", "form")
_PRODUCT_STRETCH_MARGIN = 2.0


@dataclass(frozen=True)
class BondChange:
    """One mapped bond change, defined in reactant or product space.

    ``reactant_atoms``/``product_atoms`` are 0-based indices in the respective
    structure; at least one side must be present. Auto-mapped changes always
    carry the reactant side; manual entries may define a bond in product space
    only (``reactant_atoms is None``), which marks the change as
    product-defining. ``distance_before`` is measured in the defining side's
    coordinate space; ``distance_after`` is the opposite side's distance when
    its atom pair is known, else ``None``.
    """

    reactant_atoms: tuple[int, int] | None
    product_atoms: tuple[int, int] | None
    change_type: Literal["break", "form", "order_up", "order_down"]
    bond_order_before: float
    bond_order_after: float
    distance_before: float
    distance_after: float | None
    confidence: float
    adjacent_bonds: tuple[tuple[int, int], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.reactant_atoms is None and self.product_atoms is None:
            raise ValueError("BondChange requires at least one of reactant_atoms/product_atoms")


def compute_bond_changes(
    reactant_symbols: Sequence[str],
    reactant_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    product_symbols: Sequence[str],
    product_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    mapping: AtomMapCandidate | Sequence[tuple[int, int]],
    *,
    reactant_smiles: str | None = None,
    product_smiles: str | None = None,
    charge: int = 0,
) -> list[BondChange]:
    """Classify mapped bond changes between reactant and product."""

    normalized_reactant_symbols = _normalize_symbols(reactant_symbols)
    normalized_product_symbols = _normalize_symbols(product_symbols)
    reactant_array = _normalize_coordinates(reactant_coords)
    product_array = _normalize_coordinates(product_coords)
    candidate = _coerce_mapping_candidate(mapping)
    reactant_graph = _build_mol_graph(
        normalized_reactant_symbols,
        reactant_array,
        charge=charge,
        smiles=reactant_smiles,
    )
    product_graph = _build_mol_graph(
        normalized_product_symbols,
        product_array,
        charge=charge,
        smiles=product_smiles,
    )
    return _bond_changes_from_graphs(
        reactant_graph,
        product_graph,
        reactant_array,
        product_array,
        candidate,
    )


def _bond_changes_from_graphs(
    reactant_graph: Any,
    product_graph: Any,
    reactant_array: NDArray[np.float64],
    product_array: NDArray[np.float64],
    candidate: AtomMapCandidate,
) -> list[BondChange]:
    """Classify bond changes from prebuilt mol graphs (layered semantics).

    Classification layers (evidence-first):

    * Pairs bonded on BOTH sides are order/aromaticity changes only — they are
      never classified as break/form.
    * ``break`` requires genuinely bonded in the reactant and unbonded in the
      mapped product; ``form`` requires the inverse.
    """

    reactant_bonds = _bond_orders(reactant_graph)
    product_bonds = _bond_orders(product_graph)

    reactant_to_product = {
        reactant_index: product_index for reactant_index, product_index in candidate.mapping
    }
    product_to_reactant = {
        product_index: reactant_index for reactant_index, product_index in candidate.mapping
    }
    mapped_product_bonds: dict[tuple[int, int], float] = {}
    mapped_product_atoms: dict[tuple[int, int], tuple[int, int]] = {}
    for product_pair, order in product_bonds.items():
        left, right = product_pair
        if left not in product_to_reactant or right not in product_to_reactant:
            continue
        reactant_pair = _pair(product_to_reactant[left], product_to_reactant[right])
        mapped_product_bonds[reactant_pair] = float(order)
        mapped_product_atoms[reactant_pair] = _pair(left, right)

    changed_pairs = sorted(set(reactant_bonds) | set(mapped_product_bonds))
    reliable_orders = reactant_graph.bond_orders_available and product_graph.bond_orders_available
    raw_changes: list[BondChange] = []
    for reactant_pair in changed_pairs:
        bond_order_before = float(reactant_bonds.get(reactant_pair, 0.0))
        bond_order_after = float(mapped_product_bonds.get(reactant_pair, 0.0))
        if abs(bond_order_before - bond_order_after) <= _BOND_ORDER_TOLERANCE:
            continue

        bonded_before = bond_order_before > _BOND_ORDER_TOLERANCE
        bonded_after = bond_order_after > _BOND_ORDER_TOLERANCE
        if bonded_before and bonded_after:
            # Pure order/aromaticity change between genuinely bonded atoms.
            if not reliable_orders:
                continue
            change_type: Literal["break", "form", "order_up", "order_down"] = (
                "order_up" if bond_order_after > bond_order_before else "order_down"
            )
        elif bonded_before:
            change_type = "break"
        elif bonded_after:
            change_type = "form"
        else:
            continue

        product_pair = mapped_product_atoms.get(reactant_pair)
        if product_pair is None:
            mapped_left = reactant_to_product.get(reactant_pair[0])
            mapped_right = reactant_to_product.get(reactant_pair[1])
            if mapped_left is not None and mapped_right is not None:
                product_pair = _pair(mapped_left, mapped_right)
        distance_before = GeometryUtils.calculate_distance(
            reactant_array, reactant_pair[0], reactant_pair[1]
        )
        distance_after = (
            GeometryUtils.calculate_distance(product_array, product_pair[0], product_pair[1])
            if product_pair is not None
            else 0.0
        )
        confidence = _bond_change_confidence(
            candidate.confidence,
            reliable_orders=reliable_orders,
            change_type=change_type,
        )
        raw_changes.append(
            BondChange(
                reactant_atoms=reactant_pair,
                product_atoms=product_pair,
                change_type=change_type,
                bond_order_before=bond_order_before,
                bond_order_after=bond_order_after,
                distance_before=distance_before,
                distance_after=distance_after,
                confidence=confidence,
            )
        )

    return _enrich_with_adjacent_bonds(raw_changes, reactant_bonds, mapped_product_bonds)


def suggest_coordinate_plan(
    bond_changes: Sequence[BondChange],
    *,
    points: int = 21,
    strategy: str = "guided-scan",
) -> ReactionCoordinatePlan:
    """Auto-generate a reaction-coordinate plan from mapped bond changes.

    Drive coordinates follow the defining side of each change: reactant-space
    changes drive on ``reactant_atoms``; product-defining manual changes
    (``reactant_atoms is None``) drive on ``product_atoms`` instead. The plan
    anchors at ``start_from="product"`` when any drive coordinate is
    product-space (stretch from the product end), else ``"reactant"``.
    Product-defining entries carry no reactant-space ``adjacent_bonds``, so
    they contribute no monitor coordinates (acceptable).
    """

    ranked_changes = sorted(
        bond_changes,
        key=lambda item: (
            _drive_priority(item.change_type),
            -item.confidence,
            _defining_pair(item),
        ),
    )
    drive_changes = ranked_changes[:_MAX_DRIVE_COORDINATES]
    drive_specs: list[CoordinateSpec] = []
    for index, change in enumerate(drive_changes, start=1):
        start, end = _distance_targets(change)
        drive_specs.append(
            CoordinateSpec(
                id=f"rc{index}",
                kind="distance",
                atoms=_defining_pair(change),
                role="drive",
                start=start,
                end=end,
            )
        )

    if not drive_specs:
        raise ValueError("Cannot suggest a coordinate plan without at least one bond change")

    changed_bonds = {
        change.reactant_atoms for change in ranked_changes if change.reactant_atoms is not None
    }
    monitor_pairs: list[tuple[int, int]] = []
    for change in ranked_changes:
        for pair in change.adjacent_bonds:
            if pair in changed_bonds or pair in monitor_pairs:
                continue
            monitor_pairs.append(pair)
            if len(monitor_pairs) >= _MAX_MONITOR_COORDINATES:
                break
        if len(monitor_pairs) >= _MAX_MONITOR_COORDINATES:
            break

    monitor_specs = [
        CoordinateSpec(
            id=f"rc{len(drive_specs) + offset}",
            kind="distance",
            atoms=pair,
            role="monitor",
        )
        for offset, pair in enumerate(monitor_pairs, start=1)
    ]
    start_from: Literal["reactant", "product", "custom"] = (
        "product" if any(change.reactant_atoms is None for change in drive_changes) else "reactant"
    )
    return ReactionCoordinatePlan(
        coordinates=tuple((*drive_specs, *monitor_specs)),
        points=int(points),
        start_from=start_from,
    )


def bond_changes_to_dicts(bond_changes: Sequence[BondChange]) -> list[dict[str, Any]]:
    """Serialize bond changes into JSON-trivial dictionaries."""

    return [
        {
            "reactant_atoms": (
                list(change.reactant_atoms) if change.reactant_atoms is not None else None
            ),
            "product_atoms": (
                list(change.product_atoms) if change.product_atoms is not None else None
            ),
            "change_type": change.change_type,
            "bond_order_before": change.bond_order_before,
            "bond_order_after": change.bond_order_after,
            "distance_before": change.distance_before,
            "distance_after": change.distance_after,
            "confidence": change.confidence,
            "adjacent_bonds": [list(pair) for pair in change.adjacent_bonds],
        }
        for change in bond_changes
    ]


def bond_changes_from_dicts(payload: Sequence[dict[str, Any]]) -> list[BondChange]:
    """Deserialize bond changes from JSON-trivial dictionaries.

    ``reactant_atoms``/``product_atoms`` may be null (one-sided manual
    records), but at least one of the two must be present.
    """

    parsed: list[BondChange] = []
    for item in payload:
        reactant_atoms = _parsed_atom_pair(item.get("reactant_atoms"))
        product_atoms = _parsed_atom_pair(item.get("product_atoms"))
        if reactant_atoms is None and product_atoms is None:
            raise ValueError("BondChange requires at least one of reactant_atoms/product_atoms")
        raw_distance_after = item.get("distance_after")
        parsed.append(
            BondChange(
                reactant_atoms=reactant_atoms,
                product_atoms=product_atoms,
                change_type=cast(
                    Literal["break", "form", "order_up", "order_down"],
                    str(item.get("change_type") or "break"),
                ),
                bond_order_before=float(item.get("bond_order_before") or 0.0),
                bond_order_after=float(item.get("bond_order_after") or 0.0),
                distance_before=float(item.get("distance_before") or 0.0),
                distance_after=(
                    float(raw_distance_after) if raw_distance_after is not None else None
                ),
                confidence=float(item.get("confidence") or 0.0),
                adjacent_bonds=tuple(
                    _pair(int(pair[0]), int(pair[1]))
                    for pair in item.get("adjacent_bonds") or []
                    if isinstance(pair, (list, tuple)) and len(pair) == 2
                ),
            )
        )
    return parsed


def _parsed_atom_pair(raw_atoms: Any) -> tuple[int, int] | None:
    if raw_atoms is None:
        return None
    if not isinstance(raw_atoms, (list, tuple)) or len(raw_atoms) != 2:
        raise ValueError("atom pairs must contain exactly two indices")
    return _pair(int(raw_atoms[0]), int(raw_atoms[1]))


def manual_bond_changes_to_records(
    entries: Sequence[dict[str, Any]],
    n_reactant_atoms: int,
    reactant_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    product_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    mapping: AtomMapCandidate | Sequence[tuple[int, int]],
    *,
    n_product_atoms: int,
) -> list[BondChange]:
    """Build authoritative BondChange records from user manual entries.

    Each entry defines a bond in reactant space
    (``{"reactant_atoms": [i, j], "change_type": "break"|"form"}``), in
    product space (``{"product_atoms": [i, j], ...}`` — for forming bonds the
    product conformation is closer to the TS), or both. At least one side is
    required; indices are validated against the respective structure's atom
    count.

    ``distance_before`` is measured in the defining side's coordinate space;
    the opposite side's distance (``distance_after``) is computed only when
    that side's atom pair is known — reactant-side entries resolve
    ``product_atoms``/``distance_after`` through the mapping when both atoms
    map, while product-defining entries keep ``reactant_atoms`` ``None`` so
    downstream plan generation anchors at ``start_from="product"``.
    """

    reactant_array = _normalize_coordinates(reactant_coords)
    product_array = _normalize_coordinates(product_coords)
    mapping_pairs = mapping.mapping if isinstance(mapping, AtomMapCandidate) else list(mapping)
    reactant_to_product = {
        int(reactant_index): int(product_index) for reactant_index, product_index in mapping_pairs
    }

    records: list[BondChange] = []
    for position, entry in enumerate(entries):
        reactant_pair, product_pair, change_type = _validated_manual_entry(
            entry, position, n_reactant_atoms, n_product_atoms
        )
        if reactant_pair is not None and product_pair is None:
            mapped_left = reactant_to_product.get(reactant_pair[0])
            mapped_right = reactant_to_product.get(reactant_pair[1])
            product_pair = (
                _pair(mapped_left, mapped_right)
                if mapped_left is not None and mapped_right is not None
                else None
            )
        defining_reactant = reactant_pair is not None
        defining_array = reactant_array if defining_reactant else product_array
        defining_pair = reactant_pair if defining_reactant else product_pair
        assert defining_pair is not None
        distance_before = GeometryUtils.calculate_distance(
            defining_array, defining_pair[0], defining_pair[1]
        )
        distance_after = (
            GeometryUtils.calculate_distance(product_array, product_pair[0], product_pair[1])
            if defining_reactant and product_pair is not None
            else None
        )
        breaking = change_type == "break"
        records.append(
            BondChange(
                reactant_atoms=reactant_pair,
                product_atoms=product_pair,
                change_type=change_type,
                bond_order_before=1.0 if breaking else 0.0,
                bond_order_after=0.0 if breaking else 1.0,
                distance_before=distance_before,
                distance_after=distance_after,
                confidence=1.0,
            )
        )
    return records


def _validated_manual_entry(
    entry: dict[str, Any],
    position: int,
    n_reactant_atoms: int,
    n_product_atoms: int,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None, Literal["break", "form"]]:
    if not isinstance(entry, dict):
        raise ValueError(f"manual_bond_changes[{position}] must be an object")
    reactant_pair = _validated_manual_atom_pair(
        entry.get("reactant_atoms"),
        "reactant_atoms",
        position,
        n_reactant_atoms,
    )
    product_pair = _validated_manual_atom_pair(
        entry.get("product_atoms"),
        "product_atoms",
        position,
        n_product_atoms,
    )
    if reactant_pair is None and product_pair is None:
        raise ValueError(
            f"manual_bond_changes[{position}] must define at least one of "
            "reactant_atoms or product_atoms"
        )
    change_type = str(entry.get("change_type") or "")
    if change_type not in _MANUAL_CHANGE_TYPES:
        raise ValueError(
            f"manual_bond_changes[{position}].change_type must be one of "
            f"{list(_MANUAL_CHANGE_TYPES)}, got {change_type!r}"
        )
    return reactant_pair, product_pair, cast(Literal["break", "form"], change_type)


def _validated_manual_atom_pair(
    raw_atoms: Any,
    key: str,
    position: int,
    n_atoms: int,
) -> tuple[int, int] | None:
    if raw_atoms is None:
        return None
    if not isinstance(raw_atoms, (list, tuple)) or len(raw_atoms) != 2:
        raise ValueError(
            f"manual_bond_changes[{position}].{key} must contain exactly two atom indices"
        )
    try:
        atoms = (int(raw_atoms[0]), int(raw_atoms[1]))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"manual_bond_changes[{position}].{key} must contain integer indices"
        ) from exc
    if atoms[0] == atoms[1]:
        raise ValueError(f"manual_bond_changes[{position}].{key} must not be a self-pair")
    if not (0 <= atoms[0] < n_atoms and 0 <= atoms[1] < n_atoms):
        raise ValueError(
            f"manual_bond_changes[{position}].{key} {list(atoms)} out of range for "
            f"{n_atoms} atoms (0-based)"
        )
    return _pair(atoms[0], atoms[1])


def _enrich_with_adjacent_bonds(
    raw_changes: list[BondChange],
    reactant_bonds: dict[tuple[int, int], float],
    mapped_product_bonds: dict[tuple[int, int], float],
) -> list[BondChange]:
    if not raw_changes:
        return []

    adjacent_lookup = _adjacent_bond_lookup(
        changed_bonds={
            change.reactant_atoms for change in raw_changes if change.reactant_atoms is not None
        },
        union_bonds=set(reactant_bonds) | set(mapped_product_bonds),
    )
    enriched: list[BondChange] = []
    for change in raw_changes:
        enriched.append(
            BondChange(
                reactant_atoms=change.reactant_atoms,
                product_atoms=change.product_atoms,
                change_type=change.change_type,
                bond_order_before=change.bond_order_before,
                bond_order_after=change.bond_order_after,
                distance_before=change.distance_before,
                distance_after=change.distance_after,
                confidence=change.confidence,
                adjacent_bonds=(
                    adjacent_lookup.get(change.reactant_atoms, ())
                    if change.reactant_atoms is not None
                    else ()
                ),
            )
        )
    return sorted(enriched, key=lambda item: (-item.confidence, _defining_pair(item)))


def _coerce_mapping_candidate(
    mapping: AtomMapCandidate | Sequence[tuple[int, int]],
) -> AtomMapCandidate:
    if isinstance(mapping, AtomMapCandidate):
        return mapping
    return AtomMapCandidate(
        mapping=[
            (int(reactant_index), int(product_index)) for reactant_index, product_index in mapping
        ],
        confidence=1.0,
        method="external_mapping_v1",
    )


def _bond_orders(graph: Any) -> dict[tuple[int, int], float]:
    bonds: dict[tuple[int, int], float] = {}
    for bond in graph.mol.GetBonds():
        pair = _pair(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
        order = float(bond.GetBondTypeAsDouble())
        bonds[pair] = order if graph.bond_orders_available else 1.0
    return bonds


def _pair(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left <= right else (right, left)


def _bond_change_confidence(
    mapping_confidence: float,
    *,
    reliable_orders: bool,
    change_type: str,
) -> float:
    confidence = float(mapping_confidence)
    if not reliable_orders:
        confidence *= 0.75
    if change_type in {"form", "break"}:
        confidence *= 1.0
    else:
        confidence *= 0.95
    return max(0.0, min(confidence, 1.0))


def _adjacent_bond_lookup(
    *,
    changed_bonds: set[tuple[int, int]],
    union_bonds: set[tuple[int, int]],
) -> dict[tuple[int, int], tuple[tuple[int, int], ...]]:
    lookup: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {}
    for bond in changed_bonds:
        adjacent = sorted(
            pair
            for pair in union_bonds
            if pair not in changed_bonds and (bond[0] in pair or bond[1] in pair)
        )
        lookup[bond] = tuple(adjacent)
    return lookup


def _defining_pair(change: BondChange) -> tuple[int, int]:
    return change.reactant_atoms if change.reactant_atoms is not None else change.product_atoms


def _drive_priority(change_type: str) -> int:
    return {
        "form": 0,
        "break": 1,
        "order_up": 2,
        "order_down": 3,
    }.get(change_type, 9)


def _distance_targets(change: BondChange) -> tuple[float, float]:
    """Drive-window targets in the defining side's coordinate space.

    Reactant-defining changes reuse the reactant-space logic (start =
    ``distance_before``, end from ``distance_after`` when the opposite side is
    known). Product-defining entries stretch away from the product geometry:
    start = the product-space measured distance, end = start + margin.
    """

    if change.reactant_atoms is None:
        start = float(change.distance_before)
        return start, start + _PRODUCT_STRETCH_MARGIN
    start = float(change.distance_before)
    end = change.distance_after
    if change.change_type == "break":
        end_value = float(end) if end is not None else start * 1.6
        return start, max(end_value, start * 1.6)
    if change.change_type == "form":
        end_value = max(1.0, float(end)) if end is not None else 1.0
        if end_value >= start:
            end_value = max(1.0, start - 0.1)
        return start, end_value
    if change.change_type == "order_up":
        end_value = float(end) if end is not None else start - 0.05
        return start, min(end_value, start - 0.05)
    end_value = float(end) if end is not None else start * 1.15
    return start, max(end_value, start * 1.15)


__all__ = [
    "BondChange",
    "bond_changes_from_dicts",
    "bond_changes_to_dicts",
    "compute_bond_changes",
    "manual_bond_changes_to_records",
    "suggest_coordinate_plan",
]
