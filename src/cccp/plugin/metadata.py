"""Frozen plugin metadata contract for the CCCP trusted-plugin pilot (plan T44).

Discovery reads **metadata only**; nothing here launches a program, imports a
plugin module, or creates a second execution station.  The contract exists
even when no external plugin is installed: :class:`BuiltinIdentity` plus an
explicit empty plugin set is a valid, serialisable catalog (see
:func:`cccp.plugin.discovery.builtin_catalog`).

The built-in task layer (``cccp.calculation``) remains the single execution
station.  The pilot capability is a read-only analysis seam, so it declares
``requires_subprocess=False`` and can never execute a QC program.

Author: QCcalc Team
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cccp.plugin.errors import PluginMetadataError
from cccp.version import __version__

PLUGIN_API_VERSION = 1
PLUGIN_SCHEMA_VERSION = 1
PLUGIN_API_COMPATIBILITY = ">=1,<2"
BUILTIN_EXECUTION_STATION = "cccp.calculation"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_RANGE_TOKEN_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+)$")


def api_version_in_range(version: int, expression: str) -> bool:
    """Return whether integer *version* satisfies a comma-separated range.

    Supported tokens are ``>=N``, ``>N``, ``<=N``, ``<N`` and ``==N``.  An
    empty or malformed expression never matches (fail closed).
    """
    tokens = [token.strip() for token in expression.split(",") if token.strip()]
    if not tokens:
        return False
    for token in tokens:
        match = _RANGE_TOKEN_RE.match(token)
        if match is None:
            return False
        operator, raw_bound = match.groups()
        bound = int(raw_bound)
        if operator == ">=" and not version >= bound:
            return False
        if operator == ">" and not version > bound:
            return False
        if operator == "<=" and not version <= bound:
            return False
        if operator == "<" and not version < bound:
            return False
        if operator == "==" and not version == bound:
            return False
    return True


@dataclass(frozen=True, slots=True)
class ApiCompatibility:
    """API contract a plugin was built against.

    ``api_version`` is the version the plugin targets, ``compatible_range``
    is the range it accepts from the host, and ``schema_version`` versions
    the metadata record itself.
    """

    api_version: int = PLUGIN_API_VERSION
    compatible_range: str = PLUGIN_API_COMPATIBILITY
    schema_version: int = PLUGIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.api_version < 1:
            raise PluginMetadataError("api.api_version must be >= 1")
        if self.schema_version < 1:
            raise PluginMetadataError("api.schema_version must be >= 1")
        if not api_version_in_range(self.api_version, f"=={self.api_version}"):
            raise PluginMetadataError("api.api_version must be a positive integer")
        if not self.compatible_range.strip():
            raise PluginMetadataError("api.compatible_range must not be empty")

    def supports_host_api(self, host_api_version: int = PLUGIN_API_VERSION) -> bool:
        """Return whether *host_api_version* falls in the declared range."""
        return api_version_in_range(host_api_version, self.compatible_range)


@dataclass(frozen=True, slots=True)
class ScienceSoftwareVersion:
    """Science-software version window the plugin was validated against.

    ``verified`` is the tri-state evidence marker: ``False`` means the range
    is declared but has not been confirmed with the real binary in this
    environment (recorded as NOT_VERIFIED, never as a pass).
    """

    software: str
    version_range: str
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.software.strip():
            raise PluginMetadataError("science_software.software must not be empty")
        if not self.version_range.strip():
            raise PluginMetadataError("science_software.version_range must not be empty")


@dataclass(frozen=True, slots=True)
class PluginCapability:
    """One capability the plugin declares, with the schema it emits."""

    name: str
    schema: str
    schema_version: int = PLUGIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PluginMetadataError("capability.name must not be empty")
        if not self.schema.strip():
            raise PluginMetadataError("capability.schema must not be empty")
        if self.schema_version < 1:
            raise PluginMetadataError("capability.schema_version must be >= 1")


@dataclass(frozen=True, slots=True)
class ResourceRequirements:
    """Declared resource needs; used for reporting, never auto-granted."""

    requires_subprocess: bool = False
    requires_network: bool = False
    min_memory_mb: int | None = None
    cpu_cores: int | None = None

    def __post_init__(self) -> None:
        if self.min_memory_mb is not None and self.min_memory_mb < 0:
            raise PluginMetadataError("resources.min_memory_mb must be >= 0")
        if self.cpu_cores is not None and self.cpu_cores < 1:
            raise PluginMetadataError("resources.cpu_cores must be >= 1")


@dataclass(frozen=True, slots=True)
class ErrorSemantics:
    """Frozen failure contract a plugin must honour.

    The host refuses metadata that claims it may override a built-in
    capability; override is not a supported plugin behaviour.
    """

    pre_launch_typed: bool = True
    partial_output_tolerant: bool = True
    never_overrides_builtin: bool = True


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """The complete, frozen discovery record for one plugin.

    Every field required by plan T44 is present and non-optional except the
    explicitly-optional lists/maps.  The record is pure data: constructing it
    never imports the plugin and never runs its loader.
    """

    name: str
    distribution: str
    version: str
    api: ApiCompatibility = field(default_factory=ApiCompatibility)
    capabilities: tuple[PluginCapability, ...] = ()
    science_software: tuple[ScienceSoftwareVersion, ...] = ()
    resources: ResourceRequirements = field(default_factory=ResourceRequirements)
    license: str = ""
    dependencies: tuple[str, ...] = ()
    error_semantics: ErrorSemantics = field(default_factory=ErrorSemantics)
    default_enabled: bool = False

    def __post_init__(self) -> None:
        if _NAME_RE.match(self.name) is None:
            raise PluginMetadataError(
                f"plugin name {self.name!r} must be dotted lowercase "
                "(e.g. 'vendor.readonly_parser')"
            )
        if not self.distribution.strip():
            raise PluginMetadataError("distribution must not be empty")
        if not self.version.strip():
            raise PluginMetadataError("version must not be empty")
        if not self.license.strip():
            raise PluginMetadataError("license must be declared (empty is not allowed)")
        if not self.capabilities:
            raise PluginMetadataError("at least one capability must be declared")
        capability_names = [capability.name for capability in self.capabilities]
        if len(set(capability_names)) != len(capability_names):
            raise PluginMetadataError("capability names must be unique")
        if not self.error_semantics.never_overrides_builtin:
            raise PluginMetadataError("error_semantics.never_overrides_builtin must be True")
        object.__setattr__(self, "dependencies", tuple(str(dep) for dep in self.dependencies))

    def is_api_compatible(self, host_api_version: int = PLUGIN_API_VERSION) -> bool:
        """Return whether this plugin accepts *host_api_version*."""
        return self.api.supports_host_api(host_api_version)

    def to_dict(self) -> dict[str, object]:
        """Serialise the metadata to a JSON-safe dictionary."""
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "api": {
                "api_version": self.api.api_version,
                "compatible_range": self.api.compatible_range,
                "schema_version": self.api.schema_version,
            },
            "capabilities": [
                {
                    "name": capability.name,
                    "schema": capability.schema,
                    "schema_version": capability.schema_version,
                }
                for capability in self.capabilities
            ],
            "science_software": [
                {
                    "software": entry.software,
                    "version_range": entry.version_range,
                    "verified": entry.verified,
                }
                for entry in self.science_software
            ],
            "resources": {
                "requires_subprocess": self.resources.requires_subprocess,
                "requires_network": self.resources.requires_network,
                "min_memory_mb": self.resources.min_memory_mb,
                "cpu_cores": self.resources.cpu_cores,
            },
            "license": self.license,
            "dependencies": list(self.dependencies),
            "error_semantics": {
                "pre_launch_typed": self.error_semantics.pre_launch_typed,
                "partial_output_tolerant": self.error_semantics.partial_output_tolerant,
                "never_overrides_builtin": self.error_semantics.never_overrides_builtin,
            },
            "default_enabled": self.default_enabled,
        }


@dataclass(frozen=True, slots=True)
class BuiltinIdentity:
    """Version identity of the host itself.

    Present even with zero external plugins so a catalog always states which
    CCCP/API executed it and which station owns execution.
    """

    distribution: str = "cccp"
    version: str = __version__
    api_compatibility: str = PLUGIN_API_COMPATIBILITY
    execution_station: str = BUILTIN_EXECUTION_STATION

    def to_dict(self) -> dict[str, object]:
        """Serialise the built-in identity to a JSON-safe dictionary."""
        return {
            "distribution": self.distribution,
            "version": self.version,
            "api_compatibility": self.api_compatibility,
            "execution_station": self.execution_station,
        }


__all__ = [
    "BUILTIN_EXECUTION_STATION",
    "PLUGIN_API_COMPATIBILITY",
    "PLUGIN_API_VERSION",
    "PLUGIN_SCHEMA_VERSION",
    "ApiCompatibility",
    "BuiltinIdentity",
    "ErrorSemantics",
    "PluginCapability",
    "PluginMetadata",
    "ResourceRequirements",
    "ScienceSoftwareVersion",
    "api_version_in_range",
]
