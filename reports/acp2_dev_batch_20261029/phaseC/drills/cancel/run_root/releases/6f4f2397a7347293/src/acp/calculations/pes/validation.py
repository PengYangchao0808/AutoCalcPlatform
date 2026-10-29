"""ACP compatibility shim for PES geometry/topology validation (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.validation` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.validation import ...`` imports and attribute access
keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.validation import (
    RiskyContactResult,
    SurfaceScanCoordinate,
    SurfaceScanResult,
    SurfaceScanSpec,
    TopologyGuardResult,
    _canonicalize_bond_pair,  # noqa: F401  (prior module attribute)
    _canonicalize_bond_pairs,  # noqa: F401  (prior module attribute, cross-module import)
    check_scan_trajectory,
    compare_graph_topology,
    compute_min_nonbonded_distance,
    detect_risky_contacts,
    generate_keepaway_constraints,
)

__all__ = [
    "RiskyContactResult",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
    "TopologyGuardResult",
    "check_scan_trajectory",
    "compare_graph_topology",
    "compute_min_nonbonded_distance",
    "detect_risky_contacts",
    "generate_keepaway_constraints",
]
