"""ACP compatibility shim for PES path seed selection (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.path_selection` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.path_selection import ...`` imports and attribute
access keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.path_selection import (
    SeedSelection,
    SelectionPolicy,
    policy_from_config,
    replay_rescue_selection,
    select_path_seeds,
)

__all__ = [
    "SeedSelection",
    "SelectionPolicy",
    "policy_from_config",
    "replay_rescue_selection",
    "select_path_seeds",
]
