# F4 scope audit — Amendment S2 bis: ACP Services migration stations

**Registered:** 2026-10-19
**Plan:** `docs/reference/next-change/ACP_Parallel_Plan_2_Services_20261018.md` (todo 66 shared-gate revision; B13 "共享门变更接受 A/C 证据，不删除护栏换通过")
**Owned files:** `tests/test_f4_scope_audit.py` (registry + checks), this record.
**Baseline of the audit:** `2a23b93` (`BASELINE` in the test).

## 1. Symptom that forced the revision

`python3.11 -m pytest tests/test_f4_scope_audit.py -q` failed at
`test_migration_scope_audit_reaches_migration_files` with 22 files flagged
`changed outside registered migration stations (unaudited)`. The registry
(`MIGRATION_STATION_PREFIXES`) predated the ACP Services plan, so every
Services-era station the plan legitimately created/modified was flagged. The
audit was **honestly red**, not vacuous — the fix is to register the revision,
never to widen/skip the gate.

## 2. What was registered (and why each is a legitimate station)

### 2.1 Services composition seams (lane B, Plan 2)

| Station | Plan todo / slice | Why it is a real station |
|---|---|---|
| `src/acp/services_bootstrap.py` | todo 72 / B05-7, todo 25 | Legacy composition root / DI seam; ledger row "版本化 schema 迁移" + `injection_registry.md`. |
| `src/acp/services_data_bootstrap.py` | todo 11–16 / B02 | `DataResourcePort` / `StorageBinding` / persistent prepare / transfer-verify seam (ledger rows 37, 65, 66). |
| `src/acp/services_events_bootstrap.py` | todo 22–24 / B04 | Persistent event-protocol adapter over the legacy JSONL log (one-way `acp → acp_services` edge). |
| `src/acp/services_publication_bootstrap.py` | todo 28 (B28a) | `PublicationDataPort` — manifest/candidates/markers routed through the Data entry (ledger row 73). |
| `src/acp/init_wizard/` | todo 36/37 / B07 | The single `acp init` station: unified answers/plan/persist/doctor + pure no-Paramiko node validation. |

### 2.2 ACP-side result stations (lane B, Plan 2)

| Station | Plan todo / slice | Why |
|---|---|---|
| `src/acp/results/frame_candidate_store.py` | todo 28 | Authoritative `frame_candidates.json` writer + shared `atomic_write_text` (publication entry). |
| `src/acp/results/frames_adapter.py` | todo 28 (B28b) | Science-product → `TrajectoryFrame`/`TrajectoryAnnotation` projection (ledger row 74). |
| `src/acp/results/frame_candidates.py` | todo 81 (remediation d51b024) | Production frame-candidate/manifest writes route through `PublicationDataPort` → `DataResourcePort`; direct writers remain the non-production fallback (behavior preserved). |

### 2.3 CCCP-relocation forwarders (lane C, Plan 3) seen by the shared gate

These were flagged because the shared F4 gate spans all of `src/acp/**`; they are
genuine relocation stations from the concurrent CCCP plan. Each now contains
**no implementation** — it is a pure re-export shim to `cccp.analysis.*` — so
they were registered in `MIGRATION_SHIM_PY` rather than given a bare boundary
pass. R3 shim purity is the applied tooth.

| Station | Plan 3 relocation | Now forwards to |
|---|---|---|
| `src/acp/chem/embedding.py` | intake/chem science → cccp | `cccp.analysis.structure.embedding` |
| `src/acp/intake/models.py` | intake science → cccp | `cccp.analysis.structure.models` |
| `src/acp/confsearch/sampling.py` | todo 20 sampling → cccp | `cccp.analysis.sampling` |
| `src/acp/confsearch/sampling_models.py` | todo 20 | `cccp.analysis.sampling.models` |
| `src/acp/confsearch/selection.py` | todo 21 selection → cccp | `cccp.analysis.confsearch.selection` |
| `src/acp/confsearch/shared/boltzmann.py` | todo 21 | `cccp.analysis.confsearch.boltzmann` |
| `src/acp/confsearch/shared/deduplication.py` | todo 20 | `cccp.analysis.sampling.deduplication` |

### 2.4 Scope extension: `src/acp_services/`

`MIGRATION_SCOPE` gained `src/acp_services/`, and the R2 no-`acp`-import AST scan
was extended to it (previously `src/cccp/` only). This turns the shared gate
into an *enforced* one-way dependency edge for the new package. It is
**stronger-by-coverage**, not a relaxation: the 70 changed Services modules are
scanned, and `tests/test_acp_services_isolation.py` retains its deeper
importlib / `sys.modules` / `getattr` defences.

## 3. Equivalent-or-stronger checks added

The amendment does not merely add names; it adds teeth:

1. `test_services_bootstrap_stations_are_pure_composition_seams` — each of the
   four seams imports `acp_services`, and imports **no** `paramiko` /
   `subprocess` / `cccp.qc` / `cccp.backends` / `cccp.calculation`. A seam that
   opens SSH or runs QC/exec directly fails.
2. `test_services_composition_root_is_singular` — `ServicesPorts(...)` is
   constructed in exactly one file (`src/acp/services_bootstrap.py`); a second
   composition root fails.
