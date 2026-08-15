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
    StableState,
    StableStateNode,
    StationaryPoint,
    StationaryPointRequest,
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
    EndpointMatchResult,
    EndpointProvider,
    EnsembleProvider,
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
    IrcResult,
    PathSearchStrategy,
    RefinementAttempt,
    RefinementManifest,
    RefinementProvider,
    StableStateEnsemble,
    ThermochemistryProvider,
    ThermochemistryResult,
)
from .rescue import (
    RescueAction,
    RescuePlan,
    apply_rescue_kwargs,
    build_rescue_plan,
)
from .strategies import (
    resolve_path_strategy,
    run_direct_ts,
    run_guided_scan,
    run_rph_reverse,
)

__all__ = [
    "ArtifactRef",
    "AtomIdentityMap",
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
    "MechanismInput",
    "MechanismRoute",
    "MechanismStudy",
    "PATH_STRATEGIES",
    "PathCandidate",
    "PathPoint",
    "PathResult",
    "PathSearchStrategy",
    "PathStrategy",
    "PathStrategySpec",
    "Provenance",
    "QualityGateResult",
    "ReactionNetwork",
    "RefinementAttempt",
    "RefinementManifest",
    "RefinementProvider",
    "RescueAction",
    "RescuePlan",
    "SeedCandidate",
    "StableState",
    "StableStateEnsemble",
    "StableStateNode",
    "StationaryPoint",
    "StationaryPointRequest",
    "StudyOrchestrator",
    "ThermoCorrection",
    "ThermochemistryProvider",
    "ThermochemistryResult",
    "TsIdentity",
    "TsValidation",
    "apply_rescue_kwargs",
    "build_rescue_plan",
    "classify_ts_identity",
    "compute_mode_match_score",
    "resolve_fidelity",
    "resolve_fidelity_profile",
    "resolve_path_strategy",
    "resolve_strategy",
    "run_direct_ts",
    "run_guided_scan",
    "run_rph_reverse",
    "run_gates",
    "select_candidates",
    "select_primary_int",
    "select_primary_ts",
    "validate_path_candidate",
]
