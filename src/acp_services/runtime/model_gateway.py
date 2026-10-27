"""Controller-side model gateway: planning reservation + trusted usage (M3-03).

The research loop spends model budget **before** a Proposal, an approval or a
TaskRef exists, and the Supervisor's in-memory ``used.model_requests`` is not a
settlement source.  This module is the production seam that M1-04c reserved for
it:

* every ``understand`` / ``hypothesize`` / ``critique`` / ``plan`` call is
  preceded by a planning ``model_request`` reservation on the one
  :class:`~acp_services.runtime.admission.ReservationLedger`
  (:func:`~acp_services.runtime.planning_reservation.reserve_planning_call`);
* the claim/receipt identity is **reused** from M1-02
  (:func:`~acp_services.runtime.research_session.planning_claim`): the claim is
  ``plan|<project>|<research>|<session>|<round>|<call_seq>`` and the receipt is
  ``modelrcpt|…|<request_digest>``.  A planning call never fabricates a job or
  an attempt (``job_id``/``attempt`` stay empty);
* after the call the gateway records a **trusted** receipt
  (``controller-terminal-receipt``) with the reported or conservatively
  estimated token usage.  Missing usage is recorded as *unknown*, never
  fabricated; a duplicate receipt id is a no-op and a same-id/different-payload
  delivery is a typed conflict;
* an unknown outcome (upstream error, interrupted stream, lost receipt) keeps
  the reservation charged.  A retry with the same claim replays the stored
  receipt or is refused as ``call_outcome_unknown`` — it never resets the
  allowance.  A *new* call must use a new ``call_seq`` and is charged again;
* the provider credential lives only in this gateway (process environment,
  injected for tests).  It is never persisted, never returned by
  :meth:`ModelGateway.to_dict` and never written to a receipt.  DSH/RPH call the
  gateway through an injected transport and hold no provider credential.

The gateway is *reserve + account*, not an upgrade of the capability matrix:
``model_request`` remains ``recorded-only`` in
:mod:`acp_services.runtime.execution_limits` / ``enforcement_projection`` until a
later checkpoint records the real evidence.  The gateway still refuses a call
**before** it reaches the provider when its own planning allowance is exhausted,
which is exactly the zero-call counterexample the upgrade gate names — it does
not, by itself, make the row ``enforced``.

Concurrency: the Controller process owns one gateway.  State transitions
(claim index, quota check, reservation) are serialized inside the process; the
provider call itself runs outside the lock.  A second call with the same claim
while the first is in flight is refused as unknown instead of double-charging.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import, no
``cccp`` import, no subprocess).  A real HTTP transport is provided for the
Controller composition root; tests inject a controlled fake transport.

Author: ACP Services (B lane, M3-03).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.planning_reservation import (
    PlanningReservation,
    planning_budget_id_for,
    planning_budget_policy,
    reserve_planning_call,
    settle_planning_call,
)
from acp_services.runtime.research_session import (
    ResearchIdentity,
    planning_claim,
)
from acp_services.runtime.usage_units import UsageUnitCounts

__all__ = [
    "DEFAULT_MODEL_BASE_URL",
    "DEFAULT_MODEL_CREDENTIAL_ENVS",
    "MODEL_CALL_OUTCOMES",
    "MODEL_GATEWAY_SCHEMA",
    "PLANNING_MODEL_PHASES",
    "TOKEN_SOURCES",
    "ChatCompletionsTransport",
    "ModelBudgetDeniedError",
    "ModelCallReceipt",
    "ModelCallResult",
    "ModelCallUnknownError",
    "ModelCredential",
    "ModelCredentialUnavailableError",
    "ModelGateway",
    "ModelGatewayConflictError",
    "ModelGatewayError",
    "ModelGatewayTransportFailureError",
    "ModelGatewayValidationError",
    "ModelPlanningRequest",
    "ModelTokenUsage",
    "ModelTransport",
    "ModelTransportError",
    "ModelTransportInterruptedError",
    "ModelTransportRequest",
    "ModelTransportResponse",
    "estimate_input_tokens",
    "model_gateway_statement",
    "normalize_model_usage",
    "resolve_model_credential",
]

#: Contract identity of the gateway document / receipt.
MODEL_GATEWAY_SCHEMA = "acp2.runtime.model-gateway.v1"
MODEL_CALL_RECEIPT_SCHEMA = "acp2.runtime.model-call-receipt.v1"

#: The four research phases that may spend planning budget (M3-03).  A call is
#: never accepted without naming one: the gateway must run *before* the first
#: understand/hypothesize/critique/plan request.
PLANNING_MODEL_PHASES: tuple[str, ...] = ("understand", "hypothesize", "critique", "plan")

#: Environment names the gateway accepts, in order.  Names are not secrets.
DEFAULT_MODEL_CREDENTIAL_ENVS: tuple[str, ...] = (
    "ACP2_MODEL_API_KEY",
    "DEEPSEEK_API_KEY",
)
DEFAULT_MODEL_BASE_URL = "https://api.deepseek.com/v1"

#: Receipt outcomes (M1-04c / model-budget §5 vocabulary).
MODEL_CALL_OUTCOMES: tuple[str, ...] = (
    "completed",
    "upstream_error",
    "upstream_interrupted",
    "client_disconnected",
    "upstream_status",
)

#: Where the token counts came from.  ``estimated`` is never reported as measured.
TOKEN_SOURCES: tuple[str, ...] = ("provider-reported", "partial-reported", "estimated", "unknown")


class ModelGatewayError(ValueError):
    """Base gateway error with a stable machine shape (never carries a secret)."""

    code = "model_gateway_error"

    def __init__(self, rule: str, message: str, **params: Any) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message
        self.params: dict[str, Any] = dict(params)

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "params": {"rule": self.rule, **self.params},
        }


class ModelGatewayValidationError(ModelGatewayError):
    """The request or claim violates the gateway contract."""

    code = "model_gateway_invalid"


class ModelCredentialUnavailableError(ModelGatewayError):
    """No approved provider credential is configured (zero calls, no reservation)."""

    code = "model_credential_unavailable"


class ModelBudgetDeniedError(ModelGatewayError):
    """The planning allowance is exhausted; the provider is never called."""

    code = "budget_exhausted"


class ModelCallUnknownError(ModelGatewayError):
    """A claim already has an in-flight/unknown call; it stays charged."""

    code = "call_outcome_unknown"


class ModelGatewayConflictError(ModelGatewayError):
    """A claim or receipt was reused with different content (typed conflict)."""

    code = "receipt_conflict"


class ModelGatewayTransportFailureError(ModelGatewayError):
    """The provider call failed after the reservation; the receipt is retained."""

    code = "model_transport_failure"

    def __init__(self, rule: str, message: str, *, result: ModelCallResult) -> None:
        super().__init__(rule, message, receipt_id=result.receipt.receipt_id)
        self.result = result


class ModelTransportError(RuntimeError):
    """A transport-level provider failure (the request may have been billed)."""

    def __init__(
        self,
        outcome: str,
        message: str,
        *,
        usage: Mapping[str, Any] | None = None,
    ) -> None:
        chosen = str(outcome or "upstream_error")
        if chosen not in MODEL_CALL_OUTCOMES:
            raise ModelGatewayValidationError(
                "invalid_outcome", f"unknown transport outcome {outcome!r}"
            )
        super().__init__(message)
        self.outcome = chosen
        self.message = message
        self.usage = dict(usage or {})


class ModelTransportInterruptedError(ModelTransportError):
    """A stream ended early (partial usage may still be reported)."""

    def __init__(self, message: str, *, usage: Mapping[str, Any] | None = None) -> None:
        super().__init__("upstream_interrupted", message, usage=usage)


@dataclass(frozen=True, slots=True)
class ModelTokenUsage:
    """Trusted token usage of one model call (never a price or a balance)."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    source: str = "unknown"
    complete: bool = False
    estimate: bool = False

    def __post_init__(self) -> None:
        for label, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (isinstance(value, bool) or int(value) < 0):
                raise ModelGatewayValidationError(
                    "invalid_usage", f"{label} must be a non-negative integer or null"
                )
        if self.source not in TOKEN_SOURCES:
            raise ModelGatewayValidationError(
                "invalid_usage_source", f"unknown token source {self.source!r}"
            )

    @property
    def total_tokens(self) -> int:
        """Sum of the *known* values only (never a fabricated total)."""
        return int(self.input_tokens or 0) + int(self.output_tokens or 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
            "complete": self.complete,
            "estimate": self.estimate,
        }


