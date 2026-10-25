"""Compatibility shim — NMR ``shift_predictor`` science now lives in
``cccp.analysis.nmr.shift_predictor`` (plan C05-s5 / T29).

Re-exports the same objects so existing ``acp.nmr.shift_predictor`` imports,
``isinstance``/Protocol checks and package-level identity keep working. The
isolated research seam stays isolated: this module is never imported by the
default NMR/DP4/DP5 chain and registers nothing on import."""

from __future__ import annotations

from cccp.analysis.nmr.shift_predictor import (
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

__all__ = [
    "CALIBRATION_STATUSES",
    "CalibrationStatus",
    "DuplicateShiftPredictorError",
    "GeometryRequirements",
    "InvalidShiftPredictorError",
    "PredictedShift",
    "PredictorGeometry",
    "ShiftDistribution",
    "ShiftModelProvenance",
    "ShiftPrediction",
    "ShiftPredictionRequest",
    "ShiftPredictor",
    "ShiftPredictorError",
    "UnknownShiftPredictorError",
    "get_shift_predictor",
    "list_shift_predictors",
    "register_shift_predictor",
    "unregister_shift_predictor",
]
