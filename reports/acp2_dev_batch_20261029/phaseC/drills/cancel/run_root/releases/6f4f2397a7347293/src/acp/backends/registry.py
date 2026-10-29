"""QC backend registry — compat shim for :mod:`cccp.backends.registry`."""

from __future__ import annotations

from cccp.backends.registry import (
    BackendRegistry,
    backend_registry,
    get_backend,
    register_backend,
    require_backend,
)

__all__ = ['BackendRegistry', 'backend_registry', 'get_backend', 'register_backend', 'require_backend']
