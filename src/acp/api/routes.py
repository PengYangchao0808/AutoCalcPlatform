"""
API Routes
==========

FastAPI router: service status, backend capability discovery, workflow/protocol
introspection, job lifecycle (create/list/get/cancel), SSE event stream, logs,
and result file manifest.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from acp.api.schemas import (
    BackendInfo,
    BackendsResponse,
    CapabilityInfo,
    FileEntry,
    FileManifestResponse,
    JobCreatedResponse,
    JobCreateRequest,
    JobListResponse,
    JobRecordModel,
    JobSpecModel,
    ProtocolInfo,
    ProtocolsResponse,
    QueueCounts,
    ServiceStatus,
    StatusResponse,
    WorkflowInfo,
    WorkflowsResponse,
)
from acp.scheduler.files import build_manifest, resolve_safe
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS
from acp.scheduler.logs import read_log_tail
from acp.workflows.registry import list_workflow_entries, workflow_to_dict

router = APIRouter()

_START_TIME = time.time()

_BACKEND_DEFAULT_BINARIES: dict[str, list[str]] = {
    "xtb": ["xtb"],
    "crest": ["crest"],
    "orca": ["orca"],
    "isostat": ["isostat"],
    "shermo": ["shermo"],
    "molclus": ["molclus", "Molclus"],
}


def _build_workflow_info() -> list[WorkflowInfo]:
    """Return workflow metadata from the ACP workflow registry.

    Keeps the API in sync with ``SUPPORTED_WORKFLOWS`` without hardcoding
    labels, descriptions, or required binaries in the routing layer.
    """
    return [WorkflowInfo(**workflow_to_dict(entry)) for entry in list_workflow_entries()]


def _is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version") as f:
            content = f.read().lower()
        return "microsoft" in content or "wsl" in content
    except OSError:
        return False


def _find_binary(names: list[str]) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return ""


def _manager(request: Request):
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Job scheduler not initialized")
    return manager


def _record_to_model(record) -> JobRecordModel:
    spec = record.spec
    return JobRecordModel(
        id=record.id,
        spec=JobSpecModel(
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


@router.get("/status", response_model=StatusResponse)
def get_status(request: Request) -> StatusResponse:
    manager = getattr(request.app.state, "job_manager", None)
    counts = manager.counts() if manager else {}
    queue = QueueCounts(
        queued=counts.get("queued", 0),
        running=counts.get("running", 0) + counts.get("starting", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
    )
    host = getattr(request.app.state, "host", "") or ""
    port = getattr(request.app.state, "port", 0) or 0
    run_root = getattr(request.app.state, "run_root", "") or ""
    return StatusResponse(
        service="ACP Workbench",
        version="1.0.0",
        status=ServiceStatus.OK,
        host=host,
        port=port,
        wsl=_is_wsl(),
        python=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        run_root=str(run_root),
        uptime_seconds=round(time.time() - _START_TIME, 1),
        queue=queue,
    )


def _load_executables() -> dict[str, Any]:
    try:
        from conformer_search.config import load_config

        return load_config().get("executables", {})
    except Exception:
        return {}


def _resolve_backend_path(name: str, executables: dict[str, Any]) -> str:
    cfg_entry = executables.get(name)
    if isinstance(cfg_entry, dict):
        configured = cfg_entry.get("path")
        if configured:
            found = shutil.which(str(configured))
            if found:
                return found
            return str(configured)
    return _find_binary(_BACKEND_DEFAULT_BINARIES.get(name, [name]))


@router.get("/backends", response_model=BackendsResponse)
def get_backends() -> BackendsResponse:
    from acp.backends import BackendCapabilityStatus, backend_status, list_backends

    executables = _load_executables()
    backends: list[BackendInfo] = []
    for name in list_backends():
        status = cast(dict[str, Any], backend_status(name))
        capabilities = cast(dict[str, Any], status["capabilities"])
        caps = [
            CapabilityInfo(name=cap, available=(st is BackendCapabilityStatus.AVAILABLE))
            for cap, st in capabilities.items()
        ]
        backends.append(
            BackendInfo(
                name=str(status["name"]),
                available=bool(status["is_available"]),
                path=_resolve_backend_path(name, executables),
                capabilities=caps,
            )
        )
    return BackendsResponse(backends=backends)


@router.get("/workflows", response_model=WorkflowsResponse)
def get_workflows() -> WorkflowsResponse:
    return WorkflowsResponse(workflows=_build_workflow_info())


@router.get("/protocols", response_model=ProtocolsResponse)
def get_protocols() -> ProtocolsResponse:
    names: list[str] = []
    try:
        from acp.cli import ALL_PROTOCOLS

        names = list(ALL_PROTOCOLS)
    except Exception:
        names = []
    return ProtocolsResponse(protocols=[ProtocolInfo(name=p) for p in names])


@router.post("/jobs", response_model=JobCreatedResponse, status_code=201)
def create_job(req: JobCreateRequest, request: Request) -> JobCreatedResponse:
    manager = _manager(request)
    if req.workflow not in SUPPORTED_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(SUPPORTED_WORKFLOWS)}",
        )
    from acp.scheduler.jobs import JobSpec

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
    return JobCreatedResponse(
        job_id=record.id,
        status=record.status.value,
        workflow=record.spec.workflow,
        project_id=record.project_id,
    )


@router.get("/jobs", response_model=JobListResponse)
def list_jobs(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> JobListResponse:
    manager = _manager(request)
    records = manager.list_jobs(status=status, limit=limit)
    counts = manager.counts()
    queue = QueueCounts(
        queued=counts.get("queued", 0),
        running=counts.get("running", 0) + counts.get("starting", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
    )
    return JobListResponse(jobs=[_record_to_model(r) for r in records], counts=queue)


@router.get("/jobs/{job_id}", response_model=JobRecordModel)
def get_job(job_id: str, request: Request) -> JobRecordModel:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_model(record)


@router.post("/jobs/{job_id}/cancel", response_model=JobRecordModel)
def cancel_job(job_id: str, request: Request) -> JobRecordModel:
    manager = _manager(request)
    record = manager.cancel(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_model(record)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str, request: Request, lines: int = Query(default=300, ge=1, le=5000)
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
        FileEntry(
            path=f["path"],
            size=f["size"],
            modified=f["modified"],
            is_dir=f.get("is_dir", False),
        )
        for f in manifest["files"]
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


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
    manager = _manager(request)
    event_log = manager.event_log(job_id)
    if event_log is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    async def event_generator():
        idx = 0
        terminal_seen = False
        while True:
            if await request.is_disconnected():
                break
            events = event_log.read_all()
            new_events = events[idx:]
            for evt in new_events:
                evt_type = evt.get("type", "message")
                payload = json.dumps(evt, default=str)
                yield f"event: {evt_type}\ndata: {payload}\n\n"
            idx = len(events)
            record = manager.get(job_id)
            if record is not None and record.status.is_terminal:
                if not terminal_seen:
                    done_payload = json.dumps({"job_id": job_id, "status": record.status.value})
                    yield f"event: done\ndata: {done_payload}\n\n"
                    terminal_seen = True
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["router"]
