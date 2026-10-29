"""
API Routes
==========

FastAPI router: service status, backend capability discovery, workflow/protocol
introspection, job lifecycle (create/list/get/cancel), SSE event stream, logs,
and result file manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
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
from acp.scheduler.jobs import ALL_WORKFLOWS, PUBLIC_WORKFLOWS
from acp.scheduler.logs import read_log_tail
from acp.storage.layout import runtime_file
from acp.workflows.registry import list_workflow_entries, workflow_to_dict
from acp_services.entrypoints.application_params import MAX_READ_BYTES

router = APIRouter()

_START_TIME = time.time()


def _build_workflow_info() -> list[WorkflowInfo]:
    """Return workflow metadata from the ACP workflow registry.

    Keeps the API in sync with ``PUBLIC_WORKFLOWS`` without hardcoding
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


# ---------------------------------------------------------------------------
# ACP 2.0 Controller discovery endpoints (B04a)
# ---------------------------------------------------------------------------
#
# These are the minimal read-only commands the loopback ControllerClient (and
# the B05 stdio probe) need: ``health`` proves every entry talks to the same
# application + frozen contract set, and ``capabilities`` reports the active
# workflows/backends/limits without creating a task.  Both are side-effect free.

_CONTRACT_MANIFEST_RELATIVE = "docs/reference/next-change/acp2-contracts/manifest.json"
_MCP_PROTOCOL_VERSION = "2025-06-18"
_MCP_TOOLS: tuple[str, ...] = (
    "acp_capabilities",
    "acp_preflight",
    "acp_submit",
    "acp_status",
    "acp_cancel",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_analyze",
)
#: Bounded discovery limits (application-api.v1 ``capabilities.limits``).
_CONTROLLER_LIMITS: dict[str, int] = {
    "max_artifact_bytes": 8 * 1024 * 1024,
    "max_read_bytes": MAX_READ_BYTES,
    "max_walltime_s": 7 * 24 * 60 * 60,
    "max_cores": 64,
}


def _contract_manifest_path() -> Path | None:
    """Resolve the contract manifest without assuming an installed layout."""
    override = os.environ.get("ACP_CONTRACT_MANIFEST")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / _CONTRACT_MANIFEST_RELATIVE
        if candidate.is_file():
            return candidate
    return None


def _contract_facts() -> tuple[str, str, str]:
    """Return ``(candidate_revision, contract_version, contract_digest)``.

    The Controller is the single authority for these facts: every entry reads
    them from this one endpoint rather than re-deriving a contract identity.
    """
    candidate_revision = (
        os.environ.get("ACP_CANDIDATE_REVISION") or os.environ.get("ACP2_REV") or "unknown"
    )
    contract_version = "1.0.0"
    contract_digest = hashlib.sha256(b"acp2-contract-manifest-unavailable").hexdigest()
    manifest_path = _contract_manifest_path()
    if manifest_path is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if isinstance(manifest, dict):
            baseline = manifest.get("baseline")
            if candidate_revision == "unknown" and isinstance(baseline, dict):
                recorded = baseline.get("code_candidate_revision")
                if isinstance(recorded, str) and recorded:
                    candidate_revision = recorded
            version = manifest.get("contract_version")
            if isinstance(version, str) and version:
                contract_version = version
            digest = manifest.get("contract_digest")
            if isinstance(digest, str) and digest:
                contract_digest = digest
    return candidate_revision, contract_version, contract_digest


