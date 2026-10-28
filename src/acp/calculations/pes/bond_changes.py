"""ACP compatibility shim for PES bond-change classification (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.bond_changes` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.bond_changes import ...`` imports and attribute access
keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.bond_changes import (
    BondChange,
    _bond_changes_from_graphs,  # noqa: F401  (prior module attribute, cross-module import)
    bond_changes_from_dicts,
    bond_changes_to_dicts,
    compute_bond_changes,
    manual_bond_changes_to_records,
    suggest_coordinate_plan,
)

__all__ = [
    "BondChange",
    "bond_changes_from_dicts",
    "bond_changes_to_dicts",
    "compute_bond_changes",
    "manual_bond_changes_to_records",
    "suggest_coordinate_plan",
]
