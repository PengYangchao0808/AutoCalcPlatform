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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
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
    "EventLogError",
    "EventLogPort",
    "EventOutboxPort",
    "EventReplay",
    "PersistentEventService",
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
