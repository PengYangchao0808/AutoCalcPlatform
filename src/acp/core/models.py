# pyright: reportImportCycles=false
"""
Core Models
===========

Generic molecular structure, ensemble, and job specification models.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np

HARTREE_TO_KCAL = 627.5094740631

if TYPE_CHECKING:
    from conformer_search.core.candidates import CandidateSet, ConformerCandidate


def zip_strict(*iterables):
    """Like :func:`zip` with ``strict=True`` (PEP 618), but Python 3.9-safe.

    ``zip(..., strict=True)`` was added in Python 3.10; this helper provides
    the same length-mismatch safety on interpreters that lack the keyword
    (e.g. remote compute nodes still on 3.9).  On 3.10+ it defers to the
    builtin for native performance.

    Raises:
        ValueError: If the iterables have unequal lengths.
    """
    import sys

    if sys.version_info >= (3, 10):
        return zip(*iterables, strict=True)  # type: ignore[call-arg]
    # 3.9 fallback: materialise and verify equal length.
    lists = [list(it) for it in iterables]
    if not lists:
        return iter(())
    first = len(lists[0])
    for i, lst in enumerate(lists[1:], start=1):
        if len(lst) != first:
            raise ValueError(
                f"zip_strict(): iterable {i} has length {len(lst)} "
                f"which does not match iterable 0 of length {first}"
            )
    return iter(zip(*lists))


def _coerce_int(value: object, default: int = 0) -> int:
    """Return an integer value with a safe fallback."""
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_float(value: object) -> float | None:
    """Return a float value or ``None`` when conversion is not possible."""
    if isinstance(value, (float, int, np.floating, np.integer)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class JobStatus(Enum):
    """Execution status for a workflow job."""

    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class Structure:
    """Immutable molecular structure."""

    id: str
    charge: int = 0
    multiplicity: int = 1
    symbols: list[str] = field(default_factory=list)
    coordinates: Sequence[Sequence[float]] | np.ndarray | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable inputs for safe reuse."""
        object.__setattr__(self, "symbols", list(self.symbols))
        object.__setattr__(self, "metadata", dict(self.metadata))

        if self.coordinates is None:
            return

        coordinates = np.asarray(self.coordinates, dtype=float).copy()
        if coordinates.ndim != 2 or coordinates.shape[1] != 3:
            raise ValueError("Structure.coordinates must have shape (N, 3)")

        if self.symbols and coordinates.shape[0] != len(self.symbols):
            raise ValueError(
                "Structure.coordinates row count must match len(symbols)"
            )

        coordinates.setflags(write=False)
        object.__setattr__(self, "coordinates", coordinates)

    @property
    def n_atoms(self) -> int:
        """Return atom count."""
        return len(self.symbols)

    def to_conformer_candidate(
        self,
        *,
        index: int = 0,
        energy: float = 0.0,
        weight: float = 0.0,
        source_file: Path | None = None,
        rank: int = 0,
        metadata: Mapping[str, object] | None = None,
        gibbs_energy: float | None = None,
        gibbs_correction: float | None = None,
        h_correction: float | None = None,
        u_correction: float | None = None,
        s_total: float | None = None,
        g_conc: float | None = None,
    ) -> ConformerCandidate:
        """Create a legacy ``ConformerCandidate`` from this structure."""
        if self.coordinates is None:
            raise ValueError("Cannot convert Structure without coordinates")

        from conformer_search.core.candidates import ConformerCandidate

        candidate_metadata = dict(self.metadata)
        candidate_metadata.setdefault("structure_id", self.id)
        candidate_metadata.setdefault("charge", self.charge)
        candidate_metadata.setdefault("multiplicity", self.multiplicity)
        if metadata:
            candidate_metadata.update(metadata)

        return ConformerCandidate(
            index=index,
            coordinates=np.array(self.coordinates, copy=True),
            symbols=list(self.symbols),
            energy=energy,
            weight=weight,
            source_file=source_file,
            rank=rank,
            metadata=candidate_metadata,
            gibbs_energy=gibbs_energy,
            gibbs_correction=gibbs_correction,
            h_correction=h_correction,
            u_correction=u_correction,
            s_total=s_total,
            g_conc=g_conc,
        )


