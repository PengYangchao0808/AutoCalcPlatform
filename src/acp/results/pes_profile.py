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
    if source_path:
        normalized["_source_path"] = source_path
    return normalized


def load_pes_profile(path: Path | str, *, source_path: str | None = None) -> dict[str, Any]:
    """Read and normalize a canonical or legacy PES profile JSON file."""
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unreadable PES profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"PES profile must be a JSON object: {profile_path}")
    return normalize_pes_profile(payload, source_path=source_path)
