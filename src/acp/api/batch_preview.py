"""
BatchOptimize Config Preview
=============================

Server-side normalised config-preview endpoint for the BatchOptimize
advanced-config modal.  Returns per-role effective options, source
provenance, and human-readable ORCA keyword summaries.

No job creation, no disk writes — pure read-only projection.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from acp.calculations.batch.options import (
    ROLE_DEFAULTS,
    BatchMethodOptions,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Engine constant ─────────────────────────────────────────────────────
# Mirrors ``BatchOptimizeEngine._optimization_kwargs`` default when
# ``opt_max_iter is None`` (engine.py:1029).  Duplicated here as a named
# constant to avoid importing engine privates.
_ENGINE_MAX_CYCLES_DEFAULT: int = 200

# ── ORCA keyword maps (mirrors cccp/qc/interfaces/orca_ts.py & orca.py) ─
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

# Mapping: effective key → BatchMethodOptions field for user-source check
_SHARED_SOURCE_KEYS: dict[str, str] = {
    "max_cycles": "opt_max_iter",
    "opt_level": "opt_convergence",
    "scf_maxiter": "scf_max_iter",
    "scf_convergence": "scf_convergence",
    "scf_strategy": "scf_strategy",
}

# ── Per-role override fields ─────────────────────────────────────────────
_ROLE_OVERRIDE_EFFECTIVE: tuple[str, ...] = (
    "opt_trust_radius",
    "opt_initial_hessian",
    "opt_recalc_hess",
)

_ROLE_PREFIX: dict[str, str] = {
    "int": "minimum_",
    "ts": "transition_state_",
}


# ── Request / response models ───────────────────────────────────────────


class BatchConfigPreviewRequest(BaseModel):
    """POST body for the config-preview endpoint."""

    method: dict[str, Any] = {}


# ── Helpers ──────────────────────────────────────────────────────────────


def _build_batch_method_options(normalized_batch: dict[str, Any]) -> BatchMethodOptions:
    """Construct a :class:`BatchMethodOptions` from the normalised batch level.

    Mirrors ``cli.py`` ``_handle_batch_optimize`` construction logic
    (conditional non-None kwargs + normalize_recalc_hess for the 3 recalc
    fields).
    """
    from acp.chem.composition import normalize_recalc_hess

    kwargs: dict[str, Any] = {}
    # Method / basis mapping: frontend "functional"/"basis" → dataclass
    # "optimization_method"/"optimization_basis".
    if normalized_batch.get("functional"):
        kwargs["optimization_method"] = normalized_batch["functional"]
    if normalized_batch.get("basis"):
        kwargs["optimization_basis"] = normalized_batch["basis"]
    if normalized_batch.get("single_point_method"):
        kwargs["single_point_method"] = normalized_batch["single_point_method"]
    if normalized_batch.get("single_point_basis"):
        kwargs["single_point_basis"] = normalized_batch["single_point_basis"]
    if normalized_batch.get("minimum_method"):
        kwargs["minimum_method"] = normalized_batch["minimum_method"]
    if normalized_batch.get("minimum_basis"):
        kwargs["minimum_basis"] = normalized_batch["minimum_basis"]
    if normalized_batch.get("transition_state_method"):
        kwargs["transition_state_method"] = normalized_batch["transition_state_method"]
    if normalized_batch.get("transition_state_basis"):
        kwargs["transition_state_basis"] = normalized_batch["transition_state_basis"]
    if normalized_batch.get("temperature") is not None:
        kwargs["temperature"] = float(normalized_batch["temperature"])
    if normalized_batch.get("pressure") is not None:
        kwargs["pressure"] = float(normalized_batch["pressure"])
    if normalized_batch.get("scale_factor") is not None:
        kwargs["scale_factor"] = float(normalized_batch["scale_factor"])

    # Optimization controls
    if normalized_batch.get("opt_max_iter") is not None:
        kwargs["opt_max_iter"] = int(normalized_batch["opt_max_iter"])
    if normalized_batch.get("opt_convergence"):
        kwargs["opt_convergence"] = normalized_batch["opt_convergence"]
    if normalized_batch.get("opt_trust_radius") is not None:
        kwargs["opt_trust_radius"] = float(normalized_batch["opt_trust_radius"])
    if normalized_batch.get("opt_initial_hessian") is not None:
        kwargs["opt_initial_hessian"] = normalized_batch["opt_initial_hessian"]
    if normalized_batch.get("opt_recalc_hess") is not None:
        kwargs["opt_recalc_hess"] = normalize_recalc_hess(normalized_batch["opt_recalc_hess"])
    if normalized_batch.get("opt_rescue_policy"):
        kwargs["opt_rescue_policy"] = normalized_batch["opt_rescue_policy"]
    if normalized_batch.get("opt_max_rescue") is not None:
        kwargs["opt_max_rescue"] = int(normalized_batch["opt_max_rescue"])

    # SCF controls
    if normalized_batch.get("scf_max_iter") is not None:
        kwargs["scf_max_iter"] = int(normalized_batch["scf_max_iter"])
    if normalized_batch.get("scf_convergence"):
        kwargs["scf_convergence"] = normalized_batch["scf_convergence"]
    if normalized_batch.get("scf_strategy"):
        kwargs["scf_strategy"] = normalized_batch["scf_strategy"]
    if normalized_batch.get("scf_orbital_inherit") is not None:
        kwargs["scf_orbital_inherit"] = bool(normalized_batch["scf_orbital_inherit"])

    # Per-role overrides
    for role_prefix in ("minimum_", "transition_state_"):
        for name in ("opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess"):
            field = f"{role_prefix}{name}"
            if normalized_batch.get(field) is not None:
                if name == "opt_recalc_hess":
                    kwargs[field] = normalize_recalc_hess(normalized_batch[field])
                else:
                    kwargs[field] = normalized_batch[field]

    return BatchMethodOptions(**kwargs)


def _build_effective(opts: BatchMethodOptions, is_ts: bool) -> dict[str, Any]:
    """Build the per-role effective dict.

    Combines ``resolve_role_options`` (trust/hessian/recalc) with engine
    constants (max_cycles, opt_level, scf trio).
    """
    role_opts = opts.resolve_role_options(is_ts)
    effective: dict[str, Any] = {}

    # Role-override fields from resolve_role_options
    for name in _ROLE_OVERRIDE_EFFECTIVE:
        if name in role_opts:
            effective[name] = role_opts[name]

    # Shared fields with engine constants
    effective["max_cycles"] = (
        opts.opt_max_iter if opts.opt_max_iter is not None else _ENGINE_MAX_CYCLES_DEFAULT
    )
    effective["opt_level"] = opts.opt_convergence
    effective["scf_maxiter"] = opts.scf_max_iter
    effective["scf_convergence"] = opts.scf_convergence
    effective["scf_strategy"] = opts.scf_strategy

    return effective


def _track_sources(
    user_batch: dict[str, Any],
    effective: dict[str, Any],
    is_ts: bool,
) -> dict[str, str]:
    """Determine the provenance of each effective field value.

    Returns a dict mapping effective field names to one of:
    ``"user"`` (explicit user value incl. role override),
    ``"role_default"`` (from :data:`ROLE_DEFAULTS`), or
    ``"default"`` (dataclass / product default).
    """
    role = "ts" if is_ts else "int"
    prefix = _ROLE_PREFIX[role]
    sources: dict[str, str] = {}

    # Role-override fields: trace the resolution chain
    for name in _ROLE_OVERRIDE_EFFECTIVE:
        if name not in effective:
            continue
        value = effective[name]
        # Check if user explicitly provided the role override
        role_field = f"{prefix}{name}"
        if role_field in user_batch:
            sources[name] = "user"
        # Check if user explicitly provided the common field
        elif name in user_batch:
            sources[name] = "user"
        # Check if value matches ROLE_DEFAULTS for this role
        elif name in ROLE_DEFAULTS.get(role, {}) and value == ROLE_DEFAULTS[role][name]:
            sources[name] = "role_default"
        else:
            sources[name] = "default"

    # Shared fields: user vs dataclass/product default
    for eff_key, user_key in _SHARED_SOURCE_KEYS.items():
        if eff_key in effective:
            sources[eff_key] = "user" if user_key in user_batch else "default"

    return sources


def _build_orca_summary(effective: dict[str, Any], is_ts: bool) -> list[str]:
    """Build a human-readable ORCA keyword list mirroring the engine mapping.

    Keywords mirror ``cccp/qc/interfaces/orca_ts.py`` and
    ``cccp/qc/interfaces/orca.py``.
    """
    parts: list[str] = []

    # Opt level keyword
    level = str(effective.get("opt_level", "tight")).lower()
    kw = _OPT_LEVEL_KEYWORDS.get(level)
    if kw:
        parts.append(kw)

    # SCF convergence keyword
    scf_conv = str(effective.get("scf_convergence", "tight")).lower()
    scf_kw = _SCF_CONVERGENCE_KEYWORDS.get(scf_conv)
    if scf_kw:
        parts.append(scf_kw)

    # MaxIter (always present — engine constant)
    max_cycles = effective.get("max_cycles")
    if max_cycles is not None:
        parts.append(f"MaxIter {int(max_cycles)}")

    # Trust radius (only when resolved, not None)
    trust = effective.get("opt_trust_radius")
    if trust is not None:
        parts.append(f"Trust {float(trust):g}")

    # Calc_Hess (only when initial_hessian == "calculate")
    hessian = effective.get("opt_initial_hessian")
    if hessian == "calculate":
        parts.append("Calc_Hess")

    # Recalc_Hess (only when an integer)
    recalc = effective.get("opt_recalc_hess")
    if isinstance(recalc, int):
        parts.append(f"Recalc_Hess {recalc}")

    # SCF strategy keyword (only when not "normal")
    strategy = str(effective.get("scf_strategy", "normal")).lower()
    if strategy != "normal":
        strat_kw = _SCF_STRATEGY_KEYWORDS.get(strategy)
        if strat_kw:
            parts.append(strat_kw)

    return parts


# ── Endpoint ─────────────────────────────────────────────────────────────


@router.post("/batch-optimize/config-preview")
def batch_optimize_config_preview(
    body: BatchConfigPreviewRequest,
) -> dict[str, Any]:
    """Return normalised config preview with per-role effective options.

    Pipeline:
    1. Normalise / validate the raw method dict via catalog.
    2. Construct ``BatchMethodOptions`` from the normalised values.
    3. Build per-role effective dicts + source provenance.
    4. Generate human-readable ORCA keyword summaries.
    """
    from acp.catalog import (
        METHOD_SCHEMAS,
        get_method_schema,
        normalize_and_validate_method_config,
    )

    schema = get_method_schema("batch_optimize")
    if schema is None:
        # Should never happen — batch_optimize is a core schema.
        schema = METHOD_SCHEMAS.get("batch_optimize", {})

    method_payload = {"levels": body.method.get("levels", {"batch": body.method})}

    # If body.method already has "levels", use it as-is; otherwise
    # treat the whole method dict as the batch level.
    if "levels" not in body.method:
        method_payload = {"levels": {"batch": body.method}}

    normalised, errors = normalize_and_validate_method_config(method_payload, schema)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    normalised_batch = normalised.get("batch", {})

    # User's original batch-level input for source tracking
    user_batch = (
        body.method.get("levels", {}).get("batch", {}) if "levels" in body.method else body.method
    )

    try:
        opts = _build_batch_method_options(normalised_batch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    roles: dict[str, dict[str, Any]] = {}
    orca_summary: dict[str, list[str]] = {}

    for is_ts, role_key in ((True, "ts"), (False, "int")):
        effective = _build_effective(opts, is_ts)
        sources = _track_sources(user_batch, effective, is_ts)
        roles[role_key] = {"effective": effective, "sources": sources}
        orca_summary[role_key] = _build_orca_summary(effective, is_ts)

    return {
        "schema": "batch_optimize_preview_v1",
        "common": normalised_batch,
        "roles": roles,
        "orca_summary": orca_summary,
    }


__all__ = ["router"]
