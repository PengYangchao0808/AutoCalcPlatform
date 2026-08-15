from __future__ import annotations

# pyright: reportAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportExplicitAny=false, reportMissingParameterType=false, reportMissingTypeArgument=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnnecessaryCast=false, reportUnusedCallResult=false
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from acp.backends.base import QCResult
from acp.mechanism.models import ArtifactRef, PathCandidate, PathPoint, PathResult, StableState
from acp.mechanism.presets import FidelityProfile
from acp.mechanism.primitives import build_xtb_path_profile, policy_from_config, select_path_seeds
from acp.mechanism.providers.native_peb import NativeReversePebStrategy
from acp.mechanism.strategies import run_rph_reverse
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.xtb_path import PathSearchResult
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

SYMBOLS = ["C", "C", "C", "C"]
PATH_DISTANCES_PRODUCT_TO_STRETCH = [1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0]
PATH_DISTANCES_STRETCH_TO_PRODUCT = list(reversed(PATH_DISTANCES_PRODUCT_TO_STRETCH))
PATH_PROFILE_KCAL = [0.0, 0.8, 1.8, 3.5, 6.0, 5.3, 4.0, 2.8, 2.0]


def _write_xyz(path: Path, coords: NDArray[np.float64], symbols: list[str] | None = None) -> Path:
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


def _kcal_to_hartree(values: list[float]) -> list[float]:
    return [value / 627.509 for value in values]


def _state(
    state_id: str,
    *,
    role: str,
    coordinates: NDArray[np.float64] | None,
    route_id: str = "route-native",
) -> StableState:
    metadata = {"route_id": route_id, "symbols": list(SYMBOLS)}
    if coordinates is not None:
        metadata["coordinates"] = np.asarray(coordinates, dtype=float).tolist()
    return StableState(
        state_id=state_id,
        role=role,  # type: ignore[arg-type]
        canonical_geometry=ArtifactRef(
            path=f"memory://{state_id}",
            sha256=f"sha256:{state_id}",
            kind="stable_state_geometry",
        ),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        metadata=metadata,
    )


def _plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="rc1",
                kind="distance",
                atoms=(0, 1),
                start=4.6,
                end=1.4,
            ),
        ),
        points=17,
    )


def _prepare_coarse_scan(scan_dir: Path) -> RelaxedScanResult:
    frame_dir = scan_dir / "scan_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    points: list[RelaxedScanPoint] = []
    distances = [1.4 + 0.2 * index for index in range(17)]
    energies = _kcal_to_hartree(
        [0.0, 0.2, 0.4, 0.8, 1.3, 2.0, 2.8, 3.7, 4.6, 5.2, 5.8, 6.1, 6.0, 5.7, 5.3, 5.0, 4.8]
    )
    for index, (distance, energy) in enumerate(zip(distances, energies, strict=True)):
        coords = (
            _chain_coords(distance)
            if index < 13
            else _chain_coords(distance, atom2_x=5.5, atom3_x=6.9)
        )
        _ = _write_xyz(frame_dir / f"frame_{index:03d}.xyz", coords)
        points.append(
            RelaxedScanPoint(
                frame_index=index,
                progress=index / 16,
                coordinates=coords,
                symbols=list(SYMBOLS),
                energy_hartree=energy,
                success=True,
                coordinate_values={"rc1": distance},
            )
        )
    return RelaxedScanResult(
        points=points,
        input_xyz=scan_dir / "coarse_reverse_peb_start.xyz",
        scan_dir=scan_dir,
        success=True,
        message="",
    )


def _prepare_path_frames(path_dir: Path) -> list[Path]:
    frame_dir = path_dir / "path_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []
    for index, distance in enumerate(PATH_DISTANCES_STRETCH_TO_PRODUCT):
        frame_paths.append(
            _write_xyz(frame_dir / f"path_frame_{index:03d}.xyz", _chain_coords(distance))
        )
    return frame_paths


def _energy_by_distance_kcal() -> dict[float, float]:
    return {
        round(distance, 1): energy
        for distance, energy in zip(
            PATH_DISTANCES_PRODUCT_TO_STRETCH, PATH_PROFILE_KCAL, strict=True
        )
    }


def _distance_key(coordinates: NDArray[np.float64]) -> float:
    return round(abs(float(coordinates[1, 0] - coordinates[0, 0])), 1)


def _expected_selection(path_frames_product_to_stretch: list[Path]) -> Any:
    profile = build_xtb_path_profile(
        frame_paths=path_frames_product_to_stretch,
        energies_hartree=_kcal_to_hartree(PATH_PROFILE_KCAL),
        forming_bonds=[(0, 1)],
        product_xyz=path_frames_product_to_stretch[0],
        off_path_indices=[],
    )
    return select_path_seeds(profile, policy_from_config({}, {}))


