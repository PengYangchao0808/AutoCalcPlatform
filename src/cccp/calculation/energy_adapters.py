"""Retired-engine energy-task adapters (T24).

Verbatim relocation of the science-local process seams from
``acp.workflows.energy_shared`` into the CCCP task layer: the CREST / CENSO /
Shermo / opt-freq-SP task adapters, the ACP standard rank1 handoff, and the
censo-zero xTB passthrough.  The historical ACP path re-exports the same
objects.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cccp.analysis.ensemble.levels import conformer_tag
from cccp.backends.crest import CrestBackend
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import (
    ConformerSearchOptions,
    MethodSpec,
    OptimizeOptions,
    RescueSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
    ThermochemistryOptions,
)
from cccp.calculation.results import ConformerSearchPayload, TaskResult
from cccp.calculation.tasks.censo_refine import run_censo_refine
from cccp.calculation.tasks.conformer_search import run_conformer_search
from cccp.calculation.tasks.frequency import run_frequency
from cccp.calculation.tasks.optimize import run_optimize
from cccp.calculation.tasks.singlepoint import run_singlepoint
from cccp.calculation.tasks.thermochemistry import run_thermochemistry
from cccp.qc.interfaces.censo import (
    CensoConformerRecord,
    CensoInterface,
    CensoRunResult,
    part_index,
)
from cccp.qc.translation import render_censo_template_lines
from cccp.software import get_configured_path
from cccp.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)


_FLOAT_RE = re.compile(r"[-+]?\d+\.\d+")


def _handoff_stage_dir(conf_dir: Path, stage: str, engine: str) -> Path:
    """Sibling stage dir for the same conformer (design §6: 04_FREQ/05_SP/06_THERMO).

    *conf_dir* is ``<mol>/WORK/03_OPT/<engine>/conf_NNN``; the sibling keeps
    the conf index but moves to its own stage root. Falls back to *conf_dir*
    itself when the shape does not match, so non-scheduler callers keep a
    working (if undivided) layout.
    """
    parts = conf_dir.parts
    if len(parts) >= 4 and parts[-4] == "WORK":
        work_root = conf_dir.parents[2]
        target = work_root / stage / engine / conf_dir.name
        target.mkdir(parents=True, exist_ok=True)
        return target
    return conf_dir


@dataclass(frozen=True)
class _TaskQcView:
    """QCResult-like projection of one ``TaskResult`` (legacy handoff view)."""

    success: bool
    error_message: str | None
    coordinates: Any
    symbols: list[str] | None
    log_file: Path | None
    energy: float | None


def _task_log_file(result: TaskResult, *, prefer_freq_log: bool = False) -> Path | None:
    """Return the log artifact path (freq results prefer ``frequency_log``)."""
    by_type = {artifact.type: Path(artifact.path) for artifact in result.artifacts}
    if prefer_freq_log:
        return by_type.get("frequency_log") or by_type.get("log")
    return by_type.get("log") or by_type.get("frequency_log")


def _task_qc_view(result: TaskResult, *, prefer_freq_log: bool = False) -> _TaskQcView:
    """Map one typed ``TaskResult`` onto the legacy QCResult view."""
    coordinates = (
        np.asarray(result.coordinates, dtype=float) if result.coordinates is not None else None
    )
    symbols = list(result.symbols) if result.symbols is not None else None
    return _TaskQcView(
        success=result.status == "completed",
        error_message="; ".join(result.errors) or None,
        coordinates=coordinates,
        symbols=symbols,
        log_file=_task_log_file(result, prefer_freq_log=prefer_freq_log),
        energy=result.energy_hartree,
    )


def _part_template_tokens(part_templates: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """Rendered CENSO template lines → raw per-part route keywords (D2).

    ``render_censo_template_lines`` assembles ``["! k1 k2 …"]`` from literal
    keywords, so stripping the leading ``"! "`` and splitting recovers the
    raw tokens; re-rendering them by the translation layer is idempotent for
    these literal tokens.
    """
    extras: dict[str, list[str]] = {}
    for part, lines in (part_templates or {}).items():
        tokens: list[str] = []
        for line in lines or ():
            text = str(line)
            if text.startswith("! "):
                text = text[len("! ") :]
            tokens.extend(text.split())
        if tokens:
            extras[str(part)] = tokens
    return extras


def crest_search_via_task(
    cfg: dict[str, Any],
    input_xyz: Path,
    output_dir: Path,
    *,
    charge: int,
    multiplicity: int,
    energy_window: float,
    output_name: str,
    gfn_level: int = 2,
    solvent: str | None = None,
    solvent_model: str | None = "none",
) -> Path:
    """Run one CREST conformer search via ``run_conformer_search`` (D1).

    The task core builds its backend without constructor kwargs, so the
    legacy ``gfn_level``/``solvent``/``solvent_model`` ride through the
    sanctioned ``TaskContext.backend`` runtime seam.  Returns the ensemble
    XYZ path from the payload artifact; raises ``RuntimeError`` on failure
    (the legacy ``CrestBackend.search`` contract).
    """
    crest = CrestBackend(
        config=cfg,
        gfn_level=gfn_level,
        solvent=solvent,
        solvent_model=solvent_model,
    )
    result = run_conformer_search(
        TaskRequest(
            task=TaskKind.CONFORMER_SEARCH,
            structure=StructureInput(path=Path(input_xyz)),
            charge=charge,
            multiplicity=multiplicity,
            output_dir=Path(output_dir),
            options=ConformerSearchOptions(energy_window=energy_window),
        ),
        context=TaskContext(
            backend=crest,
            config=cfg,
            capability_extras={"output_name": output_name},
        ),
    )
    payload = result.payload
    ensemble_ref = payload.ensemble_ref if isinstance(payload, ConformerSearchPayload) else None
    if result.status != "completed" or ensemble_ref is None:
        detail = "; ".join(result.errors) or "no CREST ensemble produced"
        raise RuntimeError(f"CREST search failed: {detail}")
    return Path(ensemble_ref.path)


def censo_refine_via_task(
    cfg: dict[str, Any],
    ensemble_xyz: Path,
    run_dir: Path,
    *,
    preset: str,
    charge: int,
    multiplicity: int,
    temperature: float | None = None,
    solvent: str | None = None,
    solvent_model: str | None = None,
    nproc: int | None = None,
    include_refinement: bool | None = None,
    nconf: int | None = None,
    part_overrides: dict[str, dict[str, Any]] | None = None,
    part_templates: dict[str, list[str]] | None = None,
    keep_all: bool | None = None,
) -> CensoRunResult:
    """Run one CENSO refinement via ``run_censo_refine`` (D2).

    Legacy capability kwargs ride in ``capability_extras`` verbatim except
    pre-rendered ``part_templates`` (rejected by the task): raw per-part
    tokens go under ``part_template_extras`` and are re-rendered by the
    translation layer.  The legacy ``CensoRunResult`` data contract
    (gsolv/grrho/coordinates preserved) is reconstructed from the
    final-part ``<idx>_<FINAL_PART>.json``/``.xyz`` artifacts (the
    ``censo_refine`` naming convention); missing artifacts raise
    ``RuntimeError`` like the legacy backend did.
    """
    extras: dict[str, Any] = {"preset": preset}
    for key, value in (
        ("temperature", temperature),
        ("solvent", solvent),
        ("solvent_model", solvent_model),
        ("include_refinement", include_refinement),
        ("nconf", nconf),
        ("keep_all", keep_all),
    ):
        if value is not None:
            extras[key] = value
    if part_overrides:
        extras["part_overrides"] = part_overrides
    template_extras = _part_template_tokens(part_templates)
    if template_extras:
        extras["part_template_extras"] = template_extras

    run_dir = Path(run_dir)
    result = run_censo_refine(
        TaskRequest(
            task=TaskKind.CENSO_REFINE,
            structure=StructureInput(path=Path(ensemble_xyz)),
            charge=charge,
            multiplicity=multiplicity,
            resources=TaskResources(nproc=nproc),
            output_dir=run_dir,
        ),
        context=TaskContext(config=cfg, capability_extras=extras),
    )
    metadata = result.metadata or {}
    final_part = str(metadata.get("final_part") or "")
    json_path = run_dir / f"{part_index(final_part)}_{final_part.upper()}.json"
    xyz_path = run_dir / f"{part_index(final_part)}_{final_part.upper()}.xyz"
    if not json_path.is_file() or not xyz_path.is_file():
        detail = "; ".join(result.errors) or (
            f"missing CENSO artifacts {json_path.name}/{xyz_path.name}"
        )
        raise RuntimeError(f"CENSO refine failed: {detail}")
    records = CensoInterface({}).parse_censo_json(json_path, xyz_path)
    if not records:
        detail = "; ".join(result.errors) or "no conformer records parsed"
        raise RuntimeError(f"CENSO refine failed: {detail}")
    run_result = CensoRunResult(
        preset=str(metadata.get("preset") or preset or "censo-light"),
        records=list(records),
        final_part=final_part,
        work_dir=run_dir,
        temperature=float(metadata.get("temperature_k") or temperature or 298.15),
        execution=dict(metadata.get("execution") or {}),
    )
    run_result.sort_by_gtot()
    return run_result


def shermo_via_task(
    cfg: dict[str, Any],
    *,
    freq_log: str,
    sp_energy: float,
    thermo_dir: Path,
    output_file: str,
    shermo_bin: str,
    temperature_k: float,
    pressure_atm: float,
    scl_zpe: float | None,
    ilowfreq: int | None,
    imagreal: int | None,
    conc: float | None,
) -> dict[str, Any] | None:
    """Run Shermo thermochemistry via ``run_thermochemistry`` (D4).

    Returns the legacy value dict (``g_sum``/``h_sum``/``u_sum``/
    ``s_total``/``g_conc``) or ``None`` on failure so the caller's
    ``if not raw_shermo: raise`` path fires unchanged.
    """
    result = run_thermochemistry(
        TaskRequest(
            task=TaskKind.THERMOCHEMISTRY,
            options=ThermochemistryOptions(
                freq_log_path=Path(freq_log),
                sp_energy_hartree=sp_energy,
                temperature_k=temperature_k,
                pressure_atm=pressure_atm,
                scl_zpe=scl_zpe,
                ilowfreq=ilowfreq,
                imagreal=imagreal,
                conc=conc,
            ),
            output_dir=Path(thermo_dir),
        ),
        context=TaskContext(
            config=cfg,
            capability_extras={"output_file": str(output_file), "shermo_bin": shermo_bin},
        ),
    )
    if result.status != "completed":
        return None
    metadata = result.metadata or {}
    return {key: metadata.get(key) for key in ("g_sum", "h_sum", "u_sum", "s_total", "g_conc")}


def run_rank1_handoff(
    cfg: dict[str, Any],
    coordinates: np.ndarray[Any, Any],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    work_dir: Path,
    resolved: dict[str, Any],
    solvent: str | None,
    solvent_model: str,
    index: int = 0,
    source: str = "rank1",
    sp_energy_precomputed: float | None = None,
    skip_opt_sp: bool = False,
) -> dict[str, Any]:
    """Run the ACP standard DFT handoff on one conformer.

    Enforces the v7 consistency rule: the frequency calculation uses the
    same method/basis as the geometry optimization (ORCA freq + Shermo).

    Args:
        skip_opt_sp: When True (censo-default survivors), skip the opt and
            SP steps and only run freq + Shermo on the given geometry with
            ``sp_energy_precomputed`` as electronic energy.

    Returns:
        Candidate record dict (index/coordinates/symbols/energy/gibbs/...).
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    tag = conformer_tag(source, index)

    opt_solvent = resolved.get("opt_solvent") or solvent
    opt_solvent_model = (
        resolved.get("opt_solvent_model")
        if resolved.get("opt_solvent")
        else (solvent_model if solvent else "none")
    ) or "none"
    opt_level = MethodSpec(
        method=resolved["opt_method"],
        basis=resolved["opt_basis"] or "def2-TZVPP",
        solvent=opt_solvent,
        solvent_model=opt_solvent_model if opt_solvent else "none",
    )

    if skip_opt_sp:
        opt_coords = coordinates
        opt_symbols = symbols
        sp_energy = sp_energy_precomputed
        opt_log = None
        sp_log = None
        logger.info("  [freq] same-level frequency on pre-optimized geometry")
    else:
        logger.info("  [opt] geometry optimization (%s)", resolved["opt_method"])

        opt_result = run_optimize(
            TaskRequest(
                task=TaskKind.OPTIMIZE,
                structure=StructureInput(
                    coordinates=np.asarray(coordinates, dtype=float),
                    symbols=list(symbols),
                ),
                charge=charge,
                multiplicity=multiplicity,
                level=opt_level,
                backend="orca",
                options=OptimizeOptions(rescue=RescueSpec(policy="off")),
                output_dir=work_dir,
            ),
            context=TaskContext(
                config=cfg,
                workdir=work_dir,
                capability_extras={
                    "output_name": f"{tag}_opt",
                    "route_extras": resolved.get("opt_route_extras"),
                    "geom_maxiter": resolved.get("opt_geom_maxiter"),
                    "recalc_hess": resolved.get("opt_recalc_hess"),
                },
            ),
        )
        opt_view = _task_qc_view(opt_result)
        if not opt_view.success:
            raise RuntimeError(f"rank1 geometry optimization failed: {opt_view.error_message}")
        opt_coords = opt_view.coordinates
        opt_symbols = opt_view.symbols or symbols
        opt_log = str(opt_view.log_file) if opt_view.log_file else None
        sp_energy = None
        sp_log = None

    # Frequency at the SAME method/basis as the optimization (v7 rule)
    logger.info(
        "  [freq] frequency calculation (%s, same level as opt)",
        resolved["opt_method"],
    )
    freq_dir = _handoff_stage_dir(work_dir, "04_FREQ", "ORCA")
    freq_result = run_frequency(
        TaskRequest(
            task=TaskKind.FREQUENCY,
            structure=StructureInput(
                coordinates=np.asarray(opt_coords, dtype=float),
                symbols=list(opt_symbols),
            ),
            charge=charge,
            multiplicity=multiplicity,
            level=opt_level,
            backend="orca",
            output_dir=freq_dir,
        ),
        context=TaskContext(
            config=cfg,
            workdir=freq_dir,
            capability_extras={
                "output_name": f"{tag}_freq",
                "route_extras": resolved.get("opt_freq_route_extras"),
            },
        ),
    )
    freq_view = _task_qc_view(freq_result, prefer_freq_log=True)
    if not freq_view.success:
        raise RuntimeError(f"rank1 frequency calculation failed: {freq_view.error_message}")

    if not skip_opt_sp:
        logger.info(
            "  [sp] high-level single point (%s/%s)",
            resolved["sp_method"],
            resolved["sp_basis"],
        )
        sp_solvent = resolved.get("sp_solvent") or solvent
        sp_solvent_model = (
            resolved.get("sp_solvent_model")
            if resolved.get("sp_solvent")
            else (solvent_model if solvent else "none")
        ) or "none"
        sp_dir = _handoff_stage_dir(work_dir, "05_SP", "ORCA")
        sp_result = run_singlepoint(
            TaskRequest(
                task=TaskKind.SINGLEPOINT,
                structure=StructureInput(
                    coordinates=np.asarray(opt_coords, dtype=float),
                    symbols=list(opt_symbols),
                ),
                charge=charge,
                multiplicity=multiplicity,
                level=MethodSpec(
                    method=resolved["sp_method"],
                    basis=resolved["sp_basis"] or "def2-TZVPP",
                    solvent=sp_solvent,
                    solvent_model=sp_solvent_model if sp_solvent else "none",
                ),
                backend="orca",
                output_dir=sp_dir,
            ),
            context=TaskContext(
                config=cfg,
                workdir=sp_dir,
                capability_extras={
                    "output_name": f"{tag}_sp",
                    "route_extras": resolved.get("sp_route_extras"),
                },
            ),
        )
        sp_view = _task_qc_view(sp_result)
        if not sp_view.success:
            raise RuntimeError(f"rank1 single-point failed: {sp_view.error_message}")
        sp_energy = sp_view.energy
        sp_log = str(sp_view.log_file) if sp_view.log_file else None

    if sp_energy is None:
        raise RuntimeError("No electronic energy available for Shermo handoff")

    thermo_dir = _handoff_stage_dir(work_dir, "06_THERMO", "Shermo")
    logger.info("  [thermo] thermodynamic correction")
    standard_state = _resolve_standard_state(cfg)
    raw_shermo = shermo_via_task(
        cfg,
        freq_log=str(freq_view.log_file),
        sp_energy=sp_energy,
        thermo_dir=thermo_dir,
        output_file=str(thermo_dir / f"{tag}_Shermo.sum"),
        shermo_bin=get_configured_path(cfg, "shermo"),
        temperature_k=resolved["temperature_k"],
        pressure_atm=resolved["pressure_atm"],
        scl_zpe=resolved.get("scl_zpe", 1.0),
        ilowfreq=resolved.get("ilowfreq", 0),
        imagreal=resolved.get("imagreal", 0),
        conc=resolved.get("conc"),
    )
    if not raw_shermo:
        raise RuntimeError("Shermo thermochemistry failed for rank1 conformer")
    gibbs = raw_shermo.get("g_sum")
    if gibbs is None:
        raise RuntimeError("Shermo thermochemistry failed for rank1 conformer")
    if standard_state == "1M" and raw_shermo.get("g_conc") is not None:
        gibbs = raw_shermo["g_conc"]

    return {
        "index": index,
        "coordinates": np.asarray(opt_coords),
        "symbols": list(opt_symbols),
        "energy": sp_energy,
        "gibbs": gibbs,
        "gibbs_correction": raw_shermo.get("g_sum"),
        "h_correction": raw_shermo.get("h_sum"),
        "u_correction": raw_shermo.get("u_sum"),
        "s_total": raw_shermo.get("s_total"),
        "g_conc": raw_shermo.get("g_conc"),
        "source": source,
        "opt_log": opt_log,
        "sp_log": sp_log,
    }


