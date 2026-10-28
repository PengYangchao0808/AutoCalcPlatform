"""Freeze-time step-DAG definition: the ONE conversion from recipe to runtime DAG.

M2-04b separates two identities that the pre-M2-04 code conflated:

``step definition identity``
    Computed **once at freeze time** by the existing approval/plan identity
    authority (``acp.calculations.identity``) and carried on the execution
    envelope as ``step_identities`` — read here as
    :attr:`StepDagDefinition.step_definition_identities`.  Wire compatibility is
    preserved: the envelope/receipt field keeps its name, only its *meaning* is
    now explicit.
``step execution identity``
    Materialized **only at dispatch time** by
    :func:`cccp.calculation.recipes.compile.materialize_step_identity` from the
    frozen definition plus the *actual* upstream content digests and the
    provider versions present at dispatch.  There is no second fingerprint
    algorithm in Runtime: this module delegates to CCCP and nothing else.

The frozen definition carries the closed recipe + inputs payloads (the same
canonical documents the freeze compiled) so a worker can rebuild the compiled
definition deterministically and verify the ``v2:`` plan-definition digest
before it dispatches anything.  A digest disagreement is a typed refusal — the
worker never repairs, re-freezes or writes back to the envelope.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from acp_services.runtime.step_dag import (
    STEP_TASK_KINDS,
    BoundedStepDag,
    Limits,
    OutputRef,
    ScienceStepKind,
    StepDagError,
    StepSpec,
)

logger = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_SCHEME_CCCP",
    "FROZEN_STEP_DAG_KEY",
    "output_ref_address",
    "IDENTITY_SCHEME_FIXTURE",
    "STEP_DEFINITION_SCHEMA",
    "ADR008_REQUIRED_STEPS",
    "Adr008StepRow",
    "StepDagDefinition",
    "adr008_step_declaration",
    "bounded_step_dag_from_compiled",
    "compile_step_dag_definition",
    "definition_from_frozen_payload",
    "frozen_plan_body_digest_v2",
]

#: Execution identity scheme of a definition-driven DAG (CCCP ``v2:`` domain).
IDENTITY_SCHEME_CCCP: Final = "acp2.step-execution.v2"
#: Fixture-only scheme: a handwritten StepSpec DAG with no frozen definition.
#: Such a DAG can be replayed for tests/static proofs but is never adoptable
#: production science and never carries a materialized execution identity.
IDENTITY_SCHEME_FIXTURE: Final = "static-fixture.v0"

#: Wire identity of the frozen step-DAG definition document.
STEP_DEFINITION_SCHEMA: Final = "acp2.runtime.step-dag-definition.v1"

#: Reserved frozen-plan payload key carrying the bounded step DAG + definition.
FROZEN_STEP_DAG_KEY: Final = "bounded_step_dag"

#: Supported science step kind per CCCP task kind (dispatchable subset).
_TASK_KIND_TO_STEP_KIND: Mapping[str, ScienceStepKind] = {
    task_kind.value: kind for kind, task_kind in STEP_TASK_KINDS.items()
}


#: The bounded DAG consumes whole products; a compiled selector must match it.
WHOLE_PRODUCT_SELECTOR: Final = "whole_product"


def output_ref_address(from_step: str, product_kind: str) -> str:
    """Return the CCCP dependency address for one whole-product upstream ref.

    Delegates to :func:`cccp.calculation.recipes.compile.output_ref_address`
    with the frozen whole-product selector, so the runtime keys its
    ``upstream_digests`` map exactly the way the CCCP identity does.  A second
    addressing scheme would silently make an identity unmaterializable.
    """
    from cccp.calculation.recipes import OutputRef as CccpOutputRef
    from cccp.calculation.recipes import Selector
    from cccp.calculation.recipes import output_ref_address as cccp_address

    return cccp_address(
        CccpOutputRef(
            from_step=from_step,
            product_kind=product_kind,
            selector=Selector(selector_kind=WHOLE_PRODUCT_SELECTOR),
        )
    )


def verify_plan_digests(
    *,
    approval_plan_digest: str,
    frozen_plan_body_digest: str,
    expected_approval_plan_digest: str,
    expected_frozen_plan_body_digest: str,
) -> None:
    """Verify the approval (``sha256:``) and body (``v2:``) digests separately.

    Each one is checked in **its own domain** against its own expected value.
    A missing or mismatching digest invalidates the attempt, and a correct value
    on one side never compensates for the other (that is exactly the identity
    hole M2-04b closes).
    """
    if not approval_plan_digest or not approval_plan_digest.startswith("sha256:"):
        raise StepDagError(
            "invalid_approval_digest",
            f"the approval plan digest {approval_plan_digest!r} is not a sha256: digest",
        )
    if not frozen_plan_body_digest or not frozen_plan_body_digest.startswith("v2:"):
        raise StepDagError(
            "invalid_body_digest",
            f"the frozen plan body digest {frozen_plan_body_digest!r} is not a v2: fingerprint",
        )
    if approval_plan_digest != expected_approval_plan_digest:
        raise StepDagError(
            "approval_digest_mismatch",
            f"approval plan digest {approval_plan_digest!r} != frozen "
            f"{expected_approval_plan_digest!r}",
        )
    if frozen_plan_body_digest != expected_frozen_plan_body_digest:
        raise StepDagError(
            "body_digest_mismatch",
            f"frozen plan body digest {frozen_plan_body_digest!r} != recomputed "
            f"{expected_frozen_plan_body_digest!r}",
        )


def frozen_plan_body_digest_v2(plan_body: Mapping[str, Any]) -> str:
    """Return the ``v2:`` digest of a frozen plan body (never a ``sha256:``).

    The approval ``plan_digest`` lives in the ``sha256:`` domain and the frozen
    body digest lives in the ``v2:`` domain (M1-04 D06).  They are verified
    **separately** and never compensate for one another, so this function never
    returns a ``sha256:`` value and never accepts one as input.
    """
    from cccp.analysis.identity import identity_fingerprint

    try:
        return identity_fingerprint(dict(plan_body))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise StepDagError("invalid_plan_body", str(exc)) from exc


@dataclass(frozen=True, slots=True)
class StepDagDefinition:
    """Frozen definition of one bounded recipe DAG (worker only deserializes)."""

    recipe_id: str
    plan_definition_digest: str
    recipe_payload: Mapping[str, Any]
    inputs_payload: Mapping[str, Any]
    provider_versions: Mapping[str, str] = field(default_factory=dict)
    step_definition_identities: tuple[str, ...] = ()
    #: M2-04b: the two digests are verified **separately** and in their own
    #: domains.  ``approval_plan_digest`` is the approved ``sha256:`` plan digest;
    #: ``frozen_plan_body_digest`` is the recomputed ``v2:`` body digest.  A
    #: change in either invalidates the attempt; neither ever compensates for
    #: the other.
    approval_plan_digest: str = ""
    frozen_plan_body_digest: str = ""
    identity_scheme: str = IDENTITY_SCHEME_CCCP
    worst_case_executions: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.recipe_id:
            raise StepDagError("invalid_definition", "recipe_id is required")
        if not self.plan_definition_digest.startswith("v2:"):
            raise StepDagError(
                "invalid_definition",
                f"plan_definition_digest must be a v2: fingerprint, got "
                f"{self.plan_definition_digest!r}",
            )
        if self.identity_scheme != IDENTITY_SCHEME_CCCP:
            raise StepDagError(
                "unknown_identity_scheme",
                f"unsupported identity scheme {self.identity_scheme!r}",
            )
        object.__setattr__(self, "recipe_payload", MappingProxyType(dict(self.recipe_payload)))
        object.__setattr__(self, "inputs_payload", MappingProxyType(dict(self.inputs_payload)))
        object.__setattr__(
            self,
            "provider_versions",
            MappingProxyType({str(k): str(v) for k, v in dict(self.provider_versions).items()}),
        )
        object.__setattr__(
            self,
            "step_definition_identities",
            tuple(str(item) for item in self.step_definition_identities),
        )
        if self.approval_plan_digest and not self.approval_plan_digest.startswith("sha256:"):
            raise StepDagError(
                "invalid_definition",
                f"approval_plan_digest must be a sha256: digest, got {self.approval_plan_digest!r}",
            )
        if self.frozen_plan_body_digest and not self.frozen_plan_body_digest.startswith("v2:"):
            raise StepDagError(
                "invalid_definition",
                "frozen_plan_body_digest must be a v2: fingerprint, got "
                f"{self.frozen_plan_body_digest!r}",
            )
        object.__setattr__(
            self,
            "worst_case_executions",
            MappingProxyType({str(k): int(v) for k, v in dict(self.worst_case_executions).items()}),
        )

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STEP_DEFINITION_SCHEMA,
            "recipe_id": self.recipe_id,
            "plan_definition_digest": self.plan_definition_digest,
            "identity_scheme": self.identity_scheme,
            "recipe": dict(self.recipe_payload),
            "inputs": dict(self.inputs_payload),
            "provider_versions": dict(self.provider_versions),
            "step_definition_identities": list(self.step_definition_identities),
            "approval_plan_digest": self.approval_plan_digest,
            "frozen_plan_body_digest": self.frozen_plan_body_digest,
            "worst_case_executions": dict(self.worst_case_executions),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StepDagDefinition:
        if not isinstance(payload, Mapping):
            raise StepDagError("invalid_definition", "definition payload must be a mapping")
        schema = str(payload.get("schema") or "")
        if schema != STEP_DEFINITION_SCHEMA:
            raise StepDagError(
                "invalid_definition", f"expected schema {STEP_DEFINITION_SCHEMA}, got {schema!r}"
            )
        recipe = payload.get("recipe")
        inputs = payload.get("inputs")
        if not isinstance(recipe, Mapping) or not isinstance(inputs, Mapping):
            raise StepDagError(
                "invalid_definition", "definition must carry the closed recipe and inputs payloads"
            )
        return cls(
            recipe_id=str(payload.get("recipe_id") or ""),
            plan_definition_digest=str(payload.get("plan_definition_digest") or ""),
            recipe_payload=dict(recipe),
            inputs_payload=dict(inputs),
            provider_versions=dict(payload.get("provider_versions") or {}),
            step_definition_identities=tuple(
                str(item) for item in payload.get("step_definition_identities") or ()
            ),
            approval_plan_digest=str(payload.get("approval_plan_digest") or ""),
            frozen_plan_body_digest=str(payload.get("frozen_plan_body_digest") or ""),
            identity_scheme=str(payload.get("identity_scheme") or IDENTITY_SCHEME_CCCP),
            worst_case_executions=dict(payload.get("worst_case_executions") or {}),
        )

    # -- CCCP delegation -------------------------------------------------

    def compiled(self) -> Any:
        """Rebuild the compiled definition and verify the frozen digest.

        The worker never trusts a payload it cannot reproduce: a different
        ``v2:`` plan-definition digest is a typed refusal.
        """
        from cccp.calculation.recipes import compile_recipe

        compiled = compile_recipe(dict(self.recipe_payload), dict(self.inputs_payload))
        if compiled.plan_definition_digest != self.plan_definition_digest:
            raise StepDagError(
                "definition_digest_mismatch",
                "frozen definition recompiled to "
                f"{compiled.plan_definition_digest!r}, frozen "
                f"{self.plan_definition_digest!r}",
            )
        return compiled

    def execution_identity(
        self,
        step_id: str,
        upstream_digests: Mapping[str, str],
        *,
        materialized_parameters: Mapping[str, Any] | None = None,
    ) -> Any:
        """Materialize one step's execution identity through CCCP (the only scheme)."""
        from cccp.calculation.recipes import materialize_step_identity

        compiled = self.compiled()
        return materialize_step_identity(
            compiled,
            step_id,
            dict(upstream_digests),
            dict(self.provider_versions),
            materialized_parameters=materialized_parameters,
        )


