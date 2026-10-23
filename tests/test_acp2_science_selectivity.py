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
