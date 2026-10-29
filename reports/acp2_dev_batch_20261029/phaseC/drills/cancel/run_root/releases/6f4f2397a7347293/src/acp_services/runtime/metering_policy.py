"""Frozen metering policy contract — auditable core-hour basis (B10-1 / todo 53).

The main plan §20.3 and work package PR-05a require that, **before** any hosted
core-hour metering is switched on, the platform freezes one auditable policy and
never fabricates the rest.  This module is the executable form of that contract.
It decides *how* compute is measured; it deliberately decides nothing about
*money* (prices/Credit are PR-05b and stay gated).

Frozen rules
------------

* **Explicit basis, unresolved by default.**  ``metering_basis`` is one of
  :data:`MeteringBasis.ALLOCATED_CORE_HOURS`
  (``Σ eligible_interval_seconds × allocated_cores / 3600``) or
  :data:`MeteringBasis.ACTUAL_CPU_SECONDS`
  (``Σ confirmed_cpu_seconds / 3600``).  A fresh :class:`MeteringPolicy` carries
  :data:`MeteringBasis.UNRESOLVED`; there is **no built-in default**.  Calling
  :func:`measure` on an unresolved (or otherwise incomplete) policy raises
  :class:`MeteringRefusedError` — metering is refused, not guessed.
* **Never mixed.**  :func:`measure` selects exactly one basis from the policy
  and derives every number from that basis alone; the other measurement
  (``allocated_cores`` for the actual-CPU basis, ``cpu_seconds`` for the
  allocated basis) is carried as evidence metadata only, never added in.
* **Period boundary + timezone are explicit.**  :class:`BillingPeriod` fixes the
  boundary unit (hour/day/month) and the IANA timezone; both must be configured.
  :func:`period_bounds` and :func:`segment_by_period` are pure so a
  cross-period run can be segmented by the caller (todo 55).
* **Rounding is explicit.**  :class:`RoundingSpec` names a mode and a number of
  decimal places; an unresolved rounding refuses metering.
* **Pause / failure / retry / unknown usage.**  Every one of these is
  :data:`IntervalBilling.UNRESOLVED` by default.  An unresolved knob does **not**
  silently include or exclude the interval: the interval is classified
  :data:`ChargeDecision.PENDING_RECONCILIATION` ("待对账") and contributes no
  core hours.  A missing evidence source/version is likewise pending — never
  zero-filled.
* **Queue / transfer / publication are never metered.**  They are excluded by
  construction regardless of the policy (plan §20.3).
* **Executable upper bound frozen before execution.**  :func:`freeze_executable_bound`
  records the allocated cores, walltime, hard-limit capability, policy version
  and the frozen PAUSED-metering decision.  When any of these is missing,
  :func:`admit_metering_mode` **refuses quota-only** with a structured reason;
  the only way past it is an explicit ``usage-only`` administrator choice.

Non-goals (hard)
----------------

* No hidden prices, rates or default policies live here.  The module contains no
  price vocabulary at all.
* The policy is a **platform execution/usage fact**.  It is deliberately *not*
  part of scientific identity (no ``TaskRequest`` / ``options`` / CCCP content
  identity / ACV data model field may derive from it) — see
  :data:`METERING_NOT_SCIENTIFIC_IDENTITY_NOTE`.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
------------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module is stdlib only
plus the sibling :mod:`acp_services.runtime.admission` vocabulary
(``AdmissionMode`` / hard-limit capability constants) so quota-only admission
has exactly one shared definition.

Author: ACP Services (B lane, todo 53).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from acp_services.runtime.admission import (
    HARD_LIMIT_ENFORCED,
    HARD_LIMIT_UNAVAILABLE,
    AdmissionMode,
)

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies
    "MeteringBasis",
    "RoundingRule",
    "PeriodUnit",
    "IntervalKind",
    "IntervalBilling",
    "ChargeDecision",
    "MeteringReason",
    # notes
    "METERING_NOT_SCIENTIFIC_IDENTITY_NOTE",
    "NON_METERED_CATEGORIES",
    # value objects
    "RoundingSpec",
    "BillingPeriod",
    "MeteringPolicy",
    "UsageInterval",
    "IntervalVerdict",
    "MeteringResult",
    "ExecutableBound",
    "MeteringAdmission",
    # errors
    "MeteringError",
    "MeteringValidationError",
    "MeteringRefusedError",
    # functions
    "period_bounds",
    "segment_by_period",
    "quantize_core_hours",
    "classify_interval",
    "measure",
    "freeze_executable_bound",
    "admit_metering_mode",
]

#: Executable statement that the frozen metering policy is a platform fact, not
#: a scientific identity input.  Referenced verbatim by the acceptance tests.
METERING_NOT_SCIENTIFIC_IDENTITY_NOTE = (
    "The metering policy is a platform execution/usage fact and is NOT part of "
    "scientific identity: it must never enter TaskRequest, scientific options, "
    "CCCP content identity, or the ACV data model. It only decides how compute "
    "is measured, never what science is run or how results compare."
)


# ---------------------------------------------------------------------------
# Vocabularies (str-valued so they compare/round-trip as plain strings)
# ---------------------------------------------------------------------------


class MeteringBasis(str):
    """How core hours are computed.

    ``UNRESOLVED`` is the shipped default and refuses metering.  The two real
    bases are mutually exclusive and must never be summed together.
    """

    UNRESOLVED = "unresolved"
    ALLOCATED_CORE_HOURS = "allocated-core-hours"
    ACTUAL_CPU_SECONDS = "actual-cpu-seconds"
    ALL = frozenset({UNRESOLVED, ALLOCATED_CORE_HOURS, ACTUAL_CPU_SECONDS})
    RESOLVED = frozenset({ALLOCATED_CORE_HOURS, ACTUAL_CPU_SECONDS})


class RoundingRule(str):
    """Rounding mode for the derived core-hour value (explicit, no default)."""

    UNRESOLVED = "unresolved"
    HALF_UP = "half-up"
    HALF_EVEN = "half-even"
    FLOOR = "floor"
    CEIL = "ceil"
    ALL = frozenset({UNRESOLVED, HALF_UP, HALF_EVEN, FLOOR, CEIL})


class PeriodUnit(str):
    """Billing-period boundary unit (explicit, no default)."""

    UNRESOLVED = "unresolved"
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    ALL = frozenset({UNRESOLVED, HOUR, DAY, MONTH})


class IntervalKind(str):
    """The category of one usage interval.

    ``NON_METERED`` categories (queue wait, data transfer, result publication)
    never count as compute (plan §20.3) and are excluded unconditionally.
    """

    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    RETRY = "retry"
    QUEUED = "queued"
    TRANSFER = "transfer"
    PUBLICATION = "publication"
    UNKNOWN = "unknown"
    ALL = frozenset({RUNNING, PAUSED, FAILED, RETRY, QUEUED, TRANSFER, PUBLICATION, UNKNOWN})
    NON_METERED = frozenset({QUEUED, TRANSFER, PUBLICATION})


#: Convenience export mirroring :data:`IntervalKind.NON_METERED`.
NON_METERED_CATEGORIES = IntervalKind.NON_METERED


class IntervalBilling(str):
    """Per-knob decision for pause / failure / retry intervals.

    ``UNRESOLVED`` (the default) does not mean "excluded": the interval goes to
    pending reconciliation instead, so an unconfirmed policy can never quietly
    shrink or inflate a bill.
    """

    UNRESOLVED = "unresolved"
    EXCLUDED = "excluded"
    INCLUDED = "included"
    ALL = frozenset({UNRESOLVED, EXCLUDED, INCLUDED})


class ChargeDecision(str):
    """Outcome of classifying one interval for metering."""

    METERED = "metered"
    EXCLUDED = "excluded"
    PENDING_RECONCILIATION = "pending_reconciliation"
    ALL = frozenset({METERED, EXCLUDED, PENDING_RECONCILIATION})


class MeteringReason(str):
    """Structured metering refusal / admission reason vocabulary."""

    # metering-policy gates
    METERING_BASIS_UNRESOLVED = "metering_basis_unresolved"
    ROUNDING_UNRESOLVED = "rounding_unresolved"
    PERIOD_UNRESOLVED = "period_unresolved"

    # billing-mode admission
    ADMITTED = "admitted"
    USAGE_ONLY = "usage_only"
    NO_POLICY_VERSION = "no_policy_version"
    UNBOUNDED_REQUEST = "unbounded_request"
    HARD_LIMIT_UNAVAILABLE = "hard_limit_unavailable"

    ALL = frozenset(
        {
            METERING_BASIS_UNRESOLVED,
            ROUNDING_UNRESOLVED,
            PERIOD_UNRESOLVED,
            ADMITTED,
            USAGE_ONLY,
            NO_POLICY_VERSION,
            UNBOUNDED_REQUEST,
            HARD_LIMIT_UNAVAILABLE,
        }
    )


_ROUNDING_MODES = {
    RoundingRule.HALF_UP: ROUND_HALF_UP,
    RoundingRule.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingRule.FLOOR: ROUND_FLOOR,
    RoundingRule.CEIL: ROUND_CEILING,
}

_SECONDS_PER_HOUR = Decimal(3600)
_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MeteringError(RuntimeError):
    """Base error for the metering policy contract."""


class MeteringValidationError(MeteringError):
    """A policy/interval value is structurally invalid (fail closed)."""


class MeteringRefusedError(MeteringError):
    """Metering was refused because the policy is not executable.

    Carries a machine-readable :attr:`reason_code` (from :class:`MeteringReason`)
    so callers branch on the code, not on the message.
    """

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        message = self.reason_code if not detail else f"{self.reason_code}: {detail}"
        super().__init__(message)


# ---------------------------------------------------------------------------
# Small validation helpers
# ---------------------------------------------------------------------------


def _validate_vocab(value: Any, allowed: frozenset[str], name: str) -> str:
    text = str(value)
    if text not in allowed:
        raise MeteringValidationError(f"{name}={value!r} is not one of {sorted(allowed)}")
    return text


def _require_aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise MeteringValidationError(f"{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise MeteringValidationError(f"{name} must be timezone-aware")
    return value


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundingSpec:
    """Rounding mode + decimal places for the derived core-hour value.

    ``decimal_places is None`` until the rule is resolved; a resolved rule
    requires an explicit decimal count.  There is no shipped default rule.
    """

    rule: str = RoundingRule.UNRESOLVED
    decimal_places: int | None = None

    def __post_init__(self) -> None:
        _validate_vocab(self.rule, RoundingRule.ALL, "rounding.rule")
        if self.rule == RoundingRule.UNRESOLVED:
            if self.decimal_places is not None:
                raise MeteringValidationError(
                    "rounding.decimal_places must be None while rounding is unresolved"
                )
            return
        if self.decimal_places is None:
            raise MeteringValidationError(
                "rounding.decimal_places is required once rounding is resolved"
            )
        if int(self.decimal_places) < 0:
            raise MeteringValidationError("rounding.decimal_places must be non-negative")

    @property
    def resolved(self) -> bool:
        return self.rule != RoundingRule.UNRESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "decimal_places": self.decimal_places}


@dataclass(frozen=True)
class BillingPeriod:
    """Billing-period boundary unit + IANA timezone (both explicit).

    An unresolved (or timezone-less) period refuses metering; there is no
    implicit local/UTC default because the boundary changes the ledger split.
    """

    unit: str = PeriodUnit.UNRESOLVED
    timezone: str = ""

    def __post_init__(self) -> None:
        _validate_vocab(self.unit, PeriodUnit.ALL, "period.unit")
        tz = str(self.timezone).strip()
        if self.unit == PeriodUnit.UNRESOLVED:
            if tz:
                raise MeteringValidationError(
                    "period.timezone must be empty while the period unit is unresolved"
                )
            return
        if not tz:
            raise MeteringValidationError(
                "period.timezone is required once the period unit is resolved"
            )
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MeteringValidationError(f"unknown timezone {tz!r}") from exc

    @property
    def resolved(self) -> bool:
        return self.unit != PeriodUnit.UNRESOLVED and bool(self.timezone.strip())

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.unit, "timezone": self.timezone}


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeteringPolicy:
    """The frozen, versioned metering policy.

    Every knob defaults to "unresolved" so a caller cannot accidentally meter
    with a hidden default.  :meth:`require_measurable` is the single gate that
    :func:`measure` consults.
    """

    policy_version: str
    basis: str = MeteringBasis.UNRESOLVED
    rounding: RoundingSpec = field(default_factory=RoundingSpec)
    period: BillingPeriod = field(default_factory=BillingPeriod)
    pause: str = IntervalBilling.UNRESOLVED
    failure: str = IntervalBilling.UNRESOLVED
    retry: str = IntervalBilling.UNRESOLVED

    def __post_init__(self) -> None:
        if not str(self.policy_version).strip():
            raise MeteringValidationError(
                "policy_version is required; an unversioned policy cannot be frozen"
            )
        _validate_vocab(self.basis, MeteringBasis.ALL, "basis")
        _validate_vocab(self.pause, IntervalBilling.ALL, "pause")
        _validate_vocab(self.failure, IntervalBilling.ALL, "failure")
        _validate_vocab(self.retry, IntervalBilling.ALL, "retry")

    # -- readiness -----------------------------------------------------------

    def require_measurable(self) -> None:
        """Raise :class:`MeteringRefusedError` unless metering can proceed.

        Gates, in order: an explicit basis, an explicit rounding rule, and a
        resolved period boundary + timezone.  Pause/failure/retry may stay
        unresolved — affected intervals then reconcile rather than refuse the
        whole measurement.
        """
        if self.basis == MeteringBasis.UNRESOLVED:
            raise MeteringRefusedError(
                MeteringReason.METERING_BASIS_UNRESOLVED,
                "metering_basis is unresolved; choose allocated-core-hours or "
                "actual-cpu-seconds explicitly (no built-in default)",
            )
        if not self.rounding.resolved:
            raise MeteringRefusedError(
                MeteringReason.ROUNDING_UNRESOLVED,
                "rounding rule is unresolved; freeze mode and decimal places",
            )
        if not self.period.resolved:
            raise MeteringRefusedError(
                MeteringReason.PERIOD_UNRESOLVED,
                "period unit/timezone is unresolved; freeze the billing boundary",
            )

    @property
    def measurable(self) -> bool:
        try:
            self.require_measurable()
        except MeteringRefusedError:
            return False
        return True

    def is_fully_resolved(self) -> bool:
        """Whether every knob (including pause/failure/retry) is decided."""
        return (
            self.measurable
            and self.pause != IntervalBilling.UNRESOLVED
            and self.failure != IntervalBilling.UNRESOLVED
            and self.retry != IntervalBilling.UNRESOLVED
        )

    # -- snapshot ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """A canonical, serialisable snapshot of the frozen policy."""
        return {
            "policy_version": self.policy_version,
            "basis": self.basis,
            "rounding": self.rounding.to_dict(),
            "period": self.period.to_dict(),
            "pause": self.pause,
            "failure": self.failure,
            "retry": self.retry,
        }

    def snapshot_digest(self) -> str:
        """Stable SHA-256 digest of :meth:`snapshot` (audit / drift guard)."""
        canonical = json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = self.snapshot()
        out["snapshot_digest"] = self.snapshot_digest()
        out["measurable"] = self.measurable
        out["fully_resolved"] = self.is_fully_resolved()
        return out


# ---------------------------------------------------------------------------
# Period boundaries (pure)
# ---------------------------------------------------------------------------


def _zone(period: BillingPeriod) -> ZoneInfo:
    if not period.resolved:
        raise MeteringRefusedError(
            MeteringReason.PERIOD_UNRESOLVED,
            "billing period unit/timezone is unresolved",
        )
    try:
        return ZoneInfo(period.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover (defensive)
        raise MeteringValidationError(f"unknown timezone {period.timezone!r}") from exc


def _period_start(local: datetime, unit: str) -> datetime:
    if unit == PeriodUnit.HOUR:
        return local.replace(minute=0, second=0, microsecond=0)
    if unit == PeriodUnit.DAY:
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == PeriodUnit.MONTH:
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise MeteringRefusedError(
        MeteringReason.PERIOD_UNRESOLVED, f"unsupported period unit {unit!r}"
    )


def _period_end(start: datetime, unit: str) -> datetime:
    if unit == PeriodUnit.HOUR:
        return start + timedelta(hours=1)
    if unit == PeriodUnit.DAY:
        return start + timedelta(days=1)
    if unit == PeriodUnit.MONTH:
        if start.month == 12:
            return start.replace(year=start.year + 1, month=1)
        return start.replace(month=start.month + 1)
    raise MeteringRefusedError(
        MeteringReason.PERIOD_UNRESOLVED, f"unsupported period unit {unit!r}"
    )


def period_bounds(moment: datetime, period: BillingPeriod) -> tuple[datetime, datetime]:
    """Return the half-open ``[start, end)`` billing period containing *moment*.

    The boundary is computed in the policy timezone; *moment* may be in any
    timezone.  Raises :class:`MeteringRefusedError` when the period is
    unresolved and :class:`MeteringValidationError` for a naive datetime.
    """
    tz = _zone(period)
    _require_aware(moment, "moment")
    local = moment.astimezone(tz)
    start = _period_start(local, period.unit)
    end = _period_end(start, period.unit)
    return start, end


def segment_by_period(
    start: datetime, end: datetime, period: BillingPeriod
) -> tuple[tuple[datetime, datetime], ...]:
    """Split ``[start, end)`` at every billing-period boundary it crosses.

    Pure and order-preserving: the concatenated segments exactly cover the
    input; a zero-length interval yields no segments.
    """
    _require_aware(start, "start")
    _require_aware(end, "end")
    if end < start:
        raise MeteringValidationError("end must not precede start")
    segments: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        _, boundary = period_bounds(cursor, period)
        seg_end = boundary if boundary < end else end
        if seg_end <= cursor:  # pragma: no cover - defensive
            raise MeteringValidationError("period boundary did not advance")
        segments.append((cursor, seg_end))
        cursor = seg_end
    return tuple(segments)


# ---------------------------------------------------------------------------
# Rounding function
# ---------------------------------------------------------------------------


def quantize_core_hours(value: Decimal, spec: RoundingSpec) -> Decimal:
    """Quantize *value* with *spec*; refuse when the rounding is unresolved."""
    if not spec.resolved or spec.decimal_places is None:
        raise MeteringRefusedError(
            MeteringReason.ROUNDING_UNRESOLVED,
            "rounding rule is unresolved; cannot quantize core hours",
        )
    exponent = Decimal(1).scaleb(-int(spec.decimal_places))
    return Decimal(value).quantize(exponent, rounding=_ROUNDING_MODES[spec.rule])


# ---------------------------------------------------------------------------
# Usage intervals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageInterval:
    """One measured execution interval (evidence-bound).

    ``allocated_cores`` and ``cpu_seconds`` default to ``None`` (unknown);
    passing no value is *not* a zero — the interval reconciles instead.  A
    metered interval must carry an evidence source + version.
    """

    start: datetime
    end: datetime
    kind: str = IntervalKind.RUNNING
    allocated_cores: int | None = None
    cpu_seconds: Decimal | None = None
    evidence_source: str = ""
    evidence_version: str = ""

    def __post_init__(self) -> None:
        _require_aware(self.start, "start")
        _require_aware(self.end, "end")
        if self.end < self.start:
            raise MeteringValidationError("interval end must not precede start")
        _validate_vocab(self.kind, IntervalKind.ALL, "kind")
        if self.allocated_cores is not None and int(self.allocated_cores) < 0:
            raise MeteringValidationError("allocated_cores must be non-negative")
        if self.cpu_seconds is not None and Decimal(self.cpu_seconds) < 0:
            raise MeteringValidationError("cpu_seconds must be non-negative")

    @property
    def seconds(self) -> Decimal:
        """Exact duration in seconds (no float round-trip)."""
        delta = self.end - self.start
        whole = Decimal(delta.days * 86400 + delta.seconds)
        return whole + Decimal(delta.microseconds) / _MICROSECONDS_PER_SECOND

    @property
    def evidence_present(self) -> bool:
        return bool(str(self.evidence_source).strip() and str(self.evidence_version).strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "kind": self.kind,
            "allocated_cores": self.allocated_cores,
            "cpu_seconds": None if self.cpu_seconds is None else str(self.cpu_seconds),
            "evidence_source": self.evidence_source,
            "evidence_version": self.evidence_version,
        }


@dataclass(frozen=True)
class IntervalVerdict:
    """Classification of one interval (auditable per-interval record)."""

    decision: str
    reason: str
    kind: str
    seconds: Decimal
    allocated_cores: int | None
    cpu_seconds: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "kind": self.kind,
            "seconds": str(self.seconds),
            "allocated_cores": self.allocated_cores,
            "cpu_seconds": None if self.cpu_seconds is None else str(self.cpu_seconds),
        }


def _policy_decision(
    value: str, *, unresolved_reason: str, excluded_reason: str
) -> tuple[str, str]:
    if value == IntervalBilling.UNRESOLVED:
        return ChargeDecision.PENDING_RECONCILIATION, unresolved_reason
    if value == IntervalBilling.EXCLUDED:
        return ChargeDecision.EXCLUDED, excluded_reason
    return ChargeDecision.METERED, "eligible"


def classify_interval(interval: UsageInterval, policy: MeteringPolicy) -> IntervalVerdict:
    """Classify one interval into metered / excluded / pending.

    Non-metered categories are excluded unconditionally; an unknown kind or a
    missing evidence source/version is pending reconciliation; pause/failure/
    retry follow their policy knob (unresolved => pending).
    """
    kind = interval.kind
    decision: str
    reason: str
    if kind in IntervalKind.NON_METERED:
        decision, reason = ChargeDecision.EXCLUDED, "non_metered_category"
    elif kind == IntervalKind.UNKNOWN:
        decision, reason = ChargeDecision.PENDING_RECONCILIATION, "unknown_usage"
    elif not interval.evidence_present:
        decision, reason = ChargeDecision.PENDING_RECONCILIATION, "evidence_missing"
    elif kind == IntervalKind.RUNNING:
        decision, reason = ChargeDecision.METERED, "eligible"
    elif kind == IntervalKind.PAUSED:
        decision, reason = _policy_decision(
            policy.pause, unresolved_reason="pause_unresolved", excluded_reason="pause_excluded"
        )
    elif kind == IntervalKind.FAILED:
        decision, reason = _policy_decision(
            policy.failure,
            unresolved_reason="failure_unresolved",
            excluded_reason="failure_excluded",
        )
    elif kind == IntervalKind.RETRY:
        decision, reason = _policy_decision(
            policy.retry, unresolved_reason="retry_unresolved", excluded_reason="retry_excluded"
        )
    else:  # pragma: no cover - exhaustive over IntervalKind.ALL
        decision, reason = ChargeDecision.PENDING_RECONCILIATION, "unknown_usage"
    return IntervalVerdict(
        decision=decision,
        reason=reason,
        kind=kind,
        seconds=interval.seconds,
        allocated_cores=interval.allocated_cores,
        cpu_seconds=interval.cpu_seconds,
    )


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeteringResult:
    """The auditable outcome of one metering measurement.

    Exactly one :attr:`basis` is used; :attr:`mixed_bases` is always ``False``
    by construction and exists as an executable guard.
    """

    policy_version: str
    basis: str
    core_hours: Decimal
    raw_core_hours: Decimal
    rounding: RoundingSpec
    metered_seconds: Decimal
    metered_intervals: int
    excluded_intervals: int
    pending_intervals: int
    pending: tuple[IntervalVerdict, ...] = ()
    mixed_bases: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "basis": self.basis,
            "core_hours": str(self.core_hours),
            "raw_core_hours": str(self.raw_core_hours),
            "rounding": self.rounding.to_dict(),
            "metered_seconds": str(self.metered_seconds),
            "metered_intervals": self.metered_intervals,
            "excluded_intervals": self.excluded_intervals,
            "pending_intervals": self.pending_intervals,
            "pending": [item.to_dict() for item in self.pending],
            "mixed_bases": self.mixed_bases,
        }


def measure(
    intervals: list[UsageInterval] | tuple[UsageInterval, ...],
    policy: MeteringPolicy,
) -> MeteringResult:
    """Measure core hours from *intervals* under the frozen *policy*.

    Refuses (raising :class:`MeteringRefusedError`) when the policy is not
    measurable.  The chosen basis drives the whole computation; the other
    measurement is never added in, so the two core-hour bases can never mix.
    """
    policy.require_measurable()
    basis = policy.basis

    verdicts: list[IntervalVerdict] = []
    metered_seconds = Decimal(0)
    raw = Decimal(0)
    metered = excluded = pending = 0

    for interval in intervals:
        verdict = classify_interval(interval, policy)
        decision = verdict.decision
        reason = verdict.reason
        if decision == ChargeDecision.METERED:
            if basis == MeteringBasis.ALLOCATED_CORE_HOURS and interval.allocated_cores is None:
                decision, reason = ChargeDecision.PENDING_RECONCILIATION, "allocated_cores_missing"
            elif basis == MeteringBasis.ACTUAL_CPU_SECONDS and interval.cpu_seconds is None:
                decision, reason = (
                    ChargeDecision.PENDING_RECONCILIATION,
                    "actual_cpu_seconds_missing",
                )
        verdict = IntervalVerdict(
            decision=decision,
            reason=reason,
            kind=verdict.kind,
            seconds=verdict.seconds,
            allocated_cores=verdict.allocated_cores,
            cpu_seconds=verdict.cpu_seconds,
        )
        verdicts.append(verdict)

        if decision == ChargeDecision.METERED:
            metered += 1
            metered_seconds += interval.seconds
            if basis == MeteringBasis.ALLOCATED_CORE_HOURS:
                raw += interval.seconds * Decimal(int(interval.allocated_cores))
            else:
                raw += Decimal(interval.cpu_seconds)
        elif decision == ChargeDecision.EXCLUDED:
            excluded += 1
        else:
            pending += 1

    raw_core_hours = raw / _SECONDS_PER_HOUR
    core_hours = quantize_core_hours(raw_core_hours, policy.rounding)
    pending_verdicts = tuple(
        item for item in verdicts if item.decision == ChargeDecision.PENDING_RECONCILIATION
    )

    logger.info(
        "metering measured: basis=%s policy=%s core_hours=%s metered=%d excluded=%d pending=%d",
        basis,
        policy.policy_version,
        core_hours,
        metered,
        excluded,
        pending,
    )
    return MeteringResult(
        policy_version=policy.policy_version,
        basis=basis,
        core_hours=core_hours,
        raw_core_hours=raw_core_hours,
        rounding=policy.rounding,
        metered_seconds=metered_seconds,
        metered_intervals=metered,
        excluded_intervals=excluded,
        pending_intervals=pending,
        pending=pending_verdicts,
        mixed_bases=False,
    )


# ---------------------------------------------------------------------------
# Executable upper bound + billing-mode admission
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutableBound:
    """The upper bound frozen *before* execution.

    ``cores`` and ``walltime_s`` are the allocated per-job ceiling; the
    hard-limit capability states whether the execution mechanism can actually
    enforce a ceiling; ``policy_version`` and ``pause_metering`` snapshot the
    governing policy so a later change cannot silently re-price the run.
    """

    policy_version: str = ""
    cores: int | None = None
    walltime_s: int | None = None
    hard_limit_capability: str = HARD_LIMIT_UNAVAILABLE
    pause_metering: str = IntervalBilling.UNRESOLVED
    queues: tuple[str, ...] | None = None
    nodes: tuple[str, ...] | None = None

    def missing_dimensions(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.cores is None:
            missing.append("cores")
        if self.walltime_s is None:
            missing.append("walltime_s")
        return tuple(missing)

    @property
    def executable(self) -> bool:
        return (
            not self.missing_dimensions()
            and self.hard_limit_capability == HARD_LIMIT_ENFORCED
            and bool(str(self.policy_version).strip())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "cores": self.cores,
            "walltime_s": self.walltime_s,
            "hard_limit_capability": self.hard_limit_capability,
            "pause_metering": self.pause_metering,
            "queues": None if self.queues is None else list(self.queues),
            "nodes": None if self.nodes is None else list(self.nodes),
            "executable": self.executable,
            "missing_dimensions": list(self.missing_dimensions()),
        }


def freeze_executable_bound(
    policy: MeteringPolicy,
    *,
    cores: int | None,
    walltime_s: int | None,
    hard_limit_capability: str = HARD_LIMIT_UNAVAILABLE,
    queues: tuple[str, ...] | None = None,
    nodes: tuple[str, ...] | None = None,
) -> ExecutableBound:
    """Freeze the executable upper bound for one execution under *policy*.

    The policy version and the PAUSED-metering decision are copied from
    *policy* so the bound is self-describing.  ``None`` cores/walltime are
    allowed here and surface as missing dimensions at admission time.
    """
    if cores is not None and int(cores) < 0:
        raise MeteringValidationError("cores must be non-negative")
    if walltime_s is not None and int(walltime_s) < 0:
        raise MeteringValidationError("walltime_s must be non-negative")
    capability = str(hard_limit_capability)
    if capability not in {HARD_LIMIT_ENFORCED, HARD_LIMIT_UNAVAILABLE}:
        raise MeteringValidationError(
            f"hard_limit_capability {capability!r} must be "
            f"{HARD_LIMIT_ENFORCED!r} or {HARD_LIMIT_UNAVAILABLE!r}"
        )
    return ExecutableBound(
        policy_version=policy.policy_version,
        cores=None if cores is None else int(cores),
        walltime_s=None if walltime_s is None else int(walltime_s),
        hard_limit_capability=capability,
        pause_metering=policy.pause,
        queues=queues,
        nodes=nodes,
    )


@dataclass(frozen=True)
class MeteringAdmission:
    """Structured verdict for choosing a billing mode against a frozen bound."""

    admitted: bool
    reason_code: str
    mode: str
    detail: str = ""
    bound: ExecutableBound | None = None

    @property
    def enforced(self) -> bool:
        return self.admitted and self.mode == AdmissionMode.QUOTA_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "mode": self.mode,
            "detail": self.detail,
            "bound": None if self.bound is None else self.bound.to_dict(),
            "enforced": self.enforced,
        }


def admit_metering_mode(
    bound: ExecutableBound,
    *,
    mode: str = AdmissionMode.QUOTA_ONLY,
) -> MeteringAdmission:
    """Decide whether *mode* is admissible given the frozen *bound*.

    ``usage-only`` is the explicit administrator escape hatch and is always
    admitted.  ``quota-only`` requires a policy version, an enforced hard-limit
    capability and a finite ``cores`` + ``walltime_s`` ceiling; a missing
    dimension refuses with :data:`MeteringReason.UNBOUNDED_REQUEST`.
    """
    resolved_mode = _validate_vocab(mode, AdmissionMode.ALL, "mode")
    if resolved_mode == AdmissionMode.USAGE_ONLY:
        return MeteringAdmission(
            admitted=True,
            reason_code=MeteringReason.USAGE_ONLY,
            mode=resolved_mode,
            detail="usage-only admitted by explicit administrator choice",
            bound=bound,
        )
    if not str(bound.policy_version).strip():
        return MeteringAdmission(
            admitted=False,
            reason_code=MeteringReason.NO_POLICY_VERSION,
            mode=resolved_mode,
            detail="quota-only refused: no versioned metering policy",
            bound=bound,
        )
    missing = bound.missing_dimensions()
    if missing:
        return MeteringAdmission(
            admitted=False,
            reason_code=MeteringReason.UNBOUNDED_REQUEST,
            mode=resolved_mode,
            detail=(
                "quota-only refused: no executable upper bound for "
                f"{', '.join(missing)} (only explicit usage-only is allowed)"
            ),
            bound=bound,
        )
    if bound.hard_limit_capability != HARD_LIMIT_ENFORCED:
        return MeteringAdmission(
            admitted=False,
            reason_code=MeteringReason.HARD_LIMIT_UNAVAILABLE,
            mode=resolved_mode,
            detail="quota-only refused: no enforced hard-limit capability",
            bound=bound,
        )
    return MeteringAdmission(
        admitted=True,
        reason_code=MeteringReason.ADMITTED,
        mode=resolved_mode,
        detail="quota-only admitted with a frozen executable upper bound",
        bound=bound,
    )
