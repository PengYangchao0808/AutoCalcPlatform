"""ACP compatibility shim for Confsearch MD sampling-history (plan todo 20).

Implementation moved to :mod:`cccp.analysis.sampling` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.confsearch.sampling import ...`` imports and
``acp.confsearch.sampling.<attr>`` access keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.sampling import (
    MDS_COORD_TYPE,
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    _compute_saturation,  # noqa: F401  (private re-export)
    assign_basins,
    compute_sampling_history,
    equilibration_cutoff,
    load_sampling_history,
    mds_2d,
    parse_traj_frames,
    read_traj_frame_xyz,
    write_sampling_history,
)
from cccp.analysis.sampling.deduplication import plain_rmsd  # noqa: F401  (prior attribute)

__all__ = [
    "BasinInfo",
    "MDS_COORD_TYPE",
    "SamplingHistory",
    "SamplingSaturation",
    "TrajFrame",
    "assign_basins",
    "compute_sampling_history",
    "equilibration_cutoff",
    "load_sampling_history",
    "mds_2d",
    "parse_traj_frames",
    "read_traj_frame_xyz",
    "write_sampling_history",
]
