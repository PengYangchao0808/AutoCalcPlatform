"""Geometry and topology validation for PES scan trajectories.

Migrated from ``mechanism/primitives/geometry_guard.py`` and the scan
coordinate/result dataclasses of ``mechanism/primitives/scan_rescue.py``.

Algorithms unchanged — mechanism semantics stripped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cccp.utils.file_io import read_xyz
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

# ── covalent radii ──────────────────────────────────────────────────────

_COVALENT_RADII: dict[str, float] = {
    "H": 0.31,
    "B": 0.85,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "Si": 1.11,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Br": 1.20,
    "I": 1.39,
    "Li": 1.28,
    "Mg": 1.41,
    "Al": 1.21,
    "Zn": 1.22,
}


# ── scan coordinate / spec / result ─────────────────────────────────────


@dataclass(frozen=True)
class SurfaceScanCoordinate:
    """One scan coordinate for a relaxed-scan run."""

    kind: str
    atoms: tuple[int, int]
    start: float
    end: float
    steps: int


@dataclass(frozen=True)
class SurfaceScanSpec:
    """Full relaxed-scan specification."""

    method: str
    solvent: str | None = None
    solvent_model: str | None = None
    nproc: int | None = None
    maxcore: int | None = None
    charge: int = 0
    multiplicity: int = 1
    coordinates: tuple[SurfaceScanCoordinate, ...] = ()
    simultaneous: bool = False
    scan_ts: bool = False
    full_scan: bool = True


@dataclass(frozen=True)
class SurfaceScanResult:
    """Result of a relaxed-scan execution."""

    status: str
    output_file: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ── bond pair helpers ───────────────────────────────────────────────────


def _canonicalize_bond_pair(atom_i: int, atom_j: int) -> tuple[int, int]:
    """Return a sorted ``(min, max)`` bond pair."""
    i = int(atom_i)
    j = int(atom_j)
    if i == j:
        raise ValueError(f"A bond pair must contain two different atoms: {(i, j)!r}")
    return (i, j) if i < j else (j, i)


def _canonicalize_bond_pairs(
    pairs: list[tuple[int, int]] | tuple[tuple[int, int], ...] | Any,
) -> tuple[tuple[int, int], ...]:
    """Canonicalize and deduplicate a collection of bond pairs."""
    normalized: set[tuple[int, int]] = set()
    for raw_pair in pairs:
        pair = tuple(raw_pair)
        if len(pair) != 2:
            raise ValueError(f"A bond pair must contain exactly two indices: {pair!r}")
        normalized.add(_canonicalize_bond_pair(pair[0], pair[1]))
    return tuple(sorted(normalized))


# ── bond graph construction ─────────────────────────────────────────────


def _build_bond_graph(
    coords: np.ndarray,
    symbols: list[str],
    *,
    scale: float = 1.25,
    min_dist: float = 0.6,
) -> dict[int, list[int]]:
    """Build a covalent-radius bond graph from coordinates."""
    n_atoms = len(coords)
    graph: dict[int, list[int]] = {index: [] for index in range(n_atoms)}
    for atom_i in range(n_atoms):
        for atom_j in range(atom_i + 1, n_atoms):
            distance = GeometryUtils.calculate_distance(coords, atom_i, atom_j)
            if distance < min_dist:
                continue
            radius_i = _COVALENT_RADII.get(symbols[atom_i])
            radius_j = _COVALENT_RADII.get(symbols[atom_j])
            if radius_i is None or radius_j is None:
                raise ValueError(
                    f"Unknown element radius: {symbols[atom_i]}, {symbols[atom_j]}"
                )
            threshold = scale * (radius_i + radius_j)
            if distance <= threshold:
                graph[atom_i].append(atom_j)
                graph[atom_j].append(atom_i)
    return graph


def _calculate_rmsd(left: np.ndarray, right: np.ndarray) -> float:
    return float(GeometryUtils.rmsd(left, right))


# ── topology guard ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TopologyGuardResult:
    """Result of topology validation between two geometries."""

    is_valid: bool
    new_edges: list[tuple[int, int, float]]
    lost_edges: list[tuple[int, int]]
    forming_bonds: set[tuple[int, int]]
    graph_scale: float = 1.25


@dataclass(frozen=True)
class RiskyContactResult:
    """Result of risky contact detection."""

    risky_pairs: list[tuple[int, int, float, float]]
    threshold_used: float


def compare_graph_topology(
    product_coords: np.ndarray,
    candidate_coords: np.ndarray,
    symbols: list[str],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    graph_scale: float = 1.25,
    min_dist: float = 0.6,
    topology_grace_edges: int = 0,
) -> TopologyGuardResult:
    """Compare product and candidate bond graphs for topology drift.

    Returns a :class:`TopologyGuardResult` with ``is_valid=True`` when the
    candidate has no lost non-forming edges and at most
    ``topology_grace_edges`` new non-forming edges.
    """
    try:
        product_graph = _build_bond_graph(
            product_coords, symbols, scale=graph_scale, min_dist=min_dist,
        )
        candidate_graph = _build_bond_graph(
            candidate_coords, symbols, scale=graph_scale, min_dist=min_dist,
        )
    except ValueError as exc:
        logger.warning("Failed to build bond graph: %s", exc)
        return TopologyGuardResult(
            is_valid=False,
            new_edges=[],
            lost_edges=[],
            forming_bonds=set(_canonicalize_bond_pairs(forming_bonds)),
            graph_scale=graph_scale,
        )

    product_edges: set[tuple[int, int]] = set()
    for atom_i, neighbors in product_graph.items():
        for atom_j in neighbors:
            if atom_i < atom_j:
                product_edges.add((atom_i, atom_j))

    candidate_edges: set[tuple[int, int]] = set()
    for atom_i, neighbors in candidate_graph.items():
        for atom_j in neighbors:
            if atom_i < atom_j:
                candidate_edges.add((atom_i, atom_j))

    forming_set = set(_canonicalize_bond_pairs(forming_bonds))
    forming_atoms: set[int] = set()
    for atom_i, atom_j in forming_set:
        forming_atoms.add(atom_i)
        forming_atoms.add(atom_j)

    new_edges_raw = candidate_edges - product_edges
    new_edges: list[tuple[int, int, float]] = []
    for atom_i, atom_j in sorted(new_edges_raw):
        if atom_i in forming_atoms or atom_j in forming_atoms:
            continue
        distance = GeometryUtils.calculate_distance(candidate_coords, atom_i, atom_j)
        new_edges.append((atom_i, atom_j, distance))

    lost_edges_raw = product_edges - candidate_edges - forming_set
    lost_edges = sorted(lost_edges_raw)

    is_valid = len(lost_edges) == 0 and len(new_edges) <= topology_grace_edges
    if not is_valid:
        if new_edges:
            logger.debug(
                "Topology drift: %d new edge(s) detected: %s",
                len(new_edges),
                [(i, j, f"{distance:.3f}Å") for i, j, distance in new_edges[:3]],
            )
        if lost_edges:
            logger.debug(
                "Topology drift: %d non-forming edge(s) lost: %s",
                len(lost_edges),
                lost_edges[:3],
            )

    return TopologyGuardResult(
        is_valid=is_valid,
        new_edges=new_edges,
        lost_edges=lost_edges,
        forming_bonds=forming_set,
        graph_scale=graph_scale,
    )


# ── risky contact detection ─────────────────────────────────────────────


def detect_risky_contacts(
    product_coords: np.ndarray,
    candidate_coords: np.ndarray,
    symbols: list[str],
    product_graph_scale: float = 1.25,
    near_bond_threshold_ratio: float = 0.85,
    near_bond_abs_max: float = 2.2,
    min_shrink_ratio: float = 0.75,
    max_pairs: int = 6,
) -> RiskyContactResult:
    """Detect non-product atom pairs that move into near-bond contact."""
    try:
        product_graph = _build_bond_graph(
            product_coords, symbols, scale=product_graph_scale, min_dist=0.6,
        )
    except ValueError as exc:
        logger.warning("Failed to build product graph for risk detection: %s", exc)
        return RiskyContactResult(risky_pairs=[], threshold_used=near_bond_abs_max)

    product_edges: set[tuple[int, int]] = set()
    for atom_i, neighbors in product_graph.items():
        for atom_j in neighbors:
            if atom_i < atom_j:
                product_edges.add((atom_i, atom_j))

    n_atoms = len(symbols)
    risky_pairs: list[tuple[int, int, float, float]] = []
    for atom_i in range(n_atoms):
        for atom_j in range(atom_i + 1, n_atoms):
            pair = (atom_i, atom_j)
            if pair in product_edges:
                continue
            product_distance = GeometryUtils.calculate_distance(
                product_coords, atom_i, atom_j,
            )
            candidate_distance = GeometryUtils.calculate_distance(
                candidate_coords, atom_i, atom_j,
            )
            if candidate_distance >= product_distance * min_shrink_ratio:
                continue
            radius_i = _COVALENT_RADII.get(symbols[atom_i], 0.76)
            radius_j = _COVALENT_RADII.get(symbols[atom_j], 0.76)
            near_bond_threshold = min(
                near_bond_abs_max,
                near_bond_threshold_ratio * (radius_i + radius_j),
            )
            if candidate_distance <= near_bond_threshold:
                risky_pairs.append(
                    (atom_i, atom_j, candidate_distance, product_distance)
                )

    risky_pairs.sort(key=lambda item: item[2])
    if len(risky_pairs) > max_pairs:
        logger.debug("Truncated risky pairs from %d to %d", len(risky_pairs), max_pairs)
        risky_pairs = risky_pairs[:max_pairs]
    return RiskyContactResult(
        risky_pairs=risky_pairs,
        threshold_used=near_bond_abs_max,
    )


# ── minimum non-bonded distance ─────────────────────────────────────────


def compute_min_nonbonded_distance(
    coords: np.ndarray,
    bonded_pairs: set[tuple[int, int]],
    forming_bonds: set[tuple[int, int]],
    min_considered: float = 1.25,
) -> tuple[float, tuple[int, int] | None]:
    """Compute the minimum non-bonded distance in a geometry."""
    n_atoms = len(coords)
    excluded = bonded_pairs | forming_bonds
    minimum_distance = float("inf")
    minimum_pair: tuple[int, int] | None = None
    for atom_i in range(n_atoms):
        for atom_j in range(atom_i + 1, n_atoms):
            pair = (atom_i, atom_j)
            if pair in excluded:
                continue
            distance = GeometryUtils.calculate_distance(coords, atom_i, atom_j)
            if distance < min_considered:
                continue
            if distance < minimum_distance:
                minimum_distance = distance
                minimum_pair = pair
    if minimum_distance == float("inf"):
        return float("nan"), minimum_pair
    return minimum_distance, minimum_pair


# ── keepaway constraints ────────────────────────────────────────────────


def generate_keepaway_constraints(
    risky_pairs: list[tuple[int, int, float, float]],
    keep_apart_floor: float = 3.0,
    force_constant: float = 0.5,
) -> dict[str, Any]:
    """Generate distance constraints from risky pairs."""
    distance_constraints: dict[str, float] = {}
    for atom_i, atom_j, _candidate_distance, product_distance in risky_pairs:
        target = max(product_distance, keep_apart_floor)
        distance_constraints[f"{atom_i} {atom_j}"] = target
    return {
        "distance_constraints": distance_constraints,
        "force_constant": force_constant,
    }


# ── scan trajectory validation ──────────────────────────────────────────


def check_scan_trajectory(
    *,
    product_coords: np.ndarray,
    symbols: list[str],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    frame_paths: tuple[Path, ...] | list[Path],
    graph_scale: float = 1.25,
) -> dict[str, Any]:
    """Validate a scan trajectory against topology drift and RMSD surges.

    Returns a dict with ``off_path_indices``, ``frame_issues``, and
    quality flags.
    """
    total_frames = len(frame_paths)
    off_path_indices: list[int] = []
    frame_issues: list[dict[str, Any]] = []
    prev_coords: np.ndarray | None = None
    rmsd_surge_threshold = 0.5

    for index, frame_path in enumerate(frame_paths):
        try:
            frame_coords, frame_symbols = read_xyz(Path(frame_path))
            frame_coords_np = np.asarray(frame_coords, dtype=float)
        except Exception as exc:
            off_path_indices.append(index)
            frame_issues.append(
                {"frame_index": index, "reason": f"frame_read_error:{exc}"}
            )
            continue

        if len(frame_symbols) != len(symbols):
            off_path_indices.append(index)
            frame_issues.append(
                {"frame_index": index, "reason": "symbol_count_mismatch"}
            )
            continue

        guard_result = compare_graph_topology(
            product_coords=product_coords,
            candidate_coords=frame_coords_np,
            symbols=frame_symbols,
            forming_bonds=forming_bonds,
            graph_scale=graph_scale,
        )
        if not guard_result.is_valid:
            if index not in off_path_indices:
                off_path_indices.append(index)
            frame_issues.append(
                {
                    "frame_index": index,
                    "reason": "topology_drift",
                    "new_edges": len(guard_result.new_edges),
                    "lost_edges": len(guard_result.lost_edges),
                    "new_edge_details": [
                        {
                            "atom_i": int(atom_i),
                            "atom_j": int(atom_j),
                            "distance_angstrom": float(distance),
                        }
                        for atom_i, atom_j, distance in guard_result.new_edges
                    ],
                    "lost_edge_details": [
                        {"atom_i": int(atom_i), "atom_j": int(atom_j)}
                        for atom_i, atom_j in guard_result.lost_edges
                    ],
                }
            )

        if prev_coords is not None:
            rmsd_step = _calculate_rmsd(prev_coords, frame_coords_np)
            if rmsd_step > rmsd_surge_threshold:
                if index not in off_path_indices:
                    off_path_indices.append(index)
                frame_issues.append(
                    {
                        "frame_index": index,
                        "reason": f"rmsd_surge:{rmsd_step:.3f}",
                    }
                )

        prev_coords = frame_coords_np

    topology_drift_frames = sorted(
        {
            issue["frame_index"]
            for issue in frame_issues
            if issue.get("reason") == "topology_drift"
        }
    )
    if topology_drift_frames:
        logger.warning(
            "Topology drift in %d/%d scan frames (frames %d-%d); "
            "per-frame edge details at DEBUG level",
            len(topology_drift_frames),
            total_frames,
            topology_drift_frames[0],
            topology_drift_frames[-1],
        )

    return {
        "checked": total_frames > 0,
        "total_frames": total_frames,
        "off_path_indices": off_path_indices,
        "off_path_count": len(off_path_indices),
        "frame_issues": frame_issues,
    }


__all__ = [
    "RiskyContactResult",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
    "TopologyGuardResult",
    "check_scan_trajectory",
    "compare_graph_topology",
    "compute_min_nonbonded_distance",
    "detect_risky_contacts",
    "generate_keepaway_constraints",
]
