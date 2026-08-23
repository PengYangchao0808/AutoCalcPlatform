"""v2 result parsing — QC outputs from ``WORK/`` into ``RESULT/`` viewer data (§14 Phase 5)."""

from __future__ import annotations

import logging

from acp.results.crest_parser import CrestEnsemble, parse_crest_ensemble
from acp.results.frequencies import build_frequency_report
from acp.results.manifest import (
    MANIFEST_FILENAME,
    Product,
    ProductKind,
    ResultManifest,
    find_products,
    load_result_manifest,
)
from acp.results.optimization import build_optimization_trajectory
from acp.results.orca_parser import OrcaCalculation, OrcaOutputParser
from acp.results.thermochemistry import build_thermo_report
from acp.results.xtb_parser import parse_xtb_energy, parse_xtb_opt_converged

logger = logging.getLogger(__name__)

__all__ = [
    "MANIFEST_FILENAME",
    "CrestEnsemble",
    "OrcaCalculation",
    "OrcaOutputParser",
    "Product",
    "ProductKind",
    "ResultManifest",
    "build_frequency_report",
    "build_optimization_trajectory",
    "build_thermo_report",
    "find_products",
    "load_result_manifest",
    "parse_crest_ensemble",
    "parse_xtb_energy",
    "parse_xtb_opt_converged",
]
