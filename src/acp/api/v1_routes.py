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
import math
import posixpath
import re
import sqlite3
import threading
import urllib.parse
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from typing_extensions import assert_never

try:
    from rdkit.Chem import rdDetermineBonds
except ImportError:  # pragma: no cover
    rdDetermineBonds = None  # noqa: N816

try:
    from zipstream import ZipStream
except ImportError:  # pragma: no cover
    ZipStream = None

from acp.api.job_status_view import build_live_status, resolve_display_method
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
from acp.api.submission_contract import (
    SubmissionContractError,
    validate_workflow_submission,
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
    FrequencySourceEntryModel,
    FrequencySourceOriginModel,
    FrequencySourcesResponse,
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
    NodeMatchingItem,
    NodeMatchingRequest,
    NodeMatchingResponse,
    NodePingResponse,
    NodeStatusModel,
    OptimizationFrameResponse,
    PesReviewBackupSummary,
    PesReviewCandidate,
    PesReviewRequest,
    SelectionAuthorizationCreateRequest,
    PesReviewResponse,
    PesReviewRestoreRequest,
    PesReviewRestoreResponse,
    PesReviewStateResponse,
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
    StructureViewerOverlayResponse,
    StructureViewerPayloadModel,
    StructureViewerVibrationsResponse,
    StudyPromoteResponse,
    StudyResumeResponse,
    UploadResponse,
    V1AnalyzeRequest,
    V1EditDiffEntry,
    V1EditDraftResponse,
    V1EditPreviewRequest,
    V1EditPreviewResponse,
    V1EditRecalculateRequest,
    V1EditRecalculateResponse,
    V1FrameCandidateInfo,
    V1FrameCandidateListResponse,
    V1FrameCandidateRequest,
    V1FrameCandidateResponse,
    V1JobContinueRequest,
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
    V1PreflightRequest,
    V1PrepareRequest,
    V1SamplingFrameResponse,
    V1SoftwareCandidate,
    V1SoftwareDiscoveryResponse,
    V1SoftwareEntry,
    ValidateMethodRequest,
    ValidateMethodResponse,
)
from acp.chem.embedding import (
    molfile_to_xyz,
    parse_xyz_first_frame,
    smiles_to_xyz,
    xyz_formula,
)
from acp.core.stage_labels import stage_label
from acp.results.manifest import MANIFEST_FILENAME
from acp.results.pes_profile import (
    LEGACY_S2_PROFILE_RELATIVE_PATH,
    PES_PROFILE_RELATIVE_PATH,
    load_pes_profile,
)
from acp.scheduler.artifacts import Artifact, ArtifactRegistry
from acp.scheduler.capabilities import (
    NoCapableNodeError,
    derive_required_software,
    is_degraded,
    local_satisfies,
    matches_capabilities,
)
from acp.scheduler.events import JobEventLog
from acp.scheduler.files import build_manifest, is_archived_attempt_path, resolve_safe
from acp.scheduler.job_edit import (
    EditConflictError,
    EditValidationError,
    build_edit_draft,
    compute_payload_hash,
    compute_preview_fingerprint,
    compute_source_revision,
    diff_editable_specs,
    editable_spec_from_parts,
    editable_spec_from_record,
    input_structure_changed,
    workflow_edit_status,
)
from acp.scheduler.jobs import (
    ALL_WORKFLOWS,
    PUBLIC_WORKFLOWS,
    JobRecord,
    JobSpec,
    JobStatus,
    validate_scan_submission,
)
from acp.scheduler.logs import read_log_range, read_log_tail
from acp.scheduler.manager import JobManager
from acp.scheduler.naming import canonical_molecule_name, molecule_name_from_input
from acp.scheduler.nodes import (
    ExecutionTargetError,
    validate_execution_request,
    validate_submission_target,
)
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
from acp.scheduler.resources import validate_resource_request
from acp.scheduler.runner import find_workflow_state
from acp.scheduler.stage_tasks import StageTask, StageTaskStore
from acp.scheduler.store import JobStore
from acp.scheduler.structure_sources import StructureSourceService
from acp.scheduler.tasks import resolve_task_names
from acp.storage.layout import runtime_file
from acp_services.contracts.access import AccessAction as _AccessAction
from acp_services.runtime.idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyReservationDeniedError,
)
from acp_services.runtime.local_admission import AdmissionRefusedError
from acp_services.workspace.entry_authz import EntryClass as _EntryClass

UTC = timezone.utc  # datetime.UTC is 3.11+; keep the short name on 3.10

logger = logging.getLogger(__name__)


def _job_path_authorization(request: Request) -> None:
    """Scope every ``/jobs/{job_id}/...`` route to the verified project.

    Endpoint-specific checks still decide the exact action; this router-level
    gate exists so a newly added job sub-route cannot silently bypass B15's
    project isolation.  It is inert in the default ``local-single`` mode.
    """
    job_id = request.path_params.get("job_id")
    if not job_id:
        return
    resolved = getattr(request.state, "request_identity", None)
    manager = getattr(request.app.state, "job_manager", None)
    if resolved is None or manager is None:
        return
    record = manager.get(str(job_id))
    if record is None:
        return
    path = request.url.path
    if request.method in {"GET", "HEAD"}:
        action = _AccessAction.READ
    elif path.endswith(("/pause", "/unpause", "/continue", "/cancel", "/rerun")):
        action = _AccessAction.CONTROL
    elif request.method == "DELETE":
        action = _AccessAction.CONTROL
    else:
        action = _AccessAction.WRITE
    decision = resolved.server_identity.authorize_job_project(
        record.project_id or record.spec.project_id,
        action=action,
        job_id=str(job_id),
    )
    if not decision.allowed:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


router = APIRouter(dependencies=[Depends(_job_path_authorization)])

_ENRICHMENT_CACHE_LIMIT: Final = 256
_ENRICHMENT_CACHE: OrderedDict[tuple[str, int | None, int | None, bool], dict[str, Any]] = (
    OrderedDict()
)
_ENRICHMENT_CACHE_LOCK = threading.Lock()

if TYPE_CHECKING:
    from acp.chem.composition import HessianResolution
    from acp_services.runtime.commands import RuntimeCommands


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


def _commands(request: Request) -> RuntimeCommands:
    """Return the Runtime command surface for submit/control entries (todo 25).

    Every submit/control/edit/purge endpoint routes through this single owner;
    the Runtime use cases forward once to the injected composition-root ports.
    An explicit ``app.state.runtime_commands`` may be injected (test seam /
    future self-sufficient bootstrap), otherwise it is built over the live
    manager.
    """
    commands = getattr(request.app.state, "runtime_commands", None)
    if commands is not None:
        return commands
    from acp.services_bootstrap import build_runtime_commands

    return build_runtime_commands(_manager(request))


def _entry_authorizer(request: Request) -> Any:
    """Return the per-request entry authorizer.

    An explicit authorizer may be injected on ``app.state.entry_authorizer``
    (test seam / future identity policy).  Otherwise the trusted ``local-single``
    authorizer is used: the hook always runs, it is simply inert until an
    identity/grant policy is configured.
    """
    from acp_services.workspace.entry_authz import default_entry_authorizer

    resolved = getattr(request.state, "request_identity", None)
    if resolved is not None:
        return resolved.entry_authorizer
    authorizer = getattr(request.app.state, "entry_authorizer", None)
    return authorizer if authorizer is not None else default_entry_authorizer()


def _job_project_id(request: Request, job_id: str) -> str | None:
    """Best-effort project id of *job_id* (used only for scope resolution)."""
    record = _manager(request).get(job_id)
    if record is None:
        return None
    return getattr(record, "project_id", None) or getattr(record.spec, "project_id", None)


def _require_entry_access(
    request: Request,
    entry_class: str,
    *,
    job_id: str = "",
    project_id: str | None = None,
    action: str | None = None,
    resource_id: str = "",
) -> None:
    """Authorize one resource entry; raise a 403 on deny.

    Runs at the point of effect (before a download streams, before a publish
    writes) rather than on a listing page.  A denial raises before the endpoint
    body executes, so no state is created or mutated.  The 403 body shape
    (``{"code": ..., "message": ...}``) matches the existing error contract.
    """
    authorizer = _entry_authorizer(request)
    project = (
        project_id
        if project_id is not None
        else (_job_project_id(request, job_id) if job_id else None)
    )
    decision = authorizer.authorize(
        entry_class,
        job_id=job_id,
        project_id=project,
        action=action,
        resource_id=resource_id,
    )
    if not decision.allowed:
        from acp_services.workspace.entry_authz import EntryReasonCode

        if (
            job_id
            and getattr(request.state, "request_identity", None) is not None
            and str(decision.reason_code)
            in {
                EntryReasonCode.NO_GRANT,
                EntryReasonCode.SCOPE_MISMATCH,
            }
        ):
            # A job outside the verified project must look exactly like an
            # unknown job; the 403 shape would confirm that the task exists.
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        raise HTTPException(status_code=decision.http_status, detail=decision.error_detail())


def _server_identity(request: Request) -> Any:
    """Return the Controller-process identity (never a request field).

    The lifespan installs it on ``app.state``; a bare app/test falls back to the
    process environment.  A project hint is handled by the returned object and
    can only narrow the verified scope.
    """
    resolved = getattr(request.state, "request_identity", None)
    if resolved is not None:
        return resolved.server_identity
    identity = getattr(request.app.state, "server_identity", None)
    if identity is not None:
        return identity
    from acp_services.workspace.server_identity import ServerIdentity

    identity = ServerIdentity.from_environment()
    request.app.state.server_identity = identity
    return identity


def _command_identity(request: Request, *, project_id: str | None = None) -> Any:
    """Return the verified :class:`CommandIdentity` of this request (M0-04).

    The authenticated HTTP path projects the credential-resolved principal
    (B15).  The default ``local-single`` deployment has no per-request identity
    and projects the Controller's own operator identity instead — that is the
    only legitimate use of ``LOCAL_PRINCIPAL_ID``.  A request body/header can
    never supply the principal.
    """
    from acp_services.workspace.command_identity import CommandIdentity, EntryClass

    resolved = getattr(request.state, "request_identity", None)
    if resolved is not None:
        return CommandIdentity.from_verified_http(
            resolved, project_id=project_id, entry_class=EntryClass.HTTP
        )
    # No verified credential: the command runs as the Controller process, so
    # the entry class is CONTROLLER — the same label the model gateway and the
    # research saga use. A transport label ("http") here forked the derived
    # ResearchIdentity from every other local path for the same request.
    return CommandIdentity.from_server(
        _server_identity(request),
        project_id=project_id,
        entry_class=EntryClass.CONTROLLER,
    )


def _require_submit_access(request: Request, project_hint: str | None) -> str | None:
    """Authorize a submit before normalization; return the effective project."""
    decision = _server_identity(request).resolve_submit_project(project_hint)
    if not decision.allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": decision.detail or "project hint widens the verified scope",
                "params": {
                    "reason_code": decision.reason_code,
                    "requested_project": project_hint,
                },
                "retryable": False,
            },
        )
    return decision.project_id


#: Simple ORCA workflows whose legacy method mirrors are normalized below.
_SIMPLE_LEGACY_WORKFLOWS = frozenset({"singlepoint", "optimize", "frequency"})


def _normalize_legacy_simple_method(req: Any) -> None:
    """Normalize the legacy simple-workflow payload before any digest.

    Old clients (and the MCP submit fixtures) send a flat
    ``method = {method, charge, multiplicity}`` block plus a top-level
    ``input.xyz_text``.  The runner consumes ``levels``, ``input.charge``/
    ``multiplicity`` and ``input.source``; a contract that merely *accepts* the
    legacy keys would execute defaults and silently ignore the request.  The
    conversion here is an equivalent mapping (direct method name -> the level's
    ``functional``; charge/multiplicity -> the input envelope; xyz_text ->
    source), applied before the plan digest so a preflight fingerprint and the
    submit see the same plan.
    """
    workflow = str(getattr(req, "workflow", "") or "")
    if workflow not in _SIMPLE_LEGACY_WORKFLOWS:
        return
    method = getattr(req, "method", None)
    inp = getattr(req, "input", None)
    if not isinstance(method, dict) or not isinstance(inp, dict):
        # malformed containers are the contract's business, not this conversion
        return
    # NOTE: the input block is never rewritten here for structure (that would
    # change the plan digest a preflight fingerprint was computed on);
    # xyz_text is instead read by the scheduler's shared materializer.
    for key in ("charge", "multiplicity"):
        if key not in method:
            continue
        value = method.pop(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _command_error(
                422,
                "input_invalid",
                f"method.{key} must be an integer",
                params={"field": f"method.{key}", "rule": "invalid_type"},
            )
        if key in inp and inp[key] != value:
            raise _command_error(
                422,
                "input_invalid",
                f"method.{key} conflicts with input.{key}",
                params={"field": f"method.{key}", "rule": f"conflicting_{key}"},
            )
        inp[key] = value
    direct = method.pop("method", None)
    if direct is not None:
        if not isinstance(direct, str) or not direct.strip():
            raise _command_error(
                422,
                "input_invalid",
                "method.method must be a non-empty string",
                params={"field": "method.method", "rule": "invalid_type"},
            )
        levels = method.get("levels")
        if levels is None:
            levels = {}
        if not isinstance(levels, dict):
            raise _command_error(
                422,
                "input_invalid",
                "method.levels must be an object",
                params={"field": "method.levels", "rule": "invalid_type"},
            )
        from acp.catalog import METHOD_SCHEMAS, get_workflow_by_id

        workflow_row = get_workflow_by_id(workflow) or {}
        schema = METHOD_SCHEMAS.get(str(workflow_row.get("method_schema_id") or "")) or {}
        level_ids = [
            str(level.get("level_id"))
            for level in schema.get("method_levels", [])
            if level.get("level_id")
        ]
        target = level_ids[0] if level_ids else None
        if target is None:
            raise _command_error(
                422,
                "input_invalid",
                f"{workflow} has no level id to receive method.method",
                params={"field": "method.method", "rule": "unsupported_combination"},
            )
        if target is not None:
            level = levels.get(target)
            if level is None:
                level = {}
            if not isinstance(level, dict):
                raise _command_error(
                    422,
                    "input_invalid",
                    f"method.levels.{target} must be an object",
                    params={"field": f"method.levels.{target}", "rule": "invalid_type"},
                )
            existing = level.get("functional")
            if existing is not None and str(existing) != direct.strip():
                # two consumers would pick different methods; refuse the ambiguity
                raise _command_error(
                    422,
                    "input_invalid",
                    "method.method conflicts with levels."
                    f"{target}.functional ({direct.strip()!r} vs {existing!r})",
                    params={
                        "field": "method.method",
                        "rule": "conflicting_method",
                        "level": target,
                    },
                )
            level["functional"] = direct.strip()
            levels[target] = level
        method["levels"] = levels
    req.method = method
    req.input = inp


def _require_job_scope_access(
    request: Request,
    job_id: str,
    *,
    action: str = _AccessAction.READ,
    resource_id: str = "",
    require_exists: bool = True,
) -> JobRecord | None:
    """Authorize a job-scoped control/prepare/read entry.

    A job from another project is reported with the same not-found shape as an
    unknown job id so task existence never leaks across projects.  Control
    entries pass ``require_exists=False`` so the command use case keeps owning
    its own not-found response (and its existing tests stay intact).
    """
    record = _manager(request).get(job_id)
    if record is None:
        if require_exists:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        return None
    decision = _server_identity(request).authorize_job_project(
        record.project_id or record.spec.project_id,
        action=action,
        job_id=job_id,
        resource_id=resource_id,
    )
    if not decision.allowed:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return record


def _scoped_read_project(request: Request, requested: str | None) -> str | None:
    """Narrow a read ``project_id`` hint to the verified credential project.

    In the default local-single mode this returns *requested* unchanged.  In
    API-key mode a key scoped to one project can never read another project's
    listing: a different hint is a not-found (same shape as an unknown project).
    """
    from acp_services.workspace.http_identity import scoped_project

    resolved = getattr(request.state, "request_identity", None)
    try:
        return scoped_project(resolved, requested)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


def _require_project_scope(request: Request, project_id: str) -> None:
    """Deny a project-scoped entry outside the credential's verified project."""
    resolved = getattr(request.state, "request_identity", None)
    if resolved is None or resolved.verified_project is None:
        return
    if str(project_id) != resolved.verified_project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


def _require_verified_project(request: Request, project_id: str | None) -> None:
    """Reject a project that contradicts the verified server identity.

    ``_scoped_read_project`` reads ``request.state.request_identity``, which the
    local-single middleware never sets.  Project-scoped routes therefore need an
    explicit comparison against ``_server_identity(request).verified_project``.
    """
    if project_id is None:
        return
    verified = getattr(_server_identity(request), "verified_project", None)
    if verified is not None and str(project_id) != str(verified):
        raise HTTPException(status_code=404, detail="Project not found")


def _admission_http_error(decision: Any) -> HTTPException:
    """Map an admission refusal to the stable structured error contract."""
    reason = str(getattr(decision, "reason_code", "") or "admission_denied")
    status_code = 429 if reason.startswith(("exceeds_", "capacity_")) else 403
    return HTTPException(
        status_code=status_code,
        detail={
            "code": reason if reason.startswith("budget_") else "admission_denied",
            "message": str(getattr(decision, "detail", "") or "resource admission denied"),
            "params": {
                "reason_code": reason,
                "mode": getattr(decision, "mode", None),
                "enforced": bool(getattr(decision, "enforced", False)),
                "exceeded": [dict(item) for item in getattr(decision, "exceeded", ()) or ()],
            },
            "retryable": False,
        },
    )


def _apply_auto_tag_rules_on_submit(manager: JobManager, record: Any) -> None:
    """Apply project auto-tag rules after a task is created."""
    from acp.scheduler.auto_tags import apply_auto_tag_rules

    project_id = getattr(record, "project_id", None)
    if not project_id:
        return
    project = manager.projects.get_project(project_id)
    if project is None:
        return
    settings = project.get("settings", {})
    rules = settings.get("auto_tag_rules")
    if not rules or not isinstance(rules, list):
        return
    task_row = manager.tasks.get(record.id)
    if task_row is None:
        return
    matched = apply_auto_tag_rules(rules, task_row)
    if not matched:
        return
    existing_tags: list[str] = []
    raw_tags = task_row.get("tags", "[]")
    try:
        existing_tags = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
    except (json.JSONDecodeError, TypeError):
        existing_tags = []
    merged = list(dict.fromkeys(existing_tags + matched))
    if merged != existing_tags:
        manager.tasks.update_display_fields(record.id, tags=merged)


def _is_remote_job(record: Any) -> bool:
    """True when *record* carries remote execution metadata."""
    result = record.result or {}
    return bool(result.get("node") and result.get("remote_dir"))


def _remote_structure_cache(request: Request) -> Any:
    """Return the manager-owned RemoteStructureCache singleton."""
    return _manager(request).structure_cache


def _structure_viewer_root(
    request: Request,
    record: JobRecord,
    *,
    fetch_remote_catalog: bool = False,
) -> tuple[Path, bool]:
    """Resolve the task root used to project a structure-viewer catalog.

    Local/synchronized jobs use their normal work directory.  Pure-remote
    jobs use the controlled remote cache once the small catalog files have
    been fetched.  The boolean reports whether a remote catalog is ready.
    """
    work_dir = Path(record.work_dir)
    if not _is_remote_job(record):
        return work_dir, True

    cache = _remote_structure_cache(request)
    workflow = str(record.spec.workflow or "")
    if cache.catalog_ready(work_dir, workflow):
        return work_dir, True

    cached_root = cache.job_root(record.id)
    if cache.catalog_ready(cached_root, workflow):
        return cached_root, True

    if fetch_remote_catalog:
        fetched_root = cache.fetch_catalog(record, workflow)
        if fetched_root is not None:
            return fetched_root, True

    return work_dir, False


def _job_read_root(request: Request, record: JobRecord) -> Path:
    """Resolve the readable result root for a job projection.

    Local jobs use their work dir.  Pure-remote jobs use the controlled
    ``RemoteStructureCache`` root; for terminal jobs the small catalog files
    are fetched synchronously first (the same recovery the structure-viewer
    ``?fetch=1`` path performs), so energy/PES projections never depend on a
    frontend retry.  Remote geometry stays lazy.
    """
    if not _is_remote_job(record):
        return Path(record.work_dir)
    cache = _remote_structure_cache(request)
    if record.status.is_terminal:
        cache.fetch_catalog(record, str(record.spec.workflow or ""))
    return cache.job_root(record.id)


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


def _compute_progress_state(record: JobRecord) -> str | None:
    """Derive progress_state from record fields."""
    if record.progress is not None:
        return "determinate"
    if record.status in (JobStatus.RUNNING, JobStatus.STARTING, JobStatus.PENDING):
        return "indeterminate"
    return None


def _enrich_from_state_json(record: JobRecord, job_model: V1JobRecordModel) -> V1JobRecordModel:
    """Read state.json from the job's work_dir and merge extended progress fields."""
    return _enrich_job_snapshot(record, job_model, include_event=False)


def _enrich_job_snapshot(
    record: JobRecord,
    job_model: V1JobRecordModel,
    *,
    include_event: bool = False,
) -> V1JobRecordModel:
    """Shared state.json enrichment for both list and detail endpoints.

    Reads state.json via ``find_workflow_state`` and merges progress fields
    (stage_index, stage_total, stage_progress, stage_detail, progress_state)
    plus ``snapshot_version`` (state.json mtime epoch).  When *include_event*
    is ``True``, also reads the last line of ``events.jsonl`` and populates
    ``latest_event``.
    """
    match record.status:
        case JobStatus.COMPLETED:
            completed = True
            job_model = job_model.model_copy(
                update={"progress": 1.0, "progress_state": "determinate"}
            )
        case (
            JobStatus.QUEUED
            | JobStatus.STARTING
            | JobStatus.PENDING
            | JobStatus.RUNNING
            | JobStatus.PAUSED
            | JobStatus.CANCELLING
            | JobStatus.WAITING_REVIEW
            | JobStatus.CANCELLED
            | JobStatus.FAILED
        ):
            completed = False
        case unreachable:
            assert_never(unreachable)
    if not record.work_dir:
        return job_model
    try:
        state_path = find_workflow_state(Path(record.work_dir))
    except OSError:
        return job_model
    if state_path is None:
        return job_model
    try:
        state_stat = state_path.stat()
    except OSError:
        state_stat = None
    state_mtime_ns = state_stat.st_mtime_ns if state_stat is not None else None
    events_path = runtime_file(record.work_dir, "events.jsonl")
    try:
        events_mtime_ns = events_path.stat().st_mtime_ns
    except OSError:
        events_mtime_ns = None
    cache_key = (record.id, state_mtime_ns, events_mtime_ns, include_event)
    with _ENRICHMENT_CACHE_LOCK:
        cached = _ENRICHMENT_CACHE.get(cache_key)
        if cached is not None:
            _ENRICHMENT_CACHE.move_to_end(cache_key)
    if cached is not None:
        data = cached["state"]
        latest = cached.get("latest_event")
    else:
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return job_model
        latest = _read_latest_event(record.work_dir) if include_event else None
    if not isinstance(data, dict):
        return job_model
    updates: dict[str, Any] = {}
    stages = data.get("stages")
    if isinstance(stages, dict):
        updates["stage_order"] = list(stages)
    if not record.status.is_terminal:
        for key in (
            "stage_index",
            "stage_total",
            "stage_progress",
            "stage_detail",
            "progress_state",
        ):
            val = data.get(key)
            if val is not None:
                updates[key] = val
    live = build_live_status(data)
    if live is not None:
        updates["live_status"] = live
    updates["display_method"] = resolve_display_method(
        record.spec.method,
        record.spec.input,
        record.spec.workflow,
        data.get("current_stage") or record.current_stage,
    )
    if state_stat is not None:
        updates["snapshot_version"] = int(state_stat.st_mtime)
    if include_event:
        if latest is not None:
            updates["latest_event"] = latest
    if completed:
        updates["progress"] = 1.0
        updates["progress_state"] = "determinate"
    if updates:
        job_model = job_model.model_copy(update=updates)
    with _ENRICHMENT_CACHE_LOCK:
        _ENRICHMENT_CACHE[cache_key] = {"state": data, "latest_event": latest}
        _ENRICHMENT_CACHE.move_to_end(cache_key)
        if len(_ENRICHMENT_CACHE) > _ENRICHMENT_CACHE_LIMIT:
            _ENRICHMENT_CACHE.popitem(last=False)
    return job_model


def _read_latest_event(work_dir: str) -> str | None:
    """Read the last event from events.jsonl and format as human-readable."""
    events_path = runtime_file(work_dir, "events.jsonl")
    try:
        log = JobEventLog(events_path)
        last = log.read_last()
    except (OSError, json.JSONDecodeError):
        return None
    if last is None:
        return None
    event_type = str(last.get("type") or "")
    stage = last.get("stage")
    if stage:
        return f"{event_type}: {stage}"
    return event_type or None


def _count_lines(path: Path) -> int:
    """Count total lines in a log file."""
    if not path.exists():
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _record_to_v1_model(
    record: JobRecord,
    *,
    project_name: str | None = None,
    study_id: str | None = None,
    study_status: str | None = None,
    include_release_facts: bool = False,
) -> V1JobRecordModel:
    spec = record.spec
    result = record.result if isinstance(record.result, dict) else {}
    release_facts: dict[str, str] = {}
    if include_release_facts:
        from acp_services.entrypoints.status_projection import attempt_release_facts

        release_facts = attempt_release_facts(record.work_dir)
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
            node_tags=spec.node_tags,
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
        node_id=record.node_id or result.get("node") or spec.target_node,
        host=record.host or result.get("host"),
        study_id=study_id,
        study_status=study_status,
        result=record.result,
        progress_state=_compute_progress_state(record),
        # D03: the v1 projection must carry the authoritative execution
        # identity, never schema defaults.  ``name_revision`` stays a separate
        # organization field and is merged later by ``_enrich_v1_with_names``.
        attempt=record.attempt,
        revision=record.revision,
        release_id=release_facts.get("release_id"),
        release_digest=release_facts.get("release_digest"),
        plan_digest=release_facts.get("plan_digest"),
        submission_id=release_facts.get("submission_id"),
    )


def _enrich_v1_with_names(
    model: V1JobRecordModel,
    name_projection: dict[str, Any] | None,
) -> V1JobRecordModel:
    """Merge task name projection fields onto a V1 job model."""
    if not name_projection:
        return model
    return model.model_copy(
        update={
            "custom_name": name_projection.get("custom_name"),
            "resolved_name": name_projection.get("resolved_name", ""),
            "default_name": name_projection.get("default_name", ""),
            "name_revision": name_projection.get("name_revision", 0),
            "name_updated_at": name_projection.get("name_updated_at"),
        }
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
def get_backends(request: Request) -> BackendsResponse:
    return legacy_get_backends(request)


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
    resolved = getattr(request.state, "request_identity", None)
    verified = None if resolved is None else resolved.verified_project
    projects = manager.projects.list_projects()
    if verified is not None:
        projects = [project for project in projects if str(project.get("project_id")) == verified]
    return ProjectListResponse(projects=[_project_to_model(project) for project in projects])


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
    _require_project_scope(request, project_id)
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
    _require_project_scope(request, project_id)
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
    _require_project_scope(request, project_id)
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
    _require_project_scope(request, project_id)
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


def _baseline_bond_scan_protocol(record: JobRecord) -> dict[str, Any] | None:
    """Return the historical protocol dict of *record* for edit context."""
    inp = record.spec.input or {}
    protocol = inp.get("protocol")
    if isinstance(protocol, dict):
        return protocol
    scan_request = inp.get("scan_request")
    if isinstance(scan_request, dict):
        protocol = scan_request.get("protocol")
        if isinstance(protocol, dict):
            return protocol
    return None


def _resolve_bond_scan_protocol_levels(
    protocol: dict[str, Any],
    *,
    entry: Any,
    baseline_protocol: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """API entry of the shared Q7 protocol-level resolver (T14).

    Delegates to :func:`acp.calculations.pes.scan.resolve_scan_protocol_levels`
    (the same resolver the runner path uses) and rejects with 422 when the
    layered resolution reports errors.  Migration warnings are collectable —
    the caller persists them under ``scan_request["level_warnings"]``.
    """
    from acp.calculations.pes.scan import resolve_scan_protocol_levels

    out, warnings_out, errors = resolve_scan_protocol_levels(
        protocol, context=entry, baseline_protocol=baseline_protocol
    )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return out, warnings_out


def _prepare_bond_scan_input(
    inp: dict[str, Any],
    manager: Any,
    *,
    entry: Any = "strict",
    baseline_protocol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate + pin a PESsearch ``mode=bond_length_scan`` job input (§7/§11).

    Stores the full scan request under ``input["scan_request"]`` so the
    scheduler runner can forward it verbatim; resolves/pins a task-artifact
    source manifest into ``input["from"]`` (and the source's artifact_path)
    so handoff copying works for both local and remote execution.

    The entry context (``entry``) is EXPLICIT (plan Scope entry-behavior
    contract): ``"strict"`` for new submissions (API / wizard / CLI),
    ``"edit"`` + ``baseline_protocol`` for edit-recalculate, where unchanged
    level fields migrate with warnings and user-changed fields are strict.
    Migration warnings are stored under
    ``scan_request["level_warnings"]`` (collectable — surfaced by T14/T24).
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
    coordinates = inp.get("coordinates")
    selection = inp.get("selection")
    protocol = inp.get("protocol")
    if not isinstance(coordinate, dict) or not coordinate.get("atoms"):
        # coordinates-only submissions are allowed (round-5); the shared
        # normalization already verified coordinate == coordinates[0] when both
        # are present, so the primary is simply coordinates[0].
        if (
            isinstance(coordinates, list)
            and coordinates
            and isinstance(coordinates[0], dict)
            and coordinates[0].get("atoms")
        ):
            coordinate = coordinates[0]
        else:
            raise HTTPException(
                status_code=422,
                detail="bond_length_scan requires coordinate (or coordinates) with atoms",
            )
    if not isinstance(protocol, dict):
        scan_request = inp.get("scan_request")
        inherited = scan_request.get("protocol") if isinstance(scan_request, dict) else None
        protocol = inherited if isinstance(inherited, dict) else {}
    protocol, level_warnings = _resolve_bond_scan_protocol_levels(
        protocol, entry=entry, baseline_protocol=baseline_protocol
    )
    if coordinates is not None and (
        not isinstance(coordinates, list)
        or not coordinates
        or not all(isinstance(item, dict) and item.get("atoms") for item in coordinates)
    ):
        raise HTTPException(
            status_code=422,
            detail="coordinates must be a non-empty list of coordinate objects with atoms",
        )
    if selection is not None and not isinstance(selection, dict):
        raise HTTPException(status_code=422, detail="selection must be an object")

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
    if level_warnings:
        prepared["scan_request"]["level_warnings"] = level_warnings
    return prepared


def _scan_request_level_warnings(inp: dict[str, Any] | None) -> list[str]:
    """Collect the migration warnings ``_prepare_bond_scan_input`` stored."""
    scan_request = (inp or {}).get("scan_request")
    if not isinstance(scan_request, dict):
        return []
    return [str(message) for message in (scan_request.get("level_warnings") or [])]


def _persist_method_warning_event(
    manager: Any,
    source_record: Any,
    edit_result: dict[str, Any],
    spec: JobSpec,
) -> None:
    """Persist one ``method_validation_warning`` event on the target job (T14).

    The job RECORD already carries the warnings (``spec.input.scan_request
    .level_warnings``, persisted with job.json); this adds the events.jsonl
    entry so rerun/direct-CLI consumers that bypass this endpoint still see
    the audit trail.  Best-effort: a failed append never fails the edit.
    """
    warnings = _scan_request_level_warnings(spec.input)
    if not warnings:
        return
    try:
        job_id = str(edit_result.get("job_id") or source_record.id)
        target = manager.get(job_id) or source_record
        events_path = runtime_file(Path(target.work_dir), "events.jsonl")
        JobEventLog(events_path).append(
            "method_validation_warning",
            job_id=job_id,
            source="edit_recalculate",
            warnings=warnings,
        )
    except Exception:
        logger.debug("method_validation_warning event append failed", exc_info=True)


def _validate_batch_cardinality(inp: dict[str, Any]) -> None:
    """Refuse a multi-structure Batch submission before any side effect (PR-08).

    The single_flat layout is one scheduler task per selected structure; the old
    behaviour only surfaced the split after submit.  *actual* is the count after
    source resolution (inline items or a manifest expansion), never the
    pre-expansion request shape.
    """
    actual: int | None = None
    items = inp.get("items")
    if isinstance(items, list) and items:
        actual = len(items)
    else:
        manifest = inp.get("from") or inp.get("manifest_path")
        if not (isinstance(manifest, str) and manifest):
            # The Batch field policy names the manifest source ``from_artifact``.
            # Only an absolute path is resolved here; a relative path is resolved
            # against the source job's work dir by the loader later, so this
            # static check must not guess it (round-6 audit).
            artifact = inp.get("from_artifact")
            if isinstance(artifact, str) and artifact:
                candidate = Path(artifact)
                if candidate.is_absolute() and candidate.name == "result_manifest.json":
                    manifest = str(candidate)
        if not (isinstance(manifest, str) and manifest):
            items_file = inp.get("items_file")
            if isinstance(items_file, str) and items_file:
                try:
                    document = json.loads(Path(items_file).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    document = None
                entries = (
                    document.get("items") or document.get("entries")
                    if isinstance(document, dict)
                    else None
                )
                if isinstance(entries, list):
                    actual = len(entries)
        if isinstance(manifest, str) and manifest:
            manifest_path = Path(manifest)
            if (
                manifest_path.name == "result_manifest.json"
                and manifest_path.parent.name == "RESULT"
            ):
                if not manifest_path.is_file():
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "input_invalid",
                            "message": f"batch source manifest does not exist: {manifest}",
                            "params": {
                                "field": "input.from_artifact",
                                "rule": "source_not_ready",
                            },
                        },
                    )
                root = manifest_path.parent.parent
            else:
                root = manifest_path
            try:
                from acp.calculations.batch.loaders import load_items_from_result_manifest

                actual = len(load_items_from_result_manifest(root))
            except (ValueError, OSError) as exc:
                # An unresolved source (unconfirmed selection / unsettled
                # publication) is a static refusal: preflight must not pass a
                # manifest whose item count it cannot even resolve.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "input_invalid",
                        "message": str(exc),
                        "params": {
                            "field": "input.from_artifact",
                            "rule": "source_not_ready",
                        },
                    },
                ) from exc
    if actual is None or actual == 1:
        return
    if actual == 0:
        # An empty inline/items_file list is not a split; from_job or the
        # normal resolution path decides the structure.  Only a confirmed
        # multi-structure request is refused here.
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "input_invalid",
            "message": (
                "BatchOptimize accepts exactly one selected structure per scheduler "
                "task (single_flat); split the request into one task per structure"
            ),
            "params": {
                "field": "input.items",
                "rule": "layout_limit",
                "limit": 1,
                "received": int(actual),
                "hint": (
                    "submit one BatchOptimize task per selected structure and keep "
                    "the candidate source revision/role on each item"
                ),
            },
        },
    )


