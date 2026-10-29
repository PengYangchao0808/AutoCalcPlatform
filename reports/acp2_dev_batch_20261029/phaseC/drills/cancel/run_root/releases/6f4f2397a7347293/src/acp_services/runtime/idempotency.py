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
  Controller (never a client-supplied field).  M0-04 builds it from
  :class:`acp_services.workspace.command_identity.CommandIdentity` — the
  authenticated HTTP/MCP principal, or the explicit ``local-single`` operator
  for the Controller itself and the local CLI.  Two authenticated users in one
  project therefore hold two *different* scopes: the second caller's reuse of
  the same key is an explicit ``conflict``, never a replay of the first user's
  task.  A row written before M0-04 keeps ``identity_version = "1"`` and is
  reconciled rather than silently adopted.
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
    "DIGEST_FIELD_TABLES",
    "IDENTITY_VERSION",
    "LEGACY_IDENTITY_VERSION",
    "LEGACY_REQUEST_DIGEST_FIELDS",
    "LEGACY_REQUEST_DIGEST_VERSION",
    "MIGRATION_STATE_NATIVE",
    "MIGRATION_STATE_PENDING",
    "MIGRATION_STATE_UNVERIFIED",
    "MIGRATION_STATE_VERIFIED",
    "REQUEST_DIGEST_FIELDS",
    "REQUEST_DIGEST_VERSION",
    "SCOPE_ORIGIN_LEGACY_LOCAL",
    "SCOPE_ORIGIN_LEGACY_SCOPED",
    "SCOPE_ORIGIN_LEGACY_UNKNOWN",
    "SCOPE_ORIGIN_UNSPECIFIED",
    "SCOPE_ORIGIN_VERIFIED",
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
    "canonical_request_digest_for_version",
    "resolve_scope_id",
    "split_scope_id",
    "task_ref_for",
    "verify_request_digest",
]

#: The only Runtime action that currently uses the persistent ledger.
ACTION_SUBMIT = "submit"

#: The verified scope separator.  ``principal`` and ``project`` ids never
#: contain it, so the folded id is unambiguous and stable in a DB dump.
_SCOPE_SEPARATOR = "\x1f"

#: Identity version written for a claim whose scope was built from a
#: server-verified :class:`~acp_services.workspace.command_identity.CommandIdentity`
#: (M0-04).  Version ``1`` is the legacy scope that folded every HTTP caller
#: into ``LOCAL_PRINCIPAL_ID``; such rows are reconciled by migration ``024``.
IDENTITY_VERSION = "2"
LEGACY_IDENTITY_VERSION = "1"

#: Version of the canonical request-digest rules (:func:`canonical_request_digest`
#: over the full effective submit request, ``idempotency_key`` excluded).
#:
#: * ``"2"`` (M0-06) — the material is the **field-policy canonical request**
#:   (:func:`acp_services.entrypoints.application_params.
#:   canonical_submit_request`): the exhaustive policy field set with defaults
#:   materialised, ``node_tags`` normalised, server-path fields rejected before
#:   identity and the DTO ``schema_version`` always included.  The field table is
#:   :data:`DIGEST_FIELD_TABLES`\\ ``["2"]``.
#: * ``"1"`` — the pre-M0-06 rule: the raw ``V1JobCreateRequest.model_dump(
#:   exclude_none=True)`` declaration, unknown extras dropped by the DTO.  A row
#:   of this version is verified against its own frozen field table
#:   (:func:`canonical_request_digest_for_version`) and never declared tampered
#:   merely because the rule version changed.
#:
#: A row of another version is reconciled, never silently replayed: equal digest
#: bytes prove the same request (the version only names the materialisation), and
#: a differing digest is verified under the row's frozen table; when that is not
#: possible the outcome is a typed conflict that says "reconcile", not "tampered"
#: and not "resubmit with a new key".
REQUEST_DIGEST_VERSION = "2"
LEGACY_REQUEST_DIGEST_VERSION = "1"

