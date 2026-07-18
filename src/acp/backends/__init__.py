"""Quantum chemistry backend abstraction layer."""

from acp.backends.base import (
    ClusteringTool,
    ConformerSearcher,
    FrequencyCalculator,
    GeometryOptimizer,
    NMRCalculator,
    QCBackend,
    QCResult,
    SinglePointCalculator,
    ThermoCalculator,
    TSMechanismCalculator,
)
from acp.backends.capabilities import (
    CAPABILITY_MATRIX,
    BackendCapabilityStatus,
    backend_status,
    list_backends,
    list_capabilities,
    supports,
)
from acp.backends.censo_backend import CensoBackend
from acp.backends.crest import CrestBackend
from acp.backends.external import batch_process_thermo, run_isostat, run_shermo
from acp.backends.external_backend import ExternalBackend
from acp.backends.isostat_backend import IsostatBackend
from acp.backends.molclus_backend import MolclusBackend
from acp.backends.orca import ORCABackend
from acp.backends.registry import BackendRegistry, get_backend, register_backend, require_backend
from acp.backends.xtb import XTBBackend

__all__ = [
    "BackendRegistry",
    "QCBackend",
    "QCResult",
    "GeometryOptimizer",
    "SinglePointCalculator",
    "FrequencyCalculator",
    "NMRCalculator",
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
    "run_isostat",
    "run_shermo",
    "batch_process_thermo",
    "register_backend",
    "get_backend",
    "require_backend",
]
