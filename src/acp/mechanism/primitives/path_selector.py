"""Unified S2 seed-selection policy for path and relaxed-scan profiles."""
# ruff: noqa: N803, N815

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .path_profile import PathFrameEvidence, PathProfile, build_orca_scan_profile


@dataclass
class SelectionPolicy:
    """Unified selector policy."""

    endpoint_exclusion_frames: int = 2
    min_reaction_progress: float = 0.35
    min_valid_neighbor_window: int = 1
    allow_monotonic_shoulder: bool = True
    shoulder_max_abs_slope_kcal_mol_per_A: float = 20.0
    shoulder_min_curvature_signal: float = 0.05
    allow_shared_search_seed: bool = True
    ts_min_prominence_kcal_mol: float = 0.40
    int_min_basin_prominence_kcal_mol: float = 0.50
    ts_min_reactant_barrier_kcal_mol: float = 3.0
    max_nonreactive_scaffold_rmsd_A: float = 0.75
    minimum_clean_frames_after_knee: int = 2
    ts_confidence_high_threshold: float = 0.50
    endpoint_guard_frames: int = 1
    endpoint_min_valid_frames: int = 3
    knee_enabled: bool = True
    knee_smoothing_window: int = 5
    knee_min_left_support_frames: int = 2
    knee_min_right_support_frames: int = 2
    knee_min_curvature_signal: float = 0.05
    knee_min_slope_change_kcal_mol_per_A: float = 0.0
    ts_right_shift_base_A: float = 0.15
    ts_right_shift_span_fraction: float = 0.10
    ts_right_shift_min_A: float = 0.05
    ts_right_shift_max_A: float = 0.40
    ts_right_shift_override_A: float | None = None
    int_seed_mode: str = "ts_to_effective_endpoint_midpoint"
    int_plateau_min_consecutive_frames: int = 3
    int_plateau_energy_window_kcal_mol: float = 2.0
    int_plateau_min_ts_separation_A: float = 0.10
    int_plateau_max_slope_kcal_mol_A: float = 40.0
    require_barrier_for_search_seed: bool = False
    require_scaffold_for_search_seed: bool = False


@dataclass
class SeedSelection:
    s2_state: str
    seed_evidence: str
    ts_search_seed: dict[str, Any] | None = None
    int_search_seed: dict[str, Any] | None = None
    has_independent_int: bool = False
    rejection_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    endpoint_evidence: dict[str, Any] | None = None
    knee_evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "s2_state": self.s2_state,
            "seed_evidence": self.seed_evidence,
            "ts_search_seed": None if self.ts_search_seed is None else dict(self.ts_search_seed),
            "int_search_seed": (
                None if self.int_search_seed is None else dict(self.int_search_seed)
            ),
            "has_independent_int": bool(self.has_independent_int),
            "rejection_reason": self.rejection_reason,
            "diagnostics": dict(self.diagnostics),
            "endpoint_evidence": (
                None if self.endpoint_evidence is None else dict(self.endpoint_evidence)
            ),
            "knee_evidence": None if self.knee_evidence is None else dict(self.knee_evidence),
        }


