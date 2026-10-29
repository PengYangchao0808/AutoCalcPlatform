# pyright: reportUnusedImport=false
"""Compat re-export shim for the optimization trajectory recorder (todo 18).

The implementation moved verbatim to
:mod:`cccp.calculation.optimization_trajectory` (stdlib-only).  This module
re-exports the public surface so existing ACP consumers and characterization
tests keep importing from the legacy home; ``item_id`` injection (platform
identity) stays on the ACP adapter side.
"""

from __future__ import annotations

from cccp.calculation.optimization_trajectory import (
    OptimizationTrajectoryRecorder,
    finalize_optimization_trajectory,
    merge_trajectories,
    parse_output_text,
)

__all__ = [
    "OptimizationTrajectoryRecorder",
    "finalize_optimization_trajectory",
    "merge_trajectories",
    "parse_output_text",
]
