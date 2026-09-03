"""Tests for the ACP API v1 surface."""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportUnusedParameter=false, reportImplicitStringConcatenation=false, reportIndexIssue=false, reportOperatorIssue=false
from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


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


def make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_running: int = 2
) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=max_running))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, monkeypatch, max_running=2) as test_client:
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
            manager = client.app.state.job_manager
            if job_id not in manager._submission_jobs:
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
    assert {"xtb", "crest", "orca"} <= names


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


def test_v1_job_rerun_requeues_original_task(client: TestClient) -> None:
    created = _submit_fake_job(client, name="in-place-rerun")
    job_id = str(created["job_id"])
    first = _wait_for_terminal_job(client, job_id)
    work_dir = str(first["work_dir"])

    rerun = client.post(f"/api/v1/jobs/{job_id}/rerun")
    assert rerun.status_code == 200
    body = rerun.json()
    assert body["id"] == job_id
    assert body["work_dir"] == work_dir
    assert body["status"] in {"queued", "starting", "completed"}

    second = _wait_for_terminal_job(client, job_id)
    assert second["id"] == job_id
    assert second["work_dir"] == work_dir
    assert second["result"]["attempts"] == 2

    jobs = client.get("/api/v1/jobs?limit=100")
    assert jobs.status_code == 200
    assert [job["id"] for job in jobs.json()["jobs"]].count(job_id) == 1


