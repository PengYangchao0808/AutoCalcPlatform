"""A-25a selectivity fixture: two stereochemically distinguishable endpoints.

The fixture is **constructed**.  The structures are RDKit ETKDG/MMFF geometries;
no QC job, TS search, IRC, energy or experimental measurement ran.  The tests
validate the frozen fixture with the authoritative ACP2 reaction/question
contracts and with the RDKit stereochemistry assignment used by the build
record.  They do not launch a QC executable and do not fabricate a result.

The endpoint test asserts a *stereochemical* difference (CIP configuration and
signed-volume sign at the declared stereocentre) while the atom-id order and
element sequence are identical.  An atom-order permutation must not change the
signature, so the difference cannot be explained by array position.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from rdkit import Chem

from cccp.analysis.research.question import Question
from cccp.analysis.research.reaction import Reaction

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO / "tests" / "fixtures" / "acp2" / "science" / "selectivity" / "v1"
MANIFEST_PATH = FIXTURE_DIR / "fixture-manifest.json"
QUESTIONS_PATH = FIXTURE_DIR / "questions.json"
PRODUCER_REQUEST_PATH = (
    REPO
    / "docs"
    / "reference"
    / "next-change"
    / "acp2-contracts"
    / "science"
    / "requests"
    / "selectivity-producer.md"
)
A14_DIR = REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder" / "v1"

TASK_MAPPING_TOPOLOGY_DIGEST = "f131899dba9c7b02f8d841546362eca1a2b3ef9d5e160a71f3acc7744fc3ed16"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
        return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict[str, Any]:
    return _load_json(MANIFEST_PATH)


def _reaction(label: str) -> Reaction:
    return Reaction.from_dict(_load_json(FIXTURE_DIR / f"reaction-{label}.json"))


def _product_mol(label: str) -> Chem.Mol:
    mol = Chem.MolFromMolFile(str(FIXTURE_DIR / f"product-{label}.mol"), removeHs=False)
    assert mol is not None, f"product-{label}.mol did not parse"
    Chem.AssignStereochemistryFrom3D(mol)
    return mol


def _plane_normal(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    normal = np.cross(p1 - p0, p2 - p0)
    return normal / np.linalg.norm(normal)


def _signed_distance(point: np.ndarray, origin: np.ndarray, normal: np.ndarray) -> float:
    return float(np.dot(point - origin, normal))


def _signature(mol: Chem.Mol, atom_ids: list[str]) -> dict[str, Any]:
    index = {atom_id: position for position, atom_id in enumerate(atom_ids)}
    coords = np.asarray(mol.GetConformer().GetPositions(), dtype=float)
    p0 = coords[index["pd_c4"]]
    p1 = coords[index["pd_c5"]]
    p2 = coords[index["pd_c6"]]
    normal = _plane_normal(p0, p1, p2)
    c7_distance = _signed_distance(coords[index["pd_c7"]], p0, normal)
    ester_distance = _signed_distance(coords[index["pd_c8"]], p0, normal)
    face = "endo" if c7_distance * ester_distance > 0 else "exo"
    volume = float(
        np.dot(
            coords[index["pd_c4"]] - coords[index["pd_c5"]],
            np.cross(
                coords[index["pd_c6"]] - coords[index["pd_c5"]],
                coords[index["pd_c8"]] - coords[index["pd_c5"]],
            ),
        )
    )
    work = Chem.Mol(mol)
    Chem.AssignStereochemistryFrom3D(work)
    centers = Chem.FindMolChiralCenters(work, includeUnassigned=True, useLegacyImplementation=False)
    cip_by_id = {atom_ids[atom_index]: configuration for atom_index, configuration in centers}
    return {
        "stereocenter_atom_id": "pd_c5",
        "cip_configuration": cip_by_id["pd_c5"],
        "all_cip_centers": dict(sorted(cip_by_id.items())),
        "signed_volume_atoms": ["pd_c4", "pd_c6", "pd_c8"],
        "signed_volume": volume,
        "signed_volume_sign": 1 if volume > 0 else -1,
        "reference_plane_atoms": ["pd_c4", "pd_c5", "pd_c6"],
        "c7_signed_distance_angstrom": c7_distance,
        "ester_carbon_signed_distance_angstrom": ester_distance,
        "face": face,
    }


def _heavy_coordinates(mol: Chem.Mol) -> np.ndarray:
    coords = np.asarray(mol.GetConformer().GetPositions(), dtype=float)
    heavy = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() > 1]
    return coords[heavy]


def _kabsch_rmsd(left: np.ndarray, right: np.ndarray) -> float:
    left_centered = left - left.mean(axis=0)
    right_centered = right - right.mean(axis=0)
    covariance = left_centered.T @ right_centered
    u, _, vt = np.linalg.svd(covariance)
    determinant = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1.0, 1.0, determinant]) @ u.T
    aligned = (rotation @ left_centered.T).T
    return float(np.sqrt(((aligned - right_centered) ** 2).sum() / len(left)))


def _assert_signature_matches(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Compare a recomputed signature with the frozen manifest signature.

    The mol block stores coordinates at 4 decimal places, so the distances and
    volume are compared numerically; every categorical identity (CIP, sign,
    face and plane atoms) must match exactly.
    """
    for key in (
        "stereocenter_atom_id",
        "cip_configuration",
        "all_cip_centers",
        "signed_volume_atoms",
        "signed_volume_sign",
        "reference_plane_atoms",
        "face",
    ):
        assert actual[key] == expected[key], key
    for key in (
        "signed_volume",
        "c7_signed_distance_angstrom",
        "ester_carbon_signed_distance_angstrom",
    ):
        assert math.isclose(actual[key], expected[key], rel_tol=0.0, abs_tol=1e-3), key


