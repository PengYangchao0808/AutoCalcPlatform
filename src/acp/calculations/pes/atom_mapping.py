"""Cross-state atom mapping for reaction input analysis.

Migrated from ``mechanism/atom_mapping.py``.
All atom indices are 0-based.  Algorithms unchanged — mechanism semantics
stripped.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import permutations
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AtomIdentityMap:
    """Stable atom-identity mapping across structures."""

    uid_to_structure_index: dict[str, int]
    mapping: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid_to_structure_index": dict(self.uid_to_structure_index),
            "mapping": {
                key: {inner_key: int(inner_value) for inner_key, inner_value in inner.items()}
                for key, inner in self.mapping.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtomIdentityMap:
        return cls(
            uid_to_structure_index={
                str(key): int(value)
                for key, value in dict(data.get("uid_to_structure_index") or {}).items()
            },
            mapping={
                str(key): {
                    str(inner_key): int(inner_value)
                    for inner_key, inner_value in inner.items()
                }
                for key, inner in dict(data.get("mapping") or {}).items()
            },
        )

_MAX_CANDIDATES = 8
_CONFIDENCE_TOLERANCE = 1.0e-6
_TIE_BREAK_CONFIDENCE_GAP = 0.05
_TIE_BREAK_NOTE = "minimal_change_tie_break"


@dataclass
class AtomMapCandidate:
    """One reactant→product atom-mapping candidate."""

    mapping: list[tuple[int, int]]
    confidence: float
    method: str
    symmetric_alternatives: int = 0
    notes: list[str] = field(default_factory=list)
    mapping_source: str = ""


@dataclass
class MappingResult:
    """Result bundle for cross-state atom mapping."""

    status: Literal["unique", "candidates", "count_mismatch", "failed"]
    candidates: list[AtomMapCandidate] = field(default_factory=list)
    unmatched_reactant_atoms: list[int] = field(default_factory=list)
    unmatched_product_atoms: list[int] = field(default_factory=list)
    message: str = ""
    mapping_source: str = ""


@dataclass(frozen=True)
class _MolGraph:
    mol: Any
    bond_orders_available: bool
    method: str
    notes: tuple[str, ...]
    canonical_ranks: tuple[int, ...]
    atom_fingerprints: tuple[Any | None, ...]


@dataclass(frozen=True)
class _CandidatePayload:
    mapping: tuple[tuple[int, int], ...]
    confidence: float
    method: str
    symmetry_count: int
    notes: tuple[str, ...]
    core_size: int


@dataclass(frozen=True)
class _AssignmentOption:
    pairs: tuple[tuple[int, int], ...]
    cost: float
    notes: tuple[str, ...] = ()


def map_reactant_to_product(
    reactant_symbols: Sequence[str],
    reactant_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    product_symbols: Sequence[str],
    product_coords: Sequence[Sequence[float]] | NDArray[np.float64],
    charge: int = 0,
    *,
    reactant_smiles: str | None = None,
    product_smiles: str | None = None,
) -> MappingResult:
    """Map reactant atoms onto product atoms with lazy RDKit support.

    Candidate confidence uses a weighted sum of three terms:

    * MCS core coverage ratio (45%) — mapped MCS-core atoms divided by the
      larger focused atom count (heavy atoms when present, otherwise all atoms).
    * Post-mapping geometry agreement (35%) — ``1 / (1 + RMSD)`` after Kabsch
      alignment of mapped heavy atoms (or all mapped atoms when no heavy atoms
      exist).
    * Morgan-environment agreement (20%) — average Tanimoto similarity between
      atom-centered Morgan fingerprints for mapped atom pairs.

    When bond-order perception falls back to connectivity-only graphs the final
    confidence is damped to reflect the lower chemical specificity.

    Provenance ladder (``mapping_source``): ``smiles_atommap`` (user-supplied
    atom-map-numbered SMILES, authoritative) > ``smiles_mcs`` > ``xyz_mcs`` >
    ``connectivity``.

    When candidates tie in confidence (gap < 0.05) a minimal-chemical-change
    tie-break counts break/form bond changes per tied candidate; a unique
    minimum-change winner is promoted to status ``unique``. Ties that persist
    keep status ``candidates`` to force human confirmation.
    """

    normalized_reactant_symbols = _normalize_symbols(reactant_symbols)
    normalized_product_symbols = _normalize_symbols(product_symbols)
    reactant_array = _normalize_coordinates(reactant_coords)
    product_array = _normalize_coordinates(product_coords)
    if len(normalized_reactant_symbols) != len(reactant_array):
        raise ValueError("Reactant symbols/coordinates atom counts do not match")
    if len(normalized_product_symbols) != len(product_array):
        raise ValueError("Product symbols/coordinates atom counts do not match")

    try:
        atommap_pairs = _smiles_atommap_pairs(
            reactant_smiles,
            product_smiles,
            normalized_reactant_symbols,
            normalized_product_symbols,
        )
        if atommap_pairs is not None:
            candidate = AtomMapCandidate(
                mapping=atommap_pairs,
                confidence=1.0,
                method="smiles_atommap",
                symmetric_alternatives=0,
                notes=["authoritative_user_atom_map_numbers"],
                mapping_source="smiles_atommap",
            )
            return MappingResult(
                status="unique",
                candidates=[candidate],
                message="Authoritative atom mapping resolved from atom-map-numbered SMILES",
                mapping_source="smiles_atommap",
            )

        reactant_graph = _build_mol_graph(
            normalized_reactant_symbols,
            reactant_array,
            charge=charge,
            smiles=reactant_smiles,
        )
        product_graph = _build_mol_graph(
            normalized_product_symbols,
            product_array,
            charge=charge,
            smiles=product_smiles,
        )
        raw_candidates = _enumerate_candidates(
            normalized_reactant_symbols,
            reactant_array,
            reactant_graph,
            normalized_product_symbols,
            product_array,
            product_graph,
        )
    except ImportError as exc:
        logger.warning("RDKit atom mapping unavailable: %s", exc)
        return MappingResult(status="failed", message=str(exc))
    except Exception as exc:
        logger.warning("RDKit atom mapping failed; falling back to legacy mapping path")
        logger.debug("RDKit mapping failure details", exc_info=exc)
        return MappingResult(status="failed", message=f"RDKit mapping failed: {exc}")

    if not raw_candidates:
        return MappingResult(
            status="failed",
            message="RDKit MCS search did not yield any atom-mapping candidates",
        )

    candidates = _collapse_candidates(raw_candidates, reactant_graph, product_graph)
    if not candidates:
        return MappingResult(
            status="failed",
            message="RDKit atom mapping candidates could not be ranked",
        )

    best_candidate = candidates[0]
    mapped_reactant = {reactant_index for reactant_index, _ in best_candidate.mapping}
    mapped_product = {product_index for _, product_index in best_candidate.mapping}
    unmatched_reactant = sorted(set(range(len(normalized_reactant_symbols))) - mapped_reactant)
    unmatched_product = sorted(set(range(len(normalized_product_symbols))) - mapped_product)

    count_match = len(normalized_reactant_symbols) == len(normalized_product_symbols)
    composition_match = Counter(normalized_reactant_symbols) == Counter(normalized_product_symbols)
    if unmatched_reactant or unmatched_product or not count_match or not composition_match:
        message = (
            "Atom-count/composition mismatch requires confirmation; "
            f"mapped {len(best_candidate.mapping)} atoms, "
            f"unmatched reactant={unmatched_reactant}, unmatched product={unmatched_product}"
        )
        return MappingResult(
            status="count_mismatch",
            candidates=candidates,
            unmatched_reactant_atoms=unmatched_reactant,
            unmatched_product_atoms=unmatched_product,
            message=message,
            mapping_source=candidates[0].mapping_source,
        )

    candidates, tie_break_resolved = _minimal_change_tie_break(
        candidates,
        reactant_array,
        reactant_graph,
        product_array,
        product_graph,
    )

    if len(candidates) == 1:
        message = "Unique atom mapping resolved by RDKit MCS + geometry ranking"
        return MappingResult(
            status="unique",
            candidates=candidates,
            message=message,
            mapping_source=candidates[0].mapping_source,
        )

    if tie_break_resolved:
        message = (
            "Unique atom mapping resolved by minimal-chemical-change tie-break "
            f"(fewest break/form changes among confidence-tied candidates within "
            f"{_TIE_BREAK_CONFIDENCE_GAP:g})"
        )
        return MappingResult(
            status="unique",
            candidates=candidates,
            message=message,
            mapping_source=candidates[0].mapping_source,
        )

    message = (
        "Multiple atom-mapping candidates remain after RDKit ranking; "
        "human confirmation is recommended"
    )
    return MappingResult(
        status="candidates",
        candidates=candidates,
        message=message,
        mapping_source=candidates[0].mapping_source,
    )


def to_atom_identity_map(
    candidate: AtomMapCandidate,
    reactant_state_id: str,
    product_state_id: str,
    n_reactant_atoms: int,
) -> AtomIdentityMap:
    """Materialize a mapping candidate into the legacy AtomIdentityMap shape."""

    reactant_mapping = {f"a{index + 1}": index for index in range(n_reactant_atoms)}
    product_mapping: dict[str, int] = {}
    for reactant_index, product_index in candidate.mapping:
        if 0 <= reactant_index < n_reactant_atoms:
            product_mapping[f"a{reactant_index + 1}"] = int(product_index)
    return AtomIdentityMap(
        uid_to_structure_index=dict(reactant_mapping),
        mapping={
            reactant_state_id: dict(reactant_mapping),
            product_state_id: product_mapping,
        },
    )


def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
    normalized = [str(symbol).strip().capitalize() for symbol in symbols]
    if any(not symbol for symbol in normalized):
        raise ValueError("Atomic symbols must not be empty")
    return normalized


def _normalize_coordinates(
    coordinates: Sequence[Sequence[float]] | NDArray[np.float64],
) -> NDArray[np.float64]:
    array = np.asarray(coordinates, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("Coordinates must have shape (N, 3)")
    return array


def _require_rdkit() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from rdkit import Chem, DataStructs
        from rdkit.Chem import rdDetermineBonds, rdFingerprintGenerator, rdFMCS
    except ImportError as exc:
        raise ImportError(
            "RDKit is required for atom mapping. Install with: pip install rdkit"
        ) from exc
    return Chem, DataStructs, rdDetermineBonds, rdFMCS, rdFingerprintGenerator


def _smiles_atommap_pairs(
    reactant_smiles: str | None,
    product_smiles: str | None,
    reactant_symbols: list[str],
    product_symbols: list[str],
) -> list[tuple[int, int]] | None:
    """Extract an authoritative mapping from atom-map-numbered SMILES.

    Returns ``None`` (fall through to the MCS path) unless both SMILES carry
    atom map numbers, the SMILES atom order matches the coordinate XYZ order
    on both sides, and the shared map numbers cover all heavy atoms on both
    sides.
    """

    if not reactant_smiles or not product_smiles:
        return None
    chem_module, _, _, _, _ = _require_rdkit()
    reactant_mol = chem_module.MolFromSmiles(reactant_smiles)
    product_mol = chem_module.MolFromSmiles(product_smiles)
    if reactant_mol is None or product_mol is None:
        return None
    reactant_maps = _atom_map_index(reactant_mol)
    product_maps = _atom_map_index(product_mol)
    if not reactant_maps or not product_maps:
        return None

    if len(reactant_symbols) != reactant_mol.GetNumAtoms():
        logger.debug(
            "Atom-map SMILES path skipped: reactant SMILES has %d atoms but XYZ has %d",
            reactant_mol.GetNumAtoms(),
            len(reactant_symbols),
        )
        return None
    if len(product_symbols) != product_mol.GetNumAtoms():
        logger.debug(
            "Atom-map SMILES path skipped: product SMILES has %d atoms but XYZ has %d",
            product_mol.GetNumAtoms(),
            len(product_symbols),
        )
        return None
    reactant_mol_symbols = [atom.GetSymbol() for atom in reactant_mol.GetAtoms()]
    product_mol_symbols = [atom.GetSymbol() for atom in product_mol.GetAtoms()]
    if reactant_mol_symbols != reactant_symbols or product_mol_symbols != product_symbols:
        logger.debug("Atom-map SMILES path skipped: element sequence mismatch with XYZ order")
        return None

    shared = sorted(set(reactant_maps) & set(product_maps))
    if not shared:
        logger.warning(
            "Atom-map-numbered SMILES supplied but share no map numbers; falling back to MCS"
        )
        return None
    mapped_reactant = {reactant_maps[map_number] for map_number in shared}
    mapped_product = {product_maps[map_number] for map_number in shared}
    reactant_heavy = {index for index, symbol in enumerate(reactant_symbols) if symbol != "H"}
    product_heavy = {index for index, symbol in enumerate(product_symbols) if symbol != "H"}
    if not reactant_heavy <= mapped_reactant or not product_heavy <= mapped_product:
        logger.warning("Atom-map-numbered SMILES do not cover all heavy atoms; falling back to MCS")
        return None

    pairs: list[tuple[int, int]] = []
    for map_number in shared:
        reactant_index = reactant_maps[map_number]
        product_index = product_maps[map_number]
        if reactant_symbols[reactant_index] != product_symbols[product_index]:
            logger.warning(
                "Atom map number %d links mismatched elements (%s → %s); falling back to MCS",
                map_number,
                reactant_symbols[reactant_index],
                product_symbols[product_index],
            )
            return None
        pairs.append((reactant_index, product_index))
    return sorted(pairs)


def _atom_map_index(mol: Any) -> dict[int, int]:
    """Return ``{atom_map_number: 0-based atom index}``; empty when unmapped."""

    index: dict[int, int] = {}
    duplicates: set[int] = set()
    for atom in mol.GetAtoms():
        map_number = int(atom.GetAtomMapNum())
        if map_number <= 0:
            continue
        if map_number in index:
            duplicates.add(map_number)
        index[map_number] = atom.GetIdx()
    if duplicates:
        logger.warning(
            "Duplicate atom map numbers %s; ignoring atom-map SMILES", sorted(duplicates)
        )
        return {}
    return index


def _build_mol_graph(
    symbols: list[str],
    coordinates: NDArray[np.float64],
    *,
    charge: int,
    smiles: str | None,
) -> _MolGraph:
    chem_module, _, rd_determine_bonds, _, rd_fingerprint_generator = _require_rdkit()
    notes: list[str] = []
    mol: Any | None = None
    bond_orders_available = False
    method = "rdfmcs_compareany_v1"

    if smiles:
        try:
            mol = _mol_from_smiles(chem_module, smiles, symbols)
            if mol is not None:
                bond_orders_available = True
                method = "rdfmcs_compareany_smiles_v1"
        except Exception as exc:
            logger.debug("SMILES-based graph build failed: %s", exc, exc_info=exc)
            notes.append(f"smiles_graph_failed:{type(exc).__name__}")

    if mol is None:
        try:
            mol = _mol_from_xyz(
                chem_module,
                rd_determine_bonds,
                symbols,
                coordinates,
                charge=charge,
            )
            bond_orders_available = True
            method = "rdfmcs_compareany_xyz_v1"
        except Exception as exc:
            logger.warning(
                "RDKit bond-order perception failed; degrading to connectivity-only graph"
            )
            logger.debug("XYZ bond-order perception failure", exc_info=exc)
            notes.append(f"xyz_bond_orders_failed:{type(exc).__name__}")

    if mol is None:
        mol = _connectivity_mol(chem_module, symbols, coordinates)
        bond_orders_available = False
        method = "rdfmcs_compareany_connectivity_v1"
        notes.append("connectivity_graph_only")
    assert mol is not None

    generator = rd_fingerprint_generator.GetMorganGenerator(radius=2, fpSize=256)
    try:
        canonical_ranks = tuple(
            int(rank) for rank in chem_module.CanonicalRankAtoms(mol, breakTies=False)
        )
    except Exception as exc:
        logger.debug("Canonical atom ranking failed; defaulting to zero ranks", exc_info=exc)
        notes.append(f"canonical_ranks_failed:{type(exc).__name__}")
        canonical_ranks = tuple(0 for _ in symbols)
    atom_fingerprints = tuple(
        generator.GetFingerprint(mol, fromAtoms=[atom_index]) if mol.GetNumAtoms() else None
        for atom_index in range(mol.GetNumAtoms())
    )
    return _MolGraph(
        mol=mol,
        bond_orders_available=bond_orders_available,
        method=method,
        notes=tuple(notes),
        canonical_ranks=canonical_ranks,
        atom_fingerprints=atom_fingerprints,
    )


def _mol_from_smiles(chem_module: Any, smiles: str, expected_symbols: list[str]) -> Any | None:
    base = chem_module.MolFromSmiles(smiles)
    if base is None:
        raise ValueError(f"Invalid SMILES: {smiles}")
    candidates = [base, chem_module.AddHs(base)]
    for mol in candidates:
        if mol.GetNumAtoms() != len(expected_symbols):
            continue
        symbols = [mol.GetAtomWithIdx(index).GetSymbol() for index in range(mol.GetNumAtoms())]
        if symbols == expected_symbols:
            return mol
    raise ValueError("SMILES atom ordering does not match the supplied coordinate symbols")


def _mol_from_xyz(
    chem_module: Any,
    rd_determine_bonds: Any,
    symbols: list[str],
    coordinates: NDArray[np.float64],
    *,
    charge: int,
) -> Any:
    block = _xyz_block(symbols, coordinates)
    mol = chem_module.MolFromXYZBlock(block)
    if mol is None:
        raise ValueError("RDKit could not parse the XYZ block")
    rd_determine_bonds.DetermineBonds(mol, charge=int(charge))
    rd_determine_bonds.DetermineBondOrders(mol, charge=int(charge))
    chem_module.SanitizeMol(mol)
    return mol


def _connectivity_mol(
    chem_module: Any,
    symbols: list[str],
    coordinates: NDArray[np.float64],
) -> Any:
    rwmol = chem_module.RWMol()
    for symbol in symbols:
        rwmol.AddAtom(chem_module.Atom(symbol))
    for left, right in sorted(_perceive_connectivity(chem_module, symbols, coordinates)):
        rwmol.AddBond(int(left), int(right), chem_module.BondType.SINGLE)
    mol = rwmol.GetMol()
    try:
        chem_module.SanitizeMol(mol)
    except Exception as exc:
        logger.debug(
            "Connectivity-graph sanitization failed; using loose property cache",
            exc_info=exc,
        )
        mol.UpdatePropertyCache(strict=False)
    return mol


def _perceive_connectivity(
    chem_module: Any,
    symbols: list[str],
    coordinates: NDArray[np.float64],
    *,
    covalent_scale: float = 1.20,
    covalent_tolerance: float = 0.10,
) -> set[tuple[int, int]]:
    periodic_table = chem_module.GetPeriodicTable()
    edges: set[tuple[int, int]] = set()
    default_radius = 0.77
    for left, symbol_left in enumerate(symbols):
        try:
            radius_left = periodic_table.GetRcovalent(periodic_table.GetAtomicNumber(symbol_left))
        except Exception:
            radius_left = default_radius
        for right in range(left + 1, len(symbols)):
            try:
                radius_right = periodic_table.GetRcovalent(
                    periodic_table.GetAtomicNumber(symbols[right])
                )
            except Exception:
                radius_right = default_radius
            cutoff = covalent_scale * (radius_left + radius_right) + covalent_tolerance
            distance = float(np.linalg.norm(coordinates[left] - coordinates[right]))
            if distance <= cutoff:
                edges.add((left, right))
    return edges


def _xyz_block(symbols: list[str], coordinates: NDArray[np.float64]) -> str:
    lines = [str(len(symbols)), "generated_by=acp.calculations.pes.atom_mapping"]
    for symbol, row in zip(symbols, coordinates):
        lines.append(f"{symbol} {row[0]:.10f} {row[1]:.10f} {row[2]:.10f}")
    return "\n".join(lines) + "\n"


def _enumerate_candidates(
    reactant_symbols: list[str],
    reactant_coords: NDArray[np.float64],
    reactant_graph: _MolGraph,
    product_symbols: list[str],
    product_coords: NDArray[np.float64],
    product_graph: _MolGraph,
) -> list[_CandidatePayload]:
    core_maps = _enumerate_core_maps(
        reactant_symbols,
        reactant_graph,
        product_symbols,
        product_graph,
    )
    candidates: list[_CandidatePayload] = []
    for core_mapping, symmetry_count, core_size in core_maps:
        for full_mapping, notes in _complete_mapping(
            core_mapping,
            reactant_symbols,
            reactant_coords,
            reactant_graph,
            product_symbols,
            product_coords,
            product_graph,
        ):
            confidence = _mapping_confidence(
                full_mapping,
                core_size,
                reactant_coords,
                reactant_graph,
                product_coords,
                product_graph,
                reactant_symbols,
            )
            all_notes = tuple(dict.fromkeys((*reactant_graph.notes, *product_graph.notes, *notes)))
            candidates.append(
                _CandidatePayload(
                    mapping=tuple(sorted(full_mapping.items())),
                    confidence=confidence,
                    method=(
                        "rdfmcs_compareany_v1"
                        if reactant_graph.method == product_graph.method
                        else f"{reactant_graph.method}+{product_graph.method}"
                    ),
                    symmetry_count=symmetry_count,
                    notes=all_notes,
                    core_size=core_size,
                )
            )
    return candidates


def _enumerate_core_maps(
    reactant_symbols: list[str],
    reactant_graph: _MolGraph,
    product_symbols: list[str],
    product_graph: _MolGraph,
) -> list[tuple[dict[int, int], int, int]]:
    chem_module, _, _, rd_fmcs, _ = _require_rdkit()
    reactant_focus, reactant_parents = _focus_mol(reactant_symbols, reactant_graph.mol)
    product_focus, product_parents = _focus_mol(product_symbols, product_graph.mol)
    mcs = rd_fmcs.FindMCS(
        [reactant_focus, product_focus],
        atomCompare=rd_fmcs.AtomCompare.CompareElements,
        bondCompare=rd_fmcs.BondCompare.CompareAny,
        ringMatchesRingOnly=False,
        completeRingsOnly=False,
        timeout=10,
    )
    if mcs.numAtoms <= 0:
        return []
    query = chem_module.MolFromSmarts(mcs.smartsString)
    if query is None:
        return []
    reactant_matches = reactant_focus.GetSubstructMatches(query, uniquify=False, maxMatches=64)
    product_matches = product_focus.GetSubstructMatches(query, uniquify=False, maxMatches=64)
    if not reactant_matches or not product_matches:
        return []

    dedup: dict[tuple[tuple[int, int], ...], tuple[dict[int, int], int, int]] = {}
    for reactant_match in reactant_matches:
        for product_match in product_matches:
            mapping = {
                reactant_parents[reactant_index]: product_parents[product_index]
                for reactant_index, product_index in zip(reactant_match, product_match)
            }
            key = tuple(sorted(mapping.items()))
            existing = dedup.get(key)
            if existing is None:
                dedup[key] = (mapping, 1, len(mapping))
            else:
                dedup[key] = (existing[0], existing[1] + 1, existing[2])
    return sorted(dedup.values(), key=lambda item: (-item[2], tuple(sorted(item[0].items()))))


def _focus_mol(symbols: list[str], mol: Any) -> tuple[Any, tuple[int, ...]]:
    heavy_indices = tuple(index for index, symbol in enumerate(symbols) if symbol != "H")
    if heavy_indices:
        return _submol(mol, heavy_indices)
    return mol, tuple(range(len(symbols)))


def _submol(mol: Any, atom_indices: tuple[int, ...]) -> tuple[Any, tuple[int, ...]]:
    chem_module, _, _, _, _ = _require_rdkit()
    if len(atom_indices) == mol.GetNumAtoms():
        return mol, atom_indices
    index_map = {parent_index: child_index for child_index, parent_index in enumerate(atom_indices)}
    rwmol = chem_module.RWMol()
    for parent_index in atom_indices:
        atom = mol.GetAtomWithIdx(int(parent_index))
        clone = chem_module.Atom(atom.GetAtomicNum())
        clone.SetFormalCharge(atom.GetFormalCharge())
        clone.SetIsAromatic(atom.GetIsAromatic())
        rwmol.AddAtom(clone)
    for bond in mol.GetBonds():
        begin = bond.GetBeginAtomIdx()
        end = bond.GetEndAtomIdx()
        if begin in index_map and end in index_map:
            rwmol.AddBond(index_map[begin], index_map[end], bond.GetBondType())
    submol = rwmol.GetMol()
    try:
        chem_module.SanitizeMol(submol)
    except Exception as exc:
        logger.debug("Focused submol sanitization failed", exc_info=exc)
        submol.UpdatePropertyCache(strict=False)
    return submol, atom_indices


def _complete_mapping(
    core_mapping: dict[int, int],
    reactant_symbols: list[str],
    reactant_coords: NDArray[np.float64],
    reactant_graph: _MolGraph,
    product_symbols: list[str],
    product_coords: NDArray[np.float64],
    product_graph: _MolGraph,
) -> list[tuple[dict[int, int], tuple[str, ...]]]:
    variants: list[tuple[dict[int, int], float, tuple[str, ...]]] = [(dict(core_mapping), 0.0, ())]
    remaining_reactant = [
        index for index in range(len(reactant_symbols)) if index not in core_mapping
    ]
    remaining_product = [
        index for index in range(len(product_symbols)) if index not in core_mapping.values()
    ]
    if not remaining_reactant or not remaining_product:
        return [(dict(core_mapping), ())]

    grouped_reactant = _group_unmatched_atoms(
        remaining_reactant,
        reactant_graph,
        core_mapping,
        reactant_symbols,
    )
    grouped_product = _group_unmatched_atoms(
        remaining_product,
        product_graph,
        _invert_mapping(core_mapping),
        product_symbols,
    )
    processed_reactant: set[int] = set()
    processed_product: set[int] = set()
    shared_keys = sorted(set(grouped_reactant) & set(grouped_product), key=str)
    for key in shared_keys:
        reactant_group = grouped_reactant[key]
        product_group = grouped_product[key]
        options = _assignment_options(
            reactant_group,
            product_group,
            core_mapping,
            reactant_graph,
            product_graph,
            reactant_coords,
            product_coords,
            reactant_symbols,
            product_symbols,
        )
        if not options:
            continue
        processed_reactant.update(reactant_group)
        processed_product.update(product_group)
        variants = _combine_variants(variants, options)

    remaining_reactant = sorted(
        index for index in remaining_reactant if index not in processed_reactant
    )
    remaining_product = sorted(
        index for index in remaining_product if index not in processed_product
    )
    grouped_reactant_by_symbol = _group_by_symbol(remaining_reactant, reactant_symbols)
    grouped_product_by_symbol = _group_by_symbol(remaining_product, product_symbols)
    for symbol in sorted(set(grouped_reactant_by_symbol) & set(grouped_product_by_symbol)):
        reactant_group = grouped_reactant_by_symbol[symbol]
        product_group = grouped_product_by_symbol[symbol]
        options = _assignment_options(
            reactant_group,
            product_group,
            core_mapping,
            reactant_graph,
            product_graph,
            reactant_coords,
            product_coords,
            reactant_symbols,
            product_symbols,
        )
        if not options:
            continue
        variants = _combine_variants(variants, options)

    completed: list[tuple[dict[int, int], tuple[str, ...]]] = []
    for mapping, _, notes in variants[:_MAX_CANDIDATES]:
        completed.append((mapping, notes))
    if not completed:
        return [(dict(core_mapping), ("partial_mapping_only",))]
    return completed


def _group_unmatched_atoms(
    atom_indices: list[int],
    graph: _MolGraph,
    mapped_neighbors: dict[int, int],
    symbols: list[str],
) -> dict[tuple[Any, ...], list[int]]:
    groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for atom_index in atom_indices:
        mapped_signature: list[tuple[int, float]] = []
        atom = graph.mol.GetAtomWithIdx(int(atom_index))
        for neighbor in atom.GetNeighbors():
            neighbor_index = neighbor.GetIdx()
            if neighbor_index not in mapped_neighbors:
                continue
            label = int(mapped_neighbors[neighbor_index])
            bond = graph.mol.GetBondBetweenAtoms(int(atom_index), int(neighbor_index))
            bond_order = float(bond.GetBondTypeAsDouble()) if bond is not None else 1.0
            mapped_signature.append((label, bond_order))
        groups[
            (
                symbols[atom_index],
                tuple(sorted(mapped_signature)),
                (
                    int(graph.canonical_ranks[atom_index])
                    if atom_index < len(graph.canonical_ranks)
                    else 0
                ),
            )
        ].append(int(atom_index))
    return groups


def _group_by_symbol(atom_indices: list[int], symbols: list[str]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for atom_index in atom_indices:
        groups[symbols[atom_index]].append(int(atom_index))
    return groups


def _assignment_options(
    reactant_group: list[int],
    product_group: list[int],
    core_mapping: dict[int, int],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
    reactant_coords: NDArray[np.float64],
    product_coords: NDArray[np.float64],
    reactant_symbols: list[str],
    product_symbols: list[str],
) -> list[_AssignmentOption]:
    if len(reactant_group) != len(product_group) or not reactant_group:
        return []
    if len(reactant_group) == 1:
        return [_AssignmentOption(pairs=((reactant_group[0], product_group[0]),), cost=0.0)]

    symbol = reactant_symbols[reactant_group[0]]
    if len(reactant_group) > 4 or symbol == "H":
        assignment = _best_assignment(
            reactant_group,
            product_group,
            core_mapping,
            reactant_graph,
            product_graph,
            reactant_coords,
            product_coords,
        )
        return [assignment] if assignment is not None else []

    scored: list[_AssignmentOption] = []
    for candidate_order in permutations(product_group):
        pairs = tuple(zip(reactant_group, candidate_order))
        cost = sum(
            _atom_assignment_cost(
                reactant_index,
                product_index,
                core_mapping,
                reactant_graph,
                product_graph,
                reactant_coords,
                product_coords,
            )
            for reactant_index, product_index in pairs
        )
        scored.append(_AssignmentOption(pairs=pairs, cost=cost))
    scored.sort(key=lambda item: (item.cost, item.pairs))
    if not scored:
        return []
    best_cost = scored[0].cost
    kept = [item for item in scored if item.cost <= best_cost + 1.0e-6]
    return kept[: min(4, len(kept))]


def _best_assignment(
    reactant_group: list[int],
    product_group: list[int],
    core_mapping: dict[int, int],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
    reactant_coords: NDArray[np.float64],
    product_coords: NDArray[np.float64],
) -> _AssignmentOption | None:
    remaining_product = list(product_group)
    pairs: list[tuple[int, int]] = []
    total_cost = 0.0
    for reactant_index in reactant_group:
        best_product: int | None = None
        best_cost = float("inf")
        for product_index in remaining_product:
            cost = _atom_assignment_cost(
                reactant_index,
                product_index,
                core_mapping,
                reactant_graph,
                product_graph,
                reactant_coords,
                product_coords,
            )
            if cost < best_cost - 1.0e-9 or (
                abs(cost - best_cost) <= 1.0e-9
                and best_product is not None
                and product_index < best_product
            ):
                best_product = int(product_index)
                best_cost = float(cost)
        if best_product is None:
            return None
        remaining_product.remove(best_product)
        pairs.append((int(reactant_index), int(best_product)))
        total_cost += best_cost
    return _AssignmentOption(pairs=tuple(sorted(pairs)), cost=total_cost)


def _combine_variants(
    variants: list[tuple[dict[int, int], float, tuple[str, ...]]],
    options: list[_AssignmentOption],
) -> list[tuple[dict[int, int], float, tuple[str, ...]]]:
    combined: list[tuple[dict[int, int], float, tuple[str, ...]]] = []
    for mapping, base_cost, notes in variants:
        for option in options:
            updated = dict(mapping)
            for reactant_index, product_index in option.pairs:
                updated[int(reactant_index)] = int(product_index)
            option_notes = tuple(dict.fromkeys((*notes, *option.notes)))
            combined.append((updated, base_cost + option.cost, option_notes))
    combined.sort(key=lambda item: (item[1], tuple(sorted(item[0].items()))))
    return combined[:_MAX_CANDIDATES]


def _atom_assignment_cost(
    reactant_index: int,
    product_index: int,
    core_mapping: dict[int, int],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
    reactant_coords: NDArray[np.float64],
    product_coords: NDArray[np.float64],
) -> float:
    distance_penalty = _distance_profile_penalty(
        reactant_index,
        product_index,
        core_mapping,
        reactant_coords,
        product_coords,
    )
    environment_penalty = 1.0 - _atom_environment_similarity(
        reactant_index,
        product_index,
        reactant_graph,
        product_graph,
    )
    degree_penalty = abs(
        reactant_graph.mol.GetAtomWithIdx(int(reactant_index)).GetDegree()
        - product_graph.mol.GetAtomWithIdx(int(product_index)).GetDegree()
    )
    rank_penalty = (
        0.0
        if reactant_graph.canonical_ranks[reactant_index]
        == product_graph.canonical_ranks[product_index]
        else 0.25
    )
    return float(distance_penalty + 0.4 * environment_penalty + 0.1 * degree_penalty + rank_penalty)


def _distance_profile_penalty(
    reactant_index: int,
    product_index: int,
    core_mapping: dict[int, int],
    reactant_coords: NDArray[np.float64],
    product_coords: NDArray[np.float64],
) -> float:
    if not core_mapping:
        return 0.0
    inverse_mapping = _invert_mapping(core_mapping)
    penalties: list[float] = []
    for reactant_anchor, product_anchor in sorted(core_mapping.items()):
        if reactant_anchor == reactant_index or product_anchor == product_index:
            continue
        penalties.append(
            abs(
                GeometryUtils.calculate_distance(reactant_coords, reactant_index, reactant_anchor)
                - GeometryUtils.calculate_distance(product_coords, product_index, product_anchor)
            )
        )
    if not penalties:
        for product_anchor, reactant_anchor in sorted(inverse_mapping.items()):
            if reactant_anchor == reactant_index or product_anchor == product_index:
                continue
            penalties.append(
                abs(
                    GeometryUtils.calculate_distance(
                        reactant_coords,
                        reactant_index,
                        reactant_anchor,
                    )
                    - GeometryUtils.calculate_distance(
                        product_coords,
                        product_index,
                        product_anchor,
                    )
                )
            )
    return float(sum(penalties) / len(penalties)) if penalties else 0.0


def _mapping_confidence(
    mapping: dict[int, int],
    core_size: int,
    reactant_coords: NDArray[np.float64],
    reactant_graph: _MolGraph,
    product_coords: NDArray[np.float64],
    product_graph: _MolGraph,
    reactant_symbols: list[str],
) -> float:
    focus_count = max(
        1,
        sum(1 for symbol in reactant_symbols if symbol != "H") or len(reactant_symbols),
    )
    coverage = min(float(core_size) / float(focus_count), 1.0)
    rmsd = _mapped_rmsd(mapping, reactant_coords, product_coords, reactant_symbols)
    geometry_term = 1.0 / (1.0 + max(rmsd, 0.0))
    environment_term = _mapping_environment_score(mapping, reactant_graph, product_graph)
    confidence = 0.45 * coverage + 0.35 * geometry_term + 0.20 * environment_term
    if not reactant_graph.bond_orders_available or not product_graph.bond_orders_available:
        confidence *= 0.85
    return max(0.0, min(float(confidence), 1.0))


def _mapped_rmsd(
    mapping: dict[int, int],
    reactant_coords: NDArray[np.float64],
    product_coords: NDArray[np.float64],
    reactant_symbols: list[str],
) -> float:
    reactant_indices = [reactant_index for reactant_index, _ in sorted(mapping.items())]
    product_indices = [product_index for _, product_index in sorted(mapping.items())]
    heavy_pairs = [
        (reactant_index, product_index)
        for reactant_index, product_index in zip(reactant_indices, product_indices)
        if reactant_symbols[reactant_index] != "H"
    ]
    if heavy_pairs:
        reactant_indices = [reactant_index for reactant_index, _ in heavy_pairs]
        product_indices = [product_index for _, product_index in heavy_pairs]
    reactant_subset = reactant_coords[reactant_indices]
    product_subset = product_coords[product_indices]
    if len(reactant_subset) == 0:
        return 0.0
    if len(reactant_subset) == 1:
        return float(np.linalg.norm(reactant_subset[0] - product_subset[0]))
    if len(reactant_subset) < 3:
        reactant_centered = reactant_subset - np.mean(reactant_subset, axis=0)
        product_centered = product_subset - np.mean(product_subset, axis=0)
        diff = reactant_centered - product_centered
        return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))
    try:
        aligned = GeometryUtils.align_structures(reactant_subset, product_subset)
    except Exception as exc:
        logger.debug("Kabsch alignment failed; falling back to direct RMSD", exc_info=exc)
        diff = reactant_subset - product_subset
        return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))
    diff = reactant_subset - aligned
    return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))


def _mapping_environment_score(
    mapping: dict[int, int],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
) -> float:
    similarities = [
        _atom_environment_similarity(reactant_index, product_index, reactant_graph, product_graph)
        for reactant_index, product_index in mapping.items()
    ]
    return float(sum(similarities) / len(similarities)) if similarities else 0.0


def _atom_environment_similarity(
    reactant_index: int,
    product_index: int,
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
) -> float:
    _, data_structs, _, _, _ = _require_rdkit()
    reactant_fp = reactant_graph.atom_fingerprints[reactant_index]
    product_fp = product_graph.atom_fingerprints[product_index]
    if reactant_fp is None or product_fp is None:
        return 0.0
    return float(data_structs.TanimotoSimilarity(reactant_fp, product_fp))


def _minimal_change_tie_break(
    candidates: list[AtomMapCandidate],
    reactant_coords: NDArray[np.float64],
    reactant_graph: _MolGraph,
    product_coords: NDArray[np.float64],
    product_graph: _MolGraph,
) -> tuple[list[AtomMapCandidate], bool]:
    """Break confidence ties (< 0.05 gap) by fewest break/form bond changes.

    Returns the (possibly reordered) candidate list and whether a unique
    minimum-change winner was promoted to the front. Ties that persist keep
    the original order so status stays ``candidates``.
    """

    if len(candidates) < 2:
        return candidates, False
    top_confidence = candidates[0].confidence
    tied = [
        candidate
        for candidate in candidates
        if top_confidence - candidate.confidence < _TIE_BREAK_CONFIDENCE_GAP
    ]
    if len(tied) < 2:
        return candidates, False

    from .bond_changes import _bond_changes_from_graphs

    change_counts: list[int] = []
    for candidate in tied:
        changes = _bond_changes_from_graphs(
            reactant_graph,
            product_graph,
            reactant_coords,
            product_coords,
            candidate,
        )
        change_counts.append(
            sum(1 for change in changes if change.change_type in {"break", "form"})
        )
    minimum = min(change_counts)
    winners = [index for index, count in enumerate(change_counts) if count == minimum]
    if len(winners) != 1:
        return candidates, False

    winner = tied[winners[0]]
    if _TIE_BREAK_NOTE not in winner.notes:
        winner.notes.append(f"{_TIE_BREAK_NOTE}:break_form={minimum}")
    reordered = [winner, *(candidate for candidate in candidates if candidate is not winner)]
    return reordered, True


def _mcs_mapping_source(reactant_method: str, product_method: str) -> str:
    if "smiles" in reactant_method and "smiles" in product_method:
        return "smiles_mcs"
    if "xyz" in reactant_method and "xyz" in product_method:
        return "xyz_mcs"
    return "connectivity"


def _collapse_candidates(
    raw_candidates: list[_CandidatePayload],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
) -> list[AtomMapCandidate]:
    by_exact_mapping: dict[tuple[tuple[int, int], ...], _CandidatePayload] = {}
    for candidate in raw_candidates:
        existing = by_exact_mapping.get(candidate.mapping)
        if existing is None or candidate.confidence > existing.confidence + _CONFIDENCE_TOLERANCE:
            by_exact_mapping[candidate.mapping] = candidate
        elif existing is not None and (
            abs(candidate.confidence - existing.confidence) <= _CONFIDENCE_TOLERANCE
        ):
            by_exact_mapping[candidate.mapping] = _CandidatePayload(
                mapping=existing.mapping,
                confidence=existing.confidence,
                method=existing.method,
                symmetry_count=existing.symmetry_count + candidate.symmetry_count,
                notes=tuple(dict.fromkeys((*existing.notes, *candidate.notes))),
                core_size=max(existing.core_size, candidate.core_size),
            )

    exact_candidates = list(by_exact_mapping.values())
    exact_candidates.sort(key=lambda item: (-item.confidence, item.mapping))
    if not exact_candidates:
        return []

    mapping_source = _mcs_mapping_source(reactant_graph.method, product_graph.method)

    grouped: dict[tuple[tuple[int, int], ...], list[_CandidatePayload]] = defaultdict(list)
    for candidate in exact_candidates:
        grouped[_rank_signature(candidate.mapping, reactant_graph, product_graph)].append(candidate)

    if len(grouped) == 1:
        group = next(iter(grouped.values()))
        confidences = {round(item.confidence, 8) for item in group}
        confidence_gap = (
            group[0].confidence - group[1].confidence if len(group) > 1 else group[0].confidence
        )
        if len(confidences) == 1 or confidence_gap >= 0.1:
            representative = group[0]
            return [
                AtomMapCandidate(
                    mapping=[(int(left), int(right)) for left, right in representative.mapping],
                    confidence=representative.confidence,
                    method=representative.method,
                    symmetric_alternatives=sum(item.symmetry_count for item in group),
                    notes=list(representative.notes),
                    mapping_source=mapping_source,
                )
            ]

    collapsed: list[AtomMapCandidate] = []
    for group in grouped.values():
        total_symmetry = sum(item.symmetry_count for item in group)
        for candidate in group:
            collapsed.append(
                AtomMapCandidate(
                    mapping=[(int(left), int(right)) for left, right in candidate.mapping],
                    confidence=candidate.confidence,
                    method=candidate.method,
                    symmetric_alternatives=total_symmetry,
                    notes=list(candidate.notes),
                    mapping_source=mapping_source,
                )
            )
    collapsed.sort(key=lambda item: (-item.confidence, tuple(item.mapping)))
    return collapsed[:_MAX_CANDIDATES]


def _rank_signature(
    mapping: tuple[tuple[int, int], ...],
    reactant_graph: _MolGraph,
    product_graph: _MolGraph,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            (
                int(reactant_graph.canonical_ranks[reactant_index]),
                int(product_graph.canonical_ranks[product_index]),
            )
            for reactant_index, product_index in mapping
        )
    )


def _invert_mapping(mapping: dict[int, int]) -> dict[int, int]:
    return {product_index: reactant_index for reactant_index, product_index in mapping.items()}


__all__ = [
    "AtomIdentityMap",
    "AtomMapCandidate",
    "MappingResult",
    "map_reactant_to_product",
    "to_atom_identity_map",
]
