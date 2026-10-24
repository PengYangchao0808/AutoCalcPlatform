# Tool Semantics — ACP MCP surface as consumed by RPH / DSH

Status: **frozen by T-02 / C01a (2026-10-22)** for the C track
(`.omo/plans/acp2-plan-c-rph-workbench.md`). This document is a *consumer*
contract: it records how the real server behaves today, what the bridge must
translate, and what the C track is not allowed to invent.

Ground truth is the shipped adapter:
`src/acp_services/entrypoints/mcp.py` (read at the frozen C baseline
`c54823109010b651de3f0cca28da5af54a10fdfb`). Line references below are to that
revision. The machine envelope is
`src/acp_services/entrypoints/machine_output.py`.

## 1. Transport and protocol (locked)

| Property | Value | Reference |
| --- | --- | --- |
| Transport | `stdio` only; the **caller** owns stdin/stdout framing | `mcp.py:124`, `McpServer.handle_line` `mcp.py:838` |
| Protocol version | `2025-06-18` (exactly one accepted version) | `mcp.py:120-122`, `_initialize` `mcp.py:871-885` |
| Server identity | `acp-services-mcp` `0.1.0` | `mcp.py:132-133`, `mcp.py:886` |
| Permission scope | local process scope; no API key/bearer token accepted or forwarded to a tool | module docstring `mcp.py:29-36` |
| Discovery | `tools/list` advertises the closed allowlist; `tools/call` dispatches through a literal table | `mcp.py:896-931` |

A client that requests any other protocol version is refused with JSON-RPC
`-32602` and `data.code = "unsupported_protocol_version"`; it is never silently
downgraded (`mcp.py:872-883`).

`initialize`, `tools/list`, `tools/call` and `ping` are the only methods;
anything else is JSON-RPC `-32601` (`mcp.py:855-866`).

## 2. Raw tool names ↔ DSH model-visible names

`tools/list` and the server dispatch table use the **raw** names verbatim
(`McpTool`, `mcp.py:186-214`). A DSH/agent runtime exposes MCP tools to the
model with the `mcp__acp__` prefix. The bridge applies exactly one prefix and
the server never sees it.

| # | Raw server name (JSON-RPC `tools/call.name`) | DSH model-visible name | Family | Required access | Long task |
| --- | --- | --- | --- | --- | --- |
| 1 | `acp_capabilities` | `mcp__acp__acp_capabilities` | capability discovery | `read` | no |
| 2 | `acp_preflight` | `mcp__acp__acp_preflight` | preflight/estimate | `read` | no |
| 3 | `acp_submit` | `mcp__acp__acp_submit` | controlled submit | `write` | **yes** |
| 4 | `acp_status` | `mcp__acp__acp_status` | status/cancel | `read` | no |
| 5 | `acp_cancel` | `mcp__acp__acp_cancel` | status/cancel | `control` | no |
| 6 | `acp_prepare_artifact` | `mcp__acp__acp_prepare_artifact` | artifact prepare/read | `write` | **yes** |
| 7 | `acp_read_artifact` | `mcp__acp__acp_read_artifact` | artifact prepare/read | `read` | no |
| 8 | `acp_analyze` | `mcp__acp__acp_analyze` | explicit analysis | `write` | **yes** |

Rules:

- The model-visible name is `"mcp__acp__" + raw_name` **verbatim**. Do not
  strip `acp_`, do not shorten, do not case-fold.
- **Never send an `mcp__acp__`-prefixed name back to the server.** The switch
  is one-way: strip exactly one `mcp__acp__` prefix before `tools/call`.
  The server accepts only the eight raw names; anything else is refused at the
  protocol layer with `-32602`, `data.code = "unknown_tool"` and the sorted
  `allowlist` (`mcp.py:899-914`).
- Dispatch is a literal handler table. There is no `getattr`/`eval`/`exec`
  resolution and no arbitrary shell tool (`mcp.py:780-788`).

## 3. Result shapes

Every `tools/call` **result** (not a transport error) has exactly this shape
(`_result_payload`, `mcp.py:937-943`):

```json
{
  "content": [{"type": "text", "text": "<MachineResult.to_json()>"}],
  "structuredContent": {
    "schema_version": 1,
    "operation": "acp_submit",
    "status": "ok",
    "exit_code": 0,
    "dry_run": false,
    "operation_identity": "<workspace/scoped identity or null>",
    "diagnostic_ref": "<trace handle or null>",
    "data": {"...": "operation-specific"},
    "error": null
  },
  "isError": false
}
```