def compile_step_dag_definition(
    recipe: Mapping[str, Any] | Any,
    inputs: Mapping[str, Any] | None,
    *,
    step_definition_identities: Sequence[str] = (),
    frozen_plan_body: Mapping[str, Any] | None = None,
    approval_plan_digest: str = "",
    provider_versions: Mapping[str, str] | None = None,
    task_ref: str = "",
    limits: Limits | None = None,
) -> tuple[BoundedStepDag, StepDagDefinition]:
    """Compile a recipe **at freeze time** into (runtime DAG, frozen definition).

    This is the single conversion M2-04b asks for: the science is compiled once
    here, the ``StepSpec`` view is derived from the compiled steps, and the
    worker only deserializes the result.  The per-step definition identities are
    *carried* from the approval/plan freeze — never recomputed here.
    """
    from cccp.calculation.recipes import compile_recipe

    compiled = compile_recipe(recipe, inputs)
    dag, definition = bounded_step_dag_from_compiled(
        compiled,
        task_ref=task_ref or compiled.recipe_id,
        recipe_payload=(dict(recipe) if isinstance(recipe, Mapping) else dict(recipe.to_dict())),
        inputs_payload=dict(inputs or {}),
        step_definition_identities=step_definition_identities,
        frozen_plan_body=frozen_plan_body,
        approval_plan_digest=approval_plan_digest,
        provider_versions=provider_versions,
        limits=limits,
    )
    return dag, definition


