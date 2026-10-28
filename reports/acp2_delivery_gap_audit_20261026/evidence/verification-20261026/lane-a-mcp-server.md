# Lane A — MCP 服务端协议 / 工具 / 参数 / 发现 专项验证

- **验证对象**: `reports/acp2_delivery_gap_audit_20261026/GAP_REPORT.md` §5 G01–G04（第 114–160 行）
- **代码基线**: `ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（HEAD，2026-10-25）
- **环境**: Python 3.11.13；Node v22.19.0；已运行的 Controller `http://127.0.0.1:8765`
- **验证方式**: 源码阅读 + `git log/blame` + 现有证据文件复核 + 只读 live 探针 + 参数 normalizer 直接复现 + 真实 DSH 客户端依赖的 stdio 握手复现
- **边界声明**: 未修改任何业务代码；未 commit；未重启服务；未提交 QC 作业；未调用模型 API。live 探测只做了 `GET /api/*` 与 `initialize` / `tools/list` / `tools/call acp_capabilities`，以及三条在 normalizer 处即失败、不会到达 Controller 的参数复现；未创建任务或 prepare。

## 结论总表

| Gap | 结论 | 一句话 |
|---|---|---|
| G01 | **确认** | 服务端只支持 `2025-06-18` 且要求非标准 `protocolVersions`；真实 DSH 客户端（`@modelcontextprotocol/client@2.2.0`）发送 `2025-11-25`，握手稳定 `-32602`。 |
| G02 | **确认** | `initialize` 无条件宣告 `acp.research.v1@1.1.0/negotiated=true`，但只注册 8 个 P1 工具；P2 契约的 10 个研究工具与 ControllerClient 方法全部缺失。 |
| G03 | **确认** | Bridge 的 prepare/read/evidence 参数在服务端 normalizer 处分别因 `purpose`/`artifact_ref`/`analysis_id` 缺失被拒；Bridge 还自行丢弃 read 的 `generation` 与 evidence 的 `task/attempt/resource`。 |
| G04 | **确认** | `/api/capabilities` 的 `capability_descriptors` 为 `null`，分析 registry 全仓零注册者，MCP 不消费 `/api/v2/science/capabilities` 的科学描述。 |

---

## G01 协议协商规则使本可兼容的官方 SDK 连接失败

- **验证结论: 确认**（核心事实成立；但报告使用的“SDK 1.31.0”不是锁定 DSH 插件实际依赖的包，见下）

### 报告原声明中不准确处

1. **证据包选错了 SDK 包**。报告与 `evidence/sdk-probe.json` 使用
   `@modelcontextprotocol/sdk@1.31.0`。但锁定 DSH 的 MCP 插件
   `@deepseek-ai/dsh-mcp-client@0.2.1-rc.2` 实际 import 的是
   `@modelcontextprotocol/client@2.2.0`：
   - `/ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/package.json:42` → `"@modelcontextprotocol/client": "2.2.0"`
   - `/ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/src/connection.ts:18` → `import { Client, type Transport } from '@modelcontextprotocol/client'`
   - `connection.ts:258` 创建 `new Client(...)`，`:262` 传入 `versionNegotiation: { mode: 'auto' }`

   老 SDK 1.31.0 的 `ClientOptions` 并不认识 `versionNegotiation`（在
   `@modelcontextprotocol/sdk@1.31.0` 全包 grep 无此标识符），该选项在旧包里是被忽略的。
   我们用**真实插件依赖** `@modelcontextprotocol/client@2.2.0` + `versionNegotiation:{mode:'auto'}`
   重新对 ACP stdio 做了握手，结论仍是失败（见下），因此报告的结论成立，但证据链需要换包。

2. **规范引用过于绝对**。MCP 2025-06-18 的 *Version Negotiation* 规范段确实写的是
   “If the server supports the requested protocol version, it MUST respond with the same
   version. Otherwise, the server MUST respond with another protocol version it supports.”
   同一页的 *Error Handling* 又给出一个 `-32602 Unsupported protocol version` 示例。
   报告引用前者推出“标准不允许报错”时，应同时说明规范页存在这个示例，避免被反驳。

3. **协议字段的非标准性没有点明**。`protocolVersions` 不是 MCP `initialize` 标准参数；
   `@modelcontextprotocol/client@2.2.0` 的 initialize 请求只发送
   `protocolVersion / capabilities / clientInfo`。这是 ACP 自造的回退输入，不是“客户端未按标准枚举”。

### 证据

**服务端实现**

- `src/acp_services/entrypoints/mcp.py:125-138`：`MCP_PROTOCOL_VERSION = "2025-06-18"`，
  `LOCKED_PROTOCOL_VERSIONS = (MCP_PROTOCOL_VERSION,)`，`SUPPORTED_PROTOCOL_VERSIONS` 只有这一个值。
- `mcp.py:688-699`：`_declared_protocol_versions` 只接受客户端额外传入的 `protocolVersions` 数组。
- `mcp.py:702-727`：`_negotiate_protocol_version`
  - `requested in SUPPORTED_PROTOCOL_VERSIONS` → 返回 requested；
  - 否则仅当 `declared` 里出现受支持版本才降级（`:714-716`）；
  - 否则抛出 `INVALID_PARAMS` / `unsupported_protocol_version`（`:717-726`）。
- `mcp.py:1049-1057`：`_initialize` 只读取 `protocolVersion` / `protocolVersions`，
  **完全不读取** `params["capabilities"]`；返回固定的 `supportedProtocolVersions`。

**直接复现（函数级）**

```text
$ PYTHONPATH=src /usr/bin/python3.11 - <<'EOF'
from acp_services.entrypoints.mcp import _negotiate_protocol_version, McpProtocolError
for declared in ((), ("2025-06-18",)):
    try: print(declared, "=>", _negotiate_protocol_version("2025-11-25", declared))
    except McpProtocolError as exc: print(declared, "=> ERROR", exc.code, exc.data)
EOF
() => ERROR -32602 {'code': 'unsupported_protocol_version', 'supported': ['2025-06-18'], 'locked': '2025-06-18', 'requested': '2025-11-25', 'client_versions': []}
('2025-06-18',) => 2025-06-18
```

**真实 DSH 插件依赖复现（新证据，替代/补充 sdk-probe.json）**

```text
$ cd /ACPServer/AutoCalcPlatform && C=/ACPServer/acp2-tools/deepseek-harness/node_modules/.pnpm/@modelcontextprotocol+client@2.2.0/node_modules/@modelcontextprotocol/client
$ /ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node --input-type=module -e "
const {Client}=await import('file://$C/dist/index.mjs');
const {StdioClientTransport}=await import('file://$C/dist/stdio.mjs');
const t=new StdioClientTransport({command:'/usr/bin/python3.11',args:['reports/acp2_delivery_gap_audit_20261026/probe_python.py','--stdio'],stderr:'pipe'});
const seen=[]; const orig=t.send.bind(t); t.send=async m=>{seen.push(m); return orig(m)};
const c=new Client({name:'dsh-mcp-client',version:'0.2.1-rc.2'},{capabilities:{},versionNegotiation:{mode:'auto'}});
try { await c.connect(t) } catch(e){ console.log(JSON.stringify({code:e.code,message:e.message})) }
for (const m of seen) console.log('SENT', JSON.stringify(m));
"
{"code":-32602,"message":"unsupported protocol version '2025-11-25'"}
SENT {"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"dsh-mcp-client","version":"0.2.1-rc.2"}},"jsonrpc":"2.0","id":0}
```

