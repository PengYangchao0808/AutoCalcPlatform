"""ACP compatibility shim for PES path candidate selection (plan todo 22).

Implementation moved to :mod:`cccp.analysis.pes.candidates` (T5 write-registry
§4.4 ``C直写`` 移交).  This module re-exports the **same objects** so existing
``from acp.calculations.pes.candidates import ...`` imports and attribute access
keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.pes.candidates import (
    PathCandidate,
    PathPoint,
    SearchResult,
    select_candidates,
    select_primary_int,
    select_primary_ts,
)

__all__ = [
    "PathCandidate",
    "PathPoint",
    "SearchResult",
    "select_candidates",
    "select_primary_int",
    "select_primary_ts",
]