def bounded_step_dag_from_compiled(
    compiled: Any,
    *,
    task_ref: str,
    recipe_payload: Mapping[str, Any],
    inputs_payload: Mapping[str, Any],
    step_definition_identities: Sequence[str] = (),
    frozen_plan_body: Mapping[str, Any] | None = None,
    approval_plan_digest: str = "",
    provider_versions: Mapping[str, str] | None = None,
    limits: Limits | None = None,
) -> tuple[BoundedStepDag, StepDagDefinition]:
    """Derive the runtime ``StepSpec`` DAG from an already compiled recipe."""
    steps: list[StepSpec] = []
    for compiled_step in compiled.steps:
        task_kind = compiled_step.task_kind
        if task_kind is None or not compiled_step.dispatchable:
            raise StepDagError(
                "unsupported_step_kind",
                f"{compiled_step.step_id}: pure-analysis/non-dispatchable steps are not "
                "dispatchable bounded DAG steps",
            )
        kind = _TASK_KIND_TO_STEP_KIND.get(str(task_kind))
        if kind is None:
            raise StepDagError(
                "unsupported_step_kind",
                f"{compiled_step.step_id}: task kind {task_kind!r} has no bounded DAG "
                f"step kind ({sorted(STEP_TASK_KINDS)})",
            )
        recipe_step = compiled_step.recipe_step
        for reference in (*recipe_step.consumes, *recipe_step.produces):
            if reference.selector.selector_kind != WHOLE_PRODUCT_SELECTOR:
                raise StepDagError(
                    "unsupported_selector",
                    f"{compiled_step.step_id}: bounded DAG steps consume whole products; "
                    f"{reference.from_step}/{reference.product_kind} uses selector "
                    f"{reference.selector.selector_kind!r}",
                )
        produces = tuple(
            OutputRef(
                from_step=reference.from_step,
                product_kind=reference.product_kind,
                required=bool(reference.required if reference.required is not None else True),
            )
            for reference in recipe_step.produces
        )
        if not produces:
            produces = (OutputRef(from_step=compiled_step.step_id, product_kind="step_result"),)
        steps.append(
            StepSpec(
                step_id=compiled_step.step_id,
                kind=kind,
                intent=str(recipe_step.intent),
                required=bool(recipe_step.required),
                diagnostic_only=bool(recipe_step.diagnostic_only),
                parameters=dict(compiled_step.parameters),
                consumes=tuple(
                    OutputRef(
                        from_step=reference.from_step,
                        product_kind=reference.product_kind,
                        required=bool(
                            reference.required if reference.required is not None else True
                        ),
                    )
                    for reference in recipe_step.consumes
                ),
                produces=produces,
                structure_role=str(getattr(recipe_step, "structure_role", "minimum") or "minimum"),
                implementation_version=str(
                    (provider_versions or {}).get(compiled_step.implementation_id, "")
                    or compiled_step.implementation_id
                ),
                capability=str(compiled_step.binding_id or compiled_step.implementation_id),
                max_attempts=1,
                qc_invocations=max(1, int(compiled_step.worst_case_executions)),
                worst_case_executions=int(compiled_step.worst_case_executions),
            )
        )
    dag = BoundedStepDag(
        recipe_id=compiled.recipe_id,
        task_ref=task_ref,
        steps=tuple(steps),
        limits=limits or Limits(),
    )
    errors = dag.validate()
    if errors:
        raise StepDagError("invalid_dag", "; ".join(errors))
    definition = StepDagDefinition(
        recipe_id=compiled.recipe_id,
        plan_definition_digest=compiled.plan_definition_digest,
        recipe_payload=dict(recipe_payload),
        inputs_payload=dict(inputs_payload),
        provider_versions=dict(provider_versions or {}),
        step_definition_identities=tuple(step_definition_identities),
        approval_plan_digest=str(approval_plan_digest or ""),
        frozen_plan_body_digest=(
            frozen_plan_body_digest_v2(frozen_plan_body) if frozen_plan_body is not None else ""
        ),
        worst_case_executions=compiled.worst_case_by_step(),
    )
    dag = BoundedStepDag(
        recipe_id=dag.recipe_id,
        task_ref=dag.task_ref,
        steps=dag.steps,
        limits=dag.limits,
        definition=definition,
    )
    return dag, definition


