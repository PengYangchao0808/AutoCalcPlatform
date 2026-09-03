from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import acp.api.v1_routes as v1_routes
from acp.api.v1_schemas import V1JobRecordModel
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def _write_state_and_store(
    client: TestClient,
    record: JobRecord,
    state_data: dict[str, Any],
) -> None:
    work_dir = Path(record.work_dir)
    work_dir.mkdir()
    (work_dir / "state.json").write_text(json.dumps(state_data), encoding="utf-8")
    client.app.state.job_manager.store.create(record)


def _record(job_id: str, work_dir: Path, status: JobStatus) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="fake", name=job_id),
        status=status,
        work_dir=str(work_dir),
    )


def _summary_model(
    client: TestClient,
    record: JobRecord,
    state_data: dict[str, Any],
) -> V1JobRecordModel:
    _write_state_and_store(client, record, state_data)
    response = client.get(f"/api/v1/jobs/{record.id}/summary")
    assert response.status_code == 200
    return V1JobRecordModel.model_validate(response.json())


_STAGE_STATE: Final[dict[str, Any]] = {
    "status": "running",
    "progress_state": "indeterminate",
    "current_stage": "run_single_points",
    "stage_index": 2,
    "stage_total": 7,
    "stage_progress": 0.25,
}


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


def test_job_record_serializes_default_live_status_fields() -> None:
    model = V1JobRecordModel(
        id="live-status-defaults",
        spec={"workflow": "fake"},
        status="queued",
    )

    serialized = model.model_dump()

    assert {"live_status", "display_method"} <= serialized.keys()
    assert model.live_status is None
    assert model.display_method is None
    assert serialized["live_status"] is None
    assert serialized["display_method"] is None


def test_job_record_round_trips_full_live_status_metrics() -> None:
    live_status = {
        "stage_label": "单点计算",
        "stage_index": 6,
        "stage_total": 9,
        "metrics": [
            {
                "key": "completed_total",
                "label_key": "live.single_points",
                "label": "Single points",
                "value": "14 / 25",
                "kind": "count",
                "priority": 100,
                "detail": "Completed frames",
            },
            {
                "key": "current_frame",
                "label_key": "live.current_frame",
                "label": "Current frame",
                "value": "Frame 15",
                "kind": "text",
                "priority": 90,
                "detail": "Active scan frame",
            },
        ],
    }
    model = V1JobRecordModel(
        id="live-status-round-trip",
        spec={"workflow": "PESsearch"},
        status="running",
        live_status=live_status,
        display_method="B97-3c / def2-SVP",
    )

    assert model.live_status is not None
    assert model.live_status.model_dump() == live_status
    assert model.model_dump()["live_status"] == live_status
    assert model.display_method == "B97-3c / def2-SVP"


def test_job_record_rejects_invalid_live_metric_kind() -> None:
    with pytest.raises(ValidationError):
        V1JobRecordModel(
            id="live-status-invalid-kind",
            spec={"workflow": "fake"},
            status="running",
            live_status={
                "metrics": [
                    {
                        "key": "invalid",
                        "value": "not allowed",
                        "kind": "unknown",
                    }
                ]
            },
        )


