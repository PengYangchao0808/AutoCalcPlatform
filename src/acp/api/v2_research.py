"""Research-resource write/read surface for the Workbench and RPH (M1-06).

This router owns the **application endpoints** of the research writer.  It is a
thin adapter: every document lives in
``acp_services.data.research_resources.ResearchResourceStore`` (B18 Data
authority), approvals live in ``acp_services.runtime.approvals`` (B16), the
research identity/round come from ``acp_services.runtime.research_session``
(M1-02), the submit classification from ``acp_services.runtime.submit_policy``
(M1-04b) and the evidence projection from
``acp_services.runtime.evidence_projection`` (M1-03).  No second store, no
second digest implementation, no second evidence model.

Endpoints (project-scoped; identity is always server-resolved):

=====================================================  =======================
``POST /research/proposals``                           create proposal + approval
``POST /research/proposals/{proposal_id}/cas``         CAS one proposal revision
``GET  /research/proposals/{proposal_id}``             one stored proposal
``POST /research/records``                             create the round record
``GET  /research/records/{research_id}``               one stored record
``GET  /research/records/{research_id}/evidence``      M1-03 evidence projection
=====================================================  =======================

The evidence read is a **projection**, never a stored copy: it re-reads the
record, resolves the bound attempt under the controlled local work dir, verifies
the receipt/task-result/report digests and calls the single M1-03 projection.
No endpoint here creates a task, takes a reservation or triggers science.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from acp.api.v1_routes import (
    _command_error,
    _command_identity,
    _manager,
    _require_entry_access,
    _require_submit_access,
    _require_verified_project,
    _research_project,
    _scoped_read_project,
    _server_identity,
)
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceAuthorizationError,
    ResearchResourceNotFoundError,
    ResearchResourceRevokedError,
    ResearchResourceStore,
    research_resource_key,
)
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.evidence_projection import (
    EvidenceProjectionError,
    EvidenceView,
    TaskRef,
    project_evidence,
    verify_attribution,
)
from acp_services.runtime.research_saga import SagaStore
from acp_services.runtime.research_service import (
    ResearchOperationError,
    ResearchOperations,
)
from acp_services.runtime.research_session import ResearchIdentity
from acp_services.runtime.research_writer import (
    ResearchWriteError,
    ResearchWriter,
)
from acp_services.workspace.entry_authz import EntryClass as _EntryClass

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()

_NOT_FOUND_DETAIL = "Research resource not found"


# ---------------------------------------------------------------------------
# Request models (declared, additive; identity is never a payload field)
# ---------------------------------------------------------------------------


class ProposalWriteBody(BaseModel):
    """Create/CAS payload of one proposal and its execution plan.

    ``plan`` is the *execution* plan (workflow/input/method/resources) the
    approval binds as ``plan_digest``; the proposal body is the C-shaped
    scientific document whose ``science``/``budget``/``recipe_digest`` produce
    the independent ``science_digest``.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    session_seed: str = ""
    proposal: dict[str, Any]
    plan: dict[str, Any]
    budget: dict[str, Any] | None = None
    expires_at: str | None = None
    #: P2-R1: the same key with the same request replays; another request is a
    #: typed ``idempotency_key_reuse`` conflict (never a second resource).
    idempotency_key: str | None = None


class ProposalCasBody(ProposalWriteBody):
    """CAS payload: the observed revision is mandatory."""

    expected_revision: int = Field(ge=1)


