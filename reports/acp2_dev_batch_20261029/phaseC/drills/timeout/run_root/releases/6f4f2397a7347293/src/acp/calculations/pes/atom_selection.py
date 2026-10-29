"""ACP compatibility shim for PES functional atom-selection parsing (todo 22).

Implementation moved to :mod:`cccp.analysis.pes.atom_selection` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.atom_selection import ...`` imports and attribute
access keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.atom_selection import (
    FunctionalAtomSelection,
    SelectionKind,
    normalize_selection_kind,
    parse_functional_atom_selection,
)

__all__ = [
    "FunctionalAtomSelection",
    "SelectionKind",
    "normalize_selection_kind",
    "parse_functional_atom_selection",
]
