"""Capability matrix helpers for ACP backends.

The declaration data lives in :mod:`cccp.backends.matrix` (dependency-free);
this module layers registry-aware name normalization and runtime status
reporting on top of it.  Both this module and :mod:`cccp.backends.registry`
import from ``matrix`` — never the other way round — so no import cycle.
"""

from __future__ import annotations

from cccp.backends.crest import CrestBackend
from cccp.backends.external_backend import ExternalBackend
from cccp.backends.isostat_backend import IsostatBackend
from cccp.backends.matrix import (
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    normalize_capability_name,
)
from cccp.backends.molclus_backend import MolclusBackend
from cccp.backends.orca import ORCABackend
from cccp.backends.registry import get_backend
from cccp.backends.xtb import XTBBackend

_ = (
    ORCABackend,
    CrestBackend,
    XTBBackend,
    ExternalBackend,
    MolclusBackend,
    IsostatBackend,
)


def _normalize_backend_name(backend_name: str) -> str:
    key = backend_name.lower()
    if key in CAPABILITY_MATRIX:
        return key

    try:
        backend_cls = get_backend(key)
    except KeyError as exc:
        known = ", ".join(sorted(CAPABILITY_MATRIX))
        raise KeyError(f"Unknown backend: {backend_name}. Known: {known}") from exc

    canonical_name = getattr(backend_cls, "name", "") or backend_cls.__name__.removesuffix(
        "Backend"
    )
    canonical_name = canonical_name.lower()
    if canonical_name not in CAPABILITY_MATRIX:
        known = ", ".join(sorted(CAPABILITY_MATRIX))
        raise KeyError(f"Unknown backend: {backend_name}. Known: {known}")
    return canonical_name


def supports(backend_name: str, capability: str) -> bool:
    """Return True only when the declared matrix status is AVAILABLE."""

    canonical_backend = _normalize_backend_name(backend_name)
    canonical_capability = normalize_capability_name(capability)
    return (
        CAPABILITY_MATRIX[canonical_backend][canonical_capability]
        is BackendCapabilityStatus.AVAILABLE
    )


def list_capabilities(backend_name: str) -> dict[str, BackendCapabilityStatus]:
    """Return the declared capability statuses for *backend_name*."""

    canonical_backend = _normalize_backend_name(backend_name)
    return dict(CAPABILITY_MATRIX[canonical_backend])


def list_backends(capability: str | None = None) -> list[str]:
    """List all backends, or only those with an AVAILABLE declared capability."""

    if capability is None:
        return sorted(CAPABILITY_MATRIX)

    canonical_capability = normalize_capability_name(capability)
    return [
        backend_name
        for backend_name in sorted(CAPABILITY_MATRIX)
        if (
            CAPABILITY_MATRIX[backend_name][canonical_capability]
            is BackendCapabilityStatus.AVAILABLE
        )
    ]


def backend_status(backend_name: str) -> dict[str, object]:
    """Return declared and runtime capability status for *backend_name*.

    Declarations answer "is it implemented?"; runtime probes answer "is the
    binary actually present?".  ``external`` uses per-capability probes
    (``is_isostat_available`` / ``is_shermo_available``); other backends fall
    back to the backend-wide ``is_available()``.
    """

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
