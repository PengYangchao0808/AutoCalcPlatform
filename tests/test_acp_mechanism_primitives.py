from __future__ import annotations

# pyright: reportAny=false, reportArgumentType=false, reportMissingTypeArgument=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false
import json
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

from acp.mechanism.primitives import (
    HARTREE_TO_KCAL,
    B97CRelaxedScanRescuer,
    CompositeProfileBuilder,
    ScanAttempt,
    ScanEnergyRefiner,
    SelectionPolicy,
    SinglePointSpec,
    SurfaceScanResult,
    SurfaceScanSpec,
    attempt_manifest,
    build_orca_scan_profile,
    check_scan_trajectory,
    select_path_seeds,
)

SYMBOLS = ["C", "C", "C", "C"]


def _write_xyz(
    path: Path,
    coords: NDArray[np.float64],
    symbols: list[str] | None = None,
) -> Path:
    atoms = symbols or SYMBOLS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _ = handle.write(f"{len(atoms)}\n")
        _ = handle.write(f"{path.stem}\n")
        for symbol, (x_coord, y_coord, z_coord) in zip(atoms, coords):
            _ = handle.write(f"{symbol} {x_coord:.8f} {y_coord:.8f} {z_coord:.8f}\n")
    return path


def _chain_coords(
    bond_01: float,
    *,
    atom2_x: float = 2.8,
    atom3_x: float | None = None,
) -> NDArray[np.float64]:
    atom1_x = 1.4
    atom0_x = atom1_x - bond_01
    atom3 = atom3_x if atom3_x is not None else atom2_x + 1.4
    return np.asarray(
        [
            [atom0_x, 0.0, 0.0],
            [atom1_x, 0.0, 0.0],
            [atom2_x, 0.0, 0.0],
            [atom3, 0.0, 0.0],
        ],
        dtype=float,
    )


def _write_scan_frames(base_dir: Path, bond_distances: list[float]) -> list[Path]:
    frame_paths: list[Path] = []
    for index, distance in enumerate(bond_distances):
        frame_paths.append(_write_xyz(base_dir / f"frame_{index:02d}.xyz", _chain_coords(distance)))
    return frame_paths


def _kcal_profile(values: list[float]) -> list[float]:
    return [value / HARTREE_TO_KCAL for value in values]


def test_build_orca_scan_profile_and_select_path_seeds_knee_shifted(tmp_path: Path) -> None:
    frame_paths = _write_scan_frames(
        tmp_path / "scan",
        [1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0],
    )
    energies = _kcal_profile([0.0, 0.8, 1.8, 3.5, 6.0, 5.3, 4.0, 2.8, 2.0])

    profile = build_orca_scan_profile(
        frames=frame_paths,
        energies_hartree=energies,
        forming_bonds=[(0, 1)],
        product_xyz=frame_paths[0],
        energy_source="B97-3c",
    )
    selection = select_path_seeds(profile, SelectionPolicy())

    assert profile.complete is True
    assert profile.excluded_frames == ()
    assert profile.topology_valid_intervals == ((0, 8),)
    assert profile.frames[4].relative_energy_kcal_mol == pytest.approx(6.0, abs=1e-6)
    assert selection.s2_state == "rescue_seeded"
    assert selection.seed_evidence.endswith("knee_shifted")
    assert selection.ts_search_seed is not None
    assert selection.int_search_seed is not None
    assert selection.ts_search_seed["frame_index"] == 5
    assert selection.int_search_seed["frame_index"] == 6
    assert selection.diagnostics["selection_algorithm"] == "endpoint_knee_shift_midpoint_v1"


def test_check_scan_trajectory_flags_topology_drift(tmp_path: Path) -> None:
    product_coords = _chain_coords(1.4)
    valid_frame = _write_xyz(tmp_path / "valid.xyz", _chain_coords(2.0))
    invalid_frame = _write_xyz(tmp_path / "invalid.xyz", _chain_coords(2.0, atom2_x=5.5))

    result = check_scan_trajectory(
        product_coords=product_coords,
        symbols=SYMBOLS,
        forming_bonds=[(0, 1)],
        frame_paths=[valid_frame, invalid_frame],
    )

    assert result["checked"] is True
    assert result["off_path_indices"] == [1]
    assert any(issue["reason"] == "topology_drift" for issue in result["frame_issues"])
    topology_issue = next(
        issue for issue in result["frame_issues"] if issue["reason"] == "topology_drift"
    )
    assert topology_issue["lost_edges"] >= 1


