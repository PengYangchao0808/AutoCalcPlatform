# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""Persistent idempotency contracts for Runtime submissions (B08).

This module owns the *types* of the persistent idempotency ledger and the
canonical request identity.  It does **not** open a database and it is not a
second ledger: the single ledger is
:meth:`acp_services.store.store.JobStore.claim_idempotency` /
:meth:`~acp_services.store.store.JobStore.finalize_idempotent_job` over the
``idempotency_records`` table created by migration ``022``.

Key rules
---------
* ``scope_id`` is the **verified** principal/project scope resolved by the
  Controller (never a client-supplied field).  In the local-single tier the
  principal is :data:`acp_services.contracts.access.LOCAL_PRINCIPAL_ID`; the
  project is the server-resolved project of the submission.
* The request digest is canonical: mappings are key-sorted, sequences keep
  their order, and floating point values are rendered as exact decimal
  strings (no JSON float), so two semantically identical payloads hash the
  same across processes and Python versions.
* A claim is the short-lived reservation of one ``(scope, action, key)``.  It
  is inserted in its own ``BEGIN IMMEDIATE`` transaction; the job row and the
  ``committed`` transition are written in a second short transaction by
  :meth:`JobStore.finalize_idempotent_job`.  No transaction is ever held
  across the job database, the reservation ledger or the filesystem.
* The committed ``QUEUED`` job row is the durable dispatch intent.  A replay
  returns that row's TaskRef; it never allocates a second job, directory or
  reservation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ACTION_SUBMIT",
    "CLAIM_STATES",
    "ClaimState",
    "IdempotencyClaim",
    "IdempotencyConflictError",
    "IdempotencyContext",
    "IdempotencyError",
    "IdempotencyInProgressError",
    "IdempotencyLeaseError",
    "IdempotencyOutcome",
    "IdempotencyReservationDeniedError",
    "LedgerReservationPort",
    "OutcomeKind",
    "ReservationIdempotency",
    "ReservationPort",
    "canonical_payload",
    "canonical_request_digest",
    "resolve_scope_id",
    "task_ref_for",
]

#: The only Runtime action that currently uses the persistent ledger.
ACTION_SUBMIT = "submit"

#: The verified scope separator.  ``principal`` and ``project`` ids never
#: contain it, so the folded id is unambiguous and stable in a DB dump.
_SCOPE_SEPARATOR = "\x1f"


class ClaimState(str, Enum):
    """Lifecycle of one idempotency claim.

    ``claimed`` is the only non-terminal state.  ``committed`` is the durable
    success state and the only state that yields a TaskRef.  ``rejected`` is
    the terminal admission-denial state.  ``released`` / ``expired`` are
    recoverable: a later claimant may take them over with an epoch CAS.
    """

    CLAIMED = "claimed"
    COMMITTED = "committed"
    RELEASED = "released"
    REJECTED = "rejected"
    EXPIRED = "expired"


CLAIM_STATES: frozenset[str] = frozenset(state.value for state in ClaimState)


class OutcomeKind(str, Enum):
    """The four observable outcomes of one claim attempt."""

    CREATED = "created"
    REPLAY = "replay"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"


class IdempotencyError(RuntimeError):
    """Base class for a failed idempotent submission."""


class IdempotencyConflictError(IdempotencyError):
    """The same key was used with a different payload or a different scope."""


class IdempotencyLeaseError(IdempotencyError):
    """The caller is no longer the live owner of the claim (stale token/epoch)."""


class IdempotencyInProgressError(IdempotencyError):
    """Another owner holds a live lease; retry with the same key."""

    retryable = True


