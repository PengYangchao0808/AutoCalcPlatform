"""Compat re-export shell — implementation moved to cccp (plan todo 21).

The IRC path-trajectory recorder (live ``irc_trajectory_v1`` snapshot
capture) now lives in :mod:`cccp.calculation.irc_trajectory` so the cccp IRC
task core can own the execution-time instrumentation.  This module keeps the
historical import surface working; it carries no implementation logic.
"""

from __future__ import annotations

from cccp.calculation.irc_trajectory import (
    IRC_DIRECTIONS,
    IRC_TRAJECTORY_SCHEMA,
    IrcTrajectoryRecorder,
    collect_irc_path,
    resolve_ts_energy,
    write_irc_trajectory,
)

__all__ = [
    "IRC_DIRECTIONS",
    "IRC_TRAJECTORY_SCHEMA",
    "IrcTrajectoryRecorder",
    "collect_irc_path",
    "resolve_ts_energy",
    "write_irc_trajectory",
]
