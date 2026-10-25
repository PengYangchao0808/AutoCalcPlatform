# ACP 2.0 acceptance harness contract — `acp2.acceptance.v1`

Status: **contract_ready** (B01 skeleton; live scenario implementation is owned by the
per-scenario tasks named below).
Owner: B. Consumers: A, C, integration.
Root schema: [`acceptance-result.schema.json`](acceptance-result.schema.json) —
`schema` is the constant `acp2.acceptance.v1`.
Schema dialect: `https://json-schema.org/draft/2020-12/schema`.
Contract version: `1.0.0`.

This is the **single three-track receipt contract**. A, B and C do not ship
look-alike envelopes. They reuse this schema verbatim and add only namespaced
`extensions` (or the optional compatibility fields listed in §4). The executable
validator is `scripts/acceptance/acp2_runtime.py` (`validate_receipt`,
`validate_summary`); A/C may import it or run `--check-only` / `--validate-samples`.

## 1. Three separate checks (never conflate)

| Check | What it proves | Does **not** prove |
|---|---|---|
| JSON parse (`python -m json.tool`, `json.loads`) | bytes are a JSON document | schema validity, refs, digests |
| Schema + semantic validation | document shape, enums, required fields, registry and nullable matrix | referenced files exist, digests match |
| Cross-file resolution (`--check-only`) | every `$ref` resolves, every `artifacts[].path` exists and its `size_bytes`/`sha256` match, every summary child receipt exists | the scenario actually passed |

A result file that only survives `json.tool` is **not** accepted evidence.

## 2. Canonical receipt fields

Required at the top level (see the schema for exact types):

`schema`, `schema_dialect`, `contract_version`, `owner`, `scenario`, `phase`,
`run_id`, `candidate_revision`, `candidate{server_revision,client_revision,harness_revision}`,
`command` (`string[]`, never a shell string), `cwd` (independent absolute working
directory), `started_at`, `finished_at`, `result`, `scope`, `reason`, `exit_code`,
`artifacts:[{path,size_bytes,sha256}]`, `evidence_kind`,
`job_id`, `operation_id`, `attempt`, `release_id`, `submission_id`,
`manifest_generation`, `negotiated_protocol`, `extensions`.

Optional: `assertions`, `usage`, `readiness`, `delivery_status`, `prerequisites`,
`sub_results`, `run_key`, `receipt`, `platform` (ingest alias; see §3).

Timestamps are RFC 3339 UTC with `Z` (`started_at`/`finished_at`). Legacy spellings
(`started`/`finished`, `started_on`/`finished_on`) are **illegal**; they are rejected
as unexpected fields / missing required fields. Artifacts replace C's historical
`evidence` array; `evidence` is accepted only as an ingest alias when `artifacts`
is absent.

`candidate_revision` is the full candidate identity (40-hex Git SHA or
`sha256:<64-hex>` source overlay). `candidate` is B-owned: `server_revision` is the
B source candidate, `client_revision` is C-owned, `harness_revision` is the B01
harness identity (SHA-256 of `scripts/acceptance/acp2_runtime.py`), all nullable
until delivered.

## 3. Canonical platform references and ingest aliases

Canonical location is **top-level**:

`job_id`, `operation_id`, `attempt`, `release_id`, `submission_id`,
`manifest_generation`, `negotiated_protocol`.

`platform{...}` is an **ingest-only alias**. The validator copies each present
member to its top-level field and removes `platform`. If the same reference exists
both top-level and under `platform` with different values, the receipt is rejected
(`platform_alias_conflict:<field>`). A top-level value wins only when it is the
same value; otherwise the document is ambiguous and never accepted.

Other ingest-only aliases:

| Alias | Canonical | Rule |
|---|---|---|
| `scenario_id` | `scenario` | the **only** accepted scenario alias; conflicting duplicate rejected |
| `preflight` (phase) | `da-preflight` | read alias |
| `runtime:*` (C scenario) | `experience:*` | display alias; not a second B ID |
| `deployed` (readiness) | `integration_ready` | read-only alias |
| `evidence` | `artifacts` | only when `artifacts` is absent; conflicting duplicate rejected |

After normalization, absent platform references are written as `null`, so the
required/null matrix below is judged on the normalized document.

## 4. Enumerations

