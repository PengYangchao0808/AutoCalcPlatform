"""Deterministic validation of ACP2 scientific recipes (A-07a).

The validator is a pure graph check over a :class:`~cccp.calculation.recipes.
contracts.Recipe`.  It never executes a step, starts a subprocess, writes an
artifact or imports a task module; it only proves that the declared scientific
DAG is finite, closed and honest about required versus diagnostic evidence.

Enforced invariants:

* no cycle in the main dependency graph;
* no dangling step reference in an edge, ``consumes`` entry, ``produces``
  entry or alternative replacement;
* no duplicate step, edge or alternative id;
* a required step cannot consume required evidence from a non-required
  upstream (``MissingRequiredStepError``);
* a diagnostic-only step can never satisfy a required downstream dependency
  (``DiagnosticDependencyError``) and its failure never blocks a required
  dependent (see :func:`blocked_steps`);
* every referenced capability is registered and every task kind is in the
  fifteen-kind inventory owned by the capability binding;
* declared ``limits`` are respected.

Failure propagation is explicit in the returned report: every dependency
carries ``blocks_on_upstream_failure``, which is true only for required
evidence produced by a required, non-diagnostic upstream.  The free
:func:`blocked_steps` function applies that rule transitively without any
execution loop.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.capabilities.contracts import UnknownCapabilityError
from cccp.calculation.capabilities.registry import CapabilityRegistry
from cccp.calculation.recipes.contracts import (
    Recipe,
    RecipeContractError,
    Step,
    StepFlagConflictError,
    UnknownRecipeTaskKindError,
    existing_task_kinds,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DanglingReference",
    "DanglingReferenceError",
    "DependencyRule",
    "DiagnosticDependencyError",
    "DuplicateAlternativeError",
    "DuplicateEdgeError",
    "DuplicateStepError",
    "MissingRequiredStepError",
    "RecipeCycleError",
    "RecipeLimitError",
    "RecipeValidationError",
    "RecipeValidationReport",
    "UnknownRecipeCapabilityError",
    "blocked_steps",
    "validate_recipe",
]


# ---------------------------------------------------------------------------
# Typed validation errors
# ---------------------------------------------------------------------------


class RecipeValidationError(RecipeContractError):
    """Base class for a rejected recipe DAG."""


class DuplicateStepError(RecipeValidationError):
    """Two steps (main or alternative) declare the same id."""

    def __init__(self, step_id: str, *, where: str = "recipe.steps") -> None:
        """Create the error for *step_id*."""
        super().__init__(
            f"{where}: duplicate step id {step_id!r}",
            detail={"step_id": step_id, "reason": "duplicate_step_id"},
        )


class DuplicateEdgeError(RecipeValidationError):
    """Two edges declare the same id."""

    def __init__(self, edge_id: str) -> None:
        """Create the error for *edge_id*."""
        super().__init__(
            f"recipe.edges: duplicate edge id {edge_id!r}",
            detail={"edge_id": edge_id, "reason": "duplicate_edge_id"},
        )


class DuplicateAlternativeError(RecipeValidationError):
    """Two alternative blocks declare the same id."""

    def __init__(self, alternative_id: str) -> None:
        """Create the error for *alternative_id*."""
        super().__init__(
            f"recipe.alternatives: duplicate alternative id {alternative_id!r}",
            detail={"alternative_id": alternative_id, "reason": "duplicate_alternative_id"},
        )


class RecipeCycleError(RecipeValidationError):
    """The dependency graph contains a cycle."""

    def __init__(self, cycle: Iterable[str]) -> None:
        """Create the error naming one concrete cycle."""
        self.cycle = tuple(cycle)
        rendered = " -> ".join(self.cycle)
        super().__init__(
            f"recipe dependency graph contains a cycle: {rendered}",
            detail={"cycle": list(self.cycle), "reason": "cycle"},
        )


@dataclass(frozen=True, slots=True)
class DanglingReference:
    """One reference to a step id that the recipe does not declare."""

    location: str
    step_id: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe reference view."""
        return {"location": self.location, "step_id": self.step_id}


