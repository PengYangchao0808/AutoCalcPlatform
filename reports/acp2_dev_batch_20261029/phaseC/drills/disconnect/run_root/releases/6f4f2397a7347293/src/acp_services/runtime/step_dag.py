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

* ``plan_identity`` (what was approved, ``sha256:`` domain);
* the **step definition identities** frozen with the plan and carried on the
  envelope (wire name ``step_identities``, see ``step_definition``);
* the **execution identity** materialized at dispatch by CCCP
  (:func:`cccp.calculation.recipes.compile.materialize_step_identity`) from the
  frozen definition + the *actual* upstream digests.

M2-04b deleted the second fingerprint algorithm this module used to keep: the
Runtime never derives its own step identity, it delegates to CCCP.  A DAG
without a frozen definition is a *fixture* DAG (``static-fixture.v0``): it can
be replayed for static proofs but never claims a materialized execution
identity and is never an adoptable production run.

Publication state is separate from computation state so a publication failure
never re-runs compute.  Step products are **declared** (product kind + real
artifact path + content digest) and separately from the opaque
``publish_payload``, so a ``result``-shaped digest cannot masquerade as a
published ``energy`` product.

Author: ACP Services (B lane, B18b).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from acp_services.runtime.content_digest import document_digest
from cccp.calculation.requests import TaskKind

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from acp_services.runtime.step_definition import StepDagDefinition

