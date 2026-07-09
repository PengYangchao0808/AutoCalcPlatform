"""
Cluster Adapters
================

Adapters for different execution environments (local, LSF, Slurm, etc.).

Author: QCcalc Team
"""

import logging
from typing import Dict, Any

from conformer_search.qc.cluster.base import ClusterAdapterBase, JobStatus
from conformer_search.qc.cluster.local import LocalClusterAdapter
from conformer_search.qc.cluster.lsf import LSFClusterAdapter

logger = logging.getLogger(__name__)


def create_cluster_adapter(config: Dict[str, Any]) -> ClusterAdapterBase:
    """
    Factory function to create appropriate cluster adapter.

    Args:
        config: Configuration dictionary

    Returns:
        ClusterAdapterBase instance
    """
    cluster_enabled = config.get('cluster', {}).get('enabled', False)
    cluster_type = config.get('cluster', {}).get('type', 'local')
    
    if not cluster_enabled:
        return LocalClusterAdapter(config)
    
    if cluster_type == 'lsf':
        return LSFClusterAdapter(config)
    elif cluster_type == 'local':
        return LocalClusterAdapter(config)
    else:
        logger.warning(f"Unknown cluster type '{cluster_type}', using local")
        return LocalClusterAdapter(config)


__all__ = [
    "ClusterAdapterBase",
    "JobStatus",
    "LocalClusterAdapter",
    "LSFClusterAdapter",
    "create_cluster_adapter",
]
