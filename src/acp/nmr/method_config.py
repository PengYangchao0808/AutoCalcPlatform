"""Platform entry for the NMR method resolver (plan C05-s5 / T29 split).

The science resolver moved verbatim to
``cccp.analysis.nmr.method_config`` (defaults, precedence, typed rejection
and coercion). This module keeps the ``acp.catalog`` coupling: it injects
``METHOD_META`` / ``FIELD_DEFINITIONS`` into the cccp resolver, so the
public call signature (``resolve_nmr_method(method, config=None)``) is
unchanged for the CLI and scheduler consumers.

``NmrMethodConfig`` / ``NmrMethodConfigError`` are re-exported as the same
objects, so existing imports and ``isinstance`` checks keep working.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp.catalog import FIELD_DEFINITIONS, METHOD_META
from cccp.analysis.nmr.method_config import (
    NmrMethodConfig,
    NmrMethodConfigError,
)
from cccp.analysis.nmr.method_config import (
    resolve_nmr_method as _resolve_nmr_method,
)

__all__ = ["NmrMethodConfig", "NmrMethodConfigError", "resolve_nmr_method"]


def resolve_nmr_method(
    method: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> NmrMethodConfig:
    """Resolve a catalog/GUI NMR *method* payload to typed effective state.

    Thin platform wrapper: supplies ``acp.catalog`` vocabularies to the cccp
    science resolver. See ``cccp.analysis.nmr.method_config`` for the full
    precedence/rejection contract.
    """
    return _resolve_nmr_method(
        method,
        config,
        method_meta=METHOD_META,
        field_definitions=FIELD_DEFINITIONS,
    )
