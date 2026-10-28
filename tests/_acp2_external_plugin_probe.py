"""Isolated-venv probe for the A-22 external plugin (not collected by pytest).

Run with the interpreter of a throw-away virtual environment that has the
fixture wheel installed.  ``PYTHONPATH`` supplies the platform source tree;
the fixture itself is imported only from the venv site-packages, and only
after an explicit enablement.

Modes:

* ``discover`` — metadata-only discovery; asserts the plugin module was not
  imported and prints the discovered names.
* ``inspect <native-input> <expected-output>`` — discovery, disabled refusal,
  explicit enable, capability invocation, output/units/atom-order comparison
  and integrity-digest verification.

The JSON payload is written to stdout as the final line; any assertion
failure exits non-zero with a traceback on stderr.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.plugin import PluginDisabledError, PluginHost, invoke_capability
from cccp.plugin.sdk import lock_plugin

DISTRIBUTION_NAME = "acp2-external-science-validator"
MODULE_NAME = "acp2_fixture_validator"
ENTRY_POINT_GROUP = "cccp.science_plugins"
ENTRY_POINT_NAME = "acp2_external_validator"
PLUGIN_NAME = "acp2_external.native_singlepoint"
CAPABILITY_NAME = "acp2_external_native_singlepoint"
MANIFEST_FILENAME = "cccp_science_plugin.json"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: probe.py discover | inspect <input> <expected>")
    mode = argv[1]
    if mode == "discover":
        payload = probe_discover()
    elif mode == "inspect":
        if len(argv) != 4:
            raise SystemExit("usage: probe.py inspect <native-input> <expected-output>")
        payload = probe_inspect(Path(argv[2]), Path(argv[3]))
    else:
        raise SystemExit(f"unknown probe mode {mode!r}")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def probe_discover() -> dict[str, object]:
    """Discover without importing the plugin module."""
    host = PluginHost.from_science_entry_points()
    catalog = host.catalog
    return {
        "ok": True,
        "discovered_names": list(catalog.names()),
        "entry_points_scanned": catalog.entry_points_scanned,
        "discovery_mode": catalog.discovery_mode,
        "module_imported": MODULE_NAME in sys.modules or f"{MODULE_NAME}.plugin" in sys.modules,
        "enabled_names": list(host.enabled_names()),
    }


def probe_inspect(input_path: Path, expected_path: Path) -> dict[str, object]:
    """Run the full disabled -> enabled -> invoked boundary in this venv."""
    for module in (MODULE_NAME, f"{MODULE_NAME}.plugin"):
        if module in sys.modules:
            raise AssertionError(f"{module} is already imported before discovery")

    host = PluginHost.from_science_entry_points()
    catalog = host.catalog
    if list(catalog.names()) != [PLUGIN_NAME]:
        raise AssertionError(f"discovered plugins are {list(catalog.names())!r}")
    if f"{MODULE_NAME}.plugin" in sys.modules:
        raise AssertionError("discovery imported the plugin module")
    status_before = catalog.status(PLUGIN_NAME, enabled=False)
    if status_before is None:
        raise AssertionError("discovered metadata is missing from the catalog")
    if status_before.enabled.state.value != "absent":
        raise AssertionError("plugin is not disabled by default")

    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=input_path),
    )

    disabled_error: str | None = None
    try:
        invoke_capability(host, PLUGIN_NAME, capability=CAPABILITY_NAME, request=request)
    except PluginDisabledError:
        disabled_error = "PluginDisabledError"
    else:
        raise AssertionError("invoking a disabled plugin did not raise PluginDisabledError")
    if f"{MODULE_NAME}.plugin" in sys.modules:
        raise AssertionError("a refused invocation imported the plugin module")

    host.enable(PLUGIN_NAME)
    if f"{MODULE_NAME}.plugin" in sys.modules:
        raise AssertionError("enablement eagerly imported the plugin module")
    metadata = catalog.get(PLUGIN_NAME)
    if metadata is None:
        raise AssertionError("enabled metadata disappeared")
    if metadata.is_trusted:
        raise AssertionError("the fixture must not promote its own trust level")

    result = invoke_capability(
        host,
        PLUGIN_NAME,
        capability=CAPABILITY_NAME,
        request=request,
    )
    if result.task is not TaskKind.SINGLEPOINT:
        raise AssertionError(f"result task is {result.task!r}")
    if not result.complete or result.status != "completed":
        raise AssertionError("enabled invocation did not return a complete result")

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    observed = _observed_result(result)
    if observed != expected:
        raise AssertionError(
            "invocation output does not match the fixed expected output:\n"
            f"observed={json.dumps(observed, indent=2, sort_keys=True)}\n"
            f"expected={json.dumps(expected, indent=2, sort_keys=True)}"
        )

    lock = lock_plugin(metadata)
    second_lock = lock_plugin(metadata)
    if lock.digest != second_lock.digest:
        raise AssertionError("plugin version digest is not deterministic")
    if len(lock.digest) != 64:
        raise AssertionError(f"unexpected plugin digest length: {lock.digest!r}")

    distribution = importlib.metadata.distribution(DISTRIBUTION_NAME)
    manifest = json.loads(distribution.read_text(MANIFEST_FILENAME))
    entry_points = [
        entry_point
        for entry_point in distribution.entry_points
        if entry_point.group == ENTRY_POINT_GROUP
    ]
    if [entry_point.name for entry_point in entry_points] != [ENTRY_POINT_NAME]:
        raise AssertionError(f"unexpected entry points: {entry_points!r}")
    if distribution.metadata["License"] != "MIT":
        raise AssertionError(
            f"installed license is {distribution.metadata['License']!r}, expected 'MIT'"
        )
    if distribution.version != manifest["version"]:
        raise AssertionError("installed version does not match the manifest")

    package = importlib.import_module(MODULE_NAME)
    package_path = Path(package.__file__).resolve().parent
    if not str(package_path).startswith(str(Path(sys.prefix).resolve())):
        raise AssertionError(f"plugin package is not installed in the venv: {package_path}")

    from acp2_fixture_validator.integrity import (
        distribution_digest,
        fixture_evidence_digest,
    )

    installed_digest = distribution_digest(package_path)
    installed_evidence = fixture_evidence_digest(package_path / "data")
    if installed_digest != manifest["distribution_digest"]:
        raise AssertionError(
            "installed distribution digest does not match the manifest "
            f"({installed_digest} != {manifest['distribution_digest']})"
        )
    if installed_evidence != manifest["fixture_evidence"][0]["digest"]:
        raise AssertionError("installed fixture evidence digest does not match the manifest")

    return {
        "ok": True,
        "discovered_names": list(catalog.names()),
        "entry_points_scanned": catalog.entry_points_scanned,
        "module_imported_after_discovery": False,
        "status_before": status_before.to_dict(),
        "status_after_enable": (
            host.status(PLUGIN_NAME).to_dict() if host.status(PLUGIN_NAME) else None
        ),
        "disabled_error": disabled_error,
        "module_imported_after_refusal": False,
        "preloaded_before_enable": False,
        "trust_level": metadata.trust_level.value,
        "is_trusted": metadata.is_trusted,
        "result": observed,
        "result_schema_version": result.schema_version,
        "plugin_lock_digest": lock.digest,
        "plugin_lock_digest_stable": True,
        "installed": {
            "version": distribution.version,
            "license": distribution.metadata["License"],
            "package_path": str(package_path),
            "entry_point_group": ENTRY_POINT_GROUP,
            "entry_point_name": entry_points[0].name,
            "entry_point_value": entry_points[0].value,
            "distribution_digest": installed_digest,
            "manifest_distribution_digest": manifest["distribution_digest"],
            "fixture_evidence_digest": installed_evidence,
            "manifest_fixture_evidence_digest": manifest["fixture_evidence"][0]["digest"],
        },
    }


def _observed_result(result: object) -> dict[str, object]:
    metadata = result.metadata
    return {
        "capability": metadata["capability"],
        "capability_schema": metadata["capability_schema"],
        "capability_schema_version": metadata["capability_schema_version"],
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
