"""Persistent prepare operations — the durable state machine for data preparation.

B02-3 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 13).  The legacy
scheduler kept the terminal remote-catalog prefetch in an **in-process queue**
(``acp/scheduler/manager.py`` ``_queue_catalog_prefetch`` / ``_catalog_prefetch_loop``):
a restart dropped every undecided prefetch, a duplicate enqueue ran the work
twice, and no consumer could distinguish "still running" from "the worker
vanished".  This module replaces that queue with a persistent operation record.

**The DB is the authority.**  Every fact a consumer needs (identity, state,
lease, retry budget, failure reason, availability outcome) lives in the
``data_operations`` SQLite table; logs and the in-memory wake signal are *not*
authority and may be lost at any time without changing what happened.

Frozen properties (all enforced by :class:`PrepareOperationService`):

* **Stable idempotency key.**  ``op_`` + sha256(permission_scope + resource_key).
  The permission scope is part of the key, so two observers holding different
  scopes never share an operation — cancelling one observer's operation can
  never kill another legitimate consumer's preparation of the same resource.
* **State machine.**  ``queued`` → ``running`` → ``succeeded`` | ``failed`` |
  ``canceled`` | ``expired``.  A retryable failure returns to ``queued`` while
  its budget remains.
* **Lease + ownership generation + lease token.**  A claim bumps the generation
  and issues a fresh random token.  A later claim (after expiry or a restart
  takeover) bumps the generation again, so a stale holder's completion CAS can
  never publish.
* **CAS at completion.**  ``complete`` matches the stored operation, job
  attempt, resource digest, resource generation, authorization version and
  storage binding id in one conditional UPDATE.  A revoked authorization or a
  changed storage binding therefore rejects the publish, and a stale lease
  holder loses publish eligibility.
* **Lease expiry alone neither releases nor completes.**  Expiry only makes an
  operation *retryable* (claimable by another worker); it never marks success.
* **Recovery replays, never guesses.**  :meth:`PrepareOperationService.recover`
  returns undecided ``queued``/``running`` operations to ``queued`` (bumping the
  generation so any prior lease token is dead) and lets the worker re-execute
  them.  An exhausted retry budget ends in ``failed`` with a visible reason.

**Isolation (hard rule).**  ``src/acp_services/**`` must never import the legacy
application package — this module is therefore stdlib-only plus the frozen Data
contracts.  The *executor* that performs the actual remote prefetch is injected
from the legacy side (``acp.services_data_bootstrap.build_prepare_executor``);
the worker calls it through :class:`PrepareExecutorPort`.

Author: ACP Services (B lane, todo 13).
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.data.contracts import (
    Availability,
    DataAvailability,
    Freshness,
    PrepareOperation,
    ResourceKind,
    ResourceState,
)

logger = logging.getLogger(__name__)

# Programming errors must surface instead of being logged and retried forever.
# Expected failures (I/O, transport, store rejections) still take the
# best-effort/retry path below.
_PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (TypeError, AttributeError, NameError)

__all__ = [
    "PrepareState",
    "PrepareRequest",
    "PrepareOperationRecord",
    "PrepareLease",
    "PrepareCompletion",
    "PrepareExecutionResult",
    "RecoveryReport",
    "PrepareOperationError",
    "PrepareOperationNotFoundError",
    "PrepareOperationTerminalError",
    "PreparePublishRejectedError",
    "StaleLeaseError",
    "PrepareExecutorPort",
    "PrepareOperationService",
    "PrepareOperationWorker",
    "idempotency_key_for",
    "default_resource_key",
    "resolve_operation_state",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class PrepareState(str, Enum):
    """Lifecycle of one persistent prepare operation (todo 13)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"


_TERMINAL_STATES = frozenset(
    {
        PrepareState.SUCCEEDED.value,
        PrepareState.FAILED.value,
        PrepareState.CANCELED.value,
        PrepareState.EXPIRED.value,
    }
)

#: Mapping from the operation state machine onto the frozen wire vocabulary
#: (``acp_services.data.contracts.ResourceState``).  ``expired`` has no wire
#: equivalent (``ResourceState`` predates it) and reads as a failure there.
_STATE_TO_RESOURCE: dict[PrepareState, ResourceState] = {
    PrepareState.QUEUED: ResourceState.PENDING,
    PrepareState.RUNNING: ResourceState.RUNNING,
    PrepareState.SUCCEEDED: ResourceState.READY,
    PrepareState.FAILED: ResourceState.FAILED,
    PrepareState.CANCELED: ResourceState.CANCELLED,
    PrepareState.EXPIRED: ResourceState.FAILED,
}


