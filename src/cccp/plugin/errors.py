"""Typed failure taxonomy for the CCCP trusted-plugin pilot (plan T44).

Plugin rejections are pre-launch, typed, and fail closed: a plugin can never
silently replace a built-in capability.  Every error carries the plugin name
(where one is known) so callers can report a stable, machine-readable reason
without parsing free text.

Author: QCcalc Team
"""

from __future__ import annotations

__all__ = [
    "PluginCapabilityConflictError",
    "PluginContractViolationError",
    "PluginDependencyUnavailableError",
    "PluginDisabledError",
    "PluginError",
    "PluginLoadError",
    "PluginMetadataError",
    "PluginNameConflictError",
    "PluginTimeoutError",
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


class PluginCapabilityConflictError(PluginError, ValueError):
    """A capability collides with a built-in or with another plugin.

    Capability names are a namespace, not a free-form label: a collision is a
    typed rejection so a plugin can never silently shadow the built-in task
    layer or another installed plugin.
    """

    def __init__(self, name: str, *, capability: str, reason: str) -> None:
        self.name = name
        self.capability = capability
        self.reason = reason
        super().__init__(f"plugin {name!r} capability {capability!r} rejected: {reason}")


class PluginDependencyUnavailableError(PluginError, ImportError):
    """A declared runtime dependency is not importable in this environment.

    The SDK checks availability *before* invoking a loader and reports every
    missing dependency; it never installs anything.
    """

    def __init__(self, name: str, *, missing: tuple[str, ...]) -> None:
        self.name = name
        self.missing = tuple(missing)
        super().__init__(
            f"plugin {name!r} is unavailable: missing dependency(ies) {', '.join(self.missing)}"
        )


class PluginContractViolationError(PluginError, ValueError):
    """A plugin result violates the SDK output contract.

    Covers units, atom-order mapping, partial-output semantics, envelope
    identity and handler shape.  The violation is raised after the plugin ran;
    the caller must roll back to an explicit operator decision (never silently
    substitute a built-in result under the plugin's identity).
    """

    def __init__(self, name: str, *, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(f"plugin {name!r} violated the capability contract: {detail}")


class PluginTimeoutError(PluginError, TimeoutError):
    """A capability exceeded its cooperative timeout budget."""

    def __init__(self, name: str, *, timeout_s: float) -> None:
        self.name = name
        self.timeout_s = timeout_s
        super().__init__(f"plugin {name!r} exceeded timeout budget {timeout_s:g}s")
