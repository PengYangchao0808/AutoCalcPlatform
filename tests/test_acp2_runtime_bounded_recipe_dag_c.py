"""M2-04c — worst-case allowance, fenced commit and recovery.

Three coverage rows, kept separate as the plan requires:

**c1 — compile/meter port + rescue overrun**
``CompiledRecipe.worst_case_executions`` comes from the CCCP compiler; the
reservation is written once for the attempt; the CCCP ``TaskContext.meter``
(``call_capability``) draws one unit immediately before every backend call and
refuses the second call when only one unit is reserved (a rescue that would
exceed the reservation never starts); a restart cannot re-obtain an undeducted
unit (the ledger is persistent) and the settlement never widens the reservation.

**c2 — fencing, atomic commit and the lost-right race**
``FileFencedCommit`` stages outside the authoritative tree, re-checks
attempt/owner/fencing **and** the current execution right at the commit point,
then atomically replaces.  A right lost between the check and the commit leaves
the authoritative path untouched; two workers race and exactly one commits.

**c3 — resource precheck + recovery**
A missing binary/version/asset blocks *before* the dispatch (never a late
subprocess failure), and a recovered run re-dispatches only the interrupted
steps without re-minting allowance.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp_services.runtime.execution_quota import (
    ExecutionQuotaError,
    ExecutionQuotaExceededError,
    PersistentQuotaLedger,
    QuotaReservation,
    TaskMeterAdapter,
    reserve_worst_case,
)
from acp_services.runtime.execution_right import (
    AlwaysHeldExecutionRight,
    ExecutionRightState,
    ExecutionRightVerdict,
)
from acp_services.runtime.fenced_commit import (
    AlwaysHeldCommitPort,
    FenceLostError,
    FileFencedCommit,
    assert_production_fence,
)
from acp_services.runtime.resource_precheck import (
    BLOCK_MISSING_BINARY,
    BLOCK_VERSION_MISSING,
    ExecutionResourcePrecheck,
    ResourcePrecheckBlockedError,
    ResourceRequirement,
)
from acp_services.runtime.step_dag import (
    BoundedStepDagExecutor,
    StepStatus,
)
from tests._acp2_bounded_dag_helpers import (
    FakeExecutionRight,
    build_definition,
    envelope,
    fake_runner,
    frequency_result,
    optimize_result,
)

INPUT_DIGEST = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# c1 — worst case + meter
# ---------------------------------------------------------------------------


def test_compiler_declares_the_worst_case_per_step(tmp_path: Path) -> None:
    dag, definition, _payload = build_definition(tmp_path)
    compiled = definition.compiled()
    assert compiled.worst_case_executions == sum(
        step.worst_case_executions for step in compiled.steps
    )
    assert set(compiled.worst_case_by_step()) == {step.step_id for step in dag.steps}
    for step in dag.steps:
        assert step.worst_case_executions == compiled.step(step.step_id).worst_case_executions
    # The reservation is the compiler's worst case: one per dispatchable step.
    reservation = QuotaReservation.from_dict(
        json.loads(
            (tmp_path / "WORK/00_RUNTIME/quota/quota_reservation.json").read_text(encoding="utf-8")
        )
    )
    assert reservation.limits["recipe_step"] == len(dag.steps)
    assert reservation.limits["qc_invocation"] == sum(
        step.worst_case_executions for step in dag.steps
    )
    assert reservation.limits["recipe_step"] == len(dag.steps)  # no implicit +len(steps) margin


def test_reservation_is_never_re_minted(tmp_path: Path) -> None:
    reserve_worst_case(
        tmp_path,
        reservation_id="res-1",
        task_ref="t1",
        attempt=1,
        plan_definition_digest="v2:x",
        worst_case={"s1": 1},
    )
    with pytest.raises(ExecutionQuotaError):
        reserve_worst_case(
            tmp_path,
            reservation_id="res-2",
            task_ref="t1",
            attempt=1,
            plan_definition_digest="v2:x",
            worst_case={"s1": 5},
        )


def test_ledger_refuses_beyond_the_reservation_and_survives_restart(tmp_path: Path) -> None:
    reservation = reserve_worst_case(
        tmp_path,
        reservation_id="res-1",
        task_ref="t1",
        attempt=1,
        plan_definition_digest="v2:x",
        worst_case={"s1": 1, "s2": 1},
    )
    ledger = PersistentQuotaLedger(tmp_path, reservation)
    ledger.take("qc_invocation")
    # A "restart" re-reads the same persistent ledger: the spent unit is gone.
    restarted = PersistentQuotaLedger(tmp_path, reservation)
    assert restarted.taken("qc_invocation") == 1
    restarted.take("qc_invocation")
    with pytest.raises(ExecutionQuotaExceededError):
        restarted.take("qc_invocation")
    settlement = restarted.settle()
    assert settlement["qc_invocation"] == 2
    with pytest.raises(ExecutionQuotaError):
        restarted.settle({"qc_invocation": 99})


def test_cccp_backend_call_draws_before_it_runs(tmp_path: Path) -> None:
    """``call_capability`` is the single backend seam: one draw per call."""
    from cccp.calculation._common import CalculationInputs, call_capability
    from cccp.calculation.context import TaskContext, TaskQuotaExceededError

    reservation = reserve_worst_case(
        tmp_path,
        reservation_id="res-1",
        task_ref="t1",
        attempt=1,
        plan_definition_digest="v2:x",
        worst_case={"s1": 1},
    )
    ledger = PersistentQuotaLedger(tmp_path, reservation)
    meter = TaskMeterAdapter(ledger=ledger)
    context = TaskContext(meter=meter)

    class Backend:
        name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        def single_point(self, *args, **kwargs):  # noqa: ANN002, ANN003 - fake backend
            self.calls += 1
            return {"energy_hartree": -1.0, "output": "", "coordinates": [], "symbols": []}

    backend = Backend()
    inputs = CalculationInputs(
        coordinates=((0.0, 0.0, 0.0),), symbols=("H",), charge=0, multiplicity=1
    )
    call_capability(backend, "single_point", inputs, tmp_path, {}, context=context)
    assert backend.calls == 1
    # cap == 1: the second backend call (e.g. a rescue attempt) is refused
    # *before* it starts; the backend is never entered.
    with pytest.raises(TaskQuotaExceededError):
        call_capability(backend, "single_point", inputs, tmp_path, {}, context=context)
    assert backend.calls == 1
    assert ledger.taken("qc_invocation") == 1


def test_executor_pre_blocks_a_step_whose_worst_case_exceeds_the_quota(
    tmp_path: Path,
) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    reservation = QuotaReservation(
        reservation_id="res-tight",
        task_ref=dag.task_ref,
        attempt=1,
        plan_definition_digest=dag.plan_definition_digest,
        limits={"recipe_step": 2, "qc_invocation": 1},
    )
    ledger = PersistentQuotaLedger(tmp_path, reservation)
    ledger.take("qc_invocation")
    from tests.test_acp2_runtime_bounded_recipe_dag_b import _adapter, _publish

    executor = BoundedStepDagExecutor(
        dag,
        _adapter(tmp_path),
        _publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        work_dir=str(tmp_path),
        quota=_NullQuota(ledger),
    )
    state = executor.run()
    # The quota is exhausted, so every dispatch is refused as a blocked step
    # (a platform refusal, not a scientific failure).
    assert set(state.statuses().values()) <= {"blocked", "upstream_failed"}
    assert state.states["relax"].status is StepStatus.BLOCKED
    assert state.states["relax"].reason == "budget_exceeded"


class _NullQuota:
    """Quota port adapter that only reports the ledger's remaining units."""

    def __init__(self, ledger: PersistentQuotaLedger) -> None:
        self.ledger = ledger

    def reserve(self, **kwargs):  # noqa: ANN003 - port shape
        return None

    def take(self, unit: str, units: int = 1, *, detail: str = "") -> None:
        self.ledger.take(unit, units, detail=detail)

    def settle(self, **kwargs):  # noqa: ANN003 - port shape
        return None

    def remaining(self, unit: str = "") -> int:
        return self.ledger.remaining(unit or "qc_invocation")


