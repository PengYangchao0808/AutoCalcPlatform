"""Read-root unification + the strictly read-only availability surface.

B02-5 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 15).  This module
freezes the **read side** of the Data seam and nothing else:

* :class:`ReadRootResolver` resolves **one** readable root per job from an
  injected :class:`ReadRootPort`.  The port decides *where* a remote job's
  prepared cache root lives; the resolver never touches a network.
* :class:`AvailabilitySnapshot` reports the three orthogonal dimensions of the
  frozen contract — ``availability`` (readable?), ``freshness`` (projection
  caught up?) and ``basis_revision`` (which owner revision?) — plus a diagnostic
  ``observed_at`` (explicitly **not** a cross-process clock) and the resolver
  root used.
* :class:`ReadOnlyResourceReader` answers availability and bounded range reads
  **only** by consulting the injected :class:`CacheIndexPort` (the prepared
  snapshot/cache index) and the optional :class:`OperationAvailabilityPort`
  (the todo-13 operation rows).  It never enqueues an operation, never opens
  SSH/SFTP, and never parses or writes a file.  A missing projection is reported
  as :class:`ResourceNotPreparedError` rather than triggering a fetch.

**Only three entry points are allowed to create a persistent prepare
operation** (``acp_services.data.operations``): an explicit prepare command,
the terminal-transition background flow, and startup recovery.  This module is
not one of them — it has no reference to ``enqueue`` at all.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package, so this module is stdlib + ``acp_services.data.contracts``
only.  The delegation adapters that wrap the real cache/operations live on the
legacy side of the seam (``acp.services_data_bootstrap``).

This is **preparation, not a switch** (the switch is todo 16): nothing here
reroutes an existing GET or changes a status code.  The new surface is additive
and inert until a consumer opts in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from acp_services.data.contracts import Availability, Freshness

__all__ = [
    "DEFAULT_MAX_READ_BYTES",
    "AvailabilityRead",
    "AvailabilitySnapshot",
    "BoundedRead",
    "CacheIndexFacts",
    "CacheIndexPort",
    "OperationAvailabilityPort",
    "OperationFacts",
    "RangeReadRequest",
    "ReadOnlyResourceReader",
    "ReadRoot",
    "ReadRootPort",
    "ReadRootResolver",
    "ResourceNotPreparedError",
]

#: Hard cap applied to every bounded read.  Mirrors the remote fetcher's
#: ``_MAX_READ_BYTES`` semantics (``fetcher.py:300``) so a Range request can
#: never pull more than one chunk into memory.
DEFAULT_MAX_READ_BYTES = 1 << 20  # 1 MiB


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (diagnostic only)."""
    return datetime.now(timezone.utc).isoformat()


class ResourceNotPreparedError(RuntimeError):
    """A bounded read was asked for a resource the prepared index does not hold.

    Raised instead of fetching: a read surface must not turn a cache miss into
    a network request or a new prepare operation.
    """


# ---------------------------------------------------------------------------
# Read root
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadRoot:
    """The single readable root for one job, as resolved without any network.

    ``path`` is the local directory the projection should read from: a local
    job's ``work_dir`` or a remote job's controlled cache root.  ``found`` is
    ``False`` for an unknown job.  ``catalog_ready`` says whether the small
    catalog metadata is already present under ``path`` (local stat only).
    """

    job_id: str
    path: str
    is_remote: bool = False
    catalog_ready: bool = False
    found: bool = True
    #: The attempt this root was resolved for (``None`` when the caller did not
    #: pin one).  A reader that asks for a different attempt must refuse rather
    #: than fall back to this root (D04: a missing historical original is a
    #: refusal, never the current ``work_dir``).
    attempt: int | None = None


@runtime_checkable
class ReadRootPort(Protocol):
    """Resolve a job's readable root from authoritative local metadata only."""

    def resolve(self, job_id: str) -> ReadRoot:
        """Return the readable root for *job_id*; never touches a network."""
        ...


class ReadRootResolver:
    """The one read-root resolver: job id -> :class:`ReadRoot`.

    The resolver owns no policy beyond calling the injected port; every read
    surface therefore agrees on exactly one root per job (no second read root).
    """

    def __init__(self, port: ReadRootPort) -> None:
        self._port = port

    def resolve(self, job_id: str) -> ReadRoot:
        """Resolve the readable root for *job_id*."""
        return self._port.resolve(job_id)


