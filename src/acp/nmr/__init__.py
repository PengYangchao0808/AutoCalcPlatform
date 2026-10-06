# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false
"""NMR + DP4/DP5 stereochemistry-assignment workflow.

Stage-based implementation of the Goodman DP4/DP5 method on top of the
ACP conformer-search + ORCA GIAO pipeline (DevDoc
``docs/ACP_NMR_DP4_DevDoc.md``). Reuses ``acp run ensemble`` (censo-light)
for conformer generation and adds GIAO NMR + Bayesian probability on top.
"""

from __future__ import annotations

import logging

from acp.nmr.assignment import (
    collect_residual_inputs,
    match_assigned,
    match_unassigned,
)
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.enumerate import (
    EnumeratedCandidate,
    EnumerateOptions,
    enumerate_candidates,
    enumerate_to_smiles,
)
from acp.nmr.equivalence import (
    build_all_labels,
    build_label_for_atom,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)
from acp.nmr.error_model import (
    ErrorModel,
    GoodmanDP5Model,
    GoodmanErrorModel,
    PlaceholderStudentTErrorModel,
    dp5_fchl_available,
    dp5_model_available,
    load_dp5_model,
    load_error_model,
    validate_error_model_binding,
)
from acp.nmr.fchl import (
    build_atom_representations,
    fchl_assets_available,
    fchl_kernel_active,
    generate_fchl_representation,
    get_atomic_kernels_numpy,
    kernel_backend,
    load_atomic_reps,
    qml_kernel_available,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import (
    DIGITAL_FILTER_COMPENSATION_GROUP_DELAY,
    DIGITAL_FILTER_COMPENSATION_NONE,
    DIGITAL_FILTER_STATUSES,
    PROBABILITY_STATUSES,
    PROCESSING_REASONS,
    PROCESSING_STATUSES,
    SIGNAL_GROUP_BASES,
    AcquisitionSpectrum,
    Assignment,
    AtomShift,
    CandidateEvidence,
    CandidateProbability,
    CandidateResult,
    ConformerShielding,
    DigitalFilterCheck,
    EvidenceStatus,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    NucleusEvidence,
    ProbabilityResult,
    ProbabilityStatus,
    ProcessedSpectrum,
    ProcessingAssessment,
    ProcessingProvenance,
    ProcessingQuality,
    RegressionResult,
    ResonanceSignal,
    SignalGroup,
    SpectralLine,
    assess_processing,
    check_digital_filter,
    lookup_tms_shieldings,
    resonance_signals_from_peaks,
)
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    compute_dp5_goodman,
    dp5_log_to_probability,
    normalize_dp4,
    normalize_dp4_gated,
)
from acp.nmr.reference_validation import (
    ASSET_FILES,
    PINNED_GOODMAN_UPSTREAM,
    ComparisonRow,
    PinnedSetting,
    PinnedUpstreamSettings,
    ReferenceAvailability,
    ReferenceComparison,
    ReferenceDataset,
    ReferenceRecord,
    apply_reference_validation,
    assess_reference_dataset,
    asset_hashes,
    attach_reference_segment,
    compare_reference_vs_migration,
    pinned_goodman_upstream,
)
from acp.nmr.scaling import build_assignments, fit_regression, fit_scaling_goodman
from acp.nmr.shift_predictor import (
    CALIBRATION_STATUSES,
    CalibrationStatus,
    DuplicateShiftPredictorError,
    GeometryRequirements,
    InvalidShiftPredictorError,
    PredictedShift,
    PredictorGeometry,
    ShiftDistribution,
    ShiftModelProvenance,
    ShiftPrediction,
    ShiftPredictionRequest,
    ShiftPredictor,
    ShiftPredictorError,
    UnknownShiftPredictorError,
    get_shift_predictor,
    list_shift_predictors,
    register_shift_predictor,
    unregister_shift_predictor,
)
from acp.nmr.spectra import (
    BrukerProcessResult,
    bruker_result_to_text,
    find_bruker_experiments,
    process_bruker_experiment,
    process_bruker_tree,
)
from acp.nmr.structure_map import (
    LABEL_SCHEME_GOODMAN,
    LABEL_SCHEME_PER_ELEMENT,
    LABEL_SCHEMES,
    AtomIdentity,
    NmrStructureMap,
    StructureMapError,
    parse_ambiguous_labels,
)

logger = logging.getLogger(__name__)

