"""Per-resource cache-only read switch + compat adapters (B02-6 / plan todo 16).

B02-5 (todo 15) froze the strictly read-only surface (:mod:`acp_services.data.
read_root`).  This module is the **switch carrier** for the compatibility-first
per-resource rollout: it maps each of the five read resources —

* ``catalog`` — the small per-workflow catalog root,
* ``manifest`` — ``RESULT/result_manifest.json``,
* ``frame_candidates`` — ``RESULT/frame_candidates.json``,
* ``frequency_modes`` — the normal-modes projection,
* ``geometry`` — one structure-viewer geometry file,

onto that read-only surface with a **per-resource adapter**, and exposes a
:class:`CacheOnlyResourceReader` that serves them from the prepared cache index
only.  The reader never fetches over SSH/SFTP, never enqueues a prepare
operation and never parses or writes a file; a cold cache is reported as
``not_prepared`` rather than repaired.

**Compatibility-first switch.**  :data:`CACHE_ONLY_SWITCH` is the registry that
records, for every resource, the new read path, the retained legacy fallback and
whether the switch is enabled.  ``enabled`` is the *new path* being active; the
legacy GET remains the per-resource fallback for the whole compatibility
window.  The old GETs keep their original behaviour (their terminal
``fetch_catalog`` side effect is a registered transitional item in
``tests/baseline/services-evidence/migration_ledger.md``), so an F0 client
continues to receive real results on a cold cache — which is exactly the
property this slice freezes and tests.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package, so this module is stdlib + ``acp_services.data.{contracts,
read_root}`` only.  The delegation adapters that wrap the real cache live on the
legacy side of the seam (``acp.services_data_bootstrap``).
"""

from __future__ import annotations

from dataclasses import dataclass

from acp_services.data.contracts import Availability
from acp_services.data.read_root import (
    DEFAULT_MAX_READ_BYTES,
    AvailabilityRead,
    AvailabilitySnapshot,
    BoundedRead,
    RangeReadRequest,
    ReadOnlyResourceReader,
    ResourceNotPreparedError,
)

__all__ = [
    "ALL_RESOURCE_KINDS",
    "CACHE_ONLY_SWITCH",
    "DEFAULT_ENABLED_KINDS",
    "RESOURCE_ADAPTERS",
    "RESOURCE_CATALOG",
    "RESOURCE_FRAME_CANDIDATES",
    "RESOURCE_FREQUENCY_MODES",
    "RESOURCE_GEOMETRY",
    "RESOURCE_MANIFEST",
    "CacheOnlyResourceReader",
    "ResourceAdapter",
    "ResourceServeResult",
    "ResourceSwitch",
    "ResourceUnknownError",
]

#: The five read resources covered by the cache-only switch (plan todo 16).
RESOURCE_CATALOG = "catalog"
RESOURCE_MANIFEST = "manifest"
RESOURCE_FRAME_CANDIDATES = "frame_candidates"
RESOURCE_FREQUENCY_MODES = "frequency_modes"
RESOURCE_GEOMETRY = "geometry"

ALL_RESOURCE_KINDS: tuple[str, ...] = (
    RESOURCE_CATALOG,
    RESOURCE_MANIFEST,
    RESOURCE_FRAME_CANDIDATES,
    RESOURCE_FREQUENCY_MODES,
    RESOURCE_GEOMETRY,
)

#: New-path kind tokens; kept as plain strings so the frozen wire vocabulary is
#: not coupled to an enum that would need a third value set.
_PATH_AVAILABILITY = "read_only_availability"
_PATH_BYTES = "prepared_bytes"
_FALLBACK_LEGACY_GET = "legacy_get"


class ResourceUnknownError(KeyError):
    """An unknown resource kind was requested."""


@dataclass(frozen=True)
class ResourceAdapter:
    """Map one read resource onto the frozen read-only surface.

    ``body`` is ``"catalog"`` for the job catalog root (availability only) and
    ``"file"`` for the byte resources.  ``default_relative_path`` is the
    canonical path used when a caller does not name one, so a consumer can read
    a resource without re-deriving the layout constant.
    """

    kind: str
    body: str
    default_relative_path: str
    description: str

    def is_catalog(self) -> bool:
        """True when the resource is the per-workflow catalog root."""
        return self.body == "catalog"

    def resource_id(self, workflow: str) -> str:
        """The stable caller-facing resource id (``<kind>:<workflow>``)."""
        return f"{self.kind}:{workflow}"

    def resolve_relative_path(self, relative_path: str = "") -> str:
        """Return *relative_path* if given, else this adapter's default."""
        return relative_path or self.default_relative_path


