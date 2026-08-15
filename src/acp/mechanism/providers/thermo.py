"""Thermochemistry providers shared by mechanism and energy workflows."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from acp.core.models import HARTREE_TO_KCAL, StructureEnsemble
from acp.workflows.ensemble_thermo import ensemble_total_gibbs
from cccp.qc.runners import run_shermo as _default_run_shermo
from cccp.utils.constants import GAS_CONSTANT_KCAL_PER_MOL_K as _GAS_CONSTANT_KCAL_PER_MOL_K

from .._helpers import opt_float as _opt_float
from .contracts import ThermochemistryProvider, ThermochemistryResult

logger = logging.getLogger(__name__)

_GAS_CONSTANT_L_ATM_PER_MOL_K = 0.082057366080960
_STANDARD_PRESSURE_ATM = 1.0
_STANDARD_CONCENTRATION_MOL_PER_L = 1.0
_SHERMO_RESULT_KEYS = ("u_sum", "h_sum", "g_sum", "g_conc", "s_total")
_ComputeDetailsCallable = Callable[..., ThermochemistryResult]


ShermoRunner = Callable[..., dict[str, float] | None]


@dataclass(frozen=True)
class ThermochemistryBatchItem:
    """One thermochemistry batch request.

    Attributes:
        sp_energy: Electronic single-point energy in Hartree.
        freq_log: Frequency-output path consumed by Shermo.
        ensemble: Optional ensemble carrying representative Boltzmann weight.
        temperature: Temperature in Kelvin.
        standard_state: Standard-state label (for example ``"1atm"`` or ``"1M"``).
        output_dir: Optional Shermo working directory override.
        output_file: Optional Shermo summary-file path override.
    """

    sp_energy: float | None
    freq_log: Path | str | None
    ensemble: StructureEnsemble | None = None
    temperature: float = 298.15
    standard_state: str = "1atm"
    output_dir: Path | str | None = None
    output_file: Path | str | None = None


def standard_state_correction_kcal(
    temperature_k: float,
    n_particles_change: float = 1.0,
) -> float:
    """Return the ideal-gas 1 atm → 1 mol/L correction in kcal/mol.

    The implemented formula is

    ``ΔG° = n·R·T·ln((R·T/P°)·c°)``

    with ``P° = 1 atm`` and ``c° = 1 mol/L``.  At 298.15 K this yields
    approximately ``+1.8938 kcal/mol`` per particle.

    Args:
        temperature_k: Temperature in Kelvin.
        n_particles_change: Stoichiometric particle-count change multiplier.

    Returns:
        Standard-state correction in kcal/mol.
    """

    temperature = float(temperature_k)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise ValueError("temperature_k must be a positive finite value")
    factor = (
        _GAS_CONSTANT_L_ATM_PER_MOL_K
        * temperature
        * _STANDARD_CONCENTRATION_MOL_PER_L
        / _STANDARD_PRESSURE_ATM
    )
    return float(n_particles_change) * _GAS_CONSTANT_KCAL_PER_MOL_K * temperature * math.log(factor)


def resolve_standard_state(config: Mapping[str, Any] | None, default: str = "1atm") -> str:
    """Resolve the configured thermochemistry standard state."""

    if not isinstance(config, Mapping):
        return default
    top_level = config.get("thermochemistry_standard_state")
    if isinstance(top_level, str) and top_level.strip():
        return top_level.strip()
    thermo_cfg = config.get("thermo")
    if isinstance(thermo_cfg, Mapping):
        state = thermo_cfg.get("standard_state")
        if isinstance(state, str) and state.strip():
            return state.strip()
    mechanism_cfg = config.get("mechanism")
    if isinstance(mechanism_cfg, Mapping):
        state = mechanism_cfg.get("standard_state")
        if isinstance(state, str) and state.strip():
            return state.strip()
    return default


def thermochemistry_result_to_legacy_dict(
    result: ThermochemistryResult,
) -> dict[str, float | None]:
    """Return the legacy Shermo-style result dict used downstream."""

    raw = result.corrections.get("legacy_shermo_result")
    if isinstance(raw, dict):
        legacy: dict[str, float | None] = {}
        for key in _SHERMO_RESULT_KEYS:
            legacy[key] = _opt_float(raw.get(key))
        return legacy
    legacy = {
        "u_sum": _opt_float(result.corrections.get("u_sum")),
        "h_sum": _opt_float(result.corrections.get("h_sum")),
        "g_sum": _opt_float(result.corrections.get("g_sum")),
        "g_conc": _opt_float(result.corrections.get("g_conc")),
        "s_total": _opt_float(result.corrections.get("s_total")),
    }
    if legacy["g_sum"] is None and result.gibbs_hartree is not None:
        legacy["g_sum"] = result.gibbs_hartree
    if legacy["h_sum"] is None and result.enthalpy_hartree is not None:
        legacy["h_sum"] = result.enthalpy_hartree
    if legacy["s_total"] is None and result.entropy_au is not None:
        legacy["s_total"] = result.entropy_au
    return legacy


class _BaseThermochemistryProvider:
    """Shared helper mixin for concrete thermochemistry providers."""

    def compute(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StructureEnsemble | None,
        temperature: float,
        standard_state: str,
    ) -> ThermochemistryResult:
        raise NotImplementedError

    def compute_batch(
        self,
        items: Sequence[ThermochemistryBatchItem | Mapping[str, Any]],
        *,
        shared_sp_energy: float | None = None,
        confirm_shared_sp_energy: bool = False,
    ) -> list[ThermochemistryResult]:
        """Compute thermochemistry for a batch while guarding the shared-SP footgun.

        Args:
            items: Per-item thermochemistry requests.
            shared_sp_energy: Optional single SP energy to broadcast across items.
            confirm_shared_sp_energy: Explicit confirmation for shared SP reuse.

        Returns:
            Per-item thermochemistry results.
        """

        if shared_sp_energy is not None and not confirm_shared_sp_energy:
            raise ValueError(
                "Refusing to broadcast one sp_energy across a batch; pass per-item sp_energy "
                "or set confirm_shared_sp_energy=True explicitly"
            )
        results: list[ThermochemistryResult] = []
        for raw_item in items:
            item = _coerce_batch_item(raw_item)
            sp_energy = item.sp_energy
            if sp_energy is None:
                if shared_sp_energy is None:
                    raise ValueError("Each batch item must provide its own sp_energy")
                sp_energy = shared_sp_energy
            compute_details = getattr(self, "compute_details", None)
            if callable(compute_details):
                detail_runner = cast(_ComputeDetailsCallable, compute_details)
                results.append(
                    detail_runner(
                        sp_energy=sp_energy,
                        freq_log=item.freq_log,
                        ensemble=item.ensemble,
                        temperature=item.temperature,
                        standard_state=item.standard_state,
                        output_dir=item.output_dir,
                        output_file=item.output_file,
                    )
                )
                continue
            results.append(
                self.compute(
                    sp_energy=sp_energy,
                    freq_log=item.freq_log,
                    ensemble=item.ensemble,
                    temperature=item.temperature,
                    standard_state=item.standard_state,
                )
            )
        return results


class ShermoProvider(_BaseThermochemistryProvider):
    """Default thermochemistry provider backed by :func:`cccp.qc.runners.run_shermo`."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        runner: ShermoRunner | None = None,
    ) -> None:
        self.config: Mapping[str, Any] | None = config
        self.runner: ShermoRunner = runner or _default_run_shermo

    def compute(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StructureEnsemble | None,
        temperature: float,
        standard_state: str,
    ) -> ThermochemistryResult:
        return self.compute_details(
            sp_energy=sp_energy,
            freq_log=freq_log,
            ensemble=ensemble,
            temperature=temperature,
            standard_state=standard_state,
        )

    def compute_details(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StructureEnsemble | None,
        temperature: float,
        standard_state: str,
        *,
        output_dir: Path | str | None = None,
        output_file: Path | str | None = None,
        pressure_atm: float | None = None,
        scl_zpe: float | None = None,
        ilowfreq: int | None = None,
        imagreal: int | None = None,
        conc: float | None = None,
    ) -> ThermochemistryResult:
        """Compute Shermo thermochemistry with optional execution-context overrides."""

        temperature_value = float(temperature)
        normalized_state = _normalize_standard_state(standard_state)
        if sp_energy is None:
            raise ValueError("sp_energy is required for Shermo thermochemistry")
        if freq_log is None:
            raise ValueError("freq_log is required for Shermo thermochemistry")

        freq_path = Path(freq_log)
        shermo_config = _thermo_config(self.config)
        work_dir = _resolve_output_dir(freq_path, output_dir)
        output_path = Path(output_file) if output_file is not None else work_dir / "Shermo.sum"
        effective_conc = conc
        if normalized_state == "1M" and effective_conc is None:
            effective_conc = 1.0

        raw_result = self.runner(
            freq_output=freq_path,
            sp_energy=float(sp_energy),
            output_dir=work_dir,
            shermo_bin=str(shermo_config.get("path", "Shermo")),
            output_file=output_path,
            temperature_k=temperature_value,
            pressure_atm=float(
                pressure_atm if pressure_atm is not None else shermo_config.get("pressure_atm", 1.0)
            ),
            scl_zpe=float(scl_zpe if scl_zpe is not None else shermo_config.get("scl_zpe", 0.9905)),
            ilowfreq=int(
                ilowfreq if ilowfreq is not None else shermo_config.get("shermo_ilowfreq", 2)
            ),
            imagreal=int(
                imagreal if imagreal is not None else shermo_config.get("shermo_imagreal", 0)
            ),
            conc=effective_conc,
        )
        if raw_result is None:
            return ThermochemistryResult(
                gibbs_hartree=None,
                enthalpy_hartree=None,
                entropy_au=None,
                temperature=temperature_value,
                standard_state=normalized_state,
                corrections={
                    "provider": type(self).__name__,
                    "provider_id": "shermo",
                    "success": False,
                    "freq_log": str(freq_path),
                },
            )

        g_sum = _opt_float(raw_result.get("g_sum"))
        g_conc = _opt_float(raw_result.get("g_conc"))
        standard_state_delta_hartree = None
        selected_gibbs = g_conc if g_conc is not None else g_sum
        selected_source = "g_conc" if g_conc is not None else "g_sum"
        if normalized_state == "1M" and g_conc is None and g_sum is not None:
            standard_state_delta_hartree = (
                standard_state_correction_kcal(temperature_value) / HARTREE_TO_KCAL
            )
            selected_gibbs = g_sum + standard_state_delta_hartree
            selected_source = "g_sum_plus_standard_state"

        qrrho_enabled = _qrrho_enabled(self.config, ilowfreq)
        return ThermochemistryResult(
            gibbs_hartree=selected_gibbs,
            enthalpy_hartree=_opt_float(raw_result.get("h_sum")),
            entropy_au=_opt_float(raw_result.get("s_total")),
            temperature=temperature_value,
            standard_state=normalized_state,
            corrections={
                "provider": type(self).__name__,
                "provider_id": "shermo",
                "success": True,
                "selected_gibbs_source": selected_source,
                "legacy_shermo_result": {
                    key: _opt_float(raw_result.get(key)) for key in _SHERMO_RESULT_KEYS
                },
                "u_sum": _opt_float(raw_result.get("u_sum")),
                "h_sum": _opt_float(raw_result.get("h_sum")),
                "g_sum": g_sum,
                "g_conc": g_conc,
                "s_total": _opt_float(raw_result.get("s_total")),
                "standard_state_delta_g_hartree": standard_state_delta_hartree,
                "standard_state_delta_g_kcal_mol": (
                    None
                    if standard_state_delta_hartree is None
                    else standard_state_delta_hartree * HARTREE_TO_KCAL
                ),
                "qrrho": qrrho_enabled,
                "qrrho_mode": (
                    "Shermo ilowfreq=2 quasi-RRHO" if qrrho_enabled else "disabled_or_unconfigured"
                ),
                "freq_log": str(freq_path),
                "output_file": str(output_path),
                "ensemble_supplied": ensemble is not None,
            },
        )


