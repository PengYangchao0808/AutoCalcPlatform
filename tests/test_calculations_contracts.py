# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from acp.calculations import (
    CalculationPlan,
    CalculationStep,
    OptimizationMode,
    OptimizationSpec,
    StepKind,
    StructureArtifact,
    StructureRole,
    validate_plan,
)


def _artifact() -> StructureArtifact:
    return StructureArtifact(
        path=Path("input/ts_001.xyz"),
        elements=["C", "H"],
        role=StructureRole.TRANSITION_STATE,
        source="upload",
        candidate_id="candidate_001",
    )


def test_valid_plan_passes() -> None:
    plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq_sp_thermo",
        items=[_artifact()],
        steps=[
            CalculationStep(
                kind=StepKind.OPTIMIZE,
                mode=OptimizationMode.TRANSITION_STATE,
                spec=OptimizationSpec(
                    trust_radius=0.15,
                    recalc_hess=5,
                    max_cycles=60,
                ),
            ),
            CalculationStep(kind=StepKind.FREQUENCY),
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.THERMOCHEMISTRY),
        ],
    )

    errors = validate_plan(plan)

    assert errors == []


def test_plan_rejects_irc_step() -> None:
    plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_only",
        items=[_artifact()],
        steps=[{"kind": "irc"}],
    )

    errors = validate_plan(plan)

    assert errors
    assert "irc" in errors[0]


def test_invalid_structure_role_raises_value_error() -> None:
    with pytest.raises(ValueError):
        StructureArtifact(
            path=Path("input.xyz"),
            elements=["H"],
            role="not-a-role",
            source="upload",
            candidate_id=None,
        )


def test_invalid_optimization_mode_raises_value_error() -> None:
    with pytest.raises(ValueError):
        CalculationStep(kind=StepKind.OPTIMIZE, mode="not-a-mode")


def test_contracts_are_frozen() -> None:
    artifact = _artifact()

    with pytest.raises(FrozenInstanceError):
        artifact.path = Path("other.xyz")


def test_step_kind_excludes_irc() -> None:
    assert "irc" not in {kind.value for kind in StepKind}
