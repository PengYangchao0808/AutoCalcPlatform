"""M2-03b — persistent worst-case reservations and conservative settlement.

Exit condition (EXECUTION_PLAN M2-03b): a task/attempt reserves its worst-case
units **before** dispatch on the one durable ledger, a planning call reserves
before a Proposal/TaskRef exists, an unknown/interrupted call stays charged, a
scientific approval rejection never deletes model usage already incurred, and a
restart/``run_id`` change never resets an allowance.

Coverage map:

* :func:`test_task_and_attempt_are_reserved_before_dispatch` — submit reserves
  ``task_ref`` + the first attempt; attempt 2 reserves its own unit before the
  attempt starts;
* :func:`test_attempt_over_the_limit_refuses_dispatch_before_any_process`;
* :func:`test_task_ref_over_the_limit_refuses_the_second_submit`;
* :func:`test_concurrent_reservations_never_double_admit`;
* :func:`test_run_id_change_does_not_reset_the_allowance`;
* :func:`test_process_restart_keeps_the_reservation` (durable, no in-memory
  state; a replayed claim reuses the same row);
* :func:`test_planning_reservation_needs_no_task_ref` — the empty-research
  planning allowance;
* :func:`test_planning_claim_unique_index_exists_and_is_enforced`;
* :func:`test_interrupted_planning_call_stays_reserved`;
* :func:`test_scientific_approval_rejection_keeps_incurred_model_usage`;
* :func:`test_missing_usage_settles_conservatively`.
"""

from __future__ import annotations

import sqlite3
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.local_admission import build_local_admission
from acp_services.runtime.planning_reservation import (
    PLANNING_RESERVATION_STATE_RESERVED,
    PLANNING_RESERVATION_STATE_RETAINED,
    PlanningReservationError,
    reconcile_planning,
    reserve_planning_call,
    retain_unknown_planning_call,
    settle_planning_call,
)
from acp_services.runtime.research_budget import (
    BudgetReason,
    BudgetState,
    budget_id_for,
    parse_budget_policy,
)
from acp_services.runtime.research_session import ResearchIdentity, planning_claim
from acp_services.runtime.usage_units import UsageUnitCounts
from acp_services.workspace.command_identity import CommandIdentity, SubjectKind

_PROJECT = "project-m203b"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _spec() -> Any:
    """A minimal job spec (resources only; nothing is dispatched in these tests)."""
    return types.SimpleNamespace(
        workflow="fake",
        project_id=_PROJECT,
        resources={"nproc": 1, "mem": "1GB", "walltime_s": 600},
        target_node="local",
    )


def _budget_claim(budget_id: str, definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "budget_id": budget_id,
        "budget": dict(definition),
        "approved_budget": dict(definition),
        "approval_ref": "ap_m203b",
        "project_id": _PROJECT,
    }


def _record(reservation_id: str, *, job_id: str = "job-1", attempt: int = 1) -> Any:
    return types.SimpleNamespace(
        id=job_id,
        attempt=attempt,
        status=types.SimpleNamespace(is_terminal=False),
        result={"admission": {"reservation_id": reservation_id}},
    )


def _identity() -> ResearchIdentity:
    return ResearchIdentity.allocate(
        CommandIdentity(principal_id="principal_alpha", subject_kind=SubjectKind.USER),
        session_seed="m2-03b",
    )


def _claim(round_no: int = 0, call_seq: int = 1):
    return planning_claim(
        _identity(),
        round=round_no,
        call_seq=call_seq,
        request={"model": "approved-model", "messages": [{"role": "user", "content": "plan"}]},
    )


# ---------------------------------------------------------------------------
# task / attempt reservations
# ---------------------------------------------------------------------------


