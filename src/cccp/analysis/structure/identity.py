"""Molecule identity (chemistry-network B1a) — deterministic chemical identity key.

ACP historically identified a structure by *location* (``source_uid``), by *file
bytes* (``content_checksum``) or by *display name* (``molecule_key``), so the same
molecule saved twice counts as two nodes.  This module adds a chemistry-based
identity independent of geometry, file layout and naming.

Science decisions encoded here (do not weaken without a contract change):

1. **Key definition.**  ``key = "mol_" + sha256(canonical_json)[:24]`` where the
   canonical payload is exactly ``{"scheme": SCHEME, "canonical_smiles": ...,
   "charge": int, "multiplicity": int}`` serialised with
   ``json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)``.
   Coordinates, file names, titles and InChIKeys are *never* key inputs, so
   conformers of one molecule share a key.
2. **Stereo.**  With a 3D geometry and ``stereo_from_geometry=True`` the
   perceived graph gets ``Chem.AssignStereochemistryFrom3D`` and isomeric SMILES
   (warning ``stereo_from_geometry``); otherwise non-isomeric SMILES are used and
   ``stereo_ignored`` is recorded.  Assignment is deterministic.
3. **Hydrogens are stripped before the canonical SMILES** so an explicit-H 3D
   geometry and its hydrogen-suppressed SMILES spelling converge on one key
   (``hydrogen_strip_failed`` is recorded if RDKit refuses to strip).
4. **Bond perception.**  XYZ input goes through ``Chem.MolFromXYZBlock`` then
   ``rdDetermineBonds.DetermineBonds(charge=...)``.  A charge-mismatch failure
   (RDKit reports the input charge does not match the perceived fragments) is
   *never* guessed around: the result is unresolved with ``charge_required``.
   Other failures, or a perception with zero bonds, retry once with
   ``DetermineConnectivity`` (warning ``connectivity_only``); a bond-less graph
   after that is ``no_bonds_detected``.  ``multiplicity`` is not consumed by
   RDKit; it is carried into the key and flagged with
   ``multiplicity_not_used_by_bond_perception`` when ``!= 1``.
5. **Unresolved is a first-class result.**  Never an exception: ``key=None``,
   ``canonical_smiles=""``, and a reason from :data:`UNRESOLVED_REASONS`; only
   the *input-arity* contract violation raises ``ValueError`` (a programming
   error, not a chemistry result).  ``formula``/``inchikey`` are metadata: their
   failure only adds ``formula_unavailable`` / ``inchikey_unavailable`` and never
   blocks a successful SMILES.  The InChIKey is never part of the key.
6. **Purity.**  No IO, network, clock or entropy source and no global mutable
   state: identical inputs give identical output.  This module imports only the
   standard library and RDKit (never the workflow or service packages).

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Final

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.rdDetermineBonds import DetermineBonds, DetermineConnectivity

logger = logging.getLogger(__name__)

SCHEME: Final = "mol_identity_v1"
"""Identity scheme tag; part of the key payload, so bumping it re-keys everything."""

KEY_PREFIX: Final = "mol_"
"""Prefix of every resolved key; unresolved identities carry ``key=None``."""

_KEY_HEX_LEN: Final = 24

UNRESOLVED_REASONS: Final[tuple[str, ...]] = (
    "xyz_parse_failed",
    "bond_perception_failed",
    "smiles_parse_failed",
    "mol_block_parse_failed",
    "no_bonds_detected",
    "charge_required",
)
"""Closed set of unresolved reasons (always the first entry of ``warnings``)."""

_SOURCE_FAILURE_REASON: Final = {
    "xyz_text": "bond_perception_failed",
    "smiles": "smiles_parse_failed",
    "mol_block": "mol_block_parse_failed",
}
_RDKIT_ERRORS: Final = (ValueError, RuntimeError)


@dataclass(frozen=True)
class MoleculeIdentity:
    """Chemistry-based identity of one molecule (see :func:`compute_molecule_identity`).

    ``scheme`` is always :data:`SCHEME`; ``key`` is ``"mol_" + 24 hex`` or ``None``
    when unresolved; ``canonical_smiles`` is isomeric unless stereo was ignored
    (``""`` when unresolved); ``charge``/``multiplicity`` are carried into the key;
    ``formula`` (``""`` when unavailable) and ``inchikey`` are metadata only;
    ``warnings`` holds reason codes with the unresolved reason first.
    """

    scheme: str
    key: str | None
    canonical_smiles: str
    charge: int
    multiplicity: int
    formula: str
    inchikey: str | None
    warnings: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        """Whether a chemical key could be computed."""
        return self.key is not None


def identity_key_payload(identity: MoleculeIdentity) -> dict[str, Any]:
    """Return the exact canonical payload hashed into ``identity.key``.

    Args:
        identity: Identity produced by :func:`compute_molecule_identity`.

    Returns:
        Only ``scheme``, ``canonical_smiles``, ``charge`` and ``multiplicity`` —
        the four fields allowed to influence the key (nothing else may).
    """
    return {
        "scheme": identity.scheme,
        "canonical_smiles": identity.canonical_smiles,
        "charge": identity.charge,
        "multiplicity": identity.multiplicity,
    }


def _key_from_payload(payload: dict[str, Any]) -> str:
    """Hash a key payload into ``"mol_" + 24 hex``."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return KEY_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()[:_KEY_HEX_LEN]


