# Entry-to-use-case call table (ACP 2.0 Plan B, B03 / H1, refreshed B06)

Frozen: 2026-10-22 (B03), refreshed 2026-10-22 (B06). Owner: B. Contract:
`runtime/application-api.v1.json` (`entry_call_table` is the machine-readable
source of truth; this file is its rendering).

Invariant: **exactly one application command per entry**, and no entry owns a
private Submit / Cancel / Result implementation. Every mutating entry funnels
through `acp_services.runtime.commands.RuntimeCommands`; every MCP tool funnels
through `acp.services_mcp_bootstrap` -> `ControllerClient` -> one Controller
application command.

Audit verdict: **PARTIAL** (MCP binding complete; HTTP GET reads re-owned to B10a and the CLI export manifest read to B12). All eight MCP tools are bound to the
Controller. `cli:acp task query` emits the shared
`acp_services.entrypoints.status_projection.project_task_status` projection from
both the local and Controller paths. The remaining HTTP GET read adapters are
explicitly re-owned to B10a; the CLI export manifest read is re-owned to B12.

## Entries

| Entry | Entrypoint | Application command | Use case | Conformance |
|---|---|---|---|---|
| `cli:acp run <workflow>` | `src/acp/cli.py:job_submit_command` | `submit` | `RuntimeCommands.submit` | conformant |
| `cli:acp task ingest` | `src/acp/cli.py:_handle_task_ingest` | `prepare` | `PrepareOperationService (purpose=structure-input)` | conformant_local |
| `cli:acp task plan` | `src/acp/cli.py:_handle_task_plan` | `preflight` | `plan build (zero side effects)` | conformant_local |
| `cli:acp task execute` | `src/acp/cli.py:_handle_task_execute` | `submit` | `RuntimeCommands.submit` | conformant |
| `cli:acp task query` | `src/acp/cli.py:_handle_task_query` | `status` | `status_projection.project_task_status` | conformant |
| `cli:acp task pause` | `src/acp/cli.py:_handle_task_control` | `control` | `RuntimeCommands.pause` | conformant |
| `cli:acp task unpause` | `src/acp/cli.py:_handle_task_control` | `control` | `RuntimeCommands.unpause` | conformant |
| `cli:acp task continue` | `src/acp/cli.py:_handle_task_control` | `control` | `RuntimeCommands.continue_job` | conformant |
| `cli:acp task cancel` | `src/acp/cli.py:_handle_task_control` | `cancel` | `RuntimeCommands.cancel` | conformant |
| `cli:acp task rerun` | `src/acp/cli.py:_handle_task_control` | `control` | `RuntimeCommands.rerun` | conformant |
| `cli:acp task recover` | `src/acp/cli.py:_handle_task_recover` | `recover` | `RuntimeRecovery.execute` | conformant |
| `cli:acp task export` | `src/acp/cli.py:_handle_task_export` | `read` | `Data manifest read` | gap (GAP-B03-02, re-owned B12) |
| `http:POST /api/v1/jobs` | `src/acp/api/v1_routes.py:create_job` | `submit` | `RuntimeCommands.submit` | conformant |
| `http:POST /api/v1/preflight` | `src/acp/api/v1_routes.py:preflight_request` | `preflight` | zero-side-effect task/method validation + `plan_digest` | conformant |
| `http:POST /api/v1/jobs/{job_id}/prepare` | `src/acp/api/v1_routes.py:prepare_artifact` | `prepare` | `PrepareOperationService.enqueue` | conformant |
| `http:GET /api/v1/operations/{operation_id}` | `src/acp/api/v1_routes.py:get_operation` | `status` | prepare-operation projection | conformant |
| `http:POST /api/v1/jobs/{job_id}/analyze` | `src/acp/api/v1_routes.py:analyze_job` | `analyze` | `AnalysisCapabilityRegistry` gate | conformant |
| `http:GET /api/v1/jobs` | `src/acp/api/v1_routes.py:list_jobs` | `status` | `status snapshot query` | gap (GAP-B03-01, re-owned B10a) |
| `http:GET /api/v1/jobs/{job_id}` | `src/acp/api/v1_routes.py:get_job` | `status` | `status snapshot` | gap (GAP-B03-01, re-owned B10a) |
| `http:GET /api/v1/jobs/{job_id}/summary` | `src/acp/api/v1_routes.py:get_job_summary` | `status` | `status snapshot` | gap (GAP-B03-01, re-owned B10a) |
| `http:GET /api/v1/jobs/{job_id}/detail` | `src/acp/api/v1_routes.py:get_job_detail` | `status` | `status snapshot + disk recovery matrix` | gap (GAP-B03-01, re-owned B10a) |
| `http:POST /api/v1/jobs/{job_id}/cancel` | `src/acp/api/v1_routes.py:cancel_job` | `cancel` | `RuntimeCommands.cancel` | conformant |
| `http:POST /api/v1/jobs/{job_id}/pause` | `src/acp/api/v1_routes.py:pause_job` | `control` | `RuntimeCommands.pause` | conformant |
| `http:POST /api/v1/jobs/{job_id}/unpause` | `src/acp/api/v1_routes.py:unpause_job` | `control` | `RuntimeCommands.unpause` | conformant |
| `http:POST /api/v1/jobs/{job_id}/continue` | `src/acp/api/v1_routes.py:continue_job` | `control` | `RuntimeCommands.continue_job` | conformant |
| `http:POST /api/v1/jobs/{job_id}/rerun` | `src/acp/api/v1_routes.py:rerun_job` | `control` | `RuntimeCommands.rerun` | conformant |
| `http:PATCH /api/v1/jobs/{job_id} (edit-recalculate)` | `src/acp/api/v1_routes.py` (edit endpoints) | `control` | `RuntimeCommands.edit_recalculate` | conformant |
| `http:GET /api/v1/jobs/{job_id}/availability` | `src/acp/api/v1_routes.py:get_job_availability` | `prepare` | `Data availability read` | conformant |
| `http:GET /api/v1/jobs/{job_id}/prepared/{resource}` | `src/acp/api/v1_routes.py:get_prepared_resource` | `read` | `Data resource_read` | conformant |
| `mcp:acp_capabilities` | `src/acp/services_mcp_bootstrap.py:_ControllerCapabilities` | `capabilities` | `GET /api/capabilities` | conformant |
| `mcp:acp_preflight` | `src/acp/services_mcp_bootstrap.py:_ControllerPreflight` | `preflight` | `POST /api/v1/preflight` | conformant |
| `mcp:acp_submit` | `src/acp/services_mcp_bootstrap.py:_ControllerSubmit` | `submit` | `POST /api/v1/jobs` (durable `task_ref`) | conformant |
| `mcp:acp_status` | `src/acp/services_mcp_bootstrap.py:_ControllerStatus` | `status` | `GET /api/v1/jobs/{id}` / `/operations/{id}` | conformant |
| `mcp:acp_cancel` | `src/acp/services_mcp_bootstrap.py:_ControllerCancel` | `cancel` | `POST /api/v1/jobs/{id}/cancel` | conformant |
| `mcp:acp_prepare_artifact` | `src/acp/services_mcp_bootstrap.py:_ControllerArtifacts.prepare` | `prepare` | `POST /api/v1/jobs/{id}/prepare` | conformant |
| `mcp:acp_read_artifact` | `src/acp/services_mcp_bootstrap.py:_ControllerArtifacts.read` | `read` | `GET /api/v1/jobs/{id}/prepared/{r}` | conformant |
| `mcp:acp_analyze` | `src/acp/services_mcp_bootstrap.py:_ControllerAnalysis` | `analyze` | `POST /api/v1/jobs/{id}/analyze` | conformant |