def _resolve_batch_structures_input(
    inp: dict[str, Any], request: Request
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Inline ``source_id`` references and capture metadata snapshots.

    Returns ``(resolved_input, snapshots)`` where *snapshots* is a list of
    per-item metadata dicts captured at materialisation time.  Snapshots are
    written to ``events.jsonl`` after job submission — they never feed into
    the ``JobSpec`` or ``input_hash`` computation.
    """
    items = inp.get("items")
    if not isinstance(items, list) or not items:
        return inp, []
    if not any(isinstance(item, dict) and item.get("source_id") for item in items):
        return inp, []
    service = _structure_source_service(request)
    resolved_items: list[Any] = []
    snapshots: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            resolved_items.append(item)
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            resolved_items.append(item)
            continue
        try:
            asset, source_checksum = service.get(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source_ref = item.get("source_ref")
        source_ref = source_ref if isinstance(source_ref, dict) else {}
        declared_source_id = str(source_ref.get("source_id") or "").strip()
        if declared_source_id and declared_source_id != source_id:
            raise HTTPException(status_code=422, detail="批量结构来源 ID 不匹配")
        declared_checksum = str(source_ref.get("checksum") or "").strip()
        if declared_checksum and declared_checksum != source_checksum:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "structure_source_changed",
                    "message": "所选批量结构自加载后已变化，请重新选择并确认几何。",
                    "source_id": source_id,
                    "expected_checksum": declared_checksum,
                    "current_checksum": source_checksum,
                },
            )
        resolved = {
            key: value
            for key, value in item.items()
            if key not in {"source_id", "source_ref", "allow_disabled_use", "disabled_use_reason"}
        }
        resolved["xyz"] = str(asset.get("xyz") or "")
        resolved_items.append(resolved)
        # Capture metadata snapshot (plan §7).
        snapshot: dict[str, Any] = {
            "source_id": source_id,
            "source_uid": "",
            "job_id": "",
            "relative_path": "",
            "custom_name": None,
            "default_name": asset.get("name", ""),
            "resolved_name": asset.get("name", ""),
            "tags": [],
            "role": asset.get("tag", ""),
            "role_evidence": "",
            "candidate_id": asset.get("candidate_id", ""),
            "checksum": source_checksum,
            "input_xyz_hash": hashlib.sha256(resolved["xyz"].encode("utf-8")).hexdigest(),
            "input_snapshot": {
                "charge": resolved.get("charge", asset.get("charge")),
                "multiplicity": resolved.get("multiplicity", asset.get("multiplicity")),
            },
            "metadata_revision": 0,
        }
        try:
            from acp.scheduler.structure_sources import StructureSourceService

            parsed_id, parsed_path = StructureSourceService.parse_source_id(source_id)
            snapshot["job_id"] = parsed_id
            snapshot["relative_path"] = parsed_path
            snapshot["source_ref"] = source_ref
        except (ValueError, ImportError):
            pass
        # Enrich from org store when available.
        try:
            from acp.scheduler.structure_source_store import StructureSourceStore

            manager = _manager(request)
            org_store = StructureSourceStore(manager.store.db_path)
            org_entry = org_store.get_by_legacy_source_id(source_id)
            if org_entry is not None:
                declared_uid = str(source_ref.get("source_uid") or "").strip()
                if declared_uid and declared_uid != str(org_entry.get("source_uid") or ""):
                    raise HTTPException(
                        status_code=409,
                        detail="批量结构候选来源已变化，请重新选择后重试。",
                    )
                for key in ("version_id", "geometry_hash"):
                    declared_value = str(source_ref.get(key) or "").strip()
                    current_value = str(org_entry.get(key) or "").strip()
                    if declared_value and declared_value != current_value:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": "structure_source_changed",
                                "message": "所选批量结构版本已变化，请重新选择后重试。",
                                "source_id": source_id,
                                "field": key,
                            },
                        )
                from acp.scheduler.structure_source_store import CandidateUseConflictError

                allow_disabled = bool(item.get("allow_disabled_use"))
                exception_reason = str(item.get("disabled_use_reason") or "")
                try:
                    org_entry = org_store.validate_candidate_use(
                        org_entry["source_uid"],
                        allow_disabled=allow_disabled,
                        justification=exception_reason,
                    )
                except CandidateUseConflictError as exc:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "candidate_not_active",
                            "source_uid": org_entry["source_uid"],
                            "current": exc.projection,
                        },
                    ) from exc
                snapshot["source_uid"] = org_entry.get("source_uid", "")
                snapshot["candidate_record_id"] = org_entry.get("candidate_record_id", "")
                snapshot["version_id"] = org_entry.get("version_id", "")
                snapshot["geometry_hash"] = org_entry.get("geometry_hash", "")
                snapshot["source_revision"] = org_entry.get("source_revision", "")
                snapshot["usage_status"] = org_entry.get("usage_status", "active")
                snapshot["custom_name"] = org_entry.get("custom_name")
                snapshot["default_name"] = org_entry.get("default_name", "")
                snapshot["resolved_name"] = org_entry.get("resolved_name", "")
                snapshot["tags"] = org_entry.get("tags", [])
                org_role = org_entry.get("role", "")
                if org_role:
                    snapshot["role"] = org_role
                snapshot["role_evidence"] = org_entry.get("role_evidence", "")
                snapshot["metadata_revision"] = org_entry.get("metadata_revision", 0)
                snapshot["disabled_exception"] = bool(
                    org_entry.get("usage_status") == "disabled" and allow_disabled
                )
                snapshot["exception_reason"] = exception_reason
        except HTTPException:
            raise
        except Exception:
            logger.debug("Org-store snapshot enrichment skipped", exc_info=True)
        snapshot["captured_at"] = _utc_now_iso()
        snapshots.append(snapshot)
    resolved_inp = dict(inp)
    resolved_inp["items"] = resolved_items
    return resolved_inp, snapshots


def _xyz_atom_data(xyz_text: str) -> tuple[tuple[str, tuple[float, float, float]], ...]:
    """Return the first XYZ frame's element order and coordinates."""
    from acp.intake import parse_xyz_text

    parsed = parse_xyz_text(xyz_text)
    if not parsed.structures or parsed.errors:
        raise HTTPException(status_code=422, detail="所选结构不是有效的 XYZ 几何")
    lines = str(parsed.structures[0].xyz or "").strip().splitlines()
    try:
        atom_count = int(lines[0].strip())
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="所选结构缺少有效的 XYZ 原子数") from exc
    atom_lines = lines[2 : 2 + atom_count]
    if atom_count <= 0 or len(atom_lines) != atom_count:
        raise HTTPException(status_code=422, detail="所选结构的 XYZ 原子行不完整")
    atoms: list[tuple[str, tuple[float, float, float]]] = []
    for line in atom_lines:
        fields = line.split()
        if len(fields) < 4:
            raise HTTPException(status_code=422, detail="所选结构包含不完整的 XYZ 原子行")
        try:
            coordinates = [float(value) for value in fields[1:4]]
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="所选结构包含无效坐标") from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise HTTPException(status_code=422, detail="所选结构包含非有限坐标")
        atoms.append((fields[0], (coordinates[0], coordinates[1], coordinates[2])))
    return tuple(atoms)


def _same_xyz_geometry(left: str, right: str) -> bool:
    """Compare two XYZ geometries independent of whitespace and formatting."""
    left_atoms = _xyz_atom_data(left)
    right_atoms = _xyz_atom_data(right)
    if len(left_atoms) != len(right_atoms):
        return False
    for (left_symbol, left_xyz), (right_symbol, right_xyz) in zip(
        left_atoms, right_atoms, strict=True
    ):
        if left_symbol != right_symbol or any(
            not math.isclose(a, b, rel_tol=0.0, abs_tol=1e-7)
            for a, b in zip(left_xyz, right_xyz, strict=True)
        ):
            return False
    return True


def _same_xyz_atom_order(left: str, right: str) -> bool:
    """Compare element count and ordering while allowing different coordinates."""
    try:
        left_atoms = _xyz_atom_data(left)
        right_atoms = _xyz_atom_data(right)
    except HTTPException:
        return False
    return bool(left_atoms) and [atom[0] for atom in left_atoms] == [
        atom[0] for atom in right_atoms
    ]


def _validate_structure_electronic_state(inp: dict[str, Any]) -> None:
    """Reject invalid charge/multiplicity before reusing saved geometry."""
    charge = inp.get("charge", 0)
    multiplicity = inp.get("multiplicity", 1)
    if isinstance(charge, bool) or isinstance(multiplicity, bool):
        raise HTTPException(status_code=422, detail="电荷和自旋多重度必须为整数")
    try:
        charge_value = int(charge)
        multiplicity_value = int(multiplicity)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="电荷和自旋多重度必须为整数") from exc
    if isinstance(charge, float) and charge != charge_value:
        raise HTTPException(status_code=422, detail="电荷必须为整数")
    if isinstance(multiplicity, float) and multiplicity != multiplicity_value:
        raise HTTPException(status_code=422, detail="自旋多重度必须为整数")
    if multiplicity_value < 1:
        raise HTTPException(status_code=422, detail="自旋多重度必须至少为 1")


def _snapshot_direct_source_input(
    inp: dict[str, Any],
    request: Request,
    *,
    verify_geometry: bool = False,
    include_unindexed: bool = False,
) -> list[dict[str, Any]]:
    """Validate and snapshot a directly reused structure source."""
    ref = inp.get("source_ref")
    if not isinstance(ref, dict):
        direct_source_id = str(inp.get("source_id") or "").strip()
        if direct_source_id:
            try:
                parsed_job_id, parsed_path = StructureSourceService.parse_source_id(
                    direct_source_id
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            ref = {
                "source_id": direct_source_id,
                "job_id": parsed_job_id,
                "path": parsed_path,
                "source_uid": str(inp.get("source_uid") or ""),
                "checksum": str(inp.get("checksum") or ""),
            }
    if not isinstance(ref, dict):
        return []
    job_id = str(ref.get("job_id") or "").strip()
    rel_path = str(ref.get("path") or "").strip()
    if not job_id or not rel_path:
        return []
    source_id = f"job_{job_id}:{rel_path}"
    declared_source_id = str(ref.get("source_id") or "").strip()
    if declared_source_id and declared_source_id != source_id:
        raise HTTPException(status_code=422, detail="结构来源 ID 与来源任务/路径不匹配")

    source_checksum = str(ref.get("checksum") or "").strip()
    current_checksum = ""
    if verify_geometry:
        _validate_structure_electronic_state(inp)
        try:
            asset, current_checksum = _structure_source_service(request).get(source_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if source_checksum and source_checksum != current_checksum:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "structure_source_changed",
                    "message": "所选保存结构自加载后已变化，请重新选择并确认几何。",
                    "source_id": source_id,
                    "expected_checksum": source_checksum,
                    "current_checksum": current_checksum,
                },
            )
        if str(inp.get("source_type") or "") == "xyz_text":
            submitted_xyz = str(inp.get("source") or inp.get("xyz_text") or "")
            source_xyz = str(asset.get("xyz") or "")
            if not submitted_xyz or not source_xyz:
                raise HTTPException(status_code=422, detail="保存结构缺少可用 XYZ 几何")
            if not _same_xyz_geometry(submitted_xyz, source_xyz):
                raise HTTPException(
                    status_code=422,
                    detail="提交几何与所选保存结构不一致；请重新选择来源结构后提交",
                )

    from acp.scheduler.structure_source_store import (
        CandidateUseConflictError,
        StructureSourceStore,
    )

    manager = _manager(request)
    store = StructureSourceStore(manager.store.db_path)
    projection = store.get_by_legacy_source_id(source_id)
    if projection is None and not include_unindexed:
        return []
    allow_disabled = bool(ref.get("allow_disabled_use"))
    exception_reason = str(ref.get("disabled_use_reason") or "")
    if projection is not None:
        declared_uid = str(ref.get("source_uid") or "").strip()
        if declared_uid and declared_uid != str(projection.get("source_uid") or ""):
            raise HTTPException(
                status_code=409,
                detail="结构候选来源已变化，请重新选择保存结构后重试。",
            )
        for key in ("version_id", "geometry_hash"):
            declared_value = str(ref.get(key) or "").strip()
            current_value = str(projection.get(key) or "").strip()
            if declared_value and declared_value != current_value:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "structure_source_changed",
                        "message": "所选保存结构版本已变化，请重新选择后重试。",
                        "source_id": source_id,
                        "field": key,
                    },
                )
        try:
            projection = store.validate_candidate_use(
                projection["source_uid"],
                allow_disabled=allow_disabled,
                justification=exception_reason,
            )
        except CandidateUseConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "candidate_not_active",
                    "source_uid": projection["source_uid"],
                    "current": exc.projection,
                },
            ) from exc
    source_uid = str((projection or {}).get("source_uid") or ref.get("source_uid") or "")
    input_xyz = str(inp.get("source") or inp.get("xyz_text") or "")
    snapshot = {
        "source_id": source_id,
        "source_uid": source_uid,
        "job_id": job_id,
        "relative_path": rel_path,
        "checksum": source_checksum or current_checksum or None,
        "input_xyz_hash": (
            hashlib.sha256(input_xyz.encode("utf-8")).hexdigest() if input_xyz else None
        ),
        "candidate_record_id": (projection or {}).get("candidate_record_id", ""),
        "version_id": (projection or {}).get("version_id", ""),
        "geometry_hash": (projection or {}).get("geometry_hash", ""),
        "source_revision": (projection or {}).get("source_revision", ""),
        "usage_status": (projection or {}).get("usage_status", "active"),
        "metadata_revision": (projection or {}).get("metadata_revision", 0),
        "disabled_exception": bool(
            projection and projection.get("usage_status") == "disabled" and allow_disabled
        ),
        "exception_reason": exception_reason,
        "input_snapshot": {
            "source_type": inp.get("source_type"),
            "source": inp.get("source"),
            "charge": inp.get("charge"),
            "multiplicity": inp.get("multiplicity"),
            "source_ref": ref,
        },
        "captured_at": _utc_now_iso(),
    }
    return [snapshot]


def _tsmode_error_to_http(exc: Any) -> HTTPException:
    """Map a TsmodeError to an HTTPException with the correct status code."""
    return HTTPException(
        status_code=getattr(exc, "http_status", 422),
        detail={"error": exc.error_code, "detail": exc.detail},
    )


def _prepare_tsmode_input(inp: dict[str, Any], manager: JobManager) -> dict[str, Any]:
    """Validate and rewrite tsmode submission input for scheduler materialization.

    Loads the frequency source bundle, resolves the target mode, and rewrites
    ``inp`` to the ``source_type: tsmode_bundle`` form consumed by
    ``runner._materialize_tsmode_bundle``.
    """
    from acp.calculations.tsmode.contracts import TsmodeError, sha256_file
    from acp.calculations.tsmode.mode_mapping import (
        enforce_launch_gate,
        resolve_target_mode,
    )
    from acp.calculations.tsmode.source import load_bundle_from_files

    source_job_id = inp.get("source_job_id")
    if not isinstance(source_job_id, str) or not source_job_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "frequency_source_incomplete",
                "detail": "source_job_id is required",
            },
        )

    source_record = manager.get(source_job_id)
    if source_record is None:
        raise HTTPException(status_code=404, detail=f"Source job not found: {source_job_id}")
    if not source_record.work_dir:
        raise HTTPException(status_code=404, detail=f"Source job has no work dir: {source_job_id}")

    source_work_dir = Path(source_record.work_dir)
    entry_id = inp.get("entry_id")
    sources = _discover_frequency_sources(source_work_dir, source_job_id)

    if not sources:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "frequency_source_incomplete",
                "detail": f"No frequency sources found in job {source_job_id}",
            },
        )

    matched_source = None
    if isinstance(entry_id, str) and entry_id:
        for src in sources:
            if src.entry_id == entry_id:
                matched_source = src
                break
    elif len(sources) == 1:
        matched_source = sources[0]

    if matched_source is None:
        available = [s.entry_id for s in sources]
        raise HTTPException(
            status_code=422,
            detail={
                "error": "frequency_source_incomplete",
                "detail": (
                    f"Cannot resolve frequency source (entry_id={entry_id!r}); "
                    f"available: {available}"
                ),
            },
        )

    if not matched_source.hessian_available:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "hessian_missing",
                "detail": f"No Hessian file found for source {matched_source.entry_id}",
            },
        )

    out_abs = source_work_dir / matched_source.output_path
    hess_abs = source_work_dir / matched_source.hess_path  # type: ignore[arg-type]

    source_mode_index = inp.get("source_mode_index")
    if isinstance(source_mode_index, bool) or not isinstance(source_mode_index, int):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "target_mode_invalid",
                "detail": (
                    f"source_mode_index must be an int, got {type(source_mode_index).__name__}"
                ),
            },
        )

    try:
        bundle = load_bundle_from_files(
            str(out_abs),
            str(hess_abs),
            charge=None,
            multiplicity=None,
            level=None,
            origin={"kind": "job", "job_id": source_job_id, "entry_id": matched_source.entry_id},
        )
    except TsmodeError as exc:
        raise _tsmode_error_to_http(exc) from exc

    try:
        resolution = resolve_target_mode(bundle, source_mode_index)
    except TsmodeError as exc:
        raise _tsmode_error_to_http(exc) from exc

    try:
        enforce_launch_gate(
            resolution,
            require_verified=not bool(inp.get("allow_unverified_mapping")),
            orca_version=bundle.level.orca_version,
        )
    except TsmodeError as exc:
        raise _tsmode_error_to_http(exc) from exc

    hess_sha = sha256_file(hess_abs)
    out_sha = sha256_file(out_abs)

    resolved: dict[str, Any] = {
        "source_type": "tsmode_bundle",
        "frequency_out": str(out_abs),
        "hess": str(hess_abs),
        "hess_sha256": hess_sha,
        "frequency_out_sha256": out_sha,
        "geometry": None,
        "source_mode_index": source_mode_index,
        "charge": bundle.charge,
        "multiplicity": bundle.multiplicity,
        "level": bundle.level.to_dict(),
        "origin": {
            "kind": "job",
            "job_id": source_job_id,
            "entry_id": matched_source.entry_id,
            "source_mode_index": source_mode_index,
            "target_mode_id": resolution.target_mode_id,
        },
    }

    for key in ("max_iterations", "recalc_hess", "trust_radius", "retry_limit", "final_frequency"):
        if inp.get(key) is not None:
            resolved[key] = inp[key]
    if inp.get("allow_unverified_mapping") is not None:
        resolved["allow_unverified_mapping"] = inp["allow_unverified_mapping"]

    level_overrides = inp.get("level_overrides")
    if isinstance(level_overrides, dict):
        allowed_keys = {"solvent", "solvent_model", "scf"}
        filtered = {k: v for k, v in level_overrides.items() if k in allowed_keys}
        if filtered:
            resolved_level = dict(resolved.get("level") or {})
            resolved_level.update(filtered)
            resolved["level"] = resolved_level

    resolved["_bundle_level"] = bundle.level.to_dict()
    return resolved


def _expand_method_electronic_state(method: dict[str, Any]) -> dict[str, Any]:
    """Expand electronic-state modules in a method payload (§14.1, §14.3).

    Each level's ``electronic_state`` object is validated through the typed
    model + contract, presets are applied, and the fully expanded
    configuration replaces the user payload so the persisted job spec never
    depends on a bare preset id.
    """
    levels = method.get("levels")
    if not isinstance(levels, dict):
        return method
    from pydantic import ValidationError

    from acp.api.v1_schemas import ElectronicStateModuleModel
    from acp.catalog import _normalize_electronic_state_module

    for level in levels.values():
        if not isinstance(level, dict) or "electronic_state" not in level:
            continue
        raw_module = level.get("electronic_state")
        if raw_module in (None, "", {}):
            level.pop("electronic_state", None)
            continue
        try:
            typed = ElectronicStateModuleModel.model_validate(raw_module)
            payload = typed.payload()
        except ValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid electronic_state module: {exc.errors()[0].get('msg', str(exc))}",
            ) from exc
        expanded, errors = _normalize_electronic_state_module(payload)
        if errors:
            raise HTTPException(
                status_code=400,
                detail=f"invalid electronic_state module: {'; '.join(errors)}",
            )
        if expanded is None:
            level.pop("electronic_state", None)
        else:
            level["electronic_state"] = expanded
    return method


_BATCH_KEYWORD_FIELDS: tuple[str, ...] = (
    "opt_convergence",
    "scf_convergence",
    "opt_initial_hessian",
    "minimum_opt_initial_hessian",
    "transition_state_opt_initial_hessian",
    "opt_rescue_policy",
    "scf_strategy",
)


def _canonicalize_batch_keywords(method: dict[str, Any]) -> None:
    """Rewrite BatchOptimize enumerated keyword values to catalog spellings.

    The Workbench submits both a flat mirror (``method[field]``, read by the
    scheduler's ``batchoptimize_method_flags``) and a nested
    ``method["levels"]["batch"]`` copy (read by the batch engine), so both are
    canonicalized in place. Each value is matched case-insensitively against
    ``FIELD_DEFINITIONS[field]["options"]`` and replaced with the catalog
    spelling; ``None``/empty values, fields without options, and unknown
    spellings are left untouched (free-form values are never folded).
    """
    from acp.catalog import FIELD_DEFINITIONS
    from acp.core.keywords import canonical_choice

    targets: list[dict[str, Any]] = [method]
    levels = method.get("levels")
    if isinstance(levels, dict):
        batch = levels.get("batch")
        if isinstance(batch, dict):
            targets.append(batch)
    roles = method.get("batch_roles")
    if isinstance(roles, dict):
        targets.extend(role for role in roles.values() if isinstance(role, dict))
    for field in _BATCH_KEYWORD_FIELDS:
        options = FIELD_DEFINITIONS.get(field, {}).get("options")
        if not options:
            continue
        for target in targets:
            value = target.get(field)
            if value is None or value == "":
                continue
            canonical = canonical_choice(value, options)
            if canonical is not None:
                target[field] = canonical


def _target_validation_detail(exc: Exception) -> dict[str, Any]:
    """Serialize a submission-time target error into the 400 body (D12).

    Delegates to :func:`acp.scheduler.nodes.target_error_detail` so the D12 shape
    has exactly one implementation (M0-06); the application envelope used by the
    submit entry is :func:`acp.scheduler.nodes.target_error_envelope`.
    """
    from acp.scheduler.nodes import target_error_detail

    return target_error_detail(exc)


def _source_job_id_from_input(inp: dict[str, Any]) -> str:
    """Read an explicitly referenced source job id from a stage input."""
    source = inp.get("source")
    if isinstance(source, dict):
        source_job_id = inp.get("source_job_id") or source.get("source_job_id")
    else:
        source_job_id = inp.get("source_job_id")
    return str(source_job_id or "").strip()


def _inherit_stage_execution_fields(
    workflow: str,
    inp: dict[str, Any],
    execution_mode: str | None,
    target_node: str | None,
    manager: Any,
) -> tuple[str | None, str | None]:
    """Inherit a stage job's explicit execution fields from its source job.

    Stage chains (Confsearch → PESsearch → BatchOptimize → irc) stay on
    the parent's explicitly pinned node unless the request overrides it
    (design §3.4, D6 — IRC inherits via ``run_irc_from_artifact``).  A
    request that already pins either field wins.  Only *explicit* source
    spec values are inherited — an auto parent (no ``target_node`` /
    ``execution_mode``) leaves the child auto; its resolved
    ``execution_target`` is not copied to the child spec (there is no
    creation-time affinity channel — recorded design boundary).
    """
    if workflow not in ("PESsearch", "BatchOptimize"):
        return execution_mode, target_node
    if execution_mode is not None or target_node is not None:
        return execution_mode, target_node
    source_job_id = _source_job_id_from_input(inp)
    if not source_job_id:
        return execution_mode, target_node
    source_record = manager.get(source_job_id)
    if source_record is None:
        return execution_mode, target_node
    source_spec = source_record.spec
    inherited_mode = getattr(source_spec, "execution_mode", None)
    inherited_node = getattr(source_spec, "target_node", None)
    if inherited_mode is None and inherited_node is None:
        return execution_mode, target_node
    logger.info(
        "Stage %s inherits source job %s execution fields (execution_mode=%r, target_node=%r)",
        workflow,
        source_job_id,
        inherited_mode,
        inherited_node,
    )
    return inherited_mode, inherited_node


def _resolve_irc_source_reference(inp: dict[str, Any], manager: Any) -> dict[str, Any]:
    """Map a selected task-result source to its formal manifest product."""
    from acp.calculations.irc.source import load_ts_result_manifest

    if inp.get("source_job_id") and inp.get("source_product_id"):
        return inp
    ref = inp.get("source_ref")
    source_id = str(
        inp.get("source_id") or (ref.get("source_id") if isinstance(ref, dict) else "") or ""
    )
    prefix, separator, path = source_id.partition(":")
    if not separator or not prefix.startswith("job_") or not path.startswith("RESULT/"):
        raise HTTPException(status_code=422, detail="IRC input must be a TS task result")
    source_job_id = prefix.removeprefix("job_")
    record = manager.get(source_job_id)
    if record is None or not record.work_dir:
        raise HTTPException(status_code=404, detail="IRC source job not found")
    try:
        manifest = load_ts_result_manifest(record, getattr(manager, "remote_fetcher", None))
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="IRC source manifest is invalid") from exc
    if manifest is None:
        raise HTTPException(status_code=422, detail="IRC source has no result manifest")
    relative = path.removeprefix("RESULT/")
    product = next((item for item in manifest.products if item.path == relative), None)
    if product is None:
        raise HTTPException(status_code=422, detail="IRC source is not a registered TS product")
    return {
        "source_job_id": source_job_id,
        "source_product_id": product.id,
        "directions": inp.get("directions") or ["forward", "reverse"],
    }


@router.get("/irc/ts-source")
def preview_irc_ts_source(request: Request, source_id: str = Query(...)) -> dict[str, Any]:
    """Return the locked IRC level for a selected formal TS result."""
    from acp.calculations.irc.source import resolve_verified_ts_source

    manager = _manager(request)
    reference = _resolve_irc_source_reference({"source_id": source_id}, manager)
    record = manager.get(reference["source_job_id"])
    try:
        source = resolve_verified_ts_source(
            record,
            reference["source_product_id"],
            getattr(manager, "remote_fetcher", None),
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return source.provenance()


def _validate_simple_method_cli(workflow: str, method: dict[str, Any]) -> None:
    """Fail before queueing when a method field has no simple CLI consumer."""
    if workflow not in {"singlepoint", "optimize", "frequency", "scan"}:
        return
    from acp.catalog import method_levels_to_cli_flags

    try:
        method_levels_to_cli_flags(method.get("levels") or {}, workflow=workflow)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_scan_submission(workflow: str, method: dict[str, Any], inp: dict[str, Any]) -> None:
    """Reject invalid ``scan`` submissions with a synchronous 422.

    ``scan_method_flags`` is the single coordinate contract the local/remote
    runner consumes; validating at the API boundary turns the previous
    201-then-async-failure ("scan job requires at least one coordinate") into
    an immediate client error (NEW-2).  The shared
    ``validate_scan_submission`` boundary validator completes the same
    check for v1 and v2 (indices/finite range/points).  No-op for other
    workflows.
    """
    if workflow not in ("scan", "PESsearch"):
        return
    from acp.calculations.pes.normalization import (
        CoordinateError,
        normalize_pes_coordinates,
    )

    try:
        if workflow == "PESsearch":
            normalize_pes_coordinates(inp or {}, field_prefix="input")
        else:
            validate_scan_submission(workflow, method, inp)
    except CoordinateError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": str(exc),
                "params": exc.params,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "input_invalid", "message": str(exc)},
        ) from exc


def _submit_idempotency_key(request: Request, req: V1JobCreateRequest) -> str | None:
    header_key = (request.headers.get("Idempotency-Key") or "").strip()
    body_key = (req.idempotency_key or "").strip()
    if header_key and body_key and header_key != body_key:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "input_invalid",
                "message": "Idempotency-Key header and body idempotency_key disagree",
                "params": {"field": "idempotency_key"},
            },
        )
    return header_key or body_key or None