def test_task_and_attempt_are_reserved_before_dispatch(tmp_path: Path) -> None:
    admission = build_local_admission(str(tmp_path / "admission.db"))
    budget_id = budget_id_for(_PROJECT, "prop-m203b")
    definition = {"units": {"task_ref": 2, "attempt": 1}, "wall_seconds": 600}
    decision = admission.admit_job(
        "job-1", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert decision.allowed is True
    projection = admission.ledger.research_budget_projection(budget_id)
    assert projection is not None
    # Worst-case units are charged at submit, before any work directory exists.
    assert projection.consumed["task_ref"] == 1
    assert projection.consumed["attempt"] == 1
    reservation = admission.ledger.research_budget_for_admission(str(decision.reservation_id))
    assert reservation is not None
    assert reservation.state == BudgetState.RESERVED
    # The persisted deadline projection travels with the admission result (one
    # value for all three termination surfaces; M2-03c).
    projected = admission.result_for_reservation(str(decision.reservation_id), decision=decision)[
        "execution_limits"
    ]
    assert projected["local_terminate_after_s"] == 600
    assert projected["lsf_walltime"] == "00:10"
    assert projected["source"] == "research-budget-policy"

    # Attempt 1 is covered by the submit reservation: no second charge.
    replay = admission.reserve_attempt(
        _record(str(decision.reservation_id), attempt=1),
        reservation_id=str(decision.reservation_id),
    )
    assert replay is None
    assert admission.ledger.research_budget_projection(budget_id).consumed["attempt"] == 1


def test_attempt_over_the_limit_refuses_dispatch_before_any_process(tmp_path: Path) -> None:
    admission = build_local_admission(str(tmp_path / "admission.db"))
    budget_id = budget_id_for(_PROJECT, "prop-attempt-cap")
    definition = {"units": {"task_ref": 1, "attempt": 1}, "wall_seconds": 600}
    decision = admission.admit_job(
        "job-attempt", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert decision.allowed is True
    reservation_id = str(decision.reservation_id)

    first = admission.revalidate_job(_record(reservation_id, job_id="job-attempt", attempt=1))
    assert first is not None and first.allowed is True

    # A rerun (attempt 2) must reserve its own attempt unit; the approved range
    # only allowed one, so dispatch is refused without invoking anything.
    second = admission.revalidate_job(_record(reservation_id, job_id="job-attempt", attempt=2))
    assert second is not None
    assert second.admitted is False
    assert second.reason_code == BudgetReason.EXHAUSTED
    assert second.exceeded and second.exceeded[0]["dimension"] == "attempt"

    # The refused attempt charged nothing.
    projection = admission.ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert projection.consumed["attempt"] == 1


def test_task_ref_over_the_limit_refuses_the_second_submit(tmp_path: Path) -> None:
    admission = build_local_admission(str(tmp_path / "admission.db"))
    budget_id = budget_id_for(_PROJECT, "prop-task-cap")
    definition = {"units": {"task_ref": 1}, "wall_seconds": 600}
    first = admission.admit_job(
        "job-a", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert first.allowed is True
    second = admission.admit_job(
        "job-b", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert second.admitted is False
    assert second.reason_code == BudgetReason.EXHAUSTED
    assert second.exceeded and second.exceeded[0]["dimension"] == "task_ref"
    # The refused second submit leaves no reservation row behind.
    projection = admission.ledger.research_budget_projection(budget_id)
    assert len(projection.reservations) == 1


def test_concurrent_reservations_never_double_admit(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    budget_id = budget_id_for(_PROJECT, "prop-concurrent")
    ledger.define_research_budget(
        parse_budget_policy(
            {"units": {"task_ref": 1}},
            budget_id=budget_id,
            approved_budget={"units": {"task_ref": 1}},
            project_id=_PROJECT,
        )
    )
    admitted: list[str] = []
    refused: list[str] = []
    barrier = threading.Barrier(4)

    def worker(index: int) -> None:
        barrier.wait()
        decision = ledger.reserve_research_budget(
            budget_id,
            {"task_ref": 1},
            reservation_id=f"bres_thread_{index}",
            task_ref=f"task-{index}",
        )
        (admitted if decision.admitted else refused).append(decision.reservation_id or "")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(admitted) == 1
    assert len(refused) == 3
    projection = ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert len(projection.reservations) == 1


def test_run_id_change_does_not_reset_the_allowance(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    budget_id = budget_id_for(_PROJECT, "prop-runid")
    ledger.define_research_budget(
        parse_budget_policy(
            {"units": {"task_ref": 1}},
            budget_id=budget_id,
            approved_budget={"units": {"task_ref": 1}},
            project_id=_PROJECT,
        )
    )
    first = ledger.reserve_research_budget(
        budget_id, {"task_ref": 1}, reservation_id="bres_run_a", task_ref="task-a", run_id="run-a"
    )
    assert first.admitted is True
    # A new run_id is not a new budget: the identity is project + proposal_ref.
    second = ledger.reserve_research_budget(
        budget_id, {"task_ref": 1}, reservation_id="bres_run_b", task_ref="task-b", run_id="run-b"
    )
    assert second.admitted is False
    assert second.reason_code == BudgetReason.EXHAUSTED
    assert budget_id_for(_PROJECT, "prop-runid") == budget_id


def test_process_restart_keeps_the_reservation(tmp_path: Path) -> None:
    path = tmp_path / "admission.db"
    budget_id = budget_id_for(_PROJECT, "prop-restart")
    definition = {"units": {"task_ref": 1, "attempt": 1}, "wall_seconds": 600}
    first_process = build_local_admission(str(path))
    decision = first_process.admit_job(
        "job-restart", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert decision.allowed is True

    # A fresh process (new ledger object over the same file) sees the charge;
    # no allowance is reset and the row is still a live reservation.
    restarted = build_local_admission(str(path))
    projection = restarted.ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert len(projection.reservations) == 1
    assert projection.reservations[0].state == BudgetState.RESERVED
    assert projection.consumed["task_ref"] == 1

    other = restarted.admit_job(
        "job-restart-2", _spec(), project_id=_PROJECT, budget=_budget_claim(budget_id, definition)
    )
    assert other.admitted is False
    assert other.reason_code == BudgetReason.EXHAUSTED


# ---------------------------------------------------------------------------
# planning reservations (no TaskRef)
# ---------------------------------------------------------------------------


def test_planning_reservation_needs_no_task_ref(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)
    assert outcome.admitted is True
    assert claim.budget_id.startswith("rb_")
    reservation = ledger.research_budget_for_claim(claim.claim_id)
    assert reservation is not None
    assert reservation.task_ref == ""
    assert reservation.run_id == ""
    assert reservation.state == BudgetState.RESERVED
    # The planning book is not an execution fact: no job row can be derived.
    assert "job" not in ledger.research_budget_projection(claim.budget_id).to_dict()


def test_planning_claim_unique_index_exists_and_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "admission.db"
    ledger = ReservationLedger(str(path))
    conn = sqlite3.connect(str(path))
    try:
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                ("research_budget_reservations",),
            )
        }
    finally:
        conn.close()
    assert "idx_research_budget_claim" in indexes

    claim = _claim()
    first = reserve_planning_call(ledger, claim)
    second = reserve_planning_call(ledger, claim)
    assert first.reservation.reservation_id == second.reservation.reservation_id
    assert second.decision.detail == "already_reserved"
    projection = ledger.research_budget_projection(claim.budget_id)
    assert projection is not None
    assert len(projection.reservations) == 1

    # A different reservation id with the same claim is refused by the partial
    # unique index, not silently doubled.
    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO research_budget_reservations (
                       reservation_id, budget_id, units_json, state, task_ref, run_id,
                       claim_id, admission_reservation_id, detail, created_at, updated_at)
                   VALUES ('bres_dup', ?, '{"model_request": 1}', 'reserved', '', '',
                           ?, '', 'duplicate', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
                (claim.budget_id, claim.claim_id),
            )
    finally:
        conn.close()


def test_interrupted_planning_call_stays_reserved(tmp_path: Path) -> None:
    path = tmp_path / "admission.db"
    ledger = ReservationLedger(str(path))
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)
    retention = retain_unknown_planning_call(
        outcome.reservation, reason="stream interrupted before the provider replied"
    )
    assert retention.released is False
    assert retention.state == PLANNING_RESERVATION_STATE_RETAINED
    assert retention.to_dict()["rule"].startswith("unknown keeps the charge")

    restarted = ReservationLedger(str(path))
    reconciliation = reconcile_planning(restarted, claim.budget_id)
    assert reconciliation.reserved == 1
    assert reconciliation.settled == 0
    assert reconciliation.released == 0
    assert reconciliation.reservations[0].state == PLANNING_RESERVATION_STATE_RESERVED


def test_scientific_approval_rejection_keeps_incurred_model_usage(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    approvals = ApprovalService(tmp_path / "jobs.db")
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)
    assert outcome.admitted is True

    pending, _ = approvals.request(
        proposal_ref="prop-rejected-science",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        project_id=_PROJECT,
        requester_principal="principal_alpha",
    )
    rejected, _ = approvals.reject(
        pending.approval_id,
        approver_principal="principal_reviewer",
        expected_revision=pending.revision,
        reason="the scientific plan is not acceptable",
    )
    assert rejected.state == "rejected"

    # The model call already happened: a rejected science approval must not
    # refund it (there is no release path from the approval at all).
    projection = ledger.research_budget_projection(claim.budget_id)
    assert projection is not None
    assert projection.consumed["model_request"] == 1
    assert reconcile_planning(ledger, claim.budget_id).reserved == 1


def test_missing_usage_settles_conservatively(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)

    # A receipt without usage numbers is not evidence of "zero spend": the
    # reserved worst case stays charged.
    settlement = settle_planning_call(
        ledger,
        outcome.reservation,
        receipt_id=claim.model_receipt_id,
        counters=UsageUnitCounts(),
    )
    assert settlement.applied is True
    projection = ledger.research_budget_projection(claim.budget_id)
    assert projection is not None
    assert projection.consumed["model_request"] == 1
    reconciliation = reconcile_planning(ledger, claim.budget_id)
    assert reconciliation.settled == 1
    assert reconciliation.reserved == 0


def test_planning_reservation_requires_a_claim_and_units(tmp_path: Path) -> None:
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    with pytest.raises(PlanningReservationError):
        reserve_planning_call(ledger, "not-a-claim")  # type: ignore[arg-type]
    with pytest.raises(PlanningReservationError):
        reserve_planning_call(ledger, _claim(), units=0)
