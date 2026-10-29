"""ACP compatibility shim for Confsearch refinement-policy selection (plan todo 21).

Implementation moved to :mod:`cccp.analysis.confsearch.selection` (T5
write-registry §4.4 ``C直写`` 移交).  This module re-exports the **same
objects** so existing ``from acp.confsearch.selection import ...`` imports and
``acp.confsearch.selection.<attr>`` access keep working unchanged (RT-SAME;
selection boundaries — including the 0.99/0.90 cumulative thresholds — are
preserved).
"""

from __future__ import annotations

from cccp.analysis.confsearch.selection import (
    select_for_refinement,
    threshold_for_policy,
)

__all__ = ["select_for_refinement", "threshold_for_policy"]
