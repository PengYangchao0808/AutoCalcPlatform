"""M2-04c — persistent execution allowance for a bounded recipe DAG attempt.

The CCCP compiler declares a **worst case** number of subprocess invocations per
step (:attr:`cccp.calculation.recipes.compile.CompiledStep.worst_case_executions`).
The Controller reserves that worst case **once** for the attempt and the worker
may only draw from the reservation:

* the reservation document is written by the reservation authority (Controller
  side / freeze) and is **immutable** for the worker — a worker can never mint
  allowance, so deleting/editing it is a typed refusal, not a fresh budget;
* every draw is appended (and fsynced) to a per-attempt ledger **before** the
  backend call runs, so a process kill or a lost receipt cannot refund an
  already-spent unit (``resource.restart`` never re-obtains undeducted quota);
* settlement records the actual consumption next to the reservation; it never
  widens it, and a settlement smaller than the draws is a conflict.

``TaskMeterAdapter`` implements the CCCP
:class:`cccp.calculation.context.TaskMeter` protocol, so the task cores draw one
unit immediately before each backend call (``call_capability``).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from acp_services.runtime.usage_units import normalize_usage_unit

logger = logging.getLogger(__name__)

__all__ = [
    "QUOTA_DIRNAME",
    "QUOTA_LEDGER_FILENAME",
    "QUOTA_RESERVATION_FILENAME",
    "QUOTA_RESERVATION_SCHEMA",
    "QUOTA_SETTLEMENT_FILENAME",
    "QUOTA_UNITS",
    "UNIT_QC_INVOCATION",
    "UNIT_RECIPE_STEP",
    "ExecutionQuotaError",
    "ExecutionQuotaExceededError",
    "PersistentQuotaLedger",
    "QuotaReservation",
    "TaskMeterAdapter",
    "reserve_worst_case",
]

#: Runtime directory (relative to the attempt work dir) holding quota files.
QUOTA_DIRNAME: Final = "quota"
QUOTA_RESERVATION_FILENAME: Final = "quota_reservation.json"
QUOTA_LEDGER_FILENAME: Final = "quota_ledger.jsonl"
QUOTA_SETTLEMENT_FILENAME: Final = "quota_settlement.json"

#: Wire schema of the reservation document.
QUOTA_RESERVATION_SCHEMA: Final = "acp2.runtime.execution-quota.v1"

#: The two budget units this ledger enforces (both are invocation counters).
#: They are the frozen ``usage_units`` vocabulary values — imported from the
#: dependency-free module so the worker closure never pulls the budget
#: projection (and therefore never a DB module).
UNIT_RECIPE_STEP: Final = "recipe_step"
UNIT_QC_INVOCATION: Final = "qc_invocation"

QUOTA_UNITS: Final[frozenset[str]] = frozenset({UNIT_RECIPE_STEP, UNIT_QC_INVOCATION})


class ExecutionQuotaError(RuntimeError):
    """The reservation is missing, foreign or inconsistent (fail closed)."""


class ExecutionQuotaExceededError(ExecutionQuotaError):
    """A draw would exceed the persisted reservation (the call never runs)."""

    def __init__(self, unit: str, requested: int, reserved: int, taken: int) -> None:
        super().__init__(
            f"{unit}: requested {requested} with {taken} already drawn of {reserved} reserved"
        )
        self.unit = unit
        self.requested = requested
        self.reserved = reserved
        self.taken = taken


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(payload), sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    """One attempt's immutable worst-case reservation."""

    reservation_id: str
    task_ref: str
    attempt: int
    plan_definition_digest: str
    limits: Mapping[str, int]
    per_step: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    issued_by: str = "controller"
    schema: str = QUOTA_RESERVATION_SCHEMA

    def __post_init__(self) -> None:
        if not self.reservation_id:
            raise ExecutionQuotaError("reservation_id is required")
        if not self.task_ref:
            raise ExecutionQuotaError("task_ref is required")
        if self.attempt < 1:
            raise ExecutionQuotaError("attempt must be >= 1")
        object.__setattr__(
            self,
            "limits",
            {normalize_usage_unit(unit): int(value) for unit, value in dict(self.limits).items()},
        )
        unknown = set(self.limits) - QUOTA_UNITS
        if unknown:
            raise ExecutionQuotaError(f"unsupported quota units {sorted(unknown)}")
        if any(value < 0 for value in self.limits.values()):
            raise ExecutionQuotaError("quota limits must be non-negative")
        object.__setattr__(
            self,
            "per_step",
            {
                str(step): {
                    normalize_usage_unit(unit): int(value) for unit, value in dict(limits).items()
                }
                for step, limits in dict(self.per_step).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "reservation_id": self.reservation_id,
            "task_ref": self.task_ref,
            "attempt": self.attempt,
            "plan_definition_digest": self.plan_definition_digest,
            "limits": dict(self.limits),
            "per_step": {step: dict(limits) for step, limits in self.per_step.items()},
            "issued_by": self.issued_by,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> QuotaReservation:
        if not isinstance(payload, Mapping):
            raise ExecutionQuotaError("reservation payload must be a mapping")
        schema = str(payload.get("schema") or "")
        if schema != QUOTA_RESERVATION_SCHEMA:
            raise ExecutionQuotaError(
                f"reservation schema {schema!r} is not {QUOTA_RESERVATION_SCHEMA}"
            )
        limits = payload.get("limits")
        if not isinstance(limits, Mapping):
            raise ExecutionQuotaError("reservation must carry a limits mapping")
        return cls(
            reservation_id=str(payload.get("reservation_id") or ""),
            task_ref=str(payload.get("task_ref") or ""),
            attempt=int(payload.get("attempt") or 0),
            plan_definition_digest=str(payload.get("plan_definition_digest") or ""),
            limits=dict(limits),
            per_step=dict(payload.get("per_step") or {}),
            issued_by=str(payload.get("issued_by") or "controller"),
        )


def reserve_worst_case(
    work_dir: Path | str,
    *,
    reservation_id: str,
    task_ref: str,
    attempt: int,
    plan_definition_digest: str,
    worst_case: Mapping[str, int],
    issued_by: str = "controller",
) -> QuotaReservation:
    """Write the attempt's worst-case reservation (idempotent for the same facts).

    ``worst_case`` is the compiler's ``{step_id: worst_case_executions}`` map.
    The reserved ``recipe_step`` is the number of steps that actually dispatch
    (worst case > 0) and ``qc_invocation`` is their sum — the worst case is
    reserved **once**, never per retry and never with an implicit ``+len(steps)``
    margin.
    """
    per_step = {
        str(step): {UNIT_QC_INVOCATION: int(value)} for step, value in dict(worst_case).items()
    }
    dispatchable = [value for value in per_step.values() if value[UNIT_QC_INVOCATION] > 0]
    reservation = QuotaReservation(
        reservation_id=reservation_id,
        task_ref=task_ref,
        attempt=attempt,
        plan_definition_digest=plan_definition_digest,
        limits={
            UNIT_RECIPE_STEP: len(dispatchable),
            UNIT_QC_INVOCATION: sum(value[UNIT_QC_INVOCATION] for value in dispatchable),
        },
        per_step=per_step,
        issued_by=issued_by,
    )
    target = Path(work_dir).joinpath(
        "WORK", "00_RUNTIME", QUOTA_DIRNAME, QUOTA_RESERVATION_FILENAME
    )
    if target.is_file():
        existing = QuotaReservation.from_dict(json.loads(target.read_text(encoding="utf-8")))
        if (
            existing.reservation_id != reservation.reservation_id
            or existing.task_ref != reservation.task_ref
            or existing.attempt != reservation.attempt
            or dict(existing.limits) != dict(reservation.limits)
        ):
            raise ExecutionQuotaError(
                "a different reservation already exists for this attempt; "
                "refusing to replace it (allowance is never re-minted)"
            )
        return existing
    _write_atomic(target, reservation.to_dict())
    return reservation


class PersistentQuotaLedger:
    """The attempt's append-only draw ledger over an immutable reservation."""

    def __init__(self, work_dir: Path | str, reservation: QuotaReservation) -> None:
        self.work_dir = Path(work_dir)
        self._dir = self.work_dir.joinpath("WORK", "00_RUNTIME", QUOTA_DIRNAME)
        self.reservation = reservation
        self._taken: dict[str, int] | None = None

    # -- paths -----------------------------------------------------------

    @property
    def ledger_path(self) -> Path:
        return self._dir / QUOTA_LEDGER_FILENAME

    @property
    def settlement_path(self) -> Path:
        return self._dir / QUOTA_SETTLEMENT_FILENAME

    # -- reading ---------------------------------------------------------

    def _ensure_loaded(self) -> dict[str, int]:
        if self._taken is not None:
            return self._taken
        taken = {unit: 0 for unit in self.reservation.limits}
        if self.ledger_path.is_file():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError as exc:
                    raise ExecutionQuotaError(
                        f"quota ledger at {self.ledger_path} is corrupt: {exc}"
                    ) from exc
                unit = normalize_usage_unit(str(entry.get("unit") or ""))
                if unit not in taken:
                    raise ExecutionQuotaError(f"quota ledger draws unknown unit {unit!r}")
                taken[unit] += int(entry.get("units") or 0)
        self._taken = taken
        return taken

    def taken(self, unit: str) -> int:
        """Return the persisted draw count of *unit*."""
        return self._ensure_loaded().get(normalize_usage_unit(unit), 0)

    def remaining(self, unit: str = "") -> int:
        """Return the units still available (advisory; ``unit=""`` = all)."""
        taken = self._ensure_loaded()
        if unit:
            normalized = normalize_usage_unit(unit)
            reserved = int(self.reservation.limits.get(normalized, 0))
            return max(0, reserved - taken.get(normalized, 0))
        return sum(
            max(0, int(limit) - taken.get(name, 0))
            for name, limit in self.reservation.limits.items()
        )

    def consumed(self) -> dict[str, int]:
        """Return the persisted draws as a plain mapping."""
        return dict(self._ensure_loaded())

    # -- writing ---------------------------------------------------------

    def take(self, unit: str, units: int = 1, *, detail: str = "") -> None:
        """Persistently draw *units* or refuse (the backend call never starts)."""
        normalized = normalize_usage_unit(unit)
        if normalized not in self.reservation.limits:
            raise ExecutionQuotaError(
                f"unit {normalized!r} is not reserved for this attempt "
                f"({sorted(self.reservation.limits)})"
            )
        amount = int(units)
        if amount < 0:
            raise ExecutionQuotaError("a draw cannot be negative")
        taken = self._ensure_loaded()
        reserved = int(self.reservation.limits[normalized])
        if taken.get(normalized, 0) + amount > reserved:
            raise ExecutionQuotaExceededError(
                normalized, amount, reserved, taken.get(normalized, 0)
            )
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema": QUOTA_RESERVATION_SCHEMA,
            "unit": normalized,
            "units": amount,
            "detail": detail,
            "reservation_id": self.reservation.reservation_id,
            "attempt": self.reservation.attempt,
        }
        with open(self.ledger_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        taken[normalized] = taken.get(normalized, 0) + amount

    def settle(self, consumed: Mapping[str, int] | None = None) -> dict[str, int]:
        """Record the settlement (never wider than the reservation or the draws)."""
        taken = self._ensure_loaded()
        settlement = {unit: int(value) for unit, value in (consumed or taken).items()}
        for unit, amount in settlement.items():
            if amount > int(self.reservation.limits.get(unit, 0)):
                raise ExecutionQuotaError(
                    f"settlement for {unit} ({amount}) exceeds the reservation "
                    f"({self.reservation.limits.get(unit, 0)})"
                )
        _write_atomic(
            self.settlement_path,
            {
                "schema": QUOTA_RESERVATION_SCHEMA,
                "reservation_id": self.reservation.reservation_id,
                "attempt": self.reservation.attempt,
                "drawn": taken,
                "settled": settlement,
            },
        )
        return settlement


@dataclass(frozen=True, slots=True)
class TaskMeterAdapter:
    """CCCP ``TaskMeter`` over the persistent ledger (draw before each backend call)."""

    ledger: PersistentQuotaLedger
    unit: str = UNIT_QC_INVOCATION

    def take_execution(self, units: int = 1, *, task_kind: str, detail: str = "") -> None:
        """Draw ``units`` and surface exhaustion as the CCCP typed refusal."""
        from cccp.calculation.context import TaskQuotaExceededError

        try:
            self.ledger.take(
                self.unit,
                units,
                detail=detail or task_kind,
            )
        except ExecutionQuotaExceededError as exc:
            raise TaskQuotaExceededError(
                f"{task_kind}: {exc}; the backend call is refused before it starts"
            ) from exc

    def remaining(self, *, task_kind: str = "") -> int:
        """Advisory remaining allowance (never a spend authority)."""
        return self.ledger.remaining(self.unit)
