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
    # B01-2 identity model layering (todo 7) — additive
    "Role",
    "BillingSubjectKind",
    "Tier",
    "User",
    "TenantScope",
    "ProjectScope",
    "BillingSubject",
    "ResourceGrantSpec",
    "ScopeResolution",
    "IdentityError",
    "ScopeContradictionError",
    "tier_roles",
    "tier_baseline_actions",
    "validate_scope",
    "resolve_scope",
    "SOURCE_EXPLICIT",
    "SOURCE_LEGACY_ADOPTED",
    "SOURCE_TENANT_WIDE",
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


# ---------------------------------------------------------------------------
# Identity model layering (B01-2 / todo 7)
# ---------------------------------------------------------------------------
#
# Five independent facts, deliberately never collapsed into one (MU-01):
#   * User            — *who* the account is (subject, not a tenant)
#   * TenantScope     — the project boundary (tenant owns projects + members)
#   * Role            — explicit membership capability; a Tier NEVER grants one
#   * Tier            — the deployment/authorisation tier axis (see §1.5)
#   * BillingSubject  — the payer; a separate fact that NEVER authorises
#
# ``resolve_scope`` is the single place a client-supplied ``requested_project``
# is turned into authority, and it always derives the tenant from the verified
# ``Principal`` — never from the request.


class Role(str):
    """Explicit membership capability vocabulary.

    A ``Role`` is issued by an administrator; it is never derived from a
    :class:`Tier` and never from remote execution.  ``MEMBER`` is the default,
    ``OPERATOR``/``ADMIN`` are management roles (see :data:`MANAGEMENT`).
    """

    MEMBER = "member"
    OPERATOR = "operator"
    ADMIN = "admin"
    ALL = frozenset({MEMBER, OPERATOR, ADMIN})
    MANAGEMENT = frozenset({OPERATOR, ADMIN})


class BillingSubjectKind(str):
    """Billing subject kind vocabulary — contract-only, no billing logic."""

    ACCOUNT = "account"
    PROJECT = "project"
    TENANT = "tenant"
    COST_CENTER = "cost-center"
    ALL = frozenset({ACCOUNT, PROJECT, TENANT, COST_CENTER})


class Tier(DeploymentTier):
    """Identity-model spelling of the deployment tier axis (§1.5).

    Same frozen values and ``str`` semantics as :class:`DeploymentTier`
    (``local-single`` / ``lab`` / ``hosted``), exposed under the identity-model
    name so a tier can never be confused with an :class:`ExecutionMode`.  A
    tier confers capability baselines only — it **never** grants a
    :class:`Role` (see :func:`tier_roles`).
    """


SOURCE_EXPLICIT = "explicit"
SOURCE_LEGACY_ADOPTED = "legacy-adopted"
SOURCE_TENANT_WIDE = "tenant-wide"

_UNKNOWN = object()


def tier_roles(tier: str) -> tuple[str, ...]:
    """Return the roles a tier confers automatically — always empty.

    Executable statement of "tier does not grant a role": authority roles are
    explicit facts on :class:`User`/membership, independent of ``tier``.
    """
    del tier
    return ()


_TIER_BASELINE_ACTIONS: dict[str, tuple[str, ...]] = {
    DeploymentTier.LOCAL_SINGLE: (
        AccessAction.READ,
        AccessAction.WRITE,
        AccessAction.CONTROL,
    ),
    DeploymentTier.LAB: (AccessAction.READ, AccessAction.WRITE),
    DeploymentTier.HOSTED: (AccessAction.READ,),
}


def tier_baseline_actions(tier: str) -> tuple[str, ...]:
    """Return the minimal action set an authenticated member of *tier* holds.

    Never contains :data:`AccessAction.ADMIN` — management authority is a
    :class:`Role`.  An unknown/disabled tier downgrades to the minimal safe set
    (``read`` only).
    """
    return _TIER_BASELINE_ACTIONS.get(str(tier), (AccessAction.READ,))


@dataclass(frozen=True)
class User:
    """Account subject — a person/service, **not** a tenant and not a project.

    A ``User`` alone carries no project authority; scope is derived from the
    authenticated :class:`Principal` plus tenant membership.  ``roles`` are
    explicit and ``disabled`` users are denied.
    """

    user_id: str
    display_name: str | None = None
    roles: tuple[str, ...] = ()
    disabled: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> User:
        return cls(
            user_id=str(data["user_id"]),
            display_name=(None if data.get("display_name") is None else str(data["display_name"])),
            roles=_as_tuple(data.get("roles")),
            disabled=bool(data.get("disabled", False)),
        )

    def has_role(self, role: str) -> bool:
        return role in Role.ALL and role in self.roles

    @property
    def is_management(self) -> bool:
        return not self.disabled and any(role in Role.MANAGEMENT for role in self.roles)


