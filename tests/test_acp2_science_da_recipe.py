"""Diels-Alder recipe compilation, mode provenance and upstream blocking.

Todo 30 (A-15b) exercises the frozen A-15a recipe fixture through the existing
contracts and compiler; it never adds a second DAG compiler, a scheduler or a
dispatcher.  The semantic invariants asserted here are the ones a JSON schema
cannot express:

* the compiled DAG keeps the nine required steps and carries no diagnostic
  step in the required completion set;
* the two alternatives are bounded (``max_uses == 1``) and their steps still
  reference a registered capability or one of the fifteen task kinds;
* the transition-state mode is selected by ORCA's native printed index from
  the candidate-frequency step (typed provenance), never by an array position,
  and an inexplicit mode selector is rejected instead of guessed;
* a failed or removed required upstream blocks every dependent transitively;
* frequency/IRC cannot be optional while the connectivity claim depends on
  them;
* a diagnostic scan step compiles through the same direct task-kind reference
  without becoming part of the required DA chain;
* neither the recipe nor the compiled DAG carries platform state.

The fixture files are read-only: this module compiles them in memory and
compares the result to the frozen records; it never rewrites them.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from cccp.analysis.tsmode.contracts import (
    TARGET_MODE_INVALID,
    FrequencySourceBundle,
    SourceLevelOfTheory,
    SourceModeRecord,
    TsmodeError,
)
from cccp.analysis.tsmode.mode_mapping import resolve_target_mode
from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.recipes import (
    DanglingReferenceError,
    DiagnosticDependencyError,
    MissingRequiredStepError,
    Recipe,
    UnknownCompiledStepError,
    UnresolvedOutputRefError,
    blocked_steps,
    compile_recipe,
    existing_task_kinds,
    materialize_step_identity,
    output_ref_address,
    validate_recipe,
)
from cccp.calculation.requests import TaskKind

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "v1"
RECIPE_FILE = FIXTURE_DIR / "recipe.json"
COMPILED_FILE = FIXTURE_DIR / "compiled-recipe.json"
DIGEST_FILE = FIXTURE_DIR / "recipe-digests.json"

#: The nine steps required for the frozen DA execution/control chain.
REQUIRED_STEP_IDS = (
    "endpoint_prep",
    "candidate_path",
    "candidate_frequency",
    "ts_optimize",
    "ts_frequency",
    "irc",
    "endpoint_reactants",
    "endpoint_product",
    "energy_thermochemistry",
)

#: Direct task-kind references in the frozen recipe and their expected kind.
EXPECTED_TASK_KINDS = {
    "endpoint_prep": "conformer_search",
    "candidate_path": "xtb_path_search",
    "candidate_frequency": "frequency",
    "ts_optimize": "optimize",
    "ts_frequency": "frequency",
    "irc": "irc",
    "endpoint_reactants": "singlepoint",
    "endpoint_product": "singlepoint",
    "energy_thermochemistry": "thermochemistry",
}

_PLATFORM_TOKEN_RE = re.compile(r"(job|queue|account|price|status)")
_EXPLICIT_PLATFORM_KEYS = frozenset(
    {
        "attempt",
        "job_id",
        "queue",
        "run_id",
        "scheduler",
        "status",
        "submission_id",
        "task_id",
        "work_dir",
    }
)


def _load_compile_probe() -> ModuleType:
    """Load the frozen A-15a probe to reuse its input builder and helpers."""
    spec = importlib.util.spec_from_file_location(
        "acp2_da_compile_probe",
        FIXTURE_DIR / "compile_probe.py",
    )
    if spec is None or spec.loader is None:  # pragma: no cover - filesystem contract
        raise RuntimeError("cannot load compile_probe.py from the DA fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROBE = _load_compile_probe()


def _recipe_payload() -> dict[str, Any]:
    """Return a mutable copy of the frozen recipe payload."""
    payload = json.loads(RECIPE_FILE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _recipe() -> Recipe:
    """Return the frozen recipe parsed through its canonical contract."""
    return Recipe.from_json(RECIPE_FILE.read_text(encoding="utf-8"))


def _compiled(recipe: Recipe | None = None) -> Any:
    """Compile the frozen recipe (or a semantic copy) with its frozen inputs."""
    return compile_recipe(
        _recipe() if recipe is None else recipe,
        _PROBE._build_inputs(FIXTURE_DIR),
    )


def _replace_ts_mode_selector(payload: dict[str, Any], selector: dict[str, Any]) -> None:
    """Replace the TS mode selector in an in-memory payload copy."""
    for step in payload["steps"]:
        if step["step_id"] != "ts_optimize":
            continue
        for reference in step["consumes"]:
            if reference["product_kind"] == "native_printed_mode":
                reference["selector"] = selector


def _strip_step(payload: dict[str, Any], step_id: str) -> dict[str, Any]:
    """Remove a step and every structural reference to it (in-memory copy only)."""
    payload["steps"] = [step for step in payload["steps"] if step["step_id"] != step_id]
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if edge["from_step"] != step_id and edge["to_step"] != step_id
    ]
    for step in payload["steps"]:
        step["consumes"] = [
            reference for reference in step["consumes"] if reference["from_step"] != step_id
        ]
    return payload


def _platform_field_names(payload: object, path: str = "") -> list[str]:
    """Return every field name carrying a forbidden platform token."""
    findings: list[str] = []
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key)
            location = f"{path}.{key}" if path else key
            if _PLATFORM_TOKEN_RE.search(key):
                findings.append(location)
            findings.extend(_platform_field_names(value, location))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(_platform_field_names(item, f"{path}[{index}]"))
    return findings


def _mode_bundle() -> FrequencySourceBundle:
    """Return a minimal bundle whose mode list order differs from the printed indices.

    The first list position carries native printed index 9 and the second
    carries 7, so a positional lookup would be observably wrong.
    """
    return FrequencySourceBundle(
        bundle_id="bundle.da.synthetic",
        revision="rev_da_synthetic",
        origin={"kind": "constructed"},
        elements=["C", "C"],
        masses_amu=[12.0, 12.0],
        coordinates_angstrom=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
        charge=0,
        multiplicity=1,
        level=SourceLevelOfTheory(method="wB97X-D4", basis="def2-TZVP"),
        modes=[
            SourceModeRecord(source_mode_index=9, frequency_cm1=-410.0),
            SourceModeRecord(source_mode_index=7, frequency_cm1=-180.0),
        ],
    )


# ---------------------------------------------------------------------------
# Compiled DAG and bounded alternatives
# ---------------------------------------------------------------------------


def test_compiled_dag_matches_the_frozen_fixture() -> None:
    recipe_text = RECIPE_FILE.read_text(encoding="utf-8")
    recipe = Recipe.from_json(recipe_text)
    compiled = _compiled(recipe)

    assert recipe.to_json() == recipe_text
    assert compiled.to_json() == COMPILED_FILE.read_text(encoding="utf-8")

    digest_record = json.loads(DIGEST_FILE.read_text(encoding="utf-8"))
    assert digest_record["plan_definition_digest"] == compiled.plan_definition_digest
    assert digest_record["schema_digest"] == compiled.schema_digest
    assert tuple(digest_record["required_step_ids"]) == REQUIRED_STEP_IDS
    assert tuple(compiled.topological_order) == REQUIRED_STEP_IDS


def test_compiled_dag_keeps_required_and_diagnostic_flags_honest() -> None:
    compiled = _compiled()
    report = compiled.validation

    assert report.required_step_ids == REQUIRED_STEP_IDS
    assert report.diagnostic_step_ids == ()
    for step in compiled.steps:
        assert step.recipe_step.required is True
        assert step.recipe_step.diagnostic_only is False
    assert all(rule.blocks_on_upstream_failure for rule in report.dependency_rules)


def test_every_step_references_a_registered_capability_or_task_kind() -> None:
    compiled = _compiled()
    inventory = set(existing_task_kinds())
    registry = builtin_registry()

    for step in compiled.steps:
        contract = step.recipe_step
        assert (contract.capability_ref is None) != (contract.task_kind is None)
        if contract.capability_ref is not None:
            registry.require(contract.capability_ref)
            assert step.selection.mode.value == "capability"
            assert step.selection.capability_id == contract.capability_ref
            assert step.task_kind == EXPECTED_TASK_KINDS[step.step_id]
        else:
            assert step.selection.mode.value == "existing_task_kind"
            assert step.selection.task_kind == contract.task_kind
            assert step.task_kind == contract.task_kind
        assert step.task_kind in inventory
        assert step.binding_id
        assert step.implementation_id


def test_alternatives_are_bounded_and_reference_registered_kinds() -> None:
    recipe = _recipe()
    compiled = _compiled(recipe)
    inventory = set(existing_task_kinds())
    registry = builtin_registry()
    main_ids = {step.step_id for step in recipe.steps}

    assert len(recipe.alternatives) == len(compiled.alternatives) == 2
    assert len(recipe.alternatives) <= recipe.limits.max_alternatives  # type: ignore[operator]
    alternative_step_ids: set[str] = set()
    for alternative in recipe.alternatives:
        assert alternative.max_uses == 1
        assert alternative.replaces_step_id in main_ids
        assert alternative.steps
        for step in alternative.steps:
            assert step.step_id not in main_ids
            assert step.step_id not in alternative_step_ids
            alternative_step_ids.add(step.step_id)
            assert step.required is True
            assert step.diagnostic_only is False
            if step.capability_ref is not None:
                registry.require(step.capability_ref)
                assert step.task_kind is None
            else:
                assert step.task_kind in inventory

    # Alternatives never enter the main graph or the required completion set.
    assert alternative_step_ids.isdisjoint(compiled.topological_order)
    assert alternative_step_ids.isdisjoint(compiled.validation.required_step_ids)
    for step_id in alternative_step_ids:
        with pytest.raises(UnknownCompiledStepError):
            compiled.step(step_id)


# ---------------------------------------------------------------------------
# Native printed-mode provenance
# ---------------------------------------------------------------------------


def test_native_ts_mode_uses_printed_index_and_upstream_provenance() -> None:
    compiled = _compiled()
    step = compiled.step("ts_optimize")
    mode_refs = [
        reference
        for reference in step.recipe_step.consumes
        if reference.product_kind == "native_printed_mode"
    ]

    assert len(mode_refs) == 1
    reference = mode_refs[0]
    assert reference.from_step == "candidate_frequency"
    assert reference.selector.selector_kind == "mode"
    assert reference.selector.field == "native_printed_index"
    assert reference.selector.index is None
    assert reference.selector.range is None

    # Provenance is the producing step and its typed selector, not a position.
    producer = compiled.step(reference.from_step)
    matching_outputs = [
        output
        for output in producer.recipe_step.produces
        if output.product_kind == "native_printed_mode"
        and output.selector.selector_kind == "mode"
        and output.selector.field == "native_printed_index"
    ]
    assert matching_outputs
    address = output_ref_address(reference)
    assert address.startswith('candidate_frequency::native_printed_mode::{"field"')
    assert "native_printed_index" in address
    assert '"index"' not in address

    assert step.request is not None
    assert step.request.options.mode.value == "transition_state"  # type: ignore[union-attr]


def test_mode_lookup_uses_the_native_printed_index_not_a_position() -> None:
    bundle = _mode_bundle()

    assert bundle.modes[0].source_mode_index == 9
    assert bundle.modes[1].source_mode_index == 7
    assert bundle.mode_by_index(7) is bundle.modes[1]
    assert bundle.mode_by_index(9) is bundle.modes[0]
    assert bundle.mode_by_index(0) is None


def test_inexplicit_mode_index_is_rejected() -> None:
    bundle = _mode_bundle()
    with pytest.raises(TsmodeError) as excinfo:
        resolve_target_mode(bundle, None)  # type: ignore[arg-type]
    assert excinfo.value.error_code == TARGET_MODE_INVALID

    payload = _recipe_payload()
    _replace_ts_mode_selector(payload, {"selector_kind": "mode"})
    recipe = Recipe.from_dict(payload)
    # The recipe can be read, but dispatch refuses to invent an index.
    assert validate_recipe(recipe).is_valid is True
    compiled = _compiled(recipe)
    step = compiled.step("ts_optimize")
    selector = next(
        reference.selector
        for reference in step.recipe_step.consumes
        if reference.product_kind == "native_printed_mode"
    )
    assert selector.selector_kind == "mode"
    assert selector.field is None
    assert selector.index is None

    digests = {
        output_ref_address(reference): "a" * 64
        for reference in _PROBE._dependency_refs(compiled, step)
    }
    with pytest.raises(UnresolvedOutputRefError) as excinfo:
        materialize_step_identity(compiled, "ts_optimize", digests, {})
    assert excinfo.value.detail["reason"] == "unresolved_output_ref_selector"
    assert excinfo.value.detail["from_step"] == "candidate_frequency"


# ---------------------------------------------------------------------------
# Upstream blocking and connectivity requirements
# ---------------------------------------------------------------------------


def test_failed_required_upstream_blocks_dependents_transitively() -> None:
    report = validate_recipe(_recipe())

    print("\nBLOCKED-STEP TABLE (failed required step -> transitively blocked dependents)")
    for failed_step_id in ("candidate_frequency", "ts_frequency", "irc"):
        dependents = ", ".join(blocked_steps(report, {failed_step_id})) or "<none>"
        print(f"  {failed_step_id:22} -> {dependents}")

    assert blocked_steps(report, {"irc"}) == (
        "endpoint_reactants",
        "endpoint_product",
        "energy_thermochemistry",
    )
    assert blocked_steps(report, {"ts_frequency"}) == (
        "irc",
        "endpoint_reactants",
        "endpoint_product",
        "energy_thermochemistry",
    )
    assert blocked_steps(report, {"candidate_frequency"}) == (
        "ts_optimize",
        "ts_frequency",
        "irc",
        "endpoint_reactants",
        "endpoint_product",
        "energy_thermochemistry",
    )
    assert blocked_steps(report, set()) == ()


def test_removed_required_upstream_blocks_dependents_or_cannot_validate() -> None:
    # Removing a completed required result is a failure of that step: the
    # dependent chain is blocked, never silently completed.
    report = validate_recipe(_recipe())
    assert blocked_steps(report, {"irc"}) == (
        "endpoint_reactants",
        "endpoint_product",
        "energy_thermochemistry",
    )

    # Deleting the step definition but keeping its consumers is a dangling DAG,
    # not a recipe that quietly drops a required stage.
    dangling_payload = _recipe_payload()
    dangling_payload["steps"] = [
        step for step in dangling_payload["steps"] if step["step_id"] != "irc"
    ]
    with pytest.raises(DanglingReferenceError):
        validate_recipe(Recipe.from_dict(dangling_payload))

    # A closed copy that strips the references validates but fails the required
    # completion set, so removal is still visible.
    stripped = _strip_step(_recipe_payload(), "irc")
    stripped_recipe = Recipe.from_dict(stripped)
    validate_recipe(stripped_recipe)
    assert _PROBE._check_required_completion(stripped_recipe) == [
        "missing required step(s) ['irc']"
    ]


@pytest.mark.parametrize("step_id", ["ts_frequency", "irc"])
def test_connectivity_steps_cannot_be_optional(step_id: str) -> None:
    payload = _recipe_payload()
    for step in payload["steps"]:
        if step["step_id"] == step_id:
            step["required"] = False

    with pytest.raises(MissingRequiredStepError) as excinfo:
        validate_recipe(Recipe.from_dict(payload))
    assert excinfo.value.detail["upstream_step"] == step_id


def test_irc_cannot_be_diagnostic_only_while_connectivity_is_claimed() -> None:
    payload = _recipe_payload()
    for step in payload["steps"]:
        if step["step_id"] == "irc":
            step["required"] = False
            step["diagnostic_only"] = True

    with pytest.raises(DiagnosticDependencyError) as excinfo:
        validate_recipe(Recipe.from_dict(payload))
    assert excinfo.value.detail["upstream_step"] == "irc"


# ---------------------------------------------------------------------------
# A scan uses the same task-kind reference but is not required in the DA recipe
# ---------------------------------------------------------------------------


def test_scan_step_compiles_through_the_same_task_kind_reference() -> None:
    payload = _recipe_payload()
    # The frozen DA recipe itself must never require a scan.
    assert all(step["task_kind"] != "scan" for step in payload["steps"])

    payload["steps"].append(
        {
            "step_id": "scan_diagnostics",
            "intent": (
                "Optional diagnostic relaxed scan over the mapped reaction coordinate; "
                "scan evidence is never required for the connectivity claim."
            ),
            "required": False,
            "diagnostic_only": True,
            "capability_ref": None,
            "task_kind": "scan",
            "parameters": None,
            "consumes": [
                {
                    "from_step": "ts_optimize",
                    "product_kind": "optimize_result",
                    "selector": {"selector_kind": "whole_product"},
                }
            ],
            "produces": [
                {
                    "from_step": "scan_diagnostics",
                    "product_kind": "scan_profile",
                    "selector": {"selector_kind": "whole_product"},
                }
            ],
            "validators": [],
            "alternatives": [],
        }
    )
    payload["edges"].append(
        {
            "edge_id": "edge.ts_optimize__scan_diagnostics",
            "from_step": "ts_optimize",
            "to_step": "scan_diagnostics",
        }
    )

    recipe = Recipe.from_dict(payload)
    report = validate_recipe(recipe)
    assert "scan_diagnostics" not in report.required_step_ids
    assert "scan_diagnostics" in report.diagnostic_step_ids
    assert blocked_steps(report, {"scan_diagnostics"}) == ()
    assert set(REQUIRED_STEP_IDS) <= set(report.required_step_ids)

    compiled = compile_recipe(recipe, _PROBE._build_inputs(FIXTURE_DIR))
    scan = compiled.step("scan_diagnostics")
    assert scan.task_kind == "scan"
    assert scan.selection.mode.value == "existing_task_kind"
    assert scan.selection.task_kind == "scan"
    assert scan.binding_id == "science.task.scan"
    assert scan.request is not None
    assert scan.request.task is TaskKind.SCAN
    assert scan.request_view is not None
    assert scan.request_view["task"] == "scan"
    # A direct task-kind reference is bindable for planning; this binding layer
    # executes only the three seeded compute kinds.
    assert scan.dispatchable is False

    # The frozen fixture still carries no scan step after the in-memory compile.
    assert all(step.task_kind != "scan" for step in _compiled().steps)


# ---------------------------------------------------------------------------
# No platform state in either document
# ---------------------------------------------------------------------------


def test_recipe_and_compiled_dag_have_no_platform_state_key() -> None:
    compiled = _compiled()
    documents = {
        "recipe": _recipe().to_dict(),
        "compiled": compiled.to_dict(),
        "frozen_recipe": _recipe_payload(),
        "frozen_compiled": json.loads(COMPILED_FILE.read_text(encoding="utf-8")),
    }
    for name, document in documents.items():
        assert _platform_field_names(document) == [], name


def test_no_explicit_platform_state_key_appears_at_any_depth() -> None:
    compiled = _compiled()
    seen: set[str] = set()
    stack: list[Any] = [compiled.to_dict(), _recipe().to_dict()]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            seen.update(str(key) for key in item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    assert seen.isdisjoint(_EXPLICIT_PLATFORM_KEYS)
    assert {"job_id", "task_id", "status", "queue", "account"}.isdisjoint(seen)
