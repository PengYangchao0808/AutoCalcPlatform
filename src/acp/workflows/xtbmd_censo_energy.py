"""xTB-MD CENSO energy workflow — GFN1 batch optimization + orchestration.

Phase 3 implements the frame-wise GFN1-xTB geometry optimization stage of
the ``xtbmd_censo_energy`` pipeline (see docs/ACP_xTBMD_CENSO_Energy_DevDoc.html
§7): split the GFN-FF MD trajectory into frames, discard the adaptive
equilibration prefix (sliding-window ±2σ mean test, v1.3), uniformly
subsample when the frame count exceeds ``--max-frames``, optimize every
frame with GFN1-xTB in its own work directory (one process per frame,
ThreadPool ≤ nproc), and collect the optimized frames with their GFN1
energies (``isomers.xyz`` + ``isomers_energies.json`` sidecar).

Two sampling-convergence diagnostics run alongside: a zero-cost geometric
pre-check on the raw trajectory (radius-of-gyration / principal-moment
histogram divergence + sparse RMSD sampling between first and second half)
and a formal post-optimization check (per-replica second-half groups
deduplicated with ISOSTAT, cross-group representative RMSD ≤ ``conv_rmsd``,
population-weighted novelty rate vs ``conv_novelty_max``).  Both are
non-blocking warnings recorded in the returned :class:`BatchOptResult`.

Phase 4 adds the workflow orchestration on top: the ``xtbmd_censo_energy``
entry point ``run_xtbmd_censo_energy`` (doc §8) chains embed → xTB-MD
(multi-replica, RDKit multi-start) → batch_opt → ISOSTAT dedup → GFN1
energy-window filter (``ewin``) → CENSO (censo-light / censo-default /
censo-zero) → fine DFT handoff (dual mode: full-ensemble ≥99% cumulative
Boltzmann, or ``--rank1-only``) → ensemble total-Gibbs finalization, with
per-stage checkpoint fingerprints for ``--resume`` (doc §4 E1).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.censo_backend import CensoBackend
from acp.backends.registry import get_backend
from acp.core.models import HARTREE_TO_KCAL, StructureEnsemble
from acp.core.state import WorkflowState
from acp.core.workflow import WorkflowResult
from acp.io.structures import StructureReader
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
from acp.workflows.energy_shared import (
    build_result_ensemble,
    censo_record_to_candidate,
    resolve_crest_ewin,
    resolve_levels,
    resolve_solvent_config,
    run_rank1_handoff,
    select_cumulative_boltzmann,
    v2_stage_dir,
    write_final_outputs,
    xtb_passthrough_result,
)
from acp.workflows.ensemble_thermo import ensemble_total_gibbs
from acp.workflows.xtbmd_md import run_md_replicas
from cccp.config import load_config
from cccp.utils.constants import ELEMENT_MASS
from cccp.utils.file_io import read_xyz_multiframe, write_xyz_multiframe
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

#: Boltzmann constant in Hartree per Kelvin (synced with energy.py:48 and
#: ensemble_thermo.py:39).
_K_B_HARTREE_PER_KELVIN = 3.166811563e-6

#: Equilibration detection (v1.3 ±2σ sliding-window statistical test).
_EQ_WINDOW = 100
_EQ_SIGMA_MULT = 2.0
_EQ_MIN_FRAC = 0.05
_EQ_MAX_FRAC = 0.20
_EQ_FALLBACK_FRAC = 0.10

#: Geometric pre-check sampling/derived constants.
_PRECHECK_SAMPLE_STEP = 10
_PRECHECK_MAX_SAMPLES = 100
_PRECHECK_HIST_BINS = 20
_PRECHECK_HIST_DIVERGENCE_WARN = 0.5

#: Per-frame optimization failure threshold: below this success rate the
#: whole stage fails fast instead of feeding a partial ensemble downstream.
_MIN_SUCCESS_RATE = 0.70

#: Regex for the GFN-FF potential energy embedded in xTB MD trajectory frame
#: titles (``md: <time(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``); the
#: first ``(kcal/mol)`` value is the potential energy.
_KCAL_PER_MOL_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*\(kcal/mol\)")

#: Sidecar file name written next to ``isomers.xyz`` (energy window /
#: censo-zero passthrough read this preferentially; the XYZ title energy is
#: the compatibility channel).
_ISOMERS_ENERGIES_JSON = "isomers_energies.json"


@dataclass(frozen=True)
class BatchOptResult:
    """Outcome of the GFN1 batch-optimization stage.

    Attributes:
        isomers_xyz: Output multi-frame XYZ (optimized frames only, titles
            carrying the GFN1 energies in Hartree).
        isomers_energies_json: Sidecar path keyed by batch-input frame index
            (``source_frame`` maps back to the raw trajectory index).
        n_frames_raw: Trajectory frame count before equilibration.
        n_frames: Batch-input frame count (after equilibration discard and
            max_frames subsampling).
        n_ok / n_failed / n_timeout: Frame outcome counts.  Timeouts are a
            subset of failures (``n_timeout <= n_failed``).
        n_discarded_equilibration: Frames dropped by the adaptive
            equilibration test (per replica).
        discarded_equilibration_fraction: Dropped fraction (clamped into the
            fallback interval [5%, 20%]).
        conv_passed: Formal sampling-convergence verdict (``None`` when the
            diagnostic was disabled or could not run).
        conv_novelty_rate: Population-weighted novelty of the second-half
            group (``None`` when unavailable).
        conv_notes: Non-blocking diagnostic notes.
        precheck_warnings: Geometric pre-check warnings (early sampling
            coverage indicators).
    """

    isomers_xyz: Path
    isomers_energies_json: Path
    n_frames_raw: int
    n_frames: int
    n_ok: int
    n_failed: int
    n_timeout: int
    n_discarded_equilibration: int
    discarded_equilibration_fraction: float
    conv_passed: bool | None = None
    conv_novelty_rate: float | None = None
    conv_notes: list[str] = field(default_factory=list)
    precheck_warnings: list[str] = field(default_factory=list)


def _read_trajectory(traj_xyz: Path) -> tuple[list[str], list[str], list[NDArray[np.float64]]]:
    """Parse a multi-frame XYZ trajectory into symbols / titles / frames.

    Unlike :func:`cccp.utils.file_io.read_xyz_multiframe` (which drops frame
    titles and tolerates truncated rows), this reader keeps the title lines —
    they carry the GFN-FF potential energies used by the equilibration test —
    and raises on structurally inconsistent frames so a corrupt trajectory
    fails fast instead of silently shrinking the ensemble.  Malformed frame
    header lines are skipped with a warning (mirroring the cccp reader).
    """
    text = Path(traj_xyz).read_text(encoding="utf-8")
    lines = text.splitlines()
    symbols: list[str] = []
    titles: list[str] = []
    frames: list[NDArray[np.float64]] = []
    n_atoms: int | None = None
    offset = 0
    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            logger.warning("Skipping malformed frame header at line %d: %r", offset, header)
            offset += 1
            continue
        if atom_count == 0:
            if offset == 0:
                raise ValueError(f"Trajectory {traj_xyz} is empty")
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            raise ValueError(
                f"Truncated trajectory frame at line {offset} in {traj_xyz}: "
                f"declared {atom_count} atoms, {len(lines) - offset - 2} coordinate "
                f"lines remain"
            )
        if n_atoms is None:
            n_atoms = atom_count
        elif atom_count != n_atoms:
            raise ValueError(
                f"Atom count inconsistency in {traj_xyz} at line {offset}: "
                f"expected {n_atoms}, got {atom_count}"
            )
        block: list[list[float]] = []
        for line in lines[offset + 2 : end]:
            parts = line.split()
            if len(parts) < 4:
                raise ValueError(
                    f"Malformed coordinate line in {traj_xyz} at frame {len(frames)}: {line!r}"
                )
            if len(symbols) < atom_count:
                symbols.append(parts[0])
            block.append([float(parts[1]), float(parts[2]), float(parts[3])])
        frames.append(np.asarray(block, dtype=np.float64))
        titles.append(lines[offset + 1].strip())
        offset = end
    if not frames:
        raise ValueError(f"Trajectory {traj_xyz} contains no frames")
    return symbols, titles, frames


def _parse_kcal_per_mol(title: str) -> float | None:
    """Return the first ``(kcal/mol)`` value in a trajectory frame title.

    xTB MD titles carry the potential and total energy as
    ``md: <t(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``; the first
    ``(kcal/mol)`` value is the GFN-FF potential energy used by the
    equilibration test.
    """
    match = _KCAL_PER_MOL_RE.search(title)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _equilibration_cutoff(
    energies: list[float | None],
    *,
    window: int = _EQ_WINDOW,
    sigma_mult: float = _EQ_SIGMA_MULT,
    min_frac: float = _EQ_MIN_FRAC,
    max_frac: float = _EQ_MAX_FRAC,
    fallback_frac: float = _EQ_FALLBACK_FRAC,
) -> int:
    """Return the number of leading frames to discard (equilibration).

    v1.3 rule: the trajectory is divided into non-overlapping sliding windows
    of *window* frames; the last adjacent window pair whose mean difference
    exceeds ``sigma_mult`` × the pooled standard deviation marks the end of
    the non-equilibrated prefix.  The dropped fraction is clamped into the
    fallback interval ``[min_frac, max_frac]`` (a fully stable trajectory
    still drops ``min_frac``).  When energies are missing or too few for a
    window pair, ``fallback_frac`` of the frames is dropped.
    """
    n = len(energies)
    if n == 0:
        return 0
    valid = [e for e in energies if e is not None]
    if len(valid) != n or n < 2 * window:
        return int(round(fallback_frac * n))
    values = np.asarray(valid, dtype=np.float64)
    w = window
    means: list[float] = []
    stds: list[float] = []
    for start in range(0, n, w):
        segment = values[start : start + w]
        means.append(float(segment.mean()))
        stds.append(float(segment.std()))
    last_unstable = -1
    for k in range(len(means) - 1):
        pooled = math.sqrt(0.5 * (stds[k] ** 2 + stds[k + 1] ** 2)) or 1e-12
        if abs(means[k + 1] - means[k]) > sigma_mult * pooled:
            last_unstable = k
    frac = min(max((last_unstable + 1) * w / n, min_frac), max_frac)
    return int(round(frac * n))


def _uniform_subsample_indices(n_frames: int, max_frames: int) -> NDArray[np.int64]:
    """Return up to *max_frames* evenly spaced frame indices (keeps order).

    ``max_frames <= 0`` or ``n_frames <= max_frames`` returns all indices.
    """
    if max_frames <= 0 or n_frames <= max_frames:
        return np.arange(n_frames, dtype=np.int64)
    return np.unique(np.round(np.linspace(0, n_frames - 1, max_frames)).astype(np.int64))


def _aligned_rmsd(coords_a: NDArray[np.float64], coords_b: NDArray[np.float64]) -> float:
    """Return the Kabsch-aligned RMSD between two conformations."""
    aligned = GeometryUtils.align_structures(coords_a, coords_b)
    return GeometryUtils.rmsd(coords_a, aligned)


def _radius_of_gyration(coords: NDArray[np.float64], symbols: list[str]) -> float:
    """Return the mass-weighted radius of gyration (Å)."""
    com = GeometryUtils.center_of_mass(coords, symbols)
    shifted = coords - com
    masses = np.asarray([ELEMENT_MASS.get(symbol, 1.0) for symbol in symbols], dtype=np.float64)
    total = masses.sum()
    return float(math.sqrt(float((masses * np.sum(shifted**2, axis=1)).sum() / total)))


def _max_principal_moment(coords: NDArray[np.float64], symbols: list[str]) -> float:
    """Return the largest principal moment of inertia (amu·Å²)."""
    tensor = GeometryUtils.moment_of_inertia(coords, symbols)
    eigenvalues = np.linalg.eigvalsh(tensor)
    return float(eigenvalues[-1])


def _histogram_divergence(values_a: list[float], values_b: list[float], bins: int) -> float:
    """Return 1 − histogram intersection over a shared binning."""
    if not values_a or not values_b:
        return 1.0
    lo = min(min(values_a), min(values_b))
    hi = max(max(values_a), max(values_b))
    if lo == hi:
        return 0.0
    hist_a, _ = np.histogram(values_a, bins=bins, range=(lo, hi))
    hist_b, _ = np.histogram(values_b, bins=bins, range=(lo, hi))
    intersection = float(np.minimum(hist_a, hist_b).sum())
    return 1.0 - intersection / float(max(hist_a.sum(), 1))


def _geometric_precheck(
    frames: list[NDArray[np.float64]],
    symbols: list[str],
    *,
    sample_step: int = _PRECHECK_SAMPLE_STEP,
    conv_rmsd: float,
    novelty_max: float,
) -> list[str]:
    """Zero-cost sampling-coverage pre-check on the raw MD trajectory.

    Compares the first and second half of the trajectory via (a) radius of
    gyration / largest principal moment histograms (divergence > 0.5 warns)
    and (b) sparse RMSD sampling every *sample_step* frames — second-half
    sample structures with no first-half match within *conv_rmsd* count
    toward a novelty rate that warns above *novelty_max*.  Non-blocking.
    """
    warnings: list[str] = []
    n = len(frames)
    if n < 2:
        return warnings
    half = n // 2
    first = frames[:half]
    second = frames[half:]

    rg_first = [_radius_of_gyration(frame, symbols) for frame in first]
    rg_second = [_radius_of_gyration(frame, symbols) for frame in second]
    divergence = _histogram_divergence(rg_first, rg_second, _PRECHECK_HIST_BINS)
    if divergence > _PRECHECK_HIST_DIVERGENCE_WARN:
        warnings.append(
            f"MD pre-check: radius-of-gyration distribution diverges between "
            f"first/second half (histogram divergence {divergence:.2f})"
        )

    moment_first = [_max_principal_moment(frame, symbols) for frame in first]
    moment_second = [_max_principal_moment(frame, symbols) for frame in second]
    divergence = _histogram_divergence(
        [math.log10(v) for v in moment_first],
        [math.log10(v) for v in moment_second],
        _PRECHECK_HIST_BINS,
    )
    if divergence > _PRECHECK_HIST_DIVERGENCE_WARN:
        warnings.append(
            f"MD pre-check: principal-moment distribution diverges between "
            f"first/second half (histogram divergence {divergence:.2f})"
        )

    sample_first = first[::sample_step]
    sample_second = second[::sample_step]
    # Cap the pairwise comparison size: the all-pairs RMSD scan is O(s²·N) and
    # a very long trajectory (e.g. 1 fs dump) must not stall the pre-check.
    sample_first = [
        sample_first[i]
        for i in _uniform_subsample_indices(len(sample_first), _PRECHECK_MAX_SAMPLES)
    ]
    sample_second = [
        sample_second[i]
        for i in _uniform_subsample_indices(len(sample_second), _PRECHECK_MAX_SAMPLES)
    ]
    novel = sum(
        1
        for frame in sample_second
        if all(_aligned_rmsd(frame, prior) > conv_rmsd for prior in sample_first)
    )
    rate = novel / max(len(sample_second), 1)
    if rate > novelty_max:
        warnings.append(
            f"MD pre-check: {novel}/{len(sample_second)} sampled second-half "
            f"structures have no first-half match within {conv_rmsd} Å "
            f"(novelty rate {rate:.3f} > {novelty_max})"
        )
    return warnings


def _conv_check_diagnostic(
    frames: list[NDArray[np.float64]],
    symbols: list[str],
    energies: list[float | None],
    replica_frames: list[int] | None,
    *,
    edis: float,
    gdis: float,
    conv_rmsd: float,
    novelty_max: float,
    temperature_k: float,
    work_dir: Path,
    cfg: dict[str, Any],
) -> tuple[bool | None, float | None, list[str]]:
    """Formal post-optimization sampling-convergence diagnostic.

    Per-replica frames are split at the time midpoint; all first halves are
    merged into one group and all second halves into another.  Each group is
    deduplicated with ISOSTAT (same edis/gdis as the main flow); a second-half
    cluster representative is *novel* when no first-half representative lies
    within *conv_rmsd* (dedup resolution decoupled from the production gdis).
    The novelty rate weights each cluster by its Boltzmann population share
    within the second-half group (v1.3), and exceeds *novelty_max* →
    ``conv_passed=False``.  ISOSTAT failures degrade to a note with
    ``conv_passed=None``; the diagnostic never blocks the pipeline.

    Returns ``(conv_passed, conv_novelty_rate, notes)``.
    """
    notes: list[str] = []
    if not frames:
        return None, None, notes
    if not math.isfinite(temperature_k) or temperature_k <= 0:
        notes.append(f"conv-check skipped: invalid temperature_k {temperature_k}")
        return None, None, notes
    if replica_frames is not None and sum(replica_frames) != len(frames):
        notes.append(
            f"replica_frames {replica_frames} does not sum to {len(frames)} frames "
            f"— treating the trajectory as a single replica"
        )
        replica_frames = None
    segments = replica_frames or [len(frames)]

    first_halves: list[int] = []
    second_halves: list[int] = []
    offset = 0
    for length in segments:
        stop = offset + length
        midpoint = offset + length // 2
        first_halves.extend(range(offset, midpoint))
        second_halves.extend(range(midpoint, stop))
        offset = stop

    def _cluster_half(indices: list[int], label: str) -> list[NDArray[np.float64]]:
        if not indices:
            return []
        conv_dir = work_dir / "conv_check"
        conv_dir.mkdir(parents=True, exist_ok=True)
        half_xyz = conv_dir / f"{label}.xyz"
        energies_half = [energies[i] if energies[i] is not None else 0.0 for i in indices]
        write_xyz_multiframe(
            half_xyz, np.vstack([frames[i] for i in indices]), symbols, energies=energies_half
        )
        output_dir = conv_dir / label
        result = get_backend("isostat")(cfg).cluster(
            half_xyz,
            output_dir=output_dir,
            edis=edis,
            gdis=gdis,
            temperature=temperature_k,
            nthreads=1,
        )
        if not result.success or result.output_file is None or not result.output_file.exists():
            raise RuntimeError(
                f"conv-check ISOSTAT clustering of {label} half failed: "
                f"{result.error_message or 'no cluster.xyz'}"
            )
        reps, _ = read_xyz_multiframe(result.output_file)
        n_atoms = len(symbols)
        return [
            np.asarray(reps[i * n_atoms : (i + 1) * n_atoms], dtype=np.float64)
            for i in range(reps.shape[0] // n_atoms)
        ]

    first_reps: list[NDArray[np.float64]] = []
    second_reps: list[NDArray[np.float64]] = []
    try:
        first_reps = _cluster_half(first_halves, "first_half")
        second_reps = _cluster_half(second_halves, "second_half")
    except Exception as exc:
        logger.warning("conv-check diagnostic could not run: %s", exc)
        notes.append(f"conv-check diagnostic could not run: {exc}")
        return None, None, notes

    if not second_reps:
        return True, 0.0, notes
    if not first_reps:
        notes.append(
            "conv-check: no first-half representatives — all second-half clusters count as novel"
        )
        return False, 1.0, notes

    def _assign(frame: NDArray[np.float64], reps: list[NDArray[np.float64]]) -> int:
        distances = [_aligned_rmsd(frame, rep) for rep in reps]
        return int(np.argmin(distances))

    second_indices = [i for i in second_halves if energies[i] is not None]
    if not second_indices:
        return None, None, notes
    second_energies = np.asarray([energies[i] for i in second_indices], dtype=np.float64)
    kt = _K_B_HARTREE_PER_KELVIN * temperature_k
    raw = np.exp(-(second_energies - second_energies.min()) / kt)
    total = float(raw.sum())
    if total <= 0:
        return None, None, notes
    weights = raw / total

    assignments = [_assign(frames[i], second_reps) for i in second_indices]
    cluster_weights = [0.0] * len(second_reps)
    for assignment, weight in zip(assignments, weights):
        cluster_weights[assignment] += float(weight)

    novel_rate = 0.0
    novel_count = 0
    for rep, weight in zip(second_reps, cluster_weights):
        if min(_aligned_rmsd(rep, prior) for prior in first_reps) > conv_rmsd:
            novel_rate += weight
            novel_count += 1
    conv_passed = novel_rate <= novelty_max
    if not conv_passed:
        notes.append(
            f"conv-check: {novel_count} novel second-half clusters with "
            f"population-weighted rate {novel_rate:.3f} > {novelty_max} "
            f"(conv_rmsd={conv_rmsd} Å)"
        )
    return conv_passed, novel_rate, notes


def _classify_frame_result(index: int, result: Any) -> tuple[str, float | None, str | None]:
    """Classify a per-frame backend result into (status, energy, reason).

    ``ok`` requires both success and a finite energy; timeouts (detected via
    the error message) are reported as ``timeout`` so the workflow can count
    ``n_timeout`` separately; everything else is ``failed``.
    """
    if result.success and result.energy is not None and math.isfinite(float(result.energy)):
        return "ok", float(result.energy), None
    message = str(result.error_message or "") if result.error_message else ""
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout", None, message or f"frame {index} timed out"
    if result.success:
        return "failed", None, f"frame {index} optimized without a TOTAL ENERGY"
    return "failed", None, message or f"frame {index} optimization failed"


#: xTB optimization levels accepted by ``--opt`` (a catalog hint of "loose"
#: would fail per frame at the binary level — validated here up front).
_OPT_LEVELS: tuple[str, ...] = ("crude", "normal", "tight", "verytight")

#: xTB GFN levels accepted by ``--gfn``.
_GFN_LEVELS: tuple[int, ...] = (0, 1, 2)


def _batch_opt_frames(
    traj_xyz: Path,
    *,
    gfn_level: int = 1,
    opt_level: str = "normal",
    charge: int = 0,
    multiplicity: int = 1,
    nproc: int = 1,
    solvent: str | None = None,
    solvent_model: str = "none",
    max_frames: int = 500,
    opt_timeout: int = 300,
    keep_frames: bool = False,
    edis: float = 0.5,
    gdis: float = 0.25,
    replica_frames: list[int] | None = None,
    conv_check: bool = True,
    conv_rmsd: float = 0.5,
    conv_novelty_max: float = 0.10,
    temperature_k: float = 298.15,
    work_dir: Path,
    cfg: dict[str, Any],
) -> BatchOptResult:
    """Optimize every MD trajectory frame with GFN1-xTB and collect results.

    Pipeline: read the multi-frame trajectory → geometric pre-check (warnings
    only) → per-replica adaptive equilibration discard (±2σ sliding-window
    test, fallback clamped to 5%–20%) → uniform subsampling to
    ``max_frames`` (0 = unlimited) → concurrent per-frame GFN1 optimization
    (each frame in its own ``frame_%04d/`` directory, one process per frame,
    ThreadPool workers = min(nproc, n_frames), per-frame ``opt_timeout``
    treated as a failed frame) → ``isomers.xyz`` + ``isomers_energies.json``
    sidecar → success-rate fail-fast (< 70%) → formal conv-check diagnostic.

    Frame directories are removed after a successful run unless
    ``keep_frames`` is set; on fail-fast they are retained for debugging.

    Args:
        traj_xyz: Merged MD trajectory (multi-frame XYZ; titles carry the
            GFN-FF potential energies in kcal/mol).
        gfn_level: xTB GFN level for the batch optimization (default 1).
        opt_level: xTB optimization level (``normal`` / ``tight`` / ``loose``).
        charge / multiplicity: Molecular charge / spin multiplicity.
        nproc: Max concurrent frames (each frame still uses a single process).
        solvent / solvent_model: Implicit solvent applied consistently with
            the MD and CENSO stages.
        max_frames: Frame cap for the batch (0 = unlimited; uniform
            subsampling when exceeded).
        opt_timeout: Per-frame xTB timeout in seconds (0 = unlimited).
        keep_frames: Keep the per-frame working directories after success.
        edis / gdis: ISOSTAT thresholds (kcal/mol / Å) used by the conv-check
            diagnostic.
        replica_frames: Per-replica frame counts of the merged trajectory
            (equilibration and the conv-check grouping are per-replica).
        conv_check: Run the formal sampling-convergence diagnostic.
        conv_rmsd: Conv-check dedup resolution (Å), decoupled from *gdis*.
        conv_novelty_max: Population-weighted novelty cap for the second-half
            group (exceedance warns only).
        temperature_k: Temperature for the novelty Boltzmann weights.
        work_dir: Stage working directory (``frame_%04d/`` subdirectories,
            ``isomers.xyz``, sidecar, ``conv_check/``).
        cfg: Merged config dict for ``get_backend(...)``.

    Returns:
        :class:`BatchOptResult` with the output paths and all frame/diagnostic
        statistics.

    Raises:
        ValueError: Empty or corrupt trajectory, or no frames left after the
            equilibration / subsampling selection.
        RuntimeError: Frame success rate below 70 %.
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    if not Path(traj_xyz).exists():
        raise FileNotFoundError(f"Trajectory not found: {traj_xyz}")
    if gfn_level not in _GFN_LEVELS:
        raise ValueError(f"gfn_level must be one of {_GFN_LEVELS}, got {gfn_level!r}")
    if opt_level not in _OPT_LEVELS:
        raise ValueError(f"opt_level must be one of {_OPT_LEVELS}, got {opt_level!r}")

    symbols, titles, frames = _read_trajectory(traj_xyz)
    n_frames_raw = len(frames)
    logger.info("batch_opt: %d raw trajectory frames", n_frames_raw)

    precheck_warnings: list[str] = []
    if conv_check:
        precheck_warnings = _geometric_precheck(
            frames,
            symbols,
            conv_rmsd=conv_rmsd,
            novelty_max=conv_novelty_max,
        )
        for warning in precheck_warnings:
            logger.warning("%s", warning)

    if replica_frames is not None:
        if sum(replica_frames) != n_frames_raw:
            logger.warning(
                "replica_frames %s does not sum to %d raw frames — treating the "
                "trajectory as a single replica",
                replica_frames,
                n_frames_raw,
            )
            replica_frames = None
    segments = replica_frames or [n_frames_raw]

    energies_raw = [_parse_kcal_per_mol(title) for title in titles]
    kept: list[int] = []
    kept_segments: list[int] = []
    offset = 0
    n_discarded = 0
    for length in segments:
        segment_energies = energies_raw[offset : offset + length]
        cutoff = _equilibration_cutoff(segment_energies)
        n_discarded += cutoff
        kept_segments.append(length - cutoff)
        kept.extend(range(offset + cutoff, offset + length))
        offset += length

    selected = _uniform_subsample_indices(len(kept), max_frames)
    selected_frames_idx = [kept[i] for i in selected]
    n_selected = len(selected_frames_idx)
    if n_selected == 0:
        raise ValueError(
            f"batch_opt: no frames left after equilibration discard "
            f"({n_discarded} of {n_frames_raw} dropped) and max_frames "
            f"subsampling — check the MD output and --max-frames"
        )
    discarded_frac = n_discarded / n_frames_raw if n_frames_raw else 0.0
    logger.info(
        "batch_opt: %d frames after equilibration discard (%.1f%%) + %s subsampling "
        "→ %d to optimize",
        n_discarded,
        discarded_frac * 100.0,
        f"uniform({max_frames})" if len(selected) < len(kept) else "no",
        n_selected,
    )

    workers = max(1, min(nproc, n_selected))
    statuses: list[str] = [""] * n_selected
    energies: list[float | None] = [None] * n_selected
    optimized_coords: list[NDArray[np.float64] | None] = [None] * n_selected

    def _optimize_one(
        i: int,
    ) -> tuple[int, str, float | None, str | None, NDArray[np.float64] | None]:
        frame_dir = work / f"frame_{i:04d}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        try:
            backend = get_backend("xtb")(
                cfg,
                gfn_level=gfn_level,
                nproc=1,
                solvent=solvent,
                solvent_model=solvent_model,
            )
            result = backend.optimize(
                frames[selected_frames_idx[i]],
                symbols,
                charge=charge,
                multiplicity=multiplicity,
                output_dir=frame_dir,
                opt_level=opt_level,
                timeout=opt_timeout if opt_timeout > 0 else None,
            )
        except Exception as exc:
            # A raised backend (as opposed to a failed QCResult) must not kill
            # the whole batch — it is isolated as one failed frame and the
            # success-rate fail-fast below decides the stage outcome.
            logger.warning("frame %d raised: %s", selected_frames_idx[i], exc)
            return i, "failed", None, f"frame {i} raised: {exc}", None
        status, energy, reason = _classify_frame_result(i, result)
        coords = (
            np.asarray(result.coordinates, dtype=np.float64)
            if result.success and result.coordinates is not None
            else None
        )
        return i, status, energy, reason, coords

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_optimize_one, i): i for i in range(n_selected)}
        for future in as_completed(futures):
            i, status, energy, reason, coords = future.result()
            statuses[i] = status
            energies[i] = energy
            optimized_coords[i] = coords if status == "ok" else None
            if status != "ok":
                logger.warning(
                    "frame %d (%s): %s",
                    selected_frames_idx[i],
                    status,
                    reason or "unknown failure",
                )

    n_ok = sum(1 for status in statuses if status == "ok")
    n_timeout = sum(1 for status in statuses if status == "timeout")
    n_failed = n_selected - n_ok
    success_rate = n_ok / n_selected if n_selected else 0.0
    logger.info(
        "batch_opt: %d/%d frames optimized (%.1f%%), %d failed (%d timeouts)",
        n_ok,
        n_selected,
        success_rate * 100.0,
        n_failed,
        n_timeout,
    )

    sidecar = work / _ISOMERS_ENERGIES_JSON
    sidecar.write_text(
        json.dumps(
            {
                "gfn_level": gfn_level,
                "units": "hartree",
                "temperature_k": temperature_k,
                "frames": [
                    {
                        "frame": i,
                        "source_frame": selected_frames_idx[i],
                        "status": statuses[i],
                        "energy": energies[i],
                    }
                    for i in range(n_selected)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if success_rate < _MIN_SUCCESS_RATE:
        raise RuntimeError(
            f"batch_opt fail-fast: only {n_ok}/{n_selected} frames optimized "
            f"successfully ({success_rate:.1%} < {_MIN_SUCCESS_RATE:.0%}); "
            f"frame directories kept for debugging in {work}"
        )

    isomers_xyz = work / "isomers.xyz"
    if any(coords is not None for coords in optimized_coords):
        ok_coords = np.vstack([coords for coords in optimized_coords if coords is not None])
        ok_energies = [e for e in energies if e is not None]
        write_xyz_multiframe(isomers_xyz, ok_coords, symbols, energies=ok_energies)

    if not keep_frames:
        for i in range(n_selected):
            shutil.rmtree(work / f"frame_{i:04d}", ignore_errors=True)

    conv_passed: bool | None = None
    conv_novelty_rate: float | None = None
    conv_notes: list[str] = []
    if conv_check:
        ok_indices = [i for i in range(n_selected) if statuses[i] == "ok"]
        if ok_indices:
            cumulative = 0
            ok_replica_counts: list[int] = []
            for length in kept_segments:
                count = sum(
                    1 for i in ok_indices if cumulative <= selected[i] < cumulative + length
                )
                ok_replica_counts.append(count)
                cumulative += length
            ok_frames = [frames[selected_frames_idx[i]] for i in ok_indices]
            ok_energies = [energies[i] for i in ok_indices]
            conv_passed, conv_novelty_rate, conv_notes = _conv_check_diagnostic(
                ok_frames,
                symbols,
                ok_energies,
                ok_replica_counts,
                edis=edis,
                gdis=gdis,
                conv_rmsd=conv_rmsd,
                novelty_max=conv_novelty_max,
                temperature_k=temperature_k,
                work_dir=work,
                cfg=cfg,
            )
            for note in conv_notes:
                logger.warning("%s", note)
            if conv_passed is False:
                logger.warning("conv_check: sampling-convergence diagnostic FAILED (not blocking)")

    return BatchOptResult(
        isomers_xyz=isomers_xyz,
        isomers_energies_json=sidecar,
        n_frames_raw=n_frames_raw,
        n_frames=n_selected,
        n_ok=n_ok,
        n_failed=n_failed,
        n_timeout=n_timeout,
        n_discarded_equilibration=n_discarded,
        discarded_equilibration_fraction=discarded_frac,
        conv_passed=conv_passed,
        conv_novelty_rate=conv_novelty_rate,
        conv_notes=conv_notes,
        precheck_warnings=precheck_warnings,
    )


# ---------------------------------------------------------------------------
# Phase 4: workflow orchestration (doc §8)
# ---------------------------------------------------------------------------

_ENERGY_PRESETS = ("censo-light", "censo-default", "censo-zero")

#: Base seed for the RDKit ETKDG multi-start enumeration (doc §4 v1.3);
#: replica trajectories start from distinct embedded conformations when
#: ``md_seeds > 1``.
_EMBED_SEED_BASE = 42

#: Max RMSD (Å) for the energy-window geometric sidecar lookup — ISOSTAT
#: cluster representatives are members of the isomers set, so a well-mapped
#: frame matches near zero; beyond this the title energy (compat channel)
#: is preferred.
_SIDECAR_MATCH_TOLERANCE_ANGSTROM = 0.5

#: Title-format regexes for the energy-window filter output.  The rewritten
#: titles carry the GFN1 energy in Hartree, first float; the compat channel
#: (``xtb_passthrough_result``) parses exactly that first float.
_TITLE_ENERGY_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


@dataclass(frozen=True)
class EnergyFilterResult:
    """Outcome of the GFN1 energy-window filter (doc §8.3)."""

    ensemble_xyz: Path
    ensemble_energies_json: Path
    n_total: int
    n_after_filter: int


def _stage_fingerprint(params: dict[str, Any]) -> str:
    """Stable SHA-256 of a stage's input-parameter set (E1)."""
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes (stage-input fingerprint)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _checkpoint_path(stage_dir: Path) -> Path:
    return Path(stage_dir) / "checkpoint.json"


def _write_stage_checkpoint(
    stage_dir: Path,
    stage: str,
    params: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    """Persist a stage checkpoint (fingerprint + summary for metadata)."""
    stage_dir = Path(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "stage": stage,
        "fingerprint": _stage_fingerprint(params),
        "params": params,
        "summary": summary,
    }
    _checkpoint_path(stage_dir).write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _resume_or_rerun(
    stage_dir: Path,
    stage: str,
    params: dict[str, Any],
    *,
    resume: bool,
    products: Path | list[Path],
) -> dict[str, Any] | None:
    """Return the stored stage summary when the stage can be skipped.

    Skip only when ``--resume`` is on, the checkpoint fingerprint matches
    the current parameter set, and every product file exists.  A checkpoint
    with a *mismatched* fingerprint raises (parameter change detected —
    refuse to silently reuse stale products; the user must re-run without
    ``--resume`` or clear the stage directory).  Returns ``None`` when the
    stage must re-run.
    """
    if not resume:
        return None
    checkpoint = _checkpoint_path(stage_dir)
    if not checkpoint.exists():
        return None
    try:
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{stage}: resume checkpoint unreadable ({checkpoint}): {exc}") from exc
    if record.get("stage") != stage or record.get("fingerprint") != _stage_fingerprint(params):
        raise RuntimeError(
            f"{stage}: resume checkpoint fingerprint mismatch — the products in "
            f"{stage_dir} were produced with different parameters. Re-run without "
            f"--resume, or clear the stage directory before resuming."
        )
    product_list = [products] if isinstance(products, Path) else list(products)
    missing = [p for p in product_list if not Path(p).exists()]
    if missing:
        logger.info(
            "%s: checkpoint fingerprint matches but product %s is missing — re-running",
            stage,
            missing[0],
        )
        return None
    logger.info("%s: resume — checkpoint fingerprint matches; reusing products", stage)
    return record.get("summary") or {}


def _resolve_md_timeout(md_timeout: int | None, time_ps: float) -> int:
    """Size the per-MD subprocess timeout.

    Explicit ``md_timeout > 0`` wins; otherwise estimate from the simulation
    length (≈ 1 min per ps, minimum 1 h) so production 100 ps runs are not
    killed by the 300 s backend default (doc §16 risk 1).
    """
    if md_timeout is not None and int(md_timeout) > 0:
        return int(md_timeout)
    return max(3600, int(60 * float(time_ps)))


def _batch_result_summary(result: BatchOptResult) -> dict[str, Any]:
    """Serialize a BatchOptResult for the stage checkpoint / metadata."""
    return {
        "n_frames_raw": result.n_frames_raw,
        "n_frames": result.n_frames,
        "n_ok": result.n_ok,
        "n_failed": result.n_failed,
        "n_timeout": result.n_timeout,
        "n_discarded_equilibration": result.n_discarded_equilibration,
        "discarded_equilibration_fraction": result.discarded_equilibration_fraction,
        "conv_passed": result.conv_passed,
        "conv_novelty_rate": result.conv_novelty_rate,
        "conv_notes": list(result.conv_notes),
        "precheck_warnings": list(result.precheck_warnings),
    }


def _parse_title_energy_hartree(title: str) -> float | None:
    """Return the first float in a frame title (GFN1 Hartree energy)."""
    match = _TITLE_ENERGY_RE.search(title)
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def _match_isomers_frame(
    frame: NDArray[np.float64],
    iso_stack: NDArray[np.float64],
    tolerance: float,
) -> int | None:
    """Return the isomers frame index that *frame* was copied from.

    ISOSTAT cluster representatives are verbatim members of the isomers
    set, so a vectorized unaligned-RMSD scan finds the source frame
    directly (O(n_atoms) per pair, no SVD); a Kabsch-aligned scan is the
    fallback for symmetry-rotated copies (rare).  ``None`` when no isomers
    frame lies within *tolerance*.
    """
    plain = np.sqrt(np.mean(np.sum((iso_stack - frame[None, :, :]) ** 2, axis=2), axis=1))
    nearest = int(np.argmin(plain))
    if plain[nearest] <= tolerance:
        return nearest
    aligned = [_aligned_rmsd(frame, ref) for ref in iso_stack]
    nearest = int(np.argmin(aligned))
    return nearest if aligned[nearest] <= tolerance else None


def _filter_energy_window(
    cluster_xyz: Path,
    isomers_xyz: Path,
    isomers_energies_json: Path,
    *,
    ewin: float,
    work_dir: Path,
) -> EnergyFilterResult:
    """Filter the ISOSTAT cluster to the GFN1 relative-energy window.

    Reads the cluster representatives plus their GFN1 energies — the
    sidecar (keyed by isomers frame index) is the primary channel and the
    XYZ title the compatibility channel (doc §8.3).  Each cluster frame is
    matched geometrically to its nearest isomers frame (aligned RMSD; ISOSTAT
    representatives are members of the isomers set) and the sidecar energy
    is looked up; frames without a sidecar match fall back to their title
    energy.  Frames within ``ewin`` kcal/mol of the cluster minimum are
    written to ``ensemble_xyz`` with rewritten titles (GFN1 Hartree energy
    embedded, first float — the censo-zero passthrough parses exactly this)
    plus the rebuilt ``ensemble_energies.json`` (cluster order, original
    frame index preserved).

    Raises:
        RuntimeError: No cluster frames, no GFN1 energy recoverable for a
            frame, or an empty filtered ensemble (fail-fast, E5).
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    try:
        symbols, cluster_titles, cluster_frames = _read_trajectory(cluster_xyz)
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            f"energy_filter: ISOSTAT cluster unreadable/empty ({exc}) — "
            f"relax --edis/--gdis or check the batch-optimization output"
        ) from exc
    if not cluster_frames:
        raise RuntimeError(
            "energy_filter: ISOSTAT produced 0 cluster representatives — "
            "relax --edis/--gdis or check the batch-optimization output"
        )

    iso_symbols, iso_titles, iso_frames = _read_trajectory(isomers_xyz)
    if len(symbols) != len(iso_symbols):
        raise RuntimeError(
            f"energy_filter: atom count mismatch between {cluster_xyz} "
            f"({len(symbols)}) and {isomers_xyz} ({len(iso_symbols)})"
        )

    try:
        sidecar = json.loads(Path(isomers_energies_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "energy_filter: isomers energy sidecar unreadable (%s) — "
            "falling back to the title channel",
            exc,
        )
        sidecar = {}
    sidecar_map: dict[int, float] = {}
    for entry in sidecar.get("frames", []):
        if entry.get("status") == "ok" and entry.get("energy") is not None:
            try:
                sidecar_map[int(entry["frame"])] = float(entry["energy"])
            except (TypeError, ValueError):
                continue

    iso_stack = np.stack(iso_frames) if iso_frames else None
    records: list[tuple[int, int | None, float]] = []
    for i, (frame, title) in enumerate(zip(cluster_frames, cluster_titles)):
        nearest: int | None = None
        energy: float | None = None
        if iso_stack is not None:
            nearest = _match_isomers_frame(
                frame,
                iso_stack,
                _SIDECAR_MATCH_TOLERANCE_ANGSTROM,
            )
            if nearest is not None:
                # source_frame is recorded only for a geometrically verified
                # match; title-fallback frames keep it None (unknown).
                energy = sidecar_map.get(nearest)
        if energy is None:
            energy = _parse_title_energy_hartree(title)
        if energy is None:
            raise RuntimeError(
                f"energy_filter: no GFN1 energy recoverable for cluster frame {i} — "
                f"sidecar lookup and title parsing both failed"
            )
        records.append((i, nearest, float(energy)))

    energies = np.asarray([energy for _, _, energy in records], dtype=np.float64)
    relative_kcal = (energies - energies.min()) * HARTREE_TO_KCAL
    kept = [
        (i, source_frame, energy, rel)
        for (i, source_frame, energy), rel in zip(records, relative_kcal)
        if rel <= ewin
    ]
    if not kept:
        raise RuntimeError(
            f"energy_filter: 0 conformers within the {ewin} kcal/mol energy window "
            f"(cluster minimum {energies.min():.8f} Ha) — raise --ewin or relax "
            f"--edis/--gdis"
        )

    ensemble_xyz = work / "ensemble_xyz"
    # NOTE: no blank lines between frames — the censo-zero passthrough
    # (xtb_passthrough_result) scans frame blocks by strict offset.
    with open(ensemble_xyz, "w", encoding="utf-8") as f:
        for j, (i, _source_frame, energy, _rel) in enumerate(kept):
            f.write(f"{len(symbols)}\n")
            f.write(f"Frame {i:04d} E={energy:.8f} Hartree\n")
            for sym, coord in zip(symbols, cluster_frames[i]):
                f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

    ensemble_energies_json = work / "ensemble_energies.json"
    ensemble_energies_json.write_text(
        json.dumps(
            {
                "units": "hartree",
                "ewin_kcal_mol": float(ewin),
                "frames": [
                    {
                        "frame": j,
                        "source_frame": source_frame,
                        "energy": energy,
                        "status": "ok",
                    }
                    for j, (_i, source_frame, energy, _rel) in enumerate(kept)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    logger.info(
        "energy_filter: %d/%d cluster conformers within %.1f kcal/mol window → %s",
        len(kept),
        len(records),
        ewin,
        ensemble_xyz,
    )
    return EnergyFilterResult(
        ensemble_xyz=ensemble_xyz,
        ensemble_energies_json=ensemble_energies_json,
        n_total=len(records),
        n_after_filter=len(kept),
    )


def _count_xyz_frames_strict(path: Path) -> int:
    """Return the frame count of a multi-frame XYZ (raises when malformed)."""
    _, _, frames = _read_trajectory(path)
    return len(frames)


def run_xtbmd_censo_energy(
    input_source: str,
    output_dir: str | Path = "./xtbmd_censo_energy_output",
    preset: str = "censo-light",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    solvent: str | None = None,
    nproc: int | None = None,
    no_opt: bool = False,
    levels: dict[str, Any] | None = None,
    threshold: float | None = None,
    ewin: float | None = None,
    rank1_only: bool = False,
    resume: bool = False,
    *,
    md_temperature: float = 400.0,
    md_time_ps: float = 100.0,
    md_dump_fs: float = 100.0,
    md_step_fs: float = 1.0,
    md_hmass: float = 1.0,
    md_shake: bool = True,
    md_nvt: bool = True,
    md_seed: int = 42,
    md_seeds: int = 1,
    md_method: str = "gfnff",
    md_timeout: int | None = None,
    conv_check: bool = True,
    conv_novelty_max: float = 0.10,
    conv_rmsd: float = 0.5,
    max_frames: int = 500,
    opt_gfn_level: int = 1,
    opt_level: str = "normal",
    opt_timeout: int = 300,
    keep_frames: bool = False,
    edis: float = 0.5,
    gdis: float = 0.25,
) -> WorkflowResult:
    """Run the xTB-MD conformer-search free-energy workflow.

    Pipeline (doc §1.2 / §8): embed → GFN-FF MD sampling (400 K / 100 ps,
    ``md_seeds`` replicas, multi-start RDKit embeddings) → GFN1-xTB
    per-frame batch optimization (adaptive equilibration discard,
    ``max_frames`` uniform subsampling, per-frame timeout, sidecar
    energies, two conv-check diagnostics) → ISOSTAT dedup → GFN1
    energy-window filter (``ewin``, kcal/mol — GFN1-window semantics,
    distinct from the CREST window of ``acp run energy``) → CENSO
    (censo-light / censo-default / censo-zero) → fine DFT handoff (dual
    mode: full ensemble ≥99% cumulative Boltzmann, or ``--rank1-only``)
    → ensemble total Gibbs finalization (``RESULT/energies/ensemble_thermo.json``).

    ``--resume`` skips stages whose checkpoint fingerprint (full input
    parameter set per stage) still matches and whose products exist; a
    fingerprint mismatch raises instead of silently reusing stale products
    (doc §4 E1).

    Args:
        input_source: SMILES string or structure file path.
        output_dir: Output root (per-molecule subdirectory is created).
        preset: censo-light (default) / censo-default / censo-zero.
        config: Optional config dict.
        name: Molecule name.
        charge / multiplicity: Molecular charge / spin multiplicity.
        solvent: Solvent name (consistent across MD, batch opt, CENSO).
        nproc: Max concurrent frame optimizations.
        no_opt: Skip the fine DFT handoff; CENSO/xTB refinement is final.
        levels: Method level overrides (censo / dft_opt / refinement_sp /
            screening_sp / thermo / refinement_threshold).
        threshold: Cumulative Boltzmann population threshold (0<v<=1,
            default 0.99).
        ewin: GFN1 relative-energy window in kcal/mol (default 6.0).
            Priority: explicit argument > ``levels.censo.ewin`` >
            ``censo.ewin`` config > 6.0.
        rank1_only: Fine DFT only on the CENSO/xTB rank1 conformer; the
            ensemble total G uses the full screening weight table
            (G_total = G₁ + kT·ln p₁).
        resume: Skip stages with matching checkpoint fingerprints.
        md_temperature: MD target temperature (K).
        md_time_ps: MD length (ps).
        md_dump_fs: Trajectory dump interval (fs).
        md_step_fs: Integration step (fs).
        md_hmass: Hydrogen mass scaling.
        md_shake: Constrain X–H bonds via SHAKE.
        md_nvt: NVT ensemble (False → NPT).
        md_seed: Base random seed (replica seeds increment).
        md_seeds: Replica count (each replica starts from a distinct RDKit
            embedding when > 1).
        md_method: ``gfnff`` (default) / ``gfn0`` / ``gfn1`` / ``gfn2``.
        md_timeout: Per-MD subprocess timeout (s; 0/None → estimate).
        conv_check: Run the sampling-convergence diagnostics.
        conv_novelty_max: Population-weighted second-half novelty cap.
        conv_rmsd: Conv-check dedup RMSD (Å), decoupled from *gdis*.
        max_frames: Batch-opt frame cap (0 = unlimited; uniform
            subsampling when exceeded).
        opt_gfn_level: GFN level for the batch optimization (default 1).
        opt_level: xTB optimization level (normal / tight / loose).
        opt_timeout: Per-frame xTB timeout (s; 0 = unlimited).
        keep_frames: Keep per-frame working directories.
        edis / gdis: ISOSTAT energy (kcal/mol) / structure RMSD (Å)
            dedup thresholds.

    Returns:
        WorkflowResult whose ensemble holds the fine-DFT (or CENSO/xTB)
        refined conformers; ``metadata`` carries the ensemble total Gibbs,
        the stage statistics (n_frames/n_ok/n_failed/n_timeout/
        n_after_isostat/n_after_filter) and the conv-check verdict.
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

    resolved = resolve_levels(cfg, levels)
    threshold = float(resolved["refinement_threshold"])

    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        reader = StructureReader()
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

    mol_dir = resolve_task_output_root(output_root, safe_name)
    state = WorkflowState(mol_dir, safe_name)
    state.initialize(
        input_source=input_source,
        stage_names=[
            "embed",
            "xtbmd",
            "batch_opt",
            "isostat",
            "energy_filter",
            "censo",
            "dft_handoff",
            "finalize",
            "conformer_energy",
        ],
    )

    # Solvent priority: CLI --solvent > levels (UI wizard fields) > YAML.
    # The resolved solvent is applied consistently to MD, batch opt, ISOSTAT
    # and CENSO (doc §4 E6).
    effective_solvent_arg = solvent if solvent is not None else resolved["levels_solvent"]
    censo_solvent, solvent_model = resolve_solvent_config(cfg, effective_solvent_arg)
    if censo_solvent and resolved["levels_solvent_model"]:
        solvent_model = resolved["levels_solvent_model"]
    _solvent_model = solvent_model if solvent_model else "none"
    if censo_solvent and _solvent_model == "none":
        _solvent_model = "smd"

    safe_nproc: int | None = None
    if nproc is not None and nproc > 0:
        safe_nproc = nproc
    # Batch-opt concurrency falls back to the config resource default so a
    # CLI run without --nproc does not silently degrade to single-frame
    # optimization (CENSO/xTB backends already default from the config).
    batch_nproc = safe_nproc or int(cfg.get("resources", {}).get("nproc") or 1)

    temperature_k = float(resolved["temperature_k"])
    ewin_eff = resolve_crest_ewin(
        cfg,
        ewin if ewin is not None else resolved["crest_ewin_level"],
    )
    logger.info("GFN1 energy window (post-optimization): %.2f kcal/mol", ewin_eff)

    stages_completed: list[str] = ["embed"]

    try:
        # ------------------------------------------------------------------ MD --
        xtbmd_dir = v2_stage_dir(mol_dir, "02_SEARCH", "xTB")
        embed_xyz = v2_stage_dir(mol_dir, "01_PREPARE") / "embed.xyz"
        with open(embed_xyz, "w", encoding="utf-8") as f:
            f.write(f"{len(structure.symbols)}\n")
            f.write(f"Embedded input for {safe_name}\n")
            for sym, coord in zip(structure.symbols, structure.coordinates):
                f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

        md_params: dict[str, Any] = {
            "md_method": md_method,
            "temperature": md_temperature,
            "time_ps": md_time_ps,
            "dump_fs": md_dump_fs,
            "step_fs": md_step_fs,
            "hmass": md_hmass,
            "shake": md_shake,
            "nvt": md_nvt,
            "md_seed": md_seed,
            "md_seeds": md_seeds,
            "embed_seed_base": _EMBED_SEED_BASE,
            # The embedded start structure itself: an RDKit/embedding-version
            # change must invalidate the cached trajectory too (embed.xyz is
            # rewritten before the fingerprint check).
            "embed_xyz_sha256": _file_sha256(embed_xyz),
            "solvent": censo_solvent,
            "solvent_model": _solvent_model,
            "charge": structure.charge,
            "multiplicity": structure.multiplicity,
            "input_source": input_source,
        }
        traj_xyz = xtbmd_dir / "traj.xyz"
        md_summary = _resume_or_rerun(
            xtbmd_dir,
            "xtbmd",
            md_params,
            resume=resume,
            products=traj_xyz,
        )
        if md_summary is None:
            state.set_stage("xtbmd")
            md_result = run_md_replicas(
                input_source,
                embed_xyz,
                md_seed=md_seed,
                md_seeds=md_seeds,
                embed_seed_base=_EMBED_SEED_BASE,
                md_method=md_method,
                temperature=md_temperature,
                time_ps=md_time_ps,
                dump_fs=md_dump_fs,
                step_fs=md_step_fs,
                hmass=md_hmass,
                shake=md_shake,
                nvt=md_nvt,
                solvent=censo_solvent,
                solvent_model=_solvent_model,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                output_dir=xtbmd_dir,
                config=cfg,
                timeout=_resolve_md_timeout(md_timeout, md_time_ps),
            )
            if not md_result.success:
                raise RuntimeError(f"xTB-MD sampling failed: {md_result.error_message}")
            md_meta = md_result.metadata
            md_summary = {
                "n_frames_raw": md_meta.get("n_frames"),
                "replica_frames": md_meta.get("replica_frames"),
                "start_conf_index": md_meta.get("start_conf_index"),
                "md_seed": md_meta.get("md_seed"),
                "md_seeds": md_meta.get("md_seeds"),
            }
            _write_stage_checkpoint(xtbmd_dir, "xtbmd", md_params, md_summary)
            state.complete_stage(
                "xtbmd",
                {"status": "completed", "n_frames": md_meta.get("n_frames")},
            )
        else:
            state.complete_stage(
                "xtbmd",
                {"status": "resumed", "n_frames": md_summary.get("n_frames_raw")},
            )
        stages_completed.append("xtbmd")

        # ---------------------------------------------------------- batch_opt --
        batch_dir = v2_stage_dir(mol_dir, "03_OPT", "xTB")
        # Fingerprint = the full stage parameter set (doc §4 E1): the opt
        # engine parameters + the conv-check controls + temperature (they
        # shape the isomers products and the diagnostic verdicts).  edis/gdis
        # are deliberately excluded — a threshold change must only re-run
        # ISOSTAT (its own fingerprint), matching the documented resume
        # semantics (§12.2); the conv-check's use of them is advisory-only.
        opt_params: dict[str, Any] = {
            "gfn_level": opt_gfn_level,
            "opt_level": opt_level,
            "charge": structure.charge,
            "multiplicity": structure.multiplicity,
            "solvent": censo_solvent,
            "solvent_model": _solvent_model,
            "max_frames": max_frames,
            "opt_timeout": opt_timeout,
            "conv_check": conv_check,
            "conv_rmsd": conv_rmsd,
            "conv_novelty_max": conv_novelty_max,
            "temperature_k": temperature_k,
            "traj_sha256": _file_sha256(traj_xyz),
        }
        isomers_xyz = batch_dir / "isomers.xyz"
        isomers_energies_json = batch_dir / _ISOMERS_ENERGIES_JSON
        batch_summary = _resume_or_rerun(
            batch_dir,
            "batch_opt",
            opt_params,
            resume=resume,
            products=[isomers_xyz, isomers_energies_json],
        )
        if batch_summary is None:
            state.set_stage("batch_opt")
            batch_result = _batch_opt_frames(
                traj_xyz,
                gfn_level=opt_gfn_level,
                opt_level=opt_level,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                nproc=batch_nproc,
                solvent=censo_solvent,
                solvent_model=_solvent_model,
                max_frames=max_frames,
                opt_timeout=opt_timeout,
                keep_frames=keep_frames,
                edis=edis,
                gdis=gdis,
                replica_frames=md_summary.get("replica_frames"),
                conv_check=conv_check,
                conv_rmsd=conv_rmsd,
                conv_novelty_max=conv_novelty_max,
                temperature_k=temperature_k,
                work_dir=batch_dir,
                cfg=cfg,
            )
            batch_summary = _batch_result_summary(batch_result)
            _write_stage_checkpoint(batch_dir, "batch_opt", opt_params, batch_summary)
            state.complete_stage(
                "batch_opt",
                {
                    "status": "completed",
                    "n_ok": batch_summary["n_ok"],
                    "n_failed": batch_summary["n_failed"],
                    "conv_passed": batch_summary.get("conv_passed"),
                },
            )
        else:
            state.complete_stage(
                "batch_opt",
                {
                    "status": "resumed",
                    "n_ok": batch_summary.get("n_ok"),
                    "conv_passed": batch_summary.get("conv_passed"),
                },
            )
        stages_completed.append("batch_opt")

        # ------------------------------------------------------------- isostat --
        isostat_dir = v2_stage_dir(mol_dir, "02_SEARCH", "ISOSTAT")
        cluster_xyz = isostat_dir / "cluster.xyz"
        iso_params: dict[str, Any] = {
            "edis": edis,
            "gdis": gdis,
            "temperature_k": temperature_k,
            "input_sha256": _file_sha256(isomers_xyz),
        }
        isostat_summary = _resume_or_rerun(
            isostat_dir,
            "isostat",
            iso_params,
            resume=resume,
            products=cluster_xyz,
        )
        if isostat_summary is None:
            state.set_stage("isostat")
            isostat_result = get_backend("isostat")(cfg).cluster(
                isomers_xyz,
                output_dir=isostat_dir,
                edis=edis,
                gdis=gdis,
                temperature=temperature_k,
                nthreads=1,
            )
            if (
                not isostat_result.success
                or isostat_result.output_file is None
                or not Path(isostat_result.output_file).exists()
            ):
                raise RuntimeError(
                    f"ISOSTAT clustering failed: "
                    f"{isostat_result.error_message or 'no cluster.xyz produced'}"
                )
            try:
                n_after_isostat = _count_xyz_frames_strict(cluster_xyz)
            except (ValueError, OSError) as exc:
                raise RuntimeError(
                    f"ISOSTAT clustering produced no readable clusters "
                    f"({exc}) — relax --edis/--gdis or check the batch-optimization "
                    f"output"
                ) from exc
            if n_after_isostat == 0:
                raise RuntimeError(
                    "ISOSTAT clustering produced 0 clusters — relax --edis/--gdis "
                    "or check the batch-optimization output"
                )
            isostat_summary = {"n_after_isostat": n_after_isostat}
            _write_stage_checkpoint(isostat_dir, "isostat", iso_params, isostat_summary)
            state.complete_stage(
                "isostat",
                {"status": "completed", "n_clusters": n_after_isostat},
            )
        else:
            state.complete_stage(
                "isostat",
                {"status": "resumed", "n_clusters": isostat_summary.get("n_after_isostat")},
            )
        stages_completed.append("isostat")

        # ------------------------------------------------------ energy_filter --
        state.set_stage("energy_filter")
        filter_result = _filter_energy_window(
            cluster_xyz,
            isomers_xyz,
            isomers_energies_json,
            ewin=ewin_eff,
            work_dir=v2_stage_dir(mol_dir, "03_OPT", "xTB") / "energy_filter",
        )
        ensemble_xyz = filter_result.ensemble_xyz
        state.complete_stage(
            "energy_filter",
            {
                "status": "completed",
                "n_after_filter": filter_result.n_after_filter,
                "ewin": ewin_eff,
            },
        )
        stages_completed.append("energy_filter")

        # -------------------------------------------------------------- censo --
        candidates: list[dict[str, Any]]
        # Workflow-2 (rank1_only) state: full screening-table weights drive
        # p₁/S_mix while the fine DFT G₁ enters G_total = G₁ + kT·ln p₁.
        external_weights: dict[str, float] | None = None
        external_total_gibbs: float | None = None
        external_total_gibbs_censo: float | None = None
        population_weights: dict[str, float] | None = None
        external_table_source = "censo"

        def _handoff_selected(selected: list[Any]) -> list[dict[str, Any]]:
            """Run the ACP handoff on each selected conformer (v15 ensemble).

            Individual failures beyond rank1 are logged and skipped so a
            single bad conformer does not discard the rest of the ensemble;
            an empty result raises.
            """
            results: list[dict[str, Any]] = []
            for i, rec in enumerate(selected):
                handoff_dir = v2_stage_dir(mol_dir, "03_OPT", "ORCA") / f"conf_{i:03d}"
                try:
                    cand = run_rank1_handoff(
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

        if preset == "censo-zero" and opt_enabled:
            # xTB-ranked ensemble → cumulative Boltzmann selection → ACP
            # handoff for each survivor (no CENSO CLI involved)
            passthrough = xtb_passthrough_result(ensemble_xyz, temperature_k)
            if rank1_only:
                rank1 = passthrough.records[0]
                logger.info(
                    "censo-zero (opt on, rank1-only): fine DFT handoff on %s only",
                    rank1.conf_id,
                )
                state.set_stage("dft_handoff")
                cand = run_rank1_handoff(
                    cfg,
                    np.asarray(rank1.coordinates),
                    list(rank1.symbols),
                    structure.charge,
                    structure.multiplicity,
                    v2_stage_dir(mol_dir, "03_OPT", "ORCA") / "conf_000",
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
                p1 = external_weights.get(rank1.conf_id, 0.0)
                external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
            else:
                selected = select_cumulative_boltzmann(
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
                state.set_stage("dft_handoff")
                candidates = _handoff_selected(selected)
                state.complete_stage("dft_handoff", {"status": "completed"})
                stages_completed.append("dft_handoff")
                population_weights = passthrough.boltzmann_weights()

        else:
            # All remaining paths invoke CENSO
            censo_dir = v2_stage_dir(mol_dir, "02_SEARCH", "CENSO")
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
                    ensemble_xyz,
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

                if rank1_only:
                    rank1 = censo_result.records[0]
                    logger.info(
                        "censo-light (opt on, rank1-only): fine DFT handoff on %s only",
                        rank1.conf_id,
                    )
                    state.set_stage("dft_handoff")
                    cand = run_rank1_handoff(
                        cfg,
                        np.asarray(rank1.coordinates),
                        list(rank1.symbols),
                        structure.charge,
                        structure.multiplicity,
                        v2_stage_dir(mol_dir, "03_OPT", "ORCA") / "conf_000",
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
                    p1 = external_weights.get(rank1.conf_id, 0.0)
                    external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                    external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
                else:
                    selected = select_cumulative_boltzmann(
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
                    passthrough = xtb_passthrough_result(ensemble_xyz, temperature_k)
                    nconf = 1
                    logger.info(
                        "%s (opt off, rank1-only): CENSO refinement on 1 frame, "
                        "p table from xTB ensemble",
                        preset,
                    )
                else:
                    nconf = None
                    if preset == "censo-zero":
                        passthrough = xtb_passthrough_result(ensemble_xyz, temperature_k)
                        preselected = select_cumulative_boltzmann(
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
                    ensemble_xyz,
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

                if rank1_only:
                    rank1 = censo_result.records[0]
                    candidates = [censo_record_to_candidate(rank1, index=0)]
                    external_weights = passthrough.boltzmann_weights()
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
                    selected = select_cumulative_boltzmann(
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
                        censo_record_to_candidate(rec, index=i) for i, rec in enumerate(selected)
                    ]
                    population_weights = censo_result.boltzmann_weights()

            else:
                # censo-default: full Part0–Part3 + same-level freq + Shermo
                logger.info("censo-default: full CENSO Part0–Part3 funnel")
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    ensemble_xyz,
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
                    state.set_stage("dft_handoff")
                    try:
                        cand = run_rank1_handoff(
                            cfg,
                            np.asarray(rank1.coordinates),
                            list(rank1.symbols),
                            structure.charge,
                            structure.multiplicity,
                            v2_stage_dir(mol_dir, "03_OPT", "ORCA") / "conf_000",
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
                        cand = censo_record_to_candidate(rank1, index=0)
                    candidates = [cand]
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    external_weights = censo_result.boltzmann_weights()
                    p1 = external_weights.get(rank1.conf_id, 0.0)
                    external_total_gibbs = ensemble_total_gibbs(cand["gibbs"], p1, temperature_k)
                    external_total_gibbs_censo = ensemble_total_gibbs(rank1.gtot, p1, temperature_k)
                else:
                    state.set_stage("dft_handoff")
                    candidates = []
                    for i, rec in enumerate(censo_result.records):
                        handoff_dir = v2_stage_dir(mol_dir, "03_OPT", "ORCA") / f"conf_{i:03d}"
                        try:
                            cand = run_rank1_handoff(
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
                            cand = censo_record_to_candidate(rec, index=i)
                        candidates.append(cand)
                    state.complete_stage("dft_handoff", {"status": "completed"})
                    stages_completed.append("dft_handoff")
                    population_weights = censo_result.boltzmann_weights()
        # ------------------------------------------------------------ finalize --
        state.set_stage("finalize")
        outputs = write_final_outputs(
            candidates,
            mol_dir,
            safe_name,
            temperature_k,
            external_weights=external_weights,
            external_total_gibbs=external_total_gibbs,
            external_total_gibbs_censo=external_total_gibbs_censo,
            population_weights=population_weights,
            external_table_source=external_table_source,
        )
        ensemble = build_result_ensemble(candidates, structure)
        state.complete_stage("finalize", {"n_conformers": len(candidates)})
        stages_completed.append("finalize")

    except Exception as exc:
        logger.exception("xTB-MD CENSO energy workflow failed: %s", exc)
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
        "ewin": ewin_eff,
        "md_seed": md_summary.get("md_seed", md_seed),
        "md_seeds": md_summary.get("md_seeds", md_seeds),
        "start_conf_index": md_summary.get("start_conf_index"),
        "n_frames_raw": batch_summary.get("n_frames_raw"),
        "n_frames": batch_summary.get("n_frames"),
        "n_ok": batch_summary.get("n_ok"),
        "n_failed": batch_summary.get("n_failed"),
        "n_timeout": batch_summary.get("n_timeout"),
        "n_after_isostat": isostat_summary.get("n_after_isostat"),
        "n_after_filter": filter_result.n_after_filter,
        "conv_novelty_rate": batch_summary.get("conv_novelty_rate"),
        "conv_passed": batch_summary.get("conv_passed"),
        "ensemble_xyz": str(ensemble_xyz),
        "ensemble_energies_json": str(filter_result.ensemble_energies_json),
        **outputs,
    }

    return WorkflowResult(
        status="completed",
        ensemble=ensemble,
        stages_completed=stages_completed,
        metadata=metadata,
    )


__all__ = ["BatchOptResult", "_batch_opt_frames", "run_xtbmd_censo_energy"]
