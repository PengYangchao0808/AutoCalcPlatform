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


# ---------------------------------------------------------------------------
# Todo 84 — remaining production writes route through the Data port
# ---------------------------------------------------------------------------


def _spy_data_manifest(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch the Data adapter's manifest publish; return the recorded task roots."""
    from acp import services_data_bootstrap

    recorded: list[str] = []
    real = services_data_bootstrap._DataResourceAdapter.publish_manifest

    def spy(self: Any, request: Any) -> ManifestPublication:
        recorded.append(request.task_root)
        return real(self, request)

    monkeypatch.setattr(services_data_bootstrap._DataResourceAdapter, "publish_manifest", spy)
    return recorded


def test_scan_primitive_publication_hits_data_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    from acp.calculations.contracts import CalculationRequest, StructureArtifact
    from acp.calculations.primitives import scan as scan_mod
    from cccp.calculation.contracts import ArtifactRef as CccpArtifactRef
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import ScanFrame, ScanPayload, TaskResult

    recorded = _spy_data_manifest(monkeypatch)
    publishers: list[Any] = []
    real_register = scan_mod.register_result_manifest

    def register_spy(result_dir: Any, manifest: Any, *, publisher: Any = None) -> Any:
        publishers.append(publisher)
        return real_register(result_dir, manifest, publisher=publisher)

    monkeypatch.setattr(scan_mod, "register_result_manifest", register_spy)

    result_dir = tmp_path / "RESULT"
    work = tmp_path / "WORK" / "07_PATH"
    work.mkdir(parents=True)
    geometry = work / "frame_000.xyz"
    geometry.write_text("1\nframe\nC 0.0 0.0 0.0\n", encoding="utf-8")

    payload = ScanPayload(
        frames=(
            ScanFrame(
                index=0,
                values=(1.0,),
                energy_hartree=-40.0,
                geometry_ref=CccpArtifactRef(path=geometry, type="structure"),
            ),
        )
    )
    task_result = TaskResult(task=TaskKind.SCAN, status="completed", payload=payload)
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "in.xyz", elements=["C"], source="test"),
        method="",
        resources={"result_dir": str(result_dir)},
        workflow="scan",
    )

    artifacts = scan_mod._publish_scan_products(request, task_result, work, "orca", {})

    assert publishers and publishers[0] is not None, "scan must supply a Data publisher"
    assert recorded == [str(tmp_path)], "scan RESULT manifest must publish through Data"
    assert (result_dir / "trajectories" / "scan_trajectory.json").is_file()
    assert artifacts
    assert "manifest.write(" not in inspect.getsource(scan_mod), (
        "scan primitive must not write the manifest directly"
    )


