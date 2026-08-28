"""``censo-crest`` protocol (§3.2.3): CREST → CENSO → conformer free energies.

Unifies the retired ``ensemble`` and ``energy`` entries: their difference
is expressed by the refinement policy (screen vs rank1/cumulative-99/all),
not by two separate workflows (plan §16 mapping table).

The RPH parity backend (§4) was removed in Wave 8 (2026-08 refactor).
``provider_backend='rph'`` now raises ``ValueError`` at config-parsing
time — see :func:`~acp.confsearch.contracts.validate_request`.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts import ConfsearchRequest, ProtocolOutcome
from ..selection import threshold_for_policy
from ._common import (
    outcome_from_workflow_result,
    require_completed,
    threshold_from_levels,
)

logger = logging.getLogger(__name__)


def run_censo_crest(request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
    """CREST → CENSO screening/energy → ranking → Boltzmann (§3.2.3)."""
    preset = request.preset or str(overlay.get("preset") or "censo-light")
    policy = request.refinement_policy

    if policy == "screen":
        from acp.workflows.ensemble import run_ensemble_generation

        logger.info("Confsearch censo-crest + screen: CREST → CENSO ensemble")
        result = run_ensemble_generation(
            input_source=request.input_source,
            output_dir=str(request.output_dir),
            preset=preset,
            config=request.config,
            name=request.name,
            charge=request.charge,
            multiplicity=request.multiplicity,
            solvent=request.solvent,
            nproc=request.nproc,
            ewin=request.energy_window,
        )
        require_completed(result)
        return outcome_from_workflow_result(
            result,
            sampling={"method": "crest-censo", "preset": preset},
            temperature_k=298.15,
        )

    from acp.workflows.energy import run_conformer_energy

    threshold = threshold_for_policy(policy, default=threshold_from_levels(request))
    logger.info(
        "Confsearch censo-crest + %s: CREST → CENSO → DFT refinement (preset=%s)",
        policy,
        preset,
    )
    result = run_conformer_energy(
        input_source=request.input_source,
        output_dir=str(request.output_dir),
        preset=preset,
        config=request.config,
        name=request.name,
        charge=request.charge,
        multiplicity=request.multiplicity,
        solvent=request.solvent,
        nproc=request.nproc,
        rank1_only=policy == "rank1",
        levels=request.levels,
        threshold=threshold,
        ewin=request.energy_window,
    )
    require_completed(result)
    return outcome_from_workflow_result(
        result,
        sampling={"method": "crest-censo", "preset": preset, "policy": policy},
        temperature_k=298.15,
    )


__all__ = ["run_censo_crest"]