说明：按源码，`@modelcontextprotocol/client@2.2.0` 的 `versionNegotiation:auto` 会先尝试
`server/discover`（ACP 返回 `METHOD_NOT_FOUND` 后判定为 legacy），再走 legacy
`initialize`；legacy 分支发送 `legacyVersions[0]`，即 `2025-11-25`
（`@modelcontextprotocol/client@2.2.0/dist/index.mjs:3335-3345`）。
`LATEST_PROTOCOL_VERSION = "2025-11-25"`，`SUPPORTED_PROTOCOL_VERSIONS` 同时包含
`2025-06-18`（本次 Node 导入打印）。因此 ACP 只要按规范返回 `2025-06-18`，
这个真实客户端会接受：`_legacyHandshake` 用
`legacyVersions.includes(result.protocolVersion)` 校验。

**测试 pin 住了“拒绝”行为**

- `tests/test_services_mcp.py:242-245`：
  `test_initialize_rejects_other_protocol_version` 断言 `unsupported_protocol_version`。
- `tests/test_acp2_runtime_mcp_process.py:574-586`：
  “different-supported” 行必须额外传 `declared=["2025-03-26", "2025-06-18"]` 才能协商；
- `tests/test_acp2_runtime_mcp_process.py:633-642`：
  矩阵断言 `different-supported` 成功、`no-common` 返回 `unsupported_protocol_version`；
- `tests/test_acp2_runtime_mcp_process.py:240-254`：测试辅助的 `handshake()` 只有在测试显式传
  `declared` 时才把 `protocolVersions` 放进请求。

**文档已记录该风险，但未关闭**

- `docs/reference/next-change/ACP_Services_MCP_Client_Matrix.md:18`：
  “same version → same; client `protocolVersion` unsupported but `protocolVersions` includes a
  supported one → negotiated common version (recorded); no common version → `-32602`”。
- 同文件 `:33`：`dsh-official-mcp-client` 状态是 `NOT_VERIFIED (H2 gate)`。
- `docs/reference/next-change/acp2-contracts/experience/mcp-handshake.md:126-128`：
  真实客户端 `@deepseek-ai/dsh-mcp-client 0.2.1-rc.2`、`@modelcontextprotocol/client 2.2.0`，
  `LATEST_PROTOCOL_VERSION 2025-11-25`，**不发送** `protocolVersions`。
- 同文件 `:138-145`：明确写出“expected live outcome is initialize → -32602 ... **static expectation**”。
- `docs/reference/next-change/acp2-contracts/experience/requests/B-req-18.md:50-62`：
  C 于 2026-10-22 把 R1 作为阻塞项提交给 B，状态 `open`。
- `docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md:67`：
  已写明“当前‘其他版本一律返回错误’与新版客户端的组合需要实际验证。若修改服务器协商行为，
  更新 ADR-006、协议矩阵、兼容测试和版本支持声明”。

**规范原文（2025-06-18 Lifecycle / Version Negotiation）**

> If the server supports the requested protocol version, it **MUST** respond with the same
> version. Otherwise, the server **MUST** respond with another protocol version it supports.
> This **SHOULD** be the *latest* version supported by the server.
> If the client does not support the version in the server's response, it **SHOULD** disconnect.

来源：<https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle>
（同页 Error Handling 另有一个 `-32602 Unsupported protocol version` 示例。）

### 根因链

1. **技术根因**：服务端把“协议版本协商”实现成“单版本白名单 + 客户端额外声明列表”的私有协议；
   `initialize` 不读取客户端 `capabilities`，也不会按规范返回自己支持的版本，而是直接 `-32602`。
   锁定 DSH 客户端默认先发 `2025-11-25`，且不发送 `protocolVersions`，故必然失败。

2. **设计/流程根因**：
   - B08-2（`58992ed54ad6efbf5d7fc709933e73fdfa61b7b5`，2026-10-19，
     “feat(services-machine): MCP thin entry (allowlist/identity/budget/idempotency)”）
     首先把协议锁成单一 `2025-06-18`，并对其他版本直接抛 `-32602`。
   - B05（`e4c9d723d3c717738c16d3beef347814e6457829`，2026-10-22 22:00，
     “feat(mcp): add real stdio transport and acp mcp serve”）增加了
     **非标准** `protocolVersions` 回退，并在 docstring 写下
     “never echoed back silently”——这是有意的“显式声明才降级”设计，但把
     “客户端未枚举”误当成“没有共同版本”。
   - 真实客户端握手（H2 gate）被划给 C，且长期以 `NOT_VERIFIED` 记录；
     代码注释和计划要求修改协议时同步的 **ADR-006 在仓库中没有实体文件**
     （只有多处引用，`find docs -iname "*adr*"` 无 ADR-006）。因此“改协议要过 ADR”的
     流程实际上没有可执行的记录载体。
   - 测试只覆盖服务端自定义矩阵（same / declared-different / no-common），
     没有“真实客户端默认握手”用例，导致单版本锁定通过了全部本地测试。

---

## G02 P2 研究契约与实际服务是两套接口

- **验证结论: 确认**（8 工具、无条件 1.1.0/negotiated、10 个研究工具缺失、ControllerClient 缺方法，全部复核成立）

### 报告原声明中不准确处

1. **“冻结契约声明研究工具可用”不是统一状态**。`p2-research-operations.v1.json` 的 5 行里：
   - `P2-R2` / `P2-R3` 是 `implementation_status: "delivered"`，但 `delivered_by: B16` 只交了
     HTTP 审批路由（`http_paths`），没有交 MCP 工具；
   - `P2-R1` / `P2-R4` / `P2-R5` 是 `"planned"`。
   报告应写成“契约规定协商后应可见/可调，且绑定规则要求 live tool + ControllerClient + use case
   同时交付；实际全部缺失”，而不是“契约已声明全部可用”。

2. **版本冲突不只是“服务端 1.1.0 vs Bridge 1.0.0”，B 自己的契约也互相矛盾**：
   - `application-api.v1.json:3` 的 `contract_version` 是 `1.1.0`；
   - 同文件 `:2651-2659` 的 `mcp_extensions["acp.research.v1"].version` 写成 `1.1.0`；
   - `p2-research-operations.v1.json:13-18` 的 `negotiation.extension_version` 是 `1.0.0`；
   - `ACP_2.0_Parallel_Execution_Index_20261020.md:293-294` 明确写
     “扩展 `acp.research.v1`，八工具 allowlist 不变；wire `contract_version` 仍 `1.0.0`”。
   即 B06 把“整个 application-api 的 contract_version 1.1.0”误当成“research extension 的版本”，
   产生了 B 内部的不一致。Bridge 只是按 B03 的 1.0.0 实现，撞上了这个漂移。