def test_irc_primitive_publication_hits_data_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    from acp.calculations.contracts import CalculationRequest, StructureArtifact
    from acp.calculations.primitives import irc as irc_mod
    from cccp.calculation.requests import IrcDirection, TaskKind
    from cccp.calculation.results import IrcDirectionResult, IrcPayload, TaskResult

    recorded = _spy_data_manifest(monkeypatch)
    publishers: list[Any] = []
    real_register = irc_mod.register_result_manifest

    def register_spy(result_dir: Any, manifest: Any, *, publisher: Any = None) -> Any:
        publishers.append(publisher)
        return real_register(result_dir, manifest, publisher=publisher)

    monkeypatch.setattr(irc_mod, "register_result_manifest", register_spy)

    result_dir = tmp_path / "RESULT"
    payload = IrcPayload(
        directions=(
            IrcDirectionResult(
                direction=IrcDirection.FORWARD,
                coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
                symbols=("C", "O"),
            ),
            IrcDirectionResult(
                direction=IrcDirection.REVERSE,
                coordinates=((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
                symbols=("C", "O"),
            ),
        )
    )
    task_result = TaskResult(task=TaskKind.IRC, status="completed", payload=payload)
    request = CalculationRequest(
        input_artifact=StructureArtifact(path=tmp_path / "ts.xyz", elements=["C", "O"], source="t"),
        method="",
        resources={"result_dir": str(result_dir)},
        workflow="irc",
    )

    artifacts = irc_mod._publish_irc_products(
        request, task_result, result_dir, "orca", ("forward", "reverse")
    )

    assert publishers and publishers[0] is not None, "irc must supply a Data publisher"
    assert recorded == [str(tmp_path)], "irc RESULT manifest must publish through Data"
    assert (result_dir / "irc" / "irc_forward.xyz").is_file()
    assert (result_dir / "irc" / "irc_reverse.xyz").is_file()
    assert artifacts
    assert "manifest.write(" not in inspect.getsource(irc_mod), (
        "irc primitive must not write the manifest directly"
    )


def test_executor_scan_dispatch_targets_data_backed_scan_primitive() -> None:
    import inspect

    from acp.calculations import executor as executor_mod
    from acp.calculations.contracts import StepKind
    from acp.calculations.primitives import scan as scan_mod

    assert executor_mod._PRIMITIVE_DISPATCH[StepKind.SCAN] is scan_mod.run_scan
    assert scan_mod.run_scan is scan_mod.execute_scan
    assert "publisher" in inspect.signature(scan_mod.execute_scan).parameters


def test_workflows_irc_publication_hits_data_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    from acp.workflows import irc as wf_mod

    recorded = _spy_data_manifest(monkeypatch)
    manifest = ResultManifest(task_id="irc", workflow="irc", status="completed")
    result_dir = tmp_path / "RESULT"

    written = wf_mod._default_manifest_publisher()(result_dir, manifest)

    assert written.is_file()
    assert recorded == [str(tmp_path)], "irc workflow manifest must publish through Data"
    source = inspect.getsource(wf_mod)
    assert "register_result_manifest(" in source
    assert "manifest.write(" not in source, "irc workflow must not write the manifest directly"


class _RecordingSourcePort:
    """Wrap a real source port, recording every method the API invokes."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return attr(*args, **kwargs)

        return call


def _seed_structure_source(client: TestClient, job_id: str = "pdp_src") -> str:
    from acp.scheduler.structure_source_store import StructureSourceStore, source_uid_for

    rel = "RESULT/optimized.xyz"
    store = StructureSourceStore(client.app.state.db_path)
    store.upsert_index_entries(
        [
            {
                "job_id": job_id,
                "relative_path": rel,
                "source_id": f"job_{job_id}:{rel}",
                "project_id": "uncategorized",
                "job_name": f"Task {job_id}",
                "molecule_name": "ethanol",
                "workflow": "Confsearch",
                "job_status": "completed",
                "source_kind": "final",
                "label": "Rank-1 conformer",
                "candidate_id": "",
                "role": "",
                "role_evidence": "",
                "formula": "C2H6O",
                "atom_count": 9,
                "charge": 0,
                "multiplicity": 1,
                "has_3d": True,
                "remote": False,
                "availability": "available",
                "produced_at": "2026-09-01T10:00:00+00:00",
            }
        ],
        discovery_version=1,
    )
    return source_uid_for(job_id, rel)


def test_v2_structure_source_metadata_writes_hit_typed_port(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    from acp.api import v2_structure_sources as v2ss

    uid = _seed_structure_source(client)
    real_port = v2ss._source_organization_port
    recorders: list[_RecordingSourcePort] = []

    def fake_port(request: Any) -> _RecordingSourcePort:
        recorder = _RecordingSourcePort(real_port(request))
        recorders.append(recorder)
        return recorder

    monkeypatch.setattr(v2ss, "_source_organization_port", fake_port)

    meta = client.patch(
        f"/api/v2/structure-sources/{uid}/metadata",
        json={"custom_name": "via port", "expected_revision": 0},
    )
    assert meta.status_code == 200, meta.text
    assessment = client.post(
        f"/api/v2/structure-sources/{uid}/candidate/assessments",
        json={"conclusion": "recommended", "reason_code": "manual", "scope": "reaction:A"},
    )
    assert assessment.status_code == 200, assessment.text
    batch = client.post(
        "/api/v2/structure-sources/batch-metadata",
        json={"items": [{"source_uid": uid, "add_tags": ["t84"], "expected_revision": 1}]},
    )
    assert batch.status_code == 200, batch.text
    status = client.patch(
        f"/api/v2/structure-sources/{uid}/candidate/status",
        json={"status": "trash", "expected_revision": 0},
    )
    assert status.status_code == 200, status.text
    rename = client.post(
        "/api/v2/projects/uncategorized/structure-tags/rename",
        json={"source": "t84", "target": "t84b"},
    )
    assert rename.status_code == 200, rename.text
    remove = client.post(
        "/api/v2/projects/uncategorized/structure-tags/remove",
        json={"tag": "t84b"},
    )
    assert remove.status_code == 200, remove.text

    calls = [name for recorder in recorders for name in recorder.calls]
    for expected in (
        "batch_update_metadata",
        "add_assessment",
        "set_custom_name",
        "set_candidate_status",
        "rename_project_tag",
        "remove_project_tag",
    ):
        assert expected in calls, f"v2 endpoint did not route {expected!r} through the port"

    source = inspect.getsource(v2ss)
    for direct in (
        "source_store.batch_update_metadata",
        "source_store.set_custom_name",
        "source_store.add_tags",
        "source_store.remove_tags",
        "source_store.set_candidate_status",
        "source_store.add_assessment",
        "source_store.rename_project_tag",
        "source_store.remove_project_tag",
        "source_store.purge_candidates",
        "source_store.trash_uids",
    ):
        assert direct not in source, f"v2 metadata write bypasses the port: {direct}"
