# ACP 架构验收整改 — 修复候选再验收报告

**Status: BATCH COMPLETE / SCOPED — NOT "ALL ARCHITECTURE COMPLETE"**

**Code candidate SHA:** `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798`

**Evidence-only report SHA:** recorded in `evidence/report_sha256.txt` (the report cannot contain its own digest; the sidecar and `evidence_manifest.json` bind it).

**Baseline:** `d5d85e7c804878edd0cedb6bce1d32af2d92ad83` (20261019 FAIL report preserved read-only under `evidence/baseline-20261019/`).

> This report supersedes the 20261019 FAIL conclusions **only for the in-scope remediation batch**.
> It does not declare the whole architecture adjustment complete. `G4` is OPEN, real science/LSF are
> `NOT_VERIFIED`, and the six deferred capabilities remain registered, not implemented.

---

## 1. Scope and evidence vocabulary

- In scope: D01–D13, the D07 local/remote default worker chain (singlepoint first), additive prepare/read identity contracts, the D08/D09 real-browser fixes, quality/debt cleanup, packaging/role evidence, deferred-capability handoffs, and the four frozen combinations.
- Out of scope: account/permission/quota production wiring, MCP default entry, project-package CLI/API, metering wiring, persistent event A04, frontend prepare/availability consumption; Slurm/GPU/object storage/pricing; any production restart/release.
- Vocabulary: `PASS / FAIL / NOT_VERIFIED` plus `IN_SCOPE / DEFERRED / NOT_APPLICABLE`. An executed failure is never rewritten as `NOT_VERIFIED`; `DEFERRED` is never `PASS`; every environment skip names its missing prerequisite.

## 2. Candidate and environment

| Item | Value | Evidence |
|---|---|---|
| Code candidate | `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798` | `git rev-parse HEAD`; T39 |
| Baseline / F0 | `d5d85e7c804878edd0cedb6bce1d32af2d92ad83` | `evidence/collection.log` |
| Regressions | `10,005 passed, 11 skipped, 95 deselected` (27m41s) | `evidence/t39-nonslow.log` |
| Guard set | `2,124 passed` (6m42s) | `evidence/t39-guards.log` |
| Compile | `compileall` exit 0; syntax-error negative exit 1; import smoke OK | T39 evidence |
| Node | `327 passed, 0 failed`; wizard payload `5 passed` | T40 evidence |
| Browser | Chromium 153.0.8010.12; `4 passed` (NMR + vibration) | T40 evidence |
| Four combinations | F0/S0, F1/S0, F0/S1, F1/S1 — **all PASS** | `four_combinations/*.json` |
| Real science/LSF | **NOT_VERIFIED** (`ACP_M1_ORCA` / `ACP_LSF_NODE` unset) | `real_environment/*.json` |
| Packaging | Single `acp` wheel; CCCP-only/worker-only/Web isolation **G4 OPEN** | T35 evidence |

## 3. D01–D13 closure

