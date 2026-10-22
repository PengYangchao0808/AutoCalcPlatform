# C · Real MCP stdio handshake, negotiation matrix and tool schemas (T-11 / C07)

- **Owner:** C (experience / RPH)
- **Consumer:** T-12 (DSH plugin wiring), T-13 (model tool call), T-14 (H2 receipt)
- **Scenario:** `mcp-live` (`scripts/acceptance/acp2_experience_scenario_mcp_live.py`)
- **Primary evidence run:** `$ACP2_EVIDENCE/C/mcp-live/mcp-live-20261022T180038Z-540504/main/`
- **Receipt result at capture time:** `NOT_VERIFIED`, reason `controller_unavailable`, exit `2`
- **H2 status:** **NOT closed by this row.** T-14 owns the H2 receipt.

> Rule applied: a missing environment (B's isolated Controller) is
> `NOT_VERIFIED`; an executed failure is `FAIL`. This capture never converts a
> missing Controller into a PASS and never fabricates a live handshake.

## 1. Frozen launch descriptors (consumed read-only)

| Descriptor | Path (under `$ACP2_EVIDENCE`) | SHA-256 | Contract | Contract digest |
| --- | --- | --- | --- | --- |
| B05 | `B/B05/mcp-launch.json` | `ca807cc4b8014af265fac64c9b84ca3c4050df9bfbab3b5dc408ce068b548046` | `1.0.0` | `1944f4f06c65b13b6ef98c752aa18d8e5752139b1513ecb2c19c44bb554fc488` |
| B06 | `B/B06/mcp-launch.json` | `867d25a6e5757ded401006e2c51f3512fcce551299a3221e7b892e3b82c9a7f6` | `1.1.0` | `67073115cb0856db22d84e22215d54e4dbe2437aad964efc15d3b706641e11a4` |

Both descriptors are byte-identical except for the server contract version,
the extension block and the tool `inputSchema` revision:

```json
{
  "command": "/ACPServer/acp2-development/venvs/b/bin/python",
  "args": ["-m", "acp", "mcp", "serve", "--transport", "stdio",
           "--controller", "http://127.0.0.1:18765", "--log-level", "INFO"],
  "cwd": "/ACPServer/AutoCalcPlatform/src",
  "env": {
    "ACP_RUN_ROOT": "/ACPServer/acp2-development/data/b/run-root",
    "ACP_CONTROLLER_URL": "http://127.0.0.1:18765",
    "ACP2_MCP_LOG_LEVEL": "INFO",
    "ACP2_TOOLS": "/ACPServer/acp2-development/venvs",
    "ACP_CONFIG": "/ACPServer/acp2-development/data/b/controller-config/cccp.yaml",
    "PATH": "/ACPServer/acp2-development/venvs/b/bin:/usr/sbin:/usr/local/bin:/usr/bin:/bin"
  },
  "protocol_version": "2025-06-18",
  "transport": "stdio",
  "controller": {
    "transport": "loopback-http", "loopback_only": true,
    "start_controller": false, "shared_db": false
  }
}
```

Static checks executed by the scenario (all **PASS**, run
`launch-identity.json` / assertions `launch.*`):

- `launch_version == 1`, `transport == "stdio"`, `protocol_version == "2025-06-18"`;
- contract versions `1.0.0` (B05) / `1.1.0` (B06) and 64-hex contract digests;
- absolute executable exists (`…/venvs/b/bin/python`, Python 3.11.13) and
  `cwd` exists (`/ACPServer/AutoCalcPlatform/src`);
- `args` head is `-m acp mcp serve --transport stdio --controller <url>` and the
  tail is `--log-level INFO`; B05 and B06 agree on `command/args/cwd/env` keys;
- `ACP_RUN_ROOT` is an existing directory and `ACP_CONFIG` an existing file;
- `controller.start_controller == false`, `loopback_only == true`,
  `shared_db == false` — the adapter will not start a second Controller;
- `stdio.stdout_is_channel_only == true`, `logs_to == "stderr"`;
- the allowlist is exactly the eight P1 names
  (`acp_analyze`, `acp_cancel`, `acp_capabilities`, `acp_preflight`,
  `acp_prepare_artifact`, `acp_read_artifact`, `acp_status`, `acp_submit`) and
  every `inputSchema` is an object schema.

B06 additionally negotiates the research extension:
`initialize.extensions["acp.research.v1"] = {id, version: "1.1.0", negotiated: true}`.

## 2. Controller status (B-owned)

Pre-flight `GET http://127.0.0.1:18765/api/health`:

| Observation | Value |
| --- | --- |
| Reachability | **unreachable** — `URLError: [Errno 111] Connection refused` |
| B05 PID file (81900) | process absent |
| B06 PID file (255506) | process absent |
| B07 topology PID (365514) | process absent |
| B08 PID (448951) | process absent |
| Task count before / after | **NOT_VERIFIED** (`controller_unavailable`) |
| Verified Controller PID | **NOT_VERIFIED** (`controller_unavailable`) |

C did **not** start a Controller. Evidence: `controller.json`,
`mcp-live-summary.json#controller`.

## 3. Stdlib probe (B05 descriptor)

The sanctioned probe (`docs/reference/next-change/examples/acp_mcp_stdio_probe.py`)
was executed unchanged against `B/B05/mcp-launch.json`:

| Field | Value |
| --- | --- |
| Probe exit | `1` (probe raised) |
| Adapter exit | `3` (`MCP_EXIT_CONTROLLER_UNAVAILABLE`) |
| Protocol frame sent | `initialize` with `protocolVersion: 2025-06-18`, `clientInfo: acp2-readonly-probe/1` |
| Frames received | **none** — stdout closed before the reply |
| Adapter stderr | `Controller unavailable at http://127.0.0.1:18765: Controller unreachable … [Errno 111] Connection refused` |
| Row verdict | `NOT_VERIFIED(controller_unavailable)` — not FAIL (missing prerequisite) |

Evidence: `stdlib-probe/wire.jsonl` (sha256 `f9dd0cc9…1dfe76`),
`stdlib-probe/stderr.log` (sha256 `d1846836…35d7`),
`stdlib-probe/process.json` (`{"pid": 540513, "exit_code": 3}`).

This is the real startup path: `acp mcp serve` binds its backends through
`build_mcp_runtime(controller)`, which calls `GET /api/health` **before** serving
stdin. With the Controller absent the adapter exits `3` and no MCP session can
exist. It is therefore impossible to exercise `initialize`/`tools/list` in this
window without B.

## 4. Frozen official client handshake (B06 descriptor)

`scripts/acceptance/acp2_mcp_live_client.mjs` composes the DSH harness pieces
without a second transport:

- `@deepseek-ai/dsh-mcp-client/src/transport.ts::createTransport` (the plugin's
  stdio factory, including its credential-scrubbed parent env);
- `@modelcontextprotocol/client` `2.2.0` `Client` with the plugin's own options
  (`{capabilities: {}, versionNegotiation: {mode: "auto"}}`).

A transparent recorder on the official transport's `send`/`onmessage` seam
archives the wire; it never answers, rewrites, retries or synthesises a frame.

| Field | Value |
| --- | --- |
| `@deepseek-ai/dsh-mcp-client` | `0.2.1-rc.2` |
| `@modelcontextprotocol/client` | `2.2.0` |
| SDK `LATEST_PROTOCOL_VERSION` | `2025-11-25` |
| SDK `SUPPORTED_PROTOCOL_VERSIONS` | `2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05, 2024-10-07` |
| Actual `initialize` request | `protocolVersion: "2025-11-25"` (no `protocolVersions` list emitted) |
| Negotiated protocol | **NOT_VERIFIED** (`controller_unavailable`) |
| Tool snapshot from live `tools/list` | **NOT_VERIFIED** (`controller_unavailable`) |
| Client error | `SdkError: Connection closed` (adapter exited before replying) |

Evidence: `dsh-client/dsh-client.json` (sha256 `65ca2f7b…e5438`),
`dsh-client/wire.jsonl` (sha256 `fe4493b1…6192`).

### 4.1 Compatibility risk (static, not yet executed)

The frozen client offers **`2025-11-25`** first and does not send the server's
declarative `protocolVersions` fallback list. The server's advertised supported
set is `LOCKED_PROTOCOL_VERSIONS = ("2025-06-18",)`; its negotiation function
only downgrades when the client supplies a common version in
`protocolVersions`. On the current code path the expected live outcome is
`initialize` → JSON-RPC error `data.code = "unsupported_protocol_version"`, i.e.
**no common version**, unless B adds a supported version, accepts the declared
list, or the deployment pins the client to `2025-06-18`.

This paragraph is a **static expectation**, not an observed live result. It is
recorded so T-12/T-13 do not discover it late; it does not claim incompatibility
for any case where both sides do support a common version.

## 5. Eight-tool schema snapshot

Live `tools/list` could not be captured (Controller absent). The frozen
descriptor snapshot is archived as `tool-schemas.json` (sha256
`6032c1d1…22c84e1`, `source: frozen_launch_descriptor`, `live_verified: false`).

| Tool | Required | Properties | `additionalProperties` |
| --- | --- | --- | --- |
| `acp_capabilities` | — | — | `false` |
| `acp_preflight` | `workflow`, `input` | `workflow`, `input`, `method`, `project_id`, `resources` | `false` |
| `acp_submit` | `workflow`, `input`, `plan_digest` | `workflow`, `input`, `method`, `project_id`, `resources`, `plan_digest`, `proposal_digest`, `idempotency_key` | `false` |
| `acp_status` | `reference` | `reference` (`string | {kind,id}`) | `false` |
| `acp_cancel` | `reference` | `reference`, `expected_revision` | `false` |
| `acp_prepare_artifact` | `purpose` | `purpose`, `task_ref`, `job_id`, `artifact_id`, `attempt`, `generation`, `idempotency_key` | `false` |
| `acp_read_artifact` | `artifact_ref` | `artifact_ref`, `max_bytes`, `encoding` | `false` |
| `acp_analyze` | `analysis_id`, `input_refs`, `explicit` | `analysis_id`, `input_refs`, `explicit`, `parameters`, `plan_digest`, `idempotency_key` | `false` |

**Output schemas:** the server advertises no `outputSchema` for any tool (all
eight live entries in B05's archived `tools/list` omit it). A client must treat
the tool result envelope (`content` + `structuredContent` + `isError`) as the
contract, not a declared output schema.

### 5.1 Finding F1 — descriptor vs live `tools/list` drift (evidence-backed)

B05's own archived probe (`B/B05/probe/wire.jsonl`, sha256
`f4d898901f9b7ce243c85553dfd1acc81e3e835f70c9d3b63d357d5389dbe1f5`) contains a
real `tools/list` from the same launch descriptor. Comparison
(`schema-drift.json`, sha256 `11cfc845…5e1d58`) shows **all eight** advertised
`inputSchema` values differ from the frozen descriptor:

- live schemas use `additionalProperties: true`; the descriptor uses `false`;
- `acp_preflight`/`acp_submit`/`acp_analyze` require only `workflow` live, while
  the descriptor requires `input` and (for submit/analyze) `plan_digest` /
  `analysis_id`+`input_refs`+`explicit`;
- `acp_prepare_artifact`/`acp_read_artifact` live properties are
  `job_id/resource_id/relative_path/attempt/idempotency_key`, while the
  descriptor defines `purpose/task_ref/...` and `artifact_ref/max_bytes/encoding`;
- `acp_capabilities` differs only in schema annotations.

The same weaker schemas are present in the server source that the launch `cwd`
executes (`src/acp_services/entrypoints/mcp.py::_tool_specs`, both the main
checkout and B's `58d81ec` worktree). This is **B's contract-vs-implementation
gap**, not a C fix: T-11 does not edit the server. A client (DSH) registers the
live `tools/list` schemas, so the model-facing parameter surface is the weaker
one until B reconciles the two. `NOT_VERIFIED` for the live row, recorded here
as a blocking input to T-12/T-14 and to `requests/B-req-18.md`.

## 6. Negotiation matrix (five rows)

Every row is Controller-dependent (the adapter refuses to start without a
healthy Controller), so each is `NOT_VERIFIED(controller_unavailable)` in this
capture. No row is PASS by assumption.

| Row | Required behaviour | Verdict | Reason |
| --- | --- | --- | --- |
| (a) `same` | request `2025-06-18` → same version | `NOT_VERIFIED` | `controller_unavailable` |
| (b) `different_supported` | prefer `2025-11-25`, also declare `2025-06-18` → negotiate `2025-06-18` | `NOT_VERIFIED` | `controller_unavailable` |
| (c) `no_common` | only `1999-01-01` → explicit `unsupported_protocol_version`, no tool call | `NOT_VERIFIED` | `controller_unavailable` |
| (d) `tools_before_init` | `tools/list` before `initialize` → `not_initialized` | `NOT_VERIFIED` | `controller_unavailable` |
| (e) `notification_eof` | initialized notification + stdin EOF → clean exit, no fabricated frame, no cancel | `NOT_VERIFIED` | `controller_unavailable` |

Execution for the next run is implemented in
`scripts/acceptance/acp2_mcp_negotiation_probe.py`: one fresh adapter process
per row, newline JSON-RPC framing identical to the sanctioned stdlib probe,
zero tool calls, per-row wire/stderr/exit-code archives. It is a diagnostic
harness, not a product transport; the official client remains the primary
handshake in §4.

## 7. No-side-effect check

| Check | Verdict | Evidence |
| --- | --- | --- |
| Controller PID alive after adapter exit | `NOT_VERIFIED` | Controller absent before the run |
| Task count before == after | `NOT_VERIFIED` | `controller.json` has no task count |
| No serving adapter process left | **PASS** | `ps` scan found 0 `acp mcp serve --transport stdio --controller http://127.0.0.1:18765`; probe child PID 540513 is gone |
| Tasks submitted by C | **PASS (structural)** | the scenario never calls `tools/call`; `cleanup.json#tasks_submitted = 0` |
| C started a Controller | **PASS (negative)** | `cleanup.json#controller_started_by_c = false` |
| Production `:8765` touched | **PASS (negative)** | `cleanup.json#production_8765_touched = false` |

`cleanup.json` and assertion `no_side_effect.no_serving_process_left` record the
real observation; the two Controller-dependent rows stay `NOT_VERIFIED`.

## 8. NOT_VERIFIED register (exact)

From the receipt extension `experience.mcp_live.not_verified_rows`:

```
controller.reachable
controller.pid
controller.task_count_before
schema.live_snapshot
probe.stdlib_exit
probe.stdlib_protocol
official.client_handshake
official.tool_allowlist
official.tool_schemas
official.capabilities_read
matrix.same
matrix.different_supported
matrix.no_common
matrix.tools_before_init
matrix.notification_eof
no_side_effect.task_count_unchanged
no_side_effect.controller_alive_after
```

## 9. What unblocks this row

1. B keeps one isolated Controller running at `http://127.0.0.1:18765` (never
   production `:8765`) with a verified PID, start/stop command and config
   (`requests/B-req-18.md`).
2. B reconciles the launch descriptor `tools[].inputSchema` with the live
   `tools/list` (Finding F1), or states which one is authoritative for clients.
3. B decides the `2025-11-25` vs `2025-06-18` negotiation path (§4.1) without
   silently downgrading an unsupported request.

Then T-11 is a single `c_accept mcp-live 300` re-run: the same body exercises
the stdlib probe, the official-client schema snapshot, all five matrix rows and
the task-count invariant. Until then the row remains `NOT_VERIFIED` and this
document must not be cited as an H2 PASS.
