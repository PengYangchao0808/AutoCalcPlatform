"""
API v1 Routes
=============

FastAPI router for ACP Workbench v2 resources under ``/api/v1``.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportCallInDefaultInitializer=false, reportUnnecessaryComparison=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportIndexIssue=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportUnknownLambdaType=false, reportUnusedVariable=false, reportMissingParameterType=false
from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import urllib.parse
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

try:
    from rdkit.Chem import rdDetermineBonds
except ImportError:  # pragma: no cover
    rdDetermineBonds = None  # noqa: N816

try:
    from zipstream import ZipStream
except ImportError:  # pragma: no cover
    ZipStream = None

from acp.api.routes import (
    get_backends as legacy_get_backends,
)
from acp.api.routes import (
    get_protocols as legacy_get_protocols,
)
from acp.api.routes import (
    get_status as legacy_get_status,
)
from acp.api.routes import (
    get_workflows as legacy_get_workflows,
)
from acp.api.routes import (
    stream_job_events as legacy_stream_job_events,
)
from acp.api.schemas import (
    BackendsResponse,
    FileEntry,
    FileManifestResponse,
    PinnedProduct,
    ProtocolsResponse,
    StatusResponse,
    WorkflowsResponse,
)
from acp.api.v1_schemas import (
    ArtifactListResponse,
    ArtifactModel,
    BondLengthScanJobInput,
    DecisionPointModel,
    DecisionResolveRequest,
    DecisionResolveResponse,
    DiskUsageResponse,
    EnergyGraphResponse,
    HessianPreviewRequest,
    HessianPreviewResponse,
    HessianPreviewResult,
    HessianPreviewStructure,
    JobArtifactSummaryEntry,
    JobDiskState,
    JobErrorDetail,
    JobMetrics,
    JobMoveRequest,
    JobRecovery,
    JobStageEntry,
    MaintenanceCleanupResponse,
    MechanismPlanRequest,
    MechanismPlanResponse,
    MechanismStudyCreateRequest,
    MechanismStudyDetail,
    MechanismStudyReportResponse,
    MechanismStudySummary,
    MoleculeEmbedRequest,
    MoleculeEmbedResponse,
    MoleculeResolveRequest,
    MoleculeResolveResponse,
    NodeBootstrapResponse,
    NodeListResponse,
    NodePingResponse,
    NodeStatusModel,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectModel,
    ProjectUpdateRequest,
    ReactionConfirmRequest,
    ReactionConfirmResponse,
    ReactionGetResponse,
    ReactionPreviewRequest,
    ReactionPreviewResponse,
    RemoteFileChecksumResponse,
    RemoteFileEntry,
    RemoteFileListResponse,
    RemoteFilePreviewResponse,
    RemoteLogTailResponse,
    S2CandidatesResponse,
    S2FrameModel,
    S2FrameResponse,
    S2JobReviewResponse,
    S2ProfileResponse,
    S2ReviewRequest,
    S2ReviewResponse,
    S2StructurePreviewRequest,
    S2StructurePreviewResponse,
    SRDecisionRequest,
    SRDecisionResponse,
    SRReviewListResponse,
    StageTaskListResponse,
    StageTaskModel,
    StructureAssetCreateRequest,
    StructureAssetModel,
    StructureAssetResponse,
    StructureParseRequest,
    StructureParseResponse,
    StructureSourceDetailResponse,
    StructureSourceListResponse,
    StructureSourceSummary,
    StudyPromoteResponse,
    StudyResumeResponse,
    UploadResponse,
    V1JobCreatedResponse,
    V1JobCreateRequest,
    V1JobDetailResponse,
    V1JobListResponse,
    V1JobPurgeRequest,
    V1JobPurgeResponse,
    V1JobPurgeResult,
    V1JobRecordModel,
    V1JobRerunRequest,
    V1JobSpecModel,
    V1SoftwareCandidate,
    V1SoftwareDiscoveryResponse,
    V1SoftwareEntry,
    ValidateMethodRequest,
    ValidateMethodResponse,
)
from acp.calculations.batch import normalize_tag, parse_tag_comment
from acp.chem.embedding import (
    molfile_to_xyz,
    parse_xyz_first_frame,
    smiles_to_xyz,
    xyz_formula,
)
from acp.results.manifest import MANIFEST_FILENAME, load_result_manifest
from acp.scheduler.artifacts import Artifact, ArtifactRegistry
from acp.scheduler.files import build_manifest, resolve_safe
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec, JobStatus
from acp.scheduler.logs import read_log_tail
from acp.scheduler.manager import JobManager
from acp.scheduler.naming import canonical_molecule_name, molecule_name_from_input
from acp.scheduler.nodes import ExecutionTargetError, validate_execution_request
from acp.scheduler.remote.fetcher import (
    _MAX_READ_BYTES,
    _MAX_TAIL_LINES,
    RemoteFileError,
    RemotePreviewConfig,
)
from acp.scheduler.remote.fetcher import (
    RemoteResultFetcher as _RemoteResultFetcher,
)
from acp.scheduler.remote.node_manager import NodeManager
from acp.scheduler.runner import find_workflow_state
from acp.scheduler.stage_tasks import StageTask, StageTaskStore
from acp.scheduler.store import JobStore
from acp.scheduler.structure_sources import StructureSourceService
from acp.storage.layout import runtime_file

logger = logging.getLogger(__name__)

router = APIRouter()

if TYPE_CHECKING:
    from acp.chem.composition import HessianResolution


class HessianResolver(Protocol):
    def __call__(
        self,
        *,
        explicit: object = None,
        configured: object = None,
        symbols: list[str] | tuple[str, ...] | None = None,
    ) -> HessianResolution: ...


def _manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Job scheduler not initialized")
    return manager


def _db_path(request: Request) -> Path:
    db_path = getattr(request.app.state, "db_path", None)
    if not db_path:
        db_path = _manager(request).store.db_path
    return Path(db_path)


def _stage_task_store(request: Request) -> StageTaskStore:
    return StageTaskStore(_db_path(request))


def _artifact_registry(request: Request) -> ArtifactRegistry:
    return ArtifactRegistry(_db_path(request))


def _job_store(request: Request) -> JobStore:
    return JobStore(_db_path(request))


def _record_to_v1_model(
    record: JobRecord,
    *,
    project_name: str | None = None,
    study_id: str | None = None,
    study_status: str | None = None,
) -> V1JobRecordModel:
    spec = record.spec
    return V1JobRecordModel(
        id=record.id,
        spec=V1JobSpecModel(
            workflow=spec.workflow,
            name=spec.name,
            input=spec.input,
            method=spec.method,
            resources=spec.resources,
            output_dir=spec.output_dir,
            config_path=spec.config_path,
            tags=spec.tags,
            project_id=spec.project_id,
            execution_mode=spec.execution_mode,
            target_node=spec.target_node,
            molecule_name=spec.molecule_name,
            task_name=spec.task_name,
            remark=spec.remark,
        ),
        status=record.status.value,
        work_dir=record.work_dir,
        project_id=record.project_id or spec.project_id,
        project_name=project_name,
        input_hash=record.input_hash or spec.input_hash,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        current_stage=record.current_stage,
        progress=record.progress,
        error=record.error,
        pid=record.pid,
        exit_code=record.exit_code,
        remote_job_id=record.remote_job_id,
        group_id=record.group_id or record.id,
        study_id=study_id,
        study_status=study_status,
        result=record.result,
    )


def _stage_task_to_model(task: StageTask) -> StageTaskModel:
    return StageTaskModel(
        task_id=task.task_id,
        job_id=task.job_id,
        stage_name=task.stage_name,
        task_type=task.task_type,
        state=task.state,
        exit_status=task.exit_status,
        retry_count=task.retry_count,
        pid=task.pid,
        stderr_summary=task.stderr_summary,
        started_at=task.started_at,
        completed_at=task.completed_at,
        updated_at=task.updated_at,
    )


def _artifact_to_model(artifact: Artifact) -> ArtifactModel:
    return ArtifactModel(
        artifact_id=artifact.artifact_id,
        task_id=artifact.task_id,
        job_id=artifact.job_id,
        artifact_type=artifact.artifact_type,
        file_path=artifact.file_path,
        checksum=artifact.checksum,
        size_bytes=artifact.size_bytes,
        parser_status=artifact.parser_status,
        mime_type=artifact.mime_type,
        metadata=dict(artifact.metadata or {}),
        created_at=artifact.created_at,
    )


def _project_to_model(project: dict[str, Any]) -> ProjectModel:
    return ProjectModel(
        project_id=str(project.get("project_id", "")),
        name=str(project.get("name", "")),
        description=str(project.get("description", "")),
        tags=[str(tag) for tag in project.get("tags", [])],
        run_root=str(project.get("run_root", "")),
        settings=dict(project.get("settings", {})),
        created_at=str(project.get("created_at", "")),
        updated_at=str(project.get("updated_at", "")),
    )


def _model_payload(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def _counts_for_records(records: list[JobRecord]) -> dict[str, int]:
    return dict(Counter(record.status.value for record in records))


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_file_or_none(path: Path) -> Any | None:
    if not path.exists() or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _compute_unified_status(
    study_row: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    job_status: str | None,
) -> str:
    """Project a study row + checkpoint + job status onto one chip value.

    Mapping (first match wins):

    * job ``failed`` → ``FAILED``
    * checkpoint ``status == "completed"`` or job ``completed`` → ``COMPLETED``
    * job ``waiting_review`` with any waiting ``sr_cycle_review`` decision
      in the checkpoint → ``SR_WAITING_REVIEW``
    * job ``running`` with a checkpoint → derived from ``phase_fingerprints``
      (S4 fingerprint → ``S4_RUNNING``; ``metadata.path_results`` present →
      ``S3_RUNNING``; S0/S1 fingerprints only → ``S2_RUNNING``; no
      fingerprints → ``S1_RUNNING``). events.jsonl is intentionally not
      parsed — fingerprints suffice and are cheaper.
    * row status ``draft`` → ``DRAFT``; ``reaction_confirmed`` /
      ``plan_confirmed`` → ``RESUMABLE``
    * fallback: uppercased row status.

    ``REACTION_CONFIRMATION`` is intentionally not emitted yet: the reaction
    preview flow is stateless (nothing is persisted between preview and
    confirm), so there is no transient row status to project it from.
    """
    if job_status == "failed":
        return "FAILED"
    if job_status == "completed":
        return "COMPLETED"
    if checkpoint is not None and str(checkpoint.get("status") or "") == "completed":
        return "COMPLETED"
    if job_status == "waiting_review" and checkpoint is not None:
        for entry in checkpoint.get("decision_points") or []:
            if (
                isinstance(entry, dict)
                and entry.get("status") == "waiting"
                and entry.get("type") == "sr_cycle_review"
            ):
                return "SR_WAITING_REVIEW"
    if job_status == "running" and checkpoint is not None:
        fingerprints = checkpoint.get("phase_fingerprints")
        keys = set(fingerprints) if isinstance(fingerprints, dict) else set()
        if "S4" in keys:
            return "S4_RUNNING"
        metadata = checkpoint.get("metadata")
        if isinstance(metadata, dict) and metadata.get("path_results"):
            return "S3_RUNNING"
        if keys & {"S0", "S1"}:
            return "S2_RUNNING"
        return "S1_RUNNING"
    row_status = str(study_row.get("status") or "")
    if row_status == "draft":
        return "DRAFT"
    if row_status in {"reaction_confirmed", "plan_confirmed"}:
        return "RESUMABLE"
    return row_status.upper()


def _study_unified_status(manager: JobManager | None, row: dict[str, Any]) -> str:
    """Compute ``unified_status`` for one study row; never raises."""
    job_id = row.get("job_id")
    record = manager.get(str(job_id)) if manager is not None and job_id else None
    checkpoint = _load_study_checkpoint(_study_report_dir(row, record))
    return _compute_unified_status(
        row,
        checkpoint,
        record.status.value if record is not None else None,
    )


def _study_summary_model(row: dict[str, Any], unified_status: str = "") -> MechanismStudySummary:
    study_json_raw = row.get("study_json")
    study_json = (
        json.loads(study_json_raw) if isinstance(study_json_raw, str) and study_json_raw else {}
    )
    network = study_json.get("network") if isinstance(study_json, dict) else None
    decision_points = study_json.get("decision_points") if isinstance(study_json, dict) else None
    n_states = 0
    n_edges = 0
    if isinstance(network, dict):
        nodes = network.get("nodes")
        edges = network.get("edges")
        if isinstance(nodes, list):
            n_states = len(nodes)
        if isinstance(edges, list):
            n_edges = len(edges)
    n_decisions_pending = 0
    if isinstance(decision_points, list):
        n_decisions_pending = sum(
            1
            for item in decision_points
            if isinstance(item, dict) and str(item.get("status") or "") == "waiting"
        )
    return MechanismStudySummary(
        id=str(row.get("id") or ""),
        job_id=str(row["job_id"]) if row.get("job_id") is not None else None,
        status=str(row.get("status") or "pending"),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        n_states=n_states,
        n_edges=n_edges,
        n_decisions_pending=n_decisions_pending,
        unified_status=unified_status,
    )


def _decision_point_model(row: dict[str, Any]) -> DecisionPointModel:
    payload_raw = row.get("payload")
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) and payload_raw else {}
    return DecisionPointModel(
        id=str(row.get("id") or ""),
        study_id=str(row.get("study_id") or ""),
        status=(
            "resolved"
            if str(row.get("status") or "waiting") == "resolved"
            else "superseded"
            if str(row.get("status") or "waiting") == "superseded"
            else "waiting"
        ),
        payload=payload if isinstance(payload, dict) else {},
        resolution=str(row["resolution"]) if row.get("resolution") is not None else None,
        created_at=str(row.get("created_at") or ""),
        resolved_at=str(row["resolved_at"]) if row.get("resolved_at") is not None else None,
    )


def _study_detail_model(
    store: JobStore, row: dict[str, Any], unified_status: str = ""
) -> MechanismStudyDetail:
    study_json_raw = row.get("study_json")
    study_json = (
        json.loads(study_json_raw) if isinstance(study_json_raw, str) and study_json_raw else {}
    )
    decisions = [
        _decision_point_model(item) for item in store.list_decision_points(str(row.get("id") or ""))
    ]
    return MechanismStudyDetail(
        id=str(row.get("id") or ""),
        job_id=str(row["job_id"]) if row.get("job_id") is not None else None,
        status=str(row.get("status") or "pending"),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
        study_json=study_json if isinstance(study_json, dict) else {},
        decisions=decisions,
        unified_status=unified_status,
    )


def _study_report_dir(study_row: dict[str, Any], record: JobRecord | None) -> Path | None:
    study_json_raw = study_row.get("study_json")
    study_json = (
        json.loads(study_json_raw) if isinstance(study_json_raw, str) and study_json_raw else {}
    )
    if isinstance(study_json, dict):
        study_dir = study_json.get("study_dir")
        if isinstance(study_dir, str) and study_dir:
            return Path(study_dir)
    if record is None or not record.work_dir:
        return None
    from acp.compat.legacy.layouts import find_study_layout

    layout = find_study_layout(Path(record.work_dir), str(study_row.get("id") or "") or None)
    return layout.analysis_root if layout is not None else None


def _mechanism_study_or_404(store: JobStore, study_id: str) -> dict[str, Any]:
    row = store.get_mechanism_study(study_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Mechanism study not found: {study_id}")
    return row


def _resolve_structure_asset_path(run_root: Path, source: str) -> Path:
    candidate = (run_root / source).resolve()
    try:
        candidate.relative_to(run_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Asset path escapes run_root: {source}") from exc
    if not candidate.is_file():
        raise ValueError(f"Asset file not found: {source}")
    return candidate


def _build_mechanism_report(
    study_id: str,
    job_id: str | None,
    study_dir: Path | None,
) -> MechanismStudyReportResponse:
    reaction_network = None
    mechanism_profile = None
    stationary_points = None
    quality_gates = None
    provenance = None
    if study_dir is not None:
        write_study_reports = None
        try:
            reports_module = None  # mechanism reports retired
            write_study_reports = getattr(reports_module, "write_study_reports", None)
        except ImportError:
            write_study_reports = None
        if write_study_reports is not None:
            try:
                write_study_reports(study_dir)
            except (OSError, ValueError, TypeError, RuntimeError) as exc:
                logger.warning(
                    "Failed to refresh mechanism study reports for %s: %s",
                    study_id,
                    exc,
                )
        reaction_network = _json_file_or_none(study_dir / "network.json")
        mechanism_profile = _json_file_or_none(study_dir / "mechanism_profile.json")
        stationary_points = _json_file_or_none(study_dir / "stationary_points.json")
        quality_gates = _json_file_or_none(study_dir / "quality_gates.json")
        provenance = _json_file_or_none(study_dir / "provenance.json")
        if reaction_network is None:
            reaction_network = _json_file_or_none(study_dir / "reaction_network.json")
    return MechanismStudyReportResponse(
        study_id=study_id,
        job_id=job_id,
        reaction_network=reaction_network,
        mechanism_profile=mechanism_profile,
        stationary_points=stationary_points,
        quality_gates=quality_gates,
        provenance=provenance,
    )


def _canonical_smiles(mol: Chem.Mol) -> str | None:
    try:
        return Chem.MolToSmiles(Chem.RemoveHs(Chem.Mol(mol)), canonical=True)
    except Exception:
        return None


def _formula(mol: Chem.Mol) -> str | None:
    try:
        return rdMolDescriptors.CalcMolFormula(Chem.RemoveHs(Chem.Mol(mol)))
    except Exception:
        return None


def _multiplicity(mol: Chem.Mol) -> int:
    radicals = sum(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
    return max(1, radicals + 1)


def _inchi_bundle(mol: Chem.Mol) -> tuple[str | None, str | None]:
    inchi_func = getattr(Chem, "MolToInchi", None)
    inchikey_func = getattr(Chem, "MolToInchiKey", None)
    if inchi_func is None:
        return None, None
    try:
        inchi = inchi_func(mol)
    except Exception:
        return None, None
    if not inchi:
        return None, None
    try:
        inchikey = inchikey_func(mol) if inchikey_func is not None else None
    except Exception:
        inchikey = None
    return inchi, inchikey


def _parse_resolution_input(
    req: MoleculeResolveRequest,
) -> tuple[Chem.Mol | None, str, str | None]:
    if req.smiles:
        mol = Chem.MolFromSmiles(req.smiles)
        return mol, "smiles", None if mol is not None else "Invalid SMILES"
    if req.inchi:
        inchi_func = getattr(Chem, "MolFromInchi", None)
        if inchi_func is None:
            return None, "inchi", "InChI support is unavailable"
        try:
            mol = inchi_func(req.inchi)
        except Exception as exc:
            return None, "inchi", str(exc)
        return mol, "inchi", None if mol is not None else "Invalid InChI"
    if req.molfile:
        try:
            mol = Chem.MolFromMolBlock(req.molfile, sanitize=True, removeHs=False)
        except Exception as exc:
            return None, "molfile", str(exc)
        return mol, "molfile", None if mol is not None else "Invalid molfile"
    if req.xyz:
        try:
            mol = Chem.MolFromXYZBlock(req.xyz)
            if mol is None:
                return None, "xyz", "Invalid XYZ"
            if rdDetermineBonds is None:
                return None, "xyz", "XYZ bond perception is unavailable"
            rdDetermineBonds.DetermineBonds(mol, charge=0)
            return mol, "xyz", None
        except Exception as exc:
            return None, "xyz", str(exc)
    return None, "", "No molecule input provided"


@router.get("/status", response_model=StatusResponse)
def get_status(request: Request) -> StatusResponse:
    return legacy_get_status(request)


@router.get("/backends", response_model=BackendsResponse)
def get_backends() -> BackendsResponse:
    return legacy_get_backends()


@router.get("/software/discovery", response_model=V1SoftwareDiscoveryResponse)
def get_software_discovery() -> V1SoftwareDiscoveryResponse:
    """Enumerate every discovered QC install with versions and source labels.

    Informational multi-install awareness: ``resolved``/``source`` reflect
    the unchanged first-hit-wins resolution semantics; ``candidates``
    lists every visible install (config, env, PATH order, fallback, then
    filesystem-scan hits).  Versions come from the shared TTL cache.
    """
    from cccp.software import discover_all_detailed, version_cached

    try:
        from cccp.config import load_config

        config = load_config()
    except Exception as exc:
        logger.warning("software discovery: config load failed: %s", exc)
        config = None

    entries: list[V1SoftwareEntry] = []
    for name, discovery in discover_all_detailed(config=config).items():
        candidates = [
            V1SoftwareCandidate(
                path=str(candidate.path),
                version=version_cached(name, candidate.path),
                source=candidate.source,
            )
            for candidate in discovery.candidates
        ]
        entries.append(
            V1SoftwareEntry(
                name=name,
                resolved=str(discovery.resolved) if discovery.resolved else None,
                version=version_cached(name, discovery.resolved),
                source=discovery.source,
                multiple=len(candidates) > 1,
                candidates=candidates,
            )
        )
    return V1SoftwareDiscoveryResponse(software=entries)


@router.get("/workflows", response_model=WorkflowsResponse)
def get_workflows() -> WorkflowsResponse:
    return legacy_get_workflows()


@router.get("/protocols", response_model=ProtocolsResponse)
def get_protocols() -> ProtocolsResponse:
    return legacy_get_protocols()


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(request: Request) -> ProjectListResponse:
    manager = _manager(request)
    return ProjectListResponse(
        projects=[_project_to_model(project) for project in manager.projects.list_projects()]
    )


@router.post("/projects", response_model=ProjectModel, status_code=201)
def create_project(req: ProjectCreateRequest, request: Request) -> ProjectModel:
    manager = _manager(request)
    try:
        project = manager.projects.create_project(
            req.name,
            description=req.description,
            tags=req.tags,
            settings=req.settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_to_model(project)


@router.get("/projects/{project_id}", response_model=ProjectModel)
def get_project(project_id: str, request: Request) -> ProjectModel:
    manager = _manager(request)
    project = manager.projects.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return _project_to_model(project)


@router.patch("/projects/{project_id}", response_model=ProjectModel)
def update_project(
    project_id: str,
    req: ProjectUpdateRequest,
    request: Request,
) -> ProjectModel:
    manager = _manager(request)
    try:
        project = manager.projects.update_project(project_id, **_model_payload(req))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return _project_to_model(project)


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    request: Request,
    delete_data: bool = Query(default=False),
) -> JSONResponse:
    manager = _manager(request)
    try:
        deleted = manager.delete_project(project_id, delete_data=delete_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return JSONResponse({"deleted": True, "project_id": project_id})


@router.get("/projects/{project_id}/jobs", response_model=V1JobListResponse)
def list_project_jobs(
    project_id: str,
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> V1JobListResponse:
    manager = _manager(request)
    project = manager.projects.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    records = manager.list_jobs_by_project(project_id, limit=limit)
    if status is not None:
        records = [record for record in records if record.status.value == status]
    return V1JobListResponse(
        jobs=[_record_to_v1_model(record) for record in records],
        counts=_counts_for_records(records),
    )


def _job_input_source_stem(inp: dict[str, Any]) -> str:
    """Derive a molecule-name component from the job input source.

    Structured sources are inspected field-by-field.  They must never be
    converted with ``str(dict)`` because that would put source metadata and
    Job IDs into the physical task name.
    """
    return molecule_name_from_input(inp)


def _source_job_molecule_name(inp: dict[str, Any], manager: Any) -> str:
    """Read only the canonical molecule name from an explicitly referenced job."""
    source = inp.get("source")
    source_job_id = inp.get("source_job_id")
    if isinstance(source, dict):
        source_job_id = source_job_id or source.get("source_job_id")
    source_job_id = str(source_job_id or "").strip()
    if not source_job_id:
        return ""
    source_record = manager.get(source_job_id)
    if source_record is None:
        return ""
    name = canonical_molecule_name(getattr(source_record.spec, "molecule_name", ""))
    if name:
        return name
    # Legacy source jobs may not have the v2 field yet.  Use only their input
    # source as a fallback; never use spec.name or the Job ID.
    return _job_input_source_stem(getattr(source_record.spec, "input", {}))


def _resolve_job_molecule_name(
    requested: str,
    inp: dict[str, Any],
    manager: Any,
) -> str:
    """Resolve the one molecule identity allowed to cross a task boundary."""
    source_name = _source_job_molecule_name(inp, manager)
    source = inp.get("source")
    has_source_job = bool(str(inp.get("source_job_id") or "").strip()) or (
        isinstance(source, dict) and bool(str(source.get("source_job_id") or "").strip())
    )
    if source_name and has_source_job:
        # A task-artifact import inherits exactly one field: molecule_name.
        # Task name, remark, Job ID, work_dir and old input metadata stay out.
        return source_name
    name = canonical_molecule_name(requested)
    if name:
        return name
    name = _job_input_source_stem(inp)
    if name:
        return name
    return "mol"


def _resolve_stage_artifact_ref(
    workflow: str,
    inp: dict[str, Any],
    manager: Any,
) -> dict[str, Any]:
    """Resolve a stage workflow's artifact reference path."""
    source_job_id = str(inp.get("source_job_id") or "").strip()
    relative_path = str(inp.get("from_artifact") or inp.get("relative_path") or "").strip()
    if not source_job_id or not relative_path:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{workflow} requires input.source_job_id + input.from_artifact "
                "(or a pre-resolved input.from path)"
            ),
        )
    source_job = manager.get(source_job_id)
    source_work_dir = Path(source_job.work_dir) if source_job and source_job.work_dir else None
    if source_work_dir is None or not source_work_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Source job not found or has no work dir: {source_job_id}",
        )
    manifest_path = (source_work_dir / relative_path).resolve()
    if not manifest_path.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"Artifact not found: {relative_path} (from job {source_job_id})",
        )
    resolved = dict(inp)
    resolved["from"] = str(manifest_path)
    resolved["source_job_work_dir"] = str(source_work_dir)
    return resolved


