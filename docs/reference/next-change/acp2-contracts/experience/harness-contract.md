# C experience harness contract

Status: **delivered by T-06 / C03a (2026-10-22)**. Owner: C. Consumers: C
scenario tasks (T-07 onward) and the integration reviewer.
Executable harness: `scripts/acceptance/acp2_experience.py`.
Scenario registry: `scripts/acceptance/acp2_experience_scenarios.py`.

This document records how the C experience harness consumes B's single receipt
contract. It never redefines the receipt envelope, the public phase registry or
the validator. All public fields, enums and semantic checks live in B's schema
and executable validator; C adds only namespaced extension keys.

## 1. B contract inputs (by reference, never copied)

| Input | Path | Identity |
| --- | --- | --- |
| Receipt schema | `$ACP2_PROJECT_ROOT/docs/reference/next-change/acp2-contracts/runtime/acceptance-result.schema.json` | `$id = acp2.acceptance.v1`, contract version `1.0.0`, dialect `https://json-schema.org/draft/2020-12/schema` |
| Single validator/writer | `$ACP2_PROJECT_ROOT/scripts/acceptance/acp2_runtime.py` | `validate_receipt`, `validate_summary`, `write_result`, `harness_revision` |
| Contract prose | `$ACP2_PROJECT_ROOT/docs/reference/next-change/acp2-contracts/runtime/acceptance-harness.v1.md` | canonical field list, aliases and registry |

Digests observed when this document was written:

| Artifact | SHA-256 |
| --- | --- |
| `acceptance-result.schema.json` | `9c14f1f50bbbba5733a493b8d018e15d6ee137d49c29b3a9121fda1f6a9108ea` |
| `acp2_runtime.py` at main `HEAD` (`46d5fe3`) | `7a344c0a9bce5adfb8a34e0d829f1c3995029a7f8ad4313842d9895215b051f2` |
| `acp2_runtime.py` in the working tree during T-06 verification | `906a392020d482954aa7f7a782f3bbd7c4d5b77a06ce7fec2bfc830012d74a4e` |

The authoritative digest for any single run is **not** this table: the harness
computes `sha256_file` of the validator it actually imported and records it as
`extensions["experience.harness_contract_revision"]`. A changed validator
drops the evidence back to `NOT_VERIFIED` review rather than silently
inheriting an old claim. B remains the owner of every public field.

Import path: the harness resolves the validator through
`$ACP2_PROJECT_ROOT/scripts/acceptance/acp2_runtime.py` (override for isolated
tests with `ACP2_RUNTIME_PATH`). It does not vendor, fork or wrap the validator
and does not carry a second `write_result`/`validate_receipt` implementation.

## 2. Three mutually exclusive modes

| Mode | Arguments | Exit behaviour |
| --- | --- | --- |
| Scenario | `--scenario ID --out DIR --timeout SEC [--manifest ABS] [--phase PHASE] [--mode fixture\|live] [--subcase ID] [--dry-run]` | `0` PASS (or dry-run/tool check), `1` FAIL, `2` NOT_VERIFIED/unimplemented, `3` usage |
| Receipt verification | `--verify-receipt PATH` | `0` accepted, `1` rejected with reasons, `2` validator/schema unavailable |
| Placeholder check | `--check-doc-placeholders PATH` | `0` clean, `1` body placeholder found, `3` file missing |

`--help` exits `0` in every mode and performs no model, DSH, Controller or
network initialisation. Selection errors, missing values and unknown
scenario/phase/mode/subcase combinations exit `3`. A usage failure never exits
`0` or `2`.

### Exit-code semantics

| Exit | Meaning |
| --- | --- |
| `0` | executed scenario PASS, or a tool check that worked (`--help`, `--dry-run`, `--verify-receipt` accept, `--check-doc-placeholders` clean) |
| `1` | executed scenario FAIL, invalid receipt, self-referencing artifacts, or a failed tool check |
| `2` | NOT_VERIFIED: missing prerequisite, unimplemented scenario, timeout, or validator/schema absent |
| `3` | usage error: bad/mutually exclusive mode, unknown scenario, undeclared phase/mode/subcase, missing `--out`/`--timeout`, non-empty evidence leaf |

`--dry-run` takes precedence over implementation status: a known scenario
returns `0` and writes `result=NOT_VERIFIED` even when no body is registered.
A known scenario without `--dry-run` and without a registered body returns `2`.
An unknown scenario always returns `3`. Dry-run never writes `PASS`.

## 3. Frozen scenario enum (36 IDs, verbatim)

| Phase group | Scenario IDs |
| --- | --- |
| P0 | `baseline`, `contracts`, `workspace`, `harness-selftest`, `node-dsh-install`, `browser-install`, `bridge-contract`, `mcp-live`, `dsh-discovery-live`, `model-live` |
| P1 | `client-fixtures`, `manual-browser-fixtures`, `dsh-local-singlepoint-live`, `dsh-lsf-singlepoint-live`, `dsh-optfreq-live`, `same-task-browser-live`, `client-recovery-live` |
| P2 | `question-reaction-fixtures`, `research-record-fixtures`, `approval-budget-live`, `research-loop-live`, `evidence-browser-live`, `research-resume-live`, `fault-boundaries-live`, `da-research-live`, `p2-closeout` |
| P3 | `plugin-discovery-live`, `schema-form-live`, `plugin-upgrade-live` |
| P4 | `cross-domain-live`, `multi-project-live`, `experience-upgrade-live` |
| P5 | `client-release`, `clean-install-live`, `operator-runbook-live`, `experience-release-closeout` |

