"""Behaviour tests for the ACP2 scientific recipe contracts and validator.

The recipe contract is a finite scientific DAG, not a scheduler.  These tests
prove the invariants the JSON schema cannot express on its own:

* a three-step linear recipe and a valid diagnostic branch validate;
* cycles, dangling references and missing required upstreams raise distinct
  typed errors;
* a diagnostic-only step can never satisfy a required downstream dependency;
* required upstream failures propagate, diagnostic failures do not;
* canonical JSON round-trips byte-identically and rejects other versions,
  forbidden platform tokens and undeclared fields;
* importing the recipe package pulls in no task/QC/ACP module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.recipes import (
    Alternative,
    Applicability,
    DanglingReferenceError,
    DiagnosticDependencyError,
    DuplicateStepError,
    Edge,
    Limits,
    MissingRequiredStepError,
    OutputRef,
    Recipe,
    RecipeCycleError,
    RecipeForbiddenFieldError,
    RecipeLimitError,
    RecipeSchemaError,
    RecipeSchemaVersionError,
    Selector,
    Step,
    StepFlagConflictError,
    UnknownRecipeCapabilityError,
    UnknownRecipeTaskKindError,
    blocked_steps,
    existing_task_kinds,
    validate_recipe,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_WHOLE = Selector(selector_kind="whole_product")


def _step(
    step_id: str,
    *,
    capability: str | None = None,
    task_kind: str | None = None,
    required: bool = True,
    diagnostic_only: bool = False,
    consumes: tuple[OutputRef, ...] = (),
) -> Step:
    """Build one step with the linear-fixture defaults."""
    return Step(
        step_id=step_id,
        intent=f"{step_id} intent",
        required=required,
        diagnostic_only=diagnostic_only,
        capability_ref=capability,
        task_kind=task_kind,
        consumes=consumes,
    )


def _linear_recipe(
    *,
    recipe_id: str = "recipe.linear",
    max_steps: int = 8,
    extra_steps: tuple[Step, ...] = (),
    extra_edges: tuple[Edge, ...] = (),
) -> Recipe:
    """Return a valid singlepoint -> optimize -> frequency recipe."""
    sp = _step("sp", capability="electronic.energy")
    opt = _step(
        "opt",
        capability="geometry.optimize",
        consumes=(OutputRef("sp", "energy", _WHOLE),),
    )
    freq = _step(
        "freq",
        capability="vibration.compute",
        consumes=(OutputRef("opt", "geometry", _WHOLE),),
    )
    return Recipe(
        recipe_id=recipe_id,
        applicability=Applicability(
            supported_reaction_classes=("small_molecule",),
            preconditions=("closed_shell",),
        ),
        steps=(sp, opt, freq, *extra_steps),
        edges=(
            Edge("edge.sp_opt", "sp", "opt"),
            Edge("edge.opt_freq", "opt", "freq"),
            *extra_edges,
        ),
        limits=Limits(max_steps=max_steps),
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_three_step_linear_recipe_validates() -> None:
    recipe = _linear_recipe()
    report = validate_recipe(recipe)

    assert report.is_valid is True
    assert report.step_ids == ("sp", "opt", "freq")
    assert report.topological_order == ("sp", "opt", "freq")
    assert report.required_step_ids == ("sp", "opt", "freq")
    assert report.diagnostic_step_ids == ()
    assert [(rule.from_step, rule.to_step) for rule in report.dependency_rules] == [
        ("sp", "opt"),
        ("opt", "freq"),
    ]
    assert all(rule.blocks_on_upstream_failure for rule in report.dependency_rules)


def test_valid_diagnostic_branch_validates_and_never_blocks() -> None:
    diagnostic = _step(
        "diag_scan",
        task_kind="scan",
        required=False,
        diagnostic_only=True,
        consumes=(OutputRef("sp", "energy", _WHOLE),),
    )
    recipe = _linear_recipe(
        extra_steps=(diagnostic,),
        extra_edges=(Edge("edge.sp_diag", "sp", "diag_scan"),),
    )
    report = validate_recipe(recipe)

    assert report.diagnostic_step_ids == ("diag_scan",)
    diagnostic_rule = next(
        rule
        for rule in report.dependency_rules
        if rule.to_step == "diag_scan" and rule.from_step == "sp"
    )
    assert diagnostic_rule.upstream_diagnostic is False
    assert diagnostic_rule.required_evidence is False
    assert diagnostic_rule.blocks_on_upstream_failure is False

    # A required upstream failure blocks the required chain...
    assert blocked_steps(report, {"sp"}) == ("opt", "freq")
    # ...while a diagnostic failure blocks nothing.
    assert blocked_steps(report, {"diag_scan"}) == ()


def test_validate_recipe_accepts_canonical_mapping() -> None:
    recipe = _linear_recipe()
    report = validate_recipe(recipe.to_dict())
    assert report.recipe_id == "recipe.linear"
    assert report.topological_order == ("sp", "opt", "freq")


# ---------------------------------------------------------------------------
# Typed failure paths
# ---------------------------------------------------------------------------


def test_cycle_raises_typed_cycle_error() -> None:
    sp = _step("sp", capability="electronic.energy")
    opt = _step("opt", capability="geometry.optimize")
    recipe = Recipe(
        recipe_id="recipe.cycle",
        applicability=Applicability(),
        steps=(sp, opt),
        edges=(Edge("edge.a", "sp", "opt"), Edge("edge.b", "opt", "sp")),
        limits=Limits(max_steps=4),
    )

    with pytest.raises(RecipeCycleError) as excinfo:
        validate_recipe(recipe)
    assert set(excinfo.value.cycle) >= {"sp", "opt"}


def test_dangling_reference_raises_typed_error() -> None:
    sp = _step("sp", capability="electronic.energy")
    opt = _step(
        "opt",
        capability="geometry.optimize",
        consumes=(OutputRef("missing", "energy", _WHOLE),),
    )
    recipe = Recipe(
        recipe_id="recipe.dangling",
        applicability=Applicability(),
        steps=(sp, opt),
        edges=(),
        limits=Limits(max_steps=4),
    )

    with pytest.raises(DanglingReferenceError) as excinfo:
        validate_recipe(recipe)
    assert any(reference.step_id == "missing" for reference in excinfo.value.references)


def test_missing_required_upstream_raises_typed_error() -> None:
    optional = _step("optional", capability="electronic.energy", required=False)
    required = _step(
        "required",
        capability="geometry.optimize",
        consumes=(OutputRef("optional", "energy", _WHOLE),),
    )
    recipe = Recipe(
        recipe_id="recipe.missing_required",
        applicability=Applicability(),
        steps=(optional, required),
        edges=(Edge("edge.optional_required", "optional", "required"),),
        limits=Limits(max_steps=4),
    )

    with pytest.raises(MissingRequiredStepError) as excinfo:
        validate_recipe(recipe)
    assert excinfo.value.detail["upstream_step"] == "optional"
    assert excinfo.value.detail["downstream_step"] == "required"


def test_diagnostic_step_cannot_satisfy_required_dependency() -> None:
    diagnostic = _step(
        "diag",
        task_kind="scan",
        required=False,
        diagnostic_only=True,
    )
    required = _step(
        "required",
        capability="geometry.optimize",
        consumes=(OutputRef("diag", "scan_profile", _WHOLE),),
    )
    recipe = Recipe(
        recipe_id="recipe.diagnostic_dependency",
        applicability=Applicability(),
        steps=(diagnostic, required),
        edges=(Edge("edge.diag_required", "diag", "required"),),
        limits=Limits(max_steps=4),
    )

    with pytest.raises(DiagnosticDependencyError) as excinfo:
        validate_recipe(recipe)
    assert excinfo.value.detail["upstream_step"] == "diag"
    assert excinfo.value.detail["downstream_step"] == "required"


def test_cycle_dangling_and_missing_required_are_distinct_classes() -> None:
    assert not issubclass(RecipeCycleError, DanglingReferenceError)
    assert not issubclass(DanglingReferenceError, MissingRequiredStepError)
    assert not issubclass(MissingRequiredStepError, DiagnosticDependencyError)


def test_duplicate_step_ids_raise_typed_error() -> None:
    first = _step("dup", capability="electronic.energy")
    second = _step("dup", capability="geometry.optimize")
    recipe = Recipe(
        recipe_id="recipe.duplicate",
        applicability=Applicability(),
        steps=(first, second),
        edges=(),
        limits=Limits(max_steps=4),
    )

    with pytest.raises(DuplicateStepError):
        validate_recipe(recipe)


def test_required_and_diagnostic_only_are_mutually_exclusive() -> None:
    with pytest.raises(StepFlagConflictError):
        Step(
            step_id="conflict",
            intent="conflict",
            required=True,
            diagnostic_only=True,
            capability_ref="electronic.energy",
        )


def test_unknown_capability_raises_typed_error() -> None:
    step = _step("unknown", capability="chemistry.not_registered")
    recipe = Recipe(
        recipe_id="recipe.unknown_capability",
        applicability=Applicability(),
        steps=(step,),
        edges=(),
        limits=Limits(max_steps=2),
    )

    with pytest.raises(UnknownRecipeCapabilityError):
        validate_recipe(recipe)


def test_valid_alternative_block_validates() -> None:
    optional_step = _step("alt_sp", capability="electronic.energy")
    alternative = Alternative(
        alternative_id="alt.energy",
        when="primary single point is unusable",
        max_uses=1,
        steps=(optional_step,),
        replaces_step_id="opt",
    )
    recipe = _linear_recipe()
    recipe = Recipe(
        recipe_id=recipe.recipe_id,
        applicability=recipe.applicability,
        steps=recipe.steps,
        edges=recipe.edges,
        limits=recipe.limits,
        alternatives=(alternative,),
    )

    report = validate_recipe(recipe)
    assert report.topological_order == ("sp", "opt", "freq")


def test_main_edge_cannot_target_alternative_step() -> None:
    alternative_step = _step("alt_only", capability="electronic.energy")
    alternative = Alternative(
        alternative_id="alt.only",
        when="fallback",
        max_uses=1,
        steps=(alternative_step,),
    )
    recipe = _linear_recipe(
        extra_edges=(Edge("edge.opt_alt", "opt", "alt_only"),),
    )
    recipe = Recipe(
        recipe_id=recipe.recipe_id,
        applicability=recipe.applicability,
        steps=recipe.steps,
        edges=recipe.edges,
        limits=recipe.limits,
        alternatives=(alternative,),
    )

    with pytest.raises(DanglingReferenceError):
        validate_recipe(recipe)


def test_unknown_task_kind_raises_typed_error() -> None:
    with pytest.raises(UnknownRecipeTaskKindError):
        Step(
            step_id="unknown_kind",
            intent="unknown kind",
            required=True,
            diagnostic_only=False,
            task_kind="not_a_task_kind",
        )


def test_declared_limits_are_enforced() -> None:
    recipe = _linear_recipe(max_steps=2)
    with pytest.raises(RecipeLimitError):
        validate_recipe(recipe)


# ---------------------------------------------------------------------------
# Canonical serialization and contract closure
# ---------------------------------------------------------------------------


def test_recipe_round_trips_byte_identically() -> None:
    recipe = _linear_recipe()
    payload = recipe.to_json()

    assert payload == recipe.to_json()
    assert Recipe.from_json(payload).to_json() == payload
    assert Recipe.from_dict(recipe.to_dict()).to_dict() == recipe.to_dict()
    assert json.loads(payload)["schema_version"] == 1


def test_unknown_schema_version_is_rejected() -> None:
    payload: dict[str, Any] = _linear_recipe().to_dict()
    payload["schema_version"] = 2

    with pytest.raises(RecipeSchemaVersionError):
        Recipe.from_dict(payload)


def test_forbidden_platform_token_is_rejected() -> None:
    payload: dict[str, Any] = _linear_recipe().to_dict()
    payload["job_status"] = "running"

    with pytest.raises(RecipeForbiddenFieldError):
        Recipe.from_dict(payload)


def test_unknown_scientific_field_is_rejected() -> None:
    payload: dict[str, Any] = _linear_recipe().to_dict()
    payload["central_job_status"] = "running"

    with pytest.raises(RecipeForbiddenFieldError):
        Recipe.from_dict(payload)
    payload.pop("central_job_status")
    payload["undeclared_science_field"] = "x"
    with pytest.raises(RecipeSchemaError):
        Recipe.from_dict(payload)


def test_task_kind_inventory_is_the_binding_inventory() -> None:
    from cccp.calculation.capabilities.task_binding import EXISTING_TASK_KINDS

    assert existing_task_kinds() == tuple(EXISTING_TASK_KINDS)
    assert len(existing_task_kinds()) == 15


def test_importing_recipes_pulls_no_task_qc_or_acp_module() -> None:
    script = """
