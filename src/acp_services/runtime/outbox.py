"""Transactional-outbox delivery + idempotent replay (B04-2, todo 23).

This is the Runtime-side *delivery* half of the transactional outbox.  It sits
beside :mod:`acp_services.runtime.events` (the persistent-event protocol) and is
deliberately **owner-neutral**: it never imports ``acp`` and it never writes
state.  The owner repository (the ``JobStore`` SQL layer, ``acp`` side) stages
an :class:`OutboxEventSpec` **inside the same transaction** as the state
``UPDATE`` that produced it; this module only drains committed rows to a durable
:class:`~acp_services.runtime.events.EventLogPort` **after** that commit.

Why an outbox (the lost-fact window)
------------------------------------
The legacy order was "CAS commit, then separately append the event".  A crash
between the two lost the fact forever: the state said one thing and no event
ever recorded it.  The fix is to make the event a *row* written by the same
connection/transaction as the state change:

``state UPDATE`` ─┐
                  ├─ one ``commit()``  →  committed fact **and** outbox row
``outbox INSERT`` ─┘

Delivery is then a separate, retryable, idempotent step:

``commit`` → ``deliver_pending()`` (append to durable JSONL, dedup by stable
``dedup_key``) → mark delivered (the ack) → (optional) notify SSE.

Guarantees
----------
* **never an event for an uncommitted state** — a failed CAS guard or a crash
  before the single commit rolls the outbox row back together with the state;
* **at-least-once delivery, exactly-once effect** — a crash after the durable
  append but before the ack leaves the row pending; the next sweep re-appends
  the *same* stable ``dedup_key`` (the legacy ``JobEventLog`` guard makes that a
  no-op) and then acks;
* **delivery never re-applies state** — this module only appends events, so a
  replay can never change a job row;
* **stable cursor** — the same fact keeps its identity across restarts, so
  ``after_seq`` / cursor replay stays gap-free and duplicate-free.

Author: ACP Services (B lane, todo 23).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.events import (
    EventEnvelope,
    EventScope,
    OwnerType,
)
from acp_services.runtime.events import EventLogPort

__all__ = [
    "EventOutboxStorePort",
    "OutboxDeliveryError",
    "OutboxDeliveryResult",
    "OutboxDeliveryService",
    "OutboxEventSpec",
    "OutboxRecord",
]

logger = logging.getLogger(__name__)

#: JSONL carriers the legacy event log writes around a record.  An outbox
#: payload must never contain one of these keys, otherwise ``**payload`` would
#: silently overwrite the carrier and corrupt the record.
_RESERVED_PAYLOAD_KEYS = frozenset({"type", "timestamp", "idempotency_key", "event_id"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_scope(value: EventScope | str) -> EventScope:
    return value if isinstance(value, EventScope) else EventScope(value)


def _coerce_owner_type(value: OwnerType | str) -> OwnerType:
    return value if isinstance(value, OwnerType) else OwnerType(value)


class OutboxDeliveryError(RuntimeError):
    """The durable event log rejected (or failed to accept) an outbox row."""


@dataclass(frozen=True)
class OutboxEventSpec:
    """A fact to stage inside the owner's state transaction (pre-delivery).

    The owner repository fills in the stable ``event_id`` / ``dedup_key`` and the
    post-transition ``revision`` when the row is inserted, so a caller normally
    supplies only ``event_type`` + ``payload``.  Supplying an explicit
    ``dedup_key`` lets a caller pin the identity across CAS retries (retry loops
    re-read the revision, so the derived key would otherwise move).
    """

    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    scope: EventScope = EventScope.TASK
    owner_type: OwnerType = OwnerType.JOB
    dedup_key: str | None = None
    event_id: str | None = None
    attempt: int | None = None
    emitted_at: str = ""

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("outbox event_type is required")
        object.__setattr__(self, "scope", _coerce_scope(self.scope))
        object.__setattr__(self, "owner_type", _coerce_owner_type(self.owner_type))
        payload = dict(self.payload)
        reserved = sorted(set(payload) & _RESERVED_PAYLOAD_KEYS)
        if reserved:
            raise ValueError(f"outbox payload may not carry reserved key(s): {reserved}")
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True)
class OutboxRecord:
    """One persisted outbox row (committed fact pending/acking delivery)."""

    event_id: str
    owner_id: str
    owner_type: OwnerType
    scope: EventScope
    event_type: str
    payload: Mapping[str, Any]
    dedup_key: str
    revision: int = 0
    attempt: int | None = None
    emitted_at: str = ""
    delivered_at: str | None = None
    delivered_seq: int | None = None
    delivery_attempts: int = 0
    last_error: str = ""

    @property
    def is_delivered(self) -> bool:
        """True once the durable append has been acknowledged."""
        return self.delivered_at is not None

    def to_envelope(self, *, seq: int) -> EventEnvelope:
        """The Envelope this row becomes once delivered at cursor *seq*."""
        return EventEnvelope(
            event_id=self.event_id,
            seq=seq,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            scope=self.scope,
            event_type=self.event_type,
            emitted_at=self.emitted_at or _utc_now_iso(),
            payload=self.payload,
            dedup_key=self.dedup_key,
        )


@runtime_checkable
class EventOutboxStorePort(Protocol):
    """The owner-repository surface the delivery service drains.

    Implemented on the ``acp`` side over ``JobStore`` (the SQL outbox table);
    keeping it a Protocol lets tests inject an in-memory double and lets this
    module stay free of any ``acp`` import.
    """

    def pending_outbox(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> Sequence[OutboxRecord]:
        """Rows committed but not yet acknowledged, oldest first."""
        ...

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        """Ack a delivered row; return True only for the first ack (idempotent)."""
        ...

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        """Record a delivery failure without dropping the row (retryable)."""
        ...


@dataclass(frozen=True)
class OutboxDeliveryResult:
    """Outcome of one delivery sweep."""

    delivered: tuple[EventEnvelope, ...] = ()
    duplicates: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    pending: int = 0

    @property
    def delivered_count(self) -> int:
        return len(self.delivered)

    def __bool__(self) -> bool:
        return bool(self.delivered or self.duplicates or self.failed)


class OutboxDeliveryService:
    """Drain committed outbox rows to durable logs, idempotently.

    ``log_for(owner_id)`` returns the per-owner durable :class:`EventLogPort`
    (the per-task JSONL on the ``acp`` side).  ``notifier`` is the *post-commit*
    SSE wake hook: it is invoked only after the fact is durable and acked, so an
    in-process notification can never outrun the committed fact.
    """

    def __init__(
        self,
        *,
        outbox: EventOutboxStorePort,
        log_for: Callable[[str], EventLogPort],
        notifier: Callable[[EventEnvelope], None] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._outbox = outbox
        self._log_for = log_for
        self._notifier = notifier
        self._clock = clock or _utc_now_iso

    def deliver_pending(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> OutboxDeliveryResult:
        """Deliver every pending row for *job_id* (all owners when ``None``)."""
        records = list(self._outbox.pending_outbox(job_id=job_id, limit=limit))
        delivered: list[EventEnvelope] = []
        duplicates: list[str] = []
        failed: list[tuple[str, str]] = []
        for record in records:
            try:
                envelope, duplicate = self.deliver_one(record)
            except (OSError, OutboxDeliveryError) as exc:
                self._outbox.mark_outbox_failed(record.event_id, error=str(exc))
                failed.append((record.event_id, str(exc)))
                continue
            if duplicate:
                duplicates.append(record.event_id)
            else:
                delivered.append(envelope)
        pending = len(self._outbox.pending_outbox(job_id=job_id))
        return OutboxDeliveryResult(
            delivered=tuple(delivered),
            duplicates=tuple(duplicates),
            failed=tuple(failed),
            pending=pending,
        )

    def deliver_one(self, record: OutboxRecord) -> tuple[EventEnvelope, bool]:
        """Deliver one row; returns ``(envelope, was_duplicate)``.

        The append carries the stable ``dedup_key`` (and ``event_id``) so a
        crash-before-ack replay appends nothing new; the row is acked only after
        the append returns, making the effect exactly-once even though the
        attempt is at-least-once.
        """
        log = self._log_for(record.owner_id)
        try:
            before = log.count()
            log.append(
                record.event_type,
                idempotency_key=record.dedup_key,
                event_id=record.event_id,
                **dict(record.payload),
            )
            after = log.count()
        except OSError as exc:
            raise OutboxDeliveryError(f"event log append failed: {exc}") from exc

        duplicate = after == before
        seq = after if after >= 1 else (record.delivered_seq or 1)
        envelope = record.to_envelope(seq=seq)
        self._outbox.mark_outbox_delivered(record.event_id, seq=seq, delivered_at=self._clock())
        if self._notifier is not None:
            try:
                self._notifier(envelope)
            except Exception:  # noqa: BLE001 - notify is best-effort, already durable
                logger.debug("outbox notifier failed for %s", record.event_id, exc_info=True)
        return envelope, duplicate

    def undelivered_count(self, *, job_id: str | None = None) -> int:
        """How many committed rows are still awaiting delivery."""
        return len(self._outbox.pending_outbox(job_id=job_id))