class RecordWriteBody(BaseModel):
    """Create payload of one round record (one TaskRef + digest-bound evidence)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    session_seed: str = ""
    #: Optional claim of the record's research identity.  It never selects the
    #: identity (M1-02 derives it from the verified command identity + seed); a
    #: claim that differs from the derivation is a typed `research_identity_mismatch`.
    research_id: str | None = None
    proposal_ref: str
    approval_ref: str
    plan_definition_digest: str
    round: int = Field(ge=0)
    decision: str
    task_refs: list[dict[str, Any]] = Field(min_length=1, max_length=1)
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    evidence_view: dict[str, Any] | None = None
    step_identity_refs: list[dict[str, Any]] | None = None
    missing_validation: list[dict[str, Any]] | None = None
    human_edits: list[dict[str, Any]] | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Services / helpers
# ---------------------------------------------------------------------------


def _expected_schema(kind: str) -> str:
    """Return the frozen document schema id for one research resource kind."""
    return PROPOSAL_SCHEMA if kind == "proposal" else RESEARCH_RECORD_SCHEMA


def _resource_store(request: Request) -> ResearchResourceStore:
    store: ResearchResourceStore | None = getattr(request.app.state, "research_resources", None)
    if store is None:
        raise _command_error(
            503,
            "backend_unavailable",
            "research resource store is not available",
            retryable=True,
        )
    return store


def _approval_service(request: Request) -> ApprovalService:
    manager = _manager(request)
    cached = getattr(request.app.state, "approval_service", None)
    if cached is None or str(getattr(cached, "db_path", "")) != str(manager.store.db_path):
        cached = ApprovalService(manager.store.db_path)
        request.app.state.approval_service = cached
    return cached


def _writer(request: Request) -> ResearchWriter:
    return ResearchWriter(
        resources=_resource_store(request),
        approvals=_approval_service(request),
    )


def _saga_store(request: Request) -> SagaStore:
    """The research coordination database (saga intents + operation keys).

    One file per run root, next to the research-resource database; created
    idempotently.  It owns no research fact: the resource, approval and task
    authorities stay where they are.
    """
    cached: SagaStore | None = getattr(request.app.state, "research_saga_store", None)
    if cached is not None:
        return cached
    manager = _manager(request)
    created = SagaStore(manager.run_root / "acp_research_saga.db")
    request.app.state.research_saga_store = created
    return created


def _operations(request: Request) -> ResearchOperations:
    """Build the P2 application service over the authorities of this app.

    The submit port is deliberately **absent** here: the Controller does not
    auto-submit a task from a resume in this checkpoint (the approval-bound
    ``SubmitPolicy`` is M2-03).  A saga step that would need it answers a typed
    ``resource_not_ready``/``submit_port_unavailable`` instead of creating a job.
    """
    return ResearchOperations(
        writer=_writer(request),
        resources=_resource_store(request),
        approvals=_approval_service(request),
        sagas=_saga_store(request),
    )


def _operation_error(exc: ResearchOperationError) -> HTTPException:
    detail = exc.detail()
    return _command_error(
        exc.http_status,
        str(detail["code"]),
        str(detail["message"]),
        params=dict(detail["params"]),
        retryable=bool(detail.get("retryable")),
    )


def _write_error(exc: ResearchWriteError) -> HTTPException:
    detail = exc.detail()
    return _command_error(
        exc.http_status,
        str(detail["code"]),
        str(detail["message"]),
        params=dict(detail["params"]),
        retryable=False,
    )


def _resolve_write_project(request: Request, body_project: str | None) -> str:
    """Resolve and authorize the write project (server identity wins)."""
    resolved = _require_submit_access(request, body_project)
    manager = _manager(request)
    project = resolved or body_project or manager.default_project_id
    if not project:
        raise _command_error(
            403,
            "access_denied",
            "a research write requires a project",
            params={"rule": "project_scope_mismatch"},
        )
    _research_project(manager, str(project))
    _require_verified_project(request, str(project))
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=str(project),
        action="write",
        resource_id="research.write",
    )
    return str(project)


# ---------------------------------------------------------------------------
# Write routes
# ---------------------------------------------------------------------------


@router.post("/research/proposals", status_code=201)
def create_research_proposal(body: ProposalWriteBody, request: Request) -> dict[str, Any]:
    """Create revision 1 of a proposal and request its double-digest approval."""
    project = _resolve_write_project(request, body.project_id)
    proposal = dict(body.proposal)
    proposal["project_id"] = project
    try:
        return _operations(request).proposal_create(
            command=_command_identity(request, project_id=project),
            proposal=proposal,
            plan=body.plan,
            session_seed=body.session_seed,
            budget=body.budget,
            expires_at=body.expires_at,
            idempotency_key=body.idempotency_key,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc)


@router.post("/research/proposals/{proposal_id}/cas")
def cas_research_proposal(
    proposal_id: str, body: ProposalCasBody, request: Request
) -> dict[str, Any]:
    """CAS one proposal revision; a changed plan digest opens a new round."""
    project = _resolve_write_project(request, body.project_id)
    proposal = dict(body.proposal)
    proposal["project_id"] = project
    proposal["proposal_id"] = proposal_id
    try:
        return _operations(request).proposal_update_cas(
            command=_command_identity(request, project_id=project),
            proposal_ref=proposal_id,
            expected_revision=body.expected_revision,
            proposal=proposal,
            plan=body.plan,
            session_seed=body.session_seed,
            budget=body.budget,
            expires_at=body.expires_at,
            idempotency_key=body.idempotency_key,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc)


@router.post("/research/records", status_code=201)
def create_research_record(body: RecordWriteBody, request: Request) -> dict[str, Any]:
    """Create the round record: one TaskRef, digest-bound evidence, one decision."""
    project = _resolve_write_project(request, body.project_id)
    created_at = body.created_at or ""
    if not created_at:
        import time

        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "project_id": project,
        "research_id": str(body.research_id or ""),
        "round": body.round,
        "decision": body.decision,
        "plan_definition_digest": body.plan_definition_digest,
        "task_refs": [dict(item) for item in body.task_refs],
        "evidence_refs": [dict(item) for item in body.evidence_refs],
        "step_identity_refs": list(body.step_identity_refs or []),
        "missing_validation": list(body.missing_validation or []),
        "human_edits": list(body.human_edits or []),
        "approval_ref": body.approval_ref,
        "proposal_ref": body.proposal_ref,
        "created_at": created_at,
    }
    try:
        result = _writer(request).create_record(
            command=_command_identity(request, project_id=project),
            session_seed=body.session_seed,
            record=record,
            evidence_view=body.evidence_view,
        )
    except ResearchWriteError as exc:
        raise _write_error(exc)
    return result.to_dict()


# ---------------------------------------------------------------------------
# Read routes (existing behaviour)
# ---------------------------------------------------------------------------


def _read_not_found() -> HTTPException:
    """The single not-found shape (unknown, cross-project and revoked alike)."""
    return HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)


def _read_document(
    store: ResearchResourceStore,
    *,
    resource_id: str,
    principal_id: str,
    project_id: str,
) -> Any:
    """Read one resource, mapping store refusals to HTTP without leaking scope."""
    try:
        return store.read(
            resource_id=resource_id,
            principal_id=principal_id,
            project_id=project_id,
        )
    except (
        ResearchResourceNotFoundError,
        ResearchResourceAuthorizationError,
        ResearchResourceRevokedError,
    ):
        # A revoked or cross-project row is indistinguishable from an unknown
        # row; the chosen contract is 404 for all three (never 403/410).
        raise _read_not_found() from None
    except sqlite3.Error as exc:
        raise _command_error(
            503,
            "backend_unavailable",
            "research resource store is not available",
            retryable=True,
        ) from exc


def _schema_mismatch() -> HTTPException:
    """A stored row whose schema contradicts the route kind is an internal fault."""
    logger.exception("research resource schema mismatch")
    return _command_error(500, "internal_error", "research resource schema mismatch")


def _read_research_document(
    request: Request,
    *,
    kind: str,
    document_id: str,
    project_id: str | None,
) -> JSONResponse:
    """Shared implementation for the proposal and record read routes."""
    if project_id is None or not str(project_id).strip():
        raise _command_error(
            400,
            "input_invalid",
            "project_id is required",
            params={"field": "project_id"},
        )

    resolved = _scoped_read_project(request, project_id)
    _require_verified_project(request, resolved)

    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=resolved,
        action="read",
        resource_id=f"research.{kind}:{document_id}",
    )

    # Server-side claim resolution: an unknown project is access_denied here.
    _research_project(_manager(request), resolved)

    store = _resource_store(request)
    principal = str(_server_identity(request).principal.principal_id)
    resource_id = research_resource_key(str(resolved), kind, document_id)
    record = _read_document(
        store,
        resource_id=resource_id,
        principal_id=principal,
        project_id=str(resolved),
    )

    expected_schema = _expected_schema(kind)
    if record.schema != expected_schema:
        # The DB column is the primary source; document[...] never is.
        raise _schema_mismatch()

    document = record.document
    declared_schema = document.get("schema") if isinstance(document, Mapping) else None
    if declared_schema is not None and declared_schema != record.schema:
        raise _schema_mismatch()

    if not isinstance(document, Mapping) or document.get("project_id") != resolved:
        raise _read_not_found()

    if kind == "record":
        for task_ref in document.get("task_refs") or []:
            if not isinstance(task_ref, Mapping) or task_ref.get("project_id") != resolved:
                raise _command_error(
                    409,
                    "revision_conflict",
                    "research record task_refs cross project scope",
                    params={"rule": "cross_project_task_ref"},
                )
        if document.get("research_id") != document_id:
            raise _read_not_found()
    elif document.get("proposal_id") != document_id:
        raise _read_not_found()

    # Return the stored document as-is: no envelope, no added status fields.
    return JSONResponse(document)


@router.get("/research/proposals/{proposal_id}")
def get_research_proposal(
    proposal_id: str,
    request: Request,
    project_id: str | None = Query(default=None),
) -> JSONResponse:
    """Read one stored proposal verbatim for the Workbench research panel."""
    return _read_research_document(
        request,
        kind="proposal",
        document_id=proposal_id,
        project_id=project_id,
    )


@router.get("/research/records/{research_id}")
def get_research_record(
    research_id: str,
    request: Request,
    project_id: str | None = Query(default=None),
) -> JSONResponse:
    """Read one stored research record verbatim for the Workbench research panel."""
    return _read_research_document(
        request,
        kind="record",
        document_id=research_id,
        project_id=project_id,
    )


#: The C ``Proposal``/``ResearchRecord`` bodies stay the writer's input; these
#: additive request models only carry the P2 operation envelope (M2-01c).
class RecordRoundBody(BaseModel):
    """Round/evidence append payload (a new record revision of one round)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    session_seed: str = ""
    expected_revision: int = Field(ge=1)
    record: dict[str, Any]
    evidence_view: dict[str, Any] | None = None
    idempotency_key: str | None = None


