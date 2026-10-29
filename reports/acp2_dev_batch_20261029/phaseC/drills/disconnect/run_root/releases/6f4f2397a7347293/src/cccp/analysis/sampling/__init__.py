"""CCCP MD sampling-history analysis (Confsearch ``sampling_history_v1``).

Pure-algorithm home for the xTB-MD / xTB-MD+CENSO sampling science: the
trajectory parser, equilibration detection, greedy plain-RMSD basin
clustering, classical-MDS projection, sampling-saturation metrics, the
frozen data models + JSON serialization, and the geometric deduplication
helpers.  No ``acp`` imports (``cccp`` must never import ``acp``).  The ACP
compatibility surface lives at ``acp.confsearch.sampling`` /
``acp.confsearch.sampling_models`` / ``acp.confsearch.shared.deduplication``
and re-exports the same objects.
"""

from __future__ import annotations

from .deduplication import dedup_by_rmsd, plain_rmsd
from .models import (
    MDS_COORD_TYPE,
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    load_sampling_history,
    read_traj_frame_xyz,
    write_sampling_history,
)
from .pipeline import (
    _compute_saturation,
    assign_basins,
    compute_sampling_history,
    equilibration_cutoff,
    mds_2d,
    parse_traj_frames,
)

__all__ = [
    "MDS_COORD_TYPE",
    "BasinInfo",
    "SamplingHistory",
    "SamplingSaturation",
    "TrajFrame",
    "_compute_saturation",
    "assign_basins",
    "compute_sampling_history",
    "dedup_by_rmsd",
    "equilibration_cutoff",
    "load_sampling_history",
    "mds_2d",
    "parse_traj_frames",
    "plain_rmsd",
    "read_traj_frame_xyz",
    "write_sampling_history",
]