class RPHCompositeProvider(_BaseThermochemistryProvider):
    """ACP-native composite thermochemistry provider.

    The implemented formula is

    ``G_composite = E_SP + (G_freq − E_freq) + ΔG_ensemble + ΔG°_std``

    where the base ``E_SP + (G_freq − E_freq)`` term comes from Shermo's
    ``-E`` handoff, ``ΔG_ensemble = k_B·T·ln(p_representative)`` follows ACP's
    rank-1 convention, and ``ΔG°_std`` is the ideal-gas 1 atm → 1 mol/L
    correction when ``standard_state`` requests ``1M``.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        runner: ShermoRunner | None = None,
    ) -> None:
        self.config: Mapping[str, Any] | None = config
        self._shermo: ShermoProvider = ShermoProvider(config, runner=runner)

    def compute(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StructureEnsemble | None,
        temperature: float,
        standard_state: str,
    ) -> ThermochemistryResult:
        return self.compute_details(
            sp_energy=sp_energy,
            freq_log=freq_log,
            ensemble=ensemble,
            temperature=temperature,
            standard_state=standard_state,
        )

    def compute_details(
        self,
        sp_energy: float | None,
        freq_log: Path | str | None,
        ensemble: StructureEnsemble | None,
        temperature: float,
        standard_state: str,
        *,
        output_dir: Path | str | None = None,
        output_file: Path | str | None = None,
        pressure_atm: float | None = None,
        scl_zpe: float | None = None,
        ilowfreq: int | None = None,
        imagreal: int | None = None,
        conc: float | None = None,
    ) -> ThermochemistryResult:
        """Compute composite thermochemistry with ACP-standard ensemble correction."""

        if conc is not None:
            logger.info(
                "RPHCompositeProvider ignores Shermo concentration input and applies ΔG°_std itself"
            )
        base = self._shermo.compute_details(
            sp_energy=sp_energy,
            freq_log=freq_log,
            ensemble=ensemble,
            temperature=temperature,
            standard_state="1atm",
            output_dir=output_dir,
            output_file=output_file,
            pressure_atm=pressure_atm,
            scl_zpe=scl_zpe,
            ilowfreq=ilowfreq,
            imagreal=imagreal,
            conc=None,
        )
        raw = thermochemistry_result_to_legacy_dict(base)
        base_gibbs = _opt_float(raw.get("g_sum"))
        if base_gibbs is None:
            corrections = dict(base.corrections)
            corrections.update(
                {
                    "provider": type(self).__name__,
                    "provider_id": "rph-composite",
                    "success": False,
                }
            )
            return ThermochemistryResult(
                gibbs_hartree=None,
                enthalpy_hartree=base.enthalpy_hartree,
                entropy_au=base.entropy_au,
                temperature=float(temperature),
                standard_state=_normalize_standard_state(standard_state),
                corrections=corrections,
            )

        normalized_state = _normalize_standard_state(standard_state)
        ensemble_delta_hartree, representative_weight, weight_source = _ensemble_delta_g_hartree(
            ensemble,
            float(temperature),
        )
        standard_state_delta_hartree = (
            standard_state_correction_kcal(float(temperature)) / HARTREE_TO_KCAL
            if normalized_state == "1M"
            else None
        )
        composite = base_gibbs + ensemble_delta_hartree
        if standard_state_delta_hartree is not None:
            composite += standard_state_delta_hartree
        corrections = dict(base.corrections)
        corrections.update(
            {
                "provider": type(self).__name__,
                "provider_id": "rph-composite",
                "success": True,
                "selected_gibbs_source": "composite",
                "ensemble_delta_g_hartree": ensemble_delta_hartree,
                "ensemble_delta_g_kcal_mol": ensemble_delta_hartree * HARTREE_TO_KCAL,
                "representative_weight": representative_weight,
                "representative_weight_source": weight_source,
                "standard_state_delta_g_hartree": standard_state_delta_hartree,
                "standard_state_delta_g_kcal_mol": (
                    None
                    if standard_state_delta_hartree is None
                    else standard_state_delta_hartree * HARTREE_TO_KCAL
                ),
                "gibbs_components_hartree": {
                    "sp_plus_freq": base_gibbs,
                    "ensemble_delta": ensemble_delta_hartree,
                    "standard_state_delta": standard_state_delta_hartree,
                },
            }
        )
        return ThermochemistryResult(
            gibbs_hartree=composite,
            enthalpy_hartree=base.enthalpy_hartree,
            entropy_au=base.entropy_au,
            temperature=float(temperature),
            standard_state=normalized_state,
            corrections=corrections,
        )


def get_thermochemistry_provider(
    config: Mapping[str, Any] | None = None,
    *,
    runner: ShermoRunner | None = None,
) -> ThermochemistryProvider:
    """Return the configured thermochemistry provider implementation."""

    provider_name = _provider_name(config)
    if provider_name == "rph-composite":
        return RPHCompositeProvider(config, runner=runner)
    if provider_name == "shermo":
        return ShermoProvider(config, runner=runner)
    raise ValueError(f"Unknown thermochemistry provider {provider_name!r}")


def _coerce_batch_item(
    item: ThermochemistryBatchItem | Mapping[str, Any],
) -> ThermochemistryBatchItem:
    if isinstance(item, ThermochemistryBatchItem):
        return item
    return ThermochemistryBatchItem(
        sp_energy=_opt_float(item.get("sp_energy")),
        freq_log=cast_path_like(item.get("freq_log")),
        ensemble=(
            item.get("ensemble") if isinstance(item.get("ensemble"), StructureEnsemble) else None
        ),
        temperature=float(item.get("temperature") or 298.15),
        standard_state=str(item.get("standard_state") or "1atm"),
        output_dir=cast_path_like(item.get("output_dir")),
        output_file=cast_path_like(item.get("output_file")),
    )


def _provider_name(config: Mapping[str, Any] | None) -> str:
    if not isinstance(config, Mapping):
        return "shermo"
    explicit = config.get("thermochemistry_provider")
    if isinstance(explicit, str) and explicit.strip():
        return _normalize_provider_name(explicit)
    thermo_cfg = config.get("thermo")
    if isinstance(thermo_cfg, Mapping):
        provider = thermo_cfg.get("provider")
        if isinstance(provider, str) and provider.strip():
            return _normalize_provider_name(provider)
    mechanism_cfg = config.get("mechanism")
    if isinstance(mechanism_cfg, Mapping):
        provider = mechanism_cfg.get("thermochemistry_provider")
        if isinstance(provider, str) and provider.strip():
            return _normalize_provider_name(provider)
    return "shermo"


def _normalize_provider_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"default", "shermo", "shermo-provider"}:
        return "shermo"
    if normalized in {"rph-composite", "rphcomposite", "composite"}:
        return "rph-composite"
    return normalized


def _normalize_standard_state(value: str | None) -> str:
    normalized = str(value or "1atm").strip().lower().replace(" ", "")
    if normalized in {"1m", "1mol/l", "1molperliter", "1molperl", "solution", "solution1m"}:
        return "1M"
    return "1atm"


def _resolve_output_dir(freq_path: Path, output_dir: Path | str | None) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    if freq_path.parent != Path(""):
        return freq_path.parent
    return Path.cwd()


def _thermo_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    thermo_cfg = config.get("thermo")
    if isinstance(thermo_cfg, Mapping):
        return thermo_cfg
    return {}


def _qrrho_enabled(config: Mapping[str, Any] | None, ilowfreq: int | None) -> bool:
    thermo_cfg = _thermo_config(config)
    qrrho = thermo_cfg.get("qrrho")
    if isinstance(qrrho, bool):
        return qrrho
    resolved_ilowfreq = int(
        ilowfreq if ilowfreq is not None else thermo_cfg.get("shermo_ilowfreq", 2)
    )
    return resolved_ilowfreq == 2


def _ensemble_delta_g_hartree(
    ensemble: StructureEnsemble | None,
    temperature: float,
) -> tuple[float, float | None, str | None]:
    if ensemble is None:
        return 0.0, None, None
    representative_weight, source = _representative_weight(ensemble)
    if representative_weight is None:
        return 0.0, None, source
    if representative_weight <= 0.0:
        return 0.0, representative_weight, source
    weight = min(representative_weight, 1.0)
    # k_B·T·ln(p) — the same mixing correction as the energy workflow's
    # ensemble_total_gibbs (rank1 form with a zero electronic reference).
    return ensemble_total_gibbs(0.0, weight, temperature), representative_weight, source


def _representative_weight(ensemble: StructureEnsemble) -> tuple[float | None, str | None]:
    metadata_weight = _opt_float(ensemble.metadata.get("representative_weight"))
    if metadata_weight is not None:
        return metadata_weight, "ensemble.metadata.representative_weight"

    representative_id = ensemble.metadata.get("representative_structure_id")
    if isinstance(representative_id, str):
        for record in ensemble.records:
            if record.id == representative_id or record.structure.id == representative_id:
                return _opt_float(record.weight), "representative_structure_id"

    representative_index = ensemble.metadata.get("representative_index")
    if isinstance(representative_index, int) and 0 <= representative_index < len(ensemble.records):
        return _opt_float(ensemble.records[representative_index].weight), "representative_index"

    minimum = ensemble.global_minimum()
    if minimum is not None and minimum.weight is not None:
        return _opt_float(minimum.weight), "ensemble.global_minimum"
    if ensemble.records:
        return _opt_float(ensemble.records[0].weight), "ensemble.records[0]"
    return None, None


def cast_path_like(value: object) -> Path | str | None:
    if value is None:
        return None
    if isinstance(value, (Path, str)):
        return value
    return None


__all__ = [
    "RPHCompositeProvider",
    "ShermoProvider",
    "ThermochemistryBatchItem",
    "get_thermochemistry_provider",
    "resolve_standard_state",
    "standard_state_correction_kcal",
    "thermochemistry_result_to_legacy_dict",
]
