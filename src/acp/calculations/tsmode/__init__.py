"""TS Mode directed transition-state optimization.

Design plan: docs/ACP_TSMode_Optimization_Implementation_Plan.md.

Re-exports only — implementation lives in the sibling modules
(:mod:`contracts`, :mod:`source`, :mod:`mode_mapping`, :mod:`validation`,
:mod:`engine`).
"""

from __future__ import annotations

from acp.calculations.tsmode.contracts import (
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
    TsmodeOptimizationSettings,
    TsmodeReport,
    TsmodeRequest,
    compute_target_mode_id,
    geometry_hash,
    sha256_file,
)
from acp.calculations.tsmode.engine import TsmodeEngine, TsmodeEngineResult
from acp.calculations.tsmode.mode_mapping import resolve_target_mode
from acp.calculations.tsmode.source import load_bundle_from_files
from acp.calculations.tsmode.validation import validate_ts_frequencies

__all__ = [
    "FREQUENCY_SOURCE_INCOMPLETE",
    "HESSIAN_MISSING",
    "MODE_MAPPING_AMBIGUOUS",
    "MODE_MAPPING_UNSUPPORTED",
    "SOURCE_FETCH_PENDING",
    "SOURCE_GEOMETRY_MISMATCH",
    "SOURCE_REVISION_CONFLICT",
    "TARGET_MODE_INVALID",
    "TARGET_MODE_MISMATCH",
    "FrequencySourceBundle",
    "MappingStatus",
    "SourceLevelOfTheory",
    "SourceModeRecord",
    "TargetResolution",
    "TsmodeEngine",
    "TsmodeEngineResult",
    "TsmodeError",
    "TsmodeOptimizationSettings",
    "TsmodeReport",
    "TsmodeRequest",
    "compute_target_mode_id",
    "geometry_hash",
    "load_bundle_from_files",
    "resolve_target_mode",
    "sha256_file",
    "validate_ts_frequencies",
]
