"""Compat re-export shell — TS mode mapping moved to cccp (T23, C04-s4).

The evidence-based source-mode → optimizer-mode mapping and the launch hard
gate now live in :mod:`cccp.analysis.tsmode.mode_mapping`.  This module keeps
the historical ``acp.calculations.tsmode.mode_mapping`` import surface working
by re-exporting the *same objects*; it carries no implementation logic.

The verified-version matrix is read from the cccp module at call time, so
callers that monkeypatch it must target
``cccp.analysis.tsmode.mode_mapping.TS_MODE_MAPPING_VERIFIED_VERSIONS``.
"""

from __future__ import annotations

from cccp.analysis.tsmode.mode_mapping import (
    DEGENERACY_GAP_DEFAULT,
    MAPPING_METHOD,
    MAPPING_VERSION,
    OVERLAP_THRESHOLD_DEFAULT,
    TS_MODE_MAPPING_VERIFIED_VERSIONS,
    enforce_launch_gate,
    overlap_evidence,
    resolve_target_mode,
)

__all__ = [
    "DEGENERACY_GAP_DEFAULT",
    "MAPPING_METHOD",
    "MAPPING_VERSION",
    "OVERLAP_THRESHOLD_DEFAULT",
    "TS_MODE_MAPPING_VERIFIED_VERSIONS",
    "enforce_launch_gate",
    "overlap_evidence",
    "resolve_target_mode",
]
