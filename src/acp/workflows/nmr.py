# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryIsInstance=false
"""NMR + DP4/DP5 stereochemistry-assignment workflow (DevDoc §4/§5).

Orchestrates the full pipeline for each candidate:

1. conformer generation — ACP-side CREST→CENSO orchestration over the
   cccp calculation task cores (``run_conformer_search`` + ``censo_refine``;
   ``censo-zero`` skips CENSO and passes the CREST/xTB ensemble through);
2. per-conformer GIAO NMR shielding via the ``run_nmr_shielding`` task core;
3. Boltzmann + equivalence averaging;
4. assignment (assigned passthrough / unassigned Hungarian matching);
5. per-nucleus linear-regression scaling;
6. DP4 (set-normalized) and DP5 (independent) probability.

The analysis stages 3–6 are pure-Python and run on the head node; the
heavy compute (CREST/CENSO/ORCA GIAO) goes through the cccp task cores.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import numpy as np
from numpy.typing import NDArray

from acp.calculations.progress import ProgressReporter
from acp.core.models import Structure, StructureEnsemble
from acp.core.workflow import WorkflowResult
from acp.io.structures import (
    NMR_TOPOLOGY_SOURCES,
    NMR_TOPOLOGY_XYZ_UNAVAILABLE,
    StructureReader,
    TopologyUnavailableError,
    capture_nmr_topology,
)
from acp.nmr.assignment import (
    collect_residual_inputs,
    match_assigned,
)
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.enumerate import enumerate_candidates
from acp.nmr.equivalence import (
    detect_equivalence_groups,
    merge_explicit_and_detected,
)
from acp.nmr.error_model import (
    dp5_model_available,
    load_dp5_model,
    load_error_model,
    validate_error_model_binding,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import (
    CandidateResult,
    ConformerShielding,
    ExperimentalNmr,
    NmrConfig,
    NmrReport,
)
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    compute_dp5_goodman,
    dp5_log_to_probability,
    normalize_dp4,
)
from acp.nmr.report import write_all_reports
from acp.nmr.scaling import build_assignments, fit_scaling_goodman
from acp.nmr.structure_map import NmrStructureMap
from acp.storage.layout import TaskStorage
from acp.storage.manifest import ResultManifest
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name, write_result_summary
from cccp.backends.crest import CrestBackend
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import (
    CensoRefineOptions,
    ConformerSearchOptions,
    MethodSpec,
    NmrShieldingOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
)
from cccp.calculation.results import ConformerSearchPayload, NmrShieldingPayload, TaskResult
from cccp.calculation.tasks.censo_refine import run_censo_refine
from cccp.calculation.tasks.conformer_search import run_conformer_search
from cccp.calculation.tasks.nmr_shielding import run_nmr_shielding
from cccp.config import load_config
from cccp.qc.interfaces.censo import (
    CensoConformerRecord,
    CensoInterface,
    CensoRunResult,
    part_index,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rdkit import Chem

NMR_STAGES: Final[tuple[str, ...]] = (
    "embed_smiles",
    "crest_search",
    "censo_prescreening",
    "censo_screening",
    "ensemble_export",
    "giao_nmr",
    "boltzmann_average",
    "dp4_dp5_probability",
    "nmr_report",
)


def _fail_progress(reporter: ProgressReporter | None, error: str) -> None:
    """Fail the active stage, or the whole run before stages begin."""
    if reporter is None:
        return
    current_stage = reporter.current_stage
    if current_stage is None:
        reporter.fail(error)
    else:
        reporter.fail_stage(current_stage, error)


# ---------------------------------------------------------------------------
# Input parsing helpers
# ---------------------------------------------------------------------------


def _parse_candidates(
    input_sources: list[str],
    charge: int | None,
    multiplicity: int | None,
    strict_topology: bool = False,
) -> list[Structure]:
    """Parse each input source (SMILES/SDF/XYZ) into a :class:`Structure`.

    Every candidate additionally captures its bonded molecular graph (gap
    G01) into metadata: ``nmr_topology_source`` (one of
    :data:`~acp.io.structures.NMR_TOPOLOGY_SOURCES`),
    ``nmr_structure_map`` (JSON-safe :class:`NmrStructureMap` payload fixing
    source↔mol atom order for ``atom_uid`` joins), ``nmr_topology_mol`` (the
    captured RDKit Mol or ``None``) and ``nmr_topology_reason``. Nothing is
    fabricated when topology is unavailable — the map and mol stay ``None``.

    With *strict_topology* a missing graph raises
    :class:`TopologyUnavailableError` instead of degrading.
    """
    reader = StructureReader()
    candidates: list[Structure] = []
    for idx, source in enumerate(input_sources):
        structure = reader.read(
            source,
            charge=charge,
            multiplicity=multiplicity,
            name=f"candidate_{idx + 1}",
        )
        safe = sanitize_job_name(structure.id) or f"candidate_{idx + 1}"
        metadata: dict[str, object] = {"source": source, **(structure.metadata or {})}
        metadata.update(_capture_candidate_topology(structure, source, charge, multiplicity))
        if strict_topology and metadata["nmr_structure_map"] is None:
            raise TopologyUnavailableError(
                f"topology unavailable for candidate {idx + 1} ({source!r}): "
                f"{metadata['nmr_topology_reason'] or 'no bonded graph captured'}"
            )
        candidates.append(
            Structure(
                id=safe,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                symbols=structure.symbols,
                coordinates=structure.coordinates,
                metadata=metadata,
            )
        )
    return candidates


def _capture_candidate_topology(
    structure: Structure,
    source: str,
    charge: int | None,
    multiplicity: int | None,
) -> dict[str, object]:
    """Capture the candidate's bonded graph + structure-map provenance (G01).

    Returns the four ``nmr_topology_*`` metadata keys. The map and mol are
    attached only when the captured graph matches the parsed structure
    atom-for-atom — provenance is never fabricated for a mismatched graph.
    """
    capture = capture_nmr_topology(source, charge=charge, multiplicity=multiplicity)
    unavailable: dict[str, object] = {
        "nmr_topology_source": capture.topology_source,
        "nmr_structure_map": None,
        "nmr_topology_mol": None,
        "nmr_topology_reason": capture.reason or "bonded graph unavailable",
    }
    mol = capture.mol
    if mol is None:
        return unavailable
    if mol.GetNumAtoms() != len(structure.symbols):
        reason = (
            f"captured graph has {mol.GetNumAtoms()} atoms but the parsed structure "
            f"has {len(structure.symbols)} (source/structure mismatch)"
        )
        logger.debug("NMR topology capture discarded for %r: %s", source, reason)
        unavailable["nmr_topology_reason"] = reason
        return unavailable
    try:
        structure_map = NmrStructureMap.from_mol(mol)
    except ValueError as exc:
        unavailable["nmr_topology_reason"] = f"NmrStructureMap build failed: {exc}"
        return unavailable
    return {
        "nmr_topology_source": capture.topology_source,
        "nmr_structure_map": {
            "elements": list(structure_map.elements),
            "source_atom_indices": list(structure_map.source_atom_indices),
            "canonical_ranks": list(structure_map.canonical_ranks),
        },
        "nmr_topology_mol": mol,
        "nmr_topology_reason": None,
    }


def nmr_topology_source_for(structure: Structure) -> str:
    """Return the captured ``nmr_topology_source`` (``xyz_unavailable`` if absent)."""
    value = structure.metadata.get("nmr_topology_source")
    if isinstance(value, str) and value in NMR_TOPOLOGY_SOURCES:
        return value
    return NMR_TOPOLOGY_XYZ_UNAVAILABLE


def nmr_structure_map_for(structure: Structure) -> NmrStructureMap | None:
    """Rebuild the captured :class:`NmrStructureMap` from candidate metadata.

    Returns ``None`` when topology is unavailable (never an element-merged
    stand-in). Raises on a malformed stored payload — provenance is not
    silently dropped.
    """
    raw = structure.metadata.get("nmr_structure_map")
    if raw is None:
        return None
    if isinstance(raw, NmrStructureMap):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"nmr_structure_map metadata must be a dict or NmrStructureMap, "
            f"got {type(raw).__name__}"
        )
    return NmrStructureMap.from_elements(
        [str(element) for element in raw["elements"]],
        source_atom_indices=[int(i) for i in raw["source_atom_indices"]],
        ranks=[int(r) for r in raw["canonical_ranks"]],
    )


def nmr_topology_mol_for(structure: Structure) -> Chem.Mol | None:
    """Return the captured bonded RDKit Mol, or ``None`` when unavailable."""
    mol = structure.metadata.get("nmr_topology_mol")
    return mol if mol is not None else None


def _load_experiment(spectrum_input: str | Path) -> ExperimentalNmr:
    """Read the experimental spectrum from a path or literal text."""
    path = Path(spectrum_input)
    if path.exists() and path.is_file():
        return parse_experimental_nmr(path)
    return parse_experimental_nmr(str(spectrum_input))


def _load_experiment_bruker(
    bruker_input: str | Path,
    references: dict[str, float] | None,
    output_root: Path,
) -> ExperimentalNmr:
    """Stage 0a (P3): process Bruker raw data into an unassigned peak list.

    The auto-picked peaks are also written to ``bruker_peaks.txt`` in the
    output root (DevDoc §6.2 format) so the user can inspect/correct the
    peak picking. nmrglue is an optional dependency — a clear error is
    raised when it is missing.
    """
    from acp.nmr.spectra import bruker_result_to_text, process_bruker_tree

    result = process_bruker_tree(
        bruker_input,
        references=references,
        extract_dir=output_root / "_bruker_extract",
    )
    picked = bruker_result_to_text(result)
    (output_root / "bruker_peaks.txt").write_text(picked, encoding="utf-8")
    logger.info(
        "Bruker processing: %d experiment(s) → %s",
        len(result.spectra),
        {k: len(v) for k, v in result.experiment.peaks.items()},
    )
    return result.experiment


def _enumerate_input(
    input_sources: list[str],
    stereocenters: str | list[str] | None,
    charge: int | None,
    multiplicity: int | None,
) -> str | tuple[list[str], list[Structure], int]:
    """Expand a single candidate into its diastereomers (DevDoc §5 stage 1).

    Returns either an error string (caller surfaces it) or a tuple
    ``(new_sources, new_candidates, charge)``. Enantiomer pairs collapse to
    one representative — DP4 cannot distinguish them, so keeping both would
    waste compute and return degenerate probabilities.
    """
    if len(input_sources) != 1:
        return (
            "--enumerate requires exactly one candidate input "
            f"(got {len(input_sources)}); use explicit multi-candidate input "
            "instead of enumeration."
        )
    source = input_sources[0]
    try:
        isomers = enumerate_candidates(source, stereocenters=stereocenters)
    except Exception as exc:
        logger.exception("Diastereomer enumeration failed: %s", exc)
        return f"diastereomer enumeration: {exc}"
    if len(isomers) <= 1:
        logger.info("Enumeration produced no extra isomers; using input as-is")
        return input_sources, _parse_candidates(input_sources, charge, multiplicity), charge or 0

    new_sources = [c.smiles for c in isomers]
    reader = StructureReader()
    new_candidates: list[Structure] = []
    for idx, iso in enumerate(isomers):
        structure = reader.read(
            iso.smiles,
            charge=charge,
            multiplicity=multiplicity,
            name=iso.label,
        )
        safe = sanitize_job_name(structure.id) or iso.label
        new_candidates.append(
            Structure(
                id=safe,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                symbols=structure.symbols,
                coordinates=structure.coordinates,
                metadata={
                    "source": source,
                    "smiles": iso.smiles,
                    "stereocenters": iso.stereocenters,
                    "enumerated": True,
                    **(structure.metadata or {}),
                },
            )
        )
    logger.info(
        "Enumerated %d diastereomer(s) from %s (enantiomer-deduplicated)",
        len(new_candidates),
        source,
    )
    # charge may be None → normalize to 0 for downstream consistency
    return new_sources, new_candidates, charge if charge is not None else 0


def _resolve_config(
    config: dict[str, Any] | None,
    nmr_method: str | None,
    nmr_basis: str | None,
    solvent: str | None,
    nproc: int | None,
) -> dict[str, Any]:
    """Merge config + explicit overrides (mirrors cli._build_config)."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    if solvent is not None:
        cfg.setdefault("censo", {})["solvent"] = solvent
    if nproc is not None:
        cfg.setdefault("resources", {})["nproc"] = nproc
        cfg.setdefault("executables", {}).setdefault("orca", {})["nproc"] = nproc
    if nmr_method is not None:
        cfg.setdefault("theory", {}).setdefault("nmr", {})["method"] = nmr_method
    if nmr_basis is not None:
        cfg.setdefault("theory", {}).setdefault("nmr", {})["basis"] = nmr_basis
    return cfg