#: Per-resource compat adapters (five).  The order is the canonical switch
#: order recorded in the matrix.
RESOURCE_ADAPTERS: dict[str, ResourceAdapter] = {
    RESOURCE_CATALOG: ResourceAdapter(
        kind=RESOURCE_CATALOG,
        body="catalog",
        default_relative_path="",
        description="per-workflow catalog root (availability only)",
    ),
    RESOURCE_MANIFEST: ResourceAdapter(
        kind=RESOURCE_MANIFEST,
        body="file",
        default_relative_path="RESULT/result_manifest.json",
        description="unified v2 result manifest",
    ),
    RESOURCE_FRAME_CANDIDATES: ResourceAdapter(
        kind=RESOURCE_FRAME_CANDIDATES,
        body="file",
        default_relative_path="RESULT/frame_candidates.json",
        description="frame-candidate authority file",
    ),
    RESOURCE_FREQUENCY_MODES: ResourceAdapter(
        kind=RESOURCE_FREQUENCY_MODES,
        body="file",
        default_relative_path="RESULT/frequencies/normal_modes.json",
        description="normal-modes projection",
    ),
    RESOURCE_GEOMETRY: ResourceAdapter(
        kind=RESOURCE_GEOMETRY,
        body="file",
        default_relative_path="",
        description="one structure-viewer geometry file",
    ),
}


@dataclass(frozen=True)
class ResourceSwitch:
    """One row of the per-resource cache-only switch registry."""

    kind: str
    new_path: str
    fallback: str
    enabled: bool
    note: str = ""


#: The switch registry.  ``enabled`` is the new (cache-only) path; ``fallback``
#: is the legacy GET retained for the whole compatibility window.  A resource is
#: never switched off its fallback until the F0/F1 x S0/S1 matrix has evidence
#: for every combination (plan todo 69 / todo 75).
CACHE_ONLY_SWITCH: dict[str, ResourceSwitch] = {
    RESOURCE_CATALOG: ResourceSwitch(
        kind=RESOURCE_CATALOG,
        new_path=_PATH_AVAILABILITY,
        fallback=_FALLBACK_LEGACY_GET,
        enabled=True,
        note="structure-viewer catalog; legacy GET /structure-viewer retained",
    ),
    RESOURCE_MANIFEST: ResourceSwitch(
        kind=RESOURCE_MANIFEST,
        new_path=_PATH_BYTES,
        fallback=_FALLBACK_LEGACY_GET,
        enabled=True,
        note="energy-graph / detail projection; legacy GET retained",
    ),
    RESOURCE_FRAME_CANDIDATES: ResourceSwitch(
        kind=RESOURCE_FRAME_CANDIDATES,
        new_path=_PATH_BYTES,
        fallback=_FALLBACK_LEGACY_GET,
        enabled=True,
        note="legacy GET /frame-candidates retained",
    ),
    RESOURCE_FREQUENCY_MODES: ResourceSwitch(
        kind=RESOURCE_FREQUENCY_MODES,
        new_path=_PATH_BYTES,
        fallback=_FALLBACK_LEGACY_GET,
        enabled=True,
        note="vibrations projection; legacy GET retained",
    ),
    RESOURCE_GEOMETRY: ResourceSwitch(
        kind=RESOURCE_GEOMETRY,
        new_path=_PATH_BYTES,
        fallback=_FALLBACK_LEGACY_GET,
        enabled=True,
        note="legacy GET geometry (and ?fetch=1 fallback) retained",
    ),
}

#: The default enabled set — every resource's new path is active.
DEFAULT_ENABLED_KINDS: frozenset[str] = frozenset(
    kind for kind, row in CACHE_ONLY_SWITCH.items() if row.enabled
)


@dataclass(frozen=True)
class ResourceServeResult:
    """The outcome of serving one resource cache-only.

    ``served`` is ``True`` only when the prepared cache index answered with a
    readable body (a catalog ``ready`` snapshot counts as served).  When it is
    ``False`` the caller must fall back to the per-resource legacy GET — the
    reader never repairs a miss.
    """

    kind: str
    served: bool
    source: str
    reason: str = ""
    relative_path: str = ""
    snapshot: AvailabilitySnapshot | None = None
    data: bytes | None = None

    def availability_value(self) -> str:
        """Return the availability token, or ``absent`` when not served."""
        if self.snapshot is None:
            return Availability.ABSENT.value
        return self.snapshot.availability.value