def _normalized_mapping(reaction: Reaction) -> dict[str, str]:
    return {
        left: right.split(":", 1)[1] for left, right in reaction.atom_mapping.left_to_right.items()
    }


def _normalized_bond_changes(reaction: Reaction) -> list[dict[str, Any]]:
    return [
        {
            "change_type": change.change_type,
            "left_atoms": list(change.left_atoms or ()),
            "right_atoms": [atom.split(":", 1)[1] for atom in (change.right_atoms or ())],
            "bond_order_left": change.bond_order_left,
            "bond_order_right": change.bond_order_right,
        }
        for change in reaction.bond_changes
    ]


def test_selectivity_fixture_digests_match_manifest() -> None:
    """Every frozen fixture file matches its manifest digest."""
    manifest = _manifest()
    assert manifest["schema"] == "acp2.science.fixture-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["constructed"] is True
    assert manifest["real_run"] is False
    assert manifest["frozen_before_real_run"] is True
    assert set(manifest["file_digests"]) == set(manifest["fixture_files"].values())
    for name, expected in manifest["file_digests"].items():
        actual = _sha256_file(FIXTURE_DIR / name)
        assert actual == expected, f"{name} digest drifted: {actual} != {expected}"
    reference = manifest["reference"]
    assert reference["run_id"] is None
    assert reference["run_id_gap"].strip()


def test_selectivity_manifest_records_frozen_definitions_budget_tolerance_and_policy() -> None:
    """The manifest freezes definitions, coverage, budget, tolerances and the policy."""
    manifest = _manifest()
    definition = manifest["selectivity_definition"]
    assert definition["stereocenter_atom_id"] == "pd_c5"
    assert definition["reference_plane_atoms"] == ["pd_c4", "pd_c5", "pd_c6"]
    assert definition["face_reference_atom_id"] == "pd_c7"
    assert definition["substituent_reference_atom_id"] == "pd_c8"
    assert "same side" in definition["endo_definition"]
    assert "opposite sides" in definition["exo_definition"]
    assert "atom-order permutation" in definition["stereochemical_signature"]
    assert "not only in atom order" in definition["distinguishability_requirement"]

    coverage = manifest["required_coverage"]
    assert coverage["both_paths_required"] is True
    assert coverage["declared_paths"] == ["path.endo", "path.exo"]
    assert coverage["ts_coverage"].startswith("one TS candidate per declared path")
    assert "s-cis" in coverage["methyl_acrylate_conformer"]
    assert "one frozen conformation per endpoint" in coverage["conformation_coverage"]
    assert "only the declared 2-substituted regioisomer" in coverage["regioisomer_coverage"]

    policy = manifest["missing_path_policy"]
    assert policy["both_paths_required"] is True
    assert policy["missing_path_result"] == "inconclusive"
    assert policy["absent_artifact_result"] == "NOT_VERIFIED"
    assert policy["budget_exhausted_result"] == "inconclusive"
    assert policy["no_experimental_claim"] is True
    assert "never reset the cumulative budget" in (policy["budget_exhausted_rule"])

    budget = manifest["budget"]
    assert budget["constructed"] is True
    assert budget["budget_timing_basis"] == "unmeasured"
    assert budget["measured_step_timings"] is None
    assert budget["max_task_refs"] == 1
    assert budget["max_candidate_paths"] == 2
    assert budget["max_ts_candidates_per_path"] == 1
    assert budget["max_conformers_per_endpoint"] == 1
    assert budget["max_regioisomers"] == 1
    assert budget["max_qc_invocations"] == 14
    assert budget["science_deadline_seconds"] == 3000
    assert budget["export_budget_seconds"] == 600
    assert (
        budget["aggregate_budget_seconds"]
        == budget["science_deadline_seconds"] + budget["export_budget_seconds"]
        == 3600
    )

    tolerances = manifest["tolerances"]
    assert tolerances["constructed"] is True
    assert tolerances["atom_count_tolerance"] == 0
    assert tolerances["coordinate_abs_tolerance_angstrom"] == 0.001
    assert tolerances["activation_energy_comparison_abs_kcal_per_mol"] == 0.5
    assert tolerances["selectivity_preference_abs_kcal_per_mol"] == 0.5
    assert tolerances["stereo_configuration_exact"] is True
    assert tolerances["stereo_signed_volume_sign_exact"] is True


