"""Identity lifecycle — enroll/invite, first-admin bootstrap, session-or-key-only,
disable/revoke/recover (B09-6 / plan todo 73, MU-02 / MU-04 / MU-13).

This is the Workspace-side *identity state flow* that B01-3 (todo 8, API-key
primitive) and B01-4 (todo 9, external-identity binding) deliberately left out.
It implements, for the applicable deployment tiers, one complete, testable flow::

    invite_account -> accept_invitation -> login (session)  -> logout
                                        \\-> issue_key (key-only branch)
    bootstrap_first_admin (no universal default password)
    disable_account  -> (immediate) sessions + keys revoked, live event streams closed
    revoke_account   -> (immediate) same, terminal-er credential destruction
    recover_account  -> ACTIVE again; old sessions/keys are NEVER revived

Three axes stay strictly separate (§1.5):

* ``deployment_tier`` (``local-single`` / ``lab`` / ``hosted``) — the
  authorisation/isolation boundary.  This module keys its **support scope** off
  this axis alone.
* ``execution_mode`` (``local`` / ``lsf`` / …) — where a job runs.  It is
  recorded, never consulted to grant identity; a remote execution mode never
  implies multi-tenant hosting.
* ``IdentityMode`` — ``key-only`` (``local-single``) vs ``account-session``
  (``lab`` / ``hosted`` when the tier's capabilities are present).  A
  ``local-single`` deployment is **explicitly key-only**: there is no server
  session, no invitation/enrollment and no first-admin bootstrap; the support
  scope says so rather than leaving it vague.

Hard rules encoded here (plan What / Must NOT):

* **No placeholder authentication.**  Passwords are verified with a real
  stdlib KDF (``hashlib.scrypt`` with an explicit PBKDF2 fallback), keyed by a
  per-account random salt; a stored digest is never the plaintext and a session
  token is likewise digest-only.  There is no "accept anything" branch.
* **No universal default password.**  ``bootstrap_first_admin`` and
  ``accept_invitation`` require a caller-supplied password that passes
  :func:`validate_password` (length + a documented weak-password deny-list).
  Bootstrapping is refused once a management account already exists, so it can
  never be replayed to re-escalate.
* **No surviving credential after disable/revoke.**  Disabling or revoking an
  account bumps the account ``session_epoch``, revokes every live session, and
  revokes every registered API-key reference.  Recovery bumps the epoch again;
  a pre-disable/pre-revoke session or key can never become valid again.
* **Immediately effective for new commands/downloads and EXISTING event
  streams.**  :meth:`IdentityLifecycle.authorize_entry` denies a disabled/revoked
  account for every entry class (command / download / event stream), and
  :meth:`IdentityLifecycle.disable_account` / ``revoke_account`` close every live
  ``AuthorizedEventStream`` for the principal when a stream registry is injected.
* **No auto-merge / auto-escalation.**  Accounts are looked up by ``login_id``
  only — never by e-mail — and roles are explicit facts supplied by an
  administrator; a shared e-mail, a same-named role or a deployment tier never
  merges principals or grants a role (mirrors todo 9 and
  ``contracts.access.tier_roles`` returning ``()``).
* **Tier without auth capability refuses enablement.**  A ``lab`` / ``hosted``
  tier missing the required authentication/isolation capabilities yields a
  support scope with ``enabled=False`` and :meth:`IdentityLifecycle.require_enabled`
  raises :class:`~acp_services.workspace.boundary.TierCapabilityError`; every
  lifecycle operation calls it first, so such a tier cannot be silently enabled.

OIDC decision (this iteration)
-----------------------------
No identity dependency is approved (``ACP_Services_Component_Manifest.md``:
``pwdlib`` / ``Authlib`` / ``PyCasbin`` / ``Keycloak`` are all ``candidate``;
``approved`` count is **0**).  Therefore OIDC is delivered **contract-level
only** and :data:`OIDC_SUPPORTED` is ``False``: :class:`IdentitySupportScope`
reports ``oidc_supported=False`` with an explicit limitation, so todo 52's Lab
doctor/capability report MUST NOT claim OIDC available.  The decision and the
exact support scope are recorded for todo 52 in
``docs/reference/next-change/ACP_Services_Identity_Support_Scope.md``.

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp``.  This module is stdlib plus
the Services-native ``contracts.access`` vocabulary, the tier-capability gate in
:mod:`acp_services.workspace.boundary` and the API-key primitive in
:mod:`acp_services.workspace.keys`.  It never imports the runtime HTTP layer;
the event-stream hook is a duck-typed port so the coupling stays one-directional.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.access import (
    DeploymentTier,
    ExecutionMode,
    Principal,
    PrincipalKind,
    Role,
)
from acp_services.workspace.boundary import (
    TierCapability,
    TierCapabilityError,
    tier_capability_verdict,
)
from acp_services.workspace.keys import ApiKeyManager, GeneratedKey

logger = logging.getLogger(__name__)

__all__ = [
    # support-scope vocabulary
    "IdentityMode",
    "IdentityEntry",
    "IdentityReason",
    "LIFECYCLE_HTTP_OK",
    "LIFECYCLE_HTTP_FORBIDDEN",
    "LIFECYCLE_HTTP_UNAUTHORIZED",
    "OIDC_SUPPORTED",
    "OIDC_UNAVAILABLE_REASON",
    "IdentitySupportScope",
    "identity_support_scope",
    "IdentityLifecyclePolicy",
    # records / results
    "AccountStatus",
    "AccountRecord",
    "InvitationRecord",
    "InvitationResult",
    "SessionRecord",
    "LoginResult",
    "SessionDecision",
    "CredentialRef",
    "LifecycleEvent",
    "LifecycleDecision",
    "StreamVerdict",
    "StreamRegistryPort",
    "close_principal_streams",
    # password policy
    "MIN_PASSWORD_LENGTH",
    "WEAK_PASSWORDS",
    "password_is_weak",
    "validate_password",
    # errors
    "IdentityLifecycleError",
    "IdentityLifecycleConflictError",
    "EnrollmentNotSupportedError",
    "SessionNotSupportedError",
    "BootstrapRefusedError",
    "DuplicateLoginError",
    "InvalidPasswordError",
    "UnknownAccountError",
    "InvitationError",
    "AccountStateError",
    # service
    "LifecycleStreamAuthorizer",
    "IdentityLifecycle",
]

# HTTP statuses the API layer maps a lifecycle verdict onto.
LIFECYCLE_HTTP_OK = 200
LIFECYCLE_HTTP_FORBIDDEN = 403
LIFECYCLE_HTTP_UNAUTHORIZED = 401

#: OIDC is contract-only this iteration — no approved dependency exists, so it is
#: NEVER advertised as available (todo 52 must report it NOT available).
OIDC_SUPPORTED = False
OIDC_UNAVAILABLE_REASON = (
    "OIDC/SSO is not implemented in this iteration: no identity dependency is "
    "approved (component manifest: pwdlib/Authlib/PyCasbin/Keycloak are all candidate, "
    "approved=0). Capability reports must report OIDC as NOT available."
)

# ---------------------------------------------------------------------------
# Password policy (no universal default password)
# ---------------------------------------------------------------------------

MIN_PASSWORD_LENGTH = 12

#: A small, documented deny-list of *universal* default credentials.  This is not
#: an exhaustive password meter; it exists so a bootstrap/invitation can never be
#: completed with an obvious shared default ("no universal default password").
WEAK_PASSWORDS = frozenset(
    {
        "admin",
        "administrator",
        "password",
        "password1",
        "passw0rd",
        "changeme",
        "change-me",
        "default",
        "letmein",
        "root",
        "toor",
        "qwerty",
        "123456",
        "12345678",
        "acpadmin",
        "acp-admin",
    }
)


def password_is_weak(password: Any) -> bool:
    """Whether *password* is a known universal/default credential."""
    if not isinstance(password, str):
        return True
    return password.strip().lower() in WEAK_PASSWORDS


def validate_password(password: Any, *, login_id: str | None = None) -> None:
    """Raise :class:`InvalidPasswordError` unless *password* is acceptable.

    Enforces a minimum length, rejects the universal default deny-list, and
    refuses a password equal to the login handle.  This is the executable form of
    "no universal default password": there is no code path that supplies one.
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPasswordError(
            f"password must be a string of at least {MIN_PASSWORD_LENGTH} characters"
        )
    if password_is_weak(password):
        raise InvalidPasswordError("password is a known universal/default credential")
    if login_id is not None and password.strip().lower() == str(login_id).strip().lower():
        raise InvalidPasswordError("password must not equal the login id")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IdentityLifecycleError(Exception):
    """Base class for identity-lifecycle contract violations."""


class IdentityLifecycleConflictError(IdentityLifecycleError):
    """A state transition conflicts with the current account state."""


