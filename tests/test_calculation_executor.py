# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false
"""Unit tests for calculation primitives, the shared fake backend, and the executor."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations import result_publication
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationRequest,
    CalculationStep,
    JsonValue,
    StepKind,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.executor import CalculationPlanExecutor, _run_thermochemistry
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.primitives.singlepoint import run_singlepoint
from acp.storage.manifest import ResultManifest
from cccp import calculation as cccp_calculation
from tests.conftest import FakeBackend


def _request(
    tmp_path: Path,
    *,
    role: StructureRole = StructureRole.MINIMUM,
    output_dir: Path | None = None,
) -> CalculationRequest:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    resources: dict[str, JsonValue] = {"output_dir": str(output_dir or tmp_path / "calc")}
    return CalculationRequest(
        input_artifact=StructureArtifact(
            path=input_path,
            elements=["C"],
            role=role,
            source="test",
        ),
        method="r2SCAN-3c",
        resources=resources,
        workflow="test",
        profile="default",
    )


def test_optimize_primitive_happy(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: a successful optimization result from the capability fake.
    output_dir = tmp_path / "opt"
    coordinates = np.array([[0.1, 0.2, 0.3]], dtype=float)
    fake_backend.set_result(
        "optimize",
        QCResult(
            success=True,
            energy=-40.123,
            coordinates=coordinates,
            symbols=["C"],
            converged=True,
            output_file=output_dir / "opt.inp",
            log_file=output_dir / "opt.out",
        ),
    )

    # When: the optimization primitive runs.
    result = run_optimize(_request(tmp_path, output_dir=output_dir))

    # Then: the normalized result keeps geometry, energy, and file references.
    assert result.status == "completed"
    assert result.energy == -40.123
    assert result.coords is not None
    assert np.array_equal(result.coords, coordinates)
    assert {artifact.path for artifact in result.artifacts} == {
        output_dir / "opt.inp",
        output_dir / "opt.out",
    }
    assert fake_backend.calls[0].backend == "orca"
    assert fake_backend.calls[0].method == "optimize"


def test_optimize_rescue_records_errors(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: the first optimization attempt raises and the retry succeeds.
    fake_backend.fail_next("optimize", RuntimeError("geometry did not converge"))

    # When: the optimization primitive executes its rescue chain.
    result = run_optimize(_request(tmp_path))

    # Then: the retry completes and the original failure remains observable.
    assert result.status == "completed"
    assert result.errors
    assert len(fake_backend.calls) == 2
    assert fake_backend.calls[1].kwargs["initial_hessian"] == "calculate"
    assert result.metadata["rescue_failure_type"] == "geometry_not_converged"


def test_singlepoint_primitive_happy(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: a successful single-point result.
    fake_backend.set_result("single_point", energy=-41.5, success=True)

    # When: the single-point primitive runs.
    result = run_singlepoint(_request(tmp_path))

    # Then: the energy is exposed as a completed calculation result.
    assert result.status == "completed"
    assert result.energy == -41.5
    assert fake_backend.calls[0].method == "single_point"


def test_singlepoint_primitive_failure(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: the capability reports a failed single-point calculation.
    fake_backend.set_result(
        "single_point",
        QCResult(success=False, error_message="single-point failed"),
    )

    # When: the single-point primitive runs.
    result = run_singlepoint(_request(tmp_path))

    # Then: failure is represented without hiding the backend diagnostic.
    assert result.status == "failed"
    assert result.errors == ["single-point failed"]


def test_frequency_primitive_happy(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: a successful frequency result.
    fake_backend.set_result(
        "frequency",
        QCResult(success=True, frequencies=[-120.0, 350.0], has_frequencies=True),
    )

    # When: the frequency primitive runs.
    result = run_frequency(_request(tmp_path))

    # Then: frequencies are normalized into the calculation result.
    assert result.status == "completed"
    assert result.frequencies == [-120.0, 350.0]
    assert fake_backend.calls[0].method == "frequency"


def test_frequency_primitive_failure(fake_backend: FakeBackend, tmp_path: Path) -> None:
    # Given: the capability raises a frequency execution error.
    fake_backend.fail_next("frequency", RuntimeError("frequency failed"))

    # When: the frequency primitive runs.
    result = run_frequency(_request(tmp_path))

    # Then: the error is returned in the unified result.
    assert result.status == "failed"
    assert result.errors == ["frequency failed"]


# ── Executor tests ──────────────────────────────────────────────────────


def _make_plan() -> CalculationPlan:
    """Build a 3-step plan: optimize → frequency → singlepoint."""
    return CalculationPlan(
        workflow="test",
        profile="r2SCAN-3c",
        steps=[
            CalculationStep(kind=StepKind.OPTIMIZE),
            CalculationStep(kind=StepKind.FREQUENCY),
            CalculationStep(kind=StepKind.SINGLEPOINT),
        ],
    )


def _make_input_xyz(task_root: Path) -> Path:
    """Write a minimal XYZ input file under *task_root*."""
    path = task_root / "input.xyz"
    path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    return path


def _plan_with_item(task_root: Path) -> CalculationPlan:
    """Return a 3-step plan whose item points to a real XYZ file."""
    input_path = _make_input_xyz(task_root)
    plan = _make_plan()
    # Inject the item after construction (frozen dataclass — rebuild).
    return CalculationPlan(
        workflow=plan.workflow,
        profile=plan.profile,
        items=[
            StructureArtifact(
                path=input_path,
                elements=["C"],
                source="test",
            ),
        ],
        steps=plan.steps,
    )


def test_three_step_plan(fake_backend: FakeBackend, tmp_path: Path) -> None:
    """Happy path: 3-step plan produces dirs, manifest, and checkpoint."""
    # Given: a 3-step plan with a fake backend that succeeds for all steps.
    coordinates = np.array([[0.5, 0.5, 0.5]], dtype=float)
    opt_dir = tmp_path / "WORK" / "03_OPT"
    fake_backend.set_result(
        "optimize",
        QCResult(
            success=True,
            energy=-40.0,
            coordinates=coordinates,
            symbols=["C"],
            converged=True,
            output_file=opt_dir / "opt.out",
            log_file=opt_dir / "opt.log",
        ),
    )
    fake_backend.set_result(
        "frequency",
        QCResult(
            success=True,
            frequencies=[350.0, 1200.0],
            has_frequencies=True,
            output_file=tmp_path / "WORK" / "04_FREQ" / "freq.out",
        ),
    )
    fake_backend.set_result(
        "single_point",
        QCResult(
            success=True,
            energy=-40.5,
            output_file=tmp_path / "WORK" / "05_SP" / "sp.out",
        ),
    )

    plan = _plan_with_item(tmp_path)
    executor = CalculationPlanExecutor()

    # When: the executor runs the plan.
    result = executor.execute(plan, task_root=tmp_path)

    # Then: the execution completed successfully.
    assert result.is_completed
    assert not result.is_failed
    assert len(result.step_states) == 3
    for state in result.step_states:
        assert state.status == "completed", f"step {state.index} failed: {state.error}"

    # And: the §10.3 directory layout was created.
    work_dir = tmp_path / "WORK"
    assert (work_dir / "00_RUNTIME").is_dir()
    assert (work_dir / "03_OPT").is_dir()
    assert (work_dir / "04_FREQ").is_dir()
    assert (work_dir / "05_SP").is_dir()
    assert (tmp_path / "RESULT").is_dir()

    # And: checkpoint exists with all steps completed.
    runtime_dir = work_dir / "00_RUNTIME"
    cp_path = runtime_dir / "checkpoint.json"
    assert cp_path.is_file()
    cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
    assert len(cp_data["step_states"]) == 3
    assert all(s["status"] == "completed" for s in cp_data["step_states"])

    # And: result manifest exists with products.
    manifest_path = tmp_path / "RESULT" / "result_manifest.json"
    assert manifest_path.is_file()
    manifest = ResultManifest.read(tmp_path / "RESULT")
    assert manifest.status == "completed"
    assert manifest.workflow == "test"
    assert len(manifest.products) > 0

    # And: the backend was called for each step.
    assert len(fake_backend.calls) == 3
    assert fake_backend.calls[0].method == "optimize"
    assert fake_backend.calls[1].method == "frequency"
    assert fake_backend.calls[2].method == "single_point"


def test_step2_failure_isolated(fake_backend: FakeBackend, tmp_path: Path) -> None:
    """Step 2 (frequency) raises → step_states[1] failed, step 0 preserved, resume possible."""
    # Given: optimize succeeds, frequency raises, singlepoint succeeds.
    coordinates = np.array([[0.5, 0.5, 0.5]], dtype=float)
    fake_backend.set_result(
        "optimize",
        QCResult(
            success=True,
            energy=-40.0,
            coordinates=coordinates,
            symbols=["C"],
            converged=True,
        ),
    )
    fake_backend.fail_next("frequency", RuntimeError("frequency exploded"))
    fake_backend.set_result("single_point", energy=-40.5, success=True)

    plan = _plan_with_item(tmp_path)
    executor = CalculationPlanExecutor()

    # When: the executor runs the plan.
    result = executor.execute(plan, task_root=tmp_path)

    # Then: the overall result is failed.
    assert result.is_failed
    assert len(result.step_states) == 3

    # And: step 0 (optimize) completed successfully.
    assert result.step_states[0].status == "completed"

    # And: step 1 (frequency) failed with the expected error.
    assert result.step_states[1].status == "failed"
    assert "frequency exploded" in result.step_states[1].error

    # And: step 2 (singlepoint) still ran (failure isolation).
    assert result.step_states[2].status == "completed"

    # And: the checkpoint recorded the failure.
    cp_path = tmp_path / "WORK" / "00_RUNTIME" / "checkpoint.json"
    cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
    assert cp_data["step_states"][1]["status"] == "failed"

    # And: the result manifest reports failed.
    manifest = ResultManifest.read(tmp_path / "RESULT")
    assert manifest.status == "failed"

    # And: the optimize step artifacts are preserved.
    assert (tmp_path / "WORK" / "03_OPT").is_dir()

    # And: resume is possible — re-running skips the completed steps.
    # Re-queue the failure so step 1 fails again on resume.
    fake_backend.fail_next("frequency", RuntimeError("frequency exploded"))
    executor2 = CalculationPlanExecutor()
    result2 = executor2.execute(plan, task_root=tmp_path)
    # Steps 0 and 2 were completed; step 1 still fails.
    assert result2.step_states[0].status == "skipped"
    assert result2.step_states[1].status == "failed"
    assert result2.step_states[2].status == "skipped"


def test_resume_after_interrupt_skips_completed(fake_backend: FakeBackend, tmp_path: Path) -> None:
    """Resume from checkpoint skips steps already marked completed."""
    # Given: all steps succeed.
    coordinates = np.array([[0.5, 0.5, 0.5]], dtype=float)
    fake_backend.set_result(
        "optimize",
        QCResult(
            success=True,
            energy=-40.0,
            coordinates=coordinates,
            symbols=["C"],
            converged=True,
        ),
    )
    fake_backend.set_result(
        "frequency",
        QCResult(success=True, frequencies=[350.0], has_frequencies=True),
    )
    fake_backend.set_result("single_point", energy=-40.5, success=True)

    plan = _plan_with_item(tmp_path)
    executor = CalculationPlanExecutor()

    # When: the plan runs to completion.
    result1 = executor.execute(plan, task_root=tmp_path)
    assert result1.is_completed
    calls_after_first = len(fake_backend.calls)

    # When: the executor runs again (simulating restart).
    result2 = executor.execute(plan, task_root=tmp_path)

    # Then: all steps are skipped (no new backend calls).
    assert result2.is_completed
    for state in result2.step_states:
        assert state.status == "skipped"
    assert len(fake_backend.calls) == calls_after_first  # no new calls


# ── T23 dispatch probe + publication-recovery fault injection ────────────


def _singlepoint_plan(task_root: Path) -> CalculationPlan:
    return CalculationPlan(
        workflow="test",
        profile="r2SCAN-3c",
        items=[
            StructureArtifact(
                path=_make_input_xyz(task_root),
                elements=["C"],
                source="test",
            )
        ],
        steps=[CalculationStep(kind=StepKind.SINGLEPOINT)],
    )


def test_executor_dispatch_reaches_cccp_task(
    fake_backend: FakeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a single-step plan and a spy on the cccp task function.
    fake_backend.set_result("single_point", QCResult(success=True, energy=-40.5))
    calls: list[object] = []
    real_run_singlepoint = cccp_calculation.run_singlepoint

    def spy(task_request: object, **kwargs: object) -> object:
        calls.append(task_request)
        return real_run_singlepoint(task_request, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cccp_calculation, "run_singlepoint", spy)

    # When: the executor runs the plan step.
    result = CalculationPlanExecutor().execute(_singlepoint_plan(tmp_path), task_root=tmp_path)

    # Then: the call path reached cccp.calculation.run_singlepoint exactly once.
    assert result.is_completed
    assert len(calls) == 1, "executor dispatch must reach cccp.calculation.run_singlepoint"


def test_thermochemistry_dispatch_reaches_cccp_task_with_legacy_output_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a spy on the cccp thermochemistry task and a fake Shermo runner.
    freq_log = tmp_path / "frequency.log"
    freq_log.write_text("frequency output", encoding="utf-8")
    calls: list[object] = []
    shermo_kwargs: dict[str, object] = {}
    real_run_thermochemistry = cccp_calculation.run_thermochemistry

    def spy(task_request: object, **kwargs: object) -> object:
        calls.append(task_request)
        return real_run_thermochemistry(task_request, **kwargs)  # type: ignore[arg-type]

    def fake_run_shermo(**kwargs: object) -> dict[str, float]:
        shermo_kwargs.update(kwargs)
        return {"g_sum": -1.2, "h_sum": -1.1, "s_sum": 0.01}

    monkeypatch.setattr(cccp_calculation, "run_thermochemistry", spy)
    monkeypatch.setattr("cccp.qc.shermo_adapter.run_shermo", fake_run_shermo)
    request = CalculationRequest(
        input_artifact=StructureArtifact(
            path=tmp_path / "input.xyz",
            elements=["C"],
            source="test",
        ),
        method="",
        resources={
            "output_dir": str(tmp_path),
            "freq_log_path": str(freq_log),
            "sp_energy_hartree": -10.2,
            "temperature": 298.15,
            "pressure": 1.0,
        },
    )

    # When: the executor thermochemistry dispatch runs.
    result = _run_thermochemistry(request)

    # Then: the call path reached cccp.calculation.run_thermochemistry and the
    # legacy Shermo.sum output filename survived the rewire.
    assert result.status == "completed"
    assert len(calls) == 1, "dispatch must reach cccp.calculation.run_thermochemistry"
    assert shermo_kwargs.get("output_file") == tmp_path / "Shermo.sum"


def test_executor_recovery_retries_publish_only(
    fake_backend: FakeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a single-step plan whose publication fails once after the
    # scientific result is already persisted.
    fake_backend.set_result("single_point", QCResult(success=True, energy=-40.5))
    real_register = result_publication.register_result_manifest
    state = {"failures": 1}

    def flaky_register(result_dir, manifest):  # type: ignore[no-untyped-def]
        if state["failures"]:
            state["failures"] -= 1
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(result_publication, "register_result_manifest", flaky_register)
    plan = _singlepoint_plan(tmp_path)

    # When: the first run hits the injected publish failure.
    result1 = CalculationPlanExecutor().execute(plan, task_root=tmp_path)

    # Then: the step failed but the scientific result survived (contract ①).
    assert result1.is_failed
    step_dir = tmp_path / "WORK" / "05_SP"
    assert result_publication.load_scientific_result(step_dir) is not None
    qc_calls_after_first = len(fake_backend.calls)
    assert qc_calls_after_first == 1

    # When: the executor runs again (recovery).
    result2 = CalculationPlanExecutor().execute(plan, task_root=tmp_path)

    # Then: only publication is retried — QC is not re-invoked (contract:
    # stored scientific result + publish failure → recovery retries publish
    # only, QC count unchanged) and publication completes.
    assert result2.is_completed
    assert len(fake_backend.calls) == qc_calls_after_first
    state_loaded = result_publication.load_publication_state(step_dir)
    assert state_loaded is not None and state_loaded.complete is True