__all__ = [
    "COMPUTE_OWNER",
    "CONTROL_NOT_CANCELLED",
    "DEFAULT_LIMITS",
    "EXECUTION_LOOP_OWNER",
    "PUBLISH_POLICY_NONE",
    "PUBLISH_POLICY_NORMAL",
    "PUBLISH_POLICY_PARTIAL",
    "QUALIFICATION_COMPLETE",
    "QUALIFICATION_DIAGNOSTIC",
    "QUALIFICATION_INCOMPLETE",
    "QUALIFICATION_PARTIAL",
    "QUALIFICATION_TASK_FAILED",
    "STEP_ENTRYPOINTS",
    "STEP_TASK_KINDS",
    "BoundedStepDag",
    "BoundedStepDagExecutor",
    "BudgetCaps",
    "BudgetUsage",
    "ControlIntent",
    "ControlPoll",
    "DagRunState",
    "DispatchFn",
    "Limits",
    "OutputRef",
    "PublishFn",
    "PublishStatus",
    "ScienceStepKind",
    "StepDagError",
    "StepDispatchContext",
    "StepIdentity",
    "StepOutcome",
    "StepProduct",
    "StepQuotaPort",
    "StepSpec",
    "StepState",
    "StepStatus",
    "cccp_compute_dispatch",
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
    """One stable step of one platform task.

    ``output_dir_name`` is the **explicit** per-step output directory (relative
    to the attempt work directory).  The runtime always passes an absolute
    ``output_dir`` to the adapter; a runner never guesses one.  ``input_roles``
    declares the additional non-structure inputs by role, so the adapter maps
    roles to paths from the frozen definition instead of inferring them.
    """

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
    worst_case_executions: int = 1
    output_dir_name: str = ""
    input_roles: Mapping[str, str] = field(default_factory=dict)
    product_artifact_types: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScienceStepKind(self.kind))
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(self, "consumes", tuple(self.consumes))
        object.__setattr__(self, "produces", tuple(self.produces))
        object.__setattr__(self, "input_roles", dict(self.input_roles))
        object.__setattr__(
            self,
            "product_artifact_types",
            {
                str(kind): tuple(str(item) for item in types)
                for kind, types in dict(self.product_artifact_types).items()
            },
        )
        if not self.output_dir_name:
            object.__setattr__(self, "output_dir_name", self.step_id)
        if self.worst_case_executions < 0:
            raise StepDagError(
                "invalid_worst_case",
                f"{self.step_id}: worst_case_executions must be >= 0",
            )
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
        if not self.structure_role:
            raise StepDagError(
                "missing_structure_role",
                f"{self.step_id}: a structure role is required (never guessed)",
            )
        if any(not role or not path for role, path in self.input_roles.items()):
            raise StepDagError(
                "invalid_input_role",
                f"{self.step_id}: input roles require a non-empty role and path",
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen (definition-bearing) payload of this step."""
        return {
            "step_id": self.step_id,
            "kind": self.kind.value,
            "intent": self.intent,
            "required": self.required,
            "diagnostic_only": self.diagnostic_only,
            "parameters": dict(self.parameters),
            "consumes": [
                {
                    "from_step": ref.from_step,
                    "product_kind": ref.product_kind,
                    "expected_digest": ref.expected_digest,
                    "required": ref.required,
                }
                for ref in self.consumes
            ],
            "produces": [
                {
                    "from_step": ref.from_step,
                    "product_kind": ref.product_kind,
                    "expected_digest": ref.expected_digest,
                    "required": ref.required,
                }
                for ref in self.produces
            ],
            "structure_role": self.structure_role,
            "implementation_version": self.implementation_version,
            "capability": self.capability,
            "max_attempts": self.max_attempts,
            "qc_invocations": self.qc_invocations,
            "bsub": self.bsub,
            "worst_case_executions": self.worst_case_executions,
            "output_dir_name": self.output_dir_name,
            "input_roles": dict(self.input_roles),
            "product_artifact_types": {
                kind: list(types) for kind, types in self.product_artifact_types.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepSpec:
        """Rebuild a step from its frozen payload (worker-side deserialization)."""

        def _refs(key: str) -> tuple[OutputRef, ...]:
            raw = payload.get(key) or ()
            return tuple(
                OutputRef(
                    from_step=str(item.get("from_step") or ""),
                    product_kind=str(item.get("product_kind") or ""),
                    expected_digest=str(item.get("expected_digest") or ""),
                    required=bool(item.get("required", True)),
                )
                for item in raw
                if isinstance(item, Mapping)
            )

        return cls(
            step_id=str(payload.get("step_id") or ""),
            kind=ScienceStepKind(str(payload.get("kind") or "")),
            intent=str(payload.get("intent") or ""),
            required=bool(payload.get("required", True)),
            diagnostic_only=bool(payload.get("diagnostic_only", False)),
            parameters=dict(payload.get("parameters") or {}),
            consumes=_refs("consumes"),
            produces=_refs("produces"),
            structure_role=str(payload.get("structure_role") or "minimum"),
            implementation_version=str(payload.get("implementation_version") or ""),
            capability=str(payload.get("capability") or ""),
            max_attempts=int(payload.get("max_attempts") or 1),
            qc_invocations=int(payload.get("qc_invocations") or 0),
            bsub=bool(payload.get("bsub", False)),
            worst_case_executions=int(payload.get("worst_case_executions") or 1),
            output_dir_name=str(payload.get("output_dir_name") or ""),
            input_roles=dict(payload.get("input_roles") or {}),
            product_artifact_types={
                str(kind): tuple(str(item) for item in types)
                for kind, types in dict(payload.get("product_artifact_types") or {}).items()
            },
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
    """One platform task's bounded acyclic step graph.

    ``definition`` is the frozen ``StepDagDefinition`` produced at freeze time.
    When it is ``None`` the DAG is a *fixture* graph: replayable for static
    proofs, never an adoptable production run (see ``identity_scheme``).
    """

    recipe_id: str
    task_ref: str
    steps: tuple[StepSpec, ...]
    limits: Limits = DEFAULT_LIMITS
    definition: Any | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.recipe_id:
            raise StepDagError("invalid_recipe", "recipe_id is required")
        if not self.task_ref:
            raise StepDagError("invalid_task_ref", "one non-empty TaskRef is required")

    @property
    def has_definition(self) -> bool:
        """True only for a freeze-produced (production-capable) DAG."""
        return self.definition is not None

    @property
    def plan_definition_digest(self) -> str:
        """The frozen ``v2:`` plan-definition digest (``""`` for fixtures)."""
        return str(getattr(self.definition, "plan_definition_digest", "") or "")

    def require_definition(self) -> StepDagDefinition:
        """Return the frozen definition or refuse the production dispatch."""
        definition = self.definition
        if definition is None:
            raise StepDagError(
                "missing_definition",
                f"{self.recipe_id}: a frozen step-DAG definition is required for a "
                "production dispatch (a fixture DAG carries no execution identity)",
            )
        return definition  # type: ignore[no-any-return]

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
                    **step.to_dict(),
                    # Derived execution view (documentation/acceptance): the
                    # frozen payload below is the dispatch authority.
                    "task_kind": STEP_TASK_KINDS[step.kind].value,
                    "entrypoint": STEP_ENTRYPOINTS[step.kind],
                }
                for step in self.steps
            ],
        }

    def to_frozen_payload(self) -> dict[str, Any]:
        """Return the frozen plan member that the worker only deserializes."""
        definition = self.definition
        if definition is None:
            raise StepDagError(
                "missing_definition",
                f"{self.recipe_id}: cannot freeze a DAG without a definition",
            )
        return {
            "schema": "acp2.runtime.bounded-step-dag.v1",
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
            "steps": [step.to_dict() for step in self.steps],
            "definition": definition.to_dict(),
        }


# ---------------------------------------------------------------------------
# Step identity (plan identity < definition identities < execution identity)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepIdentity:
    """The **execution** identity materialized immediately before a dispatch.

    ``step_identity`` is the CCCP ``v2:`` fingerprint of
    ``cccp.calculation.recipes.compile.materialize_step_identity`` — there is no
    Runtime-local hashing scheme.  For a fixture DAG (no frozen definition)
    ``step_identity`` is empty and ``identity_scheme`` says so; an empty
    identity is never adopted as production science.
    """

    step_id: str
    step_index: int
    plan_identity: str
    implementation_version: str
    release_id: str
    upstream_digests: tuple[str, ...]
    materialized_parameters_digest: str
    step_identity: str
    identity_scheme: str = ""

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
            "identity_scheme": self.identity_scheme,
        }


def materialize_execution_identity(
    dag: BoundedStepDag,
    step: StepSpec,
    *,
    step_index: int,
    plan_identity: str,
    upstream_digests: Mapping[str, str],
    release_id: str,
) -> StepIdentity:
    """Freeze the execution identity through the CCCP authority (M2-04b).

    ``upstream_digests`` is addressed by ``output_ref_address`` (the CCCP
    addressing scheme), so the identity binds the *actual* upstream products
    this step consumes.  A missing release or plan identity is a precondition
    failure, never an identity with empty fields; a fixture DAG yields an
    explicitly-schemed empty identity instead of inventing a digest.
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
    ordered = tuple(str(upstream_digests[key]) for key in sorted(upstream_digests))
    definition = dag.definition
    if definition is None:
        return StepIdentity(
            step_id=step.step_id,
            step_index=int(step_index),
            plan_identity=plan_identity,
            implementation_version=step.implementation_version,
            release_id=release_id,
            upstream_digests=ordered,
            materialized_parameters_digest=document_digest(dict(step.parameters)),
            step_identity="",
            identity_scheme="static-fixture.v0",
        )
    try:
        materialized = definition.execution_identity(step.step_id, dict(upstream_digests))
    except StepDagError:
        raise
    except Exception as exc:  # noqa: BLE001 - CCCP typed errors are the authority
        raise StepDagError(
            "invalid_step_identity", f"{step.step_id}: {type(exc).__name__}: {exc}"
        ) from exc
    return StepIdentity(
        step_id=step.step_id,
        step_index=int(step_index),
        plan_identity=plan_identity,
        implementation_version=step.implementation_version,
        release_id=release_id,
        upstream_digests=ordered,
        materialized_parameters_digest=document_digest(dict(step.parameters)),
        step_identity=str(materialized.execution_identity),
        identity_scheme=str(getattr(definition, "identity_scheme", "") or ""),
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

#: Completion qualification carried per step (mirrors the D01 decision tokens
#: without duplicating the truth table: the value always comes from
#: ``acp_services.runtime.worker.decide_task_result``).
QUALIFICATION_COMPLETE: str = "complete"
QUALIFICATION_PARTIAL: str = "partial_result"
QUALIFICATION_INCOMPLETE: str = "incomplete_result"
QUALIFICATION_TASK_FAILED: str = "task_failed"
#: ``StepSpec.diagnostic_only`` steps are recorded as diagnostics, never as a
#: normal completion, even when the task core reports ``completed``.
QUALIFICATION_DIAGNOSTIC: str = "diagnostic_only"

#: Publication policy tokens (from the same decision).
PUBLISH_POLICY_NORMAL: str = "normal"
PUBLISH_POLICY_PARTIAL: str = "partial"
PUBLISH_POLICY_NONE: str = "none"

#: Control-intent tokens the per-dispatch poll may return.
CONTROL_NOT_CANCELLED: str = "continue"


@dataclass(frozen=True, slots=True)
class StepProduct:
    """One **declared** step product: product kind + real artifact + digest.

    Declaration is separate from publication (``publish_payload``): a product
    must resolve to an existing file whose content digest equals the declared
    digest, so an ``energy`` product cannot be satisfied by the ``result``
    JSON digest of the whole task outcome.
    """

    product_kind: str
    path: str
    digest: str
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.product_kind:
            raise StepDagError("invalid_product", "product_kind is required")
        if not self.path:
            raise StepDagError("invalid_product", "path is required")
        if not self.digest:
            raise StepDagError("invalid_product", "digest is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_kind": self.product_kind,
            "path": self.path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepProduct:
        return cls(
            product_kind=str(payload.get("product_kind") or ""),
            path=str(payload.get("path") or ""),
            digest=str(payload.get("digest") or ""),
            size_bytes=int(payload.get("size_bytes") or 0),
        )


@dataclass(slots=True)
class StepOutcome:
    """What the compute adapter returns: qualification + products + payload.

    ``qualification``/``publish_policy`` come from
    :func:`acp_services.runtime.worker.decide_task_result`; the runtime never
    re-derives them.  ``products`` are the declared artifacts,
    ``publish_payload`` the opaque publication document; both are validated
    separately.
    """

    output_digests: dict[str, str] = field(default_factory=dict)
    receipt_digest: str = ""
    result_digest: str = ""
    publish_payload: dict[str, Any] = field(default_factory=dict)
    qualification: str = QUALIFICATION_COMPLETE
    publish_policy: str = PUBLISH_POLICY_NORMAL
    products: tuple[StepProduct, ...] = ()
    execution_identity: str = ""
    identity_scheme: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_digests": dict(self.output_digests),
            "receipt_digest": self.receipt_digest,
            "result_digest": self.result_digest,
            "publish_payload": dict(self.publish_payload),
            "qualification": self.qualification,
            "publish_policy": self.publish_policy,
            "products": [product.to_dict() for product in self.products],
            "execution_identity": self.execution_identity,
            "identity_scheme": self.identity_scheme,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepOutcome:
        outputs = payload.get("output_digests")
        publish_payload = payload.get("publish_payload")
        products = payload.get("products") or ()
        return cls(
            output_digests={
                str(key): str(value)
                for key, value in (outputs or {}).items()
                if isinstance(key, str) and isinstance(value, str)
            },
            receipt_digest=str(payload.get("receipt_digest") or ""),
            result_digest=str(payload.get("result_digest") or ""),
            publish_payload=dict(publish_payload) if isinstance(publish_payload, Mapping) else {},
            qualification=str(payload.get("qualification") or QUALIFICATION_COMPLETE),
            publish_policy=str(payload.get("publish_policy") or PUBLISH_POLICY_NORMAL),
            products=tuple(
                StepProduct.from_dict(item) for item in products if isinstance(item, Mapping)
            ),
            execution_identity=str(payload.get("execution_identity") or ""),
            identity_scheme=str(payload.get("identity_scheme") or ""),
            failure_reason=str(payload.get("failure_reason") or ""),
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
    qualification: str = ""
    publish_policy: str = ""
    products: tuple[StepProduct, ...] = ()
    identity_scheme: str = ""

    @property
    def releases_downstream(self) -> bool:
        """Whether this step may release its *required* downstream consumers.

        Only a normal completion releases a required consumer: a diagnostic
        step, a partial result or an incomplete/failed status never does.
        """
        return (
            self.status is StepStatus.COMPLETED
            and self.publish_status is PublishStatus.PUBLISHED
            and self.qualification == QUALIFICATION_COMPLETE
            and self.publish_policy == PUBLISH_POLICY_NORMAL
        )

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
            "qualification": self.qualification,
            "publish_policy": self.publish_policy,
            "products": [product.to_dict() for product in self.products],
            "identity_scheme": self.identity_scheme,
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
        products = payload.get("products") or ()
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
            qualification=str(payload.get("qualification") or ""),
            publish_policy=str(payload.get("publish_policy") or ""),
            products=tuple(
                StepProduct.from_dict(item) for item in products if isinstance(item, Mapping)
            ),
            identity_scheme=str(payload.get("identity_scheme") or ""),
        )


#: Current run-state wire schema.  ``identity_scheme`` was added in M2-04b; an
#: older document without it is **refused** for adoption (never guessed).
DAG_RUN_STATE_SCHEMA: str = "acp2.runtime.step-dag-state.v2"
DAG_RUN_STATE_SCHEMA_V1: str = "acp2.runtime.step-dag-state.v1"


@dataclass(slots=True)
class DagRunState:
    """Persistent run state for one platform task (no second task per step).

    Carries the execution facts the worker owns — ``attempt``/``owner``/
    ``fencing``/``schema_version``/``identity_scheme`` — so a recovered worker
    never has to read the jobs DB to decide whether the state is still its own.
    """

    task_ref: str
    states: dict[str, StepState] = field(default_factory=dict)
    paused: bool = False
    cancelled: bool = False
    budget: BudgetUsage = field(default_factory=BudgetUsage)
    dispatch_count: int = 0
    publication_attempts: int = 0
    schema_version: str = DAG_RUN_STATE_SCHEMA
    identity_scheme: str = ""
    attempt: int = 0
    owner: str = ""
    fencing: str = ""
    plan_identity: str = ""
    plan_definition_digest: str = ""
    frozen_plan_body_digest: str = ""
    input_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DAG_RUN_STATE_SCHEMA,
            "task_ref": self.task_ref,
            "paused": self.paused,
            "cancelled": self.cancelled,
            "dispatch_count": self.dispatch_count,
            "publication_attempts": self.publication_attempts,
            "budget": self.budget.to_dict(),
            "identity_scheme": self.identity_scheme,
            "attempt": self.attempt,
            "owner": self.owner,
            "fencing": self.fencing,
            "plan_identity": self.plan_identity,
            "plan_definition_digest": self.plan_definition_digest,
            "frozen_plan_body_digest": self.frozen_plan_body_digest,
            "input_digest": self.input_digest,
            "states": [self.states[step_id].to_dict() for step_id in sorted(self.states)],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, adopt: bool = False) -> DagRunState:
        """Decode persisted state.

        With ``adopt=True`` an unknown/missing ``identity_scheme`` or an older
        schema is a typed refusal (:class:`StepDagError`): adoption never
        guesses an identity scheme, and a v1 document must be migrated by an
        explicit decision (see ``tests/baseline/recovery_fixtures``).
        """
        schema = str(payload.get("schema") or DAG_RUN_STATE_SCHEMA_V1)
        identity_scheme = str(payload.get("identity_scheme") or "")
        if adopt:
            if schema != DAG_RUN_STATE_SCHEMA:
                raise StepDagError(
                    "unknown_run_state_schema",
                    f"refusing to adopt run state schema {schema!r}; "
                    f"expected {DAG_RUN_STATE_SCHEMA}",
                )
            if not identity_scheme:
                raise StepDagError(
                    "unknown_identity_scheme",
                    "refusing to adopt run state without an identity_scheme",
                )
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
            schema_version=DAG_RUN_STATE_SCHEMA,
            identity_scheme=identity_scheme,
            attempt=int(payload.get("attempt") or 0),
            owner=str(payload.get("owner") or ""),
            fencing=str(payload.get("fencing") or ""),
            plan_identity=str(payload.get("plan_identity") or ""),
            plan_definition_digest=str(payload.get("plan_definition_digest") or ""),
            frozen_plan_body_digest=str(payload.get("frozen_plan_body_digest") or ""),
            input_digest=str(payload.get("input_digest") or ""),
        )

    @classmethod
    def migrate_v1(
        cls,
        payload: Mapping[str, Any],
        *,
        identity_scheme: str,
        plan_identity: str = "",
        plan_definition_digest: str = "",
        input_digest: str = "",
        owner: str = "",
        fencing: str = "",
        attempt: int = 0,
    ) -> DagRunState:
        """Explicitly migrate a pre-M2-04 (v1) run state to the v2 schema.

        The migration is a **decision**, never a default: the caller must state
        which identity scheme the run will now use, and a scheme other than the
        production CCCP scheme or the explicit fixture scheme is refused.  Step
        statuses/qualifications are preserved verbatim — nothing is recomputed
        and no execution identity is invented; a completed step keeps an empty
        identity and is therefore re-dispatched by the caller's own decision.
        """
        migrated = cls.from_dict({**dict(payload), "schema": DAG_RUN_STATE_SCHEMA_V1})
        if identity_scheme not in {IDENTITY_SCHEME_PRODUCTION, IDENTITY_SCHEME_FIXTURE_FOR_RUN}:
            raise StepDagError(
                "unknown_identity_scheme",
                f"migration must name a known identity scheme, got {identity_scheme!r}",
            )
        migrated.schema_version = DAG_RUN_STATE_SCHEMA
        migrated.identity_scheme = identity_scheme
        migrated.plan_identity = plan_identity or migrated.plan_identity
        migrated.plan_definition_digest = plan_definition_digest or migrated.plan_definition_digest
        migrated.input_digest = input_digest or migrated.input_digest
        migrated.owner = owner or migrated.owner
        migrated.fencing = fencing or migrated.fencing
        migrated.attempt = attempt or migrated.attempt
        return migrated

    def statuses(self) -> dict[str, str]:
        return {step_id: state.status.value for step_id, state in self.states.items()}


@dataclass(frozen=True, slots=True)
class UpstreamArtifact:
    """One verified upstream product handed to the compute layer as a path."""

    from_step: str
    product_kind: str
    digest: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_step": self.from_step,
            "product_kind": self.product_kind,
            "digest": self.digest,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class StepDispatchContext:
    """Everything the compute layer may see for one step (no control authority).

    M2-04a: the context carries the *runtime* facts a typed
    ``TaskRequest``/``TaskContext`` needs — work dir, input base, attempt,
    per-step output dir, the actual upstream artifact paths (not only digests),
    the resolved method/resource/software configuration, the structure role and
    every additional input role, and the capability + provider version.  The
    compute adapter therefore never guesses an input role, a default config or
    an output location, and it never receives the plan ``dict``.
    """

    step: StepSpec
    step_identity: StepIdentity
    upstream_digests: tuple[str, ...]
    task_ref: str
    release_id: str
    work_dir: str = ""
    input_base: str = ""
    output_dir: str = ""
    attempt: int = 0
    owner: str = ""
    fencing: str = ""
    upstream_artifacts: tuple[UpstreamArtifact, ...] = ()
    structure_role: str = "minimum"
    input_roles: Mapping[str, str] = field(default_factory=dict)
    method: Mapping[str, Any] = field(default_factory=dict)
    resources: Mapping[str, Any] = field(default_factory=dict)
    software: Mapping[str, Any] = field(default_factory=dict)
    capability: str = ""
    provider_version: str = ""
    plan_identity: str = ""
    plan_definition_digest: str = ""
    structure_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_ref": self.task_ref,
            "release_id": self.release_id,
            "work_dir": self.work_dir,
            "input_base": self.input_base,
            "output_dir": self.output_dir,
            "attempt": self.attempt,
            "owner": self.owner,
            "fencing": self.fencing,
            "upstream_digests": list(self.upstream_digests),
            "upstream_artifacts": [item.to_dict() for item in self.upstream_artifacts],
            "structure_role": self.structure_role,
            "structure_path": self.structure_path,
            "input_roles": dict(self.input_roles),
            "method": dict(self.method),
            "resources": dict(self.resources),
            "software": dict(self.software),
            "capability": self.capability,
            "provider_version": self.provider_version,
            "plan_identity": self.plan_identity,
            "plan_definition_digest": self.plan_definition_digest,
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


def _output_ref_address(from_step: str, product_kind: str) -> str:
    """CCCP dependency address of one whole-product upstream reference."""
    from acp_services.runtime.step_definition import output_ref_address

    return output_ref_address(from_step, product_kind)


#: The one compute seam: a frozen step + verified upstream artifacts in, a
#: declared outcome out.  ``StepDispatchAdapter`` is the only production
#: implementation (it is what makes this callable a single-argument
#: ``DispatchFn``); a plan ``dict`` is never passed to a runner.
DispatchFn = Callable[[StepDispatchContext], StepOutcome]
PublishFn = Callable[[StepSpec, StepOutcome], Mapping[str, str]]
#: Re-polled immediately before every step dispatch (M2-04c).  Returning
#: ``"cancel"`` stops the loop; anything else continues.
ControlPoll = Callable[[], str]


@runtime_checkable
class StepQuotaPort(Protocol):
    """The execution-allowance seam (reservation is minted by the Controller).

    ``reserve`` is called **once** for the whole attempt from the persistent
    reservation; ``take`` is called immediately before each backend call and
    must be a persistent deduction; ``settle`` records the actual consumption
    against the receipt.  The executor can never mint allowance.
    """

    def reserve(
        self,
        *,
        task_ref: str,
        attempt: int,
        plan_definition_digest: str,
        worst_case: Mapping[str, int],
    ) -> None:
        """Bind this attempt to its persistent reservation (no minting)."""
        ...

    def take(self, unit: str, units: int = 1, *, detail: str = "") -> None:
        """Deduct *units* or raise (never silently over-spend)."""
        ...

    def settle(self, *, task_ref: str, attempt: int, consumed: Mapping[str, int]) -> None:
        """Settle the receipt's actual consumption against the reservation."""
        ...


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
        quota: StepQuotaPort | None = None,
        control: ControlPoll | None = None,
        attempt: int = 0,
        owner: str = "",
        fencing: str = "",
        input_digest: str = "",
        structure_path: str = "",
        work_dir: str = "",
        input_roles: Mapping[str, str] | None = None,
        method: Mapping[str, Any] | None = None,
        resources: Mapping[str, Any] | None = None,
        software: Mapping[str, Any] | None = None,
        require_definition: bool = False,
    ) -> None:
        errors = dag.validate()
        if errors:
            raise StepDagError("invalid_dag", "; ".join(errors))
        if require_definition:
            dag.require_definition()
        self.dag = dag
        self._dispatch = dispatch
        self._publish = publish
        self._quota = quota
        self._control = control
        self.plan_identity = plan_identity
        self.release_id = release_id
        self.attempt = int(attempt)
        self.owner = owner
        self.fencing = fencing
        self.input_digest = input_digest
        self.structure_path = structure_path
        self.work_dir = str(work_dir or "")
        self.input_roles: dict[str, str] = dict(input_roles or {})
        self.method: dict[str, Any] = dict(method or {})
        self.resources: dict[str, Any] = dict(resources or {})
        self.software: dict[str, Any] = dict(software or {})
        allowance = int(recovery_allowance)
        if allowance < 0:
            raise StepDagError("invalid_budget_caps", "recovery_allowance must be non-negative")
        # D09 (M1-04b) + M2-04c: no per-step implicit allowance.  The default
        # caps come from the *reservation* (the compiled worst case), never from
        # ``len(steps)``; a recovery margin must be authorized explicitly.
        self.recovery_allowance = allowance
        worst_case_total = sum(
            int(getattr(step, "worst_case_executions", 1) or 0) for step in dag.steps
        )
        self.budget_caps = budget_caps or BudgetCaps(
            task_refs=1,
            steps=len(dag.steps),
            qc_invocations=worst_case_total + allowance,
            attempts=sum(step.max_attempts for step in dag.steps) + allowance,
            bsub=sum(1 for step in dag.steps if step.bsub),
        )
        self.state = state or self._initial_state()
        if not self.state.task_ref:
            self.state.task_ref = dag.task_ref
        self.state.identity_scheme = (
            dag.plan_definition_digest
            and str(getattr(dag.definition, "identity_scheme", "") or "")
            or (IDENTITY_SCHEME_FIXTURE_FOR_RUN)
        )
        self.state.attempt = self.state.attempt or self.attempt
        self.state.owner = self.state.owner or self.owner
        self.state.fencing = self.state.fencing or self.fencing
        self.state.plan_identity = self.state.plan_identity or self.plan_identity
        self.state.plan_definition_digest = (
            self.state.plan_definition_digest or dag.plan_definition_digest
        )
        self.state.input_digest = self.state.input_digest or self.input_digest
        if self._quota is not None:
            self._quota.reserve(
                task_ref=dag.task_ref,
                attempt=self.state.attempt,
                plan_definition_digest=dag.plan_definition_digest,
                worst_case={
                    step.step_id: int(getattr(step, "worst_case_executions", 1) or 0)
                    for step in dag.steps
                },
            )
        self._order = dag.topological_order()
        self._index = {step_id: index for index, step_id in enumerate(self._order)}
        self._outcomes: dict[str, StepOutcome] = {
            step_id: StepOutcome.from_dict(state.publish_payload)
            for step_id, state in self.state.states.items()
            if state.publish_payload
        }

    # -- state -----------------------------------------------------------

    def _initial_state(self) -> DagRunState:
        definition = self.dag.definition
        return DagRunState(
            task_ref=self.dag.task_ref,
            states={
                step.step_id: StepState(step_id=step.step_id, status=StepStatus.PENDING)
                for step in self.dag.steps
            },
            identity_scheme=str(getattr(definition, "identity_scheme", "") or "")
            if definition is not None
            else IDENTITY_SCHEME_FIXTURE_FOR_RUN,
            attempt=self.attempt,
            owner=self.owner,
            fencing=self.fencing,
            plan_identity=self.plan_identity,
            plan_definition_digest=self.dag.plan_definition_digest,
            frozen_plan_body_digest=str(getattr(definition, "frozen_plan_body_digest", "") or ""),
            input_digest=self.input_digest,
        )

    def statuses(self) -> dict[str, str]:
        return self.state.statuses()

    def publish_statuses(self) -> dict[str, str]:
        return {step_id: value.publish_status.value for step_id, value in self.state.states.items()}

    def execution_chain(self) -> list[dict[str, Any]]:
        """Return the per-step execution identities in topological order.

        This is the additive receipt chain (M2-04b): the definition identity set
        is verified separately by the Controller, so ``execution_chain`` only
        carries what actually ran.
        """
        rows: list[dict[str, Any]] = []
        for step_id in self._order:
            state = self.state.states[step_id]
            if not state.step_identity:
                continue
            rows.append(
                {
                    "step_id": step_id,
                    "step_index": self._index[step_id],
                    "step_identity": state.step_identity,
                    "identity_scheme": state.identity_scheme,
                    "upstream_digests": list(state.upstream_digests),
                    "qualification": state.qualification,
                    "publish_policy": state.publish_policy,
                    "status": state.status.value,
                }
            )
        return rows

    # -- control ---------------------------------------------------------

    def _poll_control(self) -> bool:
        """Re-poll the control intent immediately before a dispatch.

        Returns ``True`` when the loop may continue.  A polled cancel never
        creates a new step; it is applied exactly like :meth:`apply_control`.
        """
        if self._control is None:
            return True
        intent = self._control()
        if str(intent or "").lower() in {ControlIntent.CANCEL.value, "cancel"}:
            self.apply_control(ControlIntent.CANCEL)
            return False
        if str(intent or "").lower() == ControlIntent.PAUSE.value:
            self.apply_control(ControlIntent.PAUSE)
            return False
        return True

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
            # M2-04a: a diagnostic-only or partial upstream never releases a
            # normal required consumer, even though the task core said
            # ``completed``.
            if ref.required and not producer_state.releases_downstream:
                if producer_state.qualification == QUALIFICATION_DIAGNOSTIC:
                    return False, f"upstream_diagnostic:{ref.from_step}"
                if producer_state.qualification == QUALIFICATION_PARTIAL:
                    return False, f"upstream_partial:{ref.from_step}"
                if producer_state.publish_policy in (PUBLISH_POLICY_NONE, PUBLISH_POLICY_PARTIAL):
                    return False, f"upstream_unpublishable:{ref.from_step}"
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
        worst_case = int(getattr(step, "worst_case_executions", 1) or 0)
        checks = (
            ("steps", usage.steps + (1 if first_attempt else 0), self.budget_caps.steps),
            (
                "qc_invocations",
                usage.qc_invocations + worst_case,
                self.budget_caps.qc_invocations,
            ),
            ("attempts", usage.attempts + 1, self.budget_caps.attempts),
            ("bsub", usage.bsub + (1 if step.bsub else 0), self.budget_caps.bsub),
        )
        for field_name, requested, cap in checks:
            if requested > cap:
                raise BudgetExceededError(field_name, requested, cap)
        if self._quota is not None:
            # One reservation, drawn per backend call: the executor itself takes
            # no units — the CCCP task core draws them through the meter.
            remaining = getattr(self._quota, "remaining", None)
            if callable(remaining) and remaining("qc_invocation") < worst_case:
                raise BudgetExceededError(
                    "qc_invocations",
                    usage.qc_invocations + worst_case,
                    self.budget_caps.qc_invocations,
                )
        if first_attempt:
            usage.steps += 1
        usage.qc_invocations += worst_case
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
        upstream_digests: dict[str, str] = {}
        upstream_artifacts: list[UpstreamArtifact] = []
        for ref in step.consumes:
            producer_state = self.state.states[ref.from_step]
            digest = producer_state.output_digests.get(ref.product_kind, "")
            upstream_digests[_output_ref_address(ref.from_step, ref.product_kind)] = digest
            artifact_path = ""
            for product in producer_state.products:
                if product.product_kind == ref.product_kind:
                    artifact_path = product.path
                    break
            upstream_artifacts.append(
                UpstreamArtifact(
                    from_step=ref.from_step,
                    product_kind=ref.product_kind,
                    digest=digest,
                    path=artifact_path,
                )
            )
        identity = materialize_execution_identity(
            self.dag,
            step,
            step_index=self._index[step.step_id],
            plan_identity=self.plan_identity,
            upstream_digests=upstream_digests,
            release_id=self.release_id,
        )
        current.upstream_digests = tuple(identity.upstream_digests)
        current.step_identity = identity.step_identity
        current.identity_scheme = identity.identity_scheme
        current.attempts += 1
        self.state.dispatch_count += 1
        work_dir = self.work_dir
        output_dir = str(Path(work_dir) / step.output_dir_name) if work_dir else ""
        context = StepDispatchContext(
            step=step,
            step_identity=identity,
            upstream_digests=tuple(identity.upstream_digests),
            task_ref=self.dag.task_ref,
            release_id=self.release_id,
            work_dir=work_dir,
            input_base=work_dir,
            output_dir=output_dir,
            attempt=self.state.attempt,
            owner=self.owner,
            fencing=self.fencing,
            upstream_artifacts=tuple(upstream_artifacts),
            structure_role=step.structure_role,
            input_roles=dict(step.input_roles) or dict(self.input_roles),
            method=dict(self.method),
            resources=dict(self.resources),
            software=dict(self.software),
            capability=step.capability,
            provider_version=step.implementation_version,
            plan_identity=self.plan_identity,
            plan_definition_digest=self.dag.plan_definition_digest,
            structure_path=self.structure_path,
        )
        try:
            outcome = self._dispatch(context)
        except Exception as exc:  # noqa: BLE001 - step boundary records the failure
            current.status = StepStatus.FAILED
            current.reason = f"{type(exc).__name__}: {exc}"
            current.qualification = QUALIFICATION_INCOMPLETE
            current.publish_policy = PUBLISH_POLICY_NONE
            return
        self._adopt_outcome(step, outcome, current)

    def _adopt_outcome(self, step: StepSpec, outcome: StepOutcome, current: StepState) -> None:
        """Adopt a compute outcome only when it is a completion fact.

        Rules (M2-04a): ``failed``/``cancelled``/``blocked``, a declared
        incomplete result, a missing required fact or an unknown status never
        become a normal ``completed``; a diagnostic-only step is recorded as a
        diagnostic even when the task core completed; declared products must
        resolve to real artifacts with the declared digests.
        """
        qualification = str(outcome.qualification or "")
        publish_policy = str(outcome.publish_policy or "")
        current.qualification = qualification
        current.publish_policy = publish_policy
        if qualification == QUALIFICATION_TASK_FAILED or qualification not in {
            QUALIFICATION_COMPLETE,
            QUALIFICATION_PARTIAL,
            QUALIFICATION_DIAGNOSTIC,
        }:
            current.status = StepStatus.FAILED
            current.reason = outcome.failure_reason or "result_not_complete"
            return
        if qualification == QUALIFICATION_PARTIAL:
            # A partial result may be published as a partial product, but it
            # never releases a required downstream step and is never a normal
            # completion.
            current.status = StepStatus.COMPLETED
            current.output_digests = dict(outcome.output_digests)
            current.receipt_digest = outcome.receipt_digest
            current.result_digest = outcome.result_digest
            current.products = tuple(outcome.products)
            current.publish_payload = outcome.to_dict()
            self._outcomes[step.step_id] = outcome
            self._publish_step(step, outcome)
            return
        if step.diagnostic_only and qualification != QUALIFICATION_DIAGNOSTIC:
            current.qualification = QUALIFICATION_DIAGNOSTIC
            outcome = StepOutcome(
                output_digests=dict(outcome.output_digests),
                receipt_digest=outcome.receipt_digest,
                result_digest=outcome.result_digest,
                publish_payload=dict(outcome.publish_payload),
                qualification=QUALIFICATION_DIAGNOSTIC,
                publish_policy=PUBLISH_POLICY_NONE,
                products=tuple(outcome.products),
                execution_identity=outcome.execution_identity,
                identity_scheme=outcome.identity_scheme,
            )
            current.publish_policy = PUBLISH_POLICY_NONE
        current.status = StepStatus.COMPLETED
        current.output_digests = dict(outcome.output_digests)
        current.receipt_digest = outcome.receipt_digest
        current.result_digest = outcome.result_digest
        current.products = tuple(outcome.products)
        current.publish_payload = outcome.to_dict()
        self._outcomes[step.step_id] = outcome
        if current.qualification == QUALIFICATION_DIAGNOSTIC:
            # Diagnostic results are never published as normal science.
            current.publish_status = PublishStatus.NOT_PUBLISHED
            current.publication_reason = "diagnostic_only"
            return
        self._publish_step(step, outcome)

    def _publish_step(self, step: StepSpec, outcome: StepOutcome) -> bool:
        current = self.state.states[step.step_id]
        self.state.publication_attempts += 1
        if outcome.publish_policy == PUBLISH_POLICY_NONE:
            current.publish_status = PublishStatus.NOT_PUBLISHED
            current.publication_reason = "publish_policy_none"
            return False
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
                if not self._poll_control():
                    break
                current.status = StepStatus.READY
                self._dispatch_step(step)
                progressed = True
            if not progressed:
                break
        if self._quota is not None:
            self._quota.settle(
                task_ref=self.dag.task_ref,
                attempt=self.state.attempt,
                consumed=self.state.budget.to_dict(),
            )
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
        quota: StepQuotaPort | None = None,
        control: ControlPoll | None = None,
        attempt: int = 0,
        owner: str = "",
        fencing: str = "",
        input_digest: str = "",
        work_dir: str = "",
        adopt: bool = True,
    ) -> BoundedStepDagExecutor:
        """Rebuild from persisted state, resetting only unfinished steps.

        ``completed`` (published or publication-failed), ``failed``, ``blocked``
        and ``cancelled`` are preserved; ``running``/``ready``/``pending`` become
        ``pending`` again.  The caller then calls ``run()``, which re-dispatches
        only the unfinished steps (and retries publication separately).

        M2-04b adoption rules (never guess):

        * an unknown/missing ``identity_scheme`` or an older schema is refused
          (:class:`StepDagError`) — migration is an explicit decision, recorded
          in the recovery fixtures;
        * the persisted plan identity, plan-definition digest and frozen body
          digest must equal the current freeze; the input digest must match when
          both sides know it;
        * a completed step's recorded execution identity and upstream digests
          must be reproducible from the current definition — otherwise the
          state is a *conflict*, not an adoption.

        D09 (M1-04b): a restart needs a re-dispatch allowance for the steps that
        were actually interrupted.  The allowance is **derived from the persisted
        evidence** (one per previously ``running`` step) instead of being added
        implicitly for every step; a caller may pass an explicit
        ``recovery_allowance`` only when it has a stronger authorization.
        """
        state = DagRunState.from_dict(persisted, adopt=adopt)
        known = {step.step_id for step in dag.steps}
        for step_id in list(state.states):
            if step_id not in known:
                del state.states[step_id]
        if adopt:
            definition_scheme = str(getattr(dag.definition, "identity_scheme", "") or "")
            fixture_scheme = IDENTITY_SCHEME_FIXTURE_FOR_RUN
            expected = definition_scheme or fixture_scheme
            if state.identity_scheme != expected:
                raise StepDagError(
                    "identity_scheme_mismatch",
                    f"persisted identity scheme {state.identity_scheme!r} != current {expected!r}",
                )
            if state.plan_identity and plan_identity and state.plan_identity != plan_identity:
                raise StepDagError("plan_conflict", "persisted plan identity != current plan")
            if (
                state.plan_definition_digest
                and dag.plan_definition_digest
                and state.plan_definition_digest != dag.plan_definition_digest
            ):
                raise StepDagError(
                    "definition_conflict",
                    "persisted plan-definition digest != current frozen definition",
                )
            if input_digest and state.input_digest and state.input_digest != input_digest:
                raise StepDagError("input_conflict", "persisted input digest != current input")
        for step in dag.steps:
            current = state.states.get(step.step_id)
            if current is None:
                state.states[step.step_id] = StepState(step_id=step.step_id)
                continue
            if current.status in _UNFINISHED:
                current.status = StepStatus.PENDING
                current.reason = ""
                continue
            if step.diagnostic_only and current.qualification != QUALIFICATION_DIAGNOSTIC:
                # M2-04a re-judgement: a diagnostic-only step is never adopted
                # as normal science, whatever an older state recorded — its
                # publication is revoked so no required consumer is released.
                current.qualification = QUALIFICATION_DIAGNOSTIC
                current.publish_policy = PUBLISH_POLICY_NONE
                if current.publish_status is PublishStatus.PUBLISHED:
                    current.publish_status = PublishStatus.NOT_PUBLISHED
                    current.publication_reason = "diagnostic_only_rejudged"
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
            quota=quota,
            control=control,
            attempt=attempt or state.attempt,
            owner=owner or state.owner,
            fencing=fencing or state.fencing,
            input_digest=input_digest or state.input_digest,
            work_dir=work_dir,
        )


#: Identity scheme recorded by a fixture DAG run state.
IDENTITY_SCHEME_FIXTURE_FOR_RUN = "static-fixture.v0"

#: Production execution-identity scheme (CCCP ``v2:`` step identities).
IDENTITY_SCHEME_PRODUCTION = "acp2.step-execution.v2"


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

    It is deliberately *not* a production ``DispatchFn`` on its own: a plan
    ``dict`` never reaches a runner in the production path — the typed
    ``TaskRequest`` is built by :class:`acp_services.runtime.step_dispatch.
    StepDispatchAdapter`, which also maps the completion decision and declares
    the real products.
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
