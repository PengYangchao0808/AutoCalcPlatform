"""Metadata-only discovery boundary for the CCCP plugin pilot (plan T44/T45).

Discovery is intentionally inert:

* it reads :class:`~cccp.plugin.metadata.PluginMetadata` records only;
* it never installs anything, never imports a plugin module, and never calls
  a candidate's loader;
* it fails closed on a name conflict, a duplicate name, an incompatible API
  or science range, an empty/unknown licence, or a missing manifest.

Two discovery sources exist and both stay metadata-only:

1. explicit :class:`PluginCandidate` objects (in-process callers and tests);
2. installed entry points in the stable ``cccp.science_plugins`` group
   (version 1).  For an entry point the manifest is read from the
   distribution's ``cccp_science_plugin.json`` file through
   :meth:`importlib.metadata.Distribution.read_text`; the entry point's
   ``load`` is stored behind a lazy loader and is invoked only after an
   explicit :meth:`cccp.plugin.host.PluginHost.enable` + ``load``.

The built-in task layer remains the **only** execution station.  Plugins can
add a read-only analysis capability, never a second task chain.
"""

from __future__ import annotations

import importlib.metadata
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field

from cccp.calculation.requests import TaskKind
from cccp.plugin.errors import (
    PluginCapabilityConflictError,
    PluginMetadataError,
    PluginNameConflictError,
    PluginVersionMismatchError,
)
from cccp.plugin.metadata import (
    PLUGIN_API_VERSION,
    PLUGIN_SCIENCE_VERSION,
    SCIENCE_PLUGIN_ENTRY_POINT_GROUP,
    SCIENCE_PLUGIN_ENTRY_POINT_VERSION,
    SCIENCE_PLUGIN_MANIFEST_FILENAME,
    BuiltinIdentity,
    PluginLicensePolicy,
    PluginMetadata,
)

PLUGIN_ENABLEMENT_POLICY = "disabled-by-default"
ENTRY_POINT_DISCOVERY_MODE = "entry_points"
EXPLICIT_DISCOVERY_MODE = "explicit"

#: Names a plugin may never take.  Built-in task kinds are reserved because a
#: plugin must not masquerade as an execution capability, and the parser /
#: frequency module seams are reserved because the pilot wraps them.
BUILTIN_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "cccp",
        "cccp.calculation",
        "frequency_parse",
        "orca_parser",
        "orca_output_parser",
        *[kind.value for kind in TaskKind],
    }
)

PluginLoader = Callable[[], object]


class EntryPointPluginLoader:
    """Lazy loader for one importlib entry point.

    Construction stores the entry point without touching it.  ``__call__``
    performs the import and runs only when :class:`~cccp.plugin.host.PluginHost`
    invokes the loader after an explicit enablement.
    """

    __slots__ = ("_entry_point",)

    def __init__(self, entry_point: object) -> None:
        self._entry_point = entry_point

    @property
    def value(self) -> str:
        """Return the raw entry-point value (never loaded)."""
        return str(getattr(self._entry_point, "value", ""))

    def __call__(self) -> object:
        loader = getattr(self._entry_point, "load", None)
        if not callable(loader):
            raise PluginMetadataError(
                f"entry point {self.value!r} does not provide a load() callable"
            )
        return loader()


@dataclass(frozen=True, slots=True)
class PluginCandidate:
    """One discovered candidate: metadata plus an opaque in-process loader.

    The loader is deliberately a Python callable, not an import string or a
    shell command: there is no arbitrary import/shell dispatch.  Discovery
    stores it but never calls it; only an explicit ``PluginHost.load`` after
    an explicit ``enable`` may.  ``origin`` records whether the candidate was
    supplied explicitly or scanned from the installed entry-point group.
    """

    metadata: PluginMetadata
    loader: PluginLoader | None = None
    origin: str = EXPLICIT_DISCOVERY_MODE
    entry_point: str | None = None


