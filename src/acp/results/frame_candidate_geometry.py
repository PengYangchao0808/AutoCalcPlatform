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
import re
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "FrameCandidateError",
    "resolve_frame_geometry",
]

_VALID_VIEW_TYPES = frozenset({"optimization", "sampling", "scan", "conformer", "irc"})
_IRC_FRAME_ID_RE = re.compile(r"^irc_(forward|reverse)_(\d+)$")


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


def _resolve_optimization_geometry(
    task_root: Path,
    frame_index: int,
    *,
    item_id: str | None = None,
) -> str:
    """Read optimization cycle geometry.

    Uses ``find_optimization_trajectory`` then resolves the cycle's
    ``geometry_ref`` relative to the trajectory file's parent directory,
    exactly as ``v1_routes.get_optimization_frame`` does.
    """
    from acp.results.energy_graph import find_optimization_trajectory

    traj_path, payload = find_optimization_trajectory(task_root, item_id)
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
    if not geometry_path.resolve().is_relative_to(task_root.resolve()):
        raise FrameCandidateError(f"conformer geometry escapes the task directory: {geometry_ref}")
    try:
        return geometry_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FrameCandidateError(f"unreadable conformer geometry: {geometry_path}") from exc


# ---------------------------------------------------------------------------
# IRC resolver
# ---------------------------------------------------------------------------


def _irc_direction_from_frame_id(frame_id: str | None) -> str | None:
    match = _IRC_FRAME_ID_RE.match(str(frame_id or ""))
    return match.group(1) if match else None


def _xyz_block_text(path: Path, frame_index: int) -> str | None:
    """Return the raw text of the frame_index-th complete XYZ block."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    offset = 0
    index = 0
    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            offset += 1
            continue
        if atom_count <= 0:
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            break
        if index == frame_index:
            return "\n".join(lines[offset:end]) + "\n"
        index += 1
        offset = end
    return None


def _work_irc_frame_text(task_root: Path, direction: str, frame_index: int) -> str | None:
    """Read one point from historical ORCA trajectories under ``WORK/*/ORCA``."""
    from cccp.qc.interfaces.orca_ts import (
        discover_irc_trajectory_files,
        parse_irc_trajectory_xyz,
    )

    orca_dirs = [task_root / "WORK" / "07_PATH" / "ORCA"]
    orca_dirs.extend(path for path in task_root.glob("WORK/*/ORCA") if path.is_dir())
    for orca_dir in orca_dirs:
        if not orca_dir.is_dir():
            continue
        files = discover_irc_trajectory_files(orca_dir)
        trajectory = files.get(direction)
        if trajectory is None:
            continue
        for point in parse_irc_trajectory_xyz(trajectory, direction):
            if point.index != frame_index or point.coordinates is None:
                continue
            rows = "\n".join(
                f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}"
                for symbol, coord in zip(point.symbols, point.coordinates)
            )
            return f"{len(point.symbols)}\nIRC {direction} point {point.index}\n{rows}\n"
    return None


def _resolve_irc_geometry(
    task_root: Path,
    frame_index: int,
    *,
    frame_id: str | None = None,
) -> str:
    """Resolve one IRC path point, disambiguated by the energy-node id.

    Forward and reverse trajectories share frame indexes, so the direction is
    taken from *frame_id* (``irc_{forward|reverse}_{index}``).  Resolution
    order: persisted ``irc_trajectory_v1`` per-point geometry, per-point
    RESULT file, multi-frame path/endpoint slice, then historical ORCA
    trajectories under ``WORK``.
    """
    direction = _irc_direction_from_frame_id(frame_id)
    trajectory_path = task_root / "RESULT" / "trajectories" / "irc_trajectory.json"
    payload: dict[str, object] | None = None
    if trajectory_path.is_file():
        try:
            loaded = json.loads(trajectory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            payload = loaded
    frames = payload.get("frames") if payload is not None else None
    if isinstance(frames, list):
        matches: list[dict[str, object]] = []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            try:
                index_value = int(frame.get("frame_index", frame.get("index", -1)))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if index_value != frame_index:
                continue
            if direction is not None and str(frame.get("direction")) != direction:
                continue
            matches.append(frame)
        if len(matches) == 1:
            reference = str(matches[0].get("geometry_ref") or "")
            if reference:
                target = _ensure_inside(task_root, task_root / reference)
                if target.is_file():
                    try:
                        return target.read_text(encoding="utf-8", errors="replace")
                    except OSError as exc:
                        raise FrameCandidateError(f"unreadable IRC geometry: {target}") from exc
        elif len(matches) > 1:
            raise FrameCandidateError(
                "IRC frame is ambiguous: pass frame_id "
                "(irc_{forward|reverse}_{index}) to pick a direction"
            )
    if direction is None:
        raise FrameCandidateError(
            "IRC frame requires a direction: pass frame_id (irc_{forward|reverse}_{index})"
        )
    point_file = task_root / "RESULT" / "irc" / f"irc_{direction}_point_{frame_index:04d}.xyz"
    if point_file.is_file():
        try:
            return point_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FrameCandidateError(f"unreadable IRC geometry: {point_file}") from exc
    for name in (f"irc_{direction}_path.xyz", f"irc_{direction}.xyz"):
        candidate = task_root / "RESULT" / "irc" / name
        if candidate.is_file():
            text = _xyz_block_text(candidate, frame_index)
            if text is not None:
                return text
    work_text = _work_irc_frame_text(task_root, direction, frame_index)
    if work_text is not None:
        return work_text
    raise FrameCandidateError(f"IRC {direction} frame {frame_index} not found")


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def resolve_frame_geometry(
    task_root: Path,
    *,
    view_type: str,
    frame_index: int,
    workflow: str,
    item_id: str | None = None,
    frame_id: str | None = None,
) -> str:
    """Return the XYZ text for one trajectory frame.

    Args:
        task_root: Job working directory.
        view_type: One of ``scan``, ``optimization``, ``sampling``,
            ``conformer``, ``irc``.
        frame_index: 0-based frame index (indices may be non-contiguous
            for scan).
        workflow: Workflow name (used only for error context).
        item_id: Optional BatchOptimize item identifier; when given,
            the optimization resolver narrows trajectory lookup to the
            specific item subdirectory.
        frame_id: Optional energy-node id (``irc_{forward|reverse}_{index}``)
            disambiguating IRC points that share a frame index across
            directions.

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
    if view_type == "optimization":
        return _resolve_optimization_geometry(root, frame_index, item_id=item_id)
    if view_type == "irc":
        return _resolve_irc_geometry(root, frame_index, frame_id=frame_id)
    resolvers = {
        "scan": _resolve_scan_geometry,
        "sampling": _resolve_sampling_geometry,
        "conformer": _resolve_conformer_geometry,
    }
    return resolvers[view_type](root, frame_index)