@dataclass(frozen=True)
class TenantScope:
    """Tenant = project boundary (§1.1 ``tenant_id``).

    A tenant owns zero or more legitimate projects and lists its members.  An
    empty ``project_ids`` means the tenant is not enumerating here; callers then
    supply ``known_projects`` to :func:`resolve_scope`.
    """

    tenant_id: str
    member_user_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TenantScope:
        return cls(
            tenant_id=str(data["tenant_id"]),
            member_user_ids=_as_tuple(data.get("member_user_ids")),
            project_ids=_as_tuple(data.get("project_ids")),
        )

    def contains_user(self, user_id: str) -> bool:
        return user_id in self.member_user_ids

    def contains_project(self, project_id: str) -> bool:
        return project_id in self.project_ids


@dataclass(frozen=True)
class ProjectScope:
    """Resolved project boundary for one :class:`Principal`.

    ``source`` records how the project entered scope: :data:`SOURCE_EXPLICIT`,
    :data:`SOURCE_LEGACY_ADOPTED` (deterministic adoption, see
    :func:`resolve_scope`) or :data:`SOURCE_TENANT_WIDE`.
    """

    project_id: str
    tenant_id: str
    adopted_legacy: bool = False
    source: str = SOURCE_EXPLICIT

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProjectScope:
        return cls(
            project_id=str(data["project_id"]),
            tenant_id=str(data["tenant_id"]),
            adopted_legacy=bool(data.get("adopted_legacy", False)),
            source=str(data.get("source", SOURCE_EXPLICIT)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "adopted_legacy": self.adopted_legacy,
            "source": self.source,
        }


@dataclass(frozen=True)
class BillingSubject:
    """Payer identity — a **separate** fact from user/tenant.

    Contract fields only; no charge calculation lives here, and no
    authorisation decision consults it.  ``owner_tenant_id`` is a *reference*
    checked for consistency by :func:`validate_scope`, never for granting.
    """

    billing_subject_id: str
    kind: str = BillingSubjectKind.ACCOUNT
    owner_tenant_id: str | None = None
    display_name: str | None = None
    external_ref: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BillingSubject:
        return cls(
            billing_subject_id=str(data["billing_subject_id"]),
            kind=str(data.get("kind", BillingSubjectKind.ACCOUNT)),
            owner_tenant_id=(
                None if data.get("owner_tenant_id") is None else str(data["owner_tenant_id"])
            ),
            display_name=(None if data.get("display_name") is None else str(data["display_name"])),
            external_ref=(None if data.get("external_ref") is None else str(data["external_ref"])),
        )


@dataclass(frozen=True)
class ResourceGrantSpec(ResourceGrant):
    """Variant of :class:`ResourceGrant` carrying resource-limit placeholders.

    The frozen first-slice contract keeps :class:`ResourceGrant` at exactly its
    eight fields, so the reserved quota placeholders live here on a subclass
    (``isinstance(spec, ResourceGrant)`` holds).  Every limit is ``None`` until
    an admission policy fills it — this module enforces nothing.
    """

    cores: int | None = None
    memory_mb: int | None = None
    concurrency: int | None = None
    duration_s: int | None = None
    storage_bytes: int | None = None
    queue: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResourceGrantSpec:
        return cls(
            grant_id=str(data["grant_id"]),
            principal_id=str(data["principal_id"]),
            scope_id=str(data["scope_id"]),
            resource_id=str(data["resource_id"]),
            actions=_as_tuple(data.get("actions")),
            granted_at=str(data["granted_at"]),
            expires_at=(None if data.get("expires_at") is None else str(data["expires_at"])),
            basis_revision=int(data.get("basis_revision", 0)),
            cores=_opt_int(data.get("cores")),
            memory_mb=_opt_int(data.get("memory_mb")),
            concurrency=_opt_int(data.get("concurrency")),
            duration_s=_opt_int(data.get("duration_s")),
            storage_bytes=_opt_int(data.get("storage_bytes")),
            queue=(None if data.get("queue") is None else str(data["queue"])),
        )


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(frozen=True)
class ScopeResolution:
    """Result of :func:`resolve_scope` — an :class:`AccessDecision` + scope.

    AccessDecision-shaped: ``effect`` / ``reason_code`` / ``available_actions``
    forward to ``decision``, so a consumer branches on the verdict without a
    second DTO.  ``deployment_tier`` and ``execution_mode`` are reported as two
    separate axes (§1.5): a remote execution mode never implies multi-user
    hosting.
    """

    decision: AccessDecision
    requested_project: str | None
    tenant_id: str
    project_scope: ProjectScope | None = None
    adopted_legacy: bool = False
    deployment_tier: str = Tier.LOCAL_SINGLE
    execution_mode: str = ExecutionMode.LOCAL

    @property
    def effect(self) -> str:
        return self.decision.effect

    @property
    def reason_code(self) -> str:
        return self.decision.reason_code

    @property
    def available_actions(self) -> tuple[str, ...] | None:
        return self.decision.available_actions

    @property
    def principal_id(self) -> str:
        return self.decision.principal_id

    @property
    def scope_id(self) -> str:
        return self.decision.scope_id

    @property
    def allowed(self) -> bool:
        return self.decision.effect == DecisionEffect.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "requested_project": self.requested_project,
            "tenant_id": self.tenant_id,
            "project_scope": None if self.project_scope is None else self.project_scope.to_dict(),
            "adopted_legacy": self.adopted_legacy,
            "deployment_tier": self.deployment_tier,
            "execution_mode": self.execution_mode,
        }


