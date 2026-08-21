# pyright: reportMissingImports=false
"""Tests for mechanism-study report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.mechanism.models import (
    ArtifactRef,
    AtomIdentityMap,
    ElementaryStepEdge,
    MechanismRoute,
    MechanismStudy,
    StableState,
    StationaryPoint,
    TsIdentity,
)
from acp.mechanism.orchestrator import StudyOrchestrator
from acp.mechanism.providers.fake import (
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
)
from acp.mechanism.reports import select_s4_candidates, write_study_reports
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan


def _artifact(name: str, kind: str) -> ArtifactRef:
    return ArtifactRef(path=f"memory://{name}", sha256=f"sha256:{name}", kind=kind)


def _state(
    state_id: str,
    role: Literal["reactant", "product", "intermediate"],
    energy_hartree: float,
) -> StableState:
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=Structure(
                    id=f"{state_id}_conf1",
                    symbols=["C", "H", "H"],
                    coordinates=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                    metadata={"state_id": state_id},
                ),
                energy_hartree=energy_hartree,
                free_energy_hartree=energy_hartree,
                weight=1.0,
            )
        ]
    )
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=_artifact(f"{state_id}_geom", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        ensemble=ensemble,
        metadata={"canonical_energy_hartree": energy_hartree},
    )


def _study_fixture(study_id: str = "study_reports") -> MechanismStudy:
    reactant = StableState(
        state_id="state_reactant",
        role="reactant",
        canonical_geometry=_artifact("reactant_geom", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:reactant",
        metadata={
            "coordinates": [[0.0, 0.0, 0.0], [2.4, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "symbols": ["C", "H", "H"],
        },
    )
    product = StableState(
        state_id="state_product",
        role="product",
        canonical_geometry=_artifact("product_geom", "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:product",
        metadata={
            "coordinates": [[0.0, 0.0, 0.0], [1.4, 0.0, 0.0], [0.0, 1.1, 0.0]],
            "symbols": ["C", "H", "H"],
        },
    )
    route = MechanismRoute(
        route_id="route_main",
        coordinate_plan=ReactionCoordinatePlan(
            coordinates=(
                CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.5, end=1.5),
            ),
            points=5,
        ),
        path_strategy="guided-scan",
        fidelity="s3",
        reactant_id=reactant.state_id,
        product_id=product.state_id,
        label="A to B",
    )
    return MechanismStudy(
        study_id=study_id,
        atom_identity_map=AtomIdentityMap(
            uid_to_structure_index={"a1": 0, "a2": 1, "a3": 2},
            mapping={"C[H][H]": {"a1": 0, "a2": 1, "a3": 2}},
        ),
        stable_states=[reactant, product],
        routes=[route],
    )


def _run_fake_study(tmp_path: Path) -> Path:
    orchestrator = StudyOrchestrator(
        _study_fixture(),
        study_root=tmp_path,
        ensemble_provider=FakeEnsembleProvider(),
        path_strategy=FakePathSearchStrategy(),
        refinement_provider=FakeRefinementProvider(),
        endpoint_provider=FakeEndpointProvider(),
        max_elementary_steps=3,
    )
    study = orchestrator.run()
    return tmp_path / "mechanism_study" / study.study_id


def test_write_study_reports_emits_all_five_jsons(tmp_path: Path) -> None:
    study_dir = _run_fake_study(tmp_path)

    outputs = write_study_reports(study_dir)

    assert set(outputs) == {
        "reaction_network",
        "mechanism_profile",
        "stationary_points",
        "quality_gates",
        "provenance",
    }
    for path in outputs.values():
        assert path.exists()

    reaction_network = json.loads(outputs["reaction_network"].read_text(encoding="utf-8"))
    assert reaction_network["study_id"] == "study_reports"
    assert reaction_network["quality"] == "high"
    assert reaction_network["effective_fidelity"] == "s4"
    assert len(reaction_network["nodes"]) == 3
    assert reaction_network["nodes"][0]["state_id"].startswith("state_")
    assert len(reaction_network["edges"]) >= 2
    assert reaction_network["edges"][0]["path_strategy"] == "guided-scan"

    mechanism_profile = json.loads(outputs["mechanism_profile"].read_text(encoding="utf-8"))
    assert mechanism_profile["quality"] == "high"
    assert mechanism_profile["effective_fidelity"] == "s4"
    assert mechanism_profile["routes"]
    first_route = mechanism_profile["routes"][0]
    assert first_route["methods"]["fake"]
    assert first_route["refined_stationary_points"]

    stationary_points = json.loads(outputs["stationary_points"].read_text(encoding="utf-8"))
    assert stationary_points["quality"] == "high"
    assert stationary_points["effective_fidelity"] == "s4"
    assert stationary_points["stationary_points"]
    assert any(point["canonical"] for point in stationary_points["stationary_points"])
    assert stationary_points["stationary_points"][0]["geometry_artifact"]["kind"]

    quality_gates = json.loads(outputs["quality_gates"].read_text(encoding="utf-8"))
    assert quality_gates["quality"] == "high"
    assert quality_gates["effective_fidelity"] == "s4"
    assert quality_gates["quality_gates"][0]["gate_id"] == "G0"

    provenance = json.loads(outputs["provenance"].read_text(encoding="utf-8"))
    assert provenance["quality"] == "high"
    assert provenance["effective_fidelity"] == "s4"
    assert provenance["count"] > 0
    assert {
        "provider",
        "provider_version",
        "provider_commit",
        "strategy",
        "strategy_version",
        "profile_id",
        "schema_version",
        "input_signature",
    } <= set(provenance["records"][0])


def test_write_study_reports_tolerates_missing_optional_dirs(tmp_path: Path) -> None:
    study_dir = _run_fake_study(tmp_path)
    for relative in [Path("routes"), Path("refinements")]:
        target = study_dir / relative
        if target.exists():
            for child in sorted(target.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            target.rmdir()

    outputs = write_study_reports(study_dir)
    mechanism_profile = json.loads(outputs["mechanism_profile"].read_text(encoding="utf-8"))
    provenance = json.loads(outputs["provenance"].read_text(encoding="utf-8"))

    assert outputs["reaction_network"].exists()
    assert any("missing" in note for note in mechanism_profile["notes"])
    assert any("missing" in note for note in provenance["notes"])


def test_select_s4_candidates_policies() -> None:
    reactant = _state("A", "reactant", -100.0000)
    product = _state("B", "product", -99.9970)
    intermediate = _state("C", "intermediate", -99.9900)
    ts_low = StationaryPoint(
        point_id="ts_low",
        role="transition_state",
        kind="ts",
        geometry=_artifact("ts_low", "ts_geometry"),
        charge=0,
        multiplicity=1,
        route_id="route_main",
        energy_hartree=-99.9840,
        identity=TsIdentity(imaginary_count=1, valid=True),
        metadata={"confirmed": True},
    )
    ts_competing = StationaryPoint(
        point_id="ts_competing",
        role="transition_state",
        kind="ts",
        geometry=_artifact("ts_competing", "ts_geometry"),
        charge=0,
        multiplicity=1,
        route_id="route_main",
        energy_hartree=-99.9825,
        identity=TsIdentity(imaginary_count=1, valid=True),
        metadata={"confirmed": True},
    )
    ts_high = StationaryPoint(
        point_id="ts_high",
        role="transition_state",
        kind="ts",
        geometry=_artifact("ts_high", "ts_geometry"),
        charge=0,
        multiplicity=1,
        route_id="route_alt",
        energy_hartree=-99.9700,
        identity=TsIdentity(imaginary_count=1, valid=True),
        metadata={"confirmed": True},
    )
    route = MechanismRoute(
        route_id="route_main",
        coordinate_plan=ReactionCoordinatePlan(
            coordinates=(
                CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.5, end=1.5),
            ),
            points=5,
        ),
        path_strategy="guided-scan",
        fidelity="s3",
        reactant_id="A",
        product_id="B",
    )
    study = MechanismStudy(
        study_id="promotion",
        stable_states=[reactant, product, intermediate],
        stationary_points=[ts_low, ts_competing, ts_high],
        elementary_steps=[
            ElementaryStepEdge(
                step_id="step1",
                source_state_id="A",
                sink_state_id="B",
                ts_id="ts_low",
                path_strategy="guided-scan",
                coordinate_plan=route.coordinate_plan,
                irc_connectivity={"ok": True},
                barrier_forward=10.0,
                barrier_reverse=8.0,
                fidelity="s3",
                status="confirmed",
            ),
            ElementaryStepEdge(
                step_id="step2",
                source_state_id="A",
                sink_state_id="C",
                ts_id="ts_competing",
                path_strategy="guided-scan",
                coordinate_plan=route.coordinate_plan,
                irc_connectivity={"ok": True},
                barrier_forward=11.5,
                barrier_reverse=9.0,
                fidelity="s3",
                status="confirmed",
            ),
            ElementaryStepEdge(
                step_id="step3",
                source_state_id="C",
                sink_state_id="B",
                ts_id="ts_high",
                path_strategy="guided-scan",
                coordinate_plan=route.coordinate_plan,
                irc_connectivity={"ok": True},
                barrier_forward=20.0,
                barrier_reverse=18.0,
                fidelity="s3",
                status="confirmed",
            ),
        ],
        routes=[route],
    )

    assert select_s4_candidates(study, "all_confirmed") == [
        "ts_low",
        "ts_competing",
        "ts_high",
    ]
    assert select_s4_candidates(study, "rate_relevant") == ["ts_low", "ts_competing"]
    assert select_s4_candidates(
        study,
        "user_selected",
        user_selection=["ts_competing", "missing", "ts_competing"],
    ) == ["ts_competing"]
