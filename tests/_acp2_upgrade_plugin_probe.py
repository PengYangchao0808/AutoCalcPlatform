"""Isolated-venv probe for the constructed A-23 v1/v2 upgrade pair.

Modes:

* ``capture <native-input> <expected-output> <artifact-out>`` — discover the
  installed v1 distribution metadata-only, refuse a disabled invocation,
  explicitly enable and invoke it, verify the fixed expected output and write
  a labelled historical artifact.
* ``upgrade-check <artifact> <native-input> <expected-output>`` — read the v1
  artifact without rerunning, then discover/verify the installed v2
  distribution and invoke it explicitly.
* ``read-historical <artifact>`` — read the same artifact when no plugin
  distribution is installed.
* ``inspect`` — print the metadata-only catalog for the installed version.

The probe never imports the plugin module before an explicit enable plus
invoke, and it never writes outside the paths passed on the command line.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.calculation.results import TaskResult
from cccp.plugin import PluginDisabledError, PluginHost, PluginMetadata, invoke_capability
from cccp.plugin.sdk import lock_plugin

DISTRIBUTION_NAME = "acp2-upgrade-science-validator"
MODULE_NAME = "acp2_upgrade_fixture"
PLUGIN_NAME = "acp2_upgrade.native_singlepoint"
CAPABILITY_NAME = "acp2_upgrade_native_singlepoint"
ARTIFACT_SCHEMA = "acp2.plugin-upgrade.historical-artifact"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: probe.py capture|upgrade-check|read-historical|inspect ...")
    mode = argv[1]
    if mode == "capture":
        if len(argv) != 5:
            raise SystemExit("usage: probe.py capture <input> <expected> <artifact-out>")
        payload = probe_capture(Path(argv[2]), Path(argv[3]), Path(argv[4]))
    elif mode == "upgrade-check":
        if len(argv) != 5:
            raise SystemExit("usage: probe.py upgrade-check <artifact> <input> <expected>")
        payload = probe_upgrade_check(Path(argv[2]), Path(argv[3]), Path(argv[4]))
    elif mode == "read-historical":
        if len(argv) != 3:
            raise SystemExit("usage: probe.py read-historical <artifact>")
        payload = probe_read_historical(Path(argv[2]))
    elif mode == "inspect":
        payload = probe_inspect()
    else:
        raise SystemExit(f"unknown probe mode {mode!r}")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _module_imported() -> bool:
    return MODULE_NAME in sys.modules or f"{MODULE_NAME}.plugin" in sys.modules


def _assert_not_imported(stage: str) -> None:
    if _module_imported():
        raise AssertionError(f"the plugin module was imported during {stage}")


def _request(input_path: Path) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=input_path),
    )


def _observed_result(result: TaskResult) -> dict[str, object]:
    metadata = result.metadata
    return {
        "capability": metadata["capability"],
        "capability_schema": metadata["capability_schema"],
        "capability_schema_version": metadata["capability_schema_version"],
        "plugin_version": metadata["plugin_version"],
        "task": result.task.value,
        "status": result.status,
        "complete": result.complete,
        "symbols": list(result.symbols or ()),
        "coordinates": [list(row) for row in (result.coordinates or ())],
        "atom_order": list(metadata["atom_order"]),
        "energy_hartree": result.energy_hartree,
        "units": dict(metadata["units"]),
        "native_schema": metadata["native_schema"],
        "declared_atom_count": metadata["declared_atom_count"],
        "charge": metadata["charge"],
        "multiplicity": metadata["multiplicity"],
        "input_sha256": result.provenance.input_signature if result.provenance else None,
    }


def _expected(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return payload


def _artifact_payload(
    metadata: PluginMetadata, lock_digest: str, result: TaskResult
) -> dict[str, object]:
    capability = metadata.capabilities[0]
    return {
        "constructed": True,
        "label": "constructed A-23 upgrade-pair artifact; not a real QC result",
        "schema": ARTIFACT_SCHEMA,
        "plugin_name": PLUGIN_NAME,
        "plugin_version": metadata.version,
        "capability": capability.name,
        "capability_schema": capability.schema,
        "capability_schema_version": capability.schema_version,
        "plugin_lock_digest": lock_digest,
        "distribution_digest": metadata.distribution_digest,
        "trust_level": metadata.trust_level.value,
        "default_enabled": metadata.default_enabled,
        "result": result.to_dict(),
    }


def probe_capture(input_path: Path, expected_path: Path, artifact_path: Path) -> dict[str, object]:
    """Capture one v1 invocation as a labelled historical artifact."""
    _assert_not_imported("startup")
    host = PluginHost.from_science_entry_points()
    catalog = host.catalog
    if list(catalog.names()) != [PLUGIN_NAME]:
        raise AssertionError(f"discovered plugins are {list(catalog.names())!r}")
    _assert_not_imported("discovery")
    status_before = host.status(PLUGIN_NAME)
    if status_before is None:
        raise AssertionError("discovered metadata is missing from the catalog")
    if status_before.enabled.state.value != "absent":
        raise AssertionError("the plugin is not disabled by default")
    if status_before.verified.state.value != "not_verified":
        raise AssertionError("the untrusted fixture must not report verified")
    metadata = catalog.get(PLUGIN_NAME)
    if metadata is None:
        raise AssertionError("metadata disappeared")
    lock = lock_plugin(metadata)

    request = _request(input_path)
    try:
        invoke_capability(host, PLUGIN_NAME, capability=CAPABILITY_NAME, request=request)
    except PluginDisabledError:
        disabled_error = "PluginDisabledError"
    else:
        raise AssertionError("a disabled plugin was invoked")
    _assert_not_imported("disabled invocation")

    host.enable(PLUGIN_NAME)
    _assert_not_imported("enablement")
    result = invoke_capability(host, PLUGIN_NAME, capability=CAPABILITY_NAME, request=request)
    observed = _observed_result(result)
    if observed != _expected(expected_path):
        raise AssertionError(
            "v1 output does not match the fixed expected output:\n"
            f"observed={json.dumps(observed, indent=2, sort_keys=True)}"
        )
    artifact = _artifact_payload(metadata, lock.digest, result)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    artifact_bytes = artifact_path.read_bytes()
    return {
        "ok": True,
        "mode": "capture",
        "plugin_version": metadata.version,
        "capability_schema_version": metadata.capabilities[0].schema_version,
        "plugin_lock_digest": lock.digest,
        "distribution_digest": metadata.distribution_digest,
        "trust_level": metadata.trust_level.value,
        "default_enabled": metadata.default_enabled,
        "entry_points_scanned": catalog.entry_points_scanned,
        "status_before": status_before.to_dict(),
        "disabled_error": disabled_error,
        "module_imported_before_enable": False,
        "observed": observed,
        "artifact_path": str(artifact_path),
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "artifact_size_bytes": len(artifact_bytes),
    }


def _read_artifact(path: Path) -> tuple[dict[str, object], TaskResult, str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    if payload.get("constructed") is not True:
        raise AssertionError("the historical artifact must be labelled constructed")
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise AssertionError(f"unexpected artifact schema {payload.get('schema')!r}")
    result_payload = payload.get("result")
    if not isinstance(result_payload, dict):
        raise AssertionError("artifact is missing the TaskResult payload")
    _assert_not_imported("historical artifact read")
    result = TaskResult.from_dict(result_payload)
    if result.to_dict() != result_payload:
        raise AssertionError("historical TaskResult does not round-trip byte-for-value")
    return payload, result, hashlib.sha256(raw).hexdigest()


def probe_upgrade_check(
    artifact_path: Path, input_path: Path, expected_path: Path
) -> dict[str, object]:
    """Read the v1 artifact, then verify the installed v2 plugin explicitly."""
    artifact, v1_result, artifact_sha_before = _read_artifact(artifact_path)
    if v1_result.task is not TaskKind.SINGLEPOINT:
        raise AssertionError(f"historical task is {v1_result.task!r}")
    if v1_result.metadata.get("capability_schema_version") != artifact.get(
        "capability_schema_version"
    ):
        raise AssertionError("historical capability schema version drifted")
    if v1_result.provenance is None or v1_result.provenance.version != artifact.get(
        "plugin_version"
    ):
        raise AssertionError("historical provenance version drifted")

    host = PluginHost.from_science_entry_points()
    catalog = host.catalog
    if list(catalog.names()) != [PLUGIN_NAME]:
        raise AssertionError(f"discovered plugins are {list(catalog.names())!r}")
    _assert_not_imported("v2 discovery")
    metadata = catalog.get(PLUGIN_NAME)
    if metadata is None:
        raise AssertionError("v2 metadata disappeared")
    if metadata.version == artifact.get("plugin_version"):
        raise AssertionError("the installed plugin did not upgrade to a new version")
    lock = lock_plugin(metadata)
    if lock.digest == artifact.get("plugin_lock_digest"):
        raise AssertionError("the v2 lock digest equals the v1 digest")
    status_before = host.status(PLUGIN_NAME)
    if status_before is None or status_before.enabled.state.value != "absent":
        raise AssertionError("v2 was auto-enabled from default_enabled metadata")

    request = _request(input_path)
    try:
        invoke_capability(host, PLUGIN_NAME, capability=CAPABILITY_NAME, request=request)
    except PluginDisabledError:
        disabled_error = "PluginDisabledError"
    else:
        raise AssertionError("a disabled v2 plugin was invoked")
    _assert_not_imported("v2 disabled invocation")

    host.enable(PLUGIN_NAME)
    _assert_not_imported("v2 enablement")
    result = invoke_capability(host, PLUGIN_NAME, capability=CAPABILITY_NAME, request=request)
    observed = _observed_result(result)
    if observed != _expected(expected_path):
        raise AssertionError(
            "v2 output does not match the fixed expected output:\n"
            f"observed={json.dumps(observed, indent=2, sort_keys=True)}"
        )
    artifact_sha_after = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if artifact_sha_before != artifact_sha_after:
        raise AssertionError("reading the v1 artifact rewrote its bytes")
    return {
        "ok": True,
        "mode": "upgrade-check",
        "v1_artifact_readable": True,
        "v1_result_roundtrip": True,
        "v1_artifact_sha256_before": artifact_sha_before,
        "v1_artifact_sha256_after": artifact_sha_after,
        "v1_artifact_unchanged": True,
        "v1_plugin_version": artifact.get("plugin_version"),
        "v1_capability_schema_version": artifact.get("capability_schema_version"),
        "v1_plugin_lock_digest": artifact.get("plugin_lock_digest"),
        "v2_plugin_version": metadata.version,
        "v2_capability_schema_version": metadata.capabilities[0].schema_version,
        "v2_plugin_lock_digest": lock.digest,
        "v2_distribution_digest": metadata.distribution_digest,
        "v2_trust_level": metadata.trust_level.value,
        "v2_default_enabled": metadata.default_enabled,
        "status_before_enable": status_before.to_dict(),
        "disabled_error": disabled_error,
        "module_imported_during_history_read": False,
        "module_imported_before_enable": False,
        "observed_v2": observed,
    }


def probe_read_historical(artifact_path: Path) -> dict[str, object]:
    """Read the v1 artifact after the v2 distribution was uninstalled."""
    artifact, result, artifact_sha = _read_artifact(artifact_path)
    host = PluginHost.from_science_entry_points()
    names = list(host.catalog.names())
    return {
        "ok": True,
        "mode": "read-historical",
        "v1_artifact_readable": True,
        "v1_result_roundtrip": True,
        "artifact_sha256": artifact_sha,
        "plugin_version": artifact.get("plugin_version"),
        "capability_schema_version": artifact.get("capability_schema_version"),
        "task": result.task.value,
        "discovered_names": names,
        "module_imported": _module_imported(),
    }


def probe_inspect() -> dict[str, object]:
    """Print the metadata-only catalog for whatever version is installed."""
    host = PluginHost.from_science_entry_points()
    catalog = host.catalog
    statuses = [status.to_dict() for status in host.catalog_status()]
    return {
        "ok": True,
        "mode": "inspect",
        "discovered_names": list(catalog.names()),
        "entry_points_scanned": catalog.entry_points_scanned,
        "module_imported": _module_imported(),
        "statuses": statuses,
    }


if __name__ == "__main__":
    sys.exit(main(sys.argv))
