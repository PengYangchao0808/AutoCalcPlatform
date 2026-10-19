# Services Fault-Injection Matrix (B13-2 / plan todo 67)

**Owner test:** `tests/test_services_fault_matrix.py`
**Guard:** `test_fault_matrix_report_lists_every_scenario` — fails if any scenario id
below loses its test or its row here. "Not observed" is never a pass.

**Interpreter:** `python3.11` · **Isolation:** tmp SQLite DBs + tmp dirs + in-process
protocol doubles; no real cluster, no product-code edit.

Every scenario injects a real fault through a public seam (SQLite CAS, the
transactional outbox, the transfer pipeline, the frozen worker, the access policy,
the prepare-operation state machine, the schema migrator). Assertions use the
required shape: **no state change** / **no lost fact** / **reconcilable**.

## Scenario matrix

| ID | Scenario | Injected fault | Assertion (form) | Result |
|----|----------|----------------|------------------|--------|
| S01 | Unknown submission (valid lease → never `not_accepted`) | submit intent with a live persisted lease; controller reconcile cannot see the job, returns `unknown` | no state change — `submit_lease_valid` True within lease; job stays `STARTING`; zero bsub | **PASS** |
| S02 | Duplicate / out-of-order receipts | same receipt replayed; stale attempt / stale release arrive late; duplicate CAS advance with a spent revision | no state change — duplicate receipt does not re-execute; stale receipts rejected and CAS port untouched; second CAS refused | **PASS** |
| S03 | Process crash / lost connection | worker claims a lease then the service is closed (crash); fresh service over the same DB | no lost fact — undecided row replayed to `queued`, fresh owner generation, availability committed once | **PASS** |
| S04 | Disk full | artifact write raises `OSError(ENOSPC)`; state-machine executor raises the same | no state change — no partial publish / publication record / `.tmp` residue; operation ends terminal `FAILED` (error `OSError`), never READY | **PASS** (finding F-1) |
| S05 | Transfer interruption | transient source failure, then a partial (size-mismatch) read, then a healthy retry | reconcilable — retryable rejection, retry publishes exactly once, no `.tmp` residue on any leg | **PASS** |
| S06 | Old worker still writing | generation bumped mid-transfer; stale attempt after the fence advanced; conflicting stale receipt | no state change — late write physically discarded, stale attempt never reaches the source, existing receipt bytes untouched (`ReceiptConflictError`) | **PASS** |
| S07 | Permission revocation immediately effective | grant revoked; publish denied after download; authorization version bumped at completion | no state change — access allow→deny instantly; no publish / record; completion rolled back (`PreparePublishRejectedError`, still `running`) | **PASS** |
| S08 | Budget race (B11 credit ledger) | reservation competition | **DEFERRED** — B11 ledger is price-policy-gated (+ B10) and `acp_services.workspace.ledger` is absent; the adjacent B02-2 storage budget ledger is a different mechanism | **DEFERRED** (gate reason recorded; NOT faked) |
| S09 | Post-recovery external-job reconciliation | restart with an indeterminate external submission (`submit_state=unconfirmed`) | reconcilable **before dispatch** — reconcile runs first, job deferred (not restart-failed), zero duplicate bsub | **PASS** |
| P01 | CAS committed, notification pending (outbox backlog replay) | commit succeeds, delivery never runs before the crash | no lost fact — pending row visible, event log empty; restart sweep delivers exactly once; revision unchanged | **PASS** |
| P02 | Outbox delivered, not marked | durable append succeeds, the ack is dropped | reconcilable — row stays pending; retry recognised as duplicate; single append; state unchanged | **PASS** |
| P03 | File published / DB unregistered · DB record / file missing | orphan cache file with no row; a `PUBLISHED` row whose file was deleted | reconcilable — classified `unregistered` / `missing`; orphan never deleted; row flipped to `missing` | **PASS** |
| P04 | Schema expand / contract + code rollback | legacy schema expanded by `migrate()`; then a contracted DB whose migration ledger claims all applied | expand idempotent + rollback-tolerant (old column set still writes); contraction **detected** (`sqlite3.OperationalError`), never silently masked | **PASS** (finding F-2) |

## Findings

**F-1 — `TransferPipeline.prepare` propagates `OSError` on a failed artifact write.**
On `ENOSPC` (and any non-`TransferSourceError` `OSError`) the pipeline's inner
`finally: _remove_tmp(...)` cleans the tmp file and the `OSError` surfaces to the
caller instead of being mapped to a typed `RejectionCode`. Safety holds (no
partial publish, no `.tmp` residue, no publication record) and the prepare-operation
worker (`operations.PrepareOperationWorker._execute`) catches the exception and
records a **retryable** failure, so the state machine never marks the artifact
ready. No fix applied here: mapping the write error to a typed rejection would be a
product behaviour decision outside this matrix's scope; recorded for the orchestrator.

**F-2 — the schema migration ledger is forward-only (contraction is not auto-repaired).**
`acp_services.store.migrations.migrate()` skips any migration whose id is already in
`_schema_migrations`. If a database is *contracted* (a column current code needs is
missing) while the ledger still claims the migration applied, `migrate()` reports
`0` applied and does **not** restore the column; the next current-code write raises
`sqlite3.OperationalError` (detected, never masked). This is the intended
forward-only design — a self-healing destructive repair would be far riskier — so it
is recorded as a documented limitation, not a defect. A contracted/rolled-back
database must be recovered from the consistent backup path (todo 68), not by
re-running migrations.

## Repair loop closure

* No product-code defect was found that is covered by an existing guard, so **no
  product edit** was made; the findings above are recorded for the orchestrator.
* S08 remains **DEFERRED** with its gate reason; it must be re-opened (with real
  reservation-competition injections) once the B11 price policy is approved and the
  credit ledger lands.

## Regression status (named suites)

`python3.11 -m pytest tests/test_execution_integrity_e2e.py
tests/test_services_event_ordering.py tests/test_services_prepare_operations.py
tests/test_services_transfer_verify.py tests/test_services_worker_contract.py
tests/test_acp_services_isolation.py -q` → **113 passed, 1 failed**.

The single failure is `tests/test_services_worker_contract.py::
test_runtime_package_has_no_acp_or_db_access`, and it is **pre-existing at HEAD
(`47774f5`) and outside this task's file ownership** — it is finding F-3 below, not
a regression introduced here (these new files never touch `src/acp_services/runtime/**`).

**F-3 — the runtime-package guard contradicts committed DB-backed runtime modules
(cross-lane, reported not fixed).** `scan_runtime_source` bans `import sqlite3`
(and the `acp_jobs.db` string) in **every** `src/acp_services/runtime/**.py`, but
`runtime/admission.py` (commit `38ed349`, todo 49) and `runtime/quota.py` (todo 55)
are deliberately DB-backed and import `sqlite3`, and `runtime/isolation.py`
references the `acp_jobs.db` string. The guard is therefore RED at HEAD independent
of this task (reproduced by running the test alone). Resolving it is a cross-lane
architecture decision — either the guard gains an explicit DB-backed allowlist, or
the DB-backed modules move out of `runtime/` — and neither is unambiguous, so per
the task's "report first, do not weaken guards" rule it is **reported, not fixed**.


## Reproduce

```bash
python3.11 -m pytest tests/test_services_fault_matrix.py -q
```
