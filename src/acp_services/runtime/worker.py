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
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
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
from acp_services.runtime.errors import WorkerError
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.runtime.receipt_identity import (
    ReceiptIdentityError,
    compare_receipt_identity,
)
from cccp.analysis.identity import identity_fingerprint

__all__ = [
    "JOB_STATUS_BY_RECEIPT_STATE",
    "NORMAL_REUSE_METADATA_KEY",
    "PARTIAL_RESULT_METADATA_KEY",
    "QUALIFICATION_COMPLETE",
    "QUALIFICATION_INCOMPLETE",
    "QUALIFICATION_PARTIAL",
    "QUALIFICATION_TASK_FAILED",
    "TASK_COMPLETION_AUTHORITIES",
    "TASK_RESULT_RELATIVE_PATH",
    "TASK_RESULT_SCHEMA",
    "TASK_ENVIRONMENT_CONFIG_KEY",
    "CccpTaskExecutor",
    "CompletionAuthority",
    "CompletionDecision",
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
    "RequiredFact",
    "TaskExecutionOutcome",
    "TaskExecutorPort",
    "TaskResultIntegrityError",
    "TaskResultQualification",
    "WorkerError",
    "build_receipt",
    "decide_task_result",
    "decision_for_failure",
    "frozen_plan_body_digest",
    "frozen_plan_to_dict",
    "job_status_for_receipt",
    "outcome_from_decision",
    "outcome_from_task_result",
    "qualification_metadata",
    "read_receipt",
    "read_task_result",
    "read_task_result_qualification",
    "receipt_exit_code",
    "task_result_path",
    "build_task_result_document",
    "write_receipt_atomic",
    "write_task_result",
    "write_task_result_document",
]


# ── errors ──────────────────────────────────────────────────────────────────


#: ``WorkerError`` is owned by the dependency-free :mod:`runtime.errors` module
#: (D02) so ``runtime.receipts`` can share the base without importing this
#: module. It stays importable from here (identity preserved, ANTI #30).
class PlanVerificationError(WorkerError):
    """The supplied plan does not match the envelope's frozen plan identity."""


