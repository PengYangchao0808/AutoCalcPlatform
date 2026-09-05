"""Private input validation for the thermochemistry primitive."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cccp.utils.constants import GAS_CONSTANT_KCAL_PER_MOL_K

_GAS_CONSTANT_L_ATM_PER_MOL_K: Final = 0.082057366080960
_STANDARD_PRESSURE_ATM: Final = 1.0
_STANDARD_CONCENTRATION_MOL_PER_L: Final = 1.0


class ThermochemistryInputError(ValueError):
    """Raised when a thermochemistry input violates the calculation contract."""


@dataclass(frozen=True, slots=True)
class ThermochemistryRequest:
    """Raw five-field thermochemistry request."""

    freq_log_path: Path | str | None
    sp_energy_hartree: float
    temperature: float
    pressure: float
    standard_state: str


@dataclass(frozen=True, slots=True)
class ValidatedRequest:
    """Validated and normalized thermochemistry request."""

    freq_log_path: Path
    sp_energy_hartree: float
    temperature: float
    pressure: float
    standard_state: str


def standard_state_correction_kcal(temperature: float) -> float:
    """Return the ideal-gas 1 atm to 1 mol/L correction in kcal/mol."""
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ThermochemistryInputError("temperature must be a positive finite value")
    ratio = (
        _GAS_CONSTANT_L_ATM_PER_MOL_K
        * temperature
        * _STANDARD_CONCENTRATION_MOL_PER_L
        / _STANDARD_PRESSURE_ATM
    )
    return GAS_CONSTANT_KCAL_PER_MOL_K * temperature * math.log(ratio)


def validate_request(request: ThermochemistryRequest) -> ValidatedRequest:
    """Parse and validate the public thermochemistry inputs."""
    if request.freq_log_path is None or (
        isinstance(request.freq_log_path, str) and not request.freq_log_path.strip()
    ):
        raise ThermochemistryInputError("frequency log is required")
    freq_path = Path(request.freq_log_path)
    if not freq_path.is_file():
        raise ThermochemistryInputError(f"frequency log does not exist: {freq_path}")
    energy = _finite_number(request.sp_energy_hartree, "sp_energy_hartree")
    temperature = _positive_number(request.temperature, "temperature")
    pressure = _positive_number(request.pressure, "pressure")
    return ValidatedRequest(
        freq_log_path=freq_path,
        sp_energy_hartree=energy,
        temperature=temperature,
        pressure=pressure,
        standard_state=_normalize_standard_state(request.standard_state),
    )


def _finite_number(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ThermochemistryInputError(f"{name} must be finite")
    return number


def _positive_number(value: float, name: str) -> float:
    number = _finite_number(value, name)
    if number <= 0.0:
        raise ThermochemistryInputError(f"{name} must be positive")
    return number


def _normalize_standard_state(value: str) -> str:
    normalized = str(value or "1atm").strip().lower().replace(" ", "")
    if normalized in {"1m", "1mol/l", "1molperliter", "1molperl", "solution", "solution1m"}:
        return "1M"
    return "1atm"
