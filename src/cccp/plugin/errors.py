"""Typed failure taxonomy for the CCCP trusted-plugin pilot (plan T44).

Plugin rejections are pre-launch, typed, and fail closed: a plugin can never
silently replace a built-in capability.  Every error carries the plugin name
(where one is known) so callers can report a stable, machine-readable reason
without parsing free text.

Author: QCcalc Team
"""

from __future__ import annotations

__all__ = [
    "PluginDisabledError",
    "PluginError",
    "PluginLoadError",
    "PluginMetadataError",
    "PluginNameConflictError",
    "PluginUnavailableError",
    "PluginVersionMismatchError",
]


class PluginError(Exception):
    """Base class for every plugin-boundary rejection."""


class PluginMetadataError(PluginError, ValueError):
    """A plugin metadata record is missing a required field or is malformed."""


class PluginNameConflictError(PluginError, ValueError):
    """A plugin name collides with a built-in name or another plugin.

    This is the guard that makes "never override built-in" structural: the
    conflicting plugin is rejected before any loader is considered.
    """

    def __init__(self, name: str, *, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"plugin name {name!r} rejected: {reason}")


class PluginVersionMismatchError(PluginError, ValueError):
    """A plugin declares an API compatibility range that excludes this host."""

    def __init__(self, name: str, *, declared: str, host_api_version: int) -> None:
        self.name = name
        self.declared = declared
        self.host_api_version = host_api_version
        super().__init__(
            f"plugin {name!r} requires API {declared!r}, host API version is {host_api_version}"
        )


class PluginDisabledError(PluginError, RuntimeError):
    """Explicit enablement is required; plugins are disabled by default."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"plugin {name!r} is disabled by default; call PluginHost.enable({name!r}) "
            "explicitly before loading it"
        )


class PluginUnavailableError(PluginError, LookupError):
    """The plugin is unknown, or an enabled plugin has no in-process loader."""

    def __init__(self, name: str, *, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"plugin {name!r} unavailable: {reason}")


class PluginLoadError(PluginError, RuntimeError):
    """An enabled plugin's loader raised; the original cause is preserved."""

    def __init__(self, name: str, cause: BaseException) -> None:
        self.name = name
        self.cause = cause
        super().__init__(f"plugin {name!r} failed to load: {type(cause).__name__}: {cause}")
