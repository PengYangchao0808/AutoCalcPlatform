# ACP2 第三轮差距真实性核验与遗漏项

日期：2026-10-26。源码基线：`ec4a0b39b26645139c01aa9578fbedd0c3db23a2`。核验输入：用户修订的 `GAP_REPORT.md` 和第二轮四份 lane 记录；输入摘要保存在 [baseline.json](evidence/verification-r3-20261026/baseline.json)。

**结论：完整 ACP2 仍不能交付。原报告的主要断链判断成立，但“20 项全部确认”不能解释为每一句根因和影响都已证实。本轮新增 G21–G26 六项可复现问题，其中 MCP 虚假提交成功与远程执行约束丢失直接影响现有提交入口；其余涉及客户端幂等、用户隔离、撤销语义和可信回执。**

本轮没有修改业务代码、启动付费模型、执行 QC/LSF、访问生产 Controller 或重启服务。修改仅限审计目录。HTTP 证据来自临时目录中的真实 ASGI Controller、身份系统、JobManager 和 SQLite 账本，科学提交线程被禁用；MCP 的 HTTP 传输用 ASGI TestClient 替代。它证明路由与持久化行为，不证明真实计算执行结果。

## 1. 新发现总表

严重度沿用原报告：S0 阻断目标交付，S1 高优先级；R 表示本轮复现。表中有条件触发的问题均在下文列出条件。

| ID | 级别 | 事实 | 影响范围 | 主要证据 |
|---|---|---|---|---|
| G21 | S1/R | Bridge 的幂等缓存跨操作、跨 task/attempt/resource 复用 | 接入 Bridge 后，validate 可吞掉 submit；evidence 可返回其他任务结果 | `bridge.json` |
| G22 | S0/R | MCP prepare/submit 共享无动作区分的缓存；重复 submit 丢 TaskRef | 当前 MCP 可返回成功而未提交任务；重放返回契约退化 | `mcp.json` |
| G23 | S0/R | MCP normalizer 丢弃执行目标等 HTTP 支持的字段 | 远程节点限制被静默移除；不同入口产生不同执行意图 | `mcp.json`、`http.json` |
| G24 | S1/R | HTTP 提交幂等范围固定使用 `LOCAL_PRINCIPAL_ID` | 同项目不同认证用户的幂等键冲突，第二用户拿到第一用户任务 | `http.json` |
| G25 | S1/R | MCP 缓存命中绕过 Controller 的最新凭据校验 | 撤销 API key 后，既有会话的缓存请求仍返回成功 | `http.json` |
| G26 | S1/R | 普通项目写凭据可自称 `worker-receipt` 写入虚构用量 | 科学用量证据和预算 reservation 状态可被非 worker 写入 | `http.json` |

这些不是原 G01/G02 的简单重复：即使握手和工具名全部修好，以下提交、身份、回执问题仍然存在。G21 当前受 G05 未装配遮蔽；G22–G26 位于已经存在的服务端入口或适配器中。

## 2. 新发现、触发条件和关闭标准

### G21：Bridge 的缓存身份不包含操作或证据归属

**驻点。** [Bridge](../../rph/packages/bridge/src/index.ts)：`isIdempotentOperation`（1132）、`execute`（1169）、`signatureFor`（1263）、`replayIdempotentResult`（1278）。缓存以 `idempotencyKey` 为键，签名仅含 workflow/input/method/resources/project。validate、submit、evidence 共用这张表。

**本轮复现。** 使用重新编译的真实 Bridge，注入只记录调用的 PlatformClient：

1. 同一 project/input/key 先 validate，再 submit：实际只调用一次 `acp_preflight`。第二次返回的 `operation` 仍是 `validate`，结果与第一次是同一个对象，完全没有 `acp_submit` 调用。
2. 相同 key/project 的 evidence 请求从 `job-a/attempt=1/evidence` 换为 `job-b/attempt=2/different-evidence`：仅调用第一次 `acp_analyze`，第二次直接重放。
3. 对照：validate 与 submit 使用不同 key 时，两次工具调用均发生。

**边界。** 触发需要在同一个 Bridge 实例复用 key。未证明当前未装配的 RPH 已在生产自动使用这组键，也未证明真实科学结果已经串用。即使契约要求不同动作使用不同 key，错误复用也应明确冲突，不能把另一动作的成功作为本动作的成功。

