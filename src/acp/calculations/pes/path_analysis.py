"""ACP compatibility shim for PES path-profile analysis (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.path_analysis` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.path_analysis import ...`` imports and attribute
access keep working unchanged (RT-SAME).

``HARTREE_TO_KCAL`` keeps its historical truncated value ``627.509`` verbatim;
the relocation must not change it (registered numeric baseline).
"""

from __future__ import annotations

from cccp.analysis.pes.path_analysis import (
    HARTREE_TO_KCAL,
    PathFrame,
    PathFrameEvidence,
    PathProfile,
    _kabsch_rmsd,  # noqa: F401  (prior module attribute)
    _load_xyz_records,  # noqa: F401  (prior module attribute)
    _normalized_progress,  # noqa: F401  (prior module attribute, cross-module import)
    assess_path_topology,
    build_orca_scan_profile,
    build_xtb_path_profile,
    compute_forming_bond_distances_by_frame,
    compute_neighbor_rmsds,
    compute_path_arclength,
    scaffold_rmsd_admission,
)

__all__ = [
    "HARTREE_TO_KCAL",
    "PathFrame",
    "PathFrameEvidence",
    "PathProfile",
    "assess_path_topology",
    "build_orca_scan_profile",
    "build_xtb_path_profile",
    "compute_forming_bond_distances_by_frame",
    "compute_neighbor_rmsds",
    "compute_path_arclength",
    "scaffold_rmsd_admission",
]