def test_composite_profile_builder_merges_refinement_attempt(tmp_path: Path) -> None:
    coarse_frames = _write_scan_frames(
        tmp_path / "coarse",
        [1.4, 1.9, 2.4, 2.9, 3.4],
    )
    refinement_frames = _write_scan_frames(
        tmp_path / "refine",
        [1.9, 2.15, 2.4, 2.65, 2.9],
    )
    coarse = ScanAttempt(
        attempt_id="coarse-1",
        kind="coarse",
        directory=tmp_path / "coarse",
        frame_paths=tuple(coarse_frames),
        target_coordinates_A=(1.4, 1.9, 2.4, 2.9, 3.4),
        xtb_energies_hartree=tuple(_kcal_profile([0.0, 1.0, 4.0, 7.0, 4.0])),
    )
    refinement = ScanAttempt(
        attempt_id="refine-1",
        kind="ts_refinement",
        directory=tmp_path / "refine",
        frame_paths=tuple(refinement_frames),
        target_coordinates_A=(1.9, 2.15, 2.4, 2.65, 2.9),
        xtb_energies_hartree=tuple(_kcal_profile([1.0, 2.5, 4.0, 5.5, 7.0])),
    )

    builder = CompositeProfileBuilder(forming_bonds=[(0, 1)])
    composite = builder.build([coarse, refinement])
    coordinates = [point["target_coordinate_A"] for point in composite["points"]]

    assert composite["backbone_attempt_id"] == "coarse-1"
    assert composite["accepted_attempt_ids"] == ["coarse-1", "refine-1"]
    assert coordinates == pytest.approx([1.4, 1.9, 2.15, 2.4, 2.65, 2.9, 3.4])
    assert composite["coverage"]["point_count"] == 7
    assert composite["continuity_checks"][0]["accepted"] is True
    assert composite["continuity_checks"][0]["inserted_into_composite"] is True
    assert len(attempt_manifest([coarse, refinement])) == 2


def test_scan_energy_refiner_reuses_geometry_cache_and_reports_full_coverage(
    tmp_path: Path,
) -> None:
    frame_paths = _write_scan_frames(tmp_path / "frames", [1.4, 2.0])
    calls: list[str] = []
    energy_by_name = {
        frame_paths[0].name: -100.0000,
        frame_paths[1].name: -99.5000,
    }

    def fake_sp(frame_xyz_path: Path) -> float | None:
        calls.append(frame_xyz_path.name)
        return energy_by_name[frame_xyz_path.name]

    refiner = ScanEnergyRefiner(
        tmp_path / "refiner",
        sp_callable=fake_sp,
        spec=SinglePointSpec(method="B97-3c"),
        parallel_jobs=1,
    )
    first = refiner.refine(frame_paths)
    second = refiner.refine(frame_paths)

    assert first["status"] == "complete"
    assert first["full_coverage"] is True
    assert first["energies_hartree"] == pytest.approx([-100.0, -99.5])
    assert calls == [frame_paths[0].name, frame_paths[1].name]
    assert second["status"] == "complete"
    assert second["full_coverage"] is True
    assert calls == [frame_paths[0].name, frame_paths[1].name]
    cached_records = cast(list[dict[str, object]], second["records"])
    assert all(record["reused"] is True for record in cached_records)


def test_scan_energy_refiner_marks_partial_coverage(tmp_path: Path) -> None:
    frame_paths = _write_scan_frames(tmp_path / "frames_partial", [1.4, 2.0])

    def fake_sp(frame_xyz_path: Path) -> float | None:
        if frame_xyz_path.name.endswith("01.xyz"):
            return None
        return -123.456

    refiner = ScanEnergyRefiner(
        tmp_path / "refiner_partial",
        sp_callable=fake_sp,
        spec=SinglePointSpec(method="B97-3c"),
    )
    result = refiner.refine(frame_paths)
    partial_energies = cast(list[float | None], result["energies_hartree"])
    partial_records = cast(list[dict[str, object]], result["records"])

    assert result["status"] == "partial"
    assert result["full_coverage"] is False
    assert result["completed"] == 1
    assert partial_energies[0] == pytest.approx(-123.456)
    assert partial_energies[1] is None
    assert partial_records[1]["error"] == "sp_callable_returned_none"


def test_b97c_relaxed_scan_rescuer_uses_injected_scan_callable(tmp_path: Path) -> None:
    product_xyz = _write_xyz(tmp_path / "product.xyz", _chain_coords(1.4))
    raw_frame_paths = _write_scan_frames(
        tmp_path / "scan_job" / "scan_frames",
        [1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0],
    )
    energies = _kcal_profile([0.0, 0.8, 1.8, 3.5, 6.0, 5.3, 4.0, 2.8, 2.0])
    calls: list[tuple[str, Path, Path]] = []

    def fake_scan(spec: SurfaceScanSpec, input_xyz: Path, output_dir: Path) -> SurfaceScanResult:
        calls.append((spec.method, input_xyz, output_dir))
        return SurfaceScanResult(
            status="complete",
            output_file=output_dir / "scan.out",
            extra={
                "frames": [str(path) for path in raw_frame_paths],
                "energies_hartree": energies,
                "energy_source": "B97-3c",
            },
        )

    rescuer = B97CRelaxedScanRescuer(
        scan_callable=fake_scan,
        scan_config={"stretch_end_A": 3.40, "points": 17},
        selection_config={},
        enabled=True,
        scan_method="B97-3c",
        scan_role="rescue",
    )
    result = rescuer.run(
        product_xyz,
        tmp_path / "rescue",
        [(0, 1)],
        trigger_reasons=["topology_drift"],
    )

    assert len(calls) == 1
    assert result["status"] == "complete"
    assert result["s2_state"] == "rescue_seeded"
    assert result["ts_search_seed"]["frame_index"] == 5
    assert result["int_search_seed"]["frame_index"] == 6
    assert Path(result["ts_xyz"]).name == "frame_05.xyz"
    assert Path(result["intermediate_xyz"]).name == "frame_06.xyz"
    assert all(Path(path).parent == tmp_path / "rescue" for path in result["frames"])

    profile_path = Path(str(result["scan_profile"]))
    assert profile_path.is_file()
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["selections"]["ts_guess"]["index"] == 5
    assert payload["selections"]["intermediate"]["index"] == 6