At startup the harness compares its frozen tuple with
`$ACP2_ALL_SCENARIOS` and `$ACP2_LIVE_SCENARIOS` when those variables are
present. Divergence fails closed with exit `2` and a named
`scenario_enum_divergence` / `live_enum_divergence` reason. The 21 live IDs are
the P0-P5 IDs ending in `-live` except `evidence-browser-live`, which is
fixture-by-default and live only with explicit `--mode live`.

## 4. Namespaced phase normalization

C never invents a public phase. `--phase` accepts only the common B01 phases
`main`, `approval`, `budget`, `da-preflight`, `da-submit`, `da-evidence`,
`aggregate`; a namespaced value such as `experience:fixture` is a usage error
(`3`).

Before writing, the harness normalizes:

| C input | Written phase | Evidence kind |
| --- | --- | --- |
| explicit `--phase` (common) | unchanged | `main`/`approval`/`budget`/... → `live`; `aggregate` → `aggregate` |
| `approval-budget-live --phase approval` | `experience:fixture` (scenario alias) | `fixture` |
| `approval-budget-live --phase budget` | `budget` (unchanged) | `fixture` (gap, `NOT_VERIFIED`) |
| `--subcase control` on `research-loop-live` | `experience:control` | `live` |
| `--subcase search` on `research-loop-live` | `experience:search` | `live` |
| `--subcase nmr` on `cross-domain-live` | `experience:nmr` | `live` |
| `--subcase thermo` on `cross-domain-live` | `experience:thermo` | `live` |
| `--subcase selectivity` on `cross-domain-live` | `experience:selectivity` | `live` |
| `--mode fixture`, no phase/subcase | `experience:fixture` | `fixture` |
| `--mode live`, no phase/subcase | `experience:live` | `live` |
| no mode/subcase, fixture-default scenario | `experience:fixture` | `fixture` |
| no mode/subcase, live-default scenario | `experience:live` | `live` |
| no mode/subcase, `p2-closeout` / release scenarios | `main` | `live` |

### Scenario-level phase alias (fixture-only evidence)