# ---------------------------------------------------------------------------
# Port facts (raw, network-free observations supplied by the legacy side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheIndexFacts:
    """One prepared-index observation for a resource.

    ``snapshot`` is an opaque, port-owned token.  When it is not ``None`` the
    port opened the target under the controlled root and owns a live
    descriptor; :meth:`CacheIndexPort.read_bytes` consumes that descriptor and
    performs the bounded read from it, so no path is resolved a second time.
    """

    present: bool
    cached_path: str | None = None
    total_size: int | None = None
    generation: int = 0
    snapshot: object | None = None


@dataclass(frozen=True)
class OperationFacts:
    """One prepare-operation row projection (read-only; no side effects)."""

    exists: bool
    availability: Availability = Availability.ABSENT
    freshness: Freshness = Freshness.UNKNOWN
    basis_revision: int = 0
    expected_revision: int = 0
    detail: str = ""


@runtime_checkable
class CacheIndexPort(Protocol):
    """Read the prepared cache index without fetching, parsing or writing.

    ``root`` is the already-resolved :class:`ReadRoot` for the request, so the
    port never re-resolves a root of its own.  For a local root
    (``root.is_remote is False``) the port must open the target inside that
    root as one authorized snapshot; for a remote root it may only serve the
    controlled prepared-cache copy.
    """

    def entry(
        self,
        root: ReadRoot,
        relative_path: str,
        *,
        attempt: int | None = None,
        snapshot: bool = False,
    ) -> CacheIndexFacts:
        """Return the prepared-index facts for one file under *root*.

        With ``snapshot=True`` the returned facts carry a live descriptor that
        :meth:`read_bytes` must consume and close.
        """
        ...

    def catalog(self, root: ReadRoot, workflow: str) -> CacheIndexFacts:
        """Return the prepared-index facts for *root*'s job catalog."""
        ...

    def read_bytes(self, facts: CacheIndexFacts, offset: int, limit: int) -> bytes:
        """Read at most *limit* bytes at *offset* from the facts' snapshot."""
        ...


@runtime_checkable
class OperationAvailabilityPort(Protocol):
    """Read the committed availability of a prepare operation (no enqueue)."""

    def facts(self, request: AvailabilityRead) -> OperationFacts:
        """Return the operation projection for *request*, or ``exists=False``."""
        ...


# ---------------------------------------------------------------------------
# Requests / results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvailabilityRead:
    """Identify the resource whose availability projection is requested.

    Exactly one of ``relative_path`` (one cached file) or ``workflow`` (the
    job catalog) drives the lookup; ``resource_id`` is the stable caller-facing
    id used to key the prepare operation (e.g. ``catalog:Confsearch``).
    """

    job_id: str
    resource_id: str
    workflow: str = ""
    relative_path: str = ""
    attempt: int | None = None
    expected_revision: int = 0
    basis_revision: int = 0
    permission_scope: str = "local-single"


@dataclass(frozen=True)
class RangeReadRequest:
    """A bounded byte-range read of one already-prepared resource."""

    job_id: str
    relative_path: str
    offset: int = 0
    limit: int = DEFAULT_MAX_READ_BYTES
    attempt: int | None = None


