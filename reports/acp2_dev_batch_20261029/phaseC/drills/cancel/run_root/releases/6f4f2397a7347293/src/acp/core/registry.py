"""Generic type-safe registry — compat shim.

Implementation lives in :mod:`cccp.core.registry`; this module preserves the
historical ``acp.core.registry`` import surface with ``A is B`` identity.
"""

from __future__ import annotations

from cccp.core.registry import Registry as Registry

__all__ = ["Registry"]
