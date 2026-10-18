"""ACP compatibility shim for Confsearch sampling-history models (plan todo 20).

Implementation moved to :mod:`cccp.analysis.sampling.models` (T5
write-registry §4.4 ``C直写`` 移交).  This module re-exports the **same
objects** so existing ``from acp.confsearch.sampling_models import ...``
imports keep working unchanged (RT-SAME; ``sampling_history_v1`` schema
byte-preserved).
"""

from __future__ import annotations

from cccp.analysis.sampling.models import (
    MDS_COORD_TYPE,
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    load_sampling_history,
    read_traj_frame_xyz,
    write_sampling_history,
)

__all__ = [
    "MDS_COORD_TYPE",
    "BasinInfo",
    "SamplingHistory",
    "SamplingSaturation",
    "TrajFrame",
    "load_sampling_history",
    "read_traj_frame_xyz",
    "write_sampling_history",
]
