"""M1-04b: three clocks, one budget shape, one resource resolver, one checker.

Covers the frozen clock semantics (queue vs wall vs science runtime, PAUSED and
restart), the D09 budget-shape mappings (``wall_seconds`` / ``queue_seconds`` /
tokens / ``core_hours`` are never silently dropped and never become "enforced"),
the shared resource resolver (local termination + CCCP subprocess timeout + LSF
``-W``, empty cluster walltime is *not* a limit), the split admission semantics
and the acceptance checker (consistent today, failing on a mutated matrix).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp_services.runtime.execution_limits import (
    CLOCK_CONTRACT,
    CLOCK_PRECEDENCE,
    EXECUTION_LIMITS,
    AttemptClock,
    ExecutionLimitError,
    bind_budget_shape,
    clock_projection,
    compute_attempt_clock,
    execution_limits_statement,
    limit_row,
    normalize_walltime_s,
    resolve_resource_limits,
)
from acp_services.runtime.research_budget import UNIT_CAPABILITIES, release_statement

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CHECKER = _REPO_ROOT / "scripts/acceptance/check_execution_limits_consistency.py"
_UTC = timezone.utc


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_execution_limits", _CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# capability matrix (no upgrade by accident)
# ---------------------------------------------------------------------------


def test_unit_matrix_is_unchanged_and_truthful() -> None:
    assert UNIT_CAPABILITIES == {
        "task_ref": "enforced",
        "attempt": "enforced",
        "recipe_step": "recorded-only",
        "qc_invocation": "recorded-only",
        "bsub": "recorded-only",
        "model_request": "recorded-only",
    }


def test_only_the_admission_bound_walltime_claim_is_enforced() -> None:
    enforced = [row.limit for row in EXECUTION_LIMITS if row.capability == "enforced"]
    assert enforced == ["walltime_admission_bound"]
    termination = limit_row("walltime_process_termination")
    assert termination.capability == "NOT_VERIFIED"
    assert termination.kind == "process-termination"
    assert limit_row("queue_deadline").kind == "queue-deadline"
    assert limit_row("science_runtime_recorded").capability == "recorded-only"
    assert limit_row("model_tokens").capability == "recorded-only"


def test_clocks_are_published_with_their_precedence() -> None:
    clocks = {row["clock"] for row in CLOCK_CONTRACT}
    assert clocks == {"queue", "wall", "science-runtime"}
    ranks = {row["rank"]: row["clock"] for row in CLOCK_PRECEDENCE}
    assert ranks["1"] == "queue"
    assert ranks["2"] == "wall"
    assert ranks["3"] == "science-runtime"
    # The client/RPH rule is explicit: a wait timeout is not a platform clock.
    client_rule = next(row for row in CLOCK_PRECEDENCE if row["clock"] == "client/rph")
    assert "may not choose" in client_rule["rule"]


def test_wall_clock_defaults_to_including_paused_time() -> None:
    started = datetime(2026, 1, 1, 12, 0, tzinfo=_UTC)
    clock = compute_attempt_clock(
        admitted_at=started - timedelta(minutes=5),
        started_at=started,
        wall_seconds=600,
        paused_seconds=300,
    )
    assert clock.include_paused is True
    # The pause does not extend the deadline: it is still started + 600s.
    assert clock.wall_deadline_at == started + timedelta(seconds=600)
    assert clock.remaining_seconds(started + timedelta(seconds=150)) == 450


def test_restart_restores_only_the_remaining_time() -> None:
    started = datetime(2026, 1, 1, 12, 0, tzinfo=_UTC)
    first = compute_attempt_clock(admitted_at=started, started_at=started, wall_seconds=600)
    restart_at = started + timedelta(seconds=420)
    persisted = AttemptClock(
        admitted_at=first.admitted_at,
        started_at=first.started_at,
        queue_deadline_at=first.queue_deadline_at,
        wall_deadline_at=first.wall_deadline_at,
    )
    assert persisted.remaining_seconds(restart_at) == 180
    # A restart never re-anchors the absolute deadline.
    rebuilt = compute_attempt_clock(
        admitted_at=persisted.admitted_at or started,
        started_at=restart_at,
        wall_seconds=600,
    )
    assert rebuilt.wall_deadline_at != persisted.wall_deadline_at


def test_queue_clock_governs_only_before_start() -> None:
    admitted = datetime(2026, 1, 1, 12, 0, tzinfo=_UTC)
    clock = compute_attempt_clock(admitted_at=admitted, queue_seconds=60)
    queued = clock_projection(clock, admitted + timedelta(seconds=30))
    assert queued.governing_clock == "queue"
    assert queued.expired is False
    expired = clock_projection(clock, admitted + timedelta(seconds=61))
    assert expired.governing_clock == "queue"
    assert expired.expired is True

    started = compute_attempt_clock(
        admitted_at=admitted, started_at=admitted + timedelta(seconds=10), wall_seconds=60
    )
    running = clock_projection(started, admitted + timedelta(seconds=70))
    assert running.governing_clock == "wall"
    assert running.expired is True


# ---------------------------------------------------------------------------
# budget shape -> resource resolver
# ---------------------------------------------------------------------------


def test_wall_seconds_maps_to_all_three_termination_surfaces() -> None:
    limits = resolve_resource_limits({"wall_seconds": 3700})
    assert limits.local_terminate_after_s == 3700
    assert limits.ccp_resources_timeout_s == 3700.0
    assert limits.lsf_walltime_minutes == 62
    assert limits.bounded is True
    payload = limits.to_dict()
    assert payload["lsf_walltime_limited"] is True


def test_timeout_s_is_the_same_resolver_input() -> None:
    assert resolve_resource_limits({"timeout_s": 60}).local_terminate_after_s == 60


def test_empty_cluster_walltime_is_not_a_limit() -> None:
    assert normalize_walltime_s(None) is None
    assert normalize_walltime_s("") is None
    assert normalize_walltime_s(0) is None
    unbounded = resolve_resource_limits({})
    assert unbounded.local_terminate_after_s is None
    assert unbounded.lsf_walltime_minutes is None
    assert unbounded.to_dict()["lsf_walltime_limited"] is False


def test_override_never_widens_the_approved_walltime() -> None:
    limits = resolve_resource_limits({"wall_seconds": 600}, timeout_override_s=3600)
    assert limits.local_terminate_after_s == 600


def test_queue_seconds_maps_to_the_queue_deadline() -> None:
    limits = resolve_resource_limits({"wall_seconds": 600, "queue_seconds": 120})
    assert limits.queue_deadline_s == 120


def test_tokens_and_core_hours_are_recorded_only() -> None:
    shape = bind_budget_shape({"input_tokens": 1000, "output_tokens": 500, "core_hours": 3})
    assert shape.recorded_only == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "core_hours": 3,
    }
    limits = resolve_resource_limits({"input_tokens": 1000, "output_tokens": 500, "core_hours": 3})
    assert limits.recorded_only["input_tokens"] == 1000
    # Recorded-only never turns into a termination bound.
    assert limits.local_terminate_after_s is None


def test_unknown_non_empty_budget_shape_is_a_typed_error() -> None:
    with pytest.raises(ExecutionLimitError) as excinfo:
        bind_budget_shape({"wall_clock": 60})
    assert excinfo.value.rule == "unknown_budget_shape"
    assert excinfo.value.detail()["params"]["fields"] == ["wall_clock"]


def test_non_empty_budget_never_parses_into_empty_limits() -> None:
    from acp_services.runtime.research_budget import BudgetConfigurationError, parse_budget_policy

    with pytest.raises(BudgetConfigurationError):
        parse_budget_policy({"mystery_budget": 5}, budget_id="rb_unknown")
    policy = parse_budget_policy(
        {"unit": "task_count", "count": 1, "wall_seconds": 900}, budget_id="rb_shape"
    )
    assert policy.to_dict()["execution_limits"]["wall_seconds"] == 900
    assert policy.limits == {"task_ref": 1}


def test_release_statement_carries_the_execution_limits_section() -> None:
    statement = release_statement()
    section = statement["execution_limits"]
    assert section["schema"] == "acp2.runtime.execution-limits.v1"
    assert section["enforced"] == ["walltime_admission_bound"]
    assert "walltime_process_termination" in {row["capability"] for row in section["not_verified"]}
    assert statement["enforced"] == ["task_ref", "attempt"]


def test_statement_shape_is_generated_from_one_matrix() -> None:
    section = execution_limits_statement()
    assert len(section["limits"]) == len(EXECUTION_LIMITS)
    assert section["budget_shape"]["unknown_non_empty"].startswith("typed error")


# ---------------------------------------------------------------------------
# step DAG: no implicit recovery allowance
# ---------------------------------------------------------------------------


def test_step_dag_has_no_implicit_recovery_allowance() -> None:
    import inspect

    from acp_services.runtime.step_dag import BoundedStepDagExecutor

    signature = inspect.signature(BoundedStepDagExecutor.__init__)
    assert signature.parameters["recovery_allowance"].default == 0
    source = inspect.getsource(BoundedStepDagExecutor.__init__)
    # M2-04c completes the M1-04收尾: the QC/attempt caps derive from the
    # compiled worst case plus an *explicit* allowance.  Neither a per-step
    # ``+ len(dag.steps)`` widening nor a per-step ``step.qc_invocations`` sum
    # may come back — the reservation is the only source of allowance.
    assert "worst_case_total = sum(" in source
    assert "qc_invocations=worst_case_total + allowance" in source
    assert "qc_invocations=len(dag.steps)" not in source
    assert "sum(step.qc_invocations for step in dag.steps)" not in source


# ---------------------------------------------------------------------------
# acceptance checker
# ---------------------------------------------------------------------------


def test_checker_passes_on_the_current_tree() -> None:
    checker = _load_checker()
    report = checker.build_report(_REPO_ROOT)
    assert report["summary"]["consistent"] is True, report["findings"]
    assert report["summary"]["units"] == len(UNIT_CAPABILITIES)
    assert report["summary"]["execution_limits"] == len(EXECUTION_LIMITS)
    assert set(report["sources"]["release_statement_units"]) >= set(UNIT_CAPABILITIES)


def test_checker_fails_when_the_api_upgrades_a_unit_capability() -> None:
    # Simulate the real defect the checker exists for: one surface (the live
    # discovery payload) publishes a unit as ``enforced`` while the module matrix
    # still says ``recorded-only``.
    checker = _load_checker()
    original = checker._live_capabilities

    def _mutated() -> dict:
        payload = original()
        admission = dict(payload["admission"])
        budget = dict(admission["research_budget"])
        budget["recorded_only"] = [
            unit for unit in budget["recorded_only"] if unit != "model_request"
        ]
        budget["enforced"] = [*budget["enforced"], "model_request"]
        admission["research_budget"] = budget
        payload["admission"] = admission
        return payload

    checker._live_capabilities = _mutated
    try:
        report = checker.build_report(_REPO_ROOT)
    finally:
        checker._live_capabilities = original
    assert report["summary"]["consistent"] is False
    findings = report["findings"]
    assert any(
        finding.get("kind") == "capability_mismatch"
        and finding.get("unit") == "model_request"
        and finding.get("actual") == "enforced"
        for finding in findings
    ), findings


def test_checker_exit_codes() -> None:
    checker = _load_checker()
    assert checker.main(["--check"]) == checker.EXIT_OK
