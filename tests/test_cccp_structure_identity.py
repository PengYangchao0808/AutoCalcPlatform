"""Molecule identity (chemistry-network B1a) — deterministic chemistry-based key.

Covers the contract in ``ACP_Chemistry_Network_Contract_20261103.md`` §8 together
with the science decisions documented in
:mod:`cccp.analysis.structure.identity`:

* same molecule in three different spellings → one key;
* conformer invariance (different dihedral, same key);
* stereochemistry / charge / multiplicity distinguish otherwise identical graphs;
* cross-representation convergence (SMILES ↔ XYZ ↔ mol block);
* unresolved inputs are results, never exceptions;
* the input-arity contract raises ``ValueError``;
* determinism plus the purity/source guard (no ACP import, no IO, no clock).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rdkit import Chem, rdBase
from rdkit.Chem import AllChem

import cccp.analysis.structure.identity as identity_module
from cccp.analysis.structure.identity import (
    KEY_PREFIX,
    SCHEME,
    UNRESOLVED_REASONS,
    MoleculeIdentity,
    compute_molecule_identity,
    identity_key_payload,
)

#: Ethanol (C2H6O) as a hand-written XYZ block: 4 dp, comment "ethanol A".
ETHANOL_XYZ_A = """9
ethanol A
O 0.0000 0.0000 0.0000
C 1.2000 0.0000 0.0000
C 1.8000 1.3000 0.0000
H 1.6000 -0.5000 0.9000
H 1.6000 -0.5000 -0.9000
H 2.9000 1.2000 0.0000
H 1.5000 1.8000 0.9000
H 1.5000 1.8000 -0.9000
H -0.4000 0.9000 0.0000
"""

#: The same geometry as :data:`ETHANOL_XYZ_A`: reversed atom order, 6 dp,
#: translated by +10 Å in x and carrying a different comment/title line.
ETHANOL_XYZ_B = """9
Ethanol, alternative spelling of the same structure
H 9.600000 0.900000 0.000000
H 11.500000 1.800000 -0.900000
H 11.500000 1.800000 0.900000
H 12.900000 1.200000 0.000000
H 11.600000 -0.500000 -0.900000
H 11.600000 -0.500000 0.900000
C 11.800000 1.300000 0.000000
C 11.200000 0.000000 0.000000
O 10.000000 0.000000 0.000000
"""

#: (R)- and (S)-lactic acid as SMILES (3D geometries are embedded below).
LACTIC_ACID_R = "C[C@H](O)C(=O)O"
LACTIC_ACID_S = "C[C@@H](O)C(=O)O"

#: Source substrings forbidden in the identity module (purity/source guard).
FORBIDDEN_SOURCE_SUBSTRINGS = (
    "import acp",
    "from acp",
    "acp_services",
    "open(",
    "requests",
    "time.",
    "random",
)


def _embedded_xyz(smiles: str, seed: int) -> str:
    """Embed *smiles* with a fixed seed and return its XYZ block (deterministic)."""
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0, smiles
    AllChem.MMFFOptimizeMolecule(mol)
    return Chem.MolToXYZBlock(mol)


def test_key_format_and_scheme() -> None:
    identity = compute_molecule_identity(smiles="CCO")
    assert identity.scheme == SCHEME
    assert identity.resolved is True
    assert identity.key is not None
    assert identity.key.startswith(KEY_PREFIX)
    digest = identity.key[len(KEY_PREFIX) :]
    assert len(digest) == 24
    assert all(char in "0123456789abcdef" for char in digest)


def test_key_payload_is_exactly_the_documented_four_fields() -> None:
    identity = compute_molecule_identity(smiles="CCO")
    payload = identity_key_payload(identity)
    assert set(payload) == {"scheme", "canonical_smiles", "charge", "multiplicity"}
    assert payload == {
        "scheme": SCHEME,
        "canonical_smiles": "CCO",
        "charge": 0,
        "multiplicity": 1,
    }
    # The published key is reproducible from the payload alone (canonical JSON).
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert identity.key == KEY_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def test_same_molecule_in_three_spellings_shares_one_key() -> None:
    round_tripped = Chem.MolToXYZBlock(Chem.MolFromXYZBlock(ETHANOL_XYZ_A))
    identities = [
        compute_molecule_identity(xyz_text=ETHANOL_XYZ_A),
        compute_molecule_identity(xyz_text=ETHANOL_XYZ_B),
        compute_molecule_identity(xyz_text=round_tripped),
    ]
    assert {item.key for item in identities} == {compute_molecule_identity(smiles="CCO").key}
    assert all(item.canonical_smiles == "CCO" for item in identities)
    # An explicit-H geometry must not leak "[H]" atoms into the canonical SMILES.
    assert all("[H]" not in item.canonical_smiles for item in identities)


def test_conformers_of_the_same_molecule_share_one_key() -> None:
    gauche = _embedded_xyz("CCO", seed=42)
    anti = _embedded_xyz("CCO", seed=7)
    assert gauche != anti, "the two embedded geometries should differ"
    first = compute_molecule_identity(xyz_text=gauche)
    second = compute_molecule_identity(xyz_text=anti)
    assert first.key == second.key
    assert first.key == compute_molecule_identity(smiles="CCO").key


def test_stereochemistry_distinguishes_enantiomers_from_geometry() -> None:
    r_identity = compute_molecule_identity(xyz_text=_embedded_xyz(LACTIC_ACID_R, seed=42))
    s_identity = compute_molecule_identity(xyz_text=_embedded_xyz(LACTIC_ACID_S, seed=42))
    assert r_identity.key is not None
    assert s_identity.key is not None
    assert r_identity.key != s_identity.key
    assert r_identity.canonical_smiles != s_identity.canonical_smiles
    assert "stereo_from_geometry" in r_identity.warnings
    assert "stereo_from_geometry" in s_identity.warnings


def test_stereochemistry_also_distinguishes_on_the_smiles_path() -> None:
    r_identity = compute_molecule_identity(smiles=LACTIC_ACID_R)
    s_identity = compute_molecule_identity(smiles=LACTIC_ACID_S)
    assert r_identity.key != s_identity.key
    # Isomeric SMILES keep the chiral tag; the geometry warning does not apply.
    assert "@" in r_identity.canonical_smiles or "@" in s_identity.canonical_smiles
    assert "stereo_from_geometry" not in r_identity.warnings


def test_stereo_ignored_collapses_enantiomers_to_one_key() -> None:
    r_identity = compute_molecule_identity(smiles=LACTIC_ACID_R, stereo_from_geometry=False)
    s_identity = compute_molecule_identity(smiles=LACTIC_ACID_S, stereo_from_geometry=False)
    assert r_identity.canonical_smiles == s_identity.canonical_smiles
    assert r_identity.key == s_identity.key
    assert "stereo_ignored" in r_identity.warnings


def test_charge_distinguishes_neutral_and_anionic_forms() -> None:
    neutral = compute_molecule_identity(smiles="CC(=O)O", charge=0)
    anion = compute_molecule_identity(smiles="CC(=O)[O-]", charge=-1)
    assert neutral.key is not None and anion.key is not None
    assert neutral.key != anion.key
    assert neutral.canonical_smiles != anion.canonical_smiles
    assert identity_key_payload(neutral)["charge"] == 0
    assert identity_key_payload(anion)["charge"] == -1


def test_charge_alone_changes_the_key() -> None:
    neutral = compute_molecule_identity(smiles="CCO", charge=0)
    charged = compute_molecule_identity(smiles="CCO", charge=-1)
    assert neutral.canonical_smiles == charged.canonical_smiles
    assert neutral.key != charged.key


def test_multiplicity_distinguishes_and_is_flagged_as_unused() -> None:
    singlet = compute_molecule_identity(smiles="CCO", multiplicity=1)
    triplet = compute_molecule_identity(smiles="CCO", multiplicity=3)
    assert singlet.canonical_smiles == triplet.canonical_smiles
    assert singlet.key != triplet.key
    assert identity_key_payload(triplet)["multiplicity"] == 3
    assert "multiplicity_not_used_by_bond_perception" in triplet.warnings
    assert "multiplicity_not_used_by_bond_perception" not in singlet.warnings


def test_cross_representation_smiles_xyz_molblock_share_one_key() -> None:
    from_smiles = compute_molecule_identity(smiles="CCO")
    from_xyz = compute_molecule_identity(xyz_text=ETHANOL_XYZ_A)
    from_mol_block = compute_molecule_identity(
        mol_block=Chem.MolToMolBlock(Chem.MolFromSmiles("CCO"))
    )
    assert from_smiles.key == from_xyz.key == from_mol_block.key
    assert from_smiles.canonical_smiles == from_mol_block.canonical_smiles


def test_unresolved_garbage_xyz_returns_a_reason_instead_of_raising() -> None:
    with rdBase.BlockLogs():
        identity = compute_molecule_identity(xyz_text="this is not an xyz block")
    assert isinstance(identity, MoleculeIdentity)
    assert identity.key is None
    assert identity.resolved is False
    assert identity.canonical_smiles == ""
    assert identity.formula == ""
    assert identity.inchikey is None
    assert identity.warnings[0] == "xyz_parse_failed"
    assert identity.warnings[0] in UNRESOLVED_REASONS


def test_unresolved_garbage_smiles_returns_a_reason_instead_of_raising() -> None:
    with rdBase.BlockLogs():
        identity = compute_molecule_identity(smiles="C1CC")
    assert identity.key is None
    assert identity.canonical_smiles == ""
    assert identity.warnings[0] == "smiles_parse_failed"
    assert identity.warnings[0] in UNRESOLVED_REASONS


def test_unresolved_garbage_mol_block_returns_a_reason_instead_of_raising() -> None:
    with rdBase.BlockLogs():
        identity = compute_molecule_identity(mol_block="not a mol block")
    assert identity.key is None
    assert identity.warnings[0] == "mol_block_parse_failed"
    assert identity.warnings[0] in UNRESOLVED_REASONS


def test_bondless_geometry_is_reported_as_no_bonds_detected() -> None:
    # A single argon atom perceives successfully but yields zero bonds.
    identity = compute_molecule_identity(xyz_text="1\nargon\nAr 0.0000 0.0000 0.0000\n")
    assert identity.key is None
    assert identity.warnings[0] == "no_bonds_detected"
    assert identity.warnings[0] in UNRESOLVED_REASONS


def test_charged_geometry_without_the_right_charge_is_charge_required() -> None:
    # Acetate embedded in 3D, perceived with charge=0: RDKit reports that the
    # input charge does not match the fragments, so the identity refuses to guess.
    xyz_text = _embedded_xyz("CC(=O)[O-]", seed=7)
    identity = compute_molecule_identity(xyz_text=xyz_text, charge=0)
    assert identity.key is None
    assert identity.warnings[0] == "charge_required"
    # With the correct charge the same geometry resolves and keys differently
    # from the neutral acid (charge is part of the payload).
    resolved = compute_molecule_identity(xyz_text=xyz_text, charge=-1)
    assert resolved.key is not None
    assert resolved.key != compute_molecule_identity(smiles="CC(=O)O", charge=0).key


def test_input_arity_contract_raises_value_error() -> None:
    for kwargs in ({}, {"xyz_text": ETHANOL_XYZ_A, "smiles": "CCO"}):
        try:
            compute_molecule_identity(**kwargs)
        except ValueError as exc:
            assert "exactly one of" in str(exc)
        else:  # pragma: no cover - failure path
            raise AssertionError(f"expected ValueError for {kwargs or 'no inputs'}")


def test_identity_is_deterministic_across_calls() -> None:
    first = compute_molecule_identity(xyz_text=ETHANOL_XYZ_A)
    second = compute_molecule_identity(xyz_text=ETHANOL_XYZ_A)
    assert first == second
    assert identity_key_payload(first) == identity_key_payload(second)
    # Repeating a stereo-assigning call must not drift the key either.
    for _ in range(2):
        assert compute_molecule_identity(xyz_text=_embedded_xyz("CCO", seed=42)).key == first.key


def test_identity_dataclass_is_frozen_with_tuple_warnings() -> None:
    identity = compute_molecule_identity(smiles="CCO")
    assert isinstance(identity.warnings, tuple)
    try:
        identity.key = "tampered"  # type: ignore[misc]
    except AttributeError:  # FrozenInstanceError subclasses AttributeError
        pass
    else:  # pragma: no cover - failure path
        raise AssertionError("MoleculeIdentity must be a frozen dataclass")
    assert identity.key is not None


def test_module_source_has_no_acp_import_io_clock_or_entropy() -> None:
    source = Path(identity_module.__file__).read_text(encoding="utf-8")
    for banned in FORBIDDEN_SOURCE_SUBSTRINGS:
        assert banned not in source, banned
    assert "from __future__ import annotations" in source
    assert SCHEME in source and KEY_PREFIX in source