class DanglingReferenceError(RecipeValidationError):
    """A reference names a step id that is not declared."""

    def __init__(self, references: Iterable[DanglingReference]) -> None:
        """Create the error with every unresolved reference."""
        self.references = tuple(references)
        rendered = ", ".join(
            f"{reference.location} -> {reference.step_id!r}" for reference in self.references
        )
        super().__init__(
            f"recipe contains dangling step reference(s): {rendered}",
            detail={
                "references": [reference.to_dict() for reference in self.references],
                "reason": "dangling_reference",
            },
        )


class MissingRequiredStepError(RecipeValidationError):
    """A required dependency is produced by a step that is not required."""

    def __init__(self, upstream_step: str, downstream_step: str) -> None:
        """Create the error for one required/non-required upstream pair."""
        super().__init__(
            f"required step {downstream_step!r} consumes required evidence from "
            f"non-required step {upstream_step!r}",
            detail={
                "upstream_step": upstream_step,
                "downstream_step": downstream_step,
                "reason": "missing_required_upstream",
            },
        )


class DiagnosticDependencyError(RecipeValidationError):
    """A required step consumes required evidence from a diagnostic-only step."""

    def __init__(self, upstream_step: str, downstream_step: str) -> None:
        """Create the error for one diagnostic/required dependency."""
        super().__init__(
            f"required step {downstream_step!r} cannot use diagnostic-only step "
            f"{upstream_step!r} as required evidence",
            detail={
                "upstream_step": upstream_step,
                "downstream_step": downstream_step,
                "reason": "diagnostic_cannot_satisfy_required_dependency",
            },
        )


class UnknownRecipeCapabilityError(RecipeValidationError):
    """A step references a capability that is not registered."""

    code = "unknown_implementation"

    def __init__(self, capability_id: str, *, step_id: str) -> None:
        """Create the error for an unregistered *capability_id*."""
        super().__init__(
            f"step {step_id!r} references unregistered capability {capability_id!r}",
            detail={
                "capability_id": capability_id,
                "implementation_id": "<unregistered>",
                "step_id": step_id,
                "reason": "unknown_capability",
            },
        )


class RecipeLimitError(RecipeValidationError):
    """A recipe exceeds one of its declared size bounds."""

    def __init__(self, limit: str, *, actual: int, maximum: int) -> None:
        """Create the error for a violated *limit*."""
        super().__init__(
            f"recipe.{limit} limit exceeded: {actual} > {maximum}",
            detail={"limit": limit, "actual": actual, "maximum": maximum},
        )


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependencyRule:
    """One resolved dependency edge and its failure-propagation semantics."""

    from_step: str
    to_step: str
    required_evidence: bool
    upstream_required: bool
    upstream_diagnostic: bool
    blocks_on_upstream_failure: bool

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dependency view."""
        return {
            "from_step": self.from_step,
            "to_step": self.to_step,
            "required_evidence": self.required_evidence,
            "upstream_required": self.upstream_required,
            "upstream_diagnostic": self.upstream_diagnostic,
            "blocks_on_upstream_failure": self.blocks_on_upstream_failure,
        }


@dataclass(frozen=True, slots=True)
class RecipeValidationReport:
    """Deterministic validation result for a valid recipe."""

    recipe_id: str
    schema_version: int
    step_ids: tuple[str, ...]
    topological_order: tuple[str, ...]
    required_step_ids: tuple[str, ...]
    diagnostic_step_ids: tuple[str, ...]
    dependency_rules: tuple[DependencyRule, ...]

    @property
    def is_valid(self) -> bool:
        """Return ``True``; only valid recipes produce a report."""
        return True

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe validation report."""
        return {
            "recipe_id": self.recipe_id,
            "schema_version": self.schema_version,
            "is_valid": self.is_valid,
            "step_ids": list(self.step_ids),
            "topological_order": list(self.topological_order),
            "required_step_ids": list(self.required_step_ids),
            "diagnostic_step_ids": list(self.diagnostic_step_ids),
            "dependency_rules": [rule.to_dict() for rule in self.dependency_rules],
        }


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def _find_cycle(adjacency: Mapping[str, list[str]], candidates: Iterable[str]) -> tuple[str, ...]:
    """Return one concrete cycle inside *candidates*, or an empty tuple."""
    allowed = set(candidates)
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> tuple[str, ...] | None:
        state[node] = 1
        path.append(node)
        for target in adjacency.get(node, ()):
            if target not in allowed:
                continue
            if state.get(target) == 1:
                return tuple(path[path.index(target) :] + [target])
            if state.get(target, 0) == 0:
                found = visit(target)
                if found is not None:
                    return found
        path.pop()
        state[node] = 2
        return None

    for node in candidates:
        if state.get(node, 0) == 0:
            found = visit(node)
            if found is not None:
                return found
    return ()