`structuredContent` is the single machine-readable payload; `content[0].text`
is the same envelope serialized as text for text-only clients. Field names come
from `MachineResult.to_dict()` (`machine_output.py:533-545`).

### 3.1 `isError`

- `isError` is `false` for `status == "ok"`.
- `isError` is `true` for `status == "error"` **and the call still returns a
  result**, not a JSON-RPC error. This is the path for identity denial, budget
  refusal, unknown reference and explicit-analysis misuse: `McpToolError` is
  converted into a `MachineResult(status="error", error=...)` in
  `_tools_call` (`mcp.py:921-935`) and then `_result_payload` sets
  `isError = (status == "error")` (`mcp.py:942`).
- JSON-RPC `error` is reserved for protocol failures: parse error, invalid
  request/params, unknown method, unknown/non-allowlisted tool, unsupported
  protocol version (`mcp.py:790-836`, `mcp.py:899-918`). A consumer must branch
  on `isError` first, then on the JSON-RPC `error` object.
- `error` inside `structuredContent` is a stable `ErrorObject`
  (`code`, `message`, `params`, `retryable`, `diagnostic_ref`) — never prose
  parsing (`machine_output.py:37-56`).

### 3.2 Per-tool `data`

| Tool | `data` on success | Notes |
| --- | --- | --- |
| `acp_capabilities` | backend capability descriptor plus `protocol_version` and sorted `tools` | `mcp.py:954-965` |
| `acp_preflight` | `{executed:false, metering_consumed:false, transfers_attempted:false, core_hours_reserved:0, plan, estimate}`, `dry_run:true` | zero side effects (`mcp.py:967-980`, `machine_output.py:559-598`) |
| `acp_submit` | `{reference:{kind:"job",id:...}, idempotency_key, query_tool:"acp_status", cancel_tool:"acp_cancel"}` | idempotent replay returns `{reference, idempotent_replay:true}`; `isError:false` (`mcp.py:983-1020`) |
| `acp_status` | the backend projection plus `reference` | unknown reference → `isError:true`, `code = resource_not_ready`, `retryable:true` (`mcp.py:1026-1061`) |
| `acp_cancel` | projection plus `reference` and `cancel_requested:true` | releases the long-task budget slot (`mcp.py:1063-1092`) |
| `acp_prepare_artifact` | `{reference:{kind:"operation",id:...}, idempotency_key, query_tool:"acp_status"}` | idempotent, budget-admitted (`mcp.py:1067-1105`) |
| `acp_read_artifact` | the prepared artifact projection | bounded, **no fetch on miss**, no budget admission (`mcp.py:1107-1117`) |
| `acp_analyze` | the analysis result projection | requires `explicit:true`; otherwise `isError:true`, `code = input_invalid` (`mcp.py:1119-1153`) |

## 4. Async / long-task references

`acp_submit`, `acp_prepare_artifact` and `acp_analyze` are long tasks
(`_LONG_TASK_TOOLS`, `mcp.py:246`). They return an **operation/job reference
immediately** and never block on the science:

- The reference is normalized by `_reference_from` to a stable
  `{kind: "job"|"operation", id: "<string>"}` (`mcp.py:1163-1188`).
- The adapter holds **no per-connection task state**. A dropped connection does
  not decide a job's fate; a later connection queries the same reference with
  `acp_status` (`McpServer` docstring `mcp.py:751-762`, `mcp.py:1026-1040`).
- A status observation of a terminal state releases the long-task budget slot
  (`mcp.py:1049-1052`; terminal spellings `mcp.py:250-252`).
- The model must never poll by re-submitting. Retry uses the same
  `idempotency_key`; the server collapses it onto one operation
  (`mcp.py:983-1005`, `IdempotencyRegistry` `mcp.py:700-747`).
- The bridge must surface `reference` + `query_tool` to the model, not a
  synthetic "in progress" boolean.

## 5. `generation` and `revision`

Three distinct counters exist. They must not be conflated.