@dataclass(frozen=True, slots=True)
class ModelPlanningRequest:
    """One planning model call (before any Proposal/approval/TaskRef)."""

    phase: str
    round: int
    call_seq: int
    messages: tuple[Mapping[str, Any], ...]
    model: str = ""
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        if str(self.phase) not in PLANNING_MODEL_PHASES:
            raise ModelGatewayValidationError(
                "invalid_phase",
                f"phase must be one of {PLANNING_MODEL_PHASES}; got {self.phase!r}",
            )
        if isinstance(self.round, bool) or int(self.round) < 0:
            raise ModelGatewayValidationError("invalid_round", "round must be >= 0")
        if isinstance(self.call_seq, bool) or int(self.call_seq) < 1:
            raise ModelGatewayValidationError("invalid_call_seq", "call_seq must be >= 1")
        if not self.messages:
            raise ModelGatewayValidationError("invalid_messages", "messages must not be empty")
        if int(self.max_output_tokens) < 1:
            raise ModelGatewayValidationError(
                "invalid_max_output_tokens", "max_output_tokens must be >= 1"
            )
        normalized: list[Mapping[str, Any]] = []
        for index, message in enumerate(self.messages):
            if not isinstance(message, Mapping):
                raise ModelGatewayValidationError(
                    "invalid_messages", f"messages[{index}] must be an object"
                )
            role = str(message.get("role") or "")
            content = message.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ModelGatewayValidationError(
                    "invalid_messages",
                    f"messages[{index}] needs role system|user|assistant and string content",
                )
            normalized.append({"role": role, "content": content})
        object.__setattr__(self, "messages", tuple(normalized))

    def digest_payload(self, model: str) -> dict[str, Any]:
        """Canonical request body whose digest the model receipt binds."""
        return {
            "phase": str(self.phase),
            "round": int(self.round),
            "call_seq": int(self.call_seq),
            "model": str(model),
            "max_output_tokens": int(self.max_output_tokens),
            "messages": [dict(message) for message in self.messages],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": str(self.phase),
            "round": int(self.round),
            "call_seq": int(self.call_seq),
            "model": str(self.model),
            "max_output_tokens": int(self.max_output_tokens),
            "messages": [dict(message) for message in self.messages],
        }