def _topological_order(
    step_ids: tuple[str, ...],
    adjacency: Mapping[str, list[str]],
    indegree: Mapping[str, int],
) -> tuple[str, ...]:
    """Return a deterministic topological order or raise :class:`RecipeCycleError`."""
    remaining = dict(indegree)
    ready = [step_id for step_id in step_ids if remaining[step_id] == 0]
    order: list[str] = []
    cursor = 0
    while cursor < len(ready):
        node = ready[cursor]
        cursor += 1
        order.append(node)
        for target in adjacency.get(node, ()):
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    if len(order) != len(step_ids):
        visited = set(order)
        cycle = _find_cycle(adjacency, [step_id for step_id in step_ids if step_id not in visited])
        raise RecipeCycleError(cycle)
    return tuple(order)


def _unique_step_ids(recipe: Recipe) -> dict[str, Step]:
    """Index main steps by id, rejecting duplicates."""
    by_id: dict[str, Step] = {}
    for step in recipe.steps:
        if step.step_id in by_id:
            raise DuplicateStepError(step.step_id)
        by_id[step.step_id] = step
    return by_id


def _alternative_step_ids(recipe: Recipe, main_ids: set[str]) -> set[str]:
    """Validate alternative ids and return the ids of their steps."""
    seen_alternative_ids: set[str] = set()
    alternative_step_ids: set[str] = set()
    for alternative in recipe.alternatives:
        if alternative.alternative_id in seen_alternative_ids:
            raise DuplicateAlternativeError(alternative.alternative_id)
        seen_alternative_ids.add(alternative.alternative_id)
        for step in alternative.steps:
            if step.step_id in main_ids or step.step_id in alternative_step_ids:
                raise DuplicateStepError(step.step_id, where="alternative.steps")
            alternative_step_ids.add(step.step_id)
    return alternative_step_ids


def _check_unknown_references(
    steps: Iterable[Step],
    registry: CapabilityRegistry,
) -> None:
    """Reject unregistered capabilities and task kinds outside the inventory."""
    inventory = set(existing_task_kinds())
    for step in steps:
        if step.capability_ref is not None:
            try:
                registry.require(step.capability_ref)
            except UnknownCapabilityError as exc:
                raise UnknownRecipeCapabilityError(
                    step.capability_ref,
                    step_id=step.step_id,
                ) from exc
        if step.task_kind is not None and step.task_kind not in inventory:
            raise UnknownRecipeTaskKindError(step.task_kind, step_id=step.step_id)