def resolve_operation_state(value: str | PrepareState) -> PrepareState:
    """Coerce a stored/typed state to the :class:`PrepareState` enum."""
    if isinstance(value, PrepareState):
        return value
    return PrepareState(str(value))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PrepareOperationError(RuntimeError):
    """Base error for persistent prepare operations."""


class PrepareOperationNotFoundError(PrepareOperationError):
    """The operation id does not exist in the authority store."""


class PrepareOperationTerminalError(PrepareOperationError):
    """The operation already reached a terminal state; the write is refused."""


class StaleLeaseError(PrepareOperationError):
    """A stale lease holder attempted to write (lease token / generation mismatch).

    This is the executable form of "旧租约持有者失去发布资格": after an expiry
    takeover or a restart recovery bumps the ownership generation, the previous
    holder's ``complete``/``fail``/``renew`` CAS can no longer match.
    """


class PreparePublishRejectedError(PrepareOperationError):
    """The completion CAS rejected the publish (authorization/binding/generation).

    Raised when the operation's recorded authorization version, storage binding
    id, resource generation or resource digest no longer matches what the caller
    asserts — e.g. an authorization was revoked between claim and publish.
    """


# ---------------------------------------------------------------------------
# Identity keys
# ---------------------------------------------------------------------------


def default_resource_key(job_id: str, attempt: int, resource_id: str) -> str:
    """Return the stable dedup resource key for a job-attempt resource."""
    return f"{job_id}\x1f{int(attempt)}\x1f{resource_id}"


def idempotency_key_for(*, permission_scope: str, resource_key: str) -> str:
    """Return the stable idempotency key of one scoped resource.

    The permission scope is folded into the key so a different authorization
    scope yields a distinct operation for the same resource.  That is what keeps
    one observer's cancel from killing another observer's legitimate preparation.
    """
    payload = f"{permission_scope}\x1f{resource_key}"
    return "op_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepareRequest:
    """The identity + policy of one enqueued prepare operation.

    ``resource_digest`` summarises the resource identity (job/attempt/paths) and
    is re-checked by the completion CAS.  ``permission_scope`` participates in
    the idempotency key.  ``authorization_version`` / ``binding_id`` /
    ``resource_generation`` are the frozen facts the completion CAS re-checks so
    a revoked authorization or rebound storage cannot publish.
    """

    job_id: str
    attempt: int
    resource_id: str
    resource_kind: ResourceKind
    resource_digest: str = ""
    permission_scope: str = "local-single"
    resource_key: str = ""
    authorization_version: int = 0
    binding_id: str = ""
    resource_generation: int = 0
    max_attempts: int = 3
    payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.job_id).strip():
            raise ValueError("job_id must be a non-empty string")
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")
        if not str(self.resource_id).strip():
            raise ValueError("resource_id must be a non-empty string")
        if int(self.max_attempts) < 1:
            raise ValueError("max_attempts must be >= 1")

    @property
    def key(self) -> str:
        """The dedup resource key (defaults to job-attempt-resource)."""
        return self.resource_key or default_resource_key(
            self.job_id, self.attempt, self.resource_id
        )

    @property
    def idempotency_key(self) -> str:
        """The stable scoped idempotency key."""
        return idempotency_key_for(permission_scope=self.permission_scope, resource_key=self.key)


@dataclass(frozen=True)
class PrepareOperationRecord:
    """One persisted prepare operation (authoritative row projection)."""

    operation_id: str
    idempotency_key: str
    job_id: str
    attempt: int
    resource_id: str
    resource_kind: str
    resource_digest: str
    resource_key: str
    permission_scope: str
    state: PrepareState
    owner_generation: int
    lease_token: str | None
    lease_expires_at: float | None
    attempt_count: int
    max_attempts: int
    authorization_version: int
    binding_id: str
    resource_generation: int
    availability: Availability
    error_code: str | None
    error_message: str | None
    result: Mapping[str, Any] | None
    payload: Mapping[str, Any]
    created_at: str
    updated_at: str
    available_at: float
    size_bytes: int | None = None
    sha256: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state.value in _TERMINAL_STATES

    def to_contract(self) -> PrepareOperation:
        """Project the row onto the frozen ``PrepareOperation`` wire shape."""
        error: Mapping[str, Any] | None = None
        if self.error_code is not None:
            error = {"code": self.error_code, "message": self.error_message or ""}
        if self.state is PrepareState.SUCCEEDED:
            freshness = Freshness.CURRENT
        elif self.state in (PrepareState.QUEUED, PrepareState.RUNNING):
            freshness = Freshness.UNKNOWN
        else:
            freshness = Freshness.UNKNOWN
        return PrepareOperation(
            operation_id=self.operation_id,
            idempotency_key=self.idempotency_key,
            resource_id=self.resource_id,
            resource_kind=ResourceKind(self.resource_kind),
            state=_STATE_TO_RESOURCE[self.state],
            availability=self.availability,
            freshness=freshness,
            basis_revision=self.resource_generation,
            expected_revision=self.resource_generation,
            observed_at=self.updated_at,
            error=error,
            bytes=self.size_bytes,
            sha256=self.sha256,
        )


