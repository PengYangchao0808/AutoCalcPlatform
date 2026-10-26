# ADR-006 — MCP 握手协商、DSH 客户端锁定与 “SDK 通过 ≠ Agent 通过”

- **状态**：Accepted（握手部分，2026-10-26；扩展协商部分由 M0-09 追加 §D7，2026-10-26）；研究扩展版本冻结待 M0-12/M1-01
- **Owner**：B（协议/服务端）；C-RPH（真实 DSH 客户端与体验面）；INT 汇总签核
- **来源任务**：`reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md` M0-08（G01）、M0-09（G02/G04, R12）
- **取代**：早期计划里仅作占位的 “ADR-006：DSH 版本锁定、Profile/Bundle 接口及适配层”（`docs/ACP_2.0_Four_Component_Collaboration_Plan_20261020.md` §ADR-006）。本文件保留其 DSH 锁定语义，并把 MCP 握手/协商规则固化为可执行契约。
- **代码驻点**：`src/acp_services/entrypoints/mcp.py`（`_negotiate_protocol_version` / `_initialize` / `negotiate_research_extension` / `_tool_capabilities` / `client_matrix`）、`src/acp_services/entrypoints/mcp_stdio.py`（`_PRE_INIT_METHODS`）、`src/acp/mcp_extension.py`、`src/acp/api/routes.py`（`/api/capabilities`）、`src/acp/services_mcp_bootstrap.py`、`rph/packages/bridge/src/index.ts`、`rph/packages/dsh-bundle/src/index.ts`
- **证据**：`tests/test_services_mcp.py`、`tests/test_acp2_mcp_research_handshake.py`、`tests/test_acp2_runtime_mcp_process.py`、`tests/test_acp2_mcp_sdk_contract.py`（`-m contract`）、`rph/test/mcp-handshake.test.ts`、`scripts/acceptance/acp2_mcp_negotiation_probe.py`、`scripts/acceptance/acp2_mcp_live_client.mjs`、`reports/acp2_remediation_20261026/m0-09/summary.json`

## 1. 背景与问题（G01）

1. 旧实现把 “客户端请求了服务端不支持的版本” 当成服务端错误：`initialize` 返回 `-32602`
   且 `data.code = "unsupported_protocol_version"`。MCP 2025-06-18 lifecycle 的语义相反：
   服务端应回答自己支持的版本，由客户端判断能否继续。
2. 冻结的 DSH 客户端（`@modelcontextprotocol/client@2.2.0`，`versionNegotiation: {mode: "auto"}`）
   固定先发 `protocolVersion: "2025-11-25"`，且**不**发送本仓私有 `protocolVersions` 回退列表。
   旧实现因此必然拒绝，R3 反例 `evidence/verification-r3-20261026/dsh.json` 记录
   `code=-32602 message="unsupported protocol version '2025-11-25'"`。
3. 旧 `initialize` 结果顶层携带 `extensions`，并宣称 `acp.research.v1 negotiated: true`，
   而该字段会被客户端丢弃，且 negotiated 的判定权/字段位置尚未决策（M0-09/M0-12）。

## 2. 决策

### D1 — 服务端只回答“我支持什么”，兼容性由客户端判断

`_negotiate_protocol_version(requested)` 只做三件事：

| 输入 | 输出 |
| --- | --- |
| 未提供 `protocolVersion` | `2025-06-18`（`MCP_PROTOCOL_VERSION`） |
| `protocolVersion` ∈ `SUPPORTED_PROTOCOL_VERSIONS` | 回显该版本 |
| `protocolVersion` ∉ 支持集合（含 `2025-11-25`、`1999-01-01`） | `2025-06-18`，**不是错误** |

服务端不得因为“没有共同版本”就伪造协商成功，也不得把它升级成 JSON-RPC 错误。

### D2 — 只有畸形形状是 `-32602`

