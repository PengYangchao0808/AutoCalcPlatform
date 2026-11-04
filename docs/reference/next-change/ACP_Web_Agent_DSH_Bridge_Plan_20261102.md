# ACP Workbench × 本地 DSH：网页端 Agent 与 ACP-MCP 集成方案

> **2026-11-04 双人开发同步**：此文继续为备选路线，不与 DSH 插件主线并行开发。当前双人职责/阶段以[架构 §10](ACP_3.0_Architecture_20261031.md)为准；网关或 Harness 不得另实现化学编译器、任务库或科学判断。

> **2026-11-02 路线修订**：新版 UI 以 DSH 插件为主线，见 [插件化与 RPH 接入计划 §13](ACP_Frontend_DSH_Pluginization_Plan_20261102.md)。本文的 Controller 内 Agent 网关保留为备选记录，不与主线同时实施；本轮未复跑本文历史实测。

**日期**：2026-11-02
**状态**：设计（P0 链路已在本机实测通过，P1+ 未实现）
**适用**：本机 Windows DSH 0.2.1-rc.3 + WSL Ubuntu 内的 ACP Controller（`acp.service`，:8765）
**目标读者**：后续开发 Workbench 可视化界面的开发者

---

## 0. 结论

网页端 Agent 不需要、也不应该重写 Agent 循环或第二套 MCP 传输：

```text
Workbench（agent-workspace 模块）
   │  HTTP/SSE（同源 :8765）
ACP Controller（FastAPI，新增 /api/v2/agent/*）
   │  stdio JSON-RPC（Agent Client Protocol v1）
Agent Gateway（新，Controller 内 asyncio 任务）
   │  spawn：dsh --profile acp
本地 DSH（DeepSeek Harness，自带的模型与工具运行时）
   │  MCP stdio（session/new 时按会话声明）
acp mcp serve --controller http://127.0.0.1:8765
   │  HTTP + X-API-Key
ACP Controller REST（jobs / results / frames）
```

三个要点：

1. **DSH 是引擎，ACP 是执行器**。DSH 已经提供标准 ACP v1 服务（`dsh --profile acp`），并且允许客户端在 `session/new` 时**按会话声明 MCP 服务器**——因此不需要改 DSH 的 `~/.dsh/profiles/*`，也不需要 `rph` 那套生产机路径。
2. **MCP 侧不动**。仍是 `acp mcp serve`（协议 2025-06-18，12 个基础工具 + 10 研究 + 装配时 11 消费），DSH 的官方 MCP client 负责握手、工具发现与重连。
3. **UI 已经有落点**。`frontend/js/modules/agent-workspace/module.js` 是 UI-03A 只读首片，工具时间线目前是显式占位（"工具调用时间线将在 DSH 接入后提供"）。本方案只替换这一块并新增传输层，不重做三卡结构。

**最小可见路径**（1 周内）：P0 冒烟脚本 → P1 网关 + stub 测试 → P2 面板接 fixture → P3 接真模型。每一步都有独立的验收，不允许把 fixture PASS 当 live PASS。

---

## 1. 名词消歧（必须先统一，否则代码会互相误会）

| 词 | 本项目含义 | 冲突来源 |
|---|---|---|
| **ACP** | Auto-Calc Platform（本仓库，Python 包 `acp` / `acp_services` / `cccp`） | DSH 也有 "ACP" |
| **ACP v1 / dsh-acp** | **Agent Client Protocol**（Zed 系标准，DSH 的 `dsh --profile acp` 实现） | 与本项目同名，**不是**我们的平台 |
| **ACP-MCP** | 本项目的 MCP server：`acp mcp serve` | DSH 只是消费方 |
| `mcp__acp__<tool>` | DSH 对 MCP 工具的模型可见名（serverName=`acp`） | 原始名才是 ACP 工具名 |

代码与文档约定：网关模块一律叫 `agent_*`（`agent_session.py` / `/api/v2/agent/*`），协议常量写 `dsh.acp.v1`，**不要**再引入 "acp.acp" 这类命名。

---

## 2. 现状与证据

### 2.1 本机 DSH 安装

