"""
API v1 Routes
=============

FastAPI router for ACP Workbench v2 resources under ``/api/v1``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    ProtocolsResponse,
    StatusResponse,
    WorkflowsResponse,
)
from acp.api.v1_schemas import (
    ArtifactListResponse,
    ArtifactModel,
    DiskUsageResponse,
    HessianPreviewRequest,
    HessianPreviewResponse,
    HessianPreviewResult,
    HessianPreviewStructure,
    JobMoveRequest,
    MaintenanceCleanupResponse,
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
    RemoteFileChecksumResponse,
    RemoteFileEntry,
    RemoteFileListResponse,
    RemoteFilePreviewResponse,
    RemoteLogTailResponse,
    StageTaskListResponse,
    StageTaskModel,
    StructureAssetModel,
    StructureParseRequest,
    StructureParseResponse,
    UploadResponse,
    V1JobCreatedResponse,
    V1JobCreateRequest,
    V1JobListResponse,
    V1JobRecordModel,
    V1JobSpecModel,
    ValidateMethodRequest,
    ValidateMethodResponse,
)
from acp.chem.embedding import (
    molfile_to_xyz,
    parse_xyz_first_frame,
    smiles_to_xyz,
    xyz_formula,
)
from acp.scheduler.artifacts import Artifact, ArtifactRegistry
from acp.scheduler.files import build_manifest, resolve_safe
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec
from acp.scheduler.logs import read_log_tail
from acp.scheduler.manager import JobManager
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
from acp.scheduler.stage_tasks import StageTask, StageTaskStore

logger = logging.getLogger(__name__)

router = APIRouter()


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


def _record_to_v1_model(record: JobRecord) -> V1JobRecordModel:
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
            target_node=spec.target_node,
        ),
        status=record.status.value,
        work_dir=record.work_dir,
        project_id=record.project_id or spec.project_id,
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
    project = manager.projects.create_project(
        req.name,
        description=req.description,
        tags=req.tags,
        settings=req.settings,
    )
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
    project = manager.projects.update_project(project_id, **_model_payload(req))
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


@router.post("/jobs", response_model=V1JobCreatedResponse, status_code=201)
def create_job(req: V1JobCreateRequest, request: Request) -> V1JobCreatedResponse:
    manager = _manager(request)
    if req.workflow not in SUPPORTED_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(SUPPORTED_WORKFLOWS)}",
        )
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
        target_node=req.target_node,
    )
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
    manager = _manager(request)
    if project_id:
        records = manager.list_jobs_by_project(project_id, limit=limit)
        if status is not None:
            records = [record for record in records if record.status.value == status]
    else:
        records = manager.list_jobs(status=status, limit=limit)
    return V1JobListResponse(
        jobs=[_record_to_v1_model(record) for record in records],
        counts=_counts_for_records(records),
    )


@router.get("/jobs/{job_id}", response_model=V1JobRecordModel)
def get_job(job_id: str, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


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
            "stdout": read_log_tail(work_dir / "stdout.log", lines=lines),
            "stderr": read_log_tail(work_dir / "stderr.log", lines=lines),
        }
    )


@router.get("/jobs/{job_id}/files", response_model=FileManifestResponse)
def get_job_files(
    job_id: str, request: Request, path: str | None = None
) -> FileManifestResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest = build_manifest(work_dir, relative_path=path)
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
        JobEventLog(work_dir / "events.jsonl").append(
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
    from acp.intake import detect_format, parse_structure_text

    fmt = req.format
    if fmt == "auto" or not fmt:
        fmt = detect_format(req.filename or "", req.content)

    result = parse_structure_text(req.content, fmt, req.filename)
    return StructureParseResponse(
        structures=[_asset_to_model(s) for s in result.structures],
        errors=result.errors,
        warnings=result.warnings,
        ok=result.ok,
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
_HESSIAN_RESOLVER = None
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def _get_hessian_resolver():
    global _HESSIAN_RESOLVER
    if _HESSIAN_RESOLVER is None:
        from acp.chem.composition import resolve_recalc_hess as _resolver

        _HESSIAN_RESOLVER = _resolver
    return _HESSIAN_RESOLVER


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
        if (
            normalize_recalc_hess(eff) == "auto"
            and symbols is None
        ):
            label = structure.name or f"structures[{idx}]"
            missing_structures.append(
                f"{label}: symbols or formula required for auto inference"
            )
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
                "errors": [
                    {"field": "structures", "message": msg}
                    for msg in missing_structures
                ],
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
        job_count=detail["job_count"],
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