@router.post("/jobs", response_model=V1JobCreatedResponse, status_code=201)
def create_job(req: V1JobCreateRequest, request: Request) -> V1JobCreatedResponse:
    manager = _manager(request)
    resolved_project = _require_submit_access(request, req.project_id)
    if resolved_project is not None:
        req.project_id = resolved_project
    if req.workflow not in ALL_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(PUBLIC_WORKFLOWS)}",
        )
    from acp_services.entrypoints.application_params import (
        ApplicationParamError,
        canonical_submit_request,
        normalize_submit,
    )

    # M0-06/G23: one exhaustive field policy decides what this request may carry.
    # An undeclared field or a client-chosen server path is a 422 ``input_invalid``
    # before any resolution/snapshot/dispatch side effect, and the canonical form
    # is the same object MCP and the CLI build (so the request digest and the
    # effective JobSpec cannot drift per entry).
    _normalize_legacy_simple_method(req)
    raw_input = req.input
    request_payload = req.model_dump(exclude_none=True)
    if raw_input is None:
        # An omitted input block keeps the legacy create-now-fail-later shape;
        # the field policy still sees an (empty) object, not a missing field.
        request_payload["input"] = {}
    try:
        canonical_submit_request(request_payload, entry="http")
        normalized_submit = normalize_submit(request_payload)
    except ApplicationParamError as exc:
        # A field-policy violation is 422 (the frozen extra=forbid answer); every
        # other shape violation keeps the long-standing 400 (budget/type/required).
        raise _command_error(
            _field_policy_status(exc), exc.code, exc.message, params=dict(exc.params)
        ) from exc
    # Semantic contract (P1): unknown method keys / illegal enums / missing
    # input are refused here, before classification, approval or dispatch.
    req.input = req.input or {}
    try:
        validate_workflow_submission(
            req.workflow, input=raw_input, method=req.method, resources=req.resources
        )
    except SubmissionContractError as exc:
        raise _command_error(422, "input_invalid", str(exc), params=exc.params()) from exc
    try:
        validate_resource_request(req.resources or {})
    except ValueError as exc:
        raise _command_error(
            422, "input_invalid", str(exc), params={"field": "resources",
            "rule": "invalid_resource"}
        ) from exc
    fingerprint = normalized_submit.get("preflight_fingerprint")
    if fingerprint:
        from acp_services.runtime.canonical_digest import CanonicalDigestError

        try:
            current = _plan_digest(normalized_submit)
        except CanonicalDigestError as exc:
            raise _command_error(
                400,
                "input_invalid",
                str(exc),
                params={"field": "plan", **exc.params()},
            ) from exc
        if fingerprint != current:
            raise _command_error(
                400,
                "preflight_stale",
                "preflight fingerprint does not match the submitted plan",
                params={"field": "preflight_fingerprint", "expected": current},
            )
    # M1-04b: ONE submit classification decides research vs legacy, approval
    # requirement and the recorded submit class.  An agent-scoped credential can
    # never downgrade (omit-all/budget-only/no-proposal are denials), and a legacy
    # local-single/manual submit is explicitly allowed *and recorded*.
    classification = _classify_submit(request, req, defer_approval=True)
    if not classification.allowed:
        raise _deny_submit(classification)
    if classification.research:
        req.project_id = _research_project(manager, req.project_id)
    logger.info(
        "submit classified submit_class=%s profile=%s research=%s approval_required=%s",
        classification.submit_class,
        classification.profile.name,
        classification.research,
        classification.approval_required,
    )
    # B16: an approval bound to the frozen plan_digest is checked BEFORE any
    # resolution/snapshot/dispatch side effect.  A pending/expired/revoked/
    # changed-plan/other-principal approval never reaches the manager.
    approval = _enforce_approval(request, req, classification)
    req.method = _expand_method_electronic_state(req.method)
    _validate_simple_method_cli(req.workflow, req.method)
    _validate_scan_submission(req.workflow, req.method, req.input)
    if req.workflow == "irc":
        req.input = _resolve_irc_source_reference(req.input, manager)
    if req.workflow == "BatchOptimize":
        _canonicalize_batch_keywords(req.method)
    batch_snapshots: list[dict[str, Any]] = _snapshot_direct_source_input(req.input, request)
    if req.workflow == "PESsearch" and str(req.method.get("mode") or "") == "bond_length_scan":
        req.input = _prepare_bond_scan_input(req.input, manager, entry="strict")
    elif req.workflow == "PESsearch":
        req.input = _resolve_stage_artifact_ref(req.workflow, req.input, manager)
    elif req.workflow == "BatchOptimize":
        req.input, resolved_batch_snapshots = _resolve_batch_structures_input(req.input, request)
        batch_snapshots.extend(resolved_batch_snapshots)
        _validate_batch_cardinality(req.input)
    elif req.workflow == "tsmode":
        req.input = _prepare_tsmode_input(req.input, manager)
        bundle_level = req.input.pop("_bundle_level", None)
        if bundle_level and not req.method.get("levels"):
            req.method["levels"] = {
                "tsmode": {
                    "engine": "orca",
                    "method": bundle_level.get("method", ""),
                    "basis": bundle_level.get("basis", ""),
                }
            }
    req.execution_mode, req.target_node = _inherit_stage_execution_fields(
        req.workflow, req.input, req.execution_mode, req.target_node, manager
    )
    task_name = req.task_name or req.workflow
    molecule_name = _resolve_job_molecule_name(req.molecule_name, req.input, manager)
    # M0-06: the effective JobSpec takes every decision field from the canonical
    # request (blank optional strings collapse to the single absent value), so the
    # CLI's equivalent spec is byte-identical instead of differing on "" vs None.
    # The canonical form is recomputed from the *effective* request (post project
    # resolution / stage inheritance), which is also the digest material — the
    # same rule the pre-M0-06 entry hashed.
    canonical = canonical_submit_request(req.model_dump(exclude_none=True), entry="http")
    spec = JobSpec(
        workflow=req.workflow,
        name=canonical["name"],
        input=req.input,
        method=req.method,
        resources=req.resources,
        tags=list(canonical["tags"]),
        node_tags=list(canonical["node_tags"]),
        project_id=req.project_id,
        execution_mode=req.execution_mode or None,
        target_node=req.target_node or None,
        molecule_name=molecule_name,
        task_name=task_name,
        remark=canonical["remark"],
    )
    try:
        validate_execution_request(spec)
        validate_submission_target(spec, registry=manager.registry)
    except (ExecutionTargetError, NoCapableNodeError) as exc:
        # M0-06: one application envelope for every entry — a stable
        # ``input_invalid`` code plus ``params.field``/``params.rule`` carrying the
        # fine-grained scheduler decision.  An unknown remote node is therefore the
        # same rejection at HTTP, MCP and the CLI.
        from acp.scheduler.nodes import target_error_envelope

        envelope = target_error_envelope(exc)
        raise _command_error(
            422,
            str(envelope["code"]),
            str(envelope["message"]),
            params=dict(envelope["params"]),
            retryable=False,
        ) from exc
    idempotency_key = _submit_idempotency_key(request, req)
    scope_project = str(req.project_id or manager.default_project_id)
    identity = _command_identity(request, project_id=scope_project)
    idempotency_context = None
    if idempotency_key:
        from acp_services.runtime.idempotency import (
            ACTION_SUBMIT,
            IdempotencyContext,
        )

        idempotency_context = IdempotencyContext(
            key=idempotency_key,
            scope_id=identity.idempotency_scope(project_id=scope_project),
            action=ACTION_SUBMIT,
            # The digest material is the canonical effective request (M0-06); the
            # row records its frozen rule version and the payload so a pre-M0-06
            # row is verified against its own field table instead of being called
            # tampered.
            request_payload=canonical,
            principal_id=identity.principal_id,
            key_id=identity.key_id,
        )
    budget_claim = _budget_claim_from_approval(approval)
    try:
        if idempotency_context is None:
            record = _commands(request).submit(spec, budget=budget_claim, identity=identity)
        else:
            record = _commands(request).submit(
                spec,
                idempotency=idempotency_context,
                budget=budget_claim,
                identity=identity,
            )
    except (AdmissionRefusedError, IdempotencyReservationDeniedError) as exc:
        raise _admission_http_error(exc.decision) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": str(exc) or "idempotency key conflict",
                "params": {"idempotency_key": idempotency_key},
                "retryable": False,
            },
        ) from exc
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_in_progress",
                "message": str(exc) or "idempotent submit is in progress",
                "params": {"idempotency_key": idempotency_key},
                "retryable": True,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if batch_snapshots:
        try:
            events_path = runtime_file(record.work_dir, "events.jsonl")
            JobEventLog(events_path).append(
                "structure_source_snapshot",
                job_id=record.id,
                snapshots=batch_snapshots,
            )
            from acp.scheduler.structure_source_store import StructureSourceStore

            usage_store = StructureSourceStore(manager.store.db_path)
            for snapshot in batch_snapshots:
                source_uid = str(snapshot.get("source_uid") or "")
                if source_uid:
                    usage_store.record_usage(
                        source_uid,
                        record.id,
                        snapshot,
                        disabled_exception=bool(snapshot.get("disabled_exception")),
                        exception_reason=str(snapshot.get("exception_reason") or ""),
                    )
        except Exception:
            logger.debug("Snapshot event log failed for job %s", record.id, exc_info=True)
    # --- auto-tag rules hook (T11) ---
    try:
        _apply_auto_tag_rules_on_submit(manager, record)
    except Exception:  # noqa: BLE001 — rule errors must never fail submission
        pass
    attempt = int(getattr(record, "attempt", 1) or 1)
    release_id = getattr(record, "release_id", None)
    task_ref: dict[str, Any] = {
        "task_id": record.id,
        "job_id": record.id,
        "attempt": attempt,
    }
    if isinstance(release_id, str) and release_id:
        task_ref["release"] = {
            "release_id": release_id,
            "release_digest": getattr(record, "release_digest", None),
        }
    approval_ref = None
    if approval is not None:
        approval_ref = approval.approval_id
        # The approval and the execution are different facts: recording the
        # TaskRef is evidence only (no approval state/revision change).
        try:
            _approval_service(request).bind_task_ref(
                approval.approval_id,
                task_ref | {"job_id": record.id},
                actor_principal=str(_server_identity(request).principal.principal_id),
            )
        except Exception:  # noqa: BLE001 - a binding write never invalidates a durable submit
            logger.debug(
                "approval TaskRef binding failed for %s", approval.approval_id, exc_info=True
            )
    return V1JobCreatedResponse(
        job_id=record.id,
        status=record.status.value,
        workflow=record.spec.workflow,
        project_id=record.project_id,
        attempt=attempt,
        release_id=release_id if isinstance(release_id, str) else None,
        release_digest=getattr(record, "release_digest", None),
        task_ref=task_ref,
        plan_digest=req.plan_digest,
        proposal_digest=req.proposal_digest,
        approval_ref=approval_ref,
        submit_class=classification.submit_class,
        result="accepted",
        idempotency_key=idempotency_key,
        idempotent_replay=bool(getattr(record, "_idempotent_replay", False)),
        admission=(record.result or {}).get("admission"),
        identity=_server_identity(request).to_dict(),
    )


@router.get("/jobs", response_model=V1JobListResponse)
def list_jobs(
    request: Request,
    status: str | None = Query(default=None),
    workflow: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> V1JobListResponse:
    store = _job_store(request)
    manager = _manager(request)
    project_id = _scoped_read_project(request, project_id)
    if project_id:
        enriched = store.list_enriched(limit=limit, project_id=project_id)
        if status is not None:
            enriched = [item for item in enriched if item["record"].status.value == status]
    else:
        enriched = store.list_enriched(status=status, limit=limit)
    if workflow:
        enriched = [item for item in enriched if item["record"].spec.workflow == workflow]
    jobs = []
    job_ids = [item["record"].id for item in enriched]
    name_map = manager.tasks.get_name_projections_by_job_ids(job_ids)
    for item in enriched:
        model = _record_to_v1_model(
            item["record"],
            project_name=item["project_name"],
            study_id=item["study_id"],
            study_status=item["study_status"],
        )
        model = _enrich_job_snapshot(item["record"], model, include_event=False)
        model = _enrich_v1_with_names(model, name_map.get(item["record"].id))
        jobs.append(model)
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
# PESsearch scan projection (§11): profile, candidates, frames, and the
# legacy /s2 route compatibility surface.
# ---------------------------------------------------------------------------


def _pes_profile_for_job(request: Request, record: JobRecord) -> tuple[Path, dict[str, Any]]:
    """Locate a canonical PES profile, with read-only legacy S2 fallback.

    Remote jobs probe the controlled remote cache first (catalog files are
    fetched on demand for terminal jobs) and fall back to the local work dir.
    """
    job_id = record.id
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")
    roots: list[Path] = [Path(record.work_dir)]
    if _is_remote_job(record):
        roots.insert(0, _job_read_root(request, record))
    elif not roots[0].is_dir():
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    for root in roots:
        canonical_path = root / PES_PROFILE_RELATIVE_PATH
        if canonical_path.is_file():
            try:
                payload = load_pes_profile(
                    canonical_path,
                    source_path=PES_PROFILE_RELATIVE_PATH,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return canonical_path, payload

    for root in roots:
        legacy_path = root / LEGACY_S2_PROFILE_RELATIVE_PATH
        if legacy_path.is_file():
            from acp.compat.legacy.manifests import read_s2_path_manifest

            try:
                payload = read_s2_path_manifest(legacy_path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            payload["_source_path"] = LEGACY_S2_PROFILE_RELATIVE_PATH
            return legacy_path, payload

    raise HTTPException(
        status_code=404,
        detail=(f"No PES profile for job {job_id}; expected {PES_PROFILE_RELATIVE_PATH}"),
    )


@router.get("/jobs/{job_id}/energy-graph", response_model=EnergyGraphResponse)
def get_energy_graph(
    job_id: str,
    request: Request,
    view_type: str = Query(default="auto"),
    view: str | None = Query(default=None),
    item_id: str | None = Query(default=None),
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
    work_dir = _job_read_root(request, record) if record.work_dir else Path(".")
    s2_payload: dict[str, Any] | None = None
    mechanism_report: dict[str, Any] | None = None
    s2_candidates: list[dict[str, Any]] | None = None
    s2_review_state: dict[str, Any] | None = None

    if workflow == "PESsearch" and str(method.get("mode") or "") == "bond_length_scan":
        from acp.calculations.pes.review import load_pes_review
        from acp.compat.legacy.manifests import read_s2_candidate_manifest, read_s2_review

        try:
            _manifest_path, s2_payload = _pes_profile_for_job(request, record)
        except HTTPException as exc:
            # Local running/failed jobs degrade to a live/pending 200 when the
            # final profile simply is not there yet. Remote reads keep their
            # original error (e.g. a catalog fetch failure must surface as
            # the clear 404, never a misleading pending graph).
            if _is_remote_job(record):
                raise
            if exc.status_code != 404 or not str(exc.detail).startswith("No PES profile"):
                raise
            _manifest_path, s2_payload = None, None
        # Manual review outranks the legacy s2 fallback even without a final
        # profile — failed-task annotations must survive refresh.
        manual_review = load_pes_review(work_dir)
        if manual_review is not None:
            selected_rows = []
            for row in manual_review.get("selected") or []:
                if not isinstance(row, dict) or not row.get("candidate_id"):
                    continue
                role_token = str(row.get("role") or "").upper()
                selected_rows.append(
                    {
                        "candidate_id": str(row.get("candidate_id") or ""),
                        "frame_index": int(row.get("frame_index") or 0),
                        "role": "ts" if role_token == "TS" else "intermediate",
                        "active": True,
                        "selection_source": str(row.get("selection_source") or "manual"),
                    }
                )
            s2_candidates = selected_rows or None
            s2_review_state = {
                "status": str(manual_review.get("status") or "confirmed"),
                "decided_at": manual_review.get("confirmed_at"),
                "revision": manual_review.get("revision"),
            }
        elif s2_payload is not None:
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

    job_status = (
        record.status.value if getattr(record.status, "value", None) else str(record.status)
    )
    irc_live_source = ""
    irc_live_error = ""
    if workflow == "irc" and _is_remote_job(record):
        from acp.results.irc_remote_live import refresh_remote_irc

        fetcher = manager.remote_fetcher
        if fetcher is None:
            irc_live_error = "remote_fetch_unavailable"
        else:
            try:
                irc_live_source = refresh_remote_irc(record, work_dir, fetcher)
            except (OSError, ValueError, RemoteFileError, json.JSONDecodeError) as exc:
                logger.warning("Remote IRC live refresh failed for %s: %s", job_id, exc)
                irc_live_error = "remote_fetch_failed"
    graph = build_energy_graph_from_job(
        job_id,
        workflow=workflow,
        method=method,
        work_dir=work_dir,
        s2_payload=s2_payload,
        mechanism_report=mechanism_report,
        s2_candidates=s2_candidates,
        s2_review_state=s2_review_state,
        item_id=item_id,
        view=view,
        job_status=job_status,
    )
    # view_type or explicit view param must match the returned projection;
    # unknown views safely default to the default projection (200, never 500)
    effective_view = view if view else view_type
    if effective_view not in {"", "auto", str(graph.get("view_type") or "")}:
        # Unknown view: fall back to default projection (re-build without view)
        if view:
            graph = build_energy_graph_from_job(
                job_id,
                workflow=workflow,
                method=method,
                work_dir=work_dir,
                s2_payload=s2_payload,
                mechanism_report=mechanism_report,
                s2_candidates=s2_candidates,
                s2_review_state=s2_review_state,
                item_id=item_id,
                view=None,
                job_status=job_status,
            )
    graph_metadata = dict(graph.get("metadata") or {})
    if workflow == "irc":
        if irc_live_source:
            graph_metadata["live_source"] = irc_live_source
        if irc_live_error:
            graph_metadata["live_error"] = irc_live_error
            if not graph.get("nodes"):
                graph_metadata["reason"] = irc_live_error
    graph_metadata.setdefault("job_status", job_status)
    graph["metadata"] = graph_metadata
    return EnergyGraphResponse.model_validate(graph)


@router.get("/jobs/{job_id}/irc/frames/{direction}/{frame_index}/geometry")
def get_irc_frame_geometry(
    job_id: str, direction: str, frame_index: int, request: Request
) -> dict[str, str]:
    """Return exactly one IRC XYZ frame, including during a remote run."""
    from acp.results.frame_candidate_geometry import (
        FrameCandidateError,
        resolve_frame_geometry,
    )

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None or record.spec.workflow != "irc" or not record.work_dir:
        raise HTTPException(status_code=404, detail="IRC job not found")
    valid_index = 0 <= frame_index <= 100000 or direction == "ts" and frame_index == -1
    if direction not in {"forward", "reverse", "ts"} or not valid_index:
        raise HTTPException(status_code=422, detail="Invalid IRC direction or frame index")
    if direction == "ts" and frame_index != -1:
        raise HTTPException(status_code=422, detail="TS frame index must be -1")
    work_dir = Path(record.work_dir)
    if _is_remote_job(record) and direction != "ts":
        from acp.results.irc_remote_live import ensure_remote_irc_point, refresh_remote_irc

        fetcher = manager.remote_fetcher
        if fetcher is not None:
            try:
                refresh_remote_irc(record, work_dir, fetcher)
                ensure_remote_irc_point(record, work_dir, fetcher, direction, frame_index)
            except (OSError, ValueError, RemoteFileError, json.JSONDecodeError):
                # An older job can still resolve a point from its mirrored raw
                # trajectory or multi-frame path file.
                logger.debug("Remote IRC point fetch unavailable", exc_info=True)
    frame_id = "irc_ts" if direction == "ts" else f"irc_{direction}_{frame_index}"
    try:
        xyz = resolve_frame_geometry(
            work_dir,
            view_type="irc",
            frame_index=frame_index,
            workflow="irc",
            frame_id=frame_id,
        )
    except FrameCandidateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"xyz": xyz}


@router.get("/jobs/{job_id}/irc/log-tail")
def get_irc_log_tail(
    job_id: str, request: Request, offset: int = Query(default=0, ge=0)
) -> dict[str, Any]:
    """Read complete new lines from the ORCA output during an IRC run."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None or record.spec.workflow != "irc" or not record.work_dir:
        raise HTTPException(status_code=404, detail="IRC job not found")
    relative = "WORK/07_PATH/ORCA/irc.out"
    limit = 64 * 1024
    try:
        if _is_remote_job(record):
            fetcher = manager.remote_fetcher
            if fetcher is None:
                raise HTTPException(status_code=503, detail="Remote fetch unavailable")
            info = fetcher.file_stat(record, relative)
            start = offset if offset <= info.size else 0
            data = fetcher.read_range(record, relative, start, limit)
        else:
            path = Path(record.work_dir) / relative
            size = path.stat().st_size
            start = offset if offset <= size else 0
            with path.open("rb") as handle:
                handle.seek(start)
                data = handle.read(limit)
    except FileNotFoundError:
        return {"available": False, "lines": [], "next_offset": 0}
    except (OSError, RemoteFileError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return {"available": True, "lines": [], "next_offset": start}
    consumed = data[: last_newline + 1]
    return {
        "available": True,
        "lines": consumed.decode("utf-8", errors="replace").splitlines(),
        "next_offset": start + len(consumed),
    }


@router.get(
    "/jobs/{job_id}/optimization/frame/{frame_index}",
    response_model=OptimizationFrameResponse,
)
def get_optimization_frame(
    job_id: str,
    frame_index: int,
    request: Request,
    item_id: str | None = Query(default=None),
) -> OptimizationFrameResponse:
    """Return the XYZ geometry and metrics for one optimization cycle."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    work_dir = Path(record.work_dir) if record.work_dir else None
    if work_dir is None or not work_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    from acp.results.energy_graph import build_optimization_energy_graph

    graph = build_optimization_energy_graph(job_id, work_dir, item_id=item_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"No optimization trajectory for job {job_id}")
    node = next(
        (
            candidate
            for candidate in graph.get("nodes", [])
            if int(candidate.get("frame_index", -1)) == frame_index
        ),
        None,
    )
    if node is None:
        raise HTTPException(status_code=404, detail=f"Optimization frame {frame_index} not found")

    xyz = ""
    geometry_ref = str(node.get("geometry_ref") or "")
    source_path = work_dir / str(graph.get("source") or "")
    if geometry_ref and source_path.is_file():
        candidate_path = (source_path.parent / geometry_ref).resolve()
        work_root = work_dir.resolve()
        if candidate_path.is_relative_to(work_root) and candidate_path.is_file():
            xyz = candidate_path.read_text(encoding="utf-8", errors="replace")

    metadata = dict(node.get("metadata") or {})
    return OptimizationFrameResponse(
        job_id=job_id,
        item_id=str((graph.get("metadata") or {}).get("item_id") or item_id or ""),
        frame_index=frame_index,
        cycle=int(metadata.get("cycle") or node.get("x") or frame_index + 1),
        xyz=xyz,
        energy_hartree=metadata.get("scf_energy_hartree"),
        relative_energy_kcal_mol=node.get("energy"),
        delta_energy_kcal_mol=metadata.get("delta_energy_kcal_mol"),
        rms_gradient=metadata.get("rms_gradient"),
        max_gradient=metadata.get("max_gradient"),
        rms_displacement=metadata.get("rms_displacement"),
        max_displacement=metadata.get("max_displacement"),
        scf_iterations=metadata.get("scf_iterations"),
    )


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

    # Edit provenance (todo 37): persist the four extended fields as the
    # asset's metadata sidecar and round-trip them in the response.  Legacy
    # posts without them keep an empty metadata dict (backward compatible).
    edit_metadata: dict[str, Any] = {}
    if req.parent_job_id:
        edit_metadata["parent_job_id"] = req.parent_job_id
    if req.parent_entry_id:
        edit_metadata["parent_entry_id"] = req.parent_entry_id
    if req.edit_operations:
        edit_metadata["edit_operations"] = req.edit_operations
    if req.provenance:
        edit_metadata["provenance"] = req.provenance
    if edit_metadata:
        meta_path = storage.upload_dir(project_id, _asset_id) / "metadata.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(edit_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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
        metadata=edit_metadata,
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
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    _manifest_path, payload = _pes_profile_for_job(request, record)
    scan = payload.get("scan") or {}
    frames = [S2FrameModel(**frame) for frame in scan.get("frames") or []]
    return S2ProfileResponse(
        job_id=job_id,
        mode=str(payload.get("mode") or ""),
        status=str(payload.get("status") or ""),
        stationary_point_claimed=bool(payload.get("stationary_point_claimed")),
        coordinate=dict(payload.get("coordinate") or {}),
        coordinates=[item for item in payload.get("coordinates") or [] if isinstance(item, dict)],
        selection=dict(payload.get("selection") or {}),
        protocol=dict(payload.get("protocol") or {}),
        scan={key: value for key, value in scan.items() if key != "frames"},
        energy_profile=dict(payload.get("energy_profile") or {}),
        frames=frames,
    )


@router.get("/jobs/{job_id}/s2/candidates", response_model=S2CandidatesResponse)
def get_s2_candidates(job_id: str, request: Request) -> S2CandidatesResponse:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    _manifest_path, payload = _pes_profile_for_job(request, record)
    return S2CandidatesResponse(
        job_id=job_id,
        mode=str(payload.get("mode") or ""),
        status=str(payload.get("status") or ""),
        stationary_point_claimed=bool(payload.get("stationary_point_claimed")),
        recommendations={},
        review=dict(payload.get("review") or {}),
    )


@router.get("/jobs/{job_id}/s2/frame/{frame_index}", response_model=S2FrameResponse)
def get_s2_frame(job_id: str, frame_index: int, request: Request) -> S2FrameResponse:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    manifest_path, payload = _pes_profile_for_job(request, record)
    frames = (payload.get("scan") or {}).get("frames") or []
    frame = next(
        (f for f in frames if f.get("index") is not None and int(f.get("index")) == frame_index),
        None,
    )
    if frame is None:
        raise HTTPException(
            status_code=404, detail=f"Frame {frame_index} not found in job {job_id}"
        )
    scan_dir = str((payload.get("scan") or {}).get("scan_dir") or "")
    task_root = manifest_path.parent.parent.parent
    geometry_path = str(frame.get("geometry_path") or "")
    relative_geometry = posixpath.join(
        scan_dir.replace("\\", "/"), geometry_path.replace("\\", "/")
    )
    xyz_path = resolve_safe(task_root, relative_geometry)
    if xyz_path is None and _is_remote_job(record):
        cache = _remote_structure_cache(request)
        try:
            xyz_path = cache.get_cached(record.id, relative_geometry)
            if xyz_path is None:
                xyz_path = cache.fetch(record, relative_geometry)
        except ValueError:
            xyz_path = None
    xyz = ""
    if xyz_path is not None:
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
        target_coordinates=dict(frame.get("target_coordinates") or {}),
        actual_coordinates=dict(frame.get("actual_coordinates") or {}),
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


# ---------------------------------------------------------------------------
# PES manual review (pes_review_v1): user-confirmed TS/INT selections are the
# authoritative hand-off to BatchOptimize via RESULT/result_manifest.json.
# Terminal non-completed jobs (FAILED/CANCELLED) may review their partial
# scan frames (live snapshot / ORCA ledger / frame dirs) — running jobs
# can never finalize, and unconfirmed partial data never goes downstream.
# ---------------------------------------------------------------------------

_PES_REVIEW_PARTIAL_STATUSES = frozenset({"FAILED", "CANCELLED"})

#: Per-role obligations a published candidate still needs (C2, round-4 plan).
#: Structured and role-scoped: an INT candidate must not receive TS-only
#: requirements.  Status starts at ``not_evaluated`` — the review projection has
#: not evaluated any of these yet, so it must not claim the evidence is missing.
_INITIAL_GUESS_OBLIGATIONS: dict[str, list[dict[str, str]]] = {
    "ts": [
        {
            "id": "opt_freq_verdict",
            "status": "not_evaluated",
            "guide": "workflow.BatchOptimize",
        },
        {
            "id": "single_imaginary_mode",
            "status": "not_evaluated",
            "guide": "topic.stationary_point_verification",
        },
        {
            "id": "irc_connectivity",
            "status": "not_evaluated",
            "guide": "task.verify_reaction_path",
        },
    ],
    "int": [
        {
            "id": "opt_freq_verdict",
            "status": "not_evaluated",
            "guide": "workflow.BatchOptimize",
        },
        {
            "id": "frequency_minimum",
            "status": "not_evaluated",
            "guide": "topic.stationary_point_verification",
        },
    ],
}


def _pes_review_work_dir(request: Request, job_id: str, *, for_write: bool) -> tuple[Path, str]:
    """Resolve (task dir, task status) of a PESsearch job for manual review.

    Remote jobs work on the controlled remote cache (staging root): reads are
    served from it directly and writes (save/restore) are uploaded back to
    the compute node by the caller, so the authoritative artifacts stay in
    the remote ``RESULT/`` tree.

    Raises:
        404: job/work_dir missing, or no reviewable data (neither a canonical
            PES profile nor live scan frames).
        400: job is not a PESsearch job.
        409: job is still active (POST/restore only) — running tasks cannot
            finalize a review.
        410: legacy mechanism task (read-only compatibility).
    """
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if str(record.spec.workflow or "") != "PESsearch":
        raise HTTPException(
            status_code=400,
            detail=f"PES review is only available for PESsearch jobs, got {record.spec.workflow!r}",
        )
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")
    status = record.status.value if getattr(record.status, "value", None) else str(record.status)
    status_key = str(status).strip().upper()
    active_job = status_key not in {"COMPLETED", "FAILED", "CANCELLED"}

    if _is_remote_job(record):
        work_dir = _job_read_root(request, record)
    else:
        work_dir = Path(record.work_dir)
    canonical = work_dir / PES_PROFILE_RELATIVE_PATH
    legacy = work_dir / LEGACY_S2_PROFILE_RELATIVE_PATH
    if not canonical.is_file():
        if legacy.is_file():
            raise HTTPException(
                status_code=410,
                detail="历史 mechanism 任务保持只读，不支持人工确认选点",
            )
        if for_write and active_job:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Job {job_id} is still active (status={status}); "
                    "a review can only be finalized on a terminal task"
                ),
            )
        from acp.results.pes_scan_live import collect_pes_scan_live_frames

        if collect_pes_scan_live_frames(work_dir, include_incomplete=True) is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No reviewable PES scan data for job {job_id}; expected "
                    f"{PES_PROFILE_RELATIVE_PATH} or live scan artifacts"
                ),
            )
        return work_dir, status_key
    if for_write and active_job:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is not completed yet (status={status})",
        )
    return work_dir, status_key


def _pes_frame_rel_paths(work_dir: Path, frame_indexes: set[int]) -> list[str]:
    """Map selected frame indexes to task-root-relative geometry paths."""
    profile_path = work_dir / PES_PROFILE_RELATIVE_PATH
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    frames = profile.get("frames") if isinstance(profile, dict) else None
    if not isinstance(frames, list):
        return []
    scan_dir = str(profile.get("scan_dir") or "").replace("\\", "/")
    rel_paths: list[str] = []
    for index in sorted(frame_indexes):
        if index < 0 or index >= len(frames) or not isinstance(frames[index], dict):
            continue
        geometry = str(frames[index].get("geometry_path") or "").replace("\\", "/")
        if geometry:
            rel_paths.append(posixpath.join(scan_dir, geometry) if scan_dir else geometry)
    return rel_paths


