"""Authority file and persistence helpers for frame candidates.

Low-level operations for reading, writing, and deleting the
``RESULT/frame_candidates.json`` authority file, plus the atomic-write
primitive and the XYZ comment rewrite used when materialising structure
files.  The higher-level ``save_frame_candidate`` / ``list_frame_candidates``
/ ``remove_frame_candidate`` entry points live in :mod:`frame_candidates`
and consume these helpers.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from acp.results.frame_candidate_geometry import FrameCandidateError

logger = logging.getLogger(__name__)

__all__ = [
    "FRAME_CANDIDATES_RELATIVE_PATH",
    "FRAME_CANDIDATES_SCHEMA",
    "RevisionConflictError",
    "candidate_id_for",
    "delete_authority",
    "load_authority",
    "rewrite_xyz_comment",
    "write_authority",
]

FRAME_CANDIDATES_RELATIVE_PATH = "RESULT/frame_candidates.json"
FRAME_CANDIDATES_SCHEMA = "frame_candidates_v1"

_ROLE_TOKEN_MAP = {"TS": "ts", "INT": "int", "NONE": "none"}


class RevisionConflictError(FrameCandidateError):
    """The save/remove was attempted against a stale revision (concurrent edit)."""


def load_authority(task_root: Path) -> dict[str, Any] | None:
    """Read ``RESULT/frame_candidates.json``; ``None`` when missing or corrupt."""
    path = task_root / FRAME_CANDIDATES_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def candidate_id_for(prefix: str, role: str, frame_index: int) -> str:
    """Deterministic candidate id: ``scan_ts_frame_000`` / ``opt_int_frame_003``."""
    token = _ROLE_TOKEN_MAP.get(role, "none")
    return f"{prefix}_{token}_frame_{frame_index:03d}"


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write *text* to *path* via a temp file + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def write_authority(task_root: Path, payload: dict[str, Any]) -> None:
    """Atomically write the authority file."""
    atomic_write_text(
        task_root / FRAME_CANDIDATES_RELATIVE_PATH,
        json.dumps(payload, indent=2, sort_keys=True, default=str),
    )


def delete_authority(task_root: Path) -> None:
    """Remove the authority file if it exists."""
    path = task_root / FRAME_CANDIDATES_RELATIVE_PATH
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def rewrite_xyz_comment(xyz_text: str, comment: str) -> str:
    """Replace the second line (comment) of an XYZ block."""
    lines = xyz_text.strip().splitlines()
    if not lines:
        return xyz_text
    try:
        count = int(lines[0].strip())
    except ValueError:
        return xyz_text
    if len(lines) < count + 1:
        return xyz_text
    return "\n".join([lines[0], comment, *lines[2 : count + 2]]) + "\n"
