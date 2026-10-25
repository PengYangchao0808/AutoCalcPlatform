"""B10-2 (todo 54) — trusted usage collection + ``UsageRecord`` binding.

Exercises ``acp_services.runtime.usage``:

1. **Both collectors.**  The LSF (parent) collector maps a ``bjobs``-style job
   report to the actual allocation; the local (child) collector maps OS
   process-group accounting (``getrusage``) to CPU seconds.  A policy-allowed
   local allocation is covered too.
2. **Parent/child never double-counted.**  ``select_metering_level`` returns one
   level, refuses to pick without an explicit choice when both are observed, and
   the built record only ever contains the selected level's intervals.
3. **Missing evidence reconciles, never zero-fills.**  Absent evidence, an
   incomplete report, or a missing basis measurement all yield a
   ``pending_reconciliation`` record carrying the pending intervals.
4. **UsageRecord field completeness.**  Every §20.4 field is present; the policy
   version/digest is recorded; the final vs estimated flag is independent of
   reconciliation.
5. **Guards.**  No progress/browser timing vocabulary, no price/rate/Credit, no
   scientific-payload coupling, and a stdlib-only dependency direction.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from acp_services.runtime import metering_policy as mp
from acp_services.runtime import usage as u

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "usage.py"

UTC = timezone.utc
WIN_START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WIN_END = WIN_START + timedelta(hours=1)  # exactly one hour

B = mp.MeteringBasis
K = mp.IntervalKind
CD = mp.ChargeDecision
L = u.ResourceLevel
S = u.CollectionSource
RC = u.ResourceClass
RS = u.ReconciliationStatus


def _policy(
    *,
    version: str = "pv-2026-10-19",
    basis: str = B.ALLOCATED_CORE_HOURS,
) -> mp.MeteringPolicy:
    return mp.MeteringPolicy(
        policy_version=version,
        basis=basis,
        rounding=mp.RoundingSpec(rule=mp.RoundingRule.HALF_UP, decimal_places=3),
        period=mp.BillingPeriod(unit=mp.PeriodUnit.MONTH, timezone="UTC"),
    )


def _identity(**overrides: object) -> u.UsageIdentity:
    base: dict[str, object] = {
        "job_id": "20261001_001_optimize",
        "attempt": 2,
        "project_id": "proj-1",
        "account": "acct-1",
        "submission_id": "sub_deadbeef",
        "resource_id": "node-7",
    }
    base.update(overrides)
    return u.UsageIdentity(**base)  # type: ignore[arg-type]


def _lsf_report(**overrides: object) -> u.LsfJobReport:
    base: dict[str, object] = {
        "lsf_job_id": "12345",
        "state": "DONE",
        "node": "node-07",
        "allocated_cores": 8,
        "cpu_seconds": Decimal("28800"),
        "evidence_version": "bjobs-1",
    }
    base.update(overrides)
    return u.LsfJobReport(**base)  # type: ignore[arg-type]


def _local_sample(**overrides: object) -> u.LocalProcessSample:
    base: dict[str, object] = {
        "cpu_seconds": Decimal("5400"),
        "allocated_cores": 4,
        "pgid": 4242,
        "resource_id": "node-07",
        "evidence_version": "proc-1",
    }
    base.update(overrides)
    return u.LocalProcessSample(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. Both collectors
# --------------------------------------------------------------------------- #


def test_lsf_collector_maps_report_to_actual_allocation() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    assert collected.level == L.PARENT
    assert collected.source == S.LSF_JOB_REPORT
    assert collected.external_job_id == "12345"
    assert collected.allocated_cores == 8
    assert collected.evidence_present is True
    assert collected.interval_count == 1

    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END, final=True
    )
    # 1h * 8 cores / 3600 = 8.0 allocated core-hours.
    assert record.core_hours == Decimal("8.000")
    assert record.basis == B.ALLOCATED_CORE_HOURS
    assert record.resource_level == L.PARENT
    assert record.external_job_id == "12345"
    assert record.reconciliation_status == RS.COMPLETE
    assert record.confirmable is True


def test_local_collector_maps_process_accounting() -> None:
    sample = u.local_process_sample_from_rusage(
        {"ru_utime": 3600.0, "ru_stime": 1800.0},
        pgid=4242,
        resource_id="node-07",
        evidence_version="proc-1",
    )
    assert sample.cpu_seconds == Decimal("5400.0")

    collected = u.collect_local_process_usage(sample, window_start=WIN_START, window_end=WIN_END)
    assert collected.level == L.CHILD
    assert collected.source == S.LOCAL_PROCESS_GROUP

    record = u.build_usage_record(
        collected,
        _identity(),
        _policy(basis=B.ACTUAL_CPU_SECONDS),
        window_start=WIN_START,
        window_end=WIN_END,
        final=True,
    )
    # 5400 CPU seconds / 3600 = 1.5 core-hours.
    assert record.core_hours == Decimal("1.500")
    assert record.basis == B.ACTUAL_CPU_SECONDS
    assert record.resource_level == L.CHILD


def test_local_allocated_collector_uses_policy_allowed_cores() -> None:
    collected = u.collect_local_allocated_usage(
        allocated_cores=6,
        window_start=WIN_START,
        window_end=WIN_END,
        evidence_version="local-alloc-1",
        resource_id="node-07",
    )
    assert collected.level == L.PARENT
    assert collected.source == S.LOCAL_POLICY_ALLOCATED
    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    # 1h * 6 cores = 6.0 core-hours.
    assert record.core_hours == Decimal("6.000")


def test_lsf_report_mapping_aliases_and_coercion() -> None:
    report = u.lsf_job_report_from_mapping(
        {
            "job_id": "99",
            "stat": "RUN",
            "exec_host": "host-a",
            "ncores": "16",
            "cpu_time": "7200.5",
            "version": "v2",
        }
    )
    assert report.lsf_job_id == "99"
    assert report.state == "RUN"
    assert report.node == "host-a"
    assert report.allocated_cores == 16
    assert report.cpu_seconds == Decimal("7200.5")
    assert report.evidence_version == "v2"

    with pytest.raises(u.UsageValidationError):
        u.lsf_job_report_from_mapping({"stat": "RUN"})


def test_local_sample_mapping_keeps_absent_fields_unknown() -> None:
    sample = u.local_process_sample_from_rusage({}, evidence_version="proc-1")
    assert sample.cpu_seconds is None
    assert sample.allocated_cores is None
    assert sample.observation_available() is False


# --------------------------------------------------------------------------- #
# 2. Parent/child never double-counted
# --------------------------------------------------------------------------- #


def _parent_and_child() -> tuple[u.CollectedUsage, u.CollectedUsage]:
    parent = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    child = u.collect_local_process_usage(
        _local_sample(allocated_cores=4), window_start=WIN_START, window_end=WIN_END
    )
    return parent, child


def test_parent_and_child_select_one_level_explicitly() -> None:
    parent, child = _parent_and_child()
    assert u.select_metering_level(parent, None).level == L.PARENT
    assert u.select_metering_level(None, child).level == L.CHILD
    assert u.select_metering_level(parent, child, preferred=L.PARENT).level == L.PARENT
    assert u.select_metering_level(parent, child, preferred=L.CHILD).level == L.CHILD


def test_ambiguous_level_without_preference_is_refused() -> None:
    parent, child = _parent_and_child()
    with pytest.raises(u.UsageValidationError):
        u.select_metering_level(parent, child)


def test_no_observation_at_all_is_refused() -> None:
    with pytest.raises(u.UsageCollectionError):
        u.select_metering_level(None, None)


def test_selected_parent_never_includes_child_core_hours() -> None:
    parent, child = _parent_and_child()
    # parent: 8 allocated cores * 1h = 8.0; child: 4 cores * 1h = 4.0.
    parent_record = u.build_usage_record(
        u.select_metering_level(parent, child, preferred=L.PARENT),
        _identity(),
        _policy(),
        window_start=WIN_START,
        window_end=WIN_END,
    )
    child_record = u.build_usage_record(
        u.select_metering_level(parent, child, preferred=L.CHILD),
        _identity(),
        _policy(),
        window_start=WIN_START,
        window_end=WIN_END,
    )
    assert parent_record.core_hours == Decimal("8.000")
    assert child_record.core_hours == Decimal("4.000")
    # The sum (12.0) must never appear on either record.
    assert parent_record.core_hours != Decimal("12.000")
    assert child_record.core_hours != Decimal("12.000")


def test_record_intervals_come_only_from_the_selected_level() -> None:
    parent, child = _parent_and_child()
    record = u.build_usage_record(
        u.select_metering_level(parent, child, preferred=L.PARENT),
        _identity(),
        _policy(),
        window_start=WIN_START,
        window_end=WIN_END,
    )
    payload = record.to_dict()
    assert payload["collection_source"] == S.LSF_JOB_REPORT
    assert payload["resource_level"] == L.PARENT
    assert payload["allocated_cores"] == 8
    assert payload["metered_interval_count"] == 1


def test_preferred_level_not_observed_is_refused() -> None:
    parent, _ = _parent_and_child()
    with pytest.raises(u.UsageValidationError):
        u.select_metering_level(parent, None, preferred=L.CHILD)


# --------------------------------------------------------------------------- #
# 3. Missing evidence reconciles, never zero-fills
# --------------------------------------------------------------------------- #


def test_missing_evidence_version_reconciles() -> None:
    report = _lsf_report(evidence_version="")
    collected = u.collect_lsf_job_usage(report, window_start=WIN_START, window_end=WIN_END)
    assert collected.evidence_present is False

    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END, final=True
    )
    assert record.reconciliation_status == RS.PENDING_RECONCILIATION
    assert record.pending_reconciliation is True
    assert record.pending_interval_count == 1
    assert record.pending_intervals
    assert record.final is True
    assert record.confirmable is False  # terminal but not evidence-complete
    # Nothing was confirmed; the pending interval is listed, not silently zeroed.
    assert record.core_hours == Decimal("0.000")
    assert record.metered_interval_count == 0


def test_missing_report_yields_pending_not_zero() -> None:
    collected = u.collect_lsf_job_usage(None, window_start=WIN_START, window_end=WIN_END)
    assert collected.complete is False
    assert collected.evidence_present is False
    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    assert record.reconciliation_status == RS.PENDING_RECONCILIATION
    assert record.core_hours == Decimal("0.000")


def test_incomplete_report_with_evidence_reconciles() -> None:
    report = _lsf_report(allocated_cores=None, cpu_seconds=None)
    collected = u.collect_lsf_job_usage(report, window_start=WIN_START, window_end=WIN_END)
    assert collected.complete is False
    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    assert record.reconciliation_status == RS.PENDING_RECONCILIATION


def test_basis_measurement_missing_reconciles() -> None:
    # Evidence present, but the report has no cumulative CPU seconds while the
    # policy meters actual CPU seconds: the record reconciles.
    report = _lsf_report(cpu_seconds=None)
    collected = u.collect_lsf_job_usage(report, window_start=WIN_START, window_end=WIN_END)
    record = u.build_usage_record(
        collected,
        _identity(),
        _policy(basis=B.ACTUAL_CPU_SECONDS),
        window_start=WIN_START,
        window_end=WIN_END,
    )
    assert record.reconciliation_status == RS.PENDING_RECONCILIATION
    assert record.pending_intervals[0].reason == "actual_cpu_seconds_missing"


def test_excluded_intervals_are_recorded_not_metered() -> None:
    running = mp.UsageInterval(
        start=WIN_START,
        end=WIN_END,
        kind=K.RUNNING,
        allocated_cores=8,
        evidence_source=S.LSF_JOB_REPORT,
        evidence_version="bjobs-1",
    )
    transfer = mp.UsageInterval(
        start=WIN_START,
        end=WIN_END,
        kind=K.TRANSFER,
        allocated_cores=8,
        evidence_source=S.LSF_JOB_REPORT,
        evidence_version="bjobs-1",
    )
    collected = u.CollectedUsage(
        level=L.PARENT,
        source=S.LSF_JOB_REPORT,
        intervals=(running, transfer),
        evidence=u.UsageEvidence(source=S.LSF_JOB_REPORT, version="bjobs-1"),
        allocated_cores=8,
    )
    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    assert record.excluded_interval_count == 1
    assert record.excluded_intervals[0].decision == CD.EXCLUDED
    # Only the running interval is metered.
    assert record.core_hours == Decimal("8.000")


# --------------------------------------------------------------------------- #
# 4. UsageRecord field completeness + identity binding
# --------------------------------------------------------------------------- #

_S20_4_FIELDS = {
    "usage_id",
    "project_id",
    "account",
    "job_id",
    "attempt",
    "submission_id",
    "resource_id",
    "external_job_id",
    "collection_source",
    "resource_level",
    "resource_class",
    "basis",
    "interval_start",
    "interval_end",
    "allocated_cores",
    "core_hours",
    "raw_core_hours",
    "evidence_source",
    "evidence_version",
    "evidence_digest",
    "final",
    "reconciliation_status",
    "excluded_intervals",
    "pending_intervals",
}


def test_usage_record_has_every_required_field() -> None:
    field_names = {f.name for f in dataclasses.fields(u.UsageRecord)}
    assert _S20_4_FIELDS <= field_names


def test_record_binds_attempt_submission_resource_and_evidence() -> None:
    identity = _identity(job_id="job-A", attempt=3, submission_id="sub_abc", resource_id="node-9")
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    record = u.build_usage_record(
        collected, identity, _policy(), window_start=WIN_START, window_end=WIN_END, final=True
    )
    assert record.usage_id == u.usage_id_for("job-A", 3, "sub_abc")
    assert record.job_id == "job-A"
    assert record.attempt == 3
    assert record.submission_id == "sub_abc"
    assert record.resource_id == "node-9"
    assert record.external_job_id == "12345"
    assert record.project_id == "proj-1"
    assert record.account == "acct-1"
    assert record.evidence_source == S.LSF_JOB_REPORT
    assert record.evidence_version == "bjobs-1"
    assert len(record.evidence_digest) == 64


def test_usage_id_is_deterministic_and_idempotent() -> None:
    a = u.usage_id_for("job-A", 1, "sub_x")
    b = u.usage_id_for("job-A", 1, "sub_x")
    c = u.usage_id_for("job-A", 2, "sub_x")
    assert a == b
    assert a != c
    assert a.startswith("usg_")


def test_explicit_usage_id_is_respected() -> None:
    identity = _identity(usage_id="usg_custom")
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    record = u.build_usage_record(
        collected, identity, _policy(), window_start=WIN_START, window_end=WIN_END
    )
    assert record.usage_id == "usg_custom"


def test_record_serialises_to_json() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    record = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    decoded = json.loads(record.to_json())
    assert decoded["usage_id"] == record.usage_id
    assert decoded["core_hours"] == "8.000"
    assert decoded["evidence_digest"] == record.evidence_digest


def test_policy_version_is_recorded_and_change_cannot_relabel() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    policy_v1 = _policy(version="pv1")
    record = u.build_usage_record(
        collected, _identity(), policy_v1, window_start=WIN_START, window_end=WIN_END
    )
    assert record.policy_version == "pv1"
    assert record.policy_digest == policy_v1.snapshot_digest()
    assert record.to_dict()["policy_version"] == "pv1"
    # A later policy does not relabel the recorded measurement.
    assert _policy(version="pv2").snapshot_digest() != record.policy_digest


def test_evidence_digest_is_stable_and_input_sensitive() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    other_window = WIN_START + timedelta(hours=2)
    a = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    b = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END
    )
    c = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=other_window
    )
    assert a.evidence_digest == b.evidence_digest
    assert a.evidence_digest != c.evidence_digest


def test_final_vs_estimated_flag_is_independent_of_reconciliation() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    interim = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END, final=False
    )
    final = u.build_usage_record(
        collected, _identity(), _policy(), window_start=WIN_START, window_end=WIN_END, final=True
    )
    assert interim.final is False
    assert final.final is True
    assert interim.to_dict()["final"] is False
    assert final.confirmable is True

    # A final record with missing evidence is still not confirmable.
    pending = u.build_usage_record(
        u.collect_lsf_job_usage(
            _lsf_report(evidence_version=""), window_start=WIN_START, window_end=WIN_END
        ),
        _identity(),
        _policy(),
        window_start=WIN_START,
        window_end=WIN_END,
        final=True,
    )
    assert pending.final is True
    assert pending.confirmable is False


def test_unresolved_policy_refuses_and_produces_no_record() -> None:
    collected = u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_START, window_end=WIN_END)
    with pytest.raises(mp.MeteringRefusedError) as excinfo:
        u.build_usage_record(
            collected,
            _identity(),
            mp.MeteringPolicy(policy_version="pv1"),
            window_start=WIN_START,
            window_end=WIN_END,
        )
    assert excinfo.value.reason_code == mp.MeteringReason.METERING_BASIS_UNRESOLVED


# --------------------------------------------------------------------------- #
# 5. Validation + architecture guards
# --------------------------------------------------------------------------- #


def test_validation_rejects_bad_inputs() -> None:
    with pytest.raises(u.UsageValidationError):
        u.LsfJobReport(lsf_job_id="  ")
    with pytest.raises(u.UsageValidationError):
        u.LsfJobReport(lsf_job_id="1", allocated_cores=-1)
    with pytest.raises(u.UsageValidationError):
        u.LocalProcessSample(cpu_seconds=Decimal("-1"))
    with pytest.raises(u.UsageValidationError):
        u.UsageIdentity(job_id="j", attempt=0)
    with pytest.raises(u.UsageValidationError):
        u.UsageEvidence(source="progress-timer", version="1")
    # Reversed / naive windows.
    with pytest.raises(u.UsageValidationError):
        u.collect_lsf_job_usage(_lsf_report(), window_start=WIN_END, window_end=WIN_START)
    with pytest.raises(u.UsageValidationError):
        u.collect_lsf_job_usage(
            _lsf_report(), window_start=datetime(2026, 10, 1), window_end=WIN_END
        )


def _import_roots(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_is_isolated_from_acp_and_cccp_and_networks() -> None:
    roots = _import_roots(_MODULE_SRC.read_text(encoding="utf-8"))
    assert "acp" not in roots, "acp_services must not import acp"
    assert "cccp" not in roots, "usage collection must not import cccp"
    for forbidden in ("subprocess", "socket", "paramiko", "requests"):
        assert forbidden not in roots, f"{forbidden} is not allowed in usage"


def test_usage_record_has_no_progress_or_browser_timer_field() -> None:
    field_names = {f.name for f in dataclasses.fields(u.UsageRecord)}
    for forbidden in ("progress", "browser", "elapsed", "wall_clock", "percent"):
        assert not any(forbidden in name for name in field_names), forbidden


def test_collection_sources_exclude_progress_and_browser() -> None:
    assert "progress" not in S.ALL
    assert "browser" not in S.ALL
    assert S.ALL == {S.LOCAL_PROCESS_GROUP, S.LOCAL_POLICY_ALLOCATED, S.LSF_JOB_REPORT}


def test_usage_record_carries_no_price_or_rate_field() -> None:
    field_names = {f.name for f in dataclasses.fields(u.UsageRecord)}
    assert not any(
        word in name for name in field_names for word in ("price", "rate", "credit", "cost")
    )
    assert "NOT part of scientific identity" in u.USAGE_NOT_SCIENTIFIC_IDENTITY_NOTE


def test_module_has_no_price_numeric_constant() -> None:
    tree = ast.parse(_MODULE_SRC.read_text(encoding="utf-8"))
    price_words = ("price", "rate", "credit", "usd", "cost")
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and any(
                word in target.id.lower() for word in price_words
            ):
                raise AssertionError(f"price/rate constant found: {target.id}")


def test_vocabularies_cover_expected_values() -> None:
    assert L.ALL == {L.PARENT, L.CHILD}
    assert RC.ALL == {RC.CPU}
    assert RS.ALL == {RS.COMPLETE, RS.PENDING_RECONCILIATION}
