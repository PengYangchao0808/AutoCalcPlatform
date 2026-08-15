"""Pure algorithm primitives for ACP mechanism S2 internalization."""

from __future__ import annotations

from .energy_refinement import ScanEnergyRefiner, SinglePointSpec
from .geometry_guard import (
    RiskyContactResult,
    TopologyGuardResult,
    check_scan_trajectory,
    compare_graph_topology,
    compute_min_nonbonded_distance,
    detect_risky_contacts,
    generate_keepaway_constraints,
)
from .path_profile import (
    HARTREE_TO_KCAL,
    PathFrame,
    PathFrameEvidence,
    PathProfile,
    assess_path_topology,
    build_orca_scan_profile,
    build_xtb_path_profile,
    compute_forming_bond_distances_by_frame,
    compute_neighbor_rmsds,
    compute_path_arclength,
    scaffold_rmsd_admission,
)
from .path_selector import (
    SeedSelection,
    SelectionPolicy,
    policy_from_config,
    replay_rescue_selection,
    select_path_seeds,
)
from .scan_rescue import (
    B97CRelaxedScanRescuer,
    SurfaceScanCoordinate,
    SurfaceScanResult,
    SurfaceScanSpec,
)
from .scan_trajectory import CompositeProfileBuilder, ScanAttempt, attempt_manifest
from .torsion_dedup import (
    DedupRecord,
    TorsionAwareDeduplicator,
    TorsionSignature,
    build_signature,
    signatures_equivalent,
)

__all__ = [
    "B97CRelaxedScanRescuer",
    "CompositeProfileBuilder",
    "DedupRecord",
    "HARTREE_TO_KCAL",
    "PathFrame",
    "PathFrameEvidence",
    "PathProfile",
    "RiskyContactResult",
    "ScanAttempt",
    "TorsionAwareDeduplicator",
    "TorsionSignature",
    "ScanEnergyRefiner",
    "SeedSelection",
    "SelectionPolicy",
    "SinglePointSpec",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
    "TopologyGuardResult",
    "assess_path_topology",
    "attempt_manifest",
    "build_signature",
    "signatures_equivalent",
    "build_orca_scan_profile",
    "build_xtb_path_profile",
    "check_scan_trajectory",
    "compare_graph_topology",
    "compute_forming_bond_distances_by_frame",
    "compute_min_nonbonded_distance",
    "compute_neighbor_rmsds",
    "compute_path_arclength",
    "detect_risky_contacts",
    "generate_keepaway_constraints",
    "policy_from_config",
    "replay_rescue_selection",
    "scaffold_rmsd_admission",
    "select_path_seeds",
]
