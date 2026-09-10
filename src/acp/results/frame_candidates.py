"""Frame candidate materialization — save ANY energy-viewer frame as a tagged structure.

After viewing a trajectory frame in the Energy & Trajectory viewer, the user
may promote it to a reusable candidate structure.  This module is the single
writer for the frame-candidate artifacts:

- ``RESULT/frame_candidates.json`` — authoritative candidate record
  (schema ``frame_candidates_v2``) with a monotonic ``revision`` counter.
- ``RESULT/structures/<candidate_id>.xyz`` — one materialised XYZ per
  saved frame, with a rewritten TAG comment carrying the stable
  ``candidate_id`` and ``selection_source=manual_frame``.
- ``RESULT/result_manifest.json`` — structure products are registered so
  the unified new-task flow discovers them automatically.

All candidates are validated before anything is written (all-or-nothing).
Re-saving the same frame is idempotent: candidate ids are derived
deterministically from ``view_type + frame_index + item_id`` (role is
mutable metadata, not part of the identity).

PESsearch jobs are rejected with guidance to use the dedicated
``/pes/review`` endpoint.

Geometry resolution lives in :mod:`frame_candidate_geometry`; authority-file
persistence lives in :mod:`frame_candidate_store`.  This module re-exports
all public symbols for backward compatibility.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from acp.calculations.batch._tag import build_tag_title, normalize_tag
from acp.results.frame_candidate_geometry import (  # noqa: F401 — re-export
    FrameCandidateError,
    resolve_frame_geometry,
)
from acp.results.frame_candidate_store import (  # noqa: F401 — re-export
    FRAME_CANDIDATES_RELATIVE_PATH,
    FRAME_CANDIDATES_SCHEMA,
    RevisionConflictError,
    atomic_write_text,
    candidate_id_for,
    delete_authority,
    load_authority,
    rewrite_xyz_comment,
    write_authority,
)
from acp.storage.manifest import ProductKind, ResultManifest

logger = logging.getLogger(__name__)

__all__ = [
    "FRAME_CANDIDATES_RELATIVE_PATH",
    "FRAME_CANDIDATES_SCHEMA",
    "FrameCandidateError",
    "RevisionConflictError",
    "list_frame_candidates",
    "remove_frame_candidate",
    "resolve_frame_geometry",
    "save_frame_candidate",
]

_VALID_ROLES = frozenset({"TS", "INT"})
_PREFIX_MAP = {
    "optimization": "opt",
    "sampling": "md",
    "scan": "scan",
    "conformer": "conf",
}


# ---------------------------------------------------------------------------
# save / list / remove
# ---------------------------------------------------------------------------


def save_frame_candidate(
    task_root: Path | str,
    *,
    job_id: str,
    workflow: str,
    view_type: str,
    frame_index: int,
    role: str,
    name: str | None = None,
    expected_revision: int | None = None,
    now: datetime | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Validate and persist a frame candidate (all-or-nothing).

    Args:
        task_root: Job working directory.
        job_id: Owning scheduler job id (recorded for audit).
        workflow: Workflow name (e.g. ``scan``, ``optimize``, ``Confsearch``).
        view_type: One of ``scan``, ``optimization``, ``sampling``,
            ``conformer``.
        frame_index: 0-based frame index.
        role: One of ``TS``, ``INT``.  Re-saving the same frame with the
            other role performs an in-place role change (candidate_id
            and created_seq are preserved).
        name: Optional display name; defaults to the display_label.
        expected_revision: When given, the currently stored revision must
            match.
        now: Injectable timestamp (tests); defaults to local time now.
        item_id: Optional BatchOptimize item identifier; when given,
            narrows optimization trajectory lookup to the specific item.

    Returns:
        The saved candidate entry dict.

    Raises:
        FrameCandidateError: Validation failure; nothing is written.
        RevisionConflictError: ``expected_revision`` does not match the
            stored one.
    """
    root = Path(task_root).expanduser().resolve()

    # --- Validate workflow ---
    if workflow == "PESsearch":
        raise FrameCandidateError(
            "PESsearch jobs use the dedicated /pes/review endpoint; "
            "frame-candidate save is not available for PESsearch"
        )

    # --- Validate role ---
    effective_role = role.upper().strip()
    if effective_role not in _VALID_ROLES:
        raise FrameCandidateError(f"invalid candidate role: {role!r} (expected TS or INT)")
    normalized_role = normalize_tag(effective_role)

    # --- Check revision ---
    existing = load_authority(root)
    current_revision = int(existing.get("revision", 0)) if existing else 0
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RevisionConflictError(
            f"revision conflict: stored={current_revision}, expected={expected_revision}"
        )

    # --- Validate geometry FIRST (all-or-nothing: write NOTHING on failure) ---
    frame_xyz = resolve_frame_geometry(
        root,
        view_type=view_type,
        frame_index=frame_index,
        workflow=workflow,
        item_id=item_id,
    )

    # --- Build candidate_id (v2: no role in id) ---
    prefix = _PREFIX_MAP.get(view_type, "unknown")
    cid = candidate_id_for(prefix, frame_index, item_id=item_id)

    candidates_list = list(existing.get("candidates", [])) if existing else []

    # --- Find existing entry for same (item_id, view_type, frame_index) ---
    existing_entry = _find_matching_entry(candidates_list, item_id, view_type, frame_index)

    if existing_entry is not None:
        # Idempotent or role-change: update in place
        old_role = str(existing_entry.get("role") or "").upper()
        if effective_role == old_role:
            # Idempotent: keep role_index, created_seq, display_label
            existing_entry["name"] = str(name or existing_entry.get("display_label") or cid)
            existing_entry["saved_at"] = (now or datetime.now().astimezone()).isoformat(
                timespec="seconds"
            )
            existing_entry["structure_path"] = f"structures/{cid}.xyz"
        else:
            # Role change: keep candidate_id and created_seq, compute new role_index
            old_role_index = int(existing_entry.get("role_index") or 0)
            new_role_index = _next_role_index(candidates_list, effective_role, item_id)
            existing_entry["role"] = effective_role
            existing_entry["role_index"] = new_role_index
            display_label = f"{effective_role}{new_role_index}"
            existing_entry["display_label"] = display_label
            existing_entry["name"] = str(name or display_label)
            existing_entry["saved_at"] = (now or datetime.now().astimezone()).isoformat(
                timespec="seconds"
            )
            existing_entry["structure_path"] = f"structures/{cid}.xyz"
            logger.debug(
                "Role change for %s: %s%d → %s%d (candidate_id preserved)",
                cid,
                old_role,
                old_role_index,
                effective_role,
                new_role_index,
            )
        entry = existing_entry
        new_created_seq = int(entry.get("created_seq") or 1)
    else:
        # Genuinely new candidate
        role_index = _next_role_index(candidates_list, effective_role, item_id)
        display_label = f"{effective_role}{role_index}"
        max_seq = max((int(c.get("created_seq") or 0) for c in candidates_list), default=0)
        new_created_seq = max_seq + 1
        entry = {
            "candidate_id": cid,
            "view_type": view_type,
            "frame_index": frame_index,
            "role": effective_role,
            "role_index": role_index,
            "display_label": display_label,
            "item_id": item_id,
            "created_seq": new_created_seq,
            "name": str(name or display_label),
            "structure_path": f"structures/{cid}.xyz",
            "saved_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        }
        candidates_list.append(entry)

    # --- Build TAG comment line ---
    tag_role = normalized_role or "INT"
    role_idx = int(entry.get("role_index") or 1)
    display_label = str(
        entry.get("display_label") or f"{effective_role}{role_idx}"
    )
    tag_comment = build_tag_title(
        tag_role,
        candidate_id=cid,
        source=workflow,
        frame=frame_index,
        extra=f"candidate_label={display_label},selection_source=manual_frame",
    )

    # --- Write structures XYZ ---
    structures_dir = root / "RESULT" / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)
    target = structures_dir / f"{cid}.xyz"
    xyz_text = rewrite_xyz_comment(frame_xyz, tag_comment)
    atomic_write_text(target, xyz_text)

    # --- Update authority file ---
    new_revision = current_revision + 1
    authority_payload: dict[str, Any] = {
        "schema_version": FRAME_CANDIDATES_SCHEMA,
        "job_id": job_id,
        "revision": new_revision,
        "candidates": candidates_list,
    }
    write_authority(root, authority_payload)

    # --- Register in result_manifest.json ---
    result_dir = root / "RESULT"
    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.task_id = manifest.task_id or job_id
    product_id = f"frame_candidate_{cid}"
    manifest.add_product(
        id=product_id,
        label=f"{workflow} frame {frame_index} candidate ({effective_role})",
        path=f"structures/{cid}.xyz",
        kind=ProductKind.STRUCTURE,
        metadata={
            "candidate_id": cid,
            "role": effective_role,
            "frame_index": frame_index,
            "source": workflow,
            "selection_source": "manual_frame",
            "view_type": view_type,
            "display_label": display_label,
            "item_id": item_id,
        },
    )
    try:
        manifest.write(result_dir)
    except Exception:
        # Roll back authority file on manifest failure
        if existing:
            write_authority(root, existing)
        else:
            delete_authority(root)
        raise

    entry["revision"] = new_revision
    return entry


