"""Retired-engine xTB-MD / GFN1 batch-opt adapters (T24).

Verbatim relocation of the science-local process seams from
``acp.workflows.xtbmd_md`` and ``acp.workflows.xtbmd_censo_energy`` into the
CCCP task layer: multi-replica MD sampling, per-frame GFN1 batch optimization,
ISOSTAT clustering, and the sampling-convergence diagnostics.  The historical
ACP path re-exports the same objects.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.analysis.sampling import equilibration_cutoff as _equilibration_cutoff
from cccp.analysis.structure.embedding import enumerate_embeddings
from cccp.analysis.xtbmd.trajectory import (
    _aligned_rmsd,
    _classify_frame_result,
    _histogram_divergence,
    _max_principal_moment,
    _parse_kcal_per_mol,
    _radius_of_gyration,
    _read_trajectory,
    _uniform_subsample_indices,
)
from cccp.backends.base import QCResult
from cccp.backends.isostat_backend import IsostatBackend
from cccp.backends.molclus_backend import MolclusBackend
from cccp.backends.xtb import XTBBackend
from cccp.calculation.context import TaskContext
from cccp.calculation.energy_adapters import _task_qc_view
from cccp.calculation.requests import (
    MdSamplingOptions,
    OptimizeOptions,
    RescueSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import MdSamplingPayload
from cccp.calculation.tasks.clustering import run_clustering
from cccp.calculation.tasks.md_sampling import run_md_sampling
from cccp.calculation.tasks.optimize import run_optimize
from cccp.utils.constants import KELVIN_TO_HARTREE
from cccp.utils.file_io import read_xyz_multiframe, write_xyz_multiframe

logger = logging.getLogger(__name__)


_K_B_HARTREE_PER_KELVIN = KELVIN_TO_HARTREE


_PRECHECK_SAMPLE_STEP = 10


_PRECHECK_MAX_SAMPLES = 100


_PRECHECK_HIST_BINS = 20


_PRECHECK_HIST_DIVERGENCE_WARN = 0.5


_MIN_SUCCESS_RATE = 0.70


_ISOMERS_ENERGIES_JSON = "isomers_energies.json"


_OPT_LEVELS: tuple[str, ...] = ("crude", "normal", "tight", "verytight")


_GFN_LEVELS: tuple[int, ...] = (0, 1, 2)


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


@dataclass(frozen=True)
class _IsostatView:
    """Legacy ``QCResult`` view over one ``run_clustering`` task result."""

    success: bool
    output_file: Path | None
    error_message: str | None


def _isostat_cluster_via_task(
    cfg: dict[str, Any],
    ensemble_xyz: Path,
    output_dir: Path,
    *,
    edis: float,
    gdis: float,
    temperature: float,
    nthreads: int = 1,
) -> _IsostatView:
    """Cluster one ensemble through the ``run_clustering`` task core (D1).

    ``IsostatBackend`` rides the sanctioned ``TaskContext.backend`` seam with
    the legacy constructor shape.  The view keeps the callers' QCResult branch
    contract: a produced ``cluster.xyz`` — even frame-less (zero clusters) —
    reads as success, so the callers' frame-count guards surface the real
    error instead of the generic failure branch.
    """
    result = run_clustering(
        TaskRequest(
            task=TaskKind.CLUSTERING,
            structure=StructureInput(path=Path(ensemble_xyz)),
            output_dir=Path(output_dir),
        ),
        context=TaskContext(
            config=cfg,
            backend=IsostatBackend(cfg),
            capability_extras={
                "edis": edis,
                "gdis": gdis,
                "temperature": temperature,
                "nthreads": nthreads,
            },
        ),
    )
    clustered = next((a for a in result.artifacts if a.type == "clustered"), None)
    output_file = Path(clustered.path) if clustered is not None else None
    message = "; ".join(result.errors) or None
    if result.status == "completed":
        return _IsostatView(True, output_file, None)
    if output_file is not None and output_file.is_file() and result.payload is None:
        return _IsostatView(True, output_file, message)
    return _IsostatView(False, output_file, message)


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
        result = _isostat_cluster_via_task(
            cfg,
            half_xyz,
            output_dir,
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
        cfg: Merged config dict for the ``XTBBackend``/``IsostatBackend``
            constructors and the task-core contexts.

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
            opt_result = run_optimize(
                TaskRequest(
                    task=TaskKind.OPTIMIZE,
                    structure=StructureInput(
                        coordinates=np.asarray(frames[selected_frames_idx[i]], dtype=float),
                        symbols=list(symbols),
                    ),
                    charge=charge,
                    multiplicity=multiplicity,
                    backend="xtb",
                    options=OptimizeOptions(rescue=RescueSpec(policy="off")),
                    output_dir=frame_dir,
                ),
                context=TaskContext(
                    config=cfg,
                    workdir=frame_dir,
                    backend=XTBBackend(
                        cfg,
                        gfn_level=gfn_level,
                        nproc=1,
                        solvent=solvent,
                        solvent_model=solvent_model,
                    ),
                    capability_extras={
                        "opt_level": opt_level,
                        "timeout": opt_timeout if opt_timeout > 0 else None,
                    },
                ),
            )
        except Exception as exc:
            # A raised backend (as opposed to a failed result) must not kill
            # the whole batch — it is isolated as one failed frame and the
            # success-rate fail-fast below decides the stage outcome.
            logger.warning("frame %d raised: %s", selected_frames_idx[i], exc)
            return i, "failed", None, f"frame {i} raised: {exc}", None
        result = _task_qc_view(opt_result)
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


def _run_md_via_task(
    md_backend: MolclusBackend,
    cfg: dict[str, Any],
    start_xyz: Path,
    output_dir: Path,
    *,
    charge: int,
    multiplicity: int,
    md_method: str,
    temperature: float,
    time_ps: float,
    dump_fs: float,
    step_fs: float,
    hmass: float,
    shake: bool,
    nvt: bool,
    seed: int,
    solvent: str | None,
    solvent_model: str,
) -> QCResult:
    """Run one xTB-MD trajectory through ``run_md_sampling`` (D4).

    The legacy ``MolclusBackend.run_md`` result shape is reconstructed from
    the typed ``TaskResult`` so the replica loop keeps its QCResult contract.
    """
    result = run_md_sampling(
        TaskRequest(
            task=TaskKind.MD_SAMPLING,
            structure=StructureInput(path=Path(start_xyz)),
            charge=charge,
            multiplicity=multiplicity,
            options=MdSamplingOptions(
                md_method=md_method,
                temperature_k=temperature,
                time_ps=time_ps,
                dump_fs=dump_fs,
                step_fs=step_fs,
                hmass=hmass,
                shake=shake,
                nvt=nvt,
                seed=seed,
            ),
            output_dir=Path(output_dir),
        ),
        context=TaskContext(
            config=cfg,
            workdir=Path(output_dir),
            backend=md_backend,
            capability_extras={"solvent": solvent, "solvent_model": solvent_model},
        ),
    )
    metadata = dict(result.metadata or {})
    payload = result.payload
    trajectory_ref = payload.trajectory_ref if isinstance(payload, MdSamplingPayload) else None
    trajectory_file = Path(trajectory_ref.path) if trajectory_ref is not None else None
    log_file = next(
        (Path(a.path) for a in result.artifacts if a.type in ("log", "frequency_log")),
        None,
    )
    if result.status == "completed":
        return QCResult(
            success=True,
            converged=True,
            output_file=trajectory_file,
            metadata=metadata,
        )
    return QCResult(
        success=False,
        error_message="; ".join(result.errors) or "MD sampling failed",
        log_file=log_file,
        metadata=metadata,
    )


def run_md_replicas(
    input_source: str | Path,
    primary_xyz: Path,
    *,
    md_seed: int = 42,
    md_seeds: int = 1,
    embed_seed_base: int = 42,
    md_method: str = "gfnff",
    temperature: float = 400.0,
    time_ps: float = 100.0,
    dump_fs: float = 100.0,
    step_fs: float = 1.0,
    hmass: float = 1.0,
    shake: bool = True,
    nvt: bool = True,
    solvent: str | None = None,
    solvent_model: str = "none",
    charge: int = 0,
    multiplicity: int = 1,
    output_dir: str | Path = "./xtbmd_output",
    config: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> QCResult:
    """Run ``md_seeds`` independent xTB MD trajectories and merge them.

    Replica *i* uses seed ``md_seed + i``.  With ``md_seeds > 1`` each
    replica starts from a different RDKit-embedded conformation of
    *input_source* (multi-start; the enumeration derives from the original
    input, not from the primary embedding), and the per-replica start
    conformation index is recorded in the result metadata.  Trajectories are
    merged into ``output_dir/traj.xyz`` preserving the original frame titles
    (which carry the GFN-FF potential energies used by the downstream
    equilibration analysis).

    Args:
        input_source: Original workflow input (SMILES or structure path) —
            the multi-start enumeration is derived from this.
        primary_xyz: The workflow's embedded single-frame XYZ (start
            structure for single-replica runs; replica 0 of multi-replica
            runs uses the enumeration, which re-derives it from the original
            input).
        md_seed: Base random seed; replica seeds increment from it.
        md_seeds: Number of replica trajectories (≥ 1).  Multi-start
            embedding only kicks in above 1.
        embed_seed_base: Base seed for the RDKit ETKDG multi-start
            enumeration.
        md_method: ``gfnff`` / ``gfn0`` / ``gfn1`` / ``gfn2``.
        temperature: Target temperature (K).
        time_ps: Simulation length (ps).
        dump_fs: Trajectory dump interval (fs).
        step_fs: Integration time step (fs).
        hmass: Hydrogen mass scaling.
        shake: Constrain X–H bonds via SHAKE.
        nvt: NVT ensemble (False selects NPT).
        solvent: Solvent name (e.g. ``water``); ``None`` for vacuum.
        solvent_model: ``alpb`` (default) or ``gbsa``.
        charge: Total charge.
        multiplicity: Spin multiplicity.
        output_dir: Output directory (merged trajectory lands in
            ``output_dir/traj.xyz``; per-replica working directories in
            ``output_dir/replica_%02d/``).  Callers pass the v2 WORK stage
            dir (``WORK/02_SEARCH/xTB``) so MD outputs stay in the task
            workspace.
        config: Backend config dict for the ``MolclusBackend`` constructor.
        timeout: Per-trajectory subprocess timeout in seconds.  ``None`` or
            ``0`` falls back to the backend default (300 s) — too short for
            production MD runs (10s–100s of ps can take minutes to hours),
            so size this from ``time_ps`` / system size.

    Returns:
        QCResult whose metadata carries ``trajectory_file`` / ``n_frames`` /
        ``md_seed`` / ``md_seeds`` / ``replica_frames`` /
        ``start_conf_index`` (per-replica starting embedding index).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_dir = out
    md_dir.mkdir(parents=True, exist_ok=True)

    if not isinstance(md_seeds, int) or isinstance(md_seeds, bool) or md_seeds < 1:
        raise ValueError(f"md_seeds must be an integer ≥ 1, got {md_seeds!r}")
    if not isinstance(md_seed, int) or isinstance(md_seed, bool) or md_seed < 0:
        raise ValueError(f"md_seed must be a non-negative integer, got {md_seed!r}")
    n_replicas = md_seeds

    starts: list[Path] = []
    start_indices: list[int] = []
    if n_replicas == 1:
        starts = [Path(primary_xyz)]
        start_indices = [0]
    else:
        embeddings = enumerate_embeddings(input_source, n=n_replicas, seed_base=embed_seed_base)
        for i, block in enumerate(embeddings):
            replica_dir = md_dir / f"replica_{i:02d}"
            replica_dir.mkdir(parents=True, exist_ok=True)
            start_xyz = replica_dir / "molecule.xyz"
            start_xyz.write_text(block, encoding="utf-8")
            starts.append(start_xyz)
            start_indices.append(i)

    backend_kwargs: dict[str, Any] = {}
    if timeout is not None and int(timeout) > 0:
        backend_kwargs["timeout"] = int(timeout)
    md_backend = MolclusBackend(config or {}, **backend_kwargs)

    replica_frames: list[int] = []
    replica_dirs: list[str] = []
    for i, (start_xyz, start_index) in enumerate(zip(starts, start_indices)):
        replica_dir = md_dir / f"replica_{i:02d}"
        replica_dirs.append(str(replica_dir))
        logger.info(
            "MD replica %d/%d seed=%d start_conf_index=%d method=%s",
            i + 1,
            n_replicas,
            md_seed + i,
            start_index,
            md_method,
        )
        result = _run_md_via_task(
            md_backend,
            config or {},
            start_xyz,
            replica_dir,
            charge=charge,
            multiplicity=multiplicity,
            md_method=md_method,
            temperature=temperature,
            time_ps=time_ps,
            dump_fs=dump_fs,
            step_fs=step_fs,
            hmass=hmass,
            shake=shake,
            nvt=nvt,
            seed=md_seed + i,
            solvent=solvent,
            solvent_model=solvent_model,
        )
        if not result.success:
            return QCResult(
                success=False,
                error_message=(
                    f"MD replica {i + 1}/{n_replicas} (seed={md_seed + i}) failed: "
                    f"{result.error_message}"
                ),
                log_file=result.log_file,
            )
        replica_frames.append(int(result.metadata.get("n_frames", 0)))

    merged_traj = md_dir / "traj.xyz"
    n_total = _merge_trajectories(
        [md_dir / f"replica_{i:02d}" / "traj.xyz" for i in range(n_replicas)],
        merged_traj,
    )

    return QCResult(
        success=True,
        converged=True,
        output_file=merged_traj,
        metadata={
            "trajectory_file": str(merged_traj),
            "n_frames": n_total,
            "md_seed": md_seed,
            "md_seeds": n_replicas,
            "replica_frames": replica_frames,
            "start_conf_index": start_indices,
            "replica_dirs": replica_dirs,
        },
    )


