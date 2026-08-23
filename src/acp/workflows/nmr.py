# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryIsInstance=false
"""NMR + DP4/DP5 stereochemistry-assignment workflow (DevDoc §4/§5).

Orchestrates the full pipeline for each candidate:

1. conformer generation (reuse ``run_ensemble_generation`` with
   ``censo-light``);
2. per-conformer GIAO NMR shielding via the ORCA backend
   (:class:`NmrShieldingCalculator`);
3. Boltzmann + equivalence averaging;
4. assignment (assigned passthrough / unassigned Hungarian matching);
5. per-nucleus linear-regression scaling;
6. DP4 (set-normalized) and DP5 (independent) probability.

The analysis stages 3–6 are pure-Python and run on the head node; the
heavy compute (CREST/CENSO/ORCA GIAO) goes through the backend layer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.registry import get_backend
from acp.core.models import Structure, StructureEnsemble
from acp.core.workflow import WorkflowResult
from acp.io.structures import StructureReader
from acp.nmr.assignment import (
    collect_residual_inputs,
    match_assigned,
    match_unassigned,
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
from acp.storage.layout import TaskStorage
from acp.storage.manifest import ResultManifest
from acp.workflows._helpers import sanitize_job_name, write_result_summary
from cccp.config import load_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input parsing helpers
# ---------------------------------------------------------------------------


def _parse_candidates(
    input_sources: list[str],
    charge: int | None,
    multiplicity: int | None,
) -> list[Structure]:
    """Parse each input source (SMILES or XYZ path) into a :class:`Structure`."""
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
        candidates.append(
            Structure(
                id=safe,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                symbols=structure.symbols,
                coordinates=structure.coordinates,
                metadata={"source": source, **(structure.metadata or {})},
            )
        )
    return candidates


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
    """Reuse ``run_ensemble_generation`` (censo-light) for one candidate."""
    from acp.workflows.ensemble import run_ensemble_generation

    work_dir = output_dir / f"{structure.id}" / "conformers"
    result = run_ensemble_generation(
        input_source=_structure_to_xyz(structure, work_dir),
        output_dir=str(work_dir),
        preset=nmr_config.conformer_preset,
        config=cfg,
        name=structure.id,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        solvent=solvent,
        nproc=nproc,
        ewin=ewin,
    )
    if result.status != "completed" or result.ensemble is None or not result.ensemble.records:
        logger.error(
            "Conformer generation failed for %s: %s",
            structure.id,
            result.error or "no conformers produced",
        )
        return None
    return result.ensemble


def _structure_to_xyz(structure: Structure, work_dir: Path) -> str:
    """Persist a single structure to an XYZ file and return the path."""
    from cccp.utils.file_io import write_xyz

    work_dir.mkdir(parents=True, exist_ok=True)
    xyz_path = work_dir / "input.xyz"
    coords = (
        np.asarray(structure.coordinates) if structure.coordinates is not None else np.zeros((0, 3))
    )
    write_xyz(str(xyz_path), structure.symbols, coords, title=structure.id)
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

    valid = [(r, _g(r)) for r in records if _g(r) is not None]
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
    orca_backend_cls = get_backend("orca")
    orca = orca_backend_cls(
        cfg,
        method=nmr_config.nmr_method,
        basis=nmr_config.nmr_basis,
        solvent=nmr_config.solvent,
        solvent_model=nmr_config.solvent_model,
    )
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
        coords = (
            np.asarray(structure.coordinates)
            if structure.coordinates is not None
            else np.zeros((0, 3))
        )
        out_dir = giao_dir / f"conf_{idx:03d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            qc_result = orca.nmr_shielding(
                coords,
                structure.symbols,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                output_dir=out_dir,
                nuclei=target_elements,
                solvent=nmr_config.solvent,
                solvent_model=nmr_config.solvent_model,
            )
        except Exception as exc:
            logger.exception("GIAO NMR failed for conformer %d of %s: %s", idx, structure.id, exc)
            continue
        if not getattr(qc_result, "success", False):
            logger.error(
                "GIAO NMR did not converge for conformer %d of %s: %s",
                idx,
                structure.id,
                getattr(qc_result, "error_message", "unknown"),
            )
            continue
        shieldings = dict(qc_result.metadata.get("shieldings") or {})
        if not shieldings:
            logger.warning("No shieldings parsed for conformer %d of %s", idx, structure.id)
            continue
        log_file = getattr(qc_result, "log_file", None)
        results.append(
            ConformerShielding(
                conformer_id=f"conf_{idx:03d}",
                boltzmann_weight=float(weight),
                shieldings=shieldings,
                log_file=Path(log_file) if log_file else None,
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

    if experiment.assigned:
        pairs = match_assigned(atom_shifts, experiment)
    else:
        pairs = match_unassigned(atom_shifts, experiment)
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
    c_indices = [label_to_idx.get(a.atom_label) for a in c_assignments]
    if any(idx is None for idx in c_indices):
        # label mismatch — fall back to averaged-residual path
        residual_by_nuc = {"13C": [a.residual for a in c_assignments]}
        return compute_dp5_goodman(residual_by_nuc, dp5_model)

    # per-conformer ¹³C calc shifts (TMS-converted)
    conformer_shifts: list[list[float]] = []
    weights: list[float] = []
    conformer_reps: list[list[np.ndarray]] = []
    # FCHL atomic path is only valid for molecules < 86 atoms (DP5.py:57);
    # larger molecules need the openbabel fragmentation + frag_reps path
    # (not yet wired → degrade to fallback for those rare cases).
    fchl_ok = bool(getattr(dp5_model, "fchl_available", False))
    fchl_ok = fchl_ok and len(symbols) < FRAG_ATOM_THRESHOLD
    for conf in candidate.conformer_shieldings:
        shifts: list[float] = []
        ok = True
        for idx in c_indices:
            sh = conf.shieldings.get(idx)  # type: ignore[arg-type]
            if not sh or "isotropic" not in sh:
                ok = False
                break
            iso = float(sh["isotropic"])
            # Goodman TMS formula (NMR.py:392): δ = (σ_TMS − σ) / (1 − σ_TMS/10⁶)
            shifts.append((tms_c - iso) / (1.0 - tms_c / 1e6) if tms_c is not None else iso)
        if ok:
            conformer_shifts.append(shifts)
            weights.append(conf.boltzmann_weight)
            if fchl_ok:
                coords = conf.coordinates
                conf_symbols = conf.symbols
                if coords is None or not conf_symbols:
                    fchl_ok = False
                    conformer_reps = []
                else:
                    try:
                        reps = build_atom_representations(
                            coords,
                            conf_symbols,
                            c_indices,  # type: ignore[list-item]
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

    # v2 task-storage layout (design doc §5/§13): WORK/ for engine work, RESULT/ for products.
    storage = TaskStorage(output_root)
    storage.ensure_layout(stages=["02_SEARCH", "05_SP"], categories=["reports", "structures"])

    stages_completed: list[str] = []

    # Stage 0: input parsing (stage 0a = Bruker raw processing, P3)
    if (spectrum is None) == (bruker is None):
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error="exactly one of spectrum / bruker input is required",
        )
    try:
        candidates = _parse_candidates(input_sources, charge, multiplicity)
        if bruker is not None:
            experiment = _load_experiment_bruker(bruker, bruker_references, output_root)
        else:
            experiment = _load_experiment(spectrum)  # type: ignore[arg-type]
    except Exception as exc:
        logger.exception("Input parsing failed: %s", exc)
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error=f"input parsing: {exc}",
        )
    if not candidates:
        return WorkflowResult(
            status="failed",
            stages_completed=[],
            error="no candidate structures parsed",
        )

    # Stage 1 (optional): diastereomer enumeration (DevDoc §5, P2)
    if enumerate_stereoisomers:
        enum_result = _enumerate_input(input_sources, stereocenters, charge, multiplicity)
        if isinstance(enum_result, str):
            return WorkflowResult(
                status="failed",
                stages_completed=[],
                error=enum_result,
            )
        input_sources, candidates, charge = enum_result
    stages_completed.append("input_parsing")

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
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error=str(exc),
        )

    em = load_error_model(nmr_config.error_model)
    actual_error_model = em.model_id

    # Stages 2–3: conformer generation + GIAO NMR per candidate
    candidate_results: list[CandidateResult] = []
    ensembles = prebuilt_ensembles or ([None] * len(candidates))  # type: ignore[list-item]
    if len(ensembles) != len(candidates):
        return WorkflowResult(
            status="failed",
            stages_completed=stages_completed,
            error="prebuilt_ensembles length != input_sources length",
        )

    for idx, structure in enumerate(candidates):
        giao_dir = storage.stage_dir("05_SP", "ORCA") / structure.id

        if ensembles[idx] is not None and not skip_conformers:
            conformer_shieldings = _run_giao_for_conformers(
                _select_conformers(ensembles[idx], nmr_config),  # type: ignore[arg-type]
                nmr_config,
                giao_dir,
                cfg,
                solvent,
            )
        elif skip_conformers and ensembles[idx] is not None:
            # test path: shieldings already attached — skip GIAO entirely
            conformer_shieldings = getattr(ensembles[idx], "data", []) or []  # type: ignore[union-attr]
        else:
            ensemble = _run_conformer_generation(
                structure, storage.stage_dir("02_SEARCH"), nmr_config, cfg, solvent, nproc, ewin
            )
            if ensemble is None:
                return WorkflowResult(
                    status="failed",
                    stages_completed=stages_completed,
                    error=f"conformer generation failed for {structure.id}",
                )
            conformer_shieldings = _run_giao_for_conformers(
                _select_conformers(ensemble, nmr_config),
                nmr_config,
                giao_dir,
                cfg,
                solvent,
            )

        if not conformer_shieldings:
            return WorkflowResult(
                status="failed",
                stages_completed=stages_completed,
                error=f"no GIAO shieldings for {structure.id}",
            )

        candidate_results.append(
            _analyze_candidate(idx, structure, conformer_shieldings, experiment, nmr_config)
        )

    stages_completed.append("giao_shielding")
    stages_completed.append("averaging_matching_scaling")

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

    manifest = ResultManifest(task_id="", workflow="nmr", status="completed")
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


__all__ = ["run_nmr_analysis"]