@dataclass
class StructureRecord:
    """Single structure with calculation results."""

    structure: Structure
    energy_hartree: float | None = None
    free_energy_hartree: float | None = None
    weight: float | None = None
    properties: dict[str, object] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable mappings."""
        self.properties = dict(self.properties)
        self.files = {name: Path(path) for name, path in self.files.items()}

    @property
    def id(self) -> str:
        """Return the record structure identifier."""
        return self.structure.id

    @property
    def charge(self) -> int:
        """Return molecular charge."""
        return self.structure.charge

    @property
    def multiplicity(self) -> int:
        """Return spin multiplicity."""
        return self.structure.multiplicity

    @property
    def symbols(self) -> list[str]:
        """Return element symbols."""
        return self.structure.symbols

    @property
    def coordinates(self) -> np.ndarray | None:
        """Return structure coordinates."""
        return cast(np.ndarray | None, self.structure.coordinates)

    @property
    def metadata(self) -> dict[str, object]:
        """Return structure metadata."""
        return self.structure.metadata

    def to_conformer_candidate(self) -> ConformerCandidate:
        """Create a legacy ``ConformerCandidate`` from this record."""
        if self.coordinates is None:
            raise ValueError("Cannot convert StructureRecord without coordinates")

        from conformer_search.core.candidates import ConformerCandidate

        index = _coerce_int(self.properties.get("index", 0))
        candidate_metadata = dict(self.metadata)
        if self.id != f"conf_{index:03d}":
            candidate_metadata = {"structure_id": self.id, **candidate_metadata}

        return ConformerCandidate(
            index=index,
            coordinates=np.array(self.coordinates, copy=True),
            symbols=list(self.symbols),
            energy=self.energy_hartree or 0.0,
            weight=self.weight or 0.0,
            source_file=self.files.get("source"),
            rank=_coerce_int(self.properties.get("rank", 0)),
            metadata=candidate_metadata,
            gibbs_energy=_coerce_float(
                self.properties.get("gibbs_energy_hartree", self.free_energy_hartree)
            ),
            gibbs_correction=_coerce_float(
                self.properties.get("gibbs_correction_hartree")
            ),
            h_correction=_coerce_float(self.properties.get("h_correction_hartree")),
            u_correction=_coerce_float(self.properties.get("u_correction_hartree")),
            s_total=_coerce_float(self.properties.get("entropy_total_au")),
            g_conc=_coerce_float(self.properties.get("g_conc_hartree")),
        )

    @classmethod
    def from_conformer_candidate(cls, candidate: ConformerCandidate) -> StructureRecord:
        """Create a generic record from a legacy candidate."""
        files: dict[str, object] = {}
        if candidate.source_file is not None:
            files["source"] = candidate.source_file
        free_energy = candidate.g_conc if candidate.g_conc is not None else candidate.gibbs_energy
        return cls(
            structure=Structure(
                id=str(candidate.metadata.get("structure_id", f"conf_{candidate.index:03d}")),
                charge=int(candidate.metadata.get("charge", 0)),
                multiplicity=int(candidate.metadata.get("multiplicity", 1)),
                symbols=list(candidate.symbols),
                coordinates=np.array(candidate.coordinates, copy=True),
                metadata=dict(candidate.metadata),
            ),
            energy_hartree=candidate.energy,
            free_energy_hartree=free_energy,
            weight=candidate.weight,
            properties={
                "index": candidate.index,
                "rank": candidate.rank,
                "gibbs_energy_hartree": candidate.gibbs_energy,
                "gibbs_correction_hartree": candidate.gibbs_correction,
                "h_correction_hartree": candidate.h_correction,
                "u_correction_hartree": candidate.u_correction,
                "entropy_total_au": candidate.s_total,
                "g_conc_hartree": candidate.g_conc,
            },
            files=files,
        )


@dataclass
class StructureEnsemble:
    """Collection of structure records with ensemble statistics."""

    records: list[StructureRecord] = field(default_factory=list)
    data: list[object] = field(default_factory=list)
    temperature: float = 298.15
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize mutable inputs."""
        self.records = list(self.records)
        self.data = list(self.data)
        self.metadata = dict(self.metadata)

    def __len__(self) -> int:
        """Return number of records."""
        return len(self.records)

    def __iter__(self) -> Iterator[StructureRecord]:
        """Iterate over records."""
        return iter(self.records)

    def __getitem__(self, index: int) -> StructureRecord:
        """Return a record by index."""
        return self.records[index]

    def add(self, record: StructureRecord) -> None:
        """Append a record to the ensemble."""
        self.records.append(record)

    @staticmethod
    def _energy_sort_key(record: StructureRecord) -> float:
        """Return a sortable energy key."""
        return float("inf") if record.energy_hartree is None else record.energy_hartree

    def sort_by_energy(self) -> None:
        """Sort records in-place by energy."""
        self.records.sort(key=self._energy_sort_key)

    def global_minimum(self) -> StructureRecord | None:
        """Return the lowest-energy record."""
        if not self.records:
            return None
        return min(self.records, key=self._energy_sort_key)

    def window_select(self, energy_window_kcal: float = 3.0) -> list[StructureRecord]:
        """Return records within an energy window from the minimum."""
        global_minimum = self.global_minimum()
        if global_minimum is None or global_minimum.energy_hartree is None:
            return []

        threshold_hartree = (
            global_minimum.energy_hartree + energy_window_kcal / HARTREE_TO_KCAL
        )
        return [
            record
            for record in sorted(self.records, key=self._energy_sort_key)
            if record.energy_hartree is not None
            and record.energy_hartree <= threshold_hartree
        ]

    def to_candidate_set(self) -> CandidateSet:
        """Create a legacy ``CandidateSet`` from this ensemble."""
        from conformer_search.core.candidates import CandidateSet

        candidates = [record.to_conformer_candidate() for record in self.records]
        reference_energy = _coerce_float(self.metadata.get("reference_energy_hartree"))
        if reference_energy is None:
            global_minimum = self.global_minimum()
            if global_minimum is not None:
                reference_energy = global_minimum.energy_hartree

        return CandidateSet(
            candidates=candidates,
            reference_energy=reference_energy,
            temperature=self.temperature,
        )

    @classmethod
    def from_candidate_set(cls, candidate_set: CandidateSet) -> StructureEnsemble:
        """Create a generic ensemble from a legacy candidate set."""
        return cls(
            records=[StructureRecord.from_conformer_candidate(c) for c in candidate_set.candidates],
            temperature=candidate_set.temperature,
            metadata={"reference_energy_hartree": candidate_set.reference_energy},
        )


@dataclass
class JobSpec:
    """Task/job specification."""

    workflow_type: str
    protocol: str
    input_source: str
    input_format: str = "smiles"
    params: dict[str, object] = field(default_factory=dict)


__all__ = [
    "JobSpec",
    "JobStatus",
    "Structure",
    "StructureEnsemble",
    "StructureRecord",
]
