"""Value objects for frame-wise single-point execution."""

# pyright: reportImplicitOverride=false

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

import numpy as np
from numpy.typing import NDArray

from acp.backends.base import QCBackend, SinglePointCalculator
from acp.calculations.batch._items import BatchStructureItem
from acp.calculations.contracts import StructureArtifact
from acp.core.models import Structure

FloatGeometry: TypeAlias = NDArray[np.float64] | Sequence[Sequence[float]]
FrameStatus: TypeAlias = Literal["completed", "failed"]


class BackendFactory(Protocol):
    """Resolve a named backend class or an already-created capability."""

    def __call__(self, name: str) -> type[QCBackend] | SinglePointCalculator: ...


@dataclass(frozen=True, slots=True)
class BatchSinglePointFrame:
    """A frame with explicit identity, geometry, and electronic state."""

    frame_id: str
    coordinates: FloatGeometry
    symbols: tuple[str, ...]
    charge: int = 0
    multiplicity: int = 1
    tag: str = "INT"
    candidate_id: str = ""


@dataclass(frozen=True, slots=True)
class BatchSinglePointFrameResult:
    """Result for one frame; a failed frame never aborts its siblings."""

    frame_id: str
    energy_hartree: float | None
    status: FrameStatus
    cache_key: str
    error_message: str | None = None
    output_path: Path | None = None
    cache_hit: bool = False

    @property
    def success(self) -> bool:
        """Return whether this frame produced an energy."""
        return self.status == "completed"

    @property
    def energy(self) -> float | None:
        """Return the frame energy using the short calculation-result name."""
        return self.energy_hartree

    @property
    def error(self) -> str | None:
        """Return the frame failure message."""
        return self.error_message


@dataclass(frozen=True, slots=True)
class BatchSinglePointResult(Mapping[str, BatchSinglePointFrameResult]):
    """Ordered frame-id mapping with aggregate batch counters."""

    records: dict[str, BatchSinglePointFrameResult]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", dict(self.records))

    def __getitem__(self, frame_id: str) -> BatchSinglePointFrameResult:
        return self.records[frame_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    @property
    def n_total(self) -> int:
        return len(self.records)

    @property
    def n_success(self) -> int:
        return sum(record.success for record in self.records.values())

    @property
    def n_failed(self) -> int:
        return self.n_total - self.n_success

    @property
    def n_cache_hits(self) -> int:
        return sum(record.cache_hit for record in self.records.values())


@dataclass(frozen=True, slots=True)
class PreparedFrame:
    """Normalized frame sent to the shared batch helper."""

    frame_id: str
    coordinates: NDArray[np.float64]
    symbols: tuple[str, ...]
    charge: int
    multiplicity: int
    cache_key: str


FrameInput: TypeAlias = (
    BatchSinglePointFrame
    | Structure
    | StructureArtifact
    | BatchStructureItem
    | Path
    | str
    | FloatGeometry
)


__all__ = [
    "BackendFactory",
    "BatchSinglePointFrame",
    "BatchSinglePointFrameResult",
    "BatchSinglePointResult",
    "FloatGeometry",
    "FrameInput",
    "FrameStatus",
    "PreparedFrame",
]
