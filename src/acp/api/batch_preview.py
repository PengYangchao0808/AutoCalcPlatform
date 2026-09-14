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

from acp.calculations.batch.effective_config import (
    build_effective_role,
    build_opts_from_method_dict,
    build_orca_summary,
)
from acp.calculations.batch.options import (
    _ROLE_OVERRIDE_FIELDS,
    ROLE_DEFAULTS,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Base field → effective-dict key (names deliberately differ: opt_max_iter→max_cycles etc.)
_ROLE_EFFECTIVE_KEY: dict[str, str] = {
    "opt_max_iter": "max_cycles",
    "opt_convergence": "opt_level",
    "opt_trust_radius": "opt_trust_radius",
    "opt_initial_hessian": "opt_initial_hessian",
    "opt_recalc_hess": "opt_recalc_hess",
    "opt_rescue_policy": "rescue_policy",
    "opt_max_rescue": "max_rescue",
    "scf_max_iter": "scf_maxiter",
    "scf_convergence": "scf_convergence",
    "scf_strategy": "scf_strategy",
}


# ── Request / response models ───────────────────────────────────────────


class BatchConfigPreviewRequest(BaseModel):
    """POST body for the config-preview endpoint."""

    method: dict[str, Any] = {}


# ── Helpers ──────────────────────────────────────────────────────────────

_ROLE_PREFIX: dict[str, str] = {
    "int": "minimum_",
    "ts": "transition_state_",
}


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

    def _explicit(value: Any) -> bool:
        return value is not None and value != ""

    sources: dict[str, str] = {}
    for name in _ROLE_OVERRIDE_FIELDS:
        eff_key = _ROLE_EFFECTIVE_KEY[name]
        if eff_key not in effective:
            continue
        role_value = user_batch.get(f"{prefix}{name}")
        common_value = user_batch.get(name)
        if _explicit(role_value):
            sources[eff_key] = "user"
        elif _explicit(common_value):
            sources[eff_key] = "user"
        elif (
            name in ROLE_DEFAULTS.get(role, {}) and effective[eff_key] == ROLE_DEFAULTS[role][name]
        ):
            sources[eff_key] = "role_default"
        else:
            sources[eff_key] = "default"

    return sources


# ── Endpoint ─────────────────────────────────────────────────────────────


@router.post("/batch-optimize/config-preview")
def batch_optimize_config_preview(
    body: BatchConfigPreviewRequest,
) -> dict[str, Any]:
    """Return normalised config preview with per-role effective options.

    Pipeline:
    1. Normalise / validate the raw method dict via catalog.
    2. Construct ``BatchMethodOptions`` via the shared submission-time
       builder (``build_opts_from_method_dict``) — the same generator the
       engine consumes, so preview and execution cannot diverge.
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
        opts = build_opts_from_method_dict(normalised_batch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    roles: dict[str, dict[str, Any]] = {}
    orca_summary: dict[str, list[str]] = {}

    for is_ts, role_key in ((True, "ts"), (False, "int")):
        effective = build_effective_role(opts, is_ts)
        sources = _track_sources(user_batch, effective, is_ts)
        roles[role_key] = {"effective": effective, "sources": sources}
        orca_summary[role_key] = build_orca_summary(effective)

    return {
        "schema": "batch_optimize_preview_v1",
        "common": normalised_batch,
        "roles": roles,
        "orca_summary": orca_summary,
        "config_key": opts.cache_key,
    }


__all__ = ["router"]
