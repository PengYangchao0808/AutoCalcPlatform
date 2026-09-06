"""MD sampling-history pipeline for Confsearch.

Parses xTB-MD trajectory frames, detects the equilibration prefix,
clusters conformations into geometric basins via greedy plain-RMSD,
projects basin distances to 2D via classical MDS, and computes
sampling-saturation metrics.  The resulting
:class:`SamplingHistory` is persisted as ``RESULT/confsearch/sampling_history.json``
(schema ``sampling_history_v1``) by the Confsearch engine finalize hook
for ``xtb-md`` / ``xtbmd-censo`` protocols only.

Data models, serialization, and persistence helpers live in
``acp.confsearch.sampling_models`` and are re-exported here so that
existing ``from acp.confsearch.sampling import ...`` imports keep
working.

Third-party dependency: **numpy only** (no rdkit, sklearn, etc.).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .sampling_models import (
    _KCAL_PER_MOL_RE,
    _MD_PREFIX_RE,
    _SAMPLING_SCHEMA_VERSION,
    MDS_COORD_TYPE,
    BasinInfo,
    SamplingHistory,
    SamplingSaturation,
    TrajFrame,
    load_sampling_history,
    read_traj_frame_xyz,
    write_sampling_history,
)
from .shared.deduplication import plain_rmsd

logger = logging.getLogger(__name__)

__all__ = [
    "BasinInfo",
    "MDS_COORD_TYPE",
    "SamplingHistory",
    "SamplingSaturation",
    "TrajFrame",
    "assign_basins",
    "compute_sampling_history",
    "equilibration_cutoff",
    "load_sampling_history",
    "mds_2d",
    "parse_traj_frames",
    "read_traj_frame_xyz",
    "write_sampling_history",
]

# Equilibration detection parameters — re-implements the ±2σ sliding-window
# statistical test from ``xtbmd_censo_energy._equilibration_cutoff`` (line 228).
# Import was evaluated but rejected: the source module carries heavy transitive
# deps (rdkit, cccp backends, conftest) that would bloat this lightweight module.
_EQ_WINDOW = 100
_EQ_SIGMA_MULT = 2.0
_EQ_MIN_FRAC = 0.05
_EQ_MAX_FRAC = 0.20
_EQ_FALLBACK_FRAC = 0.10


# ---------------------------------------------------------------------------
# Trajectory parser
# ---------------------------------------------------------------------------


def parse_traj_frames(traj_path: Path) -> list[TrajFrame]:
    """Parse a multi-frame XYZ trajectory into :class:`TrajFrame` objects.

    Frame titles are expected to follow the xTB-MD convention:
    ``md: <t(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``

    Supports both 4-part (``C x y z``) and 5-part (``0 C x y z``) XYZ
    coordinate lines.  The parser tries ``parts[1]`` as the first
    coordinate; if that fails it tries ``parts[2]`` (leading index).

    Malformed titles produce frames with ``energy_kcal_mol=None`` and
    ``time_ps=None`` (no crash).  An empty file returns an empty list.
    """
    text = Path(traj_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    frames: list[TrajFrame] = []
    symbols: list[str] = []
    n_atoms: int | None = None
    skip_counter = 0
    offset = 0

    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            offset += 1
            continue
        if atom_count == 0:
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            break
        title = lines[offset + 1].strip()

        # Parse symbols (first frame only)
        if n_atoms is None:
            n_atoms = atom_count
            for line in lines[offset + 2 : end]:
                parts = line.split()
                if parts:
                    symbols.append(parts[0])

        # Parse coordinates — support 4-part and 5-part XYZ lines
        coords_list: list[list[float]] = []
        for line in lines[offset + 2 : end]:
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                coords_list.append(
                    [float(parts[1]), float(parts[2]), float(parts[3])]
                )
            except ValueError:
                if len(parts) >= 5:
                    try:
                        coords_list.append(
                            [float(parts[2]), float(parts[3]), float(parts[4])]
                        )
                    except ValueError:
                        skip_counter += 1
                else:
                    skip_counter += 1

        # Parse title
        energy: float | None = None
        time_ps: float | None = None
        m = _KCAL_PER_MOL_RE.search(title)
        if m is not None:
            try:
                energy = float(m.group(1))
            except ValueError:
                skip_counter += 1
        else:
            skip_counter += 1

        tm = _MD_PREFIX_RE.match(title)
        if tm is not None:
            try:
                time_ps = float(tm.group(1))
            except ValueError:
                pass

        frames.append(TrajFrame(
            index=len(frames),
            time_ps=time_ps,
            step=len(frames),
            energy_kcal_mol=energy,
            symbols=list(symbols),
            coords=(
                np.asarray(coords_list, dtype=np.float64)
                if coords_list
                else np.empty((0, 3))
            ),
        ))
        offset = end

    if skip_counter:
        logger.debug(
            "Skipped %d malformed lines in %s", skip_counter, traj_path
        )
    return frames


# ---------------------------------------------------------------------------
# Equilibration cutoff
# ---------------------------------------------------------------------------


def equilibration_cutoff(
    energies: list[float | None],
    *,
    window: int = _EQ_WINDOW,
    sigma_mult: float = _EQ_SIGMA_MULT,
    min_frac: float = _EQ_MIN_FRAC,
    max_frac: float = _EQ_MAX_FRAC,
    fallback_frac: float = _EQ_FALLBACK_FRAC,
) -> int:
    """Return the number of leading frames to discard (equilibration).

    Re-implements the ±2σ sliding-window statistical test from
    ``acp.workflows.xtbmd_censo_energy._equilibration_cutoff`` (line 228).

    The trajectory is divided into non-overlapping sliding windows of
    *window* frames; the last adjacent window pair whose mean difference
    exceeds ``sigma_mult × pooled_std`` marks the end of the non-equilibrated
    prefix.  The dropped fraction is clamped into ``[min_frac, max_frac]``.
    When energies are missing or too few for a window pair, ``fallback_frac``
    of the frames is dropped.
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


# ---------------------------------------------------------------------------
# Basin assignment (greedy plain-RMSD)
# ---------------------------------------------------------------------------


def assign_basins(
    frames: list[TrajFrame],
    rmsd_threshold: float = 0.5,
    max_cluster_frames: int = 1500,
) -> tuple[list[int], list[BasinInfo]]:
    """Assign each frame to a geometric basin via greedy plain-RMSD clustering.

    **Subsample policy:** when ``len(frames) > max_cluster_frames``, a
    stride-subsample selects evenly-spaced frames for clustering; non-sampled
    frames inherit the **previous sampled frame's basin** (carry-forward
    approximation — this is conservative: it assumes the conformation does
    not change between sampled frames, which is reasonable for short MD
    strides but may over-count the last basin at the tail).

    Returns:
        ``(basin_ids, basin_infos)`` where ``basin_ids[i]`` is the basin
        assigned to frame *i*, and ``basin_infos`` lists basin metadata.
    """
    n = len(frames)
    if n == 0:
        return [], []

    stride = max(1, n // max_cluster_frames) if n > max_cluster_frames else 1
    sampled_indices = list(range(0, n, stride))

    basin_representatives: list[NDArray[np.float64]] = []
    basin_infos: list[BasinInfo] = []
    sampled_basin_ids: list[int] = []

    for idx in sampled_indices:
        frame = frames[idx]
        assigned = False
        for bid, rep in enumerate(basin_representatives):
            if frame.coords.shape == rep.shape and frame.coords.size > 0:
                rmsd_val = plain_rmsd(frame.coords, rep)
                if rmsd_val < rmsd_threshold:
                    sampled_basin_ids.append(bid)
                    basin_infos[bid].visit_count += 1
                    if frame.energy_kcal_mol is not None and (
                        basin_infos[bid].min_energy is None
                        or frame.energy_kcal_mol < basin_infos[bid].min_energy
                    ):
                        basin_infos[bid].min_energy = frame.energy_kcal_mol
                    assigned = True
                    break
        if not assigned:
            new_id = len(basin_representatives)
            basin_representatives.append(frame.coords.copy())
            sampled_basin_ids.append(new_id)
            basin_infos.append(BasinInfo(
                basin_id=new_id,
                first_seen_index=idx,
                first_seen_ps=frame.time_ps,
                visit_count=1,
                min_energy=frame.energy_kcal_mol,
                representative_frame=idx,
            ))

    # Carry-forward: non-sampled frames inherit previous sampled basin
    basin_ids: list[int] = []
    current_sampled_pos = 0
    for i in range(n):
        next_pos = current_sampled_pos + 1
        if next_pos < len(sampled_indices) and i >= sampled_indices[next_pos]:
            current_sampled_pos = next_pos
        basin_ids.append(sampled_basin_ids[current_sampled_pos])

    return basin_ids, basin_infos


# ---------------------------------------------------------------------------
# Classical MDS
# ---------------------------------------------------------------------------


def mds_2d(
    distance_matrix: NDArray[np.float64] | list[list[float]],
) -> list[MDS_COORD_TYPE]:
    """Classical MDS: project a distance matrix to 2D coordinates.

    Double-centers the squared-distance matrix, computes eigenvalues via
    ``np.linalg.eigh``, and returns the top-2 eigencoordinates scaled by
    ``sqrt(eigenvalue)``.  Degenerate or all-NaN input returns zeros.
    """
    dist_mat = np.asarray(distance_matrix, dtype=np.float64)
    n = dist_mat.shape[0]
    if n <= 1:
        return [(0.0, 0.0)] * max(n, 1)

    if not np.any(dist_mat):
        return [(0.0, 0.0)] * n

    dist_sq = dist_mat * dist_mat
    row_mean = dist_sq.mean(axis=1, keepdims=True)
    col_mean = dist_sq.mean(axis=0, keepdims=True)
    grand_mean = dist_sq.mean()
    gram = -0.5 * (dist_sq - row_mean - col_mean + grand_mean)

    eigenvalues, eigenvectors = np.linalg.eigh(gram)

    idx = np.argsort(eigenvalues)[::-1][:2]
    top_vals = eigenvalues[idx]
    top_vecs = eigenvectors[:, idx]

    scales = np.sqrt(np.maximum(top_vals, 0.0))
    coords = top_vecs * scales[np.newaxis, :]

    result: list[MDS_COORD_TYPE] = []
    for i in range(n):
        result.append((float(coords[i, 0]), float(coords[i, 1])))
    return result


# ---------------------------------------------------------------------------
# Saturation metrics
# ---------------------------------------------------------------------------


def _compute_saturation(
    frames: list[TrajFrame],
    basin_ids: list[int],
    basin_infos: list[BasinInfo],
) -> SamplingSaturation:
    """Compute sampling-saturation metrics from basin assignment."""
    n = len(frames)
    if n == 0:
        return SamplingSaturation(
            unique_clusters=0,
            new_clusters_last_20pct=0,
            last_new_basin_ps=None,
            revisit_ratio=0.0,
            energy_window_kcal_mol=0.0,
            level="LOW",
            cumulative_unique=[],
        )

    unique = len(basin_infos)
    n_last_20 = max(1, n // 5)
    last_20_basins = set(basin_ids[-n_last_20:])
    all_basins = set(basin_ids[:-n_last_20]) if n > n_last_20 else set()
    new_in_last_20 = len(last_20_basins - all_basins)

    last_new_ps: float | None = None
    seen: set[int] = set()
    for i, bid in enumerate(basin_ids):
        if bid not in seen:
            seen.add(bid)
            last_new_ps = frames[i].time_ps

    seen2: set[int] = set()
    revisits = 0
    for bid in basin_ids:
        if bid in seen2:
            revisits += 1
        seen2.add(bid)
    revisit_ratio = revisits / n if n > 0 else 0.0

    energies = [
        f.energy_kcal_mol for f in frames if f.energy_kcal_mol is not None
    ]
    energy_window = (max(energies) - min(energies)) if energies else 0.0

    if new_in_last_20 == 0:
        level = "HIGH"
    elif new_in_last_20 <= max(1, unique // 10):
        level = "MEDIUM"
    else:
        level = "LOW"

    cumulative_unique: list[dict[str, Any]] = []
    seen3: set[int] = set()
    for i, bid in enumerate(basin_ids):
        seen3.add(bid)
        cumulative_unique.append(
            {"time_ps": frames[i].time_ps, "unique": len(seen3)}
        )

    return SamplingSaturation(
        unique_clusters=unique,
        new_clusters_last_20pct=new_in_last_20,
        last_new_basin_ps=last_new_ps,
        revisit_ratio=revisit_ratio,
        energy_window_kcal_mol=energy_window,
        level=level,
        cumulative_unique=cumulative_unique,
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def compute_sampling_history(
    traj_path: Path,
    *,
    protocol: str = "xtb-md",
    rmsd_threshold: float = 0.5,
    max_cluster_frames: int = 1500,
) -> SamplingHistory:
    """Build a complete :class:`SamplingHistory` from a trajectory file.

    Pipeline: parse → equilibration cut → basin assignment → MDS → saturation.
    """
    frames = parse_traj_frames(traj_path)
    n_raw = len(frames)

    energies = [f.energy_kcal_mol for f in frames]
    eq_cut = equilibration_cutoff(energies)
    if eq_cut > 0:
        frames = frames[eq_cut:]
    n_used = len(frames)

    basin_ids, basin_infos = assign_basins(
        frames,
        rmsd_threshold=rmsd_threshold,
        max_cluster_frames=max_cluster_frames,
    )

    n_basins = len(basin_infos)
    if n_basins > 1:
        dist_matrix = np.zeros((n_basins, n_basins), dtype=np.float64)
        for i in range(n_basins):
            for j in range(i + 1, n_basins):
                fi = frames[basin_infos[i].representative_frame]
                fj = frames[basin_infos[j].representative_frame]
                has_coords = fi.coords.size > 0 and fj.coords.size > 0
                d = plain_rmsd(fi.coords, fj.coords) if has_coords else 0.0
                dist_matrix[i, j] = d
                dist_matrix[j, i] = d
        basin_mds = mds_2d(dist_matrix)
    else:
        basin_mds = [(0.0, 0.0)] * max(n_basins, 1)

    mds_coords: list[MDS_COORD_TYPE] = []
    for bid in basin_ids:
        if bid < len(basin_mds):
            mds_coords.append(basin_mds[bid])
        else:
            mds_coords.append((0.0, 0.0))

    is_new: list[bool] = []
    seen: set[int] = set()
    for bid in basin_ids:
        is_new.append(bid not in seen)
        seen.add(bid)

    saturation = _compute_saturation(frames, basin_ids, basin_infos)

    return SamplingHistory(
        schema_version=_SAMPLING_SCHEMA_VERSION,
        protocol=protocol,
        source_trajectory=str(traj_path),
        n_frames_raw=n_raw,
        n_frames_used=n_used,
        equilibration_cut=eq_cut,
        frames=frames,
        basin_ids=basin_ids,
        is_new_basin=is_new,
        mds_coords=mds_coords,
        basins=basin_infos,
        saturation=saturation,
        computed_at=datetime.now(timezone.utc).isoformat(),
        subsampled=len(frames) > max_cluster_frames,
        subsample_stride=(
            max(1, len(frames) // max_cluster_frames)
            if len(frames) > max_cluster_frames
            else 1
        ),
    )
