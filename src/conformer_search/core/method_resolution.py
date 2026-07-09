"""Unified QC method resolution: config is the SOLE source of truth.

All protocols use the SAME theory level from config. Protocol presets define
only STRUCTURE (which stages run, selection mode, thresholds) — never methods.

The only exception is benchmark, which can use protocol_overrides to vary
methods across sub-runs.

Resolution order (low → high):
    1. config["theory"][stage]  (global, from _get_default_config + user YAML)
    2. config["protocol_overrides"][name][stage]  (benchmark per-protocol)
    3. explicit_overrides  (caller-provided, highest priority)
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedQCMethod:
    stage: str
    method: str
    basis: str | None = None
    solvent: str | None = None
    solvent_model: str = "smd"
    dispersion: str | None = None
    engine: str | None = None


_STAGES: dict[str, str] = {
    "optimization": "optimization",
    "opt": "optimization",
    "part2_opt": "optimization",
    "frequency": "optimization",
    "freq": "optimization",
    "low_cost_sp": "low_cost_sp",
    "part1_sp": "low_cost_sp",
    "final_sp": "final_sp",
    "single_point": "final_sp",
    "part3_sp": "final_sp",
    "sp": "final_sp",
}


def resolve_qc_method(
    config: Mapping[str, Any],
    *,
    stage: str,
    protocol_name: str | None = None,
    explicit_overrides: Mapping[str, Any] | None = None,
) -> ResolvedQCMethod:
    canonical = _STAGES.get(stage, stage)
    theory = config.get("theory", {})

    if canonical == "final_sp":
        base = copy.deepcopy(theory.get("final_sp") or theory.get("single_point") or {})
    elif canonical == "low_cost_sp":
        base = copy.deepcopy(theory.get("low_cost_sp") or {})
        if not base.get("method"):
            sp = theory.get("single_point") or theory.get("final_sp") or {}
            opt = theory.get("optimization") or {}
            base["method"] = "r2scan3c"
            base["basis"] = base.get("basis") or opt.get("basis")
            base["solvent"] = base.get("solvent") if base.get("solvent") is not None else sp.get("solvent")
            base["solvent_model"] = base.get("solvent_model") or sp.get("solvent_model", "smd")
    else:
        base = copy.deepcopy(theory.get(canonical) or {})

    if protocol_name:
        user_proto = (
            config.get("protocol_overrides", {})
            .get(protocol_name, {})
            .get(canonical, {})
        )
        _merge(base, user_proto)

    if explicit_overrides:
        _merge(base, explicit_overrides)

    method = base.get("method")
    if not method:
        raise ValueError(f"No QC method for stage '{canonical}'")

    return ResolvedQCMethod(
        stage=canonical,
        method=method,
        basis=base.get("basis"),
        solvent=base.get("solvent"),
        solvent_model=base.get("solvent_model", "smd"),
        dispersion=base.get("dispersion"),
        engine=base.get("engine"),
    )


def _merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if value is not None:
            target[key] = value


__all__ = ["ResolvedQCMethod", "resolve_qc_method"]
