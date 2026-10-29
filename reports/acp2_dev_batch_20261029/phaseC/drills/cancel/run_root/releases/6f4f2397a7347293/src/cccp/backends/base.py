"""QC backend abstraction with capability-based protocols."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

# Neutral calculation-layer error re-exported as the backend layer's error
# surface (plan todo 14: capability modules stay free of task-layer imports).
from cccp.calculation.errors import BackendUnavailableError as BackendUnavailableError

# QCResult single definition (plan todo 12): canonical class lives in the
# interface base layer; re-exported here so all consumers share one identity.
from cccp.qc.interfaces.base import QCResult as QCResult
from cccp.qc.interfaces.base import to_qc_result as to_qc_result


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
class ConstrainedOptimizer(Protocol):
    """Capability: can optimize geometries under coordinate constraints."""

    def constrained_optimize(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str = "xtb_constrained_opt",
        constraints: object | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Optimize a molecular geometry with explicit coordinate constraints."""
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
class MrrhoThermoCalculator(Protocol):
    """Capability: can run xTB SPH + mRRHO thermochemistry."""

    def enso_thermo(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run xTB SPH + mRRHO thermochemistry."""
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
class CASSCFCalculator(Protocol):
    """Capability: can run CASSCF / NEVPT2 single-point calculations.

    Implementations return a :class:`QCResult` whose ``metadata["casscf"]``
    carries active-space provenance, natural orbital occupations, and
    per-root NEVPT2 energies (electronic-state design doc §11, §12.1).
    """

    def casscf(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> QCResult:
        """Run a CASSCF (optionally NEVPT2) calculation."""
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


@runtime_checkable
class RelaxedScanCalculator(Protocol):
    """Capability: can drive internal coordinates along a reaction path.

    Implementations perform a sequential multi-frame constrained optimization
    (each frame fixing the drive coordinates to their synchronous target and
    seeding from the previous frame) and return the per-frame trajectory.
    """

    def relaxed_scan(
        self,
        coordinates: NDArray[np.float64],
        symbols: list[str],
        output_dir: Path,
        plan: object,
        charge: int = 0,
        multiplicity: int = 1,
        **kwargs: Any,
    ) -> object:
        """Run a relaxed scan along *plan*; return the trajectory result."""
        ...


__all__ = [
    "BackendUnavailableError",
    "QCBackend",
    "QCResult",
    "to_qc_result",
    "GeometryOptimizer",
    "ConstrainedOptimizer",
    "SinglePointCalculator",
    "FrequencyCalculator",
    "ConformerSearcher",
    "ClusteringTool",
    "ThermoCalculator",
    "MrrhoThermoCalculator",
    "TSMechanismCalculator",
    "RelaxedScanCalculator",
    "CASSCFCalculator",
    "NmrShieldingCalculator",
]
