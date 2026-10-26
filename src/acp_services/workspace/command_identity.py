"""One frozen identity value for every command entry (M0-04 / D02).

Before this module the HTTP submit entry hard-coded
:data:`acp_services.contracts.access.LOCAL_PRINCIPAL_ID` when it built the
idempotency scope, while the admission ledger recorded the Controller-process
default principal.  In the opt-in multi-user deployment that meant two
different authenticated users shared one idempotency scope (the second user's
retry replayed the first user's task) and one admission principal (usage was
merged).  This module is the **only** new value object for that gap:

* every field projects an identity that a server-side authority already
  resolved — :class:`~acp_services.workspace.http_identity.VerifiedRequestIdentity`
  for an authenticated HTTP request, :class:`~acp_services.workspace.server_identity.ServerIdentity`
  for the trusted local Controller, or an explicit local-operator flag for the
  CLI.  There is no second identity resolver and no second ``EntryScope``;
* the raw credential never enters the object: only the non-secret ``key_id``
  reference, the granted actions and the credential's permission scope do.  A
  secret must never be logged, persisted in the ledger or published in a BOM;
* :attr:`CommandIdentity.permission_scope` is a transport/permission attribute.
  It never substitutes for :attr:`CommandIdentity.principal_id`: the principal
  is what the idempotency scope and the admission ledger key on.

``LOCAL_PRINCIPAL_ID`` remains legal on exactly one path: a real ``local-single``
operator entry (:meth:`CommandIdentity.from_local`, or
:meth:`CommandIdentity.from_server` for the Controller process itself).  An
authenticated user entry carries that user's principal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.contracts.access import (
    LOCAL_PRINCIPAL_ID,
    Principal,
    tier_baseline_actions,
)
from acp_services.runtime.idempotency import SCOPE_ORIGIN_VERIFIED, resolve_scope_id
from acp_services.workspace.server_identity import ServerIdentity

__all__ = [
    "DEFAULT_COMMAND_PROJECT",
    "EntryClass",
    "PermissionScope",
    "SubjectKind",
    "CommandIdentity",
]

#: Project used for the idempotency scope when no project was resolved yet
#: (the manager's own default project still wins at submit time).
DEFAULT_COMMAND_PROJECT = "default"


class SubjectKind(str):
    """Who the command identity represents.

    ``operator`` is reserved for the trusted ``local-single`` operator; an
    authenticated multi-user request always uses the verified principal kind.
    """

    OPERATOR = "operator"
    USER = "user"
    SERVICE = "service"
    ANONYMOUS = "anonymous"
    ALL = frozenset({OPERATOR, USER, SERVICE, ANONYMOUS})


class EntryClass(str):
    """Which entry produced the command (behaviour-equivalence axis)."""

    HTTP = "http"
    CLI = "cli"
    MCP = "mcp"
    WORKER = "worker"
    CONTROLLER = "controller"
    ALL = frozenset({HTTP, CLI, MCP, WORKER, CONTROLLER})


class PermissionScope(str):
    """Transport/permission attribute (never a principal substitute)."""

    LOCAL_SINGLE = "local-single"
    LOCAL_OPERATOR = "local-operator"
    STDIO = "stdio"
    API_KEY = "api-key"


@dataclass(frozen=True)
class CommandIdentity:
    """The server-verified identity of exactly one command entry.

    Attributes:
        principal_id: The authoritative principal of the caller.
        project_id: The resolved project scope (``None`` = not narrowed yet).
        key_id: Non-secret reference of the verifying credential, when any.
        actions: The credential/deployment action ceiling (empty = no ceiling).
        subject_kind: :class:`SubjectKind` member; ``operator`` only for the
            explicit ``local-single`` operator paths.
        entry_class: :class:`EntryClass` member naming the entry.
        permission_scope: Transport/permission attribute (key scope, deployment
            tier or transport name).  Not part of the principal or scope id.
        tenant_id: Tenant boundary reported by the identity store, if known.
        display_name: Non-authoritative display label, if known.
    """

    principal_id: str
    project_id: str | None = None
    key_id: str | None = None
    actions: tuple[str, ...] = ()
    subject_kind: str = SubjectKind.USER
    entry_class: str = EntryClass.HTTP
    permission_scope: str = PermissionScope.LOCAL_SINGLE
    tenant_id: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not str(self.principal_id or "").strip():
            raise ValueError("principal_id must be non-empty")
        if str(self.entry_class) not in EntryClass.ALL:
            raise ValueError(f"unknown entry class {self.entry_class!r}")
        if str(self.subject_kind) not in SubjectKind.ALL:
            raise ValueError(f"unknown subject kind {self.subject_kind!r}")

    # -- construction from an already-verified identity --------------------

    @classmethod
    def from_verified_http(
        cls,
        verified: Any,
        *,
        project_id: str | None = None,
        entry_class: str = EntryClass.HTTP,
    ) -> CommandIdentity:
        """Project an authenticated HTTP request identity (B15).

        Args:
            verified: A
                :class:`~acp_services.workspace.http_identity.VerifiedRequestIdentity`.
                Only non-secret fields are read.
            project_id: The effective project of the command (usually the
                server-resolved submit project).  Defaults to the credential's
                verified project.
            entry_class: Entry label; HTTP for a REST call, MCP when a stdio
                proxy forwards an already-resolved identity.

        Raises:
            TypeError: *verified* is not a resolved request identity.
        """
        principal = getattr(verified, "principal", None)
        if not isinstance(principal, Principal):
            raise TypeError("from_verified_http requires a VerifiedRequestIdentity")
        resolved_project = _clean(project_id) or _clean(getattr(verified, "verified_project", None))
        kind = str(principal.kind or SubjectKind.USER)
        subject_kind = {
            SubjectKind.SERVICE: SubjectKind.SERVICE,
            SubjectKind.ANONYMOUS: SubjectKind.ANONYMOUS,
        }.get(kind, SubjectKind.USER)
        return cls(
            principal_id=principal.principal_id,
            project_id=resolved_project,
            key_id=_clean(getattr(verified, "key_id", None)),
            actions=tuple(str(action) for action in getattr(verified, "actions", ()) or ()),
            subject_kind=subject_kind,
            entry_class=entry_class,
            permission_scope=_clean(getattr(verified, "key_scope", None))
            or _clean(getattr(verified, "mode", None))
            or PermissionScope.API_KEY,
            tenant_id=_clean(principal.tenant_id),
            display_name=_clean(principal.display_name),
        )

    @classmethod
    def from_server(
        cls,
        server: ServerIdentity,
        *,
        project_id: str | None = None,
        entry_class: str = EntryClass.CONTROLLER,
        subject_kind: str | None = None,
        permission_scope: str | None = None,
    ) -> CommandIdentity:
        """Project the Controller-process identity (the trusted local path).

        The Controller resolves the principal itself, so ``LOCAL_PRINCIPAL_ID``
        is legitimate here — but it is recorded explicitly as
        :data:`SubjectKind.OPERATOR` unless the caller says otherwise.
        """
        if not isinstance(server, ServerIdentity):
            raise TypeError("from_server requires a ServerIdentity")
        principal = server.principal
        is_local = principal.principal_id == LOCAL_PRINCIPAL_ID
        return cls(
            principal_id=principal.principal_id,
            project_id=_clean(project_id) or _clean(server.verified_project),
            key_id=None,
            actions=tuple(tier_baseline_actions(principal.deployment_tier)),
            subject_kind=(
                subject_kind
                if subject_kind is not None
                else (SubjectKind.OPERATOR if is_local else str(principal.kind))
            ),
            entry_class=entry_class,
            permission_scope=(
                permission_scope if permission_scope is not None else PermissionScope.LOCAL_SINGLE
            ),
            tenant_id=_clean(principal.tenant_id),
            display_name=_clean(principal.display_name),
        )

    @classmethod
    def from_local(
        cls,
        *,
        principal_id: str = LOCAL_PRINCIPAL_ID,
        project_id: str | None = None,
        entry_class: str = EntryClass.CLI,
        key_id: str | None = None,
        actions: tuple[str, ...] | None = None,
        permission_scope: str = PermissionScope.LOCAL_OPERATOR,
        tenant_id: str | None = None,
    ) -> CommandIdentity:
        """Build the explicit local-operator identity (CLI local task manager).

        This is the only constructor that may *declare* ``LOCAL_PRINCIPAL_ID``
        without a prior server resolution.  It is deliberate: a local CLI
        operator is the deployment owner, not a remote caller.  ``subject_kind``
        is fixed to :data:`SubjectKind.OPERATOR` so the admission ledger and the
        idempotency scope can never mistake it for an authenticated multi-user
        principal.
        """
        return cls(
            principal_id=str(principal_id or "").strip() or LOCAL_PRINCIPAL_ID,
            project_id=_clean(project_id),
            key_id=_clean(key_id),
            actions=tuple(actions) if actions is not None else (),
            subject_kind=SubjectKind.OPERATOR,
            entry_class=entry_class,
            permission_scope=permission_scope,
            tenant_id=_clean(tenant_id),
        )

    # -- projections -------------------------------------------------------

    @property
    def is_operator(self) -> bool:
        return self.subject_kind == SubjectKind.OPERATOR

    @property
    def scope_origin(self) -> str:
        """The ledger origin of this scope: always ``verified`` (M0-04).

        It is :data:`acp_services.runtime.idempotency.SCOPE_ORIGIN_VERIFIED`;
        pre-M0-04 rows keep the ``legacy-*`` origins from the same vocabulary.
        The operator/authenticated distinction is carried by
        :attr:`principal_id`/:attr:`subject_kind`, not by a second origin value.
        """
        return SCOPE_ORIGIN_VERIFIED

    def allows(self, action: str) -> bool:
        """Whether the recorded ceiling covers *action* (empty = no ceiling)."""
        if not self.actions:
            return True
        return str(action) in self.actions

    def idempotency_scope(self, *, project_id: str | None = None) -> str:
        """Return ``resolve_scope_id(principal, project)`` for this identity.

        A different principal or a different project is a different scope; the
        persistent ledger then reports an explicit ``conflict`` instead of
        replaying another user's TaskRef.
        """
        project = _clean(project_id) or self.project_id or DEFAULT_COMMAND_PROJECT
        return resolve_scope_id(self.principal_id, project)

    def admission_projection(self) -> dict[str, Any]:
        """Non-secret projection for an admission result / ledger row."""
        return {
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "key_id": self.key_id,
            "subject_kind": self.subject_kind,
            "entry_class": self.entry_class,
            "scope_origin": self.scope_origin,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the auditable shape (never contains a credential)."""
        return {
            "schema_version": 1,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "key_id": self.key_id,
            "actions": list(self.actions),
            "subject_kind": self.subject_kind,
            "entry_class": self.entry_class,
            "permission_scope": self.permission_scope,
            "scope_origin": self.scope_origin,
            "is_operator": self.is_operator,
        }


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