class DependencyNotSatisfiedError(WorkerError):
    """A frozen-plan step's prerequisite gate did not pass (blocked)."""

    def __init__(self, message: str, *, kind: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.reason = reason


class ReceiptConflictError(ReceiptIdentityError):
    """A different attempt/identity already owns the receipt path.

    Carries the full :class:`~acp_services.runtime.receipt_identity.ReceiptIdentityComparison`
    on ``comparison`` when the conflict is an identity mismatch; unreadable
    receipt payloads raise it with a message only (``comparison`` is ``None``).
    """


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


def frozen_plan_body_digest(plan: FrozenPlan) -> str:
    """Recompute the content-free body digest of a frozen plan.

    Covers exactly the dispatched body (``payload`` + ordered ``steps`` +
    ``item_symbols``) and excludes the opaque declared science identity — the
    digest is the tamper anchor the composition root recomputes, never trusts.

    Single implementation home (07-A / todo 24): the worker composition root
    (:mod:`acp_services.worker_bootstrap`) re-exports it, and the frozen-attempt
    producer (:mod:`acp_services.runtime.attempt_freeze`) imports it from here
    so the producer and the verifier can never drift apart.
    """
    return identity_fingerprint(
        {
            "scope": "frozen-plan",
            "payload": dict(plan.payload),
            "steps": [{"kind": step.kind, "payload": dict(step.payload)} for step in plan.steps],
            "item_symbols": list(plan.item_symbols),
        }
    )


def frozen_plan_to_dict(plan: FrozenPlan) -> dict[str, Any]:
    """Serialise *plan* to its frozen wire form (JSON-safe, single home)."""
    identity: dict[str, Any] | None = None
    if plan.identity is not None:
        identity = {
            "plan_digest": plan.identity.plan_digest,
            "plan_identity": plan.identity.plan_identity,
            "step_identities": list(plan.identity.step_identities),
        }
    return {
        "payload": dict(plan.payload),
        "steps": [{"kind": step.kind, "payload": dict(step.payload)} for step in plan.steps],
        "item_symbols": list(plan.item_symbols),
        "identity": identity,
    }


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
    """Typed result an executor returns for one attempt.

    ``pending_task_result`` (R05/D-recheck) carries the fully-built,
    digest-bound ``task_result.json`` document when the executor produced one
    without persisting it: the :class:`FrozenWorker` publishes it **inside the
    execution-right write seam** (after ``before_write``), so a worker that
    lost the right never writes the authoritative document.
    """

    state: str
    result_digest: str = ""
    errors: tuple[ExecutionError | str, ...] = ()
    evidence_digests: Mapping[str, str] = field(default_factory=dict)
    pending_task_result: Mapping[str, Any] | None = None


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


#: Reserved frozen-plan payload key carrying the executable scientific
#: configuration snapshot (the fully-resolved cccp config mapping the freeze
#: producer captured).  ``TaskRequest.from_dict`` ignores unknown keys (rule
#: S2), so the key rides the frozen body digest without polluting the typed
#: request; the executor pops it and restores ``TaskContext.config`` so the
#: independent worker executes with the submitted configuration instead of
#: whatever the release tree's defaults resolve to at call time (R02).
TASK_ENVIRONMENT_CONFIG_KEY: str = "environment"


@dataclass(frozen=True)
class CccpTaskExecutor:
    """Default :class:`TaskExecutorPort` backed by ``cccp.calculation.run_*``.

    The frozen ``plan.payload`` must be a serialized ``TaskRequest``
    (``cccp.calculation.requests.TaskRequest.to_dict()``). cccp imports are lazy
    so importing this module never pulls the scientific stack, and no ``acp``
    module is ever imported.

    The executor NEVER writes the authoritative ``task_result.json`` itself:
    it returns the digest-bound document on the outcome and the worker
    persists it inside the execution-right write seam (R05).
    """

    def execute(self, scope: ExecutionScope) -> TaskExecutionOutcome:
        import cccp.calculation as calculation
        from cccp.calculation.context import TaskContext
        from cccp.calculation.requests import TaskRequest

        request_payload = dict(scope.plan.payload)
        environment = request_payload.pop(TASK_ENVIRONMENT_CONFIG_KEY, None)
        request_payload.setdefault("task", scope.envelope.scientific_closure.task_kind)
        request = TaskRequest.from_dict(request_payload)
        # The worker's launch directory is not an artifact location. Bind the
        # runtime output root explicitly: task cores may otherwise pass None
        # through to a QC interface, whose default is the process CWD.
        output_dir = request.output_dir
        if output_dir is None:
            output_dir = scope.work_dir.resolve()
        elif not output_dir.is_absolute():
            output_dir = scope.work_dir.resolve() / output_dir
        request = replace(request, output_dir=output_dir)
        runner = getattr(calculation, f"run_{request.task.value}")
        context = TaskContext(
            workdir=scope.work_dir,
            input_base=scope.work_dir,
            config=environment if isinstance(environment, Mapping) and environment else None,
        )
        return outcome_from_task_result(runner(request, context=context))


# ── D01 completion authority: the frozen truth table ────────────────────────
#
# The CCCP task cores are the completion-fact authority.  This table only
# *serialises* the qualification they computed; it never invents per-task
# science policy.  A kind absent from ``TASK_COMPLETION_AUTHORITIES`` fails
# closed (``incomplete_result``), never an implicit success.
#
# ``step_requirements`` is an upstream-prerequisite consumer, not a
# current-task completion authority (see
# ``acp_services.runtime.step_requirements``).

#: Qualification tokens persisted in ``task_result.json`` and receipt evidence.
QUALIFICATION_COMPLETE: str = "complete"
QUALIFICATION_PARTIAL: str = "partial_result"
QUALIFICATION_INCOMPLETE: str = "incomplete_result"
QUALIFICATION_TASK_FAILED: str = "task_failed"

#: Receipt terminal states the truth table can emit.
_KNOWN_TASK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "blocked"})

#: Canonical ``JobStatus`` values (``acp_services.store.records.JobStatus``).
#: ``blocked`` has no canonical JobStatus — it maps onto ``failed``.
JOB_STATUS_BY_RECEIPT_STATE: Mapping[str, str] = MappingProxyType(
    {
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "blocked": "failed",
    }
)

#: Digest-bound task-result document (``WORK/00_RUNTIME/task_result.json``).
TASK_RESULT_SCHEMA: str = "acp_task_result_v1"
TASK_RESULT_RELATIVE_PATH: tuple[str, ...] = ("WORK", "00_RUNTIME", "task_result.json")

#: Metadata key carrying the qualification on a ``CalculationResult``.
PARTIAL_RESULT_METADATA_KEY: str = "partial_result"
NORMAL_REUSE_METADATA_KEY: str = "normal_reuse"


class TaskResultIntegrityError(WorkerError):
    """The persisted ``task_result.json`` cannot be trusted (fail closed)."""


@dataclass(frozen=True)
class RequiredFact:
    """One named completion fact checked against the task core's result."""

    name: str
    check: Callable[[Any], bool]


@dataclass(frozen=True)
class CompletionAuthority:
    """Frozen authority row for one :class:`cccp.calculation.requests.TaskKind`."""

    kind: str
    source: str
    required_facts: tuple[RequiredFact, ...]
    partial_policy: str


