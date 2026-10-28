"""B21 — infrastructure adapter contract, discovery, authorization and freeze.

The tests exercise the platform registry directly (no Controller and no
science):

* discovery reads metadata/entrypoint bytes but never imports/executes the
  entrypoint;
* install/enable require a server-side authorization fact and an explicit
  trust decision;
* wrong versions, missing environments and capability conflicts are named
  rejections and never auto-replace an installed version;
* each task freezes the exact adapter id/version/descriptor digest, and an old
  task reads its frozen v1 back after v1.1.0 is installed and enabled;
* an in-process plugin is not a sandbox: isolation stays ``NOT_VERIFIED``.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from acp_services.contracts.adapters import (
    ADAPTER_SCHEMA,
    AdapterContractError,
    AdapterDescriptor,
    TrustLevel,
    descriptor_digest,
)
from acp_services.runtime.adapter_bootstrap import build_adapter_registry
from acp_services.runtime.adapter_registry import (
    AdapterAuthorization,
    AdapterCapabilityConflictError,
    AdapterEnvironmentMissingError,
    AdapterRegistry,
    AdapterRegistryError,
    AdapterUntrustedError,
    AdapterVersionRejectedError,
)

PLUGIN_BODY = '''"""Test infrastructure adapter; never executes on discovery."""
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

SIDE_EFFECT_BODY = '''"""Spy: touching the marker proves the module was executed."""
from __future__ import annotations

from pathlib import Path

Path(__file__).with_name("EXECUTED").write_text("executed\\n", encoding="utf-8")


def run(operation, payload):
    return {"executed": True}
'''

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _descriptor_document(
    *,
    adapter_id: str,
    version: str,
    capability: str,
    environment: list[str] | None = None,
    trust_level: str = TrustLevel.DISCOVER_ONLY,
    claims_process_isolation: bool = False,
) -> dict[str, object]:
    return {
        "descriptor_schema": ADAPTER_SCHEMA,
        "adapter_id": adapter_id,
        "version": version,
        "kind": "storage",
        "supported_schema": [ADAPTER_SCHEMA],
        "environment": environment or [],
        "resource_limits": {
            "max_cpu_seconds": 30,
            "max_memory_bytes": 33554432,
            "max_concurrency": 1,
            "max_io_bytes": 1048576,
        },
        "verification_fixture": "verification.json",
        "capabilities": [capability],
        "trust_level": trust_level,
        "entrypoint": {"module": "adapter", "attribute": "run"},
        "description": "Test infrastructure adapter.",
        "claims_process_isolation": claims_process_isolation,
    }


def _write_plugin(
    root: Path,
    name: str,
    *,
    adapter_id: str,
    version: str,
    capability: str = "storage.fixture",
    environment: list[str] | None = None,
    body: str | None = None,
    trust_level: str = TrustLevel.DISCOVER_ONLY,
    claims_process_isolation: bool = False,
    extra_fields: dict[str, object] | None = None,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = _descriptor_document(
        adapter_id=adapter_id,
        version=version,
        capability=capability,
        environment=environment,
        trust_level=trust_level,
        claims_process_isolation=claims_process_isolation,
    )
    if extra_fields:
        descriptor.update(extra_fields)
    (directory / "adapter.json").write_text(json.dumps(descriptor), encoding="utf-8")
    if body is None:
        content = PLUGIN_BODY.format(adapter_id=adapter_id, version=version)
    elif "{adapter_id" in body or "{version" in body:
        content = body.format(adapter_id=adapter_id, version=version)
    else:
        content = body
    (directory / "adapter.py").write_text(content, encoding="utf-8")
    (directory / "verification.json").write_text("{}\n", encoding="utf-8")
    return directory


def _registry(
    root: Path,
    *,
    trusted: tuple[str, ...],
    state_root: Path | None = None,
    authorized_principals: tuple[str, ...] = ("controller",),
) -> AdapterRegistry:
    return AdapterRegistry(
        plugin_root=root,
        state_root=state_root,
        trusted_adapter_ids=trusted,
        authorized_principals=authorized_principals,
        environment_probe=lambda requirement: (
            requirement != "env:ACP2_TEST_MISSING"
            or "ACP2_TEST_MISSING" in __import__("os").environ
        ),
    )


def _auth(
    action: str,
    adapter_id: str,
    version: str,
    digest: str,
    *,
    authorized_by: str = "controller",
    expires_at: str | None = None,
) -> AdapterAuthorization:
    return AdapterAuthorization(
        action=action,
        adapter_id=adapter_id,
        version=version,
        descriptor_digest=digest,
        authorized_by=authorized_by,
        issued_at="2026-10-23T00:00:00Z",
        expires_at=expires_at,
    )


def _install_and_enable(
    registry: AdapterRegistry, discovered, adapter_id: str, version: str
) -> None:
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


# ---------------------------------------------------------------------------
# Discovery is metadata only
# ---------------------------------------------------------------------------


def test_discovery_never_imports_or_executes_an_entrypoint(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    plugin = _write_plugin(
        root,
        "side-effect",
        adapter_id="acp.storage.sideeffect",
        version="1.0.0",
        body=SIDE_EFFECT_BODY,
    )
    registry = _registry(root, trusted=("acp.storage.sideeffect",))
    report = registry.discover()

    assert (plugin / "EXECUTED").exists() is False
    assert not [name for name in sys.modules if name.startswith("acp_adapter_")]
    by_key = {
        (adapter.descriptor.adapter_id, adapter.descriptor.version): adapter
        for adapter in report.adapters
    }
    metadata = by_key[("acp.storage.sideeffect", "1.0.0")].to_metadata()
    assert metadata["adapter_id"] == "acp.storage.sideeffect"
    assert metadata["executed"] is False
    assert report.to_dict()["execution"] == "none"

    item = by_key[("acp.storage.sideeffect", "1.0.0")]
    registry.install(
        "acp.storage.sideeffect",
        "1.0.0",
        authorization=_auth(
            "adapter.install",
            "acp.storage.sideeffect",
            "1.0.0",
            item.descriptor_digest,
        ),
    )
    registry.enable(
        "acp.storage.sideeffect",
        "1.0.0",
        authorization=_auth(
            "adapter.enable",
            "acp.storage.sideeffect",
            "1.0.0",
            item.descriptor_digest,
        ),
    )
    assert (plugin / "EXECUTED").exists() is False

    with pytest.raises(AdapterRegistryError) as excinfo:
        registry.invoke(
            "acp.storage.sideeffect",
            "1.0.0",
            "inventory",
            {},
            allow_execution=False,
        )
    assert excinfo.value.code == "execution_requires_explicit_allow"
    assert (plugin / "EXECUTED").exists() is False

    registry.invoke(
        "acp.storage.sideeffect",
        "1.0.0",
        "inventory",
        {},
        allow_execution=True,
    )
    assert (plugin / "EXECUTED").is_file()


def test_unknown_metadata_and_authorization_in_metadata_are_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "unknown-field",
        adapter_id="acp.storage.badmeta",
        version="1.0.0",
        extra_fields={"on_discover": "run", "authorization": "trust-me"},
    )
    registry = _registry(root, trusted=("acp.storage.badmeta",))
    report = registry.discover()
    assert all(adapter.origin == "builtin" for adapter in report.adapters)
    assert {row.code for row in report.rejected} == {"unknown_metadata_field"}


def test_descriptor_contract_rejects_science_and_isolation_claims() -> None:
    base = _descriptor_document(
        adapter_id="acp.storage.contract", version="1.0.0", capability="storage.ok"
    )
    with pytest.raises(AdapterContractError) as science:
        AdapterDescriptor.from_dict({**base, "capabilities": ["storage.nmr"]})
    assert science.value.code == "science_capability_forbidden"
    with pytest.raises(AdapterContractError) as isolation:
        AdapterDescriptor.from_dict({**base, "claims_process_isolation": True})
    assert isolation.value.code == "adapter_isolation_claim_rejected"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_install_and_enable_require_server_side_authorization(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "authz",
        adapter_id="acp.storage.authz",
        version="1.0.0",
    )
    registry = _registry(root, trusted=("acp.storage.authz",))
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    item = discovered[("acp.storage.authz", "1.0.0")]

    with pytest.raises(AdapterRegistryError) as required:
        registry.install("acp.storage.authz", "1.0.0", authorization=None)
    assert required.value.code == "adapter_authorization_required"

    with pytest.raises(AdapterRegistryError) as wrong_action:
        registry.install(
            "acp.storage.authz",
            "1.0.0",
            authorization=_auth(
                "adapter.enable",
                "acp.storage.authz",
                "1.0.0",
                item.descriptor_digest,
            ),
        )
    assert wrong_action.value.code == "adapter_authorization_invalid"

    with pytest.raises(AdapterRegistryError) as wrong_principal:
        registry.install(
            "acp.storage.authz",
            "1.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.authz",
                "1.0.0",
                item.descriptor_digest,
                authorized_by="client",
            ),
        )
    assert wrong_principal.value.code == "adapter_authorization_invalid"

    with pytest.raises(AdapterRegistryError) as expired:
        registry.install(
            "acp.storage.authz",
            "1.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.authz",
                "1.0.0",
                item.descriptor_digest,
                expires_at="2000-01-01T00:00:00Z",
            ),
        )
    assert expired.value.code == "adapter_authorization_expired"

    installed = registry.install(
        "acp.storage.authz",
        "1.0.0",
        authorization=_auth(
            "adapter.install",
            "acp.storage.authz",
            "1.0.0",
            item.descriptor_digest,
        ),
    )
    assert installed.installed_by == "controller"
    with pytest.raises(AdapterRegistryError) as enable_required:
        registry.enable("acp.storage.authz", "1.0.0", authorization=None)
    assert enable_required.value.code == "adapter_authorization_required"
    registry.enable(
        "acp.storage.authz",
        "1.0.0",
        authorization=_auth(
            "adapter.enable",
            "acp.storage.authz",
            "1.0.0",
            item.descriptor_digest,
        ),
    )
    assert registry.is_enabled("acp.storage.authz", "1.0.0")


# ---------------------------------------------------------------------------
# Version / environment / capability negatives
# ---------------------------------------------------------------------------


def test_wrong_version_is_rejected_without_auto_replacement(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _write_plugin(root, "v1", adapter_id="acp.storage.versioned", version="1.0.0")
    _write_plugin(root, "v2", adapter_id="acp.storage.versioned", version="2.0.0")
    registry = _registry(root, trusted=("acp.storage.versioned",))
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    _install_and_enable(registry, discovered, "acp.storage.versioned", "1.0.0")

    with pytest.raises(AdapterVersionRejectedError) as unknown:
        registry.enable(
            "acp.storage.versioned",
            "9.9.9",
            authorization=_auth(
                "adapter.enable",
                "acp.storage.versioned",
                "9.9.9",
                discovered[("acp.storage.versioned", "1.0.0")].descriptor_digest,
            ),
        )
    assert unknown.value.code == "adapter_version_not_installed"
    assert registry.active_version("acp.storage.versioned") == "1.0.0"

    incompatible = discovered[("acp.storage.versioned", "2.0.0")]
    with pytest.raises(AdapterVersionRejectedError) as major:
        registry.install(
            "acp.storage.versioned",
            "2.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.versioned",
                "2.0.0",
                incompatible.descriptor_digest,
            ),
        )
    assert major.value.code == "adapter_version_incompatible"
    assert registry.active_version("acp.storage.versioned") == "1.0.0"
    with pytest.raises(AdapterVersionRejectedError):
        registry.installed("acp.storage.versioned", "2.0.0")


def test_missing_environment_is_a_named_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACP2_TEST_MISSING", raising=False)
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "missing-env",
        adapter_id="acp.storage.missing-env",
        version="1.0.0",
        environment=["env:ACP2_TEST_MISSING"],
    )
    registry = AdapterRegistry(
        plugin_root=root,
        trusted_adapter_ids=("acp.storage.missing-env",),
    )
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    item = discovered[("acp.storage.missing-env", "1.0.0")]
    with pytest.raises(AdapterEnvironmentMissingError) as excinfo:
        registry.install(
            "acp.storage.missing-env",
            "1.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.missing-env",
                "1.0.0",
                item.descriptor_digest,
            ),
        )
    assert excinfo.value.code == "adapter_environment_missing"
    assert excinfo.value.missing_environment == ("env:ACP2_TEST_MISSING",)

    monkeypatch.setenv("ACP2_TEST_MISSING", "1")
    registry.install(
        "acp.storage.missing-env",
        "1.0.0",
        authorization=_auth(
            "adapter.install",
            "acp.storage.missing-env",
            "1.0.0",
            item.descriptor_digest,
        ),
    )


def test_capability_conflict_is_named_against_builtin_local(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "conflict",
        adapter_id="acp.storage.conflict",
        version="1.0.0",
        capability="scheduler.local",
    )
    registry = _registry(root, trusted=("acp.storage.conflict",))
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    item = discovered[("acp.storage.conflict", "1.0.0")]
    with pytest.raises(AdapterCapabilityConflictError) as excinfo:
        registry.install(
            "acp.storage.conflict",
            "1.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.conflict",
                "1.0.0",
                item.descriptor_digest,
            ),
        )
    assert excinfo.value.code == "adapter_capability_conflict"
    assert excinfo.value.capability == "scheduler.local"
    assert excinfo.value.owner == "platform.scheduler.local@1.0.0"


def test_untrusted_plugin_is_never_enabled(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _write_plugin(
        root,
        "untrusted",
        adapter_id="acp.storage.untrusted",
        version="1.0.0",
        trust_level=TrustLevel.TRUSTED_EXPLICIT,
    )
    registry = _registry(root, trusted=())  # self-claimed trust is not enough
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    item = discovered[("acp.storage.untrusted", "1.0.0")]
    assert item.effective_trust_level == TrustLevel.DISCOVER_ONLY
    with pytest.raises(AdapterUntrustedError) as excinfo:
        registry.install(
            "acp.storage.untrusted",
            "1.0.0",
            authorization=_auth(
                "adapter.install",
                "acp.storage.untrusted",
                "1.0.0",
                item.descriptor_digest,
            ),
        )
    assert excinfo.value.code == "adapter_untrusted"
    assert not registry.is_enabled("acp.storage.untrusted", "1.0.0")


# ---------------------------------------------------------------------------
# Per-task freeze / old-task read-back
# ---------------------------------------------------------------------------


def test_freeze_is_per_task_and_old_task_reads_frozen_v1_after_v2(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    state_root = tmp_path / "state"
    _write_plugin(root, "v1", adapter_id="acp.storage.inventory", version="1.0.0")
    _write_plugin(root, "v11", adapter_id="acp.storage.inventory", version="1.1.0")
    registry = _registry(
        root,
        trusted=("acp.storage.inventory",),
        state_root=state_root,
    )
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    _install_and_enable(registry, discovered, "acp.storage.inventory", "1.0.0")
    old_freeze = registry.freeze_for_task(
        "task-old", 1, adapters={"acp.storage.inventory": "1.0.0"}
    )
    old_adapter = old_freeze.adapter("acp.storage.inventory")
    assert old_adapter.version == "1.0.0"

    _install_and_enable(registry, discovered, "acp.storage.inventory", "1.1.0")
    new_freeze = registry.freeze_for_task("task-new", 1)
    new_adapter = new_freeze.adapter("acp.storage.inventory")
    assert new_adapter.version == "1.1.0"
    assert (
        new_adapter.descriptor_digest
        == discovered[("acp.storage.inventory", "1.1.0")].descriptor_digest
    )

    read_back = registry.read_task_freeze("task-old", 1)
    frozen = read_back.adapter("acp.storage.inventory")
    assert frozen.version == "1.0.0"
    assert frozen.descriptor_digest == old_adapter.descriptor_digest
    assert registry.verify_frozen_adapter(frozen).descriptor.version == "1.0.0"

    old_result = registry.invoke_frozen(frozen, "inventory", {}, allow_execution=True)
    new_result = registry.invoke(
        "acp.storage.inventory", "1.1.0", "inventory", {}, allow_execution=True
    )
    assert old_result["version"] == "1.0.0"
    assert new_result["version"] == "1.1.0"

    # A fresh registry over the same persistent state still reads the frozen
    # v1 task record and never substitutes the active v1.1.0.
    restarted = _registry(
        root,
        trusted=("acp.storage.inventory",),
        state_root=state_root,
    )
    restarted_frozen = restarted.read_task_freeze("task-old", 1).adapter("acp.storage.inventory")
    assert restarted_frozen.version == "1.0.0"
    assert restarted.active_version("acp.storage.inventory") == "1.1.0"


def test_install_is_idempotent_for_the_same_digest(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    _write_plugin(root, "same", adapter_id="acp.storage.same", version="1.0.0")
    registry = _registry(root, trusted=("acp.storage.same",))
    discovered = {
        (item.descriptor.adapter_id, item.descriptor.version): item
        for item in registry.discover().adapters
    }
    item = discovered[("acp.storage.same", "1.0.0")]
    authorization = _auth("adapter.install", "acp.storage.same", "1.0.0", item.descriptor_digest)
    first = registry.install("acp.storage.same", "1.0.0", authorization=authorization)
    second = registry.install("acp.storage.same", "1.0.0", authorization=authorization)
    assert first.descriptor_digest == second.descriptor_digest


# ---------------------------------------------------------------------------
# Built-ins / isolation / composition root / dependency direction
# ---------------------------------------------------------------------------


def test_builtin_local_lsf_storage_transfer_identity_are_described() -> None:
    registry = AdapterRegistry()
    ids = {item.descriptor.adapter_id for item in registry.installed_adapters()}
    assert {
        "platform.scheduler.local",
        "platform.scheduler.lsf",
        "platform.storage.local",
        "platform.transfer.local",
        "platform.identity.local",
    } <= ids
    report = registry.isolation_report()
    assert report["process_isolation"] == "NOT_VERIFIED"
    assert report["sandbox"] is False
    assert report["trusted_plugins_only"] is True


def test_composition_root_builds_registry_from_explicit_environment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugins"
    _write_plugin(root, "env", adapter_id="acp.storage.env", version="1.0.0")
    registry = build_adapter_registry(
        environ={
            "ACP_ADAPTER_PLUGIN_ROOT": str(root),
            "ACP_ADAPTER_STATE_ROOT": str(tmp_path / "state"),
            "ACP_ADAPTER_TRUSTED_IDS": "acp.storage.env,acp.storage.other",
            "ACP_ADAPTER_AUTHORIZED_PRINCIPALS": "controller,admin",
        },
        environment_probe=lambda requirement: True,
    )
    assert registry.plugin_root == root
    assert registry.trusted_adapter_ids == frozenset({"acp.storage.env", "acp.storage.other"})
    report = registry.discover()
    item = {
        (adapter.descriptor.adapter_id, adapter.descriptor.version): adapter
        for adapter in report.adapters
    }[("acp.storage.env", "1.0.0")]
    assert item.effective_trust_level == TrustLevel.TRUSTED_EXPLICIT


def test_registry_and_contract_do_not_import_acp() -> None:
    for relative in (
        "src/acp_services/contracts/adapters.py",
        "src/acp_services/runtime/adapter_registry.py",
        "src/acp_services/runtime/adapter_bootstrap.py",
    ):
        tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.split(".")[0] == "acp" for alias in node.names), relative
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "acp", relative


def test_descriptor_digest_is_stable_and_changes_with_version() -> None:
    base = _descriptor_document(
        adapter_id="acp.storage.digest", version="1.0.0", capability="storage.digest"
    )
    first = AdapterDescriptor.from_dict(base)
    second = AdapterDescriptor.from_dict(dict(base))
    assert descriptor_digest(first) == descriptor_digest(second)
    changed = AdapterDescriptor.from_dict({**base, "version": "1.0.1"})
    assert descriptor_digest(first) != descriptor_digest(changed)
