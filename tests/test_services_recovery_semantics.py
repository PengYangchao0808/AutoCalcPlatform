"""B05-3 (todo 27) — Runtime planning/recovery seam reuses existing semantics.

The Runtime planning/recovery use cases live in
``acp_services.runtime.recovery.RuntimeRecovery``; it is a one-call forwarding
seam over injected planning ports, exactly like ``RuntimeCommands`` is for
control (todo 25). The heavy semantics — two-layer plan/step identity,
``resume_source.json`` resolution, durable ``step_result`` completion evidence,
the step prerequisite gate, and publication-only retry — are **not**
re-implemented here: they remain owned by the injected implementations and this
suite proves they still hold when reached **through** the Runtime seam.

Acceptance (plan todo 27):

* identity / checkpoint format unchanged by the seam (no golden rewrite);
* a publication failure retries PUBLICATION ONLY (no QC recompute);
* blocked / ``upstream_failed`` / diagnostic results are never adopted as
  normal ``completed`` results;
* old v1 checkpoints are NOT adopted by default (conservative recompute).

All QC is faked through ``executor._PRIMITIVE_DISPATCH`` — no real binary runs,
no fixture/golden file is mutated (fixtures are copied into ``tmp_path``).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

import acp.calculations.executor as executor_module
from acp.calculations import result_publication
from acp.calculations.checkpoint import write_checkpoint
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationResult,
    CalculationStep,
    Checkpoint,
    ExecutionPolicy,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.identity import compute_identity
from acp.calculations.result_publication import (
    load_publication_state,
    load_scientific_result,
    register_result_manifest,
)
from acp.calculations.step_requirements import (
    MISSING_REQUIREMENT,
    UPSTREAM_FAILED,
    PriorStep,
    evaluate_prerequisite,
)
from acp.storage.manifest import ResultManifest
from acp_services.contracts.injection import (
    PlanExecutionPort,
    ResultPublicationPort,
    StepRequirementPort,
)
from acp_services.runtime.recovery import RuntimeRecovery

FIXTURES = Path(__file__).resolve().parent / "baseline" / "recovery_fixtures"


# ── seam construction / fixture helpers ─────────────────────────────────────


def _recovery() -> RuntimeRecovery:
    """Build the Runtime planning/recovery seam over the real acp semantics.

    This is the Runtime-side equivalent of the legacy assembly wiring: the
    injected implementations are the real ``CalculationPlanExecutor``,
    ``evaluate_prerequisite`` and the publication read/register entry points —
    so a green run proves the existing semantics, not a test double.
    """
    return RuntimeRecovery(
        planning=CalculationPlanExecutor(),
        step_requirements=SimpleNamespace(evaluate_prerequisite=evaluate_prerequisite),
        publication=SimpleNamespace(
            register_result_manifest=register_result_manifest,
            load_scientific_result=load_scientific_result,
        ),
    )


def _copy_fixture(name: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / name, destination / name)
    return destination / name


def _v2_plan(root: Path) -> CalculationPlan:
    """The shared v2 fixture plan (content-bound identity, path-independent)."""
    return CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=root / "structures" / "input.xyz", elements=["O", "H", "H"])],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )


def _v2_dispatch(calls: dict[str, int]) -> dict[StepKind, Any]:
    def sp(_request: object) -> CalculationResult:
        calls["sp"] = calls.get("sp", 0) + 1
        return CalculationResult(energy=-2.5)

    def freq(_request: object) -> CalculationResult:
        calls["freq"] = calls.get("freq", 0) + 1
        return CalculationResult(frequencies=[100.0, 200.0])

    return {StepKind.SINGLEPOINT: sp, StepKind.FREQUENCY: freq}


def _run_v2(runner: Any, root: Path, calls: dict[str, int]) -> Any:
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _v2_dispatch(calls)):
        # Fixtures pin ``config_digest: null`` — attempt metadata, never science.
        with patch.object(executor_module, "current_config_digest", lambda: None):
            return runner.execute(_v2_plan(root), root)


def _result_ids(root: Path) -> list[str]:
    return [product.id for product in ResultManifest.read(root / "RESULT").products]


# ── ① single-owner forwarding + port shape ──────────────────────────────────


def test_seam_ports_are_runtime_checkable_protocols() -> None:
    assert isinstance(CalculationPlanExecutor(), PlanExecutionPort)
    assert isinstance(
        SimpleNamespace(evaluate_prerequisite=evaluate_prerequisite), StepRequirementPort
    )
    assert isinstance(
        SimpleNamespace(
            register_result_manifest=register_result_manifest,
            load_scientific_result=load_scientific_result,
        ),
        ResultPublicationPort,
    )


def test_seam_forwards_each_use_case_exactly_once() -> None:
    planning = Mock()
    planning.execute.return_value = "plan-result"
    requirements = Mock()
    requirements.evaluate_prerequisite.return_value = "requirement-outcome"
    publication = Mock()
    publication.register_result_manifest.return_value = Path("/published")
    publication.load_scientific_result.return_value = "scientific-record"
    seam = RuntimeRecovery(
        planning=planning, step_requirements=requirements, publication=publication
    )

    task_root = Path("/task")
    prior: list[object] = []
    assert seam.execute("plan", task_root, plan_fingerprint="fp") == "plan-result"
    planning.execute.assert_called_once_with("plan", task_root, plan_fingerprint="fp")

    assert seam.evaluate_prerequisite(prior, StepKind.FREQUENCY, ["C"]) == "requirement-outcome"
    requirements.evaluate_prerequisite.assert_called_once_with(prior, StepKind.FREQUENCY, ["C"])

    assert seam.register_result_manifest(task_root, "manifest") == Path("/published")
    publication.register_result_manifest.assert_called_once_with(task_root, "manifest")

    assert seam.load_scientific_result(task_root) == "scientific-record"
    publication.load_scientific_result.assert_called_once_with(task_root)


# ── ② v2 fixture: format unchanged + adopt (identity reuse) via the seam ────


def test_v2_fixture_resumes_through_seam_format_unchanged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _copy_fixture("v2_checkpoint_mixed", tmp_path)
    stored = json.loads((root / "identity.json").read_text(encoding="utf-8"))
    identity = compute_identity(_v2_plan(root))
    assert identity.plan_identity == stored["plan_identity"]
    assert list(identity.step_identities) == stored["step_identities"]

    checkpoint_path = root / "WORK" / "00_RUNTIME" / "checkpoint.json"
    identity_bytes = (root / "identity.json").read_bytes()
    before = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert before["identity_schema"] == 2 and "resume_count" in before

    calls: dict[str, int] = {}
    with caplog.at_level(logging.INFO):
        result = _run_v2(_recovery(), root, calls)

    # The completed SP is adopted (identity + completion evidence reused); only
    # the failed FREQ recomputes — reached through the Runtime seam.
    assert calls.get("sp", 0) == 0, "seam must adopt the verified SP result"
    assert calls.get("freq", 0) == 1
    assert result.step_states[0].status == "completed"
    assert result.step_states[0].executed_this_run is False
    assert result.status == "completed"
    assert "recovery.step_adopted" in caplog.text

    # Format is unchanged by the seam: still schema 2 / resume_count, same
    # content-bound plan identity, identity receipt byte-identical.
    after = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert after["identity_schema"] == 2 and "resume_count" in after
    assert after["plan_fingerprint"] == identity.plan_identity
    assert (root / "identity.json").read_bytes() == identity_bytes


# ── ③ publication failure retries PUBLICATION ONLY ──────────────────────────


def test_publish_failure_retries_publication_only_through_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "task"
    plan = _two_step_plan(root)
    sp_calls: list[int] = []
    freq_calls: list[int] = []
    real_register = result_publication.register_result_manifest
    armed = {"on": True}

    def flaky_register(result_dir: Path, manifest: Any) -> Path:
        if armed["on"] and getattr(manifest, "workflow", None) == "frequency":
            armed["on"] = False
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(result_publication, "register_result_manifest", flaky_register)

    seam = _recovery()
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, _counting_dispatch(sp_calls, freq_calls)):
        first = seam.execute(plan, root)
        assert len(sp_calls) == 1 and len(freq_calls) == 1
        assert first.status == "failed", "a publication failure is observable"
        second = seam.execute(plan, root)

    assert len(sp_calls) == 1, "publish retry must not re-run SP QC"
    assert len(freq_calls) == 1, "publish retry must not re-run FREQ QC"
    assert second.status == "completed", second.errors
    state = load_publication_state(root / "WORK" / "04_FREQ")
    assert state is not None and state.complete is True
    ids = _result_ids(root)
    assert any(entry.startswith("step_0_singlepoint") for entry in ids)
    assert len(ids) == len(set(ids))


def test_publish_interrupted_fixture_retries_publish_only(tmp_path: Path) -> None:
    root = _copy_fixture("v2_publish_interrupted", tmp_path)
    sp_dir = root / "WORK" / "05_SP"
    assert (sp_dir / "scientific_result.json").is_file()
    assert not (sp_dir / "publication_state.json").is_file()
    assert not (root / "RESULT" / "result_manifest.json").is_file()

    calls: dict[str, int] = {}
    result = _run_v2(_recovery(), root, calls)

    assert calls.get("sp", 0) == 0, "publish-only retry must not re-run SP QC"
    assert result.step_states[0].status == "completed"
    state = load_publication_state(sp_dir)
    assert state is not None and state.complete is True
    ids = _result_ids(root)
    assert len(ids) == len(set(ids))
    assert "step_0_singlepoint_energy" in ids


# ── ④ blocked / upstream_failed / diagnostic never adopted as completed ─────


def test_prerequisite_port_preserves_blocked_and_diagnostic_outcomes() -> None:
    seam = _recovery()
    symbols = ["C"]
    failed = PriorStep(kind=StepKind.OPTIMIZE, status="failed")
    blocked = PriorStep(kind=StepKind.OPTIMIZE, status="blocked")
    satisfied = PriorStep(
        kind=StepKind.OPTIMIZE,
        status="completed",
        result=CalculationResult(coords=[[0.0, 0.0, 0.0]]),
    )
    no_geometry = PriorStep(
        kind=StepKind.OPTIMIZE,
        status="completed",
        result=CalculationResult(coords=None),
    )
    diagnostic = PriorStep(
        kind=StepKind.OPTIMIZE,
        status="completed",
        result=CalculationResult(coords=[[0.0, 0.0, 0.0]], metadata={"diagnostic_only": True}),
    )

    assert seam.evaluate_prerequisite([failed], StepKind.FREQUENCY, symbols).reason == (
        UPSTREAM_FAILED
    )
    assert seam.evaluate_prerequisite([blocked], StepKind.FREQUENCY, symbols).reason == (
        UPSTREAM_FAILED
    )
    assert seam.evaluate_prerequisite([satisfied], StepKind.FREQUENCY, symbols).satisfied is True
    assert seam.evaluate_prerequisite([no_geometry], StepKind.FREQUENCY, symbols).reason == (
        MISSING_REQUIREMENT
    )
    assert seam.evaluate_prerequisite([diagnostic], StepKind.FREQUENCY, symbols).reason == (
        MISSING_REQUIREMENT
    )


def test_blocked_steps_never_reported_completed_through_seam(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    plan = _prereq_plan(task_root, [StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT])
    freq_spy = Mock(return_value=CalculationResult(frequencies=[100.0]))
    sp_spy = Mock(return_value=CalculationResult(energy=-1.0))

    dispatch = {
        StepKind.OPTIMIZE: _failed_opt,
        StepKind.FREQUENCY: freq_spy,
        StepKind.SINGLEPOINT: sp_spy,
    }
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        result = _recovery().execute(plan, task_root)

    assert [state.status for state in result.step_states] == ["failed", "blocked", "blocked"]
    assert result.status == "failed" and not result.is_completed
    assert result.blocked_reasons == [
        {"index": 1, "reason": UPSTREAM_FAILED},
        {"index": 2, "reason": UPSTREAM_FAILED},
    ]
    assert freq_spy.call_count == 0 and sp_spy.call_count == 0
    assert all("blocked" not in entry for entry in result.errors)


def test_diagnostic_result_not_adopted_as_normal_through_seam(tmp_path: Path) -> None:
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

    dispatch = {
        StepKind.OPTIMIZE: opt,
        StepKind.FREQUENCY: freq,
        StepKind.SINGLEPOINT: Mock(return_value=CalculationResult(energy=-1.0)),
    }
    seam = _recovery()

    # Run 1: diagnostics policy → downstream runs, marked diagnostic.
    diag_plan = _prereq_plan(
        task_root, steps, policy=ExecutionPolicy(upstream_failure="diagnostics")
    )
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        first = seam.execute(diag_plan, task_root)
    assert [state.status for state in first.step_states] == ["failed", "completed", "completed"]
    assert first.step_states[1].result is not None
    assert first.step_states[1].result.metadata["diagnostic_only"] is True
    persisted = json.loads(
        (task_root / "WORK" / "04_FREQ" / "step_result.json").read_text(encoding="utf-8")
    )
    assert persisted.get("diagnostic_only") is True

    # Run 2: policy reverted to default → prerequisite re-checked, diagnostic
    # result NOT adopted as a normal completion.
    default_plan = _prereq_plan(task_root, steps)
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        second = seam.execute(default_plan, task_root)
    assert [state.status for state in second.step_states] == ["failed", "blocked", "blocked"]
    assert freq_calls == [1], "a diagnostic result must never be adopted as completed"
    assert second.status == "failed"

    # Run 3: upstream succeeds → prerequisite met, but the persisted diagnostic
    # purpose still refuses adoption (recompute, not reuse).
    opt_behavior["fail"] = False
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, dispatch):
        third = seam.execute(default_plan, task_root)
    assert third.status == "completed", third.errors
    assert freq_calls == [1, 1], "the diagnostic step_result must be recomputed"
    assert third.step_states[1].result is not None
    assert third.step_states[1].result.metadata.get("diagnostic_only") is None


# ── ⑤ old v1 checkpoints are NOT adopted by default ─────────────────────────


def test_v1_checkpoint_not_adopted_by_default_through_seam(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "task"
    plan = _two_step_plan(root)
    identity = compute_identity(plan)
    runtime_dir = root / "WORK" / "00_RUNTIME"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    write_checkpoint(
        runtime_dir,
        Checkpoint(
            task_id="t",
            workflow=plan.workflow,
            plan_fingerprint=identity.plan_identity,
            step_states=[
                {
                    "index": 0,
                    "kind": "singlepoint",
                    "status": "completed",
                    "step_id": "step_0_singlepoint",
                },
                {
                    "index": 1,
                    "kind": "frequency",
                    "status": "pending",
                    "step_id": "step_1_frequency",
                },
            ],
            items_state={},
            resume_count=1,
            identity_schema=1,
        ),
    )

    sp_calls: list[int] = []
    freq_calls: list[int] = []
    with caplog.at_level(logging.INFO):
        with patch.dict(
            executor_module._PRIMITIVE_DISPATCH, _counting_dispatch(sp_calls, freq_calls)
        ):
            result = _recovery().execute(plan, root)

    assert len(sp_calls) == 1, "a v1 checkpoint's completed fact must not be adopted by default"
    assert "identity_unverifiable_legacy" in caplog.text
    assert "recovery.step_adopted" not in caplog.text
    assert result.step_states[0].executed_this_run is True
    after = json.loads((runtime_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert after["identity_schema"] == 2, "recompute rewrites the checkpoint under the v2 scheme"


# ── ⑥ forwarding equivalence: seam == direct executor ───────────────────────


def test_seam_execution_equates_direct_executor(tmp_path: Path) -> None:
    direct_root = _copy_fixture("v2_checkpoint_mixed", tmp_path / "direct")
    seam_root = _copy_fixture("v2_checkpoint_mixed", tmp_path / "seam")

    direct_calls: dict[str, int] = {}
    direct = _run_v2(CalculationPlanExecutor(), direct_root, direct_calls)
    seam_calls: dict[str, int] = {}
    seam = _run_v2(_recovery(), seam_root, seam_calls)

    assert [state.status for state in direct.step_states] == [
        state.status for state in seam.step_states
    ]
    assert direct.status == seam.status
    assert _result_ids(direct_root) == _result_ids(seam_root)
    assert direct_calls == seam_calls


# ── local plan/dispatch builders (mirror the executor acceptance suites) ────


def _two_step_plan(root: Path) -> CalculationPlan:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    path.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    return CalculationPlan(
        workflow="optimize",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=path, elements=["H"], source="test")],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )


def _prereq_plan(
    root: Path, steps: list[StepKind], *, policy: ExecutionPolicy | None = None
) -> CalculationPlan:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "input.xyz"
    if not path.exists():
        path.write_text("1\ninput\nC 0 0 0\n", encoding="utf-8")
    return CalculationPlan(
        workflow="test",
        profile="r2SCAN-3c",
        items=[StructureArtifact(path=path, elements=["C"], source="test")],
        steps=[CalculationStep(kind=kind) for kind in steps],
        execution_policy=policy,
    )


def _failed_opt(_request: object) -> CalculationResult:
    return CalculationResult(
        status="failed",
        errors=["opt did not converge"],
        coords=[[9.0, 9.0, 9.0]],
    )


def _counting_dispatch(sp_calls: list[int], freq_calls: list[int]) -> dict[StepKind, Any]:
    def sp(_request: object) -> CalculationResult:
        sp_calls.append(1)
        return CalculationResult(energy=-1.0)

    def freq(_request: object) -> CalculationResult:
        freq_calls.append(1)
        return CalculationResult(frequencies=[100.0, 200.0])

    return {StepKind.SINGLEPOINT: sp, StepKind.FREQUENCY: freq}
