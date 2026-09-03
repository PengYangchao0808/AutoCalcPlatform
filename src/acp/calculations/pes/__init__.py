"""PES (Potential Energy Surface) — scan, path analysis, and candidate selection.

Submodules:
    contracts   — frozen dataclasses for scan requests/frames/profiles
    scan        — relaxed-scan pipeline
    validation  — geometry and topology validation
    path_analysis — path profile construction (energies, topology, RMSD)
    path_selection — seed-selection policy (TS/INT from energy profiles)
    candidates  — candidate selection (standalone dataclasses)
    engine      — PesSearchEngine orchestrator
    atom_mapping — cross-state atom mapping
    bond_changes — bond-change classification and coordinate-plan suggestion
"""

from acp.calculations.pes.atom_selection import (
    FunctionalAtomSelection,
    SelectionKind,
    normalize_selection_kind,
    parse_functional_atom_selection,
)
from acp.calculations.pes.candidates import (
    PathCandidate,
    PathPoint,
    SearchResult,
    select_candidates,
    select_primary_int,
    select_primary_ts,
)
from acp.calculations.pes.contracts import (
    CandidateRecommendation,
    EnergyProfile,
    PesScanRequest,
    ScanCoordinate,
    ScanFrame,
    ScanOptimizer,
    ScanProtocol,
    ScanQuality,
    SinglePointSpec,
    StructureSource,
    build_default_protocol,
    coordinate_step,
    validate_scan_coordinate,
    validate_scan_coordinates,
    validate_scan_protocol,
)
from acp.calculations.pes.engine import (
    PES_E_MANIFEST,
    ConfsearchManifestInput,
    PesSearchEngine,
    PesSearchError,
    PesSearchResult,
    load_confsearch_manifest,
    resolve_representative_conformer,
)
from acp.calculations.pes.path_analysis import (
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
from acp.calculations.pes.path_selection import (
    SeedSelection,
    SelectionPolicy,
    policy_from_config,
    replay_rescue_selection,
    select_path_seeds,
)
from acp.calculations.pes.review import (
    PES_REVIEW_RELATIVE_PATH,
    PES_REVIEW_SCHEMA,
    PesReviewError,
    RevisionConflictError,
    candidate_id_for,
    load_pes_review,
    normalize_role,
    save_pes_review,
)
from acp.calculations.pes.scan import (
    PES_SCAN_STAGES,
    SCAN_DIR_NAME,
    build_coordinate_plan,
    run_pes_scan,
)
from acp.calculations.pes.validation import (
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
    # contracts
    "CandidateRecommendation",
    "FunctionalAtomSelection",
    "ConfsearchManifestInput",
    "EnergyProfile",
    # path_analysis
    "HARTREE_TO_KCAL",
    # engine
    "PES_E_MANIFEST",
    "PES_REVIEW_RELATIVE_PATH",
    "PES_REVIEW_SCHEMA",
    "PES_SCAN_STAGES",
    "PathCandidate",
    "PathFrame",
    "PathFrameEvidence",
    "PathPoint",
    "PathProfile",
    "PesScanRequest",
    "PesSearchEngine",
    "PesSearchError",
    "PesReviewError",
    "PesSearchResult",
    "RevisionConflictError",
    "RiskyContactResult",
    "SCAN_DIR_NAME",
    "ScanCoordinate",
    "ScanFrame",
    "ScanOptimizer",
    "ScanProtocol",
    "ScanQuality",
    "SelectionKind",
    # path_selection
    "SeedSelection",
    "SelectionPolicy",
    "SearchResult",
    "SinglePointSpec",
    "StructureSource",
    "SurfaceScanCoordinate",
    "SurfaceScanResult",
    "SurfaceScanSpec",
    "TopologyGuardResult",
    "assess_path_topology",
    "build_coordinate_plan",
    "build_default_protocol",
    "build_orca_scan_profile",
    "build_xtb_path_profile",
    "candidate_id_for",
    "check_scan_trajectory",
    "compare_graph_topology",
    "compute_forming_bond_distances_by_frame",
    "compute_min_nonbonded_distance",
    "compute_neighbor_rmsds",
    "compute_path_arclength",
    "coordinate_step",
    "detect_risky_contacts",
    "generate_keepaway_constraints",
    "load_confsearch_manifest",
    "load_pes_review",
    "normalize_role",
    "normalize_selection_kind",
    "parse_functional_atom_selection",
    "policy_from_config",
    "replay_rescue_selection",
    "resolve_representative_conformer",
    "run_pes_scan",
    "save_pes_review",
    "scaffold_rmsd_admission",
    # candidates
    "select_candidates",
    "select_path_seeds",
    "select_primary_int",
    "select_primary_ts",
    "validate_scan_coordinate",
    "validate_scan_coordinates",
    "validate_scan_protocol",
]