@dataclass(frozen=True, slots=True)
class ModelTransportRequest:
    """What the transport receives; the credential is ``repr=False``."""

    model: str
    messages: tuple[Mapping[str, Any], ...]
    max_output_tokens: int
    auth_token: str = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        """Secret-free projection (safe for a log line)."""
        return {
            "model": self.model,
            "messages": [dict(message) for message in self.messages],
            "max_output_tokens": int(self.max_output_tokens),
            "auth_token_present": bool(self.auth_token),
        }


@dataclass(frozen=True, slots=True)
class ModelTransportResponse:
    """A completed provider answer plus its optional usage block."""

    text: str
    usage: Mapping[str, Any] | None = None
    outcome: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ModelGatewayValidationError("invalid_response", "text must be a string")
        if str(self.outcome) not in MODEL_CALL_OUTCOMES:
            raise ModelGatewayValidationError(
                "invalid_outcome", f"unknown response outcome {self.outcome!r}"
            )
        if self.usage is not None and not isinstance(self.usage, Mapping):
            raise ModelGatewayValidationError("invalid_usage", "usage must be an object")


class ModelTransport(Protocol):
    """The injected provider seam.  It never sees a platform ledger."""

    def call(self, request: ModelTransportRequest) -> ModelTransportResponse:
        """Perform one provider call or raise :class:`ModelTransportError`."""
        ...


@dataclass(frozen=True, slots=True)
class ModelCredential:
    """The gateway-held provider credential (never serialized with the token)."""

    token: str = field(repr=False, compare=False)
    model: str
    environment: str
    base_url: str = DEFAULT_MODEL_BASE_URL

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": True,
            "model": self.model,
            "environment": self.environment,
            "base_url": self.base_url,
        }


@dataclass(frozen=True, slots=True)
class ModelCallReceipt:
    """One trusted model-call receipt (no execution fact, no secret)."""

    receipt_id: str
    claim_id: str
    budget_id: str
    reservation_id: str
    project_id: str
    research_id: str
    session_id: str
    round: int
    call_seq: int
    phase: str
    model: str
    request_digest: str
    outcome: str
    usage: ModelTokenUsage
    recorded_at: str
    trusted_source: str = "controller-terminal-receipt"
    job_id: str = ""
    attempt: int = 0

    def __post_init__(self) -> None:
        for label, value in (
            ("receipt_id", self.receipt_id),
            ("claim_id", self.claim_id),
            ("budget_id", self.budget_id),
            ("reservation_id", self.reservation_id),
        ):
            if not str(value or "").strip():
                raise ModelGatewayValidationError("missing_identity", f"{label} is required")
        if self.job_id or self.attempt:
            raise ModelGatewayValidationError(
                "fabricated_execution_fact",
                "a planning model receipt never names a job or an attempt",
            )
        if str(self.outcome) not in MODEL_CALL_OUTCOMES:
            raise ModelGatewayValidationError("invalid_outcome", "unknown receipt outcome")

    @property
    def is_execution_fact(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MODEL_CALL_RECEIPT_SCHEMA,
            "receipt_id": self.receipt_id,
            "claim_id": self.claim_id,
            "budget_id": self.budget_id,
            "reservation_id": self.reservation_id,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "round": self.round,
            "call_seq": self.call_seq,
            "phase": self.phase,
            "model": self.model,
            "request_digest": self.request_digest,
            "outcome": self.outcome,
            "usage": self.usage.to_dict(),
            "recorded_at": self.recorded_at,
            "trusted_source": self.trusted_source,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "is_execution_fact": False,
        }


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """The admitted call: reservation + settlement + trusted receipt."""

    reservation: PlanningReservation
    receipt: ModelCallReceipt
    settlement: Any | None
    output: str
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MODEL_GATEWAY_SCHEMA,
            "replayed": self.replayed,
            "reservation": self.reservation.to_dict(),
            "receipt": self.receipt.to_dict(),
            "settlement": (
                None
                if self.settlement is None
                else getattr(self.settlement, "to_dict", lambda: self.settlement)()
            ),
            "output": self.output,
        }


