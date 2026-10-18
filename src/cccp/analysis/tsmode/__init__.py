"""CCCP TS Mode science — frequency-source analysis and mode mapping.

Pure, platform-agnostic implementation of the TS Mode directed-optimization
science (plan §4 source bundles, §6 three mode identifiers / launch gate,
§12 saddle-point validation).  Relocated from
``acp.calculations.tsmode`` (T23, C04-s4); the ACP workflow/engine layer
keeps orchestration + path policy and re-exports these same objects.

This package never imports ``acp``.
"""

from __future__ import annotations

from .contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    MODE_MAPPING_AMBIGUOUS,
    MODE_MAPPING_UNSUPPORTED,
    SOURCE_FETCH_PENDING,
    SOURCE_GEOMETRY_MISMATCH,
    SOURCE_REVISION_CONFLICT,
    TARGET_MODE_INVALID,
    TARGET_MODE_MISMATCH,
    FrequencySourceBundle,
    MappingStatus,
    SourceLevelOfTheory,
    SourceModeRecord,
    TargetResolution,
    TsmodeError,
    compute_target_mode_id,
    geometry_hash,
    optimized_structure_digest,
    sha256_file,
    validate_coordinate_array,
    validate_mode_completeness,
)
from .mode_mapping import (
    MAPPING_METHOD,
    MAPPING_VERSION,
    TS_MODE_MAPPING_VERIFIED_VERSIONS,
    enforce_launch_gate,
    overlap_evidence,
    resolve_target_mode,
)
from .source import (
    HessianModes,
    build_external_subspace,
    compute_hessian_modes,
    kabsch_rmsd,
    load_bundle_from_files,
    mass_weight_and_project,
)
from .validation import (
    TsValidation,
    compare_mode_correspondence,
    validate_ts_frequencies,
)

__all__ = [
    "FREQUENCY_SOURCE_INCOMPLETE",
    "HESSIAN_MISSING",
    "MAPPING_METHOD",
    "MAPPING_VERSION",
    "MODE_MAPPING_AMBIGUOUS",
    "MODE_MAPPING_UNSUPPORTED",
    "SOURCE_FETCH_PENDING",
    "SOURCE_GEOMETRY_MISMATCH",
    "SOURCE_REVISION_CONFLICT",
    "TARGET_MODE_INVALID",
    "TARGET_MODE_MISMATCH",
    "TS_MODE_MAPPING_VERIFIED_VERSIONS",
    "FrequencySourceBundle",
    "HessianModes",
    "MappingStatus",
    "SourceLevelOfTheory",
    "SourceModeRecord",
    "TargetResolution",
    "TsValidation",
    "TsmodeError",
    "build_external_subspace",
    "compare_mode_correspondence",
    "compute_hessian_modes",
    "compute_target_mode_id",
    "enforce_launch_gate",
    "geometry_hash",
    "kabsch_rmsd",
    "load_bundle_from_files",
    "mass_weight_and_project",
    "optimized_structure_digest",
    "overlap_evidence",
    "resolve_target_mode",
    "sha256_file",
    "validate_coordinate_array",
    "validate_mode_completeness",
    "validate_ts_frequencies",
]
