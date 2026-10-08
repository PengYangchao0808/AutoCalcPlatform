"""Tests for the ACP API v2 project-task surface (design doc §12)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.nodes import NodeRegistry
from acp.storage.manifest import ResultManifest


def make_client(tmp_path: Path, max_running: int = 2) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=max_running))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, max_running=2) as test_client:
        yield test_client


def _default_project_id(client: TestClient) -> str:
    response = client.get("/api/v2/projects")
    assert response.status_code == 200
    for project in response.json():
        if project["name"] == "Uncategorized":
            return str(project["project_id"])
    raise AssertionError("default project missing from /api/v2/projects")


def _batch_create(
    client: TestClient,
    tasks: list[dict[str, object]],
    project_id: str | None = None,
) -> Any:
    payload: dict[str, object] = {"tasks": tasks}
    if project_id is not None:
        payload["project_id"] = project_id
    response = client.post("/api/v2/tasks/batch", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_fake_task(
    client: TestClient,
    *,
    molecule_name: str = "ethanol",
    task_name: str = "opt",
    remark: str = "final",
) -> dict[str, Any]:
    body = _batch_create(
        client,
        [
            {
                "molecule_name": molecule_name,
                "task_name": task_name,
                "remark": remark,
                "workflow": "fake",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            }
        ],
    )
    created = body["created"]
    assert isinstance(created, list) and len(created) == 1
    return dict[str, Any](created[0])


def _task_work_dir(client: TestClient, task_id: str) -> Path:
    response = client.get(f"/api/v2/tasks/{task_id}")
    assert response.status_code == 200
    return Path(response.json()["work_dir"])


def test_v2_projects_list_contains_default(client: TestClient) -> None:
    response = client.get("/api/v2/projects")
    assert response.status_code == 200
    projects = response.json()
    assert any(project["name"] == "Uncategorized" for project in projects)
    for field in (
        "project_id",
        "name",
        "description",
        "tags",
        "n_tasks",
        "created_at",
        "updated_at",
    ):
        assert field in projects[0]


def test_v2_batch_creates_task_with_v2_dir_name(client: TestClient) -> None:
    task = _create_fake_task(client, molecule_name="ethanol", task_name="opt", remark="final")
    assert task["task_dir_name"] == "ethanol_opt_final"
    assert task["molecule_name"] == "ethanol"
    assert task["task_name"] == "opt"
    assert task["remark"] == "final"
    assert task["display_name"] == "ethanol_opt_final"
    assert task["workflow"] == "fake"
    assert task["status"] in {"queued", "starting", "running", "completed", "failed"}

    detail = client.get(f"/api/v2/tasks/{task['task_id']}")
    assert detail.status_code == 200
    body = detail.json()
    work_dir = Path(body["work_dir"])
    assert work_dir.name == "ethanol_opt_final"
    assert work_dir.is_dir()
    assert body["input_hash"].startswith("sha256:")


def test_v2_project_tasks_list(client: TestClient) -> None:
    project_id = _default_project_id(client)
    task = _create_fake_task(client)

    listing = client.get(f"/api/v2/projects/{project_id}/tasks")
    assert listing.status_code == 200
    tasks = listing.json()
    assert any(item["task_id"] == task["task_id"] for item in tasks)
    entry = next(item for item in tasks if item["task_id"] == task["task_id"])
    assert entry["task_dir_name"] == "ethanol_opt_final"

    projects = client.get("/api/v2/projects").json()
    summary = next(p for p in projects if p["project_id"] == project_id)
    assert summary["n_tasks"] >= 1


def test_v2_project_tasks_unknown_project_404(client: TestClient) -> None:
    response = client.get("/api/v2/projects/no-such-project/tasks")
    assert response.status_code == 404


def test_v2_task_detail_unknown_404(client: TestClient) -> None:
    response = client.get("/api/v2/tasks/does-not-exist")
    assert response.status_code == 404


def test_v2_tree_result_area(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    work_dir = _task_work_dir(client, task_id)
    structures = work_dir / "RESULT" / "structures"
    structures.mkdir(parents=True, exist_ok=True)
    (structures / "x.xyz").write_text("3\nx\nC 0 0 0\nH 1 0 0\nH 0 1 0\n", encoding="utf-8")

    response = client.get(f"/api/v2/tasks/{task_id}/tree?area=result")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["area"] == "result"
    assert body["base"].endswith("RESULT")
    paths = {entry["path"]: entry for entry in body["entries"]}
    assert "structures" in paths
    assert paths["structures"]["is_dir"] is True

    default_area = client.get(f"/api/v2/tasks/{task_id}/tree")
    assert default_area.status_code == 200
    assert default_area.json()["area"] == "result"


def test_v2_tree_work_area_empty_when_absent(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    for _ in range(40):
        body = client.get(f"/api/v2/tasks/{task_id}").json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.5)
    work_dir = _task_work_dir(client, task_id)
    shutil.rmtree(work_dir / "WORK", ignore_errors=True)

    response = client.get(f"/api/v2/tasks/{task_id}/tree?area=work")
    assert response.status_code == 200
    body = response.json()
    assert body["area"] == "work"
    assert body["entries"] == []


def test_v2_tree_unknown_task_404(client: TestClient) -> None:
    response = client.get("/api/v2/tasks/unknown/tree?area=result")
    assert response.status_code == 404


def test_v2_files_download_roundtrip(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    work_dir = _task_work_dir(client, task_id)
    target = work_dir / "RESULT" / "structures" / "x.xyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("3\nethanol final\nC 0 0 0\nH 1 0 0\nH 0 1 0\n", encoding="utf-8")

    response = client.get(f"/api/v2/tasks/{task_id}/files/RESULT/structures/x.xyz")
    assert response.status_code == 200
    assert "ethanol final" in response.text

    missing = client.get(f"/api/v2/tasks/{task_id}/files/RESULT/nope.txt")
    assert missing.status_code == 404


def test_v2_files_traversal_blocked(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    traversal = client.get(f"/api/v2/tasks/{task_id}/files/RESULT/%2e%2e/%2e%2e/etc/passwd")
    assert traversal.status_code == 404


def test_v2_results_404_without_manifest(client: TestClient) -> None:
    task = _create_fake_task(client)
    response = client.get(f"/api/v2/tasks/{task['task_id']}/results")
    assert response.status_code == 404
    assert response.json()["detail"] == "no result manifest"


def test_v2_results_with_manifest(client: TestClient, tmp_path: Path) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    work_dir = _task_work_dir(client, task_id)

    manifest = ResultManifest(task_id=task_id, workflow="fake", status="completed")
    manifest.add_product(
        id="struct_1", label="final structure", path="structures/x.xyz", kind="structure"
    )
    manifest.write(work_dir / "RESULT")

    response = client.get(f"/api/v2/tasks/{task_id}/results")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["workflow"] == "fake"
    assert body["status"] == "completed"
    assert body["products"][0]["id"] == "struct_1"
    assert body["products"][0]["kind"] == "structure"


def test_v2_structure_download_and_unknown_404(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    work_dir = _task_work_dir(client, task_id)
    target = work_dir / "RESULT" / "structures" / "x.xyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("3\nstructure\nC 0 0 0\nH 1 0 0\nH 0 1 0\n", encoding="utf-8")

    manifest = ResultManifest(task_id=task_id, workflow="fake", status="completed")
    manifest.add_product(
        id="struct_1", label="final structure", path="structures/x.xyz", kind="structure"
    )
    manifest.write(work_dir / "RESULT")

    response = client.get(f"/api/v2/tasks/{task_id}/structures/struct_1")
    assert response.status_code == 200
    assert "structure" in response.text

    unknown = client.get(f"/api/v2/tasks/{task_id}/structures/no_such_id")
    assert unknown.status_code == 404


def test_v2_frequencies_download_and_unknown_404(client: TestClient) -> None:
    task = _create_fake_task(client)
    task_id = str(task["task_id"])
    work_dir = _task_work_dir(client, task_id)
    freq_dir = work_dir / "RESULT" / "frequencies"
    freq_dir.mkdir(parents=True, exist_ok=True)
    (freq_dir / "modes.json").write_text('{"modes": []}\n', encoding="utf-8")

    manifest = ResultManifest(task_id=task_id, workflow="fake", status="completed")
    manifest.add_product(
        id="freq_1",
        label="normal modes",
        path="frequencies/modes.json",
        kind="frequency_modes",
    )
    manifest.write(work_dir / "RESULT")

    response = client.get(f"/api/v2/tasks/{task_id}/frequencies/freq_1")
    assert response.status_code == 200
    assert "modes" in response.text

    unknown = client.get(f"/api/v2/tasks/{task_id}/frequencies/no_such_id")
    assert unknown.status_code == 404


def test_v2_batch_partial_failure(client: TestClient) -> None:
    body = _batch_create(
        client,
        [
            {
                "molecule_name": "methanol",
                "task_name": "opt",
                "remark": "",
                "workflow": "not-a-workflow",
                "input": {"source": "CO"},
            },
            {
                "molecule_name": "ethanol",
                "task_name": "opt",
                "remark": "final",
                "workflow": "fake",
                "input": {"source": "CCO"},
            },
        ],
    )
    created = body["created"]
    failed = body["failed"]
    assert len(created) == 1
    assert created[0]["molecule_name"] == "ethanol"
    assert len(failed) == 1
    assert failed[0]["molecule_name"] == "methanol"
    assert "Unsupported workflow" in failed[0]["error"]


def test_v2_batch_empty_tasks_rejected(client: TestClient) -> None:
    response = client.post("/api/v2/tasks/batch", json={"tasks": []})
    assert response.status_code == 422


def _duck_node(name: str, *, software: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> Any:
    """Duck-typed RemoteNode stand-in — NodeRegistry reads attributes only."""
    capabilities = SimpleNamespace(software=software, tags=tags)
    return SimpleNamespace(
        name=name,
        host=f"{name}.example.com",
        max_concurrent_jobs=4,
        enabled=True,
        capabilities=capabilities if (software or tags) else None,
    )


def _client_registry(client: TestClient, remote_nodes: list[Any] | None = None) -> None:
    """Pin the app's manager to a deterministic registry (no ambient config)."""
    manager = client.app.state.job_manager
    manager.registry = NodeRegistry(local_max_jobs=1, remote_nodes=remote_nodes or [])


