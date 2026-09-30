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

    Role-resolved values (trust/hessian/recalc/iterations/convergence/SCF/
    rescue) take priority; keys without a role resolution fall back to the
    shared common value (engine constants where ``None``).
    """
    from acp.calculations.batch.options import _ROLE_OVERRIDE_FIELDS

    role_opts = opts.resolve_role_options(is_ts)
    effective: dict[str, Any] = {}
    for name in _ROLE_OVERRIDE_FIELDS:
        if name in role_opts:
            effective[name] = role_opts[name]
    max_cycles = role_opts.get("opt_max_iter")
    if max_cycles is None:
        max_cycles = opts.opt_max_iter
    effective["max_cycles"] = (
        int(max_cycles) if max_cycles is not None else _ENGINE_MAX_CYCLES_DEFAULT
    )
    effective["opt_level"] = role_opts.get("opt_convergence") or opts.opt_convergence
    scf_maxiter = role_opts.get("scf_max_iter")
    effective["scf_maxiter"] = int(scf_maxiter) if scf_maxiter is not None else opts.scf_max_iter
    effective["scf_convergence"] = role_opts.get("scf_convergence") or opts.scf_convergence
    effective["scf_strategy"] = role_opts.get("scf_strategy") or opts.scf_strategy
    effective["rescue_policy"] = role_opts.get("opt_rescue_policy") or opts.opt_rescue_policy
    max_rescue = role_opts.get("opt_max_rescue")
    effective["max_rescue"] = int(max_rescue) if max_rescue is not None else opts.opt_max_rescue
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
    if isinstance(recalc, int) and recalc > 0:
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

    Routes through :meth:`BatchMethodOptions.from_method_dict` — the single
    entry point that handles both legacy flat dicts and new-style
    ``batch_roles`` payloads.  Importable from the shared module so the
    preview endpoint can converge here.
    """
    return BatchMethodOptions.from_method_dict(method)


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