class CacheOnlyResourceReader:
    """Serve the five read resources from the prepared cache index only.

    Args:
        reader: The strictly read-only reader (todo 15).  It has no enqueue and
            no fetch reference, so a serve here structurally cannot create an
            operation or touch a node.
        enabled: The enabled resource kinds.  Defaults to
            :data:`DEFAULT_ENABLED_KINDS`; tests pass an explicit set to
            exercise the switch on/off.
    """

    def __init__(
        self,
        reader: ReadOnlyResourceReader,
        *,
        enabled: frozenset[str] | None = None,
    ) -> None:
        self._reader = reader
        self._enabled = frozenset(DEFAULT_ENABLED_KINDS if enabled is None else enabled)

    # -- registry -------------------------------------------------------

    def enabled_kinds(self) -> frozenset[str]:
        """Return the enabled resource kinds (the active new paths)."""
        return self._enabled

    def is_enabled(self, kind: str) -> bool:
        """True when *kind*'s cache-only new path is switched on."""
        return kind in self._enabled

    def adapter(self, kind: str) -> ResourceAdapter:
        """Return the compat adapter for *kind*.

        Raises:
            ResourceUnknownError: *kind* is not one of the five resources.
        """
        try:
            return RESOURCE_ADAPTERS[kind]
        except KeyError as exc:
            raise ResourceUnknownError(kind) from exc

    # -- reads ----------------------------------------------------------

    def availability(
        self,
        kind: str,
        *,
        job_id: str,
        workflow: str,
        relative_path: str = "",
        expected_revision: int = 0,
        attempt: int | None = None,
    ) -> AvailabilitySnapshot:
        """Report availability/freshness/revision for one resource (no side effect)."""
        adapter = self.adapter(kind)
        if adapter.is_catalog():
            resource_path = ""
        else:
            resource_path = adapter.resolve_relative_path(relative_path)
        return self._reader.availability(
            AvailabilityRead(
                job_id=job_id,
                resource_id=adapter.resource_id(workflow),
                workflow=workflow,
                relative_path=resource_path,
                attempt=attempt,
                expected_revision=expected_revision,
            )
        )

    def read_range(
        self,
        kind: str,
        *,
        job_id: str,
        relative_path: str = "",
        offset: int = 0,
        limit: int = DEFAULT_MAX_READ_BYTES,
        attempt: int | None = None,
    ) -> BoundedRead:
        """Bounded read of a prepared byte resource (clamped; never whole-file).

        Raises:
            ResourceNotPreparedError: The resource is not in the prepared index.
            ResourceUnknownError: *kind* is the catalog (no byte body) or unknown.
        """
        adapter = self.adapter(kind)
        if adapter.is_catalog():
            raise ResourceUnknownError(f"{kind} has no byte body")
        return self._reader.read_range(
            RangeReadRequest(
                job_id=job_id,
                relative_path=adapter.resolve_relative_path(relative_path),
                offset=offset,
                limit=limit,
                attempt=attempt,
            )
        )

    def serve(
        self,
        kind: str,
        *,
        job_id: str,
        workflow: str,
        relative_path: str = "",
        expected_revision: int = 0,
        attempt: int | None = None,
        offset: int = 0,
        limit: int = DEFAULT_MAX_READ_BYTES,
    ) -> ResourceServeResult:
        """Serve *kind* cache-only, or report ``not_prepared`` for a fallback.

        The catalog resource serves its availability snapshot; the four byte
        resources serve the prepared body.  Nothing here fetches or enqueues.
        """
        adapter = self.adapter(kind)
        resolved_path = "" if adapter.is_catalog() else adapter.resolve_relative_path(relative_path)
        if not self.is_enabled(kind):
            snapshot = self.availability(
                kind,
                job_id=job_id,
                workflow=workflow,
                relative_path=relative_path,
                expected_revision=expected_revision,
                attempt=attempt,
            )
            return ResourceServeResult(
                kind=kind,
                served=False,
                source="disabled",
                reason="resource_switch_disabled",
                relative_path=resolved_path,
                snapshot=snapshot,
            )

        if adapter.is_catalog():
            snapshot = self.availability(
                kind,
                job_id=job_id,
                workflow=workflow,
                expected_revision=expected_revision,
                attempt=attempt,
            )
            served = snapshot.availability is Availability.READY
            return ResourceServeResult(
                kind=kind,
                served=served,
                source="cache_only" if served else "not_prepared",
                reason="" if served else snapshot.detail,
                relative_path=resolved_path,
                snapshot=snapshot,
            )

        try:
            bounded = self.read_range(
                kind,
                job_id=job_id,
                relative_path=relative_path,
                offset=offset,
                limit=limit,
                attempt=attempt,
            )
        except ResourceNotPreparedError as exc:
            return ResourceServeResult(
                kind=kind,
                served=False,
                source="not_prepared",
                reason=str(exc),
                relative_path=resolved_path,
            )
        return ResourceServeResult(
            kind=kind,
            served=True,
            source="cache_only",
            relative_path=bounded.relative_path,
            data=bounded.data,
        )
