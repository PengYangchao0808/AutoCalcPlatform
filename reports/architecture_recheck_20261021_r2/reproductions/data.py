"""Independent Data re-review of the dirty worktree, no network or real QC."""
from __future__ import annotations

import io
import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "src")

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.fetcher import RemoteResultFetcher
from acp.services_data_bootstrap import (
    _ReadCacheIndexAdapter,
    _TransferCacheAdapter,
    build_local_prepare_identity_provider,
    build_prepare_executor,
    build_read_only_reader,
)
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import PrepareOperationService, PrepareRequest
from acp_services.data.read_root import AvailabilityRead, ReadRoot


def record_at(root, workflow="singlepoint", job_id="j1"):
    return JobRecord(
        id=job_id, spec=JobSpec(workflow=workflow, name="x"),
        status=JobStatus.COMPLETED, attempt=1, work_dir=str(root / "work"),
        result={"node": "n", "remote_dir": f"/remote/{job_id}", "execution_kind": "remote"},
    )


def request_for(record):
    return PrepareRequest(
        job_id=record.id, attempt=record.attempt,
        resource_id=f"catalog:{record.spec.workflow}", resource_kind=ResourceKind.CACHE,
        resource_digest=f"{record.id}\x1f{record.attempt}\x1f{record.spec.workflow}",
        payload={"workflow": record.spec.workflow, "job_id": record.id, "attempt": record.attempt},
    )


def executor_for(cache, record):
    lookup = lambda _: record
    return build_prepare_executor(
        job_lookup=lookup, cache=cache,
        identity_provider=build_local_prepare_identity_provider(lookup),
        is_remote=lambda _: True,
    )


def real_fetcher_contract():
    class Sftp:
        def __init__(self): self.paths = []

        def stat(self, path):
            if path != "/remote/j1/RESULT/result_manifest.json": raise FileNotFoundError(path)
            return SimpleNamespace(st_size=2)

        def file(self, path, mode):
            self.paths.append(path)
            return io.BytesIO(b"{}")

    sftp = Sftp()
    class Ssh:
        @contextmanager
        def sftp_session(self, node): yield sftp

    with TemporaryDirectory() as td:
        root = Path(td)
        record = record_at(root)
        fetcher = RemoteResultFetcher(
            Ssh(), SimpleNamespace(list_remote_dir=lambda *_: []),
            SimpleNamespace(get_node=lambda _: SimpleNamespace(name="n")),
        )
        cache = RemoteStructureCache(root / "runs", fetcher_factory=lambda _: fetcher)
        with PrepareOperationService(root / "ops.db") as service:
            service.enqueue(request_for(record))
            outcome = executor_for(cache, record).execute(service.lease_next())
            return {"case": "R06_real_fetcher", "closed": outcome.prepared,
                    "detail": outcome.detail, "real_fetcher_remote_paths": sftp.paths}


def reader_publisher_snapshot():
    with TemporaryDirectory() as td:
        root = Path(td)
        cache = RemoteStructureCache(root / "runs")
        rel = "RESULT/result_manifest.json"
        target = cache.cache_path("j1", rel)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"ATTEMPT_1")
        cache._write_attempt_sidecar(target, 1)
        cache._write_fence("j1", attempt=1, generation=0)
        replacement = root / "replacement.tmp"
        replacement.write_bytes(b"ATTEMPT_2")
        reader = _ReadCacheIndexAdapter(cache)
        approved = ReadRoot(job_id="j1", path=str(cache.job_root("j1")), is_remote=True,
                            found=True, attempt=1)
        window = threading.Event()
        allow_open = threading.Event()
        writer_started = threading.Event()
        writer_finished = threading.Event()
        result = {}
        original = _ReadCacheIndexAdapter._snapshot_facts

        def pause_before_open(*args, **kwargs):
            window.set()
            assert allow_open.wait(5)
            return original(*args, **kwargs)

        def read():
            result["facts"] = reader.entry(approved, rel, attempt=1, snapshot=True)

        def publish():
            writer_started.set()
            result["publish"] = _TransferCacheAdapter(cache).publish(
                "j1", rel, str(replacement), attempt=2, expected_generation=0,
            )
            writer_finished.set()

        with patch.object(_ReadCacheIndexAdapter, "_snapshot_facts", staticmethod(pause_before_open)):
            reader_thread = threading.Thread(target=read)
            reader_thread.start()
            assert window.wait(5)
            writer_thread = threading.Thread(target=publish)
            writer_thread.start()
            assert writer_started.wait(5)
            blocked_at_window = not writer_finished.wait(0.1)
            allow_open.set()
            reader_thread.join(5)
            writer_thread.join(5)
        facts = result["facts"]
        body = reader.read_bytes(facts, 0, 100)
        return {"case": "R07_shared_mutex_snapshot", "writer_blocked_before_open": blocked_at_window,
                "requested_attempt": 1, "returned": body.decode(),
                "current_body": target.read_text(),
                "closed": blocked_at_window and body == b"ATTEMPT_1" and target.read_bytes() == b"ATTEMPT_2"}


