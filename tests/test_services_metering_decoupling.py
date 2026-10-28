"""B10-4 (todo 56) — decouple usage collection from science completion / result access.

Exercises ``acp_services.runtime.metering_decoupling``:

1. **Order independence.**  Usage ingestion and publication produce the same
   observable state regardless of which runs first.
2. **Mutual non-blocking.**  A publication failure never blocks (or corrupts)
   usage collection, and a usage-ledger failure never blocks publication.  The
   publication-failure-while-usage-complete case is covered explicitly.
3. **Result reads are not accounting-gated.**  An exhausted balance / depleted
   quota / pending-reconciliation ledger never locks an already-produced result;
   readability depends only on prepared availability.
4. **Three negative assertions.**  ``progress``, ``browser-timer`` and
   ``monitoring-sample`` are never a usage basis, statically and behaviourally;
   a monitoring sample can never be appended to the ledger.
5. **Retry independence + isolation.**  Ledger writes are idempotent by
   ``usage_id`` and retryable; the module imports no ``acp``/``cccp``/network.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from acp_services.runtime import metering_decoupling as md
from acp_services.runtime import metering_policy as mp
from acp_services.runtime import usage as u

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIR = _REPO_ROOT / "src" / "acp_services" / "runtime"
_DECOUPLING_SRC = _RUNTIME_DIR / "metering_decoupling.py"
_LEDGER_SRC = _RUNTIME_DIR / "usage.py"

UTC = timezone.utc
WIN_START = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
WIN_END = WIN_START + timedelta(hours=1)

RS = md.RejectedLedgerSource
S = u.CollectionSource

#: The Services side may never import these legacy roots (injected into the audit).
_FORBIDDEN_ROOTS = ("acp", "cccp")


# --------------------------------------------------------------------------- #
# Fakes (structural ports)
# --------------------------------------------------------------------------- #


class _FakeLedger:
    """An in-memory usage ledger that can inject transient failures."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.records: dict[str, u.UsageRecord] = {}
        self.appends = 0
        self._fail_times = fail_times

    def append(self, record: u.UsageRecord) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise md.UsageLedgerError("transient ledger hiccup")
        self.records[record.usage_id] = record
        self.appends += 1

    def get(self, usage_id: str) -> u.UsageRecord | None:
        return self.records.get(usage_id)


class _FakePublisher:
    def __init__(self, *, fail: bool = False, fail_times: int = 0) -> None:
        self.fail = fail
        self.fail_times = fail_times
        self.calls = 0

    def publish(self, job_id: str, attempt: int) -> None:
        self.calls += 1
        if self.fail or self.fail_times > 0:
            if self.fail_times > 0:
                self.fail_times -= 1
            raise md.PublicationError("publication backend unavailable")


class _FakeAvailability:
    def __init__(self, *, readable: bool = True) -> None:
        self.readable = readable

    def is_readable(self, job_id: str) -> bool:
        return self.readable


class _FakeMonitorSample:
    """A monitoring telemetry object — must never reach the ledger."""

    def __init__(self, source: str = RS.MONITOR_SAMPLE) -> None:
        self.source = source
        self.sampled_at = WIN_START.isoformat()


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _policy() -> mp.MeteringPolicy:
    return mp.MeteringPolicy(
        policy_version="pv-2026-10-19",
        basis=mp.MeteringBasis.ALLOCATED_CORE_HOURS,
        rounding=mp.RoundingSpec(rule=mp.RoundingRule.HALF_UP, decimal_places=3),
        period=mp.BillingPeriod(unit=mp.PeriodUnit.MONTH, timezone="UTC"),
    )


def _record(*, job_id: str = "20261001_001_optimize", attempt: int = 1) -> u.UsageRecord:
    collected = u.collect_local_allocated_usage(
        allocated_cores=8,
        window_start=WIN_START,
        window_end=WIN_END,
        evidence_version="local-alloc-1",
        resource_id="node-07",
    )
    return u.build_usage_record(
        collected,
        u.UsageIdentity(job_id=job_id, attempt=attempt, account="acct-1", project_id="proj-1"),
        _policy(),
        window_start=WIN_START,
        window_end=WIN_END,
        final=True,
    )


def _coordinator(
    *,
    ledger: _FakeLedger | None = None,
    publisher: _FakePublisher | None = None,
    readable: bool = True,
) -> tuple[md.DecoupledAttemptCoordinator, _FakeLedger, _FakePublisher]:
    ledger = ledger or _FakeLedger()
    publisher = publisher or _FakePublisher()
    coord = md.DecoupledAttemptCoordinator(
        usage=md.UsageIngestChannel(ledger),
        publication=md.PublicationChannel(publisher),
        results=md.ResultAccessGate(_FakeAvailability(readable=readable)),
    )
    return coord, ledger, publisher


