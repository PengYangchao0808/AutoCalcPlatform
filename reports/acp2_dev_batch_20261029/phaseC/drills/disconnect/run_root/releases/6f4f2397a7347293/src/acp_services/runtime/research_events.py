"""Cross-research-session events, cursor recovery and idempotent reconciliation.

B19 reuses the durable event protocol (:mod:`acp_services.runtime.events`) and
the transactional outbox (:mod:`acp_services.runtime.outbox`) instead of
introducing a second event store.  This module adds the *research* envelope
around one task owner's durable JSONL:

* an opaque, time-bounded **cursor** whose expiry is an explicit snapshot
  reset (never a silent gap);
* **task / attempt / revision / project** identity inside the existing
  twelve-field event payload;
* a **projection** whose terminal state is write-once (a stale or newer event
  can never revive it);
* an **idempotent consumer** that reconciles an approved plan to exactly one
  submit through an injected action ledger (the legacy ``JobStore``
  ``idempotency_records`` table is the production implementation);
* explicit **RPH-exit-after-task-completion** and **session-is-not-authority**
  rules.

Business-state separation
-------------------------
B owns ``task.*`` / ``approval.*`` / ``budget.*`` facts.  C owns the
``ResearchRecord`` question/proposal/round/task-reference and may publish only
``research.*.referenced`` facts onto this stream.  This module never writes a
C record and rejects a C fact that tries to carry task/approval/budget state;
C's reference facts never mutate the B projection's status.

Authority
---------
The durable event log is a replayable projection, never the authority.  The
CAS job store (B08/B16/B17) remains authoritative; this module only consumes
its committed outbox facts.  Cursor + snapshot together recover state; a
temporary WebSocket message is not authority.

The contract this module implements is frozen in
``docs/reference/next-change/acp2-contracts/runtime/events-recovery.v1.json``
(``acp2.runtime.events-recovery.v1``).
"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.events import EventEnvelope, EventScope, OwnerType
from acp_services.runtime.events import (
    RESET_CURSOR_EXPIRED,
    RESET_PERMISSION_CHANGED,
    RESET_SERVICE_RESTART,
    RESET_SUBSCRIPTION_CHANGED,
    EventLogPort,
    PersistentEventService,
)

__all__ = [
    "ACTION_APPROVAL",
    "ACTION_SUBMIT",
    "ADDITIVE_EVENT_TYPES",
    "ALL_EVENT_TYPES",
    "APPROVAL_EVENT_TYPES",
    "APPROVAL_TERMINAL_STATES",
    "B_EVENT_TYPES",
    "BUDGET_EVENT_TYPES",
    "BUDGET_TERMINAL_STATES",
    "C_REFERENCE_EVENT_TYPES",
    "DEFAULT_CURSOR_MAX_AGE_S",
    "PAYLOAD_VERSION",
    "RESEARCH_EVENT_SCHEMA",
    "RESEARCH_RESET_REASONS",
    "RESET_CURSOR_AHEAD",
    "RESET_CURSOR_INVALID",
    "RESET_CURSOR_MISSING",
    "RESET_RETENTION_GAP",
    "TASK_EVENT_TYPES",
    "TASK_TERMINAL_STATUSES",
    "TERMINAL_EVENT_TYPES",
    "ActionLedgerPort",
    "ClaimOutcome",
    "ConsumerAction",
    "DetachResult",
    "InMemoryActionLedger",
    "ProjectionApplyResult",
    "RecoveryOutcome",
    "ResearchCursor",
    "ResearchEvent",
    "ResearchEventService",
    "ResearchEventValidationError",
    "ResearchProjection",
    "ResearchRecoveryCoordinator",
    "ResearchReplay",
    "ResearchSessionRef",
    "SessionAuthorityError",
    "derive_terminal",
    "event_types_by_owner",
]

#: Frozen contract identity.
RESEARCH_EVENT_SCHEMA = "acp2.runtime.events-recovery.v1"
PAYLOAD_VERSION = 1
DEFAULT_CURSOR_MAX_AGE_S = 3600

#: Payload key reserved for the research envelope.  A caller payload carrying
#: this key is rejected rather than silently overwritten.
_RESEARCH_META = "_research"
_CURSOR_TOKEN_VERSION = "r1"

# Reset reasons (the two legacy reasons are re-used verbatim).
RESET_CURSOR_MISSING = "cursor_missing"
RESET_CURSOR_INVALID = "cursor_invalid"
RESET_CURSOR_AHEAD = "cursor_ahead"
RESET_RETENTION_GAP = "retention_gap"

RESEARCH_RESET_REASONS: tuple[str, ...] = (
    RESET_CURSOR_EXPIRED,
    RESET_CURSOR_MISSING,
    RESET_CURSOR_INVALID,
    RESET_CURSOR_AHEAD,
    RESET_RETENTION_GAP,
    RESET_PERMISSION_CHANGED,
    RESET_SERVICE_RESTART,
    RESET_SUBSCRIPTION_CHANGED,
)

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

TASK_EVENT_TYPES: tuple[str, ...] = (
    "task.queued",
    "task.started",
    "task.progress",
    "task.paused",
    "task.resumed",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.submitted",
)

APPROVAL_EVENT_TYPES: tuple[str, ...] = (
    "approval.requested",
    "approval.approved",
    "approval.rejected",
    "approval.revoked",
    "approval.expired",
    "approval.task_bound",
)

BUDGET_EVENT_TYPES: tuple[str, ...] = (
    "budget.reserved",
    "budget.settled",
    "budget.released",
    "budget.denied",
)

#: C may publish *references* only.  It never publishes task/approval/budget
#: state onto the B stream and B never writes the referenced record.
C_REFERENCE_EVENT_TYPES: tuple[str, ...] = (
    "research.question.referenced",
    "research.proposal.referenced",
    "research.round.referenced",
    "research.task_ref.referenced",
)

B_EVENT_TYPES: tuple[str, ...] = TASK_EVENT_TYPES + APPROVAL_EVENT_TYPES + BUDGET_EVENT_TYPES
ALL_EVENT_TYPES: tuple[str, ...] = B_EVENT_TYPES + C_REFERENCE_EVENT_TYPES

#: Facts that add a reference/submission without changing a business status.
#: They are accepted at the current revision (``approval.task_bound`` shares the
#: approval's revision) and never downgrade a terminal projection.
ADDITIVE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.submitted",
        "approval.task_bound",
        *C_REFERENCE_EVENT_TYPES,
    }
)

TASK_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})
APPROVAL_TERMINAL_STATES: frozenset[str] = frozenset({"approved", "rejected", "revoked", "expired"})
BUDGET_TERMINAL_STATES: frozenset[str] = frozenset({"settled", "released", "denied"})

TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "task.completed",
        "task.failed",
        "task.cancelled",
        "approval.rejected",
        "approval.revoked",
        "approval.expired",
        "budget.settled",
        "budget.released",
        "budget.denied",
    }
)

_TASK_STATUS_BY_EVENT: dict[str, str] = {
    "task.queued": "queued",
    "task.started": "running",
    "task.progress": "running",
    "task.paused": "paused",
    "task.resumed": "running",
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
}

_APPROVAL_STATE_BY_EVENT: dict[str, str] = {
    "approval.requested": "pending",
    "approval.approved": "approved",
    "approval.rejected": "rejected",
    "approval.revoked": "revoked",
    "approval.expired": "expired",
}

_BUDGET_STATE_BY_EVENT: dict[str, str] = {
    "budget.reserved": "reserved",
    "budget.settled": "settled",
    "budget.released": "released",
    "budget.denied": "denied",
}

ACTION_APPROVAL = "approval"
ACTION_SUBMIT = "submit"


class ResearchEventValidationError(ValueError):
    """The research envelope is malformed or uses an unknown event type."""


class SessionAuthorityError(PermissionError):
    """A chat/DSH session tried to act as the project authority."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def derive_terminal(event_type: str) -> bool:
    """Return whether *event_type* is a terminal fact in the frozen vocabulary."""
    return event_type in TERMINAL_EVENT_TYPES