@dataclass(frozen=True)
class CompletionDecision:
    """One frozen truth-table row: TaskResult facts -> the complete matrix.

    Attributes:
        row: Truth-table row token ``a``-``e``.
        receipt_state: What the worker writes on the receipt.
        exit_code: Process exit code the worker entry must return.
        job_status: Canonical ``JobStatus`` value the controller applies.
        normal_reuse: Whether a later attempt may adopt this as normal science.
        publish: Whether a product may be published at all.
        publish_policy: ``normal``, ``partial`` (partial product only) or ``none``.
        partial_result: Qualification flag persisted in ``task_result.json``.
        qualification: One of the ``QUALIFICATION_*`` tokens.
        task: Canonical task-kind value (``""`` when unknown).
        status: Declared ``TaskResult.status`` (``""`` when missing).
        complete: Declared ``TaskResult.complete`` (``None`` when missing).
        required_facts: Names of the facts the authority requires.
        missing_facts: Required facts that could not be proven.
        errors: Errors to carry on the receipt (typed or verbatim).
    """

    row: str
    receipt_state: str
    exit_code: int
    job_status: str
    normal_reuse: bool
    publish: bool
    publish_policy: str
    partial_result: bool
    qualification: str
    task: str
    status: str
    complete: bool | None
    required_facts: tuple[str, ...] = ()
    missing_facts: tuple[str, ...] = ()
    errors: tuple[ExecutionError | str, ...] = ()


@dataclass(frozen=True)
class TaskResultQualification:
    """The authoritative qualification read back from ``task_result.json``."""

    task: str
    status: str
    complete: bool | None
    qualification: str
    partial_result: bool
    normal_reuse: bool
    publish: bool
    publish_policy: str
    receipt_state: str
    job_status: str
    exit_code: int
    content_digest: str


# ── fact predicates (pure, duck-typed; no cccp import at module load) ──────


def _payload(result: Any) -> Any:
    return getattr(result, "payload", None)


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _fact_energy(result: Any) -> bool:
    return _finite_number(getattr(result, "energy_hartree", None))


def _fact_geometry(result: Any) -> bool:
    coords = _sequence(getattr(result, "coordinates", None))
    return bool(coords) and all(isinstance(row, (list, tuple)) and len(row) == 3 for row in coords)


def _fact_symbols_aligned(result: Any) -> bool:
    symbols = _sequence(getattr(result, "symbols", None))
    coords = _sequence(getattr(result, "coordinates", None))
    return bool(symbols) and len(symbols) == len(coords)


def _fact_frequency_payload(result: Any) -> bool:
    payload = _payload(result)
    return payload is not None and hasattr(payload, "n_imaginary")


