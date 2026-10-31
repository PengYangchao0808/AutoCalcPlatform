# External ACP2 science plugin fixture (A-22a)

A minimal, independently installable distribution used to prove that a new
read-only ACP2 science capability can be discovered, enabled and invoked
**without editing CCCP core sources or the root `pyproject.toml`**.

The fixture is not part of the platform install. It is built and installed
only inside an isolated virtual environment by the A-22 verification.

## Boundary

| Item | Value |
|---|---|
| Distribution | `acp2-external-science-validator` 1.0.0 |
| Licence | MIT (`LICENSE`) |
| Entry-point group | `cccp.science_plugins` (version 1) |
| Entry point | `acp2_external_validator = acp2_fixture_validator.plugin:PLUGIN` |
| Manifest | `cccp_science_plugin.json` (installed into `.dist-info`) |
| Capability | `acp2_external_native_singlepoint`, `pure_analysis`, schema version 1 |
| Execution | In-process, read-only; no `cccp.calculation.tasks`, no subprocess, no network |
| Trust | `untrusted` + fixture evidence; installation/enablement never promotes it |

The capability reads the fixed native record
`fixtures/native_singlepoint.acp2n`, validates its schema, element symbols,
atom count, finite coordinates and a finite negative Hartree energy, and
returns the built-in `TaskResult` envelope with coordinates in angstrom and
the energy in hartree. Atom order is the input order (`atom_order`).
`fixtures/expected_output.json` is the frozen expected result.

## Build

The build environment provides `build` but not `wheel`, so this project uses
a standard-library-only PEP 517 backend (`build_backend.py`) with
`requires = []`:

```bash
python -m build --wheel --no-isolation --outdir "$PWD/dist" "$PWD"
# equivalent:
pip wheel --no-deps --no-index --no-build-isolation -w "$PWD/dist" "$PWD"
```

Both commands are offline. The backend writes
`acp2_external_science_validator-1.0.0.dist-info/cccp_science_plugin.json`
and recomputes the two integrity digests from the sources at build time, so a
stale checked-in manifest cannot claim a wrong digest. The wheel bytes are
deterministic for the same sources.

## Install, enable, invoke (isolated environment)

```bash
python -m venv --system-site-packages /tmp/acp2-plugin-venv
/tmp/acp2-plugin-venv/bin/pip install --no-deps --no-index \
  "$PWD/dist/acp2_external_science_validator-1.0.0-py3-none-any.whl"

# discovery is metadata-only; the plugin module is not imported
PYTHONPATH=/path/to/ACPServer/acp2-development/A/src \
  /tmp/acp2-plugin-venv/bin/python - <<'PY'
from pathlib import Path
from cccp.calculation.requests import StructureInput, TaskKind, TaskRequest
from cccp.plugin import PluginDisabledError, PluginHost, invoke_capability
from cccp.plugin.sdk import lock_plugin

host = PluginHost.from_science_entry_points()
name = "acp2_external.native_singlepoint"
capability = "acp2_external_native_singlepoint"
print(host.catalog.names())                       # discovered, still disabled
request = TaskRequest(
    task=TaskKind.SINGLEPOINT,
    structure=StructureInput(path=Path("fixtures/native_singlepoint.acp2n")),
)
try:
    invoke_capability(host, name, capability=capability, request=request)
except PluginDisabledError:
    print("refused while disabled")

host.enable(name)
result = invoke_capability(host, name, capability=capability, request=request)
print(result.to_dict())
print(lock_plugin(host.catalog.get(name)).digest)
PY
```

Disable by dropping the name from the host's enabled set (`host.disable`);
nothing in the manifest or `default_enabled` auto-enables a plugin.

## Integrity digests

`acp2_fixture_validator.integrity` is shipped in the wheel and reproduces
both values from the installed bytes:

* `distribution_digest(package_dir)` — sha256 over the package's `*.py`
  sources in sorted relative-path order, `__pycache__` excluded;
* `fixture_evidence_digest(data_dir)` — sha256 over the fixed fixture files
  keyed by file name.

Compare the manifest's `distribution_digest` with
`distribution_digest()` executed from the installed package to detect a
tampered or partially replaced install. This is evidence, not a sandbox: the
in-process host does not provide process, permission or queue isolation.
