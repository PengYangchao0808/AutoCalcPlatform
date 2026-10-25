#!/usr/bin/env python3.11
"""Example C (T43) — plugin example (xyz) load + invoke via the SDK.

Offline sample for the trusted-plugin pilot: construct a ``PluginHost`` with
the built-in example XYZ read-only parser candidate, enable it (plugins are
disabled by default), and invoke its capability through the SDK
``invoke_capability`` — consuming the built-in ``TaskRequest`` /
``TaskResult`` envelopes, with no second task chain and no platform import.

Covers the complete-input path (water XYZ), the deterministic version-digest
lock (sha256 over identity-bearing metadata), and the partial-output boundary
(truncated XYZ keeps the valid atom prefix, ``complete=False``, typed
``parse_failure``). No QC program and no browser/platform dependency.

Run from the repo root (offline, no external software):

    PYTHONPATH=src /usr/bin/python3.11 \
        tests/baseline/cccp-evidence/examples/example_c_plugin_xyz.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.calculation.results import ErrorKind
from cccp.plugin import (
    EXAMPLE_CANDIDATE,
    EXAMPLE_CAPABILITY_NAME,
    EXAMPLE_PLUGIN_NAME,
    PluginHost,
    invoke_capability,
    lock_plugin,
)

REPO = Path(__file__).resolve().parents[4]
COMPLETE_XYZ = REPO / "tests" / "fixtures" / "xyz_readonly_example.xyz"
TRUNCATED_XYZ = REPO / "tests" / "fixtures" / "xyz_readonly_truncated.xyz"


def request(path: Path) -> TaskRequest:
    return TaskRequest(task=TaskKind.SINGLEPOINT, structure=StructureInput(path=path))


def main() -> int:
    print(f"python: {sys.version.split()[0]} ({sys.executable})")

    # 1) host with the example candidate; plugins stay disabled until enabled
    host = PluginHost(candidates=(EXAMPLE_CANDIDATE,))
    print(f"[host]   catalog names={host.catalog.names()}")
    print(f"[host]   enabled before={host.is_enabled(EXAMPLE_PLUGIN_NAME)}")
    host.enable(EXAMPLE_PLUGIN_NAME)
    print(f"[host]   enabled after ={host.is_enabled(EXAMPLE_PLUGIN_NAME)}")

    # 2) version digest lock (deterministic identity for release/step/product)
    digest = lock_plugin(EXAMPLE_CANDIDATE.metadata)
    print(f"[digest] plugin={digest.name} version={digest.plugin_version}")
    print(f"[digest] sha256={digest.digest}")
    print(f"[digest] identity_fields(release)={digest.identity_fields(scope='release')}")

    # 3) invoke on complete input
    result = invoke_capability(
        host,
        EXAMPLE_PLUGIN_NAME,
        capability=EXAMPLE_CAPABILITY_NAME,
        request=request(COMPLETE_XYZ),
    )
    print(f"[invoke] fixture=tests/fixtures/{COMPLETE_XYZ.name}")
    print(
        f"[invoke] status={result.status} complete={result.complete} "
        f"symbols={result.symbols}"
    )
    print(
        f"[invoke] units={result.metadata['units']} "
        f"atom_order={result.metadata['atom_order']}"
    )
    print(f"[invoke] first coordinates row={tuple(result.coordinates[0])}")

    # 4) partial-output boundary: truncated file keeps the real valid prefix
    partial = invoke_capability(
        host,
        EXAMPLE_PLUGIN_NAME,
        capability=EXAMPLE_CAPABILITY_NAME,
        request=request(TRUNCATED_XYZ),
    )
    print(f"[invoke] fixture=tests/fixtures/{TRUNCATED_XYZ.name}")
    print(
        f"[invoke] status={partial.status} complete={partial.complete} "
        f"error_kind={partial.error_kind.value} symbols={partial.symbols}"
    )
    print(f"[invoke] errors={partial.errors}")

    # 5) assertions (offline self-check)
    assert result.status == "completed" and result.complete is True
    assert result.symbols == ("O", "H", "H")
    assert result.metadata["units"] == {"coordinates": "angstrom"}
    assert len(digest.digest) == 64
    assert digest.digest == lock_plugin(EXAMPLE_CANDIDATE.metadata).digest
    assert partial.status == "failed" and partial.complete is False
    assert partial.error_kind is ErrorKind.PARSE_FAILURE
    assert partial.symbols == ("O", "H")  # valid prefix, never fabricated
    print("EXAMPLE C: PASS (plugin load + invoke + partial boundary + assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
