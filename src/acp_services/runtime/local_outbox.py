"""Per-database transactional outbox — the M2-01b same-transaction write API (G02/G14).

A research fact crosses **four** SQLite authorities: the jobs database
(``job_outbox``, B04-2), the Data research-resource database
(``research_resources``), the admission ledger database
(``research_budget_events``) and this slice's coordination database (saga
intents).  There is deliberately **no** distributed transaction and **no** 2PC
between them.  What each database can promise on its own is narrower and is the
only promise anything here makes:

``business write`` ─┐
                   ├─ one ``COMMIT``  →  committed fact **and** its outbox row
``outbox INSERT`` ─┘

Delivery is a **separate, retryable, idempotent** step:
:class:`acp_services.runtime.outbox.OutboxDeliveryService` drains only
*committed* rows and appends them to a durable log under a stable
``dedup_key``; it is never asked to make two databases atomic and it never
re-applies a state change.  A crash before the single ``COMMIT`` loses the
outbox row *together with* the business write (never an event for an
uncommitted fact); a crash after the append but before the ack re-appends the
same ``dedup_key`` (the log guard makes that a no-op) and then acks, so the
effect is exactly-once even though delivery is at-least-once.

Coverage (frozen, machine-checked by ``tests/test_acp2_research_saga.py``):

============================  ==============  ==================================
database                      same-transaction entry
============================  ==============  ==================================
``jobs``                      yes (existing)  ``JobStore.transition(outbox=...)``
``research``                  yes (M2-01b)    ``ResearchResourceStore.transaction()``
                                              + :func:`stage_research_outbox`
``admission``                 yes (existing)  ``ReservationLedger`` reserve/settle
                                              (business row + ``research_budget_events``
                                              in one ``BEGIN IMMEDIATE``)
``coordination``              yes (M2-01b)    :class:`SqliteLocalOutbox.write`
============================  ==============  ==================================

Isolation: stdlib plus first-party ``acp_services``; ``acp`` is never imported.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from acp_services.contracts.events import EventScope, OwnerType
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.runtime.outbox import OutboxEventSpec, OutboxRecord

__all__ = [
    "LOCAL_OUTBOX_COVERAGE",
    "LOCAL_OUTBOX_SCHEMA",
    "LOCAL_OUTBOX_TABLE",
    "LocalOutboxError",
    "LocalOutboxTable",
    "OutboxCoverage",
    "SqliteLocalOutbox",
    "ensure_research_outbox",
    "research_outbox",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Wire schema id of the local outbox row (diagnostic; the row is private).
LOCAL_OUTBOX_SCHEMA = "acp2.runtime.local-outbox.v1"
#: The table every database uses for its own committed-but-undelivered rows.
LOCAL_OUTBOX_TABLE = "local_outbox"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {LOCAL_OUTBOX_TABLE} (
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedup_key TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER,
    emitted_at TEXT NOT NULL,
    delivered_at TEXT,
    delivered_seq INTEGER,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
)
"""

_CREATE_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS idx_local_outbox_pending ON {LOCAL_OUTBOX_TABLE}(delivered_at)"
)


class LocalOutboxError(RuntimeError):
    """The local outbox row could not be staged or acknowledged."""


# ---------------------------------------------------------------------------
# Coverage table (the frozen "which database promises what" answer)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutboxCoverage:
    """One database's same-transaction outbox entry point.

    Attributes:
        database: Stable key of the authority (``jobs``/``research``/
            ``admission``/``coordination``).
        same_transaction: Whether the business write and the outbox row commit
            or roll back together on that database.  Always ``True`` here: a
            ``False`` row would mean an event can exist for an uncommitted fact.
        authority: The owning store/module that owns the transaction.
        entry: The exact call a caller must use.
        note: What the row does *not* claim (no cross-database atomicity).
    """

    database: str
    same_transaction: bool
    authority: str
    entry: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "same_transaction": self.same_transaction,
            "authority": self.authority,
            "entry": self.entry,
            "note": self.note,
        }


