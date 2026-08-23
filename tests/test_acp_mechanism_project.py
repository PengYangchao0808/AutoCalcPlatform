"""Tests for the MechanismProject model, store, and API (design §9)."""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.mechanism.project import (
    MechanismProject,
    MechanismProjectStatus,
    MechanismProjectStore,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> MechanismProjectStore:
    return MechanismProjectStore(tmp_path / "test.db")


def make_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_running: int = 2
) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=max_running))


@pytest.fixture()
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, monkeypatch, max_running=2) as test_client:
        yield test_client


def _submit_fake_job(
    client: TestClient,
    *,
    source: str = "CCO",
    name: str = "demo",
    mechanism_project_id: str | None = None,
    workflow: str = "fake",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow": workflow,
        "name": name,
        "input": {"source": source},
        "method": {"protocol": "ext"},
    }
    if mechanism_project_id is not None:
        payload["mechanism_project_id"] = mechanism_project_id
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 201
    return response.json()


def _wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(40):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        record = response.json()
        if record["status"] in {"completed", "failed", "cancelled"}:
            return record
        time.sleep(0.25)
    return record


# ── Store: CRUD ──────────────────────────────────────────────────────────


class TestMechanismProjectStoreCRUD:
    def test_create_and_get(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        project = store.create(name="Test", charge=1, multiplicity=2)
        assert project.name == "Test"
        assert project.charge == 1
        assert project.multiplicity == 2
        assert project.status == MechanismProjectStatus.CREATED
        assert project.project_id

        fetched = store.get(project.project_id)
        assert fetched is not None
        assert fetched.name == "Test"
        assert fetched.charge == 1

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get("no-such-id") is None

    def test_list_all(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.create(name="A")
        store.create(name="B")
        store.create(name="C")
        projects = store.list_all()
        assert len(projects) == 3
        names = {p.name for p in projects}
        assert names == {"A", "B", "C"}

    def test_set_stage_job(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        project = store.create(name="X")
        ok = store.set_stage_job(project.project_id, "s1", "job-001")
        assert ok is True
        fetched = store.get(project.project_id)
        assert fetched is not None
        assert fetched.stage_jobs["s1"] == "job-001"
        assert fetched.stage_jobs["s2"] is None

    def test_set_stage_job_invalid_stage(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        project = store.create(name="X")
        with pytest.raises(ValueError, match="Invalid stage"):
            store.set_stage_job(project.project_id, "s5", "job-001")

    def test_set_stage_job_nonexistent_project(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        ok = store.set_stage_job("no-such-id", "s1", "job-001")
        assert ok is False


# ── Store: state machine ─────────────────────────────────────────────────


class TestMechanismProjectAdvance:
    def _setup_project_with_jobs(self, tmp_path: Path) -> tuple[MechanismProjectStore, MechanismProject]:
        store = _make_store(tmp_path)
        project = store.create(name="Mech")
        store.set_stage_job(project.project_id, "s1", "j1")
        store.set_stage_job(project.project_id, "s2", "j2")
        store.set_stage_job(project.project_id, "s3", "j3")
        store.set_stage_job(project.project_id, "s4", "j4")
        return store, project

    def test_s1_completed_advances_to_s1_ready(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j1", "Confsearch", "completed")
        assert result is not None
        assert result.status == MechanismProjectStatus.S1_READY

    def test_s2_completed_advances_to_s2_ready(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j2", "PESsearch", "completed")
        assert result is not None
        assert result.status == MechanismProjectStatus.S2_READY

    def test_s3_completed_advances_to_s3_ready(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j3", "Lowconfirm", "completed")
        assert result is not None
        assert result.status == MechanismProjectStatus.S3_READY

    def test_s4_completed_advances_to_completed(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j4", "Highconfirm", "completed")
        assert result is not None
        assert result.status == MechanismProjectStatus.COMPLETED

    def test_failed_blocks_project(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j1", "Confsearch", "failed")
        assert result is not None
        assert result.status == MechanismProjectStatus.BLOCKED

    def test_cancelled_blocks_project(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j2", "PESsearch", "cancelled")
        assert result is not None
        assert result.status == MechanismProjectStatus.BLOCKED

    def test_blocked_does_not_unblock(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        store.advance_for_job(project.project_id, "j1", "Confsearch", "failed")
        result = store.advance_for_job(project.project_id, "j2", "PESsearch", "completed")
        assert result is not None
        assert result.status == MechanismProjectStatus.BLOCKED

    def test_completed_does_not_unblock(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        store.advance_for_job(project.project_id, "j4", "Highconfirm", "completed")
        result = store.advance_for_job(project.project_id, "j1", "Confsearch", "failed")
        assert result is not None
        assert result.status == MechanismProjectStatus.COMPLETED

    def test_wrong_job_id_ignored(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "wrong-job", "Confsearch", "completed")
        assert result is None

    def test_unknown_workflow_ignored(self, tmp_path: Path) -> None:
        store, project = self._setup_project_with_jobs(tmp_path)
        result = store.advance_for_job(project.project_id, "j1", "singlepoint", "completed")
        assert result is None

    def test_nonexistent_project(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.advance_for_job("no-such", "j1", "Confsearch", "completed")
        assert result is None


# ── API: mechanism-project endpoints ─────────────────────────────────────


class TestMechanismProjectAPI:
    def test_create_project(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "Test Project", "charge": 1, "multiplicity": 2},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Test Project"
        assert body["charge"] == 1
        assert body["multiplicity"] == 2
        assert body["status"] == "created"
        assert body["project_id"]

    def test_list_projects(self, client: TestClient) -> None:
        client.post("/api/v1/mechanism-projects", json={"name": "P1"})
        client.post("/api/v1/mechanism-projects", json={"name": "P2"})
        response = client.get("/api/v1/mechanism-projects")
        assert response.status_code == 200
        projects = response.json()["projects"]
        assert len(projects) == 2
        names = {p["name"] for p in projects}
        assert names == {"P1", "P2"}

    def test_get_project_detail(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "Detail Test", "reaction_definition_hash": "abc123"},
        )
        project_id = create_resp.json()["project_id"]
        response = client.get(f"/api/v1/mechanism-projects/{project_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Detail Test"
        assert body["reaction_definition_hash"] == "abc123"
        assert "timeline" in body
        assert len(body["timeline"]) == 4
        stages = {entry["stage"] for entry in body["timeline"]}
        assert stages == {"S1", "S2", "S3", "S4"}
        for entry in body["timeline"]:
            assert entry["job_id"] is None
            assert entry["job_status"] is None

    def test_get_project_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/mechanism-projects/nonexistent")
        assert response.status_code == 404

    def test_submit_job_with_mechanism_project_id(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "Linked"},
        )
        project_id = create_resp.json()["project_id"]
        job_resp = _submit_fake_job(client, mechanism_project_id=project_id)
        assert job_resp["mechanism_project_id"] == project_id

    def test_submit_job_with_invalid_mechanism_project_id(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "fake",
                "name": "bad",
                "input": {"source": "CCO"},
                "method": {"protocol": "ext"},
                "mechanism_project_id": "nonexistent",
            },
        )
        assert response.status_code == 404


# ── API: timeline populated after job completes ─────────────────────────


class TestMechanismProjectTimelineWithJobs:
    def test_timeline_structure(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "Timeline Test"},
        )
        project_id = create_resp.json()["project_id"]

        detail_resp = client.get(f"/api/v1/mechanism-projects/{project_id}")
        assert detail_resp.status_code == 200
        timeline = detail_resp.json()["timeline"]
        assert len(timeline) == 4
        stages = {entry["stage"] for entry in timeline}
        assert stages == {"S1", "S2", "S3", "S4"}
        for entry in timeline:
            assert entry["job_id"] is None
            assert entry["job_status"] is None
            assert entry["artifact"]

    def test_submit_fake_job_with_mechanism_project(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "Fake Linked"},
        )
        project_id = create_resp.json()["project_id"]
        job_resp = _submit_fake_job(client, mechanism_project_id=project_id, name="s1job")
        assert job_resp["mechanism_project_id"] == project_id
        _wait_for_terminal(client, job_resp["job_id"])

        detail_resp = client.get(f"/api/v1/mechanism-projects/{project_id}")
        assert detail_resp.status_code == 200
        body = detail_resp.json()
        assert body["status"] == "created"
