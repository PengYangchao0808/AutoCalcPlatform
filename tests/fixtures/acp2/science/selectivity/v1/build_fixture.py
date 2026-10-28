# ruff: noqa: E501
"""Build the constructed ACP2 selectivity fixture (A-25a).

The committed fixture files are the frozen artifacts. This script is the
provenance/reproduction record: it uses fixed RDKit ETKDG seeds and MMFF
optimization and writes the mol/XYZ structures, the two Reaction v1 documents,
the two activation-energy questions, and the manifest with recomputed digests.

The fixture is CONSTRUCTED. No QC job, TS search, IRC, energy or experimental
measurement exists. Running this script overwrites the committed files; do not
run it unless the fixture is being deliberately re-frozen and every digest in
the manifest and the tests is updated together.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.EnumerateStereoisomers import EnumerateStereoisomers, StereoEnumerationOptions

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

PRODUCT_SMILES = "C1=CC2CC(C(=O)OC)C1C2"
CP_SMILES = "C1C=CC=C1"
MA_SMILES = "C=CC(=O)OC"

HEAVY_PRODUCT_IDS = [
    "pd_c2",
    "pd_c3",
    "pd_c1",
    "pd_c4",
    "pd_c5",
    "pd_c8",
    "pd_o1",
    "pd_o2",
    "pd_c9",
    "pd_c6",
    "pd_c7",
]
H_IDS_BY_HEAVY = {
    0: ["pd_h2"],
    1: ["pd_h3"],
    2: ["pd_h1"],
    3: ["pd_h7", "pd_h8"],
    4: ["pd_h9"],
    5: [],
    6: [],
    7: [],
    8: ["pd_h10", "pd_h11", "pd_h12"],
    9: ["pd_h4"],
    10: ["pd_h5", "pd_h6"],
}
CP_HEAVY_IDS = ["cp_c5", "cp_c1", "cp_c2", "cp_c3", "cp_c4"]
CP_H_IDS_BY_HEAVY = {0: ["cp_h5", "cp_h6"], 1: ["cp_h1"], 2: ["cp_h2"], 3: ["cp_h3"], 4: ["cp_h4"]}
MA_HEAVY_IDS = ["ma_c1", "ma_c2", "ma_c3", "ma_o1", "ma_o2", "ma_c4"]
MA_H_IDS_BY_HEAVY = {
    0: ["ma_h1", "ma_h2"],
    1: ["ma_h3"],
    2: [],
    3: [],
    4: [],
    5: ["ma_h4", "ma_h5", "ma_h6"],
}

MAP = {
    "cp_c1": "pd_c1",
    "cp_c2": "pd_c2",
    "cp_c3": "pd_c3",
    "cp_c4": "pd_c6",
    "cp_c5": "pd_c7",
    "cp_h1": "pd_h1",
    "cp_h2": "pd_h2",
    "cp_h3": "pd_h3",
    "cp_h4": "pd_h4",
    "cp_h5": "pd_h5",
    "cp_h6": "pd_h6",
    "ma_c1": "pd_c4",
    "ma_c2": "pd_c5",
    "ma_c3": "pd_c8",
    "ma_o1": "pd_o1",
    "ma_o2": "pd_o2",
    "ma_c4": "pd_c9",
    "ma_h1": "pd_h7",
    "ma_h2": "pd_h8",
    "ma_h3": "pd_h9",
    "ma_h4": "pd_h10",
    "ma_h5": "pd_h11",
    "ma_h6": "pd_h12",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def assign_ids(
    mol: Chem.Mol, heavy_ids: list[str], h_ids: dict[int, list[str]]
) -> tuple[list[str], list[str]]:
    atom_ids: list[str | None] = [None] * mol.GetNumAtoms()
    for idx, atom_id in enumerate(heavy_ids):
        atom_ids[idx] = atom_id
    for heavy_idx, ids in h_ids.items():
        h_neighbors = [
            n.GetIdx()
            for n in mol.GetAtomWithIdx(heavy_idx).GetNeighbors()
            if n.GetAtomicNum() == 1
        ]
        assert len(h_neighbors) == len(ids), (heavy_idx, len(h_neighbors), ids)
        for h_idx, atom_id in zip(h_neighbors, ids, strict=True):
            atom_ids[h_idx] = atom_id
    assert all(x is not None for x in atom_ids)
    return [str(x) for x in atom_ids], [a.GetSymbol() for a in mol.GetAtoms()]


def embed(smiles: str, seed: int) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    mol = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.useSmallRingTorsions = True
    params.enforceChirality = True
    assert AllChem.EmbedMolecule(mol, params) == 0, smiles
    assert AllChem.MMFFOptimizeMolecule(mol, maxIters=10000) == 0
    return mol


def plane_normal(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray) -> np.ndarray:
    n = np.cross(p1 - p0, p2 - p0)
    return n / np.linalg.norm(n)


def signed_distance(p: np.ndarray, p0: np.ndarray, n: np.ndarray) -> float:
    return float(np.dot(p - p0, n))


def write_xyz(mol: Chem.Mol, path: Path) -> None:
    conf = mol.GetConformer()
    lines = [str(mol.GetNumAtoms()), "constructed ACP2 selectivity fixture"]
    for idx in range(mol.GetNumAtoms()):
        pos = conf.GetAtomPosition(idx)
        symbol = mol.GetAtomWithIdx(idx).GetSymbol()
        lines.append(f"{symbol:2s} {pos.x: .6f} {pos.y: .6f} {pos.z: .6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_product(iso: Chem.Mol, seed: int) -> tuple[Chem.Mol, list[str], list[str]]:
    mol = Chem.AddHs(iso)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    params.useSmallRingTorsions = True
    params.enforceChirality = True
    assert AllChem.EmbedMolecule(mol, params) == 0
    assert AllChem.MMFFOptimizeMolecule(mol, maxIters=10000) == 0
    atom_ids, elements = assign_ids(mol, HEAVY_PRODUCT_IDS, H_IDS_BY_HEAVY)
    return mol, atom_ids, elements


def product_signature(mol: Chem.Mol, atom_ids: list[str]) -> dict:
    idx = {atom_id: i for i, atom_id in enumerate(atom_ids)}
    c = np.array(mol.GetConformer().GetPositions())
    p0, p1, p2 = c[idx["pd_c4"]], c[idx["pd_c5"]], c[idx["pd_c6"]]
    n = plane_normal(p0, p1, p2)
    c7 = signed_distance(c[idx["pd_c7"]], p0, n)
    ester = signed_distance(c[idx["pd_c8"]], p0, n)
    face = "endo" if c7 * ester > 0 else "exo"
    volume = float(
        np.dot(
            c[idx["pd_c4"]] - c[idx["pd_c5"]],
            np.cross(c[idx["pd_c6"]] - c[idx["pd_c5"]], c[idx["pd_c8"]] - c[idx["pd_c5"]]),
        )
    )
    work = Chem.Mol(mol)
    Chem.AssignStereochemistryFrom3D(work)
    cip = {
        int(atom): conf
        for atom, conf in Chem.FindMolChiralCenters(
            work, includeUnassigned=True, useLegacyImplementation=False
        )
    }
    return {
        "stereocenter_atom_id": "pd_c5",
        "cip_configuration": cip[idx["pd_c5"]],
        "all_cip_centers": {atom_ids[atom]: conf for atom, conf in sorted(cip.items())},
        "signed_volume_atoms": ["pd_c4", "pd_c6", "pd_c8"],
        "signed_volume": volume,
        "signed_volume_sign": 1 if volume > 0 else -1,
        "reference_plane_atoms": ["pd_c4", "pd_c5", "pd_c6"],
        "c7_signed_distance_angstrom": c7,
        "ester_carbon_signed_distance_angstrom": ester,
        "face": face,
    }


def left_to_right(product_structure_id: str) -> dict[str, str]:
    result = {}
    for left, right in MAP.items():
        left_structure = "cyclopentadiene" if left.startswith("cp_") else "methyl_acrylate"
        result[f"{left_structure}:{left}"] = f"{product_structure_id}:{right}"
    return result


def topology_map() -> dict[str, str]:
    return {left: right for left, right in MAP.items()}


def reaction(
    product_structure_id: str, reaction_id: str, product_species: str, centers: list[dict]
) -> dict:
    l2r = left_to_right(product_structure_id)
    r2l = {v: k for k, v in l2r.items()}
    return {
        "schema_version": 1,
        "reaction_id": reaction_id,
        "reaction_scope": "net_reaction",
        "left": [
            {
                "structure_id": "cyclopentadiene",
                "species": "cyclopenta-1,3-diene",
                "formula": "C5H6",
                "input_format": "mol",
                "atom_id_scheme": "explicit",
                "atom_ids": CP_IDS,
                "elements": CP_ELEMENTS,
                "charge": 0,
                "multiplicity": 1,
                "stereochemistry": {"status": "defined", "centers": [], "double_bonds": []},
            },
            {
                "structure_id": "methyl_acrylate",
                "species": "methyl prop-2-enoate",
                "formula": "C4H6O2",
                "input_format": "mol",
                "atom_id_scheme": "explicit",
                "atom_ids": MA_IDS,
                "elements": MA_ELEMENTS,
                "charge": 0,
                "multiplicity": 1,
                "stereochemistry": {"status": "defined", "centers": [], "double_bonds": []},
            },
        ],
        "right": [
            {
                "structure_id": product_structure_id,
                "species": product_species,
                "formula": "C9H12O2",
                "input_format": "mol",
                "atom_id_scheme": "explicit",
                "atom_ids": PD_IDS,
                "elements": PD_ELEMENTS,
                "charge": 0,
                "multiplicity": 1,
                "stereochemistry": {"status": "defined", "centers": centers, "double_bonds": []},
            }
        ],
        "atom_mapping": {
            "left_to_right": l2r,
            "right_to_left": r2l,
            "unmapped_left": [],
            "unmapped_right": [],
        },
        "bond_changes": [
            {
                "change_type": "formed",
                "left_atoms": ["cyclopentadiene:cp_c1", "methyl_acrylate:ma_c1"],
                "right_atoms": [f"{product_structure_id}:pd_c1", f"{product_structure_id}:pd_c4"],
                "bond_order_left": 0,
                "bond_order_right": 1,
            },
            {
                "change_type": "formed",
                "left_atoms": ["cyclopentadiene:cp_c4", "methyl_acrylate:ma_c2"],
                "right_atoms": [f"{product_structure_id}:pd_c6", f"{product_structure_id}:pd_c5"],
                "bond_order_left": 0,
                "bond_order_right": 1,
            },
            {
                "change_type": "changed",
                "left_atoms": ["cyclopentadiene:cp_c1", "cyclopentadiene:cp_c2"],
                "right_atoms": [f"{product_structure_id}:pd_c1", f"{product_structure_id}:pd_c2"],
                "bond_order_left": 2,
                "bond_order_right": 1,
            },
            {
                "change_type": "changed",
                "left_atoms": ["cyclopentadiene:cp_c2", "cyclopentadiene:cp_c3"],
                "right_atoms": [f"{product_structure_id}:pd_c2", f"{product_structure_id}:pd_c3"],
                "bond_order_left": 1,
                "bond_order_right": 2,
            },
            {
                "change_type": "changed",
                "left_atoms": ["cyclopentadiene:cp_c3", "cyclopentadiene:cp_c4"],
                "right_atoms": [f"{product_structure_id}:pd_c3", f"{product_structure_id}:pd_c6"],
                "bond_order_left": 2,
                "bond_order_right": 1,
            },
            {
                "change_type": "changed",
                "left_atoms": ["methyl_acrylate:ma_c1", "methyl_acrylate:ma_c2"],
                "right_atoms": [f"{product_structure_id}:pd_c4", f"{product_structure_id}:pd_c5"],
                "bond_order_left": 2,
                "bond_order_right": 1,
            },
        ],
        "conditions": {
            "temperature_k": 298.15,
            "pressure_pa": 100000.0,
            "standard_state": "1_bar_ideal_gas",
            "solvent": None,
            "phase": "gas",
        },
        "input_uncertainty": {
            "unknown_fields": ["conditions.solvent"],
            "notes": "Constructed selectivity fixture. The two paths share the left side, atom-mapping topology and bond changes; only the product stereochemistry differs. No energy, TS geometry or experimental value is asserted.",
        },
    }


def question(question_id: str, hypothesis_id: str, path_label: str) -> dict:
    return {
        "schema_version": 1,
        "question_id": question_id,
        "question_type": "activation_energy",
        "hypothesis_refs": [hypothesis_id],
        "required_evidence_types": [
            "transition_state_geometry",
            "imaginary_frequency",
            "irc_forward",
            "irc_reverse",
            "stationary_point_gradient",
            "path_endpoints",
            "energy_difference",
            "thermochemistry",
        ],
        "claim_status_vocabulary": ["supported", "refuted", "inconclusive", "not_evaluated"],
        "comparison_conditions": {
            "method": "wB97X-D4/def2-SVP",
            "electronic_state": {"charge": 0, "multiplicity": 1, "state_id": "state.singlet.s0"},
            "temperature_k": 298.15,
            "pressure_pa": 100000.0,
            "standard_state": "1_bar_ideal_gas",
            "reference_state": "separated cyclopentadiene + methyl acrylate at infinite separation, and the declared product at the same conditions",
            "energy_unit": "kcal_per_mol",
            "compatibility_requirements": [
                "same_method",
                "same_electronic_state",
                "same_temperature",
                "same_pressure",
                "same_standard_state",
                "same_reference_state",
                "same_energy_unit",
            ],
            "unknown_fields": [],
        },
    }


# Build products
base = Chem.MolFromSmiles(PRODUCT_SMILES)
opts = StereoEnumerationOptions(onlyUnassigned=True, unique=True, tryEmbedding=False)
isomers = list(EnumerateStereoisomers(base, options=opts))
endo_mol, PD_IDS, PD_ELEMENTS = build_product(isomers[1], 0xE0D0)
exo_mol, exo_ids, exo_elements = build_product(isomers[3], 0xE0D1)
assert PD_IDS == exo_ids and PD_ELEMENTS == exo_elements
endo_sig = product_signature(endo_mol, PD_IDS)
exo_sig = product_signature(exo_mol, PD_IDS)
assert endo_sig["face"] == "endo", endo_sig
assert exo_sig["face"] == "exo", exo_sig
assert endo_sig["cip_configuration"] != exo_sig["cip_configuration"]
assert endo_sig["signed_volume_sign"] != exo_sig["signed_volume_sign"]

# Reactants
cp = embed(CP_SMILES, 0xC0FFEE)
ma = embed(MA_SMILES, 0xBEEF)
CP_IDS, CP_ELEMENTS = assign_ids(cp, CP_HEAVY_IDS, CP_H_IDS_BY_HEAVY)
MA_IDS, MA_ELEMENTS = assign_ids(ma, MA_HEAVY_IDS, MA_H_IDS_BY_HEAVY)

# MA conformer check
c = np.array(ma.GetConformer().GetPositions())


def dihedral(p0, p1, p2, p3):
    b0 = p0 - p1
    b1 = p2 - p1
    b2 = p3 - p2
    b1 = b1 / np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    return float(np.degrees(np.arctan2(np.dot(np.cross(b1, v), w), np.dot(v, w))))


ma_dihedral = dihedral(c[0], c[1], c[2], c[3])

# Write structures
for label, mol in (("endo", endo_mol), ("exo", exo_mol)):
    Chem.MolToMolFile(mol, str(OUT / f"product-{label}.mol"), kekulize=True)
    write_xyz(mol, OUT / f"product-{label}.xyz")
Chem.MolToMolFile(cp, str(OUT / "cyclopentadiene.mol"), kekulize=True)
Chem.MolToMolFile(ma, str(OUT / "methyl-acrylate.mol"), kekulize=True)
write_xyz(cp, OUT / "cyclopentadiene.xyz")
write_xyz(ma, OUT / "methyl-acrylate.xyz")

# Combined reactants XYZ (Cp then MA)
cp_conf = cp.GetConformer()
ma_conf = ma.GetConformer()
lines = [str(cp.GetNumAtoms() + ma.GetNumAtoms()), "constructed ACP2 selectivity reactants"]
for conf, mol in ((cp_conf, cp), (ma_conf, ma)):
    for i in range(mol.GetNumAtoms()):
        pos = conf.GetAtomPosition(i)
        lines.append(
            f"{mol.GetAtomWithIdx(i).GetSymbol():2s} {pos.x: .6f} {pos.y: .6f} {pos.z: .6f}"
        )
(OUT / "reactants.xyz").write_text("\n".join(lines) + "\n", encoding="utf-8")

# Reactions
ENDO_CENTERS = [
    {"atom_id": "pd_c1", "configuration": endo_sig["all_cip_centers"]["pd_c1"]},
    {"atom_id": "pd_c5", "configuration": endo_sig["cip_configuration"]},
    {"atom_id": "pd_c6", "configuration": endo_sig["all_cip_centers"]["pd_c6"]},
]
EXO_CENTERS = [
    {"atom_id": "pd_c1", "configuration": exo_sig["all_cip_centers"]["pd_c1"]},
    {"atom_id": "pd_c5", "configuration": exo_sig["cip_configuration"]},
    {"atom_id": "pd_c6", "configuration": exo_sig["all_cip_centers"]["pd_c6"]},
]
endo_reaction = reaction(
    "product_endo",
    "reaction.selectivity.cp_methyl_acrylate.endo.v1",
    "methyl bicyclo[2.2.1]hept-5-ene-2-carboxylate (endo)",
    ENDO_CENTERS,
)
exo_reaction = reaction(
    "product_exo",
    "reaction.selectivity.cp_methyl_acrylate.exo.v1",
    "methyl bicyclo[2.2.1]hept-5-ene-2-carboxylate (exo)",
    EXO_CENTERS,
)
for name, doc in (("endo", endo_reaction), ("exo", exo_reaction)):
    (OUT / f"reaction-{name}.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

# Questions
questions = {
    "schema": "acp2.science.selectivity-question-set",
    "schema_version": 1,
    "case_id": "selectivity.cp_methyl_acrylate.endo_exo.v1",
    "constructed": True,
    "real_run": False,
    "hypotheses": [
        {
            "hypothesis_id": "hypothesis.endo",
            "path_id": "path.endo",
            "statement": "The endo product is the lower-barrier path under the frozen conditions.",
        },
        {
            "hypothesis_id": "hypothesis.exo",
            "path_id": "path.exo",
            "statement": "The exo product is the lower-barrier path under the frozen conditions.",
        },
    ],
    "questions": [
        question("question.selectivity.endo.activation_energy.v1", "hypothesis.endo", "endo"),
        question("question.selectivity.exo.activation_energy.v1", "hypothesis.exo", "exo"),
    ],
    "selectivity_comparison": {
        "comparison_kind": "relative activation energy between the two declared paths",
        "primary_observable": "Delta-Delta-E-double-dagger = Delta-E-double-dagger(endo) - Delta-E-double-dagger(exo) in kcal_per_mol",
        "free_energy_observable": "Delta-Delta-G-double-dagger(endo/exo) only when both paths carry complete thermochemistry",
        "preference_rule": "endo preference if Delta-Delta-E <= -0.5 kcal_per_mol; exo preference if Delta-Delta-E >= +0.5 kcal_per_mol; otherwise no preference (inconclusive within tolerance)",
        "tolerance_kcal_per_mol": 0.5,
        "both_paths_required": True,
        "no_experimental_claim": True,
    },
}
(OUT / "questions.json").write_text(
    json.dumps(questions, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)


# Manifest
def digest_map(names: list[str]) -> dict[str, str]:
    return {name: sha256_file(OUT / name) for name in names}


files = [
    "cyclopentadiene.mol",
    "cyclopentadiene.xyz",
    "methyl-acrylate.mol",
    "methyl-acrylate.xyz",
    "reactants.xyz",
    "product-endo.mol",
    "product-endo.xyz",
    "product-exo.mol",
    "product-exo.xyz",
    "reaction-endo.json",
    "reaction-exo.json",
    "questions.json",
]
file_digests = digest_map(files)
topology = topology_map()
topology_digest = sha256_bytes(json.dumps(topology, sort_keys=True).encode())
manifest = {
    "schema": "acp2.science.fixture-manifest",
    "schema_version": 1,
    "fixture_id": "selectivity-cyclopentadiene-methyl-acrylate-endo-exo-wb97xd4-def2svp",
    "case_id": "selectivity.cp_methyl_acrylate.endo_exo.v1",
    "task_kind": "selectivity",
    "constructed": True,
    "real_run": False,
    "frozen_at": "2026-10-23T12:00:00Z",
    "frozen_at_basis": "constructed frozen epoch for the static fixture; it is not a run timestamp",
    "frozen_before_real_run": True,
    "source": "Constructed static fixture. All structures are RDKit ETKDG/MMFF-generated for geometric plausibility only; no QC optimization, TS search, IRC, energy or experimental measurement exists.",
    "reference": {
        "owner": "C",
        "scenario": "experience:cross-domain-live",
        "subcase": "selectivity",
        "phase": "experience:selectivity",
        "run_id": None,
        "run_id_gap": "B01 has not published the (owner, scenario, phase, run_id) registry; A does not invent a run_id.",
        "subcase_semantics": "subcase=selectivity is display metadata; the public identity is (owner, scenario, phase, run_id). Two subcases sharing one public key must be rejected as NOT_VERIFIED.",
    },
    "selectivity_definition": {
        "case_kind": "substituted Diels-Alder endo/exo competition",
        "diene": "cyclopentadiene",
        "dienophile": "methyl acrylate",
        "declared_regioisomer": "2-substituted methyl bicyclo[2.2.1]hept-5-ene-2-carboxylate",
        "stereocenter_atom_id": "pd_c5",
        "reference_plane_atoms": ["pd_c4", "pd_c5", "pd_c6"],
        "face_reference_atom_id": "pd_c7",
        "substituent_reference_atom_id": "pd_c8",
        "endo_definition": "the ester carbonyl carbon (pd_c8) and the C7 methylene bridge (pd_c7) are on the same side of the pd_c4/pd_c5/pd_c6 plane",
        "exo_definition": "the ester carbonyl carbon (pd_c8) and the C7 methylene bridge (pd_c7) are on opposite sides of the pd_c4/pd_c5/pd_c6 plane",
        "stereochemical_signature": "CIP configuration at pd_c5 plus the sign of the signed volume of (pd_c4, pd_c6, pd_c8) about pd_c5; an atom-order permutation must not change either value",
        "distinguishability_requirement": "the two endpoints must differ in stereochemistry, not only in atom order; the atom-id order and element sequence must be identical",
    },
    "paths": [
        {
            "path_id": "path.endo",
            "hypothesis_id": "hypothesis.endo",
            "label": "endo",
            "reaction_file": "reaction-endo.json",
            "reaction_id": endo_reaction["reaction_id"],
            "product_structure_id": "product_endo",
            "product_file": "product-endo.mol",
            "product_xyz": "product-endo.xyz",
            "product_formula": "C9H12O2",
            "product_atom_count": 23,
            "question_id": "question.selectivity.endo.activation_energy.v1",
            "mapping_digest": sha256_bytes(
                json.dumps(endo_reaction["atom_mapping"], sort_keys=True).encode()
            ),
            "mapping_topology_digest": topology_digest,
            "endpoint_digest": file_digests["product-endo.mol"],
            "endpoint_xyz_digest": file_digests["product-endo.xyz"],
            "signature": endo_sig,
        },
        {
            "path_id": "path.exo",
            "hypothesis_id": "hypothesis.exo",
            "label": "exo",
            "reaction_file": "reaction-exo.json",
            "reaction_id": exo_reaction["reaction_id"],
            "product_structure_id": "product_exo",
            "product_file": "product-exo.mol",
            "product_xyz": "product-exo.xyz",
            "product_formula": "C9H12O2",
            "product_atom_count": 23,
            "question_id": "question.selectivity.exo.activation_energy.v1",
            "mapping_digest": sha256_bytes(
                json.dumps(exo_reaction["atom_mapping"], sort_keys=True).encode()
            ),
            "mapping_topology_digest": topology_digest,
            "endpoint_digest": file_digests["product-exo.mol"],
            "endpoint_xyz_digest": file_digests["product-exo.xyz"],
            "signature": exo_sig,
        },
    ],
    "shared_left_side": {
        "structures": ["cyclopentadiene", "methyl_acrylate"],
        "formulas": ["C5H6", "C4H6O2"],
        "atom_count": 23,
        "mapping_topology_digest": topology_digest,
        "frozen_conformation": "one conformation per reactant; cyclopentadiene is rigid; methyl acrylate is frozen in the s-cis conformer (C=C-C=O dihedral 0.0 +/- 5.0 degrees)",
        "methyl_acrylate_dihedral_degrees": ma_dihedral,
    },
    "comparison": {
        "method": "wB97X-D4",
        "basis": "def2-SVP",
        "method_definition": "wB97X-D4/def2-SVP, gas phase, restricted closed-shell singlet; no implicit solvent",
        "charge": 0,
        "multiplicity": 1,
        "electronic_state": {"charge": 0, "multiplicity": 1, "state_id": "state.singlet.s0"},
        "phase": "gas",
        "solvent": None,
        "temperature_k": 298.15,
        "pressure_pa": 100000.0,
        "standard_state": "1_bar_ideal_gas",
        "energy_unit": "hartree",
        "comparison_energy_unit": "kcal_per_mol",
        "reference_state": "separated cyclopentadiene + methyl acrylate at infinite separation, and each declared product at the same conditions",
        "activation_energy_definition": "Delta-E-double-dagger(path) = E(TS(path)) - [E(cyclopentadiene) + E(methyl acrylate)] under the frozen conditions; Delta-G-double-dagger additionally uses the frozen thermochemistry; no tunneling correction",
        "selectivity_observable": "Delta-Delta-E-double-dagger = Delta-E-double-dagger(endo) - Delta-E-double-dagger(exo)",
        "selectivity_preference_rule": "endo if Delta-Delta-E <= -0.5 kcal_per_mol; exo if Delta-Delta-E >= +0.5 kcal_per_mol; no preference otherwise",
        "compatibility_requirements": [
            "same_method",
            "same_electronic_state",
            "same_temperature",
            "same_pressure",
            "same_standard_state",
            "same_reference_state",
            "same_energy_unit",
        ],
    },
    "required_coverage": {
        "declared_paths": ["path.endo", "path.exo"],
        "both_paths_required": True,
        "conformation_coverage": "one frozen conformation per endpoint; no conformer search, MD or Monte-Carlo sampling",
        "methyl_acrylate_conformer": "s-cis only (C=C-C=O dihedral 0.0 +/- 5.0 degrees)",
        "ts_coverage": "one TS candidate per declared path; no automated TS search",
        "required_ts_checks": [
            "stationary-point optimization",
            "exactly one significant imaginary frequency",
            "mapped imaginary mode",
        ],
        "required_path_checks": [
            "bidirectional IRC reaching both frozen endpoints",
            "exact one-to-one endpoint atom mapping",
            "endpoint geometry within the frozen coordinate tolerance",
        ],
        "required_energy_checks": [
            "single-point electronic energy at the frozen method",
            "thermochemistry at 298.15 K and 1 bar for Delta-G-double-dagger when claimed",
            "same method/state/conditions/reference/unit for both paths",
        ],
        "regioisomer_coverage": "only the declared 2-substituted regioisomer; the 3-substituted regioisomer is out of scope",
        "out_of_scope": [
            "other regioisomers",
            "other methyl acrylate conformers",
            "solvent effects",
            "tunneling",
            "experimental rates",
            "complete mechanism",
            "global minimum",
            "general method accuracy",
        ],
    },
    "missing_path_policy": {
        "both_paths_required": True,
        "missing_path_result": "inconclusive",
        "missing_path_rule": "If either declared path lacks a qualified TS, frequency, bidirectional IRC, endpoint mapping, comparable energy or thermochemistry, the selectivity result is inconclusive and no relative preference is stated.",
        "absent_artifact_result": "NOT_VERIFIED",
        "absent_artifact_rule": "If the B23b authorized artifacts or the public (owner, scenario, phase, run_id) reference are absent, the platform evidence state is NOT_VERIFIED, no preference claim is made and H6 remains open.",
        "budget_exhausted_result": "inconclusive",
        "budget_exhausted_rule": "If the frozen budget is exhausted before both paths are qualified, record the gap and report inconclusive; do not start a new hypothesis and never reset the cumulative budget by changing run_id.",
        "no_substitution_rule": "A single-point minimum, a single imaginary frequency or one IRC direction never substitutes for a qualified path.",
        "no_experimental_claim": True,
    },
    "tolerances": {
        "constructed": True,
        "atom_count_tolerance": 0,
        "coordinate_abs_tolerance_angstrom": 0.001,
        "imaginary_mode_threshold_cm1": 0.0,
        "low_frequency_threshold_cm1": 50.0,
        "energy_reparse_abs_hartree": 1e-06,
        "activation_energy_comparison_abs_kcal_per_mol": 0.5,
        "selectivity_preference_abs_kcal_per_mol": 0.5,
        "stereo_configuration_exact": True,
        "stereo_signed_volume_sign_exact": True,
    },
    "budget": {
        "constructed": True,
        "budget_timing_basis": "unmeasured",
        "measured_step_timings": None,
        "timing_evidence": [],
        "max_task_refs": 1,
        "max_candidate_paths": 2,
        "max_ts_candidates_per_path": 1,
        "max_conformers_per_endpoint": 1,
        "max_regioisomers": 1,
        "max_qc_invocations": 14,
        "max_attempts_per_step": 2,
        "max_bsub": 1,
        "science_deadline_seconds": 3000,
        "export_budget_seconds": 600,
        "aggregate_budget_seconds": 3600,
        "queue_wait_counts_toward_total_wall_time": True,
        "in_flight_stop_rule": "after an accepted stop no new step or retry is dispatched; partial artifacts are not accepted as scientific evidence",
        "export_window_rule": "the 600 s export window starts after the last terminal step and never extends the science deadline",
        "cumulative_rule": "the nmr/thermo/selectivity subrows share the B/C cross-domain cumulative budget; A never resets it by changing run_id",
        "h6_open_consequence": "the timing basis is unmeasured and B23b has not executed the selectivity paths; H6 remains open until the B/C budget table and the authorized artifacts exist",
    },
    "file_digests": file_digests,
    "fixture_files": {
        "cyclopentadiene_mol": "cyclopentadiene.mol",
        "cyclopentadiene_xyz": "cyclopentadiene.xyz",
        "methyl_acrylate_mol": "methyl-acrylate.mol",
        "methyl_acrylate_xyz": "methyl-acrylate.xyz",
        "reactants_xyz": "reactants.xyz",
        "product_endo_mol": "product-endo.mol",
        "product_endo_xyz": "product-endo.xyz",
        "product_exo_mol": "product-exo.mol",
        "product_exo_xyz": "product-exo.xyz",
        "reaction_endo": "reaction-endo.json",
        "reaction_exo": "reaction-exo.json",
        "questions": "questions.json",
    },
}
(OUT / "fixture-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print("manifest written")
print("endo signature", json.dumps(endo_sig, indent=2, sort_keys=True))
print("exo signature", json.dumps(exo_sig, indent=2, sort_keys=True))
print("MA dihedral", ma_dihedral)
