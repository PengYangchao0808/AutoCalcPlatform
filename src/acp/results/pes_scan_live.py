"""Live PES scan projections for the energy workspace (display-only).

Serves the relaxed-scan curve while ``RESULT/pes_search/pes_profile.json``
does not exist yet.  Data sources, in priority order:

1. ``RESULT/trajectories/pes_scan_trajectory.json`` — the live snapshot
   written by :class:`acp.calculations.pes.scan_snapshot.PesScanSnapshotWriter`
   (xTB / synchronous-ORCA callbacks, post-extraction seed, SP updates);
2. native-ORCA incremental parse — the ``*.relaxscanact.dat`` energy ledger
   (one row per converged scan point, appended by ORCA during the run) plus
   the per-point ``*.NNN.xyz`` geometries.  This covers jobs that are
   already running with no snapshot writer deployed;
3. ``frame_NNN/`` directory back-fill — xTB and synchronous-ORCA layouts
   (``xtbopt.xyz`` + ``xtb.log``, or the frame's constrained-opt output),
   reading only complete frames whose energy and geometry come from the
   same frame directory;
4. none of the above — the caller falls back to the PES pending projection
   (HTTP 200 waiting state, never an error).

All providers are read-only; parsing reuses the ORCA terminal-state parsers
(``cccp.qc.interfaces.orca.parse_relaxed_scan_*``) so live values match the
final profile exactly.  Like :mod:`acp.results.irc_projection`, this module
is display-only and performs no validation of the chemistry.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.calculations.pes.outputs import PES_SCAN_RELATIVE_PATH
from acp.calculations.pes.scan_snapshot import (
    SNAPSHOT_RELATIVE_PATH,
    SNAPSHOT_SCHEMA_VERSION,
    TERMINAL_STAGES,
)
from acp.results.frames import VIEW_REGISTRY, TrajectoryAnnotation, TrajectoryFrame

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 5.0
_scan_cache: dict[Path, tuple[float, _LiveScan | None]] = {}

_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})

# ORCA %geom Scan drive line, e.g. ``B 2 3 = 1.50000000, 4.00000000, 21``
# (values comma-separated, matching _orca_scan_line output).
_SCAN_DRIVE_RE = re.compile(
    r"^\s*([BAD])\b[^=]*=\s*(-?\d+(?:\.\d+)?)\s*,?\s*(-?\d+(?:\.\d+)?)\s*,?\s*(\d+)\s*$"
)
# ORCA constraint value, e.g. ``{ B 3 4 C 1.62500000 }``.
_ORCA_CONSTRAINT_RE = re.compile(r"\{\s*[BAD]\b[\d\s,]*?C\s+(-?\d+(?:\.\d+)?)\s*\}")
# xTB xcontrol constraint, e.g. ``  distance: 3, 4, 1.625000``.
_XCONTROL_CONSTRAINT_RE = re.compile(
    r"^\s*(distance|angle|dihedral):\s*[\d,\s]+,\s*(-?\d+(?:\.\d+)?)\s*$"
)

_KIND_BY_DRIVE_LETTER = {"B": "distance", "A": "angle", "D": "dihedral"}
_UNIT_BY_KIND = {"distance": "angstrom", "angle": "degree", "dihedral": "degree"}

_EXCLUDED_FRAME_GEOMETRY_PATTERNS = ("xtb_input.xyz", "_start.xyz", "_trj.xyz", ".allxyz")

__all__ = [
    "build_pes_scan_live_graph",
    "build_pes_scan_pending_energy_graph",
    "collect_pes_scan_live_frames",
    "read_pes_scan_snapshot",
]


@dataclass(frozen=True)
class _LiveScan:
    """Normalized live scan state shared by every provider."""

    frames: list[dict[str, Any]]
    stage: str
    driver: str
    points_total: int
    coordinate: dict[str, Any]
    coordinates: list[dict[str, Any]]
    source: str
    x_source: str


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _xyz_atom_count(path: Path) -> int | None:
    """Read the atom-count header of an XYZ file (None when unreadable)."""
    try:
        first_line = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        return int(first_line.strip())
    except (OSError, IndexError, ValueError):
        return None


# ── provider ①: snapshot file ──────────────────────────────────────────


def read_pes_scan_snapshot(work_dir: Path) -> dict[str, Any] | None:
    """Return the validated ``pes_scan_trajectory_v1`` payload, or None."""
    path = Path(work_dir) / SNAPSHOT_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("schema_version") or "") != SNAPSHOT_SCHEMA_VERSION:
        return None
    if not isinstance(payload.get("frames"), list):
        return None
    return payload


def _provider_snapshot(work_dir: Path) -> _LiveScan | None:
    payload = read_pes_scan_snapshot(work_dir)
    if payload is None:
        return None
    frames = [frame for frame in payload["frames"] if isinstance(frame, dict)]
    if not frames:
        return None
    return _LiveScan(
        frames=frames,
        stage=str(payload.get("scan_stage") or "running"),
        driver=str(payload.get("driver") or "unknown"),
        points_total=int(payload.get("points_total") or 0),
        coordinate=dict(payload.get("coordinate") or {}),
        coordinates=list(payload.get("coordinates") or []),
        source=SNAPSHOT_RELATIVE_PATH,
        x_source="target",
    )


# ── provider ②: native ORCA incremental artifacts ─────────────────────


def _parse_ledger_rows(path: Path) -> list[tuple[float, float]]:
    """Parse ``*.relaxscanact.dat`` rows; a torn in-flight tail row is skipped."""
    rows: list[tuple[float, float]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return rows
    for raw_line in text.splitlines():
        tokens = raw_line.split()
        if len(tokens) < 2:
            continue
        try:
            rows.append((float(tokens[0]), float(tokens[1])))
        except ValueError:
            continue
    return rows


def _parse_scan_drive_grid(scan_dir: Path) -> dict[str, Any] | None:
    """Rebuild the target grid from an ORCA ``%geom Scan`` input line."""
    for inp_path in sorted(scan_dir.glob("*.inp")):
        try:
            text = inp_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_scan = False
        for line in text.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith("scan"):
                in_scan = True
                continue
            if in_scan and lowered.startswith("end"):
                break
            if not in_scan:
                continue
            match = _SCAN_DRIVE_RE.match(stripped)
            if match is None:
                continue
            kind = _KIND_BY_DRIVE_LETTER.get(match.group(1).upper(), "distance")
            return {
                "kind": kind,
                "unit": _UNIT_BY_KIND.get(kind, "angstrom"),
                "start": float(match.group(2)),
                "end": float(match.group(3)),
                "n_points": int(match.group(4)),
            }
    return None


def _provider_native_orca(work_dir: Path) -> _LiveScan | None:
    scan_dir = Path(work_dir) / PES_SCAN_RELATIVE_PATH
    ledgers = sorted(scan_dir.glob("*.relaxscanact.dat"))
    if not ledgers:
        return None
    ledger = ledgers[0]
    base_name = ledger.name.removesuffix(".relaxscanact.dat")
    rows = _parse_ledger_rows(ledger)
    if not rows:
        return None
    grid = _parse_scan_drive_grid(scan_dir)
    input_atoms = _xyz_atom_count(scan_dir / "input.xyz")
    frames: list[dict[str, Any]] = []
    for position, (actual, energy) in enumerate(rows):
        geometry_ref = ""
        per_point = scan_dir / f"{base_name}.{position + 1:03d}.xyz"
        if per_point.is_file():
            point_atoms = _xyz_atom_count(per_point)
            if input_atoms is None or point_atoms == input_atoms:
                geometry_ref = f"{PES_SCAN_RELATIVE_PATH}/{per_point.name}"
        target = None
        if grid is not None:
            span = max(int(grid["n_points"]) - 1, 1)
            target = float(grid["start"]) + (float(grid["end"]) - float(grid["start"])) * (
                position / span
            )
        frames.append(
            {
                "index": position,
                "status": "completed",
                "converged": True,
                "target_coordinate": target,
                "actual_coordinate": actual,
                "target_coordinates": {},
                "actual_coordinates": {},
                "coordinate_unit": str(grid.get("unit")) if grid else "",
                "energy_hartree": energy,
                "geometry_ref": geometry_ref,
                "sp_energy_hartree": None,
                "sp_status": "pending",
            }
        )
    coordinate = (
        {
            "kind": str(grid["kind"]),
            "unit": str(grid["unit"]),
            "start": float(grid["start"]),
            "end": float(grid["end"]),
            "n_points": int(grid["n_points"]),
        }
        if grid
        else {"kind": "coordinate", "unit": ""}
    )
    return _LiveScan(
        frames=frames,
        stage="running",
        driver="orca",
        points_total=int(grid["n_points"]) if grid else len(rows),
        coordinate=coordinate,
        coordinates=[coordinate],
        source=f"{PES_SCAN_RELATIVE_PATH}/{ledger.name}",
        x_source="target" if grid is not None else "actual",
    )


# ── provider ③: frame directories (xTB / synchronous ORCA) ────────────


def _frame_dir_geometry(frame_dir: Path, input_atoms: int | None) -> Path | None:
    """Locate the converged single-frame geometry inside one frame directory."""
    candidates: list[Path] = []
    xtbopt = frame_dir / "xtbopt.xyz"
    if xtbopt.is_file():
        candidates.append(xtbopt)
    for xyz_path in sorted(frame_dir.glob("*.xyz")):
        name = xyz_path.name
        if any(pattern in name for pattern in _EXCLUDED_FRAME_GEOMETRY_PATTERNS):
            continue
        if xyz_path not in candidates:
            candidates.append(xyz_path)
    for candidate in candidates:
        atoms = _xyz_atom_count(candidate)
        if atoms is None or atoms == 0:
            continue
        if input_atoms is not None and atoms != input_atoms:
            continue
        return candidate
    return None


def _frame_dir_energy(frame_dir: Path) -> float | None:
    """Parse the frame's own energy (xTB log or ORCA output in that directory)."""
    xtb_log = frame_dir / "xtb.log"
    if xtb_log.is_file():
        energy = _last_float_after(xtb_log, "TOTAL ENERGY")
        if energy is not None:
            return energy
    for out_path in sorted(frame_dir.glob("*.out")):
        energy = _last_float_after(out_path, "FINAL SINGLE POINT ENERGY")
        if energy is not None:
            return energy
    return None


