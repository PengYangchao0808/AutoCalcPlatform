"""B13-2 (plan todo 67) — fault-injection matrix for the Services seams.

Executable form of the W14 fault matrix.  Every scenario injects a **real**
fault through a public seam (SQLite CAS, the transactional outbox, the transfer
pipeline, the frozen worker, the access policy, the prepare-operation state
machine, the schema migrator) against a **tmp** DB / directory.  No real cluster
and no product-code edit is involved: crashes are driven by the existing
``_fault_point_hook``, by protocol doubles that drop an ack / raise ``OSError``,
or by a deliberately contracted database file.

The nine required scenario classes:

======================  =====================================================
``S01``                 unknown submission (valid lease -> never
                        ``not_accepted``)
``S02``                 duplicate / out-of-order receipts
``S03``                 process crash / lost connection (restart recovery)
``S04``                 disk full (write-failure safety)
``S05``                 transfer interruption (resume / retry, no ``.tmp``)
``S06``                 old worker still writing (generation/fence + physical
                        write protection)
``S07``                 permission revocation immediately effective
``S08``                 budget race (B11 price-gated -> DEFERRED, not faked)
``S09``                 post-recovery external-job reconciliation
                        (reconcile BEFORE dispatch)
======================  =====================================================

Plus the persistence-boundary crash windows:

* ``P01`` CAS committed but notification pending (outbox backlog replay);
* ``P02`` outbox delivered but not marked (crash before the ack);
* ``P03`` file published / DB unregistered and DB record / file missing;
* ``P04`` schema expand / contract combined with a code rollback.

``FAULT_MATRIX`` is the machine-readable index; ``test_fault_matrix_report_
lists_every_scenario`` fails when any scenario loses its test or its row in
``tests/baseline/services-evidence/fault_matrix_report.md`` — "not observed" is
never a pass.

Run with: ``python3.11 -m pytest tests/test_services_fault_matrix.py -q``
"""

from __future__ import annotations

import errno
import importlib.util
import sqlite3
import time
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.submission import (
    register_submit_worker,
    release_submit_worker,
    submission_id_for,
    submit_lease_valid,
)
from acp.scheduler.store import JobStateConflictError, JobStore
from acp.services_events_bootstrap import (
    JobEventLogPort,
    JobStoreOutboxPort,
    open_outbox_delivery,
)
from acp_services.contracts.access import (
    AccessAction,
    AccessScope,
    DeploymentTier,
    LocalPrincipal,
    LocalSingleAccessPolicy,
    Principal,
    ReasonCode,
    ResourceGrant,
)
from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionReceipt,
    expected_submission_id,
)
from acp_services.contracts.events import EventScope, OwnerType
from acp_services.data import contracts as dc
from acp_services.data import operations as ops
from acp_services.data import transfer as tx
from acp_services.runtime.outbox import (
    OutboxDeliveryService,
    OutboxEventSpec,
)
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import (
    FrozenPlan,
    FrozenPlanIdentity,
    FrozenStep,
    FrozenWorker,
    ReceiptConflictError,
    TaskExecutionOutcome,
    read_receipt,
)

_JOB = OwnerType.JOB
_SCOPE = EventScope.TASK
_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPORT = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "fault_matrix_report.md"
_ENOSPC = errno.ENOSPC


# ---------------------------------------------------------------------------
# machine-readable index (mirrored by the tracked report)
# ---------------------------------------------------------------------------

#: id -> (scenario, injected fault, assertion form).  The report mirrors this
#: table row-for-row; the guard below proves neither side can silently drop a
#: scenario.
FAULT_MATRIX: dict[str, tuple[str, str, str]] = {
    "S01": (
        "unknown submission",
        "submit intent with a live persisted lease; reconcile cannot see the job",
        "no state change (kept pending, never not_accepted)",
    ),
    "S02": (
        "duplicate / out-of-order receipts",
        "same receipt replayed; stale attempt/submission/release arrives late",
        "no state change (idempotent replay; rejection leaves CAS port untouched)",
    ),
    "S03": (
        "process crash / lost connection",
        "worker dies holding a lease; a fresh service over the same DB restarts",
        "no lost fact (undecided row replayed; availability committed once)",
    ),
    "S04": (
        "disk full",
        "file write raises OSError(ENOSPC)",
        "no state change (no partial publish/record; tmp removed; failure recorded)",
    ),
    "S05": (
        "transfer interruption",
        "transient source failure then a partial (size-mismatch) read, then retry",
        "reconcilable (retryable rejection; retry publishes; no .tmp residue)",
    ),
    "S06": (
        "old worker still writing",
        "generation bumps mid-transfer; a stale attempt and a stale receipt follow",
        "no state change (discarded/refused; existing receipt bytes untouched)",
    ),
    "S07": (
        "permission revocation",
        "grant revoked / publish denied / authorization version bumped",
        "no state change (denied immediately; no publish; operation rolled back)",
    ),
    "S08": (
        "budget race (B11)",
        "credit-ledger reservation competition",
        "DEFERRED — B11 ledger price-gated; module absent (no fake pass)",
    ),
    "S09": (
        "post-recovery reconciliation",
        "restart with an indeterminate external submission",
        "reconcilable before dispatch (reconcile runs first; zero bsub)",
    ),
    "P01": (
        "CAS committed, notification pending",
        "commit succeeds, delivery never runs before the crash",
        "no lost fact (pending outbox row replays once; state unchanged)",
    ),
    "P02": (
        "outbox delivered, not marked",
        "durable append succeeds, the ack is dropped",
        "reconcilable (replay dedups; single append; state unchanged)",
    ),
    "P03": (
        "file published / DB unregistered; DB record / file missing",
        "orphan file with no row; a published row whose file was deleted",
        "reconcilable (classified, never deleted; row flipped to missing)",
    ),
    "P04": (
        "schema expand/contract + code rollback",
        "legacy schema expanded; then a contracted DB whose ledger says applied",
        "expand idempotent + rollback-tolerant; contraction detected, not masked",
    ),
}


