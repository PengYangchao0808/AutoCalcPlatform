"""Transfer verify → publish → availability pipeline (B02-4 / plan todo 14).

The legacy remote catalog prefetch (:mod:`acp.results.remote_structure_cache`)
downloaded a file straight to its final cache slot: whatever bytes the SFTP
read returned were renamed into place, with no content/size verification, and
only an attempt sidecar recorded *which* attempt the entry belonged to.  A
truncated read, a stale attempt receipt or a revoked permission therefore
became a silently "ready" cache entry.

This module owns the **verification half** of the prepare operation (todo 13
owns the persistent state machine).  Every accepted artifact is:

1. downloaded into a ``.tmp`` file inside the guarded cache directory,
2. checked against a **frozen expected digest / size** (from the completion
   receipt, a manifest digest, or a controlled source-side baseline — *never*
   re-derived from the just-downloaded bytes),
3. identity-checked (job / attempt / relative path),
4. re-authorized,
5. published atomically with the cache's fence / generation re-check, and
6. recorded in a persistent publication table before availability is committed.

Rejections (each recorded, none ever visible as ``ready``):

* **stale attempt** — a receipt issued for an attempt the cache has advanced
  past (or an older attempt arriving late).
* **partial file** — the downloaded size does not equal the frozen size.
* **digest mismatch** — the downloaded bytes do not match the frozen digest.
* **permission revoked** — the injected authorization denies the download or
  the publish, or the source classifies the SFTP failure as a permission error.
* **late transfer** — the purge generation or attempt fence moved between the
  download and the publish; the write is discarded and recorded.

**Isolation (hard rule).**  ``src/acp_services/**`` never imports the legacy
application.  The real download / publish / write-back are injected through the
:class:`TransferSourcePort`, :class:`TransferCachePort`,
:class:`TransferAuthorizationPort` and :class:`TransferAdoptionPort` seams; the
legacy adapters live in ``acp.services_data_bootstrap``.

**Historical adoption (review P1-6).**  Old artifacts with no digest are never
adopted by hashing the downloaded bytes and calling that "expected".  The
expected value must come from a frozen receipt, an already-recorded manifest
digest, or a controlled source-side snapshot whose provenance and verification
level are recorded with the baseline (``adopted_from``).  When nothing trusted
exists an attempt artifact is rejected (terminal), never left pending forever.

Author: ACP Services (B lane, todo 14).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from acp_services.data.contracts import Availability
from acp_services.data.operations import (
    PrepareExecutionResult,
    PrepareLease,
)

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies
    "ResourceClass",
    "VerificationLevel",
    "PublicationState",
    "RejectionCode",
    "SourceFailureKind",
    # records
    "TransferReceipt",
    "ExpectedDigest",
    "ByteRange",
    "TransferRequest",
    "FenceSnapshot",
    "SourceRead",
    "PublishOutcome",
    "TransferResult",
    "PublicationRecord",
    "ReconcileReport",
    # errors
    "TransferError",
    "TransferSourceError",
    # ports
    "TransferSourcePort",
    "TransferAuthorizationPort",
    "TransferCachePort",
    "TransferAdoptionPort",
    # store + pipeline
    "TransferPublicationStore",
    "TransferPipeline",
    "TransferPrepareExecutor",
    # helpers
    "sha256_bytes",
    "normalize_relative_path",
]

#: Suffix of an in-flight download that has not been verified/published yet.
TMP_SUFFIX = ".tmp"
#: Accepted digest length (hex characters of sha256).
_SHA256_HEX_LEN = 64


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ResourceClass(str, Enum):
    """How an artifact is expected to behave for read-consistency purposes.

    ``ATTEMPT_ARTIFACT`` is a one-shot immutable result bound to one attempt:
    its expected digest/size come from a frozen receipt or manifest.  ``MUTABLE``
    resources (logs, candidates, organization files) are *not* pinned by digest;
    they are read under a revision / generation / byte-range consistency
    protocol instead.
    """

    ATTEMPT_ARTIFACT = "attempt_artifact"
    MUTABLE = "mutable"


class VerificationLevel(str, Enum):
    """How strongly an artifact's expected value was established.

    * ``VERIFIED`` — frozen receipt / producer-recorded digest for a new result.
    * ``EXISTING_DIGEST`` — a historical artifact whose valid digest was already
      recorded (manifest product, prior publication).
    * ``ADOPTED_BASELINE`` — a historical no-digest artifact whose baseline was
      obtained through a controlled source-side snapshot/verification flow.
    * ``REVISION_CONSISTENT`` — a mutable resource accepted under the
      revision/generation read-consistency protocol (no digest pin).
    * ``UNVERIFIED`` — no trusted expected value exists; **never** published
      ready.
    """

    VERIFIED = "verified"
    EXISTING_DIGEST = "existing_digest"
    ADOPTED_BASELINE = "adopted_baseline"
    REVISION_CONSISTENT = "revision_consistent"
    UNVERIFIED = "unverified"


class PublicationState(str, Enum):
    """Persisted state of one publication record (crash-window vocabulary)."""

    PUBLISHED = "published"
    #: File present in the cache but absent from the publication table.
    UNREGISTERED = "unregistered"
    #: Publication record present but the cached file is gone.
    MISSING = "missing"
    #: A late transfer discarded by the fence/generation re-check.
    DISCARDED = "discarded"


class RejectionCode(str, Enum):
    """Why a transfer was refused (recorded, never visible as ready)."""

    STALE_ATTEMPT = "stale_attempt"
    IDENTITY_MISMATCH = "identity_mismatch"
    PARTIAL_FILE = "partial_file"
    DIGEST_MISMATCH = "digest_mismatch"
    PERMISSION_REVOKED = "permission_revoked"
    LATE_TRANSFER = "late_transfer"
    MISSING_EXPECTED_DIGEST = "missing_expected_digest"
    STALE_REVISION = "stale_revision"
    SOURCE_NOT_FOUND = "source_not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"


class SourceFailureKind(str, Enum):
    """Classification of a source-read failure (SFTP failure taxonomy)."""

    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    TRANSIENT = "transient"
    STALE_ATTEMPT = "stale_attempt"


#: Rejections that may succeed on a later retry (transient corruption / a newer
#: generation).  The rest are permanent for this request.
_RETRYABLE_REJECTIONS = frozenset(
    {
        RejectionCode.PARTIAL_FILE,
        RejectionCode.DIGEST_MISMATCH,
        RejectionCode.LATE_TRANSFER,
        RejectionCode.SOURCE_UNAVAILABLE,
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TransferError(RuntimeError):
    """Base error for the transfer pipeline."""


class TransferSourceError(TransferError):
    """A source read failed; :attr:`kind` drives the pipeline's classification."""

    def __init__(self, kind: SourceFailureKind, message: str = "") -> None:
        super().__init__(message or kind.value)
        self.kind = kind


