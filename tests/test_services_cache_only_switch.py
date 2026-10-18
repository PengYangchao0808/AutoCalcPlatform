"""B02-6 (todo 16) — per-resource cache-only switch + old-client compat channel.

Proves the compatibility-first per-resource rollout:

1. the switch registry covers exactly the five resources and every new path
   keeps its ``legacy_get`` fallback (the compat channel);
2. each per-resource compat adapter maps a resource id / default relative path
   correctly;
3. serving a resource from the prepared cache index is cache-only — zero
   network and zero new prepare operations — for every resource;
4. F0 equivalence: the original client flow still yields *real* results on a
   cold cache (not just a matching status code) — catalog, manifest,
   frame_candidates, frequency_modes and geometry;
5. a per-resource fallback drill: a cold cache reads ``not_prepared`` through
   the new path, and the retained legacy fetch/fallback restores the real
   result;
6. negative control: a switch that dropped the ``legacy_get`` fallback (the
   unsafe branch) must FAIL the F0+S1 compatibility verdict.

Run with: pytest tests/test_services_cache_only_switch.py -q
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.results.frame_candidate_store import write_authority
from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.services_data_bootstrap import build_cache_only_resource_reader
from acp_services.data.resource_read import (
    ALL_RESOURCE_KINDS,
    CACHE_ONLY_SWITCH,
    DEFAULT_ENABLED_KINDS,
    RESOURCE_ADAPTERS,
    RESOURCE_CATALOG,
    RESOURCE_FRAME_CANDIDATES,
    RESOURCE_FREQUENCY_MODES,
    RESOURCE_GEOMETRY,
    RESOURCE_MANIFEST,
    ResourceServeResult,
    ResourceSwitch,
)
from tests.test_acp_api_structure_viewer import (
    _make_normal_modes_json,
    _seed_job,
    _write_confsearch_manifest_with_xyz,
    _write_normal_modes,
)
from tests.test_acp_api_v1 import make_client

_XYZ = "3\ncomment\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"

#: The canonical prepared relative paths exercised per resource.
_RESOURCE_PATHS = {
    RESOURCE_MANIFEST: "RESULT/result_manifest.json",
    RESOURCE_FRAME_CANDIDATES: "RESULT/frame_candidates.json",
    RESOURCE_FREQUENCY_MODES: "RESULT/frequencies/normal_modes.json",
    RESOURCE_GEOMETRY: "RESULT/structures/frame_0001.xyz",
}


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _CannedFetcher:
    """Serve canned remote bytes by relative path; record every call."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.calls: list[str] = []

    def read_file(self, _record: Any, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path in self.files:
            return self.files[rel_path]
        raise FileNotFoundError(rel_path)


def _remote_record(job_id: str, *, workflow: str, run_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        attempt=1,
        work_dir=str(run_root / job_id),
        result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
        spec=SimpleNamespace(workflow=workflow),
    )


def _prepared_reader(
    tmp_path: Path, *, files: dict[str, bytes], workflow: str = "optimize"
) -> tuple[Any, RemoteStructureCache, _CannedFetcher]:
    run_root = tmp_path / "runs"
    run_root.mkdir(exist_ok=True)
    fetcher = _CannedFetcher(files)
    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: fetcher)
    job_id = "remote-switch-1"
    for rel_path, data in files.items():
        target = cache.cache_path(job_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    lookup = {job_id: _remote_record(job_id, workflow=workflow, run_root=run_root)}
    reader = build_cache_only_resource_reader(job_lookup=lookup.get, cache=cache)
    return reader, cache, fetcher


# ---------------------------------------------------------------------------
# Switch registry invariants + F0+S1 verdict
# ---------------------------------------------------------------------------


def test_switch_registry_covers_all_five_resources() -> None:
    assert set(CACHE_ONLY_SWITCH) == set(ALL_RESOURCE_KINDS)
    assert set(RESOURCE_ADAPTERS) == set(ALL_RESOURCE_KINDS)
    for kind, row in CACHE_ONLY_SWITCH.items():
        assert row.new_path in {"read_only_availability", "prepared_bytes"}
        assert row.fallback == "legacy_get", kind
        assert row.note


def test_every_resource_enabled_with_legacy_fallback() -> None:
    assert DEFAULT_ENABLED_KINDS == frozenset(ALL_RESOURCE_KINDS)
    assert all(CACHE_ONLY_SWITCH[kind].enabled for kind in ALL_RESOURCE_KINDS)


def _f0_compat_verdict(serve_result: ResourceServeResult, switch_row: ResourceSwitch) -> bool:
    """F0+S1 verdict: old client is served iff new path served OR fallback kept."""
    fallback_available = switch_row.enabled and switch_row.fallback == "legacy_get"
    return serve_result.served or fallback_available


@pytest.mark.parametrize("kind", ALL_RESOURCE_KINDS)
def test_f0_s1_compat_holds_on_cold_cache_via_fallback(kind: str) -> None:
    cold = ResourceServeResult(kind=kind, served=False, source="not_prepared")
    assert _f0_compat_verdict(cold, CACHE_ONLY_SWITCH[kind]) is True


def test_negative_control_switch_without_fallback_fails_f0_s1_compat() -> None:
    unsafe = ResourceSwitch(
        kind=RESOURCE_MANIFEST,
        new_path="prepared_bytes",
        fallback="none",
        enabled=True,
    )
    cold = ResourceServeResult(kind=RESOURCE_MANIFEST, served=False, source="not_prepared")
    assert _f0_compat_verdict(cold, unsafe) is False


# ---------------------------------------------------------------------------
# Per-resource adapter mapping
# ---------------------------------------------------------------------------


def test_adapters_map_resource_ids_and_default_paths() -> None:
    catalog = RESOURCE_ADAPTERS[RESOURCE_CATALOG]
    assert catalog.is_catalog() is True
    assert catalog.resource_id("Confsearch") == "catalog:Confsearch"
    assert catalog.resolve_relative_path() == ""

    assert (
        RESOURCE_ADAPTERS[RESOURCE_MANIFEST].resolve_relative_path()
        == "RESULT/result_manifest.json"
    )
    assert (
        RESOURCE_ADAPTERS[RESOURCE_FRAME_CANDIDATES].resolve_relative_path()
        == "RESULT/frame_candidates.json"
    )
    assert (
        RESOURCE_ADAPTERS[RESOURCE_FREQUENCY_MODES].resolve_relative_path()
        == "RESULT/frequencies/normal_modes.json"
    )
    geometry = RESOURCE_ADAPTERS[RESOURCE_GEOMETRY]
    assert geometry.resolve_relative_path("RESULT/x.xyz") == "RESULT/x.xyz"


def test_unknown_resource_kind_is_rejected(tmp_path: Path) -> None:
    from acp_services.data.resource_read import ResourceUnknownError

    reader, _cache, _fetcher = _prepared_reader(tmp_path, files={})
    with pytest.raises(ResourceUnknownError):
        reader.adapter("bogus")


# ---------------------------------------------------------------------------
# Cache-only serve for every resource + zero network / zero operations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [RESOURCE_MANIFEST, RESOURCE_FRAME_CANDIDATES, RESOURCE_FREQUENCY_MODES, RESOURCE_GEOMETRY],
)
def test_serve_file_resource_reads_prepared_bytes(tmp_path: Path, kind: str) -> None:
    body = b'{"kind": "' + kind.encode() + b'"}'
    reader, _cache, fetcher = _prepared_reader(tmp_path, files={_RESOURCE_PATHS[kind]: body})
    result = reader.serve(
        kind,
        job_id="remote-switch-1",
        workflow="optimize",
        relative_path=_RESOURCE_PATHS[kind],
    )
    assert result.served is True
    assert result.source == "cache_only"
    assert result.data == body
    assert fetcher.calls == []


