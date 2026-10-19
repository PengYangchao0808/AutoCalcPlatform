"""Core-hour quota modes — explicit usage-only / quota-only enforcement (B10-3).

(todo 55; plan §23.4 / MU-10a.)  This module is the *decision* half of common
core-hour accounting.  It consumes the frozen metering policy (todo 53), the
trusted usage record (todo 54) and the resource-admission vocabulary (todo 49)
and turns them into an auditable, pre-dispatch quota decision.

Two modes, no hidden default
----------------------------

* :data:`QuotaMode.USAGE_ONLY` — **record/alert only**.  Nothing is enforced; the
  platform merely records what was consumed and raises an alert.  Because this
  disables over-use prevention it requires an **explicit administrator
  selection** (:attr:`QuotaModeSelection.admin_selected`); a plain user cannot
  drop into it.
* :data:`QuotaMode.QUOTA_ONLY` — the **default research baseline**.  The
  effective budget is the intersection of the user and project budgets
  (``min`` — never a sum), and each dispatch first reserves an **executable**
  upper bound (``cores × walltime / 3600`` core-hours) atomically.  When that
  bound cannot be produced (missing cores/walltime, no enforced hard limit, no
  versioned policy) the admission is refused — only an explicit usage-only
  administrator choice gets past it.

A startup with **no explicit selection** is refused
:class:`QuotaStartupRefusedError`.  There is no built-in “default mode”; the
baseline still has to be selected and snapshotted.

Frozen rules
------------

* **Reserve before dispatch, never post-hoc.**  Over-use is prevented by the
  atomic executable-cap reservation, not by statistics gathered afterwards —
  see :data:`QUOTA_PRE_DISPATCH_NOTE`.
* **Write-off against confirmed usage.**  A reservation is settled only against
  a confirmable :class:`~acp_services.runtime.usage.UsageRecord`; the unused part
  of the reserved ceiling is released back to the budget.  An over-run (confirmed
  usage above the frozen executable cap) is **refused**, never settled late —
  “no over-use then settle”.
* **Unknown usage reconciles.**  A pending record leaves the reservation held in
  :data:`QuotaState.PENDING`; **lease expiry alone never releases** a reservation.
* **Cross-period segmentation.**  A reservation whose window crosses a billing
  boundary is split by the frozen period rule
  (:func:`~acp_services.runtime.metering_policy.segment_by_period`); both the
  reservation and the write-off are attributed per period.
* **Restart recovery.**  :meth:`QuotaService.recover` replays every undecided
  reservation and re-applies authorized-but-unapplied top-ups — it never releases
  on a timer.
* **Budget top-up is ordered.**  A top-up first atomically appends the
  authorization **and** the reserved hold, and only then extends the running
  allowance; if the extension fails the authorization and hold persist and the
  allowance is *not* extended, so the budget can never be over-used before the
  accounting catches up.
* **Exhaustion per approved policy.**  Exhaustion refuses **new** dispatches; it
  never kills in-flight work and never deletes produced results (see
  :class:`ExhaustionPolicy`).
* **Mode switch keeps history.**  :meth:`QuotaService.switch_mode` appends a new
  policy snapshot; it never clears the previous ones.

Non-goals (hard)
----------------

* No prices, rates or Credit vocabulary (pricing is PR-05b and stays gated).
* The quota is a platform execution/usage fact, **not** scientific identity — see
  :data:`QUOTA_NOT_SCIENTIFIC_IDENTITY_NOTE`.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
-------------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module is stdlib plus the
sibling runtime contracts (:mod:`metering_policy`, :mod:`usage`,
:mod:`admission`) and the Runtime UTC helper.

Author: ACP Services (B lane, todo 55).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from acp_services.runtime.admission import (
    HARD_LIMIT_ENFORCED,
    AdmissionMode,
)
from acp_services.runtime.helpers import utc_now_iso
from acp_services.runtime.metering_policy import (
    BillingPeriod,
    ExecutableBound,
    MeteringPolicy,
    admit_metering_mode,
    freeze_executable_bound,
    segment_by_period,
)
from acp_services.runtime.usage import ReconciliationStatus, UsageRecord

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies
    "QuotaMode",
    "QuotaState",
    "QuotaReason",
    "ExhaustionAction",
    # notes
    "QUOTA_NOT_SCIENTIFIC_IDENTITY_NOTE",
    "QUOTA_PRE_DISPATCH_NOTE",
    # value objects
    "QuotaModeSelection",
    "PolicySnapshot",
    "QuotaSegment",
    "QuotaReservation",
    "QuotaDecision",
    "WriteOffResult",
    "TopUpResult",
    "RecoveryReport",
    "ExhaustionPolicy",
    # errors
    "QuotaError",
    "QuotaValidationError",
    "QuotaStartupRefusedError",
    "QuotaReservationNotFoundError",
    "QuotaOverrunError",
    # functions / helpers
    "effective_core_hours",
    # ledger + service
    "QuotaLedger",
    "QuotaService",
]

#: Executable statement that a quota decision is a platform fact, not a
#: scientific identity input.  Referenced verbatim by the acceptance tests.
QUOTA_NOT_SCIENTIFIC_IDENTITY_NOTE = (
    "A core-hour quota/reservation is a platform execution/usage fact and is NOT "
    "part of scientific identity: it must never be written into TaskRequest, "
    "scientific options, CCCP content identity, or the ACV data model."
)

#: Executable statement that over-use is stopped before dispatch, not after.
QUOTA_PRE_DISPATCH_NOTE = (
    "Over-use is prevented by an atomic executable-cap reservation taken BEFORE "
    "dispatch; post-hoc usage statistics are never used to prevent over-use."
)

_SECONDS_PER_HOUR = Decimal(3600)
_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class QuotaMode(str):
    """The two core-hour accounting modes.

    Values deliberately mirror :class:`~acp_services.runtime.admission.AdmissionMode`
    so a mode string is shared with the resource-admission core.
    """

    QUOTA_ONLY = AdmissionMode.QUOTA_ONLY
    USAGE_ONLY = AdmissionMode.USAGE_ONLY
    ALL = AdmissionMode.ALL


class QuotaState(str):
    """Reservation lifecycle state.

    ``PENDING`` holds the reservation because the usage is unconfirmed; it is
    *not* released and **lease expiry alone never moves it to** ``RELEASED``.
    """

    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    PENDING = "pending_reconciliation"
    SETTLED = "settled"
    RELEASED = "released"
    ACTIVE = frozenset({RESERVED, DISPATCHED, PENDING, SETTLED})
    #: States whose held core-hours are counted against the budget.
    HELD = frozenset({RESERVED, DISPATCHED, PENDING, SETTLED})
    #: Undecided states replayed on restart.
    UNDECIDED = frozenset({RESERVED, DISPATCHED, PENDING})


class QuotaReason(str):
    """Structured quota decision / refusal reason vocabulary."""

    ADMITTED = "admitted"
    RECORD_ONLY = "record_only"
    SETTLED = "settled"
    USAGE_PENDING = "usage_pending_reconciliation"
    # admission gates
    MODE_NOT_SELECTED = "mode_not_selected"
    NO_FROZEN_POLICY = "no_frozen_policy"
    NO_BUDGET = "no_budget_configured"
    # budget gates
    INSUFFICIENT_BUDGET = "insufficient_budget"
    EXHAUSTED = "exhausted"
    OVERRUN = "usage_exceeds_reservation"
    # lifecycle
    RESERVATION_NOT_FOUND = "reservation_not_found"
    RESERVATION_RELEASED = "reservation_released"
    ALREADY_SETTLED = "already_settled"

    ALL = frozenset(
        {
            ADMITTED,
            RECORD_ONLY,
            SETTLED,
            USAGE_PENDING,
            MODE_NOT_SELECTED,
            NO_FROZEN_POLICY,
            NO_BUDGET,
            INSUFFICIENT_BUDGET,
            EXHAUSTED,
            OVERRUN,
            RESERVATION_NOT_FOUND,
            RESERVATION_RELEASED,
            ALREADY_SETTLED,
        }
    )


class ExhaustionAction(str):
    """Approved action when an account is exhausted (default: refuse new)."""

    REFUSE_NEW = "refuse-new"
    ALL = frozenset({REFUSE_NEW})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QuotaError(RuntimeError):
    """Base error for core-hour quota enforcement."""


class QuotaValidationError(QuotaError):
    """A quota value is structurally invalid (fail closed)."""


class QuotaStartupRefusedError(QuotaError):
    """No explicit mode was selected, so the quota service refuses to start."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(self.reason_code if not detail else f"{self.reason_code}: {detail}")