def _build_nmr_config(
    cfg: dict[str, Any],
    nuclei: list[str] | None,
    nmr_method: str | None,
    nmr_basis: str | None,
    solvent: str | None,
    boltzmann_temp: float | None,
    tms_1h: float | None,
    tms_13c: float | None,
    error_model: str | None,
    conformer_preset: str | None,
) -> NmrConfig:
    """Assemble :class:`NmrConfig` from cfg + explicit overrides."""
    theory_nmr = (cfg.get("theory") or {}).get("nmr") or {}
    nmr_section = cfg.get("nmr") or {}
    refs = dict(nmr_section.get("references") or {})

    resolved_solvent = solvent if solvent is not None else theory_nmr.get("solvent")
    resolved_method = nmr_method or theory_nmr.get("method") or "mPW1PW91"
    resolved_basis = nmr_basis or theory_nmr.get("basis") or "6-311G(d)"
    effective_solvent = resolved_solvent if resolved_solvent else "chloroform"

    # TMS references: explicit overrides > user-configured references >
    # solvent-aware Goodman TMSdata table (DevDoc §10.3) > Goodman
    # chloroform defaults. The table is keyed by (method, basis, solvent)
    # with a gas-phase fallback, so switching --solvent keeps σ_TMS at the
    # same level of theory as σ_sample.
    tms_shieldings: dict[str, float] = {}
    if tms_1h is not None:
        tms_shieldings["1H"] = float(tms_1h)
    elif "1H" in refs and refs["1H"] is not None:
        tms_shieldings["1H"] = float(refs["1H"])
    if tms_13c is not None:
        tms_shieldings["13C"] = float(tms_13c)
    elif "13C" in refs and refs["13C"] is not None:
        tms_shieldings["13C"] = float(refs["13C"])
    if "1H" not in tms_shieldings or "13C" not in tms_shieldings:
        from acp.nmr.models import lookup_tms_shieldings

        table_c, table_h = lookup_tms_shieldings(resolved_method, resolved_basis, effective_solvent)
        if "13C" not in tms_shieldings and table_c is not None:
            tms_shieldings["13C"] = float(table_c)
        if "1H" not in tms_shieldings and table_h is not None:
            tms_shieldings["1H"] = float(table_h)

    return NmrConfig(
        nuclei=tuple(nuclei) if nuclei else ("1H", "13C"),
        nmr_method=resolved_method,
        nmr_basis=resolved_basis,
        solvent=effective_solvent,
        solvent_model=theory_nmr.get("solvent_model") or "cpcm",
        tms_shieldings=tms_shieldings
        or {
            "1H": 32.1243166667,  # Goodman TMSdata mPW1PW91/6-311G(d)/chloroform
            "13C": 188.452125,
        },
        boltzmann_temp=float(boltzmann_temp or nmr_section.get("temperature_k") or 298.15),
        energy_window_kcal=float(nmr_section.get("energy_window_kcal") or 3.0),
        max_conformers=int(nmr_section.get("max_conformers") or 10),
        error_model=error_model or "goodman-legacy",
        conformer_preset=conformer_preset or "censo-light",
    )


