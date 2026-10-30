"""Preflight source feasibility for step workflows (round-4 plan, batch B2).

The preflight stays zero-side-effect: only locally-verifiable facts are
judged (missing source job, no frequency source, unregistered TS product).
A cold remote source is a submit/prepare decision, so it is never reported
infeasible here.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def test_preflight_reports_missing_tsmode_source_job(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={
            "workflow": "tsmode",
            "input": {"source_job_id": "20990101_999_frequency", "source_mode_index": 6},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feasible"] is False
    rules = {reason["rule"] for reason in body["reasons"]}
    assert "source_job_unavailable" in rules


def test_preflight_does_not_judge_workflows_without_sources(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={"workflow": "singlepoint", "input": {"input": "CCO"}},
    )
    assert response.status_code == 200
    assert response.json()["feasible"] is True


def test_preflight_requires_the_source_reference(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={"workflow": "irc", "input": {"input": "ts.xyz"}},
    )
    # the shared contract still demands the source/provenance shape
    assert response.status_code == 422
    assert response.json()["detail"]["params"]["rule"] == "missing_input"


# --------------------------------------------------------------------------- #
# C1: geometry-fact scan advisory (never a scientific criterion)
# --------------------------------------------------------------------------- #


def _pes_input(start: float, end: float, *, extra: list | None = None) -> dict:
    coordinate = {
        "kind": "distance",
        "atoms": [0, 5],
        # PESsearch schema: coordinate values are decimal strings (canonical digest)
        "start": f"{start:.2f}",
        "end": f"{end:.2f}",
        "points": 20,
    }
    payload = {
        "source": {
            "source_type": "xyz_text",
            "xyz_text": "6\n\nC 0 0 0\nC 1.5 0 0\nC 2.5 1 0\nC 3.5 0 0\nC 4.5 1 0\nC 5.5 0 0\n",
        },
        "coordinate": coordinate,
    }
    if extra is not None:
        payload["coordinates"] = extra
    return payload


def test_single_coordinate_large_span_gets_a_geometry_advisory(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={"workflow": "PESsearch", "input": _pes_input(4.9, 1.55)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["feasible"] is True  # advisory never blocks
    advisories = body["advisories"]
    assert len(advisories) == 1
    advisory = advisories[0]
    assert advisory["id"] == "scan_single_coordinate_geometry"
    assert advisory["severity"] == "info"
    assert advisory["policy"]["calibrated"] is False
    assert "4.90 -> 1.55" in advisory["message"]
    assert "not guarantee" in advisory["message"]


def test_small_span_single_coordinate_has_no_advisory(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={"workflow": "PESsearch", "input": _pes_input(2.4, 1.5)},
    )
    assert response.status_code == 200
    assert response.json()["advisories"] == []


def test_multi_coordinate_scan_has_no_advisory(client: TestClient) -> None:
    two = [
        {"kind": "distance", "atoms": [0, 5], "start": "4.90", "end": "2.00", "points": 10},
        {"kind": "distance", "atoms": [2, 3], "start": "1.54", "end": "2.00", "points": 10},
    ]
    payload = _pes_input(4.9, 2.0)
    payload.pop("coordinate", None)
    payload["coordinates"] = two
    response = client.post(
        "/api/v1/preflight", json={"workflow": "PESsearch", "input": payload}
    )
    assert response.status_code == 200
    assert response.json()["advisories"] == []


def test_other_workflows_get_no_scan_advisory(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight", json={"workflow": "singlepoint", "input": {"input": "CCO"}}
    )
    assert response.status_code == 200
    assert response.json()["advisories"] == []
