"""Research-budget projection, evidence export and receipt settlement (B17).

The durable ledger is the Controller's existing
``acp_services.runtime.admission.ReservationLedger`` database; these endpoints
add no second ledger.  A budget is quoted from the persisted B16 approval
(project + proposal), so a client can never widen the approved range.

Trust boundary
--------------

``POST .../settle`` accepts one *receipt* for a reservation this Controller
issued.  The receipt id is deterministic (``job/attempt/submission``), so a
duplicate delivery is a no-op.  Model-token enforcement stays ``NOT_VERIFIED``:
the model-call proxy is C-owned and B never proxies tokens.
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
from acp_services.runtime.usage import UsageUnitCounts, UsageValidationError

router = APIRouter()

_TRUSTED_RECEIPT_SOURCES = frozenset(
    {"controller-terminal-receipt", "worker-receipt", "c-proxy-usage"}
)


class BudgetSettlementBody(BaseModel):
    """One trusted receipt delivered for an existing budget reservation."""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str = Field(min_length=1, max_length=256)
    reservation_id: str = Field(min_length=1, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    trusted_source: str = "worker-receipt"
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
    source = str(body.trusted_source or "").strip()
    if source not in _TRUSTED_RECEIPT_SOURCES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "input_invalid",
                "message": f"untrusted receipt source {source!r}",
                "params": {"allowed": sorted(_TRUSTED_RECEIPT_SOURCES)},
            },
        )
    try:
        counters = UsageUnitCounts.from_mapping(body.usage)
    except UsageValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "input_invalid", "message": str(exc)},
        ) from exc
    settlement = _admission(request).ledger.settle_research_budget(
        budget_id,
        receipt_id=body.receipt_id,
        reservation_id=body.reservation_id,
        counters=counters,
        run_id=str(body.run_id or ""),
        trusted_source=source,
    )
    if settlement.reason_code == "budget_reservation_not_found":
        raise HTTPException(status_code=404, detail="Budget reservation not found")
    if settlement.reason_code == "budget_reservation_released":
        raise HTTPException(status_code=409, detail="Budget reservation was released")
    refreshed = _projection_or_404(request, budget_id)
    return {
        "schema": "acp2.research-budget-settlement.v1",
        "settlement": settlement.to_dict(),
        "budget": refreshed.to_dict(),
    }
