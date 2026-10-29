"""PES manual review — persist user-confirmed TS/INT selections.

After a PESsearch job terminates, the user may promote scan frames to
confirmed TS/INT candidates.  Two frame sources are supported:

- ``RESULT/pes_search/pes_profile.json`` (``pes_profile_v2``) — the final
  profile of a successful scan;
- the live-scan artifacts (``RESULT/trajectories/pes_scan_trajectory.json``
  snapshot, native-ORCA ledger, or ``frame_NNN/`` directories) — the partial
  frames of an interrupted scan from a FAILED/CANCELLED task.

This module is the single writer for the manual-review artifacts:

- ``RESULT/pes_search/pes_review.json`` — authoritative review record
  (schema ``pes_review_v1``) with a monotonic ``revision`` counter and a
  ``source`` block recording the owning task status, scan completeness,
  frame convergence, and the digest of the data the selection was made on;
- ``RESULT/structures/<candidate_id>.xyz`` — one materialised XYZ per
  confirmed frame, with a rewritten TAG comment carrying the stable
  ``candidate_id`` and ``selection_source=manual``;
- ``RESULT/result_manifest.json`` — structure products are replaced so only
  the currently confirmed candidates remain visible to BatchOptimize.
  Algorithmic recommendations stay in ``pes_profile.json`` for audit.

Frames that carry an energy but no usable geometry file remain viewable on
the curve but can never be confirmed as structure candidates.  All
candidates are validated before anything is written (all-or-nothing).
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
PES_REVIEW_BACKUP_TEMPLATE = "pes_review_backup_{n:03d}.json"
PES_REVIEW_PRODUCT_PREFIX = "pes_candidate_"
_ROLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

__all__ = [
    "PES_REVIEW_BACKUP_TEMPLATE",
    "PES_REVIEW_PRODUCT_PREFIX",
    "PES_REVIEW_RELATIVE_PATH",
    "PES_REVIEW_SCHEMA",
    "PesReviewError",
    "RevisionConflictError",
    "candidate_id_for",
    "load_pes_review",
    "load_pes_review_backups",
    "normalize_role",
    "restore_pes_review",
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
    frame_converged: bool


@dataclass(frozen=True)
class _FrameSource:
    """Resolved frame lookup backing one review save.

    ``kind`` is ``"final_profile"`` (geometry relative to ``geometry_base``
    via the ``geometry_path`` field) or ``"live_partial"`` (task-root
    relative ``geometry_ref`` from the live-scan artifacts).
    """

    kind: str
    task_status: str
    frames_by_index: dict[int, Mapping[str, Any]]
    geometry_base: str
    geometry_key: str
    scan_complete: bool
    data_source: str
    data_sha256: str


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


def _live_source_digest(task_root: Path, source: str) -> str:
    """Digest the live-scan artifact a partial review was decided against."""
    digest = hashlib.sha256()
    if source.endswith("*"):
        scan_dir = task_root / PES_SCAN_RELATIVE_PATH
        if scan_dir.is_dir():
            for item in sorted(scan_dir.rglob("*")):
                if not item.is_file():
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                digest.update(item.relative_to(scan_dir).as_posix().encode("utf-8"))
                digest.update(str(stat.st_size).encode("utf-8"))
                digest.update(str(int(stat.st_mtime)).encode("utf-8"))
        return digest.hexdigest()
    try:
        return hashlib.sha256((task_root / source).read_bytes()).hexdigest()
    except OSError:
        return ""


def _resolve_frame_source(task_root: Path, *, task_status: str) -> _FrameSource:
    """Load the final profile, else fall back to the live partial frames."""
    if (task_root / PES_PROFILE_RELATIVE_PATH).is_file():
        profile = _load_profile(task_root)
        frames_by_index: dict[int, Mapping[str, Any]] = {}
        for position, frame in enumerate(profile.get("frames") or []):
            if not isinstance(frame, Mapping):
                continue
            try:
                index = int(frame.get("index", position))
            except (TypeError, ValueError):
                index = position
            frames_by_index[index] = frame
        quality = (profile.get("scan") or {}).get("quality") or {}
        scan_complete = bool(
            quality.get(
                "scan_complete",
                str(profile.get("status") or "") in {"ready_for_review", "completed"},
            )
        )
        return _FrameSource(
            kind="final_profile",
            task_status=task_status,
            frames_by_index=frames_by_index,
            geometry_base=str(profile.get("scan_dir") or PES_SCAN_RELATIVE_PATH),
            geometry_key="geometry_path",
            scan_complete=scan_complete,
            data_source=PES_PROFILE_RELATIVE_PATH,
            data_sha256=_profile_digest(task_root),
        )

    from acp.calculations.pes.scan_snapshot import TERMINAL_STAGES
    from acp.results.pes_scan_live import collect_pes_scan_live_frames

    live = collect_pes_scan_live_frames(task_root, include_incomplete=True)
    if live is None or not live.frames:
        raise PesReviewError(
            "PES profile not found and no reviewable live scan frames: expected "
            f"{PES_PROFILE_RELATIVE_PATH} or scan artifacts under {PES_SCAN_RELATIVE_PATH}"
        )
    partial_index: dict[int, Mapping[str, Any]] = {}
    for frame in live.frames:
        try:
            partial_index[int(frame.get("index"))] = frame
        except (TypeError, ValueError):
            continue
    scan_complete = live.stage == "completed" or (
        bool(live.points_total)
        and len(partial_index) >= live.points_total
        and live.stage in TERMINAL_STAGES
    )
    return _FrameSource(
        kind="live_partial",
        task_status=task_status,
        frames_by_index=partial_index,
        geometry_base="",
        geometry_key="geometry_ref",
        scan_complete=scan_complete,
        data_source=str(live.source),
        data_sha256=_live_source_digest(task_root, str(live.source)),
    )


def _ensure_inside(task_root: Path, path: Path) -> Path:
    """Resolve *path* and refuse anything outside *task_root*."""
    resolved = path.resolve()
    try:
        resolved.relative_to(task_root)
    except ValueError:
        raise PesReviewError(f"structure path escapes the task directory: {path}") from None
    return resolved


def _read_validated_xyz(path: Path) -> str:
    """Read one frame geometry and refuse anything that is not a valid XYZ."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PesReviewError(f"unreadable frame geometry: {path}") from exc
    lines = text.strip().splitlines()
    try:
        atom_count = int(lines[0].strip())
    except (IndexError, ValueError):
        atom_count = -1
    if atom_count <= 0 or len(lines) < atom_count + 2:
        raise PesReviewError(f"frame geometry is not a valid XYZ file: {path}")
    return text