def definition_from_frozen_payload(
    payload: Mapping[str, Any],
) -> tuple[BoundedStepDag, StepDagDefinition]:
    """Deserialize the frozen plan payload member into (DAG, definition)."""
    raw = payload.get(FROZEN_STEP_DAG_KEY)
    if not isinstance(raw, Mapping):
        raise StepDagError(
            "missing_step_dag",
            f"the frozen plan carries no {FROZEN_STEP_DAG_KEY!r} definition",
        )
    definition = StepDagDefinition.from_dict(raw.get("definition") or {})
    compiled = definition.compiled()
    steps_payload = raw.get("steps")
    if not isinstance(steps_payload, Sequence) or not steps_payload:
        dag, _ = bounded_step_dag_from_compiled(
            compiled,
            task_ref=str(raw.get("task_ref") or ""),
            recipe_payload=dict(definition.recipe_payload),
            inputs_payload=dict(definition.inputs_payload),
            step_definition_identities=definition.step_definition_identities,
            provider_versions=dict(definition.provider_versions),
            approval_plan_digest=definition.approval_plan_digest,
        )
        return dag, definition
    dag = BoundedStepDag(
        recipe_id=str(raw.get("recipe_id") or definition.recipe_id),
        task_ref=str(raw.get("task_ref") or ""),
        steps=tuple(StepSpec.from_dict(item) for item in steps_payload),
        limits=Limits(),
        definition=definition,
    )
    errors = dag.validate()
    if errors:
        raise StepDagError("invalid_dag", "; ".join(errors))
    return dag, definition