def test_selectivity_reactions_validate_and_maps_are_stable() -> None:
    """Both reactions validate; the maps are exact inverses and order-independent."""
    for label in ("endo", "exo"):
        reaction = _reaction(label)
        assert reaction.schema_version == 1
        assert reaction.reaction_scope == "net_reaction"
        assert reaction.to_dict() == _load_json(FIXTURE_DIR / f"reaction-{label}.json")

        mapping = reaction.atom_mapping
        left_addresses = {
            f"{structure.structure_id}:{atom_id}"
            for structure in reaction.left
            for atom_id in structure.atom_ids
        }
        right_addresses = {
            f"{structure.structure_id}:{atom_id}"
            for structure in reaction.right
            for atom_id in structure.atom_ids
        }
        assert len(left_addresses) == len(right_addresses) == 23
        assert set(mapping.left_to_right) == left_addresses
        assert set(mapping.right_to_left) == right_addresses
        assert mapping.right_to_left == {
            right: left for left, right in mapping.left_to_right.items()
        }
        assert not mapping.unmapped_left
        assert not mapping.unmapped_right

        payload = copy.deepcopy(_load_json(FIXTURE_DIR / f"reaction-{label}.json"))
        product = payload["right"][0]
        product["atom_ids"] = list(reversed(product["atom_ids"]))
        product["elements"] = list(reversed(product["elements"]))
        reordered = Reaction.from_dict(payload)
        assert reordered.atom_mapping.to_dict() == mapping.to_dict()
        assert reordered.right[0].atom_ids == tuple(product["atom_ids"])


def test_selectivity_paths_are_comparable() -> None:
    """Both paths share the left side, conditions, mapping topology and bond changes."""
    endo = _reaction("endo")
    exo = _reaction("exo")
    assert endo.left == exo.left
    assert endo.conditions == exo.conditions
    assert _normalized_mapping(endo) == _normalized_mapping(exo)
    assert _normalized_bond_changes(endo) == _normalized_bond_changes(exo)
    assert endo.right[0].formula == exo.right[0].formula == "C9H12O2"
    assert endo.right[0].atom_ids == exo.right[0].atom_ids
    assert endo.right[0].elements == exo.right[0].elements
    assert endo.right[0].stereochemistry != exo.right[0].stereochemistry

    questions = _load_json(QUESTIONS_PATH)
    assert questions["constructed"] is True
    assert questions["real_run"] is False
    conditions = [item["comparison_conditions"] for item in questions["questions"]]
    assert conditions[0] == conditions[1]
    assert conditions[0]["method"] == "wB97X-D4/def2-SVP"
    assert conditions[0]["energy_unit"] == "kcal_per_mol"
    assert conditions[0]["compatibility_requirements"] == [
        "same_method",
        "same_electronic_state",
        "same_temperature",
        "same_pressure",
        "same_standard_state",
        "same_reference_state",
        "same_energy_unit",
    ]
    for payload in questions["questions"]:
        parsed = Question.from_dict(payload)
        assert parsed.question_type == "activation_energy"
        assert parsed.comparison_conditions.energy_unit == "kcal_per_mol"
        assert parsed.comparison_conditions.electronic_state.charge == 0
        assert parsed.comparison_conditions.electronic_state.multiplicity == 1

    comparison = questions["selectivity_comparison"]
    assert comparison["both_paths_required"] is True
    assert comparison["tolerance_kcal_per_mol"] == 0.5
    assert comparison["no_experimental_claim"] is True
    assert "endo preference" in comparison["preference_rule"]
    assert "exo preference" in comparison["preference_rule"]


def test_selectivity_endpoints_are_stereochemically_distinguishable() -> None:
    """The two endpoints differ in stereochemistry, not in atom order."""
    manifest = _manifest()
    endo_reaction = _reaction("endo")
    exo_reaction = _reaction("exo")
    endo_mol = _product_mol("endo")
    exo_mol = _product_mol("exo")

    endo_ids = list(endo_reaction.right[0].atom_ids)
    exo_ids = list(exo_reaction.right[0].atom_ids)
    assert endo_ids == exo_ids
    assert list(endo_reaction.right[0].elements) == list(exo_reaction.right[0].elements)
    assert [atom.GetSymbol() for atom in endo_mol.GetAtoms()] == list(
        endo_reaction.right[0].elements
    )
    assert [atom.GetSymbol() for atom in exo_mol.GetAtoms()] == list(exo_reaction.right[0].elements)

    endo_signature = _signature(endo_mol, endo_ids)
    exo_signature = _signature(exo_mol, exo_ids)
    print("endo endpoint signature:", json.dumps(endo_signature, sort_keys=True))
    print("exo endpoint signature:", json.dumps(exo_signature, sort_keys=True))

    _assert_signature_matches(endo_signature, manifest["paths"][0]["signature"])
    _assert_signature_matches(exo_signature, manifest["paths"][1]["signature"])
    assert manifest["paths"][0]["endpoint_digest"] == _sha256_file(FIXTURE_DIR / "product-endo.mol")
    assert manifest["paths"][1]["endpoint_digest"] == _sha256_file(FIXTURE_DIR / "product-exo.mol")

    # The stereochemical signature differs at the declared centre.
    assert endo_signature["cip_configuration"] == "S"
    assert exo_signature["cip_configuration"] == "R"
    assert endo_signature["cip_configuration"] != exo_signature["cip_configuration"]
    assert endo_signature["all_cip_centers"]["pd_c5"] == "S"
    assert exo_signature["all_cip_centers"]["pd_c5"] == "R"
    assert (
        endo_signature["all_cip_centers"]["pd_c1"]
        == (exo_signature["all_cip_centers"]["pd_c1"])
        == "R"
    )
    assert (
        endo_signature["all_cip_centers"]["pd_c6"]
        == (exo_signature["all_cip_centers"]["pd_c6"])
        == "R"
    )
    assert endo_signature["signed_volume_sign"] == -1
    assert exo_signature["signed_volume_sign"] == 1
    assert endo_signature["signed_volume_sign"] != exo_signature["signed_volume_sign"]

    # The face descriptor is endo for one path and exo for the other.
    assert endo_signature["face"] == "endo"
    assert exo_signature["face"] == "exo"
    assert math.copysign(1.0, endo_signature["c7_signed_distance_angstrom"]) == math.copysign(
        1.0, endo_signature["ester_carbon_signed_distance_angstrom"]
    )
    assert math.copysign(1.0, exo_signature["c7_signed_distance_angstrom"]) != math.copysign(
        1.0, exo_signature["ester_carbon_signed_distance_angstrom"]
    )

    # The declared reaction stereo matches the geometry-derived CIP labels.
    endo_centers = {
        center.atom_id: center.configuration
        for center in endo_reaction.right[0].stereochemistry.centers
    }
    exo_centers = {
        center.atom_id: center.configuration
        for center in exo_reaction.right[0].stereochemistry.centers
    }
    assert endo_centers == endo_signature["all_cip_centers"]
    assert exo_centers == exo_signature["all_cip_centers"]

    # The two endpoints are not the same geometry.
    rmsd = _kabsch_rmsd(_heavy_coordinates(endo_mol), _heavy_coordinates(exo_mol))
    assert rmsd > 0.1
    print(f"heavy-atom RMSD after Kabsch alignment: {rmsd:.3f} A")


