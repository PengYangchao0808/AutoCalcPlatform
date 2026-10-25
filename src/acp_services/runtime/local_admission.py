"""Local-single resource admission composition (B09).

The single ledger is :class:`acp_services.runtime.admission.ReservationLedger`
and the single decision owner is
:class:`acp_services.runtime.admission.AdmissionController`.  This module only
composes them for the Controller process:

* :class:`AdmissionLimits` freezes the server-side per-job / cumulative
  ceilings (cores, memory, walltime, storage, active tasks);
* :class:`_GrantProvider` projects those ceilings through the existing
  ``ResourceGrantSpec`` / ``GrantResolution`` contract;
* :class:`_CapacityProvider` reports the actual local pool;
* :class:`_ControllerReservationPort` is the B08 claim-keyed adapter.

The reservation is made **queued** at submit time (storage + an active-task
slot), then re-validated and promoted at dispatch.  A denial therefore happens
before a work directory, a QC process or a remote submission exists.

This is local single-principal enforcement.  It is not multi-tenant security,
and model-token metering remains ``NOT_VERIFIED``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from acp_services.contracts.access import LOCAL_PRINCIPAL_ID, ResourceGrantSpec
from acp_services.runtime.admission import (
    HARD_LIMIT_ENFORCED,
    AdmissionController,
    AdmissionDecision,
    AdmissionRequest,
    Capacity,
    GrantResolution,
    ReservationLedger,
)
from acp_services.runtime.idempotency import (
    IdempotencyReservationDeniedError,
    ReservationIdempotency,
    ReservationPort,
)
from acp_services.runtime.research_budget import (
    BudgetConfigurationError,
    BudgetReason,
    parse_budget_policy,
    release_statement,
)
from acp_services.runtime.usage import UsageUnitCounts, usage_id_for

__all__ = [
    "ADMISSION_ENFORCEMENT",
    "AdmissionLimits",
    "AdmissionRefusedError",
    "LocalAdmission",
    "build_local_admission",
]

#: Capability string exposed by ``GET /api/capabilities``.
ADMISSION_ENFORCEMENT = "local-single-enforced"

_MEMORY_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgtp]?)(?:i?b)?\s*$", re.IGNORECASE)
_MEMORY_FACTORS = {
    "": 1024,
    "k": 1 / 1024,
    "m": 1,
    "g": 1024,
    "t": 1024 * 1024,
    "p": 1024 * 1024 * 1024,
}
_GB = 1024**3


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return default


@dataclass(frozen=True)
class AdmissionLimits:
    """Server-side admission ceilings (finite by construction)."""

    max_tasks: int = 10000
    max_cores: int = 256
    max_memory_mb: int = 1_048_576
    max_walltime_s: int = 30 * 24 * 3600
    max_storage_bytes: int = 10 * 1024**4
    default_walltime_s: int = 24 * 3600
    policy_version: str = "local-single-v1"

    @classmethod
    def from_environment(cls) -> AdmissionLimits:
        """Read the round's ceilings from ``ACP_ADMISSION_*`` (or defaults)."""
        return cls(
            max_tasks=_env_int("ACP_ADMISSION_MAX_TASKS", 10000),
            max_cores=_env_int("ACP_ADMISSION_MAX_CORES", 256),
            max_memory_mb=_env_int("ACP_ADMISSION_MAX_MEMORY_MB", 1_048_576),
            max_walltime_s=_env_int("ACP_ADMISSION_MAX_WALLTIME_S", 30 * 24 * 3600),
            max_storage_bytes=_env_int("ACP_ADMISSION_MAX_STORAGE_BYTES", 10 * 1024**4),
            default_walltime_s=_env_int("ACP_ADMISSION_DEFAULT_WALLTIME_S", 24 * 3600),
            policy_version=str(
                os.environ.get("ACP_ADMISSION_POLICY_VERSION") or "local-single-v1"
            ).strip()
            or "local-single-v1",
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "max_tasks": self.max_tasks,
            "max_cores": self.max_cores,
            "max_memory_mb": self.max_memory_mb,
            "max_walltime_s": self.max_walltime_s,
            "max_storage_bytes": self.max_storage_bytes,
            "default_walltime_s": self.default_walltime_s,
            "policy_version": self.policy_version,
        }


class AdmissionRefusedError(RuntimeError):
    """A pre-claim admission denial (legacy submit path without a claim)."""

    def __init__(self, decision: AdmissionDecision) -> None:
        self.decision = decision
        super().__init__(decision.detail or decision.reason_code)


