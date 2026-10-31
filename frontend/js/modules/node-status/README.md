# node-status — ACP 3.0 节点 / 软件发现模块（A-30 / A-31）

- 文件：
  - `frontend/js/modules/node-status/module.js`（classic script，无构建、无 ESM）
  - `frontend/css/modules/node-status.css`（仅消费 `frontend/css/workbench/base.css` / `shell/shell.css` 的变量）
  - `frontend/js/module_tests/acp2_node_status.test.js`（vm + fake DOM + 注入 adapter，网络零直连）
- 注册名：`window.ACPNextModules["node-status"]`（唯一新增全局；不写页面全局、不建第二套缓存）
- 对应计划：[ACP 3.0 前端计划](../../../../docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md)
  §5.V5 A-30/A-31（`P10 系统与资源`）；验收要点：A-30「失败态明确」、A-31「不探测即显示"未知"」

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["node-status"] = {
  descriptor: {
    id: "node-status",
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["system.read"],
    capabilities: { requires: [], provides: ["system.read"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 0,
    error: { titleKey: "next.nodes.errorTitle", messageKey: "next.nodes.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ }
};
```

`ModuleInstance`：`mount(host, api) → Promise<void>`、`updateContext(snapshot)`、`refresh()`（附加方法，
等价于点击「刷新」）、`suspend()`、`resume()`、`dispose()`。

`deps`（全部可注入、可 mock）：

| 字段 | 内容 |
|---|---|
| `doc` | DOM document（缺省 `window.document`，测试注入 fake DOM） |
| `apiBase` | 缺省 `/api/v1`（6 个端点都是相对该前缀的既有 v1 路径） |
| `fetch` | fetch 风格 `(url, init) → Response`；也容忍返回已解析 body（请求式适配器） |
| `request` | 请求式 `(path, opts) → parsedBody`（既有模块同名注入缝） |
| `client.transport.request` | 页面/Shell 传输单点（`ACPClient.transport`） |
| `bootstrapTimeoutMs` | bootstrap 客户端预算，缺省 600000（与路由默认 `timeout=600` 对齐） |

解析优先级：`deps.fetch` → `deps.request` → `deps.client.transport.request` →
`window.ACPClient.transport.request`（页面兜底）。**全部缺失时模块渲染本地 error 态并明确说明，
不会退回 `window.fetch`，也不会发出任何请求**（测试 `without an injected adapter` 钉住）。

- `mount` 失败/非法宿主 → reject（TypeError）；内部请求失败 → 面板内局部 error 态 + 重试，不外泄异常。
- `mount` 返回 ≠ 数据就绪：初始请求 fire-and-forget，结果由四态渲染器落地。
- `updateContext` 保留为 ModuleInstance 形态对称：系统信息与项目/任务焦点无关，不触发重载。
- `dispose` 幂等且彻底：清 DOM、上下文/语言监听、bootstrap 计时器；之后可再次 mount（全新生命周期）。

## 2. 端点与响应形状（既有 v1 契约，未新增/未发明）

| 用途 | 请求 | 消费字段 |
|---|---|---|
| 节点列表 | `GET {API_BASE}/nodes` | `nodes[]`（`name/host/status/running_jobs/max_jobs/disk_usage_pct/last_check/error/queue/software/capability_state/declared_ok/mismatch/probe_note`）、`auto_select` |
| 单节点状态 | `GET {API_BASE}/nodes/{name}/status` | 同上单条模型；ping/bootstrap 之后 best-effort 刷新该行 |
| 连通测试 | `POST {API_BASE}/nodes/{name}/ping` | `reachable`、`node`、`status`、`error` |
| 引导安装 | `POST {API_BASE}/nodes/{name}/bootstrap` | `node/reachable/ok/exit_code/python_executable/requirements_path/sync_uploaded/sync_errors/stdout_tail/stderr_tail/error` |
| 软件发现 | `GET {API_BASE}/software/discovery` | `software[]`（`name/resolved/version/source/multiple/candidates[{path,version,source}]`） |
| 后端状态 | `GET {API_BASE}/backends` | `backends[]`（`name/available/path/version/capabilities[{name,available}]/remote_available/remote_nodes/remote_path/remote_version`） |

权威 schema：`src/acp/api/v1_schemas.py::NodeStatusModel / NodeListResponse / NodePingResponse /
NodeBootstrapResponse / V1SoftwareDiscoveryResponse`；后端列表：`src/acp/api/schemas.py::BackendsResponse`。
路由：`src/acp/api/v1_routes.py` 的 `/nodes*`、`/software/discovery`、`/backends`（v1 前缀下为同一 handler）。

- 响应解析容忍 **Response 形状**（`ok/status/json()/text()`）与 **已解析 body**，并按 FastAPI 约定
  提取 `detail`（字符串 / `{code,message}` / 422 `[{msg}]`）作为失败文案（测试钉住三种形状）。
- `POST` 只用于 ping/bootstrap 两条既有命令；**没有**任务提交、节点增删改、匹配预览等其它写操作
  （测试 `only ping/bootstrap POSTs are issued` 钉住调用集合）。
- bootstrap 通过传输的 `timeoutMs` 放宽到 600 s：页面传输默认 8 s，若沿用会把数分钟的 pip 安装
  提前中断；该值可用 `deps.bootstrapTimeoutMs` 覆盖，且不改变服务端 `timeout/sync` 默认。

## 3. 四态与失败语义

模块根节点 `data-acp-module-state` ∈ `empty | loading | ready | error`，另有
`data-nodes-count / data-backends-count / data-software-count`。

| 状态 | 触发条件 |
|---|---|
| `loading` | 首次加载进行中且尚无任何数据（刷新时保留已有数据，状态维持 ready/empty） |
| `ready` | 三个只读端点至少一个返回数据（局部失败在对应分区显示 `*.加载失败：…`，不降级整面板） |
| `empty` | 三个端点都成功但结果全为空；或节点端点 503 `Remote execution is not configured`（按"未配置远程节点"呈现，不是错误） |
| `error` | 至少一个分区失败且没有任何数据 → 面板 error 态（含服务端 detail）+ 重试按钮；未注入 adapter 时用专门的 `no_adapter` 文案且不显示无意义的重试 |

- 每个分区都有独立 note：加载失败 / 未配置 / 空 / 汇总（可用后端数、已发现软件数、自动选择开关）。
- 单节点操作失败文案与状态机：
  - ping 返回 `reachable:false` → `不可达：<error>`，并把该行状态改为响应里的 `status`；
  - ping 请求本身失败 → `连通测试失败：<detail>`；
  - bootstrap `reachable:false` → `节点不可达，引导未执行`；`ok:true` → `引导完成（exit N）`；
    `ok:false`＋`error` → `引导失败：<error>`；否则 `引导失败：退出码 N`；
  - bootstrap 失败时把 `stderr_tail`（无则 `stdout_tail`）放进 `<details>` 尾部，便于排查。
  - busy 期间两个按钮都禁用（同一节点同时只允许一条命令），bootstrap 期间按钮显示
    `引导中… Ns`（模块自有的 1 s 计时器，dispose/suspend/空闲即清）。

## 4. A-31「不猜」规则

- `version` / `path` / `source` 为空（`null`、`""`、未探测）时分别渲染 `未知` / `未发现可执行文件` /
  `来源未知`，**绝不**生成 `v0.0.0`、猜测路径或用 backend 的本地值填充远程值。
- 软件条目 `resolved:null` → `data-acp-availability="unknown"` + 徽标「未发现」；有 `resolved` → 「已发现」。
- `multiple:true` 时展开 `candidates[]`，仅用 `candidate.path === resolved` 判定「当前生效」，不重排、不排序。
- 后端 `available:false` → `data-acp-availability="unavailable"` + 「不可用」；capability 逐项渲染
  `data-available="true|false"`（支持/不支持），不从其它字段推断能力。
- 节点 `capability_state` 三态（`declared / probe-inferred / unknown`）原样映射为
  已声明 / 探测推断 / 能力未知；`declared_ok:false` 显示「声明与实际不一致」并展开 `mismatch[]`；
  `probe_note` 原文显示。节点 `software` 字典只列真实探测到的 `resolved` 值。
- 没有 fixture 模式：A-30/A-31 走的是系统只读端点，离线演示由测试注入的确定性 adapter 承担；
  若后续需要 `?view=fixture` 复用，必须按 A-00 约定加可见 fixture 徽标。

## 5. i18n（zh 内联 + data-i18n）

静态文案元素带 `data-i18n`（标题、副标题、分区标题、meta 标签），动态文案（带插值/错误消息）
只保留 zh 文本并随 `acp:langchange` 重绘——避免 `applyI18n` 用未插值的 key 覆盖运行时消息。
模块自带 `ZH` 兜底字典：Shell 字典缺 key 时 `t()` 回落本地文本（`ACPShell.i18n.t` 只回显 key）。

需要在 `frontend/js/shell/i18n.js` 补的 key（zh + en，共 76 条，zh 值与模块内联文本逐字一致）：

```
next.nodes.title
next.nodes.subtitle
next.nodes.refresh
next.nodes.refreshing
next.nodes.loading
next.nodes.empty
next.nodes.empty_hint
next.nodes.error
next.nodes.retry
next.nodes.errorTitle
next.nodes.errorMessage
next.nodes.no_adapter
next.nodes.section.nodes
next.nodes.section.backends
next.nodes.section.software
next.nodes.section_error
next.nodes.auto_select_on
next.nodes.auto_select_off
next.nodes.nodes_empty
next.nodes.backends_empty
next.nodes.software_empty
next.nodes.backend_summary
next.nodes.software_summary
next.nodes.not_configured
next.nodes.not_configured_hint
next.nodes.unnamed
next.nodes.unknown
next.nodes.version
next.nodes.path
next.nodes.source
next.nodes.meta_jobs
next.nodes.meta_disk
next.nodes.meta_queue
next.nodes.meta_checked
next.nodes.meta_capability
next.nodes.status.online
next.nodes.status.degraded
next.nodes.status.offline
next.nodes.status.unknown
next.nodes.capability.declared
next.nodes.capability.probe_inferred
next.nodes.capability.unknown
next.nodes.declared_ok
next.nodes.declared_mismatch
next.nodes.mismatch_list
next.nodes.probe_note
next.nodes.node_error
next.nodes.node_software
next.nodes.ping
next.nodes.pinging
next.nodes.ping_ok
next.nodes.ping_unreachable
next.nodes.ping_error
next.nodes.bootstrap
next.nodes.bootstrapping
next.nodes.bootstrap_ok
next.nodes.bootstrap_failed
next.nodes.bootstrap_exit
next.nodes.bootstrap_unreachable
next.nodes.stderr_tail
next.nodes.stdout_tail
next.nodes.status_refresh_failed
next.nodes.version_unknown
next.nodes.path_missing
next.nodes.source_unknown
next.nodes.found
next.nodes.not_found
next.nodes.candidates
next.nodes.candidate_active
next.nodes.backend.available
next.nodes.backend.unavailable
next.nodes.backend.remote
next.nodes.backend.remote_detail
next.nodes.backend.capabilities
next.nodes.backend.cap_yes
next.nodes.backend.cap_no
```

（`shell.module.node_status` 标题键已在字典中，无需新增。）

## 6. 接入下一步（本次文件边界外，未改动）

1. `frontend/next/index.html`：在模块脚本区加
   `<script src="/js/modules/node-status/module.js"></script>`，`<head>` 加
   `<link rel="stylesheet" href="/css/modules/node-status.css">`；
   `ACPShell.bootstrap.boot()` 的 `collectFrom(window.ACPNextModules)` 会自动收编注册。
2. `frontend/js/shell/recipes.js`：决定它进哪个工作区/槽位。`layout-preferences.js` 已把
   `node-status` 列为 `EXTRA_MODULES` 布局选项（`shell.module.node_status` 标题键已存在），
   但默认 recipe 尚未引用它——因此当前只能通过「配置布局」手动选中；若要默认可见需在 V5 装配批
   修改 recipe（执行工作区 side/bottom 或新增系统面板槽位）。
3. `frontend/js/shell/i18n.js`：补 §5 的 76 条 key（A-61 全键批次）。
4. parity 记录：A-30/A-31 的「模块注册 + 四态 + 测试 + 键组」四项中，键组覆盖依赖第 3 步；
   浏览器用例（`acp2_next_entry.browser.js`）不在本次文件边界内，尚未添加/执行。

## 7. 测试

```bash
node frontend/js/module_tests/run_all.js acp2_node_status
```

本轮实测（Node v24.21.0）：**15 passed / 0 failed**，exit 0。覆盖：

1. 注册面：唯一 `ACPNextModules` key、无额外全局、descriptor 通过 `ACPShell.registry.validateDescriptor`
   与 `collectFrom`、ModuleInstance 方法齐全；
2. `loading → ready`：节点（状态/负载/磁盘/队列/错误）、后端（可用性/版本/能力 chip）、软件
   （resolved/版本/候选安装）三类行；
3. `empty`：节点 503 `not configured` + 其余为空 → 明确空态、无重试噪音；
4. `error` + 重试恢复；局部失败保留健康分区；FastAPI detail 三种形状；
5. ping：POST 路径/忙碌禁用/成功文案/随后 `GET .../status`；不可达 vs 传输失败的文案区分；
6. bootstrap：忙碌计时器启动、成功文案、失败文案 + stderr 尾部、计时器空闲即停；
7. `deps.request` / `deps.client.transport.request` 两条注入路径 + bootstrap 600 s 预算；
8. 无 adapter 时不发请求且给出明确文案；
9. A-31 未知值语义（不编造版本/路径/来源）；
10. 刷新 token：旧刷新迟到结果被丢弃；
11. dispose：DOM/文档监听/计时器清理、幂等、迟到结果不重建 DOM；
12. 写边界：只有 ping/bootstrap 两个 POST，无任务提交按钮。

## 8. 已知缺口 / 未验证

- **未在真实浏览器执行**：本次只跑 Node vm 套件；`next/index.html` 未加载本模块，浏览器用例未添加
  （受文件边界限制），因此「节点面板在真实实例上的渲染/点击」仍是 NOT_VERIFIED。
- **未接默认布局**：见 §6.2；在 `recipes.js` 接入前，只能经布局编辑器选用。
- **Shell 字典缺 key**：见 §6.3；`data-i18n` 静态元素在 en 下仍显示 zh，直到 key 补齐
  （动态消息本就以 zh 内联 + 本地字典兜底）。
- **无自动轮询**：仅挂载时加载 + 手动刷新；节点状态缓存（服务端 30 s）与经典页一致，但本模块
  不发定时轮询。若 V5 需要周期性刷新，应显式加可 dispose 的定时器（当前只在 bootstrap 期间有计时器）。
- **无取消操作**：bootstrap 是同步长请求，模块没有取消按钮（服务端也无取消端点）；刷新/切换会
  靠 generation/token 丢弃迟到结果，但请求本身仍会完成。
- **ping 的传输预算**：走页面传输默认 8 s（经典页同源行为）；冷缓存下 `GET /nodes` 触发 SSH 探测
  可能超过 8 s 并显示加载失败。若产品要求更长，应在 `shell/bootstrap.js` 的 transport `init`
  统一配置，而不是由本模块私设数值。
- **bootstrap 参数**：前端只发默认 `timeout=600&sync=true`（不带 query），不暴露这两个参数的 UI。
- **`auto_select`**：仅按响应布尔值显示「自动选择已启用/未启用」，不实现也不描述选择策略。