def _collect_dangling(
    recipe: Recipe,
    main_ids: set[str],
    alternative_ids: set[str],
) -> tuple[DanglingReference, ...]:
    """Return every reference that names an undeclared step id.

    Main-graph references may only target main steps; an alternative step may
    consume a main step or a sibling alternative step.  Alternatives are not
    part of the main dispatch graph, so a main edge can never point at one.
    """
    references: list[DanglingReference] = []

    def require(location: str, step_id: str, allowed: set[str]) -> None:
        if step_id not in allowed:
            references.append(DanglingReference(location=location, step_id=step_id))

    for step in recipe.steps:
        for index, output in enumerate(step.consumes):
            require(f"steps[{step.step_id}].consumes[{index}]", output.from_step, main_ids)
        for index, output in enumerate(step.produces):
            require(f"steps[{step.step_id}].produces[{index}]", output.from_step, main_ids)

    for index, edge in enumerate(recipe.edges):
        require(f"edges[{index}].from_step", edge.from_step, main_ids)
        require(f"edges[{index}].to_step", edge.to_step, main_ids)

    alternative_scope = main_ids | alternative_ids
    for index, alternative in enumerate(recipe.alternatives):
        if alternative.replaces_step_id is not None:
            require(
                f"alternatives[{index}].replaces_step_id",
                alternative.replaces_step_id,
                main_ids,
            )
        for step in alternative.steps:
            for output_index, output in enumerate(step.consumes):
                require(
                    f"alternatives[{alternative.alternative_id}].steps[{step.step_id}]"
                    f".consumes[{output_index}]",
                    output.from_step,
                    alternative_scope,
                )
    return tuple(references)


def _dependency_entries(
    recipe: Recipe,
) -> list[tuple[str, str, bool | None, str]]:
    """Return ``(from_step, to_step, ref_required, location)`` for every dependency."""
    entries: list[tuple[str, str, bool | None, str]] = []
    for step in recipe.steps:
        for index, output in enumerate(step.consumes):
            entries.append(
                (
                    output.from_step,
                    step.step_id,
                    output.required,
                    f"steps[{step.step_id}].consumes[{index}]",
                )
            )
    for index, edge in enumerate(recipe.edges):
        ref_required = edge.output_ref.required if edge.output_ref is not None else None
        entries.append((edge.from_step, edge.to_step, ref_required, f"edges[{index}]"))
    return entries


