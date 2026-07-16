"""Tests for the ACP API v1 surface."""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _parse_xyz_element_counts(xyz_text: str) -> dict[str, int]:
    lines = xyz_text.strip().splitlines()
    n = int(lines[0].strip())
    counts: dict[str, int] = Counter()
    for line in lines[2 : 2 + n]:
        symbol = line.split()[0]
        counts[symbol] += 1
    return dict(counts)


def _parse_xyz_frames(xyz_text: str) -> list[str]:
    lines = xyz_text.strip().splitlines()
    frames: list[str] = []
    i = 0
    while i < len(lines):
        n = int(lines[i].strip())
        frame_lines = lines[i : i + 2 + n]
        frames.append("\n".join(frame_lines) + "\n")
        i += 2 + n
    return frames


def make_client(tmp_path: Path, max_running: int = 2) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=max_running))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, max_running=2) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str = "Alpha") -> dict[str, object]:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "description": f"{name} project", "tags": [name.lower()]},
    )
    assert response.status_code == 201
    return response.json()


def _submit_fake_job(
    client: TestClient,
    *,
    source: str = "CCO",
    name: str = "demo",
    project_id: str | None = None,
    demo_frames: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow": "fake",
        "name": name,
        "input": {"source": source, "demo_frames": demo_frames},
        "method": {"protocol": "ext"},
    }
    if project_id is not None:
        payload["project_id"] = project_id
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201
    return response.json()


def _wait_for_terminal_job(
    client: TestClient, job_id: str, path_prefix: str = "/api/v1/jobs"
) -> dict[str, object]:
    record: dict[str, object] = {}
    for _ in range(40):
        response = client.get(f"{path_prefix}/{job_id}")
        assert response.status_code == 200
        record = response.json()
        if record["status"] in {"completed", "failed", "cancelled"}:
            return record
        time.sleep(0.5)
    return record


def test_v1_status(client: TestClient) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "ACP Workbench"
    assert "queue" in body


def test_v1_backends(client: TestClient) -> None:
    response = client.get("/api/v1/backends")
    assert response.status_code == 200
    names = {backend["name"] for backend in response.json()["backends"]}
    assert {"gaussian", "xtb", "crest", "orca"} <= names


def test_v1_projects_list_default_exists(client: TestClient) -> None:
    response = client.get("/api/v1/projects")
    assert response.status_code == 200
    names = {project["name"] for project in response.json()["projects"]}
    assert "Uncategorized" in names


def test_v1_project_create_get_delete(client: TestClient) -> None:
    created = _create_project(client, name="Beta")
    project_id = created["project_id"]

    fetched = client.get(f"/api/v1/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Beta"

    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"description": "updated", "settings": {"theme": "dark"}},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"
    assert updated.json()["settings"] == {"theme": "dark"}

    deleted = client.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_v1_project_delete_with_data(client: TestClient) -> None:
    project = _create_project(client, name="DeleteWithData")
    created = _submit_fake_job(
        client, name="delete-data-job", project_id=str(project["project_id"])
    )
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)

    deleted = client.delete(f"/api/v1/projects/{project['project_id']}?delete_data=true")
    assert deleted.status_code == 200

    assert client.get(f"/api/v1/projects/{project['project_id']}").status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_v1_project_delete_without_data_moves_jobs(client: TestClient) -> None:
    project = _create_project(client, name="DeleteNoData")
    created = _submit_fake_job(
        client, name="move-default-job", project_id=str(project["project_id"])
    )
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)

    deleted = client.delete(f"/api/v1/projects/{project['project_id']}")
    assert deleted.status_code == 200

    assert client.get(f"/api/v1/projects/{project['project_id']}").status_code == 404
    moved = client.get(f"/api/v1/jobs/{job_id}")
    assert moved.status_code == 200
    assert moved.json()["project_id"] is not None


def test_v1_project_delete_default_fails(client: TestClient) -> None:
    default_list = client.get("/api/v1/projects")
    default_id = default_list.json()["projects"][0]["project_id"]
    response = client.delete(f"/api/v1/projects/{default_id}?delete_data=true")
    assert response.status_code == 400


def test_v1_job_submit_without_project_gets_default(client: TestClient) -> None:
    created = _submit_fake_job(client, name="default-project")
    assert created["project_id"] is not None

    detail = client.get(f"/api/v1/jobs/{created['job_id']}")
    assert detail.status_code == 200
    assert detail.json()["project_id"] == created["project_id"]
    assert detail.json()["spec"]["project_id"] == created["project_id"]


def test_v1_job_submit_with_project(client: TestClient) -> None:
    project = _create_project(client, name="Gamma")
    created = _submit_fake_job(client, name="gamma-job", project_id=str(project["project_id"]))
    assert created["project_id"] == project["project_id"]

    project_jobs = client.get(f"/api/v1/projects/{project['project_id']}/jobs")
    assert project_jobs.status_code == 200
    assert [job["id"] for job in project_jobs.json()["jobs"]] == [created["job_id"]]