@router.get("/health")
def get_health() -> dict[str, Any]:
    """Entry-equivalence probe (``health`` application command)."""
    candidate_revision, contract_version, contract_digest = _contract_facts()
    return {
        "schema_version": 1,
        "status": "ok",
        "candidate_revision": candidate_revision,
        "contract_version": contract_version,
        "contract_digest": contract_digest,
        "contract_digest_scope": "delivered-contract-set",
        "read_only": True,
        "started_at": datetime.fromtimestamp(_START_TIME, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _active_workflows() -> list[str]:
    try:
        from acp.scheduler.jobs import PUBLIC_WORKFLOWS

        return list(PUBLIC_WORKFLOWS)
    except ImportError:
        return []


def _task_kinds() -> list[str]:
    try:
        from cccp.calculation.requests import TaskKind

        return sorted(kind.value for kind in TaskKind)
    except ImportError:
        return []


def _backend_capability_summary() -> list[dict[str, Any]]:
    try:
        from acp.backends import backend_status, list_backends
    except ImportError:
        return []
    summaries: list[dict[str, Any]] = []
    for name in list_backends():
        try:
            status = cast(dict[str, Any], backend_status(name))
        except (KeyError, OSError, ValueError):
            summaries.append({"name": name, "available": False})
            continue
        summaries.append({"name": name, "available": bool(status.get("is_available"))})
    return summaries


def _capability_limits(request: Request) -> dict[str, Any]:
    """Publish the ceilings the running admission instance actually enforces.

    C6: the previous dict was a second hardcoded set of numbers, so discovery
    and admission could disagree.  The admission instance is the authority;
    when it is not assembled, say so instead of inventing a limit.
    """
    published = dict(_CONTROLLER_LIMITS)
    admission = getattr(request.app.state, "admission", None)
    limits = getattr(admission, "limits", None)
    if limits is None:
        published["enforcement"] = "not-verified"
        return published
    published.update(
        {
            "max_cores": int(limits.max_cores),
            "max_walltime_s": int(limits.max_walltime_s),
            "default_walltime_s": int(limits.default_walltime_s),
            "enforcement": "admission",
        }
    )
    return published


def _capabilities_payload(
    request: Request,
    *,
    research_extension: Mapping[str, Any] | str | None = None,
    project_id: str | None = None,
    client: Mapping[str, Any] | None = None,
    workflow: str | None = None,
) -> dict[str, Any]:
    """Build the discovery payload and answer the extension handshake.

    One builder serves the credential-free ``GET`` query surface and the
    equivalent ``POST`` body surface (``application-api.v1``
    ``capabilities.request``), so the two can never diverge.  The
    ``research_extension``/``project_id``/``client`` inputs are audit facts: the
    project hint is checked against the server-resolved binding and can only
    *narrow* (a mismatch is refused), never widen, and no input can authorize.
    """
    from acp.api import science_catalog
    from acp.mcp_extension import RESEARCH_EXTENSION_SPEC
    from acp_services.entrypoints.mcp import (
        RESEARCH_TOOL_NAMES,
        negotiate_research_extension,
        normalize_extension_request,
    )
    from acp_services.runtime.execution_limits import execution_limits_statement
    from acp_services.workspace.http_identity import (
        IdentityMode,
        remote_identity_providers,
        stdio_identity_binding,
    )

    admission = getattr(request.app.state, "admission", None)
    identity = getattr(request.app.state, "server_identity", None)
    identity_mode = getattr(request.app.state, "identity_mode", IdentityMode.LOCAL_SINGLE)

    if admission is not None:
        admission_snapshot = admission.snapshot()
    else:
        admission_snapshot = {
            "enforcement": "not-verified",
            "mode": "usage-only",
            "quota_enforced": False,
            "recorded_only": True,
            "model_token_enforcement": "NOT_VERIFIED",
            "multi_tenant_security": False,
        }
    try:
        requested = normalize_extension_request(research_extension)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": str(exc)},
        ) from None
    handshake = negotiate_research_extension(RESEARCH_EXTENSION_SPEC, requested)
    resolved = getattr(request.state, "request_identity", None)
    bound_project = getattr(resolved, "verified_project", None) or getattr(
        identity, "verified_project", None
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "task_kinds": _task_kinds(),
        "workflows": _active_workflows(),
        "backends": _backend_capability_summary(),
        # M2-02: the A-owned capability descriptors are forwarded instead of
        # returning ``None``.  The documents come from the one per-application
        # science catalog that `/api/v2/science/capabilities` also reads, so no
        # executable probe runs twice.  The analysis registry projection is the
        # platform fact ("what this process can execute") and keeps the frozen
        # registered/executable/unavailable distinction: a declared capability is
        # never reported as executable.
        "capability_descriptors": list(science_catalog.capability_descriptors(request.app.state)),
        "analysis_capabilities": list(science_catalog.analysis_capabilities(request.app.state)),
        "limits": _capability_limits(request),
        # M2-01c: the HTTP surface mirrors tools/list — the eight P1 tools, plus
        # the ten research tools only for a request that negotiated the extension.
        "tools": list(_MCP_TOOLS) + ([*RESEARCH_TOOL_NAMES] if handshake.negotiated else []),
        "research_tools": {
            "registered": [*RESEARCH_TOOL_NAMES],
            "visible": bool(handshake.negotiated),
            "negotiated": bool(handshake.negotiated),
        },
        "protocol_version": _MCP_PROTOCOL_VERSION,
        "extensions": [dict(entry) for entry in handshake.entries],
        "capability_request": {
            "client": dict(client) if client is not None else None,
            "project_id": project_id,
            "research_extension": (
                dict(handshake.requested) if handshake.requested is not None else None
            ),
            "authorization": "none: audit and advisory hints only",
        },
        "admission": admission_snapshot,
        "admission_enforcement": admission_snapshot["enforcement"],
        # M1-04b: the non-BudgetUnit execution limits (admission-bound /
        # process-termination / queue-deadline) and the three clocks.  Same
        # matrix as the release statement; never re-derived per surface.
        "execution_limits": execution_limits_statement(),
        "model_token_enforcement": "NOT_VERIFIED",
        "multi_tenant_security": (
            False if identity_mode == IdentityMode.LOCAL_SINGLE else "api-key-scoped"
        ),
        "identity_mode": identity_mode,
        "identity_profiles": [IdentityMode.LOCAL_SINGLE, IdentityMode.API_KEY],
        "remote_identity_providers": remote_identity_providers(),
        "stdio_identity_binding": stdio_identity_binding(),
    }
    if identity_mode == IdentityMode.LOCAL_SINGLE and identity is not None:
        payload["identity"] = identity.to_dict()
    elif identity_mode != IdentityMode.LOCAL_SINGLE:
        payload["identity"] = {
            "mode": identity_mode,
            "principal_resolution": "api-key-verified-by-controller",
            "project_hint_policy": "narrow-only",
            "multi_tenant_security": "api-key-scoped",
        }
    if workflow is not None:
        from acp.api.workflow_contracts import (
            ACTIVE_WORKFLOWS,
            canonical_workflow,
            describe_workflow,
            workflow_suggestion,
        )

        canonical = canonical_workflow(workflow)
        if canonical is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "input_invalid",
                    "message": f"unknown workflow {workflow!r}",
                    "params": {
                        "field": "workflow",
                        "rule": "unknown_workflow",
                        "allowed": list(ACTIVE_WORKFLOWS),
                        "suggestion": workflow_suggestion(workflow),
                    },
                },
            )
        payload["workflow_contract"] = describe_workflow(canonical)

    if handshake.refusal is not None:
        # Typed, in-band refusal: the client still receives extensions[] so it
        # can read the supported version instead of guessing.
        params = dict(handshake.refusal.get("params") or {})
        raise HTTPException(
            status_code=422,
            detail={
                "code": str(handshake.refusal.get("code") or "unsupported_capability"),
                "message": "research extension request refused",
                "params": params,
                "extensions": [dict(entry) for entry in handshake.entries],
            },
        )
    if project_id is not None and bound_project is not None and project_id != bound_project:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": "requested project does not match the server binding",
                "params": {
                    "rule": "project_scope_mismatch",
                    "requested_project_id": project_id,
                    "bound_project_id": bound_project,
                },
                "extensions": [dict(entry) for entry in handshake.entries],
            },
        )
    return payload