def policy_from_config(
    selection_cfg: Mapping[str, Any],
    rescue_cfg: Mapping[str, Any] | None = None,
) -> SelectionPolicy:
    selection_cfg = dict(selection_cfg or {})
    rescue_cfg = dict(rescue_cfg or {})
    return SelectionPolicy(
        endpoint_exclusion_frames=max(
            0,
            int(selection_cfg.get("endpoint_exclusion_frames", 2)),
        ),
        min_reaction_progress=min(
            1.0,
            max(0.0, float(selection_cfg.get("min_reaction_progress", 0.35))),
        ),
        min_valid_neighbor_window=max(
            1,
            int(selection_cfg.get("min_valid_neighbor_window", 1)),
        ),
        allow_monotonic_shoulder=bool(selection_cfg.get("allow_monotonic_shoulder", True)),
        shoulder_max_abs_slope_kcal_mol_per_A=max(
            0.0,
            float(selection_cfg.get("shoulder_max_abs_slope_kcal_mol_per_A", 20.0)),
        ),
        shoulder_min_curvature_signal=max(
            0.0,
            float(selection_cfg.get("shoulder_min_curvature_signal", 0.05)),
        ),
        allow_shared_search_seed=bool(selection_cfg.get("allow_shared_search_seed", True)),
        ts_min_prominence_kcal_mol=max(
            0.0,
            float(
                selection_cfg.get(
                    "ts_min_prominence_kcal_mol",
                    rescue_cfg.get("ts_min_prominence_kcal_mol", 0.40),
                )
            ),
        ),
        int_min_basin_prominence_kcal_mol=max(
            0.0,
            float(selection_cfg.get("int_min_basin_prominence_kcal_mol", 0.50)),
        ),
        ts_min_reactant_barrier_kcal_mol=max(
            0.0,
            float(
                selection_cfg.get(
                    "ts_min_reactant_barrier_kcal_mol",
                    rescue_cfg.get("ts_min_reactant_barrier_kcal_mol", 3.0),
                )
            ),
        ),
        max_nonreactive_scaffold_rmsd_A=max(
            0.0,
            float(
                selection_cfg.get(
                    "max_nonreactive_scaffold_rmsd_A",
                    rescue_cfg.get("max_nonreactive_rmsd_A", 0.75),
                )
            ),
        ),
        minimum_clean_frames_after_knee=max(
            1,
            int(selection_cfg.get("minimum_clean_frames_after_knee", 2)),
        ),
        ts_confidence_high_threshold=max(
            0.0,
            float(selection_cfg.get("ts_confidence_high_threshold", 0.50)),
        ),
        endpoint_guard_frames=max(
            0,
            int(
                dict(selection_cfg.get("endpoint", {}) or {}).get(
                    "guard_frames",
                    selection_cfg.get("endpoint_guard_frames", 1),
                )
            ),
        ),
        endpoint_min_valid_frames=max(
            3,
            int(
                dict(selection_cfg.get("endpoint", {}) or {}).get(
                    "min_valid_frames",
                    selection_cfg.get("endpoint_min_valid_frames", 3),
                )
            ),
        ),
        knee_enabled=bool(
            dict(selection_cfg.get("knee", {}) or {}).get(
                "enabled",
                selection_cfg.get("knee_enabled", True),
            )
        ),
        knee_smoothing_window=max(
            3,
            int(
                dict(selection_cfg.get("knee", {}) or {}).get(
                    "smoothing_window",
                    selection_cfg.get("knee_smoothing_window", 5),
                )
            ),
        ),
        knee_min_left_support_frames=max(
            1,
            int(
                dict(selection_cfg.get("knee", {}) or {}).get(
                    "minimum_left_support_frames",
                    selection_cfg.get("knee_min_left_support_frames", 2),
                )
            ),
        ),
        knee_min_right_support_frames=max(
            1,
            int(
                dict(selection_cfg.get("knee", {}) or {}).get(
                    "minimum_right_support_frames",
                    selection_cfg.get("knee_min_right_support_frames", 2),
                )
            ),
        ),
        knee_min_curvature_signal=max(
            0.0,
            float(
                dict(selection_cfg.get("knee", {}) or {}).get(
                    "minimum_curvature_signal",
                    selection_cfg.get(
                        "knee_min_curvature_signal",
                        selection_cfg.get("shoulder_min_curvature_signal", 0.05),
                    ),
                )
            ),
        ),
        knee_min_slope_change_kcal_mol_per_A=max(
            0.0,
            float(
                dict(selection_cfg.get("knee", {}) or {}).get(
                    "minimum_slope_change_kcal_mol_per_A",
                    selection_cfg.get("knee_min_slope_change_kcal_mol_per_A", 0.0),
                )
            ),
        ),
        ts_right_shift_base_A=max(
            0.0,
            float(
                dict(dict(selection_cfg.get("ts_seed", {}) or {}).get("right_shift", {}) or {}).get(
                    "base_A",
                    selection_cfg.get("ts_right_shift_base_A", 0.15),
                )
            ),
        ),
        ts_right_shift_span_fraction=max(
            0.0,
            float(
                dict(dict(selection_cfg.get("ts_seed", {}) or {}).get("right_shift", {}) or {}).get(
                    "span_fraction",
                    selection_cfg.get("ts_right_shift_span_fraction", 0.10),
                )
            ),
        ),
        ts_right_shift_min_A=max(
            0.0,
            float(
                dict(dict(selection_cfg.get("ts_seed", {}) or {}).get("right_shift", {}) or {}).get(
                    "min_A",
                    selection_cfg.get("ts_right_shift_min_A", 0.05),
                )
            ),
        ),
        ts_right_shift_max_A=max(
            0.0,
            float(
                dict(dict(selection_cfg.get("ts_seed", {}) or {}).get("right_shift", {}) or {}).get(
                    "max_A",
                    selection_cfg.get("ts_right_shift_max_A", 0.40),
                )
            ),
        ),
        ts_right_shift_override_A=(
            None
            if dict(dict(selection_cfg.get("ts_seed", {}) or {}).get("right_shift", {}) or {}).get(
                "override_A"
            )
            is None
            else max(
                0.0,
                float(
                    dict(
                        dict(selection_cfg.get("ts_seed", {}) or {}).get(
                            "right_shift",
                            {},
                        )
                        or {}
                    ).get("override_A")
                ),
            )
        ),
        int_seed_mode=str(
            dict(selection_cfg.get("int_seed", {}) or {}).get(
                "mode",
                selection_cfg.get("int_seed_mode", "ts_to_effective_endpoint_midpoint"),
            )
        ),
        int_plateau_min_consecutive_frames=int(
            dict(selection_cfg.get("int_plateau", {}) or {}).get(
                "min_consecutive_frames",
                int(selection_cfg.get("int_plateau_min_consecutive_frames", 3)),
            )
        ),
        int_plateau_energy_window_kcal_mol=float(
            dict(selection_cfg.get("int_plateau", {}) or {}).get(
                "energy_window_kcal_mol",
                float(selection_cfg.get("int_plateau_energy_window_kcal_mol", 2.0)),
            )
        ),
        int_plateau_min_ts_separation_A=float(
            dict(selection_cfg.get("int_plateau", {}) or {}).get(
                "min_ts_separation_A",
                float(selection_cfg.get("int_plateau_min_ts_separation_A", 0.10)),
            )
        ),
        int_plateau_max_slope_kcal_mol_A=float(
            dict(selection_cfg.get("int_plateau", {}) or {}).get(
                "max_slope_kcal_mol_A",
                float(selection_cfg.get("int_plateau_max_slope_kcal_mol_A", 40.0)),
            )
        ),
        require_barrier_for_search_seed=bool(
            dict(selection_cfg.get("admission", {}) or {}).get(
                "require_barrier_for_search_seed",
                selection_cfg.get("require_barrier_for_search_seed", False),
            )
        ),
        require_scaffold_for_search_seed=bool(
            dict(selection_cfg.get("admission", {}) or {}).get(
                "require_scaffold_for_search_seed",
                selection_cfg.get("require_scaffold_for_search_seed", False),
            )
        ),
    )