def test_serve_catalog_reports_ready_from_prepared_root(tmp_path: Path) -> None:
    reader, _cache, fetcher = _prepared_reader(
        tmp_path, files={"RESULT/result_manifest.json": b"{}"}
    )
    result = reader.serve(RESOURCE_CATALOG, job_id="remote-switch-1", workflow="optimize")
    assert result.served is True
    assert result.availability_value() == "ready"
    assert fetcher.calls == []


def test_cold_cache_serve_reports_not_prepared_without_network(tmp_path: Path) -> None:
    reader, _cache, fetcher = _prepared_reader(tmp_path, files={})
    for kind in ALL_RESOURCE_KINDS:
        result = reader.serve(kind, job_id="remote-switch-1", workflow="optimize")
        assert result.served is False
        assert result.source == "not_prepared"
    assert fetcher.calls == []


def test_disabled_resource_reports_disabled_and_does_not_serve(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    fetcher = _CannedFetcher({"RESULT/result_manifest.json": b"{}"})
    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: fetcher)
    job_id = "remote-switch-1"
    target = cache.cache_path(job_id, "RESULT/result_manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"{}")
    lookup = {job_id: _remote_record(job_id, workflow="optimize", run_root=run_root)}
    reader = build_cache_only_resource_reader(
        job_lookup=lookup.get, cache=cache, enabled=frozenset({RESOURCE_CATALOG})
    )
    result = reader.serve(RESOURCE_MANIFEST, job_id=job_id, workflow="optimize")
    assert result.served is False
    assert result.source == "disabled"
    assert result.reason == "resource_switch_disabled"


# ---------------------------------------------------------------------------
# Fallback drill per resource (remote cold cache → legacy fetch restores)
# ---------------------------------------------------------------------------


_CATALOG_FILES = {
    "RESULT/result_manifest.json": b'{"version": 2}',
    "RESULT/frame_candidates.json": b'{"schema_version": "frame_candidates_v2"}',
    "RESULT/frequencies/normal_modes.json": b'{"schema_version": "normal_modes_v1"}',
    "RESULT/structures/frame_0001.xyz": _XYZ.encode(),
}


@pytest.mark.parametrize(
    ("kind", "relative_path"),
    [
        (RESOURCE_CATALOG, ""),
        (RESOURCE_MANIFEST, _RESOURCE_PATHS[RESOURCE_MANIFEST]),
        (RESOURCE_FRAME_CANDIDATES, _RESOURCE_PATHS[RESOURCE_FRAME_CANDIDATES]),
        (RESOURCE_FREQUENCY_MODES, _RESOURCE_PATHS[RESOURCE_FREQUENCY_MODES]),
        (RESOURCE_GEOMETRY, _RESOURCE_PATHS[RESOURCE_GEOMETRY]),
    ],
)
def test_fallback_drill_cold_then_legacy_fetch_restores(
    tmp_path: Path, kind: str, relative_path: str
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    job_id = "remote-fallback-1"
    fetcher = _CannedFetcher(_CATALOG_FILES)
    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: fetcher)
    record = _remote_record(job_id, workflow="optimize", run_root=run_root)
    lookup = {job_id: record}
    reader = build_cache_only_resource_reader(job_lookup=lookup.get, cache=cache)

    cold = reader.serve(kind, job_id=job_id, workflow="optimize", relative_path=relative_path)
    assert cold.served is False
    assert fetcher.calls == []

    # The retained legacy fallback is what keeps F0 clients working.
    cache.fetch_catalog(record, "optimize")
    if relative_path:
        cache.fetch(record, relative_path)

    warm = reader.serve(kind, job_id=job_id, workflow="optimize", relative_path=relative_path)
    assert warm.served is True


# ---------------------------------------------------------------------------
# F0 equivalence via the original client flow (HTTP, cold cache)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    with make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


def _confsearch_job(client: TestClient, tmp_path: Path, job_id: str) -> Path:
    work_dir = _seed_job(client, tmp_path, job_id=job_id, workflow="Confsearch")
    _write_confsearch_manifest_with_xyz(work_dir)
    return work_dir


def test_f0_catalog_flow_yields_real_entries(client: TestClient, tmp_path: Path) -> None:
    _confsearch_job(client, tmp_path, "f0-catalog")
    body = client.get("/api/v1/jobs/f0-catalog/structure-viewer").json()
    assert body["availability"] == "ready"
    assert body["entries"], "F0 catalog flow must return real entries on a cold cache"


def test_f0_manifest_flow_yields_real_nodes(client: TestClient, tmp_path: Path) -> None:
    _confsearch_job(client, tmp_path, "f0-manifest")
    response = client.get("/api/v1/jobs/f0-manifest/energy-graph")
    assert response.status_code == 200
    assert response.json()["nodes"], "F0 manifest flow must return real nodes"


def test_f0_frame_candidates_flow_yields_real_candidates(
    client: TestClient, tmp_path: Path
) -> None:
    work_dir = _seed_job(client, tmp_path, job_id="f0-cand", workflow="optimize")
    write_authority(
        work_dir,
        {
            "schema_version": "frame_candidates_v2",
            "revision": 3,
            "candidates": [
                {
                    "candidate_id": "fc_1",
                    "view_type": "optimization",
                    "frame_index": 1,
                    "role": "TS",
                    "name": "cand",
                    "structure_path": "RESULT/structures/fc_1.xyz",
                    "saved_at": "2026-10-18T00:00:00Z",
                }
            ],
        },
    )
    body = client.get("/api/v1/jobs/f0-cand/frame-candidates").json()
    assert body["revision"] == 3
    assert [c["candidate_id"] for c in body["candidates"]] == ["fc_1"]


def test_f0_frequency_modes_flow_yields_real_modes(client: TestClient, tmp_path: Path) -> None:
    work_dir = _confsearch_job(client, tmp_path, "f0-freq")
    _write_normal_modes(work_dir, _make_normal_modes_json())
    catalog = client.get("/api/v1/jobs/f0-freq/structure-viewer").json()
    entry_id = catalog["default_entry_id"]
    body = client.get(f"/api/v1/jobs/f0-freq/structure-viewer/entries/{entry_id}/vibrations").json()
    assert body["available"] is True
    assert len(body["modes"]) == 3


def test_f0_geometry_flow_yields_real_xyz(client: TestClient, tmp_path: Path) -> None:
    _confsearch_job(client, tmp_path, "f0-geom")
    catalog = client.get("/api/v1/jobs/f0-geom/structure-viewer").json()
    entry_id = catalog["default_entry_id"]
    response = client.get(f"/api/v1/jobs/f0-geom/structure-viewer/entries/{entry_id}/geometry")
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert lines[0].strip() == "3"
    assert len(lines) == 5
    assert any(line.startswith("C ") for line in lines[2:])


# ---------------------------------------------------------------------------
# New read path (HTTP): zero network + zero new operations on a cold remote job
# ---------------------------------------------------------------------------


def _remote_job(client: TestClient, tmp_path: Path, job_id: str) -> None:
    manager = client.app.state.job_manager
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="optimize", name=job_id),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path / job_id),
        result={"node": "comp-01", "remote_dir": f"/scratch/qc/acp/{job_id}"},
    )
    manager.store.create(record)