def test_v1_job_move_to_project(client: TestClient) -> None:
    source = _create_project(client, name="Source")
    target = _create_project(client, name="Target")
    created = _submit_fake_job(client, name="move-job", project_id=str(source["project_id"]))
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)

    moved = client.post(
        f"/api/v1/jobs/{job_id}/move",
        json={"project_id": str(target["project_id"])},
    )
    assert moved.status_code == 200
    body = moved.json()
    assert body["project_id"] == target["project_id"]
    assert body["spec"]["project_id"] == target["project_id"]

    target_jobs = client.get(f"/api/v1/projects/{target['project_id']}/jobs")
    assert target_jobs.status_code == 200
    assert any(job["id"] == job_id for job in target_jobs.json()["jobs"])

    source_jobs = client.get(f"/api/v1/projects/{source['project_id']}/jobs")
    assert source_jobs.status_code == 200
    assert not any(job["id"] == job_id for job in source_jobs.json()["jobs"])


def test_v1_job_clone_to_project(client: TestClient) -> None:
    source = _create_project(client, name="SourceClone")
    target = _create_project(client, name="TargetClone")
    created = _submit_fake_job(client, name="clone-job", project_id=str(source["project_id"]))
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)

    cloned = client.post(
        f"/api/v1/jobs/{job_id}/clone",
        json={"project_id": str(target["project_id"])},
    )
    assert cloned.status_code == 201
    body = cloned.json()
    new_job_id = str(body["job_id"])
    assert new_job_id != job_id
    assert body["project_id"] == target["project_id"]

    target_jobs = client.get(f"/api/v1/projects/{target['project_id']}/jobs")
    assert target_jobs.status_code == 200
    job_ids = [job["id"] for job in target_jobs.json()["jobs"]]
    assert new_job_id in job_ids
    assert job_id not in job_ids

    source_jobs = client.get(f"/api/v1/projects/{source['project_id']}/jobs")
    assert source_jobs.status_code == 200
    assert any(job["id"] == job_id for job in source_jobs.json()["jobs"])

    cloned_record = _wait_for_terminal_job(client, new_job_id)
    assert cloned_record["status"] == "completed"


def test_v1_job_delete_with_data(client: TestClient) -> None:
    created = _submit_fake_job(client, name="delete-job")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)

    deleted = client.delete(f"/api/v1/jobs/{job_id}?delete_data=true")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404


def test_v1_job_delete_without_data(client: TestClient, tmp_path: Path) -> None:
    created = _submit_fake_job(client, name="delete-no-data-job")
    job_id = str(created["job_id"])
    record = _wait_for_terminal_job(client, job_id)
    work_dir = Path(record["work_dir"])

    deleted = client.delete(f"/api/v1/jobs/{job_id}?delete_data=false")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert work_dir.exists()


def test_v1_job_delete_active_fails(client: TestClient) -> None:
    created = _submit_fake_job(client, name="active-delete-job")
    job_id = str(created["job_id"])
    # fake jobs are usually terminal very quickly; cancel first if still active
    detail = client.get(f"/api/v1/jobs/{job_id}")
    if detail.json()["status"] in {"queued", "starting", "running", "cancelling"}:
        response = client.delete(f"/api/v1/jobs/{job_id}?delete_data=true")
        assert response.status_code == 409
        return
    # If already terminal, the test is not meaningful; skip via pytest
    pytest.skip("fake job finished before delete could be tested")


def test_v1_job_list_filter_by_project(client: TestClient) -> None:
    alpha = _create_project(client, name="Alpha")
    beta = _create_project(client, name="Beta")
    alpha_job = _submit_fake_job(client, name="alpha-job", project_id=str(alpha["project_id"]))
    beta_job = _submit_fake_job(client, name="beta-job", project_id=str(beta["project_id"]))

    filtered = client.get(f"/api/v1/jobs?project_id={alpha['project_id']}")
    assert filtered.status_code == 200
    job_ids = [job["id"] for job in filtered.json()["jobs"]]
    assert alpha_job["job_id"] in job_ids
    assert beta_job["job_id"] not in job_ids


