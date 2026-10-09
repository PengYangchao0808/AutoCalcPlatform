"""Molecular structure readers and writers.

Delegates to cccp.io.input_handler for actual parsing.
This module provides the new public API as a thin wrapper, plus the NMR
topology capture (gap G01) that preserves a bonded molecular graph with
its provenance for every input source.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

from acp.core.models import Structure

if TYPE_CHECKING:
    from rdkit import Chem


class InputFormat(Enum):
    """Supported input formats for molecular structures."""

    SMILES = auto()
    XYZ = auto()
    GJF = auto()
    LOG = auto()
    OUT = auto()
    UNKNOWN = auto()


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

    from acp.intake.parsers import normalize_molblock

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


class StructureReader:
    """Read molecular structures from SMILES strings or structure files.

    Delegates parsing to the existing MolecularInputHandler from
    cccp for format detection and coordinate extraction.
    """

    def read(
        self,
        source: str | Path,
        charge: int | None = None,
        multiplicity: int | None = None,
        name: str | None = None,
    ) -> Structure:
        """Auto-detect format and return a Structure.

        Args:
            source: SMILES string or path to a structure file. SDF/MOL inputs
                (file path or inline mol block) are parsed via
                ``acp.intake.parsers`` with atom order preserved.
            charge: Molecular charge (overrides auto-detected value).
            multiplicity: Spin multiplicity (overrides auto-detected value).
            name: Optional molecule name forwarded to the parser; when omitted
                the parser derives it from the file stem.

        Returns:
            Structure instance with parsed coordinates and metadata.
        """
        text = str(source)
        if "\n" in text and "M  END" in text:
            return self._read_molblock(
                text, path=None, charge=charge, multiplicity=multiplicity, name=name
            )
        path = Path(text)
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file and path.suffix.lower() in {".sdf", ".sd", ".mol"}:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise ValueError(f"cannot read structure file {path}: {exc}") from exc
            return self._read_molblock(
                content, path=path, charge=charge, multiplicity=multiplicity, name=name
            )

        from cccp.io.input_handler import MolecularInputHandler

        result = MolecularInputHandler.from_source(
            source,
            name=name,
            charge=charge,
            multiplicity=multiplicity,
        )

        return Structure(
            id=result.name,
            charge=result.charge,
            multiplicity=result.multiplicity,
            symbols=list(result.symbols),
            coordinates=(
                result.coordinates.copy()
                if result.coordinates is not None
                else None
            ),
            metadata={
                "source_format": str(result.source_format),
                "source_path": (
                    str(result.source_path) if result.source_path else None
                ),
                **result.metadata,
            },
        )

    @staticmethod
    def _read_molblock(
        content: str,
        *,
        path: Path | None,
        charge: int | None,
        multiplicity: int | None,
        name: str | None,
    ) -> Structure:
        """Build a Structure from SDF/MOL content (first record, order preserved)."""
        from acp.intake.parsers import parse_structure_text

        if path is not None:
            fmt = "mol" if path.suffix.lower() == ".mol" else "sdf"
            filename = path.name
        else:
            fmt = "sdf" if "$$$$" in content else "mol"
            filename = (name or "input") + (".sdf" if fmt == "sdf" else ".mol")
        result = parse_structure_text(content, fmt, filename)
        if not result.structures:
            errors = "; ".join(result.errors) or "unknown parse error"
            raise ValueError(f"failed to parse {fmt.upper()} input: {errors}")
        asset = result.structures[0]
        if not asset.xyz:
            raise ValueError(f"{fmt.upper()} input has no 3D coordinates")
        lines = asset.xyz.splitlines()
        n_atoms = int(lines[0])
        symbols: list[str] = []
        coords: list[tuple[float, float, float]] = []
        for row in lines[2 : 2 + n_atoms]:
            parts = row.split()
            symbols.append(parts[0])
            coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
        resolved_name = name or asset.name or (path.stem if path is not None else "molecule")
        metadata: dict[str, object] = {
            "source_format": fmt,
            "source_path": str(path) if path is not None else None,
        }
        if asset.smiles:
            metadata["smiles"] = asset.smiles
        return Structure(
            id=resolved_name,
            charge=charge if charge is not None else int(asset.charge),
            multiplicity=multiplicity if multiplicity is not None else int(asset.multiplicity),
            symbols=symbols,
            coordinates=np.asarray(coords, dtype=float),
            metadata=metadata,
        )

    def read_to_ensemble(self, sources: list[str | Path]) -> list[Structure]:
        """Read multiple sources into a list of Structures.

        Args:
            sources: List of SMILES strings or file paths.

        Returns:
            List of Structure instances.
        """
        return [self.read(src) for src in sources]

    def detect_format(self, source: str | Path) -> InputFormat:
        """Detect input format without fully parsing the file.

        Args:
            source: SMILES string or file path.

        Returns:
            Detected InputFormat enum value.
        """
        from cccp.io.input_handler import (
            InputFormat as OldInputFormat,
            MolecularInputHandler,
        )

        fmt = MolecularInputHandler.detect_format(source)
        mapping = {
            OldInputFormat.SMILES: InputFormat.SMILES,
            OldInputFormat.XYZ: InputFormat.XYZ,
            OldInputFormat.GJF: InputFormat.GJF,
            OldInputFormat.LOG: InputFormat.LOG,
            OldInputFormat.OUT: InputFormat.OUT,
        }
        return mapping.get(fmt, InputFormat.UNKNOWN)

    @staticmethod
    def _build_smiles_heuristic():
        """Placeholder; heuristic logic delegated to MolecularInputHandler."""
        pass


class StructureWriter:
    """Write structures to various output formats."""

    @staticmethod
    def write_xyz(structure: Structure, path: str | Path) -> Path:
        """Write structure as XYZ file.

        Args:
            structure: Structure to write.
            path: Output file path.

        Returns:
            The resolved Path that was written to.
        """
        from cccp.utils.file_io import write_xyz

        if structure.coordinates is None:
            raise ValueError("Cannot write XYZ without coordinates")

        path = Path(path)
        coordinates = np.asarray(structure.coordinates, dtype=float)
        write_xyz(
            path,
            coordinates,
            structure.symbols,
            title=structure.id,
        )
        return path

    @staticmethod
    def write_json(structure: Structure, path: str | Path) -> Path:
        """Write structure metadata as JSON.

        Args:
            structure: Structure to serialize.
            path: Output file path.

        Returns:
            The resolved Path that was written to.
        """
        path = Path(path)
        coordinates = None
        if structure.coordinates is not None:
            coordinates = np.asarray(structure.coordinates, dtype=float).tolist()

        data = {
            "id": structure.id,
            "charge": structure.charge,
            "multiplicity": structure.multiplicity,
            "symbols": structure.symbols,
            "coordinates": coordinates,
            "metadata": structure.metadata,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path
