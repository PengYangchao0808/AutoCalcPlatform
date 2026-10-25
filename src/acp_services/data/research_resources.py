"""Versioned JSON research resources in the Data project-resource boundary (B18).

This is the **Data** half of the C18 handoff: create / read / compare-and-swap
for C's ``Proposal`` and ``ResearchRecord`` documents.  It deliberately stores
opaque JSON; it does not decide a research decision, does not interpret a
``decision`` enum and creates no task table.  Task state stays in the Runtime
store.

Properties enforced here:

* one stable ``resource_id`` per project-scoped document;
* monotonically increasing ``revision`` with an optimistic ``expected_revision``
  CAS (a stale revision is a named conflict carrying the current record);
* a content ``digest`` recomputed from the stored bytes on every read;
* an authorization projection (principal/project/scope) checked before the
  body is returned; a cross-project access is refused;
* revocation is terminal for reads and writes; the old body is never served;
* round-trip preserves the exact typed JSON document, revision and digest.

Isolation: ``src/acp_services/**`` never imports ``acp``.  This module imports
stdlib only and has no Runtime/Workspace dependency.

Author: ACP Services (B lane, B18).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "PROPOSAL_SCHEMA",
    "RESEARCH_RECORD_SCHEMA",
    "RESEARCH_RESOURCE_SCHEMA",
    "SUPPORTED_RESEARCH_SCHEMAS",
    "ResearchResourceAuthorization",
    "ResearchResourceAuthorizationError",
    "ResearchResourceConflictError",
    "ResearchResourceError",
    "ResearchResourceNotFoundError",
    "ResearchResourceRecord",
    "ResearchResourceRequest",
    "ResearchResourceRevokedError",
    "ResearchResourceStore",
    "ResearchResourceValidationError",
    "research_resource_digest",
    "research_resource_key",
    "resource_id_for",
]

#: Frozen wire schema of the stored resource envelope.
RESEARCH_RESOURCE_SCHEMA = "acp2.data.research-resource.v1"
#: C's published document schema ids.
PROPOSAL_SCHEMA = "acp2.experience.proposal.v1"
RESEARCH_RECORD_SCHEMA = "acp2.experience.research-record.v1"
SUPPORTED_RESEARCH_SCHEMAS: frozenset[str] = frozenset({PROPOSAL_SCHEMA, RESEARCH_RECORD_SCHEMA})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ID_PREFIX = "rr_"
_RESOURCE_KEY_PREFIX = "rrk_"
_RESEARCH_RESOURCE_KINDS: frozenset[str] = frozenset({"proposal", "record"})
_SCHEMA_TO_KIND: dict[str, str] = {
    PROPOSAL_SCHEMA: "proposal",
    RESEARCH_RECORD_SCHEMA: "record",
}
_DOCUMENT_ID_FIELD: dict[str, str] = {
    "proposal": "proposal_id",
    "record": "research_id",
}


# ---------------------------------------------------------------------------
# Errors (every refusal is named)
# ---------------------------------------------------------------------------


class ResearchResourceError(RuntimeError):
    """Base error for the research-resource store."""

    code = "research_resource_error"


class ResearchResourceValidationError(ResearchResourceError):
    """The document or request violates the frozen resource contract."""

    code = "invalid_research_resource"


class ResearchResourceNotFoundError(ResearchResourceError):
    """No such resource id (or revision) exists for this project."""

    code = "research_resource_not_found"


class ResearchResourceConflictError(ResearchResourceError):
    """An optimistic-concurrency or create conflict; carries the current record."""

    code = "revision_conflict"

    def __init__(self, message: str, current: ResearchResourceRecord | None) -> None:
        super().__init__(message)
        self.current = current
        self.current_revision = None if current is None else current.revision
        self.current_digest = None if current is None else current.digest


class ResearchResourceAuthorizationError(ResearchResourceError):
    """The principal/project may not read or mutate this resource."""

    code = "permission_denied"


class ResearchResourceRevokedError(ResearchResourceError):
    """The resource was revoked; neither body nor revision is served."""

    code = "research_resource_revoked"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchResourceAuthorization:
    """The inert authorization projection frozen onto one revision."""

    principal_id: str
    project_id: str
    scope: str = "project"
    action: str = "read"
    allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "scope": self.scope,
            "action": self.action,
            "allowed": bool(self.allowed),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ResearchResourceAuthorization:
        data = payload or {}
        return cls(
            principal_id=str(data.get("principal_id") or ""),
            project_id=str(data.get("project_id") or ""),
            scope=str(data.get("scope") or "project"),
            action=str(data.get("action") or "read"),
            allowed=bool(data.get("allowed", True)),
        )


@dataclass(frozen=True, slots=True)
class ResearchResourceRequest:
    """A create/CAS request (the document is opaque JSON)."""

    resource_id: str
    schema: str
    document: Mapping[str, Any]
    principal_id: str
    project_id: str
    expected_revision: int | None = None
    scope: str = "project"


@dataclass(frozen=True, slots=True)
class ResearchResourceRecord:
    """One persisted revision (the body is returned exactly as stored)."""

    resource_id: str
    schema: str
    revision: int
    digest: str
    document: Mapping[str, Any]
    project_id: str
    principal_id: str
    authorization: ResearchResourceAuthorization
    created_at: str
    updated_at: str
    revoked_at: str | None = None
    revocation_reason: str = ""

    def to_dict(self, *, include_document: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": RESEARCH_RESOURCE_SCHEMA,
            "resource_id": self.resource_id,
            "document_schema": self.schema,
            "revision": self.revision,
            "digest": self.digest,
            "project_id": self.project_id,
            "principal_id": self.principal_id,
            "authorization": self.authorization.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revoked_at": self.revoked_at,
            "revocation_reason": self.revocation_reason,
        }
        if include_document:
            payload["document"] = dict(self.document)
        return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchResourceValidationError(f"document is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def research_resource_digest(document: Mapping[str, Any]) -> str:
    """Content digest of a research resource document (not ``plan_digest``)."""
    return "sha256:" + hashlib.sha256(_canonical_bytes(document)).hexdigest()


def resource_id_for(*, project_id: str, schema: str, document: Mapping[str, Any]) -> str:
    """Deterministic id for a document created without an explicit id."""
    seed = f"{project_id}\0{schema}\0{research_resource_digest(document)}".encode()
    return _ID_PREFIX + hashlib.sha256(seed).hexdigest()[:24]


def research_resource_key(project_id: str, kind: str, document_id: str) -> str:
    """Stable, unambiguous project-scoped key for one research document.

    ``kind`` is ``"proposal"`` or ``"record"``.  The identity is a JSON array so
    a colon inside ``project_id`` or ``document_id`` can never collide with the
    field separators; the key is ``"rrk_"`` plus 48 hex characters (52 total).
    """
    kind_value = str(kind)
    if kind_value not in _RESEARCH_RESOURCE_KINDS:
        raise ResearchResourceValidationError(
            f"unsupported research resource kind {kind!r}; "
            f"supported={sorted(_RESEARCH_RESOURCE_KINDS)}"
        )
    project_value = str(project_id)
    document_value = str(document_id)
    if not project_value.strip():
        raise ResearchResourceValidationError("project_id is required")
    if not document_value.strip():
        raise ResearchResourceValidationError("document_id is required")
    identity = json.dumps(
        [project_value, kind_value, document_value],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _RESOURCE_KEY_PREFIX + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:48]


def _kind_for_schema(schema: str) -> str:
    kind = _SCHEMA_TO_KIND.get(schema)
    if kind is None:
        raise ResearchResourceValidationError(
            f"unsupported research schema {schema!r}; "
            f"supported={sorted(SUPPORTED_RESEARCH_SCHEMAS)}"
        )
    return kind


def _default_resource_key(request: ResearchResourceRequest) -> str:
    kind = _kind_for_schema(request.schema)
    document_id = request.document.get(_DOCUMENT_ID_FIELD[kind])
    if document_id is None or not str(document_id).strip():
        raise ResearchResourceValidationError(
            f"document.{_DOCUMENT_ID_FIELD[kind]} is required to derive a resource id"
        )
    return research_resource_key(request.project_id, kind, str(document_id))


def _validate_request(request: ResearchResourceRequest) -> ResearchResourceAuthorization:
    if not isinstance(request.document, Mapping):
        raise ResearchResourceValidationError("document must be a JSON object")
    if request.schema not in SUPPORTED_RESEARCH_SCHEMAS:
        raise ResearchResourceValidationError(
            f"unsupported research schema {request.schema!r}; "
            f"supported={sorted(SUPPORTED_RESEARCH_SCHEMAS)}"
        )
    declared = request.document.get("schema")
    if declared is not None and declared != request.schema:
        raise ResearchResourceValidationError(
            f"document.schema {declared!r} != request.schema {request.schema!r}"
        )
    document_project = str(request.document.get("project_id") or "")
    if document_project != request.project_id:
        raise ResearchResourceValidationError(
            "document.project_id must equal the authorized project"
        )
    if not str(request.principal_id).strip():
        raise ResearchResourceValidationError("principal_id is required")
    if not str(request.project_id).strip():
        raise ResearchResourceValidationError("project_id is required")
    if request.resource_id and not _ID_RE.match(request.resource_id):
        raise ResearchResourceValidationError(f"invalid resource_id {request.resource_id!r}")
    if request.schema == RESEARCH_RECORD_SCHEMA:
        # Cross-project TaskRefs are a boundary error, not a display issue.
        for index, task_ref in enumerate(request.document.get("task_refs") or []):
            if not isinstance(task_ref, Mapping):
                raise ResearchResourceValidationError(f"task_refs[{index}] is not an object")
            if str(task_ref.get("project_id") or "") != request.project_id:
                raise ResearchResourceValidationError(
                    f"task_refs[{index}].project_id crosses the resource project"
                )
    return ResearchResourceAuthorization(
        principal_id=request.principal_id,
        project_id=request.project_id,
        scope=request.scope,
        action="write",
        allowed=True,
    )


def _require_access(record: ResearchResourceRecord, principal_id: str, project_id: str) -> None:
    if not record.authorization.allowed:
        raise ResearchResourceAuthorizationError("resource authorization projection is denied")
    if record.authorization.project_id != project_id:
        raise ResearchResourceAuthorizationError("resource belongs to another project")
    if not principal_id:
        raise ResearchResourceAuthorizationError("a principal is required for every access")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ResearchResourceStore:
    """SQLite-backed versioned JSON resource store (Data authority).

    The database is the authority; no in-memory map is consulted for a read.
    The schema is created idempotently on construction.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_resources (
                    resource_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    schema TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    authorization_json TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revocation_reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (resource_id, revision)
                )
                """
            )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    def __enter__(self) -> ResearchResourceStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- row helpers -----------------------------------------------------

    def _record(self, row: sqlite3.Row) -> ResearchResourceRecord:
        document = json.loads(row["document_json"])
        authorization = ResearchResourceAuthorization.from_dict(
            json.loads(row["authorization_json"])
        )
        return ResearchResourceRecord(
            resource_id=str(row["resource_id"]),
            schema=str(row["schema"]),
            revision=int(row["revision"]),
            digest=str(row["digest"]),
            document=document if isinstance(document, Mapping) else {},
            project_id=str(row["project_id"]),
            principal_id=str(row["principal_id"]),
            authorization=authorization,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            revoked_at=row["revoked_at"],
            revocation_reason=str(row["revocation_reason"] or ""),
        )

    def _latest(self, resource_id: str) -> ResearchResourceRecord | None:
        row = self._conn.execute(
            "SELECT * FROM research_resources WHERE resource_id = ? ORDER BY revision DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        return None if row is None else self._record(row)

    def _get(self, resource_id: str, revision: int | None) -> ResearchResourceRecord | None:
        if revision is None:
            return self._latest(resource_id)
        row = self._conn.execute(
            "SELECT * FROM research_resources WHERE resource_id = ? AND revision = ?",
            (resource_id, int(revision)),
        ).fetchone()
        return None if row is None else self._record(row)

    # -- public API ------------------------------------------------------

    def create(self, request: ResearchResourceRequest) -> ResearchResourceRecord:
        """Create revision 1 exactly once for a ``resource_id``."""
        _validate_request(request)
        resource_id = request.resource_id or _default_resource_key(request)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                current = self._latest(resource_id)
                if current is not None:
                    self._conn.execute("ROLLBACK")
                    raise ResearchResourceConflictError(
                        f"resource {resource_id!r} already exists at revision {current.revision}",
                        current,
                    )
                now = self._clock()
                document_json = _canonical_bytes(request.document).decode("utf-8")
                authorization = ResearchResourceAuthorization(
                    principal_id=request.principal_id,
                    project_id=request.project_id,
                    scope=request.scope,
                    action="write",
                    allowed=True,
                )
                self._conn.execute(
                    "INSERT INTO research_resources "
                    "(resource_id, revision, schema, digest, project_id, principal_id, "
                    " authorization_json, document_json, created_at, updated_at) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        resource_id,
                        request.schema,
                        research_resource_digest(request.document),
                        request.project_id,
                        request.principal_id,
                        json.dumps(authorization.to_dict(), sort_keys=True),
                        document_json,
                        now,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            created = self._latest(resource_id)
            if created is None:  # pragma: no cover - insert just committed
                raise ResearchResourceError("resource disappeared after create")
            return created

    def read(
        self,
        resource_id: str,
        *,
        principal_id: str,
        project_id: str,
        revision: int | None = None,
    ) -> ResearchResourceRecord:
        """Read the latest (or a pinned) revision; revoked resources refuse."""
        with self._lock:
            record = self._get(resource_id, revision)
        if record is None:
            raise ResearchResourceNotFoundError(f"resource {resource_id!r} was not found")
        _require_access(record, principal_id, project_id)
        if record.revoked_at:
            raise ResearchResourceRevokedError(
                f"resource {resource_id!r} revision {record.revision} is revoked"
            )
        return record

    def compare_and_swap(
        self,
        resource_id: str,
        *,
        expected_revision: int,
        document: Mapping[str, Any],
        principal_id: str,
        project_id: str,
        schema: str | None = None,
    ) -> ResearchResourceRecord:
        """Append revision ``expected_revision + 1`` only if the head matches."""
        with self._lock:
            current = self._latest(resource_id)
            if current is None:
                raise ResearchResourceNotFoundError(f"resource {resource_id!r} was not found")
            _require_access(current, principal_id, project_id)
            if current.revoked_at:
                raise ResearchResourceRevokedError(
                    f"resource {resource_id!r} is revoked; CAS is refused"
                )
            if int(expected_revision) != current.revision:
                raise ResearchResourceConflictError(
                    f"expected revision {expected_revision} but current is {current.revision}",
                    current,
                )
            request = ResearchResourceRequest(
                resource_id=resource_id,
                schema=schema or current.schema,
                document=document,
                principal_id=principal_id,
                project_id=project_id,
            )
            _validate_request(request)
            if request.schema != current.schema:
                raise ResearchResourceValidationError(
                    "a resource revision cannot change its document schema"
                )
            now = self._clock()
            document_json = _canonical_bytes(document).decode("utf-8")
            authorization = ResearchResourceAuthorization(
                principal_id=principal_id,
                project_id=project_id,
                scope=current.authorization.scope,
                action="write",
                allowed=True,
            )
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO research_resources "
                    "(resource_id, revision, schema, digest, project_id, principal_id, "
                    " authorization_json, document_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        resource_id,
                        current.revision + 1,
                        request.schema,
                        research_resource_digest(document),
                        project_id,
                        principal_id,
                        json.dumps(authorization.to_dict(), sort_keys=True),
                        document_json,
                        current.created_at,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            updated = self._latest(resource_id)
            if updated is None:  # pragma: no cover - insert just committed
                raise ResearchResourceError("resource disappeared after CAS")
            return updated

    def revoke(
        self,
        resource_id: str,
        *,
        principal_id: str,
        project_id: str,
        reason: str,
        expected_revision: int | None = None,
    ) -> ResearchResourceRecord:
        """Mark the current revision revoked; reads/CAS then refuse."""
        with self._lock:
            current = self._latest(resource_id)
            if current is None:
                raise ResearchResourceNotFoundError(f"resource {resource_id!r} was not found")
            _require_access(current, principal_id, project_id)
            if current.revoked_at:
                raise ResearchResourceRevokedError(f"resource {resource_id!r} is already revoked")
            if expected_revision is not None and int(expected_revision) != current.revision:
                raise ResearchResourceConflictError(
                    f"expected revision {expected_revision} but current is {current.revision}",
                    current,
                )
            now = self._clock()
            self._conn.execute(
                "UPDATE research_resources SET revoked_at = ?, "
                "revocation_reason = ?, updated_at = ? "
                "WHERE resource_id = ? AND revision = ?",
                (now, str(reason or ""), now, resource_id, current.revision),
            )
            revoked = self._latest(resource_id)
            if revoked is None:  # pragma: no cover - update just committed
                raise ResearchResourceError("resource disappeared after revoke")
            return revoked

    def versions(self, resource_id: str) -> list[ResearchResourceRecord]:
        """Return every persisted revision in ascending order (no body checks)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM research_resources WHERE resource_id = ? ORDER BY revision ASC",
                (resource_id,),
            ).fetchall()
        return [self._record(row) for row in rows]