# ---------------------------------------------------------------------------
# Frozen records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransferReceipt:
    """A frozen completion receipt / manifest entry for one produced artifact.

    The receipt is the *provenance* of the expected digest: it is issued by the
    producer (or read from the frozen source manifest) and its job/attempt/path
    identity is re-checked against the live request.  An old-attempt receipt is
    rejected, never silently trusted.
    """

    job_id: str
    attempt: int
    relative_path: str
    sha256: str
    size_bytes: int | None = None
    source: str = "completion_receipt"

    def __post_init__(self) -> None:
        if not str(self.job_id).strip():
            raise ValueError("receipt job_id must be non-empty")
        if int(self.attempt) < 1:
            raise ValueError("receipt attempt must be >= 1")
        if not str(self.relative_path).strip():
            raise ValueError("receipt relative_path must be non-empty")
        if not str(self.sha256).strip():
            raise ValueError("receipt sha256 must be non-empty")


@dataclass(frozen=True)
class ExpectedDigest:
    """A trusted expected value for one artifact.

    ``source`` records *where* the value came from and ``verification_level``
    how strong that provenance is.  The pipeline only ever compares against this
    value — it never substitutes a digest computed from the downloaded bytes.
    """

    sha256: str
    size_bytes: int | None = None
    source: str = "receipt"
    verification_level: VerificationLevel = VerificationLevel.VERIFIED

    def __post_init__(self) -> None:
        if not str(self.sha256).strip():
            raise ValueError("expected sha256 must be non-empty")

    def matches(self, *, sha256: str, size_bytes: int | None) -> bool:
        """True when the observed content matches this expected value."""
        if not hmac.compare_digest(str(self.sha256).lower(), str(sha256).lower()):
            return False
        if self.size_bytes is not None and size_bytes is not None:
            return int(self.size_bytes) == int(size_bytes)
        return True