3. **“扩展元数据还需经真实 SDK 的结果解析保留”这一句缺乏依据**。
   `@modelcontextprotocol/sdk@1.31.0` 的 `ResultSchema` 是 `z.looseObject`
   （`.../sdk/dist/esm/types.js:100`），`InitializeResultSchema` 继承它；我们实测 parse 后
   `extensions` 仍在。`@modelcontextprotocol/client@2.2.0` 的 `ResultSchema$1` 同样是
   `z.looseObject`（`.../client/dist/src-xKF0PJN-.mjs:627`）。真正的问题是：
   - DSH 的 `connection.ts` 只使用 `getInstructions()` 和 tools，**从未读取 `extensions`**
     （全文件 grep 无 `extensions`）；
   - Bridge 的 `negotiatedExtensions` 是构造参数
     （`rph/packages/bridge/src/index.ts:1077` 的 `BridgeOptions`，`:1164` 的
     `this.negotiated = options.negotiatedExtensions ?? []`）；全仓 grep 只有这两处，
     没有任何生产组合根把协商结果传进来（见 G05）。
   所以应改为“SDK schema 能保留，但 DSH 插件没有把协商结果暴露给 Bridge，且没有组合根注入”。

4. **行号**：`mcp.py:940` 是 `__init__` 内的 `self._identity`，实际注册表在 `:945-954`；
   `mcp.py:1049` 是 `_initialize` 起始，`1.1.0` 在 `:1060-1065`。

5. **`node-probes.json` 的 scope 是 “real RPH helpers with controlled fake ports; no model,
   Controller or QC called”**，即版本拒绝是真实 Bridge helper 的行为，但不是完整
   RPH/DSH 会话；报告应避免把它表述成端到端客户端验证。

### 证据

**服务端只注册 8 个工具**

- `src/acp_services/entrypoints/mcp.py:219-239`：`McpTool.ALL` 恰好 8 个名字：
  `acp_capabilities`, `acp_preflight`, `acp_submit`, `acp_status`, `acp_cancel`,
  `acp_prepare_artifact`, `acp_read_artifact`, `acp_analyze`。
- `mcp.py:945-954`：`_tool_dispatch` 字面量表只有这 8 项。
- `mcp.py:1081-1082`：`_tools_list` 永远返回 `_tool_specs()` 过滤后的这 8 项，
  **没有按协商扩展过滤**。
- `mcp.py:1084-1097`：`tools/call` 未命中即 `unknown_tool`，并返回 8 项 allowlist。

**初始化无条件宣告 1.1.0 / negotiated=true**

```text
mcp.py:1049-1065
requested = params.get("protocolVersion")
declared  = _declared_protocol_versions(params.get("protocolVersions"))
negotiated = _negotiate_protocol_version(...)
return {
  "protocolVersion": negotiated,
  "supportedProtocolVersions": [...],
  ...
  "extensions": {
    _RESEARCH_EXTENSION_ID: {
      "id": _RESEARCH_EXTENSION_ID,
      "version": "1.1.0",
      "negotiated": True,
    },
    ...
  },
}
```

`_RESEARCH_EXTENSION_ID` 在 `mcp.py:155-157` 用
`chr(97) + "cp.research.v1"` 拼出来，注释直言是“assembled so the isolation scan sees a call,
not a bare module-looking string constant”；这使 `grep -rn "acp.research" src/` 在 `mcp.py`
里只能找到注释，看不到真实 wire 常量，降低了可审计性。

另外 `src/acp/services_mcp_bootstrap.py:144-160` 的 `_ControllerCapabilities.capabilities()`
在 Controller 响应缺 `extensions` 时又硬编码了同一组
`[{"id": "acp.research.v1", "version": "1.1.0", "negotiated": True}]`
（`:156-159`），所以 `acp_capabilities` 也会重复宣告。

**真实运行观测（live，2026-10-26）**

```text
$ PYTHONPATH=src /usr/bin/python3.11 ... build_mcp_runtime("http://127.0.0.1:8765")
initialize(protocolVersion=2025-06-18, capabilities={})
extensions:
{"acp.research.v1":{"id":"acp.research.v1","version":"1.1.0","negotiated":true},
 "acp.identity.v1":{...}}
acp_capabilities.extensions:
[{"id":"acp.research.v1","version":"1.1.0","negotiated":true}]
tools/list: 只有 8 个 P1 工具

tools/call acp_research_proposal_create_v1
→ {"code":"unknown_tool","allowlist":[8 个 P1 工具]}
```

即使客户端显式传
`capabilities.extensions={"acp.research.v1":{"version":"1.0.0"}}`，`initialize` 返回的
`extensions` 仍是 `1.1.0 / negotiated=true`，研究工具仍不可见/不可调。

**冻结契约声明的 10 个研究工具与版本**

`docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json`：

- 扩展 id `acp.research.v1`，`negotiation.extension_version = "1.0.0"`（`:13-18`），
  协商参数 `capabilities.extensions`（`:14`），未协商拒绝
  `unsupported_capability/extension_not_negotiated`（`:17`）；
  `extension_rule`（`:12`）明确 “P2 tools are advertised only when the client negotiates
  the extension capability in the MCP initialize params”。
- 研究工具（5 行共 10 个 distinct 名称）：
  1. `acp_research_proposal_create_v1`（R1，`:49`）
  2. `acp_research_proposal_read_v1`（R1，`:50`）
  3. `acp_research_proposal_update_cas_v1`（R1，`:51`）
  4. `acp_research_approval_request_v1`（R2，`:68`）
  5. `acp_research_approval_query_v1`（R2，`:69`）
  6. `acp_research_approval_decide_v1`（R3，`:88`）
  7. `acp_research_approval_revoke_v1`（R3，`:89`）
  8. `acp_research_wait_v1`（R4，`:108`）
  9. `acp_research_snapshot_v1`（R4，`:109`）
  10. `acp_research_resume_v1`（R5，`:126`）
- `live_tool_requirement`（如 `:60`）：“visible in tools/list after acp.research.v1
  negotiation and callable through tools/call; a Data interface alone is not a live DSH tool”。
- `binding_ownership.rule`（`:146-149`）：“the binding owner ships the live tool, the
  ControllerClient method and the application use case together; a Data interface delivered
  without a live tool is not closure of R3-02”。

**Bridge 按 1.0.0 调用，服务端 1.1.0 被拒**

- `rph/packages/bridge/src/index.ts:397-398`：
  `BRIDGE_EXTENSION_ID = "acp.research.v1"`、`BRIDGE_EXTENSION_VERSION = "1.0.0"`。