def _remote_review_setup(
    request: Request,
    job_id: str,
    *,
    frame_indexes: set[int] | None = None,
    backup_rel: str | None = None,
) -> tuple[Any, JobRecord] | None:
    """Stage mutable artifacts + frame geometries for a remote review write.

    Remote is authoritative: the review/manifest (and the requested backup)
    are force-refreshed so revision checks run against the compute node's
    current state, and every selected frame geometry is pulled into the
    staging root before validation.  Returns ``(cache, record)`` for remote
    jobs, ``None`` for local ones; raises 502 when staging fails.
    """
    from acp.calculations.pes.review import PES_REVIEW_RELATIVE_PATH

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None or not _is_remote_job(record):
        return None
    cache = _remote_structure_cache(request)
    work_dir = cache.job_root(record.id)
    try:
        if backup_rel is not None:
            backup_path = cache.fetch(record, backup_rel, force=True, raise_errors=True)
            if backup_path is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"review backup not found: {backup_rel}",
                )
            try:
                backup = json.loads(backup_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                backup = {}
            frame_indexes = {
                int(row["frame_index"])
                for row in (backup.get("selected") or [])
                if isinstance(row, dict) and row.get("frame_index") is not None
            }
        cache.fetch(record, PES_REVIEW_RELATIVE_PATH, force=True, raise_errors=True)
        cache.fetch(record, "RESULT/result_manifest.json", force=True, raise_errors=True)
        for rel_path in _pes_frame_rel_paths(work_dir, frame_indexes or set()):
            cache.fetch(record, rel_path, raise_errors=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"审阅输入拉取失败（无法读取计算节点）: {exc}",
        ) from exc
    return cache, record


def _remote_review_write_back(cache: Any, record: JobRecord, payload: dict[str, Any]) -> None:
    """Upload staged review artifacts; the authoritative review file goes last.

    Order: structures -> result manifest -> backup -> ``pes_review.json``.
    A partial failure leaves extra files on the node at worst; re-saving the
    same selection is idempotent, so the caller can simply retry.
    """
    from acp.calculations.pes.review import PES_REVIEW_BACKUP_TEMPLATE, PES_REVIEW_RELATIVE_PATH

    rel_paths: list[str] = []
    for row in payload.get("selected") or []:
        structure_path = str((row or {}).get("structure_path") or "")
        if structure_path:
            rel_paths.append(f"RESULT/{structure_path}")
    rel_paths.append("RESULT/result_manifest.json")
    previous_revision = int(payload.get("revision") or 1) - 1
    if previous_revision >= 1:
        rel_paths.append(
            f"RESULT/pes_search/{PES_REVIEW_BACKUP_TEMPLATE.format(n=previous_revision)}"
        )
    rel_paths.append(PES_REVIEW_RELATIVE_PATH)
    try:
        cache.push_paths(record, rel_paths)
    except Exception as exc:
        # The staged cache now diverges from the node; restore coherence from
        # the authoritative remote state (best-effort) before failing loudly.
        for rel_path in ("RESULT/result_manifest.json", PES_REVIEW_RELATIVE_PATH):
            try:
                cache.fetch(record, rel_path, force=True)
            except Exception:
                logger.debug("Post-failure refresh failed for %s", rel_path, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"审阅写回计算节点失败（远端可能部分更新，重试即可）: {exc}",
        ) from exc


def _stage_remote_review_backups(request: Request, job_id: str) -> None:
    """Best-effort staging of review backups so GET exposes all rounds."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None or not _is_remote_job(record):
        return
    _remote_structure_cache(request).fetch_matching(
        record, "RESULT/pes_search", "pes_review_backup_"
    )


@router.get("/jobs/{job_id}/pes/review/status")
def get_pes_review_status(job_id: str, request: Request) -> dict[str, Any]:
    """Cache-only selection status (round-5 PR-07).

    Never fetches the compute node, never prepares and never writes: a remote
    job with nothing in the cache reports ``not_prepared`` plus the prepare
    endpoint instead of a hidden fetch.  This is the read surface the MCP
    ``acp_get_pes_review`` tool forwards to.
    """
    from acp.calculations.pes.publication import PUBLICATION_INTENT_RELATIVE_PATH
    from acp.calculations.pes.review import load_pes_review, load_pes_review_backups

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if str(record.spec.workflow or "") != "PESsearch":
        raise HTTPException(status_code=400, detail=f"Not a PESsearch job: {job_id}")
    remote = _is_remote_job(record)
    if remote:
        cache = _remote_structure_cache(request)
        work_dir = Path(cache.job_root(record.id))
    else:
        work_dir = Path(record.work_dir)
    payload: dict[str, Any] = {
        "schema_version": "acp2.pes.selection-status.v1",
        "job_id": job_id,
        "workflow": "PESsearch",
        "cache_only": True,
        "remote": remote,
        "capability": {
            "autopublish": _selection_autopublish_enabled(),
            "publish_endpoint": f"/api/v1/jobs/{job_id}/pes/review",
            "prepare_endpoint": f"/api/v1/jobs/{job_id}/prepare",
        },
    }
    review = load_pes_review(work_dir) if work_dir.is_dir() else None
    intent = None
    try:
        store = getattr(request.app.state, "job_manager", None)
        store = getattr(store, "store", None)
        if store is not None and hasattr(store, "load_selection_publication"):
            intent = store.load_selection_publication(job_id)
    except Exception:  # a status read never fails on an optional receipt
        intent = None
    if review is None:
        payload.update(
            {
                "status": "not_prepared" if remote else "pending",
                "reason": (
                    "selection artifacts are not in the local cache; prepare them"
                    if remote
                    else "no confirmed selection yet"
                ),
                "review": {},
                "backups": [],
                "publication": intent,
            }
        )
        return payload
    selected = review.get("selected") or []
    payload.update(
        {
            "status": str(review.get("status") or "confirmed"),
            "revision": int(review.get("revision") or 0),
            "confirmed_at": review.get("confirmed_at"),
            "selector": review.get("selector") or "manual",
            "actor": review.get("actor"),
            "authorization": review.get("authorization"),
            "source": {
                **(review.get("source") or {}),
                "attempt": int(getattr(record, "attempt", 1) or 1),
            },
            "review": review,
            "selected": [
                {
                    "candidate_id": row.get("candidate_id"),
                    "role": row.get("role"),
                    "frame_index": row.get("frame_index"),
                    "structure_path": row.get("structure_path"),
                    "selection_source": row.get("selection_source"),
                }
                for row in selected
                if isinstance(row, dict)
            ],
            "backups": [
                {
                    "n": int(row.get("n") or 0),
                    "confirmed_at": row.get("confirmed_at"),
                    "selected_count": int(row.get("selected_count") or 0),
                }
                for row in load_pes_review_backups(work_dir)
            ],
            "publication": intent,
            "publication_intent_path": f"RESULT/pes_search/{Path(PUBLICATION_INTENT_RELATIVE_PATH).name}",
        }
    )
    return payload


@router.get("/jobs/{job_id}/pes/review", response_model=PesReviewStateResponse)
def get_pes_review(job_id: str, request: Request) -> PesReviewStateResponse:
    """Return the saved manual-review state plus backup rounds (``pending`` when never saved)."""
    work_dir, _task_status = _pes_review_work_dir(request, job_id, for_write=False)
    _stage_remote_review_backups(request, job_id)

    from acp.calculations.pes.review import load_pes_review, load_pes_review_backups

    review = load_pes_review(work_dir)
    backups = [
        PesReviewBackupSummary(
            n=int(row.get("n") or 0),
            confirmed_at=row.get("confirmed_at"),
            note=str(row.get("note") or ""),
            selected_count=int(row.get("selected_count") or 0),
        )
        for row in load_pes_review_backups(work_dir)
    ]
    if review is None:
        return PesReviewStateResponse(job_id=job_id, status="pending", review={}, backups=backups)
    return PesReviewStateResponse(job_id=job_id, status="confirmed", review=review, backups=backups)


def _selection_autopublish_enabled() -> bool:
    """Whether the authorized-agent publication capability is enabled."""
    import os

    raw = str(os.environ.get("ACP_SELECTION_AUTOPUBLISH", "1")).strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _server_source_digests(record: Any, candidates: list[dict[str, Any]]) -> tuple[str, str]:
    """Profile/geometry digests computed from the task's own artifacts (PR-06)."""
    import hashlib

    from acp.calculations.pes.review import _resolve_frame_source

    root = Path(str(getattr(record, "work_dir", "") or ""))
    if not root.is_dir():
        return "", ""
    status_value = str(
        getattr(getattr(record, "status", ""), "value", getattr(record, "status", ""))
    ).upper()
    try:
        frame_source = _resolve_frame_source(root, task_status=status_value)
    except Exception:  # an unreadable source is reported by the publish path
        return "", ""
    profile_digest = str(frame_source.data_sha256 or "")
    geometry_parts: list[str] = []
    for item in candidates:
        try:
            index = int(item.get("frame_index"))
        except (TypeError, ValueError):
            continue
        frame = frame_source.frames_by_index.get(index)
        if not isinstance(frame, Mapping):
            continue
        relative = str(frame.get(frame_source.geometry_key) or "")
        if not relative:
            continue
        geometry_path = root / frame_source.geometry_base / relative
        if geometry_path.is_file():
            geometry_parts.append(hashlib.sha256(geometry_path.read_bytes()).hexdigest())
    geometry_digest = (
        hashlib.sha256("".join(sorted(geometry_parts)).encode()).hexdigest()
        if geometry_parts
        else ""
    )
    return profile_digest, geometry_digest


def _authorize_agent_selection(
    request: Request,
    job_id: str,
    req: Any,
) -> Any:
    """Validate and pre-consume one agent publication; returns the decision.

    The actor/project come from the verified server identity (never the body).
    """
    import hashlib
    import json as _json

    from acp_services.contracts.selection import (
        SelectionPublicationRequest,
        SelectionSourceBinding,
    )
    from acp_services.runtime.selection_authorization import (
        SelectionAuthorizationError,
        SelectionAuthorizationService,
    )

    if not _selection_autopublish_enabled():
        raise HTTPException(
            status_code=403,
            detail="authorized agent publication is disabled on this deployment",
        )
    if not req.authorization_id:
        raise HTTPException(
            status_code=422, detail="agent publication requires authorization_id"
        )
    store = getattr(request.app.state, "research_resources", None)
    if store is None:
        raise HTTPException(
            status_code=503, detail="selection authorization store is not configured"
        )
    identity = _command_identity(request)
    principal_id = str(getattr(identity, "principal_id", "") or "")
    project_id = str(getattr(identity, "project_id", None) or "uncategorized")
    record = _manager(request).get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    attempt = int(getattr(record, "attempt", 1) or 1)
    spec = getattr(record, "spec", None)
    method_block = getattr(spec, "method", None) or {}
    method = str(method_block.get("method") or method_block.get("functional") or "")
    resources = getattr(spec, "resources", None) or {}
    nproc = int(resources.get("nproc") or 0)
    source_binding = dict(req.source_binding or {})
    candidates = tuple(item.model_dump(exclude_none=True) for item in req.candidates)
    requested_profile = str(source_binding.get("profile_digest") or "")
    requested_geometry = str(source_binding.get("geometry_digest") or "")
    server_profile, server_geometry = _server_source_digests(record, list(candidates))
    if requested_profile and server_profile and requested_profile != server_profile:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "stale_source",
                "message": (
                    "the selection source profile changed; re-read the task and "
                    "re-publish against the current attempt"
                ),
                "params": {
                    "field": "source_binding.profile_digest",
                    "rule": "stale_source",
                },
            },
        )
    if requested_geometry and server_geometry and requested_geometry != server_geometry:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "stale_source",
                "message": (
                    "the selected frame geometry changed; re-read the task and "
                    "re-publish against the current attempt"
                ),
                "params": {
                    "field": "source_binding.geometry_digest",
                    "rule": "stale_source",
                },
            },
        )
    selection_request = SelectionPublicationRequest(
        authorization_id=str(req.authorization_id),
        selector="agent",
        actor_principal_id=principal_id,
        project_id=project_id,
        source=SelectionSourceBinding(
            job_id=job_id,
            attempt=attempt,
            # the server-computed digests are authoritative; a client claim was
            # only accepted after it matched (otherwise stale_source above)
            profile_digest=server_profile or requested_profile,
            geometry_digest=server_geometry or requested_geometry,
        ),
        workflow="PESsearch",
        candidates=candidates,
        method=method,
        nproc=nproc,
        idempotency_key=req.idempotency_key,
        expected_revision=req.expected_revision,
    )
    action_digest = hashlib.sha256(
        _json.dumps(
            {
                "job_id": job_id,
                "attempt": attempt,
                "expected_revision": req.expected_revision,
                "candidates": candidates,
                "note": req.note or "",
                "authorization_id": str(req.authorization_id),
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    service = getattr(request.app.state, "selection_authorizations", None)
    if service is None:
        service = SelectionAuthorizationService(store)
        request.app.state.selection_authorizations = service
    try:
        decision = service.validate(
            selection_request,
            latest_attempt=attempt,
            action_digest=action_digest,
        )
        service.consume(selection_request, decision, action_digest=action_digest)
    except SelectionAuthorizationError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": str(exc), "details": exc.details},
        ) from exc
    decision_payload = decision.to_dict()
    decision_payload["action_digest"] = action_digest
    decision_payload["actor"] = principal_id
    return decision_payload


def _publication_intent_store(request: Request, job_id: str) -> Any:
    """The Services-backed publication intent store for the API path."""
    from acp.calculations.pes.publication import StoreIntentStore

    manager = getattr(request.app.state, "job_manager", None)
    if manager is None or getattr(manager, "store", None) is None:
        return None
    return StoreIntentStore(manager.store, job_id)


def _publication_http_error(exc: Exception) -> HTTPException:
    """Map publication-group failures to typed HTTP errors (retry/recovery info)."""
    from acp.calculations.pes.publication import (
        PublicationConflictError,
        PublicationInProgressError,
        PublicationRecoveryError,
    )

    if isinstance(exc, PublicationRecoveryError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "publication_recovering",
                "message": str(exc),
                "publication": exc.intent,
                "completed_stages": list(exc.completed),
            },
        )
    if isinstance(exc, (PublicationConflictError, PublicationInProgressError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _notify_structure_sources_updated(request: Request, job_id: str) -> None:
    """Notify structure source indexer of updated candidates/manifest for job."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    try:
        manager = getattr(request.app.state, "job_manager", None)
        if manager is not None and getattr(manager, "store", None) is not None:
            with manager.store._connect() as conn:
                conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id))
                try:
                    conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (now, job_id))
                except sqlite3.OperationalError:
                    pass
                conn.commit()
    except Exception as exc:
        logger.debug("Failed to touch updated_at for job %s: %s", job_id, exc)

    try:
        from acp.api.v2_structure_sources import _get_stores

        _source_store, indexer = _get_stores(request)
        indexer.refresh_job(job_id)
    except Exception as exc:
        logger.warning("Failed to refresh structure source index for job %s: %s", job_id, exc)


@router.post("/jobs/{job_id}/pes/review/restore", response_model=PesReviewRestoreResponse)
def restore_pes_review_endpoint(
    job_id: str,
    req: PesReviewRestoreRequest,
    request: Request,
) -> PesReviewRestoreResponse:
    """Re-activate a previous review backup; manifest switches to that round."""
    from acp.calculations.pes.review import PES_REVIEW_BACKUP_TEMPLATE

    work_dir, task_status = _pes_review_work_dir(request, job_id, for_write=True)
    remote_ctx = _remote_review_setup(
        request,
        job_id,
        backup_rel=(f"RESULT/pes_search/{PES_REVIEW_BACKUP_TEMPLATE.format(n=int(req.backup))}"),
    )

    from acp.calculations.pes.publication import PublicationConflictError, PublicationInProgressError, PublicationRecoveryError
    from acp.calculations.pes.review import PesReviewError, RevisionConflictError
    from acp.calculations.pes.review import restore_pes_review as restore_pes_review_state

    try:
        payload = restore_pes_review_state(
            work_dir,
            int(req.backup),
            expected_revision=req.expected_revision,
            source_task_status=task_status,
            idempotency_key=req.idempotency_key,
            intent_store=_publication_intent_store(request, job_id),
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PublicationConflictError, PublicationInProgressError, PublicationRecoveryError) as exc:
        raise _publication_http_error(exc) from exc
    except PesReviewError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 422
        raise HTTPException(status_code=status_code, detail=message) from exc

    source_block = payload.get("source") or {}
    if remote_ctx is not None:
        try:
            _remote_review_write_back(remote_ctx[0], remote_ctx[1], payload)
            write_back = "written"
        except HTTPException as exc:
            logger.warning("PES restore write-back failed for %s: %s", job_id, exc.detail)
            publication = dict(payload.get("publication") or {})
            publication["write_back"] = "failed"
            publication["write_back_error"] = str(exc.detail)
            payload["publication"] = publication
            raise HTTPException(
                status_code=502,
                detail=(
                    "审阅写回计算节点失败（远端可能部分更新；发布意图可对账）: "
                    f"{exc.detail}; publication={json.dumps(publication, ensure_ascii=False, default=str)}"
                ),
            ) from exc
    if isinstance(payload.get("publication"), dict):
        payload["publication"].setdefault(
            "write_back", "written" if remote_ctx is not None else "local"
        )

    _notify_structure_sources_updated(request, job_id)

    return PesReviewRestoreResponse(
        job_id=job_id,
        status=str(payload.get("status") or "confirmed"),
        restored_from=int(payload.get("restored_from") or req.backup),
        revision=int(payload.get("revision") or 0),
        selected_count=len(payload.get("selected") or []),
        source_task_status=str(source_block.get("task_status") or ""),
        scan_complete=source_block.get("scan_complete"),
        frame_source=str(source_block.get("frame_source") or ""),
        candidates=[
            PesReviewCandidate(
                candidate_id=str(row.get("candidate_id") or ""),
                role=str(row.get("role") or ""),
                frame_index=int(row.get("frame_index") or 0),
                name=str(row.get("name") or ""),
                structure_path=str(row.get("structure_path") or ""),
            )
            for row in payload.get("selected") or []
        ],
        publication=payload.get("publication"),
    )


@router.post("/selection-authorizations", status_code=201)
def create_selection_authorization(
    req: SelectionAuthorizationCreateRequest, request: Request
) -> Any:
    """Admin-only: mint one bounded authorization an agent may publish with.

    Round-6 P0: ``SelectionAuthorizationService.create`` had no production
    caller, so strict agent validation would have made every MCP publication a
    permanent 403.  The owner and id are server-owned; an agent can never mint
    its own authorization (no self-grant), it only consumes a bounded one.
    """
    import uuid

    from acp_services.contracts.selection import SelectionAuthorization, SelectionLimits
    from acp_services.runtime.selection_authorization import (
        SelectionAuthorizationError,
        SelectionAuthorizationService,
    )

    identity = _command_identity(request)
    is_operator = str(getattr(identity, "subject_kind", "")) == "operator"
    if not is_operator:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "access_denied",
                "message": "only the local operator may create a selection authorization",
                "params": {"field": "authorization", "rule": "admin_required"},
            },
        )
    store = getattr(request.app.state, "research_resources", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="selection authorization store is not configured",
        )
    principal_id = str(getattr(identity, "principal_id", "") or "")
    project_id = str(getattr(identity, "project_id", None) or "uncategorized")
    try:
        limits = SelectionLimits(**{key: int(value) for key, value in req.limits.items()})
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": f"unsupported selection limit field: {exc}",
                "params": {"field": "limits", "rule": "unknown_field"},
            },
        ) from exc
    if limits.max_candidates < 1:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": "a selection authorization needs limits.max_candidates >= 1",
                "params": {
                    "field": "limits.max_candidates",
                    "rule": "positive_required",
                },
            },
        )
    authorization = SelectionAuthorization(
        authorization_id=f"auth-{uuid.uuid4().hex}",
        owner_principal_id=principal_id,
        owner_project_id=project_id,
        policy_revision=req.policy_revision,
        valid_until=req.valid_until,
        actions=tuple(req.actions),
        source_job_id=req.source_job_id,
        allowed_attempts=tuple(req.allowed_attempts),
        workflow=req.workflow,
        roles=tuple(req.roles),
        methods=tuple(req.methods),
        nproc_max=req.nproc_max,
        limits=limits,
    )
    try:
        document = SelectionAuthorizationService(store).create(
            authorization,
            is_admin=True,
            principal_id=principal_id,
            project_id=project_id,
        )
    except SelectionAuthorizationError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": exc.code,
                "message": str(exc),
                "params": dict(getattr(exc, "details", {}) or {}),
            },
        ) from exc
    return {
        "authorization_id": document.get("authorization_id")
        or authorization.authorization_id,
        "authorization": document,
    }


@router.post("/jobs/{job_id}/pes/review", response_model=PesReviewResponse)
def save_pes_review_endpoint(
    job_id: str,
    req: PesReviewRequest,
    request: Request,
) -> PesReviewResponse:
    """Confirm TS/INT selections: materialise RESULT/structures + pes_review.json + manifest."""
    work_dir, task_status = _pes_review_work_dir(request, job_id, for_write=True)
    remote_ctx = _remote_review_setup(
        request,
        job_id,
        frame_indexes={int(item.frame_index) for item in req.candidates},
    )

    from acp.calculations.pes.publication import PublicationConflictError, PublicationInProgressError, PublicationRecoveryError
    from acp.calculations.pes.review import PesReviewError, RevisionConflictError
    from acp.calculations.pes.review import save_pes_review as persist_pes_review

    selector = str(req.selector or "manual")
    if selector not in {"manual", "manual_frame", "agent"}:
        raise HTTPException(status_code=422, detail=f"unsupported selection selector {selector!r}")
    if req.authorization_id and selector != "agent":
        # A supplied authorization must never be silently ignored by the manual
        # path (the round-5 safety gate looked present but did nothing).
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": (
                    "authorization_id only applies to selector=agent; send "
                    "selector=agent for an authorized publication or omit it"
                ),
                "params": {
                    "field": "authorization_id",
                    "rule": "inconsistent_selector",
                },
            },
        )
    if selector == "agent" and req.actor:
        # the actor is server-owned; accepting a body value would be a
        # misleading "forged but ignored" field (PR-06 criterion 2).
        raise HTTPException(
            status_code=422,
            detail={
                "code": "input_invalid",
                "message": "actor is server-owned for agent publications; omit it",
                "params": {"field": "actor", "rule": "server_identity_owned"},
            },
        )
    agent_authorization: dict | None = None
    agent_source_binding: dict | None = None
    agent_actor: str | None = None
    if selector == "agent":
        agent_authorization = _authorize_agent_selection(request, job_id, req)
        agent_source_binding = dict(req.source_binding or {})
        agent_actor = str(agent_authorization.get("actor") or "")
    try:
        payload = persist_pes_review(
            work_dir,
            job_id=job_id,
            candidates=[item.model_dump(exclude_none=True) for item in req.candidates],
            note=req.note or "",
            expected_revision=req.expected_revision,
            source_task_status=task_status,
            idempotency_key=req.idempotency_key,
            intent_store=_publication_intent_store(request, job_id),
            selector=selector,
            actor=agent_actor,
            authorization=agent_authorization,
            source_binding=agent_source_binding,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (PublicationConflictError, PublicationInProgressError, PublicationRecoveryError) as exc:
        raise _publication_http_error(exc) from exc
    except PesReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source_block = payload.get("source") or {}
    if remote_ctx is not None:
        try:
            _remote_review_write_back(remote_ctx[0], remote_ctx[1], payload)
            write_back = "written"
        except HTTPException as exc:
            # The local publication is complete, but the remote copy is not:
            # fail loudly AND expose the publication intent for reconciliation
            # (the new selection must not be advertised as published remotely).
            logger.warning("PES review write-back failed for %s: %s", job_id, exc.detail)
            publication = dict(payload.get("publication") or {})
            publication["write_back"] = "failed"
            publication["write_back_error"] = str(exc.detail)
            payload["publication"] = publication
            raise HTTPException(
                status_code=502,
                detail=(
                    "审阅写回计算节点失败（远端可能部分更新；发布意图可对账）: "
                    f"{exc.detail}; publication={json.dumps(publication, ensure_ascii=False, default=str)}"
                ),
            ) from exc
    if isinstance(payload.get("publication"), dict):
        payload["publication"].setdefault(
            "write_back", "written" if remote_ctx is not None else "local"
        )

    _notify_structure_sources_updated(request, job_id)

    selected_rows = [
        PesReviewCandidate(
            candidate_id=str(row.get("candidate_id") or ""),
            role=str(row.get("role") or ""),
            frame_index=int(row.get("frame_index") or 0),
            name=str(row.get("name") or ""),
            structure_path=str(row.get("structure_path") or ""),
        )
        for row in payload.get("selected") or []
    ]
    from acp.api.v1_schemas import InitialGuessStatus

    source_record = _manager(request).get(job_id)
    attempt = getattr(source_record, "attempt", None) if source_record is not None else None
    guess_status = InitialGuessStatus(
        source={
            "job_id": job_id,
            "task_status": str(source_block.get("task_status") or ""),
            "frame_source": str(source_block.get("frame_source") or ""),
            "attempt": "" if attempt is None else str(attempt),
            "review_revision": str(payload.get("revision") or 0),
        },
        candidates=selected_rows,
        verification=[
            {
                "candidate_id": row.candidate_id,
                "role": row.role,
                "obligations": [
                    dict(obligation)
                    for obligation in _INITIAL_GUESS_OBLIGATIONS.get(
                        row.role.strip().lower(), _INITIAL_GUESS_OBLIGATIONS["int"]
                    )
                ],
            }
            for row in selected_rows
        ],
        next_step={
            "tool": "acp_capabilities",
            "arguments": {"workflow": "BatchOptimize"},
            "reason": "continue with the BatchOptimize opt_freq step for each candidate",
        },
    )
    return PesReviewResponse(
        job_id=job_id,
        status=str(payload.get("status") or "confirmed"),
        revision=int(payload.get("revision") or 0),
        selected_count=len(payload.get("selected") or []),
        note=payload.get("note") or None,
        confirmed_at=payload.get("confirmed_at"),
        source_task_status=str(source_block.get("task_status") or ""),
        scan_complete=source_block.get("scan_complete"),
        frame_source=str(source_block.get("frame_source") or ""),
        candidates=selected_rows,
        initial_guess_status=guess_status,
        publication=payload.get("publication"),
    )


# ---------------------------------------------------------------------------
# TS Mode frequency sources
# ---------------------------------------------------------------------------


def _discover_frequency_sources(work_dir: Path, job_id: str) -> list[FrequencySourceEntryModel]:
    """Walk *work_dir* for ORCA frequency outputs and matching Hessians.

    Probes ``WORK/`` for ``.out`` files containing ``VIBRATIONAL FREQUENCIES``
    and ``RESULT/result_manifest.json`` for ``kind: frequency_modes`` products.
    For each ``.out``, a matching ``.hess`` is located via sibling-stem or
    single-glob fallback (reimplements ``_discover_sibling_hessian`` semantics
    from ``cccp.qc.interfaces.orca``).
    """
    from cccp.qc.interfaces.orca_ts import parse_ts_frequency_map

    entries: list[FrequencySourceEntryModel] = []
    seen: set[str] = set()

    def _add_source(
        out_path: Path,
        *,
        label: str = "",
        item_id: str | None = None,
        entry_id: str = "",
    ) -> None:
        if str(out_path) in seen:
            return
        seen.add(str(out_path))
        out_rel = str(out_path.relative_to(work_dir))
        hess_path = _discover_sibling_hess(out_path)
        hess_rel = str(hess_path.relative_to(work_dir)) if hess_path else None
        imaginary_count = 0
        mode_count = 0
        atom_count = 0
        try:
            text = out_path.read_text(encoding="utf-8", errors="replace")
            freq_map = parse_ts_frequency_map(text)
            mode_count = len(freq_map)
            imaginary_count = sum(1 for f in freq_map.values() if f < 0.0)
            atom_count = _count_atoms_from_output(text)
        except Exception:  # noqa: BLE001
            pass
        eid = entry_id or out_path.stem
        entries.append(
            FrequencySourceEntryModel(
                entry_id=eid,
                item_id=item_id,
                label=label or out_path.stem,
                output_path=out_rel,
                hess_path=hess_rel,
                complete=True,
                hessian_available=hess_path is not None and hess_path.is_file(),
                imaginary_count=imaginary_count,
                atom_count=atom_count,
                mode_count=mode_count,
                origin=FrequencySourceOriginModel(job_id=job_id, item_id=item_id, entry_id=eid),
            )
        )

    # 1) Walk WORK/ for .out files with vibrational frequencies
    work = work_dir / "WORK"
    if work.is_dir():
        for out_file in sorted(p for p in work.rglob("*.out") if not is_archived_attempt_path(p)):
            try:
                head = out_file.read_text(encoding="utf-8", errors="replace")[:8192]
            except OSError:
                continue
            if "VIBRATIONAL FREQUENCIES" in head:
                _add_source(out_file)

    # 2) Walk RESULT/result_manifest.json for frequency_modes products
    manifest_path = work_dir / "RESULT" / "result_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}
        for product in manifest.get("products", []):
            if product.get("kind") != "frequency_modes":
                continue
            prod_path = work_dir / product.get("path", "")
            if not prod_path.is_file():
                continue
            sibling_out = prod_path.with_suffix(".out")
            if not sibling_out.is_file():
                continue
            item_id = product.get("item_id")
            entry_id = product.get("entry_id", sibling_out.stem)
            _add_source(
                sibling_out,
                label=str(product.get("label") or sibling_out.stem),
                item_id=str(item_id) if item_id else None,
                entry_id=str(entry_id),
            )

    return entries


def _discover_sibling_hess(out_path: Path) -> Path | None:
    """Find a ``.hess`` sibling for an ORCA ``.out`` file.

    Same semantics as ``cccp.qc.interfaces.orca._discover_sibling_hessian``:
    exact stem match first, then single-glob fallback.
    """
    parent = out_path.parent
    exact = parent / f"{out_path.stem}.hess"
    if exact.exists():
        return exact
    matches = sorted(parent.glob("*.hess"))
    if len(matches) == 1:
        return matches[0]
    return None


def _count_atoms_from_output(text: str) -> int:
    """Best-effort atom count from ORCA ``CARTESIAN COORDINATES`` section."""
    lines = text.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if "CARTESIAN COORDINATES" in lines[idx]:
            count = 0
            cursor = idx + 2
            while cursor < len(lines):
                stripped = lines[cursor].strip()
                cursor += 1
                if not stripped:
                    continue
                parts = stripped.split()
                if len(parts) >= 4:
                    try:
                        int(parts[0])
                        float(parts[2])
                        count += 1
                        continue
                    except (IndexError, ValueError):
                        pass
                break
            if count > 0:
                return count
    return 0


@router.get(
    "/jobs/{job_id}/frequency-sources",
    response_model=FrequencySourcesResponse,
)
def get_frequency_sources(
    job_id: str,
    request: Request,
) -> FrequencySourcesResponse:
    """Return available frequency sources for a job.

    404 when the job is unknown.  Remote jobs with absent local files return
    200 with ``sources: []`` and a ``pending_fetch`` warning.
    """
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    work_dir = Path(record.work_dir)
    warnings: list[str] = []

    is_remote = _is_remote_job(record)
    if is_remote and not work_dir.is_dir():
        warnings.append("pending_fetch")
        return FrequencySourcesResponse(job_id=job_id, sources=[], warnings=warnings)

    sources = _discover_frequency_sources(work_dir, job_id)
    return FrequencySourcesResponse(job_id=job_id, sources=sources, warnings=warnings)


# ---------------------------------------------------------------------------
# Structure viewer catalog
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}/structure-viewer",
    response_model=StructureViewerPayloadModel,
)
def get_structure_viewer_catalog(
    job_id: str,
    request: Request,
    item_id: str | None = Query(default=None),
    fetch: bool = Query(default=False),
) -> StructureViewerPayloadModel:
    """Return the structure-viewer catalog for a job.

    Resolves the job via the store and builds the payload via
    ``build_structure_viewer_payload``.  ``availability`` is ``ready`` for
    local jobs and ``pending_fetch`` for remote jobs whose primary manifest
    files are absent locally.  ``?fetch=1`` pulls the small catalog metadata
    into ``RemoteStructureCache`` and projects directly from that controlled
    cache; result geometry remains lazy.

    Retired/legacy workflows are served read-only (200 with legacy entries),
    not 410 — the structure viewer DISPLAYS retired jobs.

    Raises:
        404: Unknown job or unknown ``item_id`` (via ``StructureViewerError``).
    """
    from acp.results.structure_viewer import (
        StructureViewerError,
        build_structure_viewer_payload,
    )

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    workflow = str(record.spec.workflow or "")
    job_status = record.status.value
    task_root, remote_catalog_ready = _structure_viewer_root(
        request,
        record,
        # Catalog files are immutable enough to cache only after the job is
        # terminal.  Fetching a progressively-written batch manifest while a
        # remote job is still running would leave a stale partial catalog.
        fetch_remote_catalog=fetch and record.status.is_terminal,
    )

    if workflow == "irc" and _is_remote_job(record) and manager.remote_fetcher is not None:
        from acp.results.irc_remote_live import refresh_remote_irc

        try:
            refresh_remote_irc(record, task_root, manager.remote_fetcher)
        except (OSError, ValueError, RemoteFileError, json.JSONDecodeError):
            logger.debug("Remote IRC structure catalog refresh failed", exc_info=True)

    try:
        from acp.scheduler.input_snapshot import input_xyz_snapshot

        payload = build_structure_viewer_payload(
            task_root,
            job_id=job_id,
            workflow=workflow,
            job_status=job_status,
            item_id=item_id,
            input_xyz=input_xyz_snapshot(record.spec.input),
        )
    except StructureViewerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    body = payload.to_dict()
    warnings_list: list[str] = list(body.get("warnings") or [])

    is_remote = _is_remote_job(record)
    if is_remote and not remote_catalog_ready:
        body["availability"] = "pending_fetch"
        warnings_list.append("pending_fetch")
    else:
        body["availability"] = "ready"

    body["warnings"] = warnings_list
    return StructureViewerPayloadModel.model_validate(body)


def _resolve_geometry_path(work_dir: Path, geometry_ref: str) -> Path | None:
    """Resolve a geometry_ref to an absolute safe path under work_dir.

    geometry_ref patterns from the structure viewer resolvers:
    - ``RESULT/confsearch/conformers/0001.xyz`` (prefixed with base)
    - ``RESULT/structures/item_001__TAG_TS__optimized.xyz``
    - ``WORK/03_OPT/batch/...``
    - ``input.xyz`` (bare filename — probe work_dir/<ref> then work_dir/RESULT/<ref>)
    - ``structures/pes_ts_frame_005.xyz`` (PES review — relative to RESULT/)

    Uses ``resolve_safe`` to guard against path-escape.
    """
    if not geometry_ref:
        return None
    ref = geometry_ref.replace("\\", "/")
    if "/" in ref:
        return resolve_safe(work_dir, ref)
    found = resolve_safe(work_dir, ref)
    if found is not None:
        return found
    return resolve_safe(work_dir, f"RESULT/{ref}")


@router.get(
    "/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry",
    response_class=Response,
)
def get_structure_viewer_geometry(
    job_id: str,
    entry_id: str,
    request: Request,
    fetch: bool = Query(default=False),
) -> Response:
    """Return the XYZ geometry for a structure-viewer entry.

    Resolves the entry via the catalog, guards the path with
    ``resolve_safe``, and returns ``text/plain`` XYZ bytes.  Entries with a
    ``frame_index`` (IRC/scan/trajectory frames) return exactly that frame,
    extracted via ``read_traj_frame_xyz``.

    For remote jobs, when the geometry file is absent locally:
    - Without ``?fetch=1``: returns 409 ``pending_fetch``.
    - With ``?fetch=1``: attempts a synchronous fetch via the
      ``RemoteStructureCache``; 200 on success, 502/504 on failure.

    Raises:
        404: Unknown job, unknown entry, missing geometry_ref, or file missing.
        409: Remote file not yet synced (without fetch param).
    """
    from acp.confsearch.sampling_models import read_traj_frame_xyz
    from acp.results.structure_viewer import (
        StructureViewerError,
        build_structure_viewer_payload,
    )

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    workflow = str(record.spec.workflow or "")
    job_status = record.status.value
    is_remote = _is_remote_job(record)
    task_root, _remote_catalog_ready = _structure_viewer_root(request, record)

    try:
        from acp.scheduler.input_snapshot import input_xyz_snapshot

        payload = build_structure_viewer_payload(
            task_root,
            job_id=job_id,
            workflow=workflow,
            job_status=job_status,
            input_xyz=input_xyz_snapshot(record.spec.input),
        )
    except StructureViewerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = None
    for e in payload.entries:
        if e.id == entry_id:
            entry = e
            break
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Entry not found: {entry_id}")

    geometry_ref = entry.source.geometry_ref
    if not geometry_ref:
        raise HTTPException(
            status_code=404,
            detail=f"Entry {entry_id} has no geometry reference",
        )

    resolved = _resolve_geometry_path(task_root, geometry_ref)
    input_xyz = None
    if resolved is None and geometry_ref == "input.xyz":
        from acp.scheduler.input_snapshot import input_xyz_snapshot

        input_xyz = input_xyz_snapshot(record.spec.input)

    # Remote job: file absent locally → try cache or fetch
    if resolved is None and input_xyz is None and is_remote:
        cache = _remote_structure_cache(request)
        cached = cache.get_cached(job_id, geometry_ref)
        if cached is not None:
            resolved = cached
        elif fetch:
            try:
                fetched = cache.fetch(record, geometry_ref)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to fetch geometry from remote: {exc}",
                ) from exc
            if fetched is not None:
                resolved = fetched
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Geometry not available on remote: {geometry_ref}",
                )
        else:
            raise HTTPException(status_code=409, detail="pending_fetch")

    if resolved is None and input_xyz is None:
        raise HTTPException(
            status_code=404,
            detail=f"Geometry file not found or unsafe: {geometry_ref}",
        )

    if input_xyz is not None:
        xyz_text = input_xyz
    else:
        try:
            xyz_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=404, detail=f"Cannot read geometry file: {exc}"
            ) from exc

    if entry.source.frame_index is not None:
        frame = read_traj_frame_xyz(resolved, entry.source.frame_index)
        if frame is not None:
            xyz_text = frame

    if not xyz_text.endswith("\n"):
        xyz_text += "\n"

    return Response(content=xyz_text, media_type="text/plain; charset=utf-8")


def _try_historical_mode_projection(
    work_dir: Path,
    entry_id_str: str,
    *,
    threshold_cm1: float = 0.0,
    threshold_source: str = "fixed",
) -> StructureViewerVibrationsResponse | None:
    """Read-only fallback: parse ORCA output on-the-fly when normal_modes.json is absent.

    Searches for ORCA output files in the frequency step directory and parses
    normal-mode vectors.  Never writes any file.
    """
    from acp.api.v1_schemas import StructureViewerModeModel
    from acp.results.orca_parser import OrcaOutputParser

    work = work_dir / "WORK"
    if not work.is_dir():
        return None

    candidates: list[Path] = []
    freq_dir = work / "04_FREQ"
    if freq_dir.is_dir():
        candidates.extend(sorted(freq_dir.glob("*.out")))
        candidates.extend(sorted(freq_dir.glob("*.log")))

    if entry_id_str.startswith("batch_"):
        item_id = entry_id_str.removeprefix("batch_")
        for batch_freq in [
            work / item_id / "frequency",
            work / "04_FREQ" / "batch" / item_id / "frequency",
        ]:
            if batch_freq.is_dir():
                candidates.extend(sorted(batch_freq.glob("*.out")))
                candidates.extend(sorted(batch_freq.glob("*.log")))

    parser = OrcaOutputParser()
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
            calc = parser.parse_text(text)
        except (OSError, ValueError):
            continue
        if not calc.mode_vectors:
            continue

        atom_count = 0
        modes: list[StructureViewerModeModel] = []
        for mode_idx in sorted(calc.mode_vectors.keys()):
            freq = calc.mode_frequencies.get(mode_idx, 0.0)
            vectors_tuple = calc.mode_vectors.get(mode_idx, ())
            if not atom_count and vectors_tuple:
                atom_count = len(vectors_tuple)
            ir = calc.mode_ir_intensities.get(mode_idx) if calc.mode_ir_intensities else None
            try:
                mode = StructureViewerModeModel(
                    mode_index=mode_idx,
                    frequency_cm1=freq,
                    imaginary=freq < 0,
                    ir_intensity=ir,
                    vectors=[list(v) for v in vectors_tuple],
                )
                modes.append(mode)
            except (TypeError, ValueError):
                continue

        if modes:
            imaginary_count = sum(1 for m in modes if m.imaginary)
            return StructureViewerVibrationsResponse(
                available=True,
                reason=None,
                threshold_cm1=threshold_cm1,
                threshold_source=threshold_source,
                modes=modes,
                atom_count=atom_count,
                geometry_product_id=None,
                source="historical_projection",
                imaginary_count=imaginary_count,
            )
    return None


@router.get(
    "/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations",
    response_model=StructureViewerVibrationsResponse,
)
def get_structure_viewer_vibrations(
    job_id: str,
    entry_id: str,
    request: Request,
) -> StructureViewerVibrationsResponse:
    """Return vibration data for a structure-viewer entry.

    Probes, in order: the per-item product
    ``RESULT/frequencies/{item_id}__normal_modes.json`` (batch entries),
    the global ``RESULT/frequencies/normal_modes.json``, then the read-only
    historical projection parsing ``WORK/04_FREQ`` ORCA outputs
    (``source="historical_projection"``).

    Never returns 500 — malformed/missing data yields ``available=false``
    with an appropriate ``reason`` (remote unsynced jobs report
    ``pending_fetch`` as the reason; the vibrations endpoint itself stays
    200, unlike geometry which uses 409).

    Raises:
        404: Unknown job or unknown entry.
    """
    from acp.api.v1_schemas import StructureViewerModeModel
    from acp.results.structure_viewer import (
        StructureViewerError,
        build_structure_viewer_payload,
    )

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    workflow = str(record.spec.workflow or "")
    job_status = record.status.value
    task_root, _remote_catalog_ready = _structure_viewer_root(request, record)

    try:
        payload = build_structure_viewer_payload(
            task_root,
            job_id=job_id,
            workflow=workflow,
            job_status=job_status,
        )
    except StructureViewerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    entry = None
    for e in payload.entries:
        if e.id == entry_id:
            entry = e
            break
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Entry not found: {entry_id}")

    # Compatibility response fields; old per-job magnitude cutoffs no longer
    # affect TS evidence or the BatchOptimize/IRC validation gates.
    threshold_val, threshold_src = 0.0, "fixed"

    def _not_available(reason: str) -> StructureViewerVibrationsResponse:
        return StructureViewerVibrationsResponse(
            available=False,
            reason=reason,
            threshold_cm1=threshold_val,
            threshold_source=threshold_src,
            modes=[],
            atom_count=0,
            geometry_product_id=None,
            imaginary_count=None,
        )

    freq_dir = task_root / "RESULT" / "frequencies"
    is_remote = _is_remote_job(record)

    entry_id_str = entry.id
    is_batch_entry = entry_id_str.startswith("batch_")
    item_suffix = entry_id_str.removeprefix("batch_") if is_batch_entry else None

    from acp.results.vibration_projection import find_vibration_source

    vib_source = find_vibration_source(
        task_root,
        item_id=item_suffix,
        is_batch=is_batch_entry,
    )

    if vib_source is None:
        if is_remote and not freq_dir.is_dir():
            return _not_available("pending_fetch")
        projected = _try_historical_mode_projection(
            task_root,
            entry_id_str,
            threshold_cm1=threshold_val,
            threshold_source=threshold_src,
        )
        if projected is not None:
            return projected
        return _not_available("no_normal_modes")

    if vib_source.kind == "historical":
        projected = _try_historical_mode_projection(
            task_root,
            entry_id_str,
            threshold_cm1=threshold_val,
            threshold_source=threshold_src,
        )
        if projected is not None:
            return projected
        return _not_available("no_normal_modes")

    modes_path = vib_source.path

    try:
        raw = modes_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("Malformed normal_modes file: %s", modes_path)
        return _not_available("no_normal_modes")

    if not isinstance(data, dict):
        logger.warning("normal_modes is not a dict: %s", modes_path)
        return _not_available("no_normal_modes")

    schema_version = data.get("schema_version")
    if schema_version != "normal_modes_v1":
        logger.warning(
            "normal_modes schema_version mismatch: expected 'normal_modes_v1', got %r",
            schema_version,
        )
        return _not_available("no_normal_modes")

    modes_raw = data.get("modes")
    if not isinstance(modes_raw, list):
        logger.warning("normal_modes.modes is not a list: %s", modes_path)
        return _not_available("no_normal_modes")

    atom_count = int(data.get("atom_count") or 0)
    geometry_product_id = data.get("geometry_product_id")

    # Server-side fingerprint deep-check is future hardening; the
    # frontend geometryMismatch product-id guard ships today (todo 31).

    modes = []
    for m in modes_raw:
        if not isinstance(m, dict):
            continue
        try:
            vectors_raw = m.get("vectors") or []
            vectors = [
                [float(v) for v in vec]
                for vec in vectors_raw
                if isinstance(vec, list) and len(vec) == 3
            ]
            mode = StructureViewerModeModel(
                mode_index=int(m.get("mode_index", 0)),
                frequency_cm1=float(m.get("frequency_cm1", 0.0)),
                imaginary=bool(m.get("imaginary", False)),
                ir_intensity=(
                    float(m["ir_intensity"]) if m.get("ir_intensity") is not None else None
                ),
                vectors=vectors,
            )
            modes.append(mode)
        except (TypeError, ValueError, KeyError):
            continue

    return StructureViewerVibrationsResponse(
        available=True,
        reason=None,
        threshold_cm1=threshold_val,
        threshold_source=threshold_src,
        modes=modes,
        atom_count=atom_count,
        geometry_product_id=geometry_product_id,
        source="product",
        imaginary_count=sum(1 for m in modes if m.imaginary),
    )


@router.get(
    "/jobs/{job_id}/structure-viewer/overlay",
    response_model=StructureViewerOverlayResponse,
)
def get_structure_viewer_overlay(
    job_id: str,
    request: Request,
    entry_a: str = Query(default=""),
    entry_b: str = Query(default=""),
) -> StructureViewerOverlayResponse:
    """Overlay mapping + optimal RMSD between two structure-viewer entries.

    The mapping is computed server-side only (identity when symbol sequences
    match, else a UNIQUE RDKit MCS mapping; never guessed).  Mapping failures
    return ``ok=false`` with a reason — the endpoint never 500s on them.

    Raises:
        404: Unknown job, or unknown ``entry_a``/``entry_b``.
    """
    from acp.results.structure_viewer import (
        StructureViewerError,
        build_structure_viewer_payload,
        compute_overlay,
    )

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    if not entry_a or not entry_b:
        raise HTTPException(status_code=404, detail="entry_a and entry_b are required")

    task_root, _remote_catalog_ready = _structure_viewer_root(request, record)

    try:
        payload = build_structure_viewer_payload(
            task_root,
            job_id=job_id,
            workflow=str(record.spec.workflow or ""),
            job_status=record.status.value,
        )
    except StructureViewerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _find(entry_id: str) -> Any:
        for item in payload.entries:
            if item.id == entry_id:
                return item
        return None

    entry_obj_a = _find(entry_a)
    entry_obj_b = _find(entry_b)
    if entry_obj_a is None:
        raise HTTPException(status_code=404, detail=f"Entry not found: {entry_a}")
    if entry_obj_b is None:
        raise HTTPException(status_code=404, detail=f"Entry not found: {entry_b}")

    try:
        result = compute_overlay(job_id, task_root, entry_obj_a, entry_obj_b)
    except Exception as exc:  # noqa: BLE001 — mapping failure must not 500
        result = {
            "ok": False,
            "mapping": None,
            "rmsd": None,
            "max_displacement": None,
            "n_mapped": 0,
            "reason": "failed",
        }
        logger.warning("overlay computation failed for %s: %s", job_id, exc)

    max_disp = result.get("max_displacement")
    return StructureViewerOverlayResponse(
        ok=bool(result.get("ok")),
        mapping=result.get("mapping"),
        rmsd=result.get("rmsd"),
        max_displacement=max_disp,
        n_mapped=int(result.get("n_mapped") or 0),
        reason=str(result.get("reason") or "unproven"),
    )


# ---------------------------------------------------------------------------
# Frame-candidate CRUD: save/list/remove energy-viewer frames as tagged
# structures.  Follows the PES review ordering and revision-guard pattern.
# ---------------------------------------------------------------------------


def _frame_candidate_work_dir(
    request: Request, job_id: str, *, require_terminal: bool
) -> tuple[Path, str]:
    """Resolve work_dir + workflow for frame-candidate operations.

    Returns:
        (work_dir, workflow)

    Raises:
        404: job/work_dir missing.
        409: job not in a terminal state yet (POST only).  All terminal
            states (COMPLETED/FAILED/CANCELLED) are accepted so that
            already-materialised trajectory frames stay reusable.
    """
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not record.work_dir:
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")
    work_dir = Path(record.work_dir)
    if require_terminal and not record.status.is_terminal:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} is still active (status={record.status.value})",
        )
    return work_dir, str(record.spec.workflow or "")


def _frame_candidate_publication_entry() -> Any:
    """Production publication entry routing candidate writes through Data."""
    from acp.services_publication_bootstrap import build_publication_data_port

    return build_publication_data_port()


@router.post(
    "/jobs/{job_id}/frame-candidate",
    response_model=V1FrameCandidateResponse,
    status_code=200,
)
def save_frame_candidate_endpoint(
    job_id: str,
    req: V1FrameCandidateRequest,
    request: Request,
) -> V1FrameCandidateResponse:
    """Save an energy-viewer frame as a tagged candidate structure."""
    work_dir, workflow = _frame_candidate_work_dir(request, job_id, require_terminal=True)
    # Publish path: re-verify write authorization before any file is written.
    _require_entry_access(
        request,
        _EntryClass.FRAME_CANDIDATES,
        job_id=job_id,
        action=_AccessAction.WRITE,
        resource_id=req.name or "",
    )

    from acp.results.frame_candidate_geometry import FrameCandidateError
    from acp.results.frame_candidate_store import RevisionConflictError
    from acp.results.frame_candidates import save_frame_candidate

    try:
        entry = save_frame_candidate(
            work_dir,
            job_id=job_id,
            workflow=workflow,
            view_type=req.view_type,
            frame_index=req.frame_index,
            role=req.role,
            name=req.name,
            expected_revision=req.expected_revision,
            item_id=req.item_id,
            frame_id=req.frame_id,
            publication=_frame_candidate_publication_entry(),
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FrameCandidateError as exc:
        message = str(exc)
        # PESsearch jobs get guidance to /pes/review
        if "PESsearch" in message or "/pes/review" in message:
            raise HTTPException(status_code=400, detail=message) from exc
        # Invalid view type or role
        if "unknown view_type" in message or "invalid candidate role" in message:
            raise HTTPException(status_code=400, detail=message) from exc
        # Frame not found / geometry missing
        raise HTTPException(status_code=404, detail=message) from exc

    # Re-read the full list for the response
    from acp.results.frame_candidates import list_frame_candidates

    payload = list_frame_candidates(work_dir)
    candidates = [
        V1FrameCandidateInfo(
            candidate_id=str(c.get("candidate_id", "")),
            view_type=str(c.get("view_type", "")),
            frame_index=int(c.get("frame_index", 0)),
            role=str(c.get("role", "")),
            name=str(c.get("name", "")),
            structure_path=str(c.get("structure_path", "")),
            saved_at=str(c.get("saved_at", "")),
            item_id=c.get("item_id"),
            role_index=int(c.get("role_index") or 0),
            display_label=str(c.get("display_label") or ""),
            created_seq=int(c.get("created_seq") or 0),
        )
        for c in payload.get("candidates", [])
    ]
    saved = next((c for c in candidates if c.candidate_id == entry.get("candidate_id")), None)
    _notify_structure_sources_updated(request, job_id)
    return V1FrameCandidateResponse(
        job_id=job_id,
        revision=int(payload.get("revision", 0)),
        candidate=saved,
        candidates=candidates,
    )


@router.get(
    "/jobs/{job_id}/frame-candidates",
    response_model=V1FrameCandidateListResponse,
)
def list_frame_candidates_endpoint(
    job_id: str,
    request: Request,
) -> V1FrameCandidateListResponse:
    """List all saved frame candidates for a job."""
    work_dir, _workflow = _frame_candidate_work_dir(request, job_id, require_terminal=False)
    _require_entry_access(request, _EntryClass.FRAME_CANDIDATES, job_id=job_id)

    from acp.results.frame_candidates import list_frame_candidates

    payload = list_frame_candidates(work_dir)
    candidates = [
        V1FrameCandidateInfo(
            candidate_id=str(c.get("candidate_id", "")),
            view_type=str(c.get("view_type", "")),
            frame_index=int(c.get("frame_index", 0)),
            role=str(c.get("role", "")),
            name=str(c.get("name", "")),
            structure_path=str(c.get("structure_path", "")),
            saved_at=str(c.get("saved_at", "")),
            item_id=c.get("item_id"),
            role_index=int(c.get("role_index") or 0),
            display_label=str(c.get("display_label") or ""),
            created_seq=int(c.get("created_seq") or 0),
        )
        for c in payload.get("candidates", [])
    ]
    return V1FrameCandidateListResponse(
        job_id=job_id,
        revision=int(payload.get("revision", 0)),
        candidates=candidates,
    )


@router.delete(
    "/jobs/{job_id}/frame-candidate/{candidate_id}",
    response_model=V1FrameCandidateListResponse,
)
def delete_frame_candidate_endpoint(
    job_id: str,
    candidate_id: str,
    request: Request,
    expected_revision: int | None = Query(default=None),
) -> V1FrameCandidateListResponse:
    """Remove a frame candidate (authority + manifest; XYZ kept on disk)."""
    work_dir, _workflow = _frame_candidate_work_dir(request, job_id, require_terminal=False)
    _require_entry_access(
        request,
        _EntryClass.FRAME_CANDIDATES,
        job_id=job_id,
        action=_AccessAction.WRITE,
        resource_id=candidate_id,
    )

    from acp.results.frame_candidate_geometry import FrameCandidateError
    from acp.results.frame_candidate_store import RevisionConflictError
    from acp.results.frame_candidates import remove_frame_candidate

    try:
        payload = remove_frame_candidate(
            work_dir,
            candidate_id,
            expected_revision=expected_revision,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FrameCandidateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    candidates = [
        V1FrameCandidateInfo(
            candidate_id=str(c.get("candidate_id", "")),
            view_type=str(c.get("view_type", "")),
            frame_index=int(c.get("frame_index", 0)),
            role=str(c.get("role", "")),
            name=str(c.get("name", "")),
            structure_path=str(c.get("structure_path", "")),
            saved_at=str(c.get("saved_at", "")),
            item_id=c.get("item_id"),
            role_index=int(c.get("role_index") or 0),
            display_label=str(c.get("display_label") or ""),
            created_seq=int(c.get("created_seq") or 0),
        )
        for c in payload.get("candidates", [])
    ]
    _notify_structure_sources_updated(request, job_id)
    return V1FrameCandidateListResponse(
        job_id=job_id,
        revision=int(payload.get("revision", 0)),
        candidates=candidates,
    )


@router.get(
    "/jobs/{job_id}/sampling/frame/{frame_index}",
    response_model=V1SamplingFrameResponse,
)
def get_sampling_frame(
    job_id: str,
    frame_index: int,
    request: Request,
) -> V1SamplingFrameResponse:
    """Return the XYZ geometry and metadata for one MD sampling frame."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    work_dir = Path(record.work_dir) if record.work_dir else None
    if work_dir is None or not work_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Job has no work dir: {job_id}")

    from acp.confsearch.sampling_models import load_sampling_history, read_traj_frame_xyz

    history = load_sampling_history(work_dir)
    if history is None:
        raise HTTPException(
            status_code=404,
            detail=f"No sampling history for job {job_id}",
        )

    # Find the frame by its trajectory index (not positional)
    frame = None
    frame_pos: int | None = None
    for pos, f in enumerate(history.frames):
        if f.index == frame_index:
            frame, frame_pos = f, pos
            break
    if frame is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sampling frame {frame_index} not found (job has {len(history.frames)} frames)",
        )

    # Read XYZ from the source trajectory
    traj_path = Path(history.source_trajectory)
    if not traj_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Sampling trajectory file is unreadable for job {job_id}",
        )
    xyz = read_traj_frame_xyz(traj_path, frame_index)
    if xyz is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sampling trajectory file is unreadable for job {job_id}",
        )

    # Compute relative energy
    energies = [f.energy_kcal_mol for f in history.frames if f.energy_kcal_mol is not None]
    min_energy = min(energies) if energies else None
    relative = None
    if frame.energy_kcal_mol is not None and min_energy is not None:
        relative = frame.energy_kcal_mol - min_energy

    # Basin id from history — use positional index, not trajectory index
    basin_id = None
    if frame_pos is not None and frame_pos < len(history.basin_ids):
        basin_id = history.basin_ids[frame_pos]

    return V1SamplingFrameResponse(
        job_id=job_id,
        frame_index=frame_index,
        time_ps=frame.time_ps,
        step=frame.step,
        energy_kcal_mol=frame.energy_kcal_mol,
        relative_energy_kcal_mol=relative,
        basin_id=basin_id,
        xyz=xyz,
    )


