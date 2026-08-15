"""
API v1 Schemas
==============

Pydantic models for the ACP Workbench v2 ``/api/v1`` surface.
"""

from __future__ import annotations

from typing import Any, Literal

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
    execution_mode: Literal["local", "remote"] | None = None
    target_node: str | None = None


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
    execution_mode: Literal["local", "remote"] | None = None
    target_node: str | None = None


class V1JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    workflow: str
    project_id: str | None = None


class JobMoveRequest(BaseModel):
    project_id: str


class V1JobListResponse(BaseModel):
    jobs: list[V1JobRecordModel] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class DecisionPointModel(BaseModel):
    id: str
    study_id: str
    status: Literal["waiting", "resolved", "superseded"]
    payload: dict[str, Any] = Field(default_factory=dict)
    resolution: str | None = None
    created_at: str = ""
    resolved_at: str | None = None


class MechanismStudySummary(BaseModel):
    id: str
    job_id: str | None = None
    status: str
    created_at: str = ""
    updated_at: str = ""
    n_states: int = 0
    n_edges: int = 0
    n_decisions_pending: int = 0


class MechanismStudyDetail(BaseModel):
    id: str
    job_id: str | None = None
    status: str
    created_at: str = ""
    updated_at: str = ""
    study_json: dict[str, Any] = Field(default_factory=dict)
    decisions: list[DecisionPointModel] = Field(default_factory=list)


class MechanismStudyCreateRequest(BaseModel):
    job_id: str
    study_id: str
    status: str = "pending"
    study_json: dict[str, Any] = Field(default_factory=dict)


class MechanismStudyReportResponse(BaseModel):
    study_id: str
    job_id: str | None = None
    reaction_network: Any | None = None
    mechanism_profile: Any | None = None
    stationary_points: Any | None = None
    quality_gates: Any | None = None
    provenance: Any | None = None


class DecisionResolveRequest(BaseModel):
    resolution: str


class DecisionResolveResponse(BaseModel):
    decision: DecisionPointModel
    job_id: str | None = None
    job_status: str = ""


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


# ---------------------------------------------------------------------------
# Hessian preview (plan §12) — returns the resolved Recalc_Hess policy for
# one or more structures without running any ORCA calculation.
# ---------------------------------------------------------------------------


class HessianPreviewStructure(BaseModel):
    """A single structure submitted for Hessian-policy preview.

    Either ``symbols`` (preferred) or ``formula`` (fallback) must be
    provided when the policy resolves to ``auto``; explicit 0/N values
    do not require either.
    """

    name: str = ""
    symbols: list[str] | None = None
    formula: str | None = None


class HessianPreviewRequest(BaseModel):
    """Request body for ``POST /api/v1/hessian-preview``.

    ``recalc_hess`` mirrors the public field semantics: ``"auto"`` / ``0``
    / ``N`` / ``None`` (follow config). When omitted, the server's config
    default applies.
    """

    schema_id: str = ""
    level_id: str = ""
    recalc_hess: Any = None
    structures: list[HessianPreviewStructure] = Field(default_factory=list)


class HessianPreviewResult(BaseModel):
    """Resolved policy for a single structure."""

    name: str = ""
    enabled: bool = False
    interval: int = 0
    source: str = "config"
    reason: str = "auto"
    heavy_elements: list[str] = Field(default_factory=list)
    triggering_elements: list[str] = Field(default_factory=list)
    error: str | None = None


class HessianPreviewResponse(BaseModel):
    """Aggregated preview response."""

    results: list[HessianPreviewResult] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class RemoteFileEntry(BaseModel):
    """A single file or directory entry inside a remote job directory."""

    name: str
    size: int = 0
    mtime: float = 0.0
    is_dir: bool = False


class RemoteFileListResponse(BaseModel):
    """Response for ``GET /jobs/{id}/remote-files``."""

    job_id: str
    node: str = ""
    remote_dir: str = ""
    files: list[RemoteFileEntry] = Field(default_factory=list)
    truncated: bool = False


