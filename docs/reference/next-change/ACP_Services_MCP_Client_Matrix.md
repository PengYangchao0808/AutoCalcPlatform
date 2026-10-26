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
| Methods | `initialize`, `tools/list`, `tools/call`, `ping` (+ `notifications/*` ignored; `server/discover` is answered as an unknown method) |
| Lifecycle gate | `tools/list` / `tools/call` before a successful `initialize` → `-32600` `not_initialized` (no backend touched) |
| Negotiation (M0-08 / ADR-006) | requested version supported → echoed; requested version unsupported → server answers `2025-06-18` and the **client** decides compatibility; the private `protocolVersions` list is recorded only, never a gate; only a malformed `protocolVersion`/`protocolVersions` shape is `-32602` `invalid_protocol_version` |
| Batch frames | rejected explicitly (`-32600` `batch_not_supported`); no implicit batch support |

A client that requests an unsupported version is **not** silently upgraded: it
receives `2025-06-18` and must accept or refuse it itself (MCP 2025-06-18
lifecycle). A protocol version bump is a breaking change; register it here, in the
process-level tests, in the negotiation probe and in ADR-006 before changing the
advertised set. The `initialize` result carries no top-level `extensions` and makes
no `negotiated` claim. Extension negotiation (M0-09, ADR-006 §D7) is a
**`tools/call acp_capabilities`** handshake: the result's `extensions[]` reports
`implemented`/`negotiated`/`refusal` for `acp.research.v1@1.0.0`.

## Transport / client matrix

| Client | Transport | Permission scope | Session-coupled? | Status |
|---|---|---|---|---|
| `stdio-mcp-client` | stdio | `local-single` (`mcp.STDIO_PERMISSION_SCOPE`) | No | **Real stdio process verified (B05 + M0-08)** — `tests/test_acp2_runtime_mcp_process.py`, `tests/test_acp2_mcp_sdk_contract.py`, `acp2_mcp_negotiation_probe.py` + `acp_mcp_stdio_probe.py` |
| `dsh-official-mcp-client` (`@deepseek-ai/dsh-mcp-client` `0.2.1-rc.2`, clientInfo `dsh-mcp-client/0.0.1`) | stdio (`StdioClientTransport`) | `local-single` | No | **PASS: handshake** with `@modelcontextprotocol/client` `2.2.0`, production options `versionNegotiation:{mode:"auto"}` — client offers `2025-11-25`, server answers `2025-06-18`, client accepts, `tools/list` = 8, zero tasks. **Full DSH Agent (model/skills/research extension) `NOT_VERIFIED`** |
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

### B25b installed-entry verification (2026-10-23)

The clean role-wheel install (`acp-web` 0.3.0 in a fresh venv) launched the real entry as
`acp mcp serve --controller http://127.0.0.1:<isolated-port>` against an isolated Lab server.
`initialize` negotiated `2025-06-18` (supported set exactly `("2025-06-18",)`) and `tools/list`
returned the eight P1 tools by name: `acp_capabilities`, `acp_preflight`, `acp_submit`,
`acp_status`, `acp_cancel`, `acp_prepare_artifact`, `acp_read_artifact`, `acp_analyze`.
The run is recorded in `reports/acp2_implementation/bb45a46/B/B25/mcp-tools.json` and in the
`b25.stdio_mcp_eight_tools` assertion.

This closes the **installed-entry** part of the B25b scope only. The official DSH client handshake
remains `NOT_VERIFIED` (H2 gate, C-owned), and the `dsh-official-mcp-client` row above is unchanged.
The launch-template metadata (`entrypoint: acp_services.entrypoints.mcp`) names the protocol owner;
the runnable command is the `acp mcp serve` console entry, which composes the same module through
`src/acp/services_mcp_bootstrap.py`.

## Change record (append-only)

| Date | Item | Action | Reason |
|---|---|---|---|
| 2026-10-19 | Locked version `2025-06-18`; 8-tool allowlist; stdio matrix | add | B08-2 initial freeze |
| 2026-10-22 | Real stdio transport; advertised supported set; lifecycle gate; explicit batch rejection; DSH row `NOT_VERIFIED` | update | B05 tested set unchanged (`2025-06-18`) + real subprocess/probe evidence; no new version claimed |
| 2026-10-23 | Installed-wheel stdio entry re-verified (`protocol 2025-06-18`, eight tools) | update | B25b clean role-wheel install; DSH official handshake still `NOT_VERIFIED` |
| 2026-10-26 | Standard MCP handshake (M0-08, ADR-006): unsupported request version → server version (no `-32602`); private `protocolVersions` recorded only; malformed shapes `-32602`; `server/discover` → ordinary `-32601`; `initialize` drops top-level `extensions`/`negotiated`; DSH row → PASS handshake (full Agent `NOT_VERIFIED`) | update | G01 closure with real `@modelcontextprotocol/client` 2.2.0 + DSH transport evidence (`reports/acp2_remediation_20261026/m0-08/`) |
| 2026-10-26 | Research extension negotiated in `tools/call acp_capabilities` (`research_extension{id,version}` → `extensions[]` with `implemented=false`/`negotiated=false`/typed refusal); `initialize` only points at the carrier; extension version authority `1.0.0` | update | M0-09 path selection (ADR-006 §D7); real DSH client evidence + handshake artifacts in `reports/acp2_remediation_20261026/m0-09/`; ten P2 tools remain undelivered |

<!-- SERVICES_STATUS_SYNC:todo70 -->
## 实施状态同步（2026-10-19 / todo 70）

- **delivered:** 锁定协议版本 `2025-06-18`、8 工具 allowlist、stdio 客户端矩阵与 `src/acp_services/entrypoints/mcp.py` 协议实现（39 项测试）。
- **verified:** 模块/协议级 mock 会话（closure L1-09）；无任意 shell、身份/预算/幂等边界由测试守护。
- **NOT_VERIFIED:** MCP **默认入口绑定**（closure L2-14）与真实 MCP 客户端集成（`real_client_integration: NOT_VERIFIED`）；HTTP/SSE 传输不在本切片。
- **limits:** 协议实现已交付 ≠ 默认入口已接线；连接寿命不决定任务存活，长任务返回 operation/job。
- **authority:** 状态以 `docs/reference/current/ACP_Services_Closure_20261019.md` 为准（L2-14 MCP 默认入口 `NOT_VERIFIED`）。
- **B05 (2026-10-22):** 真实 stdio 入口 `acp mcp serve --transport stdio --controller <url>` 已交付并通过真实子进程 + 只读探针验证（八工具、`2025-06-18`、任务数不变）；真实 DSH 官方握手仍为 H2 `NOT_VERIFIED`（C 执行）。
- **M0-08 (2026-10-26):** 标准握手已按 ADR-006 落地并由真实 `@modelcontextprotocol/client` 2.2.0 + DSH transport 验证（initialize + `tools/list` 8，零任务）；完整 DSH Agent、研究扩展协商与安装后握手仍分别 `NOT_VERIFIED`（M0-09/M0-12/M5-03）。
