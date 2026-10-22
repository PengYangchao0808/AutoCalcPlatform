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
