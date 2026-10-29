"""OPT-viewer JSON projection from an :class:`OrcaCalculation` (design doc §11.1)."""

from __future__ import annotations

from acp.results.orca_parser import OrcaCalculation

__all__ = ["build_optimization_trajectory"]


def build_optimization_trajectory(calc: OrcaCalculation) -> dict:
    """Build the §11.1 optimization-viewer payload from *calc*."""
    return {
        "scf_energies": list(calc.scf_energies),
        "converged": calc.converged,
        "n_cycles": calc.n_opt_cycles,
        "gradients_rms": list(calc.gradients_rms) if calc.gradients_rms is not None else [],
    }
