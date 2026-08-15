# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false, reportMissingImports=false, reportMissingParameterType=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
"""Tests for ACP guided-scan / xtb-fast M2 mechanism providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np

import acp.mechanism.orchestrator as mechanism_orchestrator
from acp.backends.base import QCResult
from acp.mechanism.identity import (
    StableStateIdentityEvidence,
    classify_stable_state,
    classify_ts_identity,
    compute_rc_alignment_score,
)
from acp.mechanism.models import ArtifactRef, StableState
from acp.mechanism.presets import FidelityProfile
from acp.mechanism.providers.guided_scan import GuidedScanPathStrategy
from acp.mechanism.providers.xtb_ensemble import XtbFastEnsembleProvider
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult
from cccp.utils.file_io import write_xyz_multiframe
from cccp.utils.geometry_tools import GeometryUtils

mechanism_orchestrator.np = np


def _state(
    state_id: str,
    *,
    coordinates: list[list[float]],
    symbols: list[str],
    role: Literal["reactant", "product", "intermediate"] = "reactant",
    route_id: str = "route-main",
) -> StableState:
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=ArtifactRef(
            path=f"memory://{state_id}", sha256=f"sha256:{state_id}", kind="geometry"
        ),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        metadata={"coordinates": coordinates, "symbols": symbols, "route_id": route_id},
    )


class FakeScanBackend:
    def __init__(self, energies: list[float], base_coordinates: np.ndarray) -> None:
        self.energies = energies
        self.base_coordinates = np.asarray(base_coordinates, dtype=float)
        self.calls: list[dict[str, Any]] = []

    def relaxed_scan(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        output_dir: Path,
        plan: ReactionCoordinatePlan,
        **kwargs: Any,
    ) -> RelaxedScanResult:
        del coordinates
        self.calls.append({"output_dir": output_dir, "plan": plan, "kwargs": kwargs})
        points: list[RelaxedScanPoint] = []
        for index, energy in enumerate(self.energies):
            points.append(
                RelaxedScanPoint(
                    frame_index=index,
                    progress=index / (len(self.energies) - 1),
                    coordinates=self.base_coordinates + index * 0.02,
                    symbols=list(symbols),
                    energy_hartree=energy,
                    success=True,
                    coordinate_values=plan.coordinate_targets(index),
                )
            )
        return RelaxedScanResult(
            points=points,
            input_xyz=output_dir / "input.xyz",
            scan_dir=output_dir,
            success=True,
        )


class FakeSpBackend:
    def __init__(self, energies: list[float]) -> None:
        self.energies = energies
        self.calls: list[dict[str, Any]] = []

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        call_index = len(self.calls)
        self.calls.append(
            {
                "coordinates": np.asarray(coordinates, dtype=float),
                "symbols": list(symbols),
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                "kwargs": kwargs,
            }
        )
        return QCResult(
            success=True,
            energy=self.energies[call_index],
            coordinates=np.asarray(coordinates, dtype=float),
            symbols=list(symbols),
        )


class FakeCrestBackend:
    def __init__(self, frames: list[np.ndarray], energies: list[float], symbols: list[str]) -> None:
        self.frames = [np.asarray(frame, dtype=float) for frame in frames]
        self.energies = energies
        self.symbols = symbols
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        if output_dir is None:
            raise AssertionError("output_dir is required in this fake backend")
        self.calls.append(
            {
                "initial_xyz": initial_xyz,
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                "kwargs": kwargs,
            }
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        ensemble_xyz = output_dir / "crest_conformers.xyz"
        write_xyz_multiframe(
            ensemble_xyz,
            np.vstack(self.frames),
            self.symbols,
            titles=[f"CONF{i + 1} Energy: {energy:.6f}" for i, energy in enumerate(self.energies)],
        )
        return ensemble_xyz


def test_guided_scan_strategy_builds_seed_candidates_and_invokes_sp(
    monkeypatch, tmp_path: Path
) -> None:
    base_coordinates = np.array(
        [
            [0.0, 0.0, 0.0],
            [2.4, 0.0, 0.0],
            [1.5, 0.8, 0.0],
            [1.5, 1.2, 1.0],
        ]
    )
    scan_backend = FakeScanBackend(
        energies=[0.0, 0.4, 1.1, 0.2, 0.5, 0.1],
        base_coordinates=base_coordinates,
    )
    sp_backend = FakeSpBackend(energies=[-10.0, -9.8, -9.1, -9.7, -9.6, -9.9])

    def _scan_backend_factory(config: object | None = None) -> FakeScanBackend:
        del config
        return scan_backend

    def _sp_backend_factory(config: object | None = None) -> FakeSpBackend:
        del config
        return sp_backend

    def fake_get_backend(name: str):
        if name == "xtb":
            return _scan_backend_factory
        if name == "orca":
            return _sp_backend_factory
        raise KeyError(name)

    monkeypatch.setattr("acp.backends.registry.get_backend", fake_get_backend)
    strategy = GuidedScanPathStrategy(
        scan_backend="xtb",
        sp_backend="orca",
        config={},
        work_root=tmp_path,
        sp_refinement=True,
    )
    source_state = _state(
        "state_reactant",
        coordinates=base_coordinates.tolist(),
        symbols=["C", "C", "H", "H"],
        route_id="route-guided",
    )
    target_state = _state(
        "state_product",
        coordinates=(base_coordinates + 0.3).tolist(),
        symbols=["C", "C", "H", "H"],
        role="product",
        route_id="route-guided",
    )
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="rc_dist", kind="distance", atoms=(0, 1), start=2.4, end=1.5),
            CoordinateSpec(id="rc_angle", kind="angle", atoms=(0, 1, 2), start=110.0, end=125.0),
            CoordinateSpec(
                id="rc_freeze", kind="distance", atoms=(1, 3), role="freeze", start=1.35
            ),
            CoordinateSpec(id="rc_monitor", kind="dihedral", atoms=(0, 1, 2, 3), role="monitor"),
        ),
        points=6,
    )
    profile = FidelityProfile(name="s3", sp_method="B97-3c", sp_basis="")

    result = strategy.search(source_state, target_state, plan, profile)

    assert len(scan_backend.calls) == 1
    assert len(sp_backend.calls) == len(result.points)
    assert result.strategy == "guided-scan"
    assert result.route_id == "route-guided"
    assert result.complete is True
    assert result.metadata["selection_energy_key"] == "sp_refined"
    assert result.metadata["gate_policies"]["G2"]["require_complete"] is True
    assert result.metadata["sp_refinement"]["successful_frames"] == len(result.points)
    assert {seed.kind for seed in result.seed_candidates} == {"ts_seed", "intermediate_seed"}
    assert all(seed.selection_mode == "local_max_prominence" for seed in result.seed_candidates)
    assert all(seed.stationary_point_claimed is False for seed in result.seed_candidates)
    assert result.selected_ts_id == "ts_candidate_01"
    assert result.selected_int_id == "int_candidate_01"
    assert set(result.points[0].coordinate_values) == {"rc_dist", "rc_angle", "rc_freeze"}
    assert all(call["kwargs"]["method"] == "B97-3c" for call in sp_backend.calls)


def test_compute_rc_alignment_score_distance_angle_and_dihedral() -> None:
    geometry = np.array(
        [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 1.0],
        ]
    )
    distance_plan = ReactionCoordinatePlan(
        coordinates=(CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.5, end=1.5),),
        points=5,
    )
    driving_mode = np.zeros((4, 3))
    driving_mode[0, 0] = 1.0
    driving_mode[1, 0] = -1.0
    aligned = compute_rc_alignment_score(driving_mode, geometry, distance_plan)
    orthogonal_mode = np.zeros((4, 3))
    orthogonal_mode[2, 2] = 1.0
    orthogonal = compute_rc_alignment_score(orthogonal_mode, geometry, distance_plan)

    assert aligned["score"] > 0.99
    assert orthogonal["score"] < 0.01

    angle_plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="angle", kind="angle", atoms=(0, 1, 2), start=90.0, end=120.0),
        ),
        points=5,
    )
    angle_mode = np.zeros((4, 3))
    angle_mode[0, 1] = -1.0
    angle_result = compute_rc_alignment_score(angle_mode, geometry, angle_plan)
    angle_plus = geometry + angle_mode / np.linalg.norm(angle_mode) * 0.1
    angle_minus = geometry - angle_mode / np.linalg.norm(angle_mode) * 0.1
    assert np.isclose(
        angle_result["per_coordinate"]["angle"]["q_plus"],
        GeometryUtils.calculate_angle(angle_plus, 0, 1, 2),
    )
    assert np.isclose(
        angle_result["per_coordinate"]["angle"]["q_minus"],
        GeometryUtils.calculate_angle(angle_minus, 0, 1, 2),
    )

    dihedral_plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="dih", kind="dihedral", atoms=(0, 1, 2, 3), start=-90.0, end=90.0),
        ),
        points=5,
    )
    dihedral_mode = np.zeros((4, 3))
    dihedral_mode[3, 0] = 1.0
    dihedral_result = compute_rc_alignment_score(dihedral_mode, geometry, dihedral_plan)
    dihedral_plus = geometry + dihedral_mode / np.linalg.norm(dihedral_mode) * 0.1
    dihedral_minus = geometry - dihedral_mode / np.linalg.norm(dihedral_mode) * 0.1
    assert np.isclose(
        dihedral_result["per_coordinate"]["dih"]["q_plus"],
        GeometryUtils.calculate_dihedral(dihedral_plus, 0, 1, 2, 3),
    )
    assert np.isclose(
        dihedral_result["per_coordinate"]["dih"]["q_minus"],
        GeometryUtils.calculate_dihedral(dihedral_minus, 0, 1, 2, 3),
    )

    identity = classify_ts_identity([-320.0], rc_alignment=0.2, rc_alignment_threshold=0.5)
    assert identity.valid is False


def test_xtb_fast_ensemble_provider_deduplicates_by_window_and_rmsd(tmp_path: Path) -> None:
    symbols = ["C", "H", "H"]
    frames = [
        np.array([[0.0, 0.0, 0.0], [1.00, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        np.array([[0.001, 0.0, 0.0], [1.001, 0.0, 0.0], [0.0, 1.001, 0.0]]),
        np.array([[0.0, 0.0, 0.0], [1.20, 0.0, 0.0], [0.0, 1.0, 0.2]]),
        np.array([[0.0, 0.0, 0.0], [1.50, 0.0, 0.0], [0.0, 1.2, 0.4]]),
    ]
    crest_backend = FakeCrestBackend(
        frames=frames,
        energies=[-100.0000, -99.9999, -99.9980, -99.9890],
        symbols=symbols,
    )
    provider = XtbFastEnsembleProvider(
        crest_backend=crest_backend,
        config={},
        work_root=tmp_path,
        energy_window_kcal=6.0,
        rmsd_threshold=0.05,
        temperature=298.15,
    )
    stable_state = _state(
        "state_xtb_fast",
        coordinates=frames[0].tolist(),
        symbols=symbols,
        role="intermediate",
    )

    ensemble = provider.generate(stable_state, {"name": "xtb-fast", "temperature": 298.15})

    assert len(crest_backend.calls) == 1
    assert len(ensemble.records) == 2
    assert ensemble.records[0].energy_hartree <= ensemble.records[1].energy_hartree
    assert np.isclose(sum(record.weight or 0.0 for record in ensemble.records), 1.0)
    assert ensemble.metadata["strategy"] == "xtb-fast"
    assert ensemble.metadata["duplicates_dropped"] == 1
    assert ensemble.metadata["window_dropped"] == 1
    assert ensemble.metadata["provenance"]["strategy"] == "xtb-fast"


def test_classify_stable_state_evidence() -> None:
    valid = classify_stable_state(
        StableStateIdentityEvidence(
            stationary_order=0,
            connectivity_signature="intermediate_unique",
            reaction_coordinate_state="intermediate",
            rmsd_to_known_states={"reactant": 0.8, "product": 0.7},
            energy_relationship={"label": "between_endpoints", "below_ts": True},
            charge=0,
            multiplicity=1,
        ),
        thresholds={"expected_charge": 0, "expected_multiplicity": 1},
    )
    collapsed = classify_stable_state(
        StableStateIdentityEvidence(
            stationary_order=0,
            connectivity_signature="matches_product",
            reaction_coordinate_state="collapsed_to_product",
            rmsd_to_known_states={"product_state": 0.08},
            energy_relationship={"label": "product_like", "collapsed_to_product": True},
            charge=0,
            multiplicity=1,
        ),
        thresholds={"expected_charge": 0, "expected_multiplicity": 1},
    )
    ambiguous = classify_stable_state(
        StableStateIdentityEvidence(
            stationary_order=0,
            connectivity_signature="intermediate_unique",
            reaction_coordinate_state="intermediate",
            rmsd_to_known_states={"reactant": 0.8},
            charge=0,
            multiplicity=1,
            missing_evidence=["energy_relationship"],
        ),
        thresholds={"expected_charge": 0, "expected_multiplicity": 1},
    )

    assert valid["label"] == "valid_intermediate"
    assert valid["valid"] is True
    assert collapsed["label"] == "collapsed_to_product"
    assert collapsed["valid"] is False
    assert ambiguous["label"] == "ambiguous"
    assert ambiguous["missing_evidence"] == ["energy_relationship"]