def _build_graph(
    recipe: Recipe,
    entries: Iterable[tuple[str, str, bool | None, str]],
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Build adjacency and indegree over the main step ids."""
    adjacency: dict[str, list[str]] = {step.step_id: [] for step in recipe.steps}
    indegree: dict[str, int] = {step.step_id: 0 for step in recipe.steps}
    for from_step, to_step, _ref_required, _location in entries:
        adjacency[from_step].append(to_step)
        indegree[to_step] += 1
    return adjacency, indegree


def _dependency_rules(
    recipe: Recipe,
    entries: Iterable[tuple[str, str, bool | None, str]],
    by_id: Mapping[str, Step],
) -> tuple[DependencyRule, ...]:
    """Resolve dependency semantics and enforce required/diagnostic evidence."""
    collected: dict[tuple[str, str], DependencyRule] = {}
    for from_step, to_step, ref_required, location in entries:
        upstream = by_id[from_step]
        downstream = by_id[to_step]
        required_evidence = (
            bool(ref_required) if ref_required is not None else downstream.required
        ) and downstream.required
        if required_evidence:
            if upstream.diagnostic_only:
                raise DiagnosticDependencyError(from_step, to_step)
            if not upstream.required:
                raise MissingRequiredStepError(from_step, to_step)
        upstream_required = upstream.required and not upstream.diagnostic_only
        rule = DependencyRule(
            from_step=from_step,
            to_step=to_step,
            required_evidence=required_evidence,
            upstream_required=upstream_required,
            upstream_diagnostic=upstream.diagnostic_only,
            blocks_on_upstream_failure=required_evidence and upstream_required,
        )
        key = (from_step, to_step)
        previous = collected.get(key)
        if previous is None or rule.required_evidence:
            collected[key] = rule
        logger.debug("recipe dependency %s: %s -> %s", location, from_step, to_step)
    return tuple(collected.values())


def _check_limits(recipe: Recipe) -> None:
    """Reject a recipe that exceeds its declared bounds."""
    limits = recipe.limits
    if len(recipe.steps) > limits.max_steps:
        raise RecipeLimitError("max_steps", actual=len(recipe.steps), maximum=limits.max_steps)
    if limits.max_edges is not None and len(recipe.edges) > limits.max_edges:
        raise RecipeLimitError("max_edges", actual=len(recipe.edges), maximum=limits.max_edges)
    if limits.max_alternatives is not None and len(recipe.alternatives) > limits.max_alternatives:
        raise RecipeLimitError(
            "max_alternatives",
            actual=len(recipe.alternatives),
            maximum=limits.max_alternatives,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_recipe(
    recipe: Recipe | Mapping[str, Any],
    *,
    registry: CapabilityRegistry | None = None,
) -> RecipeValidationReport:
    """Validate *recipe* and return a deterministic report.

    Args:
        recipe: A :class:`Recipe` or its canonical mapping payload.
        registry: Optional capability registry; defaults to the built-in
            catalog.  Registration is checked, but nothing is executed.

    Returns:
        The validation report with topological order and dependency rules.

    Raises:
        RecipeSchemaError: The mapping payload is not a canonical v1 recipe.
        RecipeCycleError: The dependency graph contains a cycle.
        DanglingReferenceError: A reference names an undeclared step id.
        DuplicateStepError, DuplicateEdgeError, DuplicateAlternativeError:
            Declared ids collide.
        MissingRequiredStepError: Required evidence comes from a non-required
            upstream.
        DiagnosticDependencyError: Required evidence comes from a diagnostic
            upstream.
        UnknownRecipeCapabilityError: A capability reference is not registered.
        UnknownRecipeTaskKindError: A task kind is outside the inventory.
        RecipeLimitError: A declared size bound is exceeded.
    """
    contract = recipe if isinstance(recipe, Recipe) else Recipe.from_dict(recipe)
    by_id = _unique_step_ids(contract)
    step_ids = tuple(step.step_id for step in contract.steps)

    for step in contract.steps:
        if step.required and step.diagnostic_only:
            raise StepFlagConflictError(
                f"step {step.step_id!r} declares both required and diagnostic_only",
                detail={"step_id": step.step_id},
            )

    active_registry = registry if registry is not None else builtin_registry()
    all_steps = [
        *contract.steps,
        *(step for block in contract.alternatives for step in block.steps),
    ]
    _check_unknown_references(all_steps, active_registry)

    alternative_ids = _alternative_step_ids(contract, set(step_ids))
    dangling = _collect_dangling(contract, set(step_ids), alternative_ids)
    if dangling:
        raise DanglingReferenceError(dangling)

    seen_edges: set[str] = set()
    for edge in contract.edges:
        if edge.edge_id in seen_edges:
            raise DuplicateEdgeError(edge.edge_id)
        seen_edges.add(edge.edge_id)

    entries = _dependency_entries(contract)
    adjacency, indegree = _build_graph(contract, entries)
    order = _topological_order(step_ids, adjacency, indegree)
    rules = _dependency_rules(contract, entries, by_id)
    _check_limits(contract)

    return RecipeValidationReport(
        recipe_id=contract.recipe_id,
        schema_version=contract.schema_version,
        step_ids=step_ids,
        topological_order=order,
        required_step_ids=tuple(step.step_id for step in contract.steps if step.required),
        diagnostic_step_ids=tuple(step.step_id for step in contract.steps if step.diagnostic_only),
        dependency_rules=rules,
    )


def blocked_steps(
    report: RecipeValidationReport,
    failed_step_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return the steps blocked by the given failures.

    A failure blocks a dependent only when the dependency is ``required``
    evidence produced by a required, non-diagnostic upstream.  A diagnostic
    step's failure therefore never blocks a required dependent, and a
    diagnostic step can never be required evidence in the first place
    (``validate_recipe`` rejects that recipe).  The computation is a pure
    fixed-point over the report, not an execution loop.
    """
    failed = set(failed_step_ids)
    blocked: set[str] = set()
    changed = True
    while changed:
        changed = False
        for rule in report.dependency_rules:
            if not rule.blocks_on_upstream_failure:
                continue
            if rule.to_step in failed or rule.to_step in blocked:
                continue
            if rule.from_step in failed or rule.from_step in blocked:
                blocked.add(rule.to_step)
                changed = True
    return tuple(step_id for step_id in report.topological_order if step_id in blocked)