@dataclass(frozen=True)
class ByteRange:
    """A bounded byte range read (mutable resources / large files)."""

    offset: int
    limit: int

    def __post_init__(self) -> None:
        if int(self.offset) < 0 or int(self.limit) <= 0:
            raise ValueError("byte range requires offset >= 0 and limit > 0")


@dataclass(frozen=True)
class TransferRequest:
    """One artifact transfer request (identity + policy + expected value).

    ``receipt`` pins a newly produced artifact.  ``expected`` carries an
    already-resolved expected value (e.g. a historical manifest digest, or an
    adopted baseline).  When both are absent the pipeline may consult the
    injected adoption port; if that too yields nothing an attempt artifact is
    rejected (never hashed-then-trusted).
    """

    job_id: str
    attempt: int
    relative_path: str
    resource_class: ResourceClass = ResourceClass.ATTEMPT_ARTIFACT
    receipt: TransferReceipt | None = None
    expected: ExpectedDigest | None = None
    expected_size: int | None = None
    resource_generation: int = 0
    authorization_version: int = 0
    binding_id: str = ""
    basis_revision: int = 0
    expected_revision: int = 0
    byte_range: ByteRange | None = None
    payload: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not str(self.job_id).strip():
            raise ValueError("job_id must be non-empty")
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")
        if not str(self.relative_path).strip():
            raise ValueError("relative_path must be non-empty")

    @property
    def normalized_path(self) -> str:
        return normalize_relative_path(self.relative_path)


@dataclass(frozen=True)
class FenceSnapshot:
    """The cache's attempt fence + purge generation for one job."""

    attempt: int | None
    generation: int


@dataclass(frozen=True)
class SourceRead:
    """Bytes read from the source plus the version observed during the read."""

    data: bytes
    size_bytes: int
    revision: int = 0
    generation: int = 0


@dataclass(frozen=True)
class PublishOutcome:
    """The cache's atomic-publish verdict (fence/generation re-checked)."""

    published: bool
    cached_path: str | None = None
    discarded: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TransferResult:
    """Outcome of one :meth:`TransferPipeline.prepare` call."""

    accepted: bool
    relative_path: str
    state: PublicationState
    rejection: RejectionCode | None = None
    detail: str = ""
    sha256: str | None = None
    size_bytes: int | None = None
    verification_level: VerificationLevel = VerificationLevel.UNVERIFIED
    adopted_from: str = ""
    retryable: bool = False

    @property
    def prepared(self) -> bool:
        return self.accepted


@dataclass(frozen=True)
class PublicationRecord:
    """The persistent fact that one artifact was published (or discarded)."""

    job_id: str
    attempt: int
    relative_path: str
    resource_class: str
    state: str
    sha256: str | None
    size_bytes: int | None
    resource_generation: int
    basis_revision: int
    verification_level: str
    adopted_from: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReconcileReport:
    """Crash-window reconciliation between the cache and the publication table.

    * ``unregistered`` — a file exists in the cache with **no** publication row
      ("file published, DB unregistered"): it must not be served as ready until
      reconciled.
    * ``missing`` — a publication row says published but the file is gone
      ("DB record, file missing").
    * ``late`` — publication rows left in the discarded state by a late worker.
    """

    unregistered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    late: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Injected ports
# ---------------------------------------------------------------------------


@runtime_checkable
class TransferSourcePort(Protocol):
    """Reads artifact bytes from the remote source (the real SFTP fetch)."""

    def read(
        self,
        job_id: str,
        attempt: int,
        relative_path: str,
        *,
        byte_range: ByteRange | None = None,
    ) -> SourceRead:
        """Return the artifact bytes + observed version.

        Raises:
            TransferSourceError: With the classified failure kind.
        """
        ...


