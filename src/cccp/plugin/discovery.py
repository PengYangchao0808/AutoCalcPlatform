"""Metadata-only discovery boundary for the CCCP plugin pilot (plan T44).

Discovery is intentionally inert:

* it reads :class:`~cccp.plugin.metadata.PluginMetadata` records only;
* it never installs anything, never imports a plugin module, and never calls
  a candidate's loader;
* it fails closed on a name conflict, a duplicate name, or an incompatible
  API range.

The built-in task layer remains the **only** execution station.  Plugins can
add a read-only analysis capability, never a second task chain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from cccp.calculation.requests import TaskKind
from cccp.plugin.errors import (
    PluginCapabilityConflictError,
    PluginNameConflictError,
    PluginVersionMismatchError,
)
from cccp.plugin.metadata import (
    PLUGIN_API_VERSION,
    BuiltinIdentity,
    PluginMetadata,
)

PLUGIN_ENABLEMENT_POLICY = "disabled-by-default"

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


@dataclass(frozen=True, slots=True)
class PluginCandidate:
    """One discovered candidate: metadata plus an opaque in-process loader.

    The loader is deliberately a Python callable, not an import string or a
    shell command: there is no arbitrary import/shell dispatch.  Discovery
    stores it but never calls it; only an explicit ``PluginHost.load`` after
    an explicit ``enable`` may.
    """

    metadata: PluginMetadata
    loader: PluginLoader | None = None


@dataclass(frozen=True, slots=True)
class PluginCatalog:
    """The discovery result: built-in identity plus the external plugin set."""

    builtin: BuiltinIdentity = field(default_factory=BuiltinIdentity)
    plugins: tuple[PluginMetadata, ...] = ()
    enablement_policy: str = PLUGIN_ENABLEMENT_POLICY

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
        PluginVersionMismatchError: the declared API range excludes the host.

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


__all__ = [
    "BUILTIN_RESERVED_NAMES",
    "PLUGIN_ENABLEMENT_POLICY",
    "PluginCandidate",
    "PluginCatalog",
    "PluginLoader",
    "builtin_catalog",
    "discover_plugins",
]
