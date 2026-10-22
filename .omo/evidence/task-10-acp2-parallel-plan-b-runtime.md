# Task 10 (B09) — local identity and single resource admission at submit and dispatch

- Repo: `/ACPServer/AutoCalcPlatform`
- Branch: `feat/frontend-plan1-closure`
- Base HEAD at task start: `97f142f` (B08)
- Evidence root: `reports/acp2_implementation/bb45a46/B/B09/`
- Live Controller: `http://127.0.0.1:18767` (isolated), run root `/ACPServer/acp2-development/data/b/run-root-b09`
- Candidate served by the live Controller: `58d81ec5ec528577692e6c1aa300c46fbf3b43c6` (frozen H2 checkout); the B09 code was executed from `/ACPServer/AutoCalcPlatform/src` via `PYTHONPATH`.
- Commit: `feat(runtime): enforce identity and admission at submit and dispatch`

## 1. What changed

### Server identity (`src/acp_services/workspace/server_identity.py`, new)

- `ServerIdentity.build()` reuses `identity_mapping.local_identity`, `LocalPrincipal`
  and `EntryAuthorizer` (the frozen identity model).  The principal is the
  Controller process principal (`principal_local`); a request body/tool arg/`_meta`
  can never select a tenant, role or principal.
- `ACP_SERVER_PROJECT` pins the Controller's project.  A project hint may repeat it;
  a different hint is a denial.  With no pin, the legacy tenant-wide local-single
  scope is narrowed by the hint.  This is local single-principal enforcement, not
  multi-tenant security.
- `EntryAuthorizer.authorize_scope()` was added as a thin reuse of the same
  `resolve_scope` decision path for submit/control/prepare/read (the seven resource
  entry classes are unchanged).

### Admission composition (`src/acp_services/runtime/local_admission.py`, new)

- `AdmissionLimits` freezes finite server ceilings: tasks, cores, memory, walltime,
  storage, policy version.  Defaults are generous but finite; `ACP_ADMISSION_*`
  overrides them.
- `LocalAdmission` builds the one `ReservationLedger` (`<run_root>/acp_admission.db`),
  the one `AdmissionController`, the grant/capacity providers and the claim-keyed
  `ReservationPort`.
- The submit reservation is **queued** (storage + active-task slot); the dispatch
  revalidation promotes it and checks actual CPU/memory/concurrency.
- `result["admission"]` is the persisted projection: reservation id, enforcement,
  mode, policy version, principal, verified project, `multi_tenant_security: false`.

### Submit / dispatch order

`POST /api/v1/jobs` now runs:

`_require_submit_access` (server identity) -> normalize -> `claim_idempotency` ->
`ReservationLedger.reserve` (own short transaction) -> staging/work dir ->
`finalize_idempotent_job` (committed `QUEUED` row) -> poller dispatch.

