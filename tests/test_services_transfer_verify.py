"""B02-4 (todo 14) — verified transfer → publish → availability.

Exercises the Services transfer pipeline (``acp_services.data.transfer``) and
its legacy-side adapters (``acp.services_data_bootstrap``):

1. happy path — download → verify → atomic publish → availability committed.
2. four rejection classes — stale attempt / partial file / permission revoked /
   late transfer (each recorded, cache unchanged).
3. no ``.tmp`` residue in the cache directory on any path.
4. crash-window reconciliation — unregistered file / missing file / late worker.
5. historical-adoption matrix — new receipt / with-digest old / no-digest old v2
   (adopted baseline) / mutable resource / corrupt source / untrusted.
6. F0 freeze — a pre-existing legitimate task stays readable.
7. real cache adapter — traversal guard, fence/generation, publish + push_paths.

Run with: pytest tests/test_services_transfer_verify.py -q
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.services_data_bootstrap import (
    build_local_single_transfer_authorization,
    build_transfer_cache_port,
    build_transfer_pipeline,
    build_transfer_source_port,
)
from acp.storage.manifest import ResultManifest
from acp_services.data import contracts as dc
from acp_services.data import operations as ops
from acp_services.data import transfer as tx

_REPO_ROOT = Path(__file__).resolve().parents[1]
_F0_MANIFEST = (
    _REPO_ROOT
    / "tests"
    / "baseline"
    / "recovery_fixtures"
    / "historical_manifest"
    / "RESULT_v2"
    / "result_manifest.json"
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeSource:
    """Deterministic source double: bytes + optional failure/revision."""

    def __init__(
        self,
        data: bytes = b"",
        *,
        revision: int = 0,
        fail: tx.TransferSourceError | None = None,
        on_read: Any = None,
    ) -> None:
        self.data = data
        self.revision = revision
        self.fail = fail
        self.on_read = on_read
        self.calls: list[tuple[str, int, str]] = []

    def read(
        self,
        job_id: str,
        attempt: int,
        relative_path: str,
        *,
        byte_range: Any = None,
    ) -> tx.SourceRead:
        self.calls.append((job_id, attempt, relative_path))
        if self.on_read is not None:
            self.on_read()
        if self.fail is not None:
            raise self.fail
        return tx.SourceRead(data=self.data, size_bytes=len(self.data), revision=self.revision)


class FakeAuthorization:
    """Authorization double: deny a named action (or everything after a call)."""

    def __init__(self, deny: tuple[str, ...] = ()) -> None:
        self.deny = set(deny)
        self.calls: list[str] = []

    def authorize(
        self,
        *,
        job_id: str,
        attempt: int,
        relative_path: str,
        action: str,
        authorization_version: int,
    ) -> bool:
        self.calls.append(action)
        return action not in self.deny


class FakeCache:
    """In-memory cache double backed by a real directory (for .tmp checks)."""

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

    def publish(
        self,
        job_id: str,
        relative_path: str,
        tmp_path: str,
        *,
        attempt: int,
        expected_generation: int,
    ) -> tx.PublishOutcome:
        if self.generation != expected_generation:
            return tx.PublishOutcome(published=False, discarded=True, reason="generation_changed")
        if self.attempt is not None and attempt < self.attempt:
            return tx.PublishOutcome(published=False, discarded=True, reason="attempt_advanced")
        if self.attempt is None or attempt > self.attempt:
            self.attempt = attempt
        target = self._guard(job_id, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
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

    def push_paths(self, job_id: str, relative_paths: Any) -> list[str]:
        self.pushed.extend(relative_paths)
        return list(relative_paths)


class FakeAdoption:
    def __init__(self, value: tx.ExpectedDigest | None) -> None:
        self.value = value
        self.calls = 0

    def resolve(self, request: tx.TransferRequest) -> tx.ExpectedDigest | None:
        self.calls += 1
        return self.value


def _pipeline(
    tmp_path: Path,
    *,
    source: FakeSource,
    authorization: FakeAuthorization | None = None,
    adoption: FakeAdoption | None = None,
) -> tuple[tx.TransferPipeline, FakeCache, tx.TransferPublicationStore]:
    cache = FakeCache(tmp_path / ".remote_cache")
    store = tx.TransferPublicationStore(tmp_path / "publications.db")
    pipeline = tx.TransferPipeline(
        cache=cache,
        source=source,
        authorization=authorization or FakeAuthorization(),
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
    resource_class: tx.ResourceClass = tx.ResourceClass.ATTEMPT_ARTIFACT,
    expected_revision: int = 0,
) -> tx.TransferRequest:
    import hashlib

    return tx.TransferRequest(
        job_id=job_id,
        attempt=attempt,
        relative_path=relative_path,
        resource_class=resource_class,
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


# ---------------------------------------------------------------------------
# 1. happy path + availability commit
# ---------------------------------------------------------------------------


def test_happy_download_verify_publish_and_availability(tmp_path: Path) -> None:
    payload = b'{"version": 2, "products": []}'
    source = FakeSource(payload)
    pipeline, cache, store = _pipeline(tmp_path, source=source)

    service = ops.PrepareOperationService(
        tmp_path / "acp_jobs.db", retry_backoff_seconds=0.0, default_lease_seconds=10.0
    )
    request = ops.PrepareRequest(
        job_id="job_1",
        attempt=1,
        resource_id="catalog:Confsearch",
        resource_kind=dc.ResourceKind.CACHE,
        resource_digest="dg-1",
    )
    op = service.enqueue(request)

    executor = tx.TransferPrepareExecutor(
        pipeline, lambda ls: _request(payload, attempt=ls.attempt)
    )
    worker = ops.PrepareOperationWorker(service, executor, poll_interval=0.01)
    worker.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            record = service.get(op.operation_id)
            if record is not None and record.state is ops.PrepareState.SUCCEEDED:
                break
            time.sleep(0.01)
    finally:
        worker.stop()

    done = service.get(op.operation_id)
    assert done is not None and done.state is ops.PrepareState.SUCCEEDED
    availability = service.availability(op.operation_id)
    assert availability.availability is dc.Availability.READY
    assert availability.freshness is dc.Freshness.CURRENT
    assert done.sha256 == tx.sha256_bytes(payload)
    assert done.size_bytes == len(payload)

    assert cache.published["RESULT/result_manifest.json"] == payload
    published = store.get("job_1", 1, "RESULT/result_manifest.json")
    assert published is not None
    assert published.state == tx.PublicationState.PUBLISHED.value
    assert published.verification_level == tx.VerificationLevel.VERIFIED.value
    assert _tmp_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 2. four rejection classes
# ---------------------------------------------------------------------------


def test_reject_stale_attempt_receipt(tmp_path: Path) -> None:
    source = FakeSource(b"x")
    pipeline, cache, store = _pipeline(tmp_path, source=source)
    request = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="RESULT/result_manifest.json",
        receipt=tx.TransferReceipt(
            job_id="job_1",
            attempt=2,
            relative_path="RESULT/result_manifest.json",
            sha256=tx.sha256_bytes(b"x"),
        ),
    )
    result = pipeline.prepare(request)
    assert result.accepted is False
    assert result.rejection is tx.RejectionCode.STALE_ATTEMPT
    assert source.calls == []
    assert cache.published == {}
    assert store.list_all() == ()


def test_reject_partial_file_size_mismatch(tmp_path: Path) -> None:
    source = FakeSource(b"short")
    pipeline, cache, store = _pipeline(tmp_path, source=source)
    request = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="RESULT/result.json",
        expected=tx.ExpectedDigest(sha256=tx.sha256_bytes(b"the full content"), size_bytes=16),
    )
    result = pipeline.prepare(request)
    assert result.accepted is False
    assert result.rejection is tx.RejectionCode.PARTIAL_FILE
    assert cache.published == {}
    assert _tmp_files(tmp_path) == []


def test_reject_digest_mismatch(tmp_path: Path) -> None:
    source = FakeSource(b"actual")
    pipeline, cache, _store = _pipeline(tmp_path, source=source)
    request = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="RESULT/result.json",
        expected=tx.ExpectedDigest(sha256=tx.sha256_bytes(b"expected"), size_bytes=len(b"actual")),
    )
    result = pipeline.prepare(request)
    assert result.rejection is tx.RejectionCode.DIGEST_MISMATCH
    assert cache.published == {}


def test_reject_permission_revoked_before_download(tmp_path: Path) -> None:
    source = FakeSource(b"x")
    auth = FakeAuthorization(deny=("download",))
    pipeline, cache, _store = _pipeline(tmp_path, source=source, authorization=auth)
    result = pipeline.prepare(_request(b"x"))
    assert result.rejection is tx.RejectionCode.PERMISSION_REVOKED
    assert source.calls == []
    assert cache.published == {}


def test_reject_permission_revoked_before_publish(tmp_path: Path) -> None:
    source = FakeSource(b"x")
    auth = FakeAuthorization(deny=("publish",))
    pipeline, cache, store = _pipeline(tmp_path, source=source, authorization=auth)
    result = pipeline.prepare(_request(b"x"))
    assert result.rejection is tx.RejectionCode.PERMISSION_REVOKED
    assert auth.calls == ["download", "publish"]
    assert cache.published == {}
    assert store.list_all() == ()
    assert _tmp_files(tmp_path) == []


def test_reject_source_permission_failure_classified(tmp_path: Path) -> None:
    source = FakeSource(
        fail=tx.TransferSourceError(tx.SourceFailureKind.PERMISSION_DENIED, "denied")
    )
    pipeline, _cache, _store = _pipeline(tmp_path, source=source)
    result = pipeline.prepare(_request(b"x"))
    assert result.rejection is tx.RejectionCode.PERMISSION_REVOKED
    assert result.retryable is False


def test_reject_late_transfer_discarded_and_recorded(tmp_path: Path) -> None:
    payload = b"late"
    cache_box: dict[str, FakeCache] = {}

    def bump_generation() -> None:
        cache_box["cache"].generation += 1

    source = FakeSource(payload, on_read=bump_generation)
    pipeline, cache, store = _pipeline(tmp_path, source=source)
    cache_box["cache"] = cache
    result = pipeline.prepare(_request(payload))
    assert result.accepted is False
    assert result.rejection is tx.RejectionCode.LATE_TRANSFER
    assert cache.published == {}
    records = store.list_all()
    assert len(records) == 1
    assert records[0].state == tx.PublicationState.DISCARDED.value
    assert _tmp_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 3. no .tmp residue
# ---------------------------------------------------------------------------


def test_no_tmp_residue_after_mixed_runs(tmp_path: Path) -> None:
    source = FakeSource(b"ok")
    pipeline, _cache, _store = _pipeline(tmp_path, source=source)
    pipeline.prepare(_request(b"ok", relative_path="RESULT/a.json"))
    pipeline.prepare(
        tx.TransferRequest(
            job_id="job_1",
            attempt=1,
            relative_path="RESULT/b.json",
            expected=tx.ExpectedDigest(sha256=tx.sha256_bytes(b"other"), size_bytes=2),
        )
    )
    assert _tmp_files(tmp_path) == []


# ---------------------------------------------------------------------------
# 4. crash-window reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_unregistered_missing_and_late(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path / ".remote_cache")
    store = tx.TransferPublicationStore(tmp_path / "publications.db")

    unregistered_path = cache.guarded_path("job_1", "RESULT/orphan.json")
    unregistered_path.parent.mkdir(parents=True, exist_ok=True)
    unregistered_path.write_bytes(b"orphan")

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
    store.record(
        tx.PublicationRecord(
            job_id="job_1",
            attempt=1,
            relative_path="RESULT/late.json",
            resource_class=tx.ResourceClass.ATTEMPT_ARTIFACT.value,
            state=tx.PublicationState.DISCARDED.value,
            sha256=None,
            size_bytes=None,
            resource_generation=0,
            basis_revision=0,
            verification_level=tx.VerificationLevel.UNVERIFIED.value,
            adopted_from="",
            created_at="",
            updated_at="",
        )
    )

    report = store.reconcile(cache, job_ids=["job_1"])
    assert "job_1:RESULT/orphan.json" in report.unregistered
    assert "job_1:RESULT/gone.json" in report.missing
    assert "job_1:RESULT/late.json" in report.late
    # Reconciliation classifies; it never deletes the orphan.
    assert unregistered_path.is_file()
    assert store.get("job_1", 1, "RESULT/gone.json").state == tx.PublicationState.MISSING.value


# ---------------------------------------------------------------------------
# 5. historical adoption matrix
# ---------------------------------------------------------------------------


def test_adoption_new_receipt_verified(tmp_path: Path) -> None:
    payload = b"new"
    pipeline, _cache, store = _pipeline(tmp_path, source=FakeSource(payload))
    result = pipeline.prepare(_request(payload))
    assert result.accepted
    assert result.verification_level is tx.VerificationLevel.VERIFIED
    assert store.get("job_1", 1, "RESULT/result_manifest.json").verification_level == "verified"


def test_adoption_existing_manifest_digest(tmp_path: Path) -> None:
    payload = b"historical-with-digest"
    pipeline, _cache, store = _pipeline(tmp_path, source=FakeSource(payload))
    request = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="RESULT/result_manifest.json",
        expected=tx.ExpectedDigest(
            sha256=tx.sha256_bytes(payload),
            size_bytes=len(payload),
            source="manifest_existing",
            verification_level=tx.VerificationLevel.EXISTING_DIGEST,
        ),
    )
    result = pipeline.prepare(request)
    assert result.accepted
    assert result.verification_level is tx.VerificationLevel.EXISTING_DIGEST
    assert store.get("job_1", 1, "RESULT/result_manifest.json").adopted_from == "manifest_existing"


def test_adoption_no_digest_via_controlled_source_snapshot(tmp_path: Path) -> None:
    payload = b"old-v2-no-digest"
    adoption = FakeAdoption(
        tx.ExpectedDigest(
            sha256=tx.sha256_bytes(payload),
            size_bytes=len(payload),
            source="source_snapshot",
            verification_level=tx.VerificationLevel.ADOPTED_BASELINE,
        )
    )
    pipeline, _cache, store = _pipeline(tmp_path, source=FakeSource(payload), adoption=adoption)
    request = tx.TransferRequest(
        job_id="job_1", attempt=1, relative_path="RESULT/result_manifest.json"
    )
    result = pipeline.prepare(request)
    assert result.accepted
    assert result.verification_level is tx.VerificationLevel.ADOPTED_BASELINE
    assert adoption.calls == 1
    record = store.get("job_1", 1, "RESULT/result_manifest.json")
    assert record.verification_level == "adopted_baseline"
    assert record.adopted_from == "source_snapshot"


def test_adoption_without_trusted_baseline_is_terminal_not_pending(tmp_path: Path) -> None:
    pipeline, cache, store = _pipeline(
        tmp_path, source=FakeSource(b"x"), adoption=FakeAdoption(None)
    )
    result = pipeline.prepare(
        tx.TransferRequest(job_id="job_1", attempt=1, relative_path="RESULT/x.json")
    )
    assert result.rejection is tx.RejectionCode.MISSING_EXPECTED_DIGEST
    assert result.retryable is False
    assert cache.published == {}
    assert store.list_all() == ()


def test_adoption_corrupt_source_rejected(tmp_path: Path) -> None:
    adoption = FakeAdoption(
        tx.ExpectedDigest(
            sha256=tx.sha256_bytes(b"trusted-baseline"),
            size_bytes=len(b"trusted-baseline"),
            source="source_snapshot",
            verification_level=tx.VerificationLevel.ADOPTED_BASELINE,
        )
    )
    pipeline, cache, _store = _pipeline(
        tmp_path, source=FakeSource(b"0" * len(b"trusted-baseline")), adoption=adoption
    )
    result = pipeline.prepare(
        tx.TransferRequest(job_id="job_1", attempt=1, relative_path="RESULT/x.json")
    )
    assert result.rejection is tx.RejectionCode.DIGEST_MISMATCH
    assert cache.published == {}


def test_mutable_resource_revision_consistency(tmp_path: Path) -> None:
    payload = b"log line\n"
    pipeline, _cache, store = _pipeline(tmp_path, source=FakeSource(payload, revision=42))
    request = tx.TransferRequest(
        job_id="job_1",
        attempt=1,
        relative_path="WORK/run.log",
        resource_class=tx.ResourceClass.MUTABLE,
        expected_revision=42,
        basis_revision=42,
    )
    result = pipeline.prepare(request)
    assert result.accepted
    assert result.verification_level is tx.VerificationLevel.REVISION_CONSISTENT
    record = store.get("job_1", 1, "WORK/run.log")
    assert record.basis_revision == 42

    stale = pipeline.prepare(
        tx.TransferRequest(
            job_id="job_1",
            attempt=1,
            relative_path="WORK/run.log",
            resource_class=tx.ResourceClass.MUTABLE,
            expected_revision=41,
        )
    )
    assert stale.rejection is tx.RejectionCode.STALE_REVISION


# ---------------------------------------------------------------------------
# 6. F0 freeze — a pre-existing legitimate task still reads
# ---------------------------------------------------------------------------


def test_f0_preexisting_task_still_reads(tmp_path: Path) -> None:
    run_root = tmp_path / "run_root"
    cache = RemoteStructureCache(run_root)
    rel = "RESULT/result_manifest.json"
    legacy = cache.cache_path("legacy_job", rel)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b'{"version": 2}')

    # The frozen read path (no attempt identity) still serves the entry.
    assert cache.get_cached("legacy_job", rel) == legacy

    store = tx.TransferPublicationStore(tmp_path / "publications.db")
    report = store.reconcile(build_transfer_cache_port(cache), job_ids=["legacy_job"])
    assert "legacy_job:RESULT/result_manifest.json" in report.unregistered
    # Classified, not deleted; the original read still works.
    assert legacy.is_file()
    assert cache.get_cached("legacy_job", rel) == legacy

    # A frozen pre-migration manifest fixture remains readable.
    manifest = ResultManifest.read(_F0_MANIFEST.parent)
    assert manifest is not None
    assert _F0_MANIFEST.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. real cache adapter — traversal guard / fence / publish / push_paths
# ---------------------------------------------------------------------------


class _FakeFetcher:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.writes: dict[str, bytes] = {}

    def read_file(self, record: Any, filename: str) -> bytes:
        if filename not in self.files:
            raise FileNotFoundError(filename)
        return self.files[filename]

    def read_range(self, record: Any, filename: str, offset: int, limit: int) -> bytes:
        return self.read_file(record, filename)[offset : offset + limit]

    def file_info(self, record: Any, filename: str) -> Any:
        data = self.read_file(record, filename)
        return SimpleNamespace(name=filename, size=len(data), mtime=1234.5, is_dir=False)

    def write_file(self, record: Any, filename: str, data: bytes) -> None:
        self.writes[filename] = data


def test_real_cache_adapter_publishes_and_pushes_paths(tmp_path: Path) -> None:
    payload = b'{"version": 2, "products": [1]}'
    rel = "RESULT/result_manifest.json"
    fetcher = _FakeFetcher({rel: payload})
    cache = RemoteStructureCache(tmp_path / "run_root", fetcher_factory=lambda _jid: fetcher)
    pipeline = build_transfer_pipeline(
        cache=cache,
        authorization=build_local_single_transfer_authorization(),
        identity_check=lambda: None,
        db_path=tmp_path / "publications.db",
    )

    request = _request(payload)
    result = pipeline.prepare(request)
    assert result.accepted is True

    cached = cache.cache_path("job_1", rel)
    assert cached.read_bytes() == payload
    assert Path(str(cached) + ".attempt").is_file()
    assert cache.get_cached("job_1", rel, attempt=1) == cached

    written = pipeline.push_paths("job_1", [rel])
    assert written == [rel]
    assert fetcher.writes[rel] == payload
    assert _tmp_files(tmp_path) == []


def test_real_cache_adapter_traversal_guard(tmp_path: Path) -> None:
    cache = RemoteStructureCache(tmp_path / "run_root")
    port = build_transfer_cache_port(cache)
    with pytest.raises(ValueError):
        port.guarded_path("job_1", "../../etc/passwd")
    with pytest.raises(ValueError):
        port.guarded_path("../job_2", "RESULT/x.json")


def test_real_cache_adapter_source_classifies_permission(tmp_path: Path) -> None:
    class _DeniedFetcher:
        def read_file(self, record: Any, filename: str) -> bytes:
            raise PermissionError(13, "Permission denied")

    cache = RemoteStructureCache(
        tmp_path / "run_root", fetcher_factory=lambda _jid: _DeniedFetcher()
    )
    source = build_transfer_source_port(cache)
    with pytest.raises(tx.TransferSourceError) as excinfo:
        source.read("job_1", 1, "RESULT/x.json")
    assert excinfo.value.kind is tx.SourceFailureKind.PERMISSION_DENIED


# ---------------------------------------------------------------------------
# 8. D06 (task 16) — production request factory + publish-boundary guards
# ---------------------------------------------------------------------------


def _file_lease(**overrides: Any) -> ops.PrepareLease:
    payload: dict[str, Any] = {
        "workflow": "singlepoint",
        "relative_path": "RESULT/result_manifest.json",
        "expected_sha256": tx.sha256_bytes(b"NEW"),
        "expected_size": 3,
        "resource_class": tx.ResourceClass.ATTEMPT_ARTIFACT.value,
        "byte_range": {"offset": 2, "limit": 4},
    }
    payload.update(overrides.pop("payload", {}))
    fields: dict[str, Any] = {
        "operation_id": "op-file-1",
        "job_id": "job_1",
        "attempt": 2,
        "resource_id": "artifact:RESULT/result_manifest.json",
        "resource_kind": dc.ResourceKind.ARTIFACT.value,
        "resource_digest": "dg-file",
        "resource_key": "rk-file",
        "permission_scope": "unit",
        "owner_generation": 4,
        "lease_token": "token",
        "lease_expires_at": None,
        "authorization_version": 7,
        "binding_id": "binding-1",
        "resource_generation": 3,
        "attempt_count": 1,
        "payload": payload,
    }
    fields.update(overrides)
    return ops.PrepareLease(**fields)


def test_production_request_factory_carries_frozen_lease_identity() -> None:
    from acp.services_data_bootstrap import build_transfer_request_factory

    request = build_transfer_request_factory()(_file_lease())

    assert request.job_id == "job_1"
    assert request.attempt == 2
    assert request.relative_path == "RESULT/result_manifest.json"
    assert request.resource_class is tx.ResourceClass.ATTEMPT_ARTIFACT
    assert request.resource_generation == 3
    assert request.authorization_version == 7
    assert request.binding_id == "binding-1"
    assert request.byte_range == tx.ByteRange(offset=2, limit=4)
    assert request.expected is not None
    assert request.expected.sha256 == tx.sha256_bytes(b"NEW")
    assert request.expected.size_bytes == 3
    assert request.expected.verification_level is tx.VerificationLevel.VERIFIED


def test_request_factory_requires_a_frozen_relative_path() -> None:
    from acp.services_data_bootstrap import build_transfer_request_factory

    lease = _file_lease(payload={"relative_path": None})
    with pytest.raises(ValueError, match="relative_path"):
        build_transfer_request_factory()(lease)


def test_missing_expected_digest_is_rejected_never_invented(tmp_path: Path) -> None:
    payload = b"x"
    source = FakeSource(payload)
    pipeline, cache, store = _pipeline(tmp_path, source=source)

    lease = _file_lease(payload={"expected_sha256": None, "expected_size": None})
    from acp.services_data_bootstrap import build_transfer_request_factory

    request = build_transfer_request_factory()(lease)
    assert request.expected is None

    result = pipeline.prepare(request)
    assert result.accepted is False
    assert result.rejection is tx.RejectionCode.MISSING_EXPECTED_DIGEST
    assert result.retryable is False
    assert source.calls == []
    assert cache.published == {}
    assert store.list_all() == ()


def test_transfer_assembly_refuses_missing_authorization_and_identity(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import (
        build_transfer_authorization_port,
        build_transfer_pipeline,
    )

    cache = RemoteStructureCache(tmp_path / "run_root")
    with pytest.raises(ValueError, match="authorization"):
        build_transfer_pipeline(cache=cache, identity_check=lambda: None)
    with pytest.raises(ValueError, match="identity_check"):
        build_transfer_pipeline(
            cache=cache, authorization=build_local_single_transfer_authorization()
        )
    with pytest.raises(ValueError, match="authorization"):
        build_transfer_authorization_port()
    with pytest.raises(ValueError, match="authorization"):
        tx.TransferPipeline(
            cache=FakeCache(tmp_path / "cache"),
            source=FakeSource(b""),
            authorization=None,  # type: ignore[arg-type]
            publications=tx.TransferPublicationStore(tmp_path / "pub.db"),
        )


def _wait_for_state(
    service: ops.PrepareOperationService, operation_id: str, timeout: float = 5.0
) -> ops.PrepareOperationRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.get(operation_id)
        if record is not None and record.is_terminal:
            return record
        time.sleep(0.01)
    raise AssertionError(f"operation {operation_id} did not finish")


def _run_file_worker(
    tmp_path: Path,
    cache: RemoteStructureCache,
    *,
    job_id: str,
    relative_path: str,
    expected: bytes,
    publications: tx.TransferPublicationStore,
    authorization: Any,
    identity_check: Any,
    before_publish: Any = None,
    max_attempts: int = 1,
) -> tuple[ops.PrepareOperationRecord, ops.PrepareOperationService, tx.TransferPublicationStore]:
    from acp.services_data_bootstrap import (
        build_transfer_executor,
        build_transfer_pipeline,
        build_transfer_request_factory,
    )

    pipeline = build_transfer_pipeline(
        cache=cache,
        authorization=authorization,
        identity_check=identity_check,
        publications=publications,
        before_publish=before_publish,
    )
    executor = build_transfer_executor(pipeline, build_transfer_request_factory())
    service = ops.PrepareOperationService(
        tmp_path / f"ops-{job_id}.db", retry_backoff_seconds=0.0, default_lease_seconds=10.0
    )
    request = ops.PrepareRequest(
        job_id=job_id,
        attempt=1,
        resource_id=f"artifact:{relative_path}",
        resource_kind=dc.ResourceKind.ARTIFACT,
        resource_digest="dg-file",
        payload={
            "workflow": "singlepoint",
            "relative_path": relative_path,
            "expected_sha256": tx.sha256_bytes(expected),
            "expected_size": len(expected),
        },
        max_attempts=max_attempts,
    )
    op = service.enqueue(request)
    worker = ops.PrepareOperationWorker(service, executor, poll_interval=0.01)
    worker.start()
    try:
        done = _wait_for_state(service, op.operation_id)
    finally:
        worker.stop()
    return done, service, publications


def test_happy_file_download_publishes_once_and_commits_readiness(tmp_path: Path) -> None:
    rel = "RESULT/result_manifest.json"
    payload = b'{"version": 2, "products": []}'
    fetcher = _FakeFetcher({rel: payload})
    fetcher.file_info = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    cache = RemoteStructureCache(tmp_path / "runs", fetcher_factory=lambda _jid: fetcher)
    publications = tx.TransferPublicationStore(tmp_path / "publications.db")

    calls = {"reads": 0}
    original_read = fetcher.read_file

    def counting_read(record: Any, filename: str) -> bytes:
        calls["reads"] += 1
        return original_read(record, filename)

    fetcher.read_file = counting_read  # type: ignore[method-assign]

    done, service, store = _run_file_worker(
        tmp_path,
        cache,
        job_id="job_happy",
        relative_path=rel,
        expected=payload,
        publications=publications,
        authorization=build_local_single_transfer_authorization(),
        identity_check=lambda: None,
    )
    try:
        assert done.state is ops.PrepareState.SUCCEEDED
        assert done.sha256 == tx.sha256_bytes(payload)
        assert done.size_bytes == len(payload)
        availability = service.availability(done.operation_id)
        assert availability.availability is dc.Availability.READY
        assert calls["reads"] == 1
        assert cache.cache_path("job_happy", rel).read_bytes() == payload
        records = store.list_by_job("job_happy")
        assert len(records) == 1
        assert records[0].state == tx.PublicationState.PUBLISHED.value
        assert records[0].sha256 == tx.sha256_bytes(payload)
    finally:
        service.close()
        store.close()


@pytest.mark.parametrize("event", ["rerun", "rebind", "revoke", "evict"])
def test_publish_barrier_event_never_replaces_current_bytes(tmp_path: Path, event: str) -> None:
    job_id = f"job_barrier_{event}"
    rel = "RESULT/result_manifest.json"
    current = b"CURRENT"
    verified = b"NEW-VERIFIED"
    cache = RemoteStructureCache(
        tmp_path / f"runs_{event}", fetcher_factory=lambda _jid: _FakeFetcher({rel: verified})
    )
    target = cache.cache_path(job_id, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(current)
    cache._write_attempt_sidecar(target, 1)
    cache._write_fence(job_id, attempt=1, generation=0)

    state = {"valid": True, "reason": ""}

    def identity_check() -> str | None:
        return None if state["valid"] else state["reason"]

    def barrier() -> None:
        if event == "rerun":
            cache._write_fence(job_id, attempt=2, generation=0)
        elif event == "evict":
            cache._write_fence(job_id, attempt=1, generation=1)
        elif event == "rebind":
            state.update(valid=False, reason="identity_mismatch:binding")
        else:
            state.update(valid=False, reason="authorization_revoked")

    publications = tx.TransferPublicationStore(tmp_path / f"publications_{event}.db")
    done, service, store = _run_file_worker(
        tmp_path,
        cache,
        job_id=job_id,
        relative_path=rel,
        expected=verified,
        publications=publications,
        authorization=build_local_single_transfer_authorization(),
        identity_check=identity_check,
        before_publish=barrier,
    )
    try:
        assert done.state is ops.PrepareState.FAILED
        assert service.availability(done.operation_id).availability is not dc.Availability.READY
        assert target.read_bytes() == current
        assert cache._read_attempt_sidecar(target) == (True, 1)
        records = store.list_by_job(job_id)
        assert all(record.state != tx.PublicationState.PUBLISHED.value for record in records)
    finally:
        service.close()
        store.close()
    assert _tmp_files(tmp_path) == []


def test_catalog_bundle_publishes_through_the_same_publish_guard(tmp_path: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.services_data_bootstrap import (
        build_local_prepare_identity_provider,
        build_prepare_executor,
    )

    job_id = "job_catalog_guard"
    rel = "RESULT/result_manifest.json"
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="singlepoint", name="catalog"),
        status=JobStatus.COMPLETED,
        attempt=1,
        work_dir=str(tmp_path / job_id),
        result={"node": "node1", "remote_dir": f"/remote/{job_id}"},
    )
    cache = RemoteStructureCache(
        tmp_path / "runs_catalog",
        fetcher_factory=lambda _jid: _FakeFetcher({rel: b"NEW-CATALOG", "input.xyz": b"1\n"}),
    )
    seeded = cache.cache_path(job_id, "RESULT/frame_candidates.json")
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_bytes(b"CURRENT")
    target = cache.cache_path(job_id, rel)
    cache._write_fence(job_id, attempt=1, generation=0)

    state = {"valid": True}

    def before_publish() -> None:
        state["valid"] = False

    local = build_local_prepare_identity_provider(lambda _jid: record)

    def provider(lease: ops.PrepareLease) -> Any:
        identity = local(lease)
        if identity is None or state["valid"]:
            return identity
        return replace(identity, authorized=False)

    executor = build_prepare_executor(
        job_lookup=lambda _jid: record,
        identity_provider=provider,
        cache=cache,
        is_remote=lambda _record: True,
        before_publish=before_publish,
    )
    service = ops.PrepareOperationService(tmp_path / "ops-catalog.db", retry_backoff_seconds=0.0)
    try:
        workflow = "singlepoint"
        service.enqueue(
            ops.PrepareRequest(
                job_id=job_id,
                attempt=1,
                resource_id=f"catalog:{workflow}",
                resource_kind=dc.ResourceKind.CACHE,
                resource_digest=f"{job_id}\x1f1\x1f{workflow}",
                payload={"workflow": workflow},
            )
        )
        lease = service.lease_next()
        assert lease is not None
        result = executor.execute(lease)
    finally:
        service.close()

    assert result.prepared is False
    assert result.retryable is False
    assert result.detail.startswith("publish_rejected:")
    assert target.exists() is False
    assert seeded.read_bytes() == b"CURRENT"


def test_cache_hit_commit_boundary_rechecks_identity(tmp_path: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.services_data_bootstrap import (
        build_local_prepare_identity_provider,
        build_prepare_executor,
    )

    job_id = "job_cache_hit_guard"
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="singlepoint", name="cache-hit"),
        status=JobStatus.COMPLETED,
        attempt=1,
        work_dir=str(tmp_path / job_id),
        result={"node": "node1", "remote_dir": f"/remote/{job_id}"},
    )
    cache = RemoteStructureCache(
        tmp_path / "runs_hit", fetcher_factory=lambda _jid: _FakeFetcher({})
    )
    target = cache.cache_path(job_id, "RESULT/result_manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"cached")
    cache._write_attempt_sidecar(target, 1)
    cache._write_fence(job_id, attempt=1, generation=0)

    local = build_local_prepare_identity_provider(lambda _jid: record)
    calls = {"count": 0}

    def provider(lease: ops.PrepareLease) -> Any:
        calls["count"] += 1
        identity = local(lease)
        if calls["count"] >= 2 and identity is not None:
            return replace(identity, authorized=False)
        return identity

    executor = build_prepare_executor(
        job_lookup=lambda _jid: record,
        identity_provider=provider,
        cache=cache,
        is_remote=lambda _record: True,
    )
    service = ops.PrepareOperationService(tmp_path / "ops-hit.db", retry_backoff_seconds=0.0)
    try:
        service.enqueue(
            ops.PrepareRequest(
                job_id=job_id,
                attempt=1,
                resource_id="catalog:singlepoint",
                resource_kind=dc.ResourceKind.CACHE,
                resource_digest=f"{job_id}\x1f1\x1fsinglepoint",
                payload={"workflow": "singlepoint"},
            )
        )
        lease = service.lease_next()
        assert lease is not None
        result = executor.execute(lease)
    finally:
        service.close()

    assert calls["count"] >= 2
    assert result.prepared is False
    assert result.detail != "already_ready"
    assert result.availability is not dc.Availability.READY
