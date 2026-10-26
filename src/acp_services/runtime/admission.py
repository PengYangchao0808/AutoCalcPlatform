"""Resource admission — versioned user/project/key grants ∧ node/queue ∧ capacity.

B09-2 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 49, MU-05).  The
main plan §22.4 requires an *executable* resource-admission core:

* **有效资源是共同约束（交集）** — the effective resources of a submission are
  ``用户授权 ∧ 项目授权 ∧ Key 子预算 ∧ 节点/队列限制 ∧ 实际容量``.  This module
  computes that intersection (:class:`ResourceBounds.intersect`) and never sums
  two grants: two API keys of one user draw from the *same* user/project
  aggregate, so multiple keys **never stack**.
* **派发前原子预留/重验** — :meth:`ReservationLedger.reserve` performs the
  capacity check and the reservation insert in **one** SQLite
  ``BEGIN IMMEDIATE`` transaction (check-and-insert, never a read-then-reserve
  split), so concurrent submissions can never over-allocate.  A queued
  reservation holds storage/budget but **no running CPU**; :meth:`dispatch`
  atomically re-validates (capacity + policy version) immediately before the
  job is handed to execution.
* **PAUSED 计实际内存/磁盘占用** — a paused reservation continues to count its
  actual memory and disk bytes (it is not zero resource); only its CPU is
  released.  :class:`Reservation` encodes that per-state rule so the ledger's
  occupancy is honest.
* **有效上限必须可执行** — a ``quota-only`` admission requires a non-empty
  policy version, an *enforced* hard-limit capability and a finite bound for
  every requested dimension.  When no bound is derivable the admission is
  **refused** (``unbounded_request`` / ``hard_limit_unavailable`` /
  ``no_policy_version``); the only way past it is an explicit
  ``usage-only`` admission by an administrator.
* **拒绝理由结构化** — every verdict is an :class:`AdmissionDecision` with a
  typed :class:`AdmissionReason` and a machine-readable ``exceeded`` list.

Consumption of the frozen Access contract
-----------------------------------------
:class:`AdmissionController` consumes :class:`acp_services.contracts.access.ResourceGrantSpec`
(todo 7) — the frozen :class:`ResourceGrant` subclass that carries the reserved
``cores`` / ``memory_mb`` / ``concurrency`` / ``duration_s`` / ``storage_bytes``
/ ``queue`` limit placeholders.  This module does **not** extend the frozen
dataclass; node/queue allow-lists and per-queue / per-node capacity arrive
through the injected :class:`GrantProvider` (:class:`GrantResolution`), so a
node limit stays a *separate* input from a user/project/key grant.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
-------------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module is **stdlib only**
(``sqlite3`` backs the reservation ledger) plus the sibling Access contract and
the Runtime UTC helper.

Author: ACP Services (B lane, todo 49).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.access import ResourceGrantSpec
from acp_services.contracts.envelope import ExecutionReceipt
from acp_services.runtime.helpers import utc_now_iso
from acp_services.runtime.idempotency import ReservationIdempotency
from acp_services.runtime.receipts import (
    ReceiptHistoryBinding,
    ReceiptVerdict,
    verify_historical_receipt,
)
from acp_services.runtime.research_budget import (
    RECEIPT_CONSUMED_UNITS,
    RELEASE_RULES,
    UNIT_CAPABILITIES,
    BudgetConfigurationError,
    BudgetDecision,
    BudgetPolicy,
    BudgetProjection,
    BudgetReason,
    BudgetRelease,
    BudgetReservation,
    BudgetSettlement,
    BudgetState,
    release_statement,
)
from acp_services.runtime.usage_authority import (
    UntrustedUsageProducerError,
    UsageProducer,
    UsageSource,
    resolve_authority,
)
from acp_services.runtime.usage_units import UsageUnitCounts, normalize_usage_unit

logger = logging.getLogger(__name__)

__all__ = [
    # capability / mode vocabularies
    "HARD_LIMIT_ENFORCED",
    "HARD_LIMIT_UNAVAILABLE",
    "AdmissionMode",
    "AdmissionReason",
    "ReservationState",
    "AdmissionError",
    "AdmissionValidationError",
    # value objects
    "ResourceBounds",
    "Capacity",
    "AdmissionRequest",
    "GrantResolution",
    "AdmissionDecision",
    "Reservation",
    "ReserveLimits",
    "ReserveOutcome",
    # ports
    "GrantProvider",
    "CapacityProvider",
    # ledger + controller
    "ReservationLedger",
    "AdmissionController",
    "render_admission_decision",
    # trusted usage receipts (M0-07)
    "ADMISSION_SCHEMA_VERSION",
    "UsageIngestOutcome",
    "UsageReason",
    "UsageReceiptRejectedError",
    "get_admission_schema_version",
    "migrate_admission_schema",
    # research budget value objects (B17)
    "BudgetDecision",
    "BudgetPolicy",
    "BudgetProjection",
    "BudgetReason",
    "BudgetRelease",
    "BudgetReservation",
    "BudgetSettlement",
    "BudgetState",
    "release_statement",
]

#: Hard-limit capability values.  Only ``enforced`` permits a quota-only
#: admission: if the execution mechanism cannot actually enforce the ceiling we
#: must not claim a quota exists (mirrors ``data.budget``'s honest capability).
HARD_LIMIT_ENFORCED = "enforced"
HARD_LIMIT_UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class AdmissionMode(str):
    """Admission mode (plan §25.2 MU-10a).

    ``QUOTA_ONLY`` — the Lab research baseline: user/project/key limits are
    enforced and an executable upper bound is mandatory.

    ``USAGE_ONLY`` — an explicit administrator choice: usage is recorded but no
    ceiling is enforced.  It must be *requested*; it is never a silent fallback
    from a failed quota-only admission.
    """

    QUOTA_ONLY = "quota-only"
    USAGE_ONLY = "usage-only"
    ALL = frozenset({QUOTA_ONLY, USAGE_ONLY})


class ReservationState(str):
    """Lifecycle of one reservation.

    Only the four non-terminal states are "active".  Which dimensions each state
    holds is encoded by :meth:`Reservation.counted_cores` /
    :meth:`Reservation.counted_memory_mb` /
    :meth:`Reservation.counted_storage_bytes`:

    * ``RESERVED`` / ``DISPATCHED`` — hold CPU + memory + storage;
    * ``PAUSED`` — hold memory + storage (CPU released);
    * ``QUEUED`` — hold storage + a concurrency slot (no CPU);
    * ``RELEASED`` — terminal, nothing held.
    """

    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    PAUSED = "paused"
    QUEUED = "queued"
    RELEASED = "released"
    ACTIVE = frozenset({RESERVED, DISPATCHED, PAUSED, QUEUED})
    CPU_HOLDING = frozenset({RESERVED, DISPATCHED})
    MEMORY_HOLDING = frozenset({RESERVED, DISPATCHED, PAUSED})
    STORAGE_HOLDING = frozenset({RESERVED, DISPATCHED, PAUSED, QUEUED})


class AdmissionReason(str):
    """Structured admission verdict vocabulary."""

    ADMITTED = "admitted"
    USAGE_ONLY = "usage_only"

    # executable-bound / policy gates
    NO_POLICY_VERSION = "no_policy_version"
    UNBOUNDED_REQUEST = "unbounded_request"
    HARD_LIMIT_UNAVAILABLE = "hard_limit_unavailable"

    # placement
    QUEUE_NOT_ALLOWED = "queue_not_allowed"
    NODE_NOT_ALLOWED = "node_not_allowed"

    # grant / scope ceilings
    EXCEEDS_CORES = "exceeds_cores"
    EXCEEDS_MEMORY = "exceeds_memory"
    EXCEEDS_DURATION = "exceeds_duration"
    EXCEEDS_STORAGE = "exceeds_storage"
    EXCEEDS_CONCURRENCY = "exceeds_concurrency"

    # actual cluster capacity
    CAPACITY_CORES = "capacity_cores"
    CAPACITY_MEMORY = "capacity_memory"
    CAPACITY_STORAGE = "capacity_storage"
    CAPACITY_CONCURRENCY = "capacity_concurrency"

    # dispatch re-validation
    RESERVATION_NOT_FOUND = "reservation_not_found"
    RESERVATION_RELEASED = "reservation_released"
    POLICY_VERSION_CHANGED = "policy_version_changed"

    ALL = frozenset(
        {
            ADMITTED,
            USAGE_ONLY,
            NO_POLICY_VERSION,
            UNBOUNDED_REQUEST,
            HARD_LIMIT_UNAVAILABLE,
            QUEUE_NOT_ALLOWED,
            NODE_NOT_ALLOWED,
            EXCEEDS_CORES,
            EXCEEDS_MEMORY,
            EXCEEDS_DURATION,
            EXCEEDS_STORAGE,
            EXCEEDS_CONCURRENCY,
            CAPACITY_CORES,
            CAPACITY_MEMORY,
            CAPACITY_STORAGE,
            CAPACITY_CONCURRENCY,
            RESERVATION_NOT_FOUND,
            RESERVATION_RELEASED,
            POLICY_VERSION_CHANGED,
        }
    )


#: Dimensions that accumulate across reservations (capacity is additive in
#: them) and therefore must be reserved atomically.  Duration is a per-job wall
#: bound, not additive, and is checked separately.
_ADDITIVE_DIMENSIONS = ("cores", "memory_mb", "storage_bytes")
#: Per-job dimensions that need an executable upper bound in quota-only mode.
_PER_JOB_DIMENSIONS = ("cores", "memory_mb", "duration_s", "storage_bytes")

_CAPACITY_REASONS = {
    "cores": AdmissionReason.CAPACITY_CORES,
    "memory_mb": AdmissionReason.CAPACITY_MEMORY,
    "storage_bytes": AdmissionReason.CAPACITY_STORAGE,
    "concurrency": AdmissionReason.CAPACITY_CONCURRENCY,
}
_SCOPE_REASONS = {
    "cores": AdmissionReason.EXCEEDS_CORES,
    "memory_mb": AdmissionReason.EXCEEDS_MEMORY,
    "duration_s": AdmissionReason.EXCEEDS_DURATION,
    "storage_bytes": AdmissionReason.EXCEEDS_STORAGE,
    "concurrency": AdmissionReason.EXCEEDS_CONCURRENCY,
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AdmissionError(RuntimeError):
    """Base error for resource admission."""


class AdmissionValidationError(AdmissionError):
    """A request / capacity value is structurally invalid (fail closed)."""


# ---------------------------------------------------------------------------
# Effective resource bounds (the intersection)
# ---------------------------------------------------------------------------


def _min_opt(left: int | None, right: int | None) -> int | None:
    """Intersection of two optional numeric ceilings (``None`` == unbounded)."""
    if left is None:
        return right
    if right is None:
        return left
    return min(int(left), int(right))


def _intersect_opt(
    left: tuple[str, ...] | None, right: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Intersection of two optional allow-lists (``None`` == unrestricted).

    An empty tuple is a *real* value (nothing allowed) and is preserved.
    """
    if left is None:
        return right
    if right is None:
        return left
    return tuple(sorted(set(left) & set(right)))


@dataclass(frozen=True)
class ResourceBounds:
    """A single resource ceiling set — intersected, never summed.

    ``None`` means "no ceiling from this source" for a numeric field, and
    "unrestricted" for the node/queue allow-lists.  An empty tuple allow-list
    means "nothing allowed".
    """

    cores: int | None = None
    memory_mb: int | None = None
    concurrency: int | None = None
    duration_s: int | None = None
    storage_bytes: int | None = None
    queues: tuple[str, ...] | None = None
    nodes: tuple[str, ...] | None = None

    def intersect(self, other: ResourceBounds) -> ResourceBounds:
        """Return the tightest ceiling implied by both bound sets."""
        return ResourceBounds(
            cores=_min_opt(self.cores, other.cores),
            memory_mb=_min_opt(self.memory_mb, other.memory_mb),
            concurrency=_min_opt(self.concurrency, other.concurrency),
            duration_s=_min_opt(self.duration_s, other.duration_s),
            storage_bytes=_min_opt(self.storage_bytes, other.storage_bytes),
            queues=_intersect_opt(self.queues, other.queues),
            nodes=_intersect_opt(self.nodes, other.nodes),
        )

    def is_finite(self, dimension: str) -> bool:
        """Whether an executable ceiling exists for *dimension*."""
        return getattr(self, dimension) is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cores": self.cores,
            "memory_mb": self.memory_mb,
            "concurrency": self.concurrency,
            "duration_s": self.duration_s,
            "storage_bytes": self.storage_bytes,
            "queues": None if self.queues is None else list(self.queues),
            "nodes": None if self.nodes is None else list(self.nodes),
        }


def _bounds_from_grant(grant: ResourceGrantSpec | None) -> ResourceBounds:
    """Project a todo-7 :class:`ResourceGrantSpec` onto a :class:`ResourceBounds`.

    A ``None`` grant contributes no constraint (identity for the intersection);
    an explicit ``queue`` narrows the allowed queue set to exactly that queue.
    """
    if grant is None:
        return ResourceBounds()
    queues = None if grant.queue is None else (str(grant.queue),)
    return ResourceBounds(
        cores=grant.cores,
        memory_mb=grant.memory_mb,
        concurrency=grant.concurrency,
        duration_s=grant.duration_s,
        storage_bytes=grant.storage_bytes,
        queues=queues,
        nodes=None,
    )


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capacity:
    """A capacity snapshot (total amounts an execution pool can provide).

    ``available=False`` means no real hard limit is known (the honest
    "capability unavailable" state): a quota-only admission is then refused
    rather than fabricated.
    """

    cores: int = 0
    memory_mb: int = 0
    concurrency: int = 0
    storage_bytes: int = 0
    source: str = ""
    available: bool = True
    queue: str | None = None
    node: str | None = None

    def __post_init__(self) -> None:
        values = (self.cores, self.memory_mb, self.concurrency, self.storage_bytes)
        if any(int(v) < 0 for v in values):
            raise AdmissionValidationError("capacity values must be non-negative")

    def minimum(self, other: Capacity) -> Capacity:
        """Per-dimension minimum of two capacity snapshots.

        Used to fold a per-queue / per-node ceiling into the global capacity:
        the effective pool is the tightest of the three.
        """
        if not self.available or not other.available:
            return Capacity(available=False, source=self.source or other.source)
        return Capacity(
            cores=min(int(self.cores), int(other.cores)),
            memory_mb=min(int(self.memory_mb), int(other.memory_mb)),
            concurrency=min(int(self.concurrency), int(other.concurrency)),
            storage_bytes=min(int(self.storage_bytes), int(other.storage_bytes)),
            source="∧".join(s for s in (self.source, other.source) if s),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cores": self.cores,
            "memory_mb": self.memory_mb,
            "concurrency": self.concurrency,
            "storage_bytes": self.storage_bytes,
            "source": self.source,
            "available": self.available,
        }


