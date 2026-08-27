# pyright: reportMissingImports=false
"""Mechanism research module: multi-submodule reaction-path pipeline.

Submodules:

* ``models`` — route / path / candidate / TS-identity data models;
* ``presets`` — orthogonal path-strategy × fidelity preset axes;
* ``strategies`` — guided-scan / rph-reverse / direct-ts path search;
* ``candidates`` — TS / intermediate / endpoint seed selection;
* ``identity`` — imaginary-mode + reaction-coordinate-overlap TS validation;
* ``rescue`` — declarative (failure_type × structure_kind) rescue matrix.

The generic internal-coordinate primitives live in
:mod:`cccp.qc.interfaces.constraints` (distance / angle / dihedral).
"""

from __future__ import annotations

from acp.calculations.irc.contracts import (
    EndpointMatchResult,
    EndpointProvider,
    IrcResult,
)

from .atom_mapping import (
    AtomMapCandidate,
    MappingResult,
    map_reactant_to_product,
    to_atom_identity_map,
)
from .bond_changes import (
    BondChange,
    bond_changes_from_dicts,
    bond_changes_to_dicts,
    compute_bond_changes,
    suggest_mechanism_plan,
)
from .candidates import select_candidates, select_primary_int, select_primary_ts
from .gates import GATE_IDS, GateContext, run_gates
from .identity import (
    classify_ts_identity,
    compute_mode_match_score,
    validate_path_candidate,
)
from .models import (
    ArtifactRef,
    AtomIdentityMap,
    DecisionPoint,
    ElementaryStepEdge,
    ExplorationFrontier,
    Fidelity,
    MechanismInput,
    MechanismRevision,
    MechanismRoute,
    MechanismStudy,
    PathCandidate,
    PathPoint,
    PathResult,
    PathStrategy,
    Provenance,
    QualityGateResult,
    ReactionNetwork,
    SeedCandidate,
    SelectedBond,
    StableState,
    StableStateNode,
    StationaryPoint,
    StationaryPointRequest,
    StudyCycle,
    ThermoCorrection,
    TsIdentity,
    TsValidation,
)
from .orchestrator import StudyOrchestrator
from .presets import (
    FIDELITY_PROFILES,
    PATH_STRATEGIES,
    FidelityProfile,
    PathStrategySpec,
    resolve_fidelity,
    resolve_fidelity_profile,
    resolve_strategy,
)
from .providers import (
    EnsembleProvider,
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
    PathSearchStrategy,
    RefinementAttempt,
    RefinementManifest,
    RefinementProvider,
    StableStateEnsemble,
    ThermochemistryProvider,
    ThermochemistryResult,
)
from .reaction_definition import (
    MECHANISM_SCHEMA_VERSION,
    AtomMapPair,
    MappingConfirmationRequired,
    ReactionDefinition,
    RoleSpec,
    build_reaction_definition,
    compute_content_hash,
    read_reaction_json,
    validate_reaction_json,
    write_reaction_json,
)
from .rescue import (
    RescueAction,
    RescuePlan,
    apply_rescue_kwargs,
    build_rescue_plan,
)
from .strategies import NativeReversePebStrategy, run_rph_reverse

__all__ = [
    "ArtifactRef",
    "AtomMapCandidate",
    "AtomMapPair",
    "AtomIdentityMap",
    "BondChange",
    "DecisionPoint",
    "ElementaryStepEdge",
    "EndpointMatchResult",
    "EndpointProvider",
    "EnsembleProvider",
    "ExplorationFrontier",
    "FIDELITY_PROFILES",
    "Fidelity",
    "FidelityProfile",
    "FakeEnsembleProvider",
    "FakeEndpointProvider",
    "FakePathSearchStrategy",
    "FakeRefinementProvider",
    "GATE_IDS",
    "GateContext",
    "IrcResult",
    "MECHANISM_SCHEMA_VERSION",
    "MechanismInput",
    "MechanismRevision",
    "MechanismRoute",
    "MechanismStudy",
    "MappingConfirmationRequired",
    "MappingResult",
    "NativeReversePebStrategy",
    "PATH_STRATEGIES",
    "PathCandidate",
    "PathPoint",
    "PathResult",
    "PathSearchStrategy",
    "PathStrategy",
    "PathStrategySpec",
    "Provenance",
    "QualityGateResult",
    "ReactionDefinition",
    "ReactionNetwork",
    "RefinementAttempt",
    "RefinementManifest",
    "RefinementProvider",
    "RescueAction",
    "RescuePlan",
    "RoleSpec",
    "SelectedBond",
    "SeedCandidate",
    "StableState",
    "StableStateEnsemble",
    "StableStateNode",
    "StationaryPoint",
    "StationaryPointRequest",
    "StudyOrchestrator",
    "StudyCycle",
    "ThermoCorrection",
    "ThermochemistryProvider",
    "ThermochemistryResult",
    "TsIdentity",
    "TsValidation",
    "apply_rescue_kwargs",
    "bond_changes_from_dicts",
    "bond_changes_to_dicts",
    "build_reaction_definition",
    "build_rescue_plan",
    "classify_ts_identity",
    "compute_bond_changes",
    "compute_content_hash",
    "compute_mode_match_score",
    "map_reactant_to_product",
    "read_reaction_json",
    "resolve_fidelity",
    "resolve_fidelity_profile",
    "resolve_strategy",
    "run_rph_reverse",
    "run_gates",
    "select_candidates",
    "select_primary_int",
    "select_primary_ts",
    "suggest_mechanism_plan",
    "to_atom_identity_map",
    "validate_reaction_json",
    "validate_path_candidate",
    "write_reaction_json",
]
