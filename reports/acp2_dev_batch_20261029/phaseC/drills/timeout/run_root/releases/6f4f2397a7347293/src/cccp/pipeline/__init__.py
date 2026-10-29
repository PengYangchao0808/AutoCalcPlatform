"""
Pipeline Package
================

Pipeline execution components.

DEPRECATED — legacy cccp API (marked 2026-10-05, plan todo 30; no removal scheduled).

Usage evidence (verified by grep at repo HEAD 66222f7):
    - No ACP production callers and no test callers (the phrase "cccp
      pipeline" appears only in a docstring in
      ``acp/calculations/primitives/_common.py``).
    - No importers inside ``cccp`` itself — ``PipelineExecutor`` is reached
      only via this re-export.

Support scope: all exports stay (external users cannot be confirmed). Fully
dormant thin shim — its methods already delegate to an engine removed in
wave-8. New multi-step execution belongs in ``acp/calculations``
(``CalculationPlanExecutor`` / ``BatchOptimizeEngine``).
"""

from cccp.pipeline.executor import PipelineExecutor

__all__ = [
    "PipelineExecutor",
]
