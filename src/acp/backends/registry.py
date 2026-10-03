"""QC backend registry pattern."""

from __future__ import annotations

from acp.backends.base import QCBackend
from acp.backends.matrix import (
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    normalize_capability_name,
)
from acp.core.registry import Registry
from cccp.calculation.errors import UnsupportedCapabilityError


class BackendRegistry:
    """Registry for discovering and validating QC backends."""

    def __init__(self) -> None:
        self._registry: Registry[type[QCBackend]] = Registry()
        self._canonical: dict[str, type[QCBackend]] = {}

    def register(self, backend_cls: type[QCBackend]) -> None:
        """Register *backend_cls* under its canonical name and aliases."""
        canonical_name = self._canonical_name(backend_cls)
        self._canonical[canonical_name] = backend_cls

        for alias in self._aliases(backend_cls):
            self._registry.register(alias, backend_cls)

    def get(self, name: str) -> type[QCBackend]:
        """Return a registered backend class by name."""
        return self._registry.get(name)

    def require(self, capability: str) -> type[QCBackend]:
        """Return the first registered backend declaring *capability* AVAILABLE.

        Selection is declaration-driven (``acp.backends.matrix``) and always
        completes before any backend instance is constructed: stubs and
        unimplemented capabilities can never be selected (delta D1).  Binary
        presence is not judged here — runtime probes surface
        ``BackendUnavailableError`` when a declared capability lacks its tool.

        Raises:
            ValueError: If the capability name is unknown.
            UnsupportedCapabilityError: If no registered backend declares the
                capability implemented.
        """
        canonical_capability = normalize_capability_name(capability)

        for name, backend_cls in self.list_all():
            row = CAPABILITY_MATRIX.get(name)
            if row is None:
                continue
            if row.get(canonical_capability) is BackendCapabilityStatus.AVAILABLE:
                return backend_cls

        available = ", ".join(name for name, _ in self.list_all()) or "none"
        raise UnsupportedCapabilityError(
            f"No registered backend implements capability '{capability}'. "
            f"Available backends: {available}"
        )

    def list_all(self) -> list[tuple[str, type[QCBackend]]]:
        """Return registered backends as ``(name, class)`` pairs."""
        return sorted(self._canonical.items())

    @staticmethod
    def _canonical_name(backend_cls: type[QCBackend]) -> str:
        name = getattr(backend_cls, "name", "") or backend_cls.__name__.removesuffix("Backend")
        return name.lower()

    @classmethod
    def _aliases(cls, backend_cls: type[QCBackend]) -> list[str]:
        canonical_name = cls._canonical_name(backend_cls)
        aliases = {canonical_name, backend_cls.__name__.lower()}
        return sorted(aliases)


backend_registry = BackendRegistry()


def register_backend(backend_cls: type[QCBackend]) -> None:
    """Register a backend class in the shared backend registry."""
    backend_registry.register(backend_cls)


def get_backend(name: str) -> type[QCBackend]:
    """Look up a backend class by name."""
    return backend_registry.get(name)


def require_backend(capability: str) -> type[QCBackend]:
    """Return a registered backend class declaring *capability* AVAILABLE or raise."""
    return backend_registry.require(capability)


__all__ = [
    "BackendRegistry",
    "backend_registry",
    "register_backend",
    "get_backend",
    "require_backend",
]
