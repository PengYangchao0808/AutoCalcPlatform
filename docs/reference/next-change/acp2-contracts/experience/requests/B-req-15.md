# B-req-15 — Prove actual search execution (not just a candidate list)

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** B (execution/proof owner for the search sub-scenario)
- **Severity:** blocking for T-30b and T-34's search-capability claim

## Consumer step

T-30b (`experience:research-loop-live/search`). C may only present a search
result when B proves that the declared candidates were actually executed and
that the proof ties to real platform task facts.

## Owner

B — execution receipts / TaskRef facts for the search sub-scenario.

## Current schema revision

The B acceptance receipt contract is committed:
`runtime/acceptance-result.schema.json`, sha256
`9c14f1f50bbbba5733a493b8d018e15d6ee137d49c29b3a9121fda1f6a9108ea`
(v1). C references it and does not copy its field list.

## Expected schema revision

An additive execution-proof object (or a documented extension of the acceptance
receipt) **v1.0.0** containing:

- `job_id`, `attempt`, `release_id` for each executed candidate batch;
- the command/release identity actually run;
- per-candidate disposition with artifact digests;
- `queue_seconds`/`wall_seconds` and the candidate count;
- an explicit `executed: true` backed by the task facts, never a model claim.

## Minimal input

```json
{
  "search_id": "search-da-001",
  "expected_candidates": 8,
  "scenario_key": "experience:research-loop-live/search",
  "phase": "experience:search"
}
```

## Expected success output

```json
{
  "search_id": "search-da-001",
  "executed": true,
  "task_refs": [
    {"job_id": "20261022_020_Search", "attempt": 1, "release_id": "release-2026.10.22"}
  ],
  "candidate_count": 8,
  "artifacts": [
    {"candidate_id": "cand-001", "sha256": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}
  ],
  "usage": {"queue_seconds": 30, "wall_seconds": 900}
}
```

## Failure output

A proof that lists candidates but has no real TaskRef, no release id, or no
artifact digest is rejected as not-executed:

```json
{
  "class": "not-prepared",
  "code": "resource_not_ready",
  "params": {
    "rule": "search_execution_unproven",
    "missing": ["task_refs", "release_id"]
  },
  "input": {
    "search_id": "search-da-001",
    "executed": true,
    "candidates": ["cand-001", "cand-002"]
  }
}
```

## Compatibility requirement

- The proof must be derivable from B's persisted task facts; a chat transcript
  or a model-produced list never proves execution.
- Additive within v1; C references the acceptance-result schema and does not
  duplicate platform identity fields.
- If B has not delivered the proof contract, T-30b records
  `NOT_VERIFIED(search_execution_proof)` and the parent aggregate cannot PASS.

## Evidence path

`$ACP2_EVIDENCE/C/research-loop-live/<run_id>/experience/search/` plus the B
receipt artifacts referenced by digest.
