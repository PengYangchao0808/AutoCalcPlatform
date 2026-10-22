"""Compile-only probe for the limited ACP2 Diels-Alder scientific recipe.

This is deliberately **not** the A-15b pytest selection (that is todo 30).  It
is the runnable, static assertion the plan requires for A-15a: the recipe
parses, validates as a finite scientific DAG, compiles twice byte-identically
through :func:`cccp.calculation.recipes.compile.compile_recipe`, and the typed
task-request DAG round-trips against the frozen ``compiled-recipe.json``.

It never executes a step, starts a subprocess, writes a job/task record or
starts a scheduler/dispatch loop.  ``--emit`` only (re)writes the two
compile-time fixture records.

QA failure mode::

    python compile_probe.py --remove-step irc

removes the IRC step from an in-memory copy and requires the required-
completion check to fail (exit 1).  The frozen fixture is never modified.

Run the happy path::

    python compile_probe.py
    python compile_probe.py --emit      # regenerate the compile records
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from cccp.analysis.research._common import canonical_json
from cccp.calculation.recipes import (
    WHOLE_STEP_PRODUCT_KIND,
    OutputRef,
    Recipe,
    RecipeContractError,
    Selector,
    compile_recipe,
    existing_task_kinds,
    output_ref_address,
    validate_recipe,
)
from cccp.calculation.recipes.compile import RecipeInputs
from cccp.calculation.requests import MethodSpec, StructureInput, options_to_dict

FIXTURE_DIR = Path(__file__).resolve().parent
RECIPE_FILE = "recipe.json"
COMPILED_FILE = "compiled-recipe.json"
DIGEST_FILE = "recipe-digests.json"
COMPILE_RECORD_SCHEMA = "acp2.science.recipe-compile-record"
COMPILE_RECORD_SCHEMA_VERSION = 1

#: The required scientific-completion set for the limited DA control chain.
#: Missing any of these is a required-completion failure, not a diagnostic gap.
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

_PLATFORM_TOKEN_RE = re.compile(r"(job|queue|account|price|status)")
_WHOLE = Selector(selector_kind="whole_product")


class ProbeError(RuntimeError):
    """A compile-only probe assertion failed."""


def _read_json(path: Path) -> dict[str, Any]:
    payload = path.read_text(encoding="utf-8")
    import json

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ProbeError(f"{path}: expected a JSON object")
    return data


def _digest(payload: object) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _flatten_atom_ids(side: Mapping[str, Any]) -> list[str]:
    structure_id = str(side["structure_id"])
    return [f"{structure_id}:{atom_id}" for atom_id in side["atom_ids"]]


def _atom_mapping(reaction: Mapping[str, Any]) -> tuple[int, ...]:
    """Return the exact left->right permutation frozen by ``reaction.json``.

    The mapping is read from the frozen fixture rather than re-declared here,
    so the compile inputs cannot silently drift from the frozen reaction.
    """
    left = [atom for side in reaction["left"] for atom in _flatten_atom_ids(side)]
    right = [atom for side in reaction["right"] for atom in _flatten_atom_ids(side)]
    left_to_right = reaction["atom_mapping"]["left_to_right"]
    positions = {atom: index for index, atom in enumerate(right)}
    mapping = tuple(positions[left_to_right[atom]] for atom in left)
    if len(set(mapping)) != len(mapping):
        raise ProbeError("reaction atom mapping is not one-to-one")
    return mapping


def _comparison_conditions(
    manifest: Mapping[str, Any],
    question_set: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the scalar comparison conditions admitted by RecipeInputs."""
    question = question_set["questions"][0]
    conditions = question["comparison_conditions"]
    science = manifest["science_parameters"]
    return {
        "basis": science["basis"],
        "energy_unit": conditions["energy_unit"],
        "method": conditions["method"],
        "pressure_pa": conditions["pressure_pa"],
        "reference_state": conditions["reference_state"],
        "standard_state": conditions["standard_state"],
        "temperature_k": conditions["temperature_k"],
    }