# ---------------------------------------------------------------------------
# c2 — fenced commit
# ---------------------------------------------------------------------------


def _commit(tmp_path: Path, right=None) -> FileFencedCommit:
    env = envelope()
    return FileFencedCommit(
        work_dir=tmp_path,
        envelope=env,
        execution_right=right or FakeExecutionRight(),
    )


def test_commit_is_staged_then_atomic(tmp_path: Path) -> None:
    commit = _commit(tmp_path)
    staged = commit.stage("WORK/00_RUNTIME/steps/relax/step_result.json", {"step_id": "relax"})
    authoritative = tmp_path / staged.relative_path
    assert not authoritative.exists(), "staging must not touch the authoritative path"
    record = commit.commit_if_fenced(staged)
    assert authoritative.is_file()
    assert record.digest.startswith("sha256:")
    records = (tmp_path / "WORK/00_RUNTIME/commits/commit_attempt_1.jsonl").read_text()
    assert "step_result.json" in records


def test_lost_right_between_check_and_commit_leaves_no_authority(tmp_path: Path) -> None:
    class OneShotRight:
        """Reports HELD on the first verify, LOST on the second (the race)."""

        def __init__(self) -> None:
            self.calls = 0

        def acquire(self, envelope):  # noqa: ANN001 - port shape
            return ExecutionRightVerdict()

        def verify(self, envelope):  # noqa: ANN001 - port shape
            self.calls += 1
            if self.calls >= 2:
                return ExecutionRightVerdict(ExecutionRightState.LOST, "taken over")
            return ExecutionRightVerdict()

    commit = _commit(tmp_path, OneShotRight())
    staged = commit.stage("WORK/00_RUNTIME/steps/freq/step_result.json", {"step_id": "freq"})
    with pytest.raises(FenceLostError):
        commit.commit_if_fenced(staged)
    assert not (tmp_path / staged.relative_path).exists()
    assert staged.staging_path.is_file(), "the candidate stays staged, never promoted"