class QuotaReservationNotFoundError(QuotaError):
    """The referenced reservation does not exist."""


class QuotaOverrunError(QuotaError):
    """Confirmed usage exceeded the frozen executable reservation (refused)."""

    def __init__(self, reservation_id: str, reserved: Decimal, confirmed: Decimal) -> None:
        self.reservation_id = str(reservation_id)
        self.reserved = Decimal(reserved)
        self.confirmed = Decimal(confirmed)
        super().__init__(
            f"{QuotaReason.OVERRUN}: reservation {self.reservation_id} reserved "
            f"{self.reserved} but usage confirmed {self.confirmed}; refused (no "
            "over-use-then-settle)"
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value).strip())


def _span_seconds(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    whole = Decimal(delta.days * 86400 + delta.seconds)
    return whole + Decimal(delta.microseconds) / _MICROSECONDS_PER_SECOND


def _split_by_seconds(total: Decimal, seconds: Sequence[Decimal]) -> list[Decimal]:
    """Split *total* proportionally to *seconds* with the remainder on the last."""
    if not seconds:
        return []
    denominator = sum(seconds, Decimal(0))
    if denominator <= 0:
        return [Decimal(0) for _ in seconds]
    out: list[Decimal] = []
    allocated = Decimal(0)
    last = len(seconds) - 1
    for index, sec in enumerate(seconds):
        if index == last:
            out.append(total - allocated)
        else:
            part = total * sec / denominator
            out.append(part)
            allocated += part
    return out


def effective_core_hours(
    user_core_hours: Decimal | None, project_core_hours: Decimal | None
) -> Decimal | None:
    """The effective budget as the ``min`` intersection of the two scopes.

    ``None`` means "no ceiling from this scope"; when both are ``None`` the
    effective budget is unbounded (``None``) and quota-only must refuse.
    """
    candidates = [value for value in (user_core_hours, project_core_hours) if value is not None]
    if not candidates:
        return None
    return min(Decimal(v) for v in candidates)


# ---------------------------------------------------------------------------
# Selection + snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaModeSelection:
    """An explicit operator choice of the core-hour accounting mode.

    Construction fails closed: the mode must be known, ``selected_by`` must name
    the selecting operator (no implicit default) and a frozen
    :class:`MeteringPolicy` must be supplied so the choice is auditable.
    ``usage-only`` additionally requires ``admin_selected=True``.
    """

    mode: str
    selected_by: str
    admin_selected: bool = False
    policy: MeteringPolicy | None = None

    def __post_init__(self) -> None:
        if str(self.mode) not in QuotaMode.ALL:
            raise QuotaValidationError(
                f"unknown quota mode {self.mode!r}; expected one of {sorted(QuotaMode.ALL)}"
            )
        if not str(self.selected_by).strip():
            raise QuotaValidationError(
                "an explicit selection requires selected_by (no implicit default mode)"
            )
        if self.policy is None:
            raise QuotaValidationError("a frozen metering policy is required for a selection")
        if str(self.mode) == QuotaMode.USAGE_ONLY and not self.admin_selected:
            raise QuotaValidationError(
                "usage-only disables over-use prevention and requires an explicit "
                "administrator selection"
            )

    @property
    def explicit(self) -> bool:
        return bool(str(self.selected_by).strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "selected_by": self.selected_by,
            "admin_selected": self.admin_selected,
            "policy_version": None if self.policy is None else self.policy.policy_version,
        }


@dataclass(frozen=True)
class PolicySnapshot:
    """An immutable, append-only record of one mode+policy choice."""

    snapshot_id: str
    mode: str
    policy_version: str
    policy_digest: str
    selected_by: str
    admin_selected: bool
    created_at: str
    snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "mode": self.mode,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "selected_by": self.selected_by,
            "admin_selected": self.admin_selected,
            "created_at": self.created_at,
            "snapshot": dict(self.snapshot),
        }


# ---------------------------------------------------------------------------
# Reservation / decision value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaSegment:
    """One billing-period slice of a reservation (cross-period segmentation)."""

    index: int
    start: datetime
    end: datetime
    reserved_core_hours: Decimal
    written_off_core_hours: Decimal = Decimal(0)

    @property
    def seconds(self) -> Decimal:
        return _span_seconds(self.start, self.end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "seconds": str(self.seconds),
            "reserved_core_hours": str(self.reserved_core_hours),
            "written_off_core_hours": str(self.written_off_core_hours),
        }


