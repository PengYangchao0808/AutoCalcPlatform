# Task 9 (B08) — persistent idempotent submission and unknown-result reconciliation

- Repo: `/ACPServer/AutoCalcPlatform`
- Branch: `feat/frontend-plan1-closure`
- Base at task start: `f67f90f` (B06); B07 committed `ede698b` concurrently before B08 evidence
- Candidate revision used for live acceptance: `ede698bb96fd51cb2a5f347185a15afcd03f8823`
- Evidence root: `reports/acp2_implementation/bb45a46/B/B08/`

## 1. What changed

### Migration 022 (additive, forward-only)

`src/acp_services/store/migrations.py` adds `022_idempotency_records`:

- `claim_id` PK, `scope_id`, `action`, `idempotency_key`, `request_digest`,
  `state`, `owner_token`, `lease_epoch`, `lease_expires_at`, `task_id`,
  `operation_id`, `created_at`, `updated_at`
- `UNIQUE (scope_id, action, idempotency_key)`
- indexes on `task_id`, `(state, lease_expires_at)`, `(action, idempotency_key)`

The live DB dump (`migration.json`, `db-rows.json`) shows migration `022` applied
at `2026-10-22T16:13:15.187670+00:00`; migrations `001–021` are unchanged.

### `src/acp_services/runtime/idempotency.py`

Pure contract module (no DB, no second ledger):

- frozen `IdempotencyClaim`, `IdempotencyOutcome` (`created` / `replay` /
  `conflict` / `in_progress`) and `ClaimState`
  (`claimed|committed|released|rejected|expired`)
- `canonical_request_digest` / `canonical_payload`: sorted mappings, ordered
  sequences, floats rendered as exact decimal strings (no JSON float), bytes
  base64, top-level `idempotency_key` excluded
- `resolve_scope_id(principal, project)` — verified principal/project scope,
  never a client field
- `IdempotencyContext` (optional submit context) and `ReservationPort` /
  `LedgerReservationPort` adapter over the existing `ReservationLedger`
- terminal `rejected` state for admission denial; `released`/`expired` remain
  takeover-able

### `JobStore` (the single ledger)

`src/acp_services/store/store.py` adds, each in a short `BEGIN IMMEDIATE`
transaction:

- `claim_idempotency(scope, action, key, digest, lease_seconds=...)`
  - insert `claimed` + owner token + epoch `0`
  - live claim -> `in_progress` (owner token withheld)
  - committed -> stored TaskRef replay
  - digest/scope mismatch -> `conflict`
  - expired/released claim -> epoch-CAS takeover (`lease_epoch + 1`, new token)
- `finalize_idempotent_job(record, claim_id, owner_token, lease_epoch)`
  - re-checks state/token/epoch/live lease
  - inserts the `QUEUED` job row and updates the claim to `committed` in the
    same short transaction
  - stale owner/epoch/expired lease -> `IdempotencyLeaseError`, no write
- `release_idempotency(...)` with owner fencing; committed rows are immutable
- `expire_stale_idempotency_claims()` returns expired claims for staging/
  reservation sweep
- read helpers: `get_idempotency_claim`, `find_idempotency_claim`,
  `find_idempotency_claim_any_scope`, `get_idempotency_claim_for_task`,
  `list_idempotency_claims`

### Submit protocol (staged, short transactions)

`JobManager.submit(spec, group_id=None, *, idempotency=None)`:

1. legacy callers with no context take `_submit_legacy` unchanged
2. normalize/validate spec
3. `claim_idempotency` (short tx) — `scope/action/key/digest`
4. optional `ReservationLedger` reserve (own short tx, claim-keyed; existing
   ledger, same table family, no second reservation path)
5. staging dir `$ACP_RUN_ROOT/.staging/<claim_id>`
6. allocate final work dir + `.claim_binding` (claim id/attempt/digest)
7. `finalize_idempotent_job` (short tx: job row + committed claim)
8. adopt staging into the final dir, write `job.json`/events/task index
9. the committed `QUEUED` row is the only dispatch intent; the QUEUED dispatch
   pass (and poll loop) starts exactly one submission worker
