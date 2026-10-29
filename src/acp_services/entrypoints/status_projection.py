"""One task/operation status projection shared by CLI, HTTP and MCP.

B06 (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 7) closes
``GAP-B03-01`` for the machine query path: the local CLI used to read the
``JobStore`` row directly while the Controller path returned the HTTP row.  Both
now pass their source row through :func:`project_task_status`, so the same
logical task yields the same projection regardless of which entry answered.

The projection is intentionally a **normalisation**, not a second authority:
it never writes, never resolves a resource and never fills a fact the source row
did not carry.  Missing values stay missing.  The module is stdlib-only and
imports no :mod:`acp` code.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "STATUS_SCHEMA_VERSION",
    "attempt_release_facts",
    "project_operation_status",
    "project_task_status",
    "verified_release_facts",
]

#: Immutable execution facts an attempt writes beside its result.  The frozen
#: envelope is authoritative when present; the receipt fills older attempts.
_ATTEMPT_FACT_FILES: tuple[str, ...] = (
    "WORK/00_RUNTIME/frozen/envelope.json",
    "execution_receipt.json",
)
_RELEASE_FACT_KEYS: tuple[str, ...] = (
    "release_id",
    "release_digest",
    "plan_digest",
    "submission_id",
)

#: Wire version of the projection (additive fields only).
STATUS_SCHEMA_VERSION = 1


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _coerce_attempt(value: Any) -> int | None:
    """Best-effort attempt coercion (int/float/numeric str); ``None`` on failure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _task_id(payload: Mapping[str, Any], fallback: str = "") -> str:
    for key in ("task_id", "id", "job_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def attempt_release_facts(work_dir: str | Path | None) -> dict[str, str]:
    """Read the attempt's release/plan/submission facts from its own files.

    This is a read-only normalisation for status projections: it never writes,
    never resolves remote resources and returns an empty mapping when the
    files are absent (legacy rows, remote-unmirrored attempts).  Only string
    facts already written by the attempt are passed through.
    """
    if not work_dir:
        return {}
    root = Path(work_dir)
    facts: dict[str, str] = {}
    for relative in _ATTEMPT_FACT_FILES:
        try:
            payload = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        for key in _RELEASE_FACT_KEYS:
            if key in facts:
                continue
            value = payload.get(key)
            if isinstance(value, str) and value:
                facts[key] = value
    return facts


def verified_release_facts(read_root: str | Path | None, attempt: int) -> dict[str, str]:
    """Read release facts whose *own file* attests the requested attempt.

    Unlike :func:`attempt_release_facts` (which merges every file it can
    parse), this reader gates **each file individually**: a file whose
    ``attempt`` field is absent, not int-coercible or different from the
    requested ``attempt`` is discarded whole and contributes nothing.  That
    keeps a stale attempt-1 envelope from leaking ``release_digest`` into the
    science view of attempt 2 (plan B1-1/B1-5 "逐文件 facts").

    Read-only and stdlib-only: no network, no fetch, no writes.  Missing
    files, ``OSError`` and ``ValueError`` are skipped silently; an invalid
    ``read_root`` or ``attempt < 1`` yields ``{}``.

    Args:
        read_root: Root holding the two fact files (controlled resolver root,
            never ``record.work_dir``).
        attempt: Attempt whose facts are wanted.

    Returns:
        First non-empty string per :data:`_RELEASE_FACT_KEYS`, envelope
        before receipt.
    """
    wanted = _coerce_attempt(attempt)
    if read_root is None or wanted is None or wanted < 1:
        return {}
    root = Path(read_root)
    facts: dict[str, str] = {}
    for relative in _ATTEMPT_FACT_FILES:
        try:
            payload = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        file_attempt = _coerce_attempt(payload.get("attempt"))
        if file_attempt is None or file_attempt != wanted:
            continue
        for key in _RELEASE_FACT_KEYS:
            if key in facts:
                continue
            value = payload.get(key)
            if isinstance(value, str) and value:
                facts[key] = value
    return facts


def project_task_status(
    payload: Mapping[str, Any] | None,
    *,
    fallback_task_id: str = "",
) -> dict[str, Any]:
    """Project one task row onto the frozen status snapshot shape.

    Args:
        payload: A ``JobRecord.to_dict()`` mapping or the Controller's
            ``GET /api/v1/jobs/{id}`` row.
        fallback_task_id: Id to use when the row itself carries none.

    Returns:
        A JSON-safe projection carrying ``schema_version``, ``task_ref``,
        ``state`` (and the legacy ``status`` alias), ``revision``,
        ``updated_at`` and the pass-through read-only facts present in the row.
    """
    row = dict(payload or {})
    task_id = _task_id(row, fallback_task_id)
    attempt = _as_int(row.get("attempt"))
    if attempt is None:
        attempt = 1
    revision = _as_int(row.get("revision"))
    state = row.get("state") or row.get("status") or "unknown"
    facts = attempt_release_facts(row.get("work_dir"))
    task_ref: dict[str, Any] = {
        "task_id": task_id,
        "job_id": task_id,
        "attempt": attempt,
    }
    release_id = row.get("release_id") or facts.get("release_id")
    if isinstance(release_id, str) and release_id:
        task_ref["release"] = {
            "release_id": release_id,
            "release_digest": row.get("release_digest") or facts.get("release_digest"),
        }
    workflow = row.get("workflow")
    if not workflow and isinstance(row.get("spec"), Mapping):
        workflow = row["spec"].get("workflow")
    projection: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "task_id": task_id,
        "job_id": task_id,
        "task_ref": task_ref,
        "state": str(state),
        "status": str(state),
        "attempt": attempt,
        "revision": revision,
        "updated_at": row.get("updated_at"),
        "workflow": workflow,
        "project_id": row.get("project_id"),
        "work_dir": row.get("work_dir"),
    }
    for key in (
        "state_reason",
        "waiting",
        "artifacts",
        "artifacts_truncated",
        "error",
        "plan_digest",
        "submission_id",
        "release_id",
        "release_digest",
        "name",
        "task_name",
        # Progress projection: the Controller row already carries these for
        # local jobs; dropping them here was the M1/C7 report bug.
        "current_stage",
        "progress",
        "live_status",
        "progress_state",
        "stage_index",
        "stage_total",
        "stage_progress",
        "stage_detail",
        "stage_order",
        "latest_event",
        "snapshot_version",
    ):
        if key in row:
            projection[key] = row[key]
    for key in _RELEASE_FACT_KEYS:
        if not projection.get(key) and key in facts:
            projection[key] = facts[key]
    return projection