class _GrantProvider:
    """Project the local-single ceilings through the frozen grant contract."""

    def __init__(self, limits: AdmissionLimits) -> None:
        self._limits = limits

    def resolve(self, request: AdmissionRequest) -> GrantResolution:
        grant = ResourceGrantSpec(
            grant_id="local-single-admission",
            principal_id=request.principal_id,
            scope_id=request.project_id,
            resource_id="local-single",
            actions=("read", "write", "control"),
            granted_at="1970-01-01T00:00:00+00:00",
            cores=self._limits.max_cores,
            memory_mb=self._limits.max_memory_mb,
            concurrency=self._limits.max_tasks,
            duration_s=self._limits.max_walltime_s,
            storage_bytes=self._limits.max_storage_bytes,
        )
        return GrantResolution(
            policy_version=self._limits.policy_version,
            user_grant=grant,
            project_grant=grant,
            hard_limit_capability=HARD_LIMIT_ENFORCED,
        )


class _CapacityProvider:
    """Mutable local pool capacity (tests can shrink it before dispatch)."""

    def __init__(self, limits: AdmissionLimits) -> None:
        self._limits = limits
        self._capacity = self._capacity_from_limits()

    def _capacity_from_limits(self) -> Capacity:
        return Capacity(
            cores=self._limits.max_cores,
            memory_mb=self._limits.max_memory_mb,
            concurrency=self._limits.max_tasks,
            storage_bytes=self._limits.max_storage_bytes,
            source=ADMISSION_ENFORCEMENT,
            available=True,
        )

    def capacity(self) -> Capacity:
        return self._capacity

    def set_capacity(
        self,
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        concurrency: int | None = None,
        storage_bytes: int | None = None,
    ) -> Capacity:
        """Shrink/grow the pool for dispatch-revalidation tests."""
        self._capacity = replace(
            self._capacity,
            cores=self._capacity.cores if cores is None else int(cores),
            memory_mb=self._capacity.memory_mb if memory_mb is None else int(memory_mb),
            concurrency=(self._capacity.concurrency if concurrency is None else int(concurrency)),
            storage_bytes=(
                self._capacity.storage_bytes if storage_bytes is None else int(storage_bytes)
            ),
        )
        return self._capacity


def _memory_mb(value: Any) -> int:
    if value is None:
        return 1024
    if isinstance(value, bool):
        return 1024
    if isinstance(value, (int, float)):
        return max(1, int(float(value) * 1024))
    match = _MEMORY_RE.match(str(value))
    if match is None:
        return 1024
    amount = float(match.group(1))
    unit = match.group(2).lower()
    return max(1, int(round(amount * _MEMORY_FACTORS.get(unit, 1))))