def _descriptor_read_root(
    request: Request, record: JobRecord
) -> tuple[Path, str | None]:
    """Cache-only descriptor root: a status poll never fetches remote bytes."""
    if not _is_remote_job(record):
        return Path(record.work_dir), None
    cache = _remote_structure_cache(request)
    return cache.job_root(record.id), "remote_cache_only"


def _with_artifact_descriptors(
    model: V1JobRecordModel,
    read_root: Path | None,
    attempt: int,
    unknown_reason: str | None = None,
) -> V1JobRecordModel:
    """Attach the bounded manifest projection from the controlled read root."""
    from acp.api.artifact_descriptors import describe_job_artifacts

    descriptors, truncated = describe_job_artifacts(read_root, attempt=attempt)
    if not descriptors and not truncated and unknown_reason is None:
        return model
    return model.model_copy(
        update={
            "artifacts": descriptors,
            "artifacts_truncated": truncated,
            "artifacts_unknown_reason": unknown_reason,
        }
    )


@router.get("/jobs/{job_id}", response_model=V1JobRecordModel)
def get_job(job_id: str, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    model = _record_to_v1_model(record, include_release_facts=True)
    model = _enrich_job_snapshot(record, model, include_event=False)
    task_row = manager.tasks.get(job_id)
    if task_row is not None:
        model = _enrich_v1_with_names(model, resolve_task_names(task_row))
    read_root, unknown_reason = _descriptor_read_root(request, record)
    return _with_artifact_descriptors(model, read_root, record.attempt, unknown_reason)


@router.get("/jobs/{job_id}/summary", response_model=V1JobRecordModel)
def get_job_summary(job_id: str, request: Request) -> V1JobRecordModel:
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    model = _record_to_v1_model(record, include_release_facts=True)
    model = _enrich_job_snapshot(record, model, include_event=True)
    task_row = manager.tasks.get(job_id)
    if task_row is not None:
        model = _enrich_v1_with_names(model, resolve_task_names(task_row))
    read_root, unknown_reason = _descriptor_read_root(request, record)
    return _with_artifact_descriptors(model, read_root, record.attempt, unknown_reason)


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
        if record.spec.workflow == "BatchOptimize" and state_stages:
            by_name = {task.stage_name: task for task in tasks}
            tasks = [by_name[name] for name in state_stages if name in by_name]
        entries = _overlay_state_on_entries(
            tasks, state_stages, prefer_state=record.spec.workflow == "BatchOptimize"
        )
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
    *,
    prefer_state: bool = False,
) -> list[JobStageEntry]:
    """Build stage entries from stage_tasks rows, overlaying state.json data."""
    entries: list[JobStageEntry] = []
    for task in tasks:
        name = task.stage_name
        status = task.state
        progress: float | None = None
        detail: str | None = task.status_detail
        state_info = state_stages.get(name)
        started_at = task.started_at
        completed_at = task.completed_at
        error = task.stderr_summary
        if isinstance(state_info, dict):
            state_status = str(state_info.get("status") or "")
            state_order = _STAGE_ADVANCE_ORDER.get(state_status, 0)
            db_order = _STAGE_ADVANCE_ORDER.get(status, 0)
            if state_order > db_order or (prefer_state and state_status in _STAGE_ADVANCE_ORDER):
                status = state_status
            state_progress = state_info.get("progress")
            if isinstance(state_progress, (int, float)):
                progress = float(state_progress)
            state_detail = state_info.get("detail")
            if isinstance(state_detail, str) and state_detail:
                detail = state_detail
            started_at = (
                state_info.get("started_at")
                if prefer_state
                else state_info.get("started_at") or started_at
            )
            completed_at = (
                state_info.get("completed_at")
                if prefer_state
                else state_info.get("completed_at") or completed_at
            )
            error = state_info.get("error") if prefer_state else state_info.get("error") or error
        entries.append(
            JobStageEntry(
                stage_name=name,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                error=error,
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
    remote_meta = record.result.get("remote") if isinstance(record.result, dict) else None
    remote_meta = remote_meta if isinstance(remote_meta, dict) else {}
    submit_state = remote_meta.get("submit_state")
    cancel_state = remote_meta.get("cancel_state")
    if submit_state in ("intent", "unconfirmed"):
        reconcile_action = "reconcile_submission_pending"
    elif submit_state == "aborted_before_bsub":
        reconcile_action = "confirm_cancelled_from_aborted_submission"
    elif submit_state == "not_accepted":
        reconcile_action = "retention_reclaims_directory"
    else:
        reconcile_action = ""
    return JobRecovery(
        can_pause=status == JobStatus.RUNNING,
        can_unpause=status == JobStatus.PAUSED,
        can_continue=can_continue,
        continue_mode="checkpoint" if can_continue else "",
        continue_notes=notes,
        can_rerun=status.is_terminal,
        can_purge=True,
        can_cancel=status.is_active,
        submit_state=submit_state if isinstance(submit_state, str) else None,
        cancel_state=cancel_state if isinstance(cancel_state, str) else None,
        reconcile_action=reconcile_action,
    )


@router.get("/jobs/{job_id}/detail", response_model=V1JobDetailResponse)
def get_job_detail(job_id: str, request: Request) -> V1JobDetailResponse:
    """Rich read-only job projection: DB + disk + recovery matrix (§4.5)."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    job_model = _record_to_v1_model(record, include_release_facts=True)
    job_model = _enrich_job_snapshot(record, job_model, include_event=True)
    task_row = manager.tasks.get(job_id)
    if task_row is not None:
        job_model = _enrich_v1_with_names(job_model, resolve_task_names(task_row))
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
        effective_config=_read_effective_config(record),
        structure_summary=_structure_result_summary(request, record),
    )


def _structure_result_summary(request: Request, record: JobRecord) -> dict[str, Any]:
    """Keep execution, structure creation and chemical validation independent."""
    from acp.scheduler.job_edit import resolve_previous_outputs

    root = _job_read_root(request, record)
    outputs = resolve_previous_outputs(root, job_id=record.id, project_id=record.project_id)
    if _is_remote_job(record) and not (root / "RESULT" / "result_manifest.json").is_file():
        return {"availability": "pending_sync", "message": "结果目录仍待同步", "structures": []}
    if _is_remote_job(record):
        from acp.results.manifest import load_result_manifest
        from acp.results.structure_policy import reusable_product

        manifest = load_result_manifest(root)
        expected = (
            [p for p in manifest.products if reusable_product(p.to_dict())] if manifest else []
        )
        if len(outputs) < len(expected):
            return {
                "availability": "pending_fetch",
                "message": "部分结果几何仍待下载；暂不能确定结构总数",
                "structures": [
                    {k: v for k, v in row.items() if k != "xyz_text"} for row in outputs
                ],
            }
    structures = [{k: v for k, v in row.items() if k != "xyz_text"} for row in outputs]
    count = len(structures)
    if count:
        message = f"保留 {count} 个可复用结构；计算执行状态与结构验证结果分别显示"
    elif record.spec.workflow in {"frequency", "singlepoint", "nmr"}:
        message = "此任务计算属性；所用结构见原始输入来源"
    elif record.spec.workflow in {"PESsearch", "scan"}:
        message = "当前采用人工选点；可打开完整曲线和轨迹保存帧"
    else:
        message = "没有具备成功证据的自动结构；可从可读轨迹手动保存初猜"
    return {
        "availability": "available",
        "count": count,
        "message": message,
        "structures": structures,
        "input_sources": record.spec.input,
    }


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


def _read_effective_config(record: JobRecord) -> dict[str, Any] | None:
    """Read ``effective_config.json`` for BatchOptimize jobs."""
    if record.spec.workflow != "BatchOptimize" or not record.work_dir:
        return None
    try:
        from acp.calculations.batch.effective_config import read_effective_config

        return read_effective_config(Path(record.work_dir))
    except Exception:
        return None


@router.post("/jobs/{job_id}/cancel", response_model=V1JobRecordModel)
def cancel_job(job_id: str, request: Request) -> V1JobRecordModel:
    _require_job_scope_access(request, job_id, action=_AccessAction.CONTROL, require_exists=False)
    record = _commands(request).cancel(job_id)
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
    _require_job_scope_access(request, job_id, action=_AccessAction.CONTROL, require_exists=False)
    return _run_job_state_action(_commands(request).pause, job_id)


@router.post("/jobs/{job_id}/unpause", response_model=V1JobRecordModel)
def unpause_job(job_id: str, request: Request) -> V1JobRecordModel:
    """Revive a PAUSED job in place (SIGCONT locally, ``bresume`` on LSF) → RUNNING."""
    _require_job_scope_access(request, job_id, action=_AccessAction.CONTROL, require_exists=False)
    return _run_job_state_action(_commands(request).unpause, job_id)


@router.post("/jobs/{job_id}/continue", response_model=V1JobRecordModel)
def continue_job(
    job_id: str,
    request: Request,
    body: V1JobContinueRequest | None = None,
) -> V1JobRecordModel:
    """Re-enter a FAILED/CANCELLED job from its checkpoint → QUEUED.

    Optional ``body.target_node`` re-pins the execution node (D15).  An
    unknown/disabled/incapable override → HTTP 400 with the same error
    codes as job creation; ``None`` returns to the source node.
    """
    target_node = body.target_node if body else None
    _require_job_scope_access(request, job_id, action=_AccessAction.CONTROL, require_exists=False)
    try:
        record = _commands(request).continue_job(job_id, target_node=target_node)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
    except (ExecutionTargetError, NoCapableNodeError) as exc:
        raise HTTPException(status_code=400, detail=_target_validation_detail(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _record_to_v1_model(record)


@router.post("/jobs/{job_id}/rerun", response_model=V1JobRecordModel)
def rerun_job(
    job_id: str,
    request: Request,
    body: V1JobRerunRequest | None = None,
) -> V1JobRecordModel:
    """Re-queue the existing task for a full in-place rerun.

    Unlike ``/clone``, this endpoint keeps the same job row and task identity.
    A legacy task directory with shell-unsafe characters may be renamed before
    queueing. The optional legacy ``project_id`` body is accepted only when it
    matches the current project.
    """
    _require_job_scope_access(request, job_id, action=_AccessAction.CONTROL, require_exists=False)
    try:
        record = _commands(request).rerun(job_id, project_id=body.project_id if body else None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _record_to_v1_model(record)


def _build_edited_spec(
    record: JobRecord,
    req: V1EditPreviewRequest | V1EditRecalculateRequest,
    request: Request,
    manager: JobManager,
) -> tuple[JobSpec, list[dict[str, Any]]]:
    """Rebuild a validated JobSpec from an edit request.

    Shares the exact parameter resolvers of POST ``/jobs`` (electronic-state
    expansion, bond-scan preparation, stage-artifact resolution, batch
    structure inlining, stage execution inheritance) so a configuration that
    is legal at creation is legal for recalculation and vice versa (plan §9).
    """
    workflow = req.workflow or record.spec.workflow
    if req.mode == "in_place" and workflow != record.spec.workflow:
        raise EditValidationError("原地重算锁定工作流；请切换为「创建新任务」以更换工作流")
    edit_status = workflow_edit_status(workflow)
    if not edit_status["editable"]:
        raise EditValidationError(
            edit_status.get("migration_hint")
            or f"工作流 {workflow} 为 {edit_status['status']}，不支持编辑重算"
        )
    from acp.scheduler.edit_parameters import (
        apply_edited_parameters,
        merge_edit_input,
        merge_edit_section,
    )

    supplied = req.model_fields_set
    same_workflow = workflow == record.spec.workflow

    def section(key: str) -> dict[str, Any]:
        original = getattr(record.spec, key) if same_workflow else {}
        if key not in supplied:
            return deepcopy(original)
        if key == "input":
            return merge_edit_input(original, getattr(req, key))
        return merge_edit_section(original, getattr(req, key))

    method = _expand_method_electronic_state(section("method"))
    inp = section("input")
    if same_workflow:
        try:
            edited_levels = None
            if "method" in supplied and (
                req.method_projection_changed or req.method != record.spec.method
            ):
                edited_levels = req.method.get("levels") or {}
            method, inp = apply_edited_parameters(
                record.spec,
                method,
                inp,
                edited_levels=edited_levels,
            )
        except ValueError as exc:
            raise EditValidationError(str(exc)) from exc
    _validate_scan_submission(workflow, method, inp)
    _validate_simple_method_cli(workflow, method)
    if workflow == "BatchOptimize" and method != record.spec.method:
        _canonicalize_batch_keywords(method)
    batch_snapshots: list[dict[str, Any]] = []
    _validate_reaction_path_frame_input(inp, record, request, manager)
    structure_changed = input_structure_changed(
        record.spec.input if isinstance(record.spec.input, dict) else {},
        inp,
    )
    if structure_changed and req.mode == "in_place":
        raise EditValidationError(
            "已更换计算输入结构；为保留原任务及结果，请选择“创建新任务”后再提交。"
        )
    if structure_changed or req.mode == "new_job":
        batch_snapshots.extend(
            _snapshot_direct_source_input(
                inp,
                request,
                verify_geometry=True,
                include_unindexed=True,
            )
        )
    if workflow == "irc":
        try:
            inp = _resolve_irc_source_reference(inp, manager)
        except HTTPException as exc:
            raise EditValidationError(str(exc.detail)) from exc
    elif workflow == "PESsearch" and str(method.get("mode") or "") == "bond_length_scan":
        inp = _prepare_bond_scan_input(
            inp,
            manager,
            entry="edit",
            baseline_protocol=_baseline_bond_scan_protocol(record),
        )
    elif workflow == "PESsearch":
        inp = _resolve_stage_artifact_ref(workflow, inp, manager)
    elif workflow == "BatchOptimize":
        inp, resolved_snapshots = _resolve_batch_structures_input(inp, request)
        batch_snapshots.extend(resolved_snapshots)

    original_execution_mode = (
        getattr(record.spec.execution_mode, "value", None) or record.spec.execution_mode
    )
    execution_mode = (
        req.execution_mode if req.execution_mode is not None else original_execution_mode
    )
    target_node = req.target_node if req.target_node is not None else record.spec.target_node
    execution_mode, target_node = _inherit_stage_execution_fields(
        workflow, inp, execution_mode, target_node, manager
    )
    molecule_name = req.molecule_name or record.spec.molecule_name
    # Edit parity: an omitted/empty task_name keeps the ORIGINAL value (even
    # empty — scheduler/legacy records may carry '') instead of re-defaulting
    # to the workflow id, so an unmodified resubmission diffs empty. Downstream
    # task-dir naming already applies ``task_name or workflow``.
    task_name = str(req.task_name or "").strip() or record.spec.task_name
    is_submit = isinstance(req, V1EditRecalculateRequest)
    spec = JobSpec(
        workflow=workflow,
        name=req.name if is_submit and "name" in supplied else record.spec.name,
        input=inp,
        method=method,
        resources=section("resources"),
        output_dir=req.output_dir if is_submit else None,
        config_path=(req.config_path if is_submit and req.config_path else record.spec.config_path),
        tags=list(req.tags if "tags" in supplied else record.spec.tags),
        node_tags=list(req.node_tags if "node_tags" in supplied else record.spec.node_tags),
        project_id=req.project_id or (record.project_id or record.spec.project_id),
        execution_mode=execution_mode,
        target_node=target_node,
        molecule_name=molecule_name,
        task_name=task_name,
        remark=req.remark if "remark" in supplied else record.spec.remark,
    )
    if workflow == "irc":
        try:
            spec = manager._verified_irc_spec(spec)
        except ValueError as exc:
            raise EditValidationError(str(exc)) from exc
    return spec, batch_snapshots


def _validate_reaction_path_frame_input(
    inp: dict[str, Any], record: JobRecord, request: Request, manager: JobManager
) -> None:
    """Verify an edited XYZ still matches its selected upstream S2 frame."""
    origin = inp.get("edit_input_origin")
    if not isinstance(origin, dict) or origin.get("mode") != "reaction_path_frame":
        return
    parent_job_id = str(origin.get("parent_job_id") or "").strip()
    try:
        frame_index = int(origin.get("frame_index"))
    except (TypeError, ValueError) as exc:
        raise EditValidationError("反应路径帧缺少有效的帧编号，请重新选择结构。") from exc

    allowed_sources = {
        str(item.get("job_id") or "")
        for item in _reaction_path_sources_for_edit(record, manager)
        if isinstance(item, dict)
    }
    if not parent_job_id or parent_job_id not in allowed_sources:
        raise EditValidationError("所选反应路径不属于当前任务的上游 PESsearch 来源。")

    try:
        frame = get_s2_frame(parent_job_id, frame_index, request)
    except HTTPException as exc:
        raise EditValidationError(
            f"所选反应路径帧已不可读取，请重新选择结构：{exc.detail}"
        ) from exc

    items = inp.get("items")
    if isinstance(items, list):
        if len(items) != 1 or not isinstance(items[0], dict):
            raise EditValidationError("从路径帧重算目前只支持提交一个起始结构。")
        input_xyz = str(items[0].get("xyz") or items[0].get("xyz_text") or "")
    else:
        input_xyz = str(inp.get("source") or inp.get("xyz_text") or inp.get("xyz") or "")
    if not input_xyz or not frame.xyz or not _same_xyz_geometry(input_xyz, frame.xyz):
        raise EditValidationError("所选结构与上游路径帧当前几何不一致；请重新选择该路径帧后提交。")

    original_input = record.spec.input if isinstance(record.spec.input, dict) else {}
    original_xyzs: list[str] = []
    for collection_key in ("items", "candidates"):
        rows = original_input.get(collection_key)
        if isinstance(rows, list):
            original_xyzs.extend(
                str(row.get("xyz") or row.get("xyz_text") or row.get("source") or "")
                for row in rows
                if isinstance(row, dict)
            )
    scan_request = original_input.get("scan_request")
    source_input = scan_request.get("source") if isinstance(scan_request, dict) else original_input
    if isinstance(source_input, dict):
        original_xyzs.append(
            str(
                source_input.get("xyz_text")
                or source_input.get("xyz")
                or source_input.get("source")
                or ""
            )
        )
    if record.work_dir:
        try:
            original_xyzs.append((Path(record.work_dir) / "input.xyz").read_text(encoding="utf-8"))
        except OSError:
            pass
    reference_xyzs = [value for value in original_xyzs if value.strip()]
    if not reference_xyzs or not any(
        _same_xyz_atom_order(value, frame.xyz) for value in reference_xyzs
    ):
        raise EditValidationError(
            "无法确认路径帧与原任务输入的原子数量及顺序一致；请从匹配的上游结构重新发起。"
        )

    _validate_structure_electronic_state(inp)
    origin["input_xyz_sha256"] = hashlib.sha256(input_xyz.encode("utf-8")).hexdigest()
    origin["verified_parent_job_id"] = parent_job_id
    origin["verified_frame_index"] = frame_index


def _edited_spec_diff(record: JobRecord, spec: JobSpec) -> list[dict[str, Any]]:
    return diff_editable_specs(
        editable_spec_from_record(record),
        editable_spec_from_parts(
            workflow=spec.workflow,
            input_spec=spec.input,
            method=spec.method,
            resources=spec.resources,
            molecule_name=spec.molecule_name,
            task_name=spec.task_name,
            remark=spec.remark,
            tags=spec.tags,
            node_tags=spec.node_tags,
            project_id=spec.project_id,
            execution_mode=getattr(spec.execution_mode, "value", None) or spec.execution_mode,
            target_node=spec.target_node,
            config_path=spec.config_path,
        ),
    )


def _validate_edited_execution(spec: JobSpec, manager: JobManager) -> None:
    _validate_scan_submission(spec.workflow, spec.method, spec.input)
    try:
        validate_execution_request(spec)
    except ExecutionTargetError as exc:
        detail = _target_validation_detail(exc)
        detail["code"] = getattr(exc, "code", None) or "execution_mode_conflict"
        raise HTTPException(status_code=400, detail=detail) from exc
    try:
        validate_submission_target(spec, registry=manager.registry)
    except (ExecutionTargetError, NoCapableNodeError) as exc:
        raise HTTPException(status_code=400, detail=_target_validation_detail(exc)) from exc


def _edit_payload_dict(
    job_id: str, body: V1EditPreviewRequest | V1EditRecalculateRequest, spec: JobSpec
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "mode": body.mode,
        "workflow": spec.workflow,
        "input": spec.input,
        "method": spec.method,
        "resources": spec.resources,
        "molecule_name": spec.molecule_name,
        "task_name": spec.task_name,
        "remark": spec.remark,
        "tags": spec.tags,
        "node_tags": spec.node_tags,
        "project_id": spec.project_id,
        "execution_mode": getattr(spec.execution_mode, "value", None) or spec.execution_mode,
        "target_node": spec.target_node,
    }


def _edit_request_payload(job_id: str, body: V1EditRecalculateRequest) -> dict[str, Any]:
    """Hash supplied patch fields, independently of the mutable source spec."""
    return {
        "job_id": job_id,
        "request": body.model_dump(
            exclude_unset=True,
            exclude={"request_id", "expected_source_revision", "preview_fingerprint"},
        ),
    }


@router.get("/jobs/{job_id}/edit-draft", response_model=V1EditDraftResponse)
def get_job_edit_draft(job_id: str, request: Request) -> V1EditDraftResponse:
    """Restore the original submission configuration as an editable draft."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    read_record = record
    if _is_remote_job(record):
        root = _job_read_root(request, record)
        read_record = replace(record, work_dir=str(root))
    draft = build_edit_draft(read_record, run_root=manager.run_root)
    if _is_remote_job(record):
        from acp.scheduler.job_edit import resolve_previous_outputs

        root = _job_read_root(request, record)
        from acp.results.manifest import load_result_manifest

        manifest = load_result_manifest(root)
        cache = _remote_structure_cache(request)
        availability = "available"
        try:
            cache.fetch_reusable_geometries(record, root)
        except (OSError, RuntimeError, ValueError):
            availability = "pending_fetch"
        outputs = resolve_previous_outputs(root, job_id=record.id, project_id=record.project_id)
        draft["input_refs"]["previous_outputs"] = outputs
        draft["input_refs"]["last_structure"] = outputs[0] if outputs else None
        draft["input_refs"]["outputs_availability"] = (
            availability if manifest is not None or outputs else "pending_sync"
        )
    draft["input_refs"]["reaction_path_sources"] = _reaction_path_sources_for_edit(record, manager)
    return V1EditDraftResponse(**draft)


def _reaction_path_sources_for_edit(record: JobRecord, manager: JobManager) -> list[dict[str, Any]]:
    """Return PESsearch jobs that are direct or recorded structure ancestors."""
    input_spec = record.spec.input if isinstance(record.spec.input, dict) else {}
    source_job_ids: list[str] = []

    def add_source(value: Any) -> None:
        if isinstance(value, dict):
            job_id = str(value.get("job_id") or value.get("source_job_id") or "").strip()
            source_id = str(value.get("source_id") or "").strip()
            if not job_id and source_id.startswith("job_"):
                job_id = source_id[4:].partition(":")[0]
            if job_id and job_id not in source_job_ids:
                source_job_ids.append(job_id)
            for child in value.values():
                if isinstance(child, (dict, list, tuple)):
                    add_source(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                add_source(child)

    if str(record.spec.workflow or "") == "PESsearch":
        source_job_ids.append(record.id)
    add_source(input_spec)

    if record.work_dir:
        events_path = runtime_file(record.work_dir, "events.jsonl")
        if events_path.is_file():
            try:
                with events_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if (
                            isinstance(event, dict)
                            and event.get("type") == "structure_source_snapshot"
                        ):
                            add_source(event.get("snapshots") or [])
                            break
            except (OSError, UnicodeDecodeError):
                logger.debug("Could not read edit source lineage for %s", record.id, exc_info=True)

    sources: list[dict[str, Any]] = []
    for source_job_id in source_job_ids:
        source_record = manager.get(source_job_id)
        if source_record is None or str(source_record.spec.workflow or "") != "PESsearch":
            continue
        source_input = (
            source_record.spec.input if isinstance(source_record.spec.input, dict) else {}
        )
        scan_source = source_input.get("scan_request")
        scan_source = scan_source.get("source") if isinstance(scan_source, dict) else None
        electronic_state = scan_source if isinstance(scan_source, dict) else source_input
        sources.append(
            {
                "job_id": source_record.id,
                "name": source_record.spec.task_name or source_record.spec.name or source_record.id,
                "workflow": source_record.spec.workflow,
                "charge": electronic_state.get("charge", source_input.get("charge", 0)),
                "multiplicity": electronic_state.get(
                    "multiplicity", source_input.get("multiplicity", 1)
                ),
            }
        )
    return sources


@router.post("/jobs/{job_id}/edit-recalculate/preview", response_model=V1EditPreviewResponse)
def preview_job_edit_recalculate(
    job_id: str,
    body: V1EditPreviewRequest,
    request: Request,
) -> V1EditPreviewResponse:
    """Validate an edited spec and return the normalised diff (no writes)."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if body.mode not in ("in_place", "new_job"):
        raise HTTPException(status_code=422, detail=f"invalid mode: {body.mode!r}")
    source_revision = compute_source_revision(record)
    if body.expected_source_revision and body.expected_source_revision != source_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "source_revision_conflict",
                "message": "源任务配置已变化，请刷新编辑草稿后重试",
                "current": source_revision,
            },
        )
    try:
        spec, _ = _build_edited_spec(record, body, request, manager)
    except EditValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_edited_execution(spec, manager)

    blocking: list[str] = []
    if body.mode == "in_place":
        if spec.workflow != record.spec.workflow:
            blocking.append("原地重算锁定工作流；请切换为「创建新任务」以更换工作流")
        if not record.status.is_terminal:
            blocking.append(
                f"任务状态 {record.status.value} 非终态；请先取消并等待终态后再原地重算"
            )
    diff = _edited_spec_diff(record, spec)
    warnings: list[str] = []
    for entry in diff:
        if entry.get("kind") == "input":
            warnings.append(f"输入已修改（{entry['path']}）；提交后将重新物化输入")
        if entry.get("kind") == "resource":
            warnings.append(f"资源已修改（{entry['path']}）")
    # T14 warning channel (edit-recalculate preview) for migrated configs.
    warnings.extend(_scan_request_level_warnings(spec.input))
    fingerprint = compute_preview_fingerprint(
        job_id, source_revision, spec.workflow, spec.input, spec.method, spec.resources
    )
    return V1EditPreviewResponse(
        ok=not blocking,
        mode=body.mode,
        workflow=spec.workflow,
        job_id=job_id,
        diff=[V1EditDiffEntry(**entry) for entry in diff],
        warnings=warnings,
        blocking_reasons=blocking,
        preview_fingerprint=fingerprint,
        source_revision=source_revision,
    )


@router.post("/jobs/{job_id}/edit-recalculate", response_model=V1EditRecalculateResponse)
def submit_job_edit_recalculate(
    job_id: str,
    body: V1EditRecalculateRequest,
    request: Request,
) -> V1EditRecalculateResponse:
    """Apply an edited configuration as an in-place attempt or a new task."""
    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if body.mode not in ("in_place", "new_job"):
        raise HTTPException(status_code=422, detail=f"invalid mode: {body.mode!r}")
    if not str(body.request_id or "").strip():
        raise HTTPException(status_code=422, detail="request_id is required")
    from acp.scheduler.job_edit import JobEditOperationStore

    known_request = False
    payload = _edit_request_payload(job_id, body)
    payload_hash = compute_payload_hash(payload)
    legacy_payload = False
    existing_op = JobEditOperationStore(manager.store.db_path).get(body.request_id)
    if existing_op is not None:
        if existing_op["job_id"] != job_id:
            raise HTTPException(
                status_code=409,
                detail=f"request_id {body.request_id} 已用于其他任务；请使用新的 request_id",
            )
        # A replayed request may legitimately carry a stale revision and
        # fingerprint — the manager's payload-hash check arbitrates it.
        known_request = True
        stored_payload = json.loads(existing_op["payload_json"] or "{}")
        legacy_payload = "request" not in stored_payload
        if not legacy_payload:
            if existing_op["payload_hash"] != payload_hash:
                raise HTTPException(status_code=409, detail="request_id 已用于不同的提交负载")
            if existing_op["status"] == "completed":
                result = json.loads(existing_op["result_json"] or "{}")
                result["replayed"] = True
                return V1EditRecalculateResponse(**result)
    source_status = workflow_edit_status(record.spec.workflow)
    if body.mode == "in_place":
        if not source_status["editable"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    source_status.get("migration_hint")
                    or f"工作流 {record.spec.workflow} 为 {source_status['status']}；"
                    "历史任务仅支持查看与迁移，原地编辑重算已禁用"
                ),
            )
        if body.workflow and body.workflow != record.spec.workflow:
            raise HTTPException(
                status_code=422,
                detail="原地重算锁定工作流；如需更换工作流请使用「创建新任务」模式",
            )
    source_revision = compute_source_revision(record)
    if (
        not known_request
        and body.expected_source_revision
        and body.expected_source_revision != source_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "source_revision_conflict",
                "message": "源任务配置已变化，请刷新编辑草稿后重试",
                "current": source_revision,
            },
        )
    try:
        spec, source_snapshots = _build_edited_spec(record, body, request, manager)
    except EditValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_edited_execution(spec, manager)
    if body.preview_fingerprint and not known_request:
        expected = compute_preview_fingerprint(
            job_id, source_revision, spec.workflow, spec.input, spec.method, spec.resources
        )
        if body.preview_fingerprint != expected:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "preview_fingerprint_conflict",
                    "message": "提交的配置与预览时不一致，请重新预览后提交",
                },
            )
    diff = _edited_spec_diff(record, spec)
    if legacy_payload:
        payload = _edit_payload_dict(job_id, body, spec)
        payload_hash = compute_payload_hash(payload)
    try:
        result = _commands(request).edit_recalculate(
            job_id,
            mode=body.mode,
            new_spec=spec,
            expected_source_revision=body.expected_source_revision,
            request_id=body.request_id,
            payload_hash=payload_hash,
            payload_json=json.dumps(payload, default=str, sort_keys=True),
            diff_summary=diff,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from exc
    except EditConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.get("replayed"):
        _persist_method_warning_event(manager, record, result, spec)
    if result.get("operation") == "new_job" and not result.get("replayed"):
        created = manager.get(str(result.get("job_id") or ""))
        if created is not None and created.work_dir and source_snapshots:
            try:
                JobEventLog(runtime_file(created.work_dir, "events.jsonl")).append(
                    "structure_source_snapshot",
                    job_id=created.id,
                    parent_job_id=record.id,
                    snapshots=source_snapshots,
                )
                from acp.scheduler.structure_source_store import StructureSourceStore

                usage_store = StructureSourceStore(manager.store.db_path)
                for snapshot in source_snapshots:
                    source_uid = str(snapshot.get("source_uid") or "")
                    if source_uid and snapshot.get("candidate_record_id"):
                        usage_store.record_usage(
                            source_uid,
                            created.id,
                            snapshot,
                            disabled_exception=bool(snapshot.get("disabled_exception")),
                            exception_reason=str(snapshot.get("exception_reason") or ""),
                        )
            except (OSError, ValueError, sqlite3.Error):
                logger.warning(
                    "Could not persist recalculation structure provenance for job %s",
                    created.id,
                    exc_info=True,
                )
    return V1EditRecalculateResponse(
        **{key: value for key, value in result.items() if key != "diff"},
        diff_summary=[V1EditDiffEntry(**entry) for entry in diff],
    )


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
    report = _commands(request).purge(
        job_ids=body.job_ids,
        status=body.status,
        project_id=body.project_id,
        older_than_days=body.older_than_days,
        delete_data=body.delete_data,
        force_cancel=body.force_cancel,
    )
    return V1JobPurgeResponse(results=[V1JobPurgeResult(**entry) for entry in report])


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
) -> StreamingResponse:
    _require_entry_access(request, _EntryClass.EVENT_STREAM, job_id=job_id)
    return await legacy_stream_job_events(job_id, request, after_seq=after_seq)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str,
    request: Request,
    lines: int = Query(default=300, ge=1, le=5000),
    stdout_offset: int = Query(default=0, ge=0),
    stderr_offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    _require_entry_access(request, _EntryClass.LOG, job_id=job_id)

    stdout_path = runtime_file(work_dir, "stdout.log")
    stderr_path = runtime_file(work_dir, "stderr.log")

    if stdout_offset > 0 or stderr_offset > 0:
        # Cursor mode: incremental fetch from offset
        stdout_lines = read_log_range(stdout_path, offset=stdout_offset, max_lines=lines)
        stderr_lines = read_log_range(stderr_path, offset=stderr_offset, max_lines=lines)
        stdout_total = _count_lines(stdout_path)
        stderr_total = _count_lines(stderr_path)
        return JSONResponse(
            {
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines),
                "stdout_lines": stdout_lines,
                "stderr_lines": stderr_lines,
                "stdout_next_offset": stdout_offset + len(stdout_lines),
                "stderr_next_offset": stderr_offset + len(stderr_lines),
                "has_more": (stdout_offset + len(stdout_lines)) < stdout_total
                or (stderr_offset + len(stderr_lines)) < stderr_total,
            }
        )

    # Legacy mode: tail fetch (backward compatible)
    stdout_lines = read_log_tail(stdout_path, lines=lines)
    stderr_lines = read_log_tail(stderr_path, lines=lines)
    stdout_total = _count_lines(stdout_path)
    stderr_total = _count_lines(stderr_path)
    return JSONResponse(
        {
            "stdout": "\n".join(stdout_lines),
            "stderr": "\n".join(stderr_lines),
            "stdout_lines": stdout_lines,
            "stderr_lines": stderr_lines,
            "stdout_next_offset": stdout_total,
            "stderr_next_offset": stderr_total,
            "has_more": False,
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
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id)
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
    # Queued BatchOptimize jobs have not reached runner-side materialization
    # yet. Surface their persisted input snapshot as the task-root input.xyz.
    record = manager.get(job_id)
    if (
        path is None
        and record is not None
        and record.spec.workflow == "BatchOptimize"
        and not any(entry.path == "input.xyz" for entry in entries)
    ):
        from acp.scheduler.input_snapshot import input_xyz_snapshot

        xyz_snapshot = input_xyz_snapshot(record.spec.input)
        if xyz_snapshot:
            entries.append(
                FileEntry(
                    path="input.xyz",
                    size=len(xyz_snapshot.encode("utf-8")),
                    modified=datetime.now(timezone.utc).timestamp(),
                    is_dir=False,
                )
            )
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
def download_job_file(job_id: str, file_path: str, request: Request) -> Response:
    manager = _manager(request)
    work_dir = manager.work_dir_of(job_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    # Re-verify authorization immediately before the download body is produced.
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id, resource_id=file_path)
    resolved = resolve_safe(work_dir, file_path)
    if resolved is None:
        record = manager.get(job_id)
        if file_path.replace("\\", "/") == "input.xyz" and record is not None:
            from acp.scheduler.input_snapshot import input_xyz_snapshot

            xyz_snapshot = input_xyz_snapshot(record.spec.input)
            if xyz_snapshot:
                return Response(
                    content=xyz_snapshot,
                    media_type="chemical/x-xyz; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="input.xyz"'},
                )
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
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id, resource_id=path or "")
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
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id)
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
    automatically because it requires a known report file name.  Report
    consumers (e.g. the NMR panel) request ``mode=report`` explicitly and
    unwrap ``content["report"]`` — see ``_parse_remote_report`` for the
    deterministic unwrap contract.

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

    _require_entry_access(
        request,
        _EntryClass.RANGE if mode == "range" else _EntryClass.DOWNLOAD,
        job_id=job_id,
        resource_id=file_path,
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

    Deterministic unwrap contract (frontend ``fetchNmrReportJson``, todo 23):
    ``mode=report`` responses carry the parsed document at
    ``content["report"]`` whenever ``content["type"] == "json_report"`` and
    report consumers unwrap exactly that key.  Values inside the document
    dict are serialized verbatim — nulls survive (the preview model dumps
    with ``exclude_none`` at the field level only), so missing numbers render
    as an em dash upstream and are never coerced to 0.
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
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id, resource_id=file_path)
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
    # Re-verify authorization immediately before the download body is produced
    # (an SFTP rejection is not a Workspace revocation and vice versa).
    _require_entry_access(request, _EntryClass.DOWNLOAD, job_id=job_id, resource_id=file_path)
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
    _require_entry_access(request, _EntryClass.LOG, job_id=job_id, resource_id=name)
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
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    tasks = _stage_task_store(request).list_by_job(job_id)
    if record.spec.workflow == "BatchOptimize":
        by_name = {task.stage_name: task for task in tasks}
        models = []
        for entry in _detail_stages(job_id, record, request):
            task = by_name.get(entry.stage_name)
            if task is not None:
                models.append(
                    _stage_task_to_model(task).model_copy(
                        update={
                            "state": entry.status,
                            "started_at": entry.started_at,
                            "completed_at": entry.completed_at,
                            "stderr_summary": entry.error,
                        }
                    )
                )
        if models:
            return StageTaskListResponse(tasks=models)
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
    response_model_exclude_unset=True,
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

    from acp.calculations.irc.source import (
        load_ts_result_manifest,
        resolve_verified_ts_source,
    )

    try:
        manifest = load_ts_result_manifest(source_record, getattr(manager, "remote_fetcher", None))
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
        if (Path(source_record.work_dir) / "RESULT" / MANIFEST_FILENAME).is_file():
            raise HTTPException(status_code=422, detail="IRC source result manifest is invalid")
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

    product = next((item for item in manifest.products if item.id == artifact_id), None)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
    if product.kind.value != "structure":
        raise HTTPException(
            status_code=422,
            detail=f"Artifact {artifact_id} is not a structure product",
        )

    try:
        resolve_verified_ts_source(
            source_record, artifact_id, getattr(manager, "remote_fetcher", None)
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source_spec = source_record.spec
    irc_spec = JobSpec(
        workflow="irc",
        name=f"{source_spec.name or job_id}_irc",
        input={
            "source_job_id": job_id,
            "source_product_id": artifact_id,
            "directions": ["forward", "reverse"],
        },
        method={},
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
        record = _commands(request).submit(irc_spec)
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
    """List reusable structures and explicitly saved terminal-job frames."""
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
    sources = [StructureSourceSummary(**entry) for entry in entries]
    # Enrich with org-store fields (custom_name, tags, role, etc.)
    # when the org store is available.  Old DBs without org tables
    # silently skip enrichment — the optional fields stay at defaults.
    try:
        manager = _manager(request)
        from acp.scheduler.structure_source_store import StructureSourceStore

        org_store = StructureSourceStore(manager.store.db_path)
        for s in sources:
            org_entry = org_store.get_by_legacy_source_id(s.source_id)
            if org_entry is None:
                continue
            s.custom_name = org_entry.get("custom_name")
            s.resolved_name = org_entry.get("resolved_name")
            s.default_name = org_entry.get("default_name")
            s.tags = org_entry.get("tags", [])
            org_role = org_entry.get("role", "")
            if org_role:
                s.role = org_role
            s.role_evidence = org_entry.get("role_evidence", "")
            s.source_uid = org_entry.get("source_uid", "")
            s.job_resolved_name = org_entry.get("job_resolved_name")
            s.usage_status = org_entry.get("usage_status", "active")
    except Exception:
        logger.debug("Org-store enrichment skipped (store unavailable)", exc_info=True)
    return StructureSourceListResponse(sources=sources)


@router.get("/structure-sources/{source_id:path}", response_model=StructureSourceDetailResponse)
def get_structure_source(request: Request, source_id: str) -> StructureSourceDetailResponse:
    """Load one structure source (local disk or on-demand remote fetch)."""
    manager = _manager(request)
    service = _structure_source_service(request)
    try:
        job_id, rel_path = StructureSourceService.parse_source_id(source_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Invalid source_id: {source_id}")
    try:
        asset, checksum = service.get(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    record = manager.get(job_id)
    project_id = None
    project_name = None
    job_status = ""
    if record is not None:
        project_id = record.project_id or record.spec.project_id
        job_status = record.status.value
        project_name = service.project_name_map().get(str(project_id or ""))
    return StructureSourceDetailResponse(
        source_id=source_id,
        checksum=checksum,
        structure=StructureAssetModel(**asset),
        job_id=job_id,
        project_id=project_id,
        project_name=project_name,
        job_status=job_status,
        path=rel_path,
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
    warnings: list[str] = []
    normalized, errors = normalize_and_validate_method_config(method, schema, warnings)
    return ValidateMethodResponse(
        valid=not errors,
        errors=errors,
        warnings=warnings,
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
    from acp.intake import detect_and_parse
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
    # Upload must parse identically to paste (/structures/parse): same
    # candidate-fallback chain, so extension/content mismatch still parses.
    fmt, result = detect_and_parse(text, filename)

    for asset in result.structures:
        if asset.xyz:
            norm_name = f"{asset.name}.xyz"
            norm_path = upload_storage.save_normalized(project_id, upload_id, norm_name, asset.xyz)
            asset.normalized_path = str(norm_path.relative_to(upload_storage.run_root))

    if not result.structures:
        upload_storage.remove_upload(project_id, upload_id)

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
        queue=getattr(status, "queue", None),
        software=status.software,
        declared=status.declared,
        capability_state=status.capability_state,
        declared_ok=status.declared_ok,
        mismatch=status.mismatch,
        probe_note=status.probe_note,
    )


@router.get("/nodes", response_model=NodeListResponse)
def list_nodes(request: Request) -> NodeListResponse:
    """List all configured remote nodes and their cached status."""
    nm = _node_manager(request)
    return NodeListResponse(
        nodes=[_node_status_to_model(s) for s in nm.list_nodes()],
        auto_select=True,
    )


# ---------------------------------------------------------------------- #
# Submission-time matching preview (design §2.2)
# ---------------------------------------------------------------------- #

_NO_REMOTE_NODES_NOTE = "no remote nodes configured"


def _configured_node(nm: NodeManager, status) -> Any | None:
    """Config node backing a status; ``None`` when the manager has no config."""
    get_node = getattr(getattr(nm, "config", None), "get_node", None)
    if not callable(get_node):
        return None
    try:
        return get_node(status.name)
    except Exception:  # noqa: BLE001 — duck-typed NodeManager stubs in tests
        return None


def _node_declared_inputs(nm: NodeManager, status) -> tuple[Any, bool, str | None]:
    """Resolve ``(declared, enabled, queue)`` for one node status.

    ``declared`` is fed straight into :func:`matches_capabilities`, so the
    preview shares the dispatch-time declaration source.  The live config
    node is preferred (the same object the scheduler registry maps); a
    ``status.declared`` snapshot (dict-shaped, as produced by
    ``capability_state_fields``) is the fallback for duck-typed stubs.
    """
    node = _configured_node(nm, status)
    if node is not None:
        return (
            getattr(node, "capabilities", None),
            bool(getattr(node, "enabled", True)),
            getattr(node, "queue", None),
        )
    snapshot = getattr(status, "declared", None)
    if isinstance(snapshot, dict) and (snapshot.get("software") or snapshot.get("tags")):
        from acp.scheduler.remote.config import NodeCapabilities

        return (
            NodeCapabilities(
                software=tuple(snapshot.get("software") or ()),
                tags=tuple(snapshot.get("tags") or ()),
            ),
            getattr(status, "status", "") != "offline",
            snapshot.get("queue"),
        )
    return None, getattr(status, "status", "") != "offline", None


def _probed_software_names(status) -> frozenset[str]:
    """Resolved probe names from a status (empty = unknown generic fallback).

    Mirrors the registry's ``_probed_software`` extraction so preview and
    dispatch judge undeclared nodes identically.
    """
    software = getattr(status, "software", None)
    if not isinstance(software, dict):
        return frozenset()
    return frozenset(
        name for name, info in software.items() if isinstance(info, dict) and info.get("resolved")
    )


def _node_matching_item(
    nm: NodeManager,
    status: Any,
    required_software: frozenset[str],
    required_tags: frozenset[str],
) -> NodeMatchingItem:
    """Match one node against the requirements (single shared implementation)."""
    declared, enabled, _ = _node_declared_inputs(nm, status)
    match = matches_capabilities(
        required_software,
        required_tags,
        declared=declared,
        probed_software=_probed_software_names(status),
    )
    node_status = getattr(status, "status", "offline")
    return NodeMatchingItem(
        name=status.name,
        host=status.host,
        status=node_status,
        running_jobs=int(getattr(status, "running_jobs", 0) or 0),
        max_jobs=int(getattr(status, "max_jobs", 0) or 0),
        disk_usage_pct=int(getattr(status, "disk_usage_pct", 0) or 0),
        # Same top-level source as /nodes — visible for undeclared nodes too.
        queue=getattr(status, "queue", None),
        tags=list(declared.tags) if declared is not None else [],
        capability_state=getattr(status, "capability_state", "unknown"),
        declared_ok=getattr(status, "declared_ok", None),
        mismatch=list(getattr(status, "mismatch", []) or []),
        satisfies=bool(match.satisfies and enabled and node_status != "offline"),
        missing_software=list(match.missing_software),
        missing_tags=list(match.missing_tags),
        reasons=list(match.reasons),
        degraded=bool(is_degraded(status)),
    )


def _evaluate_matching(manager: JobManager, req: NodeMatchingRequest) -> NodeMatchingResponse:
    """Derive the requirements and judge every remote node (design §2.2)."""
    method = dict(req.method or {})
    if req.protocol is not None:
        method["protocol"] = req.protocol
    spec = JobSpec(workflow=req.workflow, method=method)
    required_software = derive_required_software(spec)
    required_tags = frozenset(req.node_tags or ())

    nm = manager.node_manager
    statuses = nm.list_nodes() if nm is not None else []
    nodes = [_node_matching_item(nm, s, required_software, required_tags) for s in statuses]
    return NodeMatchingResponse(
        required_software=sorted(required_software),
        node_tags=list(req.node_tags or ()),
        local_satisfies=bool(local_satisfies(required_software)),
        nodes=nodes,
        note=None if nodes else _NO_REMOTE_NODES_NOTE,
    )


@router.post("/nodes/matching", response_model=NodeMatchingResponse)
def node_matching(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> NodeMatchingResponse:
    """Preview which remote nodes can run a prospective submission.

    Read-only capability preview for the frontend node selector (design
    §2.2): derives the required software exactly as dispatch does (same
    :func:`derive_required_software` / :func:`matches_capabilities` calls)
    and reports per-node satisfies/missing fields.  Reuses the NodeManager
    30 s status cache; a cold cache triggers the same live SSH probe as
    ``GET /nodes``, so latency matches that endpoint.

    The raw body is validated explicitly so an absent ``node_tags`` key
    fails with HTTP 400 (FastAPI's pydantic default for a required field is
    422); ``node_tags: null`` is allowed and means "no tag constraint".
    """
    if "node_tags" not in payload:
        raise HTTPException(status_code=400, detail="Missing required field 'node_tags'")
    try:
        req = NodeMatchingRequest.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        raise HTTPException(status_code=400, detail=first_error.get("msg", str(exc))) from exc
    if req.workflow not in ALL_WORKFLOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported workflow '{req.workflow}'. Supported: {list(PUBLIC_WORKFLOWS)}",
        )
    manager = _manager(request)
    try:
        return _evaluate_matching(manager, req)
    except Exception:
        logger.exception("Node matching preview failed for workflow=%s", req.workflow)
        raise HTTPException(
            status_code=500, detail="Failed to evaluate node matching for the request"
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


# ---------------------------------------------------------------------------
# Read-only availability surface (B02-5 / plan todo 15)
# ---------------------------------------------------------------------------


def _read_only_reader(request: Request) -> Any:
    """Build the read-only resource reader from the manager-owned singletons.

    Strictly read-only: it consults the prepared cache index and the
    ``data_operations`` rows only.  It never fetches over SSH/SFTP and never
    enqueues a prepare operation — only an explicit prepare command, the
    terminal-transition background flow and startup recovery may do that.
    """
    from acp.services_data_bootstrap import build_read_only_reader

    manager = _manager(request)
    return build_read_only_reader(
        job_lookup=manager.get,
        cache=manager.structure_cache,
        operations=getattr(manager, "_prepare_operations", None),
    )


def _cache_only_resource_reader(request: Request) -> Any:
    """Build the per-resource cache-only reader (B02-6 / todo 16).

    Wraps the todo-15 read-only reader with the five per-resource compat
    adapters and the cache-only switch registry.  Same strict guarantees: zero
    SSH/SFTP, zero new prepare operations, zero parsing writes.  A cold cache is
    reported ``not_prepared`` so the caller falls back to the retained legacy
    GET (the compatibility channel).
    """
    from acp.services_data_bootstrap import build_cache_only_resource_reader

    manager = _manager(request)
    return build_cache_only_resource_reader(
        job_lookup=manager.get,
        cache=manager.structure_cache,
        operations=getattr(manager, "_prepare_operations", None),
    )


def _record_read_attempt(record: Any) -> int | None:
    """Return the record's authoritative 1-based attempt (``None`` if absent)."""
    raw = getattr(record, "attempt", None)
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


@router.get("/jobs/{job_id}/availability")
def get_job_availability(
    job_id: str,
    request: Request,
    resource: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    relative_path: str = Query(default=""),
    workflow: str = Query(default=""),
    expected_revision: int = Query(default=0, ge=0),
    attempt: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Return a read-only availability snapshot for one job resource.

    Additive read surface: it reports ``availability`` / ``freshness`` /
    ``basis_revision`` from the prepared cache index and prepare-operation rows
    and never triggers a fetch or a new prepare operation.  The existing job
    GETs keep their original behaviour and read root as the per-resource legacy
    fallback (the compatibility channel).

    ``resource`` names one of the five switch resources
    (catalog/manifest/frame_candidates/frequency_modes/geometry) and selects
    the per-resource compat adapter (default relative path + resource id);
    ``resource_id`` / ``relative_path`` remain supported for callers that
    already pass explicit values.

    Raises:
        404: Unknown job id.
        400: Unknown ``resource`` kind.
    """
    from acp_services.data.read_root import AvailabilityRead
    from acp_services.data.resource_read import RESOURCE_ADAPTERS

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        job_id=job_id,
        project_id=record.project_id or record.spec.project_id,
        resource_id=resource_id or resource or "",
    )
    resolved_attempt = attempt if attempt is not None else _record_read_attempt(record)
    resolved_workflow = workflow or str(record.spec.workflow or "")

    if resource is not None:
        try:
            adapter = RESOURCE_ADAPTERS[resource]
        except KeyError as exc:
            raise HTTPException(
                status_code=400, detail=f"Unknown resource kind: {resource}"
            ) from exc
        resolved_resource = resource_id or adapter.resource_id(resolved_workflow)
        resolved_path = "" if adapter.is_catalog() else adapter.resolve_relative_path(relative_path)
    else:
        resolved_resource = resource_id or f"catalog:{resolved_workflow}"
        resolved_path = relative_path

    reader = _read_only_reader(request)
    snapshot = reader.availability(
        AvailabilityRead(
            job_id=job_id,
            resource_id=resolved_resource,
            workflow=resolved_workflow,
            relative_path=resolved_path,
            attempt=resolved_attempt,
            expected_revision=expected_revision,
        )
    )
    return snapshot.to_dict()


def _artifact_error(exc: Any) -> HTTPException:
    """Map one Data artifact refusal onto the frozen command error.

    One mapping for every route that resolves prepared artifacts (the prepared
    read and the explicit analysis), so ``attempt_mismatch``/``digest_mismatch``/
    ``generation_stale``/``not_prepared``/``path_traversal`` mean the same thing
    at every entry (M2-02).
    """
    from acp_services.data.artifacts import (
        ArtifactAttemptMismatchError,
        ArtifactDigestMismatchError,
        ArtifactGenerationMismatchError,
        ArtifactNotPreparedError,
        ArtifactPathRefusedError,
    )

    if isinstance(exc, ArtifactPathRefusedError):
        return _command_error(
            400,
            "input_invalid",
            str(exc),
            params={"rule": "path_traversal", **exc.params},
        )
    if isinstance(exc, ArtifactGenerationMismatchError):
        return _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "generation_stale", **exc.params},
        )
    if isinstance(exc, ArtifactDigestMismatchError):
        return _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "digest_mismatch", **exc.params},
        )
    if isinstance(exc, ArtifactAttemptMismatchError):
        return _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "attempt_mismatch", **exc.params},
        )
    if isinstance(exc, ArtifactNotPreparedError):
        reason = str(exc.params.get("reason") or "not_prepared")
        return _command_error(
            409,
            "resource_not_ready",
            f"not_prepared: {reason}",
            params={"reason": reason, **exc.params},
            retryable=True,
        )
    return _command_error(
        int(getattr(exc, "http_status", 500)),
        "resource_not_ready" if int(getattr(exc, "http_status", 500)) == 409 else "input_invalid",
        str(exc),
        params=dict(getattr(exc, "params", {}) or {}),
    )


def _serve_prepared_artifact(
    request: Request,
    record: Any,
    *,
    job_id: str,
    artifact_id: str,
    attempt: int | None,
    generation: int,
    digest: str | None,
    encoding: str,
    max_bytes: int,
) -> JSONResponse:
    """Serve one prepared artifact from the controlled store (no fetch).

    Every refusal is named: a cold/evicted body is ``resource_not_ready`` with
    a ``not_prepared`` message, a stale generation / corrupt digest / cross-
    attempt read is ``revision_conflict`` with an explicit rule, and a
    traversing id is ``input_invalid``.  No branch enqueues, fetches or parses.
    A requested ``max_bytes`` above the frozen read bound is refused instead of
    served (``input_invalid``/``max_read_bytes_exceeded``).
    """
    from acp_services.data.artifacts import ArtifactError, ArtifactStore, resolve_artifact_read
    from acp_services.entrypoints.application_params import MAX_READ_BYTES

    if encoding not in {"identity", "base64"}:
        raise _command_error(
            400,
            "input_invalid",
            f"unsupported encoding {encoding!r}",
            params={"field": "encoding", "allowed": ["identity", "base64"]},
        )
    if not digest:
        raise _command_error(
            400,
            "input_invalid",
            "an artifact read requires artifact_ref.digest",
            params={"field": "digest", "rule": "required"},
        )
    if int(max_bytes) > MAX_READ_BYTES:
        raise _command_error(
            400,
            "input_invalid",
            f"max_bytes exceeds the frozen read bound ({MAX_READ_BYTES} bytes)",
            params={
                "field": "max_bytes",
                "rule": "max_read_bytes_exceeded",
                "limit": MAX_READ_BYTES,
                "requested": int(max_bytes),
            },
        )
    _require_entry_access(
        request,
        _EntryClass.RANGE,
        job_id=job_id,
        project_id=record.project_id or record.spec.project_id,
        action="read",
        resource_id=artifact_id,
    )
    manager = _manager(request)
    service = getattr(manager, "_prepare_operations", None)
    if service is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "prepare service is not available",
            retryable=True,
        )
    resolved_attempt = attempt if attempt is not None else _record_read_attempt(record)
    authorization = _artifact_authorization_projection(
        request,
        record,
        action=_AccessAction.READ,
        resource_id=artifact_id,
    )
    if not authorization.allowed:
        raise _command_error(
            403,
            "access_denied",
            "artifact read authorization denied",
            params={"rule": "permission_revoked"},
        )
    store = ArtifactStore(manager.run_root / "artifact-store")
    try:
        read = resolve_artifact_read(
            service,
            store,
            job_id=job_id,
            attempt=int(resolved_attempt or 0),
            resource_id=artifact_id,
            generation=int(generation),
            digest=str(digest),
            permission_scope="local-single",
            authorization=authorization,
            max_bytes=int(max_bytes),
            encoding=encoding,
        )
    except ArtifactError as exc:
        raise _artifact_error(exc) from exc
    return JSONResponse(content=read.to_dict())


@router.get("/jobs/{job_id}/prepared/{resource}")
def get_prepared_resource(
    job_id: str,
    resource: str,
    request: Request,
    relative_path: str = Query(default=""),
    expected_revision: int = Query(default=0, ge=0),
    attempt: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1 << 20, ge=1),
    artifact_id: str | None = Query(default=None),
    digest: str | None = Query(default=None),
    encoding: str = Query(default="identity"),
    max_bytes: int | None = Query(default=None, ge=1),
) -> Response:
    """Serve a prepared resource cache-only (B02-6 / todo 16 new read path).

    This is the switched **new read path** for the five resources
    (catalog/manifest/frame_candidates/frequency_modes/geometry).  It reads only
    the prepared cache index: zero SSH/SFTP, zero new prepare operations, zero
    parsing writes.  The catalog resource returns its availability snapshot as
    JSON; the byte resources return the prepared body (bounded to ``limit``).

    When the resource is not prepared (a cold cache) it returns 409
    ``not_prepared`` — the caller falls back to the retained legacy GET, which
    is the compatibility channel that keeps F0 clients working.  The legacy GETs
    are never modified or removed by this switch.

    Raises:
        404: Unknown job id.
        400: Unknown ``resource`` kind.
        409: The resource is not in the prepared cache index (use the legacy GET).
    """
    from acp_services.data.resource_read import RESOURCE_ADAPTERS

    if (
        resource not in RESOURCE_ADAPTERS
        and resource != "artifact"
        and artifact_id is None
        and digest is None
    ):
        # Preserve the frozen switch contract: an unknown resource kind is a
        # 400 before any job lookup.  An artifact read always carries a digest
        # (or an explicit artifact_id), which is how it is distinguished.
        raise HTTPException(status_code=400, detail=f"Unknown resource kind: {resource}")

    resolved_artifact = artifact_id or ("" if resource in RESOURCE_ADAPTERS else resource)
    if resource == "artifact":
        resolved_artifact = artifact_id or ""
    # A supplied artifact digest names an artifact read even when the id
    # collides with one of the five switch resource names (e.g. frequency_modes).
    if digest is not None and not resolved_artifact:
        resolved_artifact = artifact_id or resource
    if resource not in RESOURCE_ADAPTERS or resolved_artifact:
        if not resolved_artifact:
            raise _command_error(
                400,
                "input_invalid",
                "artifact read requires artifact_id",
                params={"field": "artifact_id", "rule": "required"},
            )
        record = _require_job_scope_access(
            request,
            job_id,
            action=_AccessAction.READ,
            resource_id=resolved_artifact,
        )
        return _serve_prepared_artifact(
            request,
            record,
            job_id=job_id,
            artifact_id=resolved_artifact,
            attempt=attempt,
            generation=expected_revision or 1,
            digest=digest,
            encoding=encoding,
            max_bytes=262144 if max_bytes is None else max_bytes,
        )

    record = _require_job_scope_access(
        request,
        job_id,
        action=_AccessAction.READ,
        resource_id=resource,
    )

    _require_entry_access(
        request,
        _EntryClass.RANGE,
        job_id=job_id,
        project_id=record.project_id or record.spec.project_id,
        resource_id=(f"{resource}:{relative_path}" if relative_path else resource),
    )
    resolved_attempt = attempt if attempt is not None else _record_read_attempt(record)

    reader = _cache_only_resource_reader(request)
    result = reader.serve(
        resource,
        job_id=job_id,
        workflow=str(record.spec.workflow or ""),
        relative_path=relative_path,
        expected_revision=expected_revision,
        attempt=resolved_attempt,
        offset=offset,
        limit=limit,
    )
    if not result.served:
        raise HTTPException(
            status_code=409,
            detail=f"not_prepared: {result.reason or result.kind}",
        )
    if result.data is not None:
        return Response(content=result.data, media_type="application/octet-stream")
    assert result.snapshot is not None
    return JSONResponse(content=result.snapshot.to_dict())


# ---------------------------------------------------------------------------
# B06 additive application-command routes
# ---------------------------------------------------------------------------


def _command_error(
    status_code: int,
    code: str,
    message: str,
    *,
    params: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if params:
        detail["params"] = params
    if retryable is not None:
        detail["retryable"] = retryable
    return HTTPException(status_code=status_code, detail=detail)


#: Field-policy rules that answer ``422`` (the frozen v1 ``extra=forbid`` result).
_FIELD_POLICY_RULES = frozenset({"unknown_field", "unsupported_server_path"})


def _field_policy_status(exc: Any) -> int:
    """Map an :class:`ApplicationParamError` onto its HTTP status.

    ``422`` for the two frozen field-policy rules, ``400`` for every other request
    shape violation (unchanged pre-M0-06 behaviour).
    """
    rule = str((getattr(exc, "params", {}) or {}).get("rule") or "")
    return 422 if rule in _FIELD_POLICY_RULES else 400


def _plan_digest(payload: Mapping[str, Any]) -> str:
    """Server-computed canonical digest of the frozen scientific plan.

    B16 replaces the pre-canonical ``json.dumps(default=str)`` helper with the
    single B03 implementation.  Only science-determining fields participate;
    a display name, project hint or extension never changes the digest.
    """
    from acp_services.entrypoints.application_params import normalize_preflight
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
    )
    from acp_services.runtime.canonical_digest import (
        plan_digest as canonical_plan_digest,
    )

    normalized = normalize_preflight(payload)
    definition = frozen_plan_definition(
        workflow=str(normalized["workflow"]),
        input=normalized.get("input") if isinstance(normalized.get("input"), Mapping) else {},
        method=normalized.get("method") if isinstance(normalized.get("method"), Mapping) else {},
        resources=(
            normalized.get("resources") if isinstance(normalized.get("resources"), Mapping) else {}
        ),
    )
    return canonical_plan_digest(definition).digest


def _approval_service(request: Request) -> Any:
    """One approval authority per Controller database (cached on the app)."""
    from acp_services.runtime.approvals import ApprovalService

    manager = _manager(request)
    db_path = str(manager.store.db_path)
    cached = getattr(request.app.state, "approval_service", None)
    if cached is None or str(getattr(cached, "db_path", "")) != db_path:
        cached = ApprovalService(manager.store.db_path)
        request.app.state.approval_service = cached
    return cached


def _submit_probe(req: Any) -> Any:
    """The M1-04b probe over every research-signalling submit field.

    One probe replaces the two field sets that used to disagree
    (``_is_research_launch`` vs ``_enforce_approval``): a request carrying only
    ``budget`` is research-signalling for *both* the project-scope branch and
    the approval branch.
    """
    from acp_services.runtime.submit_policy import SubmitProbe

    payload = {
        "workflow": getattr(req, "workflow", ""),
        "proposal_ref": getattr(req, "proposal_ref", None),
        "proposal_digest": getattr(req, "proposal_digest", None),
        "plan_digest": getattr(req, "plan_digest", None),
        "budget": getattr(req, "budget", None),
    }
    return SubmitProbe.from_payload(payload)


def _classify_submit(
    request: Request,
    req: Any,
    *,
    approval: Any = None,
    science_digest: str = "",
    defer_approval: bool = False,
) -> Any:
    """The single submit decision (M1-04b) for this request identity."""
    from acp_services.runtime.submit_policy import classify_submit

    identity = _command_identity(request, project_id=req.project_id)
    return classify_submit(
        _submit_probe(req),
        identity,
        approval=approval,
        science_digest=str(science_digest or ""),
        approval_science_digest=str(getattr(approval, "science_digest", "") or ""),
        defer_approval=defer_approval,
    )


def _deny_submit(classification: Any) -> HTTPException:
    """Render one submit denial in the frozen application error shape."""
    detail = classification.detail()
    return _command_error(
        classification.http_status,
        str(detail["code"]),
        str(detail["message"]),
        params=dict(detail["params"]),
    )


def _proposal_science_digest(request: Request, req: Any) -> str:
    """Compute the C ``plan_definition_digest`` of the referenced proposal.

    The Proposal is the scientific authority; it is read from the Data boundary
    and projected with B16's single canonicalization.  A missing/unreadable
    proposal returns ``""`` (unverifiable) — it is never treated as "matches".
    """
    from acp_services.runtime.canonical_digest import (
        CanonicalDigestError,
        proposal_plan_definition_digest,
    )

    proposal_ref = str(getattr(req, "proposal_ref", "") or "")
    if not proposal_ref:
        return ""
    store = getattr(request.app.state, "research_resources", None)
    if store is None:
        return ""
    from acp_services.data.research_resources import (
        ResearchResourceError,
        research_resource_key,
    )

    # The proposal lives in the resolved project; an absent payload claim uses
    # the same default the approval binding uses (never a client-chosen scope).
    project = str(getattr(req, "project_id", "") or _manager(request).default_project_id or "")
    principal = str(_server_identity(request).principal.principal_id)
    try:
        record = store.read(
            research_resource_key(project, "proposal", proposal_ref),
            principal_id=principal,
            project_id=project,
        )
    except (ResearchResourceError, KeyError, ValueError):
        return ""
    document = getattr(record, "document", None)
    if not isinstance(document, Mapping):
        return ""
    # G10 / M1-04a: a research launch never binds a science block whose critical
    # values are missing.  The single typed validator decides; a missing
    # charge/multiplicity is a clarification (never an implicit 0/1).
    science = document.get("science")
    if isinstance(science, Mapping):
        _require_typed_science(science)
    if not {"recipe_digest", "science", "budget"} <= set(document):
        return ""
    try:
        return proposal_plan_definition_digest(document).digest
    except CanonicalDigestError:
        return ""


def _require_typed_science(science: Mapping[str, Any]) -> None:
    """Validate a stored Proposal's science block (clarification, not 0/1)."""
    from acp_services.runtime.science_inputs import normalize_science_inputs

    payload: dict[str, Any] = {
        key: science[key]
        for key in ("method", "mapping", "charge", "multiplicity")
        if key in science
    }
    initial_inputs = science.get("initial_inputs")
    if isinstance(initial_inputs, list):
        payload["structure_provenance"] = [
            {
                "input_id": str(item.get("input_id") or ""),
                "sha256": str(item.get("sha256") or ""),
                "format": str(item.get("format") or ""),
                "role": str(item.get("role") or "minimum"),
            }
            for item in initial_inputs
            if isinstance(item, Mapping)
        ]
    verdict = normalize_science_inputs(payload)
    if verdict.values is not None:
        return
    if verdict.clarifications:
        raise _command_error(
            400,
            "input_invalid",
            "the proposal science is missing critical value(s): "
            + ", ".join(item.field_name for item in verdict.clarifications),
            params={
                "rule": "clarification_required",
                "fields": [item.field_name for item in verdict.clarifications],
            },
        )
    first = verdict.issues[0]
    raise _command_error(
        400,
        "input_invalid",
        first.message,
        params={"rule": first.rule, "field": first.field_name},
    )


def _enforce_approval(request: Request, req: Any, classification: Any = None) -> Any:
    """Check the frozen plan + approval binding before any submit side effect.

    M1-04b: the classification is the single decision point; this function only
    performs the digest/approval/scientific-binding work after the class is
    known.  Returns the authorizing :class:`Approval` (or ``None`` for a legacy
    submit) and raises the frozen approval/plan error shape.
    """
    from acp_services.runtime.approvals import ApprovalError
    from acp_services.runtime.canonical_digest import (
        CanonicalDigestError,
        frozen_plan_definition,
    )
    from acp_services.runtime.canonical_digest import (
        plan_digest as canonical_plan_digest,
    )

    decision = classification or _classify_submit(request, req)
    if not decision.research:
        return None
    if not decision.allowed:
        raise _deny_submit(decision)
    try:
        definition = frozen_plan_definition(
            workflow=str(req.workflow),
            input=req.input,
            method=req.method,
            resources=req.resources,
        )
        computed = canonical_plan_digest(definition).digest
    except CanonicalDigestError as exc:
        raise _command_error(
            400,
            "input_invalid",
            str(exc),
            params={"field": "plan", **exc.params()},
        ) from exc
    if computed != req.plan_digest:
        raise _command_error(
            400,
            "input_invalid",
            "plan_digest does not match the submitted scientific plan",
            params={"field": "plan_digest", "rule": "plan_digest_mismatch"},
        )
    manager = _manager(request)
    approval_project = req.project_id or manager.default_project_id
    principal = str(_server_identity(request).principal.principal_id)
    try:
        approval = _approval_service(request).authorize_submission(
            plan_digest=req.plan_digest,
            proposal_ref=str(req.proposal_ref),
            project_id=str(approval_project or ""),
            requester_principal=principal,
            proposal_digest=req.proposal_digest,
            budget=req.budget,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.detail()) from exc
    # D06 (M1-04a): the approval binds the scientific digest *and* the execution
    # digest.  Only read/project the proposal when the approval actually carries a
    # science digest, so a legacy execution-only approval keeps its behaviour.
    science_digest = ""
    if getattr(approval, "science_digest", ""):
        science_digest = _proposal_science_digest(request, req)
    final = _classify_submit(request, req, approval=approval, science_digest=science_digest)
    if not final.allowed:
        raise _deny_submit(final)
    return approval


def _budget_claim_from_approval(approval: Any) -> dict[str, Any] | None:
    """Server-side research budget quote derived from the approved record.

    The client's ``budget`` payload is only compared with the approval digest by
    B16; the effective limits come from the persisted approval, never from the
    request.  The budget id is project+proposal (independent of ``run_id``).

    M2-03c: the approved budget is normalized through the one mapping
    (:mod:`acp_services.runtime.budget_projection`), so a non-empty unknown shape
    or an unsupported launch unit is a typed 400/422 here — never a 500 inside
    the ledger and never "no limits".  The resolved deadline projection travels
    with the claim so the dispatcher has one value for all three surfaces.
    """
    if approval is None:
        return None
    approved_budget = getattr(approval, "budget", None)
    if not approved_budget:
        return None
    from acp_services.runtime.budget_projection import (
        BudgetRequestError,
        budget_request_from_approval,
    )
    from acp_services.runtime.research_budget import budget_id_for

    try:
        request = budget_request_from_approval(approved_budget)
    except BudgetRequestError as exc:
        raise _command_error(
            exc.http_status,
            "input_invalid",
            exc.message,
            params=dict(exc.params),
        ) from exc
    return {
        "budget_id": budget_id_for(str(approval.project_id), str(approval.proposal_ref)),
        "budget": dict(approved_budget),
        "approved_budget": dict(approved_budget),
        "approval_ref": str(approval.approval_id),
        "project_id": str(approval.project_id),
        "execution_limits": request.resources.to_dict(),
    }


def _research_project(manager: JobManager, project_id: str | None) -> str | None:
    """Resolve a research launch project claim server-side.

    A research launch never trusts a payload project claim blindly: an unknown
    project is ``access_denied`` with ``rule=project_scope_mismatch``.  Legacy
    (non-research) submissions keep their historical behaviour.
    """
    if project_id is None:
        return None
    project = manager.projects.get_project(project_id)
    if project is None:
        raise _command_error(
            403,
            "access_denied",
            "project_scope_mismatch",
            params={"rule": "project_scope_mismatch", "path": "project_id"},
        )
    return project_id


#: C1 (round-4 plan): a geometry-fact advisory, never a scientific criterion.
#: The thresholds are versioned, uncalibrated policy parameters (astra review).
_SCAN_ADVISORY_POLICY = {
    "id": "scan_single_coordinate_geometry",
    "version": "1",
    "calibrated": False,
    "note": "heuristic prompt policy parameter, not a scientific criterion",
}


def _coerce_scan_coordinates(
    payload: Mapping[str, Any], method: Mapping[str, Any]
) -> list[tuple[str, float, float]]:
    """Read only *explicitly normalized* coordinate facts (dict form).

    The CLI string form (``"a,b,start,end"``) is interpreted by the scheduling
    consumer, not here: re-parsing it would create a second interpretation of
    the same payload (a 3-atom entry is an angle, not a distance).  Only the
    structured dict form — which carries its own ``kind`` — is a trustworthy
    fact for an advisory.
    """
    candidates: list[Any] = []
    for source in (payload, method):
        for key in ("scan_coordinates", "coordinates", "coordinate"):
            candidate = source.get(key)
            if candidate is not None:
                candidates.append(candidate)
    # the nested PES envelope is also a real entry (input.scan_request.coordinate)
    scan_request = payload.get("scan_request")
    if isinstance(scan_request, Mapping):
        nested = scan_request.get("coordinate")
        if nested is not None:
            candidates.append(nested)
    parsed: list[tuple[str, float, float]] = []
    for raw in candidates:
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        for item in items:
            if not isinstance(item, Mapping):
                # never interpret the string form here
                continue
            kind = str(item.get("kind") or "").strip().lower()
            start, end = item.get("start"), item.get("end")
            if not kind:
                continue
            try:
                parsed.append((kind, float(start), float(end)))
            except (TypeError, ValueError):
                continue
    return parsed


def _scan_geometry_advisories(
    workflow: str, payload: Mapping[str, Any], method: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Geometry-fact advisories for relaxed scans (PESsearch / scan).

    Trigger (all): a bond-length scan, exactly one distance coordinate, and a
    start-to-end span of at least 2.5 A.  The text states the geometry and a
    conditional consequence; it never asserts the chemistry, never blocks, and
    carries the uncalibrated policy id so a false positive is attributable.
    """
    if workflow not in ("PESsearch", "scan"):
        return []
    coordinates = _coerce_scan_coordinates(payload, method)
    if len(coordinates) != 1:
        return []
    kind, start, end = coordinates[0]
    if kind not in ("distance", "bond_stretch"):
        return []
    if abs(end - start) < 2.5:
        return []
    return [
        {
            "id": "scan_single_coordinate_geometry",
            "severity": "info",
            "policy": dict(_SCAN_ADVISORY_POLICY),
            "message": (
                f"this plan drives one distance coordinate only ({start:.2f} -> {end:.2f} A). "
                "The scan does not guarantee coverage of every relevant structural change of "
                "the target reaction; the curve maximum is an initial guess only. If the "
                "reaction hypothesis involves several bonds changing, inspect how they "
                "evolve along the path and read the multi-coordinate guidance "
                "(task.find_transition_state)."
            ),
            "references": [
                {
                    "tool": "acp_get_guidance",
                    "arguments": {"guide_id": "task.find_transition_state"},
                },
                {"tool": "acp_get_guidance", "arguments": {"guide_id": "workflow.PESsearch"}},
            ],
        }
    ]


def _preflight_source_facts(
    request: Request, workflow: str, payload: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read-only source feasibility for tsmode/irc (preflight, zero side effects).

    Returns ``(reasons, advisories)``.  Reasons mark a *deterministically*
    infeasible local source; advisories mark states that cannot be verified
    without touching remote material (``needs_prepare``/``not_verified``) and
    must never be reported as unconditionally feasible.  The checks mirror the
    submit-time selection: the *selected* entry/TS product, not just "any entry
    in the job".
    """
    reasons: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    if workflow not in ("tsmode", "irc"):
        return reasons, advisories
    source_job_id = payload.get("source_job_id")
    ref = payload.get("source_ref")
    if (not isinstance(source_job_id, str) or not source_job_id) and isinstance(ref, Mapping):
        source_id = str(ref.get("source_id") or "")
        prefix, separator, relative = source_id.partition(":")
        if separator and prefix.startswith("job_") and relative.startswith("RESULT/"):
            source_job_id = prefix.removeprefix("job_")
    if not isinstance(source_job_id, str) or not source_job_id:
        return reasons, advisories
    # Reuse the shared job-scope authorization: a job from another project must
    # look exactly like an unknown job (existence never leaks across projects).
    try:
        record = _require_job_scope_access(request, source_job_id, require_exists=True)
    except HTTPException:
        reasons.append(
            {
                "code": "source_job_not_found",
                "message": f"source job {source_job_id!r} does not exist",
                "field": "source_job_id",
                "rule": "source_job_unavailable",
                "params": {"source_job_id": source_job_id},
            }
        )
        return reasons, advisories
    if record is None:
        return reasons, advisories
    # A remote or not-yet-prepared source cannot be verified here: it is a
    # submit/prepare decision and must be reported as such, never as feasible.
    if _is_remote_job(record) or not getattr(record, "work_dir", None):
        advisories.append(
            {
                "id": "source_not_verified",
                "severity": "info",
                "message": (
                    f"source job {source_job_id!r} is remote or not materialized on this "
                    "host; the submit/prepare step will decide"
                ),
                "policy": {"id": "source_not_verified", "version": "1", "calibrated": True},
            }
        )
        return reasons, advisories
    from pathlib import Path

    work_path = Path(record.work_dir)
    if workflow == "tsmode":
        try:
            sources = _discover_frequency_sources(work_path, source_job_id)
        except (OSError, ValueError, TypeError):
            advisories.append(
                {
                    "id": "source_not_verified",
                    "severity": "info",
                    "message": f"frequency sources of {source_job_id!r} could not be read here",
                    "policy": {"id": "source_not_verified", "version": "1", "calibrated": True},
                }
            )
            return reasons, advisories
        if not sources:
            reasons.append(
                {
                    "code": "frequency_source_incomplete",
                    "message": (
                        f"source job {source_job_id!r} has no discoverable frequency source "
                        "(need an ORCA frequency output and its Hessian)"
                    ),
                    "field": "source_job_id",
                    "rule": "frequency_source_unavailable",
                    "params": {"source_job_id": source_job_id},
                }
            )
            return reasons, advisories
        entry_id = payload.get("entry_id")
        available = [source.entry_id for source in sources]
        if isinstance(entry_id, str) and entry_id:
            matched = next((source for source in sources if source.entry_id == entry_id), None)
            if matched is None:
                reasons.append(
                    {
                        "code": "frequency_source_incomplete",
                        "message": (
                            f"frequency entry {entry_id!r} not found in {source_job_id!r}; "
                            f"available: {available}"
                        ),
                        "field": "entry_id",
                        "rule": "frequency_entry_unavailable",
                        "params": {"entry_id": entry_id, "available": available},
                    }
                )
                return reasons, advisories
            if not matched.hessian_available:
                reasons.append(
                    {
                        "code": "frequency_source_incomplete",
                        "message": (
                            f"frequency entry {entry_id!r} has no Hessian; tsmode needs it "
                            "to map the mode"
                        ),
                        "field": "entry_id",
                        "rule": "frequency_hessian_missing",
                        "params": {"entry_id": entry_id},
                    }
                )
            return reasons, advisories
        if len(sources) == 1:
            if not sources[0].hessian_available:
                reasons.append(
                    {
                        "code": "frequency_source_incomplete",
                        "message": (
                            f"the only frequency source in {source_job_id!r} has no Hessian"
                        ),
                        "field": "source_job_id",
                        "rule": "frequency_hessian_missing",
                        "params": {"source_job_id": source_job_id},
                    }
                )
            return reasons, advisories
        reasons.append(
            {
                "code": "frequency_source_incomplete",
                "message": (
                    f"source job {source_job_id!r} has several frequency entries; pass "
                    f"entry_id (available: {available})"
                ),
                "field": "entry_id",
                "rule": "frequency_entry_required",
                "params": {"available": available},
            }
        )
        return reasons, advisories
    # irc: only the local manifest is judged (no remote fetch during preflight)
    from acp.calculations.irc.source import load_ts_result_manifest

    try:
        manifest = load_ts_result_manifest(record, None)
    except (OSError, ValueError, TypeError):
        advisories.append(
            {
                "id": "source_not_verified",
                "severity": "info",
                "message": f"TS result manifest of {source_job_id!r} could not be read here",
                "policy": {"id": "source_not_verified", "version": "1", "calibrated": True},
            }
        )
        return reasons, advisories
    if manifest is None:
        reasons.append(
            {
                "code": "ts_source_unavailable",
                "message": f"source job {source_job_id!r} has no local TS result manifest",
                "field": "source_job_id",
                "rule": "ts_source_unavailable",
                "params": {"source_job_id": source_job_id},
            }
        )
        return reasons, advisories
    product_id = payload.get("source_product_id")
    relative: str | None = None
    if isinstance(ref, Mapping):
        source_id = str(ref.get("source_id") or "")
        _, separator, path = source_id.partition(":")
        if separator and path.startswith("RESULT/"):
            relative = path.removeprefix("RESULT/")
    if isinstance(product_id, str) and product_id:
        product = next((item for item in manifest.products if item.id == product_id), None)
        if product is None:
            reasons.append(
                {
                    "code": "ts_source_unavailable",
                    "message": f"source product {product_id!r} is not registered as a TS product",
                    "field": "source_product_id",
                    "rule": "ts_product_not_registered",
                    "params": {"source_product_id": product_id},
                }
            )
    elif relative is not None:
        product = next((item for item in manifest.products if item.path == relative), None)
        if product is None:
            reasons.append(
                {
                    "code": "ts_source_unavailable",
                    "message": f"source reference {relative!r} is not a registered TS product",
                    "field": "source_ref",
                    "rule": "ts_product_not_registered",
                    "params": {"relative_path": relative},
                }
            )
    return reasons, advisories


def _preflight_projection(req: V1PreflightRequest, request: Request) -> dict[str, Any]:
    """Build the zero-side-effect preflight projection.

    Entry access is authorized by the thin :func:`preflight_request` wrapper;
    everything after that check lives here unchanged so the B1-3 science
    preflight can reuse the single implementation.

    M0-06: the same exhaustive field policy and the same execution-target checks
    as :func:`create_job` run here first, so a typo'd field or an unknown remote
    node is reported identically before and after a submit.  The response key set
    stays frozen (B1-3); an infeasible target is projected into ``reasons`` with
    the application ``input_invalid`` code plus the fine-grained rule.
    """
    from acp_services.entrypoints.application_params import (
        ApplicationParamError,
        normalize_preflight,
        validate_request_fields,
    )

    if req.workflow not in ALL_WORKFLOWS:
        raise _command_error(
            400,
            "input_invalid",
            f"Unsupported workflow '{req.workflow}'",
            params={"field": "workflow", "allowed": list(PUBLIC_WORKFLOWS)},
        )
    _normalize_legacy_simple_method(req)
    raw_input = req.input
    payload = req.model_dump(exclude_none=True)
    if raw_input is None:
        payload["input"] = {}
    try:
        validate_request_fields(payload, request_kind="preflight", entry="http")
        normalize_preflight(payload)
    except ApplicationParamError as exc:
        raise _command_error(
            _field_policy_status(exc), exc.code, exc.message, params=dict(exc.params)
        ) from exc
    try:
        _validate_simple_method_cli(req.workflow, req.method)
        _validate_scan_submission(req.workflow, req.method, req.input)
        if req.workflow == "BatchOptimize":
            # the single_flat cardinality gate belongs to preflight too: a
            # two-structure manifest must fail here, not after a submit.
            _validate_batch_cardinality(dict(raw_input or {}))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        raise _command_error(
            int(exc.status_code),
            str(detail.get("code") or "input_invalid"),
            str(detail.get("message") or exc.detail),
            params=dict(detail.get("params") or {}),
        ) from exc
    # Same semantic contract as create_job, before any snapshot/estimate work.
    req.input = req.input or {}
    try:
        validate_workflow_submission(
            req.workflow, input=raw_input, method=req.method, resources=req.resources
        )
    except SubmissionContractError as exc:
        raise _command_error(422, "input_invalid", str(exc), params=exc.params()) from exc
    try:
        validate_resource_request(req.resources or {})
    except ValueError as exc:
        raise _command_error(
            422, "input_invalid", str(exc), params={"field": "resources", "rule": "invalid_resource"}
        ) from exc
    manager = _manager(request)
    project_id = _research_project(manager, req.project_id)
    spec = JobSpec(
        workflow=req.workflow,
        input=req.input,
        method=req.method,
        resources=req.resources,
        project_id=project_id,
        execution_mode=req.execution_mode,
        target_node=req.target_node or None,
        node_tags=list(req.node_tags),
    )
    reasons: list[dict[str, Any]] = []
    feasible = True
    # B2 (round-4 plan): source-feasibility checks for step workflows, using only
    # locally-verifiable facts.  Remote material stays a submit/prepare decision —
    # the prepare chain is the legitimate path, so a cold remote source is never
    # reported infeasible; it is surfaced as a not-verified advisory instead.
    _source_reasons, _source_advisories = _preflight_source_facts(
        request, req.workflow, raw_input or {}
    )
    for source_reason in _source_reasons:
        feasible = False
        reasons.append(source_reason)
    try:
        validate_execution_request(spec)
        # Only a target the client actually chose is validated here: an auto job's
        # capability resolution stays a submit-time decision (it depends on probe
        # state, not on the request), while a pinned node / explicit remote is the
        # same permanent check the submit entry applies.
        if req.target_node or req.execution_mode == "remote":
            validate_submission_target(spec, registry=manager.registry)
    except (ExecutionTargetError, NoCapableNodeError) as exc:
        from acp.scheduler.nodes import target_error_envelope

        feasible = False
        envelope = target_error_envelope(exc)
        params = dict(envelope["params"])
        reasons.append(
            {
                "code": envelope["code"],
                "message": envelope["message"],
                "field": params.get("field"),
                "rule": params.get("rule"),
                "params": params,
            }
        )
    digest = None
    if feasible:
        from acp_services.runtime.canonical_digest import CanonicalDigestError

        try:
            digest = _plan_digest(payload)
        except CanonicalDigestError as exc:
            raise _command_error(
                400,
                "input_invalid",
                str(exc),
                params={"field": "plan", **exc.params()},
            ) from exc
    advisories = [
        *_scan_geometry_advisories(req.workflow, raw_input or {}, req.method or {}),
        *_source_advisories,
    ]
    # Round-5 PR-03: one read-only analysis shared with submit/edit; the
    # requested/effective/executed receipt never claims an execution fact.
    submission_analysis: dict[str, Any] | None = None
    try:
        from acp.calculations.submission_validation import analysis_for

        analysis = analysis_for(req.workflow, raw_input or {}, req.method or {})
    except ValueError as exc:
        params = (
            exc.params
            if hasattr(exc, "params") and isinstance(getattr(exc, "params"), dict)
            else {"field": "input", "rule": "invalid_parameter"}
        )
        raise _command_error(422, "input_invalid", str(exc), params=params) from exc
    if analysis is not None:
        submission_analysis = analysis.to_dict()
        submission_analysis["requested"] = {
            "input": dict(raw_input or {}),
            "method": dict(req.method or {}),
        }
        submission_analysis["executed"] = None
        submission_analysis["unverified_checks"] = [
            str(item.get("id")) for item in _source_advisories if item.get("id")
        ]
    return {
        "schema_version": 1,
        "feasible": feasible,
        "reasons": reasons,
        "advisories": advisories,
        "submission_analysis": submission_analysis,
        "plan_digest": digest,
        "plan_summary": {
            "workflow": req.workflow,
            "steps": 1,
            "project_id": project_id,
        },
        "estimate": dict(req.resources or {}),
        "admission": None,
        "would_create_task": False,
        "would_reserve": False,
    }


@router.post("/preflight")
def preflight_request(req: V1PreflightRequest, request: Request) -> dict[str, Any]:
    """Zero-side-effect feasibility preflight for a would-be submit.

    Runs only the existing synchronous task/method validation.  It creates no
    task, no attempt, no prepare operation and no reservation, and it never
    starts science.
    """
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        resource_id="preflight",
    )
    return _preflight_projection(req, request)


def _artifact_authorization_projection(
    request: Request, record: Any, *, action: str, resource_id: str
) -> Any:
    """Build the inert authorization projection frozen onto an ArtifactRef."""
    from acp_services.data.artifacts import ArtifactAuthorization

    identity = _server_identity(request)
    project = record.project_id or record.spec.project_id
    decision = identity.authorize_job_project(
        project,
        action=action,
        job_id=record.id,
        resource_id=resource_id,
    )
    return ArtifactAuthorization(
        principal_id=str(getattr(identity.principal, "principal_id", "") or ""),
        project_id=str(project or ""),
        scope=str(getattr(record, "permission_scope", "") or "local-single"),
        action=str(action),
        allowed=bool(decision.allowed),
        authorization_version=int(getattr(record, "revision", 0) or 0),
    )


def _operation_response(
    record: Any,
    *,
    purpose: str,
    authorization: Any = None,
) -> dict[str, Any]:
    contract = record.to_contract()
    operation_ref = {"kind": "operation", "id": record.operation_id}
    payload = dict(record.payload or {})
    response: dict[str, Any] = {
        "schema_version": 1,
        "purpose": purpose,
        "operation_ref": operation_ref,
        "operation_id": record.operation_id,
        "idempotency_key": record.idempotency_key,
        "job_id": record.job_id,
        "attempt": record.attempt,
        "resource_id": record.resource_id,
        "resource_kind": record.resource_kind,
        "state": contract.state.value,
        "availability": contract.availability.value,
        "freshness": contract.freshness.value,
        "basis_revision": contract.basis_revision,
        "expected_revision": contract.expected_revision,
        "observed_at": contract.observed_at,
        "error": contract.error,
        "bytes": contract.bytes,
        "sha256": contract.sha256,
        "updated_at": record.updated_at,
        "idempotent_replay": False,
    }
    artifact_id = str(payload.get("artifact_id") or "")
    if artifact_id:
        from acp_services.data.artifacts import operation_to_artifact_ref

        artifact = operation_to_artifact_ref(record, authorization=authorization)
        if artifact is not None:
            response["artifact_ref"] = artifact.to_dict()
            response["resource"] = artifact.to_dict()
            response["schema"] = artifact.schema
            response["version"] = artifact.version
            response["generation"] = artifact.generation
            response["digest"] = artifact.digest
            response["size_bytes"] = artifact.size_bytes
            response["availability"] = artifact.availability.value
            response["source_schema"] = artifact.source_schema
            response["source_refs"] = [dict(item) for item in artifact.source_refs]
        else:
            response["artifact_ref"] = {
                "task_id": record.job_id,
                "attempt": record.attempt,
                "artifact_id": artifact_id,
                "generation": record.resource_generation,
                "digest": record.resource_digest,
                "relative_path": payload.get("relative_path") or None,
            }
    return response


def _fast_path_artifact_ref(
    manager: Any, job_id: str, artifact_id: str, attempt: int
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Project an already-published prepare operation onto a readable ref.

    Round-6 P1: the fast path returned ``ready`` without a five-tuple, so a
    second ``prepare`` could never feed ``read``.  Digest/generation come from
    the authoritative prepare row + artifact store, never computed here.
    """
    from acp_services.data.artifacts import ArtifactStore, operation_to_artifact_ref
    from acp_services.data.operations import PrepareState

    service = getattr(manager, "_prepare_operations", None)
    if service is None:
        return None
    existing = service.find(
        job_id=job_id,
        resource_id=artifact_id,
        attempt=attempt,
        permission_scope="local-single",
    )
    if existing is None or existing.state is not PrepareState.SUCCEEDED:
        return None
    store = ArtifactStore(manager.run_root / "artifact-store")
    published = store.lookup(
        job_id=job_id,
        attempt=existing.attempt,
        resource_id=artifact_id,
        generation=existing.resource_generation,
        digest="sha256:" + str(existing.sha256 or ""),
    )
    if published is None:
        return None
    artifact = operation_to_artifact_ref(existing)
    if artifact is None:
        return None
    return artifact.to_dict(), {"kind": "operation", "id": existing.operation_id}


def _prepare_fast_path(
    request: Request,
    record: Any,
    job_id: str,
    *,
    purpose: str,
    artifact_id: str,
    relative_path: str,
    attempt: int,
    expected_revision: int,
) -> dict[str, Any] | None:
    """Return a ready receipt for an already-prepared local artifact (PR-09).

    Uses the existing cache-only Data reader (validation + prepared index), never
    an ad-hoc ``exists()`` check; a remote or unprepared artifact falls through
    to the durable operation path unchanged.
    """
    if purpose != "artifact":
        return None
    if _is_remote_job(record):
        return None
    status_value = str(getattr(getattr(record, "status", ""), "value", getattr(record, "status", "")))
    if status_value not in {"completed", "failed", "cancelled"}:
        # only a terminal attempt can have verified, already-published products
        return None
    try:
        reader = _cache_only_resource_reader(request)
        snapshot = reader.availability(
            "artifact",
            job_id=job_id,
            workflow=str(record.spec.workflow or ""),
            relative_path=relative_path,
            expected_revision=expected_revision,
            attempt=attempt,
        )
    except Exception:  # an unavailable reader is not a fast-path failure
        return None
    if not snapshot.is_readable():
        return None
    freshness = str(getattr(snapshot.freshness, "value", snapshot.freshness))
    if freshness == "stale":
        return None
    receipt: dict[str, Any] = {
        "schema_version": "acp2.prepare.v1",
        "status": "ready",
        "fast_path": True,
        "job_id": job_id,
        "attempt": attempt,
        "artifact_id": artifact_id,
        "availability": snapshot.to_dict(),
        "read": f"/api/v1/jobs/{job_id}/prepared/artifact?artifact_id={artifact_id}",
        "reused_prepared": True,
    }
    try:
        manager = _manager(request)
    except (AttributeError, KeyError, RuntimeError):
        # No resolvable manager: never hand out a ready receipt without a
        # readable five-tuple; let the durable prepare path rebuild it.
        return None
    ref = _fast_path_artifact_ref(manager, job_id, artifact_id, attempt)
    if ref is None:
        # readable but no authoritative published ref: rebuild via prepare.
        return None
    receipt["artifact_ref"], receipt["operation_ref"] = ref
    return receipt


@router.post("/jobs/{job_id}/prepare")
def prepare_artifact(
    job_id: str,
    req: V1PrepareRequest,
    request: Request,
) -> dict[str, Any]:
    """Explicitly enqueue one persistent prepare operation.

    The operation is durable (``data_operations`` row) and deduplicated by its
    frozen content identity.  The route never fetches a remote body itself and
    never starts science; a later explicit read is cache-only.
    """
    from acp_services.data.contracts import ResourceKind
    from acp_services.data.operations import (
        PrepareOperationConflictError,
        PrepareOperationError,
        PrepareRequest,
    )
    from acp_services.entrypoints.application_params import ApplicationParamError, normalize_prepare

    manager = _manager(request)
    record = manager.get(job_id)
    if record is None:
        raise _command_error(
            404,
            "resource_not_ready",
            f"not_prepared: job {job_id!r} does not exist",
            params={"job_id": job_id},
            retryable=True,
        )
    decision = _server_identity(request).authorize_job_project(
        record.project_id or record.spec.project_id,
        action=_AccessAction.WRITE,
        job_id=job_id,
        resource_id=req.artifact_id or req.purpose,
    )
    if not decision.allowed:
        raise _command_error(
            404,
            "resource_not_ready",
            f"not_prepared: job {job_id!r} does not exist",
            params={"job_id": job_id},
            retryable=True,
        )
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        job_id=job_id,
        project_id=record.project_id or record.spec.project_id,
        action="write",
        resource_id=req.artifact_id or req.purpose,
    )
    payload = req.model_dump(exclude_none=True)
    try:
        normalized = normalize_prepare(payload)
    except ApplicationParamError as exc:
        raise _command_error(400, exc.code, exc.message, params=dict(exc.params)) from exc
    claimed = normalized.get("job_id") or (normalized.get("task_ref") or {}).get("task_id")
    if claimed and claimed != job_id:
        raise _command_error(
            409,
            "revision_conflict",
            "prepare target does not match the job in the path",
            params={"path_job_id": job_id, "claimed_job_id": claimed},
        )
    task_ref = normalized.get("task_ref") or {}
    attempt = int(normalized.get("attempt") or task_ref.get("attempt") or record.attempt or 1)
    if attempt > int(record.attempt or 0):
        raise _command_error(
            400,
            "input_invalid",
            f"attempt {attempt} is newer than the current attempt",
            params={"field": "attempt", "rule": "attempt_not_current"},
        )
    purpose = str(normalized["purpose"])
    artifact_id = str(normalized.get("artifact_id") or "")
    relative_path = str(normalized.get("relative_path") or "")
    workflow = str(record.spec.workflow or "")
    generation = int(normalized.get("generation") or (1 if purpose == "artifact" else 0))
    expected_digest = ""
    evidence_report = normalized.get("evidence_report")
    if purpose == "artifact":
        from acp_services.data.artifacts import (
            ArtifactError,
            ArtifactPathRefusedError,
            is_safe_artifact_token,
            normalize_artifact_digest,
            validate_artifact_relative_path,
        )

        if not is_safe_artifact_token(artifact_id):
            raise _command_error(
                400,
                "input_invalid",
                f"unsafe artifact id: {artifact_id!r}",
                params={"field": "artifact_id", "rule": "path_traversal"},
            )
        if relative_path and evidence_report is None:
            try:
                relative_path = validate_artifact_relative_path(relative_path)
            except ArtifactPathRefusedError as exc:
                raise _command_error(
                    400,
                    "input_invalid",
                    str(exc),
                    params={"field": "relative_path", "rule": "path_traversal"},
                ) from exc
        try:
            expected_digest = normalize_artifact_digest(
                str(normalized.get("expected_digest") or normalized.get("content_digest") or "")
            )
        except ArtifactError as exc:
            raise _command_error(
                400,
                "input_invalid",
                str(exc),
                params={"field": "expected_digest", "rule": "sha256"},
            ) from exc
        resource_id = artifact_id
        resource_kind = ResourceKind.ARTIFACT
    else:
        resource_id = f"structure-input:{relative_path or 'input.xyz'}"
        resource_kind = ResourceKind.INPUT
    fast_ready = _prepare_fast_path(
        request,
        record,
        job_id,
        purpose=purpose,
        artifact_id=artifact_id,
        relative_path=relative_path,
        attempt=attempt,
        expected_revision=int(normalized.get("expected_revision") or 0),
    )
    if fast_ready is not None:
        return fast_ready
    identity = f"{job_id}\x1f{attempt}\x1f{resource_id}".encode()
    resource_digest = expected_digest or ("sha256:" + hashlib.sha256(identity).hexdigest())
    service = getattr(manager, "_prepare_operations", None)
    if service is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "prepare service is not available",
            retryable=True,
        )
    if purpose == "artifact":
        # An evicted body must not keep a SUCCEEDED operation replaying forever:
        # expire the bodyless success so an explicit re-prepare opens a new
        # dedup epoch and rebuilds the artifact.  A read never does this.
        from acp_services.data.artifacts import ArtifactStore
        from acp_services.data.operations import PrepareState

        existing = service.find(
            job_id=job_id,
            resource_id=resource_id,
            attempt=attempt,
            permission_scope="local-single",
        )
        if existing is not None and existing.state is PrepareState.SUCCEEDED:
            store = ArtifactStore(manager.run_root / "artifact-store")
            published = store.lookup(
                job_id=job_id,
                attempt=existing.attempt,
                resource_id=resource_id,
                generation=existing.resource_generation,
                digest="sha256:" + str(existing.sha256 or ""),
            )
            if published is None:
                service.expire_succeeded_for_resource(
                    job_id=job_id,
                    attempt=existing.attempt,
                    resource_id=resource_id,
                    permission_scope="local-single",
                    reason="artifact_body_missing",
                )
    artifact_payload: dict[str, Any] = {
        "purpose": purpose,
        "workflow": workflow,
        "job_id": job_id,
        "attempt": attempt,
        "artifact_id": artifact_id,
        "relative_path": relative_path,
        "generation": generation,
        "expected_digest": expected_digest,
        "media_type": str(normalized.get("media_type") or ""),
        "source_schema": str(normalized.get("schema_ref") or ""),
        "source_refs": list(normalized.get("source_refs") or []),
    }
    if normalized.get("expected_size_bytes") is not None:
        artifact_payload["expected_size_bytes"] = int(normalized["expected_size_bytes"])
    if evidence_report is not None:
        artifact_payload["evidence_report"] = dict(evidence_report)
    # M0-05: the prepare key is versioned by the server-derived identity.  The
    # principal/project are resolved here (never from the request body); the
    # transport permission scope stays the deployment attribute.
    prepare_project = str(record.project_id or record.spec.project_id or "")
    prepare_principal = _command_identity(request, project_id=prepare_project or None).principal_id
    prepare_request = PrepareRequest(
        job_id=job_id,
        attempt=attempt,
        resource_id=resource_id,
        resource_kind=resource_kind,
        resource_digest=resource_digest,
        permission_scope="local-single",
        resource_generation=generation,
        payload=artifact_payload,
        client_idempotency_key=str(normalized.get("idempotency_key") or ""),
        principal_id=prepare_principal,
        project_id=prepare_project,
    )
    try:
        operation = service.enqueue(prepare_request)
    except PrepareOperationConflictError as exc:
        raise _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": "idempotency_key_reuse"},
        ) from exc
    except PrepareOperationError as exc:
        raise _command_error(503, "backend_unavailable", str(exc), retryable=True) from exc
    if purpose == "artifact" and attempt > 1:
        try:
            service.expire_older_attempts(
                job_id=job_id,
                attempt=attempt,
                resource_id=resource_id,
                permission_scope="local-single",
            )
        except PrepareOperationError:
            pass
    ensure_worker = getattr(manager, "ensure_prepare_worker", None)
    if callable(ensure_worker):
        ensure_worker()
    authorization = _artifact_authorization_projection(
        request, record, action=_AccessAction.WRITE, resource_id=resource_id
    )
    return _operation_response(operation, purpose=purpose, authorization=authorization)


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str, request: Request) -> dict[str, Any]:
    """Return one persistent prepare-operation projection (query-only)."""
    manager = _manager(request)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        action="read",
        resource_id=operation_id,
    )
    service = getattr(manager, "_prepare_operations", None)
    if service is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "prepare service is not available",
            retryable=True,
        )
    record = service.get(operation_id)
    if record is None:
        raise _command_error(
            404,
            "resource_not_ready",
            f"not_prepared: unknown operation {operation_id!r}",
            params={"operation_id": operation_id},
            retryable=True,
        )
    purpose = str((record.payload or {}).get("purpose") or "artifact")
    authorization = None
    job = manager.get(record.job_id)
    if job is not None:
        _require_job_scope_access(
            request,
            record.job_id,
            action=_AccessAction.READ,
            resource_id=record.resource_id,
        )
        authorization = _artifact_authorization_projection(
            request, job, action=_AccessAction.READ, resource_id=record.resource_id
        )
    return _operation_response(record, purpose=purpose, authorization=authorization)


@router.post("/jobs/{job_id}/analyze")
def analyze_job(
    job_id: str,
    req: V1AnalyzeRequest,
    request: Request,
) -> dict[str, Any]:
    """Registry-gated explicit analysis over prepared artifacts (M2-02).

    The capability registry is the **application-level** one injected by the
    composition root (``app.state.analysis_registry``) — never the module-level
    default.  Only ``state=executable`` runs: an unregistered id and a
    registered-but-not-executable id (``vibration.compute``, which is a compute
    task) are distinct typed refusals, and neither starts anything.

    A ``vibration.analyze`` handler reads only prepared artifacts of *this* task:
    each ``input_refs`` entry must be the full ArtifactRef tuple whose
    ``task_id`` equals the route's job id, and the handler re-verifies the
    prepare operation + published body digest before parsing.  Nothing here
    creates a task or calls a submit path.
    """
    from acp.api import science_catalog
    from acp_services.data.artifacts import ArtifactError
    from acp_services.entrypoints.application_params import (
        ApplicationParamError,
        normalize_analyze,
    )
    from acp_services.runtime.analysis_capabilities import ANALYSIS_CAPABILITY_IDS

    manager = _manager(request)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        job_id=job_id,
        action="write",
        resource_id=req.analysis_id,
    )
    payload = req.model_dump(exclude_none=True)
    try:
        normalized = normalize_analyze(payload)
    except ApplicationParamError as exc:
        raise _command_error(400, exc.code, exc.message, params=dict(exc.params)) from exc
    registry = science_catalog.analysis_registry(request.app.state)
    # M2-05: the frozen table is the built-in surface; an enabled third-party
    # capability is registered by the same composition root into the injected
    # registry.  An id in neither place is the frozen ``unsupported_capability``
    # refusal, with the full allowed set (built-in ids, then the registered ids).
    if req.analysis_id not in ANALYSIS_CAPABILITY_IDS and (
        registry is None or registry.get(req.analysis_id) is None
    ):
        allowed = [*ANALYSIS_CAPABILITY_IDS, *(registry.ids() if registry is not None else ())]
        raise _command_error(
            422,
            "unsupported_capability",
            f"unknown analysis capability {req.analysis_id!r}",
            params={"field": "analysis_id", "allowed": allowed},
        )
    if registry is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "the application analysis registry is not initialized",
            params={"rule": "analysis_registry_unavailable"},
            retryable=True,
        )
    capability = registry.get(req.analysis_id)
    if capability is None or capability.handler is None:
        # M1-01b freezes two distinct refusals: an id that was never registered is
        # ``not_registered``; a declared capability without a handler is
        # ``not_executable``.  Both are 422 unsupported_capability and start
        # nothing; only state=executable may run.
        registered = capability is not None
        rule = (
            "analysis_capability_not_registered"
            if not registered
            else "analysis_capability_not_executable"
        )
        raise _command_error(
            422,
            "unsupported_capability",
            (
                f"analysis capability {req.analysis_id!r} is not registered"
                if not registered
                else f"analysis capability {req.analysis_id!r} is not executable"
            ),
            params={
                "analysis_id": req.analysis_id,
                "registered": registered,
                "state": (capability.state if capability is not None else "unavailable"),
                "owner": capability.owner if capability is not None else "A",
                "rule": rule,
            },
        )
    record = manager.get(job_id)
    if record is None:
        raise _command_error(
            404,
            "resource_not_ready",
            f"not_prepared: job {job_id!r} does not exist",
            params={"job_id": job_id},
            retryable=True,
        )
    # The analysis reads prepared artifacts of the route's task only.  A ref to a
    # different task is an input error, not a cross-task read that "works".
    foreign = [
        index
        for index, ref in enumerate(normalized["input_refs"])
        if str(ref.get("task_id")) != str(job_id)
    ]
    if foreign:
        raise _command_error(
            400,
            "input_invalid",
            "input_refs must reference artifacts of the route's task",
            params={
                "field": f"input_refs[{foreign[0]}].task_id",
                "rule": "task_id_mismatch",
                "expected": job_id,
            },
        )
    from acp.services_analysis_bootstrap import AnalysisInputError
    from acp.services_science_plugins_bootstrap import PluginAnalysisInputError

    try:
        result = capability.handler(normalized)
    except NotImplementedError as exc:
        raise _command_error(
            422,
            "unsupported_capability",
            f"analysis capability {req.analysis_id!r} is not implemented",
            params={"analysis_id": req.analysis_id, "owner": "B12"},
        ) from exc
    except ArtifactError as exc:
        raise _artifact_error(exc) from exc
    except (AnalysisInputError, PluginAnalysisInputError) as exc:
        raise _command_error(
            exc.http_status,
            "resource_not_ready" if exc.http_status == 409 else "input_invalid",
            str(exc),
            params=dict(exc.params),
        ) from exc
    product = result.get("product") if isinstance(result, Mapping) else None
    evidence_refs = list(result.get("inputs") or ()) if isinstance(result, Mapping) else []
    return {
        "schema_version": 1,
        "analysis_id": req.analysis_id,
        # A read-only analysis starts nothing; only vibration.compute starts one
        # frequency task, and that path is a task submit, never this route.
        "started": False,
        "task_ref": None,
        "operation_ref": None,
        "result": dict(result) if isinstance(result, Mapping) else {"result": result},
        "evidence_refs": evidence_refs,
        "product_digest": (
            str(product.get("digest") or "") if isinstance(product, Mapping) else ""
        ),
    }


__all__ = ["router"]