`approval-budget-live` is a scenario whose current evidence content is
fixture-only: the approval row runs the zero-dependency Node approval-card
suite and the plan-digest matrix, and the budget row is
`NOT_VERIFIED(b16_handoff)`. The CLI still accepts B01's common phase
`--phase approval` (T-27's required invocation), but the harness normalizes it
to the public phase `experience:fixture` and writes `evidence_kind=fixture`.
The scenario body reads the original value from
`ResolvedIdentity.requested_phase`; the receipt never carries the common
`approval` phase for this scenario.

Rule: **a fixture-only scenario can never claim a live phase.** It must not
publish `evidence_kind=live`, and `--mode live` is a usage error (`3`) for such
a scenario. `--phase budget` remains `NOT_VERIFIED(b16_handoff)` and is
recorded as fixture evidence because no live budget artifact exists; the
aggregate parent stays `NOT_VERIFIED` while that budget child is
`NOT_VERIFIED`. When B16/B17 deliver live approval/budget endpoints, the
scenario is removed from the fixture-only set in the same change that starts
publishing live rows.

`--subcase` is legal only for `cross-domain-live` with
`nmr|thermo|selectivity` and `research-loop-live` with `control|search`. Any
other scenario/subcase pair exits `3`. `--mode live` is legal only for a live
registered scenario (the 21 live defaults plus `evidence-browser-live`);
`--mode fixture` is legal for every scenario. The scenario field written to the
receipt is `experience:` plus the frozen ID, as required for owner `C`; the
bare ID is recorded as `extensions["experience.scenario_key"]`.

### Four-tuple uniqueness proof

Receipt identity is `(owner, scenario, phase, run_id)`. With one fixed
`run_id` the three independently signable groups produce seven distinct rows:

| owner | scenario | phase | run_id |
| --- | --- | --- | --- |
| C | `experience:evidence-browser-live` | `experience:fixture` | same |
| C | `experience:evidence-browser-live` | `experience:live` | same |
| C | `experience:research-loop-live` | `experience:control` | same |
| C | `experience:research-loop-live` | `experience:search` | same |
| C | `experience:cross-domain-live` | `experience:nmr` | same |
| C | `experience:cross-domain-live` | `experience:thermo` | same |
| C | `experience:cross-domain-live` | `experience:selectivity` | same |

Because `scenario`, `phase` and `run_id` are non-empty and the scenario/phase
pairs above differ, all seven tuples are distinct. T-06 evidence includes the
machine comparison (`four-tuples.json`) produced from the frozen run.

`evidence-browser-live` with `--mode live` is the terminal whole-scenario
result. It must not be followed by `--phase aggregate` (exit `3`).

## 5. Parent aggregation and evidence

`--phase aggregate` reads immutable child receipts from the same `run_id` under
the same scenario run root. Child discovery accepts only files whose `schema`
is `acp2.acceptance.v1`, whose `owner` is `C`, whose `scenario` equals the
current `experience:` scenario, whose `run_id` matches and whose phase is not
`aggregate`. The aggregate's own leaf is excluded.

| Child state | Parent result | Exit |
| --- | --- | --- |
| all required children `PASS` | `PASS` | `0` |
| any child `FAIL` | `FAIL` | `1` |
| no `FAIL`, but a required child missing or `NOT_VERIFIED` | `NOT_VERIFIED` with a gap list | `2` |
| no child receipts at all | `NOT_VERIFIED` (no receipt is fabricated) | `2` |

A parent never selects an arbitrary child PASS. Child references are written to
the public `sub_results` field (`phase`, `receipt`, `result`, `run_id`,
`scenario`, `owner`). When every expected sub-line is present the harness also
sets `extensions["acceptance.required_child_phases"]`, which lets B's validator
reject a parent `PASS` that is missing or contradicts a required child. When
expected rows are missing, the harness records
`extensions["experience.aggregate_gaps"]` and returns `NOT_VERIFIED`; it does
not claim a partial parent PASS.

## 6. Redaction

Before any receipt or redacted manifest artifact is written, the harness
recursively masks:

- every value whose key matches `(?i)(secret|token|password|api[_-]?key|authorization|credential)`;
- every `Bearer` token, including its credential text.

Masked values become `***`. The raw value never appears in the receipt, the
redacted manifest artifact, or the nested `extensions["experience.manifest"]`
copy. Keys remain visible so a reviewer can see that a credential-bearing field
existed; only its value is removed.

## 7. C-only `extensions` keys

The B schema requires every extension key to carry a namespace. C therefore
writes flat dotted keys, not a nested `experience` object:

| Key | Meaning |
| --- | --- |
| `experience.mode` | effective lane: `fixture` or `live` |
| `experience.subcase` | `nmr`/`thermo`/`selectivity`/`control`/`search`, or null |
| `experience.scenario_key` | bare frozen scenario ID |
| `experience.interpreter` | absolute Python interpreter that ran the harness |
| `experience.product_import` | absolute `acp` package origin actually resolvable by that interpreter |
| `experience.harness_path` | absolute C harness path |
| `experience.test_revision` | C revision used for the harness/scenario test code |
| `experience.harness_contract_revision` | SHA-256 of the B validator actually imported |
| `experience.run_id` | run identity (also a public receipt field) |
| `experience.manifest` | redacted C manifest-extension document, when supplied |

C-owned candidate fields are set after B's writer produces the base document:
`candidate_revision` and `candidate.client_revision` from the C/source
revision, and `candidate.harness_revision` from B's
`harness_revision()` (SHA-256 of `acp2_runtime.py`). `candidate.server_revision`
remains B's frozen integration candidate. C does not add a duplicate
`platform` object or a second copy of a public platform reference.

## 8. Evidence layout, append-only rule and wrapper argv

`--out` points at the leaf directory. The wrapper creates:

```
$ACP2_EVIDENCE/C/<scenario>/<run_id>/<phase>[/<mode>][/<subcase>]
```

The harness inherits `run_id` from `ACP2_RUN_ID` or from the `run_id` segment
already present in `--out`. A fixed `ACP2_RUN_ID` with a non-empty leaf exits
`3`; a non-empty leaf without a fixed run id also exits `3`. A new run gets a
new `run_id`. Sibling phase/mode/subcase directories under the same `run_id`
are allowed and are the intended aggregation inputs.

`c_accept` wrapper contract (`acceptance-env-c.sh`):

- `c_accept SCENARIO TIMEOUT [--phase PHASE] [--mode MODE] [--subcase ID] [--dry-run] [--manifest ABS]`
- `timeout` is the mandatory second positional argument;
- `--out` is generated by the wrapper and is not passed by the caller;
- extra arguments are forwarded to `acp2_experience.py`; `--phase` is never
  swallowed by the wrapper;
- the wrapper's leaf path is the authoritative run/phase/mode/subcase identity;
  the harness cross-checks it against explicit flags and exits `3` on conflict.

The harness writes only `acp2.acceptance.v1` receipts through B's
`write_result`, sets the C fields, then validates with B's `validate_receipt`.
On validation failure it deletes the bad receipt and exits `1`. It never
rewrites an existing receipt (append-only evidence). `route.json` is a T-07
diagnostic and is intentionally not produced here.

## 9. Non-goals of T-06

- No scenario body is registered in `acp2_experience_scenarios.py`; T-07 adds
  the five P0 bodies. An unregistered scenario is `NOT_VERIFIED` (exit `2`) or
  a dry-run `NOT_VERIFIED` (exit `0`).
- No Controller, model, DSH, QC or platform operation is executed.
- No B-owned file, public schema, registry or validator is modified.
