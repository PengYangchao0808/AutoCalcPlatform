# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false

"""D07 — step prerequisites, blocked propagation, and explicit diagnostics policy.

``CalculationPlanExecutor`` evaluates a fixed ``StepRequirement`` table before
every step (on every run, resume included):

* FREQUENCY / SINGLEPOINT require a valid geometry — an upstream
  coord-producing step completed with coords matching the item symbols; a
  plan with no upstream producer uses the item directly;
* THERMOCHEMISTRY requires a completed FREQUENCY with a real freq-log
  artifact plus a completed SINGLEPOINT with a non-empty energy.

Unmet prerequisite + default ``block`` policy → ``status="blocked"`` with
``blocked_reason`` in ``{"upstream_failed", "missing_requirement"}``; the
primitive is never invoked (spy 0) and dependents block transitively while
independent steps still execute.  The explicit ``diagnostics`` policy lets the
step run, but the result and every manifest product carry
``metadata["diagnostic_only"]=True``, the purpose is persisted into
``step_result.json``, and a resume under the default policy re-checks the
prerequisites and never adopts a diagnostic result as a normal one.

Overall status is ``failed`` whenever any step failed OR any required step is
blocked — ``errors`` keeps only the original failure, ``blocked_reasons``
reports the blocked entries separately.  This is a step-level status word
only; no global ``JobStatus`` is introduced.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import acp.calculations.executor as executor_module
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    ExecutionPolicy,
    StepKind,
    StructureArtifact,
    validate_plan,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.storage.manifest import ResultManifest


def _write_input(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nC 0 0 0\n", encoding="utf-8")
    return path


def _plan(
    root: Path,
    steps: list[StepKind],
    *,
    policy: ExecutionPolicy | object | None = None,
) -> CalculationPlan:
    return CalculationPlan(
        workflow="test",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=_write_input(root), elements=["C"], source="test")],
        steps=[CalculationStep(kind=kind) for kind in steps],
        execution_policy=policy,  # type: ignore[arg-type]
    )


def _ok_opt(_request: object) -> CalculationResult:
    return CalculationResult(
        energy=-40.0,
        coords=[[0.0, 0.0, 0.0]],
        metadata={"optimization_status": "converged"},
    )


def _failed_opt(_request: object) -> CalculationResult:
    return CalculationResult(
        status="failed",
        errors=["opt did not converge"],
        coords=[[9.0, 9.0, 9.0]],
    )


# ── default (block) policy: failed upstream blocks dependents ───────────


def test_failed_opt_blocks_dependents_probe_reversed(tmp_path: Path) -> None:
    """Probe dependencies reversed: [failed, blocked, blocked], freq spy 0."""
    task_root = tmp_path / "task"
    plan = _plan(task_root, [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT])
    freq_requests: list[object] = []
    freq_spy = Mock(
        side_effect=lambda req: freq_requests.append(req.resources.get("coordinates"))
        or CalculationResult(frequencies=[100.0])
    )
    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))

    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        {
            StepKind.OPTIMIZE: _failed_opt,
            StepKind.FREQUENCY: freq_spy,
            StepKind.SINGLEPOINT: sp_spy,
        },
    ):
        result = CalculationPlanExecutor().execute(plan, task_root)

    # Then: states are failed → blocked → blocked; no downstream primitive ran.
    assert [state.status for state in result.step_states] == ["failed", "blocked", "blocked"]
    assert freq_spy.call_count == 0, "FREQ must not be invoked after a failed OPT"
    assert sp_spy.call_count == 0, "SP must not be invoked after a failed OPT"
    assert freq_requests == [], "the reversed probe scenario must produce no freq requests"
    assert result.blocked_reasons == [
        {"index": 1, "reason": "upstream_failed"},
        {"index": 2, "reason": "upstream_failed"},
    ]

    # And: overall failed with the ORIGINAL failure preserved — blocked steps
    # never fold into ``errors``.
    assert result.status == "failed"
    assert result.is_failed and not result.is_completed
    assert len(result.errors) == 1
    assert "opt did not converge" in result.errors[0]
    assert all("blocked" not in entry for entry in result.errors)

    # And: the checkpoint records the blocked reason, the manifest reports it.
    checkpoint = json.loads(
        (task_root / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert [entry["status"] for entry in checkpoint["step_states"]] == [
        "failed",
        "blocked",
        "blocked",
    ]
    assert checkpoint["step_states"][1]["blocked_reason"] == "upstream_failed"
    manifest = ResultManifest.read(task_root / "RESULT")
    assert manifest.status == "failed"
    blocked_products = [p for p in manifest.products if p.id.endswith("_blocked")]
    assert {p.id for p in blocked_products} == {
        "step_1_frequency_blocked",
        "step_2_singlepoint_blocked",
    }
    assert all(p.metadata.get("stage_status") == "blocked" for p in blocked_products)
    assert all(p.metadata.get("reusable") is False for p in blocked_products)


def test_only_blocked_is_not_completed(tmp_path: Path) -> None:
    """No failed step, only a blocked one → overall ``failed`` + reasons."""
    task_root = tmp_path / "task"
    plan = _plan(
        task_root,
        [
            StepKind.OPTIMIZE,
            StepKind.FREQUENCY,
            StepKind.SINGLEPOINT,
            StepKind.THERMOCHEMISTRY,
        ],
    )
    # FREQ completes WITHOUT any freq-log artifact → THERMO's prerequisite
    # (valid freq log) is missing even though every step "succeeded".
    thermo_spy = Mock(return_value=CalculationResult(energy=-3.0))
    dispatch = {
        StepKind.OPTIMIZE: _ok_opt,
        StepKind.FREQUENCY: Mock(return_value=CalculationResult(frequencies=[100.0])),
        StepKind.SINGLEPOINT: Mock(return_value=CalculationResult(energy=-1.0)),
        StepKind.THERMOCHEMISTRY: thermo_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = CalculationPlanExecutor().execute(plan, task_root)

    assert [state.status for state in result.step_states] == [
        "completed",
        "completed",
        "completed",
        "blocked",
    ]
    assert thermo_spy.call_count == 0
    assert result.status == "failed", "blocked-only runs must never be completed"
    assert not result.is_completed
    assert result.errors == [], "blocked steps must not pollute the failure list"
    assert result.blocked_reasons == [
        {"index": 3, "reason": "missing_requirement"},
    ]
    manifest = ResultManifest.read(task_root / "RESULT")
    assert manifest.status == "failed"


def test_independent_steps_still_execute(tmp_path: Path) -> None:
    """A step with no dependency on the failed one still runs."""
    task_root = tmp_path / "task"
    plan = _plan(
        task_root,
        [
            StepKind.SINGLEPOINT,
            StepKind.OPTIMIZE,
            StepKind.FREQUENCY,
            StepKind.SCAN,
        ],
    )
    scan_spy = Mock(return_value=CalculationResult(energy=-2.0))
    dispatch = {
        StepKind.SINGLEPOINT: Mock(return_value=CalculationResult(energy=-1.0)),
        StepKind.OPTIMIZE: _failed_opt,
        StepKind.FREQUENCY: Mock(return_value=CalculationResult(frequencies=[100.0])),
        StepKind.SCAN: scan_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = CalculationPlanExecutor().execute(plan, task_root)

    assert [state.status for state in result.step_states] == [
        "completed",  # SP: no upstream producer → uses the item
        "failed",  # OPT
        "blocked",  # FREQ depends on the failed OPT
        "completed",  # SCAN: no prerequisite on OPT → still executes
    ]
    assert scan_spy.call_count == 1
    assert result.status == "failed"
    assert "opt did not converge" in result.errors[0]


def test_thermochemistry_missing_freq_log_blocked(tmp_path: Path) -> None:
    """THERMOCHEMISTRY without a real freq-log artifact is blocked, not failed."""
    task_root = tmp_path / "task"
    plan = _plan(
        task_root,
        [StepKind.FREQUENCY, StepKind.SINGLEPOINT, StepKind.THERMOCHEMISTRY],
    )
    thermo_spy = Mock(return_value=CalculationResult(energy=-3.0))
    dispatch = {
        StepKind.FREQUENCY: Mock(return_value=CalculationResult(frequencies=[100.0])),
        StepKind.SINGLEPOINT: Mock(return_value=CalculationResult(energy=-1.0)),
        StepKind.THERMOCHEMISTRY: thermo_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = CalculationPlanExecutor().execute(plan, task_root)

    assert [state.status for state in result.step_states] == [
        "completed",
        "completed",
        "blocked",
    ]
    assert thermo_spy.call_count == 0
    assert result.step_states[2].blocked_reason == "missing_requirement"
    assert result.status == "failed"
    assert result.errors == []
    assert result.blocked_reasons == [
        {"index": 2, "reason": "missing_requirement"},
    ]


def test_completed_freq_log_satisfies_thermochemistry(tmp_path: Path) -> None:
    """Control: a real freq-log artifact + SP energy unblocks THERMOCHEMISTRY."""
    task_root = tmp_path / "task"
    plan = _plan(
        task_root,
        [StepKind.FREQUENCY, StepKind.SINGLEPOINT, StepKind.THERMOCHEMISTRY],
    )
    freq_log = task_root / "WORK" / "04_FREQ" / "freq.out"

    def freq(_request: object) -> CalculationResult:
        freq_log.parent.mkdir(parents=True, exist_ok=True)
        freq_log.write_text("frequency log\n", encoding="utf-8")
        return CalculationResult(
            frequencies=[100.0],
            artifacts=[ArtifactRef(path=freq_log, type="log", source="test")],
        )

    thermo_spy = Mock(return_value=CalculationResult(energy=-3.0))
    dispatch = {
        StepKind.FREQUENCY: freq,
        StepKind.SINGLEPOINT: Mock(return_value=CalculationResult(energy=-1.0)),
        StepKind.THERMOCHEMISTRY: thermo_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = CalculationPlanExecutor().execute(plan, task_root)

    assert result.status == "completed", result.errors
    assert thermo_spy.call_count == 1


# ── explicit diagnostics policy ─────────────────────────────────────────


def test_diagnostics_policy_marks_results_and_products(tmp_path: Path) -> None:
    """Under ``diagnostics`` the later steps run, always marked diagnostic."""
    task_root = tmp_path / "task"
    plan = _plan(
        task_root,
        [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT],
        policy=ExecutionPolicy(upstream_failure="diagnostics"),
    )
    freq_log = task_root / "WORK" / "04_FREQ" / "freq.out"

    def freq(_request: object) -> CalculationResult:
        freq_log.parent.mkdir(parents=True, exist_ok=True)
        freq_log.write_text("frequency log\n", encoding="utf-8")
        return CalculationResult(
            frequencies=[100.0],
            artifacts=[ArtifactRef(path=freq_log, type="log", source="test")],
        )

    freq_spy = Mock(side_effect=freq)
    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))
    dispatch = {
        StepKind.OPTIMIZE: _failed_opt,
        StepKind.FREQUENCY: freq_spy,
        StepKind.SINGLEPOINT: sp_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = CalculationPlanExecutor().execute(plan, task_root)

    # The downstream steps DID run under diagnostics…
    assert [state.status for state in result.step_states] == [
        "failed",
        "completed",
        "completed",
    ]
    assert freq_spy.call_count == 1
    assert sp_spy.call_count == 1
    assert result.blocked_reasons == []
    assert result.status == "failed"
    assert "opt did not converge" in result.errors[0]

    # …and every downstream result carries the diagnostic purpose.
    assert result.step_states[1].result is not None
    assert result.step_states[1].result.metadata["diagnostic_only"] is True
    assert result.step_states[2].result is not None
    assert result.step_states[2].result.metadata["diagnostic_only"] is True

    # The purpose is persisted into step_result.json (resume re-judges it).
    payload = json.loads(
        (task_root / "WORK" / "04_FREQ" / "step_result.json").read_text(encoding="utf-8")
    )
    assert payload.get("diagnostic_only") is True
    assert payload["metadata"]["diagnostic_only"] is True

    # Manifest products are marked and never reusable.
    manifest = ResultManifest.read(task_root / "RESULT")
    marked = [
        p
        for p in manifest.products
        if p.id.startswith(("step_1_frequency", "step_2_singlepoint"))
    ]
    assert marked, "diagnostic steps must publish marked (report) products"
    assert all(p.metadata.get("diagnostic_only") is True for p in marked)
    assert all("auto_reusable" not in p.metadata for p in manifest.products)


def test_diagnostics_reverted_policy_not_reused(tmp_path: Path) -> None:
    """Diagnostics run → revert to default → resume re-checks, never adopts."""
    task_root = tmp_path / "task"
    steps = [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT]
    opt_behavior = {"fail": True}
    freq_calls: list[int] = []

    def opt(_request: object) -> CalculationResult:
        if opt_behavior["fail"]:
            return CalculationResult(status="failed", errors=["opt failed"])
        return CalculationResult(energy=-40.0, coords=[[0.0, 0.0, 0.0]])

    def freq(_request: object) -> CalculationResult:
        freq_calls.append(1)
        return CalculationResult(frequencies=[100.0])

    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))
    dispatch = {
        StepKind.OPTIMIZE: opt,
        StepKind.FREQUENCY: freq,
        StepKind.SINGLEPOINT: sp_spy,
    }

    # Run 1: diagnostics policy → downstream runs, marked diagnostic.
    diag_plan = _plan(task_root, steps, policy=ExecutionPolicy(upstream_failure="diagnostics"))
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        first = CalculationPlanExecutor().execute(diag_plan, task_root)
    assert [state.status for state in first.step_states] == [
        "failed",
        "completed",
        "completed",
    ]
    assert first.step_states[1].result is not None
    assert first.step_states[1].result.metadata["diagnostic_only"] is True
    assert freq_calls == [1]
    persisted = json.loads(
        (task_root / "WORK" / "04_FREQ" / "step_result.json").read_text(encoding="utf-8")
    )
    assert persisted.get("diagnostic_only") is True

    # Run 2: policy reverted to the default (block) → prerequisites re-checked
    # and the diagnostic results are NOT adopted as normal completions.
    default_plan = _plan(task_root, steps)
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        second = CalculationPlanExecutor().execute(default_plan, task_root)
    assert [state.status for state in second.step_states] == [
        "failed",
        "blocked",
        "blocked",
    ]
    assert second.blocked_reasons == [
        {"index": 1, "reason": "upstream_failed"},
        {"index": 2, "reason": "upstream_failed"},
    ]
    assert freq_calls == [1], "a diagnostic result must not be adopted as completed"
    assert second.status == "failed"
    assert "opt failed" in second.errors[0]

    # Run 3: the upstream now succeeds → the prerequisite is met, but the
    # persisted diagnostic purpose still refuses adoption (recompute instead).
    opt_behavior["fail"] = False
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        third = CalculationPlanExecutor().execute(default_plan, task_root)
    assert [state.status for state in third.step_states] == [
        "completed",
        "completed",
        "completed",
    ]
    assert third.status == "completed", third.errors
    assert freq_calls == [1, 1], "the diagnostic step_result must be recomputed, not reused"
    assert third.step_states[1].result is not None
    assert third.step_states[1].result.metadata.get("diagnostic_only") is None


# ── batch parity ────────────────────────────────────────────────────────


def test_batch_parity_opt_failure_stops_later_steps(tmp_path: Path) -> None:
    """Same "opt failed" scenario: batch stops, executor blocks — parity."""
    from acp.calculations.batch._items import BatchStructureItem
    from acp.calculations.batch.engine import BatchOptimizeEngine

    # Executor half: default policy blocks FREQ/SP without invoking them.
    task_root = tmp_path / "executor_task"
    plan = _plan(task_root, [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT])
    freq_spy = Mock(return_value=CalculationResult(frequencies=[100.0]))
    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))
    with patch.dict(
        executor_module._PRIMITIVE_DISPATCH,
        {
            StepKind.OPTIMIZE: _failed_opt,
            StepKind.FREQUENCY: freq_spy,
            StepKind.SINGLEPOINT: sp_spy,
        },
    ):
        executor_result = CalculationPlanExecutor().execute(plan, task_root)
    assert [state.status for state in executor_result.step_states] == [
        "failed",
        "blocked",
        "blocked",
    ]

    # Batch half: the engine raises on the failed OPT and never reaches the
    # later profile steps.
    batch_root = tmp_path / "batch_task"
    items = [
        BatchStructureItem(
            item_id="int_001",
            name="INT",
            tag="INT",
            xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
            candidate_id="int_001",
        )
    ]
    engine = BatchOptimizeEngine(
        work_root=batch_root / "WORK",
        result_root=batch_root / "RESULT",
    )
    with patch(
        "acp.calculations.batch.engine.run_optimize",
        return_value=CalculationResult(status="failed", errors=["opt did not converge"]),
    ), patch("acp.calculations.batch.engine.run_frequency") as batch_freq, patch(
        "acp.calculations.batch.engine.run_singlepoint"
    ) as batch_sp:
        outcome = engine.run(items, profile="opt_freq_sp", charge=0)

    assert [item.status for item in outcome.items] == ["failed"]
    assert batch_freq.call_count == 0
    assert batch_sp.call_count == 0

    # Parity: neither engine invoked a later step after the failed OPT.
    assert freq_spy.call_count == batch_freq.call_count == 0
    assert sp_spy.call_count == batch_sp.call_count == 0


# ── plan contract: execution policy enum ────────────────────────────────


def test_validate_plan_execution_policy_enum(tmp_path: Path) -> None:
    """``validate_plan`` rejects an invalid upstream_failure enum value."""
    good = _plan(tmp_path / "good", [StepKind.SINGLEPOINT], policy=ExecutionPolicy())
    assert validate_plan(good) == []

    diagnostics = _plan(
        tmp_path / "diag",
        [StepKind.SINGLEPOINT],
        policy=ExecutionPolicy(upstream_failure="diagnostics"),
    )
    assert validate_plan(diagnostics) == []

    bad = _plan(
        tmp_path / "bad",
        [StepKind.SINGLEPOINT],
        policy={"upstream_failure": "never"},
    )
    errors = validate_plan(bad)
    assert errors, "an invalid execution policy must fail validation"
    assert any("upstream_failure" in error for error in errors), errors

    with pytest.raises(ValueError):
        ExecutionPolicy(upstream_failure="never")
