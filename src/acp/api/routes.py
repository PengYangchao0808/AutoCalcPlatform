"""
API Routes
==========

FastAPI router: service status, backend capability discovery, workflow/protocol
introspection, job lifecycle (create/list/get/cancel), SSE event stream, logs,
and result file manifest.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from acp import __version__
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
from acp.storage.layout import runtime_file
from acp.workflows.registry import list_workflow_entries, workflow_to_dict

router = APIRouter()

_START_TIME = time.time()


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
        pending=counts.get("pending", 0),
        running=counts.get("running", 0) + counts.get("starting", 0),
        paused=counts.get("paused", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
    )
    host = getattr(request.app.state, "host", "") or ""
    port = getattr(request.app.state, "port", 0) or 0
    run_root = getattr(request.app.state, "run_root", "") or ""
    return StatusResponse(
        service="ACP Workbench",
        version=__version__,
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
        from cccp.config import load_config

        return load_config().get("executables", {})
    except Exception:
        return {}


def _resolve_backend_path(name: str, executables: dict[str, Any]) -> str:
    configured = None
    entry = executables.get(name)
    if isinstance(entry, dict):
        configured = entry.get("path")
    from cccp.software import resolve_executable

    path = resolve_executable(name, configured_path=configured)
    return str(path) if path else ""


def _backend_version(name: str, path: str) -> str:
    """TTL-cached normalized version for a resolved backend path."""
    if not path:
        return ""
    from pathlib import Path

    from cccp.software import version_cached

    return version_cached(name, Path(path))


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
        path = _resolve_backend_path(name, executables)
        backends.append(
            BackendInfo(
                name=str(status["name"]),
                available=bool(status["is_available"]),
                path=path,
                version=_backend_version(name, path),
                capabilities=caps,
            )
        )
    return BackendsResponse(backends=backends)


@router.get("/workflows", response_model=WorkflowsResponse)
def get_workflows() -> WorkflowsResponse:
    return WorkflowsResponse(workflows=_build_workflow_info())


@router.get("/protocols", response_model=ProtocolsResponse)
def get_protocols() -> ProtocolsResponse:
    # Phase A: the standalone conformer workflow (and its ALL_PROTOCOLS
    # introspection constant in acp.cli) has been removed. The only
    # protocol-like presets that remain relevant are the CENSO presets
    # used by the ensemble/energy workflows.
    names = ["censo-light", "censo-default", "censo-zero"]
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
        pending=counts.get("pending", 0),
        running=counts.get("running", 0) + counts.get("starting", 0),
        paused=counts.get("paused", 0),
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
            "stdout": read_log_tail(runtime_file(work_dir, "stdout.log"), lines=lines),
            "stderr": read_log_tail(runtime_file(work_dir, "stderr.log"), lines=lines),
        }
    )


@router.get("/jobs/{job_id}/files", response_model=FileManifestResponse)
def get_job_files(job_id: str, request: Request, path: str | None = None) -> FileManifestResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest = build_manifest(work_dir, relative_path=path)
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
async def stream_job_events(
    job_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    history: int = Query(default=200, ge=0, le=2000),
) -> StreamingResponse:
    manager = _manager(request)
    event_log = manager.event_log(job_id)
    if event_log is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    last_event_id = request.headers.get("Last-Event-ID")
    has_valid_last_event_id = False
    if last_event_id is not None:
        try:
            parsed_last_event_id = int(last_event_id)
        except (ValueError, TypeError):
            parsed_last_event_id = None
        if parsed_last_event_id is not None and parsed_last_event_id >= 0:
            after_seq = max(after_seq, parsed_last_event_id)
            has_valid_last_event_id = True

    bounded_initial = after_seq == 0 and not has_valid_last_event_id
    if type(history) is int:
        history_limit = history
    else:
        history_limit = 200
        raw_history = request.query_params.get("history")
        if raw_history is not None:
            try:
                history_limit = max(0, min(2000, int(raw_history)))
            except ValueError:
                pass

    def _decode_event(line: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _event_frame(seq: int, event: dict[str, Any]) -> str:
        event_type = event.get("type", "message")
        payload = json.dumps(event, default=str)
        return f"id: {seq}\nevent: {event_type}\ndata: {payload}\n\n"

    def _last_complete_offset() -> int:
        try:
            with event_log.path.open("rb") as handle:
                position = handle.seek(0, 2)
                while position > 0:
                    start = max(0, position - 64 * 1024)
                    handle.seek(start)
                    chunk = handle.read(position - start)
                    newline = chunk.rfind(b"\n")
                    if newline >= 0:
                        return start + newline + 1
                    position = start
        except FileNotFoundError:
            return 0
        return 0

    def _replay(fresh: bool) -> Iterator[tuple[int, int, str | None]]:
        if fresh or bounded_initial:
            replay_start = 0
            if history_limit > 0:
                total = event_log.count()
                replay_start = max(0, total - history_limit)
                recent_start, recent_events = event_log.read_recent(history_limit)
                expected_events = min(history_limit, total)
                if recent_start == replay_start and len(recent_events) == expected_events:
                    complete_end = _last_complete_offset()
                    if complete_end > 0 or total == 0:
                        for recent_seq, event in enumerate(recent_events, start=recent_start + 1):
                            yield recent_seq, complete_end, _event_frame(recent_seq, event)
                        return
        else:
            replay_start = after_seq

        line_seq = 0
        try:
            with event_log.path.open("rb") as handle:
                while True:
                    line = handle.readline()
                    if not line or not line.endswith(b"\n"):
                        return
                    line_seq += 1
                    line_offset = handle.tell()
                    if line_seq <= replay_start:
                        yield line_seq, line_offset, None
                        continue
                    event = _decode_event(line)
                    frame = _event_frame(line_seq, event) if event is not None else None
                    yield line_seq, line_offset, frame
        except FileNotFoundError:
            return

    def _read_tail(current_seq: int, current_offset: int) -> tuple[int, int, list[str], bool]:
        try:
            with event_log.path.open("rb") as handle:
                file_size = handle.seek(0, 2)
                if file_size < current_offset:
                    return current_seq, current_offset, [], True
                handle.seek(current_offset)
                next_seq = current_seq
                next_offset = current_offset
                frames: list[str] = []
                while True:
                    line = handle.readline()
                    if not line or not line.endswith(b"\n"):
                        break
                    next_offset = handle.tell()
                    next_seq += 1
                    event = _decode_event(line)
                    if event is not None:
                        frames.append(_event_frame(next_seq, event))
                return next_seq, next_offset, frames, False
        except FileNotFoundError:
            return current_seq, current_offset, [], current_offset > 0

    async def event_generator() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"

        seq = 0
        byte_offset = 0
        terminal_seen = False
        last_heartbeat = anyio.current_time()

        try:
            for replay_seq, replay_offset, frame in _replay(False):
                seq = replay_seq
                byte_offset = replay_offset
                if frame is not None:
                    yield frame
        except OSError:
            error_payload = json.dumps({"job_id": job_id, "message": "event log read error"})
            yield f"event: error\ndata: {error_payload}\n\n"
            return
        if seq < after_seq:
            seq = after_seq

        while True:
            if await request.is_disconnected():
                break

            try:
                seq, byte_offset, frames, needs_resync = _read_tail(seq, byte_offset)
            except OSError:
                error_payload = json.dumps({"job_id": job_id, "message": "event log read error"})
                yield f"event: error\ndata: {error_payload}\n\n"
                break

            if needs_resync:
                seq = 0
                byte_offset = 0
                try:
                    for replay_seq, replay_offset, frame in _replay(True):
                        seq = replay_seq
                        byte_offset = replay_offset
                        if frame is not None:
                            yield frame
                except OSError:
                    error_payload = json.dumps(
                        {"job_id": job_id, "message": "event log read error"}
                    )
                    yield f"event: error\ndata: {error_payload}\n\n"
                    break

            for frame in frames:
                yield frame

            now = anyio.current_time()
            if now - last_heartbeat >= 15.0:
                yield ": heartbeat\n\n"
                last_heartbeat = now

            record = manager.get(job_id)
            if record is not None and record.status.is_terminal:
                if not terminal_seen:
                    done_payload = json.dumps(
                        {
                            "job_id": job_id,
                            "status": record.status.value,
                            "progress": record.progress,
                            "current_stage": record.current_stage,
                            "completed_at": record.completed_at,
                        },
                        default=str,
                    )
                    seq += 1
                    yield f"id: {seq}\nevent: done\ndata: {done_payload}\n\n"
                    terminal_seen = True
                break

            await anyio.sleep(5.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


__all__ = ["router"]
