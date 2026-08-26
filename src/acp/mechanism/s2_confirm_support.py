"""Types and manifest resolution shared by S2 confirmation services."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from acp.scheduler.jobs import JobRecord
from acp.scheduler.manager import JobManager

from .scan_manifest import read_scan_manifest
from .scan_models import ReviewCandidate


@dataclass(frozen=True, slots=True)
class S2CandidateMark:
    """Frame-indexed candidate marking supplied by an API adapter."""

    candidate_id: str = ""
    frame_index: int = 0
    role: str = "ts"
    name: str | None = None


@dataclass(frozen=True, slots=True)
class S2JobNotFoundError(KeyError):
    """Raised when an S2 source job does not exist."""

    job_id: str

    def __str__(self) -> str:
        return f"Job not found: {self.job_id}"


@dataclass(frozen=True, slots=True)
class S2JobConflictError(RuntimeError):
    """Raised when an S2 operation cannot run for the current job state."""

    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class S2ManifestNotFoundError(S2JobConflictError):
    """Raised when a local S2 job artifact is unavailable."""


@dataclass(frozen=True, slots=True)
class S2ReviewValidationError(ValueError):
    """Raised when candidate markings fail S2 validation."""

    detail: str

    def __str__(self) -> str:
        return self.detail


class S2ReviewServiceResponse(TypedDict):
    """Serialized common response returned by the S2 review service."""

    job_id: str
    project_id: str | None
    review: dict[str, Any]
    candidates: list[dict[str, Any]]
    active_count: int
    candidate_manifest: str
    structures_dir: str
    result_manifest: str


def resolve_s2_manifest_for_job(
    manager: JobManager,
    job_id: str,
    *,
    reject_remote: bool = True,
) -> tuple[JobRecord, Path, dict[str, Any]]:
    """Resolve and validate the local bond-scan manifest for a PESsearch job."""
    record = manager.get(job_id)
    if record is None:
        raise S2JobNotFoundError(job_id)
    if record.spec.workflow != "PESsearch":
        raise S2JobConflictError(f"Job {job_id} is not a PESsearch job")
    if str(record.spec.method.get("mode") or "") != "bond_length_scan":
        raise S2JobConflictError(f"Job {job_id} is not a bond_length_scan job")
    if reject_remote and manager._is_remote_job(record):
        raise S2JobConflictError("远程任务的 S2 候选编辑暂不支持：结果文件仍在远程节点")

    work_dir = Path(record.work_dir) if record.work_dir else None
    if work_dir is None or not work_dir.is_dir():
        raise S2ManifestNotFoundError(f"Job has no work dir: {job_id}")
    manifest_path = work_dir / "RESULT" / "mechanism" / "s2_path_manifest.json"
    if not manifest_path.is_file():
        raise S2ManifestNotFoundError(f"No s2_path_manifest.json found for job {job_id}")
    try:
        payload = read_scan_manifest(manifest_path)
    except ValueError as exc:
        raise S2ReviewValidationError(str(exc)) from exc
    return record, manifest_path, payload


def review_candidates(
    job_id: str,
    payload: dict[str, Any],
    marks: list[S2CandidateMark],
    selected_ts: list[str],
    selected_intermediates: list[str],
) -> list[ReviewCandidate]:
    scan_frames = (payload.get("scan") or {}).get("frames") or []
    try:
        known_frames = {
            int(frame["index"])
            for frame in scan_frames
            if isinstance(frame, dict) and frame.get("index") is not None
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise S2ReviewValidationError(f"S2 scan frames are invalid for job {job_id}") from exc

    recommendation_by_id = {
        str(recommendation.get("candidate_id")): recommendation
        for group in ("ts", "intermediates")
        for recommendation in (payload.get("recommendations") or {}).get(group) or []
        if isinstance(recommendation, dict) and recommendation.get("candidate_id")
    }
    resolved_marks = list(marks)
    if not resolved_marks:
        for candidate_id in [*selected_ts, *selected_intermediates]:
            recommendation = recommendation_by_id.get(candidate_id)
            if recommendation is None:
                raise S2ReviewValidationError(f"Unknown candidate ids: {candidate_id}")
            frame_value = recommendation.get("frame_index")
            frame_index = int(frame_value) if frame_value is not None else 0
            role = "ts" if candidate_id in selected_ts else "intermediate"
            resolved_marks.append(
                S2CandidateMark(
                    candidate_id=candidate_id,
                    frame_index=frame_index,
                    role=role,
                )
            )

    result: list[ReviewCandidate] = []
    for mark in resolved_marks:
        try:
            frame_index = int(mark.frame_index)
        except (TypeError, ValueError) as exc:
            raise S2ReviewValidationError(
                f"Invalid frame_index {mark.frame_index!r} for job {job_id}"
            ) from exc
        if frame_index not in known_frames:
            raise S2ReviewValidationError(f"Unknown frame_index {frame_index} for job {job_id}")
        try:
            result.append(
                ReviewCandidate(
                    candidate_id=mark.candidate_id,
                    frame_index=frame_index,
                    role=mark.role,
                    name=mark.name,
                )
            )
        except ValueError as exc:
            raise S2ReviewValidationError(str(exc)) from exc
    return result


__all__ = [
    "S2CandidateMark",
    "S2JobConflictError",
    "S2JobNotFoundError",
    "S2ManifestNotFoundError",
    "S2ReviewServiceResponse",
    "S2ReviewValidationError",
    "resolve_s2_manifest_for_job",
    "review_candidates",
]