@runtime_checkable
class TransferAuthorizationPort(Protocol):
    """Re-checks authorization at download time and at publish time."""

    def authorize(
        self,
        *,
        job_id: str,
        attempt: int,
        relative_path: str,
        action: str,
        authorization_version: int,
    ) -> bool:
        """Return True when *action* (``download`` / ``publish``) is allowed."""
        ...


@runtime_checkable
class TransferCachePort(Protocol):
    """The real cache mechanism (traversal guard / fence / generation / publish).

    Implemented by the legacy-side adapter delegating to
    ``acp.results.remote_structure_cache.RemoteStructureCache``; the Services
    side never re-implements the path guard, the fence or the atomic write.
    """

    def guarded_path(self, job_id: str, relative_path: str) -> Path:
        """Return the cache path, refusing any traversal escape."""
        ...

    def fence(self, job_id: str) -> FenceSnapshot:
        """Return the persisted attempt + purge generation for *job_id*."""
        ...

    def publish(
        self,
        job_id: str,
        relative_path: str,
        tmp_path: str,
        *,
        attempt: int,
        expected_generation: int,
    ) -> PublishOutcome:
        """Atomically move the verified temp file into the cache.

        Re-checks the purge generation and attempt fence; a moved fence discards
        the write (``PublishOutcome.discarded``).
        """
        ...

    def exists(self, job_id: str, relative_path: str) -> bool:
        """True when the cached file is present on disk."""
        ...

    def list_files(self, job_id: str) -> tuple[str, ...]:
        """List the cached relative paths for *job_id* (excludes sidecars)."""
        ...

    def push_paths(self, job_id: str, relative_paths: Iterable[str]) -> list[str]:
        """Write cached files back to the remote job directory (write-back)."""
        ...


@runtime_checkable
class TransferAdoptionPort(Protocol):
    """Resolves a trusted expected value for a historical (no-digest) artifact.

    The implementation must obtain the value from an **existing manifest
    digest** or a **controlled source-side snapshot / verification** flow and
    label the provenance — never from the bytes it is about to validate.
    """

    def resolve(self, request: TransferRequest) -> ExpectedDigest | None:
        """Return the expected value, or ``None`` when none can be trusted."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex sha256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def normalize_relative_path(relative_path: str) -> str:
    """Normalise a task-relative path for identity comparison."""
    return str(relative_path).replace("\\", "/").strip("/")


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _reject(
    request: TransferRequest,
    code: RejectionCode,
    detail: str = "",
) -> TransferResult:
    return TransferResult(
        accepted=False,
        relative_path=request.normalized_path,
        state=PublicationState.DISCARDED
        if code is RejectionCode.LATE_TRANSFER
        else PublicationState.MISSING,
        rejection=code,
        detail=detail,
        retryable=code in _RETRYABLE_REJECTIONS,
    )


# ---------------------------------------------------------------------------
# Persistent publication table
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_transfer_publications (
    job_id TEXT NOT NULL,
    job_attempt INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    resource_class TEXT NOT NULL,
    state TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    resource_generation INTEGER NOT NULL DEFAULT 0,
    basis_revision INTEGER NOT NULL DEFAULT 0,
    verification_level TEXT NOT NULL DEFAULT 'unverified',
    adopted_from TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, job_attempt, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_transfer_publications_job
    ON data_transfer_publications(job_id, job_attempt);
"""


