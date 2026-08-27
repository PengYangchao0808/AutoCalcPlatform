"""Batch input and execution records."""
# pyright: basic, reportArgumentType=false, reportUnusedCallResult=false

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias

from acp.calculations.contracts import StructureRole

from ._tag import kind_for_tag, normalize_tag, role_for_tag

if TYPE_CHECKING:
    from collections.abc import Mapping

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | Mapping[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


def _set_tag(item: BatchStructureItem | BatchCalculationItem, tag: str | None) -> None:
    item.tag = normalize_tag(tag) or "INT"
    item.role = StructureRole.TRANSITION_STATE if item.tag == "TS" else StructureRole.MINIMUM


@dataclass(slots=True)
class BatchStructureItem:
    """One structure input; mutability preserves user override behavior."""

    item_id: str
    name: str
    tag: str = "INT"
    role: StructureRole | str | None = None
    xyz: str = ""
    candidate_id: str = ""
    source_type: str = "upload"
    source_ref: str = ""
    charge: int | None = None
    multiplicity: int | None = None
    atom_count: int = 0
    formula: str = ""
    include: bool = True

    def __post_init__(self) -> None:
        role_tag = normalize_tag(self.role if isinstance(self.role, (str, StructureRole)) else None)
        _set_tag(self, role_tag or self.tag)

    @property
    def kind(self) -> str:
        """Return the calculation kind represented by this input."""
        return kind_for_tag(self.tag)

    def resolved_charge(self, default: int) -> int:
        """Resolve the item charge against a job default."""
        return self.charge if self.charge is not None else int(default)

    def resolved_multiplicity(self, default: int) -> int:
        """Resolve the item multiplicity against a job default."""
        return self.multiplicity if self.multiplicity is not None else int(default)

    def to_dict(self) -> JsonObject:
        """Serialize this input item."""
        return {
            "item_id": self.item_id,
            "name": self.name,
            "tag": self.tag,
            "kind": self.kind,
            "role": role_for_tag(self.tag),
            "candidate_id": self.candidate_id or self.item_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "atom_count": self.atom_count,
            "formula": self.formula,
            "include": self.include,
            "xyz": self.xyz,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> BatchStructureItem:
        """Parse an input item mapping."""
        return cls(
            item_id=str(payload.get("item_id") or payload.get("id") or ""),
            name=str(payload.get("name") or payload.get("item_id") or payload.get("id") or ""),
            tag=str(payload.get("tag") or "INT"),
            role=payload.get("role") if isinstance(payload.get("role"), str) else None,
            xyz=str(payload.get("xyz") or ""),
            candidate_id=str(payload.get("candidate_id") or ""),
            source_type=str(payload.get("source_type") or "upload"),
            source_ref=str(payload.get("source_ref") or ""),
            charge=int(payload["charge"]) if payload.get("charge") is not None else None,
            multiplicity=(
                int(payload["multiplicity"]) if payload.get("multiplicity") is not None else None
            ),
            atom_count=int(payload.get("atom_count") or 0),
            formula=str(payload.get("formula") or ""),
            include=bool(payload.get("include", True)),
        )


@dataclass(slots=True)
class BatchCalculationItem:
    """One mutable batch execution record."""

    item_id: str
    candidate_id: str
    name: str
    tag: str
    role: StructureRole | str | None = None
    status: str = "pending"
    input_xyz: str = ""
    optimized_xyz: str = ""
    charge: int = 0
    multiplicity: int = 1
    source_type: str = ""
    source_ref: str = ""
    cache_key: str = ""
    frequency: dict[str, JsonValue] = field(default_factory=dict)
    single_point: dict[str, JsonValue] = field(default_factory=dict)
    thermochemistry: dict[str, JsonValue] = field(default_factory=dict)
    error: str = ""
    work_dir: str = ""

    def __post_init__(self) -> None:
        role_tag = normalize_tag(self.role if isinstance(self.role, (str, StructureRole)) else None)
        _set_tag(self, role_tag or self.tag)

    @property
    def kind(self) -> str:
        """Return the calculation kind represented by this record."""
        return kind_for_tag(self.tag)

    @classmethod
    def from_item(
        cls, item: BatchStructureItem, charge: int, multiplicity: int
    ) -> BatchCalculationItem:
        """Create an execution record from an input item."""
        return cls(
            item_id=item.item_id,
            candidate_id=item.candidate_id or item.item_id,
            name=item.name,
            tag=item.tag,
            role=item.role,
            charge=item.resolved_charge(charge),
            multiplicity=item.resolved_multiplicity(multiplicity),
            source_type=item.source_type,
            source_ref=item.source_ref,
        )

    def to_dict(self) -> JsonObject:
        """Serialize this execution record."""
        return {
            "item_id": self.item_id,
            "candidate_id": self.candidate_id,
            "name": self.name,
            "tag": self.tag,
            "kind": self.kind,
            "role": role_for_tag(self.tag),
            "status": self.status,
            "input_xyz": self.input_xyz,
            "optimized_xyz": self.optimized_xyz,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "cache_key": self.cache_key,
            "frequency": dict(self.frequency),
            "single_point": dict(self.single_point),
            "thermochemistry": dict(self.thermochemistry),
            "error": self.error,
            "work_dir": self.work_dir,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, JsonValue]) -> BatchCalculationItem:
        """Parse an execution record mapping."""

        def mapping_value(key: str) -> dict[str, JsonValue]:
            value = payload.get(key)
            return dict(value) if isinstance(value, dict) else {}

        return cls(
            item_id=str(payload.get("item_id") or ""),
            candidate_id=str(payload.get("candidate_id") or payload.get("item_id") or ""),
            name=str(payload.get("name") or ""),
            tag=str(payload.get("tag") or "INT"),
            role=payload.get("role") if isinstance(payload.get("role"), str) else None,
            status=str(payload.get("status") or "pending"),
            input_xyz=str(payload.get("input_xyz") or ""),
            optimized_xyz=str(payload.get("optimized_xyz") or ""),
            charge=int(payload.get("charge") or 0),
            multiplicity=int(payload.get("multiplicity") or 1),
            source_type=str(payload.get("source_type") or ""),
            source_ref=str(payload.get("source_ref") or ""),
            cache_key=str(payload.get("cache_key") or ""),
            frequency=mapping_value("frequency"),
            single_point=mapping_value("single_point"),
            thermochemistry=mapping_value("thermochemistry"),
            error=str(payload.get("error") or ""),
            work_dir=str(payload.get("work_dir") or ""),
        )


def item_cache_key(item: BatchStructureItem, profile_key: str) -> str:
    """Return a stable cache key for profile, identity, TAG, and geometry."""
    values = (
        str(profile_key),
        item.candidate_id,
        item.tag,
        str(item.resolved_charge(0)),
        str(item.resolved_multiplicity(1)),
        item.xyz.strip(),
    )
    return "sha256:" + hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def apply_user_overrides(
    items: list[BatchStructureItem],
    overrides: Mapping[str, Mapping[str, JsonValue]] | None,
) -> list[BatchStructureItem]:
    """Apply user tag/name/charge/multiplicity/include edits in place."""
    if not overrides:
        return items
    by_key: dict[str, BatchStructureItem] = {}
    for item in items:
        by_key.setdefault(item.item_id, item)
        by_key.setdefault(item.candidate_id, item)
    for key, change in overrides.items():
        item = by_key.get(str(key))
        if item is None:
            continue
        tag = normalize_tag(change.get("tag") if isinstance(change.get("tag"), str) else None)
        role = change.get("role") if isinstance(change.get("role"), str) else None
        tag = tag or normalize_tag(role)
        if tag:
            _set_tag(item, tag)
        if change.get("name"):
            item.name = str(change["name"])
        if change.get("charge") is not None:
            item.charge = int(change["charge"])
        if change.get("multiplicity") is not None:
            item.multiplicity = int(change["multiplicity"])
        if isinstance(change.get("include"), bool):
            item.include = bool(change["include"])
    return items


__all__ = [
    "BatchCalculationItem",
    "BatchStructureItem",
    "JsonObject",
    "JsonValue",
    "apply_user_overrides",
    "item_cache_key",
]