def _reactant_oriented_frames(profile: PathProfile) -> list[PathFrameEvidence]:
    ordered = sorted(profile.frames, key=lambda frame: int(frame.frame_index))
    if profile.endpoint_direction == "end":
        ordered.reverse()
    return ordered


def _selection_ordered_frames(profile: PathProfile) -> list[PathFrameEvidence]:
    return sorted(profile.frames, key=lambda frame: int(frame.frame_index))


def _endpoint_excluded(
    profile: PathProfile,
    frame_index: int,
    policy: SelectionPolicy,
) -> bool:
    exclusion_frames = max(
        int(policy.endpoint_exclusion_frames),
        int(policy.endpoint_guard_frames),
    )
    if exclusion_frames <= 0:
        return False
    if profile.endpoint_direction == "end":
        return int(frame_index) >= profile.frame_count - exclusion_frames
    return int(frame_index) < exclusion_frames


def _energy_segments(frames: Sequence[PathFrameEvidence]) -> list[list[PathFrameEvidence]]:
    segments: list[list[PathFrameEvidence]] = []
    current: list[PathFrameEvidence] = []
    for frame in frames:
        if frame.relative_energy_kcal_mol is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(frame)
    if current:
        segments.append(current)
    return segments


def _asynchronicity(frame: PathFrameEvidence) -> float | None:
    if len(frame.reaction_coordinates) != 2:
        return None
    return abs(float(frame.reaction_coordinates[0]) - float(frame.reaction_coordinates[1]))


def _coordinate_vector(frame: PathFrameEvidence) -> tuple[float, ...]:
    if frame.reaction_coordinates:
        return tuple(float(value) for value in frame.reaction_coordinates)
    return (float(frame.frame_index),)


def _coordinate_distance(left: PathFrameEvidence, right: PathFrameEvidence) -> float:
    left_vector = _coordinate_vector(left)
    right_vector = _coordinate_vector(right)
    if len(left_vector) != len(right_vector):
        return abs(float(left.frame_index) - float(right.frame_index))
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left_vector, right_vector)))