**关闭标准。** 幂等身份包括动作和完整目标身份；validate→submit 不得返回预检作为提交结果；更改 task/attempt/resource 必须拒绝或正确隔离；同动作同内容重试仍只调用一次。测试必须经过真实 Bridge 的缓存分支。

证据：[probe_bridge.mjs](evidence/verification-r3-20261026/probe_bridge.mjs)、[bridge.json](evidence/verification-r3-20261026/bridge.json)。

### G22：MCP 的跨工具缓存能制造未执行的提交成功

**驻点。** [mcp.py](../../src/acp_services/entrypoints/mcp.py)：`_tool_submit`（1171）、`_tool_prepare_artifact`（1288）、`_resolve_idempotency`（1381）。缓存范围含 workspace/permission scope 和原始 key，却不包含 tool/action。prepare 写入裸 operation reference；submit 对不带 `payload_digest` 的缓存直接返回成功。

**本轮复现。** 使用真实 `McpServer.handle → tools/call`，后端为记录调用的受控端口：

- `acp_prepare_artifact(key=K)` 后调用 `acp_submit(key=K)`：返回 `isError=false`、`idempotent_replay=true`，reference.kind 为 **operation**；submit 后端调用数为 **0**。
- prepare 保持 key，改变 job/resource/attempt：仍返回第一次 operation，后端只接到第一次请求；没有内容冲突检查。
- 正常 submit 重试：首次包含 task_ref，缓存重放只剩 reference 和 idempotent_replay，task_ref 消失。
- 对照：prepare/submit 使用不同 key，submit 后端正常调用一次。

**边界。** 前两个反例使用同一 MCP 进程内缓存，并且显式复用 key。它们在 Controller normalizer/持久幂等之前短路，因此 Controller 自身更严格的实现无法兜底。本轮未将受控后端声称为真实 QC。这里的缺陷是适配层返回假成功，不是任务重复执行的实证。

**关闭标准。** MCP 不得成为第二个含不同规则的幂等权威；若保留缓存，动作、内容摘要和完整返回信封必须一致，并服从 Controller 授权。跨操作键复用应隔离或报冲突；重复提交必须保留相同 TaskRef/attempt 与必要身份字段。

证据：[probe_mcp.py](evidence/verification-r3-20261026/probe_mcp.py)、[mcp.json](evidence/verification-r3-20261026/mcp.json)。

### G23：MCP 丢弃远程执行约束，HTTP 与 MCP 行为不等价

**驻点。** [application_params.py](../../src/acp_services/entrypoints/application_params.py)：`_common_submit_fields`（173）、`normalize_submit`（212）；[services_mcp_bootstrap.py](../../src/acp/services_mcp_bootstrap.py)：`_ControllerSubmit.submit`（182）。它们重新构造请求，却没有保留 `execution_mode`、`target_node`、`node_tags`、`name`、`config_path`、`molecule_name`、`task_name`、`remark` 等字段。

HTTP 的 [V1JobCreateRequest](../../src/acp/api/v1_schemas.py)（338）接受这些字段，[create_job](../../src/acp/api/v1_routes.py)（2593–2628）使用它们构造和验证 JobSpec。HTTP 调用 normalizer 校验后仍使用原请求；MCP 则发送删字段后的新请求。

**本轮复现。** 对同一隔离 Controller、相同凭据，提交 `execution_mode=remote, target_node=r3-nonexistent-node`：

| 入口 | 实际结果 |
|---|---|
| 直接 HTTP POST `/api/v1/jobs` | HTTP 400，`unknown_target_node` |
| 真实 MCP `_ControllerSubmit` → 同一 HTTP 路由 | 创建任务成功；持久化 JobSpec 的 `execution_mode=None, target_node=None` |

另用记录端口确认上述八个字段确实在发送前被丢弃。不存在的节点是故意设置的对照，不是机器环境故障。

**边界。** 科学执行线程禁用，因此没有声称任务实际落到本地机器。已经证明“必须远程/指定节点”的限制变成自动选择。若 MCP 首期不支持节点选择，也必须在输入处显式拒绝，不能接受后静默删除。