# ---------------------------------------------------------------------------
# Credential / usage helpers
# ---------------------------------------------------------------------------


def resolve_model_credential(
    environ: Mapping[str, str] | None = None,
    *,
    credential_envs: Sequence[str] = DEFAULT_MODEL_CREDENTIAL_ENVS,
    model: str | None = None,
    base_url: str | None = None,
) -> ModelCredential:
    """Resolve the approved credential from the process environment.

    Only the gateway calls this.  The token is returned inside a value object
    with ``repr`` suppressed; it is never logged, persisted or put in a receipt.
    """
    env = os.environ if environ is None else environ
    names = [str(name) for name in credential_envs if str(name).strip()]
    if not names:
        raise ModelGatewayValidationError(
            "missing_credential_env", "no credential env is configured"
        )
    token = ""
    chosen = ""
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            token = value
            chosen = name
            break
    if not token:
        raise ModelCredentialUnavailableError(
            "no_model_credential",
            f"no provider credential is configured in {names}; the gateway refuses before any call",
            environments=names,
        )
    approved_model = str(model or env.get("ACP2_MODEL_ID", "") or "").strip()
    if not approved_model:
        raise ModelCredentialUnavailableError(
            "model_not_configured",
            "ACP2_MODEL_ID (the approved model) is not configured",
            environments=names,
        )
    return ModelCredential(
        token=token,
        model=approved_model,
        environment=chosen,
        base_url=str(base_url or env.get("ACP2_MODEL_BASE_URL", DEFAULT_MODEL_BASE_URL)),
    )


