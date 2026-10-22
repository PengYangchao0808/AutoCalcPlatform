# Entry-to-use-case call table (ACP 2.0 Plan B, B03 / H1)

Frozen: 2026-10-22. Owner: B. Contract: `runtime/application-api.v1.json` (`entry_call_table` is the machine-readable source of truth; this file is its rendering).

Invariant: **exactly one application command per entry**, and no entry owns a private Submit / Cancel / Result implementation. Every mutating entry must funnel through `acp_services.runtime.commands.RuntimeCommands`; every read must funnel through the single status/read command.

Audit verdict: **PARTIAL**. 17 conformant, 2 conformant-local (no task-state write), 14 gaps.

## Entries

| Entry | Entrypoint | Application command | Use case | Conformance |
|---|---|---|---|---|
| `cli:acp run <workflow>` | `src/acp/cli.py:job_submit_command` | `submit` | `RuntimeCommands.submit` | conformant |
| `cli:acp task ingest` | `src/acp/cli.py:2447` | `prepare` | `PrepareOperationService (purpose=structure-input)` | conformant_local |
| `cli:acp task plan` | `src/acp/cli.py:2492` | `preflight` | `plan build (zero side effects)` | conformant_local |
| `cli:acp task execute` | `src/acp/cli.py:2517` | `submit` | `RuntimeCommands.submit` | conformant |
| `cli:acp task query` | `src/acp/cli.py:2555` | `status` | `status snapshot` | gap (GAP-B03-01) |
| `cli:acp task pause` | `src/acp/cli.py:2587` | `control` | `RuntimeCommands.pause` | conformant |
| `cli:acp task unpause` | `src/acp/cli.py:2587` | `control` | `RuntimeCommands.unpause` | conformant |
| `cli:acp task continue` | `src/acp/cli.py:2587` | `control` | `RuntimeCommands.continue_job` | conformant |
| `cli:acp task cancel` | `src/acp/cli.py:2587` | `cancel` | `RuntimeCommands.cancel` | conformant |
| `cli:acp task rerun` | `src/acp/cli.py:2587` | `control` | `RuntimeCommands.rerun` | conformant |
| `cli:acp task recover` | `src/acp/cli.py:2618` | `recover` | `RuntimeRecovery.execute` | conformant |
| `cli:acp task export` | `src/acp/cli.py:2669` | `read` | `Data manifest read` | gap (GAP-B03-02) |
| `http:POST /api/v1/jobs` | `src/acp/api/v1_routes.py:2288` | `submit` | `RuntimeCommands.submit` | conformant |
| `http:GET /api/v1/jobs` | `src/acp/api/v1_routes.py:2396` | `status` | `status snapshot query` | gap (GAP-B03-01) |
| `http:GET /api/v1/jobs/{job_id}` | `src/acp/api/v1_routes.py:4598` | `status` | `status snapshot` | gap (GAP-B03-01) |
| `http:GET /api/v1/jobs/{job_id}/summary` | `src/acp/api/v1_routes.py:4611` | `status` | `status snapshot` | gap (GAP-B03-01) |
| `http:GET /api/v1/jobs/{job_id}/detail` | `src/acp/api/v1_routes.py:4996` | `status` | `status snapshot + disk recovery matrix` | gap (GAP-B03-01) |
| `http:POST /api/v1/jobs/{job_id}/cancel` | `src/acp/api/v1_routes.py:5103` | `cancel` | `RuntimeCommands.cancel` | conformant |
| `http:POST /api/v1/jobs/{job_id}/pause` | `src/acp/api/v1_routes.py:5169` | `control` | `RuntimeCommands.pause` | conformant |
| `http:POST /api/v1/jobs/{job_id}/unpause` | `src/acp/api/v1_routes.py:5175` | `control` | `RuntimeCommands.unpause` | conformant |
| `http:POST /api/v1/jobs/{job_id}/continue` | `src/acp/api/v1_routes.py:5181` | `control` | `RuntimeCommands.continue_job` | conformant |
| `http:POST /api/v1/jobs/{job_id}/rerun` | `src/acp/api/v1_routes.py:5205` | `control` | `RuntimeCommands.rerun` | conformant |
| `http:PATCH /api/v1/jobs/{job_id} (edit-recalculate)` | `src/acp/api/v1_routes.py (edit endpoints)` | `control` | `RuntimeCommands.edit_recalculate` | conformant |
| `http:GET /api/v1/jobs/{job_id}/availability` | `src/acp/api/v1_routes.py:7573` | `prepare` | `Data availability read` | conformant |
| `http:GET /api/v1/jobs/{job_id}/prepared/{resource}` | `src/acp/api/v1_routes.py:7646` | `read` | `Data resource_read` | conformant |
| `mcp:acp_capabilities` | `src/acp_services/entrypoints/mcp.py:958` | `capabilities` | `CapabilityPort.capabilities` | gap (GAP-B03-03) |
| `mcp:acp_preflight` | `src/acp_services/entrypoints/mcp.py:971` | `preflight` | `PreflightPort.preflight` | gap (GAP-B03-03) |
| `mcp:acp_submit` | `src/acp_services/entrypoints/mcp.py:983` | `submit` | `SubmitPort.submit` | gap (GAP-B03-03) |
| `mcp:acp_status` | `src/acp_services/entrypoints/mcp.py:1022` | `status` | `StatusPort.get` | gap (GAP-B03-03) |
| `mcp:acp_cancel` | `src/acp_services/entrypoints/mcp.py:1044` | `cancel` | `CancelPort.cancel` | gap (GAP-B03-03) |
| `mcp:acp_prepare_artifact` | `src/acp_services/entrypoints/mcp.py:1063` | `prepare` | `ArtifactPort.prepare` | gap (GAP-B03-03) |
| `mcp:acp_read_artifact` | `src/acp_services/entrypoints/mcp.py:1108` | `read` | `ArtifactPort.read` | gap (GAP-B03-03) |
| `mcp:acp_analyze` | `src/acp_services/entrypoints/mcp.py:1121` | `analyze` | `AnalyzePort.analyze` | gap (GAP-B03-03) |