def _dedupe(warnings: list[str]) -> tuple[str, ...]:
    """Order-preserving de-duplication of warning codes."""
    return tuple(dict.fromkeys(warnings))


def _unresolved(
    *, charge: int, multiplicity: int, reason: str, warnings: list[str]
) -> MoleculeIdentity:
    """Build the first-class "no identity" result naming *reason*."""
    return MoleculeIdentity(
        scheme=SCHEME,
        key=None,
        canonical_smiles="",
        charge=charge,
        multiplicity=multiplicity,
        formula="",
        inchikey=None,
        warnings=_dedupe([reason, *warnings]),
    )


def _connectivity_only(xyz_text: str, warnings: list[str]) -> tuple[Chem.Mol | None, str | None]:
    """Single fallback: connectivity-only graph (no bond orders, no charges)."""
    mol = Chem.MolFromXYZBlock(xyz_text)
    if mol is not None:
        try:
            DetermineConnectivity(mol)
        except _RDKIT_ERRORS as exc:
            logger.debug("DetermineConnectivity failed: %s", exc)
            mol = None
    if mol is None or mol.GetNumBonds() == 0:
        return None, "no_bonds_detected"
    warnings.append("connectivity_only")
    return mol, None


def _perceive_xyz(
    xyz_text: str, *, charge: int, warnings: list[str]
) -> tuple[Chem.Mol | None, str | None]:
    """Perceive bonds for an XYZ block.

    Returns ``(mol, reason)``: *mol* is ``None`` when unresolved and *reason* then
    names a closed-set reason.  ``connectivity_only`` is appended to *warnings*
    when the connectivity fallback produced the graph.
    """
    mol = Chem.MolFromXYZBlock(xyz_text)
    if mol is None or mol.GetNumAtoms() == 0:
        return None, "xyz_parse_failed"
    try:
        DetermineBonds(mol, charge=charge)
    except _RDKIT_ERRORS as exc:
        if "charge" in str(exc).lower():
            # Charge is under-determined; guessing would fabricate a protonation
            # state, so the honest answer is "unresolved, tell me the charge".
            logger.debug("xyz bond perception needs an explicit charge: %s", exc)
            return None, "charge_required"
        logger.debug("DetermineBonds failed, retrying connectivity-only: %s", exc)
        return _connectivity_only(xyz_text, warnings)
    if mol.GetNumBonds() > 0:
        return mol, None
    return _connectivity_only(xyz_text, warnings)


def _has_3d_geometry(mol: Chem.Mol) -> bool:
    """Whether *mol* carries a 3D conformer (i.e. usable stereo geometry)."""
    if mol.GetNumConformers() == 0:
        return False
    try:
        return bool(mol.GetConformer().Is3D())
    except _RDKIT_ERRORS:  # pragma: no cover - defensive RDKit conformer edge
        return False


def _strip_hydrogens(mol: Chem.Mol, warnings: list[str]) -> Chem.Mol:
    """Return the heavy-atom graph so explicit-H geometries share a SMILES key."""
    try:
        return Chem.RemoveHs(mol)
    except _RDKIT_ERRORS as exc:
        logger.debug("hydrogen removal failed, keeping explicit hydrogens: %s", exc)
        warnings.append("hydrogen_strip_failed")
        return mol