def _build_inputs(fixture_dir: Path) -> RecipeInputs:
    reaction = _read_json(fixture_dir / "reaction.json")
    manifest = _read_json(fixture_dir / "fixture-manifest.json")
    question_set = _read_json(fixture_dir / "question.json")
    science = manifest["science_parameters"]
    state = science["electronic_state"]
    left_elements = tuple(element for side in reaction["left"] for element in side["elements"])
    return RecipeInputs(
        structure=StructureInput(
            path=fixture_dir / "reactants.xyz",
            elements=left_elements,
            role="minimum",
            source="constructed frozen reactants (frozen_known_candidate)",
        ),
        charge=int(science["charge"]),
        multiplicity=int(science["multiplicity"]),
        method=MethodSpec(method=str(science["method"]), basis=str(science["basis"])),
        backend=None,
        electronic_state={
            "state_id": str(state["state_id"]),
            "label": str(state["label"]),
            "target_multiplicity": int(state["target_multiplicity"]),
        },
        standard_state=None,
        atom_mapping=_atom_mapping(reaction),
        comparison_conditions=_comparison_conditions(manifest, question_set),
        provider_versions={},
    )


def _main_steps(recipe: Recipe) -> dict[str, Any]:
    return {step.step_id: step for step in recipe.steps}


def _scan_field_names(payload: object, path: str = "") -> list[str]:
    """Return every field name carrying a forbidden platform token."""
    findings: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = str(key)
            location = f"{path}.{name}" if path else name
            if _PLATFORM_TOKEN_RE.search(name):
                findings.append(location)
            findings.extend(_scan_field_names(value, location))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(_scan_field_names(item, f"{path}[{index}]"))
    return findings


def _check_required_completion(recipe: Recipe) -> list[str]:
    """Return required-completion failures for the frozen DA control chain."""
    steps = _main_steps(recipe)
    missing = [step_id for step_id in REQUIRED_STEP_IDS if step_id not in steps]
    if missing:
        return [f"missing required step(s) {missing}"]

    failures: list[str] = []
    for step_id in REQUIRED_STEP_IDS:
        step = steps[step_id]
        if not step.required or step.diagnostic_only:
            failures.append(f"{step_id} must be required and not diagnostic_only")
    if (
        steps["ts_optimize"].capability_ref != "geometry.optimize"
        or not (steps["ts_optimize"].parameters or {}).get("mode") == "transition_state"
    ):
        failures.append(
            "ts_optimize must be the existing optimize capability in transition_state mode"
        )
    for frequency_step in ("candidate_frequency", "ts_frequency"):
        if steps[frequency_step].capability_ref != "vibration.compute":
            failures.append(
                f"{frequency_step} must use the registered vibration.compute capability"
            )
    if steps["irc"].task_kind != "irc":
        failures.append("irc must reference the existing irc task kind")
    else:
        directions = tuple(sorted((steps["irc"].parameters or {}).get("directions", ())))
        if directions != ("forward", "reverse"):
            failures.append("irc must declare both forward and reverse directions")
    if steps["energy_thermochemistry"].task_kind != "thermochemistry":
        failures.append(
            "energy_thermochemistry must reference the existing thermochemistry task kind"
        )
    endpoint_steps = (steps["endpoint_reactants"], steps["endpoint_product"])
    if {step.produces[0].product_kind for step in endpoint_steps} != {"endpoint_energy"}:
        failures.append("both IRC endpoint checks must produce endpoint_energy")
    return failures


def _check_native_mode_selector(recipe: Recipe) -> list[str]:
    """Prove the TS mode is selected by native printed index, not position."""
    step = _main_steps(recipe)["ts_optimize"]
    mode_refs = [ref for ref in step.consumes if ref.product_kind == "native_printed_mode"]
    if len(mode_refs) != 1:
        return [f"ts_optimize must consume exactly one native_printed_mode, got {len(mode_refs)}"]
    selector = mode_refs[0].selector
    failures: list[str] = []
    if selector.selector_kind != "mode":
        failures.append(
            f"native mode selector must use selector_kind='mode', got {selector.selector_kind!r}"
        )
    if selector.field != "native_printed_index":
        failures.append(
            "native mode selector must name field='native_printed_index', "
            f"got field={selector.field!r}"
        )
    if selector.index is not None:
        failures.append("native mode selector must not use a positional index")
    if not mode_refs[0].from_step:
        failures.append("native mode selector must carry upstream provenance")
    return failures


def _check_unregistered_execution_paths(recipe: Recipe) -> list[str]:
    """Reject non-inventory task kinds and named-reaction execution engines."""
    inventory = set(existing_task_kinds())
    failures: list[str] = []
    for step in (*recipe.steps, *(s for block in recipe.alternatives for s in block.steps)):
        if step.task_kind is not None and step.task_kind not in inventory:
            failures.append(f"{step.step_id}: task_kind {step.task_kind!r} is not in the inventory")
        if step.task_kind is not None and "diels" in step.task_kind:
            failures.append(f"{step.step_id}: named-reaction execution engine {step.task_kind!r}")
        if step.capability_ref is None and step.task_kind is None:
            failures.append(f"{step.step_id}: no capability or task-kind reference")
    return failures


