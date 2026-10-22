"""Read-only scientific capability registry (ACP2 science A-05a).

The registry is a metadata-only discovery surface.  It stores injected
:class:`~cccp.calculation.capabilities.contracts.CapabilityDescriptor`
objects, orders them deterministically by capability id and reports the four
independent projections required by the frozen ACP2 science contract:

* ``described`` — the capability is declared in this registry;
* ``installed`` — the declared executables resolve through the existing
  :func:`cccp.software.resolve_executable` seam;
* ``available`` — the implementation can be offered in this environment;
* ``verified`` — scientific verification evidence supplied by the
  descriptor, never inferred from installation or availability.

There is deliberately **no** capability-to-backend selection algorithm here.
Selection remains in :mod:`cccp.calculation.selection`; the registry only
answers scientific metadata questions.  Discovery imports no
``cccp.calculation.tasks`` module, never executes a plugin and never starts a
calculation.

Registration is functional: :meth:`CapabilityRegistry.register` returns a new
registry, leaving the current instance immutable.  Duplicate capability or
implementation ids raise a typed conflict before any record is built.

Author: QCcalc Team
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from cccp.calculation.capabilities.contracts import (
    CAPABILITY_SCHEMA_VERSION,
    ERROR_CODES,
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityProjections,
    CapabilityState,
    CapabilityStateError,
    CapabilityContractError,
    DuplicateCapabilityError,
    DuplicateImplementationError,
    PreconditionKind,
    Projection,
    UnknownCapabilityError,
    UnknownImplementationError,
)

__all__ = [
    "CAPABILITY_CATALOG_SCHEMA",
    "CapabilityCatalog",
    "CapabilityErrorRecord",
    "CapabilityQueryResult",
    "CapabilityRecord",
    "CapabilityRegistry",
    "ExecutableResolver",
]

#: Catalog envelope identity.  It is a presentation envelope, not a second
#: capability contract; each entry keeps its own schema version.
CAPABILITY_CATALOG_SCHEMA = "acp2.science.capability_catalog"

#: Injected executable probe signature.  Production defaults to
#: :func:`cccp.software.resolve_executable`; tests may inject a stub.
ExecutableResolver = Callable[[str], Path | None]


def _default_executable_resolver() -> ExecutableResolver:
    """Return the one authoritative software resolver.

    Imported lazily so module import (and metadata-only discovery with an
    injected resolver) never pulls in the software layer.
    """
    from cccp.software import resolve_executable

    return resolve_executable


@dataclass(frozen=True)
class CapabilityErrorRecord:
    """A frozen, schema-shaped error record for an unsupported query.

    It mirrors ``errors.schema.json`` without defining a second error
    vocabulary: :data:`cccp.calculation.capabilities.contracts.ERROR_CODES`
    is the only code list.
    """

    code: str
    layer: str
    retryable: bool
    detail: Mapping[str, object] = field(default_factory=dict)
    detail_required: bool = True
    message: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise CapabilityStateError(
                f"error code {self.code!r} is not in the frozen vocabulary"
            )
        if self.layer not in ("structure", "scientific", "platform"):
            raise CapabilityStateError(f"unknown error layer {self.layer!r}")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "code": self.code,
            "layer": self.layer,
            "retryable": self.retryable,
            "detail": dict(self.detail),
            "detail_required": self.detail_required,
        }
        if self.message is not None:
            payload["message"] = self.message
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class CapabilityRecord:
    """A descriptor together with its computed four-projection view."""

    descriptor: CapabilityDescriptor
    projections: CapabilityProjections

    @property
    def capability_id(self) -> str:
        """Return the capability id."""
        return self.descriptor.capability_id

    @property
    def implementation_id(self) -> str:
        """Return the implementation id."""
        return self.descriptor.implementation_id

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped descriptor with computed projections."""
        payload = self.descriptor.to_dict()
        payload["projections"] = self.projections.to_dict()
        return payload


