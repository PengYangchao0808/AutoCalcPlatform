"""
NMR Topology Capture
====================

Bonded-RDKit-Mol capture for one input source, preserving the source atom
order and the provenance of the graph (gap G01).

Relocated verbatim from ``acp.io.structures`` by plan todo 17 (C03-s2); the
historical ``acp.io.structures`` path re-exports these exact objects for
compatibility.  This module never imports ``acp`` — the mol-block
normalizer is imported lazily from :mod:`cccp.analysis.structure.parsers`.

Author: QCcalc Team
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from rdkit import Chem


# ---------------------------------------------------------------------------
# NMR topology capture (gap G01): bonded Mol + provenance per input source
# ---------------------------------------------------------------------------

NMR_TOPOLOGY_SMILES: Final = "smiles"
NMR_TOPOLOGY_SDF: Final = "sdf"
NMR_TOPOLOGY_XYZ_INFERRED: Final = "xyz_inferred"
NMR_TOPOLOGY_XYZ_UNAVAILABLE: Final = "xyz_unavailable"
NMR_TOPOLOGY_SOURCES: Final = (
    NMR_TOPOLOGY_SMILES,
    NMR_TOPOLOGY_SDF,
    NMR_TOPOLOGY_XYZ_INFERRED,
    NMR_TOPOLOGY_XYZ_UNAVAILABLE,
)
"""Allowed ``nmr_topology_source`` values (closed set, gap G01).

