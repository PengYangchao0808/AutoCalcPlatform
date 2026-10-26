"""Execution limits: three clocks, one budget shape, one resource resolver (M1-04b).

D09 separates *budget units* (``research_budget.UNIT_CAPABILITIES``) from
**execution limits** that are not ``BudgetUnit``s: an admission-bound ceiling, a
process-termination deadline and a queue deadline.  Before this module the local
admission snapshot published ``quota_enforced=True`` / ``HARD_LIMIT_ENFORCED``
for walltime without distinguishing "we refused the request at admission" from
"we terminated the process at the deadline".  Those are different facts and this
module keeps them apart:

``admission_bound``
    The submit is refused when the requested bound exceeds the server ceiling.
    That is the *only* walltime claim that is ``enforced`` today.
``process_termination``
    The running attempt is actually stopped at the deadline: local process group,
    CCCP ``resources.timeout_s`` (subprocess-level only) or LSF ``-W``.  No
    termination receipt exists yet, so this is ``NOT_VERIFIED`` until M1-05/M4-03.
``queue_deadline``
    A queued attempt expires at an absolute time.  Wired at admission, but the
    expiry → terminal transition is also pending, so ``NOT_VERIFIED``.
``recorded-only``
    Measured and reported (science runtime, cumulative core hours), never
    asserted as a cap.

Three clocks (frozen in :data:`CLOCK_CONTRACT`):

1. **queue deadline** starts when the submit is admitted/queued;
2. **per-attempt wall deadline** is a *persistent absolute time*; by default it
   includes PAUSED time (a pause does not extend a scientific budget) and a
   restart only restores the *remaining* time;
3. **science runtime** records the actual execution time; an RPH client wait
   timeout is not a substitute for it.

Precedence (:data:`CLOCK_PRECEDENCE`): a queued attempt is governed by the queue
deadline only; once started, the wall deadline terminates it; the science
runtime clock only records.  A conflict is resolved in that order by the
platform — a client/RPH may not choose one clock to bypass another.

Budget shape (D09):

===============  =========================================================
``wall_seconds``  ``ResourceBounds``/timeout: local termination + CCCP
                 ``resources.timeout_s`` (subprocess only) + LSF ``-W``
``queue_seconds`` queue deadline (absolute, from admission)
``input_tokens``  recorded-only + RPH client throttle (platform hard cap
``output_tokens`` stays ``NOT_VERIFIED``)
``core_hours``    recorded-only
===============  =========================================================

None of the four is silently dropped, and none becomes "enforced" by being
present.  A non-empty shape that is not registered is a typed error.

Isolation: stdlib only (no ``acp`` import).

Author: ACP Services (B lane, M1-04b).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from acp_services.runtime.research_budget import (
    BudgetCapability,
    BudgetConfigurationError,
)

__all__ = [
    "CLOCK_CONTRACT",
    "CLOCK_PRECEDENCE",
    "CLOCK_QUEUE",
    "CLOCK_SCIENCE_RUNTIME",
    "CLOCK_WALL",
    "DEFAULT_INCLUDE_PAUSED",
    "EXECUTION_LIMITS",
    "EXECUTION_LIMIT_SCHEMA",
    "LIMIT_ADMISSION_BOUND",
    "LIMIT_CAPABILITY_VALUES",
    "LIMIT_PROCESS_TERMINATION",
    "LIMIT_QUEUE_DEADLINE",
    "LIMIT_RECORDED_ONLY",
    "AttemptClock",
    "BudgetShape",
    "ClockProjection",
    "ExecutionLimit",
    "ExecutionLimitError",
    "ResourceLimits",
    "bind_budget_shape",
    "clock_projection",
    "compute_attempt_clock",
    "execution_limits_statement",
    "limit_row",
    "normalize_walltime_s",
    "resolve_resource_limits",
]

#: Contract identity of the execution-limit matrix.
EXECUTION_LIMIT_SCHEMA = "acp2.runtime.execution-limits.v1"

LIMIT_ADMISSION_BOUND = "admission-bound"
LIMIT_PROCESS_TERMINATION = "process-termination"
LIMIT_QUEUE_DEADLINE = "queue-deadline"
LIMIT_RECORDED_ONLY = "recorded-only"

#: Capability vocabulary is the budget one: enforced / recorded-only / NOT_VERIFIED.
LIMIT_CAPABILITY_VALUES: tuple[str, ...] = (
    BudgetCapability.ENFORCED,
    BudgetCapability.RECORDED_ONLY,
    BudgetCapability.NOT_VERIFIED,
)

CLOCK_QUEUE = "queue"
CLOCK_WALL = "wall"
CLOCK_SCIENCE_RUNTIME = "science-runtime"

#: A wall deadline includes PAUSED time unless a policy explicitly excludes it.
DEFAULT_INCLUDE_PAUSED = True


class ExecutionLimitError(BudgetConfigurationError):
    """A limit shape is invalid or unknown (typed, never silently dropped)."""

    def __init__(self, rule: str, message: str, *, params: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message
        self.params = dict(params or {})

    def detail(self) -> dict[str, Any]:
        return {
            "code": "input_invalid",
            "message": self.message,
            "params": {"rule": self.rule, **self.params},
        }


@dataclass(frozen=True, slots=True)
class ExecutionLimit:
    """One non-``BudgetUnit`` execution limit and its truthful capability."""

    limit: str
    kind: str
    carrier: str
    capability: str
    source_field: str = ""
    budget_unit: str | None = None
    evidence: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "kind": self.kind,
            "carrier": self.carrier,
            "capability": self.capability,
            "source_field": self.source_field,
            "budget_unit": self.budget_unit,
            "evidence": self.evidence,
            "notes": self.notes,
        }


#: The frozen execution-limit matrix.  Rows may only flip to ``enforced`` with
#: the closing evidence named in ``evidence`` (D09/M1-05/M4-03).
EXECUTION_LIMITS: tuple[ExecutionLimit, ...] = (
    ExecutionLimit(
        limit="walltime_admission_bound",
        kind=LIMIT_ADMISSION_BOUND,
        carrier="LocalAdmission.effective_bounds / AdmissionRequest.duration_s",
        capability=BudgetCapability.ENFORCED,
        source_field="wall_seconds",
        evidence=(
            "tests/test_acp2_runtime_admission.py (refused before dispatch); "
            "local_admission ceilings are finite by construction"
        ),
        notes=(
            "only the *request* is bounded here; it does not terminate a running "
            "attempt and must not be presented as process termination"
        ),
    ),
    ExecutionLimit(
        limit="walltime_process_termination",
        kind=LIMIT_PROCESS_TERMINATION,
        carrier="local process group / CCCP resources.timeout_s / LSF -W",
        capability=BudgetCapability.NOT_VERIFIED,
        source_field="wall_seconds",
        evidence="needs a real termination receipt (M1-05 local+LSF, M4-03)",
        notes=(
            "CCCP resources.timeout_s is subprocess-level only; the local deadline "
            "needs the process-group kill receipt and the remote one needs -W plus a "
            "terminal LSF state"
        ),
    ),
    ExecutionLimit(
        limit="queue_deadline",
        kind=LIMIT_QUEUE_DEADLINE,
        carrier="persistent absolute deadline recorded at admission",
        capability=BudgetCapability.NOT_VERIFIED,
        source_field="queue_seconds",
        evidence="needs an expiry -> terminal transition receipt (M1-05)",
        notes="a queued attempt is governed by this clock only; it never starts late",
    ),
    ExecutionLimit(
        limit="science_runtime_recorded",
        kind=LIMIT_RECORDED_ONLY,
        carrier="usage receipt seconds (controller-terminal-receipt)",
        capability=BudgetCapability.RECORDED_ONLY,
        source_field="",
        evidence="recorded from the terminal usage receipt",
        notes=(
            "records actual execution time; an RPH client wait timeout is not this "
            "clock and must never be substituted for it"
        ),
    ),
    ExecutionLimit(
        limit="cumulative_core_hours",
        kind=LIMIT_RECORDED_ONLY,
        carrier="usage/metering receipt (cores x seconds)",
        capability=BudgetCapability.RECORDED_ONLY,
        source_field="core_hours",
        evidence="recorded from trusted receipts; no pre-call hard cap in this release",
        notes="a hard cumulative cap needs an explicit IN_SCOPE work item",
    ),
    ExecutionLimit(
        limit="model_tokens",
        kind=LIMIT_RECORDED_ONLY,
        carrier="C-owned model proxy (RPH client throttle) + recorded usage",
        capability=BudgetCapability.RECORDED_ONLY,
        source_field="input_tokens/output_tokens",
        evidence="client-side throttle only (experience/model-budget.md §4)",
        notes=(
            "platform token enforcement stays NOT_VERIFIED; a client throttle never "
            "becomes a platform enforced claim"
        ),
    ),
)

_LIMITS_BY_NAME = {row.limit: row for row in EXECUTION_LIMITS}

#: Frozen clock contract rows: start, persistence, pause/restart, precedence.
CLOCK_CONTRACT: tuple[dict[str, Any], ...] = (
    {
        "clock": CLOCK_QUEUE,
        "starts_at": "submit admitted / queued",
        "representation": "persistent absolute timestamp (admission + queue_seconds)",
        "persisted": True,
        "paused": "not applicable (not running)",
        "restart": "the absolute deadline is restored unchanged; a restart never resets it",
        "authority": "platform (admission)",
        "capability": BudgetCapability.NOT_VERIFIED,
        "evidence": "expiry -> terminal transition receipt pending (M1-05)",
    },
    {
        "clock": CLOCK_WALL,
        "starts_at": "attempt dispatch (started_at)",
        "representation": "persistent absolute timestamp (started_at + wall_seconds)",
        "persisted": True,
        "paused": "PAUSED time counts by default (DEFAULT_INCLUDE_PAUSED=True)",
        "restart": "only the remaining time is restored; the deadline is never extended",
        "authority": "platform (Controller) + worker receipt",
        "capability": BudgetCapability.NOT_VERIFIED,
        "evidence": "process termination / LSF -W receipt pending (M1-05, M4-03)",
    },
    {
        "clock": CLOCK_SCIENCE_RUNTIME,
        "starts_at": "worker start of the science payload",
        "representation": "measured duration on the usage receipt",
        "persisted": True,
        "paused": "records wall time including pause unless the runtime reports otherwise",
        "restart": "re-measured per attempt; never derived from a client wait timeout",
        "authority": "worker/controller receipt (recorded-only)",
        "capability": BudgetCapability.RECORDED_ONLY,
        "evidence": "terminal usage receipt seconds",
    },
)

#: Precedence rule; the platform resolves a conflict in this order.
CLOCK_PRECEDENCE: tuple[dict[str, str], ...] = (
    {
        "rank": "1",
        "clock": CLOCK_QUEUE,
        "applies_to": "queued attempt, before start",
        "rule": "only the queue deadline may stop a queued attempt",
    },
    {
        "rank": "2",
        "clock": CLOCK_WALL,
        "applies_to": "started attempt",
        "rule": "once started, the wall deadline terminates the attempt",
    },
    {
        "rank": "3",
        "clock": CLOCK_SCIENCE_RUNTIME,
        "applies_to": "every attempt",
        "rule": "records actual runtime only; never terminates and never bypasses rank 1-2",
    },
    {
        "rank": "*",
        "clock": "client/rph",
        "applies_to": "any",
        "rule": (
            "a client wait timeout is not a platform clock; the platform resolves "
            "conflicts by rank 1 -> 2 -> 3 and a client may not choose one to bypass another"
        ),
    },
)

#: Budget keys understood by :func:`bind_budget_shape` (D09 additive shape).
_BUDGET_UNIT_KEYS: tuple[str, ...] = (
    "unit",
    "count",
    "units",
    "unit_limits",
    "core_hours",
)
_BUDGET_TIME_KEYS: tuple[str, ...] = ("timeout_s", "wall_seconds", "queue_seconds")
_BUDGET_TOKEN_KEYS: tuple[str, ...] = ("input_tokens", "output_tokens")
_BUDGET_META_KEYS: tuple[str, ...] = ("timeouts", "unit_basis", "notes")
_BUDGET_KNOWN_KEYS: frozenset[str] = frozenset(
    _BUDGET_UNIT_KEYS + _BUDGET_TIME_KEYS + _BUDGET_TOKEN_KEYS + _BUDGET_META_KEYS
)


@dataclass(frozen=True, slots=True)
class BudgetShape:
    """The normalized D09 budget shape (one interpretation for every side)."""

    wall_seconds: int | None = None
    queue_seconds: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    core_hours: int | None = None
    limits: Mapping[str, int] = field(default_factory=dict)
    timeouts: Mapping[str, float] = field(default_factory=dict)
    recorded_only: Mapping[str, int] = field(default_factory=dict)

    @property
    def bounded(self) -> bool:
        return bool(self.limits or self.wall_seconds or self.queue_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXECUTION_LIMIT_SCHEMA,
            "wall_seconds": self.wall_seconds,
            "queue_seconds": self.queue_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "core_hours": self.core_hours,
            "limits": dict(self.limits),
            "timeouts": dict(self.timeouts),
            "recorded_only": dict(self.recorded_only),
        }


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExecutionLimitError("invalid_budget_shape", f"{label} must be an integer")
    if value < 0:
        raise ExecutionLimitError("invalid_budget_shape", f"{label} must be non-negative")
    return value


def bind_budget_shape(budget: Mapping[str, Any] | None) -> BudgetShape:
    """Normalize one budget into the D09 shape; never drop a known field.

    Mapping (frozen):

    * ``wall_seconds`` -> ``ResourceBounds``/timeout (local + subprocess + LSF),
    * ``queue_seconds`` -> queue deadline,
    * ``input_tokens``/``output_tokens`` -> recorded-only (+ RPH client throttle),
    * ``core_hours`` -> recorded-only,
    * ``unit``/``count``/``units``/``unit_limits`` -> separate unit limits.

    Args:
        budget: The approval/proposal budget mapping.

    Returns:
        The normalized :class:`BudgetShape`.

    Raises:
        ExecutionLimitError: A non-empty unknown key, or an invalid value.  A
            non-empty unknown shape is never parsed into "no limits".
    """
    source = dict(budget or {})
    if not isinstance(budget, Mapping) and budget is not None:
        raise ExecutionLimitError("invalid_budget_shape", "budget must be an object")
    unknown = sorted(str(key) for key in source if key not in _BUDGET_KNOWN_KEYS)
    if unknown:
        raise ExecutionLimitError(
            "unknown_budget_shape",
            "budget carries unknown field(s): " + ", ".join(unknown),
            params={"fields": unknown},
        )

    wall = None
    if source.get("wall_seconds") is not None:
        wall = _non_negative_int(source.get("wall_seconds"), "budget.wall_seconds")
    queue = None
    if source.get("queue_seconds") is not None:
        queue = _non_negative_int(source.get("queue_seconds"), "budget.queue_seconds")
    if source.get("timeout_s") is not None and wall is None:
        wall = _non_negative_int(source.get("timeout_s"), "budget.timeout_s")

    input_tokens = None
    if source.get("input_tokens") is not None:
        input_tokens = _non_negative_int(source.get("input_tokens"), "budget.input_tokens")
    output_tokens = None
    if source.get("output_tokens") is not None:
        output_tokens = _non_negative_int(source.get("output_tokens"), "budget.output_tokens")
    core_hours = None
    if source.get("core_hours") is not None:
        core_hours = _non_negative_int(source.get("core_hours"), "budget.core_hours")

    limits: dict[str, int] = {}
    for group in ("units", "unit_limits"):
        raw = source.get(group)
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise ExecutionLimitError("invalid_budget_shape", f"budget.{group} must be an object")
        for unit, value in raw.items():
            limits[str(unit)] = _non_negative_int(value, f"budget.{group}.{unit}")
    if source.get("unit") is not None and source.get("count") is not None:
        limits[str(source["unit"])] = _non_negative_int(source.get("count"), "budget.count")

    timeouts: dict[str, float] = {}
    raw_timeouts = source.get("timeouts")
    if raw_timeouts is not None:
        if not isinstance(raw_timeouts, Mapping):
            raise ExecutionLimitError("invalid_budget_shape", "budget.timeouts must be an object")
        for key, value in raw_timeouts.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExecutionLimitError(
                    "invalid_budget_shape", f"budget.timeouts.{key} must be a number"
                )
            timeouts[str(key)] = float(value)

    recorded_only: dict[str, int] = {}
    if input_tokens is not None:
        recorded_only["input_tokens"] = input_tokens
    if output_tokens is not None:
        recorded_only["output_tokens"] = output_tokens
    if core_hours is not None:
        recorded_only["core_hours"] = core_hours

    return BudgetShape(
        wall_seconds=wall,
        queue_seconds=queue,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        core_hours=core_hours,
        limits=limits,
        timeouts=timeouts,
        recorded_only=recorded_only,
    )


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """The one resolver's projection onto the three enforcement surfaces."""

    local_terminate_after_s: int | None = None
    ccp_resources_timeout_s: float | None = None
    lsf_walltime_minutes: int | None = None
    queue_deadline_s: int | None = None
    recorded_only: Mapping[str, int] = field(default_factory=dict)
    bounded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXECUTION_LIMIT_SCHEMA,
            "local_terminate_after_s": self.local_terminate_after_s,
            "ccp_resources_timeout_s": self.ccp_resources_timeout_s,
            "lsf_walltime_minutes": self.lsf_walltime_minutes,
            "queue_deadline_s": self.queue_deadline_s,
            "recorded_only": dict(self.recorded_only),
            "bounded": self.bounded,
            "lsf_walltime_limited": self.lsf_walltime_minutes is not None,
        }


