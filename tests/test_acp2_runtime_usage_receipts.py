"""M0-07 / G26 — trusted usage receipts bound to execution identity.

The settlement wire point is ``ReservationLedger.record_usage_once`` on the one
admission ledger.  These tests prove the trust boundary that the old
``trusted_source`` string could not provide:

* a project ``write`` API key (member) cannot write trusted usage → 403;
* ``controller-terminal-receipt`` is internal-only and not assignable;
* a receipt is verified against the immutable identity bound for its own
  ``(job, attempt)`` — wrong reservation / attempt / release and fabricated
  usage are refused and write nothing;
* a duplicate receipt is idempotent (never double-charged) and a same-id/different
  payload is a typed conflict;
* a late attempt-1 receipt still books after attempt 2 started, exactly once, and
  never advances the current task state;
* booked usage remains verifiable from persisted identities after the task row and
  work directory are purged;
* failed science and failed publication still settle correctly (publication
  failure never refunds computation);
* the TaskRef over-limit negative (counter-proof) is unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp_services.contracts.envelope import expected_submission_id
from acp_services.runtime.admission import (
    ADMISSION_SCHEMA_VERSION,
    AdmissionRequest,
    ReservationLedger,
    UsageReason,
    UsageReceiptRejectedError,
    get_admission_schema_version,
)
from acp_services.runtime.local_admission import build_local_admission
from acp_services.runtime.receipts import (
    ReceiptHistoryBinding,
    verify_historical_receipt,
)
from acp_services.runtime.research_budget import (
    BudgetConfigurationError,
    BudgetState,
    budget_id_for,
    parse_budget_policy,
)
from acp_services.runtime.usage_authority import (
    ASSIGNABLE_CAPABILITIES,
    UntrustedUsageProducerError,
    UsageProducer,
    UsageSource,
    authority_for_capabilities,
    normalize_usage_capabilities,
    resolve_authority,
)
from acp_services.runtime.usage_units import UsageUnitCounts
from acp_services.workspace.identity_lifecycle import (
    IdentityLifecycle,
    IdentityLifecyclePolicy,
)
from acp_services.workspace.keys import (
    IDENTITY_SCHEMA_MIGRATION_VERSION,
    ApiKeyManager,
    get_identity_schema_version,
)

PROJECT = "project_m007"
OTHER_PROJECT = "project_m007_other"
JOB = "job-m007"
BUDGET_PROPOSAL = "prop-m007"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _policy(budget_id: str, *, count: int = 3):
    budget = {"units": {"task_ref": count, "attempt": count, "qc_invocation": count}}
    return parse_budget_policy(
        budget,
        budget_id=budget_id,
        approved_budget=budget,
        approval_ref="approval-m007",
        project_id=PROJECT,
    )


def _ledger(tmp_path: Path, name: str = "admission.db") -> ReservationLedger:
    return ReservationLedger(str(tmp_path / name))


def _reserve(
    ledger: ReservationLedger,
    *,
    job_id: str = JOB,
    reservation_id: str = "res_m007",
    dispatched: bool = True,
) -> None:
    decision = ledger.reserve(
        AdmissionRequest(principal_id="principal_m007", project_id=PROJECT, job_id=job_id),
        _limits(),
        reservation_id=reservation_id,
    )
    assert decision.allowed
    if dispatched:
        ledger.set_state(reservation_id, state="dispatched")
        ledger.declare_attempt(job_id, 1, reservation_id=reservation_id)


def _limits():
    from acp_services.runtime.admission import Capacity, ReserveLimits

    return ReserveLimits(
        capacity=Capacity(cores=8, memory_mb=8192, concurrency=8, storage_bytes=10**12)
    )


def _receipt(job_id: str = JOB, *, attempt: int = 1, **overrides: Any):
    from acp_services.contracts.envelope import ExecutionReceipt

    data: dict[str, Any] = {
        "receipt_version": 1,
        "job_id": job_id,
        "attempt": attempt,
        "submission_id": expected_submission_id(job_id, attempt),
        "state": "completed",
        "release_id": "rel_1",
        "release_digest": "sha256:release",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_identity": "sha256:plan",
        "step_identities": ("sha256:step-1",),
    }
    data.update(overrides)
    return ExecutionReceipt(**data)


def _with_budget(
    ledger: ReservationLedger, *, job_id: str = JOB, count: int = 3
) -> tuple[str, str]:
    budget_id = budget_id_for(PROJECT, BUDGET_PROPOSAL)
    ledger.define_research_budget(_policy(budget_id, count=count))
    decision = ledger.reserve_research_budget(
        budget_id,
        {"task_ref": 1, "attempt": count},
        reservation_id="bres_m007",
        task_ref=job_id,
        admission_reservation_id="res_m007",
    )
    assert decision.admitted
    ledger.mark_research_budget_dispatched("res_m007")
    return budget_id, "bres_m007"


def _book(
    ledger: ReservationLedger,
    *,
    receipt_id: str = "receipt-1",
    job_id: str = JOB,
    attempt: int = 1,
    reservation_id: str = "res_m007",
    producer: str = UsageProducer.WORKER,
    budget_id: str = "",
    budget_reservation_id: str = "",
    receipt: Any = None,
    counters: UsageUnitCounts | None = None,
):
    return ledger.record_usage_once(
        receipt_id,
        reservation_id=reservation_id,
        job_id=job_id,
        attempt=attempt,
        cores=2,
        seconds=60,
        producer=producer,
        receipt=_receipt(job_id, attempt=attempt) if receipt is None else receipt,
        counters=counters or UsageUnitCounts(task_ref=1, attempt=1),
        budget_id=budget_id,
        budget_reservation_id=budget_reservation_id,
        run_id="run-1",
    )


# ---------------------------------------------------------------------------
# Producer authority / credential capability metadata
# ---------------------------------------------------------------------------


def test_controller_terminal_source_is_internal_and_not_assignable() -> None:
    assert UsageSource.CONTROLLER_TERMINAL not in ASSIGNABLE_CAPABILITIES
    with pytest.raises(UntrustedUsageProducerError) as excinfo:
        normalize_usage_capabilities([UsageSource.CONTROLLER_TERMINAL])
    assert excinfo.value.code == "usage_capability_not_assignable"
    assert authority_for_capabilities([]) is None
    authority = resolve_authority(producer=UsageProducer.INTERNAL)
    assert authority.internal and authority.trusted_source == UsageSource.CONTROLLER_TERMINAL
    with pytest.raises(UntrustedUsageProducerError):
        resolve_authority(producer=UsageSource.CONTROLLER_TERMINAL)


def test_external_source_string_is_not_an_authorization_fact(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    budget_id, budget_reservation = _with_budget(ledger)
    counters = UsageUnitCounts(task_ref=1)
    with pytest.raises(BudgetConfigurationError):
        ledger.settle_research_budget(
            budget_id,
            receipt_id="receipt-source",
            reservation_id=budget_reservation,
            counters=counters,
            trusted_source=UsageSource.WORKER_RECEIPT,
        )
    with pytest.raises(BudgetConfigurationError):
        ledger.settle_research_budget(
            budget_id,
            receipt_id="receipt-producer",
            reservation_id=budget_reservation,
            counters=counters,
            producer=UsageProducer.WORKER,
        )
    projection = ledger.research_budget_projection(budget_id)
    assert projection is not None and projection.receipts == 0
    # The internal source is accepted (the Controller's own terminal receipt).
    settlement = ledger.settle_research_budget(
        budget_id,
        receipt_id="receipt-internal",
        reservation_id=budget_reservation,
        counters=counters,
    )
    assert settlement.applied


def test_two_producer_capabilities_are_ambiguous(tmp_path: Path) -> None:
    with pytest.raises(UntrustedUsageProducerError) as excinfo:
        authority_for_capabilities(["usage.produce.worker", "usage.produce.proxy"])
    assert excinfo.value.code == "usage_capability_ambiguous"


def test_identity_db_carries_usage_capabilities_without_touching_actions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "acp_identity.db"
    manager = ApiKeyManager(db_path, iterations=2_000)
    service_key = manager.generate(
        scope="workspace",
        actions=["read", "write"],
        project=PROJECT,
        usage_capabilities=["usage.produce.worker"],
    )
    member_key = manager.generate(scope="workspace", actions=["read", "write"], project=PROJECT)
    member = manager.verify(member_key.key, required_action="write", project=PROJECT)
    assert member.allowed and member.usage_capabilities == ()
    service = manager.verify_identity(service_key.key, project=PROJECT)
    assert service.allowed
    assert service.actions == ("read", "write"), "capabilities must not widen actions"
    assert service.usage_capabilities == ("usage.produce.worker",)
    authority = authority_for_capabilities(service.usage_capabilities)
    assert authority is not None and authority.producer == UsageProducer.WORKER
    assert authority.trusted_source == UsageSource.WORKER_RECEIPT
    with pytest.raises(UntrustedUsageProducerError):
        manager.generate(
            scope="workspace",
            actions=["write"],
            usage_capabilities=[UsageSource.CONTROLLER_TERMINAL],
        )
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        assert get_identity_schema_version(conn) == IDENTITY_SCHEMA_MIGRATION_VERSION


def test_admission_db_migration_is_versioned_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "admission.db"
    ReservationLedger(str(path))
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        assert get_admission_schema_version(conn) == ADMISSION_SCHEMA_VERSION
    # Re-opening an existing DB re-runs the additive migration without error.
    ReservationLedger(str(path))
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        assert get_admission_schema_version(conn) == ADMISSION_SCHEMA_VERSION
        rows = conn.execute("SELECT COUNT(*) AS n FROM admission_attempt_bindings").fetchone()
        assert rows["n"] == 0


# ---------------------------------------------------------------------------
# Verification: wrong reservation / attempt / release, fabricated usage
# ---------------------------------------------------------------------------


def test_untrusted_producer_and_missing_receipt_are_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        _book(ledger, producer="")
    assert excinfo.value.code == UsageReason.PRODUCER_UNTRUSTED
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        ledger.record_usage_once(
            "receipt-x",
            reservation_id="res_m007",
            job_id=JOB,
            producer=UsageProducer.WORKER,
        )
    assert excinfo.value.code == UsageReason.RECEIPT_INVALID
    assert ledger.usage_totals()["receipts"] == 0


def test_fabricated_usage_for_undispatched_task_is_not_booked(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger, dispatched=False)  # reserved, never dispatched/declared
    budget_id, budget_reservation = _with_budget(ledger)
    # The budget row was flipped to dispatched, yet the attempt itself was never
    # dispatched: an external receipt may not invent the execution fact.
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        _book(ledger, budget_id=budget_id, budget_reservation_id=budget_reservation)
    assert excinfo.value.code == UsageReason.NOT_DISPATCHED
    assert ledger.usage_totals()["receipts"] == 0
    assert ledger.research_budget_projection(budget_id).consumed["task_ref"] == 1


def test_wrong_reservation_attempt_and_release_are_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    _book(ledger, receipt_id="receipt-1")
    assert ledger.usage_totals()["receipts"] == 1

    # wrong reservation → the attempt is already bound to res_m007
    _reserve(ledger, job_id=JOB, reservation_id="res_other")
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        _book(ledger, receipt_id="receipt-2", reservation_id="res_other")
    assert excinfo.value.code == UsageReason.RESERVATION_MISMATCH

    # declared attempt and receipt attempt disagree
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        _book(ledger, receipt_id="receipt-3", attempt=1, receipt=_receipt(attempt=2))
    assert excinfo.value.code == UsageReason.ATTEMPT_CONFLICT

    # wrong release id/digest
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        _book(
            ledger,
            receipt_id="receipt-4",
            receipt=_receipt(release_id="rel_other", release_digest="sha256:other"),
        )
    assert excinfo.value.code == UsageReason.ATTEMPT_CONFLICT
    assert excinfo.value.params["field"] == "stale_release"
    assert ledger.usage_totals()["receipts"] == 1, "refusals write nothing"


def test_attempt_binding_is_immutable(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    first = ReceiptHistoryBinding.from_receipt(_receipt(), reservation_id="res_m007")
    assert ledger.bind_attempt_identity(first) is True
    assert ledger.bind_attempt_identity(first) is False
    conflicting = ReceiptHistoryBinding.from_receipt(
        _receipt(release_id="rel_2"), reservation_id="res_m007"
    )
    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        ledger.bind_attempt_identity(conflicting)
    assert excinfo.value.code == UsageReason.ATTEMPT_CONFLICT
    stored = ledger.attempt_binding(JOB, 1)
    assert stored is not None and stored.release_id == "rel_1"


def test_historical_verifier_does_not_consult_the_current_attempt() -> None:
    binding = ReceiptHistoryBinding.from_receipt(_receipt(attempt=1), reservation_id="res_m007")
    stale_release = _receipt(attempt=1, release_id="rel_9")
    verdict = verify_historical_receipt(stale_release, binding, reservation_id="res_m007")
    assert not verdict.accepted and verdict.code == "stale_release"
    accepted = verify_historical_receipt(_receipt(attempt=1), binding, reservation_id="res_m007")
    assert accepted.accepted
    # There is no current-attempt/owner/fencing input on the historical path.
    assert "current_attempt" not in verify_historical_receipt.__code__.co_varnames


# ---------------------------------------------------------------------------
# Idempotency / conflicts / multi-attempt bookkeeping
# ---------------------------------------------------------------------------


def test_duplicate_receipt_is_idempotent_and_conflicting_payload_is_refused(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    budget_id, budget_reservation = _with_budget(ledger)
    first = _book(ledger, budget_id=budget_id, budget_reservation_id=budget_reservation)
    assert first.applied and not first.duplicate
    assert first.budget_applied
    consumed = dict(first.consumed)
    second = _book(ledger, budget_id=budget_id, budget_reservation_id=budget_reservation)
    assert not second.applied and second.duplicate
    assert second.reason_code == UsageReason.DUPLICATE
    assert ledger.usage_totals()["receipts"] == 1
    assert dict(second.consumed) == consumed
    assert ledger.research_budget_projection(budget_id).receipts == 1

    with pytest.raises(UsageReceiptRejectedError) as excinfo:
        ledger.record_usage_once(
            "receipt-1",
            reservation_id="res_m007",
            job_id=JOB,
            attempt=1,
            cores=99,
            seconds=99,
            producer=UsageProducer.WORKER,
            receipt=_receipt(),
            budget_id=budget_id,
            budget_reservation_id=budget_reservation,
        )
    assert excinfo.value.code == UsageReason.RECEIPT_CONFLICT
    assert ledger.usage_totals()["receipts"] == 1


def test_late_attempt_one_receipt_after_attempt_two_started(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    budget_id, budget_reservation = _with_budget(ledger)

    # attempt 2 is already under way (its immutable identity is bound first)
    attempt_two = ReceiptHistoryBinding.from_receipt(_receipt(attempt=2), reservation_id="res_m007")
    assert ledger.bind_attempt_identity(attempt_two) is True
    attempt_one = _book(
        ledger,
        receipt_id="late-attempt-1",
        attempt=1,
        budget_id=budget_id,
        budget_reservation_id=budget_reservation,
    )
    assert attempt_one.applied
    replay = _book(
        ledger,
        receipt_id="late-attempt-1",
        attempt=1,
        budget_id=budget_id,
        budget_reservation_id=budget_reservation,
    )
    assert replay.duplicate and not replay.applied
    assert {int(row["attempt"]) for row in ledger.usage_by_job(JOB)} == {1}
    assert ledger.attempt_binding(JOB, 2) is not None
    assert ledger.attempt_binding(JOB, 2).release_id == "rel_1"
    history = ledger.verify_usage_history(JOB)
    assert history["verified"] is True
    assert [row["attempt"] for row in history["attempts"]] == [1, 2]


def test_usage_is_verifiable_after_the_task_is_purged(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    budget_id, budget_reservation = _with_budget(ledger)
    _book(ledger, budget_id=budget_id, budget_reservation_id=budget_reservation)
    # Simulate a purge: delete the job row and its work directory.
    jobs_db = tmp_path / "jobs.db"
    from acp_services.store.migrations import migrate
    from acp_services.store.store import JobStore

    migrate(jobs_db)
    store = JobStore(jobs_db)
    work_dir = tmp_path / "work" / JOB
    work_dir.mkdir(parents=True)
    store.create(
        JobRecord(
            id=JOB,
            spec=JobSpec(workflow="fake", name=JOB, project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=PROJECT,
        )
    )
    store.purge_cascade(JOB)
    import shutil

    shutil.rmtree(work_dir)
    assert store.get(JOB) is None

    restarted = ReservationLedger(str(tmp_path / "admission.db"))
    history = restarted.verify_usage_history(JOB)
    assert history["verified"] is True
    assert history["problems"] == []
    assert len(history["receipts"]) == 1
    assert history["attempts"][0]["chain_digest"].startswith("sha256:")
    assert restarted.research_budget_projection(budget_id).consumed["task_ref"] == 1


# ---------------------------------------------------------------------------
# Failure paths still settle
# ---------------------------------------------------------------------------


def test_failed_science_and_failed_publication_still_settle(tmp_path: Path) -> None:
    admission = build_local_admission(
        tmp_path / "admission.db", verified_project=PROJECT, principal_id="principal_m007"
    )
    budget_id = budget_id_for(PROJECT, BUDGET_PROPOSAL)
    admission.ledger.define_research_budget(_policy(budget_id))
    spec = JobSpec(workflow="fake", name="failed-science", project_id=PROJECT)
    decision = admission.admit_job(
        "job-m007-failed",
        spec,
        budget={
            "budget_id": budget_id,
            "project_id": PROJECT,
            "budget": {"units": {"task_ref": 1, "attempt": 1, "qc_invocation": 1}},
        },
    )
    assert decision.allowed
    admission.ledger.bind_reservation_job(str(decision.reservation_id), "job-m007-failed")
    record = JobRecord(
        id="job-m007-failed",
        spec=spec,
        status=JobStatus.FAILED,
        attempt=1,
        error="science failed",
        result={"admission": admission.result_for_reservation(decision.reservation_id)},
    )
    assert admission.record_usage(record) is True
    assert admission.record_usage(record) is False
    assert admission.controller.usage_totals()["receipts"] == 1
    budget = admission.ledger.research_budget_for_admission(str(decision.reservation_id))
    assert budget is not None and budget.state == BudgetState.SETTLED
    # A failed publication is a held settlement, never a refund/recharge.
    before = admission.ledger.research_budget_projection(budget_id).consumed
    held = admission.ledger.release_research_budget(
        admission_reservation_id=str(decision.reservation_id),
        event="publication_failure",
    )
    after = admission.ledger.research_budget_projection(budget_id).consumed
    assert held.released is False
    assert held.reason_code == "budget_held"
    assert before == after, "a failed publication never refunds or recharges computation"


def test_task_ref_over_limit_negative_is_unchanged(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for(PROJECT, BUDGET_PROPOSAL)
    ledger.define_research_budget(_policy(budget_id, count=1))
    _reserve(ledger, job_id=JOB)
    first = _book(
        ledger,
        job_id=JOB,
        budget_id=budget_id,
        budget_reservation_id=_with_budget_one(ledger),
    )
    assert first.applied
    refused = ledger.reserve_research_budget(
        budget_id,
        {"task_ref": 1},
        reservation_id="bres_over",
        task_ref="job-m007-over",
    )
    assert not refused.admitted
    assert refused.reason_code == "budget_exhausted"
    assert refused.exhausted_unit == "task_ref"
    projection = ledger.research_budget_projection(budget_id)
    assert projection.consumed["task_ref"] == 1
    assert [row.reservation_id for row in projection.reservations] == ["bres_only"]


def _with_budget_one(ledger: ReservationLedger) -> str:
    budget_id = budget_id_for(PROJECT, BUDGET_PROPOSAL)
    decision = ledger.reserve_research_budget(
        budget_id,
        {"task_ref": 1},
        reservation_id="bres_only",
        task_ref=JOB,
        admission_reservation_id="res_m007",
    )
    assert decision.admitted
    ledger.mark_research_budget_dispatched("res_m007")
    return "bres_only"


# ---------------------------------------------------------------------------
# HTTP surface: a member write key cannot write trusted usage
# ---------------------------------------------------------------------------


def _provision_identity(tmp_path: Path) -> dict[str, Any]:
    db_path = tmp_path / "acp_identity.db"
    key_manager = ApiKeyManager(db_path, iterations=2_000)
    lifecycle = IdentityLifecycle(
        db_path,
        policy=IdentityLifecyclePolicy(
            deployment_tier="lab", capabilities=("authentication", "isolation")
        ),
        key_manager=key_manager,
        password_params={"kdf": "pbkdf2_hmac_sha256", "iterations": 2_000, "salt_bytes": 16},
    )
    alice = lifecycle.accept_invitation(
        lifecycle.invite_account(
            principal_id="principal_m007_alice", login_id="m007-alice", roles=["member"]
        ).token,
        password="M007-Strong-Passw0rd!",
    )
    member = lifecycle.issue_key(
        alice.account_id, scope="workspace", actions=["read", "write", "control"], project=PROJECT
    )
    worker = lifecycle.issue_key(
        alice.account_id,
        scope="workspace",
        actions=["read", "write"],
        project=PROJECT,
        usage_capabilities=["usage.produce.worker"],
    )
    return {"db_path": db_path, "member": member, "worker": worker}


@pytest.fixture()
def usage_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    provisioned = _provision_identity(tmp_path)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_SERVER_PROJECT", PROJECT)
    monkeypatch.setenv("ACP_IDENTITY_MODE", "api-key")
    monkeypatch.setenv("ACP_IDENTITY_DB", str(provisioned["db_path"]))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        admission = client.app.state.admission
        budget_id = budget_id_for(PROJECT, BUDGET_PROPOSAL)
        admission.ledger.define_research_budget(_policy(budget_id))
        admission.controller.admit(
            AdmissionRequest(principal_id="principal_m007", project_id=PROJECT, job_id=JOB),
            queued=True,
            reservation_id="res_m007",
        )
        admission.ledger.set_state("res_m007", state="dispatched")
        admission.ledger.declare_attempt(JOB, 1, reservation_id="res_m007")
        decision = admission.ledger.reserve_research_budget(
            budget_id,
            {"task_ref": 1, "attempt": 3},
            reservation_id="bres_m007",
            task_ref=JOB,
            admission_reservation_id="res_m007",
        )
        assert decision.admitted
        admission.ledger.mark_research_budget_dispatched("res_m007")
        yield {
            "client": client,
            "budget_id": budget_id,
            "budget_reservation_id": "bres_m007",
            "member": provisioned["member"].key,
            "worker": provisioned["worker"].key,
        }


def _settle_body(
    budget_reservation_id: str,
    *,
    receipt_id: str = "receipt-http-1",
    job_id: str = JOB,
    attempt: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "receipt_id": receipt_id,
        "reservation_id": budget_reservation_id,
        "job_id": job_id,
        "attempt": attempt,
        "submission_id": expected_submission_id(job_id, attempt),
        "release_id": "rel_1",
        "release_digest": "sha256:release",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_identity": "sha256:plan",
        "step_identities": ["sha256:step-1"],
        "run_id": "run-http-1",
        "usage": {"task_ref": 1, "attempt": 1, "qc_invocation": 1},
    }
    body.update(overrides)
    return body


def test_member_write_key_cannot_settle_trusted_usage(usage_app: dict[str, Any]) -> None:
    client: TestClient = usage_app["client"]
    response = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=_settle_body(usage_app["budget_reservation_id"]),
        headers={"X-API-Key": usage_app["member"]},
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "usage_capability_missing"
    admission = client.app.state.admission
    assert admission.controller.usage_totals()["receipts"] == 0
    projection = admission.ledger.research_budget_projection(usage_app["budget_id"])
    assert projection is not None and projection.receipts == 0


def test_trusted_source_is_not_a_request_field(usage_app: dict[str, Any]) -> None:
    client: TestClient = usage_app["client"]
    body = _settle_body(usage_app["budget_reservation_id"])
    body["trusted_source"] = "controller-terminal-receipt"
    response = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=body,
        headers={"X-API-Key": usage_app["worker"]},
    )
    assert response.status_code == 422, response.text
    assert client.app.state.admission.controller.usage_totals()["receipts"] == 0


def test_worker_credential_settles_once_and_duplicates_are_noops(
    usage_app: dict[str, Any],
) -> None:
    client: TestClient = usage_app["client"]
    body = _settle_body(usage_app["budget_reservation_id"])
    first = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=body,
        headers={"X-API-Key": usage_app["worker"]},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["applied"] is True
    assert payload["duplicate"] is False
    assert payload["trusted_source"] == UsageSource.WORKER_RECEIPT
    assert payload["producer"] == UsageProducer.WORKER
    assert payload["chain_digest"].startswith("sha256:")
    assert payload["budget"]["consumed"]["task_ref"] == 1
    settled = payload["budget"]["reservations"][0]
    assert settled["attempt"] == 1
    assert settled["receipt_id"] == "receipt-http-1"
    assert settled["release_id"] == "rel_1"
    assert settled["plan_identity"] == "sha256:plan"
    assert settled["chain_digest"].startswith("sha256:")

    second = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=body,
        headers={"X-API-Key": usage_app["worker"]},
    )
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["applied"] is False
    admission = client.app.state.admission
    assert admission.controller.usage_totals()["receipts"] == 1
    assert admission.ledger.research_budget_projection(usage_app["budget_id"]).receipts == 1


def test_forged_or_mismatched_http_receipts_are_refused(usage_app: dict[str, Any]) -> None:
    client: TestClient = usage_app["client"]
    headers = {"X-API-Key": usage_app["worker"]}
    # fabricated job (no such reservation)
    forged = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=_settle_body(usage_app["budget_reservation_id"], job_id="job-does-not-exist"),
        headers=headers,
    )
    assert forged.status_code in {404, 409}, forged.text
    # wrong attempt for this task, then a late attempt-1 receipt must still work
    wrong_attempt = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=_settle_body(usage_app["budget_reservation_id"], attempt=2),
        headers=headers,
    )
    assert wrong_attempt.status_code in {404, 409}, wrong_attempt.text
    admission = client.app.state.admission
    assert admission.controller.usage_totals()["receipts"] == 0


def test_http_late_attempt_does_not_advance_current_state(usage_app: dict[str, Any]) -> None:
    client: TestClient = usage_app["client"]
    manager = client.app.state.job_manager
    work_dir = Path(client.app.state.run_root) / JOB
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=JOB,
            spec=JobSpec(workflow="fake", name=JOB, project_id=PROJECT),
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=PROJECT,
            attempt=2,
        )
    )
    before = manager.store.get(JOB)
    assert before is not None and before.status == JobStatus.QUEUED
    admission = client.app.state.admission
    admission.ledger.bind_attempt_identity(
        ReceiptHistoryBinding.from_receipt(_receipt(attempt=2), reservation_id="res_m007")
    )
    response = client.post(
        f"/api/v2/research/budgets/{usage_app['budget_id']}/settle",
        json=_settle_body(usage_app["budget_reservation_id"], receipt_id="late-1", attempt=1),
        headers={"X-API-Key": usage_app["worker"]},
    )
    assert response.status_code == 200, response.text
    after = manager.store.get(JOB)
    assert after is not None
    assert after.status == JobStatus.QUEUED
    assert after.revision == before.revision
    assert after.attempt == 2
    assert response.json()["settlement"]["attempt"] == 1
    assert admission.controller.usage_by_job(JOB)
