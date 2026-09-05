"""``xtbmd-censo`` protocol (§3.2.4): MD → GFN1 opt → ISOSTAT → CENSO → DFT.

The complete动力学高精度路线 as ONE protocol — it is never split into
overlapping user entries (plan §3.2.4). Delegates to the retired
``xtbmd_censo_energy`` implementation with the refinement policy mapped
onto its ``no_opt`` / ``rank1_only`` / ``threshold`` knobs.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts import ConfsearchRequest, ProtocolOutcome
from ..selection import threshold_for_policy
from ._common import outcome_from_workflow_result, require_completed, threshold_from_levels

logger = logging.getLogger(__name__)


def run_xtbmd_censo(request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
    """GFN-FF MD → GFN1 batch opt → ISOSTAT → CENSO → fine DFT → free energies."""
    from acp.workflows.xtbmd_censo_energy import run_xtbmd_censo_energy

    preset = request.preset or str(overlay.get("preset") or "censo-light")
    policy = request.refinement_policy
    md = {**overlay, **(request.md_params or {})}

    logger.info(
        "Confsearch xtbmd-censo + %s (preset=%s): MD → GFN1 opt → ISOSTAT → CENSO → DFT",
        policy,
        preset,
    )
    result = run_xtbmd_censo_energy(
        input_source=request.input_source,
        output_dir=str(request.output_dir),
        preset=preset,
        config=request.config,
        name=request.name,
        charge=request.charge,
        multiplicity=request.multiplicity,
        solvent=request.solvent,
        nproc=request.nproc,
        no_opt=policy == "screen",
        rank1_only=policy == "rank1",
        levels=request.levels,
        threshold=threshold_for_policy(policy, default=threshold_from_levels(request)),
        ewin=request.energy_window,
        md_temperature=float(md.get("md_temperature", 400.0)),
        md_time_ps=float(md.get("md_time_ps", 100.0)),
        md_dump_fs=float(md.get("md_dump_fs", 100.0)),
        md_step_fs=float(md.get("md_step_fs", 1.0)),
        md_hmass=float(md.get("md_hmass", 1.0)),
        md_shake=bool(md.get("md_shake", True)),
        md_nvt=bool(md.get("md_nvt", True)),
        md_seed=int(md.get("md_seed", 42)),
        md_seeds=int(md.get("md_seeds", 1)),
        md_method=str(md.get("md_method", "gfnff")),
        md_timeout=md.get("md_timeout"),
        conv_check=bool(md.get("conv_check", True)),
        conv_novelty_max=float(md.get("conv_novelty_max", 0.10)),
        conv_rmsd=float(md.get("conv_rmsd", 0.5)),
        max_frames=int(md.get("max_frames", 500)),
        opt_gfn_level=int(md.get("opt_gfn_level", 1)),
        opt_level=str(md.get("opt_level", "normal")),
        opt_timeout=int(md.get("opt_timeout", 300)),
        keep_frames=False,
        edis=float(md.get("edis", 0.5)),
        gdis=float(md.get("gdis", 0.25)),
    )
    require_completed(result)
    return outcome_from_workflow_result(
        result,
        sampling={
            "method": f"{md.get('md_method', 'gfnff')}-md",
            "preset": preset,
            "policy": policy,
            **{
                key: value for key, value in (result.metadata or {}).items() if key.startswith("n_")
            },
        },
        temperature_k=298.15,
    )


__all__ = ["run_xtbmd_censo"]
