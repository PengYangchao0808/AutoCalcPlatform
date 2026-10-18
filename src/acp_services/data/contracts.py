"""Typed Data resource port — the single write/read entry for data resources.

B02-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 11).  This module
freezes the **shape** of the Data resource seam and nothing else:

* :class:`DataResourcePort` is a PEP 544 ``Protocol`` with **explicit,
  per-domain methods** — never an arbitrary ``call(name, dict)`` dispatcher and
  never an open keyword catch-all.  The six frozen domains are:

  1. ``inputs``     — ``register_input`` / ``validate_input``
  2. ``artifacts``  — ``publish_manifest`` / ``read_manifest``
  3. ``sources``    — ``upsert_sources`` / ``list_sources`` / ``source_uid``
  4. ``candidates`` — ``save_candidates`` / ``read_candidates`` / ``list_candidates``
  5. ``transfers``  — ``upload_paths`` / ``download_file``
  6. ``cache``      — ``cache_entry`` / ``resource_availability``

* Frozen typed records for every request/result (``@dataclass(frozen=True)``),
  plus the availability / freshness / basis_revision vocabulary from the frozen
  first-slice contract (``docs/reference/next-change/
  ACP_Services_First_Slice_Contracts.md`` §2).

The vocabulary preserves the §2.3 distinctions exactly:

* ``availability`` answers *is the resource body prepared and readable* —
  ``absent`` / ``preparing`` / ``ready`` / ``unavailable``.  It does **not**
  express projection age.
* ``freshness`` answers *has the projection caught up with the authoritative
  state* — ``current`` / ``stale`` / ``unknown``.  It does **not** mean the
  resource is unreadable.
* ``basis_revision`` records *which owner revision the projection was built
  against* — it is not a clock and different owner revisions never substitute
  for one another.

**Isolation (hard rule).** ``src/acp_services/**`` must never import the
legacy application package — the isolation scanner
(``tests/test_acp_services_isolation.py``) enforces it.  This module is
therefore stdlib-only; the *delegation adapters* that wrap the existing
implementations live on the other side of the seam
(``acp.services_data_bootstrap``).  This is a **seam, not a switch**: behaviour,
the authoritative ``RESULT/frame_candidates.json`` file, ``source_uid_for``,
``expected_revision`` and legacy manifest read compatibility are all preserved
by the delegating adapters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, NewType, Protocol, runtime_checkable

__all__ = [
    "CONTRACT_VERSION",
    "Availability",
    "Freshness",
    "ResourceState",
    "ResourceKind",
    "TransferDirection",
    "BasisRevision",
    "ResourceRevision",
    "DataAvailability",
    "PrepareOperation",
    "InputRegistration",
    "InputRecord",
    "InputValidationRequest",
    "InputValidation",
    "ManifestPublish",
    "ManifestPublication",
    "ManifestRead",
    "ManifestSnapshot",
    "SourceUpsert",
    "CandidateWrite",
    "CandidateSnapshot",
    "RemoteJobRef",
    "TransferUpload",
    "TransferReport",
    "TransferDownload",
    "TransferResult",
    "CacheLookup",
    "CacheEntry",
    "AvailabilityQuery",
    "DataRevisionConflictError",
    "DataResourceUnavailableError",
    "DataResourcePort",
]

#: Frozen first-slice contract version this module implements (§0.1).
CONTRACT_VERSION = 1


# ---------------------------------------------------------------------------
# Vocabulary (§2.2 / §2.3)
# ---------------------------------------------------------------------------


class Availability(str, Enum):
    """Whether the resource body itself is prepared and readable (§2.3)."""

    ABSENT = "absent"
    PREPARING = "preparing"
    READY = "ready"
    UNAVAILABLE = "unavailable"


class Freshness(str, Enum):
    """Whether the projection has caught up with the authoritative state (§2.3)."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class ResourceState(str, Enum):
    """Lifecycle of one prepare operation (§2.2 ``state``)."""

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceKind(str, Enum):
    """The six data domains plus the manifest artifact (§2.2 ``resource_kind``)."""

    INPUT = "input"
    ARTIFACT = "artifact"
    MANIFEST = "manifest"
    SOURCE = "source"
    CANDIDATE = "candidate"
    TRANSFER = "transfer"
    CACHE = "cache"


class TransferDirection(str, Enum):
    """Direction of a transfer operation."""

    UPLOAD = "upload"
    DOWNLOAD = "download"


#: The owner revision a projection was built against (§2.3).  A distinct name
#: keeps the "revision, not a clock" semantics visible at call sites.
BasisRevision = NewType("BasisRevision", int)


# ---------------------------------------------------------------------------
# Revisions and availability (§2.2 / §2.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceRevision:
    """A resource revision pair: the authoritative revision + the basis revision.

    ``revision`` is the current authoritative revision; ``basis_revision`` is
    the owner revision a projection was built against (§2.3).  They are never
    interchangeable — a stale projection carries ``basis_revision < revision``.
    """

    revision: int
    basis_revision: int = 0

    def __post_init__(self) -> None:
        if self.revision < 0 or self.basis_revision < 0:
            raise ValueError("revisions must be non-negative")