def compute_molecule_identity(
    *,
    xyz_text: str | None = None,
    smiles: str | None = None,
    mol_block: str | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    stereo_from_geometry: bool = True,
) -> MoleculeIdentity:
    """Compute the deterministic chemistry-based identity of one molecule.

    Args:
        xyz_text: Single-frame XYZ block; bonds are perceived from geometry.
        smiles: SMILES string (stereo is whatever the SMILES encodes).
        mol_block: MDL mol block; 3D blocks get geometry stereo assignment.
        charge: Total charge, passed to bond perception and carried into the key.
        multiplicity: Spin multiplicity, carried into the key (RDKit does not
            consume it; a warning records that).
        stereo_from_geometry: Assign stereo from 3D coordinates and use isomeric
            SMILES when ``True``; ignore stereo entirely when ``False``.

    Returns:
        :class:`MoleculeIdentity`; unresolved input yields ``key is None`` and a
        reason from :data:`UNRESOLVED_REASONS` instead of an exception.

    Raises:
        ValueError: If not exactly one of *xyz_text*, *smiles*, *mol_block* is
            given — an input-arity contract violation, not a chemistry result.
    """
    provided = [
        name
        for name, value in (
            ("xyz_text", xyz_text),
            ("smiles", smiles),
            ("mol_block", mol_block),
        )
        if value is not None
    ]
    if len(provided) != 1:
        raise ValueError(
            "compute_molecule_identity requires exactly one of "
            f"'xyz_text', 'smiles', 'mol_block'; got {provided or ['none']}"
        )
    source = provided[0]

    warnings: list[str] = []
    if multiplicity != 1:
        warnings.append("multiplicity_not_used_by_bond_perception")

    if source == "xyz_text":
        mol, reason = _perceive_xyz(xyz_text or "", charge=charge, warnings=warnings)
    elif source == "smiles":
        mol = Chem.MolFromSmiles(smiles or "")
        reason = None if mol is not None else "smiles_parse_failed"
    else:
        mol = Chem.MolFromMolBlock(mol_block or "")
        reason = None if mol is not None else "mol_block_parse_failed"
    if mol is None:
        return _unresolved(
            charge=charge,
            multiplicity=multiplicity,
            reason=reason or "bond_perception_failed",
            warnings=warnings,
        )

    isomeric = True
    if not stereo_from_geometry:
        isomeric = False
        warnings.append("stereo_ignored")
    elif _has_3d_geometry(mol):
        Chem.AssignStereochemistryFrom3D(mol)
        warnings.append("stereo_from_geometry")

    heavy = _strip_hydrogens(mol, warnings)
    try:
        canonical_smiles = Chem.MolToSmiles(heavy, isomericSmiles=isomeric)
    except _RDKIT_ERRORS as exc:
        logger.debug("canonical SMILES generation failed: %s", exc)
        return _unresolved(
            charge=charge,
            multiplicity=multiplicity,
            reason=_SOURCE_FAILURE_REASON[source],
            warnings=warnings,
        )

    formula = ""
    try:
        formula = rdMolDescriptors.CalcMolFormula(heavy)
    except _RDKIT_ERRORS as exc:
        logger.debug("molecular formula unavailable: %s", exc)
        warnings.append("formula_unavailable")

    inchikey: str | None = None
    to_inchi_key = getattr(Chem, "MolToInchiKey", None)
    if to_inchi_key is None:
        warnings.append("inchikey_unavailable")
    else:
        try:
            inchikey = to_inchi_key(heavy) or None
        except _RDKIT_ERRORS as exc:
            logger.debug("InChIKey unavailable: %s", exc)
        if inchikey is None:
            warnings.append("inchikey_unavailable")

    payload = {
        "scheme": SCHEME,
        "canonical_smiles": canonical_smiles,
        "charge": charge,
        "multiplicity": multiplicity,
    }
    return MoleculeIdentity(
        scheme=SCHEME,
        key=_key_from_payload(payload),
        canonical_smiles=canonical_smiles,
        charge=charge,
        multiplicity=multiplicity,
        formula=formula,
        inchikey=inchikey,
        warnings=_dedupe(warnings),
    )


__all__ = [
    "KEY_PREFIX",
    "SCHEME",
    "UNRESOLVED_REASONS",
    "MoleculeIdentity",
    "compute_molecule_identity",
    "identity_key_payload",
]