def test_selectivity_endpoint_signature_survives_atom_order_permutation(
    tmp_path: Path,
) -> None:
    """Renumbering the endpoint atoms does not change its stereo signature."""
    reaction = _reaction("endo")
    atom_ids = list(reaction.right[0].atom_ids)
    baseline = _signature(_product_mol("endo"), atom_ids)

    order = list(range(len(atom_ids)))[::-1]
    permuted = Chem.RenumberAtoms(_product_mol("endo"), order)
    permuted_ids = [atom_ids[position] for position in order]
    path = tmp_path / "permuted-endo.mol"
    Chem.MolToMolFile(permuted, str(path), kekulize=True)
    reloaded = Chem.MolFromMolFile(str(path), removeHs=False)
    assert reloaded is not None
    signature = _signature(reloaded, permuted_ids)

    assert signature["cip_configuration"] == baseline["cip_configuration"] == "S"
    assert signature["signed_volume_sign"] == baseline["signed_volume_sign"] == -1
    assert signature["face"] == baseline["face"] == "endo"
    assert signature["all_cip_centers"]["pd_c5"] == "S"
    assert signature["all_cip_centers"]["pd_c1"] == "R"
    assert signature["all_cip_centers"]["pd_c6"] == "R"


def test_selectivity_does_not_reuse_a14_symmetric_structures() -> None:
    """The selectivity case is not the symmetric A-14 butadiene + ethene fixture."""
    manifest = _manifest()
    a14_manifest = _load_json(A14_DIR / "fixture-manifest.json")
    assert manifest["fixture_id"] != a14_manifest["fixture_id"]
    assert manifest["paths"][0]["product_formula"] == "C9H12O2"
    assert manifest["paths"][0]["product_atom_count"] == 23
    assert not set(manifest["file_digests"].values()) & set(a14_manifest["file_digests"].values())

    a14_reaction = _load_json(A14_DIR / "reaction.json")
    a14_formulas = {structure["formula"] for structure in a14_reaction["left"]}
    a14_formulas |= {structure["formula"] for structure in a14_reaction["right"]}
    assert "C6H10" in a14_formulas
    assert "C4H6" in a14_formulas
    assert "C2H4" in a14_formulas

    left_formulas = Counter(structure.formula for structure in _reaction("endo").left)
    assert left_formulas == Counter({"C5H6": 1, "C4H6O2": 1})
    assert "C6H10" not in left_formulas


def test_selectivity_producer_request_names_paths_run_key_budget_and_reference() -> None:
    """The producer request names both paths, the run key, budget and reference."""
    text = PRODUCER_REQUEST_PATH.read_text(encoding="utf-8")
    required_tokens = (
        "owner=C, scenario=experience:cross-domain-live, subcase=selectivity",
        "`path.endo`",
        "`path.exo`",
        "`experience:selectivity`",
        "`run_id`",
        "`max_task_refs`",
        "`max_qc_invocations`",
        "`science_deadline_seconds`",
        "`export_budget_seconds`",
        "`aggregate_budget_seconds`",
        "3600",
        "inconclusive",
        "NOT_VERIFIED",
        "A-25a",
        "C T-40a",
        "B23b",
        "A-25b",
        "C T-40b",
        "constructed",
        "selectivity-live",
    )
    for token in required_tokens:
        assert token in text, token
    assert "does **not** issue a run key" in text
    assert "does **not** claim a result" in text
    assert "no separate `selectivity-live` scenario" in text


