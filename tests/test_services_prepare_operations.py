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
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.runner import RemotePollObservation
from acp_services.data import contracts as dc
from acp_services.data import operations as ops
from acp_services.store.migrations import (
    get_prepare_schema_version,
    migrate,
    migrate_prepare_schema,
)

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
    generation: int = 0,
    payload: dict[str, Any] | None = None,
    client_key: str = "",
    dedup_epoch: int = 0,
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
        resource_generation=generation,
        payload=payload,
        client_idempotency_key=client_key,
        dedup_epoch=dedup_epoch,
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


# ===========================================================================
# D05 (task 12) — full content identity, client-key index and prepare migration
# ===========================================================================

_LEGACY_PREPARE_DDL = """
CREATE TABLE IF NOT EXISTS data_operations (
    operation_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL,
    job_attempt INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_digest TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    permission_scope TEXT NOT NULL,
    state TEXT NOT NULL,
    owner_generation INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_expires_at REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    authorization_version INTEGER NOT NULL DEFAULT 0,
    binding_id TEXT NOT NULL DEFAULT '',
    resource_generation INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'absent',
    error_code TEXT,
    error_message TEXT,
    result_json TEXT,
    payload_json TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    available_at REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_data_operations_state
    ON data_operations(state, available_at);
CREATE INDEX IF NOT EXISTS idx_data_operations_key
    ON data_operations(idempotency_key);
"""


def _sqlite(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _prepare_schema(db: Path) -> tuple[tuple[str, ...], dict[str, str | None], set[str]]:
    conn = _sqlite(db)
    try:
        columns = tuple(row["name"] for row in conn.execute("PRAGMA table_info(data_operations)"))
        indexes = {
            str(row["name"]): row["sql"]
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='data_operations'"
            )
        }
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return columns, indexes, tables
    finally:
        conn.close()


def _prepare_version(db: Path) -> int:
    conn = _sqlite(db)
    try:
        return get_prepare_schema_version(conn)
    finally:
        conn.close()


