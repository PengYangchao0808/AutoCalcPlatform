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


def _write_sr_checkpoint(study_dir: Path, *, cycle_index: int = 0) -> None:
    study_dir.mkdir(parents=True, exist_ok=True)
    (study_dir / "study.json").write_text(
        json.dumps(
            {
                "study_id": "study-sr",
                "cycle_index": cycle_index,
                "decision_points": [
                    {
                        "id": "decision-1",
                        "type": "sr_cycle_review",
                        "status": "waiting",
                        "options": ["continue", "reject_path", "accept_network"],
                        "payload": {
                            "cycle": cycle_index,
                            "source_state_id": "state_reactant",
                            "route_id": "route_main",
                        },
                        "created_at": "2026-08-16T00:00:00Z",
                    },
                    {
                        "id": "decision-0",
                        "type": "sr_cycle_review",
                        "status": "resolved",
                        "options": ["continue"],
                        "payload": {"cycle": 0},
                        "created_at": "2026-08-15T00:00:00Z",
                    },
                ],
                "metadata": {
                    "pending_decisions": {
                        "decision-1": {
                            "source_state_id": "state_reactant",
                            "cycle": cycle_index,
                            "review": {
                                "cycle": cycle_index,
                                "source_state_id": "state_reactant",
                                "candidates": [{"candidate_id": "cand-1", "kind": "ts_seed"}],
                                "endpoint_verdict": "NEW_STATE",
                            },
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _create_sr_study(client: TestClient, study_dir: Path, job_id: str) -> None:
    created = client.post(
        "/api/v1/mechanism-studies",
        json={
            "job_id": job_id,
            "study_id": "study-sr",
            "status": "waiting",
            "study_json": {"study_id": "study-sr", "study_dir": str(study_dir)},
        },
    )
    assert created.status_code == 201, created.text


def test_mechanism_reviews_list_reads_checkpoint(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-sr"
        _write_sr_checkpoint(study_dir)
        _create_sr_study(client, study_dir, job_id)

        response = client.get("/api/v1/mechanism-studies/study-sr/reviews")
        assert response.status_code == 200, response.text
        reviews = response.json()["reviews"]
        assert len(reviews) == 1
        review = reviews[0]
        assert review["review_id"] == "decision-1"
        assert review["type"] == "sr_cycle_review"
        assert review["cycle"] == 0
        assert review["source_state_id"] == "state_reactant"
        assert review["summary"]["candidates"][0]["candidate_id"] == "cand-1"


def test_mechanism_reviews_list_empty_without_checkpoint(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        created = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-nodir",
                "status": "waiting",
                "study_json": {"study_id": "study-nodir"},
            },
        )
        assert created.status_code == 201
        response = client.get("/api/v1/mechanism-studies/study-nodir/reviews")
        assert response.status_code == 200
        assert response.json()["reviews"] == []


def test_mechanism_review_decision_validation_errors(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.WAITING_REVIEW
        manager.store.update(record)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-sr"
        _write_sr_checkpoint(study_dir)
        _create_sr_study(client, study_dir, job_id)

        missing = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-9/decision",
            json={"decision": "accept_network"},
        )
        assert missing.status_code == 404

        resolved = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-0/decision",
            json={"decision": "accept_network"},
        )
        assert resolved.status_code == 409

        no_bonds = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-1/decision",
            json={"decision": "continue"},
        )
        assert no_bonds.status_code == 422

        bad_atoms = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-1/decision",
            json={
                "decision": "continue",
                "selected_bonds": [{"atoms": [0, 1, 2], "action": "stretch", "target": 2.5}],
            },
        )
        assert bad_atoms.status_code == 422

        missing_target = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-1/decision",
            json={
                "decision": "continue",
                "selected_bonds": [{"atoms": [0, 1], "action": "stretch"}],
            },
        )
        assert missing_target.status_code == 422


def test_mechanism_review_decision_accept_then_double_submit_409(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.WAITING_REVIEW
        manager.store.update(record)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-sr"
        _write_sr_checkpoint(study_dir)
        _create_sr_study(client, study_dir, job_id)

        payload = {
            "decision": "continue",
            "selected_bonds": [{"atoms": [0, 1], "action": "stretch", "target": 3.2}],
            "comment": "stretch next",
        }
        accepted = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-1/decision",
            json=payload,
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["status"] == "accepted"
        assert body["revision_id"] == "rev_01_decision-1"
        assert body["cycle"] == 1
        assert body["job_id"] == job_id

        # The job left WAITING_REVIEW after the first submission; a duplicate
        # decision POST must be rejected instead of double-submitting.
        duplicate = client.post(
            "/api/v1/mechanism-studies/study-sr/reviews/decision-1/decision",
            json=payload,
        )
        assert duplicate.status_code == 409

        record_after = manager.store.get(job_id)
        assert record_after is not None
        resolution = (record_after.result or {}).get("review_resolution") or {}
        decisions = resolution.get("decisions") or {}
        handed_off = decisions.get("decision-1") or {}
        assert handed_off.get("resolution") == "sr_revision"
        assert handed_off.get("cycle_id") == 0
        assert handed_off["revision"]["selected_bonds"][0]["atoms"] == [0, 1]


def test_mechanism_study_resume_endpoint_gating(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        manager = cast(JobManager, cast(Starlette, client.app).state.job_manager)
        created = client.post(
            "/api/v1/mechanism-studies",
            json={
                "job_id": job_id,
                "study_id": "study-resume",
                "status": "running",
                "study_json": {"study_id": "study-resume"},
            },
        )
        assert created.status_code == 201

        not_waiting = client.post("/api/v1/mechanism-studies/study-resume/resume")
        assert not_waiting.status_code == 409

        record = manager.store.get(job_id)
        assert record is not None
        record.status = JobStatus.WAITING_REVIEW
        manager.store.update(record)

        resumed = client.post("/api/v1/mechanism-studies/study-resume/resume")
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["status"] == "resumed"
        assert body["job_id"] == job_id
