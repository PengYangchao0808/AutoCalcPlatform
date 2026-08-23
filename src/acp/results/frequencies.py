"""FREQ-viewer JSON projection from an :class:`OrcaCalculation` (design doc §11.2)."""

from __future__ import annotations

from acp.results.orca_parser import OrcaCalculation

__all__ = ["build_frequency_report"]


def build_frequency_report(calc: OrcaCalculation) -> dict:
    """Build the §11.2 frequency-viewer payload from *calc*."""
    return {
        "frequencies": list(calc.frequencies),
        "imaginary_modes": list(calc.imaginary_modes),
        "ir_intensities": list(calc.ir_intensities) if calc.ir_intensities is not None else None,
        "has_imaginary": bool(calc.imaginary_modes),
        "normal_modes_available": False,
    }
