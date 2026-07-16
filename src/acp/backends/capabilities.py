"""Capability matrix helpers for ACP backends."""

from __future__ import annotations

from enum import Enum

from acp.backends.crest import CrestBackend
from acp.backends.external_backend import ExternalBackend
from acp.backends.isostat_backend import IsostatBackend
from acp.backends.molclus_backend import MolclusBackend
from acp.backends.orca import ORCABackend
from acp.backends.registry import get_backend
from acp.backends.xtb import XTBBackend


class BackendCapabilityStatus(str, Enum):
    """Declared status for a backend capability."""

    AVAILABLE = "available"
    STUBBED = "stubbed"
    NOT_IMPLEMENTED = "not_implemented"
    MISSING_BINARY = "missing_binary"


_ = (
    ORCABackend,
    CrestBackend,
    XTBBackend,
    ExternalBackend,
    MolclusBackend,
    IsostatBackend,
)

_CAPABILITY_ALIASES = {
    "optimization": "geometry_optimization",
    "optimizer": "geometry_optimization",
    "geometry_optimization": "geometry_optimization",
    "single_point": "single_point",
    "sp": "single_point",
    "frequency": "frequency",
    "freq": "frequency",
    "nmr": "nmr",
    "conformer_search": "conformer_search",
    "search": "conformer_search",
    "clustering": "clustering",
    "cluster": "clustering",
    "thermochemistry": "thermochemistry",
    "thermo": "thermochemistry",
}

CAPABILITY_MATRIX: dict[str, dict[str, BackendCapabilityStatus]] = {
    "orca": {
        "geometry_optimization": BackendCapabilityStatus.AVAILABLE,
        "single_point": BackendCapabilityStatus.AVAILABLE,
        "frequency": BackendCapabilityStatus.AVAILABLE,
        "nmr": BackendCapabilityStatus.AVAILABLE,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "crest": {
        "geometry_optimization": BackendCapabilityStatus.AVAILABLE,
        "single_point": BackendCapabilityStatus.AVAILABLE,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.AVAILABLE,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "xtb": {
        "geometry_optimization": BackendCapabilityStatus.AVAILABLE,
        "single_point": BackendCapabilityStatus.AVAILABLE,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "external": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.MISSING_BINARY,
        "thermochemistry": BackendCapabilityStatus.MISSING_BINARY,
    },
    "molclus": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.AVAILABLE,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "isostat": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.AVAILABLE,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
}


def _normalize_capability_name(capability: str) -> str:
    key = capability.lower()
    if key not in _CAPABILITY_ALIASES:
        known = ", ".join(sorted(_CAPABILITY_ALIASES))
        raise ValueError(f"Unknown capability: {capability}. Known: {known}")
    return _CAPABILITY_ALIASES[key]


def _normalize_backend_name(backend_name: str) -> str:
    key = backend_name.lower()
    if key in CAPABILITY_MATRIX:
        return key

    try:
        backend_cls = get_backend(key)
    except KeyError as exc:
        known = ", ".join(sorted(CAPABILITY_MATRIX))
        raise KeyError(f"Unknown backend: {backend_name}. Known: {known}") from exc

    canonical_name = getattr(backend_cls, "name", "") or backend_cls.__name__.removesuffix("Backend")
    canonical_name = canonical_name.lower()
    if canonical_name not in CAPABILITY_MATRIX:
        known = ", ".join(sorted(CAPABILITY_MATRIX))
        raise KeyError(f"Unknown backend: {backend_name}. Known: {known}")
    return canonical_name


def supports(backend_name: str, capability: str) -> bool:
    """Return True only when the declared matrix status is AVAILABLE."""

    canonical_backend = _normalize_backend_name(backend_name)
    canonical_capability = _normalize_capability_name(capability)
    return CAPABILITY_MATRIX[canonical_backend][canonical_capability] is BackendCapabilityStatus.AVAILABLE


def list_capabilities(backend_name: str) -> dict[str, BackendCapabilityStatus]:
    """Return the declared capability statuses for *backend_name*."""

    canonical_backend = _normalize_backend_name(backend_name)
    return dict(CAPABILITY_MATRIX[canonical_backend])


def list_backends(capability: str | None = None) -> list[str]:
    """List all backends, or only those with an AVAILABLE declared capability."""

    if capability is None:
        return sorted(CAPABILITY_MATRIX)

    canonical_capability = _normalize_capability_name(capability)
    return [
        backend_name
        for backend_name in sorted(CAPABILITY_MATRIX)
        if CAPABILITY_MATRIX[backend_name][canonical_capability] is BackendCapabilityStatus.AVAILABLE
    ]


def backend_status(backend_name: str) -> dict[str, object]:
    """Return declared and runtime capability status for *backend_name*."""

    canonical_backend = _normalize_backend_name(backend_name)
    declared_capabilities = list_capabilities(canonical_backend)
    backend_cls = get_backend(canonical_backend)
    backend = backend_cls({})
    backend_available = backend.is_available()

    actual_capabilities: dict[str, BackendCapabilityStatus] = {}
    for capability_name, declared_status in declared_capabilities.items():
        actual_status = declared_status
        if canonical_backend == "external" and isinstance(backend, ExternalBackend):
            if capability_name == "clustering":
                actual_status = (
                    BackendCapabilityStatus.AVAILABLE
                    if backend.is_isostat_available()
                    else BackendCapabilityStatus.MISSING_BINARY
                )
            elif capability_name == "thermochemistry":
                actual_status = (
                    BackendCapabilityStatus.AVAILABLE
                    if backend.is_shermo_available()
                    else BackendCapabilityStatus.MISSING_BINARY
                )
        elif declared_status is BackendCapabilityStatus.AVAILABLE and not backend_available:
            actual_status = BackendCapabilityStatus.MISSING_BINARY

        actual_capabilities[capability_name] = actual_status

    return {
        "name": canonical_backend,
        "backend_class": backend_cls.__name__,
        "is_available": backend_available,
        "declared_capabilities": declared_capabilities,
        "capabilities": actual_capabilities,
    }


__all__ = [
    "BackendCapabilityStatus",
    "CAPABILITY_MATRIX",
    "supports",
    "list_capabilities",
    "list_backends",
    "backend_status",
]
