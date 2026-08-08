# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
# ruff: noqa: N803, N806
"""Diastereomer enumeration for the NMR DP4/DP5 workflow (DevDoc §5 stage 1, P2).

When a single structure with incomplete stereochemistry is supplied, this
module expands it into the full set of distinct diastereomers using RDKit's
:func:`EnumerateStereoisomers`. **Enantiomer pairs collapse to a single
representative** because achiral NMR (GIAO chemical shielding + the Goodman
DP4 likelihood) is identical for enantiomers — keeping both would double the
compute budget and return degenerate probabilities.

Only input carrying bond information (SMILES, mol block, SDF/MOL) can be
enumerated: stereochemistry is a topological property, so a bare XYZ frame
(no connectivity) cannot be enumerated and raises a clear error.

The optional ``--stereocenters`` filter restricts enumeration to a subset of
stereocenters (atom labels like ``"C5,C8"``). Centres outside the filter keep
their input configuration; unassigned centres outside the filter are pinned
to an arbitrary definite configuration so RDKit does not enumerate them.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.nmr.models import normalize_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnumerateOptions:
    """Options controlling stereoisomer enumeration.

    Attributes:
        only_unassigned: Only flip stereocenters left unspecified in the
            input (RDKit default, and the DP4 use-case — fully-specified
            inputs are returned as-is).
        dedup_enantiomers: Collapse each enantiomer pair to one
            representative (default ``True`` — DP4 cannot distinguish
            enantiomers in achiral media).
        try_embedding: Have RDKit attempt a 3D embedding per isomer to
            filter out stereochemically impossible arrangements (slower).
        max_isomers: Hard cap on returned isomers (``0`` = unlimited).
        seed: Reproducibility seed handed to RDKit's random generator.
    """

    only_unassigned: bool = True
    dedup_enantiomers: bool = True
    try_embedding: bool = False
    max_isomers: int = 0
    seed: int = 42


@dataclass(frozen=True)
class EnumeratedCandidate:
    """One enumerated diastereomer ready for the NMR workflow.

    Attributes:
        smiles: Canonical isomeric SMILES (definite stereochemistry).
        label: Human-friendly label (``"diastereomer_1"``, ...).
        stereocenters: Number of tetrahedral stereocenters found.
        enumerated_centers: Number of centres that were actually flipped.
    """

    smiles: str
    label: str
    stereocenters: int = 0
    enumerated_centers: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# RDKit import shim (mirrors chem/embedding.py)
# ---------------------------------------------------------------------------


def _require_rdkit() -> tuple[Any, Any, Any, Any]:
    """Import RDKit modules or raise a clear ImportError."""
    try:
        from rdkit import Chem
        from rdkit.Chem.EnumerateStereoisomers import (
            EnumerateStereoisomers,
            StereoEnumerationOptions,
        )
    except ImportError as exc:  # pragma: no cover - rdkit is a hard dep
        raise ImportError(
            "RDKit is required for stereoisomer enumeration. Install with: pip install rdkit"
        ) from exc
    return Chem, EnumerateStereoisomers, StereoEnumerationOptions  # type: ignore[return-value]


def _build_enumeration_options(
    opts: EnumerateOptions, opts_cls: Any
) -> Any:
    """Construct ``StereoEnumerationOptions`` with a seed across RDKit versions.

    Older RDKit (≤ 2023.x) accepted ``randGenSeed=``; newer builds (2024+)
    renamed it to ``rand=<random.Random>``. Passing a keyword the running
    version does not know raises ``TypeError``, so we probe the signature once
    and use whichever parameter the installed RDKit exposes.
    """
    kwargs: dict[str, Any] = {
        "tryEmbedding": opts.try_embedding,
        "onlyUnassigned": opts.only_unassigned,
        "maxIsomers": opts.max_isomers if opts.max_isomers > 0 else 0,
        "unique": True,
    }
    if not hasattr(opts_cls, "_acp_seed_param"):
        import inspect

        param_names = set()
        try:
            param_names = set(inspect.signature(opts_cls.__init__).parameters)
        except (ValueError, TypeError):  # pragma: no cover - C++ signatures
            pass
        opts_cls._acp_seed_param = "rand" if "rand" in param_names else "randGenSeed"
    seed_param = opts_cls._acp_seed_param
    if seed_param == "rand":
        kwargs["rand"] = random.Random(opts.seed)
    else:
        kwargs["randGenSeed"] = opts.seed
    return opts_cls(**kwargs)


# ---------------------------------------------------------------------------
# Input → RDKit Mol
# ---------------------------------------------------------------------------


def _mol_from_source(source: str | Path, Chem: Any) -> tuple[Any, str]:
    """Build a sanitized RDKit :class:`Mol` from SMILES / mol block / file.

    Returns ``(mol, kind)`` where *kind* describes the input lineage for
    diagnostics. Raises :class:`ValueError` for unparseable or connectivity-
    free input (bare XYZ cannot be enumerated).
    """
    if isinstance(source, Path):
        return _mol_from_file(source, Chem)

    text = str(source).strip()
    if not text:
        raise ValueError("enumerate input is empty")

    # A path that exists on disk → read the file.
    candidate_path = Path(text)
    if "\n" not in text and candidate_path.exists() and candidate_path.is_file():
        return _mol_from_file(candidate_path, Chem)

    # A structure-file suffix we recognize (even if the path is missing) →
    # route through the file loader so the user gets a clear format error
    # (e.g. XYZ cannot be enumerated) rather than a SMILES parse error.
    if "\n" not in text and "." in text.split("/")[-1]:
        suffix = Path(text).suffix.lower()
        if suffix in {".mol", ".sdf", ".sd", ".smi", ".smiles", ".csv", ".txt", ".xyz"}:
            return _mol_from_file(Path(text), Chem)

    # SMILES heuristic: single token, no newline, balanced parentheses.
    if "\n" not in text and " " not in text and text.count("(") == text.count(")"):
        mol = Chem.MolFromSmiles(text)
        if mol is None:
            raise ValueError(f"Invalid SMILES for enumeration: {text!r}")
        return mol, "smiles"

    # Otherwise treat as a mol block / SDF. Preserve the leading (possibly
    # empty) name line — stripping it shifts the V2000 counts line and breaks
    # the parser, so pass the original text minus trailing whitespace.
    mol = _mol_from_molblock(str(source).rstrip(), Chem)
    return mol, "molblock"


def _mol_from_file(path: Path, Chem: Any) -> tuple[Any, str]:
    """Load a molecule from an SDF/MOL/SMILES file path."""
    path = Path(path)
    suffix = path.suffix.lower()
    # XYZ is rejected up-front (even for missing paths): it carries no bond
    # table, so stereochemistry cannot be enumerated from it.
    if suffix == ".xyz":
        raise ValueError(
            f"Cannot enumerate stereochemistry from XYZ ({path}): "
            "XYZ has no bond table. Supply a SMILES, SDF or MOL file."
        )
    if not path.exists():
        raise ValueError(f"Input file not found: {path}")
    try:
        if suffix in {".mol", ".sdf", ".sd"}:
            supplier = Chem.SDMolSupplier(str(path), sanitize=True, removeHs=True)
            mols = [m for m in supplier if m is not None]
            if not mols:
                # retry without removeHs (some writers emit H-inclusive blocks
                # that the strict removeHs+sanitize path rejects)
                supplier = Chem.SDMolSupplier(str(path), sanitize=False, removeHs=False)
                mols = [m for m in supplier if m is not None]
                mols = [_sanitize(m, Chem) for m in mols if m is not None]
                mols = [m for m in mols if m is not None]
            if not mols:
                raise ValueError(f"No valid molecules in {path}")
            return mols[0], "sdf"
        if suffix in {".smi", ".smiles", ".csv", ".txt"}:
            first = path.read_text(encoding="utf-8").splitlines()
            token = next((ln.strip().split()[0] for ln in first if ln.strip()), "")
            mol = Chem.MolFromSmiles(token)
            if mol is None:
                raise ValueError(f"Invalid SMILES in {path}: {token!r}")
            return mol, "smiles-file"
        raise ValueError(f"Unsupported enumerate input format: {suffix}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc


def _sanitize(mol: Any, Chem: Any) -> Any:
    """Best-effort sanitize + RemoveHs for the fallback parse path."""
    try:
        Chem.SanitizeMol(mol)
    except Exception:
        return None
    try:
        return Chem.RemoveHs(mol)
    except Exception:
        return mol


def _mol_from_molblock(block: str, Chem: Any) -> Any:
    """Parse a mol block (V2000/V3000) into a sanitized RDKit :class:`Mol`.

    Tries the strict :func:`MolFromMolBlock` first, then falls back to a
    lenient ``SDMolSupplier`` parse (which tolerates minor formatting drift)
    and finally an unsanitized parse + manual sanitize.
    """
    mol = Chem.MolFromMolBlock(block, sanitize=True, removeHs=True)
    if mol is None:
        try:
            supplier = Chem.SDMolSupplier()
            supplier.SetData(block, sanitize=True, removeHs=True)
            mol = next((m for m in supplier if m is not None), None)
        except Exception:
            mol = None
    if mol is None:
        mol = Chem.MolFromMolBlock(block, sanitize=False, removeHs=False)
        if mol is not None:
            mol = _sanitize(mol, Chem)
    if mol is None:
        raise ValueError("Invalid mol block for enumeration")
    return mol


# ---------------------------------------------------------------------------
# Stereo helpers
# ---------------------------------------------------------------------------


def _tetrahedral_tags(Chem: Any) -> tuple[Any, Any]:
    """Return ``(cw, ccw)`` chiral-tag constants for *Chem*."""
    return (
        Chem.ChiralType.CHI_TETRAHEDRAL_CW,
        Chem.ChiralType.CHI_TETRAHEDRAL_CCW,
    )


def _potential_tetrahedral_centers(mol: Any, Chem: Any) -> list[tuple[int, bool]]:
    """Return ``[(atom_index, is_specified), ...]`` for tetrahedral centres.

    Uses :func:`FindPotentialStereo` (the reliable modern RDKit API) so the
    count is correct even for fully-unspecified inputs.
    """
    try:
        stereo_info = Chem.FindPotentialStereo(mol)
    except Exception:  # pragma: no cover - rdkit edge cases
        return []
    centers: list[tuple[int, bool]] = []
    for info in stereo_info:
        if "Atom_Tetrahedral" not in str(info.type):
            continue
        atom_idx = int(info.centeredOn)
        specified = "Unspecified" not in str(info.specified)
        centers.append((atom_idx, specified))
    return centers


def _count_stereocenters(mol: Any, Chem: Any) -> tuple[int, list[int]]:
    """Return ``(n_centers, atom_indices)`` for tetrahedral stereocenters."""
    centers = _potential_tetrahedral_centers(mol, Chem)
    indices = [idx for idx, _ in centers]
    return len(indices), indices


def _apply_stereocenter_filter(mol: Any, Chem: Any, keep_indices: set[int]) -> int:
    """Pin stereocenters NOT in *keep_indices* so they are not enumerated.

    Centres inside the filter keep their input configuration (assigned stays
    assigned; unassigned will be enumerated). Unassigned centres outside the
    filter are given an arbitrary definite configuration (CW) so
    :func:`EnumerateStereoisomers` with ``onlyUnassigned=True`` skips them.

    Returns the number of unassigned centres left for enumeration.
    """
    cw, _ccw = _tetrahedral_tags(Chem)
    centers = _potential_tetrahedral_centers(mol, Chem)
    if not centers:
        return 0

    if not keep_indices:
        # empty filter == enumerate every unassigned centre → no pinning
        return sum(1 for _, specified in centers if not specified)

    pinned = 0
    enumerated = 0
    for atom_idx, specified in centers:
        if atom_idx in keep_indices:
            if not specified:
                enumerated += 1
            continue
        if not specified:
            mol.GetAtomWithIdx(atom_idx).SetChiralTag(cw)
            pinned += 1
    logger.debug(
        "stereocenter filter: %d centres pinned, %d left for enumeration",
        pinned,
        enumerated,
    )
    return enumerated


def _enantiomer_smiles(mol: Any, Chem: Any) -> str:
    """Return the canonical SMILES of the mirror image (enantiomer).

    All tetrahedral stereocenters are inverted (CW↔CCW); double-bond E/Z
    geometry is preserved because it is invariant under reflection (CIP
    priorities survive a mirror).
    """
    cw, ccw = _tetrahedral_tags(Chem)
    emol = Chem.Mol(mol)
    for atom in emol.GetAtoms():
        tag = atom.GetChiralTag()
        if tag == cw:
            atom.SetChiralTag(ccw)
        elif tag == ccw:
            atom.SetChiralTag(cw)
    return Chem.MolToSmiles(emol)


def _resolve_stereocenter_labels(labels: list[str], mol: Any, Chem: Any) -> set[int]:
    """Resolve atom labels (``"C5"``/``"C 5"``) to heavy-atom indices.

    Labels follow the NMR-package convention: prefix = element, trailing
    number = 1-based index among atoms of that element in the **heavy-atom**
    mol (implicit-H SMILES / SDF). Returns an empty set when *labels* is
    empty (= no filter / enumerate everything).
    """
    if not labels:
        return set()

    # build per-element 1-based counters over heavy atoms only
    counters: dict[str, int] = {}
    label_to_idx: dict[str, int] = {}
    for atom in mol.GetAtoms():
        sym = normalize_symbol(atom.GetSymbol())
        if sym.lower() == "h":
            continue  # enumeration mol keeps implicit H; ignore explicit H too
        counters[sym] = counters.get(sym, 0) + 1
        label_to_idx[f"{sym}{counters[sym]}"] = atom.GetIdx()
        label_to_idx[f"{sym} {counters[sym]}"] = atom.GetIdx()

    resolved: set[int] = set()
    unknown: list[str] = []
    for raw in labels:
        token = raw.strip()
        if not token:
            continue
        # tolerate "C5" / "C 5" / "c5"
        norm = normalize_symbol(token[0]) + token[1:].strip()
        if norm in label_to_idx:
            resolved.add(label_to_idx[norm])
        else:
            unknown.append(token)
    if unknown:
        logger.warning(
            "stereocenter labels not matched to any heavy atom (ignored): %s",
            ", ".join(unknown),
        )
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate_candidates(
    source: str | Path,
    *,
    stereocenters: str | list[str] | None = None,
    options: EnumerateOptions | None = None,
) -> list[EnumeratedCandidate]:
    """Expand one structure into its distinct diastereomers.

    Args:
        source: SMILES, mol-block text, SDF/MOL/SMILES file path, or a
            :class:`~pathlib.Path`.
        stereocenters: Optional whitelist of atom labels (``"C5,C8"`` or
            ``["C5", "C8"]``) restricting enumeration to those centres.
        options: :class:`EnumerateOptions` overrides (defaults: enumerate
            only unassigned centres, dedup enantiomers).

    Returns:
        List of :class:`EnumeratedCandidate` (one per distinct
        diastereomer). A fully-specified input returns a single candidate.

    Raises:
        ValueError: Input is unparseable, has no bond table (XYZ), or the
            filter labels resolve to nothing.
        ImportError: RDKit is not installed.
    """
    opts = options or EnumerateOptions()
    Chem, enumerate_fn, opts_cls = _require_rdkit()

    mol, kind = _mol_from_source(source, Chem)
    try:
        Chem.SanitizeMol(mol)
    except Exception:  # pragma: no cover - already sanitized, defensive
        pass

    filter_indices = _resolve_stereocenter_labels(_parse_stereocenter_arg(stereocenters), mol, Chem)
    if stereocenters and not filter_indices:
        raise ValueError(
            f"--stereocenters {stereocenters!r} matched no heavy atoms; "
            "check the labels (convention: element + 1-based heavy-atom index, e.g. C5)."
        )

    enumerated = _apply_stereocenter_filter(mol, Chem, filter_indices)
    n_centers, _ = _count_stereocenters(mol, Chem)

    rdopts = _build_enumeration_options(opts, opts_cls)

    try:
        raw_isomers = list(enumerate_fn(mol, options=rdopts))
    except ValueError as exc:
        # RDKit raises ValueError when no stereocenters are present.
        logger.info("No stereoisomers to enumerate (%s); returning input only", exc)
        raw_isomers = [Chem.Mol(mol)]

    # Dedup enantiomers: keep one representative per {smi, enantiomer_smi} pair.
    seen_pairs: dict[tuple[str, str], str] = {}
    results: list[EnumeratedCandidate] = []
    for iso in raw_isomers:
        try:
            Chem.SanitizeMol(iso)
        except Exception:
            pass
        smi = Chem.MolToSmiles(iso)
        if not smi:
            continue
        if opts.dedup_enantiomers:
            enant = _enantiomer_smiles(iso, Chem)
            key = (smi, enant) if smi <= enant else (enant, smi)
            if key in seen_pairs:
                logger.debug("dropping enantiomer duplicate of %s", seen_pairs[key])
                continue
            seen_pairs[key] = smi
        else:
            key = (smi, smi)
            if key in seen_pairs:
                continue
            seen_pairs[key] = smi
        results.append(smi)

    candidates: list[EnumeratedCandidate] = []
    for i, smi in enumerate(results, start=1):
        candidates.append(
            EnumeratedCandidate(
                smiles=smi,
                label=f"diastereomer_{i}",
                stereocenters=n_centers,
                enumerated_centers=enumerated,
                metadata={
                    "source_kind": kind,
                    "enantiomer_dedup": opts.dedup_enantiomers,
                },
            )
        )

    if not candidates:  # pragma: no cover - defensive
        candidates.append(
            EnumeratedCandidate(
                smiles=Chem.MolToSmiles(mol),
                label="diastereomer_1",
                stereocenters=n_centers,
                enumerated_centers=0,
                metadata={"source_kind": kind},
            )
        )

    logger.info(
        "enumerated %d distinct diastereomer(s) from %s input "
        "(%d stereocenters, enantiomer dedup=%s)",
        len(candidates),
        kind,
        n_centers,
        opts.dedup_enantiomers,
    )
    return candidates


def enumerate_to_smiles(
    source: str | Path,
    *,
    stereocenters: str | list[str] | None = None,
    options: EnumerateOptions | None = None,
) -> list[str]:
    """Convenience wrapper: return just the canonical SMILES list."""
    return [
        c.smiles for c in enumerate_candidates(source, stereocenters=stereocenters, options=options)
    ]


def _parse_stereocenter_arg(arg: str | list[str] | None) -> list[str]:
    """Normalize a ``--stereocenters`` CLI value into a label list."""
    if arg is None:
        return []
    if isinstance(arg, list):
        return [str(x).strip() for x in arg if str(x).strip()]
    text = str(arg).strip()
    if not text:
        return []
    # tolerate comma / whitespace separation
    parts = [p.strip() for p in text.replace(";", ",").split(",")]
    return [p for p in parts if p]


__all__ = [
    "EnumerateOptions",
    "EnumeratedCandidate",
    "enumerate_candidates",
    "enumerate_to_smiles",
]
