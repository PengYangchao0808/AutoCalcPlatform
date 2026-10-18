"""T44 — trusted-plugin pilot: frozen metadata and discovery/load boundaries.

The read-only ORCA parser (:mod:`cccp.analysis.orca_parser`, T18) is the
capability template.  These tests pin the *boundaries*, not the science:

* empty-set catalog still carries built-in identity;
* metadata fields are complete and frozen;
* discovery never runs a loader, rejects reserved/duplicate names and
  incompatible API ranges;
* plugins are disabled by default and reject load before enablement;
* an enabled pilot parses exactly like the built-in parser;
* missing/unavailable/loader failures are typed.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cccp.analysis.orca_parser import OrcaOutputParser
from cccp.calculation.requests import TaskKind
from cccp.plugin import (
    BUILTIN_EXECUTION_STATION,
    ORCA_READONLY_PARSER_NAME,
    PLUGIN_API_VERSION,
    ApiCompatibility,
    ErrorSemantics,
    OrcaReadonlyParserPlugin,
    PluginCandidate,
    PluginCapability,
    PluginDisabledError,
    PluginHost,
    PluginLoadError,
    PluginMetadata,
    PluginMetadataError,
    PluginNameConflictError,
    PluginUnavailableError,
    PluginVersionMismatchError,
    ResourceRequirements,
    builtin_catalog,
    discover_plugins,
    readonly_parser_metadata,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orca_optfreq_real_sections.txt"


def _metadata(name: str, **overrides: object) -> PluginMetadata:
    values: dict[str, object] = {
        "name": name,
        "distribution": "example-dist",
        "version": "1.0.0",
        "api": ApiCompatibility(),
        "capabilities": (PluginCapability(name="readonly_parser", schema="orca_calculation"),),
        "science_software": (),
        "resources": ResourceRequirements(),
        "license": "MIT",
        "dependencies": (),
        "error_semantics": ErrorSemantics(),
        "default_enabled": False,
    }
    values.update(overrides)
    return PluginMetadata(**values)  # type: ignore[arg-type]


# ── empty set + built-in identity ───────────────────────────────────────


def test_builtin_catalog_has_identity_and_explicit_empty_set() -> None:
    catalog = builtin_catalog()

    assert catalog.is_empty is True
    assert catalog.plugins == ()
    assert catalog.names() == ()
    assert catalog.builtin.distribution == "cccp"
    assert catalog.builtin.version
    assert catalog.builtin.execution_station == BUILTIN_EXECUTION_STATION
    assert catalog.to_dict()["plugins"] == []
    assert catalog.to_dict()["is_empty"] is True


def test_builtin_only_host_discovers_nothing_and_enable_is_unavailable() -> None:
    host = PluginHost.builtin_only()

    assert host.catalog.is_empty is True
    assert host.enabled_names() == ()
    with pytest.raises(PluginUnavailableError):
        host.enable(ORCA_READONLY_PARSER_NAME)


# ── metadata completeness / immutability ────────────────────────────────


def test_pilot_metadata_freezes_all_required_fields() -> None:
    meta = readonly_parser_metadata()

    assert meta.name == ORCA_READONLY_PARSER_NAME
    assert meta.distribution == "cccp"
    assert meta.version
    assert meta.api.compatible_range
    assert meta.capabilities
    assert meta.capabilities[0].schema == "orca_calculation"
    assert meta.science_software[0].software == "orca"
    # Tri-state evidence: version window declared, not verified with a binary.
    assert meta.science_software[0].verified is False
    assert meta.resources.requires_subprocess is False
    assert meta.license == "MIT"
    assert meta.error_semantics.never_overrides_builtin is True
    assert meta.default_enabled is False
    assert meta.to_dict()["dependencies"] == []


def test_pilot_metadata_is_frozen() -> None:
    meta = readonly_parser_metadata()

    with pytest.raises(FrozenInstanceError):
        meta.name = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"license": ""},
        {"capabilities": ()},
        {"error_semantics": ErrorSemantics(never_overrides_builtin=False)},
    ],
)
def test_metadata_rejects_missing_contract_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(PluginMetadataError):
        _metadata("third_party_parser", **overrides)


# ── discovery is inert ──────────────────────────────────────────────────


def test_discovery_never_executes_a_loader() -> None:
    calls: list[str] = []

    def loader() -> object:
        calls.append("called")
        return object()

    catalog = discover_plugins(
        (PluginCandidate(metadata=_metadata("third_party_parser"), loader=loader),)
    )

    assert calls == []
    assert catalog.names() == ("third_party_parser",)
    assert catalog.plugins[0].default_enabled is False


def test_discovery_rejects_reserved_builtin_task_name() -> None:
    reserved = TaskKind.FREQUENCY.value
    with pytest.raises(PluginNameConflictError) as excinfo:
        discover_plugins((PluginCandidate(metadata=_metadata(reserved)),))

    assert excinfo.value.name == reserved
    assert "reserved built-in" in excinfo.value.reason


def test_discovery_rejects_duplicate_plugin_names() -> None:
    candidates = (
        PluginCandidate(metadata=_metadata("third_party_parser")),
        PluginCandidate(metadata=_metadata("third_party_parser")),
    )
    with pytest.raises(PluginNameConflictError) as excinfo:
        discover_plugins(candidates)

    assert "duplicate" in excinfo.value.reason


def test_host_construction_cannot_override_builtin() -> None:
    with pytest.raises(PluginNameConflictError):
        PluginHost(candidates=(PluginCandidate(metadata=_metadata(TaskKind.OPTIMIZE.value)),))


def test_incompatible_api_range_is_rejected() -> None:
    meta = _metadata(
        "third_party_parser",
        api=ApiCompatibility(compatible_range=">=2,<3"),
    )
    with pytest.raises(PluginVersionMismatchError) as excinfo:
        discover_plugins((PluginCandidate(metadata=meta),))

    assert excinfo.value.name == "third_party_parser"
    assert excinfo.value.declared == ">=2,<3"
    assert excinfo.value.host_api_version == PLUGIN_API_VERSION


# ── disabled by default / enablement ────────────────────────────────────


def test_pilot_is_disabled_by_default_and_load_is_rejected() -> None:
    host = PluginHost.with_builtin_pilot()

    assert host.catalog.names() == (ORCA_READONLY_PARSER_NAME,)
    assert host.enabled_names() == ()
    with pytest.raises(PluginDisabledError):
        host.load(ORCA_READONLY_PARSER_NAME)


def test_enable_returns_metadata_and_disable_evicts_instance() -> None:
    host = PluginHost.with_builtin_pilot()

    meta = host.enable(ORCA_READONLY_PARSER_NAME)
    assert meta.name == ORCA_READONLY_PARSER_NAME
    first = host.load(ORCA_READONLY_PARSER_NAME)
    assert host.load(ORCA_READONLY_PARSER_NAME) is first

    host.disable(ORCA_READONLY_PARSER_NAME)
    assert host.is_enabled(ORCA_READONLY_PARSER_NAME) is False
    with pytest.raises(PluginDisabledError):
        host.load(ORCA_READONLY_PARSER_NAME)

    host.enable(ORCA_READONLY_PARSER_NAME)
    assert host.load(ORCA_READONLY_PARSER_NAME) is not first


def test_enabled_candidate_without_loader_is_unavailable() -> None:
    host = PluginHost(
        candidates=(PluginCandidate(metadata=_metadata("third_party_parser"), loader=None),)
    )
    host.enable("third_party_parser")

    with pytest.raises(PluginUnavailableError) as excinfo:
        host.load("third_party_parser")
    assert "no in-process loader" in excinfo.value.reason


def test_loader_failure_is_typed_with_cause() -> None:
    def broken() -> object:
        raise RuntimeError("boom")

    host = PluginHost(
        candidates=(PluginCandidate(metadata=_metadata("third_party_parser"), loader=broken),)
    )
    host.enable("third_party_parser")

    with pytest.raises(PluginLoadError) as excinfo:
        host.load("third_party_parser")
    assert isinstance(excinfo.value.cause, RuntimeError)
    assert "boom" in str(excinfo.value)


# ── happy path: pilot equals built-in parser ───────────────────────────


def test_enabled_pilot_parses_like_builtin() -> None:
    host = PluginHost.with_builtin_pilot()
    host.enable(ORCA_READONLY_PARSER_NAME)
    plugin = host.load(ORCA_READONLY_PARSER_NAME)
    assert isinstance(plugin, OrcaReadonlyParserPlugin)

    text = FIXTURE.read_text(encoding="utf-8")
    builtin = OrcaOutputParser()
    assert plugin.parse_text(text) == builtin.parse_text(text)
    assert plugin.parse(FIXTURE) == builtin.parse(FIXTURE)


def test_api_version_range_parser_is_fail_closed() -> None:
    from cccp.plugin import api_version_in_range

    assert api_version_in_range(1, ">=1,<2") is True
    assert api_version_in_range(2, ">=1,<2") is False
    assert api_version_in_range(1, "") is False
    assert api_version_in_range(1, "not-a-range") is False