def _prepare_bond_scan_input(
    inp: dict[str, Any],
    manager: Any,
) -> dict[str, Any]:
    """Validate + pin a PESsearch ``mode=bond_length_scan`` job input (§7/§11).

    Stores the full scan request under ``input["scan_request"]`` so the
    scheduler runner can forward it verbatim; resolves/pins a task-artifact
    source manifest into ``input["from"]`` (and the source's artifact_path)
    so handoff copying works for both local and remote execution.
    """

    try:
        _validated = BondLengthScanJobInput.model_validate(inp)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False, include_url=False),
        ) from exc

    src = dict(inp.get("source") or {})
    if src.get("source_type") == "task_artifact":
        if not str(src.get("source_job_id") or "").strip():
            raise HTTPException(
                status_code=422,
                detail="task_artifact source requires source.source_job_id",
            )
        artifact_path = str(src.get("artifact_path") or "").strip()
        if not artifact_path:
            raise HTTPException(
                status_code=422,
                detail="task_artifact source requires source.artifact_path "
                "(or use source_job_id + from_artifact)",
            )
        resolved = _resolve_stage_artifact_ref(
            "PESsearch",
            {
                "source_job_id": src.get("source_job_id"),
                "from_artifact": artifact_path,
            },
            manager,
        )
        pinned = str(resolved.get("from"))
        src["artifact_path"] = pinned
        inp["from"] = pinned
    elif src.get("source_type") == "structure_asset":
        asset_path = str(src.get("asset_path") or "").strip()
        if not asset_path:
            raise HTTPException(
                status_code=422,
                detail="structure_asset source requires source.asset_path",
            )
        run_root = manager.run_root
        try:
            resolved_asset = _resolve_structure_asset_path(run_root, asset_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        src["asset_path"] = str(resolved_asset)
    elif src.get("source_type") == "xyz_text" and not str(src.get("xyz_text") or "").strip():
        raise HTTPException(status_code=422, detail="xyz_text source requires source.xyz_text")

    coordinate = inp.get("coordinate")
    protocol = inp.get("protocol")
    if not isinstance(coordinate, dict) or not coordinate.get("atoms"):
        raise HTTPException(
            status_code=422, detail="bond_length_scan requires coordinate with atoms"
        )
    if not isinstance(protocol, dict):
        protocol = {}

    prepared = dict(inp)
    prepared["source"] = src
    prepared["scan_request"] = {
        "mode": "bond_length_scan",
        "source": src,
        "coordinate": coordinate,
        **({"coordinates": coordinates} if coordinates is not None else {}),
        **({"selection": selection} if isinstance(selection, dict) else {}),
        "protocol": protocol,
    }
    return prepared


def _resolve_batch_structures_input(inp: dict[str, Any], request: Request) -> dict[str, Any]:
    """Inline ``source_id`` references in a Workbench ``batch_structures`` payload.

    Items may reference reusable structures via ``source_id``
    (``job_<id>:<rel_path>``).  The runner materializer only understands
    inline XYZ, so references are expanded here while the API still has
    store + remote-fetcher access (works for local and remote source jobs).
    """
    items = inp.get("items")
    if not isinstance(items, list) or not items:
        return inp
    if not any(isinstance(item, dict) and item.get("source_id") for item in items):
        return inp
    service = _structure_source_service(request)
    resolved_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            resolved_items.append(item)
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            resolved_items.append(item)
            continue
        try:
            asset, _ = service.get(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        resolved = {key: value for key, value in item.items() if key != "source_id"}
        resolved["xyz"] = str(asset.get("xyz") or "")
        resolved_items.append(resolved)
    resolved_inp = dict(inp)
    resolved_inp["items"] = resolved_items
    return resolved_inp


@router.post("/jobs", response_model=V1JobCreatedResponse, status_code=201)
def create_job(req: V1JobCreateRequest, request: Request) -> V1JobCreatedResponse:
    manager = _manager(request)
    if req.workflow not in SUPPORTED_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(SUPPORTED_WORKFLOWS)}",
        )
    if req.workflow == "PESsearch" and str(req.method.get("mode") or "") == "bond_length_scan":
        req.input = _prepare_bond_scan_input(req.input, manager)
    elif req.workflow == "PESsearch":
        req.input = _resolve_stage_artifact_ref(req.workflow, req.input, manager)
    elif req.workflow == "BatchOptimize":
        req.input = _resolve_batch_structures_input(req.input, request)
    task_name = req.task_name or req.workflow
    molecule_name = _resolve_job_molecule_name(req.molecule_name, req.input, manager)
    spec = JobSpec(
        workflow=req.workflow,
        name=req.name,
        input=req.input,
        method=req.method,
        resources=req.resources,
        output_dir=req.output_dir,
        config_path=req.config_path,
        tags=req.tags,
        project_id=req.project_id,
        execution_mode=req.execution_mode,
        target_node=req.target_node,
        molecule_name=molecule_name,
        task_name=task_name,
        remark=req.remark,
    )
    try:
        validate_execution_request(spec)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        record = manager.submit(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return V1JobCreatedResponse(
        job_id=record.id,
        status=record.status.value,
        workflow=record.spec.workflow,
        project_id=record.project_id,
    )


@router.get("/jobs", response_model=V1JobListResponse)
def list_jobs(
    request: Request,
    status: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> V1JobListResponse:
    store = _job_store(request)
    if project_id:
        enriched = store.list_enriched(limit=limit, project_id=project_id)
        if status is not None:
            enriched = [item for item in enriched if item["record"].status.value == status]
    else:
        enriched = store.list_enriched(status=status, limit=limit)
    jobs = [
        _record_to_v1_model(
            item["record"],
            project_name=item["project_name"],
            study_id=item["study_id"],
            study_status=item["study_status"],
        )
        for item in enriched
    ]
    return V1JobListResponse(
        jobs=jobs,
        counts=_counts_for_records([item["record"] for item in enriched]),
    )


@router.get("/mechanism-studies", response_model=list[MechanismStudySummary])
def list_mechanism_studies(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    job_id: str | None = Query(default=None),
) -> list[MechanismStudySummary]:
    store = _job_store(request)
    manager = _manager(request)
    return [
        _study_summary_model(row, unified_status=_study_unified_status(manager, row))
        for row in store.list_mechanism_studies(limit=limit, job_id=job_id)
    ]


@router.post("/mechanism-studies", response_model=MechanismStudyDetail, status_code=201)
def create_mechanism_study(
    req: MechanismStudyCreateRequest,
    request: Request,
) -> MechanismStudyDetail:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get("/mechanism-studies/{study_id}", response_model=MechanismStudyDetail)
def get_mechanism_study(study_id: str, request: Request) -> MechanismStudyDetail:
    store = _job_store(request)
    manager = _manager(request)
    row = _mechanism_study_or_404(store, study_id)
    return _study_detail_model(store, row, unified_status=_study_unified_status(manager, row))


@router.post(
    "/mechanism-studies/{study_id}/reaction/preview",
    response_model=ReactionPreviewResponse,
)
def preview_mechanism_reaction(
    study_id: str,
    req: ReactionPreviewRequest,
    request: Request,
) -> ReactionPreviewResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.post(
    "/mechanism-studies/{study_id}/reaction/confirm",
    response_model=ReactionConfirmResponse,
)
def confirm_mechanism_reaction(
    study_id: str,
    req: ReactionConfirmRequest,
    request: Request,
) -> ReactionConfirmResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get(
    "/mechanism-studies/{study_id}/reaction",
    response_model=ReactionGetResponse,
)
def get_mechanism_reaction(study_id: str, request: Request) -> ReactionGetResponse:
    store = _job_store(request)
    row = _mechanism_study_or_404(store, study_id)
    reaction_json_raw = row.get("reaction_json")
    reaction = None
    if isinstance(reaction_json_raw, str) and reaction_json_raw:
        parsed = json.loads(reaction_json_raw)
        reaction = parsed if isinstance(parsed, dict) else None
    return ReactionGetResponse(reaction=reaction, status=str(row.get("status") or ""))


@router.post(
    "/mechanism-studies/{study_id}/mechanism/plan",
    response_model=MechanismPlanResponse,
)
def confirm_mechanism_plan(
    study_id: str,
    req: MechanismPlanRequest,
    request: Request,
) -> MechanismPlanResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get(
    "/mechanism-studies/{study_id}/report",
    response_model=MechanismStudyReportResponse,
)
def get_mechanism_study_report(
    study_id: str,
    request: Request,
) -> MechanismStudyReportResponse:
    manager = _manager(request)
    store = _job_store(request)
    row = _mechanism_study_or_404(store, study_id)
    job_id = str(row["job_id"]) if row.get("job_id") is not None else None
    record = manager.get(job_id) if job_id is not None else None
    study_dir = _study_report_dir(row, record)
    return _build_mechanism_report(study_id, job_id, study_dir)


@router.post(
    "/mechanism-studies/{study_id}/decisions/{decision_id}",
    response_model=DecisionResolveResponse,
)
def resolve_mechanism_decision(
    study_id: str,
    decision_id: str,
    req: DecisionResolveRequest,
    request: Request,
) -> DecisionResolveResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


def _load_study_checkpoint(study_dir: Path | None) -> dict[str, Any] | None:
    if study_dir is None:
        return None
    path = study_dir / "study.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@router.get("/mechanism-studies/{study_id}/reviews", response_model=SRReviewListResponse)
def list_mechanism_reviews(study_id: str, request: Request) -> SRReviewListResponse:
    store = _job_store(request)
    manager = _manager(request)
    study_row = _mechanism_study_or_404(store, study_id)
    job_id = study_row.get("job_id")
    record = manager.get(str(job_id)) if job_id else None
    checkpoint = _load_study_checkpoint(_study_report_dir(study_row, record))
    if checkpoint is None:
        return SRReviewListResponse(reviews=[])
    pending_contexts = (checkpoint.get("metadata") or {}).get("pending_decisions") or {}
    reviews: list[dict[str, Any]] = []
    for entry in checkpoint.get("decision_points") or []:
        if not isinstance(entry, dict) or entry.get("status") != "waiting":
            continue
        payload = entry.get("payload") or {}
        context = pending_contexts.get(entry.get("id")) or {}
        reviews.append(
            {
                "review_id": entry.get("id"),
                "type": entry.get("type"),
                "status": entry.get("status"),
                "cycle": payload.get("cycle", context.get("cycle", 0)),
                "source_state_id": payload.get("source_state_id") or context.get("source_state_id"),
                "route_id": payload.get("route_id") or context.get("route_id"),
                "options": entry.get("options") or [],
                "created_at": entry.get("created_at"),
                "summary": context.get("review") or {},
            }
        )
    return SRReviewListResponse(reviews=reviews)


@router.post(
    "/mechanism-studies/{study_id}/reviews/{review_id}/decision",
    response_model=SRDecisionResponse,
)
def submit_mechanism_review_decision(
    study_id: str,
    review_id: str,
    req: SRDecisionRequest,
    request: Request,
) -> SRDecisionResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.post("/mechanism-studies/{study_id}/resume", response_model=StudyResumeResponse)
def resume_mechanism_study_job(study_id: str, request: Request) -> StudyResumeResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.post(
    "/mechanism-studies/{study_id}/promote",
    response_model=StudyPromoteResponse,
)
def promote_mechanism_study(study_id: str, request: Request) -> StudyPromoteResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


# ---------------------------------------------------------------------------
# S2 bond-length scan (§11): structure assets, profile, candidates, frames,
# review persistence and candidate-result handoff.
# ---------------------------------------------------------------------------


def _s2_manifest_for_job(manager: Any, job_id: str) -> tuple[Path, dict[str, Any]]:
    """Locate + read the s2_path manifest for a PESsearch job."""
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    work_dir = Path(record.work_dir) if record.work_dir else None
    if work_dir is None or not work_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")
    manifest_path = work_dir / "RESULT" / "mechanism" / "s2_path_manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail=f"No s2_path_manifest.json for job {job_id}")
    from acp.compat.legacy.manifests import read_s2_path_manifest

    try:
        payload = read_s2_path_manifest(manifest_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return manifest_path, payload


@router.get("/jobs/{job_id}/energy-graph", response_model=EnergyGraphResponse)
def get_energy_graph(
    job_id: str,
    request: Request,
    view_type: str = Query(default="auto"),
) -> EnergyGraphResponse:
    """Return the normalized energy-workspace projection for a job.

    The endpoint intentionally keeps workflow-specific manifest parsing on the
    server.  The frontend receives only axes, series, nodes, annotations, and
    geometry references, so S2 review data and future optimization/MD data can
    use the same energy workspace.
    """
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    from acp.results.energy_graph import build_energy_graph_from_job

    workflow = str(record.spec.workflow or "")
    method = dict(record.spec.method or {})
    work_dir = Path(record.work_dir) if record.work_dir else Path(".")
    s2_payload: dict[str, Any] | None = None
    mechanism_report: dict[str, Any] | None = None
    s2_candidates: list[dict[str, Any]] | None = None
    s2_review_state: dict[str, Any] | None = None

    if workflow == "PESsearch" and str(method.get("mode") or "") == "bond_length_scan":
        from acp.compat.legacy.manifests import read_s2_candidate_manifest, read_s2_review

        _manifest_path, s2_payload = _s2_manifest_for_job(manager, job_id)
        if s2_payload is not None:
            saved_review = read_s2_review(_manifest_path)
            candidate_manifest = read_s2_candidate_manifest(_manifest_path)
            s2_candidates = (
                candidate_manifest.get("candidates")
                if isinstance(candidate_manifest, dict)
                else None
            )
            s2_review_state = saved_review if isinstance(saved_review, dict) else None
    elif workflow == "mechanism":
        store = _job_store(request)
        rows = store.list_mechanism_studies(limit=1, job_id=job_id)
        if rows:
            row = rows[0]
            study_id = str(row.get("id") or row.get("study_id") or "")
            study_dir = _study_report_dir(row, record)
            report = _build_mechanism_report(study_id, job_id, study_dir)
            mechanism_report = report.model_dump()

    graph = build_energy_graph_from_job(
        job_id,
        workflow=workflow,
        method=method,
        work_dir=work_dir,
        s2_payload=s2_payload,
        mechanism_report=mechanism_report,
        s2_candidates=s2_candidates,
        s2_review_state=s2_review_state,
    )
    if view_type not in {"", "auto", str(graph.get("view_type") or "")}:
        raise HTTPException(
            status_code=404,
            detail=f"Energy graph view is not available: {view_type}",
        )
    return EnergyGraphResponse.model_validate(graph)


@router.post("/structure-assets", response_model=StructureAssetResponse, status_code=201)
def create_structure_asset(
    req: StructureAssetCreateRequest,
    request: Request,
) -> StructureAssetResponse:
    """Register a pasted XYZ structure as a reusable ACP structure asset."""
    from acp.intake.parsers import parse_xyz_text

    manager = _manager(request)
    parsed = parse_xyz_text(req.xyz_text)
    if not parsed.structures and parsed.errors:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse XYZ: {'; '.join(parsed.errors[:3])}",
        )
    if not parsed.structures:
        raise HTTPException(status_code=422, detail="XYZ text contains no structures")
    asset = parsed.structures[0]
    project_id = req.project_id or "default"
    uploads = manager.run_root / project_id / "_uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    from acp.intake.storage import UploadStorage

    storage = UploadStorage(manager.run_root)
    _asset_id, _ = storage.save_upload(
        project_id, f"{req.name or 'paste'}.xyz", req.xyz_text.encode()
    )
    normalized = storage.save_normalized(
        project_id,
        _asset_id,
        f"{req.name or 'paste'}",
        str(asset.xyz),
    )
    rel_path = normalized.relative_to(manager.run_root).as_posix()
    charge = req.charge if req.charge != 0 else int(asset.charge or 0)
    multiplicity = req.multiplicity if req.multiplicity != 1 else int(asset.multiplicity or 1)
    return StructureAssetResponse(
        asset_id=_asset_id,
        name=req.name or "paste",
        atom_count=int(asset.atom_count or 0),
        formula=str(asset.formula or ""),
        charge=charge,
        multiplicity=multiplicity,
        xyz=str(asset.xyz),
        asset_path=rel_path,
        ok=True,
    )


@router.post("/s2/structure-preview", response_model=S2StructurePreviewResponse)
def preview_s2_structure(
    req: S2StructurePreviewRequest,
    request: Request,
) -> S2StructurePreviewResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get("/jobs/{job_id}/s2/profile", response_model=S2ProfileResponse)
def get_s2_profile(job_id: str, request: Request) -> S2ProfileResponse:
    manager = _manager(request)
    _manifest_path, payload = _s2_manifest_for_job(manager, job_id)
    scan = payload.get("scan") or {}
    frames = [S2FrameModel(**frame) for frame in scan.get("frames") or []]
    return S2ProfileResponse(
        job_id=job_id,
        mode=str(payload.get("mode") or ""),
        status=str(payload.get("status") or ""),
        stationary_point_claimed=bool(payload.get("stationary_point_claimed")),
        scan={key: value for key, value in scan.items() if key != "frames"},
        energy_profile=dict(payload.get("energy_profile") or {}),
        frames=frames,
    )


@router.get("/jobs/{job_id}/s2/candidates", response_model=S2CandidatesResponse)
def get_s2_candidates(job_id: str, request: Request) -> S2CandidatesResponse:
    manager = _manager(request)
    _manifest_path, payload = _s2_manifest_for_job(manager, job_id)
    return S2CandidatesResponse(
        job_id=job_id,
        mode=str(payload.get("mode") or ""),
        status=str(payload.get("status") or ""),
        stationary_point_claimed=bool(payload.get("stationary_point_claimed")),
        recommendations=dict(payload.get("recommendations") or {}),
        review=dict(payload.get("review") or {}),
    )


@router.get("/jobs/{job_id}/s2/frame/{frame_index}", response_model=S2FrameResponse)
def get_s2_frame(job_id: str, frame_index: int, request: Request) -> S2FrameResponse:
    manager = _manager(request)
    manifest_path, payload = _s2_manifest_for_job(manager, job_id)
    frames = (payload.get("scan") or {}).get("frames") or []
    frame = next((f for f in frames if int(f.get("index") or -1) == frame_index), None)
    if frame is None:
        raise HTTPException(
            status_code=404, detail=f"Frame {frame_index} not found in job {job_id}"
        )
    work_dir = Path(str(manifest_path))
    work_dir = work_dir.parent.parent.parent
    scan_dir = str((payload.get("scan") or {}).get("scan_dir") or "")
    xyz_path = work_dir / scan_dir / str(frame.get("geometry_path") or "")
    xyz = ""
    if xyz_path.is_file():
        xyz = xyz_path.read_text(encoding="utf-8")
    return S2FrameResponse(
        job_id=job_id,
        frame_index=frame_index,
        target_coordinate=float(frame.get("target_coordinate") or 0.0),
        actual_coordinate=float(frame.get("actual_coordinate") or 0.0),
        xyz=xyz,
        scan_energy_hartree=frame.get("scan_energy_hartree"),
        single_point_energy_hartree=frame.get("single_point_energy_hartree"),
        optimization_converged=bool(frame.get("optimization_converged")),
        single_point_status=str(frame.get("single_point_status") or ""),
    )


@router.post(
    "/mechanism-projects/{project_id}/s2/review",
    response_model=S2ReviewResponse,
)
def save_s2_review(
    project_id: str,
    req: S2ReviewRequest,
    request: Request,
) -> S2ReviewResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.post(
    "/jobs/{job_id}/s2/review",
    response_model=S2JobReviewResponse,
)
def save_job_s2_review(
    job_id: str,
    req: S2ReviewRequest,
    request: Request,
) -> S2JobReviewResponse:
    raise HTTPException(status_code=410, detail="该机制研究端点已退役，历史只读")


@router.get("/jobs/{job_id}", response_model=V1JobRecordModel)
def get_job(job_id: str, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


# ---------------------------------------------------------------------- #
# Rich job detail (plan §4.5) — GET /jobs/{id}/detail.
# Response field names are the frozen frontend contract; do not rename.
# ---------------------------------------------------------------------- #

_DETAIL_STDERR_TAIL_LINES = 40
_DETAIL_DISK_SCAN_CAP = 2000
_MECHANISM_PHASES = ("S0", "S1", "S2", "S3", "SR", "S4")
_CONTINUE_WORKFLOWS = ("mechanism", "xtbmd_censo_energy")


def _backfill_result_from_disk(record: JobRecord) -> dict[str, Any] | None:
    """Display-only result backfill for jobs whose ``result_json`` is null.

    Mirrors ``JobManager._collect_result`` (state.json via
    ``find_workflow_state`` + mechanism study.json metadata) without
    persisting anything.
    """
    result: dict[str, Any] = {}
    if record.work_dir:
        try:
            state_path = find_workflow_state(Path(record.work_dir))
        except OSError:
            state_path = None
        if state_path is not None:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict):
                result["state"] = payload
    return result or None


def _mechanism_stage_fallback(record: JobRecord) -> list[JobStageEntry]:
    """Best-effort stage entries from study.json phase fingerprints."""
    if not record.work_dir:
        return []
    try:
        from acp.compat.legacy.layouts import find_study_layout

        layout = find_study_layout(Path(record.work_dir))
        candidates = (
            [layout.study_json] if layout is not None and layout.study_json.is_file() else []
        )
    except OSError:
        candidates = []
    checkpoint: dict[str, Any] | None = None
    for path in candidates:
        checkpoint = _load_study_checkpoint(path.parent)
        if checkpoint is not None:
            break
    if checkpoint is None:
        return []
    fingerprints = checkpoint.get("phase_fingerprints")
    fingerprints = fingerprints if isinstance(fingerprints, dict) else {}
    entries: list[JobStageEntry] = []
    gap_seen = False
    for phase in _MECHANISM_PHASES:
        if phase in fingerprints:
            entries.append(JobStageEntry(stage_name=phase, status="completed"))
            continue
        if not gap_seen:
            gap_seen = True
            entries.append(
                JobStageEntry(
                    stage_name=phase,
                    status=_mechanism_gap_status(record),
                    error=record.error if record.status == JobStatus.FAILED else None,
                )
            )
        else:
            entries.append(JobStageEntry(stage_name=phase))
    return entries


def _mechanism_gap_status(record: JobRecord) -> str:
    if record.status == JobStatus.FAILED:
        return "failed"
    if record.status in (
        JobStatus.RUNNING,
        JobStatus.STARTING,
        JobStatus.PAUSED,
        JobStatus.WAITING_REVIEW,
    ):
        return "running"
    if record.status == JobStatus.COMPLETED:
        return "skipped"
    return "pending"


def _detail_stages(job_id: str, record: JobRecord, request: Request) -> list[JobStageEntry]:
    tasks = _stage_task_store(request).list_by_job(job_id)
    state_stages: dict[str, dict[str, Any]] = {}
    if record.work_dir:
        try:
            state_path = find_workflow_state(Path(record.work_dir))
        except OSError:
            state_path = None
        if state_path is not None:
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                raw = state_data.get("stages") if isinstance(state_data, dict) else None
                if isinstance(raw, dict):
                    state_stages = raw
            except (OSError, json.JSONDecodeError):
                pass
    if tasks:
        entries = _overlay_state_on_entries(tasks, state_stages)
        if entries:
            _apply_terminal_projection(entries, record.status)
            return entries
    if state_stages:
        entries = _synthesize_entries_from_state(state_stages)
        _apply_terminal_projection(entries, record.status)
        return entries
    if record.spec.workflow == "mechanism":
        return _mechanism_stage_fallback(record)
    return []


_STAGE_ADVANCE_ORDER: dict[str, int] = {
    "pending": 0,
    "running": 1,
    "completed": 2,
    "failed": 2,
    "skipped": 2,
    "cancelled": 2,
}

_TERMINAL_STAGE_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})


