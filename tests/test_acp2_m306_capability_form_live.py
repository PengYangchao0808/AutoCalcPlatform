"""M3-06 — the real discovery → form → preflight → view-model chain.

This is the HTTP layer of the M3-06 test plan (the zero-dependency Node suite
covers the pure modules; ``tests/test_acp2_ui_subset.py`` covers the frozen
contract documents and the read-only route):

* a real FastAPI Controller (``acp.api.server.create_app``) served by a real
  uvicorn socket;
* a real ``SciencePluginRegistry`` over an installed-looking fixture
  distribution that ships ``cccp_ui_form.json`` in its ``.dist-info`` — the
  third-party form is read metadata-only, exactly like the plugin manifest;
* the REAL Workbench modules (``resource-store``, ``ui_subset``,
  ``science-query``, ``capability-form``) run in Node with a REAL ``fetch``
  transport;
* discovery → form → values → ``POST /api/v2/science/preflight`` → view model
  → proposal provenance (descriptor/form/subset digests + human edits);
* after the run the capability is disabled in the ledger and the same HTTP
  surface projects it ``read_only`` with the frozen descriptor digest, while
  discovery no longer lists it.

Nothing here starts QC, LSF, a model, or a task; no task is created.
Skipped when Node (>= 18) or the API extras are unavailable.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NODE_DRIVER = _REPO_ROOT / "tests/fixtures/acp2/m306_capability_form_live.js"
_FIXTURE_FORM = _REPO_ROOT / "tests/fixtures/acp2/science/external_plugin/cccp_ui_form.json"
_STORE_MODULE = "frontend/js/client/resource-store.js"
_EXPERIENCE_MODULE = "frontend/js/workbench/contracts/experience_contract.js"
_SUBSET_MODULE = "frontend/js/workbench/contracts/ui_subset.js"
_SCIENCE_MODULE = "frontend/js/client/science-query.js"
_FEATURE_MODULE = "frontend/js/workbench/features/capability-form.js"

_DISTRIBUTION = "acp2-external-science-validator"
_PLUGIN = "acp2_external.native_singlepoint"
_CAPABILITY = "acp2_external_native_singlepoint"
_XYZ = "3\nH2O\nO 0 0 0\nH 0 0 0.96\nH 0 0.93 -0.24\n"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _node_binary() -> str | None:
    override = os.environ.get("ACP2_NODE")
    if override and Path(override).is_file():
        return override
    return shutil.which("node")


class _FakeEntryPoint:
    group = "cccp.science_plugins"

    def __init__(self, distribution: Any) -> None:
        self.name = "acp2_external_validator"
        self.value = "acp2_fixture_validator.plugin:PLUGIN"
        self.dist = distribution
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        raise AssertionError("discovery must never import a plugin module")


def _manifest(digest: str | None) -> dict[str, Any]:
    return {
        "name": _PLUGIN,
        "distribution": _DISTRIBUTION,
        "version": "1.0.0",
        "api": {"api_version": 1, "compatible_range": ">=1,<2", "schema_version": 1},
        "capabilities": [
            {
                "name": _CAPABILITY,
                "schema": "acp2.science.external.native_singlepoint",
                "schema_version": 1,
                "kind": "pure_analysis",
                "implementation_id": "acp2_fixture.validator.native_singlepoint",
                "delegates_to_task_kind": None,
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
        "license": "MIT",
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


def _install_fixture_distribution(root: Path) -> importlib.metadata.PathDistribution:
    """Materialise an installed distribution that ships the fixture UI form."""
    site = root / "site"
    package = site / "acp2_fixture_validator"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (package / "plugin.py").write_text("PLUGIN = object()\n", encoding="utf-8")
    (package / "data").mkdir(exist_ok=True)
    (package / "data" / "record.json").write_text('{"schema": "record"}\n', encoding="utf-8")

    dist_info = site / "acp2_external_science_validator-1.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acp2-external-science-validator\nVersion: 1.0.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[cccp.science_plugins]\nacp2_external_validator = acp2_fixture_validator.plugin:PLUGIN\n",
        encoding="utf-8",
    )
    # The third-party form ships next to the manifest, in the dist-info.  It is
    # deliberately outside the payload digest: shipping a UI form never changes
    # the scientific distribution identity.
    (dist_info / "cccp_ui_form.json").write_bytes(_FIXTURE_FORM.read_bytes())
    (dist_info / "cccp_science_plugin.json").write_text(
        json.dumps(_manifest(None)), encoding="utf-8"
    )
    payload_files = sorted(
        path.relative_to(site).as_posix()
        for path in package.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    record = [f"{name},," for name in payload_files]
    record.append(f"{dist_info.name}/RECORD,,")
    (dist_info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    distribution = importlib.metadata.PathDistribution(dist_info)

    from acp_services.runtime.science_plugins import compute_distribution_digest

    computed = compute_distribution_digest(distribution, declared=None)
    manifest = _manifest(computed.digest)
    (dist_info / "cccp_science_plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Make the installation visible to ``importlib.metadata`` exactly like a
    # ``pip install`` would: the platform reads the form metadata-only through
    # the real distribution lookup, not through an injected shortcut.
    if str(site) not in sys.path:
        sys.path.insert(0, str(site))
    importlib.invalidate_caches()
    return distribution


def _enabled_registry(root: Path) -> Any:
    from acp_services.runtime.capability_registry import CapabilityAuthorization
    from acp_services.runtime.science_plugins import SciencePluginRegistry

    distribution = _install_fixture_distribution(root)
    entry_point = _FakeEntryPoint(distribution)
    registry = SciencePluginRegistry(
        state_root=root / "state",
        trusted_capabilities=(_CAPABILITY,),
        entry_points=[entry_point],
    )
    registry.scan()
    record = registry.installed_capability(_CAPABILITY)
    registry.enable(
        _CAPABILITY,
        authorization=CapabilityAuthorization(
            action="capability.enable",
            capability_id=record.capability_id,
            version=record.version,
            descriptor_digest=record.descriptor_digest,
            authorized_by="controller",
            issued_at="2026-10-27T00:00:00Z",
        ),
    )
    assert entry_point.load_calls == 0, "enablement must not import the plugin module"
    return registry


@pytest.fixture()
def live_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, Any], None, None]:
    if _node_binary() is None:
        pytest.skip("node is not available for the M3-06 real-HTTP layer")
    pytest.importorskip("httpx")
    uvicorn = pytest.importorskip("uvicorn")

    run_root = tmp_path / "run-root"
    run_root.mkdir()
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    monkeypatch.setenv("ACP_POLL_INTERVAL", "1")
    monkeypatch.delenv("ACP_SERVER_PROJECT", raising=False)

    from acp.api import capability_forms
    from acp.api.server import create_app

    capability_forms.clear_form_cache()
    app = create_app(run_root=run_root, max_running=1)
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="m306-controller", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(600):
        if server.started:
            break
        if not thread.is_alive():
            raise AssertionError("uvicorn exited before startup")
        time.sleep(0.05)
    else:  # pragma: no cover - startup failure
        raise AssertionError("uvicorn did not start within 60s")

    import httpx

    for _ in range(200):
        try:
            probe = httpx.get(base_url + "/api/v1/projects", timeout=2.0)
            if probe.status_code < 500:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:  # pragma: no cover - readiness failure
        raise AssertionError("the Controller never answered /api/v1/projects")

    manager = app.state.job_manager
    registry = _enabled_registry(tmp_path)
    # The lifespan built its own (empty) registry; the test process installs the
    # fixture distribution and hands over the real enabled ledger.
    app.state.science_plugin_registry = registry
    app.state.science_capability_catalog = None
    for legacy in ("science_capability_descriptors", "science_capability_domain"):
        if hasattr(app.state, legacy):
            delattr(app.state, legacy)
    try:
        yield {
            "app": app,
            "base_url": base_url,
            "project": str(manager.default_project_id),
            "run_root": run_root,
            "registry": registry,
            "httpx": httpx,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=30.0)
        capability_forms.clear_form_cache()
        # The fixture distribution must not leak into any other test's
        # environment discovery (it looks exactly like an installed plugin).
        site = tmp_path / "site"
        while str(site) in sys.path:
            sys.path.remove(str(site))
        importlib.invalidate_caches()


def _scenario(controller: dict[str, Any]) -> dict[str, Any]:
    from acp.api import capability_forms

    plugin_form = json.loads(_FIXTURE_FORM.read_text(encoding="utf-8"))
    return {
        "repo_root": str(_REPO_ROOT),
        "base_url": controller["base_url"],
        "project_id": controller["project"],
        "capability_id": _CAPABILITY,
        "subset_version": capability_forms.UI_SUBSET_VERSION,
        "subset_digest": capability_forms.subset_digest(),
        "form_digest": plugin_form["form_digest"],
        "structure_text": _XYZ,
        "expected_workflow": "singlepoint",
        "expected_bindings": [
            "task.charge",
            "task.multiplicity",
            "task.resources.nproc",
            "task.structure.source",
        ],
        "expected_edits": [
            "task.charge",
            "task.resources.nproc",
            "task.structure.source",
        ],
        "store_module": _STORE_MODULE,
        "experience_module": _EXPERIENCE_MODULE,
        "subset_module": _SUBSET_MODULE,
        "science_module": _SCIENCE_MODULE,
        "feature_module": _FEATURE_MODULE,
    }


def test_third_party_form_discovery_preflight_view_model_over_real_http(
    live_controller: dict[str, Any], tmp_path: Path
) -> None:
    controller = live_controller
    scenario_path = tmp_path / "m306-scenario.json"
    scenario_path.write_text(json.dumps(_scenario(controller)), encoding="utf-8")

    node = _node_binary()
    assert node is not None
    completed = subprocess.run(
        [node, str(_NODE_DRIVER), str(scenario_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    raw = completed.stdout.strip()
    assert raw, f"node driver produced no output: {completed.stderr}"
    payload = json.loads(raw)
    assert payload.get("ok") is True, json.dumps(payload, indent=2)[-4000:]

    by_name = {step["name"]: step for step in payload["steps"]}
    for name in (
        "discovery.capability_forms",
        "form.values",
        "preflight",
        "view_model",
        "discovery.capabilities",
        "descriptor_cross_check",
    ):
        assert by_name[name]["ok"] is True, name
    view_model = by_name["view_model"]["value"]
    assert view_model["plan_digest"].startswith("sha256:")
    assert view_model["descriptor_digest"].startswith("sha256:")
    assert "task.structure.source=structure.source" in view_model["fields"]
    edits = {edit["field_path"]: edit for edit in view_model["human_edits"]}
    assert set(edits) == {
        "task.structure.source",
        "task.charge",
        "task.resources.nproc",
    }
    assert "form_digest=" in edits["task.charge"]["note"]

    # ------------------------------------------------------------------
    # Lifecycle: after disable the same capability is read-only history.
    # ------------------------------------------------------------------
    registry = controller["registry"]
    record = registry.enablement(_CAPABILITY, "1.0.0")
    assert record is not None and record.enabled is True
    from acp_services.runtime.capability_registry import CapabilityAuthorization

    registry.disable(
        _CAPABILITY,
        version="1.0.0",
        authorization=CapabilityAuthorization(
            action="capability.disable",
            capability_id=record.capability_id,
            version=record.version,
            descriptor_digest=record.descriptor_digest,
            authorized_by="controller",
            issued_at="2026-10-27T00:00:00Z",
        ),
    )

    http = controller["httpx"]
    params = {"project_id": controller["project"]}
    forms_after = http.get(
        controller["base_url"] + "/api/v2/science/capability-forms",
        params=params,
        timeout=30.0,
    )
    assert forms_after.status_code == 200, forms_after.text
    body = forms_after.json()
    assert _CAPABILITY not in {entry["capability_id"] for entry in body["forms"]}
    read_only = {entry["capability_id"]: entry for entry in body["read_only"]}
    assert read_only[_CAPABILITY]["state"] == "read_only"
    assert read_only[_CAPABILITY]["descriptor_digest"] == record.descriptor_digest
    assert read_only[_CAPABILITY]["reason"] == "capability_not_active"

    capabilities_after = http.get(
        controller["base_url"] + "/api/v2/science/capabilities",
        params=params,
        timeout=30.0,
    )
    assert capabilities_after.status_code == 200, capabilities_after.text
    ids = {
        entry["capability_id"]
        for entry in capabilities_after.json()["capabilities"]["descriptors"]["capabilities"]
    }
    assert _CAPABILITY not in ids

    # Read-only projection is a pure read: no job was created by any step.
    counts = controller["app"].state.job_manager.store.counts()
    assert counts.get("running", 0) == 0
    assert counts.get("queued", 0) == 0