- `rph/packages/bridge/src/index.ts:492`：P2 工具 `toolVersion: BRIDGE_EXTENSION_VERSION`。
- `rph/packages/bridge/src/index.ts:683-687`：协商版本不等于 `1.0.0` 时
  `extension_version_mismatch`。
- 直接调用 dist 复现：

```text
assertOperationAllowed('proposal.create', [{id:'acp.research.v1', version:'1.0.0'}])
→ ALLOWED
assertOperationAllowed('proposal.create', [{id:'acp.research.v1', version:'1.1.0'}])
→ throws BridgeContractError (message "unsupported:unsupported_capability"), e.error =
   {"class":"unsupported","code":"unsupported_capability",
    "params":{"rule":"extension_version_mismatch","extension":"acp.research.v1",
              "expected_version":"1.0.0","observed_version":"1.1.0"}}
```

**ControllerClient 没有研究方法**

`src/acp_services/entrypoints/controller_client.py:444-680` 的公开方法只有
`capabilities/health/identity/status/list_tasks/preflight/submit/cancel/control/operation/
prepare/read/analyze/recover`，没有
`create_proposal/read_proposal/update_proposal_cas/request_approval/query_approval/
decide_approval/revoke_approval/wait_research/snapshot_research/resume_research`。
`grep -rn "acp_research_\|proposal_create" src/ --include=*.py` 只命中
`src/acp/api/v2_research.py:201` 的 HTTP 只读路由，没有任何 MCP 工具实现。

**测试只测“契约结构”和“声明”，没测工具存在**

- `tests/test_acp2_runtime_contracts.py:881-884`：
  `test_application_api_v1_1_research_fields_are_additive` 只断言
  `extension["version"] == "1.1.0"`、`negotiated_in == "initialize"`、
  `p1_tool_allowlist_unchanged is True`。
- `tests/test_acp2_runtime_contracts.py:504-539`：
  `test_p2_research_operations_are_complete_and_owned` 只检查 5 行、10 个工具名符合
  `^acp_research_[a-z0-9_]+_v[0-9]+$`、owner 在 B16/B18、错误类合法；不执行 `tools/list`。
- `grep -c "extensions" tests/test_services_mcp.py tests/test_acp2_runtime_mcp_process.py`
  → `0` / `0`：MCP 单元/进程测试完全不涉及扩展声明。
- `scripts/acceptance/validate_acp2_contracts.py` 是结构/引用校验器（文件头自述
  “structural/reference validator, not a JSON-Schema engine”），只校验 manifest 结构、
  digest、依赖图和 stage 归属；没有跨 `application-api.mcp_extensions.version` 与
  `p2-research-operations.negotiation.extension_version` 的一致性检查。

### 根因链

1. **技术根因**：服务端在 `initialize` 中硬编码“研究扩展 1.1.0、已协商”，
   但 `_tool_dispatch` / `_tools_list` / `ControllerClient` 都没有对应实现；
   `_initialize` 也从不读取客户端的 `capabilities.extensions`，所谓协商是无条件的。

2. **设计/流程根因**：
   - B03 冻结 P2 契约：`acp.research.v1@1.0.0`、10 个工具、`capabilities.extensions` 协商、
     “Data interface alone is not a live DSH tool”。
   - B06（`f67f90fa72bb0cfbe01b58650debbbedbffe1b38`，2026-10-22 22:57，
     “feat(mcp): bind mcp tools to the single controller application surface”）
     同时交付了 `mcp_initialize_extension` 与 `application_api_v1_1_0`
     （`manifest.json` 的 `b06.covers`，:691-700）：把
     `application-api.contract_version` 从 `1.0.0` 升到 `1.1.0`，并在 `mcp.py` 写入
     `extensions: {acp.research.v1: 1.1.0, negotiated: True}`——**把整体契约版本
     当成了扩展版本**，且没有实现任何 P2 工具。
   - B16（`e5463de61001343d20e2a0befb7288c0515619f3`，2026-10-23，
     “feat(runtime): add persistent proposal approvals”）主要交付
     `src/acp/api/v2_approvals.py` 与 `acp_services/runtime/approvals.py`；
     B18（`938e894342d643f75740289a0e7aa84ef071115f`，2026-10-23，
     “feat(runtime): bind science recipes and evidence”）主要交付
     `ResearchResourceStore` 与 science/evidence 绑定。两者都没有加 ControllerClient
     方法或 MCP 工具。
   - `manifest.json` 仍把 `p2-research-operations` 标成 `status: delivered` /
     `updated_by: B16`，但契约自己的 `binding_ownership.rule` 明确“没有 live tool 不算关闭”。
     交付台账与验收口径脱节。
   - 测试只做 contract-only 断言，且把 1.1.0 这个错误版本 pin 进了
     `test_application_api_v1_1_research_fields_are_additive`；没有“协商后工具集合 == 契约工具集合”
     的集成断言，所以 8 工具测试全绿。

---

## G03 Bridge 基础结果消费参数无法通过服务端

- **验证结论: 确认**（三种操作的真实 MCP 调用均复现报告中的三条错误，且桥接侧还有独立的丢参问题）

### 报告原声明中不准确处

1. **“Bridge 仍使用旧资源定位模型，Data 已迁移到 ArtifactRef”这个框架不够准确**。
   - `control-and-data.v1.json:522` 仍规定
     “task_id + attempt + artifact_id + generation + digest is the only verifiable artifact reference”；
     同文件 `:524-545` 的 `artifact_ref` 字段仍是
     `schema/version/job_id/attempt/resource_id/generation/digest`；
   - `src/acp_services/data/artifacts.py:250-254` 的 `ArtifactRef` 也仍是
     `job_id/attempt/resource_id/generation/digest`。
   - `src/acp_services/entrypoints/controller_client.py:109-137` 的 wire `ArtifactRef`
     是 `task_id/attempt/artifact_id/generation/digest`；`read`（`:605-654`）对
     `artifact_ref` 的 `attempt/generation/digest` 逐项校验，缺一即 `input_invalid`。
   所以 `resource_id` 并未被“废弃”；真正的变化是 **B06 引入了新的请求信封**：
   prepare 必须 `purpose`（artifact / structure-input），artifact 模式还要 `artifact_id`；
   read 必须嵌套 `artifact_ref`（task_id/artifact_id/attempt/generation/digest）。
   Bridge 是对着 **B06 之前的 advertised `_ARTIFACT_SCHEMA`** 写的，而不是对着一个已经过时的
   “资源定位模型”写的。

2. **报告说“evidence 操作丢弃必填的 task/attempt/resource”，属实，但 Bridge 自身更矛盾**：
   `OPERATION_FIELDS` 明确 `evidence` 需要 `task+attempt+resource`
   （`rph/packages/bridge/src/index.ts:55-66`），`missingFieldsFor` 会强制校验；
   但 `buildToolArguments` 的 `evidence` 分支只转发 submit 字段 + `explicit`
   （`:1005-1028`），**从不把 task/attempt/resource 放进参数**。
   同理，`read` 的字段矩阵要求 `generation`（`:61-66`），但 `buildToolArguments` 的
   read 分支只放 `job_id/attempt/resource_id`（`:1039-1044`），`generation` 被丢弃。
   这意味着即便服务端愿意接受旧字段，Bridge 也构造不出完整的旧式 read 请求。