@dataclass(frozen=True)
class QuotaReservation:
    """A pre-dispatch core-hour reservation with its live lifecycle state."""

    reservation_id: str
    job_id: str
    attempt: int
    principal_id: str
    project_id: str
    mode: str
    state: str
    policy_version: str
    policy_digest: str
    reserved_core_hours: Decimal
    settled_core_hours: Decimal
    cores: int
    walltime_s: int
    window_start: datetime
    window_end: datetime
    period_unit: str
    period_timezone: str
    external_job_id: str | None = None
    result_ref: str | None = None
    lease_until: str = ""
    created_at: str = ""
    updated_at: str = ""
    segments: tuple[QuotaSegment, ...] = ()

    @property
    def held_core_hours(self) -> Decimal:
        """Core-hours this reservation currently holds against the budget."""
        if self.state in (QuotaState.RESERVED, QuotaState.DISPATCHED, QuotaState.PENDING):
            return self.reserved_core_hours
        if self.state == QuotaState.SETTLED:
            return self.settled_core_hours
        return Decimal(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "mode": self.mode,
            "state": self.state,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "reserved_core_hours": str(self.reserved_core_hours),
            "settled_core_hours": str(self.settled_core_hours),
            "cores": self.cores,
            "walltime_s": self.walltime_s,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "period_unit": self.period_unit,
            "period_timezone": self.period_timezone,
            "external_job_id": self.external_job_id,
            "result_ref": self.result_ref,
            "lease_until": self.lease_until,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "segments": [item.to_dict() for item in self.segments],
        }


@dataclass(frozen=True)
class QuotaDecision:
    """Structured quota verdict for one pre-dispatch admission."""

    admitted: bool
    reason_code: str
    mode: str
    detail: str = ""
    record_only: bool = False
    reservation_id: str | None = None
    reserved_core_hours: Decimal | None = None
    available_core_hours: Decimal | None = None
    bound: ExecutableBound | None = None
    segments: tuple[QuotaSegment, ...] = ()
    new_dispatch_allowed: bool = True
    in_flight_killed: bool = False
    results_readable: bool = True
    alerts: tuple[str, ...] = ()

    @property
    def enforced(self) -> bool:
        return self.admitted and self.mode == QuotaMode.QUOTA_ONLY

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "mode": self.mode,
            "detail": self.detail,
            "record_only": self.record_only,
            "reservation_id": self.reservation_id,
            "reserved_core_hours": (
                None if self.reserved_core_hours is None else str(self.reserved_core_hours)
            ),
            "available_core_hours": (
                None if self.available_core_hours is None else str(self.available_core_hours)
            ),
            "bound": None if self.bound is None else self.bound.to_dict(),
            "segments": [item.to_dict() for item in self.segments],
            "new_dispatch_allowed": self.new_dispatch_allowed,
            "in_flight_killed": self.in_flight_killed,
            "results_readable": self.results_readable,
            "alerts": list(self.alerts),
            "enforced": self.enforced,
        }


@dataclass(frozen=True)
class WriteOffResult:
    """Outcome of settling a reservation against a usage record."""

    reservation_id: str
    state: str
    reason_code: str
    settled_core_hours: Decimal
    released_core_hours: Decimal
    pending: bool
    segments: tuple[QuotaSegment, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "state": self.state,
            "reason_code": self.reason_code,
            "settled_core_hours": str(self.settled_core_hours),
            "released_core_hours": str(self.released_core_hours),
            "pending": self.pending,
            "segments": [item.to_dict() for item in self.segments],
        }


@dataclass(frozen=True)
class TopUpResult:
    """Outcome of a budget top-up, preserving the applied phase ordering."""

    topup_id: str
    scope_type: str
    scope_id: str
    amount: Decimal
    phases: tuple[str, ...]
    applied: bool
    budget_core_hours: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topup_id": self.topup_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "amount": str(self.amount),
            "phases": list(self.phases),
            "applied": self.applied,
            "budget_core_hours": (
                None if self.budget_core_hours is None else str(self.budget_core_hours)
            ),
        }


@dataclass(frozen=True)
class RecoveryReport:
    """The replayed state after a restart (nothing released by lease expiry)."""

    replayed: tuple[QuotaReservation, ...] = ()
    pending_topups: tuple[str, ...] = ()
    reapplied_topups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "replayed": [item.to_dict() for item in self.replayed],
            "pending_topups": list(self.pending_topups),
            "reapplied_topups": list(self.reapplied_topups),
        }


@dataclass(frozen=True)
class ExhaustionPolicy:
    """The approved exhaustion policy.

    Exhaustion refuses **new** dispatches, never kills in-flight work and never
    deletes produced results (which stay readable).
    """

    action: str = ExhaustionAction.REFUSE_NEW
    kill_in_flight: bool = False
    results_readable: bool = True

    def __post_init__(self) -> None:
        if str(self.action) not in ExhaustionAction.ALL:
            raise QuotaValidationError(f"unknown exhaustion action {self.action!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "kill_in_flight": self.kill_in_flight,
            "results_readable": self.results_readable,
        }


