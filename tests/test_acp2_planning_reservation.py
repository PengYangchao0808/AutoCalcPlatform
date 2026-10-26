"""M1-04c: the pre-proposal planning reservation on the existing ledger.

Covers the frozen planning identity (research/session, never ``proposal_ref`` and
never a job/attempt), the durable reservation replay, the "unknown stays
charged" rule, settlement, reconciliation and the two-phase link to the
execution budget — all on the one ``ReservationLedger`` (no second ledger).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.planning_reservation import (
    PLANNING_BUDGET_PREFIX,
    PlanningReservationError,
    link_execution_budget,
    planning_budget_id_for,
    planning_budget_policy,
    planning_reservation_id,
    reconcile_planning,
    reserve_planning_call,
    retain_unknown_planning_call,
    settle_planning_call,
)
from acp_services.runtime.research_budget import budget_id_for
from acp_services.runtime.research_session import ResearchIdentity, planning_claim
from acp_services.workspace.command_identity import CommandIdentity, SubjectKind


def _identity() -> ResearchIdentity:
    return ResearchIdentity.allocate(
        CommandIdentity(principal_id="principal_alpha", subject_kind=SubjectKind.USER),
        session_seed="m1-04c",
    )


def _claim(round_no: int = 0, call_seq: int = 1):
    return planning_claim(
        _identity(),
        round=round_no,
        call_seq=call_seq,
        request={"model": "approved-model", "messages": [{"role": "user", "content": "plan"}]},
    )


def _ledger(tmp_path: Path) -> ReservationLedger:
    return ReservationLedger(str(tmp_path / "admission.db"))


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_planning_budget_identity_is_research_session_not_proposal() -> None:
    claim = _claim()
    identity = _identity()
    expected = budget_id_for(
        identity.project_id, f"{PLANNING_BUDGET_PREFIX}{identity.research_id}:{identity.session_id}"
    )
    assert claim.budget_id == expected
    assert (
        planning_budget_id_for(identity.project_id, identity.research_id, identity.session_id)
        == expected
    )
    assert "planning:" in f"{PLANNING_BUDGET_PREFIX}{identity.research_id}:{identity.session_id}"
    # Never a proposal_ref: a proposal does not exist yet.
    assert claim.budget_id != budget_id_for(identity.project_id, "prop-does-not-exist")


def test_claim_carries_server_derived_digests_and_no_execution_fact() -> None:
    claim = _claim(round_no=2, call_seq=3)
    assert claim.claim_id.startswith("plan|")
    assert claim.request_digest.startswith("sha256:")
    assert claim.model_receipt_id.startswith("modelrcpt|")
    payload = claim.to_dict()
    assert payload["is_submit_ledger_key"] is False
    assert "job_id" not in payload
    assert "attempt" not in payload


def test_reservation_id_is_stable_and_non_empty() -> None:
    first = planning_reservation_id(_claim())
    second = planning_reservation_id(_claim())
    assert first == second
    assert first.startswith("bres_plan_")
    assert len(first) > len("bres_plan_")


def test_policy_is_the_planning_budget() -> None:
    claim = _claim()
    policy = planning_budget_policy(claim, model_requests=2)
    assert policy.budget_id == claim.budget_id
    assert policy.limits == {"model_request": 2}
    assert policy.approval_ref == ""


# ---------------------------------------------------------------------------
# reserve / replay / retain / settle
# ---------------------------------------------------------------------------


def test_planning_reservation_is_durable_and_replayed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    claim = _claim()
    first = reserve_planning_call(ledger, claim)
    assert first.admitted is True
    assert first.reservation.job_id == ""
    assert first.reservation.attempt == 0
    assert first.reservation.claim_id == claim.claim_id

    replay = reserve_planning_call(ledger, claim)
    assert replay.reservation.reservation_id == first.reservation.reservation_id
    assert replay.decision.detail == "already_reserved"

    # A restart reads the same durable row (no in-memory state).
    reopened = ReservationLedger(str(tmp_path / "admission.db"))
    projection = reopened.research_budget_projection(claim.budget_id)
    assert projection is not None
    assert len(projection.reservations) == 1


def test_unknown_outcome_stays_charged_and_never_releases(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)
    retention = retain_unknown_planning_call(outcome.reservation, reason="stream_interrupted")
    assert retention.released is False
    assert retention.state == "retained-unknown"

    reconciliation = reconcile_planning(ledger, claim.budget_id)
    assert reconciliation.settled == 0
    assert reconciliation.reserved == 1
    assert reconciliation.released == 0
    assert reconciliation.outstanding == 1
    # The allowance was not reset: a second claim of the same size still sees the
    # first one outstanding.
    other = planning_claim(
        _identity(),
        round=1,
        call_seq=1,
        request={"messages": [{"role": "user", "content": "again"}]},
    )
    second = reserve_planning_call(ledger, other)
    assert second.admitted is True
    assert second.reservation.reservation_id != outcome.reservation.reservation_id
    assert reconcile_planning(ledger, claim.budget_id).outstanding == 2


def test_settlement_marks_the_reservation_settled(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    claim = _claim()
    outcome = reserve_planning_call(ledger, claim)
    settlement = settle_planning_call(ledger, outcome.reservation, receipt_id="rcpt-planning-1")
    assert settlement.applied is True
    assert settlement.reason_code == "budget_settled"
    reconciliation = reconcile_planning(ledger, claim.budget_id)
    assert reconciliation.settled == 1
    assert reconciliation.reserved == 0

    # A duplicate receipt charges nothing new.
    duplicate = settle_planning_call(ledger, outcome.reservation, receipt_id="rcpt-planning-1")
    assert duplicate.duplicate is True or duplicate.applied is False
    assert reconcile_planning(ledger, claim.budget_id).settled == 1


def test_settlement_requires_a_receipt(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    outcome = reserve_planning_call(ledger, _claim())
    with pytest.raises(PlanningReservationError) as excinfo:
        settle_planning_call(ledger, outcome.reservation, receipt_id="")
    assert excinfo.value.rule == "missing_receipt"


def test_reservation_rejects_a_fabricated_execution_fact() -> None:
    from acp_services.runtime.planning_reservation import PlanningReservation

    claim = _claim()
    with pytest.raises(PlanningReservationError) as excinfo:
        PlanningReservation(
            claim_id=claim.claim_id,
            budget_id=claim.budget_id,
            reservation_id="bres_plan_x",
            request_digest=claim.request_digest,
            model_receipt_id=claim.model_receipt_id,
            project_id=claim.project_id,
            research_id=claim.research_id,
            session_id=claim.session_id,
            round=claim.round,
            call_seq=claim.call_seq,
            job_id="job-1",
        )
    assert excinfo.value.rule == "fabricated_execution_fact"


def test_policy_budget_must_match_the_claim(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    claim = _claim()
    from acp_services.runtime.research_budget import BudgetPolicy

    with pytest.raises(PlanningReservationError) as excinfo:
        reserve_planning_call(
            ledger,
            claim,
            policy=BudgetPolicy(budget_id="rb_other", limits={"model_request": 1}),
        )
    assert excinfo.value.rule == "budget_identity_mismatch"


# ---------------------------------------------------------------------------
# two-phase association (planning book vs execution book)
# ---------------------------------------------------------------------------


def test_execution_link_keeps_the_books_separate() -> None:
    claim = _claim()
    execution_budget = budget_id_for(claim.project_id, "prop-da-selectivity-001")
    link = link_execution_budget(
        claim, execution_budget_id=execution_budget, approval_ref="ap_test"
    )
    payload = link.to_dict()
    assert payload["planning_budget_id"] == claim.budget_id
    assert payload["execution_budget_id"] == execution_budget
    assert payload["planning_budget_id"] != payload["execution_budget_id"]
    assert payload["association"] == "research+session (both books stay separate)"
    assert payload["new_ledger_created"] is False
    with pytest.raises(PlanningReservationError) as excinfo:
        link_execution_budget(claim, execution_budget_id=claim.budget_id)
    assert excinfo.value.rule == "collapsed_budget_identity"


# ---------------------------------------------------------------------------
# one ledger, no new store
# ---------------------------------------------------------------------------


def test_no_second_ledger_file_is_created(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    claim = _claim()
    reserve_planning_call(ledger, claim)
    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["admission.db"]
    with sqlite3.connect(tmp_path / "admission.db") as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "research_budget_reservations" in tables
    assert not any("planning" in name for name in tables)