def _scenario_prefix(scenario_id: str) -> str:
    return "test_" + scenario_id.lower() + "_"


def test_fault_matrix_report_lists_every_scenario() -> None:
    """Every matrix scenario has a test and a row in the tracked report."""
    module_tests = [name for name in globals() if name.startswith("test_")]
    assert _REPORT.is_file(), f"missing tracked fault-matrix report: {_REPORT}"
    report = _REPORT.read_text(encoding="utf-8")
    for scenario_id in FAULT_MATRIX:
        prefix = _scenario_prefix(scenario_id)
        assert any(name.startswith(prefix) for name in module_tests), (
            f"{scenario_id} has no test named {prefix}* — a scenario may not be skipped"
        )
        assert scenario_id in report, f"{scenario_id} is missing from fault_matrix_report.md"
    assert "S08" in report and "DEFERRED" in report.split("S08", 1)[1][:400], (
        "the B11 budget-race row must be recorded DEFERRED, never faked as PASS"
    )


# ====================================================================== #
# Shared doubles
# ====================================================================== #


class _FakeExecutor:
    """Counting task executor (no scientific binary)."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        return TaskExecutionOutcome(state="completed", result_digest="sha256:result")


class _Satisfied:
    def evaluate_prerequisite(self, prior: object, kind: object, item_symbols: object) -> object:
        return type("_Outcome", (), {"satisfied": True, "reason": ""})()


class _AdvanceSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def advance(
        self, job_id: str, *, expected_revision: int, **fields: object
    ) -> dict[str, object]:
        self.calls.append((job_id, expected_revision, fields))
        return {"job_id": job_id, "revision": expected_revision + 1}


def _envelope(**overrides: object) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "envelope_version": CURRENT_ENVELOPE_VERSION,
        "job_id": "job-1",
        "attempt": 1,
        "submission_id": expected_submission_id("job-1", 1),
        "release_id": "rel-1",
        "release_digest": "reldig-1",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_digest": "v2:plan-digest",
        "plan_identity": "v2:plan-identity",
        "step_identities": ("v2:step-0",),
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)  # type: ignore[arg-type]


def _plan(envelope: ExecutionEnvelope) -> FrozenPlan:
    return FrozenPlan(
        payload={"task": "optimize"},
        steps=(FrozenStep("optimize"),),
        item_symbols=("C", "O"),
        identity=FrozenPlanIdentity(
            envelope.plan_digest, envelope.plan_identity, envelope.step_identities
        ),
    )


# ---------------------------------------------------------------------------
# transfer doubles (mirror tests/test_services_transfer_verify.py)
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, data: bytes = b"", *, revision: int = 0, fail=None, on_read=None) -> None:
        self.data = data
        self.revision = revision
        self.fail = fail
        self.on_read = on_read
        self.calls: list[tuple[str, int, str]] = []

    def read(self, job_id, attempt, relative_path, *, byte_range=None) -> tx.SourceRead:
        self.calls.append((job_id, attempt, relative_path))
        if self.on_read is not None:
            self.on_read()
        if self.fail is not None:
            raise self.fail
        return tx.SourceRead(data=self.data, size_bytes=len(self.data), revision=self.revision)


class _FakeAuthorization:
    def __init__(self, deny: tuple[str, ...] = ()) -> None:
        self.deny = set(deny)
        self.calls: list[str] = []

    def authorize(self, *, job_id, attempt, relative_path, action, authorization_version) -> bool:
        self.calls.append(action)
        return action not in self.deny


class _FakeCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.attempt: int | None = None
        self.generation = 0
        self.published: dict[str, bytes] = {}
        self.pushed: list[str] = []

    def _guard(self, job_id: str, relative_path: str) -> Path:
        norm = tx.normalize_relative_path(relative_path)
        if ".." in norm.split("/"):
            raise ValueError("escapes the cache directory")
        return self.root / job_id / norm

    def guarded_path(self, job_id: str, relative_path: str) -> Path:
        return self._guard(job_id, relative_path)

    def fence(self, job_id: str) -> tx.FenceSnapshot:
        return tx.FenceSnapshot(attempt=self.attempt, generation=self.generation)

    def publish(self, job_id, relative_path, tmp_path, *, attempt, expected_generation):
        if self.generation != expected_generation:
            return tx.PublishOutcome(published=False, discarded=True, reason="generation_changed")
        if self.attempt is not None and attempt < self.attempt:
            return tx.PublishOutcome(published=False, discarded=True, reason="attempt_advanced")
        if self.attempt is None or attempt > self.attempt:
            self.attempt = attempt
        target = self._guard(job_id, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        import os

        os.replace(tmp_path, str(target))
        self.published[tx.normalize_relative_path(relative_path)] = target.read_bytes()
        return tx.PublishOutcome(published=True, cached_path=str(target))

    def exists(self, job_id: str, relative_path: str) -> bool:
        try:
            return self._guard(job_id, relative_path).is_file()
        except ValueError:
            return False

    def list_files(self, job_id: str) -> tuple[str, ...]:
        root = self.root / job_id
        if not root.is_dir():
            return ()
        return tuple(sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()))

    def push_paths(self, job_id: str, relative_paths) -> list[str]:
        self.pushed.extend(relative_paths)
        return list(relative_paths)


def _pipeline(tmp_path: Path, *, source, authorization=None, adoption=None):
    cache = _FakeCache(tmp_path / ".remote_cache")
    store = tx.TransferPublicationStore(tmp_path / "publications.db")
    pipeline = tx.TransferPipeline(
        cache=cache,
        source=source,
        authorization=authorization or _FakeAuthorization(),
        publications=store,
        adoption=adoption,
    )
    return pipeline, cache, store


def _request(
    data: bytes,
    *,
    job_id: str = "job_1",
    attempt: int = 1,
    relative_path: str = "RESULT/result_manifest.json",
    expected_revision: int = 0,
) -> tx.TransferRequest:
    import hashlib

    return tx.TransferRequest(
        job_id=job_id,
        attempt=attempt,
        relative_path=relative_path,
        resource_class=tx.ResourceClass.ATTEMPT_ARTIFACT,
        receipt=tx.TransferReceipt(
            job_id=job_id,
            attempt=attempt,
            relative_path=relative_path,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        ),
        expected_revision=expected_revision,
    )


def _tmp_files(root: Path) -> list[Path]:
    return [p for p in Path(root).rglob("*.tmp")]


def _spec() -> JobSpec:
    return JobSpec(workflow="fake", name="t", input={"source": "CCO"})


def _job_record(root: Path, job_id: str, *, status: JobStatus = JobStatus.QUEUED) -> JobRecord:
    work_dir = root / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    return JobRecord(id=job_id, spec=_spec(), status=status, work_dir=str(work_dir))


def _event_path(root: Path, job_id: str) -> Path:
    return root / "runs" / job_id / "events.jsonl"


def _deliver(store: JobStore, root: Path, *, notifier=None) -> OutboxDeliveryService:
    return open_outbox_delivery(
        store, path_for=lambda job_id: _event_path(root, job_id), notifier=notifier
    )


def _spec_event(payload: dict) -> OutboxEventSpec:
    return OutboxEventSpec(event_type="state.changed", payload=payload)


# ====================================================================== #
# S01 — unknown submission (valid lease -> never not_accepted)
# ====================================================================== #


class _ReconcileSpy:
    """Remote-runner double: scripted reconcile verdicts, bsub counter."""

    def __init__(self, verdict: str = "unknown") -> None:
        self.verdict = verdict
        self.reconcile_calls = 0
        self.submit_calls = 0

    def reconcile_submission(self, record: JobRecord) -> str:
        self.reconcile_calls += 1
        return self.verdict

    def recover_job_state(self, record: JobRecord) -> bool:
        return bool(record.remote_job_id)

    def submit_remote(self, *args: object, **kwargs: object) -> str:
        self.submit_calls += 1
        raise AssertionError("a reconciled unknown submission must never be dispatched")


def test_s01_unknown_submission_within_lease_is_not_not_accepted(tmp_path: Path) -> None:
    # (a) The lease gate itself: a persisted live lease keeps the intent valid.
    meta = {
        "submission_id": submission_id_for("j1", 1),
        "lease_expires_at": "2999-01-01T00:00:00+00:00",
    }
    assert submit_lease_valid(meta) is True, "a live lease must never be judged not_accepted"
    # no decodable deadline but an issued submission id -> conservatively valid.
    assert submit_lease_valid({"submission_id": submission_id_for("j1", 1)}) is True
    # an explicit release invalidates it immediately.
    sid = submission_id_for("released", 1)
    register_submit_worker(sid, "pid:1:2:1", 120)
    try:
        assert submit_lease_valid({"submission_id": sid}) is True
        release_submit_worker(sid)
        assert submit_lease_valid({"submission_id": sid}) is False
    finally:
        release_submit_worker(sid)

    # (b) The controller reconcile: an unreconcilable ("unknown") submission
    # keeps the job pending; it is never flipped to not_accepted / failed.
    mgr = JobManager(run_root=tmp_path, max_running=1)
    mgr._poll_stop.set()
    try:
        runner = _ReconcileSpy(verdict="unknown")
        mgr.remote_runner = runner  # type: ignore[assignment]
        job_id = "unknown-submit"
        record = JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name=job_id),
            status=JobStatus.STARTING,
            work_dir=str(tmp_path / "runs" / job_id),
            result={
                "remote": {
                    "submission_id": submission_id_for(job_id, 1),
                    "submit_state": "intent",
                    "lease_expires_at": "2999-01-01T00:00:00+00:00",
                }
            },
        )
        mgr.store.create(record)
        try:
            mgr._reconcile_submission_record(mgr.store.get(job_id))
        finally:
            pass
        stored = mgr.store.get(job_id)
        assert stored is not None and stored.status == JobStatus.STARTING, (
            "an unknown submission within its lease must stay pending, never not_accepted"
        )
        assert runner.reconcile_calls == 1 and runner.submit_calls == 0
    finally:
        mgr.shutdown()


# ====================================================================== #
# S02 — duplicate / out-of-order receipts
# ====================================================================== #


def test_s02_duplicate_and_out_of_order_receipts(tmp_path: Path) -> None:
    env = _envelope()
    plan = _plan(env)
    executor = _FakeExecutor()
    worker = FrozenWorker(executor=executor, step_requirements=_Satisfied())
    receipt_path = tmp_path / "receipt.json"

    first = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)
    assert executor.calls == 1
    # Duplicate redelivery of the *same* receipt: no re-execution.
    second = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)
    assert executor.calls == 1, "a duplicate receipt must not re-execute the attempt"
    assert second == first

    # Out-of-order receipts are rejected against the current identity.
    port = _AdvanceSpy()
    stale_attempt = ExecutionReceipt.from_dict({**first.to_dict(), "attempt": 2})
    verdict = verify_receipt(
        stale_attempt,
        env,
        current_attempt=1,
        current_submission_id=env.submission_id,
        current_release_id=env.release_id,
        owner_token=env.owner,
        fencing_token=env.fencing_token,
    )
    assert verdict.accepted is False and verdict.code == "stale_attempt"
    with pytest.raises(ReceiptRejectedError):
        advance_job_state(
            port,
            env,
            stale_attempt,
            current_attempt=1,
            current_submission_id=env.submission_id,
            current_release_id=env.release_id,
            owner_token=env.owner,
            fencing_token=env.fencing_token,
            expected_revision=0,
        )
    assert port.calls == [], "a rejected receipt must never touch the CAS port"

    # A late stale *release* receipt is rejected too.
    stale_release = ExecutionReceipt.from_dict({**first.to_dict(), "release_id": "rel-OLD"})
    assert (
        verify_receipt(
            stale_release,
            env,
            current_attempt=1,
            current_submission_id=env.submission_id,
            current_release_id=env.release_id,
            owner_token=env.owner,
            fencing_token=env.fencing_token,
        ).code
        == "stale_release"
    )

    # An accepted receipt advances exactly once; a duplicate CAS advance with the
    # same expected revision is refused (out-of-order controller write).
    store = JobStore(tmp_path / "acp_jobs.db")
    store.create(_job_record(tmp_path, "s02"))
    store.transition(
        "s02",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
    )
    with pytest.raises(JobStateConflictError):
        store.transition(
            "s02",
            expected_status=JobStatus.RUNNING,
            expected_revision=0,
            status=JobStatus.COMPLETED,
        )


# ====================================================================== #
# S03 — process crash / lost connection (restart recovery)
# ====================================================================== #


def test_s03_process_crash_restart_replays_undecided_operation(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    first = ops.PrepareOperationService(db, retry_backoff_seconds=0.0, default_lease_seconds=10.0)
    op = first.enqueue(
        ops.PrepareRequest(
            job_id="j1",
            attempt=1,
            resource_id="catalog:Confsearch",
            resource_kind=dc.ResourceKind.CACHE,
        )
    )
    lease = first.lease_next()
    assert lease is not None and first.get(op.operation_id).state is ops.PrepareState.RUNNING
    first.close()  # crash: the in-memory worker is gone, the row survives

    reopened = ops.PrepareOperationService(
        db, retry_backoff_seconds=0.0, default_lease_seconds=10.0
    )
    try:
        report = reopened.recover()
        assert report.requeued == 1, "the undecided row must be replayed, not lost"
        assert reopened.get(op.operation_id).state is ops.PrepareState.QUEUED
        fresh = reopened.lease_next()
        assert fresh is not None and fresh.owner_generation > lease.owner_generation
        done = reopened.complete(fresh)
        assert done.state is ops.PrepareState.SUCCEEDED
        assert reopened.availability(op.operation_id).availability is dc.Availability.READY
    finally:
        reopened.close()


# ====================================================================== #
# S04 — disk full (write-failure safety)
# ====================================================================== #


def test_s04_disk_full_write_failure_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"result-bytes"
    pipeline, cache, store = _pipeline(tmp_path, source=_FakeSource(payload))

    real_open = open

    def _enospc_open(*args: object, **kwargs: object):
        # Only the artifact write (binary tmp file) fails; mkstemp uses os.open.
        if len(args) >= 2 and args[1] == "wb":
            raise OSError(_ENOSPC, "No space left on device")
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(tx, "open", _enospc_open, raising=False)
    with pytest.raises(OSError) as excinfo:
        pipeline.prepare(_request(payload))
    assert excinfo.value.errno == _ENOSPC
    monkeypatch.undo()

    # Safety: no partial publish, no publication record, no tmp residue.
    assert cache.published == {}
    assert store.list_all() == ()
    assert _tmp_files(tmp_path) == []

    # Write-failure safety also propagates through the durable state machine:
    # the operation is never marked ready, it is recorded as a retryable failure
    # and finally terminal.
    service = ops.PrepareOperationService(
        tmp_path / "ops.db", retry_backoff_seconds=0.0, default_lease_seconds=10.0
    )

    class _EnospcExecutor:
        def execute(self, lease: ops.PrepareLease) -> ops.PrepareExecutionResult:
            raise OSError(_ENOSPC, "No space left on device")

    op = service.enqueue(
        ops.PrepareRequest(
            job_id="j1",
            attempt=1,
            resource_id="catalog:Confsearch",
            resource_kind=dc.ResourceKind.CACHE,
        )
    )
    worker = ops.PrepareOperationWorker(service, _EnospcExecutor(), poll_interval=0.01)
    worker.start()
    try:
        deadline = time.monotonic() + 5.0
        record = service.get(op.operation_id)
        while time.monotonic() < deadline and record is not None and not record.is_terminal:
            time.sleep(0.01)
            record = service.get(op.operation_id)
    finally:
        worker.stop()
    assert record is not None and record.state is ops.PrepareState.FAILED
    assert record.error_code == "OSError"
    assert service.availability(op.operation_id).availability is not dc.Availability.READY
    service.close()


# ====================================================================== #
# S05 — transfer interruption (resume / retry, no .tmp)
# ====================================================================== #


def test_s05_transfer_interruption_resumes_without_tmp_residue(tmp_path: Path) -> None:
    payload = b"the full content"
    # 1. transient source failure -> retryable rejection, nothing published.
    pipeline, cache, store = _pipeline(
        tmp_path,
        source=_FakeSource(
            payload,
            fail=tx.TransferSourceError(tx.SourceFailureKind.TRANSIENT, "connection reset"),
        ),
    )
    interrupted = pipeline.prepare(_request(payload))
    assert interrupted.rejection is tx.RejectionCode.SOURCE_UNAVAILABLE
    assert interrupted.retryable is True, "a transient interruption must be resumable"
    assert cache.published == {} and _tmp_files(tmp_path) == []

    # 2. a partial (size-mismatch) read is rejected, still nothing published.
    partial, cache2, _store2 = _pipeline(tmp_path / "p2", source=_FakeSource(b"short"))
    request_partial = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="RESULT/result.json",
        expected=tx.ExpectedDigest(sha256=tx.sha256_bytes(payload), size_bytes=len(payload)),
    )
    assert partial.prepare(request_partial).rejection is tx.RejectionCode.PARTIAL_FILE
    assert cache2.published == {} and _tmp_files(tmp_path / "p2") == []

    # 3. resume: a healthy retry over the same identity publishes exactly once.
    resumed, cache3, store3 = _pipeline(tmp_path / "p3", source=_FakeSource(payload))
    result = resumed.prepare(_request(payload, relative_path="RESULT/result.json"))
    assert result.accepted is True
    assert cache3.published["RESULT/result.json"] == payload
    assert store3.get("job_1", 1, "RESULT/result.json").state == tx.PublicationState.PUBLISHED.value
    assert _tmp_files(tmp_path / "p3") == []


# ====================================================================== #
# S06 — old worker still writing (generation/fence + physical write protection)
# ====================================================================== #


def test_s06_old_worker_generation_fence_and_receipt_write_protection(tmp_path: Path) -> None:
    payload = b"late"
    box: dict[str, _FakeCache] = {}

    def _bump_generation() -> None:
        box["cache"].generation += 1

    source = _FakeSource(payload, on_read=_bump_generation)
    pipeline, cache, store = _pipeline(tmp_path, source=source)
    box["cache"] = cache
    # The writer is superseded while it is still writing -> discarded.
    result = pipeline.prepare(_request(payload))
    assert result.rejection is tx.RejectionCode.LATE_TRANSFER
    assert cache.published == {}, "an old worker's write must be physically discarded"
    assert store.list_all()[0].state == tx.PublicationState.DISCARDED.value
    assert _tmp_files(tmp_path) == []

    # A stale attempt cannot even download: the fence is checked before the read.
    advanced, cache_a, _store_a = _pipeline(tmp_path / "adv", source=_FakeSource(b"new"))
    assert advanced.prepare(_request(b"new", attempt=2)).accepted is True
    stale_source = _FakeSource(b"old")
    stale, _cache_s, _store_s = _pipeline(tmp_path / "adv2", source=stale_source)
    # seed the fence at attempt 2 then request attempt 1
    stale._cache.attempt = 2  # noqa: SLF001 - drive the fence directly
    blocked = stale.prepare(_request(b"old", attempt=1))
    assert blocked.rejection is tx.RejectionCode.STALE_ATTEMPT
    assert stale_source.calls == [], "a fenced stale attempt must not reach the source"

    # Physical receipt protection: a stale receipt is never overwritten.
    env1 = _envelope()
    env2 = _envelope(
        attempt=2, submission_id=expected_submission_id("job-1", 2), release_id="rel-2"
    )
    worker = FrozenWorker(executor=_FakeExecutor(), step_requirements=_Satisfied())
    receipt_path = tmp_path / "receipt.json"
    worker.execute(env1, _plan(env1), receipt_path=receipt_path, work_dir=tmp_path)
    before = receipt_path.read_bytes()
    with pytest.raises(ReceiptConflictError):
        worker.execute(env2, _plan(env2), receipt_path=receipt_path, work_dir=tmp_path)
    assert receipt_path.read_bytes() == before, "a stale receipt must never be overwritten"
    assert read_receipt(receipt_path).attempt == 1  # type: ignore[union-attr]


# ====================================================================== #
# S07 — permission revocation immediately effective
# ====================================================================== #


def test_s07_permission_revocation_is_immediately_effective(tmp_path: Path) -> None:
    # (a) Access policy: revocation flips allow -> deny at once.
    principal: Principal = LocalPrincipal.resolve()
    scope = AccessScope(
        scope_id="scope-1",
        tenant_id=principal.tenant_id,
        action=AccessAction.READ,
        resource_kinds=("job",),
        project_ids=("proj-1",),
    )
    grant = ResourceGrant(
        grant_id="grant-1",
        principal_id=principal.principal_id,
        scope_id="scope-1",
        resource_id="proj-1",
        actions=(AccessAction.READ,),
        granted_at="2026-10-18T00:00:00+00:00",
    )
    policy = LocalSingleAccessPolicy(grants=[grant], scopes=[scope])
    assert principal.deployment_tier == DeploymentTier.LOCAL_SINGLE
    assert (
        policy.authorize(principal, scope, resource_id="proj-1", action=AccessAction.READ).effect
        == "allow"
    )
    policy.revoke("grant-1")
    denied = policy.authorize(principal, scope, resource_id="proj-1", action=AccessAction.READ)
    assert denied.effect == "deny" and denied.reason_code == ReasonCode.REVOKED

    # (b) Transfer: a publish denial after download publishes nothing.
    source = _FakeSource(b"x")
    auth = _FakeAuthorization(deny=("publish",))
    pipeline, cache, store = _pipeline(tmp_path, source=source, authorization=auth)
    result = pipeline.prepare(_request(b"x"))
    assert result.rejection is tx.RejectionCode.PERMISSION_REVOKED
    assert auth.calls == ["download", "publish"]
    assert cache.published == {} and store.list_all() == () and _tmp_files(tmp_path) == []

    # (c) Prepare completion: a bumped authorization version is rejected and the
    # operation is rolled back (still running, not ready).
    service = ops.PrepareOperationService(tmp_path / "ops.db", retry_backoff_seconds=0.0)
    op = service.enqueue(
        ops.PrepareRequest(
            job_id="j1",
            attempt=1,
            resource_id="catalog:Confsearch",
            resource_kind=dc.ResourceKind.CACHE,
            authorization_version=1,
        )
    )
    lease = service.lease_next()
    assert lease is not None
    with pytest.raises(ops.PreparePublishRejectedError):
        service.complete(lease, ops.PrepareCompletion(authorization_version=2))
    assert service.get(op.operation_id).state is ops.PrepareState.RUNNING
    service.close()


# ====================================================================== #
# S08 — budget race (B11) : DEFERRED, never faked
# ====================================================================== #


def test_s08_budget_race_is_deferred_behind_the_b11_price_gate() -> None:
    # The B11 credit ledger is gated on price-policy approval (+ B10).  It is
    # not implemented, so a reservation competition cannot be exercised here and
    # is recorded DEFERRED with the gate reason — never as an unseen pass.
    assert importlib.util.find_spec("acp_services.workspace.ledger") is None, (
        "B11 ledger unexpectedly present; re-open the budget-race scenario with real injections"
    )
    # The adjacent B02-2 *storage* budget ledger exists but is a distinct
    # mechanism; it is not the B11 credit ledger the scenario targets.
    from acp_services.data.budget import StorageBudgetLedger

    assert StorageBudgetLedger is not None
    assert FAULT_MATRIX["S08"][2].startswith("DEFERRED")


# ====================================================================== #
# S09 — post-recovery reconciliation (reconcile BEFORE dispatch)
# ====================================================================== #


def test_s09_reconcile_before_dispatch_after_restart(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    mgr._poll_stop.set()
    try:
        runner = _ReconcileSpy(verdict="unknown")
        mgr.remote_runner = runner  # type: ignore[assignment]
        job_id = "restart-reconcile"
        record = JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name=job_id),
            status=JobStatus.RUNNING,
            work_dir=str(tmp_path / "runs" / job_id),
            result={
                "remote": {
                    "submission_id": submission_id_for(job_id, 1),
                    "submit_state": "unconfirmed",
                    "lease_expires_at": "2999-01-01T00:00:00+00:00",
                }
            },
        )
        mgr.store.create(record)
        # Startup recovery: the indeterminate submission is reconciled BEFORE any
        # restart-finalise; being unreconcilable, the job is deferred, not failed.
        mgr._requeue_active_on_startup()
        stored = mgr.store.get(job_id)
        assert stored is not None and stored.status == JobStatus.RUNNING, (
            "an indeterminate external submission must be reconciled before recovery, "
            "never restart-failed"
        )
        # Dispatch runs after reconcile and must not re-submit the deferred job.
        mgr._dispatch_queued_jobs()
        assert runner.submit_calls == 0, "reconcile-before-dispatch: no duplicate bsub"
        assert runner.reconcile_calls >= 1
    finally:
        mgr.shutdown()


# ====================================================================== #
# P01 — CAS committed, notification pending (outbox backlog replay)
# ====================================================================== #


def test_p01_cas_committed_notification_pending_replays(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    store = JobStore(db)
    store.create(_job_record(tmp_path, "p1"))
    stored = store.transition(
        "p1",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    log = JobEventLog(_event_path(tmp_path, "p1"))
    # Crash after commit, before delivery: the fact is committed, its event waits.
    assert stored.status == JobStatus.RUNNING
    assert len(store.pending_outbox(job_id="p1")) == 1
    assert log.read_all() == []
    revision = stored.revision

    # Restart over the same DB and sweep: the backlog is delivered exactly once.
    reopened = JobStore(db)
    result = _deliver(reopened, tmp_path).deliver_pending()
    assert result.delivered_count == 1 and result.pending == 0
    records = log.read_all()
    assert [r["type"] for r in records] == ["state.changed"]
    assert records[0]["status"] == "running"
    assert reopened.get("p1").revision == revision  # delivery never re-applies state


# ====================================================================== #
# P02 — outbox delivered, not marked (crash before the ack)
# ====================================================================== #


class _UnackedOncePort:
    def __init__(self, store: JobStore) -> None:
        self._inner = JobStoreOutboxPort(store)
        self.acked = 0

    def pending_outbox(self, *, job_id=None, limit=None):
        return self._inner.pending_outbox(job_id=job_id, limit=limit)

    def mark_outbox_delivered(self, event_id, *, seq, delivered_at):
        if self.acked == 0:
            self.acked += 1
            return False  # the ack was lost with the process
        return self._inner.mark_outbox_delivered(event_id, seq=seq, delivered_at=delivered_at)

    def mark_outbox_failed(self, event_id, *, error):
        self._inner.mark_outbox_failed(event_id, error=error)


def test_p02_outbox_delivered_not_marked_replays_idempotently(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "acp_jobs.db")
    store.create(_job_record(tmp_path, "p2"))
    stored = store.transition(
        "p2",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    log = JobEventLog(_event_path(tmp_path, "p2"))

    first = OutboxDeliveryService(
        outbox=_UnackedOncePort(store),
        log_for=lambda job_id: JobEventLogPort(JobEventLog(_event_path(tmp_path, job_id))),
    ).deliver_pending()
    assert first.delivered_count == 1
    assert len(log.read_all()) == 1
    assert len(store.pending_outbox(job_id="p2")) == 1, "an unacked row stays pending"

    second = _deliver(store, tmp_path).deliver_pending()
    assert second.delivered_count == 0 and len(second.duplicates) == 1
    assert second.pending == 0
    assert len(log.read_all()) == 1, "the same fact must never be appended twice"
    assert store.get("p2").revision == stored.revision
    assert store.get("p2").status == JobStatus.RUNNING


# ====================================================================== #
# P03 — published file / DB unregistered ; DB record / file missing
# ====================================================================== #


def test_p03_published_file_db_unregistered_and_db_record_file_missing(tmp_path: Path) -> None:
    cache = _FakeCache(tmp_path / ".remote_cache")
    store = tx.TransferPublicationStore(tmp_path / "publications.db")

    # file published, DB unregistered: an orphan in the cache with no row.
    orphan = cache.guarded_path("job_1", "RESULT/orphan.json")
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")

    # DB record, file missing: a PUBLISHED row whose file was deleted.
    store.record(
        tx.PublicationRecord(
            job_id="job_1",
            attempt=1,
            relative_path="RESULT/gone.json",
            resource_class=tx.ResourceClass.ATTEMPT_ARTIFACT.value,
            state=tx.PublicationState.PUBLISHED.value,
            sha256="0" * 64,
            size_bytes=4,
            resource_generation=0,
            basis_revision=0,
            verification_level=tx.VerificationLevel.VERIFIED.value,
            adopted_from="receipt",
            created_at="",
            updated_at="",
        )
    )

    report = store.reconcile(cache, job_ids=["job_1"])
    assert "job_1:RESULT/orphan.json" in report.unregistered
    assert "job_1:RESULT/gone.json" in report.missing
    # Reconciliation classifies; it never deletes the orphan (no silent data loss).
    assert orphan.is_file()
    assert store.get("job_1", 1, "RESULT/gone.json").state == tx.PublicationState.MISSING.value


# ====================================================================== #
# P04 — schema expand / contract + code rollback
# ====================================================================== #


def _create_legacy_jobs_db(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            workflow TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            work_dir TEXT NOT NULL DEFAULT '',
            spec_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            current_stage TEXT,
            progress REAL,
            error TEXT,
            pid INTEGER,
            exit_code INTEGER,
            result_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO jobs (id, workflow, status, work_dir, spec_json, created_at, updated_at) "
        "VALUES ('legacy-1', 'fake', 'pending', '/tmp/legacy-1', '{}', 't', 't')"
    )
    conn.commit()
    conn.close()


def _columns(db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _db_version(db: Path) -> int:
    from acp_services.store import migrations as mig

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return mig.get_schema_version(conn)
    finally:
        conn.close()


def test_p04_schema_expand_contract_and_code_rollback(tmp_path: Path) -> None:
    from acp_services.store import migrations as mig

    # (a) expand a legacy schema: migrate() adds the missing columns + tables.
    db = tmp_path / "legacy.db"
    _create_legacy_jobs_db(db)
    assert "revision" not in _columns(db, "jobs")
    applied = mig.migrate(db)
    assert applied > 0
    cols = _columns(db, "jobs")
    assert {"revision", "attempt", "project_id", "input_hash", "group_id"} <= cols
    version_after = _db_version(db)
    # legacy data preserved (no destructive rewrite).
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT id, status FROM jobs WHERE id='legacy-1'").fetchone()
    conn.close()
    assert row == ("legacy-1", "pending")

    # (b) idempotent re-apply: a rolled-back code that re-runs migrate() is safe.
    assert mig.migrate(db) == 0
    assert _db_version(db) == version_after

    # (c) code-rollback tolerance: an old writer/reader using only the legacy
    # column set still works against the expanded schema (additive migration).
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO jobs (id, workflow, status, work_dir, spec_json, created_at, updated_at) "
        "VALUES ('rolledback', 'fake', 'pending', '/tmp/rb', '{}', 't', 't')"
    )
    conn.commit()
    got = conn.execute("SELECT id, status FROM jobs WHERE id='rolledback'").fetchone()
    conn.close()
    assert got == ("rolledback", "pending")

    # (d) contraction: a DB whose jobs table lost a column current code needs,
    # while the migration ledger still says "applied" (the rollback trap).  The
    # mismatch is DETECTED as an operational error — never silently masked.
    contracted = tmp_path / "contracted.db"
    _create_legacy_jobs_db(contracted)
    conn = sqlite3.connect(str(contracted))
    conn.executescript(
        "CREATE TABLE _schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);"
    )
    now = "2026-10-19T00:00:00Z"
    conn.executemany(
        "INSERT INTO _schema_migrations (id, applied_at) VALUES (?, ?)",
        [(m["id"], now) for m in mig._MIGRATIONS],
    )
    conn.commit()
    conn.close()
    assert mig.migrate(contracted) == 0, "a ledger-claimed schema is not re-applied"
    assert "revision" not in _columns(contracted, "jobs")
    store = JobStore(contracted)  # init cannot repair: ledger says applied
    with pytest.raises(sqlite3.OperationalError):
        store.transition(
            "legacy-1",
            expected_status=JobStatus.PENDING,
            expected_revision=0,
            status=JobStatus.QUEUED,
        )
