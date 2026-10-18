"""Sampling-history data models, serialization, and persistence.

Frozen dataclasses for the ``sampling_history_v1`` schema, JSON round-trip
(``to_dict`` / ``from_dict``), atomic write/read helpers, and single-frame
XYZ extraction.  Coordinates are **not** persisted — geometry stays in
``traj.xyz``; the JSON carries per-frame metadata only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

__all__ = [
    "MDS_COORD_TYPE",
    "BasinInfo",
    "SamplingHistory",
    "SamplingSaturation",
    "TrajFrame",
    "load_sampling_history",
    "read_traj_frame_xyz",
    "write_sampling_history",
]

# ---------------------------------------------------------------------------
# Constants shared by models and persistence
# ---------------------------------------------------------------------------

#: Regex for the first ``(kcal/mol)`` energy value in xTB MD titles.
#: Mirrors ``xtbmd_censo_energy._KCAL_PER_MOL_RE`` (line 97).
_KCAL_PER_MOL_RE = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*\(kcal/mol\)")

#: Regex for the ``md:`` prefix carrying time in ps.
_MD_PREFIX_RE = re.compile(r"^md:\s*([\d.]+)")

#: Schema version for the persisted JSON.
_SAMPLING_SCHEMA_VERSION = "sampling_history_v1"

#: Per-frame MDS coordinate type (may be None when degenerate).
MDS_COORD_TYPE = tuple[float | None, float | None]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TrajFrame:
    """One parsed trajectory frame (in-memory only, not persisted).

    ``index`` and ``step`` are sequential frame counters in the parsed
    trajectory (0-based).  ``symbols`` and ``coords`` are kept in memory
    for basin clustering but are **excluded** from JSON serialization.
    """

    index: int
    time_ps: float | None
    step: int
    energy_kcal_mol: float | None
    symbols: list[str]
    coords: NDArray[np.float64]


@dataclass
class BasinInfo:
    """Metadata for one geometric basin.

    ``first_seen_index`` and ``representative_frame`` are **positional
    indices** into the post-equilibration-cut ``frames`` list (and
    double as array positions in the serialized ``frames[]`` in the
    JSON).  They are NOT raw trajectory indices — downstream consumers
    must not confuse them with ``TrajFrame.index``.
    """

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
    mds_coords: list[MDS_COORD_TYPE]
    basins: list[BasinInfo]
    saturation: SamplingSaturation
    computed_at: str
    subsampled: bool
    subsample_stride: int

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict (no coordinates).

        ``basin.first_seen_index`` and ``basin.representative_frame`` are
        positional indices into the post-equilibration ``frames`` list.
        """
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
                symbols=[],
                coords=np.empty((0, 3)),
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
        mds_coords: list[MDS_COORD_TYPE] = []
        for fd in d["frames"]:
            raw = fd.get("mds")
            if isinstance(raw, list) and len(raw) == 2:
                mds_coords.append((_to_optional_float(raw[0]), _to_optional_float(raw[1])))
            else:
                mds_coords.append((None, None))
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
            mds_coords=mds_coords,
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


def _to_optional_float(value: Any) -> float | None:
    """Coerce a JSON value to ``float | None``."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


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