def _apply_terminal_projection(entries: list[JobStageEntry], record_status: JobStatus) -> None:
    if not record_status.is_terminal:
        return
    for entry in entries:
        if entry.status not in _TERMINAL_STAGE_STATUSES:
            entry.status = "skipped"


def _overlay_state_on_entries(
    tasks: list[StageTask],
    state_stages: dict[str, dict[str, Any]],
) -> list[JobStageEntry]:
    """Build stage entries from stage_tasks rows, overlaying state.json data."""
    entries: list[JobStageEntry] = []
    for task in tasks:
        name = task.stage_name
        status = task.state
        progress: float | None = None
        detail: str | None = task.status_detail
        state_info = state_stages.get(name)
        if isinstance(state_info, dict):
            state_status = str(state_info.get("status") or "")
            state_order = _STAGE_ADVANCE_ORDER.get(state_status, 0)
            db_order = _STAGE_ADVANCE_ORDER.get(status, 0)
            if state_order > db_order:
                status = state_status
            state_progress = state_info.get("progress")
            if isinstance(state_progress, (int, float)):
                progress = float(state_progress)
            state_detail = state_info.get("detail")
            if isinstance(state_detail, str) and state_detail:
                detail = state_detail
        entries.append(
            JobStageEntry(
                stage_name=name,
                status=status,
                started_at=task.started_at,
                completed_at=task.completed_at,
                error=task.stderr_summary,
                retry_count=task.retry_count,
                status_detail=detail,
                label=stage_label(name),
                progress=progress,
                detail=detail,
            )
        )
    return entries


