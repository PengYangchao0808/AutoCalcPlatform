"""``xtb-crest`` protocol (§3.2.1): CREST → GFN2-xTB → dedup → Boltzmann.

Pure xTB — no CENSO, no ORCA. Reuses the retired ensemble workflow's
``censo-zero`` passthrough route (CREST ensemble exported directly with
xTB title energies), which is exactly this protocol's science.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts import ConfsearchRequest, ProtocolOutcome
from ._common import outcome_from_workflow_result, require_completed

logger = logging.getLogger(__name__)


def run_xtb_crest(request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
    """CREST → GFN2-xTB energies → dedup → ranking → Boltzmann (pure xTB)."""
    from acp.workflows.ensemble import run_ensemble_generation

    logger.info("Confsearch xtb-crest: CREST + xTB passthrough (censo-zero)")
    result = run_ensemble_generation(
        input_source=request.input_source,
        output_dir=str(request.output_dir),
        preset="censo-zero",
        config=request.config,
        name=request.name,
        charge=request.charge,
        multiplicity=request.multiplicity,
        solvent=request.solvent,
        nproc=request.nproc,
        ewin=request.energy_window if request.energy_window is not None else overlay.get("ewin"),
    )
    require_completed(result)
    assert result.ensemble is not None
    return outcome_from_workflow_result(
        result,
        sampling={
            "method": "crest-gfn2",
            "n_raw_frames": len(result.ensemble.records),
        },
        temperature_k=298.15,
    )


__all__ = ["run_xtb_crest"]
