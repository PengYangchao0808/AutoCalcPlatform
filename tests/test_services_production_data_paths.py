"""Todo 81 (F4-B1 / F4-B2) — default prepared projections + production Data writes.

Two acceptance properties pinned with call/scan spies:

* **IS-5** — in the default environment the v2 task-view list path performs zero
  synchronous ``state.json`` scans/parses and an uncovered row shows jobs base
  facts; the legacy compat branch is reachable only when explicitly enabled.
* **IS-4** — the production frame-candidate, manifest, executor and
  structure-source write paths reach the todo-11 ``DataResourcePort`` instead of
  calling the legacy writer directly.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.storage.manifest import ProductKind, ResultManifest
from acp_services.data.contracts import CandidateSnapshot, ManifestPublication, SourceUpsert
from tests.test_acp_api_frame_candidates import _make_optimization_task
from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# IS-5 — default projection is authoritative (zero synchronous scans)
# ---------------------------------------------------------------------------


def _seed_running_job(
    client: TestClient, *, job_id: str, work_dir: Path, state: dict[str, Any]
) -> None:
    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    spec = JobSpec(
        workflow="Confsearch",
        name=job_id,
        molecule_name="CCO",
        task_name="opt",
        remark="r",
        project_id="",
        input={"source": "CCO"},
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
        project_id="",
        attempt=1,
        revision=0,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)


def _count_find_workflow_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    from acp.api import v1_routes

    calls = {"n": 0}
    real = v1_routes.find_workflow_state

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(v1_routes, "find_workflow_state", counting)
    return calls


def _row(client: TestClient, job_id: str) -> dict[str, Any]:
    resp = client.get("/api/v2/task-view?group_by=none")
    assert resp.status_code == 200, resp.text
    for group in resp.json()["groups"]:
        for job in group["jobs"]:
            if job["id"] == job_id:
                return job
    raise AssertionError(f"job {job_id} not present in task view")


def test_default_env_list_query_performs_zero_synchronous_scans(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    assert v2_routes._ENRICH_ACTIVE_ROWS_COMPAT is False, (
        "IS-5: default must be snapshot-authoritative"
    )

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="pdp-zero",
        work_dir=tmp_path / "zero",
        state={"stage_index": 3, "stage_total": 9, "stage_detail": "z"},
    )
    v2_routes.install_task_projection(manager, auto_refresh=False)

    calls = _count_find_workflow_state(monkeypatch)
    row = _row(client, "pdp-zero")

    assert row["status"] == "running"
    assert row["stage_index"] is None, "uncovered row shows jobs base facts"
    assert calls["n"] == 0, "default list path must not scan/parse state.json"


def test_compat_branch_available_only_when_explicitly_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.api import v2_routes

    manager = client.app.state.job_manager
    _seed_running_job(
        client,
        job_id="pdp-compat",
        work_dir=tmp_path / "compat",
        state={"stage_index": 4, "stage_total": 4, "stage_detail": "legacy"},
    )
    v2_routes.install_task_projection(manager, auto_refresh=False)
    monkeypatch.setattr(v2_routes, "_ENRICH_ACTIVE_ROWS_COMPAT", True)

    calls = _count_find_workflow_state(monkeypatch)
    row = _row(client, "pdp-compat")

    assert row["stage_index"] == 4
    assert row["stage_detail"] == "legacy"
    assert calls["n"] >= 1, "compat branch is the synchronous scan path"


# ---------------------------------------------------------------------------
# IS-4 — production writes reach the DataResourcePort
# ---------------------------------------------------------------------------


class _RecordingDataPort:
    """Wrap a real Data port, recording the write requests it receives."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.candidate_roots: list[str] = []
        self.manifest_roots: list[str] = []

    def save_candidates(self, request: Any) -> CandidateSnapshot:
        self.candidate_roots.append(request.task_root)
        return self._inner.save_candidates(request)

    def publish_manifest(self, request: Any) -> ManifestPublication:
        self.manifest_roots.append(request.task_root)
        return self._inner.publish_manifest(request)