class RemoteLogTailResponse(BaseModel):
    """Response for ``GET /jobs/{id}/remote-logs/{name}``."""

    job_id: str
    name: str
    lines: list[str] = Field(default_factory=list)


class RemoteFilePreviewResponse(BaseModel):
    """Response for ``GET /jobs/{id}/remote-files/{path}/preview``."""

    job_id: str
    path: str
    mode: str
    content: str | dict[str, Any] | None = None
    truncated: bool = False
    size: int = 0


class RemoteFileChecksumResponse(BaseModel):
    """Response for ``GET /jobs/{id}/remote-files/{path}/checksum``."""

    sha256: str


class MaintenanceCleanupResponse(BaseModel):
    """Response for ``POST /api/v1/maintenance/cleanup`` (Phase 5B).

    Carries the outcome of a local disk-protection sweep.  No auth is
    enforced in the current trusted-network deployment; see the route
    docstring.
    """

    work_dirs_removed: int = 0
    db_records_removed: int = 0
    freed_bytes_est: int = 0
    freed_human: str = ""
    errors: list[str] = Field(default_factory=list)
    dry_run: bool = False
    disk_usage_before: int = 0
    disk_usage_after: int = 0
    capped: bool = False
    duration_ms: int = 0


class DiskUsageResponse(BaseModel):
    """Response for ``GET /api/v1/maintenance/disk-usage`` (Phase 5B)."""

    run_root: str
    total_bytes: float = 0.0
    used_bytes: float = 0.0
    free_bytes: float = 0.0
    percent_used: float = 0.0
    job_count: int = 0
    cleanup_enabled: bool = False


class NodeStatusModel(BaseModel):
    """Remote node status item (Phase 6)."""

    name: str
    host: str
    status: str  # "online" | "offline" | "degraded"
    running_jobs: int = 0
    max_jobs: int = 0
    disk_usage_pct: int = 0
    last_check: str = ""
    error: str | None = None


class NodeListResponse(BaseModel):
    """Response for ``GET /api/v1/nodes`` (Phase 6)."""

    nodes: list[NodeStatusModel] = Field(default_factory=list)
    auto_select: bool = True


class NodePingResponse(BaseModel):
    """Response for ``POST /api/v1/nodes/{name}/ping`` (Phase 6)."""

    reachable: bool
    node: str
    status: str = "offline"
    error: str | None = None


class NodeBootstrapResponse(BaseModel):
    """Response for ``POST /api/v1/nodes/{name}/bootstrap``.

    Provisions the node with the ACP runtime dependencies from the synced
    ``requirements-node.txt``.  ``stdout``/``stderr`` are pip's full output;
    clients typically tail them for display.
    """

    node: str
    reachable: bool
    ok: bool = False
    exit_code: int | None = None
    python_executable: str = "python"
    requirements_path: str = ""
    sync_uploaded: int = 0
    sync_errors: list[str] = Field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


__all__ = [
    "ArtifactListResponse",
    "ArtifactModel",
    "DecisionPointModel",
    "DecisionResolveRequest",
    "DecisionResolveResponse",
    "DiskUsageResponse",
    "HessianPreviewRequest",
    "HessianPreviewResponse",
    "HessianPreviewResult",
    "HessianPreviewStructure",
    "JobMoveRequest",
    "MaintenanceCleanupResponse",
    "MechanismStudyCreateRequest",
    "MechanismStudyDetail",
    "MechanismStudyReportResponse",
    "MechanismStudySummary",
    "MoleculeEmbedRequest",
    "MoleculeEmbedResponse",
    "MoleculeResolveRequest",
    "MoleculeResolveResponse",
    "NodeBootstrapResponse",
    "NodeListResponse",
    "NodePingResponse",
    "NodeStatusModel",
    "ProjectCreateRequest",
    "ProjectListResponse",
    "ProjectModel",
    "ProjectUpdateRequest",
    "RemoteFileEntry",
    "RemoteFileListResponse",
    "RemoteFilePreviewResponse",
    "RemoteFileChecksumResponse",
    "RemoteLogTailResponse",
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