def _synthesize_entries_from_state(
    state_stages: dict[str, dict[str, Any]],
) -> list[JobStageEntry]:
    """Build stage entries purely from state.json when stage_tasks has no rows."""
    entries: list[JobStageEntry] = []
    for name, info in state_stages.items():
        if not isinstance(info, dict):
            continue
        status = str(info.get("status") or "pending")
        progress_raw = info.get("progress")
        progress = float(progress_raw) if isinstance(progress_raw, (int, float)) else None
        detail_raw = info.get("detail")
        detail = str(detail_raw) if isinstance(detail_raw, str) and detail_raw else None
        entries.append(
            JobStageEntry(
                stage_name=name,
                status=status,
                label=stage_label(name),
                progress=progress,
                detail=detail,
            )
        )
    return entries


def _detail_artifacts_summary(record: JobRecord, request: Request) -> list[JobArtifactSummaryEntry]:
    result = record.result or {}
    is_remote = bool(
        record.remote_job_id or result.get("lsf_job_id") or result.get("execution_kind") == "remote"
    )
    work_dir = Path(record.work_dir) if record.work_dir else None
    entries: list[JobArtifactSummaryEntry] = []
    for artifact in _artifact_registry(request).list_by_job(record.id):
        size: int | None = None
        if not is_remote and work_dir is not None:
            resolved = resolve_safe(work_dir, artifact.file_path)
            if resolved is not None:
                try:
                    size = resolved.stat().st_size
                except OSError:
                    size = None
        if size is None and artifact.size_bytes:
            size = artifact.size_bytes
        entries.append(
            JobArtifactSummaryEntry(
                type=artifact.artifact_type,
                path=artifact.file_path,
                size=size,
            )
        )
    return entries


