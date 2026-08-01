"""
ACP Chemistry Utilities
=======================

Shared, RDKit-based helpers for molecular structure generation.

These functions are intentionally thin and stateless. They do **not** fall back
to a different molecule when the input fails: a bad SMILES or a missing RDKit
installation raises an explicit exception so callers can surface the real error.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any


def _require_rdkit() -> tuple[Any, Any]:
    """Import RDKit modules or raise a clear ImportError."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise ImportError(
            "RDKit is required for molecular embedding. Install with: pip install rdkit"
        ) from exc
    return Chem, AllChem


def smiles_to_xyz(
    smiles: str,
    *,
    seed: int = 42,
    comment: str | None = None,
) -> str:
    """Embed a SMILES string into a single-frame XYZ representation.

    Args:
        smiles: The input SMILES string.
        seed: Random seed passed to the ETKDG embedder for reproducibility.
        comment: Optional comment line for the XYZ frame. If omitted, a demo
            comment is generated that includes the source SMILES.

    Returns:
        A single-frame XYZ string.

    Raises:
        ValueError: If the SMILES is invalid or RDKit cannot embed the structure.
        ImportError: If RDKit is not installed.
    """
    if not smiles or not smiles.strip():
        raise ValueError("SMILES input is empty")

    Chem, AllChem = _require_rdkit()

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles}")

    mol3d = Chem.AddHs(mol)

    etkdg_v3 = getattr(AllChem, "ETKDGv3", None)
    etkdg = getattr(AllChem, "ETKDG", None)
    embed = getattr(AllChem, "EmbedMolecule", None)
    if embed is None or etkdg is None:
        raise RuntimeError("RDKit 3D embedding support is unavailable")

    params = etkdg_v3() if etkdg_v3 is not None else etkdg()
    params.randomSeed = seed
    code = embed(mol3d, params)
    if code != 0:
        params.useRandomCoords = True
        code = embed(mol3d, params)
    if code != 0:
        raise ValueError("RDKit failed to embed input structure")

    mmff_sanitize = getattr(AllChem, "MMFFSanitizeMolecule", None)
    mmff_optimize = getattr(AllChem, "MMFFOptimizeMolecule", None)
    uff_optimize = getattr(AllChem, "UFFOptimizeMolecule", None)
    try:
        if mmff_sanitize is not None and mmff_optimize is not None:
            mmff_sanitize(mol3d)
            mmff_optimize(mol3d)
        elif uff_optimize is not None:
            uff_optimize(mol3d, maxIters=200)
    except Exception:
        if uff_optimize is not None:
            try:
                uff_optimize(mol3d, maxIters=200)
            except Exception:
                pass

    xyz = Chem.MolToXYZBlock(mol3d)
    lines = xyz.strip("\n").splitlines()
    if len(lines) < 2:
        raise ValueError("RDKit produced an empty XYZ block")

    safe_smiles = " ".join(smiles.split())[:80]
    lines[1] = comment or f"source={safe_smiles} | generated_by=rdkit_etkdg"
    return "\n".join(lines) + "\n"


