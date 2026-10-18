"""ACP compatibility shim for Confsearch geometric deduplication (plan todo 20).

Implementation moved to :mod:`cccp.analysis.sampling.deduplication` (T5
write-registry §4.4 ``C直写`` 移交).  This module re-exports the **same
objects** so existing ``from acp.confsearch.shared.deduplication import ...``
imports keep working unchanged (RT-SAME).
"""

from __future__ import annotations

from cccp.analysis.sampling.deduplication import dedup_by_rmsd, plain_rmsd

__all__ = ["dedup_by_rmsd", "plain_rmsd"]
