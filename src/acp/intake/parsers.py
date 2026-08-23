from __future__ import annotations

import re
from typing import Any

from acp.intake.models import StructureAsset, StructureParseResult

_ASSET_COUNTER = 0


def _next_asset_id() -> str:
    global _ASSET_COUNTER
    _ASSET_COUNTER += 1
    return f"str_{_ASSET_COUNTER:04d}"


def _reset_asset_counter() -> None:
    global _ASSET_COUNTER
    _ASSET_COUNTER = 0


def _hill_formula(symbols: list[str]) -> str:
    counts: dict[str, int] = {}
    for s in symbols:
        counts[s] = counts.get(s, 0) + 1
    parts: list[str] = []
    remaining = dict(counts)
    if "C" in remaining:
        n = remaining.pop("C")
        parts.append(f"C{n}" if n > 1 else "C")
    if "H" in remaining:
        n = remaining.pop("H")
        parts.append(f"H{n}" if n > 1 else "H")
    for sym in sorted(remaining):
        n = remaining[sym]
        parts.append(f"{sym}{n}" if n > 1 else sym)
    return "".join(parts)


def _xyz_from_symbols_coords(
    symbols: list[str], coords: list[tuple[float, float, float]], comment: str = ""
) -> str:
    lines = [str(len(symbols)), comment]
    for sym, (x, y, z) in zip(symbols, coords):
        lines.append(f"{sym} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines) + "\n"


_EXT_FORMAT_MAP = {
    "xyz": "xyz",
    "sdf": "sdf",
    "sd": "sdf",
    "mol": "mol",
    "gjf": "gjf",
    "com": "gjf",
    "inp": "inp",
}

# Content-detection priority (§4.1): sdf -> mol -> xyz -> gjf -> inp -> smiles
_DETECT_ORDER = ("sdf", "mol", "xyz", "gjf", "inp", "smiles")

_ORCA_BLOCK_RE = re.compile(r"^\*\s*(xyz|int|gzmt|internal)\b", re.IGNORECASE | re.MULTILINE)


def _has_route_line(content: str) -> bool:
    """True if any line starts with ``#`` (Gaussian route-section marker)."""
    return any(line.lstrip().startswith("#") for line in content.splitlines())


def _has_orca_block_marker(content: str) -> bool:
    """True if an ORCA coordinate block (``* xyz`` / ``* int`` ...) is present."""
    return bool(_ORCA_BLOCK_RE.search(content))


def _looks_like_gjf(content: str) -> bool:
    """Gaussian GJF heuristic: ``#`` route line plus parseable section layout.

    A GJF requires a ``#`` route line AND at least 2 blank-line separators
    (the hard requirement of :func:`parse_gjf_text`). A Link0 header
    (``%chk=``/``%mem=``/``%nprocshared=``) followed by a route line also
    counts, so ``%chk``-headed GJFs are not misread as ORCA INP.
    """
    if not _has_route_line(content):
        return False
    lines = content.splitlines()
    if sum(1 for line in lines if not line.strip()) >= 2:
        return True
    head = [line.strip().lower() for line in lines if line.strip()][:4]
    return any(h.startswith(("%chk=", "%mem=", "%nprocshared=")) for h in head)


def _looks_like_orca_inp(content: str) -> bool:
    """ORCA INP heuristic: ``%``/``!`` header, ``* xyz``/``* int`` block, no route line."""
    stripped = content.strip()
    if not (stripped.startswith("%") or "!" in stripped[:200]):
        return False
    if not _has_orca_block_marker(content):
        return False
    return not _has_route_line(content)


def _first_line_is_atom_count(stripped: str) -> bool:
    first = stripped.splitlines()[0].strip() if stripped else ""
    return first.isdigit()


def _candidate_formats(content: str) -> list[str]:
    """Strictly content-detected candidate formats, in priority order."""
    stripped = content.strip()
    if not stripped:
        return []
    found: set[str] = set()
    if stripped.startswith("$SDG") or ("M  END" in stripped and "$$$$" in stripped):
        found.add("sdf")
    if "M  END" in stripped:
        found.add("mol")
    if _first_line_is_atom_count(stripped) or _looks_like_atom_coordinates(stripped):
        found.add("xyz")
    if _looks_like_gjf(content):
        found.add("gjf")
    if _looks_like_orca_inp(content):
        found.add("inp")
    if len(stripped) < 80 and "\n" not in stripped:
        found.add("smiles")
    return [fmt for fmt in _DETECT_ORDER if fmt in found]


def _verify_candidates(content: str) -> list[str]:
    """Candidate formats for the detect-and-verify fallback chain.

    Broader than :func:`_candidate_formats`: the strict ORCA check excludes
    content with a ``#`` route line, but that ambiguity is exactly what the
    verify step resolves — so ORCA-ish content with a block marker keeps
    ``inp`` as a fallback, and ``%``/``!``-headed content matching neither
    strict rule tries ``gjf`` then ``inp``.
    """
    found = set(_candidate_formats(content))
    stripped = content.strip()
    if stripped:
        orcaish = stripped.startswith("%") or "!" in stripped[:200]
        if orcaish and _has_orca_block_marker(content):
            found.add("inp")
        elif orcaish and not ({"gjf", "inp"} & found):
            found.add("gjf")
            found.add("inp")
    return [fmt for fmt in _DETECT_ORDER if fmt in found]


def detect_format(filename: str, content: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXT_FORMAT_MAP:
        return _EXT_FORMAT_MAP[ext]

    candidates = _candidate_formats(content)
    if candidates:
        return candidates[0]

    stripped = content.strip()
    if stripped.startswith("%") or "!" in stripped[:200]:
        # ORCA-ish header without a coordinate block marker — keep the
        # historical behaviour of calling this "inp".
        return "inp"
    return "smiles"


def detect_and_parse(content: str, filename: str = "") -> tuple[str, StructureParseResult]:
    """Detect the format and parse, verifying candidates in priority order.

    Tries the extension fast-path format (if any) first, then the
    content-detected candidates in the order sdf -> mol -> xyz -> gjf ->
    inp, with smiles as the last resort. If a candidate parse fails and a
    next candidate exists, falls back.

    Returns:
        A ``(final_format, parse_result)`` tuple where ``final_format`` is
        the format actually used for the returned result.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    candidates: list[str] = []
    ext_fmt = _EXT_FORMAT_MAP.get(ext)
    if ext_fmt is not None:
        candidates.append(ext_fmt)
    for fmt in _verify_candidates(content):
        if fmt not in candidates:
            candidates.append(fmt)
    if "smiles" not in candidates:
        candidates.append("smiles")

    final_fmt = candidates[-1]
    final_result = StructureParseResult(errors=["Unable to detect structure format"])
    for fmt in candidates:
        final_fmt = fmt
        try:
            final_result = parse_structure_text(content, fmt, filename)
        except (ValueError, IndexError) as exc:
            final_result = StructureParseResult(errors=[f"{fmt} parse failed: {exc}"])
        if final_result.ok:
            break
    return final_fmt, final_result


def _looks_like_atom_line(line: str) -> bool:
    parts = line.split()
    if len(parts) < 4:
        return False
    try:
        float(parts[1])
        float(parts[2])
        float(parts[3])
    except ValueError:
        return False
    # element symbol (e.g. C, H, He, Br) or atomic number (1-118)
    if len(parts[0]) <= 3 and parts[0][0].isalpha():
        return True
    if parts[0].isdigit() and 1 <= int(parts[0]) <= 118:
        return True
    return False


def _looks_like_atom_coordinates(text: str) -> bool:
    lines = text.splitlines()
    atom_lines = 0
    total_non_empty = 0
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            continue
        total_non_empty += 1
        if _looks_like_atom_line(stripped_line):
            atom_lines += 1
    if atom_lines < 1:
        return False
    return atom_lines >= total_non_empty * 0.8


def parse_structure_text(content: str, fmt: str, filename: str = "") -> StructureParseResult:
    if fmt == "xyz":
        return parse_xyz_text(content)
    if fmt == "sdf":
        return parse_sdf_text(content)
    if fmt == "mol":
        return parse_mol_text(content)
    if fmt == "gjf":
        return parse_gjf_text(content, filename or "input.gjf")
    if fmt == "inp":
        return parse_orca_inp_text(content, filename or "input.inp")
    if fmt == "smiles":
        return parse_smiles_list(content)
    return StructureParseResult(errors=[f"Unsupported format: {fmt}"])


def _parse_bare_atom_block(
    lines: list[str], start: int
) -> StructureAsset | None:
    symbols: list[str] = []
    coords: list[tuple[float, float, float]] = []
    for line in lines[start:]:
        if not _looks_like_atom_line(line):
            continue
        parts = line.strip().split()
        symbols.append(parts[0])
        coords.append((float(parts[1]), float(parts[2]), float(parts[3])))

    if not symbols:
        return None

    xyz = _xyz_from_symbols_coords(symbols, coords, "")
    return StructureAsset(
        asset_id=_next_asset_id(),
        name="frame_1",
        source_type="paste",
        original_format="xyz",
        xyz=xyz,
        has_3d=True,
        charge=0,
        multiplicity=1,
        atom_count=len(symbols),
        formula=_hill_formula(symbols),
    )


def parse_xyz_text(content: str) -> StructureParseResult:
    lines = content.strip().splitlines()
    structures: list[StructureAsset] = []
    errors: list[str] = []
    i = 0
    frame_idx = 0

    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        try:
            n = int(lines[i].strip())
        except ValueError:
            if _looks_like_atom_line(lines[i]):
                result = _parse_bare_atom_block(lines, i)
                if result is not None:
                    structures.append(result)
                else:
                    errors.append(f"Line {i + 1}: expected atom count, got '{lines[i][:40]}'")
                break
            else:
                errors.append(f"Line {i + 1}: expected atom count, got '{lines[i][:40]}'")
                break

        comment = ""
        has_comment = False
        if i + 1 < len(lines) and not _looks_like_atom_line(lines[i + 1]):
            comment = lines[i + 1]
            has_comment = True

        header_lines = 2 if has_comment else 1
        if i + header_lines + n > len(lines):
            errors.append(f"Frame {frame_idx + 1}: declared {n} atoms but file truncated")
            break

        symbols: list[str] = []
        coords: list[tuple[float, float, float]] = []
        for j in range(n):
            parts = lines[i + header_lines + j].split()
            if len(parts) < 4:
                errors.append(f"Frame {frame_idx + 1}, atom {j + 1}: malformed line")
                break
            symbols.append(parts[0])
            coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
        else:
            xyz = _xyz_from_symbols_coords(symbols, coords, comment)
            charge, mult = _parse_charge_mult_from_comment(comment)
            structures.append(
                StructureAsset(
                    asset_id=_next_asset_id(),
                    name=f"frame_{frame_idx + 1}",
                    source_type="paste",
                    original_format="xyz",
                    xyz=xyz,
                    has_3d=True,
                    charge=charge,
                    multiplicity=mult,
                    atom_count=n,
                    formula=_hill_formula(symbols),
                )
            )
            frame_idx += 1
        i += header_lines + n

    if not structures and not errors:
        errors.append("No valid XYZ frames found")

    return StructureParseResult(structures=structures, errors=errors)


def _parse_charge_mult_from_comment(comment: str) -> tuple[int, int]:
    charge = 0
    mult = 1
    cm = re.search(r"charge\s*=\s*(-?\d+)", comment, re.IGNORECASE)
    if cm:
        charge = int(cm.group(1))
    mm = re.search(r"mult(?:i(?:plicity)?)?\s*=\s*(\d+)", comment, re.IGNORECASE)
    if mm:
        mult = int(mm.group(1))
    return charge, mult


def parse_sdf_text(content: str) -> StructureParseResult:
    blocks = re.split(r"\$\$\$\$", content)
    structures: list[StructureAsset] = []
    errors: list[str] = []

    for idx, block in enumerate(blocks):
        block = block.strip()
        if not block:
            continue
        if "M  END" not in block:
            errors.append(f"Record {idx + 1}: no M  END line, skipping")
            continue
        try:
            mol = _parse_molblock(block)
        except ValueError as exc:
            errors.append(f"Record {idx + 1}: {exc}")
            continue

        name = _extract_sdf_title(block, idx)
        mol["name"] = name or mol["name"]
        mol["asset_id"] = _next_asset_id()
        mol["source_type"] = "paste"
        mol["original_format"] = "sdf"
        structures.append(StructureAsset(**mol))

    if not structures and not errors:
        errors.append("No valid SDF records found")

    return StructureParseResult(structures=structures, errors=errors)


def parse_mol_text(content: str) -> StructureParseResult:
    block = content.strip()
    if "M  END" not in block:
        return StructureParseResult(errors=["No M  END line found in MOL block"])
    try:
        mol = _parse_molblock(block)
    except ValueError as exc:
        return StructureParseResult(errors=[str(exc)])

    mol["asset_id"] = _next_asset_id()
    mol["source_type"] = "paste"
    mol["original_format"] = "mol"
    return StructureParseResult(structures=[StructureAsset(**mol)])


def _extract_sdf_title(block: str, idx: int) -> str:
    lines = block.splitlines()
    if len(lines) >= 1 and lines[0].strip():
        return lines[0].strip()
    return f"molecule_{idx + 1}"


def _parse_molblock(block: str) -> dict[str, Any]:
    try:
        from rdkit import Chem
    except ImportError:
        return _parse_molblock_manual(block)

    mol = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False)
    if mol is None:
        raise ValueError("RDKit failed to parse MOL block")

    conf = mol.GetConformer()
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    coords = [
        (conf.GetAtomPosition(i).x, conf.GetAtomPosition(i).y, conf.GetAtomPosition(i).z)
        for i in range(mol.GetNumAtoms())
    ]
    has_3d = mol.GetNumConformers() > 0

    xyz = _xyz_from_symbols_coords(symbols, coords) if has_3d else ""

    charge = sum(atom.GetFormalCharge() for atom in mol.GetAtoms())
    n_radicals = sum(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
    mult = max(1, n_radicals + 1)

    try:
        from rdkit.Chem import rdMolDescriptors
        formula = rdMolDescriptors.CalcMolFormula(mol)
    except Exception:
        formula = _hill_formula(symbols)

    return {
        "name": "",
        "xyz": xyz if has_3d else None,
        "molfile": block + "\n",
        "has_3d": has_3d,
        "charge": charge,
        "multiplicity": mult,
        "atom_count": mol.GetNumAtoms(),
        "formula": formula,
        "smiles": Chem.MolToSmiles(mol) if has_3d else None,
    }


def _parse_molblock_manual(block: str) -> dict[str, Any]:
    lines = block.splitlines()
    if len(lines) < 4:
        raise ValueError("MOL block too short")
    counts_line = lines[3]
    try:
        n_atoms = int(counts_line[0:3])
    except ValueError:
        raise ValueError("Cannot parse atom count from counts line")

    if len(lines) < 4 + n_atoms:
        raise ValueError(f"Declared {n_atoms} atoms but block too short")

    symbols: list[str] = []
    coords: list[tuple[float, float, float]] = []
    for i in range(n_atoms):
        parts = lines[4 + i].split()
        if len(parts) < 4:
            raise ValueError(f"Atom line {i + 1} malformed")
        symbols.append(parts[3])
        coords.append((float(parts[0]), float(parts[1]), float(parts[2])))

    has_3d = any(x != 0 or y != 0 or z != 0 for x, y, z in coords)
    xyz = _xyz_from_symbols_coords(symbols, coords) if has_3d else ""

    return {
        "name": lines[0].strip() if lines[0].strip() else "molecule",
        "xyz": xyz if has_3d else None,
        "molfile": block + "\n",
        "has_3d": has_3d,
        "charge": 0,
        "multiplicity": 1,
        "atom_count": n_atoms,
        "formula": _hill_formula(symbols),
        "smiles": None,
    }


def parse_gjf_text(content: str, filename: str = "input.gjf") -> StructureParseResult:
    lines = content.splitlines()
    if not lines:
        return StructureParseResult(errors=["Empty Gaussian input"])

    blank_indices = [i for i, l in enumerate(lines) if l.strip() == ""]
    if len(blank_indices) < 2:
        return StructureParseResult(errors=["Gaussian input needs at least 2 blank-line separators"])

    route_idx = blank_indices[0]
    charge_mult_line = ""
    for i in range(route_idx + 1, len(lines)):
        if lines[i].strip():
            charge_mult_line = lines[i].strip()
            break
    charge, mult = _parse_gjf_charge_mult(charge_mult_line)

    coord_start = blank_indices[1] + 1 if len(blank_indices) > 1 else route_idx + 2
    symbols: list[str] = []
    coords: list[tuple[float, float, float]] = []
    for line in lines[coord_start:]:
        if line.strip() == "":
            break
        parts = line.split()
        if len(parts) < 4:
            continue
        symbols.append(parts[0])
        coords.append((float(parts[1]), float(parts[2]), float(parts[3])))

    if not symbols:
        return StructureParseResult(errors=["No atom coordinates found in Gaussian input"])

    xyz = _xyz_from_symbols_coords(symbols, coords, filename)
    structure = StructureAsset(
        asset_id=_next_asset_id(),
        name=Path_safe(filename),
        source_type="paste",
        original_format="gjf",
        xyz=xyz,
        has_3d=True,
        charge=charge,
        multiplicity=mult,
        atom_count=len(symbols),
        formula=_hill_formula(symbols),
    )
    return StructureParseResult(structures=[structure])


def Path_safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name.rsplit(".", 1)[0])


def _parse_gjf_charge_mult(line: str) -> tuple[int, int]:
    parts = line.split()
    if len(parts) >= 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 0, 1


def parse_orca_inp_text(content: str, filename: str = "input.inp") -> StructureParseResult:
    lines = content.splitlines()
    charge = 0
    mult = 1
    in_coords = False
    symbols: list[str] = []
    coords: list[tuple[float, float, float]] = []

    for line in lines:
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("*") and "xyz" in low:
            in_coords = True
            continue
        if in_coords and stripped == "*":
            in_coords = False
            continue
        if in_coords:
            if low.startswith("charge") or stripped.isdigit():
                parts = stripped.split()
                if len(parts) >= 2 and parts[0].lower() in ("charge", "chrg"):
                    try:
                        charge = int(parts[1])
                    except ValueError:
                        pass
                    continue
                if len(parts) >= 2 and parts[0].lower() in ("mult", "multiplicity"):
                    try:
                        mult = int(parts[1])
                    except ValueError:
                        pass
                    continue
            parts = stripped.split()
            if len(parts) >= 4:
                symbols.append(parts[0])
                coords.append((float(parts[1]), float(parts[2]), float(parts[3])))

    if not symbols:
        return StructureParseResult(errors=["No atom coordinates found in ORCA input"])

    xyz = _xyz_from_symbols_coords(symbols, coords, filename)
    structure = StructureAsset(
        asset_id=_next_asset_id(),
        name=Path_safe(filename),
        source_type="paste",
        original_format="inp",
        xyz=xyz,
        has_3d=True,
        charge=charge,
        multiplicity=mult,
        atom_count=len(symbols),
        formula=_hill_formula(symbols),
    )
    return StructureParseResult(structures=[structure])


def parse_smiles_list(content: str) -> StructureParseResult:
    lines = [l.strip() for l in content.strip().splitlines() if l.strip() and not l.strip().startswith("#")]
    if not lines:
        return StructureParseResult(errors=["No SMILES found in input"])

    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        return StructureParseResult(errors=["RDKit is required for SMILES parsing"])

    structures: list[StructureAsset] = []
    errors: list[str] = []

    for idx, smiles in enumerate(lines):
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            errors.append(f"Line {idx + 1}: invalid SMILES '{smiles}'")
            continue

        mol3d = Chem.AddHs(mol)
        etkdg = getattr(AllChem, "ETKDGv3", None) or getattr(AllChem, "ETKDG", None)
        embed = getattr(AllChem, "EmbedMolecule", None)
        has_3d = False
        xyz: str | None = None
        if etkdg is not None and embed is not None:
            params = etkdg()
            params.randomSeed = 42
            if embed(mol3d, params) == 0:
                xyz = Chem.MolToXYZBlock(mol3d)
                has_3d = True

        charge = Chem.GetFormalCharge(mol)
        n_radicals = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
        mult = max(1, n_radicals + 1)
        n_atoms = mol3d.GetNumAtoms()
        symbols = [a.GetSymbol() for a in mol3d.GetAtoms()]

        structures.append(
            StructureAsset(
                asset_id=_next_asset_id(),
                name=f"mol_{idx + 1}",
                source_type="smiles",
                original_format="smiles",
                xyz=xyz,
                has_3d=has_3d,
                charge=charge,
                multiplicity=mult,
                atom_count=n_atoms,
                formula=_hill_formula(symbols),
                smiles=smiles,
                warnings=[] if has_3d else ["3D embedding failed, structure without coordinates"],
            )
        )

    return StructureParseResult(structures=structures, errors=errors)


__all__ = [
    "detect_and_parse",
    "detect_format",
    "parse_gjf_text",
    "parse_mol_text",
    "parse_orca_inp_text",
    "parse_sdf_text",
    "parse_smiles_list",
    "parse_structure_text",
    "parse_xyz_text",
]
