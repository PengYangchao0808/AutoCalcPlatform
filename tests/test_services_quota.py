"""B10-3 (todo 55) — usage-only / quota-only core-hour modes.

Exercises ``acp_services.runtime.quota``:

1. **Explicit mode selection.**  Startup without an explicit selection is refused;
   ``usage-only`` additionally requires an explicit administrator selection.
2. **usage-only records/alerts only** and never reserves budget.
3. **quota-only** (default research baseline) reserves an **executable** upper
   bound (``cores × walltime / 3600``) before dispatch; an un-executable bound is
   refused and only explicit usage-only bypasses it.
4. **Effective budget is the min intersection** of the user and project scopes.
5. **Write-off against confirmed usage** releases the unused ceiling; a pending
   record keeps the reservation held (unknown usage reconciles).
6. **Over-run is refused**, never settled late (no over-use-then-settle).
7. **Cross-period segmentation** splits a reservation and its write-off by the
   frozen billing boundary.
8. **Restart recovery** replays undecided reservations; **lease expiry alone
   never releases**.
9. **Budget top-up ordering**: authorize + reserve first, then extend; a failed
   extension persists the authorization and hold and leaves no over-use.
10. **Exhaustion** refuses new dispatches, keeps in-flight work and results
    readable.
11. **Mode switch keeps history** (old policy snapshots are never cleared).
12. **Structural guards**: no ``acp`` import, no post-hoc progress/browser
    statistics, and no result deletion on exhaustion.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from acp_services.runtime import metering_policy as mp
from acp_services.runtime import quota as q
from acp_services.runtime import usage as u

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "quota.py"

UTC = timezone.utc
M = q.QuotaMode
S = q.QuotaState
R = q.QuotaReason

WIN_START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WIN_END = WIN_START + timedelta(hours=1)


def _policy(
    *,
    version: str = "pv-2026-10-19",
    unit: str = mp.PeriodUnit.MONTH,
    tz: str = "UTC",
) -> mp.MeteringPolicy:
    return mp.MeteringPolicy(
        policy_version=version,
        basis=mp.MeteringBasis.ALLOCATED_CORE_HOURS,
        rounding=mp.RoundingSpec(rule=mp.RoundingRule.HALF_UP, decimal_places=3),
        period=mp.BillingPeriod(unit=unit, timezone=tz),
    )


def _selection(
    *,
    mode: str = M.QUOTA_ONLY,
    selected_by: str = "operator",
    admin_selected: bool = False,
    policy: mp.MeteringPolicy | None = None,
) -> q.QuotaModeSelection:
    return q.QuotaModeSelection(
        mode=mode,
        selected_by=selected_by,
        admin_selected=admin_selected,
        policy=policy if policy is not None else _policy(),
    )


def _service(
    tmp_path: Path,
    *,
    user: dict[str, Decimal] | None = None,
    project: dict[str, Decimal] | None = None,
    db: str = "quota.db",
    alerts: list[str] | None = None,
) -> tuple[q.QuotaService, q.QuotaLedger]:
    ledger = q.QuotaLedger(str(tmp_path / db))
    service = q.QuotaService(
        ledger,
        user_budgets=user,
        project_budgets=project,
        alert_sink=(alerts.append if alerts is not None else None),
    )
    return service, ledger


def _usage_record(
    policy: mp.MeteringPolicy,
    *,
    start: datetime,
    end: datetime,
    cores: int,
    complete: bool = True,
    final: bool = True,
) -> u.UsageRecord:
    evidence = u.UsageEvidence(source=u.CollectionSource.LOCAL_POLICY_ALLOCATED, version="ev-1")
    interval = mp.UsageInterval(
        start=start,
        end=end,
        kind=mp.IntervalKind.RUNNING,
        allocated_cores=cores,
        evidence_source=evidence.source,
        evidence_version=evidence.version,
    )
    collected = u.CollectedUsage(
        level=u.ResourceLevel.PARENT,
        source=u.CollectionSource.LOCAL_POLICY_ALLOCATED,
        intervals=(interval,),
        evidence=evidence,
        allocated_cores=cores,
        complete=complete,
    )
    return u.build_usage_record(
        collected,
        u.UsageIdentity(job_id="20261001_001_optimize", attempt=1, project_id="proj-1"),
        policy,
        window_start=start,
        window_end=end,
        final=final,
    )


def _admit(service: q.QuotaService, *, cores: int, walltime_s: int, job_id: str = "job-1", **kw):
    return service.admit(
        job_id=job_id,
        principal_id=kw.pop("principal_id", "alice"),
        project_id=kw.pop("project_id", "proj-1"),
        cores=cores,
        walltime_s=walltime_s,
        window_start=kw.pop("window_start", WIN_START),
        window_end=kw.pop("window_end", WIN_END),
        **kw,
    )


# --------------------------------------------------------------------------- #
# 1. Explicit mode selection
# --------------------------------------------------------------------------- #


def test_no_explicit_selection_refuses_startup(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(q.QuotaStartupRefusedError) as excinfo:
        service.startup(None)
    assert excinfo.value.reason_code == R.MODE_NOT_SELECTED


def test_selection_requires_operator_and_policy() -> None:
    with pytest.raises(q.QuotaValidationError):
        q.QuotaModeSelection(mode=M.QUOTA_ONLY, selected_by="", policy=_policy())
    with pytest.raises(q.QuotaValidationError):
        q.QuotaModeSelection(mode=M.QUOTA_ONLY, selected_by="op", policy=None)
    with pytest.raises(q.QuotaValidationError):
        q.QuotaModeSelection(mode="nope", selected_by="op", policy=_policy())


def test_usage_only_requires_admin_selection(tmp_path: Path) -> None:
    with pytest.raises(q.QuotaValidationError):
        _selection(mode=M.USAGE_ONLY, admin_selected=False)
    service, _ = _service(tmp_path)
    started = service.startup(_selection(mode=M.USAGE_ONLY, admin_selected=True))
    assert started.mode == M.USAGE_ONLY
    assert service.mode == M.USAGE_ONLY


def test_startup_before_selection_refuses_admit(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(q.QuotaStartupRefusedError):
        _admit(service, cores=1, walltime_s=3600)


# --------------------------------------------------------------------------- #
# 2. usage-only records/alerts only
# --------------------------------------------------------------------------- #


def test_usage_only_records_and_alerts_without_reservation(tmp_path: Path) -> None:
    alerts: list[str] = []
    service, ledger = _service(
        tmp_path, user={"alice": Decimal("1")}, project={"proj-1": Decimal("1")}, alerts=alerts
    )
    service.startup(_selection(mode=M.USAGE_ONLY, admin_selected=True))

    decision = _admit(service, cores=999, walltime_s=99999)
    assert decision.admitted is True
    assert decision.reason_code == R.RECORD_ONLY
    assert decision.record_only is True
    assert decision.reservation_id is None
    assert decision.alerts and "usage-only" in decision.alerts[0]
    assert alerts == list(decision.alerts)
    # Nothing was reserved despite the tiny budget.
    assert ledger.list_reservations() == []
    assert service.available("alice", "proj-1") == Decimal("1")


# --------------------------------------------------------------------------- #
# 3. quota-only executable-cap reservation + refusal when not executable
# --------------------------------------------------------------------------- #


def test_quota_only_reserves_executable_cap(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))

    decision = _admit(service, cores=4, walltime_s=7200)
    assert decision.admitted is True
    assert decision.enforced is True
    # 4 cores × 7200 s / 3600 = 8.0 core-hours reserved up-front.
    assert decision.reserved_core_hours == Decimal("8")
    assert decision.reservation_id is not None
    reservation = service.reservation(decision.reservation_id)
    assert reservation is not None
    assert reservation.state == S.RESERVED
    assert reservation.mode == M.QUOTA_ONLY


def test_not_executable_bound_refused_admission(tmp_path: Path) -> None:
    service, ledger = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}, db="cap.db"
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))

    refused = _admit(service, cores=4, walltime_s=3600, hard_limit_capability="unavailable")
    assert refused.admitted is False
    assert refused.reason_code == "hard_limit_unavailable"
    assert refused.reservation_id is None

    unbounded = _admit(service, cores=4, walltime_s=None)  # type: ignore[arg-type]
    assert unbounded.admitted is False
    assert unbounded.reason_code == "unbounded_request"
    assert ledger.list_reservations() == []


# --------------------------------------------------------------------------- #
# 4. Effective budget = min intersection
# --------------------------------------------------------------------------- #


def test_effective_budget_is_min_of_user_and_project(tmp_path: Path) -> None:
    assert q.effective_core_hours(Decimal("100"), Decimal("40")) == Decimal("40")
    assert q.effective_core_hours(None, Decimal("40")) == Decimal("40")
    assert q.effective_core_hours(None, None) is None

    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("40")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))

    first = _admit(service, cores=30, walltime_s=3600)  # 30 core-hours
    assert first.admitted is True

    # The project budget (40) is now the tighter constraint: 30 + 20 > 40.
    second = _admit(service, cores=20, walltime_s=3600, job_id="job-2")
    assert second.admitted is False
    assert second.reason_code == R.INSUFFICIENT_BUDGET


def test_quota_only_without_any_budget_refused(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.startup(_selection(mode=M.QUOTA_ONLY))
    decision = _admit(service, cores=1, walltime_s=3600)
    assert decision.admitted is False
    assert decision.reason_code == R.NO_BUDGET


# --------------------------------------------------------------------------- #
# 5/6. Write-off, pending reconciliation and over-run
# --------------------------------------------------------------------------- #


def test_write_off_confirmed_releases_unused_ceiling(tmp_path: Path) -> None:
    policy = _policy()
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY, policy=policy))

    window_end = WIN_START + timedelta(hours=2)
    decision = _admit(service, cores=4, walltime_s=7200, window_end=window_end)
    assert decision.reserved_core_hours == Decimal("8")
    assert service.available("alice", "proj-1") == Decimal("92")

    # Confirmed usage: 2h × 2 cores = 4.0 core-hours.
    record = _usage_record(policy, start=WIN_START, end=window_end, cores=2)
    assert record.core_hours == Decimal("4.000")
    result = service.write_off(decision.reservation_id, record)
    assert result.pending is False
    assert result.state == S.SETTLED
    assert result.settled_core_hours == Decimal("4.000")
    assert result.released_core_hours == Decimal("4.000")
    assert service.available("alice", "proj-1") == Decimal("96")


def test_pending_usage_keeps_reservation_held(tmp_path: Path) -> None:
    policy = _policy()
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY, policy=policy))
    decision = _admit(service, cores=4, walltime_s=3600)
    assert service.available("alice", "proj-1") == Decimal("96")

    pending_record = _usage_record(policy, start=WIN_START, end=WIN_END, cores=4, complete=False)
    assert pending_record.pending_reconciliation is True
    result = service.write_off(decision.reservation_id, pending_record)
    assert result.pending is True
    assert result.state == S.PENDING
    # Held, not released: the reservation still occupies its reservation.
    assert service.available("alice", "proj-1") == Decimal("96")


def test_overrun_is_refused_not_settled(tmp_path: Path) -> None:
    policy = _policy()
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY, policy=policy))
    decision = _admit(service, cores=2, walltime_s=3600)  # reserved 2
    assert decision.reserved_core_hours == Decimal("2")

    over = _usage_record(policy, start=WIN_START, end=WIN_END, cores=4)  # confirmed 4
    with pytest.raises(q.QuotaOverrunError):
        service.write_off(decision.reservation_id, over)

    reservation = service.reservation(decision.reservation_id)
    assert reservation is not None
    assert reservation.state == S.PENDING  # held; never settled above the cap
    # Budget still holds the full frozen reservation (no after-the-fact extension).
    assert service.available("alice", "proj-1") == Decimal("98")


# --------------------------------------------------------------------------- #
# 7. Cross-period segmentation
# --------------------------------------------------------------------------- #


def test_cross_period_reservation_and_write_off(tmp_path: Path) -> None:
    policy = _policy(unit=mp.PeriodUnit.MONTH, tz="UTC")
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY, policy=policy))

    start = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
    end = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)
    decision = _admit(service, cores=4, walltime_s=7200, window_start=start, window_end=end)
    assert decision.reserved_core_hours == Decimal("8")
    segments = decision.segments
    assert len(segments) == 2
    assert sum((seg.reserved_core_hours for seg in segments), Decimal(0)) == Decimal("8")
    assert segments[0].reserved_core_hours == Decimal("4")
    assert segments[1].reserved_core_hours == Decimal("4")

    record = _usage_record(policy, start=start, end=end, cores=4)
    result = service.write_off(decision.reservation_id, record)
    assert result.settled_core_hours == Decimal("8.000")
    assert len(result.segments) == 2
    assert result.segments[0].written_off_core_hours == Decimal("4.000")
    assert result.segments[1].written_off_core_hours == Decimal("4.000")


# --------------------------------------------------------------------------- #
# 8. Restart recovery + lease expiry
# --------------------------------------------------------------------------- #


def test_restart_recovery_replays_undecided(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))
    decision = _admit(service, cores=4, walltime_s=3600)
    rid = decision.reservation_id
    assert rid is not None
    available_before = service.available("alice", "proj-1")

    # Restart: a brand-new ledger + service over the same database file.
    reopened, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    reopened.startup(_selection(mode=M.QUOTA_ONLY))
    report = reopened.recover()
    assert rid in {item.reservation_id for item in report.replayed}
    assert reopened.available("alice", "proj-1") == available_before
    assert reopened.reservation(rid).state == S.RESERVED  # type: ignore[union-attr]


def test_lease_expiry_alone_does_not_release(tmp_path: Path) -> None:
    service, _ = _service(
        tmp_path, user={"alice": Decimal("100")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))
    decision = _admit(service, cores=4, walltime_s=3600, lease_until="2000-01-01T00:00:00+00:00")
    assert decision.reservation_id is not None
    report = service.recover()
    assert decision.reservation_id in {item.reservation_id for item in report.replayed}
    reservation = service.reservation(decision.reservation_id)
    assert reservation is not None
    assert reservation.state == S.RESERVED
    assert reservation.lease_until == "2000-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# 9. Budget top-up ordering
# --------------------------------------------------------------------------- #


def test_top_up_authorizes_and_reserves_before_extending(tmp_path: Path) -> None:
    service, ledger = _service(
        tmp_path, user={"alice": Decimal("10")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))
    _admit(service, cores=8, walltime_s=3600)  # consume 8 of 10

    result = service.top_up("user", "alice", Decimal("5"), "admin")
    assert result.applied is True
    assert result.phases == ("authorize", "reserve", "extend")
    assert ledger.get_budget("user", "alice") == Decimal("15")
    assert service.available("alice", "proj-1") == Decimal("7")  # 15 - 8


def test_top_up_failure_keeps_authorization_and_hold_without_overuse(
    tmp_path: Path,
) -> None:
    service, ledger = _service(
        tmp_path, user={"alice": Decimal("10")}, project={"proj-1": Decimal("100")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))
    _admit(service, cores=8, walltime_s=3600)

    def _explode(_amount: Decimal) -> None:
        raise RuntimeError("allowance extension failed")

    with pytest.raises(RuntimeError):
        service.top_up("user", "alice", Decimal("5"), "admin", extend_allowance=_explode)

    # Authorization + hold persisted; the allowance was NOT extended.
    pending = ledger.list_topups(state="authorized")
    assert len(pending) == 1
    topup_id = pending[0]["topup_id"]
    assert ledger.topup_events(topup_id) == ("authorize", "reserve")
    assert ledger.get_budget("user", "alice") == Decimal("10")  # unchanged

    report = service.recover(reapply_topups=False)
    assert topup_id in report.pending_topups

    # No over-use: the pending hold makes the effective budget exhausted.
    refusal = _admit(service, cores=1, walltime_s=3600, job_id="job-2")
    assert refusal.admitted is False
    assert refusal.reason_code == R.EXHAUSTED

    # Retry applies the previously authorized top-up.
    assert service.resolve_topup(topup_id) == Decimal("15")
    assert ledger.topup_state(topup_id) == "applied"


# --------------------------------------------------------------------------- #
# 10. Exhaustion: refuse new, keep in-flight, results readable
# --------------------------------------------------------------------------- #


def test_exhaustion_refuses_new_and_keeps_inflight_results_readable(tmp_path: Path) -> None:
    service, ledger = _service(
        tmp_path, user={"alice": Decimal("10")}, project={"proj-1": Decimal("10")}
    )
    service.startup(_selection(mode=M.QUOTA_ONLY))
    first = _admit(service, cores=10, walltime_s=3600, result_ref="RESULT/manifest.json")
    assert first.admitted is True
    assert first.reservation_id is not None

    refused = _admit(service, cores=1, walltime_s=3600, job_id="job-2")
    assert refused.admitted is False
    assert refused.reason_code == R.EXHAUSTED
    assert refused.new_dispatch_allowed is False
    assert refused.in_flight_killed is False
    assert refused.results_readable is True

    # In-flight reservation is untouched and its result reference is preserved.
    inflight = service.reservation(first.reservation_id)
    assert inflight is not None
    assert inflight.state == S.RESERVED
    assert inflight.result_ref == "RESULT/manifest.json"
    assert ledger.list_reservations(job_id="job-1")


def test_exhaustion_policy_defaults() -> None:
    policy = q.ExhaustionPolicy()
    assert policy.action == q.ExhaustionAction.REFUSE_NEW
    assert policy.kill_in_flight is False
    assert policy.results_readable is True


# --------------------------------------------------------------------------- #
# 11. Mode switch keeps policy history
# --------------------------------------------------------------------------- #


def test_mode_switch_appends_without_clearing_old_snapshots(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.startup(_selection(mode=M.QUOTA_ONLY, policy=_policy(version="pv-1")))
    service.switch_mode(
        _selection(mode=M.USAGE_ONLY, admin_selected=True, policy=_policy(version="pv-2"))
    )

    snapshots = service.snapshots()
    assert [snap.mode for snap in snapshots] == [M.QUOTA_ONLY, M.USAGE_ONLY]
    assert {snap.policy_version for snap in snapshots} == {"pv-1", "pv-2"}
    assert service.mode == M.USAGE_ONLY


# --------------------------------------------------------------------------- #
# 12. Structural guards
# --------------------------------------------------------------------------- #


def test_module_has_no_acp_import_and_no_post_hoc_statistics() -> None:
    source = _MODULE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "acp" and not alias.name.startswith("acp.")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module != "acp" and not module.startswith("acp.")
    # No post-hoc progress/browser statistics and no result deletion.
    for banned in ("progress", "browser", "DELETE FROM", "os.remove", "unlink"):
        assert banned not in source, f"unexpected token {banned!r} in quota.py"


def test_notes_and_exports_present() -> None:
    assert "scientific identity" in q.QUOTA_NOT_SCIENTIFIC_IDENTITY_NOTE
    assert "BEFORE" in q.QUOTA_PRE_DISPATCH_NOTE
    for name in (
        "QuotaMode",
        "QuotaState",
        "QuotaReason",
        "QuotaModeSelection",
        "QuotaLedger",
        "QuotaService",
        "ExhaustionPolicy",
        "effective_core_hours",
    ):
        assert name in q.__all__
        assert hasattr(q, name)
