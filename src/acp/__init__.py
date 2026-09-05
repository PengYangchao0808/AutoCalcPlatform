"""ACP — Auto-Calc Platform.

Main package for the Auto-Calc Platform (ACP). Phase 1 provides the
conformer search workflow; Phase 2 adds the API server.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("acp")
except PackageNotFoundError:
    __version__ = "0.1.1"


__all__ = ["__version__"]
