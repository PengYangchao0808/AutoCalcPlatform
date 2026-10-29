"""Shared low-level Shermo execution adapter (plan todo 14).

One execution/normalization path for every thermochemistry entry point:
parses and preserves the full legacy parameter set (``output_dir`` /
``output_file`` / ``sp_energy`` / ``temperature_k`` / ``pressure_atm`` /
``standard_state`` / ``scl_zpe`` / ``ilowfreq`` / ``imagreal`` / ``conc``),
resolves Shermo settings, launches Shermo exactly once per request and
normalizes the response through ``cccp.qc.thermo_normalize``.  The legacy
``ThermochemistryCalculator`` primitive and ``ExternalBackend.thermochemistry``
both delegate here; ``batch_process_thermo`` keeps calling the same runner as
a low-level compatibility wrapper.  No task-layer imports are allowed here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cccp.qc.runners import run_shermo
from cccp.qc.thermo_normalize import (
    ShermoSettings,
    ThermochemistryContext,
    ThermochemistryInputError,
    ThermochemistryOutcome,
    ThermochemistryRequest,
    ValidatedRequest,
    build_metadata,
    normalize_standard_state,
    parse_shermo_result,
    select_gibbs,
)

_DEFAULT_SCL_ZPE: Final = 0.9905
_DEFAULT_ILOWFREQ: Final = 2
_DEFAULT_IMAGREAL: Final = 0


def _section(
    source: Mapping[str, Any] | None,
    key: str,
) -> Mapping[str, Any]:
    if source is None:
        return {}
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _setting(
    key: str,
    options: Mapping[str, Any],
    section: Mapping[str, Any],
    default: Any,
) -> Any:
    return options.get(key, section.get(key, default))


def _float_setting(
    key: str,
    options: Mapping[str, Any],
    section: Mapping[str, Any],
    default: float,
) -> float:
    return _float_setting_value(_setting(key, options, section, default), key)


def _float_setting_value(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ThermochemistryInputError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ThermochemistryInputError(f"{name} must be finite")
    return number


def _int_setting(
    key: str,
    options: Mapping[str, Any],
    section: Mapping[str, Any],
    default: Any,
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
        standard_state=normalize_standard_state(request.standard_state),
    )


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


@dataclass(frozen=True, slots=True)
class ShermoRunResult:
    """Normalized outcome of one shared Shermo thermochemistry execution."""

    success: bool
    request: ValidatedRequest
    context: ThermochemistryContext
    settings: ShermoSettings
    outcome: ThermochemistryOutcome
    metadata: dict[str, Any]
    error: str | None


def execute_shermo(
    freq_log: Path | str | None,
    sp_energy: float = 0.0,
    temperature_k: float = 298.15,
    pressure_atm: float = 1.0,
    standard_state: str = "1atm",
    output_dir: Path | str | None = None,
    output_file: Path | str | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    runner_options: Mapping[str, Any] | None = None,
) -> ShermoRunResult:
    """Run Shermo once and normalize its thermochemistry (shared entry).

    Legacy parameter semantics are preserved: ``output_dir`` defaults to the
    frequency log's parent directory and ``output_file`` to
    ``<output_dir>/<file_stem>.sum`` when not given; ``standard_state`` drives
    the concentration default and Gibbs selection; ``scl_zpe`` / ``ilowfreq``
    / ``imagreal`` / ``conc`` / ``shermo_bin`` may be overridden through
    ``runner_options`` (or the ``config`` thermo/executables sections).
    Exactly one Shermo launch happens per request.  Input violations raise
    :class:`ThermochemistryInputError`; runtime failure is reported as
    ``success=False`` with ``error`` set.
    """
    request = validate_request(
        ThermochemistryRequest(
            freq_log_path=freq_log,
            sp_energy_hartree=sp_energy,
            temperature=temperature_k,
            pressure=pressure_atm,
            standard_state=standard_state,
        )
    )
    resolved_output_dir = (
        Path(output_dir) if output_dir is not None else request.freq_log_path.parent
    )
    resolved_output_file = (
        Path(output_file)
        if output_file is not None
        else resolved_output_dir / f"{request.freq_log_path.stem}.sum"
    )
    context = ThermochemistryContext(
        config=config,
        output_dir=resolved_output_dir,
        output_file=resolved_output_file,
        runner_options=dict(runner_options or {}),
        standard_state=request.standard_state,
    )
    settings = resolve_runner_settings(context)
    raw_result = run_shermo(
        freq_output=request.freq_log_path,
        sp_energy=request.sp_energy_hartree,
        output_dir=context.output_dir,
        shermo_bin=settings.shermo_bin,
        output_file=context.output_file,
        temperature_k=request.temperature,
        pressure_atm=request.pressure,
        scl_zpe=settings.scl_zpe,
        ilowfreq=settings.ilowfreq,
        imagreal=settings.imagreal,
        conc=settings.concentration,
    )
    parsed = parse_shermo_result(raw_result)
    if parsed is None:
        outcome = ThermochemistryOutcome(
            values={},
            gibbs=None,
            gibbs_source="unavailable",
            standard_delta=None,
        )
        return ShermoRunResult(
            success=False,
            request=request,
            context=context,
            settings=settings,
            outcome=outcome,
            metadata=build_metadata(request, context, settings, outcome, success=False),
            error="Shermo returned no thermochemistry data",
        )
    gibbs, gibbs_source, standard_delta = select_gibbs(
        parsed.get("g_sum"),
        parsed.get("g_conc"),
        request.temperature,
        request.standard_state,
    )
    outcome = ThermochemistryOutcome(
        values=parsed,
        gibbs=gibbs,
        gibbs_source=gibbs_source,
        standard_delta=standard_delta,
    )
    return ShermoRunResult(
        success=True,
        request=request,
        context=context,
        settings=settings,
        outcome=outcome,
        metadata=build_metadata(request, context, settings, outcome, success=True),
        error=None,
    )


__all__ = [
    "ShermoRunResult",
    "ThermochemistryRequest",
    "ValidatedRequest",
    "execute_shermo",
    "resolve_runner_settings",
    "validate_request",
]
