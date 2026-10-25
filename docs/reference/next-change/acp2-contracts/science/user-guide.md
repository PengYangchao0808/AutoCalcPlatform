# ACP2 standalone science SDK and plugin guide

Status: **normative for the standalone `cccp` science API** (A-28 / GAP-11).
This guide documents only what can be reproduced from an installed `cccp`
distribution. It is deliberately **separate** from platform-controlled
research: platform scenarios, job ids, scheduler state and signoffs are not
part of this surface and stay `NOT_VERIFIED` here.

Every command in this guide was executed from a clean interpreter built from
the wheel described below, and its real output was captured. The verification
run is:

```
$ACP2_EVIDENCE/A/A-28/20261023T044051Z/
  logs/                     one file per documented command
  harness-absent/           result.json for the missing-wheel run
  harness-present/          result.json + per-example stdout/stderr
  wheel/                    local candidate cccp wheel (deterministic)
```

The run id above is the actual evidence run; substitute your own evidence
directory when re-running. The outputs quoted here are verbatim excerpts from
`logs/` (long JSON bodies are abbreviated with an explicit note, never
invented). In output blocks, `$VENV`, `$DEMO_BIN` and `$TMPDIR` abbreviate
ephemeral verification paths; the literal paths are in the raw `logs/` files.

> **No B25a artifact exists today.** The wheel used for verification is a
> deterministic **local candidate** built from `src/cccp` (`manual_zip_from_src`,
> sha256 `78d0e622…`). It is not a B25a release candidate and is not a platform
> build. B25a, the platform release manifest and `pip install` of the published
> role wheel remain `NOT_VERIFIED`.

There is no product CLI. Standalone science is imported as `cccp…`; the only
shipped executable entry point on this surface is the acceptance harness
`scripts/acceptance/acp2_science.py`. The `cccp` package has no `-m` module
entry point, and none is documented here.

---

## 1. Install

### 1.1 Published role wheel — NOT_VERIFIED

The intended install is the `cccp` role wheel from the B25a release, following
`packaging/README.md`:

```bash
python -m venv /absolute/path/to/science-env
/absolute/path/to/science-env/bin/python -m pip install \
  --find-links /absolute/path/to/release 'cccp==<manifest-version>'
```

This command **was not executed**: no B25a artifact exists. It is therefore
`NOT_VERIFIED`. Do not treat the block above as evidence that a wheel was
published or installed. A real B25a wheel built before the B-owned package-data
patch would also omit the packaged contract corpus and fail section 2.

### 1.2 Local candidate wheel — verified clean-environment flow

The verification environment is `venv --without-pip` (this host has no `wheel`
module and no published artifact), with the wheel extracted into its
`site-packages` and third-party dependencies supplied through a dep-only
`PYTHONPATH`. These commands were executed:

```bash
export WHEEL="$EVIDENCE/wheel/cccp-0.2.0-py3-none-any.whl"
export VENV="$(mktemp -d)/venv"
python -m venv --without-pip "$VENV"
SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
"$VENV/bin/python" -m zipfile -e "$WHEEL" "$SITE"
export PY="$VENV/bin/python"
```

Real output (interpreter and wheel):

```
3.11.13 (main, Jun  1 2026, 00:00:00) [GCC 11.5.0 20240719 (Red Hat 11.5.0-14)]
78d0e622014d9ab478b870c01ebbb26f07358d419514d78a795a8109193796f1  .../cccp-0.2.0-py3-none-any.whl
```

The digest is reproducible: the wheel is built by the checked-in
`tests/test_acp2_science_installed_package.py::_build_local_candidate` (a
deterministic manual zip, no network). A wheel built by any other builder may
have a different digest even with an identical code payload.

The local candidate carries no dependency metadata, so the clean interpreter
gets the declared science dependencies from a **dep-only** path. This path is
computed from the host runner and never contains the repository source tree:

```bash
export DEPS=$(python - <<'PY'
import importlib.util
from pathlib import Path
roots = []
for name in ("numpy", "scipy", "rdkit"):
    spec = importlib.util.find_spec(name)
    if spec and spec.origin:
        roots.append(str(Path(spec.origin).resolve().parents[1]))
print(":".join(dict.fromkeys(roots)))
PY
)
export PYTHONPATH="$DEPS"
```