# ── A-25b: bounded selectivity audit over B23b-authorized artifacts ────────
#
# The report-scenario fixtures are constructed: the two path evidence reports,
# the B23b export records and the cross-domain budget tables are placeholders
# assembled with the frozen evidence contract.  No QC job, TS search, IRC,
# energy or experimental measurement ran.  The audit therefore never reports a
# real run and H6 stays open even when the constructed preference is supported.

from cccp.analysis.research.selectivity import (  # noqa: E402
    build_selectivity_audit,
)

SCENARIO_DIR = FIXTURE_DIR / "report-scenario"


def _scenario(name: str) -> dict[str, Any]:
    return _load_json(SCENARIO_DIR / name)


def _record_with_digest(name: str) -> dict[str, Any]:
    payload = _scenario(name)
    payload["record_digest"] = _sha256_file(SCENARIO_DIR / name)
    return payload


def _audit(
    *,
    export: str = "b23b-export-qualified.json",
    references: str = "reference-records.json",
    budget: str = "cross-domain-budget.json",
    paths: list[dict[str, Any]] | None = None,
    constructed: bool = True,
    signoff: dict[str, Any] | None = None,
) -> Any:
    b23b = _record_with_digest(export)
    if paths is None:
        paths = list(b23b.get("path_records", []))
    return build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario(references),
        b23b=b23b,
        budget=_record_with_digest(budget),
        paths=paths,
        read_root=SCENARIO_DIR,
        c_t40b_signoff=signoff,
        constructed=constructed,
    )


def test_a25b_qualified_two_path_report_states_a_bounded_preference() -> None:
    result = _audit()
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.constructed is True
    assert result.real_run is False
    assert result.h6_open is True
    assert result.preference == "endo"
    assert result.preference_delta_kcal_per_mol == pytest.approx(-3.0)
    assert result.preference_status == "supported"
    assert result.path_statuses == {
        "path.endo": "supported",
        "path.exo": "supported",
    }
    assert result.claim_status("selectivity.reference.registered") == "supported"
    assert result.claim_status("selectivity.path.endo.qualified") == "supported"
    assert result.claim_status("selectivity.path.exo.qualified") == "supported"
    assert result.claim_status("selectivity.preference") == "supported"
    assert result.claim_status("selectivity.budget") == "supported"
    assert result.claim_status("selectivity.experimental_prediction") == "not_evaluated"
    assert "lim-constructed" in result.limitations
    assert "lim-no-experimental" in result.limitations
    assert "lim-h6-open" in result.limitations
    assert "lim-relative-only" in result.limitations
    assert result.gaps == ()


def test_a25b_budget_and_coverage_table_records_subrows_and_cumulative() -> None:
    result = _audit()
    budget = result.budget_summary
    assert budget["run_id"] == "constructed-run-0001"
    rows = {row["domain"]: row for row in budget["subrows"]}
    assert set(rows) == {"nmr", "thermo", "selectivity"}
    assert rows["nmr"]["task_ref_count"] == rows["nmr"]["step_count"] == 0
    assert rows["nmr"]["reused"] is True
    assert rows["nmr"]["reused_artifacts"] == ["A-24a:nmr-comparison:constructed"]
    assert rows["thermo"]["reused_artifacts"] == ["A-24b:ensemble-thermo:constructed"]
    assert rows["selectivity"]["task_ref_count"] == 1
    assert rows["selectivity"]["step_count"] == 8
    assert rows["selectivity"]["qc_count"] == 14
    assert budget["cumulative"] == {
        "task_refs": 1,
        "steps": 8,
        "qc_calls": 14,
        "wall_seconds": None,
    }
    assert budget["subrow_totals"] == {"task_refs": 1, "steps": 8, "qc_calls": 14}
    assert budget["reused_artifacts"] == [
        "A-24a:nmr-comparison:constructed",
        "A-24b:ensemble-thermo:constructed",
    ]
    coverage = result.coverage
    assert coverage["both_paths_qualified"] is True
    assert coverage["paths"]["path.endo"]["qualified"] is True
    assert coverage["paths"]["path.exo"]["qualified"] is True
    assert coverage["budget_domains"] == ["nmr", "thermo", "selectivity"]
    assert coverage["reused_artifacts"] == budget["reused_artifacts"]


def test_a25b_report_uses_the_shared_evidence_contract_and_sources_verify() -> None:
    from cccp.analysis.research.evidence import (
        evidence_report_from_dict,
        verify_claim_sources,
    )

    result = _audit()
    report = result.report
    assert report.schema_version == 1
    assert evidence_report_from_dict(report.to_dict()) == report
    assert report.overall_claim_status == "not_evaluated"
    rows = verify_claim_sources(report, SCENARIO_DIR)
    assert rows
    assert all(row.to_dict()["status"] == "PASS" for row in rows)


def test_a25b_one_missing_path_refuses_a_preference() -> None:
    result = _audit(export="b23b-export-one-path.json")
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert result.path_statuses == {
        "path.endo": "supported",
        "path.exo": "not_evaluated",
    }
    assert result.claim_status("selectivity.path.exo.qualified") == "not_evaluated"
    assert any("path.exo" in gap for gap in result.gaps)


def test_a25b_absent_b23b_artifacts_is_not_verified_and_h6_open() -> None:
    result = _audit(export="b23b-export-absent.json", paths=[])
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.preference is None
    assert result.preference_status == "not_evaluated"
    assert result.h6_open is True
    assert result.claim_status("selectivity.reference.registered") == "not_evaluated"
    assert any("readiness" in gap for gap in result.gaps)
    assert any("both declared paths" in gap for gap in result.gaps)


