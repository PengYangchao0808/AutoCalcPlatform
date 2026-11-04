"""W-06 support: ``GET /api/v1/jobs?ids=`` is one round trip for a batch read.

The MCP ``acp_status`` batch path (``ControllerClient.status_many``) reuses this
selector so N status reads become ONE Controller request.  The route stays
additive: without ``ids`` the response is exactly the previous
``{jobs, counts}`` shape; an empty or oversized selector is a typed 400 rather
than a silently empty page.

Run with: pytest tests/test_acp_v1_batch_jobs.py -q
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_JOB_IDS = (
    "20261103_000000_001_alpha",
    "20261103_000000_002_beta",
    "20261103_000000_003_gamma",
)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")

    from acp.api.server import create_app
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    app = create_app(run_root=tmp_path / "runs", max_running=1)
    with TestClient(app) as test_client:
        manager = test_client.app.state.job_manager
        for job_id in _JOB_IDS:
            record = JobRecord(
                id=job_id,
                spec=JobSpec(
                    workflow="Confsearch",
                    name=job_id,
                    molecule_name="CCO",
                    input={"source": "CCO"},
                ),
                status=JobStatus.COMPLETED,
                work_dir=str(tmp_path / "work" / job_id),
            )
            manager.store.create(record)
        yield test_client


def test_ids_selects_only_requested_jobs(client: TestClient) -> None:
    requested = f"{_JOB_IDS[0]},{_JOB_IDS[2]}"
    response = client.get("/api/v1/jobs", params={"ids": requested})
    assert response.status_code == 200, response.text
    body = response.json()
    assert {job["id"] for job in body["jobs"]} == {_JOB_IDS[0], _JOB_IDS[2]}
    assert {"completed", "failed", "cancelled", "cancelling"} <= set(body["counts"])
    assert len(body["counts"]) == 10


def test_ids_is_additive_without_the_selector(client: TestClient) -> None:
    response = client.get("/api/v1/jobs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert {job["id"] for job in body["jobs"]} == set(_JOB_IDS)
    assert "jobs" in body and "counts" in body


def test_ids_rejects_empty_and_oversized_selector(client: TestClient) -> None:
    empty = client.get("/api/v1/jobs", params={"ids": " , "})
    assert empty.status_code == 400, empty.text
    too_many = ",".join(f"job-{index}" for index in range(101))
    oversized = client.get("/api/v1/jobs", params={"ids": too_many})
    assert oversized.status_code == 400, oversized.text


def test_ids_unknown_jobs_are_omitted_not_fabricated(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", params={"ids": "does-not-exist"})
    assert response.status_code == 200, response.text
    assert response.json()["jobs"] == []


def test_ids_falls_back_to_exact_lookup_beyond_the_list_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A requested id outside the list page window is resolved exactly, not lost.

    The route lists at most 1000 rows for a scope; a batch selector must still
    answer an older requested id.  The fallback is a bounded ``store.get`` per
    missing id (already scope-checked above), never a silent ``not_found``.
    """
    manager = client.app.state.job_manager
    store = manager.store
    visible = store.list_enriched(limit=1000)
    assert visible, "fixture must seed at least one job"

    monkeypatch.setattr(store, "list_enriched", lambda *args, **kwargs: [visible[0]])

    requested = f"{_JOB_IDS[0]},{_JOB_IDS[2]}"
    response = client.get("/api/v1/jobs", params={"ids": requested})
    assert response.status_code == 200, response.text
    assert {job["id"] for job in response.json()["jobs"]} == {_JOB_IDS[0], _JOB_IDS[2]}
