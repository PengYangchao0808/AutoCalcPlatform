"""PES manual review — persist user-confirmed TS/INT selections.

After a PESsearch job completes, the user may promote arbitrary scan frames
to confirmed TS/INT candidates.  This module is the single writer for the
manual-review artifacts:

- ``RESULT/pes_search/pes_review.json`` — authoritative review record
  (schema ``pes_review_v1``) with a monotonic ``revision`` counter.
- ``RESULT/structures/<candidate_id>.xyz`` — one materialised XYZ per
  confirmed frame, with a rewritten TAG comment carrying the stable
  ``candidate_id`` and ``selection_source=manual``.
- ``RESULT/result_manifest.json`` — structure products are replaced so only
  the currently confirmed candidates remain visible to BatchOptimize.
  Algorithmic recommendations stay in ``pes_profile.json`` for audit.

All candidates are validated before anything is written (all-or-nothing).
Re-saving the same selection is idempotent: candidate ids are derived
deterministically from ``role + frame_index``, so repeat saves reuse the
same files and manifest ids.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from acp.calculations.batch._tag import build_tag_title, normalize_tag
from acp.calculations.pes.outputs import (
    PES_PROFILE_RELATIVE_PATH,
    PES_SCAN_RELATIVE_PATH,
    _write_json_atomic,
)
from acp.storage.manifest import ProductKind, ResultManifest

PES_REVIEW_RELATIVE_PATH = "RESULT/pes_search/pes_review.json"
PES_REVIEW_SCHEMA = "pes_review_v1"
PES_REVIEW_PRODUCT_PREFIX = "pes_candidate_"
_ROLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

__all__ = [
    "PES_REVIEW_PRODUCT_PREFIX",
    "PES_REVIEW_RELATIVE_PATH",
    "PES_REVIEW_SCHEMA",
    "PesReviewError",
    "RevisionConflictError",
    "candidate_id_for",
    "load_pes_review",
    "normalize_role",
    "save_pes_review",
]


class PesReviewError(ValueError):
    """A PES review request failed validation."""


class RevisionConflictError(PesReviewError):
    """The review was saved against a stale revision (concurrent edit)."""


def normalize_role(value: object) -> str:
    """Normalise a role spelling (``ts``/``intermediate``/...) to ``TS`` or ``INT``."""
    tag = normalize_tag(value if isinstance(value, str) else None)
    if tag is None:
        raise PesReviewError(f"invalid candidate role: {value!r} (expected TS or INT)")
    return tag


def candidate_id_for(role: str, frame_index: int) -> str:
    """Deterministic, stable candidate id: ``pes_ts_frame_027`` / ``pes_int_frame_036``."""
    token = "ts" if role == "TS" else "int"
    return f"pes_{token}_frame_{frame_index:03d}"


def load_pes_review(task_root: Path | str) -> dict[str, Any] | None:
    """Read ``RESULT/pes_search/pes_review.json``; ``None`` when missing or corrupt."""
    path = Path(task_root) / PES_REVIEW_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class _ValidatedCandidate:
    """One fully validated review candidate, ready to materialise."""

    frame_index: int
    role: str
    candidate_id: str
    name: str
    frame_xyz: str
    tag_comment: str


def _load_profile(task_root: Path) -> dict[str, Any]:
    profile_path = task_root / PES_PROFILE_RELATIVE_PATH
    if not profile_path.is_file():
        raise PesReviewError(f"PES profile not found: {PES_PROFILE_RELATIVE_PATH}")
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PesReviewError(f"unreadable PES profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise PesReviewError(f"PES profile must be a JSON object: {profile_path}")
    if payload.get("schema_version") not in (None, "pes_profile_v2"):
        raise PesReviewError(f"unsupported PES profile schema: {payload.get('schema_version')!r}")
    if not isinstance(payload.get("frames"), list):
        raise PesReviewError("PES profile carries no frames list")
    return payload


def _profile_digest(task_root: Path) -> str:
    profile_path = task_root / PES_PROFILE_RELATIVE_PATH
    try:
        return hashlib.sha256(profile_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _ensure_inside(task_root: Path, path: Path) -> Path:
    """Resolve *path* and refuse anything outside *task_root*."""
    resolved = path.resolve()
    try:
        resolved.relative_to(task_root)
    except ValueError:
        raise PesReviewError(f"structure path escapes the task directory: {path}") from None
    return resolved


def _validate_candidates(
    task_root: Path,
    profile: dict[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[_ValidatedCandidate]:
    frames = profile.get("frames") or []
    scan_dir = str(profile.get("scan_dir") or PES_SCAN_RELATIVE_PATH)
    validated: list[_ValidatedCandidate] = []
    seen_ids: set[str] = set()
    seen_frames: set[int] = set()
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise PesReviewError(f"candidate entry must be an object: {raw!r}")
        role = normalize_role(raw.get("role") or raw.get("tag"))
        try:
            frame_index = int(raw.get("frame_index"))
        except (TypeError, ValueError):
            raise PesReviewError(f"candidate has no valid frame_index: {raw!r}") from None
        if frame_index < 0 or frame_index >= len(frames):
            raise PesReviewError(
                f"frame_index {frame_index} out of range (profile has {len(frames)} frames)"
            )
        if frame_index in seen_frames:
            raise PesReviewError(f"frame_index {frame_index} selected more than once")
        seen_frames.add(frame_index)

        frame = frames[frame_index]
        if not isinstance(frame, Mapping):
            raise PesReviewError(f"frame {frame_index} is malformed in the PES profile")
        geometry_rel = str(frame.get("geometry_path") or "")
        if not geometry_rel:
            raise PesReviewError(f"frame {frame_index} has no geometry_path")
        frame_path = _ensure_inside(task_root, task_root / scan_dir / geometry_rel)
        if not frame_path.is_file():
            raise PesReviewError(f"frame geometry missing on disk: {frame_path}")
        try:
            frame_xyz = frame_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PesReviewError(f"unreadable frame geometry: {frame_path}") from exc

        requested_id = str(raw.get("candidate_id") or "")
        if requested_id and not _ROLE_TOKEN_RE.match(requested_id):
            raise PesReviewError(f"invalid candidate_id: {requested_id!r}")
        candidate_id = requested_id or candidate_id_for(role, frame_index)
        if candidate_id in seen_ids:
            raise PesReviewError(f"duplicate candidate_id across selection: {candidate_id}")
        seen_ids.add(candidate_id)

        name = str(raw.get("name") or "") or candidate_id
        tag_comment = build_tag_title(
            role,
            candidate_id=candidate_id,
            source="PESsearch",
            frame=frame_index,
            extra="selection_source=manual",
        )
        validated.append(
            _ValidatedCandidate(
                frame_index=frame_index,
                role=role,
                candidate_id=candidate_id,
                name=name,
                frame_xyz=frame_xyz,
                tag_comment=tag_comment,
            )
        )
    return validated


def _rewrite_xyz_comment(xyz_text: str, comment: str) -> str:
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


def _materialise_structures(
    structures_dir: Path,
    validated: list[_ValidatedCandidate],
) -> list[dict[str, str]]:
    """Write one tagged XYZ per confirmed candidate; returns manifest entries."""
    structures_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str]] = []
    for candidate in validated:
        target = structures_dir / f"{candidate.candidate_id}.xyz"
        text = _rewrite_xyz_comment(candidate.frame_xyz, candidate.tag_comment)
        _atomic_write_text(target, text)
        entries.append(
            {
                "candidate_id": candidate.candidate_id,
                "role": candidate.role,
                "name": candidate.name,
                "structure_path": f"structures/{target.name}",
            }
        )
    return entries


def _atomic_write_text(path: Path, text: str) -> None:
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


def _update_result_manifest(
    result_dir: Path,
    task_id: str,
    validated: list[_ValidatedCandidate],
    entries: list[dict[str, str]],
) -> Path:
    """Replace PES structure products so only confirmed candidates remain."""
    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.task_id = manifest.task_id or task_id
    manifest.workflow = manifest.workflow or "PESsearch"
    # Drop previous PES structure references (recommendations + earlier saves);
    # recommendation data stays in pes_profile.json for audit.
    manifest.products = [
        p for p in manifest.products if not p.id.startswith(PES_REVIEW_PRODUCT_PREFIX)
    ]
    by_id = {c.candidate_id: c for c in validated}
    for entry in entries:
        candidate = by_id[entry["candidate_id"]]
        manifest.add_product(
            id=f"{PES_REVIEW_PRODUCT_PREFIX}{candidate.candidate_id}",
            label=f"PESsearch {candidate.role} candidate {candidate.candidate_id} (manual)",
            path=entry["structure_path"],
            kind=ProductKind.STRUCTURE,
            metadata={
                "candidate_id": candidate.candidate_id,
                "role": candidate.role,
                "frame_index": candidate.frame_index,
                "source": "PESsearch",
                "selection_source": "manual",
            },
        )
    return manifest.write(result_dir)


def save_pes_review(
    task_root: Path | str,
    *,
    job_id: str,
    candidates: Sequence[Mapping[str, Any]],
    note: str = "",
    expected_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and persist a manual PES review (all-or-nothing).

    Args:
        task_root: PESsearch job working directory.
        job_id: Owning scheduler job id (recorded for audit).
        candidates: Requested selections; each entry needs ``frame_index``
            and ``role`` (``TS``/``INT``), optional ``candidate_id``/``name``.
        note: Free-text annotation stored in the review file.
        expected_revision: When given, the currently stored revision must
            match; otherwise a :class:`RevisionConflictError` is raised.
        now: Injectable timestamp (tests); defaults to local time now.

    Returns:
        The written ``pes_review.json`` payload.

    Raises:
        PesReviewError: Any candidate failed validation; nothing is written.
        RevisionConflictError: ``expected_revision`` does not match the stored one.
    """
    root = Path(task_root).expanduser().resolve()
    profile = _load_profile(root)

    existing = load_pes_review(root)
    current_revision = int(existing.get("revision", 0)) if existing else 0
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RevisionConflictError(
            f"review revision conflict: stored={current_revision}, expected={expected_revision}"
        )

    validated = _validate_candidates(root, profile, candidates)

    result_dir = root / "RESULT"
    structures_dir = result_dir / "structures"
    entries = _materialise_structures(structures_dir, validated)
    manifest_path = _update_result_manifest(result_dir, job_id, validated, entries)

    confirmed_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    selected = [
        {
            "candidate_id": entry["candidate_id"],
            "frame_index": candidate.frame_index,
            "role": candidate.role,
            "name": candidate.name,
            "selection_source": "manual",
            "structure_path": entry["structure_path"],
        }
        for entry, candidate in zip(entries, validated)
    ]
    payload: dict[str, Any] = {
        "schema_version": PES_REVIEW_SCHEMA,
        "job_id": job_id,
        "status": "confirmed",
        "confirmed_at": confirmed_at,
        "revision": current_revision + 1,
        "profile_sha256": _profile_digest(root),
        "note": str(note or ""),
        "selected": selected,
    }
    _write_json_atomic(root / PES_REVIEW_RELATIVE_PATH, payload)
    payload["result_manifest_path"] = str(manifest_path)
    return payload
