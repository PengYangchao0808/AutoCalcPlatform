"""Protocol parity and registry coverage tests."""

# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from conformer_search.config import _get_default_config
from conformer_search.core.protocols import ProtocolSpec, _get_default_protocol_config
from conformer_search.core.spec_adapter import resolve_any_protocol, workflow_spec_to_protocol_spec
from conformer_search.core.specs import PROTOCOL_REGISTRY


DEFAULTS_YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"

CLI_PROTOCOL_NAMES = (
    "censo-zero",
    "censo-lite",
    "censo-full",
    "censo-full-safe",
    "allopt",
    "reference-sp",
    "ext",
)

CENSO_YAML_PROTOCOL_NAMES = (
    "censo-zero",
    "censo-lite",
    "censo-full",
    "censo-full-safe",
    "allopt",
    "reference-sp",
)

STAGE_NAMES = (
    "crest",
    "clustering",
    "optimization",
    "frequency",
    "single_point",
    "shermo",
)


def _load_defaults_yaml() -> dict[str, Any]:
    with open(DEFAULTS_YAML_PATH, "r", encoding="utf-8") as handle:
        return cast(dict[str, Any], yaml.safe_load(handle))


def _canonical_protocol_config(protocol_cfg: dict[str, Any]) -> dict[str, Any]:
    stages = protocol_cfg.get("stages") or {}
    funnel = protocol_cfg.get("funnel") or {}
    handoff = protocol_cfg.get("handoff") or {}
    final_opt_sp = protocol_cfg.get("final_opt_sp") or {}

    return {
        "stages": {stage: stages.get(stage) for stage in STAGE_NAMES},
        "two_stage_enabled": protocol_cfg.get("two_stage_enabled"),
        "ngeom_default": protocol_cfg.get("ngeom_default"),
        "ngeom_max": protocol_cfg.get("ngeom_max"),
        "funnel": dict(funnel),
        "handoff": dict(handoff),
        "opt_engine": protocol_cfg.get("opt_engine"),
        "freq_engine": protocol_cfg.get("freq_engine"),
        "final_opt_sp": {
            "final_sp_method": final_opt_sp.get("final_sp_method"),
            "final_sp_basis": final_opt_sp.get("final_sp_basis"),
        },
    }


def test_protocol_registry_covers_all_cli_names() -> None:
    """Every public CLI protocol is registered and resolvable."""
    assert set(CLI_PROTOCOL_NAMES).issubset(PROTOCOL_REGISTRY)

    for protocol_name in CLI_PROTOCOL_NAMES:
        assert resolve_any_protocol(protocol_name).name == protocol_name


def test_workflow_spec_to_protocol_spec_for_all() -> None:
    """Each public workflow preset adapts into a ProtocolSpec."""
    for protocol_name in CLI_PROTOCOL_NAMES:
        protocol_spec = workflow_spec_to_protocol_spec(PROTOCOL_REGISTRY[protocol_name])

        assert isinstance(protocol_spec, ProtocolSpec)
        assert protocol_spec.name == protocol_name


def test_censo_yaml_sections_exist() -> None:
    """defaults.yaml exposes all explicit CENSO-family/public protocol sections."""
    yaml_protocols = cast(dict[str, Any], _load_defaults_yaml()["protocols"])

    for protocol_name in CENSO_YAML_PROTOCOL_NAMES:
        assert protocol_name in yaml_protocols