The legacy path (no idempotency key) keeps working and now also admits before
`_allocate_work_dir`; a denial raises `AdmissionRefusedError` and creates nothing.
The claim strictly precedes the reservation.  If admission denies after the claim,
the manager marks the claim `rejected` (terminal) and releases the reservation; a
same-key retry is a deterministic `409 idempotency_conflict` ("rejected by
admission"), never a lease wait.

`JobManager._submit_job` re-validates the reservation after the `QUEUED -> STARTING`
CAS and before any local spawn / remote bsub.  A refusal takes the defined
non-dispatching `STARTING -> FAILED` transition with
`error="admission_revalidation_failed: <reason>"`, releases exactly once and never
spawns QC.

### Guarded entries

- `POST /api/v1/jobs` (submit)
- `POST /jobs/{id}/pause|unpause|continue|cancel|rerun`
- `POST /jobs/{id}/prepare`
- `GET /jobs/{id}/prepared/{resource}`
- MCP tool calls reach the same Controller entries, so the Controller-side
  authorizer/admission is the authority.

Cross-project control/prepare/read return the **same 404 shape** as an unknown job
(`Job not found: <id>` / `not_prepared`), so another project's task existence is not
leaked.  Control endpoints use `require_exists=False` so the Runtime command use
case keeps owning its own not-found response.

### Release / usage

- Terminal side effects (poll terminal, fake completion, restart finalization,
  cancel, local poll failure, submission failure, aborted cancellation) record the
  trusted usage receipt once and release the admission reservation.
- `admission_usage_receipts` is keyed by `usage_id_for(job, attempt, submission)`;
  `INSERT OR IGNORE` means a duplicate receipt cannot double-charge.
- `ReservationLedger.release` is idempotent; a second release returns `False` and
  changes nothing.

### MCP budget

`McpServer` no longer constructs `WorkspaceBudget` by default (`budget=None`).
The Controller's server-side ledger is the only admission authority; an MCP
`resources.cores` hint is forwarded but cannot override the server grant.
`WorkspaceBudget` remains an explicit cache/test adapter (the existing
`test_services_mcp.py` budget tests pass with an explicitly injected budget).

### Capabilities

`GET /api/capabilities` now reports:

```json
{
  "admission_enforcement": "local-single-enforced",
  "model_token_enforcement": "NOT_VERIFIED",
  "multi_tenant_security": false,
  "admission": {
    "mode": "quota-only",
    "quota_enforced": true,
    "recorded_only": false,
    "limits": {"max_tasks": 4, "max_cores": 2, "...": "..."},
    "verified_project": "proj-a"
  },
  "identity": {"principal_id": "principal_local", "project_hint_policy": "narrow-only"}
}
```

## 2. Live denial matrix (isolated Controller, `run-root-b09`)

Source: `denial-matrix.json`, `no-side-effect-proofs.json`.

| Scenario | HTTP | Reason | No side effect |
|---|---|---|---|
| over cores (legacy, no key) | 429 | `exceeds_cores` | PASS |
| claim-then-deny | 429 | `exceeds_cores` | PASS |
| same-key retry | 409 | `idempotency_conflict` / rejected claim | PASS |
| project hint widens pinned scope | 403 | `access_denied` | PASS |
| cross-project artifact read | 404 | `Job not found` | PASS |
| cross-project prepare | 404 | `resource_not_ready` | PASS |
| cross-project cancel | 404 | `Job not found` | PASS |
| over jobs (`max_tasks=4` occupied) | 429 | `exceeds_concurrency` | PASS |
| allowed `fake` submit | 201 | admitted | expected side effect |

Before/after counters cover: jobs rows, `job.json` work dirs, reservation rows,
active reservations, usage receipts and reservation-claim rows.  Every denied
scenario has identical before/after counters.  The allowed submit is the only row
with `no_side_effect: false`.

## 3. Claim-then-deny proof

`claim-then-deny.json`:

- first request: 429 `admission_denied` (`exceeds_cores`);
- `idempotency_records.state = "rejected"`, `scope_id = "principal_local\u001fproj-a"`;
- retry with the same key: 409 `idempotency_conflict` with
  `"idempotency claim was rejected by admission"`;
- no job row, no work dir, no reservation row, no usage receipt.

## 4. Reservation / replay / usage proofs

`reservation-ledger.json` and `usage-counters.json`:

- allowed submit: one committed claim, one reservation row, one work dir, one
  usage receipt;
- replay: same `job_id`, `idempotent_replay: true`, reservation count still 1,
  active reservations 0, usage receipts still 1;
- `record_usage_once("usg_b09_live_dup", ...)` first returns `True`, second
  returns `False`; totals increase once only (`receipts 1 -> 2` for the one
  synthetic receipt);
- `release` is idempotent (a second `release_job` returns `False`).

## 5. Dispatch revalidation proof

`tests/test_acp2_runtime_admission.py::test_dispatch_revalidation_failure_is_non_dispatching`:

- a queued reservation is created for a real job;
- the local capacity is shrunk to zero;
- `JobManager._submit_job` transitions the job `QUEUED -> STARTING -> FAILED` with
  `admission_revalidation_failed: capacity_cores`;
- the runner `submit` is never called (zero QC spawns);
- the reservation state is `released` and a second release returns `False`;
- no remote submission state is written.

## 6. MCP budget removal proof

`tests/test_acp2_runtime_admission.py::test_mcp_budget_is_not_in_the_final_admission_path`:

- `McpServer.__init__` `budget` default is `None`;
- an MCP submit with `resources.cores = 999999` is forwarded, not refused locally;
- an explicitly injected `WorkspaceBudget(max_cores_per_submission=1)` still
  refuses, proving the adapter is opt-in and not on the Controller path.

## 7. Tests / lint

- `pytest tests/test_acp2_runtime_admission.py tests/test_services_admission.py tests/test_services_quota.py tests/test_services_entry_authz.py -q`
  -> **113 passed** (`pytest-admission.txt`).
- B08 idempotency + MCP guards:
  `pytest tests/test_acp2_runtime_persistent_idempotency.py tests/test_services_mcp.py -q`
  -> **47 passed** (`pytest-b08-guards.txt`).
- `ruff check` + `ruff format --check` on every changed file -> clean (`ruff.txt`).

## 8. Cleanup

`cleanup.json` / `controller.txt`:

- Controller PID recorded, started detached with `setsid`, health verified at
  `/api/health`;
- stopped with `SIGTERM`; PID gone; port `18767` free;
- production `8765`, `/var/lib/acp/runs` and the shared run root were never touched.

## 9. Assumptions / limits

- This is **local single-principal** enforcement.  `multi_tenant_security` is
  explicitly `false`; model-token metering is `NOT_VERIFIED`.
- LSF is absent in this environment, so `bsub` enforcement is `NOT_VERIFIED`.
  Denied submits never reach the remote runner; the live matrix proves no job /
  work dir / reservation is created.
- The pre-existing `tests/test_services_runtime_usecases.py::test_entry_files_do_not_bypass_runtime`
  failure is caused by B06 ControllerClient `.submit()`/`.cancel()` calls in
  `src/acp/cli.py` and reproduces against `git show HEAD:src/acp/cli.py`; it is not
  a B09 regression.  The API forwarding tests in that file are green.
- `tests/test_acp2_runtime_persistent_idempotency.py::test_crash_after_reservation_before_job_row`
  has a 0.05 s lease and can flake; single and full reruns pass.
