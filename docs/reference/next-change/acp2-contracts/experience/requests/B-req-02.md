# B-req-02 — Consume the experience harness CLI and receipt schema

- **Status:** open
- **Raised by:** C (T-07 / C03b), 2026-10-22
- **Requested of:** B (B01 receipt contract / H2 aggregator owner)
- **Severity:** blocking for H2 aggregation of any `c_accept` evidence row

## Consumer step

T-07 (`Harness self-test, P0 fixture runs and 36-scenario dry-run`) and every
later C evidence row produced by `c_accept`. C writes one receipt per
`(owner, scenario, phase, run_id)` and hands the receipt (not a summary) to the
H2 aggregator. C never writes a second receipt validator.

## Owner

B — `scripts/acceptance/acp2_runtime.py` and
`docs/reference/next-change/acp2-contracts/runtime/acceptance-result.schema.json`
(B01). B owns every public field, enum and semantic rule.

## Current schema revision

Observed B main `HEAD` on 2026-10-22:

| Artifact | Identity |
| --- | --- |
| `runtime/acceptance-result.schema.json` | `$id = acp2.acceptance.v1`, contract version `1.0.0`, sha256 `9c14f1f50bbbba5733a493b8d018e15d6ee137d49c29b3a9121fda1f6a9108ea` |
| `scripts/acceptance/acp2_runtime.py` | sha256 `906a392020d482954aa7f7a782f3bbd7c4d5b77a06ce7fec2bfc830012d74a4e` |
| Schema dialect | `https://json-schema.org/draft/2020-12/schema` |

C does not copy the field list. It imports the validator by reference and
records the actual validator digest of each run as
`extensions["experience.harness_contract_revision"]`.

## Expected schema revision

No schema change is requested. This request asks B to confirm three consumer
facts:

1. the harness `--help` contract below is stable (`0` exit, no side effect);
2. the public receipt fields are the only aggregation source (C's
   `extensions.experience` is metadata, never a substitute);
3. B's H2 aggregator accepts a C receipt whose `scenario` is
   `experience:<frozen-id>` and whose `owner` is `C`.

If B needs to change any public field, the additive rule below applies and C
will re-freeze against the new digest.

### Harness CLI (verbatim `--help`, exit 0, no model/DSH/Controller import)

```
usage: acp2_experience.py [-h]
                          [--scenario SCENARIO | --verify-receipt PATH | --check-doc-placeholders PATH]
                          [--out OUT] [--timeout TIMEOUT]
                          [--manifest MANIFEST] [--phase PHASE]
                          [--mode {fixture,live}] [--subcase SUBCASE]
                          [--dry-run]

ACP 2.0 C-track experience acceptance harness. Receipts are written and validated by B's single acp2_runtime.py implementation.

options:
  -h, --help            show this help message and exit
  --scenario SCENARIO   one of the 36 frozen experience scenario IDs
  --verify-receipt PATH
                        validate one receipt with B's validate_receipt (tool
                        check, not PASS)
  --check-doc-placeholders PATH
                        fail on angle-bracket placeholders outside fences/HTML
                        comments
  --out OUT             evidence leaf directory
  --timeout TIMEOUT     total scenario timeout in seconds
  --manifest MANIFEST   absolute C experience manifest extension path
  --phase PHASE         common B01 phase (main/approval/budget/...)
  --mode {fixture,live}
                        fixture (default) or live lanes
  --subcase SUBCASE     sub-line: cross-domain-live:{nmr,thermo,selectivity},
                        research-loop-live:{control,search}
  --dry-run             validate inputs, write NOT_VERIFIED, exit 0; never
                        executes

Exit codes: 0 PASS/tool check, 1 FAIL, 2 NOT_VERIFIED/not executed, 3 usage error. --help/--dry-run/--verify-receipt/--check-doc-placeholders are tool checks, never a scenario PASS.
```

Exit codes are fixed: `0=PASS/tool check`, `1=FAIL`, `2=NOT_VERIFIED/not
executed`, `3=usage error`. A dry run always writes `result=NOT_VERIFIED` and
exits `0`; it is never a scenario PASS.

## Result-schema usage

C consumes `runtime/acceptance-result.schema.json` unchanged. The receipt is
written by B's `write_result` and validated by B's `validate_receipt`
(`verify_artifacts=True`, `artifact_root=<leaf>`). The public fields C supplies
are:

- identity: `schema`, `schema_dialect`, `contract_version`, `owner="C"`,
  `scenario="experience:<id>"`, `phase`, `run_id`;
- revisions: `candidate_revision`, `candidate.{server_revision,
  client_revision, harness_revision}`;
- verdict: `result`, `scope`, `reason`, `exit_code`;
- execution: `command`, `cwd`, `started_at`, `finished_at`, `assertions`,
  `artifacts[]` (path/size/sha256);
