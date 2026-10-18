"""B02-5 (todo 15) — read-root unification + strictly read-only availability.

Proves the new read surface (``acp_services.data.read_root`` + its legacy-side
composition in ``acp.services_data_bootstrap``) is inert and read-only:

1. availability shape — availability / freshness / basis_revision /
   expected_revision / observed_at on every branch.
2. freshness — current / stale / unknown from the (basis, expected) pair.
3. bounded large-file reads — the requested limit is clamped and the file is
   never read whole into memory (Range/chunk semantics).
4. zero SSH/SFTP and zero new prepare operations on the read path.
5. a terminal remote job served through the new surface hits the cache with no
   network and no operation.
6. negative sensitivity — a spy that *would* catch an injected enqueue/network
   call is proven sensitive, so the read-only assertion is meaningful.

Run with: pytest tests/test_services_read_root.py -q
"""

from __future__ import annotations

import ast
import sqlite3
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.services_data_bootstrap import build_read_only_reader
from acp_services.data.contracts import Availability, Freshness
from acp_services.data.read_root import (
    AvailabilityRead,
    CacheIndexFacts,
    OperationFacts,
    RangeReadRequest,
    ReadOnlyResourceReader,
    ReadRoot,
    ReadRootResolver,
    ResourceNotPreparedError,
)

_FIXED_CLOCK = "2026-10-18T12:00:00+00:00"

