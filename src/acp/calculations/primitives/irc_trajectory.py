"""IRC path trajectory capture.

Materialises the ORCA per-direction IRC trajectories (``*_IRC_[FB]_trj.xyz``)
into a stable ``irc_trajectory_v1`` snapshot at
``RESULT/trajectories/irc_trajectory.json`` plus multi-frame path XYZ files at
``RESULT/irc/irc_{direction}_path.xyz``.  Endpoint artifacts written by
``primitives/irc.py`` stay untouched — the path files are separate products.

The live recorder mirrors ``optimization_trajectory.py``: while ORCA runs it
re-reads the growing trajectory files, keeps only fully written frames, and
atomically replaces the snapshot so the viewer never reads a half point.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from cccp.qc.interfaces.orca_ts import (
    IrcPathPoint,
    discover_irc_trajectory_files,
    parse_irc_trajectory_xyz,
)

UTC = timezone.utc  # datetime.UTC is 3.11+; keep the short name on 3.10

logger = logging.getLogger(__name__)

IRC_DIRECTIONS: Final[tuple[str, str]] = ("forward", "reverse")
IRC_TRAJECTORY_SCHEMA: Final[str] = "irc_trajectory_v1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _replace_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via a unique sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            _ = handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    _replace_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _atomic_text_write(path: Path, text: str) -> None:
    _replace_atomic(path, text)


def _write_path_xyz(path: Path, direction: str, points: list[IrcPathPoint]) -> None:
    """Write the direction path as a multi-frame XYZ with energy comments."""
    blocks: list[str] = []
    for point in points:
        coordinates = point.coordinates
        if coordinates is None or not point.symbols:
            continue
        comment = f"IRC {direction} point {point.index}"
        if point.energy_hartree is not None:
            comment = f"{comment} E {point.energy_hartree:.12f}"
        rows = "\n".join(
            f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}"
            for symbol, coord in zip(point.symbols, coordinates)
        )
        blocks.append(f"{len(point.symbols)}\n{comment}\n{rows}\n")
    _atomic_text_write(path, "".join(blocks))


def collect_irc_path(target_dir: Path) -> dict[str, list[IrcPathPoint]]:
    """Parse every available IRC direction trajectory under *target_dir*."""
    files = discover_irc_trajectory_files(Path(target_dir))
    per_direction: dict[str, list[IrcPathPoint]] = {}
    for direction in IRC_DIRECTIONS:
        path = files.get(direction)
        per_direction[direction] = (
            parse_irc_trajectory_xyz(path, direction) if path is not None else []
        )
    return per_direction


def _signature(per_direction: dict[str, list[IrcPathPoint]]) -> str:
    parts: list[str] = []
    for direction in IRC_DIRECTIONS:
        points = per_direction.get(direction) or []
        energies = ",".join(f"{p.index}={p.energy_hartree}" for p in points)
        parts.append(f"{direction}:{len(points)}:{energies}")
    return "|".join(parts)


def _materialize(
    result_dir: Path,
    per_direction: dict[str, list[IrcPathPoint]],
    *,
    status: str,
    complete: bool,
    requested_directions: tuple[str, ...],
    source_log: Path | None,
    warnings: list[str] | None,
) -> dict[str, Any] | None:
    """Write the path XYZs and the JSON snapshot; ``None`` when no points."""
    result_dir = Path(result_dir)
    frames: list[dict[str, Any]] = []
    geometry_files: dict[str, str] = {}
    for direction in IRC_DIRECTIONS:
        points = per_direction.get(direction) or []
        if not points:
            continue
        path = result_dir / "irc" / f"irc_{direction}_path.xyz"
        _write_path_xyz(path, direction, points)
        relative_path = path.relative_to(result_dir).as_posix()
        geometry_files[direction] = relative_path
        for point in points:
            point_path = result_dir / "irc" / f"irc_{direction}_point_{point.index:04d}.xyz"
            if not point_path.is_file():
                _write_path_xyz(point_path, direction, [point])
            if point_path.is_file():
                point_ref = f"RESULT/irc/{point_path.name}"
            else:
                point_ref = f"RESULT/irc/{path.name}"
            frames.append(
                {
                    "direction": direction,
                    "index": point.index,
                    "frame_index": point.index,
                    "energy_hartree": point.energy_hartree,
                    "status": "completed",
                    "geometry_ref": point_ref,
                    "atom_count": len(point.symbols),
                    "comment": point.comment,
                }
            )
    if not frames:
        return None
    energies = [frame["energy_hartree"] for frame in frames if frame["energy_hartree"] is not None]
    payload: dict[str, Any] = {
        "schema": IRC_TRAJECTORY_SCHEMA,
        "schema_version": 1,
        "workflow": "irc",
        "status": status,
        "complete": bool(complete),
        "reference_energy_hartree": min(energies) if energies else None,
        "directions": [direction for direction in IRC_DIRECTIONS if per_direction.get(direction)],
        "requested_directions": list(requested_directions),
        "geometry_files": geometry_files,
        "frames": frames,
        "source": {
            "backend": "orca",
            "work_dir": str(source_log.parent) if source_log is not None else "",
            "log": str(source_log) if source_log is not None else "",
        },
        "updated_at": _now(),
    }
    if warnings:
        payload["warnings"] = list(warnings)
    _atomic_json_write(result_dir / "trajectories" / "irc_trajectory.json", payload)
    return payload


def write_irc_trajectory(
    result_dir: Path,
    *,
    target_dir: Path,
    directions: tuple[str, ...] = IRC_DIRECTIONS,
    status: str = "completed",
    complete: bool = True,
    source_log: Path | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any] | None:
    """Parse *target_dir* and persist the IRC path trajectory snapshot."""
    per_direction = collect_irc_path(target_dir)
    return _materialize(
        result_dir,
        per_direction,
        status=status,
        complete=complete,
        requested_directions=directions,
        source_log=source_log,
        warnings=warnings,
    )


@dataclass
class IrcTrajectoryRecorder:
    """Publish completed IRC path points while ORCA is still running."""

    result_dir: Path
    target_dir: Path
    directions: tuple[str, ...] = IRC_DIRECTIONS
    min_interval: float = 2.0
    persist: bool = True
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )
    _write_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )
    _last_refresh: float = field(default=0.0, init=False, repr=False, compare=False)
    _last_signature: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.result_dir = Path(self.result_dir)
        self.target_dir = Path(self.target_dir)

    def feed_line(self, _line: str) -> None:
        """Throttled refresh hook for ``ORCAInterface.irc(output_callback=...)``."""
        if not self.persist:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last_refresh < self.min_interval:
                return
            self._last_refresh = now
        _ = self.refresh()

    def refresh(self, *, force: bool = False) -> dict[str, Any] | None:
        """Re-read fully written frames and atomically replace the snapshot."""
        if not self.persist:
            return None
        per_direction = collect_irc_path(self.target_dir)
        signature = _signature(per_direction)
        with self._lock:
            if not force and signature == self._last_signature:
                return None
            self._last_signature = signature
        try:
            with self._write_lock:
                return _materialize(
                    self.result_dir,
                    per_direction,
                    status="running",
                    complete=False,
                    requested_directions=self.directions,
                    source_log=None,
                    warnings=None,
                )
        except (OSError, ValueError):
            logger.debug("Could not refresh IRC trajectory snapshot", exc_info=True)
            return None

    def finish(self, *, status: str, complete: bool) -> dict[str, Any] | None:
        """Publish the terminal snapshot after the backend call returns."""
        if not self.persist:
            return None
        per_direction = collect_irc_path(self.target_dir)
        signature = _signature(per_direction)
        with self._lock:
            self._last_signature = signature
            self._last_refresh = time.monotonic()
        try:
            with self._write_lock:
                return _materialize(
                    self.result_dir,
                    per_direction,
                    status=status,
                    complete=complete,
                    requested_directions=self.directions,
                    source_log=None,
                    warnings=None,
                )
        except (OSError, ValueError):
            logger.debug("Could not finalize IRC trajectory snapshot", exc_info=True)
            return None


__all__ = [
    "IRC_DIRECTIONS",
    "IRC_TRAJECTORY_SCHEMA",
    "IrcTrajectoryRecorder",
    "collect_irc_path",
    "write_irc_trajectory",
]