The computed value is the user site-packages directory that provides
numpy/scipy/rdkit; the exact verification-host value is recorded in
`logs/install-env.txt`.

`PYTHONPATH` directories do not process `.pth` files, so an editable install
of `acp`/`cccp` in the runner cannot leak into this interpreter. A wheel built
and installed by B25a with declared dependencies does not need this workaround;
that flow is `NOT_VERIFIED`.

---

## 2. Import and discovery without any QC software

Import and capability discovery are pure Python and never look for ORCA, xTB,
CREST, CENSO or any subprocess. This is the software-free baseline: it must
work on a machine with no computational-chemistry software installed.

```bash
"$PY" - <<'PY'
import cccp
from cccp.analysis.research import contracts
from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.capabilities.domains import domain_registry

print("cccp_version:", cccp.__version__)
print("cccp_file:", cccp.__file__)
print("contract_count:", len(contracts.list_contracts()))
print("contract_root:", contracts.contract_root())
print("builtin_capabilities:", list(builtin_registry().capability_ids))
print("domain_capability_count:", len(domain_registry().capability_ids))
PY
```

Real output:

```
cccp_version: 0.2.0
cccp_file: $VENV/lib64/python3.11/site-packages/cccp/__init__.py
contract_count: 14
contract_root: $VENV/lib/python3.11/site-packages/cccp/analysis/research/contracts
builtin_capabilities: ['electronic.energy', 'geometry.optimize', 'vibration.analyze', 'vibration.compute']
domain_capability_count: 7
```

The four built-in capabilities are the seeded compute/analysis surface:
`electronic.energy`, `geometry.optimize`, `vibration.compute` and the pure
analysis `vibration.analyze`. The packaged 14-file contract corpus is readable
through `contract_root()`, `list_contracts()` and `read_contract()`, without a
repository checkout and without network access. Importing `cccp` performs no
I/O beyond module loading and starts nothing.

**Discovery does not execute.** A capability id that is not registered reports
an `unknown_implementation` query result; it is never silently mapped to
another task. Execution is the separate step in section 4.

---

## 3. Configuring software

Software resolution is first-hit-wins, in this order:

