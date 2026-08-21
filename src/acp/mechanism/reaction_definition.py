"""Locked reaction-definition models and reaction.json persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._helpers import write_json_atomic
from .atom_mapping import MappingResult, map_reactant_to_product
from .bond_changes import (
    BondChange,
    bond_changes_from_dicts,
    bond_changes_to_dicts,
    compute_bond_changes,
    manual_bond_changes_to_records,
)

MECHANISM_SCHEMA_VERSION = 2


class MappingConfirmationRequired(ValueError):  # noqa: N818
    """Raised when a locked reaction definition needs user mapping confirmation."""

    def __init__(self, mapping_result: MappingResult):
        super().__init__(mapping_result.message or "Mapping confirmation required")
        self.mapping_result = mapping_result


@dataclass(frozen=True)
class RoleSpec:
    """Transport-only stationary-role descriptor."""

    path: str | None = None
    smiles: str | None = None
    asset_id: str | None = None
    charge: int = 0
    multiplicity: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "smiles": self.smiles,
            "asset_id": self.asset_id,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoleSpec:
        return cls(
            path=(None if data.get("path") is None else str(data.get("path"))),
            smiles=(None if data.get("smiles") is None else str(data.get("smiles"))),
            asset_id=(None if data.get("asset_id") is None else str(data.get("asset_id"))),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
        )


@dataclass(frozen=True)
class AtomMapPair:
    """JSON-trivial reactant/product atom-index pair."""

    reactant_index: int
    product_index: int

    def to_dict(self) -> dict[str, int]:
        return {
            "reactant_index": self.reactant_index,
            "product_index": self.product_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtomMapPair:
        return cls(
            reactant_index=int(data.get("reactant_index") or 0),
            product_index=int(data.get("product_index") or 0),
        )


@dataclass(frozen=True)
class ReactionDefinition:
    """Immutable, locked reaction-definition payload stored as reaction.json."""

    study_id: str
    reactant: RoleSpec
    product: RoleSpec
    ts_guess: RoleSpec | None
    atom_mapping: tuple[AtomMapPair, ...]
    bond_changes: tuple[BondChange, ...]
    schema_version: int = MECHANISM_SCHEMA_VERSION
    index_base: int = 0
    content_hash: str = ""
    locked_at: str = ""
    confirmed_by: str = "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "reactant": self.reactant.to_dict(),
            "product": self.product.to_dict(),
            "ts_guess": self.ts_guess.to_dict() if self.ts_guess is not None else None,
            "atom_mapping": [pair.to_dict() for pair in self.atom_mapping],
            "bond_changes": bond_changes_to_dicts(self.bond_changes),
            "index_base": self.index_base,
            "content_hash": self.content_hash,
            "locked_at": self.locked_at,
            "confirmed_by": self.confirmed_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactionDefinition:
        reactant = data.get("reactant") or {}
        product = data.get("product") or {}
        ts_guess = data.get("ts_guess")
        return cls(
            schema_version=int(data.get("schema_version") or MECHANISM_SCHEMA_VERSION),
            study_id=str(data.get("study_id") or ""),
            reactant=RoleSpec.from_dict(dict(reactant)),
            product=RoleSpec.from_dict(dict(product)),
            ts_guess=RoleSpec.from_dict(dict(ts_guess)) if isinstance(ts_guess, dict) else None,
            atom_mapping=tuple(
                AtomMapPair.from_dict(dict(item))
                for item in data.get("atom_mapping") or []
                if isinstance(item, dict)
            ),
            bond_changes=tuple(
                bond_changes_from_dicts(
                    [
                        dict(item)
                        for item in data.get("bond_changes") or []
                        if isinstance(item, dict)
                    ]
                )
            ),
            index_base=int(data.get("index_base") or 0),
            content_hash=str(data.get("content_hash") or ""),
            locked_at=str(data.get("locked_at") or ""),
            confirmed_by=str(data.get("confirmed_by") or "user"),
        )


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Return a stable SHA256 hash over canonical JSON content."""

    canonical = _canonicalize(payload)
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_reaction_json(study_dir: Path, definition: ReactionDefinition) -> Path:
    """Write reaction.json atomically at the mechanism-study root."""

    path = Path(study_dir) / "reaction.json"
    payload = definition.to_dict()
    payload["content_hash"] = compute_content_hash(_payload_without_hash(payload))
    write_json_atomic(path, payload)
    return path


def read_reaction_json(study_dir: Path) -> ReactionDefinition | None:
    """Read and validate reaction.json when present."""

    path = Path(study_dir) / "reaction.json"
    if not path.exists():
        return None
    return validate_reaction_json(path)


