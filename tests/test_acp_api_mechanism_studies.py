"""Tests for mechanism-study API routes (read-only after todo 41)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from starlette.applications import Starlette

from acp.scheduler.jobs import JobStatus
from acp.scheduler.manager import JobManager


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _submit_fake_job(client: TestClient, *, name: str = "study-job") -> str:
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": name,
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
        },
    )
    assert response.status_code == 201
    return str(response.json()["job_id"])


def _seed_study(store, study_id, *, job_id=None, status="waiting", study_json=None):
    """Seed a mechanism study directly in the store."""
    import json as _json

    now = "2026-08-28T00:00:00Z"
    sj = study_json or {"study_id": study_id}
    store.upsert_mechanism_study(
        study_id,
        job_id=job_id,
        study_json=_json.dumps(sj),
        status=status,
        created_at=now,
        updated_at=now,
    )


def _study_payload(study_id: str, study_dir: Path | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "study_id": study_id,
        "status": "waiting",
        "network": {
            "nodes": [{"state_id": "s1"}, {"state_id": "s2"}],
            "edges": [{"step_id": "e1"}],
        },
        "decision_points": [
            {"id": "dec-1", "status": "waiting"},
            {"id": "dec-2", "status": "resolved"},
        ],
        "metadata": {"label": "demo"},
    }
    if study_dir is not None:
        payload["study_dir"] = str(study_dir)
    return payload


# === Read-only route tests (use seeded DB) ===


def test_list_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies returns 200 with seeded data."""
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-1", job_id=job_id, study_json=_study_payload("study-1"))

        listed = client.get("/api/v1/mechanism-studies")
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1
        assert body[0]["id"] == "study-1"
        assert body[0]["n_states"] == 2


def test_detail_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id} returns 200 with seeded data."""
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-detail", job_id=job_id, study_json=_study_payload("study-detail"))

        detail = client.get("/api/v1/mechanism-studies/study-detail")
        assert detail.status_code == 200
        assert detail.json()["id"] == "study-detail"
        assert detail.json()["study_json"]["status"] == "waiting"


def test_detail_404(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id} returns 404 for missing study."""
    with make_client(tmp_path) as client:
        detail = client.get("/api/v1/mechanism-studies/nope")
        assert detail.status_code == 404


def test_report_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id}/report returns 200 with seeded data."""
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        job_detail = client.get(f"/api/v1/jobs/{job_id}")
        work_dir = Path(job_detail.json()["work_dir"])
        study_dir = work_dir / "mechanism_study" / "study-report"
        study_dir.mkdir(parents=True, exist_ok=True)
        (study_dir / "network.json").write_text(
            json.dumps({"nodes": [{"state_id": "s1"}], "edges": []}),
            encoding="utf-8",
        )
        (study_dir / "quality_gates.json").write_text(
            json.dumps({"quality_gates": [{"gate_id": "G0", "status": "pass"}]}),
            encoding="utf-8",
        )

        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(
            store,
            "study-report",
            job_id=job_id,
            study_json=_study_payload("study-report", study_dir),
        )

        report = client.get("/api/v1/mechanism-studies/study-report/report")
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["study_id"] == "study-report"
        assert body["reaction_network"] == {"nodes": [{"state_id": "s1"}], "edges": []}


def test_report_404(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id}/report returns 404 for missing study."""
    with make_client(tmp_path) as client:
        report = client.get("/api/v1/mechanism-studies/nope/report")
        assert report.status_code == 404


def test_reaction_get_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id}/reaction returns 200 with seeded data."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        reaction_data = {"schema_version": 2, "study_id": "study-rxn", "bond_changes": []}
        _seed_study(
            store,
            "study-rxn",
            status="reaction_confirmed",
            study_json={"study_id": "study-rxn"},
        )
        # Update reaction_json via store
        store.update_mechanism_study_reaction(
            "study-rxn",
            reaction_json=json.dumps(reaction_data),
            config_hash="sha256:abc",
            status="reaction_confirmed",
            updated_at="2026-08-28T00:00:00Z",
        )

        response = client.get("/api/v1/mechanism-studies/study-rxn/reaction")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "reaction_confirmed"
        assert body["reaction"]["schema_version"] == 2