3. `test_services_init_wizard_keeps_single_validation_invariants` — (a) no
   module-scope `paramiko` anywhere in `src/acp/init_wizard/**` (D7 lazy-remote
   invariant); (b) `flows.py`/`gating.py` must import the pure Services
   validators (`acp_services.contracts.config_validation` /
   `acp_services.contracts.action_gating`) — the single-validation / action-
   gating delegation; (c) those pure validators themselves import no
   `paramiko`/`acp`.
4. `test_services_relocation_shims_stay_pure_reexports` — the seven forwarders
   must pass `_is_migration_shim` (R3) today.
5. `test_services_scope_audits_acp_services_import_discipline` — an `acp` import
   smuggled into a `src/acp_services/**` module is flagged by R2 (proves the
   scope addition has teeth).
6. R3 shim purity now also covers the seven newly registered forwarders via
   `MIGRATION_SHIM_PY` (unchanged `_is_migration_shim` definition).

## 4. Negative-test proof (teeth preserved)

`test_services_amendment_keeps_unaudited_change_teeth` proves the amendment did
**not** blunt the boundary:

* `src/acp/unknown_station.py` → `unaudited` (still flagged);
* `src/acp/services_bootstrap_evil.py` → `unaudited` (near-miss on the exact-file
  registration is not swallowed);
* `src/acp/results/rogue_station.py` → `unaudited` (the amendment registered
  **exact files** under `results/`, not the whole directory, so unrelated/new
  results modules still fail);
* a mutated `src/acp/chem/embedding.py` gaining a real body → `shim` issue (R3).

Existing negatives remain untouched:
`test_migration_scope_catches_unaudited_change`,
`test_migration_scope_catches_workflow_backend_direct`,
`test_migration_scope_catches_acp_import_in_cccp`,
`test_migration_scope_catches_smuggled_shim_body`.

## 5. Deliberate non-registration (teeth-preserving decision)

The task sketch suggested registering the whole `src/acp/intake/` and
`src/acp/results/` directories. Those directories hold 4 and 26 modules,
most of them **unrelated** to the Services plan; a directory prefix would
blanket-whitelist any future change there. The amendment instead registered the
**exact flagged files** (`intake/models.py`, `results/frame_candidate_store.py`,
`results/frames_adapter.py`). This keeps the boundary strict — the negative test
above (2.4 / §4) demonstrates a new `results/` module is still flagged. The
`src/acp/init_wizard/` directory **was** registered as a whole because the entire
package is the B07 `acp init` station and it is small; the package-wide
module-level-paramiko ban (§3.3a) adds package-wide teeth to compensate.

## 6. Evidence (commands / exit codes)

| Command | Before | After |
|---|---|---|
| `python3.11 -m pytest tests/test_f4_scope_audit.py -q` | 1 failed, 28 passed (22 unaudited) | **35 passed** |
| `python3.11 -m pytest tests/test_grep_gates_script.py tests/test_architecture_invariants.py tests/test_acp_services_isolation.py -q` | — | **203 passed** |
| `python3.11 scripts/check_grep_gates.py --list-gates` | — | exit 0 |
| `python3.11 scripts/check_grep_gates.py --suite architecture-remediation` | — | exit 0 (10 gates, 0 blocking) |
| `--gate unique_run_scan src/acp src/cccp` | — | exit 0 |
| `--gate unique_run_irc src/acp src/cccp` | — | exit 0 |
| `--gate wave2_shermo_external <two stub paths>` | — | exit 0 |
| `--gate final_shermo src` | — | exit 0 |

## 7. Amendment S2 bis-2 (2026-10-19): todo 81 production frame-candidate station

**Registration:** `src/acp/results/frame_candidates.py` added to
`MIGRATION_STATION_PREFIXES` (exact file, not the `results/` directory).

**Reason:** remediation todo 81 (commit `d51b024`) routed the production
frame-candidate write path through the Data port: `save_frame_candidate(...)`
gained a duck-typed `publication` entry and now calls
`publication.save_candidates(...)` / `publication.publish_manifest(...)`, while
retaining the direct `write_authority` / `manifest.write` path as the
non-production fallback (tests / non-production callers).  Behavior is
preserved and `frame_candidate_store.py` remains the single authority writer.

**Equivalent-or-stronger check (applied, not waived):**
`_frame_candidates_publication_issues` + test
`test_frame_candidates_routes_production_writes_through_publication_port`
enforce that (a) `save_frame_candidate` accepts the `publication` port,
(b) both production writes call through it, (c) `write_authority` is imported
from `acp.results.frame_candidate_store` (no second authority), and (d) a
mutated source bypassing the port is caught.  The negative tooth in §4
(`src/acp/results/rogue_station.py` still `unaudited`) is untouched, so the
exact-file registration does not whitelist the `results/` directory.

**Verification (2026-10-19):**

| Command | Result |
|---|---|
| `python3.11 -m pytest tests/test_f4_scope_audit.py -q` | **36 passed** |
| `python3.11 -m pytest tests/test_architecture_invariants.py tests/test_acp_services_isolation.py -q` | PASS |
