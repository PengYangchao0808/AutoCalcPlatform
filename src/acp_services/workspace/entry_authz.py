"""Per-entry authorization gate (B02-7 / plan todo 17).

The legacy read/write surface enforced authorization **nowhere**: a download, a
log tail, a byte-range preview, a frame-candidate read/write, a structure-source
query, a cache read and the SSE event stream were all reachable with the same
trust as the list page.  Filtering a listing is not authorization, and hiding a
control in the frontend is not authorization either — every entry that actually
returns or mutates a resource must decide for itself.

This module is the one place that decision is made.  It reuses the frozen
identity model of B01-2 (todo 7): :func:`resolve_scope` derives the
authoritative tenant/project scope from a verified :class:`Principal` (never
from the request), and :class:`LocalSingleAccessPolicy` supplies the explicit,
revocable grant decision when a concrete resource grant is required.

Seven entry classes are covered (plan B02-7):

* ``download`` — a file body returned to the caller;
* ``log`` — a job log tail / incremental log read;
* ``range`` — a bounded byte-range read of a prepared resource;
* ``frame_candidates`` — energy-viewer candidate list / save (publish) / delete;
* ``structure_sources`` — the reusable structure-source queries and edits;
* ``cache_read`` — availability / cache-index reads (no fetch, no enqueue);
* ``event_stream`` — the SSE job event stream.

The verdict is a :class:`EntryDecision` carrying ``allowed`` plus an
HTTP-mappable ``http_status`` (``200`` allow / ``403`` deny) and a typed
``reason_code``; :meth:`EntryDecision.error_detail` produces
``{"code": ..., "message": ...}`` so the HTTP layer can raise a 403 whose shape
matches the existing error contract.  Authorization is re-checked at the point
of effect: once before a **download** streams and once before a **publish**
(a frame-candidate save).  A raw SFTP ``PermissionError`` is *not* a Workspace
revocation and this gate never treats one as the other — the two are independent
checks.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package, so this module is stdlib plus the frozen
:mod:`acp_services.contracts.access` vocabulary only.  The HTTP glue that
consumes it lives on the legacy side (``acp.api.*``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acp_services.contracts.access import (
    AccessAction,
    AccessScope,
    DecisionEffect,
    ExecutionMode,
    LocalPrincipal,
    LocalSingleAccessPolicy,
    Principal,
    ReasonCode,
    ResourceGrant,
    User,
    resolve_scope,
)

__all__ = [
    "ENTRY_HTTP_OK",
    "ENTRY_HTTP_FORBIDDEN",
    "EntryClass",
    "EntryReasonCode",
    "EntryDecision",
    "EntryAuthorizer",
    "default_entry_authorizer",
    "entry_default_action",
    "entry_class_is_valid",
]

#: HTTP status the HTTP layer uses for an allow / deny entry decision.
ENTRY_HTTP_OK = 200
ENTRY_HTTP_FORBIDDEN = 403


class EntryClass(str):
    """The seven resource entry classes guarded by this module (plan B02-7)."""

    DOWNLOAD = "download"
    LOG = "log"
    RANGE = "range"
    FRAME_CANDIDATES = "frame_candidates"
    STRUCTURE_SOURCES = "structure_sources"
    CACHE_READ = "cache_read"
    EVENT_STREAM = "event_stream"
    ALL = frozenset(
        {
            DOWNLOAD,
            LOG,
            RANGE,
            FRAME_CANDIDATES,
            STRUCTURE_SOURCES,
            CACHE_READ,
            EVENT_STREAM,
        }
    )


class EntryReasonCode(str):
    """Typed reason codes for an entry decision (HTTP-403-mappable)."""

    ALLOWED = ReasonCode.GRANTED
    NO_GRANT = ReasonCode.NO_GRANT
    SCOPE_MISMATCH = ReasonCode.SCOPE_MISMATCH
    REVOKED = ReasonCode.REVOKED
    EXPIRED = ReasonCode.EXPIRED
    UNKNOWN_ENTRY = "unknown_entry"
    ACTION_NOT_GRANTED = "action_not_granted"
    ALL = frozenset(
        {
            ALLOWED,
            NO_GRANT,
            SCOPE_MISMATCH,
            REVOKED,
            EXPIRED,
            UNKNOWN_ENTRY,
            ACTION_NOT_GRANTED,
        }
    )


#: Default action a request needs to perform on each entry class.  A caller may
#: override it (frame candidates: read for list, write for save/delete;
#: structure sources: read for queries, write for edits).
_ENTRY_DEFAULT_ACTION: dict[str, str] = {
    EntryClass.DOWNLOAD: AccessAction.READ,
    EntryClass.LOG: AccessAction.READ,
    EntryClass.RANGE: AccessAction.READ,
    EntryClass.FRAME_CANDIDATES: AccessAction.READ,
    EntryClass.STRUCTURE_SOURCES: AccessAction.READ,
    EntryClass.CACHE_READ: AccessAction.READ,
    EntryClass.EVENT_STREAM: AccessAction.READ,
}


def entry_class_is_valid(entry_class: str) -> bool:
    """Whether *entry_class* is one of the seven guarded entries."""
    return str(entry_class) in EntryClass.ALL


def entry_default_action(entry_class: str) -> str:
    """Return the default action for *entry_class* (``read`` when unknown)."""
    return _ENTRY_DEFAULT_ACTION.get(str(entry_class), AccessAction.READ)


@dataclass(frozen=True)
class EntryDecision:
    """One per-entry authorization verdict.

    ``http_status`` is the mappable status (``200`` allow / ``403`` deny) and
    ``reason_code`` the typed B08-ready code.  An unknown entry class is denied
    (fail closed) rather than guessed.
    """

    allowed: bool
    entry_class: str
    action: str
    reason_code: str
    principal_id: str
    scope_id: str
    http_status: int = ENTRY_HTTP_OK
    project_id: str | None = None
    resource_id: str = ""
    job_id: str = ""
    detail: str = ""

    def error_detail(self) -> dict[str, str]:
        """Return the HTTP error body shape used by the existing 403 contract."""
        return {"code": self.reason_code, "message": self.detail}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "entry_class": self.entry_class,
            "action": self.action,
            "reason_code": self.reason_code,
            "principal_id": self.principal_id,
            "scope_id": self.scope_id,
            "http_status": self.http_status,
            "project_id": self.project_id,
            "resource_id": self.resource_id,
            "job_id": self.job_id,
            "detail": self.detail,
        }


class EntryAuthorizer:
    """Authorize each of the seven resource entries against the identity model.

    Args:
        policy: Optional :class:`LocalSingleAccessPolicy` for explicit,
            revocable resource grants.  Consulted only when
            ``require_grant=True``.
        principal: A fixed verified principal (tests inject a forged one).  When
            omitted the loopback ``local-single`` principal is resolved.
        principal_provider: Alternative to ``principal`` — a callable returning
            the principal for the current request.
        known_projects: Mapping ``project_id -> tenant_id`` (``None`` = legacy
            tenant-less).  When omitted, a requested project is treated as
            belonging to the principal's own tenant (the ``local-single`` rule:
            every project is the local tenant's).
        user: Optional identity-model :class:`User` (disabled users are denied).
        grants: Grants folded into the resolved action set.
        execution_mode: Recorded as a separate axis (never widens the tier).
        require_grant: When True, a live explicit grant covering the requested
            resource/action is additionally required (this is the forged-scope
            and revocation axis).
    """

    def __init__(
        self,
        *,
        policy: LocalSingleAccessPolicy | None = None,
        principal: Principal | None = None,
        principal_provider: Callable[[], Principal] | None = None,
        known_projects: Mapping[str, str | None] | None = None,
        user: User | None = None,
        grants: Sequence[ResourceGrant] = (),
        execution_mode: str = ExecutionMode.LOCAL,
        require_grant: bool = False,
        source_host: str | None = None,
    ) -> None:
        self._policy = policy
        self._principal = principal
        self._principal_provider = principal_provider
        self._known_projects = None if known_projects is None else dict(known_projects)
        self._user = user
        self._grants = tuple(grants)
        self._execution_mode = str(execution_mode)
        self._require_grant = bool(require_grant)
        self._source_host = source_host

    # -- principal ------------------------------------------------------

    def _resolve_principal(self) -> Principal:
        if self._principal is not None:
            return self._principal
        if self._principal_provider is not None:
            return self._principal_provider()
        return LocalPrincipal.resolve(source_host=self._source_host)

    # -- decision helpers ----------------------------------------------

    def _verdict(
        self,
        *,
        entry_class: str,
        action: str,
        effect: str,
        reason_code: str,
        principal: Principal,
        scope_id: str,
        project_id: str | None,
        resource_id: str,
        job_id: str,
        detail: str,
    ) -> EntryDecision:
        allowed = effect == DecisionEffect.ALLOW
        return EntryDecision(
            allowed=allowed,
            entry_class=str(entry_class),
            action=str(action),
            reason_code=str(reason_code),
            principal_id=principal.principal_id,
            scope_id=str(scope_id),
            http_status=ENTRY_HTTP_OK if allowed else ENTRY_HTTP_FORBIDDEN,
            project_id=project_id,
            resource_id=resource_id,
            job_id=job_id,
            detail=detail,
        )

    # -- authorize ------------------------------------------------------

    def authorize(
        self,
        entry_class: str,
        *,
        job_id: str = "",
        project_id: str | None = None,
        action: str | None = None,
        resource_id: str = "",
        now: datetime | None = None,
    ) -> EntryDecision:
        """Return the :class:`EntryDecision` for one entry request.

        Never raises for a denial: a denied request always returns a decision
        carrying ``allowed=False`` / ``http_status=403``.  An unknown entry
        class is denied ``unknown_entry`` (fail closed) so a newly added entry
        cannot silently bypass the gate.
        """
        effective_action = str(action or entry_default_action(entry_class))
        principal = self._resolve_principal()
        resource = resource_id or project_id or job_id or "tenant"

        if not entry_class_is_valid(entry_class):
            return self._verdict(
                entry_class=entry_class,
                action=effective_action,
                effect=DecisionEffect.DENY,
                reason_code=EntryReasonCode.UNKNOWN_ENTRY,
                principal=principal,
                scope_id=f"entry:{entry_class}",
                project_id=project_id,
                resource_id=resource,
                job_id=job_id,
                detail=f"unknown entry class {entry_class!r} is denied (fail closed)",
            )

        known = self._known_projects
        if known is None and project_id is not None:
            # local-single rule: the requested project is the principal's own
            # tenant's project unless an explicit map says otherwise.
            known = {project_id: principal.tenant_id}

        resolution = resolve_scope(
            principal,
            project_id,
            known_projects=known,
            user=self._user,
            grants=self._grants,
            execution_mode=self._execution_mode,
            now=now,
        )

        if not resolution.allowed:
            return self._verdict(
                entry_class=entry_class,
                action=effective_action,
                effect=DecisionEffect.DENY,
                reason_code=resolution.reason_code,
                principal=principal,
                scope_id=resolution.scope_id,
                project_id=project_id,
                resource_id=resource,
                job_id=job_id,
                detail=(
                    f"entry {entry_class!r} denied for principal "
                    f"{principal.principal_id!r}: {resolution.reason_code}"
                ),
            )

        available = resolution.available_actions or ()
        if effective_action not in available:
            return self._verdict(
                entry_class=entry_class,
                action=effective_action,
                effect=DecisionEffect.DENY,
                reason_code=EntryReasonCode.ACTION_NOT_GRANTED,
                principal=principal,
                scope_id=resolution.scope_id,
                project_id=project_id,
                resource_id=resource,
                job_id=job_id,
                detail=(
                    f"entry {entry_class!r} needs action {effective_action!r} "
                    f"but only {list(available)!r} are available"
                ),
            )

        if self._require_grant:
            grant_decision = self._grant_check(
                principal=principal,
                resolution=resolution,
                action=effective_action,
                resource=resource,
                now=now,
            )
            if grant_decision is not None:
                return self._verdict(
                    entry_class=entry_class,
                    action=effective_action,
                    effect=DecisionEffect.DENY,
                    reason_code=grant_decision,
                    principal=principal,
                    scope_id=resolution.scope_id,
                    project_id=project_id,
                    resource_id=resource,
                    job_id=job_id,
                    detail=(
                        f"entry {entry_class!r} requires an explicit live grant for "
                        f"resource {resource!r}: {grant_decision}"
                    ),
                )

        return self._verdict(
            entry_class=entry_class,
            action=effective_action,
            effect=DecisionEffect.ALLOW,
            reason_code=EntryReasonCode.ALLOWED,
            principal=principal,
            scope_id=resolution.scope_id,
            project_id=project_id,
            resource_id=resource,
            job_id=job_id,
            detail=f"entry {entry_class!r} allowed",
        )

    def _grant_check(
        self,
        *,
        principal: Principal,
        resolution: Any,
        action: str,
        resource: str,
        now: datetime | None,
    ) -> str | None:
        """Return a deny reason from the explicit-grant policy, or ``None``."""
        if self._policy is None:
            return EntryReasonCode.NO_GRANT
        scope = AccessScope(
            scope_id=str(resolution.scope_id),
            tenant_id=str(resolution.tenant_id),
            action=str(action),
        )
        decision = self._policy.authorize(
            principal,
            scope,
            resource_id=resource,
            action=action,
            now=now,
        )
        if decision.effect == DecisionEffect.ALLOW:
            return None
        return str(decision.reason_code)


def default_entry_authorizer() -> EntryAuthorizer:
    """Return the ``local-single`` authorizer used when none is injected.

    Every project is treated as the local tenant's project, so the existing
    trusted-local behaviour is preserved: the gate is inert until an explicit
    identity/grant policy is configured, but the hook is real and always runs.
    """
    return EntryAuthorizer()