class EnrollmentNotSupportedError(IdentityLifecycleError):
    """Invitation/enrollment is not offered by the active tier (key-only)."""


class SessionNotSupportedError(IdentityLifecycleError):
    """The active tier is key-only; there is no interactive session."""


class BootstrapRefusedError(IdentityLifecycleError):
    """First-admin bootstrap is refused (already bootstrapped / not applicable)."""


class DuplicateLoginError(IdentityLifecycleConflictError):
    """The login id is already taken — accounts are never merged."""


class InvalidPasswordError(IdentityLifecycleError):
    """The supplied password fails the password policy."""


class UnknownAccountError(IdentityLifecycleError):
    """No account matched the requested id/principal/login."""


class InvitationError(IdentityLifecycleError):
    """An invitation token is unknown / expired / already used / revoked."""


class AccountStateError(IdentityLifecycleConflictError):
    """The requested transition is illegal for the account's current state."""


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class IdentityMode(str):
    """How a deployment authenticates its callers."""

    KEY_ONLY = "key-only"
    ACCOUNT_SESSION = "account-session"
    ALL = frozenset({KEY_ONLY, ACCOUNT_SESSION})


class AccountStatus(str):
    """Account lifecycle states."""

    INVITED = "invited"
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"
    ALL = frozenset({INVITED, ACTIVE, DISABLED, REVOKED})


class IdentityEntry(str):
    """The resource entries a disabled/revoked account is denied at."""

    COMMAND = "command"
    DOWNLOAD = "download"
    EVENT_STREAM = "event_stream"
    LOGIN = "login"
    ALL = frozenset({COMMAND, DOWNLOAD, EVENT_STREAM, LOGIN})


class IdentityReason(str):
    """Decision/result reason vocabulary (values mirror the Access ReasonCode)."""

    GRANTED = "granted"
    NO_GRANT = "no_grant"
    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_REVOKED = "account_revoked"
    INVITATION_PENDING = "invitation_pending"
    INVALID_CREDENTIALS = "invalid_credentials"
    SESSIONS_NOT_SUPPORTED = "sessions_not_supported"
    ENROLLMENT_NOT_SUPPORTED = "enrollment_not_supported"
    BOOTSTRAP_NOT_SUPPORTED = "bootstrap_not_supported"
    BOOTSTRAP_ALREADY_DONE = "bootstrap_already_done"
    TIER_CAPABILITY_MISSING = "tier_capability_missing"
    UNKNOWN_ACCOUNT = "unknown_account"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    SESSION_EPOCH_STALE = "session_epoch_stale"
    INVITATION_EXPIRED = "invitation_expired"
    INVITATION_USED = "invitation_used"
    INVITATION_REVOKED = "invitation_revoked"
    DUPLICATE_LOGIN = "duplicate_login"
    INVALID_PASSWORD = "invalid_password"
    UNKNOWN_ENTRY = "unknown_entry"
    ACCOUNT_NOT_ACTIVE = "account_not_active"
    ALL = frozenset(
        {
            GRANTED,
            NO_GRANT,
            ACCOUNT_DISABLED,
            ACCOUNT_REVOKED,
            INVITATION_PENDING,
            INVALID_CREDENTIALS,
            SESSIONS_NOT_SUPPORTED,
            ENROLLMENT_NOT_SUPPORTED,
            BOOTSTRAP_NOT_SUPPORTED,
            BOOTSTRAP_ALREADY_DONE,
            TIER_CAPABILITY_MISSING,
            UNKNOWN_ACCOUNT,
            SESSION_EXPIRED,
            SESSION_REVOKED,
            SESSION_EPOCH_STALE,
            INVITATION_EXPIRED,
            INVITATION_USED,
            INVITATION_REVOKED,
            DUPLICATE_LOGIN,
            INVALID_PASSWORD,
            UNKNOWN_ENTRY,
            ACCOUNT_NOT_ACTIVE,
        }
    )


# ---------------------------------------------------------------------------
# Support scope (the tier capability surface todo 52 consumes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentitySupportScope:
    """The identity capabilities a deployment tier actually supports.

    ``deployment_tier`` and ``execution_mode`` are reported as two independent
    axes.  ``enabled`` is False when the tier is missing required capabilities;
    ``oidc_supported`` is **always** False in this iteration, so a capability
    report can never claim OIDC available.
    """

    deployment_tier: str
    execution_mode: str
    enabled: bool
    mode: str
    key_only: bool
    sessions_supported: bool
    enrollment_supported: bool
    invitation_supported: bool
    bootstrap_supported: bool
    oidc_supported: bool
    limitations: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    error_code: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_tier": self.deployment_tier,
            "execution_mode": self.execution_mode,
            "enabled": self.enabled,
            "mode": self.mode,
            "key_only": self.key_only,
            "sessions_supported": self.sessions_supported,
            "enrollment_supported": self.enrollment_supported,
            "invitation_supported": self.invitation_supported,
            "bootstrap_supported": self.bootstrap_supported,
            "oidc_supported": self.oidc_supported,
            "limitations": list(self.limitations),
            "missing_capabilities": list(self.missing_capabilities),
            "error_code": self.error_code,
            "detail": self.detail,
        }


def identity_support_scope(
    deployment_tier: str,
    capabilities: object = (),
    *,
    execution_mode: str = ExecutionMode.LOCAL,
) -> IdentitySupportScope:
    """Compute the identity support scope for a tier + its available capabilities.

    ``local-single`` is key-only (no session/enrollment/bootstrap).  ``lab`` and
    ``hosted`` are account-session tiers **only** when both the authentication
    and isolation capabilities are present; otherwise ``enabled`` is False and
    the missing capabilities are named.  ``execution_mode`` is recorded, never
    used to widen the scope.
    """
    tier = str(deployment_tier)
    mode_axis = str(execution_mode)
    verdict = tier_capability_verdict(tier, capabilities)
    enabled = verdict.allowed
    account_tier = tier in (DeploymentTier.LAB, DeploymentTier.HOSTED)
    mode = IdentityMode.ACCOUNT_SESSION if account_tier else IdentityMode.KEY_ONLY

    limitations: list[str] = []
    if mode == IdentityMode.KEY_ONLY:
        limitations.append(
            "Key-only: authentication is an API key; there is no interactive login "
            "or server-side session."
        )
        limitations.append(
            "Account enrollment/invitation and first-admin bootstrap are not offered; "
            "keys are provisioned by the operator."
        )
    limitations.append(OIDC_UNAVAILABLE_REASON)

    error_code: str | None = None
    if not enabled:
        error_code = verdict.error_code
        limitations.append(
            "Tier is not enabled: missing required capabilities "
            f"{list(verdict.missing_capabilities)}."
        )

    return IdentitySupportScope(
        deployment_tier=tier,
        execution_mode=mode_axis,
        enabled=enabled,
        mode=mode,
        key_only=(mode == IdentityMode.KEY_ONLY),
        sessions_supported=enabled and mode == IdentityMode.ACCOUNT_SESSION,
        enrollment_supported=enabled and mode == IdentityMode.ACCOUNT_SESSION,
        invitation_supported=enabled and mode == IdentityMode.ACCOUNT_SESSION,
        bootstrap_supported=enabled and mode == IdentityMode.ACCOUNT_SESSION,
        oidc_supported=OIDC_SUPPORTED,
        limitations=tuple(limitations),
        missing_capabilities=tuple(verdict.missing_capabilities),
        error_code=error_code,
        detail=verdict.detail,
    )


@dataclass(frozen=True)
class IdentityLifecyclePolicy:
    """The configured identity policy for one deployment.

    The deployment tier and its available capabilities decide the support scope;
    ``execution_mode`` is a separate recorded axis that never grants identity.
    """

    deployment_tier: str = DeploymentTier.LOCAL_SINGLE
    capabilities: tuple[str, ...] = ()
    execution_mode: str = ExecutionMode.LOCAL

    def support_scope(self) -> IdentitySupportScope:
        return identity_support_scope(
            self.deployment_tier,
            self.capabilities,
            execution_mode=self.execution_mode,
        )

    @property
    def enabled(self) -> bool:
        return self.support_scope().enabled

    @property
    def mode(self) -> str:
        return self.support_scope().mode

    @property
    def sessions_supported(self) -> bool:
        return self.support_scope().sessions_supported

    @property
    def enrollment_supported(self) -> bool:
        return self.support_scope().enrollment_supported

    @property
    def bootstrap_supported(self) -> bool:
        return self.support_scope().bootstrap_supported

    def require_enabled(self) -> IdentitySupportScope:
        """Raise :class:`TierCapabilityError` unless the tier may be enabled."""
        scope = self.support_scope()
        if not scope.enabled:
            raise TierCapabilityError(
                scope.deployment_tier,
                scope.missing_capabilities,
                code=scope.error_code or "tier_capability_missing",
                detail=scope.detail,
            )
        return scope