**关闭标准。** 同一逻辑请求在 HTTP/CLI/MCP 产生相同有效 JobSpec 与相同拒绝结果；明确支持字段和拒绝字段，增加非法节点、合法远程节点、node_tags、配置与命名参数的跨入口测试。

证据：`mcp.json.controller_adapter_drops_fields`、[http.json](evidence/verification-r3-20261026/http.json) 的 `remote_target_http_vs_mcp`。

### G24：不同用户被放进相同的提交幂等范围

**驻点。** [v1_routes.py](../../src/acp/api/v1_routes.py)（2634–2646）用 `resolve_scope_id(LOCAL_PRINCIPAL_ID, scope_project)` 创建幂等上下文。与此同时，[idempotency.py](../../src/acp_services/runtime/idempotency.py)（236）明确约定范围来自认证后的 principal + project。

**本轮复现。** 临时身份库中创建 Alice/Bob 两个不同账户，分别持有同项目的 write key；`/api/identity` 确认 principal 不同。二者以相同 payload/key 提交，均成功，返回完全相同 job_id；Bob 的结果为 `idempotent_replay=true`。使用真实 JobManager 和持久 ledger，未替代幂等实现。

**边界。** 双方均有同一项目的写权限；这不是跨项目越权证据，也没有证实 Bob 获得本来禁止读取的数据。已证实的是用户级命令身份被错误合并。local-single 环境不能覆盖这个多用户缺陷。

**关闭标准。** 由当前请求的认证上下文生成 principal+project+action 范围；同一用户重试复用，不同用户或项目互不吞掉提交；HTTP、MCP 和 CLI 使用一致规则。若产品有意改为项目共享幂等，必须明确变更契约，并处理调用者归属，不能继续宣称 principal 级隔离。

证据：`http.json.two_principals_same_project`。

### G25：撤销 API key 后，MCP 缓存仍可返回成功

**驻点。** [services_mcp_bootstrap.py](../../src/acp/services_mcp_bootstrap.py)（378）只在创建 mapper 时调用一次 `client.identity()`；[mcp.py](../../src/acp_services/entrypoints/mcp.py)（1108–1139）按已绑定 actions 授权，命中提交缓存后不再访问 Controller。

**本轮复现。** 通过真实 Controller 绑定身份并完成一次隔离提交后，在临时身份库撤销 key：

- `/api/identity` 立即返回 401。
- 既有 MCP server 重放相同 submit/key：`isError=false`，Controller 新调用数为 0。
- 同一 MCP server 使用新 key 提交：实际访问 Controller，返回 `isError=true`。

**边界。** 缓存仅重放客户端此前取得的引用，没有创建新的未授权任务，也没有读取新的工件。本轮只验证 API key 撤销，不外推到账户撤销、角色变更等所有场景。问题在于服务端对“撤销后请求应被拒绝”的语义不一致。

**关闭标准。** 活跃会话在缓存返回前仍遵守凭据/权限有效性；撤销后缓存和非缓存路径一致拒绝。必须验证不增加 backend 副作用，而不是只在启动时检查身份。

证据：`http.json.revoked_key_cached_submit`。与 G22 的修复相关，但独立的权限验收不能省略。

### G26：可信用量来源由普通调用方自报

**驻点。** [v2_budgets.py](../../src/acp/api/v2_budgets.py)（98–137）仅要求项目写权限，然后检查客户端的 `trusted_source` 是否属于字符串白名单。默认值就是 `worker-receipt`；没有验证调用者是否 worker、是否持有该 reservation 的回执权限，或是否有匹配的真实执行回执。[admission.py](../../src/acp_services/runtime/admission.py)（1741–1818）直接持久化并把 reservation 改为 settled。

**本轮复现。** 在真实 ledger 中预置一个限额为 1 TaskRef 的预算和已 dispatched 的 reservation；普通 `member` 的项目 write key 提交：

```json
{"receipt_id":"arbitrary-client-receipt","reservation_id":"bres_r3_receipt",
 "trusted_source":"worker-receipt","usage":{"qc_invocation":999}}
```

实际 HTTP 200、`applied=true`；账本回执计数为 `qc_invocation=999`，reservation 为 `settled`。没有任何 QC 调用。

