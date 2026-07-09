"""Protocol unification coverage tests."""

from __future__ import annotations

import pytest

from conformer_search.config import _get_default_config
from conformer_search.core import (
    ALIASES,
    SUPPORTED_PROTOCOLS,
    is_benchmark_protocol,
    is_censo_protocol,
    is_ext_protocol,
    is_full_protocol,
    resolve_any_protocol,
    resolve_protocol_name,
    resolve_protocol_spec,
    stages_from_workflow_spec,
    workflow_spec_to_protocol_spec,
)
from conformer_search.core.specs import PROTOCOL_REGISTRY


def test_protocol_registry_and_supported_protocols_cover_all_public_names() -> None:
    """Both registries expose the full unified protocol surface."""
    registry_expected = {
        "censo-zero",
        "censo-lite",
        "censo-full",
        "censo-full-safe",
        "allopt",
        "reference-sp",
        "ext",
    }
    supported_expected = {
        "censo-zero",
        "censo-lite",
        "censo-full",
        "censo-full-safe",
        "allopt",
        "reference-sp",
        "ext",
    }

    assert registry_expected.issubset(PROTOCOL_REGISTRY)
    assert supported_expected.issubset(SUPPORTED_PROTOCOLS)


def test_resolve_protocol_name_honors_default_alias() -> None:
    """The default alias resolves through the validated config value."""
    config = _get_default_config()
    config["protocols"]["default"] = "censo-full"

    assert resolve_protocol_name(config, "default") == "censo-full"


def test_resolve_protocol_spec_supports_new_route_names() -> None:
    """Expanded protocol names resolve to routing families."""
    config = _get_default_config()

    full_spec = resolve_protocol_spec(config, "censo-full-safe")
    assert full_spec.name == "censo-full-safe"
    assert is_full_protocol(full_spec)
    assert is_censo_protocol(full_spec)

    allopt_spec = resolve_protocol_spec(config, "allopt")
    assert allopt_spec.name == "allopt"
    assert is_ext_protocol(allopt_spec)

    reference_spec = resolve_protocol_spec(config, "reference-sp")
    assert reference_spec.name == "reference-sp"
    assert is_benchmark_protocol(reference_spec)
    assert reference_spec.enable_crest is False
    assert reference_spec.enable_clustering is False
    assert reference_spec.enable_optimization is False
    assert reference_spec.final_sp_method == "DLPNO-CCSD(T)"


def test_resolve_protocol_spec_rejects_unknown_protocol() -> None:
    """Unknown protocol names now raise instead of silently falling back."""
    with pytest.raises(ValueError, match="Unknown protocol"):
        resolve_protocol_spec(_get_default_config(), "unknown-protocol")


def test_resolve_any_protocol_rejects_bare_legacy_names() -> None:
    """Bare legacy names (full, lite, zero, benchmark) raise ambiguity errors."""
    from conformer_search.core.spec_adapter import ProtocolAmbiguityError

    with pytest.raises(ProtocolAmbiguityError, match="Protocol 'full' is ambiguous"):
        resolve_any_protocol("full")
    with pytest.raises(ProtocolAmbiguityError, match="Protocol 'benchmark' is ambiguous"):
        resolve_any_protocol("benchmark")
    with pytest.raises(ProtocolAmbiguityError, match="Protocol 'zero' is ambiguous"):
        resolve_any_protocol("zero")
    with pytest.raises(ProtocolAmbiguityError, match="Protocol 'lite' is ambiguous"):
        resolve_any_protocol("lite")
    assert resolve_any_protocol("default").name == "ext"


def test_workflow_spec_adapter_derives_specs_and_stage_lists() -> None:
    """Workflow presets adapt cleanly into the ProtocolSpec surface."""
    reference_protocol = workflow_spec_to_protocol_spec(PROTOCOL_REGISTRY["reference-sp"])
    assert reference_protocol.name == "reference-sp"
    assert reference_protocol.enable_crest is False
    assert reference_protocol.enable_single_point is True

    assert stages_from_workflow_spec(PROTOCOL_REGISTRY["reference-sp"]) == [
        "single_point",
    ]
    assert stages_from_workflow_spec(PROTOCOL_REGISTRY["censo-zero"]) == [
        "embed_smiles",
        "crest_search",
        "isostat_cluster",
        "censo_part0",
        "censo_part3",
    ]
