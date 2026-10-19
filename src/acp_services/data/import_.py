"""Quota-limited, digest-verified archive import with atomic registration.

B08-4 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 45).  This
module is the **import** counterpart of :mod:`acp_services.data.archive` (todo
44, export): it turns an exported, versioned archive package into a new, locally
registered Data artifact — and nothing more.  An import **never** acquires
execution authority, never adopts an external task's lease / billing / active
run, and never executes a scientific program or unpickles a Python object.

Guarantees (each enforced here, not by convention)
--------------------------------------------------
1. **Quota-limited staging.**  Before any bytes are materialised a package is
   validated against :class:`ImportQuota`: total bytes, per-file bytes, inode
   count, path depth, symlinks and *compression amplification* (archive-bomb
   detection).  Every bundle read is bounded, so a lying manifest cannot make the
   importer allocate unbounded memory.
2. **Schema / digest / traversal / bomb validation.**  The manifest schema
   version is checked; every declared file's relative path is normalised and
   absolute/``..`` paths are structurally refused; symlink entries are refused by
   default; each file's bytes are hashed and compared to the manifest's
   ``sha256`` + ``size_bytes`` (a manifest lie is caught on read).
3. **Persistent staging + import records, with an explicit recovery order.**
   The crash window is documented and reconcilable:

   .. code-block:: text

      1. begin_staging      → INSERT staging row (state=staged)      [durable intent]
      2. validate           → state=validated (or rejected)
      3. publish files      → temp file + os.replace per entry; state=published
      4. COMMIT registration→ one SQLite transaction: import row + file rows
      5. finish staging     → state=completed

   File publication **precedes** the DB commit.  The commit (step 4) is the
   single atomic commit point and the *only* thing that makes an import visible.
   Therefore:

   * crash before step 4 → a staging row exists, **no** import row exists, so the
     import is *not visible*; :meth:`ArchiveImportService.reconcile` classifies it
     as ``orphan_staging``.  Re-calling :meth:`ArchiveImportService.import_package`
     with the same request resumes it idempotently (staging resets, files are
     re-published atomically), or :meth:`ArchiveImportService.discard_staging` can
     drop it.
   * crash after step 4 → the import row exists (visible, with all file rows) and
     the staging row is merely dangling; reconcile marks it ``completed``.

4. **Atomic registration.**  A committed import carries a new platform id, a
   frozen :class:`~acp_services.contracts.envelope.StorageBinding` (where the
   bytes live) and a :class:`SourceMapping` that preserves the *scientific*
   content identity (``package_digest`` + per-file digests) of the external
   source.  Registration is one exists-or-not row: there is **no visible
   half-registered success**.
5. **Idempotent, scope-separated.**  The idempotency key (and the platform id)
   include the ``permission_scope``, so two imports of the *same* digest under
   *different* permissions are never merged.  A duplicate import returns the
   existing record unchanged (never overwriting an existing project/attempt).
6. **Provenance only for external task state.**  An external task's status /
   active flag is recorded as provenance; the import result always reports
   ``execution_authority=False``, ``adopted_external_authority=False`` and
   ``auto_executed=False``.  Any platform task projection is returned as a
   :class:`TaskProjectionRequest` for a Runtime *public use case* to register as a
   **historical** record — the importer itself registers nothing and adopts no
   external execution right.

Isolation (hard rule).  ``src/acp_services/**`` must never import the legacy
application package; this module is stdlib-only plus its own package's
:mod:`acp_services.data.archive`, :mod:`acp_services.data.storage_binding` and
:mod:`acp_services.contracts.envelope`.  The package bytes are injected through
:class:`ImportBundlePort`; the legacy-side composition hook lives in
``acp.services_data_bootstrap``.

Anti-goals (explicitly refused): merging the same digest under different
permissions; importing execution authority; any executed/unpickled content; any
externally visible success before the DB commit.

Author: ACP Services (B lane, todo 45).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.envelope import StorageBinding
from acp_services.data.archive import (
    ARCHIVE_MANIFEST_VERSION,
    ArchiveManifest,
    ArchiveReference,
)
from acp_services.data.storage_binding import MAPPING_VERSION_CURRENT, freeze_binding

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_IMPORT_QUOTA",
    "IMPORT_KEY_PREFIX",
    "PLATFORM_ID_PREFIX",
    "ArchiveImportService",
    "BundleEntry",
    "ExternalProvenance",
    "ImportBundlePort",
    "ImportConflictError",
    "ImportError",
    "ImportIntegrityError",
    "ImportNotFoundError",
    "ImportQuota",
    "ImportRecord",
    "ImportRecordStore",
    "ImportRejectedError",
    "ImportRequest",
    "ImportResult",
    "ImportSizeLimitError",
    "ImportState",
    "LocalImportBundle",
    "ReconcileReport",
    "RejectionDetail",
    "RejectionReason",
    "SourceMapping",
    "StagedEntry",
    "StagingRecord",
    "TaskProjectionRequest",
    "import_idempotency_key_for",
    "platform_id_for",
    "validate_package",
]

#: Deterministic prefix for a new platform id allocated by an import.
PLATFORM_ID_PREFIX = "imp_"
#: Deterministic prefix for the scope-separated idempotency key.
IMPORT_KEY_PREFIX = "imk_"
#: Leaf under the import root holding every registered package.
DEFAULT_IMPORTS_LEAF = "imports"


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ImportState(str, Enum):
    """Lifecycle of one staging/import record.

    A *staging* row walks ``staged`` → ``validated`` → ``published`` →
    ``completed`` (or ends ``rejected``/``failed``).  A committed *import* row is
    exactly ``registered``: its existence is the commit.
    """

    STAGED = "staged"
    VALIDATED = "validated"
    PUBLISHED = "published"
    REGISTERED = "registered"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class RejectionReason(str, Enum):
    """Why a package was refused (recorded, never visible as a registered import)."""

    SCHEMA_VERSION = "schema_version"
    EXECUTION_AUTHORITY = "execution_authority"
    TRAVERSAL = "path_traversal"
    ABSOLUTE_PATH = "absolute_path"
    SYMLINK = "symlink"
    DEEP_PATH = "path_too_deep"
    QUOTA_BYTES = "quota_total_bytes"
    QUOTA_FILE_BYTES = "quota_file_bytes"
    QUOTA_INODES = "quota_inodes"
    QUOTA_AMPLIFICATION = "quota_compression_amplification"
    MISSING_BODY = "missing_body"
    SIZE_MISMATCH = "size_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    EMPTY_PACKAGE = "empty_package"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ImportError(RuntimeError):
    """Base class for every archive-import failure."""


class ImportRejectedError(ImportError):
    """A package was refused; ``detail`` names the exact reason.

    Raised for every validation failure *after* the staging row has been marked
    ``rejected`` — so a caller can never observe a registered import for a
    refused package.
    """

    def __init__(self, reason: RejectionReason, *, path: str = "", detail: str = "") -> None:
        self.detail = RejectionDetail(reason=reason, path=path, detail=detail)
        message = f"archive import rejected: {reason.value}"
        if path:
            message += f" ({path})"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class ImportSizeLimitError(ImportError):
    """An injected bundle exceeded a bounded read (anti bomb, raised by the port)."""


class ImportNotFoundError(ImportError):
    """A platform id or relative file is not a registered import/file."""


class ImportIntegrityError(ImportError):
    """A registered file's bytes no longer match the recorded digest/size."""


