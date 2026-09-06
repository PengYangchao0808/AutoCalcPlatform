"""Conformer energy workflow: CREST → CENSO screening → ensemble refinement.

Implements the ``acp run energy`` workflow (censo-light / censo-default /
censo-zero presets).  The refinement set is the group of lowest-free-energy
conformers whose cumulative Boltzmann population exceeds the configured
threshold (``censo.refinement_threshold``, default 0.99 — v15 semantics,
superseding the v10 rank1-only output for censo-light/censo-zero).  Default
path (opt enabled) refines each selected conformer via the ACP standard
handoff — ORCA opt → ORCA freq (same method/basis) → high-level SP →
Shermo — enforcing the opt/freq consistency rule.  The ``--no-opt`` cheap
path delegates refinement to CENSO itself (xTB geometry + xTB SPH mRRHO)
and applies the same cumulative-population selection to the refined
records.  ``censo-default`` keeps CENSO's native Part3 funnel (0.99
population cutoff) followed by same-level freq + Shermo re-ranking.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.censo_backend import CensoBackend, CensoRunResult
from acp.backends.registry import get_backend
from acp.core.models import Structure, StructureEnsemble
from acp.core.state import WorkflowState
from acp.core.workflow import WorkflowResult
from acp.io.structures import InputFormat, StructureReader
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
from acp.workflows.energy_shared import (
    build_result_ensemble as _build_result_ensemble,
)
from acp.workflows.energy_shared import (
    censo_record_to_candidate as _censo_record_to_candidate,
)
from acp.workflows.energy_shared import (
    conformer_tag as _conformer_tag,
)
from acp.workflows.energy_shared import (
    resolve_crest_ewin as _resolve_crest_ewin,
)
from acp.workflows.energy_shared import (
    resolve_levels as _resolve_levels,
)
from acp.workflows.energy_shared import (
    resolve_solvent_config as _resolve_solvent_config,
)
from acp.workflows.energy_shared import (
    run_rank1_handoff as _run_rank1_handoff,
)
from acp.workflows.energy_shared import (
    select_cumulative_boltzmann as _select_cumulative_boltzmann,
)
from acp.workflows.energy_shared import (
    v2_result_category as _v2_result_category,
)
from acp.workflows.energy_shared import (
    v2_stage_dir as _v2_stage_dir,
)
from acp.workflows.energy_shared import (
    write_final_outputs as _write_final_outputs,
)
from acp.workflows.energy_shared import (
    xtb_passthrough_result as _xtb_passthrough_result,
)
from acp.workflows.ensemble import (
    _is_file_input,
    _is_multiframe_xyz,
)
from acp.workflows.ensemble_thermo import (
    ensemble_total_gibbs,
)
from cccp.config import load_config
from cccp.utils.file_io import write_xyz

logger = logging.getLogger(__name__)

_ENERGY_PRESETS = ("censo-light", "censo-default", "censo-zero")

# Level resolution (``resolve_levels``), the ACP standard handoff
# (``run_rank1_handoff``), Boltzmann selection (``boltzmann_weights`` /
# ``select_cumulative_boltzmann``), the final-output writers
# (``write_final_outputs`` / ``build_ensemble_summary``),
# ``censo_record_to_candidate`` and the ensemble helpers
# (``xtb_passthrough_result`` / ``resolve_solvent_config`` /
# ``resolve_crest_ewin``) live in :mod:`acp.workflows.energy_shared` (E4);
# they are re-imported here under their historical private names so the
# module body and existing importers keep working unchanged.


def _write_screening_ranking(result: CensoRunResult, mol_dir: Path) -> str:
    """Write the auxiliary screening ranking table (ΔGconf,rel estimation)."""
    report_dir = _v2_result_category(mol_dir, "reports")
    csv_path = report_dir / "screening_ranking.csv"

    weights = result.boltzmann_weights()
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["conf_id", "energy_hartree", "gsolv", "grrho", "gtot", "boltzmann_weight"])
        for rec in result.records:
            writer.writerow(
                [
                    rec.conf_id,
                    f"{rec.energy:.10f}",
                    f"{rec.gsolv:.10f}",
                    f"{rec.grrho:.10f}",
                    f"{rec.gtot:.10f}",
                    f"{weights.get(rec.conf_id, 0.0):.6f}",
                ]
            )
    return str(csv_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_conformer_energy(
    input_source: str,
    output_dir: str | Path = "./energy_output",
    preset: str = "censo-light",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    solvent: str | None = None,
    nproc: int | None = None,
    no_opt: bool = False,
    rank1_only: bool = False,
    levels: dict[str, Any] | None = None,
    threshold: float | None = None,
    ewin: float | None = None,
) -> WorkflowResult:
    """Run the conformer energy workflow (cumulative-Boltzmann ensemble, v15).

    Args:
        input_source: SMILES string or path to XYZ file (multi-frame XYZ
            skips CREST and is treated as an external ensemble).
        output_dir: Output directory.
        preset: censo-light (default) / censo-default / censo-zero.
        config: Optional configuration dict.
        name: Molecule name.
        charge: Molecular charge.
        multiplicity: Spin multiplicity.
        solvent: Solvent name or None for gas phase.
        nproc: Number of processors.
        no_opt: Disable the high-accuracy geometry optimization of the
            selected conformers (cheap RSH//xTB path). No effect for
            censo-default.
        rank1_only: Only run the fine DFT handoff (or CENSO refinement on
            cheap paths) on the CENSO/xTB rank1 conformer and compute the
            ensemble total free energy as G_total = G₁ + RT·ln p₁ using the
            full screening weight table (workflow 2). Orthogonal to
            ``no_opt``.
        levels: Method level overrides (censo / dft_opt / refinement_sp /
            screening_sp / thermo / refinement_threshold), field names
            identical to the catalog.
        threshold: Cumulative Boltzmann population threshold
            (0<value<=1, default 0.99). Overrides levels.refinement_threshold
            when explicitly set.
        ewin: CREST energy window in kcal/mol. Priority: this argument >
            ``levels.censo.ewin`` > ``censo.ewin`` config > 6.0.

    Returns:
        WorkflowResult whose ensemble contains the lowest-free-energy
        conformers up to the cumulative Boltzmann population threshold
        (``censo.refinement_threshold``, default 0.99) — for all presets.
    """
    preset = (preset or "censo-light").lower()
    if preset not in _ENERGY_PRESETS:
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=f"Unknown preset '{preset}'. Allowed: {', '.join(_ENERGY_PRESETS)}",
        )

    cfg = load_config(overrides=config) if config is not None else load_config()

    opt_enabled = not no_opt
    if not cfg.get("censo", {}).get("optimization", {}).get("enabled", True):
        opt_enabled = False
    if preset == "censo-default":
        opt_enabled = True  # Part2 is always on for censo-default

    if levels is None:
        levels = {}
    if threshold is not None:
        levels["refinement_threshold"] = threshold

    resolved = _resolve_levels(cfg, levels)

    # Resolve to an absolute path: QC interfaces run subprocesses with
    # cwd=<stage dir> while passing input paths as given, so relative
    # output roots (e.g. remote jobs with --output ".") would break.
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        reader = StructureReader()
        input_format = reader.detect_format(input_source)
        structure = reader.read(
            input_source,
            charge=charge,
            multiplicity=multiplicity,
            name=name,
        )
    except Exception as exc:
        logger.exception("Failed to read input: %s", exc)
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=str(exc),
        )

    safe_name = sanitize_job_name(structure.id)
    if structure.coordinates is None or len(structure.coordinates) == 0:
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=["embed"],
            error="Input embedding produced no 3D coordinates",
        )
    structure = Structure(
        id=safe_name,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        symbols=structure.symbols,
        coordinates=structure.coordinates,
        metadata=structure.metadata,
    )

    mol_dir = resolve_task_output_root(output_root, safe_name)
    state = WorkflowState(mol_dir, safe_name)
    state.initialize(
        input_source=input_source,
        stage_names=["crest", "censo", "dft_handoff", "finalize", "conformer_energy"],
    )

    # Solvent priority: CLI --solvent > levels (UI wizard fields) > YAML.
    effective_solvent_arg = solvent if solvent is not None else resolved["levels_solvent"]
    censo_solvent, solvent_model = _resolve_solvent_config(cfg, effective_solvent_arg)
    if censo_solvent and resolved["levels_solvent_model"]:
        solvent_model = resolved["levels_solvent_model"]
    _solvent_model = solvent_model if solvent_model else "none"
    if censo_solvent and _solvent_model == "none":
        _solvent_model = "smd"

    safe_nproc: int | None = None
    if nproc is not None and nproc > 0:
        safe_nproc = nproc

    stages_completed: list[str] = ["embed"]
    crest_skipped = False
    screening_ranking_csv: str | None = None

    try:
        # ---- Stage: CREST search (or external ensemble) -------------------
        is_file = input_format not in (InputFormat.SMILES,) and _is_file_input(input_source)
        state.set_stage("crest")
        if is_file and _is_multiframe_xyz(Path(input_source)):
            logger.info("Multi-frame XYZ input detected — skipping CREST")
            crest_ensemble_xyz = Path(input_source).resolve()
            state.complete_stage("crest", {"status": "skipped", "reason": "multi-frame XYZ input"})
            crest_skipped = True
        else:
            crest_dir = _v2_stage_dir(mol_dir, "02_SEARCH", "CREST")
            crest_dir.mkdir(parents=True, exist_ok=True)

            crest_cfg = cfg.get("executables", {}).get("crest", {})
            crest_backend = get_backend("crest")(
                config=cfg,
                gfn_level=crest_cfg.get("gfn_level", 2),
                solvent=censo_solvent,
                solvent_model=_solvent_model,
            )

            coords = (
                np.asarray(structure.coordinates)
                if structure.coordinates is not None
                else np.empty((0, 3))
            )
            crest_ewin = _resolve_crest_ewin(
                cfg,
                ewin if ewin is not None else resolved["crest_ewin_level"],
            )
            logger.info("CREST energy window: %.2f kcal/mol", crest_ewin)
            crest_input_xyz = crest_dir / f"{safe_name}.xyz"
            write_xyz(
                crest_input_xyz,
                coords,
                list(structure.symbols),
                title=f"CREST input for {safe_name}",
            )
            crest_ensemble_xyz = crest_backend.search(
                initial_xyz=crest_input_xyz,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                output_dir=crest_dir,
                energy_window=crest_ewin,
            )
            state.complete_stage("crest", {"status": "completed"})

        stages_completed.append("crest")

        temperature_k = float(resolved["temperature_k"])
        threshold = float(resolved["refinement_threshold"])
        candidates: list[dict[str, Any]]
        # Workflow-2 (rank1_only) state: full screening-table weights drive
        # p₁/S_mix while the fine DFT G₁ enters the total G = G₁ + kT·ln p₁.
        external_weights: dict[str, float] | None = None
        external_total_gibbs: float | None = None
        external_total_gibbs_censo: float | None = None
        population_weights: dict[str, float] | None = None
        external_table_source = "censo"
        screening_records: list[Any] | None = None

        def _handoff_selected(selected: list[Any]) -> list[dict[str, Any]]:
            """Run the ACP handoff on each selected conformer (v15 ensemble).

            Individual failures beyond rank1 are logged and skipped so a
            single bad conformer does not discard the rest of the ensemble;
            an empty result raises.
            """
            results: list[dict[str, Any]] = []
            for i, rec in enumerate(selected):
                handoff_dir = _v2_stage_dir(mol_dir, "03_OPT", "ORCA") / _conformer_tag(
                    getattr(rec, "conf_id", None), i
                )
                try:
                    cand = _run_rank1_handoff(
                        cfg,
                        np.asarray(rec.coordinates),
                        list(rec.symbols),
                        structure.charge,
                        structure.multiplicity,
                        handoff_dir,
                        resolved,
                        censo_solvent,
                        _solvent_model,
                        index=i,
                        source=rec.conf_id,
                    )
                except RuntimeError as exc:
                    if i == 0:
                        raise
                    logger.warning(
                        "DFT handoff failed for %s (%s) — dropping this "
                        "conformer from the ensemble",
                        rec.conf_id,
                        exc,
                    )
                    continue
                results.append(cand)
            if not results:
                raise RuntimeError("All conformer handoffs failed")
            return results

        # ---- Preset dispatch ----------------------------------------------
        if preset == "censo-zero" and opt_enabled:
            # xTB-ranked ensemble → cumulative Boltzmann selection → ACP
            # handoff for each survivor (no CENSO CLI involved)
            passthrough = _xtb_passthrough_result(crest_ensemble_xyz, temperature_k)
            if rank1_only:
                rank1 = passthrough.records[0]
                logger.info(
                    "censo-zero (opt on, rank1-only): fine DFT handoff on %s only",
                    rank1.conf_id,
                )
                screening_ranking_csv = _write_screening_ranking(passthrough, mol_dir)
                state.set_stage("dft_handoff")
                cand = _run_rank1_handoff(
                    cfg,
                    np.asarray(rank1.coordinates),
                    list(rank1.symbols),
                    structure.charge,
                    structure.multiplicity,
                    _v2_stage_dir(mol_dir, "03_OPT", "ORCA") / _conformer_tag(rank1.conf_id, 0),
                    resolved,
                    censo_solvent,
                    _solvent_model,
                    index=0,
                    source=rank1.conf_id,
                )
                candidates = [cand]
                state.complete_stage("dft_handoff", {"status": "completed"})
                stages_completed.append("dft_handoff")
                external_weights = passthrough.boltzmann_weights()
                screening_records = list(passthrough.records)
                p1 = external_weights.get(rank1.conf_id, 0.0)
                external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
            else:
                selected = _select_cumulative_boltzmann(
                    passthrough.records,
                    temperature_k,
                    threshold,
                )
                logger.info(
                    "censo-zero (opt on): %d/%d conformers within %.0f%% cumulative "
                    "Boltzmann (xTB) → ACP handoff",
                    len(selected),
                    len(passthrough.records),
                    threshold * 100,
                )
                screening_ranking_csv = _write_screening_ranking(passthrough, mol_dir)
                state.set_stage("dft_handoff")
                candidates = _handoff_selected(selected)
                state.complete_stage("dft_handoff", {"status": "completed"})
                stages_completed.append("dft_handoff")
                population_weights = passthrough.boltzmann_weights()

        else:
            # All remaining paths invoke CENSO
            censo_dir = _v2_stage_dir(mol_dir, "02_SEARCH", "CENSO")
            censo_dir.mkdir(parents=True, exist_ok=True)
            backend = CensoBackend(cfg)

            part_overrides: dict[str, dict[str, Any]] = {}
            if resolved["screening_overrides"]:
                part_overrides["screening"] = resolved["screening_overrides"]
            if resolved["refinement_overrides"]:
                part_overrides["refinement"] = resolved["refinement_overrides"]
            if abs(threshold - 0.99) > 1e-9:
                part_overrides.setdefault("refinement", {})["threshold"] = threshold

            part_templates: dict[str, list[str]] = {}
            if resolved["screening_template_lines"]:
                part_templates["screening"] = resolved["screening_template_lines"]
            if resolved["refinement_template_lines"]:
                part_templates["refinement"] = resolved["refinement_template_lines"]

            if preset == "censo-light" and opt_enabled:
                # CENSO -P -S screens rank1 → ACP handoff. Refinement runs
                # ACP-side here, so only screening overrides/templates may
                # reach the CENSO rcfile (CENSO validates all sections).
                censo_overrides = {k: v for k, v in part_overrides.items() if k == "screening"}
                censo_templates = {k: v for k, v in part_templates.items() if k == "screening"}
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    part_overrides=censo_overrides or None,
                    part_templates=censo_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO screening produced no conformer records")

                screening_ranking_csv = _write_screening_ranking(censo_result, mol_dir)

                if rank1_only:
                    rank1 = censo_result.records[0]
                    logger.info(
                        "censo-light (opt on, rank1-only): fine DFT handoff on %s only",
                        rank1.conf_id,
                    )
                    state.set_stage("dft_handoff")
                    cand = _run_rank1_handoff(
                        cfg,
                        np.asarray(rank1.coordinates),
                        list(rank1.symbols),
                        structure.charge,
                        structure.multiplicity,
                        _v2_stage_dir(mol_dir, "03_OPT", "ORCA") / _conformer_tag(rank1.conf_id, 0),
                        resolved,
                        censo_solvent,
                        _solvent_model,
                        index=0,
                        source=rank1.conf_id,
                    )
                    candidates = [cand]
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    external_weights = censo_result.boltzmann_weights()
                    screening_records = list(censo_result.records)
                    p1 = external_weights.get(rank1.conf_id, 0.0)
                    external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                    external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
                else:
                    selected = _select_cumulative_boltzmann(
                        censo_result.records,
                        temperature_k,
                        threshold,
                    )
                    logger.info(
                        "censo-light (opt on): %d/%d conformers within %.0f%% "
                        "cumulative Boltzmann (screening gtot) → ACP handoff",
                        len(selected),
                        len(censo_result.records),
                        threshold * 100,
                    )
                    state.set_stage("dft_handoff")
                    candidates = _handoff_selected(selected)
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    population_weights = censo_result.boltzmann_weights()

            elif preset in ("censo-light", "censo-zero"):
                # Cheap path (--no-opt): CENSO handles refinement itself.
                # censo-zero preselects the cumulative-population set at the
                # xTB level and restricts CENSO to those frames (-n N).
                logger.info("%s (opt off): CENSO refinement cheap path", preset)
                nconf: int | None
                if rank1_only:
                    # Refine only the xTB rank1 frame (-n 1).  The CENSO
                    # result then carries a single record, so the Boltzmann
                    # table is taken from the xTB passthrough of the full
                    # ensemble instead (a 1-record table would degenerate to
                    # p₁ = 1 and silently drop the mixing correction).
                    passthrough = _xtb_passthrough_result(crest_ensemble_xyz, temperature_k)
                    nconf = 1
                    logger.info(
                        "%s (opt off, rank1-only): CENSO refinement on 1 frame, "
                        "p table from xTB ensemble",
                        preset,
                    )
                else:
                    nconf = None
                    if preset == "censo-zero":
                        passthrough = _xtb_passthrough_result(
                            crest_ensemble_xyz,
                            temperature_k,
                        )
                        preselected = _select_cumulative_boltzmann(
                            passthrough.records,
                            temperature_k,
                            threshold,
                        )
                        nconf = max(1, len(preselected))
                        logger.info(
                            "censo-zero preselection: %d/%d frames within %.0f%% "
                            "cumulative Boltzmann (xTB)",
                            nconf,
                            len(passthrough.records),
                            threshold * 100,
                        )
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    include_refinement=(preset == "censo-light"),
                    nconf=nconf,
                    part_overrides=part_overrides or None,
                    part_templates=part_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO refinement produced no conformer records")

                screening_ranking_csv = _write_screening_ranking(censo_result, mol_dir)

                if rank1_only:
                    rank1 = censo_result.records[0]
                    candidates = [_censo_record_to_candidate(rank1, index=0)]
                    external_weights = passthrough.boltzmann_weights()
                    screening_records = list(passthrough.records)
                    external_table_source = "xtb"
                    # Prefer the weight of the CENSO-refined frame (conf_id
                    # is frame-based, matching the passthrough table); fall
                    # back to the xTB rank1 weight on id mismatch.
                    p1 = external_weights.get(
                        rank1.conf_id,
                        external_weights.get(passthrough.records[0].conf_id, 0.0),
                    )
                    external_total_gibbs = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
                    external_total_gibbs_censo = external_total_gibbs
                else:
                    selected = _select_cumulative_boltzmann(
                        censo_result.records,
                        temperature_k,
                        threshold,
                    )
                    logger.info(
                        "%s (opt off): %d/%d refined conformers within %.0f%% "
                        "cumulative Boltzmann (gtot)",
                        preset,
                        len(selected),
                        len(censo_result.records),
                        threshold * 100,
                    )
                    candidates = [
                        _censo_record_to_candidate(rec, index=i) for i, rec in enumerate(selected)
                    ]
                    population_weights = censo_result.boltzmann_weights()

            else:
                # censo-default: full Part0–Part3 + same-level freq + Shermo
                logger.info("censo-default: full CENSO Part0–Part3 funnel")
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    part_overrides=part_overrides or None,
                    part_templates=part_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO refinement produced no conformer records")

                if rank1_only:
                    # Full funnel still runs CENSO-side; only rank1 gets the
                    # same-level freq + Shermo re-ranking (skip_opt_sp=True).
                    rank1 = censo_result.records[0]
                    logger.info(
                        "censo-default (rank1-only): same-level freq+Shermo on %s only",
                        rank1.conf_id,
                    )
                    screening_ranking_csv = _write_screening_ranking(censo_result, mol_dir)
                    state.set_stage("dft_handoff")
                    try:
                        cand = _run_rank1_handoff(
                            cfg,
                            np.asarray(rank1.coordinates),
                            list(rank1.symbols),
                            structure.charge,
                            structure.multiplicity,
                            _v2_stage_dir(mol_dir, "03_OPT", "ORCA")
                            / _conformer_tag(rank1.conf_id, 0),
                            resolved,
                            censo_solvent,
                            _solvent_model,
                            index=0,
                            source=rank1.conf_id,
                            sp_energy_precomputed=rank1.energy,
                            skip_opt_sp=True,
                        )
                    except RuntimeError as exc:
                        logger.warning(
                            "Same-level freq+Shermo failed for %s (%s) — "
                            "falling back to CENSO gtot",
                            rank1.conf_id,
                            exc,
                        )
                        cand = _censo_record_to_candidate(rank1, index=0)
                    candidates = [cand]
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    external_weights = censo_result.boltzmann_weights()
                    screening_records = list(censo_result.records)
                    p1 = external_weights.get(rank1.conf_id, 0.0)
                    external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                    external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
                else:
                    state.set_stage("dft_handoff")
                    candidates = []
                    for i, rec in enumerate(censo_result.records):
                        handoff_dir = _v2_stage_dir(mol_dir, "03_OPT", "ORCA") / _conformer_tag(
                            getattr(rec, "conf_id", None), i
                        )
                        try:
                            cand = _run_rank1_handoff(
                                cfg,
                                np.asarray(rec.coordinates),
                                list(rec.symbols),
                                structure.charge,
                                structure.multiplicity,
                                handoff_dir,
                                resolved,
                                censo_solvent,
                                _solvent_model,
                                index=i,
                                source=rec.conf_id,
                                sp_energy_precomputed=rec.energy,
                                skip_opt_sp=True,
                            )
                        except RuntimeError as exc:
                            logger.warning(
                                "Same-level freq+Shermo failed for %s (%s) — "
                                "falling back to CENSO gtot",
                                rec.conf_id,
                                exc,
                            )
                            cand = _censo_record_to_candidate(rec, index=i)
                        candidates.append(cand)
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    population_weights = censo_result.boltzmann_weights()

        # ---- Finalization ---------------------------------------------------
        state.set_stage("finalize")
        outputs = _write_final_outputs(
            candidates,
            mol_dir,
            safe_name,
            temperature_k,
            external_weights=external_weights,
            external_total_gibbs=external_total_gibbs,
            external_total_gibbs_censo=external_total_gibbs_censo,
            population_weights=population_weights,
            external_table_source=external_table_source,
            screening_records=screening_records,
        )
        ensemble = _build_result_ensemble(candidates, structure)
        state.complete_stage("finalize", {"n_conformers": len(candidates)})
        stages_completed.append("finalize")

    except Exception as exc:
        logger.exception("Conformer energy workflow failed: %s", exc)
        state.fail_stage("conformer_energy", str(exc))
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=list(stages_completed),
            error=str(exc),
        )

    state.mark_completed()
    metadata: dict[str, Any] = {
        "preset": preset,
        "opt_enabled": opt_enabled,
        "n_conformers": len(candidates),
        "rank1_only": rank1_only,
        "refinement_threshold": float(resolved["refinement_threshold"]),
        "crest_ewin": _resolve_crest_ewin(
            cfg,
            ewin if ewin is not None else resolved["crest_ewin_level"],
        ),
        "crest_skipped": crest_skipped,
        **outputs,
    }
    if screening_ranking_csv:
        metadata["screening_ranking_csv"] = screening_ranking_csv
    # Screening conf_ids of the returned candidates (the set that received
    # the fine refinement); matches conformer_thermo.csv "source" and the
    # conf_id= markers in all_conformers.xyz so the Confsearch engine can
    # populate ProtocolOutcome.refined_conf_ids (G1 gate).
    metadata["refined_conf_ids"] = [str(c.get("source", "")) for c in candidates]

    return WorkflowResult(
        status="completed",
        ensemble=ensemble,
        stages_completed=stages_completed,
        metadata=metadata,
    )


__all__ = ["run_conformer_energy"]
