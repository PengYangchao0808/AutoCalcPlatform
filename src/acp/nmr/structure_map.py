"""Compatibility shim — NMR ``structure_map`` science now lives in
``cccp.analysis.nmr.structure_map`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.structure_map`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.structure_map import (
    LABEL_SCHEME_GOODMAN,
    LABEL_SCHEME_PER_ELEMENT,
    LABEL_SCHEMES,
    AtomIdentity,
    NmrStructureMap,
    StructureMapError,
    parse_ambiguous_labels,
)

__all__ = [
    "AtomIdentity",
    "LABEL_SCHEMES",
    "LABEL_SCHEME_GOODMAN",
    "LABEL_SCHEME_PER_ELEMENT",
    "NmrStructureMap",
    "StructureMapError",
    "parse_ambiguous_labels",
]