class TransferPublicationStore:
    """SQLite authority for per-artifact publication records.

    The store is the *persistent publication record* the pipeline commits before
    availability is reported.  It also owns crash-window reconciliation:
    "file published, DB unregistered", "DB record, file missing" and "old worker
    late" are all derivable from it, and none of them deletes data or reports an
    unconfirmed artifact as ready.
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

    def __enter__(self) -> TransferPublicationStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- helpers --------------------------------------------------------
    def _row_to_record(self, row: sqlite3.Row) -> PublicationRecord:
        return PublicationRecord(
            job_id=row["job_id"],
            attempt=int(row["job_attempt"]),
            relative_path=row["relative_path"],
            resource_class=row["resource_class"],
            state=row["state"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            resource_generation=int(row["resource_generation"]),
            basis_revision=int(row["basis_revision"]),
            verification_level=row["verification_level"],
            adopted_from=row["adopted_from"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # -- writes ---------------------------------------------------------
    def record(self, record: PublicationRecord) -> PublicationRecord:
        """Upsert one publication record (idempotent on the identity triple)."""
        iso = _utc_now_iso()
        created = record.created_at or iso
        with self._lock:
            self._conn.execute(
                "INSERT INTO data_transfer_publications ("
                "job_id, job_attempt, relative_path, resource_class, state, sha256, "
                "size_bytes, resource_generation, basis_revision, verification_level, "
                "adopted_from, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(job_id, job_attempt, relative_path) DO UPDATE SET "
                "state=excluded.state, sha256=excluded.sha256, size_bytes=excluded.size_bytes, "
                "resource_generation=excluded.resource_generation, "
                "basis_revision=excluded.basis_revision, "
                "verification_level=excluded.verification_level, "
                "adopted_from=excluded.adopted_from, updated_at=excluded.updated_at",
                (
                    record.job_id,
                    int(record.attempt),
                    record.relative_path,
                    record.resource_class,
                    record.state,
                    record.sha256,
                    record.size_bytes,
                    int(record.resource_generation),
                    int(record.basis_revision),
                    record.verification_level,
                    record.adopted_from,
                    created,
                    iso,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM data_transfer_publications "
                "WHERE job_id=? AND job_attempt=? AND relative_path=?",
                (record.job_id, int(record.attempt), record.relative_path),
            ).fetchone()
            assert row is not None
            return self._row_to_record(row)

    def update_state(self, job_id: str, attempt: int, relative_path: str, state: str) -> None:
        """Change the persisted reconciliation state of one record."""
        with self._lock:
            self._conn.execute(
                "UPDATE data_transfer_publications SET state=?, updated_at=? "
                "WHERE job_id=? AND job_attempt=? AND relative_path=?",
                (state, _utc_now_iso(), job_id, int(attempt), relative_path),
            )

    # -- reads ----------------------------------------------------------
    def get(self, job_id: str, attempt: int, relative_path: str) -> PublicationRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_transfer_publications "
                "WHERE job_id=? AND job_attempt=? AND relative_path=?",
                (job_id, int(attempt), relative_path),
            ).fetchone()
            return self._row_to_record(row) if row is not None else None

    def list_by_job(self, job_id: str) -> tuple[PublicationRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM data_transfer_publications WHERE job_id=? "
                "ORDER BY job_attempt ASC, relative_path ASC",
                (job_id,),
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    def list_all(self, *, limit: int = 5000) -> tuple[PublicationRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM data_transfer_publications "
                "ORDER BY job_id ASC, job_attempt ASC, relative_path ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return tuple(self._row_to_record(row) for row in rows)

    # -- reconciliation -------------------------------------------------
    def reconcile(
        self,
        cache: TransferCachePort,
        *,
        job_ids: Iterable[str] | None = None,
    ) -> ReconcileReport:
        """Reconcile the cache against the publication table.

        Never deletes or reports ready: it classifies the three crash windows so
        a caller can repair them explicitly.
        """
        records = self.list_all()
        wanted = set(job_ids) if job_ids is not None else {r.job_id for r in records}
        missing: list[str] = []
        late: list[str] = []
        registered: set[tuple[str, str]] = set()
        for record in records:
            if job_ids is not None and record.job_id not in wanted:
                continue
            registered.add((record.job_id, record.relative_path))
            if record.state == PublicationState.DISCARDED.value:
                late.append(f"{record.job_id}:{record.relative_path}")
                continue
            if record.state == PublicationState.PUBLISHED.value and not cache.exists(
                record.job_id, record.relative_path
            ):
                self.update_state(
                    record.job_id,
                    record.attempt,
                    record.relative_path,
                    PublicationState.MISSING.value,
                )
                missing.append(f"{record.job_id}:{record.relative_path}")
        unregistered: list[str] = []
        for job_id in sorted(wanted):
            try:
                paths = cache.list_files(job_id)
            except Exception:  # pragma: no cover - a broken cache must not abort reconcile
                logger.warning("reconcile could not list cache for %s", job_id, exc_info=True)
                continue
            for rel in paths:
                if (job_id, normalize_relative_path(rel)) not in registered:
                    unregistered.append(f"{job_id}:{normalize_relative_path(rel)}")
        return ReconcileReport(
            unregistered=tuple(unregistered),
            missing=tuple(missing),
            late=tuple(late),
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TransferPipeline:
    """Download → verify → re-authorize → atomic publish → record.

    Args:
        cache: The real cache mechanism (guard / fence / generation / publish).
        source: The remote byte source.
        authorization: The re-checked authorization port.
        publications: The persistent publication record store.
        adoption: Optional historical expected-value resolver.
        identity_check: Optional re-read of the authoritative identity, called
            after verification and immediately before the publish decision.  It
            returns a stable refusal token (or ``None`` when still valid).  The
            cache adapter re-runs the same check under its generation lock so
            the check-to-``os.replace`` window is closed by the publisher.
        clock: Injectable wall clock (record timestamps).

    Raises:
        ValueError: When the authorization port is omitted.  There is no
            ``None``-means-allow fallback: production assembly must inject an
            explicit policy (local-single supplies its own).
    """

    def __init__(
        self,
        *,
        cache: TransferCachePort,
        source: TransferSourcePort,
        authorization: TransferAuthorizationPort,
        publications: TransferPublicationStore,
        adoption: TransferAdoptionPort | None = None,
        identity_check: Callable[[], str | None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if authorization is None:
            raise ValueError(
                "TransferPipeline requires an explicit authorization port "
                "(no None-means-allow fallback)"
            )
        self._cache = cache
        self._source = source
        self._authorization = authorization
        self._publications = publications
        self._adoption = adoption
        self._identity_check = identity_check
        self._clock = clock or time.time

    # -- public ---------------------------------------------------------
    def prepare(self, request: TransferRequest) -> TransferResult:
        """Run the full transfer pipeline for one artifact request."""
        # 1. Receipt identity + attempt (a stale-attempt receipt is refused).
        if request.receipt is not None:
            if int(request.receipt.attempt) != int(request.attempt):
                return self._record_rejection(
                    request,
                    RejectionCode.STALE_ATTEMPT,
                    (
                        f"receipt attempt {request.receipt.attempt} != "
                        f"request attempt {request.attempt}"
                    ),
                )
            if (
                request.receipt.job_id != request.job_id
                or normalize_relative_path(request.receipt.relative_path) != request.normalized_path
            ):
                return self._record_rejection(
                    request, RejectionCode.IDENTITY_MISMATCH, "receipt identity mismatch"
                )

        # 2. Fence before download: a stale attempt never reaches the node.
        fence = self._cache.fence(request.job_id)
        if fence.attempt is not None and int(request.attempt) < int(fence.attempt):
            return self._record_rejection(
                request,
                RejectionCode.STALE_ATTEMPT,
                f"cache fence is at attempt {fence.attempt}, request is {request.attempt}",
            )

        # 3. Authorization before download.
        if not self._authorized(request, "download"):
            return self._record_rejection(
                request, RejectionCode.PERMISSION_REVOKED, "download authorization denied"
            )

        # 4. Resolve the *trusted* expected value (never from the download).
        expected = self._resolve_expected(request)
        if request.resource_class is ResourceClass.ATTEMPT_ARTIFACT and expected is None:
            return self._record_rejection(
                request,
                RejectionCode.MISSING_EXPECTED_DIGEST,
                "no trusted expected digest (receipt/manifest/adopted baseline)",
            )

        # 5. Download into a .tmp file inside the guarded cache directory.
        target = self._cache.guarded_path(request.job_id, request.normalized_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=TMP_SUFFIX)
        os.close(fd)
        try:
            try:
                source_read = self._source.read(
                    request.job_id,
                    request.attempt,
                    request.normalized_path,
                    byte_range=request.byte_range,
                )
            except TransferSourceError as exc:
                code = self._source_error_code(exc.kind)
                return self._record_rejection(request, code, str(exc))

            data = bytes(source_read.data)
            size = len(data)
            with open(tmp_path, "wb") as handle:
                handle.write(data)

            # 6. Verify identity/consistency against the frozen expected value.
            if request.resource_class is ResourceClass.MUTABLE:
                if request.expected_revision and int(source_read.revision) != int(
                    request.expected_revision
                ):
                    return self._record_rejection(
                        request,
                        RejectionCode.STALE_REVISION,
                        f"source revision {source_read.revision} != expected "
                        f"{request.expected_revision}",
                    )
                verification_level = VerificationLevel.REVISION_CONSISTENT
                adopted_from = "revision_consistency"
            else:
                assert expected is not None
                expected_size = expected.size_bytes
                if expected_size is not None and int(size) != int(expected_size):
                    return self._record_rejection(
                        request,
                        RejectionCode.PARTIAL_FILE,
                        f"size {size} != expected {expected_size}",
                    )
                observed = sha256_bytes(data)
                if not expected.matches(sha256=observed, size_bytes=size):
                    return self._record_rejection(
                        request,
                        RejectionCode.DIGEST_MISMATCH,
                        f"observed {observed} != expected {expected.sha256}",
                    )
                verification_level = expected.verification_level
                adopted_from = expected.source

            observed_sha = sha256_bytes(data)
            observed_size = size

            # 7. Identity before publish: a rerun / rebind / revocation / purge
            # observed after the download must not publish.  The cache adapter
            # repeats this check under its generation lock immediately before
            # ``os.replace``, closing the check-to-publish window.
            identity_reason = self._identity_reason()
            if identity_reason:
                return self._record_rejection(
                    request,
                    _identity_rejection_code(identity_reason),
                    identity_reason,
                )

            # 8. Authorization before publish (a revocation between the two).
            if not self._authorized(request, "publish"):
                return self._record_rejection(
                    request, RejectionCode.PERMISSION_REVOKED, "publish authorization denied"
                )

            # 9. Atomic publish; the fence/generation is re-checked by the cache.
            outcome = self._cache.publish(
                request.job_id,
                request.normalized_path,
                tmp_path,
                attempt=request.attempt,
                expected_generation=fence.generation,
            )
        finally:
            _remove_tmp(tmp_path)

        if not outcome.published:
            if outcome.discarded:
                self._record_discard(request, outcome.reason)
                return self._record_rejection(
                    request,
                    RejectionCode.LATE_TRANSFER,
                    outcome.reason or "generation/attempt moved before publish",
                )
            return self._record_rejection(
                request, RejectionCode.SOURCE_UNAVAILABLE, outcome.reason or "publish failed"
            )

        # 10. Persistent publication record (before availability is committed).
        record = PublicationRecord(
            job_id=request.job_id,
            attempt=request.attempt,
            relative_path=request.normalized_path,
            resource_class=request.resource_class.value,
            state=PublicationState.PUBLISHED.value,
            sha256=observed_sha,
            size_bytes=observed_size,
            resource_generation=int(request.resource_generation),
            basis_revision=int(request.basis_revision),
            verification_level=verification_level.value,
            adopted_from=adopted_from,
            created_at=_utc_now_iso(),
            updated_at=_utc_now_iso(),
        )
        self._publications.record(record)
        return TransferResult(
            accepted=True,
            relative_path=request.normalized_path,
            state=PublicationState.PUBLISHED,
            sha256=observed_sha,
            size_bytes=observed_size,
            verification_level=verification_level,
            adopted_from=adopted_from,
        )

    def push_paths(self, job_id: str, relative_paths: Iterable[str]) -> list[str]:
        """Write cached files back to the remote job directory (unchanged)."""
        return self._cache.push_paths(job_id, relative_paths)

    # -- internals ------------------------------------------------------
    def _identity_reason(self) -> str | None:
        if self._identity_check is None:
            return None
        return self._identity_check()

    def _authorized(self, request: TransferRequest, action: str) -> bool:
        return bool(
            self._authorization.authorize(
                job_id=request.job_id,
                attempt=request.attempt,
                relative_path=request.normalized_path,
                action=action,
                authorization_version=int(request.authorization_version),
            )
        )

    def _resolve_expected(self, request: TransferRequest) -> ExpectedDigest | None:
        if request.expected is not None:
            return request.expected
        if request.receipt is not None:
            return ExpectedDigest(
                sha256=request.receipt.sha256,
                size_bytes=request.receipt.size_bytes
                if request.receipt.size_bytes is not None
                else request.expected_size,
                source=request.receipt.source,
                verification_level=VerificationLevel.VERIFIED,
            )
        if self._adoption is not None:
            return self._adoption.resolve(request)
        return None

    @staticmethod
    def _source_error_code(kind: SourceFailureKind) -> RejectionCode:
        return {
            SourceFailureKind.PERMISSION_DENIED: RejectionCode.PERMISSION_REVOKED,
            SourceFailureKind.NOT_FOUND: RejectionCode.SOURCE_NOT_FOUND,
            SourceFailureKind.STALE_ATTEMPT: RejectionCode.STALE_ATTEMPT,
            SourceFailureKind.TRANSIENT: RejectionCode.SOURCE_UNAVAILABLE,
        }[kind]

    def _record_rejection(
        self, request: TransferRequest, code: RejectionCode, detail: str
    ) -> TransferResult:
        logger.info(
            "transfer rejected %s/%s@%d: %s (%s)",
            request.job_id,
            request.normalized_path,
            request.attempt,
            code.value,
            detail,
        )
        return _reject(request, code, detail)

    def _record_discard(self, request: TransferRequest, reason: str) -> None:
        self._publications.record(
            PublicationRecord(
                job_id=request.job_id,
                attempt=request.attempt,
                relative_path=request.normalized_path,
                resource_class=request.resource_class.value,
                state=PublicationState.DISCARDED.value,
                sha256=None,
                size_bytes=None,
                resource_generation=int(request.resource_generation),
                basis_revision=int(request.basis_revision),
                verification_level=VerificationLevel.UNVERIFIED.value,
                adopted_from="",
                created_at=_utc_now_iso(),
                updated_at=_utc_now_iso(),
            )
        )
        logger.info(
            "late transfer discarded for %s/%s@%d: %s",
            request.job_id,
            request.normalized_path,
            request.attempt,
            reason,
        )


def _identity_rejection_code(reason: str) -> RejectionCode:
    """Map a stable identity-refusal token onto the recorded rejection code."""
    if reason.startswith("stale_attempt"):
        return RejectionCode.STALE_ATTEMPT
    if reason.startswith("authorization_revoked") or reason.startswith("permission"):
        return RejectionCode.PERMISSION_REVOKED
    return RejectionCode.IDENTITY_MISMATCH


def _remove_tmp(tmp_path: str) -> None:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Prepare-operation executor
# ---------------------------------------------------------------------------


class TransferPrepareExecutor:
    """Run the transfer pipeline as the todo-13 prepare-operation executor.

    The worker owns the state machine: on acceptance it calls
    ``PrepareOperationService.complete`` (availability commit) with the verified
    size/digest; on rejection it calls ``fail`` with the rejection code and the
    derived retryability.  This adapter never marks anything ready itself.
    """

    def __init__(
        self,
        pipeline: TransferPipeline,
        request_factory: Callable[[PrepareLease], TransferRequest],
    ) -> None:
        self._pipeline = pipeline
        self._request_factory = request_factory

    def execute(self, lease: PrepareLease) -> PrepareExecutionResult:
        request = self._request_factory(lease)
        result = self._pipeline.prepare(request)
        if result.accepted:
            return PrepareExecutionResult(
                prepared=True,
                availability=Availability.READY,
                detail=f"published:{result.relative_path}",
                retryable=False,
                size_bytes=result.size_bytes,
                sha256=result.sha256,
            )
        assert result.rejection is not None
        detail = result.rejection.value
        if result.detail:
            detail = f"{detail}:{result.detail}"
        return PrepareExecutionResult(
            prepared=False,
            availability=Availability.UNAVAILABLE,
            detail=detail,
            retryable=result.retryable,
        )
