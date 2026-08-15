"""Unified S2 path/profile contract for xTB PATH and ORCA scan paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cccp.utils.file_io import read_xyz
from cccp.utils.geometry_tools import GeometryUtils

from .geometry_guard import _canonicalize_bond_pairs, check_scan_trajectory

HARTREE_TO_KCAL = 627.509


@dataclass(frozen=True)
class PathFrameEvidence:
    frame_index: int
    xyz: Path
    energy_hartree: float | None
    relative_energy_kcal_mol: float | None
    reaction_coordinates: tuple[float, ...]
    progress: float
    topology_valid: bool
    topology_reason: str | None
    rmsd_to_product: float | None
    neighbor_rmsd: float | None
    gradient_proxy: float | None
    curvature_proxy: float | None
    source: str


PathFrame = PathFrameEvidence


@dataclass
class PathProfile:
    source: str
    frame_count: int
    complete: bool
    endpoint_direction: str
    excluded_frames: tuple[int, ...]
    topology_valid_intervals: tuple[tuple[int, int], ...]
    forming_bonds: tuple[tuple[int, int], ...]
    source_provenance: dict[str, Any]
    frames: tuple[PathFrameEvidence, ...]


@dataclass(frozen=True)
class _XYZRecord:
    path: Path
    coords: np.ndarray | None
    symbols: tuple[str, ...] | None
    error: str | None


def _kabsch_rmsd(coords1: np.ndarray, coords2: np.ndarray) -> float:
    if coords1.shape != coords2.shape:
        raise ValueError(f"Shape mismatch: {coords1.shape} vs {coords2.shape}")

    centered_1 = coords1 - coords1.mean(axis=0)
    centered_2 = coords2 - coords2.mean(axis=0)
    covariance = centered_1.T @ centered_2
    u_matrix, _, v_transpose = np.linalg.svd(covariance)
    rotation = u_matrix @ v_transpose
    if np.linalg.det(rotation) < 0:
        v_transpose[-1, :] *= -1
        rotation = u_matrix @ v_transpose
    aligned = centered_2 @ rotation.T
    diff = centered_1 - aligned
    return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))


def _load_xyz_records(frame_paths: tuple[Path, ...] | list[Path]) -> list[_XYZRecord]:
    records: list[_XYZRecord] = []
    for frame_path in frame_paths:
        path = Path(frame_path)
        try:
            coords, symbols = read_xyz(path)
            records.append(
                _XYZRecord(
                    path=path,
                    coords=np.asarray(coords, dtype=float),
                    symbols=tuple(str(symbol) for symbol in symbols),
                    error=None,
                )
            )
        except Exception as exc:
            records.append(_XYZRecord(path=path, coords=None, symbols=None, error=str(exc)))
    return records


def _coerce_provenance(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _coerce_provenance(subvalue) for key, subvalue in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_provenance(item) for item in value]
    return value


def _safe_aligned_rmsd(
    left: _XYZRecord,
    right: _XYZRecord,
    atom_indices: tuple[int, ...] | list[int] | None = None,
) -> float | None:
    if left.coords is None or right.coords is None:
        return None
    if left.symbols != right.symbols:
        return None
    if left.coords.shape != right.coords.shape:
        return None
    try:
        left_array = np.asarray(left.coords, dtype=float)
        right_array = np.asarray(right.coords, dtype=float)
        if atom_indices is not None:
            indices = [int(index) for index in atom_indices]
            if not indices:
                return None
            left_array = left_array[indices]
            right_array = right_array[indices]
        return float(_kabsch_rmsd(left_array, right_array))
    except (IndexError, ValueError, np.linalg.LinAlgError):
        return None


def _topology_valid_intervals(
    valid_mask: tuple[bool, ...] | list[bool],
) -> tuple[tuple[int, int], ...]:
    intervals: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_valid in enumerate(valid_mask):
        if is_valid and start is None:
            start = index
        elif not is_valid and start is not None:
            intervals.append((start, index - 1))
            start = None
    if start is not None:
        intervals.append((start, len(valid_mask) - 1))
    return tuple(intervals)


def _relative_energies_kcal(
    energies_hartree: tuple[float | None, ...] | list[float | None],
) -> list[float | None]:
    reference = next((float(value) for value in energies_hartree if value is not None), None)
    return [
        None if value is None or reference is None else (float(value) - reference) * HARTREE_TO_KCAL
        for value in energies_hartree
    ]


def _normalized_progress(arclength: np.ndarray) -> np.ndarray:
    if arclength.size == 0:
        return np.asarray([], dtype=float)
    maximum = float(arclength[-1])
    if maximum > 1.0e-12:
        return arclength / maximum
    if arclength.size == 1:
        return np.zeros(1, dtype=float)
    return np.linspace(0.0, 1.0, num=arclength.size, dtype=float)


def _energy_derivatives(
    relative_energies_kcal: tuple[float | None, ...] | list[float | None],
    arclength: np.ndarray,
) -> tuple[list[float | None], list[float | None]]:
    gradients: list[float | None] = [None] * len(relative_energies_kcal)
    curvatures: list[float | None] = [None] * len(relative_energies_kcal)
    segment: list[int] = []
    for index, energy in enumerate(relative_energies_kcal):
        if energy is None:
            if segment:
                _write_derivative_segment(
                    segment,
                    relative_energies_kcal,
                    arclength,
                    gradients,
                    curvatures,
                )
                segment = []
            continue
        segment.append(index)
    if segment:
        _write_derivative_segment(
            segment,
            relative_energies_kcal,
            arclength,
            gradients,
            curvatures,
        )
    return gradients, curvatures


def _write_derivative_segment(
    segment: tuple[int, ...] | list[int],
    relative_energies_kcal: tuple[float | None, ...] | list[float | None],
    arclength: np.ndarray,
    gradients: list[float | None],
    curvatures: list[float | None],
) -> None:
    if len(segment) < 2:
        return
    segment_energies: list[float] = []
    for index in segment:
        energy = relative_energies_kcal[index]
        if energy is None:
            return
        segment_energies.append(float(energy))
    y_values = np.asarray(segment_energies, dtype=float)
    x_values = np.asarray([float(arclength[index]) for index in segment], dtype=float)
    if len(segment) == 2 or np.any(np.diff(x_values) <= 1.0e-12):
        x_values = np.arange(len(segment), dtype=float)
    edge_order = 2 if len(segment) > 2 else 1
    gradient_values = np.gradient(y_values, x_values, edge_order=edge_order)
    for local_index, frame_index in enumerate(segment):
        gradients[frame_index] = float(gradient_values[local_index])
    if len(segment) < 3:
        return
    curvature_values = np.gradient(gradient_values, x_values, edge_order=edge_order)
    for local_index, frame_index in enumerate(segment):
        curvatures[frame_index] = float(curvature_values[local_index])


def _product_record(product_xyz: Path) -> _XYZRecord:
    records = _load_xyz_records([product_xyz])
    return records[0]


def _rmsd_to_product(
    product: _XYZRecord,
    candidate: _XYZRecord,
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
) -> float | None:
    if product.coords is None or candidate.coords is None:
        return None
    if product.symbols != candidate.symbols:
        return None
    if product.coords.shape != candidate.coords.shape:
        return None
    reactive_atoms = {int(atom) for pair in forming_bonds for atom in pair}
    scaffold = [index for index in range(len(product.coords)) if index not in reactive_atoms]
    if len(scaffold) >= 3:
        return _safe_aligned_rmsd(product, candidate, atom_indices=scaffold)
    return _safe_aligned_rmsd(product, candidate)


def compute_forming_bond_distances_by_frame(
    frame_paths: tuple[Path, ...] | list[Path],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    records: tuple[_XYZRecord, ...] | list[_XYZRecord] | None = None,
) -> list[list[float] | None]:
    normalized_bonds = _canonicalize_bond_pairs(forming_bonds)
    distances: list[list[float] | None] = []
    for record in records if records is not None else _load_xyz_records(frame_paths):
        if record.coords is None:
            distances.append(None)
            continue
        try:
            distances.append(
                [
                    float(
                        GeometryUtils.calculate_distance(
                            record.coords,
                            int(atom_i),
                            int(atom_j),
                        )
                    )
                    for atom_i, atom_j in normalized_bonds
                ]
            )
        except Exception:
            distances.append(None)
    return distances


def compute_path_arclength(
    frame_paths: tuple[Path, ...] | list[Path],
    records: tuple[_XYZRecord, ...] | list[_XYZRecord] | None = None,
) -> np.ndarray:
    loaded = records if records is not None else _load_xyz_records(frame_paths)
    arclength = np.zeros(len(loaded), dtype=float)
    for index in range(1, len(loaded)):
        step = _safe_aligned_rmsd(loaded[index - 1], loaded[index])
        arclength[index] = arclength[index - 1] + max(0.0, float(step or 0.0))
    return arclength


def compute_neighbor_rmsds(
    frame_paths: tuple[Path, ...] | list[Path],
    records: tuple[_XYZRecord, ...] | list[_XYZRecord] | None = None,
) -> list[float | None]:
    loaded = records if records is not None else _load_xyz_records(frame_paths)
    if not loaded:
        return []
    if len(loaded) == 1:
        return [None]
    step_rmsds = [
        _safe_aligned_rmsd(loaded[index], loaded[index + 1]) for index in range(len(loaded) - 1)
    ]
    local_rmsds: list[float | None] = []
    for index in range(len(loaded)):
        neighbors: list[float] = []
        if index > 0:
            left_step = step_rmsds[index - 1]
            if left_step is not None:
                neighbors.append(float(left_step))
        if index < len(step_rmsds):
            right_step = step_rmsds[index]
            if right_step is not None:
                neighbors.append(float(right_step))
        local_rmsds.append(max(neighbors) if neighbors else None)
    return local_rmsds


def scaffold_rmsd_admission(
    reference_xyz: Path,
    candidate_xyz: Path,
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    maximum_rmsd: float,
) -> dict[str, Any]:
    product = _product_record(Path(reference_xyz))
    candidate = _product_record(Path(candidate_xyz))
    normalized_bonds = _canonicalize_bond_pairs(forming_bonds)
    reactive_atoms = {int(atom) for pair in normalized_bonds for atom in pair}
    if product.coords is None or candidate.coords is None:
        return {
            "accepted": False,
            "atom_count_mismatch": False,
            "rmsd": None,
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_rmsd_A": float(maximum_rmsd),
            "maximum_nonreactive_scaffold_rmsd_A": float(maximum_rmsd),
            "reason": "geometry_check_failed",
        }
    if product.coords.shape != candidate.coords.shape:
        return {
            "accepted": False,
            "atom_count_mismatch": True,
            "rmsd": None,
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_rmsd_A": float(maximum_rmsd),
            "maximum_nonreactive_scaffold_rmsd_A": float(maximum_rmsd),
            "reason": "atom_count_mismatch",
        }
    if product.symbols != candidate.symbols:
        return {
            "accepted": False,
            "atom_count_mismatch": False,
            "rmsd": None,
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_rmsd_A": float(maximum_rmsd),
            "maximum_nonreactive_scaffold_rmsd_A": float(maximum_rmsd),
            "reason": "symbol_sequence_mismatch",
        }
    scaffold = [index for index in range(len(product.coords)) if index not in reactive_atoms]
    if len(scaffold) < 3:
        return {
            "accepted": True,
            "atom_count_mismatch": False,
            "rmsd": None,
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_rmsd_A": float(maximum_rmsd),
            "maximum_nonreactive_scaffold_rmsd_A": float(maximum_rmsd),
            "reason": "insufficient_nonreactive_atoms",
            "scaffold_atom_count": len(scaffold),
        }
    rmsd = _safe_aligned_rmsd(product, candidate, atom_indices=scaffold)
    accepted = rmsd is not None and float(rmsd) <= float(maximum_rmsd)
    return {
        "accepted": bool(accepted),
        "atom_count_mismatch": False,
        "rmsd": None if rmsd is None else float(rmsd),
        "nonreactive_scaffold_rmsd_A": None if rmsd is None else float(rmsd),
        "maximum_rmsd_A": float(maximum_rmsd),
        "maximum_nonreactive_scaffold_rmsd_A": float(maximum_rmsd),
        "reason": None if accepted else "rmsd_exceeds_threshold",
        "scaffold_atom_count": len(scaffold),
        "reactive_atom_count": len(reactive_atoms),
    }


def assess_path_topology(
    product_xyz: Path,
    symbols: tuple[str, ...] | list[str],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    frame_paths: tuple[Path, ...] | list[Path],
) -> dict[str, Any]:
    total_frames = len(frame_paths)
    normalized_bonds = _canonicalize_bond_pairs(forming_bonds)
    product = _product_record(Path(product_xyz))
    if product.coords is None or product.symbols is None:
        reason = "product_read_error"
        off_path_indices = tuple(range(total_frames))
        return {
            "checked": total_frames > 0,
            "total_frames": total_frames,
            "off_path_indices": off_path_indices,
            "off_path_count": len(off_path_indices),
            "frame_issues": tuple(
                {"frame_index": int(index), "reason": reason} for index in off_path_indices
            ),
            "topology_reason_by_frame": tuple(reason for _ in range(total_frames)),
            "topology_valid_intervals": tuple(),
        }
    reference_symbols = tuple(str(symbol) for symbol in symbols) or product.symbols
    if len(reference_symbols) != len(product.coords):
        reference_symbols = product.symbols
    assessment = check_scan_trajectory(
        product_coords=np.asarray(product.coords, dtype=float),
        symbols=list(reference_symbols),
        forming_bonds=normalized_bonds,
        frame_paths=[Path(frame_path) for frame_path in frame_paths],
    )
    issues_by_frame: dict[int, list[str]] = {}
    for issue in assessment.get("frame_issues", []) or []:
        index = int(issue.get("frame_index", -1))
        if index < 0 or index >= total_frames:
            continue
        issues_by_frame.setdefault(index, []).append(str(issue.get("reason", "topology_invalid")))
    off_path_indices = tuple(
        sorted({int(index) for index in assessment.get("off_path_indices", []) or []})
    )
    reasons: list[str | None] = [None] * total_frames
    for index, reason_list in issues_by_frame.items():
        reasons[index] = ";".join(dict.fromkeys(reason_list))
    excluded = set(off_path_indices)
    valid_mask = [index not in excluded for index in range(total_frames)]
    return {
        "checked": bool(assessment.get("checked", False)),
        "total_frames": total_frames,
        "off_path_indices": off_path_indices,
        "off_path_count": len(off_path_indices),
        "frame_issues": tuple(dict(issue) for issue in (assessment.get("frame_issues", []) or [])),
        "topology_reason_by_frame": tuple(reasons),
        "topology_valid_intervals": _topology_valid_intervals(valid_mask),
    }


def _merge_reason(current: str | None, new_reason: str) -> str:
    if not current:
        return new_reason
    tokens = current.split(";")
    if new_reason in tokens:
        return current
    return current + ";" + new_reason


def _prepare_energy_vector(
    frame_count: int,
    energies_hartree: tuple[float | None, ...] | list[float | None],
) -> tuple[list[float | None], bool]:
    energies: list[float | None] = [None] * frame_count
    complete = len(energies_hartree) == frame_count
    for index in range(min(frame_count, len(energies_hartree))):
        value = energies_hartree[index]
        energies[index] = None if value is None else float(value)
        if value is None:
            complete = False
    return energies, complete


def _build_profile(
    *,
    source: str,
    endpoint_direction: str,
    frame_paths: tuple[Path, ...] | list[Path],
    energies_hartree: tuple[float | None, ...] | list[float | None],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    product_xyz: Path,
    extra_excluded_frames: tuple[int, ...] | list[int] | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> PathProfile:
    paths = tuple(Path(frame_path) for frame_path in frame_paths)
    normalized_bonds = _canonicalize_bond_pairs(forming_bonds)
    frame_count = len(paths)
    frame_records = _load_xyz_records(paths)
    product = _product_record(Path(product_xyz))
    energies, complete = _prepare_energy_vector(frame_count, energies_hartree)
    complete = (
        complete
        and product.coords is not None
        and all(record.error is None for record in frame_records)
    )

    product_symbols = list(product.symbols or ())
    topology = assess_path_topology(
        Path(product_xyz),
        product_symbols,
        normalized_bonds,
        paths,
    )
    excluded = set(int(index) for index in (topology.get("off_path_indices") or ()))
    extra_reasons: dict[int, str] = {}
    for index in extra_excluded_frames or ():
        int_index = int(index)
        if 0 <= int_index < frame_count:
            excluded.add(int_index)
            extra_reasons[int_index] = "off_path_from_source"

    topology_reasons: list[str | None] = list(
        topology.get("topology_reason_by_frame") or (None,) * frame_count
    )
    for index, reason in extra_reasons.items():
        topology_reasons[index] = _merge_reason(topology_reasons[index], reason)

    valid_mask = [index not in excluded for index in range(frame_count)]
    topology_intervals = _topology_valid_intervals(valid_mask)
    relative_energies = _relative_energies_kcal(energies)
    forming_bond_distances = compute_forming_bond_distances_by_frame(
        paths,
        normalized_bonds,
        records=frame_records,
    )
    arclength = compute_path_arclength(paths, records=frame_records)
    progress = _normalized_progress(arclength)
    neighbor_rmsds = compute_neighbor_rmsds(paths, records=frame_records)
    gradients, curvatures = _energy_derivatives(relative_energies, arclength)
    rmsd_to_product = [
        _rmsd_to_product(product, record, normalized_bonds) for record in frame_records
    ]
    nonreactive_scaffold_atom_count = 0
    if product.coords is not None:
        reactive_atoms = {int(atom) for pair in normalized_bonds for atom in pair}
        nonreactive_scaffold_atom_count = max(0, len(product.coords) - len(reactive_atoms))

    frames = tuple(
        PathFrameEvidence(
            frame_index=index,
            xyz=paths[index],
            energy_hartree=energies[index],
            relative_energy_kcal_mol=relative_energies[index],
            reaction_coordinates=tuple(forming_bond_distances[index] or ()),
            progress=float(progress[index]) if len(progress) > index else 0.0,
            topology_valid=valid_mask[index],
            topology_reason=topology_reasons[index],
            rmsd_to_product=rmsd_to_product[index],
            neighbor_rmsd=neighbor_rmsds[index],
            gradient_proxy=gradients[index],
            curvature_proxy=curvatures[index],
            source=source,
        )
        for index in range(frame_count)
    )

    provenance = dict(_coerce_provenance(dict(source_provenance or {})))
    provenance.setdefault(
        "product_atom_count",
        len(product.coords) if product.coords is not None else None,
    )
    provenance.setdefault(
        "nonreactive_scaffold_atom_count",
        nonreactive_scaffold_atom_count,
    )
    return PathProfile(
        source=source,
        frame_count=frame_count,
        complete=bool(complete),
        endpoint_direction=endpoint_direction,
        excluded_frames=tuple(sorted(excluded)),
        topology_valid_intervals=topology_intervals,
        forming_bonds=normalized_bonds,
        source_provenance=provenance,
        frames=frames,
    )


def build_xtb_path_profile(
    *,
    frame_paths: tuple[Path, ...] | list[Path],
    energies_hartree: tuple[float | None, ...] | list[float | None],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    product_xyz: Path,
    off_path_indices: tuple[int, ...] | list[int],
    source_provenance: dict[str, Any] | None = None,
) -> PathProfile:
    return _build_profile(
        source="xtb_peb",
        endpoint_direction="start",
        frame_paths=frame_paths,
        energies_hartree=energies_hartree,
        forming_bonds=forming_bonds,
        product_xyz=Path(product_xyz),
        extra_excluded_frames=off_path_indices,
        source_provenance=source_provenance,
    )


def build_orca_scan_profile(
    *,
    frames: tuple[Path, ...] | list[Path],
    energies_hartree: tuple[float | None, ...] | list[float | None],
    forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    product_xyz: Path,
    energy_source: str,
    scan_ts_candidate_xyz: Path | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> PathProfile:
    provenance = dict(source_provenance or {})
    provenance["energy_source"] = str(energy_source)
    if scan_ts_candidate_xyz is not None:
        provenance["scan_ts_candidate_xyz"] = str(Path(scan_ts_candidate_xyz))
    return _build_profile(
        source="orca_relaxed_scan",
        endpoint_direction="end",
        frame_paths=frames,
        energies_hartree=energies_hartree,
        forming_bonds=forming_bonds,
        product_xyz=Path(product_xyz),
        extra_excluded_frames=None,
        source_provenance=provenance,
    )


__all__ = [
    "HARTREE_TO_KCAL",
    "PathFrame",
    "PathFrameEvidence",
    "PathProfile",
    "assess_path_topology",
    "build_orca_scan_profile",
    "build_xtb_path_profile",
    "compute_forming_bond_distances_by_frame",
    "compute_neighbor_rmsds",
    "compute_path_arclength",
    "scaffold_rmsd_admission",
]
