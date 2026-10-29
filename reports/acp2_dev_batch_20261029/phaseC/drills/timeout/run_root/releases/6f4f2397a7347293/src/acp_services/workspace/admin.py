"""Admin surface — use-case authorization, audit trail, restricted broker.

B09-4 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 51, MU-09 /
§22.6).  This module is the Workspace-side management boundary: it names the
administrator use cases, re-authorizes **at every entry** (never relies on a
filtered menu), records an ``organization_events``-style audit row (actor /
object / before-and-after version / reason / timestamp) and exposes a
*restricted* broker that accepts only whitelisted install-time operations —
never an arbitrary ``sudo``/shell channel.

Three non-negotiable facts encoded here (plan What / Must NOT):

* **Tier is not a role.**  A deployment :class:`~acp_services.contracts.access.Tier`
  (``local-single`` / ``lab`` / ``hosted``) is an authorisation/isolation axis,
  not an authority.  :func:`tier_grants_management` is the executable statement
  that a tier upgrade never confers a management role, and
  :class:`AdminAuthorizer` never consults the tier for authority.
* **A normal key has no management power.**  Management requires an explicit
  management :class:`~acp_services.contracts.access.Role` *and* (by default) a
  credential carrying the ``admin`` action.  A read/write API key hitting a
  management entry is denied with HTTP ``403``.
* **Hidden menus are not access control.**  :func:`admin_management_use_cases`
  returns the *complete* catalogue regardless of caller; enforcement lives in
  :meth:`AdminAuthorizer.authorize` / :meth:`AdminService.enter`, which run once
  per entry and are the only gate.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package, so this module is stdlib plus the Services-native
``contracts.access`` vocabulary and the (Services) API-key result type.  The
broker performs **no** execution: it returns a validated decision/plan, and a
source-level guard proves no ``subprocess``/``os.system``/pty channel exists.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp_services.contracts.access import (
    AccessAction,
    ExecutionMode,
    Principal,
    PrincipalKind,
    Role,
    Tier,
    User,
)
from acp_services.workspace.keys import KeyDecision

logger = logging.getLogger(__name__)

__all__ = [
    "ADMIN_HTTP_OK",
    "ADMIN_HTTP_FORBIDDEN",
    "AdminEntry",
    "AdminReason",
    "AdminDecision",
    "AdminAuthorizer",
    "AdminAuditRecord",
    "AdminAuditLog",
    "AdminService",
    "admin_management_use_cases",
    "is_management_principal",
    "tier_grants_management",
    "BrokerOperation",
    "BrokerReason",
    "BrokerRequest",
    "BrokerDecision",
    "RestrictedBroker",
]

#: HTTP status the API layer maps a management decision onto.
ADMIN_HTTP_OK = 200
ADMIN_HTTP_FORBIDDEN = 403


# ---------------------------------------------------------------------------
# Management use-case catalogue
# ---------------------------------------------------------------------------


class AdminEntry(str):
    """The administrator use cases (plan prose: users/members/roles,
    tier/quota, nodes/queues/active tasks, storage/usage, audit/credential
    revocation).

    ``SCIENTIFIC_CONTENT`` is a special *read through the admin surface* entry:
    it is reachable only by an administrator and every read is audited (a
    management read of scientific content may never be silent).
    """

    USERS = "users"
    MEMBERS = "members"
    ROLES = "roles"
    TIERS = "tier"
    QUOTA = "quota"
    NODES = "nodes"
    QUEUES = "queues"
    ACTIVE_TASKS = "active_tasks"
    STORAGE = "storage"
    USAGE = "usage"
    AUDIT = "audit"
    CREDENTIAL_REVOCATION = "credential_revocation"
    SCIENTIFIC_CONTENT = "scientific_content"

    # Ordered management catalogue (12 entries, plan B09-4).
    MANAGEMENT_USE_CASES = (
        USERS,
        MEMBERS,
        ROLES,
        TIERS,
        QUOTA,
        NODES,
        QUEUES,
        ACTIVE_TASKS,
        STORAGE,
        USAGE,
        AUDIT,
        CREDENTIAL_REVOCATION,
    )
    MANAGEMENT = frozenset(MANAGEMENT_USE_CASES)
    SCIENTIFIC = frozenset({SCIENTIFIC_CONTENT})
    ALL = MANAGEMENT | SCIENTIFIC


def admin_management_use_cases() -> tuple[str, ...]:
    """Return the complete management catalogue — never filtered by caller.

    Returned in full for every principal on purpose: hiding a menu item is not
    access control.  The gate is :meth:`AdminAuthorizer.authorize`, invoked once
    per entry.
    """
    return AdminEntry.MANAGEMENT_USE_CASES


def entry_is_known(entry: str) -> bool:
    """Whether *entry* is one of the known admin entries."""
    return str(entry) in AdminEntry.ALL


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class AdminReason(str):
    """Decision reason vocabulary.

    Core values mirror :class:`acp_services.contracts.access.ReasonCode`
    (``granted`` / ``no_grant``) so an authorisation layer maps them 1:1; the
    rest are admin-specific and fail closed.
    """

    GRANTED = "granted"
    NO_GRANT = "no_grant"
    ROLE_REQUIRED = "role_required"
    KEY_LACKS_ADMIN = "key_admin_required"
    ACCOUNT_DISABLED = "account_disabled"
    UNKNOWN_ENTRY = "unknown_entry"
    ALL = frozenset(
        {
            GRANTED,
            NO_GRANT,
            ROLE_REQUIRED,
            KEY_LACKS_ADMIN,
            ACCOUNT_DISABLED,
            UNKNOWN_ENTRY,
        }
    )


@dataclass(frozen=True)
class AdminDecision:
    """One per-entry management authorization verdict (never raises on denial)."""

    allowed: bool
    entry: str
    reason_code: str
    principal_id: str
    key_id: str | None = None
    http_status: int = ADMIN_HTTP_OK
    detail: str = ""

    def error_detail(self) -> dict[str, str]:
        """HTTP error body matching the existing 403 contract shape."""
        return {"code": self.reason_code, "message": self.detail}

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "entry": self.entry,
            "reason_code": self.reason_code,
            "principal_id": self.principal_id,
            "key_id": self.key_id,
            "http_status": self.http_status,
            "detail": self.detail,
        }


def is_management_principal(principal: Principal, user: User | None = None) -> bool:
    """Whether *principal* holds an explicit management role.

    Only ``principal.roles`` (and, when supplied, the identity-model ``user``'s
    roles) are consulted.  The deployment tier is deliberately ignored: a tier
    is not a role.
    """
    roles = set(principal.roles)
    if user is not None and not user.disabled:
        roles.update(user.roles)
    return any(role in Role.MANAGEMENT for role in roles)


def tier_grants_management(tier: str) -> bool:
    """Return whether a deployment *tier* confers management authority.

    Always ``False`` — the executable statement of "tier upgrade grants no
    admin role" (mirrors ``contracts.access.tier_roles`` returning ``()``).
    """
    del tier
    return False


class AdminAuthorizer:
    """Re-authorize every management entry from the verified principal.

    Args:
        require_admin_key: When True (default), a credential must additionally
            be presented and carry the ``admin`` action.  A read/write API key
            is therefore denied even for a management principal — the role
            says *who*, the key action authorizes *this credential*.
    """

    def __init__(self, *, require_admin_key: bool = True) -> None:
        self._require_admin_key = bool(require_admin_key)

    def authorize(
        self,
        entry: str,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
        now: datetime | None = None,
    ) -> AdminDecision:
        """Authorize one management entry for *principal*.

        Runs afresh on every call (no cached verdict), so a caller cannot turn a
        filtered list into authority.  An unknown entry is denied fail-closed.
        """
        del now  # reserved for future expiry-aware management credentials

        def verdict(reason: str, detail: str) -> AdminDecision:
            allowed = reason == AdminReason.GRANTED
            return AdminDecision(
                allowed=allowed,
                entry=str(entry),
                reason_code=str(reason),
                principal_id=principal.principal_id,
                key_id=(key_decision.key_id if key_decision is not None else None),
                http_status=ADMIN_HTTP_OK if allowed else ADMIN_HTTP_FORBIDDEN,
                detail=detail,
            )

        if not entry_is_known(entry):
            return verdict(
                AdminReason.UNKNOWN_ENTRY,
                f"unknown management entry {entry!r} is denied (fail closed)",
            )

        if str(principal.kind) not in PrincipalKind.ALL or str(principal.kind) == (
            PrincipalKind.ANONYMOUS
        ):
            return verdict(
                AdminReason.NO_GRANT,
                "anonymous/unknown principals hold no management authority",
            )

        if user is not None and user.disabled:
            return verdict(
                AdminReason.ACCOUNT_DISABLED,
                f"account {user.user_id!r} is disabled",
            )

        if not is_management_principal(principal, user):
            return verdict(
                AdminReason.ROLE_REQUIRED,
                (
                    "management authority requires an explicit operator/admin role; "
                    f"deployment tier {str(principal.deployment_tier)!r} grants none"
                ),
            )

        if self._require_admin_key:
            actions = tuple(getattr(key_decision, "actions", ()) or ())
            if (
                key_decision is None
                or not key_decision.allowed
                or AccessAction.ADMIN not in actions
            ):
                return verdict(
                    AdminReason.KEY_LACKS_ADMIN,
                    "management entry requires a credential carrying the 'admin' action",
                )

        return verdict(
            AdminReason.GRANTED,
            f"management entry {entry!r} allowed for principal {principal.principal_id!r}",
        )

    def authorize_all(
        self,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
    ) -> tuple[AdminDecision, ...]:
        """Re-authorize **each** management catalogue entry independently."""
        return tuple(
            self.authorize(entry, principal=principal, key_decision=key_decision, user=user)
            for entry in admin_management_use_cases()
        )


# ---------------------------------------------------------------------------
# Audit trail (organization_events pattern, Services-native)
# ---------------------------------------------------------------------------

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    credential TEXT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_version TEXT,
    after_version TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_AUDIT_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_admin_audit_object "
    "ON admin_audit_events (object_type, object_id)"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AdminAuditRecord:
    """One persisted audit fact.

    Fields are exactly the plan's requirement: ``actor`` (who) / ``object_type``
    + ``object_id`` (object) / ``before_version`` + ``after_version``
    (before-and-after version) / ``reason`` / ``created_at`` (timestamp).
    """

    audit_id: int
    actor: str
    object_type: str
    object_id: str
    action: str
    reason: str
    created_at: str
    credential: str | None = None
    before_version: str | None = None
    after_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "actor": self.actor,
            "credential": self.credential,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "action": self.action,
            "before_version": self.before_version,
            "after_version": self.after_version,
            "reason": self.reason,
            "created_at": self.created_at,
        }


class AdminAuditLog:
    """SQLite-backed audit trail mirroring the ``organization_events`` pattern.

    A management mutation is only complete when its audit row is persisted in
    the same connection, so a caller cannot perform a state change without the
    matching ``(actor, object, before/after version, reason, time)`` record.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_AUDIT_SCHEMA)
                conn.execute(_AUDIT_INDEX)
                conn.commit()
            finally:
                conn.close()

    def record(
        self,
        *,
        actor: str,
        object_type: str,
        object_id: str,
        action: str,
        reason: str,
        before_version: str | None = None,
        after_version: str | None = None,
        credential: str | None = None,
        now: datetime | None = None,
    ) -> AdminAuditRecord:
        """Insert one complete audit fact; raise if a required field is empty.

        The completeness check (actor/object/action/reason) is the executable
        form of "审计字段完整": an incomplete management action cannot be
        recorded, and therefore must not be performed.
        """
        missing = [
            name
            for name, value in (
                ("actor", actor),
                ("object_type", object_type),
                ("object_id", object_id),
                ("action", action),
                ("reason", reason),
            )
            if not str(value or "").strip()
        ]
        if missing:
            raise ValueError(f"audit record missing required field(s): {', '.join(missing)}")

        created_at = (now or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """INSERT INTO admin_audit_events
                           (actor, credential, object_type, object_id, action,
                            before_version, after_version, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(actor),
                        None if credential is None else str(credential),
                        str(object_type),
                        str(object_id),
                        str(action),
                        None if before_version is None else str(before_version),
                        None if after_version is None else str(after_version),
                        str(reason),
                        created_at,
                    ),
                )
                audit_id = int(cursor.lastrowid or 0)
                conn.commit()
            finally:
                conn.close()
        logger.info(
            "admin audit actor=%s action=%s object=%s/%s reason=%s",
            actor,
            action,
            object_type,
            object_id,
            reason,
        )
        return AdminAuditRecord(
            audit_id=audit_id,
            actor=str(actor),
            credential=None if credential is None else str(credential),
            object_type=str(object_type),
            object_id=str(object_id),
            action=str(action),
            before_version=None if before_version is None else str(before_version),
            after_version=None if after_version is None else str(after_version),
            reason=str(reason),
            created_at=created_at,
        )

    def list_events(
        self,
        *,
        object_type: str | None = None,
        object_id: str | None = None,
        limit: int = 200,
    ) -> list[AdminAuditRecord]:
        """Return audit rows newest-first, optionally filtered by object."""
        clauses: list[str] = []
        params: list[Any] = []
        if object_type is not None:
            clauses.append("object_type=?")
            params.append(object_type)
        if object_id is not None:
            clauses.append("object_id=?")
            params.append(object_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM admin_audit_events{where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(query, params).fetchall()
            finally:
                conn.close()
        return [_row_to_audit(row) for row in rows]

    def count(self) -> int:
        """Total number of persisted audit facts."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM admin_audit_events").fetchone()
            finally:
                conn.close()
        return int(row["n"]) if row is not None else 0


