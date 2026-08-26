"""s2_path_v2 manifest and s2_review.json I/O (plan §7.3, §8.2, §12.3).

The v2 manifest is written ONLY by the bond-length-scan mode; the legacy
``s2_path_v1`` manifests (old guided-scan/reverse-peb/direct-ts) stay
read-only.  ``stationary_point_claimed`` is always ``False`` — S2 only
produces *initial guesses* for the user to review (plan §7.3/§9.3).

The editable-candidate layer (s2_candidate_v1) lives here too: saving a
review materializes every user-marked frame under
``RESULT/structures/s2_candidates/`` and writes
``s2_candidate_manifest.json`` — the single entry point downstream S3
tasks read candidates from.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from acp.confsearch.shared.artifacts import write_json_atomic
from acp.confsearch.shared.provenance import utc_now_iso

from .scan_models import (
    CandidateRecommendation,
    EnergyProfile,
    ReviewCandidate,
    ScanFrame,
    ScanQuality,
    ScanReview,
    normalize_candidate_role,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CANDIDATE_STRUCTURES_DIR",
    "S2_CANDIDATE_MANIFEST_NAME",
    "S2_CANDIDATE_SCHEMA_VERSION",
    "S2_MANIFEST_NAME",
    "S2_REVIEW_NAME",
    "S2_V2_SCHEMA_VERSION",
    "S2_MANIFEST_KIND",
    "build_manifest_payload",
    "candidate_manifest_path",
    "frame_geometry_path",
    "manual_candidate_id",
    "materialize_s2_candidates",
    "read_s2_candidate_manifest",
    "read_s2_review",
    "read_scan_manifest",
    "write_s2_review",
    "write_scan_manifest",
]

S2_MANIFEST_NAME = "s2_path_manifest.json"
S2_REVIEW_NAME = "s2_review.json"
S2_V2_SCHEMA_VERSION = "s2_path_v2"
S2_MANIFEST_KIND = "s2_path_manifest"
S2_CANDIDATE_MANIFEST_NAME = "s2_candidate_manifest.json"
S2_CANDIDATE_SCHEMA_VERSION = "s2_candidate_v1"
CANDIDATE_STRUCTURES_DIR = "structures/s2_candidates"

# Internal representation of the per-frame scan records (dicts produced by
# :meth:`ScanFrame.to_dict`) plus the derived profile/recommendations.
_ScanRecord = dict[str, Any]


def _resolve_manifest_path(path: Path | str) -> Path:
    return Path(path)


def read_scan_manifest(path: Path | str) -> dict[str, Any]:
    """Read and validate an ``s2_path_v2`` manifest.

    Raises:
        ValueError: When the payload is not a v2 bond-length-scan manifest.
    """
    manifest_path = _resolve_manifest_path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read S2 manifest: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"S2 manifest is not valid JSON: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"S2 manifest is not a JSON object: {manifest_path}")
    if payload.get("schema_version") != S2_V2_SCHEMA_VERSION:
        raise ValueError(
            f"Not an s2_path_v2 manifest (schema_version={payload.get('schema_version')!r}): "
            f"{manifest_path}"
        )
    if payload.get("mode") != "bond_length_scan":
        raise ValueError(f"Not a bond_length_scan manifest (mode={payload.get('mode')!r})")
    return payload


def build_manifest_payload(
    *,
    request: dict[str, Any],
    coordinate: Any,
    protocol: Any,
    charge: int,
    multiplicity: int,
    frames: list[ScanFrame],
    profile: EnergyProfile,
    quality: ScanQuality,
    ts_recommendations: list[CandidateRecommendation],
    int_recommendations: list[CandidateRecommendation],
    source: dict[str, Any],
    provenance: dict[str, Any],
    review: ScanReview | None = None,
    scan_dir_rel: str = "WORK/02_SEARCH/s2_bond_scan_001",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the s2_path_v2 manifest payload (plan §7.3)."""
    from acp.confsearch.shared.provenance import utc_now_iso

    review = review or ScanReview(required=True, status="pending")
    ts_rows = [candidate.to_dict() for candidate in ts_recommendations]
    int_rows = [candidate.to_dict() for candidate in int_recommendations]
    frame_rows = [frame.to_dict() for frame in frames]

    status = quality.status
    payload: dict[str, Any] = {
        "schema": S2_V2_SCHEMA_VERSION,
        "schema_version": S2_V2_SCHEMA_VERSION,
        "workflow": "PESsearch",
        "stage": "S2",
        "mode": "bond_length_scan",
        "status": status,
        "stationary_point_claimed": False,
        "created_at": created_at or utc_now_iso(),
        "input": {
            "source": dict(source),
            "charge": charge,
            "multiplicity": multiplicity,
        },
        "protocol": protocol.to_dict(),
        "scan": {
            "scan_dir": scan_dir_rel,
            "frames": frame_rows,
            "frame_count": len(frame_rows),
            "quality": quality.to_dict(),
        },
        "energy_profile": profile.to_dict(),
        "recommendations": {
            "ts": ts_rows,
            "intermediates": int_rows,
            "needs_review": quality.needs_review,
        },
        "review": review.to_dict(),
        "provenance": dict(provenance),
    }
    # Plan §9.5: no confident candidate → surface NEEDS_REVIEW guidance.
    if quality.needs_review:
        payload["review"]["status"] = "pending"
        payload["recommendations"]["note"] = (
            "扫描曲线未产生高置信度候选；建议扩大扫描范围、增加点数或更换初始结构后重试。"
        )
    return payload


