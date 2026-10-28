"""ACP compatibility shim for PES cross-state atom mapping (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.atom_mapping` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.atom_mapping import ...`` imports and attribute
access keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.atom_mapping import (
    AtomIdentityMap,
    AtomMapCandidate,
    MappingResult,
    _build_mol_graph,  # noqa: F401  (prior module attribute, cross-module import)
    _normalize_coordinates,  # noqa: F401  (prior module attribute, cross-module import)
    _normalize_symbols,  # noqa: F401  (prior module attribute, cross-module import)
    map_reactant_to_product,
    to_atom_identity_map,
)

__all__ = [
    "AtomIdentityMap",
    "AtomMapCandidate",
    "MappingResult",
    "map_reactant_to_product",
    "to_atom_identity_map",
]
