"""M2-05 — third-party science plugin production assembly (G04/G16, R07).

Two layers, both against real code:

* **Platform layer** (no venv): the installed-plugin enablement ledger, the
  computed distribution digest, the fail-closed authorization/trust/licence/
  version/digest paths, the frozen read-only snapshot, the upgrade pin, and the
  single discovery provider that merges built-in/domain/authorised-plugin
  capabilities behind one ``app.state`` cache with a generation/etag key.
* **Clean-environment layer** (isolated venv): the fixture wheel is built and
  installed with the platform tooling, enabled by the operator path, and then a
  **new process** starts the real service (uvicorn + lifespan), discovers the
  capability on both discovery surfaces, prepares a real artifact through the
  Controller and runs the plugin analysis; then disable, uninstall and the
  historical read-only path.

Nothing here executes QC, LSF or a model: the fixture is a pure-analysis stub
over a fixed native record, and the declared stub boundary is the task record.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import shutil
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.science_plugins import (
    DISTRIBUTION_DIGEST_ALGORITHM,
    PLUGIN_ENABLEMENT_SCHEMA,
    SciencePluginRegistry,
    compute_distribution_digest,
)
from cccp.plugin import (
    PluginLicenseError,
    PluginVersionMismatchError,
)
from tests._acp2_external_plugin_harness import (
    CAPABILITY_NAME,
    DISTRIBUTION_NAME,
    FIXED_INPUT,
    PLUGIN_NAME,
    build_fixture_wheel,
    create_isolated_venv,
    install_wheel,
    run_python_script,
    uninstall_distribution,
)

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / "tests" / "_acp2_m205_plugin_probe.py"
RPC = "acp_services.runtime.capability_registry"

FAKE_DISTRIBUTION = "vendor-science-dist"
FAKE_PLUGIN = "vendor.fixture"
FAKE_CAPABILITY = "vendor_fixture_native"


# ---------------------------------------------------------------------------
# Fake installed distribution (unit layer)
# ---------------------------------------------------------------------------


def _manifest(
    *,
    version: str = "1.0.0",
    license_id: str | None = "MIT",
    digest: str | None = None,
    api_range: str = ">=1,<2",
    kind: str = "pure_analysis",
    delegates_to: str | None = None,
    capability: str = FAKE_CAPABILITY,
    plugin: str = FAKE_PLUGIN,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": plugin,
        "distribution": FAKE_DISTRIBUTION,
        "version": version,
        "api": {"api_version": 1, "compatible_range": api_range, "schema_version": 1},
        "capabilities": [
            {
                "name": capability,
                "schema": "acp2.science.vendor.native_record",
                "schema_version": 1,
                "kind": kind,
                "implementation_id": "vendor.fixture.validator",
                "delegates_to_task_kind": delegates_to,
                "recipe_refs": [],
                "validator_refs": [],
            }
        ],
        "science_software": [],
        "resources": {
            "requires_subprocess": False,
            "requires_network": False,
            "min_memory_mb": None,
            "cpu_cores": None,
        },
        "dependencies": [],
        "error_semantics": {
            "pre_launch_typed": True,
            "partial_output_tolerant": False,
            "never_overrides_builtin": True,
        },
        "default_enabled": False,
        "science_version_range": ">=1,<2",
        "entry_point_group_version": 1,
        "distribution_digest": digest,
        "fixture_evidence": [],
        "trust_level": "untrusted",
    }
    if license_id is not None:
        payload["license"] = license_id
    return payload


class _FakeEntryPoint:
    group = "cccp.science_plugins"

    def __init__(self, distribution: Any, module_name: str) -> None:
        self.name = "vendor_fixture"
        self.value = f"{module_name}:PLUGIN"
        self.dist = distribution
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        raise AssertionError("discovery/enablement must never load a plugin module")


def _install_directory(
    root: Path,
    *,
    manifest: dict[str, Any],
    extra_payload: dict[str, bytes] | None = None,
) -> tuple[_FakeEntryPoint, importlib.metadata.PathDistribution]:
    """Materialise an installed-looking distribution with a real RECORD."""
    site = root / "site"
    package = site / "vendor_fixture"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "validator.py").write_text("PLUGIN = object()\n", encoding="utf-8")
    (package / "data").mkdir(exist_ok=True)
    (package / "data" / "record.json").write_text('{"schema": "record"}\n', encoding="utf-8")
    for relative, payload in (extra_payload or {}).items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    version = str(manifest["version"])
    dist_info = site / f"vendor_science_dist-{version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {FAKE_DISTRIBUTION}\nVersion: {version}\n",
        encoding="utf-8",
    )
    manifest_path = dist_info / "cccp_science_plugin.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload_files = sorted(
        path.relative_to(site).as_posix()
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    record_lines = [f"{name},," for name in payload_files]
    record_lines.append(f"{dist_info.name}/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(record_lines) + "\n", encoding="utf-8")
    distribution = importlib.metadata.PathDistribution(dist_info)
    return _FakeEntryPoint(distribution, "vendor_fixture.validator"), distribution


def _installed(
    root: Path,
    **manifest_kwargs: Any,
) -> tuple[SciencePluginRegistry, _FakeEntryPoint]:
    """Install the fake distribution and return a registry over its state root."""
    manifest = _manifest(**manifest_kwargs)
    entry_point, distribution = _install_directory(root, manifest=manifest)
    computed = compute_distribution_digest(distribution, declared=manifest["distribution_digest"])
    if manifest["distribution_digest"] is None:
        # A well-formed installation declares the digest of its own bytes.
        manifest["distribution_digest"] = computed.digest
        dist_info = Path(distribution._path)  # noqa: SLF001 - the fake's own metadata dir
        (dist_info / "cccp_science_plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    registry = SciencePluginRegistry(
        state_root=root / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[entry_point],
        distribution_reader=lambda name: distribution,
    )
    return registry, entry_point


def _authorization(record: Any, *, action: str = "capability.enable", **overrides: Any) -> Any:
    from acp_services.runtime.capability_registry import CapabilityAuthorization

    values: dict[str, Any] = {
        "action": action,
        "capability_id": record.capability_id,
        "version": record.version,
        "descriptor_digest": record.descriptor_digest,
        "authorized_by": "controller",
        "issued_at": "2026-10-27T00:00:00Z",
    }
    values.update(overrides)
    return CapabilityAuthorization(**values)


# ---------------------------------------------------------------------------
# 1. Computed distribution digest
# ---------------------------------------------------------------------------


def test_computed_distribution_digest_hashes_the_installed_payload(tmp_path: Path) -> None:
    registry, _entry_point = _installed(tmp_path)
    record = registry.installed_capability(FAKE_CAPABILITY)

    digest = record.distribution_digest
    assert digest.algorithm == DISTRIBUTION_DIGEST_ALGORITHM
    assert digest.digest.startswith("sha256:") and len(digest.digest) == len("sha256:") + 64
    assert digest.declared == digest.digest
    assert digest.matches_declared is True
    assert digest.file_count == 3  # __init__.py, validator.py, data/record.json
    assert record.descriptor_digest.startswith("sha256:")


def test_tampered_payload_breaks_the_declared_claim(tmp_path: Path) -> None:
    registry, entry_point = _installed(tmp_path)
    registry.scan()
    payload = Path(str(entry_point.dist.locate_file("vendor_fixture/validator.py")))
    payload.write_text("PLUGIN = object()  # tampered\n", encoding="utf-8")

    refreshed = SciencePluginRegistry(
        state_root=tmp_path / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[entry_point],
        distribution_reader=lambda name: entry_point.dist,
    )
    record = refreshed.installed_capability(FAKE_CAPABILITY)
    assert record.distribution_digest.matches_declared is False
    with pytest.raises(Exception) as excinfo:
        refreshed.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    assert getattr(excinfo.value, "code", "") == "capability_distribution_digest_mismatch"


def test_manifest_digest_is_never_a_substitute_for_the_computed_value(tmp_path: Path) -> None:
    registry, _entry_point = _installed(tmp_path, digest="sha256:" + "1" * 64)
    record = registry.installed_capability(FAKE_CAPABILITY)
    assert record.distribution_digest.declared == "sha256:" + "1" * 64
    assert record.distribution_digest.matches_declared is False

    from acp_services.runtime.capability_registry import CapabilityConflictError

    with pytest.raises(CapabilityConflictError) as excinfo:
        registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    assert excinfo.value.code == "capability_distribution_digest_mismatch"


def test_missing_manifest_digest_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(digest=None)
    entry_point, distribution = _install_directory(tmp_path, manifest=manifest)
    registry = SciencePluginRegistry(
        state_root=tmp_path / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[entry_point],
        distribution_reader=lambda name: distribution,
    )
    record = registry.installed_capability(FAKE_CAPABILITY)
    assert record.distribution_digest.declared is None
    with pytest.raises(Exception) as excinfo:
        registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    assert getattr(excinfo.value, "code", "") == "capability_distribution_digest_missing"


# ---------------------------------------------------------------------------
# 2. Authorization / trust / licence / version / kind
# ---------------------------------------------------------------------------


def test_untrusted_capability_is_discover_only(tmp_path: Path) -> None:
    registry, _entry_point = _installed(tmp_path)
    untrusted = SciencePluginRegistry(
        state_root=tmp_path / "state",
        entry_points=[_entry_point],
        distribution_reader=lambda name: _entry_point.dist,
    )
    record = untrusted.installed_capability(FAKE_CAPABILITY)
    from acp_services.runtime.capability_registry import CapabilityUntrustedError

    with pytest.raises(CapabilityUntrustedError):
        untrusted.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    assert untrusted.enablements() == ()


def test_enable_requires_a_matching_authorization_fact(tmp_path: Path) -> None:
    from acp_services.runtime.capability_registry import CapabilityAuthorizationError

    registry, _entry_point = _installed(tmp_path)
    record = registry.installed_capability(FAKE_CAPABILITY)

    with pytest.raises(CapabilityAuthorizationError) as missing:
        registry.enable(FAKE_CAPABILITY, authorization=None)
    assert missing.value.code == "capability_authorization_required"

    for overrides, code in (
        ({"descriptor_digest": "sha256:" + "0" * 64}, "capability_authorization_invalid"),
        ({"action": "capability.disable"}, "capability_authorization_invalid"),
        ({"authorized_by": "browser"}, "capability_authorization_invalid"),
        ({"issued_at": ""}, "capability_authorization_invalid"),
        ({"expires_at": "2000-01-01T00:00:00Z"}, "capability_authorization_expired"),
    ):
        with pytest.raises(CapabilityAuthorizationError) as refused:
            registry.enable(FAKE_CAPABILITY, authorization=_authorization(record, **overrides))
        assert refused.value.code == code, overrides
    assert registry.enablements() == ()


def test_unknown_license_and_version_conflict_fail_closed_at_scan(tmp_path: Path) -> None:
    bad_license, _dist = _install_directory(tmp_path / "a", manifest=_manifest(license_id="WTFPL"))
    registry = SciencePluginRegistry(
        state_root=tmp_path / "a" / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[bad_license],
        distribution_reader=lambda name: bad_license.dist,
    )
    with pytest.raises(PluginLicenseError):
        registry.scan()

    bad_range, _dist = _install_directory(tmp_path / "b", manifest=_manifest(api_range=">=2,<3"))
    registry = SciencePluginRegistry(
        state_root=tmp_path / "b" / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[bad_range],
        distribution_reader=lambda name: bad_range.dist,
    )
    with pytest.raises(PluginVersionMismatchError):
        registry.scan()


def test_compute_capability_stays_deferred(tmp_path: Path) -> None:
    registry, _entry_point = _installed(tmp_path, kind="compute", delegates_to="singlepoint")
    record = registry.installed_capability(FAKE_CAPABILITY)
    with pytest.raises(Exception) as excinfo:
        registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    assert getattr(excinfo.value, "code", "") == "capability_worker_hosted_deferred"
    assert registry.authorized_capabilities() == ()


# ---------------------------------------------------------------------------
# 3. Persistence, new-process discovery and frozen history
# ---------------------------------------------------------------------------


def test_enablement_persists_installed_enabled_enabled_by_enabled_at(tmp_path: Path) -> None:
    registry, entry_point = _installed(tmp_path)
    record = registry.installed_capability(FAKE_CAPABILITY)
    enablement = registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))

    ledger = tmp_path / "state" / "plugins" / "vendor.fixture" / "1.0.0" / FAKE_CAPABILITY
    document = json.loads((ledger / "enablement.json").read_text(encoding="utf-8"))
    assert document["schema"] == PLUGIN_ENABLEMENT_SCHEMA
    assert document["installed"] is True and document["installed_at"]
    assert document["enabled"] is True
    assert document["enabled_by"] == "controller"
    assert document["enabled_at"] == enablement.enabled_at
    assert document["descriptor_digest"] == record.descriptor_digest
    assert document["distribution_digest"]["digest"] == record.distribution_digest.digest

    # A *new* process reads the same facts from the ledger.
    reopened = SciencePluginRegistry(
        state_root=tmp_path / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[entry_point],
        distribution_reader=lambda name: entry_point.dist,
    )
    assert [item.capability_id for item in reopened.authorized_capabilities()] == [FAKE_CAPABILITY]
    assert entry_point.load_calls == 0

    disabled = reopened.disable(
        FAKE_CAPABILITY, authorization=_authorization(record, action="capability.disable")
    )
    assert disabled.enabled is False and disabled.disabled_by == "controller"
    assert reopened.authorized_capabilities() == ()
    assert reopened.generation() > 0
    # The frozen snapshot is write-once and survives the disablement.
    snapshot = reopened.frozen_descriptor(FAKE_PLUGIN, "1.0.0")
    assert snapshot["capability_id"] == FAKE_CAPABILITY
    assert entry_point.load_calls == 0


def test_frozen_descriptor_survives_uninstall_and_never_resolves_the_active_version(
    tmp_path: Path,
) -> None:
    registry, entry_point = _installed(tmp_path)
    record = registry.installed_capability(FAKE_CAPABILITY)
    registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))

    def _gone(name: str) -> Any:
        raise importlib.metadata.PackageNotFoundError(name)

    after_uninstall = SciencePluginRegistry(
        state_root=tmp_path / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[],
        distribution_reader=_gone,
    )
    assert after_uninstall.scan() == ()
    assert after_uninstall.authorized_capabilities() == ()
    assert after_uninstall.refusals()[0]["reason"] == "plugin_not_installed"
    snapshot = after_uninstall.frozen_descriptor(FAKE_PLUGIN, "1.0.0")
    assert snapshot["capability_id"] == FAKE_CAPABILITY
    assert snapshot["distribution_digest"]["digest"] == record.distribution_digest.digest
    assert entry_point.load_calls == 0


def test_upgrade_freezes_the_new_pin_and_keeps_the_old_one_readable(tmp_path: Path) -> None:
    registry, entry_point = _installed(tmp_path)
    v1 = registry.installed_capability(FAKE_CAPABILITY)
    v1_enablement = registry.enable(FAKE_CAPABILITY, authorization=_authorization(v1))

    upgraded_manifest = _manifest(version="2.0.0")
    entry_point_v2, distribution_v2 = _install_directory(
        tmp_path / "v2",
        manifest=upgraded_manifest,
        extra_payload={"version_marker.txt": b"2.0.0\n"},
    )
    computed = compute_distribution_digest(distribution_v2, declared=None)
    upgraded_manifest["distribution_digest"] = computed.digest
    (Path(distribution_v2._path) / "cccp_science_plugin.json").write_text(  # noqa: SLF001
        json.dumps(upgraded_manifest), encoding="utf-8"
    )
    upgraded = SciencePluginRegistry(
        state_root=tmp_path / "state",
        trusted_capabilities=(FAKE_CAPABILITY,),
        entry_points=[entry_point_v2],
        distribution_reader=lambda name: distribution_v2,
    )
    v2 = upgraded.installed_capability(FAKE_CAPABILITY)
    # The old version is no longer installed: it is not executable, and the
    # refusal names the reason instead of silently dropping the fact.
    assert upgraded.authorized_capabilities() == ()
    assert {item["reason"] for item in upgraded.refusals()} == {"plugin_not_installed"}

    v2_enablement = upgraded.enable(FAKE_CAPABILITY, authorization=_authorization(v2))
    assert v2_enablement.version == "2.0.0"
    assert v2_enablement.computed_distribution_digest != v1_enablement.computed_distribution_digest
    assert [item.version for item in upgraded.authorized_capabilities()] == ["2.0.0"]
    # New work uses the new pin; the v1 record is unchanged and readable.
    frozen_v1 = upgraded.frozen_descriptor(FAKE_PLUGIN, "1.0.0")
    frozen_v2 = upgraded.frozen_descriptor(FAKE_PLUGIN, "2.0.0")
    assert frozen_v1["distribution_digest"]["digest"] == v1_enablement.computed_distribution_digest
    assert frozen_v2["distribution_digest"]["digest"] == v2_enablement.computed_distribution_digest
    assert frozen_v1["descriptor_digest"] != frozen_v2["descriptor_digest"]
    versions = {record.version for record in upgraded.enablements()}
    assert versions == {"1.0.0", "2.0.0"}


# ---------------------------------------------------------------------------
# 4. One discovery provider: built-in + domain + authorised plugins
# ---------------------------------------------------------------------------


class _State:
    """Minimal ``app.state`` stand-in (the provider only reads attributes)."""


def _state(registry: SciencePluginRegistry) -> _State:
    state = _State()
    state.science_plugin_registry = registry
    return state


def test_discovery_merges_the_authorised_plugin_into_one_provider(tmp_path: Path) -> None:
    from acp.api import science_catalog

    registry, entry_point = _installed(tmp_path)
    state = _state(registry)
    before = science_catalog.science_catalog(state)
    assert before.plugins == ()
    assert {entry["capability_id"] for entry in before.descriptor_entries()} == {
        "electronic.energy",
        "geometry.optimize",
        "vibration.analyze",
        "vibration.compute",
    }

    record = registry.installed_capability(FAKE_CAPABILITY)
    registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    after = science_catalog.science_catalog(state)
    assert [entry["capability_id"] for entry in after.plugins] == [FAKE_CAPABILITY]
    plugin_entry = after.plugins[0]
    assert plugin_entry["capability_source"] == "science_plugin"
    assert plugin_entry["plugin"]["name"] == FAKE_PLUGIN
    assert plugin_entry["distribution_digest"]["digest"] == record.distribution_digest.digest
    assert plugin_entry["descriptor_digest"] == record.descriptor_digest
    assert after.generation == registry.generation()
    assert after.etag.startswith("sha256:")
    # Built-in entries are forwarded unchanged, after the plugin entries.
    ids = [entry["capability_id"] for entry in after.descriptor_entries()]
    assert ids[-1] == FAKE_CAPABILITY
    assert len(ids) == 5
    # Discovery is metadata only: the plugin module is never loaded.
    assert entry_point.load_calls == 0
    # The analysis projection names it honestly: registered, not executable here
    # (this process did not freeze a handler for it).
    analysis = {
        entry["analysis_id"]: entry for entry in science_catalog.analysis_capabilities(state)
    }
    assert analysis[FAKE_CAPABILITY]["state"] == "registered"
    assert analysis[FAKE_CAPABILITY]["executable"] is False


def test_enablement_generation_invalidates_the_catalog_cache_only_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cccp.calculation.capabilities as capability_module
    from acp.api import science_catalog

    calls = {"builtin": 0, "domain": 0}
    real_builtin = capability_module.builtin_catalog
    real_domain = capability_module.domain_catalog

    def counting_builtin(*args: Any, **kwargs: Any) -> Any:
        calls["builtin"] += 1
        return real_builtin(*args, **kwargs)

    def counting_domain(*args: Any, **kwargs: Any) -> Any:
        calls["domain"] += 1
        return real_domain(*args, **kwargs)

    monkeypatch.setattr(capability_module, "builtin_catalog", counting_builtin)
    monkeypatch.setattr(capability_module, "domain_catalog", counting_domain)

    registry, _entry_point = _installed(tmp_path)
    state = _state(registry)
    first = science_catalog.science_catalog(state)
    second = science_catalog.science_catalog(state)
    assert first is second
    assert calls == {"builtin": 1, "domain": 1}

    record = registry.installed_capability(FAKE_CAPABILITY)
    registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))
    third = science_catalog.science_catalog(state)
    assert third is not first
    assert calls == {"builtin": 2, "domain": 2}
    assert [entry["capability_id"] for entry in third.plugins] == [FAKE_CAPABILITY]

    registry.disable(
        FAKE_CAPABILITY, authorization=_authorization(record, action="capability.disable")
    )
    fourth = science_catalog.science_catalog(state)
    assert fourth.plugins == ()
    assert calls == {"builtin": 3, "domain": 3}


def test_plugin_capability_is_registered_into_the_injected_analysis_registry(
    tmp_path: Path,
) -> None:
    from acp.services_science_plugins_bootstrap import build_plugin_analysis_capabilities

    registry, entry_point = _installed(tmp_path)
    record = registry.installed_capability(FAKE_CAPABILITY)
    registry.enable(FAKE_CAPABILITY, authorization=_authorization(record))

    capabilities = build_plugin_analysis_capabilities(
        registry=registry,
        prepare_operations=None,  # no artifact authority in this process
        artifact_store_root=tmp_path / "artifacts",
        entry_points=[entry_point],
    )
    assert [entry.analysis_id for entry in capabilities] == [FAKE_CAPABILITY]
    registered = capabilities[0]
    assert registered.handler is None  # honest: declared, not executable
    assert registered.state == "registered"
    assert registered.metadata["computed_distribution_digest"] == (
        record.distribution_digest.digest
    )
    assert registered.metadata["descriptor_digest"] == record.descriptor_digest
    assert entry_point.load_calls == 0


def test_plugin_composition_root_reaches_no_execution_station() -> None:
    """AST guard: the plugin composition root is science-free wiring."""
    forbidden_imports = ("subprocess", "cccp.calculation.tasks", "acp.scheduler", "acp.workflows")
    forbidden_calls = frozenset({"Popen", "system", "submit", "enqueue", "create_task"})

    for relative in (
        "src/acp/services_science_plugins_bootstrap.py",
        "src/acp_services/runtime/science_plugins.py",
    ):
        source = (REPO / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
        for module in forbidden_imports:
            assert not any(name.startswith(module) for name in imported), (relative, module)
        assert not (calls & forbidden_calls), (relative, sorted(calls & forbidden_calls))
        # acp_services must never import acp (one-way dependency).
        if relative.startswith("src/acp_services/"):
            assert not any(name == "acp" or name.startswith("acp.") for name in imported)


# ---------------------------------------------------------------------------
# 5. Clean virtual environment: install -> enable -> new process discovery
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def m205_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[dict[str, Any], None, None]:
    override = os.environ.get("ACP2_M205_WORK_ROOT")
    if override:
        root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
    else:
        root = tmp_path_factory.mktemp("m205-plugin")
    root.mkdir(parents=True, exist_ok=True)
    wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
    python, venv_argv = create_isolated_venv(root)
    install_argv, install_result = install_wheel(python, wheel_v1)
    run_root = root / "run-root"
    payload: dict[str, Any] = {
        "root": root,
        "wheel": wheel_v1,
        "python": python,
        "run_root": run_root,
        "build_argv": build_argv_v1,
        "build_log": build_log_v1,
        "venv_argv": venv_argv,
        "install_argv": install_argv,
        "install_log": install_result.stdout + install_result.stderr,
    }
    payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
    product = root / "v1-product.json"
    payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
    payload["product_path"] = product
    payload["upgrade_discovery"] = run_python_script(python, PROBE, "discovery", run_root)
    uninstall_argv, uninstall_result = uninstall_distribution(python, DISTRIBUTION_NAME)
    payload["uninstall_argv"] = uninstall_argv
    payload["uninstall_log"] = uninstall_result.stdout + uninstall_result.stderr
    payload["historical"] = run_python_script(python, PROBE, "historical", run_root, product)
    yield payload
    shutil.rmtree(root, ignore_errors=True)


def test_clean_environment_install_is_offline_and_discovery_is_metadata_only(
    m205_environment: dict[str, Any],
) -> None:
    enabled = m205_environment["enabled"]
    assert enabled["ok"] is True
    assert [entry["capability_id"] for entry in enabled["installed"]] == [CAPABILITY_NAME]
    installed = enabled["installed"][0]
    assert installed["plugin_name"] == PLUGIN_NAME
    assert installed["capability_kind"] == "pure_analysis"
    assert installed["license"] == "MIT"
    assert installed["distribution_digest"]["matches_declared"] is True
    assert installed["distribution_digest"]["algorithm"] == DISTRIBUTION_DIGEST_ALGORITHM
    assert enabled["module_imported_after_scan"] is False
    assert enabled["module_imported_after_enable"] is False

    assert "--no-isolation" in m205_environment["build_argv"]
    assert "--no-deps" in m205_environment["install_argv"]
    assert "--no-index" in m205_environment["install_argv"]


def test_clean_environment_fail_closed_negatives(m205_environment: dict[str, Any]) -> None:
    enabled = m205_environment["enabled"]
    assert enabled["untrusted_error"] == "capability_untrusted"
    assert enabled["no_authorization_error"] == "capability_authorization_required"
    assert enabled["wrong_digest_error"] == "capability_authorization_invalid"


def test_clean_environment_enablement_is_persisted(m205_environment: dict[str, Any]) -> None:
    enabled = m205_environment["enabled"]
    enablement = enabled["enablement"]
    assert enablement["installed"] is True
    assert enablement["enabled"] is True
    assert enablement["enabled_by"] == "controller"
    assert enablement["enabled_at"]
    assert enablement["capability_id"] == CAPABILITY_NAME
    assert (
        enablement["distribution_digest"]["digest"] == (enablement["computed_distribution_digest"])
    )
    ledger = (
        Path(m205_environment["run_root"])
        / "science-plugins"
        / "plugins"
        / PLUGIN_NAME
        / "1.0.0"
        / CAPABILITY_NAME
        / "enablement.json"
    )
    assert ledger.is_file()
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["enabled"] is True and document["enabled_by"] == "controller"
    assert document["installed"] is True
    assert enabled["executable"] == [CAPABILITY_NAME]


def test_real_service_discovers_the_enabled_plugin_on_both_surfaces(
    m205_environment: dict[str, Any],
) -> None:
    served = m205_environment["served"]
    assert served["ok"] is True
    plugins = served["application_plugin_entries"]
    assert [entry["capability_id"] for entry in plugins] == [CAPABILITY_NAME]
    again = served["v2_plugin_entries"]
    assert again == plugins
    assert served["descriptors_agree"] is True
    assert served["analysis_agree"] is True
    assert plugins[0]["capability_source"] == "science_plugin"
    assert plugins[0]["distribution_digest"]["matches_declared"] is True
    # The built-in capability set is still there, unchanged and first.
    assert served["application_building_capabilities"] == [
        "electronic.energy",
        "geometry.optimize",
        "vibration.analyze",
        "vibration.compute",
    ]
    analysis = {entry["analysis_id"]: entry for entry in served["analysis_entries"]}
    assert analysis["vibration.analyze"]["state"] == "executable"
    assert analysis[CAPABILITY_NAME] == {
        "analysis_id": CAPABILITY_NAME,
        "state": "executable",
        "registered": True,
        "executable": True,
        "version": "1.0.0",
        "owner": f"plugin:{PLUGIN_NAME}",
    }


def test_plugin_analysis_runs_over_the_prepared_artifact_with_full_provenance(
    m205_environment: dict[str, Any],
) -> None:
    served = m205_environment["served"]
    assert served["analyze_status"] == 200, served.get("product")
    product = served["product"]
    analysis = product["result"]
    assert product["analysis_id"] == CAPABILITY_NAME
    assert analysis["state"] == "computed"
    assert product["started"] is False
    assert analysis["product"]["capability_output_schema"] == (
        "acp2.science.external.native_singlepoint"
    )
    result = analysis["product"]["task_result"]
    assert result["task"] == "singlepoint"
    assert result["status"] == "completed" and result["complete"] is True
    assert result["metadata"]["atom_order"] == ["O", "H", "H"]

    provenance = analysis["provenance"]
    expected_path = (
        REPO
        / "tests"
        / "fixtures"
        / "acp2"
        / "science"
        / "external_plugin"
        / "fixtures"
        / "expected_output.json"
    )
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert result["energy_hartree"] == expected["energy_hartree"]
    assert provenance["capability_ref"] == f"{CAPABILITY_NAME}@1.0.0"
    assert provenance["descriptor_digest"].startswith("sha256:")
    assert provenance["form_digest"].startswith("sha256:")
    assert provenance["computed_distribution_digest"].startswith("sha256:")
    assert provenance["distribution_digest"]["digest"] == provenance["computed_distribution_digest"]
    assert provenance["starts_subprocess"] is False
    assert provenance["starts_task"] is False
    assert provenance["executed_in"] == "controller"
    assert provenance["input_digests"] == [served["artifact_ref"]["digest"]]


def test_analysis_refusals_start_nothing(m205_environment: dict[str, Any]) -> None:
    served = m205_environment["served"]
    # A tampered artifact digest is refused by the prepare-operation authority
    # with the frozen M2-02 artifact vocabulary (409 ``revision_conflict``,
    # rule ``digest_mismatch``) — the same refusal the built-in analysis gets.
    assert served["tampered_status"] == 409
    assert served["tampered_code"] == "revision_conflict"
    assert served["tampered_rule"] == "digest_mismatch"
    # An invented analysis id is refused with the full allowed set.
    assert served["unknown_status"] == 422
    assert served["unknown_code"] == "unsupported_capability"
    assert CAPABILITY_NAME in served["unknown_allowed"]
    # A plugin analysis must declare the task envelope it consumes.
    assert served["envelope_missing_status"] == 400
    assert served["envelope_missing_rule"] == "required"


def test_disable_is_persisted_and_stops_new_process_discovery(
    m205_environment: dict[str, Any],
) -> None:
    served = m205_environment["served"]
    assert served["after_disable_plugin_entries"] == []
    assert CAPABILITY_NAME not in served["after_disable_analysis_ids"]
    # Re-enabling later is a new generation, never a silent re-activation.
    assert served["generation_after_reenable"] >= 3


def test_uninstall_keeps_the_frozen_descriptor_and_the_product_readable(
    m205_environment: dict[str, Any],
) -> None:
    historical = m205_environment["historical"]
    assert historical["ok"] is True
    assert historical["discovered_names"] == []
    assert historical["module_imported"] is False
    assert historical["snapshot_capability_id"] == CAPABILITY_NAME
    assert historical["snapshot_schema"] == "acp2.plugin.capability-descriptor.v1"
    assert historical["snapshot_descriptor_digest_matches_product"] is True
    assert historical["snapshot_distribution_digest_matches_product"] is True
    assert historical["product_digest_stable"] is True
    assert historical["product_capability_ref"] == f"{CAPABILITY_NAME}@1.0.0"
    # The ledger keeps the install/enable facts for the historical record.
    assert historical["enablements"][0]["enabled"] is True
    assert historical["enablements"][0]["installed"] is True

    product_path = m205_environment["product_path"]
    product = json.loads(Path(product_path).read_text(encoding="utf-8"))
    assert product["result"]["provenance"]["computed_distribution_digest"].startswith("sha256:")
