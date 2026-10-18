"""B02-3 (todo 13) — persistent prepare operations.

Exercises the Services state machine (``acp_services.data.operations``) and its
scheduler integration (``acp.scheduler.manager``):

1. duplicate enqueue dedup — one row / one execution (stable scoped key).
2. cancel isolation — cancelling one observer's operation never kills another
   legitimate consumer of the same resource (permission scope is in the key).
3. visible failure reason + retry budget, then terminal ``failed``.
4. lease expiry alone never releases or marks success; takeover makes it
   retryable and the new holder publishes instead of the stale one.
5. completion CAS — a stale lease holder cannot publish; a revoked
   authorization / changed binding is rejected.
6. restart recovery — a fresh service on the same DB replays undecided
   operations and the worker converges to ``succeeded``.
7. manager restart — a durable operation left ``running`` by a prior
   JobManager is recovered by the next one.

Run with: pytest tests/test_services_prepare_operations.py -q
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.runner import RemotePollObservation
from acp_services.data import contracts as dc
from acp_services.data import operations as ops

MANIFEST = {
    "schema_version": "confsearch_v1",
    "workflow": "Confsearch",
    "conformers": [
        {
            "conf_id": "0001",
            "geometry": "conformers/0001.xyz",
            "energy_hartree": -100.0,
            "relative_energy_kcal": 0.0,
            "boltzmann_weight": 1.0,
            "rank": 1,
        }
    ],
}
MANIFEST_PATH = "RESULT/confsearch/confsearch_manifest.json"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeExecutor:
    """Executor double: reports a prepared/unprepared outcome deterministically."""

    def __init__(
        self,
        *,
        prepared: bool = True,
        detail: str = "fetched",
        retryable: bool = False,
        fail_calls: int = 0,
    ) -> None:
        self.prepared = prepared
        self.detail = detail
        self.retryable = retryable
        self.fail_calls = fail_calls
        self.calls: list[str] = []

    def execute(self, lease: ops.PrepareLease) -> ops.PrepareExecutionResult:
        self.calls.append(lease.operation_id)
        if self.fail_calls > 0:
            self.fail_calls -= 1
            return ops.PrepareExecutionResult(prepared=False, detail="boom", retryable=True)
        if self.prepared:
            return ops.PrepareExecutionResult(
                prepared=True, availability=dc.Availability.READY, detail=self.detail
            )
        return ops.PrepareExecutionResult(
            prepared=False,
            availability=dc.Availability.PREPARING,
            detail=self.detail,
            retryable=self.retryable,
        )


def _request(
    *,
    job_id: str = "j1",
    attempt: int = 1,
    resource_id: str = "catalog:Confsearch",
    scope: str = "local-single",
    digest: str = "dg-1",
    max_attempts: int = 3,
    authorization_version: int = 0,
    binding_id: str = "",
) -> ops.PrepareRequest:
    return ops.PrepareRequest(
        job_id=job_id,
        attempt=attempt,
        resource_id=resource_id,
        resource_kind=dc.ResourceKind.CACHE,
        resource_digest=digest,
        permission_scope=scope,
        max_attempts=max_attempts,
        authorization_version=authorization_version,
        binding_id=binding_id,
    )


def _service(tmp_path: Path, clock: FakeClock | None = None) -> ops.PrepareOperationService:
    return ops.PrepareOperationService(
        tmp_path / "acp_jobs.db",
        clock=clock or FakeClock(),
        retry_backoff_seconds=0.0,
        default_lease_seconds=10.0,
    )


def _wait_state(
    service: ops.PrepareOperationService,
    operation_id: str,
    state: ops.PrepareState,
    timeout: float = 5.0,
) -> ops.PrepareOperationRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.get(operation_id)
        if record is not None and record.state is state:
            return record
        time.sleep(0.01)
    record = service.get(operation_id)
    assert record is not None
    return record


# ---------------------------------------------------------------------------
# 1. duplicate enqueue dedup
# ---------------------------------------------------------------------------


def test_duplicate_enqueue_dedups_to_one_operation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.enqueue(_request())
    second = service.enqueue(_request())

    assert first.operation_id == second.operation_id
    assert len(service.list()) == 1
    assert service.pending_count() == 1

    lease = service.lease_next()
    assert lease is not None
    assert lease.operation_id == first.operation_id
    assert service.lease_next() is None
    service.complete(lease)
    assert len(service.list()) == 1


def test_duplicate_enqueue_only_executes_once(tmp_path: Path) -> None:
    service = _service(tmp_path)
    executor = FakeExecutor()
    service.enqueue(_request())
    service.enqueue(_request())
    worker = ops.PrepareOperationWorker(service, executor, poll_interval=0.01)
    worker.start()
    try:
        op = service.list()[0]
        _wait_state(service, op.operation_id, ops.PrepareState.SUCCEEDED)
    finally:
        worker.stop()
    assert executor.calls == [op.operation_id]


# ---------------------------------------------------------------------------
# 2. cancel isolation (scope is part of the key)
# ---------------------------------------------------------------------------


def test_cancel_one_observer_keeps_other_consumers(tmp_path: Path) -> None:
    service = _service(tmp_path)
    observer_a = service.enqueue(_request(scope="scope-A"))
    observer_b = service.enqueue(_request(scope="scope-B"))
    assert observer_a.operation_id != observer_b.operation_id
    assert len(service.list()) == 2

    service.cancel(observer_a.operation_id)
    assert service.get(observer_a.operation_id).state is ops.PrepareState.CANCELED
    assert service.get(observer_b.operation_id).state is ops.PrepareState.QUEUED

    lease = service.lease_next()
    assert lease is not None
    assert lease.operation_id == observer_b.operation_id
    service.complete(lease)
    assert service.get(observer_b.operation_id).state is ops.PrepareState.SUCCEEDED


def test_cancel_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    op = service.enqueue(_request())
    service.cancel(op.operation_id)
    again = service.cancel(op.operation_id)
    assert again.state is ops.PrepareState.CANCELED


# ---------------------------------------------------------------------------
# 3. failure reason + retry
# ---------------------------------------------------------------------------


def test_failure_reason_visible_and_retry_budget_exhausts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.enqueue(_request(max_attempts=2))

    lease = service.lease_next()
    assert lease is not None
    after_first = service.fail(lease, error_code="sftp_failed", error_message="transient")
    assert after_first.state is ops.PrepareState.QUEUED
    assert after_first.error_code == "sftp_failed"
    assert after_first.error_message == "transient"

    lease2 = service.lease_next()
    assert lease2 is not None
    after_second = service.fail(lease2, error_code="sftp_failed", error_message="still down")
    assert after_second.state is ops.PrepareState.FAILED
    assert after_second.error_code == "sftp_failed"
    assert after_second.error_message == "still down"
    assert service.pending_count() == 0


def test_non_retryable_failure_is_terminal(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.enqueue(_request())
    lease = service.lease_next()
    assert lease is not None
    record = service.fail(lease, error_code="nope", error_message="permanent", retryable=False)
    assert record.state is ops.PrepareState.FAILED


# ---------------------------------------------------------------------------
# 4. lease expiry / takeover
# ---------------------------------------------------------------------------


def test_lease_expiry_alone_neither_releases_nor_completes(tmp_path: Path) -> None:
    clock = FakeClock()
    service = _service(tmp_path, clock=clock)
    op = service.enqueue(_request())
    lease = service.lease_next()
    assert lease is not None

    clock.advance(11.0)
    current = service.get(op.operation_id)
    assert current.state is ops.PrepareState.RUNNING
    assert current.state is not ops.PrepareState.SUCCEEDED
    assert service.availability(op.operation_id).availability is dc.Availability.PREPARING


def test_expired_lease_is_retryable_and_old_holder_cannot_publish(tmp_path: Path) -> None:
    clock = FakeClock()
    service = _service(tmp_path, clock=clock)
    op = service.enqueue(_request())
    stale = service.lease_next()
    assert stale is not None

    clock.advance(11.0)
    report = service.recover(takeover_running=False)
    assert report.requeued == 1
    assert report.expired == 1
    assert service.get(op.operation_id).state is ops.PrepareState.QUEUED

    fresh = service.lease_next()
    assert fresh is not None
    assert fresh.owner_generation > stale.owner_generation

    with pytest.raises(ops.StaleLeaseError):
        service.complete(stale)

    done = service.complete(fresh)
    assert done.state is ops.PrepareState.SUCCEEDED


def test_stale_lease_holder_cannot_publish_after_takeover(tmp_path: Path) -> None:
    service = _service(tmp_path)
    op = service.enqueue(_request())
    first = service.lease_next()
    assert first is not None
    service.recover()  # startup takeover bumps the generation
    second = service.lease_next()
    assert second is not None and second.operation_id == op.operation_id

    with pytest.raises(ops.StaleLeaseError):
        service.complete(first)
    with pytest.raises(ops.StaleLeaseError):
        service.fail(first, error_code="x")

    assert service.complete(second).state is ops.PrepareState.SUCCEEDED


# ---------------------------------------------------------------------------
# 5. completion CAS (authorization / binding)
# ---------------------------------------------------------------------------


def test_completion_rejects_revoked_authorization(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.enqueue(_request(authorization_version=1))
    lease = service.lease_next()
    assert lease is not None

    with pytest.raises(ops.PreparePublishRejectedError):
        service.complete(lease, ops.PrepareCompletion(authorization_version=2))
    # The operation is untouched (still running) — the rejected write rolled back.
    assert service.get(lease.operation_id).state is ops.PrepareState.RUNNING


def test_completion_rejects_changed_binding(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.enqueue(_request(binding_id="sb_original"))
    lease = service.lease_next()
    assert lease is not None
    with pytest.raises(ops.PreparePublishRejectedError):
        service.complete(lease, ops.PrepareCompletion(binding_id="sb_rebound"))


# ---------------------------------------------------------------------------
# 6. restart recovery (same DB)
# ---------------------------------------------------------------------------


def test_restart_recovery_replays_running_operation(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    clock = FakeClock()
    first = ops.PrepareOperationService(
        db, clock=clock, retry_backoff_seconds=0.0, default_lease_seconds=1.0
    )
    op = first.enqueue(_request())
    lease = first.lease_next()
    assert lease is not None
    assert first.get(op.operation_id).state is ops.PrepareState.RUNNING
    first.close()  # crash: the worker is gone, the row survives

    second = ops.PrepareOperationService(
        db, clock=clock, retry_backoff_seconds=0.0, default_lease_seconds=10.0
    )
    try:
        report = second.recover()
        assert report.requeued == 1
        assert second.get(op.operation_id).state is ops.PrepareState.QUEUED

        executor = FakeExecutor()
        worker = ops.PrepareOperationWorker(second, executor, poll_interval=0.01)
        worker.start()
        try:
            done = _wait_state(second, op.operation_id, ops.PrepareState.SUCCEEDED)
        finally:
            worker.stop()
        assert done.state is ops.PrepareState.SUCCEEDED
        assert second.availability(op.operation_id).availability is dc.Availability.READY
    finally:
        second.close()


# ---------------------------------------------------------------------------
# 7. availability commit
# ---------------------------------------------------------------------------


def test_availability_committed_on_success_and_failure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    good = service.enqueue(_request(job_id="good"))
    bad = service.enqueue(_request(job_id="bad"))

    for _ in range(2):
        lease = service.lease_next()
        assert lease is not None
        if lease.operation_id == good.operation_id:
            service.complete(lease, ops.PrepareCompletion(availability=dc.Availability.READY))
        else:
            assert lease.operation_id == bad.operation_id
            service.fail(lease, error_code="x", error_message="y", retryable=False)

    assert service.availability(good.operation_id).availability is dc.Availability.READY
    assert service.availability(good.operation_id).freshness is dc.Freshness.CURRENT
    assert service.availability(bad.operation_id).availability is dc.Availability.UNAVAILABLE


# ---------------------------------------------------------------------------
# manager integration
# ---------------------------------------------------------------------------


class FakeFetcher:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def read_file(self, record: Any, filename: str) -> bytes:
        self.calls.append((str(record.id), filename))
        if self.fail:
            raise RuntimeError("transient SFTP failure")
        if filename == MANIFEST_PATH:
            return json.dumps(MANIFEST).encode("utf-8")
        raise FileNotFoundError(filename)


class TerminalRemoteRunner:
    def poll_remote(self, record: Any, event_log: Any, cancel_event: Any) -> RemotePollObservation:
        return RemotePollObservation(terminal=True, exit_code=0)

    def apply_terminal_side_effects(
        self, record: Any, event_log: Any, stage_events: Any = ()
    ) -> None:
        pass


def _seed_remote_job(
    mgr: JobManager,
    tmp_path: Path,
    job_id: str,
    *,
    status: JobStatus = JobStatus.RUNNING,
) -> Path:
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="Confsearch", name=job_id),
        status=status,
        work_dir=str(work_dir),
        remote_job_id="42",
        result={"node": "node1", "remote_dir": f"/remote/{job_id}"},
    )
    mgr.store.create(record)
    return work_dir


def test_manager_terminal_transition_persists_one_operation(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    mgr._poll_stop.set()
    try:
        mgr._remote_fetcher = FakeFetcher()  # type: ignore[assignment]
        mgr.remote_runner = TerminalRemoteRunner()  # type: ignore[assignment]
        _seed_remote_job(mgr, tmp_path, "rj")

        mgr._poll_job("rj")
        assert mgr.store.get("rj").status == JobStatus.COMPLETED

        operation = mgr._prepare_operations.find(
            job_id="rj", resource_id="catalog:Confsearch", attempt=1
        )
        assert operation is not None
        # Two enqueues of the same terminal job never create a second row.
        mgr._queue_catalog_prefetch("rj")
        assert len(mgr._prepare_operations.list()) == 1

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            record = mgr._prepare_operations.get(operation.operation_id)
            if record is not None and record.state is ops.PrepareState.SUCCEEDED:
                break
            time.sleep(0.01)
        final = mgr._prepare_operations.get(operation.operation_id)
        assert final is not None and final.state is ops.PrepareState.SUCCEEDED
    finally:
        mgr.shutdown()


def test_manager_restart_recovers_undecided_operation(tmp_path: Path) -> None:
    mgr1 = JobManager(run_root=tmp_path, max_running=1)
    mgr1._poll_stop.set()
    operation_id: str
    try:
        mgr1._remote_fetcher = FakeFetcher()  # type: ignore[assignment]
        _seed_remote_job(mgr1, tmp_path, "rj", status=JobStatus.COMPLETED)
        record = mgr1.store.get("rj")
        assert record is not None
        operation = mgr1._prepare_operations.enqueue(mgr1._prepare_request(record))
        operation_id = operation.operation_id
        # Simulate a worker that claimed the op and then died: leave it running.
        assert mgr1._prepare_operations.lease_next() is not None
        assert mgr1._prepare_operations.get(operation_id).state is ops.PrepareState.RUNNING
    finally:
        mgr1.shutdown()

    mgr2 = JobManager(run_root=tmp_path, max_running=1)
    mgr2._poll_stop.set()
    try:
        mgr2._remote_fetcher = FakeFetcher()  # type: ignore[assignment]
        mgr2._queue_startup_catalog_prefetch()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            got = mgr2._prepare_operations.get(operation_id)
            if got is not None and got.state is ops.PrepareState.SUCCEEDED:
                break
            time.sleep(0.01)
        final = mgr2._prepare_operations.get(operation_id)
        assert final is not None and final.state is ops.PrepareState.SUCCEEDED
    finally:
        mgr2.shutdown()