def test_summary_returns_404_for_an_unknown_job(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/unknown-summary-job/summary")

    assert response.status_code == 404


def test_summary_does_not_overlay_stale_state_for_completed_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "completed-terminal-guard"
    record = _record("completed-terminal-guard", work_dir, JobStatus.COMPLETED)
    model = _summary_model(
        client,
        record,
        {
            **_STAGE_STATE,
            "stage_detail": "stale finalize stage",
            "live_metrics": [
                {"key": "completed_total", "value": "14 / 25", "kind": "count", "priority": 100}
            ],
        },
    )
    assert model.status == JobStatus.COMPLETED.value
    assert model.progress == 1.0
    assert model.progress_state == "determinate"
    assert model.stage_index is None
    assert model.stage_total is None
    assert model.stage_progress is None
    assert model.stage_detail is None
    assert model.display_method is None
    assert model.live_status is not None
    assert model.live_status.stage_label == "单点计算"
    assert model.live_status.metrics[0].value == "14 / 25"


def test_summary_overlays_state_for_running_job(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "running-terminal-guard"
    record = JobRecord(
        id="running-terminal-guard",
        spec=JobSpec(
            workflow="PESsearch",
            name="running-terminal-guard",
            input={
                "scan_request": {"protocol": {"single_point": {"method": "B97-3c", "basis": None}}}
            },
        ),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
    )
    model = _summary_model(
        client,
        record,
        {
            **_STAGE_STATE,
            "stage_detail": "2/7 stages",
            "live_metrics": [
                {
                    "key": "current_frame",
                    "value": "Frame 15",
                    "kind": "text",
                    "priority": 90,
                },
                {
                    "key": "completed_total",
                    "value": "14 / 25",
                    "kind": "count",
                    "priority": 100,
                },
                {"key": "low_priority", "value": "keep", "kind": "text", "priority": 10},
                {"key": "ignored", "value": "drop", "kind": "text", "priority": 0},
            ],
        },
    )
    assert model.stage_index == 2
    assert model.stage_total == 7
    assert model.stage_progress == 0.25
    assert model.stage_detail == "2/7 stages"
    assert model.progress_state == "indeterminate"
    assert model.display_method == "B97-3c"
    assert model.live_status is not None
    assert model.live_status.stage_label == "单点计算"
    assert model.live_status.stage_index == 2
    assert model.live_status.stage_total == 7
    assert [metric.key for metric in model.live_status.metrics] == [
        "completed_total",
        "current_frame",
        "low_priority",
    ]


@pytest.mark.parametrize(
    ("job_id", "status", "state_data", "expected_values"),
    [
        (
            "failed-live-status",
            JobStatus.FAILED,
            {
                "current_stage": "compute",
                "live_metrics": [{"key": "iteration", "value": "7", "kind": "iteration"}],
            },
            ["7"],
        ),
        (
            "malformed-live-status",
            JobStatus.RUNNING,
            {"current_stage": "compute", "live_metrics": "broken"},
            [],
        ),
    ],
)
def test_summary_preserves_live_status_for_bad_metric_input(
    client: TestClient,
    tmp_path: Path,
    job_id: str,
    status: JobStatus,
    state_data: dict[str, Any],
    expected_values: list[str],
) -> None:
    record = _record(job_id, tmp_path / job_id, status)
    model = _summary_model(client, record, state_data)

    assert model.live_status is not None
    assert model.live_status.stage_label == "计算中"
    assert [metric.value for metric in model.live_status.metrics] == expected_values


def test_enrichment_cache_invalidates_after_live_metrics_mtime_changes(
    client: TestClient,
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "cache-invalidation"
    record = _record("cache-invalidation", work_dir, JobStatus.RUNNING)
    _write_state_and_store(
        client,
        record,
        {
            "stage_index": 1,
            "live_metrics": [{"key": "progress", "value": "first", "kind": "text"}],
        },
    )
    state_path = work_dir / "state.json"
    os.utime(state_path, ns=(1_000_000_000, 2_000_000_001))

    first = client.get(f"/api/v1/jobs/{record.id}/summary")

    state_path.write_text(
        json.dumps(
            {
                "stage_index": 1,
                "live_metrics": [{"key": "progress", "value": "second", "kind": "text"}],
            }
        ),
        encoding="utf-8",
    )
    os.utime(state_path, ns=(1_000_000_000, 2_000_000_002))
    second = client.get(f"/api/v1/jobs/{record.id}/summary")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["live_status"]["metrics"][0]["value"] == "first"
    assert second.json()["live_status"]["metrics"][0]["value"] == "second"


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
    assert first.live_status is None