class IdempotencyReservationDeniedError(IdempotencyError):
    """Admission refused the resource reservation for this claim.

    ``decision`` carries the structured :class:`AdmissionDecision` when the
    denial came from the Runtime admission controller, so the HTTP/MCP entry can
    return the same machine-readable reason/exceeded matrix it would return for
    a pre-claim denial.
    """

    def __init__(
        self,
        message: str,
        *,
        decision: Any | None = None,
        reason_code: str = "",
        exceeded: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.reason_code = str(reason_code)
        self.exceeded = tuple(dict(item) for item in exceeded)


@dataclass(frozen=True)
class IdempotencyClaim:
    """One durable row of the idempotency ledger."""

    claim_id: str
    scope_id: str
    action: str
    idempotency_key: str
    request_digest: str
    state: str
    owner_token: str | None = None
    lease_epoch: int = 0
    lease_expires_at: str | None = None
    task_id: str | None = None
    operation_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_committed(self) -> bool:
        return self.state == ClaimState.COMMITTED.value

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            ClaimState.COMMITTED.value,
            ClaimState.REJECTED.value,
        }

    def task_ref(self) -> dict[str, Any] | None:
        """Return the stored TaskRef for a committed claim (else ``None``)."""
        if not self.is_committed or not self.task_id:
            return None
        return {"task_id": self.task_id, "job_id": self.task_id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "scope_id": self.scope_id,
            "action": self.action,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
            "state": self.state,
            "owner_token": self.owner_token,
            "lease_epoch": self.lease_epoch,
            "lease_expires_at": self.lease_expires_at,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class IdempotencyOutcome:
    """Frozen result of :meth:`JobStore.claim_idempotency`.

    ``created`` means the caller now owns a fresh (or taken-over) claim and
    must reserve/finalize it.  ``replay`` carries the committed TaskRef.
    ``conflict`` is terminal for this request.  ``in_progress`` is retryable
    and never grants ownership.
    """

    kind: str
    claim: IdempotencyClaim | None = None
    task_ref: Mapping[str, Any] | None = None
    retryable: bool = False
    detail: str = ""

    @property
    def created(self) -> bool:
        return self.kind == OutcomeKind.CREATED.value

    @property
    def replay(self) -> bool:
        return self.kind == OutcomeKind.REPLAY.value

    @property
    def conflict(self) -> bool:
        return self.kind == OutcomeKind.CONFLICT.value

    @property
    def in_progress(self) -> bool:
        return self.kind == OutcomeKind.IN_PROGRESS.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "claim": None if self.claim is None else self.claim.to_dict(),
            "task_ref": None if self.task_ref is None else dict(self.task_ref),
            "retryable": self.retryable,
            "detail": self.detail,
        }


def resolve_scope_id(principal_id: str, project_id: str) -> str:
    """Return the verified scope id ``principal <US> project``.

    Both components come from the Controller's authenticated/resolved context.
    A client can neither supply nor widen this value; a changed project is a
    changed scope and is rejected rather than silently creating a second job.
    """
    principal = str(principal_id or "").strip()
    project = str(project_id or "").strip()
    if not principal:
        raise ValueError("principal_id must be non-empty")
    if not project:
        raise ValueError("project_id must be non-empty")
    return f"{principal}{_SCOPE_SEPARATOR}{project}"


def _canonical_value(value: Any) -> Any:
    """Recursively canonicalise *value* (JSON-compatible, no float literals)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request payload contains a non-finite float")
        # ``repr`` is the shortest round-trip representation in CPython; using
        # it as a *string* keeps the canonical document free of JSON floats.
        return {"$float": repr(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    return str(value)


def canonical_payload(
    payload: Mapping[str, Any],
    *,
    exclude: Sequence[str] = ("idempotency_key",),
) -> dict[str, Any]:
    """Return the canonical mapping used for request identity.

    Top-level *exclude* keys are dropped (the idempotency key names the
    request, it is not part of its content).  The result contains no float
    literals and is safe to compare/serialize deterministically.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("request payload must be a mapping")
    excluded = {str(name) for name in exclude}
    material = {key: value for key, value in payload.items() if str(key) not in excluded}
    canonical = _canonical_value(material)
    if not isinstance(canonical, dict):  # pragma: no cover - defensive
        raise TypeError("canonical payload must be a mapping")
    return canonical


def canonical_request_digest(
    payload: Mapping[str, Any],
    *,
    exclude: Sequence[str] = ("idempotency_key",),
) -> str:
    """Return ``sha256:<hex>`` over the canonical request payload."""
    canonical = canonical_payload(payload, exclude=exclude)
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReservationIdempotency:
    """The claim identity a reservation ledger records alongside its row."""

    claim_id: str
    scope_id: str
    action: str
    idempotency_key: str