def _dependency_refs(compiled: Any, step: Any) -> tuple[OutputRef, ...]:
    """Return the typed dependencies exactly as the identity materializer does."""
    references: list[OutputRef] = list(step.recipe_step.consumes)
    selected_steps = {reference.from_step for reference in references}
    for edge in compiled.edges:
        if edge.to_step != step.step_id:
            continue
        if edge.output_ref is not None:
            references.append(edge.output_ref)
        elif edge.from_step not in selected_steps:
            references.append(
                OutputRef(
                    from_step=edge.from_step,
                    product_kind=WHOLE_STEP_PRODUCT_KIND,
                    selector=_WHOLE,
                )
            )
    unique: dict[str, OutputRef] = {}
    for reference in references:
        unique.setdefault(output_ref_address(reference), reference)
    return tuple(unique[address] for address in sorted(unique))


def _step_record(compiled: Any, step: Any) -> dict[str, Any]:
    declared = dict(step.parameters) if step.parameters else None
    effective = options_to_dict(step.request.options) if step.request is not None else None
    dependencies = []
    for reference in _dependency_refs(compiled, step):
        address = output_ref_address(reference)
        dependencies.append(
            {
                "address": address,
                "address_digest": _digest(address),
                "from_step": reference.from_step,
                "product_kind": reference.product_kind,
                "selector": reference.selector.to_dict(),
                "upstream_content_digest": None,
                "note": "compile-only: no step was executed, so no upstream content digest exists",
            }
        )
    return {
        "step_id": step.step_id,
        "intent": step.recipe_step.intent,
        "required": step.recipe_step.required,
        "diagnostic_only": step.recipe_step.diagnostic_only,
        "capability_ref": step.recipe_step.capability_ref,
        "task_kind": step.task_kind,
        "selection": step.selection.to_dict(),
        "binding_id": step.binding_id,
        "implementation_id": step.implementation_id,
        "delegation_target": step.delegation_target,
        "dispatchable": step.dispatchable,
        "declared_parameters": declared,
        "declared_parameters_digest": None if declared is None else _digest(declared),
        "effective_options": effective,
        "effective_options_digest": None if effective is None else _digest(effective),
        "dependencies": dependencies,
    }


def _compile_record(compiled: Any) -> dict[str, Any]:
    return {
        "schema": COMPILE_RECORD_SCHEMA,
        "schema_version": COMPILE_RECORD_SCHEMA_VERSION,
        "recipe_id": compiled.recipe_id,
        "plan_definition_digest": compiled.plan_definition_digest,
        "schema_digest": compiled.schema_digest,
        "topological_order": list(compiled.topological_order),
        "required_step_ids": list(REQUIRED_STEP_IDS),
        "steps": [_step_record(compiled, step) for step in compiled.steps],
    }


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _print_dependency_table(compiled: Any) -> None:
    print("STEP/DEPENDENCY TABLE")
    header = f"{'step_id':30} {'kind':22} {'required':8} {'dispatchable':12} depends_on"
    print(header)
    print("-" * len(header))
    for step in compiled.steps:
        references = _dependency_refs(compiled, step)
        depends = ", ".join(
            f"{reference.from_step}:{reference.product_kind}:{reference.selector.selector_kind}"
            for reference in references
        )
        kind = step.task_kind or (step.recipe_step.capability_ref or "<none>")
        print(
            f"{step.step_id:30} {kind:22} {str(step.recipe_step.required):8} "
            f"{str(step.dispatchable):12} {depends}"
        )


def _print_digest_table(record: Mapping[str, Any]) -> None:
    print("PARAMETER/DEPENDENCY DIGESTS")
    print(f"{'step_id':30} {'declared_parameters_digest':70} dependencies")
    print("-" * 120)
    for step in record["steps"]:
        declared = step["declared_parameters_digest"] or "<none>"
        print(f"{step['step_id']:30} {declared:70} {len(step['dependencies'])}")