@router.get("/capabilities")
def get_capabilities(
    request: Request,
    research_extension: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    client: str | None = Query(default=None),
    workflow: str | None = Query(default=None),
) -> dict[str, Any]:
    """Read-only discovery (``capabilities`` application command).

    Credential-free by design.  The optional query parameters are the flat
    spelling of the ``POST`` body surface: ``research_extension=id@version``,
    ``project_id`` and ``client`` (``name/version``), all audit-only.
    """
    client_facts: dict[str, Any] | None = None
    if client:
        name, _, version = client.partition("/")
        client_facts = {"name": name.strip(), "version": version.strip() or None}
    return _capabilities_payload(
        request,
        research_extension=research_extension,
        project_id=project_id,
        client=client_facts,
        workflow=workflow,
    )


@router.post("/capabilities")
async def post_capabilities(request: Request) -> dict[str, Any]:
    """Body-form discovery: the exact equivalent of ``GET /api/capabilities``.

    Still a read-only command in the identity-exempt discovery set; the body may
    only add audit facts and the extension handshake request.
    """
    try:
        body = await request.json()
    except ValueError:
        body = None
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": "capabilities body must be an object"},
        )
    allowed_fields = {
        "schema_version",
        "extensions",
        "research_extension",
        "project_id",
        "client",
        "workflow",
    }
    unknown = set(body) - allowed_fields
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": "unknown capabilities field",
                "params": {"unknown_fields": sorted(unknown)},
            },
        )
    research = body.get("research_extension")
    if research is not None and not isinstance(research, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": "research_extension must be an object"},
        )
    client = body.get("client")
    if client is not None and not isinstance(client, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": "client must be an object"},
        )
    project_id = body.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": "project_id must be a string"},
        )
    workflow = body.get("workflow")
    if workflow is not None and not isinstance(workflow, str):
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": "workflow must be a string"},
        )
    return _capabilities_payload(
        request,
        research_extension=research,
        project_id=project_id,
        client=client,
        workflow=workflow,
    )


