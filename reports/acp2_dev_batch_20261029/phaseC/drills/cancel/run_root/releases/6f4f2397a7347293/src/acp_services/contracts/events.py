"""Versioned persistent event envelope — neutral shared contract (B04-1).

This module is the **shared, owner-neutral** event contract for the ACP
Services cut-over.  It is deliberately placed under
:mod:`acp_services.contracts` (not ``acp_services.runtime``) so that the
Workspace and Data owners can publish their own facts *without importing a
Runtime private module*: the publisher supplies an :class:`OwnerType` /
:class:`EventScope` and builds an :class:`EventEnvelope`; the wire shape is
defined here and nowhere else.

Authority
---------
The field set is frozen in
``docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`` §3.1
(fields) and §3.2 (semantics), with tracked samples at
``tests/baseline/services-evidence/contract_samples/events_*.json``.  The plan
prose (``.omo/plans/acp-parallel-plan-2-services.md`` todo 22) uses a looser
vocabulary ("owner = Runtime/Data/Workspace", "scope = task/project/
availability"); per the B00-3 frozen contract that prose must **not** fork the
wire values, so this module keeps the frozen wire vocabulary
(``owner_type`` ∈ job/task/project/data, ``scope``/``coverage`` ∈
task/project/data) and exposes the publishing *component* as a separate,
non-wire :class:`EventOwner` helper (``owner_type`` → component is documented
in :data:`OWNER_COMPONENT_BY_TYPE`).  An envelope therefore carries exactly the
twelve frozen fields on the wire; the component is derived, never serialized.

Dependency direction (allowlist ``public_allowlist.md`` / isolation gate
``tests/test_acp_services_isolation.py``): this module imports **stdlib only**
and never ``acp`` or ``cccp``.

Semantics implemented here (rule → executable guard)
----------------------------------------------------
* **cursor monotonic** — a per-owner ``seq`` must strictly increase; a
  regression is *corrupt* (:class:`EventStreamCorruptError`), never silently
  reordered (``validate_event_sequence``).
* **cursor ⇔ seq** — ``cursor`` is the string form of ``seq`` (``after_seq``
  compatibility); a mismatch is rejected (:class:`EventCursorError`).
* **dedup** — ``event_id`` is the stable identity and ``dedup_key`` the replay
  dedup key; both are frozen fields.
* **old-version migration** — a record that carries only ``after_seq`` /
  ``type`` (no ``schema_version``) is migrated by
  :meth:`EventEnvelope.from_legacy_record` (fills owner/scope, derives
  id/cursor/dedup).
* Events are a *replayable projection*: they must never replace CAS state.
  This module therefore has no state at all — it only checks shape.

Author: ACP Services (B lane, todo 22).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

__all__ = [
    "CURRENT_EVENT_SCHEMA_VERSION",
    "EVENT_ENVELOPE_FIELDS",
    "LEGACY_EVENT_SCHEMA_VERSION",
    "OWNER_COMPONENT_BY_TYPE",
    "EventContractError",
    "EventCursor",
    "EventCursorError",
    "EventEnvelope",
    "EventOwner",
    "EventScope",
    "EventSerializationError",
    "EventStreamCorruptError",
    "EventVersionError",
    "OwnerType",
    "migrate_legacy_event",
    "validate_event_sequence",
]

# ── versions ────────────────────────────────────────────────────────────────

#: Current explicit wire version of an :class:`EventEnvelope` (sample: normal=1).
CURRENT_EVENT_SCHEMA_VERSION: Final = 1
#: Pre-envelope legacy record (only ``after_seq`` / ``type``); migrated on read.
LEGACY_EVENT_SCHEMA_VERSION: Final = 0

#: The twelve frozen fields, in wire order (contract doc §3.1).
EVENT_ENVELOPE_FIELDS: Final[tuple[str, ...]] = (
    "event_id",
    "seq",
    "schema_version",
    "owner_type",
    "owner_id",
    "scope",
    "event_type",
    "cursor",
    "emitted_at",
    "payload",
    "dedup_key",
    "coverage",
)

_RESERVED_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "event_id",
        "seq",
        "schema_version",
        "owner_type",
        "owner_id",
        "scope",
        "event_type",
        "cursor",
        "emitted_at",
        "payload",
        "dedup_key",
        "coverage",
        # JSONL carriers the legacy log writes around the envelope.
        "type",
        "timestamp",
        "idempotency_key",
    }
)


# ── enums ───────────────────────────────────────────────────────────────────


class OwnerType(str, Enum):
    """Wire ``owner_type`` enum (contract doc §3.1): job/task/project/data."""

    JOB = "job"
    TASK = "task"
    PROJECT = "project"
    DATA = "data"


class EventScope(str, Enum):
    """Wire ``scope`` / ``coverage`` enum (contract doc §3.1)."""

    TASK = "task"
    PROJECT = "project"
    DATA = "data"


class EventOwner(str, Enum):
    """Non-wire *publishing component* of an owner type (plan prose vocabulary).

    The plan names the event owners "Runtime / Data / Workspace"; the frozen
    wire names the resource kind (job/task/project/data).  This enum is the
    bridge so each component publishes its own facts through the neutral
    contract without importing a Runtime private module.
    """

    RUNTIME = "runtime"
    DATA = "data"
    WORKSPACE = "workspace"


#: owner_type → publishing component (Runtime owns job/task facts, Workspace
#: owns project facts, Data owns data facts).  Not a wire value.
OWNER_COMPONENT_BY_TYPE: Final[dict[OwnerType, EventOwner]] = {
    OwnerType.JOB: EventOwner.RUNTIME,
    OwnerType.TASK: EventOwner.RUNTIME,
    OwnerType.PROJECT: EventOwner.WORKSPACE,
    OwnerType.DATA: EventOwner.DATA,
}


# ── errors ──────────────────────────────────────────────────────────────────


class EventContractError(Exception):
    """Base class for event-contract violations."""


class EventSerializationError(EventContractError):
    """An event payload cannot be (de)serialized per the frozen contract."""


class EventVersionError(EventSerializationError):
    """An unsupported/future event schema version was requested."""


class EventStreamCorruptError(EventContractError):
    """A stream's per-owner ``seq``/``cursor`` regressed (corrupt, rejected)."""


