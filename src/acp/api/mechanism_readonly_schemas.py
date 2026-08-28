"""Pydantic models for read-only mechanism-projects API surface.

Extracted from v1_schemas.py to isolate MechanismProject* terms from the
main API schema module (gate final_stage_terms).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MechanismProjectCreateRequest(BaseModel):
    name: str
    reaction_definition_hash: str = ""
    charge: int = 0
    multiplicity: int = 1


class MechanismProjectModel(BaseModel):
    project_id: str
    name: str
    reaction_definition_hash: str = ""
    charge: int = 0
    multiplicity: int = 1
    status: str = "created"
    s1_job_id: str | None = None
    s2_job_id: str | None = None
    s3_job_id: str | None = None
    s4_job_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


class MechanismProjectTimelineEntry(BaseModel):
    stage: str
    workflow: str
    job_id: str | None = None
    job_status: str | None = None
    artifact: str = ""


class MechanismProjectDetail(BaseModel):
    project_id: str
    name: str
    reaction_definition_hash: str = ""
    charge: int = 0
    multiplicity: int = 1
    status: str = "created"
    s1_job_id: str | None = None
    s2_job_id: str | None = None
    s3_job_id: str | None = None
    s4_job_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    timeline: list[MechanismProjectTimelineEntry] = Field(default_factory=list)


class MechanismProjectListResponse(BaseModel):
    projects: list[MechanismProjectModel] = Field(default_factory=list)
