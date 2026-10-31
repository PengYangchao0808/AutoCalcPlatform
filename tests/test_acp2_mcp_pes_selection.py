"""Round-5 PR-07: cache-only selection status and the MCP selection tools.

The acceptance list separates three non-callable states (not negotiated / not
assembled / capability off), a read that never writes/prepares/fetches, the
publication receipt, and the error vocabulary.  This suite covers the HTTP
read surface first; the MCP tool wiring lives in the same PR and is exercised
through the adapter below once assembled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def _register_job(client: TestClient, tmp_path: Path, work_dir: Path) -> str:
    from tests.test_acp_api_pes_review import _register_job as register

    return register(client, tmp_path, work_dir)


def _make_task(tmp_path: Path) -> Path:
    from tests.test_acp_api_pes_review import _make_pes_task

    return _make_pes_task(tmp_path / "tree")


def _counts(client: TestClient) -> dict:
    return dict(client.app.state.job_manager.store.counts())


def test_status_reads_are_cache_only_and_side_effect_free(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _register_job(client, tmp_path, _make_task(tmp_path))
    before = _counts(client)
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["cache_only"] is True
    assert body["remote"] is False
    assert body["job_id"] == job_id
    assert body["capability"]["publish_endpoint"].endswith("/pes/review")
    assert body["publication"] is None
    assert _counts(client) == before


def test_remote_uncached_status_reports_not_prepared_without_fetch(
    client: TestClient, tmp_path: Path
) -> None:
    from tests.test_acp_api_pes_review import _RemoteTreeFetcher, _register_remote_job

    client.app.state.job_manager._remote_fetcher = _RemoteTreeFetcher({})
    job_id, _local = _register_remote_job(client, tmp_path)
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["remote"] is True
    assert body["status"] == "not_prepared"
    assert "prepare" in str(body["capability"]["prepare_endpoint"])
    assert client.app.state.job_manager._remote_fetcher.calls == []


def test_confirmed_status_exposes_revision_and_publication(client: TestClient, tmp_path: Path) -> None:
    job_id = _register_job(client, tmp_path, _make_task(tmp_path))
    saved = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={
            "candidates": [
                {"frame_index": 1, "role": "TS"},
                {"frame_index": 2, "role": "INT"},
            ],
            "idempotency_key": "status-1",
        },
    )
    assert saved.status_code == 200, saved.text
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["revision"] == 1
    assert body["selector"] == "manual"
    assert len(body["selected"]) == 2
    assert body["selected"][0]["selection_source"] == "manual"
    assert body["publication"]["status"] == "published"
    assert body["source"]["task_status"] == "COMPLETED"


def test_status_rejects_non_pes_workflows(client: TestClient, tmp_path: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir = tmp_path / "freq_task"
    work_dir.mkdir(parents=True)
    spec = JobSpec(workflow="frequency", name="freq", input={"smiles": "CCO"}, method={})
    record = JobRecord(
        id="20261031_001_frequency",
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        created_at="2026-10-31T00:00:00",
        updated_at="2026-10-31T00:00:00",
    )
    manager.store.create(record)
    response = client.get("/api/v1/jobs/20261031_001_frequency/pes/review/status")
    assert response.status_code == 400
