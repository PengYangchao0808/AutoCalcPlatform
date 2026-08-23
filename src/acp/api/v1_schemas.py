"""
API v1 Schemas
==============

Pydantic models for the ACP Workbench v2 ``/api/v1`` surface.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""


class V1JobRecordModel(BaseModel):
    id: str
    spec: V1JobSpecModel
    status: str
    work_dir: str = ""
    project_id: str | None = None
    project_name: str | None = None
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
    remote_job_id: str | None = None
    group_id: str | None = None
    study_id: str | None = None
    study_status: str | None = None
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
    mechanism_project_id: str | None = None
    execution_mode: Literal["local", "remote"] | None = None
    target_node: str | None = None
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""


class MechanismRolePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    source_type: Literal["smiles", "xyz_text", "structure_asset"]
    source: str = Field(min_length=1)
    asset_id: str | None = None
    charge: int | None = None
    multiplicity: int | None = None

    @model_validator(mode="after")
    def _require_asset_id_for_structure_asset(self) -> MechanismRolePayload:
        if self.source_type == "structure_asset" and not self.asset_id:
            raise ValueError("asset_id is required when source_type='structure_asset'")
        return self


class MechanismCoordinateSpec(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str
    kind: Literal["distance", "angle", "dihedral"]
    atoms: list[int]
    role: Literal["drive", "freeze", "monitor"]
    start: float | None = None
    end: float | None = None

    @model_validator(mode="after")
    def _validate_backend_coordinate_contract(self) -> MechanismCoordinateSpec:
        expected_atoms = {"distance": 2, "angle": 3, "dihedral": 4}[self.kind]
        if len(self.atoms) != expected_atoms:
            raise ValueError(f"kind='{self.kind}' requires {expected_atoms} atoms")
        if self.role == "drive" and (self.start is None or self.end is None):
            raise ValueError("drive coordinates require both start and end values")
        if self.role == "freeze" and self.start is None:
            raise ValueError("freeze coordinates require a start value")
        return self


class MechanismCoordinatePlan(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    coordinates: list[MechanismCoordinateSpec]
    points: int = Field(ge=2)
    coupling: Literal["synchronous"]
    start_from: Literal["reactant", "product", "custom"]

    @model_validator(mode="after")
    def _require_drive_coordinate(self) -> MechanismCoordinatePlan:
        if not any(coordinate.role == "drive" for coordinate in self.coordinates):
            raise ValueError("coordinate_plan requires at least one drive coordinate")
        return self


class MechanismRouteIn(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    route_id: str
    coordinate_plan: MechanismCoordinatePlan | None = None
    path_strategy: Literal["guided-scan", "rph-reverse", "direct-ts"]
    fidelity: Literal["s3", "s4"] | None = None
    ts_guess_id: str | None = None

    @model_validator(mode="after")
    def _validate_route_requirements(self) -> MechanismRouteIn:
        if self.path_strategy == "direct-ts":
            if not self.ts_guess_id:
                raise ValueError("direct-ts routes require ts_guess_id")
            return self
        if self.coordinate_plan is None:
            raise ValueError("coordinate_plan is required unless path_strategy='direct-ts'")
        return self


class MechanismJobInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    source_type: Literal["mechanism"]
    reactant: MechanismRolePayload
    product: MechanismRolePayload | None = None
    ts_guess: MechanismRolePayload | None = None
    routes: list[MechanismRouteIn] = Field(default_factory=list)


class MechanismJobMethod(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    schema_id: str | None = None
    profile_id: str | None = None
    preset: str | None = None
    strategy: Literal["guided-scan", "rph-reverse", "direct-ts"] | None = None
    fidelity: Literal["s3", "s4"] | None = None
    scan_points: int | None = Field(default=None, ge=2)
    irc_points: int | None = Field(default=None, ge=1)
    conformer_mode: Literal["auto", "censo-lite", "xtb-fast"] | None = None
    max_elementary_steps: int | None = Field(default=None, ge=1)
    promotion_policy: Literal["all_confirmed", "rate_relevant", "user_selected"] | None = None
    int_extension: bool | None = None
    auto_converge: bool | None = None
    require_sr_review: bool | None = None
    study_id: str | None = None
    levels: dict[str, Any] | None = None


class ReactionPreviewRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    reactant: MechanismRolePayload
    product: MechanismRolePayload
    ts_guess: MechanismRolePayload | None = None
    charge: int = 0
    multiplicity: int = 1
    selected_candidate: int | None = None
    manual_bond_editing: bool | None = None


class ReactionConfirmRequest(ReactionPreviewRequest):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    user_note: str = ""
    manual_bond_changes: list[dict[str, Any]] | None = None
    allow_zero_changes: bool = False


class ReactionPreviewResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    status: Literal["ok", "confirmation_required"]
    mapping_status: str
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    selected_candidate: int | None = None
    unmatched_reactant_atoms: list[int] = Field(default_factory=list)
    unmatched_product_atoms: list[int] = Field(default_factory=list)
    bond_changes: list[dict[str, Any]] = Field(default_factory=list)
    suggested_plan: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    preview_hash: str
    manual_mode: bool = False


class ReactionConfirmResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    status: Literal["locked"]
    reaction: dict[str, Any]
    config_hash: str
    suggested_plan: dict[str, Any] | None = None


class MechanismPlanRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    plan: dict[str, Any]
    strategy: str
    fidelity: str


class ReactionGetResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    reaction: dict[str, Any] | None = None
    status: str = ""


class MechanismPlanResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    status: str
    plan_hash: str


class V1JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    workflow: str
    project_id: str | None = None
    mechanism_project_id: str | None = None


class JobMoveRequest(BaseModel):
    project_id: str


class V1JobRerunRequest(BaseModel):
    """Optional body for POST /jobs/{id}/rerun."""

    project_id: str | None = None


class V1JobPurgeRequest(BaseModel):
    """Body for POST /jobs/purge (plan §4.6)."""

    job_ids: list[str] | None = None
    status: str | None = None
    project_id: str | None = None
    older_than_days: float | None = None
    delete_data: bool = False
    force_cancel: bool = False


class V1JobPurgeResult(BaseModel):
    """Per-job purge report entry (``JobManager.purge_jobs`` contract)."""

    job_id: str
    ok: bool
    action: str
    error: str | None = None


class V1JobPurgeResponse(BaseModel):
    results: list[V1JobPurgeResult] = Field(default_factory=list)


class V1JobListResponse(BaseModel):
    jobs: list[V1JobRecordModel] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rich job detail (plan §4.5) — GET /api/v1/jobs/{id}/detail.
# Field names are the frozen frontend contract; do not rename.
# ---------------------------------------------------------------------------


class JobStageEntry(BaseModel):
    """Stage projection (stage_tasks ``state`` → ``status``)."""

    stage_name: str
    status: str = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    retry_count: int = 0
    status_detail: str | None = None


class JobMetrics(BaseModel):
    """Display-only QC runtime metrics from the ``metrics.json`` sidecar."""

    engine: str | None = None
    last_energy_hartree: float | None = None
    opt_converged: bool | None = None
    updated_at: str | None = None


class JobArtifactSummaryEntry(BaseModel):
    """Artifact projection; ``size`` is null when not locally stat-able."""

    type: str
    path: str
    size: int | None = None


class JobErrorDetail(BaseModel):
    """Failure detail block; null when the job never failed."""

    error: str | None = None
    stderr_tail: str = ""
    failed_stage: str | None = None


class JobDiskState(BaseModel):
    """Work-dir probe results."""

    work_dir_exists: bool = False
    has_state_json: bool = False
    has_study_checkpoint: bool = False
    has_review_payload: bool = False
    size_bytes: int = 0


class JobRecovery(BaseModel):
    """Server-computed recovery matrix (§4.4)."""

    can_pause: bool = False
    can_unpause: bool = False
    can_continue: bool = False
    continue_mode: str = ""
    continue_notes: str = ""
    can_rerun: bool = False
    can_purge: bool = True
    can_cancel: bool = False


class V1JobDetailResponse(BaseModel):
    """Rich job detail: DB + disk aggregation + recovery matrix."""

    job: V1JobRecordModel
    stages: list[JobStageEntry] = Field(default_factory=list)
    artifacts_summary: list[JobArtifactSummaryEntry] = Field(default_factory=list)
    error_detail: JobErrorDetail | None = None
    disk_state: JobDiskState = Field(default_factory=JobDiskState)
    recovery: JobRecovery = Field(default_factory=JobRecovery)
    metrics: JobMetrics | None = None


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
    unified_status: str = ""


class MechanismStudyDetail(BaseModel):
    id: str
    job_id: str | None = None
    status: str
    created_at: str = ""
    updated_at: str = ""
    study_json: dict[str, Any] = Field(default_factory=dict)
    decisions: list[DecisionPointModel] = Field(default_factory=list)
    unified_status: str = ""


class MechanismStudyCreateRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    job_id: str | None = None
    study_id: str
    status: str = "draft"
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


class SRSelectedBond(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    atoms: list[int]
    action: Literal["stretch", "form", "keep"]
    start: float | None = None
    target: float | None = None


class SRDecisionRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    decision: Literal["continue", "reject_path", "accept_network"]
    selected_bonds: list[SRSelectedBond] = Field(default_factory=list)
    parent_state: str | None = None
    comment: str = ""
    config_hash: str | None = None


class SRDecisionResponse(BaseModel):
    status: str
    revision_id: str
    cycle: int
    job_id: str | None = None
    job_status: str = ""


class SRReviewListResponse(BaseModel):
    reviews: list[dict[str, Any]] = Field(default_factory=list)


class StudyResumeResponse(BaseModel):
    status: str
    job_id: str
    job_status: str = ""


class StudyPromoteResponse(BaseModel):
    status: str
    revision_id: str
    job_id: str | None = None
    job_status: str = ""


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
    detected_format: str | None = None


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    size: int
    structures: list[StructureAssetModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ok: bool = False
    detected_format: str | None = None


class StructureSourceSummary(BaseModel):
    source_id: str
    job_id: str
    job_name: str
    workflow: str
    project_id: str | None = None
    completed_at: str = ""
    label: str = ""
    path: str = ""
    formula: str = ""
    atom_count: int = 0
    charge: int = 0
    multiplicity: int = 1
    has_3d: bool = True
    remote: bool = False
    needs_fetch: bool = False


class StructureSourceListResponse(BaseModel):
    sources: list[StructureSourceSummary] = Field(default_factory=list)


class StructureSourceDetailResponse(BaseModel):
    source_id: str
    checksum: str | None = None
    structure: StructureAssetModel


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


class V1SoftwareCandidate(BaseModel):
    """One discovered install of a QC executable."""

    path: str
    version: str = ""
    source: str | None = None


class V1SoftwareEntry(BaseModel):
    """Discovery summary for one software package.

    ``resolved``/``source`` describe the path ``resolve_executable``
    actually picks (explicit pin wins; discovery never re-orders
    resolution). ``multiple`` flags multi-install machines.
    """

    name: str
    resolved: str | None = None
    version: str = ""
    source: str | None = None
    multiple: bool = False
    candidates: list[V1SoftwareCandidate] = Field(default_factory=list)


class V1SoftwareDiscoveryResponse(BaseModel):
    """Response for ``GET /api/v1/software/discovery``."""

    software: list[V1SoftwareEntry] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Mechanism project (design §9) — links four stage jobs.
# ---------------------------------------------------------------------------


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
    "JobArtifactSummaryEntry",
    "JobDiskState",
    "JobErrorDetail",
    "JobMoveRequest",
    "JobRecovery",
    "JobStageEntry",
    "MaintenanceCleanupResponse",
    "MechanismProjectCreateRequest",
    "MechanismProjectDetail",
    "MechanismProjectListResponse",
    "MechanismProjectModel",
    "MechanismProjectTimelineEntry",
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
    "V1JobCreatedResponse",
    "V1JobCreateRequest",
    "V1JobDetailResponse",
    "V1JobListResponse",
    "V1JobPurgeRequest",
    "V1JobPurgeResponse",
    "V1JobPurgeResult",
    "V1JobRecordModel",
    "V1JobRerunRequest",
    "V1JobSpecModel",
    "V1SoftwareCandidate",
    "V1SoftwareDiscoveryResponse",
    "V1SoftwareEntry",
]
