"""Data namespace provisioning — the single ``ensure_namespace`` use case.

B09-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 48, MU-03).  The
main plan §22.2 splits "开通个人环境" three ways:

* **Workspace** registers an identity / default project and *persists the
  environment provisioning intent*;
* **Runtime** owns the ``ProvisionWorkspace`` use case — it consumes the
  persisted intent and calls the public ``Data.ensure_namespace`` entry point;
* **Data** creates the namespace + storage binding (and any necessary
  permission), exactly once.

This module is only the **Data** half: :meth:`NamespaceStore.ensure_namespace`
atomically creates the namespace and the frozen
:class:`~acp_services.contracts.envelope.StorageBinding`.  It never imports
Runtime, Workspace or ``acp`` — the dependency direction is
``Runtime → Data`` and never ``Workspace → Data``.

Hard properties (all enforced here)

* **Idempotent.**  The namespace key is the logical identity; the row carries a
  ``UNIQUE(namespace_key)`` constraint, so repeating the same request returns
  the *same* namespace and never creates a second default space.  A concurrent
  duplicate insert adopts the winner (``IntegrityError`` → re-select).
* **Atomic binding.**  The binding is derived deterministically and inserted in
  one ``BEGIN IMMEDIATE`` transaction, so a namespace never exists without its
  binding.
* **Persisted state.**  The namespace, its kind, state, owning tenant/project
  and the frozen binding live in the ``namespaces`` SQLite table — the DB is the
  authority, not memory.
* **Strict, not silent.**  A request that reuses a key with a *different*
  binding identity (tenant/project/endpoint/root/relative path/kind) is refused
  with :class:`NamespaceConflictError` instead of silently reusing a wrong
  space.

This module never opens a network connection, spawns a subprocess or creates a
system account; it is a pure storage use case.

Author: ACP Services (B lane, todo 48).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp_services.contracts.envelope import StorageBinding
from acp_services.data.storage_binding import MAPPING_VERSION_CURRENT, freeze_binding

logger = logging.getLogger(__name__)

__all__ = [
    "NAMESPACE_ID_PREFIX",
    "NamespaceKind",
    "NamespaceState",
    "NamespaceError",
    "NamespaceValidationError",
    "NamespaceConflictError",
    "EnsureNamespaceRequest",
    "NamespaceRecord",
    "NamespaceStore",
    "namespace_id_for",
]


#: Deterministic prefix for a namespace id (parallels ``sb_`` / ``ss_``).
NAMESPACE_ID_PREFIX = "ns_"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class NamespaceKind(str):
    """Kind of logical space a provisioning intent asks Data to create."""

    PERSONAL_SPACE = "personal_space"
    DEFAULT_PROJECT = "default_project"
    NAMESPACE = "namespace"
    ALL = frozenset({PERSONAL_SPACE, DEFAULT_PROJECT, NAMESPACE})


class NamespaceState(str):
    """Persisted lifecycle of one namespace row."""

    PROVISIONED = "provisioned"
    FAILED = "failed"
    ALL = frozenset({PROVISIONED, FAILED})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NamespaceError(RuntimeError):
    """Base error for namespace provisioning."""


class NamespaceValidationError(NamespaceError):
    """The request is malformed (empty key/root/path or unknown kind)."""


class NamespaceConflictError(NamespaceError):
    """A reused namespace key carries a different binding identity (not silent)."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def namespace_id_for(namespace_key: str) -> str:
    """Return the deterministic namespace id of one logical key.

    Stable across retries and re-runs, so a repeated provisioning resolves to
    the *same* id; the ``UNIQUE(namespace_key)`` constraint is the authority.
    """
    digest = hashlib.sha256(str(namespace_key).encode("utf-8")).hexdigest()[:24]
    return f"{NAMESPACE_ID_PREFIX}{digest}"


def _project_leaf(relative_path: str) -> str:
    head, sep, _tail = relative_path.partition("/")
    return head if sep else ""


