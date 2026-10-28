# Candidate-Contracts — M1-06 early thin vertical slice

- **Task**: M1-06 (G02/G07/G11/G14/G20), Candidate-Contracts freeze point.
- **Status**: thin slice **PASS** for the declared scope; `evidence_kind=fixture`.
- **Candidate tree**: the `git.head` recorded in [candidate-contracts.json](candidate-contracts.json)
  (`2551c03` at generation time) plus the uncommitted files listed there; the
  workspace was **not** committed, rebased or pushed by this task.
- **Reproduce**: `set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a`
  then `PYTHONPATH=src python3.11 scripts/acceptance/acp2_thin_slice.py --out reports/acp2_remediation_20261026/candidate-contracts/`
  (Node 22.19 + `pnpm --dir rph build` are prerequisites; the script exits
  `0=PASS`, `1=FAIL`, `2=NOT_VERIFIED`, `3=usage`).

## What actually ran (real)

| Ring | Real implementation |
| --- | --- |
| Controller | `python -m uvicorn acp.api.server:app`, isolated `ACP_RUN_ROOT`, real SQLite (`acp_jobs.db` schema 25, `acp_research.db`) |
| Proposal writer | `POST /api/v2/research/proposals` → `acp_services.runtime.research_writer` over the Data `ResearchResourceStore` (B18) |
| Identity | M1-02 `ResearchIdentity.allocate` (principal/project/research/session derived server-side) |
| Approval | `ApprovalService` (B16) with the M1-04b double digest (`plan_digest` + `science_digest`), decided over HTTP |
| Submit | MCP stdio child `acp mcp serve --controller …`, RPH bridge (`@rph/bridge`) with the M1-02 `SubmitIntentLedger`, protocol `2025-06-18`, 8 tools |
| Replay | a second bridge/ledger instance derives the same intent key; the platform ledger replays the **same TaskRef** (`idempotent_replay=true`) |
| Artifact | `acp_prepare_artifact` + `acp_read_artifact` on the real operation/artifact stores (400-byte body) |
| Evidence | `acp_services.runtime.evidence_projection.project_evidence` (M1-03, the only projection) |
| Record | `POST /api/v2/research/records` (one TaskRef, digest-bound refs, decision) + `GET …/evidence` recomputes the same projection |
| Consumers | the real frontend validators (`experience_contract.js`, `research-evidence-view.js`) via `frontend/js/module_tests/evidence_contract_probe.js` |

## Declared stub boundaries (see [stub-boundaries.json](stub-boundaries.json))

1. **`cccp.dispatch`** — the task is the synthetic `fake` workflow; the
   attempt's `task_result.json`, `execution_receipt.json` and evidence report are
   written by a controlled stub that uses the real worker writers. No QC ran.
2. **`science.report`** — the report bytes are A's frozen schema fixture with the
   subject/report identity rewritten; claim sources are static fixture artifacts.
3. **`supervisor.skills`** — the Supervisor ran with injected test skills
   (deterministic local skills + adversarial model text). Its submit port called
   the **real** bridge/MCP; its approve port received the real approval state read
   over HTTP by the orchestrator (M3-04 owns the supervisor's own approval port).
4. **quota** — zero model, QC and LSF calls.

## Negatives (all typed, zero new tasks)

| Case | Expected | Observed |
| --- | --- | --- |
| stale proposal revision | `409 revision_conflict` | 409 `revision_conflict` |
| foreign research identity | `403 research_identity_mismatch` | 403 `research_identity_mismatch` |
| tampered projection digest | `409 projection_digest_mismatch` | 409 `projection_digest_mismatch` |
| `accept` on partial evidence | `400 evidence_not_complete` | 400 `evidence_not_complete` |
| model text changing the plan | `400 plan_digest_mismatch` | 400 `plan_digest_mismatch` |
| payload-named approver | `403 approver_identity_from_payload` | 403 `approver_identity_from_payload` |
| revoked approval, fresh key | `403 approval_revoked` | 403 `approval_revoked` |

Prompt-injection observation: the injected test skills returned "skip the
approval / accept the run" text; the Supervisor still stopped on
`wait_timeout` (never `accepted`), issued exactly one real submit that replayed
the same TaskRef, and the platform refused every unapproved/forged attempt. Model
text changed no platform decision.

## Not claimed

- real science / QC / LSF execution, real chemistry or energy values;
- a real model or DSH agent run (no model call at all);
- a real browser/Chromium session (module tests + contract probe only);
- the P2 research MCP tools (the `acp.research.v1` extension is advertised,
  `implemented=false`; the adapter refuses it in-band);
- M2-01b/c, M3-01b, M3-05b and every later checkpoint.

## Files

| File | Content |
| --- | --- |
| `summary.json` | step table, status, digests, MCP/supervisor facts, limitations |
| `trace.jsonl` | per-step orchestration request/response/digest/duration |
| `mcp-runner/mcp-trace.jsonl` | per-tool MCP/bridge trace from the RPH runner |
| `mcp-runner/runner.json` | runner summary (tools, protocol, TaskRef, supervisor) |
| `candidate-contracts.json` | candidate tree, contract digests, DB migrations, call trace, error paths, claims |
| `negative-cases.json` | the seven refusal cases with status/rule/side-effect counts |
| `stub-boundaries.json` | every stub boundary with real-vs-stubbed facts |
| `go-no-go-inputs.json` | decision **inputs** only (no decision): contracts, effort, quota, plan-graph remainder, observability, risks |
| `http/*.json` | the exact server payloads fed to the frontend validators |
| `controller.log` | Controller stdout/stderr for this run |
