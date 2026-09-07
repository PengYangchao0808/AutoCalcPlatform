"""Frame candidate materialization — save ANY energy-viewer frame as a tagged structure.

After viewing a trajectory frame in the Energy & Trajectory viewer, the user
may promote it to a reusable candidate structure.  This module is the single
writer for the frame-candidate artifacts:

- ``RESULT/frame_candidates.json`` — authoritative candidate record
  (schema ``frame_candidates_v1``) with a monotonic ``revision`` counter.
- ``RESULT/structures/<candidate_id>.xyz`` — one materialised XYZ per
  saved frame, with a rewritten TAG comment carrying the stable
  ``candidate_id`` and ``selection_source=manual_frame``.
- ``RESULT/result_manifest.json`` — structure products are registered so
  the unified new-task flow discovers them automatically.

All candidates are validated before anything is written (all-or-nothing).
Re-saving the same frame is idempotent: candidate ids are derived
deterministically from ``view_type + role + frame_index``, so repeat saves
reuse the same files and manifest ids.

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

_VALID_ROLES = frozenset({"TS", "INT", "NONE"})
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
) -> dict[str, Any]:
    """Validate and persist a frame candidate (all-or-nothing).

    Args:
        task_root: Job working directory.
        job_id: Owning scheduler job id (recorded for audit).
        workflow: Workflow name (e.g. ``scan``, ``optimize``, ``Confsearch``).
        view_type: One of ``scan``, ``optimization``, ``sampling``,
            ``conformer``.
        frame_index: 0-based frame index.
        role: One of ``TS``, ``INT``, ``NONE``.
        name: Optional display name; defaults to the candidate_id.
        expected_revision: When given, the currently stored revision must
            match.
        now: Injectable timestamp (tests); defaults to local time now.

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
        raise FrameCandidateError(f"invalid candidate role: {role!r} (expected TS, INT, or NONE)")
    # NONE is not a chemistry tag -- it's our own sentinel for "no role";
    # normalize_tag maps it to None, which build_tag_title renders as "INT".
    normalized_role = normalize_tag(role) if effective_role != "NONE" else "INT"

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
    )

    # --- Build candidate_id ---
    prefix = _PREFIX_MAP.get(view_type, "unknown")
    cid = candidate_id_for(prefix, effective_role, frame_index)

    # --- Build TAG comment line ---
    tag_role = normalized_role or "INT"
    tag_comment = build_tag_title(
        tag_role,
        candidate_id=cid,
        source=workflow,
        frame=frame_index,
        extra="selection_source=manual_frame",
    )

    # --- Write structures XYZ ---
    structures_dir = root / "RESULT" / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)
    target = structures_dir / f"{cid}.xyz"
    xyz_text = rewrite_xyz_comment(frame_xyz, tag_comment)
    atomic_write_text(target, xyz_text)

    # --- Update authority file ---
    entry: dict[str, Any] = {
        "candidate_id": cid,
        "view_type": view_type,
        "frame_index": frame_index,
        "role": effective_role,
        "name": str(name or cid),
        "structure_path": f"structures/{cid}.xyz",
        "saved_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
    }
    candidates_list = list(existing.get("candidates", [])) if existing else []
    # Idempotent: replace existing entry with same candidate_id
    candidates_list = [c for c in candidates_list if c.get("candidate_id") != cid]
    candidates_list.append(entry)
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


def list_frame_candidates(task_root: Path | str) -> dict[str, Any]:
    """Return the current frame-candidates payload (None-safe).

    Args:
        task_root: Job working directory.

    Returns:
        The authority payload (``schema_version``, ``candidates``,
        ``revision``), or an empty payload with ``revision=0`` when
        missing/corrupt.
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
    """Remove a candidate atomically; its XYZ file is kept on disk."""
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

    # Remove from authority
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