def test_v1_job_detail(client: TestClient) -> None:
    created = _submit_fake_job(client, name="detail-job")
    response = client.get(f"/api/v1/jobs/{created['job_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["job_id"]
    assert body["spec"]["workflow"] == "fake"
    assert body["project_id"] == created["project_id"]
    assert body["input_hash"].startswith("sha256:")


def test_v1_job_cancel(tmp_path: Path) -> None:
    with make_client(tmp_path, max_running=1) as client:
        blocker = _submit_fake_job(client, source="X", name="blocker")
        second = _submit_fake_job(client, source="Y", name="second")
        time.sleep(0.5)

        cancelled = client.post(f"/api/v1/jobs/{second['job_id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] in {"cancelling", "cancelled"}

        record = _wait_for_terminal_job(client, str(second["job_id"]))
        assert record["status"] in {"cancelled", "completed", "failed"}
        client.post(f"/api/v1/jobs/{blocker['job_id']}/cancel")


def test_v1_job_tasks(client: TestClient) -> None:
    created = _submit_fake_job(client, name="tasks-job")
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "completed"

    tasks = client.get(f"/api/v1/jobs/{created['job_id']}/tasks")
    assert tasks.status_code == 200
    body = tasks.json()["tasks"]
    assert [task["stage_name"] for task in body] == ["init", "compute", "finalize"]

    task_detail = client.get(f"/api/v1/tasks/{body[0]['task_id']}")
    assert task_detail.status_code == 200
    assert task_detail.json()["job_id"] == created["job_id"]


def test_v1_job_artifacts(client: TestClient) -> None:
    created = _submit_fake_job(client, name="artifacts-job")
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "completed"

    artifacts = client.get(f"/api/v1/jobs/{created['job_id']}/artifacts")
    assert artifacts.status_code == 200
    items = artifacts.json()["artifacts"]
    assert items
    assert any(item["file_path"] == "results/input_preview.xyz" for item in items)

    detail = client.get(f"/api/v1/artifacts/{items[0]['artifact_id']}")
    assert detail.status_code == 200
    assert detail.json()["job_id"] == created["job_id"]


def test_v1_artifact_download(client: TestClient) -> None:
    created = _submit_fake_job(client, name="download-artifact-job")
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "completed"

    artifacts = client.get(f"/api/v1/jobs/{created['job_id']}/artifacts").json()["artifacts"]
    preview = next(item for item in artifacts if item["file_path"] == "results/input_preview.xyz")
    response = client.get(f"/api/v1/artifacts/{preview['artifact_id']}/download")
    assert response.status_code == 200
    counts = _parse_xyz_element_counts(response.text)
    assert counts == {"C": 2, "H": 6, "O": 1}


def test_v1_fake_job_invalid_smiles_fails(client: TestClient) -> None:
    created = _submit_fake_job(client, source="not-a-smiles", name="invalid-smiles-job")
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "failed"
    assert record["error"] and "Invalid SMILES" in str(record["error"])


def test_v1_fake_job_demo_frames(client: TestClient) -> None:
    created = _submit_fake_job(client, name="demo-frames-job", demo_frames=True)
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "completed"

    artifacts = client.get(f"/api/v1/jobs/{created['job_id']}/artifacts").json()["artifacts"]
    assert any(item["file_path"] == "results/demo_frames.xyz" for item in artifacts)

    demo = next(item for item in artifacts if item["file_path"] == "results/demo_frames.xyz")
    response = client.get(f"/api/v1/artifacts/{demo['artifact_id']}/download")
    assert response.status_code == 200
    frames = _parse_xyz_frames(response.text)
    assert len(frames) == 3
    first_counts = _parse_xyz_element_counts(frames[0])
    for frame in frames[1:]:
        assert _parse_xyz_element_counts(frame) == first_counts


def test_v1_molecule_resolve_smiles(client: TestClient) -> None:
    response = client.post("/api/v1/molecule/resolve", json={"smiles": "CCO"})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["smiles"] == "CCO"
    assert body["formula"] == "C2H6O"
    assert body["source"] == "smiles"


def test_v1_molecule_resolve_invalid(client: TestClient) -> None:
    response = client.post("/api/v1/molecule/resolve", json={"smiles": "not-a-smiles"})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["error"]


def test_v1_molecule_embed(client: TestClient) -> None:
    response = client.post("/api/v1/molecule/embed", json={"smiles": "CCO"})
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["formula"] == "C2H6O"
    assert body["num_atoms"] == 9
    assert _parse_xyz_element_counts(body["xyz"]) == {"C": 2, "H": 6, "O": 1}


def test_v1_molecule_embed_molfile(client: TestClient) -> None:
    from rdkit import Chem as _Chem

    mol = _Chem.AddHs(_Chem.MolFromSmiles("CCO"))
    molfile = _Chem.MolToMolBlock(mol)
    response = client.post("/api/v1/molecule/embed", json={"molfile": molfile})
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["formula"] == "C2H6O"
    assert _parse_xyz_element_counts(body["xyz"]) == {"C": 2, "H": 6, "O": 1}


def test_v1_molecule_embed_invalid(client: TestClient) -> None:
    response = client.post("/api/v1/molecule/embed", json={"smiles": "not-a-smiles"})
    assert response.status_code == 200
    body = response.json()
    assert body["error"] and "Invalid SMILES" in body["error"]


def test_v1_old_api_still_works(client: TestClient) -> None:
    status = client.get("/api/status")
    assert status.status_code == 200

    project = _create_project(client, name="Legacy")
    created = client.post(
        "/api/jobs",
        json={
            "workflow": "fake",
            "name": "legacy-job",
            "input": {"source": "CCO"},
            "project_id": project["project_id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == project["project_id"]

    detail = client.get(f"/api/jobs/{created.json()['job_id']}")
    assert detail.status_code == 200
    assert detail.json()["project_id"] == project["project_id"]