#: Frozen v1 field table (the ``V1JobCreateRequest`` declaration at the time the
#: rule was frozen, including the two server-path fields that M0-06 now rejects
#: at the entry).  A legacy row is only verifiable when the current request can
#: be projected onto exactly this table.
LEGACY_REQUEST_DIGEST_FIELDS: tuple[str, ...] = (
    "schema_version",
    "workflow",
    "name",
    "input",
    "method",
    "resources",
    "output_dir",
    "config_path",
    "tags",
    "node_tags",
    "project_id",
    "execution_mode",
    "target_node",
    "molecule_name",
    "task_name",
    "remark",
    "plan_digest",
    "proposal_ref",
    "proposal_digest",
    "budget",
    "idempotency_key",
    "preflight_fingerprint",
)

#: Frozen v2 field table: the policy-accepted submit fields
#: (``application_params.SUBMIT_FIELD_POLICY`` with ``job_spec``,
#: ``request_control`` and ``research`` dispositions; the two server-path fields
#: are absent because they can never reach identity).
REQUEST_DIGEST_FIELDS: tuple[str, ...] = tuple(
    name for name in LEGACY_REQUEST_DIGEST_FIELDS if name not in {"output_dir", "config_path"}
)

#: Digest version → frozen field table.  The only authority for "which fields did
#: this rule hash"; add a row before bumping :data:`REQUEST_DIGEST_VERSION`.
DIGEST_FIELD_TABLES: dict[str, tuple[str, ...]] = {
    LEGACY_REQUEST_DIGEST_VERSION: LEGACY_REQUEST_DIGEST_FIELDS,
    REQUEST_DIGEST_VERSION: REQUEST_DIGEST_FIELDS,
}


#: ``scope_origin`` vocabulary of the idempotency ledger.
SCOPE_ORIGIN_VERIFIED = "verified"
SCOPE_ORIGIN_UNSPECIFIED = "unspecified"
SCOPE_ORIGIN_LEGACY_LOCAL = "legacy-local"
SCOPE_ORIGIN_LEGACY_SCOPED = "legacy-scoped"
SCOPE_ORIGIN_LEGACY_UNKNOWN = "legacy-unknown"

#: ``migration_state`` vocabulary of the idempotency ledger (R09 reconciliation).
MIGRATION_STATE_NATIVE = "native"
MIGRATION_STATE_VERIFIED = "legacy-verified"
MIGRATION_STATE_PENDING = "legacy-pending"
MIGRATION_STATE_UNVERIFIED = "legacy-unverified"


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
    """One durable row of the idempotency ledger.

    ``identity_version``/``request_digest_version``/``scope_origin``/
    ``migration_state`` are the M0-04 (R09) reconciliation columns added by
    migration ``024``.  A row written by the current code carries
    :data:`IDENTITY_VERSION` and :data:`MIGRATION_STATE_NATIVE`; a pre-M0-04 row
    keeps version ``1`` and one of the ``legacy-*`` migration states, and is
    only replayable when its principal/project attribution is verifiable.
    """

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
    principal_id: str | None = None
    project_id: str | None = None
    identity_version: str = IDENTITY_VERSION
    request_digest_version: str = REQUEST_DIGEST_VERSION
    scope_origin: str = SCOPE_ORIGIN_UNSPECIFIED
    migration_state: str = MIGRATION_STATE_NATIVE

    @property
    def is_committed(self) -> bool:
        return self.state == ClaimState.COMMITTED.value

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            ClaimState.COMMITTED.value,
            ClaimState.REJECTED.value,
        }

    @property
    def is_legacy(self) -> bool:
        """Whether this row predates the verified ``CommandIdentity`` scope."""
        return str(self.identity_version) == LEGACY_IDENTITY_VERSION

    @property
    def is_replayable(self) -> bool:
        """Whether the row's attribution allows a replay/mapping at all.

        An unattributable legacy row is *reconciled*, never replayed: it must
        not be handed to whichever caller happens to present the same key.
        """
        if not self.is_legacy:
            return True
        return self.migration_state == MIGRATION_STATE_VERIFIED

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
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "identity_version": self.identity_version,
            "request_digest_version": self.request_digest_version,
            "scope_origin": self.scope_origin,
            "migration_state": self.migration_state,
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


