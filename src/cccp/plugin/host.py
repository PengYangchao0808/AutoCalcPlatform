"""Enablement and loading boundary for the CCCP plugin pilot (plan T44).

Two deliberate gates sit between discovery and use:

1. **Enablement** — every plugin is disabled by default.  ``default_enabled``
   is declarative metadata only; the host never auto-enables from it.  A
   caller must explicitly call :meth:`PluginHost.enable`.
2. **Loading** — only an enabled plugin may be loaded, and only through an
   in-process loader supplied with the candidate.  There is no string/shell
   dispatch.  If a name is unknown or has no loader, the host raises a typed
   unavailable error instead of guessing.

Loaded instances are cached per host.  Disabling evicts the cached instance
so re-enabling re-runs the explicit loader.
"""

from __future__ import annotations

from cccp.plugin.discovery import PluginCandidate, PluginCatalog, discover_plugins
from cccp.plugin.errors import (
    PluginDisabledError,
    PluginError,
    PluginLoadError,
    PluginUnavailableError,
)
from cccp.plugin.metadata import BuiltinIdentity, PluginMetadata


class PluginHost:
    """Explicit, disabled-by-default plugin host.

    Construction performs metadata-only discovery.  No loader runs until
    :meth:`load` is called for a name that was explicitly enabled.
    """

    def __init__(
        self,
        *,
        builtin: BuiltinIdentity | None = None,
        candidates: tuple[PluginCandidate, ...] = (),
    ) -> None:
        self._candidates = tuple(candidates)
        self._catalog = discover_plugins(self._candidates, builtin=builtin)
        self._loaders = {
            candidate.metadata.name: candidate.loader for candidate in self._candidates
        }
        self._enabled: set[str] = set()
        self._loaded: dict[str, object] = {}

    @classmethod
    def builtin_only(cls) -> PluginHost:
        """Return a host with no external or pilot plugins (empty set)."""
        return cls()

    @classmethod
    def with_builtin_pilot(cls) -> PluginHost:
        """Return a host that *knows* the read-only parser pilot (still disabled)."""
        from cccp.plugin.readonly_parser import PILOT_CANDIDATE

        return cls(candidates=(PILOT_CANDIDATE,))

    @property
    def catalog(self) -> PluginCatalog:
        """The metadata-only discovery result."""
        return self._catalog

    def enabled_names(self) -> tuple[str, ...]:
        """Return explicitly enabled plugin names in catalog order."""
        return tuple(name for name in self._catalog.names() if name in self._enabled)

    def is_enabled(self, name: str) -> bool:
        """Return whether *name* was explicitly enabled."""
        return name in self._enabled

    def enable(self, name: str) -> PluginMetadata:
        """Explicitly enable one discovered plugin and return its metadata.

        Raises:
            PluginUnavailableError: the name was not discovered.
        """
        metadata = self._catalog.get(name)
        if metadata is None:
            raise PluginUnavailableError(name, reason="not discovered")
        self._enabled.add(name)
        return metadata

    def disable(self, name: str) -> None:
        """Disable *name* and evict any cached instance (idempotent)."""
        self._enabled.discard(name)
        self._loaded.pop(name, None)

    def load(self, name: str) -> object:
        """Load and return the enabled plugin instance for *name*.

        Raises:
            PluginUnavailableError: the name was not discovered, or the
                enabled candidate has no in-process loader.
            PluginDisabledError: the plugin exists but was not enabled.
            PluginLoadError: the enabled loader raised.
        """
        metadata = self._catalog.get(name)
        if metadata is None:
            raise PluginUnavailableError(name, reason="not discovered")
        if name not in self._enabled:
            raise PluginDisabledError(name)
        cached = self._loaded.get(name)
        if cached is not None:
            return cached
        loader = self._loaders.get(name)
        if loader is None:
            raise PluginUnavailableError(name, reason="no in-process loader supplied")
        try:
            instance = loader()
        except PluginError:
            raise
        except Exception as exc:
            raise PluginLoadError(name, exc) from exc
        self._loaded[name] = instance
        return instance


__all__ = ["PluginHost"]
