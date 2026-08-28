"""Role-specific method and basis options for BatchOptimize."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing_extensions import assert_never

from acp.calculations.contracts import StepKind


@dataclass(frozen=True, slots=True)
class BatchMethodOptions:
    """Optional ORCA method and basis overrides for minimum and TS items."""

    minimum_method: str = ""
    minimum_basis: str = ""
    transition_state_method: str = ""
    transition_state_basis: str = ""
    frequency_method: str = ""
    frequency_basis: str = ""
    single_point_method: str = ""
    single_point_basis: str = ""

    def for_role(self, is_transition_state: bool) -> tuple[str, str]:
        """Return the method and basis selected for one item role."""
        if is_transition_state:
            return self.transition_state_method, self.transition_state_basis
        return self.minimum_method, self.minimum_basis

    def for_step(self, step: StepKind, is_transition_state: bool) -> tuple[str, str]:
        role_method, role_basis = self.for_role(is_transition_state)
        match step:
            case StepKind.FREQUENCY:
                return self.frequency_method or role_method, self.frequency_basis or role_basis
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
                "minimum_method": self.minimum_method,
                "minimum_basis": self.minimum_basis,
                "transition_state_method": self.transition_state_method,
                "transition_state_basis": self.transition_state_basis,
                "frequency_method": self.frequency_method,
                "frequency_basis": self.frequency_basis,
                "single_point_method": self.single_point_method,
                "single_point_basis": self.single_point_basis,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = ["BatchMethodOptions"]