def _valid_fake_item(molecule_name: str, **extra: object) -> dict[str, object]:
    item: dict[str, object] = {
        "molecule_name": molecule_name,
        "task_name": "opt",
        "remark": "final",
        "workflow": "fake",
        "input": {"source": "CCO"},
    }
    item.update(extra)
    return item


def test_v2_batch_unknown_target_node_goes_to_failed(client: TestClient) -> None:
    """Unknown target_node rejects that item into failed[] — never an HTTP 4xx.

    Locked contract: v2 batch is per-item (test_v2_batch_partial_failure); a
    target-validation failure must surface with its machine-readable code in
    the ``failed[]`` error while sibling items still create normally.
    """
    _client_registry(client, remote_nodes=[])
    body = _batch_create(
        client,
        [
            _valid_fake_item("ethanol"),
            _valid_fake_item(
                "butanol",
                execution_mode="remote",
                target_node="comp-99",
            ),
        ],
    )
    created = body["created"]
    failed = body["failed"]
    assert len(created) == 1
    assert created[0]["molecule_name"] == "ethanol"
    assert len(failed) == 1
    assert failed[0]["molecule_name"] == "butanol"
    assert "unknown_target_node" in failed[0]["error"]
    assert "comp-99" in failed[0]["error"]


def test_v2_batch_incapable_target_node_goes_to_failed(client: TestClient) -> None:
    """A target that cannot satisfy the derived software rejects per-item.

    ``xtb-crest`` derives software ``{xtb, crest}`` (confsearch table);
    ``comp-01`` declares only ``xtb``, so the item carries
    ``target_node_incapable`` plus the missing software — as a ``failed[]``
    entry, leaving the batch 201 and the sibling item created.
    """
    _client_registry(client, remote_nodes=[_duck_node("comp-01", software=("xtb",))])
    body = _batch_create(
        client,
        [
            _valid_fake_item("ethanol"),
            {
                "molecule_name": "propanol",
                "task_name": "conf",
                "remark": "",
                "workflow": "Confsearch",
                "input": {"source": "CCCO"},
                "method": {"protocol": "xtb-crest"},
                "target_node": "comp-01",
            },
        ],
    )
    created = body["created"]
    failed = body["failed"]
    assert len(created) == 1
    assert len(failed) == 1
    assert failed[0]["molecule_name"] == "propanol"
    error = failed[0]["error"]
    assert "target_node_incapable" in error
    assert "crest" in error


