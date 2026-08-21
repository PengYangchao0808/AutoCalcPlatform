"""Tests for mechanism-study promote endpoint, unified_status, and submit gating."""

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


def _manager(client: TestClient) -> JobManager:
    return cast(JobManager, cast(Starlette, client.app).state.job_manager)


def _set_job_status(client: TestClient, job_id: str, status: JobStatus) -> None:
    manager = _manager(client)
    record = manager.store.get(job_id)
    assert record is not None
    record.status = status
    manager.store.update(record)


def _write_checkpoint(
    study_dir: Path,
    *,
    status: str = "waiting",
    cycle_index: int = 0,
    fingerprints: dict[str, str] | None = None,
    path_results: dict[str, object] | None = None,
    waiting_decisions: list[dict[str, object]] | None = None,
) -> None:
    if waiting_decisions is None:
        waiting_decisions = [
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
            }
        ]
    resolved = {
        "id": "decision-0",
        "type": "sr_cycle_review",
        "status": "resolved",
        "options": ["continue"],
        "payload": {"cycle": 0},
        "created_at": "2026-08-15T00:00:00Z",
    }
    metadata: dict[str, object] = {
        "pending_decisions": {
            str(decision["id"]): {"source_state_id": "state_reactant", "cycle": cycle_index}
            for decision in waiting_decisions
        }
    }
    if path_results is not None:
        metadata["path_results"] = path_results
    study_dir.mkdir(parents=True, exist_ok=True)
    (study_dir / "study.json").write_text(
        json.dumps(
            {
                "study_id": "study-x",
                "status": status,
                "cycle_index": cycle_index,
                "decision_points": [*waiting_decisions, resolved],
                "phase_fingerprints": fingerprints or {},
                "metadata": metadata,
            }
        ),
        encoding="utf-8",
    )


def _create_study(
    client: TestClient,
    study_id: str,
    *,
    study_dir: Path | None = None,
    job_id: str | None = None,
    status: str = "waiting",
) -> None:
    study_json: dict[str, object] = {"study_id": study_id}
    if study_dir is not None:
        study_json["study_dir"] = str(study_dir)
    payload: dict[str, object] = {
        "study_id": study_id,
        "status": status,
        "study_json": study_json,
    }
    if job_id is not None:
        payload["job_id"] = job_id
    created = client.post("/api/v1/mechanism-studies", json=payload)
    assert created.status_code == 201, created.text


def test_promote_happy_path_resolves_decision_and_requeues(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.WAITING_REVIEW)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-sr"
        _write_checkpoint(study_dir)
        _create_study(client, "study-sr", study_dir=study_dir, job_id=job_id)

        response = client.post("/api/v1/mechanism-studies/study-sr/promote")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "promoted"
        assert body["revision_id"] == "promote_01_decision-1"
        assert body["job_id"] == job_id
        assert body["job_status"] != "waiting_review"

        decision_row = _manager(client).store.get_decision_point("decision-1")
        assert decision_row is not None
        assert str(decision_row["status"]) == "resolved"
        assert str(decision_row["resolution"]) == "sr_revision:accept_network"

        record = _manager(client).store.get(job_id)
        assert record is not None
        resolution = (record.result or {}).get("review_resolution") or {}
        handed_off = (resolution.get("decisions") or {}).get("decision-1") or {}
        assert handed_off.get("resolution") == "sr_revision"
        assert handed_off.get("cycle_id") == 0
        assert handed_off["revision"]["decision"] == "accept_network"
        assert handed_off["revision"]["selected_bonds"] == []
        assert handed_off["revision"]["comment"] == "promote to S4"


def test_promote_picks_latest_waiting_review(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.WAITING_REVIEW)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-multi"
        _write_checkpoint(
            study_dir,
            waiting_decisions=[
                {
                    "id": "decision-1",
                    "type": "sr_cycle_review",
                    "status": "waiting",
                    "options": ["accept_network"],
                    "payload": {"cycle": 0, "source_state_id": "state_a"},
                    "created_at": "2026-08-16T00:00:00Z",
                },
                {
                    "id": "decision-2",
                    "type": "sr_cycle_review",
                    "status": "waiting",
                    "options": ["accept_network"],
                    "payload": {"cycle": 0, "source_state_id": "state_b"},
                    "created_at": "2026-08-16T00:05:00Z",
                },
            ],
        )
        _create_study(client, "study-multi", study_dir=study_dir, job_id=job_id)

        response = client.post("/api/v1/mechanism-studies/study-multi/promote")
        assert response.status_code == 200, response.text
        assert response.json()["revision_id"] == "promote_01_decision-2"

        store = _manager(client).store
        promoted_row = store.get_decision_point("decision-2")
        assert promoted_row is not None
        assert str(promoted_row["status"]) == "resolved"
        # The non-promoted review only exists in the checkpoint, never upserted.
        assert store.get_decision_point("decision-1") is None


def test_promote_without_waiting_review_returns_409(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.WAITING_REVIEW)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-none"
        _write_checkpoint(study_dir, waiting_decisions=[])
        _create_study(client, "study-none", study_dir=study_dir, job_id=job_id)

        response = client.post("/api/v1/mechanism-studies/study-none/promote")
        assert response.status_code == 409
        assert response.json()["detail"] == "no waiting SR review to promote"


