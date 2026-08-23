"""Tests for M3 endpoint matching and real-provider SR wiring."""

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedParameter=false, reportAny=false, reportUnusedCallResult=false

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.mechanism.endpoint import (
    DefaultEndpointProvider,
    EndpointCandidate,
    EndpointMatcher,
    EndpointMatchThresholds,
)
from acp.mechanism.models import ArtifactRef, MechanismRoute, MechanismStudy, StableState
from acp.mechanism.orchestrator import StudyOrchestrator
from acp.mechanism.providers.fake import (
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
)
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca_ts import IrcResult as BackendIrcResult


def _artifact(name: str, kind: str) -> ArtifactRef:
    return ArtifactRef(path=f"memory://{name}", sha256=f"sha256:{name}", kind=kind)


def _plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=(CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.5, end=1.5),),
        points=5,
    )


def _state(
    state_id: str,
    role: Literal["reactant", "product", "intermediate"],
    *,
    coords: list[list[float]],
    symbols: list[str] | None = None,
    energy: float | None = None,
    ensemble_coords: list[list[list[float]]] | None = None,
) -> StableState:
    symbols = symbols or ["C", "C", "O", "H", "H", "H"]
    state = StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=_artifact(f"{state_id}_geom", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        metadata={"coordinates": coords, "symbols": symbols, "energy_hartree": energy},
    )
    conformers = ensemble_coords or [coords]
    state.ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id=f"{state_id}_conf_{index}",
                    charge=0,
                    multiplicity=1,
                    symbols=symbols,
                    coordinates=conformer,
                ),
                energy_hartree=(energy if energy is not None else -100.0) + index * 0.0001,
            )
            for index, conformer in enumerate(conformers, start=1)
        ]
    )
    return state


def _matcher() -> EndpointMatcher:
    return EndpointMatcher(EndpointMatchThresholds())


def test_endpoint_matcher_identical_geometry_matches_existing() -> None:
    reactant = _state(
        "state_A",
        "reactant",
        coords=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [2.70, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
        energy=-150.0,
    )
    candidate = EndpointCandidate(
        coordinates=reactant.metadata["coordinates"],
        symbols=reactant.metadata["symbols"],
        charge=0,
        multiplicity=1,
        energy_hartree=-149.9998,
        metadata={"validated_minimum": True},
    )

    result = _matcher().classify(candidate, [reactant])

    assert result.verdict == "MATCH_EXISTING"
    assert result.state_id == reactant.state_id
    assert result.evidence["selected_state_id"] == reactant.state_id
    assert result.evidence["rmsd_A"] == 0.0


def test_endpoint_matcher_novel_connectivity_is_new_state() -> None:
    known = _state(
        "state_A",
        "reactant",
        coords=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [2.70, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
    )
    candidate = EndpointCandidate(
        coordinates=[
            [0.0, 0.0, 0.0],
            [1.35, 0.0, 0.0],
            [1.35, 1.22, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [2.10, -0.70, 0.0],
        ],
        symbols=known.metadata["symbols"],
        charge=0,
        multiplicity=1,
        energy_hartree=-149.90,
        metadata={"validated_minimum": True, "state_id_hint": "state_INT"},
    )

    result = _matcher().classify(candidate, [known])

    assert result.verdict == "NEW_STATE"
    assert result.state_id == "state_INT"
    assert result.evidence["candidate_state"]["state_id"] == "state_INT"


def test_endpoint_matcher_borderline_rmsd_is_ambiguous() -> None:
    known = _state(
        "state_A",
        "reactant",
        coords=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [2.70, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
    )
    candidate = EndpointCandidate(
        coordinates=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [3.35, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
        symbols=known.metadata["symbols"],
        charge=0,
        multiplicity=1,
        energy_hartree=-150.0,
        metadata={"validated_minimum": True},
    )

    result = _matcher().classify(candidate, [known])

    assert result.verdict == "AMBIGUOUS"
    assert result.evidence["reason"] == "borderline_existing_state_match"


def test_endpoint_matcher_charge_mismatch_does_not_match() -> None:
    known = _state(
        "state_A",
        "reactant",
        coords=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [2.70, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
    )
    candidate = EndpointCandidate(
        coordinates=known.metadata["coordinates"],
        symbols=known.metadata["symbols"],
        charge=1,
        multiplicity=1,
        energy_hartree=-150.0,
        metadata={"validated_minimum": True},
    )

    result = _matcher().classify(candidate, [known])

    assert result.verdict == "NEW_STATE"
    assert result.evidence["comparisons"][0]["charge_match"] is False


def test_endpoint_matcher_records_missing_ensemble_evidence() -> None:
    state = StableState(
        state_id="state_sparse",
        role="intermediate",
        canonical_geometry=_artifact("sparse", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:sparse",
        metadata={},
    )
    candidate = EndpointCandidate(
        coordinates=[[0.0, 0.0, 0.0], [1.2, 0.0, 0.0], [2.3, 0.0, 0.0]],
        symbols=["C", "C", "O"],
        charge=0,
        multiplicity=1,
    )

    result = _matcher().classify(candidate, [state])

    assert "energy" in result.evidence["missing"]
    assert "ensemble" in result.evidence["comparisons"][0]["missing"]


@dataclass
class FakeIrcBackend:
    """Minimal IRC/opt/freq backend for DefaultEndpointProvider tests."""

    step: int = 0

    def irc(self, coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs):
        self.step += 1
        output = Path(output_dir or ".")
        output.mkdir(parents=True, exist_ok=True)
        if self.step == 1:
            reverse = np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.52, 0.0, 0.0],
                    [2.70, 0.0, 0.0],
                    [-0.55, 0.90, 0.0],
                    [-0.55, -0.90, 0.0],
                    [1.52, 1.0, 0.0],
                ],
                dtype=float,
            )
            forward = np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.35, 0.0, 0.0],
                    [1.35, 1.22, 0.0],
                    [-0.55, 0.90, 0.0],
                    [-0.55, -0.90, 0.0],
                    [2.10, -0.70, 0.0],
                ],
                dtype=float,
            )
        else:
            reverse = np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.35, 0.0, 0.0],
                    [1.35, 1.22, 0.0],
                    [-0.55, 0.90, 0.0],
                    [-0.55, -0.90, 0.0],
                    [2.10, -0.70, 0.0],
                ],
                dtype=float,
            )
            forward = np.asarray(
                [
                    [0.0, 0.0, 0.0],
                    [1.25, 0.0, 0.0],
                    [2.50, 0.0, 0.0],
                    [-0.60, 0.85, 0.0],
                    [-0.60, -0.85, 0.0],
                    [1.25, 1.05, 0.0],
                ],
                dtype=float,
            )
        return BackendIrcResult(
            success=True,
            endpoints={
                "forward": output / f"forward_{self.step}.xyz",
                "reverse": output / f"reverse_{self.step}.xyz",
            },
            final_geometries={"forward": forward, "reverse": reverse},
        )

    def optimize(self, coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs):
        return type(
            "OptResult",
            (),
            {
                "success": True,
                "coordinates": np.asarray(coordinates, dtype=float),
                "energy": -200.0 + 0.01 * self.step,
            },
        )()

    def frequency(self, coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs):
        return type(
            "FreqResult",
            (),
            {
                "success": True,
                "coordinates": np.asarray(coordinates, dtype=float),
                "energy": -200.0 + 0.01 * self.step,
                "frequencies": [120.0, 240.0, 350.0],
            },
        )()