class ImportConflictError(ImportError):
    """A concurrent commit won the idempotency key (the caller adopts the winner)."""


# ---------------------------------------------------------------------------
# Frozen records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportQuota:
    """Staging limits enforced before any package is registered.

    ``max_compression_ratio`` is the archive-bomb guard: a declared
    (uncompressed) size that is more than this multiple of its stored
    (compressed) size is refused.  ``max_path_depth`` counts path segments.
    """

    max_total_bytes: int = 16 * 1024 * 1024 * 1024
    max_file_bytes: int = 4 * 1024 * 1024 * 1024
    max_inodes: int = 200_000
    max_compression_ratio: float = 100.0
    max_path_depth: int = 32
    allow_symlinks: bool = False

    def __post_init__(self) -> None:
        if int(self.max_total_bytes) < 0:
            raise ValueError("max_total_bytes must be >= 0")
        if int(self.max_file_bytes) < 0:
            raise ValueError("max_file_bytes must be >= 0")
        if int(self.max_inodes) < 0:
            raise ValueError("max_inodes must be >= 0")
        if float(self.max_compression_ratio) < 1.0:
            raise ValueError("max_compression_ratio must be >= 1.0")
        if int(self.max_path_depth) < 1:
            raise ValueError("max_path_depth must be >= 1")


DEFAULT_IMPORT_QUOTA = ImportQuota()


@dataclass(frozen=True)
class RejectionDetail:
    """Machine-readable reason a package was refused."""

    reason: RejectionReason
    path: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"reason": self.reason.value, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class BundleEntry:
    """One entry the package body offers (layout facts, not yet validated)."""

    relative_path: str
    is_symlink: bool = False
    stored_size: int | None = None


@dataclass(frozen=True)
class StagedEntry:
    """One validated + published file (digest, size, sniffed type, depth)."""

    relative_path: str
    sha256: str
    size_bytes: int
    content_type: str
    source: str = ""
    depth: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
            "content_type": self.content_type,
            "source": self.source,
            "depth": int(self.depth),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StagedEntry:
        return cls(
            relative_path=str(payload.get("relative_path", "")),
            sha256=str(payload.get("sha256", "")),
            size_bytes=int(payload.get("size_bytes", 0) or 0),
            content_type=str(payload.get("content_type", "")),
            source=str(payload.get("source", "")),
            depth=int(payload.get("depth", 1) or 1),
        )