__all__ = [
    # models
    "Assignment",
    "AtomShift",
    "CandidateEvidence",
    "CandidateProbability",
    "CandidateResult",
    "ConformerShielding",
    "EvidenceStatus",
    "ExperimentalNmr",
    "ExperimentalPeak",
    "NmrConfig",
    "NmrReport",
    "NucleusEvidence",
    "ProbabilityResult",
    "ProbabilityStatus",
    "PROBABILITY_STATUSES",
    "RegressionResult",
    "SignalGroup",
    "SIGNAL_GROUP_BASES",
    "lookup_tms_shieldings",
    # four-layer spectrum model (todo 41 / G10)
    "AcquisitionSpectrum",
    "ProcessedSpectrum",
    "ProcessingProvenance",
    "ProcessingQuality",
    "SpectralLine",
    "ResonanceSignal",
    "resonance_signals_from_peaks",
    # processing quality gates (todo 42 / G10)
    "ProcessingAssessment",
    "PROCESSING_STATUSES",
    "PROCESSING_REASONS",
    "DigitalFilterCheck",
    "DIGITAL_FILTER_STATUSES",
    "DIGITAL_FILTER_COMPENSATION_NONE",
    "DIGITAL_FILTER_COMPENSATION_GROUP_DELAY",
    "assess_processing",
    "check_digital_filter",
    # io
    "parse_experimental_nmr",
    # equivalence
    "build_all_labels",
    "build_label_for_atom",
    "detect_equivalence_groups",
    "merge_explicit_and_detected",
    # averaging
    "boltzmann_average_shieldings",
    # enumerate (P2)
    "EnumerateOptions",
    "EnumeratedCandidate",
    "enumerate_candidates",
    "enumerate_to_smiles",
    # assignment
    "match_assigned",
    "match_unassigned",
    "collect_residual_inputs",
    # scaling
    "fit_regression",
    "fit_scaling_goodman",
    "build_assignments",
    # probability
    "compute_dp4",
    "normalize_dp4",
    "normalize_dp4_gated",
    "compute_dp5",
    "compute_dp5_goodman",
    "dp5_log_to_probability",
    # reference validation (todo 31)
    "ASSET_FILES",
    "PINNED_GOODMAN_UPSTREAM",
    "ComparisonRow",
    "PinnedSetting",
    "PinnedUpstreamSettings",
    "ReferenceAvailability",
    "ReferenceComparison",
    "ReferenceDataset",
    "ReferenceRecord",
    "apply_reference_validation",
    "asset_hashes",
    "assess_reference_dataset",
    "attach_reference_segment",
    "compare_reference_vs_migration",
    "pinned_goodman_upstream",
    # error model
    "ErrorModel",
    "GoodmanErrorModel",
    "GoodmanDP5Model",
    "PlaceholderStudentTErrorModel",
    "load_error_model",
    "load_dp5_model",
    "dp5_model_available",
    "dp5_fchl_available",
    "validate_error_model_binding",
    # FCHL (P4, DevDoc appendix D)
    "build_atom_representations",
    "fchl_assets_available",
    "fchl_kernel_active",
    "generate_fchl_representation",
    "get_atomic_kernels_numpy",
    "kernel_backend",
    "load_atomic_reps",
    "qml_kernel_available",
    # spectra (P3)
    "BrukerProcessResult",
    "ProcessedSpectrum",
    "bruker_result_to_text",
    "find_bruker_experiments",
    "process_bruker_experiment",
    "process_bruker_tree",
    # structure map (stable atom identity + label schemes)
    "AtomIdentity",
    "NmrStructureMap",
    "StructureMapError",
    "parse_ambiguous_labels",
    "LABEL_SCHEME_PER_ELEMENT",
    "LABEL_SCHEME_GOODMAN",
    "LABEL_SCHEMES",
    # shift predictor (todo 55 / gap G17): isolated research route — never a
    # replacement for the GIAO/DP4/DP5 chain and never selected by default.
    "ShiftPredictor",
    "ShiftModelProvenance",
    "GeometryRequirements",
    "ShiftDistribution",
    "PredictedShift",
    "ShiftPrediction",
    "ShiftPredictionRequest",
    "PredictorGeometry",
    "CalibrationStatus",
    "CALIBRATION_STATUSES",
    "ShiftPredictorError",
    "InvalidShiftPredictorError",
    "DuplicateShiftPredictorError",
    "UnknownShiftPredictorError",
    "register_shift_predictor",
    "get_shift_predictor",
    "list_shift_predictors",
    "unregister_shift_predictor",
]