def _last_float_after(path: Path, marker: str) -> float | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    value: float | None = None
    for line in text.splitlines():
        if marker not in line:
            continue
        for token in reversed(line.replace("D", "E").replace("d", "e").split()):
            try:
                value = float(token)
                break
            except ValueError:
                continue
    return value


def _frame_dir_target(frame_dir: Path) -> float | None:
    """Extract the frame's constraint target from its xcontrol or ORCA input."""
    xcontrol = frame_dir / ".xcontrol"
    if xcontrol.is_file():
        try:
            for line in xcontrol.read_text(encoding="utf-8", errors="replace").splitlines():
                match = _XCONTROL_CONSTRAINT_RE.match(line)
                if match is not None:
                    return float(match.group(2))
        except OSError:
            pass
    for inp_path in sorted(frame_dir.glob("*.inp")):
        try:
            text = inp_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _ORCA_CONSTRAINT_RE.search(text)
        if match is not None:
            return float(match.group(1))
    return None


def _provider_frame_dirs(work_dir: Path) -> _LiveScan | None:
    scan_dir = Path(work_dir) / PES_SCAN_RELATIVE_PATH
    frame_dirs = sorted(path for path in scan_dir.glob("frame_[0-9][0-9][0-9]") if path.is_dir())
    if not frame_dirs:
        return None
    input_atoms = _xyz_atom_count(scan_dir / "input.xyz")
    frames: list[dict[str, Any]] = []
    for frame_dir in frame_dirs:
        index = int(frame_dir.name.rsplit("_", 1)[1])
        geometry = _frame_dir_geometry(frame_dir, input_atoms)
        energy = _frame_dir_energy(frame_dir)
        if geometry is None or energy is None:
            continue
        frames.append(
            {
                "index": index,
                "status": "completed",
                "converged": True,
                "target_coordinate": _frame_dir_target(frame_dir),
                "actual_coordinate": None,
                "target_coordinates": {},
                "actual_coordinates": {},
                "coordinate_unit": "",
                "energy_hartree": energy,
                "geometry_ref": f"{PES_SCAN_RELATIVE_PATH}/{frame_dir.name}/{geometry.name}",
                "sp_energy_hartree": None,
                "sp_status": "pending",
            }
        )
    if not frames:
        return None
    has_target = any(frame["target_coordinate"] is not None for frame in frames)
    return _LiveScan(
        frames=frames,
        stage="running",
        driver="frame_dirs",
        points_total=len(frame_dirs),
        coordinate={"kind": "coordinate", "unit": ""},
        coordinates=[],
        source=f"{PES_SCAN_RELATIVE_PATH}/frame_*",
        x_source="target" if has_target else "index",
    )