def test_a25b_duplicate_public_key_is_not_verified_and_states_no_preference() -> None:
    result = _audit(references="reference-records-duplicate-key.json")
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.preference is None
    assert result.preference_status == "not_evaluated"
    assert any("two subcases share one public" in gap for gap in result.gaps)


def test_a25b_cumulative_budget_mismatch_refuses_a_preference() -> None:
    result = _audit(budget="cross-domain-budget-mismatch.json")
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert result.claim_status("selectivity.budget") == "inconclusive"
    assert any("cumulative budget total" in gap for gap in result.gaps)


def test_a25b_changed_run_id_does_not_reset_the_cumulative_budget() -> None:
    budget = _record_with_digest("cross-domain-budget.json")
    budget["run_id"] = "constructed-run-different"
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=budget,
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.preference is None
    assert any("run_id differs" in gap for gap in result.gaps)


def test_a25b_missing_budget_subrow_is_not_verified() -> None:
    budget = _record_with_digest("cross-domain-budget.json")
    budget["subrows"] = [row for row in budget["subrows"] if row["domain"] != "nmr"]
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=budget,
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.preference is None
    assert any("nmr, thermo and selectivity" in gap for gap in result.gaps)


def test_a25b_no_real_qc_binary_is_never_claimed_as_a_real_run() -> None:
    b23b = _record_with_digest("b23b-export-qualified.json")
    b23b.pop("constructed", None)
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=False,
    )
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.real_run is False
    assert any("no real QC binary ran" in gap for gap in result.platform_evidence_gaps)


def test_a25b_c_t40b_signoff_alone_does_not_close_h6_for_a_constructed_report() -> None:
    result = _audit(
        signoff={
            "owner": "C",
            "phase": "experience:selectivity",
            "run_id": "constructed-run-0001",
            "signed": True,
            "signed_after_audit": True,
        }
    )
    assert result.h6_open is True


def test_a25b_atom_order_only_difference_is_rejected_as_unqualified(tmp_path: Path) -> None:
    path_records = list(_record_with_digest("b23b-export-qualified.json")["path_records"])
    exo = copy.deepcopy(path_records[1])
    exo["endpoint_signature"]["cip_configuration"] = "S"
    exo["endpoint_signature"]["signed_volume_sign"] = -1
    path_records[1] = exo
    result = _audit(paths=path_records)
    assert result.path_statuses["path.exo"] == "inconclusive"
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert any("endpoint signature" in gap for gap in result.gaps)


def test_a25b_report_does_not_invent_an_experimental_quantitative_claim() -> None:
    result = _audit()
    claim = result.report.claim("selectivity.experimental_prediction")
    assert claim is not None
    assert claim.status == "not_evaluated"
    assert {check.check_id for check in claim.missing_checks} == {"not_executed"}
    preference = result.report.claim("selectivity.preference")
    assert preference is not None
    assert "not an experimental" in preference.statement
    for limitation in result.report.limitations:
        if limitation.limitation_id == "lim-no-experimental":
            assert "No experimental quantitative claim" in limitation.description


# ── A-25b: reference, readiness, budget-reset and key-uniqueness edges ─────

from cccp.analysis.research.selectivity import (  # noqa: E402
    B23B_READINESS,
    PATH_IDS,
    SELECTIVITY_PHASE,
    SELECTIVITY_SCENARIO,
    SelectivityPublicKey,
)


def test_a25b_absent_reference_is_not_verified_and_h6_open() -> None:
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=[],
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.public_key is None
    assert result.preference is None
    assert result.preference_status == "not_evaluated"
    assert result.h6_open is True
    assert any("no registered reference" in gap for gap in result.gaps)
    claim = result.report.claim("selectivity.reference.registered")
    assert claim is not None
    assert claim.status == "not_evaluated"
    check_ids = {check.check_id for check in claim.missing_checks}
    assert "run_key_unavailable" in check_ids
    assert "external_artifact_unavailable" in check_ids


def test_a25b_b23b_readiness_must_be_integration_ready_not_verified() -> None:
    b23b = _record_with_digest("b23b-export-qualified.json")
    b23b["readiness"] = "verified"
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert B23B_READINESS == "integration_ready"
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.preference is None
    assert result.preference_status == "not_evaluated"
    assert any("not 'integration_ready'" in gap for gap in result.gaps)
    assert result.claim_status("selectivity.reference.registered") == "not_evaluated"


def test_a25b_b23b_artifact_id_is_recorded_verbatim_not_from_a_numeric_prefix() -> None:
    b23b = _record_with_digest("b23b-export-qualified.json")
    b23b["artifact_id"] = "B23b:selectivity-authorized-artifacts"
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.b23b_artifact_id == "B23b:selectivity-authorized-artifacts"
    assert result.b23b_readiness == "integration_ready"


def test_a25b_previous_cumulative_decrease_is_refused() -> None:
    budget = _record_with_digest("cross-domain-budget.json")
    budget["previous_cumulative"] = {"task_refs": 2, "steps": 9, "qc_calls": 15}
    budget["previous_run_ids"] = ["constructed-run-0000"]
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=budget,
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert any("decreased" in gap for gap in result.gaps)
    assert result.claim_status("selectivity.budget") == "inconclusive"


