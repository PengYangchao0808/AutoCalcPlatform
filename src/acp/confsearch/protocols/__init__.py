"""Confsearch protocol runner registry.

Each protocol owns one sampling + primary-energy路线 (§3.2) and returns a
normalized :class:`~acp.confsearch.contracts.ProtocolOutcome`. Runners
delegate to the battle-tested workflow implementations (ensemble / energy /
xtbmd_censo_energy) instead of duplicating CREST/CENSO/xTB orchestration
(plan §14 migration table).
"""

from __future__ import annotations

from ._common import (  # noqa: F401
    coords_list,
    outcome_from_workflow_result,
    records_from_ensemble_result,
    refined_ids_from_metadata,
    require_completed,
    threshold_from_levels,
)
from .censo_crest import run_censo_crest  # noqa: E402
from .xtb_crest import run_xtb_crest  # noqa: E402
from .xtb_md import run_xtb_md  # noqa: E402
from .xtbmd_censo import run_xtbmd_censo  # noqa: E402

PROTOCOL_RUNNERS = {
    "xtb-crest": run_xtb_crest,
    "xtb-md": run_xtb_md,
    "censo-crest": run_censo_crest,
    "xtbmd-censo": run_xtbmd_censo,
}

__all__ = [
    "PROTOCOL_RUNNERS",
    "coords_list",
    "outcome_from_workflow_result",
    "records_from_ensemble_result",
    "refined_ids_from_metadata",
    "require_completed",
    "run_censo_crest",
    "run_xtb_crest",
    "run_xtb_md",
    "run_xtbmd_censo",
    "threshold_from_levels",
]
