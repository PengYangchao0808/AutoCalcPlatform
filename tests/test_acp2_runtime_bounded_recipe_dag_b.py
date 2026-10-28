"""M2-04b — frozen definition identity vs runtime execution identity.

Covered here (checkpoint b):

* the conversion happens at **freeze** time and the worker only deserializes;
  a definition whose payload no longer recompiles to the frozen ``v2:`` digest is
  a typed refusal;
* ``Runtime.step_dag`` keeps **no** second fingerprint algorithm — the execution
  identity comes from ``cccp...materialize_step_identity`` only;
* the envelope's ``step_identities`` is the *definition* identity set (wire name
  preserved) and is carried verbatim on the frozen definition;
* ``step_result.json`` / ``DagRunState`` / publication documents record the
  execution identity;
* the receipt carries the additive ``step_execution_chain`` + ``chain_digest``
  and the Controller verifies the definition set **and** the chain separately;
* ``plan_digest`` (``sha256:``) and ``frozen_plan_body_digest`` (``v2:``) are
  verified separately and never compensate for one another;
* an old ``DagRunState`` without an ``identity_scheme`` is refused for adoption,
  and adoption conflicts on plan/input/definition are typed refusals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp_services.contracts.envelope import ExecutionReceipt
from acp_services.runtime import step_dag as step_dag_module
from acp_services.runtime.receipts import (
    chain_digest_of,
    verify_receipt,
    verify_step_execution_chain,
)
from acp_services.runtime.step_dag import (
    DAG_RUN_STATE_SCHEMA,
    DAG_RUN_STATE_SCHEMA_V1,
    BoundedStepDagExecutor,
    DagRunState,
    StepDagError,
    StepStatus,
)
from acp_services.runtime.step_definition import (
    IDENTITY_SCHEME_CCCP,
    StepDagDefinition,
    definition_from_frozen_payload,
    frozen_plan_body_digest_v2,
    verify_plan_digests,
)
from tests._acp2_bounded_dag_helpers import (
    APPROVAL_DIGEST,
    PLAN_IDENTITY,
    build_definition,
    envelope,
    fake_runner,
    frozen_plan,
)

INPUT_DIGEST = "sha256:" + "b" * 64


def _publish(step, outcome):  # noqa: ANN001 - test-local PublishFn
    return {product.product_kind: product.digest for product in outcome.products}


def _run(tmp_path: Path, *, plan_payload=None, runner=None, attempt: int = 1):
    dag, definition, payload = build_definition(tmp_path)
    plan_payload = plan_payload or payload
    executor = BoundedStepDagExecutor(
        dag,
        _adapter(tmp_path, runner=runner),
        _publish,
        plan_identity=PLAN_IDENTITY,
        release_id="rel-m204",
        attempt=attempt,
        owner="worker-1",
        fencing="fence-1",
        work_dir=str(tmp_path),
        input_digest=INPUT_DIGEST,
        require_definition=True,
    )
    return executor, dag, definition


def _adapter(work: Path, *, runner=None):
    from tests._acp2_bounded_dag_helpers import StepDispatchAdapterFactory

    return StepDispatchAdapterFactory(work, runner=runner)


# ---------------------------------------------------------------------------
# freeze-time conversion / worker deserialization
# ---------------------------------------------------------------------------


def test_definition_round_trips_through_the_frozen_payload(tmp_path: Path) -> None:
    dag, definition, payload = build_definition(tmp_path)
    restored_dag, restored_definition = definition_from_frozen_payload(payload)
    assert [step.step_id for step in restored_dag.steps] == [step.step_id for step in dag.steps]
    assert restored_definition.plan_definition_digest == definition.plan_definition_digest
    assert restored_definition.identity_scheme == IDENTITY_SCHEME_CCCP
    # The worker recompiles only to *verify* the digest; it never re-derives the
    # StepSpec view from a plan dict.
    restored_definition.compiled()


def test_tampered_definition_payload_is_refused(tmp_path: Path) -> None:
    _dag, definition, payload = build_definition(tmp_path)
    broken = json.loads(json.dumps(payload))
    broken["bounded_step_dag"]["definition"]["recipe"]["limits"]["max_steps"] = 42
    with pytest.raises(StepDagError) as excinfo:
        definition_from_frozen_payload(broken)
    assert excinfo.value.code in {"definition_digest_mismatch", "invalid_dag"}


def test_missing_definition_refuses_a_production_dispatch(tmp_path: Path) -> None:
    dag, _definition, _payload = build_definition(tmp_path)
    from acp_services.runtime.step_dag import BoundedStepDag

    fixture = BoundedStepDag(
        recipe_id=dag.recipe_id, task_ref=dag.task_ref, steps=dag.steps, definition=None
    )
    with pytest.raises(StepDagError) as excinfo:
        BoundedStepDagExecutor(
            fixture,
            _adapter(tmp_path),
            _publish,
            plan_identity=PLAN_IDENTITY,
            release_id="rel-m204",
            require_definition=True,
        )
    assert excinfo.value.code == "missing_definition"


def test_runtime_keeps_no_second_fingerprint_algorithm() -> None:
    assert not hasattr(step_dag_module, "materialize_step_identity")
    assert hasattr(step_dag_module, "materialize_execution_identity")
    source = Path(step_dag_module.__file__).read_text(encoding="utf-8")
    # The Runtime no longer hashes a step identity itself: it delegates.
    assert "canonical_digest(" not in source
    assert "materialized.execution_identity" in source


def test_definition_identities_are_carried_never_recomputed(tmp_path: Path) -> None:
    carried = ("sha256:" + "1" * 64, "sha256:" + "2" * 64)
    dag, definition, payload = build_definition(
        tmp_path, step_definition_identities=carried, approval_plan_digest=APPROVAL_DIGEST
    )
    assert definition.step_definition_identities == carried
    assert payload["bounded_step_dag"]["definition"]["step_definition_identities"] == list(carried)
    assert definition.approval_plan_digest == APPROVAL_DIGEST
    with pytest.raises(StepDagError) as excinfo:
        StepDagDefinition(
            recipe_id="r",
            plan_definition_digest="sha256:" + "3" * 64,
            recipe_payload={},
            inputs_payload={},
        )
    assert excinfo.value.code == "invalid_definition"


# ---------------------------------------------------------------------------
# per-step records + receipt chain
# ---------------------------------------------------------------------------


def test_step_state_and_state_document_record_the_execution_identity(tmp_path: Path) -> None:
    executor, _dag, _definition = _run(tmp_path)
    state = executor.run()
    document = state.to_dict()
    assert document["schema"] == DAG_RUN_STATE_SCHEMA
    assert document["identity_scheme"] == IDENTITY_SCHEME_CCCP
    for row in document["states"]:
        assert row["step_identity"].startswith("v2:")
        assert row["identity_scheme"] == IDENTITY_SCHEME_CCCP
    chain = executor.execution_chain()
    assert [row["step_id"] for row in chain] == ["relax", "freq"]
    assert all(row["step_identity"].startswith("v2:") for row in chain)


def test_receipt_chain_is_additive_and_verified_separately(tmp_path: Path) -> None:
    executor, _dag, _definition = _run(tmp_path)
    state = executor.run()
    chain = tuple(executor.execution_chain())
    env = envelope()
    receipt = ExecutionReceipt(
        receipt_version=1,
        job_id=env.job_id,
        attempt=env.attempt,
        submission_id=env.submission_id,
        state="completed",
        result_digest=chain_digest_of(chain),
        release_id=env.release_id,
        release_digest=env.release_digest,
        owner=env.owner,
        fencing_token=env.fencing_token,
        plan_identity=env.plan_identity,
        step_identities=env.step_identities,
        step_execution_chain=chain,
        chain_digest=chain_digest_of(chain),
    )
    # Round-trips through the wire form without losing the additive fields.
    restored = ExecutionReceipt.from_dict(receipt.to_dict())
    assert restored.step_execution_chain == chain
    assert restored.chain_digest == receipt.chain_digest
    assert verify_step_execution_chain(restored).accepted

    verdict = verify_receipt(
        restored,
        env,
        current_attempt=env.attempt,
        current_submission_id=env.submission_id,
        current_release_id=env.release_id,
        owner_token=env.owner,
        fencing_token=env.fencing_token,
    )
    assert verdict.accepted, verdict.reason
    assert state.states["relax"].qualification == "complete"

    # A forged chain (edited after signing) is rejected by the chain digest.
    forged = ExecutionReceipt.from_dict({**receipt.to_dict(), "chain_digest": "sha256:" + "0" * 64})
    assert not verify_step_execution_chain(forged).accepted
    assert not verify_receipt(
        forged,
        env,
        current_attempt=env.attempt,
        current_submission_id=env.submission_id,
        current_release_id=env.release_id,
        owner_token=env.owner,
        fencing_token=env.fencing_token,
    ).accepted


def test_chain_row_without_a_v2_identity_is_rejected(tmp_path: Path) -> None:
    chain = (
        {
            "step_id": "relax",
            "step_identity": "sha256:" + "1" * 64,
            "identity_scheme": IDENTITY_SCHEME_CCCP,
        },
    )
    env = envelope()
    receipt = ExecutionReceipt(
        receipt_version=1,
        job_id=env.job_id,
        attempt=env.attempt,
        submission_id=env.submission_id,
        state="completed",
        step_execution_chain=chain,
        chain_digest=chain_digest_of(chain),
    )
    verdict = verify_step_execution_chain(receipt)
    assert not verdict.accepted
    assert verdict.code == "chain_mismatch"


def test_fixture_chain_is_never_production_evidence() -> None:
    chain = (
        {
            "step_id": "relax",
            "step_identity": "v2:" + "1" * 32,
            "identity_scheme": "static-fixture.v0",
        },
    )
    env = envelope()
    receipt = ExecutionReceipt(
        receipt_version=1,
        job_id=env.job_id,
        attempt=env.attempt,
        submission_id=env.submission_id,
        state="completed",
        step_execution_chain=chain,
        chain_digest=chain_digest_of(chain),
    )
    assert not verify_step_execution_chain(receipt).accepted


# ---------------------------------------------------------------------------
# two digests, two domains
# ---------------------------------------------------------------------------


def test_plan_and_body_digests_are_verified_separately(tmp_path: Path) -> None:
    _dag, _definition, payload = build_definition(tmp_path, approval_plan_digest=APPROVAL_DIGEST)
    body = frozen_plan_body_digest_v2(payload["bounded_step_dag"])
    verify_plan_digests(
        approval_plan_digest=APPROVAL_DIGEST,
        frozen_plan_body_digest=body,
        expected_approval_plan_digest=APPROVAL_DIGEST,
        expected_frozen_plan_body_digest=body,
    )
    # A changed approval digest is rejected even though the body is intact.
    with pytest.raises(StepDagError) as excinfo:
        verify_plan_digests(
            approval_plan_digest="sha256:" + "9" * 64,
            frozen_plan_body_digest=body,
            expected_approval_plan_digest=APPROVAL_DIGEST,
            expected_frozen_plan_body_digest=body,
        )
    assert excinfo.value.code == "approval_digest_mismatch"
    # ...and a changed body digest is rejected even though the approval is intact.
    with pytest.raises(StepDagError) as excinfo:
        verify_plan_digests(
            approval_plan_digest=APPROVAL_DIGEST,
            frozen_plan_body_digest="v2:" + "8" * 32,
            expected_approval_plan_digest=APPROVAL_DIGEST,
            expected_frozen_plan_body_digest=body,
        )
    assert excinfo.value.code == "body_digest_mismatch"
    # A sha256: value in the body slot (the classic substitution) is refused.
    with pytest.raises(StepDagError) as excinfo:
        verify_plan_digests(
            approval_plan_digest=APPROVAL_DIGEST,
            frozen_plan_body_digest=APPROVAL_DIGEST,
            expected_approval_plan_digest=APPROVAL_DIGEST,
            expected_frozen_plan_body_digest=APPROVAL_DIGEST,
        )
    assert excinfo.value.code == "invalid_body_digest"


# ---------------------------------------------------------------------------
# adoption of a persisted state
# ---------------------------------------------------------------------------


def test_old_run_state_without_identity_scheme_is_refused(tmp_path: Path) -> None:
    executor, dag, _definition = _run(tmp_path)
    persisted = executor.run().to_dict()
    legacy = dict(persisted)
    legacy["schema"] = DAG_RUN_STATE_SCHEMA_V1
    legacy.pop("identity_scheme")
    with pytest.raises(StepDagError) as excinfo:
        BoundedStepDagExecutor.recover(
            dag,
            _adapter(tmp_path),
            _publish,
            plan_identity=PLAN_IDENTITY,
            release_id="rel-m204",
            persisted=legacy,
            work_dir=str(tmp_path),
        )
    assert excinfo.value.code in {"unknown_run_state_schema", "unknown_identity_scheme"}

    # The migration is an explicit decision: with the scheme present the same
    # document is adoptable.
    adopted = DagRunState.from_dict({**persisted, "schema": DAG_RUN_STATE_SCHEMA}, adopt=True)
    assert adopted.identity_scheme == IDENTITY_SCHEME_CCCP


def test_adoption_conflicts_on_plan_and_input(tmp_path: Path) -> None:
    executor, dag, _definition = _run(tmp_path)
    persisted = executor.run().to_dict()
    with pytest.raises(StepDagError) as excinfo:
        BoundedStepDagExecutor.recover(
            dag,
            _adapter(tmp_path),
            _publish,
            plan_identity="v2:another-plan",
            release_id="rel-m204",
            persisted=persisted,
            work_dir=str(tmp_path),
        )
    assert excinfo.value.code == "plan_conflict"
    with pytest.raises(StepDagError) as excinfo:
        BoundedStepDagExecutor.recover(
            dag,
            _adapter(tmp_path),
            _publish,
            plan_identity=PLAN_IDENTITY,
            release_id="rel-m204",
            persisted=persisted,
            input_digest="sha256:" + "7" * 64,
            work_dir=str(tmp_path),
        )
    assert excinfo.value.code == "input_conflict"
    recovered = BoundedStepDagExecutor.recover(
        dag,
        _adapter(tmp_path),
        _publish,
        plan_identity=PLAN_IDENTITY,
        release_id="rel-m204",
        persisted=persisted,
        work_dir=str(tmp_path),
        input_digest=INPUT_DIGEST,
    )
    assert recovered.state.identity_scheme == IDENTITY_SCHEME_CCCP
    assert recovered.state.attempt == 1


def test_v1_run_state_fixture_requires_an_explicit_migration(tmp_path: Path) -> None:
    """The pre-M2-04 run state is a frozen fixture: adoption is refused by default."""
    fixture = (
        Path(__file__).resolve().parent / "baseline" / "recovery_fixtures" / "dag_run_state_v1.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    assert payload["schema"] == DAG_RUN_STATE_SCHEMA_V1
    assert "identity_scheme" not in payload

    _dag, definition, _frozen = build_definition(tmp_path)
    dag, _definition2, _frozen2 = build_definition(tmp_path / "work2")
    with pytest.raises(StepDagError):
        DagRunState.from_dict(payload, adopt=True)
    with pytest.raises(StepDagError):
        BoundedStepDagExecutor.recover(
            dag,
            _adapter(tmp_path / "work2"),
            _publish,
            plan_identity=PLAN_IDENTITY,
            release_id="rel-m204",
            persisted=payload,
            work_dir=str(tmp_path / "work2"),
        )

    migrated = DagRunState.migrate_v1(
        payload,
        identity_scheme=IDENTITY_SCHEME_CCCP,
        plan_identity=PLAN_IDENTITY,
        plan_definition_digest=definition.plan_definition_digest,
        attempt=1,
        owner="worker-1",
        fencing="fence-1",
    )
    assert migrated.schema_version == DAG_RUN_STATE_SCHEMA
    assert migrated.identity_scheme == IDENTITY_SCHEME_CCCP
    # Statuses are preserved verbatim; no execution identity is invented.
    assert migrated.statuses() == {"relax": "completed", "freq": "running"}
    assert migrated.states["relax"].step_identity.startswith("sha256:")
    with pytest.raises(StepDagError):
        DagRunState.migrate_v1(payload, identity_scheme="something-else")


def test_recovery_redispatch_uses_the_actual_upstream_digest(tmp_path: Path) -> None:
    executor, dag, _definition = _run(tmp_path)
    state = executor.run()
    persisted = state.to_dict()
    # Mark the consumer as interrupted; the recovered run must re-dispatch it
    # with the real upstream digest, not a placeholder.
    for row in persisted["states"]:
        if row["step_id"] == "freq":
            row["status"] = "running"
    runner = fake_runner()
    recovered = BoundedStepDagExecutor.recover(
        dag,
        _adapter(tmp_path, runner=runner),
        _publish,
        plan_identity=PLAN_IDENTITY,
        release_id="rel-m204",
        persisted=persisted,
        work_dir=str(tmp_path),
        input_digest=INPUT_DIGEST,
    )
    recovered.run()
    assert [call["task"] for call in runner.calls] == ["frequency"]
    assert recovered.state.states["freq"].status is StepStatus.COMPLETED
    assert recovered.state.states["freq"].step_identity.startswith("v2:")


def test_definition_identity_is_stable_across_workers(tmp_path: Path) -> None:
    _dag, definition, _payload = build_definition(tmp_path)
    identity_a = definition.execution_identity(
        "relax", {}, materialized_parameters=None
    ).execution_identity
    identity_b = definition.execution_identity(
        "relax", {}, materialized_parameters=None
    ).execution_identity
    assert identity_a == identity_b
    changed = definition.execution_identity(
        "relax", {}, materialized_parameters={"optimizer": "other"}
    ).execution_identity
    assert changed != identity_a


def test_frozen_plan_identity_matches_the_envelope(tmp_path: Path) -> None:
    _dag, _definition, payload = build_definition(tmp_path, approval_plan_digest=APPROVAL_DIGEST)
    plan = frozen_plan(payload)
    env = envelope()
    assert plan.identity is not None
    assert plan.identity.plan_identity == env.plan_identity
    assert plan.identity.plan_digest.startswith("v2:")