def _row_to_audit(row: sqlite3.Row) -> AdminAuditRecord:
    return AdminAuditRecord(
        audit_id=int(row["id"]),
        actor=row["actor"],
        credential=row["credential"],
        object_type=row["object_type"],
        object_id=row["object_id"],
        action=row["action"],
        before_version=row["before_version"],
        after_version=row["after_version"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Management use-case facade (re-auth + audit)
# ---------------------------------------------------------------------------


class AdminService:
    """Perform management use cases with per-entry re-auth and mandatory audit.

    Args:
        audit: An :class:`AdminAuditLog`.  Required — there is no code path that
            performs a management operation (including reading scientific
            content through the admin surface) without an audit sink.
        authorizer: Optional :class:`AdminAuthorizer`; a default is built.
    """

    def __init__(
        self,
        audit: AdminAuditLog,
        *,
        authorizer: AdminAuthorizer | None = None,
    ) -> None:
        if audit is None:
            raise ValueError("AdminService requires an audit log (no unaudited management reads)")
        self._audit = audit
        self._authorizer = authorizer or AdminAuthorizer()

    @property
    def authorizer(self) -> AdminAuthorizer:
        return self._authorizer

    def management_catalogue(self) -> tuple[str, ...]:
        """The full, unfiltered catalogue (enforcement is :meth:`enter`)."""
        return admin_management_use_cases()

    def enter(
        self,
        entry: str,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
    ) -> AdminDecision:
        """Re-authorize a single management entry (fresh decision each call)."""
        return self._authorizer.authorize(
            entry, principal=principal, key_decision=key_decision, user=user
        )

    def read_scientific_content(
        self,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
        object_type: str,
        object_id: str,
        version: str,
        reason: str,
    ) -> AdminDecision:
        """Read scientific content through the admin surface, always audited.

        A read has ``before_version == after_version == version`` so the trail
        still records the version the administrator observed.
        """
        decision = self.enter(
            AdminEntry.SCIENTIFIC_CONTENT,
            principal=principal,
            key_decision=key_decision,
            user=user,
        )
        self._audit.record(
            actor=principal.principal_id,
            credential=(key_decision.key_id if key_decision is not None else None),
            object_type=object_type,
            object_id=object_id,
            action="read",
            before_version=version,
            after_version=version,
            reason=reason if decision.allowed else decision.reason_code,
        )
        return decision

    def mutate(
        self,
        entry: str,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
        object_type: str,
        object_id: str,
        action: str,
        before_version: str,
        after_version: str,
        reason: str,
    ) -> AdminDecision:
        """Perform an audited management mutation (before/after versions)."""
        decision = self.enter(entry, principal=principal, key_decision=key_decision, user=user)
        self._audit.record(
            actor=principal.principal_id,
            credential=(key_decision.key_id if key_decision is not None else None),
            object_type=object_type,
            object_id=object_id,
            action=action,
            before_version=before_version,
            after_version=after_version,
            reason=reason if decision.allowed else decision.reason_code,
        )
        return decision

    def revoke_credential(
        self,
        *,
        principal: Principal,
        key_decision: KeyDecision | None = None,
        user: User | None = None,
        target_key_id: str,
        before_version: str,
        after_version: str,
        reason: str,
    ) -> AdminDecision:
        """Audited credential revocation (a first-class management use case)."""
        return self.mutate(
            AdminEntry.CREDENTIAL_REVOCATION,
            principal=principal,
            key_decision=key_decision,
            user=user,
            object_type="api_key",
            object_id=target_key_id,
            action="revoke",
            before_version=before_version,
            after_version=after_version,
            reason=reason,
        )


# ---------------------------------------------------------------------------
# Restricted broker (no arbitrary root / shell channel)
# ---------------------------------------------------------------------------


class BrokerOperation(str):
    """The only install-time privileged operations the broker may describe.

    Creating a system user, setting a quota and preparing an isolation
    environment are configuration operations — each is expressed as structured
    parameters (data), never as a command string.
    """

    CREATE_SYSTEM_USER = "create_system_user"
    SET_QUOTA = "set_quota"
    SETUP_ISOLATION = "setup_isolation"
    ALL = frozenset({CREATE_SYSTEM_USER, SET_QUOTA, SETUP_ISOLATION})


class BrokerReason(str):
    """Broker refusal/acceptance vocabulary."""

    GRANTED = "granted"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    PARAMETER_NOT_ALLOWED = "parameter_not_allowed"
    MISSING_PARAMETER = "missing_parameter"
    INVALID_VALUE = "invalid_value"
    ROOT_CHANNEL_FORBIDDEN = "root_channel_forbidden"
    ALL = frozenset(
        {
            GRANTED,
            OPERATION_NOT_ALLOWED,
            PARAMETER_NOT_ALLOWED,
            MISSING_PARAMETER,
            INVALID_VALUE,
            ROOT_CHANNEL_FORBIDDEN,
        }
    )


#: Exact parameter name allow-list per operation.  Anything outside it —
#: including ``sudo`` / ``shell`` / ``command`` / ``args`` — is refused.
_BROKER_PARAMETERS: dict[str, tuple[str, ...]] = {
    BrokerOperation.CREATE_SYSTEM_USER: ("username",),
    BrokerOperation.SET_QUOTA: ("subject", "dimension", "value"),
    BrokerOperation.SETUP_ISOLATION: ("tier", "execution_mode"),
}

_QUOTA_DIMENSIONS = frozenset({"cores", "memory_mb", "storage_bytes", "concurrency", "duration_s"})

_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
#: Shell-affecting characters; their presence in any value is refused outright.
_SHELL_METACHARS = frozenset(";|&$`><\n\r(){}*?!~\\\"'")
#: Parameter names that request a shell/root channel by name.
_FORBIDDEN_PARAMETER_NAMES = frozenset(
    {"sudo", "shell", "command", "cmd", "args", "argv", "script", "exec", "env", "path"}
)


@dataclass(frozen=True)
class BrokerRequest:
    """A broker request: a whitelisted operation plus structured parameters."""

    operation: str
    parameters: Mapping[str, str]

    def parameter_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.parameters))


