# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false
"""Unit tests for calculation primitives, the shared fake backend, and the executor."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

import acp.calculations.executor as executor_module
from acp.backends.base import QCResult
from acp.calculations import result_publication
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationPlan,
    CalculationRequest,
    CalculationResult,
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


def test_step2_failure_isolated(tmp_path: Path) -> None:
    """D07 default policy: a failed OPT blocks FREQ/SP (primitives never run).

    Failure isolation now means: the original OPT failure is preserved in
    ``errors`` while its dependents become ``blocked`` (not silently executed
    on invalid geometry), and a resume re-attempts only the failed step.
    """
    # Given: optimize fails; frequency/singlepoint must never be invoked.
    plan = _plan_with_item(tmp_path)
    executor = CalculationPlanExecutor()
    opt_calls: list[object] = []
    freq_spy = Mock(return_value=CalculationResult(frequencies=[350.0]))
    sp_spy = Mock(return_value=CalculationResult(energy=-40.5))

    def failing_opt(request: object) -> CalculationResult:
        opt_calls.append(request)
        return CalculationResult(status="failed", errors=["optimize exploded"])

    dispatch = {
        StepKind.OPTIMIZE: failing_opt,
        StepKind.FREQUENCY: freq_spy,
        StepKind.SINGLEPOINT: sp_spy,
    }

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        # When: the executor runs the plan.
        result = executor.execute(plan, task_root=tmp_path)

        # Then: the overall result is failed with the original OPT error.
        assert result.is_failed
        assert len(result.step_states) == 3
        assert [state.status for state in result.step_states] == [
            "failed",
            "blocked",
            "blocked",
        ]
        assert "optimize exploded" in result.errors[0]
        assert result.blocked_reasons == [
            {"index": 1, "reason": "upstream_failed"},
            {"index": 2, "reason": "upstream_failed"},
        ]

        # And: the dependents' primitives were never invoked (spy = 0).
        assert freq_spy.call_count == 0
        assert sp_spy.call_count == 0

        # And: the checkpoint records the failure and the blocked reasons.
        cp_path = tmp_path / "WORK" / "00_RUNTIME" / "checkpoint.json"
        cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
        assert cp_data["step_states"][1]["status"] == "blocked"
        assert cp_data["step_states"][1]["blocked_reason"] == "upstream_failed"

        # And: the result manifest reports failed.
        manifest = ResultManifest.read(tmp_path / "RESULT")
        assert manifest.status == "failed"

        # And: the optimize step artifacts are preserved.
        assert (tmp_path / "WORK" / "03_OPT").is_dir()

        # And: resume re-attempts the failed OPT only; FREQ/SP stay blocked
        # and their primitives are still not invoked.
        result2 = CalculationPlanExecutor().execute(plan, task_root=tmp_path)
        assert result2.step_states[0].status == "failed"
        assert result2.step_states[1].status == "blocked"
        assert result2.step_states[2].status == "blocked"
        assert len(opt_calls) == 2
        assert freq_spy.call_count == 0
        assert sp_spy.call_count == 0
        cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
        assert cp_data["step_states"][0]["status"] == "failed"
        assert cp_data["step_states"][1]["status"] == "blocked"


def test_resume_after_interrupt_skips_completed(fake_backend: FakeBackend, tmp_path: Path) -> None:
    """Resume from checkpoint skips execution of steps already completed."""
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

    # Then: all steps keep completed status without re-execution.
    assert result2.is_completed
    for state in result2.step_states:
        assert state.status == "completed"
        assert state.executed_this_run is False
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


# ── CASSCF completion gating + conservative recovery (T10/D5) ──────────────

_CASSCF_STEP_SPEC: dict[str, JsonValue] = {
    "method": "casscf",
    "backend": "orca",
    "casscf": {"active_electrons": 2, "active_orbitals": 2},
}

_CASSCF_CONVERGED_METADATA: dict[str, JsonValue] = {
    "casscf": {
        "casscf_energy_hartree": -109.0,
        "natural_occupations": [1.9, 0.1],
        "nevpt2_roots": [],
        "converged": True,
    }
}

_CASSCF_NOT_CONVERGED_METADATA: dict[str, JsonValue] = {
    "casscf": {
        "casscf_energy_hartree": -109.0,
        "natural_occupations": [1.6, 0.4],
        "nevpt2_roots": [],
        "converged": False,
    }
}


def _casscf_plan(task_root: Path, *, with_dependent: bool = False) -> CalculationPlan:
    """A CAS plan (optionally followed by a dependent singlepoint step)."""
    task_root.mkdir(parents=True, exist_ok=True)
    steps = [CalculationStep(kind=StepKind.CASSCF, spec=dict(_CASSCF_STEP_SPEC))]
    if with_dependent:
        steps.append(CalculationStep(kind=StepKind.SINGLEPOINT, spec={"method": "r2SCAN-3c"}))
    return CalculationPlan(
        workflow="casscf",
        profile="default",
        items=[
            StructureArtifact(path=_make_input_xyz(task_root), elements=["C"], source="test"),
        ],
        steps=steps,
    )


def _casscf_recompute_dispatch(calls: dict[str, int]) -> dict[StepKind, object]:
    def recompute(request: object) -> CalculationResult:
        calls["casscf"] = calls.get("casscf", 0) + 1
        return CalculationResult(
            energy=-108.5,
            metadata={"multireference": {"converged": True}},
        )

    return {StepKind.CASSCF: recompute}


def test_casscf_not_converged_fails_and_blocks_dependents(
    fake_backend: FakeBackend,
    tmp_path: Path,
) -> None:
    """(b) backend success + parsed converged=false → failed + blocked + not completed."""
    fake_backend.set_result(
        "casscf",
        QCResult(
            success=True,
            energy=-109.14691549,
            symbols=["C"],
            converged=False,
            metadata=dict(_CASSCF_NOT_CONVERGED_METADATA),
        ),
    )
    task_root = tmp_path / "task"
    plan = _casscf_plan(task_root, with_dependent=True)
    sp_spy = Mock(return_value=CalculationResult(energy=-40.5))

    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {StepKind.SINGLEPOINT: sp_spy}):
        result = CalculationPlanExecutor().execute(plan, task_root=task_root)

    assert result.is_failed
    assert [state.status for state in result.step_states] == ["failed", "blocked"]
    assert result.blocked_reasons == [{"index": 1, "reason": "upstream_failed"}]
    assert sp_spy.call_count == 0, "dependent steps must not run after a CAS failure"
    assert len(fake_backend.calls) == 1, "only the necessary CAS invocation happened"

    cas_state = result.step_states[0]
    assert cas_state.result is not None
    assert cas_state.result.energy == pytest.approx(-109.14691549)
    assert cas_state.result.metadata["multireference"]["converged"] is False
    assert (task_root / "WORK" / "08_CASSCF" / "active_space.json").is_file()
    assert ResultManifest.read(task_root / "RESULT").status == "failed"


def test_casscf_step_result_adoption_refuses_false_convergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(c1) a stored completed receipt with converged=false is never adopted."""
    from acp.calculations.checkpoint import write_checkpoint
    from acp.calculations.contracts import Checkpoint
    from acp.calculations.identity import compute_identity
    from acp.calculations.step_result import STEP_RESULT_SCHEMA_VERSION, write_step_result

    task_root = tmp_path / "task"
    plan = _casscf_plan(task_root)
    identity = compute_identity(plan)
    step_dir = task_root / "WORK" / "08_CASSCF"
    step_dir.mkdir(parents=True)
    receipt = CalculationResult(
        status="completed",
        energy=-108.5,
        metadata={"multireference": {"converged": False, "casscf_energy_hartree": -108.5}},
    )
    payload = receipt.to_step_result_dict(root=task_root)
    payload.update(
        {
            "schema_version": STEP_RESULT_SCHEMA_VERSION,
            "step_identity": identity.step_identities[0],
            "step_id": executor_module._step_id(0, StepKind.CASSCF),
            "index": 0,
            "kind": "casscf",
            "symbols": ["C"],
            "job_id": None,
            "attempt": None,
            "code_release": "",
            "config_digest": None,
            "dependency_artifacts": [],
        }
    )
    digest = write_step_result(step_dir / "step_result.json", payload)
    write_checkpoint(
        task_root / "WORK" / "00_RUNTIME",
        Checkpoint(
            task_id="t11_c1",
            workflow="casscf",
            plan_fingerprint=identity.plan_identity,
            step_states=[
                {
                    "index": 0,
                    "kind": "casscf",
                    "status": "completed",
                    "error": "",
                    "energy": -108.5,
                    "executed_this_run": False,
                    "last_executed_attempt": 1,
                    "result_ref": {
                        "path": "WORK/08_CASSCF/step_result.json",
                        "sha256": digest,
                    },
                    "reused_from_attempt": None,
                }
            ],
            items_state={},
            resume_count=0,
            identity_schema=2,
        ),
    )
    monkeypatch.setattr(executor_module, "current_config_digest", lambda: None)

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        with patch.dict(executor_module._PRIMITIVE_DISPATCH, _casscf_recompute_dispatch(calls)):
            result = CalculationPlanExecutor().execute(plan, task_root=task_root)

    assert "recovery.step_not_adopted" in caplog.text
    assert "cas_not_converged" in caplog.text
    assert calls.get("casscf", 0) == 1, "refused adoption must conservatively recompute once"
    assert result.is_completed
    state = result.step_states[0]
    assert state.executed_this_run is True
    assert state.reused_from_attempt is None