@dataclass(frozen=True)
class PrepareLease:
    """The claim a worker holds over one operation.

    Carried to the executor (which reads :attr:`payload`) and back into
    ``complete``/``fail``/``renew`` where the token + generation are the CAS
    guard.
    """

    operation_id: str
    job_id: str
    attempt: int
    resource_id: str
    resource_kind: str
    resource_digest: str
    resource_key: str
    permission_scope: str
    owner_generation: int
    lease_token: str
    lease_expires_at: float | None
    authorization_version: int
    binding_id: str
    resource_generation: int
    attempt_count: int
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrepareCompletion:
    """The publish facts asserted at completion (CAS inputs).

    ``authorization_version`` / ``binding_id`` / ``resource_generation`` default
    to the lease's claim-time values; passing the *current* values lets the CAS
    detect a revocation or rebind that happened after the claim.
    """

    availability: Availability = Availability.READY
    result: Mapping[str, Any] | None = None
    authorization_version: int | None = None
    binding_id: str | None = None
    resource_generation: int | None = None
    resource_digest: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class PrepareExecutionResult:
    """Outcome of one executor run (what the worker records)."""

    prepared: bool
    availability: Availability = Availability.ABSENT
    detail: str = ""
    retryable: bool = True
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class RecoveryReport:
    """Counts from one :meth:`PrepareOperationService.recover` pass."""

    requeued: int = 0
    expired: int = 0
    queued: int = 0
    running: int = 0


# ---------------------------------------------------------------------------
# Executor port
# ---------------------------------------------------------------------------


@runtime_checkable
class PrepareExecutorPort(Protocol):
    """The injected prepare mechanism (the actual prefetch lives outside here).

    The Services worker owns the state machine; the executor only performs the
    resource preparation and reports the outcome.  The legacy-side adapter
    (``acp.services_data_bootstrap.build_prepare_executor``) implements this by
    delegating to the real remote structure cache.
    """

    def execute(self, lease: PrepareLease) -> PrepareExecutionResult:
        """Perform the preparation for *lease* and report the outcome."""
        ...


# ---------------------------------------------------------------------------
# SQLite authority
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    job_attempt INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_digest TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    permission_scope TEXT NOT NULL,
    state TEXT NOT NULL,
    owner_generation INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_expires_at REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    authorization_version INTEGER NOT NULL DEFAULT 0,
    binding_id TEXT NOT NULL DEFAULT '',
    resource_generation INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'absent',
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    payload_json TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    available_at REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_data_operations_state
    ON data_operations(state, available_at);
CREATE INDEX IF NOT EXISTS idx_data_operations_key
    ON data_operations(idempotency_key);
