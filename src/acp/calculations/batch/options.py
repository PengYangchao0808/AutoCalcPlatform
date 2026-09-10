"""Shared method and basis options for BatchOptimize."""

from __future__ import annotations

import json
from dataclasses import dataclass

from typing_extensions import assert_never

from acp.calculations.contracts import StepKind


@dataclass(frozen=True, slots=True)
class BatchMethodOptions:
    """ORCA settings shared by every BatchOptimize item.

    ``optimization_method`` and ``optimization_basis`` are the canonical
    method pair for both optimization and frequency calculations.  The
    legacy role-specific fields remain available for TS/minimum overrides,
    but frequency-specific fields are deliberately ignored so an old config
    cannot silently make optimization and frequency inconsistent.
    """

    # Keep the historical positional order for callers that still construct
    # this dataclass positionally.
    minimum_method: str = ""
    minimum_basis: str = ""
    transition_state_method: str = ""
    transition_state_basis: str = ""
    # Deprecated compatibility fields.  They are retained for old callers,
    # but frequency calculations always use the optimization method pair.
    frequency_method: str = ""
    frequency_basis: str = ""
    optimization_method: str = ""
    optimization_basis: str = ""
    single_point_method: str = ""
    single_point_basis: str = ""
    temperature: float = 298.15
    pressure: float = 1.0
    scale_factor: float = 0.9905

    # Optimization controls
    opt_max_iter: int | None = None
    opt_convergence: str = "tight"
    opt_trust_radius: float | None = None
    opt_initial_hessian: str | None = None
    opt_recalc_hess: str | int | None = None
    opt_rescue_policy: str = "adaptive"
    opt_max_rescue: int = 2

    # SCF controls
    scf_max_iter: int = 300
    scf_convergence: str = "tight"
    scf_strategy: str = "normal"
    scf_orbital_inherit: bool = True
    scf_damp: bool = False
    scf_damp_fac: float = 0.50
    scf_shift: bool = False
    scf_shift_fac: float = 0.30

    def for_role(self, is_transition_state: bool) -> tuple[str, str]:
        """Return the method and basis selected for one item role."""
        method = self.optimization_method or self.minimum_method
        basis = self.optimization_basis or self.minimum_basis
        if is_transition_state:
            method = self.transition_state_method or method
            basis = self.transition_state_basis or basis
        return method, basis

    def for_step(self, step: StepKind, is_transition_state: bool) -> tuple[str, str]:
        role_method, role_basis = self.for_role(is_transition_state)
        match step:
            case StepKind.FREQUENCY:
                # Frequency must use exactly the same electronic-structure
                # settings as optimization for a given role.
                return role_method, role_basis
            case StepKind.SINGLEPOINT:
                return (
                    self.single_point_method or role_method,
                    self.single_point_basis or role_basis,
                )
            case StepKind.OPTIMIZE | StepKind.SCAN | StepKind.THERMOCHEMISTRY | StepKind.CASSCF:
                return role_method, role_basis
            case unreachable:
                assert_never(unreachable)

    @property
    def cache_key(self) -> str:
        """Return a deterministic representation for checkpoint invalidation."""
        return json.dumps(
            {
                "optimization_method": self.optimization_method,
                "optimization_basis": self.optimization_basis,
                "single_point_method": self.single_point_method,
                "single_point_basis": self.single_point_basis,
                "temperature": self.temperature,
                "pressure": self.pressure,
                "scale_factor": self.scale_factor,
                "minimum_method": self.minimum_method,
                "minimum_basis": self.minimum_basis,
                "transition_state_method": self.transition_state_method,
                "transition_state_basis": self.transition_state_basis,
                "frequency_method": self.frequency_method,
                "frequency_basis": self.frequency_basis,
                "opt_max_iter": self.opt_max_iter,
                "opt_convergence": self.opt_convergence,
                "opt_trust_radius": self.opt_trust_radius,
                "opt_initial_hessian": self.opt_initial_hessian,
                "opt_recalc_hess": self.opt_recalc_hess,
                "opt_rescue_policy": self.opt_rescue_policy,
                "opt_max_rescue": self.opt_max_rescue,
                "scf_max_iter": self.scf_max_iter,
                "scf_convergence": self.scf_convergence,
                "scf_strategy": self.scf_strategy,
                "scf_orbital_inherit": self.scf_orbital_inherit,
                "scf_damp": self.scf_damp,
                "scf_damp_fac": self.scf_damp_fac,
                "scf_shift": self.scf_shift,
                "scf_shift_fac": self.scf_shift_fac,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = ["BatchMethodOptions"]
