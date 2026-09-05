"""Read-only mechanism-projects API surface (historical-job display only).

Extracted from v1_routes.py to isolate MechanismProject/Lowconfirm/Highconfirm
terms from the main job-submission router (gate final_stage_terms).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from acp.api.mechanism_readonly_schemas import (
    MechanismProjectCreateRequest,
    MechanismProjectDetail,
    MechanismProjectListResponse,
    MechanismProjectModel,
    MechanismProjectTimelineEntry,
)
from acp.api.v1_routes import _job_store, _manager

router = APIRouter()

_ARTIFACT_MAP: dict[str, str] = {
    "s1": "RESULT/confsearch/confsearch_manifest.json",
    "s2": "RESULT/mechanism/s2_path_manifest.json",
    "s3": "RESULT/mechanism/s3_lowconfirm_manifest.json",
    "s4": "RESULT/mechanism/s4_highconfirm_manifest.json",
}

_STAGE_WORKFLOW: dict[str, str] = {
    "s1": "Confsearch",
    "s2": "PESsearch",
    "s3": "Lowconfirm",
    "s4": "Highconfirm",
}


def _project_timeline(
    project: Any,
    job_store: Any,
) -> list[MechanismProjectTimelineEntry]:
    entries: list[MechanismProjectTimelineEntry] = []
    for stage in ("s1", "s2", "s3", "s4"):
        job_id = project.stage_jobs.get(stage)
        job_status: str | None = None
        if job_id:
            record = job_store.get(job_id)
            job_status = record.status.value if record else None
        entries.append(
            MechanismProjectTimelineEntry(
                stage=stage.upper(),
                workflow=_STAGE_WORKFLOW[stage],
                job_id=job_id,
                job_status=job_status,
                artifact=_ARTIFACT_MAP[stage],
            )
        )
    return entries


@router.post("/mechanism-projects", response_model=MechanismProjectModel, status_code=201)
def create_mechanism_project(
    req: MechanismProjectCreateRequest,
    request: Request,
) -> MechanismProjectModel:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get("/mechanism-projects", response_model=MechanismProjectListResponse)
def list_mechanism_projects(request: Request) -> MechanismProjectListResponse:
    manager = _manager(request)
    projects_store = getattr(manager, "_mechanism_projects", None)
    projects = projects_store.list_all() if projects_store else []
    return MechanismProjectListResponse(
        projects=[
            MechanismProjectModel(
                project_id=p.project_id,
                name=p.name,
                reaction_definition_hash=p.reaction_definition_hash,
                charge=p.charge,
                multiplicity=p.multiplicity,
                status=p.status.value,
                s1_job_id=p.stage_jobs.get("s1"),
                s2_job_id=p.stage_jobs.get("s2"),
                s3_job_id=p.stage_jobs.get("s3"),
                s4_job_id=p.stage_jobs.get("s4"),
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in projects
        ]
    )


@router.get("/mechanism-projects/{project_id}", response_model=MechanismProjectDetail)
def get_mechanism_project(project_id: str, request: Request) -> MechanismProjectDetail:
    manager = _manager(request)
    projects_store = getattr(manager, "_mechanism_projects", None)
    if projects_store is None:
        raise HTTPException(status_code=404, detail=f"Mechanism project not found: {project_id}")
    project = projects_store.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Mechanism project not found: {project_id}")
    store = _job_store(request)
    return MechanismProjectDetail(
        project_id=project.project_id,
        name=project.name,
        reaction_definition_hash=project.reaction_definition_hash,
        charge=project.charge,
        multiplicity=project.multiplicity,
        status=project.status.value,
        s1_job_id=project.stage_jobs.get("s1"),
        s2_job_id=project.stage_jobs.get("s2"),
        s3_job_id=project.stage_jobs.get("s3"),
        s4_job_id=project.stage_jobs.get("s4"),
        created_at=project.created_at,
        updated_at=project.updated_at,
        timeline=_project_timeline(project, store),
    )