## Gaps

### GAP-B03-01 — private_result_path

- status: closed for the machine query projection; HTTP GET adapters re-owned to B10a
- entries: `cli:acp task query` (closed), `http:GET /api/v1/jobs*` (re-owned)
- owner: B06 (query projection) with B10a (HTTP TaskRef projection)
- closing gate: local and Controller query emit the same `project_task_status` projection; three-entry reads the same TaskRef facts

### GAP-B03-02 — private_artifact_path

- status: reowned to B12
- entries: `cli:acp task export`
- owner: B12
- closing gate: artifact scenario reads only through read/prepare; B06 ships only the route/port shells

### GAP-B03-03 — port_not_command

- status: closed except durable replay
- entries: all eight `mcp:*` entries
- owner: B06 (binding), B08 (durable idempotency)
- closing gate: `mcp-stdio` scenario proves every tool crosses `ControllerClient` into one application command; cross-process submit replay remains B08

### GAP-B03-04 — missing_controller_target

- status: closed
- entries: all entries
- owner: B04a
- closing gate: `entry-readonly` + `mcp-stdio` run against one Controller and the task count is unchanged

## What is explicitly NOT claimed

- The HTTP GET read adapters still read the authoritative store; they are
  read-only adapters explicitly re-owned to B10a, not a second writer.
- The in-memory `IdempotencyRegistry` that once lived in the MCP adapter was
  **removed in M0-05**: the production path is the persistent Controller ledger
  (B08/B09), the raw client key is forwarded verbatim, and the MCP server keeps
  no operation-result state. The remaining `WorkspaceBudget` cap is admission
  accounting, never cross-process idempotency authority.
- B06 ships the prepare/analyze route shells. The real artifact lifecycle and
  the negative matrix belong to B12.
