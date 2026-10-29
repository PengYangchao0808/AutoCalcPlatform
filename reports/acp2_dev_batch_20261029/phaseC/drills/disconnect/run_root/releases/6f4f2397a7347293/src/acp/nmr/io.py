"""Compatibility shim — NMR ``io`` science now lives in
``cccp.analysis.nmr.io`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.io`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.io import (
    parse_experimental_nmr,
)

__all__ = [
    "parse_experimental_nmr",
]