def _detail_error_detail(record: JobRecord, stages: list[JobStageEntry]) -> JobErrorDetail | None:
    if record.status != JobStatus.FAILED and not record.error:
        return None
    stderr_lines: list[str] = []
    if record.work_dir:
        stderr_lines = read_log_tail(
            runtime_file(record.work_dir, "stderr.log"), lines=_DETAIL_STDERR_TAIL_LINES
        )
    failed_stage = next(
        (entry.stage_name for entry in stages if entry.status == "failed"),
        None,
    )
    return JobErrorDetail(
        error=record.error,
        stderr_tail="\n".join(stderr_lines),
        failed_stage=failed_stage,
    )


def _detail_disk_size(work_dir: Path) -> int:
    total = 0
    scanned = 0
    try:
        for item in work_dir.rglob("*"):
            scanned += 1
            if scanned > _DETAIL_DISK_SCAN_CAP:
                break
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _probe_disk_state(record: JobRecord) -> JobDiskState:
    state = JobDiskState()
    if not record.work_dir:
        return state
    work_dir = Path(record.work_dir)
    try:
        if not work_dir.is_dir():
            return state
    except OSError:
        return state
    state.work_dir_exists = True
    try:
        state.has_state_json = find_workflow_state(work_dir) is not None
    except OSError:
        state.has_state_json = False
    if record.spec.workflow == "mechanism":
        try:
            from acp.compat.legacy.layouts import find_study_layout

            layout = find_study_layout(work_dir)
            state.has_study_checkpoint = layout is not None and layout.study_json.is_file()
        except OSError:
            state.has_study_checkpoint = False
    has_payload = isinstance((record.result or {}).get("review_payload"), dict)
    if not has_payload:
        try:
            has_payload = (work_dir / "review_payload.json").is_file()
        except OSError:
            has_payload = False
    state.has_review_payload = has_payload
    state.size_bytes = _detail_disk_size(work_dir)
    return state


def _compute_recovery(record: JobRecord, disk_state: JobDiskState) -> JobRecovery:
    """Pure §4.4 recovery-matrix projection (no I/O)."""
    status = record.status
    workflow = record.spec.workflow
    can_continue = (
        status in (JobStatus.FAILED, JobStatus.CANCELLED)
        and workflow in _CONTINUE_WORKFLOWS
        and disk_state.work_dir_exists
    )
    if can_continue and workflow == "mechanism":
        notes = "mechanism study 将从断点相位继续(相位指纹保护已完成部分)"
    elif can_continue:
        notes = "跳过指纹一致的已完成阶段(xtbmd/batch_opt/isostat),CENSO/DFT 重算"
    elif status.is_terminal:
        notes = "该工作流不支持断点续算，请使用重算"
    else:
        notes = ""
    return JobRecovery(
        can_pause=status == JobStatus.RUNNING,
        can_unpause=status == JobStatus.PAUSED,
        can_continue=can_continue,
        continue_mode="checkpoint" if can_continue else "",
        continue_notes=notes,
        can_rerun=status.is_terminal,
        can_purge=True,
        can_cancel=status.is_active,
    )


@router.get("/jobs/{job_id}/detail", response_model=V1JobDetailResponse)
def get_job_detail(job_id: str, request: Request) -> V1JobDetailResponse:
    """Rich read-only job projection: DB + disk + recovery matrix (§4.5)."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    job_model = _record_to_v1_model(record)
    if record.result is None:
        backfilled = _backfill_result_from_disk(record)
        if backfilled is not None:
            job_model = job_model.model_copy(update={"result": backfilled})
    stages = _detail_stages(job_id, record, request)
    disk_state = _probe_disk_state(record)
    return V1JobDetailResponse(
        job=job_model,
        stages=stages,
        artifacts_summary=_detail_artifacts_summary(record, request),
        error_detail=_detail_error_detail(record, stages),
        disk_state=disk_state,
        recovery=_compute_recovery(record, disk_state),
        metrics=_read_job_metrics(record),
    )


def _read_job_metrics(record: JobRecord) -> JobMetrics | None:
    """Read the display-only ``metrics.json`` sidecar, if present."""
    if not record.work_dir:
        return None
    metrics_path = Path(record.work_dir) / "metrics.json"
    try:
        if not metrics_path.exists():
            return None
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return JobMetrics(
        engine=payload.get("engine"),
        last_energy_hartree=payload.get("last_energy_hartree"),
        opt_converged=payload.get("opt_converged"),
        updated_at=payload.get("updated_at"),
    )


@router.post("/jobs/{job_id}/cancel", response_model=V1JobRecordModel)
def cancel_job(job_id: str, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    record = manager.cancel(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


@router.post("/jobs/{job_id}/move", response_model=V1JobRecordModel)
def move_job(job_id: str, req: JobMoveRequest, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    try:
        record = manager.move_job(job_id, req.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


@router.post("/jobs/{job_id}/clone", response_model=V1JobCreatedResponse, status_code=201)
def clone_job(job_id: str, req: JobMoveRequest, request: Request) -> V1JobCreatedResponse:
    manager = _manager(request)
    try:
        record = manager.clone_job(job_id, req.project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return V1JobCreatedResponse(
        job_id=record.id,
        status=record.status.value,
        workflow=record.spec.workflow,
        project_id=record.project_id,
    )


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    request: Request,
    delete_data: bool = Query(default=False),
) -> JSONResponse:
    manager = _manager(request)
    try:
        deleted = manager.delete_job(job_id, delete_data=delete_data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return JSONResponse({"deleted": True, "job_id": job_id})


def _run_job_state_action(action: Callable[[str], JobRecord], job_id: str) -> V1JobRecordModel:
    """Invoke a manager state transition (pause/unpause/continue) with uniform
    error mapping: KeyError → 404; ValueError/RuntimeError → 409 (the message
    doubles as frontend guidance text)."""
    try:
        record = action(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _record_to_v1_model(record)


@router.post("/jobs/{job_id}/pause", response_model=V1JobRecordModel)
def pause_job(job_id: str, request: Request) -> V1JobRecordModel:
    """Pause a RUNNING job (SIGSTOP locally, ``bstop`` on LSF) → PAUSED."""
    return _run_job_state_action(_manager(request).pause_job, job_id)


@router.post("/jobs/{job_id}/unpause", response_model=V1JobRecordModel)
def unpause_job(job_id: str, request: Request) -> V1JobRecordModel:
    """Revive a PAUSED job in place (SIGCONT locally, ``bresume`` on LSF) → RUNNING."""
    return _run_job_state_action(_manager(request).unpause_job, job_id)


@router.post("/jobs/{job_id}/continue", response_model=V1JobRecordModel)
def continue_job(job_id: str, request: Request) -> V1JobRecordModel:
    """Re-enter a FAILED/CANCELLED job from its checkpoint → QUEUED."""
    return _run_job_state_action(_manager(request).continue_job, job_id)


@router.post("/jobs/{job_id}/rerun", response_model=V1JobRecordModel)
def rerun_job(
    job_id: str,
    request: Request,
    body: V1JobRerunRequest | None = None,
) -> V1JobRecordModel:
    """Re-queue the existing task for a full in-place rerun.

    Unlike ``/clone``, this endpoint never creates a new job row or task
    directory.  The optional legacy ``project_id`` body is accepted only
    when it matches the current project.
    """
    manager = _manager(request)
    try:
        record = manager.rerun_job(job_id, project_id=body.project_id if body else None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


@router.post("/jobs/purge", response_model=V1JobPurgeResponse)
def purge_jobs(body: V1JobPurgeRequest, request: Request) -> V1JobPurgeResponse:
    """Batch-purge jobs; returns a per-job report (plan §4.6)."""
    if (
        body.job_ids is None
        and body.status is None
        and body.project_id is None
        and body.older_than_days is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Refusing to purge the whole queue: provide job_ids or at least "
            "one of status / project_id / older_than_days",
        )
    manager = _manager(request)
    report = manager.purge_jobs(
        job_ids=body.job_ids,
        status=body.status,
        project_id=body.project_id,
        older_than_days=body.older_than_days,
        delete_data=body.delete_data,
        force_cancel=body.force_cancel,
    )
    return V1JobPurgeResponse(results=[V1JobPurgeResult(**entry) for entry in report])


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
    return await legacy_stream_job_events(job_id, request)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str,
    request: Request,
    lines: int = Query(default=300, ge=1, le=5000),
) -> JSONResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return JSONResponse(
        {
            "stdout": read_log_tail(runtime_file(work_dir, "stdout.log"), lines=lines),
            "stderr": read_log_tail(runtime_file(work_dir, "stderr.log"), lines=lines),
        }
    )


@router.get("/jobs/{job_id}/files", response_model=FileManifestResponse)
def get_job_files(
    job_id: str,
    request: Request,
    path: str | None = None,
    view: str = Query(default="raw", pattern="^(raw|summary)$"),
) -> FileManifestResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest = build_manifest(work_dir, relative_path=path, view=view)
    entries = [
        FileEntry(
            path=item["path"],
            size=item["size"],
            modified=item["modified"],
            is_dir=item.get("is_dir", False),
        )
        for item in manifest["files"]
    ]
    return FileManifestResponse(
        work_dir=manifest["work_dir"],
        files=entries,
        truncated=manifest["truncated"],
        pinned=[
            PinnedProduct(label=item["label"], path=item["path"], kind=item["kind"])
            for item in manifest.get("pinned") or []
        ],
    )


@router.get("/jobs/{job_id}/files/{file_path:path}")
def download_job_file(job_id: str, file_path: str, request: Request) -> FileResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    resolved = resolve_safe(work_dir, file_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="File not found or outside work directory")
    return FileResponse(str(resolved), filename=resolved.name)


# ---------------------------------------------------------------------- #
# Remote job files / logs (on-demand retrieval over SFTP)
# ---------------------------------------------------------------------- #


def _remote_fetcher(request: Request):
    """Return the manager's :class:`RemoteResultFetcher` or 503 if disabled."""
    manager = _manager(request)
    fetcher = manager.remote_fetcher
    if fetcher is None:
        raise HTTPException(
            status_code=503,
            detail="Remote execution is not configured on this server",
        )
    return fetcher


