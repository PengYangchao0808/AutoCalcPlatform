"""
Protocol Specification Adapter
==============================

Bridge helpers between composable workflow specs and the flat ProtocolSpec model.

Author: QCcalc Team
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from conformer_search.core.protocols import (
    ProtocolSpec,
    _get_default_protocol_config,
    resolve_protocol_spec,
)
from conformer_search.core.specs import ConformerWorkflowSpec, PROTOCOL_REGISTRY


class ProtocolAmbiguityError(ValueError):
    """Raised when a removed/ambiguous protocol name is requested."""


_REMOVED_NAMES: dict[str, str] = {
    "zero": "Use 'censo-zero' for the CENSO funnel.",
    "lite": "Use 'censo-lite' for the CENSO funnel.",
    "full": "Use 'censo-full' for the CENSO funnel.",
    "benchmark": "Use 'reference-sp' for high-level SP or 'acp benchmark' for meta-comparison.",
}

ALIASES: dict[str, str] = {
    "default": "ext",
}


def resolve_any_protocol(
    name: str,
    config: dict[str, Any] | None = None,
) -> ConformerWorkflowSpec:
    """Resolve any public protocol name to a workflow specification."""
    requested = (name or "ext").strip().lower() or "ext"
    if requested == "default":
        configured_default = (config or {}).get("protocols", {}).get("default", "ext")
        if isinstance(configured_default, str) and configured_default.strip():
            requested = configured_default.strip().lower()
        else:
            requested = "ext"

    if requested in _REMOVED_NAMES:
        raise ProtocolAmbiguityError(
            f"Protocol {requested!r} is ambiguous and has been removed. {_REMOVED_NAMES[requested]}"
        )

    if requested in PROTOCOL_REGISTRY:
        return PROTOCOL_REGISTRY[requested]

    alias = ALIASES.get(requested)
    if alias is not None and alias in PROTOCOL_REGISTRY:
        return PROTOCOL_REGISTRY[alias]

    available = ", ".join(sorted(PROTOCOL_REGISTRY))
    raise ValueError(f"Unknown protocol: {name!r}. Available: {available}")


def workflow_spec_to_protocol_spec(spec: ConformerWorkflowSpec) -> ProtocolSpec:
    """Adapt a workflow specification into the flat ProtocolSpec model."""
    protocol_name = spec.name.strip().lower()
    config = workflow_spec_to_config_overrides(spec)
    protocol_spec = resolve_protocol_spec(config, protocol_name)
    if protocol_spec.name != protocol_name:
        return replace(protocol_spec, name=protocol_name)
    return protocol_spec


def workflow_spec_to_config_overrides(spec: ConformerWorkflowSpec) -> dict[str, Any]:
    """Convert a workflow specification into ProtocolSpec config overrides."""
    protocol_name = spec.name.strip().lower()
    config_protocol_name = (
        protocol_name if spec.family == "censo" and protocol_name.startswith("censo-")
        else protocol_name
    )
    protocol_cfg = deepcopy(_get_default_protocol_config(config_protocol_name))

    protocol_cfg["two_stage_enabled"] = spec.search.two_stage_enabled

    if protocol_name in {"allopt", "reference-sp"}:
        candidate_cap = spec.search.n_cluster_cap or protocol_cfg.get("ngeom_max", 6)
        protocol_cfg["ngeom_default"] = candidate_cap
        protocol_cfg["ngeom_max"] = candidate_cap

    funnel_cfg = protocol_cfg.setdefault("funnel", {})
    funnel_cfg["clustering_mode"] = spec.search.clusterer

    if spec.search.backend == "external_xyz":
        funnel_cfg["search_mode"] = "external_xyz"
    elif spec.search.backend == "crest":
        funnel_cfg["search_mode"] = (
            "crest_two_stage_gfn0_to_gfn2"
            if spec.search.two_stage_enabled
            else "crest_gfn2"
        )
    elif spec.search.backend == "molclus_xtb_md":
        funnel_cfg["search_mode"] = "molclus_xtb_md"

    if spec.recipe.part0_window_kcal is not None:
        if protocol_name in {"censo-full", "censo-full-safe"}:
            funnel_cfg["prescreen_window_kcal"] = spec.recipe.part0_window_kcal
        else:
            funnel_cfg["survivor_window_kcal"] = spec.recipe.part0_window_kcal

    if spec.recipe.part1_window_kcal is not None:
        funnel_cfg["screening_window_kcal"] = spec.recipe.part1_window_kcal

    if spec.recipe.part2_window_kcal is not None:
        funnel_cfg["survivor_window_kcal"] = spec.recipe.part2_window_kcal

    if spec.recipe.boltzmann_cutoff is not None:
        funnel_cfg["boltzmann_cutoff"] = spec.recipe.boltzmann_cutoff

    if protocol_name == "censo-lite":
        funnel_cfg["top2_fallback_enabled"] = spec.recipe.top2_fallback_enabled
        funnel_cfg["use_mrrho_like_correction"] = True

    handoff_cfg = protocol_cfg.setdefault("handoff", {})
    if spec.recipe.select_mode == "boltzmann_ensemble":
        handoff_cfg["ranking_after_handoff"] = "final_sp_plus_boltzmann"
    elif protocol_name == "censo-zero":
        handoff_cfg["ranking_after_handoff"] = "xtb_energy"
    else:
        handoff_cfg["ranking_after_handoff"] = "final_sp_minimum"

    if spec.recipe.top2_fallback_enabled:
        handoff_cfg["fallback_mode"] = "optimize_top2_if_gap_small"
    if spec.recipe.top2_gap_kcal is not None:
        handoff_cfg["small_gap_kcal"] = spec.recipe.top2_gap_kcal

    final_cfg = protocol_cfg.setdefault("final_opt_sp", {})
    if spec.energy.final_sp_method is not None:
        final_cfg["final_sp_method"] = spec.energy.final_sp_method.upper()
        if spec.energy.final_sp_method.lower() == "wb97x-d4":
            final_cfg["final_sp_method"] = "wB97X-D4"
        if spec.energy.final_sp_method.lower() == "dlpno-ccsdt":
            final_cfg["final_sp_method"] = "DLPNO-CCSD(T)"
    if spec.energy.final_basis is not None:
        final_cfg["final_sp_basis"] = spec.energy.final_basis

    stages_cfg = protocol_cfg.setdefault("stages", {})
    if protocol_name == "reference-sp" or spec.search.backend == "external_xyz":
        stages_cfg.update({
            "crest": False,
            "clustering": False,
            "optimization": False,
            "frequency": False,
            "single_point": True,
            "shermo": False,
        })
    elif protocol_name == "censo-zero":
        stages_cfg.update({
            "crest": True,
            "clustering": True,
            "optimization": False,
            "frequency": False,
            "single_point": True,
            "shermo": False,
        })

    return {
        "protocols": {
            config_protocol_name: protocol_cfg,
        }
    }


def stages_from_workflow_spec(spec: ConformerWorkflowSpec) -> list[str]:
    """Return canonical ACP stage names for a workflow specification."""
    protocol_name = spec.name.strip().lower()
    protocol_spec = workflow_spec_to_protocol_spec(spec)

    if spec.family == "reference":
        return ["single_point"]

    stage_names = ["embed_smiles"]

    if protocol_spec.enable_crest:
        stage_names.append("crest_search")
    if protocol_spec.enable_clustering:
        stage_names.append("isostat_cluster")

    if spec.recipe.run_part0:
        stage_names.append("censo_part0")
    if spec.recipe.run_part1:
        stage_names.append("censo_part1")
    if spec.recipe.run_part2:
        stage_names.append("censo_part2")
    if spec.recipe.run_part3:
        stage_names.append("censo_part3")

    if len(stage_names) == 1 and (
        protocol_name == "censo-zero"
        or _is_single_point_only_protocol(protocol_spec)
    ):
        stage_names.append("single_point")

    return stage_names


def _is_single_point_only_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Return True when the legacy spec reduces to a public SP-only workflow."""
    return (
        not protocol_spec.enable_crest
        and not protocol_spec.enable_clustering
        and not protocol_spec.enable_optimization
        and not protocol_spec.enable_frequency
        and protocol_spec.enable_single_point
    )


__all__ = [
    "ProtocolAmbiguityError",
    "ALIASES",
    "resolve_any_protocol",
    "workflow_spec_to_protocol_spec",
    "workflow_spec_to_config_overrides",
    "stages_from_workflow_spec",
]
