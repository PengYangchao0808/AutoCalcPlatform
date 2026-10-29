"""
QC Interface Base
=================

Abstract base class for quantum chemistry software interfaces.

Author: QCcalc Team (adapted from RPH)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional, List
import numpy as np


@dataclass
class QCResult:
    """Result from a quantum chemistry calculation."""
    success: bool = False
    energy: Optional[float] = None
    coordinates: Optional[np.ndarray] = None
    symbols: Optional[List[str]] = None
    converged: bool = False
    output_file: Optional[Path] = None
    log_file: Optional[Path] = None
    freq_log_file: Optional[Path] = None
    error_message: Optional[str] = None
    frequencies: Optional[List[float]] = None
    has_frequencies: bool = False
    zpe: Optional[float] = None
    enthalpy: Optional[float] = None
    gibbs: Optional[float] = None
    entropy: Optional[float] = None
    
    metadata: Dict[str, Any] = field(default_factory=dict)


def to_qc_result(result: object) -> QCResult:
    """Normalize legacy QC result objects into :class:`QCResult`.

    Single definition (plan todo 12 QCResult merge): the backend layer
    re-exports this helper and :class:`QCResult` so every consumer sees one
    identity.  Objects that already are :class:`QCResult` pass through
    unchanged; anything else is field-copied into a new instance.
    """
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


class QCInterfaceBase(ABC):
    """
    Abstract base class for QC software interfaces.
    
    All QC interface implementations should inherit from this class
    and implement the required abstract methods.
    """

    def __init__(self, config: Dict[str, Any], **kwargs):
        """
        Initialize QC interface.

        Args:
            config: Configuration dictionary
            **kwargs: Additional interface-specific parameters
        """
        self.config = config
        self.executables = config.get('executables', {})
        self.resources = config.get('resources', {})
        
    @abstractmethod
    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        **kwargs
    ) -> QCResult:
        """
        Perform geometry optimization.

        Args:
            coordinates: Initial coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional method-specific parameters

        Returns:
            QCResult with optimization results
        """
        pass

    @abstractmethod
    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        **kwargs
    ) -> QCResult:
        """
        Perform single-point energy calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional method-specific parameters

        Returns:
            QCResult with single-point energy
        """
        pass

    @abstractmethod
    def frequency(
        self,
        coordinates: np.ndarray,
        symbols: List[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path = None,
        **kwargs
    ) -> QCResult:
        """
        Perform frequency calculation.

        Args:
            coordinates: Molecular coordinates (N, 3)
            symbols: Element symbols
            charge: Molecular charge
            multiplicity: Spin multiplicity
            output_dir: Output directory
            **kwargs: Additional method-specific parameters

        Returns:
            QCResult with frequency results
        """
        pass

    def validate_result(self, result: QCResult) -> bool:
        """
        Validate calculation result.

        Args:
            result: QCResult to validate

        Returns:
            True if result is valid for downstream use
        """
        if not result.success:
            return False
        if result.energy is None:
            return False
        if result.coordinates is None:
            return False
        return True