10. replay resumes from the last committed stage; stale claims are expired,
    their staging dirs removed and their reservations released

Cross-storage owners/compensation:

| Window | Owner / compensation |
|---|---|
| reservation before job | manager except-path releases reservation + `released` claim; sweep also releases on expiry |
| job before directory adoption | claim committed, `job.json` absent; `_dispatch_queued_jobs` adopts before dispatch |
| directory bound before completion fact | `.claim_binding` identifies claim/attempt; takeover reuses the bound dir instead of allocating `__02` |
| job committed before dispatch | committed `QUEUED` row is the sole intent; adoption/`job.json` is the dispatch gate |
| local/bsub started with lost response | existing local `run.lock`/PID adoption and remote `submission_id`/attempt reconciliation; no fallback submit |
| takeover vs old owner | epoch/token fencing: stale finalize/release/cleanup/dispatch fail |

### Entry wiring

- `JobSubmissionPort.submit` / `RuntimeCommands.submit` accept optional
  `idempotency`; legacy calls pass no extra kwarg to the port (tests pin exact
  forwarding)
- `POST /api/v1/jobs` reads `Idempotency-Key` header or body
  `idempotency_key` (disagreement -> 400); replay -> original job with
  `idempotent_replay=true`; digest/scope conflict -> HTTP 409
  (`idempotency_conflict`); concurrent live claim -> 409
  (`idempotency_in_progress`, `retryable=true`)
- MCP `acp_submit` sends the key in the body to the Controller; the in-memory
  registry is only a payload-digest cache (same key + changed payload bypasses
  the cache and reaches the Controller 409). Error codes map to
  `revision_conflict`.

## 2. Acceptance

Command (real Controller on 18766, separate run root):

```
PYTHONPATH=/ACPServer/AutoCalcPlatform/src \
/ACPServer/acp2-development/venvs/b/bin/python scripts/acceptance/acp2_runtime.py \
  --scenario submit-replay \
  --controller http://127.0.0.1:18766 \
  --launch /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B08/mcp-launch.json \
  --fixture /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B08/fixtures/singlepoint.xyz \
  --out /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/B/B08 \
  --timeout 600
```

Result: `PASS: submit-replay/main`, exit `0`.
Receipt: `submit-replay-main-20261022T161353Z-449619-4d977737.json`.

Assertions in the receipt:

- `two_mcp_processes_same_task_ref` PASS — both OS processes returned
  `20261023_001354_001_singlepoint`
- `exactly_one_new_task` PASS — `before=0 after=1`
- `exactly_one_dispatch` PASS — one `job.started` event
- `same_key_changed_payload_conflict` PASS — Controller 409
- `same_key_changed_scope_rejected` PASS — Controller 409 under a changed
  verified project scope

The acceptance uses the real local singlepoint path; the job completed
(`db-rows.json`: `status=completed`, `attempt=1`).

## 3. Concurrency (two real MCP OS processes)

`concurrency.json` is the receipt projection. Two independent
`acp mcp serve` processes were initialized, then submitted the same key behind
a barrier. Both returned the same TaskRef; the Controller task count grew by
exactly one and the job's event log contains exactly one `job.started`.

The pytest integration test repeats this against a fresh Controller and also
restarts the Controller on the same run root/DB (see §5).

## 4. Crash-window receipts

Raw dumps: `crash-windows/*.json` (the raw pytest temp run roots were removed
after dumping).

