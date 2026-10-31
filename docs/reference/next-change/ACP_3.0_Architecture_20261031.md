# ACP 3.0 架构：以用户虚拟工作站为中心的 RPH/DSH 一体化体系

**设计定位**：Per-User Workspace + DSH Agent + Distributed Scientific Computing。
过去的 ACP 围绕「科学计算任务」组织系统；ACP 3.0 围绕「研究者自己的计算工作环境」组织系统——每个用户拥有独立的 Linux 科研环境（文件、项目、终端、DSH Agent、模型配置、研究历史），从网页/APP/CLI 进入同一环境，并调用分布式集群资源。

日期：2026-10-31
定位：**架构提案与共享契约**，不是已实现记录。后端/前端拆分为两份可并行执行的计划：
- [后端实现计划](ACP_3.0_Backend_Plan_20261031.md)
- [前端兼容计划](ACP_3.0_Frontend_Plan_20261031.md)
细节附件：[多用户虚拟主机 + 一人一 DSH 拓扑](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)（主机/VFS 的代码级差距与文件清单）、[前端目标形态](ACP_Frontend_Target_Workbench_Plan_20261030.md)（Ketcher/DSH 面板）。

---

## 0. 一句话

ACP 3.0 = **一个用户，一个虚拟科研工作站；一个工作站，一个主 DSH Runtime；多个项目，多个可恢复的 ResearchRun；底层计算与数据由 ACP Control Plane / CCCP 统一管理。**

> 术语：本文的 **ACP Control Plane**（即提案中的 ACP-CLI/ACR）在仓库中对应 `src/acp_services`（Workspace/Runtime/Data/Store）+ `src/acp`（API/Scheduler/CLI）；CCCP 为 `src/cccp`；Workbench 为 `frontend/`；RPH 为 `rph/`。

```
用户访问层：ACP Web · Desktop APP · CLI · 第三方 Harness
        │
ACP Gateway / Identity / Workspace Router
（认证、会话路由、权限、工作站唤醒）
        │
┌──────────── 用户 A 工作站 ────────────┐   ┌──────────── 用户 B 工作站 ────────────┐
│ DSH + RPH（会话/模型/Skills/工具）     │   │ 同上，完全隔离                         │
│ Terminal · Projects · ACP-VFS          │   │                                        │
│ 模型配置 / 个人 Skills                 │   │                                        │
└───────────────────────────────────────┘   └───────────────────────────────────────┘
        │
ACP Control Plane（Workspace · Runtime · Data · API/MCP）
任务调度 · 权限 · Artifact Catalog · 事件 Outbox · 研究恢复
        │
CCCP（科学能力与协议） → HPC 集群（Slurm/PBS/LSF/本地） · 分布式存储（热/归档/缓存）
```

---

## 1. 三个核心决策（已冻结）

### D1 每用户一个逻辑 Linux 工作站，而不是强制一台 KVM
- 三种形态对外同一逻辑：**Local-first**（本地 Linux/WSL2）、**Hosted Workstation**（每账户容器 + 持久 Home Volume）、**Isolated Workstation**（microVM/独立 VM）。
- 默认 `systemd-nspawn/LXC`，hosted 可升 microVM；开发环境 `ExistingHostProvider` 把单机 adopt 成工作站。
- 隔离必须来自容器/VM、UID、挂载、网络策略与密钥，**不能只改 `DSH_HOME`**（`isolation.py` 对伪隔离的拒绝语义保留）。
- 工作站可以休眠/唤醒，但**休眠不等于任务停止**：已提交到集群的任务由 Control Plane 持续管理。

### D2 每用户一个 DSH Runtime，RPH 是它的化学研究 Bundle
- 一个账户一个 `dsh web`，`DSH_HOME=/home/acpuser/.dsh`，独立端口、独立凭据、独立插件；**一个账户多个研究 Session，不为每个项目重复起 DSH**。
- RPH 拆为 `rph-dsh-bundle` / `rph-research-core` / `rph-acp-bridge` / `rph-skills`；`research-core` 不依赖 DSH 内部类型，保留跨 Harness 能力。
- **命名冲突**：DSH 自带 `acp` profile 指 Agent Client Protocol，ACP 的 Profile 必须叫 `rph` 或 `acp-rph`，文档与包名不得混用。
- DSH 是 Agent 运行底座，**不是** ACP 的任务调度器、HPC 数据库或分布式文件系统；DSH 的 Session/Storage 不得成为第二套任务权威。