# ---------------------------------------------------------------------------
# ADR-008 (DA) declaration — evidence chain, never a hard-coded execution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Adr008StepRow:
    """One ADR-008 required recipe step and its bounded-DAG mapping verdict."""

    index: int
    step_id: str
    task_kind: str
    required: bool
    mapping: str
    dag_step_kind: str = ""
    reason: str = ""

    @property
    def open(self) -> bool:
        """``True`` when the step cannot be dispatched by this DAG today."""
        return self.mapping != "mapped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step_id": self.step_id,
            "task_kind": self.task_kind,
            "required": self.required,
            "mapping": self.mapping,
            "dag_step_kind": self.dag_step_kind,
            "reason": self.reason,
        }


#: ADR-008 §4 — the nine required recipe steps, in order.  ``mapping`` is
#: ``mapped`` only for the task kinds this bounded DAG can dispatch today; every
#: other row stays ``OPEN`` (declared, not faked): the ADR lists the step as
#: required, so a partial chain must never be presented as the full chain.
ADR008_REQUIRED_STEPS: tuple[Adr008StepRow, ...] = (
    Adr008StepRow(
        1,
        "endpoint_prep",
        "conformer_search",
        True,
        "open",
        reason="conformer_search has no bounded StepDispatchAdapter task core mapping",
    ),
    Adr008StepRow(
        2,
        "candidate_path",
        "xtb_path_search",
        True,
        "open",
        reason="xtb_path_search has no bounded StepDispatchAdapter task core mapping",
    ),
    Adr008StepRow(
        3,
        "candidate_frequency",
        "frequency",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.FREQUENCY.value,
    ),
    Adr008StepRow(
        4,
        "ts_optimize",
        "optimize",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.OPTIMIZE.value,
    ),
    Adr008StepRow(
        5,
        "ts_frequency",
        "frequency",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.FREQUENCY.value,
    ),
    Adr008StepRow(
        6,
        "irc",
        "irc",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.IRC.value,
    ),
    Adr008StepRow(
        7,
        "endpoint_reactants",
        "singlepoint",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.SINGLEPOINT.value,
    ),
    Adr008StepRow(
        8,
        "endpoint_product",
        "singlepoint",
        True,
        "mapped",
        dag_step_kind=ScienceStepKind.SINGLEPOINT.value,
    ),
    Adr008StepRow(
        9,
        "energy_thermochemistry",
        "thermochemistry",
        True,
        "open",
        reason="thermochemistry has no bounded StepDispatchAdapter task core mapping",
    ),
)


