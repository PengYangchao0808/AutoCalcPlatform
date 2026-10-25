# B-req-17 — B00 must publish baseline-lock.json + manifest.json with an absolute evidence_root

- **Request ID:** `B-req-17`
- **Owner:** B00
- **Producer:** B00
- **Consumer step:** C/T-01 (baseline freeze and evidence root), every downstream C
  evidence row (`$ACP2_EVIDENCE/C/<scenario>/<run_id>/`)
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)
- **Current schema revision:** none. C observed:
  - `docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json` — absent
  - `docs/reference/next-change/acp2-contracts/runtime/manifest.json` — absent
  - `docs/reference/next-change/acp2-contracts/` — absent in the main checkout
  Status: `NOT_VERIFIED(b00_handoff)`.
- **Expected schema revision:** `runtime/baseline-lock.json` and
  `runtime/manifest.json`, versioned by B00 (v1 or later), with the fields below.
  C does not invent a B schema; C only requires these fields to exist and to agree.

## Required fields

`docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json`:

- `candidate_id` — the B00-signed candidate identity.
- `evidence_root` — an **absolute** path. It must be the same string as
  `manifest.json.evidence_root`. No relative path, no `~`, no environment expansion.
- `final_debug_candidate` — either a full 40-hex commit SHA of the applied
  candidate, or a `pending_fix_bundle` object pointing at
  `runtime/final-debug-patch/` with `bundle_dir`, `apply_order`, and a per-file
  `sha256` map. A bare aggregate `git diff` digest is not acceptable.
- `candidate_id` must not be the historical reference
  `096cdffe9a3817d8ee443d824830d87ec6e07bc2`.

`docs/reference/next-change/acp2-contracts/runtime/manifest.json`:

- `candidate_id` — equal to `baseline-lock.json.candidate_id`.
- `evidence_root` — equal to `baseline-lock.json.evidence_root`, absolute.
- `baseline_lock_path` — versioned path to the lock above.

C cannot invent the evidence root because product evidence must be reconcilable by
B and must not land in a path B later reassigns. Today C uses
`/opt/acp2-dev/evidence` **only provisionally** and marks it
`PROVISIONAL — B00 must confirm or reassign; NOT_VERIFIED(b00_handoff)`.

## Minimal input

```json
{
  "request": "B-req-17",
  "user": "C/T-01",
  "observed_baseline_lock": "absent",
  "observed_manifest": "absent",
  "historical_reference_sha": "096cdffe9a3817d8ee443d824830d87ec6e07bc2",
  "provisional_evidence_root": "/opt/acp2-dev/evidence"
}
```

## Expected success output

```json
{
  "baseline-lock.json": {
    "schema": "acp2.baseline_lock.v1",
    "candidate_id": "sha256:<b00-signed>",
    "evidence_root": "/absolute/b00/assigned/evidence-root",
    "final_debug_candidate": "0123456789abcdef0123456789abcdef01234567",
    "historical_reference": "096cdffe9a3817d8ee443d824830d87ec6e07bc2"
  },
  "manifest.json": {
    "schema": "acp2.manifest.v1",
    "candidate_id": "sha256:<b00-signed>",
    "evidence_root": "/absolute/b00/assigned/evidence-root",
    "baseline_lock_path": "docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json"
  },
  "invariant": "baseline-lock.evidence_root == manifest.evidence_root == absolute path"
}
```

If the candidate is delivered as a patch bundle, the success output additionally
contains `final_debug_candidate.pending_fix_bundle` with `bundle_dir`,
`apply_order`, and a per-file `sha256` map; C applies it file by file, verifies each
pre-image and post-image digest, commits to `acp2/c-experience`, and writes the
resulting HEAD into `ownership.md`.

## Failure output

Minimum one accepted failure — a lock that exists but is not usable:

```json
{
  "request": "B-req-17",
  "result": "FAIL",
  "reason": "evidence_root is not absolute",
  "detail": "baseline-lock.json.evidence_root = 'evidence/C'",
  "expected": "an absolute path identical in baseline-lock.json and manifest.json"
}
```

Second failure mode — a lock that reuses the historical reference without a new
candidate:

```json
{
  "request": "B-req-17",
  "result": "FAIL",
  "reason": "final_debug_candidate equals the historical reference",
  "detail": "final_debug_candidate = 096cdffe9a3817d8ee443d824830d87ec6e07bc2",
  "expected": "a new applied-and-committed SHA, or a pending_fix_bundle with per-file sha256"
}
```

Third failure mode — a patch bundle with only an aggregate digest:

```json
{
  "request": "B-req-17",
  "result": "FAIL",
  "reason": "pending_fix_bundle has no per-file sha256 map",
  "detail": "only bundle_dir and apply_order are present",
  "expected": "bundle_dir + apply_order + per-file pre/post sha256"
}
```

## Compatibility requirement

- C's `experience/ownership.md` candidate block must be updated verbatim from the
  B00 lock: `candidate_id`, `evidence_root`, and `final_debug_candidate`.
- `evidence_root` must remain stable after publication; reassignment requires a new
  lock revision and a migration note (C does not move existing evidence silently).
- The lock path stays inside B-owned `runtime/**`; C never writes it.
- When the lock is published, C re-runs T-01's evidence-root assertion
  (`$ACP2_EVIDENCE == baseline-lock.evidence_root`) and only then removes the
  provisional marker.

## Evidence path

- C-side observation and drift report:
  `/opt/acp2-dev/evidence/C/baseline/20261022T191200Z-c00/`
- C candidate block: `docs/reference/next-change/acp2-contracts/experience/ownership.md`
- B-side expected: `docs/reference/next-change/acp2-contracts/runtime/baseline-lock.json`
  and `docs/reference/next-change/acp2-contracts/runtime/manifest.json`.
