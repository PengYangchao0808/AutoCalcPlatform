"""Controller model-gateway entry (M4-02 composition of the M3-03 gateway).

The M3-03 :class:`~acp_services.runtime.model_gateway.ModelGateway` is the only
place a planning model request is reserved, charged and receipted.  This module
is its **Controller composition root and transport binding**: DSH/RPH never hold
a provider credential (they call these two routes), and the provider is reached
only by the gateway's own injected transport.

Wire contract (frozen by M3-02/M3-03, consumed by
``rph/packages/dsh-bundle/skills/model-planner.ts``)::

    POST /api/v2/model/plan
      {phase, round, messages[], max_output_tokens, model?, session_seed?,
       project_id?, research_id?}
    -> {output, usage, receipt_id, receipt, identity, budget}

Identity is **server-derived**: ``project_id`` can only narrow the verified
scope (the same :func:`acp.api.v1_routes._require_submit_access` gate as every
other submit), the client's ``research_id`` is never used to select a
reservation scope, and ``call_seq`` is allocated by the Controller.  A client
can therefore spend its own allowance, never another principal's, and never
rewrite a claim.

``GET /api/v2/model/usage`` is the Supervisor's authoritative planning count
(``model_requests``) plus the secret-free credential state and the receipt
summaries of that budget.  The provider key is never returned, logged or
receipted.

When no approved credential is configured the routes answer ``503`` with the
gateway's typed refusal and make **zero** provider calls; they never fabricate
a receipt and never fall back to a local settlement.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from acp.api.v1_routes import _EntryClass, _require_entry_access, _require_submit_access
from acp_services.runtime.model_gateway import (
    DEFAULT_MODEL_BASE_URL,
    ChatCompletionsTransport,
    ModelBudgetDeniedError,
    ModelCallUnknownError,
    ModelCredentialUnavailableError,
    ModelGateway,
    ModelGatewayConflictError,
    ModelGatewayTransportFailureError,
    ModelGatewayValidationError,
    ModelPlanningRequest,
)
from acp_services.runtime.research_session import ResearchIdentity
from acp_services.workspace.command_identity import CommandIdentity
from acp_services.workspace.command_identity import EntryClass as _CommandEntryClass

router = APIRouter()

#: Contract identity of the route document (not the internal gateway schema).
MODEL_PLAN_ROUTE_SCHEMA = "acp2.api.model-plan.v1"
MODEL_USAGE_SCHEMA = "acp2.api.model-usage.v1"

#: Default cumulative planning-call cap for one planning session (M4-02 cap).
DEFAULT_MODEL_REQUEST_LIMIT = 20
#: Per-request output cap when the client does not ask for one.
DEFAULT_MAX_OUTPUT_TOKENS = 800
#: Upper bound the client may request per call (never clamped silently).
MAX_OUTPUT_TOKENS_CEILING = 4096

#: One Controller process owns one gateway; the call_seq allocator is
#: process-wide so two concurrent plan requests can never share a claim.
_ALLOCATION_LOCK = threading.Lock()


def _limit_from_env(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = str(env.get("ACP2_MODEL_REQUEST_LIMIT", "") or "").strip()
    if not raw:
        return DEFAULT_MODEL_REQUEST_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MODEL_REQUEST_LIMIT
    return value if value >= 0 else DEFAULT_MODEL_REQUEST_LIMIT


def build_controller_model_gateway(ledger: Any, run_root: Any) -> ModelGateway:
    """Compose the one Controller gateway over the admission ledger.

    Args:
        ledger: The Controller's durable ``ReservationLedger`` (never a second
            ledger — planning reservations live in the admission database).
        run_root: Controller data root; receipts/claim rows go to
            ``<run_root>/model-gateway`` (never a job directory).
    """
    from pathlib import Path

    base_url = str(os.environ.get("ACP2_MODEL_BASE_URL", DEFAULT_MODEL_BASE_URL) or "")
    timeout_s = float(os.environ.get("ACP2_MODEL_TIMEOUT_S", "90") or "90")
    return ModelGateway(
        ledger,
        transport=ChatCompletionsTransport(base_url=base_url, timeout_s=timeout_s),
        receipt_root=Path(run_root) / "model-gateway",
        model_request_limit=_limit_from_env(),
        model=str(os.environ.get("ACP2_MODEL_ID", "") or "").strip() or None,
        base_url=base_url,
    )


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class ModelPlanBody(BaseModel):
    """One planning call as DSH sends it (no credential, no execution fact)."""

    model_config = ConfigDict(extra="ignore")

    phase: str = "plan"
    round: int = 0
    messages: list[_Message] = Field(min_length=1)
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    model: str = ""
    session_seed: str = ""
    project_id: str | None = None
    #: Accepted as a *hint* only; it never selects the reservation scope.
    research_id: str = ""


def _gateway(request: Request) -> ModelGateway:
    gateway = getattr(request.app.state, "model_gateway", None)
    if gateway is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "model_gateway_unavailable",
                "message": "the Controller has no model gateway (not composed)",
                "retryable": False,
            },
        )
    return gateway


def _command_identity(request: Request, project: str | None) -> CommandIdentity:
    resolved = getattr(request.state, "request_identity", None)
    if resolved is not None:
        try:
            return CommandIdentity.from_verified_http(
                resolved, project_id=project, entry_class=_CommandEntryClass.HTTP
            )
        except TypeError:  # pragma: no cover - defensive: unverified shape
            pass
    server = getattr(request.app.state, "server_identity", None)
    if server is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "identity_unavailable",
                "message": "the Controller has no server identity",
                "retryable": False,
            },
        )
    return CommandIdentity.from_server(
        server, project_id=project, entry_class=_CommandEntryClass.CONTROLLER
    )


def _identity_document(identity: ResearchIdentity) -> dict[str, Any]:
    return {
        "principal_id": identity.principal_id,
        "project_id": identity.project_id,
        "research_id": identity.research_id,
        "session_id": identity.session_id,
    }


def _budget_document(
    gateway: ModelGateway, identity: ResearchIdentity, *, round: int
) -> dict[str, Any]:
    return gateway.planning_usage(identity, round=round)


def _refusal(exc: Exception) -> HTTPException:
    """Map one typed gateway refusal to a stable HTTP answer."""
    if isinstance(exc, ModelCredentialUnavailableError):
        status = 503
    elif isinstance(exc, ModelBudgetDeniedError):
        status = 429
    elif isinstance(exc, ModelCallUnknownError):
        status = 409
    elif isinstance(exc, ModelGatewayConflictError):
        status = 409
    elif isinstance(exc, ModelGatewayValidationError):
        status = 422
    else:  # pragma: no cover - defensive: unexpected gateway error class
        status = 400
    detail = exc.detail() if hasattr(exc, "detail") else {"code": "model_gateway_error"}
    detail = {**detail, "retryable": False}
    return HTTPException(status_code=status, detail=detail)


def _receipts_for(gateway: ModelGateway, budget_id: str) -> list[dict[str, Any]]:
    """Read this budget's persisted receipts (summaries only, no secret)."""
    root = gateway._receipt_root  # noqa: SLF001 - the gateway owns the layout
    summaries: list[dict[str, Any]] = []
    if not root.is_dir():
        return summaries
    for path in sorted(root.glob("receipt-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("budget_id") != budget_id:
            continue
        summaries.append(
            {
                "receipt_id": payload.get("receipt_id"),
                "claim_id": payload.get("claim_id"),
                "round": payload.get("round"),
                "call_seq": payload.get("call_seq"),
                "phase": payload.get("phase"),
                "model": payload.get("model"),
                "outcome": payload.get("outcome"),
                "usage": payload.get("usage"),
                "request_digest": payload.get("request_digest"),
                "recorded_at": payload.get("recorded_at"),
                "job_id": payload.get("job_id"),
                "attempt": payload.get("attempt"),
            }
        )
    return summaries


@router.post("/model/plan")
def plan_model_call(body: ModelPlanBody, request: Request) -> dict[str, Any]:
    """Reserve, call and settle one planning model request."""
    gateway = _gateway(request)
    project = _require_submit_access(request, body.project_id)
    identity = ResearchIdentity.allocate(
        _command_identity(request, project), session_seed=body.session_seed
    )
    if int(body.max_output_tokens) < 1 or int(body.max_output_tokens) > MAX_OUTPUT_TOKENS_CEILING:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_max_output_tokens",
                "message": (
                    f"max_output_tokens must be 1..{MAX_OUTPUT_TOKENS_CEILING}; "
                    "the gateway never clamps a request silently"
                ),
                "retryable": False,
            },
        )
    with _ALLOCATION_LOCK:
        call_seq = int(_budget_document(gateway, identity, round=body.round)["model_requests"]) + 1
        planning_request = ModelPlanningRequest(
            phase=str(body.phase),
            round=int(body.round),
            call_seq=call_seq,
            messages=tuple(
                {"role": message.role, "content": message.content} for message in body.messages
            ),
            model=str(body.model or ""),
            max_output_tokens=int(body.max_output_tokens),
        )
        try:
            result = gateway.call(identity, planning_request)
        except ModelGatewayTransportFailureError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    **exc.detail(),
                    "retryable": False,
                    "receipt": exc.result.receipt.to_dict(),
                },
            ) from exc
        except (
            ModelCredentialUnavailableError,
            ModelBudgetDeniedError,
            ModelCallUnknownError,
            ModelGatewayConflictError,
            ModelGatewayValidationError,
        ) as exc:
            raise _refusal(exc) from exc
    return {
        "schema": MODEL_PLAN_ROUTE_SCHEMA,
        "output": result.output,
        "usage": result.receipt.usage.to_dict(),
        "receipt_id": result.receipt.receipt_id,
        "receipt": result.receipt.to_dict(),
        "replayed": result.replayed,
        "identity": _identity_document(identity),
        "client_research_id_used": False,
        "budget": _budget_document(gateway, identity, round=int(body.round)),
    }