def _get_remote_job_record(
    job_id: str,
    request: Request,
    project_id: str | None = None,
) -> JobRecord:
    """Fetch a job record, validating it is a remote job (404/400 otherwise).

    In the trusted-network deployment auth is not enforced, but a caller may
    supply a *project_id* to verify the job belongs to that project.  If the
    supplied project id does not match the job's project, a 403 is returned.
    """
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if project_id is not None:
        job_project = record.project_id or record.spec.project_id
        if job_project is not None and job_project != project_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied for this job",
            )
    fetcher = _remote_fetcher(request)
    if not fetcher.is_remote_job(record):
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} is not a remote job (use /jobs/{job_id}/files for local jobs)",
        )
    return record


def _user_from_request(request: Request) -> str:
    """Return a caller identifier for audit logging.

    When a trusted proxy forwards a user header, use it.  Otherwise fall
    back to the client IP, which is enough for the current deployment.
    """
    user_header = request.headers.get("x-remote-user")
    if user_header:
        return user_header
    client = request.client
    return client.host if client else "unknown"


def _log_remote_access(
    request: Request,
    job_id: str,
    file_path: str,
    action: str,
) -> None:
    """Append a remote-file access event to the job's event log."""
    from acp.scheduler.events import JobEventLog

    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        return
    try:
        JobEventLog(runtime_file(work_dir, "events.jsonl")).append(
            "remote_file_access",
            user=_user_from_request(request),
            job_id=job_id,
            file_path=file_path,
            action=action,
        )
    except Exception:
        logger = logging.getLogger(__name__)
        logger.debug("Failed to write remote access log for %s", job_id, exc_info=True)


def _remote_exception_to_http(exc: Exception) -> HTTPException:
    """Map common remote-file exceptions to a consistent HTTP status code."""
    if isinstance(exc, RemoteFileError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    if isinstance(exc, OSError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


# Whitelist of file extensions that may be previewed as text or structure.
_PREVIEW_TEXT_EXT = {".log", ".out", ".gjf", ".com", ".txt", ".json", ".csv"}
_PREVIEW_STRUCTURE_EXT = {".xyz", ".sdf", ".mol"}


def _content_type_for(file_path: str) -> str:
    """Return a reasonable Content-Type for *file_path* based on extension."""
    ext = Path(file_path).suffix.lower()
    mapping = {
        ".log": "text/plain",
        ".out": "text/plain",
        ".txt": "text/plain",
        ".gjf": "text/plain",
        ".com": "text/plain",
        ".json": "application/json",
        ".csv": "text/csv",
        ".xyz": "chemical/x-xyz",
        ".sdf": "chemical/x-mdl-sdfile",
        ".mol": "chemical/x-mdl-molfile",
    }
    return mapping.get(ext, "application/octet-stream")


def _parse_glob_patterns(value: str | None) -> list[str] | None:
    """Split a comma-separated glob string into a list of patterns."""
    if not value:
        return None
    return [p.strip() for p in value.split(",") if p.strip()]


@router.get("/jobs/{job_id}/remote-files", response_model=RemoteFileListResponse)
def list_remote_files(
    job_id: str,
    request: Request,
    project_id: str | None = Query(default=None, description="Verify job belongs to project"),
    path: str | None = Query(
        default=None, description="Subdirectory to list (one level, non-recursive)"
    ),
) -> RemoteFileListResponse:
    """List files and directories in a remote job's working directory.

    When *path* is ``None``, lists only top-level entries (one level deep).
    When *path* is provided, lists the immediate children of that subdirectory.
    """
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request, project_id=project_id)
    try:
        files = fetcher.list_files(record, relative_path=path)
        truncated = False
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc
    _log_remote_access(request, job_id, path or "", "list")
    result = record.result or {}
    return RemoteFileListResponse(
        job_id=job_id,
        node=str(result.get("node", "")),
        remote_dir=str(result.get("remote_dir", "")),
        files=[
            RemoteFileEntry(name=f.name, size=f.size, mtime=f.mtime, is_dir=f.is_dir) for f in files
        ],
        truncated=truncated,
    )


@router.get("/jobs/{job_id}/remote-files/archive")
def download_remote_archive(
    job_id: str,
    request: Request,
    include: str | None = Query(
        default=None, description="Comma-separated glob patterns, e.g. *.log,*.xyz"
    ),
    exclude: str | None = Query(
        default=None, description="Comma-separated exclusion patterns, e.g. *.rwf,*.chk"
    ),
) -> StreamingResponse:
    """Stream the entire remote job directory as a ZIP archive (no disk temp file)."""
    if ZipStream is None:
        raise HTTPException(
            status_code=503,
            detail="Streaming ZIP support is not available (zipstream-ng not installed)",
        )
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request)
    include_patterns = _parse_glob_patterns(include)
    exclude_patterns = _parse_glob_patterns(exclude)

    try:
        files = list(
            fetcher.walk_remote_files(record, include=include_patterns, exclude=exclude_patterns)
        )
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc

    total_size = sum(info.size for _, info in files)
    if total_size > RemotePreviewConfig.max_archive_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Archive contents exceed {RemotePreviewConfig.max_archive_bytes} bytes "
                f"({total_size} bytes); reduce with include/exclude filters"
            ),
        )

    zs = ZipStream()
    for rel_path, _info in files:
        zs.add(fetcher.stream_file(record, rel_path), rel_path)

    safe_job_id = job_id.replace('"', "").replace("\\", "")
    _log_remote_access(request, job_id, "", "archive")
    return StreamingResponse(
        zs,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_job_id}.zip"'},
    )


@router.get(
    "/jobs/{job_id}/remote-files/{file_path:path}/preview",
    response_model=RemoteFilePreviewResponse,
)
def preview_remote_file(
    job_id: str,
    file_path: str,
    request: Request,
    mode: str = Query(default="auto", description="text | tail | range | structure | report"),
    lines: int = Query(default=500, ge=1, le=_MAX_TAIL_LINES),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1024 * 1024, ge=1, le=_MAX_READ_BYTES),
) -> JSONResponse | RemoteFilePreviewResponse:
    """Preview a remote file as text, tail, byte range, or structure content.

    *mode=auto* chooses the preview type based on the file extension:
    ``.xyz/.sdf/.mol`` -> ``structure``, text extensions -> ``tail`` for
    ``.log/.out`` and ``text`` otherwise, and ``report`` is not selected
    automatically because it requires a known report file name.

    Files larger than the online preview limit are automatically downgraded
    to ``tail`` mode and marked with ``truncated=true``.
    """
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request)

    ext = Path(file_path).suffix.lower()
    if mode == "auto":
        if ext in _PREVIEW_STRUCTURE_EXT:
            mode = "structure"
        elif ext in _PREVIEW_TEXT_EXT:
            mode = "tail" if ext in (".log", ".out") else "text"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Preview not available for {file_path!r}; use download",
            )

    try:
        info = fetcher.file_stat(record, file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Remote file not found: {file_path}")
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc

    truncated = False
    if info.size > RemotePreviewConfig.max_text_preview_bytes and mode in (
        "text",
        "structure",
    ):
        mode = "tail"
        truncated = True

    content: str | dict[str, Any] | None = None
    try:
        if mode == "text":
            data = fetcher.read_file(record, file_path)
            content = data.decode("utf-8", errors="replace")
        elif mode == "tail":
            content = fetcher.read_tail(record, file_path, lines=lines)
        elif mode == "range":
            content = fetcher.read_range(record, file_path, offset, limit).decode(
                "utf-8", errors="replace"
            )
        elif mode == "structure":
            data = fetcher.read_file(record, file_path)
            content = data.decode("utf-8", errors="replace")
        elif mode == "report":
            content = _parse_remote_report(fetcher, record, file_path)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown preview mode: {mode!r}")
    except HTTPException:
        raise
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc

    _log_remote_access(request, job_id, file_path, f"preview:{mode}")
    response = RemoteFilePreviewResponse(
        job_id=job_id,
        path=file_path,
        mode=mode,
        content=content,
        truncated=truncated,
        size=info.size,
    )
    headers = {"X-Preview-Truncated": "true"} if truncated else {}
    return JSONResponse(_model_payload(response), headers=headers)


def _parse_remote_report(
    fetcher: _RemoteResultFetcher,
    record: JobRecord,
    file_path: str,
) -> dict[str, Any]:
    """Parse a known report file and return a structured JSON payload.

    Generic JSON reports are returned under a ``json_report`` envelope.
    Other files are returned as plain text wrapped in a generic envelope.
    """
    import json

    ext = Path(file_path).suffix.lower()
    if ext == ".json":
        data = fetcher.read_file(record, file_path)
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise RemoteFileError(f"Invalid JSON report: {exc}") from exc
        return {
            "type": "json_report",
            "file_path": file_path,
            "report": parsed,
        }
    # Fallback: return the text content as a generic report envelope.
    data = fetcher.read_file(record, file_path)
    return {
        "type": "unknown_report",
        "file_path": file_path,
        "text": data.decode("utf-8", errors="replace"),
        "generated_at": "",
    }


@router.get(
    "/jobs/{job_id}/remote-files/{file_path:path}/checksum",
    response_model=RemoteFileChecksumResponse,
)
def remote_file_checksum(
    job_id: str,
    file_path: str,
    request: Request,
) -> RemoteFileChecksumResponse:
    """Return the SHA-256 checksum of a remote file."""
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request)
    try:
        data = fetcher.read_file(record, file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Remote file not found: {file_path}")
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc
    _log_remote_access(request, job_id, file_path, "checksum")
    return RemoteFileChecksumResponse(sha256=hashlib.sha256(data).hexdigest())


@router.get("/jobs/{job_id}/remote-files/{file_path:path}")
def download_remote_file(
    job_id: str,
    file_path: str,
    request: Request,
    project_id: str | None = Query(default=None, description="Verify job belongs to project"),
) -> StreamingResponse:
    """Stream a single file from the remote job directory (on-demand)."""
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request, project_id=project_id)
    try:
        info = fetcher.file_stat(record, file_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Remote file not found: {file_path}")
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc

    filename = posixpath.basename(file_path) or "download"
    filename = filename.replace('"', "").replace("\\", "")
    safe_name = urllib.parse.quote(filename, safe="")
    headers: dict[str, str] = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}",
    }
    if info.size:
        headers["Content-Length"] = str(info.size)
    _log_remote_access(request, job_id, file_path, "download")
    return StreamingResponse(
        fetcher.stream_file(record, file_path),
        media_type=_content_type_for(file_path),
        headers=headers,
    )


@router.get("/jobs/{job_id}/remote-logs/{name}", response_model=RemoteLogTailResponse)
def get_remote_log_tail(
    job_id: str,
    name: str,
    request: Request,
    lines: int = Query(default=100, ge=1, le=5000),
    project_id: str | None = Query(default=None, description="Verify job belongs to project"),
) -> RemoteLogTailResponse:
    """Return the tail of a remote job log (``stdout.log`` / ``stderr.log``)."""
    if name not in ("stdout.log", "stderr.log"):
        raise HTTPException(
            status_code=400,
            detail="Log name must be 'stdout.log' or 'stderr.log'",
        )
    fetcher = _remote_fetcher(request)
    record = _get_remote_job_record(job_id, request, project_id=project_id)
    try:
        tail = fetcher.log_tail(record, name, lines=lines)
    except Exception as exc:
        raise _remote_exception_to_http(exc) from exc
    _log_remote_access(request, job_id, name, f"log_tail:{lines}")
    return RemoteLogTailResponse(
        job_id=job_id,
        name=name,
        lines=tail.splitlines() if tail else [],
    )


@router.get("/jobs/{job_id}/tasks", response_model=StageTaskListResponse)
def list_stage_tasks(job_id: str, request: Request) -> StageTaskListResponse:
    manager = _manager(request)
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    tasks = _stage_task_store(request).list_by_job(job_id)
    return StageTaskListResponse(tasks=[_stage_task_to_model(task) for task in tasks])


@router.get("/tasks/{task_id}", response_model=StageTaskModel)
def get_stage_task(task_id: str, request: Request) -> StageTaskModel:
    task = _stage_task_store(request).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _stage_task_to_model(task)