def _find_matching_entry(
    candidates_list: list[dict[str, Any]],
    item_id: str | None,
    view_type: str,
    frame_index: int,
) -> dict[str, Any] | None:
    """Find existing candidate matching (item_id, view_type, frame_index)."""
    for c in candidates_list:
        if (
            c.get("view_type") == view_type
            and c.get("frame_index") == frame_index
            and c.get("item_id") == item_id
        ):
            return c
    return None


def _next_role_index(
    candidates_list: list[dict[str, Any]],
    role: str,
    item_id: str | None,
) -> int:
    """Return the next role_index for (role, item_id): max existing + 1."""
    max_idx = 0
    for c in candidates_list:
        if c.get("role") == role and c.get("item_id") == item_id:
            idx = int(c.get("role_index") or 0)
            if idx > max_idx:
                max_idx = idx
    return max_idx + 1


def list_frame_candidates(task_root: Path | str) -> dict[str, Any]:
    """Return the current frame-candidates payload (None-safe).

    Args:
        task_root: Job working directory.

    Returns:
        The authority payload (``schema_version``, ``candidates``,
        ``revision``), or an empty payload with ``revision=0`` when
        missing/corrupt.  V1 files are migrated in memory.
    """
    root = Path(task_root).expanduser().resolve()
    existing = load_authority(root)
    if existing is None:
        return {
            "schema_version": FRAME_CANDIDATES_SCHEMA,
            "candidates": [],
            "revision": 0,
        }
    return existing