# --------------------------------------------------------------------------- #
# 1. Order independence
# --------------------------------------------------------------------------- #


def test_usage_and_publication_are_order_independent() -> None:
    record = _record()
    first, ledger_a, pub_a = _coordinator()
    second, ledger_b, pub_b = _coordinator()

    out_a = first.run(record, job_id=record.job_id, attempt=1, order=("usage", "publication"))
    out_b = second.run(record, job_id=record.job_id, attempt=1, order=("publication", "usage"))

    assert out_a.usage_recorded is True
    assert out_b.usage_recorded is True
    assert ledger_a.records == ledger_b.records
    assert out_a.publication.published is True
    assert out_b.publication.published is True
    assert pub_a.calls == pub_b.calls == 1


def test_order_must_be_a_permutation() -> None:
    coord, _, _ = _coordinator()
    with pytest.raises(md.DecouplingError):
        coord.run(_record(), job_id="j", attempt=1, order=("usage", "usage"))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 2. Mutual non-blocking
# --------------------------------------------------------------------------- #


def test_publication_failure_never_blocks_usage_collection() -> None:
    record = _record()
    ledger = _FakeLedger()
    coord, _, _ = _coordinator(ledger=ledger, publisher=_FakePublisher(fail=True))

    for order in (("usage", "publication"), ("publication", "usage")):
        ledger.records.clear()
        outcome = coord.run(record, job_id=record.job_id, attempt=1, order=order)  # type: ignore[arg-type]
        assert outcome.publication.published is False
        assert outcome.publication.error
        # Usage is complete despite publication failure, regardless of order.
        assert outcome.usage_recorded is True
        assert record.usage_id in ledger.records


def test_usage_ledger_failure_never_blocks_publication() -> None:
    record = _record()
    ledger = _FakeLedger(fail_times=10)
    publisher = _FakePublisher()
    coord, _, _ = _coordinator(ledger=ledger, publisher=publisher)

    outlet = coord.run(record, job_id=record.job_id, attempt=1, order=("usage", "publication"))
    assert outlet.usage.accepted is False
    assert outlet.usage.error
    # Publication is unaffected by the ledger failure.
    assert outlet.publication.published is True
    assert publisher.calls == 1

    # And with publication first, usage still runs (and still fails independently).
    publisher.calls = 0
    outlet2 = coord.run(record, job_id=record.job_id, attempt=1, order=("publication", "usage"))
    assert outlet2.publication.published is True
    assert outlet2.usage.accepted is False


def test_publication_failure_while_usage_complete() -> None:
    record = _record()
    coord, ledger, _ = _coordinator(publisher=_FakePublisher(fail=True))
    outcome = coord.run(record, job_id=record.job_id, attempt=1)

    assert outcome.usage.accepted is True
    stored = ledger.records[record.usage_id]
    assert stored.confirmable is True
    assert stored.core_hours == Decimal("8.000")
    assert outcome.publication.published is False
    # The stored usage record is untouched by the failed publication.
    assert ledger.records[record.usage_id] == stored


def test_publication_channel_retries_without_touching_usage() -> None:
    publisher = _FakePublisher(fail_times=2)
    channel = md.PublicationChannel(publisher)
    outcome = channel.publish("job-1", 1, max_attempts=3)
    assert outcome.published is True
    assert outcome.attempts == 3
    assert publisher.calls == 3


# --------------------------------------------------------------------------- #
# 3. Result reads are not accounting-gated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "accounting_state",
    ["ok", "insufficient_balance", "quota_exhausted", "pending_reconciliation", "suspended"],
)
def test_result_read_not_blocked_by_any_accounting_state(accounting_state: str) -> None:
    gate = md.ResultAccessGate(_FakeAvailability(readable=True))
    decision = gate.decide("job-1", accounting_state=accounting_state)
    assert decision.readable is True
    assert decision.blocked_by_accounting is False
    assert decision.reason == "prepared"
    assert decision.accounting_state == accounting_state


def test_result_read_denied_only_by_availability() -> None:
    gate = md.ResultAccessGate(_FakeAvailability(readable=False))
    decision = gate.decide("job-1", accounting_state="ok")
    assert decision.readable is False
    assert decision.reason == "not_prepared"
    assert decision.blocked_by_accounting is False


# --------------------------------------------------------------------------- #
# 4. Three negative assertions
# --------------------------------------------------------------------------- #


def test_progress_is_never_a_usage_basis() -> None:
    assert md.is_usage_basis(RS.PROGRESS) is False
    with pytest.raises(md.UntrustedUsageBasisError):
        md.require_trusted_usage_source(RS.PROGRESS)


def test_browser_timer_is_never_a_usage_basis() -> None:
    assert md.is_usage_basis(RS.BROWSER_TIMER) is False
    with pytest.raises(md.UntrustedUsageBasisError):
        md.require_trusted_usage_source(RS.BROWSER_TIMER)


