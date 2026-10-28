"""Compatibility shim — NMR ``equivalence`` science now lives in
``cccp.analysis.nmr.equivalence`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.equivalence`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.equivalence import (
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    EquivalenceError,
    EquivalenceGroup,
    EquivalenceResult,
    build_all_labels,
    build_label_for_atom,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)
from cccp.analysis.nmr.equivalence import (
    _build_label_index as _build_label_index,
)

__all__ = [
    "EQ_BASIS_EXPLICIT",
    "EQ_BASIS_TOPOLOGY",
    "EQ_BASIS_UNKNOWN",
    "EquivalenceError",
    "EquivalenceGroup",
    "EquivalenceResult",
    "build_all_labels",
    "build_label_for_atom",
    "detect_equivalence_groups",
    "merge_explicit_and_detected",
]
