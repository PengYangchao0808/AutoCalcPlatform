"""NMR domain models."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Return a normalized element symbol."""
    normalized = symbol.strip()
    if not normalized:
        return normalized
    return normalized[:1].upper() + normalized[1:].lower()


@dataclass(frozen=True)
class NMRAtomShielding:
    """Per-atom shielding tensor summary."""

    atom_index: int
    symbol: str
    isotropic_ppm: float
    anisotropy_ppm: float | None = None
    tensor_components_ppm: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))
        object.__setattr__(self, "tensor_components_ppm", dict(self.tensor_components_ppm))


@dataclass(frozen=True)
class NMRAtomShift:
    """Per-atom calibrated chemical shift."""

    atom_index: int
    symbol: str
    nucleus: str | None
    shielding_ppm: float
    reference_ppm: float | None
    shift_ppm: float | None
    anisotropy_ppm: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))


@dataclass
class NMRConformerResult:
    """NMR results for a single conformer."""

    record_id: str
    energy_hartree: float | None
    free_energy_hartree: float | None
    weight: float | None
    log_file: Path
    shieldings: list[NMRAtomShielding] = field(default_factory=list)
    shifts: list[NMRAtomShift] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.log_file = Path(self.log_file)
        self.shieldings = list(self.shieldings)
        self.shifts = list(self.shifts)


@dataclass(frozen=True)
class NMRAveragedAtomResult:
    """Boltzmann-averaged NMR result for one atom."""

    atom_index: int
    symbol: str
    nucleus: str | None
    averaged_shielding_ppm: float
    reference_ppm: float | None
    averaged_shift_ppm: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _normalize_symbol(self.symbol))


@dataclass
class NMRReport:
    """Top-level NMR report for one molecule."""

    molecule_name: str
    backend: str
    method: str | None
    basis: str | None
    temperature_k: float | None
    references: dict[str, float | None]
    conformers: list[NMRConformerResult] = field(default_factory=list)
    averaged_atoms: list[NMRAveragedAtomResult] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.references = dict(self.references)
        self.conformers = list(self.conformers)
        self.averaged_atoms = list(self.averaged_atoms)
        self.metadata = dict(self.metadata)


__all__ = [
    "NMRAtomShielding",
    "NMRAtomShift",
    "NMRConformerResult",
    "NMRAveragedAtomResult",
    "NMRReport",
]
