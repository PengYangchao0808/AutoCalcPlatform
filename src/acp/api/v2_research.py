"""Read-only research-resource projections for the Workbench (B1-7).

Architecture note
-----------------
This module is the Workbench's **read-only UI projection** of the research
documents owned by the Data authority.  It delegates every read to
``acp_services.data.research_resources.ResearchResourceStore`` (the B18 Data
authority, a versioned JSON resource store) and never writes, revokes or
otherwise mutates a document.  It is deliberately **not** a second P2 client
contract: the P2 writers (proposal / research-record land later) remain the only
producers, and this router is a thin read surface over the Data rows they store.

Honesty note: until those P2 writers are delivered the store has no production
producer, so every read of an unseeded store is the same 404 as an unknown
document.  An empty research panel is therefore the correct, non-lying answer.

Endpoints (both project-scoped, both read-only):

===============================================  ==========================
``GET /research/proposals/{proposal_id}``        one stored proposal
``GET /research/records/{research_id}``          one stored research record
===============================================  ==========================

Gate order (identical for both kinds): required ``project_id`` query →
credential-scoped project → verified-project pin → cache-read entry access →
server-side project claim → store availability → resource key → store read →
schema / scope / nested ``task_refs`` validation.  A document is returned
verbatim (no envelope), never a projection of it.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from acp.api.v1_routes import (
    _command_error,
    _manager,
    _require_entry_access,
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
from acp_services.workspace.entry_authz import EntryClass as _EntryClass

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()

_NOT_FOUND_DETAIL = "Research resource not found"


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
