# B20a — Limited Diels–Alder runtime slice (todo 22)

**Result: `NOT_VERIFIED(c24a_absent)`.** B20a-ready passed with zero side effects; B20a-exec was not attempted because C24a has no approved single submission. No task, QC process, prepare operation, reservation or bsub was created.

## Three separate statuses

| Field | Value | Meaning |
|---|---|---|
| scenario `result` | `NOT_VERIFIED` | `da-runtime/da-submit` cannot execute without C24a. |
| `claim_status` | `not_evaluated` | No authorized TS/frequency/IRC/energy artifact exists. |
| `h4_gate` | `open` | Real positive rows and B20a-search are still missing. |
| B20a-ready sub-result | `PASS` | Interface/preflight consumed and validated without side effects. |
| B20a-exec | `NOT_VERIFIED(c24a_absent)` | Exact C24a artifact and fields recorded; nothing fabricated. |
| B20a-search | `NOT_VERIFIED(search_not_declared)` | Known-candidate chain is not search evidence. |
| Incomplete-input negative | `PASS` | Rejected before dispatch with `incomplete_scientific_input:atom_mapping_incomplete`. |

## Receipt

- `da-runtime-da-submit-20261023T090146Z-1420676-2da363de.json` — exit 2, `evidence_kind=live`, `candidate_revision=9ee70af…`, `claim_status=not_evaluated`, `h4_gate=open`.
- Candidate: server `9ee70af73c59db5fb5e1738064f7f4b4123849fd`; client `39d4aeb20451c984e20d27e12270a15070adce12` (C HEAD, read-only contracts); harness `9d291a18…`; contract `7d35beef…`; env `a9891b47…`.

## B20a-ready (zero side effects)

`b20a-ready.json` + `counters.json`:

- Consumed A-14..A-16 contracts: `reaction/recipe/task-binding/evidence` schemas and the frozen `reaction-da.json` example.
- Consumed B18 `da-preflight` receipt `…/B18/da-runtime-da-preflight-20261023T080839Z-1375609-088dcea2.json` (`result=PASS`).
- `tasks_created=0`, `prepare_operations=0`, `qc_invocations=0`, `reservations=0`, `bsub=0`; run-root directory set unchanged; only `GET /api/health` and `GET /api/v1/jobs` were called.
- Interface: `--scenario da-runtime --phase da-submit`, explicit `--phase`, `submission_attempted=false`.

## P2 test candidate

`p2-candidate.json` (overlay `sha256:6e6ccf5cdda3a37403eea2987383903301724e8e20b3370466fc9a2401f8958e`): built as a clean overlay (`data/b/b20-candidate` = `git archive HEAD` + the declared B20 files). Unrelated dirty working-tree changes are excluded; the overlay content matches the files used for the run. Frozen P1 candidate `77ac1de…` was not modified. A/C contract digests are recorded in the same file.

## Run spec and timeout

`run-spec.json`: science `13380s` + export `900s` + aggregate `120s` = total `14400s`; queue wait included in the same cumulative window; Local selected (no LSF client/queue/node); in-flight steps stopped with confirmed termination or retained by reference. Representative B14b Local timings (upper bounds): singlepoint 6s, optimize 185s, frequency 3s. `started_at=null` because the science clock must start at the first C24a-approved submit. `ACP2_DA_TIMEOUT=14400` is frozen in `data/b/acceptance-env.sh` (0600) and `B/fixture-runs.json`; an unset timeout is a usage error.

## Dependency edges

`runtime/dependencies.json` now carries: A-14/A-15/A-16 → B20a-ready (external AND edges), B20a-ready → C/T-34 (C24a) consumer edge, C24a → B20a-exec (external AND edge), B20b depends on B20a-exec. C plan T-34 is `[~]` and already gates on B20a-ready; `c24a-gap.json` records the observed C state.

## C24a gap

Expected artifact: `reports/acp2_implementation/bb45a46/C/da-research-live/a/c24a-submission.json` with `proposal_digest`, `approval_ref`, `job_id`, `recipe_digest`, `budget_basis`, `run_spec`, `search_capability`. It is absent; C's plan marks T-34 `[~]` and no `da-research-live` C evidence exists. No substitute submission was run.

## Search gap

`search-gap.json`: no declared input class, TS seed, candidate source/count, generation steps, filtering/rejection reasons, validator or success criteria. Search stays `NOT_VERIFIED`; H4's search row stays open. The known-candidate chain is explicitly not counted as search.

## Incomplete-input negative

`incomplete-input-negative.json`: `missing-atom-mapping` fixture removed `ethene:et_h4` from both mapping directions and marked it unmapped. The deterministic gate rejected it before dispatch with `incomplete_scientific_input:atom_mapping_incomplete`; `dispatch_attempted=false`.

## H4 separation

`status-separation.json` keeps scenario result, scientific claim and H4 gate independent. A readiness PASS and a negative-honesty PASS do not close H4 and do not imply a supported scientific claim.

## Verification

- `pytest tests/test_acp2_runtime_acceptance_harness.py tests/test_acp2_runtime_contracts.py tests/test_acp2_runtime_science_binding.py -q` → 74 passed (`pytest.txt`, exit 0).
- `ruff check` and `ruff format --check` on the changed Python files → exit 0.
- `validate_acp2_contracts.py` → `VALID` (manifest digests refreshed after the additive `claim_status`/`h4_gate` schema fields).
- `acp2_runtime.py --check-only summary.json` → `VALID` (tool check only, not a scenario PASS).

## Cleanup

`cleanup.json`: one Controller on `127.0.0.1:18781`, SIGTERM sent, process gone, port free, `run-root-b20a` removed; production `8765` and `/var/lib/acp/runs` untouched. `tasks_created_by_b20a=0`.

## Remaining

B20a-exec requires a real C24a approved submission from the real DSH; B20a-search requires its own declared search run. H4 remains open until both plus the real positive TS/IRC/comparable-energy rows exist. B20b consumes B20a-exec.
