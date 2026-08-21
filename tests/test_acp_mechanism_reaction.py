"""Tests for Phase-A mechanism reaction-definition infrastructure."""

from __future__ import annotations

import json

import numpy as np
import pytest

from acp.mechanism.atom_mapping import (
    AtomMapCandidate,
    _build_mol_graph,
    _minimal_change_tie_break,
    map_reactant_to_product,
)
from acp.mechanism.bond_changes import (
    bond_changes_from_dicts,
    bond_changes_to_dicts,
    compute_bond_changes,
    manual_bond_changes_to_records,
    suggest_mechanism_plan,
)
from acp.mechanism.models import MechanismRevision, MechanismStudy, SelectedBond, StudyCycle
from acp.mechanism.reaction_definition import (
    MECHANISM_SCHEMA_VERSION,
    MappingConfirmationRequired,
    ReactionDefinition,
    RoleSpec,
    build_reaction_definition,
    read_reaction_json,
    validate_reaction_json,
    write_reaction_json,
)
from cccp.qc.interfaces.constraints import ReactionCoordinatePlan


def _plan_payload(plan: ReactionCoordinatePlan) -> dict[str, object]:
    return {
        "coordinates": [
            {
                "id": spec.id,
                "kind": spec.kind,
                "atoms": list(spec.atoms),
                "role": spec.role,
                "start": spec.start,
                "end": spec.end,
                "force_constant": spec.force_constant,
            }
            for spec in plan.coordinates
        ],
        "points": plan.points,
        "coupling": plan.coupling,
        "start_from": plan.start_from,
    }


def test_mapping_and_bond_order_change_for_ethene_to_ethane() -> None:
    reactant_symbols = ["C", "C"]
    product_symbols = ["C", "C"]
    reactant_coords = [[0.0, 0.0, 0.0], [1.33, 0.0, 0.0]]
    product_coords = [[0.0, 0.0, 0.0], [1.52, 0.0, 0.0]]

    mapping = map_reactant_to_product(
        reactant_symbols,
        reactant_coords,
        product_symbols,
        product_coords,
        reactant_smiles="C=C",
        product_smiles="CC",
    )

    assert mapping.status == "unique"
    assert mapping.candidates[0].mapping == [(0, 0), (1, 1)]
    assert mapping.candidates[0].symmetric_alternatives > 0

    bond_changes = compute_bond_changes(
        reactant_symbols,
        reactant_coords,
        product_symbols,
        product_coords,
        mapping.candidates[0],
        reactant_smiles="C=C",
        product_smiles="CC",
    )

    assert len(bond_changes) == 1
    change = bond_changes[0]
    assert change.reactant_atoms == (0, 1)
    assert change.product_atoms == (0, 1)
    assert change.change_type == "order_down"
    assert change.bond_order_before > change.bond_order_after


def test_symmetric_atom_mapping_exposes_alternatives() -> None:
    symbols = ["C", "C", "O", "C"]
    coords = [
        [-1.30, 0.00, 0.00],
        [0.00, 0.00, 0.00],
        [0.00, 1.22, 0.00],
        [1.30, 0.00, 0.00],
    ]

    mapping = map_reactant_to_product(
        symbols,
        coords,
        symbols,
        coords,
        reactant_smiles="CC(=O)C",
        product_smiles="CC(=O)C",
    )

    assert mapping.status in {"unique", "candidates"}
    if mapping.status == "unique":
        assert mapping.candidates[0].symmetric_alternatives > 0
    else:
        assert len(mapping.candidates) >= 2


def test_count_mismatch_requires_confirmation_then_succeeds(tmp_path) -> None:
    reactant_role = RoleSpec(smiles="CCl", charge=0, multiplicity=1)
    product_role = RoleSpec(smiles="C", charge=0, multiplicity=1)
    reactant_symbols = ["C", "Cl"]
    product_symbols = ["C"]
    reactant_coords = [[0.0, 0.0, 0.0], [1.76, 0.0, 0.0]]
    product_coords = [[0.0, 0.0, 0.0]]

    mapping = map_reactant_to_product(
        reactant_symbols,
        reactant_coords,
        product_symbols,
        product_coords,
        reactant_smiles=reactant_role.smiles,
        product_smiles=product_role.smiles,
    )

    assert mapping.status == "count_mismatch"
    assert mapping.unmatched_reactant_atoms == [1]
    assert mapping.unmatched_product_atoms == []

    with pytest.raises(MappingConfirmationRequired):
        build_reaction_definition(
            "study_count_mismatch",
            reactant_role,
            product_role,
            None,
            reactant_symbols,
            reactant_coords,
            product_symbols,
            product_coords,
        )

    definition = build_reaction_definition(
        "study_count_mismatch",
        reactant_role,
        product_role,
        None,
        reactant_symbols,
        reactant_coords,
        product_symbols,
        product_coords,
        selected_candidate=0,
    )
    assert definition.schema_version == MECHANISM_SCHEMA_VERSION
    assert list(definition.atom_mapping)[0].reactant_index == 0
    path = write_reaction_json(tmp_path, definition)
    assert path.exists()


