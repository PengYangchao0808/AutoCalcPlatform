"""Frozen-envelope worker — execute one attempt, write one receipt (B06-2).

The worker is the *execution* half of the execution-integrity split: it reads a
frozen :class:`~acp_services.contracts.envelope.ExecutionEnvelope`, verifies the
frozen plan against it, gates the plan's prerequisites through an injected
:class:`~acp_services.contracts.injection.StepRequirementPort`, dispatches the
frozen task through an injected :class:`TaskExecutorPort`, and writes exactly one
:class:`~acp_services.contracts.envelope.ExecutionReceipt` atomically.

What the worker deliberately does **not** do (all guarded by
``tests/test_services_worker_contract.py``):

* it never imports ``acp`` (dependency direction is one-way);
* it never opens/reads/writes the jobs database and never touches a store;
* it never increments ``attempt`` and never writes job status — advancing the
  job is the controller's job, gated on receipt eligibility
  (:func:`acp_services.runtime.receipts.advance_job_state`);
* it never re-executes an attempt whose receipt already exists (idempotent
  re-delivery returns the existing receipt; a conflicting identity is rejected
  rather than silently overwritten).

The two seams it *does* need are explicit Protocols, never parent-process
objects: the transitional plan verifier / step-requirement gate are injected by
the legacy assembly until the self-sufficient worker composition root (todo 78)
constructs them from frozen data alone. The default :class:`CccpTaskExecutor`
talks to ``cccp.calculation.run_*`` directly (cccp is a lower layer, not an
``acp`` import).

Author: ACP Services (B lane, todo 31).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.envelope import (
    CURRENT_RECEIPT_VERSION,
    ExecutionEnvelope,
    ExecutionError,
    ExecutionReceipt,
    StorageBinding,
    ensure_no_runtime_state,
)
from acp_services.contracts.injection import StepRequirementPort

__all__ = [
    "CccpTaskExecutor",
    "DeclaredPlanVerifier",
    "DependencyNotSatisfiedError",
    "ExecutionScope",
    "FrozenPlan",
    "FrozenPlanIdentity",
    "FrozenStep",
    "FrozenWorker",
    "PlanVerificationError",
    "PlanVerificationPort",
    "ReceiptConflictError",
    "TaskExecutionOutcome",
    "TaskExecutorPort",
    "WorkerError",
    "build_receipt",
    "read_receipt",
    "write_receipt_atomic",
]


# ── errors ──────────────────────────────────────────────────────────────────


class WorkerError(Exception):
    """Base class for frozen-worker contract violations."""


class PlanVerificationError(WorkerError):
    """The supplied plan does not match the envelope's frozen plan identity."""