| 字段 | 合法 | 畸形（`-32602`） |
| --- | --- | --- |
| `protocolVersion` | 省略 或 非空字符串 | 数字/布尔/对象/数组/空串或纯空白 → `data.code="invalid_protocol_version"` |
| `protocolVersions`（私有） | 省略 或 非空字符串数组 | 字符串/含非字符串/含空串/对象 → `-32602`（保持既有消息） |

### D3 — 私有 `protocolVersions` 仅记录，永不作门

它被写入 `capabilities.extensions["acp.negotiation.v1"].client_declared_versions`
作为 trace/证据，不参与版本选择、不做降级判断、不产生 `required` 语义。
未来若采纳为标准协商输入，必须新 ADR 或 M0-12 冻结，不得就地改语义。

### D4 — `initialize` 不宣称任何扩展已协商

结果形状：

```json
{
  "protocolVersion": "2025-06-18",
  "supportedProtocolVersions": ["2025-06-18"],
  "capabilities": {
    "tools": {"listChanged": false},
    "extensions": {
      "acp.negotiation.v1": {
        "id": "acp.negotiation.v1",
        "version": "1.0.0",
        "supported_versions": ["2025-06-18"],
        "server_preferred_version": "2025-06-18",
        "negotiation": "server_advertises_supported_version",
        "client_decides_compatibility": true,
        "requested_version": "<client value or null>",
        "client_declared_versions": ["<private hint, recorded only>"]
      }
    }
  },
  "serverInfo": {"name": "acp-services-mcp", "version": "0.1.0"},
  "instructions": "…"
}
```

- **顶层 `extensions` 已删除**；`acp.research.v1` / `acp.identity.v1` 不再出现在 `initialize`。
- `capabilities.extensions` 只承载 supported_versions 与协商指引，**没有** `negotiated` 字段、
  没有 `negotiated: true/false` 的任何变体。
- 身份绑定投影（`IdentityMapper.binding_projection()`）保留为未来应用级握手的输入来源，
  但当前不上线到 wire。
- `acp.negotiation.v1` 是 B 拥有的**信息型**扩展 id，不是能力承诺；M0-12 冻结集成决策时
  可改名/移位，但不得把它变成 negotiated 声明。其字面量目前沿用既有 `chr(97)` 组装
  （`acp_services` 隔离扫描器把任何 `"acp.*"` 字符串常量视为 `acp` 导入）；M1-01 把它
  迁入 `entrypoints/wire_identifiers.py` 并去掉组装，本 ADR 不新增扫描器例外。

### D5 — `server/discover` 探测：放行到协议层，返回普通 `-32601`

`acp2_mcp_live_client.mjs` 实测：SDK `auto` 模式在 stdio 上先做**一次性 sibling 探测进程**
（`server/discover`），失败后才对**会话进程**发 `initialize`。

- `mcp_stdio._PRE_INIT_METHODS` 增加 `server/discover`，使生命周期门（原先返回 `-32600` +
  `data.code="not_initialized"`）不再劫持它；
- `McpServer._invoke` **不实现** discover：客户端得到普通 JSON-RPC `-32601 unknown method`；
- 服务端不得返回伪造的 `DiscoverResult`，也不得用 `-32022`（UnsupportedProtocolVersion，
  未来协议语义的前瞻保留码）表达 2025-era 版本不支持；
- 探测进程与会话进程都必须零副作用（见 §4）。

### D6 — 回退规则（服务端侧）

1. 客户端声明现代版本（≥ `2026-07-28`）→ 本服务端仍只回答 `2025-06-18`，客户端自行回退到
   legacy `initialize`（SDK `auto` 的既有行为）。
2. 客户端在 legacy `initialize` 里收到 `2025-06-18` 后若自身支持集合不含它 → **客户端失败**，
   服务端不重试、不改答、不静默升级；该失败仍是零任务。