3. **行号**：报告写 `src/acp/services_mcp_bootstrap.py:264` 起是适配链；
   实际 `_ControllerArtifacts.prepare` 在 `:268`，`read` 在 `:289`，`analyze` 在 `:304`（差几行，不影响结论）。

### 证据

**Bridge 的参数构造**

`rph/packages/bridge/src/index.ts:1005-1044`：

```ts
case "validate":
case "submit":
case "evidence":
  put("workflow", request.workflow);
  put("input", request.input);
  put("method", request.method);
  put("project_id", request.project);
  put("resources", request.resources);
  put("idempotency_key", request.idempotencyKey);
  if (request.operation === "evidence") put("explicit", request.explicit);
  return Object.freeze(args);
case "prepare":
  put("job_id", request.task);
  put("attempt", request.attempt);
  put("resource_id", request.resource);
  put("idempotency_key", request.idempotencyKey);
  return Object.freeze(args);
case "read":
  put("job_id", request.task);
  put("attempt", request.attempt);
  put("resource_id", request.resource);
  return Object.freeze(args);
```

直接跑 dist：

```text
$ node --input-type=module -e "... buildToolArguments ..."
read {task:'job-1',attempt:2,generation:5,resource:'freq.out'}
→ missing: []        // 说明 Bridge 认为参数完整
→ args: {"job_id":"job-1","attempt":2,"resource_id":"freq.out"}   // generation 丢了

evidence {task:'job-1',attempt:2,resource:'freq.out',project:'p',explicit:true,workflow:'nmr',input:{},method:{}}
→ missing: []
→ args: {"workflow":"nmr","input":{},"method":{},"project_id":"p","explicit":true}  // task/attempt/resource 丢了
```

**服务端 normalizer 的必填字段**

- `src/acp_services/entrypoints/application_params.py:231-308` `normalize_prepare`：
  `purpose` 必须是 `artifact|structure-input`（`:235-244`）；
  `purpose == "artifact"` 时要求 `task_ref` 或 `job_id`（`:297-302`）且 `artifact_id`
  必填（`:303-307`）。
- `application_params.py:311-328` `normalize_read`：
  `artifact_ref` 必须是 mapping（`:315-319`），没有就 `artifact_ref is required`。
- `application_params.py:331-344` `normalize_analyze`：
  `explicit` 必须为 `True`；`analysis_id` 必填；`input_refs` 必须是非空数组。
- 适配链：`src/acp/services_mcp_bootstrap.py:268-314` 的
  `_ControllerArtifacts.prepare/read`、`_ControllerAnalysis.analyze` 直接调用上述 normalizer；
  MCP handler 只是转发 `arguments`（`mcp.py:1285-1377`）。

**直接复现（normalizer 级，与报告表完全一致）**

```text
$ PYTHONPATH=src /usr/bin/python3.11 - <<'EOF'
from acp_services.entrypoints.application_params import normalize_prepare, normalize_read, normalize_analyze
...
prepare(bridge)            => REJECTED input_invalid purpose must be artifact or structure-input
                              {'field':'purpose','rule':'enum','allowed':['artifact','structure-input']}
read(bridge)               => REJECTED input_invalid artifact_ref is required
                              {'field':'artifact_ref','rule':'required'}
analyze(evidence/bridge)   => REJECTED input_invalid analysis_id is required
                              {'field':'analysis_id','rule':'required'}
prepare(minimal purpose=artifact) => ACCEPTED
                              {'schema_version':1,'purpose':'artifact','job_id':'j','artifact_id':'a'}
```

**真实 MCP 调用复现（live，2026-10-26）**

```text
prepare acp_prepare_artifact {job_id:'audit-job',attempt:1,resource_id:'result_manifest'}
→ ('error','input_invalid','purpose must be artifact or structure-input')
read acp_read_artifact {job_id:'audit-job',attempt:1,resource_id:'result_manifest'}
→ ('error','input_invalid','artifact_ref is required')
analyze acp_analyze {workflow:'nmr',input:{},method:{},project_id:'audit-project',explicit:true}
→ ('error','input_invalid','analysis_id is required')
```

三条都在 normalizer 处失败，未向 Controller 发出 prepare/read/analyze 请求。

**MCP `tools/list` 的 schema 仍展示旧字段 / analyze 复用 submit schema**

- `src/acp_services/entrypoints/mcp.py:355-365` `_ARTIFACT_SCHEMA` 的属性是
  `job_id/resource_id/relative_path/attempt/idempotency_key`，没有
  `purpose`、`artifact_id`、`artifact_ref`；`additionalProperties: true`。
- `mcp.py:395-404`：`acp_prepare_artifact` 和 `acp_read_artifact` 共用这个 schema。
- `mcp.py:405-412`：`acp_analyze` 的 schema 是
  `{**_SUBMIT_SCHEMA, properties: {..._SUBMIT_SCHEMA["properties"], explicit: boolean}}`，
  即复用 submit schema；没有 `analysis_id`/`input_refs`，`required` 仍是 `["workflow"]`。
- 因此模型仅凭 `tools/list` 无法学到服务端真正要求的字段；而服务端 `tools/call`
  也不做 schema 校验，直接进入 normalizer 才报错。

`evidence/python-probes.json#tool_schemas` 的 live 结果与源码一致：

```json
acp_prepare_artifact properties: {"job_id","resource_id","relative_path","attempt","idempotency_key"}
acp_read_artifact   properties: {"job_id","resource_id","relative_path","attempt","idempotency_key"}
acp_analyze         inputSchema  = _SUBMIT_SCHEMA + {"explicit": boolean}
```

**跨轨请求与证据已明确记录该漂移**

- `docs/reference/next-change/acp2-contracts/experience/requests/B-req-18.md:70-82` R2：
  “live `acp_read_artifact` = `job_id/resource_id/...`；the descriptor requires `artifact_ref`.
  All eight tools differ... Please state which surface is authoritative”。
  状态 `open`。
- `docs/reference/next-change/acp2-contracts/experience/mcp-handshake.md:173-197` Finding F1：
  “all eight advertised `inputSchema` values differ from the frozen descriptor”；
  `:392-399`：“Finding F1 — still OPEN; B must reconcile before H2”。
- `tests/test_acp2_runtime_mcp_backends.py:232-263` 服务端 prepare 测试用的是
  `{"purpose":"artifact","job_id":...,"artifact_id":"catalog"}`；
  `rph/test/bridge-contract.test.ts:451-459` 却把
  `{job_id, attempt, resource_id}` pin 成 Bridge 的 prepare 参数。
  两套测试各自绿，没有跨仓 parity 测试。
