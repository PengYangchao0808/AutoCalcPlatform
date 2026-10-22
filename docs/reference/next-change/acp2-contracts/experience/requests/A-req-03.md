# A-req-03 — Selectivity sub-scenario uses one key and one ordered chain

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** A (A-25a fixture/criteria, A-25b scientific audit)
- **Severity:** blocking for T-40 (`cross-domain-live/selectivity`)

## Consumer step

T-40a (`C research submission driver`) and T-40b (`C presentation and
sign-off`), with A-25a supplying the two-path fixture/criteria and A-25b the
scientific audit. B23a supplies permissions/budget/execution interface; B23b
executes and exports the authorized artifacts.

## Owner

A — A-25a (fixture/criteria) and A-25b (scientific audit).

## Current schema revision

A-25a/A-25b are not committed in the C baseline; the selector contract is
`NOT_VERIFIED(a_selectivity_handoff)`.

## Expected schema revision

A selectivity fixture/criteria **v1.0.0** carrying exactly the key
`scenario_key = "experience:cross-domain-live/selectivity"`, referencing rather
than re-defining the B01 phase `experience:selectivity`. The required chain is:

```text
T-40a (single research submission by C)
  -> B23b (execute + export authorized artifacts)
  -> A-25b (scientific audit of both paths)
  -> T-40b (C presentation and sign-off)
```

The `selectivity` sub-scenario must not create a separate `selectivity-live`
scenario. A-25b must cite the same `scenario_key`; it must not invent a second
scenario identity.

## Minimal input

```json
{
  "scenario_key": "experience:cross-domain-live/selectivity",
  "subcase": "selectivity",
  "paths": [
    {"path_id": "endo", "structure_sha256": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "criteria_ref": "criteria-endo-v1"},
    {"path_id": "exo",  "structure_sha256": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "criteria_ref": "criteria-exo-v1"}
  ],
  "submission_task_ref": {"job_id": "20261022_010_DAselectivity", "attempt": 1, "release_id": "release-2026.10.22"}
}
```

## Expected success output

Both paths are present and distinguishable, the audit produces a per-path
verdict, and the relative preference is issued only when neither path is
missing:

```json
{
  "scenario_key": "experience:cross-domain-live/selectivity",
  "paths": {"endo": "supported", "exo": "supported"},
  "verdict": "endo_lower_free_energy",
  "audit_ref": "A-25b-audit-001",
  "submission_task_ref": {"job_id": "20261022_010_DAselectivity", "attempt": 1, "release_id": "release-2026.10.22"}
}
```

## Failure output

A different scenario identity, or a single-path claim of relative preference,
is rejected:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "selectivity_scenario_key_mismatch",
    "expected": "experience:cross-domain-live/selectivity"
  },
  "input": {
    "scenario_key": "experience:selectivity-live",
    "paths": [{"path_id": "endo"}],
    "verdict": "endo_lower_free_energy"
  }
}
```

A missing second path is also rejected (`PATH_INSUFFICIENT`), and the parent
must report `inconclusive` rather than inventing a preference.

## Compatibility requirement

- The key string is immutable. Aliases are read-only normalizations registered
  by B01, never a reason to accept a new key.
- A-25b must not fork a second scenario or a second audit schema for the same
  sub-scenario.
- If the chain order changes, the H6 scenario matrix and B01 registry must be
  updated together; C alone must not record `NOT_VERIFIED` while other tracks
  claim completeness.

## Evidence path

`$ACP2_EVIDENCE/C/cross-domain-live/<run_id>/experience/selectivity/` and
A/B handoff evidence referenced by digest in `plan-identity.md`-compliant form.