def _resolve_standard_state(cfg: dict[str, Any]) -> str:
    thermo = cfg.get("thermo", {})
    if isinstance(thermo, dict):
        ss = thermo.get("standard_state", "1atm")
        if isinstance(ss, str):
            return ss
    return "1atm"


def xtb_passthrough_result(
    ensemble_xyz: Path,
    temperature: float,
) -> CensoRunResult:
    """Build a CensoRunResult directly from an xTB-ranked ensemble (censo-zero).

    The censo-zero preset does not invoke CENSO (§7): the ensemble is
    exported as-is, sorted by the xTB energies parsed from the frame title
    lines. ``gsolv``/``grrho`` are zero — the ``gtot`` equals the xTB
    electronic energy.
    """
    all_coords, symbols = read_xyz_multiframe(ensemble_xyz)
    n_atoms = len(symbols)
    if n_atoms == 0:
        raise ValueError(f"No atoms found in ensemble: {ensemble_xyz}")
    n_frames = len(all_coords) // n_atoms

    energies: list[float] = []
    with open(ensemble_xyz, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    idx = 0
    while idx < len(lines) and len(energies) < n_frames:
        try:
            frame_atoms = int(lines[idx].strip())
        except ValueError:
            break
        title = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        match = _FLOAT_RE.search(title)
        energies.append(float(match.group()) if match else 0.0)
        idx += frame_atoms + 2

    if len(energies) < n_frames:
        logger.warning(
            "Parsed %d/%d title energies from %s — missing values set to 0.0",
            len(energies),
            n_frames,
            ensemble_xyz,
        )
        energies.extend([0.0] * (n_frames - len(energies)))

    records = []
    for i in range(n_frames):
        start = i * n_atoms
        records.append(
            CensoConformerRecord(
                conf_id=f"CONF{i + 1}",
                frame_index=i,
                energy=energies[i],
                gsolv=0.0,
                grrho=0.0,
                gtot=energies[i],
                coordinates=np.array(all_coords[start : start + n_atoms], dtype=float),
                symbols=list(symbols),
            )
        )

    result = CensoRunResult(
        preset="censo-zero",
        records=records,
        final_part="crest_passthrough",
        work_dir=ensemble_xyz.parent,
        temperature=temperature,
    )
    result.sort_by_gtot()
    return result

__all__ = [
    "_TaskQcView",
    "_handoff_stage_dir",
    "_part_template_tokens",
    "_resolve_standard_state",
    "_task_log_file",
    "_task_qc_view",
    "censo_refine_via_task",
    "crest_search_via_task",
    "run_rank1_handoff",
    "shermo_via_task",
    "xtb_passthrough_result",
]
