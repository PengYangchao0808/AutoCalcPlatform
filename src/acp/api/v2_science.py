"""Read-only ACP2 science read surfaces (B1-2 / B1-3 / B1-5 / B1-6).

This router is the Workbench's read-only projection of science metadata and
task attempts.  Every endpoint obeys the same invariants:

* **Read-only**: a GET never prepares, transfers, fetches, submits or reserves.
  ``manager.store.counts()`` is identical before and after any number of reads.
* **One read root**: attempt data is resolved through the controlled
  ``build_read_root_resolver`` factory, never ``_job_read_root`` (which fetches
  a remote catalog on a terminal job).
* **One preflight implementation**: the projection is produced by
  ``v1_routes._preflight_projection``; this module only reshapes it.
* **Project boundary first**: the credential-scoped project, the verified
  server identity and the server-side project claim are all checked before any
  data is projected, so an unknown/foreign project never leaks existence.

Endpoints (all project-scoped):

================================================================  ==============
``GET /science/capabilities``                                      capabilities
``POST /science/preflight``                                        zero-side-effect plan
``GET /science/tasks/{job_id}/attempts/{attempt}/view``            attempt view model
``GET .../attempts/{attempt}/artifacts/{artifact_id:path}/read``    prepared bytes
================================================================  ==============

The ``:path`` converter on the artifact id exists only so that a slash-bearing
id reaches :class:`~acp.api.science_projection.UnsafeArtifactId` and becomes an
explicit 400; a readable artifact id is always a single safe token.

``server.py`` mounts this router (``/api/v2``); the module never mounts itself.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from acp.api import science_catalog, science_projection
from acp.api.v1_routes import (
    _artifact_authorization_projection,
    _command_error,
    _manager,
    _preflight_projection,
    _require_entry_access,
    _require_job_scope_access,
    _require_verified_project,
    _research_project,
    _scoped_read_project,
)
from acp.api.v1_schemas import V1PreflightRequest
from acp.services_data_bootstrap import build_read_root_resolver
from acp_services.contracts.access import AccessAction as _AccessAction
from acp_services.data.artifacts import ArtifactStore
from acp_services.entrypoints.application_params import MAX_READ_BYTES as _MAX_READ_BYTES
from acp_services.workspace.entry_authz import EntryClass as _EntryClass

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()

#: Default bound for a bounded artifact read (the hard cap is ``MAX_READ_BYTES``).
_DEFAULT_MAX_BYTES = 256 * 1024

#: A required artifact digest is ``sha256:<64 hex>`` (a bare hex is normalized).
_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")

#: ``attempt_view`` value errors that mean "no such attempt for this task".
_NOT_FOUND_ATTEMPT_CODES = frozenset({"attempt_not_found", "attempt_not_current"})

#: Projection errors that mean "this attempt cannot be proven read-only".
_READINESS_REASONS = {
    science_projection.AttemptCacheIdentityUnverified: "attempt_cache_identity_unverified",
    science_projection.AttemptHistoryUnavailable: "attempt_history_unavailable",
    science_projection.ViewProvenanceUnavailable: "view_provenance_unavailable",
}

#: Wire keys of ``task_ref`` (``release`` is deliberately absent).
_TASK_REF_WIRE_KEYS = ("job_id", "attempt", "task_id")


def _resolve_read_project(request: Request, record: Any, project_id: str | None) -> Any:
    """Narrow the project hint and prove it identifies *record*'s project."""

    manager = _manager(request)
    resolved = _scoped_read_project(request, project_id)
    _require_verified_project(request, resolved)
    if resolved is not None:
        record_project = record.project_id or record.spec.project_id or manager.default_project_id
        if str(resolved) != str(record_project):
            raise HTTPException(status_code=404, detail="Project not found")
    return resolved


def _prepare_operations(request: Request) -> Any:
    service = getattr(_manager(request), "_prepare_operations", None)
    if service is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "prepare service is not available",
            retryable=True,
        )
    return service


