# M4-01 — no-QC full product chain and fault matrix (G20)

- status: **PASS**
- evidence_kind: `fixture` (fixture; not real science/model/browser)
- model adapter: `test-fixture`
- fault cases: 19/19
- git HEAD: `1a473b44b64ad1a1328726442e6718e998c53579`

## What is real

- Controller (uvicorn/FastAPI/SQLite), M2-01 research store/writer/approvals;
- MCP stdio server + RPH bridge (proposal create/read, approval query/decide,
  bounded wait, snapshot, submit, status, prepare, read);
- the Supervisor state machine over real approval/submit/wait ports (no
  evidence port in this run, so it must stop rather than accept);
- the M1-03 evidence projection and decision policy;
- the Workbench client/view modules in Node over the live HTTP Controller.

## Declared seams

1. `cccp.dispatch` — the synthetic `fake` workflow; the attempt receipt,
   `task_result.json` and the evidence report are written by a controlled stub
   that follows the worker contract. No QC, no LSF.
2. `model.adapter` — the explicit test adapter (deterministic skills plus
   adversarial model text). The real model branch is implemented in
   `acp2_full_chain_mcp.mjs` (`runProductionSupervisor` + gateway transport) and
   is selected by M4-02 with `--model model-gateway`.

## Not claimed

- real science / QC / LSF execution (M4-02, M4-03);
- a real model or DSH provider run (M4-02);
- real browser/Chromium (M4-02; the Workbench row here is Node + real HTTP);
- remote/LSF publication retry against a real cluster (M4-03).

## Fault matrix

| case | expected | observed | result |
|---|---|---|---|
| `fault.cross_tool_same_key` | `cross_tool_key_isolation` | `cross_tool_key_isolation` | PASS |
| `fault.cross_user` | `cross_user_isolation` | `cross_user_isolation` | PASS |
| `fault.illegal_remote_target` | `illegal_remote_target_refused` | `illegal_remote_target_refused` | PASS |
| `fault.forged_receipt` | `wrong_owner` | `wrong_owner` | PASS |
| `fault.approval_bypass` | `approval_bypass_refused` | `approval_bypass_refused` | PASS |
| `fault.budget_concurrency` | `budget_concurrency_one_admission` | `budget_concurrency_one_admission` | PASS |
| `fault.wrong_evidence_identity` | `foreign_research` | `foreign_research` | PASS |
| `fault.cursor_expiry` | `cursor_expired` | `cursor_expired` | PASS |
| `fault.publication_retry` | `publication_retry_once` | `publication_retry_once` | PASS |
| `fault.crash_recovery` | `crash_recovery_same_chain` | `crash_recovery_same_chain` | PASS |
| `fault.r01_incomplete_task_result` | `incomplete_result_not_published` | `incomplete_result_not_published` | PASS |
| `fault.r02_qc_rescue_over_limit` | `qc_rescue_over_limit_blocked` | `qc_rescue_over_limit_blocked` | PASS |
| `fault.r03_runtime_identity_mismatch` | `attempt_identity_mismatch` | `attempt_identity_mismatch` | PASS |
| `fault.r04_lost_right_intermediate_write` | `lost_right_write_refused` | `lost_right_write_refused` | PASS |
| `fault.r05_historical_attempt_usage` | `historical_attempt_usage_settled_once` | `historical_attempt_usage_settled_once` | PASS |
| `fault.r06_planning_quota_without_taskref` | `planning_claim_without_taskref_is_durable` | `planning_claim_without_taskref_is_durable` | PASS |
| `fault.r07_legacy_key_and_data_row` | `legacy_key_and_row_reconciled` | `legacy_key_and_row_reconciled` | PASS |
| `fault.r08_malicious_artifact_and_receipt` | `malicious_artifact_cannot_authorize` | `malicious_artifact_cannot_authorize` | PASS |
| `fault.revoked_approval` | `approval_revoked` | `approval_revoked` | PASS |

## Files

- `summary.json` — chain, rounds, findings, limitations
- `fault-matrix.json` — one row per case with its assertion
- `performance.json` — Data idempotency + Controller call counts + p50/p95
- `stub-boundaries.json` — the declared seams
- `trace.jsonl` — the orchestrator step trace
- `mcp-round0/`, `mcp-round1/` — the MCP/bridge runner trace and result
- `workbench/` — the Workbench driver scenario, stdout and stderr
- `controller.log`, `controller.restart.log` — the real Controller logs
