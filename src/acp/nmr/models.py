"""Compatibility shim — NMR ``models`` science now lives in
``cccp.analysis.nmr.models`` (plan C05-s1 / T25).

Re-exports the same objects so existing ``acp.nmr.models`` imports,
``isinstance`` checks and dataclass identity keep working. DP5q/ShiftPredictor
isolation is untouched."""

from __future__ import annotations

from cccp.analysis.nmr.models import (
    _TMS_TABLE as _TMS_TABLE,
)
from cccp.analysis.nmr.models import (
    CONFORMER_EXCLUSION_REASONS,
    DIGITAL_FILTER_COMPENSATION_GROUP_DELAY,
    DIGITAL_FILTER_COMPENSATION_NONE,
    DIGITAL_FILTER_COMPENSATIONS,
    DIGITAL_FILTER_STATUSES,
    ENERGY_DEFINITIONS,
    ENSEMBLE_QUALITY_FLAGS,
    ENSEMBLE_QUALITY_STATUSES,
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    PARSE_ISSUE_CODES,
    PROBABILITY_STATUSES,
    PROCESSING_REASONS,
    PROCESSING_STATUSES,
    REPORT_SCHEMA_VERSION,
    SIGNAL_GROUP_BASES,
    AcquisitionSpectrum,
    Assignment,
    AtomShift,
    CandidateProbability,
    CandidateResult,
    ConformerPopulation,
    ConformerShielding,
    DigitalFilterCheck,
    EnsembleQuality,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    ParseIssue,
    ParseIssueCode,
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
    element_of_nucleus,
    normalize_symbol,
    nucleus_label,
    resonance_signals_from_peaks,
)
from cccp.analysis.nmr.models import (
    CandidateEvidence as CandidateEvidence,
)
from cccp.analysis.nmr.models import (
    EvidenceStatus as EvidenceStatus,
)
from cccp.analysis.nmr.models import (
    NucleusEvidence as NucleusEvidence,
)
from cccp.analysis.nmr.models import (
    _dp4_rank_key as _dp4_rank_key,
)
from cccp.analysis.nmr.models import (
    _load_tms_table as _load_tms_table,
)
from cccp.analysis.nmr.models import (
    lookup_tms_shieldings as lookup_tms_shieldings,
)

__all__ = [
    "AcquisitionSpectrum",
    "Assignment",
    "AtomShift",
    "CONFORMER_EXCLUSION_REASONS",
    "CandidateProbability",
    "CandidateResult",
    "ConformerPopulation",
    "ConformerShielding",
    "DIGITAL_FILTER_COMPENSATIONS",
    "DIGITAL_FILTER_COMPENSATION_GROUP_DELAY",
    "DIGITAL_FILTER_COMPENSATION_NONE",
    "DIGITAL_FILTER_STATUSES",
    "DigitalFilterCheck",
    "ENERGY_DEFINITIONS",
    "ENSEMBLE_QUALITY_FLAGS",
    "ENSEMBLE_QUALITY_STATUSES",
    "EQ_BASIS_EXPLICIT",
    "EQ_BASIS_TOPOLOGY",
    "EQ_BASIS_UNKNOWN",
    "EnsembleQuality",
    "ExperimentalNmr",
    "ExperimentalPeak",
    "NmrConfig",
    "NmrReport",
    "PARSE_ISSUE_CODES",
    "PROBABILITY_STATUSES",
    "PROCESSING_REASONS",
    "PROCESSING_STATUSES",
    "ParseIssue",
    "ParseIssueCode",
    "ProbabilityResult",
    "ProbabilityStatus",
    "ProcessedSpectrum",
    "ProcessingAssessment",
    "ProcessingProvenance",
    "ProcessingQuality",
    "REPORT_SCHEMA_VERSION",
    "RegressionResult",
    "ResonanceSignal",
    "SIGNAL_GROUP_BASES",
    "SignalGroup",
    "SpectralLine",
    "assess_processing",
    "check_digital_filter",
    "element_of_nucleus",
    "normalize_symbol",
    "nucleus_label",
    "resonance_signals_from_peaks",
]