**重要反证。** 本轮最初怀疑空结算可以释放 TaskRef 额度；探针否定了它。`_budget_consumed_in`（1524）仍计入所有非 released reservation，伪结算后下一次 TaskRef 预留依然 `admitted=false`。因此本报告**不认定 task_count 上限被绕过，不认定所有预算失效**。问题限定为可信回执来源、用量证据和结算状态可被错误写入。

**关闭标准。** worker/controller/proxy 来源必须由服务端认证及可核对的执行身份确定，普通项目 write 不能声明自己为 worker；回执绑定 reservation/job/attempt/执行来源，重复回执保持幂等；虚构用量和任意来源标签必须拒绝。保留并继续验证现有 TaskRef 额度保护。

证据：`http.json.client_asserted_trusted_receipt`；被反证的初始假设记录在 [http-budget-hypothesis.log](evidence/verification-r3-20261026/http-budget-hypothesis.log)。

## 3. 对修订稿的真实性校正

以下校正不抹去核心差距，但应优先于原报告及第二轮 lane 中的扩大表述。

| 项目 | 本轮核验后的准确表述 |
|---|---|
| G01 依赖归属 | G01 正文仍把 `sdk@1.31.0` 写成 DSH 实际依赖，与后面的第二轮纠正冲突。本轮从插件 package.json 所在位置解析 `@modelcontextprotocol/client`，得到 **2.2.0**，实际握手仍为 -32602。新版证据见 `dsh.json`；旧探针只能说明另一个 SDK 的兼容性。 |
| G01 标准判断 | 以官方 Version Negotiation 条款为据：服务端不支持请求版本时返回其支持的版本，由客户端决定是否接受。同页也有不兼容初始化错误示例，不能写成“任何版本错误均不得拒绝”。本例客户端支持服务端版本，失败与 ACP 的额外列表要求有关。 |
| G02 交付范围 | P2 契约内部分行本就标 planned，另有 delivered HTTP 行。应写“完整研究 MCP 缺失且版本/协商声明不一致”，不能把十个工具都说成早已承诺 delivered。 |
| G07 证据模型数量 | 四种 evidence 形状共存本身不是缺陷；真正失败证据是合法 CCCP 输出不能被当前 RPH validator 消费，且没有已装配的权威转换。 |
| G11 用户可见行为 | 已证实真实模块函数能制造本地 approved/submitted；但 `mount` 未找到产品调用，不能宣称已观察到生产用户点击得到假成功。更准确的是审批产品动作缺少接线，fixture 函数存在危险的成功语义。 |
| G11/G20 无 fetch 检查 | 模块禁止直接 fetch/XHR 是合理分层约束，**不禁止通过注入适配器调用平台**。静态测试通过不能证明接线存在，但“禁止 fetch 导致永远无法接线”不成立。缺的是适配器及其端到端验收。 |
| G12 身份枚举 | `PrincipalKind` 没有 agent 不能单独证明系统无法实施 Agent 审批策略；现有 service principal、credential scope 或独立权限也能承载。当前缺陷是入口没有相应强制策略，不能把“新增 agent enum”当唯一修复。 |
| G13 模型预算 | model_request 的 recorded-only 与 model-token enforcement 的 NOT_VERIFIED 描述的是不同维度，不构成逻辑矛盾。解析错误和 limits={} 已本轮复现；未另外证明 core_hours/walltime_s 的真实 HTTP 500，仍限定为解析失败及路径风险。 |
| G15 执行层 | 生产 worker 装配存在，既有 ACP 也有批量执行能力；缺的是 **bounded Recipe DAG** 的生产编译/构造/派发入口，不能扩大为 ACP 没有任何 worker/多步执行。 |
| G18 live 标签 | 内外 evidence_kind 不一致需要修正；外层结果仍是 NOT_VERIFIED，不是“离线结果伪造 live PASS”。已排期但 blocked 的占位仍然阻断交付，不过不能称完全未排期。 |
| G19 无 ref 的提交 | `abec2bc` 没有 ref 包含，但对象仍存在。本轮 `git show` 和 `git ls-tree` 均成功，可按 SHA 读取/检出。应写“缺持久 ref 的保存与移交风险”，撤回“因此源码不可复现”的强推论。 |
| G19 多个 SHA | 历史目录有 43 个候选值不自动构成缺陷；source/base/landed_head 不同也可以合理。问题应落在最终签收 BOM、版本映射、证据路径和摘要能否闭合，不能以数量或 SHA 不同替代证明。 |

