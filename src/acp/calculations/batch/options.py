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
            case StepKind.OPTIMIZE | StepKind.SCAN | StepKind.THERMOCHEMISTRY:
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
            },
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = ["BatchMethodOptions"]
