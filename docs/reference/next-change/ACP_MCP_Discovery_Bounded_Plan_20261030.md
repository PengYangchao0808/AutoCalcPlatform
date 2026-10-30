# ACP MCP 有界发现与科学提示 — 第 4 轮实施计划

日期：2026-10-30。状态：**计划（已过 external 评审）**。
上位计划：[ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md](ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md)（§3.2 U01—U09、§10 验收）。
外部评审：codex/gpt-6-astra，`consult` 只读评审（完整意见：`reports/acp2_mcp_guidance_20261030/astra_round4_review.md`）。本计划已按评审意见修订，不再保留被否决的"默认 summary / 单坐标科学判据 / 分支切换断言"。

## 1. 背景与证据

真实测试会话 `session-c5822372`（2026-10-30 17:10–18:20，216 次工具调用，任务"用 ACP 的 MCP 找 C=CCCC=C 的 [3,3]-σ 重排过渡态"）的完整轨迹分析：

| 观测 | 数据 |
|---|---|
| `acp_capabilities({})` 回包 | 49,933 字符（DSH 侧截断 15,172 字节） |
| 关键块位置 | `workflow_contracts` 索引 pos=47,882（96%）、`capacity` pos=48,322 |
| 当前线上回包 | 61,219 字符：`admission` 40,526（limits/clocks/precedence 三处重复）、`capability_descriptors` 12,852、`execution_limits` 5,505，索引/容量在最后 2.5KB |
| workflow 查询行为 | 调过 `capabilities(workflow=tsmode)`（not_provided）但**未再试 PESsearch**；`workflow=` 回包仍是完整大包再附加契约块 |
| singlepoint 试错 | 4 次提交（input/source/charge 组合） |
| irc 输入 | 从源码扒出形状（当时 irc = not_provided） |
| 目录定位 | 20+ 次 bash 猜测 run root（当时无 `directory_name`） |
| 科学路径 | 单坐标 C1···C6 扫描（4.9→1.55 Å）→ 不可用；改单坐标 C3–C4 断裂 → 不可用；最终自建椅式 guess 交 OptTS 成功；IRC 双向 40 点耗尽（如实 inconclusive） |

**时间线澄清**：该会话运行于 PR #45 之后、PR #46 之前——`capacity`、`workflow_contracts` 索引在线，但 `task_instructions`、PES2TS 三件套契约、索引 `detail`/`fallback`、`status.directory_name` 均未上线。归因时须区分"时间差缺失"与"结构性问题"；216 次调用总数只能作背景，不能单独作为改进有效性的证据（见 §5）。

**评审指出的归因修正**：agent 已调用过 `capabilities(workflow=...)`，因此"完全不知道存在 workflow 查询通道"不是充分解释；更可能的链是"回包淹没/截断 → 未读全索引 → 以 tsmode 的 not_provided 泛化出'MCP 没有可用 TS 路径'"。该假设需在 §5 的可归因验收中复验。

## 2. 根因（修订版）

- **R1 发现回包过大且 `workflow=` 查询不收敛**：索引/容量埋在 ~96% 位置；显式 workflow 查询仍携带全量大包（`routes.py:352/409` 先构造再附加）→ 第二次读取同样可能截断。
- **R2 契约覆盖缺口**：11/15 工作流 `not_provided`，singlepoint/irc 的提交形状只能从源码猜；权威应是 `submission_contract` 的共用语义契约（消费表/CLI/pydantic 都只是部分来源）。
- **R3 产物/目录定位**：目录名与 run-root 提示缺失（`directory_name` 已在 PR #45 补齐，待复验）。
- **R4 科学路由与提示缺失**：协同反应的单坐标扫描风险、曲线采样连续性、选点后验证状态，都没有结构化提示（task instruction 已在 PR #46 补齐部分，待复验）。
- **R5 预检与提交不一致**：tsmode 等来源校验在 submit 才做，preflight 未做同等检查（`v1_routes.py:2655` vs `8808`）→ 假报可行、反馈回路变长。
- **R6 指南与契约语义漂移**：task 指南写"反应物侧压缩须为感知键"，但 `bond_stretch` 解析允许非感知键（`guidance_pack.py:718` vs `atom_selection.py:88`）——需明确是科学建议还是输入限制，并校验公开入口实际行为。

## 3. 批次与交付物

### 批次 A（P0）：有界发现（Bounded Discovery）

**A1 `acp_capabilities` 显式分层，不改变旧调用形状**

