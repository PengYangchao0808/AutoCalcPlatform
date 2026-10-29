# M4-02 model leg — real model gateway with declared no-QC scientific dispatch

> **Documentation correction, 2026-10-28.** This README inherited M4-01 template labels. Model reality is established by the [parent model receipts](../summary.json), and this leg's [summary](summary.json) records `evidence_kind=live` with `model_adapter=model-gateway`. That label establishes the real model branch; the declared scientific dispatch remains a fixture. This correction does not relabel frozen JSON, assert real QC, or close M3-03 production credential readiness. Current boundaries: [status](../../../../docs/reference/current/ACP_RPH_Status_20261028.md).

- status: **PASS**
- evidence boundary: real model gateway calls; fixture scientific dispatch; no real QC or browser inside this leg
- model adapter: `model-gateway`
- fault cases: 19/19
- git HEAD: `980122608540b27ff734745a5542bf3872bf1f66`

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
2. `model.adapter` — this run selected the real `model-gateway` branch
   (`runProductionSupervisor` + gateway transport). The parent M4-02 summary
   records three `deepseek-chat` calls with provider-reported usage and an
   evidence-feedback round. The other skill helpers default to deterministic
   implementations; the science seam in item 1 remains a fixture.

## Not claimed

- real science / QC / LSF execution (M4-02, M4-03);
- real browser/Chromium (M4-02; the Workbench row here is Node + real HTTP);
- remote/LSF publication retry against a real cluster (M4-03).

## Findings

`summary.json` separates the two states:

- `findings` — defects this run observed and did **not** work around (`status: open`); an empty list is a requirement, not a formality;
- `closed_findings` — findings recorded by an earlier run, re-tested here with `status: closed` and the evidence that closed them (F7: the product session observes the negotiated 8+10 surface; F8: the server-issued wait cursor round-trips through the machine envelope and resumes the wait).

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

- `summary.json` — chain, rounds, open/closed findings, limitations
- `fault-matrix.json` — one row per case with its assertion
- `performance.json` — Data idempotency + Controller call counts + p50/p95
- `stub-boundaries.json` — the declared seams
- `trace.jsonl` — the orchestrator step trace
- `mcp-round0/`, `mcp-round1/` — the MCP/bridge runner trace and result
- `workbench/` — the Workbench driver scenario, stdout and stderr
- `controller.log`, `controller.restart.log` — the real Controller logs
