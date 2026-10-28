"""Compatibility shim — NMR ``averaging`` science now lives in
``cccp.analysis.nmr.averaging`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.averaging`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.averaging import (
    boltzmann_average_shieldings,
    incomplete_conformer_ids,
    labels_for_atoms,
)

__all__ = [
    "boltzmann_average_shieldings",
    "incomplete_conformer_ids",
    "labels_for_atoms",
]
