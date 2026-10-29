"""Capability matrix helpers — compat shim for :mod:`cccp.backends.capabilities`."""

from __future__ import annotations

from cccp.backends.capabilities import (
    BackendCapabilityStatus,
    CAPABILITY_MATRIX,
    backend_status,
    list_backends,
    list_capabilities,
    supports,
)

__all__ = ['BackendCapabilityStatus', 'CAPABILITY_MATRIX', 'backend_status', 'list_backends', 'list_capabilities', 'supports']