def _artifact_store(request: Request) -> ArtifactStore:
    return ArtifactStore(_manager(request).run_root / "artifact-store")


def _capability_descriptors(request: Request) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Return the per-app capability documents (one catalog build per process).

    Delegates to :mod:`acp.api.science_catalog`, the single discovery provider
    that ``GET /api/capabilities`` also reads, so the two surfaces can never
    probe the executable seam twice or answer with different documents (M2-02).
    """

    catalog = science_catalog.science_catalog(request.app.state)
    return catalog.descriptors, catalog.domain


def _resolve_workflow(payload: Mapping[str, Any], request: Request) -> str:
    """Resolve the required workflow from the body or ``question_id``."""

    raw_workflow = payload.get("workflow")
    if isinstance(raw_workflow, str) and raw_workflow.strip():
        return raw_workflow.strip()

    raw_question = payload.get("question_id")
    if isinstance(raw_question, str) and raw_question.strip():
        question_id = raw_question.strip()
        descriptors, _ = _capability_descriptors(request)
        for spec in science_projection.question_specs(descriptors=descriptors):
            if spec.get("question_id") == question_id and isinstance(spec.get("workflow"), str):
                return str(spec["workflow"])

    raise _command_error(
        400,
        "input_invalid",
        "workflow is required or resolvable from question_id",
        params={"field": "workflow"},
    )


def _readiness_error(exc: science_projection.ScienceProjectionError) -> HTTPException:
    reason = _READINESS_REASONS.get(type(exc), "view_provenance_unavailable")
    return _command_error(
        409,
        "resource_not_ready",
        str(exc),
        params={"reason": reason},
    )


def _invalid_attempt_error() -> HTTPException:
    return _command_error(
        400,
        "input_invalid",
        "attempt must be >= 1",
        params={"field": "attempt"},
    )


def _missing_digest_error() -> HTTPException:
    return _command_error(
        400,
        "input_invalid",
        "an artifact read requires artifact_ref.digest",
        params={"field": "digest", "rule": "required"},
    )


def _normalize_digest(value: str | None) -> str:
    text = (value or "").strip().lower()
    if _DIGEST_RE.fullmatch(text) is None:
        raise _missing_digest_error()
    return text if text.startswith("sha256:") else "sha256:" + text


def _bounded_max_bytes(value: int | None) -> int:
    """Return the bounded read limit, refusing a request above the frozen cap.

    ``None``/``<1`` means the default bound; a value above
    ``capabilities.limits.max_read_bytes`` is an ``input_invalid`` refusal
    (``max_read_bytes_exceeded``) rather than a silently clamped read.
    """
    if value is None or value < 1:
        return _DEFAULT_MAX_BYTES
    if int(value) > _MAX_READ_BYTES:
        raise _command_error(
            400,
            "input_invalid",
            f"max_bytes exceeds the frozen read bound ({_MAX_READ_BYTES} bytes)",
            params={
                "field": "max_bytes",
                "rule": "max_read_bytes_exceeded",
                "limit": _MAX_READ_BYTES,
                "requested": int(value),
            },
        )
    return int(value)


def _scoped_read_root(request: Request, job_id: str, attempt: int) -> Any:
    manager = _manager(request)
    resolver = build_read_root_resolver(
        job_lookup=manager.get,
        cache=manager.structure_cache,
    )
    return resolver.resolve(job_id, attempt)


def _require_attempt_root(root: Any, job_id: str) -> None:
    """Refuse a root the resolver did not resolve for this attempt.

    ``attempt_view`` projects the task record even when the resolver reported a
    miss, so the route (which owns the resolver) must turn a miss into the
    frozen 404/409 shapes before any projection reads a stale root.
    """

    if getattr(root, "found", False) is True:
        return
    if str(getattr(root, "reason", "") or "") == "historical_attempt_missing":
        raise _command_error(
            409,
            "resource_not_ready",
            "historical attempt is unavailable",
            params={"reason": "attempt_history_unavailable"},
        )
    raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@router.get("/science/capabilities")
def get_science_capabilities(
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the stable capability document (tools, questions, descriptors)."""

    resolved = _scoped_read_project(request, project_id)
    _require_verified_project(request, resolved)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=resolved,
        action="read",
        resource_id="science.capabilities",
    )
    _research_project(_manager(request), resolved)

    descriptors, domain = _capability_descriptors(request)
    return science_projection.science_capabilities(
        descriptors=descriptors,
        domain=domain,
        analysis=science_catalog.analysis_capabilities(request.app.state),
    )