import sys
import cccp.calculation.recipes  # noqa: F401
heavy = sorted(
    name
    for name in sys.modules
    if name.startswith(("cccp.calculation.tasks", "cccp.qc", "acp"))
)
print(heavy)
raise SystemExit(0 if not heavy else 1)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# ---------------------------------------------------------------------------
# A-07b: deterministic compilation and the science identity boundary
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402

from cccp.calculation.recipes import (  # noqa: E402
    ApprovalMetadata,
    CompiledRecipe,
    InvalidUpstreamDigestError,
    RecipeInputs,
    StepExecutionIdentity,
    UnknownScientificFieldError,
    UnresolvedOutputRefError,
    compile_recipe,
    materialize_step_identity,
    output_ref_address,
    read_historical_recipe,
)
from cccp.calculation.requests import MethodSpec, StructureInput, TaskRequest  # noqa: E402


def _science_inputs(**overrides: Any) -> RecipeInputs:
    """Return frozen initial inputs with an inline H2 structure."""
    base: dict[str, Any] = {
        "structure": StructureInput(
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74)),
            symbols=("H", "H"),
        ),
        "charge": 0,
        "multiplicity": 1,
        "method": MethodSpec(method="r2SCAN-3c"),
        "provider_versions": {"orca": "6.0.1"},
    }
    base.update(overrides)
    return RecipeInputs(**base)