## Gaps (not a PASS)

### GAP-B03-01 — private_result_path

Reads resolve JobManager/JobStore directly instead of one status command.

- entries: `cli:acp task query`, `http:GET /api/v1/jobs`, `http:GET /api/v1/jobs/{job_id}`, `http:GET /api/v1/jobs/{job_id}/summary`, `http:GET /api/v1/jobs/{job_id}/detail`
- owner: B06 (bind) with B10a (TaskRef projection)
- closing gate: entry-readonly + three-entry scenarios read the same TaskRef facts

### GAP-B03-02 — private_artifact_path

Reads RESULT/result_manifest.json directly and resolves --job through JobStore.

- entries: `cli:acp task export`
- owner: B06 with B12
- closing gate: artifact scenario reads only through read/prepare

### GAP-B03-03 — port_not_command

MCP tools call injected port adapters (and hold an in-connection idempotency dict) instead of the application command.

- entries: `mcp:acp_capabilities`, `mcp:acp_preflight`, `mcp:acp_submit`, `mcp:acp_status`, `mcp:acp_cancel`, `mcp:acp_prepare_artifact`, `mcp:acp_read_artifact`, `mcp:acp_analyze`
- owner: B05/B06
- closing gate: mcp-stdio scenario: submit replay after reconnect yields the same task_ref

### GAP-B03-04 — missing_controller_target

No entry accepts a Controller target yet; ControllerClient does not exist.

- entries: `all entries`
- owner: B04a
- closing gate: entry-readonly launches two adapters against one Controller and the task count is unchanged

## Notes per entry

- `cli:acp run <workflow>`: all run_* handlers funnel through job_submit_command -> build_runtime_commands().submit
- `cli:acp task ingest`: parse and write input.xyz only; creates no task, no attempt
- `cli:acp task plan`: currently returns steps without plan_digest; B06 wires preflight to return it
- `cli:acp task query`: reads JobStore.get/list directly (private Result read path); owner B06/B10a
- `cli:acp task recover`: single planning/recovery owner, not a second executor
- `cli:acp task export`: reads RESULT/result_manifest.json via ResultManifest.read and resolves --job via JobStore.get; must go through the read command
- `http:POST /api/v1/jobs`: boundary validation happens before the single submit call
- `http:GET /api/v1/jobs`: lists via manager/store (private Result read path)
- `http:GET /api/v1/jobs/{job_id}/availability`: read-only availability snapshot; never prepares
- `http:GET /api/v1/jobs/{job_id}/prepared/{resource}`: no implicit fetch on miss
- `mcp:acp_capabilities`: calls an injected port adapter directly; B06 binds it to the application command
- `mcp:acp_submit`: in-connection _idempotency dict is connection state authority; B05/B06 must remove it

## What is explicitly NOT claimed

- The gaps above are implementation gaps owned by B04a/B05/B06/B10a/B12. This table freezes the target mapping; it does not assert the mapping is already implemented.
- No panel, panel-side submit, or MCP connection state may become a second authority. The MCP `_idempotency` dict is recorded as GAP-B03-03.
- Entry equivalence is proven at B04a by comparing `health.candidate_revision`, `health.contract_version` and `health.contract_digest` across entries.