def molfile_to_xyz(
    molfile: str,
    *,
    seed: int = 42,
    comment: str | None = None,
) -> str:
    """Embed a molfile/SDF block into a single-frame XYZ representation.

    Args:
        molfile: A molfile (V2000/V3000) or SDF block.
        seed: Random seed passed to the ETKDG embedder for reproducibility.
        comment: Optional comment line for the XYZ frame.

    Returns:
        A single-frame XYZ string.

    Raises:
        ValueError: If the molfile is invalid or RDKit cannot embed the structure.
        ImportError: If RDKit is not installed.
    """
    if not molfile or not molfile.strip():
        raise ValueError("molfile input is empty")

    Chem, AllChem = _require_rdkit()

    try:
        mol = Chem.MolFromMolBlock(molfile, sanitize=True, removeHs=False)
    except Exception as exc:
        raise ValueError(f"Invalid molfile: {exc}") from exc
    if mol is None:
        raise ValueError("Invalid molfile")

    mol3d = Chem.AddHs(mol)

    etkdg_v3 = getattr(AllChem, "ETKDGv3", None)
    etkdg = getattr(AllChem, "ETKDG", None)
    embed = getattr(AllChem, "EmbedMolecule", None)
    if embed is None or etkdg is None:
        raise RuntimeError("RDKit 3D embedding support is unavailable")

    params = etkdg_v3() if etkdg_v3 is not None else etkdg()
    params.randomSeed = seed
    code = embed(mol3d, params)
    if code != 0:
        params.useRandomCoords = True
        code = embed(mol3d, params)
    if code != 0:
        raise ValueError("RDKit failed to embed input structure")

    mmff_sanitize = getattr(AllChem, "MMFFSanitizeMolecule", None)
    mmff_optimize = getattr(AllChem, "MMFFOptimizeMolecule", None)
    uff_optimize = getattr(AllChem, "UFFOptimizeMolecule", None)
    try:
        if mmff_sanitize is not None and mmff_optimize is not None:
            mmff_sanitize(mol3d)
            mmff_optimize(mol3d)
        elif uff_optimize is not None:
            uff_optimize(mol3d, maxIters=200)
    except Exception:
        if uff_optimize is not None:
            try:
                uff_optimize(mol3d, maxIters=200)
            except Exception:
                pass

    xyz = Chem.MolToXYZBlock(mol3d)
    lines = xyz.strip("\n").splitlines()
    if len(lines) < 2:
        raise ValueError("RDKit produced an empty XYZ block")

    lines[1] = comment or "source=molfile | generated_by=rdkit_etkdg"
    return "\n".join(lines) + "\n"


def xyz_to_multiframe_demo(
    xyz: str,
    *,
    frames: int = 3,
    seed: int = 42,
    comment: str | None = None,
) -> str:
    """Generate a deterministic multi-frame XYZ from a single-frame XYZ.

    This is **only** for UI/demo purposes (e.g. testing the frame player). It
    does not represent a conformer search or reaction path.

    Args:
        xyz: Single-frame XYZ string.
        frames: Number of frames to generate (minimum 2).
        seed: Random seed for deterministic perturbations.
        comment: Optional base comment. Defaults to the original comment line.

    Returns:
        A multi-frame XYZ string.

    Raises:
        ValueError: If ``xyz`` is not a valid single-frame XYZ.
    """
    base_text = xyz.strip("\n")
    lines = base_text.splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ input is too short to build demo frames")

    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("XYZ first line must contain atom count") from exc

    if len(lines) < atom_count + 2:
        raise ValueError("XYZ atom lines are incomplete")

    base_lines = lines[: atom_count + 2]
    atoms: list[tuple[str, float, float, float]] = []
    for atom_line in base_lines[2:]:
        parts = atom_line.split()
        if len(parts) < 4:
            raise ValueError(f"Invalid XYZ atom line: {atom_line}")
        atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))

    ensemble_blocks = ["\n".join(base_lines)]
    rng = random.Random(seed)
    base_comment = comment if comment is not None else base_lines[1].strip()
    for frame_index in range(1, max(frames, 2)):
        amplitude = 0.02 * frame_index
        frame_lines = [str(atom_count), f"{base_comment} | frame {frame_index + 1}"]
        for symbol, x, y, z in atoms:
            frame_lines.append(
                " ".join(
                    (
                        symbol,
                        f"{x + rng.uniform(-amplitude, amplitude):.6f}",
                        f"{y + rng.uniform(-amplitude, amplitude):.6f}",
                        f"{z + rng.uniform(-amplitude, amplitude):.6f}",
                    )
                )
            )
        ensemble_blocks.append("\n".join(frame_lines))

    return "\n".join(ensemble_blocks) + "\n"


def count_elements_from_xyz(xyz: str) -> dict[str, int]:
    """Count element occurrences in the first frame of an XYZ string.

    Args:
        xyz: XYZ string (single or multi-frame; only the first frame is read).

    Returns:
        Mapping from element symbol to atom count.

    Raises:
        ValueError: If ``xyz`` is not a valid XYZ frame.
    """
    lines = xyz.strip("\n").splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ input is too short")

    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("XYZ first line must contain atom count") from exc

    if len(lines) < atom_count + 2:
        raise ValueError("XYZ atom lines are incomplete")

    counts: dict[str, int] = {}
    for line in lines[2 : atom_count + 2]:
        parts = line.split()
        if not parts:
            raise ValueError("Empty atom line in XYZ")
        symbol = parts[0]
        counts[symbol] = counts.get(symbol, 0) + 1
    return counts