# ---------------------------------------------------------------------------
# Per-candidate pipeline
# ---------------------------------------------------------------------------


def _run_conformer_generation(
    structure: Structure,
    output_dir: Path,
    nmr_config: NmrConfig,
    cfg: dict[str, Any],
    solvent: str | None,
    nproc: int | None,
    ewin: float | None,
) -> StructureEnsemble | None:
    """CREST→CENSO conformer generation for one candidate (task cores).

    Keeps the ACP orchestration on top of the cccp single-item task cores:
    CREST via ``run_conformer_search`` (configured ``CrestBackend`` instance
    passed through the ``TaskContext.backend`` runtime seam), then CENSO via
    ``run_censo_refine`` for every preset except ``censo-zero``, which skips
    CENSO and passes the CREST ensemble through on its xTB title energies
    (§7).  The legacy ``CensoRunResult`` data contract is reconstructed so
    ensemble building (free energies + Boltzmann weights) is unchanged.
    """
    from acp.workflows.energy_shared import (
        resolve_crest_ewin as _resolve_crest_ewin,
    )
    from acp.workflows.energy_shared import (
        resolve_solvent_config as _resolve_solvent_config,
    )

    work_dir = output_dir / f"{structure.id}" / "conformers"
    work_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_job_name(structure.id) or structure.id
    mol_dir = resolve_task_output_root(work_dir, safe_name)
    stage_storage = TaskStorage(mol_dir)
    crest_dir = stage_storage.stage_dir("02_SEARCH", "CREST")
    crest_dir.mkdir(parents=True, exist_ok=True)

    # Solvent/ewin resolution mirrors the old ensemble-generation rules
    # (including the smd default when a solvent has no model).
    censo_solvent, solvent_model = _resolve_solvent_config(cfg, solvent)
    if censo_solvent and solvent_model == "none":
        solvent_model = "smd"
    safe_nproc: int | None = nproc if (nproc is not None and nproc > 0) else None
    crest_ewin = _resolve_crest_ewin(cfg, ewin)

    input_xyz = _structure_to_xyz(structure, work_dir)

    try:
        crest_result_ensemble = _run_conformer_tasks(
            structure,
            nmr_config,
            cfg,
            input_xyz,
            safe_name,
            crest_dir,
            stage_storage,
            censo_solvent,
            solvent_model,
            safe_nproc,
            crest_ewin,
        )
    except Exception as exc:
        logger.exception("Conformer generation failed for %s: %s", structure.id, exc)
        return None
    if crest_result_ensemble is None or not crest_result_ensemble.records:
        logger.error("Conformer generation failed for %s: no conformers produced", structure.id)
        return None
    return crest_result_ensemble


def _run_conformer_tasks(
    structure: Structure,
    nmr_config: NmrConfig,
    cfg: dict[str, Any],
    input_xyz: str,
    safe_name: str,
    crest_dir: Path,
    stage_storage: TaskStorage,
    censo_solvent: str | None,
    solvent_model: str,
    safe_nproc: int | None,
    crest_ewin: float,
) -> StructureEnsemble | None:
    """CREST task + CENSO/xtb-passthrough task → ensemble (see _run_conformer_generation)."""
    from acp.workflows.ensemble import _build_ensemble_from_censo

    # CREST: configured instance via the sanctioned runtime seam (the task
    # core builds its backend without constructor kwargs).
    crest_cfg = cfg.get("executables", {}).get("crest", {})
    crest = CrestBackend(
        config=cfg,
        gfn_level=crest_cfg.get("gfn_level", 2),
        solvent=censo_solvent,
        solvent_model=solvent_model,
    )
    search_result = run_conformer_search(
        TaskRequest(
            task=TaskKind.CONFORMER_SEARCH,
            structure=StructureInput(path=Path(input_xyz)),
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            options=ConformerSearchOptions(energy_window=crest_ewin),
            output_dir=crest_dir,
        ),
        context=TaskContext(
            backend=crest,
            config=cfg,
            capability_extras={"output_name": safe_name},
        ),
    )
    search_payload = search_result.payload
    if (
        not isinstance(search_payload, ConformerSearchPayload)
        or search_payload.ensemble_ref is None
    ):
        logger.error(
            "Conformer generation failed for %s: %s",
            structure.id,
            "; ".join(search_result.errors) or "no CREST ensemble produced",
        )
        return None
    if search_result.status != "completed":
        logger.warning(
            "CREST conformer search partial for %s: %s",
            structure.id,
            "; ".join(search_result.errors) or search_result.status,
        )
    ensemble_xyz = Path(search_payload.ensemble_ref.path)

    preset = nmr_config.conformer_preset or ""
    if preset.lower() == "censo-zero":
        # §7: no CENSO call — CREST ensemble sorted by xTB title energies.
        logger.info("censo-zero: CREST xTB passthrough (no CENSO call)")
        censo_result = _xtb_passthrough_from_search(search_payload, ensemble_xyz, cfg)
    else:
        censo_dir = stage_storage.stage_dir("02_SEARCH", "CENSO")
        censo_dir.mkdir(parents=True, exist_ok=True)
        refine_result = run_censo_refine(
            TaskRequest(
                task=TaskKind.CENSO_REFINE,
                structure=StructureInput(path=ensemble_xyz),
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                options=CensoRefineOptions(preset=preset or None),
                resources=TaskResources(nproc=safe_nproc),
                output_dir=censo_dir,
            ),
            context=TaskContext(
                config=cfg,
                capability_extras={"solvent": censo_solvent, "solvent_model": solvent_model},
            ),
        )
        censo_result = _censo_result_from_refine(refine_result, censo_dir, preset, cfg)
        if censo_result is None:
            logger.error(
                "Conformer generation failed for %s: %s",
                structure.id,
                "; ".join(refine_result.errors) or "CENSO refine produced no records",
            )
            return None

    return _build_ensemble_from_censo(censo_result, structure)


