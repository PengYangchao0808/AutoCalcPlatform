# pyright: reportMissingImports=false
"""Tests for the contract-first mechanism study layer (M0)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import numpy as np
import pytest

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
from acp.mechanism.presets import FidelityProfile, apply_levels_overrides
from acp.mechanism.providers.fake import (
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
)
from acp.mechanism.providers.guided_scan import GuidedScanPathStrategy
from acp.mechanism.providers.native_censo_lite import NativeCensoLiteProvider
from acp.mechanism.providers.native_peb import NativeReversePebStrategy
from acp.mechanism.providers.native_refinement import NativeRefinementProvider
from acp.mechanism.providers.xtb_ensemble import XtbFastEnsembleProvider
from acp.mechanism.study_runner import run_mechanism_study
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan


class RecordingRefinementProvider(FakeRefinementProvider):
    def __init__(
        self,
        *,
        s4_energy_shift: float = -0.25,
        raise_on_request_ids: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.s4_energy_shift = s4_energy_shift
        self.raise_on_request_ids = set(raise_on_request_ids or set())
        self.fidelity_calls: list[str] = []
        self.request_batches: list[list[str]] = []

    def refine(self, requests: list[StationaryPointRequest], fidelity: object):
        fidelity_name = getattr(fidelity, "name", str(fidelity))
        self.fidelity_calls.append(fidelity_name)
        self.request_batches.append([request.id for request in requests])
        if fidelity_name == "s4" and self.raise_on_request_ids.intersection(
            request.id for request in requests
        ):
            self.calls += 1
            raise RuntimeError("synthetic s4 refinement failure")
        manifest = super().refine(requests, fidelity)
        if fidelity_name == "s4":
            for attempt in manifest.attempts:
                if (
                    attempt.stationary_point is None
                    or attempt.stationary_point.energy_hartree is None
                ):
                    continue
                attempt.stationary_point.energy_hartree += self.s4_energy_shift
        return manifest


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
    refinement: FakeRefinementProvider | None = None,
) -> tuple[
    StudyOrchestrator,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
    FakeEndpointProvider,
]:
    ensemble = FakeEnsembleProvider()
    strategy = FakePathSearchStrategy()
    refinement = refinement or FakeRefinementProvider()
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
    refinement = RecordingRefinementProvider()
    orchestrator, ensemble, strategy, _, endpoint = _orchestrator(
        tmp_path,
        _study(),
        refinement=refinement,
    )

    result = orchestrator.run()

    assert result.status == "completed"
    assert result.quality == "high"
    assert len(result.stable_states) == 3
    assert len(result.network.nodes) == 3
    assert len(result.elementary_steps) >= 2
    assert len(result.network.edges_between("state_reactant", "state_int")) == 1
    assert len(result.network.edges_between("state_int", "state_product")) == 1
    assert result.frontier.empty()
    assert ensemble.calls == 3
    assert strategy.calls == 2
    assert refinement.calls == 3
    assert refinement.fidelity_calls == ["s3", "s3", "s4"]
    assert endpoint.classify_calls == 2
    ts_points = [point for point in result.stationary_points if point.kind == "ts"]
    assert ts_points
    for point in ts_points:
        energies = point.metadata.get("energies_hartree")
        assert isinstance(energies, dict)
        assert energies["s4"] < energies["s3"]
        assert point.metadata["fidelity"] == "s4"
    g5 = next(gate for gate in result.quality_gates if gate.gate_id == "G5")
    assert g5.status == "pass"

    analysis = tmp_path / "WORK" / "08_ANALYSIS"
    assert (analysis / "events.jsonl").exists()
    assert (analysis / "study.json").exists()
    assert (analysis / "network.json").exists()
    assert (analysis / "quality_gates.json").exists()
    assert not (tmp_path / "mechanism_study").exists()


def test_s4_no_candidates_retains_medium_quality_and_warns_g5(tmp_path: Path) -> None:
    study = _study("no_s4_candidates")
    study.metadata["study_runner"] = {"promotion_policy": "user_selected", "fidelity": "s3"}
    orchestrator, _, _, refinement, _ = _orchestrator(tmp_path, study)

    result = orchestrator.run()

    assert result.status == "completed"
    assert result.quality == "medium"
    assert result.metadata["high_fidelity"] is None
    assert refinement.calls == 2
    g5 = next(gate for gate in result.quality_gates if gate.gate_id == "G5")
    assert g5.status == "warn"


def test_s4_failing_candidate_retains_medium_quality_without_crashing(tmp_path: Path) -> None:
    refinement = RecordingRefinementProvider(raise_on_request_ids={"seed_ts2_s3"})
    orchestrator, _, _, refinement, _ = _orchestrator(
        tmp_path,
        _study("s4_failure"),
        refinement=refinement,
    )

    result = orchestrator.run()

    assert result.status == "completed"
    assert result.quality == "medium"
    high_fidelity = result.metadata["high_fidelity"]
    assert high_fidelity["failed_candidate_ids"] == ["seed_ts2_s3"]
    assert high_fidelity["successful_candidate_ids"] == ["seed_ts1_s3"]
    g5 = next(gate for gate in result.quality_gates if gate.gate_id == "G5")
    assert g5.status == "warn"


def test_s4_resume_idempotency_preserves_quality(tmp_path: Path) -> None:
    refinement = RecordingRefinementProvider()
    orchestrator, _, _, _, _ = _orchestrator(
        tmp_path,
        _study("s4_idempotent"),
        refinement=refinement,
    )

    first = orchestrator.run()
    assert first.quality == "high"
    study_dir = tmp_path / "WORK" / "08_ANALYSIS"
    payload = json.loads((study_dir / "study.json").read_text(encoding="utf-8"))
    payload["status"] = "running"
    (study_dir / "study.json").write_text(json.dumps(payload), encoding="utf-8")

    rerun = orchestrator.run()

    assert rerun.status == "completed"
    assert rerun.quality == "high"
    assert refinement.calls == 3
    assert refinement.fidelity_calls == ["s3", "s3", "s4"]


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


def test_apply_levels_overrides_maps_stage_fields_and_tolerates_unknowns() -> None:
    profile = FidelityProfile(name="s3")

    updated = apply_levels_overrides(
        profile,
        {
            "scan": {"scan_points": 25, "mystery": "ignored"},
            "ts_opt": {
                "functional": "M062X",
                "basis": "def2-SVP",
                "grid": "DefGrid3",
                "scf_convergence": "Tight",
                "ts_initial_hessian": "read",
            },
            "sp": {
                "functional": "wB97M-V",
                "basis": "def2-TZVPP",
                "ri_approximation": "RIJCOSX",
                "aux_j_basis": "def2/J",
            },
        },
    )

    assert profile.sp_method == "r2SCAN-3c"
    assert updated.scan_points == 25
    assert updated.ts_method == "M062X"
    assert updated.ts_basis == "def2-SVP"
    assert updated.ts_grid == "DefGrid3"
    assert updated.ts_scf == "TightSCF"
    assert updated.ts_initial_hessian == "read"
    assert updated.sp_method == "wB97M-V"
    assert updated.sp_basis == "def2-TZVPP"
    assert updated.sp_ri_approximation == "RIJCOSX"
    assert updated.sp_aux_j == "def2/J"


def test_run_mechanism_study_applies_levels_and_updates_mechanism_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mechanism_config = tmp_path / "mechanism_config.json"
    mechanism_config.write_text(
        json.dumps(
            {
                "version": 1,
                "method": {},
                "resolved": {
                    "preset": "rph-s3",
                    "fidelity": "s3",
                    "scan_points": 21,
                    "irc_points": 30,
                    "study_id": "cfg_study",
                    "conformer_mode": "auto",
                    "max_elementary_steps": 3,
                    "promotion_policy": "all_confirmed",
                    "int_extension": False,
                    "auto_converge": False,
                },
            }
        ),
        encoding="utf-8",
    )

    captures: dict[str, object] = {}

    def fake_read_structure(
        source: str,
        *,
        charge: int | None,
        multiplicity: int | None,
        name: str | None,
    ) -> Structure:
        del source
        return Structure(
            id=name or "state",
            charge=charge or 0,
            multiplicity=multiplicity or 1,
            symbols=["H", "H"],
            coordinates=np.asarray([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]], dtype=float),
        )

    monkeypatch.setattr("acp.mechanism.study_runner._read_structure", fake_read_structure)
    monkeypatch.setattr(
        "acp.mechanism.study_runner._build_initial_states",
        lambda **_kwargs: (
            [
                _state("state_reactant", "reactant", coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
                _state("state_product", "product", coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.6]]),
            ],
            _atom_map(),
        ),
    )
    monkeypatch.setattr(
        "acp.mechanism.study_runner._build_routes",
        lambda **_kwargs: [
            MechanismRoute(
                route_id="route_1",
                coordinate_plan=_plan(),
                path_strategy="guided-scan",
                fidelity="s4",
                reactant_id="state_reactant",
                product_id="state_product",
            )
        ],
    )
    monkeypatch.setattr(
        "acp.mechanism.study_runner._build_endpoint_provider",
        lambda *_args, **_kwargs: FakeEndpointProvider(),
    )

    def fake_build_study_providers(
        conformer_mode: str,
        strategy: str,
        fidelity: str,
        config: dict[str, object],
        low_fidelity_profile: FidelityProfile | None = None,
        *,
        layout: object | None = None,
    ) -> dict[str, object]:
        captures["conformer_mode"] = conformer_mode
        captures["strategy"] = strategy
        captures["fidelity"] = fidelity
        captures["profile"] = low_fidelity_profile
        captures["config"] = config
        captures["layout"] = layout
        return {
            "ensemble_provider": FakeEnsembleProvider(),
            "path_strategy": FakePathSearchStrategy(),
            "refinement_provider": FakeRefinementProvider(),
            "provider_backend": "native",
            "resolved_conformer_mode": conformer_mode,
            "ensemble_profile": {},
            "low_fidelity_profile": low_fidelity_profile,
            "high_fidelity_profile": FidelityProfile(name="s4"),
        }

    monkeypatch.setattr(
        "acp.mechanism.study_runner.build_study_providers",
        fake_build_study_providers,
    )

    class FakeOrchestrator:
        def __init__(self, study: MechanismStudy, **kwargs: object) -> None:
            captures["study"] = study
            captures["orchestrator_kwargs"] = kwargs
            self.study = study
            max_steps = kwargs.get("max_elementary_steps")
            assert isinstance(max_steps, int)
            self.max_elementary_steps = max_steps

        def run(self) -> MechanismStudy:
            self.study.status = "completed"
            self.study.study_dir = str(tmp_path / "WORK" / "08_ANALYSIS")
            return self.study

    monkeypatch.setattr("acp.mechanism.study_runner.StudyOrchestrator", FakeOrchestrator)

    summary = run_mechanism_study(
        input_source="reactant.xyz",
        output_dir=tmp_path,
        config={"mechanism": {"provider_backend": "native"}},
        name="rxn",
        charge=0,
        multiplicity=1,
        product_source="product.xyz",
        preset="rph-s3",
        fidelity="s4",
        conformer_mode=None,
        max_elementary_steps=None,
        int_extension=False,
        promotion_policy=None,
        auto_converge=False,
        config_resolved={
            "preset": "rph-s3",
            "fidelity": "s3",
            "scan_points": 21,
            "irc_points": 30,
            "study_id": "cfg_study",
            "conformer_mode": "auto",
            "max_elementary_steps": 3,
            "promotion_policy": "all_confirmed",
            "int_extension": False,
            "auto_converge": False,
        },
        method_levels={
            "scan": {
                "scan_points": 29,
                "conformer_mode": "xtb-fast",
                "max_elementary_steps": 4,
                "int_extension": True,
                "promotion_policy": "rate_relevant",
                "auto_converge": True,
            },
            "ts_opt": {
                "functional": "M062X",
                "basis": "def2-SVP",
                "grid": "DefGrid3",
                "scf_convergence": "Tight",
            },
            "sp": {
                "functional": "wB97M-V",
                "basis": "def2-TZVPP",
                "ri_approximation": "RIJCOSX",
                "aux_j_basis": "def2/J",
            },
            "irc": {"irc_points": 44},
        },
        mechanism_config_path=mechanism_config,
    )

    profile = captures["profile"]
    assert isinstance(profile, FidelityProfile)
    assert summary["study_id"] == "cfg_study"
    assert summary["effective_fidelity"] == "s4"
    assert captures["conformer_mode"] == "xtb-fast"
    assert captures["fidelity"] == "s4"
    assert profile.scan_points == 29
    assert profile.irc_points == 44
    assert profile.ts_method == "M062X"
    assert profile.ts_basis == "def2-SVP"
    assert profile.ts_grid == "DefGrid3"
    assert profile.ts_scf == "TightSCF"
    assert profile.sp_method == "wB97M-V"
    assert profile.sp_basis == "def2-TZVPP"
    assert profile.sp_aux_j == "def2/J"
    assert profile.sp_ri_approximation == "RIJCOSX"
    orchestrator_kwargs = captures["orchestrator_kwargs"]
    assert isinstance(orchestrator_kwargs, dict)
    assert orchestrator_kwargs["max_elementary_steps"] == 4
    assert orchestrator_kwargs["thermochemistry_provider"] is not None

    study = captures["study"]
    assert isinstance(study, MechanismStudy)
    runner_meta = study.metadata["study_runner"]
    assert runner_meta["conformer_mode"] == "xtb-fast"
    assert runner_meta["int_extension"] is True
    assert runner_meta["promotion_policy"] == "rate_relevant"
    assert runner_meta["auto_converge"] is True
    assert runner_meta["fidelity_profile_name"] == "s4"

    updated_config = json.loads(mechanism_config.read_text(encoding="utf-8"))
    assert updated_config["resolved"]["fidelity"] == "s4"
    assert updated_config["resolved"]["scan_points"] == 29
    assert updated_config["resolved"]["irc_points"] == 44
    assert updated_config["resolved"]["conformer_mode"] == "xtb-fast"
    assert updated_config["resolved"]["max_elementary_steps"] == 4
    assert updated_config["resolved"]["int_extension"] is True
    assert updated_config["resolved"]["promotion_policy"] == "rate_relevant"
    assert updated_config["resolved"]["auto_converge"] is True
    assert updated_config["resolved"]["fidelity_profile"]["sp_method"] == "wB97M-V"


def _sr_orchestrator(tmp_path: Path) -> StudyOrchestrator:
    return StudyOrchestrator(
        _study(),
        study_root=tmp_path,
        ensemble_provider=FakeEnsembleProvider(),
        path_strategy=FakePathSearchStrategy(),
        refinement_provider=FakeRefinementProvider(),
        endpoint_provider=FakeEndpointProvider(),
        max_elementary_steps=3,
        require_sr_review=True,
    )


def _sr_resolution(
    decision_id: str,
    revision: dict[str, object],
    cycle_id: int | None = None,
) -> dict[str, dict[str, object]]:
    payload: dict[str, object] = {"resolution": "sr_revision", "revision": revision}
    if cycle_id is not None:
        payload["cycle_id"] = cycle_id
    return {decision_id: payload}


def test_sr_cycle_review_pauses_every_pass_when_required(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)

    result = orchestrator.run()

    assert result.status == "waiting"
    assert len(result.decision_points) == 1
    decision = result.decision_points[0]
    assert decision.type == "sr_cycle_review"
    assert decision.options == ["continue", "reject_path", "accept_network"]
    assert decision.payload["cycle"] == 0
    pending = result.metadata["pending_decisions"][decision.id]
    review = pending["review"]
    assert review["cycle"] == 0
    assert review["source_state_id"] == "state_reactant"
    assert review["candidates"]
    assert review["endpoint_verdict"] in {"NEW_STATE", "MATCH_EXISTING", "AMBIGUOUS"}


def test_sr_revision_continue_restarts_cycle_with_fresh_frontier(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)
    paused = orchestrator.run()
    decision = paused.decision_points[0]

    result = orchestrator.resume(
        _sr_resolution(
            decision.id,
            {
                "decision": "continue",
                "parent_state": "state_reactant",
                "selected_bonds": [{"atoms": [0, 1], "action": "stretch", "target": 3.2}],
                "comment": "stretch C-H next",
            },
            cycle_id=0,
        )
    )

    assert result.cycle_index == 1
    assert len(result.cycles) == 1
    assert result.cycles[0].seeded_from_state == "state_reactant"
    assert len(result.revisions) == 1
    revision = result.revisions[0]
    assert revision.cycle == 1
    assert revision.selected_bonds[0].atoms == (0, 1)
    new_routes = [route for route in result.routes if route.route_id.startswith("cycle1_")]
    assert len(new_routes) == 1
    drive = new_routes[0].coordinate_plan.drive_coordinates()
    assert len(drive) == 1
    assert drive[0].end == 3.2
    assert "0" in result.metadata["cycle_archive"]
    assert result.status == "waiting"
    follow_up = result.decision_points[-1]
    assert follow_up.id != decision.id
    assert follow_up.payload["cycle"] == 1

    study_dir = tmp_path / "WORK" / "08_ANALYSIS"
    assert (study_dir / "cycles" / "cycle_01" / "revision.json").exists()


def test_sr_revision_accept_network_completes_study(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)
    paused = orchestrator.run()
    decision = paused.decision_points[0]

    result = orchestrator.resume(
        _sr_resolution(
            decision.id,
            {"decision": "accept_network", "parent_state": "state_reactant"},
            cycle_id=0,
        )
    )

    assert result.status == "completed"
    assert result.frontier.empty()
    assert result.revisions[0].decision == "accept_network"
    assert result.cycle_index == 0


def test_sr_revision_reject_path_reseeds_when_frontier_empty(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)
    paused = orchestrator.run()
    decision = paused.decision_points[0]

    result = orchestrator.resume(
        _sr_resolution(
            decision.id,
            {"decision": "reject_path", "parent_state": "state_reactant"},
            cycle_id=0,
        )
    )

    assert result.status == "waiting"
    assert result.frontier.empty()
    reseed = result.decision_points[-1]
    assert reseed.id != decision.id
    assert reseed.status == "waiting"
    assert reseed.payload["reseed"] is True
    assert reseed.options == ["continue", "accept_network"]


def test_sr_revision_out_of_range_bond_keeps_decision_waiting(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)
    paused = orchestrator.run()
    decision = paused.decision_points[0]

    result = orchestrator.resume(
        _sr_resolution(
            decision.id,
            {
                "decision": "continue",
                "selected_bonds": [{"atoms": [0, 99], "action": "stretch", "target": 3.0}],
            },
            cycle_id=0,
        )
    )

    assert result.status == "waiting"
    assert result.cycle_index == 0
    assert result.decision_points[0].status == "waiting"
    assert not result.revisions


def test_sr_revision_stale_cycle_id_is_skipped(tmp_path: Path) -> None:
    orchestrator = _sr_orchestrator(tmp_path)
    paused = orchestrator.run()
    decision = paused.decision_points[0]

    result = orchestrator.resume(
        _sr_resolution(
            decision.id,
            {"decision": "accept_network", "parent_state": "state_reactant"},
            cycle_id=7,
        )
    )

    assert result.status == "waiting"
    assert result.decision_points[0].status == "waiting"
    assert not result.revisions


# ---- calc-root threading + resume-safe sequencing (calc/ work_root) ----


def test_next_sequence_scans_trailing_integer_dirs(tmp_path: Path) -> None:
    from acp.mechanism._helpers import next_sequence

    assert next_sequence(tmp_path, "*/ensemble_*") == 0
    assert next_sequence(tmp_path / "missing", "*/ensemble_*") == 0

    state_dir = tmp_path / "state_reactant"
    (state_dir / "ensemble_001").mkdir(parents=True)
    (state_dir / "ensemble_003").mkdir()
    (state_dir / "ensemble_003" / "nested").mkdir()
    (state_dir / "notes").mkdir()
    (state_dir / "ensemble_004").write_text("not a dir", encoding="utf-8")
    (tmp_path / "route-1__scan_007").mkdir()

    assert next_sequence(tmp_path, "*/ensemble_*") == 4
    assert next_sequence(tmp_path, "*__scan_*") == 8


def test_build_study_providers_threads_work_root(tmp_path: Path) -> None:
    from acp.mechanism.layout import resolve_study_layout
    from acp.mechanism.study_runner import build_study_providers

    layout = resolve_study_layout(tmp_path, "study_x")
    providers = build_study_providers("censo-lite", "guided-scan", "s3", {}, layout=layout)

    assert providers["ensemble_provider"].work_root == layout.s1_root
    assert providers["path_strategy"].work_root == layout.s2_root
    assert providers["refinement_provider"].work_root == layout.ts_root

    peb_providers = build_study_providers("xtb-fast", "rph-reverse", "s3", {}, layout=layout)
    assert peb_providers["ensemble_provider"].work_root == layout.s1_xtbfast_root
    assert peb_providers["path_strategy"].work_root == layout.s2_peb_root


def test_providers_fall_back_to_cwd_acp_calc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert NativeCensoLiteProvider(config={}).work_root == tmp_path / "acp_calc"
    assert XtbFastEnsembleProvider(config={}).work_root == tmp_path / "acp_calc"
    assert GuidedScanPathStrategy(config={}).work_root == tmp_path / "acp_calc"
    assert NativeReversePebStrategy(config={}).work_root == tmp_path / "acp_calc"
    assert NativeRefinementProvider(config={}).work_root == tmp_path / "acp_calc"


def test_censo_lite_provider_resumes_ensemble_numbering(tmp_path: Path) -> None:
    (tmp_path / "state_reactant" / "ensemble_002").mkdir(parents=True)
    provider = NativeCensoLiteProvider(config={}, work_root=tmp_path)

    assert provider.calls == 3


def test_guided_scan_provider_resumes_scan_numbering(tmp_path: Path) -> None:
    (tmp_path / "route-1__scan_004").mkdir(parents=True)
    strategy = GuidedScanPathStrategy(config={}, work_root=tmp_path)

    assert strategy.calls == 5


def test_find_study_layout_new_then_legacy(tmp_path: Path) -> None:
    from acp.mechanism.layout import (
        find_reaction_json,
        find_study_layout,
        resolve_study_layout,
    )

    assert find_study_layout(tmp_path) is None

    layout = resolve_study_layout(tmp_path, "study_new")
    layout.analysis_root.mkdir(parents=True)
    (layout.study_json).write_text('{"study_id": "study_new"}', encoding="utf-8")
    found = find_study_layout(tmp_path)
    assert found is not None
    assert found.study_json.parent == layout.analysis_root
    assert not found.legacy
    assert found.s1_root == tmp_path / "WORK" / "02_SEARCH" / "s1"
    assert found.routes_root == tmp_path / "WORK" / "07_PATH" / "routes"
    assert found.ts_root == tmp_path / "WORK" / "03_OPT" / "TS"

    legacy_dir = tmp_path / "mechanism_study" / "study_old"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "study.json").write_text('{"study_id": "study_old"}', encoding="utf-8")
    (legacy_dir / "reaction.json").write_text(
        '{"study_id": "study_old", "reactant": "CCO"}', encoding="utf-8"
    )
    (layout.study_json).unlink()
    legacy = find_study_layout(tmp_path, "study_old")
    assert legacy is not None
    assert legacy.legacy
    assert legacy.analysis_root == legacy_dir
    assert legacy.reaction_json == legacy_dir / "reaction.json"
    assert find_reaction_json(tmp_path, "study_old") == legacy_dir / "reaction.json"
    assert legacy.s1_root == legacy_dir / "calc" / "s1"
    assert legacy.routes_root == legacy_dir / "routes"


def test_find_reaction_json_probes_both_layouts(tmp_path: Path) -> None:
    from acp.mechanism.layout import find_reaction_json

    assert find_reaction_json(tmp_path, "s1") is None

    legacy = tmp_path / "mechanism_study" / "s1"
    legacy.mkdir(parents=True)
    (legacy / "reaction.json").write_text("{}", encoding="utf-8")
    assert find_reaction_json(tmp_path, "s1") == legacy / "reaction.json"

    new = tmp_path / "WORK" / "08_ANALYSIS" / "reaction.json"
    new.parent.mkdir(parents=True)
    new.write_text("{}", encoding="utf-8")
    assert find_reaction_json(tmp_path, "s1") == new


def test_legacy_layout_fallback_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import acp.mechanism.layout as layout_module

    legacy_dir = tmp_path / "mechanism_study" / "study_old"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "study.json").write_text('{"study_id": "study_old"}', encoding="utf-8")
    (legacy_dir / "reaction.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(layout_module, "LEGACY_FALLBACK_ENABLED", False)

    assert layout_module.find_study_layout(tmp_path, "study_old") is None
    assert layout_module.find_reaction_json(tmp_path, "study_old") is None
