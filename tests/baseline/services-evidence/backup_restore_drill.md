# B13-3 — Consistent backup/restore drill (DB / artifacts / secrets / ledger) — OS-11

**Plan item**: `.omo/plans/acp-parallel-plan-2-services.md` todo 68 (B13-3)
**Executable drill**: `tests/test_services_backup_restore.py`
**Extends**: P0-04 (todo 4) drill `.omo/evidence/acp-parallel-plan-2-services/drill_b00_backup_recovery.py`
**Interpreter**: `python3.11` · **Run scope**: temporary run roots only — no production
run_root / DB / service / config was read or written, no product code changed.

## Reproduction

```bash
cd <repo>
python3.11 -m pytest tests/test_services_backup_restore.py -q
# 7 passed
```

The drill builds a source run root (`A_source/`) from the **real** Services
initialisers, snapshots it with the SQLite **online backup API** (WAL-safe) plus a
task-tree and secret-file copy, restores it into `B_restored/`, then boots the
**real** `JobManager` against the restored root and checks the recovery order.

## Drill record — restore + reconciliation

| # | Checklist item | Verdict | Evidence (test) |
|---|---|---|---|
| R0 | Coherent restore of the Services DB (jobs + tasks + stage_tasks + structure_source_* + organization_events + projects + data_operations) — per-table row count **and** logical row-content digest identical A↔B | **PASS** | `test_coherent_backup_restore_db_dirs_manifest_secret` |
| R0b | Schema version identical after restore | **PASS** | same test (`get_schema_version`) |
| R1 | Task directories restored (v2 `WORK/00_RUNTIME` + `RESULT`, `job.json` / `task.json` / `events.jsonl`) | **PASS** | same test |
| R2 | Scientific originals + artifacts restored byte-for-byte (`input.xyz`, `RESULT/structures/min.xyz`, `result_manifest.json` sha256 parity) | **PASS** | same test |
| R3 | Secret version placeholder restored identical | **PASS** | same test |
| R4 | Digest-only key store restored: pre-backup plaintext still verifies after restore | **PASS** | same test (`ApiKeyManager.verify` allow) |
| R5 | Plaintext credential never appears in the backup bytes | **PASS** | same test (`_backup_leaks_plaintext` False) |
| R6 | Durable Services prepare operation survives with same `operation_id` / state `queued` | **PASS** | same test (`PrepareOperationService.get`) |
| R7 | Re-applying the same snapshot is idempotent (restored state unchanged) | **PASS** | `test_restore_is_idempotent` |
| L1 | Ledger in scope only if B11 enabled | **DEFERRED / NOT_APPLICABLE** | `test_credit_ledger_subcase_deferred_by_price_gate` |
| D1 | Recovery reconciles external in-flight jobs **before** dispatch opens | **PASS** | `test_restore_reconciles_external_jobs_before_dispatch` |
| D2 | Restored **old** DB never re-dispatches an existing computation | **PASS** | same test (dispatch set stays empty; completed job untouched) |
| D3 | Reconcile adopts the already-submitted LSF job with **no second `bsub`** | **PASS** | same test (`ssh.bsub_calls == 0`) |
| C1 | Control: skip-reconcile + naive requeue → duplicate LSF name (risk exposed) | **FAIL (expected control)** | `test_control_skip_reconcile_duplicate_dispatch` |
| M1 | Component integration only per manifest adoption scope (OS-11) | **PASS** | `test_component_integration_follows_manifest_adoption_scope` |

## Ledger sub-case — DEFERRED (price gate)

**Verdict: `NOT_APPLICABLE` / `DEFERRED`.** B11 (Credit ledger) is gated on an
**approved price policy** (plan Wave 12: "gate=价格政策批准 + B10 通过；未过不开工").
It is not enabled, so the ledger is **not** part of the backup/restore scope and
**no ledger recovery evidence is fabricated**:

* `src/acp_services/workspace/ledger.py` does not exist; `importlib.util.find_spec("acp_services.workspace.ledger") is None`.
* The restored DB contains **no** table matching `ledger|credit|wallet`.
* The drill re-scopes automatically only when the ledger module appears (test would then require a ledger restore-idempotence case).

## Component adoption scope (OS-11) — component-list diff

Adoption follows `docs/reference/next-change/ACP_Services_Component_Manifest.md`.
Manifest status counts (parsed from §0 + main table, asserted consistent):

```
existing=12  candidate=16  pilot=0  approved=0
```

| Component | Manifest status | Packaging declared | Imported by `src/` | Installed (module/binary) | Adopted by ACP |
|---|---|---|---|---|---|
| Caddy (OS-11) | candidate | no | no | no | **no** |
| Prometheus (OS-11) | candidate | no | no | no | **no** |
| restic (OS-11) | candidate | no | no | no | **no** |
| PostgreSQL / Slurm / Podman / SeaweedFS / Lago / Keycloak / pwdlib / Authlib / PyCasbin / ECharts / QCElemental / QCEngine / cclib | candidate | no | no | not as ACP deps (Podman binary is present on this host at `/usr/bin/podman`, but is **not** referenced by ACP code or packaging → unadopted) | **no** |

Component diff result: **every `candidate` component is absent from
`pyproject.toml` / `requirements-node.txt`, absent from `src/` imports, and has
`证据=None`**; the three OS-11 components (Caddy / Prometheus / restic) are also
absent from `scripts/*.sh` and from the host PATH.  No component beyond the
`existing` set is integrated or installed.  `pilot=0 approved=0` — nothing beyond
the adopted set is claimed.

## Guarded order (proof)

1. `JobManager.__init__` runs `_requeue_active_on_startup()` (`manager.py:465`)
   **before** `_dispatch_queued_jobs()` (`manager.py:468`).
2. Inside startup recovery, a STARTING job with a pending submission intent is
   reconciled (`manager.py:_requeue_active_on_startup`) before any
   recover/finalise — recorded call order in the drill:
   `requeue_active_on_startup` → `reconcile:<inflight>` → `dispatch_queued_jobs`.
3. `_dispatch_queued_jobs` scans only `QUEUED` rows, so the restored in-flight
   (STARTING) and terminal (COMPLETED) jobs are never selected; the dispatch set
   recorded by the drill is empty.
4. The reconciliation adopts the existing LSF job (`found → PENDING`,
   `remote_job_id=12345`) with zero `bsub` calls; the persisted `submission_id`
   is unchanged.

## Control — duplicate-dispatch risk (expected FAIL)

The control restores the same snapshot into `C_control/`, naively requeues the
in-flight job to `QUEUED` (simulating recovery without reconciliation), and boot
with startup reconciliation **skipped**:

```
control dispatched: ['20260101_000002_optimize_inflight']
redispatch LSF name == pre-crash LSF name (deterministic submission_id) -> duplicate dispatch
```

The deterministic submission id (`submission_id_for(job_id, attempt)`) yields the
identical LSF name, so a skip-reconcile re-dispatch issues a second `bsub` with
the same `-J acp_<id>` — the duplicate computation P0-04 mandates preventing.
This control `FAIL` is **by design** (risk confirmation), not a drill failure.

## Not claimed

* No production backup system and no atomic rollback guarantee.
* No restic/Prometheus/Caddy integration evidence (candidates, not adopted).
* No B11 ledger recovery evidence (price-gated, deferred).
