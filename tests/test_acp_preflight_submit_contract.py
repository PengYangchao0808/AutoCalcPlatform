"""Contract guard for the Next task-config submit path (UI-03).

The Next config panel is a two-step submit: ``POST /api/v1/preflight`` returns a
canonical ``plan_digest`` that is replayed as ``preflight_fingerprint`` on
``POST /api/v1/jobs``. These tests pin the server side of that contract with the
synthetic ``fake`` workflow, so they are hermetic (no QC binary) and prove:

1. a feasible preflight creates nothing;
2. the fingerprint matching the same plan is accepted and creates the task;
3. a changed plan with a stale fingerprint is rejected with ``preflight_stale``
   *before* any task exists;
4. the client ``Idempotency-Key`` replays instead of duplicating.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BASIC_SUBMIT = {
    "schema_version": 1,
    "workflow": "fake",
    "input": {"source": "CCO"},
    "method": {"protocol": "ext"},
    "resources": {"nproc": 2, "mem": "2000MB"},
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _preflight(client: TestClient, payload: dict[str, object] | None = None) -> dict[str, object]:
    response = client.post("/api/v1/preflight", json=payload or BASIC_SUBMIT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["would_create_task"] is False
    assert body["would_reserve"] is False
    return body


def test_preflight_then_submit_with_matching_fingerprint(client: TestClient) -> None:
    body = _preflight(client)
    digest = str(body["plan_digest"])
    assert body["feasible"] is True
    assert digest.startswith("sha256:")

    created = client.post(
        "/api/v1/jobs",
        json={**BASIC_SUBMIT, "preflight_fingerprint": digest},
        headers={"Idempotency-Key": "next-contract-1"},
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["job_id"]
    assert receipt["workflow"] == "fake"
    assert receipt["idempotent_replay"] is False


def test_stale_fingerprint_is_rejected_before_creation(client: TestClient) -> None:
    digest = str(_preflight(client)["plan_digest"])

    tampered = {**BASIC_SUBMIT, "input": {"source": "CCN"}, "preflight_fingerprint": digest}
    rejected = client.post("/api/v1/jobs", json=tampered)
    assert rejected.status_code == 400, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "preflight_stale"
    assert detail["params"]["field"] == "preflight_fingerprint"

    assert client.get("/api/v1/jobs?limit=10").json()["jobs"] == []


def test_same_idempotency_key_replays_same_task(client: TestClient) -> None:
    digest = str(_preflight(client)["plan_digest"])
    payload = {**BASIC_SUBMIT, "preflight_fingerprint": digest}
    headers = {"Idempotency-Key": "next-contract-replay"}

    first = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert second.status_code in (200, 201), second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["idempotent_replay"] is True
    assert len(client.get("/api/v1/jobs?limit=10").json()["jobs"]) == 1


def test_preflight_rejects_unknown_workflow(client: TestClient) -> None:
    response = client.post("/api/v1/preflight", json={**BASIC_SUBMIT, "workflow": "not-a-workflow"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"]["field"] == "workflow"
