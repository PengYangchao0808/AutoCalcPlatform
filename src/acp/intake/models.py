"""Structure intake models — compatibility forwarder (plan todo 16).

The ``StructureAsset`` / ``StructureParseResult`` dataclasses moved verbatim to
``cccp.analysis.structure.models``; this module re-exports the *same objects*
so the historical ``acp.intake.models`` import surface stays stable.  It
contains no implementation of its own.
"""

from __future__ import annotations

from cccp.analysis.structure.models import StructureAsset, StructureParseResult

__all__ = ["StructureAsset", "StructureParseResult"]