| 事实 | 值 |
|---|---|
| 应用 | `DeepSeek Harness.exe`，`resources/runtime/…` 0.2.1-rc.3 |
| CLI | `resources/runtime/cli/bin/dsh.cmd`（`ELECTRON_RUN_AS_NODE=1` 调 `app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js`） |
| 正在使用的 profile | `C:\Users\<user>\.dsh\profiles\desktop`（桌面 GUI，含 `oh-my-coharness`、`opencode-go/deepseek-v4.1-flash`） |
| 其它 profile | `web`（`dsh-base` + `dsh-web-app`，`cordis.patch.yml` 为空） |
| 可用应用面 | `dsh web` / `dsh headless --json` / `dsh tui` / **`dsh --profile acp`** / `dsh plugin` |

`dsh --profile acp --help` 本机实测 **exit 0**：

```text
Usage: dsh --profile acp [options]
Serve automation clients over Agent Client Protocol stdio.
```

### 2.2 DSH 的 ACP v1 服务面（来自 `@deepseek-ai/dsh-acp` README）

| 能力 | 内容 | 对 UI 的意义 |
|---|---|---|
| `initialize` | 返回 ACP v1 + `session/list` / `resume` / `close` | 版本握手 |
| `session/new` | **绝对 cwd + stdio/HTTP MCP 声明**，连接失败即回滚不入册 | 注入 ACP-MCP 的唯一入口 |
| `session/prompt` | 文本/资源链接/prompt 图片（本机 image=false），一次一个 | 发消息 |
| `session/cancel` | 取消当前 prompt 或自主工作 | 停止按钮 |
| `session/update` | 已提交的 assistant message / thought、**通用 tool 生命周期**、配置变化、context usage | 时间线与用量 |
| `session/request_permission` | 服务端→客户端的一次性 allow/reject | 工具审批卡 |
| `session/set_config_option` | `model` / `reasoning_effort` | 模型选择器 |
| `session/list` / `resume` / `close` | 持久会话，可跨进程 | 历史会话 |

已知限制（必须写进验收）：**没有 token 流**（更新按提交点成块）、**没有 transcript replay**（`session/load` 不支持）、**一个会话同时只能一个 prompt**、prompt 图片在 `acp` profile 下不可用、额外目录不支持。

### 2.3 我们的 MCP 面（不变）

- 启动：`acp mcp serve --transport stdio --controller <url>`；启动前探测 `GET /api/health`，Controller 不可达则 `exit 3`。
- 未协商 12 个基础工具（`acp_capabilities`、`acp_preflight`、`acp_submit`、`acp_status`、`acp_cancel`、`acp_prepare_artifact`、`acp_read_artifact`、`acp_analyze`、`acp_get_guidance`、`acp_list_jobs`、`acp_get_pes_review`、`acp_publish_pes_review`），协商后另有 10 个研究工具，装配 consumption 后另有 11 个消费操作。
- 鉴权：`X-API-Key`（`ControllerClient(api_key=…)`）；本地单用户当前是 `local-single`。

### 2.4 前端已有落点（不要新建第二套）

| 位置 | 现状 | 本方案如何用 |
|---|---|---|
| `frontend/js/modules/agent-workspace/module.js` | UI-03A 只读三卡（研究摘要 / 计划与步骤 / 证据），`window.ACPNextModules` 注册 | **扩展**：把占位时间线换成真工具卡；新增 transcript 区 |
| 同文件 643–650 行 | `工具调用时间线将在 DSH 接入后提供`（`data-acp-tools-placeholder`） | 直接替换的数据槽 |
| 同文件 8–13 行 | 明确"无 submit/approve/decide 写操作" | 写操作仍走既有统一入口，本模块不新增 |
| `frontend/js/workbench/contracts/experience_contract.js` | `ACP_TOOLS` 冻结清单 + `validateCapabilities/validateViewModel/...` | 新增 `validateAgentEvent`，工具名必须在此清单内 |
| `frontend/js/client/event-stream.js` | 已有 EventSource 封装（关旧连接、断线处理） | 复用到 `agent-stream.js` |
| `frontend/js/client/transport.js` | 统一超时/错误/`err.status`/`err.body` | REST 调用复用 |
| `frontend/js/workbench/features/research-approval.js` | 已把 **DSH tool decision** 与**平台 research approval** 分成两套映射（`dsh.tool_decision`） | 权限卡沿用这条分界线 |

### 2.5 rph 已有资产（结构可借鉴，路径不可直接用）