def event_types_by_owner() -> dict[str, tuple[str, ...]]:
    """The contract's owner split (B state facts, C reference-only facts)."""
    return {"B": B_EVENT_TYPES, "C": C_REFERENCE_EVENT_TYPES}


def _parse_epoch(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return time.time()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return time.time()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _now_epoch() -> float:
    return time.time()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchCursor:
    """Opaque cursor bound to one sequence position and an issue time.

    Consumers must treat :meth:`encode` output as opaque.  The wire token is
    ``r1.<base64url(seq:issued_at)>``; the server is the only decoder.  A token
    older than ``max_age_s`` is expired and forces a snapshot reset.
    """

    seq: int
    issued_at: float

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ResearchEventValidationError(f"cursor seq must be >= 0, got {self.seq}")

    def encode(self) -> str:
        return f"{_CURSOR_TOKEN_VERSION}." + _b64encode(f"{self.seq}:{self.issued_at:.6f}".encode())

    @classmethod
    def decode(cls, token: str) -> ResearchCursor:
        try:
            version, encoded = str(token).split(".", 1)
            if version != _CURSOR_TOKEN_VERSION:
                raise ValueError("unknown cursor version")
            seq_text, issued_text = _b64decode(encoded).decode("ascii").split(":", 1)
            return cls(seq=int(seq_text), issued_at=float(issued_text))
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:  # type: ignore[attr-defined]
            raise ResearchEventValidationError(f"invalid research cursor {token!r}") from exc

    def expired(self, *, now: float, max_age_s: int) -> bool:
        return (now - self.issued_at) > max_age_s


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchEvent:
    """One research fact (durable envelope plus research identity)."""

    event_id: str
    seq: int
    cursor: str
    event_type: str
    task_id: str
    attempt: int
    project_id: str
    revision: int
    payload_version: int
    owner: str
    terminal: bool
    emitted_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    dedup_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "cursor": self.cursor,
            "event_type": self.event_type,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "project_id": self.project_id,
            "revision": self.revision,
            "payload_version": self.payload_version,
            "owner": self.owner,
            "terminal": self.terminal,
            "emitted_at": self.emitted_at,
            "payload": dict(self.payload),
            "dedup_key": self.dedup_key,
        }

    @classmethod
    def from_envelope(cls, envelope: EventEnvelope) -> ResearchEvent:
        """Decode the research envelope from a durable event envelope.

        Legacy records without the ``_research`` payload block remain readable:
        the task id is the owner id, the attempt is 1, the revision is the
        sequence and the owner defaults to B.
        """
        raw_payload = dict(envelope.payload)
        meta = raw_payload.pop(_RESEARCH_META, {})
        if not isinstance(meta, Mapping):
            meta = {}
        event_type = str(envelope.event_type)
        owner = str(meta.get("owner") or "B")
        if owner not in {"B", "C"}:
            raise ResearchEventValidationError(f"unknown research owner {owner!r}")
        return cls(
            event_id=envelope.event_id,
            seq=envelope.seq,
            cursor=ResearchCursor(
                seq=envelope.seq,
                issued_at=_parse_epoch(envelope.emitted_at),
            ).encode(),
            event_type=event_type,
            task_id=str(meta.get("task_id") or envelope.owner_id),
            attempt=int(meta.get("attempt") or 1),
            project_id=str(meta.get("project_id") or ""),
            revision=int(meta.get("revision") or envelope.seq),
            payload_version=int(meta.get("payload_version") or PAYLOAD_VERSION),
            owner=owner,
            terminal=derive_terminal(event_type),
            emitted_at=envelope.emitted_at,
            payload=raw_payload,
            dedup_key=envelope.dedup_key,
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchReplay:
    """One cursor replay result.

    ``reset=True`` always carries the current snapshot in ``events`` and
    ``snapshot_required=True``.  It is never an empty-but-silent success.
    """

    events: tuple[ResearchEvent, ...] = ()
    cursor: str = ""
    reset: bool = False
    reset_reason: str = ""
    snapshot_required: bool = False
    earliest_seq: int = 0
    latest_seq: int = 0

    def __bool__(self) -> bool:
        return bool(self.events)


class ResearchEventService:
    """Durable research event protocol for one ``(task, attempt)`` owner.

    The underlying per-task JSONL remains the only event log.  Every append
    carries a stable dedup key, so an at-least-once outbox delivery and a
    duplicate client message both become a no-op.

    Args:
        log: The injected durable log port (the acp-side adapter wraps the real
            ``JobEventLog``).
        project_id: The authoritative project scope for this stream.  A fact
            for another project is rejected at append and on replay.
        task_id: The task owner id.
        attempt: The execution attempt; an event for another attempt is never
            mixed into this projection.
        cursor_max_age_s: Cursor lifetime.  A cursor older than this forces a
            snapshot reset.
        clock: Epoch-seconds clock used to stamp new cursors (tests pin it).
        retention: Optional bounded replay window; an older cursor is a
            ``retention_gap`` reset.
    """

    def __init__(
        self,
        log: EventLogPort,
        *,
        project_id: str,
        task_id: str,
        attempt: int = 1,
        cursor_max_age_s: int = DEFAULT_CURSOR_MAX_AGE_S,
        clock: Callable[[], float] | None = None,
        retention: int | None = None,
    ) -> None:
        if not project_id:
            raise ResearchEventValidationError("project_id is required")
        if not task_id:
            raise ResearchEventValidationError("task_id is required")
        if attempt < 1:
            raise ResearchEventValidationError("attempt must be >= 1")
        if cursor_max_age_s < 1:
            raise ResearchEventValidationError("cursor_max_age_s must be >= 1")
        self.project_id = str(project_id)
        self.task_id = str(task_id)
        self.attempt = int(attempt)
        self.cursor_max_age_s = int(cursor_max_age_s)
        self._clock = clock or _now_epoch
        self._persistent = PersistentEventService(
            log=log,
            owner_type=OwnerType.TASK,
            owner_id=self.task_id,
            scope=EventScope.PROJECT,
            retention=retention,
        )
        self._lock = threading.RLock()

    # -- append ------------------------------------------------------------ #

    def append(
        self,
        event_type: str,
        *,
        revision: int,
        payload: Mapping[str, Any] | None = None,
        payload_version: int = PAYLOAD_VERSION,
        owner_component: str = "B",
        dedup_key: str | None = None,
        emitted_at: str | None = None,
        task_id: str | None = None,
        attempt: int | None = None,
        project_id: str | None = None,
    ) -> tuple[ResearchEvent, bool]:
        """Append one research fact idempotently.

        Returns ``(event, duplicate)``.  A repeated ``dedup_key`` returns the
        already-durable fact with ``duplicate=True`` and appends nothing.
        """
        with self._lock:
            kind = str(event_type)
            if kind not in ALL_EVENT_TYPES:
                raise ResearchEventValidationError(f"unknown research event type {kind!r}")
            owner = str(owner_component)
            if owner == "B" and kind not in B_EVENT_TYPES:
                raise ResearchEventValidationError(f"B may not publish C reference fact {kind!r}")
            if owner == "C" and kind not in C_REFERENCE_EVENT_TYPES:
                raise ResearchEventValidationError(
                    f"C may publish reference facts only, not {kind!r}"
                )
            resolved_project = str(project_id or self.project_id)
            if resolved_project != self.project_id:
                raise ResearchEventValidationError(
                    f"project scope mismatch: {resolved_project!r} != {self.project_id!r}"
                )
            resolved_task = str(task_id or self.task_id)
            if resolved_task != self.task_id:
                raise ResearchEventValidationError(
                    f"task scope mismatch: {resolved_task!r} != {self.task_id!r}"
                )
            resolved_attempt = int(attempt or self.attempt)
            if resolved_attempt != self.attempt:
                raise ResearchEventValidationError(
                    f"attempt scope mismatch: {resolved_attempt} != {self.attempt}"
                )
            if int(revision) < 1:
                raise ResearchEventValidationError("revision must be >= 1")
            body = dict(payload or {})
            if _RESEARCH_META in body:
                raise ResearchEventValidationError(
                    f"payload may not carry reserved key {_RESEARCH_META!r}"
                )
            meta = {
                "task_id": self.task_id,
                "attempt": self.attempt,
                "project_id": self.project_id,
                "revision": int(revision),
                "payload_version": int(payload_version),
                "owner": owner,
                "terminal": derive_terminal(kind),
            }
            envelope_payload = {**body, _RESEARCH_META: meta}
            resolved_dedup = dedup_key or f"{self.task_id}:{self.attempt}:{kind}:{revision}"
            existing = {envelope.dedup_key: envelope for envelope in self._persistent.snapshot()}
            envelope = self._persistent.append(
                kind,
                payload=envelope_payload,
                dedup_key=resolved_dedup,
                emitted_at=emitted_at,
            )
            duplicate = resolved_dedup in existing
            return ResearchEvent.from_envelope(envelope), duplicate

    # -- replay / snapshot ------------------------------------------------- #

    def replay(
        self,
        cursor: str | None,
        *,
        limit: int | None = None,
        now: float | None = None,
    ) -> ResearchReplay:
        """Replay after *cursor*, or return an explicit reset + current snapshot.

        A missing, invalid, expired, ahead-of-tail or retention-gap cursor is
        answered with ``reset=True``, a typed reason and the full current
        snapshot.  A lost cursor therefore re-reads state instead of silently
        skipping facts.
        """
        with self._lock:
            envelopes = list(self._persistent.snapshot())
            latest_seq = envelopes[-1].seq if envelopes else 0
            earliest_seq = envelopes[0].seq if envelopes else 0
            current = ResearchCursor(seq=latest_seq, issued_at=self._clock()).encode()

            def _reset(reason: str) -> ResearchReplay:
                return ResearchReplay(
                    events=tuple(ResearchEvent.from_envelope(item) for item in envelopes),
                    cursor=current,
                    reset=True,
                    reset_reason=reason,
                    snapshot_required=True,
                    earliest_seq=earliest_seq,
                    latest_seq=latest_seq,
                )

            if not cursor:
                return _reset(RESET_CURSOR_MISSING)
            try:
                decoded = ResearchCursor.decode(str(cursor))
            except ResearchEventValidationError:
                return _reset(RESET_CURSOR_INVALID)
            current_now = now if now is not None else self._clock()
            if decoded.expired(now=current_now, max_age_s=self.cursor_max_age_s):
                return _reset(RESET_CURSOR_EXPIRED)
            if decoded.seq > latest_seq:
                return _reset(RESET_CURSOR_AHEAD)

            replay = self._persistent.replay(decoded.seq, limit=limit)
            if replay.reset:
                return _reset(RESET_RETENTION_GAP)
            events = tuple(ResearchEvent.from_envelope(item) for item in replay.events)
            resume_seq = events[-1].seq if events else latest_seq
            return ResearchReplay(
                events=events,
                cursor=ResearchCursor(seq=resume_seq, issued_at=self._clock()).encode(),
                reset=False,
                reset_reason="",
                snapshot_required=False,
                earliest_seq=replay.earliest_seq,
                latest_seq=replay.latest_seq,
            )

    def snapshot(self) -> tuple[ResearchEvent, ...]:
        """Full durable re-read (the snapshot half of recovery)."""
        with self._lock:
            return tuple(ResearchEvent.from_envelope(item) for item in self._persistent.snapshot())

    def current_cursor(self) -> str:
        """A fresh opaque cursor at the current durable tail."""
        with self._lock:
            envelopes = self._persistent.snapshot()
            seq = envelopes[-1].seq if envelopes else 0
            return ResearchCursor(seq=seq, issued_at=self._clock()).encode()


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionApplyResult:
    """Outcome of applying one event to a projection."""

    outcome: str
    event_id: str
    reason: str = ""

    @property
    def applied(self) -> bool:
        return self.outcome in {"applied", "applied_additive"}


class ResearchProjection:
    """Idempotent, terminal-protected consumer projection for one task/attempt.

    The projection is deliberately *not* a business authority: it is rebuilt
    from the durable event log (or a snapshot) and can always be discarded.
    """

    _MAX_APPLIED_IDS = 4096

    def __init__(self, *, project_id: str, task_id: str, attempt: int = 1) -> None:
        self.project_id = str(project_id)
        self.task_id = str(task_id)
        self.attempt = int(attempt)
        self.status = "unknown"
        self.revision = 0
        self.approval_state = ""
        self.approval_revision = 0
        self.approval_ref = ""
        self.budget_state = ""
        self.budget_revision = 0
        self.submitted = False
        self.task_ref: dict[str, Any] | None = None
        self.progress: float | None = None
        self.error = ""
        self.c_refs: dict[str, dict[str, Any]] = {}
        self.last_event_id = ""
        self._applied_order: list[str] = []
        self._applied: set[str] = set()

    # -- state ------------------------------------------------------------- #

    @property
    def task_terminal(self) -> bool:
        return self.status in TASK_TERMINAL_STATUSES

    @property
    def approval_terminal(self) -> bool:
        return self.approval_state in APPROVAL_TERMINAL_STATES

    @property
    def budget_terminal(self) -> bool:
        return self.budget_state in BUDGET_TERMINAL_STATES

    @property
    def terminal(self) -> bool:
        return self.task_terminal

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "status": self.status,
            "revision": self.revision,
            "task_terminal": self.task_terminal,
            "approval_state": self.approval_state,
            "approval_revision": self.approval_revision,
            "approval_ref": self.approval_ref,
            "budget_state": self.budget_state,
            "budget_revision": self.budget_revision,
            "submitted": self.submitted,
            "task_ref": dict(self.task_ref) if self.task_ref else None,
            "progress": self.progress,
            "error": self.error,
            "c_references": {key: dict(value) for key, value in self.c_refs.items()},
            "last_event_id": self.last_event_id,
            "applied_event_count": len(self._applied_order),
        }

    # -- rebuild ----------------------------------------------------------- #

    def reset(self) -> None:
        """Discard the in-memory projection (snapshot rebuild entry point)."""
        self.status = "unknown"
        self.revision = 0
        self.approval_state = ""
        self.approval_revision = 0
        self.approval_ref = ""
        self.budget_state = ""
        self.budget_revision = 0
        self.submitted = False
        self.task_ref = None
        self.progress = None
        self.error = ""
        self.c_refs = {}
        self.last_event_id = ""
        self._applied_order = []
        self._applied = set()

    def rebuild_from_snapshot(self, events: Sequence[ResearchEvent]) -> int:
        """Rebuild from a full snapshot; returns the number of applied facts."""
        self.reset()
        applied = 0
        for event in events:
            result = self.apply(event)
            if result.applied:
                applied += 1
        return applied

    # -- apply ------------------------------------------------------------- #

    def apply(self, event: ResearchEvent) -> ProjectionApplyResult:
        """Apply one fact idempotently, in revision order, without revival."""
        if event.project_id and event.project_id != self.project_id:
            return ProjectionApplyResult("project_scope_violation", event.event_id)
        if event.event_id in self._applied:
            return ProjectionApplyResult("duplicate", event.event_id)

        if event.owner == "C":
            if event.event_type not in C_REFERENCE_EVENT_TYPES:
                return ProjectionApplyResult("unknown_event", event.event_id)
            if event.payload_version > PAYLOAD_VERSION:
                return ProjectionApplyResult("unsupported_payload_version", event.event_id)
            self.c_refs[event.event_type] = {
                "resource_ref": event.payload.get("resource_ref", ""),
                "resource_revision": event.payload.get("resource_revision"),
                "resource_digest": event.payload.get("resource_digest", ""),
                "event_id": event.event_id,
            }
            self._mark(event)
            return ProjectionApplyResult("applied", event.event_id, "c_reference")

        if event.event_type not in B_EVENT_TYPES:
            return ProjectionApplyResult("unknown_event", event.event_id)
        if event.task_id != self.task_id:
            return ProjectionApplyResult("task_scope_mismatch", event.event_id)
        if event.attempt != self.attempt:
            if event.attempt < self.attempt:
                return ProjectionApplyResult("stale_attempt", event.event_id)
            return ProjectionApplyResult("attempt_mismatch", event.event_id)
        if event.payload_version > PAYLOAD_VERSION:
            return ProjectionApplyResult("unsupported_payload_version", event.event_id)

        additive = event.event_type in ADDITIVE_EVENT_TYPES
        if event.revision < self.revision:
            return ProjectionApplyResult("stale", event.event_id)
        if event.revision == self.revision:
            if additive:
                changed = self._apply_additive(event)
                self._mark(event)
                return ProjectionApplyResult(
                    "applied_additive" if changed else "out_of_order",
                    event.event_id,
                )
            self._mark(event)
            return ProjectionApplyResult("out_of_order", event.event_id)

        # A newer revision still cannot revive a terminal state.
        if additive:
            changed = self._apply_additive(event)
            if event.revision > self.revision:
                self.revision = event.revision
            self._mark(event)
            return ProjectionApplyResult(
                "applied_additive" if changed else "out_of_order",
                event.event_id,
            )

        if event.event_type in _TASK_STATUS_BY_EVENT:
            if self.task_terminal:
                return ProjectionApplyResult("terminal_protected", event.event_id)
            self.status = _TASK_STATUS_BY_EVENT[event.event_type]
            if event.event_type == "task.progress":
                progress = event.payload.get("progress")
                if isinstance(progress, (int, float)):
                    self.progress = float(progress)
            if event.event_type == "task.failed":
                self.error = str(event.payload.get("error") or "")
        elif event.event_type in _APPROVAL_STATE_BY_EVENT:
            if self.approval_terminal:
                return ProjectionApplyResult("terminal_protected", event.event_id)
            self.approval_state = _APPROVAL_STATE_BY_EVENT[event.event_type]
            self.approval_revision = event.revision
            self.approval_ref = str(event.payload.get("approval_ref") or self.approval_ref)
        elif event.event_type in _BUDGET_STATE_BY_EVENT:
            if self.budget_terminal:
                return ProjectionApplyResult("terminal_protected", event.event_id)
            self.budget_state = _BUDGET_STATE_BY_EVENT[event.event_type]
            self.budget_revision = event.revision
        else:  # pragma: no cover - the vocabulary above is total
            return ProjectionApplyResult("unknown_event", event.event_id)

        self.revision = max(self.revision, event.revision)
        self._mark(event)
        return ProjectionApplyResult("applied", event.event_id)

    # -- internals --------------------------------------------------------- #

    def _apply_additive(self, event: ResearchEvent) -> bool:
        if event.event_type == "task.submitted":
            ref = event.payload.get("task_ref")
            changed = not self.submitted
            self.submitted = True
            if isinstance(ref, Mapping):
                self.task_ref = dict(ref)
            return changed
        if event.event_type == "approval.task_bound":
            ref = event.payload.get("task_ref")
            changed = not self.submitted
            self.submitted = True
            self.approval_ref = str(event.payload.get("approval_ref") or self.approval_ref)
            if isinstance(ref, Mapping):
                self.task_ref = dict(ref)
            return changed
        return False

    def _mark(self, event: ResearchEvent) -> None:
        if event.event_id in self._applied:
            return
        self._applied.add(event.event_id)
        self._applied_order.append(event.event_id)
        if len(self._applied_order) > self._MAX_APPLIED_IDS:
            dropped = self._applied_order.pop(0)
            self._applied.discard(dropped)
        self.last_event_id = event.event_id


