"""QC backend abstraction — compat shim for :mod:`cccp.backends.base`."""

from __future__ import annotations

from cccp.backends.base import (
    BackendUnavailableError,
    CASSCFCalculator,
    ClusteringTool,
    ConformerSearcher,
    ConstrainedOptimizer,
    FrequencyCalculator,
    GeometryOptimizer,
    MrrhoThermoCalculator,
    NmrShieldingCalculator,
    QCBackend,
    QCResult,
    RelaxedScanCalculator,
    SinglePointCalculator,
    TSMechanismCalculator,
    ThermoCalculator,
    to_qc_result,
)

__all__ = ['BackendUnavailableError', 'CASSCFCalculator', 'ClusteringTool', 'ConformerSearcher', 'ConstrainedOptimizer', 'FrequencyCalculator', 'GeometryOptimizer', 'MrrhoThermoCalculator', 'NmrShieldingCalculator', 'QCBackend', 'QCResult', 'RelaxedScanCalculator', 'SinglePointCalculator', 'TSMechanismCalculator', 'ThermoCalculator', 'to_qc_result']
