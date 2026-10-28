"""B1-4 — the v1 attempt-status read surface.

This router is deliberately **not** mounted on the shared ``v1_router``: that
router inherits ``dependencies=[Depends(_job_path_authorization)]``, while this
module owns its explicit gate chain.  Every entry therefore performs, in order:

1. ``attempt >= 1`` input validation (400 ``input_invalid``);
2. job scope access (unknown or cross-project job → 404 that never leaks
   existence);
3. project scoping against the verified server identity (404 on mismatch);
4. resolution of the attempt's controlled read root through the read-only
   resolver — never ``_job_read_root``, which fetches a remote catalog on a
   terminal job and would turn a GET into a write/network side effect;
5. projection through :func:`acp.api.science_projection.attempt_status`, with
   the wire payload filtered to the four fields the frontend consumes.

The route never creates a task, prepares a resource, transfers a file or
fetches a remote catalog.  ``manager.store.counts()`` is identical before and
after any number of GETs (pinned by ``tests/test_acp2_v1_science_status.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from acp.api import science_projection
from acp.api.v1_routes import (
    _command_error,
    _manager,
    _require_job_scope_access,
    _require_verified_project,
    _scoped_read_project,
)
from acp.services_data_bootstrap import build_read_root_resolver
from acp_services.contracts.access import AccessAction as _AccessAction

__all__ = ["router", "science_attempt_status"]

logger = logging.getLogger(__name__)

router = APIRouter()

#: ``attempt_status`` value errors that mean "no such attempt for this task".
_NOT_FOUND_ATTEMPT_CODES = frozenset({"attempt_not_found", "attempt_not_current"})

#: Wire keys of ``task_ref`` (``release`` is deliberately absent).
_TASK_REF_WIRE_KEYS = ("job_id", "attempt", "task_id")


def _value_error_code(exc: ValueError) -> str:
    """Return the stable code carried by ``attempt_status``'s ``ValueError``."""
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def _attempt_history_error(exc: BaseException) -> HTTPException:
    """Map an unprovable historical attempt to the frozen 409 contract."""
    reason = getattr(exc, "code", None) or "attempt_history_unavailable"
    return _command_error(
        409,
        "resource_not_ready",
        "this attempt's history cannot be proven",
        params={"reason": str(reason)},
    )


def _status_wire_payload(payload: Any, *, job_id: str, attempt: int) -> dict[str, Any]:
    """Filter the projection to exactly the four frozen wire fields.

    ``attempt_status`` already returns the minimal shape, but re-projecting
    here is the hard guarantee that no internal field (``work_dir``, ``pid``,
    ``host``, ``remote_job_id`` or ``task_ref.release``) can ever leak if the
    projection grows an additive internal field later.
    """
    projection = payload if isinstance(payload, dict) else {}
    raw_ref = projection.get("task_ref")
    task_ref = raw_ref if isinstance(raw_ref, dict) else {}
    filtered_ref = {key: task_ref.get(key) for key in _TASK_REF_WIRE_KEYS}
    filtered_ref["job_id"] = filtered_ref.get("job_id") or job_id
    filtered_ref["attempt"] = attempt
    revision = projection.get("revision")
    return {
        "schema_version": projection.get("schema_version", 1),
        "task_ref": filtered_ref,
        "status": projection.get("status"),
        "revision": revision if isinstance(revision, int) else int(revision or 0),
    }


@router.get("/science/tasks/{job_id}/attempts/{attempt}/status")
def science_attempt_status(
    job_id: str,
    attempt: int,
    request: Request,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Return the read-only status snapshot of one task attempt.

    Args:
        job_id: Task id; unknown and cross-project ids share the same 404.
        attempt: 1-based attempt number; ``< 1`` is a 400 input error.
        request: Live request (job manager, server identity).
        project_id: Optional project hint, narrowed to the verified scope.

    Returns:
        ``{"schema_version", "task_ref", "status", "revision"}`` — never a
        ``work_dir``/``pid``/``host``/``remote_job_id``/``release`` field.
    """
    if attempt < 1:
        raise _command_error(
            400,
            "input_invalid",
            "attempt must be >= 1",
            params={"field": "attempt"},
        )

    record = _require_job_scope_access(
        request,
        job_id,
        action=_AccessAction.READ,
        resource_id="science.status",
    )
    if record is None:
        # Unreachable: ``require_exists`` defaults to True, so the helper has
        # already raised this exact 404 — kept to narrow the union for mypy.
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manager = _manager(request)

    resolved = _scoped_read_project(request, project_id)
    _require_verified_project(request, resolved)
    if resolved is not None:
        record_project = record.project_id or record.spec.project_id or manager.default_project_id
        if str(resolved) != str(record_project):
            # Same shape as an unknown project: existence never leaks.
            raise HTTPException(status_code=404, detail="Project not found")

    resolver = build_read_root_resolver(
        job_lookup=manager.get,
        cache=manager.structure_cache,
    )
    root = resolver.resolve(job_id, attempt)

    try:
        payload = science_projection.attempt_status(record, attempt, root)
    except science_projection.AttemptHistoryUnavailable as exc:
        logger.info(
            "science attempt status unprovable: job=%s attempt=%s reason=%s",
            job_id,
            attempt,
            getattr(exc, "code", ""),
        )
        raise _attempt_history_error(exc) from exc
    except ValueError as exc:
        code = _value_error_code(exc)
        if code in _NOT_FOUND_ATTEMPT_CODES:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
        if code == "invalid_attempt":
            # Unreachable: step 1 already rejects ``attempt < 1``.
            raise _command_error(
                400,
                "input_invalid",
                "attempt must be >= 1",
                params={"field": "attempt"},
            ) from exc
        raise _attempt_history_error(exc) from exc

    return _status_wire_payload(payload, job_id=job_id, attempt=attempt)
