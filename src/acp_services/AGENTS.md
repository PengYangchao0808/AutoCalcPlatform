# acp_services/ — Platform Services Layer

## OVERVIEW
Platform service layer for ACP: workspace/access boundary, Data prepare/read, Runtime use cases, persistent store/schema, contracts, and thin entrypoints. Dependency direction is **one-way**: `acp → acp_services`; `src/acp_services/**` must never import `acp` (including lazy imports, `TYPE_CHECKING`, `importlib`, `__import__`, string `getattr`). ~70 `.py`; Python 3.10+; PEP 604 annotations.

**Status (2026-10-19):** module/contract slices are delivered and covered by `tests/test_services_*.py`; real browser / scientific software / LSF / production remain `NOT_VERIFIED` (see `docs/reference/current/ACP_Services_Closure_20261019.md`). `acp` old import paths are retained as identity forwarders — never re-add an implementation on the `acp` side.

## STRUCTURE
```
acp_services/
├── bootstrap.py          # ServicesPorts frozen port bundle (transitional injection; each port has a migration/removal task)
├── worker_bootstrap.py   # self-sufficient worker composition root (`python -m acp_services.worker_bootstrap`; zero acp import / zero jobs DB)
├── workspace/            # projects, admin, entry_authz, identity_lifecycle, identity_mapping, keys, boundary
├── data/                 # contracts, operations (prepare), transfer, resource_read, read_root, storage_binding, namespaces, archive, import_, budget, helpers
├── runtime/              # commands, execution_chain, worker, receipts, recovery, plan_contracts, step_result, step_requirements,
│                         # admission, quota, usage, metering_policy, freshness, projections, events, outbox, provisioning,
│                         # workflow_enablement, isolation, lab_capabilities, lineage, helpers, method_flags
├── store/                # store (JobStore CAS), migrations, record(s), layout, naming, legacy_method, molecule_groups
├── contracts/            # access, envelope, events, config_plan, config_validation, action_gating, injection
└── entrypoints/          # diagnostics, machine_output, mcp (thin; some not bound to a default entry — see closure L2)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Port bundle / transitional assembly | `bootstrap.py::ServicesPorts` | Frozen dataclass; `JobStorePort`/`MigrationsPort`/`StepRequirementPort` already removed (moved in place) |
| Worker composition root | `worker_bootstrap.py` | Builds from frozen envelope/plan/binding/artifacts only; writes one receipt; never touches jobs DB |
| Store / CAS | `store/store.py` | `JobStore`, `JobStateConflictError`, `CursorSnapshot`; implementation root of `acp.scheduler.store` forwarder |
| Schema migrations | `store/migrations.py` | `migrate`, `get_schema_version`; implementation root of `acp.scheduler.migrations` forwarder |
| Plan/step contracts | `runtime/plan_contracts.py`, `runtime/step_result.py`, `runtime/step_requirements.py` | Identity-forwarded from `acp.calculations.{contracts,step_result,step_requirements}` |
| Runtime use cases | `runtime/commands.py` | submit/pause/unpause/cancel/continue/rerun/edit/purge + CLI completion registration via injected ports |
| Execution entry selection | `runtime/execution_chain.py` | Per-attempt envelope vs legacy; 8/15 workflows enabled, 7 legacy retained |
| Data prepare/read | `data/operations.py`, `data/transfer.py`, `data/resource_read.py`, `data/read_root.py` | Persistent prepare operations, verified transfer, cache-only reads; old GET remains compat |
| Storage binding / namespace | `data/storage_binding.py`, `data/namespaces.py` | Single allocation authority; atomic idempotent namespace |
| Events / outbox | `runtime/events.py`, `runtime/outbox.py`, `events.py` | Persistent event protocol; per-task JSONL reuse |
| Access / identity | `workspace/{entry_authz,identity_lifecycle,identity_mapping,keys,boundary}.py` | Local-single first; OIDC `False` |
| Admin | `workspace/admin.py` | Per-entry re-authorization + audit + restricted broker |
| Admission / quota / usage | `runtime/{admission,quota,usage,metering_policy}.py` | Module-verified; dispatch/UI wiring `NOT_VERIFIED` |
| Contracts | `contracts/{access,envelope,events,config_plan,config_validation,action_gating,injection}.py` | Frozen data classes; no `acp.*` in public signatures |
| Entrypoints | `entrypoints/{diagnostics,machine_output,mcp}.py` | Thin; MCP stdio implemented but not bound to a default entry |
| Method flags | `runtime/method_flags.py` | Implementation root of `acp.catalog.method_levels_to_cli_flags` |

## CONVENTIONS
- **One-way dependency**: never import `acp` from this package (AST guard `tests/test_acp_services_isolation.py`; worker guard `tests/test_services_worker_contract.py`).
- **Single implementation root**: `acp` old paths must be identity forwarders (`is` same object). Do not re-add implementations or business logic there.
- **Frozen contracts first**: `contracts/**` and `runtime/plan_contracts.py` are `@dataclass(frozen=True)`; extend additively, never fork a second DTO.
- **Explicit injection**: `ServicesPorts` is a frozen bundle, no global mutable singleton, no dynamic lookup.
- **No service restart from docs**: deployment is generated by `scripts/install_systemd.sh`; actual release/restart is todo 76 (gated).
- **Evidence**: update `tests/baseline/services-evidence/migration_ledger.md` (6 columns, status token in the exit-condition cell) and `public_shim_inventory.md` when adding/retaining a public surface.

## ANTI-PATTERNS
- **`acp_services → acp` import** — hard fail, including lazy/`TYPE_CHECKING`/string reflection.
- **Second state machine** — control intent stays with `JobManager`; Runtime use cases delegate through ports exactly once.
- **DB access from worker** — `worker_bootstrap` must run with an empty `ACP_RUN_ROOT` and create no DB file.
- **Mock as real** — `PASS` at module level is not real-science/browser/LSF/production evidence; keep `NOT_VERIFIED` explicit.
- **Deleting compat surfaces for line count** — primitives/backends/compat legacy/recovery fixtures/model assets/vendor/bootstraps are retained by design.
- **Rewriting `NOT_VERIFIED` to `PASS`** or documenting “field delivered” as “frontend integrated” — forbidden.

## INSTALLED ROLE (B25b, 2026-10-23)
- `acp-worker` installs only `acp_services/**` + `cccp`; `python -m acp_services.worker_bootstrap` and the `acp-worker` script are the standalone worker entries. Do not install Web/DSH/browser into a worker or CCCP node.
- The installed wheel keeps `acp_services` independent of `acp`; the root `acp` distribution composes it. MCP stdio is served by `acp mcp serve` through `src/acp/services_mcp_bootstrap.py`, not by importing `acp` from this package.
- Clean-install evidence: `reports/acp2_implementation/bb45a46/B/B25/` (`installed-probes.json`, `assets-audit.json`).