# ── collection with TTL cache ──────────────────────────────────────────


def collect_pes_scan_live_frames(work_dir: Path | str) -> _LiveScan | None:
    """Return the best available live scan state (snapshot → native → frames)."""
    work = Path(work_dir)
    cache_key = work / PES_SCAN_RELATIVE_PATH
    now = time.monotonic()
    cached = _scan_cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    result = _provider_snapshot(work) or _provider_native_orca(work) or _provider_frame_dirs(work)
    _scan_cache[cache_key] = (now, result)
    return result


def _cache_clear() -> None:
    _scan_cache.clear()


# ── projections ────────────────────────────────────────────────────────


def build_pes_scan_live_graph(
    job_id: str,
    work_dir: Path | str,
    *,
    job_status: str | None = None,
) -> dict[str, Any] | None:
    """Project the live scan state through the frames contract."""
    from acp.results import energy_graph as _eg

    live = collect_pes_scan_live_frames(work_dir)
    if live is None or not live.frames:
        return None
    frames = sorted(live.frames, key=lambda frame: int(frame.get("index") or 0))
    energies = [_number(frame.get("energy_hartree")) for frame in frames]
    relative = _eg._relative_from_first(energies)
    single_point = [_number(frame.get("sp_energy_hartree")) for frame in frames]
    has_sp = any(value is not None for value in single_point)

    series: list[dict[str, Any]] = []
    for series_id, label, unit, values in (
        ("scan_energy", "扫描能量", "Eh", energies),
        ("relative_energy", "相对能量", "kcal/mol", relative),
        ("single_point_energy", "单点能量", "Eh", single_point),
    ):
        if series_id == "single_point_energy" and not has_sp:
            continue
        if any(value is not None for value in values):
            series.append(
                {
                    "id": series_id,
                    "label": label,
                    "unit": unit,
                    "axis": "left",
                    "values": values,
                    "source": live.source,
                }
            )
    by_id = {item["id"]: item for item in series}
    default_series = (
        "relative_energy"
        if any(value is not None for value in by_id.get("relative_energy", {}).get("values", []))
        else "scan_energy"
    )
    default_values = by_id.get(default_series, {}).get("values", [])

    nodes: list[dict[str, Any]] = []
    for position, frame in enumerate(frames):
        index = int(frame.get("index") or 0)
        failed = str(frame.get("status") or "").lower() == "failed" or not bool(
            frame.get("converged", True)
        )
        x = _number(frame.get("target_coordinate"))
        if x is None:
            x = _number(frame.get("actual_coordinate"))
        if x is None:
            x = float(index)
        nodes.append(
            TrajectoryFrame(
                frame_id=f"frame_{index}",
                label=f"Frame {index + 1}",
                frame_index=index,
                x=x,
                energy=default_values[position] if position < len(default_values) else None,
                status="failed" if failed else "converged",
                geometry_ref=str(frame.get("geometry_ref") or ""),
                metadata={
                    "target_coordinate": _number(frame.get("target_coordinate")),
                    "actual_coordinate": _number(frame.get("actual_coordinate")),
                    "scan_energy_hartree": energies[position],
                    "single_point_energy_hartree": single_point[position],
                    "single_point_status": frame.get("sp_status"),
                },
            ).to_node(VIEW_REGISTRY["scan"].node_type)
        )

    annotations: list[dict[str, Any]] = []
    finite_nodes = [node for node in nodes if node.get("energy") is not None]
    if finite_nodes:
        minimum = min(finite_nodes, key=lambda node: float(node["energy"]))
        annotations.append(
            TrajectoryAnnotation(
                id=f"minimum_{minimum['frame_index']}",
                type="minimum",
                label="最低能量",
                frame_index=minimum["frame_index"],
                x=minimum["x"],
                y=minimum["energy"],
                status=minimum["status"],
                geometry_ref=minimum["geometry_ref"],
            ).to_annotation()
        )
    for node in nodes:
        if node["status"] == "failed":
            annotations.append(
                TrajectoryAnnotation(
                    id=f"failed_{node['frame_index']}",
                    type="failed",
                    label="未收敛",
                    frame_index=node["frame_index"],
                    x=node["x"],
                    y=node["energy"],
                    status="failed",
                    geometry_ref=node["geometry_ref"],
                ).to_annotation()
            )

    complete = live.stage in TERMINAL_STAGES
    metadata: dict[str, Any] = {
        "live": True,
        "scan_stage": live.stage,
        "driver": live.driver,
        "points_total": live.points_total,
        "frame_count": len(nodes),
        "x_source": live.x_source,
        "coordinate": live.coordinate,
    }
    if job_status:
        metadata["job_status"] = str(job_status)
    projection: dict[str, Any] = _eg._sanitize_json(
        {
            "job_id": job_id,
            "view_type": "scan",
            "title": VIEW_REGISTRY["scan"].title_zh,
            "status": live.stage,
            "complete": complete,
            "revision": _eg._revision({"frames": frames, "stage": live.stage}),
            "default_series": default_series,
            "available_views": ["scan"],
            "x_axis": _eg._coordinate_axis({"protocol": {"coordinate": live.coordinate}}),
            "series": series,
            "nodes": nodes,
            "edges": [],
            "annotations": annotations,
            "source": live.source,
            "provenance": {"provider": "pes_scan_live", "driver": live.driver},
            "metadata": metadata,
        }
    )
    return projection


def build_pes_scan_pending_energy_graph(
    job_id: str,
    *,
    job_status: str | None = None,
) -> dict[str, Any]:
    """Return the HTTP-200 waiting projection while no scan point exists yet."""
    from acp.results import energy_graph as _eg

    status_key = str(job_status or "").strip().lower()
    if status_key in _TERMINAL_JOB_STATUSES:
        return _eg.build_unavailable_energy_graph(
            job_id, workflow="PESsearch", reason="pes_scan_no_data"
        )
    return {
        "job_id": job_id,
        "view_type": "scan",
        "title": VIEW_REGISTRY["scan"].title_zh,
        "status": "running",
        "complete": False,
        "revision": "",
        "default_series": "",
        "available_views": ["scan"],
        "x_axis": {},
        "series": [],
        "nodes": [],
        "edges": [],
        "annotations": [],
        "source": "",
        "provenance": {},
        "metadata": {
            "reason": "pes_scan_pending",
            "live": True,
            "job_status": str(job_status or ""),
        },
    }