def _coordinate_positions(frames: Sequence[PathFrameEvidence]) -> dict[int, float]:
    positions: dict[int, float] = {}
    cumulative = 0.0
    previous: PathFrameEvidence | None = None
    for frame in frames:
        if previous is not None:
            step = _coordinate_distance(previous, frame)
            cumulative += step if step > 1.0e-12 else 1.0
        positions[int(frame.frame_index)] = float(cumulative)
        previous = frame
    return positions


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _smooth_values(values: Sequence[float], window: int) -> list[float]:
    if len(values) < 3:
        return [float(value) for value in values]
    width = max(3, int(window))
    if width % 2 == 0:
        width += 1
    width = min(width, len(values) if len(values) % 2 else len(values) - 1)
    radius = max(1, width // 2)
    smoothed: list[float] = []
    for index in range(len(values)):
        left = max(0, index - radius)
        right = min(len(values), index + radius + 1)
        smoothed.append(float(sum(values[left:right]) / max(1, right - left)))
    return smoothed


def _derivative_series(
    values: Sequence[float],
    positions: Sequence[float],
) -> tuple[list[float], list[float]]:
    count = len(values)
    gradients = [0.0] * count
    curvatures = [0.0] * count
    if count < 2:
        return gradients, curvatures
    for index in range(count):
        if index == 0:
            left, right = 0, 1
        elif index == count - 1:
            left, right = count - 2, count - 1
        else:
            left, right = index - 1, index + 1
        delta_x = float(positions[right]) - float(positions[left])
        gradients[index] = (
            0.0
            if abs(delta_x) <= 1.0e-12
            else (float(values[right]) - float(values[left])) / delta_x
        )
    if count < 3:
        return gradients, curvatures
    for index in range(count):
        if index == 0:
            left, right = 0, 1
        elif index == count - 1:
            left, right = count - 2, count - 1
        else:
            left, right = index - 1, index + 1
        delta_x = float(positions[right]) - float(positions[left])
        curvatures[index] = (
            0.0
            if abs(delta_x) <= 1.0e-12
            else (float(gradients[right]) - float(gradients[left])) / delta_x
        )
    return gradients, curvatures


def _endpoint_evidence(
    profile: PathProfile,
    ordered_frames: Sequence[PathFrameEvidence],
    candidate_frames: Sequence[PathFrameEvidence],
    policy: SelectionPolicy,
) -> dict[str, Any]:
    candidate_ids = {int(frame.frame_index) for frame in candidate_frames}
    ordered_ids = [int(frame.frame_index) for frame in ordered_frames]
    invalid_ids = {
        int(frame.frame_index) for frame in ordered_frames if not bool(frame.topology_valid)
    }
    distortion_index = next(
        (index for index in ordered_ids if index in invalid_ids),
        None,
    )
    valid_endpoint = int(candidate_frames[-1].frame_index) if candidate_frames else None
    stretch_endpoint = int(ordered_frames[-1].frame_index) if ordered_frames else None
    path_class = (
        "no_valid_corridor"
        if valid_endpoint is None
        else "topology_distorted_tail"
        if distortion_index is not None
        else "topology_valid_to_scan_endpoint"
    )
    return {
        "substrate_endpoint_index": int(ordered_frames[0].frame_index) if ordered_frames else None,
        "stretch_endpoint_index": stretch_endpoint,
        "distortion_frame_index": distortion_index,
        "valid_endpoint_index": valid_endpoint,
        "effective_endpoint_index": valid_endpoint,
        "path_class": path_class,
        "endpoint_direction": profile.endpoint_direction,
        "candidate_frame_count": len(candidate_ids),
        "minimum_valid_frames": int(policy.endpoint_min_valid_frames),
    }


def _resolve_knee_candidate(
    segments: Sequence[Sequence[PathFrameEvidence]],
    positions: Mapping[int, float],
    policy: SelectionPolicy,
) -> tuple[PathFrameEvidence | None, list[dict[str, Any]]]:
    candidates: list[tuple[float, int, PathFrameEvidence]] = []
    diagnostics: list[dict[str, Any]] = []
    for segment in segments:
        if len(segment) < max(
            3,
            policy.knee_min_left_support_frames + policy.knee_min_right_support_frames + 1,
        ):
            continue
        raw_values = [
            float(frame.relative_energy_kcal_mol)
            for frame in segment
            if frame.relative_energy_kcal_mol is not None
        ]
        if len(raw_values) != len(segment):
            continue
        x_values = [float(positions[int(frame.frame_index)]) for frame in segment]
        values = _smooth_values(raw_values, policy.knee_smoothing_window)
        gradients, curvatures = _derivative_series(values, x_values)
        start = policy.knee_min_left_support_frames
        stop = len(segment) - policy.knee_min_right_support_frames
        for index in range(start, stop):
            frame = segment[index]
            curvature = abs(float(curvatures[index]))
            left_slope = abs(float(gradients[index - 1]))
            right_end = min(len(gradients), index + 1 + policy.knee_min_right_support_frames)
            right_slope = _median([abs(float(value)) for value in gradients[index + 1 : right_end]])
            slope_change = max(0.0, left_slope - right_slope)
            slope_drop_ratio = slope_change / max(left_slope, 1.0e-12)
            accepted = (
                curvature >= policy.knee_min_curvature_signal
                and slope_change >= policy.knee_min_slope_change_kcal_mol_per_A
                and bool(frame.topology_valid)
            )
            score = curvature * (0.5 + slope_drop_ratio) / (1.0 + right_slope)
            record = {
                "frame_index": int(frame.frame_index),
                "coordinate_A": float(x_values[index]),
                "gradient_kcal_mol_per_A": float(gradients[index]),
                "curvature_signal": float(curvature),
                "left_slope_kcal_mol_per_A": float(left_slope),
                "right_slope_kcal_mol_per_A": float(right_slope),
                "slope_change_kcal_mol_per_A": float(slope_change),
                "slope_drop_ratio": float(slope_drop_ratio),
                "score": float(score),
                "accepted": bool(accepted),
            }
            diagnostics.append(record)
            if accepted:
                candidates.append((float(score), int(index), frame))
    if not candidates:
        return None, diagnostics
    _score, _position, selected = max(candidates, key=lambda item: (item[0], item[1]))
    return selected, diagnostics


def _make_ts_seed(frame: PathFrameEvidence, confidence: str) -> dict[str, Any]:
    return {
        "frame_index": int(frame.frame_index),
        "xyz": str(frame.xyz),
        "confidence": str(confidence),
    }


def _make_int_seed(
    frame: PathFrameEvidence,
    shared_with_ts: bool,
    *,
    selection_mode: str = "shared_ts_fallback",
) -> dict[str, Any]:
    return {
        "frame_index": int(frame.frame_index),
        "xyz": str(frame.xyz),
        "shared_with_ts": bool(shared_with_ts),
        "selection_mode": str(selection_mode),
    }


def _detect_stretch_plateau(
    frames: Sequence[PathFrameEvidence],
    positions: Mapping[int, float],
    ts_position: float,
    endpoint_position: float,
    *,
    min_consecutive_frames: int = 3,
    energy_window_kcal_mol: float = 2.0,
    min_ts_separation_A: float = 0.10,
    max_slope_kcal_mol_per_A: float = 40.0,
) -> PathFrameEvidence | None:
    if not frames:
        return None
    candidates: list[PathFrameEvidence] = []
    for frame in frames:
        if frame.relative_energy_kcal_mol is None:
            continue
        coordinate = positions.get(int(frame.frame_index))
        if coordinate is None:
            continue
        if coordinate < ts_position + min_ts_separation_A:
            continue
        if coordinate > endpoint_position:
            continue
        candidates.append(frame)
    if len(candidates) < min_consecutive_frames:
        return None

    ordered = sorted(candidates, key=lambda frame: positions[int(frame.frame_index)])
    energies = [float(frame.relative_energy_kcal_mol) for frame in ordered]
    best_start: int | None = None
    best_len = 0
    run_start = 0
    for index in range(1, len(ordered)):
        energy_jump = abs(energies[index] - energies[index - 1])
        if energy_jump <= energy_window_kcal_mol:
            continue
        run_len = index - run_start
        if run_len >= min_consecutive_frames and run_len > best_len:
            best_len = run_len
            best_start = run_start
        run_start = index
    run_len = len(ordered) - run_start
    if run_len >= min_consecutive_frames and run_len > best_len:
        best_len = run_len
        best_start = run_start
    if best_start is None:
        return None

    plateau_run = ordered[best_start : best_start + best_len]
    run_energies = [float(frame.relative_energy_kcal_mol) for frame in plateau_run]
    slope = 0.0
    for index in range(1, len(plateau_run)):
        coord_prev = positions[int(plateau_run[index - 1].frame_index)]
        coord_cur = positions[int(plateau_run[index].frame_index)]
        delta_coord = coord_cur - coord_prev
        if delta_coord > 1e-9:
            slope = max(
                slope,
                abs(run_energies[index] - run_energies[index - 1]) / delta_coord,
            )
    if slope > max_slope_kcal_mol_per_A:
        return None
    return plateau_run[0]


def _neighbor_window_lookup(
    frames: Sequence[PathFrameEvidence],
    policy: SelectionPolicy,
) -> dict[int, bool]:
    support: dict[int, bool] = {}
    minimum = max(1, int(policy.min_valid_neighbor_window))
    for position, frame in enumerate(frames):
        support[int(frame.frame_index)] = position >= minimum and position + minimum < len(frames)
    return support


def _reactant_reference_frame(profile: PathProfile) -> PathFrameEvidence | None:
    for frame in _reactant_oriented_frames(profile):
        if frame.relative_energy_kcal_mol is not None:
            return frame
    return None


def _barrier_from_reactant(profile: PathProfile, ts_frame: PathFrameEvidence) -> float | None:
    ordered = _selection_ordered_frames(profile)
    reference = next(
        (frame for frame in ordered if frame.relative_energy_kcal_mol is not None),
        None,
    )
    if reference is None or ts_frame.relative_energy_kcal_mol is None:
        return None
    reference_energy = reference.relative_energy_kcal_mol
    if reference_energy is None:
        return None
    return float(ts_frame.relative_energy_kcal_mol) - float(reference_energy)


def _scaffold_gate(
    profile: PathProfile,
    frame: PathFrameEvidence,
    policy: SelectionPolicy,
) -> dict[str, Any]:
    scaffold_count_raw = profile.source_provenance.get("nonreactive_scaffold_atom_count")
    scaffold_count = None if scaffold_count_raw is None else int(scaffold_count_raw)
    if scaffold_count is not None and scaffold_count < 3:
        return {
            "accepted": True,
            "reason": "insufficient_nonreactive_atoms",
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_nonreactive_scaffold_rmsd_A": float(policy.max_nonreactive_scaffold_rmsd_A),
        }
    if frame.rmsd_to_product is None:
        return {
            "accepted": False,
            "reason": "missing_rmsd_to_product",
            "nonreactive_scaffold_rmsd_A": None,
            "maximum_nonreactive_scaffold_rmsd_A": float(policy.max_nonreactive_scaffold_rmsd_A),
        }
    return {
        "accepted": float(frame.rmsd_to_product) <= float(policy.max_nonreactive_scaffold_rmsd_A),
        "reason": None,
        "nonreactive_scaffold_rmsd_A": float(frame.rmsd_to_product),
        "maximum_nonreactive_scaffold_rmsd_A": float(policy.max_nonreactive_scaffold_rmsd_A),
    }


def _resolve_basin_candidate(
    segment: Sequence[PathFrameEvidence],
    peak_position: int,
    policy: SelectionPolicy,
) -> tuple[PathFrameEvidence | None, list[dict[str, Any]]]:
    basin_candidates: list[tuple[int, float]] = []
    diagnostics: list[dict[str, Any]] = []
    for position in range(1, peak_position):
        left = segment[position - 1]
        center = segment[position]
        right = segment[position + 1]
        if (
            center.relative_energy_kcal_mol is None
            or left.relative_energy_kcal_mol is None
            or right.relative_energy_kcal_mol is None
        ):
            continue
        left_wall = float(left.relative_energy_kcal_mol) - float(center.relative_energy_kcal_mol)
        right_wall = float(right.relative_energy_kcal_mol) - float(center.relative_energy_kcal_mol)
        prominence = min(left_wall, right_wall)
        diagnostics.append(
            {
                "frame_index": int(center.frame_index),
                "prominence_kcal_mol": float(prominence),
            }
        )
        if (
            left_wall > 0.0
            and right_wall > 0.0
            and prominence >= policy.int_min_basin_prominence_kcal_mol
        ):
            basin_candidates.append((position, float(prominence)))
    if not basin_candidates:
        return None, diagnostics
    selected_position, _prominence = max(basin_candidates, key=lambda item: (item[0], item[1]))
    return segment[selected_position], diagnostics


def _resolve_shoulder_candidate(
    segments: Sequence[Sequence[PathFrameEvidence]],
    neighbor_support: Mapping[int, bool],
    policy: SelectionPolicy,
) -> tuple[PathFrameEvidence | None, list[dict[str, Any]]]:
    accepted_groups: list[list[PathFrameEvidence]] = []
    diagnostics: list[dict[str, Any]] = []
    for segment in segments:
        if len(segment) < 3:
            continue
        current: list[PathFrameEvidence] = []
        for position in range(1, len(segment) - 1):
            frame = segment[position]
            gradient = frame.gradient_proxy
            curvature = frame.curvature_proxy
            signal = None if curvature is None else abs(float(curvature))
            accepted = (
                gradient is not None
                and signal is not None
                and abs(float(gradient)) <= policy.shoulder_max_abs_slope_kcal_mol_per_A
                and signal >= policy.shoulder_min_curvature_signal
                and bool(neighbor_support.get(int(frame.frame_index), False))
            )
            diagnostics.append(
                {
                    "frame_index": int(frame.frame_index),
                    "abs_slope_kcal_mol_per_A": None if gradient is None else abs(float(gradient)),
                    "curvature_signal": signal,
                    "accepted": bool(accepted),
                }
            )
            if accepted:
                current.append(frame)
            elif current:
                accepted_groups.append(current)
                current = []
        if current:
            accepted_groups.append(current)
    if not accepted_groups:
        return None, diagnostics
    group = max(
        accepted_groups,
        key=lambda values: (
            max(float(frame.relative_energy_kcal_mol or 0.0) for frame in values),
            len(values),
            values[-1].progress,
        ),
    )
    return group[len(group) // 2], diagnostics


def select_path_seeds(profile: PathProfile, policy: SelectionPolicy) -> SeedSelection:
    diagnostics: dict[str, Any] = {
        "profile_source": profile.source,
        "complete": bool(profile.complete),
        "endpoint_direction": profile.endpoint_direction,
        "frame_count": int(profile.frame_count),
        "selection_algorithm": "endpoint_knee_shift_midpoint_v1",
    }
    if not profile.frames:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_frames",
            diagnostics=diagnostics,
        )
    if not profile.complete:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="incomplete_profile",
            diagnostics=diagnostics,
        )

    oriented_frames = _selection_ordered_frames(profile)
    positions = _coordinate_positions(oriented_frames)
    valid_frames = [
        frame
        for frame in oriented_frames
        if frame.topology_valid
        and frame.relative_energy_kcal_mol is not None
        and float(frame.progress) >= float(policy.min_reaction_progress)
    ]
    base_candidates = [
        frame
        for frame in valid_frames
        if not _endpoint_excluded(profile, int(frame.frame_index), policy)
    ]
    diagnostics["filtered_frame_indices"] = [int(frame.frame_index) for frame in base_candidates]
    diagnostics["excluded_frame_indices"] = [int(index) for index in profile.excluded_frames]
    endpoint_evidence = _endpoint_evidence(profile, oriented_frames, valid_frames, policy)
    diagnostics["endpoints"] = endpoint_evidence
    asynchronicity_by_frame: dict[str, float] = {}
    for frame in base_candidates:
        value = _asynchronicity(frame)
        if value is not None:
            asynchronicity_by_frame[str(int(frame.frame_index))] = float(value)
    diagnostics["asynchronicity_by_frame_A"] = asynchronicity_by_frame

    if not base_candidates:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_valid_frames_after_filters",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
        )
    if len(base_candidates) < policy.endpoint_min_valid_frames:
        diagnostics["valid_frame_count"] = len(base_candidates)
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="insufficient_valid_endpoint_corridor",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
        )

    neighbor_support = _neighbor_window_lookup(base_candidates, policy)
    diagnostics["neighbor_window_support"] = {
        str(frame_index): bool(supported) for frame_index, supported in neighbor_support.items()
    }
    energy_segments = _energy_segments(base_candidates)
    diagnostics["energy_segments"] = [
        [int(frame.frame_index) for frame in segment] for segment in energy_segments
    ]

    peak_diagnostics: list[dict[str, Any]] = []
    peak_anchors: list[tuple[float, PathFrameEvidence]] = []
    for segment in energy_segments:
        for index in range(1, len(segment) - 1):
            left, center, right = segment[index - 1 : index + 2]
            prominence = min(
                float(center.relative_energy_kcal_mol) - float(left.relative_energy_kcal_mol),
                float(center.relative_energy_kcal_mol) - float(right.relative_energy_kcal_mol),
            )
            is_peak = float(center.relative_energy_kcal_mol) > float(
                left.relative_energy_kcal_mol
            ) and float(center.relative_energy_kcal_mol) > float(right.relative_energy_kcal_mol)
            accepted = bool(
                is_peak
                and prominence >= policy.ts_min_prominence_kcal_mol
                and neighbor_support.get(int(center.frame_index), False)
            )
            peak_diagnostics.append(
                {
                    "frame_index": int(center.frame_index),
                    "prominence_kcal_mol": float(prominence),
                    "neighbor_window_ok": bool(
                        neighbor_support.get(int(center.frame_index), False)
                    ),
                    "accepted_as_knee_anchor": accepted,
                }
            )
            if accepted:
                peak_anchors.append((float(prominence), center))
    diagnostics["peak_candidates"] = peak_diagnostics

    knee_frame: PathFrameEvidence | None = None
    knee_diagnostics: list[dict[str, Any]] = []
    if policy.knee_enabled:
        knee_frame, knee_diagnostics = _resolve_knee_candidate(
            energy_segments,
            positions,
            policy,
        )
    anchor_type = "curvature_knee"
    if knee_frame is None and peak_anchors:
        _prominence, knee_frame = max(
            peak_anchors,
            key=lambda item: (item[0], -int(item[1].frame_index)),
        )
        anchor_type = "peak_knee_anchor"
        knee_diagnostics.append(
            {
                "frame_index": int(knee_frame.frame_index),
                "accepted": True,
                "anchor_type": anchor_type,
            }
        )
    diagnostics["knee_candidates"] = knee_diagnostics
    if knee_frame is None:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_valid_knee_point",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
        )

    effective_endpoint_index = endpoint_evidence.get("effective_endpoint_index")
    effective_endpoint = next(
        (frame for frame in valid_frames if int(frame.frame_index) == effective_endpoint_index),
        None,
    )
    if effective_endpoint is None:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_effective_endpoint",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
            knee_evidence={
                "frame_index": int(knee_frame.frame_index),
                "anchor_type": anchor_type,
            },
        )

    knee_position = float(positions[int(knee_frame.frame_index)])
    endpoint_position = float(positions[int(effective_endpoint.frame_index)])
    span = endpoint_position - knee_position
    if span <= 1.0e-12:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_right_of_knee_seed_region",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
            knee_evidence={
                "frame_index": int(knee_frame.frame_index),
                "anchor_type": anchor_type,
            },
        )

    requested_shift = (
        float(policy.ts_right_shift_override_A)
        if policy.ts_right_shift_override_A is not None
        else max(
            float(policy.ts_right_shift_base_A),
            float(policy.ts_right_shift_span_fraction) * span,
        )
    )
    shift = min(
        float(policy.ts_right_shift_max_A),
        max(float(policy.ts_right_shift_min_A), requested_shift),
    )
    target_position = min(knee_position + shift, endpoint_position)
    ts_candidates = [
        frame
        for frame in base_candidates
        if float(positions[int(frame.frame_index)]) > knee_position
    ]
    if not ts_candidates:
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="none",
            rejection_reason="no_right_of_knee_seed_region",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
            knee_evidence={
                "frame_index": int(knee_frame.frame_index),
                "anchor_type": anchor_type,
            },
        )
    ts_frame = min(
        ts_candidates,
        key=lambda frame: (
            abs(float(positions[int(frame.frame_index)]) - target_position),
            0 if float(positions[int(frame.frame_index)]) >= target_position else 1,
            -float(positions[int(frame.frame_index)]),
        ),
    )
    ts_position = float(positions[int(ts_frame.frame_index)])
    diagnostics.update(
        {
            "knee_frame_index": int(knee_frame.frame_index),
            "knee_anchor_type": anchor_type,
            "knee_coordinate_A": knee_position,
            "effective_endpoint_coordinate_A": endpoint_position,
            "knee_to_endpoint_span_A": span,
            "ts_right_shift_requested_A": requested_shift,
            "ts_right_shift_source": (
                "feedback_override"
                if policy.ts_right_shift_override_A is not None
                else "profile_span_adaptive"
            ),
            "ts_right_shift_applied_A": max(0.0, ts_position - knee_position),
            "ts_target_coordinate_A": target_position,
            "ts_frame_index": int(ts_frame.frame_index),
            "energy_peak_index": int(knee_frame.frame_index),
        }
    )
    knee_evidence = {
        "frame_index": int(knee_frame.frame_index),
        "coordinate_A": knee_position,
        "anchor_type": anchor_type,
        "right_shift_A": max(0.0, ts_position - knee_position),
    }

    barrier = _barrier_from_reactant(profile, ts_frame)
    diagnostics["barrier_from_reactant_kcal_mol"] = barrier
    diagnostics["barrier_gate"] = {
        "accepted": barrier is not None and barrier >= policy.ts_min_reactant_barrier_kcal_mol,
        "required_for_search_seed": bool(policy.require_barrier_for_search_seed),
        "minimum_kcal_mol": float(policy.ts_min_reactant_barrier_kcal_mol),
    }
    diagnostics["ts_asynchronicity_A"] = _asynchronicity(ts_frame)
    if policy.require_barrier_for_search_seed and (
        barrier is None or barrier < policy.ts_min_reactant_barrier_kcal_mol
    ):
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="knee_shifted",
            rejection_reason="insufficient_barrier",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
            knee_evidence=knee_evidence,
        )

    ts_gate = _scaffold_gate(profile, ts_frame, policy)
    diagnostics["ts_scaffold_gate"] = ts_gate
    if policy.require_scaffold_for_search_seed and not bool(ts_gate.get("accepted", False)):
        return SeedSelection(
            s2_state="unresolved",
            seed_evidence="knee_shifted",
            rejection_reason="scaffold_rmsd",
            diagnostics=diagnostics,
            endpoint_evidence=endpoint_evidence,
            knee_evidence=knee_evidence,
        )

    ts_confidence = "high" if anchor_type == "peak_knee_anchor" else "medium"
    if barrier is None or barrier < policy.ts_min_reactant_barrier_kcal_mol:
        ts_confidence = "low" if anchor_type != "peak_knee_anchor" else "medium"
    ts_seed = _make_ts_seed(ts_frame, ts_confidence)
    ts_seed.update(
        {
            "selection_mode": "knee_shifted_ts",
            "knee_frame_index": int(knee_frame.frame_index),
            "right_shift_A": max(0.0, ts_position - knee_position),
            "stationary_point_claimed": False,
        }
    )

    int_frame: PathFrameEvidence | None = None
    int_seed: dict[str, Any] | None = None
    int_candidates = [
        frame
        for frame in valid_frames
        if float(positions[int(frame.frame_index)]) >= ts_position
        and float(positions[int(frame.frame_index)]) <= endpoint_position
    ]
    if policy.int_seed_mode == "ts_to_effective_endpoint_midpoint" and int_candidates:
        plateau_frame = _detect_stretch_plateau(
            int_candidates,
            positions,
            ts_position,
            endpoint_position,
            min_consecutive_frames=max(
                3,
                int(getattr(policy, "int_plateau_min_consecutive_frames", 3) or 3),
            ),
            energy_window_kcal_mol=float(
                getattr(policy, "int_plateau_energy_window_kcal_mol", 2.0) or 2.0
            ),
            min_ts_separation_A=float(
                getattr(policy, "int_plateau_min_ts_separation_A", 0.10) or 0.10
            ),
            max_slope_kcal_mol_per_A=float(
                getattr(policy, "int_plateau_max_slope_kcal_mol_A", 40.0) or 40.0
            ),
        )
        if plateau_frame is not None:
            int_frame = plateau_frame
            int_seed = _make_int_seed(
                int_frame,
                shared_with_ts=False,
                selection_mode="stretch_plateau",
            )
            diagnostics.update(
                {
                    "int_frame_index": int(int_frame.frame_index),
                    "int_selection_mode": "stretch_plateau",
                }
            )
        else:
            ts_vector = _coordinate_vector(ts_frame)
            endpoint_vector = _coordinate_vector(effective_endpoint)
            if len(ts_vector) == len(endpoint_vector):
                target_vector = tuple(
                    float(left) + 0.5 * (float(right) - float(left))
                    for left, right in zip(ts_vector, endpoint_vector)
                )

                def midpoint_distance(frame: PathFrameEvidence) -> float:
                    vector = _coordinate_vector(frame)
                    if len(vector) != len(target_vector):
                        return float("inf")
                    return math.sqrt(
                        sum(
                            (float(value) - float(target)) ** 2
                            for value, target in zip(vector, target_vector)
                        )
                    )

                int_frame = min(int_candidates, key=midpoint_distance)
                if int_frame.frame_index == ts_frame.frame_index and len(int_candidates) > 1:
                    int_frame = int_candidates[1]
                int_seed = _make_int_seed(
                    int_frame,
                    shared_with_ts=False,
                    selection_mode="ts_to_effective_endpoint_midpoint",
                )
                diagnostics.update(
                    {
                        "int_frame_index": int(int_frame.frame_index),
                        "int_selection_mode": "ts_to_effective_endpoint_midpoint",
                        "int_target_coordinate_A": float(0.5 * (ts_position + endpoint_position)),
                    }
                )
        diagnostics["int_asynchronicity_A"] = _asynchronicity(int_frame)
        int_gate = _scaffold_gate(profile, int_frame, policy)
        diagnostics["int_scaffold_gate"] = int_gate
        if policy.require_scaffold_for_search_seed and not bool(int_gate.get("accepted", False)):
            return SeedSelection(
                s2_state="unresolved",
                seed_evidence="knee_shifted",
                rejection_reason="scaffold_rmsd",
                diagnostics=diagnostics,
                endpoint_evidence=endpoint_evidence,
                knee_evidence=knee_evidence,
            )

    if int_seed is None and policy.allow_shared_search_seed:
        int_seed = _make_int_seed(
            ts_frame,
            shared_with_ts=True,
            selection_mode="shared_ts_fallback",
        )
        diagnostics["int_selection_mode"] = "shared_ts_fallback"

    s2_state = "rescue_seeded" if profile.source == "orca_relaxed_scan" else "path_seeded"
    return SeedSelection(
        s2_state=s2_state,
        seed_evidence="peak_knee_shifted" if anchor_type == "peak_knee_anchor" else "knee_shifted",
        ts_search_seed=ts_seed,
        int_search_seed=int_seed,
        has_independent_int=bool(
            int_seed is not None and str(int_seed.get("selection_mode") or "") == "stretch_plateau"
        ),
        rejection_reason=None,
        diagnostics=diagnostics,
        endpoint_evidence=endpoint_evidence,
        knee_evidence=knee_evidence,
    )