- `rph/packages/dsh-bundle/profiles/web.cordis.yml`：DSH 侧的 `@deepseek-ai/dsh-mcp-client` 插入示例 + 无 shell 的 `rph-research` preset。其 `command/cwd/env` 指向 `/ACPServer/...` 生产机，本机不可直接套用。
- `rph/packages/dsh-bundle/src/mcp-stdio-launch.ts`、`platform-session.ts`：MCP 启动描述符与平台会话装配。
- `docs/reference/next-change/acp2-contracts/experience/mcp-handshake.md`：MCP 协商矩阵与 F1（descriptor vs live `tools/list` schema 漂移）。**F1 仍按原文保持 OPEN 口径**，本方案不宣称已关闭。

### 2.6 本次实测（P0 证据，2026-11-02 14:31 CST）

用一个 60 行的 stdio 探针（`cmd.exe /c dsh.cmd --profile acp`）依次发送：

1. `initialize` → 成功。返回：

   ```json
   {"protocolVersion":1,
    "agentInfo":{"name":"deepseek-harness-acp","version":"0.0.1"},
    "agentCapabilities":{"mcpCapabilities":{"http":true},
      "promptCapabilities":{"image":false,"audio":false,"embeddedContext":false},
      "sessionCapabilities":{"close":{},"list":{},"resume":{}}},
    "authMethods":[]}
   ```

2. `session/new`（`cwd=E:\...\ACP_V1_20260811`，`mcpServers=[{stdio,name:"acp",command:"C:\\Windows\\System32\\wsl.exe",args:["-d","Ubuntu","-e","bash","-lc","cd /mnt/e/... && export PYTHONPATH=$PWD/src && exec /root/miniforge3/bin/python -m acp mcp serve --transport stdio --controller http://127.0.0.1:8765"]}]`）→ **成功**，返回 `sessionId` 与 `configOptions=[model, reasoning_effort]`。
3. `session/list` → 成功（16 个 DSH 持久会话）。
4. `session/close` → 成功。

DSH 子进程 stderr 同时出现我们的 MCP server 日志，证明 MCP 真的被拉起并完成握手：

```text
acp.services_mcp_bootstrap: MCP adapter bound to Controller http://127.0.0.1:8765
  (candidate_revision=bb309134…, contract_version=1.7.0, identity_binding=local-single)
acp_services.entrypoints.mcp_stdio: MCP session initialized (protocolVersion=2025-06-18)
```

结论：

- **Windows 侧 DSH 可以按会话拉起 WSL 内的 ACP-MCP**（`wsl.exe` interop 可用；WSL2 localhost 转发已使 Windows 能访问 `127.0.0.1:8765`）。
- **协议兼容性实测成立**：DSH MCP client 主动降级到我们唯一支持的 `2025-06-18`，`mcp-handshake.md` §4.1 的静态担忧在本路径上未发生（该文档口径仍保留，等正式 live 运行再更新）。
- `session/new` 成功即代表 MCP 连接与工具发现未回滚（DSH 的文档语义：initial connection/discovery failure rolls back the unpublished Agent）。

**仍未验证**：真模型 prompt、模型实际调用 `mcp__acp__acp_*`、权限请求流、浏览器端到端。这些必须留在 P3 的 live 行，标 `NOT_VERIFIED` 直到真跑。

---

## 3. 目标与非目标

### 3.1 目标

1. Workbench 页内 Agent 面板：输入、流式（块状）回复、工具时间线、引用跳转。
2. Agent 通过 ACP-MCP 完成"发现→预检→提交→等待→产物→找回"的闭环，并在既有可视化组件里打开结果。
3. 会话可取消、可跨刷新恢复、可多会话并存。
4. 与本地 DSH 共用同一引擎与模型路由，不 fork、不 vendor。

### 3.2 非目标

- 不复制 DSH 桌面 GUI，不替换用户正在用的 DSH。
- 浏览器不直连 stdio、不持有 provider key。
- 不实现第二套 Agent 循环或第二套 MCP 传输（DSH 的官方 client 是唯一 MCP 消费方）。
- 不做自动审批：平台 research approval 永远需要人；Agent 不能自批。
- 不在本方案里改 job 状态机、不新增 job status。

---