@dataclass(frozen=True)
class BoundedRead:
    """The bounded result of :meth:`ReadOnlyResourceReader.read_range`."""

    relative_path: str
    offset: int
    length: int
    data: bytes
    total_size: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class AvailabilitySnapshot:
    """Frozen availability/freshness/revision projection of one resource.

    ``availability`` answers *is the body prepared and readable*;
    ``freshness`` answers *has the projection caught up with the authoritative
    revision*; ``basis_revision`` records *which owner revision the projection
    was built against*.  ``expected_revision`` is the revision the caller
    expects; a projection with ``basis_revision < expected_revision`` is
    ``stale``.  ``observed_at`` is diagnostics, never a freshness clock.
    """

    resource_id: str
    availability: Availability
    freshness: Freshness
    basis_revision: int
    expected_revision: int
    observed_at: str
    job_id: str = ""
    is_remote: bool = False
    prepared_root: str | None = None
    size_bytes: int | None = None
    detail: str = ""

    def is_readable(self) -> bool:
        """True when the body is prepared (``ready``) regardless of freshness."""
        return self.availability is Availability.READY

    def to_dict(self) -> dict[str, object]:
        """Serialise to the wire shape (enum values as plain strings)."""
        return {
            "resource_id": self.resource_id,
            "job_id": self.job_id,
            "availability": self.availability.value,
            "freshness": self.freshness.value,
            "basis_revision": self.basis_revision,
            "expected_revision": self.expected_revision,
            "observed_at": self.observed_at,
            "is_remote": self.is_remote,
            "prepared_root": self.prepared_root,
            "size_bytes": self.size_bytes,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _requested_attempt(attempt: int | None) -> int | None:
    """Return the explicitly requested attempt, or ``None`` when unpinned.

    An unpinned request keeps the port's default attempt-fence behaviour; a
    pinned request is never silently replaced by the root's attempt (the port
    refuses a root that cannot prove that attempt).
    """
    return int(attempt) if attempt is not None else None


def _freshness_of(basis_revision: int, expected_revision: int) -> Freshness:
    """Map a (basis, expected) revision pair onto the frozen freshness trips.

    No expected revision means the caller asserted nothing, so the projection
    cannot be judged caught up (``unknown``); a matching-or-newer basis is
    ``current``; an older basis is ``stale``.
    """
    expected = int(expected_revision)
    if expected <= 0:
        return Freshness.UNKNOWN
    if int(basis_revision) >= expected:
        return Freshness.CURRENT
    return Freshness.STALE


class ReadOnlyResourceReader:
    """Availability + bounded reads over the prepared index (strictly read-only).

    Args:
        resolver: The single read-root resolver (no network).
        cache_index: Prepared snapshot/cache index (local stat + range read).
        operations: Optional todo-13 operation reader; when omitted a missing
            projection reads as ``absent``.  This port has no write method.
        clock: Diagnostic timestamp source (tests pin it); defaults to UTC now.
        max_read_bytes: Hard cap for every bounded read (clamped, never exceeded).
        before_read: Optional instrumentation barrier invoked after the target
            snapshot has been opened and before its bounded read (tests use it
            to swap a symlink/root and prove the descriptor still pins one
            inside-root snapshot).
    """

    def __init__(
        self,
        *,
        resolver: ReadRootResolver,
        cache_index: CacheIndexPort,
        operations: OperationAvailabilityPort | None = None,
        clock: Callable[[], str] | None = None,
        max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
        before_read: Callable[[], None] | None = None,
    ) -> None:
        self._resolver = resolver
        self._cache = cache_index
        self._operations = operations
        self._clock = clock or _utc_now_iso
        self._max_read_bytes = max(1, int(max_read_bytes))
        self._before_read = before_read

    # -- availability ---------------------------------------------------

    def availability(self, request: AvailabilityRead) -> AvailabilitySnapshot:
        """Report availability/freshness/revision for one resource (no side effects).

        Precedence: the operation row for the caller's expected identity is
        authoritative whenever it exists.  A queued/running/failed/expired (or
        identity-mismatched) operation suppresses a cache-index hit, so an old
        cached body can never report READY for a newer current identity.  When
        the operation is READY (or no operation exists) a prepared cache-index
        hit wins; otherwise the operation projection is reported; otherwise the
        resource is ``absent``.  Nothing is fetched, enqueued or written on any
        branch.
        """
        observed_at = self._clock()
        root = self._resolver.resolve(request.job_id)
        if not root.found:
            return self._absent(request, observed_at, root, detail="job_not_found")

        op_facts = self._operations.facts(request) if self._operations is not None else None
        if (
            op_facts is not None
            and op_facts.exists
            and op_facts.availability is not Availability.READY
        ):
            expected = int(op_facts.expected_revision) or int(request.expected_revision)
            return AvailabilitySnapshot(
                resource_id=request.resource_id,
                availability=op_facts.availability,
                freshness=op_facts.freshness,
                basis_revision=int(op_facts.basis_revision),
                expected_revision=expected,
                observed_at=observed_at,
                job_id=request.job_id,
                is_remote=root.is_remote,
                prepared_root=None,
                size_bytes=None,
                detail=op_facts.detail or "operation",
            )

        if request.relative_path:
            facts = self._cache.entry(
                root,
                request.relative_path,
                attempt=_requested_attempt(request.attempt),
            )
            ready = facts.present
            basis_revision = facts.generation if facts.present else request.basis_revision
            size_bytes = facts.total_size
            prepared_root = facts.cached_path if facts.present else None
        else:
            facts = self._cache.catalog(root, request.workflow)
            ready = bool(root.path and facts.present)
            basis_revision = facts.generation
            size_bytes = facts.total_size
            prepared_root = (facts.cached_path or root.path) if ready else None

        if ready:
            operation_generation = int(op_facts.expected_revision) if op_facts else 0
            if (
                op_facts is not None
                and op_facts.exists
                and op_facts.availability is Availability.READY
                and operation_generation > 0
                and int(basis_revision) > 0
                and int(basis_revision) < operation_generation
            ):
                return AvailabilitySnapshot(
                    resource_id=request.resource_id,
                    availability=Availability.ABSENT,
                    freshness=Freshness.STALE,
                    basis_revision=int(basis_revision),
                    expected_revision=operation_generation,
                    observed_at=observed_at,
                    job_id=request.job_id,
                    is_remote=root.is_remote,
                    prepared_root=None,
                    size_bytes=None,
                    detail="cache_generation_behind_operation",
                )
            return AvailabilitySnapshot(
                resource_id=request.resource_id,
                availability=Availability.READY,
                freshness=_freshness_of(basis_revision, request.expected_revision),
                basis_revision=int(basis_revision),
                expected_revision=int(request.expected_revision),
                observed_at=observed_at,
                job_id=request.job_id,
                is_remote=root.is_remote,
                prepared_root=prepared_root,
                size_bytes=size_bytes,
                detail="cache_index",
            )

        if op_facts is not None:
            if op_facts.exists:
                expected = int(op_facts.expected_revision) or int(request.expected_revision)
                return AvailabilitySnapshot(
                    resource_id=request.resource_id,
                    availability=op_facts.availability,
                    freshness=op_facts.freshness,
                    basis_revision=int(op_facts.basis_revision),
                    expected_revision=expected,
                    observed_at=observed_at,
                    job_id=request.job_id,
                    is_remote=root.is_remote,
                    prepared_root=None,
                    size_bytes=None,
                    detail=op_facts.detail or "operation",
                )

        return self._absent(request, observed_at, root, detail="no_projection")

    # -- bounded read ---------------------------------------------------

    def read_range(self, request: RangeReadRequest) -> BoundedRead:
        """Read a bounded byte range from an already-prepared resource.

        The root is resolved exactly once per call and handed to the index
        port.  For a local root the port opens the target inside this root as
        one authorized snapshot; the bounded read consumes that same opened
        descriptor, so a symlink/root/attempt swap after the check cannot move
        the read outside the root or mix two attempts.

        The requested ``limit`` is clamped to ``max_read_bytes`` before it
        reaches the index port, so the file is never read whole into memory
        (Range/chunk semantics mirror ``remote/fetcher.py:read_range``).

        Raises:
            ResourceNotPreparedError: The resource is not readable under the
                resolved root.  No fetch, no enqueue and no fallback to
                another root is attempted.
        """
        root = self._resolver.resolve(request.job_id)
        attempt = _requested_attempt(request.attempt)
        if not root.found or not root.path:
            raise ResourceNotPreparedError(f"resource not prepared: {request.job_id}")
        facts = self._cache.entry(
            root,
            request.relative_path,
            attempt=attempt,
            snapshot=True,
        )
        if not facts.present or not facts.cached_path or facts.snapshot is None:
            raise ResourceNotPreparedError(
                f"resource not prepared: {request.job_id}/{request.relative_path}"
            )
        offset = max(0, int(request.offset))
        limit = max(0, min(int(request.limit), self._max_read_bytes))
        if self._before_read is not None:
            self._before_read()
        data = bytes(self._cache.read_bytes(facts, offset, limit))
        total_size = facts.total_size
        end = offset + len(data)
        truncated = total_size is not None and end < int(total_size)
        return BoundedRead(
            relative_path=request.relative_path,
            offset=offset,
            length=len(data),
            data=data,
            total_size=total_size,
            truncated=truncated,
        )

    # -- helpers --------------------------------------------------------

    def _absent(
        self,
        request: AvailabilityRead,
        observed_at: str,
        root: ReadRoot,
        *,
        detail: str,
    ) -> AvailabilitySnapshot:
        return AvailabilitySnapshot(
            resource_id=request.resource_id,
            availability=Availability.ABSENT,
            freshness=Freshness.UNKNOWN,
            basis_revision=int(request.basis_revision),
            expected_revision=int(request.expected_revision),
            observed_at=observed_at,
            job_id=request.job_id,
            is_remote=root.is_remote,
            prepared_root=None,
            size_bytes=None,
            detail=detail,
        )
