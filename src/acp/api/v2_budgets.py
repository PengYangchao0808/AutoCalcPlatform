"""Research-budget projection, evidence export and receipt settlement (B17/M0-07).

The durable ledger is the Controller's existing
``acp_services.runtime.admission.ReservationLedger`` database; these endpoints
add no second ledger.  A budget is quoted from the persisted B16 approval
(project + proposal), so a client can never widen the approved range.

Trust boundary (M0-07 / G26)
----------------------------

``POST .../settle`` books one *trusted usage receipt* for a reservation this
Controller issued.  The authorization fact is **not** a wire field:

* the caller's producer is resolved server-side from the credential's dedicated
  ``usage.produce.<producer>`` capability metadata
  (:mod:`acp_services.runtime.usage_authority`) — an ordinary project ``write``
  key has no such capability and is refused with ``403``;
* ``controller-terminal-receipt`` is the Controller's internal source and is not
  an assignable capability, so no HTTP caller (and no project write key) can
  select it;
* the trusted source recorded on the receipt is derived from the resolved
  producer, never sent by the client;
* the receipt must present its attempt execution identity (job, attempt,
  deterministic submission, release/plan/step digests) and is verified against
  the immutable binding persisted for that ``(job, attempt)`` — reuse of
  :func:`acp_services.runtime.receipts.verify_historical_receipt`, no new
  ledger;
* usage and its research-budget settlement are written in the same admission-DB
  transaction, keyed by the deterministic receipt id, so a duplicate delivery is
  a no-op and a forged/stale receipt writes nothing.

Model-token enforcement stays ``NOT_VERIFIED``: the model-call proxy is C-owned
and B never proxies tokens.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from acp.api.v1_routes import (
    _EntryClass,
    _require_entry_access,
    _require_submit_access,
)
from acp_services.contracts.envelope import ExecutionReceipt
from acp_services.runtime.admission import UsageReceiptRejectedError
from acp_services.runtime.usage import UsageUnitCounts, UsageValidationError
from acp_services.runtime.usage_authority import (
    UntrustedUsageProducerError,
    UsageAuthority,
    UsageProducer,
    authority_for_capabilities,
    resolve_authority,
)

router = APIRouter()

#: Settlement refusals that are authorization problems (HTTP 403).
_FORBIDDEN_CODES = frozenset(
    {
        "usage_producer_untrusted",
        "usage_capability_missing",
        "usage_capability_ambiguous",
        "usage_capability_unknown",
        "usage_capability_not_assignable",
    }
)


class BudgetSettlementBody(BaseModel):
    """One trusted receipt for an existing budget reservation.

    The receipt identity is required for an external producer: ``job_id``,
    ``attempt`` and the deterministic ``submission_id`` (``sub_`` + sha256 of
    ``job:attempt``), plus the frozen release/plan/step digests.  There is
    deliberately **no** ``trusted_source`` field — that is server-derived.
    """

    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(min_length=1, max_length=256)
    reservation_id: str = Field(min_length=1, max_length=256)
    job_id: str = Field(min_length=1, max_length=256)
    attempt: int = Field(default=1, ge=1)
    submission_id: str = Field(default="", max_length=256)
    release_id: str = Field(default="", max_length=256)
    release_digest: str = Field(default="", max_length=256)
    owner: str = Field(default="", max_length=256)
    fencing_token: str = Field(default="", max_length=256)
    plan_identity: str = Field(default="", max_length=256)
    step_identities: list[str] = Field(default_factory=list)
    run_id: str | None = Field(default=None, max_length=256)
    usage: dict[str, int] = Field(default_factory=dict)


def _admission(request: Request) -> Any:
    admission = getattr(request.app.state, "admission", None)
    if admission is None:
        raise HTTPException(status_code=503, detail="admission is not initialized")
    return admission


def _projection_or_404(request: Request, budget_id: str) -> Any:
    projection = _admission(request).ledger.research_budget_projection(budget_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="Research budget not found")
    verified = _admission(request).verified_project
    if verified is not None and str(projection.policy.project_id) != str(verified):
        raise HTTPException(status_code=404, detail="Research budget not found")
    return projection


def _usage_authority(request: Request) -> UsageAuthority:
    """Resolve the caller's settlement authority (never a request field).

    ``local-single`` has no per-request credential: the Controller process is the
    internal producer and books its own terminal receipts.  In ``api-key`` mode
    the verified key must carry exactly one ``usage.produce.*`` capability;
    otherwise the settlement is refused with 403 before anything is written.
    """
    resolved = getattr(request.state, "request_identity", None)
    if resolved is None:
        return resolve_authority(producer=UsageProducer.INTERNAL)
    try:
        authority = authority_for_capabilities(getattr(resolved, "usage_capabilities", ()))
    except UntrustedUsageProducerError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    if authority is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "usage_capability_missing",
                "message": (
                    "this credential carries no usage-producer capability; a project "
                    "write key cannot write trusted usage"
                ),
                "params": {
                    "required": "usage.produce.worker|service|proxy",
                },
            },
        )
    return authority


def _receipt_from_body(body: BudgetSettlementBody) -> ExecutionReceipt:
    """Project the declared attempt identity onto the frozen receipt shape."""
    return ExecutionReceipt(
        receipt_version=1,
        job_id=body.job_id,
        attempt=int(body.attempt),
        submission_id=body.submission_id,
        state="completed",
        release_id=body.release_id,
        release_digest=body.release_digest,
        owner=body.owner,
        fencing_token=body.fencing_token,
        plan_identity=body.plan_identity,
        step_identities=tuple(body.step_identities),
    )


@router.get("/research/budgets/{budget_id}")
def get_research_budget(budget_id: str, request: Request) -> dict[str, Any]:
    """Read the server-side limits, consumed, remaining and unit capabilities."""
    projection = _projection_or_404(request, budget_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        project_id=projection.policy.project_id or None,
        resource_id=budget_id,
    )
    return {"schema": "acp2.research-budget.v1", **projection.to_dict()}


@router.get("/research/budgets/{budget_id}/evidence")
def get_research_budget_evidence(budget_id: str, request: Request) -> dict[str, Any]:
    """Bounded read-only export that remains available after science stops."""
    projection = _projection_or_404(request, budget_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        project_id=projection.policy.project_id or None,
        resource_id=budget_id,
    )
    document = _admission(request).ledger.research_budget_evidence(budget_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Research budget not found")
    return document


@router.post("/research/budgets/{budget_id}/settle")
def settle_research_budget(
    budget_id: str, body: BudgetSettlementBody, request: Request
) -> dict[str, Any]:
    """Settle one trusted receipt exactly once (duplicates never re-charge)."""
    projection = _projection_or_404(request, budget_id)
    _require_submit_access(request, projection.policy.project_id or None)
    authority = _usage_authority(request)
    try:
        counters = UsageUnitCounts.from_mapping(body.usage)
    except UsageValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "input_invalid", "message": str(exc)},
        ) from exc
    try:
        outcome = _admission(request).controller.ingest_usage_receipt(
            body.receipt_id,
            job_id=body.job_id,
            attempt=int(body.attempt),
            producer=authority.producer,
            receipt=_receipt_from_body(body),
            counters=counters,
            budget_id=budget_id,
            budget_reservation_id=body.reservation_id,
            run_id=str(body.run_id or ""),
        )
    except UsageReceiptRejectedError as exc:
        status = (
            403
            if exc.code in _FORBIDDEN_CODES
            else 409
            if exc.code
            in {
                "usage_attempt_conflict",
                "usage_receipt_conflict",
                "usage_reservation_mismatch",
                "usage_reservation_job_mismatch",
                "usage_budget_mismatch",
                "usage_budget_released",
                "usage_not_dispatched",
                "usage_receipt_invalid",
            }
            else 404
        )
        raise HTTPException(status_code=status, detail=exc.to_dict()) from exc
    refreshed = _projection_or_404(request, budget_id)
    return {
        "schema": "acp2.research-budget-settlement.v1",
        "applied": outcome.applied,
        "duplicate": outcome.duplicate,
        "producer": outcome.producer,
        "trusted_source": outcome.trusted_source,
        "chain_digest": outcome.chain_digest,
        "settlement": outcome.to_dict(),
        "budget": refreshed.to_dict(),
    }
