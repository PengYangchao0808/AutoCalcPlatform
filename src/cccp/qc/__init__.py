"""
QC Package
==========

Quantum chemistry interfaces and runners.
"""

from cccp.qc.interfaces import (
    QCInterfaceBase,
    QCResult,
    ORCAInterface,
    CRESTInterface,
    XTBInterface,
    IsostatInterface,
)

from cccp.qc.runners import (
    run_isostat,  # DEPRECATED — prefer cccp.qc.interfaces.IsostatInterface (via acp.backends)
    run_shermo,
    batch_process_thermo,
)

from cccp.qc.cluster import (
    ClusterAdapterBase,
    LocalClusterAdapter,
    LSFClusterAdapter,
    create_cluster_adapter,
    JobStatus,
)

__all__ = [
    "QCInterfaceBase",
    "QCResult",
    "ORCAInterface",
    "CRESTInterface",
    "XTBInterface",
    "IsostatInterface",
    "run_isostat",  # DEPRECATED — IsostatInterface is the single ISOSTAT path
    "run_shermo",
    "batch_process_thermo",
    "ClusterAdapterBase",
    "LocalClusterAdapter",
    "LSFClusterAdapter",
    "create_cluster_adapter",
    "JobStatus",
]
