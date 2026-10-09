"""Pure scientific normalization for Shermo thermochemistry (no I/O).

Single normalization path for every thermochemistry entry point (plan todo
14): standard-state (``1atm``/``1M``) correction, unit conversion, Gibbs
selection (``select_gibbs`` semantics) and metadata/units assembly.  This
module performs no file or process I/O; the shared execution path lives in
``cccp.qc.shermo_adapter`` and calls in here.  The frozen data containers
below are pure data classes shared by that path.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypedDict

from cccp.utils.constants import GAS_CONSTANT_KCAL_PER_MOL_K, HARTREE_TO_KCAL

_GAS_CONSTANT_L_ATM_PER_MOL_K: Final = 0.082057366080960
_STANDARD_PRESSURE_ATM: Final = 1.0
_STANDARD_CONCENTRATION_MOL_PER_L: Final = 1.0
_SHERMO_KEYS: Final = ("u_sum", "h_sum", "g_sum", "g_conc", "s_total")


class ThermochemistryInputError(ValueError):
    """Raised when a thermochemistry input violates the calculation contract."""


class ShermoValues(TypedDict, total=False):
    """Values parsed from a Shermo summary file."""

    u_sum: float
    h_sum: float
    g_sum: float
    g_conc: float
    s_total: float


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


@dataclass(frozen=True, slots=True)
class ThermochemistryContext:
    """Execution paths and configuration used by one calculation."""

    config: Mapping[str, Any] | None
    output_dir: Path
    output_file: Path
    runner_options: Mapping[str, Any]
    standard_state: str


@dataclass(frozen=True, slots=True)
class ShermoSettings:
    """Shermo execution settings resolved from ACP configuration."""

    shermo_bin: str
    scl_zpe: float
    ilowfreq: int
    imagreal: int
    concentration: float | None
    qrrho: bool


@dataclass(frozen=True, slots=True)
class ThermochemistryOutcome:
    """Parsed Shermo values and selected free-energy correction."""

    values: ShermoValues
    gibbs: float | None
    gibbs_source: str
    standard_delta: float | None


def normalize_standard_state(value: str | None) -> str:
    """Normalize a standard-state token to ``"1atm"`` or ``"1M"``."""
    normalized = str(value or "1atm").strip().lower().replace(" ", "")
    if normalized in {"1m", "1mol/l", "1molperliter", "1molperl", "solution", "solution1m"}:
        return "1M"
    return "1atm"


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


def parse_shermo_result(raw_result: dict[str, float] | None) -> ShermoValues | None:
    """Extract the stable Shermo keys from the runner response."""
    if not raw_result:
        return None
    values = ShermoValues()
    if "u_sum" in raw_result:
        values["u_sum"] = float(raw_result["u_sum"])
    if "h_sum" in raw_result:
        values["h_sum"] = float(raw_result["h_sum"])
    if "g_sum" in raw_result:
        values["g_sum"] = float(raw_result["g_sum"])
    if "g_conc" in raw_result:
        values["g_conc"] = float(raw_result["g_conc"])
    if "s_total" in raw_result:
        values["s_total"] = float(raw_result["s_total"])
    return values


def select_gibbs(
    g_sum: float | None,
    g_conc: float | None,
    temperature: float,
    standard_state: str,
) -> tuple[float | None, str, float | None]:
    """Select concentration-aware Gibbs energy and its standard-state delta."""
    if g_conc is not None:
        return g_conc, "g_conc", None
    if g_sum is None:
        return None, "unavailable", None
    if standard_state != "1M":
        return g_sum, "g_sum", None
    delta = standard_state_correction_kcal(temperature) / HARTREE_TO_KCAL
    return g_sum + delta, "g_sum_plus_standard_state", delta


def build_metadata(
    request: ValidatedRequest,
    context: ThermochemistryContext,
    settings: ShermoSettings,
    outcome: ThermochemistryOutcome,
    *,
    success: bool,
) -> dict[str, Any]:
    """Build the stable metadata projection for a calculation result."""
    enthalpy = outcome.values.get("h_sum")
    entropy = outcome.values.get("s_total")
    u_sum = outcome.values.get("u_sum")
    legacy_values: dict[str, Any] = {}
    if u_sum is not None:
        legacy_values["u_sum"] = u_sum
    if enthalpy is not None:
        legacy_values["h_sum"] = enthalpy
    g_sum = outcome.values.get("g_sum")
    if g_sum is not None:
        legacy_values["g_sum"] = g_sum
    g_conc = outcome.values.get("g_conc")
    if g_conc is not None:
        legacy_values["g_conc"] = g_conc
    if entropy is not None:
        legacy_values["s_total"] = entropy
    return {
        "success": success,
        "freq_log_path": str(request.freq_log_path),
        "output_file": str(context.output_file),
        "sp_energy_hartree": request.sp_energy_hartree,
        "temperature_k": request.temperature,
        "pressure_atm": request.pressure,
        "temperature": request.temperature,
        "pressure": request.pressure,
        "standard_state": request.standard_state,
        "u_sum": outcome.values.get("u_sum"),
        "h_sum": enthalpy,
        "g_sum": outcome.values.get("g_sum"),
        "g_conc": outcome.values.get("g_conc"),
        "s_total": entropy,
        "gibbs_hartree": outcome.gibbs,
        "enthalpy_hartree": enthalpy,
        "entropy_au": entropy,
        "gibbs_free_energy_hartree": outcome.gibbs,
        "total_gibbs_hartree": outcome.gibbs,
        "total_enthalpy_hartree": enthalpy,
        "entropy": entropy,
        "free_energy_hartree": outcome.gibbs,
        "free_energy_kcal_mol": (
            None if outcome.gibbs is None else outcome.gibbs * HARTREE_TO_KCAL
        ),
        "selected_gibbs_source": outcome.gibbs_source,
        "standard_state_delta_g_hartree": outcome.standard_delta,
        "standard_state_delta_g_kcal_mol": (
            None if outcome.standard_delta is None else outcome.standard_delta * HARTREE_TO_KCAL
        ),
        "qrrho": settings.qrrho,
        "qrrho_mode": "Shermo ilowfreq=2 quasi-RRHO"
        if settings.qrrho
        else "disabled_or_unconfigured",
        "thermal_correction_u_hartree": (
            None if u_sum is None else u_sum - request.sp_energy_hartree
        ),
        "thermo": legacy_values,
        "legacy_shermo_result": legacy_values,
    }


__all__ = [
    "ShermoSettings",
    "ShermoValues",
    "ThermochemistryContext",
    "ThermochemistryInputError",
    "ThermochemistryOutcome",
    "ThermochemistryRequest",
    "ValidatedRequest",
    "build_metadata",
    "normalize_standard_state",
    "parse_shermo_result",
    "select_gibbs",
    "standard_state_correction_kcal",
]
