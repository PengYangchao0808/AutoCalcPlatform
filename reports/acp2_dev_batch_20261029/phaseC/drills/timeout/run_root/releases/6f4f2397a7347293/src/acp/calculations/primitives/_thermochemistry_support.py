"""Private validation, settings, and result-shaping helpers for Shermo (compat shim).

The implementation lives in ``cccp.qc.thermo_normalize`` (pure normalization)
and ``cccp.qc.shermo_adapter`` (settings resolution) since plan todo 14; this
module keeps the historical private import surface.
"""

from __future__ import annotations

from cccp.qc.shermo_adapter import resolve_runner_settings
from cccp.qc.thermo_normalize import (
    ShermoSettings,
    ShermoValues,
    ThermochemistryContext,
    ThermochemistryOutcome,
    build_metadata,
    parse_shermo_result,
    select_gibbs,
)

__all__ = [
    "ShermoSettings",
    "ShermoValues",
    "ThermochemistryContext",
    "ThermochemistryOutcome",
    "build_metadata",
    "parse_shermo_result",
    "resolve_runner_settings",
    "select_gibbs",
]