def test_casscf_scientific_record_entry_refuses_false_convergence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(c2) the publish-retry entry must not restore converged=false records."""
    from acp.calculations.identity import compute_identity

    task_root = tmp_path / "task"
    plan = _casscf_plan(task_root)
    identity = compute_identity(plan)
    step_dir = task_root / "WORK" / "08_CASSCF"
    step_dir.mkdir(parents=True)
    log_path = step_dir / "casscf.log"
    log_path.write_text("CAS-SCF did not converge\n", encoding="utf-8")
    science = CalculationResult(
        status="completed",
        energy=-108.5,
        artifacts=[ArtifactRef(path=log_path, type="log", source="test")],
        metadata={"multireference": {"converged": False}},
    )
    record = executor_module._step_scientific_record(
        science,
        result_id=executor_module._step_result_id(identity.plan_identity, 0, StepKind.CASSCF),
        kind=StepKind.CASSCF,
        result_dir=step_dir,
    )
    result_publication.save_scientific_result(step_dir, record)

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        with patch.dict(executor_module._PRIMITIVE_DISPATCH, _casscf_recompute_dispatch(calls)):
            result = CalculationPlanExecutor().execute(plan, task_root=task_root)

    assert "recovery.scientific_result_not_reusable" in caplog.text
    assert "cas_not_converged" in caplog.text
    assert calls.get("casscf", 0) == 1, "only the necessary recompute executes QC"
    assert result.is_completed
    assert result.step_states[0].executed_this_run is True


def _run_casscf_with_flaky_publish(
    fake_backend: FakeBackend,
    task_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    """Execute one CAS plan once with an injected publish failure; QC count."""
    real_register = result_publication.register_result_manifest
    state = {"failures": 1}

    def flaky_register(result_dir, manifest):  # type: ignore[no-untyped-def]
        if state["failures"]:
            state["failures"] -= 1
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(result_publication, "register_result_manifest", flaky_register)
    plan = _casscf_plan(task_root)
    first = CalculationPlanExecutor().execute(plan, task_root=task_root)
    assert first.is_failed
    step_dir = task_root / "WORK" / "08_CASSCF"
    assert result_publication.load_scientific_result(step_dir) is not None
    return len(fake_backend.calls)


def test_casscf_publish_retry_adopts_receipt_without_qc(
    fake_backend: FakeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) true-converged publish-only retry: QC invocation count unchanged."""
    fake_backend.set_result(
        "casscf",
        QCResult(
            success=True,
            energy=-109.14691549,
            symbols=["C"],
            converged=True,
            metadata=dict(_CASSCF_CONVERGED_METADATA),
        ),
    )
    task_root = tmp_path / "task"
    qc_after_first = _run_casscf_with_flaky_publish(fake_backend, task_root, monkeypatch)
    assert qc_after_first == 1

    result = CalculationPlanExecutor().execute(_casscf_plan(task_root), task_root=task_root)

    assert result.is_completed
    assert len(fake_backend.calls) == qc_after_first, "publish retry must not re-run QC"
    state = result.step_states[0]
    assert state.status == "completed"
    assert state.executed_this_run is False
    publication = result_publication.load_publication_state(task_root / "WORK" / "08_CASSCF")
    assert publication is not None and publication.complete is True


