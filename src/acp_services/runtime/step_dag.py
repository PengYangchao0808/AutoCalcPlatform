"""B18b — one platform task with bounded, persistent science steps.

The DA DAG representation chosen by B18 is deliberately **one platform task
with multiple persistent steps** (the default): it preserves the unique
``TaskRef``.  This module is the step model and the *only* loop; the scientific
compute stays in ``cccp.calculation`` and is injected as a dispatcher.

Layer duties (frozen):

* **Runtime (`BoundedStepDagExecutor`)** owns the loop, readiness, per-step
  control propagation, budget accounting, recovery and publication retry.
* **CCCP (`cccp.calculation.run_*`)** only computes one step.  It never decides
  what runs next and never publishes a platform artifact.

The DAG is intentionally small: at most ``Limits.max_steps`` nodes, a linear
chain by default (one consumer per producer), no arbitrary branching and no
second orchestrator.  ``irc`` is a first-class bounded step kind and delegates
to the existing ``cccp.calculation.run_irc`` task core.

Step identity has three layers (mirrors the frozen approval identity):
``plan_identity`` (what was approved), ``step_identity`` (the actual upstream
byte digests + materialized parameters + implementation/release version frozen
before dispatch) and the worker receipt/result digests.  Publication state is
separate from computation state so a publication failure never re-runs compute.

Author: ACP Services (B lane, B18b).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from acp_services.runtime.canonical_digest import CanonicalDigestError, canonical_digest
from acp_services.runtime.science_binding import document_digest
from cccp.calculation.requests import TaskKind

__all__ = [
    "COMPUTE_OWNER",
    "DEFAULT_LIMITS",
    "EXECUTION_LOOP_OWNER",
    "STEP_ENTRYPOINTS",
    "STEP_TASK_KINDS",
    "BoundedStepDag",
    "BoundedStepDagExecutor",
    "BudgetCaps",
    "BudgetUsage",
    "ControlIntent",
    "DagRunState",
    "Limits",
    "OutputRef",
    "PublishStatus",
    "ScienceStepKind",
    "StepDagError",
    "StepDispatchContext",
    "StepIdentity",
    "StepOutcome",
    "StepSpec",
    "StepState",
    "StepStatus",
    "cccp_compute_dispatch",
    "materialize_step_identity",
    "verify_worker_chain",
]

#: The layer that loops.
EXECUTION_LOOP_OWNER = "acp_services.runtime.step_dag.BoundedStepDagExecutor"
#: The layer that only computes.
COMPUTE_OWNER = "cccp.calculation.tasks"


class StepStatus(str, Enum):
    """Per-step completion/dependency state (execution, not scientific claim)."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    UPSTREAM_FAILED = "upstream_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Statuses that are *not* a completion fact.  ``completed`` is the only
#: execution-complete state; a missing upstream is never silently promoted.
TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {
        StepStatus.COMPLETED,
        StepStatus.BLOCKED,
        StepStatus.UPSTREAM_FAILED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    }
)
_UNFINISHED: frozenset[StepStatus] = frozenset(
    {StepStatus.PENDING, StepStatus.READY, StepStatus.RUNNING}
)


class PublishStatus(str, Enum):
    """Publication state, deliberately separate from computation state."""

    NOT_PUBLISHED = "not_published"
    PUBLISHED = "published"
    FAILED = "failed"


class ControlIntent(str, Enum):
    """Plan-level control propagated to every unfinished step."""

    PAUSE = "pause"
    CONTINUE = "continue"
    CANCEL = "cancel"


class ScienceStepKind(str, Enum):
    """Bounded science step kinds (legacy ``StepKind`` is untouched).

    ``irc`` is added here rather than to the legacy ``CalculationPlan``
    ``StepKind`` because that enum's "IRC is independent" invariant is still
    the contract for the ordinary workflow dispatch.  A bounded DA DAG may use
    IRC as one step of one task; the legacy independent-IRC path is preserved.
    """

    SINGLEPOINT = "singlepoint"
    OPTIMIZE = "optimize"
    FREQUENCY = "frequency"
    IRC = "irc"


#: Step kind -> cccp task kind (the existing task core).
STEP_TASK_KINDS: Mapping[ScienceStepKind, TaskKind] = {
    ScienceStepKind.SINGLEPOINT: TaskKind.SINGLEPOINT,
    ScienceStepKind.OPTIMIZE: TaskKind.OPTIMIZE,
    ScienceStepKind.FREQUENCY: TaskKind.FREQUENCY,
    ScienceStepKind.IRC: TaskKind.IRC,
}

