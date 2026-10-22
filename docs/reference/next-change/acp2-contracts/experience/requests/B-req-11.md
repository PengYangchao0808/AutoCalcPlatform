# B-req-11 — DA run_spec and explicit da-preflight / da-submit phases

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** B (B18a preflight, B20a-ready/exec)
- **Severity:** blocking for T-41 (`DA research live`) and the H4/H6 gates

## Consumer step

T-41 (`B20a-exec`) consumes B18a's preflight and B20a's submit. C's harness
passes an explicit `--phase`; it must never infer the phase from an output
directory or a task number.

## Owner

B — B18a (preflight) and B20a-ready/exec (submit/evidence).

## Current schema revision

The B application API is committed at `acp2.runtime.application-api.v1`
v1.0.0 (sha256
`a9f7e741007029638b64e069d918746f7105c2bf99c97dc1e9c3818cdcc0d4a4`). The
`da-preflight` / `da-submit` phases are not yet registered in a delivered B01
registry (`NOT_VERIFIED(b01_handoff)`).

## Expected schema revision

B18a/B20a **v1.0.0** exposing:

- one shared `run_spec` with `started_at`, `science_deadline_seconds=13380`,
  `export_budget_seconds=900`, `aggregate_budget_seconds=120`,
  `queue_accounting`, `inflight_stop_policy`;
- explicit phases `da-preflight`, `da-submit`, `da-evidence`, `aggregate`
  (registered by B01);
- the same `run_spec` identity on preflight and submit results.

## Minimal input

```json
{
  "scenario": "da-research-live",
  "phase": "da-preflight",
  "run_spec": {
    "run_id": "da-research-live-r1",
    "started_at": "2026-10-22T12:00:00Z",
    "science_deadline_seconds": 13380,
    "export_budget_seconds": 900,
    "aggregate_budget_seconds": 120,
    "queue_accounting": "excluded_from_science_clock",
    "inflight_stop_policy": "stop_on_deadline"
  },
  "proposal_ref": "prop-da-selectivity-001",
  "plan_digest": "sha256:d6b0c35220aec8385f0c92778da312d1c60f7023630572969fbbcd2357b70c8b"
}
```

## Expected success output

```json
{
  "phase": "da-preflight",
  "run_spec_id": "da-research-live-r1",
  "preflight": {"approval_bound": true, "budget_ok": true, "release_id": "release-2026.10.22"},
  "next_phase": "da-submit"
}
```

Then `da-submit` returns the TaskRef and the same `run_spec_id`.

## Failure output

An implicit phase (derived from an output directory or task number), or a
preflight/submit pair with different run_spec identities, is rejected:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "implicit_phase_dispatch",
    "expected": "explicit da-preflight or da-submit",
    "observed": "phase inferred from output_dir"
  },
  "input": {
    "out": ".../da-research-live-r1/main",
    "run_spec": {"science_deadline_seconds": 13380}
  }
}
```

Also rejected: `da-submit` without the `run_spec`, or a submit whose
`run_spec.started_at`/budgets differ from the preflight.

## Compatibility requirement

- `--phase da-preflight` and `--phase da-submit` are explicit and B01-registered;
  aliases are normalized only per B01's read-only alias rule.
- The `run_spec` is one frozen object, not copied per phase.
- C records `NOT_VERIFIED(phase_dispatch)` if the explicit phases are absent;
  it never falls back to directory-derived dispatch.

## Evidence path

`$ACP2_EVIDENCE/C/da-research-live/<run_id>/main/da-preflight/` and
`.../da-submit/`, plus the B18a/B20a receipts referenced by digest.