def test_casscf_record_publish_retry_restores_without_qc(
    fake_backend: FakeBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d, entry ii) a converged record restores completed without QC."""
    fake_backend.set_result(
        "casscf",
        QCResult(
            success=True,
            energy=-109.14691549,
            symbols=["C"],
            converged=True,
            metadata=dict(_CASSCF_CONVERGED_METADATA),
        ),
    )
    task_root = tmp_path / "task"
    qc_after_first = _run_casscf_with_flaky_publish(fake_backend, task_root, monkeypatch)
    assert qc_after_first == 1

    # Remove the receipt so only the WORK-layer scientific record remains
    # (the legacy publication-only branch reaches entry (ii)).
    step_dir = task_root / "WORK" / "08_CASSCF"
    (step_dir / "step_result.json").unlink()
    cp_path = task_root / "WORK" / "00_RUNTIME" / "checkpoint.json"
    cp_payload = json.loads(cp_path.read_text(encoding="utf-8"))
    cp_payload["step_states"][0]["status"] = "pending"
    cp_payload["step_states"][0]["result_ref"] = None
    cp_path.write_text(json.dumps(cp_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = CalculationPlanExecutor().execute(_casscf_plan(task_root), task_root=task_root)

    assert result.is_completed
    assert len(fake_backend.calls) == qc_after_first, (
        "the scientific-record publish-retry entry must not re-run QC"
    )
    publication = result_publication.load_publication_state(step_dir)
    assert publication is not None and publication.complete is True
    assert step_dir.joinpath("step_result.json").is_file(), "receipt re-materialised"