- `owner`: `B | A | C | integration`.
- `result`: `PASS | FAIL | NOT_VERIFIED`.
- `scope`: `IN_SCOPE | DEFERRED | NOT_APPLICABLE`.
- `evidence_kind`: `fixture | live | aggregate`.
- `phase`: `main | approval | budget | da-preflight | da-submit | da-evidence | fixture | live | aggregate`,
  plus namespaced extensions (`science:*`, `experience:*`, `integration:*`, `da:*`,
  `infra:*`, `multitenant:*`). Independently signable sublines use namespaced values:
  `experience:fixture`, `experience:live`, `experience:control`,
  `experience:search`, `experience:nmr`, `experience:thermo`,
  `experience:selectivity`; `infra:install`, `infra:upgrade`;
  `multitenant:authz`, `multitenant:two-project`.
- `readiness`: `contract_ready | implementation_ready | integration_ready | verified`
  (ingest alias `deployed` → `integration_ready`).
- `delivery_status`: `planned | blocked`.
- `operation_class`: `submit | read | prepare | discover | standalone | control | aggregate`.
  This is registry metadata, **not** a free-text receipt field. A/C may declare it
  under `extensions["acceptance.operation_class"]` for a non-B scenario.
- `target`: `local | remote | either` (registry metadata; A/C may declare
  `extensions["acceptance.target"]`).

`fixture` / `live` phase values must agree with `evidence_kind`; the same rule
applies to namespaced `*:fixture` / `*:live` phases. `mode` and `subcase` are
display metadata only and never select behavior.

## 5. Required / null matrix

The matrix is enforced by evidence kind plus the scenario's declared
`operation_class` and `target`. `live-submit` and `live-read` are sub-cases of
`evidence_kind=live`, selected by the registry — never by a free-text field.

| Kind / operation | `job_id` | `attempt` | `release_id` | `submission_id` | `operation_id` | `manifest_generation` | `negotiated_protocol` |
|---|---|---|---|---|---|---|---|
| `fixture` (any) | null | null | null | null | null | null | null |
| `live` + `discover` / `standalone` | null | null | null | null | null | null | optional (protocol probe) |
| `live` + `prepare` | null | null | null | null | optional | null | optional |
| `live` + `submit`, local | **required** | **required** | optional | **null** | null | null | optional |
| `live` + `submit`, remote | **required** | **required** | **required** | **required** | null | null | optional |
| `live` + `read`, local | **required** | **required** | **required** | **null** | optional | required for artifact reads | optional |
| `live` + `read`, remote | **required** | **required** | **required** | **required** | optional | required for artifact reads | optional |
| `live` + `control` | optional | optional | optional | optional | optional | optional | optional |
| `aggregate` | optional | optional | optional | optional | optional | optional | optional; at least one child ref |

"Required" means a non-null value of the declared type. "Null" means the field
must be `null` (a non-null local `submission_id`, a fixture carrying a `job_id`,
or a live discovery carrying a `job_id` is rejected). `manifest_generation` is
required for `artifact` and `da-evidence` reads, optional for other reads.

An `aggregate` receipt must carry at least one child reference in `sub_results`
(`receipt` + `result` + `phase`). A parent aggregate that declares
`extensions["da.required_child_phases"]` must list **every** required child; a
missing child is rejected (`aggregate_missing_required_child_phase:<phase>`), and a
parent `result=PASS` is rejected when any required child is not `PASS`
(`aggregate_child_not_pass:<phase>`). Aggregates never choose an arbitrary child
PASS.

## 6. Scenario identity and ownership

Receipt identity is `(owner, scenario, phase, run_id)`. `run_id` is unique per
execution; a stage summary references immutable run files and never rewrites them.

- B's 15 IDs are **immutable and unprefixed**:
  `env-runtime`, `entry-readonly`, `mcp-stdio`, `submit-replay`,
  `local-singlepoint`, `lsf-singlepoint`, `three-entry`, `artifact`,
  `control-recovery`, `approval-budget`, `da-runtime`, `infra-plugin`,
  `multitenant`, `migration`, `package-install`.
- A adds `science:*`; C adds `experience:*`. `dsh-local-singlepoint` is a
  **C-owned example** (`experience:dsh-local-singlepoint`), not a 16th B scenario.
- Exactly one implementing owner is named per `(scenario, phase)`. Shared
  scenarios have one writer and named consumers.
- An undeclared `(scenario, phase)` combination is a **usage error (exit 3)**.

## 7. Frozen registry

Prerequisites are inputs the harness must observe, not work it performs.
`controller` means connect to an existing Controller; the harness never starts one
unless it explicitly records `started_controller=true` for a test Controller.

