"""``censo-crest`` protocol (§3.2.3): CREST → CENSO → conformer free energies.

Unifies the retired ``ensemble`` and ``energy`` entries: their difference
is expressed by the refinement policy (screen vs rank1/cumulative-99/all),
not by two separate workflows (plan §16 mapping table). The explicit
``backend=rph-parity`` route (§4) also lives here — ReactionProfileHunter's
CENSO-lite chain is the parity comparison for this protocol only.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts import ConfsearchRequest, ProtocolOutcome
from ..selection import threshold_for_policy
from ._common import (
    coords_list,
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


def run_rph_parity(request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
    """Explicit RPH parity backend for ``censo-crest`` (§4).

    Runs the ReactionProfileHunter CENSO-lite ensemble chain
    (CREST → xTB prescreen → B97-3c SP → xTB mRRHO → Boltzmann) via the
    parity-only ``RPHEnsembleProvider``. Production default remains
    ``backend=native``; unsupported protocol combinations are rejected by
    :func:`~acp.confsearch.contracts.validate_request` before reaching here.
    """
    from acp.io.structures import StructureReader
    from acp.mechanism.engines.conformer import ConformerEngine
    from acp.mechanism.providers.rph_adapter import RPHEnsembleProvider
    from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
    from acp.workflows.energy_shared import v2_stage_dir

    reader = StructureReader()
    structure = reader.read(
        request.input_source,
        charge=request.charge,
        multiplicity=request.multiplicity,
        name=request.name,
    )
    safe_name = sanitize_job_name(structure.id)
    mol_dir = resolve_task_output_root(request.output_dir.resolve(), safe_name)
    work_root = v2_stage_dir(mol_dir, "02_SEARCH", "S1")

    engine = ConformerEngine(
        config=request.config,
        work_root=work_root,
        mode="censo-lite",
        ensemble_provider=RPHEnsembleProvider(config=request.config),
    )
    state = engine.run(
        request.input_source,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        name=request.name,
    )
    ensemble = state.ensemble
    records: list[dict[str, Any]] = []
    if ensemble is not None:
        for record in ensemble.records:
            records.append(
                {
                    "conf_id": str(record.id),
                    "symbols": list(record.symbols),
                    "coordinates": (
                        coords_list(record.coordinates) if record.coordinates is not None else None
                    ),
                    "energy_hartree": record.energy_hartree,
                    "free_energy_hartree": record.free_energy_hartree,
                    "weight": record.weight,
                    "properties": dict(record.properties or {}),
                }
            )
    if not records:
        raise RuntimeError("RPH parity ensemble produced no conformer records")
    return ProtocolOutcome(
        records=records,
        temperature_k=298.15,
        refined_conf_ids=[],
        sampling={"method": "rph-censo-lite", "parity": True},
        stages_completed=["s1"],
        workflow_metadata={"provider": "rph-parity"},
    )


__all__ = ["run_censo_crest", "run_rph_parity"]
