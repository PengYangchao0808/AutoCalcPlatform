"""Access contracts — Principal / AccessScope / ResourceGrant / AccessDecision.

B01-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 6).  This module
is the executable form of the frozen first-slice Access boundary
(``docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`` §1), owned
by B.  Field **names and semantics** are frozen by that contract; consumers
(todo 7 identity model, todo 8 API keys, todo 9 OIDC mapping, todo 10 local
boundary) extend here and must not invent a parallel DTO.

Two concepts are deliberately kept apart (§1.5, review P1-8):

* ``DeploymentTier`` — *where the platform is deployed* (authorisation/isolation
  capability boundary): ``local-single`` / ``lab`` / ``hosted``.
* ``ExecutionMode`` — *where a job actually runs*: ``local`` / ``lsf`` /
  ``slurm`` / ``podman``.

They are never interchangeable.  A deployment tier does not grant roles, and a
remote execution mode does not imply multi-tenant hosting.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
-------------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module is **stdlib
only**.  It lives at ``acp_services.contracts.access``; the public symbols below
are the first-slice allowlist names (``acp_services.contracts`` prefix).

Importable as ``from acp_services.contracts.access import Principal, ...``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

__all__ = [
    # Vocabularies
    "PrincipalKind",
    "AccessAction",
    "DecisionEffect",
    "ReasonCode",
    "DeploymentTier",
    "ExecutionMode",
    # Frozen dataclasses (contract §1.1–§1.4)
    "Principal",
    "AccessScope",
    "ResourceGrant",
    "AccessDecision",
    "Decision",
    # Local-single principal + minimal policy
    "LocalPrincipal",
    "LocalSingleAccessPolicy",
    "LOCAL_PRINCIPAL_ID",
    "LOCAL_TENANT_ID",
    "LOCAL_ISSUER",
    "is_loopback_host",
]

_IS0_RE = re.compile(r"[Zz]$")


# ---------------------------------------------------------------------------
# Vocabularies (str-valued so they compare/round-trip as plain strings)
# ---------------------------------------------------------------------------


class PrincipalKind(str):
    """Principal kind vocabulary (contract §1.1 ``kind``)."""

    USER = "user"
    SERVICE = "service"
    ANONYMOUS = "anonymous"
    ALL = frozenset({USER, SERVICE, ANONYMOUS})


class AccessAction(str):
    """Action vocabulary (contract §1.2 ``action`` / §1.3 ``actions``)."""

    READ = "read"
    WRITE = "write"
    CONTROL = "control"
    ADMIN = "admin"
    ALL = frozenset({READ, WRITE, CONTROL, ADMIN})


class DecisionEffect(str):
    """Decision effect — exactly two values; anything else is rejected (§1.4)."""

    ALLOW = "allow"
    DENY = "deny"
    ALL = frozenset({ALLOW, DENY})


class ReasonCode(str):
    """Decision reason vocabulary (contract §1.4 ``reason_code``)."""

    GRANTED = "granted"
    NO_GRANT = "no_grant"
    EXPIRED = "expired"
    SCOPE_MISMATCH = "scope_mismatch"
    REVOKED = "revoked"
    ALL = frozenset({GRANTED, NO_GRANT, EXPIRED, SCOPE_MISMATCH, REVOKED})


class DeploymentTier(str):
    """Deployment tier — NOT the same axis as :class:`ExecutionMode` (§1.5)."""

    LOCAL_SINGLE = "local-single"
    LAB = "lab"
    HOSTED = "hosted"
    ALL = frozenset({LOCAL_SINGLE, LAB, HOSTED})


class ExecutionMode(str):
    """Execution mode — where a job runs, NOT a deployment tier (§1.5)."""

    LOCAL = "local"
    LSF = "lsf"
    SLURM = "slurm"
    PODMAN = "podman"
    ALL = frozenset({LOCAL, LSF, SLURM, PODMAN})


# ---------------------------------------------------------------------------
# Frozen dataclasses (contract §1.1–§1.4)
# ---------------------------------------------------------------------------


def _as_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a JSON list/None into an immutable tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class Principal:
    """Stable authenticated subject (contract §1.1).

    ``principal_id`` is derived once from the issuer+subject mapping and does
    not change across sessions.  ``tenant_id`` is the tenant/project boundary;
    for the ``local-single`` tier it is the single default tenant.  ``roles``
    may be empty (legal — empty means no roles, not an error).
    """

    principal_id: str
    kind: str
    tenant_id: str
    roles: tuple[str, ...] = ()
    deployment_tier: str = DeploymentTier.LOCAL_SINGLE
    display_name: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Principal:
        return cls(
            principal_id=str(data["principal_id"]),
            kind=str(data["kind"]),
            tenant_id=str(data["tenant_id"]),
            roles=_as_tuple(data.get("roles")),
            deployment_tier=str(data.get("deployment_tier", DeploymentTier.LOCAL_SINGLE)),
            display_name=(None if data.get("display_name") is None else str(data["display_name"])),
        )


@dataclass(frozen=True)
class AccessScope:
    """Authorisation scope (contract §1.2).

    ``project_ids`` empty == every project within the tenant (local-single);
    a non-empty list narrows the scope to those project ids.
    """

    scope_id: str
    tenant_id: str
    action: str
    resource_kinds: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AccessScope:
        return cls(
            scope_id=str(data["scope_id"]),
            tenant_id=str(data["tenant_id"]),
            action=str(data["action"]),
            resource_kinds=_as_tuple(data.get("resource_kinds")),
            project_ids=_as_tuple(data.get("project_ids")),
        )


@dataclass(frozen=True)
class ResourceGrant:
    """One grant binding a principal to a resource via a scope (contract §1.3).

    ``expires_at=None`` is legal (no expiry).  ``basis_revision`` is the owner
    revision the grant was issued against — it is **not** mixed with job/task
    revisions.
    """

    grant_id: str
    principal_id: str
    scope_id: str
    resource_id: str
    actions: tuple[str, ...]
    granted_at: str
    expires_at: str | None = None
    basis_revision: int = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceGrant:
        return cls(
            grant_id=str(data["grant_id"]),
            principal_id=str(data["principal_id"]),
            scope_id=str(data["scope_id"]),
            resource_id=str(data["resource_id"]),
            actions=_as_tuple(data.get("actions")),
            granted_at=str(data["granted_at"]),
            expires_at=(None if data.get("expires_at") is None else str(data["expires_at"])),
            basis_revision=int(data.get("basis_revision", 0)),
        )


@dataclass(frozen=True)
class AccessDecision:
    """One authorisation verdict (contract §1.4).

    ``effect`` is exactly ``allow`` or ``deny``.  ``available_actions`` is
    conditional: present on a full ``allow``, absent (``None``) marks a partial
    decision (contract §1.6 partial sample).
    """

    decision_id: str
    effect: str
    principal_id: str
    scope_id: str
    reason_code: str
    decided_at: str
    available_actions: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "decision_id": self.decision_id,
            "effect": self.effect,
            "principal_id": self.principal_id,
            "scope_id": self.scope_id,
            "reason_code": self.reason_code,
            "decided_at": self.decided_at,
        }
        if self.available_actions is not None:
            out["available_actions"] = list(self.available_actions)
        return out


# The plan prose calls the §1.4 type "Decision"; the frozen contract names it
# ``AccessDecision``.  Expose both names for one implementation.
Decision = AccessDecision


# ---------------------------------------------------------------------------
# Local-single principal
# ---------------------------------------------------------------------------

LOCAL_PRINCIPAL_ID = "principal_local"
LOCAL_TENANT_ID = "local-single"
LOCAL_ISSUER = "local"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_host(host: str | None) -> bool:
    """Return whether *host* denotes the local/loopback context.

    ``None`` (no Origin header / in-process call) is treated as local in the
    ``local-single`` tier.  Full Origin/CSRF enforcement lives in todo 10.
    """
    if host is None:
        return True
    candidate = host.strip().lower()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if ":" in candidate and candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    if candidate in _LOOPBACK_HOSTS:
        return True
    return candidate.startswith("127.")


def _parse_iso(value: str) -> datetime | None:
    text = _IS0_RE.sub("+00:00", value.strip())
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class LocalPrincipal:
    """The fixed ``local-single`` principal.

    There is exactly one local principal identity.  Resolution is driven by the
    **loopback/local context**, never by a client-supplied ``tenant_id``: any
    ``requested_tenant_id`` is ignored so a caller cannot widen or move the
    tenant boundary.
    """

    principal_id: str = LOCAL_PRINCIPAL_ID
    tenant_id: str = LOCAL_TENANT_ID
    issuer: str = LOCAL_ISSUER

    @classmethod
    def resolve(
        cls,
        *,
        source_host: str | None = None,
        requested_tenant_id: str | None = None,
        display_name: str | None = None,
        roles: Sequence[str] = (),
        kind: str = PrincipalKind.USER,
    ) -> Principal:
        """Return the fixed local principal.

        Args:
            source_host: Request origin host; diagnostic only, the tier is
                ``local-single`` regardless (todo 10 owns Origin enforcement).
            requested_tenant_id: **Ignored.** Present to make the trust
                boundary explicit — a client cannot select its own tenant.
            display_name: Optional non-authoritative display label.
            roles: Optional role set (may be empty).
            kind: Principal kind; defaults to ``user``.

        Returns:
            A frozen :class:`Principal` with the fixed local id and tenant.
        """
        del source_host, requested_tenant_id  # loopback/local context; never trusted
        return Principal(
            principal_id=cls.principal_id,
            kind=str(kind),
            tenant_id=cls.tenant_id,
            roles=_as_tuple(roles),
            deployment_tier=DeploymentTier.LOCAL_SINGLE,
            display_name=display_name,
        )


# ---------------------------------------------------------------------------
# Minimal local-single access policy
# ---------------------------------------------------------------------------


def _decision_id(principal: Principal, scope: AccessScope, resource_id: str, action: str) -> str:
    material = f"{principal.principal_id}|{scope.scope_id}|{resource_id}|{action}"
    return "dec_" + sha256(material.encode("utf-8")).hexdigest()[:16]


class LocalSingleAccessPolicy:
    """Minimal allow-list authorisation for the ``local-single`` tier.

    Semantics (contract §1.4 / §1.6):

    * **default deny** — no matching, live grant yields ``deny``/``no_grant``;
    * **explicit allow** — a live grant whose ``actions`` cover the request
      yields ``allow``/``granted`` with the union of executable actions;
    * **explicit deny reasons** — ``scope_mismatch`` (tenant/action/project),
      ``revoked`` (grant withdrawn), ``expired`` (past ``expires_at``);
    * **revocable** — :meth:`revoke` / :meth:`restore` flip a grant without
      deleting it (the testable revocation slot).

    The policy never falls back to anonymous or a wider scope on failure.
    """

    def __init__(
        self,
        *,
        grants: Iterable[ResourceGrant] = (),
        scopes: Iterable[AccessScope] | None = None,
        revoked: Iterable[str] = (),
    ) -> None:
        self._grants = list(grants)
        self._scopes = {scope.scope_id: scope for scope in (scopes or [])}
        self._revoked = set(revoked)

    # -- revocation slot ---------------------------------------------------

    def revoke(self, grant_id: str) -> None:
        """Mark *grant_id* revoked (idempotent)."""
        self._revoked.add(grant_id)

    def restore(self, grant_id: str) -> None:
        """Clear the revoked flag on *grant_id* (idempotent)."""
        self._revoked.discard(grant_id)

    def is_revoked(self, grant_id: str) -> bool:
        return grant_id in self._revoked

    def add_grant(self, grant: ResourceGrant) -> None:
        self._grants.append(grant)

    def add_scope(self, scope: AccessScope) -> None:
        self._scopes[scope.scope_id] = scope

    def scope_for(self, scope_id: str) -> AccessScope | None:
        return self._scopes.get(scope_id)

    # -- decision ----------------------------------------------------------

    def authorize(
        self,
        principal: Principal,
        scope: AccessScope,
        *,
        resource_id: str,
        action: str,
        now: datetime | None = None,
    ) -> AccessDecision:
        """Return the :class:`AccessDecision` for one (principal, scope,
        resource, action) request.  Never raises for a denial."""
        decided_at = (now or datetime.now(timezone.utc)).isoformat()

        def verdict(effect: str, reason: str, actions: tuple[str, ...] | None = None):
            return AccessDecision(
                decision_id=_decision_id(principal, scope, resource_id, action),
                effect=effect,
                principal_id=principal.principal_id,
                scope_id=scope.scope_id,
                reason_code=reason,
                decided_at=decided_at,
                available_actions=actions,
            )

        if scope.tenant_id != principal.tenant_id:
            return verdict(DecisionEffect.DENY, ReasonCode.SCOPE_MISMATCH)
        if scope.action != action:
            return verdict(DecisionEffect.DENY, ReasonCode.SCOPE_MISMATCH)
        if scope.project_ids and resource_id not in scope.project_ids:
            return verdict(DecisionEffect.DENY, ReasonCode.NO_GRANT)

        candidates = [
            grant
            for grant in self._grants
            if grant.principal_id == principal.principal_id
            and grant.scope_id == scope.scope_id
            and grant.resource_id == resource_id
            and action in grant.actions
        ]
        if not candidates:
            return verdict(DecisionEffect.DENY, ReasonCode.NO_GRANT)

        for grant in candidates:
            if self.is_revoked(grant.grant_id):
                return verdict(DecisionEffect.DENY, ReasonCode.REVOKED)

        reference = now or datetime.now(timezone.utc)
        live = [grant for grant in candidates if not _grant_expired(grant, reference)]
        if not live:
            return verdict(DecisionEffect.DENY, ReasonCode.EXPIRED)

        allowed = tuple(sorted({a for grant in live for a in grant.actions}))
        return verdict(DecisionEffect.ALLOW, ReasonCode.GRANTED, allowed)


def _grant_expired(grant: ResourceGrant, now: datetime) -> bool:
    if not grant.expires_at:
        return False
    expiry = _parse_iso(grant.expires_at)
    if expiry is None:
        return False
    reference = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return reference >= expiry