- B06 自己的 `parameter-parity.json` 只覆盖 `normalize_submit`（`reports/acp2_implementation/
  bb45a46/B/B06/parameter-parity.json` 的 `normalizer` 字段），没有 prepare/read/analyze。

### 根因链

1. **技术根因**：`tools/list` 暴露的是 2026-10-19 的旧 schema，而 `tools/call` 在
   B06 之后走的是新的 normalizer / 请求信封；声明与执行不一致，且运行时不校验 schema。

2. **设计/流程根因**：
   - B08-2（`58992ed54ad6efbf5d7fc709933e73fdfa61b7b5`，2026-10-19）写下
     `_ARTIFACT_SCHEMA = {job_id, resource_id, relative_path, attempt, idempotency_key}`。
   - C 在 `c54823109010b651de3f0cca28da5af54a10fdfb`（2026-10-22 19:39）冻结
     `experience/tool-semantics.md`，其开头明说 “Ground truth is the shipped adapter ...
     at the frozen C baseline c548231”，`:179` 明确 artifact 请求携带
     `job_id, resource_id, attempt, relative_path`。Bridge 随后在
     `5e01758376ecde96fd5bfa3bc8b6379b0e23951b`（2026-10-22 22:45）按这个 baseline 实现。
   - B06 `f67f90fa72bb0cfbe01b58650debbbedbffe1b38`（2026-10-22 22:57，比 Bridge 晚 **12 分钟**）
     新增 `application_params.py`，把 prepare/read/analyze 的请求信封改成
     `purpose/artifact_id/artifact_ref/analysis_id/input_refs`，但没有同步
     `_tool_specs` / `_ARTIFACT_SCHEMA`，也没有更新 C 的消费合同。
   - C 随后在 B-req-18 R2 / Finding F1 里报告“all eight tools differ”，但该请求一直 `open`；
     B06 的验收只做 submit parity，RPH 与 ACP 之间没有跨仓参数一致性测试。
   - 最终形成“服务端测试用新信封、Bridge 测试用旧信封、两边都绿”的典型契约漂移。

---

## G04 MCP 发现的能力与可执行分析不完整

- **验证结论: 确认**（`capability_descriptors` 为 null、分析 registry 无注册者、MCP 无 v2 science 消费链，均已复核）

### 报告原声明中不准确处

1. **行号是装饰器/函数起始，不是语句位置**：
   - 报告 `src/acp/api/routes.py:273` 实际是 `@router.get("/capabilities")` 装饰器；
     `capability_descriptors: None` 在 `:301`。
   - 报告 `src/acp/api/v1_routes.py:8889` 实际是
     `@router.post("/jobs/{job_id}/analyze")` 装饰器；
     `def analyze_job` 在 `:8893`，`DEFAULT_ANALYSIS_REGISTRY.get` 在 `:8928`。
   - `analysis_capabilities.py:106` 正确（`DEFAULT_ANALYSIS_REGISTRY` 定义）。
   建议报告统一写成“路由装饰器行 + 实际语句行”。

2. **“capability_descriptors 固定为 None”应写成“契约声明为 A-owned、由 B discovery 转发，
   但 B 端点没有转发”**。`application-api.v1.json:767-775` 明确：
   `capability_descriptors` 类型 `array|null`，描述
   “A-owned descriptors forwarded verbatim by B discovery; only the ui_schema_subset
   keywords may appear”。所以这不是一个有意的永久 `None`，而是一个未实现的转发。

3. **“未找到生产注册者”可以更强**：全仓（含 `tests/`）都**没有任何**
   `register_analysis_capability(...)` 或 `AnalysisCapability(...)` 调用；
   现有测试不是“注册者”，而是把 `unsupported_capability` 当作预期行为 pin 住
   （`tests/test_acp2_runtime_mcp_backends.py:267-301`）。因此不是“生产没接”，
   而是“连测试也没有可执行 handler”。

4. **“capabilities 端点只投影 legacy workflow”不完整**：它还投影
   `task_kinds`、`backends`、`limits`、`tools`、`admission`、`identity*`；
   缺少的是科学 descriptors，不是“只有 workflow”。

### 证据

**`/api/capabilities` 的 `capability_descriptors` 为 null**

- `src/acp/api/routes.py:273-325` `get_capabilities`；
  `:296-315` 构造 payload；`:301` 明确 `"capability_descriptors": None`。
- 该行由 `21610bc1b071d58f367cd04c4f65edb4d2153a5b`（2026-10-22 21:14，
  “feat(cli): add controller-targeted task client and topology”，B04a）引入；
  diff 中直接写了 `+ "capability_descriptors": None`。
- 契约要求见 `application-api.v1.json:767-775`（A-owned descriptors forwarded by B discovery）。

**MCP 只消费 `/api/capabilities`**

- `src/acp/services_mcp_bootstrap.py:144-160` `_ControllerCapabilities.capabilities()`
  调用 `ControllerClient.capabilities()`；
- `src/acp_services/entrypoints/controller_client.py:444-450` 固定
  `GET /api/capabilities`；
- `src/acp_services/entrypoints/mcp.py:1143-1154` `_tool_capabilities` 直接
  `dict(self._backends.capabilities.capabilities())` 并补 `protocol_version/tools`。
- `grep -rn "science" src/acp/services_mcp_bootstrap.py src/acp_services/entrypoints/mcp.py`
  只命中 `mcp.py:377` 的英文描述 “no science, no metering”，**没有任何**
  `/api/v2/science/capabilities` 消费链。

**live 观测（2026-10-26）**

```text
$ curl -sS http://127.0.0.1:8765/api/capabilities
capability_descriptors = None
tools = [8 个 P1 工具]
workflows = 15 个 active workflow

$ PYTHONPATH=src ... build_mcp_runtime("http://127.0.0.1:8765")
initialize(protocolVersion=2025-06-18)
tools/call acp_capabilities
capability_descriptors: None
workflows count: 15
extensions: [{"id":"acp.research.v1","version":"1.1.0","negotiated":true}]

$ curl -sS http://127.0.0.1:8765/api/v2/science/capabilities
# 对返回 JSON 做本地字段统计：
top keys: ['protocol_version', 'tools', 'capabilities']
n capabilities: 4
ids: ['electronic.energy', 'geometry.optimize', 'vibration.analyze', 'vibration.compute']
question ids: ['single_point_energy', 'geometry_optimization', 'frequency_analysis']
```

即：较完整的科学描述在 v2 science 路由（`src/acp/api/v2_science.py:229-248`，
投影在 `src/acp/api/science_projection.py:427-450`），但 MCP 的 `acp_capabilities`
拿不到它。注意 `science_projection.py:34` 反过来 import 了
`acp_services.entrypoints.mcp.TOOL_ALLOWLIST` 来填 `tools`，但 MCP 侧没有对应的反向消费；
这是单向引用，不构成发现链。

**默认分析 registry 为空，且无注册者**

- `src/acp_services/runtime/analysis_capabilities.py:1-23` 文档：
  “Registry-gated explicit analysis capabilities (B06 shell; A/B12 register) ... until a
  science owner registers an analysis handler, the route returns unsupported_capability
  and runs nothing.”