def _operation_count(db_path: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM data_operations").fetchone()
        return int(row[0]) if row is not None else 0
    finally:
        conn.close()


def test_new_path_cold_cache_is_409_not_prepared_with_no_side_effects(
    client: TestClient, tmp_path: Path
) -> None:
    _remote_job(client, tmp_path, "newpath-cold")
    manager = client.app.state.job_manager
    before = _operation_count(manager.store.db_path)

    response = client.get("/api/v1/jobs/newpath-cold/prepared/manifest")
    assert response.status_code == 409
    assert "not_prepared" in response.json()["detail"]

    assert _operation_count(manager.store.db_path) == before

    availability = client.get("/api/v1/jobs/newpath-cold/availability?resource=frequency_modes")
    assert availability.status_code == 200
    body = availability.json()
    assert body["resource_id"] == "frequency_modes:optimize"
    assert body["availability"] == "absent"
    assert _operation_count(manager.store.db_path) == before


def test_new_path_serves_prepared_geometry_cache_only(client: TestClient, tmp_path: Path) -> None:
    _remote_job(client, tmp_path, "newpath-warm")
    manager = client.app.state.job_manager
    cache_root = manager.structure_cache.job_root("newpath-warm")
    target = cache_root / "RESULT" / "structures" / "frame_0001.xyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_XYZ, encoding="utf-8")
    before = _operation_count(manager.store.db_path)

    response = client.get(
        "/api/v1/jobs/newpath-warm/prepared/geometry?relative_path=RESULT/structures/frame_0001.xyz"
    )
    assert response.status_code == 200
    assert response.text.strip().startswith("3")
    assert _operation_count(manager.store.db_path) == before


def test_new_path_rejects_unknown_resource(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/whatever/prepared/bogus")
    assert response.status_code == 400


def test_availability_unknown_resource_is_400(client: TestClient, tmp_path: Path) -> None:
    _remote_job(client, tmp_path, "avail-bad")
    response = client.get("/api/v1/jobs/avail-bad/availability?resource=bogus")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Static guard: the switch module has no fetch/enqueue/network entry point
# ---------------------------------------------------------------------------


def test_resource_read_module_has_no_fetch_or_enqueue_entry_point() -> None:
    import ast

    source = (
        Path(__file__).resolve().parents[1] / "src" / "acp_services" / "data" / "resource_read.py"
    ).read_text(encoding="utf-8")
    called: set[str] = set()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "fetch" not in called
    assert "enqueue" not in called
    assert "fetch_catalog" not in called
    forbidden = {"paramiko", "socket", "urllib", "subprocess", "requests", "acp"}
    roots = {name.split(".")[0] for name in imported}
    assert not (roots & forbidden), roots & forbidden