"""

_RECORD_COLUMNS = (
    "operation_id",
    "idempotency_key",
    "job_id",
    "job_attempt",
    "resource_id",
    "resource_kind",
    "resource_digest",
    "resource_key",
    "permission_scope",
    "state",
    "owner_generation",
    "lease_token",
    "lease_expires_at",
    "attempt_count",
    "max_attempts",
    "authorization_version",
    "binding_id",
    "resource_generation",
    "availability",
    "error_code",
    "error_message",
    "result_json",
    "payload_json",
    "size_bytes",
    "sha256",
    "created_at",
    "updated_at",
    "available_at",
)


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _json_dumps(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(dict(value), sort_keys=True)


def _json_loads(value: str | None) -> Mapping[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class PrepareOperationService:
    """Durable prepare-operation state machine (DB authority, no memory facts).

    Args:
        db_path: Explicit SQLite path (idempotent schema created on init).  The
            scheduler passes its own ``acp_jobs.db`` so operations survive the
            same backup/restore as jobs.
        default_max_attempts: Retry budget applied to requests that don't set one.
        default_lease_seconds: Lease duration granted by :meth:`lease_next`.
        retry_backoff_seconds: Delay before a retryable failure is claimable again.
        clock: Injectable time source (seconds); tests drive lease expiry with it.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        default_max_attempts: int = 3,
        default_lease_seconds: float = 60.0,
        retry_backoff_seconds: float = 0.25,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._default_max_attempts = max(1, int(default_max_attempts))
        self._default_lease_seconds = max(0.001, float(default_lease_seconds))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._clock = clock or time.time
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        """Close the authority connection (idempotent)."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    def __enter__(self) -> PrepareOperationService:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- row helpers ----------------------------------------------------

    def _record_from_row(self, row: sqlite3.Row) -> PrepareOperationRecord:
        return PrepareOperationRecord(
            operation_id=row["operation_id"],
            idempotency_key=row["idempotency_key"],
            job_id=row["job_id"],
            attempt=int(row["job_attempt"]),
            resource_id=row["resource_id"],
            resource_kind=row["resource_kind"],
            resource_digest=row["resource_digest"],
            resource_key=row["resource_key"],
            permission_scope=row["permission_scope"],
            state=PrepareState(row["state"]),
            owner_generation=int(row["owner_generation"]),
            lease_token=row["lease_token"],
            lease_expires_at=row["lease_expires_at"],
            attempt_count=int(row["attempt_count"]),
            max_attempts=int(row["max_attempts"]),
            authorization_version=int(row["authorization_version"]),
            binding_id=str(row["binding_id"] or ""),
            resource_generation=int(row["resource_generation"]),
            availability=Availability(row["availability"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            result=_json_loads(row["result_json"]),
            payload=_json_loads(row["payload_json"]) or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            available_at=float(row["available_at"]),
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
        )

    def _lease_from_row(self, row: sqlite3.Row) -> PrepareLease:
        return PrepareLease(
            operation_id=row["operation_id"],
            job_id=row["job_id"],
            attempt=int(row["job_attempt"]),
            resource_id=row["resource_id"],
            resource_kind=row["resource_kind"],
            resource_digest=row["resource_digest"],
            resource_key=row["resource_key"],
            permission_scope=row["permission_scope"],
            owner_generation=int(row["owner_generation"]),
            lease_token=str(row["lease_token"] or ""),
            lease_expires_at=row["lease_expires_at"],
            authorization_version=int(row["authorization_version"]),
            binding_id=str(row["binding_id"] or ""),
            resource_generation=int(row["resource_generation"]),
            attempt_count=int(row["attempt_count"]),
            payload=_json_loads(row["payload_json"]) or {},
        )

    def _get_row_locked(self, operation_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM data_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()

    # -- reads ----------------------------------------------------------

    def get(self, operation_id: str) -> PrepareOperationRecord | None:
        """Return one operation, or ``None`` when it does not exist."""
        with self._lock:
            row = self._get_row_locked(operation_id)
            return self._record_from_row(row) if row is not None else None

    def find(
        self,
        *,
        job_id: str,
        resource_id: str,
        attempt: int,
        permission_scope: str = "local-single",
        resource_key: str = "",
    ) -> PrepareOperationRecord | None:
        """Look an operation up by its dedup identity."""
        key = resource_key or default_resource_key(job_id, attempt, resource_id)
        idem = idempotency_key_for(permission_scope=permission_scope, resource_key=key)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_operations WHERE idempotency_key=?", (idem,)
            ).fetchone()
            return self._record_from_row(row) if row is not None else None

    def list(
        self,
        *,
        states: Iterable[PrepareState | str] | None = None,
        limit: int = 1000,
    ) -> tuple[PrepareOperationRecord, ...]:
        """List operations, optionally filtered by state."""
        with self._lock:
            if states is None:
                rows = self._conn.execute(
                    "SELECT * FROM data_operations ORDER BY created_at ASC, operation_id ASC "
                    "LIMIT ?",
                    (int(limit),),
                ).fetchall()
            else:
                values = [(s.value if isinstance(s, PrepareState) else str(s)) for s in states]
                if not values:
                    return ()
                placeholders = ",".join("?" for _ in values)
                rows = self._conn.execute(
                    f"SELECT * FROM data_operations WHERE state IN ({placeholders}) "
                    "ORDER BY created_at ASC, operation_id ASC LIMIT ?",
                    (*values, int(limit)),
                ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)

    def pending_count(self) -> int:
        """Number of undecided (``queued`` + ``running``) operations."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM data_operations WHERE state IN (?, ?)",
                (PrepareState.QUEUED.value, PrepareState.RUNNING.value),
            ).fetchone()
            return int(row["n"])

    def availability(self, operation_id: str) -> DataAvailability:
        """Return the committed availability of one operation.

        The availability outcome is committed by :meth:`complete`; while the
        operation is undecided it reads as ``preparing`` and a failure reads as
        ``unavailable`` — never as a ready success.
        """
        record = self.get(operation_id)
        if record is None:
            raise PrepareOperationNotFoundError(f"no prepare operation {operation_id!r}")
        if record.state is PrepareState.SUCCEEDED:
            availability = record.availability
            freshness = Freshness.CURRENT
        elif record.state in (PrepareState.QUEUED, PrepareState.RUNNING):
            availability = Availability.PREPARING
            freshness = Freshness.UNKNOWN
        elif record.state is PrepareState.FAILED:
            availability = Availability.UNAVAILABLE
            freshness = Freshness.UNKNOWN
        else:
            availability = Availability.ABSENT
            freshness = Freshness.UNKNOWN
        return DataAvailability(
            resource_id=record.resource_id,
            availability=availability,
            freshness=freshness,
            basis_revision=record.resource_generation,
            expected_revision=record.resource_generation,
        )

    # -- enqueue (dedup by stable key) ----------------------------------

    def enqueue(self, request: PrepareRequest) -> PrepareOperationRecord:
        """Enqueue one operation, deduplicating on the stable idempotency key.

        Returns the existing operation when the same scoped resource is already
        known (one row / one execution).  A previously ``failed`` operation is
        reset to ``queued`` with a fresh retry budget so a re-enqueue retries
        rather than silently succeeding.
        """
        now = self._clock()
        iso = _utc_now_iso()
        resource_key = request.key
        idem = request.idempotency_key
        payload_json = _json_dumps(request.payload)
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM data_operations WHERE idempotency_key=?", (idem,)
            ).fetchone()
            if existing is not None:
                record = self._record_from_row(existing)
                if record.state is PrepareState.FAILED:
                    self._conn.execute(
                        "UPDATE data_operations SET state=?, owner_generation=owner_generation+1, "
                        "lease_token=NULL, lease_expires_at=NULL, attempt_count=0, available_at=?, "
                        "authorization_version=?, binding_id=?, resource_generation=?, "
                        "resource_digest=?, payload_json=?, error_code=NULL, error_message=NULL, "
                        "updated_at=? WHERE operation_id=?",
                        (
                            PrepareState.QUEUED.value,
                            now,
                            int(request.authorization_version),
                            request.binding_id,
                            int(request.resource_generation),
                            request.resource_digest,
                            payload_json,
                            iso,
                            record.operation_id,
                        ),
                    )
                    return self._record_from_row(self._get_row_locked(record.operation_id))
                return record

            operation_id = "op_" + uuid.uuid4().hex
            params = (
                operation_id,
                idem,
                request.job_id,
                int(request.attempt),
                request.resource_id,
                request.resource_kind.value,
                request.resource_digest,
                resource_key,
                request.permission_scope,
                PrepareState.QUEUED.value,
                0,
                None,
                None,
                0,
                int(request.max_attempts) or self._default_max_attempts,
                int(request.authorization_version),
                request.binding_id,
                int(request.resource_generation),
                Availability.ABSENT.value,
                None,
                None,
                None,
                payload_json,
                None,
                None,
                iso,
                iso,
                now,
            )
            columns = ", ".join(_RECORD_COLUMNS)
            placeholders = ", ".join("?" for _ in _RECORD_COLUMNS)
            try:
                self._conn.execute(
                    f"INSERT INTO data_operations ({columns}) VALUES ({placeholders})", params
                )
            except sqlite3.IntegrityError:
                # Concurrent enqueue of the same scoped resource: adopt the winner.
                row = self._conn.execute(
                    "SELECT * FROM data_operations WHERE idempotency_key=?", (idem,)
                ).fetchone()
                if row is None:  # pragma: no cover - unique-constraint anomaly
                    raise
                return self._record_from_row(row)
            return self._record_from_row(self._get_row_locked(operation_id))

    # -- lease / heartbeat ----------------------------------------------

    def lease_next(
        self,
        *,
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> PrepareLease | None:
        """Claim the oldest eligible operation; ``None`` when none is ready.

        Eligible = a ``queued`` operation whose backoff has elapsed, or a
        ``running`` operation whose lease has expired (a vanished worker).  The
        claim bumps the ownership generation and issues a fresh lease token, so
        the prior holder's write CAS can never match again.
        """
        current = self._clock() if now is None else float(now)
        duration = self._default_lease_seconds if lease_seconds is None else float(lease_seconds)
        iso = _utc_now_iso()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                candidate = self._conn.execute(
                    "SELECT operation_id FROM data_operations "
                    "WHERE (state=? AND available_at<=?) "
                    "   OR (state=? AND lease_expires_at IS NOT NULL AND lease_expires_at<=?) "
                    "ORDER BY created_at ASC, operation_id ASC LIMIT 1",
                    (
                        PrepareState.QUEUED.value,
                        current,
                        PrepareState.RUNNING.value,
                        current,
                    ),
                ).fetchone()
                if candidate is None:
                    self._conn.execute("COMMIT")
                    return None
                operation_id = candidate["operation_id"]
                token = secrets.token_hex(16)
                updated = self._conn.execute(
                    "UPDATE data_operations SET state=?, owner_generation=owner_generation+1, "
                    "lease_token=?, lease_expires_at=?, attempt_count=attempt_count+1, "
                    "available_at=?, updated_at=? "
                    "WHERE operation_id=? AND (state=? OR (state=? AND lease_expires_at<=?))",
                    (
                        PrepareState.RUNNING.value,
                        token,
                        current + duration,
                        current,
                        iso,
                        operation_id,
                        PrepareState.QUEUED.value,
                        PrepareState.RUNNING.value,
                        current,
                    ),
                )
                if updated.rowcount == 0:
                    self._conn.execute("ROLLBACK")
                    return None
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._get_row_locked(operation_id)
            return self._lease_from_row(row) if row is not None else None

    def renew(
        self,
        lease: PrepareLease,
        *,
        lease_seconds: float | None = None,
        now: float | None = None,
    ) -> PrepareLease:
        """Extend a held lease; raises :class:`StaleLeaseError` when superseded."""
        current = self._clock() if now is None else float(now)
        duration = self._default_lease_seconds if lease_seconds is None else float(lease_seconds)
        with self._lock:
            updated = self._conn.execute(
                "UPDATE data_operations SET lease_expires_at=?, updated_at=? "
                "WHERE operation_id=? AND state=? AND lease_token=? AND owner_generation=?",
                (
                    current + duration,
                    _utc_now_iso(),
                    lease.operation_id,
                    PrepareState.RUNNING.value,
                    lease.lease_token,
                    lease.owner_generation,
                ),
            )
            if updated.rowcount == 0:
                raise StaleLeaseError(f"lease for {lease.operation_id!r} is no longer current")
            row = self._get_row_locked(lease.operation_id)
            if row is None:  # pragma: no cover - row vanished mid-lock
                raise PrepareOperationNotFoundError(lease.operation_id)
            return self._lease_from_row(row)

    # -- completion CAS -------------------------------------------------

    def complete(
        self,
        lease: PrepareLease,
        completion: PrepareCompletion | None = None,
    ) -> PrepareOperationRecord:
        """Commit a successful preparation with a full CAS check.

        The conditional UPDATE matches the operation, job attempt, resource
        digest, resource generation, authorization version and storage binding
        id, plus the lease token + ownership generation.  A stale lease holder
        raises :class:`StaleLeaseError`; a revoked authorization or rebind
        raises :class:`PreparePublishRejectedError`.
        """
        completion = completion or PrepareCompletion()
        auth = (
            lease.authorization_version
            if completion.authorization_version is None
            else int(completion.authorization_version)
        )
        binding = lease.binding_id if completion.binding_id is None else completion.binding_id
        generation = (
            lease.resource_generation
            if completion.resource_generation is None
            else int(completion.resource_generation)
        )
        digest = (
            lease.resource_digest
            if completion.resource_digest is None
            else completion.resource_digest
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._get_row_locked(lease.operation_id)
                if row is None:
                    raise PrepareOperationNotFoundError(lease.operation_id)
                if row["state"] != PrepareState.RUNNING.value:
                    raise PrepareOperationTerminalError(
                        f"operation {lease.operation_id!r} is {row['state']}, not running"
                    )
                if (
                    row["lease_token"] != lease.lease_token
                    or int(row["owner_generation"]) != lease.owner_generation
                    or row["resource_digest"] != digest
                    or int(row["job_attempt"]) != lease.attempt
                ):
                    raise StaleLeaseError(
                        f"stale lease holder cannot publish {lease.operation_id!r}"
                    )
                if (
                    int(row["authorization_version"]) != auth
                    or str(row["binding_id"] or "") != binding
                    or int(row["resource_generation"]) != generation
                ):
                    raise PreparePublishRejectedError(
                        f"publish rejected for {lease.operation_id!r}: "
                        "authorization/binding/generation changed"
                    )
                self._conn.execute(
                    "UPDATE data_operations SET state=?, availability=?, result_json=?, "
                    "size_bytes=?, sha256=?, lease_token=NULL, lease_expires_at=NULL, "
                    "updated_at=? WHERE operation_id=?",
                    (
                        PrepareState.SUCCEEDED.value,
                        completion.availability.value,
                        _json_dumps(completion.result),
                        completion.size_bytes,
                        completion.sha256,
                        _utc_now_iso(),
                        lease.operation_id,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._get_row_locked(lease.operation_id)
            assert row is not None
            return self._record_from_row(row)

    def fail(
        self,
        lease: PrepareLease,
        *,
        error_code: str,
        error_message: str = "",
        retryable: bool = True,
    ) -> PrepareOperationRecord:
        """Record a failure; a retryable failure with budget left returns to ``queued``."""
        now = self._clock()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._get_row_locked(lease.operation_id)
                if row is None:
                    raise PrepareOperationNotFoundError(lease.operation_id)
                if row["state"] != PrepareState.RUNNING.value:
                    raise PrepareOperationTerminalError(
                        f"operation {lease.operation_id!r} is {row['state']}, not running"
                    )
                if (
                    row["lease_token"] != lease.lease_token
                    or int(row["owner_generation"]) != lease.owner_generation
                ):
                    raise StaleLeaseError(f"stale lease holder cannot fail {lease.operation_id!r}")
                budget_left = int(row["attempt_count"]) < int(row["max_attempts"])
                if retryable and budget_left:
                    new_state = PrepareState.QUEUED.value
                    available_at = now + self._retry_backoff_seconds
                else:
                    new_state = PrepareState.FAILED.value
                    available_at = 0.0
                self._conn.execute(
                    "UPDATE data_operations SET state=?, error_code=?, error_message=?, "
                    "lease_token=NULL, lease_expires_at=NULL, available_at=?, updated_at=? "
                    "WHERE operation_id=?",
                    (
                        new_state,
                        error_code,
                        error_message,
                        available_at,
                        _utc_now_iso(),
                        lease.operation_id,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._get_row_locked(lease.operation_id)
            assert row is not None
            return self._record_from_row(row)

    # -- control --------------------------------------------------------

    def cancel(self, operation_id: str) -> PrepareOperationRecord:
        """Cancel one operation (idempotent; never touches another observer's).

        Because the idempotency key includes the permission scope, a cancel
        addresses exactly one scoped operation.  Other legitimate consumers of
        the same resource hold distinct operations and are unaffected.
        """
        with self._lock:
            row = self._get_row_locked(operation_id)
            if row is None:
                raise PrepareOperationNotFoundError(operation_id)
            if row["state"] in _TERMINAL_STATES:
                return self._record_from_row(row)
            self._conn.execute(
                "UPDATE data_operations SET state=?, lease_token=NULL, lease_expires_at=NULL, "
                "updated_at=? WHERE operation_id=?",
                (PrepareState.CANCELED.value, _utc_now_iso(), operation_id),
            )
            return self._record_from_row(self._get_row_locked(operation_id))

    def expire(self, operation_id: str, *, reason: str = "") -> PrepareOperationRecord:
        """Invalidate a non-terminal operation (e.g. its resource was superseded).

        Expiry is explicit — a lease simply running out never sets this state.
        """
        with self._lock:
            row = self._get_row_locked(operation_id)
            if row is None:
                raise PrepareOperationNotFoundError(operation_id)
            if row["state"] in _TERMINAL_STATES:
                return self._record_from_row(row)
            self._conn.execute(
                "UPDATE data_operations SET state=?, error_code=?, error_message=?, "
                "lease_token=NULL, lease_expires_at=NULL, updated_at=? WHERE operation_id=?",
                (
                    PrepareState.EXPIRED.value,
                    "expired",
                    reason,
                    _utc_now_iso(),
                    operation_id,
                ),
            )
            return self._record_from_row(self._get_row_locked(operation_id))

    def recover(
        self,
        *,
        takeover_running: bool = True,
        now: float | None = None,
    ) -> RecoveryReport:
        """Replay undecided operations after a restart.

        ``queued`` operations are made immediately claimable.  ``running``
        operations are returned to ``queued`` (bumping the ownership generation
        so the vanished holder's token is dead) either unconditionally
        (``takeover_running=True``, the startup replay) or only when their lease
        has expired.  Recovery **never** marks success.
        """
        current = self._clock() if now is None else float(now)
        requeued = 0
        expired = 0
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT operation_id, state, lease_expires_at, available_at "
                    "FROM data_operations WHERE state IN (?, ?)",
                    (PrepareState.QUEUED.value, PrepareState.RUNNING.value),
                ).fetchall()
                for row in rows:
                    if row["state"] == PrepareState.RUNNING.value:
                        lease_expired = (
                            row["lease_expires_at"] is not None
                            and float(row["lease_expires_at"]) <= current
                        )
                        if not takeover_running and not lease_expired:
                            continue
                        self._conn.execute(
                            "UPDATE data_operations SET state=?, "
                            "owner_generation=owner_generation+1, lease_token=NULL, "
                            "lease_expires_at=NULL, available_at=?, updated_at=? "
                            "WHERE operation_id=?",
                            (
                                PrepareState.QUEUED.value,
                                current,
                                _utc_now_iso(),
                                row["operation_id"],
                            ),
                        )
                        requeued += 1
                        if lease_expired:
                            expired += 1
                    elif float(row["available_at"]) > current:
                        self._conn.execute(
                            "UPDATE data_operations SET available_at=?, updated_at=? "
                            "WHERE operation_id=?",
                            (current, _utc_now_iso(), row["operation_id"]),
                        )
                queued = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM data_operations WHERE state=?",
                    (PrepareState.QUEUED.value,),
                ).fetchone()
                running = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM data_operations WHERE state=?",
                    (PrepareState.RUNNING.value,),
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return RecoveryReport(
            requeued=requeued,
            expired=expired,
            queued=int(queued["n"]),
            running=int(running["n"]),
        )


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class PrepareOperationWorker:
    """Drain queued prepare operations through an injected executor.

    The worker polls the authority store; the optional ``wake_queue`` is only a
    low-latency signal and is never the source of truth.  One execution failure
    is recorded (and retried per policy) and never kills the loop.
    """

    def __init__(
        self,
        service: PrepareOperationService,
        executor: PrepareExecutorPort,
        *,
        stop_event: threading.Event | None = None,
        wake_queue: queue.Queue[Any] | None = None,
        lease_seconds: float | None = None,
        poll_interval: float = 0.2,
    ) -> None:
        self._service = service
        self._executor = executor
        self._stop = stop_event or threading.Event()
        self._wake_queue = wake_queue
        self._lease_seconds = lease_seconds
        self._poll_interval = max(0.01, float(poll_interval))
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the worker thread (idempotent)."""
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="acp-prepare-operations"
        )
        self.thread.start()

    def stop(self, *, timeout: float | None = 5.0) -> None:
        """Signal the worker and join it."""
        self._stop.set()
        if self._wake_queue is not None:
            try:
                self._wake_queue.put(None)
            except Exception:  # pragma: no cover - queue never rejects
                pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    @property
    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _wait(self) -> None:
        if self._wake_queue is not None:
            try:
                self._wake_queue.get(timeout=self._poll_interval)
            except queue.Empty:
                pass
        else:
            self._stop.wait(self._poll_interval)

    def _loop(self) -> None:
        while not self._stop.is_set():
            lease: PrepareLease | None = None
            try:
                lease = self._service.lease_next(lease_seconds=self._lease_seconds)
            except StaleLeaseError:  # pragma: no cover - lease_next never raises it
                lease = None
            except _PROGRAMMING_ERRORS:
                raise
            except Exception:
                logger.warning("prepare lease_next failed", exc_info=True)
            if lease is None:
                self._wait()
                continue
            self._execute(lease)

    def _execute(self, lease: PrepareLease) -> None:
        try:
            result = self._executor.execute(lease)
        except _PROGRAMMING_ERRORS:
            raise
        except Exception as exc:
            self._record_failure(lease, type(exc).__name__, str(exc), retryable=True)
            return
        if result.prepared:
            try:
                self._service.complete(
                    lease,
                    PrepareCompletion(
                        availability=result.availability,
                        result={"detail": result.detail} if result.detail else None,
                        size_bytes=result.size_bytes,
                        sha256=result.sha256,
                    ),
                )
            except PrepareOperationError:
                logger.info("prepare completion rejected for %s", lease.operation_id, exc_info=True)
        else:
            self._record_failure(lease, "not_prepared", result.detail, retryable=result.retryable)

    def _record_failure(
        self,
        lease: PrepareLease,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
    ) -> None:
        try:
            self._service.fail(
                lease,
                error_code=error_code,
                error_message=error_message,
                retryable=retryable,
            )
        except PrepareOperationError:
            logger.info("prepare failure record rejected for %s", lease.operation_id, exc_info=True)
        except _PROGRAMMING_ERRORS:
            raise
        except Exception:
            logger.warning(
                "prepare failure record errored for %s", lease.operation_id, exc_info=True
            )