@router.get("/identity")
def get_identity(request: Request) -> dict[str, Any]:
    """Return the server-resolved principal for this request's credential."""
    from acp_services.workspace.http_identity import IdentityMode

    resolved = getattr(request.state, "request_identity", None)
    if resolved is not None:
        return resolved.to_dict()
    identity = getattr(request.app.state, "server_identity", None)
    identity_mode = getattr(request.app.state, "identity_mode", IdentityMode.LOCAL_SINGLE)
    if identity is None:
        raise HTTPException(status_code=503, detail="identity is not initialized")
    payload = identity.to_dict()
    payload["schema_version"] = 1
    payload["mode"] = identity_mode
    return payload


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


# Software probe names that map to a differently-named backend adapter.
_REMOTE_SOFTWARE_TO_BACKEND = {"shermo": "external"}


def _remote_backend_map(request: Request) -> dict[str, dict[str, Any]]:
    """Aggregate QC software probed on reachable remote nodes, keyed by backend name.

    Reads the NodeManager's cached node statuses only (no SSH), so this never
    blocks ``/api/backends``; the node panel's own polling keeps the cache
    warm.  Returns ``{}`` when remote execution is not configured.
    """
    manager = getattr(request.app.state, "job_manager", None)
    nm = getattr(manager, "node_manager", None) if manager is not None else None
    if nm is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for status in nm.cached_node_statuses():
        if status.status == "offline":
            continue
        for sw_name, info in (status.software or {}).items():
            resolved = info.get("resolved") if isinstance(info, dict) else None
            if not resolved:
                continue
            backend = _REMOTE_SOFTWARE_TO_BACKEND.get(sw_name, sw_name)
            entry = result.setdefault(backend, {"nodes": [], "path": "", "version": ""})
            entry["nodes"].append(status.name)
            if not entry["path"]:
                entry["path"] = str(resolved)
                entry["version"] = str(info.get("version") or "")
    return result