class ResearchWaitBody(BaseModel):
    """Bounded wait payload (``max_wait_s`` <= 60, ``limit`` <= 200)."""

    model_config = ConfigDict(extra="forbid")

    kinds: list[str] = Field(min_length=1)
    cursor: str | None = None
    max_wait_s: float | None = None
    limit: int | None = None
    project_id: str | None = None


class ResearchResumeBody(BaseModel):
    """Resume payload; the saga id is derived from the identity, never supplied."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    proposal_ref: str
    plan_digest: str
    task_ref: dict[str, Any] | None = None
    cursor: str | None = None
    diagnostic_only: bool = False


class SagaReconcileBody(BaseModel):
    """Explicit reconciliation action (the only mutating saga entry)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = None
    action: str
    expected_revision: int = Field(ge=1)
    reason: str = ""


def _research_project_scope(request: Request, project_hint: str | None) -> str:
    """Resolve the read/write project of one P2 operation (server identity wins)."""
    resolved = _require_submit_access(request, project_hint)
    manager = _manager(request)
    project = resolved or project_hint or manager.default_project_id
    if not project:
        raise _command_error(
            403,
            "access_denied",
            "a research operation requires a project",
            params={"rule": "project_scope_mismatch"},
        )
    _research_project(manager, str(project))
    _require_verified_project(request, str(project))
    return str(project)