class DependencyNotSatisfiedError(WorkerError):
    """A frozen-plan step's prerequisite gate did not pass (blocked)."""

    def __init__(self, message: str, *, kind: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.reason = reason


class ReceiptConflictError(WorkerError):
    """A different attempt/submission already owns the receipt path."""


# ── frozen plan wire form ───────────────────────────────────────────────────


@dataclass(frozen=True)
class FrozenPlanIdentity:
    """Two-layer frozen plan identity (``identity.py:366-392`` shape)."""

    plan_digest: str
    plan_identity: str
    step_identities: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenStep:
    """One frozen plan step: its task kind plus the frozen request payload."""

    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FrozenPlan:
    """Wire form of the plan frozen for one attempt.

    ``payload`` is the serialized task request the executor consumes;
    ``steps`` drives the dependency gate; ``item_symbols`` is the row order the
    geometry prerequisite is checked against; ``identity`` is the declared
    frozen identity (used by :class:`DeclaredPlanVerifier`; the transitional
    recompute-backed verifier ignores it).
    """

    payload: Mapping[str, Any] = field(default_factory=dict)
    steps: tuple[FrozenStep, ...] = ()
    item_symbols: tuple[str, ...] = ()
    identity: FrozenPlanIdentity | None = None


# ── execution seam dataclasses ──────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionScope:
    """Everything one executor call needs, sourced only from frozen data."""

    envelope: ExecutionEnvelope
    plan: FrozenPlan
    work_dir: Path
    storage_binding: StorageBinding | None = None


@dataclass(frozen=True)
class TaskExecutionOutcome:
    """Typed result an executor returns for one attempt."""

    state: str
    result_digest: str = ""
    errors: tuple[ExecutionError | str, ...] = ()
    evidence_digests: Mapping[str, str] = field(default_factory=dict)


# ── injected ports ──────────────────────────────────────────────────────────


@runtime_checkable
class PlanVerificationPort(Protocol):
    """Recompute the frozen plan identity for verification.

    The transitional assembly injects an adapter over
    ``acp.calculations.identity`` (the two-layer ``plan_identity`` /
    ``step_identities`` authority, ``identity.py:366-392``); the self-sufficient
    worker composition root (todo 78) supplies its own implementation from
    frozen data. Kept out of ``acp_services`` so the worker never imports ``acp``.
    """

    def verify(self, plan: FrozenPlan) -> FrozenPlanIdentity:
        """Return the identity recomputed from *plan*'s body."""
        ...


@runtime_checkable
class TaskExecutorPort(Protocol):
    """Execute one frozen task for an attempt and return a typed outcome.

    The default :class:`CccpTaskExecutor` calls ``cccp.calculation.run_*``;
    tests inject a fake so no scientific binary is executed.
    """

    def execute(self, scope: ExecutionScope) -> TaskExecutionOutcome:
        """Run the frozen task described by *scope*."""
        ...


@dataclass(frozen=True)
class DeclaredPlanVerifier:
    """Structural verifier that returns the plan's declared frozen identity.

    This is the Services-side default used when the plan already carries its
    frozen identity (consistency check: the dispatched plan must match the
    envelope). The stronger recompute-backed verifier is injected transitionally
    and owned by todo 78.
    """

    def verify(self, plan: FrozenPlan) -> FrozenPlanIdentity:
        if plan.identity is None:
            raise PlanVerificationError(
                "frozen plan carries no declared identity; a recompute-backed "
                "PlanVerificationPort is required"
            )
        return plan.identity


# ── default executor (cccp task core) ───────────────────────────────────────


@dataclass(frozen=True)
class CccpTaskExecutor:
    """Default :class:`TaskExecutorPort` backed by ``cccp.calculation.run_*``.

    The frozen ``plan.payload`` must be a serialized ``TaskRequest``
    (``cccp.calculation.requests.TaskRequest.to_dict()``). cccp imports are lazy
    so importing this module never pulls the scientific stack, and no ``acp``
    module is ever imported.
    """

    def execute(self, scope: ExecutionScope) -> TaskExecutionOutcome:
        import cccp.calculation as calculation
        from cccp.calculation.context import TaskContext
        from cccp.calculation.requests import TaskRequest

        request_payload = dict(scope.plan.payload)
        request_payload.setdefault("task", scope.envelope.scientific_closure.task_kind)
        request = TaskRequest.from_dict(request_payload)
        runner = getattr(calculation, f"run_{request.task.value}")
        context = TaskContext(workdir=scope.work_dir, input_base=scope.work_dir)
        return _outcome_from_task_result(runner(request, context=context))


def _outcome_from_task_result(result: Any) -> TaskExecutionOutcome:
    status = str(getattr(result, "status", "completed"))
    state = status if status in ("completed", "failed", "cancelled", "blocked") else "failed"
    if state == status and getattr(result, "complete", True) is False:
        state = "completed"
    digest = _result_digest(result)
    raw_errors = tuple(getattr(result, "errors", ()) or ())
    return TaskExecutionOutcome(
        state=state,
        result_digest=digest,
        errors=raw_errors,
        evidence_digests={},
    )


def _result_digest(result: Any) -> str:
    to_dict = getattr(result, "to_dict", None)
    if to_dict is None:
        return ""
    blob = json.dumps(to_dict(), sort_keys=True, separators=(",", ":"), default=str)
    import hashlib

    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── receipt writing (atomic + idempotent) ───────────────────────────────────


def build_receipt(
    envelope: ExecutionEnvelope,
    outcome: TaskExecutionOutcome,
    *,
    written_at: str | None = None,
) -> ExecutionReceipt:
    """Bind *outcome* to the envelope's frozen identity as a receipt."""
    return ExecutionReceipt(
        receipt_version=CURRENT_RECEIPT_VERSION,
        job_id=envelope.job_id,
        attempt=envelope.attempt,
        submission_id=envelope.submission_id,
        state=outcome.state,
        result_digest=outcome.result_digest,
        errors=tuple(outcome.errors),
        written_at=written_at if written_at is not None else _utc_now_iso(),
        release_id=envelope.release_id,
        release_digest=envelope.release_digest,
        owner=envelope.owner,
        fencing_token=envelope.fencing_token,
        plan_identity=envelope.plan_identity,
        step_identities=envelope.step_identities,
        data_bindings=envelope.data_bindings,
        evidence_digests=outcome.evidence_digests,
    )


def write_receipt_atomic(path: Path | str, receipt: ExecutionReceipt) -> None:
    """Write *receipt* to *path* via a temp file in the same directory + rename.

    Atomic on POSIX (``os.replace``) and crash-safe: either the previous content
    or the complete new receipt is observable, never a partial file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(receipt.to_dict(), sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_receipt(path: Path | str) -> ExecutionReceipt | None:
    """Read a receipt at *path* (``None`` when absent; raises when corrupt)."""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReceiptConflictError(f"existing receipt at {target} is unreadable: {exc}") from exc
    return ExecutionReceipt.from_dict(payload)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the worker ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrozenWorker:
    """Execute exactly one frozen attempt and write its receipt.

    The worker is a pure function of frozen data plus its three explicit seams.
    ``step_requirements`` gates each frozen-plan step's prerequisites (reusing
    ``acp/calculations/step_requirements.py:170`` through the port);
    ``executor`` runs the task; ``plan_verifier`` checks the plan matches the
    envelope's frozen identity. No seam exposes a mutable store, a database, or
    the ability to change job state.
    """

    executor: TaskExecutorPort
    step_requirements: StepRequirementPort
    plan_verifier: PlanVerificationPort = field(default_factory=DeclaredPlanVerifier)

    def validate(
        self,
        envelope: ExecutionEnvelope,
        plan: FrozenPlan,
        *,
        prior_steps: Sequence[Any] = (),
        item_symbols: Sequence[str] | None = None,
    ) -> None:
        """Verify the frozen plan identity and every step's prerequisite gate."""
        ensure_no_runtime_state(dict(plan.payload), path="plan.payload")
        identity = self.plan_verifier.verify(plan)
        _assert_plan_matches_envelope(envelope, identity)
        symbols = plan.item_symbols if item_symbols is None else tuple(item_symbols)
        for step in plan.steps:
            outcome = self.step_requirements.evaluate_prerequisite(prior_steps, step.kind, symbols)
            if not bool(getattr(outcome, "satisfied", False)):
                reason = str(getattr(outcome, "reason", "") or "prerequisite not satisfied")
                raise DependencyNotSatisfiedError(
                    f"step {step.kind!r} blocked: {reason}",
                    kind=step.kind,
                    reason=reason,
                )

    def execute(
        self,
        envelope: ExecutionEnvelope,
        plan: FrozenPlan,
        *,
        receipt_path: Path | str,
        work_dir: Path | str,
        storage_binding: StorageBinding | None = None,
        prior_steps: Sequence[Any] = (),
        item_symbols: Sequence[str] | None = None,
    ) -> ExecutionReceipt:
        """Execute the attempt once and return the (atomically written) receipt.

        Idempotent: an existing receipt for the same ``(job, attempt,
        submission)`` is returned without re-execution; an existing receipt for
        a different identity is rejected (never silently overwritten).
        """
        existing = read_receipt(receipt_path)
        if existing is not None:
            return _reuse_or_conflict(existing, envelope)

        self.validate(envelope, plan, prior_steps=prior_steps, item_symbols=item_symbols)
        scope = ExecutionScope(
            envelope=envelope,
            plan=plan,
            work_dir=Path(work_dir),
            storage_binding=storage_binding,
        )
        outcome = self.executor.execute(scope)
        receipt = build_receipt(envelope, outcome)
        write_receipt_atomic(receipt_path, receipt)
        return receipt