@dataclass(frozen=True)
class BrokerDecision:
    """A validated broker outcome; carries no executable command string."""

    allowed: bool
    operation: str
    reason_code: str
    parameters: Mapping[str, str]
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "operation": self.operation,
            "reason_code": self.reason_code,
            "parameters": dict(self.parameters),
            "detail": self.detail,
        }


class RestrictedBroker:
    """Validate install-time privileged operations against a hard allow-list.

    The broker **never executes anything**: it validates the requested
    operation and its *structured* parameters and returns a decision.  There is
    no code path that builds a shell command, runs ``sudo`` or spawns a process,
    so an arbitrary root channel cannot exist here by construction.
    """

    def whitelist(self) -> tuple[str, ...]:
        """The accepted operation names, sorted."""
        return tuple(sorted(BrokerOperation.ALL))

    def allowed_parameters(self, operation: str) -> tuple[str, ...]:
        """The exact parameter names accepted for *operation* (empty if unknown)."""
        return _BROKER_PARAMETERS.get(str(operation), ())

    def execute(self, request: BrokerRequest) -> BrokerDecision:
        """Validate *request*; refuse anything outside the whitelist."""
        operation = str(request.operation)
        parameters = {str(k): "" if v is None else str(v) for k, v in request.parameters.items()}

        def refuse(reason: str, detail: str) -> BrokerDecision:
            return BrokerDecision(
                allowed=False,
                operation=operation,
                reason_code=str(reason),
                parameters=parameters,
                detail=detail,
            )

        def accept(detail: str) -> BrokerDecision:
            return BrokerDecision(
                allowed=True,
                operation=operation,
                reason_code=BrokerReason.GRANTED,
                parameters=parameters,
                detail=detail,
            )

        if operation not in BrokerOperation.ALL:
            return refuse(
                BrokerReason.OPERATION_NOT_ALLOWED,
                f"operation {operation!r} is not in the install-time whitelist",
            )

        allowed_names = _BROKER_PARAMETERS[operation]
        forbidden = sorted(
            name
            for name in parameters
            if name in _FORBIDDEN_PARAMETER_NAMES or name not in allowed_names
        )
        if forbidden:
            return refuse(
                BrokerReason.PARAMETER_NOT_ALLOWED,
                f"parameter(s) {forbidden!r} are outside the whitelist for {operation!r}",
            )

        missing = sorted(name for name in allowed_names if name not in parameters)
        if missing:
            return refuse(
                BrokerReason.MISSING_PARAMETER,
                f"operation {operation!r} requires parameter(s) {missing!r}",
            )

        for name, value in parameters.items():
            if name in _FORBIDDEN_PARAMETER_NAMES:
                return refuse(
                    BrokerReason.ROOT_CHANNEL_FORBIDDEN,
                    f"parameter {name!r} requests a root/shell channel",
                )
            if any(ch in _SHELL_METACHARS for ch in value) or value.startswith("-"):
                return refuse(
                    BrokerReason.INVALID_VALUE,
                    f"value for {name!r} contains shell-affecting characters or argv flags",
                )

        invalid = self._validate_values(operation, parameters)
        if invalid is not None:
            return refuse(BrokerReason.INVALID_VALUE, invalid)

        return accept(f"operation {operation!r} accepted (structured parameters only)")

    def _validate_values(self, operation: str, parameters: Mapping[str, str]) -> str | None:
        """Return an error detail for a bad value, or ``None`` when all valid."""
        if operation == BrokerOperation.CREATE_SYSTEM_USER:
            if not _NAME_RE.match(parameters["username"]):
                return f"username {parameters['username']!r} is not a valid system user name"
        elif operation == BrokerOperation.SET_QUOTA:
            if not _NAME_RE.match(parameters["subject"]):
                return f"quota subject {parameters['subject']!r} is not a valid name"
            if parameters["dimension"] not in _QUOTA_DIMENSIONS:
                return f"quota dimension {parameters['dimension']!r} is not recognised"
            value = parameters["value"]
            if not value.isdigit() or int(value) <= 0:
                return f"quota value {value!r} must be a positive integer"
        elif operation == BrokerOperation.SETUP_ISOLATION:
            if parameters["tier"] not in Tier.ALL:
                return f"isolation tier {parameters['tier']!r} is not recognised"
            if parameters["execution_mode"] not in ExecutionMode.ALL:
                return f"execution mode {parameters['execution_mode']!r} is not recognised"
        return None