def test_production_manifest_publisher_routes_result_through_data_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp import services_data_bootstrap
    from acp.calculations.result_publication import register_result_manifest
    from acp.services_publication_bootstrap import default_manifest_publisher

    published: list[str] = []
    real = services_data_bootstrap._DataResourceAdapter.publish_manifest

    def spy(self: Any, request: Any) -> ManifestPublication:
        published.append(request.task_root)
        return real(self, request)

    monkeypatch.setattr(services_data_bootstrap._DataResourceAdapter, "publish_manifest", spy)

    result_dir = tmp_path / "RESULT"
    manifest = ResultManifest(task_id="t", workflow="optimize", status="completed")
    manifest.add_product("p", "P", "structures/final.xyz", ProductKind.STRUCTURE)

    written = register_result_manifest(result_dir, manifest, publisher=default_manifest_publisher())

    assert published == [str(tmp_path)], "RESULT manifest must publish through the Data port"
    assert written.is_file()


def test_production_executor_manifest_routes_through_data_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp import services_bootstrap, services_data_bootstrap
    from acp.calculations.contracts import CalculationPlan

    published: list[str] = []
    real = services_data_bootstrap._DataResourceAdapter.publish_manifest

    def spy(self: Any, request: Any) -> ManifestPublication:
        published.append(request.task_root)
        return real(self, request)

    monkeypatch.setattr(services_data_bootstrap._DataResourceAdapter, "publish_manifest", spy)

    ports = services_bootstrap.build_services_ports(tmp_path)
    executor = ports.planning
    assert getattr(executor, "_publication", None) is not None, (
        "production executor must carry a port"
    )

    result_dir = tmp_path / "task" / "RESULT"
    executor._write_result_manifest(
        result_dir,
        CalculationPlan(workflow="optimize"),
        [],
        "completed",
        publisher=executor._publication_publisher(),
    )

    assert published == [str(result_dir.parent)]
    assert (result_dir / "result_manifest.json").is_file()


def test_production_frame_candidate_write_hits_data_port(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.api import v1_routes
    from acp.results import frame_candidate_store, frame_candidates
    from acp.services_data_bootstrap import build_data_resource_port
    from acp.services_publication_bootstrap import PublicationDataPort

    work_dir = _make_optimization_task(tmp_path)
    manager = client.app.state.job_manager
    manager.store.create(
        JobRecord(
            id="pdp_opt",
            spec=JobSpec(workflow="optimize", name="pdp_opt", input={}),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
        )
    )

    recording = _RecordingDataPort(build_data_resource_port(tmp_path))
    monkeypatch.setattr(
        v1_routes,
        "_frame_candidate_publication_entry",
        lambda: PublicationDataPort(recording),
    )

    def _fail_direct(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct frame-candidate authority write on the production path")

    monkeypatch.setattr(frame_candidate_store, "write_authority", _fail_direct)
    monkeypatch.setattr(frame_candidates, "write_authority", _fail_direct)

    resp = client.post(
        "/api/v1/jobs/pdp_opt/frame-candidate",
        json={"view_type": "optimization", "frame_index": 1, "role": "TS", "name": "spy"},
    )
    assert resp.status_code == 200, resp.text
    assert recording.candidate_roots == [str(work_dir)]
    assert recording.manifest_roots == [str(work_dir)]


class _SpySourcePort:
    def __init__(self) -> None:
        self.upserts: list[SourceUpsert] = []

    def upsert_sources(self, request: SourceUpsert) -> int:
        self.upserts.append(request)
        return len(request.entries)


class _FailingSourceStore:
    def upsert_index_entries(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct StructureSourceStore write on the production path")


def test_structure_source_indexer_write_hits_data_port(tmp_path: Path) -> None:
    from acp.scheduler.structure_source_indexer import StructureSourceIndexer

    spy = _SpySourcePort()
    indexer = StructureSourceIndexer(
        store=SimpleNamespace(),
        source_store=_FailingSourceStore(),  # type: ignore[arg-type]
        run_root=tmp_path,
        data_port=spy,
    )

    indexer._write_index_entries([{"job_id": "j", "relative_path": "a.xyz"}], discovery_version=1)

    assert len(spy.upserts) == 1
    assert spy.upserts[0].entries == [{"job_id": "j", "relative_path": "a.xyz"}]


def test_structure_source_indexer_direct_store_when_no_port(tmp_path: Path) -> None:
    from acp.scheduler.structure_source_indexer import StructureSourceIndexer

    seen: list[list[dict[str, Any]]] = []

    class _Store:
        def upsert_index_entries(self, entries: Any, **kwargs: Any) -> None:
            seen.append(list(entries))

    indexer = StructureSourceIndexer(
        store=SimpleNamespace(),
        source_store=_Store(),  # type: ignore[arg-type]
        run_root=tmp_path,
    )
    indexer._write_index_entries([{"job_id": "j"}], discovery_version=1)
    assert seen == [[{"job_id": "j"}]]