def test_a25b_public_key_excludes_display_only_subcase() -> None:
    key = SelectivityPublicKey(
        owner="C",
        scenario=SELECTIVITY_SCENARIO,
        phase=SELECTIVITY_PHASE,
        run_id="constructed-run-0001",
        subcase="selectivity",
    )
    assert key.key() == (
        "C",
        SELECTIVITY_SCENARIO,
        SELECTIVITY_PHASE,
        "constructed-run-0001",
    )
    other = SelectivityPublicKey(
        owner="C",
        scenario=SELECTIVITY_SCENARIO,
        phase=SELECTIVITY_PHASE,
        run_id="constructed-run-0001",
        subcase="nmr",
    )
    assert other.key() == key.key()
    assert PATH_IDS == ("path.endo", "path.exo")
    result = _audit()
    assert result.public_key is not None
    assert result.public_key.key() == key.key()
    assert result.public_key.subcase == "selectivity"


# ── A-25b: published absent-producer report and adversarial edges ──────────

from cccp.analysis.research.evidence import evidence_report_from_dict  # noqa: E402
from cccp.analysis.research.selectivity import (  # noqa: E402
    B23B_EDGE_ARTIFACT_ID,
    SELECTIVITY_OWNER,
)

PUBLISHED_REPORT = (
    REPO
    / "docs"
    / "reference"
    / "next-change"
    / "acp2-contracts"
    / "science"
    / "selectivity-evidence-report.json"
)


def _published_report() -> dict[str, Any]:
    return _load_json(PUBLISHED_REPORT)


def _absent_audit(report_id: str = "selectivity-a25b-absent-producer-report") -> Any:
    return build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=(),
        b23b=None,
        budget=None,
        paths=(),
        read_root=FIXTURE_DIR,
        constructed=True,
        report_id=report_id,
    )


def test_a25b_published_report_is_absent_producer_not_verified_and_h6_open() -> None:
    """The checked-in report is a real absent-producer artifact, not a result."""
    payload = _published_report()
    report = evidence_report_from_dict(payload["report"])
    assert evidence_report_from_dict(report.to_dict()) == report
    assert report.schema_version == 1

    audit = payload["audit"]
    assert audit["platform_evidence_state"] == "NOT_VERIFIED"
    assert audit["real_run"] is False
    assert audit["constructed"] is True
    assert audit["h6_open"] is True
    assert audit["preference"] is None
    assert audit["preference_status"] == "not_evaluated"
    assert audit["both_paths_qualified"] is False
    assert audit["path_statuses"] == {
        "path.endo": "not_evaluated",
        "path.exo": "not_evaluated",
    }
    assert audit["public_key"] is None
    assert audit["b23b_artifact_id"] is None
    assert audit["b23b_readiness"] is None
    assert audit["budget"] == {}
    assert audit["absent_producer"] == {
        "reference_present": False,
        "b23b_artifact_present": False,
        "budget_table_present": False,
        "path_records_present": False,
        "run_key_present": False,
        "note": (
            "Missing producer inputs: reference, b23b_artifact, budget_table, "
            "path_records, run_key. This report records the absent-producer state "
            "and is never upgraded by this file's presence."
        ),
    }

    joined_gaps = " ".join([*audit["gaps"], *audit["platform_evidence_gaps"]])
    for token in (
        "experience:cross-domain-live",
        "subcase=selectivity",
        "run_id",
        B23B_EDGE_ARTIFACT_ID,
        "budget",
    ):
        assert token in joined_gaps, token

    coverage = audit["coverage"]
    assert coverage["budget_table_present"] is False
    assert coverage["path_records_present"] is False
    assert coverage["budget_domains"] == ["nmr", "thermo", "selectivity"]
    assert coverage["b23b_edge_artifact_id"] == B23B_EDGE_ARTIFACT_ID
    requirement = coverage["budget_requirement"]
    assert requirement["max_task_refs"] == 1
    assert requirement["max_candidate_paths"] == 2
    assert requirement["max_qc_invocations"] == 14
    assert requirement["science_deadline_seconds"] == 3000
    assert requirement["export_budget_seconds"] == 600
    assert requirement["aggregate_budget_seconds"] == 3600
    assert requirement["required_subrow_domains"] == ["nmr", "thermo", "selectivity"]
    assert requirement["constructed"] is True

    assert all(claim.status == "not_evaluated" for claim in report.claims)
    reference_claim = report.claim("selectivity.reference.registered")
    assert reference_claim is not None
    assert {check.check_id for check in reference_claim.missing_checks} >= {
        "run_key_unavailable",
        "external_artifact_unavailable",
    }
    preference_claim = report.claim("selectivity.preference")
    assert preference_claim is not None
    assert preference_claim.status == "not_evaluated"
    experimental_claim = report.claim("selectivity.experimental_prediction")
    assert experimental_claim is not None
    assert experimental_claim.status == "not_evaluated"
    assert {check.check_id for check in experimental_claim.missing_checks} == {"not_executed"}

    limitation_ids = {limitation.limitation_id for limitation in report.limitations}
    assert limitation_ids == {
        "lim-budget-unmeasured",
        "lim-constructed",
        "lim-h6-open",
        "lim-no-experimental",
        "lim-platform-not-verified",
        "lim-relative-only",
        "lim-single-conformer",
        "lim-single-regioisomer",
    }


