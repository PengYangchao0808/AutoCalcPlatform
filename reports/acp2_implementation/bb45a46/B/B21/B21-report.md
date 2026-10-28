# B21 — Infrastructure adapter contract, discovery and isolation

- Candidate: `feat/frontend-plan1-closure` @ `9793bfa16640e29f293a1d5672565f3a7c30e1cf` (working tree)
- Date: 2026-10-23 UTC
- Owner: Plan B runtime (B21; B22 consumes these facts)
- Controller: one loopback test Controller at `127.0.0.1:18782`, run root
  `/ACPServer/acp2-development/data/b/run-root-b21`; production `8765` and
  `/var/lib/acp/runs` untouched. The scenario observed `tasks_before=0` and
  `tasks_after=0`, so adapter discovery/install did not start a second
  authority or create a task.
- Harness: `scripts/acceptance/acp2_runtime.py --scenario infra-plugin`
  phases `infra:install` and `infra:upgrade`, both exit `0`.

## What was added

| Path | Purpose |
|---|---|
| `src/acp_services/contracts/adapters.py` | Frozen `AdapterDescriptor` (id, version, kind, supported schema, environment, resource limits, verification fixture, capabilities, trust, inert entrypoint metadata), validation, canonical digest, and built-in Local/LSF/storage/transfer/identity descriptors. No science parser/threshold; scientific capability names and isolation claims are rejected. |
| `src/acp_services/runtime/adapter_registry.py` | Metadata-only discovery, authorization-gated install/enable, exact-version resolution, environment/capability/trust checks, per-task freeze and old-task read-back, explicit `allow_execution` invocation, persistent install/active/task records, and a `NOT_VERIFIED` process-isolation report. |
| `src/acp_services/runtime/adapter_bootstrap.py` | Minimal composition root (`ACP_ADAPTER_PLUGIN_ROOT`, `ACP_ADAPTER_STATE_ROOT`, `ACP_ADAPTER_TRUSTED_IDS`, `ACP_ADAPTER_AUTHORIZED_PRINCIPALS`). It starts no Controller/scheduler/subprocess. |
| `tests/test_acp2_runtime_adapter_registry.py` | 14 focused tests: discovery-no-execution, authorization, wrong version, missing environment, capability conflict, trust, freeze/read-back, metadata-only execution boundary, isolation, composition root, dependency direction. |
| `scripts/acceptance/acp2_runtime.py` | Real `infra:install` / `infra:upgrade` scenario bodies (replacing the B01 stub). |

The Controller was **not** rewritten for a plugin directory. External adapters
are discovered by the same registry seam; Local/LSF paths and descriptors are
unchanged behaviour.

## External plugin without a core-source edit

Bundle outside the repository:
`/ACPServer/acp2-development/data/b/plugins/b21/`.

- adapter: `acp.storage.inventory` (storage infrastructure; no science)
- v1 `1.0.0` descriptor `ae511f7aa91ff85e…`, entrypoint
  `98454e2d55749908…`
- v1.1 `1.1.0` descriptor `97ea1d8601e46253…`, entrypoint
  `808825879f0f08fd…`
- descriptors copied to `external-plugin/descriptor-v{1,2}.json`; all bundle
  file digests in `external-plugin/bundle-manifest.json` and
  `external-plugin/bundle.sha256`.
- `external-plugin/source-location.txt` records that no file under
  `src/` was modified to recognize this adapter.

A real infrastructure operation (`inventory` over a workspace file) ran through
the installed edge and returned the file's SHA-256; result in
`infra_install-plugin-run-v1.json`.

## Discovery never executes the entrypoint

Discovery reads `adapter.json` and hashes the entrypoint bytes only. Evidence:

- `infra_install-discovery.json` has `"execution": "none"`, every metadata row
  has `"executed": false`, and `plugin_modules_in_sys_modules=[]`.
- The `storage-sideeffect` bundle writes a marker if imported. The marker is
  absent after discovery, install **and** enable:
  `infra_install-discovery.json.side_effect_marker.exists=false` and
  assertion `b21.metadata.install_enable_did_not_execute`.