def estimate_input_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    """Conservative pre-request estimate (never reported as measured)."""
    payload = json.dumps([dict(message) for message in messages], ensure_ascii=False)
    return int((len(payload) + 2) // 3) + 32


def normalize_model_usage(
    usage: Mapping[str, Any] | None,
    *,
    messages: Sequence[Mapping[str, Any]],
    outcome: str = "completed",
) -> ModelTokenUsage:
    """Normalize one provider usage block; missing usage is *unknown*."""
    reported: dict[str, int] = {}
    if isinstance(usage, Mapping):
        for label, keys in (
            ("input_tokens", ("prompt_tokens", "input_tokens")),
            ("output_tokens", ("completion_tokens", "output_tokens")),
        ):
            for key in keys:
                value = usage.get(key)
                if isinstance(value, bool) or value is None:
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if number >= 0:
                    reported[label] = number
                    break
    if "input_tokens" in reported and "output_tokens" in reported:
        return ModelTokenUsage(
            input_tokens=reported["input_tokens"],
            output_tokens=reported["output_tokens"],
            source="provider-reported",
            complete=True,
        )
    if reported:
        return ModelTokenUsage(
            input_tokens=reported.get("input_tokens", estimate_input_tokens(messages)),
            output_tokens=reported.get("output_tokens"),
            source="partial-reported",
            complete=False,
            estimate="input_tokens" not in reported,
        )
    if outcome == "completed":
        # A completed call without a usage block: charge the conservative
        # estimate for the input and leave the output unknown.
        return ModelTokenUsage(
            input_tokens=estimate_input_tokens(messages),
            output_tokens=None,
            source="estimated",
            complete=False,
            estimate=True,
        )
    return ModelTokenUsage(
        input_tokens=estimate_input_tokens(messages),
        output_tokens=None,
        source="estimated",
        complete=False,
        estimate=True,
    )


# ---------------------------------------------------------------------------
# A real OpenAI-compatible transport (Controller composition root)
# ---------------------------------------------------------------------------


class ChatCompletionsTransport:
    """Minimal stdlib OpenAI-compatible Chat Completions transport.

    The credential is supplied per call by the gateway; this class never reads
    the environment, never logs headers and never stores the token.
    """

    def __init__(self, base_url: str = DEFAULT_MODEL_BASE_URL, *, timeout_s: float = 60.0) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._timeout_s = float(timeout_s)

    def call(self, request: ModelTransportRequest) -> ModelTransportResponse:
        payload = {
            "model": request.model,
            "messages": [dict(message) for message in request.messages],
            "max_tokens": int(request.max_output_tokens),
            "stream": False,
        }
        http_request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {request.auth_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise ModelTransportError(
                "upstream_status", f"provider answered HTTP {int(exc.code)}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelTransportError("upstream_error", f"provider request failed: {exc}") from exc
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ModelTransportError("upstream_error", "provider returned non-JSON") from exc
        choices = parsed.get("choices") if isinstance(parsed, Mapping) else None
        if not isinstance(choices, list) or not choices:
            raise ModelTransportError("upstream_error", "provider returned no choice")
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message") if isinstance(first, Mapping) else None
        text = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(text, str):
            raise ModelTransportError("upstream_error", "provider choice carries no text")
        usage = parsed.get("usage") if isinstance(parsed, Mapping) else None
        return ModelTransportResponse(
            text=text,
            usage=usage if isinstance(usage, Mapping) else None,
        )


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


class ModelGateway:
    """The one Controller-side model gateway (planning budget + receipts)."""

    def __init__(
        self,
        ledger: ReservationLedger,
        *,
        transport: ModelTransport,
        receipt_root: Path | str,
        model_request_limit: int = 1,
        credential_envs: Sequence[str] = DEFAULT_MODEL_CREDENTIAL_ENVS,
        environ: Mapping[str, str] | None = None,
        model: str | None = None,
        base_url: str | None = None,
        clock: Any | None = None,
    ) -> None:
        if not isinstance(ledger, ReservationLedger):
            raise ModelGatewayValidationError(
                "invalid_ledger", "the gateway requires the durable ReservationLedger"
            )
        if not hasattr(transport, "call"):
            raise ModelGatewayValidationError(
                "invalid_transport", "a transport with call() is required"
            )
        if isinstance(model_request_limit, bool) or int(model_request_limit) < 0:
            raise ModelGatewayValidationError(
                "invalid_limit", "model_request_limit must be a non-negative integer"
            )
        self._ledger = ledger
        self._transport = transport
        self._receipt_root = Path(receipt_root)
        self._receipt_root.mkdir(parents=True, exist_ok=True)
        self._limit = int(model_request_limit)
        self._credential_envs = tuple(str(name) for name in credential_envs)
        self._environ = environ
        self._model = model
        self._base_url = base_url
        self._clock = clock or (lambda: datetime.now(UTC).isoformat())
        self._lock = threading.Lock()

    # -- projection ------------------------------------------------------- #

    def credential_state(self) -> dict[str, Any]:
        """Secret-free credential status (for a capability/doctor read)."""
        try:
            credential = resolve_model_credential(
                self._environ,
                credential_envs=self._credential_envs,
                model=self._model,
                base_url=self._base_url,
            )
        except ModelGatewayError as exc:
            return {"configured": False, "rule": exc.rule, "message": exc.message}
        return credential.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MODEL_GATEWAY_SCHEMA,
            "model_request_limit": self._limit,
            "credential": self.credential_state(),
            "receipt_root": str(self._receipt_root),
            "phases": list(PLANNING_MODEL_PHASES),
            "job_or_attempt_fabricated": False,
        }

    def planning_usage(self, identity: ResearchIdentity, *, round: int = 0) -> dict[str, Any]:
        """Authoritative planning usage for the Supervisor sync point.

        The count is the number of non-released planning reservations — the
        platform record, not a process-local counter.  ``projection.consumed``
        is deliberately not used: a settled model receipt is counted once in the
        reservation book (M1-04c) and must not be re-added here.
        """
        if not isinstance(identity, ResearchIdentity):
            raise ModelGatewayValidationError("invalid_identity", "a ResearchIdentity is required")
        budget_id = planning_budget_id_for(
            identity.project_id, identity.research_id, identity.session_id
        )
        projection = self._ledger.research_budget_projection(budget_id)
        if projection is None:
            return {
                "budget_id": budget_id,
                "model_requests": 0,
                "receipts": 0,
                "reserved": 0,
                "settled": 0,
                "released": 0,
                "remaining": self._limit,
                "limit": self._limit,
                "round": int(round),
                "is_planned_identity": True,
            }
        reserved = settled = released = 0
        for row in projection.reservations:
            state = str(row.state)
            if state == "settled":
                settled += 1
            elif state == "released":
                released += 1
            else:
                reserved += 1
        calls = reserved + settled
        return {
            "budget_id": budget_id,
            "model_requests": calls,
            "receipts": int(projection.receipts),
            "reserved": reserved,
            "settled": settled,
            "released": released,
            "remaining": max(0, self._limit - calls),
            "limit": self._limit,
            "round": int(round),
            "is_planned_identity": True,
        }

    def receipt(self, receipt_id: str) -> dict[str, Any] | None:
        """Read one persisted trusted receipt (None when absent)."""
        path = self._receipt_path(receipt_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # -- the call --------------------------------------------------------- #

    def call(self, identity: ResearchIdentity, request: ModelPlanningRequest) -> ModelCallResult:
        """Reserve, call, settle and receipt one planning model request.

        Raises:
            ModelCredentialUnavailableError: no approved credential (zero calls).
            ModelBudgetDeniedError: the planning allowance is exhausted (zero calls).
            ModelCallUnknownError: the claim already has an unknown/in-flight call.
            ModelGatewayTransportFailureError: the provider call failed; the
                reservation stays charged and the receipt is retained.
        """
        if not isinstance(identity, ResearchIdentity):
            raise ModelGatewayValidationError("invalid_identity", "a ResearchIdentity is required")
        if not isinstance(request, ModelPlanningRequest):
            raise ModelGatewayValidationError(
                "invalid_request", "a ModelPlanningRequest is required"
            )
        credential = resolve_model_credential(
            self._environ,
            credential_envs=self._credential_envs,
            model=self._model,
            base_url=self._base_url,
        )
        approved_model = credential.model
        requested_model = str(request.model or "").strip()
        if requested_model and requested_model != approved_model:
            raise ModelGatewayValidationError(
                "model_not_approved",
                f"requested model {requested_model!r} is not the approved model {approved_model!r}",
            )
        claim = planning_claim(
            identity,
            round=request.round,
            call_seq=request.call_seq,
            request=request.digest_payload(approved_model),
        )
        with self._lock:
            existing = self._read_claim_record(claim.claim_id)
            if existing is not None:
                return self._replay(claim, existing)
            policy = planning_budget_policy(claim, model_requests=self._limit)
            self._ledger.define_research_budget(policy)
            usage = self.planning_usage(identity, round=request.round)
            if int(usage["model_requests"]) + 1 > self._limit:
                raise ModelBudgetDeniedError(
                    "planning_model_request_exhausted",
                    f"planning allowance {self._limit} - used {usage['model_requests']} < 1; "
                    "the provider is never called",
                    budget_id=claim.budget_id,
                    used=int(usage["model_requests"]),
                    limit=self._limit,
                )
            outcome = reserve_planning_call(self._ledger, claim, policy=policy)
            if not outcome.admitted:
                raise ModelBudgetDeniedError(
                    "planning_model_request_refused",
                    f"reservation refused: {outcome.decision.reason_code}",
                    budget_id=claim.budget_id,
                    reason=outcome.decision.reason_code,
                )
            reservation = outcome.reservation
            self._write_claim_record(
                {
                    "schema": MODEL_GATEWAY_SCHEMA,
                    "claim_id": claim.claim_id,
                    "request_digest": claim.request_digest,
                    "model_receipt_id": claim.model_receipt_id,
                    "reservation_id": reservation.reservation_id,
                    "budget_id": claim.budget_id,
                    "project_id": claim.project_id,
                    "research_id": claim.research_id,
                    "session_id": claim.session_id,
                    "round": claim.round,
                    "call_seq": claim.call_seq,
                    "phase": request.phase,
                    "model": approved_model,
                    "state": "inflight",
                }
            )
        transport_request = ModelTransportRequest(
            model=approved_model,
            messages=request.messages,
            max_output_tokens=int(request.max_output_tokens),
            auth_token=credential.token,
        )
        try:
            response = self._transport.call(transport_request)
        except ModelTransportError as exc:
            return self._finish_unknown(claim, reservation, request, approved_model, exc)
        return self._finish_completed(claim, reservation, request, approved_model, response)

    # -- completion paths ------------------------------------------------- #

    def _finish_completed(
        self,
        claim: Any,
        reservation: PlanningReservation,
        request: ModelPlanningRequest,
        model: str,
        response: ModelTransportResponse,
    ) -> ModelCallResult:
        usage = normalize_model_usage(
            response.usage, messages=request.messages, outcome=str(response.outcome)
        )
        settlement = self._settle(claim, reservation)
        receipt = self._build_receipt(
            claim,
            reservation,
            request,
            model,
            outcome=str(response.outcome),
            usage=usage,
        )
        self._write_receipt(receipt)
        self._mark_claim(claim.claim_id, state="settled", receipt_id=receipt.receipt_id)
        return ModelCallResult(
            reservation=reservation,
            receipt=receipt,
            settlement=settlement,
            output=response.text,
        )

    def _finish_unknown(
        self,
        claim: Any,
        reservation: PlanningReservation,
        request: ModelPlanningRequest,
        model: str,
        error: ModelTransportError,
    ) -> ModelCallResult:
        usage = normalize_model_usage(error.usage, messages=request.messages, outcome=error.outcome)
        settlement = self._settle(claim, reservation)
        receipt = self._build_receipt(
            claim,
            reservation,
            request,
            model,
            outcome=error.outcome,
            usage=usage,
        )
        self._write_receipt(receipt)
        self._mark_claim(claim.claim_id, state="settled", receipt_id=receipt.receipt_id)
        result = ModelCallResult(
            reservation=reservation,
            receipt=receipt,
            settlement=settlement,
            output="",
        )
        raise ModelGatewayTransportFailureError(error.outcome, error.message, result=result)

    def _settle(self, claim: Any, reservation: PlanningReservation) -> Any:
        settlement = settle_planning_call(
            self._ledger,
            reservation,
            receipt_id=claim.model_receipt_id,
            counters=UsageUnitCounts(model_request=1),
        )
        if not (
            bool(getattr(settlement, "applied", False))
            or bool(getattr(settlement, "duplicate", False))
        ):
            # The reservation stays (unknown) and the allowance is never reset.
            raise ModelGatewayError(
                "settlement_refused",
                "the planning reservation could not be settled; it stays charged",
                budget_id=claim.budget_id,
                reservation_id=reservation.reservation_id,
            )
        return settlement

    def _build_receipt(
        self,
        claim: Any,
        reservation: PlanningReservation,
        request: ModelPlanningRequest,
        model: str,
        *,
        outcome: str,
        usage: ModelTokenUsage,
    ) -> ModelCallReceipt:
        return ModelCallReceipt(
            receipt_id=claim.model_receipt_id,
            claim_id=claim.claim_id,
            budget_id=claim.budget_id,
            reservation_id=reservation.reservation_id,
            project_id=claim.project_id,
            research_id=claim.research_id,
            session_id=claim.session_id,
            round=claim.round,
            call_seq=claim.call_seq,
            phase=str(request.phase),
            model=model,
            request_digest=claim.request_digest,
            outcome=outcome,
            usage=usage,
            recorded_at=str(self._clock()),
        )

    # -- replay ----------------------------------------------------------- #

    def _replay(self, claim: Any, record: Mapping[str, Any]) -> ModelCallResult:
        if str(record.get("request_digest")) != claim.request_digest:
            raise ModelGatewayConflictError(
                "claim_request_changed",
                "the claim was already used for a different request digest; "
                "advance call_seq instead of rewriting a planning call",
                claim_id=claim.claim_id,
            )
        reservation = self._find_reservation(claim)
        if reservation is None:
            raise ModelGatewayConflictError(
                "reservation_missing",
                "the claim index names a reservation that does not exist",
                claim_id=claim.claim_id,
            )
        state = str(record.get("state") or "")
        if state == "settled" or str(reservation.state) == "settled":
            receipt_payload = self.receipt(claim.model_receipt_id)
            if receipt_payload is None:
                raise ModelCallUnknownError(
                    "receipt_missing",
                    "the claim settled but its trusted receipt is not readable; "
                    "the allowance stays charged",
                    claim_id=claim.claim_id,
                )
            receipt = _receipt_from_payload(receipt_payload)
            return ModelCallResult(
                reservation=reservation,
                receipt=receipt,
                settlement=None,
                output="",
                replayed=True,
            )
        raise ModelCallUnknownError(
            "call_in_flight",
            "a prior call with this claim has no receipt; the reservation stays "
            "charged and the provider is not called again",
            claim_id=claim.claim_id,
            reservation_id=reservation.reservation_id,
        )

    def _find_reservation(self, claim: Any) -> PlanningReservation | None:
        projection = self._ledger.research_budget_projection(claim.budget_id)
        if projection is None:
            return None
        for row in projection.reservations:
            if str(row.claim_id) == claim.claim_id:
                return PlanningReservation(
                    claim_id=str(row.claim_id),
                    budget_id=str(row.budget_id),
                    reservation_id=str(row.reservation_id),
                    request_digest=claim.request_digest,
                    model_receipt_id=claim.model_receipt_id,
                    project_id=claim.project_id,
                    research_id=claim.research_id,
                    session_id=claim.session_id,
                    round=claim.round,
                    call_seq=claim.call_seq,
                    units=int(sum(row.units.values()) or 1),
                    state=str(row.state),
                )
        return None

    # -- durable gateway artifacts --------------------------------------- #

    def _receipt_path(self, receipt_id: str) -> Path:
        name = hashlib.sha256(str(receipt_id).encode("utf-8")).hexdigest()[:32]
        return self._receipt_root / f"receipt-{name}.json"

    def _claim_path(self, claim_id: str) -> Path:
        name = hashlib.sha256(str(claim_id).encode("utf-8")).hexdigest()[:32]
        return self._receipt_root / f"claim-{name}.json"

    def _read_claim_record(self, claim_id: str) -> dict[str, Any] | None:
        path = self._claim_path(claim_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelGatewayConflictError(
                "claim_index_corrupt", "the claim index is not valid JSON", claim_id=claim_id
            ) from exc
        if not isinstance(payload, dict):
            raise ModelGatewayConflictError(
                "claim_index_corrupt", "the claim index is not an object", claim_id=claim_id
            )
        return payload

    def _write_claim_record(self, payload: Mapping[str, Any]) -> None:
        self._write_json(self._claim_path(str(payload["claim_id"])), payload, immutable=False)

    def _mark_claim(self, claim_id: str, *, state: str, receipt_id: str) -> None:
        record = self._read_claim_record(claim_id) or {"claim_id": claim_id}
        updated = {**record, "state": str(state), "receipt_id": str(receipt_id)}
        self._write_claim_record(updated)

    def _write_receipt(self, receipt: ModelCallReceipt) -> None:
        self._write_json(self._receipt_path(receipt.receipt_id), receipt.to_dict())

    def _write_json(
        self, path: Path, payload: Mapping[str, Any], *, immutable: bool = True
    ) -> None:
        """Atomically write one artifact.

        Receipts are immutable: a same-id/different-bytes row is a typed
        conflict.  The claim index is a small mutable state row (inflight ->
        settled) and rewrites itself atomically.
        """
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        encoded = data.encode("utf-8")
        if path.is_file():
            if path.read_bytes() == encoded:
                return
            if immutable:
                raise ModelGatewayConflictError(
                    "artifact_conflict",
                    f"{path.name} already exists with different content",
                )
        temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{threading.get_ident()}")
        temporary.write_bytes(encoded)
        os.replace(temporary, path)


def _receipt_from_payload(payload: Mapping[str, Any]) -> ModelCallReceipt:
    usage_payload = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    usage = ModelTokenUsage(
        input_tokens=usage_payload.get("input_tokens"),
        output_tokens=usage_payload.get("output_tokens"),
        source=str(usage_payload.get("source") or "unknown"),
        complete=bool(usage_payload.get("complete")),
        estimate=bool(usage_payload.get("estimate")),
    )
    return ModelCallReceipt(
        receipt_id=str(payload.get("receipt_id") or ""),
        claim_id=str(payload.get("claim_id") or ""),
        budget_id=str(payload.get("budget_id") or ""),
        reservation_id=str(payload.get("reservation_id") or ""),
        project_id=str(payload.get("project_id") or ""),
        research_id=str(payload.get("research_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        round=int(payload.get("round") or 0),
        call_seq=int(payload.get("call_seq") or 1),
        phase=str(payload.get("phase") or ""),
        model=str(payload.get("model") or ""),
        request_digest=str(payload.get("request_digest") or ""),
        outcome=str(payload.get("outcome") or "unknown"),
        usage=usage,
        recorded_at=str(payload.get("recorded_at") or ""),
        trusted_source=str(payload.get("trusted_source") or "controller-terminal-receipt"),
    )


def model_gateway_statement() -> dict[str, Any]:
    """Capability statement: reservation + accounting, not an enforced row."""
    return {
        "schema": MODEL_GATEWAY_SCHEMA,
        "phases": list(PLANNING_MODEL_PHASES),
        "reserves_before_call": True,
        "records_trusted_usage": True,
        "credential_holder": "controller model gateway (environment)",
        "direct_client_credentials_allowed": False,
        "capability": "recorded-only",
        "upgrade_gate": "M3-03 gateway evidence; the matrix row is not upgraded here",
        "job_or_attempt_fabricated": False,
        "receipt_identity": (
            "M1-02 modelrcpt|<project>|<research>|<session>|<round>|<call_seq>|<digest>"
        ),
        "budget_identity": "project + planning:<research>:<session>",
    }