## 4. 方案对比

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 引擎网关（推荐）** | Controller 内 spawn `dsh --profile acp`，翻译 ACP v1 ↔ SSE | 标准协议、多会话、可取消、可恢复；不改 DSH 配置；MCP 按会话声明 | 需新增网关与依赖；Windows/WSL 进程管理 | **采用** |
| B. 扩展用户 DSH profile | 在 `~/.dsh/profiles/desktop` 插入 ACP-MCP，让用户自己的 DSH GUI 直接有 ACP 工具 | 零代码、立刻可用 | 没有页内 UI；每次改配置要重启 DSH；只覆盖用户本人 | 作为**旁路**提供（可选脚本） |
| C. iframe DSH Web UI | Workbench 内嵌 `dsh web` | 成本最低 | 跨源/auth fence（当前 :61600 全 401）、两套 UI 心智、无法与 canvas 联动 | 不采用 |
| D. Python 自研 Agent 循环 | 自己接模型 + 工具循环 | 完全可控 | 重复 DSH 能力、模型/工具/权限全要重做、违背"不 fork" | 不采用 |

B 的价值：给"只用 DSH 桌面端"的场景一条零代码路径；本次 P0 已验证同一条 MCP 声明，写成文档即可，不占开发主线。

---

## 5. 推荐架构

### 5.1 分层与职责

| 层 | 进程 | 职责 | 禁止 |
|---|---|---|---|
| UI | 浏览器 | 对话、时间线、权限卡、引用跳转 | 不碰 stdio、不存 key、不判科学结论 |
| Controller | WSL `acp.api.server` | 鉴权、SSE、事件日志、会话索引、生命周期 | 不实现 Agent 循环 |
| Gateway | Controller 内 asyncio 任务 | ACP v1 客户端、进程管理、事件归一、权限代理 | 不碰 jobs DB 状态机、不写第二任务事实 |
| DSH | Windows 子进程 | 模型、工具、会话持久化、MCP client | 不直接访问 ACP 内部状态 |
| MCP | WSL 子进程/会话 | 12+10(+11) 工具，1:1 Controller 命令 | 不等待 QC、不拥有任务状态 |

### 5.2 进程与边界（本机真实拓扑）

```text
Windows                                    WSL (Ubuntu)
┌───────────────────────────┐              ┌──────────────────────────────┐
│ Browser → 127.0.0.1:8765  │──localhost──▶│ uvicorn acp.api.server:app    │
│                           │  forwarding  │   └─ /api/v2/agent/* (new)    │
│ DSH child (dsh --profile  │◀──spawn──────│      └─ AgentGateway (async)  │
│  acp)  ──stdio JSON-RPC──▶│              │                              │
│    └─ MCP child via       │              │ acp mcp serve ◀──stdio───────┼──┐
│       wsl.exe -d Ubuntu   │──────────────┼──────────────▶ 127.0.0.1:8765│  │
└───────────────────────────┘              └──────────────────────────────┘  │
                                                                             │
        MCP 由 DSH 拉起（Windows → wsl.exe → python -m acp mcp serve）───────┘
```

实现选择：

- **网关在 WSL（Controller 进程内）**，用 `asyncio.create_subprocess_exec` 调 Windows 的 `dsh.cmd`。WSL interop 需要 `/mnt/c/.../DeepSeek Harness/...` 绝对路径（含空格，用 exec 参数数组而非 shell 字符串）。
- 若 interop 在某些部署被禁用：退回"网关作为 Windows 侧伴生进程，Controller 通过 loopback HTTP 调它"，接口契约不变，只换传输实现（本方案预留 `ACP_AGENT_HOST` 配置）。
- MCP 命令由**网关配置生成**（不是写死在 DSH profile）：`command=wsl.exe`、`args=[-d, <distro>, -e, bash, -lc, <launch>]`、`env` 只带 `ACP_CONTROLLER_URL` 与 `ACP_API_KEY` 引用。P0 已验证该形态。

### 5.3 会话生命周期

```text
POST /sessions ──▶ gateway.ensure_agent_process()
                 └─ initialize(protocol_version=1)
                 └─ new_session(cwd, mcp_servers=[acp])
                 └─ 记录 session_id / configOptions / mcp 状态
prompt ──────▶ session/prompt（长请求；期间 session/update 流）
update ──────▶ seq++ → SSE broadcast + append jsonl
cancel ──────▶ session/cancel → stop_reason=cancelled
close ───────▶ session/close（持久化保留，可 resume）
```

- **一个 DSH 进程多会话**（ACP v1 明确支持并发 session），首次使用时惰性启动，空闲 `ACP_AGENT_IDLE_S=900` 后回收。
- 进程崩溃：网关重启进程，把所有活会话标 `orphaned`，推 `status` 事件；前端可对某个 session 调 `resume`（`session/resume`），**不自动重放历史**。
- prompt 未结束时不接受同会话第二条 prompt（返回 409 `session_busy`，与 DSH 语义一致）。

