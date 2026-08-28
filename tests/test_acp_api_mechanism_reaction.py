"""Tests for mechanism reaction preview/confirm/plan — all write routes now 410 Gone."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from starlette.applications import Starlette

from acp.scheduler.manager import JobManager


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _seed_study(store, study_id, *, job_id=None, status="draft", study_json=None):
    now = "2026-08-28T00:00:00Z"
    sj = study_json or {"study_id": study_id}
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mechanism_studies "
            "(id, job_id, study_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (study_id, job_id, json.dumps(sj), status, now, now),
        )
        conn.commit()


def test_preview_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-p")
        response = client.post(
            "/api/v1/mechanism-studies/study-p/reaction/preview",
            json={
                "reactant": {"source_type": "smiles", "source": "CCO"},
                "product": {"source_type": "smiles", "source": "CCO"},
            },
        )
        assert response.status_code == 410


def test_confirm_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-c")
        response = client.post(
            "/api/v1/mechanism-studies/study-c/reaction/confirm",
            json={
                "reactant": {"source_type": "smiles", "source": "CCO"},
                "product": {"source_type": "smiles", "source": "CCO"},
            },
        )
        assert response.status_code == 410


def test_plan_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-pl")
        response = client.post(
            "/api/v1/mechanism-studies/study-pl/mechanism/plan",
            json={"plan": {}, "strategy": "guided-scan", "fidelity": "s3"},
        )
        assert response.status_code == 410


def test_get_reaction_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id}/reaction still works (read-only)."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-get", status="reaction_confirmed")
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                "UPDATE mechanism_studies SET reaction_json=?, config_hash=?,"
                " status=?, updated_at=? WHERE id=?",
                (
                    json.dumps({"schema_version": 2, "study_id": "study-get", "bond_changes": []}),
                    "sha256:abc",
                    "reaction_confirmed",
                    "2026-08-28T00:00:00Z",
                    "study-get",
                ),
            )
            conn.commit()
        response = client.get("/api/v1/mechanism-studies/study-get/reaction")
        assert response.status_code == 200
        assert response.json()["reaction"]["schema_version"] == 2
