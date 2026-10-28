# Trusted usage receipt & settlement binding v1 (M0-07 / G26)

Status: **delivered in code, acceptance evidence `NOT_VERIFIED`** (no live
Controller run with this contract in this change set).
Owner: B. Supersedes the pre-M0-07 `POST .../settle` body, which carried a
caller-supplied `trusted_source` string as if it were authorization.

Machine-readable block:

```json
{
  "schema": "acp2.trusted-usage-receipt.v1",
  "wire": {
    "route": "POST /api/v2/research/budgets/{budget_id}/settle",
    "additionalProperties": false,
    "request_fields": [
      "receipt_id",
      "reservation_id",
      "job_id",
      "attempt",
      "submission_id",
      "release_id",
      "release_digest",
      "owner",
      "fencing_token",
      "plan_identity",
      "step_identities",
      "run_id",
      "usage"
    ],
    "removed_fields": ["trusted_source"],
    "response_schema": "acp2.research-budget-settlement.v1",
    "response_fields": [
      "schema",
      "applied",
      "duplicate",
      "producer",
      "trusted_source",
      "chain_digest",
      "settlement",
      "budget"
    ]
  },
  "producer_capabilities": {
    "worker": "usage.produce.worker",
    "service": "usage.produce.service",
    "proxy": "usage.produce.proxy"
  },
  "producer_sources": {
    "worker": "worker-receipt",
    "service": "service-receipt",
    "proxy": "c-proxy-usage",
    "internal": "controller-terminal-receipt"
  },
  "internal_only_sources": ["controller-terminal-receipt"],
  "unassignable_capabilities": ["controller-terminal-receipt"],
  "refusal_codes": [
    "usage_producer_untrusted",
    "usage_capability_missing",
    "usage_capability_ambiguous",
    "usage_receipt_invalid",
    "usage_reservation_not_found",
    "usage_reservation_job_mismatch",
    "usage_reservation_mismatch",
    "usage_attempt_conflict",
    "usage_receipt_conflict",
    "usage_budget_mismatch",
    "usage_budget_released",
    "usage_not_dispatched"
  ],
  "idempotency_keys": ["receipt_id"],
  "migrations": {
    "identity_db": 25,
    "admission_db": 26,
    "jobs_db_unchanged": 24
  }
}
```

## Rules

1. **The wire cannot name its own authority.** `trusted_source` is removed from
   the body (`extra="forbid"` ⇒ a legacy client that still sends it gets `422`).
   The recorded trusted source is `producer_sources[producer]` of the resolved
   producer, always server-derived.
2. **Producer capability, not `AccessAction`.** An API key/service credential may
   carry dedicated `usage_capabilities` metadata (`usage.produce.worker|service|
   proxy`). It is additive next to `read`/`write`/`control`: it never widens an
   action ceiling and withholding it never restricts a read/write entry. A
   project `write` key with no capability is refused with `403
   usage_capability_missing`. Exactly one producer capability per credential
   (`usage_capability_ambiguous` otherwise).
3. **`controller-terminal-receipt` is internal-only.** It is not an assignable
   capability: `ApiKeyManager.generate`/`IdentityLifecycle.issue_key` refuse it
   (`usage_capability_not_assignable`) and no HTTP route can select it. The
   Controller process (no per-request credential, `local-single`) is the only
   producer of that source.
4. **Execution identity is verified, not asserted.** An external receipt must
   present the frozen attempt identity: `job_id`, `attempt`, the deterministic
   `submission_id = sub_ + sha256("job:attempt")[:16]`, release id/digest, owner,
   fencing token, plan identity and step identities. The ledger compares it with
   the **immutable binding persisted for its own `(job, attempt)`** (reusing
   `runtime/receipts.verify_historical_receipt`), never with the *current*
   attempt. A late attempt-1 receipt therefore still books after attempt 2
   started, once, and never advances task state.
5. **A dispatch fact is required.** The advertised attempt must have been
   declared by the Controller at dispatch (`admission_attempt_declarations`), the
   admission reservation must exist and be bound to the job
   (`admission_reservation_job`), and a referenced budget reservation must be
   `dispatched`/`settled`. Otherwise: `usage_not_dispatched` and nothing is
   written.
6. **One transaction, one idempotency key.** The usage row and the research-budget
   receipt are inserted in the same `BEGIN IMMEDIATE` transaction on the
   admission DB. `receipt_id` is the key: a re-delivery returns `duplicate=true`
   and charges nothing; the same id with a **different** payload is
   `usage_receipt_conflict` (never a silent no-op).
7. **Identity is persisted for post-purge verification.** Reservation/receipt/
   usage rows store attempt, submission, release, plan identity, step identities,
   the chain digest (canonical identity projection), producer and receipt id at
   posting time. `ReservationLedger.verify_usage_history(job_id)` re-verifies it
   from the database only — it never reads a work directory, so a purged task's
   booked usage stays auditable.
8. **Failure does not escape settlement.** Failed science still books what ran;
   publication failure is a *held* settlement (`budget_held`), never a refund or
   re-charge. Accounting never blocks science (`metering_decoupling`); the
   `UsageIngestChannel` remains a decoupling seam and is not an authority.

## Negative vectors (see `tests/test_acp2_runtime_usage_receipts.py`)

| Vector | Expected |
|---|---|
| member/project `write` key POSTs a receipt | `403 usage_capability_missing`, zero receipts |
| body contains `trusted_source` | `422` (unknown field), zero receipts |
| fabricated `job_id` / never-dispatched attempt | `404`/`409`, zero receipts |
| already-bound attempt with another release id/digest | `409 usage_attempt_conflict` (`stale_release`) |
| second attempt bound first, late attempt-1 receipt | booked once; task row status/revision unchanged |
| same `receipt_id`, different payload | `409 usage_receipt_conflict` |
| purge job row + work dir, then verify | `verify_usage_history.verified = true` |

## Known limits (must not be upgraded without evidence)

* Model-token enforcement remains `NOT_VERIFIED`; `model_request` is
  `recorded-only` (C-owned proxy).
* No live Controller run of this contract is recorded in this change set; the
  acceptance harness assertion is `b17.project_write_cannot_settle_usage` +
  `b17.usage_receipt_settled_from_capability`, evidence kind `fixture` until
  executed and filed under `reports/`.