3. 客户端不发 `protocolVersions` → 不受影响（D3）。
4. 客户端从不发 `initialize` 而直接 `tools/list`/`tools/call` → 仍 `not_initialized` 拒绝
   （生命周期门保留，M0-08 未放宽）。
5. `SUPPORTED_PROTOCOL_VERSIONS` 扩容属于契约变更：必须同时更新 process 测试、
   negotiation probe、client matrix 与本 ADR。

## 3. Client matrix（真实锁定值）

| 角色 | 包 / 版本 | 实际 `clientInfo` | 构造函数选项 | 传输 |
| --- | --- | --- | --- | --- |
| 冻结 DSH 客户端 | `@deepseek-ai/dsh-mcp-client` `0.2.1-rc.2`（外部 `$ACP2_TOOLS/deepseek-harness`） | `dsh-mcp-client` / `0.0.1` | `{capabilities:{}, versionNegotiation:{mode:"auto"}}` | `StdioClientTransport`（插件 `src/transport.ts::createTransport`） |
| 其钉住的 SDK | `@modelcontextprotocol/client` `2.2.0`（插件 `dependencies` 精确声明） | — | `LATEST=2025-11-25`，`SUPPORTED=[2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05, 2024-10-07]` | stdio |

实测（`tests/test_acp2_mcp_sdk_contract.py`，真实 Node 22.19 + 外部 DSH 树）：

- 客户端 `initialize.protocolVersion = "2025-11-25"`；
- 服务端回答 `2025-06-18`；客户端接受（其支持集合包含该版本）；
- `tools/list` 返回 8 个 P1 工具；
- `acp_capabilities` 调用 `status=ok`；
- 该结果同步进 `mcp.client_matrix()`：DSH 行状态为 “PASS handshake … full DSH Agent NOT_VERIFIED”。

## 4. 零副作用（探测进程 + 会话进程）

实测观测（loopback Controller stub 计数，`tests/_acp2_mcp_live_harness.py`）：

| 观测 | 期望 | 说明 |
| --- | --- | --- |
| `GET /api/health` | **≥ 2** | 每个适配器进程启动各一次 ⇒ sibling 探测进程 + 会话进程都真实存在 |
| `POST /api/v1/jobs` | **0** | 握手不建任务 |
| 其他 Controller 请求 | **0** | 探测不触碰 status/cancel/artifact |
| `ACP_RUN_ROOT` 新文件 | **0** | 不建 DB/目录/锁 |
| 会话 wire 的 send 方法序列 | `initialize, notifications/initialized, tools/list, tools/call` | `server/discover` 只出现在 sibling，不出现在会话连接 |
| probe/initialize 后残留进程 | 0 | 探测进程被 SDK 回收，会话进程 EOF 退出 0 |

进程级 Python 测试另证：init 前 `tools/list` 被拒、`server/discover` 返回 `-32601`、
畸形版本 `-32602` 之后仍可重新 `initialize`，全程 `submit_posts == 0`。
不启动第二个 Controller（`launch.controller.start_controller = false`），生产 `:8765` 不被触碰。

## 5. “SDK 握手通过” ≠ “完整 DSH Agent 通过”

本 ADR 关闭的只是 G01 的 **握手** 判定：官方 SDK + 官方 transport + 官方构造函数选项能完成
`initialize` 并列出八工具。以下仍为 `NOT_VERIFIED`，不得引用本 ADR 宣称完整放行：

- DSH Agent 端到端（模型调用、skills、preset、`ctx.tools` 注册、权限/审批交互）；
- 研究扩展（`acp.research.v1`）：**wire 位置已由 §D7（M0-09）选定**为
  `tools/call acp_capabilities`；版本冻结/实现语义仍归 M0-12 与 M1-01，
  十个研究工具仍未交付（`implemented=false`）；
- 身份/项目**绑定来源**已在 §D7 上线（`identity` 字段来自服务端），但
  “身份随 handshake 传入并参与授权”的完整应用级握手仍 `NOT_VERIFIED`（M1-01/M3）；