| 层级 | 返回内容 | 兼容策略 |
|---|---|---|
| 旧版无参调用（`{}`） | **保留现有字段与类型**（可调整 JSON 键顺序改善展示） | 保留无参回归测试；不把默认改为 summary |
| 显式 `detail:"summary"` | 导航与关键限制、workflow 覆盖索引、task instruction 索引、capacity、limits/admission 摘要、详情入口 | 新客户端显式选择；默认切换留待新版协议 |
| 按需详情 `workflow=<id>` | **只返回该 workflow 契约 + 必要公共引用**（不再携带大包） | 单块大小上限 + 省略声明 |
| `detail:"full"` | 现行为（完整 descriptor/admission/execution_limits） | 旧客户端可继续用 |

- `workflow=` 请求的重构是本批**最高收益项**：显式请求只组装目标块，避免"二次读取仍截断"。
- summary 中的 capability 用**独立的 `capability_index`**（不冒充完整 descriptor）：capability_id、契约版本、入口、声明状态与可执行状态的区分、关键限制；enforcement 不压成单一布尔；capacity 保留 `observed_at`/`unknown`/admission-view 语义。
- 验收：`<8KB` 定义为 **MCP 序列化回包 UTF-8 字节数**（含 envelope 与后加字段），另验 harness 最终可见文本；头 2KB 为"导航与关键限制"；超预算给显式省略与续读入口（禁止截断 JSON）；三组测试（旧无参原样 / 显式 summary / 协商后工具面）。

**A2 结构化 fallback**：`not_provided` 索引条目返回"缺失能力、受支持入口、前置条件"；CLI 模板仅用于明确支持的 CLI 场景，**不默认建议客户端本地执行**（防绕过 Controller 来源/授权/恢复链）。

### 批次 B（P1）：契约覆盖与预检一致性

**B1 六个工作流契约补全**（权威 = `submission_contract` 共用语义；逐工作流核对真实消费源）：

| 工作流 | 真实来源（必须核对） |
|---|---|
| singlepoint / optimize / frequency | `services/runtime/method_flags.py:104` 共用转换、level/default 解析、输入处理、CLI 消费者 |
| xtb_optimize | `runner.py:1693` 独立 xTB 消费路径（不能套 ORCA simple 字段集） |
| irc | 公开来源引用解析、正式 TS product 验证、runner 转换；**公开来源引用 / 内部 `ts_source` / CLI `--ts-provenance-json` 不混成一张可写表**；`maxpoints` 从 method 或 method.levels.irc 消费 |
| tsmode | 优先公开"来源发现/引用"契约（API 已解析 source_job_id/entry_id 并校验 Hessian），不只暴露内部 bundle 形状 |

- 验收沿"公开示例 → preflight → submit 校验 → 生效 JobSpec/argv"核对默认值、别名优先级、非法字段、来源绑定；覆盖 local/remote；只验证 schema 接受样例不算 complete。

**B2 预检/提交共享无副作用来源校验**：tsmode 等来源检查进入 preflight（缺 Hessian、来源无效、模式不可解析提前返回）；冷远端缺材料时返回"需要 prepare"而不是假报可行。

**B3 指南—契约语义对账**：修正 R6 漂移，明确"建议 vs 限制"并校验公开入口实际行为；仅改版本号不算完成。

**B4 跨工作流衔接的可执行契约**：补齐上位计划已有的显式操作链（来源发现 → 候选提案/合法发布 → 可消费引用 → Batch → 验证），返回下一步所需输入与阻塞原因；**不建"自动决定全部科学步骤"的大编排工具**，多个显式命令可自动连续执行。

### 批次 C（P0，与 B 并行小批）：科学提示 A/C

**C1 A 类：几何事实提示**（不做化学断言）

- 触发前先统一坐标表示/单位/方向（复用 `contracts.py:518` 的 `scan_coordinates` 归一），不直接检查原始 JSON 字段有无。
- 文案（几何事实 + 条件性建议）：
  > 当前计划只驱动一个距离坐标（4.90 → 1.55 Å）。该扫描不保证覆盖目标反应的全部相关结构变化；曲线峰值仅供初猜。若已给定的反应假设涉及多个键变化，请检查其沿途演化，并读取适用的多坐标或路径指南。
- 阈值（跨度/终点）作为**有版本、待校准的提示策略参数**（记入契约参数表，注明未校准），不称科学判据；`severity:"info"`、不阻断；归属：preflight/submit 规范化之后的纯函数，Controller 投影（scheduler 不维护第二套规则）。

