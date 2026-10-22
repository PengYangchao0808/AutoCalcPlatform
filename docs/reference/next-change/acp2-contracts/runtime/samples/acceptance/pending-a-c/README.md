# Pending A/C acceptance samples — `NOT_VERIFIED / blocked_fixture`

B01 is the common first handoff for the three-track `acp2.acceptance.v1` receipt
protocol. B has delivered its legal and illegal samples and the shared validator
(`scripts/acceptance/acp2_runtime.py --validate-samples`). The protocol freeze is
**not** complete until A and C each submit one legal and one illegal sample and
the same validator accepts/rejects them.

## Current status (2026-10-22)

| Track | Expected input | Status | Reason |
|---|---|---|---|
| A (science) | legal `science:*` receipt + one illegal variant | **NOT_VERIFIED** | `blocked_fixture` — A's contract directory currently has only `science/baseline.md` and `science/path-map.md`; no acceptance sample exists. |
| C (experience) | legal `experience:*` fixture receipt + legal live receipt + one illegal variant | **NOT_VERIFIED** | `blocked_fixture` — C's contract directory has `experience/handoffs/dependencies-c.json` and two requests, but no acceptance sample. The two `positive/c-dsh-local-singlepoint-{fixture,live}.json` files here are **B-authored shape exemplars** of the C-owned example, not C-authored evidence. |

A/C must not fabricate these from the B schema alone; the point of the handoff is
to prove the real track can produce the envelope.

## Exact expected inputs

Place files under `runtime/samples/acceptance/positive/` and
`runtime/samples/acceptance/negative/` and add them to `cases.json` (or hand the
paths to B for corpus registration). Every file is validated with
`validate_receipt(..., verify_artifacts=True, artifact_root=<sample dir>)`.

### A (owner `A`)

1. **Legal fixture or standalone receipt**:
   - `owner: "A"`, `scenario: "science:<id>"`, `phase: "science:fixture"` or
     `"science:live"`.
   - `candidate.server_revision` = current B source candidate;
     `candidate.harness_revision` = A harness identity; `candidate.client_revision`
     nullable.
   - `command: string[]`, independent absolute `cwd`, `started_at`/`finished_at`
     RFC 3339 `Z`.
   - `artifacts[{path,size_bytes,sha256}]` (real files, digest verified).
   - `extensions` may carry `science.run_key`, `science.receipt`,
     `acceptance.operation_class` (`discover|standalone|submit|read`) and
     `acceptance.target` (`local|remote`). `run_key` and `receipt` may instead use
     the optional top-level compatibility fields.
   - Fixture: every platform reference null. Live standalone/discover: every
     platform reference null except optional `negotiated_protocol`. Live submit:
     `job_id` + `attempt` required, local `submission_id` null.
2. **Illegal variant**: mutate exactly one frozen rule (bad time field, corrupt
   digest, unknown major `contract_version`, missing `owner`, fixture with a
   platform ref, invalid `result`/`scope`/`evidence_kind`) and state the expected
   reason class from `acceptance-harness.v1.md` §8.

### C (owner `C`)

1. **Legal fixture** `experience:dsh-local-singlepoint` (or another
   `experience:*` ID) with `phase: "experience:fixture"`, `evidence_kind:
   "fixture"`, all platform refs null.
2. **Legal live** same scenario with `phase: "experience:live"`, `evidence_kind:
   "live"`, `extensions["acceptance.operation_class"] = "submit"`,
   `extensions["acceptance.target"] = "local"`, non-null `job_id`/`attempt`/
   `release_id`, null `submission_id` (local) and null `manifest_generation`.
3. **Illegal variant** with one named reason (for example a fixture receipt
   carrying `job_id`, or a local live receipt carrying `submission_id`).

## Acceptance command

```bash
/usr/bin/python3.11 scripts/acceptance/acp2_runtime.py --validate-samples
```

Expected after A/C handoff: every new positive sample prints `ACCEPT` and every
new negative sample prints `REJECT <expected reason>`, and the summary reports
`N/N sample expectations matched`. Until then this README is the recorded
`NOT_VERIFIED / blocked_fixture` row; B01 does not wait on A/C and does not mark
the three-track protocol frozen.