@dataclass(frozen=True)
class ExternalProvenance:
    """What an external task looked like at import time — **provenance only**.

    ``is_active`` is recorded so a consumer can see the external source was still
    running; it never changes what the importer does and never becomes an adopted
    lease / billing relationship.
    """

    source_namespace: str = ""
    source_id: str = ""
    external_status: str = ""
    is_active: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_namespace": self.source_namespace,
            "source_id": self.source_id,
            "external_status": self.external_status,
            "is_active": bool(self.is_active),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ExternalProvenance | None:
        if not payload:
            return None
        return cls(
            source_namespace=str(payload.get("source_namespace", "")),
            source_id=str(payload.get("source_id", "")),
            external_status=str(payload.get("external_status", "")),
            is_active=bool(payload.get("is_active", False)),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SourceMapping:
    """The preserved scientific-source identity of one registered import.

    ``content_identity`` is the package digest: it is *identical* across
    permission scopes for the same scientific content, while ``platform_id`` is
    scope-separated so two permissions are never merged.
    """

    source_namespace: str
    source_id: str
    source_revision: str
    package_digest: str
    platform_id: str
    permission_scope: str
    content_identity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_namespace": self.source_namespace,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "package_digest": self.package_digest,
            "platform_id": self.platform_id,
            "permission_scope": self.permission_scope,
            "content_identity": self.content_identity,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SourceMapping:
        return cls(
            source_namespace=str(payload.get("source_namespace", "")),
            source_id=str(payload.get("source_id", "")),
            source_revision=str(payload.get("source_revision", "")),
            package_digest=str(payload.get("package_digest", "")),
            platform_id=str(payload.get("platform_id", "")),
            permission_scope=str(payload.get("permission_scope", "")),
            content_identity=str(payload.get("content_identity", "")),
        )


@dataclass(frozen=True)
class TaskProjectionRequest:
    """A request for Runtime to register a **historical** task projection.

    The importer never registers it and never copies execution authority: a
    Runtime *public use case* decides whether to create the historical record.
    """

    source_namespace: str
    source_id: str
    platform_id: str
    source_revision: str = ""
    historical: bool = True
    execution_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_namespace": self.source_namespace,
            "source_id": self.source_id,
            "platform_id": self.platform_id,
            "source_revision": self.source_revision,
            "historical": bool(self.historical),
            "execution_authority": bool(self.execution_authority),
        }


@dataclass(frozen=True)
class ImportRequest:
    """Identity + policy of one archive import, independent of the package body."""

    source_namespace: str
    source_id: str
    manifest: ArchiveManifest
    permission_scope: str = "local-single"
    source_revision: str = ""
    provenance: ExternalProvenance | None = None
    tenant_id: str = ""
    project_id: str = ""
    imports_leaf: str = DEFAULT_IMPORTS_LEAF

    def __post_init__(self) -> None:
        if not str(self.source_namespace).strip():
            raise ValueError("source_namespace must be a non-empty string")
        if not str(self.source_id).strip():
            raise ValueError("source_id must be a non-empty string")
        if not str(self.permission_scope).strip():
            raise ValueError("permission_scope must be a non-empty string")
        leaf = str(self.imports_leaf).strip()
        if not leaf or "/" in leaf or "\\" in leaf or leaf in (".", ".."):
            raise ValueError(f"imports_leaf must be one safe path segment: {self.imports_leaf!r}")

    @property
    def package_digest(self) -> str:
        """Content digest of the whole package (the preserved science identity)."""
        return package_digest_for(self.manifest)

    @property
    def platform_id(self) -> str:
        return platform_id_for(
            source_namespace=self.source_namespace,
            source_id=self.source_id,
            package_digest=self.package_digest,
            permission_scope=self.permission_scope,
        )

    @property
    def idempotency_key(self) -> str:
        return import_idempotency_key_for(
            source_namespace=self.source_namespace,
            source_id=self.source_id,
            package_digest=self.package_digest,
            permission_scope=self.permission_scope,
        )


@dataclass(frozen=True)
class StagingRecord:
    """One persisted staging row (durable intent; never a visible import)."""

    idempotency_key: str
    platform_id: str
    state: ImportState
    package_digest: str
    permission_scope: str
    source_namespace: str
    source_id: str
    staging_dir: str
    detail: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ImportRecord:
    """One committed (visible) import: the atomic registration row + files."""

    platform_id: str
    idempotency_key: str
    source_namespace: str
    source_id: str
    source_revision: str
    package_digest: str
    permission_scope: str
    imports_leaf: str
    binding: StorageBinding
    mapping: SourceMapping
    provenance: ExternalProvenance | None
    manifest: Mapping[str, Any]
    files: tuple[StagedEntry, ...]
    created_at: str
    updated_at: str

    @property
    def state(self) -> ImportState:
        return ImportState.REGISTERED


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one successful (or duplicate) import.

    The three authority flags are hard-wired ``False`` — an import never executes,
    never unpickles and never adopts an external execution right.
    """

    platform_id: str
    idempotency_key: str
    state: ImportState
    registered: bool
    duplicate: bool
    binding: StorageBinding | None = None
    mapping: SourceMapping | None = None
    provenance: ExternalProvenance | None = None
    files: tuple[StagedEntry, ...] = ()
    references: tuple[ArchiveReference, ...] = ()
    task_projection: TaskProjectionRequest | None = None
    staging_dir: str = ""
    rejection: RejectionDetail | None = None
    execution_authority: bool = False
    adopted_external_authority: bool = False
    auto_executed: bool = False


@dataclass(frozen=True)
class ReconcileReport:
    """Crash-window classification from :meth:`ArchiveImportService.reconcile`."""

    registered: int = 0
    completed_staging: int = 0
    rejected_staging: int = 0
    orphan_staging: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pure identity helpers
# ---------------------------------------------------------------------------


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def package_digest_for(manifest: ArchiveManifest) -> str:
    """Content digest of a package: sha256 over its versioned manifest body.

    The manifest's own ``created_at``/``export_attempts`` are diagnostic and are
    excluded, so the digest identifies *content*, not *when* it was exported.
    """
    body = manifest.to_dict()
    body.pop("created_at", None)
    body.pop("export_attempts", None)
    return "pkg_" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:40]


def platform_id_for(
    *, source_namespace: str, source_id: str, package_digest: str, permission_scope: str
) -> str:
    """Return the deterministic, scope-separated platform id of one import."""
    payload = "\x1f".join([source_namespace, source_id, package_digest, permission_scope])
    return PLATFORM_ID_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def import_idempotency_key_for(
    *, source_namespace: str, source_id: str, package_digest: str, permission_scope: str
) -> str:
    """Return the scope-separated idempotency key (different scopes never merge)."""
    payload = "\x1f".join([permission_scope, source_namespace, source_id, package_digest])
    return IMPORT_KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _safe_relative_path(value: str) -> str | None:
    """Normalise to a safe POSIX relative path, or ``None`` when hostile.

    Absolute paths (POSIX or Windows) and ``..`` traversal return ``None`` so a
    hostile path can never be published — the exclusion is structural.
    """
    text = str(value).replace("\\", "/").strip()
    if not text:
        return None
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _hostile_path_reason(value: str) -> RejectionReason:
    """Classify a refused path: an absolute path vs a ``..`` traversal."""
    text = str(value).replace("\\", "/").strip()
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return RejectionReason.ABSOLUTE_PATH
    return RejectionReason.TRAVERSAL


def _depth_of(relative_path: str) -> int:
    return len(relative_path.split("/"))


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _import_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file in the same dir + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=str(path.parent), suffix=".tmp", delete=False)
    try:
        handle.write(data)
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` bytes; raise the moment the file is larger."""
    with open(path, "rb") as handle:  # noqa: PTH123 - bounded binary read
        data = handle.read(int(max_bytes) + 1)
    if len(data) > int(max_bytes):
        raise ImportSizeLimitError(f"{path.name} exceeds {int(max_bytes)} bytes")
    return data