def _compiled(**overrides: Any) -> CompiledRecipe:
    """Compile the linear recipe with the shared science inputs."""
    return compile_recipe(_linear_recipe(), _science_inputs(**overrides))


def test_compile_twice_is_byte_identical_and_digest_stable() -> None:
    first = _compiled()
    second = _compiled()

    assert isinstance(first, CompiledRecipe)
    assert first.definition_json == second.definition_json
    assert first.to_json() == second.to_json()
    assert first.plan_definition_digest == second.plan_definition_digest
    assert first.schema_digest == second.schema_digest
    assert first.plan_definition_digest.startswith("v2:")
    assert first.schema_digest == (
        "sha256:" + hashlib.sha256(first.definition_json.encode("utf-8")).hexdigest()
    )


def test_each_compute_step_is_typed_task_request() -> None:
    compiled = _compiled()

    assert compiled.topological_order == ("sp", "opt", "freq")
    assert [step.task_kind for step in compiled.steps] == ["singlepoint", "optimize", "frequency"]
    for step in compiled.steps:
        assert isinstance(step.request, TaskRequest)
        assert step.request.task.value == step.task_kind
        assert step.request_view is not None
        assert step.request_view["task"] == step.task_kind


def _digest_with(**overrides: Any) -> str:
    if "recipe" in overrides:
        recipe = overrides.pop("recipe")
        return compile_recipe(recipe, _science_inputs(**overrides)).plan_definition_digest
    return _compiled(**overrides).plan_definition_digest