| Name | Owner | Meaning | Grounded in |
| --- | --- | --- | --- |
| `schema_version` | server wire envelope | Integer wire version of `MachineResult` (currently `1`). Consumer branches on shape, not key presence. | `machine_output.py:77`, `machine_output.py:538` |
| `generation` | client | Monotonic per-resource/connection counter. A late response from an older generation is discarded (`StaleDiscard`), never allowed to clear a newer request. | `frontend/js/client/resource-store.js:83,92,102`; SSE connection serial `event-stream.js:14-25` |
| `revision` | server record | Optimistic-concurrency token for a mutable record (e.g. `expected_name_revision`). Stale writes are rejected with a conflict, not silently applied. | `frontend/js/client/job-commands.js:137` |
| `manifest_generation` | server result manifest | Generation of the result manifest a view snapshot was projected from. An older generation is never re-mixed with a newer one. | `view-model.schema.json` (`manifest_generation`) |

Rules for the bridge/view:

- Never render a lower `generation`/`manifest_generation` over a higher one.
- A discarded/aborted request is **not** an error to show as a scientific
  failure; it is a supersession.
- `revision` conflicts are user-visible conflicts with a refresh, not automatic
  retries that overwrite another writer.

## 6. `prepare` / `read` boundary

- `acp_prepare_artifact` **ensures** an artifact exists: it is idempotent
  server-side, admitted against the workspace budget, keyed by
  `(workspace, permission scope, idempotency_key)`, and returns an operation
  reference (`mcp.py:1067-1105`, `mcp.py:726-747`).
- `acp_read_artifact` **never fetches**. It returns an already-prepared
  artifact only; a miss is an error, not an implicit prepare. No budget slot is
  taken (`mcp.py:1107-1117`, tool description `mcp.py:329-330`).
- A caller must therefore: `prepare` → query its reference via `acp_status`
  until the artifact is readable → `read`. A view may show availability
  `unprepared|preparing|readable|failed`; it must not call `read` as a probe.

## 7. Project and attempt ownership

- Identity is **server-enforced**. The client may send `project_id` only as a
  hint; tenant, tier and principal are derived from the verified local
  principal, and a client-supplied tenant/tier is ignored or refused
  (`IdentityMapper.map`, `mcp.py:490-575`).
- Every status/cancel/artifact call is scoped to the resolved `workspace_id`.
  A reference from another project is not resolvable in this scope; it surfaces
  as `resource_not_ready` or `access_denied`, never as a cross-project read
  (`mcp.py:1026-1061`, `mcp.py:1063-1092`).
- Artifact requests carry `job_id`, `resource_id`, `attempt`, `relative_path`
  (`_ARTIFACT_SCHEMA`, `mcp.py:287-298`). `(job_id, attempt)` is part of
  artifact identity; a result from another attempt is not a valid substitute.
- The view model mirrors this as `task_ref = {job_id, attempt}` and refuses a
  second task identity (`view-model.schema.json`).
- The MCP adapter is a transport adapter, **never a second execution owner**:
  it does not write job status, does not run QC, and holds no task DB
  (`mcp.py:1-27`).

## 8. P2 extension policy (B03 gate)

The surface above is frozen at eight tools. The P2 research surface may extend
it **only** through B03's versioned MCP allowlist:

- The allowlist version + digest are reference-only (`mcp_launch.tool_allowlist`
  in `manifest-extension.schema.json`, `owner: "B03"`).
- Until B03 delivers, every P2 extension row is
  `NOT_VERIFIED(mcp_extension)` / `NOT_VERIFIED(b03_handoff)` and **no**
  additional tool is advertised.
- There is **no fallback HTTP client** that bypasses MCP, and **no arbitrary
  shell/SSH tool**. Submitting science outside the platform's controlled
  submit path is forbidden.

## 9. Consumer checklist (machine-checkable intent)

1. `initialize` with `2025-06-18`; reject any other negotiated version.
2. Read `tools/list`; build the model-visible table by prefixing
   `mcp__acp__` exactly once.
3. Strip the prefix before `tools/call`; never send a prefixed name.
4. Branch on JSON-RPC `error` first, then on `isError`, then on
   `structuredContent.error.code`.
5. Persist `reference` for long tasks; poll with `acp_status`; retry with the
   same `idempotency_key`; never re-submit blindly.
6. `prepare` before `read`; treat `read` on a miss as an error, not a trigger.
7. Scope every query by the server-resolved project; never accept a
   client-supplied tenant/tier as authority.
8. Show `generation`/`manifest_generation`-stale data as superseded, not as a
   scientific result.