class IdentityError(Exception):
    """Base class for identity/scope contract violations."""


class ScopeContradictionError(IdentityError):
    """Raised by :func:`validate_scope` on contradictory scope facts."""


def validate_scope(
    *,
    user: User | None = None,
    tenant: TenantScope | None = None,
    project: ProjectScope | None = None,
    grants: Sequence[ResourceGrant] = (),
    billing_subject: BillingSubject | None = None,
) -> None:
    """Check the relationship facts of one identity bundle; raise on conflict.

    Validates: tenant/project membership, explicit role vocabulary, grant
    references, and billing-subject *reference* consistency.  Billing never
    authorises here — a mismatch is only a reference contradiction.

    Raises:
        ScopeContradictionError: when two supplied facts cannot both hold.
    """
    if user is not None:
        unknown = sorted(role for role in user.roles if role not in Role.ALL)
        if unknown:
            raise ScopeContradictionError(f"unknown role(s) for user {user.user_id!r}: {unknown}")

    if tenant is not None:
        if not tenant.tenant_id:
            raise ScopeContradictionError("tenant_id must be non-empty")

    if user is not None and tenant is not None:
        if tenant.member_user_ids and not tenant.contains_user(user.user_id):
            raise ScopeContradictionError(
                f"user {user.user_id!r} is not a member of tenant {tenant.tenant_id!r}"
            )

    if project is not None:
        if tenant is not None and project.tenant_id != tenant.tenant_id:
            raise ScopeContradictionError(
                f"project {project.project_id!r} belongs to tenant {project.tenant_id!r}, "
                f"not {tenant.tenant_id!r}"
            )
        if (
            tenant is not None
            and tenant.project_ids
            and not tenant.contains_project(project.project_id)
        ):
            raise ScopeContradictionError(
                f"project {project.project_id!r} is not declared by tenant {tenant.tenant_id!r}"
            )

    for grant in grants:
        if not grant.scope_id:
            raise ScopeContradictionError(f"grant {grant.grant_id!r} has an empty scope reference")
        if not grant.resource_id:
            raise ScopeContradictionError(
                f"grant {grant.grant_id!r} has an empty resource reference"
            )
        if (
            tenant is not None
            and tenant.project_ids
            and grant.resource_id not in tenant.project_ids
        ):
            raise ScopeContradictionError(
                f"grant {grant.grant_id!r} references resource {grant.resource_id!r} "
                f"outside tenant {tenant.tenant_id!r}"
            )

    if billing_subject is not None and tenant is not None:
        owner = billing_subject.owner_tenant_id
        if owner is not None and owner != tenant.tenant_id:
            raise ScopeContradictionError(
                f"billing subject {billing_subject.billing_subject_id!r} references tenant "
                f"{owner!r}, not {tenant.tenant_id!r}"
            )


def _known_project_tenants(
    known_projects: Mapping[str, str | None] | Iterable[str] | None,
) -> dict[str, str | None]:
    """Normalise ``known_projects`` to ``{project_id: tenant_id | None}``.

    ``None`` as a tenant marks a **legacy** (tenant-less) project id; a plain
    iterable has no tenant metadata and is treated as entirely legacy.
    """
    if known_projects is None:
        return {}
    if isinstance(known_projects, Mapping):
        return {
            str(project_id): (None if tenant_id is None else str(tenant_id))
            for project_id, tenant_id in known_projects.items()
        }
    return {str(project_id): None for project_id in known_projects}


def _scope_decision_id(
    principal: Principal,
    project: str | None,
    effect: str,
    reason_code: str,
) -> str:
    material = f"{principal.principal_id}|{project}|{effect}|{reason_code}"
    return "dec_" + sha256(material.encode("utf-8")).hexdigest()[:16]


def _effective_roles(principal: Principal, user: User | None) -> set[str]:
    roles = set(principal.roles)
    if user is not None:
        roles.update(user.roles)
    return roles


