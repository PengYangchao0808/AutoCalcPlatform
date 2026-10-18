"""Legacy-side composition root for the persistent event protocol (B04-1).

The Services-side event protocol (:mod:`acp_services.runtime.events`) must not
import ``acp``, so the adapter that delegates to the *existing* per-task JSONL
log (``acp.scheduler.events.JobEventLog``) lives here, on the ``acp`` side of
the one-way dependency edge (``acp → acp_services`` is allowed).

This is a **seam, not a switch**: nothing in the legacy SSE/`after_seq` path is
rerouted here.  The old ``GET /api/jobs/{id}/events`` endpoint and its v1 alias
keep using ``JobEventLog`` directly and are behaviourally unchanged; this module
only exposes the same durable log to the new Services protocol so later slices
(todo 23 outbox delivery, todo 24 authorized stream) can consume it.

The event file format is untouched: the adapter writes through
``JobEventLog.append`` (which still writes ``type`` / ``timestamp`` and the
``idempotency_key`` guard), and old event records without ``schema_version``
remain readable (migrated on read by the Services protocol).

Author: ACP Services (B lane, todo 22).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from acp.scheduler.events import JobEventLog
from acp.scheduler.store import JobStore
from acp_services.contracts.events import EventEnvelope, EventScope, OwnerType
from acp_services.runtime.events import PersistentEventService
from acp_services.runtime.outbox import (
    OutboxDeliveryService,
    OutboxRecord,
)

__all__ = [
    "JobEventLogPort",
    "JobStoreOutboxPort",
    "open_outbox_delivery",
    "open_persistent_event_service",
    "open_persistent_event_service_for_path",
]


class JobEventLogPort:
    """Adapt :class:`~acp.scheduler.events.JobEventLog` to ``EventLogPort``.

    A pass-through of the durable JSONL surface (``append`` with the
    ``idempotency_key`` dedup guard, ``count``, ``read_all``, ``read_last``);
    it adds no caching and no second write path.
    """

    __slots__ = ("_log",)

    def __init__(self, log: JobEventLog) -> None:
        self._log = log

    @property
    def path(self) -> Path:
        """The underlying ``events.jsonl`` path (for diagnostics/tests)."""
        return self._log.path

    def append(self, event_type: str, *, idempotency_key: str | None = None, **data: Any) -> None:
        self._log.append(event_type, idempotency_key=idempotency_key, **data)

    def count(self) -> int:
        return self._log.count()

    def read_all(self) -> list[dict[str, Any]]:
        return self._log.read_all()

    def read_last(self) -> dict[str, Any] | None:
        return self._log.read_last()


def open_persistent_event_service(
    log: JobEventLog,
    *,
    owner_type: OwnerType,
    owner_id: str,
    scope: EventScope,
    retention: int | None = None,
) -> PersistentEventService:
    """Wrap an existing ``JobEventLog`` in the Services event protocol."""
    return PersistentEventService(
        log=JobEventLogPort(log),
        owner_type=owner_type,
        owner_id=owner_id,
        scope=scope,
        retention=retention,
    )


def open_persistent_event_service_for_path(
    path: Path | str,
    *,
    owner_type: OwnerType,
    owner_id: str,
    scope: EventScope,
    retention: int | None = None,
) -> PersistentEventService:
    """Open (creating the parent dir) the ``events.jsonl`` at *path*."""
    return open_persistent_event_service(
        JobEventLog(path),
        owner_type=owner_type,
        owner_id=owner_id,
        scope=scope,
        retention=retention,
    )


class JobStoreOutboxPort:
    """Adapt :class:`JobStore`'s SQL outbox to ``EventOutboxStorePort``.

    A pass-through of the owner-repository transaction boundary: the rows were
    written in the same commit as the state change; this adapter only lets the
    Services delivery service read and acknowledge them.
    """

    __slots__ = ("_store",)

    def __init__(self, store: JobStore) -> None:
        self._store = store

    def pending_outbox(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> list[OutboxRecord]:
        return self._store.pending_outbox(job_id=job_id, limit=limit)

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        return self._store.mark_outbox_delivered(event_id, seq=seq, delivered_at=delivered_at)

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        self._store.mark_outbox_failed(event_id, error=error)


def open_outbox_delivery(
    store: JobStore,
    *,
    path_for: Callable[[str], Path | str],
    notifier: Callable[[EventEnvelope], None] | None = None,
) -> OutboxDeliveryService:
    """Wire the Services outbox delivery onto the legacy ``JobStore`` + JSONL.

    ``path_for(job_id) -> Path`` resolves the per-task ``events.jsonl``;
    ``notifier`` is the post-commit SSE wake hook (called only after the fact is
    durable).  The delivery writes the legacy-compatible record shape (``type`` /
    ``idempotency_key`` / ``event_id``) so the old ``after_seq`` SSE reader keeps
    working unchanged, while :class:`PersistentEventService` migrates the same
    records into envelopes on read.
    """

    def _log_for(owner_id: str):  # noqa: ANN202 - local closure
        return JobEventLogPort(JobEventLog(path_for(owner_id)))

    return OutboxDeliveryService(
        outbox=JobStoreOutboxPort(store),
        log_for=_log_for,
        notifier=notifier,
    )
