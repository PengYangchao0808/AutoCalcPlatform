"""Compatibility shim — NMR ``enumerate`` science now lives in
``cccp.analysis.nmr.enumerate`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.enumerate`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.enumerate import (
    EnumeratedCandidate,
    EnumerateOptions,
    enumerate_candidates,
    enumerate_to_smiles,
)
from cccp.analysis.nmr.enumerate import (
    _parse_stereocenter_arg as _parse_stereocenter_arg,
)
from cccp.analysis.nmr.enumerate import (
    _resolve_stereocenter_labels as _resolve_stereocenter_labels,
)

__all__ = [
    "EnumerateOptions",
    "EnumeratedCandidate",
    "enumerate_candidates",
    "enumerate_to_smiles",
]
