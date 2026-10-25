"""A-21b — science plugin registry boundaries and catalog projections.

The suite pins the ACP2 boundary, not a plugin implementation:

* the ``cccp.science_plugins`` group is scanned metadata-only and an
  installed fixture is discovered without importing its module;
* a host loads an entry point only after explicit enablement;
* described / installed / enabled / verified stay four separate projections;
* a plugin cannot override a built-in task/capability;
* a validator or recipe reference that a capability did not declare is
  rejected before the plugin is loaded;
* a compute capability is never invoked by the plugin host, and a
  pure-analysis capability cannot declare a subprocess requirement.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.metadata
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

from cccp.calculation.capabilities.contracts import CapabilityKind, CapabilityState
from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.calculation.results import TaskResult
from cccp.plugin import (
    SCIENCE_PLUGIN_ENTRY_POINT_GROUP,
    SCIENCE_PLUGIN_MANIFEST_FILENAME,
    ApiCompatibility,
    EntryPointPluginLoader,
    ErrorSemantics,
    PluginCandidate,
    PluginCapability,
    PluginCapabilityConflictError,
    PluginContractViolationError,
    PluginDisabledError,
    PluginFixtureEvidence,
    PluginHost,
    PluginLicenseError,
    PluginLicensePolicy,
    PluginMetadata,
    PluginMetadataError,
    PluginNameConflictError,
    PluginOutputSpec,
    PluginRecipeRef,
    PluginTrustLevel,
    PluginValidatorRef,
    ResourceRequirements,
    discover_plugins,
    discover_science_plugins,
    invoke_capability,
    scan_science_plugin_entry_points,
    validate_declared_recipe,
    validate_declared_validators,
)
from tests._acp2_external_plugin_harness import (
    CAPABILITY_NAME as EXTERNAL_CAPABILITY_NAME,
)
from tests._acp2_external_plugin_harness import (
    EXPECTED_OUTPUT as EXTERNAL_EXPECTED_OUTPUT,
)
from tests._acp2_external_plugin_harness import (
    FIXED_INPUT as EXTERNAL_FIXED_INPUT,
)
from tests._acp2_external_plugin_harness import (
    PLUGIN_NAME as EXTERNAL_PLUGIN_NAME,
)
from tests._acp2_external_plugin_harness import (
    build_fixture_wheel,
    create_isolated_venv,
    install_wheel,
    run_probe,
)

CAPABILITY = "readonly_validator"
PLUGIN_NAME = "vendor.readonly_validator"
VALIDATOR = PluginValidatorRef(validator_id="check.vendor_units", validator_version="1.0.0")
RECIPE = PluginRecipeRef(recipe_id="vendor.recipe.v1")
FIXTURE_DIGEST = "sha256:" + "d" * 64


def _metadata(
    name: str = PLUGIN_NAME,
    *,
    capability_name: str = CAPABILITY,
    kind: CapabilityKind | str = CapabilityKind.PURE_ANALYSIS,
    delegates_to_task_kind: str | None = None,
    validator_refs: tuple[PluginValidatorRef, ...] = (VALIDATOR,),
    recipe_refs: tuple[PluginRecipeRef, ...] = (RECIPE,),
    trust_level: PluginTrustLevel | str = PluginTrustLevel.UNTRUSTED,
    fixture_evidence: tuple[PluginFixtureEvidence, ...] = (),
    resources: ResourceRequirements | None = None,
    license: str = "MIT",
    license_policy: PluginLicensePolicy | None = None,
) -> PluginMetadata:
    values: dict[str, object] = {
        "name": name,
        "distribution": "vendor-science-dist",
        "version": "2.0.0",
        "api": ApiCompatibility(),
        "capabilities": (
            PluginCapability(
                name=capability_name,
                schema="acp2.science.validator",
                kind=kind,
                implementation_id="vendor.science.validator",
                delegates_to_task_kind=delegates_to_task_kind,
                validator_refs=validator_refs,
                recipe_refs=recipe_refs,
            ),
        ),
        "resources": resources or ResourceRequirements(),
        "license": license,
        "error_semantics": ErrorSemantics(),
        "trust_level": trust_level,
        "fixture_evidence": fixture_evidence,
    }
    if license_policy is not None:
        values["license_policy"] = license_policy
    return PluginMetadata(**values)  # type: ignore[arg-type]


def _trusted_metadata(name: str = PLUGIN_NAME) -> PluginMetadata:
    return _metadata(
        name,
        trust_level=PluginTrustLevel.TRUSTED,
        fixture_evidence=(
            PluginFixtureEvidence(fixture_id="vendor.validator.fixed-input", digest=FIXTURE_DIGEST),
        ),
    )


class _AnalysisHandler:
    """Minimal pure-analysis handler used to prove invocation boundaries."""

    def __init__(self) -> None:
        self.calls: list[TaskKind] = []

    def capability_names(self) -> tuple[str, ...]:
        return (CAPABILITY,)

    def output_spec_for(self, capability: str) -> PluginOutputSpec:
        return PluginOutputSpec(units=(("energy", "hartree"),), allows_partial=False)

    def invoke(self, request: TaskRequest, *, context: object | None = None) -> TaskResult:
        self.calls.append(request.task)
        return TaskResult(
            task=request.task,
            metadata={
                "capability": CAPABILITY,
                "units": {"energy": "hartree"},
            },
        )


def _request() -> TaskRequest:
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=Path("/nonexistent/a21b-input.xyz")),
    )


def _host(metadata: PluginMetadata, handler: object | None = None) -> tuple[PluginHost, list[str]]:
    calls: list[str] = []

    def loader() -> object:
        calls.append("loaded")
        return _AnalysisHandler() if handler is None else handler

    host = PluginHost(candidates=(PluginCandidate(metadata=metadata, loader=loader),))
    return host, calls


# ── catalog projections: described / installed / enabled / verified ─────


def test_projection_separates_described_installed_enabled_and_verified() -> None:
    catalog = discover_plugins(
        (
            PluginCandidate(
                metadata=_trusted_metadata(),
                loader=lambda: object(),
                origin="entry_point",
                entry_point=f"{SCIENCE_PLUGIN_ENTRY_POINT_GROUP}:vendor_validator",
            ),
        )
    )

    disabled = catalog.status(PLUGIN_NAME, enabled=False)
    enabled = catalog.status(PLUGIN_NAME, enabled=True)

    assert disabled is not None and enabled is not None
    assert disabled.described.state is CapabilityState.PRESENT
    assert disabled.installed.state is CapabilityState.PRESENT
    assert disabled.enabled.state is CapabilityState.ABSENT
    assert disabled.verified.state is CapabilityState.VERIFIED
    assert disabled.verified.evidence_ref == FIXTURE_DIGEST
    # Enabling changes exactly one projection.
    assert enabled.enabled.state is CapabilityState.PRESENT
    assert enabled.described == disabled.described
    assert enabled.installed == disabled.installed
    assert enabled.verified == disabled.verified
    assert enabled.origin == "entry_point"
    assert enabled.entry_point == f"{SCIENCE_PLUGIN_ENTRY_POINT_GROUP}:vendor_validator"


def test_enablement_never_promotes_verified() -> None:
    catalog = discover_plugins((PluginCandidate(metadata=_metadata()),))
    before = catalog.status(PLUGIN_NAME, enabled=False)
    after = catalog.status(PLUGIN_NAME, enabled=True)

    assert before is not None and after is not None
    assert before.verified.state is CapabilityState.NOT_VERIFIED
    assert after.verified.state is CapabilityState.NOT_VERIFIED
    assert after.enabled.state is CapabilityState.PRESENT
    payload = catalog.to_dict(enabled_names=(PLUGIN_NAME,))
    status = payload["statuses"][0]
    assert status["enabled"]["state"] == "present"
    assert status["verified"]["state"] == "not_verified"


def test_host_catalog_status_tracks_explicit_enablement_only() -> None:
    host, calls = _host(_metadata())
    before = host.catalog_status()
    assert before[0].enabled.state is CapabilityState.ABSENT
    assert before[0].verified.state is CapabilityState.NOT_VERIFIED
    assert calls == []

    host.enable(PLUGIN_NAME)
    after = host.catalog_status()
    assert after[0].enabled.state is CapabilityState.PRESENT
    assert after[0].verified == before[0].verified
    assert calls == []


def test_metadata_only_catalog_does_not_call_a_loader() -> None:
    calls: list[str] = []

    def loader() -> object:
        calls.append("called")
        return object()

    catalog = discover_plugins((PluginCandidate(metadata=_trusted_metadata(), loader=loader),))

    assert calls == []
    assert catalog.status(PLUGIN_NAME, enabled=False).installed.state is CapabilityState.PRESENT


# ── entry-point discovery stays metadata-only ───────────────────────────


class _FakeEntryPoint:
    group = SCIENCE_PLUGIN_ENTRY_POINT_GROUP

    def __init__(self, name: str, distribution: object, module_name: str) -> None:
        self.name = name
        self.value = f"{module_name}:PLUGIN"
        self.dist = distribution
        self.module_name = module_name
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        return importlib.import_module(self.module_name)


def _installed_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata: PluginMetadata,
) -> tuple[_FakeEntryPoint, str]:
    site = tmp_path / "site"
    dist_info = site / "vendor_science_dist-2.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: vendor-science-dist\nVersion: 2.0.0\n",
        encoding="utf-8",
    )
    (dist_info / SCIENCE_PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(metadata.to_dict()), encoding="utf-8"
    )
    module_name = f"a21b_fixture_{uuid.uuid4().hex}"
    (site / f"{module_name}.py").write_text("PLUGIN = object()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(site))
    distribution = importlib.metadata.PathDistribution(dist_info)
    entry_point = _FakeEntryPoint("vendor_validator", distribution, module_name)
    return entry_point, module_name


def test_installed_fixture_is_discovered_without_importing_its_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry_point, module_name = _installed_fixture(tmp_path, monkeypatch, _trusted_metadata())

    scan = scan_science_plugin_entry_points(entry_points=[entry_point])

    assert scan.entry_points_scanned == 1
    assert len(scan.candidates) == 1
    assert isinstance(scan.candidates[0].loader, EntryPointPluginLoader)
    assert entry_point.load_calls == 0
    assert module_name not in sys.modules

    catalog = discover_science_plugins(entry_points=[entry_point])
    assert catalog.names() == (PLUGIN_NAME,)
    assert catalog.discovery_mode == "entry_points"
    assert catalog.origin_for(PLUGIN_NAME).entry_point.endswith(":vendor_validator")
    assert entry_point.load_calls == 0
    assert module_name not in sys.modules


def test_entry_points_are_scanned_metadata_only_through_importlib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry_point, module_name = _installed_fixture(tmp_path, monkeypatch, _trusted_metadata())
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: [entry_point])

    catalog = discover_science_plugins()

    assert catalog.names() == (PLUGIN_NAME,)
    assert entry_point.load_calls == 0
    assert module_name not in sys.modules


def test_host_loads_an_installed_entry_point_only_after_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry_point, module_name = _installed_fixture(tmp_path, monkeypatch, _trusted_metadata())
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: [entry_point])

    host = PluginHost.from_science_entry_points()
    assert host.catalog.names() == (PLUGIN_NAME,)
    assert host.enabled_names() == ()
    assert entry_point.load_calls == 0
    assert module_name not in sys.modules

    with pytest.raises(PluginDisabledError):
        host.load(PLUGIN_NAME)
    assert entry_point.load_calls == 0

    host.enable(PLUGIN_NAME)
    assert entry_point.load_calls == 0
    loaded = host.load(PLUGIN_NAME)
    assert loaded is not None
    assert entry_point.load_calls == 1
    assert module_name in sys.modules


# ── built-in override and execution boundaries ──────────────────────────


def test_plugin_cannot_override_a_builtin_task_capability() -> None:
    metadata = _metadata(capability_name=TaskKind.FREQUENCY.value)
    with pytest.raises(PluginCapabilityConflictError) as excinfo:
        discover_plugins((PluginCandidate(metadata=metadata),))
    assert excinfo.value.capability == TaskKind.FREQUENCY.value
    assert "built-in" in excinfo.value.reason


def test_plugin_cannot_take_a_builtin_task_name() -> None:
    with pytest.raises(PluginNameConflictError):
        discover_plugins((PluginCandidate(metadata=_metadata(name=TaskKind.OPTIMIZE.value)),))


def test_compute_capability_is_never_invoked_by_the_plugin_host() -> None:
    metadata = _metadata(
        kind=CapabilityKind.COMPUTE,
        delegates_to_task_kind=TaskKind.FREQUENCY.value,
    )
    host, calls = _host(metadata)
    host.enable(PLUGIN_NAME)

    with pytest.raises(PluginCapabilityConflictError) as excinfo:
        invoke_capability(
            host,
            PLUGIN_NAME,
            capability=CAPABILITY,
            request=_request(),
        )
    assert "built-in task layer" in excinfo.value.reason
    assert calls == []


def test_pure_analysis_plugin_cannot_declare_a_subprocess() -> None:
    with pytest.raises(PluginMetadataError, match="requires_subprocess"):
        _metadata(resources=ResourceRequirements(requires_subprocess=True))


def test_plugin_framework_does_not_import_subprocess() -> None:
    plugin_dir = Path(__file__).resolve().parent.parent / "src" / "cccp" / "plugin"
    offenders: list[str] = []
    for path in plugin_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}:{alias.name}"
                    for alias in node.names
                    if alias.name == "subprocess"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                offenders.append(f"{path.name}:from subprocess")
    assert offenders == []


# ── validator / recipe references are declarations, not authority ───────


def test_undeclared_validator_reference_is_rejected_before_load() -> None:
    host, calls = _host(_metadata())
    host.enable(PLUGIN_NAME)

    with pytest.raises(PluginContractViolationError) as excinfo:
        invoke_capability(
            host,
            PLUGIN_NAME,
            capability=CAPABILITY,
            request=_request(),
            validator_ids=("check.not_declared",),
        )
    assert "not declared" in excinfo.value.detail
    assert calls == []
    assert host.catalog_status()[0].enabled.state is CapabilityState.PRESENT


def test_declared_validator_reference_is_accepted() -> None:
    host, calls = _host(_metadata())
    host.enable(PLUGIN_NAME)

    result = invoke_capability(
        host,
        PLUGIN_NAME,
        capability=CAPABILITY,
        request=_request(),
        validator_ids=(VALIDATOR.validator_id,),
    )
    assert result.complete is True
    assert calls == ["loaded"]


def test_undeclared_recipe_reference_is_rejected_before_load() -> None:
    host, calls = _host(_metadata())
    host.enable(PLUGIN_NAME)

    with pytest.raises(PluginContractViolationError, match="recipe reference"):
        invoke_capability(
            host,
            PLUGIN_NAME,
            capability=CAPABILITY,
            request=_request(),
            recipe_id="vendor.recipe.unknown",
        )
    assert calls == []


def test_declared_recipe_reference_is_accepted() -> None:
    host, calls = _host(_metadata())
    host.enable(PLUGIN_NAME)

    result = invoke_capability(
        host,
        PLUGIN_NAME,
        capability=CAPABILITY,
        request=_request(),
        recipe_id=RECIPE.recipe_id,
    )
    assert result.complete is True
    assert calls == ["loaded"]


def test_standalone_reference_validators_are_typed() -> None:
    metadata = _metadata()
    validate_declared_validators(metadata, CAPABILITY, (VALIDATOR.validator_id,))
    validate_declared_recipe(metadata, CAPABILITY, RECIPE.recipe_id)
    with pytest.raises(PluginContractViolationError):
        validate_declared_validators(metadata, CAPABILITY, ("check.unknown",))
    with pytest.raises(PluginContractViolationError):
        validate_declared_recipe(metadata, CAPABILITY, "vendor.recipe.unknown")


def test_license_policy_is_applied_to_entry_point_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry_point, _module_name = _installed_fixture(tmp_path, monkeypatch, _metadata())
    with pytest.raises(PluginLicenseError):
        discover_science_plugins(
            entry_points=[entry_point],
            license_policy=PluginLicensePolicy.default().narrowed(excluded={"MIT"}),
        )
    assert entry_point.load_calls == 0


# ── external distribution: isolated install, enable and invocation (A-22b) ──


@pytest.fixture(scope="module")
def external_distribution(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Build and install the A-22 fixture in a throw-away virtual environment.

    The shared integration environment is never modified: the wheel is built
    offline and installed with ``pip install --no-deps --no-index`` into a
    fresh venv created for this test module.  ``ACP2_A22_WORK_ROOT`` can pin
    the working directory for evidence runs.
    """
    override = os.environ.get("ACP2_A22_WORK_ROOT")
    if override:
        root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = tmp_path_factory.mktemp("a22b-external")
    wheel, build_argv, build_log = build_fixture_wheel(root / "wheel")
    python, venv_argv = create_isolated_venv(root)
    install_argv, install_result = install_wheel(python, wheel)
    payload: dict[str, object] = {
        "root": root,
        "wheel": wheel,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "python": python,
        "build_argv": build_argv,
        "build_log": build_log,
        "venv_argv": venv_argv,
        "install_argv": install_argv,
        "install_log": install_result.stdout + install_result.stderr,
    }
    yield payload
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def external_distribution_probe(
    external_distribution: dict[str, object],
) -> dict[str, object]:
    """Run the disabled/enabled/invoked probe inside the isolated venv."""
    python = external_distribution["python"]
    assert isinstance(python, Path)
    return run_probe(
        python,
        "inspect",
        EXTERNAL_FIXED_INPUT,
        EXTERNAL_EXPECTED_OUTPUT,
    )