def test_a25b_published_report_rebuilds_deterministically_from_the_absent_state() -> None:
    """The checked-in report is exactly the module's absent-producer output."""
    payload = _published_report()
    rebuilt = _absent_audit().to_dict()
    assert rebuilt["report"] == payload["report"]
    assert rebuilt["audit"] == payload["audit"]


def test_a25b_tampered_budget_digest_cannot_state_a_preference() -> None:
    """A budget record whose bytes do not match its digest is inconclusive."""
    budget = _record_with_digest("cross-domain-budget.json")
    budget["record_digest"] = "0" * 64
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=budget,
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=False,
    )
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert result.claim_status("selectivity.budget") == "inconclusive"
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.real_run is False
    assert any("selectivity.budget.unverifiable" in gap for gap in result.gaps)


def test_a25b_path_escape_is_rejected_before_any_digest_is_read(tmp_path: Path) -> None:
    """A path report that traverses parents never becomes path evidence."""
    b23b = _record_with_digest("b23b-export-qualified.json")
    path_records = copy.deepcopy(b23b["path_records"])
    path_records[0]["report_path"] = "../../../etc/passwd"
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=path_records,
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.path_statuses["path.endo"] == "not_evaluated"
    assert result.preference is None
    assert any("relative path without '..'" in gap for gap in result.gaps)
    assert not (tmp_path / "etc").exists()


def test_a25b_previous_run_ids_without_cumulative_is_refused() -> None:
    """A changed run_id cannot hide a reset when the prior total is unstated."""
    budget = _record_with_digest("cross-domain-budget.json")
    budget["previous_run_ids"] = ["constructed-run-0000"]
    budget["previous_cumulative"] = None
    b23b = _record_with_digest("b23b-export-qualified.json")
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=budget,
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert result.claim_status("selectivity.budget") == "inconclusive"
    assert any("selectivity.budget.reset" in gap for gap in result.gaps)


def test_a25b_contract_ready_readiness_is_rejected_like_verified() -> None:
    """Only integration_ready closes the B23b edge, not contract_ready/verified."""
    for readiness in ("contract_ready", "verified"):
        b23b = _record_with_digest("b23b-export-qualified.json")
        b23b["readiness"] = readiness
        result = build_selectivity_audit(
            fixture_manifest=_manifest(),
            reference_records=_scenario("reference-records.json"),
            b23b=b23b,
            budget=_record_with_digest("cross-domain-budget.json"),
            paths=b23b["path_records"],
            read_root=SCENARIO_DIR,
            constructed=True,
        )
        assert result.platform_evidence_state == "NOT_VERIFIED"
        assert result.preference is None
        assert result.claim_status("selectivity.reference.registered") == "not_evaluated"
        assert any(f"not {B23B_READINESS!r}" in gap for gap in result.gaps), readiness


def test_a25b_one_qualified_path_never_selects_the_other_path() -> None:
    """When only one path qualifies the preference is absent and inconclusive."""
    result = _audit(export="b23b-export-one-path.json")
    assert result.preference is None
    assert result.preference_status == "inconclusive"
    assert result.path_statuses["path.endo"] == "supported"
    assert result.path_statuses["path.exo"] == "not_evaluated"
    preference_claim = result.report.claim("selectivity.preference")
    assert preference_claim is not None
    assert preference_claim.status == "inconclusive"
    assert "endo" not in preference_claim.statement.lower().replace("endo/exo", "")


def test_a25b_reference_owner_and_scenario_are_registered_values() -> None:
    """The audit cannot approve a differently-owned or differently-named reference."""
    assert SELECTIVITY_OWNER == "C"
    assert SELECTIVITY_SCENARIO == "experience:cross-domain-live"
    assert SELECTIVITY_PHASE == "experience:selectivity"
    records = copy.deepcopy(_scenario("reference-records.json"))
    records[0]["owner"] = "B"
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=records,
        b23b=_record_with_digest("b23b-export-qualified.json"),
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=_record_with_digest("b23b-export-qualified.json")["path_records"],
        read_root=SCENARIO_DIR,
        constructed=True,
    )
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.public_key is None
    assert result.preference is None
    assert any("no registered reference" in gap for gap in result.gaps)


def test_a25b_self_declared_constructed_producer_cannot_masquerade_as_real() -> None:
    """A producer that declares constructed data stays NOT_VERIFIED even if it claims QC."""
    b23b = _record_with_digest("b23b-export-qualified.json")
    assert b23b["constructed"] is True
    b23b["real_qc_binary_ran"] = True
    result = build_selectivity_audit(
        fixture_manifest=_manifest(),
        reference_records=_scenario("reference-records.json"),
        b23b=b23b,
        budget=_record_with_digest("cross-domain-budget.json"),
        paths=b23b["path_records"],
        read_root=SCENARIO_DIR,
        constructed=False,
    )
    assert result.constructed is True
    assert result.real_run is False
    assert result.platform_evidence_state == "NOT_VERIFIED"
    assert result.h6_open is True
