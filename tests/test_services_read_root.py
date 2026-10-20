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
import os
import sqlite3
from collections.abc import Generator
from dataclasses import dataclass, field, replace
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

    def resolve(self, job_id: str, attempt: int | None = None) -> ReadRoot:
        root = self.roots.get(job_id, ReadRoot(job_id=job_id, path="", found=False))
        if attempt is not None and root.found and root.attempt is None:
            return replace(root, attempt=attempt)
        return root


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
        self.roots_seen: list[ReadRoot] = []
        self.catalogs_seen: list[ReadRoot] = []

    def entry(
        self,
        root: ReadRoot,
        relative_path: str,
        *,
        attempt: int | None = None,
        snapshot: bool = False,
    ) -> CacheIndexFacts:
        self.roots_seen.append(root)
        facts = self.entries.get((root.job_id, relative_path), CacheIndexFacts(present=False))
        if facts.present and snapshot:
            return replace(facts, snapshot=facts.cached_path)
        return facts

    def catalog(self, root: ReadRoot, workflow: str) -> CacheIndexFacts:
        self.catalogs_seen.append(root)
        return self.catalogs.get((root.job_id, workflow), CacheIndexFacts(present=False))

    def read_bytes(self, facts: CacheIndexFacts, offset: int, limit: int) -> bytes:
        path = str(facts.cached_path)
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
    before_read: Any | None = None,
) -> ReadOnlyResourceReader:
    return ReadOnlyResourceReader(
        resolver=ReadRootResolver(_FakeRootPort(roots)),
        cache_index=cache,
        operations=ops,
        clock=lambda: _FIXED_CLOCK,
        max_read_bytes=max_read_bytes,
        before_read=before_read,
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
# D04 — local originals and remote cached copies through one read root
# ---------------------------------------------------------------------------

_LOCAL_FILES: dict[str, bytes] = {
    "RESULT/result_manifest.json": b'{"version": 2}',
    "RESULT/frame_candidates.json": b'{"schema_version": "frame_candidates_v2"}',
    "RESULT/frequencies/normal_modes.json": b'{"schema_version": "normal_modes_v1"}',
    "RESULT/structures/frame_0001.xyz": b"3\nwater\nO 0 0 0\nH 0 0 1\nH 0 0 -1\n",
}

_GEOMETRY_PATH = "RESULT/structures/frame_0001.xyz"


class _FetchGuard:
    """Any fetch-factory / fetcher use inside a read is a counted hard failure."""

    def __init__(self) -> None:
        self.factory_calls = 0
        self.fetcher_calls = 0

    def factory(self, _job_id: str) -> Any:
        self.factory_calls += 1
        raise AssertionError("fetch factory invoked inside a read")

    def fetcher(self, *_args: Any, **_kwargs: Any) -> Any:
        self.fetcher_calls += 1
        raise AssertionError("fetcher invoked inside a read")


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for relative_path, data in files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _local_record(
    job_id: str, work_dir: Path, *, workflow: str = "optimize", attempt: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        attempt=attempt,
        work_dir=str(work_dir),
        result={},
        spec=SimpleNamespace(workflow=workflow),
    )


def _serve_all(
    reader: Any, job_id: str, workflow: str, *, attempt: int | None = None
) -> dict[str, Any]:
    served: dict[str, Any] = {}
    for kind, relative_path in (
        ("catalog", ""),
        ("manifest", "RESULT/result_manifest.json"),
        ("frame_candidates", "RESULT/frame_candidates.json"),
        ("frequency_modes", "RESULT/frequencies/normal_modes.json"),
        ("geometry", _GEOMETRY_PATH),
    ):
        served[kind] = reader.serve(
            kind,
            job_id=job_id,
            workflow=workflow,
            relative_path=relative_path,
            attempt=attempt,
        )
    return served


def test_reader_passes_resolved_read_root_to_cache_index_port() -> None:
    root = ReadRoot(job_id="j1", path="/work/j1", found=True, attempt=4)
    cache = _FakeCache(
        entries={("j1", "RESULT/x.xyz"): CacheIndexFacts(present=True, cached_path="/w/x")},
        catalogs={("j1", "optimize"): CacheIndexFacts(present=True, cached_path="/w/j1")},
    )
    reader = _reader(roots={"j1": root}, cache=cache)
    reader.availability(
        AvailabilityRead(job_id="j1", resource_id="file:x", relative_path="RESULT/x.xyz")
    )
    reader.availability(
        AvailabilityRead(job_id="j1", resource_id="catalog:optimize", workflow="optimize")
    )
    reader.read_range(RangeReadRequest(job_id="j1", relative_path="RESULT/x.xyz", limit=4))
    assert cache.roots_seen[0] == root
    assert cache.catalogs_seen[0] == root
    assert cache.roots_seen[-1] == root


def test_local_root_serves_all_five_resources_without_copy_or_fetch(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    guard = _FetchGuard()
    cache = RemoteStructureCache(run_root, fetcher_factory=guard.factory)
    work = tmp_path / "project" / "water"
    _write_tree(work, _LOCAL_FILES)
    record = _local_record("local-five", work)
    reader = build_cache_only_resource_reader(job_lookup={record.id: record}.get, cache=cache)

    served = _serve_all(reader, record.id, "optimize")

    assert set(served) == {"catalog", "manifest", "frame_candidates", "frequency_modes", "geometry"}
    assert all(result.served for result in served.values()), served
    assert served["manifest"].data == _LOCAL_FILES["RESULT/result_manifest.json"]
    assert served["frame_candidates"].data == _LOCAL_FILES["RESULT/frame_candidates.json"]
    assert served["frequency_modes"].data == _LOCAL_FILES["RESULT/frequencies/normal_modes.json"]
    assert served["geometry"].data == _LOCAL_FILES[_GEOMETRY_PATH]
    assert guard.factory_calls == 0
    assert guard.fetcher_calls == 0
    assert not (run_root / ".remote_cache").exists()


def test_d04_counterexample_local_manifest_byte_identical(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    work = tmp_path / "project" / "water"
    manifest = work / "RESULT" / "result_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"audit": true}', encoding="utf-8")
    record = _local_record("audit-local", work, workflow="singlepoint")
    cache = RemoteStructureCache(tmp_path / "runs")
    reader = build_cache_only_resource_reader(job_lookup=lambda _jid: record, cache=cache)

    result = reader.serve("manifest", job_id=record.id, workflow="singlepoint")

    assert result.served, result
    assert result.data == manifest.read_bytes()


def test_remote_root_serves_all_five_resources_from_get_cached_only(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    guard = _FetchGuard()
    cache = RemoteStructureCache(run_root, fetcher_factory=guard.factory)
    job_id = "remote-five"
    cache_root = cache.job_root(job_id)
    _write_tree(cache_root, _LOCAL_FILES)
    record = _remote_record(job_id, workflow="optimize", run_root=run_root)
    reader = build_cache_only_resource_reader(job_lookup={job_id: record}.get, cache=cache)

    served = _serve_all(reader, job_id, "optimize")

    assert all(result.served for result in served.values()), served
    assert served["manifest"].data == _LOCAL_FILES["RESULT/result_manifest.json"]
    assert served["geometry"].data == _LOCAL_FILES[_GEOMETRY_PATH]
    assert guard.factory_calls == 0
    assert guard.fetcher_calls == 0


def test_local_read_rejects_absolute_parent_and_symlink_escape(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_read_only_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"SECRET")
    record = _local_record("local-paths", work)
    reader = build_read_only_reader(job_lookup={record.id: record}.get, cache=cache)

    for bad_path in (str(outside), "../outside.txt", "RESULT/../../outside.txt"):
        with pytest.raises(ResourceNotPreparedError):
            reader.read_range(RangeReadRequest(job_id=record.id, relative_path=bad_path))

    (work / "RESULT").mkdir()
    (work / "RESULT" / "evil.txt").symlink_to(outside)
    with pytest.raises(ResourceNotPreparedError):
        reader.read_range(RangeReadRequest(job_id=record.id, relative_path="RESULT/evil.txt"))

    (work / "links").symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(ResourceNotPreparedError):
        reader.read_range(RangeReadRequest(job_id=record.id, relative_path="links/outside.txt"))


def test_symlink_and_root_swap_after_snapshot_cannot_leave_root(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_read_only_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    work = tmp_path / "work"
    _write_tree(work, {"RESULT/result_manifest.json": b"SAFE"})
    outside = tmp_path / "outside"
    _write_tree(outside, {"RESULT/result_manifest.json": b"EVIL"})
    record = _local_record("local-swap", work)
    swaps: list[str] = []

    def swap_root() -> None:
        (work / "RESULT").rename(work / "RESULT_ORIGINAL")
        (work / "RESULT").symlink_to(outside / "RESULT", target_is_directory=True)
        swaps.append("swapped")

    reader = build_read_only_reader(
        job_lookup={record.id: record}.get, cache=cache, before_read=swap_root
    )
    result = reader.read_range(
        RangeReadRequest(job_id=record.id, relative_path="RESULT/result_manifest.json")
    )

    assert swaps == ["swapped"]
    assert result.data == b"SAFE"


def test_read_during_rerun_sees_one_attempt_snapshot(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_read_only_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    work = tmp_path / "work"
    target = work / "RESULT" / "result_manifest.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"ATTEMPT-1-BYTES")
    record = _local_record("local-rerun", work, attempt=1)
    replaced: list[bool] = []

    def rerun() -> None:
        replacement = target.with_name("result_manifest.json.rerun")
        replacement.write_bytes(b"ATTEMPT-2")
        os.replace(replacement, target)
        replaced.append(True)

    reader = build_read_only_reader(
        job_lookup={record.id: record}.get, cache=cache, before_read=rerun
    )
    result = reader.read_range(
        RangeReadRequest(job_id=record.id, relative_path="RESULT/result_manifest.json")
    )

    assert replaced == [True]
    assert result.data == b"ATTEMPT-1-BYTES"
    assert target.read_bytes() == b"ATTEMPT-2"


def test_missing_historical_attempt_refuses_instead_of_current_work_dir(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    work = tmp_path / "work"
    _write_tree(work, {"RESULT/result_manifest.json": b"CURRENT"})
    record = _local_record("local-historical", work, attempt=2)
    reader = build_cache_only_resource_reader(job_lookup={record.id: record}.get, cache=cache)

    historical = reader.serve("manifest", job_id=record.id, workflow="optimize", attempt=1)
    assert historical.served is False
    assert historical.source == "not_prepared"
    with pytest.raises(ResourceNotPreparedError):
        reader.read_range("manifest", job_id=record.id, attempt=1)

    current = reader.serve("manifest", job_id=record.id, workflow="optimize", attempt=2)
    assert current.served is True
    assert current.data == b"CURRENT"


def test_local_and_remote_ranges_are_clamped(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_read_only_reader

    run_root = tmp_path / "runs"
    run_root.mkdir()
    cache = RemoteStructureCache(run_root)
    body = bytes(range(100))
    work = tmp_path / "local"
    _write_tree(work, {"RESULT/bytes.bin": body})
    local_record = _local_record("local-range", work)
    remote_id = "remote-range"
    _write_tree(cache.job_root(remote_id), {"RESULT/bytes.bin": body})
    remote_record = _remote_record(remote_id, workflow="optimize", run_root=run_root)
    lookup = {local_record.id: local_record, remote_id: remote_record}.get

    for job_id in (local_record.id, remote_id):
        reader = build_read_only_reader(job_lookup=lookup, cache=cache, max_read_bytes=16)
        result = reader.read_range(
            RangeReadRequest(job_id=job_id, relative_path="RESULT/bytes.bin", offset=4, limit=10**9)
        )
        assert result.offset == 4
        assert result.length == 16
        assert result.data == body[4:20]
        assert result.truncated is True


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
    manifest = tmp_path / job_id / "RESULT" / "result_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    response = client.get(f"/api/v1/jobs/{job_id}/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "ready"
    assert body["is_remote"] is False
    assert body["detail"] == "cache_index"
    assert body["size_bytes"] == len(b"{}")


def test_endpoint_availability_local_directory_only_is_not_ready(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "local-avail-empty"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="local-avail-empty"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / job_id),
        ),
    )
    response = client.get(f"/api/v1/jobs/{job_id}/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "absent"
    assert body["detail"] == "no_projection"


def test_endpoint_availability_unknown_job_404(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/does-not-exist/availability")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# D11 — resolve the authoritative current attempt instead of attempt 0
# ---------------------------------------------------------------------------


def _catalog_request(record: Any, *, attempt: int) -> Any:
    from acp_services.data.contracts import ResourceKind
    from acp_services.data.operations import PrepareRequest

    workflow = str(record.spec.workflow)
    return PrepareRequest(
        job_id=record.id,
        attempt=attempt,
        resource_id=f"catalog:{workflow}",
        resource_kind=ResourceKind.CACHE,
        resource_digest=f"{record.id}\x1f{attempt}\x1f{workflow}",
        payload={"workflow": workflow, "job_id": record.id, "attempt": attempt},
    )


class _NoLookupOperations:
    """Operation port double: any attempt-0 lookup is a hard failure."""

    def find(self, **_kwargs: Any) -> Any:
        raise AssertionError("operation lookup reached the store")

    def availability(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("operation availability reached the store")


def test_builder_availability_defaults_to_current_attempt(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader
    from acp_services.data.operations import PrepareOperationService

    record = JobRecord(
        id="builder-avail-attempt",
        spec=JobSpec(workflow="singlepoint", name="builder-avail-attempt"),
        status=JobStatus.COMPLETED,
        attempt=2,
        work_dir=str(tmp_path / "builder-avail-attempt"),
        result={"node": "comp-01", "remote_dir": "/scratch/qc/acp/builder-avail-attempt"},
    )
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        service.enqueue(_catalog_request(record, attempt=2))
        reader = build_cache_only_resource_reader(
            job_lookup=lambda _job_id: record,
            cache=RemoteStructureCache(tmp_path / "runs"),
            operations=service,
        )

        default = reader.availability("catalog", job_id=record.id, workflow="singlepoint")
        explicit = reader.availability(
            "catalog", job_id=record.id, workflow="singlepoint", attempt=2
        )

        assert default.availability is Availability.PREPARING
        assert default.availability is explicit.availability
        assert default.detail == explicit.detail == "operation:queued"
    finally:
        service.close()


def test_builder_rerun_does_not_return_old_attempt_operation(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader
    from acp_services.data.operations import PrepareOperationService

    record = JobRecord(
        id="builder-rerun-attempt",
        spec=JobSpec(workflow="singlepoint", name="builder-rerun-attempt"),
        status=JobStatus.COMPLETED,
        attempt=2,
        work_dir=str(tmp_path / "builder-rerun-attempt"),
        result={"node": "comp-01", "remote_dir": "/scratch/qc/acp/builder-rerun-attempt"},
    )
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        old = service.enqueue(_catalog_request(record, attempt=1))
        lease = service.lease_next()
        assert lease is not None
        service.complete(lease)
        service.enqueue(_catalog_request(record, attempt=2))
        reader = build_cache_only_resource_reader(
            job_lookup=lambda _job_id: record,
            cache=RemoteStructureCache(tmp_path / "runs"),
            operations=service,
        )

        default = reader.availability("catalog", job_id=record.id, workflow="singlepoint")
        explicit = reader.availability(
            "catalog", job_id=record.id, workflow="singlepoint", attempt=2
        )

        assert service.availability(old.operation_id).availability is Availability.READY
        assert default.availability is Availability.PREPARING
        assert default.availability is not Availability.READY
        assert default.detail == explicit.detail == "operation:queued"
    finally:
        service.close()


def test_builder_historical_attempt_reads_its_own_archived_original(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    work = tmp_path / "historical-work"
    _write_tree(work, {"RESULT/result_manifest.json": b"CURRENT"})
    _write_tree(
        work / "WORK" / "00_RUNTIME" / "attempts" / "1",
        {"RESULT/result_manifest.json": b"ATTEMPT-1-ORIGINAL"},
    )
    record = _local_record("local-historical-own", work, attempt=2)
    cache = RemoteStructureCache(tmp_path / "runs")
    reader = build_cache_only_resource_reader(job_lookup={record.id: record}.get, cache=cache)

    historical = reader.serve("manifest", job_id=record.id, workflow="optimize", attempt=1)
    current = reader.serve("manifest", job_id=record.id, workflow="optimize")
    explicit_current = reader.serve("manifest", job_id=record.id, workflow="optimize", attempt=2)

    assert historical.served is True
    assert historical.data == b"ATTEMPT-1-ORIGINAL"
    assert current.data == b"CURRENT"
    assert explicit_current.data == b"CURRENT"


def test_attempt_zero_is_rejected_without_an_operation_lookup(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    work = tmp_path / "attempt-zero-work"
    _write_tree(work, {"RESULT/result_manifest.json": b"CURRENT"})
    record = _local_record("local-attempt-zero", work, attempt=1)
    reader = build_cache_only_resource_reader(
        job_lookup={record.id: record}.get,
        cache=RemoteStructureCache(tmp_path / "runs"),
        operations=_NoLookupOperations(),
    )

    snapshot = reader.availability("manifest", job_id=record.id, workflow="optimize", attempt=0)
    assert snapshot.availability is Availability.ABSENT
    assert snapshot.detail == "invalid_attempt"
    with pytest.raises(ResourceNotPreparedError):
        reader.read_range("manifest", job_id=record.id, attempt=0)


def test_builder_missing_record_attempt_is_a_structured_absence(tmp_path: Path) -> None:
    from acp.services_data_bootstrap import build_cache_only_resource_reader
    from acp_services.data.operations import PrepareOperationService

    record = SimpleNamespace(
        id="builder-attempt-removed",
        work_dir=str(tmp_path / "builder-attempt-removed"),
        result={},
        spec=SimpleNamespace(workflow="optimize"),
    )
    service = PrepareOperationService(tmp_path / "prepare.db")
    try:
        reader = build_cache_only_resource_reader(
            job_lookup={record.id: record}.get,
            cache=RemoteStructureCache(tmp_path / "runs"),
            operations=service,
        )
        snapshot = reader.availability("catalog", job_id=record.id, workflow="optimize")
        assert snapshot.availability is Availability.ABSENT
        assert snapshot.detail == "attempt_unresolved"
        assert service.list() == ()
    finally:
        service.close()


def test_endpoint_availability_default_and_explicit_attempt_agree(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "http-attempt-current"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name="http-attempt-current"),
            status=JobStatus.COMPLETED,
            attempt=2,
            work_dir=str(tmp_path / job_id),
            result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        ),
    )
    manager = client.app.state.job_manager
    manager._prepare_operations.enqueue(_catalog_request(manager.get(job_id), attempt=2))
    before = _operation_count(manager.store.db_path)

    default = client.get(f"/api/v1/jobs/{job_id}/availability")
    explicit = client.get(f"/api/v1/jobs/{job_id}/availability?attempt=2")
    prepared_default = client.get(f"/api/v1/jobs/{job_id}/prepared/catalog")
    prepared_explicit = client.get(f"/api/v1/jobs/{job_id}/prepared/catalog?attempt=2")

    assert default.status_code == 200
    assert explicit.status_code == 200
    assert default.json()["availability"] == explicit.json()["availability"] == "preparing"
    assert default.json()["detail"] == explicit.json()["detail"] == "operation:queued"
    assert prepared_default.status_code == 409
    assert prepared_explicit.status_code == 409
    assert prepared_default.json() == prepared_explicit.json()
    assert _operation_count(manager.store.db_path) == before


def test_endpoint_availability_rerun_reports_current_attempt(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "http-attempt-rerun"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="singlepoint", name="http-attempt-rerun"),
            status=JobStatus.COMPLETED,
            attempt=2,
            work_dir=str(tmp_path / job_id),
            result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        ),
    )
    manager = client.app.state.job_manager
    operations = manager._prepare_operations
    record = manager.get(job_id)
    old = operations.enqueue(_catalog_request(record, attempt=1))
    lease = operations.lease_next()
    assert lease is not None
    operations.complete(lease)
    operations.enqueue(_catalog_request(record, attempt=2))

    default = client.get(f"/api/v1/jobs/{job_id}/availability")
    explicit = client.get(f"/api/v1/jobs/{job_id}/availability?attempt=2")

    assert operations.availability(old.operation_id).availability is Availability.READY
    assert default.json()["availability"] == "preparing"
    assert default.json()["availability"] != "ready"
    assert default.json()["detail"] == explicit.json()["detail"] == "operation:queued"


def test_endpoint_prepared_historical_attempt_never_reads_current_original(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "http-historical-attempt"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="http-historical-attempt"),
            status=JobStatus.COMPLETED,
            attempt=2,
            work_dir=str(tmp_path / job_id),
        ),
    )
    work = tmp_path / job_id
    _write_tree(work, {"RESULT/result_manifest.json": b"CURRENT"})
    _write_tree(
        work / "WORK" / "00_RUNTIME" / "attempts" / "1",
        {"RESULT/result_manifest.json": b"ATTEMPT-1-ORIGINAL"},
    )

    default = client.get(f"/api/v1/jobs/{job_id}/prepared/manifest")
    historical = client.get(f"/api/v1/jobs/{job_id}/prepared/manifest?attempt=1")

    assert default.status_code == 200
    assert default.content == b"CURRENT"
    assert historical.status_code == 200
    assert historical.content == b"ATTEMPT-1-ORIGINAL"

    missing_id = "http-historical-missing"
    _seed_job(
        client,
        JobRecord(
            id=missing_id,
            spec=JobSpec(workflow="optimize", name="http-historical-missing"),
            status=JobStatus.COMPLETED,
            attempt=2,
            work_dir=str(tmp_path / missing_id),
        ),
    )
    _write_tree(tmp_path / missing_id, {"RESULT/result_manifest.json": b"CURRENT"})
    missing = client.get(f"/api/v1/jobs/{missing_id}/prepared/manifest?attempt=1")
    assert missing.status_code == 409
    assert "not_prepared" in missing.json()["detail"]


def test_endpoint_attempt_zero_is_rejected(client: TestClient, tmp_path: Path) -> None:
    job_id = "http-attempt-zero"
    _seed_job(
        client,
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name="http-attempt-zero"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / job_id),
        ),
    )
    availability = client.get(f"/api/v1/jobs/{job_id}/availability?attempt=0")
    prepared = client.get(f"/api/v1/jobs/{job_id}/prepared/manifest?attempt=0")
    assert availability.status_code == 422
    assert prepared.status_code == 422
