"""``xtb-md`` protocol (§3.2.2): GFN-FF/xTB MD → GFN1 opt → dedup → Boltzmann.

Pure xTB — no CENSO, no ORCA. Reuses the xtbmd sampling layer
(``run_md_replicas`` / ``_batch_opt_frames`` / ISOSTAT backend /
``_filter_energy_window``) as the shared sampling base (plan §3.2.2/§14)
without the CENSO/DFT tail that ``xtbmd-censo`` adds.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from ..contracts import ConfsearchRequest, ProtocolOutcome
from ._common import coords_list

logger = logging.getLogger(__name__)


def _write_embed_xyz(path: Path, symbols: list[str], coords: Any, safe_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"{len(symbols)}\n")
        handle.write(f"Embedded input for {safe_name}\n")
        for symbol, coord in zip(symbols, coords):
            handle.write(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")


def run_xtb_md(request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
    """GFN-FF/xTB MD → GFN1 batch opt → ISOSTAT dedup → xTB ranking → Boltzmann."""
    from acp.backends.isostat_backend import IsostatBackend
    from acp.backends.registry import get_backend
    from acp.io.structures import StructureReader
    from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
    from ..shared import resolve_crest_ewin, v2_stage_dir, xtb_passthrough_result
    from acp.workflows.xtbmd_censo_energy import _batch_opt_frames, _filter_energy_window  # noqa: F401 — retired, lazy
    from acp.workflows.xtbmd_md import run_md_replicas
    from cccp.config import load_config

    cfg = request.config if request.config is not None else load_config()

    reader = StructureReader()
    structure = reader.read(
        request.input_source,
        charge=request.charge,
        multiplicity=request.multiplicity,
        name=request.name,
    )
    safe_name = sanitize_job_name(structure.id)
    mol_dir = resolve_task_output_root(request.output_dir.resolve(), safe_name)

    md = {**overlay, **(request.md_params or {})}
    md_temperature = float(md.get("md_temperature", 400.0))
    solvent = request.solvent
    solvent_model = str(md.get("solvent_model", "none")) if solvent else "none"
    temperature_k = float(request.temperature or 298.15)

    # ── MD sampling (shared layer) ─────────────────────────────────────
    xtbmd_dir = v2_stage_dir(mol_dir, "02_SEARCH", "xTB")
    xtbmd_dir.mkdir(parents=True, exist_ok=True)
    embed_xyz = v2_stage_dir(mol_dir, "01_PREPARE") / "embed.xyz"
    coords = structure.coordinates if structure.coordinates is not None else []
    _write_embed_xyz(embed_xyz, list(structure.symbols), coords, safe_name)

    md_result = run_md_replicas(
        request.input_source,
        embed_xyz,
        md_seed=int(md.get("md_seed", 42)),
        md_seeds=int(md.get("md_seeds", 1)),
        md_method=str(md.get("md_method", "gfnff")),
        temperature=md_temperature,
        time_ps=float(md.get("md_time_ps", 100.0)),
        dump_fs=float(md.get("md_dump_fs", 100.0)),
        step_fs=float(md.get("md_step_fs", 1.0)),
        hmass=float(md.get("md_hmass", 1.0)),
        shake=bool(md.get("md_shake", True)),
        nvt=bool(md.get("md_nvt", True)),
        solvent=solvent,
        solvent_model=solvent_model,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        output_dir=xtbmd_dir,
        config=cfg,
    )
    if not md_result.success:
        raise RuntimeError(f"xTB-MD sampling failed: {md_result.error_message}")
    traj_xyz = xtbmd_dir / "traj.xyz"
    n_frames_raw = int(str((md_result.metadata or {}).get("n_frames", 0)))

    # ── GFN1 batch optimization (shared layer) ─────────────────────────
    batch_dir = v2_stage_dir(mol_dir, "03_OPT", "xTB")
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_result = _batch_opt_frames(
        traj_xyz,
        gfn_level=int(md.get("opt_gfn_level", 1)),
        opt_level=str(md.get("opt_level", "normal")),
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        nproc=request.nproc or int(cfg.get("resources", {}).get("nproc") or 1),
        solvent=solvent,
        solvent_model=solvent_model,
        max_frames=int(md.get("max_frames", 500)),
        opt_timeout=int(md.get("opt_timeout", 300)),
        keep_frames=False,
        edis=float(md.get("edis", 0.5)),
        gdis=float(md.get("gdis", 0.25)),
        replica_frames=cast(list[int] | None, (md_result.metadata or {}).get("replica_frames")),
        conv_check=bool(md.get("conv_check", True)),
        conv_rmsd=float(md.get("conv_rmsd", 0.5)),
        conv_novelty_max=float(md.get("conv_novelty_max", 0.10)),
        temperature_k=temperature_k,
        work_dir=batch_dir,
        cfg=cfg,
    )
    isomers_xyz = batch_dir / "isomers.xyz"
    isomers_energies_json = batch_dir / "isomers_energies.json"

    # ── ISOSTAT dedup (shared layer) ───────────────────────────────────
    isostat_dir = v2_stage_dir(mol_dir, "02_SEARCH", "ISOSTAT")
    isostat_dir.mkdir(parents=True, exist_ok=True)
    isostat_backend = cast(IsostatBackend, get_backend("isostat")(cfg))
    isostat_result = isostat_backend.cluster(
        isomers_xyz,
        output_dir=isostat_dir,
        edis=float(md.get("edis", 0.5)),
        gdis=float(md.get("gdis", 0.25)),
        temperature=temperature_k,
        nthreads=1,
    )
    if not isostat_result.success or not isostat_result.output_file:
        raise RuntimeError(
            f"ISOSTAT clustering failed: {isostat_result.error_message or 'no output'}"
        )
    cluster_xyz = Path(isostat_result.output_file)

    # ── energy window + xTB ranking (pure xTB terminus) ────────────────
    ewin = resolve_crest_ewin(
        cfg,
        request.energy_window if request.energy_window is not None else md.get("ewin"),
    )
    filter_result = _filter_energy_window(
        cluster_xyz,
        isomers_xyz,
        isomers_energies_json,
        ewin=ewin,
        work_dir=batch_dir / "energy_filter",
    )
    passthrough = xtb_passthrough_result(filter_result.ensemble_xyz, temperature_k)
    weights = passthrough.boltzmann_weights()
    records: list[dict[str, Any]] = []
    for rec in passthrough.records:
        records.append(
            {
                "conf_id": str(rec.conf_id),
                "symbols": list(rec.symbols),
                "coordinates": coords_list(rec.coordinates),
                "energy_hartree": rec.energy,
                "free_energy_hartree": rec.gtot,
                "weight": weights.get(rec.conf_id, 0.0),
                "properties": {},
            }
        )
    if not records:
        raise RuntimeError("xtb-md protocol produced no conformers after filtering")
    return ProtocolOutcome(
        records=records,
        temperature_k=temperature_k,
        refined_conf_ids=[],
        sampling={
            "method": f"{md.get('md_method', 'gfnff')}-md",
            "n_raw_frames": n_frames_raw,
            "n_after_isostat": getattr(batch_result, "n_ok", None),
            "n_after_filter": filter_result.n_after_filter,
            "ewin": ewin,
        },
        stages_completed=["embed", "xtbmd", "batch_opt", "isostat", "energy_filter"],
        workflow_metadata={},
    )


__all__ = ["run_xtb_md"]
