# pyright: reportMissingImports=false
"""Tests for the contract-first mechanism study layer (M0)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.mechanism.models import (
    ArtifactRef,
    AtomIdentityMap,
    DecisionPoint,
    ElementaryStepEdge,
    ExplorationFrontier,
    MechanismRoute,
    MechanismStudy,
    PathPoint,
    PathResult,
    Provenance,
    QualityGateResult,
    ReactionNetwork,
    SeedCandidate,
    StableState,
    StableStateNode,
    StationaryPoint,
    StationaryPointRequest,
    ThermoCorrection,
)
from acp.mechanism.orchestrator import StudyOrchestrator
from acp.mechanism.providers.fake import (
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
)
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan


def _plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=(CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.5, end=1.5),),
        points=5,
    )


def _artifact(name: str, kind: str) -> ArtifactRef:
    return ArtifactRef(path=f"memory://{name}", sha256=f"sha256:{name}", kind=kind)


def _state(
    state_id: str,
    role: Literal["reactant", "product", "intermediate"],
    *,
    coords: list[list[float]],
    symbols: list[str] | None = None,
) -> StableState:
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=_artifact(f"{state_id}_geom", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        metadata={
            "coordinates": coords,
            "symbols": symbols or ["C", "H", "H"],
        },
    )


def _atom_map() -> AtomIdentityMap:
    return AtomIdentityMap(
        uid_to_structure_index={"a1": 0, "a2": 1, "a3": 2},
        mapping={"C[H][H]": {"a1": 0, "a2": 1, "a3": 2}},
    )


def _study(study_id: str = "study_m0") -> MechanismStudy:
    reactant = _state(
        "state_reactant",
        "reactant",
        coords=[[0.0, 0.0, 0.0], [2.4, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )
    product = _state(
        "state_product",
        "product",
        coords=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0], [0.0, 1.1, 0.0]],
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
        study_id=study_id,
        atom_identity_map=_atom_map(),
        stable_states=[reactant, product],
        routes=[route],
    )


def _orchestrator(
    tmp_path: Path,
    study: MechanismStudy,
    *,
    ambiguous_first: bool = False,
) -> tuple[
    StudyOrchestrator,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
    FakeEndpointProvider,
]:
    ensemble = FakeEnsembleProvider()
    strategy = FakePathSearchStrategy()
    refinement = FakeRefinementProvider()
    endpoint = FakeEndpointProvider(ambiguous_first=ambiguous_first)
    orchestrator = StudyOrchestrator(
        study,
        study_root=tmp_path,
        ensemble_provider=ensemble,
        path_strategy=strategy,
        refinement_provider=refinement,
        endpoint_provider=endpoint,
        max_elementary_steps=3,
    )
    return orchestrator, ensemble, strategy, refinement, endpoint


def test_fake_provider_end_to_end_builds_two_step_network(tmp_path: Path) -> None:
    orchestrator, ensemble, strategy, refinement, endpoint = _orchestrator(tmp_path, _study())

    result = orchestrator.run()

    assert result.status == "completed"
    assert len(result.stable_states) == 3
    assert len(result.network.nodes) == 3
    assert len(result.elementary_steps) >= 2
    assert len(result.network.edges_between("state_reactant", "state_int")) == 1
    assert len(result.network.edges_between("state_int", "state_product")) == 1
    assert result.frontier.empty()
    assert ensemble.calls == 3
    assert strategy.calls == 2
    assert refinement.calls == 2
    assert endpoint.classify_calls == 2

    study_dir = tmp_path / "mechanism_study" / result.study_id
    assert (study_dir / "events.jsonl").exists()
    assert (study_dir / "study.json").exists()
    assert (study_dir / "network.json").exists()
    assert (study_dir / "quality_gates.json").exists()


def test_decision_point_pause_and_resume(tmp_path: Path) -> None:
    orchestrator, _, _, _, _ = _orchestrator(tmp_path, _study("pause_resume"), ambiguous_first=True)

    paused = orchestrator.run()

    assert paused.status == "waiting"
    assert len(paused.decision_points) == 1
    decision = paused.decision_points[0]
    assert decision.status == "waiting"

    resumed = orchestrator.resume({decision.id: {"resolution": "continue"}})

    assert resumed.status == "completed"
    assert resumed.decision_points[0].status == "resolved"
    assert len(resumed.network.nodes) == 3
    assert len(resumed.elementary_steps) >= 2


def test_resume_idempotency_skips_completed_phases(tmp_path: Path) -> None:
    study = _study("idempotent")
    orchestrator, ensemble, strategy, refinement, endpoint = _orchestrator(tmp_path, study)

    first = orchestrator.run()
    assert first.status == "completed"
    counts = (
        ensemble.calls,
        strategy.calls,
        refinement.calls,
        endpoint.irc_calls,
        endpoint.classify_calls,
    )

    rerun = orchestrator.run()

    assert rerun.status == "completed"
    assert counts == (
        ensemble.calls,
        strategy.calls,
        refinement.calls,
        endpoint.irc_calls,
        endpoint.classify_calls,
    )


def test_model_round_trip_for_major_new_models() -> None:
    provenance = Provenance(
        provider="fake",
        provider_version="1.0",
        provider_commit="abc",
        strategy="guided-scan",
        strategy_version="1.1",
        profile_id="s3",
        schema_version="m0",
        input_signature="sha256:test",
    )
    thermo = ThermoCorrection(ensemble_delta_g_hartree=0.001)
    atom_map = _atom_map()
    request = StationaryPointRequest(
        id="req1",
        role="transition_state",
        kind="ts",
        input_geometry=_artifact("ts_seed", "geometry"),
        coordinate_plan=_plan(),
        fallback_geometries=[_artifact("fallback", "geometry")],
        source_stage="S2",
        charge=0,
        multiplicity=1,
        atom_mapping=atom_map,
        parent_state_id="state_reactant",
        route_id="route_main",
        ensemble_correction=thermo,
        provenance=provenance,
    )
    point = PathPoint(
        point_id="p001",
        progress=0.5,
        coordinate_values={"rc1": 2.0},
        reaction_coordinates={"rc1": 2.0},
        energies_hartree={"fake": -1.0},
        arc_length=0.5,
        topology_valid=True,
        diagnostics={"ok": True},
        provenance=provenance,
    )
    path_result = PathResult(
        points=[point],
        candidates=[],
        strategy="guided-scan",
        route_id="route_main",
        seed_candidates=[
            SeedCandidate(
                id="seed_ts",
                kind="ts_seed",
                geometry=_artifact("ts_seed_geom", "geometry"),
                rank=1,
                selection_mode="selector",
                confidence="high",
                evidence={"peak": True},
            )
        ],
        strategy_id="guided-scan",
        strategy_version="1.0",
        complete=True,
        endpoint_evidence={"endpoint": "ok"},
        topology_segments=[{"seg": 1}],
        artifacts={"plot": "fake://plot"},
    )
    state = _state(
        "state_roundtrip",
        "intermediate",
        coords=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )
    state.ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id="conf1",
                    symbols=["C", "H", "H"],
                    coordinates=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                ),
                energy_hartree=-10.0,
            )
        ]
    )
    node = state.to_node()
    edge = ElementaryStepEdge(
        step_id="step1",
        source_state_id="A",
        sink_state_id="B",
        ts_id="TS1",
        path_strategy="guided-scan",
        coordinate_plan=_plan(),
        irc_connectivity={"ok": True},
        barrier_forward=10.0,
        barrier_reverse=8.0,
        fidelity="s3",
        status="confirmed",
    )
    network = ReactionNetwork(nodes={node.state_id: node}, edges=[edge])
    frontier = ExplorationFrontier()
    frontier.push("state_roundtrip", "route_main", depth=1)
    decision = DecisionPoint(
        id="decision_001",
        type="mechanism_frontier_review",
        status="waiting",
        options=["continue"],
        payload={"reason": "ambiguous"},
        created_at="2026-08-12T00:00:00+00:00",
    )
    gate = QualityGateResult(
        gate_id="G0",
        status="pass",
        evidence={"ok": True},
        thresholds={"require": True},
        missing_evidence=[],
    )
    study = MechanismStudy(
        study_id="roundtrip",
        atom_identity_map=atom_map,
        stable_states=[state],
        stationary_points=[
            StationaryPoint(
                point_id="ts1",
                role="transition_state",
                kind="ts",
                geometry=_artifact("ts1", "geometry"),
                charge=0,
                multiplicity=1,
                provenance=provenance,
            )
        ],
        elementary_steps=[edge],
        network=network,
        frontier=frontier,
        decision_points=[decision],
        quality_gates=[gate],
        routes=[MechanismRoute(route_id="route_main", coordinate_plan=_plan())],
    )

    assert PathResult.from_dict(path_result.to_dict()).to_dict() == path_result.to_dict()
    assert StationaryPointRequest.from_dict(request.to_dict()).to_dict() == request.to_dict()
    assert StableState.from_dict(state.to_dict()).to_dict() == state.to_dict()
    assert StableStateNode.from_dict(node.to_dict()).to_dict() == node.to_dict()
    assert ReactionNetwork.from_dict(network.to_dict()).to_dict() == network.to_dict()
    assert ExplorationFrontier.from_dict(frontier.to_dict()).to_dict() == frontier.to_dict()
    assert DecisionPoint.from_dict(decision.to_dict()).to_dict() == decision.to_dict()
    assert QualityGateResult.from_dict(gate.to_dict()).to_dict() == gate.to_dict()
    assert MechanismStudy.from_dict(study.to_dict()).to_dict() == study.to_dict()


def test_reaction_network_allows_parallel_edges() -> None:
    node_a = StableStateNode(
        state_id="A",
        canonical_geometry=_artifact("A", "geom"),
        ensemble=None,
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:A",
    )
    node_b = StableStateNode(
        state_id="B",
        canonical_geometry=_artifact("B", "geom"),
        ensemble=None,
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:B",
    )
    edge1 = ElementaryStepEdge(
        step_id="step1",
        source_state_id="A",
        sink_state_id="B",
        ts_id="TS1",
        path_strategy="guided-scan",
        coordinate_plan=_plan(),
        irc_connectivity={},
        barrier_forward=5.0,
        barrier_reverse=4.0,
        fidelity="s3",
        status="confirmed",
    )
    edge2 = ElementaryStepEdge(
        step_id="step2",
        source_state_id="A",
        sink_state_id="B",
        ts_id="TS2",
        path_strategy="guided-scan",
        coordinate_plan=_plan(),
        irc_connectivity={},
        barrier_forward=6.0,
        barrier_reverse=3.0,
        fidelity="s3",
        status="confirmed",
    )
    network = ReactionNetwork()
    network.add_node(node_a)
    network.add_node(node_b)
    network.add_edge(edge1)
    network.add_edge(edge2)

    edges = network.edges_between("A", "B")

    assert len(edges) == 2
    assert {edge.ts_id for edge in edges} == {"TS1", "TS2"}
