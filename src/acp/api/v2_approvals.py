"""Versioned application endpoints for persistent proposal approvals (B16).

The approval resource is a Runtime fact bound to a frozen scientific plan.  It
is deliberately *not* a task state: no endpoint here creates or mutates a job,
and no ``JobStatus``/``WAITING_REVIEW`` value is involved.

Endpoints (all additive under ``/api/v2``):

============================================  ============================
``POST /research/approvals``                  request/query approval (R2)
``GET  /research/approvals``                  bounded query
``GET  /research/approvals/{approval_ref}``   read one approval
``POST /research/approvals/{approval_ref}/approve``   decision (R3)
``POST /research/approvals/{approval_ref}/reject``    decision (R3)
``POST /research/approvals/{approval_ref}/revoke``    revoke (R3)
============================================  ============================

Identity rules (frozen by B15 and the P2 contract): the principal comes from the
authenticated Controller channel, never from the payload.  A payload field that
names an approver/role/tenant is refused with
``access_denied(rule=approver_identity_from_payload)``.  A model cannot approve
on behalf of a human and a chat reply is never read by these endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from acp.api.v1_routes import (
    _EntryClass,
    _manager,
    _require_entry_access,
    _require_submit_access,
    _research_project,
    _server_identity,
)
from acp_services.runtime.approvals import (
    ApprovalError,
    ApprovalNotFoundError,
    ApprovalService,
)
from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    CanonicalDigestError,
)
from acp_services.runtime.canonical_digest import (
    plan_digest as canonical_plan_digest,
)
from acp_services.runtime.canonical_digest import (
    proposal_digest as canonical_proposal_digest,
)

router = APIRouter()

#: Payload fields that would impersonate the authenticated approver.
_FORBIDDEN_IDENTITY_FIELDS = (
    "approver",
    "approver_ref",
    "approver_id",
    "approver_source",
    "approved_by",
    "role",
    "roles",
    "tenant",
    "tenant_id",
    "principal_id",
)


class ApprovalRequestBody(BaseModel):
    """Approval request/query payload (additive P2 contract)."""

    model_config = ConfigDict(extra="allow")

    proposal_ref: str
    proposal: dict[str, Any] | None = None
    proposal_digest: str | None = None
    plan: dict[str, Any] | None = None
    plan_digest: str | None = None
    project_id: str | None = None
    budget: dict[str, Any] | None = None
    allowed_change_range: dict[str, Any] | None = None
    expires_at: str | None = None
    idempotency_key: str | None = None
    draft: bool = False


class ApprovalDecisionBody(BaseModel):
    """Approve/reject/revoke payload.  No identity field is accepted."""

    model_config = ConfigDict(extra="allow")

    expected_revision: int = Field(ge=1)
    reason: str | None = None
    idempotency_key: str | None = None


def _approval_service(request: Request) -> ApprovalService:
    manager = _manager(request)
    db_path = str(manager.store.db_path)
    cached = getattr(request.app.state, "approval_service", None)
    if cached is None or str(getattr(cached, "db_path", "")) != db_path:
        cached = ApprovalService(manager.store.db_path)
        request.app.state.approval_service = cached
    return cached


def _principal(request: Request) -> str:
    return str(_server_identity(request).principal.principal_id)


def _http_error(exc: ApprovalError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.detail())


def _reject_payload_identity(body: BaseModel) -> None:
    extra = getattr(body, "model_extra", None) or {}
    claimed = sorted(
        name for name in _FORBIDDEN_IDENTITY_FIELDS if extra.get(name) not in (None, "", [])
    )
    if claimed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": "approver identity must come from the authenticated channel",
                "params": {
                    "rule": "approver_identity_from_payload",
                    "fields": claimed,
                },
                "retryable": False,
            },
        )


def _digest_error(exc: CanonicalDigestError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "input_invalid",
            "message": str(exc),
            "params": {
                "rule": exc.rule,
                "canonical_digest_version": CANONICAL_DIGEST_VERSION,
            },
            "retryable": False,
        },
    )


def _resolve_project(request: Request, project_hint: str | None) -> str:
    resolved = _require_submit_access(request, project_hint)
    manager = _manager(request)
    project = resolved or project_hint or manager.default_project_id
    if not project:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": "a research approval requires a project",
                "params": {"rule": "project_scope_mismatch"},
                "retryable": False,
            },
        )
    # The project must exist; an unknown claim is not a scope.
    _research_project(manager, str(project))
    return str(project)


def _approval_projection(approval: Any, *, replayed: bool = False) -> dict[str, Any]:
    payload = approval.to_dict()
    payload["schema_version"] = 1
    payload["replayed"] = bool(replayed)
    return payload


def _compute_request_digests(
    body: ApprovalRequestBody,
) -> tuple[str, str, dict[str, Any] | None]:
    plan_definition = dict(body.plan) if body.plan is not None else None
    if plan_definition is not None:
        if body.plan_digest is not None:
            try:
                computed = canonical_plan_digest(plan_definition).digest
            except CanonicalDigestError as exc:
                raise _digest_error(exc) from exc
            if computed != body.plan_digest:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "input_invalid",
                        "message": "plan_digest does not match the frozen plan definition",
                        "params": {"field": "plan_digest", "rule": "plan_digest_mismatch"},
                        "retryable": False,
                    },
                )
        try:
            plan_digest = canonical_plan_digest(plan_definition).digest
        except CanonicalDigestError as exc:
            raise _digest_error(exc) from exc
    else:
        plan_digest = str(body.plan_digest or "")

    if body.proposal is not None:
        try:
            computed_proposal = canonical_proposal_digest(body.proposal).digest
        except CanonicalDigestError as exc:
            raise _digest_error(exc) from exc
        if body.proposal_digest is not None and computed_proposal != body.proposal_digest:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "input_invalid",
                    "message": "proposal_digest does not match the semantic projection",
                    "params": {"field": "proposal_digest", "rule": "proposal_digest_mismatch"},
                    "retryable": False,
                },
            )
        proposal = body.proposal
        proposal_digest = computed_proposal
        claimed_plan = proposal.get("plan_digest")
        if claimed_plan is not None and plan_digest and claimed_plan != plan_digest:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "input_invalid",
                    "message": "the proposal references a different plan_digest",
                    "params": {"field": "plan_digest", "rule": "plan_digest_mismatch"},
                    "retryable": False,
                },
            )
    else:
        proposal_digest = str(body.proposal_digest or "")

    if not plan_digest or not proposal_digest:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "input_invalid",
                "message": "plan_digest and proposal_digest are required",
                "params": {"rule": "missing_science_field"},
                "retryable": False,
            },
        )
    return proposal_digest, plan_digest, plan_definition


@router.post("/research/approvals", status_code=201)
def request_approval(body: ApprovalRequestBody, request: Request) -> dict[str, Any]:
    """Persist (or replay) the approval request for a frozen plan."""
    _reject_payload_identity(body)
    project_id = _resolve_project(request, body.project_id)
    proposal_digest, plan_digest, plan_definition = _compute_request_digests(body)
    principal = _principal(request)
    service = _approval_service(request)
    try:
        if body.draft:
            approval = service.create_draft(
                proposal_ref=body.proposal_ref,
                proposal_digest=proposal_digest,
                plan_digest=plan_digest,
                project_id=project_id,
                requester_principal=principal,
                plan_definition=plan_definition,
                budget=body.budget,
                allowed_change_range=body.allowed_change_range,
                expires_at=body.expires_at,
                idempotency_key=body.idempotency_key,
            )
            return _approval_projection(approval)
        approval, replayed = service.request(
            proposal_ref=body.proposal_ref,
            proposal_digest=proposal_digest,
            plan_digest=plan_digest,
            project_id=project_id,
            requester_principal=principal,
            plan_definition=plan_definition,
            budget=body.budget,
            allowed_change_range=body.allowed_change_range,
            expires_at=body.expires_at,
            idempotency_key=body.idempotency_key,
        )
    except ApprovalError as exc:
        raise _http_error(exc) from exc
    return _approval_projection(approval, replayed=replayed)


@router.get("/research/approvals")
def query_approvals(
    request: Request,
    approval_ref: str | None = Query(default=None),
    proposal_ref: str | None = Query(default=None),
    status: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Bounded query over the authoritative approval records."""
    manager = _manager(request)
    project = project_id or getattr(_server_identity(request), "verified_project", None)
    project = project or manager.default_project_id
    if not project:
        raise HTTPException(status_code=403, detail={"code": "access_denied"})
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        project_id=str(project),
        resource_id="approvals",
    )
    principal = _principal(request)
    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID

    scoped_principal = None if principal == LOCAL_PRINCIPAL_ID else principal
    try:
        approvals = _approval_service(request).query(
            project_id=str(project),
            principal_id=scoped_principal,
            approval_ref=approval_ref,
            proposal_ref=proposal_ref,
            status=status,
            limit=limit,
        )
    except ApprovalError as exc:
        raise _http_error(exc) from exc
    records = [_approval_projection(item) for item in approvals]
    return {
        "schema_version": 1,
        "approvals": records,
        "snapshot_revision": max((item.revision for item in approvals), default=0),
        "canonical_digest": {
            "spec_id": "acp2.canonical-digest.v1",
            "version": CANONICAL_DIGEST_VERSION,
        },
    }