def test_break_and_form_changes_record_distances_and_plan_round_trips() -> None:
    reactant_symbols = ["C", "O", "N"]
    product_symbols = ["C", "O", "N"]
    reactant_coords = [[0.0, 0.0, 0.0], [1.20, 0.0, 0.0], [3.00, 0.0, 0.0]]
    product_coords = [[0.0, 0.0, 0.0], [3.10, 0.0, 0.0], [1.30, 0.0, 0.0]]

    bond_changes = compute_bond_changes(
        reactant_symbols,
        reactant_coords,
        product_symbols,
        product_coords,
        [(0, 0), (1, 1), (2, 2)],
    )

    by_type = {change.change_type: change for change in bond_changes}
    assert by_type["break"].reactant_atoms == (0, 1)
    assert by_type["form"].reactant_atoms == (0, 2)
    assert by_type["break"].distance_after > by_type["break"].distance_before
    assert by_type["form"].distance_after < by_type["form"].distance_before

    plan = suggest_mechanism_plan(bond_changes, points=17)
    assert plan.points == 17
    assert plan.start_from == "reactant"
    drive_lookup = {spec.atoms: spec for spec in plan.drive_coordinates()}
    break_spec = drive_lookup[(0, 1)]
    form_spec = drive_lookup[(0, 2)]
    assert break_spec.start is not None and break_spec.end is not None
    assert form_spec.start is not None and form_spec.end is not None
    assert break_spec.end > break_spec.start
    assert form_spec.end < form_spec.start
    round_trip = ReactionCoordinatePlan.from_dict(_plan_payload(plan))
    assert round_trip.points == 17
    assert len(round_trip.drive_coordinates()) >= 1


def _manual_record_inputs() -> tuple[list[list[float]], list[list[float]]]:
    reactant_coords = [[0.0, 0.0, 0.0], [1.50, 0.0, 0.0], [3.00, 0.0, 0.0]]
    product_coords = [[0.0, 0.0, 0.0], [3.10, 0.0, 0.0], [1.40, 0.0, 0.0]]
    return reactant_coords, product_coords


def test_manual_product_side_entry_builds_product_defining_record() -> None:
    reactant_coords, product_coords = _manual_record_inputs()

    records = manual_bond_changes_to_records(
        [{"product_atoms": [0, 2], "change_type": "form"}],
        n_reactant_atoms=3,
        reactant_coords=reactant_coords,
        product_coords=product_coords,
        mapping=[(0, 0), (1, 1), (2, 2)],
        n_product_atoms=3,
    )

    record = records[0]
    assert record.reactant_atoms is None
    assert record.product_atoms == (0, 2)
    assert record.distance_before == pytest.approx(1.40)
    assert record.distance_after is None
    assert record.bond_order_before == 0.0
    assert record.bond_order_after == 1.0
    assert record.confidence == 1.0

    plan = suggest_mechanism_plan(records)
    assert plan.start_from == "product"
    drive_spec = plan.drive_coordinates()[0]
    assert drive_spec.atoms == (0, 2)
    assert drive_spec.start == pytest.approx(1.40)
    assert drive_spec.end == pytest.approx(3.40)


def test_manual_reactant_side_entry_resolves_product_through_mapping() -> None:
    reactant_coords, product_coords = _manual_record_inputs()

    records = manual_bond_changes_to_records(
        [{"reactant_atoms": [0, 1], "change_type": "break"}],
        n_reactant_atoms=3,
        reactant_coords=reactant_coords,
        product_coords=product_coords,
        mapping=[(0, 0), (1, 1), (2, 2)],
        n_product_atoms=3,
    )

    record = records[0]
    assert record.reactant_atoms == (0, 1)
    assert record.product_atoms == (0, 1)
    assert record.distance_before == pytest.approx(1.50)
    assert record.distance_after == pytest.approx(3.10)
    assert suggest_mechanism_plan(records).start_from == "reactant"


