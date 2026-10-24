"""Read-only ACP2 external science plugin fixture (A-22a).

This package is a **separately installable** distribution, not a CCCP module.
It is deliberately tiny: one deterministic, read-only validator for a fixed
native single-point record format.  It imports no task-layer module
(``cccp.calculation.tasks``), launches no subprocess and creates no second
execution station.

The distribution is discovered through the stable ``cccp.science_plugins``
entry-point group (version 1); the platform reads ``cccp_science_plugin.json``
from the installed ``.dist-info`` metadata before any module import.
"""

from __future__ import annotations

import importlib.metadata

DISTRIBUTION_NAME = "acp2-external-science-validator"

try:  # installed distribution: the wheel record is authoritative
    __version__ = importlib.metadata.version(DISTRIBUTION_NAME)
except importlib.metadata.PackageNotFoundError:  # running from the source tree
    __version__ = "1.0.0"

__all__ = ["DISTRIBUTION_NAME", "__version__"]
