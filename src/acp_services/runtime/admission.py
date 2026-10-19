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

import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.access import ResourceGrantSpec
from acp_services.runtime.helpers import utc_now_iso

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
"""


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
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(_CREATE_TABLE_SQL)
            finally:
                conn.close()

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

    def reserve(self, request: AdmissionRequest, limits: ReserveLimits) -> ReserveOutcome:
        """Atomically check every ceiling and insert the reservation.

        The occupancy read, the ceiling comparison and the ``INSERT`` all happen
        inside one ``BEGIN IMMEDIATE`` transaction.  A refusal rolls back without
        inserting anything (no partial reservation).
        """
        now = self._clock()
        state = ReservationState.QUEUED if limits.queued else ReservationState.RESERVED
        reservation = Reservation(
            reservation_id=self._new_id(),
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
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return ReserveOutcome(True, AdmissionReason.ADMITTED, "reserved", reservation, ())

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
    ) -> AdmissionDecision:
        """Admit *request*, atomically reserving its resources before dispatch.

        In ``quota-only`` mode the effective ceiling must be *executable*: a
        policy version, an enforced hard-limit capability and a finite per-job
        bound for every requested dimension.  Any missing bound is a structured
        refusal; the only bypass is an explicit ``usage-only`` mode.
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
        outcome = self._ledger.reserve(request, limits)
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