def test_manual_mixed_entries_anchor_plan_at_product() -> None:
    reactant_coords, product_coords = _manual_record_inputs()
    entries = [
        {"reactant_atoms": [0, 1], "change_type": "break"},
        {"product_atoms": [0, 2], "change_type": "form"},
    ]

    records = manual_bond_changes_to_records(
        entries,
        n_reactant_atoms=3,
        reactant_coords=reactant_coords,
        product_coords=product_coords,
        mapping=[],
        n_product_atoms=3,
    )

    plan = suggest_mechanism_plan(records)
    assert plan.start_from == "product"
    drive_atoms = {spec.atoms for spec in plan.drive_coordinates()}
    assert drive_atoms == {(0, 1), (0, 2)}

    serialized = bond_changes_to_dicts(records)
    assert serialized[0]["distance_after"] is None
    assert serialized[1]["reactant_atoms"] is None
    deserialized = bond_changes_from_dicts(serialized)
    assert deserialized[0].reactant_atoms == (0, 1)
    assert deserialized[0].distance_after is None
    assert deserialized[1].reactant_atoms is None
    assert deserialized[1].product_atoms == (0, 2)


def test_manual_entry_validation_rejects_missing_sides_and_bad_ranges() -> None:
    reactant_coords, product_coords = _manual_record_inputs()

    with pytest.raises(ValueError, match="at least one of"):
        manual_bond_changes_to_records(
            [{"change_type": "form"}],
            n_reactant_atoms=3,
            reactant_coords=reactant_coords,
            product_coords=product_coords,
            mapping=[],
            n_product_atoms=3,
        )

    with pytest.raises(ValueError, match="product_atoms.*out of range"):
        manual_bond_changes_to_records(
            [{"product_atoms": [0, 9], "change_type": "form"}],
            n_reactant_atoms=3,
            reactant_coords=reactant_coords,
            product_coords=product_coords,
            mapping=[],
            n_product_atoms=3,
        )

    with pytest.raises(ValueError, match="product_atoms must contain exactly two"):
        manual_bond_changes_to_records(
            [{"product_atoms": [0, 1, 2], "change_type": "form"}],
            n_reactant_atoms=3,
            reactant_coords=reactant_coords,
            product_coords=product_coords,
            mapping=[],
            n_product_atoms=3,
        )


def test_reaction_json_round_trip_and_hash_tamper_detection(tmp_path) -> None:
    definition = build_reaction_definition(
        "study_locked",
        RoleSpec(smiles="C=C", charge=0, multiplicity=1),
        RoleSpec(smiles="CC", charge=0, multiplicity=1),
        None,
        ["C", "C"],
        [[0.0, 0.0, 0.0], [1.33, 0.0, 0.0]],
        ["C", "C"],
        [[0.0, 0.0, 0.0], [1.52, 0.0, 0.0]],
    )

    path = write_reaction_json(tmp_path, definition)
    loaded = read_reaction_json(tmp_path)

    assert loaded is not None
    assert isinstance(loaded, ReactionDefinition)
    assert loaded.content_hash == definition.content_hash
    assert loaded.atom_mapping[0].reactant_index == 0
    assert loaded.bond_changes[0].reactant_atoms == (0, 1)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["confirmed_by"] = "attacker"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_reaction_json(path)


def test_mechanism_study_cycle_and_revision_round_trip_is_backward_compatible() -> None:
    study = MechanismStudy(
        study_id="study_cycles",
        cycle_index=2,
        cycles=[
            StudyCycle(
                cycle_index=1,
                revision_id="rev_01",
                seeded_from_state="state_reactant",
                route_ids=["route_1"],
                status="completed",
            )
        ],
        revisions=[
            MechanismRevision(
                revision_id="rev_01",
                study_id="study_cycles",
                cycle=1,
                parent_state="state_reactant",
                selected_bonds=[
                    SelectedBond(atoms=(0, 1), action="stretch", start=2.4, target=1.5)
                ],
                decision="continue",
                comment="seed next cycle",
                config_hash="sha256:cfg",
                created_at="2026-08-16T00:00:00+00:00",
            )
        ],
    )

    round_trip = MechanismStudy.from_dict(study.to_dict())
    assert round_trip.cycle_index == 2
    assert round_trip.cycles[0].revision_id == "rev_01"
    assert round_trip.revisions[0].selected_bonds[0].atoms == (0, 1)

    legacy = MechanismStudy.from_dict({"study_id": "legacy_study"})
    assert legacy.study_id == "legacy_study"
    assert legacy.cycle_index == 0
    assert legacy.cycles == []
    assert legacy.revisions == []