@router.get("/research/approvals/{approval_ref}")
def get_approval(approval_ref: str, request: Request) -> dict[str, Any]:
    service = _approval_service(request)
    approval = service.get(approval_ref)
    if approval is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        project_id=approval.project_id,
        resource_id="approval",
    )
    principal = _principal(request)
    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID

    if principal != LOCAL_PRINCIPAL_ID and principal not in {
        approval.requester_principal,
        approval.approver_principal,
    }:
        # Same not-found shape: no cross-project/cross-principal existence oracle.
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return _approval_projection(approval)


def _decision(
    request: Request,
    approval_ref: str,
    body: ApprovalDecisionBody,
    *,
    outcome: str,
) -> dict[str, Any]:
    _reject_payload_identity(body)
    service = _approval_service(request)
    approval = service.get(approval_ref)
    if approval is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    identity = _server_identity(request)
    decision = identity.authorize_job_project(
        approval.project_id,
        action="control",
        job_id=approval_ref,
        resource_id="approval_decision",
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": "approval permission is required",
                "params": {"reason_code": decision.reason_code},
                "retryable": False,
            },
        )
    actor = _principal(request)
    try:
        if outcome == "approved":
            updated, replayed = service.approve(
                approval_ref,
                approver_principal=actor,
                expected_revision=body.expected_revision,
                reason=body.reason,
            )
        elif outcome == "rejected":
            updated, replayed = service.reject(
                approval_ref,
                approver_principal=actor,
                expected_revision=body.expected_revision,
                reason=body.reason,
            )
        else:
            updated, replayed = service.revoke(
                approval_ref,
                actor_principal=actor,
                expected_revision=body.expected_revision,
                reason=body.reason,
            )
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "not_found"}) from exc
    except ApprovalError as exc:
        raise _http_error(exc) from exc
    return _approval_projection(updated, replayed=replayed)


@router.post("/research/approvals/{approval_ref}/approve")
def approve_approval(
    approval_ref: str, body: ApprovalDecisionBody, request: Request
) -> dict[str, Any]:
    return _decision(request, approval_ref, body, outcome="approved")


@router.post("/research/approvals/{approval_ref}/reject")
def reject_approval(
    approval_ref: str, body: ApprovalDecisionBody, request: Request
) -> dict[str, Any]:
    return _decision(request, approval_ref, body, outcome="rejected")


@router.post("/research/approvals/{approval_ref}/revoke")
def revoke_approval(
    approval_ref: str, body: ApprovalDecisionBody, request: Request
) -> dict[str, Any]:
    return _decision(request, approval_ref, body, outcome="revoked")


__all__ = ["router"]