@dataclass(frozen=True)
class CapabilityQueryResult:
    """Explicit result of a capability or implementation lookup.

    An unknown query never raises an untyped error: it returns this typed
    unsupported result with ``supported=False`` and a structured
    ``unknown_implementation`` error record.
    """

    capability_id: str
    record: CapabilityRecord | None = None
    error: CapabilityErrorRecord | None = None

    @property
    def found(self) -> bool:
        """Return whether a record was found."""
        return self.record is not None

    @property
    def supported(self) -> bool:
        """Return whether the query is supported by this registry."""
        return self.found

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe query view."""
        return {
            "capability_id": self.capability_id,
            "supported": self.supported,
            "record": self.record.to_dict() if self.record is not None else None,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True)
class CapabilityCatalog:
    """Deterministically ordered capability catalog."""

    capabilities: tuple[CapabilityRecord, ...]
    schema: str = CAPABILITY_CATALOG_SCHEMA
    schema_version: int = CAPABILITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        """Return the catalog JSON object in capability-id order."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "capabilities": [record.to_dict() for record in self.capabilities],
        }

    def to_json(self) -> str:
        """Serialize to canonical JSON (sorted keys, no whitespace)."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


class CapabilityRegistry:
    """Immutable, metadata-only capability registry."""

    def __init__(
        self,
        descriptors: Iterable[CapabilityDescriptor] = (),
        *,
        resolver: ExecutableResolver | None = None,
    ) -> None:
        """Build a registry from descriptors.

        Args:
            descriptors: Injected capability descriptors.  Duplicate
                capability ids or implementation ids are rejected.
            resolver: Optional executable probe used for the ``installed``
                and ``available`` projections.  Production defaults to
                :func:`cccp.software.resolve_executable`.

        Raises:
            DuplicateCapabilityError: Two descriptors share a capability id.
            DuplicateImplementationError: Two descriptors share an
                implementation id.
            UnknownMajorVersionError: A descriptor's schema major is not 1.
        """
        by_capability: dict[str, CapabilityDescriptor] = {}
        by_implementation: dict[str, CapabilityDescriptor] = {}
        for descriptor in descriptors:
            if not isinstance(descriptor, CapabilityDescriptor):
                raise CapabilityContractError(
                    "CapabilityRegistry accepts CapabilityDescriptor instances only"
                )
            existing = by_capability.get(descriptor.capability_id)
            if existing is not None:
                raise DuplicateCapabilityError(
                    f"duplicate capability id {descriptor.capability_id!r}: "
                    f"{existing.implementation_id!r} and "
                    f"{descriptor.implementation_id!r}"
                )
            existing_impl = by_implementation.get(descriptor.implementation_id)
            if existing_impl is not None:
                raise DuplicateImplementationError(
                    f"duplicate implementation id {descriptor.implementation_id!r}: "
                    f"{existing_impl.capability_id!r} and "
                    f"{descriptor.capability_id!r}"
                )
            by_capability[descriptor.capability_id] = descriptor
            by_implementation[descriptor.implementation_id] = descriptor

        ordered = tuple(by_capability[key] for key in sorted(by_capability))
        self._descriptors = ordered
        self._resolver = resolver if resolver is not None else _default_executable_resolver()
        self._by_implementation = MappingProxyType(by_implementation)
        self._records = MappingProxyType(
            {
                descriptor.capability_id: CapabilityRecord(
                    descriptor=descriptor,
                    projections=self._derive_projections(descriptor),
                )
                for descriptor in ordered
            }
        )

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        """Return descriptors in deterministic capability-id order."""
        return self._descriptors

    @property
    def capability_ids(self) -> tuple[str, ...]:
        """Return registered capability ids in deterministic order."""
        return tuple(record.capability_id for record in self._records.values())

    def register(self, descriptor: CapabilityDescriptor) -> "CapabilityRegistry":
        """Return a new registry with one descriptor added.

        The current registry is never mutated.  Duplicate ids are rejected
        by the returned registry's construction, using the same typed
        conflict errors.
        """
        return CapabilityRegistry(
            (*self._descriptors, descriptor),
            resolver=self._resolver,
        )

    def query(self, capability_id: str) -> CapabilityQueryResult:
        """Look up a capability without raising for an unknown id.

        Returns a typed unsupported result (``supported=False`` with a
        ``unknown_implementation`` error record) when the id is absent.
        """
        record = self._records.get(capability_id)
        if record is not None:
            return CapabilityQueryResult(capability_id=capability_id, record=record)
        return CapabilityQueryResult(
            capability_id=capability_id,
            error=CapabilityErrorRecord(
                code="unknown_implementation",
                layer="scientific",
                retryable=False,
                detail={
                    "capability_id": capability_id,
                    "implementation_id": "<unregistered>",
                },
                message=f"capability {capability_id!r} is not registered",
            ),
        )

    def require(self, capability_id: str) -> CapabilityRecord:
        """Return the record or raise :class:`UnknownCapabilityError`."""
        result = self.query(capability_id)
        if result.record is None:
            raise UnknownCapabilityError(
                f"capability {capability_id!r} is not registered"
            )
        return result.record

    def query_implementation(self, implementation_id: str) -> CapabilityQueryResult:
        """Look up an implementation id without raising for an unknown id."""
        descriptor = self._by_implementation.get(implementation_id)
        if descriptor is None:
            return CapabilityQueryResult(
                capability_id="",
                error=CapabilityErrorRecord(
                    code="unknown_implementation",
                    layer="scientific",
                    retryable=False,
                    detail={
                        "capability_id": "<unregistered>",
                        "implementation_id": implementation_id,
                    },
                    message=f"implementation {implementation_id!r} is not registered",
                ),
            )
        record = self._records[descriptor.capability_id]
        return CapabilityQueryResult(capability_id=record.capability_id, record=record)

    def require_implementation(self, implementation_id: str) -> CapabilityRecord:
        """Return the record or raise :class:`UnknownImplementationError`."""
        result = self.query_implementation(implementation_id)
        if result.record is None:
            raise UnknownImplementationError(
                f"implementation {implementation_id!r} is not registered"
            )
        return result.record

    def catalog(self) -> CapabilityCatalog:
        """Return the catalog in deterministic capability-id order."""
        return CapabilityCatalog(
            capabilities=tuple(self._records[key] for key in sorted(self._records))
        )

    def catalog_json(self) -> str:
        """Return the canonical serialized catalog."""
        return self.catalog().to_json()

    # -- projection derivation ------------------------------------------------

    def _derive_projections(
        self, descriptor: CapabilityDescriptor
    ) -> CapabilityProjections:
        """Derive described/installed/available while preserving verified.

        ``verified`` is taken from the descriptor unchanged: installation
        and availability are observations, never scientific evidence.
        """
        installed, available = self._probe_descriptor(descriptor)
        return CapabilityProjections(
            described=Projection(
                CapabilityState.PRESENT,
                reason="registered in the read-only capability registry",
            ),
            installed=installed,
            available=available,
            verified=descriptor.projections.verified,
        )

    def _probe_descriptor(
        self, descriptor: CapabilityDescriptor
    ) -> tuple[Projection, Projection]:
        """Return the installed and available projections.

        Pure-analysis descriptors need no executable.  Compute descriptors
        probe every required ``software``/``binary`` precondition id through
        the injected resolver (the existing ``resolve_executable`` seam in
        production).  No task module is imported and no backend is selected.
        """
        if descriptor.kind is CapabilityKind.PURE_ANALYSIS:
            return (
                Projection(
                    CapabilityState.NOT_VERIFIED,
                    reason=(
                        "pure analysis reads existing artifacts; no installed "
                        "executable is required"
                    ),
                ),
                Projection(
                    CapabilityState.PRESENT,
                    reason="pure analysis requires no executable",
                ),
            )

        required_software = tuple(
            sorted(
                {
                    precondition.id
                    for precondition in descriptor.preconditions
                    if precondition.kind in (PreconditionKind.SOFTWARE, PreconditionKind.BINARY)
                    and precondition.required
                }
            )
        )
        if not required_software:
            reason = "no executable precondition is declared"
            return (
                Projection(CapabilityState.UNKNOWN, reason=reason),
                Projection(CapabilityState.UNKNOWN, reason=reason),
            )

        missing: list[str] = []
        resolved: list[str] = []
        for software in required_software:
            if self._resolver(software) is None:
                missing.append(software)
            else:
                resolved.append(software)
        if missing:
            reason = "unresolved executable: " + ", ".join(missing)
            return (
                Projection(CapabilityState.ABSENT, reason=reason),
                Projection(CapabilityState.ABSENT, reason=reason),
            )
        reason = "resolved executable: " + ", ".join(resolved)
        return (
            Projection(CapabilityState.PRESENT, reason=reason),
            Projection(CapabilityState.PRESENT, reason=reason),
        )
