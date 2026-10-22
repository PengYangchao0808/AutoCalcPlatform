# B-req-10 — B23a and B23b must use the same selectivity scenario key

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** B (B23a permissions/budget/execution interface, B23b execute + export)
- **Severity:** blocking for T-40 (`cross-domain-live/selectivity`)

## Consumer step

T-40a/T-40b consume B23a and B23b results under one key. C's harness writes the
public phase `experience:selectivity`; the handoff key must match exactly.

## Owner

B — B23a and B23b (same delivery row, same scenario identity).

## Current schema revision

B23 is not committed in the C baseline. B01's phase registry is referenced by
`registry_ref` and is `NOT_VERIFIED(b01_handoff)` until delivered.

## Expected schema revision

B23 handoff **v1.0.0** where both B23a and B23b carry:

```text
scenario_key = "experience:cross-domain-live/selectivity"
phase        = "experience:selectivity"   (B01-registered)
mode         = "live"
```

B23a's 1800 s budget is only for isolation/permission/budget checks; it must
not replace the selectivity scientific execution. B23b's execution usage is
recorded separately and the combined wall clock still respects 3600 s and the
TaskRef cap.

## Minimal input

```json
{
  "scenario_key": "experience:cross-domain-live/selectivity",
  "producer_task": "B23a",
  "consumer_task": "T-40a",
  "phase": "experience:selectivity",
  "run_spec_ref": "run-spec-da-selectivity-001"
}
```

## Expected success output

B23a and B23b return the same `scenario_key`, the same `run_spec_ref`, and
distinct usage rows that aggregate without duplication:

```json
{
  "scenario_key": "experience:cross-domain-live/selectivity",
  "b23a": {"permission": "granted", "budget_check": "pass", "usage": {"wall_seconds": 120, "new_task_refs": 0}},
  "b23b": {"execution": "completed", "usage": {"wall_seconds": 2400, "new_task_refs": 1}},
  "combined": {"wall_seconds": 2520, "new_task_refs": 1, "cap_ok": true}
}
```

## Failure output

A mismatched key, or a B23a result that claims the selectivity science result,
is rejected:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "scenario_key_mismatch",
    "b23a": "experience:cross-domain-live/selectivity",
    "b23b": "experience:selectivity-live"
  }
}
```

Also rejected: B23a reporting `new_task_refs: 1` as if it executed the science
(B23a is permission/budget only), or a combined usage that exceeds 3600 s.

## Compatibility requirement

- The key is shared verbatim; B must not add a second alias for B23b.
- A change to the key requires a coordinated update to B01's registry, the H6
  matrix and this request.
- Usage accounting is cumulative within the run_id; changing `run_id` must not
  reset it.

## Evidence path

`$ACP2_EVIDENCE/C/cross-domain-live/<run_id>/experience/selectivity/` plus the
B23a/B23b receipts referenced by digest.