# ---------------------------------------------------------------------------
# Session authority + action ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchSessionRef:
    """A chat/DSH session identity.  It carries no project authority."""

    session_id: str
    claimed_project_id: str = ""


@dataclass(frozen=True)
class DetachResult:
    """Outcome of an RPH/session exit (never a task cancellation)."""

    continues: bool
    blocks_on_llm: bool
    reason: str


@dataclass(frozen=True)
class ClaimOutcome:
    """One action-ledger claim result."""

    kind: str
    task_ref: Mapping[str, Any] | None = None
    detail: str = ""


@runtime_checkable
class ActionLedgerPort(Protocol):
    """Persistent ``(action, idempotency_key)`` ledger (B08 ``JobStore``)."""

    def claim(self, *, action: str, idempotency_key: str, request_digest: str) -> ClaimOutcome:
        """Claim one action or report the durable outcome."""
        ...

    def complete(
        self, *, action: str, idempotency_key: str, task_ref: Mapping[str, Any]
    ) -> ClaimOutcome:
        """Record the TaskRef for a claim this consumer just executed."""
        ...


@dataclass
class _LedgerRow:
    request_digest: str
    task_ref: dict[str, Any] | None = None


class InMemoryActionLedger:
    """Thread-safe action ledger double for fixture-level recovery tests."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], _LedgerRow] = {}
        self._lock = threading.RLock()
        self.claim_calls = 0
        self.complete_calls = 0

    def claim(self, *, action: str, idempotency_key: str, request_digest: str) -> ClaimOutcome:
        with self._lock:
            self.claim_calls += 1
            key = (str(action), str(idempotency_key))
            row = self._rows.get(key)
            if row is None:
                self._rows[key] = _LedgerRow(request_digest=str(request_digest))
                return ClaimOutcome("created")
            if row.request_digest != str(request_digest):
                return ClaimOutcome("conflict", detail="idempotency key reuse")
            if row.task_ref is not None:
                return ClaimOutcome("replay", task_ref=dict(row.task_ref))
            return ClaimOutcome("in_progress", detail="another owner holds the claim")

    def complete(
        self, *, action: str, idempotency_key: str, task_ref: Mapping[str, Any]
    ) -> ClaimOutcome:
        with self._lock:
            self.complete_calls += 1
            key = (str(action), str(idempotency_key))
            row = self._rows.get(key)
            if row is None:
                raise ResearchEventValidationError(
                    f"cannot complete unclaimed action {action!r}/{idempotency_key!r}"
                )
            row.task_ref = dict(task_ref)
            return ClaimOutcome("replay", task_ref=dict(row.task_ref))


# ---------------------------------------------------------------------------
# Recovery coordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsumerAction:
    """The single next action an idempotent consumer should take."""

    kind: str
    idempotency_key: str = ""
    detail: str = ""


@dataclass(frozen=True)
class RecoveryOutcome:
    """Result of one cursor recovery + reconciliation pass."""

    projection: ResearchProjection
    replayed: int
    duplicates: int
    stale: int
    reset: bool
    reset_reason: str
    previous_cursor: str
    next_cursor: str
    action: ConsumerAction
    claim: ClaimOutcome | None = None
    submitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection": self.projection.to_dict(),
            "replayed": self.replayed,
            "duplicates": self.duplicates,
            "stale": self.stale,
            "reset": self.reset,
            "reset_reason": self.reset_reason,
            "previous_cursor": self.previous_cursor,
            "next_cursor": self.next_cursor,
            "action": {
                "kind": self.action.kind,
                "idempotency_key": self.action.idempotency_key,
                "detail": self.action.detail,
            },
            "claim": (
                {
                    "kind": self.claim.kind,
                    "task_ref": dict(self.claim.task_ref) if self.claim.task_ref else None,
                    "detail": self.claim.detail,
                }
                if self.claim is not None
                else None
            ),
            "submitted": self.submitted,
        }


class ResearchRecoveryCoordinator:
    """Idempotent cross-session recovery consumer.

    ``recover`` replays from the last cursor (or rebuilds from the snapshot on
    an expiry/loss reset), then reconciles an approved-but-unsubmitted plan
    through the action ledger.  A lost or expired cursor therefore re-reads the
    durable state and **does not** cause a second submit: the ledger returns
    ``replay`` for the same key.
    """

    def __init__(
        self,
        service: ResearchEventService,
        projection: ResearchProjection,
        ledger: ActionLedgerPort,
    ) -> None:
        self.service = service
        self.projection = projection
        self.ledger = ledger
        self._lock = threading.RLock()

    def recover(
        self,
        cursor: str | None,
        *,
        submit_key: str = "",
        request_digest: str = "",
        submit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> RecoveryOutcome:
        """Replay + reconcile once; safe to call repeatedly with the same key."""
        with self._lock:
            replay = self.service.replay(cursor)
            duplicates = 0
            stale = 0
            replayed = 0
            if replay.reset:
                self.projection.rebuild_from_snapshot(replay.events)
                replayed = len(replay.events)
            else:
                for event in replay.events:
                    result = self.projection.apply(event)
                    if result.outcome == "duplicate":
                        duplicates += 1
                    elif result.outcome in {"stale", "out_of_order", "terminal_protected"}:
                        stale += 1
                    if result.applied:
                        replayed += 1

            action = self._decide()
            claim: ClaimOutcome | None = None
            submitted = False
            if action.kind == "submit":
                key = submit_key or action.idempotency_key
                digest = request_digest or key
                claim = self.ledger.claim(
                    action=ACTION_SUBMIT,
                    idempotency_key=key,
                    request_digest=digest,
                )
                if claim.kind == "created":
                    task_ref = (
                        dict(submit(self.projection.to_dict()))
                        if submit is not None
                        else {"task_id": self.projection.task_id}
                    )
                    self.ledger.complete(
                        action=ACTION_SUBMIT,
                        idempotency_key=key,
                        task_ref=task_ref,
                    )
                    self._record_submission(key, task_ref)
                    submitted = True
                elif claim.kind == "replay" and claim.task_ref:
                    self._record_submission(key, dict(claim.task_ref))
            return RecoveryOutcome(
                projection=self.projection,
                replayed=replayed,
                duplicates=duplicates,
                stale=stale,
                reset=replay.reset,
                reset_reason=replay.reset_reason,
                previous_cursor=str(cursor or ""),
                next_cursor=replay.cursor,
                action=action,
                claim=claim,
                submitted=submitted,
            )

    def detach_session(
        self, session: ResearchSessionRef, *, reason: str = "rph_exit"
    ) -> DetachResult:
        """Apply the RPH-exit rule: a session exit never blocks or cancels work.

        The session id is recorded for diagnostics only.  A claimed project id
        is **not** authority (a mismatch is refused); the durable projection's
        project scope is the only authority.
        """
        if session.claimed_project_id and session.claimed_project_id != self.projection.project_id:
            raise SessionAuthorityError(
                "a chat/DSH session may not claim a different project scope"
            )
        if self.projection.task_terminal:
            return DetachResult(continues=False, blocks_on_llm=False, reason="task_terminal")
        return DetachResult(
            continues=True,
            blocks_on_llm=False,
            reason=f"{reason}:task_not_terminal",
        )

    # -- internals --------------------------------------------------------- #

    def _decide(self) -> ConsumerAction:
        projection = self.projection
        if projection.task_terminal:
            return ConsumerAction("no_action", detail="task terminal")
        if projection.approval_state == "approved" and not projection.submitted:
            key = (
                f"{projection.project_id}:{projection.task_id}:"
                f"{projection.approval_ref or projection.approval_revision}:submit"
            )
            return ConsumerAction(ACTION_SUBMIT, idempotency_key=key)
        if projection.approval_state in {"", "pending"} and not projection.submitted:
            return ConsumerAction("await_approval", detail="approval not granted")
        if projection.status in {"queued", "running", "paused"}:
            return ConsumerAction("observe", detail="task not terminal")
        if projection.submitted:
            return ConsumerAction("no_action", detail="already submitted")
        return ConsumerAction("no_action", detail="no reconcilable state")

    def _record_submission(self, key: str, task_ref: Mapping[str, Any]) -> None:
        revision = max(self.projection.revision, 1) + 1
        event, _duplicate = self.service.append(
            "task.submitted",
            revision=revision,
            payload={"task_ref": dict(task_ref), "submit_key": key},
            dedup_key=f"submit:{key}",
            owner_component="B",
        )
        self.projection.apply(event)