def test_fence_mismatch_is_refused(tmp_path: Path) -> None:
    env = envelope(owner="worker-1", fencing="fence-1")
    from acp_services.runtime.fenced_commit import CommitFence

    commit = FileFencedCommit(
        work_dir=tmp_path,
        envelope=env,
        execution_right=FakeExecutionRight(),
        fence=CommitFence(attempt=1, owner="someone-else", fencing="fence-1"),
    )
    staged = commit.stage("WORK/00_RUNTIME/steps/relax/step_result.json", {"x": 1})
    with pytest.raises(FenceLostError):
        commit.commit_if_fenced(staged)


def test_two_workers_compete_and_exactly_one_commits(tmp_path: Path) -> None:
    env = envelope()
    winner = FileFencedCommit(
        work_dir=tmp_path, envelope=env, execution_right=FakeExecutionRight(held=True)
    )
    loser = FileFencedCommit(
        work_dir=tmp_path, envelope=env, execution_right=FakeExecutionRight(held=False)
    )
    # The holder commits first; the competition loser's candidate is refused at
    # the commit point and never replaces the authoritative document.
    staged_winner = winner.stage("WORK/00_RUNTIME/steps/relax/step_result.json", {"by": "winner"})
    winner.commit_if_fenced(staged_winner)
    staged_loser = loser.stage("WORK/00_RUNTIME/steps/relax/step_result.json", {"by": "loser"})
    with pytest.raises(FenceLostError):
        loser.commit_if_fenced(staged_loser)
    payload = json.loads((tmp_path / staged_winner.relative_path).read_text())
    assert payload == {"by": "winner"}
    assert staged_loser.staging_path.is_file()


def test_always_held_is_refused_on_a_production_seam() -> None:
    with pytest.raises(Exception) as excinfo:
        assert_production_fence(AlwaysHeldExecutionRight(), seam="worker.receipt")
    assert "must never back a production commit" in str(excinfo.value)
    with pytest.raises(Exception):
        assert_production_fence(AlwaysHeldCommitPort(work_dir=Path(".")), seam="worker.receipt")


def test_production_dag_executor_uses_the_fenced_port(tmp_path: Path) -> None:
    """The production executor writes every authoritative document fenced."""
    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor

    dag, _definition, payload = build_definition(tmp_path)
    from tests._acp2_bounded_dag_helpers import frozen_plan

    plan = frozen_plan(payload)
    env = envelope()
    precheck = ExecutionResourcePrecheck(
        resolver=lambda name, configured: Path("/usr/bin/env"),
        version_probe=lambda name, path: "1.0.0",
    )
    executor = StepDagTaskExecutor(precheck=precheck, runner_factory=fake_runner())
    from types import SimpleNamespace

    outcome = executor.execute(
        SimpleNamespace(
            plan=plan,
            envelope=env,
            work_dir=tmp_path,
            storage_binding=None,
            execution_right=FakeExecutionRight(),
            control_poll=None,
        )
    )
    assert outcome.state == "completed"
    assert (tmp_path / "WORK/00_RUNTIME/commits/commit_attempt_1.jsonl").is_file()
    for step_id in ("relax", "freq"):
        assert (tmp_path / f"WORK/00_RUNTIME/steps/{step_id}/step_result.json").is_file()


