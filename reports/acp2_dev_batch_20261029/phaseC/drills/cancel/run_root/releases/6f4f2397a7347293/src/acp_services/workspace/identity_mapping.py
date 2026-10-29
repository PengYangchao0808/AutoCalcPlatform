"""External identity binding — LocalIdentity <-> OIDC subject mapping (B01-4 / todo 9).

The single implementation body for associating an *external* login identity with
the immutable Workspace ``principal_id``.  It is the executable form of the
identity-provider migration contract (main plan §26.4, MU-13; open-source reuse
assessment §3.1): an external identity is identified by a **verified
``issuer`` + ``subject`` pair** and nothing else.

Hard rules encoded here
-----------------------
* **Stable principal, stable association.**  ``principal_id`` is the immutable
  Workspace subject that keeps task/member/billing ownership.  Switching
  providers changes *login bindings only* — it never creates a replacement
  business user and never rewrites scientific hashes or execution history.
* **issuer + subject is the identity key.**  E-mail, user-name and the provider
  display name are **not** identity keys and are **never** used to merge two
  principals.  :class:`ExternalIdentity` carries ``email``/``display_name`` as
  non-authoritative hints only; a same-e-mail/different-subject attempt is
  *recorded as a conflict* and the two principals stay distinct.
* **One external maps to exactly one principal** (SQLite ``UNIQUE(issuer,
  subject)``); a principal may hold many externals.  Duplicate subjects,
  one-to-many (one external wanted by two principals) and already-occupied
  (historically bound to another principal) are typed conflicts, recorded and
  rejected — never silently merged or overwritten.
* **No privilege escalation.**  A binding carries no roles; an external identity
  that happens to share a role name (or is the provider's "admin") grants
  nothing.  Role assignment lives elsewhere (todo 73).
* **Rollback never revives a revoked credential.**  :meth:`rollback` restores
  only verified bindings that were *unbound*; revoked bindings stay revoked.
* **Local tier keeps ``issuer="local"``.**  :data:`LOCAL_ISSUER` is the local
  provider namespace; :func:`local_identity` builds a local-tier identity.

Explicitly out of scope (deferred, do NOT implement here)
---------------------------------------------------------
* No OIDC network login, redirect/callback, token validation or session
  handling.  Those belong to **B12 / OS-03** (plan todo 73, MU-13) and must
  build the *adapter* on top of this pure association contract.
* No account registration, invitation, session or API-key lifecycle.
* This module never touches sessions or credentials stored elsewhere.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
-----------------------------------------------------------------------------
``src/acp_services/**`` must never import the legacy ``acp`` package.  This
module is **stdlib only** (``sqlite3`` is the storage backend).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "LOCAL_ISSUER",
    "BindingState",
    "ConflictKind",
    "ExternalIdentity",
    "local_identity",
    "IdentityBinding",
    "RecordedConflict",
    "PrincipalFacts",
    "MigrationPreviewEntry",
    "MigrationPreview",
    "RollbackResult",
    "IdentityMappingError",
    "IdentityMappingConflictError",
    "DuplicateSubjectError",
    "OneToManyConflictError",
    "AlreadyOccupiedError",
    "CredentialRevokedError",
    "UnverifiedBindingError",
    "IdentityMappingStore",
    "migration_preview",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Provider namespace of the local deployment tier.  The local tier always
#: identifies its subject with this issuer, so a later OIDC provider (a distinct
#: issuer) never collides with a local identity.
LOCAL_ISSUER = "local"


class BindingState(str):
    """Lifecycle of one external-identity binding."""

    ACTIVE = "active"
    UNBOUND = "unbound"
    REVOKED = "revoked"
    ALL = frozenset({ACTIVE, UNBOUND, REVOKED})


class ConflictKind(str):
    """Deterministic conflict vocabulary (the conflict matrix cells)."""

    #: The exact (issuer, subject) is already actively bound -> rejected.
    DUPLICATE_SUBJECT = "duplicate_subject"
    #: One external wanted by a second principal while still active.
    ONE_TO_MANY = "one_to_many"
    #: External historically bound to another principal (unbound slot reuse).
    ALREADY_OCCUPIED = "already_occupied"
    #: The external was revoked; binding must not revive it.
    REVOKED = "revoked"
    #: Same e-mail, different subject/principal -> recorded, never merged.
    SAME_EMAIL_DISTINCT_SUBJECT = "same_email_distinct_subject"
    ALL = frozenset(
        {
            DUPLICATE_SUBJECT,
            ONE_TO_MANY,
            ALREADY_OCCUPIED,
            REVOKED,
            SAME_EMAIL_DISTINCT_SUBJECT,
        }
    )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IdentityMappingError(Exception):
    """Base class for identity-mapping contract violations."""


class IdentityMappingConflictError(IdentityMappingError):
    """A binding conflict; carries the :class:`ConflictKind` that fired."""

    def __init__(self, message: str, *, kind: str, conflict: RecordedConflict | None = None):
        super().__init__(message)
        self.kind = kind
        self.conflict = conflict


class DuplicateSubjectError(IdentityMappingConflictError):
    """The exact (issuer, subject) is already actively bound."""

    def __init__(self, message: str, *, conflict: RecordedConflict | None = None):
        super().__init__(message, kind=ConflictKind.DUPLICATE_SUBJECT, conflict=conflict)


class OneToManyConflictError(IdentityMappingConflictError):
    """An active external cannot be bound to a second principal."""

    def __init__(self, message: str, *, conflict: RecordedConflict | None = None):
        super().__init__(message, kind=ConflictKind.ONE_TO_MANY, conflict=conflict)


class AlreadyOccupiedError(IdentityMappingConflictError):
    """The external historically belonged to another principal (unbound reuse)."""

    def __init__(self, message: str, *, conflict: RecordedConflict | None = None):
        super().__init__(message, kind=ConflictKind.ALREADY_OCCUPIED, conflict=conflict)


class CredentialRevokedError(IdentityMappingConflictError):
    """The external was revoked and must not be revived by an implicit path."""

    def __init__(self, message: str, *, conflict: RecordedConflict | None = None):
        super().__init__(message, kind=ConflictKind.REVOKED, conflict=conflict)


class UnverifiedBindingError(IdentityMappingError):
    """An unverified binding cannot be restored/rolled back to active."""


# ---------------------------------------------------------------------------
# Value shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExternalIdentity:
    """A verified external identity: ``issuer`` + ``subject``.

    ``email`` and ``display_name`` are **non-authoritative hints** kept for the
    migration preview and diagnostics only.  Neither is an identity key: two
    externals that share an e-mail are *not* merged (see
    :class:`IdentityMappingStore`).
    """

    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.issuer, self.subject)

    @property
    def local(self) -> bool:
        return self.issuer == LOCAL_ISSUER

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExternalIdentity:
        return cls(
            issuer=str(data["issuer"]),
            subject=str(data["subject"]),
            email=(None if data.get("email") is None else str(data["email"])),
            display_name=(None if data.get("display_name") is None else str(data["display_name"])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "email": self.email,
            "display_name": self.display_name,
        }


def local_identity(
    subject: str,
    *,
    email: str | None = None,
    display_name: str | None = None,
) -> ExternalIdentity:
    """Build a local-tier identity (``issuer=`` :data:`LOCAL_ISSUER`)."""
    return ExternalIdentity(
        issuer=LOCAL_ISSUER, subject=str(subject), email=email, display_name=display_name
    )


@dataclass(frozen=True)
class IdentityBinding:
    """One persisted association between ``principal_id`` and an external.

    Deliberately carries **no** roles/actions: a binding associates a login, it
    never confers authority (no privilege escalation by external identity).
    """

    binding_id: str
    principal_id: str
    issuer: str
    subject: str
    state: str
    verified: bool
    email: str | None = None
    display_name: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def external(self) -> ExternalIdentity:
        return ExternalIdentity(
            issuer=self.issuer,
            subject=self.subject,
            email=self.email,
            display_name=self.display_name,
        )

    @property
    def active(self) -> bool:
        return self.state == BindingState.ACTIVE

    @property
    def revoked(self) -> bool:
        return self.state == BindingState.REVOKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "principal_id": self.principal_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "state": self.state,
            "verified": self.verified,
            "email": self.email,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class RecordedConflict:
    """A persisted conflict observation (audit trail; nothing is overwritten)."""

    conflict_id: str
    kind: str
    issuer: str
    subject: str
    existing_principal_id: str | None
    attempted_principal_id: str | None
    detail: str
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "kind": self.kind,
            "issuer": self.issuer,
            "subject": self.subject,
            "existing_principal_id": self.existing_principal_id,
            "attempted_principal_id": self.attempted_principal_id,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class PrincipalFacts:
    """Non-authoritative facts used only to *preview* migration impact.

    These are read-only projections supplied by the caller; this module never
    writes them and never derives authority from them.
    """

    principal_id: str
    project_ids: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrincipalFacts:
        return cls(
            principal_id=str(data["principal_id"]),
            project_ids=_as_tuple(data.get("project_ids")),
            roles=_as_tuple(data.get("roles")),
            account_ids=_as_tuple(data.get("account_ids")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "project_ids": list(self.project_ids),
            "roles": list(self.roles),
            "account_ids": list(self.account_ids),
        }


@dataclass(frozen=True)
class MigrationPreviewEntry:
    """One proposed binding evaluated in-memory (dry-run; never persisted)."""

    principal_id: str
    issuer: str
    subject: str
    status: str
    conflict_kind: str | None
    affected_projects: tuple[str, ...] = ()
    affected_roles: tuple[str, ...] = ()
    affected_accounts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "status": self.status,
            "conflict_kind": self.conflict_kind,
            "affected_projects": list(self.affected_projects),
            "affected_roles": list(self.affected_roles),
            "affected_accounts": list(self.affected_accounts),
        }


@dataclass(frozen=True)
class MigrationPreview:
    """Byte-stable dry-run projection of a migration proposal set.

    Contains no timestamp: the same store state + proposals always serialise to
    identical bytes, so ``digest`` is reproducible across calls.
    """

    entries: tuple[MigrationPreviewEntry, ...]
    conflicts: tuple[RecordedConflict, ...]
    digest: str
    dry_run: bool = True

    @property
    def ready(self) -> tuple[MigrationPreviewEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == "ready")

    @property
    def conflicting(self) -> tuple[MigrationPreviewEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == "conflict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "entries": [entry.to_dict() for entry in self.entries],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "digest": self.digest,
        }


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of :meth:`IdentityMappingStore.rollback`."""

    restored: int
    skipped_unverified: int
    skipped_revoked: int
    restored_bindings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "restored": self.restored,
            "skipped_unverified": self.skipped_unverified,
            "skipped_revoked": self.skipped_revoked,
            "restored_bindings": list(self.restored_bindings),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _binding_id(issuer: str, subject: str) -> str:
    material = f"{issuer}\x00{subject}".encode()
    return "idb_" + hashlib.sha256(material).hexdigest()[:24]


