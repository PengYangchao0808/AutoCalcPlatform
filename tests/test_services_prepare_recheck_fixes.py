"""Data regressions for the second architecture recheck (R2-02 / R2-03)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

import pytest

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.services_data_bootstrap import (
    build_local_prepare_identity_provider,
    build_prepare_executor,
)
from acp_services.data import operations as operations_module
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import (
    PrepareOperationService,
    PrepareRequest,
    PrepareState,
)

MANIFEST_PATH = "RESULT/result_manifest.json"
MODE_PATH = "RESULT/frequencies/normal_modes.json"
MODES = b'{"modes": []}'
MANIFEST = json.dumps(
    {"products": [{"kind": "frequency_modes", "path": "frequencies/normal_modes.json"}]}
).encode()


def _record(tmp_path: Path, *, attempt: int = 1) -> JobRecord:
    return JobRecord(
        id="target",
        spec=JobSpec(workflow="frequency", name="target"),
        status=JobStatus.COMPLETED,
        attempt=attempt,
        work_dir=str(tmp_path / "work"),
        result={
            "node": "node1",
            "remote_dir": "/remote/target",
            "execution_kind": "remote",
        },
    )


def _request(record: JobRecord) -> PrepareRequest:
    return PrepareRequest(
        job_id=record.id,
        attempt=record.attempt,
        resource_id=f"catalog:{record.spec.workflow}",
        resource_kind=ResourceKind.CACHE,
        resource_digest=f"{record.id}\x1f{record.attempt}\x1f{record.spec.workflow}",
        payload={
            "workflow": record.spec.workflow,
            "job_id": record.id,
            "attempt": record.attempt,
        },
    )


def _succeed(service: PrepareOperationService, request: PrepareRequest) -> str:
    operation = service.enqueue(request)
    lease = service.lease_next()
    assert lease is not None and lease.operation_id == operation.operation_id
    service.complete(lease)
    return operation.operation_id


class _Fetcher:
    def __init__(self, files: dict[str, bytes], *, transient_modes: bool = False) -> None:
        self.files = files
        self.transient_modes = transient_modes
        self.on_modes: Any = None
        self.calls: list[str] = []

    def read_file(self, record: Any, relative_path: str) -> bytes:
        self.calls.append(relative_path)
        if relative_path == MODE_PATH:
            if self.on_modes is not None:
                self.on_modes()
            if self.transient_modes:
                self.transient_modes = False
                raise ConnectionError("temporary mode download failure")
        try:
            return self.files[relative_path]
        except KeyError as exc:
            raise FileNotFoundError(relative_path) from exc


def _executor(record: JobRecord, cache: RemoteStructureCache) -> Any:
    def lookup(_job_id: str) -> JobRecord:
        return record

    return build_prepare_executor(
        job_lookup=lookup,
        identity_provider=build_local_prepare_identity_provider(lookup),
        cache=cache,
        is_remote=JobManager._is_remote_job,
    )


def _seed_manifest(cache: RemoteStructureCache, record: JobRecord, root: Path) -> None:
    manifest = root / MANIFEST_PATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(MANIFEST)
    cache._write_attempt_sidecar(manifest, record.attempt)
    cache._write_fence(record.id, attempt=record.attempt, generation=0)


def test_evicted_target_requeues_after_more_than_1000_unrelated_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record(tmp_path, attempt=2)
    cache = RemoteStructureCache(tmp_path / "runs")
    with PrepareOperationService(tmp_path / "ops.db") as service:
        monkeypatch.setattr(operations_module, "_utc_now_iso", lambda: "2000-01-01T00:00:00Z")
        unrelated = [
            _succeed(service, replace(_request(record), job_id=f"old-{number}"))
            for number in range(1000)
        ]
        monkeypatch.setattr(operations_module, "_utc_now_iso", lambda: "2099-01-01T00:00:00Z")
        original = _succeed(service, _request(record))
        preserved = [
            _succeed(service, request)
            for request in (
                replace(_request(record), attempt=1),
                replace(_request(record), resource_id="geometry:frequency"),
                replace(_request(record), permission_scope="other-observer"),
            )
        ]
        assert original not in {
            operation.operation_id for operation in service.list(states=[PrepareState.SUCCEEDED])
        }

        manager = JobManager.__new__(JobManager)
        manager._remote_fetcher = object()
        manager._remote_structure_cache = cache
        manager._prepare_operations = service
        manager.store = SimpleNamespace(get=lambda _job_id: record)
        manager._ensure_prepare_worker = lambda: None
        manager._catalog_prefetch_queue = Queue()
        manager._queue_catalog_prefetch(record.id)

        replacement = service.find(
            job_id=record.id, attempt=record.attempt, resource_id="catalog:frequency"
        )
        assert replacement is not None
        assert replacement.operation_id != original
        assert replacement.dedup_epoch == 1
        assert replacement.state is PrepareState.QUEUED
        assert service.get(original).state is PrepareState.EXPIRED
        assert service.pending_count() == 1
        assert all(
            service.get(operation_id).state is PrepareState.SUCCEEDED
            for operation_id in (*unrelated, *preserved)
        )


def test_mode_download_is_retried_after_main_catalog_was_published(tmp_path: Path) -> None:
    record = _record(tmp_path)
    fetcher = _Fetcher({MANIFEST_PATH: MANIFEST, MODE_PATH: MODES}, transient_modes=True)
    cache = RemoteStructureCache(tmp_path / "runs", fetcher_factory=lambda _job_id: fetcher)
    executor = _executor(record, cache)
    with PrepareOperationService(tmp_path / "ops.db", retry_backoff_seconds=0) as service:
        service.enqueue(_request(record))
        first_lease = service.lease_next()
        assert first_lease is not None
        first = executor.execute(first_lease)
        assert not first.prepared and first.retryable
        assert cache.get_cached(record.id, MANIFEST_PATH, attempt=1) is not None
        assert cache.get_cached(record.id, MODE_PATH, attempt=1) is None
        service.fail(first_lease, error_code="transient", retryable=True)

        retry_lease = service.lease_next()
        assert retry_lease is not None
        retried = executor.execute(retry_lease)
        assert retried.prepared and not retried.retryable
        service.complete(retry_lease)
        assert cache.get_cached(record.id, MODE_PATH, attempt=1).read_bytes() == MODES
        assert fetcher.calls.count(MODE_PATH) == 2
        assert fetcher.calls.count(MANIFEST_PATH) == 1


@pytest.mark.parametrize("catalog_in_work_dir", [False, True])
def test_existing_catalog_restores_only_missing_auxiliary_products(
    tmp_path: Path, catalog_in_work_dir: bool
) -> None:
    record = _record(tmp_path)
    fetcher = _Fetcher({MODE_PATH: MODES})
    cache = RemoteStructureCache(tmp_path / "runs", fetcher_factory=lambda _job_id: fetcher)
    root = Path(record.work_dir) if catalog_in_work_dir else cache.job_root(record.id)
    _seed_manifest(cache, record, root)
    with PrepareOperationService(tmp_path / "ops.db") as service:
        service.enqueue(_request(record))
        lease = service.lease_next()
        assert lease is not None
        executor = _executor(record, cache)
        outcome = executor.execute(lease)
        assert outcome.prepared
        assert cache.get_cached(record.id, MODE_PATH, attempt=1).read_bytes() == MODES
        assert fetcher.calls == [MODE_PATH]
        assert executor.execute(lease).prepared
        assert fetcher.calls == [MODE_PATH], "a prepared auxiliary body needs no download"


def test_optional_mode_404_keeps_catalog_prepared(tmp_path: Path) -> None:
    record = _record(tmp_path)
    fetcher = _Fetcher({})
    cache = RemoteStructureCache(tmp_path / "runs", fetcher_factory=lambda _job_id: fetcher)
    _seed_manifest(cache, record, cache.job_root(record.id))
    with PrepareOperationService(tmp_path / "ops.db") as service:
        service.enqueue(_request(record))
        lease = service.lease_next()
        assert lease is not None
        outcome = _executor(record, cache).execute(lease)
        assert outcome.prepared and not outcome.retryable
        assert cache.get_cached(record.id, MODE_PATH, attempt=1) is None
        assert fetcher.calls == [MODE_PATH]


def test_auxiliary_retry_keeps_attempt_guard_at_publish(tmp_path: Path) -> None:
    record = _record(tmp_path)
    fetcher = _Fetcher({MODE_PATH: MODES})
    cache = RemoteStructureCache(tmp_path / "runs", fetcher_factory=lambda _job_id: fetcher)
    _seed_manifest(cache, record, cache.job_root(record.id))
    fetcher.on_modes = lambda: setattr(record, "attempt", 2)
    with PrepareOperationService(tmp_path / "ops.db") as service:
        service.enqueue(_request(record))
        lease = service.lease_next()
        assert lease is not None
        outcome = _executor(record, cache).execute(lease)
        assert not outcome.prepared and not outcome.retryable
        assert cache.get_cached(record.id, MODE_PATH, attempt=1) is None
