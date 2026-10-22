# ACP Services — MCP thin entry: protocol / client matrix (B08-2 / todo 43)

**Created:** 2026-10-19 · **Owner:** B lane (`src/acp_services/entrypoints/mcp.py`) · **Plan:** `.omo/plans/acp-parallel-plan-2-services.md` todo 43 (B08-2)

This note fixes the **locked protocol version**, the **transport/client matrix**
and the **tool allowlist** of the MCP thin entry. It is the tracked reference for
downstream todos 60 (three-entry consistency: GUI / CLI / MCP) and A06.

## Locked protocol

| Item | Value |
|---|---|
| Protocol version (default) | `2025-06-18` (`mcp.MCP_PROTOCOL_VERSION`) |
| Advertised supported set | exactly `("2025-06-18",)` (`mcp.SUPPORTED_PROTOCOL_VERSIONS`, alias `mcp.LOCKED_PROTOCOL_VERSIONS`) |
| Wire framing | newline-delimited UTF-8 JSON-RPC 2.0 (`mcp_stdio.JsonRpcLineTransport`; `handle` / `handle_line` remain the protocol core) |
| Methods | `initialize`, `tools/list`, `tools/call`, `ping` (+ `notifications/*` ignored) |
| Lifecycle gate | `tools/list` / `tools/call` before a successful `initialize` → `-32600` `not_initialized` (no backend touched) |
| Negotiation | same version → same; client `protocolVersion` unsupported but `protocolVersions` includes a supported one → negotiated common version (recorded); no common version → `-32602` `unsupported_protocol_version` |
| Batch frames | rejected explicitly (`-32600` `batch_not_supported`); no implicit batch support |

A client that requests an unsupported version is **never silently accepted**:
the server either returns a version both sides explicitly declared, or fails
`initialize`. A protocol version bump is a breaking change; register it here and
in the process-level tests before changing the advertised set.

## Transport / client matrix

| Client | Transport | Permission scope | Session-coupled? | Status |
|---|---|---|---|---|
| `stdio-mcp-client` | stdio | `local-single` (`mcp.STDIO_PERMISSION_SCOPE`) | No | **Real stdio process verified (B05)** — `tests/test_acp2_runtime_mcp_process.py` + `acp_mcp_stdio_probe.py` against a live Controller |
| `dsh-official-mcp-client` | stdio | `local-single` | No | **NOT_VERIFIED (H2 gate)** — real DSH handshake owned by C; do not claim compatibility |
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

B05 adds a **real stdio process**: `acp mcp serve --transport stdio
--controller <loopback-url>` launches `acp_services.entrypoints.mcp_stdio`,
which owns newline-delimited UTF-8 JSON-RPC framing while
`acp_services.entrypoints.mcp` remains the protocol/allowlist/policy owner.
The read-only probe (`initialize → notifications/initialized → tools/list →
acp_capabilities`) runs against a live Controller and confirms eight tools,
`protocolVersion=2025-06-18`, stdout protocol-only and an unchanged task count.
The `acp`-side composition root is `src/acp/services_mcp_bootstrap.py`; at B05
`acp_capabilities`/`acp_status` are bound for real and the remaining business
commands return structured `unsupported_capability` (`binding=B06`,
`status=not_verified`).

The **official DSH client handshake is still NOT_VERIFIED** (H2 gate, C-owned).
The B05 evidence covers the stdlib probe and the process-level matrix only.

## Change record (append-only)

| Date | Item | Action | Reason |
|---|---|---|---|
| 2026-10-19 | Locked version `2025-06-18`; 8-tool allowlist; stdio matrix | add | B08-2 initial freeze |
| 2026-10-22 | Real stdio transport; advertised supported set; lifecycle gate; explicit batch rejection; DSH row `NOT_VERIFIED` | update | B05 tested set unchanged (`2025-06-18`) + real subprocess/probe evidence; no new version claimed |

<!-- SERVICES_STATUS_SYNC:todo70 -->
## 实施状态同步（2026-10-19 / todo 70）

- **delivered:** 锁定协议版本 `2025-06-18`、8 工具 allowlist、stdio 客户端矩阵与 `src/acp_services/entrypoints/mcp.py` 协议实现（39 项测试）。
- **verified:** 模块/协议级 mock 会话（closure L1-09）；无任意 shell、身份/预算/幂等边界由测试守护。
- **NOT_VERIFIED:** MCP **默认入口绑定**（closure L2-14）与真实 MCP 客户端集成（`real_client_integration: NOT_VERIFIED`）；HTTP/SSE 传输不在本切片。
- **limits:** 协议实现已交付 ≠ 默认入口已接线；连接寿命不决定任务存活，长任务返回 operation/job。
- **authority:** 状态以 `docs/reference/current/ACP_Services_Closure_20261019.md` 为准（L2-14 MCP 默认入口 `NOT_VERIFIED`）。
- **B05 (2026-10-22):** 真实 stdio 入口 `acp mcp serve --transport stdio --controller <url>` 已交付并通过真实子进程 + 只读探针验证（八工具、`2025-06-18`、任务数不变）；真实 DSH 官方握手仍为 H2 `NOT_VERIFIED`（C 执行）。