- 安装后（发行 wheel + 干净 venv）握手 → 由 M5-03/发布验收承担；
- LSF/QC/真实 Science 执行一律与本 ADR 无关（零调用）。

## 6. 对工具的约束

1. 任何“握手失败即 PASS”或“把 `-32602` 当作正常协商结果”的记录都是 FAIL。
2. 不得用 `-32022` 表达 2025-era 版本不支持；该码只在未来协议语义下使用。
3. 新增/修改客户端行必须附真实 wire + 版本来源；手写 JSON-RPC 自证不算客户端证据。
4. `initialize`（含其 `capabilities.extensions`）中不得出现 `negotiated` 类断言，直至
   M1-01 冻结；扩展的 `implemented`/`negotiated` 只允许出现在
   `tools/call acp_capabilities` 的 `extensions[]`（§D7.2），且 `negotiated=true`
   必须先 `implemented=true`。
5. 不得把 `extensions[]` 里的广告当作交付证据：承诺集合未被注册工具覆盖时，
   `implemented` 必须为 `false`。

### 6.1 已知契约漂移（M0-08 登记；已由 §6.2 收口）

M0-08 交付时已交付契约仍写着旧语义；下表保留当时的登记事实，实际 mutation 见 §6.2：

| 文件 | 字段 | 现值 | M0-08 后的事实 | 处置 |
| --- | --- | --- | --- | --- |
| `acp2-contracts/runtime/application-api.v1.json` | `mcp_extensions["acp.research.v1"].negotiated_in` | `"initialize"` | `initialize` 已不含任何扩展声明 | M0-09 决定扩展路径后由 M0-12 冻结并 mutation |
| `src/acp/services_mcp_bootstrap.py` | `_ControllerCapabilities` 默认 `extensions[0].negotiated` | `true` | 工具输出仍宣称研究扩展已协商 | 同上（M0-09 的 `acp_capabilities` 语义） |
| `src/acp_services/entrypoints/mcp.py` | `IdentityMapper.binding_projection()` | 保留但不上线 | 身份传递未在 wire 上 | M0-09 应用级握手接线 |

这三处不影响 G01 握手判定（它们不参与 `initialize` 的版本行为），但 M1-01 之前必须消除，
且不得把其中任何一处当作 “扩展已协商” 的证据。

### 6.2 M0-09 收口（2026-10-26，已消除上表三处漂移）

| 文件 | 变化 | 结果 |
| --- | --- | --- |
| `acp2-contracts/runtime/application-api.v1.json` | `mcp_extensions["acp.research.v1"].negotiated_in` `initialize` → `tools/call`；`version` `1.1.0` → `1.0.0`（1.1.0 是应用契约整体版本，不是扩展版本） | 与 §D7 一致；`contract_version` 仍为 `1.1.0` |
| `src/acp/services_mcp_bootstrap.py` | 删除 `_ControllerCapabilities` 的 `extensions=[{... negotiated: true}]` 兜底 | `extensions[]` 只由 `mcp.McpServer._tool_capabilities` 计算一次 |
| `src/acp_services/entrypoints/mcp.py` | `IdentityMapper.binding_projection()` 现在作为 `identity` 字段上线到 `acp_capabilities` 结果 | 身份/项目来源可见且仍是服务端绑定 |
| `acp2-contracts/runtime/mcp-launch.schema.json` / `.template.json` | `initialize.extensions[...].negotiated` const `true` → `false`（模板值同步 1.0.0） | launch descriptor 不再宣称任何协商状态 |

`p2-research-operations.v1.json::client_path.negotiation` 由 `initialize_param` 改为
`request_carrier=tools/call` / `request_tool=acp_capabilities` /
`request_field=research_extension` / `initialize_claim=none`；`extension_version` 保持
`1.0.0`（唯一权威）。契约的 sha256 与 `contract_digest` 已重算，`validate_acp2_contracts.py` 通过。