def test_native_reverse_peb_builds_rph_parity_path_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture: dict[str, Path] = {}
    energy_by_distance = _energy_by_distance_kcal()
    b97_hartree = {
        distance: -200.0 + (energy + 0.25) / 627.509
        for distance, energy in energy_by_distance.items()
    }
    path_frames_returned = _prepare_path_frames(tmp_path / "native_run" / "xtb_path")

    def fake_relaxed_scan(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        scan_coordinate: CoordinateSpec,
        points: int,
        **kwargs: object,
    ) -> RelaxedScanResult:
        del self, coordinates, symbols, scan_coordinate, points, kwargs
        return _prepare_coarse_scan(
            tmp_path / "native_run" / "s2" / "reactant__product" / "coarse_scan"
        )

    def fake_path_search(
        self,
        start_xyz: Path,
        end_xyz: Path,
        output_dir: Path,
        **kwargs: object,
    ) -> PathSearchResult:
        del self, output_dir, kwargs
        capture["start_xyz"] = Path(start_xyz)
        capture["end_xyz"] = Path(end_xyz)
        return PathSearchResult(
            frame_paths=path_frames_returned,
            energies_hartree=[None] * len(path_frames_returned),
            success=True,
        )

    def fake_xtb_single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        del self, symbols, charge, multiplicity, output_dir, kwargs
        distance = _distance_key(np.asarray(coordinates, dtype=float))
        return QCResult(success=True, energy=-100.0 + energy_by_distance[distance] / 627.509)

    def fake_orca_single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        del self, symbols, charge, multiplicity, output_dir, kwargs
        distance = _distance_key(np.asarray(coordinates, dtype=float))
        return QCResult(success=True, energy=b97_hartree[distance])

    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.ORCAInterface.relaxed_scan",
        fake_relaxed_scan,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.XTBPathInterface.path_search",
        fake_path_search,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.XTBBackend.single_point",
        fake_xtb_single_point,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.ORCABackend.single_point",
        fake_orca_single_point,
    )

    strategy = NativeReversePebStrategy(config={}, work_root=tmp_path / "native_run")
    result = strategy.search(
        _state("reactant", role="reactant", coordinates=None),
        _state("product", role="product", coordinates=_chain_coords(1.4)),
        _plan(),
        FidelityProfile(name="s3"),
    )

    expected_frames = list(reversed(path_frames_returned))
    expected_selection = _expected_selection(expected_frames)
    expected_ts_frame = int(expected_selection.ts_search_seed["frame_index"])
    expected_int_frame = int(expected_selection.int_search_seed["frame_index"])

    assert capture["start_xyz"].name == "frame_012.xyz"
    assert capture["end_xyz"].name == "product.xyz"
    assert len(result.points) == 9
    assert all(set(point.energies_hartree) == {"xtb", "b97-3c"} for point in result.points)
    assert result.selected_ts_id == f"ts_candidate_p{expected_ts_frame:03d}"
    assert result.selected_int_id == f"int_candidate_p{expected_int_frame:03d}"
    assert [candidate.kind for candidate in result.seed_candidates] == [
        "ts_seed",
        "intermediate_seed",
    ]
    assert any(candidate.kind == "ts_seed" for candidate in result.candidates)
    assert any(candidate.kind == "intermediate_seed" for candidate in result.candidates)
    assert all(seed.stationary_point_claimed is False for seed in result.seed_candidates)
    assert set(result.metadata["gate_policies"]["G2"]) == {
        "require_profile_complete",
        "require_b97_full_coverage",
        "require_valid_corridor",
        "require_effective_endpoint",
        "require_knee",
        "require_ts_seed",
        "profile_complete",
        "b97_full_coverage",
        "selector",
    }
    assert result.metadata["gate_policies"]["G2"]["b97_full_coverage"] is True
    assert result.complete is True
    assert Path(str(result.artifacts["scan_profile"])).is_file()
    assert Path(str(result.artifacts["product_xyz"])).is_file()
    assert len(result.artifacts["geometry_sha256_by_point"]) == 9

    scan_profile = Path(str(result.artifacts["scan_profile"])).read_text(encoding="utf-8")
    assert '"forming_bonds": [' in scan_profile
    assert '"anchor_frame_index": 12' in scan_profile