| Scenario | Phase | Implementer | operation_class | target | Prerequisites | Allowed side effects | Timeout |
|---|---|---|---|---|---|---|---|
| `env-runtime` | `main` | B07 | discover | local | `--out` | none | 120 s |
| `entry-readonly` | `main` | B04a | discover | local | `--controller`, `--out` | none (no submit/page) | 120 s |
| `entry-readonly` | `live` | B04a | discover | local | `--controller`, `--out` | none | 180 s |
| `mcp-stdio` | `main` | B05 | discover | local | `--launch`, `--controller`, `--out` | process start only; no task/page/science | 180 s |
| `submit-replay` | `main` | B08 | submit | local | `--controller`, `--fixture`, `--out` | create one task; retries are idempotent | 600 s |
| `local-singlepoint` | `main` | B10 | submit | local | `--controller`, `--fixture`, `--out` | one local QC task | 1800 s |
| `local-singlepoint` | `live` | B10 | submit | local | `--controller`, `--fixture`, `--out` | one local QC task | 1800 s |
| `lsf-singlepoint` | `main` | B11 | submit | remote | `--controller`, `--fixture`, `--out` | one LSF submission | 3600 s |
| `lsf-singlepoint` | `live` | B11 | submit | remote | `--controller`, `--fixture`, `--out` | one LSF submission | 3600 s |
| `three-entry` | `main` | B10 | submit | local | `--controller`, `--fixture`, `--out` | CLI/HTTP/MCP submit the same request | 900 s |
| `artifact` | `main` | B12 | read | local | `--controller`, `--fixture`, `--out` | prepare + cache-only read | 900 s |
| `control-recovery` | `main` | B13 | control | local | `--controller`, `--fixture`, `--out` | pause/unpause/cancel/restart | 1800 s |
| `approval-budget` | `approval` | B16 | control | local | `--controller`, `--fixture`, `--out` | approval request/decision only | 600 s |
| `approval-budget` | `budget` | B17 | control | local | `--controller`, `--fixture`, `--out` | reservation/settlement only | 600 s |
| `da-runtime` | `da-preflight` | B18 | discover | local | `--controller`, `--fixture`, `--out` | none | 300 s |
| `da-runtime` | `da-submit` | B20a | submit | local | `--controller`, `--fixture`, `--out` | one task submit | 900 s |
| `da-runtime` | `da-evidence` | B20b | read | local | `--controller`, `--fixture`, `--out` | cache-only read; no implicit fetch | 300 s |
| `da-runtime` | `aggregate` | B20c | aggregate | local | `--controller`, `--fixture`, `--out` | none | 120 s |
| `infra-plugin` | `infra:install` | B21 | control | local | `--fixture`, `--out` | install plugin/adapter | 900 s |
| `infra-plugin` | `infra:upgrade` | B22 | control | local | `--fixture`, `--out` | upgrade plugin/adapter | 900 s |
| `multitenant` | `multitenant:authz` | B15 | control | local | `--controller`, `--fixture`, `--out` | denied requests only | 600 s |
| `multitenant` | `multitenant:two-project` | B23 | control | local | `--controller`, `--fixture`, `--out` | isolated project tasks | 900 s |
| `migration` | `main` | B24a | control | local | `--fixture`, `--out` | export/import/backup restore | 1800 s |
| `package-install` | `main` | B25b | control | local | `--out` | clean-environment install | 1800 s |

The registry is emitted by `acp2_runtime.py --help`. Adding a phase or scenario
requires the owner named here to update this table and the executable registry in
the same change.

## 8. Validation layers and error classes

| Layer | Owner | Examples |
|---|---|---|
| JSON structure / schema shape | B | type, required, enum, `minimum`/`maximum`, union types for nullability, `$ref` |
| CCCP scientific semantics | A | scientific thresholds, unit-bearing values, science payload meaning |
| Runtime platform constraints | B | scenario registry, platform reference matrix, alias conflicts, token/budget authorization |

Use JSON Schema 2020-12 standard keywords. `range` and `nullable` are **not**
validation keywords: encode bounds as `minimum`/`maximum` and nullability as a
union type (`["string", "null"]`), or mark such annotations as UI-only extensions.

### Unknown-field policy (per operation)

- New execution requests reject unsupported execution fields.
- Historical reads preserve unknown fields but gain no execution or recovery
  eligibility.
- An unknown **major** `contract_version` does not execute.
- Extra metadata lives only under a namespaced `extensions` key. Un-namespaced
  extension keys are rejected.

### Stable reason classes

Schema reasons are `schema:<keyword>:$:<field>` (for example
`schema:missing_required:$:owner`, `schema:enum:$:result`). Semantic reasons:

`unsupported_contract_version_major`, `scenario_unknown`, `phase_undeclared`,
`owner_scenario_prefix_mismatch`, `scenario_alias_conflict`,
`platform_alias_conflict`, `evidence_alias_conflict`, `phase_evidence_kind_mismatch`,
`fixture_platform_refs_not_null`, `live_discover_platform_ref`,
`live_standalone_platform_ref`, `live_submit_missing_job_id`,
`live_submit_missing_attempt`, `live_submit_missing_release_id`,
`live_submit_missing_submission_id`, `live_submit_submission_id_not_null_local`,
`live_submit_manifest_generation_not_null`, `live_read_missing_job_id`,
`live_read_missing_attempt`, `live_read_missing_release_id`,
`live_read_missing_submission_id`, `live_local_submission_id_not_null`,
`live_read_missing_manifest_generation`, `aggregate_missing_child_refs`,
`aggregate_missing_required_child_phase`, `aggregate_child_not_pass`,
`artifact_missing`, `artifact_size_bytes_mismatch`, `artifact_sha256_mismatch`,
`unresolved_ref`, `unknown_summary_schema`, `summary_missing_run`,
`summary_candidate_revision_mismatch`.

## 9. Harness CLI and exit codes

```text
python scripts/acceptance/acp2_runtime.py --help
python scripts/acceptance/acp2_runtime.py --scenario env-runtime --dry-run --out /tmp/acp2-env
python scripts/acceptance/acp2_runtime.py --scenario mcp-stdio --launch launch.json \
    --controller http://127.0.0.1:8765 --out /tmp/acp2-mcp
python scripts/acceptance/acp2_runtime.py --check-only /tmp/acp2-mcp/summary.json
python scripts/acceptance/acp2_runtime.py --validate-samples
```

| Exit | Meaning |
|---|---|
| `0` | `PASS` for an executed scenario, **or** the tool check itself worked (`--help`, `--dry-run`, `--check-only`, `--validate-samples`) |
| `1` | `FAIL` — the scenario executed and an assertion failed, or a validation/corpus check failed |
| `2` | `NOT_VERIFIED` — missing environment, capability or prerequisite |
| `3` | usage error: unknown scenario/phase, undeclared `(scenario, phase)`, missing required argument |

`--help`, `--dry-run` and `--check-only` are **never** scenario PASS. `--dry-run`
prints the exact commands (no literal `...`) and creates no DB, process or job.
`--controller` connects to an existing Controller and never starts one.
Unimplemented scenarios return `2` with a named missing capability; there is no
fake PASS.

### Result writer

A run writes one immutable `<scenario>-<phase>-<run_id>.json` and a `summary.json`
(`acp2.acceptance.summary.v1`) that references it by relative path. The run file
contains every field in §2. A stage summary references immutable run files and
never overwrites them.

## 10. Process, submission and cleanup accounting

The harness records every PID, TaskRef (`job_id`, `attempt`, `release_id`,
`manifest_generation`) and `submission_id` it creates. On timeout it:

1. saves logs;
2. cancels **only harness-created active tasks** through the platform;
3. confirms stop/reconciliation;
4. exports native files, manifest and digests;
5. stops adapters;
6. stops the test Controller **only if the harness started it**.

The resulting `timeout-cleanup.json` lists the tracked references and this order.
A scenario may never be marked `PASS` by a cleanup-only run.

## 11. Cross-track handoff status (B01, 2026-10-22)

B01 is the common first handoff, not a B-only harness. A, B and C each owe one
legal and one illegal sample, and this single validator must accept/reject them
before the protocol freezes.

Current status:

| Track | Legal sample | Illegal sample | Status |
|---|---|---|---|
| B | `samples/acceptance/positive/b-*.json` | `samples/acceptance/negative/*.json` | delivered |
| C | `positive/c-dsh-local-singlepoint-{fixture,live}.json` (B-authored **shape exemplars**; C-authored sample still absent) | pending C | `NOT_VERIFIED / blocked_fixture` |
| A | pending A | pending A | `NOT_VERIFIED / blocked_fixture` |

See `samples/acceptance/pending-a-c/README.md` for the exact expected inputs.
The schema may be frozen for B skeleton work, but the three-track freeze remains
blocked until A/C deliver their samples and the same validator accepts them.

## 12. A/C consumption rules

- Reference `acceptance-result.schema.json` verbatim; do not fork the envelope.
- Add metadata only under namespaced `extensions` (or the optional `run_key` /
  `receipt` compatibility fields for A).
- `prerequisites` and `sub_results` are optional top-level compatibility fields;
  aggregate child references always live in `sub_results`.
- Name scenarios `science:*` / `experience:*` and use `extensions`
  (`acceptance.operation_class`, `acceptance.target`) when the operation class is
  not inferable from a B registry entry.
- The 15 B IDs are never reused with a different meaning.