class TestExternalDistribution:
    def test_external_distribution_discovery_is_metadata_only_and_disabled(
        self,
        external_distribution: dict[str, object],
        external_distribution_probe: dict[str, object],
    ) -> None:
        probe = external_distribution_probe
        assert probe["ok"] is True
        assert probe["discovered_names"] == [EXTERNAL_PLUGIN_NAME]
        assert probe["entry_points_scanned"] == 1
        assert probe["module_imported_after_discovery"] is False
        assert probe["preloaded_before_enable"] is False
        assert probe["disabled_error"] == "PluginDisabledError"
        assert probe["is_trusted"] is False
        assert probe["trust_level"] == "untrusted"

        status = probe["status_before"]
        assert isinstance(status, dict)
        assert status["described"]["state"] == "present"
        assert status["installed"]["state"] == "present"
        assert status["enabled"]["state"] == "absent"
        assert status["verified"]["state"] == "not_verified"
        assert (
            status["entry_point"] == f"{SCIENCE_PLUGIN_ENTRY_POINT_GROUP}:acp2_external_validator"
        )
        assert status["capability_ids"] == [EXTERNAL_CAPABILITY_NAME]

        after_enable = probe["status_after_enable"]
        assert isinstance(after_enable, dict)
        assert after_enable["enabled"]["state"] == "present"
        assert after_enable["verified"] == status["verified"]

        assert "--no-isolation" in external_distribution["build_argv"] or (
            "--no-build-isolation" in external_distribution["build_argv"]
        )
        assert "--no-deps" in external_distribution["install_argv"]
        assert "--no-index" in external_distribution["install_argv"]

    def test_external_distribution_enabled_invocation_returns_valid_result(
        self,
        external_distribution_probe: dict[str, object],
    ) -> None:
        probe = external_distribution_probe
        expected = json.loads(EXTERNAL_EXPECTED_OUTPUT.read_text(encoding="utf-8"))
        result = probe["result"]
        assert isinstance(result, dict)
        assert result == expected
        assert result["task"] == TaskKind.SINGLEPOINT.value
        assert result["status"] == "completed"
        assert result["complete"] is True
        assert result["units"] == {"coordinates": "angstrom", "energy": "hartree"}
        assert result["atom_order"] == result["symbols"] == ["O", "H", "H"]
        assert result["capability_schema"] == "acp2.science.external.native_singlepoint"
        assert probe["result_schema_version"] == 1
        assert probe["plugin_lock_digest_stable"] is True
        assert isinstance(probe["plugin_lock_digest"], str)
        assert len(probe["plugin_lock_digest"]) == 64

    def test_external_distribution_digest_matches_installed_metadata(
        self,
        external_distribution: dict[str, object],
        external_distribution_probe: dict[str, object],
    ) -> None:
        probe = external_distribution_probe
        installed = probe["installed"]
        assert isinstance(installed, dict)
        assert installed["version"] == "1.0.0"
        assert installed["license"] == "MIT"
        assert installed["entry_point_group"] == SCIENCE_PLUGIN_ENTRY_POINT_GROUP
        assert installed["entry_point_name"] == "acp2_external_validator"
        assert installed["entry_point_value"] == "acp2_fixture_validator.plugin:PLUGIN"
        assert installed["distribution_digest"] == installed["manifest_distribution_digest"]
        assert installed["fixture_evidence_digest"] == installed["manifest_fixture_evidence_digest"]
        assert installed["distribution_digest"].startswith("sha256:")
        assert len(installed["distribution_digest"]) == len("sha256:") + 64

        python = external_distribution["python"]
        assert isinstance(python, Path)
        assert installed["package_path"].startswith(str(python.parent.parent))
        wheel_sha256 = external_distribution["wheel_sha256"]
        assert isinstance(wheel_sha256, str) and len(wheel_sha256) == 64