- `:106` `DEFAULT_ANALYSIS_REGISTRY = AnalysisCapabilityRegistry()`；
  `:109-111` 的 `register_analysis_capability` 是唯一写入口。
- 全仓检索：

```text
$ grep -rn "register_analysis_capability\|AnalysisCapabilityRegistry(" --include=*.py . | grep -v __pycache__
src/acp_services/runtime/analysis_capabilities.py:30:    "register_analysis_capability",
src/acp_services/runtime/analysis_capabilities.py:106:DEFAULT_ANALYSIS_REGISTRY = AnalysisCapabilityRegistry()
src/acp_services/runtime/analysis_capabilities.py:109:def register_analysis_capability(...):
```

没有任何调用者（`src/` 与 `tests/` 都没有）。

- analyze 路由：

```text
src/acp/api/v1_routes.py:8893 def analyze_job
src/acp/api/v1_routes.py:8903-8907 from acp_services.runtime.analysis_capabilities import ANALYSIS_CAPABILITY_IDS, DEFAULT_ANALYSIS_REGISTRY
src/acp/api/v1_routes.py:8928 capability = DEFAULT_ANALYSIS_REGISTRY.get(req.analysis_id)
src/acp/api/v1_routes.py:8929-8944 if capability is None or capability.handler is None:
                                raise 422 unsupported_capability
```

- 探针 `evidence/python-probes.json#analysis_registry_ids` 为 `[]`。
- `tests/test_acp2_runtime_mcp_backends.py:267-301`
  `test_analyze_requires_explicit_and_is_registry_gated` 明确断言：
  正确的 `analysis_id/input_refs/explicit` 也得到
  `structuredContent.error.code == "unsupported_capability"`，且任务数/prepare 数不变。
  这等于把“分析不可执行”写成了通过条件。

**MCP 进程测试 fixture 也把 null 当默认**

- `tests/test_acp2_runtime_mcp_process.py:80-95` 的 fake `/api/capabilities`
  返回 `"capability_descriptors": None`；测试只断言 tools/task count，不断言 descriptors。

### 根因链

1. **技术根因**：MCP 发现绑定的是 B04a 的最小 `/api/capabilities`，该端点把
   `capability_descriptors` 留成 `None`；A 的科学能力目录只通过 2026-10-25 新增的
   `/api/v2/science/capabilities` 暴露，MCP 从未改绑或合并；分析 registry 是注入式空壳，
   没有组合根注册 handler，因此 `acp_analyze` 永远 422 `unsupported_capability`。

2. **设计/流程根因**：
   - B04a（`21610bc1b071d58f367cd04c4f65edb4d2153a5b`，2026-10-22 21:14）
     的任务定位就是 “minimal capabilities / controller-targeted task client”
     （plan B 的 P0 表：`Controller + minimal capabilities`，
     `docs/reference/next-change/acp2-contracts/runtime/final-debug-patch/new-files/.omo/plans/acp2-parallel-plan-b-runtime.md:119`），
     `None` 是一个合法的 placeholder；但后续没有“把 A descriptors 转发进 B discovery”的
     交付项被真正执行。
   - A 的科学能力 registry 在 `5fc5c52`（2026-10-22 21:33，
     “feat(cccp): add read-only scientific capability registry”）与
     `f6bedcb` / `29e8b3c`（同日晚些时候）已经存在，早于 B06（22:57），但 B06 没有接。
   - B06（`f67f90fa...`，2026-10-22 22:57）把 MCP 工具绑到 Controller，同时创建
     `analysis_capabilities.py`，文档写明 “B06 shell; A/B12 register”，
     但没有任何 owner 在组合根里注册；`v1_routes.py` 的 analyze 路由与空 registry
     同时上线，测试直接把 `unsupported_capability` pin 成预期。
   - 较完整的 `/api/v2/science/capabilities` 直到
     `ec4a0b39b26645139c01aa9578fbedd0c3db23a2`（2026-10-25，
     “fix(acp2): final acceptance remediation — read-only science/research routes + defect fixes”）
     才加入；此时 MCP 早已绑定旧的 `/api/capabilities`，没有回补消费链。
   - 因此 `acp_capabilities` 的“8 工具 + 15 workflow + backends”看起来像完整发现，
     但科学 descriptors 与可执行分析状态都不在 MCP 视野内。

---

## 附录：本次 live / 进程内探针的精确命令

以下命令均为只读；`build_mcp_runtime` 只连接已运行的 Controller（`127.0.0.1:8765`），
`probe_python.py --stdio` 使用 `NoEffects` 端口，不会执行 QC 或创建任务。

```bash
# 1) initialize 扩展 + tools/list + acp_capabilities（G02/G04）
cd /ACPServer/AutoCalcPlatform
PYTHONPATH=src /usr/bin/python3.11 - <<'PY'
import json
from acp.services_mcp_bootstrap import build_mcp_runtime
rt = build_mcp_runtime("http://127.0.0.1:8765", timeout=5)
s = rt.server
init = s.handle({"jsonrpc":"2.0","id":1,"method":"initialize",
                 "params":{"protocolVersion":"2025-06-18","capabilities":{}}})
print(json.dumps(init["result"]["extensions"], ensure_ascii=False))
call = s.handle({"jsonrpc":"2.0","id":2,"method":"tools/call",
                 "params":{"name":"acp_capabilities","arguments":{}}})
data = call["result"]["structuredContent"]["data"]
print("capability_descriptors:", data.get("capability_descriptors"))
print("tools:", data.get("tools"))
print("workflows:", len(data.get("workflows") or []))
PY

# 2) Bridge 形状参数在真实 MCP 调用中的三条错误（G03）
PYTHONPATH=src /usr/bin/python3.11 - <<'PY'
import json
from acp.services_mcp_bootstrap import build_mcp_runtime
s = build_mcp_runtime("http://127.0.0.1:8765", timeout=5).server
s.handle({"jsonrpc":"2.0","id":1,"method":"initialize",
          "params":{"protocolVersion":"2025-06-18"}})
def call(name, args):
    r = s.handle({"jsonrpc":"2.0","id":9,"method":"tools/call",
                  "params":{"name":name,"arguments":args}})
    sc = r["result"]["structuredContent"]
    return sc.get("status"), (sc.get("error") or {}).get("code"), (sc.get("error") or {}).get("message")
print("prepare:", call("acp_prepare_artifact", {"job_id":"audit-job","attempt":1,"resource_id":"result_manifest"}))
print("read:", call("acp_read_artifact", {"job_id":"audit-job","attempt":1,"resource_id":"result_manifest"}))
print("analyze:", call("acp_analyze", {"workflow":"nmr","input":{},"method":{},"project_id":"audit-project","explicit":True}))
PY

# 3) 研究工具 unknown_tool（G02）
PYTHONPATH=src /usr/bin/python3.11 - <<'PY'
from acp.services_mcp_bootstrap import build_mcp_runtime
s = build_mcp_runtime("http://127.0.0.1:8765", timeout=5).server
s.handle({"jsonrpc":"2.0","id":1,"method":"initialize",
          "params":{"protocolVersion":"2025-06-18",
                    "capabilities":{"extensions":{"acp.research.v1":{"version":"1.0.0"}}}}})
r = s.handle({"jsonrpc":"2.0","id":3,"method":"tools/call",
              "params":{"name":"acp_research_proposal_create_v1","arguments":{}}})
print(r)
PY

# 4) actual DSH client dependency handshake（G01）
C=/ACPServer/acp2-tools/deepseek-harness/node_modules/.pnpm/@modelcontextprotocol+client@2.2.0/node_modules/@modelcontextprotocol/client
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node --input-type=module -e "
const {Client}=await import('file://$C/dist/index.mjs');
const {StdioClientTransport}=await import('file://$C/dist/stdio.mjs');
const t=new StdioClientTransport({command:'/usr/bin/python3.11',
  args:['reports/acp2_delivery_gap_audit_20261026/probe_python.py','--stdio'],stderr:'pipe'});
const seen=[]; const orig=t.send.bind(t); t.send=async m=>{seen.push(m); return orig(m)};
const c=new Client({name:'dsh-mcp-client',version:'0.2.1-rc.2'},
  {capabilities:{},versionNegotiation:{mode:'auto'}});
try { await c.connect(t) } catch(e){ console.log(JSON.stringify({code:e.code,message:e.message})) }
for (const m of seen) console.log('SENT', JSON.stringify(m));
try{await c.close()}catch{}
"

# 5) 只读 HTTP 观测（G04）
curl -sS http://127.0.0.1:8765/api/capabilities
curl -sS http://127.0.0.1:8765/api/v2/science/capabilities
```

