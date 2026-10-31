# Research session, submit intent and crash-point recovery (v1)

- **Contract id**: `acp2.runtime.research-session.v1`
- **Owner**: B + C-RPH (M1-02, gaps G09/G14)
- **Status**: frozen (implementation root `src/acp_services/runtime/research_session.py`;
  RPH keys in `rph/packages/research/src/submit-intent.ts`)
- **Consumes**: `decision-freeze/identity.v1.json` D-IDENT-04 (planning claim),
  D-IDENT-05 (model receipt), D-IDENT-07 (plan digest in the RPH key),
  D-IDENT-06 (frozen RPH key segments), D-IDENT-08 (request digest v2)
- **Does not cover**: extension negotiation / wire schema (`p2-research-operations.v1.json`,
  M1-01), evidence projection semantics (M1-03), submit policy and budget shapes (M1-04)

This document freezes the research-session fields, the two server-derived
planning namespaces and the recovery table that every crash point must satisfy.
The Python module renders these tables as constants; the test module
`tests/test_acp2_research_session_contract.py` parses this document and fails on
any drift, so the prose cannot rot away from the code.

## 1. Identity is allocated before the first plan

A research session carries `principal / project / research / session` **before**
a Proposal, an approval or a TaskRef exists. The planning budget, the planning
claims and the model receipts of that phase are addressed by this tuple only.

- `principal_id` and `project_id` come from the server-resolved
  `CommandIdentity` (M0-04); an unresolved project falls back to the command
  default exactly like the idempotency scope.
- `research_id` / `session_id` are derived with the single canonical-digest
  implementation (`runtime/canonical_digest.py`), never by a second hasher.
- A client payload can never set `project_id`, `research_id` or `session_id`.
  Its only lever is a non-secret `session_seed` that can *separate* two sessions
  of the same principal/project; it cannot select a principal or another
  principal's research.
- The tuple never contains `|` (claim segment rule) and `research_id` matches
  C's `^res-[A-Za-z0-9][A-Za-z0-9._:-]*$`.

| Field | Meaning |
| --- | --- |
| `principal_id` | Verified principal that owns the session (server-resolved). |
| `project_id` | Server-resolved project scope; never a client-selected tenant. |
| `research_id` | Stable research identity within the project, allocated pre-plan. |
| `session_id` | One agent/chat working session on that research; not authority. |
| `round` | Research round index (integer, starts at 0). Not a task attempt. |
| `revision` | Optimistic CAS counter of the session value (starts at 1). |

## 2. Planning claim and model receipt namespaces

Both are **planning reservation** identities: they are charged before any task
exists and they never name a job or an attempt. They must not be written to the
submit idempotency ledger (`idempotency_records.action = submit`) and must never
be an empty id that escapes a partial index.

Grammar (every segment is non-empty, contains no `|`, and is server-derived):

```
plan|<project>|<research>|<session>|<round>|<call_seq>
modelrcpt|<project>|<research>|<session>|<round>|<call_seq>|<request_digest>
```

- `call_seq` is a positive integer, monotonically increasing per
  `(project, research, session, round)`.
- `request_digest` is `sha256:<64 hex>` over the canonical planning request
  (produced by `runtime/canonical_digest.py`; no private hashing).
- The planning budget id is server-derived from `project + research/session`
  (not from a `proposal_ref` that does not exist yet).
- The receipt is the settlement key of exactly one model call; unknown,
  stream-interrupted or unknown-outcome calls stay reserved and reconcile.
  A scientific rejection deletes no already-incurred model usage.

## 3. Submit intent fields

The submit key is **RPH-derived and opaque**. The platform stores it, echoes it
and never completes, rewrites or re-derives it. RPH binds
`project + research + round + plan_digest` and persists the key before the call;
a changed `plan_digest` invalidates the old approval and opens a new
round/revision with a new key (D-IDENT-07).

| Field | Meaning |
| --- | --- |
| `intent_key` | Opaque client-derived idempotency key, forwarded verbatim. |
| `project_id` | Session project; the intent may not cross it. |
| `research_id` | Session research identity. |
| `session_id` | Session identity that derived the key. |
| `round` | Research round the intent belongs to. |
| `plan_digest` | Approved plan the intent is bound to; a change needs a new round. |
| `approval_ref` | Approval that authorized this exact plan digest (empty before approval). |
| `revision` | Optimistic revision of the intent record (starts at 1). |
| `state` | `prepared` → `submitted` → `settled`, or `superseded`. |

## 4. Task binding, wait cursor, evidence ref and decision

| Field | Meaning |
| --- | --- |
| `project_id` | TaskRef project; must equal the session project. |
| `job_id` | Platform job id of the executed attempt. |
| `attempt` | Attempt number (>= 1). |
| `release_id` | Verified execution release the attempt ran under. |

| Field | Meaning |
| --- | --- |
| `cursor` | Opaque research cursor (`r1.<base64url>`); never parsed for authority. |
| `recorded_revision` | Session revision at which the cursor was stored. |

| Field | Meaning |
| --- | --- |
| `evidence_id` | Stable evidence identity within the record. |
| `kind` | Evidence kind label (`result_manifest`, `acceptance_receipt`, …). |
| `sha256` | Digest-bound content hash; an unverified read is not evidence. |
| `relative_path` | Evidence-root relative path, or `null` for remote-only artifacts. |

