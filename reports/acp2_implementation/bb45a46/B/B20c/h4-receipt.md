# B20c — H4 aggregation and limited P2 candidate freeze (todo 24)

**Aggregation result: `PASS` (scope: aggregation only).**
**H4 status: `OPEN`. Scientific result: `NOT_VERIFIED`. Promotion allowed: `false`.**

This receipt aggregates the P2 phase evidence. It does **not** close H4, does **not** sign A-20 or
C25, and does **not** claim that the Diels–Alder reaction mechanism is proven.

## 1. Frozen P2 candidate

| Field | Value |
|---|---|
| Candidate commit (`candidate_sha`) | `1ffb76da3b5d36ef6b9e67e93b004a74bfe1a000` |
| Candidate tree | `041706226619a403256a2ea206d05ad30a509033` |
| Content revision (`candidate_revision`) | `sha256:c415d71598e43ac5de1046616b5fbc4c1b808881eebe56c0731642027788932e` |
| P2 wave base (`base_revision`) | `c3040007cd360af2373b6d1710c763634377761f` |
| Parent revision | `e0ae07b4f1e9f6bce697e5e7676c58595060c90b` |
| `server_revision` | `1ffb76da3b5d36ef6b9e67e93b004a74bfe1a000` |
| `client_revision` | `6905477c51c34fa1f8d53421553805ecbf88d897` (C HEAD; C24a/C25 absent) |
| `harness_revision` | `2fc709f77bef5c15dbd3659bf50e3038fe95afc32435e1e9b0ecab3d71cdc884` |
| `contract_digest` | `7d35beef1c1e777bac9493189023d1d868fd30d2bfc9c47d7854624a34fd97b6` |
| `contract_version` | `1.3.0` |
| `env_digest` | `d487f3af442db1f9d98ba6317157cd715a61002360d375f549f6bf0b4dfde8b6` |
| Freeze kind | `verified_immutable_overlay` |
| Overlay root | `/opt/acp2-dev/data/b/b20-candidate` |
| Frozen P1 candidate | `77ac1de1af6a5db6b57f4f904a859305ff73042c` — not reused, not modified |

The source checkout is intentionally dirty with unrelated changes, so the freeze is **not** claimed
as a clean worktree. Instead the freeze is an immutable overlay produced by `git archive 460b0db`
into a fresh directory. Verification compared all **2907** tracked files by `git hash-object`
against the committed blob: **2907 verified, 0 mismatches, 0 missing, 0 extra**. Per-file SHA-256
values are recorded in `candidate-files.sha256`; the aggregate digest of that manifest is recorded
in `p2-candidate.json`.

A/C P2 contracts are referenced by digest only (A: reaction/recipe/task-binding/evidence,
`reaction-da.json`, `handoff-H4.json`; C: proposal/research-record, `p2-operation-map.md`,
`plan-identity.md`). They are not vendored and are not editable truth in this repository.

## 2. Shared run spec (does not fail the evidence phase)

`13380s` science + `900s` export + `120s` aggregate = `14400s` total
(`ACP2_DA_TIMEOUT=14400`). `started_at = null` because no C24a-approved submit exists.
The run spec is one cumulative budget; the export and aggregate windows are used and H4 closes by
**actual evidence**, never by elapsed time. The evidence phase is therefore not failed by the
`13380/900/120/14400` values.

## 3. DA fixture and run keys

| Key | Value |
|---|---|
| Scenario / phase | `da-runtime` / `da-submit` |
| `run_id` | `20261023T091047Z-1425776-5bf82a03` |
| Fixture | `/opt/acp2-dev/data/b/fixtures/da/da-run.json` (`ef543b69…`, 2413 bytes) |
| Phase result | `NOT_VERIFIED`, reason `c24a_absent`, exit `2` |
| Platform refs | `job_id`/`operation_id`/`attempt`/`release_id`/`submission_id`/`manifest_generation` all `null` |
| Search run key | `null` (`NOT_VERIFIED(search_not_declared)`) |

`tasks_created=0`, `prepare_operations=0`, `qc_invocations=0`, `reservations=0`, `bsub=0`,
`submission_attempted=false`. No execution fact is turned into a supported scientific claim.

## 4. Cross-track sign-off references

### A-20 — `NOT_VERIFIED(a20_absent)`