#: The frozen coverage of the four databases one research fact crosses.  A
#: missing row is a contract error, never "no outbox needed".
LOCAL_OUTBOX_COVERAGE: Mapping[str, OutboxCoverage] = {
    "jobs": OutboxCoverage(
        database="jobs",
        same_transaction=True,
        authority="acp_services.store.store.JobStore",
        entry="JobStore.transition(..., outbox=OutboxEventSpec(...))",
        note=(
            "B04-2 job_outbox: the row is inserted on the same connection before "
            "the single commit as the state UPDATE"
        ),
    ),
    "research": OutboxCoverage(
        database="research",
        same_transaction=True,
        authority="acp_services.data.research_resources.ResearchResourceStore",
        entry="ResearchResourceStore.transaction() + stage_research_outbox(...)",
        note="M2-01b: the resource revision and the outbox row share one commit",
    ),
    "admission": OutboxCoverage(
        database="admission",
        same_transaction=True,
        authority="acp_services.runtime.admission.ReservationLedger",
        entry="ReservationLedger reserve/settle (business row + research_budget_events)",
        note="existing policy authority: M2-01b adds no second budget write path",
    ),
    "coordination": OutboxCoverage(
        database="coordination",
        same_transaction=True,
        authority="acp_services.runtime.local_outbox.SqliteLocalOutbox",
        entry="SqliteLocalOutbox.write(business, events=..., owner_id=...)",
        note="saga intents + their events (M2-01b)",
    ),
}


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _coerce_scope(value: EventScope | str) -> EventScope:
    return value if isinstance(value, EventScope) else EventScope(value)


def _coerce_owner(value: OwnerType | str) -> OwnerType:
    return value if isinstance(value, OwnerType) else OwnerType(value)