## 7. §D7 — M0-09：研究扩展走应用级握手（`tools/call acp_capabilities`）

**决策（2026-10-26，`path_selected`）**：扩展的 wire 位置是 **`tools/call` 的
`acp_capabilities`**，请求字段 `research_extension{id,version}`；`initialize` 不做任何扩展声明
（只通过 `capabilities.extensions["acp.negotiation.v1"].extension_handshake` **指向**载体）。

### D7.1 为什么不是 `initialize`、也不是上游配置变更

| 选项 | 实测 | 结论 |
| --- | --- | --- |
| `initialize` 顶层 `extensions` | 冻结客户端丢弃该字段（M0-08 已证） | 不可用 |
| `initialize` 的 `capabilities.extensions` | 冻结 DSH 客户端硬编码 `capabilities: {}`（`@deepseek-ai/dsh-mcp-client` 0.2.1-rc.2，无配置项） | 需上游变更 → 走 external_change_required |
| 应用级握手（本决策） | `acp_capabilities` 属 P1 八工具，冻结客户端已可调用 | **选中**，无需外部变更 |

因此 **M0-13 = `NOT_APPLICABLE`（`external_change_not_required`）**：没有 DSH fork、patch、
lock 变更或 BOM 条目，未修改外部 `$ACP2_TOOLS/deepseek-ai` 源树。

### D7.2 三态分离（advertised / implemented / negotiated）

`acp_capabilities` 的结果恒带 `extensions[]`，每项：

| 字段 | 含义 |
| --- | --- |
| `id` / `version` / `supported_versions` / `supported` | 服务端**支持并广告**的扩展身份（权威 `acp.research.v1@1.0.0`） |
| `requested` | 客户端本次请求的 `{id, version}`（审计回显，`null` 表示未请求） |
| `negotiated_by` | 恒为 `tools/call:acp_capabilities`（唯一载体） |
| `implemented` | **实现集合是否覆盖承诺集合**：`RESEARCH_TOOL_NAMES`（10 个 P2 工具）必须全部在 `TOOL_ALLOWLIST` 中。当前为 `false` |
| `negotiated` | `implemented` **且** 请求版本等于支持版本的唯一结果。当前恒 `false` |
| `refusal` | 带内 structured 拒绝（`unsupported_capability` + `params.rule`），无拒绝为 `null` |

`params.rule` 取值：`extension_not_advertised`（id 不匹配/服务端无该扩展）、
`extension_version_mismatch`（版本不匹配）、`extension_not_implemented`（未实现）。
权限不匹配（请求 `project_id` 与服务端绑定不一致）用 `access_denied` +
`params.rule=project_scope_mismatch`，且拒绝结果**仍**带 `extensions[]` 供客户端读取支持版本。

**硬规则**：任何 `negotiated=true` 必须先 `implemented=true`；广告 ≠ 实现 ≠ 协商。
`initialize` 永远不出现 `acp.research.v1`、也不出现 `negotiated` 断言（`tests/test_services_mcp.py` 守护）。

### D7.3 DSH 实际行为（`tools/list` 与 `isError`）

| 观测 | 事实 | 对设计的影响 |
| --- | --- | --- |
| `tools/list` | 是模型可见的**全部**工具面；客户端把每条广告都注册，**没有**按工具协商开关 | “未握手不可 callable” 由**未注册**保证：10 个研究工具不在 allowlist ⇒ 调用得到 JSON-RPC `unknown_tool`，不是靠 `negotiated=false` 拦 |
| `isError` 结果 | 官方插件把 MCP `isError` **转成抛出的工具失败**（`packages/mcp/mcp-client/src/tools.ts::createExecutor`），`structuredContent` 不再交付模型；消息体是结果文本 | ACP 把整份 machine envelope 放进 text，因此 `dsh-bundle` 适配器可从错误消息恢复 typed 拒绝（`rph/test/mcp-handshake.test.ts` 用 fake `ctx.tools` 证明） |
| 工具名 | DSH 注册为 `mcp__<serverName>__<rawName>`（`publicToolName`），`ctx.tools.execute` 按该模型可见名查找 | 适配器默认加 `mcp__acp__` 前缀（`resolveToolName` 可覆盖） |