| Window | Receipt | Observed |
|---|---|---|
| (a) crash after reservation, before job row | `test_crash_after_reservation_b0.json` | no `jobs` row; claim `expired`; reservation `released`; no task dirs; staging empty |
| (b) crash after job row, before dispatch | `test_crash_after_job_row_befor0.json` | one job completed after restart/adoption; one `job.created`; staging empty |
| (c) directory bound before completion fact | `test_crash_after_directory_bin0.json` | claim takeover (`lease_epoch=1`) reused the single bound dir; no `__02`; one job |
| (d) Controller restart + same-key replay | `restart-replay.json` | same TaskRef `20261023_002746_001_singlepoint`, same job count, `idempotent_replay=true`, port freed between starts |
| local spawn lost response | `test_local_spawn_lost_response0.json` | spawn counter = 1 after child SIGKILL and replay; existing restart recovery marks the lost attempt FAILED, never resubmits |
| remote `bsub` lost response | `remote-window.json` | **NOT_VERIFIED** — LSF absent; no `bsub` was executed; no fallback submit added; existing `submission_id`/attempt protocol retained |

The crash children were killed with `os._exit(137)` at named fault points
(`idempotency.after_reservation`, `idempotency.before_finalize`,
`idempotency.after_finalize`, `dispatch.after_local_spawn`), i.e. real process
death, not an in-process exception.

## 5. Restart replay

`restart-replay.json`:

- first Controller start: TaskRef `20261023_002746_001_singlepoint`, 1 job
- stop -> port free (`port_free_after_stop=true`)
- new Controller, same DB/run root: replay same key -> same TaskRef, still 1
  job, `idempotent_replay=true`
- final stop -> port free

`restart-db-rows.json` shows one committed claim and one job in that DB.

## 6. Tests / lint

- `pytest-idempotency.txt`: `tests/test_acp2_runtime_persistent_idempotency.py`
  — 8 passed (store CAS/takeover/fencing, four crash windows, local-spawn
  lost response, remote identity reuse, two-MCP-process replay + restart)
- `pytest-guards.txt`:
  `test_services_cas_fidelity.py`, `test_services_recovery_semantics.py`,
  `test_services_worker_contract.py`, `test_services_admission.py`,
  `test_services_quota.py` — 153 passed
- `ruff.txt`: `ruff check` and `ruff format --check` on every changed file —
  pass
- `tests/test_acp2_runtime_mcp_backends.py::test_mcp_and_http_submit_parameters_match`
  — pass after its capture stub accepted the new optional `idempotency` kwarg
- Pre-existing, unrelated baseline failure noted: `tests/test_acp_api_v1.py::test_run_irc_endpoint`
  (and `test_services_runtime_usecases.py::test_entry_files_do_not_bypass_runtime`)
  fail at `ede698b` without B08 changes (extra `V1JobCreatedResponse` default
  fields / `ControllerClient.submit` scan). Not introduced or masked here.

## 7. Cleanup

`cleanup.json`: port 18766 free, port 18765 (B07) free, production 8765 never
touched, no `uvicorn`/`acp mcp serve` processes left, no staging dirs left.

## 8. Assumptions and rejected alternatives

- `scope_id` is `principal_local\x1f<resolved project>` in the local-single
  tier. The project is server-resolved; a changed project is a changed scope
  and is rejected with 409 rather than silently creating a second job.
- The reservation ledger is optional in B08 (B09 owns enforced admission).
  Production callers leave it `None`; the claim-keyed ledger path is exercised
  by the crash-window test and is ready for B09 wiring.
- Rejected: one long write transaction across the job DB and reservation
  ledger. It would hold a job-DB write lock while another ledger opens its own
  write connection; the staged short-transaction protocol above is used
  instead.
- Rejected: a second dispatch outbox. The committed `QUEUED` job row is the
  single durable dispatch intent; adoption (`job.json`) is the dispatch gate.
- Rejected: a 'fallback submit' for unknown remote results. The existing
  `submission_id`/attempt reconciliation remains authoritative; the idempotent
  replay returns the stored TaskRef and never submits again.
- No secrets are stored in the ledger or staging dirs. The request digest is
  content identity, not a credential.
