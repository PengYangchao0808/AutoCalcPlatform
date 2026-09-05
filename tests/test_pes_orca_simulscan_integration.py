"""Real-ORCA integration: double-bond synchronous scan stays on the path.

Guards the 2026-09-04 incident class end-to-end: with correct 0-based
constraint indices, every frame's optimized geometry must sit on the
prescribed constraint targets (corrector acceptance).  Requires the real
ORCA binary and ``--run-slow``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cccp.config import load_config
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from tests.conftest import requires_orca

# Butane: constrain the two terminal bonds (0-based pairs 0-1 and 2-3).
_BUTANE_SYMBOLS = ["C"] * 4 + ["H"] * 10
_BUTANE_COORDS = np.array(
    [
        [0.000, 0.000, 0.000],
        [1.530, 0.000, 0.000],
        [2.100, 1.450, 0.000],
        [3.630, 1.450, 0.000],
        [-0.500, 0.500, 0.900],
        [-0.500, 0.500, -0.900],
        [-0.500, -1.000, 0.000],
        [1.900, -0.300, 1.000],
        [1.900, -0.300, -1.000],
        [1.700, 2.500, 0.000],
        [3.900, 2.450, 0.000],
        [4.100, 0.500, 0.000],
        [3.900, 2.450, -1.000],
        [4.100, 0.500, 1.000],
    ]
)


def _distance(coords: np.ndarray, i: int, j: int) -> float:
    return float(np.linalg.norm(coords[i] - coords[j]))


@requires_orca
@pytest.mark.slow
def test_orca_synchronous_scan_tracks_both_targets(tmp_path: Path) -> None:
    interface = ORCAInterface(load_config())
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=1.53, end=2.13),
            CoordinateSpec(id="rc2", kind="distance", atoms=(2, 3), start=1.54, end=2.14),
        ),
        points=4,
    )
    result = interface.relaxed_scan(
        _BUTANE_COORDS,
        _BUTANE_SYMBOLS,
        plan=plan,
        charge=0,
        multiplicity=1,
        output_dir=tmp_path,
        output_name="simul_check",
        method="GFN2-xTB",
    )

    assert result.success, result.message
    assert len(result.points) == 4
    for point in result.points:
        assert point.coordinates is not None
        targets = plan.coordinate_targets(point.frame_index)
        assert abs(_distance(point.coordinates, 0, 1) - targets["rc1"]) <= 0.01
        assert abs(_distance(point.coordinates, 2, 3) - targets["rc2"]) <= 0.01