def test_promote_without_checkpoint_returns_404(tmp_path: Path) -> None:
    # Documented behavior: a missing study.json checkpoint maps to 404,
    # mirroring the review-decision endpoint.
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _create_study(client, "study-nofile", job_id=job_id)

        response = client.post("/api/v1/mechanism-studies/study-nofile/promote")
        assert response.status_code == 404


def test_promote_without_linked_job_returns_400(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        _create_study(client, "study-nojob")

        response = client.post("/api/v1/mechanism-studies/study-nojob/promote")
        assert response.status_code == 400


def test_promote_missing_study_returns_404(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/api/v1/mechanism-studies/missing/promote")
        assert response.status_code == 404


def test_unified_status_draft(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        _create_study(client, "study-draft", status="draft")

        detail = client.get("/api/v1/mechanism-studies/study-draft")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "DRAFT"

        listed = client.get("/api/v1/mechanism-studies")
        assert listed.status_code == 200
        entries = {item["id"]: item for item in listed.json()}
        assert entries["study-draft"]["unified_status"] == "DRAFT"


def test_unified_status_sr_waiting_review(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.WAITING_REVIEW)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-wait"
        _write_checkpoint(study_dir)
        _create_study(client, "study-wait", study_dir=study_dir, job_id=job_id)

        detail = client.get("/api/v1/mechanism-studies/study-wait")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "SR_WAITING_REVIEW"


def test_unified_status_completed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.COMPLETED)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-done"
        _write_checkpoint(study_dir, status="completed")
        _create_study(client, "study-done", study_dir=study_dir, job_id=job_id)

        detail = client.get("/api/v1/mechanism-studies/study-done")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "COMPLETED"


def test_unified_status_running_fingerprint_ladder(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.RUNNING)

        cases: list[tuple[dict[str, str] | None, dict[str, object] | None, str]] = [
            ({}, None, "S1_RUNNING"),
            ({"S0": "a"}, None, "S2_RUNNING"),
            ({"S0": "a", "S1": "b"}, None, "S2_RUNNING"),
            ({"S0": "a", "S1": "b"}, {"route_main": {"status": "ok"}}, "S3_RUNNING"),
            ({"S0": "a", "S4": "c"}, None, "S4_RUNNING"),
        ]
        for index, (fingerprints, path_results, expected) in enumerate(cases):
            study_id = f"study-run-{index}"
            study_dir = tmp_path / "work" / "mechanism_study" / study_id
            _write_checkpoint(
                study_dir,
                status="running",
                fingerprints=fingerprints,
                path_results=path_results,
            )
            _create_study(client, study_id, study_dir=study_dir, job_id=job_id)

            detail = client.get(f"/api/v1/mechanism-studies/{study_id}")
            assert detail.status_code == 200
            assert detail.json()["unified_status"] == expected, (study_id, fingerprints)


def test_unified_status_resumable_and_confirmation_states(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        _create_study(client, "study-r", status="reaction_confirmed")
        _create_study(client, "study-p", status="plan_confirmed")

        listed = client.get("/api/v1/mechanism-studies")
        entries = {item["id"]: item["unified_status"] for item in listed.json()}
        assert entries["study-r"] == "RESUMABLE"
        assert entries["study-p"] == "RESUMABLE"


def test_unified_status_failed_job_wins(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        job_id = _submit_fake_job(client)
        _set_job_status(client, job_id, JobStatus.FAILED)
        study_dir = tmp_path / "work" / "mechanism_study" / "study-fail"
        _write_checkpoint(study_dir, waiting_decisions=[])
        _create_study(client, "study-fail", study_dir=study_dir, job_id=job_id)

        detail = client.get("/api/v1/mechanism-studies/study-fail")
        assert detail.status_code == 200
        assert detail.json()["unified_status"] == "FAILED"


def _mechanism_submit_payload() -> dict[str, object]:
    return {
        "workflow": "mechanism",
        "name": "mechanism-submit",
        "input": {
            "source_type": "mechanism",
            "reactant": {"source_type": "smiles", "source": "C=O"},
            "product": {"source_type": "smiles", "source": "C[O-]"},
        },
        "method": {"fidelity": "s3"},
    }


def test_mechanism_submit_both_sr_flags_returns_422(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        payload = _mechanism_submit_payload()
        method = cast(dict[str, object], payload["method"])
        method["auto_converge"] = True
        method["require_sr_review"] = True

        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 422
        assert "mutually exclusive" in response.json()["detail"]


def test_mechanism_submit_single_sr_flag_still_accepted(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        payload = _mechanism_submit_payload()
        method = cast(dict[str, object], payload["method"])
        method["auto_converge"] = True

        response = client.post("/api/v1/jobs", json=payload)
        assert response.status_code == 201, response.text

        payload_only_review = _mechanism_submit_payload()
        method_review = cast(dict[str, object], payload_only_review["method"])
        method_review["require_sr_review"] = True

        response_review = client.post("/api/v1/jobs", json=payload_only_review)
        assert response_review.status_code == 201, response_review.text
