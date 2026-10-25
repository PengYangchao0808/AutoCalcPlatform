#!/usr/bin/env python3
"""Build and compile a small recipe twice from the public recipe contracts.

The recipe is constructed in code (no fixture file, no repository path).  It is
a one-step finite science DAG: a single point evaluated through the registered
``electronic.energy`` capability.  Compilation is deterministic: the canonical
definition JSON and the ``v2:`` plan-definition digest must be identical
between the two compilations.  Nothing executes.

Usage:
    python compile_recipe.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

from cccp.calculation.recipes import (
    Applicability,
    Limits,
    Recipe,
    Step,
    compile_recipe,
)


def build_recipe() -> Recipe:
    """Return the example recipe: one single-point step, no edges."""
    return Recipe(
        recipe_id="recipe.example_singlepoint",
        title="Example one-step single point",
        applicability=Applicability(
            supported_reaction_classes=("net_reaction",),
            preconditions=(
                "one frozen input structure",
                "a single electronic-structure single point",
            ),
            unsupported_when=(
                "a conformer search or a multi-path mechanism is requested",
                "an experimental or universal accuracy claim is expected",
            ),
        ),
        steps=(
            Step(
                step_id="energy",
                intent="single-point energy of the frozen input structure",
                required=True,
                diagnostic_only=False,
                capability_ref="electronic.energy",
                parameters={"stability_check": False},
            ),
        ),
        edges=(),
        limits=Limits(max_steps=4, max_edges=0, max_alternatives=0),
    )


def main(argv: list[str] | None = None) -> int:
    """Compile twice and print the equality and identity facts."""
    parser = argparse.ArgumentParser(
        description="Compile a code-built ACP2 science recipe twice (software-free)."
    )
    parser.parse_args(argv)

    recipe = build_recipe()
    first = compile_recipe(recipe)
    second = compile_recipe(recipe)
    document: dict[str, Any] = {
        "example": "compile_recipe",
        "recipe_id": first.recipe_id,
        "schema_version": first.schema_version,
        "topological_order": list(first.topological_order),
        "compiled_steps": len(first.steps),
        "plan_definition_digest": first.plan_definition_digest,
        "schema_digest": first.schema_digest,
        "definition_json_sha256": hashlib.sha256(first.definition_json.encode("utf-8")).hexdigest(),
        "canonical_json_equal": first.definition_json == second.definition_json,
        "plan_definition_digest_equal": (
            first.plan_definition_digest == second.plan_definition_digest
        ),
        "canonical_definition": json.loads(first.definition_json),
    }
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document["canonical_json_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