### D3 统一逻辑文件系统，数据分布式存储
- 用户看到 `/home/acpuser/acp/projects/<project>/tasks/<task>/`，真实文件可在工作站、HPC 集群或归档存储；由 **ACP-Data / Artifact Catalog** 维护索引、状态与生命周期。
- 不要求把所有计算数据复制到用户工作站；本地只保留元数据与热缓存。
- 四种访问行为固定：本地缓存直接读；远端在线流式读；已归档展示元数据 + 申请恢复；不可用返回明确原因与最后位置，**绝不返回伪造空文件**。

---

## 2. 边界与权威（不可违反）

| 层 | 负责 | 不负责 |
|---|---|---|
| DSH | Agent 会话、模型、工具、终端、文件 UI、插件生命周期 | 任务调度、HPC 状态、跨集群文件、科学结果权威 |
| RPH | 化学研究逻辑：假设/计划/证据/研究状态；调用 ACP 能力 | 持有任务数据库、直接跑科学程序、定义 ACP 状态 |
| ACP Control Plane（ACP-CLI/ACR） | 任务/Attempt/Artifact、集群适配、事件 Outbox、权限、预算、研究恢复 | 重新实现 Agent Harness、持有模型会话 |
| CCCP | 科学能力与执行协议、证据/Manifest | DSH 依赖、用户/租户概念 |
| Workbench | 交互展示、面板编排 | 脱离 ACR 的后台计算、第二套任务状态 |

### 2.1 三个“不”
- DSH/RPH **负责思考**，不负责成为分布式任务的唯一状态权威。
- ACP Control Plane **负责执行与数据**，不负责重新实现 Agent Harness。
- Workbench **负责交互**，不负责创建一套脱离 ACR 的后台计算服务。

### 2.2 手动计算不依赖 LLM
用户可以在 Molecular 画分子、在 Execution 配参数、手动提交；没有模型或 DSH 不可用时，任务管理与科学结果仍通过 ACP API 可用。这是产品底线。

### 2.3 RPH 四包与长任务唤醒

```
研究者 → DSH Session → RPH Supervisor → Chemistry Skills/Planner
      → rph-acp-bridge → MCP → ACR（任务/研究状态） → CCCP → Cluster Workers
      → Artifact/Evidence
```

| 包 | 职责 | 现状/改造 |
|---|---|---|
| `rph-research-core` | 科学假设、实验计划、证据评价、研究状态；**不依赖 DSH 类型** | 现 `rph/packages/research` |
| `rph-acp-bridge` | 访问 ACP 科学能力/项目/任务/文件；类型化 MCP/SDK | 现 `rph/packages/bridge` |
| `rph-dsh-bundle` | 把 RPH 装入 DSH profile / Cordis 环境 | 现 `rph/packages/dsh-bundle`（profile 名 `rph`） |
| `rph-skills` | Mechanism/NMR/TD 等可移植科研方法 | 现 `dsh-bundle/skills`，拆出独立版本 |

**长任务不靠模型轮询**：RPH 提交 `science_submit` 拿到稳定 `task_id` 后结束当前推理轮次；ACR 持续监控集群；完成事件经 Outbox → Agent Host 唤醒对应 `research_run_id`，RPH 从持久研究状态与 `last_consumed_event_id` 继续。DSH Session 不是可靠任务队列，重启后必须能从 ACP 恢复。

**终端定位**：用户看到的是自己工作站的 shell（`acpuser@acp-workstation:~/acp`），不是某个 HPC 节点的 shell。建议提供 `acp project/task/fs` 与 `rph research` 子命令（尚未实现）；终端只是 ACP-CLI 的人机入口，不是第二套任务系统。

---

## 3. 共享契约（后端/前端并行开发的基础）

### 3.1 标识

| ID | 归属 | 说明 |
|---|---|---|
| `account_id` | Workspace | ACP 账户；`tenant_id` = 该账户 |
| `workstation_id` | Workspace | 一人一机；与 `account_id` 1:1（active） |
| `project_id` | Workspace | 工作站内项目 |
| `research_run_id` | Runtime/Research | 一次 RPH 研究活动，可引用多个 task |
| `task_id` | Runtime | 逻辑计算任务（现有 `job_id` 的对外名） |
| `attempt_id` | Runtime | 任务的一次运行/重试 |
| `artifact_id` | Data | 输入/输出/证据/衍生数据 |
| `dsh_session_id` | Agent Host | DSH 会话；绑定 `research_run_id` |
| `event_id` | Runtime | Outbox 稳定事件 ID；消费游标以它为准 |

**映射约定**：对外 API 用 `task_id`，内部 JobStore 继续用 `job_id`；`attempt` 保持 1-based；`artifact_id` 与现有 `result_manifest` product id/路径双向映射，不新建第二份元数据。

