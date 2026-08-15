"""Tests for mechanism-study API routes."""

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


def test_mechanism_studies_create_list_and_detail(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        create = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-1",
                "status": "waiting",
                "study_json": _study_payload("study-1"),
            },
        )
        assert create.status_code == 201, create.text
        created = create.json()
        assert created["id"] == "study-1"
        assert created["job_id"] == job_id
        assert created["study_json"]["metadata"]["label"] == "demo"

        listed = client.get("/api/v1/mechanism-studies")
        assert listed.status_code == 200
        body = listed.json()
        assert len(body) == 1
        assert body[0]["id"] == "study-1"
        assert body[0]["n_states"] == 2
        assert body[0]["n_edges"] == 1
        assert body[0]["n_decisions_pending"] == 1

        detail = client.get("/api/v1/mechanism-studies/study-1")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["id"] == "study-1"
        assert detail_body["study_json"]["status"] == "waiting"
        assert detail_body["decisions"] == []


def test_mechanism_study_create_404_for_missing_job(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": "missing-job",
                "study_id": "study-missing",
                "study_json": _study_payload("study-missing"),
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found: missing-job"


def test_mechanism_study_detail_and_report_404(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        detail = client.get("/api/v1/mechanism-studies/nope")
        assert detail.status_code == 404
        report = client.get("/api/v1/mechanism-studies/nope/report")
        assert report.status_code == 404


def test_mechanism_study_report_reads_partial_files(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        job_detail = client.get(f"/api/v1/jobs/{job_id}")
        assert job_detail.status_code == 200
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

        created = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-report",
                "status": "waiting",
                "study_json": _study_payload("study-report", study_dir),
            },
        )
        assert created.status_code == 201

        report = client.get("/api/v1/mechanism-studies/study-report/report")
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["study_id"] == "study-report"
        assert body["job_id"] == job_id
        assert body["reaction_network"] == {"nodes": [{"state_id": "s1"}], "edges": []}
        assert body["quality_gates"] == {"quality_gates": [{"gate_id": "G0", "status": "pass"}]}
        assert body["mechanism_profile"] is None
        assert body["stationary_points"] is None
        assert body["provenance"] is None


def test_mechanism_decision_resolve_happy_path(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.WAITING_REVIEW
        manager.store.update(record)

        created = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-decision",
                "status": "waiting",
                "study_json": _study_payload("study-decision"),
            },
        )
        assert created.status_code == 201

        manager.store.upsert_decision_point(
            "decision-1",
            study_id="study-decision",
            status="waiting",
            payload=json.dumps({"question": "Continue?"}),
            resolution=None,
            created_at="2026-08-12T00:00:00Z",
            resolved_at=None,
        )

        response = client.post(
            "/api/v1/mechanism-studies/study-decision/decisions/decision-1",
            json={"resolution": "approve"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job_id"] == job_id
        # Resolving a decision requeues the paused job so the study subprocess
        # restarts and resumes from its checkpoint.
        assert body["job_status"] == "starting"
        assert body["decision"]["status"] == "resolved"
        assert body["decision"]["resolution"] == "approve"
        assert body["decision"]["payload"] == {"question": "Continue?"}


def test_mechanism_decision_resolve_404_and_409_paths(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.WAITING_REVIEW
        manager.store.update(record)

        created = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-errors",
                "status": "waiting",
                "study_json": _study_payload("study-errors"),
            },
        )
        assert created.status_code == 201

        missing = client.post(
            "/api/v1/mechanism-studies/study-errors/decisions/missing",
            json={"resolution": "approve"},
        )
        assert missing.status_code == 404

        manager.store.upsert_decision_point(
            "decision-2",
            study_id="study-errors",
            status="resolved",
            payload=json.dumps({"question": "Already done"}),
            resolution="approve",
            created_at="2026-08-12T00:00:00Z",
            resolved_at="2026-08-12T00:05:00Z",
        )
        conflict = client.post(
            "/api/v1/mechanism-studies/study-errors/decisions/decision-2",
            json={"resolution": "reject"},
        )
        assert conflict.status_code == 409
