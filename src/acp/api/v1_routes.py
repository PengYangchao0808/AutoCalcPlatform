"""
API v1 Routes
=============

FastAPI router for ACP Workbench v2 resources under ``/api/v1``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any
import re

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

try:
    from rdkit.Chem import rdDetermineBonds
except ImportError:  # pragma: no cover
    rdDetermineBonds = None

from acp.chem.embedding import (
    count_elements_from_xyz,
    molfile_to_xyz,
    parse_xyz_first_frame,
    smiles_to_xyz,
    xyz_formula,
)
from acp.api.routes import (
    get_backends as legacy_get_backends,
    get_protocols as legacy_get_protocols,
    get_status as legacy_get_status,
    get_workflows as legacy_get_workflows,
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
    MoleculeEmbedRequest,
    MoleculeEmbedResponse,
    MoleculeResolveRequest,
    MoleculeResolveResponse,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectModel,
    ProjectUpdateRequest,
    StageTaskListResponse,
    StageTaskModel,
    StructureAssetModel,
    StructureParseRequest,
    StructureParseResponse,
    UploadResponse,
    ValidateMethodRequest,
    ValidateMethodResponse,
    V1JobCreateRequest,
    V1JobCreatedResponse,
    V1JobListResponse,
    V1JobRecordModel,
    V1JobSpecModel,
)
from acp.scheduler.artifacts import Artifact, ArtifactRegistry
from acp.scheduler.files import build_manifest, resolve_safe
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec
from acp.scheduler.logs import read_log_tail
from acp.scheduler.manager import JobManager
from acp.scheduler.stage_tasks import StageTask, StageTaskStore

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
    if project_id == manager.default_project_id:
        raise HTTPException(status_code=400, detail="Default project cannot be deleted")
    deleted = manager.projects.delete_project(project_id, delete_data=delete_data)
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
def get_job_files(job_id: str, request: Request) -> FileManifestResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest = build_manifest(work_dir)
    entries = [
        FileEntry(path=item["path"], size=item["size"], modified=item["modified"])
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
def get_method_catalog() -> dict[str, Any]:
    from acp.catalog import get_method_catalog as _get_method_catalog
    return _get_method_catalog()


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


@router.post("/uploads", response_model=UploadResponse)
async def upload_structure_file(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Query(default=""),
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


__all__ = ["router"]