def adr008_step_declaration() -> dict[str, Any]:
    """Return the ADR-008 step declaration + evidence chain status.

    ``complete`` is ``False`` while any required row is ``open``: the DA control
    chain cannot be claimed from a partial mapping, and this function never
    invents an execution to close a row.
    """
    rows = [row.to_dict() for row in ADR008_REQUIRED_STEPS]
    open_rows = [row.step_id for row in ADR008_REQUIRED_STEPS if row.open]
    return {
        "schema": "acp2.runtime.adr008-step-declaration.v1",
        "adr": "ADR-008",
        "recipe_id": "recipe.diels_alder.limited.v1",
        "criteria_version": "1.0.0",
        "steps": rows,
        "mapped": [row.step_id for row in ADR008_REQUIRED_STEPS if not row.open],
        "open": open_rows,
        "complete": not open_rows,
        "evidence_chain": {
            "definition_identity": "carried from the approved plan (step_definition_identities)",
            "execution_identity": IDENTITY_SCHEME_CCCP,
            "per_step_record": "WORK/00_RUNTIME/steps/<step_id>/step_result.json",
            "receipt_chain": "step_execution_chain + chain_digest (additive)",
            "unmapped_policy": "OPEN — never hard-coded, never replaced by a partial chain",
        },
    }
