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
import re
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
FRAME_CANDIDATES_SCHEMA_V1 = "frame_candidates_v1"
FRAME_CANDIDATES_SCHEMA = "frame_candidates_v2"

_ROLE_TOKEN_MAP = {"TS": "ts", "INT": "int", "NONE": "none"}
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class RevisionConflictError(FrameCandidateError):
    """The save/remove was attempted against a stale revision (concurrent edit)."""


def _sanitize_item_id(item_id: str) -> str:
    """Sanitize item_id to alnum/underscore/dash only (slugify)."""
    return _SLUG_RE.sub("_", item_id).strip("_") or "item"


def load_authority(task_root: Path) -> dict[str, Any] | None:
    """Read ``RESULT/frame_candidates.json``; ``None`` when missing or corrupt.

    Accepts both v1 and v2 schemas.  When a v1 file is read, the payload is
    migrated in memory (NOT rewritten to disk): each candidate gains
    ``item_id=None``, ``created_seq`` (1-based file order), and
    ``role_index`` (numbered per (role, item_id) ordered by (saved_at,
    frame_index)).  ``display_label`` is set to ``f"{role}{role_index}"``.
    The schema_version is upgraded to v2 in the returned dict; the next
    ``write_authority`` call persists v2.
    """
    path = task_root / FRAME_CANDIDATES_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    schema = str(payload.get("schema_version") or "")
    if schema == FRAME_CANDIDATES_SCHEMA_V1:
        return _migrate_v1_to_v2(payload)
    return payload


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1 authority payload to v2 shape (in-memory only)."""
    candidates = list(payload.get("candidates") or [])
    # Assign created_seq in file order (1-based)
    for seq, candidate in enumerate(candidates, start=1):
        candidate.setdefault("item_id", None)
        candidate.setdefault("created_seq", seq)
    # Compute role_index per (role, item_id) ordered by (saved_at, frame_index)
    from collections import defaultdict

    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        role = str(candidate.get("role") or "TS").upper()
        item_id = candidate.get("item_id")
        groups[(role, item_id)].append(candidate)
    for (role, _item_id), group in groups.items():
        group.sort(
            key=lambda c: (
                str(c.get("saved_at") or ""),
                int(c.get("frame_index") or 0),
            )
        )
        for idx, candidate in enumerate(group, start=1):
            candidate.setdefault("role_index", idx)
            candidate.setdefault("display_label", f"{role}{idx}")
    payload["schema_version"] = FRAME_CANDIDATES_SCHEMA
    return payload


def candidate_id_for(
    prefix: str,
    frame_index: int,
    item_id: str | None = None,
) -> str:
    """Deterministic candidate id (v2): role-free geometric identity.

    Format: ``{prefix}_frame_{frame_index:04d}`` when no item_id,
    ``{prefix}_{item_id}_frame_{frame_index:04d}`` when item_id present.
    """
    if item_id is not None:
        safe = _sanitize_item_id(str(item_id))
        return f"{prefix}_{safe}_frame_{frame_index:04d}"
    return f"{prefix}_frame_{frame_index:04d}"


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
