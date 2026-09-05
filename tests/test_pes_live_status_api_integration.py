"""Integration coverage for the live PES status summary projection."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.api.v1_schemas import V1JobRecordModel
from acp.calculations.pes.scan import PES_SCAN_STAGES
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Provide a lifespan-managed API client bound to an isolated run root."""
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


@pytest.mark.integration
def test_live_pes_projection_from_store_row(client: TestClient, tmp_path: Path) -> None:
    # Given: a real ProgressReporter has written the sixth, live PES stage
    # and its current single-point metrics into an isolated task directory.
    job_id = "current-store-row-pes-live"
    work_dir = tmp_path / "current-store-row-pes-live"
    reporter = ProgressReporter(
        work_dir,
        job_name=job_id,
        stages=list(PES_SCAN_STAGES),
        min_interval=0,
    )
    reporter.initialize()
    for stage_name in PES_SCAN_STAGES[:5]:
        reporter.start_stage(stage_name)
        reporter.complete_stage(stage_name)
    reporter.start_stage("run_single_points")
    reporter.update_stage("run_single_points", completed=14, total=25)
    reporter.set_live_metrics(
        [
            LiveMetric(
                key="completed_total",
                label_key="live.single_points",
                value="14 / 25",
                kind="count",
                priority=100,
            ),
            LiveMetric(
                key="current_frame",
                label_key="live.current_frame",
                value="Frame 15",
                kind="text",
                priority=90,
            ),
        ]
    )

    # The scheduler row is inserted directly after app startup, so no
    # bootstrap-seeded row can supply the response's identity or spec.
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="PESsearch",
            name="current-store-row-pes-live",
            input={
                "scan_request": {
                    "protocol": {
                        "single_point": {"method": "B97-3c", "basis": None},
                        "scan_optimizer": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
                    }
                }
            },
            method={"method": "top-level-fallback", "basis": "top-level-basis"},
        ),
        status=JobStatus.RUNNING,
        work_dir=str(work_dir),
        current_stage="run_single_points",
    )
    client.app.state.job_manager.store.create(record)

    # When: the real HTTP summary route loads the current row and enriches it.
    response = client.get(f"/api/v1/jobs/{job_id}/summary")

    # Then: the response is the live store-row projection, not bootstrap data.
    assert response.status_code == 200
    model = V1JobRecordModel.model_validate(response.json())
    assert model.id == job_id
    assert model.status == "running"
    assert model.spec.workflow == "PESsearch"
    assert model.spec.name == "current-store-row-pes-live"
    assert model.spec.method["method"] == "top-level-fallback"
    assert model.current_stage == "run_single_points"
    assert model.display_method == "B97-3c"
    assert model.live_status is not None
    assert model.live_status.stage_label == "单点计算"
    assert model.live_status.stage_index == 6
    assert model.live_status.stage_total == 9
    assert [metric.value for metric in model.live_status.metrics] == ["14 / 25", "Frame 15"]
    assert [metric.key for metric in model.live_status.metrics] == [
        "completed_total",
        "current_frame",
    ]

    # The completed-at-14/25-without-new-dots terminal guard is covered by
    # tests/test_v1_summary_and_terminal_guard.py; this test remains RUNNING.
