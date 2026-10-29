"""Canonical BatchOptimize profiles.

The profile table is shared by plan construction and execution so that the
public workflow cannot drift away from the calculation engine.
"""

from __future__ import annotations

from typing import Final

from acp.calculations.contracts import StepKind

BATCH_PROFILE_STEPS: Final[dict[str, tuple[StepKind, ...]]] = {
    "opt_only": (StepKind.OPTIMIZE,),
    "opt_freq": (StepKind.OPTIMIZE, StepKind.FREQUENCY),
    "opt_freq_sp": (StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT),
    "opt_freq_sp_thermo": (
        StepKind.OPTIMIZE,
        StepKind.FREQUENCY,
        StepKind.SINGLEPOINT,
        StepKind.THERMOCHEMISTRY,
    ),
}

BATCH_PROFILE_NAMES: Final[tuple[str, ...]] = tuple(BATCH_PROFILE_STEPS)

__all__ = ["BATCH_PROFILE_NAMES", "BATCH_PROFILE_STEPS"]