def _walltime_s(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        return numbers[0] * 3600 + numbers[1] * 60
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def _storage_bytes(resources: Any) -> int:
    if not isinstance(resources, dict):
        return 0
    raw = resources.get("storage_bytes")
    if raw is not None:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0
    raw_gb = resources.get("storage_gb")
    if raw_gb is not None:
        try:
            return max(0, int(float(raw_gb) * _GB))
        except (TypeError, ValueError):
            return 0
    return 0


def _cores(resources: Any) -> int:
    if not isinstance(resources, dict):
        return 1
    raw = resources.get("nproc", resources.get("ncores", resources.get("cores", 1)))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _as_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class _ControllerReservationPort:
    """Claim-keyed reservation adapter over the one admission controller."""

    def __init__(self, admission: LocalAdmission) -> None:
        self._admission = admission

    def reserve_for_claim(
        self,
        claim_id: str,
        *,
        scope_id: str,
        action: str,
        idempotency_key: str,
        spec: Any,
        budget: Mapping[str, Any] | None = None,
    ) -> str:
        reservation_id = "res_" + str(claim_id)
        request = self._admission.request_for_spec(claim_id, spec)
        if (
            self._admission.verified_project is not None
            and request.project_id != self._admission.verified_project
        ):
            denial = AdmissionDecision(
                admitted=False,
                reason_code="scope_mismatch",
                detail=(
                    f"project {request.project_id!r} is outside the verified Controller "
                    f"scope {self._admission.verified_project!r}"
                ),
            )
            raise IdempotencyReservationDeniedError(
                denial.detail,
                decision=denial,
                reason_code=denial.reason_code,
            )
        decision = self._admission.controller.admit(
            request,
            queued=True,
            reservation_id=reservation_id,
            idempotency=ReservationIdempotency(
                claim_id=str(claim_id),
                scope_id=str(scope_id),
                action=str(action),
                idempotency_key=str(idempotency_key),
            ),
        )
        if not decision.allowed:
            raise IdempotencyReservationDeniedError(
                decision.detail or decision.reason_code,
                decision=decision,
                reason_code=decision.reason_code,
                exceeded=decision.exceeded,
            )
        try:
            budget_decision = self._admission.reserve_budget_for_claim(
                budget,
                claim_id=str(claim_id),
                task_ref=str(claim_id),
                admission_reservation_id=str(decision.reservation_id or reservation_id),
            )
        except BudgetConfigurationError as exc:
            self._admission.ledger.release(str(decision.reservation_id or reservation_id))
            raise IdempotencyReservationDeniedError(
                str(exc),
                decision=AdmissionDecision(
                    admitted=False,
                    reason_code=BudgetReason.INVALID,
                    detail=str(exc),
                    mode=decision.mode,
                    enforced=True,
                ),
                reason_code=BudgetReason.INVALID,
            ) from exc
        if budget_decision is not None and not budget_decision.admitted:
            self._admission.ledger.release(str(decision.reservation_id or reservation_id))
            exceeded = (
                {
                    "scope": "research_budget",
                    "dimension": budget_decision.exhausted_unit or "remaining",
                    "requested": dict(budget_decision.requested),
                },
            )
            raise IdempotencyReservationDeniedError(
                budget_decision.detail or budget_decision.reason_code,
                decision=AdmissionDecision(
                    admitted=False,
                    reason_code=budget_decision.reason_code,
                    detail=budget_decision.detail,
                    mode=decision.mode,
                    enforced=True,
                    effective_bounds=decision.effective_bounds,
                    policy_version=decision.policy_version,
                    exceeded=exceeded,
                ),
                reason_code=budget_decision.reason_code,
                exceeded=exceeded,
            )
        return decision.reservation_id or reservation_id

    def release_for_claim(self, claim_id: str) -> bool:
        self._admission.ledger.release_research_budget(
            claim_id=str(claim_id), event="submit_failed"
        )
        return bool(self._admission.ledger.release_for_claim(str(claim_id)))


@dataclass
class LocalAdmission:
    """The Controller's admission controller + ledger + claim adapter."""

    limits: AdmissionLimits
    ledger: ReservationLedger
    controller: AdmissionController
    principal_id: str = LOCAL_PRINCIPAL_ID
    verified_project: str | None = None
    capacity: _CapacityProvider | None = None
    grants: _GrantProvider | None = None

    @classmethod
    def build(
        cls,
        db_path: str | os.PathLike[str],
        *,
        limits: AdmissionLimits | None = None,
        principal_id: str = LOCAL_PRINCIPAL_ID,
        verified_project: str | None = None,
    ) -> LocalAdmission:
        resolved_limits = limits or AdmissionLimits.from_environment()
        ledger = ReservationLedger(str(db_path))
        capacity = _CapacityProvider(resolved_limits)
        grants = _GrantProvider(resolved_limits)
        controller = AdmissionController(grants=grants, capacity=capacity, ledger=ledger)
        return cls(
            limits=resolved_limits,
            ledger=ledger,
            controller=controller,
            principal_id=str(principal_id),
            verified_project=(None if not verified_project else str(verified_project)),
            capacity=capacity,
            grants=grants,
        )

    # -- request construction -------------------------------------------

    def request_for_spec(self, job_id: str, spec: Any) -> AdmissionRequest:
        """Build the server-verified admission request for *spec*."""
        resources = getattr(spec, "resources", None)
        project = (
            str(getattr(spec, "project_id", None) or self.verified_project or "default").strip()
            or "default"
        )
        duration = _walltime_s(resources.get("walltime_s") if isinstance(resources, dict) else None)
        if duration is None:
            duration = _walltime_s(
                resources.get("walltime") if isinstance(resources, dict) else None
            )
        if duration is None:
            duration = self.limits.default_walltime_s
        target = getattr(spec, "target_node", None)
        return AdmissionRequest(
            principal_id=self.principal_id,
            project_id=project,
            job_id=str(job_id),
            cores=_cores(resources),
            memory_mb=_memory_mb(resources.get("mem") if isinstance(resources, dict) else None),
            duration_s=int(duration),
            storage_bytes=_storage_bytes(resources),
            node=(None if target in (None, "", "local") else str(target)),
            queued=True,
        )

    # -- submit / dispatch / release ------------------------------------

    def admit_job(
        self,
        job_id: str,
        spec: Any,
        *,
        project_id: str | None = None,
        budget: Mapping[str, Any] | None = None,
    ) -> AdmissionDecision:
        """Pre-claim admission for the legacy (no-idempotency) submit path."""
        request = self.request_for_spec(job_id, spec)
        project = str(project_id or request.project_id)
        if self.verified_project is not None and project != self.verified_project:
            return AdmissionDecision(
                admitted=False,
                reason_code="scope_mismatch",
                detail=(
                    f"project {project!r} is outside the verified Controller scope "
                    f"{self.verified_project!r}"
                ),
            )
        decision = self.controller.admit(
            replace(request, project_id=project),
            queued=True,
            reservation_id="res_job_" + str(job_id),
        )
        if not decision.allowed:
            return decision
        try:
            budget_decision = self.reserve_budget_for_claim(
                budget,
                claim_id=str(job_id),
                task_ref=str(job_id),
                admission_reservation_id=str(decision.reservation_id or ""),
            )
        except BudgetConfigurationError as exc:
            self.controller.release(str(decision.reservation_id or ""))
            return AdmissionDecision(
                admitted=False,
                reason_code=BudgetReason.INVALID,
                detail=str(exc),
                mode=decision.mode,
                enforced=True,
            )
        if budget_decision is not None and not budget_decision.admitted:
            self.controller.release(str(decision.reservation_id or ""))
            return AdmissionDecision(
                admitted=False,
                reason_code=budget_decision.reason_code,
                detail=budget_decision.detail,
                mode=decision.mode,
                enforced=True,
                effective_bounds=decision.effective_bounds,
                policy_version=decision.policy_version,
                exceeded=(
                    {
                        "scope": "research_budget",
                        "dimension": budget_decision.exhausted_unit or "remaining",
                        "requested": dict(budget_decision.requested),
                    },
                ),
            )
        return decision

    def reserve_budget_for_claim(
        self,
        budget: Mapping[str, Any] | None,
        *,
        claim_id: str,
        task_ref: str,
        admission_reservation_id: str,
    ) -> Any | None:
        """Define (idempotently) and reserve the server-quoted budget."""
        if not budget:
            return None
        budget_id = str(budget.get("budget_id") or "").strip()
        definition = budget.get("budget")
        if not budget_id or not isinstance(definition, Mapping):
            return None
        policy = parse_budget_policy(
            definition,
            budget_id=budget_id,
            approved_budget=(budget.get("approved_budget") or definition),
            approval_ref=str(budget.get("approval_ref") or ""),
            project_id=str(budget.get("project_id") or self.verified_project or ""),
        )
        self.ledger.define_research_budget(policy)
        return self.ledger.reserve_research_budget(
            budget_id,
            {"task_ref": 1, "attempt": 1},
            reservation_id="bres_" + str(claim_id),
            task_ref=str(task_ref),
            run_id=str(budget.get("run_id") or ""),
            claim_id=str(claim_id),
            admission_reservation_id=str(admission_reservation_id),
        )

    def result_for_reservation(
        self,
        reservation_id: str | None,
        *,
        decision: AdmissionDecision | None = None,
    ) -> dict[str, Any]:
        """Return the persisted ``result["admission"]`` projection."""
        payload: dict[str, Any] = {
            "reservation_id": reservation_id,
            "enforcement": ADMISSION_ENFORCEMENT,
            "mode": (decision.mode if decision is not None else "quota-only"),
            "enforced": bool(decision.enforced) if decision is not None else True,
            "policy_version": self.limits.policy_version,
            "principal_id": self.principal_id,
            "verified_project": self.verified_project,
            "multi_tenant_security": False,
        }
        if decision is not None:
            payload["reason_code"] = decision.reason_code
        if reservation_id:
            budget_reservation = self.ledger.research_budget_for_admission(str(reservation_id))
            if budget_reservation is not None:
                payload["budget"] = {
                    "budget_id": budget_reservation.budget_id,
                    "reservation_id": budget_reservation.reservation_id,
                    "units": dict(budget_reservation.units),
                    "state": budget_reservation.state,
                }
        return payload

    def reservation_id_for_record(self, record: Any) -> str | None:
        result = getattr(record, "result", None)
        if not isinstance(result, dict):
            return None
        admission = result.get("admission")
        if not isinstance(admission, dict):
            return None
        reservation_id = admission.get("reservation_id")
        return None if reservation_id is None else str(reservation_id)

    def revalidate_job(self, record: Any) -> AdmissionDecision | None:
        """Re-validate the job's reservation immediately before dispatch."""
        reservation_id = self.reservation_id_for_record(record)
        if reservation_id is None:
            return None
        decision = self.controller.revalidate(reservation_id)
        if decision.allowed:
            self.ledger.mark_research_budget_dispatched(str(reservation_id))
        return decision

    def release_job(self, record: Any) -> bool:
        """Release the job's reservation and apply the frozen budget release rule."""
        reservation_id = self.reservation_id_for_record(record)
        if reservation_id is None:
            return False
        budget_reservation = self.ledger.research_budget_for_admission(reservation_id)
        if budget_reservation is not None:
            event = (
                "cancel_before_dispatch"
                if budget_reservation.state == "reserved"
                else "cancel_after_dispatch"
            )
            self.ledger.release_research_budget(
                admission_reservation_id=reservation_id, event=event
            )
        return self.controller.release(reservation_id)

    def release_reservation_id(self, reservation_id: str | None) -> bool:
        if not reservation_id:
            return False
        budget_reservation = self.ledger.research_budget_for_admission(str(reservation_id))
        if budget_reservation is not None:
            event = (
                "cancel_before_dispatch"
                if budget_reservation.state == "reserved"
                else "cancel_after_dispatch"
            )
            self.ledger.release_research_budget(
                admission_reservation_id=str(reservation_id), event=event
            )
        return self.controller.release(str(reservation_id))

    def record_usage(self, record: Any) -> bool:
        """Record trusted usage once for a terminal job (duplicate-safe)."""
        reservation_id = self.reservation_id_for_record(record)
        if reservation_id is None:
            return False
        reservation = self.controller.reservation(reservation_id)
        cores = 0 if reservation is None else int(reservation.cores)
        if getattr(record, "status", None) is not None and not getattr(
            record.status, "is_terminal", True
        ):
            return False
        started = _as_datetime(getattr(record, "started_at", None)) or _as_datetime(
            getattr(record, "created_at", None)
        )
        finished = _as_datetime(getattr(record, "completed_at", None)) or datetime.now(timezone.utc)
        seconds = 0
        if started is not None and finished is not None and finished >= started:
            seconds = int((finished - started).total_seconds())
        submission_id = None
        result = getattr(record, "result", None)
        if isinstance(result, dict):
            remote = result.get("remote")
            if isinstance(remote, dict) and remote.get("submission_id") is not None:
                submission_id = str(remote["submission_id"])
        usage_id = usage_id_for(
            str(record.id), int(getattr(record, "attempt", 1) or 1), submission_id
        )
        recorded = self.controller.record_usage_receipt(
            usage_id,
            reservation_id=reservation_id,
            job_id=str(record.id),
            attempt=int(getattr(record, "attempt", 1) or 1),
            cores=cores,
            seconds=seconds,
        )
        budget_reservation = self.ledger.research_budget_for_admission(reservation_id)
        if budget_reservation is not None:
            self.ledger.settle_research_budget(
                budget_reservation.budget_id,
                receipt_id=usage_id,
                reservation_id=budget_reservation.reservation_id,
                counters=UsageUnitCounts(
                    task_ref=1,
                    attempt=int(getattr(record, "attempt", 1) or 1),
                ),
                run_id=str(getattr(record, "id", "")),
                trusted_source="controller-terminal-receipt",
            )
        return recorded

    def reservation_port(self) -> ReservationPort:
        return _ControllerReservationPort(self)

    # -- projections ----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """P2-compatible admission capability projection."""
        occupancy = self.controller.occupancy()
        return {
            "enforcement": ADMISSION_ENFORCEMENT,
            "mode": "quota-only",
            "quota_enforced": True,
            "recorded_only": False,
            "model_token_enforcement": "NOT_VERIFIED",
            "multi_tenant_security": False,
            "principal_id": self.principal_id,
            "verified_project": self.verified_project,
            "policy_version": self.limits.policy_version,
            "limits": self.limits.to_dict(),
            "occupancy": occupancy,
            "usage": self.controller.usage_totals(),
            "research_budget": {
                "ledger": "durable-reservation-ledger",
                "budget_identity": "project+proposal_ref (independent of run_id)",
                "model_token_enforcement": "NOT_VERIFIED",
                "model_proxy_owner": (
                    "C (acp2_experience_budget_proxy.py, C T-13); B does not proxy tokens"
                ),
                **release_statement(),
            },
        }


def build_local_admission(
    db_path: str | os.PathLike[str],
    *,
    limits: AdmissionLimits | None = None,
    verified_project: str | None = None,
) -> LocalAdmission:
    """Build the Controller's single local admission composition."""
    return LocalAdmission.build(
        db_path,
        limits=limits,
        verified_project=verified_project,
    )