@router.get("/backends", response_model=BackendsResponse)
def get_backends(request: Request) -> BackendsResponse:
    from acp.backends import BackendCapabilityStatus, backend_status, list_backends

    executables = _load_executables()
    remote = _remote_backend_map(request)
    backends: list[BackendInfo] = []
    for name in list_backends():
        status = cast(dict[str, Any], backend_status(name))
        capabilities = cast(dict[str, Any], status["capabilities"])
        caps = [
            CapabilityInfo(name=cap, available=(st is BackendCapabilityStatus.AVAILABLE))
            for cap, st in capabilities.items()
        ]
        path = _resolve_backend_path(name, executables)
        rinfo = remote.get(name) or {}
        backends.append(
            BackendInfo(
                name=str(status["name"]),
                available=bool(status["is_available"]),
                path=path,
                version=_backend_version(name, path),
                capabilities=caps,
                remote_available=bool(rinfo),
                remote_nodes=list(rinfo.get("nodes") or []),
                remote_path=str(rinfo.get("path") or ""),
                remote_version=str(rinfo.get("version") or ""),
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
    if req.workflow not in ALL_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(PUBLIC_WORKFLOWS)}",
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


def _legacy_stream_still_authorized(request: Request, job_id: str) -> bool:
    """Re-resolve the credential on every poll of an already-open SSE stream.

    A subscription started before revocation must stop at the next delivery, so
    the in-process ``app.state`` authorizer alone is never trusted here.
    """
    boundary = getattr(request.app.state, "identity_boundary", None)
    if boundary is None or not boundary.enabled:
        return True
    from acp_services.workspace.http_identity import credential_from_headers

    resolved, denial = boundary.resolve(credential_from_headers(dict(request.headers)))
    if denial is not None or resolved is None:
        return False
    manager = getattr(request.app.state, "job_manager", None)
    record = manager.get(job_id) if manager is not None else None
    if record is None:
        return False
    decision = resolved.server_identity.authorize_job_project(
        record.project_id or record.spec.project_id,
        action="read",
        job_id=job_id,
    )
    return bool(decision.allowed)


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

            if not _legacy_stream_still_authorized(request, job_id):
                yield 'event: reset\ndata: {"reason": "permission_changed"}\n\n'
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


# ---------------------------------------------------------------------- #
# One authorized, reusable stream per (principal, scope)
# ---------------------------------------------------------------------- #
#
# One connection multiplexes every owner in the requested scope.  Authorization
# is re-checked on every poll (revocation closes the live connection), and
# cursor expiry / permission change / restart emit an explicit snapshot-reset
# frame.  Coverage is declared in the A04 contract document; whether a consumer
# may stop polling is that consumer's decision.


class _EntryStreamAuthorizer:
    """Adapt the per-entry authorization gate to the Services stream port."""

    def __init__(self, authorizer: Any) -> None:
        self._authorizer = authorizer

    def authorize_stream(self, *, principal_id: str, scope: Any, job_id: str = ""):
        from acp_services.contracts.events import EventScope
        from acp_services.runtime.events import StreamAuthorization
        from acp_services.workspace.entry_authz import EntryClass

        decision = self._authorizer.authorize(EntryClass.EVENT_STREAM, job_id=job_id)
        resolved_scope = scope if isinstance(scope, EventScope) else EventScope(scope)
        return StreamAuthorization(
            allowed=bool(decision.allowed),
            principal_id=decision.principal_id,
            scope=resolved_scope,
            reason_code=decision.reason_code,
            detail=decision.detail,
        )


class _ReauthorizingStreamAuthorizer:
    """Re-resolve the API-key credential on every poll of a scope stream.

    The stream registry already re-checks its authorizer each cycle; binding a
    resolver that reads the key/account rows is what makes revocation effective
    on a subscription that was opened *before* the revocation.
    """

    def __init__(self, boundary: Any, credential: str | None, manager: Any) -> None:
        self._boundary = boundary
        self._credential = credential
        self._manager = manager

    def authorize_stream(self, *, principal_id: str, scope: Any, job_id: str = ""):
        from acp_services.contracts.events import EventScope
        from acp_services.runtime.events import StreamAuthorization

        resolved_scope = scope if isinstance(scope, EventScope) else EventScope(scope)
        resolved, denial = self._boundary.resolve(self._credential)
        if denial is not None or resolved is None:
            return StreamAuthorization(
                allowed=False,
                principal_id=principal_id,
                scope=resolved_scope,
                reason_code="revoked",
                detail="credential is no longer valid",
            )
        if principal_id and resolved.principal.principal_id != principal_id:
            return StreamAuthorization(
                allowed=False,
                principal_id=resolved.principal.principal_id,
                scope=resolved_scope,
                reason_code="permission_changed",
                detail="stream principal changed",
            )
        record = self._manager.get(job_id) if job_id else None
        project = None
        if record is not None:
            project = record.project_id or record.spec.project_id
        decision = resolved.server_identity.authorize_job_project(
            project,
            action="read",
            job_id=job_id,
        )
        return StreamAuthorization(
            allowed=bool(decision.allowed),
            principal_id=resolved.principal.principal_id,
            scope=resolved_scope,
            reason_code=decision.reason_code,
            detail=decision.detail,
        )


def _scope_stream_registry(request: Request):
    from acp_services.runtime.events import AuthorizedStreamRegistry

    registry = getattr(request.app.state, "authorized_event_streams", None)
    if registry is None:
        registry = AuthorizedStreamRegistry()
        request.app.state.authorized_event_streams = registry
    return registry


def _parse_stream_cursors(raw: str) -> dict[str, int]:
    cursors: dict[str, int] = {}
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        owner, _, seq = token.rpartition(":")
        if not owner:
            continue
        try:
            cursors[owner] = max(0, int(seq))
        except ValueError:
            continue
    return cursors


def _scope_event_frame(event: Any) -> str:
    payload = json.dumps(event.to_dict(), default=str)
    return f"id: {event.owner_id}:{event.seq}\nevent: {event.event_type}\ndata: {payload}\n\n"


def _scope_reset_frame(reason: str, scope: Any, principal_id: str, **extra: Any) -> str:
    payload = {
        "reason": reason,
        "scope": scope.value,
        "principal_id": principal_id,
        **extra,
    }
    return f"event: reset\ndata: {json.dumps(payload, default=str)}\n\n"


@router.get("/events/stream")
async def stream_scope_events(
    request: Request,
    scope: str = Query(default="task"),
    job_ids: str = Query(default="", description="Comma-separated owners in this scope"),
    after: str = Query(default="", description="Composite cursor: job_id:seq,job_id:seq"),
    epoch: str = Query(default="", description="Client's last service epoch"),
) -> StreamingResponse:
    """One authorized stream per ``(principal, scope)``; many tasks, one connection."""
    from acp_services.contracts.events import EventScope
    from acp_services.runtime.events import (
        RESET_CURSOR_EXPIRED,
        RESET_PERMISSION_CHANGED,
        RESET_SERVICE_RESTART,
        StreamAuthorizationError,
        StreamCursor,
    )
    from acp_services.workspace.entry_authz import default_entry_authorizer

    manager = _manager(request)
    try:
        resolved_scope = EventScope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown event scope: {scope}") from exc

    owners = [item.strip() for item in job_ids.split(",") if item.strip()]
    if not owners:
        raise HTTPException(status_code=422, detail="job_ids must name at least one owner")

    resolved_request = getattr(request.state, "request_identity", None)
    for owner in owners:
        record = manager.get(owner)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Job not found: {owner}")
        if resolved_request is not None:
            decision = resolved_request.server_identity.authorize_job_project(
                record.project_id or record.spec.project_id,
                action="read",
                job_id=owner,
            )
            if not decision.allowed:
                raise HTTPException(status_code=404, detail=f"Job not found: {owner}")

    boundary = getattr(request.app.state, "identity_boundary", None)
    authorizer = getattr(request.app.state, "entry_authorizer", None)
    if resolved_request is not None and boundary is not None and boundary.enabled:
        from acp_services.workspace.http_identity import credential_from_headers

        guard = _ReauthorizingStreamAuthorizer(
            boundary,
            credential_from_headers(dict(request.headers)),
            manager,
        )
    else:
        guard = _EntryStreamAuthorizer(
            authorizer if authorizer is not None else default_entry_authorizer()
        )
    probe = guard.authorize_stream(principal_id="", scope=resolved_scope, job_id=owners[0])
    if probe.denied:
        raise HTTPException(
            status_code=403,
            detail={"code": probe.reason_code, "message": probe.detail or "event stream denied"},
        )

    registry = _scope_stream_registry(request)
    try:
        stream = registry.open(
            principal_id=probe.principal_id,
            scope=resolved_scope,
            service_epoch=_START_TIME,
            log_for=manager.event_log,
            job_ids=owners,
            authorizer=guard,
        )
    except StreamAuthorizationError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": RESET_PERMISSION_CHANGED, "message": str(exc)},
        ) from exc

    initial_cursors = _parse_stream_cursors(after)
    client_epoch = epoch or str(_START_TIME)

    def _cursors(values: dict[str, int]) -> list[Any]:
        return [
            StreamCursor(
                principal_id=stream.principal_id,
                scope=stream.scope,
                job_id=owner,
                after_seq=values.get(owner, 0),
                service_epoch=client_epoch,
            )
            for owner in owners
        ]

    async def event_generator() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        cursors = _cursors(initial_cursors)
        last_heartbeat = anyio.current_time()

        while True:
            if await request.is_disconnected():
                break

            if not stream.check_authorization(guard, job_ids=owners):
                yield _scope_reset_frame(
                    RESET_PERMISSION_CHANGED, stream.scope, stream.principal_id
                )
                break

            batch = stream.poll(cursors)
            if batch.reset:
                yield _scope_reset_frame(
                    batch.reset_reason or RESET_CURSOR_EXPIRED,
                    stream.scope,
                    stream.principal_id,
                    earliest_seq=dict(batch.earliest_seq),
                    latest_seq=dict(batch.latest_seq),
                )
                snapshot = stream.snapshot()
                for event in snapshot.events:
                    yield _scope_event_frame(event)
                cursors = _cursors(dict(snapshot.cursors))
                if batch.reset_reason in (RESET_SERVICE_RESTART, RESET_PERMISSION_CHANGED):
                    break
            else:
                for event in batch.events:
                    yield _scope_event_frame(event)
                cursors = _cursors(dict(batch.cursors))

            records = [manager.get(owner) for owner in owners]
            if all(record is not None and record.status.is_terminal for record in records):
                for owner, record in zip(owners, records):
                    done_payload = json.dumps(
                        {
                            "job_id": owner,
                            "status": record.status.value,
                            "progress": record.progress,
                            "current_stage": record.current_stage,
                            "completed_at": record.completed_at,
                        },
                        default=str,
                    )
                    yield f"event: done\ndata: {done_payload}\n\n"
                break

            now = anyio.current_time()
            if now - last_heartbeat >= 15.0:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await anyio.sleep(1.0)

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