def _merge_trajectories(trajectories: list[Path], output: Path) -> int:
    """Concatenate replica trajectory files, preserving frame titles.

    All replica trajectories must share the same atom count (enforced by
    their common molecular graph) and contain only well-formed frames;
    violations raise before anything is written, so a failed merge never
    leaves a partial ``output`` behind (which a resume-based workflow could
    mistake for a complete checkpoint).  Returns the merged frame count.
    """
    texts: list[str] = []
    expected_atoms: int | None = None
    total_frames = 0
    for traj in trajectories:
        if not traj.exists():
            raise FileNotFoundError(f"Trajectory missing: {traj}")
        text = traj.read_text(encoding="utf-8")
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            try:
                n_atoms = int(line)
            except ValueError:
                raise ValueError(
                    f"Malformed trajectory frame in {traj} at line {i + 1}: "
                    f"{line!r} (expected an atom count)"
                ) from None
            if n_atoms <= 0 or i + 2 + n_atoms > len(lines):
                raise ValueError(
                    f"Truncated trajectory frame in {traj} at line {i + 1} "
                    f"(declared {n_atoms} atoms, {len(lines) - i - 2} lines remain)"
                )
            if expected_atoms is None:
                expected_atoms = n_atoms
            elif n_atoms != expected_atoms:
                raise ValueError(
                    f"Atom count mismatch in {traj}: frame at line {i + 1} "
                    f"has {n_atoms} atoms, expected {expected_atoms}"
                )
            total_frames += 1
            i += 2 + n_atoms
        texts.append(text)

    with open(output, "w", encoding="utf-8") as out:
        for text in texts:
            out.write(text)
            if text and not text.endswith("\n"):
                out.write("\n")
    return total_frames

__all__ = [
    "BatchOptResult",
    "_ISOMERS_ENERGIES_JSON",
    "_batch_opt_frames",
    "_conv_check_diagnostic",
    "_equilibration_cutoff",
    "_geometric_precheck",
    "_isostat_cluster_via_task",
    "_merge_trajectories",
    "_run_md_via_task",
    "_task_qc_view",
    "run_md_replicas",
]