def replay_rescue_selection(
    rescue_payload: Mapping[str, Any],
    *,
    forming_bonds: Sequence[tuple[int, int]],
    product_xyz: Path,
    selection_config: Mapping[str, Any] | None = None,
    scan_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rescue = dict(rescue_payload.get("rescue") or rescue_payload)
    frames = tuple(Path(str(value)) for value in rescue.get("frames") or [])
    energies = list(rescue.get("energies_hartree") or [])
    if not frames or len(frames) != len(energies):
        return {
            "mode": "offline_replay",
            "algorithm": "endpoint_knee_shift_midpoint_v1",
            "s2_state": "unresolved",
            "seed_evidence": "none",
            "rejection_reason": "incomplete_rescue_profile",
            "ts_search_seed": None,
            "int_search_seed": None,
            "endpoint_evidence": None,
            "knee_evidence": None,
            "diagnostics": {"complete": False},
        }
    profile = build_orca_scan_profile(
        frames=frames,
        energies_hartree=energies,
        forming_bonds=forming_bonds,
        product_xyz=Path(product_xyz),
        energy_source="B97-3c",
        source_provenance={"mode": "offline_replay"},
    )
    selection = select_path_seeds(
        profile,
        policy_from_config(selection_config or {}, scan_config or {}),
    )
    result = selection.to_dict()
    result.update(
        {
            "mode": "offline_replay",
            "algorithm": str(
                selection.diagnostics.get(
                    "selection_algorithm",
                    "endpoint_knee_shift_midpoint_v1",
                )
            ),
            "selection_source": "orca_relaxed_scan",
            "ts_xyz": (
                None
                if selection.ts_search_seed is None
                else str(profile.frames[int(selection.ts_search_seed["frame_index"])].xyz)
            ),
            "int_xyz": (
                None
                if selection.int_search_seed is None
                else str(profile.frames[int(selection.int_search_seed["frame_index"])].xyz)
            ),
            "s3_dispatch": {
                "resolution": selection.s2_state,
                "submit_ts": selection.ts_search_seed is not None,
                "submit_intermediate": selection.int_search_seed is not None,
                "neb_eligible": False,
                "source": "b973c_relaxed_scan",
            },
        }
    )
    return result


__all__ = [
    "SeedSelection",
    "SelectionPolicy",
    "policy_from_config",
    "replay_rescue_selection",
    "select_path_seeds",
]
