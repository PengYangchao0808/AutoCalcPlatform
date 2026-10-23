"""B22 — adapter upgrade/rollback and third-party capability admission.

The tests cover the platform invariants B22 adds on top of B21:

* installing/enabling v2 never mutates a task attempt frozen on v1;
* uninstalling a version returns named errors for new execution while each
  task's frozen snapshot stays readable (and the historical read path does not
  resolve to "latest");
* importability never raises trust, and an unknown licence is registered as
  ``approved=False`` and refused for execution while remaining readable;
* a third-party capability is discovered/registered/admitted/frozen and run
  through the existing B18/CCCP binding path (no science logic here);
* the frozen H5 manifest and B25a packaging-request record keep their
  no-fake-PASS invariants.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from acp_services.contracts.adapters import (
    ADAPTER_SCHEMA,
    TrustLevel,
)
from acp_services.runtime.adapter_registry import (
    AdapterAuthorization,
    AdapterRegistry,
    AdapterRegistryError,
)
from acp_services.runtime.capability_registry import (
    CapabilityAuthorization,
    CapabilityLicenseError,
    CapabilityRegistry,
    CapabilityUntrustedError,
    discover_view_model,
    load_capability_schema,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_B22_EVIDENCE = _REPO_ROOT / "reports" / "acp2_implementation" / "bb45a46" / "B" / "B22"

_ADAPTER_BODY = '''"""B22 test infrastructure adapter; discovery never imports it."""
from __future__ import annotations

ADAPTER_ID = {adapter_id!r}
VERSION = {version!r}


def run(operation, payload):
    if operation == "inventory":
        return {{
            "adapter_id": ADAPTER_ID,
            "version": VERSION,
            "operation": operation,
            "science": False,
            "path": payload.get("path"),
        }}
    raise ValueError(operation)
'''


def _descriptor(adapter_id: str, version: str, capability: str = "storage.inventory") -> dict:
    return {
        "descriptor_schema": ADAPTER_SCHEMA,
        "adapter_id": adapter_id,
        "version": version,
        "kind": "storage",
        "supported_schema": [ADAPTER_SCHEMA],
        "environment": [],
        "resource_limits": {
            "max_cpu_seconds": 30,
            "max_memory_bytes": 33554432,
            "max_concurrency": 1,
            "max_io_bytes": 1048576,
        },
        "verification_fixture": "verification.json",
        "capabilities": [capability],
        "trust_level": TrustLevel.DISCOVER_ONLY,
        "entrypoint": {"module": "adapter", "attribute": "run"},
        "description": "B22 test infrastructure adapter.",
        "claims_process_isolation": False,
    }


def _write_adapter(
    root: Path,
    name: str,
    *,
    adapter_id: str,
    version: str,
    capability: str = "storage.inventory",
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "adapter.json").write_text(
        json.dumps(_descriptor(adapter_id, version, capability)), encoding="utf-8"
    )
    (directory / "adapter.py").write_text(
        _ADAPTER_BODY.format(adapter_id=adapter_id, version=version), encoding="utf-8"
    )
    (directory / "verification.json").write_text("{}\n", encoding="utf-8")
    return directory


def _auth(
    action: str,
    adapter_id: str,
    version: str,
    digest: str,
    *,
    authorized_by: str = "controller",
) -> AdapterAuthorization:
    return AdapterAuthorization(
        action=action,
        adapter_id=adapter_id,
        version=version,
        descriptor_digest=digest,
        authorized_by=authorized_by,
        issued_at="2026-10-23T00:00:00Z",
    )


def _registry(root: Path, state_root: Path, trusted: tuple[str, ...] = ("acp.storage.inventory",)):
    return AdapterRegistry(
        plugin_root=root,
        state_root=state_root,
        trusted_adapter_ids=trusted,
        environment_probe=lambda requirement: True,
    )


def _install_enable(registry: AdapterRegistry, discovered: dict, adapter_id: str, version: str):
    item = discovered[(adapter_id, version)]
    registry.install(
        adapter_id,
        version,
        authorization=_auth("adapter.install", adapter_id, version, item.descriptor_digest),
    )
    registry.enable(
        adapter_id,
        version,
        authorization=_auth("adapter.enable", adapter_id, version, item.descriptor_digest),
    )
    return item


# ---------------------------------------------------------------------------
# Adapter upgrade / rollback
# ---------------------------------------------------------------------------


def test_v2_never_overwrites_frozen_v1_and_uninstall_keeps_read_only_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    state_root = tmp_path / "state"
    v1_dir = _write_adapter(root, "v1", adapter_id="acp.storage.inventory", version="1.0.0")
    v2_dir = _write_adapter(root, "v2", adapter_id="acp.storage.inventory", version="1.1.0")
    registry = _registry(root, state_root)
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    v1 = _install_enable(registry, discovered, "acp.storage.inventory", "1.0.0")
    old_freeze = registry.freeze_for_task(
        "old-task", 1, adapters={"acp.storage.inventory": "1.0.0"}
    )
    old_frozen = old_freeze.adapter("acp.storage.inventory")

    v2 = _install_enable(registry, discovered, "acp.storage.inventory", "1.1.0")
    assert registry.active_version("acp.storage.inventory") == "1.1.0"
    new_freeze = registry.freeze_for_task("new-task", 1)
    new_frozen = new_freeze.adapter("acp.storage.inventory")

    # A running v1 attempt is identified by its frozen adapter, not by the
    # active version.  Installing/enabling v2 must not mutate it.
    read_back = registry.read_task_freeze("old-task", 1).adapter("acp.storage.inventory")
    assert read_back.to_dict() == old_frozen.to_dict()
    assert read_back.version == "1.0.0"
    assert registry.verify_frozen_adapter(read_back).descriptor.version == "1.0.0"
    assert (v1_dir / "adapter.py").stat().st_size == len(
        _ADAPTER_BODY.format(adapter_id="acp.storage.inventory", version="1.0.0").encode()
    )
    assert registry.invoke_frozen(read_back, "inventory", {}, allow_execution=True)["version"] == (
        "1.0.0"
    )
    assert (
        registry.invoke("acp.storage.inventory", "1.1.0", "inventory", {}, allow_execution=True)[
            "version"
        ]
        == "1.1.0"
    )

    # Uninstall v2 and remove its payload.  New execution fails with a named
    # error; the new task's snapshot remains readable; v1 still executes.
    receipt = registry.uninstall(
        "acp.storage.inventory",
        "1.1.0",
        authorization=_auth(
            "adapter.uninstall", "acp.storage.inventory", "1.1.0", v2.descriptor_digest
        ),
        remove_payload=True,
    )
    assert receipt["payload_removed"] is True
    assert receipt["active_after"] is None
    assert not v2_dir.exists()
    with pytest.raises(AdapterRegistryError) as removed:
        registry.invoke("acp.storage.inventory", "1.1.0", "inventory", {}, allow_execution=True)
    assert removed.value.code == "adapter_version_not_installed"
    with pytest.raises(AdapterRegistryError) as removed_frozen:
        registry.invoke_frozen(new_frozen, "inventory", {}, allow_execution=True)
    assert removed_frozen.value.code == "adapter_version_not_installed"

    v2_artifact = registry.read_frozen_artifact("new-task", 1, "acp.storage.inventory")
    assert v2_artifact.version == "1.1.0"
    assert v2_artifact.read_only is True
    assert v2_artifact.to_dict()["entrypoint_sha256"] == new_frozen.entrypoint_digest
    assert registry.invoke_frozen(old_frozen, "inventory", {}, allow_execution=True)["version"] == (
        "1.0.0"
    )

    # Uninstall v1 too: the historical v1 artifact is still readable, while a
    # new execution on the removed dependency fails clearly.
    registry.uninstall(
        "acp.storage.inventory",
        "1.0.0",
        authorization=_auth(
            "adapter.uninstall", "acp.storage.inventory", "1.0.0", v1.descriptor_digest
        ),
        remove_payload=True,
    )
    v1_artifact = registry.read_frozen_artifact("old-task", 1, "acp.storage.inventory")
    assert v1_artifact.version == "1.0.0"
    assert v1_artifact.read_only is True
    assert v1_artifact.to_dict()["entrypoint_sha256"] == old_frozen.entrypoint_digest
    with pytest.raises(AdapterRegistryError) as gone:
        registry.invoke_frozen(old_frozen, "inventory", {}, allow_execution=True)
    assert gone.value.code == "adapter_version_not_installed"

    # Persistence: a fresh registry over the same state root reads the same
    # frozen snapshots and never substitutes the removed (or active) version.
    restarted = _registry(root, state_root)
    assert restarted.read_frozen_artifact("old-task", 1, "acp.storage.inventory").version == "1.0.0"
    assert restarted.read_frozen_artifact("new-task", 1, "acp.storage.inventory").version == "1.1.0"
    assert restarted.active_version("acp.storage.inventory") is None


def test_uninstall_requires_authorization_and_named_errors(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    state_root = tmp_path / "state"
    _write_adapter(root, "v1", adapter_id="acp.storage.inventory", version="1.0.0")
    registry = _registry(root, state_root)
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    v1 = _install_enable(registry, discovered, "acp.storage.inventory", "1.0.0")
    with pytest.raises(AdapterRegistryError) as required:
        registry.uninstall("acp.storage.inventory", "1.0.0", authorization=None)
    assert required.value.code == "adapter_authorization_required"
    with pytest.raises(AdapterRegistryError) as wrong:
        registry.uninstall(
            "acp.storage.inventory",
            "1.0.0",
            authorization=_auth("adapter.uninstall", "acp.storage.inventory", "1.0.0", "0" * 64),
        )
    assert wrong.value.code == "adapter_authorization_invalid"
    with pytest.raises(AdapterRegistryError) as unknown:
        registry.uninstall(
            "acp.storage.inventory",
            "9.9.9",
            authorization=_auth("adapter.uninstall", "acp.storage.inventory", "9.9.9", "0" * 64),
        )
    assert unknown.value.code == "adapter_version_not_installed"
    registry.uninstall(
        "acp.storage.inventory",
        "1.0.0",
        authorization=_auth(
            "adapter.uninstall", "acp.storage.inventory", "1.0.0", v1.descriptor_digest
        ),
    )


# ---------------------------------------------------------------------------
# Third-party capability admission
# ---------------------------------------------------------------------------


def _capability_contract(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    schema = {
        "$id": "https://acp.local/acp2/science/capability.schema.json",
        "schema_version": 1,
        "required": [
            "schema",
            "schema_version",
            "capability_id",
            "implementation_id",
            "compatibility",
            "execution_semantics",
            "projections",
        ],
        "properties": {
            "schema": {},
            "schema_version": {},
            "capability_id": {},
            "implementation_id": {},
            "compatibility": {},
            "execution_semantics": {},
            "projections": {},
            "limitations": {},
        },
    }
    schema_path = directory / "capability.schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    view_model = {
        "$id": "https://acp.local/acp2/experience/view-model.schema.json",
        "schema": "acp2.experience.view-model.v1",
        "schema_version": "1.0.0",
        "required": ["snapshot_revision", "project_id", "read_only"],
        "properties": {
            "snapshot_revision": {},
            "project_id": {},
            "read_only": {},
        },
        "ui_dialect": {"version": "acp2.ui-subset.v1", "supported_keywords": ["type"]},
    }
    view_path = directory / "view-model.schema.json"
    view_path.write_text(json.dumps(view_model), encoding="utf-8")
    return schema_path, view_path


def _write_capability(root: Path) -> None:
    directory = root / "energy"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "schema": "acp2.science.capability",
        "schema_version": 1,
        "capability_id": "test.energy",
        "implementation_id": "cccp.calculation.tasks.singlepoint",
        "compatibility": {
            "declared_major": 1,
            "unknown_major_version": "reject_and_never_execute",
        },
        "execution_semantics": {"kind": "compute"},
        "projections": {"described": {"state": "present"}},
        "limitations": [],
    }
    binding = {
        "schema": "acp2.science.task_binding",
        "schema_version": 1,
        "selection": {"capability_id": "test.energy"},
        "binding_output": {
            "binding_metadata": {
                "resolved_task_kind": "singlepoint",
                "implementation_id": "cccp.calculation.tasks.singlepoint",
                "delegation_target": "cccp.calculation.run_singlepoint",
                "request_schema_version": 1,
                "result_schema_version": 1,
            }
        },
        "validation_layers": {
            "json_structure": {"owner": "contract"},
            "cccp_scientific_semantics": {"owner": "cccp"},
            "runtime_platform_constraints": {"owner": "runtime"},
        },
    }
    (directory / "capability.json").write_text(json.dumps(descriptor), encoding="utf-8")
    (directory / "task-binding.json").write_text(json.dumps(binding), encoding="utf-8")


def _capability_auth(action: str, digest: str, capability_id: str = "test.energy"):
    return CapabilityAuthorization(
        action=action,
        capability_id=capability_id,
        version="1.0.0",
        descriptor_digest=digest,
        authorized_by="controller",
        issued_at="2026-10-23T00:00:00Z",
    )


def test_capability_is_registered_admitted_frozen_and_run_through_cccp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "capabilities"
    _write_capability(root)
    schema_path, view_path = _capability_contract(tmp_path)
    state_root = tmp_path / "state"
    registry = CapabilityRegistry(
        capability_root=root,
        state_root=state_root,
        schema_path=schema_path,
        view_model_path=view_path,
        trusted_capabilities=["test.energy"],
    )
    report = registry.discover()
    assert not report.rejected
    item = report.capabilities[0]
    assert item.capability_id == "test.energy"
    assert item.discovery.task_kind == "singlepoint"
    assert report.view_model is not None
    assert "snapshot_revision" in report.view_model.required_fields

    registration = registry.register(
        "test.energy",
        authorization=_capability_auth("capability.register", item.descriptor_digest),
        license_state="approved",
        license_source="test://MIT",
    )
    assert registration.approved is True
    assert registration.trust_level == TrustLevel.TRUSTED_EXPLICIT

    freeze = registry.admit_for_task(
        "task-1",
        1,
        "test.energy",
        authorization=_capability_auth("capability.admit", item.descriptor_digest),
    )
    assert freeze.purpose == "execute"
    artifact = registry.read_frozen_artifact("task-1", 1)
    assert artifact.read_only is True
    assert artifact.descriptor["capability_id"] == "test.energy"

    calls: list[dict] = []

    def fake_run(request, context=None):
        calls.append({"task": request.task.value, "method": request.level.method})
        return {"status": "completed", "energy_hartree": -76.0}

    monkeypatch.setattr(
        "acp_services.runtime.capability_registry._resolve_run_function",
        lambda implementation_id, task_kind: fake_run,
    )
    form = {
        "workflow": "singlepoint",
        "structure": {
            "source": "test",
            "coordinates": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.93, 0.0, -0.25]],
            "symbols": ["O", "H", "H"],
            "role": "minimum",
        },
        "method": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
        "charge": 0,
        "multiplicity": 1,
        "resources": {"nproc": 1, "mem": "1GB"},
    }
    with pytest.raises(Exception) as explicit:
        registry.run_frozen("task-1", 1, form)
    assert getattr(explicit.value, "code", None) == "execution_requires_explicit_allow"
    result = registry.run_frozen("task-1", 1, form, allow_execution=True)
    assert result["status"] == "completed"
    assert calls == [{"task": "singlepoint", "method": "r2SCAN-3c"}]

    # Unregister removes the registration but not the task snapshot.
    registry.unregister(
        "test.energy",
        authorization=_capability_auth("capability.unregister", item.descriptor_digest),
    )
    with pytest.raises(Exception) as not_registered:
        registry.registered("test.energy")
    assert getattr(not_registered.value, "code", None) == "capability_not_registered"
    assert registry.read_frozen_artifact("task-1", 1).descriptor["capability_id"] == "test.energy"

    restarted = CapabilityRegistry(
        capability_root=root,
        state_root=state_root,
        schema_path=schema_path,
        authorized_principals=("controller",),
    )
    assert restarted.read_frozen_artifact("task-1", 1).read_only is True


def test_capability_trust_and_unknown_license_are_separate(tmp_path: Path) -> None:
    root = tmp_path / "capabilities"
    _write_capability(root)
    schema_path, view_path = _capability_contract(tmp_path)

    # Importability does not grant trust: an unlisted capability is discoverable
    # but cannot be registered.
    untrusted = CapabilityRegistry(
        capability_root=root,
        schema_path=schema_path,
        view_model_path=view_path,
        trusted_capabilities=[],
    )
    item = untrusted.discover().capabilities[0]
    assert item.effective_trust_level == TrustLevel.DISCOVER_ONLY
    with pytest.raises(CapabilityUntrustedError) as refused:
        untrusted.register(
            "test.energy",
            authorization=_capability_auth("capability.register", item.descriptor_digest),
            license_state="approved",
        )
    assert refused.value.code == "capability_untrusted"

    # Unknown licence: registration is metadata-only (approved=False); execute
    # admission is refused, read-only admission/read-back is allowed.
    registry = CapabilityRegistry(
        capability_root=root,
        state_root=tmp_path / "state",
        schema_path=schema_path,
        view_model_path=view_path,
        trusted_capabilities=["test.energy"],
    )
    item = registry.discover().capabilities[0]
    registration = registry.register(
        "test.energy",
        authorization=_capability_auth("capability.register", item.descriptor_digest),
        license_state="unknown",
    )
    assert registration.approved is False
    assert registration.to_record()["license_approved"] is False
    with pytest.raises(CapabilityLicenseError) as execute:
        registry.admit_for_task(
            "task-unknown",
            1,
            "test.energy",
            authorization=_capability_auth("capability.admit", item.descriptor_digest),
        )
    assert execute.value.code == "capability_license_unapproved"
    read_only = registry.admit_for_task(
        "task-unknown-read",
        1,
        "test.energy",
        authorization=_capability_auth("capability.admit", item.descriptor_digest),
        purpose="read_only",
    )
    assert read_only.purpose == "read_only"
    assert registry.read_frozen_artifact("task-unknown-read", 1).read_only is True
    with pytest.raises(Exception) as not_executable:
        registry.run_frozen("task-unknown-read", 1, {}, allow_execution=True)
    assert getattr(not_executable.value, "code", None) == "capability_not_admitted_for_execution"


def test_view_model_discovery_reads_ui_dialect_and_rejects_unknown_schema(
    tmp_path: Path,
) -> None:
    _, view_path = _capability_contract(tmp_path)
    discovery = discover_view_model(view_path)
    assert discovery.schema_id == "acp2.experience.view-model.v1"
    assert discovery.required_fields == ("snapshot_revision", "project_id", "read_only")
    assert discovery.ui_dialect_version == "acp2.ui-subset.v1"
    bad = tmp_path / "bad-view.json"
    bad.write_text(
        json.dumps({"$id": "https://example.invalid/x", "schema": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as mismatch:
        discover_view_model(bad)
    assert getattr(mismatch.value, "code", None) == "view_model_schema_id_mismatch"


def test_a_schema_validation_rejects_unknown_fields(tmp_path: Path) -> None:
    schema_path, _ = _capability_contract(tmp_path)
    schema = load_capability_schema(schema_path)
    valid = {
        "schema": "acp2.science.capability",
        "schema_version": 1,
        "capability_id": "test.energy",
        "implementation_id": "cccp.calculation.tasks.singlepoint",
        "compatibility": {
            "declared_major": 1,
            "unknown_major_version": "reject_and_never_execute",
        },
        "execution_semantics": {"kind": "compute"},
        "projections": {"described": {"state": "present"}},
        "limitations": [],
    }
    from acp_services.runtime.capability_registry import validate_descriptor_against_schema

    validate_descriptor_against_schema(valid, schema)
    with pytest.raises(Exception) as unknown:
        validate_descriptor_against_schema({**valid, "authorization": "trust-me"}, schema)
    assert getattr(unknown.value, "code", None) == "capability_descriptor_unknown_field"
    with pytest.raises(Exception) as major:
        validate_descriptor_against_schema(
            {**valid, "compatibility": dict(valid["compatibility"], declared_major=2)}, schema
        )
    assert getattr(major.value, "code", None) == "unknown_major_version"


# ---------------------------------------------------------------------------
# Frozen B22 evidence
# ---------------------------------------------------------------------------


def _load_optional(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"B22 evidence not generated in this checkout: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_h5_manifest_and_packaging_requests_keep_no_fake_pass_invariants() -> None:
    manifest = _load_optional(_B22_EVIDENCE / "h5-manifest.json")
    assert manifest["schema"] == "acp2.h5.manifest.v1"
    assert manifest["adapter"]["adapter_id"]
    assert manifest["adapter"]["sources"]
    assert manifest["adapter"]["descriptors"]
    assert manifest["notices"]
    assert manifest["adapter"]["license_approved"] is False  # no LICENSE/NOTICE in the bundle
    assert manifest["unknown_licenses"]
    assert all(row["approved"] is False for row in manifest["unknown_licenses"])
    assert all(
        row["license_state"] in {"unknown", "NOT_VERIFIED"} for row in manifest["unknown_licenses"]
    )
    assert manifest["new_dependencies"] == []
    classes = {row["class"] for row in manifest["dependency_classification"]}
    assert classes <= {"science-node", "control", "DSH", "browser"}
    assert {"control", "science-node", "DSH", "browser"} <= classes
    assert manifest["verification"]["unknown_license_approved"] is False
    assert manifest["boundaries"]["version_bump_performed"] is False
    assert manifest["boundaries"]["core_source_edits"] == []

    for step in manifest["rollback_steps"]:
        assert step["order"] >= 1
        assert step["command"].strip()
        assert "..." not in step["command"]
        assert step["expected"].strip()

    requests = _load_optional(_B22_EVIDENCE / "packaging-requests.json")
    assert requests["schema"] == "acp2.b22.packaging-requests.v1"
    assert requests["version_bump_performed"] is False
    assert requests["owner"] == "B25a"
    assert requests["entry_point_requests"]
    assert all(row["read"] for row in requests["reviewed_requests"])
    assert requests["dependency_sync"]["new_dependencies"] == []
    assert requests["dependency_sync"]["pyproject_changed_by_b22"] is False


def test_b22_evidence_has_no_core_source_edit() -> None:
    third_party = _load_optional(_B22_EVIDENCE / "infra_upgrade-third-party-capability.json")
    assert third_party["core_source_edits"] == []
    assert third_party["execution_label"].startswith(("REAL:", "NOT_VERIFIED:"))
    if third_party["run"]["status"] == "PASS":
        assert third_party["run"]["evidence_class"] == "REAL"
        assert third_party["run"]["energy_hartree"] is not None
    else:
        assert third_party["run"]["evidence_class"] == "NOT_VERIFIED"
        assert third_party["run"]["reason"]


def test_new_modules_do_not_import_acp() -> None:
    modules = [
        _REPO_ROOT / "src/acp_services/runtime/adapter_registry.py",
        _REPO_ROOT / "src/acp_services/runtime/capability_registry.py",
    ]
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name == "acp" or name.startswith("acp.") for name in imported), (
            f"{path} imports acp: {sorted(imported)}"
        )
    # The capability module must not import cccp at module import time; it
    # resolves the registered task core lazily inside _resolve_run_function.
    capability_source = modules[1].read_text(encoding="utf-8")
    assert "import cccp" not in capability_source.split("def _resolve_run_function")[0]
