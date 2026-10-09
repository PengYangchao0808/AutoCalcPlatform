# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false

"""D08 plan-contract guards: cardinality, duplicate kinds, illegal combos.

``validate_plan`` is the single rejection layer for ``CalculationPlan``
input (executor entry + explicit callers).  Multi-item plans and duplicate
step kinds are unsupported — the executor's output directories are
per-kind, so a plan must carry exactly one item and at most one step of
each kind.  These tests pin the exact rejection messages and prove that a
rejected plan never reaches a primitive (spy count 0).
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import acp.calculations.executor as executor_module
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    StepKind,
    StructureArtifact,
    validate_plan,
)
from acp.calculations.executor import CalculationPlanExecutor

CARDINALITY_ERROR = (
    "calculation plans support exactly one input item; "
    "submit multiple structures via BatchOptimize"
)
THERMOCHEMISTRY_ERROR = (
    "thermochemistry requires preceding frequency and singlepoint steps"
)


def _artifact(path: Path) -> StructureArtifact:
    return StructureArtifact(path=path, elements=["C"], source="test")


def _write_input(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nC 0 0 0\n", encoding="utf-8")
    return path


def _plan(
    items: list[StructureArtifact],
    steps: list[CalculationStep],
) -> CalculationPlan:
    return CalculationPlan(
        workflow="test",
        profile="r2SCAN-3c",
        items=items,
        steps=steps,
    )


def _full_dispatch(spy: Mock) -> dict[StepKind, object]:
    return {kind: spy for kind in StepKind}


def _assert_rejected_without_primitives(
    plan: CalculationPlan,
    expected_error: str,
    task_root: Path,
) -> tuple[list[str], int]:
    """validate rejects with the exact message; execute raises; spy count 0."""
    errors = validate_plan(plan)
    assert expected_error in errors, errors

    spy = Mock(return_value=CalculationResult(energy=-1.0))
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _full_dispatch(spy)):
        with pytest.raises(ValueError, match="plan validation failed"):
            CalculationPlanExecutor().execute(plan, task_root)
    assert spy.call_count == 0, "no primitive may run for a rejected plan"
    return errors, spy.call_count


# ── cardinality ─────────────────────────────────────────────────────────


def test_two_item_plan_rejected(tmp_path: Path) -> None:
    """Two items → exact cardinality error; execute raises; spy=0."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path), _artifact(input_path)],
        [CalculationStep(kind=StepKind.SINGLEPOINT)],
    )

    errors, calls = _assert_rejected_without_primitives(
        plan, CARDINALITY_ERROR, tmp_path / "task"
    )

    assert len(errors) == 1
    assert calls == 0


def test_empty_items_plan_rejected(tmp_path: Path) -> None:
    """Zero items is rejected with the same single-item message."""
    plan = _plan([], [CalculationStep(kind=StepKind.SINGLEPOINT)])

    _assert_rejected_without_primitives(plan, CARDINALITY_ERROR, tmp_path / "task")


# ── duplicate step kinds ────────────────────────────────────────────────


def test_duplicate_step_kind_rejected(tmp_path: Path) -> None:
    """Two SINGLEPOINT steps share one per-kind output directory → reject."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path)],
        [
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.SINGLEPOINT),
        ],
    )

    errors, calls = _assert_rejected_without_primitives(
        plan,
        "duplicate step kind 'singlepoint' is unsupported: "
        "output directories are per-kind; split into separate plans",
        tmp_path / "task",
    )

    assert calls == 0


# ── illegal combinations ────────────────────────────────────────────────


def test_thermochemistry_without_prerequisites_rejected(tmp_path: Path) -> None:
    """THERMOCHEMISTRY needs preceding FREQUENCY and SINGLEPOINT steps."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path)],
        [CalculationStep(kind=StepKind.THERMOCHEMISTRY)],
    )

    _assert_rejected_without_primitives(plan, THERMOCHEMISTRY_ERROR, tmp_path / "task")


def test_thermochemistry_missing_singlepoint_rejected(tmp_path: Path) -> None:
    """FREQUENCY alone is not enough — SINGLEPOINT must precede as well."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path)],
        [
            CalculationStep(kind=StepKind.FREQUENCY),
            CalculationStep(kind=StepKind.THERMOCHEMISTRY),
        ],
    )

    _assert_rejected_without_primitives(plan, THERMOCHEMISTRY_ERROR, tmp_path / "task")


# ── legal plans stay legal ──────────────────────────────────────────────


def test_legal_simple_plan_passes(tmp_path: Path) -> None:
    """A legal simple plan (1 item x 1 step) validates clean and executes."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path)],
        [CalculationStep(kind=StepKind.SINGLEPOINT)],
    )

    assert validate_plan(plan) == []

    spy = Mock(return_value=CalculationResult(energy=-1.0))
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _full_dispatch(spy)):
        result = CalculationPlanExecutor().execute(plan, tmp_path / "task")
    assert result.is_completed
    assert spy.call_count == 1


def test_legal_opt_freq_sp_thermo_plan_passes(tmp_path: Path) -> None:
    """1 item x 4 ordered steps (the batch profile shape) validates clean."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan(
        [_artifact(input_path)],
        [
            CalculationStep(kind=StepKind.OPTIMIZE),
            CalculationStep(kind=StepKind.FREQUENCY),
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.THERMOCHEMISTRY),
        ],
    )

    assert validate_plan(plan) == []


def test_existing_irc_rejection_preserved(tmp_path: Path) -> None:
    """The pre-existing unsupported-kind/IRC rejection stays in place."""
    input_path = _write_input(tmp_path / "task")
    plan = _plan([_artifact(input_path)], [{"kind": "irc"}])  # type: ignore[list-item]

    errors = validate_plan(plan)

    assert any("IRC" in error for error in errors), errors
