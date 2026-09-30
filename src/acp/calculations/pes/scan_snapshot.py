"""Live PES scan snapshot writer (``pes_scan_trajectory_v1``).

Publishes the relaxed-scan curve while the scan runs so the energy-graph
API can serve per-point data before ``RESULT/pes_search/pes_profile.json``
exists.  The snapshot is display-only: publishing failures are logged and
never propagate into the calculation pipeline.

Data flow (see docs/ACP_Energy_Trajectory_Viewer_DevDoc.md):

* xTB / synchronous-ORCA drivers invoke :meth:`PesScanSnapshotWriter.publish_point`
  from the interface's per-frame callback (``point_callback``);
* native ORCA scans publish nothing during the run — the live reader in
  :mod:`acp.results.pes_scan_live` parses the growing scan artifacts
  (``*.relaxscanact.dat`` + per-point ``*.NNN.xyz``) instead;
* after frame extraction every driver seeds the snapshot via
  :meth:`replace_frames`, and the single-point stage updates the
  ``sp_*`` fields frame by frame via :meth:`publish_sp`;
* :meth:`finalize` freezes the terminal stage (first terminal wins).

Writes are atomic (temporary file + ``os.replace``) so the API can never
read a half-written JSON.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from acp.calculations.pes.outputs import _write_json_atomic
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint
from cccp.utils.file_io import write_xyz
from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA_VERSION = "pes_scan_trajectory_v1"
SNAPSHOT_RELATIVE_PATH = "RESULT/trajectories/pes_scan_trajectory.json"
SNAPSHOT_DIR_RELATIVE_PATH = "RESULT/trajectories"
SCAN_FRAMES_DIR_NAME = "scan_frames"
TERMINAL_STAGES = frozenset({"completed", "failed", "cancelled"})

__all__ = [
    "SNAPSHOT_DIR_RELATIVE_PATH",
    "SNAPSHOT_RELATIVE_PATH",
    "SNAPSHOT_SCHEMA_VERSION",
    "PesScanSnapshotWriter",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _measure_actual(
    coordinates: np.ndarray[Any, Any],
    coordinate: dict[str, Any] | None,
) -> float | None:
    """Measure the primary driven coordinate on one geometry (None on failure)."""
    if not coordinate:
        return None
    kind = str(coordinate.get("kind") or "")
    atoms = coordinate.get("atoms")
    if not isinstance(atoms, (list, tuple)) or not atoms:
        return None
    try:
        indices = [int(atom) for atom in atoms]
        if kind == "distance" and len(indices) == 2:
            return float(GeometryUtils.calculate_distance(coordinates, *indices))
        if kind == "angle" and len(indices) == 3:
            return float(GeometryUtils.calculate_angle(coordinates, *indices))
        if kind == "dihedral" and len(indices) == 4:
            return float(GeometryUtils.calculate_dihedral(coordinates, *indices))
    except (ValueError, IndexError, TypeError):
        return None
    return None


class PesScanSnapshotWriter:
    """Single-writer publisher for ``RESULT/trajectories/pes_scan_trajectory.json``.

    All public methods are defensive: a publishing failure is logged and
    swallowed so the scan pipeline itself is never affected.
    """

    def __init__(
        self,
        result_root: Path | str,
        *,
        scan_dir: Path | str,
        coordinate: dict[str, Any],
        coordinates: Sequence[dict[str, Any]],
        points_total: int,
        driver: str,
    ) -> None:
        self._result_root = Path(result_root)
        self._scan_dir = Path(scan_dir)
        self._task_root = self._result_root.parent
        self._snapshot_dir = self._result_root / "trajectories"
        self._snapshot_path = self._snapshot_dir / "pes_scan_trajectory.json"
        self._scan_frames_dir = self._scan_dir / SCAN_FRAMES_DIR_NAME
        self._coordinate = dict(coordinate or {})
        self._coordinates = [dict(item) for item in (coordinates or [])]
        self._points_total = int(points_total)
        self._driver = str(driver)
        self._stage = "running"
        self._frames: dict[int, dict[str, Any]] = {}
        self._lock = Lock()

    @property
    def snapshot_path(self) -> Path:
        return self._snapshot_path

    def publish_point(self, point: RelaxedScanPoint) -> None:
        """Publish one terminal scan point (success or failure) from a callback."""
        try:
            geometry_ref = ""
            if point.coordinates is not None:
                geometry_ref = self._write_frame_geometry(
                    int(point.frame_index),
                    np.asarray(point.coordinates, dtype=float),
                    [str(symbol) for symbol in (point.symbols or [])],
                    energy=point.energy_hartree,
                )
            target_coordinates = {
                str(key): float(value) for key, value in (point.coordinate_values or {}).items()
            }
            primary_target = next(iter(target_coordinates.values()), None)
            actual = None
            if point.coordinates is not None:
                actual = _measure_actual(
                    np.asarray(point.coordinates, dtype=float), self._coordinate
                )
            unit = "angstrom"
            if str(self._coordinate.get("kind") or "distance") != "distance":
                unit = "degree"
            frame = {
                "frame_id": f"frame_{int(point.frame_index):03d}",
                "index": int(point.frame_index),
                "status": "completed" if point.success else "failed",
                "converged": bool(point.success),
                "target_coordinate": primary_target,
                "actual_coordinate": actual,
                "target_coordinates": target_coordinates,
                "actual_coordinates": (
                    {next(iter(target_coordinates), "coordinate"): actual}
                    if actual is not None
                    else {}
                ),
                "coordinate_unit": unit,
                "energy_hartree": (
                    float(point.energy_hartree) if point.energy_hartree is not None else None
                ),
                "geometry_ref": geometry_ref,
                "sp_energy_hartree": None,
                "sp_status": "pending",
                "published_at": _utc_now(),
            }
            with self._lock:
                self._frames[int(point.frame_index)] = frame
                self._flush_locked()
        except Exception as exc:  # noqa: BLE001 - publishing must never abort the scan
            logger.warning("PES scan snapshot publish_point failed: %s", exc)

    def replace_frames(self, frames: Sequence[Any]) -> None:
        """Replace the frame set with the authoritative extracted :class:`ScanFrame` records."""
        try:
            rebuilt: dict[int, dict[str, Any]] = {}
            for frame in frames:
                index = int(frame.index)
                geometry_ref = self._geometry_ref_for(str(frame.geometry_path or ""))
                sp_status = str(frame.single_point_status or "pending")
                rebuilt[index] = {
                    "frame_id": f"frame_{index:03d}",
                    "index": index,
                    "status": "completed" if bool(frame.optimization_converged) else "failed",
                    "converged": bool(frame.optimization_converged),
                    "target_coordinate": (
                        float(frame.target_coordinate)
                        if frame.target_coordinate is not None
                        else None
                    ),
                    "actual_coordinate": (
                        float(frame.actual_coordinate)
                        if frame.actual_coordinate is not None
                        else None
                    ),
                    "target_coordinates": dict(frame.target_coordinates or {}),
                    "actual_coordinates": dict(frame.actual_coordinates or {}),
                    "coordinate_unit": str(frame.coordinate_unit or "angstrom"),
                    "energy_hartree": (
                        float(frame.scan_energy_hartree)
                        if frame.scan_energy_hartree is not None
                        else None
                    ),
                    "geometry_ref": geometry_ref,
                    "sp_energy_hartree": (
                        float(frame.single_point_energy_hartree)
                        if frame.single_point_energy_hartree is not None
                        else None
                    ),
                    "sp_status": sp_status,
                    "published_at": _utc_now(),
                }
            with self._lock:
                self._frames = rebuilt
                self._flush_locked()
        except Exception as exc:  # noqa: BLE001 - publishing must never abort the scan
            logger.warning("PES scan snapshot replace_frames failed: %s", exc)

    def publish_sp(self, frame_index: int, energy_hartree: float | None, status: str) -> None:
        """Update the single-point fields of one published frame."""
        try:
            with self._lock:
                frame = self._frames.get(int(frame_index))
                if frame is None:
                    return
                frame["sp_energy_hartree"] = (
                    float(energy_hartree) if energy_hartree is not None else None
                )
                frame["sp_status"] = str(status)
                self._flush_locked()
        except Exception as exc:  # noqa: BLE001 - publishing must never abort the scan
            logger.warning("PES scan snapshot publish_sp failed: %s", exc)

    def finalize(self, stage: str) -> None:
        """Freeze the snapshot stage; the first terminal stage wins."""
        try:
            stage_key = str(stage).lower()
            if stage_key not in TERMINAL_STAGES:
                return
            with self._lock:
                if self._stage in TERMINAL_STAGES:
                    return
                self._stage = stage_key
                self._flush_locked()
        except Exception as exc:  # noqa: BLE001 - publishing must never abort the scan
            logger.warning("PES scan snapshot finalize failed: %s", exc)

    def _write_frame_geometry(
        self,
        index: int,
        coordinates: np.ndarray[Any, Any],
        symbols: list[str],
        *,
        energy: float | None,
    ) -> str:
        """Atomically write one frame geometry and return its task-root-relative ref."""
        if not symbols or coordinates.size == 0:
            return ""
        self._scan_frames_dir.mkdir(parents=True, exist_ok=True)
        frame_path = self._scan_frames_dir / f"frame_{index:03d}.xyz"
        tmp_path = self._scan_frames_dir / f".frame_{index:03d}.xyz.tmp"
        if energy is not None:
            write_xyz(
                tmp_path,
                coordinates,
                symbols,
                title=f"pes scan frame {index}",
                energy=float(energy),
            )
        else:
            write_xyz(tmp_path, coordinates, symbols, title=f"pes scan frame {index}")
        os.replace(tmp_path, frame_path)
        return self._geometry_ref_for(
            f"{SCAN_FRAMES_DIR_NAME}/frame_{index:03d}.xyz", base=self._scan_dir
        )

    def _geometry_ref_for(self, scan_relative: str, *, base: Path | None = None) -> str:
        """Map a scan-dir-relative path to a task-root-relative POSIX ref."""
        if not scan_relative:
            return ""
        root = base or self._scan_dir
        try:
            scan_rel = root.resolve().relative_to(self._task_root.resolve()).as_posix()
        except ValueError:
            return ""
        return f"{scan_rel}/{Path(scan_relative).as_posix()}"

    def _flush_locked(self) -> None:
        payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "workflow": "PESsearch",
            "scan_stage": self._stage,
            "driver": self._driver,
            "coordinate": dict(self._coordinate),
            "coordinates": list(self._coordinates),
            "points_total": self._points_total,
            "updated_at": _utc_now(),
            "frames": [self._frames[key] for key in sorted(self._frames)],
        }
        _write_json_atomic(self._snapshot_path, payload)