def _study() -> MechanismStudy:
    reactant = _state(
        "state_A",
        "reactant",
        coords=[
            [0.0, 0.0, 0.0],
            [1.52, 0.0, 0.0],
            [2.70, 0.0, 0.0],
            [-0.55, 0.90, 0.0],
            [-0.55, -0.90, 0.0],
            [1.52, 1.0, 0.0],
        ],
        energy=-150.0,
    )
    product = _state(
        "state_B",
        "product",
        coords=[
            [0.0, 0.0, 0.0],
            [1.25, 0.0, 0.0],
            [2.50, 0.0, 0.0],
            [-0.60, 0.85, 0.0],
            [-0.60, -0.85, 0.0],
            [1.25, 1.05, 0.0],
        ],
        energy=-149.8,
    )
    route = MechanismRoute(
        route_id="route_main",
        coordinate_plan=_plan(),
        path_strategy="guided-scan",
        fidelity="s3",
        reactant_id=reactant.state_id,
        product_id=product.state_id,
        label="A to B",
    )
    return MechanismStudy(
        study_id="study_endpoint",
        stable_states=[reactant, product],
        routes=[route],
    )


def test_default_endpoint_provider_drives_real_two_step_network(tmp_path: Path) -> None:
    backend = FakeIrcBackend()
    provider = DefaultEndpointProvider(
        backend=backend,
        validate_minimum=True,
        work_root=tmp_path / "provider",
    )
    orchestrator = StudyOrchestrator(
        _study(),
        study_root=tmp_path,
        ensemble_provider=FakeEnsembleProvider(),
        path_strategy=FakePathSearchStrategy(),
        refinement_provider=FakeRefinementProvider(),
        endpoint_provider=provider,
        max_elementary_steps=3,
    )

    result = orchestrator.run()

    assert result.status == "completed"
    assert {state.state_id for state in result.stable_states} >= {"state_A", "state_B"}
    intermediates = [state for state in result.stable_states if state.role == "intermediate"]
    assert len(intermediates) >= 1
    assert len(result.network.edges_between("state_A", intermediates[0].state_id)) == 1
    assert any(edge.sink_state_id == "state_B" for edge in result.elementary_steps)
    minimum_points = [point for point in result.stationary_points if point.kind == "minimum"]
    assert len(minimum_points) >= 1
    assert minimum_points[0].metadata["validated"] is True
    study_json = json.loads(
        (tmp_path / "WORK" / "08_ANALYSIS" / "study.json").read_text()
    )
    assert study_json["status"] == "completed"