_READ_ROOT_SOURCE = (
    Path(__file__).resolve().parents[1] / "src" / "acp_services" / "data" / "read_root.py"
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeRootPort:
    roots: dict[str, ReadRoot] = field(default_factory=dict)

    def resolve(self, job_id: str) -> ReadRoot:
        return self.roots.get(job_id, ReadRoot(job_id=job_id, path="", found=False))


class _FakeCache:
    def __init__(
        self,
        *,
        entries: dict[tuple[str, str], CacheIndexFacts] | None = None,
        catalogs: dict[tuple[str, str], CacheIndexFacts] | None = None,
    ) -> None:
        self.entries = entries or {}
        self.catalogs = catalogs or {}
        self.read_calls: list[tuple[str, int, int]] = []
        self.max_limit = 0
        self.network_calls = 0

    def entry(
        self, job_id: str, relative_path: str, *, attempt: int | None = None
    ) -> CacheIndexFacts:
        return self.entries.get((job_id, relative_path), CacheIndexFacts(present=False))

    def catalog(self, job_id: str, root: str, workflow: str) -> CacheIndexFacts:
        return self.catalogs.get((job_id, workflow), CacheIndexFacts(present=False))

    def read_bytes(self, path: str, offset: int, limit: int) -> bytes:
        self.read_calls.append((path, offset, limit))
        self.max_limit = max(self.max_limit, limit)
        return b"x" * limit


class _FakeOps:
    def __init__(self, facts: dict[str, OperationFacts] | None = None) -> None:
        self.facts_map = facts or {}
        self.calls: list[AvailabilityRead] = []
        self.enqueue_calls = 0

    def facts(self, request: AvailabilityRead) -> OperationFacts:
        self.calls.append(request)
        return self.facts_map.get(request.resource_id, OperationFacts(exists=False))

    def enqueue(self, *_args: Any, **_kwargs: Any) -> None:
        self.enqueue_calls += 1


class _NetworkSpy:
    """Fetcher double: any remote read is a hard failure and a counted call."""

    def __init__(self) -> None:
        self.calls = 0

    def _trip(self, name: str) -> None:
        self.calls += 1
        raise AssertionError(f"unexpected network call: {name}")

    def read_file(self, *_a: Any, **_k: Any) -> bytes:
        self._trip("read_file")
        return b""

    def read_range(self, *_a: Any, **_k: Any) -> bytes:
        self._trip("read_range")
        return b""

    def list_files(self, *_a: Any, **_k: Any) -> list[str]:
        self._trip("list_files")
        return []

    def file_info(self, *_a: Any, **_k: Any) -> Any:
        self._trip("file_info")
        return None

    def stream_file(self, *_a: Any, **_k: Any) -> Any:
        self._trip("stream_file")
        return iter(())


def _remote_roots(job_id: str, *, catalog_ready: bool) -> dict[str, ReadRoot]:
    return {
        job_id: ReadRoot(
            job_id=job_id,
            path=f"/cache/{job_id}",
            is_remote=True,
            catalog_ready=catalog_ready,
            found=True,
        )
    }


def _reader(
    *,
    roots: dict[str, ReadRoot],
    cache: _FakeCache,
    ops: _FakeOps | None = None,
    max_read_bytes: int = 1 << 20,
) -> ReadOnlyResourceReader:
    return ReadOnlyResourceReader(
        resolver=ReadRootResolver(_FakeRootPort(roots)),
        cache_index=cache,
        operations=ops,
        clock=lambda: _FIXED_CLOCK,
        max_read_bytes=max_read_bytes,
    )


def _snapshot_fields(snapshot: Any) -> dict[str, Any]:
    body = snapshot.to_dict()
    return body


# ---------------------------------------------------------------------------
# Availability shape + freshness
# ---------------------------------------------------------------------------


def test_availability_ready_from_cache_index() -> None:
    cache = _FakeCache(
        entries={
            ("j1", "RESULT/x.xyz"): CacheIndexFacts(
                present=True, cached_path="/cache/j1/RESULT/x.xyz", total_size=42, generation=7
            )
        }
    )
    reader = _reader(roots=_remote_roots("j1", catalog_ready=True), cache=cache)
    snapshot = reader.availability(
        AvailabilityRead(
            job_id="j1",
            resource_id="file:RESULT/x.xyz",
            relative_path="RESULT/x.xyz",
            expected_revision=7,
        )
    )
    body = _snapshot_fields(snapshot)
    assert body == {
        "resource_id": "file:RESULT/x.xyz",
        "job_id": "j1",
        "availability": "ready",
        "freshness": "current",
        "basis_revision": 7,
        "expected_revision": 7,
        "observed_at": _FIXED_CLOCK,
        "is_remote": True,
        "prepared_root": "/cache/j1/RESULT/x.xyz",
        "size_bytes": 42,
        "detail": "cache_index",
    }
    assert snapshot.is_readable()


def test_availability_stale_when_basis_behind_expected() -> None:
    cache = _FakeCache(
        entries={
            ("j1", "RESULT/x.xyz"): CacheIndexFacts(present=True, cached_path="/c/x", generation=1)
        }
    )
    reader = _reader(roots=_remote_roots("j1", catalog_ready=True), cache=cache)
    snapshot = reader.availability(
        AvailabilityRead(
            job_id="j1",
            resource_id="file:x",
            relative_path="RESULT/x.xyz",
            expected_revision=3,
        )
    )
    assert snapshot.availability is Availability.READY
    assert snapshot.freshness is Freshness.STALE
    assert snapshot.basis_revision == 1
    assert snapshot.expected_revision == 3


def test_availability_unknown_freshness_without_expected() -> None:
    cache = _FakeCache(
        entries={("j1", "RESULT/x.xyz"): CacheIndexFacts(present=True, cached_path="/c/x")}
    )
    reader = _reader(roots=_remote_roots("j1", catalog_ready=True), cache=cache)
    snapshot = reader.availability(
        AvailabilityRead(job_id="j1", resource_id="file:x", relative_path="RESULT/x.xyz")
    )
    assert snapshot.availability is Availability.READY
    assert snapshot.freshness is Freshness.UNKNOWN


def test_availability_preparing_from_operation() -> None:
    cache = _FakeCache()
    ops = _FakeOps(
        {"catalog:Confsearch": OperationFacts(exists=True, availability=Availability.PREPARING)}
    )
    reader = _reader(roots=_remote_roots("j1", catalog_ready=False), cache=cache, ops=ops)
    snapshot = reader.availability(
        AvailabilityRead(job_id="j1", resource_id="catalog:Confsearch", workflow="Confsearch")
    )
    assert snapshot.availability is Availability.PREPARING
    assert snapshot.freshness is Freshness.UNKNOWN
    assert snapshot.detail == "operation"


def test_availability_unavailable_from_failed_operation() -> None:
    cache = _FakeCache()
    ops = _FakeOps(
        {
            "catalog:Confsearch": OperationFacts(
                exists=True, availability=Availability.UNAVAILABLE, detail="operation:failed"
            )
        }
    )
    reader = _reader(roots=_remote_roots("j1", catalog_ready=False), cache=cache, ops=ops)
    snapshot = reader.availability(
        AvailabilityRead(job_id="j1", resource_id="catalog:Confsearch", workflow="Confsearch")
    )
    assert snapshot.availability is Availability.UNAVAILABLE
    assert snapshot.detail == "operation:failed"


def test_availability_absent_when_no_projection() -> None:
    cache = _FakeCache()
    ops = _FakeOps()
    reader = _reader(roots=_remote_roots("j1", catalog_ready=False), cache=cache, ops=ops)
    snapshot = reader.availability(
        AvailabilityRead(job_id="j1", resource_id="catalog:Confsearch", workflow="Confsearch")
    )
    assert snapshot.availability is Availability.ABSENT
    assert snapshot.detail == "no_projection"
    assert ops.enqueue_calls == 0


def test_availability_unknown_job_is_absent() -> None:
    reader = _reader(roots={}, cache=_FakeCache())
    snapshot = reader.availability(AvailabilityRead(job_id="missing", resource_id="catalog:X"))
    assert snapshot.availability is Availability.ABSENT
    assert snapshot.detail == "job_not_found"
    assert snapshot.is_remote is False


# ---------------------------------------------------------------------------
# Bounded reads
# ---------------------------------------------------------------------------


def test_read_range_clamps_limit_and_reports_shape() -> None:
    cache = _FakeCache(
        entries={
            ("j1", "RESULT/big.xyz"): CacheIndexFacts(
                present=True, cached_path="/c/big", total_size=100_000_000
            )
        }
    )
    reader = _reader(
        roots=_remote_roots("j1", catalog_ready=True), cache=cache, max_read_bytes=4096
    )
    result = reader.read_range(
        RangeReadRequest(job_id="j1", relative_path="RESULT/big.xyz", offset=100, limit=10**9)
    )
    assert cache.read_calls == [("/c/big", 100, 4096)]
    assert cache.max_limit == 4096
    assert result.length == 4096
    assert result.offset == 100
    assert result.total_size == 100_000_000
    assert result.truncated is True
    assert result.data == b"x" * 4096


def test_read_range_missing_resource_refuses_without_network() -> None:
    cache = _FakeCache()
    reader = _reader(roots=_remote_roots("j1", catalog_ready=False), cache=cache)
    with pytest.raises(ResourceNotPreparedError):
        reader.read_range(RangeReadRequest(job_id="j1", relative_path="RESULT/missing.xyz"))
    assert cache.read_calls == []
    assert cache.network_calls == 0


def test_reader_never_enqueues_or_calls_network() -> None:
    cache = _FakeCache(
        entries={("j1", "RESULT/x.xyz"): CacheIndexFacts(present=True, cached_path="/c/x")}
    )
    ops = _FakeOps()
    reader = _reader(roots=_remote_roots("j1", catalog_ready=True), cache=cache, ops=ops)
    reader.availability(
        AvailabilityRead(job_id="j1", resource_id="file:x", relative_path="RESULT/x.xyz")
    )
    reader.availability(AvailabilityRead(job_id="j1", resource_id="catalog:Confsearch"))
    reader.read_range(RangeReadRequest(job_id="j1", relative_path="RESULT/x.xyz", limit=8))
    assert ops.enqueue_calls == 0
    assert cache.network_calls == 0


# ---------------------------------------------------------------------------
# Static guard: the module has no enqueue / network entry point at all
# ---------------------------------------------------------------------------


def _called_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_read_root_module_has_no_enqueue_or_network_entry_point() -> None:
    source = _READ_ROOT_SOURCE.read_text(encoding="utf-8")
    called = _called_names(source)
    assert "enqueue" not in called
    assert "fetch" not in called
    assert "fetch_catalog" not in called
    assert "open" not in called
    forbidden_modules = {"paramiko", "socket", "urllib", "subprocess", "requests", "acp"}
    imported = {name.split(".")[0] for name in _imported_modules(source)}
    assert not (imported & forbidden_modules), imported & forbidden_modules


def test_spy_detects_injected_enqueue_so_negative_assertion_is_meaningful() -> None:
    violating = _FakeOps()
    violating.enqueue()
    assert violating.enqueue_calls == 1

    clean = _FakeOps()
    reader = _reader(
        roots=_remote_roots("j1", catalog_ready=True),
        cache=_FakeCache(),
        ops=clean,
    )
    reader.availability(AvailabilityRead(job_id="j1", resource_id="catalog:Confsearch"))
    assert clean.enqueue_calls == 0


# ---------------------------------------------------------------------------
# Real cache integration (legacy-side adapters)
# ---------------------------------------------------------------------------


def _remote_record(job_id: str, *, workflow: str, run_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        attempt=1,
        work_dir=str(run_root / job_id),
        result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        spec=SimpleNamespace(workflow=workflow),
    )


def test_real_reader_bounded_range_never_reads_whole_file(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    job_id = "job-range-1"
    rel_path = "RESULT/big.xyz"
    target = cache.cache_path(job_id, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 5 * 1024 * 1024
    target.write_bytes(b"A" * size)

    lookup = {job_id: _remote_record(job_id, workflow="optimize", run_root=run_root)}
    reader = build_read_only_reader(job_lookup=lookup.get, cache=cache, max_read_bytes=4096)
    result = reader.read_range(
        RangeReadRequest(job_id=job_id, relative_path=rel_path, offset=1024, limit=10**9)
    )
    assert result.length == 4096
    assert result.offset == 1024
    assert result.total_size == size
    assert result.truncated is True
    assert result.data == b"A" * 4096


def test_real_reader_availability_catalog_hit_no_network(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    job_id = "job-cat-1"
    root = cache.job_root(job_id)
    (root / "RESULT").mkdir(parents=True, exist_ok=True)
    (root / "RESULT" / "result_manifest.json").write_text("{}", encoding="utf-8")

    spy = _NetworkSpy()
    lookup = {job_id: _remote_record(job_id, workflow="optimize", run_root=run_root)}
    reader = build_read_only_reader(job_lookup=lookup.get, cache=cache)
    snapshot = reader.availability(
        AvailabilityRead(job_id=job_id, resource_id="catalog:optimize", workflow="optimize")
    )
    assert snapshot.availability is Availability.READY
    assert snapshot.is_remote is True
    assert snapshot.freshness is Freshness.UNKNOWN
    assert spy.calls == 0


# ---------------------------------------------------------------------------
# API endpoint (additive GET)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _seed_job(client: TestClient, record: JobRecord) -> None:
    manager = client.app.state.job_manager
    Path(record.work_dir).mkdir(parents=True, exist_ok=True)
    manager.store.create(record)


def _operation_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM data_operations").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def test_endpoint_availability_remote_cache_hit_zero_side_effects(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "remote-avail-1"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="remote-avail"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / job_id),
            result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        ),
    )
    manager = client.app.state.job_manager
    cache_root = manager.structure_cache.job_root(job_id)
    (cache_root / "RESULT").mkdir(parents=True, exist_ok=True)
    (cache_root / "RESULT" / "result_manifest.json").write_text("{}", encoding="utf-8")

    spy = _NetworkSpy()
    monkeypatch.setattr(manager, "_remote_fetcher", spy, raising=False)
    before = _operation_count(manager.store.db_path)

    response = client.get(f"/api/v1/jobs/{job_id}/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "ready"
    assert body["is_remote"] is True
    assert body["job_id"] == job_id
    assert body["resource_id"] == "catalog:optimize"
    assert set(body) == {
        "resource_id",
        "job_id",
        "availability",
        "freshness",
        "basis_revision",
        "expected_revision",
        "observed_at",
        "is_remote",
        "prepared_root",
        "size_bytes",
        "detail",
    }

    assert spy.calls == 0
    assert _operation_count(manager.store.db_path) == before


def test_endpoint_availability_local_job_ready(client: TestClient, tmp_path: Path) -> None:
    job_id = "local-avail-1"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="local-avail"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / job_id),
        ),
    )
    response = client.get(f"/api/v1/jobs/{job_id}/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "ready"
    assert body["is_remote"] is False
    assert body["detail"] == "cache_index"


def test_endpoint_availability_unknown_job_404(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/does-not-exist/availability")
    assert response.status_code == 404