# ---------------------------------------------------------------------------
# Requests / resolutions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdmissionRequest:
    """One pre-dispatch resource request.

    ``queued=True`` asks for a *queued* admission: storage/budget and a job slot
    are reserved but no running CPU (the job waits in line).
    """

    principal_id: str
    project_id: str
    job_id: str
    key_id: str | None = None
    cores: int = 0
    memory_mb: int = 0
    duration_s: int = 0
    storage_bytes: int = 0
    queue: str | None = None
    node: str | None = None
    queued: bool = False

    def __post_init__(self) -> None:
        if not str(self.principal_id).strip():
            raise AdmissionValidationError("principal_id must be non-empty")
        if not str(self.project_id).strip():
            raise AdmissionValidationError("project_id must be non-empty")
        if not str(self.job_id).strip():
            raise AdmissionValidationError("job_id must be non-empty")
        for name in ("cores", "memory_mb", "duration_s", "storage_bytes"):
            if int(getattr(self, name)) < 0:
                raise AdmissionValidationError(f"{name} must be non-negative")

    def resource_dimensions(self) -> tuple[str, ...]:
        """Requested per-job dimensions with a positive amount."""
        return tuple(dim for dim in _PER_JOB_DIMENSIONS if int(getattr(self, dim)) > 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "key_id": self.key_id,
            "cores": self.cores,
            "memory_mb": self.memory_mb,
            "duration_s": self.duration_s,
            "storage_bytes": self.storage_bytes,
            "queue": self.queue,
            "node": self.node,
            "queued": self.queued,
        }


@dataclass(frozen=True)
class GrantResolution:
    """The three grants + placement/policy facts for one request.

    Produced by an injected :class:`GrantProvider`.  Keeping node/queue
    allow-lists and per-queue / per-node capacity here (rather than on the frozen
    :class:`ResourceGrantSpec`) preserves the contract that a *node limit* is a
    separate input from a user/project/key grant.
    """

    policy_version: str
    user_grant: ResourceGrantSpec | None = None
    project_grant: ResourceGrantSpec | None = None
    key_grant: ResourceGrantSpec | None = None
    hard_limit_capability: str = HARD_LIMIT_ENFORCED
    allowed_queues: tuple[str, ...] | None = None
    allowed_nodes: tuple[str, ...] | None = None
    queue_capacity: Mapping[str, Capacity] = field(default_factory=dict)
    node_capacity: Mapping[str, Capacity] = field(default_factory=dict)

    def effective_bounds(self) -> ResourceBounds:
        """The effective ceiling: user ∧ project ∧ key ∧ placement."""
        bounds = ResourceBounds(queues=self.allowed_queues, nodes=self.allowed_nodes)
        for grant in (self.user_grant, self.project_grant, self.key_grant):
            bounds = bounds.intersect(_bounds_from_grant(grant))
        return bounds


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


@runtime_checkable
class GrantProvider(Protocol):
    """Resolves the versioned user/project/key grants + placement for a request."""

    def resolve(self, request: AdmissionRequest) -> GrantResolution:
        """Return the grants and limits that govern *request*."""
        ...


@runtime_checkable
class CapacityProvider(Protocol):
    """Supplies the current *actual* capacity of the execution pool."""

    def capacity(self) -> Capacity:
        """Return the current capacity snapshot."""
        ...


# ---------------------------------------------------------------------------
# Decisions / records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdmissionDecision:
    """Structured admission verdict — the refusal reason is machine-readable."""

    admitted: bool
    reason_code: str
    detail: str = ""
    reservation_id: str | None = None
    mode: str = AdmissionMode.QUOTA_ONLY
    enforced: bool = False
    effective_bounds: ResourceBounds | None = None
    policy_version: str = ""
    exceeded: tuple[Mapping[str, Any], ...] = ()

    @property
    def allowed(self) -> bool:
        return self.admitted

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "reservation_id": self.reservation_id,
            "mode": self.mode,
            "enforced": self.enforced,
            "effective_bounds": (
                None if self.effective_bounds is None else self.effective_bounds.to_dict()
            ),
            "policy_version": self.policy_version,
            "exceeded": [dict(item) for item in self.exceeded],
        }


@dataclass(frozen=True)
class Reservation:
    """One persisted reservation with its live state and measured usage."""

    reservation_id: str
    principal_id: str
    project_id: str
    job_id: str
    state: str
    cores: int = 0
    memory_mb: int = 0
    storage_bytes: int = 0
    duration_s: int = 0
    key_id: str | None = None
    queue: str | None = None
    node: str | None = None
    policy_version: str = ""
    actual_cores: int | None = None
    actual_memory_mb: int | None = None
    actual_storage_bytes: int | None = None
    created_at: str = ""
    updated_at: str = ""

    def counted_cores(self) -> int:
        """CPU cores this reservation holds right now (0 for queued/paused)."""
        if self.state not in ReservationState.CPU_HOLDING:
            return 0
        return self.cores if self.actual_cores is None else self.actual_cores

    def counted_memory_mb(self) -> int:
        """Memory held right now (counted for paused — memory is not freed)."""
        if self.state not in ReservationState.MEMORY_HOLDING:
            return 0
        return self.memory_mb if self.actual_memory_mb is None else self.actual_memory_mb

    def counted_storage_bytes(self) -> int:
        """Disk held right now (counted for paused and queued)."""
        if self.state not in ReservationState.STORAGE_HOLDING:
            return 0
        if self.actual_storage_bytes is None:
            return self.storage_bytes
        return self.actual_storage_bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "job_id": self.job_id,
            "state": self.state,
            "cores": self.cores,
            "memory_mb": self.memory_mb,
            "storage_bytes": self.storage_bytes,
            "duration_s": self.duration_s,
            "key_id": self.key_id,
            "queue": self.queue,
            "node": self.node,
            "policy_version": self.policy_version,
            "actual_cores": self.actual_cores,
            "actual_memory_mb": self.actual_memory_mb,
            "actual_storage_bytes": self.actual_storage_bytes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ReserveLimits:
    """Everything :meth:`ReservationLedger.reserve` needs to decide atomically.

    ``request_bounds`` is the effective per-job ceiling (the intersection);
    ``user_bounds`` / ``project_bounds`` / ``key_bounds`` are the per-scope
    cumulative ceilings the atomic check aggregates against.
    """

    capacity: Capacity
    policy_version: str = ""
    request_bounds: ResourceBounds = field(default_factory=ResourceBounds)
    user_bounds: ResourceBounds = field(default_factory=ResourceBounds)
    project_bounds: ResourceBounds = field(default_factory=ResourceBounds)
    key_bounds: ResourceBounds = field(default_factory=ResourceBounds)
    queued: bool = False
    enforce: bool = True


@dataclass(frozen=True)
class ReserveOutcome:
    """Raw ledger outcome (the controller lifts it into an :class:`AdmissionDecision`)."""

    allowed: bool
    reason_code: str
    detail: str = ""
    reservation: Reservation | None = None
    exceeded: tuple[Mapping[str, Any], ...] = ()


class UsageReason(str):
    """Stable settlement verdict vocabulary (usage receipts, M0-07)."""

    RECORDED = "usage_recorded"
    DUPLICATE = "usage_receipt_duplicate"

    # refusals — nothing is written on any of these
    PRODUCER_UNTRUSTED = "usage_producer_untrusted"
    CAPABILITY_MISSING = "usage_capability_missing"
    RECEIPT_INVALID = "usage_receipt_invalid"
    RESERVATION_NOT_FOUND = "usage_reservation_not_found"
    RESERVATION_JOB_MISMATCH = "usage_reservation_job_mismatch"
    RESERVATION_MISMATCH = "usage_reservation_mismatch"
    ATTEMPT_CONFLICT = "usage_attempt_conflict"
    RECEIPT_CONFLICT = "usage_receipt_conflict"
    BUDGET_MISMATCH = "usage_budget_mismatch"
    BUDGET_RELEASED = "usage_budget_released"
    NOT_DISPATCHED = "usage_not_dispatched"

    ALL = frozenset(
        {
            RECORDED,
            DUPLICATE,
            PRODUCER_UNTRUSTED,
            CAPABILITY_MISSING,
            RECEIPT_INVALID,
            RESERVATION_NOT_FOUND,
            RESERVATION_JOB_MISMATCH,
            RESERVATION_MISMATCH,
            ATTEMPT_CONFLICT,
            RECEIPT_CONFLICT,
            BUDGET_MISMATCH,
            BUDGET_RELEASED,
            NOT_DISPATCHED,
        }
    )


class UsageReceiptRejectedError(AdmissionError):
    """A trusted usage receipt failed verification; no row was written.

    ``code`` is a stable :class:`UsageReason` value.  The ledger raises this
    *before* committing anything, so a forged or stale receipt can never change
    a reservation, a remaining budget or the settlement projection.
    """

    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}" if detail else str(code))
        self.code = str(code)
        self.detail = str(detail)
        self.params = dict(params or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.detail, "params": dict(self.params)}


@dataclass(frozen=True)
class UsageIngestOutcome:
    """The settled result of one trusted usage receipt (never a silent pass)."""

    applied: bool
    duplicate: bool
    reason_code: str
    usage_id: str
    job_id: str
    attempt: int
    reservation_id: str = ""
    producer: str = ""
    trusted_source: str = ""
    chain_digest: str = ""
    binding_digest: str = ""
    counters: UsageUnitCounts = field(default_factory=UsageUnitCounts)
    budget_id: str = ""
    budget_reservation_id: str = ""
    budget_applied: bool = False
    consumed: Mapping[str, int] = field(default_factory=dict)
    remaining: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "duplicate": self.duplicate,
            "reason_code": self.reason_code,
            "receipt_id": self.usage_id,
            "usage_id": self.usage_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "reservation_id": self.reservation_id,
            "producer": self.producer,
            "trusted_source": self.trusted_source,
            "chain_digest": self.chain_digest,
            "binding_digest": self.binding_digest,
            "counters": self.counters.to_dict(),
            "budget_id": self.budget_id,
            "budget_reservation_id": self.budget_reservation_id,
            "budget_applied": self.budget_applied,
            "consumed": dict(self.consumed),
            "remaining": dict(self.remaining),
        }


#: Admission DB schema version.  ``025`` is the identity DB migration
#: (``api_keys.usage_capabilities``); this ledger continues the shared series at
#: ``026`` because it is a *separate* SQLite file with its own id-versioned meta
#: table.  Numbers are never reused, exactly like the jobs-DB 001–024 series.
ADMISSION_SCHEMA_VERSION = 26
_ADMISSION_MIGRATIONS: dict[int, str] = {
    26: (
        "trusted usage receipts (M0-07/G26): immutable per-(job, attempt) "
        "execution identity bindings plus receipt identity/chain columns on the "
        "usage and research-budget receipt rows"
    ),
}