def _xtb_passthrough_from_search(
    payload: ConformerSearchPayload,
    ensemble_xyz: Path,
    cfg: dict[str, Any],
) -> CensoRunResult:
    """censo-zero rule: CREST ensemble on its xTB title energies (§7).

    Replicates the legacy ``xtb_passthrough_result`` record semantics from
    the ``run_conformer_search`` result (ensemble artifact + energy table):
    ``gtot`` equals the xTB electronic energy, ``gsolv``/``grrho`` are zero,
    and missing title energies fall back to 0.0.
    """
    from cccp.utils.file_io import read_xyz_multiframe

    all_coords, symbols = read_xyz_multiframe(ensemble_xyz)
    n_atoms = len(symbols)
    if n_atoms == 0:
        raise ValueError(f"No atoms found in ensemble: {ensemble_xyz}")
    n_frames = len(all_coords) // n_atoms
    energy_by_index = {int(row.frame_index): row.energy_hartree for row in payload.energy_table}

    records: list[CensoConformerRecord] = []
    for index in range(n_frames):
        raw_energy = energy_by_index.get(index)
        energy = float(raw_energy) if raw_energy is not None else 0.0
        start = index * n_atoms
        records.append(
            CensoConformerRecord(
                conf_id=f"CONF{index + 1}",
                frame_index=index,
                energy=energy,
                gsolv=0.0,
                grrho=0.0,
                gtot=energy,
                coordinates=np.array(all_coords[start : start + n_atoms], dtype=float),
                symbols=list(symbols),
            )
        )

    temperature = float(cfg.get("censo", {}).get("temperature", 298.15))
    result = CensoRunResult(
        preset="censo-zero",
        records=records,
        final_part="crest_passthrough",
        work_dir=ensemble_xyz.parent,
        temperature=temperature,
    )
    result.sort_by_gtot()
    return result


def _censo_result_from_refine(
    refine_result: TaskResult,
    run_dir: Path,
    preset: str,
    cfg: dict[str, Any],
) -> CensoRunResult | None:
    """Reconstruct the legacy ``CensoRunResult`` from a ``censo_refine`` result.

    The task payload carries only the summary rows; the full record data
    contract (gsolv/grrho/coordinates) is recovered from the CENSO
    final-part JSON/XYZ under the run dir (``<idx>_<FINAL_PART>`` naming).
    """
    metadata = refine_result.metadata or {}
    final_part = str(metadata.get("final_part") or "")
    if not final_part:
        return None
    part_idx = part_index(final_part)
    json_path = run_dir / f"{part_idx}_{final_part.upper()}.json"
    xyz_path = run_dir / f"{part_idx}_{final_part.upper()}.xyz"
    records = CensoInterface({}).parse_censo_json(json_path, xyz_path)
    if not records:
        return None
    temperature = float(metadata.get("temperature_k") or 0.0) or float(
        cfg.get("censo", {}).get("temperature", 298.15)
    )
    result = CensoRunResult(
        preset=str(metadata.get("preset") or preset or "censo-light"),
        records=records,
        final_part=final_part,
        work_dir=run_dir,
        temperature=temperature,
    )
    result.sort_by_gtot()
    return result


def _structure_to_xyz(structure: Structure, work_dir: Path) -> str:
    """Persist a single structure to an XYZ file and return the path."""
    from cccp.utils.file_io import write_xyz

    work_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = work_dir / "input.xyz"
    coords: NDArray[np.float64] = (
        np.asarray(structure.coordinates, dtype=np.float64)
        if structure.coordinates is not None
        else np.zeros((0, 3), dtype=np.float64)
    )
    write_xyz(xyz_path, coords, list(structure.symbols), title=structure.id)
    return str(xyz_path)


def _select_conformers(
    ensemble: StructureEnsemble,
    nmr_config: NmrConfig,
) -> list[tuple[Structure, float, float]]:
    """Select conformers within the energy window, return (structure, weight, ΔG)."""
    records = list(ensemble.records)
    if not records:
        return []

    # prefer free_energy_hartree; fall back to energy_hartree
    def _g(rec: Any) -> float | None:
        return (
            rec.free_energy_hartree if rec.free_energy_hartree is not None else rec.energy_hartree
        )

    valid: list[tuple[Any, float]] = []
    for record in records:
        energy = _g(record)
        if energy is not None:
            valid.append((record, energy))
    if not valid:
        logger.warning("No energies on ensemble records; using raw records without window")
        valid = [(r, 0.0) for r in records]
    valid.sort(key=lambda x: x[1])
    min_g = valid[0][1]
    # energy-window cutoff
    from cccp.utils.constants import HARTREE_TO_KCAL

    window = nmr_config.energy_window_kcal / HARTREE_TO_KCAL
    selected = [(r, g) for r, g in valid if (g - min_g) <= window]
    if len(selected) > nmr_config.max_conformers:
        selected = selected[: nmr_config.max_conformers]
    # Boltzmann weights. RT in Hartree = R[kcal/(mol·K)] · T[K] / HARTREE_TO_KCAL.
    # (Parity audit 2026-08-07: the previous ``/ 1000.0`` was a unit-confusion
    # bug — it divided by 1000 instead of 627.509, making the weights 1.59×
    # too sharp and over-weighting the global minimum.)
    deltas = [g - min_g for _, g in selected]
    kt = 0.001987204259 * nmr_config.boltzmann_temp / HARTREE_TO_KCAL
    if kt <= 0:
        weights = [1.0 / len(selected)] * len(selected)
    else:
        import math

        exps = [math.exp(-d / kt) for d in deltas]
        total = sum(exps)
        weights = [e / total for e in exps] if total > 0 else [1.0 / len(selected)] * len(selected)
    return [(r.structure, w, d) for (r, _), w, d in zip(selected, weights, deltas)]


