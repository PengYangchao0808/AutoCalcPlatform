"""Versioned scan-attempt and composite-trajectory assembly for S2."""
# ruff: noqa: N802, N803, N806, N815

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from cccp.utils.file_io import read_xyz

from .geometry_guard import _build_bond_graph

HARTREE_TO_KCAL = 627.509


@dataclass(frozen=True)
class ScanAttempt:
    """One immutable xTB scan attempt."""

    attempt_id: str
    kind: str
    directory: Path
    frame_paths: tuple[Path, ...]
    target_coordinates_A: tuple[float, ...]
    xtb_energies_hartree: tuple[float, ...]
    off_path_indices: tuple[int, ...] = ()
    trajectory_quality: dict[str, Any] = field(default_factory=dict)
    parent_attempt_id: str | None = None
    seed_xyz: Path | None = None
    seed_source_attempt: str | None = None
    seed_source_frame_index: int | None = None
    seed_coordinate_A: float | None = None
    scan_policy: str | None = None
    fixed_constraints: dict[str, float] = field(default_factory=dict)
    selected_for_composite: bool = False

    def __post_init__(self) -> None:
        size = len(self.frame_paths)
        if size == 0:
            raise ValueError(f"Scan attempt {self.attempt_id!r} has no frames")
        if len(self.target_coordinates_A) != size or len(self.xtb_energies_hartree) != size:
            raise ValueError(
                f"Scan attempt {self.attempt_id!r} has inconsistent frame/coordinate/energy counts"
            )

    @property
    def coordinate_span_A(self) -> float:
        return max(self.target_coordinates_A) - min(self.target_coordinates_A)

    @property
    def topology_valid_count(self) -> int:
        return len(self.frame_paths) - len(set(self.off_path_indices))

    def to_manifest(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "kind": self.kind,
            "parent_attempt_id": self.parent_attempt_id,
            "seed_xyz": str(self.seed_xyz) if self.seed_xyz else None,
            "seed_source_attempt": self.seed_source_attempt,
            "seed_source_frame_index": self.seed_source_frame_index,
            "seed_coordinate_A": self.seed_coordinate_A,
            "directory": str(self.directory),
            "frame_count": len(self.frame_paths),
            "frame_paths": [str(path) for path in self.frame_paths],
            "target_coordinates_A": list(self.target_coordinates_A),
            "xtb_energies_hartree": list(self.xtb_energies_hartree),
            "coordinate_min_A": min(self.target_coordinates_A),
            "coordinate_max_A": max(self.target_coordinates_A),
            "off_path_indices": list(self.off_path_indices),
            "topology_valid_count": self.topology_valid_count,
            "trajectory_quality": self.trajectory_quality,
            "scan_policy": self.scan_policy,
            "fixed_constraints": dict(self.fixed_constraints),
            "selected_for_composite": self.selected_for_composite,
        }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CompositeProfileBuilder:
    """Build a continuous profile from retained coarse and fine attempts."""

    _KIND_PRIORITY = {
        "coarse": 0,
        "endpoint_extension": 1,
        "ts_refinement": 3,
        "intermediate_refinement": 4,
    }

    def __init__(
        self,
        *,
        coordinate_tolerance_A: float = 0.01,
        min_overlap_points: int = 3,
        max_overlap_rmsd_A: float = 0.75,
        max_reaction_core_rmsd_A: float = 0.50,
        max_overlap_energy_gap_kcal: float = 25.0,
        max_overlap_shape_residual_kcal: float = 15.0,
        forming_bonds: tuple[tuple[int, int], ...] | list[tuple[int, int]] = (),
        reaction_core_depth: int = 2,
    ) -> None:
        self.coordinate_tolerance_A = max(1.0e-6, float(coordinate_tolerance_A))
        self.min_overlap_points = max(1, int(min_overlap_points))
        self.max_overlap_rmsd_A = max(0.0, float(max_overlap_rmsd_A))
        self.max_reaction_core_rmsd_A = max(0.0, float(max_reaction_core_rmsd_A))
        self.max_overlap_energy_gap_kcal = max(0.0, float(max_overlap_energy_gap_kcal))
        self.max_overlap_shape_residual_kcal = max(
            0.0,
            float(max_overlap_shape_residual_kcal),
        )
        self.forming_bonds = tuple((int(i), int(j)) for i, j in forming_bonds)
        self.reaction_core_depth = max(0, int(reaction_core_depth))

    @staticmethod
    def _aligned_rmsd(
        left_path: Path,
        right_path: Path,
        atom_indices: tuple[int, ...] | list[int] | None = None,
    ) -> float:
        left, left_symbols = read_xyz(Path(left_path))
        right, right_symbols = read_xyz(Path(right_path))
        if list(left_symbols) != list(right_symbols):
            raise ValueError("atom_symbol_sequence_mismatch")
        left_array = np.asarray(left, dtype=float)
        right_array = np.asarray(right, dtype=float)
        if left_array.shape != right_array.shape:
            raise ValueError("atom_count_mismatch")
        if atom_indices is not None:
            indices = [int(index) for index in atom_indices]
            if not indices:
                raise ValueError("empty_alignment_atom_set")
            left_array = left_array[indices]
            right_array = right_array[indices]
        left_centered = left_array - left_array.mean(axis=0)
        right_centered = right_array - right_array.mean(axis=0)
        covariance = left_centered.T @ right_centered
        u_matrix, _, v_transpose = np.linalg.svd(covariance)
        rotation = v_transpose.T @ u_matrix.T
        if np.linalg.det(rotation) < 0:
            v_transpose[-1, :] *= -1
            rotation = v_transpose.T @ u_matrix.T
        aligned = left_centered @ rotation.T
        return float(np.sqrt(np.mean(np.sum((aligned - right_centered) ** 2, axis=1))))

    def _reaction_core_indices(self, reference_path: Path) -> tuple[int, ...]:
        coords, symbols = read_xyz(Path(reference_path))
        if not self.forming_bonds:
            heavy = tuple(
                index for index, symbol in enumerate(symbols) if str(symbol).upper() != "H"
            )
            return heavy or tuple(range(len(symbols)))
        graph = _build_bond_graph(np.asarray(coords, dtype=float), list(symbols))
        core = {atom for pair in self.forming_bonds for atom in pair}
        frontier = set(core)
        for _ in range(self.reaction_core_depth):
            neighbors = {
                int(neighbor) for atom in frontier for neighbor in graph.get(int(atom), set())
            }
            frontier = neighbors - core
            core.update(neighbors)
        heavy = tuple(sorted(index for index in core if str(symbols[index]).upper() != "H"))
        return heavy or tuple(sorted(core))

    @staticmethod
    def _backbone_rank(attempt: ScanAttempt) -> tuple[float, int, int, int]:
        kind_bonus = 1 if attempt.kind == "endpoint_extension" else 0
        return (
            round(attempt.coordinate_span_A, 8),
            attempt.topology_valid_count,
            -len(attempt.off_path_indices),
            kind_bonus,
        )

    def _nearest_indices(
        self,
        base_coordinates: tuple[float, ...] | list[float],
        overlay_coordinates: tuple[float, ...] | list[float],
    ) -> list[tuple[int, int]]:
        matches: list[tuple[int, int]] = []
        tolerance = max(self.coordinate_tolerance_A * 2.0, 0.04)
        for overlay_index, coordinate in enumerate(overlay_coordinates):
            base_index = min(
                range(len(base_coordinates)),
                key=lambda index: abs(float(base_coordinates[index]) - float(coordinate)),
            )
            if abs(float(base_coordinates[base_index]) - float(coordinate)) <= tolerance:
                matches.append((base_index, overlay_index))
        return matches

    def build(self, attempts: tuple[ScanAttempt, ...] | list[ScanAttempt]) -> dict[str, Any]:
        if not attempts:
            raise RuntimeError("Cannot assemble an S2 composite profile without scan attempts")

        backbone = max(attempts, key=self._backbone_rank)
        reaction_core_indices = self._reaction_core_indices(backbone.frame_paths[0])
        points: list[dict[str, Any]] = []
        for index, (frame, coordinate, energy) in enumerate(
            zip(
                backbone.frame_paths,
                backbone.target_coordinates_A,
                backbone.xtb_energies_hartree,
            )
        ):
            points.append(self._point(backbone, index, frame, coordinate, energy))

        continuity_checks: list[dict[str, Any]] = []
        overlays = sorted(
            (attempt for attempt in attempts if attempt.attempt_id != backbone.attempt_id),
            key=lambda attempt: (
                self._KIND_PRIORITY.get(attempt.kind, 0),
                -self._median_step(attempt.target_coordinates_A),
            ),
        )
        accepted_attempts = {backbone.attempt_id}
        for attempt in overlays:
            current_coordinates = [float(point["target_coordinate_A"]) for point in points]
            matches = self._nearest_indices(current_coordinates, attempt.target_coordinates_A)
            is_refinement = "refinement" in attempt.kind
            accepted = not is_refinement or len(matches) >= self.min_overlap_points
            reason = "accepted" if accepted else "insufficient_overlap"
            overlap_rmsd_A: list[float] = []
            overlap_reaction_core_rmsd_A: list[float] = []
            overlap_energy_gaps_kcal: list[float] = []
            overlap_energy_offsets_kcal: list[float] = []
            if accepted and matches:
                try:
                    for base_index, overlay_index in matches:
                        overlap_rmsd_A.append(
                            self._aligned_rmsd(
                                Path(points[base_index]["frame_xyz"]),
                                attempt.frame_paths[overlay_index],
                            )
                        )
                        overlap_reaction_core_rmsd_A.append(
                            self._aligned_rmsd(
                                Path(points[base_index]["frame_xyz"]),
                                attempt.frame_paths[overlay_index],
                                reaction_core_indices,
                            )
                        )
                        overlap_energy_gaps_kcal.append(
                            abs(
                                float(points[base_index]["xtb_energy_hartree"])
                                - float(attempt.xtb_energies_hartree[overlay_index])
                            )
                            * HARTREE_TO_KCAL
                        )
                        overlap_energy_offsets_kcal.append(
                            (
                                float(attempt.xtb_energies_hartree[overlay_index])
                                - float(points[base_index]["xtb_energy_hartree"])
                            )
                            * HARTREE_TO_KCAL
                        )
                except (OSError, ValueError, np.linalg.LinAlgError) as exc:
                    accepted = False
                    reason = str(exc)
            rejection_reasons: list[str] = [] if accepted else [reason]
            core_rmsd_p95 = (
                float(np.percentile(overlap_reaction_core_rmsd_A, 95))
                if overlap_reaction_core_rmsd_A
                else None
            )
            energy_offset = (
                float(np.median(overlap_energy_offsets_kcal))
                if overlap_energy_offsets_kcal
                else None
            )
            energy_shape_residuals = (
                [abs(value - energy_offset) for value in overlap_energy_offsets_kcal]
                if energy_offset is not None
                else []
            )
            energy_shape_p95 = (
                float(np.percentile(energy_shape_residuals, 95)) if energy_shape_residuals else None
            )
            if (
                accepted
                and core_rmsd_p95 is not None
                and core_rmsd_p95 > self.max_reaction_core_rmsd_A
            ):
                rejection_reasons.append("reaction_core_rmsd_exceeded")
            if (
                accepted
                and energy_offset is not None
                and abs(energy_offset) > self.max_overlap_energy_gap_kcal
            ):
                rejection_reasons.append("overlap_energy_offset_exceeded")
            if (
                accepted
                and energy_shape_p95 is not None
                and energy_shape_p95 > self.max_overlap_shape_residual_kcal
            ):
                rejection_reasons.append("overlap_energy_shape_residual_exceeded")
            if rejection_reasons:
                accepted = False
                reason = rejection_reasons[0]
            continuity_checks.append(
                {
                    "attempt_id": attempt.attempt_id,
                    "accepted": accepted,
                    "inserted_into_composite": False,
                    "reason": reason,
                    "rejection_reasons": rejection_reasons,
                    "overlap_points": len(matches),
                    "required_overlap_points": self.min_overlap_points if is_refinement else 0,
                    "max_overlap_rmsd_A": max(overlap_rmsd_A) if overlap_rmsd_A else None,
                    "global_rmsd_warning": bool(
                        overlap_rmsd_A and max(overlap_rmsd_A) > self.max_overlap_rmsd_A
                    ),
                    "reaction_core_atom_indices": list(reaction_core_indices),
                    "max_reaction_core_rmsd_A": (
                        max(overlap_reaction_core_rmsd_A) if overlap_reaction_core_rmsd_A else None
                    ),
                    "p95_reaction_core_rmsd_A": core_rmsd_p95,
                    "max_overlap_energy_gap_kcal": (
                        max(overlap_energy_gaps_kcal) if overlap_energy_gaps_kcal else None
                    ),
                    "median_overlap_energy_offset_kcal": energy_offset,
                    "absolute_median_overlap_energy_offset_kcal": (
                        abs(energy_offset) if energy_offset is not None else None
                    ),
                    "p95_overlap_energy_shape_residual_kcal": energy_shape_p95,
                    "alternate_path_candidate": not accepted and bool(matches),
                }
            )
            if not accepted:
                continue
            if not is_refinement:
                continuity_checks[-1]["inserted_into_composite"] = False
                continuity_checks[-1]["reason"] = "compatible_redundant_backbone"
                continue
            accepted_attempts.add(attempt.attempt_id)
            continuity_checks[-1]["inserted_into_composite"] = True

            low = min(attempt.target_coordinates_A)
            high = max(attempt.target_coordinates_A)
            if is_refinement:
                points = [
                    point
                    for point in points
                    if not (
                        low + self.coordinate_tolerance_A
                        < float(point["target_coordinate_A"])
                        < high - self.coordinate_tolerance_A
                    )
                ]
            existing_coordinates = [float(point["target_coordinate_A"]) for point in points]
            for index, (frame, coordinate, energy) in enumerate(
                zip(
                    attempt.frame_paths,
                    attempt.target_coordinates_A,
                    attempt.xtb_energies_hartree,
                )
            ):
                if any(
                    abs(float(coordinate) - existing) <= self.coordinate_tolerance_A
                    for existing in existing_coordinates
                ):
                    continue
                points.append(self._point(attempt, index, frame, coordinate, energy))
                existing_coordinates.append(float(coordinate))

        points.sort(key=lambda point: float(point["target_coordinate_A"]))
        for index, point in enumerate(points):
            point["composite_index"] = index
            point["point_id"] = f"p_{index:04d}"

        valid_points = [point for point in points if point["topology_valid"]]
        if not valid_points:
            raise RuntimeError("S2 composite profile has no topology-valid points")

        return {
            "backbone_attempt_id": backbone.attempt_id,
            "accepted_attempt_ids": sorted(accepted_attempts),
            "points": points,
            "coverage": {
                "coordinate_min_A": min(float(point["target_coordinate_A"]) for point in points),
                "coordinate_max_A": max(float(point["target_coordinate_A"]) for point in points),
                "point_count": len(points),
                "topology_valid_point_count": len(valid_points),
                "complete_xtb_curve": len(points)
                == sum(1 for point in points if point.get("xtb_energy_hartree") is not None),
            },
            "continuity_checks": continuity_checks,
        }

    @staticmethod
    def _median_step(coordinates: tuple[float, ...] | list[float]) -> float:
        if len(coordinates) < 2:
            return float("inf")
        steps = sorted(
            abs(float(right) - float(left)) for left, right in zip(coordinates, coordinates[1:])
        )
        return steps[len(steps) // 2]

    @staticmethod
    def _point(
        attempt: ScanAttempt,
        index: int,
        frame: Path,
        coordinate: float,
        energy: float,
    ) -> dict[str, Any]:
        off_path = int(index) in set(attempt.off_path_indices)
        return {
            "point_id": None,
            "composite_index": None,
            "source_attempt": attempt.attempt_id,
            "source_frame_index": int(index),
            "frame_xyz": str(Path(frame)),
            "xyz_sha256": _file_sha256(Path(frame)),
            "target_coordinate_A": float(coordinate),
            "xtb_energy_hartree": float(energy),
            "topology_valid": not off_path,
        }


def attempt_manifest(attempts: tuple[ScanAttempt, ...] | list[ScanAttempt]) -> list[dict[str, Any]]:
    return [attempt.to_manifest() for attempt in attempts]


__all__ = [
    "CompositeProfileBuilder",
    "HARTREE_TO_KCAL",
    "ScanAttempt",
    "attempt_manifest",
]