### 3.2 研究/任务关联（唤醒与幂等的基础）

```
account_id
  └── workstation_id
       └── project_id
            └── research_run_id
                 ├── dsh_session_id
                 ├── research_state
                 ├── task_ids[]
                 └── last_consumed_event_id
```

- 提交必须带幂等键；同一 `research_run_id + 科学请求` 重试不产生第二个 task。
- ACR 事件 → Agent Host 唤醒 RPH：以 `last_consumed_event_id` 去重，至少一次投递、恰好一次效果。
- 研究状态持久化在 ACP，不依赖 DSH 聊天历史；DSH 重启后从 `research_state` 继续。

### 3.3 API 面（对外稳定，语言中立）

| 面 | 路径（建议） | 权威 |
|---|---|---|
| 工作站 | `/api/v3/workstations`、`/api/v3/workstations/{id}/{start,stop,suspend,resume,reset}` | Workspace |
| 会话路由 | `/u/{account}/dsh/*`（反代）、`/api/v3/agent/sessions` | Agent Host |
| 项目/任务 | `/api/v3/projects`、`/api/v3/tasks`、`/api/v3/tasks/{id}/attempts` | Runtime |
| 研究 | `/api/v3/research-runs`、`/api/v3/research-runs/{id}/events` | Runtime + RPH |
| 文件/工件 | `/api/v3/artifacts`、`/api/v3/artifacts/{id}/{read,restore,pin}`、`/api/v3/fs/{list,stat}` | Data |
| 事件 | `/api/v3/events/stream`（SSE）、`/api/v3/events/{id}` | Runtime Outbox |
| MCP | `acp mcp serve --controller <url>`（八工具） | Runtime |

前端只依赖上表；具体实现可先 Python、后 Rust，路径不变。

### 3.4 事件枚举（最小集）

`task.submitted` / `task.started` / `task.completed` / `task.failed` / `task.cancelled` / `attempt.changed` / `artifact.published` / `artifact.archived` / `artifact.restored` / `research.run.updated` / `workstation.state.changed`。

事件信封：`{event_id, type, occurred_at, account_id, project_id, research_run_id?, task_id?, attempt?, artifact_id?, payload, schema_version}`。

### 3.5 文件系统路径

```
/home/acpuser/acp/
├── projects/<project_slug>/
│   ├── project.yaml
│   ├── molecules/
│   ├── research/{hypotheses,reports,evidence}/
│   └── tasks/<task_slug>/{input,attempts,results,manifest.json}
├── shared/
├── scratch/
└── .acp/{workspace.json,cache/}
```

- `Project` / `ResearchRun` / `Task` / `Attempt` / `Artifact` 是**内部关系**，不强行塞进同一目录层级；目录只表达 project/task/artifact。
- 计算结果默认只读；输入与用户工作文件可写；运行中日志用流式 tail，不作为普通可写文件。

---

## 4. 组件地图（现有九业务模块 + RPH，及六个新基础设施）

| 现有模块 | 3.0 改动 | 优先级 |
|---|---|---|
| CCCP/calculation | 远端 Worker 标准化任务；保持唯一 Scientific Capability Registry | P1 |
| CCCP/analysis | EvidenceReport 与可归档科学结果清单 | P1 |
| CCCP/software | 引擎适配/Parser 不变，不引入 DSH 依赖 | 保持 |
| ACR/workspace（`acp_services/workspace`） | Account → Workstation → Project；工作站管理 | P0 |
| ACR/runtime（`acp_services/runtime`） | 集群 Adapter、Task/Attempt 持久化、事件与 Agent 唤醒 | P0 |
| ACR/data（`acp_services/data`） | Artifact Catalog、Storage Backend、分级归档、ACP-VFS | P0 |
| Workbench/workbench（`frontend/`） | Research/Molecular/Execution 三工作区，适配 DSH UI 宿主 | P1 |
| Workbench/client（`frontend/js/client`） | API Gateway、事件流、资源引用与缓存 | P1 |
| Workbench/acv（`frontend/js/visualization` + viewer） | 保持独立可视化库，按需装载为 DSH UI Feature | P1 |
| RPH（`rph/`） | 拆 DSH Bundle / Research Core / ACP Bridge / Skills | P0 |

**六个新基础设施**（都不新建“大 Core”）：

