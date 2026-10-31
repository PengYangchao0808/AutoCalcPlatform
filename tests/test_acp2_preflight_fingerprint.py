"""B1-3 v1 preflight fingerprint gate tests.

Pins the additive ``preflight_fingerprint`` contract:

* v1 ``POST /api/v1/preflight`` keeps its exact response shape (pure extraction);
* a submit carrying the matching preflight digest is accepted;
* a submit whose plan changed after preflight is rejected with
  ``400 preflight_stale`` before any store/admission side effect;
* a submit without the field keeps the legacy behaviour;
* ``preflight_fingerprint`` alone is not a research launch trigger.
"""

# pyright: reportMissingTypeArgument=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportImplicitStringConcatenation=false, reportIndexIssue=false, reportOperatorIssue=false
from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_PREFLIGHT_KEYS = {
    "schema_version",
    "feasible",
    "reasons",
    # C1 (round-4): additive advisory list; an empty list is the default and
    # old clients ignore the extra key.
    "advisories",
    # PR-03 (round-5): read-only analysis receipt (None for uncovered workflows).
    "submission_analysis",
    "plan_digest",
    "plan_summary",
    "estimate",
    "admission",
    "would_create_task",
    "would_reserve",
}


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflow": "fake",
        "name": "preflight-fingerprint",
        "input": {"source": "CCO", "demo_frames": False},
        "method": {"protocol": "ext"},
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _preflight(client: TestClient, payload: dict[str, object]) -> dict[str, object]:
    response = client.post("/api/v1/preflight", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _total_jobs(client: TestClient) -> int:
    return sum(client.app.state.job_manager.store.counts().values())


def _cancel_and_wait(client: TestClient, job_id: str) -> None:
    manager = client.app.state.job_manager
    client.post(f"/api/v1/jobs/{job_id}/cancel")
    for _ in range(60):
        response = client.get(f"/api/v1/jobs/{job_id}")
        if response.status_code == 200 and response.json()["status"] in {
            "completed",
            "failed",
            "cancelled",
        }:
            if job_id not in manager._submission_jobs:
                return
        time.sleep(0.1)


def test_preflight_v1_response_shape_is_unchanged(client: TestClient) -> None:
    body = _preflight(client, _payload())

    assert set(body) == _PREFLIGHT_KEYS
    assert isinstance(body["feasible"], bool)
    assert isinstance(body["reasons"], list)
    assert isinstance(body["advisories"], list)
    assert body["plan_digest"] is None or isinstance(body["plan_digest"], str)
    assert body["admission"] is None
    assert body["would_create_task"] is False
    assert body["would_reserve"] is False
    assert _total_jobs(client) == 0


def test_matching_preflight_fingerprint_is_accepted(client: TestClient) -> None:
    payload = _payload()
    digest = _preflight(client, payload)["plan_digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")

    response = client.post("/api/v1/jobs", json={**payload, "preflight_fingerprint": digest})
    assert response.status_code == 201, response.text
    job_id = response.json()["job_id"]

    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 200
    _cancel_and_wait(client, job_id)


def test_stale_fingerprint_is_rejected_before_side_effect(client: TestClient) -> None:
    original = _payload(input={"source": "CCO", "demo_frames": False, "charge": 0})
    stale = _preflight(client, original)["plan_digest"]
    assert isinstance(stale, str)

    mutated = _payload(input={"source": "CCO", "demo_frames": False, "charge": 1})
    expected = _preflight(client, mutated)["plan_digest"]
    assert expected != stale

    before = client.app.state.job_manager.store.counts()
    response = client.post("/api/v1/jobs", json={**mutated, "preflight_fingerprint": stale})
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "preflight_stale"
    assert detail["params"]["field"] == "preflight_fingerprint"
    assert detail["params"]["expected"] == expected
    assert client.app.state.job_manager.store.counts() == before


def test_legacy_submit_without_fingerprint_is_unchanged(client: TestClient) -> None:
    response = client.post("/api/v1/jobs", json=_payload())
    assert response.status_code == 201, response.text
    _cancel_and_wait(client, response.json()["job_id"])


def test_fingerprint_alone_is_not_a_research_launch(client: TestClient) -> None:
    payload = _payload()
    digest = _preflight(client, payload)["plan_digest"]
    assert isinstance(digest, str)

    response = client.post("/api/v1/jobs", json={**payload, "preflight_fingerprint": digest})
    assert (
        response.status_code != 403
        or response.json().get("detail", {}).get("params", {}).get("rule") != "approval_required"
    )
    assert response.status_code == 201, response.text
    _cancel_and_wait(client, response.json()["job_id"])
