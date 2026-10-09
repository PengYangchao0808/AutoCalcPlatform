"""Quantum chemistry backend abstraction layer.

Compat re-export (plan todo 12): implementations live in :mod:`cccp.backends`;
this package preserves the historical ``acp.backends`` import surface with
``A is B`` identity.  ``batch`` is the quarantined legacy backend-direct
batch entry (sanctioned legacy surface, not a production path — see the
``legacy_batch_quarantine`` gate).
"""

from __future__ import annotations

from cccp.backends import (
    BackendCapabilityStatus,
    BackendRegistry,
    CAPABILITY_MATRIX,
    CensoBackend,
    ClusteringTool,
    ConformerSearcher,
    CrestBackend,
    ExternalBackend,
    FrequencyCalculator,
    GeometryOptimizer,
    IsostatBackend,
    MolclusBackend,
    ORCABackend,
    QCBackend,
    QCResult,
    RelaxedScanCalculator,
    SinglePointCalculator,
    TSMechanismCalculator,
    ThermoCalculator,
    XTBBackend,
    backend_status,
    batch_process_thermo,
    get_backend,
    list_backends,
    list_capabilities,
    register_backend,
    require_backend,
    supports,
)

from .batch import BatchSpFrameResult, BatchSpResult, batch_single_point

__all__ = [
    "BackendRegistry",
    "QCBackend",
    "QCResult",
    "BatchSpFrameResult",
    "BatchSpResult",
    "GeometryOptimizer",
    "SinglePointCalculator",
    "FrequencyCalculator",
    "RelaxedScanCalculator",
    "ConformerSearcher",
    "ClusteringTool",
    "ThermoCalculator",
    "TSMechanismCalculator",
    "BackendCapabilityStatus",
    "CAPABILITY_MATRIX",
    "supports",
    "list_capabilities",
    "list_backends",
    "backend_status",
    "CensoBackend",
    "ORCABackend",
    "CrestBackend",
    "XTBBackend",
    "ExternalBackend",
    "MolclusBackend",
    "IsostatBackend",
    "batch_process_thermo",
    "batch_single_point",
    "register_backend",
    "get_backend",
    "require_backend",
]