def _now_iso() -> str:
    """Data-owner timestamp format (matches ``data.operations`` / ``transfer``)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# ---------------------------------------------------------------------------
# Request / record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnsureNamespaceRequest:
    """The logical identity of the namespace Data must ensure.

    ``namespace_key`` is the stable logical identity (e.g.
    ``personal_space:alice``); repeating it with the same identity is a no-op.
    ``root`` + ``relative_path`` + ``endpoint`` position the storage binding;
    ``tenant_id`` / ``project_id`` scope it.
    """

    namespace_key: str
    namespace_kind: str = NamespaceKind.NAMESPACE
    tenant_id: str = ""
    project_id: str = ""
    principal_id: str = ""
    root: str = ""
    relative_path: str = ""
    endpoint: str = ""
    display_name: str = ""
    mapping_version: int = MAPPING_VERSION_CURRENT

    def __post_init__(self) -> None:
        if not str(self.namespace_key).strip():
            raise NamespaceValidationError("namespace_key must be a non-empty string")
        if str(self.namespace_kind) not in NamespaceKind.ALL:
            raise NamespaceValidationError(
                f"unknown namespace_kind {self.namespace_kind!r}; "
                f"expected one of {sorted(NamespaceKind.ALL)}"
            )
        if not str(self.root).strip():
            raise NamespaceValidationError("root must be a non-empty string")
        if not str(self.relative_path).strip():
            raise NamespaceValidationError("relative_path must be a non-empty string")
        if int(self.mapping_version) < 0:
            raise NamespaceValidationError("mapping_version must be >= 0")


@dataclass(frozen=True)
class NamespaceRecord:
    """One persisted namespace (authoritative row projection).

    ``storage_binding`` is the immutable
    :class:`~acp_services.contracts.envelope.StorageBinding` created for the
    namespace; it is returned unchanged on every idempotent re-request.
    """

    namespace_id: str
    namespace_key: str
    namespace_kind: str
    state: str
    tenant_id: str
    project_id: str
    principal_id: str
    display_name: str
    storage_binding: StorageBinding
    revision: int
    created_at: str
    updated_at: str

    @property
    def binding_id(self) -> str:
        return self.storage_binding.binding_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace_id": self.namespace_id,
            "namespace_key": self.namespace_key,
            "namespace_kind": self.namespace_kind,
            "state": self.state,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "principal_id": self.principal_id,
            "display_name": self.display_name,
            "storage_binding": self.storage_binding.to_dict(),
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# SQLite authority
# ---------------------------------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS namespaces (
    namespace_id   TEXT PRIMARY KEY,
    namespace_key  TEXT NOT NULL UNIQUE,
    namespace_kind TEXT NOT NULL,
    state          TEXT NOT NULL,
    tenant_id      TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    principal_id   TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    binding_id     TEXT NOT NULL,
    binding_json   TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_namespaces_state ON namespaces(state);
"""