@dataclass(frozen=True)
class DataAvailability:
    """Frozen combination of the three orthogonal dimensions (§2.3).

    ``availability`` (readable?), ``freshness`` (projection caught up?) and
    ``basis_revision`` (which owner revision?) are reported separately and must
    never be collapsed into one another.  A ``ready`` resource with ``stale``
    freshness is readable but must be shown with an explicit stale marker; an
    ``absent`` resource simply has no projection yet.
    """

    resource_id: str
    availability: Availability
    freshness: Freshness = Freshness.UNKNOWN
    basis_revision: int = 0
    expected_revision: int = 0

    def is_readable(self) -> bool:
        """True when the body is prepared (``ready``) regardless of freshness."""
        return self.availability is Availability.READY


@dataclass(frozen=True)
class PrepareOperation:
    """Frozen shape of one persistent prepare operation (contract §2.2).

    The persistent prepare state machine itself is B02-3 (todo 13); this record
    freezes the wire shape so downstream slices and consumers cannot invent a
    parallel DTO.  ``observed_at`` is diagnostic only — it is explicitly **not**
    a cross-process freshness clock (§2.2/§2.3).
    """

    operation_id: str
    idempotency_key: str
    resource_id: str
    resource_kind: ResourceKind
    state: ResourceState
    availability: Availability
    freshness: Freshness
    basis_revision: int
    expected_revision: int
    observed_at: str
    error: Mapping[str, Any] | None = None
    bytes: int | None = None
    sha256: str | None = None


# ---------------------------------------------------------------------------
# Domain 1 — inputs (register / validate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputRegistration:
    """Register one input structure as an indexed, reusable source.

    ``relative_path`` is the input's task-relative path (e.g. ``input.xyz``).
    The delegated implementation keys the resource by
    ``source_uid_for(job_id, relative_path)`` so the identity is stable and
    indistinguishable from any other structure-source registration.
    """

    task_root: str
    job_id: str
    input_id: str
    relative_path: str
    project_id: str | None = None
    label: str = ""
    role: str = ""
    source_kind: str = "input"
    availability: str = "available"
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class InputRecord:
    """Result of :meth:`DataResourcePort.register_input`."""

    input_id: str
    source_uid: str
    job_id: str
    relative_path: str
    registered: bool


@dataclass(frozen=True)
class InputValidationRequest:
    """Identify one previously registered input for validation."""

    task_root: str
    job_id: str
    relative_path: str


@dataclass(frozen=True)
class InputValidation:
    """Result of :meth:`DataResourcePort.validate_input`."""

    source_uid: str
    valid: bool
    exists: bool
    availability: str = ""
    reason: str | None = None


# ---------------------------------------------------------------------------
# Domain 2 — artifacts / manifest (publish / read)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestPublish:
    """Publish a v2 ``result_manifest`` payload under ``<task_root>/RESULT``.

    ``manifest`` is the serialised v2 payload (same field names as
    ``RESULT/result_manifest.json`` — *not* a second DTO).  The delegated writer
    is the authoritative v2 writer, so no schema change is made or permitted.
    """

    task_root: str
    manifest: Mapping[str, Any]
    expected_revision: int | None = None


@dataclass(frozen=True)
class ManifestPublication:
    """Result of :meth:`DataResourcePort.publish_manifest`."""

    path: str
    version: int
    product_count: int
    revision: int


@dataclass(frozen=True)
class ManifestRead:
    """Read the manifest under ``<task_root>/RESULT``."""

    task_root: str


@dataclass(frozen=True)
class ManifestSnapshot:
    """Result of :meth:`DataResourcePort.read_manifest` (missing == found False)."""

    found: bool
    version: int
    manifest: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Domain 3 — sources (structure-source organization)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceUpsert:
    """Upsert discovered structure-source index entries (discovery fields only)."""

    entries: Sequence[Mapping[str, Any]]
    discovery_version: int = 1
    replace_job_id: str | None = None


# ---------------------------------------------------------------------------
# Domain 4 — candidates (authoritative frame_candidates file)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateWrite:
    """Write the authoritative ``RESULT/frame_candidates.json`` payload.

    ``payload`` carries the candidate list.  The adapter always persists the
    ``frame_candidates_v2`` schema and increments ``revision``; when
    ``expected_revision`` is supplied it must match the stored revision or the
    write is rejected with :class:`DataRevisionConflictError` (no silent overwrite).
    """

    task_root: str
    payload: Mapping[str, Any]
    expected_revision: int | None = None


@dataclass(frozen=True)
class CandidateSnapshot:
    """Result of a candidates read/write (missing == found False, revision 0)."""

    found: bool
    schema_version: str | None
    revision: int
    candidates: tuple[Mapping[str, Any], ...] = ()


