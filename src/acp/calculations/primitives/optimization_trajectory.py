"""Incremental geometry-optimization trajectory capture.

The calculator process writes a small, atomic JSON snapshot while ORCA is
running.  The workbench can therefore render the last completed optimization
cycle without waiting for the final ``.out`` parser.  Geometry snapshots are
stored beside the JSON file as ordinary XYZ files so a selected cycle can be
loaded by the API.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_CYCLE_RE = re.compile(
    r"^\s*(?:GEOMETRY\s+OPTIMIZATION\s+)?(?:CYCLE|STEP)\s*[:#]?\s*(\d+)",
    re.IGNORECASE,
)
_ENERGY_RE = re.compile(rf"FINAL\s+SINGLE\s+POINT\s+ENERGY\s+({_FLOAT})", re.IGNORECASE)
_RMS_GRADIENT_RE = re.compile(rf"RMS\s+GRAD(?:IENT)?\D+({_FLOAT})", re.IGNORECASE)
_MAX_GRADIENT_RE = re.compile(rf"MAX\s+GRAD(?:IENT)?\D+({_FLOAT})", re.IGNORECASE)
_RMS_DISPLACEMENT_RE = re.compile(rf"RMS\s+DISPLACEMENT\D+({_FLOAT})", re.IGNORECASE)
_MAX_DISPLACEMENT_RE = re.compile(rf"MAX\s+DISPLACEMENT\D+({_FLOAT})", re.IGNORECASE)
_SCF_ITER_RE = re.compile(r"SCF\s+CONVERGED\s+AFTER\s+(\d+)\s+ITER", re.IGNORECASE)
_ATOM_RE = re.compile(
    rf"^\s*(?:\d+\s+)?([A-Za-z][A-Za-z]?)\s+({_FLOAT})\s+({_FLOAT})\s+({_FLOAT})(?:\s|$)"
)
_COORD_HEADER_RE = re.compile(r"CARTESIAN\s+COORDINATES\s*\(\s*ANGSTROEM\s*\)", re.IGNORECASE)
_DASH_RE = re.compile(r"^\s*-{3,}\s*$")


def _float(value: str) -> float:
    """Parse ORCA's Fortran ``D`` exponent notation."""
    return float(value.replace("D", "E").replace("d", "e"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class OptimizationTrajectoryRecorder:
    """Parse optimization output one line at a time and persist snapshots.

    The recorder is deliberately tolerant: ORCA versions and optimization
    modes do not print every convergence metric in exactly the same place.
    A cycle is published as soon as it has an energy, while gradients,
    displacements, and geometry are added when encountered later in the same
    cycle.
    """

    def __init__(self, output_dir: Path, *, item_id: str = "") -> None:
        self.output_dir = Path(output_dir)
        self.path = self.output_dir / "optimization_trajectory.json"
        self.cycles_dir = self.output_dir / "cycles"
        self.item_id = item_id
        self._lock = threading.RLock()
        self._cycles: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._cycle_number = 0
        self._geometry_active = False
        self._geometry_started = False
        self._geometry_rows: list[tuple[str, float, float, float]] = []
        self._status = "running"
        self._converged = False
        self._last_write_signature = ""
        self._write_snapshot()

    def feed_line(self, line: str) -> None:
        """Consume one ORCA stdout line."""
        with self._lock:
            text = str(line).rstrip("\r\n")
            cycle_match = _CYCLE_RE.match(text)
            if cycle_match:
                self._start_cycle(int(cycle_match.group(1)))

            if _COORD_HEADER_RE.search(text):
                self._begin_geometry()
                return

            if self._geometry_active:
                if _DASH_RE.match(text):
                    if self._geometry_started:
                        self._finish_geometry()
                    return
                atom_match = _ATOM_RE.match(text)
                if atom_match:
                    self._geometry_started = True
                    self._geometry_rows.append(
                        (
                            atom_match.group(1),
                            _float(atom_match.group(2)),
                            _float(atom_match.group(3)),
                            _float(atom_match.group(4)),
                        )
                    )
                    return
                if self._geometry_started and not text.strip():
                    self._finish_geometry()

            if self._current is None:
                # Some ORCA output variants omit the explicit cycle banner.
                self._start_cycle(self._cycle_number + 1)
            current = self._current

            energy_match = _ENERGY_RE.search(text)
            if energy_match:
                current["energy_hartree"] = _float(energy_match.group(1))
                self._publish()
            for key, pattern in (
                ("rms_gradient", _RMS_GRADIENT_RE),
                ("max_gradient", _MAX_GRADIENT_RE),
                ("rms_displacement", _RMS_DISPLACEMENT_RE),
                ("max_displacement", _MAX_DISPLACEMENT_RE),
            ):
                match = pattern.search(text)
                if match:
                    current[key] = _float(match.group(1))
                    self._publish()
            scf_match = _SCF_ITER_RE.search(text)
            if scf_match:
                current["scf_iterations"] = int(scf_match.group(1))
                self._publish()

            lower = text.lower()
            if "geometry optimization converged" in lower or "optimization converged" in lower:
                current["status"] = "converged"
                self._converged = True
                self._publish()

    def finish(self, *, converged: bool, status: str | None = None) -> None:
        """Finalize and publish the terminal snapshot."""
        with self._lock:
            if self._geometry_active:
                self._finish_geometry()
            self._publish()
            self._converged = bool(converged)
            self._status = status or ("completed" if converged else "failed")
            if self._current is not None:
                self._current["status"] = (
                    "converged" if converged else self._current.get("status", "failed")
                )
            self._write_snapshot(force=True)

    def _start_cycle(self, number: int) -> None:
        if self._geometry_active:
            self._finish_geometry()
        if self._current is not None and self._current not in self._cycles:
            self._cycles.append(self._current)
        self._cycle_number = max(
            number,
            self._cycle_number + 1 if number <= self._cycle_number else number,
        )
        self._current = {
            "cycle": self._cycle_number,
            "status": "running",
        }
        self._publish()

    def _begin_geometry(self) -> None:
        if self._current is None:
            self._start_cycle(self._cycle_number + 1)
        self._geometry_active = True
        self._geometry_started = False
        self._geometry_rows = []

    def _finish_geometry(self) -> None:
        if not self._geometry_active:
            return
        if self._current is not None and self._geometry_rows:
            cycle = int(self._current["cycle"])
            geometry_path = self.cycles_dir / f"cycle_{cycle:04d}.xyz"
            self.cycles_dir.mkdir(parents=True, exist_ok=True)
            xyz = "\n".join(
                [
                    str(len(self._geometry_rows)),
                    f"optimization cycle {cycle}",
                    *(
                        f"{symbol:2s} {x:.10f} {y:.10f} {z:.10f}"
                        for symbol, x, y, z in self._geometry_rows
                    ),
                    "",
                ]
            )
            _atomic_text_write(geometry_path, xyz)
            self._current["geometry_ref"] = geometry_path.relative_to(self.output_dir).as_posix()
            self._current["atom_count"] = len(self._geometry_rows)
            self._publish()
        self._geometry_active = False
        self._geometry_started = False
        self._geometry_rows = []

    def _publish(self) -> None:
        if self._current is None:
            return
        if self._current not in self._cycles and "energy_hartree" in self._current:
            self._cycles.append(self._current)
        self._write_snapshot()

    def _write_snapshot(self, *, force: bool = False) -> None:
        cycles = [dict(cycle) for cycle in self._cycles]
        if self._current is not None and self._current not in self._cycles:
            cycles.append(dict(self._current))
        payload: dict[str, Any] = {
            "schema_version": 1,
            "item_id": self.item_id,
            "status": self._status,
            "converged": self._converged,
            "current_cycle": self._cycle_number or None,
            "cycles": cycles,
            "updated_at": _now(),
            "source": "ORCA incremental stdout",
        }
        signature = json.dumps(payload, sort_keys=True, default=str)
        if not force and signature == self._last_write_signature:
            return
        self._last_write_signature = signature
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            _atomic_json_write(self.path, payload)
        except OSError:
            logger.debug("Could not update optimization trajectory: %s", self.path, exc_info=True)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a JSON snapshot in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _atomic_text_write(path: Path, text: str) -> None:
    """Atomically replace a small geometry file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


__all__ = ["OptimizationTrajectoryRecorder"]