def _normalize_facts(
    facts: Mapping[str, PrincipalFacts] | Iterable[PrincipalFacts] | None,
) -> dict[str, PrincipalFacts]:
    if facts is None:
        return {}
    if isinstance(facts, Mapping):
        return {str(key): value for key, value in facts.items()}
    return {item.principal_id: item for item in facts}


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


_CREATE_BINDINGS_SQL = """
CREATE TABLE IF NOT EXISTS identity_bindings (
    binding_id      TEXT PRIMARY KEY,
    principal_id    TEXT NOT NULL,
    issuer          TEXT NOT NULL,
    subject         TEXT NOT NULL,
    email           TEXT,
    display_name    TEXT,
    state           TEXT NOT NULL,
    verified        INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (issuer, subject)
)
"""

_CREATE_CONFLICTS_SQL = """
CREATE TABLE IF NOT EXISTS identity_conflicts (
    conflict_id            TEXT PRIMARY KEY,
    kind                   TEXT NOT NULL,
    issuer                 TEXT NOT NULL,
    subject                TEXT NOT NULL,
    existing_principal_id  TEXT,
    attempted_principal_id TEXT,
    detail                 TEXT NOT NULL,
    recorded_at            TEXT NOT NULL
)
"""


def _row_to_binding(row: sqlite3.Row) -> IdentityBinding:
    return IdentityBinding(
        binding_id=row["binding_id"],
        principal_id=row["principal_id"],
        issuer=row["issuer"],
        subject=row["subject"],
        state=row["state"],
        verified=bool(row["verified"]),
        email=row["email"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_conflict(row: sqlite3.Row) -> RecordedConflict:
    return RecordedConflict(
        conflict_id=row["conflict_id"],
        kind=row["kind"],
        issuer=row["issuer"],
        subject=row["subject"],
        existing_principal_id=row["existing_principal_id"],
        attempted_principal_id=row["attempted_principal_id"],
        detail=row["detail"],
        recorded_at=row["recorded_at"],
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class IdentityMappingStore:
    """SQLite-backed external-identity binding store (stdlib only).

    Args:
        db_path: Explicit path to the SQLite database; the two tables are
            created idempotently.  The caller owns the database location.
        clock: Optional ``() -> datetime`` override for deterministic stamping.

    The store never talks to an identity provider; it only persists the
    *verified* association the caller already established (offline contract).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- schema / connection ------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_CREATE_BINDINGS_SQL)
                conn.execute(_CREATE_CONFLICTS_SQL)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self, now: datetime | None) -> str:
        return _coerce_now(now if now is not None else self._clock()).isoformat()

    @staticmethod
    def _fetch(conn: sqlite3.Connection, issuer: str, subject: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM identity_bindings WHERE issuer=? AND subject=?",
            (issuer, subject),
        ).fetchone()

    def _record_conflict(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        issuer: str,
        subject: str,
        existing_principal_id: str | None,
        attempted_principal_id: str | None,
        detail: str,
        now: datetime | None,
    ) -> RecordedConflict:
        conflict = RecordedConflict(
            conflict_id="idc_" + uuid.uuid4().hex,
            kind=kind,
            issuer=issuer,
            subject=subject,
            existing_principal_id=existing_principal_id,
            attempted_principal_id=attempted_principal_id,
            detail=detail,
            recorded_at=self._now(now),
        )
        conn.execute(
            """
            INSERT INTO identity_conflicts (
                conflict_id, kind, issuer, subject, existing_principal_id,
                attempted_principal_id, detail, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conflict.conflict_id,
                conflict.kind,
                conflict.issuer,
                conflict.subject,
                conflict.existing_principal_id,
                conflict.attempted_principal_id,
                conflict.detail,
                conflict.recorded_at,
            ),
        )
        logger.warning(
            "identity conflict kind=%s issuer=%s subject=%s attempted=%s existing=%s",
            kind,
            issuer,
            subject,
            attempted_principal_id,
            existing_principal_id,
        )
        return conflict

    # -- bind / unbind ------------------------------------------------------

    def bind(
        self,
        principal_id: str,
        external: ExternalIdentity,
        *,
        verified: bool = True,
        now: datetime | None = None,
    ) -> IdentityBinding:
        """Bind *external* to *principal_id*.

        Raises:
            DuplicateSubjectError: the exact external is already active.
            OneToManyConflictError: the active external belongs to another
                principal (or a batch wants it twice).
            AlreadyOccupiedError: the external historically belonged to another
                principal (unbound slot reuse).
            CredentialRevokedError: the external was revoked; binding must not
                revive it.
            ValueError: empty principal id or empty issuer/subject.
        """
        principal_id = str(principal_id)
        if not principal_id:
            raise ValueError("principal_id is required")
        issuer = str(external.issuer)
        subject = str(external.subject)
        if not issuer or not subject:
            raise ValueError("external identity requires a non-empty issuer and subject")

        with self._lock:
            conn = self._connect()
            try:
                existing = self._fetch(conn, issuer, subject)
                timestamp = self._now(now)

                if existing is not None:
                    row = _row_to_binding(existing)
                    if row.state == BindingState.REVOKED:
                        conflict = self._record_conflict(
                            conn,
                            kind=ConflictKind.REVOKED,
                            issuer=issuer,
                            subject=subject,
                            existing_principal_id=row.principal_id,
                            attempted_principal_id=principal_id,
                            detail="revoked external identity cannot be re-bound",
                            now=now,
                        )
                        conn.commit()
                        raise CredentialRevokedError(
                            f"external ({issuer!r}, {subject!r}) is revoked", conflict=conflict
                        )
                    if row.state == BindingState.ACTIVE and row.principal_id == principal_id:
                        conflict = self._record_conflict(
                            conn,
                            kind=ConflictKind.DUPLICATE_SUBJECT,
                            issuer=issuer,
                            subject=subject,
                            existing_principal_id=row.principal_id,
                            attempted_principal_id=principal_id,
                            detail="duplicate subject already bound to this principal",
                            now=now,
                        )
                        conn.commit()
                        raise DuplicateSubjectError(
                            f"external ({issuer!r}, {subject!r}) already bound", conflict=conflict
                        )
                    if row.state == BindingState.ACTIVE:
                        conflict = self._record_conflict(
                            conn,
                            kind=ConflictKind.ONE_TO_MANY,
                            issuer=issuer,
                            subject=subject,
                            existing_principal_id=row.principal_id,
                            attempted_principal_id=principal_id,
                            detail="active external cannot be bound to a second principal",
                            now=now,
                        )
                        conn.commit()
                        raise OneToManyConflictError(
                            f"external ({issuer!r}, {subject!r}) is already active for "
                            f"principal {row.principal_id!r}",
                            conflict=conflict,
                        )
                    if row.principal_id != principal_id:
                        conflict = self._record_conflict(
                            conn,
                            kind=ConflictKind.ALREADY_OCCUPIED,
                            issuer=issuer,
                            subject=subject,
                            existing_principal_id=row.principal_id,
                            attempted_principal_id=principal_id,
                            detail="external historically bound to another principal",
                            now=now,
                        )
                        conn.commit()
                        raise AlreadyOccupiedError(
                            f"external ({issuer!r}, {subject!r}) was bound to principal "
                            f"{row.principal_id!r}",
                            conflict=conflict,
                        )
                    # Same principal, unbound -> reactivate (restore semantics).
                    conn.execute(
                        """
                        UPDATE identity_bindings
                        SET state=?, verified=?, email=COALESCE(?, email),
                            display_name=COALESCE(?, display_name), updated_at=?
                        WHERE binding_id=?
                        """,
                        (
                            BindingState.ACTIVE,
                            1 if verified else int(row.verified),
                            external.email,
                            external.display_name,
                            timestamp,
                            row.binding_id,
                        ),
                    )
                    conn.commit()
                    return self._get_binding_by_id(conn, row.binding_id)

                conn.execute(
                    """
                    INSERT INTO identity_bindings (
                        binding_id, principal_id, issuer, subject, email, display_name,
                        state, verified, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _binding_id(issuer, subject),
                        principal_id,
                        issuer,
                        subject,
                        external.email,
                        external.display_name,
                        BindingState.ACTIVE,
                        1 if verified else 0,
                        timestamp,
                        timestamp,
                    ),
                )
                self._record_email_conflicts(
                    conn,
                    principal_id=principal_id,
                    external=external,
                    now=now,
                )
                conn.commit()
            finally:
                conn.close()

        logger.info(
            "identity bound principal=%s issuer=%s subject=%s", principal_id, issuer, subject
        )
        return self.get_binding(external)  # type: ignore[return-value]

    def _record_email_conflicts(
        self,
        conn: sqlite3.Connection,
        *,
        principal_id: str,
        external: ExternalIdentity,
        now: datetime | None,
    ) -> None:
        """Record (never merge) same-e-mail/different-subject/principal clashes."""
        if not external.email:
            return
        rows = conn.execute(
            """
            SELECT issuer, subject, principal_id FROM identity_bindings
            WHERE state=? AND email=? AND NOT (issuer=? AND subject=?)
            """,
            (BindingState.ACTIVE, external.email, external.issuer, external.subject),
        ).fetchall()
        for row in rows:
            if row["principal_id"] == principal_id:
                continue
            self._record_conflict(
                conn,
                kind=ConflictKind.SAME_EMAIL_DISTINCT_SUBJECT,
                issuer=str(external.issuer),
                subject=str(external.subject),
                existing_principal_id=str(row["principal_id"]),
                attempted_principal_id=principal_id,
                detail=(
                    f"same email {external.email!r} as ({row['issuer']!r}, "
                    f"{row['subject']!r}); not merged"
                ),
                now=now,
            )

    def unbind(self, external: ExternalIdentity, *, now: datetime | None = None) -> bool:
        """Soft-unbind an active external (restorable).  ``False`` if not active."""
        with self._lock:
            conn = self._connect()
            try:
                existing = self._fetch(conn, external.issuer, external.subject)
                if existing is None or existing["state"] != BindingState.ACTIVE:
                    return False
                conn.execute(
                    "UPDATE identity_bindings SET state=?, updated_at=? WHERE binding_id=?",
                    (BindingState.UNBOUND, self._now(now), existing["binding_id"]),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def revoke(self, external: ExternalIdentity, *, now: datetime | None = None) -> bool:
        """Revoke a binding (idempotent).  A revoked credential is never revived."""
        with self._lock:
            conn = self._connect()
            try:
                existing = self._fetch(conn, external.issuer, external.subject)
                if existing is None or existing["state"] == BindingState.REVOKED:
                    return False
                conn.execute(
                    "UPDATE identity_bindings SET state=?, updated_at=? WHERE binding_id=?",
                    (BindingState.REVOKED, self._now(now), existing["binding_id"]),
                )
                conn.commit()
                logger.info(
                    "identity revoked issuer=%s subject=%s",
                    external.issuer,
                    external.subject,
                )
                return True
            finally:
                conn.close()

    def restore(self, external: ExternalIdentity, *, now: datetime | None = None) -> bool:
        """Restore a single **unbound, verified** binding to active.

        Raises:
            CredentialRevokedError: a revoked external is never revived here.
            UnverifiedBindingError: an unverified binding cannot be restored.
        """
        with self._lock:
            conn = self._connect()
            try:
                existing = self._fetch(conn, external.issuer, external.subject)
                if existing is None:
                    return False
                state = existing["state"]
                if state == BindingState.REVOKED:
                    conflict = self._record_conflict(
                        conn,
                        kind=ConflictKind.REVOKED,
                        issuer=external.issuer,
                        subject=external.subject,
                        existing_principal_id=existing["principal_id"],
                        attempted_principal_id=existing["principal_id"],
                        detail="restore refused: credential is revoked",
                        now=now,
                    )
                    conn.commit()
                    raise CredentialRevokedError(
                        "revoked credential is not revived", conflict=conflict
                    )
                if state == BindingState.ACTIVE:
                    return False
                if not bool(existing["verified"]):
                    raise UnverifiedBindingError(
                        f"binding {existing['binding_id']!r} is not verified"
                    )
                conn.execute(
                    "UPDATE identity_bindings SET state=?, updated_at=? WHERE binding_id=?",
                    (BindingState.ACTIVE, self._now(now), existing["binding_id"]),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def rollback(
        self,
        binding_ids: Iterable[str] | None = None,
        *,
        now: datetime | None = None,
    ) -> RollbackResult:
        """Bulk-restore verified bindings; never revive revoked credentials.

        With no ``binding_ids`` every non-active binding is considered.  Only
        ``unbound`` **and** ``verified`` rows become active again; ``revoked``
        rows are counted in ``skipped_revoked`` and left untouched.
        """
        with self._lock:
            conn = self._connect()
            try:
                if binding_ids is None:
                    rows = conn.execute(
                        "SELECT * FROM identity_bindings WHERE state<>?",
                        (BindingState.ACTIVE,),
                    ).fetchall()
                else:
                    ids = [str(item) for item in binding_ids]
                    if not ids:
                        return RollbackResult(0, 0, 0, ())
                    placeholders = ",".join("?" for _ in ids)
                    rows = conn.execute(
                        f"SELECT * FROM identity_bindings WHERE binding_id IN ({placeholders})",
                        ids,
                    ).fetchall()

                restored: list[str] = []
                skipped_unverified = 0
                skipped_revoked = 0
                timestamp = self._now(now)
                for row in rows:
                    if row["state"] == BindingState.REVOKED:
                        skipped_revoked += 1
                        continue
                    if row["state"] == BindingState.ACTIVE:
                        continue
                    if not bool(row["verified"]):
                        skipped_unverified += 1
                        continue
                    conn.execute(
                        "UPDATE identity_bindings SET state=?, updated_at=? WHERE binding_id=?",
                        (BindingState.ACTIVE, timestamp, row["binding_id"]),
                    )
                    restored.append(str(row["binding_id"]))
                conn.commit()
            finally:
                conn.close()

        if restored:
            logger.info("identity rollback restored=%d", len(restored))
        return RollbackResult(
            restored=len(restored),
            skipped_unverified=skipped_unverified,
            skipped_revoked=skipped_revoked,
            restored_bindings=tuple(restored),
        )

    # -- reads --------------------------------------------------------------

    def _get_binding_by_id(self, conn: sqlite3.Connection, binding_id: str) -> IdentityBinding:
        row = conn.execute(
            "SELECT * FROM identity_bindings WHERE binding_id=?", (binding_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - guarded by callers
            raise IdentityMappingError(f"binding {binding_id!r} vanished")
        return _row_to_binding(row)

    def get_binding(self, external: ExternalIdentity) -> IdentityBinding | None:
        with self._lock:
            conn = self._connect()
            try:
                row = self._fetch(conn, external.issuer, external.subject)
                return _row_to_binding(row) if row is not None else None
            finally:
                conn.close()

    def resolve_principal(self, external: ExternalIdentity) -> str | None:
        """Return the principal an **active** external maps to (else ``None``)."""
        binding = self.get_binding(external)
        if binding is None or not binding.active:
            return None
        return binding.principal_id

    def list_bindings(self, principal_id: str | None = None) -> list[IdentityBinding]:
        with self._lock:
            conn = self._connect()
            try:
                if principal_id is None:
                    rows = conn.execute(
                        "SELECT * FROM identity_bindings ORDER BY issuer, subject"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM identity_bindings WHERE principal_id=? "
                        "ORDER BY issuer, subject",
                        (str(principal_id),),
                    ).fetchall()
                return [_row_to_binding(row) for row in rows]
            finally:
                conn.close()

    def list_conflicts(self) -> list[RecordedConflict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM identity_conflicts ORDER BY recorded_at, kind, issuer, subject"
                ).fetchall()
                return [_row_to_conflict(row) for row in rows]
            finally:
                conn.close()

    def conflicts_of_kind(self, kind: str) -> list[RecordedConflict]:
        return [conflict for conflict in self.list_conflicts() if conflict.kind == kind]

    # -- dry-run migration preview -----------------------------------------

    def migration_preview(
        self,
        proposals: Iterable[tuple[str, ExternalIdentity]],
        *,
        principal_facts: Mapping[str, PrincipalFacts] | Iterable[PrincipalFacts] | None = None,
    ) -> MigrationPreview:
        """Delegate to :func:`migration_preview` (dry-run, zero side effects)."""
        return migration_preview(self, proposals, principal_facts=principal_facts)


# ---------------------------------------------------------------------------
# Migration preview (dry-run)
# ---------------------------------------------------------------------------


def _effective_existing(
    conn: sqlite3.Connection,
    issuer: str,
    subject: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM identity_bindings WHERE issuer=? AND subject=?",
        (issuer, subject),
    ).fetchone()


def _preview_conflict_kind(
    existing_row: sqlite3.Row | None,
    *,
    principal_id: str,
) -> str | None:
    if existing_row is None:
        return None
    state = existing_row["state"]
    owner = existing_row["principal_id"]
    if state == BindingState.REVOKED:
        return ConflictKind.REVOKED
    if state == BindingState.ACTIVE and owner == principal_id:
        return ConflictKind.DUPLICATE_SUBJECT
    if state == BindingState.ACTIVE:
        return ConflictKind.ONE_TO_MANY
    # unbound
    if owner != principal_id:
        return ConflictKind.ALREADY_OCCUPIED
    return None


def migration_preview(
    store: IdentityMappingStore,
    proposals: Iterable[tuple[str, ExternalIdentity]],
    *,
    principal_facts: Mapping[str, PrincipalFacts] | Iterable[PrincipalFacts] | None = None,
) -> MigrationPreview:
    """Compute a **dry-run** migration preview; never writes to the store.

    Args:
        store: The binding store to preview against (read-only here).
        proposals: ``(principal_id, external)`` pairs describing the intended
            bindings.
        principal_facts: Optional read-only facts used to report affected
            projects/roles/accounts per principal.

    Returns:
        A :class:`MigrationPreview` whose ``to_dict()`` is byte-stable across
        calls for identical inputs (sorted entries, canonical JSON digest, no
        timestamps).
    """
    facts = _normalize_facts(principal_facts)
    proposal_list = [(str(pid), ext) for pid, ext in proposals]

    # Read-only snapshot of active email owners (hint map; never authoritative).
    active_emails: dict[str, list[tuple[str, str, str]]] = {}
    with store._lock:  # noqa: SLF001 - same-package internal read snapshot
        conn = store._connect()  # noqa: SLF001
        try:
            for row in conn.execute(
                "SELECT issuer, subject, principal_id, email FROM identity_bindings "
                "WHERE state=? AND email IS NOT NULL",
                (BindingState.ACTIVE,),
            ).fetchall():
                active_emails.setdefault(str(row["email"]), []).append(
                    (str(row["issuer"]), str(row["subject"]), str(row["principal_id"]))
                )
            existing_rows = {
                (str(row["issuer"]), str(row["subject"])): row
                for row in conn.execute("SELECT * FROM identity_bindings").fetchall()
            }
        finally:
            conn.close()

    entries: list[MigrationPreviewEntry] = []
    conflicts: list[RecordedConflict] = []
    proposed: dict[tuple[str, str], str] = {}

    for principal_id, external in proposal_list:
        key = (str(external.issuer), str(external.subject))

        # Intra-batch conflict: an earlier proposal already claimed this external.
        kind: str | None = None
        owner_before: str | None = None
        if key in proposed:
            owner_before = proposed[key]
            if owner_before == principal_id:
                kind = ConflictKind.DUPLICATE_SUBJECT
            else:
                kind = ConflictKind.ONE_TO_MANY
        else:
            existing_row = existing_rows.get(key)
            kind = _preview_conflict_kind(existing_row, principal_id=principal_id)
            owner_before = str(existing_row["principal_id"]) if existing_row is not None else None

        # Same-email/different-subject/different-principal hint (soft conflict).
        if external.email:
            for issuer, subject, owner in active_emails.get(str(external.email), []):
                if (issuer, subject) == key or owner == principal_id:
                    continue
                conflicts.append(
                    RecordedConflict(
                        conflict_id="idc_preview_"
                        + _digest(
                            [
                                ConflictKind.SAME_EMAIL_DISTINCT_SUBJECT,
                                issuer,
                                subject,
                                owner,
                                principal_id,
                            ]
                        )[:16],
                        kind=ConflictKind.SAME_EMAIL_DISTINCT_SUBJECT,
                        issuer=str(external.issuer),
                        subject=str(external.subject),
                        existing_principal_id=owner,
                        attempted_principal_id=principal_id,
                        detail=f"same email {external.email!r} as ({issuer!r}, {subject!r})",
                        recorded_at="",
                    )
                )

        if kind is not None:
            conflicts.append(
                RecordedConflict(
                    conflict_id="idc_preview_" + _digest([kind, key[0], key[1], principal_id])[:16],
                    kind=kind,
                    issuer=key[0],
                    subject=key[1],
                    existing_principal_id=owner_before,
                    attempted_principal_id=principal_id,
                    detail="conflict detected during dry-run",
                    recorded_at="",
                )
            )

        facts_for = facts.get(principal_id)
        entries.append(
            MigrationPreviewEntry(
                principal_id=principal_id,
                issuer=key[0],
                subject=key[1],
                status="conflict" if kind is not None else "ready",
                conflict_kind=kind,
                affected_projects=facts_for.project_ids if facts_for else (),
                affected_roles=facts_for.roles if facts_for else (),
                affected_accounts=facts_for.account_ids if facts_for else (),
            )
        )
        proposed.setdefault(key, principal_id)

    entries.sort(key=lambda e: (e.issuer, e.subject, e.principal_id))
    conflicts.sort(key=lambda c: (c.kind, c.issuer, c.subject, c.attempted_principal_id or ""))
    payload = {
        "dry_run": True,
        "entries": [entry.to_dict() for entry in entries],
        "conflicts": [conflict.to_dict() for conflict in conflicts],
    }
    return MigrationPreview(
        entries=tuple(entries),
        conflicts=tuple(conflicts),
        digest=_digest(payload),
        dry_run=True,
    )
