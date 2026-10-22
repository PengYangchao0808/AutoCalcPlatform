"""Read-only Data review diagnostics; writes temporary data outside the repository.

Run: PYTHONDONTWRITEBYTECODE=1 python3.11 /tmp/acp_data_recheck_20261021.py
The returned findings are defects reproduced, not tests claiming product PASS.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, "<repo>/src")

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote.fetcher import RemoteResultFetcher
from acp.services_data_bootstrap import (
    build_local_prepare_identity_provider,
    build_prepare_executor,
    build_read_only_reader,
    build_transfer_source_port,
)
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import PrepareOperationService, PrepareRequest
from acp_services.data.read_root import AvailabilityRead, RangeReadRequest


def record_at(root: Path, workflow: str = "singlepoint") -> JobRecord:
    return JobRecord(
        id="j1", spec=JobSpec(workflow=workflow, name="x"),
        status=JobStatus.COMPLETED, attempt=1, work_dir=str(root / "work"),
        result={"node": "n", "remote_dir": "/remote/x"},
    )


def request_for(record: JobRecord) -> PrepareRequest:
    workflow = record.spec.workflow
    return PrepareRequest(
        job_id=record.id, attempt=record.attempt,
        resource_id=f"catalog:{workflow}", resource_kind=ResourceKind.CACHE,
        resource_digest=f"{record.id}\x1f{record.attempt}\x1f{workflow}",
        payload={"workflow": workflow, "job_id": record.id, "attempt": record.attempt},
    )


def real_fetcher_contract() -> dict:
    with TemporaryDirectory() as td:
        root = Path(td)
        # The real production class fails before its SSH/config ports are touched.
        fetcher = RemoteResultFetcher(None, None, None)
        cache = RemoteStructureCache(root / "runs", fetcher_factory=lambda _: fetcher)
        record = record_at(root)
        lookup = lambda _: record
        with PrepareOperationService(root / "db.sqlite") as service:
            service.enqueue(request_for(record))
            lease = service.lease_next()
            executor = build_prepare_executor(
                job_lookup=lookup, cache=cache,
                identity_provider=build_local_prepare_identity_provider(lookup),
                is_remote=lambda _: True,
            )
            outcome = executor.execute(lease)
            source_error = ""
            try:
                build_transfer_source_port(cache).read("j1", 1, "RESULT/result_manifest.json")
            except Exception as exc:
                source_error = f"{type(exc).__name__}: {exc}"
            return {
                "issue": "production_fetcher_loses_remote_record",
                "prepared": outcome.prepared, "detail": outcome.detail,
                "retryable": outcome.retryable, "source_error": source_error,
                "reproduced": not outcome.prepared and "has no attribute 'result'" in source_error,
            }


def eviction_reprepare() -> dict:
    with TemporaryDirectory() as td:
        root = Path(td)
        cache = RemoteStructureCache(root / "runs")
        record = record_at(root)
        request = request_for(record)
        with PrepareOperationService(root / "db.sqlite") as service:
            first = service.enqueue(request)
            service.complete(service.lease_next())
            target = cache.cache_path("j1", "RESULT/result_manifest.json")
            target.parent.mkdir(parents=True)
            target.write_bytes(b"{}")
            cache._write_attempt_sidecar(target, 1)
            reader = build_read_only_reader(job_lookup=lambda _: record, cache=cache, operations=service)
            read = AvailabilityRead(job_id="j1", resource_id="catalog:singlepoint", workflow="singlepoint")
            before = reader.availability(read).to_dict()
            cache.purge_job("j1")
            after = reader.availability(read).to_dict()
            second = service.enqueue(request)
            same = second.operation_id == first.operation_id
            pending = service.pending_count()
            no_lease = service.lease_next() is None
            return {
                "issue": "eviction_keeps_ready_and_cannot_reprepare", "before": before,
                "after": after, "same_operation": same, "reenqueue_state": second.state.value,
                "pending_count": pending, "no_next_lease": no_lease,
                "reproduced": after["availability"] == "ready" and after["prepared_root"] is None and same and no_lease,
            }


def snapshot_rerun_race() -> dict:
    with TemporaryDirectory() as td:
        root = Path(td)
        cache = RemoteStructureCache(root / "runs")
        record = record_at(root)
        target = cache.cache_path("j1", "RESULT/result_manifest.json")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"ATTEMPT_1")
        cache._write_attempt_sidecar(target, 1)
        cache._write_fence("j1", attempt=1, generation=0)
        original_get = cache.get_cached

        def get_then_rerun(*args, **kwargs):
            approved = original_get(*args, **kwargs)
            # Schedule another publisher after the sidecar check, before open.
            record.attempt = 2
            replacement = target.with_name("replacement.tmp")
            replacement.write_bytes(b"ATTEMPT_2")
            replacement.replace(target)
            cache._write_attempt_sidecar(target, 2)
            cache._write_fence("j1", attempt=2, generation=0)
            return approved

        cache.get_cached = get_then_rerun
        reader = build_read_only_reader(job_lookup=lambda _: record, cache=cache)
        response = reader.read_range(RangeReadRequest(
            job_id="j1", relative_path="RESULT/result_manifest.json", attempt=1,
        ))
        return {
            "issue": "remote_read_check_open_attempt_race", "requested_attempt": 1,
            "current_attempt": record.attempt, "returned_bytes": response.data.decode(),
            "reproduced": response.data == b"ATTEMPT_2",
        }


def auxiliary_products() -> dict:
    class Fetcher:
        def __init__(self):
            self.calls = []

        def read_file(self, _record, path):
            self.calls.append(path)
            if path == "RESULT/result_manifest.json":
                return json.dumps({"products": [{
                    "kind": "frequency_modes", "path": "frequencies/normal_modes.json",
                }]}).encode()
            if path == "RESULT/frequencies/normal_modes.json":
                return b'{"modes":[]}'
            raise FileNotFoundError(path)

    with TemporaryDirectory() as td:
        root = Path(td)
        fetcher = Fetcher()
        cache = RemoteStructureCache(root / "runs", fetcher_factory=lambda _: fetcher)
        record = record_at(root, workflow="frequency")
        lookup = lambda _: record
        with PrepareOperationService(root / "db.sqlite") as service:
            service.enqueue(request_for(record))
            executor = build_prepare_executor(
                job_lookup=lookup, cache=cache,
                identity_provider=build_local_prepare_identity_provider(lookup),
                is_remote=lambda _: True,
            )
            outcome = executor.execute(service.lease_next())
            cached = cache.cache_path("j1", "RESULT/frequencies/normal_modes.json").exists()
            new_requested = list(fetcher.calls)
            legacy_fetcher = Fetcher()
            legacy_cache = RemoteStructureCache(
                root / "legacy-runs", fetcher_factory=lambda _: legacy_fetcher,
            )
            legacy_cache.fetch_catalog(record, "frequency")
            legacy_cached = legacy_cache.cache_path(
                "j1", "RESULT/frequencies/normal_modes.json",
            ).exists()
            return {
                "issue": "catalog_prepare_loses_auxiliary_frequency_modes",
                "prepared": outcome.prepared, "detail": outcome.detail,
                "mode_path_cached": cached, "requested_paths": new_requested,
                "retained_legacy_fetch_catalog_fetched_modes": legacy_cached,
                "reproduced": outcome.prepared and not cached and legacy_cached,
            }


if __name__ == "__main__":
    findings = [real_fetcher_contract(), eviction_reprepare(), snapshot_rerun_race(), auxiliary_products()]
    print(json.dumps(findings, ensure_ascii=False, indent=2))