def split_scope_id(scope_id: str) -> tuple[str | None, str | None]:
    """Split a scope id back into ``(principal, project)``.

    Returns ``(None, None)`` for a value that does not carry exactly one
    separator: such a row cannot be attributed to a verified principal and is
    therefore *unverified* for replay purposes (R09), never guessed.
    """
    text = str(scope_id or "")
    principal, separator, project = text.partition(_SCOPE_SEPARATOR)
    if not separator or not principal or not project or _SCOPE_SEPARATOR in project:
        return None, None
    return principal, project


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


def canonical_request_digest_for_version(version: str, payload: Mapping[str, Any]) -> str | None:
    """Return the digest of *payload* under the frozen rule *version*.

    ``None`` when the version is not registered: an unknown rule can never
    verify a stored row, so the caller must reconcile instead of guessing.
    """
    fields = DIGEST_FIELD_TABLES.get(str(version))
    if fields is None:
        return None
    material = {str(key): value for key, value in payload.items() if str(key) in set(fields)}
    return canonical_request_digest(material)


def verify_request_digest(
    request_digest: str, request_digest_version: str, payload: Mapping[str, Any] | None
) -> bool:
    """Whether *payload* is the same request a stored row recorded.

    Equal digest bytes prove identity regardless of the declared rule version
    (the version only names the materialisation).  A different digest is
    re-derived from the row's **frozen field table** when the payload is
    available; an unregistered version or a missing payload cannot verify and
    therefore returns ``False`` (the caller reconciles).
    """
    stored = str(request_digest or "")
    if not stored:
        return False
    if payload is None:
        return False
    computed = canonical_request_digest_for_version(request_digest_version, payload)
    return computed is not None and computed == stored


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
        budget: Any | None = None,
        principal_id: str | None = None,
        key_id: str | None = None,
    ) -> str:
        """Reserve resources (and optionally the research budget) for *claim_id*.

        ``principal_id``/``key_id`` are the verified caller identity of the
        claim (M0-04).  A reservation adapter that records admission must
        attribute the reservation to that principal; an adapter without an
        admission ledger ignores them.
        """
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
        budget: Any | None = None,
        principal_id: str | None = None,
        key_id: str | None = None,
    ) -> str:
        del budget, principal_id, key_id
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

    ``principal_id``/``key_id`` are additive (M0-04): they are the verified
    caller identity that the admission reservation and the ledger row are
    attributed to.  They never widen ``scope_id`` — the scope id is built from
    the same principal by :meth:`acp_services.workspace.command_identity.
    CommandIdentity.idempotency_scope`.

    ``request_digest_version`` is additive (M0-06): the frozen digest rule the
    row is written with.  It defaults to :data:`REQUEST_DIGEST_VERSION`; a caller
    replaying a pre-M0-06 row presents the same payload and the store verifies the
    stored digest against that row's frozen field table instead of declaring a
    different rule a conflict.
    """

    key: str
    scope_id: str
    action: str = ACTION_SUBMIT
    request_payload: Mapping[str, Any] | None = None
    request_digest: str = ""
    request_digest_version: str = REQUEST_DIGEST_VERSION
    lease_seconds: float = 60.0
    wait_seconds: float = 10.0
    reservations: ReservationPort | None = None
    principal_id: str | None = None
    key_id: str | None = None

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

    @property
    def principal(self) -> str | None:
        """The verified principal of the claim (``None`` = legacy caller)."""
        text = str(self.principal_id or "").strip()
        return text or None

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
