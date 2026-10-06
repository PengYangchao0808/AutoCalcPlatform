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
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import numpy as np
from numpy.typing import NDArray

from acp.calculations.checkpoint import Checkpoint, load_checkpoint, write_checkpoint
from acp.calculations.identity import IDENTITY_SCHEMA, identity_fingerprint
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
from acp.nmr.analysis_revision import (
    AnalysisRevision,
    EvidenceHashMismatchError,
    NmrAnalysisSnapshot,
    PeakEdit,
    PeakEditError,
    PeakRevision,
    analysis_evidence_hash,
    analysis_result_identity,
    apply_peak_edits,
    experiment_peak_digest,
    review_only_status,
    revision_identity,
    utc_now_iso,
    verify_evidence_hash,
    write_revision_record,
)
from acp.nmr.assignment import (
    collect_residual_inputs,
    match_assigned,
)
from acp.nmr.atomic_diagnostics import (
    AtomDp5Support,
    AtomicDiagnosticsBundle,
    aggregate_atom_support,
    build_atomic_diagnostics,
)
from acp.nmr.averaging import boltzmann_average_shieldings, incomplete_conformer_ids
from acp.nmr.enumerate import enumerate_candidates
from acp.nmr.equivalence import (
    EquivalenceError,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)
from acp.nmr.error_model import (
    Dp5ProbabilityRecord,
    dp5_model_available,
    load_dp5_model,
    load_error_model,
    validate_error_model_binding,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import (
    Assignment,
    AtomShift,
    CandidateEvidence,
    CandidateProbability,
    CandidateResult,
    ConformerPopulation,
    ConformerShielding,
    EnsembleQuality,
    EvidenceStatus,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    NucleusEvidence,
    ProbabilityResult,
    SignalGroup,
    element_of_nucleus,
    normalize_symbol,
)
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    compute_dp5_goodman,
    dp5_log_to_probability,
    normalize_dp4_gated,
)
from acp.nmr.protocol import (
    GeometrySegment,
    NmrProtocolSpec,
    PopulationEnergySegment,
    ReferenceSegment,
    SamplingSegment,
    ShieldingSegment,
    StatisticalModelSegment,
    aggregate_protocol_block,
    build_protocol_spec,
    classify_tms_source,
)
from acp.nmr.report import write_all_reports
from acp.nmr.scaling import build_assignments, fit_scaling_goodman
from acp.nmr.structure_map import NmrStructureMap, StructureMapError
from acp.storage.layout import TaskStorage
from acp.storage.manifest import ResultManifest
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name, write_result_summary
from cccp.backends.crest import CrestBackend
from cccp.calculation._common import theory_run_config
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
    CENSO_PRESETS,
    CensoConformerRecord,
    CensoInterface,
    CensoRunResult,
    part_index,
)
from cccp.utils.constants import HARTREE_TO_KCAL

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
) -> str | tuple[list[str], list[Structure], int | None]:
    """Expand a single candidate into its diastereomers (DevDoc §5 stage 1).

    Returns either an error string (caller surfaces it) or a tuple
    ``(new_sources, new_candidates, charge)``. ``charge`` passes through
    unchanged (``None`` means auto-detect downstream — never zeroed).
    Enantiomer pairs collapse to one representative — DP4 cannot
    distinguish them, so keeping both would waste compute and return
    degenerate probabilities.
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
        return input_sources, _parse_candidates(input_sources, charge, multiplicity), charge

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
        metadata: dict[str, object] = {
            "source": source,
            "smiles": iso.smiles,
            "stereocenters": iso.stereocenters,
            "enumerated": True,
            **(structure.metadata or {}),
        }
        metadata.update(_capture_candidate_topology(structure, iso.smiles, charge, multiplicity))
        new_candidates.append(
            Structure(
                id=safe,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                symbols=structure.symbols,
                coordinates=structure.coordinates,
                metadata=metadata,
            )
        )
    logger.info(
        "Enumerated %d diastereomer(s) from %s (enantiomer-deduplicated)",
        len(new_candidates),
        source,
    )
    return new_sources, new_candidates, charge


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
    strict_equivalence: bool = False,
    solvent_model: str | None = None,
    max_conformers: int | None = None,
) -> NmrConfig:
    """Assemble :class:`NmrConfig` from cfg + explicit overrides.

    Single resolution path (T21): explicit kwargs win over ``theory.nmr``,
    and solvent + solvent_model are resolved together here — a second
    default table downstream would desynchronize them.
    """
    theory_nmr = (cfg.get("theory") or {}).get("nmr") or {}
    nmr_section = cfg.get("nmr") or {}
    refs = dict(nmr_section.get("references") or {})

    resolved_method = nmr_method or theory_nmr.get("method") or "mPW1PW91"
    resolved_basis = nmr_basis or theory_nmr.get("basis") or "6-311G(d)"
    # The model gates the solvent (T17 resolver contract: ``none`` ⇒ solvent
    # ``""``), so it is resolved first and the chloroform default below is
    # only reachable for solvated runs.
    resolved_model = (
        (solvent_model if solvent_model is not None else theory_nmr.get("solvent_model")) or "cpcm"
    ).lower()
    resolved_solvent = solvent if solvent is not None else theory_nmr.get("solvent")
    if resolved_model == "none":
        # Gas phase: no solvent, ever — the recorded effective config must
        # equal what the GIAO level executes (never the chloroform default).
        effective_solvent = ""
    else:
        effective_solvent = resolved_solvent if resolved_solvent else "chloroform"

    # TMS references: explicit overrides > user-configured references >
    # solvent-aware Goodman TMSdata table (DevDoc §10.3) > Goodman
    # chloroform defaults. The table is keyed by (method, basis, solvent);
    # a gas-phase run passes ``""`` which selects the table's ``solvent=
    # "none"`` row (same level of theory as the gas-phase GIAO call), and
    # any level absent from the table falls back to the Goodman defaults.
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
        solvent_model=resolved_model,
        tms_shieldings=tms_shieldings
        or {
            "1H": 32.1243166667,  # Goodman TMSdata mPW1PW91/6-311G(d)/chloroform
            "13C": 188.452125,
        },
        boltzmann_temp=float(boltzmann_temp or nmr_section.get("temperature_k") or 298.15),
        energy_window_kcal=float(nmr_section.get("energy_window_kcal") or 3.0),
        max_conformers=int(
            max_conformers
            if max_conformers is not None and max_conformers > 0
            else nmr_section.get("max_conformers") or 10
        ),
        error_model=error_model or "goodman-legacy",
        conformer_preset=conformer_preset or "censo-light",
        strict_equivalence=strict_equivalence or bool(nmr_section.get("strict_equivalence")),
    )


def _missing_reference_nuclei(nmr_config: NmrConfig, candidates: list[Structure]) -> set[str]:
    """Required nuclei (element present in a candidate) lacking a TMS reference."""
    missing = {n for n in nmr_config.nuclei if nmr_config.tms_for(n) is None}
    if not missing:
        return set()
    present: set[str] = set()
    for structure in candidates:
        present.update(nmr_config.element_nuclei(list(structure.symbols)))
    return missing & present


def _protocol_spec_for_candidate(
    nmr_config: NmrConfig,
    structure: Structure,
    *,
    generation_executed: bool,
    error_model: str,
    dp5_model_id: str | None,
    dp5_mode: str | None,
    dp5_model_present: bool,
) -> NmrProtocolSpec:
    """Build one candidate's six-segment protocol record from what ran.

    Sampling/geometry facts mirror the runtime dispatch: censo-zero never
    calls CENSO (empty parts), a prebuilt/foreign ensemble records ``None``
    (unknown — never upgraded), and an optimization level is only recorded
    when the preset's optimization part actually executed.
    """
    preset = nmr_config.conformer_preset or ""
    if not generation_executed:
        parts: tuple[str, ...] | None = None
    elif preset.lower() == "censo-zero":
        parts = ()
    else:
        preset_parts = CENSO_PRESETS.get(preset, {}).get("parts")
        parts = (
            tuple(str(part) for part in preset_parts) if isinstance(preset_parts, list) else None
        )
    if parts is None:
        optimization_executed: bool | None = None
        optimization_level: str | None = None
    else:
        optimization_executed = "optimization" in parts
        optimization_level = None
        if optimization_executed:
            opt_cfg = CENSO_PRESETS.get(preset, {}).get("optimization") or {}
            func = opt_cfg.get("func") if isinstance(opt_cfg, dict) else None
            optimization_level = str(func) if func else None

    missing_all = {n for n in nmr_config.nuclei if nmr_config.tms_for(n) is None}
    missing_here = tuple(
        sorted(missing_all & set(nmr_config.element_nuclei(list(structure.symbols))))
    )
    return build_protocol_spec(
        SamplingSegment(
            conformer_preset=preset,
            crest_executed=generation_executed,
            censo_executed=generation_executed and preset.lower() != "censo-zero",
            parts=parts,
        ),
        GeometrySegment(
            optimization_executed=optimization_executed,
            optimization_level=optimization_level,
        ),
        PopulationEnergySegment(
            energy_window_kcal=nmr_config.energy_window_kcal,
            boltzmann_temp=nmr_config.boltzmann_temp,
        ),
        ShieldingSegment(
            nmr_method=nmr_config.nmr_method,
            nmr_basis=nmr_config.nmr_basis,
            solvent_model=nmr_config.solvent_model,
        ),
        ReferenceSegment(
            tms_source=classify_tms_source(
                nmr_config.nmr_method,
                nmr_config.nmr_basis,
                nmr_config.solvent or "",
                nmr_config.tms_shieldings,
            ),
            effective_solvent=nmr_config.solvent or "",
            tms_shieldings=dict(nmr_config.tms_shieldings),
            missing_nuclei=missing_here,
            reference_data_present=False,
        ),
        StatisticalModelSegment(
            error_model=error_model,
            dp5_model_id=dp5_model_id,
            dp5_mode=dp5_mode,
            dp5_model_present=dp5_model_present,
        ),
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


#: Engineering population target for the cumulative-population gate (G09:
#: 0.99 is an engineering goal, not a validated scientific threshold).
CUMULATIVE_POPULATION_TARGET: Final = 0.99
#: A conformer carrying at least this share of the selected population is
#: "dominant" — its failure degrades the ensemble quality (G09).
DOMINANT_POPULATION: Final = 0.5
#: Successful-population floor for an undegraded ensemble.
QUALITY_POPULATION_TARGET: Final = 0.95
#: Resource-cap uncovered mass above which the quality degrades.
UNCOVERED_POPULATION_LIMIT: Final = 0.05


@dataclass(frozen=True)
class _SelectedConformer:
    """One conformer selected for GIAO: weights + Δ for recomputation."""

    conformer_id: str
    structure: Structure
    raw_weight: float
    selected_weight: float
    delta_hartree: float


@dataclass(frozen=True)
class _SelectionResult:
    """Ensemble selection evidence before GIAO (todo 30 / G09)."""

    definition: str
    selected: tuple[_SelectedConformer, ...]
    populations: tuple[ConformerPopulation, ...]
    preselection_population: float
    selected_population: float
    uncovered_population: float
    population_gate_dropped: float
    flags: tuple[str, ...]


def _select_conformers(
    ensemble: StructureEnsemble,
    nmr_config: NmrConfig,
) -> _SelectionResult:
    """Select conformers under ONE energy definition with population evidence.

    G09 rules enforced here:

    * the ensemble is compared on a single energy definition — ``free_energy``
      when every energy-bearing record carries it, else ``energy`` (the more
      complete column when neither is complete); a record without the chosen
      definition is excluded with ``missing_energy`` and NEVER assigned 0;
    * the energy window, a cumulative-population gate (engineering target
      :data:`CUMULATIVE_POPULATION_TARGET`) and the hard
      ``max_conformers`` cap jointly decide the selected set;
    * every conformer's raw/selected weights, Δ and exclusion reason are
      recorded so the weights are recomputable (``ConformerPopulation``);
    * cap truncation records its uncovered mass loudly
      (``resource_cap_truncated``) — discovered-ensemble coverage is not
      solution coverage.
    """
    records = list(ensemble.records)
    if not records:
        return _SelectionResult("none", (), (), 0.0, 0.0, 0.0, 0.0, ())

    def _finite(value: Any) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    free_values = [_finite(record.free_energy_hartree) for record in records]
    energy_values = [_finite(record.energy_hartree) for record in records]
    has_free = [value is not None for value in free_values]
    has_energy = [value is not None for value in energy_values]
    any_energy = [free or energy for free, energy in zip(has_free, has_energy, strict=True)]

    if not any(any_energy):
        populations = tuple(
            ConformerPopulation(
                conformer_id=f"conf_{index:03d}",
                energy_definition="none",
                energy_hartree=None,
                delta_hartree=None,
                raw_weight=None,
                selected_weight=None,
                final_weight=None,
                exclusion_reason="missing_energy",
            )
            for index in range(len(records))
        )
        return _SelectionResult(
            "none", (), populations, 0.0, 0.0, 0.0, 0.0, ("energy_definition_incomplete",)
        )

    n_any = sum(any_energy)
    n_free = sum(has_free)
    n_energy = sum(has_energy)
    if n_free == n_any:
        definition = "free_energy"
    elif n_energy == n_any:
        definition = "energy"
    elif n_free >= n_energy:
        definition = "free_energy"
    else:
        definition = "energy"
    values = free_values if definition == "free_energy" else energy_values

    valid = sorted(
        ((index, value) for index, value in enumerate(values) if value is not None),
        key=lambda item: (item[1], item[0]),
    )
    minimum = valid[0][1]
    delta_by_index = {index: value - minimum for index, value in valid}
    kt = 0.001987204259 * nmr_config.boltzmann_temp / HARTREE_TO_KCAL
    if kt > 0:
        factors = [(index, math.exp(-(value - minimum) / kt)) for index, value in valid]
    else:
        factors = [(index, 1.0) for index, _ in valid]
    total = sum(factor for _, factor in factors)
    raw_by_index = {index: factor / total for index, factor in factors}

    window = nmr_config.energy_window_kcal / HARTREE_TO_KCAL
    window_indices = [index for index, _ in valid if delta_by_index[index] <= window]

    # cumulative-population gate: keep the most populated records until the
    # engineering target is met (ties resolved by discovered order)
    by_weight = sorted(window_indices, key=lambda index: (-raw_by_index[index], index))
    cumulative = 0.0
    gate_indices: list[int] = []
    for index in by_weight:
        gate_indices.append(index)
        cumulative += raw_by_index[index]
        if cumulative >= CUMULATIVE_POPULATION_TARGET:
            break
    gate_set = set(gate_indices)
    population_gate_dropped = sum(
        raw_by_index[index] for index in window_indices if index not in gate_set
    )

    # hard resource cap on the energy-sorted gate set (lowest energy first)
    gate_sorted = sorted(gate_indices, key=lambda index: (values[index], index))
    cap = max(0, int(nmr_config.max_conformers))
    cap_indices = gate_sorted[:cap]
    cap_set = set(cap_indices)
    uncovered_population = sum(raw_by_index[index] for index in gate_sorted if index not in cap_set)
    selected_mass = sum(raw_by_index[index] for index in cap_indices)
    if selected_mass > 0:
        selected_weight_by_index = {
            index: raw_by_index[index] / selected_mass for index in cap_indices
        }
    else:
        selected_weight_by_index = {index: 1.0 / len(cap_indices) for index in cap_indices}

    selected = tuple(
        _SelectedConformer(
            conformer_id=f"conf_{index:03d}",
            structure=records[index].structure,
            raw_weight=raw_by_index[index],
            selected_weight=selected_weight_by_index[index],
            delta_hartree=delta_by_index[index],
        )
        for index in cap_indices
    )

    cap_drop_set = gate_set - cap_set
    gate_drop_set = set(window_indices) - gate_set
    populations: list[ConformerPopulation] = []
    for index in range(len(records)):
        if index not in raw_by_index:
            reason: str | None = "missing_energy"
        elif index in cap_set:
            reason = None
        elif index in cap_drop_set:
            reason = "resource_cap"
        elif index in gate_drop_set:
            reason = "population_threshold"
        else:
            reason = "outside_energy_window"
        populations.append(
            ConformerPopulation(
                conformer_id=f"conf_{index:03d}",
                energy_definition=definition,
                energy_hartree=values[index],
                delta_hartree=delta_by_index.get(index),
                raw_weight=raw_by_index.get(index),
                selected_weight=selected_weight_by_index.get(index),
                final_weight=None,
                exclusion_reason=reason,
            )
        )

    flags: list[str] = []
    if any(population.exclusion_reason == "missing_energy" for population in populations):
        flags.append("energy_definition_incomplete")
    if population_gate_dropped > 1e-12:
        flags.append("population_gate_applied")
    if uncovered_population > 1e-12:
        flags.append("resource_cap_truncated")

    return _SelectionResult(
        definition=definition,
        selected=selected,
        populations=tuple(populations),
        preselection_population=len(valid) / len(records),
        selected_population=selected_mass,
        uncovered_population=uncovered_population,
        population_gate_dropped=population_gate_dropped,
        flags=tuple(flags),
    )


def _finalize_ensemble_quality(
    selection: _SelectionResult,
    shieldings: list[ConformerShielding],
    symbols: list[str],
    nmr_config: NmrConfig,
    omit_atom_indices: list[int] | None = None,
) -> tuple[list[ConformerShielding], EnsembleQuality]:
    """Reject incomplete conformers whole and build the quality record (G09).

    Returns the conformers the averaging/DP5 algorithms may consume — weights
    renormalized over the successful complete set (reported weights = actual
    algorithm input) with their Δ threaded through — plus the
    :class:`EnsembleQuality` evidence record.
    """
    incomplete = set(incomplete_conformer_ids(shieldings, symbols, nmr_config, omit_atom_indices))
    complete = [cs for cs in shieldings if cs.conformer_id not in incomplete]
    total_weight = sum(float(cs.boltzmann_weight) for cs in complete)
    if complete and total_weight > 0:
        final_weights = [float(cs.boltzmann_weight) / total_weight for cs in complete]
    elif complete:
        final_weights = [1.0 / len(complete)] * len(complete)
    else:
        final_weights = []

    delta_by_id = {item.conformer_id: item.delta_hartree for item in selection.selected}
    selected_weight_by_id = {item.conformer_id: item.selected_weight for item in selection.selected}
    finalized = [
        replace(
            cs,
            boltzmann_weight=weight,
            delta_hartree=delta_by_id.get(cs.conformer_id),
        )
        for cs, weight in zip(complete, final_weights, strict=True)
    ]
    final_by_id = {cs.conformer_id: cs.boltzmann_weight for cs in finalized}
    successful_ids = set(final_by_id)
    successful_population = 0.0
    for cs, original in zip(finalized, complete, strict=True):
        selected_weight = selected_weight_by_id.get(cs.conformer_id)
        if selected_weight is None:
            selected_weight = float(original.boltzmann_weight)
        successful_population += selected_weight

    updated: list[ConformerPopulation] = []
    for population in selection.populations:
        if population.conformer_id in successful_ids:
            updated.append(
                replace(
                    population,
                    final_weight=final_by_id[population.conformer_id],
                    exclusion_reason=None,
                )
            )
        elif population.exclusion_reason is None:
            reason = (
                "incomplete_shieldings" if population.conformer_id in incomplete else "giao_failed"
            )
            updated.append(replace(population, exclusion_reason=reason))
        else:
            updated.append(population)

    flags = list(selection.flags)
    failed_selected = [
        population
        for population in updated
        if population.exclusion_reason in ("giao_failed", "incomplete_shieldings")
    ]
    if any(
        (population.selected_weight or 0.0) >= DOMINANT_POPULATION for population in failed_selected
    ):
        flags.append("dominant_conformer_failed")
    if any(
        (population.selected_weight or 0.0) < DOMINANT_POPULATION for population in failed_selected
    ):
        flags.append("tail_conformer_failed")
    if successful_population < QUALITY_POPULATION_TARGET:
        flags.append("successful_population_below_target")
    degraded = (
        "dominant_conformer_failed" in flags
        or "successful_population_below_target" in flags
        or selection.uncovered_population > UNCOVERED_POPULATION_LIMIT
    )
    quality = EnsembleQuality(
        energy_definition=selection.definition,
        n_discovered=len(selection.populations),
        n_selected=len(selection.selected),
        n_successful=len(finalized),
        preselection_population=selection.preselection_population,
        selected_population=selection.selected_population,
        successful_population=successful_population,
        uncovered_population=selection.uncovered_population,
        population_gate_dropped=selection.population_gate_dropped,
        quality_status="degraded" if degraded else "ok",
        quality_flags=tuple(flags),
        conformers=tuple(updated),
    )
    return finalized, quality


# ── Per-conformer GIAO shielding checkpoints + resource budget (todo 28 / gap G15) ──

_GIAO_CHECKPOINT_SCHEMA: Final = "acp-nmr-giao-shielding-v1"


def _giao_geometry_token(coords: Any) -> list[float]:
    """Canonical geometry token: 10-dp rounded, ``-0.0`` normalised to ``0.0``."""
    array = (
        np.asarray(coords, dtype=np.float64)
        if coords is not None
        else np.zeros((0, 3), dtype=np.float64)
    )
    return [round(float(value), 10) + 0.0 for value in array.ravel()]


def _giao_fingerprint(
    nmr_config: NmrConfig,
    cfg: Mapping[str, Any] | None,
    structure: Structure,
    giao_solvent: str,
    target_elements: list[str],
    *,
    atom_index_base: int,
    geometry: list[float] | None,
) -> str:
    """Science identity of one GIAO shielding result — no stale reuse ever.

    Covers every input that reaches ``run_nmr_shielding``: method/basis/
    solvent (effective, gas-phase ``""``)/solvent_model/nuclei (target
    elements as sent), charge/multiplicity, atom mapping (symbols +
    atom_index_base) and the ``theory.*`` run-config layer that
    ``resolve_spec`` folds into the rendered ORCA input. ``geometry=None``
    yields the non-geometry plan fingerprint that gates the whole
    checkpoint file (method-level changes invalidate everything); passing
    the geometry token yields the per-conformer entry fingerprint
    (geometry changes invalidate a single entry).
    """
    payload: dict[str, Any] = {
        "scope": "acp_nmr_giao_shielding",
        "schema": _GIAO_CHECKPOINT_SCHEMA,
        "method": nmr_config.nmr_method,
        "basis": nmr_config.nmr_basis,
        "solvent": giao_solvent,
        "solvent_model": nmr_config.solvent_model,
        "nuclei": list(target_elements),
        "charge": structure.charge,
        "multiplicity": structure.multiplicity,
        "atom_mapping": {
            "symbols": list(structure.symbols),
            "atom_index_base": atom_index_base,
        },
        "theory_run_config": theory_run_config(cfg),
    }
    if geometry is not None:
        payload["geometry"] = geometry
    return identity_fingerprint(payload)


def _shieldings_from_cache(entry: Mapping[str, Any]) -> dict[int, dict[str, object]] | None:
    """Rebuild ``{atom: {symbol, isotropic}}`` from a cached entry; ``None`` if malformed."""
    raw = entry.get("shieldings")
    if not isinstance(raw, Mapping) or not raw:
        return None
    restored: dict[int, dict[str, object]] = {}
    try:
        for key, value in raw.items():
            if not isinstance(value, Mapping):
                return None
            symbol = value.get("symbol")
            isotropic = value.get("isotropic")
            if symbol is None or isotropic is None:
                return None
            restored[int(key)] = {"symbol": str(symbol), "isotropic": float(isotropic)}
    except (TypeError, ValueError):
        return None
    return restored


def _log_file_token(log_file: Path | None, giao_dir: Path) -> str | None:
    if log_file is None:
        return None
    try:
        return Path(log_file).resolve().relative_to(giao_dir.resolve()).as_posix()
    except ValueError:
        return str(log_file)


def _stored_log_file(giao_dir: Path, stored: Any) -> Path | None:
    if not isinstance(stored, str) or not stored:
        return None
    path = Path(stored)
    resolved = path if path.is_absolute() else giao_dir / path
    return resolved if resolved.exists() else None


def _load_giao_checkpoint(giao_dir: Path, plan_fingerprint: str) -> tuple[dict[str, Any], int]:
    """Load the per-candidate GIAO checkpoint under the D06 identity contract.

    Missing, malformed or fingerprint-mismatched files return ``({}, 0)``
    — conservative recompute, never a crash and never stale reuse.
    """
    checkpoint = load_checkpoint(giao_dir, plan_fingerprint)
    if checkpoint is None:
        return {}, 0
    items = {
        str(key): value
        for key, value in checkpoint.items_state.items()
        if isinstance(value, Mapping)
    }
    return items, checkpoint.resume_count


def _write_giao_checkpoint(
    giao_dir: Path, plan_fingerprint: str, items_state: Mapping[str, Any], resume_count: int
) -> None:
    """Atomic ``checkpoint.json`` write inside the GIAO stage dir.

    A cache-write failure only costs a future recompute — it can never
    fail the run (``OSError`` degrade-to-uncached) nor poison the cache.
    """
    try:
        write_checkpoint(
            giao_dir,
            Checkpoint(
                task_id="nmr",
                workflow="nmr",
                plan_fingerprint=plan_fingerprint,
                step_states=[],
                items_state=dict(items_state),
                resume_count=resume_count,
                identity_schema=IDENTITY_SCHEMA,
            ),
        )
    except OSError as exc:
        logger.warning(
            "GIAO checkpoint write failed for %s (%s); results kept uncached", giao_dir, exc
        )


def _giao_resource_budget(
    cfg: Mapping[str, Any], *, n_candidates: int, n_conformers: int
) -> dict[str, Any]:
    """Explicit ``候选×构象×nproc`` GIAO resource budget — capped, verified.

    * ``nproc``/``mem`` are the job spec (merged-config ``resources``);
    * one GIAO job may use ``executables.orca.nproc`` threads, clamped to
      the job ``nproc`` (never budget more threads per job than the job
      owns) — ``oversubscribed`` records a raw ORCA request above the job
      spec so oversubscription is loud, never silent;
    * ``max_parallel_giao_jobs = min(max(1, nproc // nproc_per_giao), giao_jobs)``
      is the worker cap over the ``候选×构象`` demand envelope; the current
      execution is sequential (1 worker), always ≤ this cap.
    """
    resources = cfg.get("resources")
    resources = resources if isinstance(resources, Mapping) else {}
    raw_nproc = resources.get("nproc")
    job_nproc = (
        raw_nproc
        if isinstance(raw_nproc, int) and not isinstance(raw_nproc, bool) and raw_nproc > 0
        else 1
    )
    mem = resources.get("mem")
    executables = cfg.get("executables")
    executables = executables if isinstance(executables, Mapping) else {}
    orca_cfg = executables.get("orca")
    orca_cfg = orca_cfg if isinstance(orca_cfg, Mapping) else {}
    raw_requested = orca_cfg.get("nproc")
    requested = (
        raw_requested
        if isinstance(raw_requested, int)
        and not isinstance(raw_requested, bool)
        and raw_requested > 0
        else job_nproc
    )
    nproc_per_giao = min(requested, job_nproc)
    giao_jobs = max(0, int(n_candidates)) * max(0, int(n_conformers))
    return {
        "nproc": job_nproc,
        "mem": mem if isinstance(mem, (str, int)) and not isinstance(mem, bool) else None,
        "nproc_per_giao": nproc_per_giao,
        "oversubscribed": requested > job_nproc,
        "n_candidates": int(n_candidates),
        "n_conformers": int(n_conformers),
        "giao_jobs": giao_jobs,
        "max_parallel_giao_jobs": min(max(1, job_nproc // nproc_per_giao), giao_jobs),
        "execution": "sequential",
    }


def _verify_giao_budget(budget: Mapping[str, Any]) -> None:
    """Bound check: recorded workers must fit the job spec (raises otherwise)."""
    nproc = int(budget["nproc"])
    per = int(budget["nproc_per_giao"])
    parallel = int(budget["max_parallel_giao_jobs"])
    jobs = int(budget["giao_jobs"])
    if nproc < 1 or per < 1:
        raise RuntimeError(
            f"GIAO budget has non-positive core counts: nproc={nproc}, nproc_per_giao={per}"
        )
    if parallel > jobs:
        raise RuntimeError(f"GIAO budget workers {parallel} exceed demand giao_jobs={jobs}")
    if per * parallel > nproc:
        raise RuntimeError(
            f"GIAO budget oversubscribes the job spec: "
            f"{per} threads x {parallel} workers > {nproc} cores"
        )


def _run_giao_for_conformers(
    conformers: list[tuple[Structure, float, float]],
    nmr_config: NmrConfig,
    giao_dir: Path,
    cfg: dict[str, Any],
    solvent: str | None,
    *,
    budget: Mapping[str, Any] | None = None,
    conformer_ids: Sequence[str] | None = None,
) -> list[ConformerShielding]:
    """Run ORCA GIAO NMR for each conformer and parse shieldings.

    *giao_dir* is the final per-candidate GIAO root (v2 layout:
    ``WORK/05_SP/ORCA/<candidate_id>``); per-conformer outputs land in
    ``conf_<idx>`` subdirectories beneath it.

    Per-conformer shieldings are fingerprinted into
    ``<giao_dir>/checkpoint.json`` (todo 28): a re-run with matching
    fingerprints reuses stored results and computes only missing or
    invalidated conformers; method-level changes invalidate the whole
    file (plan fingerprint mismatch), a geometry change invalidates a
    single entry. Successful results are checkpointed atomically after
    each conformer, so an interrupted run resumes exactly where it
    stopped. Execution is sequential — one GIAO at a time, always within
    the explicit ``budget`` cap (verified before the loop).
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

    # Gas-phase contract (T21): solvent_model=none executes with no solvent
    # keyword at all — belt and braces on top of _build_nmr_config so a
    # directly-built NmrConfig can never inject a cpcm/SMD block.
    giao_solvent = "" if nmr_config.solvent_model.lower() == "none" else nmr_config.solvent

    if budget is None:
        budget = _giao_resource_budget(cfg, n_candidates=1, n_conformers=len(conformers))
    _verify_giao_budget(budget)
    logger.info("GIAO resource budget: %s", json.dumps(budget, sort_keys=True))
    if budget["oversubscribed"]:
        logger.warning(
            "GIAO orca nproc exceeds the job nproc budget "
            "(nproc_per_giao=%s requested, capped at nproc=%s) — "
            "thread oversubscription recorded, not silent",
            budget["nproc_per_giao"],
            budget["nproc"],
        )

    results: list[ConformerShielding] = []
    if not conformers:
        return results

    giao_options = NmrShieldingOptions()
    probe_structure = conformers[0][0]
    plan_fingerprint = _giao_fingerprint(
        nmr_config,
        cfg,
        probe_structure,
        giao_solvent,
        target_elements,
        atom_index_base=giao_options.atom_index_base,
        geometry=None,
    )
    stored_items, resume_count = _load_giao_checkpoint(giao_dir, plan_fingerprint)
    if stored_items:
        resume_count += 1
        _write_giao_checkpoint(giao_dir, plan_fingerprint, stored_items, resume_count)
        logger.info(
            "GIAO resume: checkpoint hit for plan %s — %d cached conformer(s), resume #%d",
            plan_fingerprint,
            len(stored_items),
            resume_count,
        )
    else:
        logger.info(
            "GIAO resume: no reusable checkpoint for plan %s — fresh compute",
            plan_fingerprint,
        )

    items_state: dict[str, Any] = {}
    for idx, (structure, weight, delta) in enumerate(conformers):
        coords: NDArray[np.float64] = (
            np.asarray(structure.coordinates, dtype=np.float64)
            if structure.coordinates is not None
            else np.zeros((0, 3), dtype=np.float64)
        )
        conformer_id = conformer_ids[idx] if conformer_ids is not None else f"conf_{idx:03d}"
        entry_fingerprint = _giao_fingerprint(
            nmr_config,
            cfg,
            structure,
            giao_solvent,
            target_elements,
            atom_index_base=giao_options.atom_index_base,
            geometry=_giao_geometry_token(coords),
        )
        cached = stored_items.get(conformer_id)
        reused: dict[int, dict[str, object]] | None = None
        if isinstance(cached, Mapping) and cached.get("fingerprint") == entry_fingerprint:
            reused = _shieldings_from_cache(cached)
        if reused is not None and isinstance(cached, Mapping):
            items_state[conformer_id] = cached
            results.append(
                ConformerShielding(
                    conformer_id=conformer_id,
                    boltzmann_weight=float(weight),
                    shieldings=reused,
                    log_file=_stored_log_file(giao_dir, cached.get("log_file")),
                    coordinates=structure.coordinates,
                    symbols=list(structure.symbols),
                )
            )
            logger.info(
                "GIAO %s: RESUMED from checkpoint (fingerprint match %s)",
                conformer_id,
                entry_fingerprint,
            )
            continue
        if cached is None:
            logger.info("GIAO %s: COMPUTING (no checkpoint entry)", conformer_id)
        else:
            logger.info(
                "GIAO %s: COMPUTING (fingerprint mismatch — stale entry invalidated)",
                conformer_id,
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
                        solvent=giao_solvent,
                        solvent_model=nmr_config.solvent_model,
                    ),
                    options=giao_options,
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
        items_state[conformer_id] = {
            "fingerprint": entry_fingerprint,
            "shieldings": {
                str(int(atom_index)): {
                    "symbol": str(entry["symbol"]),
                    "isotropic": float(entry["isotropic"]),  # type: ignore[arg-type]
                }
                for atom_index, entry in shieldings.items()
            },
            "log_file": _log_file_token(log_file, giao_dir),
        }
        _write_giao_checkpoint(giao_dir, plan_fingerprint, items_state, resume_count)
        results.append(
            ConformerShielding(
                conformer_id=conformer_id,
                boltzmann_weight=float(weight),
                shieldings=shieldings,
                log_file=log_file,
                coordinates=structure.coordinates,
                symbols=list(structure.symbols),
            )
        )
    _ = delta  # ΔG is Boltzmann bookkeeping, not a shielding input
    _ = solvent  # provenance only — solvent is applied via nmr_config
    return results


def _observation_id_for_peak(
    peak: ExperimentalPeak,
    element_peaks: Sequence[ExperimentalPeak],
) -> str:
    """Stable observation id (``"element:index"``) for a matched peak (G16).

    Mirrors the evidence gate's ids; a hand-built peak without an index
    falls back to its position in the element's peak list so the id stays
    stable and unique.
    """
    if peak.index is not None:
        return f"{peak.element}:{peak.index}"
    for position, candidate in enumerate(element_peaks):
        if candidate is peak:
            return f"{peak.element}:{position}"
    return f"{peak.element}:{peak.shift_ppm}"


def _analyze_candidate(
    index: int,
    structure: Structure,
    conformer_shieldings: list[ConformerShielding],
    experiment: ExperimentalNmr,
    nmr_config: NmrConfig,
) -> CandidateResult:
    """Stages 4–7: average, match, scale, collect residuals for probability."""
    symbols = list(structure.symbols)
    omit_indices = _omit_atom_indices(experiment, structure, strict=nmr_config.strict_equivalence)

    # DevDoc §5 stage 5: assigned spectra use ONLY explicit EQ groups (the
    # user has already labeled every atom of interest); auto-detection
    # would wrongly collapse distinct assigned atoms. Unassigned spectra
    # rely on detection (no labels to disambiguate).
    if experiment.assigned:
        equivalence_groups = _explicit_eq_to_indices(
            experiment, structure, strict=nmr_config.strict_equivalence
        )
    else:
        # G01: prefer the captured bonded graph (any input format), then
        # the SMILES rebuild. Without any graph the detector returns
        # single-atom groups flagged equivalence_unknown and strict mode
        # rejects (EquivalenceError) — same-element atoms never merge.
        mol = nmr_topology_mol_for(structure)
        if mol is None:
            mol = _try_build_rdkit_mol(structure)
        detected_groups = detect_equivalence_groups(
            symbols, mol=mol, strict=nmr_config.strict_equivalence
        )
        equivalence_groups = merge_explicit_and_detected(
            experiment.equivalence_groups, detected_groups, symbols
        )

    atom_shifts = boltzmann_average_shieldings(
        conformer_shieldings,
        symbols,
        nmr_config,
        equivalence_groups=equivalence_groups,
        omit_atom_indices=omit_indices,
        structure_map=nmr_structure_map_for(structure),
    )
    atom_shifts = _relabel_shifts_with_map(atom_shifts, structure)

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
        built = build_assignments(
            arrays["labels"],
            arrays["elements"],
            arrays["exp"],
            arrays["calc"],
            scaled,
            residuals,
            signal_groups=arrays["signal_groups"],
        )
        # G16: keep the matched experimental observation on each row so the
        # atomic diagnostics can link signal ↔ assignment ↔ experiment peak.
        peaks_for_element = experiment.peaks_for(element_of_nucleus(nucleus))
        assignments.extend(
            replace(
                assignment,
                observation_id=_observation_id_for_peak(peak, peaks_for_element),
            )
            for assignment, (_shift, peak) in zip(built, pairs.get(nucleus, ()), strict=True)
        )

    emitted_groups: list[SignalGroup] = []
    seen_groups: set[SignalGroup] = set()
    for shift in atom_shifts:
        group = shift.signal_group
        if group is not None and group not in seen_groups:
            seen_groups.add(group)
            emitted_groups.append(group)

    evidence = _build_candidate_evidence(experiment, nmr_config, residual_by_nucleus, pairs)
    return CandidateResult(
        index=index,
        label=structure.id,
        atom_shifts=atom_shifts,
        assignments=assignments,
        regressions=regressions,
        conformer_shieldings=conformer_shieldings,
        evidence=evidence,
        signal_groups=tuple(emitted_groups),
        # probabilities set by the orchestrator (need all candidates first)
    )


def _build_candidate_evidence(
    experiment: ExperimentalNmr,
    nmr_config: NmrConfig,
    residual_by_nucleus: dict[str, list[float]],
    pairs: dict[str, list[tuple[AtomShift, ExperimentalPeak]]],
) -> CandidateEvidence:
    """Assemble the evidence record gating this candidate into DP4 (G05).

    Counts the experimental observations requested for the configured
    nuclei (``expected``), the residuals actually produced (``matched``)
    and the stable ids of the matched peaks (``"element:index"``). The
    base status is decided here — no matched signals at all is
    ``invalid``/``no_matched_signals``, 1–2 matched signals cannot support
    a calibrated ranking and is ``evidence_insufficient``/
    ``two_point_calibration``; cross-candidate comparability is applied
    later in stage 7 (see :func:`_apply_evidence_comparability_gate`).
    """
    per_nucleus: dict[str, NucleusEvidence] = {}
    observation_ids: list[str] = []
    total_matched = 0
    for nucleus in nmr_config.nuclei:
        expected = len(experiment.peaks_for(element_of_nucleus(nucleus)))
        matched = len(residual_by_nucleus.get(nucleus, []))
        per_nucleus[nucleus] = NucleusEvidence(expected=expected, matched=matched)
        total_matched += matched
        for _shift, peak in pairs.get(nucleus, []):
            observation_ids.append(f"{peak.element}:{peak.index}")

    status: EvidenceStatus
    reasons: list[str]
    if total_matched == 0:
        status = "invalid"
        reasons = ["no_matched_signals"]
    elif total_matched <= 2:
        status = "evidence_insufficient"
        reasons = ["two_point_calibration"]
    else:
        status = "valid"
        reasons = []
    return CandidateEvidence(
        status=status,
        per_nucleus=per_nucleus,
        observation_ids=tuple(observation_ids),
        exclusion_reasons=tuple(reasons),
        total_matched=total_matched,
    )


#: Sensitivity axes capped for cost: leave-one-out × conformers and two
#: temperature bounds per candidate set.
_SENSITIVITY_MAX_CONFORMERS: Final = 40
_SENSITIVITY_TEMPERATURE_SPREAD: Final = 0.1

SENSITIVITY_FLAGS: Final[tuple[str, ...]] = (
    "leave_one_out_winner_flip",
    "temperature_winner_flip",
)


def _dp4_log_likelihood(
    candidate_result: CandidateResult,
    nmr_config: NmrConfig,
    error_model: Any,
) -> float:
    """DP4 log-likelihood of one candidate from its own residuals."""
    return compute_dp4(
        {
            nucleus: [
                assignment.residual
                for assignment in candidate_result.assignments
                if _nucleus_of_element(assignment.element) == nucleus
            ]
            for nucleus in nmr_config.nuclei
        },
        error_model,
    )


def _dp4_ranking_winner(
    probabilities: list[float | None],
    candidate_results: list[CandidateResult],
    nmr_config: NmrConfig,
    error_model_id: str,
) -> str | None:
    """Winner label under the DP4-only ranking (no DP5 tie-break).

    Sensitivity compares the primary (DP4) order; typed probability blocks
    are dropped from the temporary candidates so a stale DP5 value can never
    influence a perturbed ranking.
    """
    temporary = [
        replace(cr, dp4_probability=probability, dp5_probability=None, probability=None)
        for cr, probability in zip(candidate_results, probabilities, strict=True)
    ]
    report = NmrReport(candidates=temporary, config=nmr_config, error_model=error_model_id)
    winner = report.winner
    return winner.label if winner is not None else None


def _reweight_by_temperature(
    shieldings: list[ConformerShielding],
    temperature_k: float,
    nmr_config: NmrConfig,
) -> list[ConformerShielding] | None:
    """Boltzmann-recompute weights at *temperature_k*; ``None`` when no Δ data."""
    deltas = [cs.delta_hartree for cs in shieldings]
    if any(delta is None for delta in deltas):
        return None
    kt = 0.001987204259 * temperature_k / HARTREE_TO_KCAL
    if kt <= 0:
        return None
    factors = [math.exp(-float(delta) / kt) for delta in deltas]
    total = sum(factors)
    if total <= 0:
        return None
    return [
        replace(cs, boltzmann_weight=factor / total)
        for cs, factor in zip(shieldings, factors, strict=True)
    ]


def _sensitivity_analysis(
    structures: list[Structure],
    candidate_results: list[CandidateResult],
    conformer_shieldings_by_candidate: list[list[ConformerShielding]],
    experiment: ExperimentalNmr,
    nmr_config: NmrConfig,
    error_model: Any,
) -> dict[str, object]:
    """Leave-one-conformer-out + temperature stability of the DP4 winner.

    A conclusion that flips when one conformer is removed or the Boltzmann
    temperature moves ±10 % is marked ``requires_review`` — never reported
    as a stable ranking (G09). Pure post-processing on cached shieldings: no
    QC is re-run.
    """
    n_conformers = sum(len(confs) for confs in conformer_shieldings_by_candidate)
    baseline_ll = [_dp4_log_likelihood(cr, nmr_config, error_model) for cr in candidate_results]
    statuses = [
        cr.evidence.status if cr.evidence is not None else "valid" for cr in candidate_results
    ]
    baseline_probs = normalize_dp4_gated(baseline_ll, statuses)
    baseline_winner = _dp4_ranking_winner(
        baseline_probs, candidate_results, nmr_config, error_model.model_id
    )
    report: dict[str, object] = {
        "status": "computed",
        "flags": [],
        "requires_review": False,
        "baseline_winner": baseline_winner,
        "leave_one_out": {"n_cases": 0, "n_flips": 0, "cases": []},
        "temperature": {
            "status": "skipped",
            "reason": "not_evaluated",
            "range_k": [],
            "results": [],
        },
    }
    if baseline_winner is None or len(candidate_results) < 2:
        report["status"] = "skipped"
        report["reason"] = "no_ranking"
        return report
    if n_conformers > _SENSITIVITY_MAX_CONFORMERS:
        report["status"] = "skipped"
        report["reason"] = "resource_guard"
        report["n_conformers"] = n_conformers
        return report

    flags: list[str] = []
    cases: list[dict[str, object]] = []
    for index, confs in enumerate(conformer_shieldings_by_candidate):
        if len(confs) < 2:
            continue
        for position, removed in enumerate(confs):
            remaining = list(confs[:position]) + list(confs[position + 1 :])
            perturbed = _analyze_candidate(
                index, structures[index], remaining, experiment, nmr_config
            )
            perturbed_ll = list(baseline_ll)
            perturbed_ll[index] = _dp4_log_likelihood(perturbed, nmr_config, error_model)
            perturbed_statuses = list(statuses)
            perturbed_statuses[index] = (
                perturbed.evidence.status if perturbed.evidence is not None else "valid"
            )
            probabilities = normalize_dp4_gated(perturbed_ll, perturbed_statuses)
            perturbed_candidates = list(candidate_results)
            perturbed_candidates[index] = perturbed
            winner = _dp4_ranking_winner(
                probabilities, perturbed_candidates, nmr_config, error_model.model_id
            )
            cases.append(
                {
                    "candidate": index,
                    "label": candidate_results[index].label,
                    "removed_conformer": removed.conformer_id,
                    "winner": winner,
                    "flips": winner != baseline_winner,
                }
            )
    n_flips = sum(1 for case in cases if case["flips"])
    report["leave_one_out"] = {"n_cases": len(cases), "n_flips": n_flips, "cases": cases}
    if n_flips:
        flags.append("leave_one_out_winner_flip")

    temperature = nmr_config.boltzmann_temp
    lower = temperature * (1.0 - _SENSITIVITY_TEMPERATURE_SPREAD)
    upper = temperature * (1.0 + _SENSITIVITY_TEMPERATURE_SPREAD)
    if all(len(confs) < 2 for confs in conformer_shieldings_by_candidate):
        report["temperature"] = {
            "status": "skipped",
            "reason": "single_conformer",
            "range_k": [lower, upper],
            "results": [],
        }
    else:
        temperature_results: list[dict[str, object]] = []
        skip_reason: str | None = None
        for bound in (lower, upper):
            reweighted: list[list[ConformerShielding]] = []
            for confs in conformer_shieldings_by_candidate:
                updated = _reweight_by_temperature(confs, bound, nmr_config)
                if updated is None:
                    skip_reason = "missing_delta_evidence"
                    break
                reweighted.append(updated)
            if skip_reason is not None:
                break
            perturbed_results = [
                _analyze_candidate(
                    index, structures[index], reweighted[index], experiment, nmr_config
                )
                for index in range(len(candidate_results))
            ]
            likelihoods = [
                _dp4_log_likelihood(cr, nmr_config, error_model) for cr in perturbed_results
            ]
            perturbed_statuses = [
                cr.evidence.status if cr.evidence is not None else "valid"
                for cr in perturbed_results
            ]
            probabilities = normalize_dp4_gated(likelihoods, perturbed_statuses)
            winner = _dp4_ranking_winner(
                probabilities, perturbed_results, nmr_config, error_model.model_id
            )
            temperature_results.append(
                {
                    "temperature_k": bound,
                    "winner": winner,
                    "flips": winner != baseline_winner,
                }
            )
        if skip_reason is not None:
            report["temperature"] = {
                "status": "skipped",
                "reason": skip_reason,
                "range_k": [lower, upper],
                "results": [],
            }
        else:
            report["temperature"] = {
                "status": "computed",
                "reason": None,
                "range_k": [lower, upper],
                "results": temperature_results,
            }
            if any(entry["flips"] for entry in temperature_results):
                flags.append("temperature_winner_flip")

    report["flags"] = flags
    report["requires_review"] = bool(flags)
    return report


def _apply_evidence_comparability_gate(candidate_results: list[CandidateResult]) -> None:
    """Stage-7 cross-candidate check (G05): rank on one shared nucleus set.

    DP4 log-likelihoods sum only over nuclei with matched residuals, so two
    candidates scoring over different nucleus sets are not comparable: a
    candidate missing a nucleus that every other evidence-bearing candidate
    matched would silently skip that term and get an unfairly high
    likelihood. Such a candidate is marked ``invalid`` with reason
    ``incomparable_nuclei``. Candidates with no evidence never contribute to
    the reference set — they are already excluded by their own gate.
    """
    evidence_sets: list[set[str]] = [
        (
            {nuc for nuc, ev in cr.evidence.per_nucleus.items() if ev.matched > 0}
            if cr.evidence is not None
            else set()
        )
        for cr in candidate_results
    ]
    for position, cr in enumerate(candidate_results):
        if cr.evidence is None:
            continue
        others = [s for i, s in enumerate(evidence_sets) if i != position and s]
        if not others:
            continue  # single candidate / no other evidence to compare against
        if set.intersection(*others) - evidence_sets[position]:
            cr.evidence = replace(
                cr.evidence,
                status="invalid",
                exclusion_reasons=cr.evidence.exclusion_reasons + ("incomparable_nuclei",),
            )


def _omit_atom_indices(
    experiment: ExperimentalNmr,
    structure: Structure,
    strict: bool = False,
) -> list[int]:
    """Resolve OMIT labels to 0-based candidate (mol) indices.

    Resolution goes through :func:`nmr_structure_map_for`; without a
    molecular graph the labels cannot be resolved and are ignored
    (warning) — there is no per-element ordinal fallback.
    """
    if not experiment.omit_atoms:
        return []
    m = nmr_structure_map_for(structure)
    if m is None:
        logger.warning("no molecular graph; OMIT/EQ labels cannot be resolved — ignored")
        return []
    resolved: list[int] = []
    for label in experiment.omit_atoms:
        try:
            resolved.append(m.mol_index_for_source(m.source_index_for_label(label)))
        except StructureMapError as exc:
            if strict:
                raise EquivalenceError(
                    f"unresolvable label {label!r} for candidate {structure.id!r}: {exc}"
                ) from exc
            logger.warning(
                "label %r not resolvable for candidate %s (skipped): %s",
                label,
                structure.id,
                exc,
            )
    return resolved


def _explicit_eq_to_indices(
    experiment: ExperimentalNmr,
    structure: Structure,
    strict: bool = False,
) -> list[list[int]]:
    """Convert explicit ``EQ:`` labels to 0-based candidate (mol) index groups.

    Labels resolve through the stable :class:`NmrStructureMap` (see
    :func:`_omit_atom_indices` for the no-graph behavior). Groups keeping
    fewer than two resolvable indices are dropped — a singleton is the
    default behavior anyway. Returns an empty list when no explicit groups
    are present (each atom is then its own singleton — the desired behavior
    for fully-assigned spectra where every atom is distinct).
    """
    if not experiment.equivalence_groups:
        return []
    m = nmr_structure_map_for(structure)
    if m is None:
        logger.warning("no molecular graph; OMIT/EQ labels cannot be resolved — ignored")
        return []
    groups: list[list[int]] = []
    for group in experiment.equivalence_groups:
        idxs: list[int] = []
        for label in group:
            try:
                idxs.append(m.mol_index_for_source(m.source_index_for_label(label)))
            except StructureMapError as exc:
                if strict:
                    raise EquivalenceError(
                        f"unresolvable label {label!r} for candidate {structure.id!r}: {exc}"
                    ) from exc
                logger.warning(
                    "label %r not resolvable for candidate %s (skipped): %s",
                    label,
                    structure.id,
                    exc,
                )
        if len(idxs) > 1:
            groups.append(idxs)
    return groups


def _relabel_shifts_with_map(
    atom_shifts: list[AtomShift],
    structure: Structure,
) -> list[AtomShift]:
    """Rewrite peak labels into source space under non-identity provenance.

    :func:`boltzmann_average_shieldings` emits labels from the candidate's
    (mol/QC) atom order. When the structure map records a different source
    order, experimental peaks — which are authored against the *source*
    labels — must match the source-space spelling; ``match_assigned`` locks
    labels verbatim, so this runs before it.
    """
    m = nmr_structure_map_for(structure)
    if m is None:
        return list(atom_shifts)
    relabeled: list[AtomShift] = []
    for shift in atom_shifts:
        try:
            new = m.label_for_source(m.source_index_for_mol(shift.atom_index))
        except StructureMapError as exc:  # pragma: no cover - defensive
            logger.warning(
                "atom %d of candidate %s not relabeled: %s",
                shift.atom_index,
                structure.id,
                exc,
            )
            relabeled.append(shift)
            continue
        relabeled.append(shift if new == shift.atom_label else replace(shift, atom_label=new))
    return relabeled


def _validate_mol_for_structure(mol: Chem.Mol, structure: Structure) -> None:
    """Check that *mol* corresponds to *structure* atom-for-atom (G01).

    Verifies the atom count and the element sequence order, then runs
    ``Chem.CanonicalRankAtoms(breakTies=True, includeChirality=True)`` as
    a rank sanity check (the ranks must form a unique 0..n-1 permutation).

    Args:
        mol: Candidate bonded graph under consideration.
        structure: Candidate structure the graph must match.

    Raises:
        StructureMapError: Atom count mismatch, element order mismatch, or
            ranking failure — never an out-of-range index.
    """
    from rdkit import Chem

    symbols = list(structure.symbols)
    n_atoms = len(symbols)
    if mol.GetNumAtoms() != n_atoms:
        raise StructureMapError(
            f"mol has {mol.GetNumAtoms()} atoms but structure {structure.id!r} has {n_atoms}"
        )
    mol_symbols = [normalize_symbol(atom.GetSymbol()) for atom in mol.GetAtoms()]
    struct_symbols = [normalize_symbol(s) for s in symbols]
    if mol_symbols != struct_symbols:
        mismatch = next(i for i, (a, b) in enumerate(zip(mol_symbols, struct_symbols)) if a != b)
        raise StructureMapError(
            f"mol element order does not match structure {structure.id!r}: "
            f"first mismatch at atom {mismatch} (mol {mol_symbols[mismatch]} "
            f"vs structure {struct_symbols[mismatch]})"
        )
    try:
        # same preparation as detect_equivalence_groups (ring info for ranking)
        mol.UpdatePropertyCache(strict=False)
        Chem.GetSymmSSSR(mol)
        ranks = list(Chem.CanonicalRankAtoms(mol, breakTies=True, includeChirality=True))
    except (ValueError, RuntimeError) as exc:
        raise StructureMapError(
            f"canonical ranking failed for structure {structure.id!r}: {exc}"
        ) from exc
    if sorted(ranks) != list(range(n_atoms)):
        raise StructureMapError(
            f"canonical ranks are not a unique permutation for structure "
            f"{structure.id!r}: {sorted(ranks)[:10]}"
        )


def _try_build_rdkit_mol(structure: Structure) -> Chem.Mol | None:
    """Build a bonded RDKit Mol for symmetry equivalence detection.

    Resolution order (gap G01 — enumerated candidates store the original
    XYZ path in ``source`` and the isomer SMILES in ``smiles``):

    1. the candidate's captured graph (``nmr_topology_mol``) — any input
       format; a no-op when the caller already passed it;
    2. ``metadata["smiles"]`` — enumerated/canonical isomer SMILES;
    3. ``metadata["source"]`` when it is SMILES-like.

    Every candidate graph is checked by :func:`_validate_mol_for_structure`
    and rejected with a typed :class:`StructureMapError` (logged, next
    source tried) — never an out-of-range index. Returns ``None`` when all
    sources fail (the element-free equivalence fallback then applies).
    """
    from rdkit import Chem

    captured = nmr_topology_mol_for(structure)
    if captured is not None:
        try:
            _validate_mol_for_structure(captured, structure)
            return captured
        except StructureMapError as exc:
            logger.debug("captured graph rejected for candidate %s: %s", structure.id, exc)

    smiles = structure.metadata.get("smiles")
    source = structure.metadata.get("source", "")
    sources: list[str] = []
    if isinstance(smiles, str) and smiles.strip():
        sources.append(smiles)
    if isinstance(source, str) and source and _looks_like_smiles(source):
        if source not in sources:
            sources.append(source)

    for text in sources:
        try:
            mol = Chem.MolFromSmiles(text)
            if mol is None:
                continue
            mol = Chem.AddHs(mol)
            Chem.SanitizeMol(mol)
            _validate_mol_for_structure(mol, structure)
            return mol
        except StructureMapError as exc:
            logger.debug(
                "RDKit Mol from %r rejected for candidate %s: %s",
                text,
                structure.id,
                exc,
            )
        except (ValueError, RuntimeError) as exc:
            logger.debug("RDKit Mol build failed for '%s': %s", text, exc)
    return None


def _looks_like_smiles(source: str) -> bool:
    """Heuristic: SMILES contains bond/atom symbols but no whitespace."""
    s = source.strip()
    if not s or "\n" in s or " " in s:
        return False
    return any(c in s for c in "CcNnOoPpSsFf=[#]()-/\\123456789")


# Closed vocabulary of per-candidate DP5 computation modes (G07).
# "unavailable"/"not_applicable"/placeholder are stage-7 *status* branches,
# never computation modes, so they are intentionally absent here.
DP5_OUTCOME_MODES: Final[tuple[str, ...]] = ("fchl", "fallback", "averaged")

# Closed vocabulary of per-candidate DP5 outcome validity (G07/t15):
# "invalid" = no complete conformer geometry existed, so no probability.
DP5_OUTCOME_STATUSES: Final[tuple[str, ...]] = ("valid", "invalid")


@dataclass(frozen=True)
class Dp5Outcome:
    """Immutable per-candidate DP5 result (G07).

    Replaces the former shared ``dp5_model.dp5_mode``/``fchl_kernel``
    mutable attributes, where the last candidate overwrote every earlier
    candidate's mode. Each ``_compute_candidate_dp5`` call returns its own
    outcome instead of mutating the model.

    Attributes:
        probability: DP5 probability in ``[0, 1]``, or ``None`` when the
            path produced no value.
        mode: One of :data:`DP5_OUTCOME_MODES` — the path that ran.
        kernel: FCHL kernel backend (``"qml"``/``"numpy"``) when the FCHL
            path ran, else ``""``; read from ``kernel_backend()`` at call
            time, never from model state.
        diagnostics: JSON-safe per-call diagnostics dicts, e.g.
            ``n_conformers_used``, ``fchl_attempted``, ``fallback_reason``.
        status: One of :data:`DP5_OUTCOME_STATUSES`. ``"invalid"`` carries
            ``probability=None`` (validated) — stage 7 maps it to the typed
            ``ProbabilityStatus "invalid"``; ``mode`` is informational there.
        record: The todo-38 :class:`Dp5ProbabilityRecord` when the model
            produced one (path/calibration/support diagnostics), else
            ``None`` for legacy float-only stand-ins.
        atom_support: Per-signal FCHL support records (todo 39) collected
            from the SAME weighted-KDE pass — empty when the unweighted
            fallback ran (no neighbour support exists there).
    """

    probability: float | None
    mode: str
    kernel: str = ""
    diagnostics: tuple[dict[str, object], ...] = ()
    status: Literal["valid", "invalid"] = "valid"
    record: Dp5ProbabilityRecord | None = None
    atom_support: tuple[AtomDp5Support, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in DP5_OUTCOME_MODES:
            raise ValueError(f"unknown DP5 mode {self.mode!r}; expected one of {DP5_OUTCOME_MODES}")
        if self.status not in DP5_OUTCOME_STATUSES:
            raise ValueError(
                f"unknown DP5 outcome status {self.status!r}; "
                f"expected one of {DP5_OUTCOME_STATUSES}"
            )
        if self.status == "invalid" and self.probability is not None:
            raise ValueError("invalid DP5 outcome must carry probability=None")


def _isotropic_shielding(
    shieldings: Mapping[int, dict[str, object]],
    atom_index: int,
) -> float | None:
    """Finite isotropic shielding of *atom_index*, or ``None`` when absent/malformed."""
    entry = shieldings.get(atom_index)
    if not entry or "isotropic" not in entry:
        return None
    raw = entry["isotropic"]
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _conformer_index_for_signal_uid(
    uid: str,
    structure_map: NmrStructureMap | None,
    label_to_idx: Mapping[str, int],
) -> int | None:
    """Resolve one SignalGroup member uid → conformer atom index (G08).

    Map uids (``"C:3"``) go through :meth:`NmrStructureMap.mol_index_for_atom_uid`;
    label uids (``"C1"``) through the per-element label index, with the
    structure map's source-order label scheme as a fallback. ``None`` when
    neither scheme resolves the uid — the caller degrades explicitly.
    """
    if ":" in uid:
        if structure_map is None:
            return None
        try:
            return structure_map.mol_index_for_atom_uid(uid)
        except StructureMapError:
            return None
    idx = label_to_idx.get(uid)
    if idx is not None:
        return idx
    if structure_map is not None:
        try:
            return structure_map.mol_index_for_source(structure_map.source_index_for_label(uid))
        except StructureMapError:
            return None
    return None


def _resolve_signal_group_members(
    group: SignalGroup,
    structure_map: NmrStructureMap | None,
    label_to_idx: Mapping[str, int],
) -> tuple[tuple[int, float], ...] | None:
    """Resolve every member of *group*, or return ``None``.

    A signal is reconstructed from ALL its members with their coefficients or
    skipped whole — a partial member list or the representative's raw
    shielding is never substituted (G08).
    """
    if len(group.atom_uids) != len(group.coefficients):
        logger.warning(
            "SignalGroup for %s has %d atom_uids but %d coefficients; signal skipped",
            list(group.atom_uids),
            len(group.atom_uids),
            len(group.coefficients),
        )
        return None
    members: list[tuple[int, float]] = []
    for uid, coefficient in zip(group.atom_uids, group.coefficients, strict=True):
        atom_index = _conformer_index_for_signal_uid(uid, structure_map, label_to_idx)
        if atom_index is None:
            logger.warning(
                "signal group member %r of %s does not resolve in this structure; "
                "the whole signal is skipped — the representative atom is never substituted",
                uid,
                list(group.atom_uids),
            )
            return None
        members.append((atom_index, float(coefficient)))
    return tuple(members)


def _conformer_mol_block(
    structure: Structure,
    conf: ConformerShielding,
) -> str | None:
    """MolBlock of the candidate graph carrying *conf*'s coordinates.

    The ≥86-atom FCHL fragment path needs bond connectivity for the
    openbabel radius-3 BFS. The captured candidate graph is preferred, then
    the SMILES rebuild; ``None`` when no graph matches the structure's
    atom order (the caller degrades explicitly, never element-merges).
    """
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    mol = nmr_topology_mol_for(structure)
    if mol is None:
        mol = _try_build_rdkit_mol(structure)
    if mol is None or mol.GetNumAtoms() != len(structure.symbols):
        return None
    if [atom.GetSymbol() for atom in mol.GetAtoms()] != list(structure.symbols):
        return None
    coordinates = np.asarray(conf.coordinates, dtype=float)
    if coordinates.shape != (mol.GetNumAtoms(), 3):
        return None
    work = Chem.Mol(mol)
    rd_conformer = Chem.Conformer(work.GetNumAtoms())
    for index in range(work.GetNumAtoms()):
        x, y, z = (float(value) for value in coordinates[index])
        rd_conformer.SetAtomPosition(index, Point3D(x, y, z))
    work.RemoveAllConformers()
    work.AddConformer(rd_conformer, assignId=True)
    return Chem.MolToMolBlock(work)


def _compute_candidate_dp5(
    candidate: CandidateResult,
    structure: Structure,
    nmr_config: NmrConfig,
    dp5_model: Any,
) -> Dp5Outcome:
    """DP5 for one candidate via the Goodman-faithful per-conformer path.

    Goodman evaluates the KDE per conformer then averages probabilities
    (DP5.py:339-353), which differs from evaluating once on the averaged
    shielding because the KDE is nonlinear. This helper reconstructs the
    per-conformer ¹³C calc shifts aligned with the matched exp shifts from
    each assignment's :class:`SignalGroup` membership — the
    coefficient-weighted member combination, i.e. the same signal definition
    the DP4 residual was calibrated on (G08) — then calls
    :meth:`GoodmanDP5Model.probability_per_conformer`.

    When ``qml`` + the FCHL assets are available (DevDoc appendix D, P4),
    the per-atom probabilities use the FCHL-similarity weighted KDE
    (:meth:`GoodmanDP5Model.probability_per_conformer_fchl`) built from the
    conformer geometries threaded through :class:`ConformerShielding`, for
    single-member signals. A multi-member signal has no single atomic
    environment, so those candidates use the unweighted-KDE fallback —
    descriptors are never averaged for convenience (G08). Signals whose
    members do not resolve are skipped with explicit diagnostics (the
    averaged-residual path runs when nothing remains); the representative's
    raw shielding is never substituted.

    Zero complete conformers return ``status="invalid"`` (probability
    ``None``) — never a silently averaged value; exactly one conformer runs
    the same geometry-weighted pipeline with weight ``[1.0]``, so FCHL
    stays reachable. The averaged-residual path remains only for the
    no-¹³C/label-mismatch branches.

    Every path returns a frozen :class:`Dp5Outcome` — which path ran, its
    kernel and diagnostics are per-call facts, never shared model state.

    Args:
        candidate: Candidate carrying ¹³C assignments + conformer shieldings.
        structure: Candidate structure (symbols for label alignment).
        nmr_config: NMR config (TMS references).
        dp5_model: Loaded :class:`GoodmanDP5Model` (or test stand-in).

    Returns:
        Immutable outcome with ``probability``, ``mode``, ``kernel`` and
        JSON-safe ``diagnostics``.
    """
    from acp.nmr.equivalence import _build_label_index
    from acp.nmr.fchl import (
        FRAG_ATOM_THRESHOLD,
        FRAG_MAX_SIZE,
        FragmentPathStatus,
        build_atom_representations,
        build_fragment_representations,
        fragment_path_status,
        fragment_status_fallback_reason,
        kernel_backend,
    )

    symbols = list(structure.symbols)
    label_to_idx = _build_label_index(symbols)
    structure_map = nmr_structure_map_for(structure)
    tms_c = nmr_config.tms_for("13C")

    # 13C assignments give us the matched (exp_ppm, signal definition) pairs
    c_assignments = [a for a in candidate.assignments if a.element.upper() == "C"]
    if not c_assignments:
        return Dp5Outcome(
            probability=0.0,
            mode="averaged",
            diagnostics=(
                {
                    "n_conformers_used": 0,
                    "fchl_attempted": False,
                    "fallback_reason": "no_carbon",
                },
            ),
        )

    signals: list[tuple[float, tuple[tuple[int, float], ...]]] = []
    used_assignments: list[Assignment] = []
    skipped_signal_groups: list[dict[str, object]] = []
    for assignment in c_assignments:
        group = assignment.signal_group
        if group is None:
            # legacy hand-built assignment: the representative label is the
            # only member definition available
            idx = label_to_idx.get(assignment.atom_label)
            if idx is None:
                # label mismatch — fall back to averaged-residual path
                residual_by_nuc = {"13C": [a.residual for a in c_assignments]}
                return Dp5Outcome(
                    probability=compute_dp5_goodman(residual_by_nuc, dp5_model),
                    mode="averaged",
                    diagnostics=(
                        {
                            "n_conformers_used": 0,
                            "fchl_attempted": False,
                            "fallback_reason": "label_mismatch",
                        },
                    ),
                )
            signals.append((assignment.exp_ppm, ((idx, 1.0),)))
            used_assignments.append(assignment)
            continue
        if len(group.atom_uids) != len(group.coefficients):
            skipped_signal_groups.append(
                {
                    "atom_label": assignment.atom_label,
                    "atom_uids": list(group.atom_uids),
                    "reason": "length_mismatch",
                }
            )
            logger.warning(
                "DP5 candidate %s: signal %s has %d members but %d coefficients; "
                "skipping the signal (no representative substitution)",
                candidate.label,
                assignment.atom_label,
                len(group.atom_uids),
                len(group.coefficients),
            )
            continue
        members = _resolve_signal_group_members(group, structure_map, label_to_idx)
        if members is None:
            skipped_signal_groups.append(
                {
                    "atom_label": assignment.atom_label,
                    "atom_uids": list(group.atom_uids),
                    "reason": "member_unresolved",
                }
            )
            logger.warning(
                "DP5 candidate %s: signal %s (%s) has an unresolvable member; "
                "skipping the signal (no representative substitution)",
                candidate.label,
                assignment.atom_label,
                list(group.atom_uids),
            )
            continue
        signals.append((assignment.exp_ppm, members))
        used_assignments.append(assignment)

    if not signals:
        # every carbon signal is unreconstructable — typed averaged-residual
        # degradation, never a representative substitution
        residual_by_nuc = {"13C": [a.residual for a in c_assignments]}
        return Dp5Outcome(
            probability=compute_dp5_goodman(residual_by_nuc, dp5_model),
            mode="averaged",
            diagnostics=(
                {
                    "n_conformers_used": 0,
                    "fchl_attempted": False,
                    "fallback_reason": "signal_group_unresolved",
                    "skipped_signal_groups": skipped_signal_groups,
                },
            ),
        )

    exp_c = [exp_ppm for exp_ppm, _ in signals]
    fchl_indices = (
        [members[0][0] for _, members in signals]
        if all(len(members) == 1 for _, members in signals)
        else []
    )
    # per-conformer ¹³C calc shifts (TMS-converted), reconstructed from the
    # SignalGroup members with their averaging coefficients — the same linear
    # definition the DP4 residual was calibrated on (G08)
    conformer_shifts: list[list[float]] = []
    weights: list[float] = []
    conformer_reps: list[list[NDArray[np.float64]]] = []
    # FCHL training-set switch (DP5.py:57): <86 atoms use whole-molecule
    # atomic_reps; ≥86 atoms use openbabel radius-3 fragments against
    # frag_reps.gz. The fragment path is gated by the typed asset/residual
    # index correspondence (G12) — never by assuming the atomic array can be
    # swapped for the fragment array. A signal with several members has no
    # single atomic environment — FCHL is not attempted for it (no
    # uncalibrated descriptor averaging, G08).
    fchl_available = bool(getattr(dp5_model, "fchl_available", False))
    large_molecule = len(symbols) >= FRAG_ATOM_THRESHOLD
    fragment_status: FragmentPathStatus | None = None
    if fchl_available and large_molecule:
        fragment_status = fragment_path_status(getattr(dp5_model, "models_dir", None))
    fragment_mode = fragment_status is not None and fragment_status.available
    fragment_diag: dict[str, object] = (
        {"fchl_fragment_status": fragment_status.status} if fragment_status is not None else {}
    )
    fchl_requested = fchl_available and (not large_molecule or fragment_mode)
    fchl_multi_member_blocked = fchl_requested and not fchl_indices
    fchl_ok = fchl_requested and bool(fchl_indices)
    fchl_attempted = fchl_requested and not fchl_multi_member_blocked
    for conf in candidate.conformer_shieldings:
        shifts: list[float] = []
        ok = True
        for _, members in signals:
            shielding = 0.0
            for idx, coefficient in members:
                iso = _isotropic_shielding(conf.shieldings, idx)
                if iso is None:
                    ok = False
                    break
                shielding += coefficient * iso
            if not ok:
                break
            # Goodman TMS formula (NMR.py:392): δ = (σ_TMS − σ) / (1 − σ_TMS/10⁶)
            shifts.append(
                (tms_c - shielding) / (1.0 - tms_c / 1e6) if tms_c is not None else shielding
            )
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
                        if fragment_mode:
                            mol_block = _conformer_mol_block(structure, conf)
                            reps = (
                                None
                                if mol_block is None
                                else build_fragment_representations(
                                    coords,
                                    conf_symbols,
                                    fchl_indices,
                                    mol_block=mol_block,
                                    max_size=fragment_status.representation_size or FRAG_MAX_SIZE,
                                )
                            )
                        else:
                            reps = build_atom_representations(
                                coords,
                                conf_symbols,
                                fchl_indices,
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
                        if reps is None:
                            logger.warning(
                                "FCHL fragment path needs a bonded candidate graph for %s "
                                "conformer %s; degrading",
                                structure.id,
                                conf.conformer_id,
                            )
                            fchl_ok = False
                            conformer_reps = []
                        else:
                            conformer_reps.append(reps)

    if len(conformer_shifts) == 0:
        # zero complete conformers — no geometry-weighted input exists, so
        # there is nothing to rank on: typed invalid, never a silent average
        return Dp5Outcome(
            status="invalid",
            probability=None,
            mode="averaged",
            diagnostics=(
                {
                    "n_conformers_used": 0,
                    "fchl_attempted": fchl_attempted,
                    "fallback_reason": "no_complete_conformers",
                    "skipped_signal_groups": skipped_signal_groups,
                    **fragment_diag,
                },
            ),
        )

    # normalize weights (guard against drift); a single conformer gets the
    # unit weight so it runs the same geometry-weighted pipeline as the
    # multi-conformer case — FCHL stays reachable, no averaged shortcut
    if len(conformer_shifts) == 1:
        weights = [1.0]
    else:
        total_w = sum(weights)
        if total_w <= 0:
            weights = [1.0 / len(weights)] * len(weights)
        else:
            weights = [w / total_w for w in weights]

    base_diag: dict[str, object] = {
        "n_conformers_used": len(conformer_shifts),
        "fchl_attempted": fchl_attempted,
        "n_signals_used": len(signals),
        "skipped_signal_groups": skipped_signal_groups,
        **fragment_diag,
    }
    if fchl_ok and len(conformer_reps) == len(conformer_shifts):
        record: Dp5ProbabilityRecord | None = None
        atom_records: tuple[tuple[Any, ...], ...] = ()
        atom_diagnostics_method = getattr(
            dp5_model, "probability_per_conformer_fchl_atom_diagnostics", None
        )
        if callable(atom_diagnostics_method):
            fchl_diagnostics = atom_diagnostics_method(
                conformer_shifts,
                exp_c,
                weights,
                conformer_reps,
                use_fragment_reps=fragment_mode,
            )
            record = fchl_diagnostics.record
            atom_records = fchl_diagnostics.atom_records
            probability = record.probability
        elif fragment_mode:
            probability = dp5_model.probability_per_conformer_fchl(
                conformer_shifts,
                exp_c,
                weights,
                conformer_reps,
                use_fragment_reps=True,
            )
        else:
            probability = dp5_model.probability_per_conformer_fchl(
                conformer_shifts,
                exp_c,
                weights,
                conformer_reps,
            )
        atom_support_records = (
            aggregate_atom_support(used_assignments, exp_c, atom_records, weights)
            if atom_records
            else ()
        )
        return Dp5Outcome(
            probability=probability,
            mode="fchl",
            kernel=kernel_backend(),
            diagnostics=({**base_diag, "fallback_reason": None},),
            record=record,
            atom_support=atom_support_records,
        )

    if not fchl_requested:
        if fragment_status is not None:
            fallback_reason = fragment_status_fallback_reason(fragment_status.status)
        else:
            fallback_reason = "fchl_unavailable"
    elif fchl_multi_member_blocked:
        fallback_reason = "fchl_multi_member_signal"
    else:
        fallback_reason = "fchl_representations_incomplete"
    fallback_diagnostics_method = getattr(dp5_model, "probability_per_conformer_diagnostic", None)
    fallback_record: Dp5ProbabilityRecord | None = None
    if callable(fallback_diagnostics_method):
        fallback_record = fallback_diagnostics_method(conformer_shifts, exp_c, weights)
        probability = fallback_record.probability
    else:
        probability = dp5_model.probability_per_conformer(conformer_shifts, exp_c, weights)
    return Dp5Outcome(
        probability=probability,
        mode="fallback",
        diagnostics=(
            {
                **base_diag,
                "fallback_reason": fallback_reason,
            },
        ),
        record=fallback_record,
    )


# ---------------------------------------------------------------------------
# Pure-analysis stages (shared by the full run and the revision entry, todo 47)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProbabilityStage:
    """Stage-7 product: sensitivity verdict + whether the DP5 model loaded."""

    sensitivity: dict[str, Any]
    dp5_model_present: bool
    diagnostics: AtomicDiagnosticsBundle | None = None


@dataclass(frozen=True)
class _ReportStage:
    """Stage-8 product: the report object + aggregated DP5 provenance."""

    report: NmrReport
    dp5_mode: str
    fchl_kernel: str
    dp5_modes: tuple[dict[str, Any], ...]


def _score_candidate_probabilities(
    candidate_results: list[CandidateResult],
    candidates: list[Structure],
    final_shieldings_by_candidate: list[list[ConformerShielding]],
    experiment: ExperimentalNmr,
    nmr_config: NmrConfig,
    em: Any,
) -> _ProbabilityStage:
    """Stage 7: evidence gate, DP4/DP5 probabilities and the sensitivity verdict.

    Pure analysis over already-computed shieldings/residuals — never runs QC.
    Shared by the full workflow and the analysis-only revision path (todo 47).
    """
    actual_error_model = em.model_id
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
    _apply_evidence_comparability_gate(candidate_results)
    statuses = [
        cr.evidence.status if cr.evidence is not None else "valid" for cr in candidate_results
    ]
    dp4_probs = normalize_dp4_gated(log_likelihoods, statuses)

    # DP5 (G05/G07): the real Goodman KDE model only when its assets load.
    # Missing assets → status "unavailable" + probability None (never a
    # fabricated placeholder value). The placeholder path is explicit-only
    # (error_model=placeholder-*) and writes the separately-named
    # ``dp5_diagnostic_score``; DP5 needs ¹³C evidence, otherwise
    # "not_applicable" without invoking the model.
    dp5_placeholder_requested = nmr_config.error_model.startswith("placeholder")
    dp5_model = None
    dp5_unavailable_reason: str | None = None
    if not dp5_placeholder_requested:
        if not dp5_model_available():
            dp5_unavailable_reason = "dp5_model_unavailable"
        else:
            try:
                dp5_model = load_dp5_model()
            except Exception as exc:  # pragma: no cover - asset-load robustness
                logger.warning("Goodman DP5 model load failed (%s); DP5 unavailable", exc)
                dp5_unavailable_reason = "dp5_model_load_failed"
    dp5_model_id = "placeholder-dp5" if dp5_placeholder_requested else "goodman-dp5"
    dp5_model_version = (
        str(getattr(dp5_model, "model_id", "goodman-dp5"))
        if dp5_model is not None
        else actual_error_model
    )

    dp5_records: dict[int, Dp5ProbabilityRecord] = {}
    atom_support_by_candidate: dict[int, tuple[AtomDp5Support, ...]] = {}
    for cr, p4 in zip(candidate_results, dp4_probs):
        evidence_status = cr.evidence.status if cr.evidence is not None else "valid"
        exclusion_reasons = tuple(cr.evidence.exclusion_reasons) if cr.evidence is not None else ()
        if p4 is None:
            # excluded by the evidence gate — a probability is never fabricated
            cr.dp4_probability = None
            cr.dp5_probability = None
            cr.dp5_diagnostic_score = None
            cr.dp5_kernel = None
            cr.probability = CandidateProbability(
                dp4=ProbabilityResult(
                    model_id="goodman-dp4",
                    model_version=actual_error_model,
                    status=evidence_status,
                    probability=None,
                    mode=None,
                    calibration_status=evidence_status,
                    reasons=exclusion_reasons,
                ),
                dp5=ProbabilityResult(
                    model_id=dp5_model_id,
                    model_version=dp5_model_version,
                    status="unavailable",
                    probability=None,
                    mode=None,
                    calibration_status="not_evaluated",
                    reasons=exclusion_reasons or ("dp5_probability_unavailable",),
                ),
            )
            continue
        residual_by_nuc = {
            nuc: [a.residual for a in cr.assignments if _nucleus_of_element(a.element) == nuc]
            for nuc in nmr_config.nuclei
        }
        cr.dp4_probability = float(p4)
        cr.dp5_probability = None
        cr.dp5_diagnostic_score = None
        cr.dp5_kernel = None
        dp5_reasons: tuple[str, ...] = ()
        if dp5_model is not None:
            if not residual_by_nuc.get("13C"):
                # no ¹³C residuals for this candidate — DP5 does not apply
                dp5_status = "not_applicable"
                dp5_calibration = "not_evaluated"
                candidate_dp5_mode = None
                dp5_reasons = ("no_carbon_evidence",)
            else:
                outcome = _compute_candidate_dp5(cr, candidates[cr.index], nmr_config, dp5_model)
                if outcome.record is not None:
                    dp5_records[cr.index] = outcome.record
                if outcome.atom_support:
                    atom_support_by_candidate[cr.index] = outcome.atom_support
                if outcome.status == "invalid":
                    # zero complete conformers — typed invalid, never a
                    # silently averaged value; mode/kernel stay unset and a
                    # None probability already excludes the candidate
                    cr.dp5_probability = None
                    dp5_status = "invalid"
                    dp5_calibration = "not_evaluated"
                    candidate_dp5_mode = None
                    fallback_reason = (
                        outcome.diagnostics[0].get("fallback_reason")
                        if outcome.diagnostics
                        else None
                    )
                    dp5_reasons = (
                        str(fallback_reason) if fallback_reason else "no_complete_conformers",
                    )
                else:
                    cr.dp5_probability = outcome.probability
                    if cr.dp5_probability is None:
                        dp5_status = "unavailable"
                        dp5_calibration = "not_evaluated"
                        candidate_dp5_mode = None
                        dp5_reasons = ("dp5_probability_unavailable",)
                    else:
                        dp5_status = "valid"
                        dp5_calibration = "goodman_kde"
                        candidate_dp5_mode = outcome.mode
                        cr.dp5_kernel = outcome.kernel or None
        elif dp5_placeholder_requested:
            # explicit placeholder mode only — renamed to a diagnostic so it
            # can never masquerade as a probability in reports or ranking
            cr.dp5_diagnostic_score = float(
                dp5_log_to_probability(compute_dp5(residual_by_nuc, em))
            )
            dp5_status = "placeholder"
            dp5_calibration = "placeholder_parameters"
            candidate_dp5_mode = "fallback"
            dp5_reasons = ("placeholder_error_model",)
        else:
            # assets missing / load failed — report honestly, compute nothing
            dp5_status = "unavailable"
            dp5_calibration = "not_evaluated"
            candidate_dp5_mode = None
            dp5_reasons = (dp5_unavailable_reason or "dp5_model_unavailable",)
        cr.probability = CandidateProbability(
            dp4=ProbabilityResult(
                model_id="goodman-dp4",
                model_version=actual_error_model,
                status=evidence_status,
                probability=cr.dp4_probability,
                mode=None,
                calibration_status=evidence_status,
                reasons=exclusion_reasons,
            ),
            dp5=ProbabilityResult(
                model_id=dp5_model_id,
                model_version=dp5_model_version,
                status=dp5_status,
                probability=cr.dp5_probability,
                mode=candidate_dp5_mode,
                calibration_status=dp5_calibration,
                reasons=dp5_reasons,
            ),
        )
    # todo 39 (G16): atomic/signal risk diagnostics built from the same
    # residuals/records — attached per candidate and returned for the report.
    diagnostics = build_atomic_diagnostics(
        candidate_results,
        nmr_config,
        em,
        experiment=experiment,
        dp5_records=dp5_records,
        atom_support=atom_support_by_candidate,
    )
    for cr, candidate_diagnostics in zip(candidate_results, diagnostics.candidates, strict=True):
        cr.atomic_diagnostics = candidate_diagnostics
    # todo 30 / G09: winner stability under leave-one-conformer-out and
    # ±10 % temperature — a wobbling conclusion is marked, not hidden.
    sensitivity = _sensitivity_analysis(
        candidates,
        candidate_results,
        final_shieldings_by_candidate,
        experiment,
        nmr_config,
        em,
    )
    if sensitivity.get("requires_review"):
        logger.warning(
            "NMR sensitivity: conclusion marked for review (%s)",
            ", ".join(str(flag) for flag in sensitivity.get("flags", [])),
        )
    return _ProbabilityStage(
        sensitivity=sensitivity,
        dp5_model_present=dp5_model is not None,
        diagnostics=diagnostics,
    )


def _build_nmr_report(
    candidates: list[Structure],
    candidate_results: list[CandidateResult],
    nmr_config: NmrConfig,
    em: Any,
    generated_ensembles: list[bool],
    dp5_model_present: bool,
    sensitivity: dict[str, Any],
    diagnostics: AtomicDiagnosticsBundle | None = None,
) -> _ReportStage:
    """Stage 8: aggregate DP5 modes + protocol verdict into the report object.

    Pure analysis — never runs QC. Shared by the full workflow and the
    analysis-only revision path (todo 47).
    """
    actual_error_model = em.model_id
    # Per-candidate DP5 modes come from each candidate's own immutable
    # outcome (G07), never from shared model-object state.
    real_dp5: list[tuple[int, str, str | None]] = []
    for cr in candidate_results:
        prob = cr.probability
        if prob is not None and prob.dp5.status == "valid" and prob.dp5.mode is not None:
            real_dp5.append((cr.index, prob.dp5.mode, cr.dp5_kernel))
    dp5_mode_set = {mode for _, mode, _ in real_dp5}
    if not dp5_mode_set:
        dp5_mode = "fallback"
        fchl_kernel = ""
    elif len(dp5_mode_set) == 1:
        dp5_mode = next(iter(dp5_mode_set))
        kernel_set = {kernel or "" for _, _, kernel in real_dp5}
        fchl_kernel = next(iter(kernel_set)) if len(kernel_set) == 1 else ""
    else:
        dp5_mode = "mixed"
        fchl_kernel = ""
    dp5_modes = tuple(
        {"index": index, "mode": mode, "kernel": kernel} for index, mode, kernel in real_dp5
    )

    # Stage 7/8 boundary (todo 29): record what actually ran per candidate
    # and surface the protocol verdict (mode + calibration_status).
    protocol_specs = [
        _protocol_spec_for_candidate(
            nmr_config,
            structure,
            generation_executed=generated_ensembles[idx],
            error_model=actual_error_model,
            dp5_model_id=(cr.probability.dp5.model_id if cr.probability is not None else None),
            dp5_mode=cr.probability.dp5.mode if cr.probability is not None else None,
            dp5_model_present=dp5_model_present,
        )
        for idx, (structure, cr) in enumerate(zip(candidates, candidate_results, strict=True))
    ]
    protocol_block = aggregate_protocol_block(protocol_specs)
    logger.info(
        "NMR protocol verdict: mode=%s calibration_status=%s issues=%s fingerprint=%s",
        protocol_block["mode"],
        protocol_block["calibration_status"],
        protocol_block["issues"],
        protocol_block["fingerprint"],
    )
    report_config = replace(nmr_config, protocol_fingerprint=str(protocol_block["fingerprint"]))

    metadata: dict[str, Any] = {
        "n_candidates": len(candidate_results),
        "fchl_kernel": fchl_kernel,
        "protocol_id": str(protocol_block["fingerprint"]),
        "protocol": protocol_block,
        "sensitivity": sensitivity,
    }
    if diagnostics is not None:
        metadata["atomic_diagnostics"] = diagnostics.report_block()
    report = NmrReport(
        candidates=candidate_results,
        config=report_config,
        error_model=actual_error_model,
        dp5_mode=dp5_mode,
        metadata=metadata,
    )
    return _ReportStage(
        report=report,
        dp5_mode=dp5_mode,
        fchl_kernel=fchl_kernel,
        dp5_modes=dp5_modes,
    )


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
    solvent_model: str | None = None,
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
    max_conformers: int | None = None,
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
        solvent_model: ORCA solvation model for the GIAO level (``none`` =
            gas phase; falls back to config ``theory.nmr.solvent_model`` or
            ``cpcm``).
        charge / multiplicity: Per-candidate overrides.
        strict_topology: When ``True``, stage-0 parsing raises
            :class:`TopologyUnavailableError` (surfaced as a failed result)
            if any candidate has no bonded molecular graph — e.g. an XYZ
            input without an explicit charge. Defaults to ``False``. The
            same flag wires ``NmrConfig.strict_equivalence`` so equivalence
            detection also rejects (:class:`EquivalenceError`) if no graph
            is usable at analysis time (todo 4); config key
            ``nmr.strict_equivalence`` sets it independently.
        nproc: CPU core override.
        boltzmann_temp: Boltzmann-weight temperature (K).
        tms_1h / tms_13c: Override TMS reference shieldings.
        error_model: Error-model id (default ``goodman-legacy``).
        conformer_preset: CENSO preset (default ``censo-light``).
        ewin: CREST energy window (kcal/mol).
        max_conformers: Maximum conformers retained per candidate (falls
            back to config ``nmr.max_conformers`` or 10).
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
        strict_equivalence=strict_topology,
        solvent_model=solvent_model,
        max_conformers=max_conformers,
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

    # todo 29: missing-reference gate — a required nucleus without a TMS
    # reference must fail here, before any averaging: a shielding must never
    # stand in for a missing shift reference.
    missing_reference = _missing_reference_nuclei(nmr_config, candidates)
    if missing_reference:
        error = (
            "missing TMS reference for nucleus "
            + ", ".join(sorted(missing_reference))
            + " — refusing to derive shifts without a reference"
        )
        _fail_progress(progress_reporter, error)
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error=error,
        )

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
        for rank, selected in enumerate(_select_conformers(ensemble, nmr_config).selected, 1):
            generated = selected.structure
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

    selections = [_select_conformers(ensemble, nmr_config) for ensemble in resolved_ensembles]
    giao_budget = _giao_resource_budget(
        cfg,
        n_candidates=len(candidates),
        n_conformers=max((len(selection.selected) for selection in selections), default=0),
    )
    logger.info("NMR GIAO budget (候选×构象×nproc): %s", json.dumps(giao_budget, sort_keys=True))

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
                [
                    (selected.structure, selected.selected_weight, selected.delta_hartree)
                    for selected in selections[idx].selected
                ],
                nmr_config,
                giao_dir,
                cfg,
                solvent,
                budget=giao_budget,
                conformer_ids=[selected.conformer_id for selected in selections[idx].selected],
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
    final_shieldings_by_candidate: list[list[ConformerShielding]] = []
    for idx, (structure, conformer_shieldings) in enumerate(
        zip(candidates, conformer_shieldings_by_candidate, strict=True)
    ):
        omit_indices = _omit_atom_indices(
            experiment, structure, strict=nmr_config.strict_equivalence
        )
        final_shieldings, quality = _finalize_ensemble_quality(
            selections[idx],
            conformer_shieldings,
            list(structure.symbols),
            nmr_config,
            omit_indices,
        )
        if not final_shieldings:
            error = (
                f"no complete GIAO shieldings for {structure.id}: "
                f"{len(conformer_shieldings)} returned conformer(s), none complete "
                "for the required nuclei"
            )
            _fail_progress(progress_reporter, error)
            return WorkflowResult(
                status="failed",
                stages_completed=stages_completed,
                error=error,
            )
        candidate_result = _analyze_candidate(
            idx, structure, final_shieldings, experiment, nmr_config
        )
        candidate_result.ensemble_quality = quality
        candidate_results.append(candidate_result)
        final_shieldings_by_candidate.append(final_shieldings)

    stages_completed.append("giao_shielding")
    stages_completed.append("averaging_matching_scaling")
    if progress_reporter is not None:
        progress_reporter.complete_stage("boltzmann_average")
        progress_reporter.start_stage("dp4_dp5_probability")

    # Stage 7: DP4 / DP5 — evidence gate first (G05): candidates without
    # comparable valid evidence never enter the normalization.
    probability_stage = _score_candidate_probabilities(
        candidate_results,
        candidates,
        final_shieldings_by_candidate,
        experiment,
        nmr_config,
        em,
    )
    sensitivity = probability_stage.sensitivity
    stages_completed.append("probability")
    if progress_reporter is not None:
        progress_reporter.complete_stage("dp4_dp5_probability")
        progress_reporter.start_stage("nmr_report")

    # Stage 8: report — per-candidate DP5 modes come from each candidate's
    # own immutable outcome (G07), never from shared model-object state.
    report_stage = _build_nmr_report(
        candidates,
        candidate_results,
        nmr_config,
        em,
        generated_ensembles,
        probability_stage.dp5_model_present,
        sensitivity,
        probability_stage.diagnostics,
    )
    report = report_stage.report
    fchl_kernel = report_stage.fchl_kernel
    dp5_modes = list(report_stage.dp5_modes)
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
        "dp4_ranking": report.dp4_ranking,
        "candidates": [
            {
                "index": cr.index,
                "label": cr.label,
                "dp4_probability": cr.dp4_probability,
                "dp5_probability": cr.dp5_probability,
                "evidence_status": cr.evidence.status if cr.evidence is not None else None,
                # G05: typed statuses are null only when no probability block is attached
                "dp4_status": cr.probability.dp4.status if cr.probability is not None else None,
                "dp5_status": cr.probability.dp5.status if cr.probability is not None else None,
                "exclusion_reasons": list(cr.evidence.exclusion_reasons)
                if cr.evidence is not None
                else [],
            }
            for cr in candidate_results
        ],
        "error_model": actual_error_model,
        "dp5_mode": report.dp5_mode,
        "dp5_modes": dp5_modes,
        "fchl_kernel": fchl_kernel,
        "stages": stages_completed,
        "giao_resource_budget": giao_budget,
        "protocol": report.metadata.get("protocol"),
        "sensitivity": sensitivity,
        "outputs": {
            "json": str(paths["json"]),
            "xlsx": str(paths["xlsx"]) if paths["xlsx"] else None,
            "plots": [str(p) for p in paths["plots"]],
        },
    }
    (reports_dir / "nmr_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # todo 22 (gap §12.1): paths derive from the artifact's location relative
    # to RESULT/ (plots live under reports/plots/, not reports/); artifacts
    # missing on disk are NOT registered — explicit warning instead (policy).
    result_dir = storage.result_dir()
    artifact_entries: list[tuple[str, str, str, Path]] = [
        ("nmr_report", "NMR report (JSON)", "report", paths["json"]),
    ]
    if paths["xlsx"]:
        artifact_entries.append(("nmr_xlsx", "NMR assignment (XLSX)", "table", paths["xlsx"]))
    artifact_entries.extend(
        (f"plot_{index}", f"Plot {index}", "plot", plot)
        for index, plot in enumerate(paths["plots"], start=1)
    )

    products: list[dict[str, Any]] = []
    manifest_products: list[tuple[str, str, str]] = []
    for product_id, label, kind, artifact in artifact_entries:
        if not artifact.is_file():
            logger.warning("nmr product missing on disk; not registered: %s", artifact)
            continue
        rel = artifact.relative_to(result_dir).as_posix()
        manifest_products.append((product_id, label, rel))
        # result_summary.json lives at the task root, so re-root RESULT/<rel>
        products.append({"label": label, "path": f"{result_dir.name}/{rel}", "kind": kind})
    write_result_summary(output_root, workflow="nmr", products=products)

    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError:
        manifest = ResultManifest(task_id="", workflow="nmr")
    manifest.status = "completed"
    for product_id, label, rel in manifest_products:
        # manifest kind stays "report" for every nmr product (schema unchanged)
        manifest.add_product(product_id, label, rel, "report")
    manifest.write(result_dir)

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
            "giao_resource_budget": giao_budget,
            "sensitivity": sensitivity,
            "note": (
                "DP4/DP5 use placeholder error-model parameters (P1a); values are relative only."
                if actual_error_model.startswith("placeholder")
                else ""
            ),
        },
    )


def _report_relative_path(path: Path | str, reports_root: Path) -> str:
    """Report path relative to the reports root when possible (POSIX)."""
    candidate = Path(path)
    try:
        return candidate.relative_to(reports_root).as_posix()
    except ValueError:
        return str(candidate)


def revise_nmr_analysis(
    snapshot: NmrAnalysisSnapshot,
    edits: Sequence[PeakEdit],
    *,
    output_dir: str | Path | None = None,
    require_review: bool | None = None,
) -> WorkflowResult:
    """Recompute ONLY the pure-analysis stages for manually revised peaks (todo 47).

    Reuses the snapshot's cached per-conformer shieldings — no conformer
    generation, no GIAO/ORCA subprocess, no WORK/ QC artifacts. The base
    report is preserved; a NEW revision report is written under
    ``<reports>/revisions/<revision_id>/`` together with an explicit
    :class:`AnalysisRevision` record linking ``report_before`` →
    ``report_after``. A changed original-evidence hash invalidates the
    revision (:class:`EvidenceHashMismatchError`, reported as a failed
    result with ``revision_status="invalidated"``) — never a silent
    recompute on stale shieldings. When review is required (derived from
    the sensitivity verdict or forced via *require_review*), the result
    carries the EXISTING ``WAITING_REVIEW`` review-only semantics; no new
    lifecycle entry point is introduced.
    """
    try:
        actual_evidence_hash = analysis_evidence_hash(
            snapshot.candidates,
            snapshot.conformer_shieldings,
            snapshot.nmr_config,
            protocol_id=snapshot.protocol_id,
            error_model_id=snapshot.error_model_id,
            dp5_model_id=snapshot.dp5_model_id,
        )
        verify_evidence_hash(snapshot.evidence_hash, actual_evidence_hash)
    except EvidenceHashMismatchError as exc:
        logger.error("NMR analysis revision refused: %s", exc)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=str(exc),
            metadata={
                "revision_status": "invalidated",
                "expected_evidence_hash": exc.expected,
                "actual_evidence_hash": exc.actual,
            },
        )

    try:
        revised_experiment = apply_peak_edits(snapshot.experiment, edits)
    except PeakEditError as exc:
        logger.error("NMR analysis revision rejected: %s", exc)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=str(exc),
            metadata={"revision_status": "rejected"},
        )

    peak_revision = PeakRevision(
        base_digest=experiment_peak_digest(snapshot.experiment),
        revised_digest=experiment_peak_digest(revised_experiment),
        edits=tuple(edits),
        reason=next((edit.reason for edit in edits if edit.reason), ""),
    )
    revision_id = revision_identity(
        base_evidence_hash=snapshot.evidence_hash,
        peak_revision=peak_revision,
        protocol_id=snapshot.protocol_id,
        error_model_id=snapshot.error_model_id,
        dp5_model_id=snapshot.dp5_model_id,
    )
    reports_root = snapshot.base_report_path.parent
    revision_dir = (
        Path(output_dir) if output_dir is not None else reports_root / "revisions" / revision_id
    )

    nmr_config = snapshot.nmr_config
    try:
        validate_error_model_binding(nmr_config)
        em = load_error_model(nmr_config.error_model)
    except ValueError as exc:
        logger.error("NMR analysis revision failed: %s", exc)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=str(exc),
            metadata={"revision_status": "failed"},
        )

    candidates = list(snapshot.candidates)
    final_shieldings_by_candidate = [list(group) for group in snapshot.conformer_shieldings]
    candidate_results: list[CandidateResult] = []
    for idx, structure in enumerate(candidates):
        candidate_result = _analyze_candidate(
            idx, structure, final_shieldings_by_candidate[idx], revised_experiment, nmr_config
        )
        candidate_result.ensemble_quality = snapshot.ensemble_qualities[idx]
        candidate_results.append(candidate_result)

    probability_stage = _score_candidate_probabilities(
        candidate_results,
        candidates,
        final_shieldings_by_candidate,
        revised_experiment,
        nmr_config,
        em,
    )
    report_stage = _build_nmr_report(
        candidates,
        candidate_results,
        nmr_config,
        em,
        list(snapshot.generated_ensembles),
        probability_stage.dp5_model_present,
        probability_stage.sensitivity,
        probability_stage.diagnostics,
    )
    paths = write_all_reports(report_stage.report, revision_dir)
    result_identity = analysis_result_identity(report_stage.report)
    requires_review = (
        require_review
        if require_review is not None
        else bool(probability_stage.sensitivity.get("requires_review"))
    )
    revision = AnalysisRevision(
        revision_id=revision_id,
        base_evidence_hash=snapshot.evidence_hash,
        peak_revision=peak_revision,
        protocol_id=snapshot.protocol_id,
        error_model_id=snapshot.error_model_id,
        dp5_model_id=snapshot.dp5_model_id,
        report_before=_report_relative_path(snapshot.base_report_path, reports_root),
        report_after=_report_relative_path(paths["json"], reports_root),
        result_identity=result_identity,
        created_at=utc_now_iso(),
        requires_review=requires_review,
        review_status=review_only_status(requires_review) or "",
    )
    write_revision_record(revision_dir, revision, index_root=revision_dir.parent)

    report = report_stage.report
    winner = report.winner
    summary = {
        "status": "completed",
        "revision": revision.as_dict(),
        "n_candidates": len(candidate_results),
        "winner": (
            {
                "index": winner.index,
                "label": winner.label,
                "dp4": winner.dp4_probability,
                "dp5": winner.dp5_probability,
            }
            if winner is not None
            else None
        ),
        "dp4_ranking": report.dp4_ranking,
        "dp5_mode": report_stage.dp5_mode,
        "dp5_modes": list(report_stage.dp5_modes),
        "fchl_kernel": report_stage.fchl_kernel,
        "error_model": em.model_id,
        "review_required": revision.requires_review,
        "review_status": revision.review_status or None,
        "outputs": {
            "json": str(paths["json"]),
            "xlsx": str(paths["xlsx"]) if paths["xlsx"] else None,
            "plots": [str(plot) for plot in paths["plots"]],
        },
    }
    summary_path = revision_dir / "nmr_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return WorkflowResult(
        status="completed",
        stages_completed=["analysis_revision", "report"],
        metadata={
            "revision_id": revision_id,
            "revision_dir": str(revision_dir),
            "revision_report": str(paths["json"]),
            "revision_xlsx": str(paths["xlsx"]) if paths["xlsx"] else None,
            "revision_summary": str(summary_path),
            "base_report": str(snapshot.base_report_path),
            "base_evidence_hash": snapshot.evidence_hash,
            "result_identity": result_identity,
            "report_before": revision.report_before,
            "report_after": revision.report_after,
            "n_candidates": len(candidate_results),
            "winner": summary["winner"],
            "dp5_mode": report_stage.dp5_mode,
            "fchl_kernel": report_stage.fchl_kernel,
            "error_model": em.model_id,
            "review_required": revision.requires_review,
            "review_status": revision.review_status or None,
        },
    )


def _nucleus_of_element(element: str) -> str:
    sym = (element or "").strip()
    if not sym:
        return "?"
    sym = sym[:1].upper() + sym[1:].lower()
    defaults = {"H": "1H", "C": "13C", "N": "15N", "F": "19F", "P": "31P"}
    return defaults.get(sym, f"1{sym}")


__all__ = ["NMR_STAGES", "revise_nmr_analysis", "run_nmr_analysis"]
