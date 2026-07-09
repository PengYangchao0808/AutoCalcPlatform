# pyright: reportMissingTypeStubs=false
"""NMR domain helpers and models."""

from __future__ import annotations

import logging

from acp.nmr.calibration import (
    assign_nucleus,
    average_atom_results,
    calibrate_shifts,
    select_conformers,
)
from acp.nmr.models import (
    NMRAveragedAtomResult,
    NMRAtomShielding,
    NMRAtomShift,
    NMRConformerResult,
    NMRReport,
)
from acp.nmr.parser import parse_gaussian_nmr_log, parse_nmr_output

logger = logging.getLogger(__name__)

__all__ = [
    "NMRAtomShielding",
    "NMRAtomShift",
    "NMRConformerResult",
    "NMRAveragedAtomResult",
    "NMRReport",
    "parse_gaussian_nmr_log",
    "parse_nmr_output",
    "select_conformers",
    "assign_nucleus",
    "calibrate_shifts",
    "average_atom_results",
]
