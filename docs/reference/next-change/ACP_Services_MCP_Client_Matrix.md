# ACP Services — MCP thin entry: protocol / client matrix (B08-2 / todo 43)

**Created:** 2026-10-19 · **Owner:** B lane (`src/acp_services/entrypoints/mcp.py`) · **Plan:** `.omo/plans/acp-parallel-plan-2-services.md` todo 43 (B08-2)

This note fixes the **locked protocol version**, the **transport/client matrix**
and the **tool allowlist** of the MCP thin entry. It is the tracked reference for
downstream todos 60 (three-entry consistency: GUI / CLI / MCP) and A06.

## Locked protocol

| Item | Value |
|---|---|
| Protocol version (locked) | `2025-06-18` (`mcp.MCP_PROTOCOL_VERSION`) |
| Accepted versions | exactly the locked one (`mcp.LOCKED_PROTOCOL_VERSIONS`) |
| Wire framing | JSON-RPC 2.0; caller owns stdin/stdout line framing (`handle` / `handle_line`) |
| Methods | `initialize`, `tools/list`, `tools/call`, `ping` (+ `notifications/*` ignored) |
| Rejection | another `protocolVersion` → JSON-RPC `-32602` `unsupported_protocol_version` |

A client that requests a different version is **refused**, never silently
downgraded. A protocol version bump is a breaking change; register it here.

## Transport / client matrix

| Client | Transport | Permission scope | Session-coupled? | Status |
|---|---|---|---|---|
| `stdio-mcp-client` | stdio | `local-single` (`mcp.STDIO_PERMISSION_SCOPE`) | No | **Protocol-level mock verified** (`tests/test_services_mcp.py::test_protocol_level_client_session_record`) |
| `http-sse-mcp-client` | http+sse | n/a | No | **NOT_VERIFIED** — no HTTP/SSE transport in this slice |

**Session coupling is always `False`.** The adapter holds no per-connection task
state: a long task returns an operation/job reference immediately, and a dropped
connection never decides the job's fate (`test_disconnect_does_not_affect_operation`).

**Permission scope (explicit).** The stdio transport runs with the local process
scope only. No long-lived API key / bearer token is accepted or forwarded to a
tool. The permission scope is folded into every idempotency key
(`acp_services.data.operations.idempotency_key_for`), so one scope's retry can
never shadow another's operation.

## Tool allowlist (six families, eight tools)

| Tool | Family | Required action | Long task? |
|---|---|---|---|
| `acp_capabilities` | capability discovery | `read` | no |
| `acp_preflight` | preflight / estimate | `read` | no (zero-side-effect dry run) |
| `acp_submit` | controlled submit | `write` | yes |
| `acp_status` | status / cancel | `read` | no |
| `acp_cancel` | status / cancel | `control` | no |
| `acp_prepare_artifact` | artifact prepare / read | `write` | yes |
| `acp_read_artifact` | artifact prepare / read | `read` | no |
| `acp_analyze` | explicit analysis | `write` | yes (requires `explicit=true`) |

Dispatch is a **literal** table keyed by these names (`McpServer._tool_dispatch`).
There is no arbitrary shell, no `getattr`/`eval`/`exec`/`importlib` function-name
dispatch and no path/command string ever reaches a subprocess; a static AST guard
in the test module enforces this.

## Server-enforced policy

- **Identity → Workspace.** The client may only hint a `project_id`; the
  principal, tenant and tier are derived server-side
  (`acp_services.contracts.access.resolve_scope`). A client-supplied tenant is
  ignored; a deployment-tier override is refused (`access_denied`).
- **Budget cap.** A per-workspace `WorkspaceBudget` refuses an over-cap
  submission before the backend is reached (`access_denied`, structured
  `exceeded`). Slot/core counters are released on cancel or a terminal status.
- **Idempotency key.** A `(workspace, scoped-key)` `IdempotencyRegistry` collapses
  replays onto a single operation; identical requests without a client key derive
  a deterministic key.

## Integration record

The only integration exercised in this slice is a **protocol-level mock** that
drives a full session (`initialize → notifications/initialized → tools/list →
acp_submit → acp_status → acp_cancel`) through the text framing. **No real MCP
client is wired yet** (`real_client_integration: NOT_VERIFIED`); wire one when the
downstream GUI/CLI/MCP consistency task (todo 60) lands.

## Change record (append-only)

| Date | Item | Action | Reason |
|---|---|---|---|
| 2026-10-19 | Locked version `2025-06-18`; 8-tool allowlist; stdio matrix | add | B08-2 initial freeze |