| Defect | Original counterexample | Fix commit(s) | Formal regression | Verdict / limits |
|---|---|---|---|---|
| D01 | failed/cancelled/blocked rewritten to completed; exit 0 | `93fdb61` | `tests/test_services_worker_contract.py` D01 truth-table + real subprocess probes; suite 534/T39 10,005 | **PASS** — authoritative truth table; missing fact outranks `complete=True`; partial qualification digest-bound. No new JobStatus. |
| D02 | receipt reused on job/attempt/submission only; failure paths return old receipt | `bc9a426`, `b46a79e` | worker-contract identity matrix + worker-bootstrap conflict/holder tests | **PASS** — full identity predicate, acyclic imports, bytes preserved, loser writes no authoritative receipt. |
| D03 | v1 projection omits attempt/revision | `db4a4ec`, `5039cf2` | `tests/test_acp_api_v1.py`; Node `job_detail_controller.test.js` | **PASS** — v1/v2 parity, rerun invalidates old session, `name_revision` isolated. |
| D04 | local byte resources ignore resolved work_dir; remote-only cache | `21b9e4d` | `tests/test_services_read_root.py`, `test_services_cache_only_switch.py` | **PASS** — descriptor-pinned `O_NOFOLLOW` snapshot; local originals served; zero GET fetch/enqueue; historical miss refuses. |
| D05 | dedupe omits generation/digest; EXPIRED cannot revive | `3b2f8c3`, `3b97f70`, `ce47a9f` | `tests/test_services_prepare_operations.py` | **PASS** — canonical content identity, DB partial unique key, new `dedup_epoch` row, availability by expected identity. |
| D06 | lease reduced to job id; latest record prefetched | `b94c644`, `25cbd42` | prepare + transfer suites; barrier rerun/rebind/revoke/evict | **PASS** — whole lease, mandatory identity/authorization provider, publish-boundary re-check. Historical probe is an interface difference (no provider), not a pass. |
| D07 | no production freeze/verify/advance; ordinary submit uses `acp.cli` | `dbb3579`, `d82d9ee`, `1fe3d44`, `25b9513`, `ef2b54f`, `336ee8a`, `983cfb9`, `ac915aa` | chain-switch T24–T32; four combinations; T29 ordinary submit | **PASS (local + controlled remote)** — immutable release, group commit, claim, receipt/CAS/publication, singlepoint default only; real LSF **NOT_VERIFIED**. |
| D08 | success path clears canvas parent with `textContent = ""` | `7e8806f`, `28ec5ed` | `tests/test_acp_frontend_browser.py::test_nmr_canvas_survives_success` | **PASS** — canvas connected, atoms loaded, non-empty view/export; real Chromium. |
| D09 | `_arrowsVisible` gates first draw on `enabled` | `4ef1de1`, `0805f13` | real-Chromium vibration tests; Node vibration suite | **PASS** — cold-start arrows drawn from display intent; animation/combo/multi-instance/teardown verified. |
| D10 | bounded identity drops source generator/algorithm/version/params | `52edf7f`, `6ddc0dd` | `tests/test_cccp_bounded_products.py` D10 matrix | **PASS** — source digest in every derived/block identity; v1 readable but not adoptable; forged digest rejected. |
| D11 | availability converts `None` to attempt 0 | `2cbc2c1` | `tests/test_services_cache_only_switch.py`; read-root attempt tests | **PASS** — HTTP + direct builder resolve the authoritative attempt; `attempt=0` rejected; historical miss refuses. Probe D11 is a post-D05 identity interface difference. |
| D12 | source pins read only `wizard.js` | `625f62d`, `4bbb398` | phase5 pins; Node `wizard_submit_payload.test.js`; real page assembly | **PASS** — owner pins to `submit-edit.js`/`draft-form.js`; explicit 0/false preserved; retired defaults confined. |
| D13 | test patches `_MIGRATIONS` on the forwarder | `8d01b1d` | `tests/test_job_state_transitions.py::test_migration_backfills_legacy_attempts` | **PASS** — unique root, forwarder identity, `020` applied, idempotent reopen. |

Historical probe: `reports/architecture_acceptance_20261019/reproduce_defects.py`, sha256 `85d22f07c55768330dda653f528aad2ef8ec0b42f5da8a641409a25125141ecd` (unchanged). Result: 3 failed, 9 passed. The three failures are interface differences (D06 provider-less assembly, D07 direct `_build_execution_cmd`, D11 pre-D05 bare request); each maps to a formal test above and is not relabeled PASS or NOT_VERIFIED.

## 4. Default worker chain (D07) — scoped claim

- `DEFAULT_ENVELOPE_WORKFLOWS == {"singlepoint"}`. The other 14 active workflows keep their registered legacy paths; `ENABLED_WORKFLOWS` remains the eight-workflow scientific-closure set.
- Local singlepoint ordinary submit: freeze group → worker bootstrap → receipt → CAS → publication, verified by T27/T29/T41.
- Remote: immutable release, `src/acp_services` sync, manifest-last group upload, receipt + `task_result.json` fetch, three recovery windows, same-attempt protocol. Controlled fake transport only; real LSF **NOT_VERIFIED**.
- Support matrix: `support-matrix.md` separates scientific closure / default submit freeze / local / remote / publication / real science; 15/15 gate stays **OPEN**; only explicit default-enabled rows are marked default.

## 5. Packaging, roles and assets