def _granted_actions(
    principal: Principal,
    grants: Sequence[ResourceGrant],
    project: str | None,
) -> set[str]:
    actions: set[str] = set()
    for grant in grants:
        if grant.principal_id != principal.principal_id:
            continue
        if project is not None and grant.resource_id != project:
            continue
        actions.update(action for action in grant.actions if action in AccessAction.ALL)
    return actions


def resolve_scope(
    principal: Principal,
    requested_project: str | None,
    *,
    known_projects: Mapping[str, str | None] | Iterable[str] | None = None,
    tenant_scope: TenantScope | None = None,
    user: User | None = None,
    grants: Sequence[ResourceGrant] = (),
    execution_mode: str = ExecutionMode.LOCAL,
    now: datetime | None = None,
) -> ScopeResolution:
    """Resolve a client ``requested_project`` into authoritative scope.

    The verified ``principal`` is the sole authority: its ``tenant_id`` and
    ``deployment_tier`` are never taken from the request.  ``requested_project=None``
    means tenant-wide scope (local-single legitimately keeps **many** projects).

    Deterministic legacy adoption rule: ``known_projects`` maps each known
    project id to its owning tenant (``None`` = legacy/tenant-less).  A project
    is admitted iff its id is known **and** (its tenant equals the principal's
    tenant, or it is legacy and the principal's tier is ``local-single``).
    Unknown ids are denied ``no_grant``; known cross-tenant ids are denied
    ``scope_mismatch``.

    ``execution_mode`` is recorded as a separate axis; remote execution does not
    imply multi-user hosting and never widens the tier baseline.
    """
    decided_at = (now or datetime.now(timezone.utc)).isoformat()
    tenant_id = principal.tenant_id
    tier = str(principal.deployment_tier)
    projects = _known_project_tenants(known_projects)
    if tenant_scope is not None and tenant_scope.project_ids:
        for project_id in tenant_scope.project_ids:
            projects.setdefault(project_id, tenant_scope.tenant_id)

    def verdict(
        effect: str,
        reason_code: str,
        *,
        actions: tuple[str, ...] | None = None,
        project_scope: ProjectScope | None = None,
        adopted: bool = False,
    ) -> ScopeResolution:
        scope_ref = project_scope.project_id if project_scope is not None else f"tenant:{tenant_id}"
        decision = AccessDecision(
            decision_id=_scope_decision_id(principal, requested_project, effect, reason_code),
            effect=effect,
            principal_id=principal.principal_id,
            scope_id=scope_ref,
            reason_code=reason_code,
            decided_at=decided_at,
            available_actions=actions,
        )
        return ScopeResolution(
            decision=decision,
            requested_project=requested_project,
            tenant_id=tenant_id,
            project_scope=project_scope,
            adopted_legacy=adopted,
            deployment_tier=tier,
            execution_mode=str(execution_mode),
        )

    def deny(reason_code: str) -> ScopeResolution:
        return verdict(DecisionEffect.DENY, reason_code)

    if str(principal.kind) not in PrincipalKind.ALL or str(principal.kind) == (
        PrincipalKind.ANONYMOUS
    ):
        return deny(ReasonCode.NO_GRANT)

    if user is not None and user.disabled:
        return deny(ReasonCode.REVOKED)

    if tenant_scope is not None and tenant_scope.tenant_id != tenant_id:
        return deny(ReasonCode.SCOPE_MISMATCH)

    if (
        user is not None
        and tenant_scope is not None
        and tenant_scope.member_user_ids
        and not tenant_scope.contains_user(user.user_id)
    ):
        return deny(ReasonCode.SCOPE_MISMATCH)

    adopted = False
    project_scope: ProjectScope | None = None
    if requested_project is not None:
        owner = projects.get(requested_project, _UNKNOWN)
        if owner is _UNKNOWN:
            return deny(ReasonCode.NO_GRANT)
        if owner is not None and str(owner) != tenant_id:
            return deny(ReasonCode.SCOPE_MISMATCH)
        adopted = owner is None
        if adopted and tier != DeploymentTier.LOCAL_SINGLE:
            return deny(ReasonCode.SCOPE_MISMATCH)
        project_scope = ProjectScope(
            project_id=requested_project,
            tenant_id=tenant_id,
            adopted_legacy=adopted,
            source=SOURCE_LEGACY_ADOPTED if adopted else SOURCE_EXPLICIT,
        )

    actions = set(tier_baseline_actions(tier))
    if any(role in Role.MANAGEMENT for role in _effective_roles(principal, user)):
        actions.add(AccessAction.ADMIN)
    actions.update(_granted_actions(principal, grants, requested_project))

    return verdict(
        DecisionEffect.ALLOW,
        ReasonCode.GRANTED,
        actions=tuple(sorted(actions)),
        project_scope=project_scope,
        adopted=adopted,
    )
