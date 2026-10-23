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

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from cccp.analysis.orca_parser import OrcaOutputParser
from cccp.calculation.requests import TaskKind
from cccp.plugin import (
    BUILTIN_EXECUTION_STATION,
    ORCA_READONLY_PARSER_NAME,
    PLUGIN_API_VERSION,
    SCIENCE_PLUGIN_ENTRY_POINT_GROUP,
    SCIENCE_PLUGIN_MANIFEST_FILENAME,
    ApiCompatibility,
    CapabilityKind,
    EntryPointPluginLoader,
    ErrorSemantics,
    OrcaReadonlyParserPlugin,
    PluginCandidate,
    PluginCapability,
    PluginDisabledError,
    PluginFixtureEvidence,
    PluginHost,
    PluginLicenseError,
    PluginLicensePolicy,
    PluginLoadError,
    PluginMetadata,
    PluginMetadataError,
    PluginNameConflictError,
    PluginRecipeRef,
    PluginTrustLevel,
    PluginUnavailableError,
    PluginValidatorRef,
    PluginVersionMismatchError,
    ResourceRequirements,
    builtin_catalog,
    discover_plugins,
    discover_science_plugins,
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


# ── A-21a: extended science manifest ───────────────────────────────────


def _extended_metadata(
    name: str = "vendor.readonly_validator", **overrides: object
) -> PluginMetadata:
    values: dict[str, object] = {
        "name": name,
        "distribution": "vendor-science-dist",
        "version": "2.0.0",
        "capabilities": (
            PluginCapability(
                name="readonly_validator",
                schema="acp2.science.validator",
                kind=CapabilityKind.PURE_ANALYSIS,
                implementation_id="vendor.science.validator",
                recipe_refs=(PluginRecipeRef(recipe_id="vendor.recipe.v1"),),
                validator_refs=(
                    PluginValidatorRef(
                        validator_id="check.vendor_units", validator_version="1.0.0"
                    ),
                ),
            ),
        ),
        "license": "MIT",
    }
    values.update(overrides)
    return PluginMetadata(**values)  # type: ignore[arg-type]


def test_extended_manifest_serialises_and_reloads_through_adapter() -> None:
    metadata = _extended_metadata(
        trust_level=PluginTrustLevel.TRUSTED,
        distribution_digest="sha256:" + "a" * 64,
        fixture_evidence=(
            PluginFixtureEvidence(
                fixture_id="vendor.validator.fixed-input",
                digest="sha256:" + "b" * 64,
                verified=True,
            ),
        ),
    )

    payload = metadata.to_dict()
    reloaded = PluginMetadata.from_dict(payload)

    assert reloaded == metadata
    assert reloaded.trust_level is PluginTrustLevel.TRUSTED
    assert reloaded.is_trusted is True
    assert reloaded.capabilities[0].kind is CapabilityKind.PURE_ANALYSIS
    assert reloaded.capabilities[0].implementation_id == "vendor.science.validator"
    assert reloaded.capabilities[0].recipe_refs[0].recipe_id == "vendor.recipe.v1"
    assert reloaded.capabilities[0].validator_refs[0].validator_id == "check.vendor_units"
    assert payload["distribution_digest"] == "sha256:" + "a" * 64
    assert payload["fixture_evidence"][0]["digest"] == "sha256:" + "b" * 64


def _legacy_manifest_dict(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "legacy_parser",
        "distribution": "legacy-dist",
        "version": "0.9.0",
        "api": {
            "api_version": PLUGIN_API_VERSION,
            "compatible_range": ">=1,<2",
            "schema_version": 1,
        },
        "capabilities": [{"name": "legacy_read", "schema": "legacy_output", "schema_version": 1}],
        "science_software": [],
        "resources": {"requires_subprocess": False, "requires_network": False},
        "license": "MIT",
        "dependencies": [],
        "error_semantics": {"never_overrides_builtin": True},
        "default_enabled": False,
    }
    payload.update(overrides)
    return payload


def test_legacy_metadata_loads_through_adapter_with_conservative_defaults() -> None:
    metadata = PluginMetadata.from_dict(_legacy_manifest_dict())

    assert metadata.name == "legacy_parser"
    assert metadata.capabilities[0].kind is CapabilityKind.PURE_ANALYSIS
    assert metadata.trust_level is PluginTrustLevel.UNTRUSTED
    assert metadata.is_trusted is False
    assert metadata.distribution_digest is None
    assert metadata.fixture_evidence == ()
    assert metadata.science_version_range == ">=1,<2"
    assert metadata.entry_point_group_version == 1
    # The adapter never grants trust just because a field was absent.
    assert metadata.has_verified_fixture_evidence is False


def test_legacy_metadata_claiming_builtin_override_is_rejected() -> None:
    payload = _legacy_manifest_dict(
        error_semantics={
            "pre_launch_typed": True,
            "partial_output_tolerant": True,
            "never_overrides_builtin": False,
        }
    )
    with pytest.raises(PluginMetadataError):
        PluginMetadata.from_dict(payload)


def test_legacy_metadata_unknown_license_raises_typed_license_error() -> None:
    payload = _legacy_manifest_dict(license="Proprietary-Unknown-1.0")
    with pytest.raises(PluginLicenseError) as excinfo:
        PluginMetadata.from_dict(payload)
    assert excinfo.value.licence == "Proprietary-Unknown-1.0"
    assert excinfo.value.plugin == "legacy_parser"


def test_empty_license_raises_typed_license_error() -> None:
    with pytest.raises(PluginLicenseError):
        _extended_metadata(license="")


def test_owner_can_narrow_the_license_policy() -> None:
    narrowed = PluginLicensePolicy.default().narrowed(excluded={"MIT"})
    with pytest.raises(PluginLicenseError):
        _extended_metadata(license="MIT", license_policy=narrowed)
    assert "MIT" not in narrowed.accepted_licenses
    assert "Apache-2.0" in narrowed.accepted_licenses


def test_trust_defaults_to_lowest_and_is_never_inferred() -> None:
    metadata = _extended_metadata()
    assert metadata.trust_level is PluginTrustLevel.UNTRUSTED
    assert PluginTrustLevel.lowest() is PluginTrustLevel.UNTRUSTED
    assert metadata.is_trusted is False
    # Installing, declaring a valid API range or naming a validator does not
    # promote trust.
    assert metadata.is_api_compatible(PLUGIN_API_VERSION) is True
    assert metadata.is_trusted is False


def test_trusted_without_fixture_evidence_is_rejected() -> None:
    with pytest.raises(PluginMetadataError, match="verified fixture evidence"):
        _extended_metadata(trust_level="trusted")
    with pytest.raises(PluginMetadataError, match="verified fixture evidence"):
        _extended_metadata(
            trust_level="trusted",
            fixture_evidence=(
                PluginFixtureEvidence(
                    fixture_id="vendor.validator.fixed-input",
                    digest="sha256:" + "b" * 64,
                    verified=False,
                ),
            ),
        )


def test_verified_alias_is_accepted_as_trusted_only_with_evidence() -> None:
    with pytest.raises(PluginMetadataError, match="verified fixture evidence"):
        _extended_metadata(trust_level="validated")
    trusted = _extended_metadata(
        trust_level="validated",
        fixture_evidence=(
            PluginFixtureEvidence(
                fixture_id="vendor.validator.fixed-input",
                digest="sha256:" + "c" * 64,
            ),
        ),
    )
    assert trusted.trust_level is PluginTrustLevel.TRUSTED
    assert trusted.is_trusted is True


@pytest.mark.parametrize(
    "mixed_fields",
    [
        {"delegates_to_task_kind": TaskKind.FREQUENCY.value},
    ],
)
def test_pure_analysis_capability_must_not_delegate(mixed_fields: dict[str, object]) -> None:
    with pytest.raises(PluginMetadataError):
        PluginCapability(
            name="readonly_validator",
            schema="acp2.science.validator",
            kind=CapabilityKind.PURE_ANALYSIS,
            **mixed_fields,
        )


def test_compute_capability_must_delegate_to_one_existing_task_kind() -> None:
    with pytest.raises(PluginMetadataError):
        PluginCapability(
            name="science_compute",
            schema="acp2.science.result",
            kind=CapabilityKind.COMPUTE,
        )
    with pytest.raises(PluginMetadataError):
        PluginCapability(
            name="science_compute",
            schema="acp2.science.result",
            kind=CapabilityKind.COMPUTE,
            delegates_to_task_kind="not_a_task_kind",
        )
    capability = PluginCapability(
        name="science_compute",
        schema="acp2.science.result",
        kind=CapabilityKind.COMPUTE,
        delegates_to_task_kind=TaskKind.SINGLEPOINT.value,
    )
    assert capability.delegates_to_task_kind == TaskKind.SINGLEPOINT.value


def test_pure_analysis_only_plugin_cannot_require_a_subprocess() -> None:
    with pytest.raises(PluginMetadataError, match="requires_subprocess"):
        _extended_metadata(
            resources=ResourceRequirements(requires_subprocess=True),
        )


def test_science_version_range_must_include_the_host() -> None:
    with pytest.raises(PluginVersionMismatchError):
        discover_plugins(
            (PluginCandidate(metadata=_extended_metadata(science_version_range=">=2,<3")),)
        )


class _FakeDistribution:
    def __init__(self, manifest: str | None) -> None:
        self._manifest = manifest
        self.read_paths: list[str] = []

    def read_text(self, filename: str) -> str | None:
        self.read_paths.append(filename)
        return self._manifest


class _FakeEntryPoint:
    group = SCIENCE_PLUGIN_ENTRY_POINT_GROUP

    def __init__(self, name: str, manifest: str | None) -> None:
        self.name = name
        self.value = "vendor_plugin:PLUGIN"
        self.dist = _FakeDistribution(manifest)
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        return object()


def test_entry_point_discovery_reads_manifest_without_loading_plugin() -> None:
    manifest = json.dumps(_extended_metadata(name="vendor.readonly_validator").to_dict())
    entry_point = _FakeEntryPoint("vendor_validator", manifest)
    unrelated = _FakeEntryPoint("unrelated", manifest)
    unrelated.group = "unrelated.group"

    catalog = discover_science_plugins(entry_points=[entry_point, unrelated])

    assert entry_point.load_calls == 0
    assert unrelated.load_calls == 0
    assert catalog.names() == ("vendor.readonly_validator",)
    assert catalog.discovery_mode == "entry_points"
    assert catalog.entry_points_scanned == 1
    assert entry_point.dist.read_paths == [SCIENCE_PLUGIN_MANIFEST_FILENAME]


def test_entry_point_loader_is_lazy_until_called() -> None:
    manifest = json.dumps(_extended_metadata(name="vendor.readonly_validator").to_dict())
    entry_point = _FakeEntryPoint("vendor_validator", manifest)
    discover_science_plugins(entry_points=[entry_point])
    loader = EntryPointPluginLoader(entry_point)

    assert entry_point.load_calls == 0
    loader()
    assert entry_point.load_calls == 1


def test_entry_point_without_manifest_is_rejected_without_importing() -> None:
    entry_point = _FakeEntryPoint("vendor_validator", None)
    with pytest.raises(PluginMetadataError, match="no cccp_science_plugin.json"):
        discover_science_plugins(entry_points=[entry_point])
    assert entry_point.load_calls == 0


def test_entry_point_group_version_is_enforced() -> None:
    manifest = json.dumps(
        _extended_metadata(name="vendor.readonly_validator", entry_point_group_version=2).to_dict()
    )
    entry_point = _FakeEntryPoint("vendor_validator", manifest)
    with pytest.raises(PluginMetadataError, match="entry_point_group_version"):
        discover_science_plugins(entry_points=[entry_point])
    assert entry_point.load_calls == 0


def test_entry_point_license_failure_is_typed() -> None:
    payload = _extended_metadata(name="vendor.readonly_validator").to_dict()
    payload["license"] = "Unknown-License"
    entry_point = _FakeEntryPoint("vendor_validator", json.dumps(payload))
    with pytest.raises(PluginLicenseError):
        discover_science_plugins(entry_points=[entry_point])
    assert entry_point.load_calls == 0
