# B03 entry-to-use-case audit notes

Audited: 2026-10-22. Branch `feat/frontend-plan1-closure` (B00 `31e60cb`).
Inputs read: `src/acp_services/runtime/commands.py`, `src/acp_services/contracts/injection.py`,
`src/acp_services/entrypoints/machine_output.py`, `src/acp_services/entrypoints/mcp.py`,
`src/acp_services/runtime/plan_contracts.py`, `src/acp_services/runtime/step_requirements.py`,
`src/acp/services_bootstrap.py`, `src/acp/cli.py`, `src/acp/api/v1_routes.py`, `src/acp/api/v1_schemas.py`.

Machine-readable table: `docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json`
(`entry_call_table`). Rendering: `runtime/entry-call-table.v1.md`.

## Verdict

**PARTIAL.** 17 conformant, 2 conformant-local (no task-state write), 14 gaps.
No entry has a private *Submit* implementation: every submit path reaches
`RuntimeCommands.submit`. Private *Cancel* paths: none. Private *Result* paths: yes (GAP-B03-01/02/03).

## Submit chain (verified single owner)

```
acp run <workflow> ──► cli.job_submit_command ──► services_bootstrap.job_runtime_commands(manager)
                                              └─► RuntimeCommands.submit ──► JobSubmissionPort.submit
POST /api/v1/jobs ───► v1_routes.create_job ──► _commands(request).submit(spec)   (v1_routes.py:2357)
acp task execute ────► cli._handle_task_execute ──► job_submit_command ──► RuntimeCommands.submit
mcp:acp_submit ──────► mcp.McpServer._tool_submit ──► SubmitPort.submit   ✗ not RuntimeCommands
```

`RuntimeCommands.submit` forwards exactly once (`commands.py:60-62`). `_commands()` builds the bundle
through `build_runtime_commands` (`services_bootstrap.py:283-301`), which is the only place the legacy
`JobManager` is adapted onto the ports. Boundary validation (workflow whitelist, scan contract, IRC
source resolution, execution-target conflict) happens in `create_job` **before** the single submit call,
so it does not create a second submit path.

## Control chain (verified single owner)

```
POST /api/v1/jobs/{id}/{pause,unpause,cancel,continue,rerun} ──► _commands(request).<verb>   conformant
acp task {pause,unpause,continue,cancel,rerun} ──► cli.job_control_command ──► RuntimeCommands   conformant
PATCH edit-recalculate ──► RuntimeCommands.edit_recalculate   conformant
```

`cancel` is `RuntimeCommands.cancel` → `JobControlPort.cancel`; the route returns the record unchanged
(`v1_routes.py:5103-5110`). Terminal-state semantics stay with the manager; no second state machine
was introduced.

## Gaps (recorded, not hidden)

| id | evidence | why it is a gap |
|---|---|---|
| GAP-B03-01 | `cli.py:2555` `_handle_task_query` uses `_task_store(run_root).get/list`; `v1_routes.py:4598,4611,4996,2396` use `_manager(request).get/list` | a read bypasses the single `status` command; four HTTP surfaces and one CLI verb each own their projection |
| GAP-B03-02 | `cli.py:2669` `_handle_task_export` reads `RESULT/result_manifest.json` via `ResultManifest.read` and resolves `--job` through `JobStore.get` | artifact read bypasses the `read` command and Data `resource_read` |
| GAP-B03-03 | `mcp.py:958-1140`: every tool calls an injected port (`CapabilityPort`, `SubmitPort`, `StatusPort`, ...); `_tool_submit`/`_tool_prepare_artifact` keep `self._idempotency` (a process-local dict) | MCP is not bound to the application commands, and connection-local state is the idempotency authority (forbidden by the runbook no-side-effect/`tool_call_state_authority` rule) |
| GAP-B03-04 | no entry accepts a Controller target; `src/acp_services/entrypoints/controller_client.py` does not exist | entry equivalence across CLI/HTTP/MCP cannot be demonstrated until B04a |

Owners: GAP-B03-01 → B06 (bind) + B10a (TaskRef projection); GAP-B03-02 → B06 + B12; GAP-B03-03 → B05/B06;
GAP-B03-04 → B04a. Closing gates are named in the contract (`entry_call_table.gaps[].closing_gate`).

## Conformance count

`conformant` 17 · `conformant_local` 2 (`acp task ingest`, `acp task plan` — local, no task state write) ·
`gap` 14. Counts are asserted by `tests/test_acp2_runtime_contracts.py::test_entry_call_table_has_one_command_per_entry`
only for the invariants (one command per entry, gap ids exist); the verdict string is asserted as `PARTIAL`
so no later stage can silently upgrade it without updating evidence.

## Deliberate contract additions

`control` (pause/unpause/continue/rerun/edit) and `recover` are frozen as application commands in addition to
the nine required ones. The alternative — mapping pause/rerun onto `cancel` and recovery onto `status` — was
rejected because it would mislabel distinct intents and hide them outside the single command surface.
Both already have exactly one owner today (`RuntimeCommands.control`, `RuntimeRecovery`), so the contract
describes reality rather than inventing a new path.