_ADMISSION_META_SQL = """
CREATE TABLE IF NOT EXISTS admission_schema_migrations (
    id         INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


# ---------------------------------------------------------------------------
# Atomic reservation ledger
# ---------------------------------------------------------------------------


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS admission_reservations (
    reservation_id       TEXT PRIMARY KEY,
    principal_id         TEXT NOT NULL,
    project_id           TEXT NOT NULL,
    key_id               TEXT,
    job_id               TEXT NOT NULL,
    state                TEXT NOT NULL,
    cores                INTEGER NOT NULL DEFAULT 0,
    memory_mb            INTEGER NOT NULL DEFAULT 0,
    storage_bytes        INTEGER NOT NULL DEFAULT 0,
    duration_s           INTEGER NOT NULL DEFAULT 0,
    queue                TEXT,
    node                 TEXT,
    policy_version       TEXT NOT NULL,
    actual_cores         INTEGER,
    actual_memory_mb     INTEGER,
    actual_storage_bytes INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admission_scope
    ON admission_reservations(state, principal_id, project_id, key_id);
CREATE INDEX IF NOT EXISTS idx_admission_job ON admission_reservations(job_id);
CREATE TABLE IF NOT EXISTS admission_reservation_jobs (
    reservation_id TEXT NOT NULL,
    job_id         TEXT NOT NULL,
    bound_at       TEXT NOT NULL,
    PRIMARY KEY (reservation_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_admission_reservation_jobs_job
    ON admission_reservation_jobs(job_id);
CREATE TABLE IF NOT EXISTS admission_attempt_declarations (
    job_id         TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    reservation_id TEXT NOT NULL DEFAULT '',
    declared_at    TEXT NOT NULL,
    PRIMARY KEY (job_id, attempt)
);
CREATE TABLE IF NOT EXISTS admission_reservation_claims (
    claim_id         TEXT PRIMARY KEY,
    reservation_id   TEXT NOT NULL,
    scope_id         TEXT NOT NULL,
    action           TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admission_claim_reservation
    ON admission_reservation_claims(reservation_id);
CREATE TABLE IF NOT EXISTS admission_usage_receipts (
    usage_id        TEXT PRIMARY KEY,
    reservation_id  TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    attempt         INTEGER NOT NULL DEFAULT 1,
    cores           INTEGER NOT NULL DEFAULT 0,
    seconds         INTEGER NOT NULL DEFAULT 0,
    core_hours      TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    producer        TEXT NOT NULL DEFAULT '',
    trusted_source  TEXT NOT NULL DEFAULT '',
    submission_id   TEXT NOT NULL DEFAULT '',
    release_id      TEXT NOT NULL DEFAULT '',
    plan_identity   TEXT NOT NULL DEFAULT '',
    step_identities_json TEXT NOT NULL DEFAULT '[]',
    chain_digest    TEXT NOT NULL DEFAULT '',
    binding_digest  TEXT NOT NULL DEFAULT '',
    budget_id       TEXT NOT NULL DEFAULT '',
    budget_reservation_id TEXT NOT NULL DEFAULT '',
    run_id          TEXT NOT NULL DEFAULT '',
    payload_digest  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_admission_usage_reservation
    ON admission_usage_receipts(reservation_id);
CREATE INDEX IF NOT EXISTS idx_admission_usage_job
    ON admission_usage_receipts(job_id, attempt);
CREATE TABLE IF NOT EXISTS admission_attempt_bindings (
    job_id               TEXT NOT NULL,
    attempt              INTEGER NOT NULL,
    reservation_id       TEXT NOT NULL DEFAULT '',
    submission_id        TEXT NOT NULL DEFAULT '',
    release_id           TEXT NOT NULL DEFAULT '',
    release_digest       TEXT NOT NULL DEFAULT '',
    owner                TEXT NOT NULL DEFAULT '',
    fencing_token        TEXT NOT NULL DEFAULT '',
    plan_identity        TEXT NOT NULL DEFAULT '',
    step_identities_json TEXT NOT NULL DEFAULT '[]',
    chain_digest         TEXT NOT NULL DEFAULT '',
    producer             TEXT NOT NULL DEFAULT '',
    task_ref             TEXT NOT NULL DEFAULT '',
    budget_id            TEXT NOT NULL DEFAULT '',
    budget_reservation_id TEXT NOT NULL DEFAULT '',
    bound_at             TEXT NOT NULL,
    PRIMARY KEY (job_id, attempt)
);
CREATE TABLE IF NOT EXISTS research_budget_policies (
    budget_id     TEXT PRIMARY KEY,
    approval_ref  TEXT NOT NULL DEFAULT '',
    project_id    TEXT NOT NULL DEFAULT '',
    policy_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_budget_reservations (
    reservation_id           TEXT PRIMARY KEY,
    budget_id                TEXT NOT NULL,
    units_json               TEXT NOT NULL,
    state                    TEXT NOT NULL,
    task_ref                 TEXT NOT NULL DEFAULT '',
    run_id                   TEXT NOT NULL DEFAULT '',
    claim_id                 TEXT NOT NULL DEFAULT '',
    admission_reservation_id TEXT NOT NULL DEFAULT '',
    detail                   TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_budget_claim
    ON research_budget_reservations(budget_id, claim_id)
    WHERE claim_id != '';
CREATE INDEX IF NOT EXISTS idx_research_budget_state
    ON research_budget_reservations(budget_id, state);
CREATE INDEX IF NOT EXISTS idx_research_budget_task
    ON research_budget_reservations(task_ref);
CREATE INDEX IF NOT EXISTS idx_research_budget_admission
    ON research_budget_reservations(admission_reservation_id);
CREATE TABLE IF NOT EXISTS research_budget_receipts (
    receipt_id      TEXT PRIMARY KEY,
    budget_id       TEXT NOT NULL,
    reservation_id  TEXT NOT NULL,
    run_id          TEXT NOT NULL DEFAULT '',
    usage_json      TEXT NOT NULL,
    trusted_source  TEXT NOT NULL,
    applied         INTEGER NOT NULL DEFAULT 0,
    recorded_at     TEXT NOT NULL,
    job_id          TEXT NOT NULL DEFAULT '',
    attempt         INTEGER NOT NULL DEFAULT 0,
    usage_id        TEXT NOT NULL DEFAULT '',
    chain_digest    TEXT NOT NULL DEFAULT '',
    binding_digest  TEXT NOT NULL DEFAULT '',
    producer        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_research_budget_receipts_budget
    ON research_budget_receipts(budget_id);
CREATE INDEX IF NOT EXISTS idx_research_budget_receipts_job
    ON research_budget_receipts(job_id, attempt);
CREATE TABLE IF NOT EXISTS research_budget_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject    TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


_ADDITIVE_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "admission_usage_receipts": (
        ("producer", "TEXT NOT NULL DEFAULT ''"),
        ("trusted_source", "TEXT NOT NULL DEFAULT ''"),
        ("submission_id", "TEXT NOT NULL DEFAULT ''"),
        ("release_id", "TEXT NOT NULL DEFAULT ''"),
        ("plan_identity", "TEXT NOT NULL DEFAULT ''"),
        ("step_identities_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("chain_digest", "TEXT NOT NULL DEFAULT ''"),
        ("binding_digest", "TEXT NOT NULL DEFAULT ''"),
        ("budget_id", "TEXT NOT NULL DEFAULT ''"),
        ("budget_reservation_id", "TEXT NOT NULL DEFAULT ''"),
        ("run_id", "TEXT NOT NULL DEFAULT ''"),
        ("payload_digest", "TEXT NOT NULL DEFAULT ''"),
    ),
    "research_budget_receipts": (
        ("job_id", "TEXT NOT NULL DEFAULT ''"),
        ("attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("usage_id", "TEXT NOT NULL DEFAULT ''"),
        ("chain_digest", "TEXT NOT NULL DEFAULT ''"),
        ("binding_digest", "TEXT NOT NULL DEFAULT ''"),
        ("producer", "TEXT NOT NULL DEFAULT ''"),
    ),
    "research_budget_reservations": (
        ("attempt", "INTEGER NOT NULL DEFAULT 0"),
        ("receipt_id", "TEXT NOT NULL DEFAULT ''"),
        ("chain_digest", "TEXT NOT NULL DEFAULT ''"),
        ("release_id", "TEXT NOT NULL DEFAULT ''"),
        ("plan_identity", "TEXT NOT NULL DEFAULT ''"),
    ),
}


def get_admission_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied admission-DB migration id (``0`` when none)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='admission_schema_migrations'"
    ).fetchone()
    if row is None:
        return 0
    highest = conn.execute("SELECT MAX(id) AS v FROM admission_schema_migrations").fetchone()
    return 0 if highest is None or highest["v"] is None else int(highest["v"])


def migrate_admission_schema(conn: sqlite3.Connection) -> int:
    """Apply the additive admission-DB migrations (idempotent, ``026`` newest).

    The admission ledger is a separate SQLite file from the jobs DB, so it keeps
    its own id-versioned ``admission_schema_migrations`` table.  The numbering
    continues the repository-wide series (``025`` belongs to the identity DB's
    ``api_keys.usage_capabilities``); ids are never renumbered or reused.
    """
    conn.executescript(_ADMISSION_META_SQL)
    for table, columns in _ADDITIVE_COLUMNS.items():
        existing = _table_columns(conn, table)
        if not existing:
            continue
        for name, definition in columns:
            if name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            except sqlite3.OperationalError as exc:
                # A concurrent initialiser may have added the same additive
                # column between our PRAGMA read and this ALTER; that is the
                # only accepted conflict (anything else fails closed).
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "INSERT OR IGNORE INTO admission_schema_migrations (id, applied_at) VALUES (?, ?)",
        (ADMISSION_SCHEMA_VERSION, utc_now_iso()),
    )
    return ADMISSION_SCHEMA_VERSION


def _row_to_binding(row: sqlite3.Row) -> ReceiptHistoryBinding:
    """Rebuild a persisted immutable attempt identity."""
    try:
        step_identities = tuple(json.loads(row["step_identities_json"]) or [])
    except (json.JSONDecodeError, TypeError):
        step_identities = ()
    return ReceiptHistoryBinding(
        job_id=str(row["job_id"]),
        attempt=int(row["attempt"]),
        submission_id=str(row["submission_id"] or ""),
        release_id=str(row["release_id"] or ""),
        release_digest=str(row["release_digest"] or ""),
        owner=str(row["owner"] or ""),
        fencing_token=str(row["fencing_token"] or ""),
        plan_identity=str(row["plan_identity"] or ""),
        step_identities=step_identities,
        reservation_id=str(row["reservation_id"] or ""),
        chain_digest=str(row["chain_digest"] or ""),
    )


def _receipt_for_binding(binding: ReceiptHistoryBinding) -> ExecutionReceipt:
    """Project a binding onto the receipt shape the history verifier consumes."""
    return ExecutionReceipt(
        receipt_version=1,
        job_id=binding.job_id,
        attempt=int(binding.attempt),
        submission_id=binding.submission_id,
        state="completed",
        release_id=binding.release_id,
        release_digest=binding.release_digest,
        owner=binding.owner,
        fencing_token=binding.fencing_token,
        plan_identity=binding.plan_identity,
        step_identities=tuple(binding.step_identities),
    )


def _usage_payload_digest(
    usage_id: str,
    *,
    job_id: str,
    attempt: int,
    reservation_id: str,
    cores: int,
    seconds: int,
    counters: UsageUnitCounts,
    producer: str,
    chain_digest: str,
    budget_id: str,
    budget_reservation_id: str,
    run_id: str,
) -> str:
    """Stable digest of one receipt's payload (same id + different payload = conflict)."""
    payload = {
        "usage_id": str(usage_id),
        "job_id": str(job_id),
        "attempt": int(attempt),
        "reservation_id": str(reservation_id),
        "cores": int(cores),
        "seconds": int(seconds),
        "counters": counters.to_dict(),
        "producer": str(producer),
        "chain_digest": str(chain_digest),
        "budget_id": str(budget_id),
        "budget_reservation_id": str(budget_reservation_id),
        "run_id": str(run_id),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_reservation(row: sqlite3.Row) -> Reservation:
    return Reservation(
        reservation_id=row["reservation_id"],
        principal_id=row["principal_id"],
        project_id=row["project_id"],
        job_id=row["job_id"],
        state=row["state"],
        cores=int(row["cores"]),
        memory_mb=int(row["memory_mb"]),
        storage_bytes=int(row["storage_bytes"]),
        duration_s=int(row["duration_s"]),
        key_id=row["key_id"],
        queue=row["queue"],
        node=row["node"],
        policy_version=row["policy_version"],
        actual_cores=row["actual_cores"],
        actual_memory_mb=row["actual_memory_mb"],
        actual_storage_bytes=row["actual_storage_bytes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _contribution(request: AdmissionRequest, queued: bool, dimension: str) -> int:
    """How much of *dimension* a new *request* would add to occupied load.

    A queued request adds storage (and, separately, a concurrency slot) but no
    running CPU/memory.
    """
    if dimension == "storage_bytes":
        return int(request.storage_bytes)
    if queued:
        return 0
    return int(getattr(request, dimension))


def _new_contribution(limits: ReserveLimits, request: AdmissionRequest, dimension: str) -> int:
    return _contribution(request, limits.queued, dimension)


def _aggregate(
    reservations: Sequence[Reservation],
) -> dict[str, int]:
    """Sum the counted load of *reservations* per additive dimension."""
    totals = {dim: 0 for dim in _ADDITIVE_DIMENSIONS}
    for reservation in reservations:
        totals["cores"] += reservation.counted_cores()
        totals["memory_mb"] += reservation.counted_memory_mb()
        totals["storage_bytes"] += reservation.counted_storage_bytes()
    return totals


def _evaluate_reserve(
    reservations: Sequence[Reservation],
    request: AdmissionRequest,
    limits: ReserveLimits,
) -> ReserveOutcome:
    """Check a pending reservation against every scope + capacity (pure).

    Returns an allowed/refused :class:`ReserveOutcome`; the caller runs this
    *inside* the atomic transaction, right before the insert.
    """
    if not limits.enforce:
        return ReserveOutcome(True, AdmissionReason.USAGE_ONLY)

    exceeded: list[Mapping[str, Any]] = []

    # 1. per-job: a single job may not exceed its own effective ceiling.
    for dimension in _PER_JOB_DIMENSIONS:
        value = int(getattr(request, dimension))
        bound = getattr(limits.request_bounds, dimension)
        if value > 0 and bound is not None and value > bound:
            exceeded.append(
                {"scope": "request", "dimension": dimension, "requested": value, "limit": bound}
            )

    # 2. cumulative per scope (user / project / key) — never summed across keys.
    scopes: list[tuple[str, ResourceBounds, Callable[[Reservation], bool]]] = [
        ("user", limits.user_bounds, lambda r: r.principal_id == request.principal_id),
        ("project", limits.project_bounds, lambda r: r.project_id == request.project_id),
    ]
    if request.key_id is not None:
        scopes.append(("key", limits.key_bounds, lambda r: r.key_id == request.key_id))
    for scope_name, bounds, predicate in scopes:
        members = [r for r in reservations if predicate(r)]
        totals = _aggregate(members)
        for dimension in _ADDITIVE_DIMENSIONS:
            bound = getattr(bounds, dimension)
            if bound is None:
                continue
            requested = totals[dimension] + _new_contribution(limits, request, dimension)
            if requested > bound:
                exceeded.append(
                    {
                        "scope": scope_name,
                        "dimension": dimension,
                        "requested": requested,
                        "limit": bound,
                    }
                )
        if bounds.concurrency is not None and len(members) + 1 > bounds.concurrency:
            exceeded.append(
                {
                    "scope": scope_name,
                    "dimension": "concurrency",
                    "requested": len(members) + 1,
                    "limit": bounds.concurrency,
                }
            )

    # 3. actual capacity — shared across every principal/project/key.
    if limits.capacity.available:
        totals = _aggregate(reservations)
        for dimension in _ADDITIVE_DIMENSIONS:
            requested = totals[dimension] + _new_contribution(limits, request, dimension)
            limit = int(getattr(limits.capacity, dimension))
            if requested > limit:
                exceeded.append(
                    {
                        "scope": "capacity",
                        "dimension": dimension,
                        "requested": requested,
                        "limit": limit,
                    }
                )
        if len(reservations) + 1 > int(limits.capacity.concurrency):
            exceeded.append(
                {
                    "scope": "capacity",
                    "dimension": "concurrency",
                    "requested": len(reservations) + 1,
                    "limit": int(limits.capacity.concurrency),
                }
            )

    if not exceeded:
        return ReserveOutcome(True, AdmissionReason.ADMITTED)

    first = exceeded[0]
    scope = str(first["scope"])
    dimension = str(first["dimension"])
    reason = _CAPACITY_REASONS[dimension] if scope == "capacity" else _SCOPE_REASONS[dimension]
    detail = "; ".join(
        f"{item['scope']} {item['dimension']} {item['requested']}>{item['limit']}"
        for item in exceeded
    )
    return ReserveOutcome(False, reason, detail, None, tuple(exceeded))


class ReservationLedger:
    """SQLite-backed reservation ledger with atomic check-and-insert.

    Args:
        db_path: SQLite database path (the table is created idempotently).
        clock: Optional ``() -> str`` timestamp override (deterministic tests).
        reservation_id_factory: Optional ``() -> str`` id factory.

    Every size-changing write runs inside ``BEGIN IMMEDIATE`` so the read of the
    current occupancy and the reservation insert happen in one transaction: a
    concurrent submission either sees the committed reservation or waits — it can
    never reserve against a stale, pre-commit view (no read-then-reserve split).
    """

    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], str] | None = None,
        reservation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        from pathlib import Path

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or utc_now_iso
        self._new_id = reservation_id_factory or (lambda: "res_" + uuid.uuid4().hex)
        self._lock = threading.Lock()
        self._init_schema()

    # -- schema / connection -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                self._enable_wal(conn)
                conn.executescript(_CREATE_TABLE_SQL)
                migrate_admission_schema(conn)
            finally:
                conn.close()

    @staticmethod
    def _enable_wal(conn: sqlite3.Connection) -> None:
        # ``PRAGMA journal_mode=WAL`` returns SQLITE_BUSY without consulting
        # busy_timeout while another connection flips the same file, so two
        # concurrent first opens would otherwise fail the schema init.  Wait
        # explicitly inside the same 5 s budget instead.
        deadline = time.monotonic() + 5.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)

    # -- reads ---------------------------------------------------------------

    def get(self, reservation_id: str) -> Reservation | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else _row_to_reservation(row)

    def list_active(
        self,
        *,
        job_id: str | None = None,
        principal_id: str | None = None,
        project_id: str | None = None,
    ) -> list[Reservation]:
        """Return every non-released reservation (optionally filtered)."""
        clauses = ["state != ?"]
        params: list[Any] = [ReservationState.RELEASED]
        if job_id is not None:
            clauses.append("job_id=?")
            params.append(job_id)
        if principal_id is not None:
            clauses.append("principal_id=?")
            params.append(principal_id)
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        query = (
            f"SELECT * FROM admission_reservations WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at, reservation_id"
        )
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(query, params).fetchall()
            finally:
                conn.close()
        return [_row_to_reservation(row) for row in rows]

    def count(self, *, active_only: bool = False) -> int:
        where = " WHERE state != ?" if active_only else ""
        params: tuple[Any, ...] = (ReservationState.RELEASED,) if active_only else ()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM admission_reservations{where}", params
                ).fetchone()
            finally:
                conn.close()
        return int(row["n"]) if row is not None else 0

    def occupancy(self) -> dict[str, Any]:
        """Live occupancy across every active reservation."""
        active = self.list_active()
        totals = _aggregate(active)
        by_state: dict[str, int] = {}
        for reservation in active:
            by_state[reservation.state] = by_state.get(reservation.state, 0) + 1
        return {
            "cores": totals["cores"],
            "memory_mb": totals["memory_mb"],
            "storage_bytes": totals["storage_bytes"],
            "concurrency": len(active),
            "reservations": len(active),
            "by_state": by_state,
        }

    # -- atomic reserve ------------------------------------------------------

    def reserve(
        self,
        request: AdmissionRequest,
        limits: ReserveLimits,
        *,
        reservation_id: str | None = None,
        idempotency: ReservationIdempotency | None = None,
    ) -> ReserveOutcome:
        """Atomically check every ceiling and insert the reservation.

        The occupancy read, the ceiling comparison, the ``INSERT`` and the
        claim-keyed link row all happen inside one ``BEGIN IMMEDIATE``
        transaction.  A refusal rolls back without inserting anything (no
        partial reservation).  When ``idempotency`` is supplied, a claim that
        already holds a live reservation reuses it instead of reserving twice.
        """
        now = self._clock()
        state = ReservationState.QUEUED if limits.queued else ReservationState.RESERVED
        reservation = Reservation(
            reservation_id=reservation_id or self._new_id(),
            principal_id=request.principal_id,
            project_id=request.project_id,
            job_id=request.job_id,
            state=state,
            cores=int(request.cores),
            memory_mb=int(request.memory_mb),
            storage_bytes=int(request.storage_bytes),
            duration_s=int(request.duration_s),
            key_id=request.key_id,
            queue=request.queue,
            node=request.node,
            policy_version=str(limits.policy_version),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if idempotency is not None:
                    linked = conn.execute(
                        "SELECT reservation_id FROM admission_reservation_claims WHERE claim_id=?",
                        (idempotency.claim_id,),
                    ).fetchone()
                    if linked is not None:
                        existing = conn.execute(
                            "SELECT * FROM admission_reservations WHERE reservation_id=?",
                            (linked["reservation_id"],),
                        ).fetchone()
                        if existing is not None and existing["state"] != ReservationState.RELEASED:
                            conn.execute("ROLLBACK")
                            return ReserveOutcome(
                                True,
                                AdmissionReason.ADMITTED,
                                "already_reserved",
                                _row_to_reservation(existing),
                                (),
                            )
                        conn.execute(
                            "DELETE FROM admission_reservation_claims WHERE claim_id=?",
                            (idempotency.claim_id,),
                        )
                rows = conn.execute(
                    "SELECT * FROM admission_reservations WHERE state != ?",
                    (ReservationState.RELEASED,),
                ).fetchall()
                outcome = _evaluate_reserve([_row_to_reservation(r) for r in rows], request, limits)
                if not outcome.allowed:
                    conn.execute("ROLLBACK")
                    logger.info(
                        "admission refused job=%s reason=%s detail=%s",
                        request.job_id,
                        outcome.reason_code,
                        outcome.detail,
                    )
                    return outcome
                conn.execute(
                    """INSERT INTO admission_reservations (
                           reservation_id, principal_id, project_id, key_id, job_id,
                           state, cores, memory_mb, storage_bytes, duration_s, queue,
                           node, policy_version, actual_cores, actual_memory_mb,
                           actual_storage_bytes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)""",
                    (
                        reservation.reservation_id,
                        reservation.principal_id,
                        reservation.project_id,
                        reservation.key_id,
                        reservation.job_id,
                        reservation.state,
                        reservation.cores,
                        reservation.memory_mb,
                        reservation.storage_bytes,
                        reservation.duration_s,
                        reservation.queue,
                        reservation.node,
                        reservation.policy_version,
                        reservation.created_at,
                        reservation.updated_at,
                    ),
                )
                if idempotency is not None:
                    conn.execute(
                        """INSERT INTO admission_reservation_claims (
                               claim_id, reservation_id, scope_id, action,
                               idempotency_key, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            idempotency.claim_id,
                            reservation.reservation_id,
                            idempotency.scope_id,
                            idempotency.action,
                            idempotency.idempotency_key,
                            now,
                        ),
                    )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return ReserveOutcome(True, AdmissionReason.ADMITTED, "reserved", reservation, ())

    def release_for_claim(self, claim_id: str) -> bool:
        """Release the reservation recorded for *claim_id* (idempotent)."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                linked = conn.execute(
                    "SELECT reservation_id FROM admission_reservation_claims WHERE claim_id=?",
                    (str(claim_id),),
                ).fetchone()
                if linked is None:
                    conn.execute("ROLLBACK")
                    return False
                cursor = conn.execute(
                    "UPDATE admission_reservations SET state=?, updated_at=? "
                    "WHERE reservation_id=? AND state != ?",
                    (
                        ReservationState.RELEASED,
                        now,
                        linked["reservation_id"],
                        ReservationState.RELEASED,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return cursor.rowcount == 1

    def reservation_id_for_claim(self, claim_id: str) -> str | None:
        """Return the reservation linked to *claim_id* (diagnostic)."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT reservation_id FROM admission_reservation_claims WHERE claim_id=?",
                    (str(claim_id),),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else str(row["reservation_id"])

    def bind_reservation_job(self, reservation_id: str, job_id: str) -> bool:
        """Bind a claim-keyed reservation to the task it admitted (idempotent).

        An idempotent submit reserves **before** the task id exists, so the
        reservation row carries the claim id.  The Controller records the real
        task id here at dispatch; trusted usage settlement then verifies that the
        reservation and the receipt's job are the same execution.
        """
        reservation = str(reservation_id or "").strip()
        job = str(job_id or "").strip()
        if not reservation or not job:
            raise AdmissionValidationError("bind_reservation_job needs reservation_id and job_id")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT job_id FROM admission_reservations WHERE reservation_id=?",
                    (reservation,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT OR IGNORE INTO admission_reservation_jobs "
                    "(reservation_id, job_id, bound_at) VALUES (?, ?, ?)",
                    (reservation, job, now),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return True

    def declare_attempt(self, job_id: str, attempt: int, *, reservation_id: str = "") -> bool:
        """Record that the Controller dispatched ``(job, attempt)`` (idempotent).

        This is the server-side execution fact a trusted external usage receipt
        is checked against: an attempt number that was never dispatched cannot be
        the first receipt to invent usage.  A late receipt for an *earlier*
        dispatched attempt is unaffected (the declaration is per attempt).
        """
        job = str(job_id or "").strip()
        attempt_value = int(attempt)
        if not job or attempt_value < 1:
            raise AdmissionValidationError("declare_attempt needs job_id and attempt >= 1")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT reservation_id FROM admission_attempt_declarations "
                    "WHERE job_id=? AND attempt=?",
                    (job, attempt_value),
                ).fetchone()
                if row is not None:
                    stored = str(row["reservation_id"] or "")
                    if stored and reservation_id and stored != str(reservation_id):
                        conn.execute("ROLLBACK")
                        return False
                    conn.execute("COMMIT")
                    return False
                conn.execute(
                    "INSERT INTO admission_attempt_declarations "
                    "(job_id, attempt, reservation_id, declared_at) VALUES (?, ?, ?, ?)",
                    (job, attempt_value, str(reservation_id), now),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return True

    def _attempt_declared(self, conn: sqlite3.Connection, job_id: str, attempt: int) -> bool:
        row = conn.execute(
            "SELECT 1 FROM admission_attempt_declarations WHERE job_id=? AND attempt=?",
            (str(job_id), int(attempt)),
        ).fetchone()
        return row is not None

    def _reservation_job_ids(self, conn: sqlite3.Connection, reservation_id: str) -> set[str]:
        rows = conn.execute(
            "SELECT job_id FROM admission_reservation_jobs WHERE reservation_id=?",
            (str(reservation_id),),
        ).fetchall()
        return {str(row["job_id"]) for row in rows}

    # -- trusted usage receipts (M0-07/G26) ---------------------------------
    #
    # The admission DB is the single settlement ledger.  Two receipt contexts
    # are deliberately distinct:
    #
    # * the **current-attempt** guard (``runtime/receipts.verify_receipt``)
    #   gates a job state advance;
    # * the **historical** guard (``verify_historical_receipt``) re-checks a late
    #   receipt against the immutable identity bound for its own attempt.  It
    #   books usage only and never advances task state.

    def _bind_attempt_in(
        self,
        conn: sqlite3.Connection,
        binding: ReceiptHistoryBinding,
        *,
        now: str,
        producer: str = "",
        budget_id: str = "",
        budget_reservation_id: str = "",
    ) -> bool:
        """Insert one immutable ``(job, attempt)`` identity (idempotent, same txn)."""
        candidate = binding.with_chain_digest()
        row = conn.execute(
            "SELECT * FROM admission_attempt_bindings WHERE job_id=? AND attempt=?",
            (candidate.job_id, int(candidate.attempt)),
        ).fetchone()
        if row is not None:
            stored = _row_to_binding(row)
            if stored.chain_digest != candidate.chain_digest:
                raise UsageReceiptRejectedError(
                    UsageReason.ATTEMPT_CONFLICT,
                    f"job {candidate.job_id!r} attempt {candidate.attempt} is already bound to "
                    "a different execution identity",
                    params={
                        "field": "chain_digest",
                        "expected": stored.chain_digest,
                        "actual": candidate.chain_digest,
                    },
                )
            if (
                stored.reservation_id
                and candidate.reservation_id
                and stored.reservation_id != candidate.reservation_id
            ):
                raise UsageReceiptRejectedError(
                    UsageReason.RESERVATION_MISMATCH,
                    f"bound reservation {stored.reservation_id!r} != {candidate.reservation_id!r}",
                    params={
                        "field": "reservation_id",
                        "expected": stored.reservation_id,
                        "actual": candidate.reservation_id,
                    },
                )
            return False
        conn.execute(
            """INSERT INTO admission_attempt_bindings (
                   job_id, attempt, reservation_id, submission_id, release_id,
                   release_digest, owner, fencing_token, plan_identity,
                   step_identities_json, chain_digest, producer, task_ref,
                   budget_id, budget_reservation_id, bound_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.job_id,
                int(candidate.attempt),
                str(candidate.reservation_id),
                str(candidate.submission_id),
                str(candidate.release_id),
                str(candidate.release_digest),
                str(candidate.owner),
                str(candidate.fencing_token),
                str(candidate.plan_identity),
                json.dumps(list(candidate.step_identities)),
                candidate.chain_digest,
                str(producer),
                str(candidate.job_id),
                str(budget_id),
                str(budget_reservation_id),
                now,
            ),
        )
        return True

    def bind_attempt_identity(self, binding: ReceiptHistoryBinding) -> bool:
        """Persist the immutable execution identity of one ``(job, attempt)``.

        Called by the Controller seam when an attempt is frozen/dispatched (and
        implicitly by :meth:`record_usage_once`).  A re-bind with a different
        identity raises :class:`UsageReceiptRejectedError`; an identical re-bind is a
        no-op.  Nothing here depends on a work directory.
        """
        identifier = str(binding.job_id or "").strip()
        if not identifier or int(binding.attempt) < 1:
            raise AdmissionValidationError("attempt binding needs job_id and attempt >= 1")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                inserted = self._bind_attempt_in(conn, binding, now=now)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return inserted

    def attempt_binding(self, job_id: str, attempt: int) -> ReceiptHistoryBinding | None:
        """Return the persisted immutable identity for ``(job, attempt)``."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM admission_attempt_bindings WHERE job_id=? AND attempt=?",
                    (str(job_id), int(attempt)),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else _row_to_binding(row)

    def _settle_budget_in(
        self,
        conn: sqlite3.Connection,
        *,
        budget_row: sqlite3.Row,
        receipt_id: str,
        counters: UsageUnitCounts,
        run_id: str,
        trusted_source: str,
        job_id: str,
        attempt: int,
        usage_id: str,
        chain_digest: str,
        binding_digest: str,
        producer: str,
        now: str,
        release_id: str = "",
        plan_identity: str = "",
    ) -> bool:
        """Insert the budget receipt + settle the reservation inside *conn*.

        The receipt id is the idempotency key: a duplicate delivery is a no-op,
        so usage and its budget settlement move together in one transaction.
        Returns ``True`` when the receipt was applied.
        """
        budget_id = str(budget_row["budget_id"])
        reservation_id = str(budget_row["reservation_id"])
        cursor = conn.execute(
            """INSERT OR IGNORE INTO research_budget_receipts (
                   receipt_id, budget_id, reservation_id, run_id, usage_json,
                   trusted_source, applied, recorded_at, job_id, attempt, usage_id,
                   chain_digest, binding_digest, producer)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(receipt_id),
                budget_id,
                reservation_id,
                str(run_id),
                json.dumps(counters.to_dict(), sort_keys=True),
                str(trusted_source),
                now,
                str(job_id),
                int(attempt),
                str(usage_id),
                str(chain_digest),
                str(binding_digest),
                str(producer),
            ),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute(
            "UPDATE research_budget_reservations SET state=?, updated_at=?, attempt=?, "
            "receipt_id=?, chain_digest=?, release_id=?, plan_identity=? "
            "WHERE reservation_id=? AND state != ?",
            (
                BudgetState.SETTLED,
                now,
                int(attempt),
                str(receipt_id),
                str(chain_digest),
                str(release_id),
                str(plan_identity),
                reservation_id,
                BudgetState.RELEASED,
            ),
        )
        conn.execute(
            "INSERT INTO research_budget_events (subject, event, detail, created_at) "
            "VALUES (?, ?, ?, ?)",
            (reservation_id, "budget_settle", str(receipt_id), now),
        )
        return True

    def record_usage_once(
        self,
        usage_id: str,
        *,
        reservation_id: str = "",
        job_id: str,
        attempt: int = 1,
        cores: int = 0,
        seconds: int = 0,
        producer: str = "",
        receipt: ExecutionReceipt | None = None,
        binding: ReceiptHistoryBinding | None = None,
        counters: UsageUnitCounts | None = None,
        budget_id: str = "",
        budget_reservation_id: str = "",
        run_id: str = "",
    ) -> UsageIngestOutcome:
        """Book one trusted usage receipt exactly once — the settlement wire point.

        Verification (all inside one ``BEGIN IMMEDIATE`` transaction, so a
        refusal writes nothing):

        1. the **producer** must be a known producer; the trusted source is
           derived from it (a caller-supplied source string is never accepted);
        2. an **external** producer must present the receipt identity (job,
           attempt, deterministic submission, release/plan/step digests) and the
           job must already be dispatched — no execution fact, no settlement;
        3. the ``(job, attempt)`` identity is bound immutably on first sight and
           every later receipt is verified against that binding (wrong
           reservation / attempt / release are refused);
        4. the admission reservation must exist and belong to the job; the budget
           reservation must belong to the same job;
        5. the usage row and the research-budget receipt are inserted in the
           **same** transaction; the receipt id is the idempotency key and a
           re-delivery with a different payload is a typed conflict, never a
           silent no-op.

        A duplicate delivery returns ``duplicate=True`` and charges nothing.
        A late receipt for an older attempt is verified against *its* binding,
        so an already-started attempt 2 cannot make attempt 1 usage disappear —
        and this method never advances task state.
        """
        identifier = str(usage_id or "").strip()
        if not identifier:
            raise AdmissionValidationError("usage_id must be non-empty")
        try:
            authority = resolve_authority(producer=producer)
        except UntrustedUsageProducerError as exc:
            raise UsageReceiptRejectedError(exc.code, exc.detail) from exc
        attempt_value = int(attempt)
        if attempt_value < 1:
            raise UsageReceiptRejectedError(UsageReason.RECEIPT_INVALID, "attempt must be >= 1")
        target_job = str(job_id or "").strip()
        if not target_job:
            raise UsageReceiptRejectedError(UsageReason.RECEIPT_INVALID, "job_id is required")
        counter_values = counters or UsageUnitCounts()
        core_seconds = max(0, int(cores)) * max(0, int(seconds))
        core_hours = (Decimal(core_seconds) / Decimal(3600)).quantize(Decimal("0.000001"))

        candidate = binding
        if candidate is None and receipt is not None:
            candidate = ReceiptHistoryBinding.from_receipt(receipt)
        if candidate is not None:
            if candidate.job_id and candidate.job_id != target_job:
                raise UsageReceiptRejectedError(
                    UsageReason.RESERVATION_JOB_MISMATCH,
                    f"receipt job {candidate.job_id!r} != {target_job!r}",
                )
            if int(candidate.attempt) != attempt_value:
                raise UsageReceiptRejectedError(
                    UsageReason.ATTEMPT_CONFLICT,
                    f"receipt attempt {candidate.attempt} != declared {attempt_value}",
                )
        if not authority.internal and (receipt is None or candidate is None):
            raise UsageReceiptRejectedError(
                UsageReason.RECEIPT_INVALID,
                f"producer {authority.producer!r} must present the attempt receipt identity",
            )

        now = self._clock()
        budget_applied = False
        consumed: dict[str, int] = {}
        remaining: dict[str, int] = {}
        applied = False
        duplicate = False
        resolved_reservation = ""
        resolved_budget_id = ""
        chain_digest = ""
        binding_digest = ""
        identity_release = ""
        identity_plan = ""

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                budget_row: sqlite3.Row | None = None
                if budget_reservation_id:
                    budget_row = conn.execute(
                        "SELECT * FROM research_budget_reservations "
                        "WHERE reservation_id=? AND budget_id=?",
                        (str(budget_reservation_id), str(budget_id)),
                    ).fetchone()
                    if budget_row is None:
                        raise UsageReceiptRejectedError(
                            UsageReason.BUDGET_MISMATCH,
                            f"budget reservation {budget_reservation_id!r} not found on "
                            f"budget {budget_id!r}",
                        )
                    if str(budget_row["state"]) == BudgetState.RELEASED:
                        raise UsageReceiptRejectedError(
                            UsageReason.BUDGET_RELEASED,
                            f"budget reservation {budget_reservation_id!r} was released",
                        )
                    # The reservation's ``task_ref`` may be the submit claim id
                    # (idempotent submit reserves before the task id exists); the
                    # durable link is ``admission_reservation_id`` → the
                    # reservation's bound job ids, checked below.
                    resolved_budget_id = str(budget_row["budget_id"])
                    if not reservation_id:
                        reservation_id = str(budget_row["admission_reservation_id"] or "")
                resolved_reservation = str(reservation_id or "")
                if not resolved_reservation:
                    raise UsageReceiptRejectedError(
                        UsageReason.RESERVATION_NOT_FOUND,
                        "a usage receipt must name the admission reservation it settles",
                    )
                reservation_row = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (resolved_reservation,),
                ).fetchone()
                if reservation_row is None:
                    raise UsageReceiptRejectedError(
                        UsageReason.RESERVATION_NOT_FOUND,
                        f"admission reservation {resolved_reservation!r} not found",
                    )
                known_jobs = {str(reservation_row["job_id"])}
                known_jobs |= self._reservation_job_ids(conn, resolved_reservation)
                if target_job not in known_jobs:
                    if authority.internal:
                        # The Controller owns both facts; record the task id it
                        # admitted so later historical verification is durable.
                        conn.execute(
                            "INSERT OR IGNORE INTO admission_reservation_jobs "
                            "(reservation_id, job_id, bound_at) VALUES (?, ?, ?)",
                            (resolved_reservation, target_job, now),
                        )
                    else:
                        raise UsageReceiptRejectedError(
                            UsageReason.RESERVATION_JOB_MISMATCH,
                            f"reservation {resolved_reservation!r} does not belong to "
                            f"{target_job!r}",
                        )

                stored_row = conn.execute(
                    "SELECT * FROM admission_attempt_bindings WHERE job_id=? AND attempt=?",
                    (target_job, attempt_value),
                ).fetchone()
                if stored_row is not None:
                    stored = _row_to_binding(stored_row)
                    if stored.reservation_id and stored.reservation_id != resolved_reservation:
                        raise UsageReceiptRejectedError(
                            UsageReason.RESERVATION_MISMATCH,
                            f"attempt {attempt_value} is bound to reservation "
                            f"{stored.reservation_id!r}, not {resolved_reservation!r}",
                            params={
                                "field": "reservation_id",
                                "expected": stored.reservation_id,
                                "actual": resolved_reservation,
                            },
                        )
                    if candidate is not None:
                        if authority.internal:
                            # The Controller's own terminal observation carries no
                            # frozen submission derivation; its identity is the
                            # persisted one it is re-deriving.
                            if candidate.with_chain_digest().chain_digest != stored.chain_digest:
                                raise UsageReceiptRejectedError(
                                    UsageReason.ATTEMPT_CONFLICT,
                                    "internal receipt identity disagrees with the bound "
                                    "attempt identity",
                                    params={"field": "chain_digest"},
                                )
                        else:
                            verdict: ReceiptVerdict = verify_historical_receipt(
                                receipt if receipt is not None else _receipt_for_binding(candidate),
                                stored,
                                reservation_id=resolved_reservation,
                            )
                            if not verdict.accepted:
                                raise UsageReceiptRejectedError(
                                    UsageReason.ATTEMPT_CONFLICT,
                                    f"{verdict.code}: {verdict.reason}",
                                    params={"field": verdict.code},
                                )
                    chain_digest = stored.chain_digest
                    binding_digest = stored.chain_digest
                    identity_release = stored.release_id
                    identity_plan = stored.plan_identity
                else:
                    if not authority.internal and not self._attempt_declared(
                        conn, target_job, attempt_value
                    ):
                        raise UsageReceiptRejectedError(
                            UsageReason.NOT_DISPATCHED,
                            f"attempt {attempt_value} was never dispatched for this task; "
                            "trusted usage cannot invent an execution",
                        )
                    if (
                        not authority.internal
                        and budget_row is not None
                        and str(budget_row["state"])
                        not in (BudgetState.DISPATCHED, BudgetState.SETTLED)
                    ):
                        raise UsageReceiptRejectedError(
                            UsageReason.NOT_DISPATCHED,
                            "the budget reservation for this task was never dispatched; "
                            "trusted usage cannot settle it",
                        )
                    assert candidate is not None  # external path validated above
                    seeded = ReceiptHistoryBinding(
                        job_id=candidate.job_id,
                        attempt=candidate.attempt,
                        submission_id=candidate.submission_id,
                        release_id=candidate.release_id,
                        release_digest=candidate.release_digest,
                        owner=candidate.owner,
                        fencing_token=candidate.fencing_token,
                        plan_identity=candidate.plan_identity,
                        step_identities=tuple(candidate.step_identities),
                        reservation_id=resolved_reservation,
                    ).with_chain_digest()
                    if not authority.internal:
                        verdict = verify_historical_receipt(
                            receipt if receipt is not None else _receipt_for_binding(seeded),
                            seeded,
                            reservation_id=resolved_reservation,
                        )
                        if not verdict.accepted:
                            raise UsageReceiptRejectedError(
                                UsageReason.RECEIPT_INVALID,
                                f"{verdict.code}: {verdict.reason}",
                                params={"field": verdict.code},
                            )
                    self._bind_attempt_in(
                        conn,
                        seeded,
                        now=now,
                        producer=authority.producer,
                        budget_id=resolved_budget_id,
                        budget_reservation_id=str(budget_reservation_id),
                    )
                    chain_digest = seeded.chain_digest
                    binding_digest = seeded.chain_digest
                    identity_release = seeded.release_id
                    identity_plan = seeded.plan_identity

                payload_digest = _usage_payload_digest(
                    identifier,
                    job_id=target_job,
                    attempt=attempt_value,
                    reservation_id=resolved_reservation,
                    cores=max(0, int(cores)),
                    seconds=max(0, int(seconds)),
                    counters=counter_values,
                    producer=authority.producer,
                    chain_digest=chain_digest,
                    budget_id=resolved_budget_id,
                    budget_reservation_id=str(budget_reservation_id),
                    run_id=str(run_id),
                )
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO admission_usage_receipts (
                           usage_id, reservation_id, job_id, attempt, cores,
                           seconds, core_hours, recorded_at, producer, trusted_source,
                           submission_id, release_id, plan_identity,
                           step_identities_json, chain_digest, binding_digest,
                           budget_id, budget_reservation_id, run_id, payload_digest)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        identifier,
                        resolved_reservation,
                        target_job,
                        attempt_value,
                        max(0, int(cores)),
                        max(0, int(seconds)),
                        str(core_hours),
                        now,
                        authority.producer,
                        authority.trusted_source,
                        "" if candidate is None else str(candidate.submission_id),
                        "" if candidate is None else str(candidate.release_id),
                        "" if candidate is None else str(candidate.plan_identity),
                        json.dumps([] if candidate is None else list(candidate.step_identities)),
                        chain_digest,
                        binding_digest,
                        resolved_budget_id,
                        str(budget_reservation_id),
                        str(run_id),
                        payload_digest,
                    ),
                )
                if cursor.rowcount == 1:
                    applied = True
                    if budget_row is not None:
                        budget_applied = self._settle_budget_in(
                            conn,
                            budget_row=budget_row,
                            receipt_id=identifier,
                            counters=counter_values,
                            run_id=str(run_id),
                            trusted_source=authority.trusted_source,
                            job_id=target_job,
                            attempt=attempt_value,
                            usage_id=identifier,
                            chain_digest=chain_digest,
                            binding_digest=binding_digest,
                            producer=authority.producer,
                            now=now,
                            release_id=identity_release,
                            plan_identity=identity_plan,
                        )
                else:
                    stored_usage = conn.execute(
                        "SELECT payload_digest FROM admission_usage_receipts WHERE usage_id=?",
                        (identifier,),
                    ).fetchone()
                    if (
                        stored_usage is None
                        or str(stored_usage["payload_digest"] or "") != payload_digest
                    ):
                        raise UsageReceiptRejectedError(
                            UsageReason.RECEIPT_CONFLICT,
                            f"receipt {identifier!r} was already recorded with a different payload",
                        )
                    duplicate = True
                if resolved_budget_id:
                    consumed = self._budget_consumed_in(conn, resolved_budget_id)
                    policy = self._budget_policy_in(conn, resolved_budget_id)
                    remaining = {
                        unit: max(0, cap - consumed.get(unit, 0))
                        for unit, cap in (policy.limits if policy is not None else {}).items()
                    }
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

        return UsageIngestOutcome(
            applied=applied,
            duplicate=duplicate,
            reason_code=UsageReason.RECORDED if applied else UsageReason.DUPLICATE,
            usage_id=identifier,
            job_id=target_job,
            attempt=attempt_value,
            reservation_id=resolved_reservation,
            producer=authority.producer,
            trusted_source=authority.trusted_source,
            chain_digest=chain_digest,
            binding_digest=binding_digest,
            counters=counter_values,
            budget_id=resolved_budget_id,
            budget_reservation_id=str(budget_reservation_id),
            budget_applied=budget_applied,
            consumed=consumed,
            remaining=remaining,
        )

    def usage_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """Every usage receipt booked for *job_id* (survives task purge)."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM admission_usage_receipts WHERE job_id=? "
                    "ORDER BY attempt, recorded_at, usage_id",
                    (str(job_id),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def verify_usage_history(self, job_id: str) -> dict[str, Any]:
        """Re-verify booked usage from persisted identities only (no work_dir).

        Post-purge / post-restart evidence: every usage receipt is matched to
        its immutable ``(job, attempt)`` binding, the binding's chain digest is
        recomputed from its own fields, and receipts from several attempts are
        reported separately.  This never reads a task directory and never
        advances a task state.
        """
        receipts = self.usage_by_job(job_id)
        with self._lock:
            conn = self._connect()
            try:
                binding_rows = conn.execute(
                    "SELECT * FROM admission_attempt_bindings WHERE job_id=? ORDER BY attempt",
                    (str(job_id),),
                ).fetchall()
            finally:
                conn.close()
        bindings = {int(row["attempt"]): _row_to_binding(row) for row in binding_rows}
        attempts: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = []
        for attempt, binding in sorted(bindings.items()):
            recomputed = binding.computed_digest()
            if binding.chain_digest != recomputed:
                problems.append(
                    {
                        "attempt": attempt,
                        "code": "binding_digest_mismatch",
                        "expected": binding.chain_digest,
                        "actual": recomputed,
                    }
                )
            attempts.append(
                {
                    **binding.to_dict(),
                    "recomputed_digest": recomputed,
                    "receipts": [
                        row["usage_id"] for row in receipts if int(row["attempt"]) == attempt
                    ],
                }
            )
        for row in receipts:
            attempt = int(row["attempt"])
            binding = bindings.get(attempt)
            if binding is None:
                problems.append(
                    {"usage_id": row["usage_id"], "attempt": attempt, "code": "binding_missing"}
                )
                continue
            if str(row.get("chain_digest") or "") != binding.chain_digest:
                problems.append(
                    {
                        "usage_id": row["usage_id"],
                        "attempt": attempt,
                        "code": "receipt_chain_mismatch",
                    }
                )
            if str(row.get("reservation_id") or "") != binding.reservation_id:
                problems.append(
                    {
                        "usage_id": row["usage_id"],
                        "attempt": attempt,
                        "code": "receipt_reservation_mismatch",
                    }
                )
        return {
            "schema": "acp2.usage-history.v1",
            "job_id": str(job_id),
            "verified": not problems,
            "attempts": attempts,
            "receipts": receipts,
            "problems": problems,
        }

    def usage_totals(self) -> dict[str, Any]:
        """Return the recorded usage projection (duplicates are not charged)."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT cores, seconds, core_hours FROM admission_usage_receipts"
                ).fetchall()
            finally:
                conn.close()
        core_seconds = sum(int(row["cores"]) * int(row["seconds"]) for row in rows)
        core_hours = sum((Decimal(row["core_hours"]) for row in rows), Decimal(0))
        return {
            "receipts": len(rows),
            "core_seconds": core_seconds,
            "core_hours": str(core_hours),
        }

    def usage_receipts(self) -> list[dict[str, Any]]:
        """Return every usage receipt (diagnostic / evidence dump)."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM admission_usage_receipts ORDER BY recorded_at, usage_id"
                ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    # -- atomic dispatch re-validation --------------------------------------

    def dispatch(
        self,
        reservation_id: str,
        *,
        capacity: Capacity,
        expected_policy_version: str,
        enforce: bool = True,
    ) -> ReserveOutcome:
        """Re-validate and promote a reservation to ``dispatched`` atomically.

        A ``queued``/``paused`` reservation currently holds no CPU; this method
        atomically checks that the freed CPU/memory now fits (or, for a
        ``reserved`` row, simply confirms) before flipping the state.  The
        policy version is re-checked against the recorded snapshot so a policy
        change between reserve and dispatch is refused rather than ignored.
        """
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return ReserveOutcome(False, AdmissionReason.RESERVATION_NOT_FOUND)
                reservation = _row_to_reservation(row)
                if reservation.state == ReservationState.RELEASED:
                    conn.execute("ROLLBACK")
                    return ReserveOutcome(False, AdmissionReason.RESERVATION_RELEASED)
                if reservation.policy_version != str(expected_policy_version):
                    conn.execute("ROLLBACK")
                    return ReserveOutcome(
                        False,
                        AdmissionReason.POLICY_VERSION_CHANGED,
                        f"policy {reservation.policy_version!r} -> {expected_policy_version!r}",
                    )

                if enforce and reservation.state in (
                    ReservationState.QUEUED,
                    ReservationState.PAUSED,
                ):
                    others = [
                        _row_to_reservation(r)
                        for r in conn.execute(
                            "SELECT * FROM admission_reservations "
                            "WHERE state != ? AND reservation_id != ?",
                            (ReservationState.RELEASED, reservation_id),
                        ).fetchall()
                    ]
                    totals = _aggregate(others)
                    exceeded: list[Mapping[str, Any]] = []
                    for dimension in ("cores", "memory_mb"):
                        limit = int(getattr(capacity, dimension))
                        requested = totals[dimension] + int(getattr(reservation, dimension))
                        if requested > limit:
                            exceeded.append(
                                {
                                    "scope": "capacity",
                                    "dimension": dimension,
                                    "requested": requested,
                                    "limit": limit,
                                }
                            )
                    if len(others) + 1 > int(capacity.concurrency):
                        exceeded.append(
                            {
                                "scope": "capacity",
                                "dimension": "concurrency",
                                "requested": len(others) + 1,
                                "limit": int(capacity.concurrency),
                            }
                        )
                    if exceeded:
                        first = exceeded[0]
                        conn.execute("ROLLBACK")
                        return ReserveOutcome(
                            False,
                            _CAPACITY_REASONS[str(first["dimension"])],
                            "; ".join(
                                f"capacity {i['dimension']} {i['requested']}>{i['limit']}"
                                for i in exceeded
                            ),
                            None,
                            tuple(exceeded),
                        )

                conn.execute(
                    "UPDATE admission_reservations SET state=?, updated_at=? "
                    "WHERE reservation_id=?",
                    (ReservationState.DISPATCHED, now, reservation_id),
                )
                updated = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return ReserveOutcome(
            True,
            AdmissionReason.ADMITTED,
            "dispatched",
            None if updated is None else _row_to_reservation(updated),
        )

    # -- state / usage writes ------------------------------------------------

    def set_state(
        self,
        reservation_id: str,
        *,
        state: str,
        actual_cores: int | None = None,
        actual_memory_mb: int | None = None,
        actual_storage_bytes: int | None = None,
    ) -> Reservation | None:
        """Mutate a reservation's state and optional measured usage."""
        if state not in ReservationState.ACTIVE:
            raise AdmissionValidationError(f"unknown reservation state {state!r}")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return None
                current = _row_to_reservation(row)
                if current.state == ReservationState.RELEASED:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute(
                    """UPDATE admission_reservations
                       SET state=?, actual_cores=?, actual_memory_mb=?,
                           actual_storage_bytes=?, updated_at=?
                       WHERE reservation_id=?""",
                    (
                        state,
                        current.actual_cores if actual_cores is None else int(actual_cores),
                        (
                            current.actual_memory_mb
                            if actual_memory_mb is None
                            else int(actual_memory_mb)
                        ),
                        (
                            current.actual_storage_bytes
                            if actual_storage_bytes is None
                            else int(actual_storage_bytes)
                        ),
                        now,
                        reservation_id,
                    ),
                )
                updated = conn.execute(
                    "SELECT * FROM admission_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return None if updated is None else _row_to_reservation(updated)

    def release(self, reservation_id: str) -> bool:
        """Mark a reservation released (idempotent); ``False`` if unknown."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE admission_reservations SET state=?, updated_at=? "
                    "WHERE reservation_id=? AND state != ?",
                    (ReservationState.RELEASED, now, reservation_id, ReservationState.RELEASED),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return cursor.rowcount == 1

    # -- research budget ledger (same SQLite database, same lock) -----------

    @staticmethod
    def _budget_policy_in(conn: sqlite3.Connection, budget_id: str) -> BudgetPolicy | None:
        row = conn.execute(
            "SELECT * FROM research_budget_policies WHERE budget_id=?", (str(budget_id),)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["policy_json"])
        return BudgetPolicy(
            budget_id=str(row["budget_id"]),
            limits=payload.get("limits") or {},
            approved_limits=payload.get("approved_limits") or {},
            timeouts=payload.get("timeouts") or {},
            approval_ref=str(row["approval_ref"] or ""),
            project_id=str(row["project_id"] or ""),
        )

    @staticmethod
    def _budget_consumed_in(conn: sqlite3.Connection, budget_id: str) -> dict[str, int]:
        consumed = {unit: 0 for unit in UNIT_CAPABILITIES}
        rows = conn.execute(
            "SELECT units_json FROM research_budget_reservations WHERE budget_id=? AND state != ?",
            (str(budget_id), BudgetState.RELEASED),
        ).fetchall()
        for row in rows:
            for unit, amount in (json.loads(row["units_json"]) or {}).items():
                consumed[unit] = consumed.get(unit, 0) + int(amount)
        receipts = conn.execute(
            "SELECT usage_json FROM research_budget_receipts WHERE budget_id=?",
            (str(budget_id),),
        ).fetchall()
        for row in receipts:
            for unit, amount in (json.loads(row["usage_json"]) or {}).items():
                if unit in RECEIPT_CONSUMED_UNITS:
                    consumed[unit] = consumed.get(unit, 0) + int(amount)
        return consumed

    def define_research_budget(self, policy: BudgetPolicy) -> BudgetPolicy:
        """Persist one budget policy; a limit change after reservation is refused."""
        payload = {
            "limits": dict(policy.limits),
            "approved_limits": dict(policy.approved_limits),
            "timeouts": dict(policy.timeouts),
        }
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = self._budget_policy_in(conn, policy.budget_id)
                if existing is not None and (
                    dict(existing.limits) != dict(policy.limits)
                    or dict(existing.approved_limits) != dict(policy.approved_limits)
                ):
                    decided = conn.execute(
                        "SELECT COUNT(*) AS n FROM research_budget_reservations "
                        "WHERE budget_id=? AND state != ?",
                        (policy.budget_id, BudgetState.RELEASED),
                    ).fetchone()
                    if int(decided["n"] if decided is not None else 0) > 0:
                        conn.execute("ROLLBACK")
                        raise BudgetConfigurationError(
                            "budget limits changed after reservations were made"
                        )
                conn.execute(
                    """INSERT INTO research_budget_policies (
                           budget_id, approval_ref, project_id, policy_json,
                           created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(budget_id) DO UPDATE SET
                           approval_ref=excluded.approval_ref,
                           project_id=excluded.project_id,
                           policy_json=excluded.policy_json,
                           updated_at=excluded.updated_at""",
                    (
                        policy.budget_id,
                        policy.approval_ref,
                        policy.project_id,
                        json.dumps(payload, sort_keys=True),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO research_budget_events (subject, event, detail, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (policy.budget_id, "budget_defined", "", now),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return policy

    def get_research_budget(self, budget_id: str) -> BudgetPolicy | None:
        with self._lock:
            conn = self._connect()
            try:
                return self._budget_policy_in(conn, budget_id)
            finally:
                conn.close()

    def reserve_research_budget(
        self,
        budget_id: str,
        units: Mapping[str, Any],
        *,
        reservation_id: str,
        task_ref: str = "",
        run_id: str = "",
        claim_id: str = "",
        admission_reservation_id: str = "",
        detail: str = "",
    ) -> BudgetDecision:
        """Atomically check the remaining budget and insert one reservation."""
        requested: dict[str, int] = {}
        for raw_unit, raw_amount in (units or {}).items():
            unit = normalize_usage_unit(raw_unit)
            amount = int(raw_amount)
            if amount < 0:
                raise BudgetConfigurationError(f"{unit} amount must be non-negative")
            if amount:
                requested[unit] = requested.get(unit, 0) + amount
        if not requested:
            return BudgetDecision(
                True, BudgetReason.ADMITTED, budget_id=budget_id, reservation_id=reservation_id
            )
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                policy = self._budget_policy_in(conn, budget_id)
                if policy is None:
                    conn.execute("ROLLBACK")
                    return BudgetDecision(
                        False,
                        BudgetReason.NOT_CONFIGURED,
                        budget_id=budget_id,
                        detail="no persisted budget exists for this research round",
                        requested=requested,
                    )
                if claim_id:
                    linked = conn.execute(
                        "SELECT * FROM research_budget_reservations "
                        "WHERE budget_id=? AND claim_id=?",
                        (budget_id, claim_id),
                    ).fetchone()
                    if linked is not None:
                        conn.execute("COMMIT")
                        return BudgetDecision(
                            True,
                            BudgetReason.ADMITTED,
                            budget_id=budget_id,
                            reservation_id=str(linked["reservation_id"]),
                            detail="already_reserved",
                            requested=requested,
                        )
                consumed = self._budget_consumed_in(conn, budget_id)
                for unit, limit in policy.limits.items():
                    if UNIT_CAPABILITIES.get(unit) != "enforced":
                        continue
                    if requested.get(unit, 0) <= 0 and consumed.get(unit, 0) < limit:
                        continue
                    if consumed.get(unit, 0) + requested.get(unit, 0) > limit:
                        conn.execute("ROLLBACK")
                        remaining = {
                            name: max(0, cap - consumed.get(name, 0))
                            for name, cap in policy.limits.items()
                        }
                        return BudgetDecision(
                            False,
                            BudgetReason.EXHAUSTED,
                            budget_id=budget_id,
                            detail=(
                                f"{unit} budget {limit} - consumed {consumed.get(unit, 0)} < "
                                f"requested {requested.get(unit, 0)}; new dispatch refused"
                            ),
                            exhausted_unit=unit,
                            requested=requested,
                            remaining=remaining,
                        )
                conn.execute(
                    """INSERT INTO research_budget_reservations (
                           reservation_id, budget_id, units_json, state, task_ref,
                           run_id, claim_id, admission_reservation_id, detail,
                           created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(reservation_id),
                        budget_id,
                        json.dumps(requested, sort_keys=True),
                        BudgetState.RESERVED,
                        str(task_ref),
                        str(run_id),
                        str(claim_id),
                        str(admission_reservation_id),
                        str(detail),
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO research_budget_events (subject, event, detail, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        str(reservation_id),
                        "budget_reserve",
                        json.dumps(requested, sort_keys=True),
                        now,
                    ),
                )
                remaining = {
                    unit: max(0, cap - consumed.get(unit, 0) - requested.get(unit, 0))
                    for unit, cap in policy.limits.items()
                }
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return BudgetDecision(
            True,
            BudgetReason.ADMITTED,
            budget_id=budget_id,
            reservation_id=str(reservation_id),
            requested=requested,
            remaining=remaining,
        )

    def settle_research_budget(
        self,
        budget_id: str,
        *,
        receipt_id: str,
        reservation_id: str,
        counters: UsageUnitCounts,
        run_id: str = "",
        trusted_source: str = UsageSource.CONTROLLER_TERMINAL,
        job_id: str = "",
        attempt: int = 0,
        usage_id: str = "",
        chain_digest: str = "",
        binding_digest: str = "",
        producer: str = "",
    ) -> BudgetSettlement:
        """Settle from the Controller's own trusted receipt (internal only).

        Lower-level budget API.  The settlement *wire point* for execution
        evidence is :meth:`record_usage_once`, which resolves the producer from a
        credential capability and verifies the attempt identity.  This method is
        deliberately restricted to the internal
        ``controller-terminal-receipt`` source: a caller-supplied source string
        (and a caller-declared external producer) is **not** an authorization
        fact and is refused rather than recorded.
        """
        identifier = str(receipt_id or "").strip()
        if not identifier:
            raise BudgetConfigurationError("receipt_id must be non-empty")
        source = str(trusted_source or "").strip()
        if producer and str(producer).strip().lower() != UsageProducer.INTERNAL:
            raise BudgetConfigurationError(
                f"producer {producer!r} cannot settle through this internal API; "
                "external receipts must go through record_usage_once (capability + "
                "attempt identity)"
            )
        if source not in UsageSource.INTERNAL:
            raise BudgetConfigurationError(
                f"trusted source {source!r} is not the internal Controller source "
                f"{sorted(UsageSource.INTERNAL)}; an external source string is not an "
                "authorization fact"
            )
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM research_budget_reservations WHERE reservation_id=? "
                    "AND budget_id=?",
                    (str(reservation_id), str(budget_id)),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return BudgetSettlement(
                        False,
                        False,
                        BudgetReason.RESERVATION_NOT_FOUND,
                        budget_id,
                        str(reservation_id),
                        identifier,
                        counters,
                    )
                if str(row["state"]) == BudgetState.RELEASED:
                    conn.execute("ROLLBACK")
                    return BudgetSettlement(
                        False,
                        False,
                        BudgetReason.RESERVATION_RELEASED,
                        budget_id,
                        str(reservation_id),
                        identifier,
                        counters,
                    )
                applied = self._settle_budget_in(
                    conn,
                    budget_row=row,
                    receipt_id=identifier,
                    counters=counters,
                    run_id=str(run_id),
                    trusted_source=source,
                    job_id=str(job_id),
                    attempt=int(attempt),
                    usage_id=str(usage_id),
                    chain_digest=str(chain_digest),
                    binding_digest=str(binding_digest),
                    producer=str(producer),
                    now=now,
                )
                consumed = self._budget_consumed_in(conn, budget_id)
                policy = self._budget_policy_in(conn, budget_id)
                remaining = {
                    unit: max(0, cap - consumed.get(unit, 0))
                    for unit, cap in (policy.limits if policy is not None else {}).items()
                }
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return BudgetSettlement(
            applied,
            not applied,
            BudgetReason.SETTLED if applied else BudgetReason.DUPLICATE,
            budget_id,
            str(reservation_id),
            identifier,
            counters,
            consumed,
            remaining,
        )

    def mark_research_budget_dispatched(self, admission_reservation_id: str) -> bool:
        """Promote a reservation to dispatched (revalidation) before any QC work."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE research_budget_reservations SET state=?, updated_at=? "
                    "WHERE admission_reservation_id=? AND state=?",
                    (
                        BudgetState.DISPATCHED,
                        now,
                        str(admission_reservation_id),
                        BudgetState.RESERVED,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return cursor.rowcount == 1

    def release_research_budget(
        self,
        *,
        admission_reservation_id: str | None = None,
        claim_id: str | None = None,
        reservation_id: str | None = None,
        event: str = "cancel_before_dispatch",
    ) -> BudgetRelease:
        """Apply the frozen release rule; computation already done is not refunded."""
        rule = RELEASE_RULES.get(str(event)) or {
            "release_reserved_units": False,
            "detail": f"unknown settlement event {event!r}",
        }
        if not rule.get("release_reserved_units"):
            return BudgetRelease(
                False,
                BudgetReason.HELD,
                str(event),
                str(rule.get("detail") or "held"),
            )
        clauses: list[str] = []
        params: list[Any] = []
        if admission_reservation_id:
            clauses.append("admission_reservation_id=?")
            params.append(str(admission_reservation_id))
        if claim_id:
            clauses.append("claim_id=?")
            params.append(str(claim_id))
        if reservation_id:
            clauses.append("reservation_id=?")
            params.append(str(reservation_id))
        if not clauses:
            return BudgetRelease(False, BudgetReason.RESERVATION_NOT_FOUND, str(event), "no key")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM research_budget_reservations WHERE " + " AND ".join(clauses),
                    params,
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return BudgetRelease(
                        False, BudgetReason.RESERVATION_NOT_FOUND, str(event), "no reservation"
                    )
                cursor = conn.execute(
                    "UPDATE research_budget_reservations SET state=?, updated_at=? "
                    "WHERE reservation_id=? AND state IN (?, ?)",
                    (
                        BudgetState.RELEASED,
                        now,
                        str(row["reservation_id"]),
                        BudgetState.RESERVED,
                        BudgetState.DISPATCHED,
                    ),
                )
                if cursor.rowcount:
                    conn.execute(
                        "INSERT INTO research_budget_events "
                        "(subject, event, detail, created_at) VALUES (?, ?, ?, ?)",
                        (str(row["reservation_id"]), "budget_release", str(event), now),
                    )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        if cursor.rowcount:
            return BudgetRelease(True, BudgetReason.RELEASED, str(event), str(rule.get("detail")))
        return BudgetRelease(False, BudgetReason.RELEASED, str(event), "already released")

    def research_budget_for_admission(
        self, admission_reservation_id: str
    ) -> BudgetReservation | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM research_budget_reservations "
                    "WHERE admission_reservation_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(admission_reservation_id),),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else self._row_to_budget_reservation(row)

    def research_budget_for_claim(self, claim_id: str) -> BudgetReservation | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM research_budget_reservations WHERE claim_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (str(claim_id),),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else self._row_to_budget_reservation(row)

    def research_budget_projection(self, budget_id: str) -> BudgetProjection | None:
        with self._lock:
            conn = self._connect()
            try:
                policy = self._budget_policy_in(conn, budget_id)
                if policy is None:
                    return None
                consumed = self._budget_consumed_in(conn, budget_id)
                reservation_rows = conn.execute(
                    "SELECT * FROM research_budget_reservations WHERE budget_id=? "
                    "ORDER BY created_at, reservation_id",
                    (str(budget_id),),
                ).fetchall()
                receipt_rows = conn.execute(
                    "SELECT usage_json FROM research_budget_receipts WHERE budget_id=?",
                    (str(budget_id),),
                ).fetchall()
            finally:
                conn.close()
        receipt_counters = {unit: 0 for unit in UNIT_CAPABILITIES}
        for row in receipt_rows:
            for unit, amount in (json.loads(row["usage_json"]) or {}).items():
                receipt_counters[unit] = receipt_counters.get(unit, 0) + int(amount)
        remaining = {
            unit: max(0, cap - consumed.get(unit, 0)) for unit, cap in policy.limits.items()
        }
        return BudgetProjection(
            policy=policy,
            consumed=consumed,
            remaining=remaining,
            reservations=tuple(self._row_to_budget_reservation(row) for row in reservation_rows),
            receipts=len(receipt_rows),
            receipt_counters=receipt_counters,
        )

    @staticmethod
    def _row_to_budget_reservation(row: sqlite3.Row) -> BudgetReservation:
        def _optional(name: str, default: Any) -> Any:
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        return BudgetReservation(
            reservation_id=str(row["reservation_id"]),
            budget_id=str(row["budget_id"]),
            units=json.loads(row["units_json"]) or {},
            state=str(row["state"]),
            task_ref=str(row["task_ref"] or ""),
            run_id=str(row["run_id"] or ""),
            claim_id=str(row["claim_id"] or ""),
            admission_reservation_id=str(row["admission_reservation_id"] or ""),
            detail=str(row["detail"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            attempt=int(_optional("attempt", 0) or 0),
            receipt_id=str(_optional("receipt_id", "") or ""),
            chain_digest=str(_optional("chain_digest", "") or ""),
            release_id=str(_optional("release_id", "") or ""),
            plan_identity=str(_optional("plan_identity", "") or ""),
        )

    def research_budget_evidence(
        self,
        budget_id: str,
        *,
        max_receipts: int = 25,
        max_reservations: int = 50,
        max_bytes: int = 65536,
    ) -> dict[str, Any] | None:
        """Bounded read-only export that stays available after science stops."""
        projection = self.research_budget_projection(budget_id)
        if projection is None:
            return None
        with self._lock:
            conn = self._connect()
            try:
                receipts = [
                    {
                        "receipt_id": row["receipt_id"],
                        "reservation_id": row["reservation_id"],
                        "run_id": row["run_id"],
                        "usage": json.loads(row["usage_json"]) or {},
                        "trusted_source": row["trusted_source"],
                        "producer": row["producer"],
                        "job_id": row["job_id"],
                        "attempt": int(row["attempt"] or 0),
                        "usage_id": row["usage_id"],
                        "chain_digest": row["chain_digest"],
                        "binding_digest": row["binding_digest"],
                        "applied": bool(row["applied"]),
                        "recorded_at": row["recorded_at"],
                    }
                    for row in conn.execute(
                        "SELECT * FROM research_budget_receipts WHERE budget_id=? "
                        "ORDER BY recorded_at, receipt_id LIMIT ?",
                        (str(budget_id), int(max_receipts) + 1),
                    ).fetchall()
                ]
                events = [
                    {
                        "event": row["event"],
                        "subject": row["subject"],
                        "detail": row["detail"],
                        "created_at": row["created_at"],
                    }
                    for row in conn.execute(
                        "SELECT * FROM research_budget_events ORDER BY seq DESC LIMIT 100"
                    ).fetchall()
                ]
            finally:
                conn.close()
        receipts_truncated = len(receipts) > int(max_receipts)
        receipts = receipts[: int(max_receipts)]
        reservations = [item.to_dict() for item in projection.reservations]
        reservations_truncated = len(reservations) > int(max_reservations)
        reservations = reservations[: int(max_reservations)]
        budget_view = projection.to_dict()
        budget_view.pop("reservations", None)
        budget_view.pop("release_statement", None)
        statement = release_statement()
        document: dict[str, Any] = {
            "schema": "acp2.research-budget-evidence.v1",
            "read_only": True,
            "budget": budget_view,
            "reservations": reservations,
            "reservations_truncated": reservations_truncated,
            "receipts": receipts,
            "receipts_truncated": receipts_truncated,
            "events": events,
            "release_statement": statement,
            "bounds": {
                "max_receipts": int(max_receipts),
                "max_reservations": int(max_reservations),
                "max_bytes": int(max_bytes),
            },
            "generated_at": self._clock(),
        }

        def _size() -> int:
            return len(json.dumps(document, sort_keys=True))

        if _size() > int(max_bytes):
            document["release_statement"] = {
                key: statement[key] for key in ("enforced", "recorded_only", "not_verified")
            }
        if _size() > int(max_bytes):
            document["events"] = []
        while _size() > int(max_bytes) and document["receipts"]:
            document["receipts"].pop()
            document["receipts_truncated"] = True
        while _size() > int(max_bytes) and document["reservations"]:
            document["reservations"].pop()
            document["reservations_truncated"] = True
        if _size() > int(max_bytes):
            document.pop("release_statement", None)
        return document


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class AdmissionController:
    """Compute effective resources and admit/reserve/re-validate submissions.

    Args:
        grants: Injected :class:`GrantProvider` (versioned grants + placement).
        capacity: Injected :class:`CapacityProvider` (actual pool capacity).
        ledger: The atomic :class:`ReservationLedger`.
        clock: Optional ``() -> str`` timestamp override for decisions.
    """

    def __init__(
        self,
        *,
        grants: GrantProvider,
        capacity: CapacityProvider,
        ledger: ReservationLedger,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._grants = grants
        self._capacity = capacity
        self._ledger = ledger
        self._clock = clock or utc_now_iso

    # -- public API ----------------------------------------------------------

    def admit(
        self,
        request: AdmissionRequest,
        *,
        mode: str = AdmissionMode.QUOTA_ONLY,
        queued: bool | None = None,
        reservation_id: str | None = None,
        idempotency: ReservationIdempotency | None = None,
    ) -> AdmissionDecision:
        """Admit *request*, atomically reserving its resources before dispatch.

        In ``quota-only`` mode the effective ceiling must be *executable*: a
        policy version, an enforced hard-limit capability and a finite per-job
        bound for every requested dimension.  Any missing bound is a structured
        refusal; the only bypass is an explicit ``usage-only`` mode.

        ``reservation_id`` / ``idempotency`` are the B08 claim-keyed hooks: the
        reservation row and its claim link are written in the same short ledger
        transaction, so a replay never reserves twice.
        """
        usage_only = self._mode(mode) == AdmissionMode.USAGE_ONLY
        resolution = self._grants.resolve(request)
        bounds = resolution.effective_bounds()

        gate = self._executable_gate(mode, resolution, request, bounds)
        if gate is not None:
            return gate

        placement = self._placement_gate(bounds, request)
        if placement is not None:
            return placement

        capacity = self._effective_capacity(resolution, request)
        limits = ReserveLimits(
            capacity=capacity,
            policy_version=resolution.policy_version,
            request_bounds=bounds,
            user_bounds=_bounds_from_grant(resolution.user_grant),
            project_bounds=_bounds_from_grant(resolution.project_grant),
            key_bounds=_bounds_from_grant(resolution.key_grant),
            queued=request.queued if queued is None else bool(queued),
            enforce=not usage_only,
        )
        outcome = self._ledger.reserve(
            request,
            limits,
            reservation_id=reservation_id,
            idempotency=idempotency,
        )
        return self._decision(
            outcome,
            mode=mode,
            usage_only=usage_only,
            bounds=bounds,
            policy_version=resolution.policy_version,
        )

    def revalidate(
        self,
        reservation_id: str,
        *,
        mode: str = AdmissionMode.QUOTA_ONLY,
    ) -> AdmissionDecision:
        """Atomically re-check and promote a reservation immediately before dispatch.

        Refuses when the reservation is unknown/released, when the governing
        policy version changed since the reservation was made, when the effective
        ceiling is no longer executable, or when the actual capacity no longer
        holds the freed CPU/memory.
        """
        existing = self._ledger.get(reservation_id)
        if existing is None:
            return self._refuse(
                AdmissionReason.RESERVATION_NOT_FOUND,
                f"reservation {reservation_id!r} does not exist",
                mode=mode,
            )
        if existing.state == ReservationState.RELEASED:
            return self._refuse(
                AdmissionReason.RESERVATION_RELEASED,
                f"reservation {reservation_id!r} was released",
                mode=mode,
                reservation_id=reservation_id,
            )

        request = _request_from_reservation(existing)
        resolution = self._grants.resolve(request)
        usage_only = self._mode(mode) == AdmissionMode.USAGE_ONLY

        if resolution.policy_version != existing.policy_version:
            return self._refuse(
                AdmissionReason.POLICY_VERSION_CHANGED,
                f"policy version {existing.policy_version!r} -> "
                f"{resolution.policy_version!r}; re-admit required",
                mode=mode,
                reservation_id=reservation_id,
                policy_version=resolution.policy_version,
            )

        bounds = resolution.effective_bounds()
        gate = self._executable_gate(mode, resolution, request, bounds)
        if gate is not None:
            return self._replace_reservation(gate, reservation_id)

        placement = self._placement_gate(bounds, request)
        if placement is not None:
            return self._replace_reservation(placement, reservation_id)

        capacity = self._effective_capacity(resolution, request)
        outcome = self._ledger.dispatch(
            reservation_id,
            capacity=capacity,
            expected_policy_version=existing.policy_version,
            enforce=not usage_only,
        )
        return self._decision(
            outcome,
            mode=mode,
            usage_only=usage_only,
            bounds=bounds,
            policy_version=resolution.policy_version,
            reservation_id=reservation_id,
        )

    def mark_paused(
        self,
        reservation_id: str,
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        storage_bytes: int | None = None,
    ) -> Reservation | None:
        """Pause a reservation: CPU released, **actual** memory/disk kept."""
        return self._ledger.set_state(
            reservation_id,
            state=ReservationState.PAUSED,
            actual_cores=0 if cores is None else cores,
            actual_memory_mb=memory_mb,
            actual_storage_bytes=storage_bytes,
        )

    def record_usage(
        self,
        reservation_id: str,
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        storage_bytes: int | None = None,
    ) -> Reservation | None:
        """Record measured usage without changing state.

        A paused reservation's CPU is still released (the state rule decides how
        much is counted); memory/disk measured here are what the occupancy uses.
        """
        existing = self._ledger.get(reservation_id)
        if existing is None or existing.state == ReservationState.RELEASED:
            return None
        return self._ledger.set_state(
            reservation_id,
            state=existing.state,
            actual_cores=cores,
            actual_memory_mb=memory_mb,
            actual_storage_bytes=storage_bytes,
        )

    def release(self, reservation_id: str) -> bool:
        """Release a reservation (terminal); frees all held dimensions."""
        return self._ledger.release(reservation_id)

    def record_usage_receipt(
        self,
        usage_id: str,
        *,
        reservation_id: str = "",
        job_id: str,
        attempt: int = 1,
        cores: int = 0,
        seconds: int = 0,
        producer: str = UsageProducer.INTERNAL,
        receipt: ExecutionReceipt | None = None,
        binding: ReceiptHistoryBinding | None = None,
        counters: UsageUnitCounts | None = None,
        budget_id: str = "",
        budget_reservation_id: str = "",
        run_id: str = "",
    ) -> bool:
        """Book one trusted usage receipt exactly once (idempotent by id).

        Returns ``True`` only when this delivery applied.  A rejection raises
        :class:`UsageReceiptRejectedError` and writes nothing.
        """
        outcome = self.ingest_usage_receipt(
            usage_id,
            reservation_id=reservation_id,
            job_id=job_id,
            attempt=attempt,
            cores=cores,
            seconds=seconds,
            producer=producer,
            receipt=receipt,
            binding=binding,
            counters=counters,
            budget_id=budget_id,
            budget_reservation_id=budget_reservation_id,
            run_id=run_id,
        )
        return outcome.applied

    def ingest_usage_receipt(
        self,
        usage_id: str,
        *,
        job_id: str,
        attempt: int = 1,
        reservation_id: str = "",
        cores: int = 0,
        seconds: int = 0,
        producer: str,
        receipt: ExecutionReceipt | None = None,
        binding: ReceiptHistoryBinding | None = None,
        counters: UsageUnitCounts | None = None,
        budget_id: str = "",
        budget_reservation_id: str = "",
        run_id: str = "",
    ) -> UsageIngestOutcome:
        """Settlement wire point: verify the producer + attempt, then book.

        ``producer`` must come from a server-side authority
        (:mod:`acp_services.runtime.usage_authority`), never from a request
        field; the trusted source recorded on the receipt is derived from it.
        """
        return self._ledger.record_usage_once(
            usage_id,
            reservation_id=reservation_id,
            job_id=job_id,
            attempt=attempt,
            cores=cores,
            seconds=seconds,
            producer=producer,
            receipt=receipt,
            binding=binding,
            counters=counters,
            budget_id=budget_id,
            budget_reservation_id=budget_reservation_id,
            run_id=run_id,
        )

    def bind_attempt_identity(self, binding: ReceiptHistoryBinding) -> bool:
        """Persist the immutable identity of one ``(job, attempt)``."""
        return self._ledger.bind_attempt_identity(binding)

    def bind_reservation_job(self, reservation_id: str, job_id: str) -> bool:
        """Bind a claim-keyed reservation to the task it admitted."""
        return self._ledger.bind_reservation_job(reservation_id, job_id)

    def declare_attempt(self, job_id: str, attempt: int, *, reservation_id: str = "") -> bool:
        """Record the Controller's dispatch of one ``(job, attempt)``."""
        return self._ledger.declare_attempt(job_id, attempt, reservation_id=reservation_id)

    def attempt_binding(self, job_id: str, attempt: int) -> ReceiptHistoryBinding | None:
        """Return the persisted identity of one ``(job, attempt)`` (if bound)."""
        return self._ledger.attempt_binding(job_id, attempt)

    def usage_by_job(self, job_id: str) -> list[dict[str, Any]]:
        """Booked usage receipts for *job_id* (readable after a task purge)."""
        return self._ledger.usage_by_job(job_id)

    def verify_usage_history(self, job_id: str) -> dict[str, Any]:
        """Re-verify booked usage from persisted identities only (no work_dir)."""
        return self._ledger.verify_usage_history(job_id)

    def usage_totals(self) -> dict[str, Any]:
        """Return the ledger's usage projection (duplicates never double-charge)."""
        return self._ledger.usage_totals()

    def reservation(self, reservation_id: str) -> Reservation | None:
        return self._ledger.get(reservation_id)

    def occupancy(self) -> dict[str, Any]:
        return self._ledger.occupancy()

    def effective_bounds(self, request: AdmissionRequest) -> ResourceBounds:
        """Expose the computed effective ceiling for diagnostics/UI."""
        return self._grants.resolve(request).effective_bounds()

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _mode(mode: str) -> str:
        if str(mode) not in AdmissionMode.ALL:
            raise AdmissionValidationError(
                f"unknown admission mode {mode!r}; expected one of {sorted(AdmissionMode.ALL)}"
            )
        return str(mode)

    def _effective_capacity(
        self, resolution: GrantResolution, request: AdmissionRequest
    ) -> Capacity:
        capacity = self._capacity.capacity()
        if request.queue is not None and request.queue in resolution.queue_capacity:
            capacity = capacity.minimum(resolution.queue_capacity[request.queue])
        if request.node is not None and request.node in resolution.node_capacity:
            capacity = capacity.minimum(resolution.node_capacity[request.node])
        return capacity

    def _executable_gate(
        self,
        mode: str,
        resolution: GrantResolution,
        request: AdmissionRequest,
        bounds: ResourceBounds,
    ) -> AdmissionDecision | None:
        if not str(resolution.policy_version).strip():
            return self._refuse(
                AdmissionReason.NO_POLICY_VERSION,
                "a versioned policy version is required for resource admission",
                mode=mode,
                bounds=bounds,
                policy_version=resolution.policy_version,
            )
        if self._mode(mode) == AdmissionMode.USAGE_ONLY:
            return None
        if str(resolution.hard_limit_capability) != HARD_LIMIT_ENFORCED:
            return self._refuse(
                AdmissionReason.HARD_LIMIT_UNAVAILABLE,
                "quota-only admission refused: no enforced hard-limit capability",
                mode=mode,
                bounds=bounds,
                policy_version=resolution.policy_version,
            )
        capacity = self._effective_capacity(resolution, request)
        if not capacity.available:
            return self._refuse(
                AdmissionReason.HARD_LIMIT_UNAVAILABLE,
                "quota-only admission refused: actual capacity unavailable",
                mode=mode,
                bounds=bounds,
                policy_version=resolution.policy_version,
            )
        for dimension in request.resource_dimensions():
            if not bounds.is_finite(dimension):
                return self._refuse(
                    AdmissionReason.UNBOUNDED_REQUEST,
                    f"quota-only admission refused: no executable upper bound for "
                    f"{dimension} (only explicit usage-only is allowed)",
                    mode=mode,
                    bounds=bounds,
                    policy_version=resolution.policy_version,
                )
        return None

    def _placement_gate(
        self, bounds: ResourceBounds, request: AdmissionRequest
    ) -> AdmissionDecision | None:
        if request.queue is not None and bounds.queues is not None:
            if request.queue not in bounds.queues:
                return self._refuse(
                    AdmissionReason.QUEUE_NOT_ALLOWED,
                    f"queue {request.queue!r} is not within the effective allow-list",
                    bounds=bounds,
                )
        if request.node is not None and bounds.nodes is not None:
            if request.node not in bounds.nodes:
                return self._refuse(
                    AdmissionReason.NODE_NOT_ALLOWED,
                    f"node {request.node!r} is not within the effective allow-list",
                    bounds=bounds,
                )
        return None

    def _decision(
        self,
        outcome: ReserveOutcome,
        *,
        mode: str,
        usage_only: bool,
        bounds: ResourceBounds,
        policy_version: str,
        reservation_id: str | None = None,
    ) -> AdmissionDecision:
        admitted = outcome.allowed
        reason = outcome.reason_code
        if admitted and usage_only:
            reason = AdmissionReason.USAGE_ONLY
        rid = reservation_id
        if rid is None and outcome.reservation is not None:
            rid = outcome.reservation.reservation_id
        return AdmissionDecision(
            admitted=admitted,
            reason_code=reason,
            detail=outcome.detail,
            reservation_id=rid,
            mode=self._mode(mode),
            enforced=admitted and not usage_only,
            effective_bounds=bounds,
            policy_version=policy_version,
            exceeded=outcome.exceeded,
        )

    def _refuse(
        self,
        reason: str,
        detail: str,
        *,
        mode: str = AdmissionMode.QUOTA_ONLY,
        bounds: ResourceBounds | None = None,
        policy_version: str = "",
        reservation_id: str | None = None,
        exceeded: tuple[Mapping[str, Any], ...] = (),
    ) -> AdmissionDecision:
        return AdmissionDecision(
            admitted=False,
            reason_code=reason,
            detail=detail,
            reservation_id=reservation_id,
            mode=self._mode(mode),
            enforced=False,
            effective_bounds=bounds,
            policy_version=policy_version,
            exceeded=exceeded,
        )

    def _replace_reservation(
        self, decision: AdmissionDecision, reservation_id: str
    ) -> AdmissionDecision:
        if decision.reservation_id == reservation_id:
            return decision
        return AdmissionDecision(
            admitted=decision.admitted,
            reason_code=decision.reason_code,
            detail=decision.detail,
            reservation_id=reservation_id,
            mode=decision.mode,
            enforced=decision.enforced,
            effective_bounds=decision.effective_bounds,
            policy_version=decision.policy_version,
            exceeded=decision.exceeded,
        )


def _request_from_reservation(reservation: Reservation) -> AdmissionRequest:
    return AdmissionRequest(
        principal_id=reservation.principal_id,
        project_id=reservation.project_id,
        job_id=reservation.job_id,
        key_id=reservation.key_id,
        cores=reservation.cores,
        memory_mb=reservation.memory_mb,
        duration_s=reservation.duration_s,
        storage_bytes=reservation.storage_bytes,
        queue=reservation.queue,
        node=reservation.node,
    )


def render_admission_decision(decision: AdmissionDecision) -> str:
    """Render an :class:`AdmissionDecision` for an operator/CLI surface."""
    verdict = "准入通过" if decision.admitted else "准入拒绝"
    lines = [f"{verdict}（{decision.reason_code}，mode={decision.mode}）"]
    if decision.detail:
        lines.append(f"  {decision.detail}")
    if decision.effective_bounds is not None:
        bound = decision.effective_bounds
        lines.append(
            "  有效上限："
            f"cores={bound.cores} memory_mb={bound.memory_mb} "
            f"concurrency={bound.concurrency} duration_s={bound.duration_s} "
            f"storage_bytes={bound.storage_bytes} "
            f"queues={bound.queues} nodes={bound.nodes}"
        )
    if decision.policy_version:
        lines.append(f"  政策版本：{decision.policy_version}")
    return "\n".join(lines)