def project_operation_status(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project one prepare-operation row onto its frozen read-only shape."""
    row = dict(payload or {})
    operation_id = str(row.get("operation_id") or "")
    projection: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "operation_id": operation_id,
        "operation_ref": {"kind": "operation", "id": operation_id},
        "idempotency_key": row.get("idempotency_key"),
        "job_id": row.get("job_id"),
        "attempt": row.get("attempt"),
        "resource_id": row.get("resource_id"),
        "resource_kind": row.get("resource_kind"),
        "state": row.get("state"),
        "availability": row.get("availability"),
        "freshness": row.get("freshness"),
        "basis_revision": row.get("basis_revision", row.get("resource_generation", 0)),
        "expected_revision": row.get("expected_revision", row.get("resource_generation", 0)),
        "observed_at": row.get("observed_at", row.get("updated_at")),
        "updated_at": row.get("updated_at"),
        "error": row.get("error"),
        "bytes": row.get("bytes"),
        "sha256": row.get("sha256"),
        "purpose": (row.get("payload") or {}).get("purpose"),
    }
    artifact_ref = row.get("artifact_ref")
    if isinstance(artifact_ref, Mapping):
        projection["artifact_ref"] = dict(artifact_ref)
    for key in ("artifact_id", "generation", "digest"):
        if key in row and row[key] is not None:
            projection[key] = row[key]
    return projection