1. explicit `executables.<name>.path` from the resolved config;
2. `CONFSEARCH_<NAME>_PATH` (for ORCA, `CONFSEARCH_ORCA_PATH`);
3. `PATH` (plus the current interpreter's directory);
4. legacy fallback locations;
5. exactly one unambiguous install under conventional layouts.

A configured path is used only when it is an executable file. A *nonexistent*
pin is ignored and resolution falls through — this is why an execution probe
must pin a strict context rather than trust a bare name.

```bash
DEMO_BIN=$(mktemp -d)
printf '#!/bin/sh\nexit 0\n' > "$DEMO_BIN/orca"
chmod +x "$DEMO_BIN/orca"
"$PY" - "$DEMO_BIN/orca" <<'PY'
import os
import sys
from cccp.software import get_configured_path, resolve_executable_with_source

fake = sys.argv[1]
os.environ.pop("CONFSEARCH_ORCA_PATH", None)
print("bare_name_resolution:", resolve_executable_with_source("orca"))
print("nonexistent_pin_falls_through:", resolve_executable_with_source("orca", configured_path="/nonexistent/orca"))
os.environ["CONFSEARCH_ORCA_PATH"] = fake
print("env_override:", resolve_executable_with_source("orca"))
print("explicit_pin_wins:", resolve_executable_with_source("orca", configured_path=fake))
print("configured_path:", get_configured_path({"executables": {"orca": {"path": "/opt/orca/orca"}}}, "orca"))
PY
rm -rf "$DEMO_BIN"
```

Real output:

```
bare_name_resolution: (PosixPath('/usr/bin/orca'), 'path')
nonexistent_pin_falls_through: (PosixPath('/usr/bin/orca'), 'path')
env_override: (PosixPath('$DEMO_BIN/orca'), 'env')
explicit_pin_wins: (PosixPath('$DEMO_BIN/orca'), 'config')
configured_path: /opt/orca/orca
```

### 3.1 The `/usr/bin/orca` GNOME screen-reader false positive

On this Linux host `/usr/bin/orca` is the **GNOME screen reader**, not the ORCA
quantum-chemistry package. A bare `resolve_executable("orca")` therefore
returns a misleading executable. Never conclude “ORCA is installed” from a
non-`None` bare resolution. Use one of:

- an explicit, validated `executables.orca.path` (or `CONFSEARCH_ORCA_PATH`);
- a strict `TaskContext(config={"executables": {"orca": {"path": …}}})` for an
  execution probe (section 4.4), which raises the typed
  `BackendUnavailableError` instead of running the wrong binary.

### 3.2 Config file and environment forms

The same value can be supplied as:

```yaml
# ~/.cccp.yaml (resolved config; never a pipeline execution file)
executables:
  orca:
    path: /opt/orca/orca
```

or as `CONFSEARCH_ORCA_PATH=/opt/orca/orca`. Do not commit machine-specific
absolute paths into a recipe or evidence report; software location is runtime
configuration, not scientific identity.

---

## 4. Singlepoint, optimize and frequency through the public API

The standalone path is: build a `TaskRequest` → bind a registered capability →
run the side-effect-free scientific preflight → (only when software exists)
execute exactly one delegated task. No binding starts a second executor or a
scheduler.

### 4.1 Build, bind and preflight a singlepoint (software-free)

```bash
"$PY" "$EVIDENCE/examples/singlepoint_public_api.py"
```

Real output (excerpt; the full JSON is in
`logs/cmd03-singlepoint.txt`):

```
{
  "example": "singlepoint_public_api",
  "execute": false,
  "binding_id": "science.electronic.energy",
  "capability_id": "electronic.energy",
  "implementation_id": "cccp.calculation.tasks.singlepoint",
  "delegation_target": "cccp.calculation.run_singlepoint",
  "dispatchable": true,
  "preflight_outcome": "ready",
  "preflight_ready": true,
  "preflight_checks": [
    {"id": "structure_supplied", "satisfied": true, ...},
    {"id": "structure_geometry_finite_angstrom", "satisfied": true, ...},
    {"id": "charge_multiplicity_consistent", "satisfied": true, ...},
    {"id": "method_or_basis", "satisfied": true, ...}
  ],
  "request": { "task": "singlepoint", "charge": 0, "multiplicity": 1, ... }
}
```

The example uses inline coordinates and inline method/basis. There is no
input-file path, no network, no subprocess and no write. The preflight is the
only scientific precondition gate; it is side-effect free.

### 4.2 Bind optimize and frequency (software-free)

```bash
"$PY" - <<'PY'
from cccp.calculation.capabilities.task_binding import bind_capability
from cccp.calculation.contracts import StructureRole
from cccp.calculation.requests import (
    FrequencyOptions, MethodSpec, OptimizeOptions, StructureInput, TaskKind, TaskRequest,
)

structure = StructureInput(
    coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.96), (0.93, 0.0, -0.25)),
    symbols=("O", "H", "H"),
    role=StructureRole.MINIMUM,
    source="inline",
)
for capability_id, task, options in (
    ("geometry.optimize", TaskKind.OPTIMIZE, OptimizeOptions()),
    ("vibration.compute", TaskKind.FREQUENCY, FrequencyOptions()),
):
    request = TaskRequest(
        task=task,
        structure=structure,
        level=MethodSpec(method="r2SCAN-3c", basis="def2-SVP"),
        options=options,
    )
    binding = bind_capability(capability_id, request)
    preflight = binding.preflight()
    print(capability_id)
    print("  implementation:", binding.implementation_id)
    print("  delegation:", binding.delegation_target)
    print("  outcome:", preflight.outcome.value)
    print("  checks:", [check.id for check in preflight.checks])
PY
```

Real output:

```
geometry.optimize
  implementation: cccp.calculation.tasks.optimize
  delegation: cccp.calculation.run_optimize
  outcome: ready
  checks: ['structure_supplied', 'structure_geometry_finite_angstrom', 'charge_multiplicity_consistent', 'method_or_basis', 'optimization_mode_applicable']
vibration.compute
  implementation: cccp.calculation.tasks.frequency
  delegation: cccp.calculation.run_frequency
  outcome: ready
  checks: ['structure_supplied', 'structure_geometry_finite_angstrom', 'charge_multiplicity_consistent', 'method_or_basis']
```

The three compute capabilities delegate to exactly one `run_*` task core:
singlepoint, optimize and frequency. There is no second execution path.

### 4.3 Execution requires software

Preflight `ready` does **not** mean the software exists. Execution additionally
requires the backend runtime and a `TaskContext`. On a machine with a real
ORCA installation, pass an explicit executable path in the context; a real
singlepoint/optimize/frequency execution is `NOT_VERIFIED` by this guide.

```python
from cccp.calculation.context import TaskContext

context = TaskContext(
    config={"executables": {"orca": {"path": "/explicit/path/to/orca"}}},
    workdir=output_dir,
)
result = binding.execute(context=context)
```

Do not run this block as a documentation example unless you own a real ORCA
installation and a scratch work directory. The guide does not claim it ran.

### 4.4 The typed unavailable path (verified)

The same example with `--execute` pins a nonexistent ORCA path and shows the
typed error instead of accidentally running `/usr/bin/orca`:

```bash
"$PY" "$EVIDENCE/examples/singlepoint_public_api.py" --execute
```

Real stderr and exit code:

```
ERROR: cccp.calculation.errors.BackendUnavailableError: Backend 'orca' requires program(s) for capability 'single_point' that are not available in this context: 'orca' (configured path: /nonexistent/orca)
```

```
exit code: 1
```

No `PASS` marker is printed on this path. This is a deliberate failure, not a
successful scientific result.

---

## 5. Reading reports

An evidence report separates **execution facts** from **claim status**. A
completed process is not evidence that a claim is supported; a failed process
never upgrades a claim. Read a report with the public contract reader:

```bash
"$PY" "$EVIDENCE/examples/read_report.py" --report "$EVIDENCE/inputs/supported_valid.json"
```

Real output:

```
{
  "example": "read_report",
  "report_id": "report-supported-valid",
  "schema_version": 1,
  "execution": {
    "argv": ["orca", "freq.inp"],
    "exit_code": 0,
    "terminal_state": "completed",
    ...
  },
  "execution_completed": true,
  "overall_claim_status": "supported",
  "claim_statuses": {"c-true-minimum": "supported"},
  "status_table": [
    {
      "claim_id": "c-true-minimum",
      "status": "supported",
      "validator_id": "check.imaginary_mode_count",
      "validator_version": "1.0.0",
      "validator_kind": "deterministic",
      "source_ref_count": 1,
      "missing_checks": [],
      "limitations": ["lim-single-conformer"]
    }
  ],
  "limitations": [
    {
      "limitation_id": "lim-single-conformer",
      "description": "Only conformer A was evaluated; other conformers may not be minima.",
      "affected_claim_ids": ["c-true-minimum"]
    }
  ],
  "provenance_digest": null
}
```

Reading rules:

- `execution` is the only place for `terminal_state`, `argv`, `exit_code`,
  timestamps and stdout/stderr paths. Claims carry none of these fields.
- A `supported`/`refuted` claim must carry an admissible validator
  (`check.*`, `software.*` or `human.*`) **and** at least one digested source
  reference. Model text is not a validator.
- `missing_checks` is always present, even when empty, so “nothing was
  missing” is explicit.
- Report-level `limitations[]` are stable ids; a claim may reference them.
  A limitation never hides a missing check.
- The overall status is conservative: `refuted` > `inconclusive` >
  `not_evaluated` > `supported`. One unsupported claim is not hidden by
  another supported claim.

Provenance is separate from evidence. It records **how** a result was derived
(software version, method/basis/state, input digests, declared conditions) and
is never a support/refutation status. The contract corpus itself is available
from `contract_root()`; `evidence-semantics.md` there is normative.

---

## 6. Units and mapping

### 6.1 Energy units

Internal electronic energies are hartree. Convert explicitly:

```bash
"$PY" - <<'PY'
from cccp.utils.constants import HARTREE_TO_KCAL
print("HARTREE_TO_KCAL:", HARTREE_TO_KCAL)
print("0.01 hartree in kcal/mol:", round(0.01 * HARTREE_TO_KCAL, 6))
PY
```

Real output:

```
HARTREE_TO_KCAL: 627.5094740631
0.01 hartree in kcal/mol: 6.275095
```

Never copy the constant into a report or recipe: import
`cccp.utils.constants.HARTREE_TO_KCAL` (and `KCAL_TO_HARTREE` for the inverse).
NMR chemical shifts are reported in **ppm** by the NMR analysis modules; they
are not hartree and must not be converted with the energy constant. Frequency
outputs are in `cm-1`; geometries are in angstrom. A numeric source reference
must declare its unit explicitly (`hartree`, `kcal/mol`, `cm-1`, `angstrom`,
`kelvin`, …); `null` is allowed only for a non-numeric artifact.

### 6.2 Atom mapping and invalidation

An atom mapping is a one-to-one correspondence with both directions required
to be exact inverses. Duplicate targets, coverage overlap, and non-inverse
directions are rejected at construction time:

```bash
"$PY" - <<'PY'
from cccp.analysis.research.reaction import AtomMapping, MapInverseError

mapping = AtomMapping(
    left_to_right={"a:c1": "b:c1", "a:o1": "b:o1"},
    right_to_left={"b:c1": "a:c1", "b:o1": "a:o1"},
)
print("valid_mapping:", mapping.to_dict())
try:
    AtomMapping(
        left_to_right={"a:c1": "b:c1"},
        right_to_left={"b:c1": "a:o1"},
    )
except MapInverseError as exc:
    print("non_inverse_rejected:", type(exc).__name__, str(exc))
PY
```

Real output:

```
valid_mapping: {'left_to_right': {'a:c1': 'b:c1', 'a:o1': 'b:o1'}, 'right_to_left': {'b:c1': 'a:c1', 'b:o1': 'a:o1'}, 'unmapped_left': [], 'unmapped_right': []}
non_inverse_rejected: MapInverseError atom_mapping directions are not exact inverses (first differences: [('b:c1', 'a:c1'), ('b:c1', 'a:o1')])
```

**Mapping invalidation:** changed atom order, reordered structure input, edited
coordinates that alter connectivity, or a re-optimized structure with a new
identity invalidates a previously computed mapping. Do not reuse a mapping
across structures; recompute it and revalidate coverage. A one-sided or
ambiguous mapping must stay `inconclusive` for connectivity claims.

---

## 7. Failure interpretation

### 7.1 Claim status vocabulary

| Status | Meaning | Required floor |
|---|---|---|
| `supported` | evidence affirms the claim within its scope | validator id+version and a digested source |
| `refuted` | evidence contradicts the claim within its scope | validator id+version and a digested source |
| `inconclusive` | evidence exists but is partial/conflicting | missing/partial checks listed |
| `not_evaluated` | check not attempted or prerequisite absent | at least one explicit missing check |

`NOT_VERIFIED` is **not** one of the four claim statuses. It is the harness /
guide term for “this environment or artifact was not available”, e.g. a missing
wheel or a real QC run that never happened. `FAIL` means an executed criterion
failed; `NOT_VERIFIED` means it could not be executed. Do not translate one
into the other.

### 7.2 Missing checks and contradictions

A missing check (`artifact_missing`, `artifact_digest_mismatch`,
`partial_output`, `missing_irc`, `frequency_not_computed`,
`convergence_not_met`, …) is structured evidence, not prose. A blocking
missing check forbids `supported`/`refuted`. If the output is partial, the
claim is `inconclusive` (or `refuted` only when the evidence positively
contradicts it), never `supported`.

### 7.3 Acceptance harness exit codes

`scripts/acceptance/acp2_science.py` uses the project contract:

```
0 = PASS          1 = FAIL
2 = NOT_VERIFIED  3 = usage error
```

Verified for `installed-examples`:

```bash
# no wheel supplied
env -u ACP2_CCCP_WHEEL "$PY" scripts/acceptance/acp2_science.py \
  --scenario installed-examples --out "$EVIDENCE/harness-absent" --timeout 300
```

Real result:

```
NOT_VERIFIED: installed cccp wheel not provided: ACP2_CCCP_WHEEL
exit code: 2
```

```bash
# wheel supplied
ACP2_CCCP_WHEEL="$WHEEL" "$PY" scripts/acceptance/acp2_science.py \
  --scenario installed-examples --out "$EVIDENCE/harness-present" --timeout 300
```

Real result:

```
PASS: installed examples passed with the clean interpreter .../venv/bin/python and wheel sha256 78d0e622...
exit code: 0
```

The harness never starts a scheduler, a QC subprocess or a production run
root. `--help` exits 0 and lists every scenario; an unknown scenario exits 3.

---

## 8. Resource limits and cleanup

### 8.1 Task resources

Resources are runtime data in `TaskRequest.resources`, never scientific
identity:

```bash
"$PY" - <<'PY'
from cccp.calculation.requests import TaskResources
resources = TaskResources(nproc=2, maxcore=256, mem="1GB")
print("resources:", resources.to_dict())
print("mem_total_mb:", resources.mem_total_mb())
PY
```

Real output:

```
resources: {'nproc': 2, 'mem': '1GB', 'maxcore': 256}
mem_total_mb: 1024.0
```

`nproc`, `maxcore` (MB per core) and `mem` (total task memory) are declared per
task; the completion-time identity excludes them. The platform layer owns
batch/queue/attempt budgets and their caps; standalone science does not invent
a second budget engine.

### 8.2 Cleanup

- The `installed-examples` harness creates its clean venv and work directory
  under a temporary directory, runs the four examples, writes stdout/stderr
  logs, and removes the directory. A verified run records a receipt such as
  `removed $TMPDIR/acp2-a28-installed-examples-…` in `examples.json`.
- The standalone API opens no long-lived process. If you create a venv or
  work directory, remove it yourself; nothing in this guide writes to a
  production run root.
- Never point an example or harness `--out` at a production run root. The
  project production root is `/var/lib/acp/runs`; this guide's scenarios stay
  in `/tmp` or an evidence directory.

---

## 9. Standalone science versus platform-controlled research

This guide is **standalone science**. It covers import, discovery, request
construction, preflight, report reading, units, mapping, failure semantics,
resources and plugin metadata. It does not cover, and does not claim:

- platform job submission, queueing, pause/resume/cancel, attempts or reruns;
- platform-owned storage layout, authorization or lifecycle;
- a real ORCA/xTB execution on this host;
- Diels-Alder or selectivity evidence, H6 closure, or any signoff.

Those belong to the platform scenarios and their own acceptance records. In
this document they remain `NOT_VERIFIED`. A standalone PASS is never a platform
acceptance PASS, and a platform run id or job id must not appear in a
standalone science request or report.

---

## 10. Plugin development

### 10.1 Manifest and discovery group

An external science plugin is declared by `cccp_science_plugin.json` in the
distribution and discovered from the `cccp.science_plugins` entry-point group.
Web/scheduler dependencies are not part of this surface.

```bash
"$PY" - <<'PY'
from cccp.plugin import (
    PLUGIN_API_VERSION, PLUGIN_ENABLEMENT_POLICY, PLUGIN_SCIENCE_VERSION,
    SCIENCE_PLUGIN_ENTRY_POINT_GROUP, SCIENCE_PLUGIN_MANIFEST_FILENAME,
)
print("manifest_filename:", SCIENCE_PLUGIN_MANIFEST_FILENAME)
print("entry_point_group:", SCIENCE_PLUGIN_ENTRY_POINT_GROUP)
print("enablement_policy:", PLUGIN_ENABLEMENT_POLICY)
print("host_api_version:", PLUGIN_API_VERSION)
print("host_science_version:", PLUGIN_SCIENCE_VERSION)
PY
```

Real output:

```
manifest_filename: cccp_science_plugin.json
entry_point_group: cccp.science_plugins
enablement_policy: disabled-by-default
host_api_version: 1
host_science_version: 1
```

Required manifest parts: `name`, `distribution`, `version`, `api`
(`api_version`, `compatible_range`, `schema_version`), at least one capability
(`name`, `schema`, `schema_version`, `kind`, `implementation_id`,
`validator_refs`/`recipe_refs`), `science_software`, `resources`, `license`,
`error_semantics` (`never_overrides_builtin: true`), `default_enabled`,
`science_version_range`, `entry_point_group_version`, and optional
`fixture_evidence`/`distribution_digest`.

### 10.2 Metadata/trust/license projection

```bash
"$PY" "$EVIDENCE/examples/external_plugin.py" --manifest "$EVIDENCE/inputs/cccp_science_plugin.json"
```

Real output (excerpt; full JSON in `logs/cmd08-external-plugin.txt`):

```
{
  "example": "external_plugin",
  "name": "acp2_external.native_singlepoint",
  "entry_point_group": "cccp.science_plugins",
  "default_enabled": false,
  "trust_level": "untrusted",
  "is_trusted": false,
  "has_verified_fixture_evidence": true,
  "auto_enabled": false,
  "enablement_policy": "disabled-by-default",
  "projections": {
    "described": {"state": "present"},
    "installed": {"state": "present", "reason": "discovered from explicit"},
    "enabled": {"state": "absent", "reason": "disabled by default; explicit enablement required"},
    "verified": {"state": "not_verified", "reason": "trust level is 'untrusted', not evidence-backed 'trusted'"}
  },
  "license_projection": {
    "license": "MIT",
    "accepted_licenses": ["AGPL-3.0-only", "Apache-2.0", "..."],
    "unknown_policy": "reject"
  }
}
```

Rules:

- **Discovery is metadata-only.** Reading a manifest parses frozen data; it
  does not import the plugin, call a loader, spawn a process or open a socket.
  The four projections are independent: installation never promotes
  `verified`; enablement never promotes `verified`.
- **Enablement is explicit.** `default_enabled` is a declaration, not an
  action. A discovered plugin is `enabled: absent` until an operator calls
  `PluginHost.enable`; `default_enabled: true` does not auto-enable it.
- **Trust/license.** `trusted` requires verified fixture evidence with a
  sha256 digest; an `untrusted` manifest stays `not_verified` even when
  fixture evidence is present. The license must be in `accepted_licenses`
  (`unknown_policy: reject` by default).
- **No override of built-ins.** A plugin may not take a built-in name,
  capability or implementation id; conflicts raise typed errors before any
  loader.
- **No process sandbox claim.** In-process plugins are trusted code, not a
  security boundary. The plugin pilot does not claim filesystem, network or
  process isolation. Do not use this surface to run untrusted code.

A recipe or validator reference grants no execution authority, and a plugin
digest is derived from the manifest and result envelope — never from a job id,
queue, account or display name.

---

## Appendix A — Documented command index

| # | Command (clean venv) | Exit |
|---|---|---:|
| 1 | import/discovery baseline | 0 |
| 2 | software resolution precedence | 0 |
| 3 | `singlepoint_public_api.py` | 0 |
| 4 | optimize + frequency binding | 0 |
| 5 | `read_report.py --report …` | 0 |
| 6 | units + atom mapping | 0 |
| 7 | resource limits | 0 |
| 8 | `external_plugin.py --manifest …` | 0 |
| 9 | plugin contract constants | 0 |
| 10 | `singlepoint_public_api.py --execute` | 1 (typed unavailable) |
| 11 | harness `--help` | 0 |
| 12 | harness `installed-examples` without wheel | 2 (`NOT_VERIFIED`) |
| 13 | harness `installed-examples` with wheel | 0 (`PASS`) |

## Appendix B — Remaining NOT_VERIFIED

- B25a published `cccp` wheel, release manifest and `pip install` flow.
- A real ORCA/xTB/CREST singlepoint, optimize or frequency execution.
- Real Diels-Alder / selectivity evidence and H6 closure.
- Any platform job, queue, scheduler or production-run acceptance.
- A wheel with B-owned package data built before the corpus patch (it would
  fail section 2 and must be rejected, not “fixed” by reading the repository).