def _validate_candidates(
    task_root: Path,
    source: _FrameSource,
    candidates: Sequence[Mapping[str, Any]],
) -> list[_ValidatedCandidate]:
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
        if frame_index in seen_frames:
            raise PesReviewError(f"frame_index {frame_index} selected more than once")
        seen_frames.add(frame_index)

        frame = source.frames_by_index.get(frame_index)
        if frame is None:
            available = sorted(source.frames_by_index)
            raise PesReviewError(
                f"frame_index {frame_index} out of range (source has "
                f"{len(available)} frames: {available})"
            )

        geometry_rel = str(frame.get(source.geometry_key) or "")
        if not geometry_rel:
            raise PesReviewError(
                f"frame {frame_index} has energy but no usable geometry; "
                "view-only frames cannot be confirmed as structure candidates"
            )
        frame_path = _ensure_inside(task_root, task_root / source.geometry_base / geometry_rel)
        if not frame_path.is_file():
            raise PesReviewError(f"frame geometry missing on disk: {frame_path}")
        frame_xyz = _read_validated_xyz(frame_path)

        frame_converged = (
            bool(frame.get("optimization_converged", True))
            if source.kind == "final_profile"
            else bool(frame.get("converged", True))
        )

        requested_id = str(raw.get("candidate_id") or "")
        if requested_id and not _ROLE_TOKEN_RE.match(requested_id):
            raise PesReviewError(f"invalid candidate_id: {requested_id!r}")
        candidate_id = requested_id or candidate_id_for(role, frame_index)
        if candidate_id in seen_ids:
            raise PesReviewError(f"duplicate candidate_id across selection: {candidate_id}")
        seen_ids.add(candidate_id)

        name = str(raw.get("name") or "") or candidate_id
        extra = "selection_source=manual"
        if source.kind == "live_partial":
            extra += (
                f" scan_complete={'true' if source.scan_complete else 'false'}"
                f" task_status={source.task_status}"
            )
        tag_comment = build_tag_title(
            role,
            candidate_id=candidate_id,
            source="PESsearch",
            frame=frame_index,
            extra=extra,
        )
        validated.append(
            _ValidatedCandidate(
                frame_index=frame_index,
                role=role,
                candidate_id=candidate_id,
                name=name,
                frame_xyz=frame_xyz,
                tag_comment=tag_comment,
                frame_converged=frame_converged,
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
    source: _FrameSource,
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
                "task_status": source.task_status,
                "scan_complete": source.scan_complete,
                "frame_converged": candidate.frame_converged,
                "data_source": source.data_source,
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
    restored_from: int | None = None,
    source_task_status: str = "COMPLETED",
) -> dict[str, Any]:
    """Validate and persist a manual PES review (all-or-nothing).

    Frame lookup accepts the final ``pes_profile.json`` (successful scans) or
    the live partial frames (interrupted scans of FAILED/CANCELLED tasks);
    the caller owns the policy — the API layer only allows partial frames
    for terminal non-completed jobs.

    The previous confirmed state (revision >= 1) is first rotated into
    ``RESULT/pes_search/pes_review_backup_<n>.json`` (n = previous revision
    = attempt count); backups are never deleted, enabling multi-round
    selection switching via :func:`restore_pes_review`.

    Args:
        task_root: PESsearch job working directory.
        job_id: Owning scheduler job id (recorded for audit).
        candidates: Requested selections; each entry needs ``frame_index``
            and ``role`` (``TS``/``INT``), optional ``candidate_id``/``name``.
        note: Free-text annotation stored in the review file.
        expected_revision: When given, the currently stored revision must
            match; otherwise a :class:`RevisionConflictError` is raised.
        now: Injectable timestamp (tests); defaults to local time now.
        restored_from: Set by :func:`restore_pes_review` for audit.
        source_task_status: Status of the owning task at save time
            (``COMPLETED``/``FAILED``/``CANCELLED``); recorded as provenance.

    Returns:
        The written ``pes_review.json`` payload.

    Raises:
        PesReviewError: Any candidate failed validation; nothing is written.
        RevisionConflictError: ``expected_revision`` does not match the stored one.
    """
    root = Path(task_root).expanduser().resolve()
    task_status = re.sub(r"[^A-Za-z0-9_]", "_", str(source_task_status or "UNKNOWN")).upper()
    source = _resolve_frame_source(root, task_status=task_status)

    existing = load_pes_review(root)
    current_revision = int(existing.get("revision", 0)) if existing else 0
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RevisionConflictError(
            f"review revision conflict: stored={current_revision}, expected={expected_revision}"
        )

    validated = _validate_candidates(root, source, candidates)

    result_dir = root / "RESULT"
    structures_dir = result_dir / "structures"
    if existing and current_revision >= 1:
        _rotate_backup(root, existing, current_revision)
    entries = _materialise_structures(structures_dir, validated)
    manifest_path = _update_result_manifest(result_dir, job_id, source, validated, entries)

    confirmed_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    selected = [
        {
            "candidate_id": entry["candidate_id"],
            "frame_index": candidate.frame_index,
            "role": candidate.role,
            "name": candidate.name,
            "selection_source": "manual",
            "structure_path": entry["structure_path"],
            "frame_converged": candidate.frame_converged,
        }
        for entry, candidate in zip(entries, validated)
    ]
    payload: dict[str, Any] = {
        "schema_version": PES_REVIEW_SCHEMA,
        "job_id": job_id,
        "status": "confirmed",
        "confirmed_at": confirmed_at,
        "revision": current_revision + 1,
        "attempt": current_revision + 1,
        "profile_sha256": source.data_sha256 if source.kind == "final_profile" else "",
        "source": {
            "task_status": source.task_status,
            "frame_source": source.kind,
            "scan_complete": source.scan_complete,
            "data_source": source.data_source,
            "data_sha256": source.data_sha256,
        },
        "note": str(note or ""),
        "selected": selected,
    }
    if restored_from is not None:
        payload["restored_from"] = int(restored_from)
    _write_json_atomic(root / PES_REVIEW_RELATIVE_PATH, payload)
    payload["result_manifest_path"] = str(manifest_path)
    return payload


def _rotate_backup(root: Path, existing: dict[str, Any], revision: int) -> Path:
    """Preserve the current review as ``pes_review_backup_<revision>.json``."""
    backup_path = root / "RESULT" / "pes_search" / PES_REVIEW_BACKUP_TEMPLATE.format(n=revision)
    _atomic_write_text(backup_path, json.dumps(existing, indent=2, sort_keys=True, default=str))
    return backup_path


def load_pes_review_backups(task_root: Path | str) -> list[dict[str, Any]]:
    """List review backups (n, confirmed_at, note, selected_count, selected).

    Sorted by attempt number ascending; corrupt entries are skipped.
    """
    pes_dir = Path(task_root) / "RESULT" / "pes_search"
    if not pes_dir.is_dir():
        return []
    backups: list[dict[str, Any]] = []
    for path in pes_dir.glob("pes_review_backup_*.json"):
        match = re.search(r"pes_review_backup_(\d+)\.json$", path.name)
        if match is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        selected = payload.get("selected")
        backups.append(
            {
                "n": int(match.group(1)),
                "confirmed_at": payload.get("confirmed_at"),
                "note": payload.get("note") or "",
                "selected_count": len(selected) if isinstance(selected, list) else 0,
                "selected": selected if isinstance(selected, list) else [],
            }
        )
    backups.sort(key=lambda item: item["n"])
    return backups


def restore_pes_review(
    task_root: Path | str,
    backup_n: int,
    *,
    expected_revision: int | None = None,
    now: datetime | None = None,
    source_task_status: str = "COMPLETED",
) -> dict[str, Any]:
    """Re-activate backup *backup_n* as the current review (rotation-aware).

    The restore goes through the full save pipeline: structures are
    re-materialised, the result manifest switches to the restored selection,
    the current state is first rotated into a fresh backup, and the review
    revision/attempt advance by one.

    Raises:
        PesReviewError: Unknown backup or invalid stored selection.
        RevisionConflictError: ``expected_revision`` mismatch.
    """
    root = Path(task_root).expanduser().resolve()
    backup_path = root / "RESULT" / "pes_search" / PES_REVIEW_BACKUP_TEMPLATE.format(n=backup_n)
    if not backup_path.is_file():
        raise PesReviewError(f"review backup not found: {backup_path.name}")
    try:
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PesReviewError(f"unreadable review backup: {backup_path.name}") from exc
    if not isinstance(backup, dict):
        raise PesReviewError(f"malformed review backup: {backup_path.name}")

    candidates = [
        {
            "frame_index": row.get("frame_index"),
            "role": row.get("role"),
            "candidate_id": row.get("candidate_id"),
            "name": row.get("name"),
        }
        for row in backup.get("selected") or []
        if isinstance(row, dict)
    ]
    payload = save_pes_review(
        root,
        job_id=str(backup.get("job_id") or ""),
        candidates=candidates,
        note=str(backup.get("note") or ""),
        expected_revision=expected_revision,
        now=now,
        restored_from=int(backup_n),
        source_task_status=source_task_status,
    )
    payload["restored_from"] = int(backup_n)
    return payload