### 5.4 事件模型与游标

- 每个会话一个单调 `seq`，从 1 开始；**网关是序号的唯一分配者**。
- 事件同时写 `<run_root>/agent-sessions/<session_id>/events.jsonl`（append-only、`os.replace` 原子写索引）；DSH 自己持久化对话，但 **ACP v1 不提供 transcript replay**，所以这份日志是 UI 重建历史的唯一手段。
- 明确边界：这是 **UI transcript 缓存**，不是任务事实库、不是第二状态机、不参与 job 判定；DSH 的 session 才是对话权威。
- 重连：`GET /events?after_seq=N`；若 `N < 最早保留 seq`（默认保留 5000 条/会话），返回 `409 snapshot_required`，前端清空并从 1 重放（日志未裁剪时总是可行）。
- 截断：单条事件 ≤ 32 KiB，文本字段 ≤ 8 KiB 并标 `truncated: true`（与 DSH headless `--json` 的边界对齐，避免两套 UI 行为）。
- 文本按"提交点"到达，不是逐 token；UI 必须接受块状渲染，不做假打字机。

---

## 6. 接口契约（新增，`/api/v2/agent`）

### 6.1 HTTP

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/capabilities` | — | `{enabled, dsh:{version,profile}, protocol:"dsh.acp.v1", mcp:{tools:[…]}, models:[…]}` | 面板启动探测；`enabled=false` → 503 |
| POST | `/sessions` | `{project_id?, cwd?, model?, reasoning_effort?}` | `201 {session_id, config_options, mcp:{name,tools}}` | 创建即完成 MCP 发现 |
| GET | `/sessions` | `?limit=` | `{sessions:[{session_id,cwd,updated_at,preview?}]}` | 来自 `session/list` + 本地索引 |
| POST | `/sessions/{id}/prompt` | `{text}` | `202 {message_id}` | 立即返回；事件走 SSE |
| POST | `/sessions/{id}/cancel` | — | `202` | `session/cancel` |
| POST | `/sessions/{id}/config` | `{option_id, value}` | `200 {config_options}` | `set_config_option` |
| POST | `/sessions/{id}/permissions/{request_id}` | `{option_id}` | `200 {outcome}` | 回答 `request_permission` |
| POST | `/sessions/{id}/close` | — | `204` | 持久化保留 |
| GET | `/sessions/{id}/events` | `?after_seq=N` | `text/event-stream` | 见 6.2 |

鉴权与门控：与 v1/v2 相同的主体/项目校验；路由仅在 `ACP_AGENT_ENABLED=1` 时挂载（默认关闭，避免未验收能力进入生产）；只监听 loopback。

### 6.2 SSE 事件信封 `acp2.experience.agent-event.v1`

```json
{
  "schema": "acp2.experience.agent-event.v1",
  "session_id": "6268c109-…",
  "seq": 12,
  "at": "2026-11-02T06:31:05.293Z",
  "type": "text | thinking | tool_call | tool_update | permission | config | status | turn_end | error",
  "text": "……",
  "truncated": false,
  "tool": {
    "call_id": "call-7",
    "title": "Submit singlepoint",
    "kind": "execute",
    "status": "pending | in_progress | completed | failed",
    "server": "acp",
    "name": "acp_submit",
    "raw_name": "acp_submit",
    "args_digest": "sha256:…",
    "result_digest": "sha256:…"
  },
  "refs": {
    "task_ref": {"job_id": "20261102_001_singlepoint", "attempt": 1},
    "artifact_ref": {"job_id": "…", "resource": "…", "generation": 1, "sha256": "…"},
    "frame": {"view_type": "energy", "path": "RESULT/…"}
  },
  "permission": {"request_id": "perm-1", "tool_call_id": "call-7", "options": [{"option_id":"allow_once","name":"Allow once","kind":"allow_once"}]},
  "stop_reason": "end_turn | cancelled | max_tokens | error"
}
```

规则：

- `type=tool_call` 首次出现，后续状态变化用 `tool_update` 且 `call_id` 相同；前端按 `call_id` 合并。
- 工具名：DSH 给的 `mcp__acp__acp_submit` → `server="acp"`、`name="acp_submit"`；`name` 必须在 `experience_contract.ACP_TOOLS` 内，否则 UI 显示"未知工具"且不提供任何引用按钮（不猜）。
- `refs` 只从工具结果的结构化 payload 提取（`task_ref`/`artifact_ref`/frame 路径），不从模型文本推断。
- `permission` 与平台审批无关，见 6.4。

### 6.3 工具 → 可视化联动矩阵

| MCP 工具 | 结果 | UI 行为 |
|---|---|---|
| `acp_submit` | `task_ref` | 时间线卡片 + "打开任务" → `commands.openTask` |
| `acp_status` | `status/revision` | 卡片状态徽标；RUNNING 时挂到任务详情轮询 |
| `acp_prepare_artifact` / `acp_read_artifact` | `ArtifactRef` | "查看产物" → 既有 viewer / 文件树 |
| `acp_get_pes_review` / `acp_publish_pes_review` | PES 候选/revision | 切到 PES 画布，聚焦候选点 |
| `acp_analyze` | analysis 结果 | 证据卡（verified fact 容器，不放进模型文本） |
| `acp_get_guidance` / `acp_list_jobs` | 指南/任务列表 | 只读面板，不触发提交 |

约束：**energy viewer 仍然禁止提交任务**（ANTI #22）；Agent 的"提交"只能来自 `acp_submit` 工具调用，不从 viewer 发起。

### 6.4 审批：两套映射，永不合并

- **DSH tool permission**（`session/request_permission`）：一次性 allow/reject，只影响本次工具调用；默认 `deny`，`allow_once` 需用户点击；超时 300 s 自动取消。落 `agent.permission.*`。
- **平台 research approval**（`/api/v2/research/approvals/*`）：`plan_digest` 绑定、CAS、人审；Agent principal **没有** approve 权限。
- 前端复用 `research-approval.js` 已有的分界（`dsh.tool_decision` 不触碰平台状态）；新增权限卡只是 `dsh` 侧。

---

## 7. 安全与身份

| 项 | 设计 |
|---|---|
| 入口 | 仅 loopback；`ACP_AGENT_ENABLED=1` 才挂载；与 v1/v2 同一身份中间件 |
| 主体 | Agent 会话绑定创建者 principal；跨 principal 读会话一律 404（与 job 读一致） |
| MCP 凭证 | 每次会话启动注入 `ACP_API_KEY`（scoped），不继承 Controller 的全权 key；`ACP_CONTROLLER_URL` 固定 loopback |
| 模型凭证 | 只在 DSH 子进程 env；网关只传 `*_API_KEY` 的**引用名**，日志/回显一律脱敏（复用现有 `redact` 规则） |
| 工具面 | 仍由 MCP `TOOL_ALLOWLIST` 决定；网关不自行扩权 |
| 审计 | 会话创建、prompt、权限决定、工具调用摘要进事件日志；job 侧审计仍在 Controller |
| 危险操作 | 默认无 shell 工具（DSH `acp` profile 的 base 工具集包含 read/write/edit，P1 用 overlay 收紧到最小集，见 9.1） |

---

## 8. 前端设计

### 8.1 新增/修改文件

| 文件 | 作用 |
|---|---|
| `frontend/js/client/agent-session.js` | `create/list/prompt/cancel/setConfig/answerPermission/close`（复用 `ACPClient.transport`） |
| `frontend/js/client/agent-stream.js` | SSE 订阅：`after_seq`、断线退避、`snapshot_required` 处理、去重（复用 `event-stream.js` 模式） |
| `frontend/js/workbench/controllers/agent-controller.js` | 状态机：`idle → connecting → streaming → waiting_permission → done/error`；会话切换与 generation 守卫 |
| `frontend/js/modules/agent-workspace/module.js` | 替换占位时间线；新增 transcript 区；工具卡与权限卡 |
| `frontend/js/workbench/contracts/agent_contract.js` | `validateAgentEvent`（枚举、`seq` 单调、工具名白名单、截断标记） |
| `frontend/css/shell/agent.css` | 时间线/权限卡样式（沿用 `acp-*` 设计 token） |
| `frontend/js/module_tests/acp2_module_agent.test.js` | 扩展：事件合并、未知工具、`snapshot_required`、权限超时 |

### 8.2 渲染规则（沿用现有诚实性约束）

- 模型文本只在 `.acp-model-note`；验证结论只在 `.acp-verified-fact` / `.acp-tag--pass|fail|warn`，且只来自服务端投影。
- 未知/越界事件 → 显式"数据不合规"卡片，不半渲染。
- 无 DSH 连接是**正常态**，显示"Agent 未连接"与启用条件，不伪造对话。
- fixture（`__ACP_NEXT_AGENT_FIXTURE__`）继续只用于测试/演示，带 `data-acp-fixture="true"` 徽标。

### 8.3 面板布局建议

```text
┌ Agent ────────────────────────────────┬ 画布（structure/path/energy/wavefunction）
│ [model ▾] [effort ▾]      [停止]      │
│                                       │
│ 对话（text/thinking，块状）           │
│ 工具时间线（call_id 合并）            │
│   ✓ acp_preflight  23ms  [详情]       │
│   ▶ acp_submit     …     [打开任务]   │
│ 权限卡（如有）                        │
├───────────────────────────────────────┤
│ 输入…                          [发送] │
└───────────────────────────────────────┘
```

对话与画布共享现有任务上下文：`openTask` 后 canvas 切到 `path`/`energy` 等既有 tab；不新建页面路由。

---

## 9. 实施阶段与验收

### 9.1 P0 — 链路冒烟（0.5–1 天，**已实现并本机 PASS**）

交付：`scripts/agent/acp_smoke.py`（已实现；参数化 `--dsh` / `--cwd` / `--distro` / `--controller` / `--wsl-python` / `--mcp-launch`），无模型调用，退出码遵循三态（0 PASS / 1 FAIL / 2 NOT_VERIFIED）。

2026-11-02 14:34 CST 本机运行结果：

```text
controller: 1.7.0
initialize: deepseek-harness-acp 0.0.1 proto=1
session/new: 440edff1-… options=['model', 'reasoning_effort']
session/list: 18 session(s)
mcp: … MCP session initialized (protocolVersion=2025-06-18)
session/close: ok
PASS: DSH ACP + ACP-MCP session published (no model call, no job created)
```

通过条件（全部满足才算 PASS）：

1. `initialize` 返回 `protocolVersion=1`；
2. `session/new` 返回 `sessionId` + `configOptions`；
3. 该会话的 MCP 子进程日志出现 `MCP session initialized (protocolVersion=2025-06-18)`；
4. `session/list`、`session/close` 成功；
5. 退出后无残留 `acp mcp serve` 进程。

### 9.2 P1 — 网关（3–5 天）

交付：`acp_services/runtime/agent_session.py`、`acp_services/entrypoints/agent_wire.py`、`acp/api/v2_agent.py`、`acp/services_agent_bootstrap.py`、migration 029（会话索引）、stub ACP server fixture。

测试（全部无模型）：

- 严格 JSON-RPC 断言：只发 `initialize/session/new/prompt/cancel/close/list/resume/set_config_option`；未声明方法一律拒绝。
- 事件归一：`session/update` → `agent-event.v1`；`tool_call` + `tool_call_update` 合并；工具名白名单；截断标记。
- 游标：`after_seq` 连续、重复、越界、`snapshot_required` 四条。
- 进程：崩溃重启、空闲回收、并发两会话、同会话第二个 prompt → 409。
- 权限：请求挂起 → 超时 → 取消；回答 allow_once 只作用于该 `call_id`。

### 9.3 P2 — 面板（3–5 天）

交付：8.1 的 6 个前端文件 + i18n key + module tests（fixture，无网络）。

通过条件：fixture 驱动的完整会话（text → tool_call → tool_update → permission → turn_end）在浏览器中无控制台错误；`snapshot_required` 与未知工具两条负例正确渲染；`resize/窄屏/中英文` 各一张截图。

### 9.4 P3 — 真模型联调（1–2 天，需授权）

一条最小真实链：`acp_capabilities` → `acp_preflight` →（可选）`acp_submit` → `acp_status` → `acp_read_artifact`。

证据要求：wire 日志（脱敏）、`session/update` 时间线、生成的 `task_ref`、控制器侧 job 记录。**任何一步缺失即 NOT_VERIFIED**，不得用 fixture 结果替代。

### 9.5 P4 — 可选

多用户会话映射（principal 索引 + 跨用户 404）、预算/用量展示、`resume` 策略、会话归档。

---

## 10. 文件级改动清单

**后端**

- 新增：`src/acp_services/runtime/agent_session.py`（进程 + ACP client + 会话表）
- 新增：`src/acp_services/runtime/agent_events.py`（信封、seq、ring、jsonl）
- 新增：`src/acp/api/v2_agent.py`（路由 + SSE）
- 新增：`src/acp/services_agent_bootstrap.py`（组合根，读 `ACP_AGENT_*` 配置）
- 修改：`src/acp/api/server.py`（挂载 router + 启动/关闭生命周期）
- 修改：`src/acp_services/store/migrations.py`（`029_agent_sessions`：session_id/principal/project/created_at/last_seen，仅索引）
- 修改：`pyproject.toml`（`[agent]` extra：`agent-client-protocol`，pin 到 P1 首次实测通过的版本）

**前端**

- 新增：`frontend/js/client/agent-session.js`、`agent-stream.js`
- 新增：`frontend/js/workbench/controllers/agent-controller.js`
- 新增：`frontend/js/workbench/contracts/agent_contract.js`
- 修改：`frontend/js/modules/agent-workspace/module.js`、`frontend/css/shell/agent.css`、`frontend/js/module_tests/acp2_module_agent.test.js`、`frontend/js/workbench/i18n.js`
- 修改：`frontend/ACP_Workbench_v2.html`（挂载点/脚本引入）

**契约与文档**

- 新增（计划）：`acp2-contracts/experience/agent-session.v1.md` 与 `.json`（落在既有 `docs/reference/next-change/acp2-contracts/experience/` 目录内，本行写出时尚未创建）
- 修改：`docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json`（发布前随前端刷新，见 2026-11-02 的清单重建流程）

**测试**

- `tests/test_acp2_agent_events.py`、`tests/test_acp2_agent_gateway.py`、`tests/test_acp2_agent_routes.py`、`tests/test_acp_agent_live_contract.py`（`-m contract`，需真实 DSH，无模型）

---

## 11. 风险与未决

| 风险 | 影响 | 缓解 |
|---|---|---|
| DSH 版本耦合（本机 0.2.1-rc.3） | 升级可能改 ACP 细节 | P0 冒烟进 CI 的 `contract` 组；`agentInfo.version` 记入证据 |
| Python SDK 为 `1.0.0rc3`（预发布） | schema 漂移 | **线上帧已由 P0 裸 JSON-RPC 探针验证，SDK 客户端包装尚未验证**；P1 第一件事是用 SDK 重跑同一序列，失败则退回薄客户端（仅 8 个方法，附 wire 契约测试） |
| 无 token 流 | 体验"块状" | 明确文案与骨架屏；不伪造打字机 |
| 无 transcript replay | 刷新后重建依赖网关日志 | 日志 append-only + `snapshot_required`；日志丢失时显示"历史不可用"并允许按 id resume |
| Windows/WSL 进程树 | 取消/回收不彻底 | `start_new_session` + 杀进程组；P0 检查残留进程 |
| MCP 启动受 Controller 健康约束 | Controller 重启后旧会话工具失效 | 事件里显式 `mcp_unavailable`；重开会话；不静默重试 |
| 模型费用 | Agent 循环消耗 token | `reasoning_effort` 默认 low；面板显示 usage；预算硬限制仍 DEFERRED（与现状一致） |
| 命名冲突 | 文档/代码误读 | 见第 1 节，代码只出现 `agent_*` 与 `dsh.acp.v1` |

---

## 12. 附：可复现的验证命令

```powershell
# 1) DSH ACP profile 可用
& "C:\Users\<user>\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd" --profile acp --help

# 2) 网关探针（P0 脚本；无模型调用）
python scripts/agent/acp_smoke.py --cwd E:\...\ACP_V1_20260811

# 3) Controller 健康（WSL 侧）
wsl -e curl -s http://127.0.0.1:8765/api/health
```

P0 探针的最小 ACP 帧序列（已实测）：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"E:\\...","mcpServers":[{"type":"stdio","name":"acp","command":"C:\\Windows\\System32\\wsl.exe","args":["-d","Ubuntu","-e","bash","-lc","cd /mnt/e/... && export PYTHONPATH=$PWD/src && exec /root/miniforge3/bin/python -m acp mcp serve --transport stdio --controller http://127.0.0.1:8765"]}]}}
{"jsonrpc":"2.0","id":3,"method":"session/list","params":{}}
{"jsonrpc":"2.0","id":4,"method":"session/close","params":{"sessionId":"<id>"}}
```
