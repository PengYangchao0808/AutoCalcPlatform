"""Private validation, settings, and result-shaping helpers for Shermo."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

from acp.calculations.contracts import JsonValue
from cccp.utils.constants import HARTREE_TO_KCAL

from ._thermochemistry_input import (
    ThermochemistryInputError,
    ValidatedRequest,
    standard_state_correction_kcal,
)

_DEFAULT_SCL_ZPE: Final = 0.9905
_DEFAULT_ILOWFREQ: Final = 2
_DEFAULT_IMAGREAL: Final = 0
_SHERMO_KEYS: Final = ("u_sum", "h_sum", "g_sum", "g_conc", "s_total")


class ShermoValues(TypedDict, total=False):
    """Values parsed from a Shermo summary file."""

    u_sum: float
    h_sum: float
    g_sum: float
    g_conc: float
    s_total: float


@dataclass(frozen=True, slots=True)
class ThermochemistryContext:
    """Execution paths and configuration used by one calculation."""

    config: Mapping[str, JsonValue] | None
    output_dir: Path
    output_file: Path
    runner_options: Mapping[str, JsonValue]
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


def resolve_runner_settings(context: ThermochemistryContext) -> ShermoSettings:
    """Resolve Shermo executable, qRRHO, and correction settings."""
    thermo_config = _section(context.config, "thermo")
    executable_config = _section(_section(context.config, "executables"), "shermo")
    shermo_bin = str(
        _setting(
            "shermo_bin",
            context.runner_options,
            thermo_config,
            str(thermo_config.get("path", executable_config.get("path", "Shermo"))),
        )
    )
    scl_zpe = _float_setting("scl_zpe", context.runner_options, thermo_config, _DEFAULT_SCL_ZPE)
    ilowfreq = _int_setting(
        "ilowfreq",
        context.runner_options,
        thermo_config,
        thermo_config.get("shermo_ilowfreq", _DEFAULT_ILOWFREQ),
    )
    imagreal = _int_setting(
        "imagreal",
        context.runner_options,
        thermo_config,
        thermo_config.get("shermo_imagreal", _DEFAULT_IMAGREAL),
    )
    configured_qrrho = thermo_config.get("qrrho")
    qrrho = configured_qrrho if isinstance(configured_qrrho, bool) else ilowfreq == 2
    configured_concentration = _setting("conc", context.runner_options, thermo_config, None)
    concentration = (
        None
        if configured_concentration is None and context.standard_state != "1M"
        else 1.0
        if configured_concentration is None
        else _float_setting_value(configured_concentration, "conc")
    )
    return ShermoSettings(
        shermo_bin=shermo_bin,
        scl_zpe=scl_zpe,
        ilowfreq=ilowfreq,
        imagreal=imagreal,
        concentration=concentration,
        qrrho=qrrho,
    )


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
) -> dict[str, JsonValue]:
    """Build the stable metadata projection for a calculation result."""
    enthalpy = outcome.values.get("h_sum")
    entropy = outcome.values.get("s_total")
    u_sum = outcome.values.get("u_sum")
    legacy_values: dict[str, JsonValue] = {}
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


def _section(
    source: Mapping[str, JsonValue] | None,
    key: str,
) -> Mapping[str, JsonValue]:
    if source is None:
        return {}
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _setting(
    key: str,
    options: Mapping[str, JsonValue],
    section: Mapping[str, JsonValue],
    default: JsonValue,
) -> JsonValue:
    return options.get(key, section.get(key, default))


def _float_setting(
    key: str,
    options: Mapping[str, JsonValue],
    section: Mapping[str, JsonValue],
    default: float,
) -> float:
    return _float_setting_value(_setting(key, options, section, default), key)


def _float_setting_value(value: JsonValue, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ThermochemistryInputError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ThermochemistryInputError(f"{name} must be finite")
    return number


def _int_setting(
    key: str,
    options: Mapping[str, JsonValue],
    section: Mapping[str, JsonValue],
    default: JsonValue,
) -> int:
    value = _setting(key, options, section, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ThermochemistryInputError(f"{key} must be integral")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ThermochemistryInputError(f"{key} must be integral") from exc
    if float(value) != number:
        raise ThermochemistryInputError(f"{key} must be integral")
    return number