def test_plan_definition_digest_covers_every_science_field() -> None:
    base = _compiled().plan_definition_digest

    indexed = OutputRef("sp", "energy", Selector(selector_kind="index", index=0))
    selector_recipe = _linear_recipe()
    selector_recipe = Recipe(
        recipe_id=selector_recipe.recipe_id,
        applicability=selector_recipe.applicability,
        steps=(
            selector_recipe.steps[0],
            Step(
                step_id="opt",
                intent="opt intent",
                required=True,
                diagnostic_only=False,
                capability_ref="geometry.optimize",
                consumes=(indexed,),
            ),
            selector_recipe.steps[2],
        ),
        edges=selector_recipe.edges,
        limits=selector_recipe.limits,
    )

    variants = [
        _digest_with(atom_mapping=(1, 0)),
        _digest_with(method=MethodSpec(method="B3LYP", basis="def2-SVP")),
        _digest_with(charge=1, multiplicity=2),
        _digest_with(
            electronic_state={
                "state_id": "triplet",
                "label": "T1",
                "target_multiplicity": 3,
            }
        ),
        _digest_with(standard_state="1M"),
        _digest_with(provider_versions={"orca": "6.0.2"}),
        _digest_with(recipe=selector_recipe),
    ]

    for digest in variants:
        assert digest != base
    assert len(set(variants)) == len(variants)


