"""Persistent event protocol service — append / replay / dedup / reset (B04-1).

This is the Runtime-side *delivery* half of the persistent event protocol.  It
sits on top of the existing per-task append-only JSONL log
(``src/acp/scheduler/events.py::JobEventLog``) through an injected, neutral
:class:`EventLogPort`; the acp-side adapter that delegates to the real
``JobEventLog`` lives in ``src/acp/services_events_bootstrap.py`` so this module
never imports ``acp``.

Guarantees
----------
* **Stable identity / idempotent append** — every event has a stable
  ``event_id`` (and ``dedup_key``).  Re-delivering the same fact appends
  *nothing twice*: :meth:`PersistentEventService.append` returns the existing
  envelope when the dedup key is already present (the underlying log's own
  ``idempotency_key`` guard is used in addition).
* **Persistent cursor** — a cursor is the bare ``str(seq)`` form of a durable
  per-owner sequence, so ``after_seq`` replay survives a process restart.
* **No silent drops** — a cursor that falls outside the retained window is
  answered with an explicit :class:`EventReplay` carrying ``reset=True`` and
  ``reset_reason='cursor_expired'`` (never an empty-but-silent success).  The
  consumer must then reset and full-reread via :meth:`snapshot`.
* **Corrupt streams are rejected** — a per-owner ``seq`` regression raises
  :class:`~acp_services.contracts.events.EventStreamCorruptError` rather than
  being reordered or truncated.
* **Old JSONL compatibility** — records written by the legacy code paths (only
  ``type`` / optional ``after_seq`` / ``timestamp``, no ``schema_version``) are
  migrated on read; the old event file and its fields are never modified.

Out-of-scope seam (todo 23)
---------------------------
The **transactional outbox** — writing an owner's state change and its outbox
row in one database transaction *before* delivery — is owned by todo 23.  This
module deliberately performs only the post-commit durable delivery; the seam it
will be composed through is :class:`EventOutboxPort`, documented below and left
unimplemented here.  Events are a replayable projection and must never become
the authority for job state (the CAS store remains authoritative).

Author: ACP Services (B lane, todo 22).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    EventCursor,
    EventCursorError,
    EventEnvelope,
    EventScope,
    OwnerType,
    validate_event_sequence,
)

__all__ = [
    "CATEGORY_COVERAGE",
    "EventCategory",
    "EventLogError",
    "EventLogPort",
    "EventOutboxPort",
    "EventReplay",
    "PersistentEventService",
    "RESET_CURSOR_EXPIRED",
    "RESET_PERMISSION_CHANGED",
    "RESET_SERVICE_RESTART",
    "RESET_SUBSCRIPTION_CHANGED",
    "ScopeCoverage",
    "StreamAuthorization",
    "StreamAuthorizationError",
    "StreamAuthorizer",
    "StreamBatch",
    "StreamCursor",
    "StreamKey",
    "StreamScopeMismatchError",
    "AuthorizedEventStream",
    "AuthorizedStreamRegistry",
    "classify_event",
    "coverage_matrix",
    "envelopes_from_records",
]


# ── errors ──────────────────────────────────────────────────────────────────


class EventLogError(RuntimeError):
    """The injected event-log port failed to deliver/read a fact."""


# ── injected ports ──────────────────────────────────────────────────────────


@runtime_checkable
class EventLogPort(Protocol):
    """The append/read surface the service needs from a durable event log.

    Exactly the public shape of ``acp.scheduler.events.JobEventLog`` (``append``
    with an ``idempotency_key`` guard, ``count``, ``read_all``, ``read_last``);
    the acp-side adapter implements it, tests may inject an in-memory fake, and
    this Protocol keeps the service free of any ``acp`` import.
    """

    def append(self, event_type: str, *, idempotency_key: str | None = None, **data: Any) -> None:
        """Append/merge one record (no-op when *idempotency_key* already exists)."""
        ...

    def count(self) -> int:
        """Number of durable event slots (complete lines)."""
        ...

    def read_all(self) -> list[dict[str, Any]]:
        """Return every decodable record, oldest first."""
        ...

    def read_last(self) -> dict[str, Any] | None:
        """Return the last decodable record, or ``None``."""
        ...


@runtime_checkable
class EventOutboxPort(Protocol):
    """TODO 23 seam — write an event inside the owner's state transaction.

    Not implemented here.  Todo 23 (B04-2, transactional outbox) extends the
    owner repository's transaction boundary so a state change and its outbox
    row commit together; delivery to this service (a durable
    :class:`EventLogPort`) happens **after** that commit, idempotently, keyed on
    the same stable ``event_id`` / ``dedup_key``.  Declaring the seam now lets
    the Runtime controller be wired without a second transaction wrapper and
    without treating an in-process notification as fact.
    """

    def enqueue(self, envelope: EventEnvelope) -> None:
        """Stage *envelope* for durable delivery within the owner transaction."""
        ...


# ── result carriers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventReplay:
    """Result of a cursor-addressed replay.

    ``reset=True`` is the **snapshot-reset signal**: the requested cursor fell
    outside the retained window, ``events`` is empty, and the consumer must
    discard its cursor and full-reread (:meth:`PersistentEventService.snapshot`).
    ``reset=False`` with an empty ``events`` tuple simply means "caught up" — the
    two are never conflated, so an expired cursor can never masquerade as a
    silent drop.
    """

    events: tuple[EventEnvelope, ...] = ()
    cursor: EventCursor | None = None
    reset: bool = False
    reset_reason: str = ""
    earliest_seq: int = 0
    latest_seq: int = 0

    def __bool__(self) -> bool:
        return bool(self.events)


# ── migration helper (read path) ────────────────────────────────────────────


def envelopes_from_records(
    records: Sequence[Mapping[str, Any]],
    *,
    owner_type: OwnerType,
    owner_id: str,
    scope: EventScope,
) -> list[EventEnvelope]:
    """Decode JSONL records into envelopes, migrating legacy records.

    Records carrying an explicit ``schema_version`` + ``seq`` are decoded
    strictly; anything else (legacy ``type`` / optional ``after_seq`` records)
    is migrated using its file position as the sequence so no record is lost and
    the old format remains readable.
    """
    out: list[EventEnvelope] = []
    for index, record in enumerate(records, start=1):
        if "schema_version" in record and "seq" in record:
            out.append(EventEnvelope.from_dict(record))
        else:
            out.append(
                EventEnvelope.from_legacy_record(
                    record,
                    owner_type=owner_type,
                    owner_id=owner_id,
                    scope=scope,
                    seq=index,
                )
            )
    validate_event_sequence(out)
    return out


# ── the service ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PersistentEventService:
    """Durable event protocol over an injected :class:`EventLogPort`.

    ``retention`` bounds how many of the most-recent events remain addressable
    for incremental replay (``None`` = the whole append-only log stays
    addressable, matching today's unbounded JSONL).  A request older than the
    retained window yields the explicit reset signal rather than a silent gap.
    """

    log: EventLogPort
    owner_type: OwnerType
    owner_id: str
    scope: EventScope
    retention: int | None = None
    schema_version: int = CURRENT_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.retention is not None and self.retention < 1:
            raise EventLogError("retention must be >= 1 or None")

    # -- append ------------------------------------------------------------ #

    def append(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        dedup_key: str | None = None,
        emitted_at: str | None = None,
    ) -> EventEnvelope:
        """Append one fact idempotently; return the durable envelope.

        A repeated ``dedup_key`` (stable event ID) returns the already-written
        envelope and appends nothing.  The event's ``seq`` is derived from the
        durable slot count, so the cursor is persistent across restarts.
        """
        envelopes = self._read_envelopes()
        resolved_dedup = dedup_key or ""
        if resolved_dedup:
            for existing in envelopes:
                if existing.dedup_key == resolved_dedup:
                    return existing
        next_seq = (envelopes[-1].seq if envelopes else 0) + 1
        event_id = resolved_dedup or f"{self.owner_type.value}:{self.owner_id}:{next_seq}"
        envelope = EventEnvelope(
            event_id=event_id,
            seq=next_seq,
            schema_version=self.schema_version,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            scope=self.scope,
            event_type=event_type,
            cursor=str(next_seq),
            emitted_at=emitted_at if emitted_at is not None else _utc_now_iso(),
            payload=dict(payload or {}),
            dedup_key=resolved_dedup or event_id,
            coverage=self.scope,
        )
        self.log.append(
            event_type,
            idempotency_key=envelope.dedup_key,
            **self._record_payload(envelope),
        )
        return envelope

    @staticmethod
    def _record_payload(envelope: EventEnvelope) -> dict[str, Any]:
        """The envelope persisted onto the JSONL (carriers renamed, not dropped).

        ``event_type`` is the log's own ``type`` argument and ``emitted_at`` is
        written as ``timestamp`` (the log's carrier) so a re-read reconstructs an
        envelope byte-for-byte identical to the one returned by ``append``.
        """
        record = envelope.to_dict()
        record.pop("event_type", None)
        record.pop("emitted_at", None)
        record["timestamp"] = envelope.emitted_at
        return record

    # -- replay / snapshot ------------------------------------------------- #

    def replay(self, after_seq: int = 0, *, limit: int | None = None) -> EventReplay:
        """Return events strictly after *after_seq*, or an explicit reset signal.

        ``after_seq`` is the legacy ``after_seq`` / ``Last-Event-ID`` integer.
        A cursor below the retained window yields ``reset=True`` with no events
        (the consumer must reset + full-reread) — it is never answered with an
        empty success while events exist.
        """
        if after_seq < 0:
            raise EventCursorError(f"after_seq must be >= 0, got {after_seq}")
        envelopes = self._read_envelopes()
        latest_seq = envelopes[-1].seq if envelopes else 0
        earliest_seq = 1
        reset = False
        reset_reason = ""
        if self.retention is not None and envelopes:
            earliest_seq = max(1, latest_seq - self.retention + 1)
            if after_seq > 0 and after_seq + 1 < earliest_seq:
                reset = True
                reset_reason = "cursor_expired"

        if reset:
            return EventReplay(
                events=(),
                cursor=EventCursor(
                    owner_type=self.owner_type, owner_id=self.owner_id, seq=after_seq
                ),
                reset=True,
                reset_reason=reset_reason,
                earliest_seq=earliest_seq,
                latest_seq=latest_seq,
            )

        selected = [event for event in envelopes if event.seq > after_seq]
        if self.retention is not None and after_seq == 0:
            # Fresh subscription: expose only the retained tail (bounded initial).
            selected = [event for event in selected if event.seq >= earliest_seq]
        if limit is not None and limit >= 0:
            selected = selected[:limit]

        if selected:
            resume_seq = selected[-1].seq
        elif after_seq == 0:
            resume_seq = latest_seq
        else:
            resume_seq = after_seq
        return EventReplay(
            events=tuple(selected),
            cursor=EventCursor(owner_type=self.owner_type, owner_id=self.owner_id, seq=resume_seq),
            reset=False,
            reset_reason="",
            earliest_seq=earliest_seq,
            latest_seq=latest_seq,
        )

    def snapshot(self) -> tuple[EventEnvelope, ...]:
        """Full re-read of every durable event (the reset-recovery read)."""
        return tuple(self._read_envelopes())

    def current_cursor(self) -> EventCursor:
        """Cursor at the current durable tail (seq 0 when the log is empty)."""
        envelopes = self._read_envelopes()
        seq = envelopes[-1].seq if envelopes else 0
        return EventCursor(owner_type=self.owner_type, owner_id=self.owner_id, seq=seq)

    # -- internal ---------------------------------------------------------- #

    def _read_envelopes(self) -> list[EventEnvelope]:
        try:
            records = self.log.read_all()
        except OSError as exc:  # pragma: no cover - injected port failure
            raise EventLogError(f"event log read failed: {exc}") from exc
        return envelopes_from_records(
            records,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            scope=self.scope,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════
# B04-3 (todo 24) — authorized reusable stream + snapshot reset + scope matrix
# ══════════════════════════════════════════════════════════════════════════
#
# The persistent-event protocol above is *per owner* (one durable JSONL).  This
# layer composes owners into **one authorized stream per (principal, scope)**:
# a single subscription multiplexes every task/project/data owner inside that
# scope, so N tasks never mean N streams.  It deliberately does not decide
# whether a consumer may stop polling — it declares the event coverage
# (log / task progress / task summary / data availability) and leaves that
# degradation decision to the consumer (A04).


# ── non-wire event categories (coverage classes) ────────────────────────────


class EventCategory(str, Enum):
    """The four coverage classes declared to the A04 consumer.

    These are **not** wire ``scope`` values.  The frozen envelope vocabulary
    (task/project/data) is untouched; a category is derived from
    ``event_type`` and only describes what a consumer may infer from the event.
    """

    LOG = "log"
    TASK_PROGRESS = "task_progress"
    TASK_SUMMARY = "task_summary"
    DATA_AVAILABILITY = "data_availability"


#: Event types whose category is unambiguous.  Prefix families cover the rest.
_CATEGORY_BY_EVENT_TYPE: dict[str, EventCategory] = {
    "log": EventCategory.LOG,
    "log_line": EventCategory.LOG,
    "log_tail": EventCategory.LOG,
    "stdout": EventCategory.LOG,
    "stderr": EventCategory.LOG,
    "progress": EventCategory.TASK_PROGRESS,
    "stage": EventCategory.TASK_PROGRESS,
    "job.started": EventCategory.TASK_PROGRESS,
    "job.cancelling": EventCategory.TASK_PROGRESS,
    "job.waiting_review": EventCategory.TASK_PROGRESS,
    "job.paused": EventCategory.TASK_PROGRESS,
    "job.resumed": EventCategory.TASK_PROGRESS,
    "job.created": EventCategory.TASK_SUMMARY,
    "job.completed": EventCategory.TASK_SUMMARY,
    "job.failed": EventCategory.TASK_SUMMARY,
    "job.cancelled": EventCategory.TASK_SUMMARY,
    "job.purged": EventCategory.TASK_SUMMARY,
    "finalize": EventCategory.TASK_SUMMARY,
    "summary": EventCategory.TASK_SUMMARY,
    "availability": EventCategory.DATA_AVAILABILITY,
    "data_availability": EventCategory.DATA_AVAILABILITY,
    "artifact_available": EventCategory.DATA_AVAILABILITY,
    "cache_index": EventCategory.DATA_AVAILABILITY,
    "pending_fetch": EventCategory.DATA_AVAILABILITY,
}

#: Conservative fallback: an unknown owner fact is treated as a summary, never
#: as live progress, so it cannot justify stopping a progress poll on its own.
_DEFAULT_CATEGORY = EventCategory.TASK_SUMMARY


def classify_event(event_type: str, payload: Mapping[str, Any] | None = None) -> EventCategory:
    """Classify one event into its coverage category.

    Deterministic and total: unknown ``event_type`` values fall back to
    :data:`_DEFAULT_CATEGORY` (task summary), never to an error, so an old or
    future event type can still flow through an existing stream.  ``payload`` is
    accepted for forward-compatibility (e.g. an explicit ``category`` hint) but
    is not trusted over ``event_type`` today.
    """
    name = str(event_type or "").strip().lower()
    direct = _CATEGORY_BY_EVENT_TYPE.get(name)
    if direct is not None:
        return direct
    if name.startswith(("log", "stdout", "stderr")):
        return EventCategory.LOG
    if name.startswith(("avail", "data.", "data_", "cache_", "artifact_")):
        return EventCategory.DATA_AVAILABILITY
    if name.startswith(("progress", "stage", "process.")) or name.endswith("_progress"):
        return EventCategory.TASK_PROGRESS
    return _DEFAULT_CATEGORY


# ── coverage matrix (A04 consumption declaration) ───────────────────────────


@dataclass(frozen=True)
class ScopeCoverage:
    """One declared coverage row (category → channels / reset / sample).

    ``polling_degradation`` is intentionally the literal ``"a_side_decision"``:
    this service declares coverage, it does **not** tell the consumer to stop
    polling.  ``sample_sequence`` is a representative event-type sequence used
    by the tracked A04 contract document and its tests.
    """

    category: EventCategory
    wire_scope: EventScope
    event_types: tuple[str, ...]
    channels: tuple[str, ...]
    transport: str
    reset_on: tuple[str, ...]
    polling_degradation: str
    sample_sequence: tuple[str, ...]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "wire_scope": self.wire_scope.value,
            "event_types": list(self.event_types),
            "channels": list(self.channels),
            "transport": self.transport,
            "reset_on": list(self.reset_on),
            "polling_degradation": self.polling_degradation,
            "sample_sequence": list(self.sample_sequence),
            "notes": self.notes,
        }


#: Snapshot-reset reasons.  A consumer must discard its cursor and full-reread.
RESET_CURSOR_EXPIRED = "cursor_expired"
RESET_PERMISSION_CHANGED = "permission_changed"
RESET_SERVICE_RESTART = "service_restart"
RESET_SUBSCRIPTION_CHANGED = "subscription_changed"

_ALL_RESETS: tuple[str, ...] = (
    RESET_CURSOR_EXPIRED,
    RESET_PERMISSION_CHANGED,
    RESET_SERVICE_RESTART,
    RESET_SUBSCRIPTION_CHANGED,
)

CATEGORY_COVERAGE: dict[EventCategory, ScopeCoverage] = {
    EventCategory.LOG: ScopeCoverage(
        category=EventCategory.LOG,
        wire_scope=EventScope.TASK,
        event_types=("log", "log_line", "stdout", "stderr"),
        channels=("logs_api", "event_stream"),
        transport="poll_tail",
        reset_on=(RESET_CURSOR_EXPIRED, RESET_PERMISSION_CHANGED, RESET_SERVICE_RESTART),
        polling_degradation="a_side_decision",
        sample_sequence=("log_line", "log_line", "log_tail"),
        notes="Tail/offset semantics stay owned by the logs endpoint.",
    ),
    EventCategory.TASK_PROGRESS: ScopeCoverage(
        category=EventCategory.TASK_PROGRESS,
        wire_scope=EventScope.TASK,
        event_types=("progress", "stage.started", "stage.completed", "process.starting"),
        channels=("event_stream", "job_detail_poll"),
        transport="sse",
        reset_on=_ALL_RESETS,
        polling_degradation="a_side_decision",
        sample_sequence=("job.started", "stage.started", "progress", "stage.completed"),
        notes="Multiple task owners share one stream; progress is a hint, never authority.",
    ),
    EventCategory.TASK_SUMMARY: ScopeCoverage(
        category=EventCategory.TASK_SUMMARY,
        wire_scope=EventScope.TASK,
        event_types=("job.created", "job.completed", "job.failed", "job.cancelled"),
        channels=("event_stream", "job_detail_poll", "queue_poll"),
        transport="sse",
        reset_on=_ALL_RESETS,
        polling_degradation="a_side_decision",
        sample_sequence=("job.created", "job.completed"),
        notes="Terminal summary remains readable by the legacy after_seq endpoint.",
    ),
    EventCategory.DATA_AVAILABILITY: ScopeCoverage(
        category=EventCategory.DATA_AVAILABILITY,
        wire_scope=EventScope.DATA,
        event_types=("availability", "artifact_available", "cache_index", "pending_fetch"),
        channels=("availability_api", "data_event_stream"),
        transport="poll_diff",
        reset_on=(RESET_CURSOR_EXPIRED, RESET_PERMISSION_CHANGED, RESET_SERVICE_RESTART),
        polling_degradation="a_side_decision",
        sample_sequence=("availability", "artifact_available", "cache_index"),
        notes="Declares data coverage only; the consumer owns the fetch/degrade decision.",
    ),
}


def coverage_matrix() -> tuple[ScopeCoverage, ...]:
    """Return the declared coverage matrix in stable category order."""
    return tuple(CATEGORY_COVERAGE[category] for category in EventCategory)


# ── authorization / cursor carriers ─────────────────────────────────────────


class StreamAuthorizationError(PermissionError):
    """A subscription was refused (new connection or live revocation)."""


class StreamScopeMismatchError(StreamAuthorizationError):
    """A cursor addressed another principal/scope (cross-scope reuse denied)."""


@dataclass(frozen=True)
class StreamAuthorization:
    """Verdict of a subscription authorization check (Services-side value)."""

    allowed: bool
    principal_id: str = ""
    scope: EventScope = EventScope.TASK
    reason_code: str = ""
    detail: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


@runtime_checkable
class StreamAuthorizer(Protocol):
    """The subscription authorization port (adapter lives on the ``acp`` side)."""

    def authorize_stream(
        self, *, principal_id: str, scope: EventScope, job_id: str = ""
    ) -> StreamAuthorization:
        """Return the current verdict for this principal/scope/(job) subscription."""
        ...


@dataclass(frozen=True)
class StreamCursor:
    """A cursor bound to a principal + scope + owner (never a bare integer)."""

    principal_id: str
    scope: EventScope
    job_id: str
    after_seq: int = 0
    service_epoch: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EventScope):
            object.__setattr__(self, "scope", EventScope(self.scope))
        if self.after_seq < 0:
            raise EventCursorError(f"after_seq must be >= 0, got {self.after_seq}")


@dataclass(frozen=True)
class StreamKey:
    """Identity of one reusable stream: exactly ``(principal, scope)``."""

    principal_id: str
    scope: EventScope


@dataclass(frozen=True)
class StreamBatch:
    """One poll result over the multiplexed stream.

    ``reset=True`` is the snapshot-reset signal: the caller discards its
    per-owner cursors and full-rereads (:meth:`AuthorizedEventStream.snapshot`).
    ``cursors`` has advanced only over the events actually returned, so a bounded
    poll can never skip a fact.
    """

    events: tuple[EventEnvelope, ...] = ()
    cursors: Mapping[str, int] = field(default_factory=dict)
    reset: bool = False
    reset_reason: str = ""
    earliest_seq: Mapping[str, int] = field(default_factory=dict)
    latest_seq: Mapping[str, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.events)


# ── one authorized stream per (principal, scope) ────────────────────────────


class AuthorizedEventStream:
    """Multiplexing authorized stream (one object, many owners in one scope).

    Reuse contract
    --------------
    The registry hands the **same** instance back for the same
    ``(principal_id, scope)``; subscribing another task adds an owner to this
    stream, it does not create a per-task stream.  Calling :meth:`subscribe`
    twice is idempotent.

    Authorization
    -------------
    :meth:`check_authorization` re-evaluates every subscribed owner through the
    injected :class:`StreamAuthorizer`.  A denial closes the stream
    **immediately** (``closed`` + ``closed_reason='permission_changed'``); the
    next :meth:`poll` returns the reset signal and no further events.  New
    connections are refused by :meth:`AuthorizedStreamRegistry.open`.

    Reset
    -----
    Snapshot reset is signalled for an expired cursor, an authorization change,
    a service restart (epoch mismatch), and an explicit subscription change.
    A reset is always explicit (``reset=True`` + ``reset_reason``); it can never
    be mistaken for "caught up".
    """

    def __init__(
        self,
        *,
        principal_id: str,
        scope: EventScope,
        service_epoch: str,
        log_for: Callable[[str], EventLogPort | None],
        owner_type: OwnerType = OwnerType.JOB,
        retention: int | None = None,
    ) -> None:
        if retention is not None and retention < 1:
            raise EventLogError("retention must be >= 1 or None")
        self.principal_id = str(principal_id)
        self.scope = scope if isinstance(scope, EventScope) else EventScope(scope)
        self.service_epoch = str(service_epoch)
        self.owner_type = owner_type if isinstance(owner_type, OwnerType) else OwnerType(owner_type)
        self.retention = retention
        self._log_for = log_for
        self._tasks: dict[str, None] = {}
        self._closed = False
        self._closed_reason = ""
        self._pending_reset = ""
        self._lock = threading.RLock()

    # -- identity / subscription ------------------------------------------- #

    @property
    def key(self) -> StreamKey:
        return StreamKey(principal_id=self.principal_id, scope=self.scope)

    @property
    def task_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._tasks)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def closed_reason(self) -> str:
        return self._closed_reason

    def subscribe(self, job_ids: Sequence[str]) -> tuple[str, ...]:
        """Add owner(s) to this stream; idempotent, never returns a new stream."""
        with self._lock:
            if self._closed:
                raise StreamAuthorizationError(
                    f"stream {self.key.principal_id!r}/{self.scope.value} is closed "
                    f"({self._closed_reason or 'closed'})"
                )
            for job_id in job_ids:
                normalized = str(job_id)
                if normalized:
                    self._tasks.setdefault(normalized, None)
            return tuple(self._tasks)

    def unsubscribe(self, job_ids: Sequence[str]) -> tuple[str, ...]:
        """Remove owner(s); a subscription change signals a snapshot reset."""
        with self._lock:
            removed = False
            for job_id in job_ids:
                if str(job_id) in self._tasks:
                    del self._tasks[str(job_id)]
                    removed = True
            if removed:
                self._pending_reset = RESET_SUBSCRIPTION_CHANGED
            return tuple(self._tasks)

    # -- authorization / epoch --------------------------------------------- #

    def check_authorization(
        self,
        authorizer: StreamAuthorizer,
        *,
        job_ids: Sequence[str] | None = None,
    ) -> bool:
        """Re-check every subscription; close immediately on the first denial."""
        targets = tuple(job_ids) if job_ids is not None else self.task_ids
        if not targets:
            targets = ("",)
        for job_id in targets:
            verdict = authorizer.authorize_stream(
                principal_id=self.principal_id, scope=self.scope, job_id=job_id
            )
            if verdict.denied or (
                verdict.principal_id and verdict.principal_id != self.principal_id
            ):
                self._close(RESET_PERMISSION_CHANGED)
                return False
        return True

    def check_service_epoch(self, service_epoch: str) -> bool:
        """False (and a pending reset) when the process epoch changed."""
        if service_epoch and str(service_epoch) != self.service_epoch:
            self._close(RESET_SERVICE_RESTART)
            return False
        return True

    def reset(self, reason: str = RESET_SUBSCRIPTION_CHANGED) -> None:
        """Request an explicit snapshot reset on the next poll."""
        with self._lock:
            self._pending_reset = RESET_CURSOR_EXPIRED if reason == RESET_CURSOR_EXPIRED else reason

    def close(self, reason: str = "closed") -> None:
        self._close(reason)

    def _close(self, reason: str) -> None:
        with self._lock:
            self._closed = True
            self._closed_reason = reason

    # -- reads -------------------------------------------------------------- #

    def poll(
        self,
        cursors: Sequence[StreamCursor] = (),
        *,
        limit: int | None = None,
    ) -> StreamBatch:
        """Return events after *cursors* across every subscribed owner.

        Cross-principal / cross-scope cursors raise
        :class:`StreamScopeMismatchError` (reuse is denied, not reinterpreted).
        An epoch mismatch or a closed stream yields an explicit reset batch.
        """
        cursor_map = self._cursor_map(cursors)
        with self._lock:
            if self._closed:
                return StreamBatch(reset=True, reset_reason=self._closed_reason or "closed")
            if self._pending_reset:
                reason = self._pending_reset
                self._pending_reset = ""
                return StreamBatch(reset=True, reset_reason=reason)

            merged: list[EventEnvelope] = []
            cursors_out: dict[str, int] = {}
            earliest: dict[str, int] = {}
            latest: dict[str, int] = {}
            for job_id in self._tasks:
                after = cursor_map.get(job_id, 0)
                stream = self._service_for(job_id)
                if stream is None:
                    cursors_out[job_id] = after
                    continue
                replay = stream.replay(after)
                if replay.reset:
                    return StreamBatch(
                        events=(),
                        cursors={},
                        reset=True,
                        reset_reason=replay.reset_reason or RESET_CURSOR_EXPIRED,
                        earliest_seq={job_id: replay.earliest_seq},
                        latest_seq={job_id: replay.latest_seq},
                    )
                merged.extend(replay.events)
                earliest[job_id] = replay.earliest_seq
                latest[job_id] = replay.latest_seq
                cursors_out[job_id] = replay.cursor.seq if replay.cursor else after

            merged.sort(key=lambda event: (event.emitted_at, event.owner_id, event.seq))
            if limit is not None and limit >= 0 and len(merged) > limit:
                merged = merged[:limit]
                cursors_out = self._acked_cursors(merged, cursor_map)
            return StreamBatch(
                events=tuple(merged),
                cursors=cursors_out,
                earliest_seq=earliest,
                latest_seq=latest,
            )

    def snapshot(self) -> StreamBatch:
        """Full re-read of every subscribed owner (the reset-recovery read)."""
        with self._lock:
            merged: list[EventEnvelope] = []
            cursors_out: dict[str, int] = {}
            for job_id in self._tasks:
                stream = self._service_for(job_id)
                if stream is None:
                    cursors_out[job_id] = 0
                    continue
                events = stream.snapshot()
                merged.extend(events)
                cursors_out[job_id] = events[-1].seq if events else 0
            merged.sort(key=lambda event: (event.emitted_at, event.owner_id, event.seq))
            return StreamBatch(events=tuple(merged), cursors=cursors_out)

    # -- internal ----------------------------------------------------------- #

    def _cursor_map(self, cursors: Sequence[StreamCursor]) -> dict[str, int]:
        resolved: dict[str, int] = {}
        for cursor in cursors:
            if cursor.principal_id != self.principal_id or cursor.scope != self.scope:
                raise StreamScopeMismatchError(
                    "cross-scope stream reuse denied: cursor "
                    f"{cursor.principal_id!r}/{cursor.scope.value} cannot address "
                    f"stream {self.principal_id!r}/{self.scope.value}"
                )
            if cursor.service_epoch and self.check_service_epoch(cursor.service_epoch) is False:
                # A restart is a reset, not an authorization failure.
                return resolved
            resolved[cursor.job_id] = cursor.after_seq
        return resolved

    @staticmethod
    def _acked_cursors(
        events: Sequence[EventEnvelope], cursor_map: Mapping[str, int]
    ) -> dict[str, int]:
        acked = dict(cursor_map)
        for event in events:
            acked[event.owner_id] = event.seq
        return acked

    def _service_for(self, job_id: str) -> PersistentEventService | None:
        log = self._log_for(job_id)
        if log is None:
            return None
        return PersistentEventService(
            log=log,
            owner_type=self.owner_type,
            owner_id=job_id,
            scope=self.scope,
            retention=self.retention,
        )


class AuthorizedStreamRegistry:
    """Reuse registry: at most one live stream per ``(principal_id, scope)``.

    ``open`` refuses a subscription whose authorization check fails (new
    connection denied) and returns the existing instance for a repeated
    ``(principal, scope)`` — task subscriptions are added to that instance, they
    are never granted their own stream.
    """

    def __init__(
        self,
        *,
        owner_type: OwnerType = OwnerType.JOB,
        retention: int | None = None,
    ) -> None:
        self.owner_type = owner_type if isinstance(owner_type, OwnerType) else OwnerType(owner_type)
        self.retention = retention
        self._streams: dict[StreamKey, AuthorizedEventStream] = {}
        self._lock = threading.RLock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for stream in self._streams.values() if not stream.closed)

    @property
    def keys(self) -> tuple[StreamKey, ...]:
        with self._lock:
            return tuple(self._streams)

    def get(self, principal_id: str, scope: EventScope) -> AuthorizedEventStream | None:
        with self._lock:
            return self._streams.get(StreamKey(principal_id=principal_id, scope=scope))

    def open(
        self,
        *,
        principal_id: str,
        scope: EventScope,
        service_epoch: str,
        log_for: Callable[[str], EventLogPort | None],
        job_ids: Sequence[str] = (),
        authorizer: StreamAuthorizer | None = None,
        owner_type: OwnerType | None = None,
        retention: int | None = None,
    ) -> AuthorizedEventStream:
        """Open or join the one stream for ``(principal_id, scope)``.

        Raises:
            StreamAuthorizationError: the subscription is not authorized (new
                connections are refused; no stream remains registered).
        """
        resolved_scope = scope if isinstance(scope, EventScope) else EventScope(scope)
        key = StreamKey(principal_id=str(principal_id), scope=resolved_scope)
        with self._lock:
            existing = self._streams.get(key)
            if (
                existing is not None
                and not existing.closed
                and existing.service_epoch == str(service_epoch)
            ):
                stream = existing
            else:
                if existing is not None:
                    existing.close(RESET_SERVICE_RESTART)
                stream = AuthorizedEventStream(
                    principal_id=str(principal_id),
                    scope=resolved_scope,
                    service_epoch=str(service_epoch),
                    log_for=log_for,
                    owner_type=owner_type or self.owner_type,
                    retention=self.retention if retention is None else retention,
                )
                self._streams[key] = stream

        if job_ids:
            stream.subscribe(job_ids)
        if authorizer is not None and not stream.check_authorization(
            authorizer, job_ids=job_ids or stream.task_ids
        ):
            with self._lock:
                if self._streams.get(key) is stream:
                    del self._streams[key]
            stream.close(RESET_PERMISSION_CHANGED)
            raise StreamAuthorizationError(
                f"stream subscription denied for principal {principal_id!r} "
                f"scope {resolved_scope.value!r}"
            )
        return stream

    def close(self, principal_id: str, scope: EventScope, reason: str = "closed") -> None:
        with self._lock:
            stream = self._streams.pop(StreamKey(principal_id=str(principal_id), scope=scope), None)
        if stream is not None:
            stream.close(reason)

    def close_all(self, reason: str = "closed") -> None:
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.close(reason)