#: Step kind -> the existing CCCP entry point (never a second executor).
STEP_ENTRYPOINTS: Mapping[ScienceStepKind, str] = {
    ScienceStepKind.SINGLEPOINT: "cccp.calculation.run_singlepoint",
    ScienceStepKind.OPTIMIZE: "cccp.calculation.run_optimize",
    ScienceStepKind.FREQUENCY: "cccp.calculation.run_frequency",
    ScienceStepKind.IRC: "cccp.calculation.run_irc",
}


class StepDagError(ValueError):
    """A bounded-DAG violation with a stable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Step model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputRef:
    """Producer step + artifact role + expected content digest.

    ``expected_digest`` is optional because the digest is only known after the
    producer computes.  When it is set, a consumer refuses a different digest
    (``digest_mismatch``) instead of adopting it.
    """

    from_step: str
    product_kind: str
    expected_digest: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        if not self.from_step:
            raise StepDagError("invalid_output_ref", "from_step is required")
        if not self.product_kind:
            raise StepDagError("invalid_output_ref", "product_kind is required")


@dataclass(frozen=True, slots=True)
class StepSpec:
    """One stable step of one platform task."""

    step_id: str
    kind: ScienceStepKind
    intent: str
    required: bool = True
    diagnostic_only: bool = False
    parameters: Mapping[str, Any] = field(default_factory=dict)
    consumes: tuple[OutputRef, ...] = ()
    produces: tuple[OutputRef, ...] = ()
    structure_role: str = "minimum"
    implementation_version: str = ""
    capability: str = ""
    max_attempts: int = 1
    qc_invocations: int = 1
    bsub: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScienceStepKind(self.kind))
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(self, "consumes", tuple(self.consumes))
        object.__setattr__(self, "produces", tuple(self.produces))
        if not self.step_id or any(
            not (ch.islower() or ch.isdigit() or ch in "_.-") for ch in self.step_id
        ):
            raise StepDagError(
                "invalid_step_id",
                f"step_id {self.step_id!r} must be lowercase [a-z0-9_.-]+",
            )
        if self.required and self.diagnostic_only:
            raise StepDagError(
                "required_diagnostic_conflict",
                f"{self.step_id}: required and diagnostic_only are mutually exclusive",
            )
        if self.max_attempts < 1:
            raise StepDagError("invalid_attempts", f"{self.step_id}: max_attempts must be >= 1")
        if self.qc_invocations < 0:
            raise StepDagError("invalid_qc", f"{self.step_id}: qc_invocations must be >= 0")
        produced = [(ref.from_step, ref.product_kind) for ref in self.produces]
        if len(set(produced)) != len(produced):
            raise StepDagError(
                "duplicate_product", f"{self.step_id}: duplicate (step, product_kind) output"
            )
        for ref in self.produces:
            if ref.from_step != self.step_id:
                raise StepDagError(
                    "producer_mismatch",
                    f"{self.step_id} declares a product owned by {ref.from_step!r}",
                )


@dataclass(frozen=True, slots=True)
class Limits:
    """Bounded-DAG limits (linear chain by default; never arbitrary branching)."""

    max_steps: int = 8
    max_edges: int = 8
    max_consumers_per_step: int = 1
    max_fan_in: int = 1

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise StepDagError("invalid_limits", "max_steps must be >= 1")
        if self.max_consumers_per_step < 1:
            raise StepDagError("invalid_limits", "max_consumers_per_step must be >= 1")


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True, slots=True)
class BoundedStepDag:
    """One platform task's bounded acyclic step graph."""

    recipe_id: str
    task_ref: str
    steps: tuple[StepSpec, ...]
    limits: Limits = DEFAULT_LIMITS

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.recipe_id:
            raise StepDagError("invalid_recipe", "recipe_id is required")
        if not self.task_ref:
            raise StepDagError("invalid_task_ref", "one non-empty TaskRef is required")

    def by_id(self) -> dict[str, StepSpec]:
        return {step.step_id: step for step in self.steps}

    def validate(self) -> list[str]:
        """Return every structural violation (empty means valid)."""
        errors: list[str] = []
        limits = self.limits
        if not self.steps:
            errors.append("dag.empty")
            return errors
        if len(self.steps) > limits.max_steps:
            errors.append(f"dag.too_many_steps:{len(self.steps)}>{limits.max_steps}")
        ids = [step.step_id for step in self.steps]
        if len(set(ids)) != len(ids):
            errors.append("dag.duplicate_step_id")
        by_id = self.by_id()
        edge_count = 0
        consumers: dict[str, int] = {}
        for step in self.steps:
            fan_in = 0
            for ref in step.consumes:
                edge_count += 1
                producer = by_id.get(ref.from_step)
                if producer is None:
                    errors.append(f"dag.dangling_output_ref:{step.step_id}->{ref.from_step}")
                    continue
                if ref.from_step == step.step_id:
                    errors.append(f"dag.self_edge:{step.step_id}")
                    continue
                fan_in += 1
                consumers[ref.from_step] = consumers.get(ref.from_step, 0) + 1
                products = {item.product_kind for item in producer.produces}
                if ref.product_kind not in products:
                    errors.append(
                        "dag.missing_product:"
                        f"{producer.step_id} does not produce {ref.product_kind!r}"
                    )
                if producer.diagnostic_only and ref.required:
                    errors.append(
                        f"dag.diagnostic_satisfies_required:{producer.step_id}->{step.step_id}"
                    )
            if fan_in > limits.max_fan_in:
                errors.append(f"dag.fan_in_exceeded:{step.step_id}")
        if edge_count > limits.max_edges:
            errors.append(f"dag.too_many_edges:{edge_count}>{limits.max_edges}")
        for producer_id, count in consumers.items():
            if count > limits.max_consumers_per_step:
                errors.append(f"dag.fan_out_exceeded:{producer_id}:{count}")
        if not errors:
            try:
                self.topological_order()
            except StepDagError as exc:
                errors.append(exc.code)
        return errors

    def topological_order(self) -> list[str]:
        """Return a deterministic topological order (raises on a cycle)."""
        by_id = self.by_id()
        indegree = {step.step_id: 0 for step in self.steps}
        outgoing: dict[str, list[str]] = {step.step_id: [] for step in self.steps}
        for step in self.steps:
            for ref in step.consumes:
                if ref.from_step not in by_id:
                    raise StepDagError("dangling_output_ref", f"{step.step_id}->{ref.from_step}")
                indegree[step.step_id] += 1
                outgoing[ref.from_step].append(step.step_id)
        ready = sorted(step_id for step_id, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in sorted(outgoing[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
            ready.sort()
        if len(order) != len(self.steps):
            raise StepDagError("dag.cycle", "the step graph contains a cycle")
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "acp2.runtime.step-dag.v1",
            "recipe_id": self.recipe_id,
            "task_ref": self.task_ref,
            "execution_unit": {
                "loop_owner": EXECUTION_LOOP_OWNER,
                "compute_owner": COMPUTE_OWNER,
                "one_platform_task": True,
            },
            "limits": {
                "max_steps": self.limits.max_steps,
                "max_edges": self.limits.max_edges,
                "max_consumers_per_step": self.limits.max_consumers_per_step,
                "max_fan_in": self.limits.max_fan_in,
            },
            "steps": [
                {
                    "step_id": step.step_id,
                    "kind": step.kind.value,
                    "task_kind": STEP_TASK_KINDS[step.kind].value,
                    "entrypoint": STEP_ENTRYPOINTS[step.kind],
                    "intent": step.intent,
                    "required": step.required,
                    "diagnostic_only": step.diagnostic_only,
                    "capability": step.capability,
                    "implementation_version": step.implementation_version,
                    "structure_role": step.structure_role,
                    "parameters": dict(step.parameters),
                    "consumes": [
                        {
                            "from_step": ref.from_step,
                            "product_kind": ref.product_kind,
                            "expected_digest": ref.expected_digest,
                            "required": ref.required,
                        }
                        for ref in step.consumes
                    ],
                    "produces": [
                        {
                            "from_step": ref.from_step,
                            "product_kind": ref.product_kind,
                            "required": ref.required,
                        }
                        for ref in step.produces
                    ],
                }
                for step in self.steps
            ],
        }


# ---------------------------------------------------------------------------
# Step identity (plan identity < step identity < worker receipt/result)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepIdentity:
    """Materialized identity frozen immediately before a step dispatch."""

    step_id: str
    step_index: int
    plan_identity: str
    implementation_version: str
    release_id: str
    upstream_digests: tuple[str, ...]
    materialized_parameters_digest: str
    step_identity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "plan_identity": self.plan_identity,
            "implementation_version": self.implementation_version,
            "release_id": self.release_id,
            "upstream_digests": list(self.upstream_digests),
            "materialized_parameters_digest": self.materialized_parameters_digest,
            "step_identity": self.step_identity,
        }


def materialize_step_identity(
    step: StepSpec,
    *,
    step_index: int,
    plan_identity: str,
    upstream_digests: Sequence[str],
    release_id: str,
) -> StepIdentity:
    """Freeze the step identity with the single B16 canonicalization.

    ``plan_identity`` is the approved plan digest/identity; ``release_id`` is
    the verified software/plugin release; the upstream digests are the *actual*
    bytes this step will consume.  A missing release or plan identity is a
    precondition failure, not an identity with empty fields.
    """
    if not plan_identity:
        raise StepDagError("missing_plan_identity", "plan identity is required before dispatch")
    if not release_id:
        raise StepDagError(
            "missing_release", "a verified software/plugin release is required before dispatch"
        )
    if not step.implementation_version:
        raise StepDagError(
            "missing_implementation", f"{step.step_id}: implementation_version is required"
        )
    parameters_digest = document_digest(dict(step.parameters))
    payload: dict[str, Any] = {
        "plan_identity": plan_identity,
        "step_id": step.step_id,
        "step_index": int(step_index),
        "task_kind": STEP_TASK_KINDS[step.kind].value,
        "implementation_version": step.implementation_version,
        "release_id": release_id,
        "materialized_parameters_digest": parameters_digest,
        "upstream_digests": list(upstream_digests),
    }
    try:
        identity = canonical_digest(payload, set_fields=("upstream_digests",))
    except CanonicalDigestError as exc:
        raise StepDagError("invalid_step_identity", f"{step.step_id}: {exc}") from exc
    return StepIdentity(
        step_id=step.step_id,
        step_index=int(step_index),
        plan_identity=plan_identity,
        implementation_version=step.implementation_version,
        release_id=release_id,
        upstream_digests=tuple(str(item) for item in upstream_digests),
        materialized_parameters_digest=parameters_digest,
        step_identity=identity,
    )


def verify_worker_chain(
    *,
    plan_identity: str,
    release_id: str,
    input_digest: str,
    atom_mapping_digest: str,
    receipt: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """Verify receipt/result agree with the frozen identity before adoption.

    A mismatch is never repaired: the caller must not adopt the result.
    """
    checks = {
        "plan_identity": plan_identity,
        "release_id": release_id,
        "input_digest": input_digest,
        "atom_mapping_digest": atom_mapping_digest,
    }
    for field_name, expected in checks.items():
        for label, document in (("receipt", receipt), ("result", result)):
            if field_name in document and document.get(field_name) != expected:
                raise StepDagError(
                    "digest_mismatch",
                    f"{label}.{field_name} {document.get(field_name)!r} != {expected!r}",
                )
    if receipt.get("result_digest") and result.get("result_digest"):
        if receipt.get("result_digest") != result.get("result_digest"):
            raise StepDagError(
                "digest_mismatch",
                "receipt.result_digest != result.result_digest",
            )


# ---------------------------------------------------------------------------
# Budget accounting (TaskRef / step / QC invocation / attempt / bsub)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetCaps:
    """Frozen per-task caps (B17 units reused)."""

    task_refs: int = 1
    steps: int = 8
    qc_invocations: int = 8
    attempts: int = 8
    bsub: int = 0


@dataclass(slots=True)
class BudgetUsage:
    """Observed cumulative counts (one dict is the receipt projection)."""

    task_refs: int = 1
    steps: int = 0
    qc_invocations: int = 0
    attempts: int = 0
    bsub: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "task_refs": self.task_refs,
            "steps": self.steps,
            "qc_invocations": self.qc_invocations,
            "attempts": self.attempts,
            "bsub": self.bsub,
        }


class BudgetExceededError(StepDagError):
    """A dispatch would exceed the frozen caps; it never runs."""

    def __init__(self, field_name: str, requested: int, cap: int) -> None:
        super().__init__("budget_exceeded", f"{field_name} would reach {requested} > cap {cap}")
        self.field_name = field_name
        self.requested = requested
        self.cap = cap


# ---------------------------------------------------------------------------
# Executor state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StepOutcome:
    """What a compute layer returns: content digests + opaque publish payload."""

    output_digests: dict[str, str] = field(default_factory=dict)
    receipt_digest: str = ""
    result_digest: str = ""
    publish_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_digests": dict(self.output_digests),
            "receipt_digest": self.receipt_digest,
            "result_digest": self.result_digest,
            "publish_payload": dict(self.publish_payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepOutcome:
        outputs = payload.get("output_digests")
        publish_payload = payload.get("publish_payload")
        return cls(
            output_digests={
                str(key): str(value)
                for key, value in (outputs or {}).items()
                if isinstance(key, str) and isinstance(value, str)
            },
            receipt_digest=str(payload.get("receipt_digest") or ""),
            result_digest=str(payload.get("result_digest") or ""),
            publish_payload=dict(publish_payload) if isinstance(publish_payload, Mapping) else {},
        )


@dataclass(slots=True)
class StepState:
    """Persistent per-step state (completion, dependency and publish separately)."""

    step_id: str
    status: StepStatus = StepStatus.PENDING
    publish_status: PublishStatus = PublishStatus.NOT_PUBLISHED
    attempts: int = 0
    output_digests: dict[str, str] = field(default_factory=dict)
    upstream_digests: tuple[str, ...] = ()
    step_identity: str = ""
    receipt_digest: str = ""
    result_digest: str = ""
    reason: str = ""
    publication_reason: str = ""
    publish_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "publish_status": self.publish_status.value,
            "attempts": self.attempts,
            "output_digests": dict(self.output_digests),
            "upstream_digests": list(self.upstream_digests),
            "step_identity": self.step_identity,
            "receipt_digest": self.receipt_digest,
            "result_digest": self.result_digest,
            "reason": self.reason,
            "publication_reason": self.publication_reason,
            "publish_payload": dict(self.publish_payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepState:
        try:
            status = StepStatus(str(payload.get("status") or StepStatus.PENDING.value))
        except ValueError:
            status = StepStatus.PENDING
        try:
            publish_status = PublishStatus(
                str(payload.get("publish_status") or PublishStatus.NOT_PUBLISHED.value)
            )
        except ValueError:
            publish_status = PublishStatus.NOT_PUBLISHED
        outputs = payload.get("output_digests")
        return cls(
            step_id=str(payload.get("step_id") or ""),
            status=status,
            publish_status=publish_status,
            attempts=int(payload.get("attempts") or 0),
            output_digests={
                str(key): str(value)
                for key, value in (outputs or {}).items()
                if isinstance(key, str) and isinstance(value, str)
            },
            upstream_digests=tuple(str(item) for item in payload.get("upstream_digests") or []),
            step_identity=str(payload.get("step_identity") or ""),
            receipt_digest=str(payload.get("receipt_digest") or ""),
            result_digest=str(payload.get("result_digest") or ""),
            reason=str(payload.get("reason") or ""),
            publication_reason=str(payload.get("publication_reason") or ""),
            publish_payload=dict(payload.get("publish_payload") or {}),
        )


@dataclass(slots=True)
class DagRunState:
    """Persistent run state for one platform task (no second task per step)."""

    task_ref: str
    states: dict[str, StepState] = field(default_factory=dict)
    paused: bool = False
    cancelled: bool = False
    budget: BudgetUsage = field(default_factory=BudgetUsage)
    dispatch_count: int = 0
    publication_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "acp2.runtime.step-dag-state.v1",
            "task_ref": self.task_ref,
            "paused": self.paused,
            "cancelled": self.cancelled,
            "dispatch_count": self.dispatch_count,
            "publication_attempts": self.publication_attempts,
            "budget": self.budget.to_dict(),
            "states": [self.states[step_id].to_dict() for step_id in sorted(self.states)],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DagRunState:
        budget = payload.get("budget") if isinstance(payload.get("budget"), Mapping) else {}
        states: dict[str, StepState] = {}
        for entry in payload.get("states") or []:
            if not isinstance(entry, Mapping):
                continue
            state = StepState.from_dict(entry)
            if state.step_id:
                states[state.step_id] = state
        return cls(
            task_ref=str(payload.get("task_ref") or ""),
            states=states,
            paused=bool(payload.get("paused", False)),
            cancelled=bool(payload.get("cancelled", False)),
            budget=BudgetUsage(
                task_refs=int(budget.get("task_refs", 1) or 1),
                steps=int(budget.get("steps", 0) or 0),
                qc_invocations=int(budget.get("qc_invocations", 0) or 0),
                attempts=int(budget.get("attempts", 0) or 0),
                bsub=int(budget.get("bsub", 0) or 0),
            ),
            dispatch_count=int(payload.get("dispatch_count") or 0),
            publication_attempts=int(payload.get("publication_attempts") or 0),
        )

    def statuses(self) -> dict[str, str]:
        return {step_id: state.status.value for step_id, state in self.states.items()}


@dataclass(frozen=True, slots=True)
class StepDispatchContext:
    """Everything the compute layer may see for one step (no control authority)."""

    step: StepSpec
    step_identity: StepIdentity
    upstream_digests: tuple[str, ...]
    task_ref: str
    release_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "release_id": self.release_id,
            "upstream_digests": list(self.upstream_digests),
            "step_identity": self.step_identity.to_dict(),
            "step": {
                "step_id": self.step.step_id,
                "kind": self.step.kind.value,
                "task_kind": STEP_TASK_KINDS[self.step.kind].value,
                "entrypoint": STEP_ENTRYPOINTS[self.step.kind],
            },
        }


def _persisted_running(persisted: Mapping[str, Any], step_id: str) -> bool:
    """Whether the persisted state says *step_id* was interrupted while running."""
    for entry in persisted.get("states") or ():
        if isinstance(entry, Mapping) and str(entry.get("step_id")) == str(step_id):
            return str(entry.get("status")) == "running"
    return False


DispatchFn = Callable[[StepDispatchContext], StepOutcome]
PublishFn = Callable[[StepSpec, StepOutcome], Mapping[str, str]]


# ---------------------------------------------------------------------------
# The only loop
# ---------------------------------------------------------------------------


class BoundedStepDagExecutor:
    """Runtime loop for one bounded DAG; compute and publish are injected."""

    def __init__(
        self,
        dag: BoundedStepDag,
        dispatch: DispatchFn,
        publish: PublishFn,
        *,
        plan_identity: str,
        release_id: str,
        budget_caps: BudgetCaps | None = None,
        state: DagRunState | None = None,
        recovery_allowance: int = 0,
    ) -> None:
        errors = dag.validate()
        if errors:
            raise StepDagError("invalid_dag", "; ".join(errors))
        self.dag = dag
        self._dispatch = dispatch
        self._publish = publish
        self.plan_identity = plan_identity
        self.release_id = release_id
        allowance = int(recovery_allowance)
        if allowance < 0:
            raise StepDagError("invalid_budget_caps", "recovery_allowance must be non-negative")
        # D09 (M1-04b): the pre-M1-04 default added one implicit allowance per
        # step (``+ len(dag.steps)``) for crash re-dispatch.  That silently
        # widened the QC/attempt budget, so the allowance is now explicit: a
        # caller that wants a recovery margin must authorize it here, and the
        # default derives the caps from the reservation only.
        self.recovery_allowance = allowance
        self.budget_caps = budget_caps or BudgetCaps(
            task_refs=1,
            steps=len(dag.steps),
            qc_invocations=sum(step.qc_invocations for step in dag.steps) + allowance,
            attempts=sum(step.max_attempts for step in dag.steps) + allowance,
            bsub=sum(1 for step in dag.steps if step.bsub),
        )
        self.state = state or self._initial_state()
        self._order = dag.topological_order()
        self._index = {step_id: index for index, step_id in enumerate(self._order)}
        self._outcomes: dict[str, StepOutcome] = {
            step_id: StepOutcome.from_dict(state.publish_payload)
            for step_id, state in self.state.states.items()
            if state.publish_payload
        }

    # -- state -----------------------------------------------------------

    def _initial_state(self) -> DagRunState:
        return DagRunState(
            task_ref=self.dag.task_ref,
            states={
                step.step_id: StepState(step_id=step.step_id, status=StepStatus.PENDING)
                for step in self.dag.steps
            },
        )

    def statuses(self) -> dict[str, str]:
        return self.state.statuses()

    def publish_statuses(self) -> dict[str, str]:
        return {step_id: value.publish_status.value for step_id, value in self.state.states.items()}

    # -- control ---------------------------------------------------------

    def apply_control(self, intent: ControlIntent) -> dict[str, str]:
        """Propagate a plan-level intent to every unfinished step.

        A cancel never creates a new step: everything not already completed is
        ``cancelled`` and no dispatch happens afterwards.
        """
        intent = ControlIntent(intent)
        if intent is ControlIntent.PAUSE:
            self.state.paused = True
        elif intent is ControlIntent.CONTINUE:
            self.state.paused = False
        elif intent is ControlIntent.CANCEL:
            self.state.cancelled = True
            self.state.paused = False
            for step in self.dag.steps:
                current = self.state.states[step.step_id]
                if current.status in _UNFINISHED:
                    current.status = StepStatus.CANCELLED
                    current.reason = "plan_cancelled"
        return self.statuses()

    # -- readiness -------------------------------------------------------

    def _readiness(self, step: StepSpec) -> tuple[bool, str]:
        for ref in step.consumes:
            producer_state = self.state.states[ref.from_step]
            if producer_state.status is StepStatus.CANCELLED:
                return False, f"upstream_cancelled:{ref.from_step}"
            if producer_state.status in {
                StepStatus.FAILED,
                StepStatus.BLOCKED,
                StepStatus.UPSTREAM_FAILED,
            }:
                return False, f"upstream_failed:{ref.from_step}"
            if producer_state.status is not StepStatus.COMPLETED:
                return False, ""
            if producer_state.publish_status is not PublishStatus.PUBLISHED:
                return False, f"upstream_unpublished:{ref.from_step}"
            observed = producer_state.output_digests.get(ref.product_kind, "")
            if not observed:
                return False, f"missing_requirement:{ref.from_step}.{ref.product_kind}"
            if ref.expected_digest and observed != ref.expected_digest:
                return False, f"digest_mismatch:{ref.from_step}.{ref.product_kind}"
        return True, ""

    def _block(self, step: StepSpec, reason: str) -> None:
        current = self.state.states[step.step_id]
        if reason.startswith("upstream_failed") or reason.startswith("upstream_cancelled"):
            current.status = StepStatus.UPSTREAM_FAILED
        else:
            current.status = StepStatus.BLOCKED
        current.reason = reason

    # -- budget ----------------------------------------------------------

    def _reserve_budget(self, step: StepSpec, *, first_attempt: bool) -> None:
        usage = self.state.budget
        checks = (
            ("steps", usage.steps + (1 if first_attempt else 0), self.budget_caps.steps),
            (
                "qc_invocations",
                usage.qc_invocations + step.qc_invocations,
                self.budget_caps.qc_invocations,
            ),
            ("attempts", usage.attempts + 1, self.budget_caps.attempts),
            ("bsub", usage.bsub + (1 if step.bsub else 0), self.budget_caps.bsub),
        )
        for field_name, requested, cap in checks:
            if requested > cap:
                raise BudgetExceededError(field_name, requested, cap)
        if first_attempt:
            usage.steps += 1
        usage.qc_invocations += step.qc_invocations
        usage.attempts += 1
        if step.bsub:
            usage.bsub += 1

    # -- dispatch --------------------------------------------------------

    def _dispatch_step(self, step: StepSpec) -> None:
        current = self.state.states[step.step_id]
        current.status = StepStatus.RUNNING
        first_attempt = current.attempts == 0
        try:
            self._reserve_budget(step, first_attempt=first_attempt)
        except BudgetExceededError as exc:
            current.status = StepStatus.BLOCKED
            current.reason = str(exc.code)
            return
        upstream: list[str] = []
        for ref in step.consumes:
            upstream.append(
                self.state.states[ref.from_step].output_digests.get(ref.product_kind, "")
            )
        identity = materialize_step_identity(
            step,
            step_index=self._index[step.step_id],
            plan_identity=self.plan_identity,
            upstream_digests=upstream,
            release_id=self.release_id,
        )
        current.upstream_digests = tuple(upstream)
        current.step_identity = identity.step_identity
        current.attempts += 1
        self.state.dispatch_count += 1
        context = StepDispatchContext(
            step=step,
            step_identity=identity,
            upstream_digests=tuple(upstream),
            task_ref=self.dag.task_ref,
            release_id=self.release_id,
        )
        try:
            outcome = self._dispatch(context)
        except Exception as exc:  # noqa: BLE001 - step boundary records the failure
            current.status = StepStatus.FAILED
            current.reason = f"{type(exc).__name__}: {exc}"
            return
        current.status = StepStatus.COMPLETED
        current.output_digests = dict(outcome.output_digests)
        current.receipt_digest = outcome.receipt_digest
        current.result_digest = outcome.result_digest
        current.publish_payload = outcome.to_dict()
        self._outcomes[step.step_id] = outcome
        self._publish_step(step, outcome)

    def _publish_step(self, step: StepSpec, outcome: StepOutcome) -> bool:
        current = self.state.states[step.step_id]
        self.state.publication_attempts += 1
        try:
            published = self._publish(step, outcome)
        except Exception as exc:  # noqa: BLE001 - publication failure is recorded, not recompute
            current.publish_status = PublishStatus.FAILED
            current.publication_reason = f"{type(exc).__name__}: {exc}"
            return False
        for product_kind, digest in outcome.output_digests.items():
            if published.get(product_kind, digest) != digest:
                current.publish_status = PublishStatus.FAILED
                current.publication_reason = "publication_digest_mismatch"
                return False
        current.publish_status = PublishStatus.PUBLISHED
        current.publication_reason = ""
        # A dependent that was waiting only for this publication may proceed.
        for dependent in self.dag.steps:
            if not any(ref.from_step == step.step_id for ref in dependent.consumes):
                continue
            dependent_state = self.state.states[dependent.step_id]
            if dependent_state.status is StepStatus.BLOCKED and dependent_state.reason.startswith(
                "upstream_unpublished"
            ):
                dependent_state.status = StepStatus.PENDING
                dependent_state.reason = ""
        return True

    def retry_publication(self, step_id: str) -> bool:
        """Retry only the publication of an already-computed step.

        Neither the dispatcher nor the budget is invoked: a publication failure
        never recomputes the science.
        """
        current = self.state.states[step_id]
        if current.status is not StepStatus.COMPLETED:
            raise StepDagError(
                "publication_not_retryable",
                f"{step_id}: only a completed step can retry publication",
            )
        if current.publish_status is PublishStatus.PUBLISHED:
            return True
        outcome = self._outcomes.get(step_id)
        if outcome is None:
            outcome = StepOutcome.from_dict(current.publish_payload)
            self._outcomes[step_id] = outcome
        step = self.dag.by_id()[step_id]
        return self._publish_step(step, outcome)

    # -- run -------------------------------------------------------------

    def run(self) -> DagRunState:
        """Run until no step can progress (bounded, deterministic)."""
        while not self.state.paused and not self.state.cancelled:
            progressed = False
            for step in self.dag.steps:
                if self.state.paused or self.state.cancelled:
                    break
                current = self.state.states[step.step_id]
                if current.status in TERMINAL_STEP_STATUSES:
                    continue
                if current.status is StepStatus.RUNNING:
                    # A recovered ``running`` step is never adopted; recovery
                    # resets it before the loop (see ``recover``).
                    current.status = StepStatus.PENDING
                ready, reason = self._readiness(step)
                if not ready:
                    if reason:
                        self._block(step, reason)
                        progressed = True
                    continue
                current.status = StepStatus.READY
                self._dispatch_step(step)
                progressed = True
            if not progressed:
                break
        return self.state

    # -- recovery --------------------------------------------------------

    @classmethod
    def recover(
        cls,
        dag: BoundedStepDag,
        dispatch: DispatchFn,
        publish: PublishFn,
        *,
        plan_identity: str,
        release_id: str,
        persisted: Mapping[str, Any],
        budget_caps: BudgetCaps | None = None,
        recovery_allowance: int | None = None,
    ) -> BoundedStepDagExecutor:
        """Rebuild from persisted state, resetting only unfinished steps.

        ``completed`` (published or publication-failed), ``failed``, ``blocked``
        and ``cancelled`` are preserved; ``running``/``ready``/``pending`` become
        ``pending`` again.  The caller then calls ``run()``, which re-dispatches
        only the unfinished steps (and retries publication separately).

        D09 (M1-04b): a restart needs a re-dispatch allowance for the steps that
        were actually interrupted.  The allowance is **derived from the persisted
        evidence** (one per previously ``running`` step) instead of being added
        implicitly for every step; a caller may pass an explicit
        ``recovery_allowance`` only when it has a stronger authorization.
        """
        state = DagRunState.from_dict(persisted)
        known = {step.step_id for step in dag.steps}
        for step_id in list(state.states):
            if step_id not in known:
                del state.states[step_id]
        for step in dag.steps:
            current = state.states.get(step.step_id)
            if current is None:
                state.states[step.step_id] = StepState(step_id=step.step_id)
                continue
            if current.status in _UNFINISHED:
                current.status = StepStatus.PENDING
                current.reason = ""
        allowance = recovery_allowance
        if allowance is None:
            allowance = sum(
                1
                for step in dag.steps
                if state.states[step.step_id].status == StepStatus.PENDING
                and _persisted_running(persisted, step.step_id)
            )
        return cls(
            dag,
            dispatch,
            publish,
            plan_identity=plan_identity,
            release_id=release_id,
            budget_caps=budget_caps,
            state=state,
            recovery_allowance=int(allowance),
        )


# ---------------------------------------------------------------------------
# CCCP compute adapter (compute only; never called by the B18 static proof)
# ---------------------------------------------------------------------------


def cccp_compute_dispatch(
    context: StepDispatchContext,
    request: Any,
    *,
    task_context: Any = None,
) -> StepOutcome:
    """Invoke exactly one existing ``cccp.calculation.run_*`` task core.

    This is the injected compute boundary.  It does not loop, schedule,
    publish or create a second executor; the Runtime loop owns those.
    """
    import cccp.calculation as calculation

    entrypoint = STEP_ENTRYPOINTS[context.step.kind].rsplit(".", 1)[-1]
    runner = getattr(calculation, entrypoint, None)
    if runner is None:
        raise StepDagError("unknown_implementation", f"missing {entrypoint}")
    result = runner(request, context=task_context)
    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    return StepOutcome(
        output_digests={
            "result": document_digest(payload),
        },
        result_digest=document_digest(payload),
        publish_payload={"result": payload},
    )