_INSERT_SQL = """
INSERT INTO namespaces (
    namespace_id, namespace_key, namespace_kind, state, tenant_id, project_id,
    principal_id, display_name, binding_id, binding_json, revision,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _row_to_record(row: sqlite3.Row) -> NamespaceRecord:
    binding = StorageBinding.from_dict(json.loads(row["binding_json"]))
    return NamespaceRecord(
        namespace_id=row["namespace_id"],
        namespace_key=row["namespace_key"],
        namespace_kind=row["namespace_kind"],
        state=row["state"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        principal_id=row["principal_id"],
        display_name=row["display_name"],
        storage_binding=binding,
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class NamespaceStore:
    """SQLite-backed namespace authority (one row per logical namespace key).

    Args:
        db_path: Explicit SQLite path; the schema is created idempotently.  The
            scheduler passes its own ``acp_jobs.db`` so namespaces survive the
            same backup/restore as jobs.
        clock: Optional ``() -> str`` timestamp override (deterministic tests).
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _now_iso
        self._lock = threading.RLock()
        self._ensure_schema()

    # -- schema / connection ------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE_SQL)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -- binding derivation -------------------------------------------------

    @staticmethod
    def _binding_for(request: EnsureNamespaceRequest) -> StorageBinding:
        """Derive + freeze the binding for *request* (pure, no I/O)."""
        relative = str(request.relative_path).strip()
        binding = StorageBinding(
            binding_id="",
            tenant_id=request.tenant_id,
            project_id=request.project_id or _project_leaf(relative),
            endpoint=request.endpoint,
            root=request.root,
            relative_path=relative,
            mapping_version=int(request.mapping_version),
        )
        return freeze_binding(binding)

    @staticmethod
    def _assert_compatible(record: NamespaceRecord, request: EnsureNamespaceRequest) -> None:
        """Refuse to silently reuse a key whose binding identity differs."""
        derived = NamespaceStore._binding_for(request)
        existing = record.storage_binding
        if str(record.namespace_kind) != str(request.namespace_kind):
            raise NamespaceConflictError(
                f"namespace_key {request.namespace_key!r} already exists with kind "
                f"{record.namespace_kind!r}, not {request.namespace_kind!r}"
            )
        if (
            existing.tenant_id != derived.tenant_id
            or existing.project_id != derived.project_id
            or existing.endpoint != derived.endpoint
            or existing.root != derived.root
            or existing.relative_path != derived.relative_path
            or int(existing.mapping_version) != int(derived.mapping_version)
        ):
            raise NamespaceConflictError(
                f"namespace_key {request.namespace_key!r} already exists with a different "
                f"binding identity ({existing.relative_path!r} @ {existing.root!r}); "
                "refusing to silently reuse it"
            )

    # -- ensure -------------------------------------------------------------

    def ensure_namespace(self, request: EnsureNamespaceRequest) -> NamespaceRecord:
        """Idempotently ensure the namespace + binding for *request*.

        Repeating the same request returns the *same* record and never creates a
        second namespace (``UNIQUE(namespace_key)``).  A concurrent duplicate
        adopts the winner.  A reused key with a different binding identity is
        refused with :class:`NamespaceConflictError`.

        Raises:
            NamespaceValidationError: The request is malformed (raised in
                :class:`EnsureNamespaceRequest.__post_init__`).
            NamespaceConflictError: The key exists with a different identity.
            NamespaceError: The underlying storage write failed.
        """
        if not isinstance(request, EnsureNamespaceRequest):
            raise NamespaceValidationError("ensure_namespace requires an EnsureNamespaceRequest")
        binding = self._binding_for(request)
        now = self._clock()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM namespaces WHERE namespace_key=?",
                    (request.namespace_key,),
                ).fetchone()
                if row is not None:
                    record = _row_to_record(row)
                    self._assert_compatible(record, request)
                    conn.execute("COMMIT")
                    return record

                namespace_id = namespace_id_for(request.namespace_key)
                params = (
                    namespace_id,
                    request.namespace_key,
                    request.namespace_kind,
                    NamespaceState.PROVISIONED,
                    request.tenant_id,
                    request.project_id,
                    request.principal_id,
                    request.display_name,
                    binding.binding_id,
                    json.dumps(binding.to_dict(), sort_keys=True),
                    1,
                    now,
                    now,
                )
                try:
                    conn.execute(_INSERT_SQL, params)
                except sqlite3.IntegrityError:
                    # A concurrent creator won the race; adopt it (idempotent).
                    row = conn.execute(
                        "SELECT * FROM namespaces WHERE namespace_key=?",
                        (request.namespace_key,),
                    ).fetchone()
                    if row is None:  # pragma: no cover - unique-constraint anomaly
                        raise
                    record = _row_to_record(row)
                    self._assert_compatible(record, request)
                    conn.execute("COMMIT")
                    return record
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - no active transaction
                    pass
                raise
            finally:
                conn.close()

        created = self.get(namespace_id)
        if created is None:  # pragma: no cover - row written above
            raise NamespaceError(f"namespace {namespace_id!r} vanished after insert")
        logger.info(
            "namespace ensured key=%s id=%s kind=%s",
            request.namespace_key,
            namespace_id,
            request.namespace_kind,
        )
        return created

    # -- reads --------------------------------------------------------------

    def get(self, namespace_id: str) -> NamespaceRecord | None:
        """Return one namespace by id, or ``None``."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM namespaces WHERE namespace_id=?", (str(namespace_id),)
                ).fetchone()
                return _row_to_record(row) if row is not None else None
            finally:
                conn.close()

    def find_by_key(self, namespace_key: str) -> NamespaceRecord | None:
        """Return one namespace by its stable logical key, or ``None``."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM namespaces WHERE namespace_key=?", (str(namespace_key),)
                ).fetchone()
                return _row_to_record(row) if row is not None else None
            finally:
                conn.close()

    def list(self, *, state: str | None = None) -> tuple[NamespaceRecord, ...]:
        """List namespaces, optionally filtered by state (creation order)."""
        with self._lock:
            conn = self._connect()
            try:
                if state is None:
                    rows = conn.execute(
                        "SELECT * FROM namespaces ORDER BY created_at ASC, namespace_id ASC"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM namespaces WHERE state=? "
                        "ORDER BY created_at ASC, namespace_id ASC",
                        (str(state),),
                    ).fetchall()
                return tuple(_row_to_record(row) for row in rows)
            finally:
                conn.close()

    def count(self) -> int:
        """Total number of persisted namespaces."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM namespaces").fetchone()
                return int(row["n"])
            finally:
                conn.close()