def _create_legacy_prepare_db(db: Path) -> None:
    conn = _sqlite(db)
    try:
        conn.executescript(_LEGACY_PREPARE_DDL)
        conn.execute(
            "INSERT INTO data_operations ("
            "operation_id, idempotency_key, job_id, job_attempt, resource_id, "
            "resource_kind, resource_digest, resource_key, permission_scope, state, "
            "availability, created_at, updated_at, available_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "op_legacy",
                "op_legacy_key",
                "old-job",
                1,
                "catalog:Confsearch",
                "cache",
                "dg-old",
                "old-job\x1f1\x1fcatalog:Confsearch",
                "local-single",
                "succeeded",
                "ready",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                0.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_content_identity_is_canonical_and_covers_every_fact() -> None:
    base = _request(
        payload={"b": 2, "a": 1},
        digest="dg-1",
        generation=1,
        binding_id="sb_1",
        authorization_version=1,
    )
    assert base.content_identity == ops.content_identity_for(
        permission_scope=base.permission_scope,
        job_id=base.job_id,
        attempt=base.attempt,
        resource_id=base.resource_id,
        resource_kind=base.resource_kind,
        resource_digest=base.resource_digest,
        resource_generation=base.resource_generation,
        binding_id=base.binding_id,
        authorization_version=base.authorization_version,
        payload={"a": 1, "b": 2},
    )
    # key order in the payload is not identity-bearing
    assert base.content_identity == replace(base, payload={"a": 1, "b": 2}).content_identity

    variants = [
        replace(base, resource_generation=2),
        replace(base, resource_digest="dg-2"),
        replace(base, binding_id="sb_2"),
        replace(base, authorization_version=2),
        replace(base, payload={"a": 1, "b": 3}),
        replace(base, resource_id="catalog:OrcaGradient"),
        replace(base, job_id="j2"),
        replace(base, attempt=2),
        replace(base, permission_scope="scope-B"),
    ]
    identities = {base.content_identity} | {variant.content_identity for variant in variants}
    keys = {base.idempotency_key} | {variant.idempotency_key for variant in variants}
    assert len(identities) == len(variants) + 1
    assert len(keys) == len(variants) + 1


def test_content_identity_change_creates_a_new_operation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.enqueue(
        _request(generation=1, digest="dg-1", payload={"workflow": "Confsearch"})
    )
    newer = service.enqueue(
        _request(generation=2, digest="dg-2", payload={"workflow": "Confsearch"})
    )

    assert base.operation_id != newer.operation_id
    assert base.content_identity != newer.content_identity
    assert len(service.list()) == 2
    # same logical key, newest preparation returned by the logical lookup
    assert service.find(job_id="j1", attempt=1, resource_id="catalog:Confsearch") is not None
    assert (
        newer.operation_id
        == service.find(job_id="j1", attempt=1, resource_id="catalog:Confsearch").operation_id
    )


def test_enqueue_same_client_key_same_content_merges(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request(client_key="client-1")
    first = service.enqueue(request)
    second = service.enqueue(request)
    assert first.operation_id == second.operation_id
    assert second.client_idempotency_key == "client-1"
    assert second.content_identity == first.content_identity
    assert len(service.list()) == 1


def test_same_client_key_different_params_conflicts_one_winner(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    services = [_service(tmp_path), ops.PrepareOperationService(db, clock=FakeClock())]
    barrier = threading.Barrier(2)
    results: list[ops.PrepareOperationRecord] = []
    errors: list[BaseException] = []

    def _attempt(service: ops.PrepareOperationService, digest: str) -> None:
        barrier.wait(timeout=10)
        try:
            results.append(service.enqueue(_request(client_key="shared", digest=digest)))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=_attempt, args=(services[0], "dg-A")),
        threading.Thread(target=_attempt, args=(services[1], "dg-B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 1, (results, errors)
    assert len(errors) == 1 and isinstance(errors[0], ops.PrepareOperationConflictError), errors
    assert len(services[0].list()) == 1
    stored = services[0].list()[0]
    assert stored.operation_id == results[0].operation_id
    assert stored.client_idempotency_key == "shared"
    for service in services:
        service.close()


def test_same_client_key_same_content_concurrent_merge_is_one_row(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    services = [
        ops.PrepareOperationService(db, clock=FakeClock(), retry_backoff_seconds=0.0),
        ops.PrepareOperationService(db, clock=FakeClock(), retry_backoff_seconds=0.0),
    ]
    barrier = threading.Barrier(2)
    results: list[ops.PrepareOperationRecord] = []

    def _attempt(service: ops.PrepareOperationService) -> None:
        barrier.wait(timeout=10)
        results.append(service.enqueue(_request(client_key="shared", digest="dg-same")))

    threads = [
        threading.Thread(target=_attempt, args=(services[0],)),
        threading.Thread(target=_attempt, args=(services[1],)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(results) == 2
    assert results[0].operation_id == results[1].operation_id
    assert len(services[0].list()) == 1
    for service in services:
        service.close()


def test_expired_same_client_key_same_content_starts_new_epoch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request(client_key="reprepare")
    first = service.enqueue(request)
    expired = service.expire(first.operation_id, reason="cache_evicted")
    assert expired.state is ops.PrepareState.EXPIRED
    assert service.availability(first.operation_id).availability is dc.Availability.ABSENT

    second = service.enqueue(request)
    assert second.operation_id != first.operation_id
    assert second.dedup_epoch == 1
    assert second.client_idempotency_key == "reprepare"
    assert second.content_identity == first.content_identity
    assert service.get(first.operation_id).state is ops.PrepareState.EXPIRED
    assert service.pending_count() == 1

    with pytest.raises(ops.PrepareOperationConflictError):
        service.enqueue(_request(client_key="reprepare", digest="dg-other"))


def test_expired_same_client_key_different_content_conflicts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.enqueue(_request(client_key="reprepare", digest="dg-A"))
    service.expire(first.operation_id, reason="cache_evicted")

    with pytest.raises(ops.PrepareOperationConflictError):
        service.enqueue(_request(client_key="reprepare", digest="dg-B"))
    assert service.get(first.operation_id).state is ops.PrepareState.EXPIRED
    assert len(service.list()) == 1


def test_legacy_empty_client_keys_do_not_collide(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.enqueue(_request(resource_id="catalog:Confsearch"))
    second = service.enqueue(_request(resource_id="catalog:OrcaGradient"))
    assert first.operation_id != second.operation_id
    assert first.client_idempotency_key == ""
    assert second.client_idempotency_key == ""
    assert len(service.list()) == 2


def test_prepare_migration_new_db_standalone(tmp_path: Path) -> None:
    db = tmp_path / "prepare.db"
    service = ops.PrepareOperationService(db, clock=FakeClock())
    columns, indexes, tables = _prepare_schema(db)
    assert columns[-3:] == ("content_identity", "client_idempotency_key", "dedup_epoch")
    assert "idx_data_operations_client_key" in indexes
    assert "WHERE client_idempotency_key <> ''" in str(indexes["idx_data_operations_client_key"])
    assert _prepare_version(db) == 1
    assert "data_operations" in tables
    assert "jobs" not in tables and "tasks" not in tables, tables
    service.close()


def test_prepare_migration_old_prepare_only_db_is_conservative(tmp_path: Path) -> None:
    db = tmp_path / "prepare.db"
    _create_legacy_prepare_db(db)
    assert "content_identity" not in _prepare_schema(db)[0]

    reference_db = tmp_path / "reference.db"
    ops.PrepareOperationService(reference_db, clock=FakeClock()).close()
    reference_columns, reference_indexes, _ = _prepare_schema(reference_db)

    service = ops.PrepareOperationService(db, clock=FakeClock())
    try:
        columns, indexes, tables = _prepare_schema(db)
        # one canonical shape regardless of create-then-migrate vs migrate-old
        assert columns == reference_columns
        assert set(indexes) == set(reference_indexes)
        assert columns[-3:] == ("content_identity", "client_idempotency_key", "dedup_epoch")
        assert "idx_data_operations_client_key" in indexes
        assert _prepare_version(db) == 1
        assert "jobs" not in tables and "tasks" not in tables

        legacy = service.get("op_legacy")
        assert legacy is not None
        assert legacy.content_identity == ""
        assert legacy.client_idempotency_key == ""
        assert legacy.dedup_epoch == 0
        assert legacy.state is ops.PrepareState.SUCCEEDED
        assert service.availability("op_legacy").availability is dc.Availability.UNAVAILABLE
        assert service.availability("op_legacy").availability is not dc.Availability.READY
        assert legacy.to_contract().availability is dc.Availability.UNAVAILABLE
        assert legacy.to_contract().freshness is dc.Freshness.STALE

        # old operation-id lookup still works; the logical lookup still finds it
        found = service.find(job_id="old-job", attempt=1, resource_id="catalog:Confsearch")
        assert found is not None
        # a new enqueue with the same logical key starts a NEW content-addressed
        # operation (never reuses the legacy success as the current one)
        fresh = service.enqueue(_request(job_id="old-job", digest="dg-old"))
        assert fresh.operation_id != "op_legacy"
        assert fresh.content_identity != ""
        assert len(service.list()) == 2
    finally:
        service.close()


def test_prepare_migration_mixed_jobs_db_migrates_once(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    assert migrate(db) > 0
    conn = _sqlite(db)
    try:
        jobs_version = conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
    finally:
        conn.close()
    assert _prepare_version(db) == 0

    first = ops.PrepareOperationService(db, clock=FakeClock())
    second = ops.PrepareOperationService(db, clock=FakeClock())
    try:
        conn = _sqlite(db)
        try:
            prepare_rows = conn.execute(
                "SELECT COUNT(*) FROM _prepare_schema_migrations"
            ).fetchone()[0]
            jobs_rows = conn.execute("SELECT COUNT(*) FROM _schema_migrations").fetchone()[0]
            assert prepare_rows == 1
            assert jobs_rows == jobs_version
        finally:
            conn.close()
        assert _prepare_version(db) == 1
        op = first.enqueue(_request())
        assert second.find(job_id="j1", attempt=1, resource_id="catalog:Confsearch") is not None
        assert op.content_identity != ""
    finally:
        first.close()
        second.close()


def test_prepare_migration_before_create_and_missing_table_not_marked(tmp_path: Path) -> None:
    migrated_first = tmp_path / "migrated-first.db"
    assert migrate_prepare_schema(migrated_first) is True
    canonical = _prepare_schema(migrated_first)
    assert canonical[0][-3:] == ("content_identity", "client_idempotency_key", "dedup_epoch")
    service = ops.PrepareOperationService(migrated_first, clock=FakeClock())
    service.close()
    assert _prepare_schema(migrated_first) == canonical
    assert _prepare_version(migrated_first) == 1

    missing = tmp_path / "missing.db"
    assert migrate_prepare_schema(missing, create_table=False) is False
    assert "data_operations" not in _prepare_schema_allow_missing(missing)
    assert _prepare_version(missing) == 0


def _prepare_schema_allow_missing(db: Path) -> set[str]:
    conn = _sqlite(db)
    try:
        return {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_two_concurrent_initializers_converge(tmp_path: Path) -> None:
    db = tmp_path / "prepare.db"
    code = (
        "import sys\n"
        "from acp_services.data.operations import PrepareOperationService\n"
        "PrepareOperationService(sys.argv[1]).close()\n"
    )
    processes = [subprocess.Popen([sys.executable, "-c", code, str(db)]) for _ in range(2)]
    try:
        for process in processes:
            assert process.wait(timeout=60) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    columns, indexes, tables = _prepare_schema(db)
    assert columns[-3:] == ("content_identity", "client_idempotency_key", "dedup_epoch")
    assert "idx_data_operations_client_key" in indexes
    assert "jobs" not in tables and "tasks" not in tables
    assert _prepare_version(db) == 1
    conn = _sqlite(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM _prepare_schema_migrations").fetchone()[0] == 1
    finally:
        conn.close()


# ===========================================================================
# D05 (task 13) — frozen epoch/reopen, expire(SUCCEEDED), cross-process safety
# ===========================================================================


def test_d05_counterexample_generation_two_is_not_old_success(tmp_path: Path) -> None:
    service = _service(tmp_path)
    original = _request(generation=1, digest="sha256:old")
    first = service.enqueue(original)
    leased = service.lease_next()
    assert leased is not None
    assert service.complete(leased).state is ops.PrepareState.SUCCEEDED

    newer = _request(generation=2, digest="sha256:new")
    second = service.enqueue(newer)
    assert second.operation_id != first.operation_id
    assert second.resource_generation == 2
    assert second.state is not ops.PrepareState.SUCCEEDED
    assert second.state is ops.PrepareState.QUEUED
    found = service.find(job_id="j1", attempt=1, resource_id="catalog:Confsearch")
    assert found is not None and found.operation_id == second.operation_id

    claim = service.lease_next()
    assert claim is not None and claim.operation_id == second.operation_id
    assert service.complete(claim).state is ops.PrepareState.SUCCEEDED

    # same identity (generation 2) executes once — the second enqueue merges
    again = service.enqueue(newer)
    assert again.operation_id == second.operation_id
    assert service.lease_next() is None
    assert len(service.list()) == 2


def test_expire_succeeded_row_is_not_ready_and_keeps_result(tmp_path: Path) -> None:
    service = _service(tmp_path)
    op = service.enqueue(_request(client_key="client-1"))
    lease = service.lease_next()
    assert lease is not None
    done = service.complete(
        lease,
        ops.PrepareCompletion(result={"manifest": MANIFEST_PATH}, size_bytes=42, sha256="abc123"),
    )
    assert done.state is ops.PrepareState.SUCCEEDED
    assert done.result == {"manifest": MANIFEST_PATH}
    assert service.availability(op.operation_id).availability is dc.Availability.READY

    expired = service.expire(op.operation_id, reason="cache_evicted")
    assert expired.state is ops.PrepareState.EXPIRED
    assert expired.error_code == "expired"
    assert expired.error_message == "cache_evicted"
    assert expired.result == {"manifest": MANIFEST_PATH}
    assert expired.size_bytes == 42
    assert expired.sha256 == "abc123"

    assert service.availability(op.operation_id).availability is dc.Availability.ABSENT
    assert service.availability(op.operation_id).availability is not dc.Availability.READY
    contract = service.get(op.operation_id).to_contract()
    assert contract.state is dc.ResourceState.FAILED
    assert contract.availability is dc.Availability.ABSENT
    assert contract.freshness is dc.Freshness.UNKNOWN

    assert service.expire(op.operation_id, reason="again").state is ops.PrepareState.EXPIRED
    with pytest.raises(ops.StaleLeaseError):
        service.complete(lease)


def test_expire_succeeded_same_client_key_reopens_next_epoch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request(client_key="reprepare")
    first = service.enqueue(request)
    lease = service.lease_next()
    assert lease is not None
    service.complete(lease, ops.PrepareCompletion(result={"manifest": MANIFEST_PATH}))
    service.expire(first.operation_id, reason="cache_evicted")

    second = service.enqueue(request)
    assert second.operation_id != first.operation_id
    assert second.dedup_epoch == first.dedup_epoch + 1 == 1
    assert second.state is ops.PrepareState.QUEUED
    assert second.content_identity == first.content_identity
    assert second.client_idempotency_key == "reprepare"

    old = service.get(first.operation_id)
    assert old is not None
    assert old.state is ops.PrepareState.EXPIRED
    assert old.result == {"manifest": MANIFEST_PATH}
    assert len(service.list()) == 2

    claim = service.lease_next()
    assert claim is not None and claim.operation_id == second.operation_id
    with pytest.raises(ops.PrepareOperationConflictError):
        service.enqueue(_request(client_key="reprepare", digest="dg-other"))


def test_expire_running_invalidates_old_lease(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request()
    op = service.enqueue(request)
    stale = service.lease_next()
    assert stale is not None
    service.expire(op.operation_id, reason="superseded")

    with pytest.raises(ops.StaleLeaseError):
        service.complete(stale)
    with pytest.raises(ops.StaleLeaseError):
        service.fail(stale, error_code="x")

    row = service.get(op.operation_id)
    assert row is not None
    assert row.state is ops.PrepareState.EXPIRED
    assert row.error_message == "superseded"
    assert service.availability(op.operation_id).availability is not dc.Availability.READY
    assert service.lease_next() is None

    reopened = service.enqueue(request)
    assert reopened.operation_id != op.operation_id
    assert reopened.dedup_epoch == 1
    claim = service.lease_next()
    assert claim is not None and claim.operation_id == reopened.operation_id


def test_failed_row_resets_retry_budget_on_reenqueue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = _request(max_attempts=1)
    op = service.enqueue(request)
    lease = service.lease_next()
    assert lease is not None
    assert (
        service.fail(lease, error_code="sftp_failed", error_message="down").state
        is ops.PrepareState.FAILED
    )

    reset = service.enqueue(request)
    assert reset.operation_id == op.operation_id
    assert reset.state is ops.PrepareState.QUEUED
    assert reset.attempt_count == 0
    assert reset.error_code is None and reset.error_message is None
    assert reset.owner_generation > op.owner_generation

    retry = service.lease_next()
    assert retry is not None and retry.operation_id == op.operation_id
    assert service.complete(retry).state is ops.PrepareState.SUCCEEDED


def test_same_identity_executes_once_across_two_connections(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    first_service = ops.PrepareOperationService(db, clock=FakeClock(), retry_backoff_seconds=0.0)
    second_service = ops.PrepareOperationService(db, clock=FakeClock(), retry_backoff_seconds=0.0)
    try:
        request = _request(client_key="shared")
        first = first_service.enqueue(request)
        second = second_service.enqueue(request)
        assert first.operation_id == second.operation_id
        assert len(first_service.list()) == 1

        lease = first_service.lease_next()
        assert lease is not None
        assert second_service.lease_next() is None
        first_service.complete(lease)
        assert second_service.get(first.operation_id).state is ops.PrepareState.SUCCEEDED
    finally:
        first_service.close()
        second_service.close()


def test_client_key_conflict_is_not_the_idempotency_hash(tmp_path: Path) -> None:
    first_request = _request(client_key="same", digest="dg-A")
    second_request = _request(client_key="same", digest="dg-B")
    # Different content means different idempotency hashes: only the partial
    # unique index on (scope, client_key, epoch) can reject this.
    assert first_request.idempotency_key != second_request.idempotency_key

    service = _service(tmp_path)
    service.enqueue(first_request)
    with pytest.raises(ops.PrepareOperationConflictError):
        service.enqueue(second_request)
    assert len(service.list()) == 1
    assert service.list()[0].content_identity == first_request.content_identity


_PROCESS_ENQUEUE = """
import sys
from acp_services.data import contracts as dc
from acp_services.data import operations as ops

service = ops.PrepareOperationService(sys.argv[1], retry_backoff_seconds=0.0)
try:
    request = ops.PrepareRequest(
        job_id="j1",
        attempt=1,
        resource_id="catalog:Confsearch",
        resource_kind=dc.ResourceKind.CACHE,
        resource_digest=sys.argv[2],
        client_idempotency_key="shared",
    )
    print(service.enqueue(request).operation_id)
except ops.PrepareOperationConflictError:
    print("CONFLICT")
    raise SystemExit(3)
finally:
    service.close()
"""


def _run_enqueue_processes(db: Path, digests: tuple[str, ...]) -> list[tuple[int, str, str]]:
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _PROCESS_ENQUEUE, str(db), digest],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for digest in digests
    ]
    outcomes: list[tuple[int, str, str]] = []
    try:
        for process in processes:
            out, err = process.communicate(timeout=60)
            outcomes.append((int(process.returncode), out.strip(), err.strip()))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    return outcomes


def test_cross_process_same_client_key_different_content_one_winner(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    outcomes = _run_enqueue_processes(db, ("dg-A", "dg-B"))
    codes = sorted(code for code, _out, _err in outcomes)
    assert codes == [0, 3], outcomes
    winners = [out for code, out, _err in outcomes if code == 0]
    conflicts = [out for code, out, _err in outcomes if code == 3]
    assert len(winners) == 1 and winners[0].startswith("op_")
    assert conflicts == ["CONFLICT"]

    service = ops.PrepareOperationService(db, clock=FakeClock())
    try:
        rows = service.list()
        assert len(rows) == 1
        assert rows[0].operation_id == winners[0]
        assert rows[0].client_idempotency_key == "shared"
    finally:
        service.close()


def test_cross_process_same_identity_produces_one_row(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    outcomes = _run_enqueue_processes(db, ("dg-same", "dg-same"))
    assert [code for code, _out, _err in outcomes] == [0, 0], outcomes
    operation_ids = {out for _code, out, _err in outcomes}
    assert len(operation_ids) == 1
    operation_id = operation_ids.pop()
    assert operation_id.startswith("op_")

    service = ops.PrepareOperationService(db)
    try:
        assert [row.operation_id for row in service.list()] == [operation_id]
        lease = service.lease_next()
        assert lease is not None and lease.operation_id == operation_id
        assert service.lease_next() is None
    finally:
        service.close()