# ---------------------------------------------------------------------------
# Round/evidence append (P2-R1 additive write)
# ---------------------------------------------------------------------------


@router.post("/research/records/{research_id}/rounds", status_code=201)
def append_research_round(
    research_id: str, body: RecordRoundBody, request: Request
) -> dict[str, Any]:
    """Append one round to an existing record (CAS on the observed revision)."""
    project = _research_project_scope(request, body.project_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=project,
        action="write",
        resource_id=f"research.record:{research_id}",
    )
    try:
        result = _operations(request).record_append_round(
            command=_command_identity(request, project_id=project),
            research_id=research_id,
            expected_revision=body.expected_revision,
            record=dict(body.record),
            session_seed=body.session_seed,
            evidence_view=body.evidence_view,
            idempotency_key=body.idempotency_key,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc
    return result


# ---------------------------------------------------------------------------
# Bounded wait / snapshot (P2-R4)
# ---------------------------------------------------------------------------


@router.post("/research/wait")
def wait_research(body: ResearchWaitBody, request: Request) -> dict[str, Any]:
    """Bounded wait over the authoritative snapshots (never an unbounded poll)."""
    project = _research_project_scope(request, body.project_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=project,
        action="read",
        resource_id="research.wait",
    )
    try:
        return _operations(request).research_wait(
            command=_command_identity(request, project_id=project),
            kinds=body.kinds,
            cursor=body.cursor,
            max_wait_s=body.max_wait_s,
            limit=body.limit,
            project_id=project,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc


@router.get("/research/snapshot")
def snapshot_research(
    request: Request,
    refs: str = Query(..., min_length=1),
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Authoritative snapshot of ``kind:ref`` references (read-only)."""
    project = _research_project_scope(request, project_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=project,
        action="read",
        resource_id="research.snapshot",
    )
    requested = [item.strip() for item in str(refs).split(",") if item.strip()]
    if not requested:
        raise _command_error(
            400, "input_invalid", "refs must name at least one reference", params={"field": "refs"}
        )
    try:
        return _operations(request).research_snapshot(
            command=_command_identity(request, project_id=project),
            refs=requested,
            project_id=project,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc


# ---------------------------------------------------------------------------
# Resume + saga reconciliation (P2-R5)
# ---------------------------------------------------------------------------


@router.post("/research/resume")
def resume_research(body: ResearchResumeBody, request: Request) -> dict[str, Any]:
    """Resume the derived saga of ``(proposal, plan_digest)`` without a second task."""
    project = _research_project_scope(request, body.project_id)
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=project,
        action="read",
        resource_id="research.resume",
    )
    try:
        return _operations(request).research_resume(
            command=_command_identity(request, project_id=project),
            proposal_ref=body.proposal_ref,
            plan_digest=body.plan_digest,
            task_ref=body.task_ref,
            cursor=body.cursor,
            diagnostic_only=body.diagnostic_only,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc


@router.get("/research/sagas/{saga_id}")
def get_research_saga(saga_id: str, request: Request) -> dict[str, Any]:
    """Read-only intent reconciliation of one saga (never retries anything)."""
    store = _saga_store(request)
    header = store.header(saga_id)
    if header is None:
        raise _command_error(404, "not_found", "no such research saga")
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=header.project_id,
        action="read",
        resource_id=f"research.saga:{saga_id}",
    )
    try:
        return _operations(request).saga_reconcile(
            command=_command_identity(request, project_id=header.project_id),
            saga_id=saga_id,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc


@router.post("/research/sagas/{saga_id}/reconcile")
def reconcile_research_saga(
    saga_id: str, body: SagaReconcileBody, request: Request
) -> dict[str, Any]:
    """Apply one explicit reconciliation action (``advance``/``abandon``)."""
    store = _saga_store(request)
    header = store.header(saga_id)
    if header is None:
        raise _command_error(404, "not_found", "no such research saga")
    project = _research_project_scope(request, body.project_id)
    if project != header.project_id:
        raise _command_error(
            403,
            "access_denied",
            "the saga belongs to another project",
            params={"rule": "project_scope_mismatch"},
        )
    _require_entry_access(
        request,
        _EntryClass.CACHE_READ,
        project_id=project,
        action="write",
        resource_id=f"research.saga:{saga_id}",
    )
    try:
        return _operations(request).saga_reconcile(
            command=_command_identity(request, project_id=project),
            saga_id=saga_id,
            action=body.action,
            expected_revision=body.expected_revision,
            reason=body.reason,
        )
    except ResearchOperationError as exc:
        raise _operation_error(exc) from exc


# ---------------------------------------------------------------------------
# Evidence projection read (M1-03 single implementation, no stored copy)
# ---------------------------------------------------------------------------


def _local_work_dir(request: Request, job_id: str) -> Any:
    """Resolve the local attempt work dir; a remote attempt is typed unavailable."""
    record = _manager(request).get(job_id)
    if record is None:
        raise _command_error(
            404,
            "resource_not_ready",
            f"evidence source not found for task {job_id!r}",
            params={"job_id": job_id},
            retryable=False,
        )
    work_dir = str(getattr(record, "work_dir", "") or "")
    if not work_dir:
        raise _command_error(
            503,
            "resource_unavailable",
            "the attempt has no local work dir; remote evidence is not wired in this slice",
            params={"job_id": job_id, "rule": "remote_evidence_not_available"},
            retryable=False,
        )
    from pathlib import Path

    return Path(work_dir)


@router.get("/research/records/{research_id}/evidence")
def get_research_record_evidence(
    research_id: str,
    request: Request,
    project_id: str | None = Query(default=None),
    session_seed: str = Query(default=""),
    round: int | None = Query(default=None),
) -> JSONResponse:
    """Project the record's bound attempt through the single M1-03 adapter.

    The record is the authority for *which* attempt and *which* digest-bound
    evidence report; the projection is recomputed from the attempt's real
    receipt/task-result/report bytes on every read.  A second stored copy of the
    view would be a second evidence model, so there is none.
    """
    stored = _read_research_document(
        request,
        kind="record",
        document_id=research_id,
        project_id=project_id,
    )
    record = json.loads(stored.body)
    project = str(record.get("project_id") or "")
    identity = ResearchIdentity.allocate(
        _command_identity(request, project_id=project), session_seed=session_seed
    )
    if identity.project_id != project or identity.research_id != str(
        record.get("research_id") or ""
    ):
        raise _command_error(
            403,
            "access_denied",
            "the record research identity is not the one derived for this session",
            params={"rule": "research_identity_mismatch"},
        )
    record_round = record.get("round")
    if not isinstance(record_round, int) or isinstance(record_round, bool):
        raise _command_error(409, "revision_conflict", "record round is not an integer")
    if round is not None and int(round) != record_round:
        raise _command_error(
            409,
            "revision_conflict",
            "requested round differs from the record round",
            params={"rule": "foreign_round", "record_round": record_round},
        )

    task_refs = record.get("task_refs") or []
    if not isinstance(task_refs, list) or len(task_refs) != 1:
        raise _command_error(
            409,
            "revision_conflict",
            "the record does not bind exactly one TaskRef",
            params={"rule": "ambiguous_task_ref"},
        )
    binding = task_refs[0]
    report_ref: Mapping[str, Any] | None = None
    for ref in record.get("evidence_refs") or []:
        if isinstance(ref, Mapping) and str(ref.get("kind") or "") == "evidence_report":
            report_ref = ref
            break
    if report_ref is None or not report_ref.get("relative_path"):
        raise _command_error(
            409,
            "revision_conflict",
            "the record cites no digest-bound evidence report",
            params={"rule": "evidence_report_missing"},
        )

    work_dir = _local_work_dir(request, str(binding.get("job_id") or ""))
    from acp_services.runtime import worker

    try:
        receipt = worker.read_receipt(work_dir / "execution_receipt.json")
        task_result = worker.read_task_result(work_dir)
    except (
        OSError,
        ValueError,
        worker.ReceiptConflictError,
        worker.TaskResultIntegrityError,
    ) as exc:
        raise _command_error(
            409,
            "revision_conflict",
            f"the attempt artifacts are not readable: {type(exc).__name__}",
            params={"rule": "evidence_source_unavailable"},
        ) from exc
    if receipt is None or task_result is None:
        raise _command_error(
            409,
            "resource_not_ready",
            "the attempt has no receipt/task_result yet",
            params={"rule": "evidence_not_ready"},
            retryable=True,
        )
    from pathlib import Path

    root = Path(work_dir).resolve()
    report_path = (root / str(report_ref["relative_path"])).resolve()
    if root not in report_path.parents:
        raise _command_error(
            409,
            "revision_conflict",
            "the evidence report path escapes the attempt root",
            params={"rule": "evidence_path_refused"},
        )
    try:
        payload = report_path.read_bytes()
    except OSError as exc:
        raise _command_error(
            409,
            "resource_not_ready",
            "the evidence report is not readable",
            params={"rule": "evidence_source_unavailable"},
            retryable=True,
        ) from exc
    actual = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual != str(report_ref.get("sha256") or ""):
        raise _command_error(
            409,
            "revision_conflict",
            "the evidence report digest differs from the record",
            params={"rule": "evidence_digest_mismatch"},
        )
    # The C record binding is flat (no release digest).  The executed receipt is
    # the verified source of that digest: the record's release id must match the
    # receipt, and the digest comes from the receipt, never from the caller.
    if str(receipt.release_id or "") != str(binding.get("release_id") or ""):
        raise _command_error(
            409,
            "revision_conflict",
            "the record release id differs from the executed receipt",
            params={"rule": "foreign_release"},
        )
    try:
        task_ref = TaskRef.from_task_binding(
            binding, release_digest=str(receipt.release_digest or "")
        )
    except EvidenceProjectionError as exc:
        raise _command_error(
            400, "input_invalid", str(exc), params={"rule": exc.code, "field": "task_refs"}
        ) from exc
    try:
        view: EvidenceView = project_evidence(
            identity=identity,
            round_index=record_round,
            task_ref=task_ref,
            receipt=receipt.to_dict(),
            task_result=task_result,
            report=payload,
            report_digest=actual,
            read_root=work_dir,
        )
        verify_attribution(view, identity, record_round, task_ref)
    except EvidenceProjectionError as exc:
        raise _command_error(
            409,
            "revision_conflict",
            str(exc),
            params={"rule": exc.code},
        ) from exc
    return JSONResponse(view.to_dict())
