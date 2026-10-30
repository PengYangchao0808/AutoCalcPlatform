"""P3: status/operation projections must not drop progress or artifact refs."""

from __future__ import annotations

from acp_services.entrypoints.status_projection import (
    project_operation_status,
    project_task_status,
)


def test_workflow_falls_back_to_spec() -> None:
    projection = project_task_status({"spec": {"workflow": "Confsearch"}, "state": "running"})
    assert projection["workflow"] == "Confsearch"


def test_progress_fields_pass_through() -> None:
    projection = project_task_status(
        {
            "id": "job_1",
            "spec": {"workflow": "Confsearch"},
            "state": "running",
            "current_stage": "ensemble_generation",
            "progress": 0.42,
            "progress_state": "determinate",
            "stage_index": 2,
            "stage_total": 5,
            "stage_progress": 0.5,
            "stage_detail": "17/40",
            "live_status": {"stage_label": "CENSO"},
        }
    )
    assert projection["current_stage"] == "ensemble_generation"
    assert projection["progress"] == 0.42
    assert projection["stage_index"] == 2
    assert projection["live_status"] == {"stage_label": "CENSO"}


def test_artifact_uncertainty_reason_passes_through() -> None:
    """A cold remote cache explains WHY artifacts are empty (codex P2, 2026-10-30)."""
    projection = project_task_status(
        {
            "id": "job_r1",
            "spec": {"workflow": "PESsearch"},
            "state": "completed",
            "artifacts": [],
            "artifacts_truncated": False,
            "artifacts_unknown_reason": "remote_cache_only",
        }
    )
    assert projection["artifacts_unknown_reason"] == "remote_cache_only"


def test_operation_status_keeps_artifact_ref() -> None:
    ref = {
        "task_id": "job_1",
        "attempt": 1,
        "artifact_id": "final_report",
        "generation": 3,
        "digest": "sha256:" + "a" * 64,
    }
    projection = project_operation_status(
        {"operation_id": "op_1", "artifact_ref": ref, "state": "ready"}
    )
    assert projection["artifact_ref"] == ref


def test_directory_name_survives_redaction() -> None:
    """Round 3: the absolute work_dir is redacted; the relative name must not be."""
    projection = project_task_status(
        {
            "id": "j1",
            "spec": {"workflow": "PESsearch"},
            "state": "completed",
            "work_dir": "/var/lib/acp/runs/uncategorized/mol_PESsearch__05",
        }
    )
    assert projection["directory_name"] == "mol_PESsearch__05"
