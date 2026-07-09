"""QC backend registry pattern."""

from __future__ import annotations

from typing import Any

from acp.backends.base import (
    ClusteringTool,
    ConformerSearcher,
    FrequencyCalculator,
    GeometryOptimizer,
    NMRCalculator,
    QCBackend,
    SinglePointCalculator,
    ThermoCalculator,
    TSMechanismCalculator,
)
from acp.core.registry import Registry

_CAPABILITY_PROTOCOLS: dict[str, type[Any]] = {
    "optimization": GeometryOptimizer,
    "optimizer": GeometryOptimizer,
    "geometry_optimization": GeometryOptimizer,
    "single_point": SinglePointCalculator,
    "sp": SinglePointCalculator,
    "frequency": FrequencyCalculator,
    "freq": FrequencyCalculator,
    "nmr": NMRCalculator,
    "conformer_search": ConformerSearcher,
    "search": ConformerSearcher,
    "clustering": ClusteringTool,
    "cluster": ClusteringTool,
    "thermochemistry": ThermoCalculator,
    "thermo": ThermoCalculator,
    "ts": TSMechanismCalculator,
    "transition_state": TSMechanismCalculator,
}


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
        """Return a backend class supporting *capability* or raise."""
        protocol = self._resolve_capability_protocol(capability)

        for _, backend_cls in self.list_all():
            if issubclass(backend_cls, protocol):
                return backend_cls

        available = ", ".join(name for name, _ in self.list_all()) or "none"
        raise LookupError(
            f"No registered backend supports capability '{capability}'. "
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

    @staticmethod
    def _resolve_capability_protocol(capability: str) -> type[Any]:
        key = capability.lower()
        if key not in _CAPABILITY_PROTOCOLS:
            known = ", ".join(sorted(_CAPABILITY_PROTOCOLS))
            raise ValueError(f"Unknown capability: {capability}. Known: {known}")
        return _CAPABILITY_PROTOCOLS[key]


backend_registry = BackendRegistry()


def register_backend(backend_cls: type[QCBackend]) -> None:
    """Register a backend class in the shared backend registry."""
    backend_registry.register(backend_cls)


def get_backend(name: str) -> type[QCBackend]:
    """Look up a backend class by name."""
    return backend_registry.get(name)


def require_backend(capability: str) -> type[QCBackend]:
    """Return a registered backend class supporting *capability* or raise."""
    return backend_registry.require(capability)


__all__ = [
    "BackendRegistry",
    "backend_registry",
    "register_backend",
    "get_backend",
    "require_backend",
]
