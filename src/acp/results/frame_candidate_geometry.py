"""Geometry resolvers for frame candidate materialization.

Each ``_resolve_*_geometry`` function reads the trajectory or manifest
appropriate for its ``view_type`` and returns the XYZ text of the requested
frame.  ``resolve_frame_geometry`` is the public dispatcher that selects the
correct resolver.

This module intentionally carries the path-escape guard (``_ensure_inside``)
and the ``FrameCandidateError`` base exception so that geometry resolution
is self-contained and testable without the persistence layer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "FrameCandidateError",
    "resolve_frame_geometry",
]

_VALID_VIEW_TYPES = frozenset({"optimization", "sampling", "scan", "conformer"})


class FrameCandidateError(ValueError):
    """A frame-candidate request failed validation."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _ensure_inside(task_root: Path, path: Path) -> Path:
    """Resolve *path* and refuse anything outside *task_root*."""
    resolved = path.resolve()
    try:
        resolved.relative_to(task_root)
    except ValueError:
        raise FrameCandidateError(f"structure path escapes the task directory: {path}") from None
    return resolved


# ---------------------------------------------------------------------------
# Per-view_type resolvers
# ---------------------------------------------------------------------------


def _resolve_scan_geometry(task_root: Path, frame_index: int) -> str:
    """Read scan frame xyz from ``RESULT/trajectories/scan_trajectory.json``."""
    traj_path = task_root / "RESULT" / "trajectories" / "scan_trajectory.json"
    if not traj_path.is_file():
        raise FrameCandidateError(f"scan trajectory not found: {traj_path}")
    try:
        payload = json.loads(traj_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FrameCandidateError(f"unreadable scan trajectory: {traj_path}") from exc
    frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list):
        raise FrameCandidateError("scan trajectory has no frames list")
    frame = next((f for f in frames if int(f.get("index", -1)) == frame_index), None)
    if frame is None:
        raise FrameCandidateError(
            f"scan frame {frame_index} not found (indices may be non-contiguous)"
        )
    rel_path = str(frame.get("path") or "")
    if not rel_path:
        raise FrameCandidateError(f"scan frame {frame_index} has no path")
    frame_path = _ensure_inside(task_root, task_root / "RESULT" / rel_path)
    if not frame_path.is_file():
        raise FrameCandidateError(f"scan frame geometry missing: {frame_path}")
    try:
        return frame_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FrameCandidateError(f"unreadable scan frame: {frame_path}") from exc


def _resolve_optimization_geometry(task_root: Path, frame_index: int) -> str:
    """Read optimization cycle geometry.

    Uses ``find_optimization_trajectory`` then resolves the cycle's
    ``geometry_ref`` relative to the trajectory file's parent directory,
    exactly as ``v1_routes.get_optimization_frame`` does.
    """
    from acp.results.energy_graph import find_optimization_trajectory

    traj_path, payload = find_optimization_trajectory(task_root, None)
    if traj_path is None or payload is None:
        raise FrameCandidateError("no optimization trajectory found")
    cycles = payload.get("cycles")
    if not isinstance(cycles, list) or frame_index < 0 or frame_index >= len(cycles):
        n_cycles = len(cycles) if isinstance(cycles, list) else 0
        raise FrameCandidateError(
            f"optimization frame {frame_index} out of range (trajectory has {n_cycles} cycles)"
        )
    geometry_ref = str(cycles[frame_index].get("geometry_ref") or "")
    if not geometry_ref:
        raise FrameCandidateError(f"optimization cycle {frame_index} has no geometry_ref")
    candidate_path = (traj_path.parent / geometry_ref).resolve()
    work_root = task_root.resolve()
    if not candidate_path.is_relative_to(work_root):
        raise FrameCandidateError(
            f"optimization geometry escapes the task directory: {geometry_ref}"
        )
    if not candidate_path.is_file():
        raise FrameCandidateError(f"optimization geometry missing: {candidate_path}")
    try:
        return candidate_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FrameCandidateError(f"unreadable optimization geometry: {candidate_path}") from exc


def _resolve_sampling_geometry(task_root: Path, frame_index: int) -> str:
    """Read sampling MD frame xyz from ``WORK/02_SEARCH/xTB/traj.xyz``."""
    from acp.confsearch.sampling_models import read_traj_frame_xyz

    traj_path = task_root / "WORK" / "02_SEARCH" / "xTB" / "traj.xyz"
    if not traj_path.is_file():
        raise FrameCandidateError(f"sampling trajectory not found: {traj_path}")
    xyz = read_traj_frame_xyz(traj_path, frame_index)
    if xyz is None:
        raise FrameCandidateError(f"sampling frame {frame_index} not found in trajectory")
    return xyz


def _resolve_conformer_geometry(task_root: Path, frame_index: int) -> str:
    """Read conformer geometry from confsearch manifest.

    ``frame_index`` is 0-based; the conformer at rank ``frame_index + 1`` is
    selected.
    """
    from acp.confsearch.manifest import (
        find_confsearch_manifest,
        read_manifest,
        resolve_manifest_geometry,
    )

    manifest_path = find_confsearch_manifest(task_root)
    if manifest_path is None:
        raise FrameCandidateError("confsearch manifest not found")
    try:
        payload = read_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        raise FrameCandidateError(f"unreadable confsearch manifest: {exc}") from exc
    conformers = payload.get("conformers") if isinstance(payload, dict) else None
    if not isinstance(conformers, list) or not conformers:
        raise FrameCandidateError("confsearch manifest has no conformers")
    rank = frame_index + 1
    entry = next((c for c in conformers if int(c.get("rank", 0)) == rank), None)
    if entry is None:
        raise FrameCandidateError(f"conformer at rank {rank} (frame_index={frame_index}) not found")
    geometry_ref = str(entry.get("geometry") or "")
    if not geometry_ref:
        raise FrameCandidateError(f"conformer rank {rank} has no geometry reference")
    try:
        geometry_path = resolve_manifest_geometry(manifest_path, geometry_ref)
    except FileNotFoundError as exc:
        raise FrameCandidateError(str(exc)) from exc
    try:
        return geometry_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FrameCandidateError(f"unreadable conformer geometry: {geometry_path}") from exc


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def resolve_frame_geometry(
    task_root: Path,
    *,
    view_type: str,
    frame_index: int,
    workflow: str,
) -> str:
    """Return the XYZ text for one trajectory frame.

    Args:
        task_root: Job working directory.
        view_type: One of ``scan``, ``optimization``, ``sampling``,
            ``conformer``.
        frame_index: 0-based frame index (indices may be non-contiguous
            for scan).
        workflow: Workflow name (used only for error context).

    Returns:
        XYZ text of the requested frame.

    Raises:
        FrameCandidateError: Unknown view_type, missing frame, or
            missing file.
    """
    root = Path(task_root).expanduser().resolve()
    if view_type not in _VALID_VIEW_TYPES:
        raise FrameCandidateError(
            f"unknown view_type {view_type!r} "
            f"(expected one of {', '.join(sorted(_VALID_VIEW_TYPES))})"
        )
    resolvers = {
        "scan": _resolve_scan_geometry,
        "optimization": _resolve_optimization_geometry,
        "sampling": _resolve_sampling_geometry,
        "conformer": _resolve_conformer_geometry,
    }
    return resolvers[view_type](root, frame_index)