- platform references: `job_id`, `operation_id`, `attempt`, `release_id`,
  `submission_id`, `manifest_generation`, `negotiated_protocol` (all `null`
  for the five P0 environment rows);
- `usage`, `evidence_kind`, `sub_results[]`, `extensions`.

C-owned extension keys (namespaced, never public fields):

| Key | Meaning |
| --- | --- |
| `experience.mode` | `fixture` or `live` |
| `experience.subcase` | `nmr`/`thermo`/`selectivity`/`control`/`search` or `null` |
| `experience.scenario_key` | frozen bare scenario id |
| `experience.interpreter` | absolute interpreter that executed the row |
| `experience.product_import` | resolved product import path |
| `experience.harness_path` | absolute harness path |
| `experience.test_revision` | C test/candidate revision |
| `experience.harness_contract_revision` | sha256 of the B validator actually imported |
| `experience.run_id` | run id (duplicate of the public field for tooling) |
| `experience.manifest` | redacted C manifest extension |
| `acceptance.required_child_phases` | aggregate child-phase list (aggregate rows only) |
| `experience.aggregate_gaps` | missing child phases (aggregate NOT_VERIFIED only) |

### Namespaced phase normalization (four-tuple uniqueness)

Receipt identity is `(owner, scenario, phase, run_id)`. C normalizes
`--mode`/`--subcase` before writing so independently signable rows never
collide:

| Input | Written phase | `evidence_kind` |
| --- | --- | --- |
| `--mode fixture` (default fixture scenario) | `experience:fixture` | `fixture` |
| `--mode live` on a live-registered scenario | `experience:live` | `live` |
| `cross-domain-live --subcase nmr` | `experience:nmr` | `live` |
| `research-loop-live --subcase control` | `experience:control` | `live` |
| closeout/release scenarios | `main` | `live` |

## Minimal input

Dry-run of the P0 baseline row (writes `NOT_VERIFIED`, exits `0`):

```bash
c_accept baseline 300 --dry-run
# wrapper argv -> acp2_experience.py
#   --scenario baseline --timeout 300 --out <evidence>/C/baseline/<run_id>/main --dry-run
```

A real P0 row in this todo:

```bash
c_accept baseline 300
```

## Expected success output

A validated receipt (abridged) with the P0 environment verdict:

```json
{
  "schema": "acp2.acceptance.v1",
  "owner": "C",
  "scenario": "experience:baseline",
  "phase": "experience:fixture",
  "run_id": "baseline-20261022T120000Z-12345",
  "result": "PASS",
  "evidence_kind": "fixture",
  "exit_code": 0,
  "assertions": [
    {"name": "baseline.git_head", "status": "PASS", "detail": "b3b7fc2..."}
  ],
  "artifacts": [
    {"path": "baseline.json", "size_bytes": 512, "sha256": "<64 hex>"}
  ],
  "extensions": {
    "experience.mode": "fixture",
    "experience.scenario_key": "baseline",
    "experience.interpreter": "/opt/acp2-dev/venvs/c/bin/python",
    "experience.product_import": "/opt/acp2-dev/C/src/acp/__init__.py",
    "experience.harness_path": "/opt/acp2-dev/C/scripts/acceptance/acp2_experience.py",
    "experience.test_revision": "<40-hex>"
  }
}
```

B's `validate_receipt` returns an empty error list for this document.

## Failure output

A live-evidence row carrying a fake transport/backend/model is rejected as a C
`FAIL`, never converted to `NOT_VERIFIED`, and a tampered artifact digest is
rejected by B's validator:

```json
{
  "schema": "acp2.acceptance.v1",
  "owner": "C",
  "scenario": "experience:mcp-live",
  "phase": "experience:live",
  "result": "FAIL",
  "evidence_kind": "live",
  "exit_code": 1,
  "assertions": [
    {
      "name": "live.no_fake_transport",
      "status": "FAIL",
      "detail": "env:ACP2_FAKE_TRANSPORT"
    }
  ],
  "extensions": {"experience.mode": "live"}
}
```

`acp2_experience.py --verify-receipt <tampered>` exits `1` with
`REJECT: artifact_sha_mismatch:...`, and
`acp2_experience.py --scenario does-not-exist ...` exits `3`.

## Compatibility requirement

- Additive within `v1`; no public field may be renamed or removed without a
  major contract version and a re-freeze of the C harness digest.
- C keeps writing `extensions.experience` only. A public-field change request
  from B is accepted by digest bump, not by C copying the field list.
- C never restarts B's Controller, never opens a second receipt writer and
  never publishes a dry run as PASS.
- `fixture` rows are never selectable as live terminal evidence by H4.

## Evidence path

`$ACP2_EVIDENCE/C/harness-selftest/` — self-test log, 36-scenario dry-run
table, `route.json` (non-authoritative diagnostic) and the five P0
`c_accept` receipts under `$ACP2_EVIDENCE/C/<scenario>/<run_id>/`.