@router.get("/model/usage")
def model_usage(
    request: Request, session_seed: str = "", project_id: str | None = None
) -> dict[str, Any]:
    """Authoritative planning usage for the Supervisor sync point."""
    gateway = _gateway(request)
    _require_entry_access(request, _EntryClass.CACHE_READ, action="read", project_id=project_id)
    identity = ResearchIdentity.allocate(
        _command_identity(request, project_id), session_seed=session_seed
    )
    budget = _budget_document(gateway, identity, round=0)
    return {
        "schema": MODEL_USAGE_SCHEMA,
        "model_requests": int(budget["model_requests"]),
        "receipts": int(budget["receipts"]),
        "remaining": int(budget["remaining"]),
        "limit": int(budget["limit"]),
        "reserved": int(budget["reserved"]),
        "settled": int(budget["settled"]),
        "released": int(budget["released"]),
        "budget_id": str(budget["budget_id"]),
        "identity": _identity_document(identity),
        "credential": gateway.credential_state(),
        "receipt_rows": _receipts_for(gateway, str(budget["budget_id"])),
    }


@router.get("/model/statement")
def model_statement(request: Request) -> dict[str, Any]:
    """Secret-free capability statement of the Controller gateway."""
    gateway = _gateway(request)
    document = gateway.to_dict()
    document["credential"] = gateway.credential_state()
    document["provider_calls_are_never_fabricated"] = True
    return document


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MODEL_REQUEST_LIMIT",
    "MAX_OUTPUT_TOKENS_CEILING",
    "MODEL_PLAN_ROUTE_SCHEMA",
    "MODEL_USAGE_SCHEMA",
    "build_controller_model_gateway",
    "router",
]