| 组件 | 仓库落点 | 职责 |
|---|---|---|
| workstation-manager | `acp_services/runtime/{hosts,host_lifecycle,os_identity,host_providers}` | 创建/休眠/恢复/隔离用户 Linux 环境 |
| agent-host | `acp_services/runtime/{dsh_host,session_router,recovery_bridge}` | 启动/监控/恢复每用户 DSH；会话路由 |
| artifact-catalog | `acp_services/data/artifacts.py` + `scheduler/artifacts.py` 扩展 | 跨集群 Artifact 统一目录 |
| storage-lifecycle | `acp_services/data/{retention,archive,transfer}.py` | 完整性、分级归档、保留/清理 |
| acp-vfs | `acp_services/data/{virtual_fs,fs_cache}.py` + 可选 `acpfs` | 逻辑文件 API，后续 POSIX 挂载 |
| rph-dsh-bundle | `rph/packages/dsh-bundle` | 把 RPH 装入 DSH Profile |

**复用既有设施（不要重造）**：
- 事务性 Outbox 已存在（`runtime/outbox.py`：提交时同事务写事件、至少一次投递、稳定 dedup、游标重放）→ 直接用于 ResearchRun 事件/唤醒。
- 恢复语义已存在（`runtime/recovery.py` + `step_result`/`resume_source`）→ 用于 Attempt/工件恢复，不新建第二套 checkpoint。
- ArtifactRegistry 已存在（`scheduler/artifacts.py`，SQLite）→ 扩展为 Catalog 的本地索引，跨集群位置另存 `locations`。
- StorageBinding/Namespace/ReadRoot 已存在 → ACP-VFS 在其上做投影，不新增分配器。

### 4.1 DSH 能力采用表

| DSH 能力 | 采用 | 边界 |
|---|---|---|
| Cordis / Profiles / Bundles | **直接复用**，创建 `rph` profile | 不 Fork DSH 核心 |
| Agent Loop / Sessions | **直接复用** | 研究状态仍存 ACP，不把 Session 当任务队列 |
| Model Provider / 凭据 | 每用户独立配置 | 凭据只在工作站 `DSH_HOME` |
| SDK JSON-RPC | Agent Host 集成/事件监听 | 不用它当任务状态权威 |
| `ctx.fs`（本地/SSH 后端） | 工作站文件能力 | **不当作跨集群统一 VFS** |
| `ctx.subprocess` / `ctx.terminals` | **复用**，不重造 PTY | 终端会话不跨 Harness 重启存活 |
| Web Workspace Files | 文件展示/导航 | 不成为 Artifact 权威 |
| Client Slots / Sidebar | 承载 ACP 面板 | 面板内化学逻辑归 ACP |
| SSH Provider | 参考其远端工作区原型 | 它是单远端路径空间，**不负责 Slurm/PBS 生命周期与多集群目录** |

### 4.2 目录映射（提案树 → 当前仓库）

| 提案路径 | 当前仓库落点 |
|---|---|
| `acp-cli/workspace` | `src/acp_services/workspace` + `src/acp_services/runtime` |
| `acp-cli/runtime` | `src/acp_services/runtime` + `src/acp/scheduler` |
| `acp-cli/data` | `src/acp_services/data` |
| `agent-host` | `src/acp_services/runtime/{dsh_host,session_router,recovery_bridge}`（新） |
| `rph/research-core` | `rph/packages/research` |
| `rph/dsh-bundle` | `rph/packages/dsh-bundle` |
| `rph/acp-mcp-bridge` | `rph/packages/bridge` |
| `rph/skills` | 现位于 `rph/packages/dsh-bundle/skills`，计划拆出独立包 |
| `workbench/client` | `frontend/js/client` |
| `workbench/acv` | `frontend/js/visualization` + `frontend/js/structure_viewer.js` |
| `workbench/dsh-plugins` | `frontend/dsh-plugins/`（新） |
| `cccp/*` | `src/cccp/*`（不动） |

> 当前仓库没有 Rust 工程；语言策略见后端计划 §9-D1（Stage 0–2 Python，契约语言中立，Rust 后续可替换）。

---

## 5. 版本与命名

- **DSH**：pin 到具体版本（当前 0.2.1-rc 系列，MIT，Node 24）；主机镜像、插件、profile 同版本；升级灰度 + 回滚。
- **RPH**：四个包独立版本；`rph-dsh-bundle` 声明 `engines.dsh`；`bridge` 契约版本 `acp.research.v1`。
- **Profile 名**：`rph` / `acp-rph`（禁止用 `acp`，避免与 DSH 的 Agent Client Protocol profile 冲突）。
- **API 版本**：新面统一 `/api/v3`；旧 `/api/v1|v2` 保留一个迁移周期。

---

## 6. 安全基线（必须满足）