@dataclass(frozen=True, slots=True)
class PluginCatalog:
    """The discovery result: built-in identity plus the external plugin set."""

    builtin: BuiltinIdentity = field(default_factory=BuiltinIdentity)
    plugins: tuple[PluginMetadata, ...] = ()
    enablement_policy: str = PLUGIN_ENABLEMENT_POLICY
    discovery_mode: str = EXPLICIT_DISCOVERY_MODE
    entry_points_scanned: int = 0

    @property
    def is_empty(self) -> bool:
        """Return True when no external plugin was discovered."""
        return not self.plugins

    def names(self) -> tuple[str, ...]:
        """Return discovered plugin names in catalog order."""
        return tuple(metadata.name for metadata in self.plugins)

    def get(self, name: str) -> PluginMetadata | None:
        """Return the metadata record for *name* or ``None``."""
        for metadata in self.plugins:
            if metadata.name == name:
                return metadata
        return None

    def to_dict(self) -> dict[str, object]:
        """Serialise the catalog to a JSON-safe dictionary (empty set explicit)."""
        return {
            "builtin": self.builtin.to_dict(),
            "plugins": [metadata.to_dict() for metadata in self.plugins],
            "plugin_count": len(self.plugins),
            "is_empty": self.is_empty,
            "enablement_policy": self.enablement_policy,
            "discovery_mode": self.discovery_mode,
            "entry_points_scanned": self.entry_points_scanned,
        }


def builtin_catalog() -> PluginCatalog:
    """Return the catalog for a zero-external-plugin installation.

    This is the explicit empty-set representation required even when nothing
    is installed: the built-in identity is always present and ``plugins`` is
    an explicit empty tuple.
    """
    return PluginCatalog(builtin=BuiltinIdentity(), plugins=())


def discover_plugins(
    candidates: Iterable[PluginCandidate] = (),
    *,
    builtin: BuiltinIdentity | None = None,
) -> PluginCatalog:
    """Validate candidate metadata and return a catalog **without loading**.

    Raises:
        PluginNameConflictError: the name is reserved for a built-in, or two
            candidates declare the same name.
        PluginCapabilityConflictError: a capability is reserved for a
            built-in task, or two candidates declare the same capability.
        PluginVersionMismatchError: the declared API or science range
            excludes the host.

    The candidate ``loader`` is never invoked here.
    """
    identity = BuiltinIdentity() if builtin is None else builtin
    seen: set[str] = set()
    seen_capabilities: dict[str, str] = {}
    discovered: list[PluginMetadata] = []
    for candidate in candidates:
        metadata = candidate.metadata
        if metadata.name in BUILTIN_RESERVED_NAMES:
            raise PluginNameConflictError(
                metadata.name, reason="reserved built-in name (built-ins cannot be overridden)"
            )
        if metadata.name in seen:
            raise PluginNameConflictError(metadata.name, reason="duplicate plugin name")
        if not metadata.is_api_compatible(PLUGIN_API_VERSION):
            raise PluginVersionMismatchError(
                metadata.name,
                declared=metadata.api.compatible_range,
                host_api_version=PLUGIN_API_VERSION,
            )
        if not metadata.is_science_compatible(PLUGIN_SCIENCE_VERSION):
            raise PluginVersionMismatchError(
                metadata.name,
                declared=metadata.science_version_range,
                host_api_version=PLUGIN_SCIENCE_VERSION,
            )
        for capability in metadata.capabilities:
            if capability.name in BUILTIN_RESERVED_NAMES:
                raise PluginCapabilityConflictError(
                    metadata.name,
                    capability=capability.name,
                    reason="reserved built-in task/capability name (the built-in task layer "
                    "remains the only execution station)",
                )
            previous = seen_capabilities.get(capability.name)
            if previous is not None:
                raise PluginCapabilityConflictError(
                    metadata.name,
                    capability=capability.name,
                    reason=f"duplicate capability already declared by {previous!r}",
                )
            seen_capabilities[capability.name] = metadata.name
        seen.add(metadata.name)
        discovered.append(metadata)
    return PluginCatalog(builtin=identity, plugins=tuple(discovered))


def _iter_group_entry_points(entry_points: object, group: str) -> Iterator[object]:
    """Yield entry points in *group* across importlib.metadata versions."""
    if entry_points is None:
        entry_points = importlib.metadata.entry_points()
    select = getattr(entry_points, "select", None)
    if callable(select):
        yield from select(group=group)
        return
    if isinstance(entry_points, Mapping):
        yield from entry_points.get(group, ())
        return
    for entry_point in entry_points:  # type: ignore[union-attr]
        if getattr(entry_point, "group", None) == group:
            yield entry_point