---

## 对报告的修正建议

1. **G01 证据链换包并补规范细读**：
   - 把 `evidence/sdk-probe.json` 的 `sdk_client` 从
     `@modelcontextprotocol/sdk@1.31.0` 改为/补充
     `@modelcontextprotocol/client@2.2.0`（DSH `connection.ts:18` 实际 import 的包）；
     我们的复现已证明它同样发送 `2025-11-25` 且不发送 `protocolVersions`，收到同样的 `-32602`。
   - 规范引用改成“Version Negotiation 规范段要求 MUST 返回另一受支持版本；同页 Error Handling
     仍给出 -32602 示例”，避免把规范说成绝对禁止报错。
   - 明确 `protocolVersions` 是 ACP 私有字段；建议在关闭条件中加一条：
     “服务端按规范返回 `2025-06-18` 后，未修改的 `@modelcontextprotocol/client@2.2.0`
     默认 `versionNegotiation:auto` 能完成 initialize 并列出 8 工具”。
   - 补一句：ADR-006 目前不存在实体文件（仅被引用），修复时应先创建 ADR-006，而不是“更新 ADR-006”。

2. **G02 拆成“B 内部契约矛盾”与“服务端宣告未实现扩展”两层**：
   - 补 `application-api.mcp_extensions.version=1.1.0` vs
     `p2-research-operations.negotiation.extension_version=1.0.0` vs
     `Execution Index` 的 `wire contract_version 1.0.0` 三处冲突；
   - 补 `p2` 行的 `implementation_status`：R2/R3 只是 HTTP delivered，R1/R4/R5 planned；
     报告不要笼统写成“契约已声明可用”。
   - 把“扩展元数据经真实 SDK 解析保留”改为：
     “SDK 1.31.0 / client 2.2.0 的 ResultSchema 都是 `z.looseObject`，`extensions` 能保留；
     真正缺的是 DSH `connection.ts` 从不读取 `extensions`，且没有组合根把
     `negotiatedExtensions` 注入 Bridge（G05）。”
   - 关闭条件建议加机器可验的两条：
     (a) `initialize(capabilities.extensions=[{id:'acp.research.v1',version:'1.0.0'}])` 后
     `tools/list` 的 name 集合 == p2 契约的 10 个研究工具 ∪ 8 个 P1；
     (b) 每个研究工具经 `tools/call` 至少到达一个存在的 ControllerClient 方法
     （mock/真实 Controller 均可），未协商客户端只看到 8 个。

3. **G03 把根因从“旧模型 vs 新模型”改写为“B06 请求信封变更未同步 advertised schema”**：
   - 引用 `control-and-data.v1.json:522-545` 说明 `resource_id` 仍是 ArtifactRef 的一部分，
     真正新增的是 `purpose/artifact_id` 与嵌套 `artifact_ref`；
   - 补 Bridge 自身的丢参：read 丢 `generation`、evidence 丢 `task/attempt/resource`
     （我们已用 dist 复现），这独立于服务端迁移；
   - 引用 B-req-18 R2 与 `mcp-handshake.md` Finding F1（均 OPEN）说明 C 已报告、
     B 未修复；补 B06 `parameter-parity.json` 只覆盖 submit 的事实；
   - 关闭条件加“tools/list 的 inputSchema 必须由与 normalizer 同源的请求契约生成/校验，
     且跨仓测试直接拿 Bridge `buildToolArguments` 的输出喂真实 McpServer”。

4. **G04 修正行号并强化“无注册者”结论**：
   - `routes.py:273` → 装饰器；实际 `None` 在 `:301`；
     `v1_routes.py:8889` → 装饰器；`analyze_job` 在 `:8893`、registry get 在 `:8928`；
   - 说明 `capability_descriptors` 是契约要求的 A-owned 转发字段（`application-api:767-775`），
     不是设计上永久为 `None`；
   - 补 live 对比：`/api/capabilities` = `capability_descriptors:null`，
     `/api/v2/science/capabilities` = 4 capabilities / 3 questions，MCP 不消费后者；
   - 补“全仓零注册者（含 tests）”与
     `test_acp2_runtime_mcp_backends.py:267-301` 主动 pin `unsupported_capability`；
   - 关闭条件建议加：至少一个真实 handler 注册后，`acp_capabilities` 的 descriptors 非 null
     且 `executable` 状态与 `acp_analyze` 实际可执行性一致；未注册能力必须显式
     `unavailable`，不得只靠工具名暗示可用。

5. **跨项建议（对应 G20 的测试缺口）**：
   - 增加一个跨轨 MCP 验收脚本，固定使用锁定 DSH 的
     `@modelcontextprotocol/client@2.2.0` 与 `versionNegotiation:{mode:'auto'}`：
     initialize → tools/list → 逐工具调用（或 Controller mock）→ 对比 p2 契约工具集；
   - 增加一个参数同源测试：服务端 `_tool_specs().inputSchema`、`application_params`
     normalizer、Bridge `buildToolArguments` 三者对同一请求产生一致字段；
   - 这四个 Gap 的共同特征是“单侧测试全绿、跨侧契约无断言”；不补跨侧测试，
     仅改文档/版本号仍会复发。