def _run_giao_for_conformers(
    conformers: list[tuple[Structure, float, float]],
    nmr_config: NmrConfig,
    giao_dir: Path,
    cfg: dict[str, Any],
    solvent: str | None,
) -> list[ConformerShielding]:
    """Run ORCA GIAO NMR for each conformer and parse shieldings.

    *giao_dir* is the final per-candidate GIAO root (v2 layout:
    ``WORK/05_SP/ORCA/<candidate_id>``); per-conformer outputs land in
    ``conf_<idx>`` subdirectories beneath it.
    """
    nmr_nuclei = [n.split(maxsplit=1)[-1] if n[0].isdigit() else n for n in nmr_config.nuclei]
    # deduplicate elements
    seen: set[str] = set()
    target_elements: list[str] = []
    for n in nmr_nuclei:
        if n not in seen:
            seen.add(n)
            target_elements.append(n)

    giao_dir.mkdir(parents=True, exist_ok=True)

    results: list[ConformerShielding] = []
    for idx, (structure, weight, delta) in enumerate(conformers):
        coords: NDArray[np.float64] = (
            np.asarray(structure.coordinates, dtype=np.float64)
            if structure.coordinates is not None
            else np.zeros((0, 3), dtype=np.float64)
        )
        out_dir = giao_dir / f"conf_{idx:03d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            task_result = run_nmr_shielding(
                TaskRequest(
                    task=TaskKind.NMR_SHIELDING,
                    structure=StructureInput(
                        coordinates=tuple(tuple(float(c) for c in row) for row in coords),
                        symbols=tuple(str(s) for s in structure.symbols),
                    ),
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    level=MethodSpec(
                        method=nmr_config.nmr_method,
                        basis=nmr_config.nmr_basis,
                        solvent=nmr_config.solvent,
                        solvent_model=nmr_config.solvent_model,
                    ),
                    options=NmrShieldingOptions(),
                    output_dir=out_dir,
                ),
                context=TaskContext(
                    config=cfg,
                    capability_extras={"nuclei": list(target_elements)},
                ),
            )
        except Exception as exc:
            logger.exception("GIAO NMR failed for conformer %d of %s: %s", idx, structure.id, exc)
            continue
        payload = task_result.payload
        shieldings_raw = (
            dict(payload.shieldings) if isinstance(payload, NmrShieldingPayload) else {}
        )
        if task_result.status != "completed":
            logger.error(
                "GIAO NMR did not converge for conformer %d of %s: %s",
                idx,
                structure.id,
                "; ".join(task_result.errors) or "unknown",
            )
            continue
        if not shieldings_raw:
            logger.warning("No shieldings parsed for conformer %d of %s", idx, structure.id)
            continue
        shieldings: dict[int, dict[str, object]] = {
            int(atom_index): {"symbol": entry.symbol, "isotropic": float(entry.isotropic)}
            for atom_index, entry in shieldings_raw.items()
        }
        log_file = next(
            (
                Path(artifact.path)
                for artifact in task_result.artifacts
                if artifact.type == "log" and artifact.path
            ),
            None,
        )
        results.append(
            ConformerShielding(
                conformer_id=f"conf_{idx:03d}",
                boltzmann_weight=float(weight),
                shieldings=shieldings,
                log_file=log_file,
                coordinates=structure.coordinates,
                symbols=list(structure.symbols),
            )
        )
    _ = solvent  # provenance only — solvent is applied via nmr_config
    return results


def _analyze_candidate(
    index: int,
    structure: Structure,
    conformer_shieldings: list[ConformerShielding],
    experiment: ExperimentalNmr,
    nmr_config: NmrConfig,
) -> CandidateResult:
    """Stages 4–7: average, match, scale, collect residuals for probability."""
    symbols = list(structure.symbols)
    omit_indices = _omit_atom_indices(experiment, symbols)

    # DevDoc §5 stage 5: assigned spectra use ONLY explicit EQ groups (the
    # user has already labeled every atom of interest); auto-detection
    # would wrongly collapse distinct assigned atoms. Unassigned spectra
    # rely on detection (no labels to disambiguate).
    if experiment.assigned:
        equivalence_groups = _explicit_eq_to_indices(experiment, symbols)
    else:
        # Pass a bonded RDKit Mol (from the candidate's SMILES source) so
        # equivalence detection uses true topology, not the element-only
        # fallback. Parity audit 2026-08-07: without connectivity, all C
        # atoms collapse into one group — wrong for any multi-carbon mol.
        mol = _try_build_rdkit_mol(structure)
        detected_groups = detect_equivalence_groups(symbols, mol=mol)
        equivalence_groups = merge_explicit_and_detected(
            experiment.equivalence_groups, detected_groups, symbols
        )

    atom_shifts = boltzmann_average_shieldings(
        conformer_shieldings,
        symbols,
        nmr_config,
        equivalence_groups=equivalence_groups,
        omit_atom_indices=omit_indices,
    )

    assign_result = match_assigned(atom_shifts, experiment)
    pairs = assign_result.pairs
    for entry in assign_result.unmatched:
        logger.warning(
            "Unmatched experimental peak %s[%s] %.4f ppm (%s)%s",
            entry.element,
            entry.index,
            entry.shift_ppm,
            entry.reason,
            f" label={entry.atom_label}" if entry.atom_label else "",
        )
    logger.debug(
        "candidate %s assignment: %d locked, %d matched, %d unmatched, near-optimal deltas=%s",
        index,
        sum(len(g) for g in assign_result.locked.values()),
        sum(len(g) for g in pairs.values()),
        len(assign_result.unmatched),
        [round(alt.delta, 6) for alt in assign_result.near_optimal],
    )
    residual_inputs = collect_residual_inputs(pairs)

    regressions: dict[str, Any] = {}
    residual_by_nucleus: dict[str, list[float]] = {}
    assignments = []
    for nucleus, arrays in residual_inputs.items():
        # Goodman internal-scaling convention (calc-on-exp regression,
        # DP4.py:151) — the trained σ values assume this regression
        # direction; using exp-on-calc would invalidate them.
        reg, scaled, residuals = fit_scaling_goodman(arrays["calc"], arrays["exp"], nucleus)
        regressions[nucleus] = reg
        residual_by_nucleus[nucleus] = residuals
        assignments.extend(
            build_assignments(
                arrays["labels"],
                arrays["elements"],
                arrays["exp"],
                arrays["calc"],
                scaled,
                residuals,
            )
        )

    return CandidateResult(
        index=index,
        label=structure.id,
        atom_shifts=atom_shifts,
        assignments=assignments,
        regressions=regressions,
        conformer_shieldings=conformer_shieldings,
        # probabilities set by the orchestrator (need all candidates first)
        dp4_probability=0.0,
        dp5_probability=0.0,
    )


def _omit_atom_indices(experiment: ExperimentalNmr, symbols: list[str]) -> list[int]:
    """Resolve OMIT labels to 0-based atom indices."""
    if not experiment.omit_atoms:
        return []
    from acp.nmr.equivalence import _build_label_index

    label_to_idx = _build_label_index(symbols)
    return [label_to_idx[label] for label in experiment.omit_atoms if label in label_to_idx]


def _explicit_eq_to_indices(
    experiment: ExperimentalNmr,
    symbols: list[str],
) -> list[list[int]]:
    """Convert explicit ``EQ:`` labels to 0-based index groups.

    Returns an empty list when no explicit groups are present (each atom
    is then its own singleton — the desired behavior for fully-assigned
    spectra where every atom is distinct).
    """
    if not experiment.equivalence_groups:
        return []
    from acp.nmr.equivalence import _build_label_index

    label_to_idx = _build_label_index(symbols)
    groups: list[list[int]] = []
    for group in experiment.equivalence_groups:
        idxs = [label_to_idx[label] for label in group if label in label_to_idx]
        if len(idxs) > 1:
            groups.append(idxs)
    return groups


def _try_build_rdkit_mol(structure: Structure):
    """Build a bonded RDKit Mol for symmetry equivalence detection.

    Prefers the candidate's SMILES source (stored in metadata by
    :func:`_parse_candidates`); falls back to ``None`` when only XYZ is
    available (the element-only equivalence fallback then applies).
    """
    source = structure.metadata.get("source", "")
    if not isinstance(source, str):
        return None
    if not source or _looks_like_smiles(source):
        try:
            from rdkit import Chem

            mol = Chem.MolFromSmiles(source) if source else None
            if mol is not None:
                mol = Chem.AddHs(mol)
                Chem.SanitizeMol(mol)
                # Only use the Mol when its atom count matches the structure —
                # otherwise the symmetry ranks would index out of range. This
                # guards against test mocks and SMILES↔XYZ mismatches.
                if mol.GetNumAtoms() == len(structure.symbols):
                    return mol
        except Exception as exc:  # pragma: no cover - rdkit edge cases
            logger.debug("RDKit Mol build failed for '%s': %s", source, exc)
    return None