def _fact_scan_frames(result: Any) -> bool:
    frames = _sequence(getattr(_payload(result), "frames", None))
    if not frames:
        return False
    if not all(
        bool(getattr(frame, "success", False)) and getattr(frame, "geometry_ref", None) is not None
        for frame in frames
    ):
        return False
    metadata = getattr(result, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return metadata.get("frame_count") == len(frames) and (
        metadata.get("successful_frame_count") == len(frames)
    )


def _direction_value(entry: Any) -> Any:
    raw = getattr(entry, "direction", None)
    return getattr(raw, "value", raw)


def _fact_irc_directions(result: Any) -> bool:
    entries = _sequence(getattr(_payload(result), "directions", None))
    if not entries:
        return False
    metadata = getattr(result, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    requested = metadata.get("directions")
    if not isinstance(requested, (list, tuple)) or not requested:
        return False
    requested_set = {str(direction) for direction in requested}
    evidenced = False
    for entry in entries:
        direction = _direction_value(entry)
        if direction not in requested_set:
            return False
        if not bool(getattr(entry, "success", False)):
            continue
        if (
            getattr(entry, "coordinates", None) is None
            and getattr(entry, "trajectory_ref", None) is None
        ):
            return False
        evidenced = True
    return evidenced


def _fact_casscf_converged(result: Any) -> bool:
    return getattr(result, "converged", None) is True


def _fact_casscf_payload(result: Any) -> bool:
    payload = _payload(result)
    return payload is not None and hasattr(payload, "active_space")


def _fact_thermochemistry_payload(result: Any) -> bool:
    payload = _payload(result)
    if payload is None or not hasattr(payload, "gibbs_hartree"):
        return False
    return payload.gibbs_hartree is not None or payload.enthalpy_hartree is not None


def _fact_conformer_ensemble(result: Any) -> bool:
    payload = _payload(result)
    count = getattr(payload, "conformer_count", 0)
    return (
        payload is not None
        and getattr(payload, "ensemble_ref", None) is not None
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 1
    )


def _fact_md_frames(result: Any) -> bool:
    payload = _payload(result)
    count = getattr(payload, "n_frames", 0)
    return (
        payload is not None
        and getattr(payload, "trajectory_ref", None) is not None
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 1
    )


def _fact_clusters(result: Any) -> bool:
    payload = _payload(result)
    assignments = _sequence(getattr(payload, "assignments", None))
    return (
        payload is not None
        and bool(assignments)
        and getattr(payload, "clustered_ref", None) is not None
    )


def _fact_censo_records(result: Any) -> bool:
    payload = _payload(result)
    records = _sequence(getattr(payload, "records", None))
    return (
        payload is not None
        and bool(records)
        and getattr(payload, "refined_ensemble_ref", None) is not None
    )


def _fact_nmr_shieldings(result: Any) -> bool:
    payload = _payload(result)
    shieldings = getattr(payload, "shieldings", None)
    return payload is not None and isinstance(shieldings, Mapping) and bool(shieldings)


def _fact_xtb_path(result: Any) -> bool:
    payload = _payload(result)
    frames = _sequence(getattr(payload, "frames", None))
    return (
        payload is not None
        and bool(frames)
        and getattr(payload, "trajectory_ref", None) is not None
        and getattr(payload, "end_frame_index", None) is not None
    )


def _fact_gradient_rows(result: Any) -> bool:
    payload = _payload(result)
    gradients = _sequence(getattr(payload, "gradients", None))
    symbols = _sequence(getattr(result, "symbols", None))
    return (
        payload is not None
        and bool(gradients)
        and bool(symbols)
        and (len(gradients) == len(symbols))
    )


def _fact_constrained_energy(result: Any) -> bool:
    payload = _payload(result)
    return payload is not None and _finite_number(
        getattr(payload, "energy_effective_hartree", None)
    )


# ── the frozen per-TaskKind authority map ──────────────────────────────────


TASK_COMPLETION_AUTHORITIES: Mapping[str, CompletionAuthority] = MappingProxyType(
    {
        "singlepoint": CompletionAuthority(
            kind="singlepoint",
            source="cccp.calculation.tasks.singlepoint:184-239",
            required_facts=(RequiredFact("energy_hartree", _fact_energy),),
            partial_policy="no registered partial — missing energy is failed/incomplete",
        ),
        "optimize": CompletionAuthority(
            kind="optimize",
            source="cccp.calculation.tasks.optimize:467-599 (_successful_geometry/_result)",
            required_facts=(
                RequiredFact("coordinates", _fact_geometry),
                RequiredFact("symbols_aligned", _fact_symbols_aligned),
            ),
            partial_policy="no registered partial — non-converged is failed, no normal reuse",
        ),
        "frequency": CompletionAuthority(
            kind="frequency",
            source="cccp.calculation.tasks.frequency:177-229",
            required_facts=(RequiredFact("frequency_payload", _fact_frequency_payload),),
            partial_policy="n_imaginary stays payload evidence; not a completion gate",
        ),
        "scan": CompletionAuthority(
            kind="scan",
            source="cccp.calculation.tasks.scan:216-239,441-447 (_scan_completed)",
            required_facts=(RequiredFact("scan_frames", _fact_scan_frames),),
            partial_policy="valid frames remain payload evidence; not a normal completion",
        ),
        "irc": CompletionAuthority(
            kind="irc",
            source="cccp.calculation.tasks.irc:254-333,362-408 (completed_directions)",
            required_facts=(RequiredFact("irc_directions", _fact_irc_directions),),
            partial_policy="a missing requested direction is complete=False partial",
        ),
        "casscf": CompletionAuthority(
            kind="casscf",
            source="cccp.calculation.tasks.casscf:126-188,367-404 (validate_casscf_completion)",
            required_facts=(
                RequiredFact("cas_convergence", _fact_casscf_converged),
                RequiredFact("casscf_payload", _fact_casscf_payload),
            ),
            partial_policy="explicit false or missing convergence fact is failed/incomplete",
        ),
        "thermochemistry": CompletionAuthority(
            kind="thermochemistry",
            source="cccp.calculation.tasks.thermochemistry:120-165",
            required_facts=(
                RequiredFact("thermochemistry_payload", _fact_thermochemistry_payload),
            ),
            partial_policy="no registered partial — Shermo failure is failed",
        ),
        "conformer_search": CompletionAuthority(
            kind="conformer_search",
            source="cccp.calculation.requests:1612-1621 + tasks.conformer_search",
            required_facts=(RequiredFact("conformer_ensemble", _fact_conformer_ensemble),),
            partial_policy="complete=False keeps valid conformers at original indices",
        ),
        "md_sampling": CompletionAuthority(
            kind="md_sampling",
            source="cccp.calculation.requests:1622-1631 + tasks.md_sampling",
            required_facts=(RequiredFact("md_frames", _fact_md_frames),),
            partial_policy="complete=False keeps the valid frame prefix at original indices",
        ),
        "clustering": CompletionAuthority(
            kind="clustering",
            source="cccp.calculation.requests:1632-1641 + tasks.clustering",
            required_facts=(RequiredFact("cluster_assignments", _fact_clusters),),
            partial_policy="complete=False keeps assigned clusters and representatives",
        ),
        "censo_refine": CompletionAuthority(
            kind="censo_refine",
            source="cccp.calculation.requests:1642-1651 + tasks.censo_refine",
            required_facts=(RequiredFact("censo_records", _fact_censo_records),),
            partial_policy="complete=False keeps valid rows keyed by original frame_index",
        ),
        "nmr_shielding": CompletionAuthority(
            kind="nmr_shielding",
            source="cccp.calculation.requests:1652-1661 + tasks.nmr_shielding",
            required_facts=(RequiredFact("nmr_shieldings", _fact_nmr_shieldings),),
            partial_policy="complete=False keeps the shielded-atom subset at original keys",
        ),
        "xtb_path_search": CompletionAuthority(
            kind="xtb_path_search",
            source="cccp.calculation.requests:1662-1671 + tasks.xtb_path_search",
            required_facts=(RequiredFact("xtb_path_frames", _fact_xtb_path),),
            partial_policy="complete=False keeps valid frames at original indices",
        ),
        "orca_gradient": CompletionAuthority(
            kind="orca_gradient",
            source="cccp.calculation.requests:1672-1681 + tasks.orca_gradient",
            required_facts=(RequiredFact("gradient_rows", _fact_gradient_rows),),
            partial_policy="n/a — a gradient is all-or-nothing per atom order",
        ),
        "xtb_constrained_opt": CompletionAuthority(
            kind="xtb_constrained_opt",
            source="cccp.calculation.requests:1682-1694 + tasks.xtb_constrained_opt",
            required_facts=(RequiredFact("constrained_energy", _fact_constrained_energy),),
            partial_policy="non-converged geometry stays a completed result (payload evidence)",
        ),
    }
)


def _task_kind_value(result: Any) -> str | None:
    raw = getattr(result, "task", None)
    value = getattr(raw, "value", raw)
    return value if isinstance(value, str) and value else None


def _incomplete_error(message: str) -> ExecutionError:
    return ExecutionError(code=QUALIFICATION_INCOMPLETE, message=message, retryable=False)


def _missing_facts(result: Any, authority: CompletionAuthority) -> tuple[str, ...]:
    missing: list[str] = []
    for fact in authority.required_facts:
        try:
            satisfied = bool(fact.check(result))
        except (AttributeError, ArithmeticError, LookupError, TypeError, ValueError):
            satisfied = False
        if not satisfied:
            missing.append(fact.name)
    return tuple(missing)


def decide_task_result(result: Any) -> CompletionDecision:
    """Map one CCCP ``TaskResult`` onto the frozen truth table.

    Rows:

    * ``a`` — ``failed``/``cancelled``/``blocked``: same receipt state, exit 1,
      job ``failed``/``cancelled``, no normal reuse, no publication;
    * ``b`` — ``completed`` + ``complete=True`` + every required fact: exit 0,
      job ``completed``, normal reuse, normal publication;
    * ``c`` — ``completed`` + ``complete=False`` + every required fact: exit 0,
      job ``completed``, **no normal reuse**, partial publication only;
    * ``d`` — ``completed`` + ``complete=False`` (or a ``complete`` declaration
      that cannot be trusted) + a missing required fact: failed
      ``incomplete_result``, exit 1, job ``failed``, no publication;
    * ``e`` — unknown/missing status or unknown task kind: failed
      ``incomplete_result``, exit 1, job ``failed``, no publication.

    A missing required fact always outranks a ``complete=True`` declaration.
    """
    task = _task_kind_value(result)
    raw_status = getattr(result, "status", None)
    status = raw_status if isinstance(raw_status, str) else ""
    raw_complete = getattr(result, "complete", None)
    complete = raw_complete if isinstance(raw_complete, bool) else None
    raw_errors = tuple(getattr(result, "errors", ()) or ())
    authority = TASK_COMPLETION_AUTHORITIES.get(task or "")
    fact_names = tuple(fact.name for fact in authority.required_facts) if authority else ()

    if status not in _KNOWN_TASK_STATUSES:
        return CompletionDecision(
            row="e",
            receipt_state="failed",
            exit_code=1,
            job_status="failed",
            normal_reuse=False,
            publish=False,
            publish_policy="none",
            partial_result=False,
            qualification=QUALIFICATION_INCOMPLETE,
            task=task or "",
            status=status,
            complete=complete,
            required_facts=fact_names,
            missing_facts=(),
            errors=raw_errors
            + (
                _incomplete_error(
                    f"task result status {raw_status!r} is not one of "
                    f"{sorted(_KNOWN_TASK_STATUSES)}"
                ),
            ),
        )

    if status in ("failed", "cancelled", "blocked"):
        return CompletionDecision(
            row="a",
            receipt_state=status,
            exit_code=1,
            job_status=JOB_STATUS_BY_RECEIPT_STATE[status],
            normal_reuse=False,
            publish=False,
            publish_policy="none",
            partial_result=False,
            qualification=QUALIFICATION_TASK_FAILED,
            task=task or "",
            status=status,
            complete=complete,
            required_facts=fact_names,
            missing_facts=(),
            errors=raw_errors,
        )

    if authority is None:
        return CompletionDecision(
            row="e",
            receipt_state="failed",
            exit_code=1,
            job_status="failed",
            normal_reuse=False,
            publish=False,
            publish_policy="none",
            partial_result=False,
            qualification=QUALIFICATION_INCOMPLETE,
            task=task or "",
            status=status,
            complete=complete,
            required_facts=(),
            missing_facts=(),
            errors=raw_errors
            + (
                _incomplete_error(
                    f"no completion authority for task kind {task!r}; failing closed"
                ),
            ),
        )

    missing = _missing_facts(result, authority)
    if missing or complete is None:
        reason = (
            f"completion facts missing for {authority.kind!r}: {', '.join(missing)}"
            if missing
            else f"task result for {authority.kind!r} carries no boolean 'complete' fact"
        )
        return CompletionDecision(
            row="d",
            receipt_state="failed",
            exit_code=1,
            job_status="failed",
            normal_reuse=False,
            publish=False,
            publish_policy="none",
            partial_result=False,
            qualification=QUALIFICATION_INCOMPLETE,
            task=authority.kind,
            status=status,
            complete=complete,
            required_facts=fact_names,
            missing_facts=missing,
            errors=raw_errors + (_incomplete_error(reason),),
        )

    if complete is False:
        return CompletionDecision(
            row="c",
            receipt_state="completed",
            exit_code=0,
            job_status="completed",
            normal_reuse=False,
            publish=True,
            publish_policy="partial",
            partial_result=True,
            qualification=QUALIFICATION_PARTIAL,
            task=authority.kind,
            status=status,
            complete=False,
            required_facts=fact_names,
            missing_facts=(),
            errors=raw_errors,
        )

    return CompletionDecision(
        row="b",
        receipt_state="completed",
        exit_code=0,
        job_status="completed",
        normal_reuse=True,
        publish=True,
        publish_policy="normal",
        partial_result=False,
        qualification=QUALIFICATION_COMPLETE,
        task=authority.kind,
        status=status,
        complete=True,
        required_facts=fact_names,
        missing_facts=(),
        errors=raw_errors,
    )


def decision_for_failure(error: ExecutionError | str, *, task: str = "") -> CompletionDecision:
    """Row ``e`` for an executor/assembly failure with no ``TaskResult``."""
    authority = TASK_COMPLETION_AUTHORITIES.get(task)
    return CompletionDecision(
        row="e",
        receipt_state="failed",
        exit_code=1,
        job_status="failed",
        normal_reuse=False,
        publish=False,
        publish_policy="none",
        partial_result=False,
        qualification=QUALIFICATION_INCOMPLETE,
        task=task,
        status="",
        complete=None,
        required_facts=(tuple(fact.name for fact in authority.required_facts) if authority else ()),
        missing_facts=(),
        errors=(error,),
    )


# ── digest-bound task_result.json (atomic) ─────────────────────────────────


def task_result_path(work_dir: Path | str) -> Path:
    """Canonical ``WORK/00_RUNTIME/task_result.json`` location for an attempt."""
    return Path(work_dir).joinpath(*TASK_RESULT_RELATIVE_PATH)


def _canonical_blob(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _content_digest(payload: Mapping[str, Any]) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(_canonical_blob(payload)).hexdigest()


def _result_digest(result: Any) -> str:
    to_dict = getattr(result, "to_dict", None)
    if to_dict is None:
        return ""
    return _content_digest(dict(to_dict()))


def _result_body(result: Any | None) -> dict[str, Any] | None:
    """Full scientific body of *result* (``TaskResult.to_dict()``), when able.

    R01: the persisted document must carry the complete scientific result —
    energy, coordinates, frequencies, payload and artifact references — not
    only the qualification and a digest, or a completed task has no
    recoverable/displayable science after publication or restart.
    """
    if result is None:
        return None
    to_dict = getattr(result, "to_dict", None)
    if to_dict is None:
        return None
    try:
        body = dict(to_dict())
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) and body else None


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, sort_keys=True, indent=2) + "\n"
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


def build_task_result_document(
    decision: CompletionDecision,
    result: Any | None = None,
) -> dict[str, Any]:
    """Build the digest-bound ``task_result.json`` document (pure).

    The document carries the qualification matrix, the receipt/job mapping,
    the result digest and — since R01 — the **full scientific result body**
    under ``result``.  The ``content_digest`` covers every other field
    including the body, so a tampered document is rejected by
    :func:`read_task_result`.
    """
    authority = TASK_COMPLETION_AUTHORITIES.get(decision.task)
    document: dict[str, Any] = {
        "schema": TASK_RESULT_SCHEMA,
        "task": decision.task,
        "status": decision.status,
        "complete": decision.complete,
        "truth_table_row": decision.row,
        "qualification": decision.qualification,
        "partial_result": decision.partial_result,
        "normal_reuse": decision.normal_reuse,
        "publish": decision.publish,
        "publish_policy": decision.publish_policy,
        "receipt_state": decision.receipt_state,
        "job_status": decision.job_status,
        "exit_code": decision.exit_code,
        "required_facts": list(decision.required_facts),
        "missing_required_facts": list(decision.missing_facts),
        "authority": authority.source if authority is not None else "",
        "result_digest": _result_digest(result) if result is not None else "",
        "written_at": _utc_now_iso(),
    }
    body = _result_body(result)
    if body is not None:
        document["result"] = body
    document["content_digest"] = _content_digest(document)
    return document


def write_task_result(
    work_dir: Path | str,
    decision: CompletionDecision,
    result: Any | None = None,
) -> str:
    """Persist the authoritative qualification; return its content digest.

    The document is written atomically (temp file + ``os.replace``) and carries
    the full scientific result body plus a ``content_digest`` over every other
    field, so a tampered document can be rejected by :func:`read_task_result`.
    """
    document = build_task_result_document(decision, result)
    _write_json_atomic(task_result_path(work_dir), document)
    return str(document["content_digest"])


def write_task_result_document(work_dir: Path | str, document: Mapping[str, Any]) -> str:
    """Persist an already-built digest-bound document; return its digest.

    The R05 write seam: executors return built documents on the outcome and
    the worker calls this only after re-verifying the execution right.
    """
    _write_json_atomic(task_result_path(work_dir), dict(document))
    return str(document.get("content_digest") or "")


def read_task_result(work_dir: Path | str) -> dict[str, Any] | None:
    """Read and digest-verify ``task_result.json`` (``None`` when absent).

    Raises:
        TaskResultIntegrityError: When the document is unreadable, carries an
            unknown schema, or its ``content_digest`` does not match.
    """
    target = task_result_path(work_dir)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskResultIntegrityError(f"task_result at {target} is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise TaskResultIntegrityError(f"task_result at {target} is not a mapping")
    document = dict(payload)
    if document.get("schema") != TASK_RESULT_SCHEMA:
        raise TaskResultIntegrityError(
            f"task_result at {target} has schema {document.get('schema')!r}; "
            f"expected {TASK_RESULT_SCHEMA!r}"
        )
    stored = document.pop("content_digest", None)
    if not isinstance(stored, str) or not stored:
        raise TaskResultIntegrityError(f"task_result at {target} carries no content_digest")
    if _content_digest(document) != stored:
        raise TaskResultIntegrityError(f"task_result at {target} failed its content digest")
    document["content_digest"] = stored
    return document


def _document_bool(document: Mapping[str, Any], key: str) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise TaskResultIntegrityError(f"task_result field {key!r} must be a boolean")
    return value


def _document_int(document: Mapping[str, Any], key: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskResultIntegrityError(f"task_result field {key!r} must be an integer")
    return value


def read_task_result_qualification(work_dir: Path | str) -> TaskResultQualification | None:
    """Read the persisted qualification (``None`` when no document exists)."""
    document = read_task_result(work_dir)
    if document is None:
        return None
    raw_complete = document.get("complete")
    if raw_complete is not None and not isinstance(raw_complete, bool):
        raise TaskResultIntegrityError("task_result field 'complete' must be a boolean or null")
    qualification = document.get("qualification")
    if not isinstance(qualification, str) or not qualification:
        raise TaskResultIntegrityError("task_result field 'qualification' is required")
    return TaskResultQualification(
        task=str(document.get("task") or ""),
        status=str(document.get("status") or ""),
        complete=raw_complete,
        qualification=qualification,
        partial_result=_document_bool(document, "partial_result"),
        normal_reuse=_document_bool(document, "normal_reuse"),
        publish=_document_bool(document, "publish"),
        publish_policy=str(document.get("publish_policy") or ""),
        receipt_state=str(document.get("receipt_state") or ""),
        job_status=str(document.get("job_status") or ""),
        exit_code=_document_int(document, "exit_code"),
        content_digest=str(document.get("content_digest") or ""),
    )


def qualification_metadata(work_dir: Path | str) -> dict[str, Any]:
    """Metadata fragment consumers merge onto an adopted result (empty if absent).

    A completed-partial fact stays returnable on redelivery/reuse of the same
    execution fact but must never satisfy a normal prerequisite; consumers read
    ``partial_result``/``normal_reuse`` from here (or from the same keys on a
    ``CalculationResult.metadata``).
    """
    qualification = read_task_result_qualification(work_dir)
    if qualification is None:
        return {}
    return {
        PARTIAL_RESULT_METADATA_KEY: qualification.partial_result,
        NORMAL_REUSE_METADATA_KEY: qualification.normal_reuse,
        "qualification": qualification.qualification,
    }


def _evidence_for(decision: CompletionDecision, task_result_digest: str) -> dict[str, str]:
    evidence = {
        "qualification": decision.qualification,
        PARTIAL_RESULT_METADATA_KEY: "true" if decision.partial_result else "false",
        NORMAL_REUSE_METADATA_KEY: "true" if decision.normal_reuse else "false",
        "publish": "true" if decision.publish else "false",
        "publish_policy": decision.publish_policy,
        "job_status": decision.job_status,
        "exit_code": str(decision.exit_code),
        "truth_table_row": decision.row,
        "required_facts": ",".join(decision.required_facts),
        "missing_required_facts": ",".join(decision.missing_facts),
    }
    if task_result_digest:
        evidence["task_result_digest"] = task_result_digest
    return evidence


def outcome_from_decision(
    decision: CompletionDecision, *, work_dir: Path | str | None = None
) -> TaskExecutionOutcome:
    """Build the executor outcome for *decision*, persisting it when given a root."""
    document = build_task_result_document(decision)
    if work_dir is not None:
        write_task_result_document(work_dir, document)
    return TaskExecutionOutcome(
        state=decision.receipt_state,
        result_digest="",
        errors=decision.errors,
        evidence_digests=_evidence_for(decision, str(document["content_digest"])),
        pending_task_result=document,
    )


def outcome_from_task_result(
    result: Any, *, work_dir: Path | str | None = None
) -> TaskExecutionOutcome:
    """Apply the truth table and (optionally) persist the qualification.

    ``work_dir`` is the task root: the document lands at
    ``WORK/00_RUNTIME/task_result.json``.  Persistence happens **before** the
    receipt is written, so the digest-bound qualification always exists for a
    receipt that references it.

    R05: when ``work_dir`` is omitted the outcome still carries the built
    document as ``pending_task_result``; the :class:`FrozenWorker` publishes
    it after the execution-right seam, so a worker that lost the right never
    writes the authoritative document.
    """
    decision = decide_task_result(result)
    document = build_task_result_document(decision, result)
    if work_dir is not None:
        write_task_result_document(work_dir, document)
    return TaskExecutionOutcome(
        state=decision.receipt_state,
        result_digest=_result_digest(result),
        errors=decision.errors,
        evidence_digests=_evidence_for(decision, str(document["content_digest"])),
        pending_task_result=document,
    )


def _outcome_from_task_result(
    result: Any, *, work_dir: Path | str | None = None
) -> TaskExecutionOutcome:
    """Backward-compatible alias for :func:`outcome_from_task_result`."""
    return outcome_from_task_result(result, work_dir=work_dir)


def receipt_exit_code(receipt: ExecutionReceipt) -> int:
    """The process exit code for *receipt* (row ``b``/``c`` are 0; else 1)."""
    return 0 if receipt.state == "completed" else 1


def job_status_for_receipt(receipt: ExecutionReceipt) -> str:
    """Canonical ``JobStatus`` value the controller applies for *receipt*."""
    try:
        return JOB_STATUS_BY_RECEIPT_STATE[receipt.state]
    except KeyError as exc:  # pragma: no cover - receipt validation prevents this
        raise ReceiptConflictError(f"receipt state {receipt.state!r} has no job status") from exc


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
        before_write: Callable[[], None] | None = None,
    ) -> ExecutionReceipt:
        """Execute the attempt once and return the (atomically written) receipt.

        Idempotent: an existing receipt for the same full frozen identity is
        returned without re-execution; an existing receipt for a different
        identity is rejected (never silently overwritten).

        ``validate()`` always runs first — even when a receipt already exists —
        so a tampered plan body, a plan/envelope identity mismatch, or a
        prerequisite gate failure is rejected before a stale receipt can be
        mistaken for the current attempt's result.

        ``before_write`` is the execution-right write seam (07-A): the caller
        re-checks that it still holds the exclusive right immediately before
        any authoritative write is published and raises otherwise.  A worker
        that lost the right therefore never writes this path — nor the
        authoritative ``task_result.json`` (R05): an executor-returned
        ``pending_task_result`` document is persisted only after the seam.
        """
        existing = read_receipt(receipt_path)
        self.validate(envelope, plan, prior_steps=prior_steps, item_symbols=item_symbols)
        if existing is not None:
            return _reuse_or_conflict(existing, envelope)

        scope = ExecutionScope(
            envelope=envelope,
            plan=plan,
            work_dir=Path(work_dir),
            storage_binding=storage_binding,
        )
        outcome = self.executor.execute(scope)
        if before_write is not None:
            before_write()
        if outcome.pending_task_result is not None:
            write_task_result_document(work_dir, outcome.pending_task_result)
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
    comparison = compare_receipt_identity(existing, envelope)
    if comparison.matches:
        return existing
    raise ReceiptConflictError(
        "existing receipt does not carry the frozen envelope's identity "
        f"({comparison.describe()}); refusing to reuse or overwrite it",
        comparison=comparison,
    )
