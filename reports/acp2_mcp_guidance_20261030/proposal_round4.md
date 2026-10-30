# MCP 易用性第 4 轮改进提案（基于 Cope TS 测试会话分析）

日期：2026-10-30。状态：**提案，待外部评审（astra）后落实为计划**。
证据来源：会话 `session-c5822372`（17:10–18:20，216 次工具调用，任务「用 ACP 的 MCP 找到 C=CCCC=C 的 [3,3]-σ 重排过渡态」）的完整轨迹分析。
时间线前提：该测试跑在 PR #45 之后、PR #46 之前——`workflow_contracts` 索引与 `capacity` 在线，**但没有** `task_instructions`、PES2TS 三件套契约、索引 `detail`/`fallback`、`status.directory_name`。改进项需区分“时间差造成的缺失”与“结构性问题”。

## 1. 会话事实与根因

### 1.1 观测事实

| 事实 | 数据 |
|---|---|
| 回包体积 | `acp_capabilities({})` 返回 49,933 字符；agent 侧被 DSH 截断 15,172 字节 |
| 关键块位置 | `workflow_contracts` 索引 pos=47,882 / 49,933（96%）；`capacity` pos=48,322 |
| 回包构成（当前线上实测 61,219 字符） | `admission` 40,526（三处重复的 limits/clocks/precedence）、`capability_descriptors` 12,852、`execution_limits` 5,505，而索引/容量在最后 2.5KB |
| 工具面使用 | 调过 `capabilities({})`、`capabilities(workflow=tsmode)`（not_provided）、`guidance(shared/PESsearch/tsmode/orchestration/topic)`；**从未调 `capabilities(workflow=PESsearch)`** |
| singlepoint 试错 | 4 次提交（input 字符串/source/charge 组合）才猜对形状 |
| irc 输入 | 完全从源码扒出（source_job_id/source_product_id/directions/maxpoints），当时 irc = not_provided |
| 目录定位 | `ls ~/.acp`→`ps`→`find /`→`/ACPServer/acp2-runs`→`~/ACP_runs`，20+ 次 bash；`status` 的 work_dir 被脱敏 |
| 科学路径 | 单坐标扫 C1···C6（4.9→1.55 Å）→ 发现曲线不可用；改扫 C3–C4 断裂 → 仍不可用；最后自建椅式 guess 交 OptTS 才成功；IRC 双向 40 点耗尽未达端点（如实标注 inconclusive） |

### 1.2 根因（按影响排序）

- **R1 回包淹没/截断**：索引与容量位于 96–98% 位置，`admission` 40KB 重复块在前；agent 未见索引 → 不知道 `workflow=ID` 获取 schema 的通道 → 转向源码。
- **R2 契约覆盖缺口**：singlepoint/irc 等 11 个工作流 not_provided，提交形状只能从源码猜（4 次试错 + 源码扒 IRC）。
- **R3 产物/目录定位**：`status` 无可用目录名（当时 directory_name 未上线），MCP 无 list_jobs→ 20+ 次 bash 猜目录。
- **R4 科学路由缺失**：单坐标扫描对协同 [3,3] 迁移科学上不完整（半条路径/分支切换/假高点）；当时无 task instruction、无科学 advisory。
- **R5 时间差**：上述 P2/P3 修复在测试时尚未上线（task_instructions、PES2TS 契约、索引 detail/fallback、directory_name）。

## 2. 改进提案

### P0-a 能力回包分层（修 R1）

- `acp_capabilities` 新增 `detail: "summary" | "full"`（默认 `summary`）。
- summary 保留：`schema_version`、`workflows`、`backends`、`tools`、`protocol_version`、`extensions`、`identity_mode` 摘要、`admission` 摘要（max_cores/max_memory_mb/max_tasks/max_walltime_s + enforcement 标志）、`limits` 摘要、**`task_instructions`、`workflow_contracts`、`capacity` 置于最前**。
- summary 省略/摘要：`capability_descriptors`（12.8KB）、`admission` 全量（40KB）、`execution_limits` 详情、`identity_profiles`/`remote_identity_providers`/`stdio_identity_binding`、`model_token_enforcement`。
- 目标：默认回包 < 8KB；`detail:"full"` 保留现行为（兼容既有客户端与测试）。
- 验收：summary 体积阈值测试；关键块位置断言（index/task/capacity 在头 2KB）；full 与 summary 字段覆盖对账；旧字段不因 summary 丢失（关键小字段保留）。

