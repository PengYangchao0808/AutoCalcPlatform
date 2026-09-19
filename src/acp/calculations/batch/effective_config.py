"""BatchOptimize effective-config computation and persistence.

Provides the single source of truth for computing the *resolved* method
configuration that will actually be used by the batch engine, including
per-role (INT/TS) overrides.  Both the submission-time snapshot (written
to ``effective_config.json`` at the task root) and the server-side
preview endpoint share this logic.

Schema version ``batch_optimize_effective_v1`` — additive, backward-compatible.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from acp.calculations.batch.options import BatchMethodOptions

logger = logging.getLogger(__name__)

SCHEMA_VERSION: str = "batch_optimize_effective_v1"
CONFIG_FILENAME: str = "effective_config.json"

# ── ORCA keyword maps (shared with batch_preview.py) ───────────────────
_OPT_LEVEL_KEYWORDS: dict[str, str | None] = {
    "loose": "LooseOpt",
    "normal": "Opt",
    "tight": "TightOpt",
    "verytight": "VeryTightOpt",
}

_SCF_CONVERGENCE_KEYWORDS: dict[str, str] = {
    "loose": "LooseSCF",
    "tight": "TightSCF",
    "verytight": "VeryTightSCF",
}

_SCF_STRATEGY_KEYWORDS: dict[str, str] = {
    "slowconv": "SlowConv",
    "soscf": "SOSCF",
}

# Engine constant — mirrors BatchOptimizeEngine._optimization_kwargs default.
_ENGINE_MAX_CYCLES_DEFAULT: int = 200

__all__ = [
    "CONFIG_FILENAME",
    "SCHEMA_VERSION",
    "build_batch_effective_config",
    "build_effective_role",
    "build_orca_summary",
    "read_effective_config",
    "write_effective_config",
]


# ── Build from BatchMethodOptions ──────────────────────────────────────


def build_effective_role(
    opts: BatchMethodOptions,
    is_ts: bool,
) -> dict[str, Any]:
    """Build per-role effective dict from resolved options.

    Combines ``resolve_role_options`` (trust/hessian/recalc) with engine
    constants (max_cycles, opt_level, scf trio).
    """
    role_opts = opts.resolve_role_options(is_ts)
    effective: dict[str, Any] = {}
    for name in ("opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess"):
        if name in role_opts:
            effective[name] = role_opts[name]
    effective["max_cycles"] = (
        opts.opt_max_iter if opts.opt_max_iter is not None else _ENGINE_MAX_CYCLES_DEFAULT
    )
    effective["opt_level"] = opts.opt_convergence
    effective["scf_maxiter"] = opts.scf_max_iter
    effective["scf_convergence"] = opts.scf_convergence
    effective["scf_strategy"] = opts.scf_strategy
    return effective


def build_orca_summary(effective: dict[str, Any]) -> list[str]:
    """Build a human-readable ORCA keyword list for one role."""
    parts: list[str] = []
    level = str(effective.get("opt_level", "tight")).lower()
    kw = _OPT_LEVEL_KEYWORDS.get(level)
    if kw:
        parts.append(kw)
    scf_conv = str(effective.get("scf_convergence", "tight")).lower()
    scf_kw = _SCF_CONVERGENCE_KEYWORDS.get(scf_conv)
    if scf_kw:
        parts.append(scf_kw)
    max_cycles = effective.get("max_cycles")
    if max_cycles is not None:
        parts.append(f"MaxIter {int(max_cycles)}")
    trust = effective.get("opt_trust_radius")
    if trust is not None:
        parts.append(f"Trust {float(trust):g}")
    hessian = effective.get("opt_initial_hessian")
    if hessian == "calculate":
        parts.append("Calc_Hess")
    recalc = effective.get("opt_recalc_hess")
    if isinstance(recalc, int):
        parts.append(f"Recalc_Hess {recalc}")
    strategy = str(effective.get("scf_strategy", "normal")).lower()
    if strategy != "normal":
        strat_kw = _SCF_STRATEGY_KEYWORDS.get(strategy)
        if strat_kw:
            parts.append(strat_kw)
    return parts


def build_batch_effective_config(opts: BatchMethodOptions) -> dict[str, Any]:
    """Compute the full effective config for both INT and TS roles.

    Returns a dict conforming to ``batch_optimize_effective_v1`` schema.
    """
    roles: dict[str, dict[str, Any]] = {}
    orca_summary: dict[str, list[str]] = {}
    for is_ts, role_key in ((False, "int"), (True, "ts")):
        effective = build_effective_role(opts, is_ts)
        roles[role_key] = effective
        orca_summary[role_key] = build_orca_summary(effective)

    return {
        "schema": SCHEMA_VERSION,
        "common": {
            "optimization_method": opts.optimization_method,
            "optimization_basis": opts.optimization_basis,
            "single_point_method": opts.single_point_method,
            "single_point_basis": opts.single_point_basis,
            "temperature": opts.temperature,
            "pressure": opts.pressure,
            "scale_factor": opts.scale_factor,
            "opt_rescue_policy": opts.opt_rescue_policy,
            "opt_max_rescue": opts.opt_max_rescue,
            "scf_orbital_inherit": opts.scf_orbital_inherit,
        },
        "roles": roles,
        "orca_summary": orca_summary,
    }


# ── Reconstruct BatchMethodOptions from raw method dict ────────────────


def build_opts_from_method_dict(method: dict[str, Any]) -> BatchMethodOptions:
    """Construct ``BatchMethodOptions`` from a normalised method payload.

    Mirrors ``batch_preview._build_batch_method_options`` — importable
    from the shared module so the preview endpoint can converge here
    later.
    """
    from acp.chem.composition import normalize_recalc_hess

    kwargs: dict[str, Any] = {}
    if method.get("functional"):
        kwargs["optimization_method"] = method["functional"]
    if method.get("basis"):
        kwargs["optimization_basis"] = method["basis"]
    if method.get("single_point_method"):
        kwargs["single_point_method"] = method["single_point_method"]
    if method.get("single_point_basis"):
        kwargs["single_point_basis"] = method["single_point_basis"]
    if method.get("minimum_method"):
        kwargs["minimum_method"] = method["minimum_method"]
    if method.get("minimum_basis"):
        kwargs["minimum_basis"] = method["minimum_basis"]
    if method.get("transition_state_method"):
        kwargs["transition_state_method"] = method["transition_state_method"]
    if method.get("transition_state_basis"):
        kwargs["transition_state_basis"] = method["transition_state_basis"]
    if method.get("temperature") is not None:
        kwargs["temperature"] = float(method["temperature"])
    if method.get("pressure") is not None:
        kwargs["pressure"] = float(method["pressure"])
    if method.get("scale_factor") is not None:
        kwargs["scale_factor"] = float(method["scale_factor"])

    # Optimization controls
    if method.get("opt_max_iter") is not None:
        kwargs["opt_max_iter"] = int(method["opt_max_iter"])
    if method.get("opt_convergence"):
        kwargs["opt_convergence"] = method["opt_convergence"]
    if method.get("opt_trust_radius") is not None:
        kwargs["opt_trust_radius"] = float(method["opt_trust_radius"])
    if method.get("opt_initial_hessian") is not None:
        kwargs["opt_initial_hessian"] = method["opt_initial_hessian"]
    if method.get("opt_recalc_hess") is not None:
        kwargs["opt_recalc_hess"] = normalize_recalc_hess(method["opt_recalc_hess"])
    if method.get("opt_rescue_policy"):
        kwargs["opt_rescue_policy"] = method["opt_rescue_policy"]
    if method.get("opt_max_rescue") is not None:
        kwargs["opt_max_rescue"] = int(method["opt_max_rescue"])

    # SCF controls
    if method.get("scf_max_iter") is not None:
        kwargs["scf_max_iter"] = int(method["scf_max_iter"])
    if method.get("scf_convergence"):
        kwargs["scf_convergence"] = method["scf_convergence"]
    if method.get("scf_strategy"):
        kwargs["scf_strategy"] = method["scf_strategy"]
    if method.get("scf_orbital_inherit") is not None:
        kwargs["scf_orbital_inherit"] = bool(method["scf_orbital_inherit"])

    # Per-role overrides
    for prefix in ("minimum_", "transition_state_"):
        for name in ("opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess"):
            field = f"{prefix}{name}"
            if method.get(field) is not None:
                if name == "opt_recalc_hess":
                    kwargs[field] = normalize_recalc_hess(method[field])
                else:
                    kwargs[field] = method[field]

    return BatchMethodOptions(**kwargs)


# ── Compute from raw method dict (submission convenience) ──────────────


def compute_effective_from_method(method: dict[str, Any]) -> dict[str, Any]:
    """One-shot: raw method dict → full effective config dict."""
    opts = build_opts_from_method_dict(method)
    return build_batch_effective_config(opts)


# ── Persistence ────────────────────────────────────────────────────────


def write_effective_config(work_dir: Path | str, config: dict[str, Any]) -> Path:
    """Atomically write ``effective_config.json`` under *work_dir*."""
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / CONFIG_FILENAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(config, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def read_effective_config(work_dir: Path | str) -> dict[str, Any] | None:
    """Read ``effective_config.json`` from *work_dir*, or ``None`` if absent."""
    path = Path(work_dir) / CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.debug("unreadable effective_config.json: %s", path, exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None