# ---------------------------------------------------------------------------
# Domain 5 — transfers (upload / download)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteJobRef:
    """Opaque, serialisable job handle for transfer/cache operations.

    The adapter turns this into whatever record shape the existing cache
    implementation expects; the Services port never names a legacy record type.
    """

    job_id: str
    attempt: int | None = None


@dataclass(frozen=True)
class TransferUpload:
    """Write cached files back to the remote job directory (layout preserved)."""

    job: RemoteJobRef
    relative_paths: Sequence[str]


@dataclass(frozen=True)
class TransferReport:
    """Result of :meth:`DataResourcePort.upload_paths`."""

    direction: TransferDirection
    uploaded: tuple[str, ...]
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferDownload:
    """Fetch one remote file into the controlled cache."""

    job: RemoteJobRef
    relative_path: str
    force: bool = False
    raise_errors: bool = False


@dataclass(frozen=True)
class TransferResult:
    """Result of :meth:`DataResourcePort.download_file`."""

    direction: TransferDirection
    relative_path: str
    cached_path: str | None
    fetched: bool


# ---------------------------------------------------------------------------
# Domain 6 — cache (availability)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheLookup:
    """Look up one cached file without any network access."""

    job: RemoteJobRef
    relative_path: str


@dataclass(frozen=True)
class CacheEntry:
    """Result of :meth:`DataResourcePort.cache_entry` (local-only, no fetch)."""

    relative_path: str
    present: bool
    cached_path: str | None = None


@dataclass(frozen=True)
class AvailabilityQuery:
    """Query the availability/freshness of a resource projection.

    Exactly one of ``relative_path`` / ``workflow`` may be supplied: a
    ``relative_path`` asks about one cached file, a ``workflow`` asks whether
    the workflow's catalog is ready under ``root``.
    """

    resource_id: str
    root: str
    workflow: str = ""
    relative_path: str = ""
    job_id: str = ""
    expected_revision: int = 0
    basis_revision: int = 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DataRevisionConflictError(RuntimeError):
    """``expected_revision`` did not match the stored revision (contract §0.3)."""


class DataResourceUnavailableError(RuntimeError):
    """An operation needs a backend (store/cache/fetcher) that is not configured."""


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class DataResourcePort(Protocol):
    """Explicit typed seam onto the data resources of a task.

    Every method is named for exactly one operation; there is deliberately no
    generic ``call``/``invoke`` dispatcher and no open keyword catch-all, so
    the surface is statically checkable and can be satisfied by a test double
    that implements only the methods a slice uses.
    """

    # --- inputs ---------------------------------------------------------
    def register_input(self, request: InputRegistration) -> InputRecord:
        """Register one input structure as an indexed source."""
        ...

    def validate_input(self, request: InputValidationRequest) -> InputValidation:
        """Validate that a registered input exists and is usable."""
        ...

    # --- artifacts / manifest ------------------------------------------
    def publish_manifest(self, request: ManifestPublish) -> ManifestPublication:
        """Publish the v2 result manifest under ``<task_root>/RESULT``."""
        ...

    def read_manifest(self, request: ManifestRead) -> ManifestSnapshot:
        """Read the (possibly legacy-version) result manifest; never raises."""
        ...

    # --- sources -------------------------------------------------------
    def upsert_sources(self, request: SourceUpsert) -> int:
        """Upsert structure-source discovery entries; returns rows affected."""
        ...

    def list_sources(self, job_id: str) -> tuple[Mapping[str, Any], ...]:
        """Return the structure-source projections of one job."""
        ...

    def source_uid(
        self,
        job_id: str,
        relative_path: str,
    ) -> str:
        """Return the stable ``source_uid_for(job_id, relative_path)``."""
        ...

    # --- candidates ----------------------------------------------------
    def save_candidates(self, request: CandidateWrite) -> CandidateSnapshot:
        """Write the authoritative ``frame_candidates.json`` (revision + 1)."""
        ...

    def read_candidates(self, task_root: str) -> CandidateSnapshot:
        """Read the authoritative ``frame_candidates.json`` (v1 migrated in memory)."""
        ...

    def list_candidates(self, task_root: str) -> tuple[Mapping[str, Any], ...]:
        """List the authoritative candidate entries (empty when absent)."""
        ...

    # --- transfers -----------------------------------------------------
    def upload_paths(self, request: TransferUpload) -> TransferReport:
        """Write cached files back to the remote job directory."""
        ...

    def download_file(self, request: TransferDownload) -> TransferResult:
        """Fetch one remote file into the controlled cache."""
        ...

    # --- cache ---------------------------------------------------------
    def cache_entry(self, request: CacheLookup) -> CacheEntry:
        """Look up one cached file locally (no network, no enqueue)."""
        ...

    def resource_availability(self, request: AvailabilityQuery) -> DataAvailability:
        """Report availability/freshness/basis_revision for a resource."""
        ...