协议依据：[MCP 2025-06-18 Lifecycle — Version Negotiation](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle#version-negotiation)。本轮已重新读取该官方页面。

## 4. 原 G01–G20 的逐项结论

“仍开放”表示当前证据不足以关闭该差距，不表示该项每个假设都已复现。R=本轮直接复现，S=源码/调用点/契约核对，H/N=历史证据或未做本轮现场认证。

| ID | 本轮判断 | 本轮依据与限制 |
|---|---|---|
| G01 | R，确认握手失败 | 插件实际依赖 2.2.0 → 真实 ACP stdio；没有运行完整 DSH Agent |
| G02 | R/S，仍开放 | 真实 tools/list 为 8；缺 10 个 P2 工具；Bridge 拒绝 1.1.0；注意 planned/delivered 区别 |
| G03 | R，确认参数不兼容 | 重新编译的 Bridge 输出送入真实 normalizer，prepare/read/evidence 全部失败；另补 G23 |
| G04 | R/S，仍开放 | 默认 analysis registry 为空；`register_analysis_capability` 在 src/tests 未找到调用；不否认独立 science API 的存在 |
| G05 | S，仍开放 | RPH packages 中找到 Supervisor 定义而无启动调用，CLI 仍 help/version/profile；未认证仓库外部署装配 |
| G06 | S，仍开放 | web profile 保持机器绝对路径、验收端口与配置目录；这是可移植发行缺口 |
| G07 | R，确认消费失败 | CCCP 正例被其自身 parser 接受，RPH validator 返回 20 个结构问题 |
| G08 | R，确认反例被接受 | 真实 Supervisor 对 foreign project/research/task/attempt/release 事件/报告返回 accepted；端口受控，不是生产事故 |
| G09 | R/S，仍开放 | 第一次 wait 无事件即 wait_timeout；恢复/持久化合同缺口仍在；未做真实长任务 |
| G10 | R/S，仍开放 | 带电双重态输入问题得到默认 0/1 计划和 round 摘要；说明默认离线规划器，不代表真实模型曾误判 |
| G11 | R/S，仍开放 | 无平台端口的模块可生成 submitted；产品 mount/写调用未找到，按上一节收窄影响 |
| G12 | R/S，仍开放 | budget-only 被判 research 但 approval=None；没有据此宣称全部平台配额已被绕过 |
| G13 | R/S，仍开放 | task_count 映射可用，另两种应用单位抛错；RPH budget 得 limits={}；新增 G26 可信来源问题 |
| G14 | S，仍开放 | 研究读取和存储不等于用户产品 writer；现有客户端 readOnly/P2 不通，未发现可启动写入闭环 |
| G15 | S，仍开放 | src 中 DAG executor/CCCP dispatch 仅定义与内部引用，无生产装配调用；不泛化到既有 batch |
| G16 | S/N，未关闭 | 现有科学切片与通用插件驱动表单的差距仍在；未补真实浏览器验证，不据静态检查声称浏览器全失败 |
| G17 | S/H/N，未关闭 | RPH CLI/package 仍占位，未补四组分独立安装；private/无 bin 单独不是“任何方式都无法发行”的证明 |
| G18 | S/H/N，未关闭 | control harness 的 live 模型/Controller 状态仍写死 False；最终 NOT_VERIFIED；本轮未补模型/QC/LSF 证据 |
| G19 | S/H，需保留但收窄 | 提交对象可读，历史多 SHA 可合理；最终证据移交和签收映射仍需独立核验 |
| G20 | R/S，确认覆盖盲点 | 新反例成立时，MCP/身份/预算原有 69 项仍全绿；不能据此贬低已有集成测试或声称全仓无集成门 |

原反例本轮重跑结果：[node-probes.json](evidence/verification-r3-20261026/node-probes.json)、[python-probes.json](evidence/verification-r3-20261026/python-probes.json)。附加契约和静态检查：[budget-contract.json](evidence/verification-r3-20261026/budget-contract.json)、[static-checks.json](evidence/verification-r3-20261026/static-checks.json)。

## 5. 验证记录与可复现边界

| 检查 | 本轮结果 | 可证明范围 |
|---|---|---|
| RPH TypeScript build | 通过 | 新 Bridge 探针使用当前编译产物 |
| Bridge 定向反例和不同 key 对照 | 通过断言 | 缓存实际返回与调用次数 |
| MCP dispatcher/Controller adapter 定向反例 | 通过断言 | 适配器行为；后端端口受控 |
| 临时 ASGI Controller 四组反例 | 通过断言 | 真身份、路由、持久幂等、任务记录、预算 ledger；禁用科学执行 |
| 插件解析依赖 → ACP stdio | -32602，符合缺陷反例断言 | 未更改客户端首选版本，未启动 Controller/模型 |
| 原审计核心反例重跑 | 与原结果一致 | 参数/证据/默认规划/等待/fixture 函数边界 |
| 原有 MCP/多用户/预算定向 pytest | **69 passed，2 warnings，11.62s** | [regression.xml](evidence/verification-r3-20261026/regression.xml) 与 [regression.log](evidence/verification-r3-20261026/regression.log)；不是全量回归 |

“探针断言通过”表示成功复现缺陷，不能读成产品验收通过。此前报告中的 299/80/444 测试计数属于此前执行，本轮没有把它们重新计入。

ASGI 探针在沙箱启动处挂起，终止后按获准的沙箱外方式执行成功。早期探针读取 adapter 返回字段错误，以及被反证的预算假设，均是审计过程记录，不计为产品测试失败。最终 `http.json` 对应完成全部四组断言的 `http-final.log`。

复现脚本位于 [verification-r3-20261026](evidence/verification-r3-20261026/)。从仓库根执行：

```bash
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node rph/node_modules/typescript/bin/tsc -b rph --pretty false
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node reports/acp2_delivery_gap_audit_20261026/evidence/verification-r3-20261026/probe_bridge.mjs
/usr/bin/python3.11 reports/acp2_delivery_gap_audit_20261026/evidence/verification-r3-20261026/probe_mcp.py
/usr/bin/python3.11 reports/acp2_delivery_gap_audit_20261026/evidence/verification-r3-20261026/probe_http.py
/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node reports/acp2_delivery_gap_audit_20261026/evidence/verification-r3-20261026/probe_dsh.mjs
```

`probe_http.py` 会创建临时身份/任务/账本并清理，不能将其改成生产地址后直接使用。`probe_dsh.mjs` 依赖本机已锁定 DSH checkout，仅证明该实际依赖兼容性。源码摘要在 [source-sha256.json](evidence/verification-r3-20261026/source-sha256.json)；证据清单在 [SHA256SUMS](evidence/verification-r3-20261026/SHA256SUMS)。

## 6. 阻塞关闭顺序的调整

原报告先统一 MCP/证据契约、再装配产品的总体方向保留，但不能只修“连不上”和“工具缺失”。

1. **把 G21–G25 纳入同一提交与身份集成门。** 默认 DSH 握手成功之后，至少验证 validate→submit、跨工具相同 key、重试返回同一完整 TaskRef、同项目不同用户、撤销后的命中/未命中缓存，以及 remote 约束不丢失。MCP 缓存不得越过 Controller 的身份与持久幂等权威。
2. **把 G26 纳入可信执行回执门。** 先保证 usage 的来源可认证，再谈扩大预算维度；保留现有 TaskRef/attempt 防超限和 CAS 保护。
3. **继续关闭原产品断链。** P2 writer/审批、Supervisor 装配、EvidenceReport 归属、长任务恢复和 bounded DAG 都不能由以上局部修复代替。
4. **最终再做真实纵向切片与发行认证。** 对固定 BOM 运行 DSH→Controller→Local/LSF→工件→科学证据→Workbench，验证完整权限/恢复/预算路径。当前报告没有给缩小范围的产品或生产实例放行。

本轮核验发现了更多确定问题，也推翻了一个预算绕过假设并收窄了若干旧表述。它提供下一轮整改的可执行反例集，不宣称阻塞项已穷尽。