- Built `acp-0.2.0-py3-none-any.whl` (89,957,369 bytes, sha256 `8a45a72a…`); `acp` 291 / `acp_services` 75 / `cccp` 189 files; no `frontend/`.
- Model/license assets present and hash-identical (`LICENSE-DP5`, `NOTICE.md`, `atomic_reps.gz`, `frag_reps.gz`).
- **G4 OPEN** (code-delivery gap): one combined wheel, no CCCP-only, worker-only, or Web/frontend packaging config. Owner: B — packaging/release. Entry condition: separate packaging configs + isolated build/import/serve smokes. Next gate: re-run T35.
- Worker-only frozen request **PASS** (exit 0, completed receipt, no jobs DB); negative `import acp` fails because the wheel includes `acp`. CCCP-only/Web isolation likewise not delivered.

## 6. Deferred capabilities (registered, not implemented)

`tests/baseline/services-evidence/handoff_registry.md` carries owner/blocker/entry condition/next gate/status for account-permission-quota wiring, MCP default entry, project-package CLI/API, metering wiring, persistent event A04, and frontend prepare/availability consumption. All remain `NOT_VERIFIED`/`OPEN`; no deferred capability was implemented or promoted to PASS.

## 7. A / B / C verdicts

| Layer | Verdict | Basis |
|---|---|---|
| A — defect fixes (D01–D13) | **PASS (in-scope code/tests)** | Formal coverage matrix + 10,005-pass suite + 2,124 guards + four combinations; historical probe interface differences recorded. |
| B — default chain / release | **PASS (local + controlled remote)** | singlepoint default chain, immutable release, CAS/publication, remote controlled transport; real LSF **NOT_VERIFIED**. |
| C — real function | **NOT_VERIFIED** | No real ORCA/CREST/xTB/CENSO or LSF/SSH/SFTP; no performance sample; packaging G4 OPEN. D08/D09 real-Chromium UI is PASS. |

## 8. Gates G1–G6

| Gate | Verdict | Scope / reason |
|---|---|---|
| G1 correctness | **PASS** | D01–D13 formal tests; no executed failure rewritten. |
| G2 formal regression | **PASS** | 10,005 non-slow + 2,124 guards + Node 327 + browser 4; 11 environment skips individually attributable to missing QC/LSF extras. |
| G3 default chain | **PASS (in-scope)** | Local + controlled remote; real LSF NOT_VERIFIED. |
| G4 independent delivery | **OPEN** | No CCCP-only/worker-only/Web packaging config; owner B packaging/release; next gate T35. |
| G5 functional acceptance | **PASS for D08/D09 UI**; **NOT_VERIFIED for real science** | Real Chromium assertions pass; no real QC available; product gate stays NOT_VERIFIED. |
| G6 release / run | **NOT_VERIFIED** | This plan does not restart/redeploy production; release/restart remains in the existing F1–F4 process. |

## 9. Batch completion verdict

**COMPLETE for the in-scope code/verification items this plan was required to deliver.** All 45 implementation todos are closed; the four final-wave reviews (F1–F4) are recorded alongside this report. `G4` is OPEN with an owner, the six deferred capabilities are registered with owners/gates, and real science/LSF/performance are `NOT_VERIFIED` with exact missing prerequisites. This is not a claim that the whole architecture adjustment is complete.

## 10. Residual limits and rollback

- Real science/LSF and performance remain environment gaps; they are not code-delivery failures.
- Packaging isolation remains a code-delivery gap (G4 OPEN).
- Baseline dirty facts (external docs and `tests/baseline/refactor-evidence/e2e-task-root.txt`) were recorded, never packaged or committed.
- Rollback for each defect is the corresponding commit's revert; compatibility shims/assets/fixtures and the 20261019 report are retained and were not deleted.

## 11. Evidence index

- Task evidence: `.omo/evidence/acp-architecture-acceptance-remediation/task-{1..45}-acp-architecture-acceptance-remediation.md`.
- Raw logs: `evidence/t39-nonslow.log`, `evidence/t39-guards.log`, `evidence/collection.log`, `evidence/verification.log`.
- Four combinations: `four_combinations/F0_S0.json`, `F1_S0.json`, `F0_S1.json`, `F1_S1.json`, `summary.json`.
- Real environment: `real_environment/m1-singlepoint.json`, `lsf-singlepoint.json`, `summary.json`.
- Packaging: `.omo/evidence/acp-architecture-acceptance-remediation/task-35-acp-architecture-acceptance-remediation.md`.
- Manifest and report digest: `evidence_manifest.json`, `evidence/report_sha256.txt`.