def validate_reaction_json(
    path: Path,
    expected_hash: str | None = None,
) -> ReactionDefinition:
    """Validate stored reaction.json content and return the parsed definition."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"reaction.json must contain a JSON object: {path}")
    actual_hash = compute_content_hash(_payload_without_hash(payload))
    stored_hash = str(payload.get("content_hash") or "")
    if not stored_hash:
        raise ValueError(f"reaction.json is missing content_hash: {path}")
    if stored_hash != actual_hash:
        raise ValueError(
            f"reaction.json hash mismatch: stored={stored_hash!r}, actual={actual_hash!r}"
        )
    if expected_hash is not None and stored_hash != expected_hash:
        raise ValueError(
            f"reaction.json did not match expected hash {expected_hash!r}: {stored_hash!r}"
        )
    definition = ReactionDefinition.from_dict(payload)
    if definition.schema_version != MECHANISM_SCHEMA_VERSION:
        raise ValueError(
            "reaction.json schema_version must be "
            f"{MECHANISM_SCHEMA_VERSION}, got {definition.schema_version}"
        )
    if definition.index_base != 0:
        raise ValueError(f"reaction.json index_base must be 0, got {definition.index_base}")
    return definition


def build_reaction_definition(
    study_id: str,
    reactant: RoleSpec,
    product: RoleSpec,
    ts_guess: RoleSpec | None,
    reactant_symbols: Sequence[str],
    reactant_coords: Sequence[Sequence[float]],
    product_symbols: Sequence[str],
    product_coords: Sequence[Sequence[float]],
    *,
    reactant_smiles: str | None = None,
    product_smiles: str | None = None,
    selected_candidate: int | None = None,
    confirmed_by: str = "user",
    manual_bond_changes: Sequence[dict[str, Any]] | None = None,
    resolve_mapping: bool = True,
) -> ReactionDefinition:
    """Build a locked reaction definition from endpoint geometries.

    When ``resolve_mapping`` is False, ``map_reactant_to_product`` is skipped
    entirely (manual-only mode): ``atom_mapping`` stays empty and bond changes
    come solely from ``manual_bond_changes`` with ``product_atoms`` unresolved
    (``None``); no :class:`MappingConfirmationRequired` can be raised.

    When ``manual_bond_changes`` is provided with mapping resolution enabled,
    automatic ``compute_bond_changes`` is skipped and authoritative records are
    built from the manual entries; the atom mapping is still computed because
    ``product_atoms``/``distance_after`` resolution and downstream stages need
    it. Each entry defines a bond in reactant space
    (``{"reactant_atoms": [i, j], "change_type": "break"|"form"}``), in
    product space (``{"product_atoms": [i, j], ...}``), or both; at least one
    side is required. Invalid entries raise ValueError.
    """

    if resolve_mapping:
        mapping_result = map_reactant_to_product(
            reactant_symbols,
            reactant_coords,
            product_symbols,
            product_coords,
            charge=int(reactant.charge),
            reactant_smiles=reactant_smiles or reactant.smiles,
            product_smiles=product_smiles or product.smiles,
        )
        if mapping_result.status == "failed":
            raise ValueError(mapping_result.message or "Atom mapping failed")

        if mapping_result.status == "unique":
            candidate_index = 0
        else:
            if selected_candidate is None:
                raise MappingConfirmationRequired(mapping_result)
            if selected_candidate < 0 or selected_candidate >= len(mapping_result.candidates):
                raise ValueError(
                    f"Selected atom-mapping candidate {selected_candidate} is out of range"
                )
            candidate_index = int(selected_candidate)

        mapping_pairs = list(mapping_result.candidates[candidate_index].mapping)
    else:
        mapping_pairs = []

    if not resolve_mapping or manual_bond_changes is not None:
        bond_changes: list[BondChange] = manual_bond_changes_to_records(
            list(manual_bond_changes or []),
            n_reactant_atoms=len(reactant_symbols),
            reactant_coords=reactant_coords,
            product_coords=product_coords,
            mapping=mapping_pairs,
            n_product_atoms=len(product_symbols),
        )
    else:
        bond_changes = compute_bond_changes(
            reactant_symbols,
            reactant_coords,
            product_symbols,
            product_coords,
            mapping_pairs,
            reactant_smiles=reactant_smiles or reactant.smiles,
            product_smiles=product_smiles or product.smiles,
            charge=int(reactant.charge),
        )
    locked_at = _utc_now_iso()
    manual_mode = not resolve_mapping or manual_bond_changes is not None
    definition = ReactionDefinition(
        study_id=study_id,
        reactant=reactant,
        product=product,
        ts_guess=ts_guess,
        atom_mapping=tuple(
            AtomMapPair(reactant_index=reactant_index, product_index=product_index)
            for reactant_index, product_index in mapping_pairs
        ),
        bond_changes=tuple(bond_changes),
        locked_at=locked_at,
        confirmed_by="user_manual" if manual_mode else confirmed_by,
    )
    payload = definition.to_dict()
    return replace(
        definition,
        content_hash=compute_content_hash(_payload_without_hash(payload)),
    )


def _payload_without_hash(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.pop("content_hash", None)
    return normalized


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "AtomMapPair",
    "MECHANISM_SCHEMA_VERSION",
    "MappingConfirmationRequired",
    "ReactionDefinition",
    "RoleSpec",
    "build_reaction_definition",
    "compute_content_hash",
    "read_reaction_json",
    "validate_reaction_json",
    "write_reaction_json",
]