- `b21.metadata.explicit_execution_required` proves `invoke(...)` without
  `allow_execution=True` raises `execution_requires_explicit_allow` before any
  module load.

## Authorization

`AdapterAuthorization` is a server-side fact (action, adapter id/version,
descriptor digest, authorized principal, issue/expiry). It is never read from
plugin metadata; unknown metadata fields such as `authorization` are rejected at
discovery. `infra_install-authorization.json` records the install/enable facts.
Rejections: missing fact, wrong action, non-authorized principal, expired fact,
and enable-before-install.

## Frozen task and old-task read-back

- Old task `b21-old-task` attempt 1 froze `acp.storage.inventory@1.0.0`
  (`ae511f7aa91ff85e…`, entrypoint `98454e2d55749908…`) before the upgrade.
- v1.1.0 was then installed and enabled; new task `b21-new-task` attempt 1 froze
  `1.1.0` (`97ea1d8601e46253…`).
- Reading the old task returns v1 unchanged and `invoke_frozen` executes v1
  (`1.0.0`) while the active path executes v1.1.0:
  `infra_install-old-task-readback.json`.
- A fresh registry over the same state root reads the same frozen v1 record
  (`persistence-state/tasks/b21-old-task/attempt-1.json`), proving persistence
  is not an in-memory substitute and never resolves to "latest".

## Rejection matrix (`infra_install-rejection-matrix.json`)

| Case | Expected / actual code | Result |
|---|---|---|
| wrong platform major (`2.0.0`) | `adapter_version_incompatible` | PASS |
| unknown exact version (`9.9.9`) | `adapter_version_not_installed`; active stays `1.1.0` | PASS |
| missing environment | `adapter_environment_missing` (`env:ACP2_B21_MISSING_ENV`) | PASS |
| capability conflict | `adapter_capability_conflict` (`scheduler.local` owned by `platform.scheduler.local@1.0.0`) | PASS |
| untrusted plugin | `adapter_untrusted`; not enabled | PASS |
| isolation claim in metadata | discovery `adapter_isolation_claim_rejected` | PASS |
| science capability (`storage.nmr`) | discovery `science_capability_forbidden` | PASS |

## Compatibility / isolation truth

`infra_install-compatibility-matrix.json`:

| Backend | Status | Evidence |
|---|---|---|
| Local | PASS | Built-in `platform.scheduler.local@1.0.0`; real local regression command exit 0 (`local-regression.log`) |
| LSF | NOT_VERIFIED | No live LSF client/queue attached; descriptor only, no `bsub` run |
| Slurm | NOT_VERIFIED | No real Slurm environment; submit/cancel/recover not executed |

`isolation.json` reports `execution_model=in-process`,
`process_isolation=NOT_VERIFIED`, `sandbox=false`, `trusted_plugins_only=true`.
An in-process plugin is not a sandbox; this task makes no isolation claim.

## Verification

- `pytest.txt`: 94 passed (adapter registry, acceptance harness, simulated LSF,
  isolation/lab Local paths, Services isolation guards).
- `ruff-check.txt` / `ruff-format.txt`: exit 0 (`All checks passed!`,
  `5 files already formatted`).
- `validator.txt` / `validator.exit`: `VALID` for `summary.json` (both phases).
- `samples.txt` / `samples.exit`: 25/25 acceptance sample expectations matched.
- Scenario exits: `scenario-install.exit=0`, `scenario-upgrade.exit=0`.
- `cleanup.json`: Controller stopped, port 18782 free, test run root and plugin
  state removed, side-effect marker removed, external plugin bundle retained for
  B22, `tasks_created_by_b21=0`.

## Non-claims

- No Slurm support is claimed; no LSF live run is claimed.
- No science parser/threshold was added to the platform adapter layer.
- No untrusted plugin was enabled; trust is an explicit server-side allow-list.
- Process/OS isolation is a separate capability and remains `NOT_VERIFIED`.
