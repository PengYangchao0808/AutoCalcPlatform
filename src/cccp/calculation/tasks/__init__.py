"""cccp task implementations (plan todos 17–22) — re-export shell only."""

from __future__ import annotations

from cccp.calculation.tasks.casscf import run_casscf
from cccp.calculation.tasks.frequency import run_frequency
from cccp.calculation.tasks.irc import run_irc
from cccp.calculation.tasks.optimize import run_optimize
from cccp.calculation.tasks.scan import run_scan
from cccp.calculation.tasks.singlepoint import run_singlepoint
from cccp.calculation.tasks.thermochemistry import run_thermochemistry

__all__ = [
    "run_casscf",
    "run_frequency",
    "run_irc",
    "run_optimize",
    "run_scan",
    "run_singlepoint",
    "run_thermochemistry",
]