def test_native_reverse_peb_requires_full_b97_coverage_for_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    energy_by_distance = _energy_by_distance_kcal()
    path_frames_returned = _prepare_path_frames(tmp_path / "native_partial" / "xtb_path")

    def fake_relaxed_scan(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        scan_coordinate: CoordinateSpec,
        points: int,
        **kwargs: object,
    ) -> RelaxedScanResult:
        del self, coordinates, symbols, scan_coordinate, points, kwargs
        return _prepare_coarse_scan(
            tmp_path / "native_partial" / "s2" / "reactant__product" / "coarse_scan"
        )

    def fake_path_search(
        self,
        start_xyz: Path,
        end_xyz: Path,
        output_dir: Path,
        **kwargs: object,
    ) -> PathSearchResult:
        del self, start_xyz, end_xyz, output_dir, kwargs
        return PathSearchResult(
            frame_paths=path_frames_returned,
            energies_hartree=[None] * len(path_frames_returned),
            success=True,
        )

    def fake_xtb_single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        del self, symbols, charge, multiplicity, output_dir, kwargs
        distance = _distance_key(np.asarray(coordinates, dtype=float))
        return QCResult(success=True, energy=-100.0 + energy_by_distance[distance] / 627.509)

    def fake_orca_single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: object,
    ) -> QCResult:
        del self, symbols, charge, multiplicity, output_dir, kwargs
        distance = _distance_key(np.asarray(coordinates, dtype=float))
        if distance == 2.4:
            return QCResult(success=False, energy=None, error_message="mock partial coverage")
        return QCResult(success=True, energy=-200.0 + energy_by_distance[distance] / 627.509)

    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.ORCAInterface.relaxed_scan",
        fake_relaxed_scan,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.XTBPathInterface.path_search",
        fake_path_search,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.XTBBackend.single_point",
        fake_xtb_single_point,
    )
    monkeypatch.setattr(
        "acp.mechanism.providers.native_peb.ORCABackend.single_point",
        fake_orca_single_point,
    )

    strategy = NativeReversePebStrategy(config={}, work_root=tmp_path / "native_partial")
    result = strategy.search(
        _state("reactant", role="reactant", coordinates=None),
        _state("product", role="product", coordinates=_chain_coords(1.4)),
        _plan(),
        FidelityProfile(name="s3"),
    )

    assert result.metadata["gate_policies"]["G2"]["b97_full_coverage"] is False
    assert result.complete is False


def test_native_reverse_peb_requires_distance_coordinate(tmp_path: Path) -> None:
    strategy = NativeReversePebStrategy(config={}, work_root=tmp_path)
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="a1", kind="angle", atoms=(0, 1, 2), start=100.0, end=120.0),
        ),
        points=5,
    )

    with pytest.raises(ValueError, match="distance coordinate"):
        strategy.search(
            _state("reactant", role="reactant", coordinates=None),
            _state("product", role="product", coordinates=_chain_coords(1.4)),
            plan,
            FidelityProfile(name="s3"),
        )


def test_run_rph_reverse_delegates_to_native_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeStrategy:
        def __init__(
            self, config: dict[str, object] | None = None, *, work_root: Path | str | None = None
        ) -> None:
            captured["config"] = config
            captured["work_root"] = work_root

        def search(
            self,
            source_state: StableState,
            target_state: StableState | None,
            coordinate_plan: ReactionCoordinatePlan,
            profile: object,
        ) -> PathResult:
            captured["source_state"] = source_state
            captured["target_state"] = target_state
            captured["coordinate_plan"] = coordinate_plan
            captured["profile"] = profile
            return PathResult(
                points=[PathPoint(point_id="p000", progress=0.5, coordinate_values={"rc1": 2.0})],
                candidates=[
                    PathCandidate(
                        candidate_id="ts_candidate_p000",
                        kind="ts_seed",
                        point_id="p000",
                        reason="endpoint_knee_shift_midpoint_v1",
                        progress=0.5,
                    )
                ],
                strategy="rph-reverse",
                route_id="route-native",
                selected_ts_id="ts_candidate_p000",
            )

    monkeypatch.setattr("acp.mechanism.strategies.NativeReversePebStrategy", FakeStrategy)

    route = SimpleNamespace(
        route_id="route-native",
        coordinate_plan=_plan(),
        path_strategy="rph-reverse",
        fidelity="s3",
        reactant_id="reactant-state",
        product_id="product-state",
        ts_guess_id=None,
        label="",
    )
    coordinates = _chain_coords(1.4)
    result = run_rph_reverse(
        route,
        coordinates=coordinates,
        symbols=list(SYMBOLS),
        charge=0,
        multiplicity=1,
        scan_dir=tmp_path,
        backend=SimpleNamespace(config={"mode": "test"}),
        fidelity=FidelityProfile(name="s3"),
    )

    assert captured["config"] == {"mode": "test"}
    assert captured["work_root"] == tmp_path
    assert isinstance(captured["source_state"], StableState)
    assert isinstance(captured["target_state"], StableState)
    assert cast(StableState, captured["target_state"]).state_id == "product-state"
    assert (
        cast(StableState, captured["target_state"]).metadata["coordinates"] == coordinates.tolist()
    )
    assert result.strategy == "rph-reverse"
    assert result.route_id == "route-native"
    assert result.selected_ts_id == "ts_candidate_p000"
    assert len(result.points) == 1