def test_monitoring_sample_is_never_a_usage_basis() -> None:
    assert md.is_usage_basis(RS.MONITOR_SAMPLE) is False
    with pytest.raises(md.UntrustedUsageBasisError):
        md.require_trusted_usage_source(RS.MONITOR_SAMPLE)


def test_rejected_signals_are_disjoint_from_trusted_sources() -> None:
    assert RS.ALL.isdisjoint(S.ALL)
    for trusted in S.ALL:
        assert md.is_usage_basis(trusted) is True
        assert md.require_trusted_usage_source(trusted) == trusted


def test_unknown_source_is_refused() -> None:
    assert md.is_usage_basis("made-up-signal") is False
    with pytest.raises(md.UntrustedUsageBasisError):
        md.require_trusted_usage_source("made-up-signal")


def test_monitoring_sample_can_never_reach_the_ledger() -> None:
    ledger = _FakeLedger()
    channel = md.UsageIngestChannel(ledger)
    with pytest.raises(md.DecouplingError):
        channel.record(_FakeMonitorSample())  # type: ignore[arg-type]
    assert ledger.records == {}
    assert "monitoring" in md.MONITORING_IS_NOT_LEDGER_NOTE


def test_ledger_module_has_no_progress_or_timer_vocabulary() -> None:
    audit = md.audit_decoupling(
        ledger_source=_LEDGER_SRC.read_text(encoding="utf-8"),
        decoupling_source=_DECOUPLING_SRC.read_text(encoding="utf-8"),
        forbidden_import_roots=_FORBIDDEN_ROOTS,
    )
    assert audit.passed is True, [v.detail for v in audit.violations]


# --------------------------------------------------------------------------- #
# 5. Static audit + isolation
# --------------------------------------------------------------------------- #


def test_static_audit_flags_forbidden_import() -> None:
    bad_ledger = "from acp.calculations import executor\n"
    audit = md.audit_decoupling(
        ledger_source=bad_ledger,
        decoupling_source="x = 1\n",
        forbidden_import_roots=_FORBIDDEN_ROOTS,
    )
    assert audit.passed is False
    assert md.DecouplingViolationKind.FORBIDDEN_IMPORT in audit.kinds()
    assert any(v.module == "ledger" and v.detail == "acp" for v in audit.violations)


def test_static_audit_flags_progress_identifier() -> None:
    bad_ledger = "def progress_fraction() -> float:\n    return 1.0\n"
    audit = md.audit_decoupling(
        ledger_source=bad_ledger,
        decoupling_source="x = 1\n",
        forbidden_import_roots=_FORBIDDEN_ROOTS,
    )
    assert audit.passed is False
    assert md.DecouplingViolationKind.REJECTED_SIGNAL_TERM in audit.kinds()
    assert any(v.detail == "progress" for v in audit.violations)


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
    roots = _import_roots(_DECOUPLING_SRC.read_text(encoding="utf-8"))
    assert "acp" not in roots, "acp_services must not import acp"
    assert "cccp" not in roots, "metering decoupling must not import cccp"
    for forbidden in ("subprocess", "socket", "paramiko", "requests"):
        assert forbidden not in roots, f"{forbidden} is not allowed in metering_decoupling"


# --------------------------------------------------------------------------- #
# 6. Retry independence / idempotency
# --------------------------------------------------------------------------- #


def test_usage_ingest_is_idempotent_by_usage_id() -> None:
    ledger = _FakeLedger()
    channel = md.UsageIngestChannel(ledger)
    record = _record()

    first = channel.record(record)
    assert first.accepted is True
    assert first.idempotent is False
    assert ledger.appends == 1

    second = channel.record(record)
    assert second.accepted is False
    assert second.idempotent is True
    assert second.recorded is True
    assert ledger.appends == 1  # no double count


def test_usage_ingest_retries_transient_ledger_failure() -> None:
    ledger = _FakeLedger(fail_times=2)
    channel = md.UsageIngestChannel(ledger)
    receipt = channel.record(_record(), max_attempts=3)
    assert receipt.accepted is True
    assert receipt.attempts == 3
    assert ledger.appends == 1


def test_usage_ingest_exhausted_retries_reports_retryable_failure() -> None:
    ledger = _FakeLedger(fail_times=5)
    channel = md.UsageIngestChannel(ledger)
    receipt = channel.record(_record(), max_attempts=3)
    assert receipt.accepted is False
    assert receipt.recorded is False
    assert receipt.attempts == 3
    assert receipt.error
    assert ledger.appends == 0


def test_usage_ingest_rejects_invalid_max_attempts() -> None:
    channel = md.UsageIngestChannel(_FakeLedger())
    with pytest.raises(md.DecouplingError):
        channel.record(_record(), max_attempts=0)
