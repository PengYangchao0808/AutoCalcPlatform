"""Sampling energy projection for conformer-search MD trajectories.

Builds the normalized energy-graph projection for the "sampling" view,
loading ``RESULT/confsearch/sampling_history.json`` (schema
``sampling_history_v1``) produced by the Confsearch engine finalize hook
(Todo 7).  The projection presents energy-vs-time series with basin
annotations and MDS metadata so the frontend can render trajectory,
sampling-space, and coverage subviews.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from acp.confsearch.sampling_models import SamplingHistory, load_sampling_history
from acp.results.frames import VIEW_REGISTRY, TrajectoryAnnotation, TrajectoryFrame

logger = logging.getLogger(__name__)

__all__ = [
    "build_sampling_energy_graph",
    "has_sampling_history",
]


def has_sampling_history(work_dir: Path) -> bool:
    """Return ``True`` when a valid sampling history file exists."""
    return load_sampling_history(work_dir) is not None


def build_sampling_energy_graph(
    job_id: str,
    work_dir: Path,
) -> dict[str, Any] | None:
    """Build the sampling energy projection for a Confsearch job.

    Returns ``None`` when the sampling history file is missing or corrupt
    (callers should fall back to the conformer view).
    """
    history = load_sampling_history(work_dir)
    if history is None:
        return None
    return _build_from_history(job_id, work_dir, history)


def _build_from_history(
    job_id: str,
    work_dir: Path,
    history: SamplingHistory,
) -> dict[str, Any]:
    spec = VIEW_REGISTRY["sampling"]
    frames = history.frames
    basin_ids = history.basin_ids
    is_new = history.is_new_basin
    mds_coords = history.mds_coords

    # Compute relative energies from the persisted energy_kcal_mol values
    energies = [f.energy_kcal_mol for f in frames]
    finite_energies = [e for e in energies if e is not None]
    min_energy = min(finite_energies) if finite_energies else None

    def _relative(e: float | None) -> float | None:
        if e is None or min_energy is None:
            return None
        return e - min_energy

    relative = [_relative(e) for e in energies]

    # Build series
    potential_values = energies
    relative_values = relative
    series: list[dict[str, Any]] = []
    for series_id, label, unit, values in (
        ("energy_potential", "势能", "kcal/mol", potential_values),
        ("relative_energy", "相对能量", "kcal/mol", relative_values),
    ):
        if any(v is not None for v in values):
            series.append(
                {
                    "id": series_id,
                    "label": label,
                    "unit": unit,
                    "axis": "left",
                    "values": values,
                }
            )

    # Build nodes
    nodes: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        bid = basin_ids[index] if index < len(basin_ids) else 0
        is_new_flag = is_new[index] if index < len(is_new) else False
        mds_pair = mds_coords[index] if index < len(mds_coords) else (None, None)
        node_metadata: dict[str, Any] = {
            "energy_kcal_mol": frame.energy_kcal_mol,
            "step": frame.step,
            "is_new_basin": is_new_flag,
            "mds": [_safe_float(mds_pair[0]), _safe_float(mds_pair[1])],
        }
        nodes.append(
            TrajectoryFrame(
                frame_id=f"frame_{frame.index}",
                label=f"Frame {frame.index + 1}",
                frame_index=frame.index,
                x=frame.time_ps,
                energy=relative[index],
                status="completed" if frame.energy_kcal_mol is not None else "unknown",
                step=frame.step,
                time_ps=frame.time_ps,
                basin_id=bid,
                metadata=node_metadata,
            ).to_node(spec.node_type)
        )

    # Build annotations
    annotations: list[dict[str, Any]] = []
    # new_basin annotations — one per basin first-seen
    basin_first_seen: dict[int, dict[str, Any]] = {}
    for index, bid in enumerate(basin_ids):
        if bid not in basin_first_seen:
            basin_first_seen[bid] = nodes[index] if index < len(nodes) else {}
    for bid, node in sorted(basin_first_seen.items()):
        if not node:
            continue
        annotations.append(
            TrajectoryAnnotation(
                id=f"new_basin_{bid}",
                type="new_basin",
                label="新盆地",
                frame_index=node["frame_index"],
                x=node["x"],
                y=node["energy"],
                status=node["status"],
                geometry_ref=node["geometry_ref"],
            ).to_annotation()
        )
    # minimum annotation
    finite_nodes = [node for node in nodes if node.get("energy") is not None]
    if finite_nodes:
        minimum = min(finite_nodes, key=lambda node: float(node["energy"]))
        annotations.append(
            TrajectoryAnnotation(
                id=f"minimum_{minimum['frame_index']}",
                type="minimum",
                label="最低能量",
                frame_index=minimum["frame_index"],
                x=minimum["x"],
                y=minimum["energy"],
                status=minimum["status"],
                geometry_ref=minimum["geometry_ref"],
            ).to_annotation()
        )

    # Metadata
    sat = history.saturation
    metadata: dict[str, Any] = {
        "basins": [
            {
                "basin_id": b.basin_id,
                "first_seen_index": b.first_seen_index,
                "first_seen_ps": b.first_seen_ps,
                "visit_count": b.visit_count,
                "min_energy": b.min_energy,
            }
            for b in history.basins
        ],
        "saturation": {
            "unique_clusters": sat.unique_clusters,
            "new_clusters_last_20pct": sat.new_clusters_last_20pct,
            "last_new_basin_ps": sat.last_new_basin_ps,
            "revisit_ratio": sat.revisit_ratio,
            "energy_window_kcal_mol": sat.energy_window_kcal_mol,
            "level": sat.level,
        },
        "cumulative_unique": sat.cumulative_unique,
        "axis_options": {
            "x": ["time_ps", "step", "frame"],
            "y": ["potential", "relative"],
        },
        "frame_count": len(nodes),
        "n_frames_raw": history.n_frames_raw,
        "subsampled": history.subsampled,
        "subsample_stride": history.subsample_stride,
    }

    return {
        "job_id": job_id,
        "view_type": "sampling",
        "title": spec.title_zh,
        "status": "completed",
        "complete": True,
        "revision": "",
        "default_series": "energy_potential",
        "available_views": ["sampling"],
        "x_axis": {"label": "模拟时间", "unit": "ps"},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": "RESULT/confsearch/sampling_history.json",
        "provenance": {},
        "metadata": metadata,
    }


def _safe_float(value: float | None) -> float | None:
    """Return the float value or None — strips non-finite values."""
    import math

    if value is None:
        return None
    return value if math.isfinite(value) else None
