"""D07 — step prerequisite table and evaluation (no general DAG engine).

A fixed :class:`StepRequirement` table keyed by :class:`StepKind`:

* ``FREQUENCY`` / ``SINGLEPOINT`` require a valid geometry — the last
  upstream coord-producing step (``OPTIMIZE``) completed with coords whose
  row count matches the item symbols; a plan with no upstream producer uses
  the plan item directly;
* ``THERMOCHEMISTRY`` requires a completed ``FREQUENCY`` whose result carries
  an existing freq-log artifact plus a completed ``SINGLEPOINT`` with a
  non-empty energy;
* every other kind has no prerequisite.

Evaluation is sequential over the ordered plan (``validate_plan`` already
pins the legal orderings) — this is intentionally NOT a general DAG or
condition-expression engine.  Reasons are exactly ``upstream_failed`` (a
required upstream step failed or is blocked) or ``missing_requirement`` (the
upstream completed without the required artifact/geometry, or its result was
produced under the diagnostics purpose).

A result produced under the ``diagnostics`` policy carries
``metadata["diagnostic_only"]=True``; such a result never satisfies a normal
prerequisite (consumers re-run as diagnostics or block, always marked).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from acp.calculations.contracts import CalculationResult, StepKind

__all__ = [
    "COORD_PRODUCING_KINDS",
    "MISSING_REQUIREMENT",
    "UPSTREAM_FAILED",
    "PriorStep",
    "RequirementOutcome",
    "SATISFIED",
    "STEP_REQUIREMENTS",
    "StepRequirement",
    "evaluate_prerequisite",
    "is_diagnostic_result",
    "payload_is_diagnostic",
]

#: Why a step became ``blocked`` — the only two accepted values.
UPSTREAM_FAILED = "upstream_failed"
MISSING_REQUIREMENT = "missing_requirement"

#: Step kinds whose results feed coordinates into downstream steps
#: (mirrors ``executor._COORD_PRODUCING_KINDS``).
COORD_PRODUCING_KINDS: frozenset[StepKind] = frozenset({StepKind.OPTIMIZE})

#: Artifact types that count as a frequency log for THERMOCHEMISTRY.
_FREQ_LOG_TYPES: frozenset[str] = frozenset({"frequency_log", "log"})


@dataclass(frozen=True, slots=True)
class StepRequirement:
    """Prerequisites of one step kind (``requires_geometry`` XOR/plus kinds)."""

    kind: StepKind
    requires_geometry: bool = False
    required_upstream: tuple[StepKind, ...] = ()


STEP_REQUIREMENTS: Mapping[StepKind, StepRequirement] = {
    StepKind.FREQUENCY: StepRequirement(StepKind.FREQUENCY, requires_geometry=True),
    StepKind.SINGLEPOINT: StepRequirement(StepKind.SINGLEPOINT, requires_geometry=True),
    StepKind.THERMOCHEMISTRY: StepRequirement(
        StepKind.THERMOCHEMISTRY,
        required_upstream=(StepKind.FREQUENCY, StepKind.SINGLEPOINT),
    ),
}


@dataclass(frozen=True, slots=True)
class RequirementOutcome:
    """Whether a prerequisite is satisfied; ``reason`` is ``""`` when it is."""

    satisfied: bool
    reason: str = ""


SATISFIED = RequirementOutcome(satisfied=True)


@dataclass(frozen=True, slots=True)
class PriorStep:
    """One already-processed upstream step as seen by the evaluator."""

    kind: StepKind
    status: str
    result: CalculationResult | None = None


def is_diagnostic_result(result: CalculationResult | None) -> bool:
    """``True`` when *result* was produced under the diagnostics purpose."""
    if result is None:
        return False
    return result.metadata.get("diagnostic_only") is True


def payload_is_diagnostic(payload: Mapping[str, object]) -> bool:
    """``True`` when a persisted ``step_result.json`` records the purpose."""
    if payload.get("diagnostic_only") is True:
        return True
    metadata = payload.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get("diagnostic_only") is True


def _has_freq_log(result: CalculationResult) -> bool:
    for artifact in result.artifacts:
        if artifact.type not in _FREQ_LOG_TYPES:
            continue
        try:
            if Path(artifact.path).is_file():
                return True
        except OSError:
            continue
    return False


def _geometry_outcome(
    prior: Sequence[PriorStep],
    item_symbols: Sequence[str],
) -> RequirementOutcome:
    coord_steps = [entry for entry in prior if entry.kind in COORD_PRODUCING_KINDS]
    if not coord_steps:
        return SATISFIED
    producer = coord_steps[-1]
    if producer.status in ("failed", "blocked"):
        return RequirementOutcome(satisfied=False, reason=UPSTREAM_FAILED)
    if producer.status != "completed" or producer.result is None:
        return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
    if is_diagnostic_result(producer.result):
        return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
    coords = producer.result.coords
    if coords is None:
        return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
    if item_symbols and len(coords) != len(item_symbols):
        return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
    return SATISFIED


def _upstream_outcome(
    prior: Sequence[PriorStep],
    required: tuple[StepKind, ...],
) -> RequirementOutcome:
    for needed in required:
        candidates = [entry for entry in prior if entry.kind == needed]
        if not candidates:
            return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
        upstream = candidates[-1]
        if upstream.status in ("failed", "blocked"):
            return RequirementOutcome(satisfied=False, reason=UPSTREAM_FAILED)
        if upstream.status != "completed" or upstream.result is None:
            return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
        if is_diagnostic_result(upstream.result):
            return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
        if needed is StepKind.FREQUENCY and not _has_freq_log(upstream.result):
            return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
        if needed is StepKind.SINGLEPOINT and upstream.result.energy is None:
            return RequirementOutcome(satisfied=False, reason=MISSING_REQUIREMENT)
    return SATISFIED


def evaluate_prerequisite(
    prior: Sequence[PriorStep],
    kind: StepKind,
    item_symbols: Sequence[str],
) -> RequirementOutcome:
    """Evaluate the prerequisite of step *kind* against its upstream steps."""
    requirement = STEP_REQUIREMENTS.get(kind)
    if requirement is None:
        return SATISFIED
    if requirement.requires_geometry:
        return _geometry_outcome(prior, item_symbols)
    if requirement.required_upstream:
        return _upstream_outcome(prior, requirement.required_upstream)
    return SATISFIED