def test_tie_break_prefers_mapping_with_fewest_break_form_changes() -> None:
    reactant_symbols = ["C", "C", "C"]
    reactant_coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]])
    product_symbols = ["C", "C", "C"]
    product_coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 0.0, 3.0]])
    reactant_graph = _build_mol_graph(reactant_symbols, reactant_coords, charge=0, smiles=None)
    product_graph = _build_mol_graph(product_symbols, product_coords, charge=0, smiles=None)

    identity = AtomMapCandidate(mapping=[(0, 0), (1, 1), (2, 2)], confidence=0.63, method="t")
    permutation = AtomMapCandidate(mapping=[(0, 0), (1, 2), (2, 1)], confidence=0.62, method="t")
    outside_tie = AtomMapCandidate(mapping=[(0, 2), (1, 1), (2, 0)], confidence=0.30, method="t")

    identity_changes = compute_bond_changes(
        reactant_symbols, reactant_coords, product_symbols, product_coords, identity
    )
    permutation_changes = compute_bond_changes(
        reactant_symbols, reactant_coords, product_symbols, product_coords, permutation
    )
    assert sum(c.change_type in {"break", "form"} for c in identity_changes) == 1
    assert sum(c.change_type in {"break", "form"} for c in permutation_changes) == 3

    reordered, resolved = _minimal_change_tie_break(
        [identity, permutation, outside_tie],
        reactant_coords,
        reactant_graph,
        product_coords,
        product_graph,
    )
    assert resolved is True
    assert reordered[0] is identity
    assert reordered[1] is permutation
    assert reordered[2] is outside_tie
    assert "minimal_change_tie_break:break_form=1" in identity.notes


def test_tie_break_keeps_candidates_when_minimum_is_not_unique() -> None:
    reactant_symbols = ["C", "C", "C"]
    reactant_coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]])
    product_symbols = ["C", "C", "C"]
    product_coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 0.0, 3.0]])
    reactant_graph = _build_mol_graph(reactant_symbols, reactant_coords, charge=0, smiles=None)
    product_graph = _build_mol_graph(product_symbols, product_coords, charge=0, smiles=None)

    first = AtomMapCandidate(mapping=[(0, 0), (1, 1), (2, 2)], confidence=0.63, method="t")
    second = AtomMapCandidate(mapping=[(0, 2), (1, 1), (2, 0)], confidence=0.63, method="t")

    reordered, resolved = _minimal_change_tie_break(
        [first, second],
        reactant_coords,
        reactant_graph,
        product_coords,
        product_graph,
    )
    assert resolved is False
    assert reordered == [first, second]


def test_xyz_order_change_is_not_classified_as_break_and_form() -> None:
    ethene_symbols = ["C", "C", "H", "H", "H", "H"]
    ethene_coords = [
        [-0.6675, 0.0, 0.0],
        [0.6675, 0.0, 0.0],
        [-1.234, 0.93, 0.0],
        [-1.234, -0.93, 0.0],
        [1.234, 0.93, 0.0],
        [1.234, -0.93, 0.0],
    ]
    ethane_symbols = ["C", "C", "H", "H", "H", "H", "H", "H"]
    ethane_coords = [
        [-0.76, 0.0, 0.0],
        [0.76, 0.0, 0.0],
        [-1.1233, 1.0277, 0.0],
        [-1.1233, -0.5139, 0.8900],
        [-1.1233, -0.5139, -0.8900],
        [1.1233, 0.5139, -0.8900],
        [1.1233, 0.5139, 0.8900],
        [1.1233, -1.0277, 0.0],
    ]
    mapping = [(0, 0), (1, 1), (2, 2), (3, 3), (4, 5), (5, 6)]

    bond_changes = compute_bond_changes(
        ethene_symbols,
        ethene_coords,
        ethane_symbols,
        ethane_coords,
        mapping,
    )

    assert len(bond_changes) == 1
    change = bond_changes[0]
    assert change.reactant_atoms == (0, 1)
    assert change.change_type == "order_down"
    assert change.bond_order_before == 2.0
    assert change.bond_order_after == 1.0


def test_smiles_atommap_numbers_yield_authoritative_unique_mapping() -> None:
    symbols = ["C", "C"]
    coords = [[0.0, 0.0, 0.0], [1.33, 0.0, 0.0]]

    mapping = map_reactant_to_product(
        symbols,
        coords,
        symbols,
        coords,
        reactant_smiles="[C:1][C:2]",
        product_smiles="[C:2]=[C:1]",
    )

    assert mapping.status == "unique"
    assert mapping.mapping_source == "smiles_atommap"
    assert len(mapping.candidates) == 1
    candidate = mapping.candidates[0]
    assert candidate.confidence == 1.0
    assert candidate.mapping_source == "smiles_atommap"
    assert candidate.mapping == [(0, 1), (1, 0)]
    assert candidate.method == "smiles_atommap"

    fallback = map_reactant_to_product(
        symbols,
        coords,
        symbols,
        coords,
        reactant_smiles="[C:1][C:2]",
        product_smiles="C=C",
    )
    assert fallback.mapping_source != "smiles_atommap"
    assert fallback.candidates[0].mapping_source != "smiles_atommap"
