"""Execute the three clocks: what the platform does when one expires (M4-03).

:mod:`acp_services.runtime.execution_limits` computes the persistent absolute
deadlines and :mod:`acp_services.runtime.deadline_wiring` carries the value to
the mechanisms.  Neither *acts*: a deadline that nobody enforces is a promise,
not a limit.  This module is the pure decision half of enforcement — it says
which clock expired, which surface must act, and what the platform must persist
as the receipt — while the acting half stays with the single control owner
(``acp.scheduler.manager``) and its process/LSF adapters.

Frozen rules
------------

1. **One decision per evaluation.**  A queued attempt is governed by the queue
   deadline only; a started attempt by the wall deadline
   (:func:`acp_services.runtime.execution_limits.clock_projection`).  The
   science-runtime clock never terminates anything.
2. **A receipt is evidence, not a status.**  The receipt records the clock,
   the absolute deadline, what was observed natively (local process groups /
   ``bjobs -l`` / ``#BSUB -W``) and the terminal status this evaluation
   produced.  A missing receipt is *not* a pass (``verified`` stays ``False``).
3. **A late success never revives a terminal attempt.**  The decision carries
   ``terminal_status="failed"``; the caller must apply it with a compare-and-swap
   that refuses a non-active source state, so an observation arriving after the
   deadline cannot flip the row back to ``completed``.
4. **PAUSED time counts.**  The wall deadline is absolute from ``started_at``
   (``DEFAULT_INCLUDE_PAUSED``); a pause never extends it, so re-evaluating
   after a resume returns the *remaining* time.

Isolation: stdlib + first-party ``acp_services`` (no ``acp`` import).

Author: ACP Services (B lane, M4-03).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from acp_services.runtime.execution_limits import (
    CLOCK_QUEUE,
    CLOCK_WALL,
    AttemptClock,
    ClockProjection,
    ResourceLimits,
    clock_projection,
    compute_attempt_clock,
    limits_from_execution_limits_block,
)

__all__ = [
    "DECISION_EXPIRE_QUEUE",
    "DECISION_NONE",
    "DECISION_TERMINATE_WALL",
    "DEADLINE_RECEIPT_SCHEMA",
    "DeadlineDecision",
    "DeadlineReceipt",
    "TERMINAL_STATUS_FAILED",
    "admission_limits",
    "attempt_clock_from_record",
    "deadline_receipt",
    "evaluate_deadline",
    "receipt_from_decision",
]

#: Contract identity of one deadline-enforcement receipt.
DEADLINE_RECEIPT_SCHEMA = "acp2.runtime.deadline-receipt.v1"

DECISION_NONE = "none"
DECISION_EXPIRE_QUEUE = "queue-deadline-expired"
DECISION_TERMINATE_WALL = "wall-deadline-expired"

#: A deadline never completes an attempt; it always ends it as failed.
TERMINAL_STATUS_FAILED = "failed"

#: The persisted result key that carries the receipt (durable, re-readable).
RECEIPT_RESULT_KEY = "execution_deadline"


@dataclass(frozen=True, slots=True)
class DeadlineDecision:
    """One evaluated clock: act now, or not."""

    decision: str
    clock: str
    deadline_at: datetime | None
    remaining_s: int | None
    terminal_status: str = TERMINAL_STATUS_FAILED
    surface: str = ""
    rule: str = ""

    @property
    def act(self) -> bool:
        return self.decision != DECISION_NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "clock": self.clock,
            "deadline_at": _iso(self.deadline_at),
            "remaining_s": self.remaining_s,
            "terminal_status": self.terminal_status,
            "surface": self.surface,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class DeadlineReceipt:
    """What happened when a clock expired (the closing evidence of a limit)."""

    job_id: str
    attempt: int
    decision: str
    clock: str
    deadline_at: datetime | None
    observed_at: datetime
    admitted_at: datetime | None = None
    started_at: datetime | None = None
    wall_seconds: int | None = None
    queue_seconds: int | None = None
    surface: str = ""
    terminal_status: str = TERMINAL_STATUS_FAILED
    native: Mapping[str, Any] = field(default_factory=dict)
    verified: bool = False
    schema: str = DEADLINE_RECEIPT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "attempt": int(self.attempt),
            "decision": self.decision,
            "clock": self.clock,
            "deadline_at": _iso(self.deadline_at),
            "observed_at": _iso(self.observed_at),
            "admitted_at": _iso(self.admitted_at),
            "started_at": _iso(self.started_at),
            "wall_seconds": self.wall_seconds,
            "queue_seconds": self.queue_seconds,
            "surface": self.surface,
            "terminal_status": self.terminal_status,
            "native": dict(self.native),
            "verified": bool(self.verified),
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def admission_limits(result: Mapping[str, Any] | None) -> ResourceLimits:
    """Read the durable deadline projection persisted by admission (M2-03c).

    The admission result carries ``execution_limits`` — the resolved
    :class:`ResourceLimits` projection, not the raw budget shape — so a restart
    reads the *same* three surfaces instead of re-deriving them.
    """
    admission = (result or {}).get("admission")
    if not isinstance(admission, Mapping):
        return ResourceLimits()
    block = admission.get("execution_limits")
    if not isinstance(block, Mapping):
        return ResourceLimits()
    return limits_from_execution_limits_block(block)


def attempt_clock_from_record(
    record: Any,
    *,
    include_paused: bool = True,
    paused_seconds: int = 0,
) -> AttemptClock:
    """Build the persistent attempt clock of one job row.

    ``admitted_at`` is the submission timestamp and ``started_at`` the dispatch
    timestamp — both durable columns, so a Controller restart restores the same
    absolute deadlines without writing a second clock.
    """
    result = getattr(record, "result", None)
    limits = admission_limits(result if isinstance(result, Mapping) else None)
    # A remote attempt "starts" when the batch system actually runs it, not when
    # the worker is dispatched: the queue clock governs until then.  The RUN
    # observation persists ``result["remote"]["started_at"]``; a remote row
    # without it has no wall clock yet (never a dispatch-time substitute).
    is_remote = False
    remote_started: datetime | None = None
    if isinstance(result, Mapping):
        remote = result.get("remote")
        is_remote = result.get("execution_kind") == "remote" or isinstance(remote, Mapping)
        if isinstance(remote, Mapping):
            remote_started = _parse(remote.get("started_at"))
    # A remote attempt has not started until the batch system runs it: the
    # dispatch timestamp is NOT a wall-clock start (queue waiting would eat the
    # approved budget).  Until the RUN observation the queue clock is the only
    # one that governs; a local attempt starts at dispatch.
    started = remote_started if is_remote else _parse(getattr(record, "started_at", None))
    return compute_attempt_clock(
        admitted_at=_parse(getattr(record, "created_at", None)) or datetime.now(timezone.utc),
        started_at=started,
        wall_seconds=limits.local_terminate_after_s,
        queue_seconds=limits.queue_deadline_s,
        paused_seconds=paused_seconds,
        include_paused=include_paused,
    )


def evaluate_deadline(clock: AttemptClock, now: datetime) -> DeadlineDecision:
    """Decide whether one clock expired and which surface must act.

    Returns a non-acting decision when nothing expired, so the caller can keep
    one code path (and never has to interpret a ``None``).
    """
    projection: ClockProjection = clock_projection(clock, now)
    if not projection.expired:
        return DeadlineDecision(
            decision=DECISION_NONE,
            clock=projection.governing_clock,
            deadline_at=(
                clock.queue_deadline_at
                if projection.governing_clock == CLOCK_QUEUE
                else clock.wall_deadline_at
            ),
            remaining_s=projection.remaining_s,
            rule=projection.rule,
        )
    if projection.governing_clock == CLOCK_QUEUE:
        return DeadlineDecision(
            decision=DECISION_EXPIRE_QUEUE,
            clock=CLOCK_QUEUE,
            deadline_at=clock.queue_deadline_at,
            remaining_s=projection.remaining_s,
            surface="queue",
            rule="rank 1: the queued attempt's only clock expired; it never starts late",
        )
    return DeadlineDecision(
        decision=DECISION_TERMINATE_WALL,
        clock=CLOCK_WALL,
        deadline_at=clock.wall_deadline_at,
        remaining_s=projection.remaining_s,
        surface="wall",
        rule="rank 2: the started attempt's wall deadline terminates it",
    )


def receipt_from_decision(
    *,
    job_id: str,
    attempt: int,
    decision: DeadlineDecision,
    clock: AttemptClock,
    observed_at: datetime,
    surface: str = "",
    native: Mapping[str, Any] | None = None,
    verified: bool = False,
    terminal_status: str = TERMINAL_STATUS_FAILED,
) -> DeadlineReceipt:
    """Materialize the receipt of one acted decision."""
    return DeadlineReceipt(
        job_id=str(job_id),
        attempt=int(attempt),
        decision=decision.decision,
        clock=decision.clock,
        deadline_at=decision.deadline_at,
        observed_at=observed_at,
        admitted_at=clock.admitted_at,
        started_at=clock.started_at,
        wall_seconds=None
        if clock.wall_deadline_at is None or clock.started_at is None
        else int((clock.wall_deadline_at - clock.started_at).total_seconds()),
        queue_seconds=None
        if clock.queue_deadline_at is None or clock.admitted_at is None
        else int((clock.queue_deadline_at - clock.admitted_at).total_seconds()),
        surface=surface or decision.surface,
        terminal_status=terminal_status,
        native=dict(native or {}),
        verified=bool(verified),
    )


def deadline_receipt(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize (or reject) a persisted receipt for projection."""
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    if payload.get("schema") != DEADLINE_RECEIPT_SCHEMA:
        return None
    return payload