def test_v2_batch_node_fields_passthrough_to_spec(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid target passes its selection fields through to the job spec.

    A spy on ``validate_submission_target`` proves the *exact* spec handed to
    the shared helper (and then to ``manager.submit``) carries the three new
    fields; the detail endpoint then shows the persisted ``target_node``.
    ``comp-01`` declares software + the requested ``gpu`` tag so the real
    validation passes end-to-end (deterministic regardless of the dev
    machine's QC binaries — ``fake`` derives an empty software set).
    """
    _client_registry(
        client,
        remote_nodes=[_duck_node("comp-01", software=("xtb", "crest"), tags=("gpu",))],
    )
    from acp.api import v2_routes

    captured: list[Any] = []
    real = v2_routes.validate_submission_target

    def spy(spec: Any, *, registry: NodeRegistry) -> None:
        captured.append(spec)
        real(spec, registry=registry)

    monkeypatch.setattr(v2_routes, "validate_submission_target", spy)
    body = _batch_create(
        client,
        [
            _valid_fake_item(
                "ethanol",
                execution_mode="remote",
                target_node="comp-01",
                node_tags=["gpu"],
            )
        ],
    )
    assert body["failed"] == []
    created = body["created"]
    assert len(created) == 1

    assert len(captured) == 1
    spec = captured[0]
    assert spec.execution_mode == "remote"
    assert spec.target_node == "comp-01"
    assert spec.node_tags == ["gpu"]

    detail = client.get(f"/api/v2/tasks/{created[0]['task_id']}")
    assert detail.status_code == 200
    assert detail.json()["node_id"] == "comp-01"


def test_v2_batch_item_normalizes_node_tags(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch-item node_tags are stripped/deduped/emptied and capped (m7).

    The spy captures the spec handed to the shared target validator — the
    item itself is rejected afterwards (no registry pins the node), but the
    schema-level normalization has already happened by then.
    """
    from acp.api import v2_routes

    captured: list[Any] = []

    def spy(spec: Any, *, registry: NodeRegistry) -> None:
        captured.append(spec)

    monkeypatch.setattr(v2_routes, "validate_submission_target", spy)
    valid_long = "g" * 63
    too_long = "x" * 65
    _batch_create(
        client,
        [
            _valid_fake_item(
                "ethanol",
                node_tags=["gpu", "", " gpu ", "gpu", valid_long, too_long],
            )
        ],
    )
    assert len(captured) == 1
    assert captured[0].node_tags == ["gpu", valid_long]


def test_v2_batch_without_node_fields_matches_previous_behaviour(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Field-less items keep the pre-change submission behaviour.

    The shared target validator runs for every item (mirroring v1), but a
    field-less auto item whose derived software the local machine satisfies
    still submits normally — the new fields are a pure addition, so the
    existing batch tests (all field-less, ``fake`` workflow) must stay green.
    ``local_satisfies`` is pinned True so the auto branch is deterministic
    regardless of which QC binaries the dev machine happens to have.
    """
    _client_registry(client, remote_nodes=[_duck_node("comp-01", software=("xtb", "crest"))])
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: True)
    from acp.api import v2_routes

    calls: list[Any] = []

    def spy(spec: Any, *, registry: NodeRegistry) -> None:
        calls.append(spec)

    monkeypatch.setattr(v2_routes, "validate_submission_target", spy)
    body = _batch_create(
        client,
        [
            _valid_fake_item("ethanol"),
            {
                "molecule_name": "propanol",
                "task_name": "conf",
                "remark": "",
                "workflow": "Confsearch",
                "input": {"source": "CCCO"},
                "method": {"protocol": "xtb-crest"},
            },
        ],
    )
    assert len(calls) == 2  # validator ran for both — none rejected
    assert body["failed"] == []
    assert len(body["created"]) == 2


# ── Remote-aware v2 reads (plan todo 6 / GAP-5 / R6) ─────────────────────────
#
# Cold-cache contract: only RESULT/result_manifest.json is seeded into the
# manager-owned RemoteStructureCache singleton; the node holds the structure,
# `.out` and frequency files; the local work_dir deliberately has NO RESULT
# tree.  The v2 read/download/tree endpoints must serve the manifest from the
# cache, fetch missing files on demand exactly once, distinguish remote-missing
# (404) from transport failure (502), reject traversal before any SFTP call,
# and never write inside work_dir.

_V2_REMOTE_JOB = "v2_remote_job"


class _NodeFetcher:
    """Fake compute node: serves known files; per-path transport failure."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.files: dict[str, bytes] = {}

    def read_file(self, record: Any, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path in self.fail:
            raise RuntimeError("ssh transport broken")
        if rel_path in self.files:
            return self.files[rel_path]
        raise FileNotFoundError(rel_path)


def _v2_remote_manifest(job_id: str) -> ResultManifest:
    manifest = ResultManifest(task_id=job_id, workflow="optimize", status="completed")
    manifest.add_product(
        id="optimized", label="optimized structure", path="simple/optimized.xyz", kind="structure"
    )
    manifest.add_product(id="opt_out", label="orca output", path="simple/opt.out", kind="file")
    manifest.add_product(
        id="freq_1", label="normal modes", path="frequencies/modes.json", kind="frequency_modes"
    )
    return manifest


@pytest.fixture()
def v2_remote(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """TestClient whose remote job has a manifest-only cache and a live node."""
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        manager = client.app.state.job_manager
        work_dir = tmp_path / "v2_remote_task"
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id=_V2_REMOTE_JOB,
            spec=JobSpec(
                workflow="optimize",
                name="v2_remote_task",
                molecule_name="m",
                task_name="opt",
                remark="final",
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            remote_job_id="7",
            result={"node": "node1", "remote_dir": "/remote/v2_remote_task"},
        )
        manager.store.create(record)

        fetcher = _NodeFetcher()
        fetcher.files = {
            "RESULT/simple/optimized.xyz": b"2\nattempt-remote structure\nH 0 0 0\n",
            "RESULT/simple/opt.out": b"ORCA fake output\n",
            "RESULT/frequencies/modes.json": b'{"modes": []}\n',
        }
        # Production wiring: the manager-owned lazy singleton reads _remote_fetcher.
        manager._remote_fetcher = fetcher  # type: ignore[attr-defined]

        # Seed EXACTLY the manifest into the controlled cache (cold file cache).
        cache = manager.structure_cache
        manifest_path = cache.cache_path(_V2_REMOTE_JOB, "RESULT/result_manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(_v2_remote_manifest(_V2_REMOTE_JOB).to_dict()), encoding="utf-8"
        )
        assert cache.get_cached(_V2_REMOTE_JOB, "RESULT/simple/optimized.xyz") is None
        yield {
            "client": client,
            "fetcher": fetcher,
            "record": record,
            "work_dir": work_dir,
            "manager": manager,
        }


def test_v2_remote_results_served_from_manifest_only_cache(
    v2_remote: dict[str, Any],
) -> None:
    """/results serves the cached manifest for a remote job with no local RESULT."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]
    work_dir: Path = v2_remote["work_dir"]

    response = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/results")
    assert response.status_code == 200, response.text
    product_ids = {product["id"] for product in response.json()["products"]}
    assert {"optimized", "opt_out", "freq_1"} <= product_ids

    # The manifest read must come from the cache, never from work_dir.
    assert not (work_dir / "RESULT").exists()
    # ...and no geometry on-demand fetch was triggered by the manifest read.
    assert "RESULT/simple/optimized.xyz" not in fetcher.calls


def test_v2_remote_structure_download_fetches_once_then_hits_cache(
    v2_remote: dict[str, Any],
) -> None:
    """Cold structure download fetches from the node exactly once; repeat hits cache."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]
    work_dir: Path = v2_remote["work_dir"]
    before = set(work_dir.rglob("*"))

    first = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/structures/optimized")
    assert first.status_code == 200, first.text
    assert b"attempt-remote structure" in first.content
    assert fetcher.calls.count("RESULT/simple/optimized.xyz") == 1

    second = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/structures/optimized")
    assert second.status_code == 200
    assert second.content == first.content
    assert fetcher.calls.count("RESULT/simple/optimized.xyz") == 1, "repeat hit re-fetched"

    # Never write into the task work_dir.
    assert set(work_dir.rglob("*")) == before
    assert not (work_dir / "RESULT").exists()


def test_v2_remote_out_file_download_fetches_once_then_hits_cache(
    v2_remote: dict[str, Any],
) -> None:
    """Generic .out download: one on-demand fetch, then served from the cache."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]

    first = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/files/RESULT/simple/opt.out")
    assert first.status_code == 200, first.text
    assert first.content == b"ORCA fake output\n"
    assert fetcher.calls.count("RESULT/simple/opt.out") == 1

    second = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/files/RESULT/simple/opt.out")
    assert second.status_code == 200
    assert fetcher.calls.count("RESULT/simple/opt.out") == 1, "repeat hit re-fetched"
    assert (
        v2_remote["manager"].structure_cache.get_cached(_V2_REMOTE_JOB, "RESULT/simple/opt.out")
        is not None
    )


def test_v2_remote_frequency_download_fetches_once_then_hits_cache(
    v2_remote: dict[str, Any],
) -> None:
    """Frequency product download: exactly one node fetch across repeated reads."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]

    first = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/frequencies/freq_1")
    assert first.status_code == 200, first.text
    assert b'"modes"' in first.content
    assert fetcher.calls.count("RESULT/frequencies/modes.json") == 1

    second = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/frequencies/freq_1")
    assert second.status_code == 200
    assert second.content == first.content
    assert fetcher.calls.count("RESULT/frequencies/modes.json") == 1, "repeat hit re-fetched"


def test_v2_remote_missing_vs_transport_failure_distinguished(
    v2_remote: dict[str, Any],
) -> None:
    """Remote-missing → 404; connection failure → 502 (never conflated)."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]

    missing = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/files/RESULT/simple/nope.out")
    assert missing.status_code == 404, missing.text

    fetcher.fail.add("RESULT/simple/opt.out")
    failed = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/files/RESULT/simple/opt.out")
    assert failed.status_code == 502, (
        f"transport failure not distinguished from missing: {failed.status_code} {failed.text}"
    )
    assert "remote fetch failed" in failed.json()["detail"]
    assert "ssh transport broken" in failed.json()["detail"]


def test_v2_remote_traversal_rejected_before_sftp(
    v2_remote: dict[str, Any],
) -> None:
    """Malicious relative paths are rejected before any remote read."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]

    for url_path in (
        "/api/v2/tasks/{job}/files/RESULT/%2e%2e/%2e%2e/etc/passwd",
        "/api/v2/tasks/{job}/files/%2e%2e/%2e%2e/etc/passwd",
        "/api/v2/tasks/{job}/files/RESULT/%5c..%5c..%5cetc%5cpasswd",
    ):
        response = client.get(url_path.format(job=_V2_REMOTE_JOB))
        assert response.status_code == 404, f"{url_path} -> {response.status_code}"

    assert not any("etc/passwd" in call for call in fetcher.calls), (
        f"traversal reached the node: {fetcher.calls}"
    )


def test_v2_remote_evil_manifest_product_rejected_before_fetch(
    v2_remote: dict[str, Any],
) -> None:
    """A manifest product path escaping RESULT/ is rejected without an SFTP read."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]
    manager = v2_remote["manager"]

    manifest_path = manager.structure_cache.cache_path(
        _V2_REMOTE_JOB, "RESULT/result_manifest.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["products"].append(
        {"id": "evil", "label": "evil", "path": "../../etc/passwd", "kind": "structure"}
    )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/structures/evil")
    assert response.status_code == 404, response.text
    assert not any("etc/passwd" in call for call in fetcher.calls), (
        f"escaped product path reached the node: {fetcher.calls}"
    )


def test_v2_remote_tree_declared_scope_never_masquerades(
    v2_remote: dict[str, Any],
) -> None:
    """Remote tree = manifest-registered products only; work area stays empty.

    Declared contract (endpoint docstring): for remote tasks ``area=result``
    lists a one-level view synthesized from the cached result manifest, and
    ``area=work`` always returns an empty entry list — a partial cache
    directory must never masquerade as the full RESULT/WORK tree.
    """
    client = v2_remote["client"]
    manager = v2_remote["manager"]
    work_dir: Path = v2_remote["work_dir"]

    # Partial-cache noise: files the manifest never registered.
    cache = manager.structure_cache
    stray = cache.cache_path(_V2_REMOTE_JOB, "RESULT/stray.txt")
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not registered\n", encoding="utf-8")
    work_file = cache.cache_path(_V2_REMOTE_JOB, "WORK/stage_01/run.out")
    work_file.parent.mkdir(parents=True, exist_ok=True)
    work_file.write_text("partial work copy\n", encoding="utf-8")

    result = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/tree?area=result")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["area"] == "result"
    assert body["base"].endswith("RESULT")
    entries = {entry["path"]: entry for entry in body["entries"]}
    assert set(entries) == {"simple", "frequencies"}, (
        f"tree scope must equal manifest-registered products: {sorted(entries)}"
    )
    assert all(entry["is_dir"] for entry in entries.values())
    assert "stray.txt" not in entries

    work = client.get(f"/api/v2/tasks/{_V2_REMOTE_JOB}/tree?area=work")
    assert work.status_code == 200, work.text
    assert work.json()["entries"] == [], (
        "partial cache WORK files must never masquerade as the full WORK tree"
    )

    # The listing itself never materializes anything in the task dir.
    assert not (work_dir / "RESULT").exists()
    assert not (work_dir / "WORK").exists()


def test_v2_remote_concurrent_downloads_fetch_once(
    v2_remote: dict[str, Any],
) -> None:
    """Concurrent cold downloads trigger exactly one node fetch (cache dedup)."""
    client = v2_remote["client"]
    fetcher: _NodeFetcher = v2_remote["fetcher"]
    url = f"/api/v2/tasks/{_V2_REMOTE_JOB}/structures/optimized"

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _i: client.get(url), range(4)))

    assert all(response.status_code == 200 for response in responses), [
        (r.status_code, r.text) for r in responses
    ]
    assert fetcher.calls.count("RESULT/simple/optimized.xyz") == 1, (
        f"concurrent requests re-fetched: {fetcher.calls}"
    )


def test_v2_remote_js_helper_v2_namespace_60s_budget_and_timeout_conversion() -> None:
    """Executed JS probe: /api/v2 namespace + 60000ms budget + converted timeout.

    Root ANTI #28: remote-backed v2 reads must opt into the 60s remote budget
    while preserving the /api/v2 namespace (never routed through the v1-pinned
    ``apiRemote()``) and sharing ``apiRequest``'s error conversion — a slow or
    failed remote fetch surfaces a localized timeout Error, never a raw
    AbortError.  The probe extracts the real helper functions from the workbench
    source and executes them under node with a stubbed ``fetch``.
    """
    if not shutil.which("node"):
        pytest.skip("node not available")

    frontend = Path(__file__).resolve().parents[1] / "frontend" / "ACP_Workbench_v2.html"
    html = frontend.read_text(encoding="utf-8")
    assert "timeoutMs: API_REMOTE_TIMEOUT_MS" in html, (
        "no version-aware v2 remote helper passes the 60s budget yet"
    )

    match = re.search(
        r"(var API_TIMEOUT_MS = 8000;[\s\S]*?async function apiV2\(path, opts\) \{[\s\S]*?\n\})",
        html,
    )
    assert match, "api/apiRequest/apiRemote/apiV2 block not found in workbench HTML"
    remote_match = re.search(r"(function apiV2Remote\(path, opts\) \{[\s\S]*?\n\})", html)
    assert remote_match, "apiV2Remote helper missing from workbench HTML"
    api_source = match.group(1) + "\n" + remote_match.group(1)

    script = (
        "const API_BASE = '/api/v1';\n"
        "function t(key, vars) { return key + (vars ? ':' + JSON.stringify(vars) : ''); }\n"
        "function invalidateTaskViewMetadata() {}\n"
        "function fail(msg) { console.error('FAIL: ' + msg); process.exit(1); }\n"
        "class FakeAbortError extends Error {\n"
        "  constructor() {\n"
        "    super('signal is aborted without reason');\n"
        "    this.name = 'AbortError';\n"
        "  }\n"
        "}\n"
        + api_source
        + "\n"
        + textwrap.dedent(
            """
            (async function main() {
              var capturedUrls = [];
              var capturedDelays = [];
              var realSetTimeout = global.setTimeout;
              var realClearTimeout = global.clearTimeout;
              // Budget spy: record the requested delay, fire immediately (no 60s wait).
              global.setTimeout = function (fn, ms) {
                capturedDelays.push(ms);
                return realSetTimeout(fn, 0);
              };
              global.clearTimeout = function (id) { return realClearTimeout(id); };
              global.fetch = function (url, opts) {
                capturedUrls.push(url);
                return new Promise(function (_resolve, reject) {
                  if (opts && opts.signal) {
                    opts.signal.addEventListener('abort', function () {
                      reject(new FakeAbortError());
                    });
                  }
                });
              };
              if (API_TIMEOUT_MS !== 8000) fail('local default budget changed: ' + API_TIMEOUT_MS);
              if (API_REMOTE_TIMEOUT_MS !== 60000)
                fail('remote budget changed: ' + API_REMOTE_TIMEOUT_MS);

              // (1) version-aware remote wrapper: /api/v2 URL + 60000ms budget.
              try {
                await apiV2Remote('/tasks/task_demo/structures/optimized');
                fail('remote timeout did not reject');
              } catch (e) {
                if (capturedUrls[0] !== '/api/v2/tasks/task_demo/structures/optimized')
                  fail('wrong request URL: ' + capturedUrls[0]);
                if (capturedDelays[0] !== 60000)
                  fail('remote budget not 60000ms: ' + capturedDelays[0]);
                if (e.name === 'AbortError') fail('remote timeout leaked AbortError');
                if (String(e.message).indexOf('aborted') >= 0)
                  fail('remote timeout leaked raw abort text: ' + e.message);
                if (String(e.message).indexOf('api.timeout') !== 0)
                  fail('timeout not converted by apiRequest: ' + e.message);
              }

              // (2) explicit frozen form: apiV2(path, {timeoutMs: API_REMOTE_TIMEOUT_MS}).
              capturedUrls.length = 0; capturedDelays.length = 0;
              try {
                await apiV2('/tasks/task_demo/results', { timeoutMs: API_REMOTE_TIMEOUT_MS });
                fail('explicit remote timeout did not reject');
              } catch (e) {
                if (capturedUrls[0] !== '/api/v2/tasks/task_demo/results')
                  fail('wrong explicit URL: ' + capturedUrls[0]);
                if (capturedDelays[0] !== 60000)
                  fail('explicit budget not 60000ms: ' + capturedDelays[0]);
                if (e.name === 'AbortError') fail('explicit timeout leaked AbortError');
                if (String(e.message).indexOf('api.timeout') !== 0)
                  fail('explicit timeout not converted: ' + e.message);
              }

              // (3) plain v2 calls keep the local 8s default.
              capturedUrls.length = 0; capturedDelays.length = 0;
              try {
                await apiV2('/tasks/task_demo/tree');
                fail('local timeout did not reject');
              } catch (e) {
                if (capturedDelays[0] !== 8000)
                  fail('local default budget changed: ' + capturedDelays[0]);
              }

              console.log('PASS');
            })().catch(function (e) { console.error('FAIL: unexpected', e); process.exit(1); });
            """
        )
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"node failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "PASS" in result.stdout


# ── Cold-cache remote read acceptance (plan todo 11 / GAP-5 / R6) ────────────
#
# Independent R6 acceptance layer over the cold-cache contract (T6 = the
# endpoint slices above; T15 = cache unit/fence tests in
# test_acp_api_structure_viewer.py): one fake-fetcher matrix exercised
# end-to-end through the real v2 API.  The cache starts manifest-only; the
# node holds structures / frequencies / `.out` / WORK files plus the optional
# terminal-catalog files so catalog retries pin to one read each — every
# accounting below is ABSOLUTE (whole call log), not path-scoped.  The task
# work_dir is snapshotted before/after every matrix and must stay identical.

_R6_JOB = "v2_r6_job"
# Terminal catalog fetch for workflow "optimize": exactly these optional
# files, each read once; afterwards every read re-validates them from cache.
_R6_CATALOG_PATHS = frozenset(
    {"input.xyz", "RESULT/frame_candidates.json", "RESULT/frequencies/modes.json"}
)
_R6_DATA_PATHS = frozenset({"RESULT/simple/optimized.xyz", "RESULT/simple/opt.out"})


class _R6NodeFetcher:
    """Fake node: absolute call accounting, per-path failure, optional gate."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.files: dict[str, bytes] = {}
        # Single-flight gate: the first reader of ``gate_path`` parks until
        # ``gate_release`` so sibling requests are forced to pile up on the
        # per-path lock (a broken dedup would surface as extra calls).
        self.gate_path: str | None = None
        self.gate_entered = threading.Event()
        self.gate_release = threading.Event()

    def read_file(self, record: Any, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path in self.fail:
            raise RuntimeError("ssh transport broken")
        if rel_path == self.gate_path:
            self.gate_entered.set()
            self.gate_release.wait(timeout=10.0)
        if rel_path in self.files:
            return self.files[rel_path]
        raise FileNotFoundError(rel_path)


def _r6_work_tree(work_dir: Path) -> frozenset[str]:
    """Snapshot of *work_dir* (directories marked with a trailing ``/``)."""
    if not work_dir.exists():
        return frozenset()
    return frozenset(
        entry.relative_to(work_dir).as_posix() + ("/" if entry.is_dir() else "")
        for entry in work_dir.rglob("*")
    )


@pytest.fixture()
def r6_remote(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Cold-cache stage: only the manifest is cached; the node holds the rest."""
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        manager = client.app.state.job_manager
        work_dir = tmp_path / "v2_r6_task"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "keep.txt").write_text("sentinel\n", encoding="utf-8")
        record = JobRecord(
            id=_R6_JOB,
            spec=JobSpec(
                workflow="optimize",
                name="v2_r6_task",
                molecule_name="m",
                task_name="opt",
                remark="final",
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            remote_job_id="11",
            result={"node": "node1", "remote_dir": "/remote/v2_r6_task"},
        )
        manager.store.create(record)

        fetcher = _R6NodeFetcher()
        fetcher.files = {
            "RESULT/simple/optimized.xyz": b"2\nr6 structure\nH 0 0 0\n",
            "RESULT/simple/opt.out": b"ORCA r6 output\n",
            "RESULT/frequencies/modes.json": b'{"modes": []}\n',
            # Optional terminal-catalog files the node also holds: fetched
            # once, they pin the catalog so the second-hit assertions can be
            # absolute (zero extra reads overall) instead of path-scoped.
            "input.xyz": b"3\ninput\nC 0 0 0\nH 1 0 0\nH 0 1 0\n",
            "RESULT/frame_candidates.json": b'{"candidates": []}\n',
            # Node-side WORK tree: remote area=work must never fetch it.
            "WORK/stage_01/run.out": b"node work\n",
        }
        manager._remote_fetcher = fetcher  # type: ignore[attr-defined]

        # Seed EXACTLY the manifest into the controlled cache (cold file cache).
        cache = manager.structure_cache
        manifest_path = cache.cache_path(_R6_JOB, "RESULT/result_manifest.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(_v2_remote_manifest(_R6_JOB).to_dict()), encoding="utf-8"
        )
        assert cache.get_cached(_R6_JOB, "RESULT/simple/optimized.xyz") is None
        yield {
            "client": client,
            "fetcher": fetcher,
            "manager": manager,
            "record": record,
            "work_dir": work_dir,
            "manifest_path": manifest_path,
        }


class TestColdCacheRemoteReadAcceptance:
    """R6 acceptance matrix through the real v2 API (fake fetcher, GAP-5).

    Covers end-to-end: manifest-only warm state -> first download -> second
    hit with absolute zero extra remote reads -> 4-thread single-flight ->
    remote-missing 404 vs transport-failure 502 -> traversal rejected
    pre-SFTP with zero fetcher calls -> declared-scope tree (remote
    ``area=work`` == ``[]``) -> no work_dir writes anywhere.
    """

    def test_first_download_second_hit_zero_extra_remote_reads(
        self, r6_remote: dict[str, Any]
    ) -> None:
        client: TestClient = r6_remote["client"]
        fetcher: _R6NodeFetcher = r6_remote["fetcher"]
        work_dir: Path = r6_remote["work_dir"]
        tree_before = _r6_work_tree(work_dir)
        assert tree_before == {"keep.txt"}, f"unexpected baseline: {tree_before}"

        # (a) Manifest-only cache serves /results with zero geometry reads;
        #     the terminal catalog pulls exactly its three optional files.
        results = client.get(f"/api/v2/tasks/{_R6_JOB}/results")
        assert results.status_code == 200, results.text
        product_ids = {product["id"] for product in results.json()["products"]}
        assert {"optimized", "opt_out", "freq_1"} <= product_ids
        assert "RESULT/simple/optimized.xyz" not in fetcher.calls, (
            "manifest read must not pull geometry"
        )
        assert len(fetcher.calls) == 3, fetcher.calls
        assert set(fetcher.calls) == _R6_CATALOG_PATHS

        # (b) First downloads: exactly one node read per data file.
        struct_first = client.get(f"/api/v2/tasks/{_R6_JOB}/structures/optimized")
        out_first = client.get(f"/api/v2/tasks/{_R6_JOB}/files/RESULT/simple/opt.out")
        freq_first = client.get(f"/api/v2/tasks/{_R6_JOB}/frequencies/freq_1")
        assert [r.status_code for r in (struct_first, out_first, freq_first)] == [200, 200, 200]
        assert b"r6 structure" in struct_first.content
        assert out_first.content == b"ORCA r6 output\n"
        assert b'"modes"' in freq_first.content
        assert fetcher.calls.count("RESULT/simple/optimized.xyz") == 1
        assert fetcher.calls.count("RESULT/simple/opt.out") == 1
        assert fetcher.calls.count("RESULT/frequencies/modes.json") == 1
        first_round = list(fetcher.calls)
        assert len(first_round) == 5, first_round

        # (c) Second hits: byte-identical responses and ABSOLUTE zero extra
        #     remote reads — the whole call log must not grow at all.
        seconds = [
            client.get(f"/api/v2/tasks/{_R6_JOB}/structures/optimized"),
            client.get(f"/api/v2/tasks/{_R6_JOB}/files/RESULT/simple/opt.out"),
            client.get(f"/api/v2/tasks/{_R6_JOB}/frequencies/freq_1"),
            client.get(f"/api/v2/tasks/{_R6_JOB}/results"),
        ]
        assert [r.status_code for r in seconds] == [200, 200, 200, 200], [
            (r.status_code, r.text) for r in seconds
        ]
        assert seconds[0].content == struct_first.content
        assert seconds[1].content == out_first.content
        assert seconds[2].content == freq_first.content
        assert fetcher.calls == first_round, (
            f"second hit re-read the node: {fetcher.calls[len(first_round) :]}"
        )

        # (d) Absolute accounting over the whole journey: each path exactly once.
        assert len(fetcher.calls) == 5, fetcher.calls
        assert set(fetcher.calls) == _R6_CATALOG_PATHS | _R6_DATA_PATHS

        # (e) The task work_dir is never written.
        assert _r6_work_tree(work_dir) == tree_before
        assert (work_dir / "keep.txt").read_text(encoding="utf-8") == "sentinel\n"
        assert not (work_dir / "RESULT").exists()
        assert not (work_dir / "WORK").exists()

    def test_concurrent_cold_downloads_single_flight(self, r6_remote: dict[str, Any]) -> None:
        client: TestClient = r6_remote["client"]
        fetcher: _R6NodeFetcher = r6_remote["fetcher"]
        url = f"/api/v2/tasks/{_R6_JOB}/structures/optimized"
        fetcher.gate_path = "RESULT/simple/optimized.xyz"

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(client.get, url) for _ in range(4)]
            try:
                assert fetcher.gate_entered.wait(timeout=10.0), "no fetch reached the node"
                # Let the three sibling requests pile up on the per-path lock
                # while the first reader is parked inside the gate.
                time.sleep(0.3)
            finally:
                fetcher.gate_release.set()
            responses = [future.result(timeout=60) for future in futures]

        assert [r.status_code for r in responses] == [200, 200, 200, 200], [
            (r.status_code, r.text) for r in responses
        ]
        # Exactly one read for the gated structure path...
        assert fetcher.calls.count("RESULT/simple/optimized.xyz") == 1, fetcher.calls
        # ...and absolute single-flight overall: catalog 3 + structure 1.
        assert len(fetcher.calls) == 4, fetcher.calls
        assert set(fetcher.calls) == _R6_CATALOG_PATHS | {"RESULT/simple/optimized.xyz"}

    def test_remote_missing_404_vs_transport_failure_502(self, r6_remote: dict[str, Any]) -> None:
        client: TestClient = r6_remote["client"]
        fetcher: _R6NodeFetcher = r6_remote["fetcher"]
        manager = r6_remote["manager"]
        work_dir: Path = r6_remote["work_dir"]
        tree_before = _r6_work_tree(work_dir)

        missing = client.get(f"/api/v2/tasks/{_R6_JOB}/files/RESULT/simple/nope.out")
        assert missing.status_code == 404, missing.text
        assert "File not found" in missing.json()["detail"]
        assert fetcher.calls.count("RESULT/simple/nope.out") == 1, fetcher.calls

        fetcher.fail.add("RESULT/simple/flaky.out")
        failed = client.get(f"/api/v2/tasks/{_R6_JOB}/files/RESULT/simple/flaky.out")
        assert failed.status_code == 502, (
            f"transport failure conflated with remote-missing: {failed.status_code} {failed.text}"
        )
        failed_detail = failed.json()["detail"]
        assert "remote fetch failed" in failed_detail
        assert "ssh transport broken" in failed_detail
        assert fetcher.calls.count("RESULT/simple/flaky.out") == 1, fetcher.calls

        # Never conflated: the outcomes differ, and a failed fetch leaves
        # no cached bytes behind.
        assert missing.status_code != failed.status_code
        assert manager.structure_cache.get_cached(_R6_JOB, "RESULT/simple/flaky.out") is None
        assert _r6_work_tree(work_dir) == tree_before

    def test_traversal_vectors_rejected_pre_sftp_zero_fetcher_calls(
        self, r6_remote: dict[str, Any]
    ) -> None:
        client: TestClient = r6_remote["client"]
        fetcher: _R6NodeFetcher = r6_remote["fetcher"]
        manifest_path: Path = r6_remote["manifest_path"]
        work_dir: Path = r6_remote["work_dir"]
        tree_before = _r6_work_tree(work_dir)

        for url_path in (
            "/api/v2/tasks/{job}/files/RESULT/%2e%2e/%2e%2e/etc/passwd",
            "/api/v2/tasks/{job}/files/%2e%2e/%2e%2e/etc/passwd",
            "/api/v2/tasks/{job}/files/RESULT/%5c..%5c..%5cetc%5cpasswd",
            "/api/v2/tasks/{job}/files/%2fetc%2fpasswd",
        ):
            response = client.get(url_path.format(job=_R6_JOB))
            assert response.status_code == 404, f"{url_path} -> {response.status_code}"
            assert "outside work directory" in response.json()["detail"], (
                f"{url_path} bypassed the pre-SFTP normalizer: {response.text}"
            )

        # Absolute accounting: not one fetcher call — no path-scoped filtering.
        assert fetcher.calls == [], f"traversal reached the node: {fetcher.calls}"

        # Manifest-product traversal: rejected after normalization, still
        # before any SFTP read for the evil path.
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["products"].append(
            {"id": "evil", "label": "evil", "path": "../../etc/passwd", "kind": "structure"}
        )
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        evil = client.get(f"/api/v2/tasks/{_R6_JOB}/structures/evil")
        assert evil.status_code == 404, evil.text
        assert "outside work directory" in evil.json()["detail"]
        # Absolute: only the legitimate terminal catalog reads happened —
        # nothing containing "passwd" or ".." ever reached the fetcher.
        assert len(fetcher.calls) == len(_R6_CATALOG_PATHS), fetcher.calls
        assert set(fetcher.calls) == _R6_CATALOG_PATHS
        assert not any("passwd" in call or ".." in call for call in fetcher.calls)
        assert _r6_work_tree(work_dir) == tree_before

    def test_remote_tree_declared_scope_and_work_always_empty(
        self, r6_remote: dict[str, Any]
    ) -> None:
        client: TestClient = r6_remote["client"]
        fetcher: _R6NodeFetcher = r6_remote["fetcher"]
        manager = r6_remote["manager"]
        work_dir: Path = r6_remote["work_dir"]
        tree_before = _r6_work_tree(work_dir)

        # Cached strays: one under WORK, one unregistered under RESULT.
        cache = manager.structure_cache
        stray_work = cache.cache_path(_R6_JOB, "WORK/stage_01/run.out")
        stray_work.parent.mkdir(parents=True, exist_ok=True)
        stray_work.write_text("cached work stray\n", encoding="utf-8")
        stray_result = cache.cache_path(_R6_JOB, "RESULT/stray.txt")
        stray_result.parent.mkdir(parents=True, exist_ok=True)
        stray_result.write_text("not registered\n", encoding="utf-8")

        # Remote area=work: [] with ABSOLUTE zero fetcher calls — the node
        # holds WORK files, yet none is ever requested.
        work = client.get(f"/api/v2/tasks/{_R6_JOB}/tree?area=work")
        assert work.status_code == 200, work.text
        assert work.json()["entries"] == [], "cached WORK strays leaked into the listing"
        assert fetcher.calls == [], f"work listing touched the node: {fetcher.calls}"

        # area=result: exactly the declared manifest-products scope.
        result = client.get(f"/api/v2/tasks/{_R6_JOB}/tree?area=result")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["area"] == "result"
        assert body["base"].endswith("RESULT")
        entries = {entry["path"]: entry for entry in body["entries"]}
        assert set(entries) == {"simple", "frequencies"}, sorted(entries)
        assert all(entry["is_dir"] for entry in entries.values())
        assert "stray.txt" not in entries and "stage_01" not in entries

        # Only manifest-scope catalog reads reached the node; WORK/ never.
        assert len(fetcher.calls) == len(_R6_CATALOG_PATHS), fetcher.calls
        assert set(fetcher.calls) == _R6_CATALOG_PATHS
        assert not any(call.startswith("WORK/") for call in fetcher.calls)

        # Listing never materializes anything in the task dir.
        assert _r6_work_tree(work_dir) == tree_before
        assert not (work_dir / "RESULT").exists()
        assert not (work_dir / "WORK").exists()