# ---------------------------------------------------------------------------
# SQLite ledger
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quota_policy_snapshots (
    snapshot_id      TEXT PRIMARY KEY,
    mode             TEXT NOT NULL,
    policy_version   TEXT NOT NULL,
    policy_digest    TEXT NOT NULL,
    selected_by      TEXT NOT NULL,
    admin_selected   INTEGER NOT NULL DEFAULT 0,
    snapshot_json    TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quota_budgets (
    scope_type   TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    core_hours   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS quota_reservations (
    reservation_id      TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL,
    attempt             INTEGER NOT NULL DEFAULT 1,
    principal_id        TEXT NOT NULL,
    project_id          TEXT NOT NULL,
    mode                TEXT NOT NULL,
    state               TEXT NOT NULL,
    policy_version      TEXT NOT NULL,
    policy_digest       TEXT NOT NULL,
    reserved_core_hours TEXT NOT NULL,
    settled_core_hours  TEXT NOT NULL DEFAULT '0',
    cores               INTEGER NOT NULL DEFAULT 0,
    walltime_s          INTEGER NOT NULL DEFAULT 0,
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    period_unit         TEXT NOT NULL,
    period_timezone     TEXT NOT NULL,
    external_job_id     TEXT,
    result_ref          TEXT,
    lease_until         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_reservations_scope
    ON quota_reservations(state, principal_id, project_id);
CREATE TABLE IF NOT EXISTS quota_segments (
    reservation_id          TEXT NOT NULL,
    idx                     INTEGER NOT NULL,
    start                   TEXT NOT NULL,
    end                     TEXT NOT NULL,
    reserved_core_hours     TEXT NOT NULL,
    written_off_core_hours  TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (reservation_id, idx)
);
CREATE TABLE IF NOT EXISTS quota_topups (
    topup_id      TEXT PRIMARY KEY,
    scope_type    TEXT NOT NULL,
    scope_id      TEXT NOT NULL,
    amount        TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    state         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    applied_at    TEXT
);
CREATE TABLE IF NOT EXISTS quota_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    subject    TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

_TOPUP_AUTHORIZED = "authorized"
_TOPUP_APPLIED = "applied"


def _row_to_reservation(row: sqlite3.Row) -> QuotaReservation:
    return QuotaReservation(
        reservation_id=row["reservation_id"],
        job_id=row["job_id"],
        attempt=int(row["attempt"]),
        principal_id=row["principal_id"],
        project_id=row["project_id"],
        mode=row["mode"],
        state=row["state"],
        policy_version=row["policy_version"],
        policy_digest=row["policy_digest"],
        reserved_core_hours=_dec(row["reserved_core_hours"]),
        settled_core_hours=_dec(row["settled_core_hours"]),
        cores=int(row["cores"]),
        walltime_s=int(row["walltime_s"]),
        window_start=datetime.fromisoformat(row["window_start"]),
        window_end=datetime.fromisoformat(row["window_end"]),
        period_unit=row["period_unit"],
        period_timezone=row["period_timezone"],
        external_job_id=row["external_job_id"],
        result_ref=row["result_ref"],
        lease_until=row["lease_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True)
class _ReservationSpec:
    job_id: str
    attempt: int
    principal_id: str
    project_id: str
    cores: int
    walltime_s: int
    reserved_core_hours: Decimal
    policy_version: str
    policy_digest: str
    window_start: datetime
    window_end: datetime
    period: BillingPeriod
    external_job_id: str | None = None
    result_ref: str | None = None
    lease_until: str = ""


@dataclass(frozen=True)
class _ReserveOutcome:
    reserved: bool
    reason_code: str
    detail: str = ""
    reservation: QuotaReservation | None = None
    available_core_hours: Decimal | None = None


class QuotaLedger:
    """SQLite-backed core-hour ledger with atomic check-and-insert.

    Every size-changing write runs inside ``BEGIN IMMEDIATE`` so the occupancy read
    and the reservation insert are one transaction: a concurrent admission can
    never reserve against a stale, pre-commit view.
    """

    def __init__(
        self,
        db_path: str,
        *,
        clock: Callable[[], str] | None = None,
        reservation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        from pathlib import Path

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or utc_now_iso
        self._new_id = reservation_id_factory or (lambda: "qres_" + uuid.uuid4().hex)
        self._lock = threading.Lock()
        self._init_schema()

    # -- connection / schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(_SCHEMA_SQL)
            finally:
                conn.close()

    # -- policy snapshots (append-only) -------------------------------------

    def record_snapshot(self, selection: QuotaModeSelection) -> PolicySnapshot:
        if selection.policy is None:  # pragma: no cover - guarded by selection
            raise QuotaValidationError("selection has no policy")
        snapshot_id = "qsnap_" + uuid.uuid4().hex
        now = self._clock()
        snapshot = selection.policy.snapshot()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO quota_policy_snapshots (
                           snapshot_id, mode, policy_version, policy_digest,
                           selected_by, admin_selected, snapshot_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot_id,
                        str(selection.mode),
                        str(selection.policy.policy_version),
                        selection.policy.snapshot_digest(),
                        str(selection.selected_by),
                        1 if selection.admin_selected else 0,
                        json.dumps(snapshot, sort_keys=True),
                        now,
                    ),
                )
            finally:
                conn.close()
        return PolicySnapshot(
            snapshot_id=snapshot_id,
            mode=str(selection.mode),
            policy_version=str(selection.policy.policy_version),
            policy_digest=selection.policy.snapshot_digest(),
            selected_by=str(selection.selected_by),
            admin_selected=bool(selection.admin_selected),
            created_at=now,
            snapshot=snapshot,
        )

    def list_snapshots(self) -> list[PolicySnapshot]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM quota_policy_snapshots ORDER BY rowid"
                ).fetchall()
            finally:
                conn.close()
        return [
            PolicySnapshot(
                snapshot_id=row["snapshot_id"],
                mode=row["mode"],
                policy_version=row["policy_version"],
                policy_digest=row["policy_digest"],
                selected_by=row["selected_by"],
                admin_selected=bool(row["admin_selected"]),
                created_at=row["created_at"],
                snapshot=json.loads(row["snapshot_json"]),
            )
            for row in rows
        ]

    # -- budgets -------------------------------------------------------------

    def set_budget(self, scope_type: str, scope_id: str, core_hours: Decimal) -> None:
        if scope_type not in ("user", "project"):
            raise QuotaValidationError(f"unknown budget scope {scope_type!r}")
        amount = _dec(core_hours)
        if amount < 0:
            raise QuotaValidationError("budget must be non-negative")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._upsert_budget(conn, scope_type, scope_id, amount)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    @staticmethod
    def _upsert_budget(
        conn: sqlite3.Connection, scope_type: str, scope_id: str, amount: Decimal
    ) -> None:
        conn.execute(
            """INSERT INTO quota_budgets (scope_type, scope_id, core_hours, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(scope_type, scope_id)
               DO UPDATE SET core_hours=excluded.core_hours, updated_at=excluded.updated_at""",
            (scope_type, scope_id, str(amount), utc_now_iso()),
        )

    def get_budget(self, scope_type: str, scope_id: str) -> Decimal | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT core_hours FROM quota_budgets WHERE scope_type=? AND scope_id=?",
                    (scope_type, scope_id),
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else _dec(row["core_hours"])

    def extend_budget(self, scope_type: str, scope_id: str, amount: Decimal) -> Decimal:
        """Add *amount* to a scope budget atomically; return the new total."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT core_hours FROM quota_budgets WHERE scope_type=? AND scope_id=?",
                    (scope_type, scope_id),
                ).fetchone()
                current = Decimal(0) if row is None else _dec(row["core_hours"])
                updated = current + _dec(amount)
                conn.execute(
                    """INSERT INTO quota_budgets (scope_type, scope_id, core_hours, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(scope_type, scope_id)
                       DO UPDATE SET core_hours=excluded.core_hours,
                                     updated_at=excluded.updated_at""",
                    (scope_type, scope_id, str(updated), self._clock()),
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return updated

    # -- occupancy -----------------------------------------------------------

    def _consumed(self, conn: sqlite3.Connection, scope_type: str, scope_id: str) -> Decimal:
        if scope_type == "user":
            reservation_rows = conn.execute(
                "SELECT * FROM quota_reservations WHERE state != ? AND principal_id=?",
                (QuotaState.RELEASED, scope_id),
            ).fetchall()
        else:
            reservation_rows = conn.execute(
                "SELECT * FROM quota_reservations WHERE state != ? AND project_id=?",
                (QuotaState.RELEASED, scope_id),
            ).fetchall()
        held = sum((_row_to_reservation(r).held_core_hours for r in reservation_rows), Decimal(0))
        hold_rows = conn.execute(
            "SELECT amount FROM quota_topups WHERE state=? AND scope_type=? AND scope_id=?",
            (_TOPUP_AUTHORIZED, scope_type, scope_id),
        ).fetchall()
        holds = sum((_dec(r["amount"]) for r in hold_rows), Decimal(0))
        return held + holds

    def consumed(self, scope_type: str, scope_id: str) -> Decimal:
        with self._lock:
            conn = self._connect()
            try:
                return self._consumed(conn, scope_type, scope_id)
            finally:
                conn.close()

    def available(self, principal_id: str, project_id: str) -> Decimal | None:
        """Effective remaining budget = ``min`` of the user/project remainders."""
        with self._lock:
            conn = self._connect()
            try:
                user_budget = self._budget_in(conn, "user", principal_id)
                project_budget = self._budget_in(conn, "project", project_id)
                remainders: list[Decimal] = []
                if user_budget is not None:
                    remainders.append(user_budget - self._consumed(conn, "user", principal_id))
                if project_budget is not None:
                    remainders.append(project_budget - self._consumed(conn, "project", project_id))
            finally:
                conn.close()
        if not remainders:
            return None
        return min(remainders)

    @staticmethod
    def _budget_in(conn: sqlite3.Connection, scope_type: str, scope_id: str) -> Decimal | None:
        row = conn.execute(
            "SELECT core_hours FROM quota_budgets WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        ).fetchone()
        return None if row is None else _dec(row["core_hours"])

    # -- atomic reserve ------------------------------------------------------

    def reserve(
        self,
        spec: _ReservationSpec,
        *,
        user_budget: Decimal | None,
        project_budget: Decimal | None,
    ) -> _ReserveOutcome:
        now = self._clock()
        segments = self._build_segments(spec)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if user_budget is None and project_budget is None:
                    conn.execute("ROLLBACK")
                    return _ReserveOutcome(False, QuotaReason.NO_BUDGET, "no budget configured")

                user_consumed = self._consumed(conn, "user", spec.principal_id)
                project_consumed = self._consumed(conn, "project", spec.project_id)
                looks_exhausted = False
                if user_budget is not None:
                    if user_consumed >= user_budget:
                        looks_exhausted = True
                    elif user_consumed + spec.reserved_core_hours > user_budget:
                        conn.execute("ROLLBACK")
                        return _ReserveOutcome(
                            False,
                            QuotaReason.INSUFFICIENT_BUDGET,
                            f"user budget {user_budget} - consumed {user_consumed} < "
                            f"requested {spec.reserved_core_hours}",
                        )
                if project_budget is not None:
                    if project_consumed >= project_budget:
                        looks_exhausted = True
                    elif project_consumed + spec.reserved_core_hours > project_budget:
                        conn.execute("ROLLBACK")
                        return _ReserveOutcome(
                            False,
                            QuotaReason.INSUFFICIENT_BUDGET,
                            f"project budget {project_budget} - consumed {project_consumed} < "
                            f"requested {spec.reserved_core_hours}",
                        )
                if looks_exhausted:
                    conn.execute("ROLLBACK")
                    return _ReserveOutcome(
                        False,
                        QuotaReason.EXHAUSTED,
                        "account exhausted; new dispatch refused (in-flight preserved)",
                    )

                reservation_id = self._new_id()
                conn.execute(
                    """INSERT INTO quota_reservations (
                           reservation_id, job_id, attempt, principal_id, project_id, mode,
                           state, policy_version, policy_digest, reserved_core_hours,
                           settled_core_hours, cores, walltime_s, window_start, window_end,
                           period_unit, period_timezone, external_job_id, result_ref,
                           lease_until, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0', ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        reservation_id,
                        spec.job_id,
                        int(spec.attempt),
                        spec.principal_id,
                        spec.project_id,
                        QuotaMode.QUOTA_ONLY,
                        QuotaState.RESERVED,
                        spec.policy_version,
                        spec.policy_digest,
                        str(spec.reserved_core_hours),
                        int(spec.cores),
                        int(spec.walltime_s),
                        spec.window_start.isoformat(),
                        spec.window_end.isoformat(),
                        spec.period.unit,
                        spec.period.timezone,
                        spec.external_job_id,
                        spec.result_ref,
                        spec.lease_until,
                        now,
                        now,
                    ),
                )
                for segment in segments:
                    conn.execute(
                        """INSERT INTO quota_segments (
                               reservation_id, idx, start, end,
                               reserved_core_hours, written_off_core_hours)
                           VALUES (?, ?, ?, ?, ?, '0')""",
                        (
                            reservation_id,
                            segment.index,
                            segment.start.isoformat(),
                            segment.end.isoformat(),
                            str(segment.reserved_core_hours),
                        ),
                    )
                self._emit(conn, reservation_id, "reserve", str(spec.reserved_core_hours))
                available = self._available_in(
                    conn, spec.principal_id, spec.project_id, user_budget, project_budget
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        reservation = self.get(reservation_id)
        return _ReserveOutcome(True, QuotaReason.ADMITTED, "reserved", reservation, available)

    def _available_in(
        self,
        conn: sqlite3.Connection,
        principal_id: str,
        project_id: str,
        user_budget: Decimal | None,
        project_budget: Decimal | None,
    ) -> Decimal | None:
        remainders: list[Decimal] = []
        if user_budget is not None:
            remainders.append(user_budget - self._consumed(conn, "user", principal_id))
        if project_budget is not None:
            remainders.append(project_budget - self._consumed(conn, "project", project_id))
        return min(remainders) if remainders else None

    @staticmethod
    def _build_segments(spec: _ReservationSpec) -> list[QuotaSegment]:
        spans = segment_by_period(spec.window_start, spec.window_end, spec.period)
        seconds = [_span_seconds(start, end) for start, end in spans]
        amounts = _split_by_seconds(spec.reserved_core_hours, seconds)
        return [
            QuotaSegment(index=index, start=start, end=end, reserved_core_hours=amount)
            for index, ((start, end), amount) in enumerate(zip(spans, amounts))
        ]

    # -- reads ---------------------------------------------------------------

    def get(self, reservation_id: str) -> QuotaReservation | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM quota_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    return None
                segments = self._segments_in(conn, reservation_id)
            finally:
                conn.close()
        return replace(_row_to_reservation(row), segments=segments)

    def _segments_in(
        self, conn: sqlite3.Connection, reservation_id: str
    ) -> tuple[QuotaSegment, ...]:
        rows = conn.execute(
            "SELECT * FROM quota_segments WHERE reservation_id=? ORDER BY idx",
            (reservation_id,),
        ).fetchall()
        return tuple(
            QuotaSegment(
                index=int(row["idx"]),
                start=datetime.fromisoformat(row["start"]),
                end=datetime.fromisoformat(row["end"]),
                reserved_core_hours=_dec(row["reserved_core_hours"]),
                written_off_core_hours=_dec(row["written_off_core_hours"]),
            )
            for row in rows
        )

    def list_reservations(
        self, *, job_id: str | None = None, states: Sequence[str] | None = None
    ) -> list[QuotaReservation]:
        clauses: list[str] = []
        params: list[Any] = []
        if job_id is not None:
            clauses.append("job_id=?")
            params.append(job_id)
        if states is not None:
            placeholders = ",".join("?" for _ in states)
            clauses.append(f"state IN ({placeholders})")
            params.extend(states)
        query = "SELECT reservation_id FROM quota_reservations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, reservation_id"
        with self._lock:
            conn = self._connect()
            try:
                ids = [row["reservation_id"] for row in conn.execute(query, params).fetchall()]
            finally:
                conn.close()
        return [item for rid in ids if (item := self.get(rid)) is not None]

    def list_undecided(self) -> list[QuotaReservation]:
        """Reservations replayed on restart (reserved / dispatched / pending)."""
        return self.list_reservations(states=sorted(QuotaState.UNDECIDED))

    # -- state transitions ---------------------------------------------------

    def set_state(self, reservation_id: str, *, state: str, detail: str = "") -> QuotaReservation:
        if state not in QuotaState.ACTIVE and state != QuotaState.RELEASED:
            raise QuotaValidationError(f"unknown reservation state {state!r}")
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM quota_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise QuotaReservationNotFoundError(reservation_id)
                if row["state"] == QuotaState.RELEASED:
                    conn.execute("ROLLBACK")
                    raise QuotaReservationNotFoundError(reservation_id)
                conn.execute(
                    "UPDATE quota_reservations SET state=?, updated_at=? WHERE reservation_id=?",
                    (state, now, reservation_id),
                )
                self._emit(conn, reservation_id, f"state:{state}", detail)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        updated = self.get(reservation_id)
        assert updated is not None
        return updated

    def release(self, reservation_id: str) -> bool:
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE quota_reservations SET state=?, updated_at=? "
                    "WHERE reservation_id=? AND state != ?",
                    (QuotaState.RELEASED, now, reservation_id, QuotaState.RELEASED),
                )
                if cursor.rowcount == 1:
                    self._emit(conn, reservation_id, "release", "")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return cursor.rowcount == 1

    def settle(
        self,
        reservation_id: str,
        record: UsageRecord,
        *,
        confirmed_core_hours: Decimal,
    ) -> WriteOffResult:
        """Atomically write off *confirmed_core_hours* against the reservation."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM quota_reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise QuotaReservationNotFoundError(reservation_id)
                reservation = _row_to_reservation(row)
                if reservation.state == QuotaState.RELEASED:
                    conn.execute("ROLLBACK")
                    return WriteOffResult(
                        reservation_id,
                        QuotaState.RELEASED,
                        QuotaReason.RESERVATION_RELEASED,
                        Decimal(0),
                        Decimal(0),
                        False,
                    )
                if reservation.state == QuotaState.SETTLED:
                    segments = self._segments_in(conn, reservation_id)
                    conn.execute("COMMIT")
                    return WriteOffResult(
                        reservation_id,
                        QuotaState.SETTLED,
                        QuotaReason.ALREADY_SETTLED,
                        reservation.settled_core_hours,
                        reservation.reserved_core_hours - reservation.settled_core_hours,
                        False,
                        segments,
                    )

                pending = (
                    record.reconciliation_status == ReconciliationStatus.PENDING_RECONCILIATION
                    or not record.confirmable
                )
                if pending:
                    conn.execute(
                        "UPDATE quota_reservations SET state=?, updated_at=? "
                        "WHERE reservation_id=?",
                        (QuotaState.PENDING, now, reservation_id),
                    )
                    self._emit(conn, reservation_id, "pending_reconciliation", record.usage_id)
                    pending_segments = self._segments_in(conn, reservation_id)
                    conn.execute("COMMIT")
                    return WriteOffResult(
                        reservation_id,
                        QuotaState.PENDING,
                        QuotaReason.USAGE_PENDING,
                        Decimal(0),
                        Decimal(0),
                        True,
                        pending_segments,
                    )

                if confirmed_core_hours > reservation.reserved_core_hours:
                    conn.execute(
                        "UPDATE quota_reservations SET state=?, updated_at=? "
                        "WHERE reservation_id=?",
                        (QuotaState.PENDING, now, reservation_id),
                    )
                    self._emit(
                        conn,
                        reservation_id,
                        "overrun",
                        f"reserved={reservation.reserved_core_hours} "
                        f"confirmed={confirmed_core_hours}",
                    )
                    conn.execute("COMMIT")
                    raise QuotaOverrunError(
                        reservation_id, reservation.reserved_core_hours, confirmed_core_hours
                    )

                existing_segments = self._segments_in(conn, reservation_id)
                written = self._allocate_write_off(
                    existing_segments,
                    record.interval_start,
                    record.interval_end,
                    confirmed_core_hours,
                )
                for segment in written:
                    conn.execute(
                        "UPDATE quota_segments SET written_off_core_hours=? "
                        "WHERE reservation_id=? AND idx=?",
                        (str(segment.written_off_core_hours), reservation_id, segment.index),
                    )
                conn.execute(
                    "UPDATE quota_reservations SET state=?, settled_core_hours=?, updated_at=? "
                    "WHERE reservation_id=?",
                    (QuotaState.SETTLED, str(confirmed_core_hours), now, reservation_id),
                )
                self._emit(conn, reservation_id, "write_off", record.usage_id)
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return WriteOffResult(
            reservation_id,
            QuotaState.SETTLED,
            QuotaReason.SETTLED,
            confirmed_core_hours,
            reservation.reserved_core_hours - confirmed_core_hours,
            False,
            tuple(written),
        )

    @staticmethod
    def _allocate_write_off(
        segments: Sequence[QuotaSegment],
        record_start: datetime,
        record_end: datetime,
        confirmed_core_hours: Decimal,
    ) -> list[QuotaSegment]:
        overlaps: list[Decimal] = []
        for segment in segments:
            start = max(segment.start, record_start)
            end = min(segment.end, record_end)
            overlaps.append(_span_seconds(start, end) if end > start else Decimal(0))
        amounts = _split_by_seconds(confirmed_core_hours, overlaps)
        return [
            replace(segment, written_off_core_hours=amount)
            for segment, amount in zip(segments, amounts)
        ]

    # -- top-ups (ordered: authorize+reserve, then extend) -------------------

    def begin_topup(
        self,
        scope_type: str,
        scope_id: str,
        amount: Decimal,
        authorized_by: str,
    ) -> str:
        if scope_type not in ("user", "project"):
            raise QuotaValidationError(f"unknown top-up scope {scope_type!r}")
        if _dec(amount) <= 0:
            raise QuotaValidationError("top-up amount must be positive")
        if not str(authorized_by).strip():
            raise QuotaValidationError("top-up requires an authorizing operator")
        topup_id = "qtop_" + uuid.uuid4().hex
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO quota_topups (
                           topup_id, scope_type, scope_id, amount, authorized_by,
                           state, created_at, applied_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        topup_id,
                        scope_type,
                        scope_id,
                        str(_dec(amount)),
                        authorized_by,
                        _TOPUP_AUTHORIZED,
                        now,
                    ),
                )
                self._emit(conn, topup_id, "authorize", str(_dec(amount)))
                self._emit(conn, topup_id, "reserve", str(_dec(amount)))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return topup_id

    def apply_topup(self, topup_id: str) -> Decimal:
        """Extend the budget and mark applied in one transaction."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM quota_topups WHERE topup_id=?", (topup_id,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise QuotaValidationError(f"unknown top-up {topup_id!r}")
                if row["state"] == _TOPUP_APPLIED:
                    budget = self._budget_in(conn, row["scope_type"], row["scope_id"])
                    conn.execute("COMMIT")
                    return Decimal(0) if budget is None else budget
                row_budget = self._budget_in(conn, row["scope_type"], row["scope_id"])
                current = Decimal(0) if row_budget is None else row_budget
                updated = current + _dec(row["amount"])
                conn.execute(
                    """INSERT INTO quota_budgets (scope_type, scope_id, core_hours, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(scope_type, scope_id)
                       DO UPDATE SET core_hours=excluded.core_hours,
                                     updated_at=excluded.updated_at""",
                    (row["scope_type"], row["scope_id"], str(updated), now),
                )
                conn.execute(
                    "UPDATE quota_topups SET state=?, applied_at=? WHERE topup_id=?",
                    (_TOPUP_APPLIED, now, topup_id),
                )
                self._emit(conn, topup_id, "extend", str(_dec(row["amount"])))
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
        return updated

    def mark_topup_applied(self, topup_id: str) -> None:
        """Mark an externally-extended top-up applied (order preserved)."""
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE quota_topups SET state=?, applied_at=? WHERE topup_id=?",
                    (_TOPUP_APPLIED, now, topup_id),
                )
                self._emit(conn, topup_id, "extend", "")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def topup_state(self, topup_id: str) -> str:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT state FROM quota_topups WHERE topup_id=?", (topup_id,)
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            raise QuotaValidationError(f"unknown top-up {topup_id!r}")
        return str(row["state"])

    def list_topups(self, *, state: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                if state is None:
                    rows = conn.execute("SELECT * FROM quota_topups ORDER BY rowid").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM quota_topups WHERE state=? ORDER BY rowid", (state,)
                    ).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def topup_events(self, topup_id: str) -> tuple[str, ...]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT event FROM quota_events WHERE subject=? ORDER BY seq", (topup_id,)
                ).fetchall()
            finally:
                conn.close()
        return tuple(str(row["event"]) for row in rows)

    # -- events --------------------------------------------------------------

    @staticmethod
    def _emit(conn: sqlite3.Connection, subject: str, event: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO quota_events (subject, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (subject, event, detail, utc_now_iso()),
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class QuotaService:
    """Enforce the selected core-hour mode with pre-dispatch reservations.

    Args:
        ledger: The atomic :class:`QuotaLedger`.
        user_budgets: Optional initial per-user budgets (core-hours).
        project_budgets: Optional initial per-project budgets (core-hours).
        exhaustion_policy: The approved exhaustion policy.
        alert_sink: Optional sink for usage-only alerts.
        clock: Optional ``() -> str`` timestamp override.
    """

    def __init__(
        self,
        ledger: QuotaLedger,
        *,
        user_budgets: Mapping[str, Decimal] | None = None,
        project_budgets: Mapping[str, Decimal] | None = None,
        exhaustion_policy: ExhaustionPolicy | None = None,
        alert_sink: Callable[[str], None] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._ledger = ledger
        self._exhaustion = exhaustion_policy or ExhaustionPolicy()
        self._alert_sink = alert_sink
        self._clock = clock or utc_now_iso
        self._selection: QuotaModeSelection | None = None
        for scope_id, amount in (user_budgets or {}).items():
            ledger.set_budget("user", scope_id, amount)
        for scope_id, amount in (project_budgets or {}).items():
            ledger.set_budget("project", scope_id, amount)

    # -- startup -------------------------------------------------------------

    def startup(self, selection: QuotaModeSelection | None) -> QuotaModeSelection:
        """Explicitly select the mode; refuse to start without a choice."""
        if selection is None or not selection.explicit:
            raise QuotaStartupRefusedError(
                QuotaReason.MODE_NOT_SELECTED,
                "no explicit quota mode was selected; startup refused",
            )
        policy = selection.policy
        if policy is None:
            raise QuotaStartupRefusedError(QuotaReason.NO_FROZEN_POLICY)
        if str(selection.mode) == QuotaMode.QUOTA_ONLY:
            policy.require_measurable()
        self._selection = selection
        self._ledger.record_snapshot(selection)
        return selection

    @property
    def mode(self) -> str:
        self._require_started()
        assert self._selection is not None
        return str(self._selection.mode)

    @property
    def selection(self) -> QuotaModeSelection:
        self._require_started()
        assert self._selection is not None
        return self._selection

    def switch_mode(self, selection: QuotaModeSelection) -> PolicySnapshot:
        """Select a new mode; the previous policy snapshots are never cleared."""
        self._require_started()
        if str(selection.mode) == QuotaMode.QUOTA_ONLY and selection.policy is not None:
            selection.policy.require_measurable()
        self._selection = selection
        return self._ledger.record_snapshot(selection)

    def snapshots(self) -> list[PolicySnapshot]:
        return self._ledger.list_snapshots()

    # -- admission -----------------------------------------------------------

    def admit(
        self,
        *,
        job_id: str,
        principal_id: str,
        project_id: str,
        cores: int,
        walltime_s: int,
        window_start: datetime,
        window_end: datetime,
        attempt: int = 1,
        hard_limit_capability: str = HARD_LIMIT_ENFORCED,
        external_job_id: str | None = None,
        result_ref: str | None = None,
        lease_until: str = "",
    ) -> QuotaDecision:
        """Admit a job under the selected mode, reserving before dispatch.

        ``usage-only`` records/alerts only; ``quota-only`` freezes the executable
        bound, reserves core-hours atomically against the user∧project budgets and
        refuses when the bound is not executable or the account is exhausted.
        """
        self._require_started()
        assert self._selection is not None
        mode = str(self._selection.mode)
        policy = self._selection.policy
        if policy is None:  # pragma: no cover - guarded by startup
            return QuotaDecision(False, QuotaReason.NO_FROZEN_POLICY, mode)

        if mode == QuotaMode.USAGE_ONLY:
            alert = (
                f"usage-only mode: job {job_id} recorded, not enforced "
                "(explicit administrator selection)"
            )
            if self._alert_sink is not None:
                self._alert_sink(alert)
            logger.info("quota usage-only: job=%s recorded (no enforcement)", job_id)
            return QuotaDecision(
                admitted=True,
                reason_code=QuotaReason.RECORD_ONLY,
                mode=mode,
                detail="usage-only: recorded and alerted, no reservation taken",
                record_only=True,
                new_dispatch_allowed=True,
                results_readable=self._exhaustion.results_readable,
                alerts=(alert,),
            )

        bound = freeze_executable_bound(
            policy,
            cores=cores,
            walltime_s=walltime_s,
            hard_limit_capability=hard_limit_capability,
            queues=None,
            nodes=None,
        )
        gate = admit_metering_mode(bound, mode=AdmissionMode.QUOTA_ONLY)
        if not gate.admitted:
            logger.info("quota refused job=%s reason=%s", job_id, gate.reason_code)
            return QuotaDecision(
                admitted=False,
                reason_code=gate.reason_code,
                mode=mode,
                detail=gate.detail,
                bound=bound,
                new_dispatch_allowed=True,
                results_readable=self._exhaustion.results_readable,
            )

        reserved = Decimal(int(cores)) * Decimal(int(walltime_s)) / _SECONDS_PER_HOUR
        spec = _ReservationSpec(
            job_id=job_id,
            attempt=attempt,
            principal_id=principal_id,
            project_id=project_id,
            cores=int(cores),
            walltime_s=int(walltime_s),
            reserved_core_hours=reserved,
            policy_version=str(policy.policy_version),
            policy_digest=policy.snapshot_digest(),
            window_start=window_start,
            window_end=window_end,
            period=policy.period,
            external_job_id=external_job_id,
            result_ref=result_ref,
            lease_until=lease_until,
        )
        outcome = self._ledger.reserve(
            spec,
            user_budget=self._ledger.get_budget("user", principal_id),
            project_budget=self._ledger.get_budget("project", project_id),
        )
        if not outcome.reserved or outcome.reservation is None:
            exhausted = outcome.reason_code == QuotaReason.EXHAUSTED
            return QuotaDecision(
                admitted=False,
                reason_code=outcome.reason_code,
                mode=mode,
                detail=outcome.detail,
                bound=bound,
                reserved_core_hours=reserved,
                available_core_hours=outcome.available_core_hours,
                new_dispatch_allowed=False if exhausted else True,
                in_flight_killed=self._exhaustion.kill_in_flight,
                results_readable=self._exhaustion.results_readable,
            )
        return QuotaDecision(
            admitted=True,
            reason_code=QuotaReason.ADMITTED,
            mode=mode,
            detail="quota-only admission with a frozen executable reservation",
            reservation_id=outcome.reservation.reservation_id,
            reserved_core_hours=reserved,
            available_core_hours=outcome.available_core_hours,
            bound=bound,
            segments=outcome.reservation.segments,
            results_readable=self._exhaustion.results_readable,
        )

    # -- write-off -----------------------------------------------------------

    def write_off(self, reservation_id: str, record: UsageRecord) -> WriteOffResult:
        """Settle a reservation against a confirmed usage record.

        A pending record keeps the reservation held; a confirmable record settles
        it and releases the unused ceiling.  A record above the frozen executable
        reservation raises :class:`QuotaOverrunError` (never settled late).
        """
        self._require_started()
        return self._ledger.settle(
            reservation_id, record, confirmed_core_hours=Decimal(record.core_hours)
        )

    def mark_dispatched(self, reservation_id: str) -> QuotaReservation:
        return self._ledger.set_state(reservation_id, state=QuotaState.DISPATCHED)

    def reservation(self, reservation_id: str) -> QuotaReservation | None:
        return self._ledger.get(reservation_id)

    def reservations_for_job(self, job_id: str) -> list[QuotaReservation]:
        return self._ledger.list_reservations(job_id=job_id)

    def available(self, principal_id: str, project_id: str) -> Decimal | None:
        return self._ledger.available(principal_id, project_id)

    # -- top-up --------------------------------------------------------------

    def top_up(
        self,
        scope_type: str,
        scope_id: str,
        additional_core_hours: Decimal,
        authorized_by: str,
        *,
        extend_allowance: Callable[[Decimal], None] | None = None,
    ) -> TopUpResult:
        """Authorize + reserve the top-up first, then extend the allowance.

        With ``extend_allowance`` the external extension runs after the atomic
        authorization+hold is persisted; if it raises, the authorization and hold
        remain and the allowance is **not** extended, so no over-use can occur
        before the accounting catches up.
        """
        amount = _dec(additional_core_hours)
        topup_id = self._ledger.begin_topup(scope_type, scope_id, amount, authorized_by)
        if extend_allowance is None:
            budget = self._ledger.apply_topup(topup_id)
        else:
            extend_allowance(amount)
            self._ledger.mark_topup_applied(topup_id)
            budget = self._ledger.get_budget(scope_type, scope_id)
        return TopUpResult(
            topup_id=topup_id,
            scope_type=scope_type,
            scope_id=scope_id,
            amount=amount,
            phases=self._ledger.topup_events(topup_id),
            applied=self._ledger.topup_state(topup_id) == _TOPUP_APPLIED,
            budget_core_hours=budget,
        )

    def resolve_topup(self, topup_id: str) -> Decimal:
        """Apply an authorized-but-unapplied top-up (retry/recovery path)."""
        return self._ledger.apply_topup(topup_id)

    # -- recovery ------------------------------------------------------------

    def recover(self, *, reapply_topups: bool = True) -> RecoveryReport:
        """Replay undecided reservations; never release on lease expiry."""
        replayed = tuple(self._ledger.list_undecided())
        authorized = [row["topup_id"] for row in self._ledger.list_topups(state=_TOPUP_AUTHORIZED)]
        reapplied: list[str] = []
        pending: list[str] = []
        for topup_id in authorized:
            if reapply_topups:
                self._ledger.apply_topup(topup_id)
                reapplied.append(topup_id)
            else:
                pending.append(topup_id)
        logger.info(
            "quota recovery: replayed=%d topups_reapplied=%d topups_pending=%d",
            len(replayed),
            len(reapplied),
            len(pending),
        )
        return RecoveryReport(
            replayed=replayed,
            pending_topups=tuple(pending),
            reapplied_topups=tuple(reapplied),
        )

    # -- internals -----------------------------------------------------------

    def _require_started(self) -> None:
        if self._selection is None:
            raise QuotaStartupRefusedError(
                QuotaReason.MODE_NOT_SELECTED,
                "QuotaService has not started with an explicit mode selection",
            )