@router.post("/science/preflight")
def post_science_preflight(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the zero-side-effect plan/estimate for a normalized request."""

    resolved = _scoped_read_project(request, project_id)
    _require_verified_project(request, resolved)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=resolved,
        action="read",
        resource_id="science.preflight",
    )
    _research_project(_manager(request), resolved)

    workflow = _resolve_workflow(payload, request)
    raw_input = payload.get("input")
    raw_method = payload.get("method")
    raw_resources = payload.get("resources")
    body_input = dict(raw_input) if isinstance(raw_input, Mapping) else {}
    body_method = dict(raw_method) if isinstance(raw_method, Mapping) else {}
    body_resources = dict(raw_resources) if isinstance(raw_resources, Mapping) else {}

    preflight = V1PreflightRequest(
        schema_version=1,
        workflow=workflow,
        input=body_input,
        method=body_method,
        resources=body_resources,
        project_id=resolved,
    )
    projection = _preflight_projection(preflight, request)
    return science_projection.science_preflight(projection, resources=body_resources)


@router.get("/science/tasks/{job_id}/attempts/{attempt}/view")
def get_science_attempt_view(
    job_id: str,
    attempt: int,
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the frozen read-only view model for one task attempt."""

    if attempt < 1:
        raise _invalid_attempt_error()

    record = _require_job_scope_access(
        request,
        job_id,
        action=_AccessAction.READ,
        resource_id="science.view",
    )
    if record is None:
        # Unreachable: ``require_exists`` defaults to True, so the helper has
        # already raised this exact 404 — kept to narrow the union for mypy.
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    resolved = _resolve_read_project(request, record, project_id)
    manager = _manager(request)

    root = _scoped_read_root(request, job_id, attempt)
    _require_attempt_root(root, job_id)
    prepare_ops = _prepare_operations(request)
    artifact_store = _artifact_store(request)

    view_project = (
        str(resolved)
        if resolved is not None
        else str(record.project_id or record.spec.project_id or manager.default_project_id)
    )

    def _authorize(operation: Any) -> Any:
        return _artifact_authorization_projection(
            request,
            record,
            action=_AccessAction.READ,
            resource_id=str(operation.resource_id),
        )

    try:
        view = science_projection.attempt_view(
            record,
            attempt,
            root,
            prepare_ops,
            artifact_store,
            project_id=view_project,
            claim_authorizer=_authorize,
        )
    except (
        science_projection.AttemptCacheIdentityUnverified,
        science_projection.AttemptHistoryUnavailable,
        science_projection.ViewProvenanceUnavailable,
    ) as exc:
        logger.info(
            "science attempt view unprovable: job=%s attempt=%s reason=%s",
            job_id,
            attempt,
            _READINESS_REASONS.get(type(exc), "unknown"),
        )
        raise _readiness_error(exc) from exc
    except ValueError as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)
        if code in _NOT_FOUND_ATTEMPT_CODES:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
        if code == "invalid_attempt":
            raise _invalid_attempt_error() from exc
        raise

    return _view_wire_payload(view, job_id=job_id, attempt=attempt)


