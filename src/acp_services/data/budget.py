"""Storage budget reservation + hard-limit reconciliation (B02-7 / plan todo 17).

The Data seam prepares, uploads and caches bytes without ever reserving them
against a real capacity: a prefix is a location, **not** a quota.  This module
makes the reservation explicit and honest:

* every prepare / upload / cache allocation registers a reservation covering
  **bytes**, **inodes**, **scratch** bytes, **upload** bytes and **temporary
  amplification** bytes;
* the **peak** bytes of a reservation count the temporary files *and* the final
  files together (:data:`PEAK_DEFINITION`), so a prepare that stages a large
  scratch copy next to the published artifact is measured at its true high-water
  mark;
* reservations are reconciled against real hard limits when those are known
  (``statvfs`` / quota reports supplied by the caller); when no hard limit is
  available the report says ``hard_limits: unavailable`` and
  ``capability: unavailable`` — it never fabricates a quota;
* a path prefix is never reported as a hard quota
  (``prefix_is_quota`` is always ``False`` with an explicit note);
* a reservation that would exceed a known hard limit is **refused** and warned
  about; refusing the new allocation never deletes completed results.

Isolation: stdlib only (no legacy ``acp`` import).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "HARD_LIMITS_UNAVAILABLE",
    "CAPABILITY_ENFORCED",
    "CAPABILITY_UNAVAILABLE",
    "PEAK_DEFINITION",
    "PREFIX_NOTE",
    "BudgetPhase",
    "BudgetDimension",
    "StorageBudgetRequest",
    "BudgetReservation",
    "ReservationDecision",
    "HardLimits",
    "BudgetReport",
    "StorageBudgetLedger",
]

#: Value reported for ``hard_limits`` when no real capacity limit is known.
HARD_LIMITS_UNAVAILABLE = "unavailable"
CAPABILITY_ENFORCED = "enforced"
CAPABILITY_UNAVAILABLE = "unavailable"

#: The one definition of the peak (high-water) a reservation must satisfy.
PEAK_DEFINITION = "final_bytes + temporary_bytes"

#: Honest statement that a path prefix is not a capacity boundary.
PREFIX_NOTE = (
    "a path prefix is only a location, not a hard quota; enforcement requires an "
    "OS/quota-reported limit (statvfs/quota). Without one this ledger reports "
    "hard_limits: unavailable and never fabricates a capacity."
)


class BudgetPhase(str, Enum):
    """The three data phases that consume storage (plan B02-7)."""

    PREPARE = "prepare"
    UPLOAD = "upload"
    CACHE = "cache"


class BudgetDimension(str, Enum):
    """Dimensions a reservation is accounted against."""

    BYTES = "bytes"
    INODES = "inodes"
    SCRATCH = "scratch"
    UPLOAD = "upload"
    TEMPORARY_AMPLIFICATION = "temporary_amplification"


@dataclass(frozen=True)
class StorageBudgetRequest:
    """One storage allocation request (one prepare/upload/cache action).

    ``final_bytes`` are the published/formal files; ``temporary_bytes`` are the
    transient scratch copies (temporary amplification).  :attr:`peak_bytes`
    counts both together: that is the amount that must be simultaneously
    available while the action runs.
    """

    phase: str = BudgetPhase.PREPARE.value
    final_bytes: int = 0
    temporary_bytes: int = 0
    inodes: int = 0
    scratch_bytes: int = 0
    upload_bytes: int = 0
    resource_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.phase).strip():
            raise ValueError("phase must be a non-empty string")

    @property
    def peak_bytes(self) -> int:
        """Peak bytes: temporary files and final files held together."""
        return max(0, int(self.final_bytes)) + max(0, int(self.temporary_bytes))

    def dimension_totals(self) -> dict[str, int]:
        """The per-dimension amount this request reserves."""
        return {
            BudgetDimension.BYTES.value: self.peak_bytes,
            BudgetDimension.INODES.value: max(0, int(self.inodes)),
            BudgetDimension.SCRATCH.value: max(0, int(self.scratch_bytes)),
            BudgetDimension.UPLOAD.value: max(0, int(self.upload_bytes)),
            BudgetDimension.TEMPORARY_AMPLIFICATION.value: max(0, int(self.temporary_bytes)),
        }


@dataclass(frozen=True)
class BudgetReservation:
    """A granted storage reservation (releasable by id)."""

    reservation_id: str
    phase: str
    final_bytes: int
    temporary_bytes: int
    inodes: int
    scratch_bytes: int
    upload_bytes: int
    resource_id: str = ""

    @property
    def peak_bytes(self) -> int:
        return self.final_bytes + self.temporary_bytes

    def dimension_totals(self) -> dict[str, int]:
        return {
            BudgetDimension.BYTES.value: self.peak_bytes,
            BudgetDimension.INODES.value: self.inodes,
            BudgetDimension.SCRATCH.value: self.scratch_bytes,
            BudgetDimension.UPLOAD.value: self.upload_bytes,
            BudgetDimension.TEMPORARY_AMPLIFICATION.value: self.temporary_bytes,
        }


@dataclass(frozen=True)
class ReservationDecision:
    """Outcome of :meth:`StorageBudgetLedger.reserve`.

    ``allowed=False`` means the new allocation was **refused** (``exceeded``
    carries the offending ``dimension`` / ``requested`` / ``limit`` triples);
    completed results are untouched by a refusal.
    """

    allowed: bool
    reservation: BudgetReservation | None
    reason_code: str
    detail: str = ""
    exceeded: tuple[Mapping[str, str | int], ...] = ()


@dataclass(frozen=True)
class HardLimits:
    """Real capacity limits supplied by the caller (``statvfs`` / quota).

    ``limits`` maps a :class:`BudgetDimension` value to its hard limit.
    ``source`` records where the numbers came from (e.g. ``statvfs:/data``) so
    the report can name the authoritative limit instead of a path prefix.
    """

    limits: Mapping[str, int] = field(default_factory=dict)
    source: str = ""

    def limit_for(self, dimension: str) -> int | None:
        value = self.limits.get(str(dimension))
        return None if value is None else int(value)

    @property
    def available(self) -> bool:
        return bool(self.limits)


@dataclass(frozen=True)
class BudgetReport:
    """The reconciliation/capability report of one ledger.

    ``hard_limits`` is either the mapping of known limits or the literal
    ``"unavailable"``; ``capability`` mirrors that (``enforced`` / ``unavailable``)
    and ``prefix_is_quota`` is always ``False`` (a prefix is never a quota).
    """

    hard_limits: Mapping[str, int] | str
    hard_limits_available: bool
    capability: str
    limiting_source: str
    prefix: str
    prefix_is_quota: bool
    prefix_note: str
    peak_definition: str
    committed: Mapping[str, int]
    reservation_count: int
    by_phase: Mapping[str, int]
    completed_bytes: int
    refusal_count: int
    last_refusal_reason: str

    def to_dict(self) -> dict[str, Any]:
        hard_limits: Mapping[str, int] | str = self.hard_limits
        return {
            "hard_limits": (dict(hard_limits) if not isinstance(hard_limits, str) else hard_limits),
            "hard_limits_available": self.hard_limits_available,
            "capability": self.capability,
            "limiting_source": self.limiting_source,
            "prefix": self.prefix,
            "prefix_is_quota": self.prefix_is_quota,
            "prefix_note": self.prefix_note,
            "peak_definition": self.peak_definition,
            "committed": dict(self.committed),
            "reservation_count": self.reservation_count,
            "by_phase": dict(self.by_phase),
            "completed_bytes": self.completed_bytes,
            "refusal_count": self.refusal_count,
            "last_refusal_reason": self.last_refusal_reason,
        }


class StorageBudgetLedger:
    """Reserve prepare/upload/cache storage and reconcile it with hard limits.

    Args:
        hard_limits: Real capacity limits (``None`` = none known → every
            reservation is admitted and the report is honest about the missing
            capability; the ledger never invents a limit).
        prefix: The storage path prefix (recorded for the report; never treated
            as a quota).
    """

    def __init__(
        self,
        *,
        hard_limits: HardLimits | None = None,
        prefix: str = "",
    ) -> None:
        self._hard_limits = hard_limits
        self._prefix = str(prefix or "")
        self._reservations: dict[str, BudgetReservation] = {}
        self._completed: dict[str, int] = {}
        self._refusals: list[str] = []

    # -- reservation ----------------------------------------------------

    def reserve(self, request: StorageBudgetRequest) -> ReservationDecision:
        """Reserve storage for one request, refusing a hard-limit overrun.

        A refusal logs a warning and returns ``allowed=False`` **without**
        touching any completed result: the ledger never deletes existing
        artifacts to make room.
        """
        candidate = self._dimension_totals()
        for dimension, value in request.dimension_totals().items():
            candidate[dimension] = candidate.get(dimension, 0) + value

        if self._hard_limits is not None and self._hard_limits.available:
            exceeded: list[Mapping[str, str | int]] = []
            for dimension, total in sorted(candidate.items()):
                limit = self._hard_limits.limit_for(dimension)
                if limit is not None and total > limit:
                    exceeded.append({"dimension": dimension, "requested": total, "limit": limit})
            if exceeded:
                detail = "storage reservation refused: " + ", ".join(
                    f"{item['dimension']} {item['requested']}>{item['limit']}" for item in exceeded
                )
                logger.warning(
                    "storage budget reservation refused (phase=%s resource=%s): %s; "
                    "completed results are preserved",
                    request.phase,
                    request.resource_id,
                    detail,
                )
                self._refusals.append(detail)
                return ReservationDecision(
                    allowed=False,
                    reservation=None,
                    reason_code="budget_exceeded",
                    detail=detail,
                    exceeded=tuple(exceeded),
                )

        reservation = BudgetReservation(
            reservation_id="res_" + uuid.uuid4().hex,
            phase=str(request.phase),
            final_bytes=max(0, int(request.final_bytes)),
            temporary_bytes=max(0, int(request.temporary_bytes)),
            inodes=max(0, int(request.inodes)),
            scratch_bytes=max(0, int(request.scratch_bytes)),
            upload_bytes=max(0, int(request.upload_bytes)),
            resource_id=str(request.resource_id),
        )
        self._reservations[reservation.reservation_id] = reservation
        return ReservationDecision(
            allowed=True,
            reservation=reservation,
            reason_code="reserved",
            detail=f"reserved peak {reservation.peak_bytes} bytes for {request.phase}",
        )

    def release(self, reservation_id: str) -> bool:
        """Release one reservation; returns whether it existed."""
        return self._reservations.pop(str(reservation_id), None) is not None

    # -- completed results ---------------------------------------------

    def record_completed(self, resource_id: str, bytes_: int) -> None:
        """Record a completed result's bytes (never deleted by a refusal)."""
        self._completed[str(resource_id)] = max(0, int(bytes_))

    @property
    def completed_bytes(self) -> int:
        return sum(self._completed.values())

    # -- report ---------------------------------------------------------

    def _dimension_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for reservation in self._reservations.values():
            for dimension, value in reservation.dimension_totals().items():
                totals[dimension] = totals.get(dimension, 0) + value
        return totals

    def report(self) -> BudgetReport:
        """Return the reconciliation / capability report (never fakes a quota)."""
        limits: dict[str, int] = {}
        available = False
        source = ""
        if self._hard_limits is not None and self._hard_limits.available:
            limits = {str(k): int(v) for k, v in self._hard_limits.limits.items()}
            available = True
            source = str(self._hard_limits.source)

        by_phase: dict[str, int] = {}
        for reservation in self._reservations.values():
            by_phase[reservation.phase] = by_phase.get(reservation.phase, 0) + (
                reservation.peak_bytes
            )

        return BudgetReport(
            hard_limits=(limits if available else HARD_LIMITS_UNAVAILABLE),
            hard_limits_available=available,
            capability=(CAPABILITY_ENFORCED if available else CAPABILITY_UNAVAILABLE),
            limiting_source=source,
            prefix=self._prefix,
            prefix_is_quota=False,
            prefix_note=PREFIX_NOTE,
            peak_definition=PEAK_DEFINITION,
            committed=self._dimension_totals(),
            reservation_count=len(self._reservations),
            by_phase=by_phase,
            completed_bytes=self.completed_bytes,
            refusal_count=len(self._refusals),
            last_refusal_reason=(self._refusals[-1] if self._refusals else ""),
        )