def remove_frame_candidate(
    task_root: Path | str,
    candidate_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Remove a candidate atomically; its XYZ file is kept on disk.

    No renumbering of remaining candidates occurs.
    """
    root = Path(task_root).expanduser().resolve()
    existing = load_authority(root)
    if existing is None:
        raise FrameCandidateError(f"candidate not found: {candidate_id}")

    current_revision = int(existing.get("revision", 0))
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RevisionConflictError(
            f"revision conflict: stored={current_revision}, expected={expected_revision}"
        )

    candidates_list = list(existing.get("candidates", []))
    if not any(c.get("candidate_id") == candidate_id for c in candidates_list):
        raise FrameCandidateError(f"candidate not found: {candidate_id}")

    # Remove from authority — no renumbering
    new_revision = current_revision + 1
    authority_payload: dict[str, Any] = {
        "schema_version": FRAME_CANDIDATES_SCHEMA,
        "job_id": existing.get("job_id", ""),
        "revision": new_revision,
        "candidates": [c for c in candidates_list if c.get("candidate_id") != candidate_id],
    }

    # Remove from manifest
    try:
        manifest = ResultManifest.read(root / "RESULT")
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = None
    else:
        manifest.products = [
            p for p in manifest.products if p.id != f"frame_candidate_{candidate_id}"
        ]

    authority_path = root / FRAME_CANDIDATES_RELATIVE_PATH
    previous_authority = authority_path.read_text(encoding="utf-8")
    write_authority(root, authority_payload)
    if manifest is not None:
        try:
            manifest.write(root / "RESULT")
        except (OSError, TypeError, ValueError, RuntimeError):
            atomic_write_text(authority_path, previous_authority)
            raise

    return authority_payload
