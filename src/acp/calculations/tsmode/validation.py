"""Compat re-export shell — TS validation moved to cccp (T23, C04-s4).

The saddle-point classification and mode-correspondence evidence now live in
:mod:`cccp.analysis.tsmode.validation`.  This module keeps the historical
``acp.calculations.tsmode.validation`` import surface working by re-exporting
the *same objects*; it carries no implementation logic.
"""

from __future__ import annotations

from cccp.analysis.tsmode.validation import (
    TsValidation,
    compare_mode_correspondence,
    validate_ts_frequencies,
)

__all__ = [
    "TsValidation",
    "validate_ts_frequencies",
    "compare_mode_correspondence",
]