def _view_wire_payload(view: Any, *, job_id: str, attempt: int) -> dict[str, Any]:
    """Re-project ``task_ref`` so no internal field can ever leak."""

    projection = view if isinstance(view, Mapping) else {}
    raw_ref = projection.get("task_ref")
    task_ref = raw_ref if isinstance(raw_ref, Mapping) else {}
    filtered_ref = {key: task_ref.get(key) for key in _TASK_REF_WIRE_KEYS}
    filtered_ref["job_id"] = filtered_ref.get("job_id") or job_id
    filtered_ref["attempt"] = attempt
    payload = dict(projection)
    payload["task_ref"] = filtered_ref
    return payload


@router.get("/science/tasks/{job_id}/attempts/{attempt}/artifacts/{artifact_id:path}/read")
def get_science_artifact_read(
    job_id: str,
    attempt: int,
    artifact_id: str,
    request: Request,
    project_id: str | None = Query(default=None),
    generation: int = Query(default=0),
    digest: str | None = Query(default=None),
    max_bytes: int | None = Query(default=None),
) -> JSONResponse:
    """Serve one prepared artifact read-only (never prepare, transfer or fetch)."""

    if attempt < 1:
        raise _invalid_attempt_error()

    record = _require_job_scope_access(
        request,
        job_id,
        action=_AccessAction.READ,
        resource_id=artifact_id,
    )
    if record is None:
        # Unreachable: ``require_exists`` defaults to True, so the helper has
        # already raised this exact 404 — kept to narrow the union for mypy.
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    _resolve_read_project(request, record, project_id)

    _require_entry_access(
        request,
        _EntryClass.RANGE,
        job_id=job_id,
        project_id=record.project_id or record.spec.project_id,
        action="read",
        resource_id=artifact_id,
    )
    authorization = _artifact_authorization_projection(
        request,
        record,
        action=_AccessAction.READ,
        resource_id=artifact_id,
    )
    if not getattr(authorization, "allowed", False):
        raise _command_error(
            403,
            "access_denied",
            "artifact read authorization denied",
            params={"rule": "permission_revoked"},
        )

    normalized_digest = _normalize_digest(digest)
    bounded_max_bytes = _bounded_max_bytes(max_bytes)
    prepare_ops = _prepare_operations(request)
    artifact_store = _artifact_store(request)

    from acp_services.data.artifacts import (
        ArtifactAttemptMismatchError,
        ArtifactDigestMismatchError,
        ArtifactError,
        ArtifactGenerationMismatchError,
        ArtifactNotPreparedError,
        ArtifactPathRefusedError,
    )

    try:
        read = science_projection.artifact_read(
            prepare_ops,
            artifact_store,
            job_id=job_id,
            attempt=attempt,
            artifact_id=artifact_id,
            generation=generation,
            digest=normalized_digest,
            max_bytes=bounded_max_bytes,
            permission_scope="local-single",
            authorization=authorization,
        )
    except science_projection.UnsafeArtifactId as exc:
        raise _command_error(
            400,
            "input_invalid",
            str(exc),
            params={"rule": "path_traversal"},
        ) from exc
    except ArtifactPathRefusedError as exc:
        raise _command_error(
            400,
            "input_invalid",
            str(exc),
            params={"rule": "path_traversal", **exc.params},
        ) from exc
    except ArtifactGenerationMismatchError as exc:
        raise _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "generation_stale", **exc.params},
        ) from exc
    except ArtifactDigestMismatchError as exc:
        raise _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "digest_mismatch", **exc.params},
        ) from exc
    except ArtifactAttemptMismatchError as exc:
        raise _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "attempt_mismatch", **exc.params},
        ) from exc
    except ArtifactNotPreparedError as exc:
        detail_reason = str(exc.params.get("reason") or "not_prepared")
        raise _command_error(
            409,
            "resource_not_ready",
            f"not_prepared: {detail_reason}",
            params={**exc.params, "reason": "not_prepared"},
            retryable=True,
        ) from exc
    except ArtifactError as exc:
        raise _command_error(
            500,
            "resource_not_ready" if exc.http_status == 409 else "internal_error",
            str(exc),
            params=dict(exc.params),
        ) from exc

    return JSONResponse(content=read)
