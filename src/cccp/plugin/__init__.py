"""CCCP trusted-plugin pilot surface (plan T44).

Pure re-exports.  Implementation lives in the sibling modules:

* :mod:`cccp.plugin.metadata` — frozen metadata contract + built-in identity
* :mod:`cccp.plugin.discovery` — metadata-only discovery + empty-set catalog
* :mod:`cccp.plugin.host` — disabled-by-default enable/load boundary
* :mod:`cccp.plugin.readonly_parser` — ORCA read-only parser pilot
* :mod:`cccp.plugin.errors` — typed rejection taxonomy
"""

from __future__ import annotations

from cccp.plugin.discovery import (
    BUILTIN_RESERVED_NAMES,
    PLUGIN_ENABLEMENT_POLICY,
    PluginCandidate,
    PluginCatalog,
    PluginLoader,
    builtin_catalog,
    discover_plugins,
)
from cccp.plugin.errors import (
    PluginDisabledError,
    PluginError,
    PluginLoadError,
    PluginMetadataError,
    PluginNameConflictError,
    PluginUnavailableError,
    PluginVersionMismatchError,
)
from cccp.plugin.host import PluginHost
from cccp.plugin.metadata import (
    BUILTIN_EXECUTION_STATION,
    PLUGIN_API_COMPATIBILITY,
    PLUGIN_API_VERSION,
    PLUGIN_SCHEMA_VERSION,
    ApiCompatibility,
    BuiltinIdentity,
    ErrorSemantics,
    PluginCapability,
    PluginMetadata,
    ResourceRequirements,
    ScienceSoftwareVersion,
    api_version_in_range,
)
from cccp.plugin.readonly_parser import (
    ORCA_READONLY_PARSER_NAME,
    ORCA_READONLY_PARSER_SCHEMA,
    ORCA_READONLY_PARSER_SCHEMA_VERSION,
    PILOT_CANDIDATE,
    OrcaReadonlyParserPlugin,
    readonly_parser_metadata,
)

__all__ = [
    "BUILTIN_EXECUTION_STATION",
    "BUILTIN_RESERVED_NAMES",
    "ORCA_READONLY_PARSER_NAME",
    "ORCA_READONLY_PARSER_SCHEMA",
    "ORCA_READONLY_PARSER_SCHEMA_VERSION",
    "PILOT_CANDIDATE",
    "PLUGIN_API_COMPATIBILITY",
    "PLUGIN_API_VERSION",
    "PLUGIN_ENABLEMENT_POLICY",
    "PLUGIN_SCHEMA_VERSION",
    "ApiCompatibility",
    "BuiltinIdentity",
    "ErrorSemantics",
    "OrcaReadonlyParserPlugin",
    "PluginCandidate",
    "PluginCatalog",
    "PluginCapability",
    "PluginDisabledError",
    "PluginError",
    "PluginHost",
    "PluginLoadError",
    "PluginLoader",
    "PluginMetadata",
    "PluginMetadataError",
    "PluginNameConflictError",
    "PluginUnavailableError",
    "PluginVersionMismatchError",
    "ResourceRequirements",
    "ScienceSoftwareVersion",
    "api_version_in_range",
    "builtin_catalog",
    "discover_plugins",
    "readonly_parser_metadata",
]
