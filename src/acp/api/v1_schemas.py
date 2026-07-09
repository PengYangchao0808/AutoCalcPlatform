"""
API v1 Schemas
==============

Pydantic models for the ACP Workbench v2 ``/api/v1`` surface.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectModel(BaseModel):
    project_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    run_root: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ProjectCreateRequest(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    settings: dict[str, Any] | None = None


class ProjectListResponse(BaseModel):
    projects: list[ProjectModel] = Field(default_factory=list)


class StageTaskModel(BaseModel):
    task_id: str
    job_id: str
    stage_name: str
    task_type: str | None = None
    state: str
    exit_status: int | None = None
    retry_count: int = 0
    pid: int | None = None
    stderr_summary: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str = ""


class StageTaskListResponse(BaseModel):
    tasks: list[StageTaskModel] = Field(default_factory=list)


class ArtifactModel(BaseModel):
    artifact_id: str
    task_id: str | None = None
    job_id: str
    artifact_type: str
    file_path: str
    checksum: str | None = None
    size_bytes: int = 0
    parser_status: str = "pending"
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactModel] = Field(default_factory=list)


class V1JobSpecModel(BaseModel):
    workflow: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None


class V1JobRecordModel(BaseModel):
    id: str
    spec: V1JobSpecModel
    status: str
    work_dir: str = ""
    project_id: str | None = None
    input_hash: str | None = None
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    result: dict[str, Any] | None = None


class V1JobCreateRequest(BaseModel):
    workflow: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None


class V1JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    workflow: str
    project_id: str | None = None


class V1JobListResponse(BaseModel):
    jobs: list[V1JobRecordModel] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class MoleculeResolveRequest(BaseModel):
    smiles: str | None = None
    inchi: str | None = None
    molfile: str | None = None
    xyz: str | None = None


class MoleculeResolveResponse(BaseModel):
    smiles: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    formula: str | None = None
    charge: int = 0
    multiplicity: int = 1
    source: str = ""
    valid: bool = True
    error: str | None = None


class MoleculeEmbedRequest(BaseModel):
    smiles: str | None = None
    molfile: str | None = None
    charge: int = 0
    multiplicity: int = 1


class MoleculeEmbedResponse(BaseModel):
    xyz: str
    smiles: str | None = None
    formula: str | None = None
    num_atoms: int = 0
    error: str | None = None


class StructureAssetModel(BaseModel):
    asset_id: str
    name: str
    source_type: str = ""
    original_format: str = ""
    xyz: str | None = None
    molfile: str | None = None
    has_3d: bool = False
    charge: int = 0
    multiplicity: int = 1
    atom_count: int = 0
    formula: str = ""
    smiles: str | None = None
    normalized_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class StructureParseRequest(BaseModel):
    content: str
    format: str = "auto"
    filename: str = ""


class StructureParseResponse(BaseModel):
    structures: list[StructureAssetModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ok: bool = False


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    size: int
    structures: list[StructureAssetModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ok: bool = False


class ValidateMethodRequest(BaseModel):
    schema_id: str
    levels: dict[str, Any] = Field(default_factory=dict)


class ValidateMethodResponse(BaseModel):
    valid: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    normalized_levels: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ArtifactListResponse",
    "ArtifactModel",
    "MoleculeEmbedRequest",
    "MoleculeEmbedResponse",
    "MoleculeResolveRequest",
    "MoleculeResolveResponse",
    "ProjectCreateRequest",
    "ProjectListResponse",
    "ProjectModel",
    "ProjectUpdateRequest",
    "StageTaskListResponse",
    "StageTaskModel",
    "StructureAssetModel",
    "StructureParseRequest",
    "StructureParseResponse",
    "UploadResponse",
    "ValidateMethodRequest",
    "ValidateMethodResponse",
    "V1JobCreateRequest",
    "V1JobCreatedResponse",
    "V1JobListResponse",
    "V1JobRecordModel",
    "V1JobSpecModel",
]
