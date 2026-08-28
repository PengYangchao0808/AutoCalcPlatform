"""Tests for mechanism-study promote endpoint — all routes now 410 Gone."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app
    return TestClient(create_app(run_root=tmp_path, max_running=2))


def test_promote_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/mechanism-studies/study-x/promote")
        assert response.status_code == 410
        assert "已退役" in response.json()["detail"]


def test_resume_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/mechanism-studies/study-x/resume")
        assert response.status_code == 410


def test_review_decision_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-studies/study-x/reviews/dec-1/decision",
            json={"decision": "accept_network"},
        )
        assert response.status_code == 410


def test_decision_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-studies/study-x/decisions/dec-1",
            json={"resolution": "approve"},
        )
        assert response.status_code == 410


def test_unified_status_readonly(tmp_path: Path) -> None:
    """List endpoint still works (read-only)."""
    with make_client(tmp_path) as client:
        listed = client.get("/api/v1/mechanism-studies")
        assert listed.status_code == 200
        assert listed.json() == []
