# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

from collections.abc import Mapping
from pathlib import Path

import pytest

from acp.calculations.contracts import (
    CalculationRequest,
    CalculationStep,
    OptimizationMode,
    StepKind,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.plans import (
    IrcRequest,
    build_batch_plan,
    build_irc_request,
    build_simple_plan,
)


def _artifact(role: StructureRole = StructureRole.TRANSITION_STATE) -> StructureArtifact:
    return StructureArtifact(
        path=Path("input/ts_001.xyz"),
        elements=["C", "H"],
        role=role,
        source="upload",
        candidate_id="candidate_001",
    )


def _ts_item() -> Mapping[str, str]:
    return {
        "id": "candidate_001",
        "role": StructureRole.TRANSITION_STATE.value,
        "geometry": "input/ts_001.xyz",
    }


@pytest.mark.parametrize(
    ("profile", "expected_kinds"),
    [
        ("opt_only", (StepKind.OPTIMIZE,)),
        ("opt_freq", (StepKind.OPTIMIZE, StepKind.FREQUENCY)),
        (
            "opt_freq_sp",
            (StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT),
        ),
        (
            "opt_freq_sp_thermo",
            (
                StepKind.OPTIMIZE,
                StepKind.FREQUENCY,
                StepKind.SINGLEPOINT,
                StepKind.THERMOCHEMISTRY,
            ),
        ),
    ],
)
def test_batch_plan_profile_steps(profile: str, expected_kinds: tuple[StepKind, ...]) -> None:
    # Given: a transition-state batch item and one supported profile.
    item = _ts_item()

    # When: the batch calculation plan is built.
    plan = build_batch_plan([item], profile)

    # Then: the profile expands to ordered steps and marks optimization as TS.
    assert plan.workflow == "BatchOptimize"
    assert plan.profile == profile
    assert plan.items == [item]
    assert all(isinstance(step, CalculationStep) for step in plan.steps)
    steps = tuple(step for step in plan.steps if isinstance(step, CalculationStep))
    assert tuple(step.kind for step in steps) == expected_kinds
    assert steps[0].mode is OptimizationMode.TRANSITION_STATE
    assert steps[1:] == tuple(CalculationStep(kind=kind) for kind in expected_kinds[1:])


def test_simple_plan_contains_one_requested_step() -> None:
    # Given: a simple-workflow request for one minimum structure.
    artifact = _artifact(StructureRole.MINIMUM)
    request = CalculationRequest(
        input_artifact=artifact,
        method="r2SCAN-3c",
        workflow="optimize",
        profile="default",
    )

    # When: the simple plan is built from the requested operation.
    plan = build_simple_plan(StepKind.OPTIMIZE, request)

    # Then: the plan keeps the request identity and contains one step only.
    assert plan.workflow == "optimize"
    assert plan.profile == "default"
    assert plan.items == [artifact]
    assert plan.steps == [CalculationStep(kind=StepKind.OPTIMIZE)]


def test_unknown_profile_raises() -> None:
    # Given: a batch item and a misspelled profile.
    item = _ts_item()

    # When / Then: plan construction rejects the unknown profile.
    with pytest.raises(ValueError, match="unknown batch profile"):
        build_batch_plan([item], "opt_freq_sp_thremo")


def test_empty_batch_items_raise() -> None:
    # Given: no structures to calculate.
    # When / Then: plan construction fails before producing an empty plan.
    with pytest.raises(ValueError, match="at least one item"):
        build_batch_plan([], "opt_only")


def test_irc_request_is_not_a_calculation_plan() -> None:
    # Given: a transition-state artifact and both IRC directions.
    artifact = _artifact()

    # When: the independent IRC request is built.
    request = build_irc_request(artifact, ["forward", "reverse"])

    # Then: it has the IRC request shape, not a calculation-plan step list.
    assert isinstance(request, IrcRequest)
    assert not hasattr(request, "steps")
    assert request.workflow == "irc"
    assert request.input_artifact is artifact
    assert request.input_role is StructureRole.TRANSITION_STATE
    assert request.directions == ("forward", "reverse")
