"""Declarative backend capability matrix — compat shim for :mod:`cccp.backends.matrix`."""

from __future__ import annotations

from cccp.backends.matrix import (
    CAPABILITY_ALIASES,
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    normalize_capability_name,
)

__all__ = ['CAPABILITY_ALIASES', 'CAPABILITY_MATRIX', 'BackendCapabilityStatus', 'normalize_capability_name']
