"""
Molecular Composition & Hessian Default Utilities
=================================================

Compatibility re-export shim (acp→cccp architecture remediation, plan
todo 6 / F1).  The element classification and ``Recalc_Hess`` policy
resolution implementation moved verbatim to
``cccp.qc.hessian_policy`` — the single source of truth so that CLI, API,
catalog, scheduler, workflows, and the legacy ORCA interface all share
one graded-default heuristic (ORCA Hessian Defaults Plan v1.3).

This module keeps the historical ``acp.chem.composition`` import surface
stable for ACP-side consumers during the migration period; it contains
no implementation of its own.

Author: QCcalc Team
"""

from __future__ import annotations

from cccp.qc import hessian_policy as _hessian_policy
from cccp.qc.hessian_policy import (
    AUTO_RECALC_HESS,
    HETEROATOM_ELEMENTS,
    LIGHT_ELEMENTS,
    MAX_RECALC_HESS_INTERVAL,
    NON_LIGHT_DEFAULT_INTERVAL,
    HessianResolution,
    classify_symbols,
    default_recalc_hess_for_symbols,
    is_light_element_molecule,
    normalize_recalc_hess,
    resolve_recalc_hess,
)

# Private provenance constants kept accessible for compatibility with the
# historical module namespace; the single definition lives in cccp.
_HESSIAN_PREVIEW_REASON_LIGHT = _hessian_policy._HESSIAN_PREVIEW_REASON_LIGHT
_HESSIAN_PREVIEW_REASON_HETERO = _hessian_policy._HESSIAN_PREVIEW_REASON_HETERO
_HESSIAN_PREVIEW_REASON_HEAVY = _hessian_policy._HESSIAN_PREVIEW_REASON_HEAVY

__all__ = [
    "AUTO_RECALC_HESS",
    "HETEROATOM_ELEMENTS",
    "HessianResolution",
    "LIGHT_ELEMENTS",
    "MAX_RECALC_HESS_INTERVAL",
    "NON_LIGHT_DEFAULT_INTERVAL",
    "classify_symbols",
    "default_recalc_hess_for_symbols",
    "is_light_element_molecule",
    "normalize_recalc_hess",
    "resolve_recalc_hess",
]