def default_lab_capabilities() -> tuple[str, ...]:
    """The capabilities a Lab/Hosted tier needs to enable identity."""
    return (TierCapability.AUTHENTICATION, TierCapability.ISOLATION)


# ---------------------------------------------------------------------------
# Records / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountRecord:
    """A stored account projection (never carries a digest/salt)."""

    account_id: str
    principal_id: str
    login_id: str
    status: str
    roles: tuple[str, ...]
    session_epoch: int
    created_at: str
    updated_at: str
    email: str | None = None
    display_name: str | None = None
    disabled_at: str | None = None
    revoked_at: str | None = None
    recovered_at: str | None = None
    invited_by: str | None = None
    bootstrap: bool = False
    has_password: bool = False

    @property
    def active(self) -> bool:
        return self.status == AccountStatus.ACTIVE

    @property
    def management(self) -> bool:
        return any(role in Role.MANAGEMENT for role in self.roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "principal_id": self.principal_id,
            "login_id": self.login_id,
            "status": self.status,
            "roles": list(self.roles),
            "session_epoch": self.session_epoch,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "email": self.email,
            "display_name": self.display_name,
            "disabled_at": self.disabled_at,
            "revoked_at": self.revoked_at,
            "recovered_at": self.recovered_at,
            "invited_by": self.invited_by,
            "bootstrap": self.bootstrap,
            "has_password": self.has_password,
        }


@dataclass(frozen=True)
class InvitationRecord:
    """Stored invitation metadata (no token digest/salt)."""

    invitation_id: str
    account_id: str
    created_at: str
    expires_at: str | None = None
    accepted_at: str | None = None
    revoked_at: str | None = None

    @property
    def accepted(self) -> bool:
        return self.accepted_at is not None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "invitation_id": self.invitation_id,
            "account_id": self.account_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "accepted_at": self.accepted_at,
            "revoked_at": self.revoked_at,
        }


@dataclass(frozen=True)
class InvitationResult:
    """A freshly minted invitation; ``token`` is shown exactly once."""

    invitation_id: str
    account_id: str
    login_id: str
    token: str = field(repr=False)
    expires_at: str | None = None
    created_at: str = ""

    def to_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "invitation_id": self.invitation_id,
            "account_id": self.account_id,
            "login_id": self.login_id,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }
        if include_token:
            data["token"] = self.token
        return data


@dataclass(frozen=True)
class SessionRecord:
    """Stored session metadata (no token digest/salt)."""

    session_id: str
    account_id: str
    principal_id: str
    session_epoch: int
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_seen_at: str | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "principal_id": self.principal_id,
            "session_epoch": self.session_epoch,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(frozen=True)
class LoginResult:
    """Outcome of :meth:`IdentityLifecycle.login`.

    ``token`` is returned exactly once (session branch); it is never stored.  A
    key-only deployment returns ``allowed=False`` / ``sessions_not_supported``
    rather than fabricating a placeholder session.
    """

    allowed: bool
    reason: str
    account_id: str | None = None
    session: SessionRecord | None = None
    token: str | None = field(default=None, repr=False)
    expires_at: str | None = None
    detail: str = ""

    def to_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "allowed": self.allowed,
            "reason": self.reason,
            "account_id": self.account_id,
            "session": None if self.session is None else self.session.to_dict(),
            "expires_at": self.expires_at,
            "detail": self.detail,
        }
        if include_token:
            data["token"] = self.token
        return data


@dataclass(frozen=True)
class SessionDecision:
    """Outcome of :meth:`IdentityLifecycle.verify_session`."""

    allowed: bool
    reason: str
    session_id: str | None = None
    account_id: str | None = None
    principal_id: str | None = None
    expires_at: str | None = None
    detail: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "session_id": self.session_id,
            "account_id": self.account_id,
            "principal_id": self.principal_id,
            "expires_at": self.expires_at,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LifecycleDecision:
    """An entry authorization verdict for a disabled/revoked account gate."""

    allowed: bool
    entry: str
    reason_code: str
    principal_id: str
    account_id: str | None = None
    status: str | None = None
    http_status: int = LIFECYCLE_HTTP_OK
    detail: str = ""

    def error_detail(self) -> dict[str, str]:
        return {"code": self.reason_code, "message": self.detail}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "entry": self.entry,
            "reason_code": self.reason_code,
            "principal_id": self.principal_id,
            "account_id": self.account_id,
            "status": self.status,
            "http_status": self.http_status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CredentialRef:
    """Link between an account and an issued API key id."""

    ref_id: str
    account_id: str
    kind: str
    key_id: str
    created_at: str
    revoked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "account_id": self.account_id,
            "kind": self.kind,
            "key_id": self.key_id,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }


@dataclass(frozen=True)
class LifecycleEvent:
    """One persisted audit fact for an identity transition."""

    event_id: str
    action: str
    actor: str
    reason: str
    created_at: str
    account_id: str | None = None
    principal_id: str | None = None
    before_status: str | None = None
    after_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "action": self.action,
            "actor": self.actor,
            "reason": self.reason,
            "created_at": self.created_at,
            "account_id": self.account_id,
            "principal_id": self.principal_id,
            "before_status": self.before_status,
            "after_status": self.after_status,
        }


# ---------------------------------------------------------------------------
# Stream revocation port (duck-typed; no runtime HTTP import)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamVerdict:
    """Verdict shape the runtime ``AuthorizedEventStream`` consumes.

    Exposes ``allowed`` / ``denied`` / ``principal_id`` — exactly the attributes
    the runtime reads — so this module never imports the runtime layer.
    """

    allowed: bool
    principal_id: str = ""
    reason_code: str = ""
    detail: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


@runtime_checkable
class StreamRegistryPort(Protocol):
    """The minimal registry surface needed to terminate live streams."""

    @property
    def keys(self) -> Sequence[Any]:
        """Live stream keys (objects carrying ``principal_id`` + ``scope``)."""
        ...

    def close(self, principal_id: str, scope: Any, reason: str = "closed") -> None:
        """Close the stream for ``(principal_id, scope)``."""
        ...


def close_principal_streams(
    registry: StreamRegistryPort | None,
    principal_id: str,
    *,
    reason: str = "permission_changed",
) -> tuple[str, ...]:
    """Close every live stream belonging to *principal_id*; return closed scopes.

    A ``None`` registry (no SSE layer wired) is a no-op.  The registry is
    duck-typed so this module stays free of a runtime import.
    """
    if registry is None:
        return ()
    closed: list[str] = []
    for key in tuple(registry.keys):
        if str(getattr(key, "principal_id", "")) != str(principal_id):
            continue
        scope = getattr(key, "scope", None)
        registry.close(str(principal_id), scope, reason)
        labels = getattr(scope, "value", scope)
        closed.append(str(labels))
    return tuple(closed)


class LifecycleStreamAuthorizer:
    """A ``StreamAuthorizer`` that denies disabled/revoked/unauthenticated principals.

    Implements the runtime's ``authorize_stream(*, principal_id, scope, job_id)``
    shape.  ``allow_unknown`` controls whether a principal with no account is
    allowed (``True`` preserves the trusted ``local-single`` key-only behaviour;
    account tiers pass ``False`` so an unknown principal is denied).
    """

    def __init__(self, lifecycle: IdentityLifecycle, *, allow_unknown: bool = True) -> None:
        self._lifecycle = lifecycle
        self._allow_unknown = bool(allow_unknown)

    def authorize_stream(
        self,
        *,
        principal_id: str,
        scope: Any = None,
        job_id: str = "",
    ) -> StreamVerdict:
        account = self._lifecycle.get_account_by_principal(str(principal_id))
        if account is None:
            if self._allow_unknown:
                return StreamVerdict(
                    allowed=True,
                    principal_id=str(principal_id),
                    reason_code=IdentityReason.GRANTED,
                    detail="no account on this principal; key-only trusted context",
                )
            return StreamVerdict(
                allowed=False,
                principal_id=str(principal_id),
                reason_code=IdentityReason.UNKNOWN_ACCOUNT,
                detail=f"no account for principal {principal_id!r}",
            )
        decision = self._lifecycle.authorize_entry(str(principal_id), IdentityEntry.EVENT_STREAM)
        return StreamVerdict(
            allowed=decision.allowed,
            principal_id=decision.principal_id,
            reason_code=decision.reason_code,
            detail=decision.detail,
        )


# ---------------------------------------------------------------------------
# KDF / token helpers
# ---------------------------------------------------------------------------

KDF_SCRYPT = "scrypt"
KDF_PBKDF2 = "pbkdf2_hmac_sha256"

_DEFAULT_SCRYPT_N = 1 << 15
_DEFAULT_SCRYPT_R = 8
_DEFAULT_SCRYPT_P = 1
_DEFAULT_PBKDF2_ITERATIONS = 600_000
_DEFAULT_TOKEN_ITERATIONS = 100_000