def write_scan_manifest(payload: dict[str, Any], path: Path | str) -> Path:
    """Atomically write an s2_path_v2 manifest payload."""
    manifest_path = _resolve_manifest_path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest_path, payload)
    logger.info(
        "S2 v2 manifest written: %s (status=%s, ts=%d, int=%d)",
        manifest_path,
        payload.get("status"),
        len((payload.get("recommendations") or {}).get("ts") or []),
        len((payload.get("recommendations") or {}).get("intermediates") or []),
    )
    return manifest_path


def _review_path(manifest_path: Path | str) -> Path:
    return _resolve_manifest_path(manifest_path).with_name(S2_REVIEW_NAME)


def read_s2_review(manifest_path: Path | str) -> ScanReview | None:
    """Read the sibling ``s2_review.json``; None when not yet decided."""
    path = _review_path(manifest_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable s2_review.json at %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        return None
    return ScanReview.from_dict(payload)


def write_s2_review(review: ScanReview, manifest_path: Path | str) -> Path:
    """Atomically write the user's candidate review next to the manifest."""
    path = _review_path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, review.to_dict())
    logger.info("S2 review written: %s (status=%s)", path, review.status)
    return path


# ── editable-candidate layer (s2_candidate_v1) ──────────────────────────


def candidate_manifest_path(manifest_path: Path | str) -> Path:
    """Sibling ``s2_candidate_manifest.json`` path for an S2 manifest."""
    return _resolve_manifest_path(manifest_path).with_name(S2_CANDIDATE_MANIFEST_NAME)