def _canonical_payload(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:  # pragma: no cover - caller bug
        raise LocalOutboxError(f"outbox payload is not canonical JSON: {exc}") from exc


def _stable_key(owner_id: str, spec: OutboxEventSpec) -> tuple[str, str]:
    """Return ``(event_id, dedup_key)`` for one staged spec (deterministic)."""
    digest = hashlib.sha256(
        "\0".join(
            [
                str(owner_id),
                str(spec.event_type),
                _canonical_payload(spec.payload),
                str(spec.attempt if spec.attempt is not None else ""),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return ("ox_" + digest[:32], "od_" + digest[:40])


def _row_to_record(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        event_id=str(row["event_id"]),
        owner_id=str(row["owner_id"]),
        owner_type=_coerce_owner(row["owner_type"]),
        scope=_coerce_scope(row["scope"]),
        event_type=str(row["event_type"]),
        payload=json.loads(row["payload_json"]),
        dedup_key=str(row["dedup_key"]),
        revision=int(row["revision"] or 0),
        attempt=None if row["attempt"] is None else int(row["attempt"]),
        emitted_at=str(row["emitted_at"] or ""),
        delivered_at=row["delivered_at"],
        delivered_seq=None if row["delivered_seq"] is None else int(row["delivered_seq"]),
        delivery_attempts=int(row["delivery_attempts"] or 0),
        last_error=str(row["last_error"] or ""),
    )


# ---------------------------------------------------------------------------
# The table (works on any connection)
# ---------------------------------------------------------------------------


class LocalOutboxTable:
    """The ``local_outbox`` table: stage / read / ack on a caller's connection.

    Every method takes the connection it must use.  Staging is therefore always
    inside whatever transaction the caller already opened, which is the whole
    point: the outbox row and the business row share one ``COMMIT``.
    """

    @staticmethod
    def ensure(conn: sqlite3.Connection) -> None:
        """Create the table/index idempotently (safe inside a transaction)."""
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)

    @staticmethod
    def stage(
        conn: sqlite3.Connection,
        spec: OutboxEventSpec,
        *,
        owner_id: str,
        revision: int = 0,
        event_id: str | None = None,
        dedup_key: str | None = None,
        emitted_at: str = "",
    ) -> str:
        """Insert one row on *conn*; a duplicate ``dedup_key`` is a no-op.

        Returns:
            The stable ``event_id`` of the row (the same value on a retry).
        """
        owner = str(owner_id)
        if not owner:
            raise LocalOutboxError("an outbox row requires an owner id")
        stable_id, stable_key = _stable_key(owner, spec)
        resolved_id = str(event_id or spec.event_id or stable_id)
        resolved_key = str(dedup_key or spec.dedup_key or stable_key)
        conn.execute(
            f"INSERT OR IGNORE INTO {LOCAL_OUTBOX_TABLE} "
            "(event_id, owner_id, owner_type, scope, event_type, payload_json, "
            " dedup_key, revision, attempt, emitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resolved_id,
                owner,
                _coerce_owner(spec.owner_type).value,
                _coerce_scope(spec.scope).value,
                str(spec.event_type),
                _canonical_payload(spec.payload),
                resolved_key,
                int(revision),
                None if spec.attempt is None else int(spec.attempt),
                str(spec.emitted_at or emitted_at or _utc_now_iso()),
            ),
        )
        return resolved_id

    @staticmethod
    def pending(
        conn: sqlite3.Connection,
        *,
        owner_id: str | None = None,
        limit: int | None = None,
    ) -> list[OutboxRecord]:
        """Committed-but-undelivered rows, oldest first."""
        clauses = ["delivered_at IS NULL"]
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(str(owner_id))
        sql = f"SELECT * FROM {LOCAL_OUTBOX_TABLE} WHERE {' AND '.join(clauses)} ORDER BY rowid"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_row_to_record(row) for row in conn.execute(sql, params).fetchall()]

    @staticmethod
    def ack(conn: sqlite3.Connection, event_id: str, *, seq: int, delivered_at: str) -> bool:
        """Acknowledge one delivered row; True only for the first ack."""
        cursor = conn.execute(
            f"UPDATE {LOCAL_OUTBOX_TABLE} SET delivered_at=?, delivered_seq=?, "
            "last_error=NULL WHERE event_id=? AND delivered_at IS NULL",
            (str(delivered_at), int(seq), str(event_id)),
        )
        return bool(cursor.rowcount)

    @staticmethod
    def mark_failed(conn: sqlite3.Connection, event_id: str, *, error: str) -> None:
        """Record a delivery failure without dropping the row (retryable)."""
        conn.execute(
            f"UPDATE {LOCAL_OUTBOX_TABLE} SET delivery_attempts=delivery_attempts+1, "
            "last_error=? WHERE event_id=?",
            (str(error), str(event_id)),
        )

    @staticmethod
    def undelivered(conn: sqlite3.Connection, *, owner_id: str | None = None) -> int:
        clause = "" if owner_id is None else " AND owner_id = ?"
        params: tuple[Any, ...] = () if owner_id is None else (str(owner_id),)
        row = conn.execute(
            f"SELECT COUNT(*) FROM {LOCAL_OUTBOX_TABLE} WHERE delivered_at IS NULL{clause}",
            params,
        ).fetchone()
        return int(row[0] if row else 0)


# ---------------------------------------------------------------------------
# A whole database as an outbox (coordination database + delivery half)
# ---------------------------------------------------------------------------


class SqliteLocalOutbox:
    """One database's outbox, for both staging and delivery.

    ``write`` opens one ``BEGIN IMMEDIATE`` on its own connection, runs the
    caller's business callable on that same connection, stages the event rows and
    commits once.  Any exception (including a business refusal) rolls the whole
    transaction back, so a failed write never leaves an event behind.

    The delivery half implements
    :class:`~acp_services.runtime.outbox.EventOutboxStorePort`, so the existing
    :class:`~acp_services.runtime.outbox.OutboxDeliveryService` drains it
    unchanged — this class never appends to a log and never re-applies a fact.
    """

    def __init__(self, db_path: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now_iso
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            LocalOutboxTable.ensure(self._conn)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    def __enter__(self) -> SqliteLocalOutbox:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- staging -----------------------------------------------------------

    def write(
        self,
        business: Callable[[sqlite3.Connection], T],
        *,
        events: Sequence[OutboxEventSpec],
        owner_id: str,
        revision: int = 0,
    ) -> T:
        """Run *business* and stage *events* in one transaction.

        Args:
            business: Callable receiving this database's connection; every
                statement it runs is part of the same transaction.
            events: Facts to stage for the *committed* business result.
            owner_id: Stable owner of the facts (saga id / resource id).
            revision: Post-write revision recorded on the events (diagnostic).

        Returns:
            Whatever *business* returned, after the single commit.

        Raises:
            Exception: whatever *business* raised, after rolling everything back.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = business(self._conn)
                for spec in events:
                    LocalOutboxTable.stage(
                        self._conn,
                        spec,
                        owner_id=owner_id,
                        revision=revision,
                        emitted_at=self._clock(),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            return result

    # -- delivery half (EventOutboxStorePort) ------------------------------

    def pending_outbox(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> Sequence[OutboxRecord]:
        """Rows committed but not yet acknowledged, oldest first.

        ``job_id`` is the generic owner filter of the port (the coordination
        database uses a saga id there); ``None`` drains every owner.
        """
        with self._lock:
            return LocalOutboxTable.pending(self._conn, owner_id=job_id, limit=limit)

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                acked = LocalOutboxTable.ack(
                    self._conn, event_id, seq=seq, delivered_at=delivered_at
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            return acked

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                LocalOutboxTable.mark_failed(self._conn, event_id, error=error)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def undelivered_count(self, *, owner_id: str | None = None) -> int:
        with self._lock:
            return LocalOutboxTable.undelivered(self._conn, owner_id=owner_id)


# ---------------------------------------------------------------------------
# Research database adapter (business write + outbox row in one commit)
# ---------------------------------------------------------------------------


def ensure_research_outbox(store: Any) -> None:
    """Create the research database's ``local_outbox`` table idempotently.

    Called once when the writer is built so the table never depends on a
    successful business write (a rolled-back first write must still leave a
    readable, empty outbox rather than a missing table).
    """
    transaction = getattr(store, "transaction", None)
    if transaction is None:
        raise LocalOutboxError("the research store exposes no transaction seam")
    with transaction() as conn:
        LocalOutboxTable.ensure(conn)


def research_outbox(
    store: Any,
    *,
    events: Sequence[OutboxEventSpec],
    owner_id: str,
    business: Callable[[sqlite3.Connection], T],
    revision: int = 0,
) -> T:
    """Run a research-resource write and stage its events in one transaction.

    Args:
        store: A
            :class:`~acp_services.data.research_resources.ResearchResourceStore`.
            Only its single-writer transaction seam is used, so the caller's
            business callable can be one of its own ``*_in_transaction`` methods.
        events: Facts of the committed revision.
        owner_id: Stable owner of the facts (the resource id or a saga id).
        business: Callable receiving the store's connection.
        revision: Revision recorded on the staged events.

    Returns:
        Whatever *business* returned, after the single commit.
    """
    transaction = getattr(store, "transaction", None)
    if transaction is None:
        raise LocalOutboxError(
            "the research store exposes no transaction seam; refusing to write "
            "an outbox row outside the business transaction"
        )
    # Idempotent DDL in its own transaction: a rolled-back business write must
    # still leave a readable (empty) outbox rather than a missing table.
    ensure_research_outbox(store)
    with transaction() as conn:
        LocalOutboxTable.ensure(conn)
        result = business(conn)
        for spec in events:
            LocalOutboxTable.stage(
                conn,
                spec,
                owner_id=str(owner_id),
                revision=revision,
                emitted_at=_utc_now_iso(),
            )
        return result