``xyz_unavailable`` is the generic "no bonded graph" bucket: XYZ without an
explicit charge, failed bond determination, or any other bond-less input
format (GJF/log/INP/unknown).
"""

_XYZ_CHARGE_RE = re.compile(r"charge\s*=\s*(-?\d+)", re.IGNORECASE)
_XYZ_MULT_RE = re.compile(r"mult(?:i(?:plicity)?)?\s*=\s*(\d+)", re.IGNORECASE)
_STRUCTURE_LIKE_SUFFIXES: Final = frozenset(
    {".sdf", ".sd", ".mol", ".xyz", ".gjf", ".com", ".log", ".out", ".inp"}
)


class TopologyUnavailableError(ValueError):
    """Typed error: no bonded molecular graph for a candidate in strict mode."""


@dataclass(frozen=True)
class NmrTopologyCapture:
    """Bonded-Mol capture for one input source (see :func:`capture_nmr_topology`)."""

    topology_source: str
    """One of :data:`NMR_TOPOLOGY_SOURCES`."""
    mol: Chem.Mol | None
    """The bonded RDKit Mol in source atom order, or ``None`` when unavailable."""
    reason: str | None = None
    """Machine-readable explanation when *mol* is ``None``."""

    @property
    def available(self) -> bool:
        """True when a bonded Mol was captured."""
        return self.mol is not None


def _xyz_unavailable(reason: str) -> NmrTopologyCapture:
    return NmrTopologyCapture(NMR_TOPOLOGY_XYZ_UNAVAILABLE, None, reason)


def _looks_like_xyz_atom_line(line: str) -> bool:
    parts = line.split()
    if len(parts) < 4:
        return False
    try:
        float(parts[1])
        float(parts[2])
        float(parts[3])
    except ValueError:
        return False
    return True


def _capture_smiles_topology(text: str) -> NmrTopologyCapture:
    from rdkit import Chem

    stripped = text.strip()
    if not stripped:
        return _xyz_unavailable("empty input source")
    try:
        mol = Chem.MolFromSmiles(stripped)
        if mol is None:
            return _xyz_unavailable("not a valid SMILES and no structure file found")
        mol = Chem.AddHs(mol)
        Chem.SanitizeMol(mol)
    except (ValueError, RuntimeError) as exc:
        return _xyz_unavailable(f"SMILES parse failed: {exc}")
    if mol.GetNumAtoms() == 0:
        return _xyz_unavailable("SMILES parsed to an empty molecule")
    return NmrTopologyCapture(NMR_TOPOLOGY_SMILES, mol, None)


def _capture_molblock_topology(content: str) -> NmrTopologyCapture:
    from rdkit import Chem

    from cccp.analysis.structure.parsers import normalize_molblock

    raw = content.split("$$$$", 1)[0] if "$$$$" in content else content
    block = normalize_molblock(raw.rstrip())
    if "M  END" not in block:
        return _xyz_unavailable("input carries no M  END mol block")
    try:
        mol = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False)
    except (ValueError, RuntimeError) as exc:
        return _xyz_unavailable(f"mol block parse failed: {exc}")
    if mol is None or mol.GetNumAtoms() == 0:
        return _xyz_unavailable("RDKit failed to parse the mol block")
    return NmrTopologyCapture(NMR_TOPOLOGY_SDF, mol, None)


def _capture_xyz_topology(
    path: Path,
    *,
    charge: int | None,
    multiplicity: int | None,
) -> NmrTopologyCapture:
    """Infer XYZ topology via ``rdDetermineBonds.DetermineBonds``.

    Bond determination runs ONLY with an explicit charge (argument or
    ``charge=`` comment line) and never for open-shell input: the RDKit
    ``DetermineBonds`` API has no multiplicity argument, so multiplicity > 1
    bond orders would have to be guessed.
    """
    from rdkit import Chem

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _xyz_unavailable(f"cannot read {path}: {exc}")
    lines = content.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        return _xyz_unavailable("XYZ file is empty")
    try:
        n_atoms = int(lines[start].strip())
    except ValueError:
        return _xyz_unavailable("XYZ first line is not an atom count")
    if n_atoms < 1:
        return _xyz_unavailable("XYZ atom count must be positive")
    comment = ""
    atom_start = start + 1
    if atom_start < len(lines) and not _looks_like_xyz_atom_line(lines[atom_start]):
        comment = lines[atom_start]
        atom_start += 1
    if atom_start + n_atoms > len(lines):
        return _xyz_unavailable("XYZ file truncated (declared atom count exceeds content)")

    effective_charge = charge
    if effective_charge is None:
        cm = _XYZ_CHARGE_RE.search(comment)
        if cm:
            effective_charge = int(cm.group(1))
    effective_mult = multiplicity
    if effective_mult is None:
        mm = _XYZ_MULT_RE.search(comment)
        if mm:
            effective_mult = int(mm.group(1))
    if effective_charge is None:
        return _xyz_unavailable(
            "XYZ topology requires an explicit charge (pass charge=... or write "
            "'charge=N' in the comment line); bond determination is never guessed"
        )
    if effective_mult is not None and effective_mult > 1:
        return _xyz_unavailable(
            f"multiplicity={effective_mult}: rdDetermineBonds has no multiplicity "
            "argument; open-shell XYZ bond orders are never guessed"
        )

    block = "\n".join([lines[start], comment, *lines[atom_start : atom_start + n_atoms]]) + "\n"
    try:
        xyz_mol = Chem.MolFromXYZBlock(block)
    except (ValueError, RuntimeError) as exc:
        return _xyz_unavailable(f"XYZ parse failed: {exc}")
    if xyz_mol is None:
        return _xyz_unavailable("RDKit failed to parse the XYZ block")
    try:
        from rdkit.Chem import rdDetermineBonds
    except ImportError as exc:  # pragma: no cover - RDKit build without the module
        return _xyz_unavailable(f"rdDetermineBonds unavailable in this RDKit build: {exc}")
    try:
        rdDetermineBonds.DetermineBonds(xyz_mol, charge=int(effective_charge))
    except (ValueError, RuntimeError) as exc:
        return _xyz_unavailable(f"rdDetermineBonds.DetermineBonds failed: {exc}")
    if xyz_mol.GetNumAtoms() == 0:
        return _xyz_unavailable("bond determination produced an empty molecule")
    return NmrTopologyCapture(NMR_TOPOLOGY_XYZ_INFERRED, xyz_mol, None)


def capture_nmr_topology(
    source: str | Path,
    *,
    charge: int | None = None,
    multiplicity: int | None = None,
) -> NmrTopologyCapture:
    """Capture a bonded RDKit Mol for one input source (gap G01).

    Dispatch:

    * SMILES literal → ``MolFromSmiles`` + ``AddHs`` (explicit H in source
      order) → ``"smiles"``;
    * SDF/MOL file path or inline mol block → ``MolFromMolBlock`` with
      ``removeHs=False`` (order preserved) → ``"sdf"``;
    * XYZ file → ``rdDetermineBonds.DetermineBonds`` with an explicit charge
      (argument or ``charge=`` comment) → ``"xyz_inferred"``; without an
      explicit charge / on multiplicity > 1 / when determination fails →
      ``"xyz_unavailable"``;
    * any other bond-less format → ``"xyz_unavailable"``.

    Never raises and never fabricates a fallback (element-merged) graph:
    failures come back as ``mol=None`` plus a ``reason``.

    Args:
        source: SMILES string, structure file path, or inline mol block.
        charge: Explicit molecular charge (overrides the XYZ comment line).
        multiplicity: Explicit spin multiplicity (overrides the XYZ comment).

    Returns:
        The capture — ``mol`` is ``None`` iff topology is unavailable.
    """
    text = str(source)
    path = Path(text)
    try:
        if "M  END" in text and ("\n" in text or "\r" in text):
            return _capture_molblock_topology(text)
        is_file = path.is_file()
    except (OSError, ValueError):
        is_file = False
    if is_file:
        suffix = path.suffix.lower()
        if suffix in {".sdf", ".sd", ".mol"}:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return _xyz_unavailable(f"cannot read {path}: {exc}")
            return _capture_molblock_topology(content)
        if suffix == ".xyz":
            return _capture_xyz_topology(path, charge=charge, multiplicity=multiplicity)
        return _xyz_unavailable(f"input format {suffix or 'unknown'!r} carries no bond table")
    if path.suffix.lower() in _STRUCTURE_LIKE_SUFFIXES:
        return _xyz_unavailable(f"input file not found: {text}")
    return _capture_smiles_topology(text)


__all__ = [
    "NMR_TOPOLOGY_SDF",
    "NMR_TOPOLOGY_SMILES",
    "NMR_TOPOLOGY_SOURCES",
    "NMR_TOPOLOGY_XYZ_INFERRED",
    "NMR_TOPOLOGY_XYZ_UNAVAILABLE",
    "NmrTopologyCapture",
    "TopologyUnavailableError",
    "capture_nmr_topology",
]