def xyz_formula(xyz: str) -> str:
    """Return a Hill-ordered formula string for the first XYZ frame.

    Args:
        xyz: XYZ string (single or multi-frame; only the first frame is read).

    Returns:
        Formula string such as ``C2H6O``.
    """
    counts = count_elements_from_xyz(xyz)
    return _hill_formula(counts)


def _hill_formula(counts: dict[str, int]) -> str:
    """Order element counts according to the Hill system."""
    remaining = dict(counts)
    parts: list[str] = []
    if "C" in remaining:
        parts.append(_format_element("C", remaining.pop("C")))
    if "H" in remaining:
        parts.append(_format_element("H", remaining.pop("H")))
    for symbol in sorted(remaining):
        parts.append(_format_element(symbol, remaining[symbol]))
    return "".join(parts)


def _format_element(symbol: str, count: int) -> str:
    return symbol if count == 1 else f"{symbol}{count}"


def parse_xyz_first_frame(xyz: str) -> list[dict[str, Any]]:
    """Parse the first frame of an XYZ string into atom records.

    Args:
        xyz: XYZ string.

    Returns:
        List of atom dicts with keys ``elem``, ``x``, ``y``, ``z``.
    """
    lines = xyz.strip("\n").splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ input is too short")

    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("XYZ first line must contain atom count") from exc

    if len(lines) < atom_count + 2:
        raise ValueError("XYZ atom lines are incomplete")

    atoms: list[dict[str, Any]] = []
    for idx, line in enumerate(lines[2 : atom_count + 2], start=0):
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"Invalid XYZ atom line: {line}")
        atoms.append(
            {
                "id": idx,
                "elem": parts[0],
                "x": float(parts[1]),
                "y": float(parts[2]),
                "z": float(parts[3]),
            }
        )
    return atoms


def split_xyz_frames(xyz: str) -> list[str]:
    """Split a multi-frame XYZ string into individual frame strings.

    Args:
        xyz: Multi-frame XYZ string.

    Returns:
        List of single-frame XYZ strings.
    """
    lines = xyz.strip("\n").splitlines()
    frames: list[str] = []
    i = 0
    while i < len(lines):
        try:
            n = int(lines[i].strip())
        except (ValueError, IndexError):
            break
        if i + 1 + n > len(lines):
            break
        frame_lines = lines[i : i + 2 + n]
        frames.append("\n".join(frame_lines) + "\n")
        i += 2 + n
    return frames


