"""CCCP PES pure-science analysis (path profiles, selection, candidates).

Relocated from ``acp`` by plan todo 22 (C04-s3):

* :mod:`.atom_mapping` — cross-state RDKit atom mapping (``atom_mapping.py``).
* :mod:`.bond_changes` — bond-change classification + coordinate plans
  (``bond_changes.py``).
* :mod:`.path_analysis` — path-profile construction: arclength, RMSD,
  derivatives, topology (``path_analysis.py``).
* :mod:`.path_selection` — endpoint/knee seed-selection policy
  (``path_selection.py``).
* :mod:`.candidates` — standalone path TS/INT/endpoint candidate selection
  (``candidates.py``).
* :mod:`.validation` — geometry/topology guards + risky-contact detection
  (``validation.py``).
* :mod:`.atom_selection` — functional atom-selection parsing
  (``atom_selection.py``).

No ``acp`` imports (``cccp`` must never import ``acp``).  The ACP compatibility
surface lives at ``acp.calculations.pes.<module>`` (re-export shims) and
resolves the same objects.

``path_analysis.HARTREE_TO_KCAL`` keeps its historical truncated value
``627.509`` verbatim (registered in the numeric baseline; migration must not
change it).  ``__init__`` carries re-exports only (no implementation) per repo
ANTI #5.
"""

from __future__ import annotations

from cccp.analysis.pes.atom_mapping import (
    AtomIdentityMap,
    AtomMapCandidate,
    MappingResult,
    map_reactant_to_product,
    to_atom_identity_map,
)
from cccp.analysis.pes.atom_selection import (
    FunctionalAtomSelection,
    SelectionKind,
    normalize_selection_kind,
    parse_functional_atom_selection,
)
from cccp.analysis.pes.bond_changes import (
    BondChange,
    bond_changes_from_dicts,
    bond_changes_to_dicts,
    compute_bond_changes,
    manual_bond_changes_to_records,
    suggest_coordinate_plan,
)
from cccp.analysis.pes.candidates import (
    PathCandidate,
    PathPoint,
    SearchResult,
    select_candidates,
    select_primary_int,
    select_primary_ts,
)
from cccp.analysis.pes.path_analysis import (
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
from cccp.analysis.pes.path_selection import (
    SeedSelection,
    SelectionPolicy,
    policy_from_config,
    replay_rescue_selection,
    select_path_seeds,
)
from cccp.analysis.pes.validation import (
    RiskyContactResult,
    SurfaceScanCoordinate,
    SurfaceScanResult,
    SurfaceScanSpec,
    TopologyGuardResult,
    check_scan_trajectory,
    compare_graph_topology,
    compute_min_nonbonded_distance,
    detect_risky_contacts,
    generate_keepaway_constraints,
)

__all__ = [
    "HARTREE_TO_KCAL",
    "AtomIdentityMap",
    "AtomMapCandidate",
    "BondChange",
    "FunctionalAtomSelection",
    "MappingResult",
    "PathCandidate",
    "PathFrame",
    "PathFrameEvidence",
    "PathPoint",
    "PathProfile",
    "RiskyContactResult",
    "SearchResult",
    "SeedSelection",
    "SelectionKind",
    "SelectionPolicy",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
    "TopologyGuardResult",
    "assess_path_topology",
    "bond_changes_from_dicts",
    "bond_changes_to_dicts",
    "build_orca_scan_profile",
    "build_xtb_path_profile",
    "check_scan_trajectory",
    "compare_graph_topology",
    "compute_bond_changes",
    "compute_forming_bond_distances_by_frame",
    "compute_min_nonbonded_distance",
    "compute_neighbor_rmsds",
    "compute_path_arclength",
    "detect_risky_contacts",
    "generate_keepaway_constraints",
    "manual_bond_changes_to_records",
    "map_reactant_to_product",
    "normalize_selection_kind",
    "parse_functional_atom_selection",
    "policy_from_config",
    "replay_rescue_selection",
    "scaffold_rmsd_admission",
    "select_candidates",
    "select_path_seeds",
    "select_primary_int",
    "select_primary_ts",
    "suggest_coordinate_plan",
    "to_atom_identity_map",
]