def _assert_plan_matches_envelope(
    envelope: ExecutionEnvelope, identity: FrozenPlanIdentity
) -> None:
    if identity.plan_digest != envelope.plan_digest:
        raise PlanVerificationError(
            f"plan_digest mismatch: plan {identity.plan_digest!r} != "
            f"envelope {envelope.plan_digest!r}"
        )
    if identity.plan_identity != envelope.plan_identity:
        raise PlanVerificationError(
            f"plan_identity mismatch: plan {identity.plan_identity!r} != "
            f"envelope {envelope.plan_identity!r}"
        )
    if tuple(identity.step_identities) != tuple(envelope.step_identities):
        raise PlanVerificationError("step_identities mismatch between plan and envelope")


def _reuse_or_conflict(existing: ExecutionReceipt, envelope: ExecutionEnvelope) -> ExecutionReceipt:
    if (
        existing.job_id,
        existing.attempt,
        existing.submission_id,
    ) == (envelope.job_id, envelope.attempt, envelope.submission_id):
        return existing
    raise ReceiptConflictError(
        f"receipt already exists for job {existing.job_id!r} attempt "
        f"{existing.attempt} submission {existing.submission_id!r}; refusing to "
        f"overwrite with job {envelope.job_id!r} attempt {envelope.attempt} "
        f"submission {envelope.submission_id!r}"
    )