The research decision vocabulary is exactly `accept`, `revise`, `reject`,
`defer`, `escalate` (identical to C's ResearchRecord schema) and is disjoint
from every task/job status. `record_revision` stays the optimistic concurrency
counter of the *record*, not of the session (the session uses `revision`).

### 4.1 Record create / read / CAS ownership

The record itself keeps its single implementation in the Data boundary:
`acp_services/data/research_resources.py` owns `create` / `read` /
`compare_and_swap` / `revoke` / `versions`, the project-scoped `resource_id`
(`research_resource_key`) and the `record_revision` CAS. This document freezes
only the session fields that surround it (`round`, `submit_intent`,
`task_binding`, `wait_cursor`, `evidence_ref`, `decision`, `revision`); it adds
no second record store. A CAS is accepted only with the observed
`record_revision`, and the session's `evidence_refs` are what the CAS publishes.

## 5. Crash-point recovery table

Recovery is decided from durable facts only: the RPH-persisted intent, the
approval CAS row, the bound TaskRef, the research-record revision and the event
cursor. In-memory state or a local WAL is never authority. Research resume
**never** calls `JobManager.resume()`: that method is review-only
(`WAITING_REVIEW`) and a research session may resume around a queued, running or
completed task without changing its control state.

| Stage | Already persisted | Allowed actions | Forbidden actions | Next action |
| --- | --- | --- | --- | --- |
| `before_submit` | identity tuple; planning claim + receipt per model call; approved approval bound to `plan_digest`; RPH intent key | `derive_intent_key_and_persist`, `resubmit_same_intent_key`, `replay_cursor_or_snapshot` | `derive_a_second_intent_key_for_the_same_plan`, `submit_without_a_persisted_intent_key`, `call_job_manager_review_resume` | `submit_once_with_the_persisted_intent_key` |
| `submitted_unacked` | intent marked submitted; no receipt/TaskRef yet; the ledger row for that key, if the request landed | `resubmit_same_intent_key`, `replay_cursor_or_snapshot`, `observe_task_status` | `derive_a_second_intent_key_for_the_same_plan`, `fabricate_a_task_ref_from_a_missing_receipt`, `call_job_manager_review_resume` | `resubmit_the_same_key_and_accept_the_ledger_replay` |
| `awaiting` | TaskRef bound; opaque wait cursor; non-terminal task projection | `replay_cursor_or_snapshot`, `observe_task_status`, `read_digest_bound_evidence` | `resubmit_a_task_that_already_has_a_task_ref`, `call_job_manager_review_resume`, `fabricate_a_task_ref_from_a_missing_receipt` | `replay_from_the_cursor_or_rebuild_from_the_snapshot` |
| `evidence_read` | terminal TaskRef; verified `evidence_refs`; record at last known revision | `cas_record_with_observed_revision`, `replay_cursor_or_snapshot` | `publish_a_decision_without_digest_bound_evidence`, `resubmit_a_task_that_already_has_a_task_ref`, `reuse_the_previous_approval_for_a_changed_plan`, `blind_cas_on_a_stale_record_revision`, `call_job_manager_review_resume` | `cas_the_record_with_expected_revision_and_those_evidence_refs` |
| `record_cas_pending` | a CAS was attempted, outcome unknown; last observed record revision; evidence + decision to publish | `cas_record_only_after_reread`, `replay_cursor_or_snapshot` | `blind_cas_on_a_stale_record_revision`, `resubmit_a_task_that_already_has_a_task_ref`, `publish_a_decision_without_digest_bound_evidence`, `call_job_manager_review_resume` | `reread_the_record_then_cas_only_on_the_observed_revision` |
| `settled` | record carries the round decision; budget/model receipts settled; terminal task projection | `replay_cursor_or_snapshot`, `start_the_next_round` | `resubmit_a_task_that_already_has_a_task_ref`, `reuse_the_previous_approval_for_a_changed_plan`, `call_job_manager_review_resume` | `no_reconciliation_needed` |

## 6. Plan-digest change chain

1. A persisted intent is bound to one `plan_digest`; the same round cannot take a
   second intent with a different digest (typed `plan_digest_changed`, carrying
   `next_round`).
2. The old approval is revoked through the normal optimistic CAS when it was
   `approved`; a `pending`/`draft` approval cannot authorize the new digest
   either and is only reported.
3. A new approval intent is requested for the new digest (`revision` grows by
   one), and the session opens `round + 1`.
4. The new key is derived **by RPH** for that new round. The platform returns
   `intent_key_required: true` and never fabricates a key.
5. No execution fact is written: no job row, no `JobStatus`, no
   `WAITING_REVIEW`, no TaskRef bind beyond the existing evidence-only bind.

## 7. Compatibility and change rule

- The document is additive to `events-recovery.v1.json`; cursor/snapshot
  semantics stay owned by `ResearchRecoveryCoordinator` (`runtime/research_events.py`).
- A new field or a new crash-point stage is a new contract version; removing or
  renaming a field needs a migration note and a C consumer confirmation.
- The RPH key layout is additive: a new segment is appended at the end behind a
  new key function, and an already-persisted key stays addressable.
