"""B10-1 (todo 53) — frozen metering-policy contract.

Exercises ``acp_services.runtime.metering_policy``:

1. **Explicit basis or a refusal.**  An unresolved policy (the shipped default)
   refuses metering with a typed :class:`MeteringRefusedError`; there is no
   hidden default basis/rounding/period.
2. **Both documented formulas.**  Allocated core hours use
   ``Σ(seconds × allocated_cores)/3600``; actual CPU hours use
   ``Σ(cpu_seconds)/3600``.  The two bases are never mixed.
3. **Rounding / period / pause boundaries.**  Rounding modes are applied at the
   documented decimal places; billing periods respect the configured timezone;
   pause/failure/retry follow their (default-unresolved) policy knob.
4. **Non-metered categories.**  Queue wait / transfer / publication are excluded
   unconditionally.
5. **Unknown usage reconciles.**  An unknown kind or a missing measurement /
   evidence is pending reconciliation, never zero-filled.
6. **Frozen policy version snapshot** — immutable policy, stable digest, and a
   result that keeps the version it measured under.
7. **Executable upper bound before execution** and quota-only refusal when any
   bound dimension or the hard-limit capability is unavailable.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from acp_services.runtime import metering_policy as mp

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "metering_policy.py"

B = mp.MeteringBasis
R = mp.RoundingRule
P = mp.PeriodUnit
K = mp.IntervalKind
IB = mp.IntervalBilling
CD = mp.ChargeDecision
MR = mp.MeteringReason

UTC = timezone.utc


def _policy(
    *,
    version: str = "pv-2026-10-19",
    basis: str = B.ALLOCATED_CORE_HOURS,
    rule: str = R.HALF_UP,
    places: int | None = 3,
    unit: str = P.MONTH,
    tz: str = "UTC",
    pause: str = IB.UNRESOLVED,
    failure: str = IB.UNRESOLVED,
    retry: str = IB.UNRESOLVED,
) -> mp.MeteringPolicy:
    return mp.MeteringPolicy(
        policy_version=version,
        basis=basis,
        rounding=mp.RoundingSpec(rule=rule, decimal_places=places),
        period=mp.BillingPeriod(unit=unit, timezone=tz),
        pause=pause,
        failure=failure,
        retry=retry,
    )


def _interval(
    *,
    start_h: float = 0.0,
    end_h: float = 1.0,
    kind: str = K.RUNNING,
    cores: int | None = 4,
    cpu_seconds: Decimal | None = None,
    evidence: bool = True,
) -> mp.UsageInterval:
    """Build an interval on 2026-10-01 UTC with hour offsets."""
    base = datetime(2026, 10, 1, tzinfo=UTC)
    return mp.UsageInterval(
        start=base + timedelta(hours=start_h),
        end=base + timedelta(hours=end_h),
        kind=kind,
        allocated_cores=cores,
        cpu_seconds=cpu_seconds,
        evidence_source="local-proc" if evidence else "",
        evidence_version="1" if evidence else "",
    )


# --------------------------------------------------------------------------- #
# 1. Explicit basis or refusal (no hidden defaults)
# --------------------------------------------------------------------------- #


def test_unresolved_basis_refuses_metering_with_explicit_error() -> None:
    policy = mp.MeteringPolicy(policy_version="v1")
    assert policy.basis == B.UNRESOLVED
    assert policy.measurable is False
    with pytest.raises(mp.MeteringRefusedError) as excinfo:
        mp.measure([_interval()], policy)
    assert excinfo.value.reason_code == MR.METERING_BASIS_UNRESOLVED


def test_unresolved_rounding_refuses_metering() -> None:
    policy = _policy(rule=R.UNRESOLVED, places=None)
    assert policy.basis == B.ALLOCATED_CORE_HOURS
    with pytest.raises(mp.MeteringRefusedError) as excinfo:
        mp.measure([_interval()], policy)
    assert excinfo.value.reason_code == MR.ROUNDING_UNRESOLVED


def test_unresolved_period_refuses_metering() -> None:
    policy = _policy(unit=P.UNRESOLVED, tz="")
    with pytest.raises(mp.MeteringRefusedError) as excinfo:
        mp.measure([_interval()], policy)
    assert excinfo.value.reason_code == MR.PERIOD_UNRESOLVED


def test_policy_defaults_are_all_unresolved_no_hidden_default() -> None:
    policy = mp.MeteringPolicy(policy_version="v1")
    assert policy.basis == B.UNRESOLVED
    assert policy.rounding == mp.RoundingSpec()
    assert policy.period == mp.BillingPeriod()
    assert policy.pause == IB.UNRESOLVED
    assert policy.failure == IB.UNRESOLVED
    assert policy.retry == IB.UNRESOLVED
    assert policy.measurable is False
    assert policy.is_fully_resolved() is False


def test_policy_version_is_required() -> None:
    with pytest.raises(mp.MeteringValidationError):
        mp.MeteringPolicy(policy_version="   ")


def test_rounding_requires_decimal_places_when_resolved() -> None:
    with pytest.raises(mp.MeteringValidationError):
        mp.RoundingSpec(rule=R.HALF_UP, decimal_places=None)
    with pytest.raises(mp.MeteringValidationError):
        mp.RoundingSpec(rule=R.HALF_UP, decimal_places=-1)
    # Unresolved rounding must not smuggle decimal places.
    with pytest.raises(mp.MeteringValidationError):
        mp.RoundingSpec(rule=R.UNRESOLVED, decimal_places=2)


def test_period_requires_timezone_when_resolved_and_validates_zone() -> None:
    with pytest.raises(mp.MeteringValidationError):
        mp.BillingPeriod(unit=P.MONTH, timezone="")
    with pytest.raises(mp.MeteringValidationError):
        mp.BillingPeriod(unit=P.MONTH, timezone="Mars/Olympus")


# --------------------------------------------------------------------------- #
# 2. Both documented formulas; never mixed
# --------------------------------------------------------------------------- #


def test_allocated_core_hours_formula() -> None:
    policy = _policy(basis=B.ALLOCATED_CORE_HOURS)
    intervals = [
        _interval(start_h=0, end_h=1, cores=4),  # 3600s * 4 = 14400 core-s
        _interval(start_h=2, end_h=2.5, cores=2),  # 1800s * 2 = 3600 core-s
    ]
    result = mp.measure(intervals, policy)
    assert result.basis == B.ALLOCATED_CORE_HOURS
    assert result.raw_core_hours == Decimal("5")
    assert result.core_hours == Decimal("5.000")
    assert result.metered_intervals == 2
    assert result.metered_seconds == Decimal("5400")
    assert result.mixed_bases is False


def test_actual_cpu_seconds_formula() -> None:
    policy = _policy(basis=B.ACTUAL_CPU_SECONDS)
    intervals = [
        _interval(cpu_seconds=Decimal("3600")),
        _interval(start_h=2, end_h=3, cpu_seconds=Decimal("1800")),
    ]
    result = mp.measure(intervals, policy)
    assert result.basis == B.ACTUAL_CPU_SECONDS
    assert result.raw_core_hours == Decimal("1.5")
    assert result.core_hours == Decimal("1.500")
    assert result.metered_intervals == 2


def test_allocated_basis_ignores_measured_cpu_seconds() -> None:
    policy = _policy(basis=B.ALLOCATED_CORE_HOURS)
    interval = _interval(cores=2, cpu_seconds=Decimal("36000"))
    result = mp.measure([interval], policy)
    # 3600s * 2 / 3600 = 2.0 — the 36000 measured CPU seconds must not be added.
    assert result.core_hours == Decimal("2.000")


def test_actual_basis_ignores_allocated_cores() -> None:
    policy = _policy(basis=B.ACTUAL_CPU_SECONDS)
    interval = _interval(cores=64, cpu_seconds=Decimal("3600"))
    result = mp.measure([interval], policy)
    # 3600 / 3600 = 1.0 — the 64 allocated cores must not be multiplied in.
    assert result.core_hours == Decimal("1.000")


# --------------------------------------------------------------------------- #
# 3a. Rounding boundaries
# --------------------------------------------------------------------------- #


def _half_core_hour_basis() -> tuple[mp.MeteringPolicy, list[mp.UsageInterval]]:
    # one core for 1800s -> exactly 0.5 core-hours
    intervals = [_interval(start_h=0, end_h=0.5, cores=1)]
    return intervals


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (R.HALF_UP, Decimal("1")),
        (R.HALF_EVEN, Decimal("0")),
        (R.FLOOR, Decimal("0")),
        (R.CEIL, Decimal("1")),
    ],
)
def test_rounding_at_half_boundary(rule: str, expected: Decimal) -> None:
    intervals = _half_core_hour_basis()
    policy = _policy(rule=rule, places=0)
    assert mp.measure(intervals, policy).core_hours == expected


def test_rounding_decimal_places() -> None:
    # one core for 1200s -> 0.33333... core-hours
    interval = _interval(start_h=0, end_h=1 / 3, cores=1)
    result = mp.measure([interval], _policy(rule=R.HALF_UP, places=3))
    assert result.core_hours == Decimal("0.333")
    assert result.raw_core_hours > Decimal("0.333")


def test_quantize_core_hours_refuses_when_unresolved() -> None:
    with pytest.raises(mp.MeteringRefusedError) as excinfo:
        mp.quantize_core_hours(Decimal("1"), mp.RoundingSpec())
    assert excinfo.value.reason_code == MR.ROUNDING_UNRESOLVED


# --------------------------------------------------------------------------- #
# 3b. Period boundaries + timezone
# --------------------------------------------------------------------------- #


def test_month_period_bounds_utc() -> None:
    period = mp.BillingPeriod(unit=P.MONTH, timezone="UTC")
    start, end = mp.period_bounds(datetime(2026, 10, 15, 12, tzinfo=UTC), period)
    assert start == datetime(2026, 10, 1, tzinfo=UTC)
    assert end == datetime(2026, 11, 1, tzinfo=UTC)


def test_period_timezone_changes_the_boundary() -> None:
    utc_period = mp.BillingPeriod(unit=P.MONTH, timezone="UTC")
    sh_period = mp.BillingPeriod(unit=P.MONTH, timezone="Asia/Shanghai")
    moment = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)  # 2026-11-01 04:00 +08:00
    assert mp.period_bounds(moment, utc_period)[0] == datetime(2026, 10, 1, tzinfo=UTC)
    assert mp.period_bounds(moment, sh_period)[0] == datetime(
        2026, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai")
    )


def test_day_period_bounds_in_configured_timezone() -> None:
    period = mp.BillingPeriod(unit=P.DAY, timezone="Asia/Shanghai")
    moment = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)  # 2026-10-02 04:00 +08:00
    start, end = mp.period_bounds(moment, period)
    tz = ZoneInfo("Asia/Shanghai")
    assert start == datetime(2026, 10, 2, tzinfo=tz)
    assert end == datetime(2026, 10, 3, tzinfo=tz)


def test_segment_by_period_splits_cross_period_interval() -> None:
    period = mp.BillingPeriod(unit=P.MONTH, timezone="UTC")
    start = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)
    end = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
    segments = mp.segment_by_period(start, end, period)
    assert segments == (
        (datetime(2026, 9, 30, 23, 0, tzinfo=UTC), datetime(2026, 10, 1, 0, 0, tzinfo=UTC)),
        (datetime(2026, 10, 1, 0, 0, tzinfo=UTC), datetime(2026, 10, 1, 1, 0, tzinfo=UTC)),
    )
    assert sum((b - a).total_seconds() for a, b in segments) == (end - start).total_seconds()


def test_segment_by_period_empty_interval_yields_no_segments() -> None:
    period = mp.BillingPeriod(unit=P.DAY, timezone="UTC")
    moment = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
    assert mp.segment_by_period(moment, moment, period) == ()


def test_period_bounds_rejects_naive_datetime() -> None:
    period = mp.BillingPeriod(unit=P.MONTH, timezone="UTC")
    with pytest.raises(mp.MeteringValidationError):
        mp.period_bounds(datetime(2026, 10, 1), period)


# --------------------------------------------------------------------------- #
# 3c. Pause / failure / retry boundaries
# --------------------------------------------------------------------------- #


def test_pause_unresolved_goes_pending_not_metered_or_excluded() -> None:
    policy = _policy(pause=IB.UNRESOLVED)
    intervals = [_interval(kind=K.RUNNING), _interval(start_h=1, end_h=2, kind=K.PAUSED)]
    result = mp.measure(intervals, policy)
    assert result.metered_intervals == 1
    assert result.pending_intervals == 1
    assert result.core_hours == Decimal("4.000")  # only the running interval
    assert result.pending[0].reason == "pause_unresolved"


def test_pause_excluded_boundary() -> None:
    policy = _policy(pause=IB.EXCLUDED)
    p = mp.classify_interval(_interval(kind=K.PAUSED), policy)
    assert p.decision == CD.EXCLUDED and p.reason == "pause_excluded"


def test_pause_included_boundary() -> None:
    policy = _policy(pause=IB.INCLUDED)
    p = mp.classify_interval(_interval(kind=K.PAUSED), policy)
    assert p.decision == CD.METERED
    # and it contributes core hours
    result = mp.measure([_interval(kind=K.PAUSED)], policy)
    assert result.core_hours == Decimal("4.000")


def test_failure_and_retry_unresolved_reconcile() -> None:
    policy = _policy(failure=IB.UNRESOLVED, retry=IB.UNRESOLVED)
    verdicts = [
        mp.classify_interval(_interval(kind=K.FAILED), policy),
        mp.classify_interval(_interval(kind=K.RETRY), policy),
    ]
    assert [v.decision for v in verdicts] == [
        CD.PENDING_RECONCILIATION,
        CD.PENDING_RECONCILIATION,
    ]
    assert [v.reason for v in verdicts] == ["failure_unresolved", "retry_unresolved"]


def test_fully_resolved_requires_all_knobs() -> None:
    partial = _policy(pause=IB.INCLUDED, failure=IB.EXCLUDED, retry=IB.UNRESOLVED)
    assert partial.measurable is True
    assert partial.is_fully_resolved() is False
    complete = _policy(pause=IB.INCLUDED, failure=IB.EXCLUDED, retry=IB.INCLUDED)
    assert complete.is_fully_resolved() is True


# --------------------------------------------------------------------------- #
# 4. Queue / transfer / publication never metered
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", [K.QUEUED, K.TRANSFER, K.PUBLICATION])
def test_non_metered_categories_are_always_excluded(kind: str) -> None:
    # Even the most permissive policy cannot meter queue/transfer/publication.
    policy = _policy(pause=IB.INCLUDED, failure=IB.INCLUDED, retry=IB.INCLUDED)
    verdict = mp.classify_interval(_interval(kind=kind), policy)
    assert verdict.decision == CD.EXCLUDED
    assert verdict.reason == "non_metered_category"
    result = mp.measure([_interval(kind=kind)], policy)
    assert result.metered_intervals == 0
    assert result.excluded_intervals == 1
    assert result.core_hours == Decimal("0.000")


def test_non_metered_excluded_even_without_evidence() -> None:
    policy = _policy()
    verdict = mp.classify_interval(_interval(kind=K.TRANSFER, evidence=False), policy)
    assert verdict.decision == CD.EXCLUDED  # exclusion beats the evidence check


def test_non_metered_categories_constant() -> None:
    assert mp.NON_METERED_CATEGORIES == {K.QUEUED, K.TRANSFER, K.PUBLICATION}
    assert K.NON_METERED == mp.NON_METERED_CATEGORIES


# --------------------------------------------------------------------------- #
# 5. Unknown usage reconciles (never zero-filled)
# --------------------------------------------------------------------------- #


def test_unknown_kind_is_pending() -> None:
    policy = _policy()
    verdict = mp.classify_interval(_interval(kind=K.UNKNOWN), policy)
    assert verdict.decision == CD.PENDING_RECONCILIATION
    assert verdict.reason == "unknown_usage"


def test_missing_evidence_is_pending() -> None:
    policy = _policy()
    verdict = mp.classify_interval(_interval(evidence=False), policy)
    assert verdict.decision == CD.PENDING_RECONCILIATION
    assert verdict.reason == "evidence_missing"


def test_actual_basis_without_cpu_seconds_is_pending_not_zero() -> None:
    policy = _policy(basis=B.ACTUAL_CPU_SECONDS)
    result = mp.measure([_interval(cpu_seconds=None)], policy)
    assert result.metered_intervals == 0
    assert result.pending_intervals == 1
    assert result.pending[0].reason == "actual_cpu_seconds_missing"
    assert result.core_hours == Decimal("0.000")


def test_allocated_basis_without_cores_is_pending_not_zero() -> None:
    policy = _policy(basis=B.ALLOCATED_CORE_HOURS)
    result = mp.measure([_interval(cores=None)], policy)
    assert result.metered_intervals == 0
    assert result.pending_intervals == 1
    assert result.pending[0].reason == "allocated_cores_missing"


# --------------------------------------------------------------------------- #
# 6. Frozen policy-version snapshot
# --------------------------------------------------------------------------- #


def test_policy_dataclass_is_frozen() -> None:
    assert dataclasses.is_dataclass(mp.MeteringPolicy)
    assert mp.MeteringPolicy.__dataclass_params__.frozen
    policy = _policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.basis = B.ACTUAL_CPU_SECONDS  # type: ignore[misc]


def test_snapshot_digest_is_stable_and_version_sensitive() -> None:
    a = _policy(version="pv1")
    b = _policy(version="pv1")
    c = _policy(version="pv2")
    d = _policy(version="pv1", basis=B.ACTUAL_CPU_SECONDS)
    assert a.snapshot_digest() == b.snapshot_digest()
    assert a.snapshot_digest() != c.snapshot_digest()
    assert a.snapshot_digest() != d.snapshot_digest()
    assert len(a.snapshot_digest()) == 64


def test_measure_result_keeps_the_policy_version_it_measured_under() -> None:
    policy_v1 = _policy(version="pv1")
    result = mp.measure([_interval()], policy_v1)
    assert result.policy_version == "pv1"
    # A later "policy change" cannot relabel a recorded measurement.
    policy_v2 = _policy(version="pv2")
    assert policy_v2.policy_version == "pv2"
    assert result.policy_version == "pv1"
    assert result.to_dict()["policy_version"] == "pv1"


def test_executable_bound_snapshots_policy_version_and_pause() -> None:
    policy = _policy(version="pv1", pause=IB.EXCLUDED)
    bound = mp.freeze_executable_bound(
        policy, cores=8, walltime_s=3600, hard_limit_capability=mp.HARD_LIMIT_ENFORCED
    )
    assert bound.policy_version == "pv1"
    assert bound.pause_metering == IB.EXCLUDED
    assert bound.executable is True


# --------------------------------------------------------------------------- #
# 7. Executable upper bound + quota-only refusal
# --------------------------------------------------------------------------- #


def _executable_bound() -> mp.ExecutableBound:
    return mp.freeze_executable_bound(
        _policy(),
        cores=8,
        walltime_s=3600,
        hard_limit_capability=mp.HARD_LIMIT_ENFORCED,
    )


def test_freeze_executable_bound_validation() -> None:
    policy = _policy()
    with pytest.raises(mp.MeteringValidationError):
        mp.freeze_executable_bound(policy, cores=-1, walltime_s=10)
    with pytest.raises(mp.MeteringValidationError):
        mp.freeze_executable_bound(policy, cores=1, walltime_s=10, hard_limit_capability="maybe")


def test_quota_only_admitted_with_executable_bound() -> None:
    decision = mp.admit_metering_mode(_executable_bound())
    assert decision.admitted is True
    assert decision.reason_code == MR.ADMITTED
    assert decision.enforced is True


def test_quota_only_refused_without_policy_version() -> None:
    bound = mp.ExecutableBound(
        policy_version="",
        cores=8,
        walltime_s=3600,
        hard_limit_capability=mp.HARD_LIMIT_ENFORCED,
    )
    decision = mp.admit_metering_mode(bound)
    assert decision.admitted is False
    assert decision.reason_code == MR.NO_POLICY_VERSION


def test_quota_only_refused_when_core_bound_missing() -> None:
    bound = mp.freeze_executable_bound(
        _policy(), cores=None, walltime_s=3600, hard_limit_capability=mp.HARD_LIMIT_ENFORCED
    )
    decision = mp.admit_metering_mode(bound)
    assert decision.admitted is False
    assert decision.reason_code == MR.UNBOUNDED_REQUEST
    assert "cores" in decision.detail


def test_quota_only_refused_when_walltime_bound_missing() -> None:
    bound = mp.freeze_executable_bound(
        _policy(), cores=8, walltime_s=None, hard_limit_capability=mp.HARD_LIMIT_ENFORCED
    )
    decision = mp.admit_metering_mode(bound)
    assert decision.admitted is False
    assert decision.reason_code == MR.UNBOUNDED_REQUEST
    assert "walltime_s" in decision.detail


def test_quota_only_refused_when_hard_limit_unavailable() -> None:
    bound = mp.freeze_executable_bound(
        _policy(), cores=8, walltime_s=3600, hard_limit_capability=mp.HARD_LIMIT_UNAVAILABLE
    )
    decision = mp.admit_metering_mode(bound)
    assert decision.admitted is False
    assert decision.reason_code == MR.HARD_LIMIT_UNAVAILABLE


def test_usage_only_is_the_explicit_escape_hatch_even_unbounded() -> None:
    empty = mp.ExecutableBound()
    decision = mp.admit_metering_mode(empty, mode=mp.AdmissionMode.USAGE_ONLY)
    assert decision.admitted is True
    assert decision.reason_code == MR.USAGE_ONLY
    assert decision.enforced is False


def test_admit_metering_mode_rejects_unknown_mode() -> None:
    with pytest.raises(mp.MeteringValidationError):
        mp.admit_metering_mode(_executable_bound(), mode="unlimited")


# --------------------------------------------------------------------------- #
# 8. Architecture / no-hidden-price guards
# --------------------------------------------------------------------------- #


def _import_roots(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_is_isolated_from_acp_cccp_and_networks() -> None:
    roots = _import_roots(_MODULE_SRC.read_text(encoding="utf-8"))
    assert "acp" not in roots, "acp_services must not import acp"
    assert "cccp" not in roots, "the metering policy contract must not import cccp"
    for forbidden in ("subprocess", "socket", "paramiko", "requests"):
        assert forbidden not in roots, f"{forbidden} is not allowed in metering_policy"


def test_policy_carries_no_price_or_rate_field() -> None:
    field_names = {f.name for f in dataclasses.fields(mp.MeteringPolicy)}
    assert not any("price" in name or "rate" in name or "credit" in name for name in field_names)
    # The module states the policy is not scientific identity.
    assert "NOT part of scientific identity" in mp.METERING_NOT_SCIENTIFIC_IDENTITY_NOTE


def test_module_has_no_numeric_price_constants() -> None:
    """No hidden price/rate numeric literal may be defined in the module."""
    tree = ast.parse(_MODULE_SRC.read_text(encoding="utf-8"))
    price_words = ("price", "rate", "credit", "usd", "cost")
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and any(
                    word in target.id.lower() for word in price_words
                ):
                    raise AssertionError(f"price/rate constant found: {target.id}")


def test_classify_and_measure_vocabularies_cover_all_kinds() -> None:
    policy = _policy(pause=IB.INCLUDED, failure=IB.INCLUDED, retry=IB.INCLUDED)
    for kind in K.ALL:
        verdict = mp.classify_interval(_interval(kind=kind), policy)
        assert verdict.decision in CD.ALL
        assert verdict.reason
