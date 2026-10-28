"""Server-side verified identity for runtime entries (B09).

The Controller process — never a request body, a tool argument or an MCP
``_meta`` field — is the authority for the principal and the project scope of a
submit, a control intent, a prepare operation or an artifact read.

This module reuses the frozen identity model:

* :func:`acp_services.workspace.identity_mapping.local_identity` builds the
  local external identity (``issuer=local``);
* :class:`acp_services.contracts.access.LocalPrincipal` resolves the fixed
  ``local-single`` principal (a client ``tenant_id`` is ignored by design);
* :class:`acp_services.workspace.entry_authz.EntryAuthorizer` resolves the
  authoritative project scope through :func:`resolve_scope`.

The only request input is a **project hint**.  It can narrow a tenant-wide
server scope to one project, but it can never widen a server-pinned project
scope: a different project is a denial, not a second scope.  This is local
single-principal enforcement, *not* multi-tenant security (see
``capabilities.admission``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from acp_services.contracts.access import (
    LOCAL_PRINCIPAL_ID,
    AccessAction,
    LocalPrincipal,
    Principal,
    ReasonCode,
)
from acp_services.workspace.entry_authz import EntryAuthorizer, EntryDecision
from acp_services.workspace.identity_mapping import ExternalIdentity, local_identity

__all__ = [
    "SERVER_PROJECT_ENV",
    "ServerIdentity",
    "resolve_server_project",
]

#: Deployment-level project pin for this Controller process.
SERVER_PROJECT_ENV = "ACP_SERVER_PROJECT"


def _clean_project(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_server_project() -> str | None:
    """Return the Controller-pinned project (env only, never a request field)."""
    return _clean_project(os.environ.get(SERVER_PROJECT_ENV))


@dataclass(frozen=True)
class ServerIdentity:
    """The verified principal + project scope of the Controller process."""

    principal: Principal
    external: ExternalIdentity
    verified_project: str | None
    authorizer: EntryAuthorizer

    @classmethod
    def build(
        cls,
        *,
        verified_project: str | None = None,
        display_name: str | None = None,
        require_grant: bool = False,
    ) -> ServerIdentity:
        """Build the local-single server identity.

        ``verified_project`` is the server-pinned project (``None`` = the local
        tenant is tenant-wide, the legacy local-single behaviour).  The external
        identity is a local binding only; it never carries roles or grants.
        """
        project = _clean_project(verified_project)
        external = local_identity(LOCAL_PRINCIPAL_ID, display_name=display_name)
        principal = LocalPrincipal.resolve(display_name=external.display_name)
        known_projects = None if project is None else {project: principal.tenant_id}
        authorizer = EntryAuthorizer(
            principal=principal,
            known_projects=known_projects,
            require_grant=require_grant,
        )
        return cls(
            principal=principal,
            external=external,
            verified_project=project,
            authorizer=authorizer,
        )

    @classmethod
    def from_environment(cls) -> ServerIdentity:
        """Build from the process environment (composition-root fallback)."""
        return cls.build(verified_project=resolve_server_project())

    @classmethod
    def from_principal(
        cls,
        principal: Principal,
        *,
        external: ExternalIdentity | None = None,
        verified_project: str | None = None,
        known_projects: Mapping[str, str | None] | None = None,
        action_ceiling: Sequence[str] | None = None,
        require_grant: bool = False,
    ) -> ServerIdentity:
        """Build a server identity from an already-verified non-local principal.

        B15 uses this for the opt-in API-key identity path: the principal,
        tenant, roles and credential action ceiling all come from the identity
        store, never from the request.  ``known_projects`` is the project map the
        Controller itself resolved; a request project outside it is denied.
        """
        project = _clean_project(verified_project)
        authorizer = EntryAuthorizer(
            principal=principal,
            known_projects=known_projects,
            require_grant=require_grant,
            action_ceiling=action_ceiling,
        )
        return cls(
            principal=principal,
            external=external or local_identity(principal.principal_id),
            verified_project=project,
            authorizer=authorizer,
        )

    # -- submit ---------------------------------------------------------

    def resolve_submit_project(self, hint: str | None) -> EntryDecision:
        """Authorize a submit and return the effective project decision.

        The principal is the server principal.  A hint equal to the pinned
        project (or absent) is allowed; a different hint is denied.  With no
        pinned project the tenant-wide scope is narrowed to the hint.
        """
        requested = _clean_project(hint)
        project = requested if requested is not None else self.verified_project
        decision = self.authorizer.authorize_scope(
            project_id=project,
            action=AccessAction.WRITE,
            resource_id="submit",
        )
        return decision

    # -- job-scoped entries ---------------------------------------------

    def authorize_job_project(
        self,
        project_id: str | None,
        *,
        action: str = AccessAction.READ,
        job_id: str = "",
        resource_id: str = "",
    ) -> EntryDecision:
        """Authorize a job-scoped entry against the job's verified project.

        A job without a project belongs to the tenant-wide local scope.  A
        project different from the pinned server project is denied; the caller
        maps that denial to the same not-found shape as an unknown job so task
        existence never leaks across projects.
        """
        project = _clean_project(project_id) or self.verified_project
        return self.authorizer.authorize_scope(
            project_id=project,
            action=action,
            job_id=job_id,
            resource_id=resource_id,
        )

    # -- projections ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """P2-compatible identity projection (no multi-tenant claim)."""
        return {
            "principal_id": self.principal.principal_id,
            "issuer": self.external.issuer,
            "subject": self.external.subject,
            "tenant_id": self.principal.tenant_id,
            "deployment_tier": self.principal.deployment_tier,
            "verified_project": self.verified_project,
            "project_hint_policy": "narrow-only",
            "multi_tenant_security": False,
        }


def deny_reason(decision: EntryDecision) -> str:
    """Return a stable reason code for a denied scope decision."""
    return str(decision.reason_code or ReasonCode.NO_GRANT)
