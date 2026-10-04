"""cccp task implementations (plan todos 17–22 / 42–43) — re-export shell only."""

from __future__ import annotations

from cccp.calculation.tasks.casscf import run_casscf
from cccp.calculation.tasks.clustering import run_clustering
from cccp.calculation.tasks.conformer_search import run_conformer_search
from cccp.calculation.tasks.frequency import run_frequency
from cccp.calculation.tasks.irc import run_irc
from cccp.calculation.tasks.md_sampling import run_md_sampling
from cccp.calculation.tasks.optimize import run_optimize
from cccp.calculation.tasks.scan import run_scan
from cccp.calculation.tasks.singlepoint import run_singlepoint
from cccp.calculation.tasks.thermochemistry import run_thermochemistry
from cccp.calculation.tasks.xtb_path_search import run_xtb_path_search

__all__ = [
    "run_casscf",
    "run_conformer_search",
    "run_clustering",
    "run_frequency",
    "run_irc",
    "run_md_sampling",
    "run_optimize",
    "run_scan",
    "run_singlepoint",
    "run_thermochemistry",
    "run_xtb_path_search",
]