def test_production_executor_refuses_the_always_held_fixture(tmp_path: Path) -> None:
    """``AlwaysHeldExecutionRight`` may never back a production DAG commit."""
    from types import SimpleNamespace

    from acp_services.runtime.step_dag import StepDagError
    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
    from tests._acp2_bounded_dag_helpers import frozen_plan

    _dag, _definition, payload = build_definition(tmp_path)
    executor = StepDagTaskExecutor(
        precheck=ExecutionResourcePrecheck(
            resolver=lambda name, configured: Path("/usr/bin/env"),
            version_probe=lambda name, path: "1.0.0",
        ),
        runner_factory=fake_runner(),
    )
    with pytest.raises(Exception) as excinfo:
        executor.execute(
            SimpleNamespace(
                plan=frozen_plan(payload),
                envelope=envelope(),
                work_dir=tmp_path,
                storage_binding=None,
                execution_right=AlwaysHeldExecutionRight(),
                control_poll=None,
            )
        )
    assert "must never back a production commit" in str(excinfo.value)
    assert not (tmp_path / "WORK/00_RUNTIME/steps/relax/step_result.json").exists()

    with pytest.raises(StepDagError) as excinfo:
        executor.execute(
            SimpleNamespace(
                plan=frozen_plan(payload),
                envelope=envelope(),
                work_dir=tmp_path,
                storage_binding=None,
                execution_right=None,
                control_poll=None,
            )
        )
    assert excinfo.value.code == "missing_execution_right"


