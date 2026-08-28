"""Tests for mechanism reaction preview/confirm/plan — all write routes now 410 Gone."""

from __future__ import annotations

import json
import os
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
    store.upsert_mechanism_study(study_id, job_id=job_id, study_json=json.dumps(sj), status=status, created_at=now, updated_at=now)


def test_preview_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-p")
        response = client.post(
            "/api/v1/mechanism-studies/study-p/reaction/preview",
            json={"reactant": {"source_type": "smiles", "source": "CCO"}, "product": {"source_type": "smiles", "source": "CCO"}},
        )
        assert response.status_code == 410


def test_confirm_returns_410(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-c")
        response = client.post(
            "/api/v1/mechanism-studies/study-c/reaction/confirm",
            json={"reactant": {"source_type": "smiles", "source": "CCO"}, "product": {"source_type": "smiles", "source": "CCO"}},
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
        store.update_mechanism_study_reaction(
            "study-get",
            reaction_json=json.dumps({"schema_version": 2, "study_id": "study-get", "bond_changes": []}),
            config_hash="sha256:abc",
            status="reaction_confirmed",
            updated_at="2026-08-28T00:00:00Z",
        )
        response = client.get("/api/v1/mechanism-studies/study-get/reaction")
        assert response.status_code == 200
        assert response.json()["reaction"]["schema_version"] == 2
