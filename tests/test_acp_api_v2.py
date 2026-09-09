"""Tests for the ACP API v2 project-task surface (design doc §12)."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

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