1. **DSH 不是多用户身份系统**：任何 DSH 端口只对可信内部网络开放；浏览器先过 ACP Gateway，再路由到专属工作站；launch token 不作为可分享链接。
2. **插件按账户隔离 + 审批**：DSH Plugin Manager 作用于当前 Profile，会影响该 Profile 的所有 Session——这正是「一人一 DSH」的理由。生产采用可信版本锁、安装审批、权限分级、升级回滚；不可信插件不得访问集群凭据/跨账户文件/ACR 管理 API。
3. **模型凭据**：只存用户工作站 `$DSH_HOME/.credentials.yaml`（`chmod 600`）；ACP 只存 ref 元数据；同 UID 的 agent 可读是工作站隔离边界内的事实，需明确接受。
4. **计算预算与模型预算分离**：模型/会话预算归 DSH；HPC 任务配额/存储配额归 ACP Runtime/Data，两者不混。
5. **手动路径**：无模型、DSH 不可用时，任务提交/查询/文件读取仍走 ACP API。

---

## 7. 路线图（先闭环，再扩展）

| 阶段 | 目标 | 验收 |
|---|---|---|
| **Stage 0** 固化契约 | Workstation/Project/ResearchRun/Task/Artifact ID 与 API 面；pin DSH；rph profile 加载测试 | RPH 在 DSH 内用 ACP MCP，且不建第二个任务库 |
| **Stage 1** 单用户闭环 | 本地 Linux/WSL2：独立 DSH Home、项目树、终端、RPH | 同一账户从 Web/终端/RPH 访问同一项目与计算记录 |
| **Stage 2** 多集群 | Slurm/PBS/LSF Adapter、Artifact Catalog、按需迁移 | 两个集群结果出现在同一项目树，原始文件无需全量复制 |
| **Stage 3** 长任务恢复 | Outbox → ResearchRun → Agent 唤醒；TS→频率→IRC→Evidence 真闭环 | 关浏览器/重启 DSH，任务继续，RPH 从研究状态恢复 |
| **Stage 4** 多用户/APP | 按需工作站、Gateway、隔离、安装引导、三工作区 UI、可选 FUSE | 两账户 DSH/文件/终端/模型凭据完全隔离；休眠/升级不丢任务 |

**下一个具体目标**（而非新写 APP / 大规模 VM）：**Workstation Manager + DSH RPH Bundle + Artifact Catalog 的端到端闭环**，先在单用户 Linux 验证，再扩多用户/远程集群/桌面。

### 7.1 后端/前端并行里程碑

| 周 | 后端 | 前端 | 共享门 |
|---|---|---|---|
| 0–2 | WP0 契约/迁移；RPH 包重排；DSH pin | FE-0 插件骨架；v3 mock/fixture；主题/语言桥 | **M0 契约定稿**（ID/API/事件） |
| 2–6 | WP1 工作站 + WP2 Agent Host | FE-1 Research 工作区（mock → 真接口） | **M1 单用户 DSH 闭环** |
| 6–12 | WP3 Catalog + WP4 VFS + WP6 第二集群 | FE-2 Molecular + FE-3 Execution | **M2 两集群 + 工件/文件** |
| 12–18 | WP5 ResearchRun/Outbox 唤醒 | FE-5 兼容 + FE-6 测试/发布 | **M3 长任务恢复** |
| 18+ | Stage 4 多用户/按需工作站 | APP/WebView、插件生态 | **M4 多用户隔离验收** |

前 6 周前端可完全用 mock 并行；M0 之后 API/事件变更必须走共享契约评审。

---

## 8. 冻结原则

1. 一个用户，一个虚拟科研工作站；一个工作站，一个主要 DSH Runtime；多个项目，多个可恢复的 ResearchRun。
2. DSH 是 Agent 底座，不是任务调度器/HPC 数据库/分布式文件系统。
3. ACP Control Plane 是任务、Attempt、Artifact、事件与预算的唯一权威。
4. Workbench 只做交互，不创建脱离 ACR 的后台计算。
5. 手动计算不依赖 LLM。
6. 新建基础设施只做适配器/运行设施，不复制现有权威状态。

---

## 9. 文档索引

- [后端实现计划](ACP_3.0_Backend_Plan_20261031.md) — 数据模型、API、迁移、工作包、测试。
- [前端兼容计划](ACP_3.0_Frontend_Plan_20261031.md) — DSH 宿主、三工作区、插件、旧入口迁移。
- [拓扑细节与代码差距](ACP_MultiUser_VirtualHost_DSH_Topology_20261031.md)
- [前端目标形态（Ketcher/DSH 面板）](ACP_Frontend_Target_Workbench_Plan_20261030.md)
- [身份支持范围](../next-change/ACP_Services_Identity_Support_Scope.md)
