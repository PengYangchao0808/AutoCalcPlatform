"""Canonical PESsearch profile reader with legacy S2 compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from acp.calculations.pes.outputs import PES_PROFILE_RELATIVE_PATH

PES_PROFILE_SCHEMA = "pes_profile_v2"
LEGACY_S2_PROFILE_RELATIVE_PATH = "RESULT/mechanism/s2_path_manifest.json"

__all__ = [
    "LEGACY_S2_PROFILE_RELATIVE_PATH",
    "PES_PROFILE_RELATIVE_PATH",
    "PES_PROFILE_SCHEMA",
    "load_pes_profile",
    "normalize_pes_profile",
]


def normalize_pes_profile(
    payload: dict[str, Any],
    *,
    source_path: str | None = None,
    profile_path: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Adapt canonical and legacy PES/S2 payloads to one read projection.

    The returned shape intentionally keeps the historical API fields
    (``scan``, ``energy_profile``, and ``recommendations``), while canonical
    ``pes_profile_v2`` files may store those values at the top level.
    """
    if isinstance(payload.get("scan"), dict) and "energy_profile" in payload:
        normalized = dict(payload)
    else:
        frames = payload.get("frames")
        if not isinstance(frames, list):
            frames = []
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            profile = payload.get("energy_profile")
        if not isinstance(profile, dict):
            profile = {}
        quality = payload.get("quality")
        if not isinstance(quality, dict):
            quality = {}
        ts_candidates = payload.get("ts_candidates")
        if not isinstance(ts_candidates, list):
            ts_candidates = []
        int_candidates = payload.get("int_candidates")
        if not isinstance(int_candidates, list):
            int_candidates = []
        recommendations = payload.get("recommendations")
        if not isinstance(recommendations, dict):
            recommendations = {"ts": ts_candidates, "intermediates": int_candidates}
        protocol = payload.get("protocol")
        if not isinstance(protocol, dict):
            protocol = {}
        if "coordinate" not in protocol and isinstance(payload.get("coordinate"), dict):
            protocol = {"coordinate": payload["coordinate"], **protocol}
        normalized = {
            **payload,
            "workflow": str(payload.get("workflow") or "PESsearch"),
            "mode": str(payload.get("mode") or "bond_length_scan"),
            "status": str(payload.get("status") or quality.get("status") or "unknown"),
            "stationary_point_claimed": bool(payload.get("stationary_point_claimed", False)),
            "protocol": protocol,
            "coordinate": payload.get("coordinate") or protocol.get("coordinate") or {},
            "coordinates": payload.get("coordinates") or [],
            "selection": payload.get("selection") or {},
            "scan": {
                "scan_dir": str(payload.get("scan_dir") or ""),
                "frame_count": int(payload.get("frames_count") or len(frames)),
                "quality": quality,
                "frames": frames,
            },
            "energy_profile": profile,
            "recommendations": recommendations,
            "review": payload.get("review") if isinstance(payload.get("review"), dict) else {},
        }
    normalized["selection_mode"] = "manual_only"
    normalized["recommendations"] = {"ts": [], "intermediates": []}
    normalized["ts_candidates"] = []
    normalized["int_candidates"] = []
    normalized["selected_ts_id"] = None
    normalized["selected_int_id"] = None
    if source_path:
        normalized["_source_path"] = source_path
    if profile_path:
        normalized["_profile_path"] = str(profile_path)
    if work_dir:
        normalized["_work_dir"] = str(work_dir)
    scan_block = normalized.get("scan") or {}
    scan_frames = scan_block.get("frames")
    if isinstance(scan_frames, list):
        _backfill_path_coordinates(
            scan_frames,
            source_path=source_path,
            profile_path=profile_path or normalized.get("_profile_path"),
            work_dir=work_dir or normalized.get("_work_dir"),
            scan_dir=str(scan_block.get("scan_dir") or ""),
        )
    return normalized


def _backfill_path_coordinates(
    frames: list[dict[str, Any]],
    *,
    source_path: str | None = None,
    profile_path: Path | str | None = None,
    work_dir: Path | str | None = None,
    scan_dir: str | None = None,
) -> None:
    """Back-fill cumulative arc-length and progress from frame geometries on disk."""
    if len(frames) <= 1:
        return
    if all(frame.get("cumulative_arclength_A") is not None for frame in frames):
        return

    task_root: Path | None = None
    source_file: Path | None = None

    if work_dir is not None:
        task_root = Path(work_dir).resolve()

    target = profile_path or source_path
    if target is not None:
        p = Path(target).resolve()
        if p.is_file():
            source_file = p
        elif p.is_dir() and task_root is None:
            task_root = p

    if source_file is not None and task_root is None:
        if source_file.parent.name == "pes_search":
            task_root = source_file.parent.parent.parent
        elif source_file.parent.name == "RESULT":
            task_root = source_file.parent.parent
        else:
            task_root = source_file.parent

    search_dirs: list[Path] = []
    if source_file is not None:
        search_dirs.append(source_file.parent)
    if task_root is not None:
        if scan_dir:
            search_dirs.append(task_root / scan_dir / "scan_frames")
            search_dirs.append(task_root / scan_dir)
        search_dirs.append(task_root / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames")
        search_dirs.append(task_root / "WORK" / "07_PATH" / "pes_scan_001")
        search_dirs.append(task_root / "RESULT" / "structures")
        search_dirs.append(task_root / "RESULT" / "pes_search")
        search_dirs.append(task_root / "RESULT")
        search_dirs.append(task_root)

    if not search_dirs:
        return

    frame_paths: list[Path] = []
    for frame in frames:
        geom_path = str(frame.get("geometry_path") or "")
        found: Path | None = None
        if geom_path:
            p = Path(geom_path)
            if p.is_file():
                found = p
            else:
                for base in search_dirs:
                    cand = base / geom_path
                    if cand.is_file():
                        found = cand
                        break
                    cand2 = base / Path(geom_path).name
                    if cand2.is_file():
                        found = cand2
                        break
        if not found:
            idx = int(frame.get("index", 0))
            for base in search_dirs:
                for cand in (
                    base / f"frame_{idx:03d}.xyz",
                    base / f"frame_{idx}.xyz",
                    base / "scan_frames" / f"frame_{idx:03d}.xyz",
                ):
                    if cand.is_file():
                        found = cand
                        break
                if found:
                    break
        if found:
            frame_paths.append(found)
        else:
            break

    if len(frame_paths) == len(frames):
        try:
            from acp.calculations.pes.path_analysis import (
                _normalized_progress,
                compute_neighbor_rmsds,
                compute_path_arclength,
            )

            arclength = compute_path_arclength(frame_paths)
            progress = _normalized_progress(arclength)
            step_rmsds = compute_neighbor_rmsds(frame_paths)
            for i, frame in enumerate(frames):
                frame["cumulative_arclength_A"] = float(arclength[i])
                frame["reaction_progress"] = float(progress[i])
                frame["step_rmsd_A"] = float(step_rmsds[i]) if step_rmsds[i] is not None else None
        except (ValueError, TypeError, OSError, RuntimeError):
            pass


def load_pes_profile(path: Path | str, *, source_path: str | None = None) -> dict[str, Any]:
    """Read and normalize a canonical or legacy PES profile JSON file."""
    profile_path = Path(path).resolve()
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable PES profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"PES profile must be a JSON object: {profile_path}")
    return normalize_pes_profile(
        payload,
        source_path=source_path,
        profile_path=profile_path,
    )
