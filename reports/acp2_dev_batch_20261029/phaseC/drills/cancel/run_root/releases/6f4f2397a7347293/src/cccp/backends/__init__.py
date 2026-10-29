"""Quantum chemistry backend capability layer (implementation home).

Moved from ``acp.backends`` (plan todo 12): capability protocols, the
declarative matrix, the backend registry and the concrete backends live
here.  ``acp.backends`` is a pure re-export shim over this package; the
legacy backend-direct batch entry ``acp.backends.batch`` deliberately stays
in ACP as a quarantined legacy surface (see ``legacy_batch_quarantine``).
"""

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
from cccp.backends.capabilities import (
    backend_status,
    list_backends,
    list_capabilities,
    supports,
)
from cccp.backends.censo_backend import CensoBackend
from cccp.backends.crest import CrestBackend
from cccp.backends.external import batch_process_thermo
from cccp.backends.external_backend import ExternalBackend
from cccp.backends.isostat_backend import IsostatBackend
from cccp.backends.matrix import (
    CAPABILITY_ALIASES,
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    normalize_capability_name,
)
from cccp.backends.molclus_backend import MolclusBackend
from cccp.backends.orca import ORCABackend
from cccp.backends.registry import (
    BackendRegistry,
    backend_registry,
    get_backend,
    register_backend,
    require_backend,
)
from cccp.backends.xtb import XTBBackend

__all__ = [
    "BackendRegistry",
    "QCBackend",
    "QCResult",
    "to_qc_result",
    "BackendUnavailableError",
    "GeometryOptimizer",
    "ConstrainedOptimizer",
    "SinglePointCalculator",
    "FrequencyCalculator",
    "RelaxedScanCalculator",
    "ConformerSearcher",
    "ClusteringTool",
    "ThermoCalculator",
    "MrrhoThermoCalculator",
    "TSMechanismCalculator",
    "CASSCFCalculator",
    "NmrShieldingCalculator",
    "BackendCapabilityStatus",
    "CAPABILITY_ALIASES",
    "CAPABILITY_MATRIX",
    "normalize_capability_name",
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
    "backend_registry",
    "register_backend",
    "get_backend",
    "require_backend",
]