# ---------------------------------------------------------------------------
# Bundle seam + local implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class ImportBundlePort(Protocol):
    """The bytes + layout of an exported package, injected behind a seam.

    ``read_bytes`` **must** be bounded: it returns at most ``max_bytes`` bytes and
    raises :class:`ImportSizeLimitError` when more exist, so the importer never
    decompresses an unbounded payload.  ``stored_size`` returns the compressed
    size when the package is delivered compressed (``None`` otherwise) and feeds
    the archive-bomb check.
    """

    def list_entries(self) -> tuple[BundleEntry, ...]:
        """Every entry the package body offers (layout inspection only)."""
        ...

    def read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        """Read one entry's bytes, bounded by ``max_bytes``."""
        ...

    def stored_size(self, relative_path: str) -> int | None:
        """Compressed/on-disk size of one entry, or ``None`` when not applicable."""
        ...


class LocalImportBundle:
    """An :class:`ImportBundlePort` over a local package directory.

    Symlinks are reported (never followed) so the validator can refuse them; the
    stored size is the on-disk size.  Reads are bounded.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def list_entries(self) -> tuple[BundleEntry, ...]:
        if not self._root.is_dir():
            return ()
        entries: list[BundleEntry] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() and not path.is_symlink():
                continue
            relative = path.relative_to(self._root).as_posix()
            entries.append(
                BundleEntry(
                    relative_path=relative,
                    is_symlink=path.is_symlink(),
                    stored_size=None,
                )
            )
        return tuple(entries)

    def read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        normalized = _safe_relative_path(relative_path)
        if normalized is None:
            raise ImportSizeLimitError(f"unsafe bundle path: {relative_path!r}")
        target = self._root / normalized
        if target.is_symlink():
            raise ImportSizeLimitError(f"refusing to follow symlink: {relative_path!r}")
        return _read_bounded(target, max_bytes=int(max_bytes))

    def stored_size(self, relative_path: str) -> int | None:
        normalized = _safe_relative_path(relative_path)
        if normalized is None:
            return None
        try:
            return (self._root / normalized).stat().st_size
        except OSError:
            return None


# ---------------------------------------------------------------------------
# Validation (pure over the injected bundle)
# ---------------------------------------------------------------------------


def _check_bundle_layout(
    bundle_entries: tuple[BundleEntry, ...], quota: ImportQuota
) -> dict[str, BundleEntry]:
    if len(bundle_entries) > int(quota.max_inodes):
        raise ImportRejectedError(
            RejectionReason.QUOTA_INODES,
            detail=f"{len(bundle_entries)} entries > {int(quota.max_inodes)}",
        )
    by_path: dict[str, BundleEntry] = {}
    for entry in bundle_entries:
        normalized = _safe_relative_path(entry.relative_path)
        if normalized is None:
            raise ImportRejectedError(
                _hostile_path_reason(entry.relative_path), path=entry.relative_path
            )
        if entry.is_symlink and not quota.allow_symlinks:
            raise ImportRejectedError(RejectionReason.SYMLINK, path=entry.relative_path)
        if _depth_of(normalized) > int(quota.max_path_depth):
            raise ImportRejectedError(
                RejectionReason.DEEP_PATH,
                path=entry.relative_path,
                detail=f"depth {_depth_of(normalized)} > {int(quota.max_path_depth)}",
            )
        by_path[normalized] = entry
    return by_path


def validate_package(
    manifest: ArchiveManifest,
    bundle: ImportBundlePort,
    quota: ImportQuota,
) -> tuple[StagedEntry, ...]:
    """Validate + hash every declared file of *manifest* against *bundle*.

    Returns the validated entries (digest verified) or raises
    :class:`ImportRejectedError`.  The manifest is *declared* metadata; every
    declared byte is re-read (bounded) and re-hashed, so a lying manifest cannot
    smuggle content past the digest check.
    """
    if int(manifest.manifest_version) != ARCHIVE_MANIFEST_VERSION:
        raise ImportRejectedError(
            RejectionReason.SCHEMA_VERSION,
            detail=f"manifest_version {manifest.manifest_version} != {ARCHIVE_MANIFEST_VERSION}",
        )
    if manifest.execution_authority:
        raise ImportRejectedError(
            RejectionReason.EXECUTION_AUTHORITY,
            detail="import never accepts a package carrying execution authority",
        )

    bundle_by_path = _check_bundle_layout(tuple(bundle.list_entries()), quota)

    declared_total = sum(int(entry.size_bytes) for entry in manifest.files)
    if declared_total > int(quota.max_total_bytes):
        raise ImportRejectedError(
            RejectionReason.QUOTA_BYTES,
            detail=f"declared {declared_total} bytes > {int(quota.max_total_bytes)}",
        )

    validated: list[StagedEntry] = []
    for entry in manifest.files:
        normalized = _safe_relative_path(entry.relative_path)
        if normalized is None:
            raise ImportRejectedError(
                _hostile_path_reason(entry.relative_path), path=entry.relative_path
            )
        depth = _depth_of(normalized)
        if depth > int(quota.max_path_depth):
            raise ImportRejectedError(
                RejectionReason.DEEP_PATH,
                path=entry.relative_path,
                detail=f"depth {depth} > {int(quota.max_path_depth)}",
            )
        if int(entry.size_bytes) > int(quota.max_file_bytes):
            raise ImportRejectedError(
                RejectionReason.QUOTA_FILE_BYTES,
                path=entry.relative_path,
                detail=f"{int(entry.size_bytes)} bytes > {int(quota.max_file_bytes)}",
            )
        stored = bundle.stored_size(normalized)
        if stored is not None and int(stored) >= 0:
            ratio = float(int(entry.size_bytes)) / float(max(int(stored), 1))
            if ratio > float(quota.max_compression_ratio):
                limit = float(quota.max_compression_ratio)
                raise ImportRejectedError(
                    RejectionReason.QUOTA_AMPLIFICATION,
                    path=entry.relative_path,
                    detail=f"amplification {ratio:.1f}x > {limit:.1f}x",
                )
        if normalized not in bundle_by_path:
            raise ImportRejectedError(RejectionReason.MISSING_BODY, path=entry.relative_path)
        try:
            data = bundle.read_bytes(normalized, max_bytes=int(quota.max_file_bytes))
        except ImportSizeLimitError as exc:
            raise ImportRejectedError(
                RejectionReason.QUOTA_FILE_BYTES, path=entry.relative_path, detail=str(exc)
            ) from exc
        if len(data) != int(entry.size_bytes):
            raise ImportRejectedError(
                RejectionReason.SIZE_MISMATCH,
                path=entry.relative_path,
                detail=f"observed {len(data)} != declared {int(entry.size_bytes)}",
            )
        observed = _sha256_hex(data)
        if observed != str(entry.sha256):
            raise ImportRejectedError(
                RejectionReason.DIGEST_MISMATCH,
                path=entry.relative_path,
                detail=f"observed sha256 {observed} != declared {entry.sha256}",
            )
        validated.append(
            StagedEntry(
                relative_path=normalized,
                sha256=observed,
                size_bytes=len(data),
                content_type=str(entry.content_type),
                source=str(entry.source),
                depth=depth,
            )
        )
    validated.sort(key=lambda item: item.relative_path)
    return tuple(validated)


# ---------------------------------------------------------------------------
# Persistence authority (SQLite)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_import_staging (
    idempotency_key TEXT PRIMARY KEY,
    platform_id TEXT NOT NULL,
    state TEXT NOT NULL,
    package_digest TEXT NOT NULL,
    permission_scope TEXT NOT NULL,
    source_namespace TEXT NOT NULL,
    source_id TEXT NOT NULL,
    staging_dir TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_import_staging_state ON data_import_staging(state);
CREATE TABLE IF NOT EXISTS data_imports (
    platform_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_namespace TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL DEFAULT '',
    package_digest TEXT NOT NULL,
    permission_scope TEXT NOT NULL,
    imports_leaf TEXT NOT NULL DEFAULT 'imports',
    binding_json TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_imports_source ON data_imports(source_namespace, source_id);
CREATE TABLE IF NOT EXISTS data_import_files (
    platform_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (platform_id, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_import_files_platform ON data_import_files(platform_id);
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class ImportRecordStore:
    """SQLite authority for staging + committed import records.

    The committed import row (``data_imports``) is the *only* visible success: a
    staging row without an import row is an unfinished attempt, never a partial
    registration.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    def __enter__(self) -> ImportRecordStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- staging --------------------------------------------------------

    def _staging_from_row(self, row: sqlite3.Row) -> StagingRecord:
        return StagingRecord(
            idempotency_key=row["idempotency_key"],
            platform_id=row["platform_id"],
            state=ImportState(row["state"]),
            package_digest=row["package_digest"],
            permission_scope=row["permission_scope"],
            source_namespace=row["source_namespace"],
            source_id=row["source_id"],
            staging_dir=row["staging_dir"],
            detail=str(row["detail"] or ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def begin_staging(self, record: StagingRecord) -> StagingRecord:
        """Durably record import intent (resetting any prior attempt for this key)."""
        iso = _import_timestamp()
        created = record.created_at or iso
        with self._lock:
            self._conn.execute(
                "INSERT INTO data_import_staging ("
                "idempotency_key, platform_id, state, package_digest, permission_scope, "
                "source_namespace, source_id, staging_dir, detail, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(idempotency_key) DO UPDATE SET "
                "platform_id=excluded.platform_id, state=excluded.state, "
                "package_digest=excluded.package_digest, "
                "permission_scope=excluded.permission_scope, "
                "source_namespace=excluded.source_namespace, source_id=excluded.source_id, "
                "staging_dir=excluded.staging_dir, detail='', updated_at=excluded.updated_at",
                (
                    record.idempotency_key,
                    record.platform_id,
                    record.state.value,
                    record.package_digest,
                    record.permission_scope,
                    record.source_namespace,
                    record.source_id,
                    record.staging_dir,
                    record.detail,
                    created,
                    iso,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM data_import_staging WHERE idempotency_key=?",
                (record.idempotency_key,),
            ).fetchone()
            assert row is not None
            return self._staging_from_row(row)

    def update_staging(self, idempotency_key: str, *, state: ImportState, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE data_import_staging SET state=?, detail=?, updated_at=? "
                "WHERE idempotency_key=?",
                (state.value, detail, _import_timestamp(), idempotency_key),
            )

    def get_staging(self, idempotency_key: str) -> StagingRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_import_staging WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return self._staging_from_row(row) if row is not None else None

    def list_staging(self) -> tuple[StagingRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM data_import_staging ORDER BY created_at ASC, idempotency_key ASC"
            ).fetchall()
            return tuple(self._staging_from_row(row) for row in rows)

    # -- committed imports ---------------------------------------------

    def _files_for(self, platform_id: str) -> tuple[StagedEntry, ...]:
        rows = self._conn.execute(
            "SELECT * FROM data_import_files WHERE platform_id=? ORDER BY relative_path ASC",
            (platform_id,),
        ).fetchall()
        return tuple(
            StagedEntry(
                relative_path=row["relative_path"],
                sha256=row["sha256"],
                size_bytes=int(row["size_bytes"]),
                content_type=row["content_type"],
                source=row["source"],
                depth=int(row["depth"]),
            )
            for row in rows
        )

    def _import_from_row(self, row: sqlite3.Row, *, with_files: bool) -> ImportRecord:
        platform_id = row["platform_id"]
        files = self._files_for(platform_id) if with_files else ()
        return ImportRecord(
            platform_id=platform_id,
            idempotency_key=row["idempotency_key"],
            source_namespace=row["source_namespace"],
            source_id=row["source_id"],
            source_revision=row["source_revision"],
            package_digest=row["package_digest"],
            permission_scope=row["permission_scope"],
            imports_leaf=row["imports_leaf"],
            binding=StorageBinding.from_dict(_json_loads(row["binding_json"])),
            mapping=SourceMapping.from_dict(_json_loads(row["mapping_json"])),
            provenance=ExternalProvenance.from_dict(_json_loads(row["provenance_json"])),
            manifest=_json_loads(row["manifest_json"]),
            files=files,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def commit_import(
        self,
        *,
        record: ImportRecord,
        files: tuple[StagedEntry, ...],
    ) -> tuple[ImportRecord, bool]:
        """Atomically commit the import row + its file rows.

        Returns ``(record, duplicate)``.  The whole registration is one
        transaction: either the import row and all file rows exist, or neither
        does.  A unique-key conflict (a concurrent winner) returns the winner
        unchanged — never an overwrite.
        """
        iso = _import_timestamp()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM data_imports WHERE idempotency_key=?", (record.idempotency_key,)
            ).fetchone()
            if existing is not None:
                return self._import_from_row(existing, with_files=True), True
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT INTO data_imports ("
                    "platform_id, idempotency_key, source_namespace, source_id, source_revision, "
                    "package_digest, permission_scope, imports_leaf, binding_json, mapping_json, "
                    "provenance_json, manifest_json, created_at, updated_at"
                    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.platform_id,
                        record.idempotency_key,
                        record.source_namespace,
                        record.source_id,
                        record.source_revision,
                        record.package_digest,
                        record.permission_scope,
                        record.imports_leaf,
                        _json_dumps(record.binding.to_dict()),
                        _json_dumps(record.mapping.to_dict()),
                        _json_dumps(record.provenance.to_dict()) if record.provenance else "{}",
                        _json_dumps(dict(record.manifest)),
                        record.created_at or iso,
                        iso,
                    ),
                )
                self._conn.executemany(
                    "INSERT INTO data_import_files ("
                    "platform_id, relative_path, sha256, size_bytes, content_type, source, depth"
                    ") VALUES (?,?,?,?,?,?,?)",
                    [
                        (
                            record.platform_id,
                            entry.relative_path,
                            entry.sha256,
                            int(entry.size_bytes),
                            entry.content_type,
                            entry.source,
                            int(entry.depth),
                        )
                        for entry in files
                    ],
                )
                self._conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                self._conn.execute("ROLLBACK")
                winner = self._conn.execute(
                    "SELECT * FROM data_imports WHERE idempotency_key=?",
                    (record.idempotency_key,),
                ).fetchone()
                if winner is None:  # pragma: no cover - integrity anomaly without a winner
                    raise
                return self._import_from_row(winner, with_files=True), True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._conn.execute(
                "SELECT * FROM data_imports WHERE platform_id=?", (record.platform_id,)
            ).fetchone()
            assert row is not None
            return self._import_from_row(row, with_files=True), False

    def get_import(self, platform_id: str) -> ImportRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_imports WHERE platform_id=?", (platform_id,)
            ).fetchone()
            return self._import_from_row(row, with_files=True) if row is not None else None

    def get_import_by_key(self, idempotency_key: str) -> ImportRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_imports WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return self._import_from_row(row, with_files=True) if row is not None else None

    def list_imports(self) -> tuple[ImportRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM data_imports ORDER BY created_at ASC, platform_id ASC"
            ).fetchall()
            return tuple(self._import_from_row(row, with_files=True) for row in rows)

    def registered_keys(self) -> frozenset[str]:
        with self._lock:
            rows = self._conn.execute("SELECT idempotency_key FROM data_imports").fetchall()
            return frozenset(str(row["idempotency_key"]) for row in rows)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ArchiveImportService:
    """Stage → validate → publish → atomically register an archive package.

    Args:
        store: The persistent staging + import authority.
        import_root: Controlled root the published bytes live under.
        quota: Staging limits (default :data:`DEFAULT_IMPORT_QUOTA`).
        clock: Injectable wall clock (diagnostic timestamps; tests pin it).
    """

    def __init__(
        self,
        store: ImportRecordStore,
        *,
        import_root: str | Path,
        quota: ImportQuota = DEFAULT_IMPORT_QUOTA,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._import_root = Path(import_root)
        self._quota = quota
        self._clock = clock or time.time

    # -- public ---------------------------------------------------------

    @property
    def store(self) -> ImportRecordStore:
        return self._store

    def import_package(
        self,
        request: ImportRequest,
        bundle: ImportBundlePort,
    ) -> ImportResult:
        """Import one package, idempotently, or raise :class:`ImportRejectedError`.

        A duplicate (same scope + digest) returns the existing registration
        unchanged.  A refused package leaves *no* import row (only a
        ``rejected`` staging row), so no half-registration is ever visible.
        """
        platform_id = request.platform_id
        idempotency_key = request.idempotency_key

        existing = self._store.get_import_by_key(idempotency_key)
        if existing is not None:
            return self._result_from_record(existing, request=request, duplicate=True)

        staging_dir = self._import_root / request.imports_leaf / platform_id
        self._store.begin_staging(
            StagingRecord(
                idempotency_key=idempotency_key,
                platform_id=platform_id,
                state=ImportState.STAGED,
                package_digest=request.package_digest,
                permission_scope=request.permission_scope,
                source_namespace=request.source_namespace,
                source_id=request.source_id,
                staging_dir=str(staging_dir),
                created_at=_import_timestamp(),
                updated_at=_import_timestamp(),
            )
        )

        try:
            entries = validate_package(request.manifest, bundle, self._quota)
        except ImportRejectedError as exc:
            self._store.update_staging(
                idempotency_key, state=ImportState.REJECTED, detail=exc.detail.detail
            )
            raise

        self._store.update_staging(idempotency_key, state=ImportState.VALIDATED)
        try:
            published = self._publish(staging_dir, entries, bundle)
        except ImportRejectedError as exc:
            self._store.update_staging(
                idempotency_key, state=ImportState.REJECTED, detail=exc.detail.detail
            )
            raise
        except OSError as exc:
            self._store.update_staging(idempotency_key, state=ImportState.FAILED, detail=str(exc))
            raise ImportError(f"failed to publish package {platform_id!r}: {exc}") from exc

        self._store.update_staging(idempotency_key, state=ImportState.PUBLISHED)

        binding = freeze_binding(
            StorageBinding(
                binding_id="",
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                endpoint="",
                root=str(self._import_root),
                relative_path=f"{request.imports_leaf}/{platform_id}",
                mapping_version=MAPPING_VERSION_CURRENT,
                artifact_digests={entry.relative_path: entry.sha256 for entry in published},
            )
        )
        mapping = SourceMapping(
            source_namespace=request.source_namespace,
            source_id=request.source_id,
            source_revision=request.source_revision,
            package_digest=request.package_digest,
            platform_id=platform_id,
            permission_scope=request.permission_scope,
            content_identity=request.package_digest,
        )
        iso = _import_timestamp()
        record = ImportRecord(
            platform_id=platform_id,
            idempotency_key=idempotency_key,
            source_namespace=request.source_namespace,
            source_id=request.source_id,
            source_revision=request.source_revision,
            package_digest=request.package_digest,
            permission_scope=request.permission_scope,
            imports_leaf=request.imports_leaf,
            binding=binding,
            mapping=mapping,
            provenance=request.provenance,
            manifest=request.manifest.to_dict(),
            files=published,
            created_at=iso,
            updated_at=iso,
        )
        committed, duplicate = self._store.commit_import(record=record, files=published)
        self._store.update_staging(idempotency_key, state=ImportState.COMPLETED)
        return self._result_from_record(committed, request=request, duplicate=duplicate)

    def get(self, platform_id: str) -> ImportRecord | None:
        """Return a **registered** import, or ``None`` (never a staging row)."""
        return self._store.get_import(platform_id)

    def find(
        self,
        *,
        source_namespace: str,
        source_id: str,
        package_digest: str,
        permission_scope: str,
    ) -> ImportRecord | None:
        """Look up a registered import by its scope-separated identity."""
        key = import_idempotency_key_for(
            source_namespace=source_namespace,
            source_id=source_id,
            package_digest=package_digest,
            permission_scope=permission_scope,
        )
        return self._store.get_import_by_key(key)

    def list(self) -> tuple[ImportRecord, ...]:
        """Every registered import (staging rows are never listed)."""
        return self._store.list_imports()

    def read_manifest(self, platform_id: str) -> dict[str, Any]:
        """Return the stored package manifest of a registered import."""
        record = self._require_registered(platform_id)
        return dict(record.manifest)

    def read_file(self, platform_id: str, relative_path: str) -> bytes:
        """Read one registered file back, re-verifying its recorded digest/size."""
        record = self._require_registered(platform_id)
        normalized = _safe_relative_path(relative_path)
        if normalized is None:
            raise ImportRejectedError(RejectionReason.TRAVERSAL, path=relative_path)
        entry = next((item for item in record.files if item.relative_path == normalized), None)
        if entry is None:
            raise ImportNotFoundError(
                f"{relative_path!r} is not a registered file of {platform_id!r}"
            )
        target = Path(record.binding.root) / record.binding.relative_path / normalized
        data = _read_bounded(target, max_bytes=entry.size_bytes)
        if len(data) != entry.size_bytes or _sha256_hex(data) != entry.sha256:
            raise ImportIntegrityError(f"registered file {relative_path!r} was modified")
        return data

    # -- recovery -------------------------------------------------------

    def reconcile(self) -> ReconcileReport:
        """Classify staging rows against the commit (crash-window reconciliation)."""
        registered = {record.idempotency_key for record in self._store.list_imports()}
        completed = 0
        rejected = 0
        orphans: list[str] = []
        for staging in self._store.list_staging():
            if staging.state is ImportState.REJECTED:
                rejected += 1
                continue
            if staging.state is ImportState.COMPLETED:
                completed += 1
                continue
            if staging.idempotency_key in registered:
                # Import committed but the finishing update was lost: dangling, safe.
                self._store.update_staging(staging.idempotency_key, state=ImportState.COMPLETED)
                completed += 1
                continue
            orphans.append(staging.idempotency_key)
        return ReconcileReport(
            registered=len(registered),
            completed_staging=completed,
            rejected_staging=rejected,
            orphan_staging=tuple(orphans),
        )

    def discard_staging(self, idempotency_key: str) -> bool:
        """Drop an orphan staging attempt (its files are not a registered import)."""
        staging = self._store.get_staging(idempotency_key)
        if staging is None:
            return False
        if self._store.get_import_by_key(idempotency_key) is not None:
            # A committed import is never discarded by a staging cleanup.
            return False
        self._store.update_staging(idempotency_key, state=ImportState.REJECTED, detail="discarded")
        return True

    # -- internals ------------------------------------------------------

    def _require_registered(self, platform_id: str) -> ImportRecord:
        record = self._store.get_import(platform_id)
        if record is None:
            raise ImportNotFoundError(f"no registered import {platform_id!r}")
        return record

    def _publish(
        self,
        staging_dir: Path,
        entries: tuple[StagedEntry, ...],
        bundle: ImportBundlePort,
    ) -> tuple[StagedEntry, ...]:
        """Write every validated entry atomically; re-verify the digest on write."""
        for entry in entries:
            try:
                data = bundle.read_bytes(entry.relative_path, max_bytes=self._quota.max_file_bytes)
            except ImportSizeLimitError as exc:
                raise ImportRejectedError(
                    RejectionReason.QUOTA_FILE_BYTES, path=entry.relative_path, detail=str(exc)
                ) from exc
            if len(data) != entry.size_bytes or _sha256_hex(data) != entry.sha256:
                raise ImportRejectedError(
                    RejectionReason.DIGEST_MISMATCH,
                    path=entry.relative_path,
                    detail="package changed between validation and publish",
                )
            _atomic_write_bytes(staging_dir / entry.relative_path, data)
        return entries

    def _result_from_record(
        self, record: ImportRecord, *, request: ImportRequest, duplicate: bool
    ) -> ImportResult:
        projection = TaskProjectionRequest(
            source_namespace=record.source_namespace,
            source_id=record.source_id,
            platform_id=record.platform_id,
            source_revision=record.source_revision,
        )
        return ImportResult(
            platform_id=record.platform_id,
            idempotency_key=record.idempotency_key,
            state=ImportState.REGISTERED,
            registered=True,
            duplicate=duplicate,
            binding=replace(record.binding),
            mapping=record.mapping,
            provenance=record.provenance,
            files=record.files,
            references=tuple(request.manifest.references),
            task_projection=projection,
            staging_dir=str(self._import_root / record.imports_leaf / record.platform_id),
            rejection=None,
            execution_authority=False,
            adopted_external_authority=False,
            auto_executed=False,
        )
