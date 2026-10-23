"""B17 — persistent cumulative research budgets, reservations and settlement.

The durable ledger is the existing ``ReservationLedger`` SQLite database.  These
tests prove pre-dispatch refusal, the concurrent last-unit race, restart
survival, idempotent settlement, release rules, separate unit/timeout counters
and the bounded read-only evidence export.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from acp_services.runtime import metering_policy as mp
from acp_services.runtime import quota as q
from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.research_budget import (
    BudgetConfigurationError,
    BudgetReason,
    BudgetState,
    budget_id_for,
    evaluate_budget_boundary,
    parse_budget_policy,
    release_statement,
)
from acp_services.runtime.usage_units import UsageUnitCounts, UsageUnitValidationError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _REPO_ROOT / "src" / "acp_services" / "runtime"


def _ledger(tmp_path: Path, name: str = "budget.db") -> ReservationLedger:
    return ReservationLedger(str(tmp_path / name))


def _policy(budget_id: str, *, count: int = 1, **extra: object):
    budget = {"unit": "task_count", "count": count, "timeout_s": 60, **extra}
    return parse_budget_policy(
        budget,
        budget_id=budget_id,
        approved_budget=budget,
        approval_ref="ap_test",
        project_id="project_alpha",
    )


def _submit_units() -> dict[str, int]:
    return {"task_ref": 1, "attempt": 1}


# --------------------------------------------------------------------------- #
# Identity / policy
# --------------------------------------------------------------------------- #


def test_budget_id_is_project_and_proposal_not_run_id() -> None:
    first = budget_id_for("project_alpha", "prop-main")
    assert first == budget_id_for("project_alpha", "prop-main")
    assert first != budget_id_for("project_beta", "prop-main")
    assert first != budget_id_for("project_alpha", "prop-other")
    assert "run" not in first


def test_policy_keeps_four_timeout_classes_separate() -> None:
    policy = parse_budget_policy(
        {
            "units": {"task_ref": 2},
            "timeouts": {
                "queue_wait_s": 5,
                "science_runtime_s": 900,
                "client_request_s": 30,
                "overall_acceptance_s": 1200,
            },
        },
        budget_id="rb_timeouts",
    )
    assert policy.timeouts == {
        "queue_wait_s": 5.0,
        "science_runtime_s": 900.0,
        "client_request_s": 30.0,
        "overall_acceptance_s": 1200.0,
    }


def test_policy_cannot_exceed_the_approved_range() -> None:
    with pytest.raises(BudgetConfigurationError, match="exceeds the approved range"):
        parse_budget_policy(
            {"unit": "task_count", "count": 3},
            budget_id="rb_over",
            approved_budget={"unit": "task_count", "count": 1},
        )


# --------------------------------------------------------------------------- #
# Pre-dispatch reserve / refusal / concurrency
# --------------------------------------------------------------------------- #


def test_reserve_refuses_before_dispatch_and_leaves_no_partial_row(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-one")
    ledger.define_research_budget(_policy(budget_id))
    first = ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_1", task_ref="task-1"
    )
    assert first.admitted
    second = ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_2", task_ref="task-2"
    )
    assert not second.admitted
    assert second.reason_code == BudgetReason.EXHAUSTED
    assert second.exhausted_unit == "task_ref"
    projection = ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert len(projection.reservations) == 1
    assert projection.consumed["task_ref"] == 1
    assert projection.remaining["task_ref"] == 0


def test_concurrent_last_unit_yields_exactly_one_admission(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-race")
    ledger.define_research_budget(_policy(budget_id))
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def worker(index: int) -> None:
        barrier.wait()
        decision = ledger.reserve_research_budget(
            budget_id,
            _submit_units(),
            reservation_id=f"bres_race_{index}",
            task_ref=f"task-{index}",
        )
        results.append(decision.admitted)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(results) == 1
    projection = ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert projection.consumed["task_ref"] == 1


def test_claim_replay_does_not_reserve_twice(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-claim")
    ledger.define_research_budget(_policy(budget_id))
    first = ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_claim", claim_id="claim-1"
    )
    replay = ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_claim_2", claim_id="claim-1"
    )
    assert first.admitted and replay.admitted
    assert replay.detail == "already_reserved"
    projection = ledger.research_budget_projection(budget_id)
    assert projection is not None
    assert projection.consumed["task_ref"] == 1


# --------------------------------------------------------------------------- #
# Restart / settlement idempotency
# --------------------------------------------------------------------------- #


def test_restart_preserves_reserved_and_consumed(tmp_path: Path) -> None:
    budget_id = budget_id_for("project_alpha", "prop-restart")
    first = _ledger(tmp_path, "restart.db")
    first.define_research_budget(_policy(budget_id, count=2))
    first.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_1", task_ref="task-1"
    )
    first.settle_research_budget(
        budget_id,
        receipt_id="receipt-1",
        reservation_id="bres_1",
        counters=UsageUnitCounts(qc_invocation=2),
    )

    restarted = _ledger(tmp_path, "restart.db")
    projection = restarted.research_budget_projection(budget_id)
    assert projection is not None
    assert projection.consumed["task_ref"] == 1
    assert projection.consumed["qc_invocation"] == 2
    assert projection.receipts == 1
    assert projection.reservations[0].state == BudgetState.SETTLED


def test_duplicate_receipt_cannot_double_charge(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-duplicate")
    ledger.define_research_budget(_policy(budget_id, count=2))
    ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_1", task_ref="task-1"
    )
    counters = UsageUnitCounts(qc_invocation=1, bsub=0, model_request=3)
    first = ledger.settle_research_budget(
        budget_id, receipt_id="receipt-1", reservation_id="bres_1", counters=counters
    )
    second = ledger.settle_research_budget(
        budget_id, receipt_id="receipt-1", reservation_id="bres_1", counters=counters
    )
    assert first.applied and not first.duplicate
    assert not second.applied and second.duplicate
    assert second.reason_code == BudgetReason.DUPLICATE
    assert first.consumed == second.consumed
    assert first.consumed["qc_invocation"] == 1
    assert first.consumed["model_request"] == 3


# --------------------------------------------------------------------------- #
# Release rules
# --------------------------------------------------------------------------- #


def test_cancel_before_dispatch_releases_but_after_dispatch_holds(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-release")
    ledger.define_research_budget(_policy(budget_id, count=2))
    ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_queued", task_ref="task-queued"
    )
    released = ledger.release_research_budget(
        reservation_id="bres_queued", event="cancel_before_dispatch"
    )
    assert released.released
    assert ledger.research_budget_projection(budget_id).consumed["task_ref"] == 0

    ledger.reserve_research_budget(
        budget_id,
        _submit_units(),
        reservation_id="bres_running",
        task_ref="task-running",
        admission_reservation_id="res_running",
    )
    assert ledger.mark_research_budget_dispatched("res_running")
    held = ledger.release_research_budget(
        admission_reservation_id="res_running", event="cancel_after_dispatch"
    )
    assert not held.released
    assert ledger.research_budget_projection(budget_id).consumed["task_ref"] == 1


def test_publication_failure_never_recharges_computation(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-publication")
    ledger.define_research_budget(_policy(budget_id, count=2))
    ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_1", task_ref="task-1"
    )
    before = ledger.research_budget_projection(budget_id).consumed
    decision = ledger.release_research_budget(reservation_id="bres_1", event="publication_failure")
    after = ledger.research_budget_projection(budget_id).consumed
    assert not decision.released
    assert decision.reason_code == BudgetReason.HELD
    assert before == after


# --------------------------------------------------------------------------- #
# Units / boundary / release statement / evidence
# --------------------------------------------------------------------------- #


def test_unit_counts_are_separate_and_validated() -> None:
    counts = UsageUnitCounts.from_mapping(
        {"task_count": 1, "recipe_step": 2, "qc_invocation_count": 3, "bsub": 0, "model_call": 4}
    )
    assert counts.to_dict() == {
        "task_ref": 1,
        "recipe_step": 2,
        "qc_invocation": 3,
        "attempt": 0,
        "bsub": 0,
        "model_request": 4,
    }
    with pytest.raises(UsageUnitValidationError):
        UsageUnitCounts.from_mapping({"unknown_unit": 1})


def test_boundary_at_cap_pass_over_cap_and_missing_fail() -> None:
    at_cap = evaluate_budget_boundary(
        limits={"task_ref": 1, "qc_invocation": 2},
        consumed={"task_ref": 1, "qc_invocation": 2},
        required_operations={"task_ref": True, "qc_invocation": True},
    )
    assert at_cap.passed and at_cap.at_cap
    over = evaluate_budget_boundary(
        limits={"task_ref": 1}, consumed={"task_ref": 2}, required_operations={"task_ref": True}
    )
    assert over.status == "FAIL" and over.over_cap
    missing = evaluate_budget_boundary(
        limits={"task_ref": 1},
        consumed={"task_ref": 1},
        required_operations={"task_ref": True, "qc_invocation": False},
    )
    assert missing.status == "FAIL" and missing.missing_operations == ("qc_invocation",)


def test_release_statement_separates_enforced_recorded_not_verified() -> None:
    statement = release_statement()
    assert "task_ref" in statement["enforced"]
    assert "bsub" in statement["recorded_only"]
    assert "model_request" in statement["recorded_only"]
    capabilities = {row["capability"] for row in statement["not_verified"]}
    assert "model_token_enforcement" in capabilities
    assert statement["notes"]["model_tokens"].startswith("model-token enforcement is NOT_VERIFIED")


def test_bounded_read_only_evidence_survives_science_stop(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    budget_id = budget_id_for("project_alpha", "prop-evidence")
    ledger.define_research_budget(_policy(budget_id, count=10))
    ledger.reserve_research_budget(
        budget_id, _submit_units(), reservation_id="bres_1", task_ref="task-1"
    )
    ledger.settle_research_budget(
        budget_id,
        receipt_id="receipt-1",
        reservation_id="bres_1",
        counters=UsageUnitCounts(qc_invocation=1),
    )
    document = ledger.research_budget_evidence(budget_id, max_bytes=4096)
    assert document is not None
    assert document["read_only"] is True
    assert len(json.dumps(document, sort_keys=True)) <= 4096
    assert document["budget"]["consumed"]["task_ref"] == 1


# --------------------------------------------------------------------------- #
# Quota core-hour ledger: research-round scope on the same database
# --------------------------------------------------------------------------- #


def test_quota_research_round_scope_uses_existing_ledger(tmp_path: Path) -> None:
    ledger = q.QuotaLedger(str(tmp_path / "quota.db"))
    ledger.set_budget("research_round", "round-1", 2)
    policy = mp.MeteringPolicy(
        policy_version="pv",
        basis=mp.MeteringBasis.ALLOCATED_CORE_HOURS,
        rounding=mp.RoundingSpec(rule=mp.RoundingRule.HALF_UP, decimal_places=3),
        period=mp.BillingPeriod(unit=mp.PeriodUnit.MONTH, timezone="UTC"),
    )
    service = q.QuotaService(ledger)
    service.startup(
        q.QuotaModeSelection(mode=q.QuotaMode.QUOTA_ONLY, selected_by="operator", policy=policy)
    )
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for index in range(2):
        decision = service.admit(
            job_id=f"job-{index}",
            principal_id="user",
            project_id="project",
            cores=1,
            walltime_s=3600,
            window_start=now,
            window_end=now + timedelta(hours=1),
            research_round="round-1",
        )
        assert decision.admitted
    refused = service.admit(
        job_id="job-3",
        principal_id="user",
        project_id="project",
        cores=1,
        walltime_s=3600,
        window_start=now,
        window_end=now + timedelta(hours=1),
        research_round="round-1",
    )
    assert not refused.admitted
    assert service.available("user", "project", "round-1") == 0


# --------------------------------------------------------------------------- #
# Isolation / no scientific or commercial vocabulary
# --------------------------------------------------------------------------- #


def test_budget_modules_never_import_acp_and_carry_no_price_fields() -> None:
    for name in ("research_budget.py", "usage_units.py"):
        tree = ast.parse((_RUNTIME / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("acp") for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("acp.") and node.module != "acp"
        terms: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                terms.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                terms.add(node.attr.lower())
            elif isinstance(node, ast.arg):
                terms.add(node.arg.lower())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                terms.add(node.name.lower())
        for forbidden in ("price", "credit", "balance", "rate_per"):
            assert not any(forbidden in term for term in terms)


def test_budget_reservation_is_not_written_into_task_spec(tmp_path: Path) -> None:
    from acp_services.store.records import JobSpec

    spec = JobSpec(workflow="singlepoint", name="water")
    assert "budget" not in spec.to_dict()
    assert "budget_id" not in json.dumps(spec.to_dict())