@router.post(
    "/jobs/{job_id}/artifacts/{artifact_id}/run-irc",
    response_model=V1JobCreatedResponse,
    status_code=202,
)
def run_irc_from_artifact(
    job_id: str,
    artifact_id: str,
    request: Request,
) -> V1JobCreatedResponse:
    """Submit an independent IRC job from a TS product in this job's manifest."""
    manager = _manager(request)
    source_record = manager.get(job_id)
    if source_record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not source_record.work_dir:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

    source_dir = Path(source_record.work_dir)
    result_dir = source_dir / "RESULT"
    manifest_path = result_dir / MANIFEST_FILENAME
    if not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    try:
        manifest = load_result_manifest(source_dir)
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid result manifest for job {job_id}: {exc}",
        ) from exc
    if manifest is None:
        if manifest_path.is_file():
            raise HTTPException(
                status_code=422,
                detail=f"Invalid result manifest for job {job_id}",
            )
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

    product = next((item for item in manifest.products if item.id == artifact_id), None)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    if product.kind.value != "structure":
        raise HTTPException(
            status_code=422,
            detail=f"Artifact {artifact_id} is not a structure product",
        )

    structure_path = resolve_safe(result_dir, product.path)
    if structure_path is None:
        raise HTTPException(
            status_code=422,
            detail=f"Artifact {artifact_id} has an invalid manifest path",
        )

    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, dict):
            raise HTTPException(status_code=422, detail="Result manifest root must be an object")
        raw_products = raw_manifest.get("products")
        if not isinstance(raw_products, list):
            raise HTTPException(status_code=422, detail="Result manifest products must be a list")
        raw_product = next(
            (
                item
                for item in raw_products
                if isinstance(item, dict) and str(item.get("id", "")) == artifact_id
            ),
            {},
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid result manifest for job {job_id}: {exc}",
        ) from exc

    metadata_tag: str | None = None
    for key in ("role", "tag"):
        raw_tag = raw_product.get(key)
        if isinstance(raw_tag, str):
            metadata_tag = normalize_tag(raw_tag)
            if metadata_tag is not None:
                break
    try:
        lines = structure_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot read structure artifact {artifact_id}: {exc}",
        ) from exc
    comment_tag = parse_tag_comment(lines[1] if len(lines) > 1 else "").get("tag")
    if (metadata_tag or comment_tag) != "TS":
        raise HTTPException(
            status_code=422,
            detail=f"Artifact {artifact_id} must carry a TS role or TAG",
        )

    source_spec = source_record.spec
    irc_spec = JobSpec(
        workflow="irc",
        name=f"{source_spec.name or job_id}_irc",
        input={
            "input_artifact": str(structure_path),
            "input_role": "transition_state",
            "directions": ["forward", "reverse"],
        },
        method=dict(source_spec.method),
        resources=dict(source_spec.resources),
        config_path=source_spec.config_path,
        tags=list(source_spec.tags),
        project_id=source_record.project_id or source_spec.project_id,
        execution_mode=source_spec.execution_mode,
        target_node=source_spec.target_node,
        molecule_name=source_spec.molecule_name,
        task_name="irc",
    )
    try:
        validate_execution_request(irc_spec)
        record = manager.submit(irc_spec)
    except ExecutionTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return V1JobCreatedResponse(
        job_id=record.id,
        status=record.status.value,
        workflow=record.spec.workflow,
        project_id=record.project_id,
    )


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
def list_artifacts(job_id: str, request: Request) -> ArtifactListResponse:
    manager = _manager(request)
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    artifacts = _artifact_registry(request).list_by_job(job_id)
    return ArtifactListResponse(artifacts=[_artifact_to_model(artifact) for artifact in artifacts])


@router.get("/artifacts/{artifact_id}", response_model=ArtifactModel)
def get_artifact(artifact_id: str, request: Request) -> ArtifactModel:
    artifact = _artifact_registry(request).get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    return _artifact_to_model(artifact)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, request: Request) -> FileResponse:
    manager = _manager(request)
    artifact = _artifact_registry(request).get(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    work_dir = manager.work_dir_of(artifact.job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {artifact.job_id}")
    resolved = resolve_safe(work_dir, artifact.file_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Artifact file not found")
    return FileResponse(str(resolved), filename=resolved.name)


@router.post("/molecule/resolve", response_model=MoleculeResolveResponse)
def resolve_molecule(req: MoleculeResolveRequest) -> MoleculeResolveResponse:
    mol, source, error = _parse_resolution_input(req)
    if mol is None:
        return MoleculeResolveResponse(source=source, valid=False, error=error)
    smiles = _canonical_smiles(mol)
    inchi, inchikey = _inchi_bundle(mol)
    return MoleculeResolveResponse(
        smiles=smiles,
        inchi=inchi,
        inchikey=inchikey,
        formula=_formula(mol),
        charge=Chem.GetFormalCharge(mol),
        multiplicity=_multiplicity(mol),
        source=source,
        valid=True,
        error=None,
    )


@router.post("/molecule/embed", response_model=MoleculeEmbedResponse)
def embed_molecule(req: MoleculeEmbedRequest) -> MoleculeEmbedResponse:
    if req.smiles:
        try:
            xyz = smiles_to_xyz(req.smiles)
        except Exception as exc:
            return MoleculeEmbedResponse(xyz="", error=str(exc))
        return MoleculeEmbedResponse(
            xyz=xyz,
            smiles=req.smiles,
            formula=xyz_formula(xyz),
            num_atoms=len(parse_xyz_first_frame(xyz)),
            error=None,
        )
    if req.molfile:
        try:
            xyz = molfile_to_xyz(req.molfile)
        except Exception as exc:
            return MoleculeEmbedResponse(xyz="", error=str(exc))
        return MoleculeEmbedResponse(
            xyz=xyz,
            smiles=None,
            formula=xyz_formula(xyz),
            num_atoms=len(parse_xyz_first_frame(xyz)),
            error=None,
        )
    return MoleculeEmbedResponse(xyz="", error="No molecule input provided")


def _asset_to_model(asset) -> StructureAssetModel:
    return StructureAssetModel(
        asset_id=asset.asset_id,
        name=asset.name,
        molecule_name=getattr(asset, "molecule_name", ""),
        tag=getattr(asset, "tag", ""),
        candidate_id=getattr(asset, "candidate_id", ""),
        source_type=asset.source_type,
        original_format=asset.original_format,
        xyz=asset.xyz,
        molfile=asset.molfile,
        has_3d=asset.has_3d,
        charge=asset.charge,
        multiplicity=asset.multiplicity,
        atom_count=asset.atom_count,
        formula=asset.formula,
        smiles=asset.smiles,
        normalized_path=getattr(asset, "normalized_path", None),
        warnings=asset.warnings,
        errors=asset.errors,
    )


@router.post("/structures/parse", response_model=StructureParseResponse)
def parse_structures(req: StructureParseRequest) -> StructureParseResponse:
    from acp.intake import detect_and_parse, parse_structure_text

    fmt = req.format
    if fmt == "auto" or not fmt:
        fmt, result = detect_and_parse(req.content, req.filename or "")
    else:
        result = parse_structure_text(req.content, fmt, req.filename)
    return StructureParseResponse(
        structures=[_asset_to_model(s) for s in result.structures],
        errors=result.errors,
        warnings=result.warnings,
        ok=result.ok,
        detected_format=fmt,
    )


# ---------------------------------------------------------------------- #
# Structure sources (reusable final structures from completed jobs)
# ---------------------------------------------------------------------- #


def _structure_source_service(request: Request) -> StructureSourceService:
    """Build a StructureSourceService from app state (503 when uninitialized)."""
    manager = _manager(request)
    run_root = getattr(request.app.state, "run_root", None) or manager.run_root
    return StructureSourceService(
        manager.store,
        Path(run_root),
        fetcher=manager.remote_fetcher,
    )


@router.get("/structure-sources/recent", response_model=StructureSourceListResponse)
def list_structure_sources(
    request: Request,
    project_id: str | None = None,
    all_projects: bool = False,
    workflow: str | None = None,
    limit: int = Query(20, ge=1, le=50),
    include_remote: bool = True,
) -> StructureSourceListResponse:
    """List reusable final structures from recent COMPLETED jobs."""
    service = _structure_source_service(request)
    if project_id:
        effective_project = project_id
    elif all_projects:
        effective_project = None
    else:
        effective_project = "uncategorized"
    entries = service.list_recent(
        limit=limit,
        project_id=effective_project,
        workflow=workflow,
        include_remote=include_remote,
    )
    return StructureSourceListResponse(
        sources=[StructureSourceSummary(**entry) for entry in entries]
    )


@router.get("/structure-sources/{source_id:path}", response_model=StructureSourceDetailResponse)
def get_structure_source(request: Request, source_id: str) -> StructureSourceDetailResponse:
    """Load one structure source (local disk or on-demand remote fetch)."""
    service = _structure_source_service(request)
    try:
        StructureSourceService.parse_source_id(source_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Invalid source_id: {source_id}")
    try:
        asset, checksum = service.get(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return StructureSourceDetailResponse(
        source_id=source_id,
        checksum=checksum,
        structure=StructureAssetModel(**asset),
    )


@router.get("/workflow-catalog")
def get_workflow_catalog() -> dict[str, Any]:
    from acp.catalog import get_workflow_catalog as _get_catalog

    return {"workflows": _get_catalog()}


@router.get("/method-catalog")
def get_method_catalog(request: Request) -> Response:
    """Return the method catalog with content-hash ETag + Cache-Control.

    R11: the catalog payload is large (>15 KB) but changes only on deploy.
    The response body is pre-serialised here (rather than letting FastAPI
    re-serialise the dict) so the SHA-256 ETag matches the actual bytes
    sent to the client. ``If-None-Match`` matches return a body-less 304.
    """
    from acp.catalog import get_method_catalog as _get_method_catalog

    payload = _get_method_catalog()
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "max-age=300"}
    if request.headers.get("if-none-match") == etag:
        # 304 must not include a body per RFC 7232 §4.1.
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.post("/validate-method", response_model=ValidateMethodResponse)
def validate_method(req: ValidateMethodRequest) -> ValidateMethodResponse:
    from acp.catalog import get_method_schema, normalize_and_validate_method_config

    schema = get_method_schema(req.schema_id)
    if not schema:
        return ValidateMethodResponse(valid=False, errors=[f"Unknown schema: {req.schema_id}"])

    method = {"levels": req.levels}
    normalized, errors = normalize_and_validate_method_config(method, schema)
    return ValidateMethodResponse(
        valid=not errors,
        errors=errors,
        normalized_levels=normalized,
    )


# ---------------------------------------------------------------------------
# Hessian preview (plan §12)
# ---------------------------------------------------------------------------

# Module-level cache for the lazy resolver. Avoids re-importing
# ``acp.chem.composition`` on every preview request.
_hessian_resolver: HessianResolver | None = None
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def _get_hessian_resolver():
    global _hessian_resolver
    if _hessian_resolver is None:
        from acp.chem.composition import resolve_recalc_hess as _resolver

        _hessian_resolver = _resolver
    return _hessian_resolver


def _formula_to_symbols(formula: str) -> list[str] | None:
    """Best-effort Hill formula → list of unique element symbols.

    Used only as a fallback when ``symbols`` is not supplied. Returns
    ``None`` if the formula cannot be parsed (e.g. empty or contains
    unrecognized tokens). Counts are ignored — only the set of distinct
    elements matters for Hessian classification.
    """
    if not formula:
        return None
    text = formula.strip()
    if not text:
        return None
    symbols: list[str] = []
    pos = 0
    for match in _FORMULA_TOKEN_RE.finditer(text):
        if match.start() != pos:
            # Unrecognized gap → bail out.
            return None
        symbols.append(match.group(1))
        pos = match.end()
    if pos != len(text):
        return None
    # Deduplicate while preserving discovery order.
    seen: dict[str, None] = {}
    for sym in symbols:
        seen.setdefault(sym, None)
    return list(seen.keys()) or None


def _resolve_configured_recalc_hess() -> object:
    """Return the server's configured ``optimization_control.recalc_hess``."""
    try:
        from cccp.config import load_config

        cfg = load_config()
        to_cfg = cfg.get("optimization_control") or {}
        return to_cfg.get("recalc_hess", "auto")
    except Exception:
        return "auto"


def _build_hessian_result(
    structure: HessianPreviewStructure,
    recalc_hess: Any,
    configured: object,
) -> HessianPreviewResult:
    resolver = _get_hessian_resolver()

    # Prefer explicit symbols; fall back to formula parsing. Only needed
    # when the policy resolves to auto — explicit 0/N do not require it.
    symbols = structure.symbols
    if not symbols and structure.formula:
        symbols = _formula_to_symbols(structure.formula)

    try:
        resolution = resolver(
            explicit=recalc_hess,
            configured=configured,
            symbols=symbols,
        )
    except ValueError as exc:
        return HessianPreviewResult(
            name=structure.name,
            error=str(exc),
        )

    # Translate the internal reason/source into the API-facing vocabulary
    # documented in plan §12.4. The resolver reports ``source="explicit"``
    # for any non-null user value (including "auto"); the API contract
    # distinguishes user-Auto (``source="auto"``) from user-N/0
    # (``source="explicit"``).
    if resolution.reason == "auto":
        if not resolution.heavy_elements:
            api_reason = "light_elements"
        elif not resolution.triggering_elements:
            api_reason = "heteroatom_only"
        else:
            api_reason = "heavy_elements"
        # ``recalc_hess`` is None when caller omitted the field → config.
        api_source = "config" if recalc_hess is None else "auto"
    else:
        api_reason = resolution.reason
        api_source = "explicit"

    return HessianPreviewResult(
        name=structure.name,
        enabled=bool(resolution.enabled),
        interval=int(resolution.interval),
        source=api_source,
        reason=api_reason,
        heavy_elements=list(resolution.heavy_elements),
        triggering_elements=list(resolution.triggering_elements),
    )


@router.post("/hessian-preview", response_model=HessianPreviewResponse)
def hessian_preview(req: HessianPreviewRequest) -> Response:
    """Resolve the Hessian policy for one or more structures.

    Does not run any ORCA calculation. Returns 422 with a field-level
    error when ``recalc_hess`` itself is invalid, and per-structure
    ``error`` entries when a structure lacks the symbols/formula needed
    for auto inference (plan §12.5).
    """
    from acp.chem.composition import normalize_recalc_hess

    # Validate the top-level recalc_hess first. Invalid values here are a
    # client error — never silently fall back.
    try:
        normalized_recalc = normalize_recalc_hess(req.recalc_hess)
    except ValueError:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "validation error",
                "errors": [
                    {
                        "field": "recalc_hess",
                        "message": "must be 'auto', 0, or an integer 1-1000",
                    }
                ],
            },
        )

    configured = _resolve_configured_recalc_hess()
    # ``normalized_recalc`` is None when the caller omitted the field —
    # that means "follow config", so pass None to the resolver.
    explicit_value: Any = normalized_recalc

    results: list[HessianPreviewResult] = []
    missing_structures: list[str] = []
    for idx, structure in enumerate(req.structures):
        # Auto inference needs symbols or formula. Defer the check until
        # we know auto is actually in play.
        symbols = structure.symbols
        if not symbols and structure.formula:
            symbols = _formula_to_symbols(structure.formula)

        eff = explicit_value if explicit_value is not None else configured
        if normalize_recalc_hess(eff) == "auto" and symbols is None:
            label = structure.name or f"structures[{idx}]"
            missing_structures.append(f"{label}: symbols or formula required for auto inference")
            results.append(
                HessianPreviewResult(
                    name=structure.name,
                    error="symbols or formula required for auto inference",
                )
            )
            continue

        results.append(_build_hessian_result(structure, explicit_value, configured))

    if missing_structures:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "validation error",
                "errors": [{"field": "structures", "message": msg} for msg in missing_structures],
                "results": [r.model_dump() for r in results],
            },
        )

    enabled = sum(1 for r in results if r.enabled and not r.error)
    disabled = sum(1 for r in results if not r.enabled and not r.error)
    summary = {"total": len(results), "enabled": enabled, "disabled": disabled}

    payload = HessianPreviewResponse(results=results, summary=summary)
    return JSONResponse(status_code=200, content=payload.model_dump())