_SCRYPT_AVAILABLE = hasattr(hashlib, "scrypt")

_KEY_ID_BYTES = 8
_SECRET_BYTES = 32
_SALT_BYTES = 16


def _default_password_params() -> dict[str, Any]:
    if _SCRYPT_AVAILABLE:
        return {
            "kdf": KDF_SCRYPT,
            "n": _DEFAULT_SCRYPT_N,
            "r": _DEFAULT_SCRYPT_R,
            "p": _DEFAULT_SCRYPT_P,
            "dklen": 32,
        }
    return {"kdf": KDF_PBKDF2, "iterations": _DEFAULT_PBKDF2_ITERATIONS, "dklen": 32}


def _derive(secret: str, salt: bytes, params: Mapping[str, Any]) -> bytes:
    """Derive a digest for *secret* using the stored KDF parameters."""
    kdf = str(params["kdf"])
    salt_bytes = bytes(salt)
    if kdf == KDF_SCRYPT:
        n = int(params["n"])
        r = int(params["r"])
        p = int(params["p"])
        dklen = int(params.get("dklen", 32))
        return hashlib.scrypt(
            secret.encode("utf-8"),
            salt=salt_bytes,
            n=n,
            r=r,
            p=p,
            dklen=dklen,
            maxmem=128 * n * r * 2,
        )
    if kdf == KDF_PBKDF2:
        return hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode("utf-8"),
            salt_bytes,
            int(params["iterations"]),
            dklen=int(params.get("dklen", 32)),
        )
    raise IdentityLifecycleError(f"unknown password KDF {kdf!r}")


def _token_params(iterations: int) -> dict[str, Any]:
    return {"kdf": KDF_PBKDF2, "iterations": int(iterations), "dklen": 32}


def _new_secret() -> str:
    return secrets.token_urlsafe(_SECRET_BYTES)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(_KEY_ID_BYTES)}"


def _split_token(token: Any, prefix: str) -> tuple[str, str] | None:
    if not isinstance(token, str) or not token.startswith(prefix):
        return None
    token_id, separator, secret = token[len(prefix) :].partition(".")
    if not separator or not token_id or not secret:
        return None
    return token_id, secret


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _is_past(value: str | None, now: datetime) -> bool:
    parsed = _parse_iso(value)
    return parsed is not None and now >= parsed


def _normalize_roles(roles: Iterable[str] | str | None) -> tuple[str, ...]:
    if roles is None:
        return ()
    items = [roles] if isinstance(roles, str) else list(roles)
    ordered: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if text not in Role.ALL:
            raise IdentityLifecycleError(f"unknown role {text!r} (roles are explicit facts)")
        if text not in ordered:
            ordered.append(text)
    return tuple(ordered)


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_CREATE_ACCOUNTS_SQL = """
CREATE TABLE IF NOT EXISTS identity_accounts (
    account_id        TEXT PRIMARY KEY,
    principal_id      TEXT NOT NULL UNIQUE,
    login_id          TEXT NOT NULL UNIQUE,
    email             TEXT,
    display_name      TEXT,
    status            TEXT NOT NULL,
    roles             TEXT NOT NULL,
    password_kdf      TEXT,
    password_params   TEXT,
    password_salt     BLOB,
    password_digest   BLOB,
    session_epoch     INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    disabled_at       TEXT,
    revoked_at        TEXT,
    recovered_at      TEXT,
    invited_by        TEXT,
    bootstrap         INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_INVITATIONS_SQL = """
CREATE TABLE IF NOT EXISTS identity_invitations (
    invitation_id   TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    token_digest    BLOB NOT NULL,
    token_salt      BLOB NOT NULL,
    kdf             TEXT NOT NULL,
    kdf_params      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    accepted_at     TEXT,
    revoked_at      TEXT
)
"""

_CREATE_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS identity_sessions (
    session_id     TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    principal_id   TEXT NOT NULL,
    token_digest   BLOB NOT NULL,
    token_salt     BLOB NOT NULL,
    kdf            TEXT NOT NULL,
    kdf_params     TEXT NOT NULL,
    session_epoch  INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT,
    revoked_at     TEXT,
    last_seen_at   TEXT
)
"""

_CREATE_REFS_SQL = """
CREATE TABLE IF NOT EXISTS identity_credential_refs (
    ref_id      TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    key_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    revoked_at  TEXT
)
"""

_CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS identity_lifecycle_events (
    event_id       TEXT PRIMARY KEY,
    account_id     TEXT,
    principal_id   TEXT,
    action         TEXT NOT NULL,
    actor          TEXT NOT NULL,
    reason         TEXT NOT NULL,
    before_status  TEXT,
    after_status   TEXT,
    created_at     TEXT NOT NULL
)
"""


def _row_to_account(row: sqlite3.Row) -> AccountRecord:
    try:
        roles = tuple(json.loads(row["roles"]) or [])
    except (json.JSONDecodeError, TypeError):  # pragma: no cover - defensive
        roles = ()
    return AccountRecord(
        account_id=row["account_id"],
        principal_id=row["principal_id"],
        login_id=row["login_id"],
        status=row["status"],
        roles=roles,
        session_epoch=int(row["session_epoch"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        email=row["email"],
        display_name=row["display_name"],
        disabled_at=row["disabled_at"],
        revoked_at=row["revoked_at"],
        recovered_at=row["recovered_at"],
        invited_by=row["invited_by"],
        bootstrap=bool(row["bootstrap"]),
        has_password=row["password_digest"] is not None,
    )


def _row_to_invitation(row: sqlite3.Row) -> InvitationRecord:
    return InvitationRecord(
        invitation_id=row["invitation_id"],
        account_id=row["account_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        accepted_at=row["accepted_at"],
        revoked_at=row["revoked_at"],
    )


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        account_id=row["account_id"],
        principal_id=row["principal_id"],
        session_epoch=int(row["session_epoch"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_seen_at=row["last_seen_at"],
    )


def _row_to_ref(row: sqlite3.Row) -> CredentialRef:
    return CredentialRef(
        ref_id=row["ref_id"],
        account_id=row["account_id"],
        kind=row["kind"],
        key_id=row["key_id"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )


def _row_to_event(row: sqlite3.Row) -> LifecycleEvent:
    return LifecycleEvent(
        event_id=row["event_id"],
        action=row["action"],
        actor=row["actor"],
        reason=row["reason"],
        created_at=row["created_at"],
        account_id=row["account_id"],
        principal_id=row["principal_id"],
        before_status=row["before_status"],
        after_status=row["after_status"],
    )


# ---------------------------------------------------------------------------
# The lifecycle service
# ---------------------------------------------------------------------------


class IdentityLifecycle:
    """SQLite-backed identity lifecycle state machine (stdlib-only core).

    Args:
        db_path: Explicit SQLite path; all tables are created idempotently.
        policy: The configured :class:`IdentityLifecyclePolicy`.  Defaults to the
            trusted ``local-single`` key-only policy.
        key_manager: Optional :class:`~acp_services.workspace.keys.ApiKeyManager`.
            When supplied, :meth:`issue_key` mints real keys and disable/revoke
            revoke them.
        stream_registry: Optional registry providing ``keys`` + ``close``; when
            supplied, disable/revoke close every live event stream for the
            principal (immediate effect on existing streams).
        clock: Optional ``() -> datetime`` override.
        password_params: Optional KDF parameter override (tests use a cheap KDF).
        token_iterations: PBKDF2 iterations for session/invitation token digests.
        session_ttl / invitation_ttl: Credential lifetimes.
        allow_unknown_key_only: Whether an unknown principal is allowed in a
            key-only tier (default True preserves the trusted local behaviour).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        policy: IdentityLifecyclePolicy | None = None,
        key_manager: ApiKeyManager | None = None,
        stream_registry: StreamRegistryPort | None = None,
        clock: Callable[[], datetime] | None = None,
        password_params: Mapping[str, Any] | None = None,
        token_iterations: int = _DEFAULT_TOKEN_ITERATIONS,
        session_ttl: timedelta = timedelta(hours=12),
        invitation_ttl: timedelta = timedelta(days=7),
        allow_unknown_key_only: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._policy = policy or IdentityLifecyclePolicy()
        self._key_manager = key_manager
        self._stream_registry = stream_registry
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._password_params = dict(password_params or _default_password_params())
        self._token_iterations = int(token_iterations)
        self._session_ttl = session_ttl
        self._invitation_ttl = invitation_ttl
        self._allow_unknown_key_only = bool(allow_unknown_key_only)
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- schema / connection ------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                for ddl in (
                    _CREATE_ACCOUNTS_SQL,
                    _CREATE_INVITATIONS_SQL,
                    _CREATE_SESSIONS_SQL,
                    _CREATE_REFS_SQL,
                    _CREATE_EVENTS_SQL,
                ):
                    conn.execute(ddl)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self, now: datetime | None) -> datetime:
        return _coerce_now(now if now is not None else self._clock())

    @property
    def policy(self) -> IdentityLifecyclePolicy:
        return self._policy

    def support_scope(self) -> IdentitySupportScope:
        """Return the tier capability/support scope (for doctor / todo 52)."""
        return self._policy.support_scope()

    def verify_enablement(self) -> IdentitySupportScope:
        """Non-raising enablement check; the report names any missing capability."""
        return self._policy.support_scope()

    def require_enabled(self) -> IdentitySupportScope:
        """Raise unless the tier may be enabled (auth capability present)."""
        return self._policy.require_enabled()

    # -- audit --------------------------------------------------------------

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        actor: str,
        reason: str,
        created_at: str,
        account_id: str | None = None,
        principal_id: str | None = None,
        before_status: str | None = None,
        after_status: str | None = None,
    ) -> LifecycleEvent:
        event = LifecycleEvent(
            event_id=_new_id("idle"),
            action=str(action),
            actor=str(actor),
            reason=str(reason),
            created_at=created_at,
            account_id=account_id,
            principal_id=principal_id,
            before_status=before_status,
            after_status=after_status,
        )
        conn.execute(
            """INSERT INTO identity_lifecycle_events
                   (event_id, account_id, principal_id, action, actor, reason,
                    before_status, after_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.account_id,
                event.principal_id,
                event.action,
                event.actor,
                event.reason,
                event.before_status,
                event.after_status,
                event.created_at,
            ),
        )
        return event

    # -- account creation / invitation -------------------------------------

    def invite_account(
        self,
        *,
        principal_id: str,
        login_id: str,
        roles: Iterable[str] | str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        actor: str = "admin",
        now: datetime | None = None,
    ) -> InvitationResult:
        """Create an INVITED account + one-time invitation token (Lab tiers).

        Refuses when the tier is key-only (``enrollment_not_supported``) or when
        the same ``login_id`` already exists (``duplicate_login``).  Accounts are
        keyed by ``login_id`` only — a shared e-mail never merges principals.
        """
        self.require_enabled()
        if not self._policy.enrollment_supported:
            raise EnrollmentNotSupportedError(
                "account enrollment/invitation is not offered by this tier "
                f"({self._policy.mode}); key-only deployments provision keys directly"
            )
        principal_id = str(principal_id)
        login_id = str(login_id).strip()
        if not principal_id or not login_id:
            raise ValueError("principal_id and login_id are required")
        role_tuple = _normalize_roles(roles)
        reference = self._now(now)
        timestamp = reference.isoformat()

        with self._lock:
            conn = self._connect()
            try:
                if self._find_by_login(conn, login_id) is not None:
                    self._audit(
                        conn,
                        action="invite_rejected",
                        actor=actor,
                        reason=IdentityReason.DUPLICATE_LOGIN,
                        created_at=timestamp,
                        principal_id=principal_id,
                    )
                    conn.commit()
                    raise DuplicateLoginError(f"login id {login_id!r} is already taken")

                account_id = _new_id("acct")
                conn.execute(
                    """INSERT INTO identity_accounts
                           (account_id, principal_id, login_id, email, display_name,
                            status, roles, session_epoch, created_at, updated_at,
                            invited_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (
                        account_id,
                        principal_id,
                        login_id,
                        email,
                        display_name,
                        AccountStatus.INVITED,
                        json.dumps(list(role_tuple), ensure_ascii=False),
                        timestamp,
                        timestamp,
                        actor,
                    ),
                )
                invitation = self._mint_invitation(conn, account_id=account_id, reference=reference)
                self._audit(
                    conn,
                    action="invite",
                    actor=actor,
                    reason=IdentityReason.GRANTED,
                    created_at=timestamp,
                    account_id=account_id,
                    principal_id=principal_id,
                    before_status=None,
                    after_status=AccountStatus.INVITED,
                )
                conn.commit()
            finally:
                conn.close()

        logger.info("identity invitation issued account=%s login=%s", account_id, login_id)
        return invitation

    def _mint_invitation(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        reference: datetime,
    ) -> InvitationResult:
        invitation_id = _new_id("inv")
        secret = _new_secret()
        salt = secrets.token_bytes(_SALT_BYTES)
        params = _token_params(self._token_iterations)
        digest = _derive(secret, salt, params)
        created_at = reference.isoformat()
        expires_at = (reference + self._invitation_ttl).isoformat()
        conn.execute(
            """INSERT INTO identity_invitations
                   (invitation_id, account_id, token_digest, token_salt, kdf,
                    kdf_params, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invitation_id,
                account_id,
                sqlite3.Binary(digest),
                sqlite3.Binary(salt),
                params["kdf"],
                json.dumps(params, ensure_ascii=False),
                created_at,
                expires_at,
            ),
        )
        row = conn.execute(
            "SELECT login_id FROM identity_accounts WHERE account_id=?", (account_id,)
        ).fetchone()
        login_id = str(row["login_id"]) if row is not None else ""
        return InvitationResult(
            invitation_id=invitation_id,
            account_id=account_id,
            login_id=login_id,
            token=f"acpi_{invitation_id}.{secret}",
            expires_at=expires_at,
            created_at=created_at,
        )

    def accept_invitation(
        self,
        token: Any,
        *,
        password: Any,
        actor: str = "invitee",
        now: datetime | None = None,
    ) -> AccountRecord:
        """Establish the account: set the caller-supplied credential and activate.

        Raises :class:`InvitationError` for an unknown/expired/used/revoked token
        and :class:`InvalidPasswordError` when the password fails policy.  There
        is **no** default password branch.
        """
        self.require_enabled()
        parsed = _split_token(token, "acpi_")
        reference = self._now(now)
        timestamp = reference.isoformat()

        with self._lock:
            conn = self._connect()
            try:
                if parsed is None:
                    raise InvitationError("malformed invitation token")
                invitation_id, secret = parsed
                row = conn.execute(
                    "SELECT * FROM identity_invitations WHERE invitation_id=?",
                    (invitation_id,),
                ).fetchone()
                if row is None:
                    raise InvitationError("unknown invitation token")
                params = json.loads(row["kdf_params"])
                candidate = _derive(secret, bytes(row["token_salt"]), params)
                if not hmac.compare_digest(candidate, bytes(row["token_digest"])):
                    raise InvitationError("invitation token does not match")
                if row["accepted_at"] is not None:
                    raise InvitationError("invitation token has already been used")
                if row["revoked_at"] is not None:
                    raise InvitationError("invitation token has been revoked")
                if _is_past(row["expires_at"], reference):
                    raise InvitationError("invitation token has expired")

                account_row = self._find_by_id(conn, str(row["account_id"]))
                if account_row is None:  # pragma: no cover - FK by construction
                    raise InvitationError("invitation target account is missing")
                account = _row_to_account(account_row)
                if account.status != AccountStatus.INVITED:
                    raise AccountStateError(
                        f"account {account.account_id!r} is {account.status!r}, not invited"
                    )

                validate_password(password, login_id=account.login_id)
                salt = secrets.token_bytes(_SALT_BYTES)
                digest = _derive(str(password), salt, self._password_params)
                conn.execute(
                    """UPDATE identity_accounts
                       SET status=?, password_kdf=?, password_params=?, password_salt=?,
                           password_digest=?, updated_at=?
                       WHERE account_id=?""",
                    (
                        AccountStatus.ACTIVE,
                        self._password_params["kdf"],
                        json.dumps(self._password_params, ensure_ascii=False),
                        sqlite3.Binary(salt),
                        sqlite3.Binary(digest),
                        timestamp,
                        account.account_id,
                    ),
                )
                conn.execute(
                    "UPDATE identity_invitations SET accepted_at=? WHERE invitation_id=?",
                    (timestamp, invitation_id),
                )
                self._audit(
                    conn,
                    action="accept_invitation",
                    actor=actor,
                    reason=IdentityReason.GRANTED,
                    created_at=timestamp,
                    account_id=account.account_id,
                    principal_id=account.principal_id,
                    before_status=AccountStatus.INVITED,
                    after_status=AccountStatus.ACTIVE,
                )
                conn.commit()
                return self._get_account(conn, account.account_id)
            finally:
                conn.close()

    def bootstrap_first_admin(
        self,
        *,
        principal_id: str,
        login_id: str,
        password: Any,
        email: str | None = None,
        display_name: str | None = None,
        actor: str = "bootstrap",
        now: datetime | None = None,
    ) -> AccountRecord:
        """Create the first management account — with **no** default password.

        Refuses when a management account already exists (``bootstrap_already_done``)
        or the tier is key-only / not enabled.  The password is caller-supplied and
        policy-validated; there is no universal default.
        """
        self.require_enabled()
        if not self._policy.bootstrap_supported:
            raise BootstrapRefusedError(
                "first-admin bootstrap is only offered by an account/session tier "
                f"({self._policy.mode})"
            )
        principal_id = str(principal_id)
        login_id = str(login_id).strip()
        if not principal_id or not login_id:
            raise ValueError("principal_id and login_id are required")

        reference = self._now(now)
        timestamp = reference.isoformat()
        with self._lock:
            conn = self._connect()
            try:
                if self._management_account_exists(conn):
                    self._audit(
                        conn,
                        action="bootstrap_rejected",
                        actor=actor,
                        reason=IdentityReason.BOOTSTRAP_ALREADY_DONE,
                        created_at=timestamp,
                        principal_id=principal_id,
                    )
                    conn.commit()
                    raise BootstrapRefusedError("a management account already exists")
                if self._find_by_login(conn, login_id) is not None:
                    raise DuplicateLoginError(f"login id {login_id!r} is already taken")

                validate_password(password, login_id=login_id)
                account_id = _new_id("acct")
                salt = secrets.token_bytes(_SALT_BYTES)
                digest = _derive(str(password), salt, self._password_params)
                conn.execute(
                    """INSERT INTO identity_accounts
                           (account_id, principal_id, login_id, email, display_name,
                            status, roles, password_kdf, password_params, password_salt,
                            password_digest, session_epoch, created_at, updated_at,
                            invited_by, bootstrap)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)""",
                    (
                        account_id,
                        principal_id,
                        login_id,
                        email,
                        display_name,
                        AccountStatus.ACTIVE,
                        json.dumps([Role.ADMIN], ensure_ascii=False),
                        self._password_params["kdf"],
                        json.dumps(self._password_params, ensure_ascii=False),
                        sqlite3.Binary(salt),
                        sqlite3.Binary(digest),
                        timestamp,
                        timestamp,
                        actor,
                    ),
                )
                self._audit(
                    conn,
                    action="bootstrap",
                    actor=actor,
                    reason=IdentityReason.GRANTED,
                    created_at=timestamp,
                    account_id=account_id,
                    principal_id=principal_id,
                    before_status=None,
                    after_status=AccountStatus.ACTIVE,
                )
                conn.commit()
                return self._get_account(conn, account_id)
            finally:
                conn.close()

    # -- authentication / sessions -----------------------------------------

    def authenticate(
        self,
        *,
        login_id: str,
        password: Any,
        now: datetime | None = None,
    ) -> AccountRecord | None:
        """Verify credentials; return the ACTIVE account or ``None``.

        A wrong password, an unknown login id and an inactive account all return
        ``None`` so a caller cannot use this as a state oracle.  Use
        :meth:`login` when a typed reason is needed.
        """
        result = self._authenticate(login_id=login_id, password=password, now=now)
        return result[0]

    def _authenticate(
        self,
        *,
        login_id: str,
        password: Any,
        now: datetime | None,
    ) -> tuple[AccountRecord | None, str]:
        account: AccountRecord | None
        with self._lock:
            conn = self._connect()
            try:
                row = self._find_by_login(conn, str(login_id).strip())
                account = _row_to_account(row) if row is not None else None
                stored = row
            finally:
                conn.close()

        if account is None or stored is None:
            # Spend a comparable KDF so login existence is not a timing oracle.
            _derive(str(password), b"\x00" * _SALT_BYTES, self._password_params)
            return None, IdentityReason.INVALID_CREDENTIALS

        params = json.loads(stored["password_params"])
        candidate = _derive(str(password), bytes(stored["password_salt"]), params)
        if not hmac.compare_digest(candidate, bytes(stored["password_digest"])):
            return None, IdentityReason.INVALID_CREDENTIALS
        if account.status == AccountStatus.INVITED:
            return None, IdentityReason.INVITATION_PENDING
        if account.status == AccountStatus.DISABLED:
            return None, IdentityReason.ACCOUNT_DISABLED
        if account.status == AccountStatus.REVOKED:
            return None, IdentityReason.ACCOUNT_REVOKED
        return account, IdentityReason.GRANTED

    def login(
        self,
        *,
        login_id: str,
        password: Any,
        now: datetime | None = None,
    ) -> LoginResult:
        """Authenticate and mint a session token.

        A key-only tier returns ``allowed=False`` / ``sessions_not_supported`` —
        it never fabricates a placeholder session.
        """
        self.require_enabled()
        if not self._policy.sessions_supported:
            return LoginResult(
                allowed=False,
                reason=IdentityReason.SESSIONS_NOT_SUPPORTED,
                detail=(
                    "this tier is key-only: there is no interactive login/session; "
                    "authenticate with an API key"
                ),
            )
        reference = self._now(now)
        account, reason = self._authenticate(login_id=login_id, password=password, now=reference)
        if account is None:
            return LoginResult(allowed=False, reason=reason, detail="login refused")

        timestamp = reference.isoformat()
        session_id = _new_id("sess")
        secret = _new_secret()
        salt = secrets.token_bytes(_SALT_BYTES)
        params = _token_params(self._token_iterations)
        digest = _derive(secret, salt, params)
        expires_at = (reference + self._session_ttl).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO identity_sessions
                           (session_id, account_id, principal_id, token_digest, token_salt,
                            kdf, kdf_params, session_epoch, created_at, expires_at,
                            last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        account.account_id,
                        account.principal_id,
                        sqlite3.Binary(digest),
                        sqlite3.Binary(salt),
                        params["kdf"],
                        json.dumps(params, ensure_ascii=False),
                        account.session_epoch,
                        timestamp,
                        expires_at,
                        timestamp,
                    ),
                )
                self._audit(
                    conn,
                    action="login",
                    actor=account.principal_id,
                    reason=IdentityReason.GRANTED,
                    created_at=timestamp,
                    account_id=account.account_id,
                    principal_id=account.principal_id,
                    before_status=account.status,
                    after_status=account.status,
                )
                conn.commit()
                session = self._get_session(conn, session_id)
            finally:
                conn.close()
        logger.info("identity login account=%s session=%s", account.account_id, session_id)
        return LoginResult(
            allowed=True,
            reason=IdentityReason.GRANTED,
            account_id=account.account_id,
            session=session,
            token=f"acps_{session_id}.{secret}",
            expires_at=expires_at,
        )

    def verify_session(self, token: Any, *, now: datetime | None = None) -> SessionDecision:
        """Validate a session token; deny on revoked/expired/stale-epoch/disabled."""
        reference = self._now(now)
        parsed = _split_token(token, "acps_")
        if parsed is None:
            return SessionDecision(False, IdentityReason.NO_GRANT, detail="malformed session token")
        session_id, secret = parsed

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM identity_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                if row is None:
                    return SessionDecision(
                        False, IdentityReason.NO_GRANT, detail="unknown session token"
                    )
                params = json.loads(row["kdf_params"])
                candidate = _derive(secret, bytes(row["token_salt"]), params)
                if not hmac.compare_digest(candidate, bytes(row["token_digest"])):
                    return SessionDecision(
                        False, IdentityReason.NO_GRANT, detail="session token does not match"
                    )
                session = _row_to_session(row)
                if session.revoked:
                    return SessionDecision(
                        False,
                        IdentityReason.SESSION_REVOKED,
                        session_id=session.session_id,
                        account_id=session.account_id,
                        principal_id=session.principal_id,
                    )
                if _is_past(session.expires_at, reference):
                    return SessionDecision(
                        False,
                        IdentityReason.SESSION_EXPIRED,
                        session_id=session.session_id,
                        account_id=session.account_id,
                        principal_id=session.principal_id,
                    )
                account_row = self._find_by_id(conn, session.account_id)
                if account_row is None:
                    return SessionDecision(
                        False, IdentityReason.UNKNOWN_ACCOUNT, session_id=session.session_id
                    )
                account = _row_to_account(account_row)
                if session.session_epoch != account.session_epoch:
                    return SessionDecision(
                        False,
                        IdentityReason.SESSION_EPOCH_STALE,
                        session_id=session.session_id,
                        account_id=session.account_id,
                        principal_id=session.principal_id,
                    )
                if account.status != AccountStatus.ACTIVE:
                    reason = (
                        IdentityReason.ACCOUNT_DISABLED
                        if account.status == AccountStatus.DISABLED
                        else IdentityReason.ACCOUNT_REVOKED
                    )
                    return SessionDecision(
                        False,
                        reason,
                        session_id=session.session_id,
                        account_id=session.account_id,
                        principal_id=session.principal_id,
                    )
                conn.execute(
                    "UPDATE identity_sessions SET last_seen_at=? WHERE session_id=?",
                    (reference.isoformat(), session.session_id),
                )
                conn.commit()
                return SessionDecision(
                    True,
                    IdentityReason.GRANTED,
                    session_id=session.session_id,
                    account_id=session.account_id,
                    principal_id=session.principal_id,
                    expires_at=session.expires_at,
                )
            finally:
                conn.close()

    def logout(self, token: Any, *, now: datetime | None = None) -> bool:
        """Revoke a single session (idempotent)."""
        parsed = _split_token(token, "acps_")
        if parsed is None:
            return False
        session_id, _ = parsed
        timestamp = self._now(now).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT revoked_at FROM identity_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if row is None:
                    return False
                if row["revoked_at"] is None:
                    conn.execute(
                        "UPDATE identity_sessions SET revoked_at=? WHERE session_id=?",
                        (timestamp, session_id),
                    )
                    conn.commit()
                return True
            finally:
                conn.close()

    # -- credentials (API keys) --------------------------------------------

    def issue_key(
        self,
        account_id: str,
        *,
        scope: str,
        actions: Iterable[str] | str | None = None,
        project: str | None = None,
        expires_at: datetime | str | None = None,
        now: datetime | None = None,
    ) -> GeneratedKey:
        """Issue an API key for an ACTIVE account and register it for revocation.

        Refuses on a disabled/revoked/invited account so a disabled account can
        never mint a new credential.
        """
        self.require_enabled()
        if self._key_manager is None:
            raise IdentityLifecycleError("no ApiKeyManager configured for key issuance")
        account = self.get_account(account_id)
        if account is None:
            raise UnknownAccountError(f"unknown account {account_id!r}")
        if account.status != AccountStatus.ACTIVE:
            raise AccountStateError(
                f"account {account_id!r} is {account.status!r}; cannot issue a key"
            )
        generated = self._key_manager.generate(
            scope=scope,
            actions=actions,
            project=project,
            expires_at=expires_at,
            now=now,
        )
        self.register_key_ref(account_id, generated.key_id, now=now)
        return generated

    def register_key_ref(
        self,
        account_id: str,
        key_id: str,
        *,
        kind: str = "api_key",
        now: datetime | None = None,
    ) -> CredentialRef:
        """Record that *key_id* belongs to *account_id* (for revocation)."""
        timestamp = self._now(now).isoformat()
        ref_id = _new_id("idref")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """INSERT INTO identity_credential_refs
                           (ref_id, account_id, kind, key_id, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ref_id, str(account_id), str(kind), str(key_id), timestamp),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM identity_credential_refs WHERE ref_id=?", (ref_id,)
                ).fetchone()
                return _row_to_ref(row)
            finally:
                conn.close()

    def _revoke_credentials(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        timestamp: str,
    ) -> list[str]:
        """Mark every live session + key ref revoked; return the key ids.

        The caller revokes the key-manager rows **after** this transaction
        commits.  Nesting a second write connection on the same SQLite file
        inside this write transaction deadlocks (``database is locked``), which
        is exactly the single-database deployment B15 must support.
        """
        conn.execute(
            """UPDATE identity_sessions SET revoked_at=?
               WHERE account_id=? AND revoked_at IS NULL""",
            (timestamp, account_id),
        )
        refs = conn.execute(
            """SELECT key_id FROM identity_credential_refs
               WHERE account_id=? AND revoked_at IS NULL""",
            (account_id,),
        ).fetchall()
        conn.execute(
            """UPDATE identity_credential_refs SET revoked_at=?
               WHERE account_id=? AND revoked_at IS NULL""",
            (timestamp, account_id),
        )
        return [str(ref["key_id"]) for ref in refs]

    # -- disable / revoke / recover ----------------------------------------

    def disable_account(
        self,
        account_id: str,
        *,
        actor: str,
        reason: str = "disabled by administrator",
        now: datetime | None = None,
    ) -> AccountRecord:
        """Suspend an account; credentials stop working immediately."""
        return self._deactivate(
            account_id, target=AccountStatus.DISABLED, actor=actor, reason=reason, now=now
        )

    def revoke_account(
        self,
        account_id: str,
        *,
        actor: str,
        reason: str = "revoked by administrator",
        now: datetime | None = None,
    ) -> AccountRecord:
        """Revoke an account; every session/key is destroyed immediately."""
        return self._deactivate(
            account_id, target=AccountStatus.REVOKED, actor=actor, reason=reason, now=now
        )

    def _deactivate(
        self,
        account_id: str,
        *,
        target: str,
        actor: str,
        reason: str,
        now: datetime | None,
    ) -> AccountRecord:
        reference = self._now(now)
        timestamp = reference.isoformat()
        account_id = str(account_id)
        with self._lock:
            conn = self._connect()
            try:
                row = self._find_by_id(conn, account_id)
                if row is None:
                    raise UnknownAccountError(f"unknown account {account_id!r}")
                account = _row_to_account(row)
                if account.status == target:
                    return account
                if account.status == AccountStatus.INVITED and target == AccountStatus.DISABLED:
                    raise AccountStateError("cannot disable an unaccepted invitation")
                column = "disabled_at" if target == AccountStatus.DISABLED else "revoked_at"
                conn.execute(
                    f"""UPDATE identity_accounts
                        SET status=?, {column}=?, session_epoch=session_epoch+1, updated_at=?
                        WHERE account_id=?""",
                    (target, timestamp, timestamp, account_id),
                )
                key_ids = self._revoke_credentials(
                    conn,
                    account_id=account_id,
                    timestamp=timestamp,
                )
                self._audit(
                    conn,
                    action=target,
                    actor=actor,
                    reason=reason,
                    created_at=timestamp,
                    account_id=account_id,
                    principal_id=account.principal_id,
                    before_status=account.status,
                    after_status=target,
                )
                conn.commit()
                updated = self._get_account(conn, account_id)
            finally:
                conn.close()

        revoked = 0
        if self._key_manager is not None:
            for key_id in key_ids:
                if self._key_manager.revoke(key_id, now=None):
                    revoked += 1

        # Terminate live event streams outside the DB transaction (immediate
        # effect on EXISTING streams, not only new connections).
        closed = close_principal_streams(
            self._stream_registry, account.principal_id, reason="permission_changed"
        )
        logger.info(
            "identity %s account=%s principal=%s revoked_keys=%d closed_streams=%s",
            target,
            account_id,
            account.principal_id,
            revoked,
            list(closed),
        )
        return updated

    def recover_account(
        self,
        account_id: str,
        *,
        actor: str,
        reason: str = "recovered by administrator",
        now: datetime | None = None,
    ) -> AccountRecord:
        """Return a DISABLED or REVOKED account to ACTIVE.

        Recovery bumps the session epoch but **never** revives a pre-disable /
        pre-revoke session or key; the account must log in again and any new key
        must be issued afresh.
        """
        reference = self._now(now)
        timestamp = reference.isoformat()
        account_id = str(account_id)
        with self._lock:
            conn = self._connect()
            try:
                row = self._find_by_id(conn, account_id)
                if row is None:
                    raise UnknownAccountError(f"unknown account {account_id!r}")
                account = _row_to_account(row)
                if account.status == AccountStatus.ACTIVE:
                    return account
                if account.status == AccountStatus.INVITED:
                    raise AccountStateError(
                        "an unaccepted invitation cannot be recovered; accept it first"
                    )
                conn.execute(
                    """UPDATE identity_accounts
                       SET status=?, recovered_at=?, session_epoch=session_epoch+1,
                           updated_at=?
                       WHERE account_id=?""",
                    (AccountStatus.ACTIVE, timestamp, timestamp, account_id),
                )
                self._audit(
                    conn,
                    action="recover",
                    actor=actor,
                    reason=reason,
                    created_at=timestamp,
                    account_id=account_id,
                    principal_id=account.principal_id,
                    before_status=account.status,
                    after_status=AccountStatus.ACTIVE,
                )
                conn.commit()
                return self._get_account(conn, account_id)
            finally:
                conn.close()

    # -- entry authorization ------------------------------------------------

    def authorize_entry(
        self,
        principal_id: str,
        entry: str = IdentityEntry.COMMAND,
        *,
        now: datetime | None = None,
    ) -> LifecycleDecision:
        """Authorize a new command/download/event-stream for *principal_id*.

        A disabled/revoked account is denied with the matching typed reason and
        HTTP 403; an INVITED account is denied (no credential yet).  In a
        key-only tier a principal with no account is allowed by default (trusted
        local context).  Never raises for a denial.
        """
        del now  # reserved for time-scoped grants
        entry_name = str(entry)
        if entry_name not in IdentityEntry.ALL:
            return LifecycleDecision(
                allowed=False,
                entry=entry_name,
                reason_code=IdentityReason.UNKNOWN_ENTRY,
                principal_id=str(principal_id),
                http_status=LIFECYCLE_HTTP_FORBIDDEN,
                detail=f"unknown identity entry {entry_name!r} (fail closed)",
            )
        account = self.get_account_by_principal(str(principal_id))
        if account is None:
            if self._policy.mode == IdentityMode.KEY_ONLY and self._allow_unknown_key_only:
                return LifecycleDecision(
                    allowed=True,
                    entry=entry_name,
                    reason_code=IdentityReason.GRANTED,
                    principal_id=str(principal_id),
                    http_status=LIFECYCLE_HTTP_OK,
                    detail="key-only trusted context; no account required",
                )
            return LifecycleDecision(
                allowed=False,
                entry=entry_name,
                reason_code=IdentityReason.UNKNOWN_ACCOUNT,
                principal_id=str(principal_id),
                http_status=LIFECYCLE_HTTP_FORBIDDEN,
                detail=f"no account for principal {principal_id!r}",
            )
        return self._entry_decision(entry_name, account)

    def authorize_command(
        self, principal_id: str, *, now: datetime | None = None
    ) -> LifecycleDecision:
        return self.authorize_entry(principal_id, IdentityEntry.COMMAND, now=now)

    def authorize_download(
        self, principal_id: str, *, now: datetime | None = None
    ) -> LifecycleDecision:
        return self.authorize_entry(principal_id, IdentityEntry.DOWNLOAD, now=now)

    def _entry_decision(self, entry: str, account: AccountRecord) -> LifecycleDecision:
        if account.status == AccountStatus.ACTIVE:
            return LifecycleDecision(
                allowed=True,
                entry=entry,
                reason_code=IdentityReason.GRANTED,
                principal_id=account.principal_id,
                account_id=account.account_id,
                status=account.status,
                http_status=LIFECYCLE_HTTP_OK,
                detail=f"entry {entry!r} allowed for active account",
            )
        reason_map = {
            AccountStatus.DISABLED: IdentityReason.ACCOUNT_DISABLED,
            AccountStatus.REVOKED: IdentityReason.ACCOUNT_REVOKED,
            AccountStatus.INVITED: IdentityReason.INVITATION_PENDING,
        }
        reason = reason_map.get(account.status, IdentityReason.ACCOUNT_NOT_ACTIVE)
        return LifecycleDecision(
            allowed=False,
            entry=entry,
            reason_code=reason,
            principal_id=account.principal_id,
            account_id=account.account_id,
            status=account.status,
            http_status=LIFECYCLE_HTTP_FORBIDDEN,
            detail=f"entry {entry!r} denied: account is {account.status!r}",
        )

    def stream_authorizer(self, *, allow_unknown: bool | None = None) -> LifecycleStreamAuthorizer:
        """Return a ``StreamAuthorizer`` bound to this lifecycle store."""
        resolved = self._allow_unknown_key_only if allow_unknown is None else bool(allow_unknown)
        return LifecycleStreamAuthorizer(self, allow_unknown=resolved)

    def principal_for_account(
        self,
        account_id: str,
        *,
        tenant_id: str | None = None,
        kind: str = PrincipalKind.USER,
        deployment_tier: str | None = None,
    ) -> Principal:
        """Build a :class:`Principal` from an account for use with ``resolve_scope``.

        Roles come from the account only — the tier never contributes a role
        (mirrors ``contracts.access.tier_roles`` returning ``()``).

        ``deployment_tier`` overrides the *policy* tier for the resolved
        principal.  B15 uses it so an opt-in ``api-key`` **identity mode** can
        enforce project-scoped credentials while the deployment tier remains the
        explicit ``local-single`` default (the two axes are never collapsed).
        """
        account = self.get_account(account_id)
        if account is None:
            raise UnknownAccountError(f"unknown account {account_id!r}")
        return Principal(
            principal_id=account.principal_id,
            kind=str(kind),
            tenant_id=tenant_id or account.principal_id,
            roles=account.roles,
            deployment_tier=str(deployment_tier or self._policy.deployment_tier),
        )

    def account_for_key(self, key_id: str) -> AccountRecord | None:
        """Return the ACTIVE/disabled/revoked account a live key reference belongs to.

        This is the reverse of :meth:`issue_key`/:meth:`register_key_ref`: a key
        that was never registered to an account has no principal and is denied.
        Revoked credential references are ignored so a re-issued key id can never
        inherit an old account binding.
        """
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """SELECT account_id FROM identity_credential_refs
                       WHERE key_id=? AND revoked_at IS NULL
                       ORDER BY created_at DESC LIMIT 1""",
                    (str(key_id),),
                ).fetchone()
                if row is None:
                    return None
                account_id = str(row["account_id"])
                account_row = self._find_by_id(conn, account_id)
                return None if account_row is None else _row_to_account(account_row)
            finally:
                conn.close()

    # -- reads --------------------------------------------------------------

    def _find_by_id(self, conn: sqlite3.Connection, account_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM identity_accounts WHERE account_id=?", (account_id,)
        ).fetchone()

    def _find_by_login(self, conn: sqlite3.Connection, login_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM identity_accounts WHERE login_id=?", (login_id,)
        ).fetchone()

    def _get_account(self, conn: sqlite3.Connection, account_id: str) -> AccountRecord:
        row = self._find_by_id(conn, account_id)
        if row is None:  # pragma: no cover - guarded by callers
            raise UnknownAccountError(f"unknown account {account_id!r}")
        return _row_to_account(row)

    def _get_session(self, conn: sqlite3.Connection, session_id: str) -> SessionRecord:
        row = conn.execute(
            "SELECT * FROM identity_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - guarded by callers
            raise IdentityLifecycleError(f"session {session_id!r} vanished")
        return _row_to_session(row)

    def _management_account_exists(self, conn: sqlite3.Connection) -> bool:
        rows = conn.execute(
            "SELECT roles FROM identity_accounts WHERE status<>?", (AccountStatus.REVOKED,)
        ).fetchall()
        for row in rows:
            try:
                roles = json.loads(row["roles"]) or []
            except (json.JSONDecodeError, TypeError):  # pragma: no cover - defensive
                continue
            if any(role in Role.MANAGEMENT for role in roles):
                return True
        return False

    def get_account(self, account_id: str) -> AccountRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = self._find_by_id(conn, str(account_id))
                return _row_to_account(row) if row is not None else None
            finally:
                conn.close()

    def get_account_by_principal(self, principal_id: str) -> AccountRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM identity_accounts WHERE principal_id=?",
                    (str(principal_id),),
                ).fetchone()
                return _row_to_account(row) if row is not None else None
            finally:
                conn.close()

    def get_account_by_login(self, login_id: str) -> AccountRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = self._find_by_login(conn, str(login_id))
                return _row_to_account(row) if row is not None else None
            finally:
                conn.close()

    def list_accounts(self) -> list[AccountRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM identity_accounts ORDER BY created_at, account_id"
                ).fetchall()
                return [_row_to_account(row) for row in rows]
            finally:
                conn.close()

    def list_sessions(self, account_id: str | None = None) -> list[SessionRecord]:
        with self._lock:
            conn = self._connect()
            try:
                if account_id is None:
                    rows = conn.execute(
                        "SELECT * FROM identity_sessions ORDER BY created_at, session_id"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM identity_sessions WHERE account_id=? "
                        "ORDER BY created_at, session_id",
                        (str(account_id),),
                    ).fetchall()
                return [_row_to_session(row) for row in rows]
            finally:
                conn.close()

    def list_key_refs(self, account_id: str | None = None) -> list[CredentialRef]:
        with self._lock:
            conn = self._connect()
            try:
                if account_id is None:
                    rows = conn.execute(
                        "SELECT * FROM identity_credential_refs ORDER BY created_at, ref_id"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM identity_credential_refs WHERE account_id=? "
                        "ORDER BY created_at, ref_id",
                        (str(account_id),),
                    ).fetchall()
                return [_row_to_ref(row) for row in rows]
            finally:
                conn.close()

    def list_events(self, account_id: str | None = None) -> list[LifecycleEvent]:
        with self._lock:
            conn = self._connect()
            try:
                if account_id is None:
                    rows = conn.execute(
                        "SELECT * FROM identity_lifecycle_events ORDER BY created_at, event_id"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM identity_lifecycle_events WHERE account_id=? "
                        "ORDER BY created_at, event_id",
                        (str(account_id),),
                    ).fetchall()
                return [_row_to_event(row) for row in rows]
            finally:
                conn.close()

    def get_invitation(self, invitation_id: str) -> InvitationRecord | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM identity_invitations WHERE invitation_id=?",
                    (str(invitation_id),),
                ).fetchone()
                return _row_to_invitation(row) if row is not None else None
            finally:
                conn.close()

    def counts(self) -> dict[str, int]:
        """Small counts projection for diagnostics."""
        with self._lock:
            conn = self._connect()
            try:

                def count(table: str) -> int:
                    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                    return int(row["n"]) if row is not None else 0

                return {
                    "accounts": count("identity_accounts"),
                    "sessions": count("identity_sessions"),
                    "invitations": count("identity_invitations"),
                    "key_refs": count("identity_credential_refs"),
                    "events": count("identity_lifecycle_events"),
                }
            finally:
                conn.close()