def test_management_fields_never_change_the_definition_digest() -> None:
    base = _compiled()

    renamed = compile_recipe(
        _linear_recipe(),
        _science_inputs(),
        approval=ApprovalMetadata(
            approval_ref="approval-001",
            approval_status="approved",
            evidence_refs=("evidence/a.json", "evidence/b.json"),
            display_name="Linear science recipe",
            timestamp="2026-10-22T00:00:00Z",
        ),
    )
    retitled_recipe = Recipe(
        recipe_id=base.recipe.recipe_id,
        applicability=base.recipe.applicability,
        steps=base.recipe.steps,
        edges=base.recipe.edges,
        limits=base.recipe.limits,
        title="A different display name",
    )
    retitled = compile_recipe(retitled_recipe, _science_inputs())

    assert renamed.plan_definition_digest == base.plan_definition_digest
    assert renamed.to_json() == base.to_json()
    assert retitled.plan_definition_digest == base.plan_definition_digest
    assert renamed.approval.display_name == "Linear science recipe"


def test_changed_upstream_byte_changes_step_execution_identity() -> None:
    compiled = _compiled()
    reference = OutputRef("sp", "energy", _WHOLE)
    address = output_ref_address(reference)
    versions = {"orca": "6.0.1"}

    first = materialize_step_identity(compiled, "opt", {address: "a" * 64}, versions)
    second = materialize_step_identity(compiled, "opt", {address: "b" * 64}, versions)

    assert isinstance(first, StepExecutionIdentity)
    assert first.execution_identity.startswith("v2:")
    assert first.execution_identity != second.execution_identity
    assert first.plan_definition_digest == compiled.plan_definition_digest
    dependency = first.to_dict()["science"]["dependencies"][0]  # type: ignore[index]
    assert dependency["digest"] == "sha256:" + "a" * 64


def test_changed_dispatch_provider_version_changes_step_identity() -> None:
    compiled = _compiled()
    address = output_ref_address(OutputRef("sp", "energy", _WHOLE))

    first = materialize_step_identity(compiled, "opt", {address: "a" * 64}, {"orca": "6.0.1"})
    second = materialize_step_identity(compiled, "opt", {address: "a" * 64}, {"orca": "6.0.2"})

    assert first.execution_identity != second.execution_identity