def _looks_like_smiles(source: str) -> bool:
    """Heuristic: SMILES contains bond/atom symbols but no whitespace."""
    s = source.strip()
    if not s or "\n" in s or " " in s:
        return False
    return any(c in s for c in "CcNnOoPpSsFf=[#]()-/\\123456789")


def _compute_candidate_dp5(
    candidate: CandidateResult,
    structure: Structure,
    nmr_config: NmrConfig,
    dp5_model: Any,
) -> float:
    """DP5 for one candidate via the Goodman-faithful per-conformer path.

    Goodman evaluates the KDE per conformer then averages probabilities
    (DP5.py:339-353), which differs from evaluating once on the averaged
    shielding because the KDE is nonlinear. This helper reconstructs the
    per-conformer ¹³C calc shifts aligned with the matched exp shifts,
    then calls :meth:`GoodmanDP5Model.probability_per_conformer`.

    When ``qml`` + the FCHL assets are available (DevDoc appendix D, P4),
    the per-atom probabilities use the FCHL-similarity weighted KDE
    (:meth:`GoodmanDP5Model.probability_per_conformer_fchl`) built from the
    conformer geometries threaded through :class:`ConformerShielding`.
    Otherwise the unweighted-KDE fallback is used and ``dp5_mode`` stays
    ``"fallback"``.

    Falls back to the averaged-residual path when per-conformer shieldings
    are unavailable (e.g. the test-only ``skip_conformers`` fast path that
    injects a single pre-averaged shielding set).
    """
    from acp.nmr.equivalence import _build_label_index
    from acp.nmr.fchl import FRAG_ATOM_THRESHOLD, build_atom_representations

    symbols = list(structure.symbols)
    label_to_idx = _build_label_index(symbols)
    tms_c = nmr_config.tms_for("13C")

    # 13C assignments give us the matched (atom_label, exp_ppm) pairs
    c_assignments = [a for a in candidate.assignments if a.element.upper() == "C"]
    if not c_assignments:
        return 0.0

    exp_c = [a.exp_ppm for a in c_assignments]
    maybe_indices = [label_to_idx.get(a.atom_label) for a in c_assignments]
    if any(idx is None for idx in maybe_indices):
        # label mismatch — fall back to averaged-residual path
        residual_by_nuc = {"13C": [a.residual for a in c_assignments]}
        return compute_dp5_goodman(residual_by_nuc, dp5_model)
    c_indices = [idx for idx in maybe_indices if idx is not None]

    # per-conformer ¹³C calc shifts (TMS-converted)
    conformer_shifts: list[list[float]] = []
    weights: list[float] = []
    conformer_reps: list[list[NDArray[np.float64]]] = []
    # FCHL atomic path is only valid for molecules < 86 atoms (DP5.py:57);
    # larger molecules need the openbabel fragmentation + frag_reps path
    # (not yet wired → degrade to fallback for those rare cases).
    fchl_ok = bool(getattr(dp5_model, "fchl_available", False))
    fchl_ok = fchl_ok and len(symbols) < FRAG_ATOM_THRESHOLD
    for conf in candidate.conformer_shieldings:
        shifts: list[float] = []
        ok = True
        for idx in c_indices:
            sh = conf.shieldings.get(idx)
            isotropic = sh.get("isotropic") if sh else None
            if not isinstance(isotropic, (int, float, str)):
                ok = False
                break
            iso = float(isotropic)
            # Goodman TMS formula (NMR.py:392): δ = (σ_TMS − σ) / (1 − σ_TMS/10⁶)
            shifts.append((tms_c - iso) / (1.0 - tms_c / 1e6) if tms_c is not None else iso)
        if ok:
            conformer_shifts.append(shifts)
            weights.append(conf.boltzmann_weight)
            if fchl_ok:
                coords = conf.coordinates
                conf_symbols = conf.symbols
                if not isinstance(coords, np.ndarray) or not conf_symbols:
                    fchl_ok = False
                    conformer_reps = []
                else:
                    try:
                        reps = build_atom_representations(
                            coords,
                            conf_symbols,
                            c_indices,
                        )
                    except Exception as exc:  # pragma: no cover - qml/kernel edge
                        logger.warning(
                            "FCHL representation build failed for %s conformer %s: %s",
                            structure.id,
                            conf.conformer_id,
                            exc,
                        )
                        fchl_ok = False
                        conformer_reps = []
                    else:
                        conformer_reps.append(reps)

    if len(conformer_shifts) <= 1:
        # single conformer or unavailable — averaged path
        residual_by_nuc = {"13C": [a.residual for a in c_assignments]}
        return compute_dp5_goodman(residual_by_nuc, dp5_model)

    # normalize weights (guard against drift)
    total_w = sum(weights)
    if total_w <= 0:
        weights = [1.0 / len(weights)] * len(weights)
    else:
        weights = [w / total_w for w in weights]

    if fchl_ok and len(conformer_reps) == len(conformer_shifts):
        dp5_model.dp5_mode = "fchl"
        return dp5_model.probability_per_conformer_fchl(
            conformer_shifts, exp_c, weights, conformer_reps
        )

    dp5_model.dp5_mode = "fallback"
    return dp5_model.probability_per_conformer(conformer_shifts, exp_c, weights)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def run_nmr_analysis(
    input_sources: list[str],
    spectrum: str | Path | None = None,
    output_dir: str | Path = "./nmr_output",
    config: dict[str, Any] | None = None,
    nuclei: list[str] | None = None,
    nmr_method: str | None = None,
    nmr_basis: str | None = None,
    solvent: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    strict_topology: bool = False,
    nproc: int | None = None,
    boltzmann_temp: float | None = None,
    tms_1h: float | None = None,
    tms_13c: float | None = None,
    error_model: str | None = None,
    conformer_preset: str | None = None,
    ewin: float | None = None,
    enumerate_stereoisomers: bool = False,
    stereocenters: str | list[str] | None = None,
    skip_conformers: bool = False,
    prebuilt_ensembles: list[StructureEnsemble] | None = None,
    bruker: str | Path | None = None,
    bruker_references: dict[str, float] | None = None,
    progress_reporter: ProgressReporter | None = None,
) -> WorkflowResult:
    """Run the full NMR + DP4/DP5 workflow.

    Args:
        input_sources: Candidate inputs (SMILES or XYZ paths), one per candidate.
        spectrum: Path to a §6.2 experimental-spectrum text file, or the
            literal text. Mutually exclusive with *bruker*.
        bruker: P3 — Bruker raw data directory (§6.3 layout) or a ``.zip``
            archive of one. Processed via stage 0a (FT/phase/baseline/peak
            picking) into an unassigned peak list. Requires nmrglue.
        bruker_references: Optional manual ppm references per nucleus
            (``{"1H": 7.26}``) used to calibrate the picked peaks.
        output_dir: Output root.
        config: Optional merged config dict.
        nuclei: Target nuclei (default ``["1H", "13C"]``).
        nmr_method / nmr_basis: Override the GIAO DFT level (default
            ``mPW1PW91/6-311G(d)`` — must match the error model).
        solvent: Solvent name (applied to both conformer gen and GIAO NMR).
        charge / multiplicity: Per-candidate overrides.
        strict_topology: When ``True``, stage-0 parsing raises
            :class:`TopologyUnavailableError` (surfaced as a failed result)
            if any candidate has no bonded molecular graph — e.g. an XYZ
            input without an explicit charge. Defaults to ``False``;
            ``NmrConfig.strict_equivalence`` wires here later (todo 4).
        nproc: CPU core override.
        boltzmann_temp: Boltzmann-weight temperature (K).
        tms_1h / tms_13c: Override TMS reference shieldings.
        error_model: Error-model id (default ``goodman-legacy``).
        conformer_preset: CENSO preset (default ``censo-light``).
        ewin: CREST energy window (kcal/mol).
        enumerate_stereoisomers: When ``True`` and exactly one candidate is
            supplied, expand it into all distinct diastereomers (enantiomer
            pairs collapse to one representative — DP4 cannot distinguish
            them). Requires bond-bearing input (SMILES/SDF/MOL).
        stereocenters: Optional atom-label whitelist (``"C5,C8"``)
            restricting enumeration to those centres.
        skip_conformers: When ``True``, skip stages 2–3 (useful for tests
            with ``prebuilt_ensembles``).
        prebuilt_ensembles: Pre-computed ensembles (one per candidate) —
            bypasses stage 2. Used by tests and the ``--resume`` path.

    Returns:
        :class:`WorkflowResult` with ``metadata`` carrying report paths.
    """
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    if progress_reporter is not None:
        progress_reporter.initialize()
        progress_reporter.start_stage("embed_smiles")

    # v2 task-storage layout (design doc §5/§13): WORK/ for engine work, RESULT/ for products.
    storage = TaskStorage(output_root)
    storage.ensure_layout(stages=["02_SEARCH", "05_SP"], categories=["reports", "structures"])

    stages_completed: list[str] = []

    # Stage 0: input parsing (stage 0a = Bruker raw processing, P3)
    if (spectrum is None) == (bruker is None):
        error = "exactly one of spectrum / bruker input is required"
        _fail_progress(progress_reporter, error)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=error,
        )
    try:
        candidates = _parse_candidates(
            input_sources, charge, multiplicity, strict_topology=strict_topology
        )
        if bruker is not None:
            experiment = _load_experiment_bruker(bruker, bruker_references, output_root)
        else:
            if spectrum is None:
                error = "spectrum input is required when bruker is not supplied"
                _fail_progress(progress_reporter, error)
                return WorkflowResult(
                    status="failed",
                    stages_completed=stages_completed,
                    error=error,
                )
            experiment = _load_experiment(spectrum)
    except Exception as exc:
        logger.exception("Input parsing failed: %s", exc)
        error = f"input parsing: {exc}"
        _fail_progress(progress_reporter, error)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=error,
        )
    if not candidates:
        error = "no candidate structures parsed"
        _fail_progress(progress_reporter, error)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=error,
        )

    # Stage 1 (optional): diastereomer enumeration (DevDoc §5, P2)
    if enumerate_stereoisomers:
        enum_result = _enumerate_input(input_sources, stereocenters, charge, multiplicity)
        if isinstance(enum_result, str):
            _fail_progress(progress_reporter, enum_result)
            return WorkflowResult(
                status="failed",
                stages_completed=[],
                error=enum_result,
            )
        input_sources, candidates, charge = enum_result
    stages_completed.append("input_parsing")
    if progress_reporter is not None:
        progress_reporter.complete_stage("embed_smiles")
        progress_reporter.start_stage("crest_search")

    cfg = _resolve_config(config, nmr_method, nmr_basis, solvent, nproc)
    nmr_config = _build_nmr_config(
        cfg,
        nuclei=nuclei,
        nmr_method=nmr_method,
        nmr_basis=nmr_basis,
        solvent=solvent,
        boltzmann_temp=boltzmann_temp,
        tms_1h=tms_1h,
        tms_13c=tms_13c,
        error_model=error_model,
        conformer_preset=conformer_preset,
    )

    # validate error-model ↔ NMR-level binding (DevDoc §10.2)
    try:
        validate_error_model_binding(nmr_config)
    except ValueError as exc:
        _fail_progress(progress_reporter, str(exc))
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error=str(exc),
        )

    try:
        em = load_error_model(nmr_config.error_model)
    except ValueError as exc:
        _fail_progress(progress_reporter, str(exc))
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error=str(exc),
        )
    actual_error_model = em.model_id

    # Stages 2–3: conformer generation + GIAO NMR per candidate
    candidate_results: list[CandidateResult] = []
    ensembles: list[StructureEnsemble | None]
    if prebuilt_ensembles is None:
        ensembles = [None] * len(candidates)
    else:
        ensembles = list(prebuilt_ensembles)
    if len(ensembles) != len(candidates):
        error = "prebuilt_ensembles length != input_sources length"
        _fail_progress(progress_reporter, error)
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error=error,
        )

    generated_ensembles: list[bool] = []
    resolved_ensembles: list[StructureEnsemble] = []
    needs_generation = any(ensemble is None for ensemble in ensembles)
    for idx, structure in enumerate(candidates):
        source_ensemble = ensembles[idx]
        if source_ensemble is not None:
            resolved_ensembles.append(source_ensemble)
            generated_ensembles.append(False)
            continue
        ensemble = _run_conformer_generation(
            structure, storage.stage_dir("02_SEARCH"), nmr_config, cfg, solvent, nproc, ewin
        )
        if ensemble is None:
            error = f"conformer generation failed for {structure.id}"
            _fail_progress(progress_reporter, error)
            return WorkflowResult(
                status="failed",
                stages_completed=stages_completed,
                error=error,
            )
        resolved_ensembles.append(ensemble)
        generated_ensembles.append(True)
        # Publish successful structure generation before GIAO can fail.
        from acp.results.frame_candidate_store import atomic_write_text
        from acp.results.structure_policy import single_geometry
        try:
            generated_manifest = ResultManifest.read(storage.result_dir())
        except FileNotFoundError:
            generated_manifest = ResultManifest(workflow="nmr", status="running")
        for rank, (generated, _weight, _energy) in enumerate(_select_conformers(ensemble, nmr_config), 1):
            lines = [str(len(generated.symbols)), f"NMR generated conformer rank={rank}"]
            if generated.coordinates is None:
                continue
            lines.extend(f"{symbol} {float(row[0]):.10f} {float(row[1]):.10f} {float(row[2]):.10f}"
                         for symbol, row in zip(generated.symbols, generated.coordinates))
            xyz = "\n".join(lines) + "\n"
            if single_geometry(xyz) is None:
                continue
            rel = f"structures/nmr_{idx}_{rank}.xyz"
            atomic_write_text(storage.result_dir() / rel, xyz)
            generated_manifest.add_product(f"nmr_conformer_{idx}_{rank}",
                f"NMR conformer {idx + 1}/{rank}", rel, "structure",
                metadata={"source_kind":"conformer", "rank":rank, "stage_id":"conformer_generation", "policy_version":1})
        generated_manifest.write(storage.result_dir())


    if progress_reporter is not None:
        skipped = {"status": "skipped"} if not needs_generation else None
        progress_reporter.complete_stage("crest_search", skipped)
        progress_reporter.start_stage("censo_prescreening")
        progress_reporter.complete_stage("censo_prescreening", skipped)
        progress_reporter.start_stage("censo_screening")
        progress_reporter.complete_stage("censo_screening", skipped)
        progress_reporter.start_stage("ensemble_export")
        progress_reporter.complete_stage("ensemble_export", skipped)
        progress_reporter.start_stage("giao_nmr")

    conformer_shieldings_by_candidate: list[list[ConformerShielding]] = []
    for idx, structure in enumerate(candidates):
        giao_dir = storage.stage_dir("05_SP", "ORCA") / structure.id
        ensemble = resolved_ensembles[idx]
        if not generated_ensembles[idx] and skip_conformers:
            # test path: shieldings already attached — skip GIAO entirely
            conformer_shieldings = [
                item for item in ensemble.data if isinstance(item, ConformerShielding)
            ]
        else:
            conformer_shieldings = _run_giao_for_conformers(
                _select_conformers(ensemble, nmr_config),
                nmr_config,
                giao_dir,
                cfg,
                solvent,
            )

        if not conformer_shieldings:
            error = f"no GIAO shieldings for {structure.id}"
            _fail_progress(progress_reporter, error)
            return WorkflowResult(
                status="failed",
                stages_completed=stages_completed,
                error=error,
            )
        conformer_shieldings_by_candidate.append(conformer_shieldings)

    if progress_reporter is not None:
        progress_reporter.complete_stage("giao_nmr")
        progress_reporter.start_stage("boltzmann_average")
    for idx, (structure, conformer_shieldings) in enumerate(
        zip(candidates, conformer_shieldings_by_candidate, strict=True)
    ):
        candidate_results.append(
            _analyze_candidate(idx, structure, conformer_shieldings, experiment, nmr_config)
        )

    stages_completed.append("giao_shielding")
    stages_completed.append("averaging_matching_scaling")
    if progress_reporter is not None:
        progress_reporter.complete_stage("boltzmann_average")
        progress_reporter.start_stage("dp4_dp5_probability")

    # Stage 7: DP4 / DP5
    log_likelihoods = [
        compute_dp4(
            {
                nuc: [a.residual for a in cr.assignments if _nucleus_of_element(a.element) == nuc]
                for nuc in nmr_config.nuclei
            },
            em,
        )
        for cr in candidate_results
    ]
    dp4_probs = normalize_dp4(log_likelihoods)

    # DP5: prefer the real Goodman KDE model when its assets are present;
    # fall back to the placeholder sigmoid otherwise.
    dp5_model = None
    if not nmr_config.error_model.startswith("placeholder") and dp5_model_available():
        try:
            dp5_model = load_dp5_model()
        except Exception as exc:  # pragma: no cover - asset-load robustness
            logger.warning("Goodman DP5 model load failed (%s); using placeholder", exc)

    for cr, p4 in zip(candidate_results, dp4_probs):
        residual_by_nuc = {
            nuc: [a.residual for a in cr.assignments if _nucleus_of_element(a.element) == nuc]
            for nuc in nmr_config.nuclei
        }
        cr.dp4_probability = float(p4)
        if dp5_model is not None:
            cr.dp5_probability = _compute_candidate_dp5(
                cr, candidates[cr.index], nmr_config, dp5_model
            )
        else:
            cr.dp5_probability = float(dp5_log_to_probability(compute_dp5(residual_by_nuc, em)))
    stages_completed.append("probability")
    if progress_reporter is not None:
        progress_reporter.complete_stage("dp4_dp5_probability")
        progress_reporter.start_stage("nmr_report")

    # Stage 8: report
    dp5_mode = getattr(dp5_model, "dp5_mode", "fallback") if dp5_model is not None else "fallback"
    fchl_kernel = getattr(dp5_model, "fchl_kernel", "") if dp5_model is not None else ""
    report = NmrReport(
        candidates=candidate_results,
        config=nmr_config,
        error_model=actual_error_model,
        dp5_mode=dp5_mode,
        metadata={"n_candidates": len(candidate_results), "fchl_kernel": fchl_kernel},
    )
    reports_dir = storage.result_category_dir("reports")
    paths = write_all_reports(report, reports_dir)
    stages_completed.append("report")

    # write a small machine-readable summary next to the report
    summary = {
        "status": "completed",
        "n_candidates": len(candidate_results),
        "winner": (
            {
                "index": report.winner.index,
                "label": report.winner.label,
                "dp4": report.winner.dp4_probability,
                "dp5": report.winner.dp5_probability,
            }
            if report.winner is not None
            else None
        ),
        "error_model": actual_error_model,
        "dp5_mode": report.dp5_mode,
        "fchl_kernel": fchl_kernel,
        "stages": stages_completed,
        "outputs": {
            "json": str(paths["json"]),
            "xlsx": str(paths["xlsx"]) if paths["xlsx"] else None,
            "plots": [str(p) for p in paths["plots"]],
        },
    }
    (reports_dir / "nmr_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    products: list[dict[str, Any]] = [
        {
            "label": "NMR report (JSON)",
            "path": f"RESULT/reports/{paths['json'].name}",
            "kind": "report",
        }
    ]
    if paths["xlsx"]:
        products.append(
            {
                "label": "NMR assignment (XLSX)",
                "path": f"RESULT/reports/{paths['xlsx'].name}",
                "kind": "table",
            }
        )
    for i, plot in enumerate(paths["plots"], start=1):
        try:
            products.append(
                {
                    "label": f"Plot {i}",
                    "path": f"RESULT/reports/{plot.name}",
                    "kind": "plot",
                }
            )
        except ValueError:
            continue
    write_result_summary(output_root, workflow="nmr", products=products)

    try:
        manifest = ResultManifest.read(storage.result_dir())
    except FileNotFoundError:
        manifest = ResultManifest(task_id="", workflow="nmr")
    manifest.status = "completed"
    manifest.add_product(
        "nmr_report", "NMR report (JSON)", f"reports/{paths['json'].name}", "report"
    )
    if paths["xlsx"]:
        manifest.add_product(
            "nmr_xlsx",
            "NMR assignment (XLSX)",
            f"reports/{paths['xlsx'].name}",
            "report",
        )
    for i, plot in enumerate(paths["plots"], start=1):
        manifest.add_product(f"plot_{i}", f"Plot {i}", f"reports/{plot.name}", "report")
    manifest.write(storage.result_dir())

    if progress_reporter is not None:
        progress_reporter.complete_stage("nmr_report")

    return WorkflowResult(
        status="completed",
        stages_completed=stages_completed,
        metadata={
            "n_candidates": len(candidate_results),
            "winner": summary["winner"],
            "report_json": str(paths["json"]),
            "report_xlsx": str(paths["xlsx"]) if paths["xlsx"] else None,
            "error_model": actual_error_model,
            "dp5_mode": report.dp5_mode,
            "fchl_kernel": fchl_kernel,
            "note": (
                "DP4/DP5 use placeholder error-model parameters (P1a); values are relative only."
                if actual_error_model.startswith("placeholder")
                else ""
            ),
        },
    )


def _nucleus_of_element(element: str) -> str:
    sym = (element or "").strip()
    if not sym:
        return "?"
    sym = sym[:1].upper() + sym[1:].lower()
    defaults = {"H": "1H", "C": "13C", "N": "15N", "F": "19F", "P": "31P"}
    return defaults.get(sym, f"1{sym}")


__all__ = ["NMR_STAGES", "run_nmr_analysis"]