def _fail(message: str) -> int:
    print(f"required_completion: FAIL - {message}")
    return 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=FIXTURE_DIR)
    parser.add_argument(
        "--emit",
        action="store_true",
        help="rewrite compiled-recipe.json and recipe-digests.json from this compile",
    )
    parser.add_argument(
        "--remove-step",
        action="append",
        default=[],
        metavar="STEP_ID",
        help="QA failure mode: remove a main step from an in-memory copy and require failure",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    fixture_dir: Path = args.fixture_dir
    recipe_path = fixture_dir / RECIPE_FILE
    if not recipe_path.is_file():
        print(f"missing recipe: {recipe_path}", file=sys.stderr)
        return 3

    recipe_text = recipe_path.read_text(encoding="utf-8")
    import json

    recipe_payload = json.loads(recipe_text)
    if not isinstance(recipe_payload, dict):
        print("recipe.json is not an object", file=sys.stderr)
        return 3

    if args.remove_step:
        for step_id in args.remove_step:
            before = len(recipe_payload["steps"])
            recipe_payload["steps"] = [
                step for step in recipe_payload["steps"] if step.get("step_id") != step_id
            ]
            if len(recipe_payload["steps"]) == before:
                return _fail(f"step {step_id!r} was not present in the copy")
            recipe_payload["edges"] = [
                edge
                for edge in recipe_payload["edges"]
                if edge.get("from_step") != step_id and edge.get("to_step") != step_id
            ]
            for remaining in recipe_payload["steps"]:
                remaining["consumes"] = [
                    reference
                    for reference in remaining.get("consumes", ())
                    if reference.get("from_step") != step_id
                ]
        try:
            removed_recipe = Recipe.from_dict(recipe_payload)
            validate_recipe(removed_recipe)
            completion = _check_required_completion(removed_recipe)
        except RecipeContractError as exc:
            return _fail(f"{type(exc).__name__}: {exc}")
        if completion:
            return _fail("; ".join(completion))
        print("required_completion: PASS (unexpected: the removed step was not required)")
        return 2

    recipe = Recipe.from_json(recipe_text)
    canonical_recipe = recipe.to_json()
    if canonical_recipe != recipe_text:
        print("recipe canonical round-trip: FAIL")
        return 1
    print("recipe canonical round-trip: PASS")

    report = validate_recipe(recipe)
    completion = _check_required_completion(recipe)
    if completion:
        return _fail("; ".join(completion))
    native_mode = _check_native_mode_selector(recipe)
    if native_mode:
        return _fail("; ".join(native_mode))
    unregistered = _check_unregistered_execution_paths(recipe)
    if unregistered:
        return _fail("; ".join(unregistered))
    platform_fields = _scan_field_names(recipe.to_dict())
    if platform_fields:
        return _fail(f"forbidden platform field(s) {platform_fields}")

    inputs = _build_inputs(fixture_dir)
    first = compile_recipe(recipe, inputs)
    second = compile_recipe(recipe, inputs)
    if first.definition_json != second.definition_json or first.to_json() != second.to_json():
        print("compiled round-trip: FAIL")
        return 1
    print("compiled round-trip: PASS")

    record = _compile_record(first)
    compiled_path = fixture_dir / COMPILED_FILE
    digest_path = fixture_dir / DIGEST_FILE
    if args.emit:
        _atomic_write(compiled_path, first.to_json())
        _atomic_write(digest_path, canonical_json(record))
        print(f"emitted {compiled_path}")
        print(f"emitted {digest_path}")
    else:
        if not compiled_path.is_file():
            return _fail(f"missing {compiled_path}; rerun with --emit")
        if compiled_path.read_text(encoding="utf-8") != first.to_json():
            return _fail(f"{compiled_path} does not match the current compile; rerun with --emit")
        if not digest_path.is_file():
            return _fail(f"missing {digest_path}; rerun with --emit")
        if digest_path.read_text(encoding="utf-8") != canonical_json(record):
            return _fail(f"{digest_path} does not match the current compile; rerun with --emit")
        print(f"compiled fixture match: PASS ({compiled_path.name})")
        print(f"digest record match: PASS ({digest_path.name})")

    print(f"recipe_id: {first.recipe_id}")
    print(f"steps: {len(first.steps)}")
    print(f"topological_order: {', '.join(report.topological_order)}")
    print(f"plan_definition_digest: {first.plan_definition_digest}")
    print(f"schema_digest: {first.schema_digest}")
    print("required_completion: PASS")
    print("native_mode_selector: PASS (field native_printed_index; no positional index)")
    print("platform_field_scan: PASS")
    _print_dependency_table(first)
    _print_digest_table(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