def read_s2_candidate_manifest(manifest_path: Path | str) -> dict[str, Any] | None:
    """Read the sibling candidate manifest; None when never saved."""
    path = candidate_manifest_path(manifest_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Unreadable %s at %s: %s", S2_CANDIDATE_MANIFEST_NAME, path, exc)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def manual_candidate_id(frame_index: int) -> str:
    """Stable id for a manually-added frame candidate."""
    return f"manual_frame_{int(frame_index):03d}"


def _job_root(manifest_path: Path) -> Path:
    # RESULT/mechanism/s2_path_manifest.json → job root
    return manifest_path.parent.parent.parent


def _annotate_candidate_structure(
    xyz_path: Path,
    *,
    tag: str,
    candidate_id: str,
    frame_index: int | None,
) -> None:
    """Rewrite the copied frame's comment into the canonical TAG line.

    Downstream S3/S4 batch intake parses ``TAG: TS|INT`` from the XYZ
    comment (batch plan §4); the raw scan-frame title carries none.
    """
    from .batch_models import _rewrite_comment, build_tag_title

    try:
        text = xyz_path.read_text(encoding="utf-8")
        xyz_path.write_text(
            _rewrite_comment(
                text,
                build_tag_title(
                    tag,
                    candidate_id=candidate_id,
                    source="PESsearch",
                    frame=frame_index,
                ),
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not annotate candidate structure %s: %s", xyz_path, exc)


def frame_geometry_path(
    manifest_path: Path, payload: dict[str, Any], frame_index: int
) -> Path | None:
    """Resolve the on-disk XYZ for one scan frame; None when unavailable."""
    frames = (payload.get("scan") or {}).get("frames") or []
    frame = next(
        (
            row
            for row in frames
            if isinstance(row, dict) and int(row.get("index") or -1) == int(frame_index)
        ),
        None,
    )
    if frame is None:
        return None
    geometry_ref = str(frame.get("geometry_path") or "")
    if not geometry_ref:
        return None
    scan_dir = str((payload.get("scan") or {}).get("scan_dir") or "")
    candidates = [
        (_job_root(manifest_path) / scan_dir / geometry_ref) if scan_dir else None,
        manifest_path.parent / geometry_ref,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    return None


def _recommendation_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    recommendations = payload.get("recommendations") or {}
    for role, group in (("ts", "ts"), ("intermediate", "intermediates")):
        for rec in recommendations.get(group) or []:
            if isinstance(rec, dict) and rec.get("candidate_id"):
                row = dict(rec)
                row["role"] = role
                index[str(rec["candidate_id"])] = row
    return index


def _previous_rows(manifest_path: Path) -> dict[str, dict[str, Any]]:
    previous = read_s2_candidate_manifest(manifest_path) or {}
    rows: dict[str, dict[str, Any]] = {}
    for row in previous.get("candidates") or []:
        if isinstance(row, dict) and row.get("candidate_id"):
            rows[str(row["candidate_id"])] = dict(row)
    return rows


def materialize_s2_candidates(
    manifest_path: Path | str,
    payload: dict[str, Any],
    candidates: list[ReviewCandidate],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Persist the user's candidate marking and materialize its structures.

    Validates every submitted marking against the scan manifest, copies each
    marked frame's XYZ into ``RESULT/structures/s2_candidates/``, writes
    ``s2_candidate_manifest.json`` + ``s2_review.json``, flips the embedded
    manifest review to ``confirmed``/``rejected``, and registers the
    structure products in ``RESULT/result_manifest.json``.

    Returns:
        A summary payload: ``{review, candidates, candidate_manifest,
        structures_dir, result_manifest}``.

    Raises:
        ValueError: On unknown frames, missing XYZ, duplicate frames or
            invalid roles.
    """
    from acp.storage.manifest import ResultManifest

    manifest_path = _resolve_manifest_path(manifest_path)
    result_root = manifest_path.parent.parent  # RESULT/
    structures_dir = result_root / "structures" / "s2_candidates"
    structures_dir.mkdir(parents=True, exist_ok=True)

    recommendations = _recommendation_index(payload)
    previous = _previous_rows(manifest_path)
    submitted_frames: set[int] = set()
    resolved: list[dict[str, Any]] = []

    for candidate in candidates:
        role = normalize_candidate_role(candidate.role)
        frame_index = int(candidate.frame_index)
        if frame_index in submitted_frames:
            raise ValueError(
                f"frame {frame_index} is marked more than once — one TS/INT marking per frame"
            )
        submitted_frames.add(frame_index)
        xyz_path = frame_geometry_path(manifest_path, payload, frame_index)
        if xyz_path is None:
            raise ValueError(f"frame {frame_index} has no resolvable XYZ geometry")
        candidate_id = str(candidate.candidate_id or "").strip()
        if not candidate_id:
            candidate_id = manual_candidate_id(frame_index)
        recommended = recommendations.get(candidate_id)
        previous_row = previous.get(candidate_id)
        source = "manual"
        recommended_role: str | None = None
        if recommended is not None:
            source = str(recommended.get("selection_source") or "algorithm")
            recommended_role = normalize_candidate_role(recommended.get("role") or role)
        elif previous_row is not None:
            source = str(previous_row.get("selection_source") or "manual")
            recommended_role = previous_row.get("recommended_role")
        shutil.copy2(xyz_path, structures_dir / f"{candidate_id}.xyz")
        _annotate_candidate_structure(
            structures_dir / f"{candidate_id}.xyz",
            tag="TS" if role == "ts" else "INT",
            candidate_id=candidate_id,
            frame_index=frame_index,
        )
        resolved.append(
            {
                "candidate_id": candidate_id,
                "frame_index": frame_index,
                "role": role,
                "geometry": f"{CANDIDATE_STRUCTURES_DIR}/{candidate_id}.xyz",
                "recommended_role": recommended_role,
                "selection_source": source,
                "active": True,
                **({"name": candidate.name} if candidate.name is not None else {}),
                **(
                    {"confidence": recommended["confidence"], "reason": recommended.get("reason")}
                    if recommended is not None
                    else {}
                ),
            }
        )

    for candidate_id, row in previous.items():
        if any(item["candidate_id"] == candidate_id for item in resolved):
            continue
        carried = dict(row)
        carried["active"] = False
        fallback_role = carried.get("role") or carried.get("recommended_role") or "ts"
        carried["role"] = normalize_candidate_role(fallback_role)
        resolved.append(carried)

    resolved.sort(key=lambda row: (0 if row["role"] == "ts" else 1, row["frame_index"]))
    now = utc_now_iso()
    active_rows = [row for row in resolved if row["active"]]
    review = ScanReview(
        required=True,
        status="confirmed" if active_rows else "rejected",
        selected_ts=tuple(row["candidate_id"] for row in active_rows if row["role"] == "ts"),
        selected_intermediates=tuple(
            row["candidate_id"] for row in active_rows if row["role"] == "intermediate"
        ),
        candidates=tuple(
            ReviewCandidate(
                candidate_id=row["candidate_id"],
                frame_index=int(row["frame_index"]),
                role=str(row["role"]),
                name=None if row.get("name") is None else str(row["name"]),
            )
            for row in active_rows
        ),
        decided_at=now,
        note=note,
    )

    previous_candidate_manifest = read_s2_candidate_manifest(manifest_path) or {}
    candidate_manifest_payload = {
        "schema_version": S2_CANDIDATE_SCHEMA_VERSION,
        "source_manifest": "RESULT/mechanism/" + S2_MANIFEST_NAME,
        "created_at": previous_candidate_manifest.get("created_at") or now,
        "updated_at": now,
        "candidates": resolved,
    }
    write_json_atomic(candidate_manifest_path(manifest_path), candidate_manifest_payload)
    write_s2_review(review, manifest_path)

    # Keep the embedded manifest review in sync so the CLI-side S3 gate
    # (_require_confirmed_review) sees the saved decision.
    payload["review"] = review.to_dict()
    write_scan_manifest(payload, manifest_path)

    result_manifest = ResultManifest(
        task_id=str(payload.get("provenance", {}).get("job_id") or ""),
        workflow="PESsearch",
        status="confirmed" if active_rows else "rejected",
    )
    existing_manifest_path = result_root / "result_manifest.json"
    if existing_manifest_path.is_file():
        try:
            result_manifest = ResultManifest.read(result_root)
            result_manifest.status = "confirmed" if active_rows else "rejected"
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("unreadable result_manifest.json — rewriting it", exc_info=True)
    result_manifest.products = [
        product
        for product in result_manifest.products
        if not product.id.startswith("s2_candidate_")
    ]
    for row in active_rows:
        result_manifest.add_product(
            f"s2_candidate_{row['candidate_id']}",
            f"S2 candidate {row['candidate_id']} ({row['role'].upper()})",
            f"{CANDIDATE_STRUCTURES_DIR}/{row['candidate_id']}.xyz",
            "structure",
        )
    result_manifest.add_product(
        "s2_candidate_manifest",
        "S2 candidate manifest",
        f"mechanism/{S2_CANDIDATE_MANIFEST_NAME}",
        "file",
    )
    result_manifest.write(result_root)

    logger.info(
        "S2 candidates materialized: %d active / %d total under %s",
        len(active_rows),
        len(resolved),
        structures_dir,
    )
    return {
        "review": review.to_dict(),
        "candidates": resolved,
        "candidate_manifest": f"RESULT/mechanism/{S2_CANDIDATE_MANIFEST_NAME}",
        "structures_dir": f"RESULT/{CANDIDATE_STRUCTURES_DIR}",
        "result_manifest": "RESULT/result_manifest.json",
    }


# Keep the module's serialisation helpers importable for API projection.
ScanFrameRecord = ScanFrame
