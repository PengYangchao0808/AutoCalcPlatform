"""Shared S2 candidate-review persistence services."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
from __future__ import annotations

from acp.scheduler.manager import JobManager

from .s2_confirm_support import (
    S2CandidateMark,
    S2JobConflictError,
    S2JobNotFoundError,
    S2ManifestNotFoundError,
    S2ReviewServiceResponse,
    S2ReviewValidationError,
    resolve_s2_manifest_for_job,
    review_candidates,
)
from .scan_manifest import (
    materialize_s2_candidates,
)


def save_s2_review_for_job(
    manager: JobManager,
    job_id: str,
    candidates: list[S2CandidateMark],
    note: str | None = None,
    *,
    selected_ts: list[str] | None = None,
    selected_intermediates: list[str] | None = None,
    mechanism_project_id: str | None = None,
) -> S2ReviewServiceResponse:
    """Save a frame-indexed S2 review and materialize its candidate package."""
    record, manifest_path, payload = resolve_s2_manifest_for_job(manager, job_id)
    resolved_candidates = review_candidates(
        job_id,
        payload,
        candidates,
        selected_ts or [],
        selected_intermediates or [],
    )
    try:
        summary = materialize_s2_candidates(
            manifest_path,
            payload,
            resolved_candidates,
            note=note,
        )
    except ValueError as exc:
        raise S2ReviewValidationError(str(exc)) from exc

    rows = [row for row in summary["candidates"] if isinstance(row, dict)]
    active_count = sum(1 for row in rows if row.get("active"))
    project_id = getattr(record.spec, "mechanism_project_id", None) or mechanism_project_id
    if project_id and active_count:
        manager._mechanism_projects.confirm_s2_candidates(project_id)
    return {
        "job_id": job_id,
        "project_id": project_id,
        "review": dict(summary["review"]),
        "candidates": rows,
        "active_count": active_count,
        "candidate_manifest": str(summary["candidate_manifest"]),
        "structures_dir": str(summary["structures_dir"]),
        "result_manifest": str(summary["result_manifest"]),
    }


__all__ = [
    "S2CandidateMark",
    "S2JobConflictError",
    "S2JobNotFoundError",
    "S2ManifestNotFoundError",
    "S2ReviewServiceResponse",
    "S2ReviewValidationError",
    "resolve_s2_manifest_for_job",
    "save_s2_review_for_job",
]