@runtime_checkable
class ReservationPort(Protocol):
    """Claim-keyed reservation port (adapter over the existing ledger).

    Implementations must use the existing :class:`ReservationLedger` short
    transaction — this is *not* a second reservation path.  ``reserve`` is
    called once per created claim; ``release`` is the compensation used by the
    claim-keyed sweep and must be idempotent.
    """

    def reserve_for_claim(
        self,
        claim_id: str,
        *,
        scope_id: str,
        action: str,
        idempotency_key: str,
        spec: Any,
    ) -> str:
        """Reserve resources for *claim_id* and return the reservation id."""
        ...

    def release_for_claim(self, claim_id: str) -> bool:
        """Release the reservation recorded for *claim_id* (idempotent)."""
        ...


class LedgerReservationPort:
    """Adapter from :class:`ReservationPort` onto the existing ledger.

    The ledger is injected (never imported here, so this module stays a pure
    contract module and ``admission`` may import it).  The adapter only calls
    ``ledger.reserve(..., idempotency=...)`` and
    ``ledger.release_for_claim``; it adds no reservation state of its own.
    """

    def __init__(
        self,
        ledger: Any,
        *,
        request_factory: Callable[[str, Any], Any],
        limits_factory: Callable[[Any], Any],
    ) -> None:
        self._ledger = ledger
        self._request_factory = request_factory
        self._limits_factory = limits_factory

    def reserve_for_claim(
        self,
        claim_id: str,
        *,
        scope_id: str,
        action: str,
        idempotency_key: str,
        spec: Any,
    ) -> str:
        request = self._request_factory(claim_id, spec)
        outcome = self._ledger.reserve(
            request,
            self._limits_factory(request),
            reservation_id="res_" + str(claim_id),
            idempotency=ReservationIdempotency(
                claim_id=str(claim_id),
                scope_id=str(scope_id),
                action=str(action),
                idempotency_key=str(idempotency_key),
            ),
        )
        if not outcome.allowed:
            raise IdempotencyReservationDeniedError(
                f"admission refused reservation: {outcome.reason_code}"
            )
        reservation = outcome.reservation
        if reservation is None:
            raise IdempotencyReservationDeniedError("admission returned no reservation")
        return str(reservation.reservation_id)

    def release_for_claim(self, claim_id: str) -> bool:
        return bool(self._ledger.release_for_claim(str(claim_id)))


@dataclass(frozen=True)
class IdempotencyContext:
    """Optional idempotency context for one submit call.

    Legacy callers pass nothing and keep the original submit path.  The HTTP
    and MCP entrypoints build this from the verified scope plus the request
    payload; ``request_payload`` is hashed with
    :func:`canonical_request_digest`.
    """

    key: str
    scope_id: str
    action: str = ACTION_SUBMIT
    request_payload: Mapping[str, Any] | None = None
    request_digest: str = ""
    lease_seconds: float = 60.0
    wait_seconds: float = 10.0
    reservations: ReservationPort | None = None

    def __post_init__(self) -> None:
        if not str(self.key or "").strip():
            raise ValueError("idempotency key must be non-empty")
        if not str(self.scope_id or "").strip():
            raise ValueError("scope_id must be non-empty")
        if not str(self.action or "").strip():
            raise ValueError("action must be non-empty")
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.wait_seconds < 0:
            raise ValueError("wait_seconds must be non-negative")

    def digest(self) -> str:
        """Return the canonical digest (explicit digest wins when supplied)."""
        if self.request_digest:
            return self.request_digest
        if self.request_payload is None:
            raise ValueError("idempotency context needs request_payload or request_digest")
        return canonical_request_digest(self.request_payload)


def task_ref_for(claim: IdempotencyClaim, *, attempt: int = 1) -> dict[str, Any]:
    """Return a TaskRef for a committed claim (``attempt`` from the job row)."""
    ref = claim.task_ref()
    if ref is None:
        raise IdempotencyConflictError(
            f"claim {claim.claim_id} is not committed (state={claim.state})"
        )
    ref["attempt"] = int(attempt)
    return ref