def test_production_executor_refuses_a_lost_right(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
    from tests._acp2_bounded_dag_helpers import frozen_plan

    dag, _definition, payload = build_definition(tmp_path)
    executor = StepDagTaskExecutor(
        precheck=ExecutionResourcePrecheck(
            resolver=lambda name, configured: Path("/usr/bin/env"),
            version_probe=lambda name, path: "1.0.0",
        ),
        runner_factory=fake_runner(),
    )
    with pytest.raises(FenceLostError):
        executor.execute(
            SimpleNamespace(
                plan=frozen_plan(payload),
                envelope=envelope(),
                work_dir=tmp_path,
                storage_binding=None,
                execution_right=FakeExecutionRight(held=False),
                control_poll=None,
            )
        )
    assert not (tmp_path / "WORK/00_RUNTIME/steps/relax/step_result.json").exists()


# ---------------------------------------------------------------------------
# c3 — resource precheck + control polling + recovery
# ---------------------------------------------------------------------------


def test_precheck_blocks_a_missing_binary_before_dispatch(tmp_path: Path) -> None:
    precheck = ExecutionResourcePrecheck(
        resolver=lambda name, configured: None,
        version_probe=lambda name, path: "1.0.0",
        expected_release_id="rel-m204",
    )
    result = precheck.check(
        [ResourceRequirement(step_id="relax", executables=("orca",), require_version=True)]
    )
    assert not result.ok
    assert {blocker.code for blocker in result.blockers} == {
        BLOCK_MISSING_BINARY,
        "release_manifest_missing",
    }
    with pytest.raises(ResourcePrecheckBlockedError):
        precheck.require(
            [ResourceRequirement(step_id="relax", executables=("orca",), require_version=True)]
        )


def test_precheck_blocks_a_missing_version_and_asset(tmp_path: Path) -> None:
    precheck = ExecutionResourcePrecheck(
        resolver=lambda name, configured: Path("/usr/bin/env"),
        version_probe=lambda name, path: None,
        release_dir=tmp_path,
    )
    result = precheck.check(
        [
            ResourceRequirement(
                step_id="freq",
                executables=("orca",),
                require_version=True,
                assets=("model_assets/dp4.json",),
            )
        ]
    )
    codes = {blocker.code for blocker in result.blockers}
    assert BLOCK_VERSION_MISSING in codes
    assert "asset_missing" in codes


def test_production_executor_returns_blocked_when_a_binary_is_missing(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
    from tests._acp2_bounded_dag_helpers import frozen_plan

    _dag, _definition, payload = build_definition(tmp_path)
    runner = fake_runner()
    executor = StepDagTaskExecutor(
        precheck=ExecutionResourcePrecheck(
            resolver=lambda name, configured: None,
            version_probe=lambda name, path: "1.0.0",
            expected_release_id="rel-m204",
            release_manifest={"release_id": "rel-m204", "release_digest": "sha256:" + "0" * 64},
        ),
        runner_factory=runner,
    )
    outcome = executor.execute(
        SimpleNamespace(
            plan=frozen_plan(payload),
            envelope=envelope(),
            work_dir=tmp_path,
            storage_binding=None,
            execution_right=FakeExecutionRight(),
            control_poll=None,
        )
    )
    assert outcome.state == "blocked"
    assert runner.calls == []  # nothing ran


def test_step_requirements_is_not_a_binary_check() -> None:
    from acp_services.runtime import step_requirements

    source = Path(step_requirements.__file__).read_text(encoding="utf-8")
    assert "resolve_executable" not in source
    assert "detect_version" not in source
    assert "subprocess" not in source


def test_control_poll_cancels_before_the_next_dispatch(tmp_path: Path) -> None:
    from tests.test_acp2_runtime_bounded_recipe_dag_b import _adapter, _publish

    dag, _definition, _payload = build_definition(tmp_path)
    runner = fake_runner()
    polls = {"count": 0}

    def control() -> str:
        polls["count"] += 1
        return "cancel" if polls["count"] > 1 else "continue"

    executor = BoundedStepDagExecutor(
        dag,
        _adapter(tmp_path, runner=runner),
        _publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=1,
        work_dir=str(tmp_path),
        control=control,
    )
    state = executor.run()
    assert [call["task"] for call in runner.calls] == ["optimize"]
    assert state.states["relax"].status is StepStatus.COMPLETED
    assert state.states["freq"].status is StepStatus.CANCELLED
    assert state.cancelled is True


def test_control_intent_file_is_polled(tmp_path: Path) -> None:
    from acp_services.runtime.step_dag_worker import FileControlPoll

    poll = FileControlPoll(tmp_path)
    assert poll() == "continue"
    target = tmp_path / "WORK/00_RUNTIME/control_intent.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"intent": "cancel"}), encoding="utf-8")
    assert poll() == "cancel"
    target.write_text(json.dumps({"intent": "nonsense"}), encoding="utf-8")
    with pytest.raises(Exception):
        poll()


def test_run_state_carries_the_execution_facts(tmp_path: Path) -> None:
    from tests.test_acp2_runtime_bounded_recipe_dag_b import _adapter, _publish

    dag, _definition, _payload = build_definition(tmp_path)
    executor = BoundedStepDagExecutor(
        dag,
        _adapter(tmp_path),
        _publish,
        plan_identity="v2:plan-identity-m204",
        release_id="rel-m204",
        attempt=3,
        owner="worker-3",
        fencing="fence-3",
        work_dir=str(tmp_path),
        input_digest=INPUT_DIGEST,
    )
    document = executor.run().to_dict()
    assert document["attempt"] == 3
    assert document["owner"] == "worker-3"
    assert document["fencing"] == "fence-3"
    assert document["schema"] == "acp2.runtime.step-dag-state.v2"
    assert document["identity_scheme"]
    assert document["input_digest"] == INPUT_DIGEST


def test_worker_never_opens_a_jobs_database(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
    from tests._acp2_bounded_dag_helpers import frozen_plan

    _dag, _definition, payload = build_definition(tmp_path)
    executor = StepDagTaskExecutor(
        precheck=ExecutionResourcePrecheck(
            resolver=lambda name, configured: Path("/usr/bin/env"),
            version_probe=lambda name, path: "1.0.0",
        ),
        runner_factory=fake_runner(),
    )
    executor.execute(
        SimpleNamespace(
            plan=frozen_plan(payload),
            envelope=envelope(),
            work_dir=tmp_path,
            storage_binding=None,
            execution_right=FakeExecutionRight(),
            control_poll=None,
        )
    )
    assert list(tmp_path.rglob("*.db")) == []
    assert list(tmp_path.rglob("*.sqlite3")) == []


def test_settlement_records_the_receipt_consumption(tmp_path: Path) -> None:
    reservation = reserve_worst_case(
        tmp_path,
        reservation_id="res-1",
        task_ref="t1",
        attempt=1,
        plan_definition_digest="v2:x",
        worst_case={"s1": 2},
    )
    ledger = PersistentQuotaLedger(tmp_path, reservation)
    ledger.take("qc_invocation")
    ledger.take("qc_invocation")
    ledger.settle()
    document = json.loads(
        (tmp_path / "WORK/00_RUNTIME/quota/quota_settlement.json").read_text(encoding="utf-8")
    )
    assert document["drawn"]["qc_invocation"] == 2
    assert document["settled"]["qc_invocation"] == 2


def test_frequency_result_builder_is_typed() -> None:
    """Guard: the test fixtures use the real typed payload (no dict shortcut)."""
    import tempfile

    result = frequency_result(Path(tempfile.mkdtemp()))
    assert result.task.value == "frequency"
    result_opt = optimize_result(Path(tempfile.mkdtemp()))
    assert result_opt.artifacts[0].type == "geometry"
