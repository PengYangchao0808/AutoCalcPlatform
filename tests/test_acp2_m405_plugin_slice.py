"""M4-05 — third-party capability plugin vertical slice (G16/G18).

Two layers, both against real code:

* **contract layer** (fast): the registered ``schema-form-live`` C row is a real
  body with its declared observations, the acceptance orchestrator reaches the
  platform only through the operator CLI / real Controller / plugin host (never
  an in-test ``register``), the negative rows are all present, and the fixture
  variant helper mutates only an isolated copy of the committed tree.
* **clean-environment layer** (``--run-slow``): a throw-away venv is created, the
  committed fixture wheel is built offline and installed, the **operator CLI**
  enables the capability and persists the enablement, and mutated wheels
  (incompatible API range / unknown licence) fail closed.  Nothing is installed
  into the shared integration environment.

The full slice (discovery on both surfaces, Workbench form + preflight,
analysis execution, upgrade, uninstall, real browser row) is executed by
``scripts/acceptance/acp2_m405.py``; this file guards its shape and the
environment mechanics it depends on.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ACCEPTANCE = REPO / "scripts" / "acceptance"
PLUGIN_ENV_PATH = ACCEPTANCE / "acp2_m405_plugin_env.py"
ORCHESTRATOR_PATH = ACCEPTANCE / "acp2_m405.py"
PROBE_PATH = ACCEPTANCE / "acp2_m405_probe.py"
DRIVER_PATH = ACCEPTANCE / "acp2_m405_form.js"
SCENARIO_PATH = ACCEPTANCE / "acp2_experience_scenario_schema_form_live.py"
SCENARIOS_PATH = ACCEPTANCE / "acp2_experience_scenarios.py"
RUNTIME_PATH = ACCEPTANCE / "acp2_runtime.py"
HARNESS_PATH = ACCEPTANCE / "acp2_experience.py"

REQUIRED_NEGATIVES = (
    "untrusted_capability",
    "insufficient_authorization",
    "installed_bytes_digest_mismatch",
    "restored_bytes_control",
    "incompatible_api_range",
    "unknown_license",
    "clean_reinstall_control",
)
SLICE_STEPS = (
    "environment",
    "enable",
    "discovery",
    "form",
    "execution",
    "upgrade",
    "upgrade.after",
    "browser",
    "uninstall",
    "negatives",
)


def _load(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plugin_env():
    if str(ACCEPTANCE) not in sys.path:
        sys.path.insert(0, str(ACCEPTANCE))
    return _load("acp2_m405_plugin_env_test", PLUGIN_ENV_PATH)


@pytest.fixture(scope="module")
def orchestrator(plugin_env):
    return _load("acp2_m405_acceptance_test", ORCHESTRATOR_PATH)


@pytest.fixture(scope="module")
def scenario_registry(plugin_env):
    # Importing the registry discovers and imports every sibling scenario body,
    # including the M4-05 one.
    return _load("acp2_experience_scenarios_m405", SCENARIOS_PATH)


@pytest.fixture(scope="module")
def runtime():
    return _load("acp2_runtime_m405", RUNTIME_PATH)


@pytest.fixture(scope="module")
def harness():
    return _load("acp2_experience_m405", HARNESS_PATH)


# ---------------------------------------------------------------------------
# 1. The registered browser scenario is live and really implemented
# ---------------------------------------------------------------------------


def test_schema_form_live_is_registered_as_a_live_row(scenario_registry, runtime, harness):
    assert "schema-form-live" in scenario_registry.SCENARIO_IMPLS
    impl = scenario_registry.SCENARIO_IMPLS["schema-form-live"]
    assert impl.__module__ == "acp2_experience_scenario_schema_form_live"
    assert "schema-form-live" in harness.SCENARIO_IDS
    assert "schema-form-live" in harness.LIVE_DEFAULT
    assert "schema-form-live" not in harness.FIXTURE_ONLY_SCENARIOS

    declared = runtime.EXPERIENCE_REQUIRED_OBSERVATIONS["schema-form-live"]
    # The row runs a real Controller, a real HTTP client and a real browser, and
    # starts no model/QC/LSF process and prepares no scientific artifact.
    assert declared["transport"] == "present"
    assert declared["controller"] == "present"
    assert declared["browser"] == "present"
    assert declared["model"] == "absent"
    assert declared["qc"] == "absent"
    assert declared["lsf"] == "absent"
    assert declared["artifact"] == "absent"
    assert declared["denial"] == "absent"


def test_scenario_body_reaches_the_plugin_through_public_paths(plugin_env, scenario_registry):
    source = SCENARIO_PATH.read_text(encoding="utf-8")
    # The environment is provisioned/installed and enabled by the real operator
    # CLI; the service is the real Controller; no capability is registered here.
    assert "acp2_m405_plugin_env" in source
    assert "run_operator_cli" in source
    assert "PlatformController" in source
    assert "provision_identities" in source
    for forbidden in (
        "SciencePluginRegistry(",
        "entry_points=",
        "PluginHost(",
        "invoke_capability(",
        "monkeypatch",
    ):
        assert forbidden not in source, forbidden
    # The module is import-safe (the discovery import already succeeded); the
    # body is the registered handler itself, never a second wrapper.
    module = sys.modules["acp2_experience_scenario_schema_form_live"]
    assert module.SCENARIO_ID == "schema-form-live"
    assert callable(module.handle)
    assert scenario_registry.SCENARIO_IMPLS["schema-form-live"] is module.handle


# ---------------------------------------------------------------------------
# 2. The orchestrator is a real public-path slice (no in-process registration)
# ---------------------------------------------------------------------------


def test_orchestrator_runs_every_slice_step(orchestrator):
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for step in SLICE_STEPS:
        assert f'self.step("{step}"' in source, step
    # The browser leg is the registered C row, not a second browser harness.
    assert 'BROWSER_SCENARIO = "schema-form-live"' in source
    assert '"--scenario"' in source and '"--mode"' in source and '"live"' in source
    # Evidence is written with digests for every leg document.
    assert '"trace.jsonl"' not in source  # Trace owns the file
    assert "summary.json" in source


def test_orchestrator_never_registers_a_capability(orchestrator):
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "SciencePluginRegistry(",
        "registry.enable(",
        "registry.register(",
        "entry_points=",
        "PluginHost(",
    ):
        assert forbidden not in source, forbidden
    # It uses the operator CLI, the real Controller and the real prepare/analyze
    # commands (the exact public paths the plan requires).
    assert "run_operator_cli" in source
    assert "PluginController" in source
    assert "ControllerClient" in source
    assert "/analyze" in source


def test_every_fail_closed_negative_row_is_exercised(orchestrator):
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    for row in REQUIRED_NEGATIVES:
        assert f'"{row}"' in source, row
    # The four plan-mandated negatives map onto those rows.
    assert "capability_untrusted" in source
    assert "capability_authorization_invalid" in source
    assert "capability_distribution_digest_mismatch" in source
    assert "distribution_digest_mismatch" in source
    assert "requires API" in source
    assert "license" in source


def test_probe_and_driver_are_real_and_present(plugin_env):
    assert PROBE_PATH.is_file()
    probe = PROBE_PATH.read_text(encoding="utf-8")
    assert "frozen_descriptor" in probe
    assert "compute_input_digest" in probe
    assert "SciencePluginRegistry(" not in probe

    assert DRIVER_PATH.is_file()
    driver = DRIVER_PATH.read_text(encoding="utf-8")
    for step in (
        "discovery.capability_forms",
        "form.render",
        "preflight.numeric_edit_refused",
        "preflight",
        "view_model",
        "descriptor_cross_check",
    ):
        assert step in driver, step
    # No test double transport: the driver uses the platform's real client
    # modules and a real fetch.
    assert 'require("vm")' in driver
    assert "global.fetch" not in driver  # fetch is the Node global, not replaced
    assert "fetch(" in driver
    assert "ACPClient.science" in driver
    assert "ACPCapabilityForm" in driver


# ---------------------------------------------------------------------------
# 3. Fixture variants mutate only an isolated copy
# ---------------------------------------------------------------------------


def test_variant_tree_mutates_only_the_copied_tree(plugin_env, tmp_path: Path):
    destination = tmp_path / "variant"
    applied = plugin_env.prepare_variant_tree(
        plugin_env.FIXTURE_ROOT,
        destination,
        version="1.0.1",
        license_id="WTFPL",
        api_range=">=2,<3",
    )
    manifest = json.loads((destination / "cccp_science_plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.1"
    assert manifest["license"] == "WTFPL"
    assert manifest["api"]["compatible_range"] == ">=2,<3"
    backend = (destination / "build_backend.py").read_text(encoding="utf-8")
    assert 'VERSION = "1.0.1"' in backend
    assert 'LICENSE_ID = "WTFPL"' in backend
    assert applied["api.compatible_range"]["to"] == ">=2,<3"

    original = json.loads(
        (plugin_env.FIXTURE_ROOT / "cccp_science_plugin.json").read_text(encoding="utf-8")
    )
    assert original["version"] == "1.0.0"
    assert original["license"] == "MIT"
    assert original["api"]["compatible_range"] == ">=1,<2"


def test_upgrade_pair_is_a_real_same_capability_upgrade(plugin_env):
    v1 = json.loads(
        (plugin_env.UPGRADE_ROOT / "v1" / "cccp_science_plugin.json").read_text(encoding="utf-8")
    )
    v2 = json.loads(
        (plugin_env.UPGRADE_ROOT / "v2" / "cccp_science_plugin.json").read_text(encoding="utf-8")
    )
    assert v1["name"] == v2["name"] == plugin_env.UPGRADE_PLUGIN
    assert v1["capabilities"][0]["name"] == v2["capabilities"][0]["name"]
    assert (v1["version"], v2["version"]) == ("1.0.0", "2.0.0")
    assert v1["distribution"] == v2["distribution"] == plugin_env.UPGRADE_DISTRIBUTION


# ---------------------------------------------------------------------------
# 4. Clean environment: install -> operator enable -> fail-closed variants
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_operator_cli_enables_the_installed_fixture(plugin_env, tmp_path: Path):
    python, _venv = plugin_env.create_isolated_venv(tmp_path)
    wheel, build = plugin_env.build_fixture_wheel(plugin_env.FIXTURE_ROOT, tmp_path / "wheel")
    assert "--no-isolation" in build.argv
    install = plugin_env.install_wheel(python, wheel)
    assert "--no-deps" in install.argv and "--no-index" in install.argv

    run_root = tmp_path / "run-root"
    listed, payload = plugin_env.run_operator_cli(python, ["list"], run_root=run_root)
    assert listed.returncode == 0, listed.log
    installed = payload["installed"]
    assert [entry["capability_id"] for entry in installed] == [plugin_env.CAPABILITY]
    assert installed[0]["distribution_digest"]["matches_declared"] is True
    computed = installed[0]["distribution_digest"]["digest"]

    enabled, enable_payload = plugin_env.run_operator_cli(
        python, ["enable", "--capability", plugin_env.CAPABILITY], run_root=run_root
    )
    assert enabled.returncode == 0, enabled.log
    record = enable_payload["enabled"]
    assert record["capability_id"] == plugin_env.CAPABILITY
    assert record["enabled"] is True
    assert record["enabled_by"] == plugin_env.DEFAULT_PRINCIPAL
    assert record["computed_distribution_digest"] == computed
    ledger = (
        run_root
        / "science-plugins"
        / "plugins"
        / plugin_env.PLUGIN
        / "1.0.0"
        / plugin_env.CAPABILITY
        / "enablement.json"
    )
    assert ledger.is_file()
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert document["enabled"] is True and document["installed"] is True

    # Permission: an authorization principal outside the allow-list is refused.
    denied, denied_payload = plugin_env.run_operator_cli(
        python,
        ["enable", "--capability", plugin_env.CAPABILITY, "--authorized-by", "reviewer"],
        run_root=tmp_path / "denied-root",
        principals="controller",
    )
    assert denied.returncode != 0
    assert denied_payload["error"] == "capability_authorization_invalid"

    # Trust: the same install is discover-only without the allow-list.
    untrusted, untrusted_payload = plugin_env.run_operator_cli(
        python,
        ["enable", "--capability", plugin_env.CAPABILITY],
        run_root=tmp_path / "untrusted-root",
        trusted=(),
    )
    assert untrusted.returncode != 0
    assert untrusted_payload["error"] == "capability_untrusted"


@pytest.mark.slow
def test_mutated_wheels_fail_closed(plugin_env, tmp_path: Path):
    python, _venv = plugin_env.create_isolated_venv(tmp_path)
    run_root = tmp_path / "run-root"

    for label, kwargs, needle in (
        ("api", {"api_range": ">=2,<3"}, "requires API"),
        ("license", {"license_id": "WTFPL"}, "license"),
    ):
        source = tmp_path / f"variant-{label}"
        plugin_env.prepare_variant_tree(plugin_env.FIXTURE_ROOT, source, **kwargs)
        wheel, build = plugin_env.build_fixture_wheel(source, tmp_path / f"wheel-{label}")
        assert "--no-isolation" in build.argv
        plugin_env.install_wheel(python, wheel)
        listed, payload = plugin_env.run_operator_cli(
            python, ["list"], run_root=run_root, trusted=(plugin_env.CAPABILITY,)
        )
        assert listed.returncode != 0, listed.log
        assert payload["status"] == "failed"
        assert needle in str(payload.get("reason") or ""), payload
        plugin_env.uninstall_distribution(python, plugin_env.DISTRIBUTION)
