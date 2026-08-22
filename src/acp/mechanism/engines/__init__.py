"""Mechanism engines: per-module computation cores (M1/M3).

Each engine wraps one provider family so both the Study layer and the
standalone ``mech-*`` modules share the same science.

Author: QCcalc Team
"""

from __future__ import annotations

from .confirmation import ConfirmationEngine
from .conformer import ConformerEngine
from .elementary_step import (
    ElementaryStepEngine,
    RouteContext,
    StepOutcome,
    exploration_key,
    mark_route_status,
    persist_refinement_manifest,
    persist_route_manifest,
    replace_seed_candidate,
    route_fingerprint,
    route_status_matches,
)

__all__ = [
    "ConformerEngine",
    "ConfirmationEngine",
    "ElementaryStepEngine",
    "RouteContext",
    "StepOutcome",
    "exploration_key",
    "mark_route_status",
    "persist_refinement_manifest",
    "persist_route_manifest",
    "replace_seed_candidate",
    "route_fingerprint",
    "route_status_matches",
]