def normalize_walltime_s(value: Any) -> int | None:
    """Return a whole-second walltime or ``None`` for "not limited".

    An empty/absent cluster walltime is **not** a limit: ``None`` means the
    deployment did not bound the attempt, never "already limited".
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError as exc:
            raise ExecutionLimitError(
                "invalid_walltime", f"walltime {text!r} is not an integer number of seconds"
            ) from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionLimitError("invalid_walltime", "walltime must be an integer")
    seconds = int(value)
    if seconds <= 0:
        return None
    return seconds


def resolve_resource_limits(
    budget: Mapping[str, Any] | None,
    *,
    timeout_override_s: int | None = None,
) -> ResourceLimits:
    """Map one approval budget onto local termination, CCCP timeout and LSF ``-W``.

    The three surfaces are **the same value** from one resolver; a surface that
    is not bounded stays ``None`` (cluster default empty walltime is not a limit).

    Args:
        budget: The approval/proposal budget (D09 shape).
        timeout_override_s: An explicit, already-verified walltime (for example a
            deployment-level default); it never *widens* ``wall_seconds``.

    Returns:
        The :class:`ResourceLimits` projection.
    """
    shape = bind_budget_shape(budget)
    wall = shape.wall_seconds
    if timeout_override_s is not None:
        override = normalize_walltime_s(timeout_override_s)
        wall = override if wall is None else min(wall, override or wall)
    wall = normalize_walltime_s(wall)
    minutes = None if wall is None else max(1, -(-wall // 60))
    return ResourceLimits(
        local_terminate_after_s=wall,
        # CCCP applies resources.timeout_s to a *subprocess*, so it can never be
        # wider than the attempt deadline; equality is the only safe mapping.
        ccp_resources_timeout_s=None if wall is None else float(wall),
        lsf_walltime_minutes=minutes,
        queue_deadline_s=shape.queue_seconds,
        recorded_only=dict(shape.recorded_only),
        bounded=shape.bounded,
    )


@dataclass(frozen=True, slots=True)
class AttemptClock:
    """The persistent absolute clocks of one attempt (queue + wall)."""

    admitted_at: datetime | None = None
    started_at: datetime | None = None
    queue_deadline_at: datetime | None = None
    wall_deadline_at: datetime | None = None
    include_paused: bool = DEFAULT_INCLUDE_PAUSED
    paused_seconds: int = 0

    def queue_expired(self, now: datetime) -> bool:
        return self.queue_deadline_at is not None and now >= self.queue_deadline_at

    def wall_expired(self, now: datetime) -> bool:
        return self.wall_deadline_at is not None and now >= self.wall_deadline_at

    def remaining_seconds(self, now: datetime) -> int | None:
        """Remaining wall seconds; PAUSED already consumed them by default."""
        if self.wall_deadline_at is None:
            return None
        delta = (self.wall_deadline_at - now).total_seconds()
        return max(0, int(delta))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXECUTION_LIMIT_SCHEMA,
            "admitted_at": _iso(self.admitted_at),
            "started_at": _iso(self.started_at),
            "queue_deadline_at": _iso(self.queue_deadline_at),
            "wall_deadline_at": _iso(self.wall_deadline_at),
            "include_paused": self.include_paused,
            "paused_seconds": self.paused_seconds,
        }


@dataclass(frozen=True, slots=True)
class ClockProjection:
    """Which clock governs an attempt right now, and whether it expired."""

    governing_clock: str
    expired: bool
    remaining_s: int | None = None
    rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "governing_clock": self.governing_clock,
            "expired": self.expired,
            "remaining_s": self.remaining_s,
            "rule": self.rule,
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def compute_attempt_clock(
    *,
    admitted_at: datetime,
    started_at: datetime | None = None,
    wall_seconds: int | None = None,
    queue_seconds: int | None = None,
    paused_seconds: int = 0,
    include_paused: bool = DEFAULT_INCLUDE_PAUSED,
) -> AttemptClock:
    """Compute the persistent absolute deadlines for one attempt.

    ``wall_deadline_at`` is ``started_at + wall_seconds``; when PAUSED time is
    included (the default) the deadline is *not* extended by ``paused_seconds``,
    so a restart only restores the remainder.  Excluding pause requires an
    explicit ``include_paused=False`` and is then the caller's documented policy.
    """
    admitted = _as_utc(admitted_at)
    started = _as_utc(started_at) if started_at is not None else None
    queue_deadline = (
        admitted + timedelta(seconds=queue_seconds) if queue_seconds is not None else None
    )
    wall_deadline = None
    if started is not None and wall_seconds is not None:
        extra = 0 if include_paused else max(0, int(paused_seconds))
        wall_deadline = started + timedelta(seconds=int(wall_seconds) + extra)
    return AttemptClock(
        admitted_at=admitted,
        started_at=started,
        queue_deadline_at=queue_deadline,
        wall_deadline_at=wall_deadline,
        include_paused=include_paused,
        paused_seconds=max(0, int(paused_seconds)),
    )


def clock_projection(clock: AttemptClock, now: datetime) -> ClockProjection:
    """Resolve which of the three clocks governs *now* (frozen precedence).

    A queued attempt (no ``started_at``) is governed by the queue deadline only.
    A started attempt is governed by the wall deadline.  The science-runtime
    clock never governs: it only records.
    """
    current = _as_utc(now)
    if clock.started_at is None:
        if clock.queue_deadline_at is None:
            return ClockProjection(
                governing_clock=CLOCK_QUEUE,
                expired=False,
                remaining_s=None,
                rule="no queue deadline configured; the attempt stays queued",
            )
        remaining = max(0, int((clock.queue_deadline_at - current).total_seconds()))
        return ClockProjection(
            governing_clock=CLOCK_QUEUE,
            expired=current >= clock.queue_deadline_at,
            remaining_s=remaining,
            rule="rank 1: a queued attempt is governed by the queue deadline only",
        )
    remaining = clock.remaining_seconds(current)
    return ClockProjection(
        governing_clock=CLOCK_WALL,
        expired=clock.wall_expired(current),
        remaining_s=remaining,
        rule="rank 2: a started attempt is terminated by the wall deadline",
    )


def limit_row(limit: str) -> ExecutionLimit:
    """Return one registered execution limit or raise."""
    row = _LIMITS_BY_NAME.get(str(limit))
    if row is None:
        raise ExecutionLimitError("unknown_execution_limit", f"{limit!r} is not registered")
    return row


def execution_limits_statement() -> dict[str, Any]:
    """The machine-readable ``execution_limits`` section for ``/api/capabilities``."""
    return {
        "schema": EXECUTION_LIMIT_SCHEMA,
        "limits": [row.to_dict() for row in EXECUTION_LIMITS],
        "clocks": [dict(row) for row in CLOCK_CONTRACT],
        "precedence": [dict(row) for row in CLOCK_PRECEDENCE],
        "budget_shape": {
            "wall_seconds": "ResourceBounds/timeout (local + CCCP subprocess + LSF -W)",
            "queue_seconds": "queue deadline (absolute, from admission)",
            "input_tokens": "recorded-only + RPH client throttle",
            "output_tokens": "recorded-only + RPH client throttle",
            "core_hours": "recorded-only",
            "unknown_non_empty": "typed error (never parsed into empty limits)",
        },
        "enforced": [row.limit for row in EXECUTION_LIMITS if row.capability == "enforced"],
        "recorded_only": [
            row.limit for row in EXECUTION_LIMITS if row.capability == "recorded-only"
        ],
        "not_verified": [
            {"capability": row.limit, "reason": row.evidence}
            for row in EXECUTION_LIMITS
            if row.capability == "NOT_VERIFIED"
        ],
    }
