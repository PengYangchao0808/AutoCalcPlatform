"""A-23 — plugin conflicts, upgrades and historical read-only access (H5).

The suite pins the plugin-ecosystem gate, not a production plugin:

* every conflict class is a distinct typed rejection raised before a loader;
* discovery never calls a loader, for explicit candidates and installed
  entry points alike;
* an untrusted/unverified plugin is never auto-enabled, even when its
  manifest declares ``default_enabled``;
* a constructed v1/v2 pair proves that v2 is used explicitly for new work
  while a v1 artifact stays readable without rerunning;
* ``handoff-H5.json`` records every compatibility row, the frozen
  metadata→capability→recipe→provenance chain and the upgrade/rollback
  matrix, and never claims a process-level sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

import cccp.plugin.errors as plugin_errors
from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.calculation.results import TaskResult
from cccp.plugin import (
    ApiCompatibility,
    CapabilityKind,
    ErrorSemantics,
    PluginCandidate,
    PluginCapability,
    PluginCapabilityConflictError,
    PluginDependencyUnavailableError,
    PluginDisabledError,
    PluginError,
    PluginFixtureEvidence,
    PluginFixtureIntegrityError,
    PluginHost,
    PluginImplementationConflictError,
    PluginLicenseError,
    PluginMetadata,
    PluginMetadataError,
    PluginOutputSpec,
    PluginRecipeRef,
    PluginTrustLevel,
    PluginValidatorRef,
    PluginVersionMismatchError,
    ResourceRequirements,
    discover_plugins,
    discover_science_plugins,
    failure_report,
    invoke_capability,
    lock_plugin,
    scan_science_plugin_entry_points,
    verify_fixture_evidence,
)
from tests._acp2_external_plugin_harness import (
    build_fixture_wheel,
    create_isolated_venv,
    install_wheel,
    run_python_script,
    uninstall_distribution,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
UPGRADE_ROOT = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "plugin_upgrade"
V1_ROOT = UPGRADE_ROOT / "v1"
V2_ROOT = UPGRADE_ROOT / "v2"
V1_INPUT = V1_ROOT / "fixtures" / "native_singlepoint.json"
V1_EXPECTED = V1_ROOT / "fixtures" / "expected_output.json"
V2_INPUT = V2_ROOT / "fixtures" / "native_singlepoint.json"
V2_EXPECTED = V2_ROOT / "fixtures" / "expected_output.json"
CHECKED_IN_ARTIFACT = UPGRADE_ROOT / "artifacts" / "v1-task-result.json"
UPGRADE_PROBE = Path(__file__).with_name("_acp2_upgrade_plugin_probe.py")
SCIENCE_CONTRACT_ROOT = (
    REPO_ROOT / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
)
H5_PATH = SCIENCE_CONTRACT_ROOT / "handoff-H5.json"
H4_PATH = SCIENCE_CONTRACT_ROOT / "handoff-H4.json"
UPGRADE_DISTRIBUTION = "acp2-upgrade-science-validator"
UPGRADE_CAPABILITY = "acp2_upgrade_native_singlepoint"
UPGRADE_PLUGIN = "acp2_upgrade.native_singlepoint"
FIXTURE_DIGEST = "sha256:" + "a" * 64

CONFLICT_ROWS = {
    "H5-01-duplicate-capability-name": "PluginCapabilityConflictError",
    "H5-02-duplicate-implementation-id": "PluginImplementationConflictError",
    "H5-03-incompatible-api-range": "PluginVersionMismatchError",
    "H5-04-missing-dependency": "PluginDependencyUnavailableError",
    "H5-05-unknown-or-empty-license": "PluginLicenseError",
    "H5-06-corrupt-fixture-data": "PluginFixtureIntegrityError",
    "H5-07-untrusted-unverified": "PluginDisabledError",
}
REQUIRED_ROW_IDS = (*CONFLICT_ROWS, "H5-08-v1-artifact-after-v2-upgrade")
UPGRADE_STEP_IDS = (
    "H5-UP-1-keep-v1-artifact",
    "H5-UP-2-install-v2",
    "H5-UP-3-new-task-uses-v2",
    "H5-UP-4-v1-artifact-readable-after-v2",
    "H5-UP-5-uninstall-v2",
    "H5-UP-6-v1-artifact-readable-after-rollback",
)


def _metadata(
    name: str = "vendor.readonly_validator",
    *,
    capability_name: str = "readonly_validator",
    implementation_id: str = "vendor.science.validator",
    dependencies: tuple[str, ...] = (),
    license: str = "MIT",
    trust_level: PluginTrustLevel | str = PluginTrustLevel.UNTRUSTED,
    fixture_evidence: tuple[PluginFixtureEvidence, ...] = (),
    default_enabled: bool = False,
    api: ApiCompatibility | None = None,
    science_version_range: str = ">=1,<2",
) -> PluginMetadata:
    return PluginMetadata(
        name=name,
        distribution="vendor-science-dist",
        version="2.0.0",
        api=api or ApiCompatibility(),
        capabilities=(
            PluginCapability(
                name=capability_name,
                schema="acp2.science.validator",
                kind=CapabilityKind.PURE_ANALYSIS,
                implementation_id=implementation_id,
                recipe_refs=(PluginRecipeRef(recipe_id="vendor.recipe.v1"),),
                validator_refs=(
                    PluginValidatorRef(
                        validator_id="check.vendor_units", validator_version="1.0.0"
                    ),
                ),
            ),
        ),
        resources=ResourceRequirements(),
        license=license,
        dependencies=dependencies,
        error_semantics=ErrorSemantics(),
        default_enabled=default_enabled,
        trust_level=trust_level,
        fixture_evidence=fixture_evidence,
        science_version_range=science_version_range,
    )


class _AnalysisHandler:
    def __init__(self, capability: str = "readonly_validator") -> None:
        self._capability = capability

    def capability_names(self) -> tuple[str, ...]:
        return (self._capability,)

    def output_spec_for(self, capability: str) -> PluginOutputSpec:
        return PluginOutputSpec(units=(("energy", "hartree"),), allows_partial=False)

    def invoke(self, request: TaskRequest, *, context: object | None = None) -> TaskResult:
        return TaskResult(
            task=request.task,
            metadata={"capability": self._capability, "units": {"energy": "hartree"}},
        )


def _request() -> TaskRequest:
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=Path("/nonexistent/a23-input.xyz")),
    )


def _host(metadata: PluginMetadata) -> tuple[PluginHost, list[str]]:
    calls: list[str] = []

    def loader() -> object:
        calls.append("loaded")
        return _AnalysisHandler(metadata.capabilities[0].name)

    return PluginHost(candidates=(PluginCandidate(metadata=metadata, loader=loader),)), calls


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


class _FakeDistribution:
    def __init__(self, manifest: str | None) -> None:
        self._manifest = manifest

    def read_text(self, filename: str) -> str | None:
        return self._manifest


class _FakeEntryPoint:
    group = "cccp.science_plugins"

    def __init__(self, name: str, manifest: str | None) -> None:
        self.name = name
        self.value = "vendor_plugin:PLUGIN"
        self.dist = _FakeDistribution(manifest)
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        return object()


# ── conflict taxonomy: one distinct typed error per row ──────────────────


def test_duplicate_capability_name_is_a_typed_conflict() -> None:
    first = _metadata("vendor_one", capability_name="shared_capability")
    second = _metadata("vendor_two", capability_name="shared_capability")

    with pytest.raises(PluginCapabilityConflictError) as excinfo:
        discover_plugins((PluginCandidate(metadata=first), PluginCandidate(metadata=second)))

    assert type(excinfo.value) is PluginCapabilityConflictError
    assert excinfo.value.name == "vendor_two"
    assert excinfo.value.capability == "shared_capability"
    assert "vendor_one" in excinfo.value.reason


def test_duplicate_implementation_id_is_a_distinct_typed_conflict() -> None:
    first = _metadata(
        "vendor_one",
        capability_name="capability_one",
        implementation_id="vendor.science.shared_impl",
    )
    second = _metadata(
        "vendor_two",
        capability_name="capability_two",
        implementation_id="vendor.science.shared_impl",
    )

    with pytest.raises(PluginImplementationConflictError) as excinfo:
        discover_plugins((PluginCandidate(metadata=first), PluginCandidate(metadata=second)))

    assert type(excinfo.value) is PluginImplementationConflictError
    assert excinfo.value.name == "vendor_two"
    assert excinfo.value.implementation_id == "vendor.science.shared_impl"
    assert "vendor_one" in excinfo.value.reason


def test_duplicate_implementation_id_within_one_plugin_is_rejected() -> None:
    with pytest.raises(PluginImplementationConflictError):
        PluginMetadata(
            name="vendor_internal_duplicate",
            distribution="vendor-science-dist",
            version="1.0.0",
            capabilities=(
                PluginCapability(
                    name="capability_one",
                    schema="acp2.science.validator",
                    implementation_id="vendor.science.shared_impl",
                ),
                PluginCapability(
                    name="capability_two",
                    schema="acp2.science.validator",
                    implementation_id="vendor.science.shared_impl",
                ),
            ),
            license="MIT",
        )


def test_incompatible_api_and_science_ranges_are_typed() -> None:
    with pytest.raises(PluginVersionMismatchError) as api_error:
        discover_plugins(
            (PluginCandidate(metadata=_metadata(api=ApiCompatibility(compatible_range=">=2,<3"))),)
        )
    assert type(api_error.value) is PluginVersionMismatchError
    assert api_error.value.declared == ">=2,<3"

    with pytest.raises(PluginVersionMismatchError) as science_error:
        discover_plugins((PluginCandidate(metadata=_metadata(science_version_range=">=2,<3")),))
    assert type(science_error.value) is PluginVersionMismatchError
    assert science_error.value.declared == ">=2,<3"


def test_missing_dependency_is_typed_before_the_loader() -> None:
    missing = f"cccp_a23_absent_dependency_{uuid.uuid4().hex}"
    host, calls = _host(_metadata(dependencies=(missing,)))
    host.enable("vendor.readonly_validator")

    with pytest.raises(PluginDependencyUnavailableError) as excinfo:
        invoke_capability(
            host,
            "vendor.readonly_validator",
            capability="readonly_validator",
            request=_request(),
        )

    assert type(excinfo.value) is PluginDependencyUnavailableError
    assert excinfo.value.missing == (missing,)
    assert calls == []


def test_unknown_and_empty_licenses_are_typed() -> None:
    with pytest.raises(PluginLicenseError) as unknown:
        _metadata(license="Proprietary-Unknown-1.0")
    assert type(unknown.value) is PluginLicenseError
    assert unknown.value.licence == "Proprietary-Unknown-1.0"

    with pytest.raises(PluginLicenseError) as empty:
        _metadata(license="")
    assert type(empty.value) is PluginLicenseError


def test_corrupt_fixture_digest_is_typed() -> None:
    evidence = PluginFixtureEvidence(fixture_id="vendor.fixture", digest=FIXTURE_DIGEST)
    metadata = _metadata(fixture_evidence=(evidence,))

    verify_fixture_evidence(metadata, fixture_id="vendor.fixture", computed_digest=FIXTURE_DIGEST)
    with pytest.raises(PluginFixtureIntegrityError) as mismatch:
        verify_fixture_evidence(
            metadata,
            fixture_id="vendor.fixture",
            computed_digest="sha256:" + "b" * 64,
        )
    assert type(mismatch.value) is PluginFixtureIntegrityError
    assert mismatch.value.name == "vendor.readonly_validator"
    assert mismatch.value.fixture_id == "vendor.fixture"
    assert "does not match" in mismatch.value.reason

    with pytest.raises(PluginFixtureIntegrityError):
        verify_fixture_evidence(
            metadata, fixture_id="vendor.not_declared", computed_digest=FIXTURE_DIGEST
        )
    with pytest.raises(PluginFixtureIntegrityError):
        verify_fixture_evidence(
            metadata, fixture_id="vendor.fixture", computed_digest="not-a-digest"
        )


def test_corrupt_entry_point_manifest_is_typed_before_import() -> None:
    entry_point = _FakeEntryPoint("vendor_validator", "{not-json")

    with pytest.raises(PluginMetadataError):
        discover_science_plugins(entry_points=[entry_point])

    assert entry_point.load_calls == 0


def test_untrusted_plugin_is_never_auto_enabled_even_with_default_enabled() -> None:
    metadata = _metadata(
        trust_level=PluginTrustLevel.UNTRUSTED,
        fixture_evidence=(
            PluginFixtureEvidence(fixture_id="vendor.fixture", digest=FIXTURE_DIGEST),
        ),
        default_enabled=True,
    )
    host, calls = _host(metadata)

    assert metadata.default_enabled is True
    assert metadata.is_trusted is False
    assert host.enabled_names() == ()
    status = host.status("vendor.readonly_validator")
    assert status is not None
    assert status.enabled.state.value == "absent"
    assert status.verified.state.value == "not_verified"

    with pytest.raises(PluginDisabledError) as excinfo:
        invoke_capability(
            host,
            "vendor.readonly_validator",
            capability="readonly_validator",
            request=_request(),
        )
    assert type(excinfo.value) is PluginDisabledError
    assert calls == []
    report = failure_report(excinfo.value)
    assert report.guidance.silent_substitution_allowed is False
    assert report.guidance.builtin_capability_preserved is True


def test_discovery_never_calls_a_loader_for_any_source() -> None:
    calls: list[str] = []

    def loader() -> object:
        calls.append("called")
        return object()

    explicit = PluginCandidate(metadata=_metadata(default_enabled=True), loader=loader)
    catalog = discover_plugins((explicit,))
    assert calls == []
    assert catalog.names() == ("vendor.readonly_validator",)

    manifest = json.dumps(_metadata("vendor.entry_point", default_enabled=True).to_dict())
    entry_point = _FakeEntryPoint("vendor_validator", manifest)
    scan = scan_science_plugin_entry_points(entry_points=[entry_point])
    discovered = discover_plugins(scan.candidates)
    assert calls == []
    assert entry_point.load_calls == 0
    assert discovered.names() == ("vendor.entry_point",)


# ── constructed v1/v2 upgrade and historical read-only access ────────────


@pytest.fixture(scope="module")
def upgrade_environment(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    override = os.environ.get("ACP2_A23_WORK_ROOT")
    if override:
        root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = tmp_path_factory.mktemp("a23-upgrade")
    wheel_v1, build_v1_argv, build_v1_log = build_fixture_wheel(
        root / "wheel-v1", fixture_root=V1_ROOT
    )
    python, venv_argv = create_isolated_venv(root)
    install_v1_argv, install_v1 = install_wheel(python, wheel_v1)
    artifact = root / "v1-artifact.json"
    capture = run_python_script(python, UPGRADE_PROBE, "capture", V1_INPUT, V1_EXPECTED, artifact)

    wheel_v2, build_v2_argv, build_v2_log = build_fixture_wheel(
        root / "wheel-v2", fixture_root=V2_ROOT
    )
    install_v2_argv, install_v2 = install_wheel(python, wheel_v2)
    upgrade = run_python_script(
        python, UPGRADE_PROBE, "upgrade-check", artifact, V2_INPUT, V2_EXPECTED
    )
    uninstall_argv, uninstall_result = uninstall_distribution(python, UPGRADE_DISTRIBUTION)
    historical = run_python_script(python, UPGRADE_PROBE, "read-historical", artifact)

    payload: dict[str, object] = {
        "root": root,
        "python": python,
        "artifact": artifact,
        "wheel_v1": wheel_v1,
        "wheel_v2": wheel_v2,
        "wheel_v1_sha256": _sha256(wheel_v1),
        "wheel_v2_sha256": _sha256(wheel_v2),
        "build_v1_argv": build_v1_argv,
        "build_v1_log": build_v1_log,
        "build_v2_argv": build_v2_argv,
        "build_v2_log": build_v2_log,
        "venv_argv": venv_argv,
        "install_v1_argv": install_v1_argv,
        "install_v1_log": install_v1.stdout + install_v1.stderr,
        "install_v2_argv": install_v2_argv,
        "install_v2_log": install_v2.stdout + install_v2.stderr,
        "uninstall_argv": uninstall_argv,
        "uninstall_log": uninstall_result.stdout + uninstall_result.stderr,
        "capture": capture,
        "upgrade": upgrade,
        "historical": historical,
    }
    yield payload
    shutil.rmtree(root, ignore_errors=True)


class TestConstructedUpgradePair:
    def test_v1_artifact_is_produced_by_the_v1_distribution(
        self, upgrade_environment: dict[str, object]
    ) -> None:
        capture = upgrade_environment["capture"]
        assert isinstance(capture, dict)
        assert capture["ok"] is True
        assert capture["plugin_version"] == "1.0.0"
        assert capture["capability_schema_version"] == 1
        assert capture["disabled_error"] == "PluginDisabledError"
        assert capture["module_imported_before_enable"] is False
        assert capture["trust_level"] == "untrusted"
        assert capture["default_enabled"] is False
        assert capture["status_before"]["verified"]["state"] == "not_verified"

        artifact = upgrade_environment["artifact"]
        assert isinstance(artifact, Path)
        runtime_payload = _load_json(artifact)
        checked_in = _load_json(CHECKED_IN_ARTIFACT)
        assert runtime_payload == checked_in
        assert capture["artifact_sha256"] == _sha256(CHECKED_IN_ARTIFACT)

    def test_v1_artifact_survives_v2_upgrade_without_rerun(
        self, upgrade_environment: dict[str, object]
    ) -> None:
        upgrade = upgrade_environment["upgrade"]
        assert isinstance(upgrade, dict)
        assert upgrade["ok"] is True
        assert upgrade["v1_artifact_readable"] is True
        assert upgrade["v1_result_roundtrip"] is True
        assert upgrade["v1_artifact_unchanged"] is True
        assert upgrade["v1_artifact_sha256_before"] == upgrade["v1_artifact_sha256_after"]
        assert upgrade["v1_plugin_version"] == "1.0.0"
        assert upgrade["module_imported_during_history_read"] is False
        assert upgrade["module_imported_before_enable"] is False

    def test_new_tasks_use_v2_explicitly_and_never_auto_enable(
        self, upgrade_environment: dict[str, object]
    ) -> None:
        upgrade = upgrade_environment["upgrade"]
        assert isinstance(upgrade, dict)
        assert upgrade["v2_plugin_version"] == "2.0.0"
        assert upgrade["v2_capability_schema_version"] == 2
        assert upgrade["v2_plugin_lock_digest"] != upgrade["v1_plugin_lock_digest"]
        assert upgrade["v2_default_enabled"] is True
        assert upgrade["status_before_enable"]["enabled"]["state"] == "absent"
        assert upgrade["disabled_error"] == "PluginDisabledError"
        observed = upgrade["observed_v2"]
        assert isinstance(observed, dict)
        assert observed["plugin_version"] == "2.0.0"
        assert observed["capability_schema_version"] == 2
        assert observed["input_sha256"] == _load_json(V2_EXPECTED)["input_sha256"]

        wheel_v1 = upgrade_environment["wheel_v1"]
        wheel_v2 = upgrade_environment["wheel_v2"]
        assert isinstance(wheel_v1, Path) and isinstance(wheel_v2, Path)
        assert "1.0.0" in wheel_v1.name
        assert "2.0.0" in wheel_v2.name
        assert upgrade_environment["wheel_v1_sha256"] != upgrade_environment["wheel_v2_sha256"]
        assert "--no-deps" in upgrade_environment["install_v2_argv"]
        assert "--no-index" in upgrade_environment["install_v2_argv"]

    def test_v1_artifact_reads_after_v2_uninstall(
        self, upgrade_environment: dict[str, object]
    ) -> None:
        historical = upgrade_environment["historical"]
        assert isinstance(historical, dict)
        assert historical["ok"] is True
        assert historical["v1_artifact_readable"] is True
        assert historical["v1_result_roundtrip"] is True
        assert historical["plugin_version"] == "1.0.0"
        assert historical["capability_schema_version"] == 1
        assert historical["task"] == "singlepoint"
        assert historical["discovered_names"] == []
        assert historical["module_imported"] is False
        assert historical["artifact_sha256"] == _sha256(CHECKED_IN_ARTIFACT)

    def test_checked_in_v1_artifact_is_labelled_constructed_and_readable(self) -> None:
        payload = _load_json(CHECKED_IN_ARTIFACT)
        assert payload["constructed"] is True
        assert "constructed" in str(payload["label"]).lower()
        assert payload["schema"] == "acp2.plugin-upgrade.historical-artifact"
        result = TaskResult.from_dict(payload["result"])
        assert result.task is TaskKind.SINGLEPOINT
        assert result.to_dict() == payload["result"]
        assert result.metadata["capability_schema_version"] == 1
        assert result.provenance is not None
        assert result.provenance.version == "1.0.0"


# ── handoff H5: compatibility rows, chain and rollback matrix ────────────


def _h5() -> dict[str, object]:
    return _load_json(H5_PATH)


def test_handoff_h5_has_every_compatibility_row_with_result_and_evidence() -> None:
    handoff = _h5()
    assert handoff["handoff_id"] == "H5"
    rows = handoff["compatibility_rows"]
    assert isinstance(rows, list)
    rows_by_id = {row["row_id"]: row for row in rows}
    assert set(rows_by_id) == set(REQUIRED_ROW_IDS)

    for row in rows:
        assert row["result"] == "PASS"
        assert row["constructed"] is True
        assert isinstance(row["requirement"], str) and row["requirement"].strip()
        evidence = row["evidence"]
        assert isinstance(evidence["summary"], str) and evidence["summary"].strip()
        assert evidence["commands"]
        assert evidence["tests"]
        for artifact in evidence["artifacts"]:
            path = REPO_ROOT / artifact["path"]
            assert path.is_file(), artifact["path"]
            assert _sha256(path) == artifact["sha256"], artifact["path"]


def test_handoff_h5_conflict_rows_use_distinct_typed_errors() -> None:
    rows_by_id = {row["row_id"]: row for row in _h5()["compatibility_rows"]}
    observed_types: set[str] = set()

    for row_id, expected_type in CONFLICT_ROWS.items():
        row = rows_by_id[row_id]
        assert row["error_type"] == expected_type
        assert row["distinct_typed_error"] is True
        error_class = getattr(plugin_errors, expected_type)
        assert issubclass(error_class, PluginError)
        assert error_class.__name__ == expected_type
        observed_types.add(expected_type)

    assert len(observed_types) == len(CONFLICT_ROWS)
    assert rows_by_id["H5-08-v1-artifact-after-v2-upgrade"]["error_type"] is None


def test_handoff_h5_freezes_the_metadata_capability_recipe_provenance_chain() -> None:
    chain = _h5()["chain_frozen"]
    assert chain["order"] == ["metadata", "capability", "recipe", "validator", "provenance"]

    v2_manifest = _load_json(V2_ROOT / "cccp_science_plugin.json")
    v2_metadata = PluginMetadata.from_dict(v2_manifest)
    v2_lock = lock_plugin(v2_metadata)
    capability = v2_manifest["capabilities"][0]

    metadata_link = chain["links"]["metadata"]["instance"]
    assert metadata_link["name"] == v2_manifest["name"]
    assert metadata_link["distribution"] == v2_manifest["distribution"]
    assert metadata_link["version"] == "2.0.0"
    assert metadata_link["trust_level"] == "untrusted"
    assert metadata_link["default_enabled"] is True

    capability_link = chain["links"]["capability"]["instance"]
    assert capability_link["name"] == capability["name"]
    assert capability_link["implementation_id"] == capability["implementation_id"]
    assert capability_link["schema"] == capability["schema"]
    assert capability_link["schema_version"] == 2
    assert capability_link["kind"] == "pure_analysis"

    recipe_link = chain["links"]["recipe"]["instance"]
    assert recipe_link["recipe_id"] == capability["recipe_refs"][0]["recipe_id"]
    assert recipe_link["schema_version"] == capability["recipe_refs"][0]["schema_version"]

    validator_link = chain["links"]["validator"]["instance"]
    assert validator_link["validator_id"] == capability["validator_refs"][0]["validator_id"]
    assert (
        validator_link["validator_version"] == capability["validator_refs"][0]["validator_version"]
    )

    provenance_link = chain["links"]["provenance"]["instance"]
    assert provenance_link["plugin_version"] == "2.0.0"
    assert provenance_link["plugin_digest"] == v2_lock.digest
    assert len(v2_lock.digest) == 64
    assert provenance_link["capability_schema_version"] == 2
    assert provenance_link["capability"] == capability["name"]
    assert chain["frozen"] is True


def test_handoff_h5_upgrade_matrix_covers_upgrade_and_rollback() -> None:
    matrix = _h5()["upgrade_matrix"]
    assert matrix["constructed"] is True
    assert tuple(matrix["step_ids"]) == UPGRADE_STEP_IDS
    steps = {step["step_id"]: step for step in matrix["steps"]}
    assert set(steps) == set(UPGRADE_STEP_IDS)

    for step in steps.values():
        assert step["result"] == "PASS"
        assert step["evidence"]["tests"]
        assert step["evidence"]["summary"]

    assert steps["H5-UP-1-keep-v1-artifact"]["v1_artifact_sha256"] == _sha256(CHECKED_IN_ARTIFACT)
    assert steps["H5-UP-2-install-v2"]["v2_plugin_version"] == "2.0.0"
    assert steps["H5-UP-3-new-task-uses-v2"]["v2_capability_schema_version"] == 2
    assert steps["H5-UP-4-v1-artifact-readable-after-v2"]["v1_artifact_readable"] is True
    assert steps["H5-UP-6-v1-artifact-readable-after-rollback"]["discovered_names"] == []


def test_handoff_h5_proves_discovery_never_loads_and_claims_no_process_sandbox() -> None:
    handoff = _h5()
    discovery = handoff["discovery_loader_proof"]
    assert discovery["discovery_calls_loader"] is False
    assert discovery["auto_enables_unverified_plugin"] is False
    assert discovery["silently_substitutes_plugin_result"] is False
    assert discovery["tests"]

    sandbox = handoff["sandbox_boundary"]
    assert sandbox["process_level_sandbox_claimed"] is False
    assert sandbox["sandbox_owner"] == "platform owner (B)"
    assert sandbox["in_process_host"] is True
    assert sandbox["platform_owned_limits"]
    assert handoff["no_process_sandbox_claim"] is True


def test_handoff_h5_closure_matches_the_rows_and_h4_basis() -> None:
    handoff = _h5()
    closure = handoff["h5_closure"]
    rows = {row["row_id"]: row for row in handoff["compatibility_rows"]}
    open_rows = [row_id for row_id in REQUIRED_ROW_IDS if rows[row_id]["result"] != "PASS"]

    assert closure["required_row_ids"] == list(REQUIRED_ROW_IDS)
    assert closure["open_required_row_ids"] == open_rows
    assert closure["all_required_pass"] is (not open_rows)
    assert closure["closed"] is (not open_rows)

    basis = handoff["contract_version_basis"]
    assert basis["handoff_h4_sha256"] == _sha256(H4_PATH)