**C2 C 类：结构化证据状态**（不新增第三份正文）

- 选点/发布回包返回结构化 `initial_guess_status`：来源 task/attempt、帧/候选引用、缺失验证项（频率/模式/端点）、指南引用；按缺失证据提示，不复述指南正文；不把"opt_freq+单虚频"写成充分判据。
- 归属：ACP 候选发布/证据投影层；授权、CAS、来源绑定仍由既有服务管理。

### 批次 D（P2，后置）：采样连续性指标

- 改名"**相邻采样连续性异常**"。先冻结测量定义再谈阈值：
  - 比较对象：同 attempt/profile、明确原子映射、**实际相邻的有效采样点**（缺点不可跨越当作相邻）；
  - 几何：固定原子集合、对齐方法、是否允许置换；输出相邻 RMSD + 目标反应相关距离变化；
  - 能量：同方法/电子态/能量定义；分别报告 ΔE、实际坐标步长、局部斜率变化；
  - 未校准时返回数值与 `not_evaluated`，**不宣布"连续"或"分支切换"**；
  - 复用 `compute_neighbor_rmsds`（`path_analysis.py:297`）但保留**边级**证据（现返回每帧左右邻居最大值，不能直接标注 12→13）。
- 阈值以"物理量下限 + 局部 median/MAD 异常度"组合评估，倍数/窗口/最小样本数需标注样本校准；校准前只出指标不出判定。
- 归属：CCCP 科学分析产指标 → Services 管理工件生命周期 → Controller 读；只读 status 不得为生成提示而隐式启动计算或远端 fetch。

### 批次 E：可归因的行为验收（与各批次绑定）

- **冻结**模型、harness、代码版本、任务条件（同一 Cope 任务）。
- **分档比较**：只改发现（A）→ 再补契约（B）→ 再加提示（C/D）；统计指标：
  - 首次有效 preflight 的调用序号；
  - 被丢弃/纠错的字段数；
  - 源码探索与目录探索次数；
  - 无变化轮询次数；
  - 重复计算（同任务重复提交）次数；
  - 无依据结论数（如未验证即称 TS）。
- 216 次总调用仅作背景；每档收益必须由指标差异支持，不能由单次 PASS 代替。

## 4. 风险与开放问题

- **兼容性**：旧客户端无参调用保持原形；新参数需按实际 `tools/list` schema 使用；HTTP POST 字段白名单需同步（`routes.py:590`）。
- **阈值合法性**：C1 触发参数标注"未校准"；D 类在样本校准前只出指标。任何固定阈值都不得表述为科学判据。
- **契约权威**：新增契约必须从 `submission_contract` 扩展并投影，不从 argparse 反向生成。
- **开放问题**：`detail` 参数命名与版本协商方式（新版协议改默认 summary 的时机）；C1 阈值是否需要按体系类别分组；B4 显式操作链的首批范围（PES/scan + Batch 先行）。

## 5. 验收总纲

1. A1：summary UTF-8 < 8KB 且头 2KB 为导航；`workflow=` 单块请求不携带大包；旧无参/新显式/协商后三组回归；显式省略与续读入口。
2. A2：`not_provided` 条目含缺失能力/受支持入口/前置条件；CLI 模板仅在明确支持的场景出现。
3. B1：六工作流"示例→preflight→submit→生效 argv"链路（local/remote）与非法字段负例。
4. B2：tsmode 缺失/无效来源在 preflight 即返回 typed refusal；冷远端返回需 prepare 而非假报可行。
5. B3：指南与契约对账清单通过（无"建议/限制"混淆）；公开入口行为与指南一致。
6. C1：几何提示正例/负例（协同与单键机理各自的边界）、不阻断、归一化后触发；C2：`initial_guess_status` 缺失项由证据计算而非文案复制。
7. D：指标字段与 `not_evaluated` 语义；边级证据可定位到具体相邻帧对；校准样本与误报/漏报声明（校准前不得出判定）。
8. E：三档归因比较表（发现/契约/提示），指标差异报告；不得用总调用数或单次 PASS 替代。

## 6. 批次顺序（评审结论）

`A（有界发现） → B（契约覆盖 + 预检一致性） → C1/C2（几何提示与证据状态，可小批并行） → D（校准后启用） → E（贯穿验收）`
——依据：先让已有契约真正可读（A），再消除"猜形状"的动机（B），最后才是需要校准的科学提示（C/D）。
