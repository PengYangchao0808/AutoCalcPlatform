"""
xTB Relaxed-Scan Helpers
========================

Pure helpers for driving xTB constrained optimization along a reaction
coordinate: xcontrol ``$constrain`` block generation (1-based atom indices,
xTB convention) and the relaxed-scan result containers.

Author: QCcalc Team
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from cccp.qc.interfaces.constraints import (
    AngleConstraint,
    CoordinateConstraint,
    CoordinateSpec,
    DihedralConstraint,
    DistanceConstraint,
    ReactionCoordinatePlan,
)


def xcontrol_constraint_block(constraints: Sequence[CoordinateConstraint]) -> str:
    """Render xTB xcontrol ``$constrain`` lines for *constraints*.

    xTB uses 1-based atom indices and the per-line syntax
    ``<kind>: <i>, <j>[, <k>[, <l>]], <value>[, <force_constant>]``.
    """
    if not constraints:
        return ""
    lines = ["$constrain"]
    for c in constraints:
        if isinstance(c, DistanceConstraint):
            kind = "distance"
        elif isinstance(c, AngleConstraint):
            kind = "angle"
        else:
            kind = "dihedral"
        atoms = ", ".join(str(a + 1) for a in c.atoms)
        line = f"  {kind}: {atoms}, {c.target:.6f}"
        if c.force_constant is not None:
            line += f", {c.force_constant:.4f}"
        lines.append(line)
    lines.append("$end")
    return "\n".join(lines)


def plan_from_dict(data: dict[str, Any]) -> ReactionCoordinatePlan:
    """Build a :class:`ReactionCoordinatePlan` from a plain JSON-style dict."""
    coordinates_raw = data.get("coordinates")
    if not isinstance(coordinates_raw, list):
        raise ValueError("scan_plan: 'coordinates' must be a list")
    coordinates = tuple(CoordinateSpec.from_dict(c) for c in coordinates_raw)
    return ReactionCoordinatePlan(
        coordinates=coordinates,
        points=int(data.get("points") or 21),
        coupling=str(data.get("coupling") or "synchronous"),  # type: ignore[arg-type]
        start_from=str(data.get("start_from") or "reactant"),  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class RelaxedScanPoint:
    """One optimized frame of a relaxed scan.

    Attributes:
        frame_index: Index in [0, points).
        progress: Synchronous progress λ = frame_index/(points−1).
        coordinates: Optimized geometry (Å, N×3) or None on failure.
        symbols: Element symbols or None on failure.
        energy_hartree: Converged total energy or None on failure.
        success: Whether the frame optimization converged.
        coordinate_values: Coordinate id → target value at this frame.
    """

    frame_index: int
    progress: float
    coordinates: NDArray[np.float64] | None
    symbols: list[str] | None
    energy_hartree: float | None
    success: bool
    coordinate_values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class RelaxedScanResult:
    """Aggregated relaxed-scan outcome.

    Attributes:
        points: Per-frame results (one per plan frame).
        input_xyz: Input geometry used for the first frame.
        scan_dir: Directory holding all frame outputs.
        success: Whether the scan completed (or aborted early on failure).
        message: Human-readable status / failure note.
    """

    points: list[RelaxedScanPoint]
    input_xyz: Path
    scan_dir: Path
    success: bool
    message: str = ""

    def energies(self) -> list[Optional[float]]:
        return [p.energy_hartree for p in self.points]

    def best_point(self) -> Optional[RelaxedScanPoint]:
        """Frame with the lowest converged energy (or None when empty)."""
        converged = [p for p in self.points if p.success and p.energy_hartree is not None]
        if not converged:
            return None
        return min(converged, key=lambda p: float(p.energy_hartree))

    def __len__(self) -> int:
        return len(self.points)


__all__ = [
    "RelaxedScanPoint",
    "RelaxedScanResult",
    "plan_from_dict",
    "xcontrol_constraint_block",
]