def enumerate_embeddings(
    source: str | Path,
    *,
    n: int,
    seed_base: int = 42,
    comment: str | None = None,
) -> list[str]:
    """Enumerate *n* distinct RDKit-embedded XYZ strings from the input.

    The enumeration derives from the **original** input (SMILES string or
    structure file), not from an already-embedded result, so each returned
    conformation is an independent ETKDG sample (seed = ``seed_base + i``).
    This backs the multi-start xTB-MD replica scheme of the
    ``xtbmd_censo_energy`` workflow.

    Args:
        source: SMILES string or a path to an XYZ/molfile/SDF structure.
        n: Number of distinct embeddings to produce.
        seed_base: Base seed for the ETKDG embedder; replica *i* uses
            ``seed_base + i``.
        comment: Optional comment line for the XYZ frames.

    Returns:
        List of ``n`` single-frame XYZ strings.

    Raises:
        ValueError: If the source cannot be parsed or embedded, or ``n`` is
            not positive.
        ImportError: If RDKit is not installed.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")

    Chem, AllChem = _require_rdkit()

    if isinstance(source, Path):
        source_text = str(source)
        mol = _mol_from_file(source, Chem)
    elif "\n" not in source.strip() and not Path(source).exists():
        mol = Chem.MolFromSmiles(source)
        if mol is None:
            raise ValueError(f"Invalid SMILES: {source}")
        source_text = source
        mol = Chem.AddHs(mol)
    else:
        mol = _mol_from_file(Path(source), Chem)
        source_text = str(source)

    embeddings: list[str] = []
    for i in range(n):
        # Embed into a fresh copy: re-embedding the shared mol object would
        # depend on RDKit-version conformer append/replace semantics and
        # could return the same conformation for every replica.
        try:
            block = _embed_mol(Chem.Mol(mol), seed_base + i, Chem, AllChem)
        except Exception as exc:
            raise ValueError(f"RDKit failed to embed structure {i}: {exc}") from exc
        lines = block.strip("\n").splitlines()
        safe_source = " ".join(source_text.split())[:80]
        lines[1] = comment or f"source={safe_source} | generated_by=rdkit_etkdg | emb={i}"
        embeddings.append("\n".join(lines) + "\n")
    return embeddings


def _mol_from_file(path: Path, Chem: Any) -> Any:
    """Load a molecule from an XYZ/molfile/SDF path, adding hydrogens."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".xyz":
            mol = Chem.MolFromXYZFile(str(path))
        elif suffix in {".mol", ".sdf", ".sd"}:
            mol = Chem.MolFromMolFile(str(path))
        else:
            raise ValueError(f"Unsupported structure format for embedding: {suffix}")
    except Exception as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc
    if mol is None:
        raise ValueError(f"RDKit could not parse {path}")
    try:
        # XYZ frames carry no bond information; re-running RemoveHs forces
        # the implicit-valence bookkeeping so AddHs can compute the full
        # hydrogen count.
        mol = Chem.RemoveHs(mol)
        return Chem.AddHs(mol)
    except Exception as exc:
        raise ValueError(f"RDKit could not add hydrogens to {path}: {exc}") from exc


def _embed_mol(mol3d: Any, seed: int, Chem: Any, AllChem: Any) -> str:
    """Embed *mol3d* with ETKDG (``randomSeed=seed``) and return its XYZ block.

    Falls back to random-coordinate embedding when deterministic ETKDG
    fails, then relaxes with MMFF (UFF fallback) — the same recipe used by
    :func:`smiles_to_xyz`.
    """
    etkdg_v3 = getattr(AllChem, "ETKDGv3", None)
    etkdg = getattr(AllChem, "ETKDG", None)
    embed = getattr(AllChem, "EmbedMolecule", None)
    if embed is None or etkdg is None:
        raise RuntimeError("RDKit 3D embedding support is unavailable")

    params = etkdg_v3() if etkdg_v3 is not None else etkdg()
    params.randomSeed = seed
    code = embed(mol3d, params)
    if code != 0:
        params.useRandomCoords = True
        code = embed(mol3d, params)
    if code != 0:
        raise ValueError("RDKit failed to embed input structure")

    mmff_sanitize = getattr(AllChem, "MMFFSanitizeMolecule", None)
    mmff_optimize = getattr(AllChem, "MMFFOptimizeMolecule", None)
    uff_optimize = getattr(AllChem, "UFFOptimizeMolecule", None)
    try:
        if mmff_sanitize is not None and mmff_optimize is not None:
            mmff_sanitize(mol3d)
            mmff_optimize(mol3d)
        elif uff_optimize is not None:
            uff_optimize(mol3d, maxIters=200)
    except Exception:
        if uff_optimize is not None:
            try:
                uff_optimize(mol3d, maxIters=200)
            except Exception:
                pass

    xyz = Chem.MolToXYZBlock(mol3d)
    lines = xyz.strip("\n").splitlines()
    if len(lines) < 2:
        raise ValueError("RDKit produced an empty XYZ block")
    return "\n".join(lines) + "\n"


__all__ = [
    "count_elements_from_xyz",
    "enumerate_embeddings",
    "molfile_to_xyz",
    "parse_xyz_first_frame",
    "smiles_to_xyz",
    "split_xyz_frames",
    "xyz_formula",
    "xyz_to_multiframe_demo",
]