def test_unresolved_output_ref_selector_fails_at_dispatch() -> None:
    unresolved = Step(
        step_id="opt",
        intent="opt intent",
        required=True,
        diagnostic_only=False,
        capability_ref="geometry.optimize",
        consumes=(OutputRef("sp", "energy", Selector(selector_kind="index")),),
    )
    recipe = Recipe(
        recipe_id="recipe.unresolved",
        applicability=Applicability(),
        steps=(_step("sp", capability="electronic.energy"), unresolved),
        edges=(Edge("edge.sp_opt", "sp", "opt"),),
        limits=Limits(max_steps=4),
    )
    compiled = compile_recipe(recipe, _science_inputs())
    address = output_ref_address(unresolved.consumes[0])

    with pytest.raises(UnresolvedOutputRefError) as excinfo:
        materialize_step_identity(compiled, "opt", {address: "a" * 64}, {})
    assert excinfo.value.detail["reason"] == "unresolved_output_ref_selector"


def test_missing_upstream_digest_fails_at_dispatch() -> None:
    compiled = _compiled()

    with pytest.raises(UnresolvedOutputRefError) as excinfo:
        materialize_step_identity(compiled, "opt", {}, {})
    assert excinfo.value.detail["reason"] == "missing_upstream_digest"


def test_invalid_upstream_digest_is_rejected() -> None:
    compiled = _compiled()
    address = output_ref_address(OutputRef("sp", "energy", _WHOLE))

    with pytest.raises(InvalidUpstreamDigestError):
        materialize_step_identity(compiled, "opt", {address: "not-a-digest"}, {})


def test_unknown_step_parameter_is_rejected_for_execution() -> None:
    typo = Step(
        step_id="sp",
        intent="single point",
        required=True,
        diagnostic_only=False,
        capability_ref="electronic.energy",
        parameters={"stabilty_check": True},
    )
    recipe = Recipe(
        recipe_id="recipe.unknown_parameter",
        applicability=Applicability(),
        steps=(typo,),
        edges=(),
        limits=Limits(max_steps=2),
    )

    assert validate_recipe(recipe).is_valid is True
    with pytest.raises(UnknownScientificFieldError) as excinfo:
        compile_recipe(recipe, _science_inputs())
    assert excinfo.value.detail["reason"] == "unknown_scientific_field"


def test_unknown_recipe_field_is_rejected_for_execution() -> None:
    payload: dict[str, Any] = _linear_recipe().to_dict()
    payload["undeclared_science_field"] = "x"

    with pytest.raises(RecipeSchemaError):
        compile_recipe(payload, _science_inputs())


def test_historical_read_preserves_unknown_fields_without_eligibility() -> None:
    payload: dict[str, Any] = _linear_recipe().to_dict()
    payload["undeclared_science_field"] = {"kept": True}
    payload["steps"][0]["undeclared_step_field"] = "also kept"

    historical = read_historical_recipe(payload)

    assert historical.execution_eligible is False
    assert historical.recovery_eligible is False
    assert historical.payload["undeclared_science_field"] == {"kept": True}
    step_payload = historical.payload["steps"][0]
    assert step_payload["undeclared_step_field"] == "also kept"
    assert "undeclared_science_field" in historical.unknown_fields
    assert any("undeclared_step_field" in path for path in historical.unknown_fields)

    with pytest.raises(RecipeSchemaError):
        compile_recipe(payload, _science_inputs())


def test_step_identity_excludes_platform_and_display_fields() -> None:
    compiled = _compiled()
    address = output_ref_address(OutputRef("sp", "energy", _WHOLE))
    identity = materialize_step_identity(
        compiled,
        "opt",
        {address: "a" * 64},
        {"orca": "6.0.1"},
    )
    payload = identity.to_dict()

    forbidden = {"job_id", "display_name", "approval_ref", "approval_status", "evidence_refs"}
    stack: list[Any] = [payload]
    seen: set[str] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            seen.update(str(key) for key in item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    assert forbidden.isdisjoint(seen)
    assert payload["science"]["dependencies"]  # materialized dependency digests
    assert payload["science"]["provider_versions"] == {"orca": "6.0.1"}
