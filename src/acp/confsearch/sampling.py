"""MD sampling-history capture for Confsearch (todo 6+7).

Parses xTB-MD trajectory frames, detects the equilibration prefix,
clusters conformations into geometric basins via greedy plain-RMSD,
projects basin distances to 2D via classical MDS, and computes
sampling-saturation metrics.  The resulting
:class:`SamplingHistory` is persisted as ``RESULT/confsearch/sampling_history.json``
(schema ``sampling_history_v1``) by the Confsearch engine finalize hook
for ``xtb-md`` / ``xtbmd-censo`` protocols only.

Coordinates are **not** persisted — geometry stays in ``traj.xyz``;
the JSON carries per-frame metadata only.

Third-party dependency: **numpy only** (no rdkit, sklearn, etc.).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .shared.deduplication import plain_rmsd

logger = logging.getLogger(__name__)

__all__ = [
    "BasinInfo",
    "MDS_2D",
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Regex for the first ``(kcal/mol)`` energy value in xTB MD titles.
#: Mirrors ``xtbmd_censo_energy._KCAL_PER_MOL_RE`` (line 97).
_KCAL_PER_MOL_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*\(kcal/mol\)")

#: Regex for the ``md:`` prefix carrying time in ps.
_MD_PREFIX_RE = re.compile(r"^md:\s*([\d.]+)")

#: Schema version for the persisted JSON.
_SAMPLING_SCHEMA_VERSION = "sampling_history_v1"

# Equilibration detection parameters — re-implements the ±2σ sliding-window
# statistical test from ``xtbmd_censo_energy._equilibration_cutoff`` (line 228).
# Import was evaluated but rejected: the source module carries heavy transitive
# deps (rdkit, cccp backends, conftest) that would bloat this lightweight module.
_EQ_WINDOW = 100
_EQ_SIGMA_MULT = 2.0
_EQ_MIN_FRAC = 0.05
_EQ_MAX_FRAC = 0.20
_EQ_FALLBACK_FRAC = 0.10

#: Path to trajectory relative to task root (merged-traj convention,
#: ``xtbmd_md.py:170-173``).
_TRAJ_REL_PATH = Path("WORK") / "02_SEARCH" / "xTB" / "traj.xyz"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TrajFrame:
    """One parsed trajectory frame (in-memory only, not persisted)."""

    index: int
    time_ps: float | None
    step: int
    energy_kcal_mol: float | None
    symbols: list[str]
    coords: NDArray[np.float64]


@dataclass
class BasinInfo:
    """Metadata for one geometric basin."""

    basin_id: int
    first_seen_index: int
    first_seen_ps: float | None
    visit_count: int
    min_energy: float | None
    representative_frame: int


@dataclass
class SamplingSaturation:
    """Sampling-saturation metrics and level classification.

    Level rule:
        * **HIGH** — ``new_clusters_last_20pct == 0`` (no new basins in the
          final 20% of sampled frames).
        * **MEDIUM** — ``new_clusters_last_20pct <= max(1, unique // 10)``.
        * **LOW** — otherwise.
    """

    unique_clusters: int
    new_clusters_last_20pct: int
    last_new_basin_ps: float | None
    revisit_ratio: float
    energy_window_kcal_mol: float
    level: str
    cumulative_unique: list[dict[str, Any]]


@dataclass(frozen=True)
class SamplingHistory:
    """Complete sampling-history artifact (schema ``sampling_history_v1``).

    Coordinates are **not** persisted — geometry stays in the source
    trajectory file.  The ``frames`` list carries per-frame metadata only.
    """

    schema_version: str
    protocol: str
    source_trajectory: str
    n_frames_raw: int
    n_frames_used: int
    equilibration_cut: int
    frames: list[TrajFrame]
    basin_ids: list[int]
    is_new_basin: list[bool]
    mds_coords: list[tuple[float, float]]
    basins: list[BasinInfo]
    saturation: SamplingSaturation
    computed_at: str
    subsampled: bool
    subsample_stride: int

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict (no coordinates)."""
        min_e = min(
            (f.energy_kcal_mol for f in self.frames if f.energy_kcal_mol is not None),
            default=None,
        )
        frame_dicts: list[dict[str, Any]] = []
        for f, basin_id, is_new, (mx, my) in zip(
            self.frames, self.basin_ids, self.is_new_basin, self.mds_coords, strict=True
        ):
            rel = (
                (f.energy_kcal_mol - min_e)
                if (f.energy_kcal_mol is not None and min_e is not None)
                else None
            )
            frame_dicts.append({
                "index": f.index,
                "time_ps": _sanitize(f.time_ps),
                "step": f.step,
                "energy_kcal_mol": _sanitize(f.energy_kcal_mol),
                "relative_energy_kcal_mol": _sanitize(rel),
                "basin_id": basin_id,
                "is_new_basin": is_new,
                "mds": [_sanitize(mx), _sanitize(my)],
            })
        return {
            "schema_version": self.schema_version,
            "protocol": self.protocol,
            "source_trajectory": self.source_trajectory,
            "n_frames_raw": self.n_frames_raw,
            "n_frames_used": self.n_frames_used,
            "equilibration_cut": self.equilibration_cut,
            "frames": frame_dicts,
            "basins": [
                {
                    "basin_id": b.basin_id,
                    "first_seen_index": b.first_seen_index,
                    "first_seen_ps": _sanitize(b.first_seen_ps),
                    "visit_count": b.visit_count,
                    "min_energy": _sanitize(b.min_energy),
                    "representative_frame": b.representative_frame,
                }
                for b in self.basins
            ],
            "saturation": {
                "unique_clusters": self.saturation.unique_clusters,
                "new_clusters_last_20pct": self.saturation.new_clusters_last_20pct,
                "last_new_basin_ps": _sanitize(self.saturation.last_new_basin_ps),
                "revisit_ratio": _sanitize(self.saturation.revisit_ratio),
                "energy_window_kcal_mol": _sanitize(self.saturation.energy_window_kcal_mol),
                "level": self.saturation.level,
                "cumulative_unique": self.saturation.cumulative_unique,
            },
            "computed_at": self.computed_at,
            "subsampled": self.subsampled,
            "subsample_stride": self.subsample_stride,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SamplingHistory:
        """Deserialize from JSON dict (coordinates not restored)."""
        frames = [
            TrajFrame(
                index=f["index"],
                time_ps=f.get("time_ps"),
                step=f["step"],
                energy_kcal_mol=f.get("energy_kcal_mol"),
                symbols=[],  # not persisted
                coords=np.empty((0, 3)),  # not persisted
            )
            for f in d["frames"]
        ]
        sat_d = d["saturation"]
        sat = SamplingSaturation(
            unique_clusters=sat_d["unique_clusters"],
            new_clusters_last_20pct=sat_d["new_clusters_last_20pct"],
            last_new_basin_ps=sat_d.get("last_new_basin_ps"),
            revisit_ratio=sat_d["revisit_ratio"],
            energy_window_kcal_mol=sat_d["energy_window_kcal_mol"],
            level=sat_d["level"],
            cumulative_unique=sat_d.get("cumulative_unique", []),
        )
        basins = [
            BasinInfo(
                basin_id=b["basin_id"],
                first_seen_index=b["first_seen_index"],
                first_seen_ps=b.get("first_seen_ps"),
                visit_count=b["visit_count"],
                min_energy=b.get("min_energy"),
                representative_frame=b["representative_frame"],
            )
            for b in d["basins"]
        ]
        return cls(
            schema_version=d["schema_version"],
            protocol=d["protocol"],
            source_trajectory=d["source_trajectory"],
            n_frames_raw=d["n_frames_raw"],
            n_frames_used=d["n_frames_used"],
            equilibration_cut=d["equilibration_cut"],
            frames=frames,
            basin_ids=[fd.get("basin_id", 0) for fd in d["frames"]],
            is_new_basin=[fd.get("is_new_basin", False) for fd in d["frames"]],
            mds_coords=[tuple(fd.get("mds", [0.0, 0.0])) for fd in d["frames"]],  # type: ignore[misc]
            basins=basins,
            saturation=sat,
            computed_at=d["computed_at"],
            subsampled=d["subsampled"],
            subsample_stride=d["subsample_stride"],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize(value: float | None) -> float | None:
    """Replace non-finite floats with None for JSON safety."""
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return value


# ---------------------------------------------------------------------------
# Trajectory parser
# ---------------------------------------------------------------------------


def parse_traj_frames(traj_path: Path) -> list[TrajFrame]:
    """Parse a multi-frame XYZ trajectory into :class:`TrajFrame` objects.

    Frame titles are expected to follow the xTB-MD convention:
    ``md: <t(ps)> <E_pot> (kcal/mol) <E_tot> (kcal/mol)``

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

        # Parse coordinates
        coords_list: list[list[float]] = []
        for line in lines[offset + 2 : end]:
            parts = line.split()
            if len(parts) >= 4:
                coords_list.append([float(parts[1]), float(parts[2]), float(parts[3])])

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
            coords=np.asarray(coords_list, dtype=np.float64) if coords_list else np.empty((0, 3)),
        ))
        offset = end

    if skip_counter:
        logger.debug("Skipped %d malformed title lines in %s", skip_counter, traj_path)
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
    Import was evaluated but rejected: the source module transitively imports
    rdkit, cccp backends, and heavy workflow machinery — unacceptable for
    this lightweight confsearch module.

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

    # Determine sampling stride
    stride = max(1, n // max_cluster_frames) if n > max_cluster_frames else 1
    sampled_indices = list(range(0, n, stride))

    # Greedy clustering on sampled frames
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

    # Carry-forward: non-sampled frames inherit the previous sampled frame's basin
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

#: Type alias for 2D MDS coordinates.
MDS_2D = list[tuple[float, float]]


def mds_2d(distance_matrix: NDArray[np.float64] | list[list[float]]) -> MDS_2D:
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

    # Take top-2 (largest eigenvalues, eigh returns ascending)
    idx = np.argsort(eigenvalues)[::-1][:2]
    top_vals = eigenvalues[idx]
    top_vecs = eigenvectors[:, idx]

    # Scale by sqrt(eigenvalue), clamp negative eigenvalues to 0
    scales = np.sqrt(np.maximum(top_vals, 0.0))
    coords = top_vecs * scales[np.newaxis, :]

    result: MDS_2D = []
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

    # Last new basin time
    last_new_ps: float | None = None
    seen: set[int] = set()
    for i, bid in enumerate(basin_ids):
        if bid not in seen:
            seen.add(bid)
            last_new_ps = frames[i].time_ps

    # Revisit ratio: fraction of frames that join an existing basin
    seen2: set[int] = set()
    revisits = 0
    for bid in basin_ids:
        if bid in seen2:
            revisits += 1
        seen2.add(bid)
    revisit_ratio = revisits / n if n > 0 else 0.0

    # Energy window
    energies = [f.energy_kcal_mol for f in frames if f.energy_kcal_mol is not None]
    energy_window = (max(energies) - min(energies)) if energies else 0.0

    # Level rule
    if new_in_last_20 == 0:
        level = "HIGH"
    elif new_in_last_20 <= max(1, unique // 10):
        level = "MEDIUM"
    else:
        level = "LOW"

    # Cumulative unique series
    cumulative_unique: list[dict[str, Any]] = []
    seen3: set[int] = set()
    for i, bid in enumerate(basin_ids):
        seen3.add(bid)
        cumulative_unique.append({"time_ps": frames[i].time_ps, "unique": len(seen3)})

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

    # Equilibration cut
    energies = [f.energy_kcal_mol for f in frames]
    eq_cut = equilibration_cutoff(energies)
    if eq_cut > 0:
        frames = frames[eq_cut:]
    n_used = len(frames)

    # Basin assignment
    basin_ids, basin_infos = assign_basins(
        frames, rmsd_threshold=rmsd_threshold, max_cluster_frames=max_cluster_frames
    )

    # MDS 2D projection
    n_basins = len(basin_infos)
    if n_basins > 1:
        # Build inter-basin distance matrix from representative frames
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

    # Map basin MDS coords to per-frame coords
    mds_coords: MDS_2D = []
    for bid in basin_ids:
        if bid < len(basin_mds):
            mds_coords.append(basin_mds[bid])
        else:
            mds_coords.append((0.0, 0.0))

    # is_new_basin
    is_new: list[bool] = []
    seen: set[int] = set()
    for bid in basin_ids:
        is_new.append(bid not in seen)
        seen.add(bid)

    # Saturation
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


# ---------------------------------------------------------------------------
# Atomic JSON persistence
# ---------------------------------------------------------------------------


def write_sampling_history(task_root: Path, history: SamplingHistory) -> Path:
    """Atomically write ``RESULT/confsearch/sampling_history.json``."""
    result_dir = task_root / "RESULT" / "confsearch"
    result_dir.mkdir(parents=True, exist_ok=True)
    path = result_dir / "sampling_history.json"
    tmp = path.with_suffix(".json.tmp")
    payload = history.to_dict()
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_sampling_history(task_root: Path) -> SamplingHistory | None:
    """Load ``RESULT/confsearch/sampling_history.json`` (None on missing/corrupt)."""
    path = task_root / "RESULT" / "confsearch" / "sampling_history.json"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return SamplingHistory.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.debug("Failed to load sampling history from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Single-frame XYZ extraction
# ---------------------------------------------------------------------------


def read_traj_frame_xyz(traj_path: Path, index: int) -> str | None:
    """Return the exact original XYZ text block for frame *index*.

    Returns ``None`` when *index* is out of range.
    """
    text = Path(traj_path).read_text(encoding="utf-8")
    lines = text.splitlines()
    current = 0
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
        if current == index:
            return "\n".join(lines[offset:end]) + "\n"
        current += 1
        offset = end
    return None