### P0-b 科学 advisory 三类（修 R4）

共同原则：只基于**可观测输入/数据事实**给条件性提示；不阻断、不做化学断言、不改写意图；引用 `task.find_transition_state` / `workflow.*`。

- **A 提交前（PESsearch/scan，单坐标大跨度闭合）**：触发 = `coordinates` 缺失或长度 1，且 |end−start| ≥ 2.5 Å，且终点 < 2.2 Å（成键距离区）。文案：单坐标只探索一个自由度；若目标是协同多键变化，单键长扫描通常只给半条路径、最高点不一定是 TS，约束优化在中段可能分支切换；建议用 `coordinates` 同步双坐标（一收一放），或读 `task.find_transition_state`。`severity:"info"`，不阻断。
- **B 结果后（曲线质量）**：在曲线概况中标注相邻帧结构突变/能量跳变（如 frame 12→13）为“分支切换区间”；提示该段不可当作平滑反应路径读取、选点用连续段。不报错（约束扫描分支切换是常态）。
- **C 选点/发布后**：提示“选点只是初猜；未经 BatchOptimize opt_freq + 单虚频判定不得声称 TS 找到”。
- 验收：A 类正例（大跨度单坐标触发）、负例（小跨度/多坐标/单键机理不触发）、不阻断断言；B 类量化定义（相邻帧 RMSD/能量跳变的固定阈值写在契约里，标注而非判定）；C 类存在性断言。

### P1-c 契约覆盖补全（修 R2）

- 补 `singlepoint`、`optimize`、`frequency`、`xtb_optimize` 的 input/method 契约（从 `scheduler/jobs.py` 消费表 + simple workflow CLI 投影）。
- 补 `irc`（source_job_id/source_product_id/input/input_role/ts_provenance/directions/maxpoints 等真实消费键）与 `tsmode`（source bundle 形状）。
- 验收：每个工作流正例/负例；Workbench payload 兼容；`describe_workflow` coverage 从 not_provided 改为 partial/complete 并同步索引与 fallback。

### P2-d fallback 升级（修 R2/R3 的兜底）

- `not_provided` 条目 fallback 从散文升级为**可执行 CLI 命令模板**（含输入形状占位符），agent 可直接复制执行。

### 已登记待办（不属本轮）

prepare 错误细节、`acp_wait`、simple workflow `live_status` 投影、接口形状统一、acp_list_jobs 分页/reference。

## 3. 风险与兼容性

- 回包分层是**行为变更**：既有依赖 full 字段的客户端需显式 `detail:"full"`。需检查仓库内测试断言（capabilities 相关测试较多）并逐一改为显式 full 或新默认语义。
- advisory 触发阈值为**启发式**：可能误伤单键机理用户；以 info 级、不阻断、可关闭（`advisories:false`？）控制。
- 契约覆盖新增字段需全链同步（契约语料 manifest/coverage/bom/前端 ACP_TOOLS/验收脚本/测试计数）。

## 4. 验收总纲

1. 回包 summary < 8KB 且关键块在头 2KB；full 兼容。
2. advisory A/B/C 行为测试（触发/不触发/不阻断/引用存在）。
3. 6 个工作流契约正负例 + Workbench 兼容。
4. 全链同步门（manifest/coverage 双 VALID、测试计数随注册表）。
5. 真实会话复验：新会话（重启后）重跑 Cope 任务，统计到首个有效提交的工具调用数、目录定位 bash 次数、singlepoint 试错次数，与基线（216 次）对比。