def _read_entry_point_manifest(
    entry_point: object,
    manifest_reader: Callable[[object], str | None] | None,
) -> str | None:
    """Read the distribution manifest without importing plugin code."""
    if manifest_reader is not None:
        return manifest_reader(entry_point)
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return None
    read_text = getattr(distribution, "read_text", None)
    if not callable(read_text):
        return None
    try:
        return read_text(SCIENCE_PLUGIN_MANIFEST_FILENAME)
    except (KeyError, OSError, UnicodeError):
        return None


def discover_science_plugins(
    *,
    entry_points: object = None,
    manifest_reader: Callable[[object], str | None] | None = None,
    builtin: BuiltinIdentity | None = None,
    license_policy: PluginLicensePolicy | None = None,
) -> PluginCatalog:
    """Scan the ``cccp.science_plugins`` group for metadata-only candidates.

    The scan never imports plugin code: the manifest JSON is read from the
    installed distribution and the entry point is wrapped in
    :class:`EntryPointPluginLoader`, whose ``__call__`` is the only path that
    imports.  ``PluginHost`` calls that loader only after explicit enablement.

    Args:
        entry_points: Optional injected entry points (tests).  ``None`` reads
            :func:`importlib.metadata.entry_points`.
        manifest_reader: Optional injected manifest reader for tests.  The
            default reads ``cccp_science_plugin.json`` from ``ep.dist``.
        builtin: Optional built-in identity override.
        license_policy: Optional explicit accepted-license policy.  The
            platform owner may narrow the default set.

    Raises:
        PluginMetadataError: An entry point has no manifest or a malformed
            manifest, or its entry-point group version is not 1.
        PluginLicenseError: The manifest license is empty or unknown.
        PluginNameConflictError: A plugin name/capability is reserved or
            duplicated.
        PluginVersionMismatchError: The API or science range excludes the
            host.
    """
    candidates: list[PluginCandidate] = []
    scanned = 0
    for entry_point in _iter_group_entry_points(entry_points, SCIENCE_PLUGIN_ENTRY_POINT_GROUP):
        scanned += 1
        raw_manifest = _read_entry_point_manifest(entry_point, manifest_reader)
        entry_name = str(getattr(entry_point, "name", "") or "")
        if raw_manifest is None:
            raise PluginMetadataError(
                f"entry point {SCIENCE_PLUGIN_ENTRY_POINT_GROUP}:{entry_name} has no "
                f"{SCIENCE_PLUGIN_MANIFEST_FILENAME} manifest; refusing to import it"
            )
        try:
            payload = json.loads(raw_manifest)
        except json.JSONDecodeError as exc:
            raise PluginMetadataError(
                f"entry point {entry_name} has a malformed "
                f"{SCIENCE_PLUGIN_MANIFEST_FILENAME} manifest: {exc}"
            ) from exc
        metadata = PluginMetadata.from_dict(payload, license_policy=license_policy)
        if metadata.entry_point_group_version != SCIENCE_PLUGIN_ENTRY_POINT_VERSION:
            raise PluginMetadataError(
                f"entry point {entry_name} declares entry_point_group_version "
                f"{metadata.entry_point_group_version}; only "
                f"{SCIENCE_PLUGIN_ENTRY_POINT_VERSION} is supported"
            )
        candidates.append(
            PluginCandidate(
                metadata=metadata,
                loader=EntryPointPluginLoader(entry_point),
                origin=ENTRY_POINT_DISCOVERY_MODE,
                entry_point=f"{SCIENCE_PLUGIN_ENTRY_POINT_GROUP}:{entry_name}",
            )
        )
    catalog = discover_plugins(candidates, builtin=builtin)
    return PluginCatalog(
        builtin=catalog.builtin,
        plugins=catalog.plugins,
        enablement_policy=catalog.enablement_policy,
        discovery_mode=ENTRY_POINT_DISCOVERY_MODE,
        entry_points_scanned=scanned,
    )


__all__ = [
    "BUILTIN_RESERVED_NAMES",
    "ENTRY_POINT_DISCOVERY_MODE",
    "EXPLICIT_DISCOVERY_MODE",
    "PLUGIN_ENABLEMENT_POLICY",
    "EntryPointPluginLoader",
    "PluginCandidate",
    "PluginCatalog",
    "PluginLoader",
    "builtin_catalog",
    "discover_plugins",
    "discover_science_plugins",
]
