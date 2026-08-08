"""QC backend abstraction with capability-based protocols."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray


@dataclass
class QCResult:
    """Standard calculation result mirrored from the legacy interface layer."""

    success: bool = False
    energy: float | None = None
    coordinates: NDArray[np.float64] | None = None
    symbols: list[str] | None = None
    converged: bool = False
    output_file: Path | None = None
    log_file: Path | None = None
    freq_log_file: Path | None = None
    error_message: str | None = None
    frequencies: list[float] | None = None
    has_frequencies: bool = False
    zpe: float | None = None
    enthalpy: float | None = None
    gibbs: float | None = None
    entropy: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def to_qc_result(result: object) -> QCResult:
    """Normalize legacy QC result objects into :class:`QCResult`."""
    if isinstance(result, QCResult):
        return result

    metadata = getattr(result, "metadata", {}) or {}
    return QCResult(
        success=getattr(result, "success", False),
        energy=getattr(result, "energy", None),
        coordinates=getattr(result, "coordinates", None),
        symbols=getattr(result, "symbols", None),
        converged=getattr(result, "converged", False),
        output_file=getattr(result, "output_file", None),
        log_file=getattr(result, "log_file", None),
        freq_log_file=getattr(result, "freq_log_file", None),
        error_message=getattr(result, "error_message", None),
        frequencies=getattr(result, "frequencies", None),
        has_frequencies=getattr(result, "has_frequencies", False),
        zpe=getattr(result, "zpe", None),
        enthalpy=getattr(result, "enthalpy", None),
        gibbs=getattr(result, "gibbs", None),
        entropy=getattr(result, "entropy", None),
        metadata=dict(metadata),
    )


class QCBackend(ABC):
    """Base class for all QC program backends."""

    name: str = ""

    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        self.config = config
        self.options = dict(kwargs)

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the QC program is installed and accessible."""
        raise NotImplementedError

    def get_version(self) -> str | None:
        """Return the backend version when available."""
        return None


@runtime_checkable
class GeometryOptimizer(Protocol):
    """Capability: can perform geometry optimization."""

    def optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Optimize a molecular geometry."""
        ...


@runtime_checkable
class SinglePointCalculator(Protocol):
    """Capability: can compute single-point energies."""

    def single_point(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run a single-point energy calculation."""
        ...


@runtime_checkable
class FrequencyCalculator(Protocol):
    """Capability: can perform frequency calculations."""

    def frequency(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run a frequency calculation."""
        ...


@runtime_checkable
class ConformerSearcher(Protocol):
    """Capability: can perform conformer searches from an XYZ input."""

    def search(
        self,
        initial_xyz: Path,
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        """Run a conformer search and return the ensemble XYZ path."""
        ...


@runtime_checkable
class ClusteringTool(Protocol):
    """Capability: can cluster conformer ensembles."""

    def cluster(
        self,
        ensemble_xyz: Path,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Path:
        """Cluster an ensemble XYZ file and return the clustered XYZ path."""
        ...


@runtime_checkable
class ThermoCalculator(Protocol):
    """Capability: can perform thermochemistry calculations from log files."""

    def thermochemistry(
        self,
        log_file: Path,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run thermochemistry for a single log file."""
        ...

    def batch_thermochemistry(
        self,
        log_files: list[Path],
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> list[QCResult]:
        """Run thermochemistry for multiple log files."""
        ...


@runtime_checkable
class TSMechanismCalculator(Protocol):
    """Capability: can perform transition-state and IRC calculations."""

    def transition_state_opt(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run a transition-state optimization."""
        ...

    def irc(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run an intrinsic reaction coordinate calculation."""
        ...


@runtime_checkable
class NmrShieldingCalculator(Protocol):
    """Capability: can compute NMR shielding constants (GIAO).

    Implementations return a :class:`QCResult` whose ``metadata["shieldings"]``
    maps a 0-based atom index to a descriptor carrying at minimum
    ``{"symbol", "isotropic"}`` (the isotropic magnetic shielding in ppm).
    """

    def nmr_shielding(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        nuclei: list[str] | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Compute isotropic magnetic shieldings for target nuclei."""
        ...


__all__ = [
    "QCBackend",
    "QCResult",
    "to_qc_result",
    "GeometryOptimizer",
    "SinglePointCalculator",
    "FrequencyCalculator",
    "ConformerSearcher",
    "ClusteringTool",
    "ThermoCalculator",
    "TSMechanismCalculator",
    "NmrShieldingCalculator",
]