class EventCursorError(EventContractError):
    """A cursor does not correspond to its ``seq`` / is out of range."""


# ── coercion helpers ────────────────────────────────────────────────────────


def _as_str(value: object, *, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise EventSerializationError(f"{name} must be a str, got {type(value).__name__}")
    return value


def _as_int(value: object, *, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventSerializationError(f"{name} must be an int, got {type(value).__name__}")
    return int(value)


def _as_scope(value: object, *, name: str) -> EventScope:
    try:
        return EventScope(value) if not isinstance(value, EventScope) else value
    except ValueError as exc:
        raise EventSerializationError(
            f"{name} {value!r} not in {sorted(s.value for s in EventScope)}"
        ) from exc


def _as_owner_type(value: object, *, name: str) -> OwnerType:
    try:
        return OwnerType(value) if not isinstance(value, OwnerType) else value
    except ValueError as exc:
        raise EventSerializationError(
            f"{name} {value!r} not in {sorted(t.value for t in OwnerType)}"
        ) from exc


def _as_payload(value: object, *, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EventSerializationError(f"{name} must be a mapping")
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise EventSerializationError(f"{name} keys must be str")
        out[key] = item
    # Round-trip guard: payload must have a JSON wire form (no runtime state).
    try:
        json.dumps(out, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise EventSerializationError(f"{name} is not JSON-serializable: {exc}") from exc
    return out


# ── cursor ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventCursor:
    """A persistent per-owner cursor: ``(owner_type, owner_id, seq)``.

    The wire form is the bare string ``str(seq)`` (contract doc §3.1: "cursor ...
    与 seq 对应"), which is exactly the ``after_seq`` integer the legacy SSE
    endpoint already understands.  The owner tuple is carried alongside so a
    cursor can be validated against the stream it addresses.
    """

    owner_type: OwnerType
    owner_id: str
    seq: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_type", _as_owner_type(self.owner_type, name="owner_type"))
        object.__setattr__(self, "owner_id", _as_str(self.owner_id, name="owner_id"))
        object.__setattr__(self, "seq", _as_int(self.seq, name="seq", default=0))
        if self.seq < 0:
            raise EventCursorError(f"cursor seq must be >= 0, got {self.seq}")

    @property
    def value(self) -> str:
        """Wire cursor string."""
        return str(self.seq)

    @classmethod
    def initial(cls, owner_type: OwnerType, owner_id: str) -> EventCursor:
        """Cursor before any event (seq 0)."""
        return cls(owner_type=owner_type, owner_id=owner_id, seq=0)

    @classmethod
    def from_wire(cls, value: str, *, owner_type: OwnerType, owner_id: str) -> EventCursor:
        """Parse the wire cursor string back into a cursor."""
        try:
            seq = int(value)
        except (TypeError, ValueError) as exc:
            raise EventCursorError(f"cursor {value!r} is not an integer string") from exc
        return cls(owner_type=owner_type, owner_id=owner_id, seq=seq)

    def advance(self, steps: int = 1) -> EventCursor:
        """Return a cursor advanced by *steps* (default 1)."""
        if steps < 0:
            raise EventCursorError(f"cursor cannot advance backwards ({steps})")
        return EventCursor(owner_type=self.owner_type, owner_id=self.owner_id, seq=self.seq + steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_type": self.owner_type.value,
            "owner_id": self.owner_id,
            "cursor": self.value,
        }


# ── envelope ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventEnvelope:
    """One versioned persistent event (frozen 12-field wire contract).

    ``schema_version`` defaults to the current explicit version; ``cursor``
    defaults to ``str(seq)``; ``dedup_key`` defaults to ``event_id``; and
    ``coverage`` defaults to ``scope`` (a caller may set a *different*
    coverage, in which case :attr:`is_consumable` is ``False`` and the event
    must not be consumed, per contract doc §3.2 "覆盖范围").
    """

    event_id: str
    seq: int
    owner_type: OwnerType
    owner_id: str
    scope: EventScope
    event_type: str
    schema_version: int = CURRENT_EVENT_SCHEMA_VERSION
    cursor: str = ""
    emitted_at: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    dedup_key: str = ""
    coverage: EventScope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _as_str(self.event_id, name="event_id"))
        object.__setattr__(self, "seq", _as_int(self.seq, name="seq", default=0))
        object.__setattr__(
            self, "schema_version", _as_int(self.schema_version, name="schema_version", default=0)
        )
        object.__setattr__(self, "owner_type", _as_owner_type(self.owner_type, name="owner_type"))
        object.__setattr__(self, "owner_id", _as_str(self.owner_id, name="owner_id"))
        object.__setattr__(self, "scope", _as_scope(self.scope, name="scope"))
        object.__setattr__(self, "event_type", _as_str(self.event_type, name="event_type"))
        object.__setattr__(self, "emitted_at", _as_str(self.emitted_at, name="emitted_at"))
        object.__setattr__(self, "payload", _as_payload(self.payload, name="payload"))

        if not self.event_id:
            raise EventSerializationError("event_id is required (stable identity)")
        if self.seq < 1:
            raise EventSerializationError(f"seq must be >= 1, got {self.seq}")
        if not self.owner_id:
            raise EventSerializationError("owner_id is required")
        if not self.event_type:
            raise EventSerializationError("event_type is required")

        dedup = self.dedup_key or self.event_id
        object.__setattr__(self, "dedup_key", _as_str(dedup, name="dedup_key"))

        coverage = self.coverage if self.coverage is not None else self.scope
        object.__setattr__(self, "coverage", _as_scope(coverage, name="coverage"))

        cursor = self.cursor or str(self.seq)
        if not cursor.lstrip("-").isdigit() or int(cursor) != self.seq:
            raise EventCursorError(
                f"cursor {cursor!r} does not correspond to seq {self.seq} (must be {self.seq!r})"
            )
        object.__setattr__(self, "cursor", cursor)

    def __hash__(self) -> int:
        # payload is a mutable mapping; identity is (owner, seq, event_id).
        return hash((self.owner_type, self.owner_id, self.seq, self.event_id))

    @property
    def publishing_component(self) -> EventOwner:
        """The non-wire component that owns this event's ``owner_type``."""
        return OWNER_COMPONENT_BY_TYPE[self.owner_type]

    @property
    def is_consumable(self) -> bool:
        """True when ``coverage`` matches ``scope`` (contract doc §3.2)."""
        return self.coverage == self.scope

    @property
    def cursor_value(self) -> EventCursor:
        """The cursor object addressing *this* event (seq already consumed)."""
        return EventCursor(owner_type=self.owner_type, owner_id=self.owner_id, seq=self.seq)

    # -- wire form --------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen twelve-field wire dict in canonical order."""
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "schema_version": self.schema_version,
            "owner_type": self.owner_type.value,
            "owner_id": self.owner_id,
            "scope": self.scope.value,
            "event_type": self.event_type,
            "cursor": self.cursor,
            "emitted_at": self.emitted_at,
            "payload": dict(self.payload),
            "dedup_key": self.dedup_key,
            "coverage": (self.coverage or self.scope).value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EventEnvelope:
        """Strictly decode one envelope (unknown fields rejected).

        Legacy implicit records (no ``schema_version``) are migrated via
        :func:`migrate_legacy_event`, using the record's own ``type`` /
        ``after_seq`` if present.  JSONL carriers (``type`` / ``timestamp`` /
        ``idempotency_key``) are accepted as aliases so the durable per-task log
        can hold the envelope without a separate file.
        """
        if not isinstance(payload, Mapping):
            raise EventSerializationError("event payload must be a mapping")
        raw = dict(payload)
        unknown = sorted(set(raw) - _RESERVED_RECORD_KEYS)
        if unknown:
            raise EventSerializationError(f"event: unknown field(s) {unknown!r}")

        if raw.get("schema_version") is None or "seq" not in raw:
            return migrate_legacy_event(raw)

        version = _as_int(raw.get("schema_version"), name="schema_version", default=0)
        if version > CURRENT_EVENT_SCHEMA_VERSION:
            raise EventVersionError(
                f"schema_version {version} is newer than supported {CURRENT_EVENT_SCHEMA_VERSION}"
            )
        return cls(
            event_id=_as_str(raw.get("event_id"), name="event_id"),
            seq=_as_int(raw.get("seq"), name="seq", default=0),
            schema_version=version,
            owner_type=_as_owner_type(raw.get("owner_type"), name="owner_type"),
            owner_id=_as_str(raw.get("owner_id"), name="owner_id"),
            scope=_as_scope(raw.get("scope"), name="scope"),
            event_type=_as_str(raw.get("event_type", raw.get("type")), name="event_type"),
            cursor=_as_str(raw.get("cursor"), name="cursor"),
            emitted_at=_as_str(raw.get("emitted_at", raw.get("timestamp")), name="emitted_at"),
            payload=_as_payload(raw.get("payload"), name="payload"),
            dedup_key=_as_str(raw.get("dedup_key", raw.get("idempotency_key")), name="dedup_key"),
            coverage=(
                _as_scope(raw.get("coverage"), name="coverage")
                if raw.get("coverage") is not None
                else None
            ),
        )

    @classmethod
    def from_legacy_record(
        cls,
        record: Mapping[str, Any],
        *,
        owner_type: OwnerType,
        owner_id: str,
        scope: EventScope,
        seq: int,
    ) -> EventEnvelope:
        """Migrate one legacy JSONL record into an explicit envelope.

        Legacy records carry only ``type`` (+ optional ``after_seq`` /
        ``timestamp`` / arbitrary payload keys) and no owner/scope; the caller
        supplies the owner identity and the sequence the record occupies.
        """
        return migrate_legacy_event(
            record, owner_type=owner_type, owner_id=owner_id, scope=scope, seq=seq
        )


# ── validation / migration ──────────────────────────────────────────────────


def validate_event_sequence(events: Sequence[EventEnvelope]) -> None:
    """Raise :class:`EventStreamCorruptError` if any per-owner ``seq`` regresses.

    Mirrors the frozen sample classifier (``events_corrupt``): a non-monotonic
    sequence is *rejected*, never silently reordered or truncated.
    """
    last: int | None = None
    last_owner: tuple[OwnerType, str] | None = None
    for event in events:
        owner = (event.owner_type, event.owner_id)
        if owner != last_owner:
            last = None
            last_owner = owner
        if last is not None and event.seq <= last:
            raise EventStreamCorruptError(
                f"seq regression for owner {owner[0].value}:{owner[1]!r}: {event.seq} after {last}"
            )
        last = event.seq


def migrate_legacy_event(
    record: Mapping[str, Any],
    *,
    owner_type: OwnerType = OwnerType.JOB,
    owner_id: str = "unknown",
    scope: EventScope = EventScope.TASK,
    seq: int | None = None,
) -> EventEnvelope:
    """Migrate a legacy event record (``after_seq`` / ``type``) to an envelope.

    ``seq`` resolution order: explicit *seq* → the record's own ``after_seq`` →
    1.  Every non-reserved record key becomes part of ``payload`` so no legacy
    field is lost (the old event file/fields are never deleted).
    """
    if not isinstance(record, Mapping):
        raise EventSerializationError("legacy event record must be a mapping")
    resolved_seq = seq
    if resolved_seq is None:
        resolved_seq = _as_int(record.get("after_seq"), name="after_seq", default=1)
    resolved_owner = _as_owner_type(owner_type, name="owner_type")
    resolved_owner_id = _as_str(owner_id, name="owner_id") or "unknown"
    resolved_scope = _as_scope(scope, name="scope")
    event_type = _as_str(
        record.get("event_type", record.get("type")) or "message", name="event_type"
    )
    payload = {key: value for key, value in record.items() if key not in _RESERVED_RECORD_KEYS}
    event_id = (
        _as_str(record.get("event_id"), name="event_id")
        or f"{resolved_owner.value}:{resolved_owner_id}:{resolved_seq}"
    )
    return EventEnvelope(
        event_id=event_id,
        seq=int(resolved_seq),
        schema_version=CURRENT_EVENT_SCHEMA_VERSION,
        owner_type=resolved_owner,
        owner_id=resolved_owner_id,
        scope=resolved_scope,
        event_type=event_type,
        cursor=str(resolved_seq),
        emitted_at=_as_str(record.get("emitted_at", record.get("timestamp")), name="emitted_at"),
        payload=payload,
        dedup_key=_as_str(record.get("dedup_key", record.get("idempotency_key")), name="dedup_key")
        or event_id,
        coverage=resolved_scope,
    )
