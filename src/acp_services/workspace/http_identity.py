"""HTTP authentication-to-principal binding for opt-in multi-user deployments.

B15 (plan todo 17) wires the **existing** identity primitive to the HTTP entry
without creating a second authority:

* :class:`~acp_services.workspace.keys.ApiKeyManager` verifies the credential
  (digest, revocation, expiry, scope);
* :class:`~acp_services.workspace.identity_lifecycle.IdentityLifecycle` resolves
  the account/principal and its status (a revoked/disabled account is denied);
* :class:`~acp_services.workspace.entry_authz.EntryAuthorizer` resolves the
  authoritative project scope and intersects it with the key's action ceiling.

The default deployment stays ``local-single``.  Multi-user is explicitly opt-in
via :data:`IDENTITY_MODE_ENV`; when it is off this module is never consulted by
the HTTP boundary and existing clients keep their trusted-local behaviour.

A request supplies a credential only.  A ``project_id``/workspace/tenant/role in
a header or body is a **hint** at most: the principal, tenant, roles and verified
project always come from the server-side identity store.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp_services.contracts.access import (
    AccessAction,
    DecisionEffect,
    DeploymentTier,
    Principal,
    PrincipalKind,
    ReasonCode,
)
from acp_services.workspace.boundary import API_KEY_HEADER, AUTHORIZATION_HEADER
from acp_services.workspace.entry_authz import EntryAuthorizer
from acp_services.workspace.identity_lifecycle import (
    OIDC_UNAVAILABLE_REASON,
    AccountStatus,
    IdentityLifecycle,
)
from acp_services.workspace.identity_mapping import ExternalIdentity, local_identity
from acp_services.workspace.keys import ApiKeyManager, KeyReason
from acp_services.workspace.server_identity import ServerIdentity

__all__ = [
    "IDENTITY_MODE_ENV",
    "IDENTITY_DB_ENV",
    "IDENTITY_TENANT_ENV",
    "DEFAULT_TENANT_ID",
    "HTTP_UNAUTHORIZED",
    "HTTP_FORBIDDEN",
    "IdentityMode",
    "IdentityDenial",
    "VerifiedRequestIdentity",
    "MultiUserIdentity",
    "credential_from_headers",
    "remote_identity_providers",
    "stdio_identity_binding",
    "STDIO_PERMISSION_CEILING",
]

IDENTITY_MODE_ENV = "ACP_IDENTITY_MODE"
IDENTITY_DB_ENV = "ACP_IDENTITY_DB"
IDENTITY_TENANT_ENV = "ACP_IDENTITY_TENANT"
DEFAULT_TENANT_ID = "acp-multiuser"

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403

#: The stdio proxy may never request management authority, even when the bound
#: account is an administrator.  It is a transport ceiling, not a grant.
STDIO_PERMISSION_CEILING = frozenset({AccessAction.READ, AccessAction.WRITE, AccessAction.CONTROL})


class IdentityMode(str):
    """How an HTTP entry authenticates its caller."""

    LOCAL_SINGLE = "local-single"
    API_KEY = "api-key"
    ALL = frozenset({LOCAL_SINGLE, API_KEY})


@dataclass(frozen=True)
class IdentityDenial:
    """A refused authentication.  The wire shape is deliberately uniform."""

    reason: str
    http_status: int = HTTP_UNAUTHORIZED
    detail: str = "authentication required"

    def to_dict(self) -> dict[str, str]:
        """Return the non-disclosing HTTP error body.

        Unknown, malformed, revoked, expired and unregistered credentials all
        share this one shape so the response cannot be used as a key/account
        existence oracle.
        """
        return {"code": "unauthenticated", "message": self.detail}


@dataclass(frozen=True)
class VerifiedRequestIdentity:
    """The server-resolved identity for one HTTP request."""

    principal: Principal
    external: ExternalIdentity
    mode: str
    server_identity: ServerIdentity
    key_id: str | None = None
    key_scope: str | None = None
    key_project: str | None = None
    actions: tuple[str, ...] = ()
    usage_capabilities: tuple[str, ...] = ()
    account_id: str | None = None

    @property
    def verified_project(self) -> str | None:
        return self.server_identity.verified_project

    @property
    def entry_authorizer(self) -> EntryAuthorizer:
        return self.server_identity.authorizer

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": self.mode,
            "principal": {
                "principal_id": self.principal.principal_id,
                "kind": self.principal.kind,
                "tenant_id": self.principal.tenant_id,
                "roles": list(self.principal.roles),
                "deployment_tier": self.principal.deployment_tier,
            },
            "issuer": self.external.issuer,
            "subject": self.external.subject,
            "verified_project": self.verified_project,
            "key_id": self.key_id,
            "key_scope": self.key_scope,
            "key_project": self.key_project,
            "actions": list(self.actions),
            "usage_capabilities": list(self.usage_capabilities),
            "account_id": self.account_id,
            "project_hint_policy": "narrow-only",
            "multi_tenant_security": "api-key-scoped",
        }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return str(value)
    lowered = str(name).lower()
    for key, candidate in headers.items():
        if str(key).lower() == lowered:
            return str(candidate)
    return None


def credential_from_headers(headers: Mapping[str, str]) -> str | None:
    """Extract the credential from ``X-API-Key`` or a Bearer ``Authorization``.

    Only the credential is returned; no other header is an identity input.
    """
    direct = _header(headers, API_KEY_HEADER)
    if direct:
        return direct.strip() or None
    authorization = _header(headers, AUTHORIZATION_HEADER)
    if not authorization:
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def remote_identity_providers() -> dict[str, dict[str, str]]:
    """Report remote IdPs truthfully as unsupported (no capability claim)."""
    return {
        "oidc": {"state": "unsupported", "reason": OIDC_UNAVAILABLE_REASON},
        "external_iam": {
            "state": "unsupported",
            "reason": (
                "External IAM/federation is not implemented; enforced modes are "
                "local-single and project-scoped API keys."
            ),
        },
    }


def stdio_identity_binding() -> dict[str, Any]:
    """The stdio proxy identity binding + permission ceiling (contract prose)."""
    return {
        "binding": "server-resolved-principal",
        "identity_source": "api-key-verified-by-controller",
        "tool_arguments_are_identity": False,
        "shared_launch_allowed": False,
        "permission_ceiling": sorted(STDIO_PERMISSION_CEILING),
        "permission_ceiling_rule": "bound-actions-intersect-ceiling",
        "local_single_default": True,
        "multi_user_requires_explicit_api_key": True,
    }


class MultiUserIdentity:
    """Resolve an HTTP credential to the Controller's verified principal.

    The object is built only when :data:`IDENTITY_MODE_ENV` selects
    ``api-key``.  Construction is cheap; each :meth:`resolve` re-reads the key
    and account rows so revocation takes effect on the very next request (and on
    the next poll of an already-open event stream).
    """

    def __init__(
        self,
        *,
        mode: str,
        tenant_id: str,
        key_manager: ApiKeyManager | None = None,
        lifecycle: IdentityLifecycle | None = None,
        known_projects: Mapping[str, str | None] | None = None,
    ) -> None:
        if mode not in IdentityMode.ALL:
            raise ValueError(f"unknown identity mode {mode!r}")
        if mode == IdentityMode.API_KEY and (key_manager is None or lifecycle is None):
            raise ValueError("api-key identity mode requires a key manager and lifecycle store")
        self._mode = str(mode)
        self._tenant_id = str(tenant_id)
        self._key_manager = key_manager
        self._lifecycle = lifecycle
        self._known_projects = (
            None if known_projects is None else {str(k): v for k, v in known_projects.items()}
        )

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def enabled(self) -> bool:
        return self._mode == IdentityMode.API_KEY

    @classmethod
    def from_environment(cls, base_dir: Path | str | None = None) -> MultiUserIdentity:
        """Build from process configuration; ``local-single`` by default."""
        mode = (os.environ.get(IDENTITY_MODE_ENV) or IdentityMode.LOCAL_SINGLE).strip().lower()
        tenant = (os.environ.get(IDENTITY_TENANT_ENV) or DEFAULT_TENANT_ID).strip()
        if mode != IdentityMode.API_KEY:
            return cls(mode=IdentityMode.LOCAL_SINGLE, tenant_id=tenant)
        db_value = os.environ.get(IDENTITY_DB_ENV)
        if db_value:
            db_path = Path(db_value)
        elif base_dir is not None:
            db_path = Path(base_dir) / "acp_identity.db"
        else:
            db_path = Path.home() / ".local/share/acp" / "acp_identity.db"
        key_manager = ApiKeyManager(db_path)
        lifecycle = IdentityLifecycle(db_path, key_manager=key_manager)
        return cls(
            mode=IdentityMode.API_KEY,
            tenant_id=tenant,
            key_manager=key_manager,
            lifecycle=lifecycle,
        )

    def resolve(
        self,
        credential: str | None,
        *,
        project_hint: str | None = None,
        required_action: str | None = None,
    ) -> tuple[VerifiedRequestIdentity | None, IdentityDenial | None]:
        """Resolve *credential* into the server-side principal.

        Never raises for a denial.  ``project_hint`` is validated against the
        credential's own project scope but never becomes the principal's scope.
        """
        if not self.enabled:
            raise RuntimeError("multi-user identity is not enabled")
        if not credential:
            return None, IdentityDenial(KeyReason.NO_GRANT)
        assert self._key_manager is not None
        assert self._lifecycle is not None

        decision = self._key_manager.verify_identity(credential, project=project_hint)
        if not decision.allowed:
            return None, IdentityDenial(str(decision.reason))

        key_id = decision.key_id or ""
        account = self._lifecycle.account_for_key(key_id)
        if account is None:
            return None, IdentityDenial("unknown_account")
        if account.status != AccountStatus.ACTIVE:
            return None, IdentityDenial(f"account_{account.status}")

        principal = self._lifecycle.principal_for_account(
            account.account_id,
            tenant_id=self._tenant_id,
            kind=PrincipalKind.USER,
            deployment_tier=DeploymentTier.LOCAL_SINGLE,
        )
        actions = tuple(action for action in decision.actions if action in AccessAction.ALL)
        if required_action is not None and required_action not in actions:
            return None, IdentityDenial(KeyReason.NO_GRANT, HTTP_FORBIDDEN)

        key_project = decision.project
        if key_project is not None:
            known_projects: dict[str, str | None] = {key_project: self._tenant_id}
        else:
            known_projects = dict(self._known_projects or {})
        server_identity = ServerIdentity.from_principal(
            principal,
            external=local_identity(
                principal.principal_id,
                display_name=account.display_name,
            ),
            verified_project=key_project,
            known_projects=known_projects,
            action_ceiling=actions,
        )
        return (
            VerifiedRequestIdentity(
                principal=principal,
                external=local_identity(
                    principal.principal_id,
                    display_name=account.display_name,
                ),
                mode=self._mode,
                server_identity=server_identity,
                key_id=key_id,
                key_scope=decision.scope,
                key_project=key_project,
                actions=actions,
                usage_capabilities=tuple(decision.usage_capabilities),
                account_id=account.account_id,
            ),
            None,
        )

    def scope_projection(self) -> dict[str, Any]:
        """Read-only projection of the configured project map (no secrets)."""
        return {
            "mode": self._mode,
            "tenant_id": self._tenant_id,
            "known_projects": sorted(self._known_projects or {}),
        }


def scoped_project(identity: VerifiedRequestIdentity | None, requested: str | None) -> str | None:
    """Return the effective read project; a hint may only match the verified one.

    Raises:
        PermissionError: the hint names a project outside the credential scope.
    """
    if identity is None:
        return requested
    verified = identity.verified_project
    if verified is None:
        return requested
    if requested is not None and str(requested) != verified:
        raise PermissionError(ReasonCode.SCOPE_MISMATCH)
    return verified


def authorize_action_ceiling(
    identity: VerifiedRequestIdentity | None,
    action: str,
) -> bool:
    """Whether the credential's ceiling covers *action* (no-op when local-single)."""
    if identity is None:
        return True
    return action in identity.actions


def scope_decision_allows(decision: Any) -> bool:
    """Whether an :class:`~acp_services.contracts.access.AccessDecision` allows."""
    return getattr(decision, "effect", None) == DecisionEffect.ALLOW


__all__ += [
    "scoped_project",
    "authorize_action_ceiling",
    "scope_decision_allows",
]
