from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StructureAsset:
    asset_id: str
    name: str
    source_type: str
    original_format: str
    xyz: str | None = None
    molfile: str | None = None
    has_3d: bool = False
    charge: int = 0
    multiplicity: int = 1
    atom_count: int = 0
    formula: str = ""
    smiles: str | None = None
    normalized_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        return not self.errors and self.atom_count > 0


@dataclass
class StructureParseResult:
    structures: list[StructureAsset] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.structures) and not self.errors


__all__ = ["StructureAsset", "StructureParseResult"]