def test_reviews_list_readonly(tmp_path: Path) -> None:
    """GET /mechanism-studies/{id}/reviews returns 200 with seeded data."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-rev", study_json={"study_id": "study-rev"})

        response = client.get("/api/v1/mechanism-studies/study-rev/reviews")
        assert response.status_code == 200
        assert response.json()["reviews"] == []


def test_projects_list_readonly(tmp_path: Path) -> None:
    """GET /mechanism-projects returns 200."""
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/mechanism-projects")
        assert response.status_code == 200
        assert response.json()["projects"] == []


# === Write route tests (all should return 410 Gone) ===


def test_create_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies returns 410 Gone."""
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-studies",
            json={"study_id": "study-new", "study_json": {"study_id": "study-new"}},
        )
        assert response.status_code == 410
        assert "已退役" in response.json()["detail"]


def test_reaction_preview_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/reaction/preview returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-preview", study_json={"study_id": "study-preview"})
        response = client.post(
            "/api/v1/mechanism-studies/study-preview/reaction/preview",
            json={
                "reactant": {"source_type": "smiles", "source": "CCO"},
                "product": {"source_type": "smiles", "source": "CCO"},
            },
        )
        assert response.status_code == 410


def test_reaction_confirm_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/reaction/confirm returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-confirm", study_json={"study_id": "study-confirm"})
        response = client.post(
            "/api/v1/mechanism-studies/study-confirm/reaction/confirm",
            json={
                "reactant": {"source_type": "smiles", "source": "CCO"},
                "product": {"source_type": "smiles", "source": "CCO"},
            },
        )
        assert response.status_code == 410


def test_plan_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/mechanism/plan returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-plan", study_json={"study_id": "study-plan"})
        response = client.post(
            "/api/v1/mechanism-studies/study-plan/mechanism/plan",
            json={"plan": {}, "strategy": "guided-scan", "fidelity": "s3"},
        )
        assert response.status_code == 410


def test_decision_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/decisions/{did} returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-dec", study_json={"study_id": "study-dec"})
        response = client.post(
            "/api/v1/mechanism-studies/study-dec/decisions/dec-1",
            json={"resolution": "approve"},
        )
        assert response.status_code == 410


def test_review_decision_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/reviews/{rid}/decision returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-rdec", study_json={"study_id": "study-rdec"})
        response = client.post(
            "/api/v1/mechanism-studies/study-rdec/reviews/dec-1/decision",
            json={"decision": "accept_network"},
        )
        assert response.status_code == 410


def test_resume_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/resume returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-resume", study_json={"study_id": "study-resume"})
        response = client.post("/api/v1/mechanism-studies/study-resume/resume")
        assert response.status_code == 410


def test_promote_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-studies/{id}/promote returns 410 Gone."""
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-promote", study_json={"study_id": "study-promote"})
        response = client.post("/api/v1/mechanism-studies/study-promote/promote")
        assert response.status_code == 410


def test_project_create_returns_410(tmp_path: Path) -> None:
    """POST /mechanism-projects returns 410 Gone."""
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-projects",
            json={"name": "test-project"},
        )
        assert response.status_code == 410


def test_s2_structure_preview_returns_410(tmp_path: Path) -> None:
    """POST /s2/structure-preview returns 410 Gone."""
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/s2/structure-preview",
            json={"source": {"source_type": "xyz_text", "xyz_text": "1\n\nC 0 0 0"}},
        )
        assert response.status_code == 410


# === Unified status tests (read-only, seeded) ===


def test_unified_status_draft(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        store = cast(JobManager, cast(Starlette, client.app).state.job_manager).store
        _seed_study(store, "study-draft", status="draft", study_json={"study_id": "study-draft"})
        detail = client.get("/api/v1/mechanism-studies/study-draft")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "DRAFT"


def test_unified_status_completed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.COMPLETED
        manager.store.update(record)

        store = manager.store
        study_dir = tmp_path / "work" / "mechanism_study" / "study-done"
        study_dir.mkdir(parents=True, exist_ok=True)
        (study_dir / "study.json").write_text(
            json.dumps({"study_id": "study-done", "status": "completed"}),
            encoding="utf-8",
        )
        _seed_study(
            store,
            "study-done",
            job_id=job_id,
            status="completed",
            study_json={"study_id": "study-done", "study_dir": str(study_dir)},
        )
        detail = client.get("/api/v1/mechanism-studies/study-done")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "COMPLETED"