@router.post("/uploads", response_model=UploadResponse)
async def upload_structure_file(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Query(default=""),
    parse: bool = Query(default=True),
) -> UploadResponse:
    from acp.intake import detect_format, parse_structure_text
    from acp.intake.storage import UploadStorage

    run_root = getattr(request.app.state, "run_root", None)
    if run_root is None:
        raise HTTPException(status_code=503, detail="Server not initialized with run_root")
    if not project_id:
        project_id = "uncategorized"

    upload_storage = UploadStorage(run_root)

    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="Empty upload body")

    filename = file.filename or "upload.xyz"

    try:
        upload_id, saved_path = upload_storage.save_upload(project_id, filename, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # parse=false: store-only upload (e.g. NMR Bruker raw-data zip) — the
    # binary payload is not a structure and must not go through the
    # structure parsers. The job runner resolves the asset by upload_id.
    if not parse:
        return UploadResponse(
            upload_id=upload_id,
            filename=filename,
            size=len(body),
            structures=[],
            errors=[],
            warnings=[],
            ok=True,
        )

    text = body.decode("utf-8", errors="replace")
    fmt = detect_format(filename, text)
    result = parse_structure_text(text, fmt, filename)

    for asset in result.structures:
        if asset.xyz:
            norm_name = f"{asset.name}.xyz"
            norm_path = upload_storage.save_normalized(project_id, upload_id, norm_name, asset.xyz)
            asset.normalized_path = str(norm_path.relative_to(upload_storage.run_root))

    asset_models = [_asset_to_model(s) for s in result.structures]

    return UploadResponse(
        upload_id=upload_id,
        filename=filename,
        size=len(body),
        structures=asset_models,
        errors=result.errors,
        warnings=result.warnings,
        ok=result.ok,
        detected_format=fmt,
    )


# ---------------------------------------------------------------------- #
# Maintenance endpoints (Phase 5B — local disk protection)
# ---------------------------------------------------------------------- #


def _format_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    size = float(n)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.1f} {units[idx]}"


@router.get("/maintenance/disk-usage", response_model=DiskUsageResponse)
def get_disk_usage(request: Request) -> DiskUsageResponse:
    """Return local run_root disk usage + job count (Phase 5B).

    No authentication is enforced in the current trusted-network
    deployment; for production exposure place the server behind a
    reverse proxy with IP allow-listing.
    """
    manager = _manager(request)
    cleanup = manager.local_cleanup
    if cleanup is None:
        # Cleanup disabled — still report basic filesystem stats so the
        # dashboard has something to show.
        import shutil as _shutil

        run_root = getattr(request.app.state, "run_root", str(manager.run_root))
        try:
            usage = _shutil.disk_usage(run_root)
            total, used, free = float(usage.total), float(usage.used), float(usage.free)
            pct = round((used / total) * 100, 2) if total > 0 else 0.0
        except OSError:
            total = used = free = 0.0
            pct = 0.0
        try:
            job_count = sum(manager.counts().values())
        except Exception:
            job_count = 0
        return DiskUsageResponse(
            run_root=str(run_root),
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            percent_used=pct,
            job_count=job_count,
            cleanup_enabled=False,
        )
    detail = cleanup.disk_usage_detail()
    return DiskUsageResponse(
        run_root=str(manager.run_root),
        total_bytes=detail["total_bytes"],
        used_bytes=detail["used_bytes"],
        free_bytes=detail["free_bytes"],
        percent_used=detail["percent_used"],
        job_count=int(detail["job_count"]),
        cleanup_enabled=True,
    )


@router.post("/maintenance/cleanup", response_model=MaintenanceCleanupResponse)
def trigger_cleanup(
    request: Request,
    dry_run: bool = Query(default=False),
    scope: str = Query(default="all"),
) -> MaintenanceCleanupResponse:
    """Trigger a local disk-protection sweep (Phase 5B).

    Args:
        dry_run: When ``true`` report what *would* be removed without
            deleting anything.
        scope: ``all`` (default) sweeps work_dir + DB records; ``work_dirs``
            only removes job directories; ``db_records`` only prunes SQLite
            rows.

    No authentication is enforced (trusted-network deployment).
    """
    import time as _time

    manager = _manager(request)
    cleanup = manager.local_cleanup
    if cleanup is None:
        raise HTTPException(
            status_code=503,
            detail="Local cleanup is disabled (cluster.local_retention.enabled=false)",
        )

    if scope not in ("all", "work_dirs", "db_records"):
        raise HTTPException(
            status_code=400,
            detail="scope must be one of: all, work_dirs, db_records",
        )

    started = _time.monotonic()
    # Reuse the background-thread lock so manual and automatic sweeps
    # never overlap.  When another sweep holds the lock, return a
    # conflict-style 409 so the caller can retry.
    if scope == "all":
        # full sweep goes through the manager so the cleanup.log audit
        # trail is written consistently with the background thread.
        sweep = manager.trigger_local_cleanup(dry_run=dry_run)
        if sweep is None:
            raise HTTPException(status_code=409, detail="A cleanup sweep is already in progress")
        report = sweep
    else:
        lock = manager._cleanup_lock  # type: ignore[attr-defined]
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A cleanup sweep is already in progress")
        try:
            if scope == "work_dirs":
                report = cleanup.cleanup_old_work_dirs(dry_run=dry_run)
            else:  # db_records
                report = cleanup.cleanup_old_db_records(dry_run=dry_run)
        finally:
            lock.release()
    duration_ms = int((_time.monotonic() - started) * 1000)

    return MaintenanceCleanupResponse(
        work_dirs_removed=len(report.work_dirs_removed),
        db_records_removed=report.db_records_removed,
        freed_bytes_est=report.freed_bytes_est,
        freed_human=_format_bytes(report.freed_bytes_est),
        errors=report.errors,
        dry_run=report.dry_run,
        disk_usage_before=report.disk_usage_before,
        disk_usage_after=report.disk_usage_after,
        capped=report.capped,
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------- #
# Remote node management (Phase 6)
# ---------------------------------------------------------------------- #


def _node_manager(request: Request) -> NodeManager:
    """Return the manager's :class:`NodeManager` or 503 if remote is off."""
    manager = _manager(request)
    nm = manager.node_manager
    if nm is None:
        raise HTTPException(
            status_code=503,
            detail="Remote execution is not configured on this server",
        )
    return nm


def _node_status_to_model(status) -> NodeStatusModel:
    return NodeStatusModel(
        name=status.name,
        host=status.host,
        status=status.status,
        running_jobs=status.running_jobs,
        max_jobs=status.max_jobs,
        disk_usage_pct=status.disk_usage_pct,
        last_check=status.last_check,
        error=status.error,
    )


@router.get("/nodes", response_model=NodeListResponse)
def list_nodes(request: Request) -> NodeListResponse:
    """List all configured remote nodes and their cached status."""
    nm = _node_manager(request)
    return NodeListResponse(
        nodes=[_node_status_to_model(s) for s in nm.list_nodes()],
        auto_select=True,
    )


@router.get("/nodes/{name}/status", response_model=NodeStatusModel)
def get_node_status(name: str, request: Request) -> NodeStatusModel:
    """Get the cached status of a single remote node."""
    nm = _node_manager(request)
    try:
        status = nm.get_node_status(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _node_status_to_model(status)


@router.post("/nodes/{name}/ping", response_model=NodePingResponse)
def ping_node(name: str, request: Request) -> NodePingResponse:
    """Probe SSH connectivity to a remote node and refresh its status."""
    nm = _node_manager(request)
    node = nm.config.get_node(name)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {name}")
    reachable = nm.ping_node(name)
    status = nm.get_node_status(name)
    return NodePingResponse(
        reachable=reachable,
        node=name,
        status=status.status,
        error=status.error,
    )


@router.post("/nodes/{name}/bootstrap", response_model=NodeBootstrapResponse)
def bootstrap_node(
    name: str,
    request: Request,
    timeout: int = Query(default=600, ge=10, le=1800),
    sync: bool = Query(default=True),
) -> NodeBootstrapResponse:
    """Provision a remote node with ACP runtime dependencies.

    Syncs the code (so ``requirements-node.txt`` is fresh) then runs
    ``pip install --user -r <remote_code_dir>/requirements-node.txt`` on the
    node via SSH.  Use this to bring a newly added or rebuilt node up to the
    dependency set declared in the repository, so that remote jobs do not
    fail on missing Python packages (e.g. ``openpyxl``).
    """
    nm = _node_manager(request)
    node = nm.config.get_node(name)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {name}")
    try:
        result = nm.bootstrap_node(name, timeout=timeout, sync=sync)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _tail(text: str, n: int = 4000) -> str:
        if len(text) <= n:
            return text
        return "..." + text[-n:]

    return NodeBootstrapResponse(
        node=result.node,
        reachable=result.reachable,
        ok=result.ok,
        exit_code=result.exit_code,
        python_executable=result.python_executable,
        requirements_path=result.requirements_path,
        sync_uploaded=result.sync_uploaded,
        sync_errors=result.sync_errors,
        stdout_tail=_tail(result.stdout),
        stderr_tail=_tail(result.stderr),
        error=result.error,
    )


__all__ = ["router"]
