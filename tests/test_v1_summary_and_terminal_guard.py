from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import acp.api.v1_routes as v1_routes
from acp.api.v1_schemas import V1JobRecordModel
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def test_summary_returns_the_job_record_schema_for_a_submitted_fake_job(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "summary-job",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
        },
    )
    assert created.status_code == 201
    job_id = str(created.json()["job_id"])

    summary = client.get(f"/api/v1/jobs/{job_id}/summary")

    assert summary.status_code == 200
    body = summary.json()
    model = V1JobRecordModel.model_validate(body)
    assert model.id == job_id
    assert model.spec.workflow == "fake"
    assert set(body) == set(V1JobRecordModel.model_fields)


def test_summary_returns_404_for_an_unknown_job(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/unknown-summary-job/summary")

    assert response.status_code == 404


def test_summary_does_not_overlay_stale_state_for_completed_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    manager = client.app.state.job_manager
    work_dir = tmp_path / "completed-terminal-guard"
    work_dir.mkdir()
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "running",
                "progress_state": "indeterminate",
                "stage_index": 2,
                "stage_total": 7,
                "stage_progress": 0.25,
                "stage_detail": "stale finalize stage",
            }
        ),
        encoding="utf-8",
    )
    record = JobRecord(
        id="completed-terminal-guard",
        spec=JobSpec(
            workflow="fake",
            name="completed-terminal-guard",
            input={"source": "CCO"},
            method={"protocol": "ext"},
            project_id=manager.default_project_id,
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)

    response = client.get(f"/api/v1/jobs/{record.id}/summary")

    assert response.status_code == 200
    model = V1JobRecordModel.model_validate(response.json())
    assert model.status == JobStatus.COMPLETED.value
    assert model.progress == 1.0
    assert model.progress_state == "determinate"
    assert model.stage_index is None
    assert model.stage_total is None
    assert model.stage_progress is None
    assert model.stage_detail is None


def test_summary_overlays_state_for_running_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    manager = client.app.state.job_manager
    work_dir = tmp_path / "running-terminal-guard"
    work_dir.mkdir()
    (work_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "running",
                "progress_state": "indeterminate",
                "stage_index": 2,
                "stage_total": 7,
                "stage_progress": 0.25,
                "stage_detail": "2/7 stages",
            }
        ),
        encoding="utf-8",
    )
    record = JobRecord(
        id="running-terminal-guard",
        spec=JobSpec(
            workflow="fake",
            name="running-terminal-guard",
            input={"source": "CCO"},
            method={"protocol": "ext"},
            project_id=manager.default_project_id,
        ),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)

    response = client.get(f"/api/v1/jobs/{record.id}/summary")

    assert response.status_code == 200
    model = V1JobRecordModel.model_validate(response.json())
    assert model.stage_index == 2
    assert model.stage_total == 7
    assert model.stage_progress == 0.25
    assert model.stage_detail == "2/7 stages"
    assert model.progress_state == "indeterminate"


def test_enrichment_cache_invalidates_after_state_mtime_changes(tmp_path: Path) -> None:
    work_dir = tmp_path / "cache-invalidation"
    work_dir.mkdir()
    state_path = work_dir / "state.json"
    state_path.write_text(json.dumps({"stage_index": 1}), encoding="utf-8")
    os.utime(state_path, ns=(1_000_000_000, 2_000_000_001))
    record = JobRecord(
        id="cache-invalidation",
        spec=JobSpec(workflow="fake", name="cache-invalidation"),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
    )
    base_model = v1_routes._record_to_v1_model(record)

    first = v1_routes._enrich_job_snapshot(record, base_model)

    state_path.write_text(json.dumps({"stage_index": 9}), encoding="utf-8")
    os.utime(state_path, ns=(1_000_000_000, 2_000_000_002))
    second = v1_routes._enrich_job_snapshot(record, base_model)

    assert first.stage_index == 1
    assert second.stage_index == 9


def test_enrichment_cache_reuses_same_mtime_without_parsing_again(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "cache-hit"
    work_dir.mkdir()
    state_path = work_dir / "state.json"
    state_path.write_text(json.dumps({"stage_index": 3}), encoding="utf-8")
    os.utime(state_path, ns=(1_000_000_000, 3_000_000_001))
    record = JobRecord(
        id="cache-hit",
        spec=JobSpec(workflow="fake", name="cache-hit"),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
    )
    base_model = v1_routes._record_to_v1_model(record)

    with patch.object(v1_routes.json, "loads", wraps=v1_routes.json.loads) as loads:
        first = v1_routes._enrich_job_snapshot(record, base_model)
        second = v1_routes._enrich_job_snapshot(record, base_model)

    assert first is second
    assert loads.call_count == 1