def evicted_requeue(prior_successes):
    with TemporaryDirectory() as td:
        root = Path(td)
        with PrepareOperationService(root / "ops.db") as service:
            for i in range(prior_successes):
                service.enqueue(PrepareRequest(job_id=f"old-{i}", attempt=1,
                    resource_id="catalog:singlepoint", resource_kind=ResourceKind.CACHE))
                service.complete(service.lease_next())
            service._conn.execute("UPDATE data_operations SET created_at='2000-01-01T00:00:00Z'")
            record = record_at(root, job_id="target")
            manager = JobManager.__new__(JobManager)
            manager._remote_fetcher = object()
            manager._remote_structure_cache = RemoteStructureCache(root / "runs")
            manager._prepare_operations = service
            manager.store = SimpleNamespace(get=lambda _: record)
            manager._ensure_prepare_worker = lambda: None
            manager._catalog_prefetch_queue = Queue()
            first = service.enqueue(manager._prepare_request(record))
            service.complete(service.lease_next())
            service._conn.execute("UPDATE data_operations SET created_at='2099-01-01T00:00:00Z' WHERE job_id='target'")
            read = AvailabilityRead(job_id="target", resource_id="catalog:singlepoint", workflow="singlepoint")
            reader = build_read_only_reader(job_lookup=lambda _: record, cache=manager.structure_cache, operations=service)
            availability = reader.availability(read)
            manager._queue_catalog_prefetch("target")
            after = service.find(job_id="target", attempt=1, resource_id="catalog:singlepoint")
            return {"case": "R08_evicted_requeue", "prior_successes": prior_successes,
                    "availability_without_body": availability.availability.value,
                    "same_operation": after.operation_id == first.operation_id,
                    "state_after_queue": after.state.value, "pending_count": service.pending_count(),
                    "rebuild_scheduled": after.state.value == "queued" and after.operation_id != first.operation_id}


def auxiliary_retry(fail_once):
    class Fetcher:
        def __init__(self): self.calls = []; self.fail = fail_once
        def read_file(self, record, path):
            self.calls.append(path)
            if path == "RESULT/result_manifest.json":
                return json.dumps({"products": [{"kind": "frequency_modes", "path": "frequencies/normal_modes.json"}]}).encode()
            if path == "RESULT/frequencies/normal_modes.json":
                if self.fail:
                    self.fail = False
                    raise ConnectionError("temporary network interruption")
                return b'{"modes":[]}'
            raise FileNotFoundError(path)

    with TemporaryDirectory() as td:
        root = Path(td)
        fetcher = Fetcher()
        cache = RemoteStructureCache(root / "runs", fetcher_factory=lambda _: fetcher)
        record = record_at(root, workflow="frequency")
        executor = executor_for(cache, record)
        with PrepareOperationService(root / "ops.db", retry_backoff_seconds=0) as service:
            service.enqueue(request_for(record))
            lease = service.lease_next()
            first = executor.execute(lease)
            if first.prepared:
                service.complete(lease)
                last = first
            else:
                service.fail(lease, error_code="transient", retryable=first.retryable)
                second_lease = service.lease_next()
                last = executor.execute(second_lease)
                if last.prepared: service.complete(second_lease)
            cached = cache.cache_path("j1", "RESULT/frequencies/normal_modes.json").exists()
            return {"case": "R09_frequency_mode_prepare", "transient_failure_first": fail_once,
                    "first_prepared": first.prepared, "final_prepared": last.prepared,
                    "final_detail": last.detail, "modes_cached": cached,
                    "modes_download_calls": fetcher.calls.count("RESULT/frequencies/normal_modes.json"),
                    "correct": last.prepared and cached}


if __name__ == "__main__":
    print(json.dumps([real_fetcher_contract(), reader_publisher_snapshot(),
                     evicted_requeue(0), evicted_requeue(1000),
                     auxiliary_retry(False), auxiliary_retry(True)], indent=2))