- Expected evidence: `reports/acp2_implementation/bb45a46/A/A-20/result.json`
- Accepted pattern (A plan): `.../A/A-20/<run-id>/result.json`
- Expected schema: `acp2.acceptance.v1`; the science sign-off is `handoff-H4.json`
  (`handoff_id=H4`, `handoff_version=1`).
- Digest: **null** (not fabricated).
- Only the provisional A contract handoff exists (`3c1691b0…`), and its own `h4_closure.closed`
  is `false`. It is not an A-20 real-run sign-off.

### C25 — `NOT_VERIFIED(c25_absent)`

- Expected handoff: `/opt/acp2-dev/C/docs/reference/next-change/acp2-contracts/experience/handoffs/H4.json`
- Expected receipt root: `reports/acp2_implementation/bb45a46/C/p2-closeout/`
  (schema `acp2.acceptance.v1`).
- Digest: **null** (not fabricated).
- C T-34 and T-35 are both `[~]` blocked; no `p2-closeout` receipt and no C `H4.json` exist.

## 5. Open rows (H4 stays OPEN)

| Row | Owner | Status | Why it is open |
|---|---|---|---|
| `H4-01-valid-ts` | A | `NOT_VERIFIED` | No real authorized TS optimize/frequency artifact (one imaginary mode with mapped mode). |
| `H4-02-bidirectional-irc` | A | `NOT_VERIFIED` | No real forward/reverse IRC receipts or direction results. |
| `H4-03-endpoint-connectivity` | A | `NOT_VERIFIED` | No real endpoint geometries or atom-order-stable mapping. |
| `H4-04-comparable-energy` | A | `NOT_VERIFIED` | No real energy/thermochemistry artifact; B18 standard-state reconciliation still required. |
| `H4-search` | A | `NOT_VERIFIED(search_not_declared)` | B20a-search published no input class, candidate accounting, generation steps, rejections or criteria. |
| `A-20-science-signoff` | A | `NOT_VERIFIED(a20_absent)` | A real-run failure/reverse-case gate result is absent. |
| `C25-experience-closeout` | C | `NOT_VERIFIED(c25_absent)` | C T-36 p2-closeout is blocked on T-34/T-35. |

No row is signed by a non-owner. B signs only execution/release/attempt/manifest/budget/
evidence-reference facts; A and C rows carry `signed_by: []`. A control-loop or known-candidate
PASS is not search evidence, and one readiness/negative PASS does not cover an unexecuted control
or failure case.

## 6. Closeout input set and digest chain

Inputs (8 P2 phase results): `B20a-ready`, `B20a-exec`, `B20a-search`, `B20b`, `B19`, `B18`,
`B17`, `B16`.

- `closeout_input_digest = d7795d45f952db139583e6b85292b1b905699c7574b9dc427faf66cdcc7fce3b`
  — SHA-256 over sorted `<repo-relative path> <file sha256>\n` lines of those eight inputs only.
- The closeout result (`h4-closeout.json`) is **not** in the input set; its own digest appears in
  no input entry. Validation ran against the closed input set before the closeout was written.
- `final_reference_digest = e915a12a447ef998e9309217d62c18f5c7c087b99a95836c4c66e3503a2f84d7`
  — SHA-256 over canonical JSON of the closeout with `final_reference_digest=""`. The final index
  (`h4-final-index.json`) references this value; the closeout never references the index as input.

## 7. Reusable research records

Reusable: the P2 candidate freeze, B20a readiness/execution facts (as `NOT_VERIFIED` provenance),
contracts by digest, B19 events/recovery facts, A/C contract references, and the shared run spec.

Not reusable: A scientific sign-off, C experience sign-off, the real DA chain, search evidence,
user approval, supported science claims, and any mechanism conclusion.

## 8. Rerun commands

Exact argv arrays (no placeholders) are in `rerun-index.json`, covering the B20a ready/exec
harness invocation, the `--check-only` tool check, the B20b handoff test, the B20c validation
test, and `validate_acp2_contracts.py`. `B20a-search` has no producer command and is recorded as
`NOT_VERIFIED(search_not_declared)` instead of an invented command.

## 9. ADR-008 boundary

Fixture selection and scientific acceptance belong to **A/user**. B prepares run evidence and
does not make the scientific call. A B20c `PASS` is an aggregation result only.
