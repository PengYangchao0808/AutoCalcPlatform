"""Constructed ACP2 v1 plugin distribution (A-23 upgrade pair).

This package is a separately installable fixture, not a CCCP module.  It
imports no task-layer module, launches no subprocess and creates no second
execution station.
"""

from __future__ import annotations

import importlib.metadata

DISTRIBUTION_NAME = "acp2-upgrade-science-validator"

try:
    __version__ = importlib.metadata.version(DISTRIBUTION_NAME)
except importlib.metadata.PackageNotFoundError:
    __version__ = "2.0.0"

__all__ = ["DISTRIBUTION_NAME", "__version__"]