### D7.4 客户端面（C-RPH）

- `rph/packages/bridge/src/index.ts` 新增纯函数 `parseExtensionAdvertisements` /
  `parseResearchHandshake`（只从服务端广告推导 `negotiated`，请求永不自我升级）与
  `negotiateResearch(client, request)`（经注入的 `PlatformClient` 调 `acp_capabilities`，
  遵守 `cli → dsh-bundle → bridge → research`，无 fetch/spawn/新包）。
- `rph/packages/dsh-bundle/src/index.ts` 新增 `platformClientFromToolRuntime(ctx.tools, opts)`：
  结构型 `DshToolRuntime`（不 import DSH），成功保留 `structuredContent`，`isError` 从
  错误消息恢复 envelope。
- `negotiateResearch` 的 `negotiatedExtensions` 可直接喂给 `Bridge({negotiatedExtensions})`；
  未协商时 `assertOperationAllowed` 仍在本地拒绝 P2（`extension_not_negotiated`）。

### D7.5 证据

- `reports/acp2_remediation_20261026/m0-09/summary.json`（决策、口径、零副作用、产物 digest）
  与 `live-client/`（真实 DSH 客户端 + 真实 `acp mcp serve` 的
  `dsh-client.json` / `wire.jsonl` / `mcp-launch.json`）。
- `tests/test_acp2_mcp_sdk_contract.py::test_real_sdk_capabilities_call_returns_the_research_handshake`
  （真实 SDK 调 `acp_capabilities` 并读到 `extensions[]`；`tools/call` 计 2 次，`POST /api/v1/jobs` 计 0 次）。
- `tests/test_acp2_mcp_research_handshake.py`（单元/HTTP/ControllerClient 等价面）
  与 `rph/test/mcp-handshake.test.ts`（bridge + fake `ctx.tools`）。
- 契约正负样本：`runtime/samples/application-api/capabilities-research-extension{,.positive}`、
  `...version-mismatch.negative.json`、`...unknown.negative.json`。

### D7.6 仍未验证（不得据本文宣称放行）

- 完整 DSH Agent 端到端（模型、skills、preset、`ctx.tools` 注册、审批交互）→ M3-01/M3-03/M3-04；
- P2 十个研究工具**未交付**：`implemented=false`、`negotiated=false` 是当前正确状态，
  任何 “研究工具已交付” 的表述都是违约；
- 发行 wheel + 干净 venv 下的握手 → M5-03；
- 真实 QC/LSF/模型调用与本任务无关（零调用）。

## 8. 验证清单（本文档对应的可复现命令）

```bash
PYTHONPATH=src python -m pytest tests/test_services_mcp.py tests/test_acp2_runtime_mcp_process.py -q
PYTHONPATH=src python -m pytest tests/test_acp2_mcp_research_handshake.py -q          # M0-09 单元/HTTP/客户端
PYTHONPATH=src python -m pytest tests/test_acp2_mcp_sdk_contract.py -m contract -q   # 真实 SDK/transport
python scripts/acceptance/acp2_mcp_negotiation_probe.py --launch <launch.json> --out <dir>
node scripts/acceptance/acp2_mcp_live_client.mjs --launch <launch.json> --out <dir> [--scenario incompatible-client]
python scripts/acceptance/validate_acp2_contracts.py                                 # 契约 digest/结构门
pnpm --dir rph build && pnpm --dir rph test && pnpm --dir rph check                   # C-RPH 握手面
```