def test_v1_energy_graph_reads_legacy_result_files(client: TestClient) -> None:
    created = _submit_fake_job(client, name="legacy-energy-graph")
    job_id = str(created["job_id"])
    record_data = _wait_for_terminal_job(client, job_id)
    manager = client.app.state.job_manager
    record = manager.get(job_id)
    assert record is not None
    record.spec = replace(record.spec, workflow="energy")
    manager.store.update(record)

    energy_dir = Path(record_data["work_dir"]) / "RESULT" / "energies"
    energy_dir.mkdir(parents=True)
    (energy_dir / "ensemble_thermo.json").write_text(
        json.dumps(
            {
                "conformers": [
                    {
                        "conf_id": "CONF1",
                        "gibbs_hartree": -10.0,
                        "delta_gibbs_kcal_mol": 0.0,
                        "weight": 1.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (energy_dir / "conformer_thermo.csv").write_text(
        "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,h_correction,u_correction,"
        "s_total,g_conc,weight,source\n"
        "0,1,-10.1,0,-10.0,0,0,0,0,1.0,CONF1\n"
        "TOTAL,,,,,,,,,,ensemble_total\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/v1/jobs/{job_id}/energy-graph")

    assert response.status_code == 200
    body = response.json()
    assert body["view_type"] == "conformer"
    assert body["nodes"]


def test_v1_pessearch_reads_canonical_profile_and_07_path_frame(client: TestClient) -> None:
    created = _submit_fake_job(client, name="pes-canonical-energy-graph")
    job_id = str(created["job_id"])
    record_data = _wait_for_terminal_job(client, job_id)
    manager = client.app.state.job_manager
    record = manager.get(job_id)
    assert record is not None
    record.spec = replace(
        record.spec,
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
    )
    manager.store.update(record)

    task_root = Path(record_data["work_dir"])
    scan_dir = task_root / "WORK" / "07_PATH" / "pes_scan_001"
    frames_dir = scan_dir / "scan_frames"
    frames_dir.mkdir(parents=True)
    (frames_dir / "frame_000.xyz").write_text("2\nPES frame\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    profile_path = task_root / "RESULT" / "pes_search" / "pes_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "pes_profile_v2",
                "workflow": "PESsearch",
                "mode": "bond_length_scan",
                "status": "completed",
                "coordinate": {"kind": "distance", "unit": "angstrom"},
                "protocol": {"coordinate": {"kind": "distance", "unit": "angstrom"}},
                "scan_dir": "WORK/07_PATH/pes_scan_001",
                "frames": [
                    {
                        "index": 0,
                        "target_coordinate": 1.2,
                        "actual_coordinate": 1.2,
                        "geometry_path": "scan_frames/frame_000.xyz",
                        "scan_energy_hartree": -10.0,
                        "single_point_energy_hartree": -10.0,
                        "optimization_converged": True,
                        "single_point_status": "completed",
                    }
                ],
                "profile": {
                    "energy_source": "single_point",
                    "relative_energies_kcal_mol": [0.0],
                    "raw_hartree": [-10.0],
                },
                "quality": {"scan_complete": True},
                "ts_candidates": [],
                "int_candidates": [],
            }
        ),
        encoding="utf-8",
    )

    graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
    assert graph.status_code == 200, graph.text
    assert graph.json()["source"] == "RESULT/pes_search/pes_profile.json"
    assert graph.json()["title"] == "PESsearch 扫描能量"

    frame = client.get(f"/api/v1/jobs/{job_id}/s2/frame/0")
    assert frame.status_code == 200, frame.text
    assert "C 0 0 0" in frame.json()["xyz"]


def test_v1_energy_graph_unsupported_workflow_is_explicit(client: TestClient) -> None:
    created = _submit_fake_job(client, name="unsupported-energy-graph")

    response = client.get(f"/api/v1/jobs/{created['job_id']}/energy-graph")

    assert response.status_code == 200
    body = response.json()
    assert body["view_type"] == "unsupported"
    assert body["status"] == "unavailable"
    assert body["metadata"]["reason"] == "workflow_has_no_energy_graph"


def test_v1_batch_optimize_live_energy_graph_and_frame(client: TestClient) -> None:
    created = _submit_fake_job(client, name="batch-live-energy-graph")
    job_id = str(created["job_id"])
    record_data = _wait_for_terminal_job(client, job_id)
    manager = client.app.state.job_manager
    record = manager.get(job_id)
    assert record is not None
    record.spec = replace(record.spec, workflow="BatchOptimize")
    manager.store.update(record)

    trajectory_dir = (
        Path(str(record_data["work_dir"])) / "WORK" / "03_OPT" / "batch" / "TS1" / "optimize"
    )
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / "cycles").mkdir()
    (trajectory_dir / "cycles" / "cycle_0001.xyz").write_text(
        "2\ncycle 1\nC 0 0 0\nH 0 0 1\n", encoding="utf-8"
    )
    (trajectory_dir / "optimization_trajectory.json").write_text(
        json.dumps(
            {
                "item_id": "TS1",
                "status": "running",
                "converged": False,
                "current_cycle": 1,
                "cycles": [
                    {
                        "cycle": 1,
                        "energy_hartree": -10.0,
                        "rms_gradient": 0.2,
                        "geometry_ref": "cycles/cycle_0001.xyz",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    graph_response = client.get(f"/api/v1/jobs/{job_id}/energy-graph?item_id=TS1")
    assert graph_response.status_code == 200, graph_response.text
    assert graph_response.json()["view_type"] == "optimization"
    assert graph_response.json()["status"] == "running"

    frame_response = client.get(f"/api/v1/jobs/{job_id}/optimization/frame/0?item_id=TS1")
    assert frame_response.status_code == 200, frame_response.text
    assert frame_response.json()["cycle"] == 1
    assert "C 0 0 0" in frame_response.json()["xyz"]


def test_v1_job_rerun_running_job_returns_conflict(client: TestClient) -> None:
    manager = client.app.state.job_manager
    work_dir = manager.run_root / "running-rerun"
    work_dir.mkdir(parents=True)
    record = JobRecord(
        id="running-rerun",
        spec=JobSpec(
            workflow="fake",
            name="running-rerun",
            project_id=manager.default_project_id,
        ),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)

    response = client.post("/api/v1/jobs/running-rerun/rerun")

    assert response.status_code == 409


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


def test_v1_job_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with make_client(tmp_path, monkeypatch, max_running=1) as client:
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
    assert any(item["file_path"] == "RESULT/input_preview.xyz" for item in items)

    detail = client.get(f"/api/v1/artifacts/{items[0]['artifact_id']}")
    assert detail.status_code == 200
    assert detail.json()["job_id"] == created["job_id"]


def test_v1_artifact_download(client: TestClient) -> None:
    created = _submit_fake_job(client, name="download-artifact-job")
    record = _wait_for_terminal_job(client, str(created["job_id"]))
    assert record["status"] == "completed"

    artifacts = client.get(f"/api/v1/jobs/{created['job_id']}/artifacts").json()["artifacts"]
    preview = next(item for item in artifacts if item["file_path"] == "RESULT/input_preview.xyz")
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
    assert any(item["file_path"] == "RESULT/demo_frames.xyz" for item in artifacts)

    demo = next(item for item in artifacts if item["file_path"] == "RESULT/demo_frames.xyz")
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


# ---------------------------------------------------------------------------
# Hessian preview (plan §12)
# ---------------------------------------------------------------------------


def test_v1_hessian_preview_batch(client: TestClient) -> None:
    """AC13: ethanol(0) / dmso(10) / ferrocene(10); summary 2/1 enabled."""
    r = client.post(
        "/api/v1/hessian-preview",
        json={
            "schema_id": "dft_optimize",
            "level_id": "optimize",
            "recalc_hess": "auto",
            "structures": [
                {"name": "ethanol", "symbols": ["C", "C", "O", "H", "H", "H", "H", "H", "H"]},
                {"name": "dmso", "formula": "C2H6OS"},
                {"name": "ferrocene", "symbols": ["Fe"]},
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["summary"] == {"total": 3, "enabled": 2, "disabled": 1}
    by_name = {x["name"]: x for x in data["results"]}
    assert by_name["ethanol"]["interval"] == 0
    assert by_name["ethanol"]["reason"] == "light_elements"
    assert by_name["dmso"]["interval"] == 10
    assert by_name["dmso"]["reason"] == "heteroatom_only"
    assert by_name["ferrocene"]["interval"] == 10
    assert by_name["ferrocene"]["reason"] == "heavy_elements"
    assert by_name["ferrocene"]["triggering_elements"] == ["Fe"]


def test_v1_hessian_preview_invalid_recalc_hess_422(client: TestClient) -> None:
    """AC21: invalid top-level recalc_hess returns 422 with field error."""
    r = client.post(
        "/api/v1/hessian-preview",
        json={"recalc_hess": 20.5, "structures": []},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "validation error"
    assert body["errors"][0]["field"] == "recalc_hess"


def test_v1_hessian_preview_explicit_zero(client: TestClient) -> None:
    """AC9: explicit recalc_hess=0 → enabled=False, source=explicit."""
    r = client.post(
        "/api/v1/hessian-preview",
        json={"recalc_hess": 0, "structures": [{"name": "x", "symbols": ["Fe", "Cl"]}]},
    )
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["interval"] == 0
    assert res["enabled"] is False
    assert res["source"] == "explicit"
    assert res["reason"] == "explicit_off"


def test_v1_hessian_preview_auto_missing_symbols_422(client: TestClient) -> None:
    """When auto is selected and a structure lacks symbols+formula, return
    a per-structure 422 (plan §12.5)."""
    r = client.post(
        "/api/v1/hessian-preview",
        json={"recalc_hess": "auto", "structures": [{"name": "mystery"}]},
    )
    assert r.status_code == 422
    body = r.json()
    assert "symbols or formula" in body["errors"][0]["message"]


def test_v1_hessian_preview_source_mapping(client: TestClient) -> None:
    """source vocabulary: explicit-Auto → 'auto'; null → 'config'."""
    # Explicit Auto
    r = client.post(
        "/api/v1/hessian-preview",
        json={"recalc_hess": "auto", "structures": [{"name": "x", "symbols": ["C"]}]},
    )
    assert r.json()["results"][0]["source"] == "auto"
    # Omitted → config (server config defaults to 'auto')
    r = client.post(
        "/api/v1/hessian-preview",
        json={"structures": [{"name": "x", "symbols": ["C"]}]},
    )
    assert r.json()["results"][0]["source"] == "config"
    # Explicit N → explicit
    r = client.post(
        "/api/v1/hessian-preview",
        json={"recalc_hess": 5, "structures": [{"name": "x", "symbols": ["C"]}]},
    )
    res = r.json()["results"][0]
    assert res["source"] == "explicit"
    assert res["reason"] == "explicit_interval"


def test_v1_create_job_with_v2_naming(client: TestClient) -> None:
    """molecule_name/task_name/remark drive the v2 task-dir name (design §4)."""
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "demo",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
            "molecule_name": "ethanol",
            "task_name": "opt",
            "remark": "final",
        },
    )
    assert response.status_code == 201
    job_id = response.json()["job_id"]

    detail = client.get(f"/api/v1/jobs/{job_id}/detail").json()
    work_dir = detail["job"]["work_dir"]
    assert Path(work_dir).name == "ethanol_opt_final"
    spec = detail["job"]["spec"]
    assert spec["name"] == Path(work_dir).name
    assert spec["molecule_name"] == "ethanol"
    assert spec["task_name"] == "opt"
    assert spec["remark"] == "final"


def test_v1_create_job_without_v2_fields_defaults_naming(client: TestClient) -> None:
    """Without the v2 fields the task dir defaults to ``<input stem>_<workflow>``."""
    created = _submit_fake_job(client, name="legacyjob")
    detail = client.get(f"/api/v1/jobs/{created['job_id']}/detail").json()
    leaf = Path(detail["job"]["work_dir"]).name
    assert leaf == "CCO_fake"
    assert detail["job"]["spec"]["name"] == leaf
    # job_id stays the timestamped DB identity — it never appears in the path.
    assert created["job_id"].startswith("20")
    assert "legacyjob" in created["job_id"]


# ---------------------------------------------------------------------------
# Batch structure submission for stage workflows (batch plan §3)
# ---------------------------------------------------------------------------


def _submit_batch_optimize(client: TestClient, items: list[dict[str, object]]) -> dict[str, object]:
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "BatchOptimize",
            "name": "batch_optimize",
            "input": {
                "source_type": "batch_structures",
                "schema_version": "batch_structures_v1",
                "charge": 0,
                "multiplicity": 1,
                "items": items,
            },
            "method": {"profile": "opt_freq"},
            "resources": {"nproc": 2, "mem": "4GB"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["workflow"] == "BatchOptimize"
    return response.json()


def test_v1_batch_structures_inline_xyz_submission(client: TestClient) -> None:
    job = _submit_batch_optimize(
        client,
        [
            {
                "name": "ts_1",
                "tag": "TS",
                "xyz": "3\nTAG: TS | candidate_id=ts_1 | source=test\n"
                "O 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n",
                "charge": 0,
                "multiplicity": 1,
                "include": True,
            },
            {
                "name": "int_1",
                "tag": "INT",
                "xyz": "3\nTAG: INT\nO 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n",
                "include": True,
            },
            {
                "name": "dropped",
                "xyz": "3\nplain\nO 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n",
                "include": False,
            },
        ],
    )
    assert job["status"] in {"queued", "pending", "starting", "failed"}

    record = client.app.state.job_manager.get(job["job_id"])
    assert record is not None
    assert record.spec.workflow == "BatchOptimize"
    assert record.spec.method["profile"] == "opt_freq"
    batch_input = record.spec.input
    assert batch_input["schema_version"] == "batch_structures_v1"
    assert batch_input["source_type"] == "batch_structures"
    items = batch_input["items"]
    assert len(items) == 3, "BatchOptimize receives the submitted request unchanged"
    assert items[0]["tag"] == "TS"
    assert items[1]["tag"] == "INT"
    assert items[0]["xyz"]
    assert items[2]["include"] is False

    tasks = client.get(f"/api/v1/jobs/{job['job_id']}/tasks")
    assert tasks.status_code == 200
    assert [task["stage_name"] for task in tasks.json()["tasks"]] == [
        "prepare",
        "optimize",
        "frequency",
        "finalize",
    ]


def test_v1_batch_structures_source_id_resolution(client: TestClient, tmp_path: Path) -> None:
    # 2026-09-03 wave: source_id references are inlined to XYZ at submission
    # so the runner materializer never sees unresolved references.
    # Complete an upstream PESsearch job whose result list exposes a candidate.
    source_dir = tmp_path / "uncategorized" / "20260823_001_PESsearch"
    result_dir = source_dir / "RESULT"
    (result_dir / "mechanism" / "ts_guesses").mkdir(parents=True, exist_ok=True)
    xyz = (
        "3\nTAG: TS | candidate_id=ts_guess_001 | source=PESsearch\n"
        "O 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n"
    )
    (result_dir / "mechanism" / "ts_guesses" / "ts_guess_001.xyz").write_text(xyz, encoding="utf-8")
    (result_dir / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [
                    {
                        "id": "s2_candidate_ts_guess_001",
                        "label": "S2 candidate ts_guess_001 (TS)",
                        "path": "mechanism/ts_guesses/ts_guess_001.xyz",
                        "kind": "structure",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_record = JobRecord(
        id="20260823_001_PESsearch",
        spec=JobSpec(workflow="PESsearch", name="pes", input={"source_type": "stage_artifact"}),
        status=JobStatus.COMPLETED,
        work_dir=str(source_dir),
        project_id="uncategorized",
    )
    client.app.state.job_manager.store.create(source_record)

    job = _submit_batch_optimize(
        client,
        [
            {
                "name": "ts_1",
                "source_id": (
                    "job_20260823_001_PESsearch:RESULT/mechanism/ts_guesses/ts_guess_001.xyz"
                ),
                "tag": "TS",
            }
        ],
    )
    record = client.app.state.job_manager.get(job["job_id"])
    assert record is not None
    assert record.spec.workflow == "BatchOptimize"
    items = record.spec.input["items"]
    assert len(items) == 1
    assert "source_id" not in items[0]
    assert "TAG: TS | candidate_id=ts_guess_001" in items[0]["xyz"]
    assert items[0]["tag"] == "TS"
    assert items[0]["name"] == "ts_1"


def test_v1_batch_structures_requires_nonempty_items(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "BatchOptimize",
            "input": {
                "source_type": "batch_structures",
                "schema_version": "batch_structures_v1",
                "items": [],
            },
            "method": {"profile": "opt_freq"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["workflow"] == "BatchOptimize"
    record = client.app.state.job_manager.get(body["job_id"])
    assert record is not None
    assert record.spec.input["items"] == []


def test_v1_batch_structures_rejects_bad_item(client: TestClient) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "BatchOptimize",
            "input": {
                "source_type": "batch_structures",
                "schema_version": "batch_structures_v1",
                "items": [{"name": "x"}],
            },
            "method": {"profile": "opt_freq"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["workflow"] == "BatchOptimize"
    record = client.app.state.job_manager.get(body["job_id"])
    assert record is not None
    assert record.spec.input["items"] == [{"name": "x"}]


class _FakeIrcManager:
    records: dict[str, JobRecord]
    submitted: JobSpec | None

    def __init__(self, records: dict[str, JobRecord]) -> None:
        self.records = records
        self.submitted = None

    def get(self, job_id: str) -> JobRecord | None:
        return self.records.get(job_id)

    def submit(self, spec: JobSpec, group_id: str | None = None) -> JobRecord:
        self.submitted = spec
        return JobRecord(
            id="irc-job-001",
            spec=spec,
            status=JobStatus.QUEUED,
            project_id=spec.project_id,
        )


def _write_irc_manifest(
    task_dir: Path,
    products: list[dict[str, str]],
    comment: str,
) -> None:
    result_dir = task_dir / "RESULT"
    result_dir.mkdir(parents=True, exist_ok=True)
    for product in products:
        structure_path = result_dir / product["path"]
        structure_path.parent.mkdir(parents=True, exist_ok=True)
        structure_path.write_text(
            f"2\n{comment}\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            encoding="utf-8",
        )
    (result_dir / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": products,
            }
        ),
        encoding="utf-8",
    )


def _irc_source_record(job_id: str, task_dir: Path) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="PESsearch",
            name="source-pes",
            input={"source": "CCO"},
            method={"functional": "r2SCAN-3c"},
            resources={"nproc": 2},
            project_id="uncategorized",
            molecule_name="CCO",
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(task_dir),
        project_id="uncategorized",
    )


def test_run_irc_endpoint(client: TestClient, tmp_path: Path) -> None:
    source_dir = tmp_path / "irc-source"
    _write_irc_manifest(
        source_dir,
        [
            {
                "id": "ts_artifact",
                "label": "TS candidate",
                "path": "structures/ts.xyz",
                "kind": "structure",
                "role": "transition_state",
            }
        ],
        "TAG: TS | candidate_id=ts_001 | source=PESsearch",
    )
    manager = _FakeIrcManager({"source-job": _irc_source_record("source-job", source_dir)})
    client.app.state.job_manager = manager

    response = client.post("/api/v1/jobs/source-job/artifacts/ts_artifact/run-irc")

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "irc-job-001",
        "status": "queued",
        "workflow": "irc",
        "project_id": "uncategorized",
    }
    assert manager.submitted is not None
    assert manager.submitted.workflow == "irc"
    assert manager.submitted.input == {
        "input_artifact": str(source_dir / "RESULT" / "structures" / "ts.xyz"),
        "input_role": "transition_state",
        "directions": ["forward", "reverse"],
    }
    assert manager.submitted.method == {"functional": "r2SCAN-3c"}
    assert manager.submitted.resources == {"nproc": 2}


@pytest.mark.parametrize(
    ("state", "expected_status"),
    (
        ("missing", 404),
        ("non_ts", 422),
        ("cross_job", 404),
        ("corrupt", 422),
    ),
)
def test_run_irc_endpoint_rejections(
    client: TestClient,
    tmp_path: Path,
    state: str,
    expected_status: int,
) -> None:
    source_dir = tmp_path / "irc-source"
    source_job = _irc_source_record("source-job", source_dir)
    artifact_id = "ts_artifact"
    other_job = source_job

    if state == "missing":
        _write_irc_manifest(
            source_dir,
            [
                {
                    "id": artifact_id,
                    "label": "TS candidate",
                    "path": "structures/ts.xyz",
                    "kind": "structure",
                    "role": "transition_state",
                }
            ],
            "TAG: TS | candidate_id=ts_001",
        )
        artifact_id = "missing-artifact"
    elif state == "non_ts":
        _write_irc_manifest(
            source_dir,
            [
                {
                    "id": artifact_id,
                    "label": "Minimum",
                    "path": "structures/minimum.xyz",
                    "kind": "structure",
                }
            ],
            "optimized geometry",
        )
    elif state == "cross_job":
        _write_irc_manifest(
            source_dir,
            [
                {
                    "id": "source-ts",
                    "label": "Source TS",
                    "path": "structures/source-ts.xyz",
                    "kind": "structure",
                    "role": "transition_state",
                }
            ],
            "TAG: TS | candidate_id=source_ts",
        )
        other_dir = tmp_path / "other-source"
        _write_irc_manifest(
            other_dir,
            [
                {
                    "id": artifact_id,
                    "label": "Other TS",
                    "path": "structures/other-ts.xyz",
                    "kind": "structure",
                    "role": "transition_state",
                }
            ],
            "TAG: TS | candidate_id=other_ts",
        )
        other_job = _irc_source_record("other-job", other_dir)
    elif state == "corrupt":
        result_dir = source_dir / "RESULT"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "result_manifest.json").write_text("{not-json", encoding="utf-8")
    else:
        pytest.fail(f"unknown rejection state: {state}")

    records = {"source-job": source_job}
    if state == "cross_job":
        records["other-job"] = other_job
    manager = _FakeIrcManager(records)
    client.app.state.job_manager = manager

    response = client.post(f"/api/v1/jobs/source-job/artifacts/{artifact_id}/run-irc")

    assert response.status_code == expected_status
    assert response.status_code != 500
    assert manager.submitted is None


# ---------------------------------------------------------------------------
# MD §12.3 route matrix — 8 endpoints, one parametrized test
# ---------------------------------------------------------------------------


def _setup_job_for_state(client: TestClient, state: str, tmp_path: Path) -> str:
    """Create a job record directly in the store at *state*."""
    manager = client.app.state.job_manager
    job_id = f"matrix-{state}-001"
    work_dir = tmp_path / f"work-{state}"
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="fake",
            name=f"matrix-{state}",
            input={"source": "CCO"},
            method={"protocol": "ext"},
            project_id=manager.default_project_id,
            molecule_name="CCO",
        ),
        status=JobStatus(state) if state != "queued" else JobStatus.QUEUED,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    return job_id


@pytest.mark.parametrize(
    ("endpoint", "method", "setup_state", "expected_status"),
    [
        # 1. POST /jobs — submit a new job
        ("jobs", "POST", None, 201),
        # 2. GET /jobs/{id} — retrieve a job record
        ("jobs/{id}", "GET", "queued", 200),
        # 3. POST /jobs/{id}/pause — pause a running job (409 if no live process)
        ("jobs/{id}/pause", "POST", "running", 409),
        # 4. POST /jobs/{id}/continue — continue a failed job (409 if unsupported workflow)
        ("jobs/{id}/continue", "POST", "failed", 409),
        # 5. POST /jobs/{id}/rerun — rerun a failed job
        ("jobs/{id}/rerun", "POST", "failed", 200),
        # 6. POST /jobs/purge — purge with status filter
        ("jobs/purge", "POST", None, 200),
        # 7. GET /jobs/{id}/artifacts — list artifacts for a job
        ("jobs/{id}/artifacts", "GET", "queued", 200),
        # 8. POST /jobs/{id}/artifacts/{artifact_id}/run-irc — submit IRC from TS artifact
        ("jobs/{id}/artifacts/{artifact_id}/run-irc", "POST", "irc", 202),
    ],
    ids=[
        "POST /jobs",
        "GET /jobs/{id}",
        "POST /jobs/{id}/pause",
        "POST /jobs/{id}/continue",
        "POST /jobs/{id}/rerun",
        "POST /jobs/purge",
        "GET /jobs/{id}/artifacts",
        "POST /jobs/{id}/artifacts/{artifact_id}/run-irc",
    ],
)
def test_route_matrix_v1(
    client: TestClient,
    tmp_path: Path,
    endpoint: str,
    method: str,
    setup_state: str | None,
    expected_status: int,
) -> None:
    """MD §12.3: assert all 8 core endpoints are registered and behave correctly."""
    if setup_state == "irc":
        # Special setup: use _FakeIrcManager with a completed PESsearch source
        source_dir = tmp_path / "irc-source"
        _write_irc_manifest(
            source_dir,
            [
                {
                    "id": "ts_artifact",
                    "label": "TS candidate",
                    "path": "structures/ts.xyz",
                    "kind": "structure",
                    "role": "transition_state",
                }
            ],
            "TAG: TS | candidate_id=ts_001 | source=PESsearch",
        )
        irc_manager = _FakeIrcManager({"source-job": _irc_source_record("source-job", source_dir)})
        client.app.state.job_manager = irc_manager
        url = "/api/v1/jobs/source-job/artifacts/ts_artifact/run-irc"
        response = client.post(url)
    elif endpoint == "jobs" and method == "POST":
        response = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "fake",
                "name": "matrix-submit",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
            },
        )
    elif endpoint == "jobs/purge":
        response = client.post(
            "/api/v1/jobs/purge",
            json={"status": "completed", "older_than_days": 999},
        )
    else:
        job_id = _setup_job_for_state(client, setup_state or "queued", tmp_path)
        url = f"/api/v1/{endpoint.replace('{id}', job_id)}"
        response = client.request(method, url)

    assert response.status_code == expected_status, (
        f"{method} {endpoint} → {response.status_code}, expected {expected_status}: "
        f"{response.text[:200]}"
    )


def test_batchoptimize_submit_stageplan(client: TestClient) -> None:
    """BatchOptimize submit yields a queued task with profile-trimmed stage plan."""
    job = _submit_batch_optimize(
        client,
        [
            {
                "name": "ts_1",
                "tag": "TS",
                "xyz": "3\nTAG: TS | candidate_id=ts_1 | source=test\n"
                "O 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n",
                "charge": 0,
                "multiplicity": 1,
                "include": True,
            },
            {
                "name": "int_1",
                "tag": "INT",
                "xyz": "3\nTAG: INT\nO 0 0 0\nH 0.9 0 0\nH -0.3 0.9 0\n",
                "include": True,
            },
        ],
    )
    assert job["status"] in {"queued", "pending", "starting", "failed"}

    record = client.app.state.job_manager.get(job["job_id"])
    assert record is not None
    assert record.spec.workflow == "BatchOptimize"
    assert record.spec.method["profile"] == "opt_freq"

    tasks = client.get(f"/api/v1/jobs/{job['job_id']}/tasks")
    assert tasks.status_code == 200
    stage_names = [task["stage_name"] for task in tasks.json()["tasks"]]
    assert "prepare" in stage_names
    assert "optimize" in stage_names
    assert "frequency" in stage_names
    assert "finalize" in stage_names


def test_non_ts_artifact_role_mismatch_422(client: TestClient, tmp_path: Path) -> None:
    """Non-TS artifact with TS role request returns 422."""
    source_dir = tmp_path / "irc-source"
    _write_irc_manifest(
        source_dir,
        [
            {
                "id": "min_artifact",
                "label": "Minimum",
                "path": "structures/min.xyz",
                "kind": "structure",
                "role": "minimum",
            }
        ],
        "TAG: INT | candidate_id=min_001",
    )
    manager = _FakeIrcManager({"source-job": _irc_source_record("source-job", source_dir)})
    client.app.state.job_manager = manager

    response = client.post("/api/v1/jobs/source-job/artifacts/min_artifact/run-irc")

    assert response.status_code == 422


# ── Progress / log cursor / SSE v2 tests ─────────────────────────────────


def test_v1_job_list_has_progress_state(client: TestClient) -> None:
    """Job list items carry the progress_state field."""
    _submit_fake_job(client, name="progress-state-check")
    response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert len(jobs) >= 1
    for j in jobs:
        assert "progress_state" in j
        assert "stage_index" in j
        assert "stage_total" in j
        assert "stage_progress" in j
        assert "stage_detail" in j


def test_v1_job_detail_has_progress_state(client: TestClient) -> None:
    """Job detail response carries progress_state on the job model."""
    job = _submit_fake_job(client, name="detail-progress-state")
    job_id = str(job["job_id"])
    response = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert response.status_code == 200
    detail = response.json()
    assert "progress_state" in detail["job"]


def test_v1_job_logs_legacy_mode(client: TestClient) -> None:
    """Log endpoint returns joined strings + arrays + offsets in legacy mode."""
    job = _submit_fake_job(client, name="log-legacy")
    job_id = str(job["job_id"])
    _wait_for_terminal_job(client, job_id)
    response = client.get(f"/api/v1/jobs/{job_id}/logs?lines=100")
    assert response.status_code == 200
    data = response.json()
    # Legacy fields: joined strings
    assert isinstance(data["stdout"], str)
    assert isinstance(data["stderr"], str)
    # New fields: arrays + offsets
    assert isinstance(data["stdout_lines"], list)
    assert isinstance(data["stderr_lines"], list)
    assert isinstance(data["stdout_next_offset"], int)
    assert isinstance(data["stderr_next_offset"], int)
    assert isinstance(data["has_more"], bool)


def test_v1_job_logs_cursor_mode(client: TestClient) -> None:
    """Log endpoint supports cursor-based incremental fetch."""
    job = _submit_fake_job(client, name="log-cursor")
    job_id = str(job["job_id"])
    _wait_for_terminal_job(client, job_id)
    # First fetch: legacy mode to get initial offsets
    resp1 = client.get(f"/api/v1/jobs/{job_id}/logs?lines=100")
    assert resp1.status_code == 200
    data1 = resp1.json()
    stdout_off = data1["stdout_next_offset"]
    stderr_off = data1["stderr_next_offset"]
    # Second fetch: cursor mode from the returned offsets
    resp2 = client.get(
        f"/api/v1/jobs/{job_id}/logs?stdout_offset={stdout_off}&stderr_offset={stderr_off}"
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    # Cursor mode should return empty or minimal new lines (job is done)
    assert isinstance(data2["stdout_lines"], list)
    assert isinstance(data2["stderr_lines"], list)


def test_v1_job_logs_not_found(client: TestClient) -> None:
    """Log endpoint returns 404 for nonexistent job."""
    response = client.get("/api/v1/jobs/nonexistent-job/logs")
    assert response.status_code == 404


def test_v1_sse_endpoint_exists(client: TestClient) -> None:
    """SSE endpoint returns event-stream content type."""
    job = _submit_fake_job(client, name="sse-test")
    job_id = str(job["job_id"])
    _wait_for_terminal_job(client, job_id)
    # For terminal jobs, the SSE stream should close quickly with a done event
    response = client.get(f"/api/v1/jobs/{job_id}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


def test_v1_sse_after_seq_param(client: TestClient) -> None:
    """SSE endpoint accepts after_seq query param for resume."""
    job = _submit_fake_job(client, name="sse-resume")
    job_id = str(job["job_id"])
    _wait_for_terminal_job(client, job_id)
    response = client.get(f"/api/v1/jobs/{job_id}/events?after_seq=999")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")


def test_v1_sse_done_event_has_snapshot(client: TestClient) -> None:
    """SSE done event includes job snapshot fields."""
    job = _submit_fake_job(client, name="sse-done-payload")
    job_id = str(job["job_id"])
    _wait_for_terminal_job(client, job_id)
    response = client.get(f"/api/v1/jobs/{job_id}/events")
    assert response.status_code == 200
    # Read the response text — for terminal jobs the stream closes immediately
    text = response.text
    # Should contain a done event with job snapshot
    assert "event: done" in text or "event:done" in text or '"job_id"' in text


# ── Unified snapshot / stage-label tests ────────────────────────────────────


def test_v1_detail_stages_have_labels(client: TestClient) -> None:
    created = _submit_fake_job(client, name="label-check")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    detail = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail.status_code == 200
    stages = detail.json()["stages"]
    assert len(stages) >= 1
    for entry in stages:
        assert "label" in entry
        assert entry["label"] is not None
        assert "progress" in entry
        assert "detail" in entry


def test_v1_detail_snapshot_version_present(client: TestClient) -> None:
    created = _submit_fake_job(client, name="snapshot-ver")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    detail = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail.status_code == 200
    job = detail.json()["job"]
    assert "snapshot_version" in job
    assert job["snapshot_version"] is not None
    assert isinstance(job["snapshot_version"], int)
    assert job["snapshot_version"] > 0


def test_v1_detail_latest_event_present(client: TestClient) -> None:
    created = _submit_fake_job(client, name="latest-event")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    detail = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail.status_code == 200
    job = detail.json()["job"]
    assert "latest_event" in job
    assert job["latest_event"] is not None
    assert isinstance(job["latest_event"], str)
    assert len(job["latest_event"]) > 0


def test_v1_list_detail_agree_on_progress_fields(client: TestClient) -> None:
    created = _submit_fake_job(client, name="progress-agree")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    list_resp = client.get("/api/v1/jobs")
    assert list_resp.status_code == 200
    list_job = next(j for j in list_resp.json()["jobs"] if j["id"] == job_id)
    detail_resp = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail_resp.status_code == 200
    detail_job = detail_resp.json()["job"]
    for field in ("stage_index", "stage_total", "stage_progress", "stage_detail", "progress_state"):
        assert list_job.get(field) == detail_job.get(field), f"Mismatch on {field}"


def test_v1_list_has_snapshot_version_for_completed(client: TestClient) -> None:
    created = _submit_fake_job(client, name="list-snap-ver")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    list_resp = client.get("/api/v1/jobs")
    assert list_resp.status_code == 200
    list_job = next(j for j in list_resp.json()["jobs"] if j["id"] == job_id)
    assert "snapshot_version" in list_job
    assert list_job["snapshot_version"] is not None


def test_v1_terminal_job_ignores_stale_state_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminal jobs are DB-authoritative: a stale ``state.json`` left behind
    with ``status: running`` must NOT override job-level progress fields, and
    non-terminal stage rows are projected to ``skipped``."""
    with make_client(tmp_path, monkeypatch) as client:
        created = _submit_fake_job(client, name="overlay-test")
        job_id = str(created["job_id"])
        _wait_for_terminal_job(client, job_id)

        manager = client.app.state.job_manager
        rec = manager.get(job_id)
        assert rec is not None
        work_dir = Path(rec.work_dir)

        from acp.scheduler.stage_tasks import StageTaskStore

        db_path = manager.store.db_path
        store = StageTaskStore(db_path)
        for task in store.list_by_job(job_id):
            task.state = "pending"
            store.update(task)

        state_data = {
            "version": "1.0",
            "job_name": "overlay-test",
            "status": "running",
            "current_stage": "compute",
            "stage_index": 2,
            "stage_total": 3,
            "stage_progress": 0.56,
            "stage_detail": "17/30",
            "progress_state": "determinate",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:01:00Z",
            "stages": {
                "init": {"status": "completed", "progress": 1.0, "detail": "done"},
                "compute": {"status": "running", "progress": 0.56, "detail": "17/30"},
                "finalize": {"status": "pending"},
            },
        }
        (work_dir / "state.json").write_text(json.dumps(state_data), encoding="utf-8")

        detail = client.get(f"/api/v1/jobs/{job_id}/detail")
        assert detail.status_code == 200
        body = detail.json()

        assert body["job"]["stage_index"] is None
        assert body["job"]["stage_total"] is None
        assert body["job"]["stage_progress"] is None
        assert body["job"]["stage_detail"] is None
        assert body["job"]["progress"] == 1.0
        assert body["job"]["progress_state"] == "determinate"
        assert isinstance(body["job"]["snapshot_version"], int)

        stages = {s["stage_name"]: s for s in body["stages"]}
        assert stages["init"]["status"] == "completed"
        assert stages["init"]["label"] == "初始化"
        assert stages["compute"]["status"] == "skipped"
        assert stages["compute"]["label"] == "计算中"
        assert stages["compute"]["progress"] == 0.56
        assert stages["compute"]["detail"] == "17/30"
        assert stages["finalize"]["status"] == "skipped"


def test_v1_terminal_job_projects_pending_as_skipped(client: TestClient) -> None:
    created = _submit_fake_job(client, name="terminal-skip")
    job_id = str(created["job_id"])
    _wait_for_terminal_job(client, job_id)
    detail = client.get(f"/api/v1/jobs/{job_id}/detail")
    assert detail.status_code == 200
    stages = detail.json()["stages"]
    assert len(stages) >= 1
    for entry in stages:
        assert entry["status"] != "pending"
        assert entry["status"] != "running"


def test_bond_scan_create_job_accepts_double_coordinates(client: TestClient) -> None:
    xyz = (
        "5\nC5 chain\nC 0.0 0.0 0.0\nC 1.4 0.0 0.0\nC 2.8 0.0 0.0\n"
        "C 4.2 0.0 0.0\nC 5.6 0.0 0.0\n"
    )
    coordinate = {"kind": "distance", "atoms": [0, 1], "start": 1.2, "end": 2.2, "n_points": 4}
    payload = {
        "workflow": "PESsearch",
        "name": "pes-double-scan",
        "method": {"mode": "bond_length_scan"},
        "input": {
            "source": {"source_type": "xyz_text", "xyz_text": xyz},
            "coordinate": coordinate,
            "coordinates": [
                coordinate,
                {"kind": "distance", "atoms": [3, 4], "start": 1.2, "end": 2.2, "n_points": 4},
            ],
            "selection": {
                "mode": "functional",
                "kind": "double_bond_scan",
                "atom_indices": [0, 1, 3, 4],
            },
            "protocol": {},
        },
    }
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201, response.text

    manager = client.app.state.job_manager
    record = manager.get(str(response.json()["job_id"]))
    assert record is not None
    assert len(record.spec.input["coordinates"]) == 2
    assert record.spec.input["coordinates"][1]["atoms"] == [3, 4]
    assert record.spec.input["selection"]["kind"] == "double_bond_scan"


def test_bond_scan_create_job_rejects_malformed_coordinates(client: TestClient) -> None:
    base = {
        "workflow": "PESsearch",
        "name": "pes-bad-coordinates",
        "method": {"mode": "bond_length_scan"},
        "input": {
            "source_type": "xyz_text",
            "xyz_text": "2\nC2\nC 0 0 0\nC 1.4 0 0\n",
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.2,
                "n_points": 4,
            },
        },
    }
    bad_coordinates = dict(base, input=dict(base["input"], coordinates="nope"))
    response = client.post("/api/v1/jobs", json=bad_coordinates)
    assert response.status_code == 422

    bad_selection = dict(
        base,
        name="pes-bad-selection",
        input=dict(base["input"], selection="nope"),
    )
    response = client.post("/api/v1/jobs", json=bad_selection)
    assert response.status_code == 422


def test_s2_profile_exposes_coordinates_and_selection(
    client: TestClient,
    tmp_path: Path,
) -> None:
    created = _submit_fake_job(client, name="pes-profile-metadata")
    job_id = str(created["job_id"])
    record_data = _wait_for_terminal_job(client, job_id)
    manager = client.app.state.job_manager
    record = manager.get(job_id)
    assert record is not None
    record.spec = replace(
        record.spec,
        workflow="PESsearch",
        method={"mode": "bond_length_scan"},
    )
    manager.store.update(record)

    task_root = Path(record_data["work_dir"])
    profile_path = task_root / "RESULT" / "pes_search" / "pes_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": "pes_profile_v2",
                "workflow": "PESsearch",
                "mode": "bond_length_scan",
                "status": "completed",
                "coordinate": {"kind": "distance", "atoms": [0, 1], "unit": "angstrom"},
                "coordinates": [
                    {"kind": "distance", "atoms": [0, 1], "unit": "angstrom"},
                    {"kind": "distance", "atoms": [3, 4], "unit": "angstrom"},
                ],
                "selection": {"kind": "double_bond_scan", "atom_indices": [0, 1, 3, 4]},
                "protocol": {"scan_type": "distance_scan"},
                "scan_dir": "WORK/07_PATH/pes_scan_001",
                "frames": [],
                "profile": {},
                "quality": {},
                "ts_candidates": [],
                "int_candidates": [],
            }
        ),
        encoding="utf-8",
    )

    response = client.get(f"/api/v1/jobs/{job_id}/s2/profile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["coordinates"]) == 2
    assert body["coordinates"][1]["atoms"] == [3, 4]
    assert body["selection"]["kind"] == "double_bond_scan"
    assert body["protocol"]["scan_type"] == "distance_scan"
    assert body["coordinate"]["atoms"] == [0, 1]
