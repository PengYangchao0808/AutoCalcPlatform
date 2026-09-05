"""Constraint-residual gating and target-first x-axis for PES scans.

Regression coverage for the 2026-09-04 double-bond scan incident:
off-constraint frames must be flagged, candidate recommendation must be
suppressed, and the energy graph must plot the prescribed (target)
coordinate instead of drifting actuals.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from acp.calculations.pes.contracts import (
    EnergyProfile,
    ScanCoordinate,
    ScanFrame,
    ScanQuality,
)
from acp.calculations.pes.scan import (
    _constraint_tolerances,
    _extract_frames,
    _recommend_candidates,
)
from acp.results.energy_graph import build_s2_energy_graph
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult


def _scan_frame(
    index: int,
    target: float,
    actual: float,
    *,
    residual: float,
) -> ScanFrame:
    return ScanFrame(
        index=index,
        target_coordinate=target,
        actual_coordinate=actual,
        geometry_path=f"scan_frames/frame_{index:03d}.xyz",
        scan_energy_hartree=-100.0 + index * 0.01,
        optimization_converged=True,
        target_coordinates={"coordinate_1": target},
        actual_coordinates={"coordinate_1": actual},
        constraint_residuals={"coordinate_1": residual},
        constraint_residual_ok=abs(residual) <= 0.01,
        max_constraint_residual=abs(residual),
    )


def _profile() -> EnergyProfile:
    return EnergyProfile(
        energy_source="scan",
        unit="kcal/mol",
        relative_energies_kcal_mol=(0.0, 1.0, 2.0),
        raw_hartree=(-100.0, -99.99, -99.98),
    )


def test_constraint_tolerances_default_and_override() -> None:
    defaults = _constraint_tolerances({})
    assert defaults["distance"] == 0.01
    assert defaults["angle"] == 0.5
    overridden = _constraint_tolerances(
        {"pes_scan": {"constraint_residual_tolerance_angstrom": 0.05}}
    )
    assert overridden["distance"] == 0.05
    assert overridden["angle"] == 0.5


def test_extract_frames_flags_off_constraint_frames(tmp_path: Path) -> None:
    coordinates = (
        ScanCoordinate(kind="distance", atoms=(0, 1), start=1.5, end=2.5, n_points=3),
        ScanCoordinate(kind="distance", atoms=(2, 3), start=1.4, end=2.4, n_points=3),
    )
    geometry = np.zeros((4, 3), dtype=float)
    geometry[1][0] = 1.5
    geometry[3][0] = 1.4
    points = [
        RelaxedScanPoint(
            frame_index=0,
            progress=0.0,
            coordinates=geometry.copy(),
            symbols=["C", "C", "C", "C"],
            energy_hartree=-10.0,
            success=True,
            coordinate_values={"coordinate_1": 1.5, "coordinate_2": 1.4},
        ),
        RelaxedScanPoint(
            frame_index=1,
            progress=0.5,
            coordinates=geometry.copy(),
            symbols=["C", "C", "C", "C"],
            energy_hartree=-9.9,
            success=True,
            coordinate_values={"coordinate_1": 2.0, "coordinate_2": 1.9},
        ),
    ]
    result = RelaxedScanResult(
        points=points, input_xyz=tmp_path / "start.xyz", scan_dir=tmp_path, success=True
    )
    frames = _extract_frames(
        result,
        coordinates[0],
        tmp_path,
        coordinates=coordinates,
        tolerances={"distance": 0.01},
    )
    assert frames[0].constraint_residual_ok is True
    assert frames[0].constraint_residuals["coordinate_1"] == 0.0
    # frame 1 geometry was not advanced: both residuals exceed tolerance
    assert frames[1].constraint_residual_ok is False
    assert frames[1].constraint_residuals["coordinate_1"] == -0.5
    assert frames[1].constraint_residuals["coordinate_2"] == -0.5
    assert frames[1].invalid_reasons


def test_recommend_candidates_suppressed_when_constraints_violated(
    tmp_path: Path,
) -> None:
    frames = [
        _scan_frame(0, 1.5, 1.5, residual=0.0),
        _scan_frame(1, 2.0, 1.5, residual=-0.5),
        _scan_frame(2, 2.5, 1.5, residual=-1.0),
    ]
    coordinate = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.5, end=2.5, n_points=3)
    ts, ints, quality = _recommend_candidates(
        frames,
        coordinate,
        _profile(),
        {},
        tmp_path,
        coordinates=(coordinate,),
        constraints_satisfied=False,
        constraint_tolerance=0.01,
        max_constraint_residual=1.0,
    )
    assert ts == [] and ints == []
    assert quality.status == "invalid"
    assert quality.constraints_satisfied is False
    assert quality.max_constraint_residual == 1.0
    assert "constraint_residual_exceeded" in quality.notes


def test_recommend_candidates_normal_when_constraints_hold(tmp_path: Path) -> None:
    frames = [
        _scan_frame(0, 1.5, 1.5, residual=0.001),
        _scan_frame(1, 2.0, 2.0, residual=-0.002),
        _scan_frame(2, 2.5, 2.5, residual=0.0),
    ]
    coordinate = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.5, end=2.5, n_points=3)
    ts, ints, quality = _recommend_candidates(
        frames,
        coordinate,
        _profile(),
        {},
        tmp_path,
        coordinates=(coordinate,),
        constraints_satisfied=True,
        constraint_tolerance=0.01,
        max_constraint_residual=0.002,
    )
    assert quality.status != "invalid"
    assert quality.constraints_satisfied is True
    assert quality.max_constraint_residual == 0.002


def test_scan_quality_dict_carries_constraint_fields() -> None:
    quality = ScanQuality(
        status="invalid",
        constraints_satisfied=False,
        constraint_tolerance=0.01,
        max_constraint_residual=1.6,
    )
    payload = quality.to_dict()
    assert payload["constraints_satisfied"] is False
    assert payload["constraint_tolerance"] == 0.01
    assert payload["max_constraint_residual"] == 1.6


def test_energy_graph_plots_target_coordinate_not_drifting_actual() -> None:
    payload = {
        "status": "completed",
        "protocol": {"coordinate": {"kind": "distance", "unit": "angstrom"}},
        "scan": {
            "quality": {
                "scan_complete": True,
                "constraints_satisfied": False,
                "max_constraint_residual": 1.6,
                "constraint_tolerance": 0.01,
            },
            "frames": [
                {
                    "index": 0,
                    "target_coordinate": 1.5,
                    "actual_coordinate": 1.47,
                    "geometry_path": "f0.xyz",
                    "scan_energy_hartree": -100.0,
                    "optimization_converged": True,
                    "single_point_status": "skipped",
                },
                {
                    "index": 1,
                    "target_coordinate": 2.0,
                    "actual_coordinate": 1.48,
                    "geometry_path": "f1.xyz",
                    "scan_energy_hartree": -99.9,
                    "optimization_converged": True,
                    "single_point_status": "skipped",
                },
            ],
        },
        "energy_profile": {
            "energy_source": "scan",
            "relative_energies_kcal_mol": [0.0, 6.3],
            "raw_hartree": [-100.0, -99.9],
        },
        "recommendations": {"ts": [], "intermediates": []},
    }
    graph = build_s2_energy_graph("job-1", payload)
    # x follows the monotone prescribed coordinate even when actuals drift
    assert [node["x"] for node in graph["nodes"]] == [1.5, 2.0]
    assert graph["metadata"]["constraints_satisfied"] is False
    assert graph["metadata"]["max_constraint_residual"] == 1.6
