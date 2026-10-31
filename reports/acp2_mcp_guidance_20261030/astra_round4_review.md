codex_consult: consult in /ACPServer/AutoCalcPlatform (timeout 900s)
Reading additional input from stdin...
OpenAI Codex v0.163.0
--------
workdir: /ACPServer/AutoCalcPlatform
model: gpt-6-astra
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 01a18c60-4ccf-7bf1-9f97-b8137ee8010b
--------
user
... (read-only exploration transcript omitted; session 01a18a9e-... ) ...

codex
我的建议是：**P0-a 优先，但必须版本化；P1-c 提前到科学启发式之前；P0-b 拆开交付；P2-d 不应把直接执行 CLI 当作默认补救。**

本次只读核查基于 HEAD `35760baf`，未修改文件、运行计算或重启服务。61,219 字符可由本地 [cap_now.json](/tmp/da_validation/cap_now.json) 核实，但该快照没有 `task_instructions`，不能代表当前源码组装的完整回包。216 次调用的归因仍应作为待复验假设。

1. **P0-a：支持有界发现，不支持“默认 summary＋保留 full 就兼容”的表述。**

   **兼容性存在两个方向的问题。**

   - **旧客户端→新服务端**：旧客户端仍发送 `{}`，不会自动补 `detail:"full"`。删除字段、改变嵌套结构，甚至把缺失值解释成“能力不存在”，都可能改变行为。现有端口明确约定省略选择参数保持冻结形状：[mcp.py:1634](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:1634)。把原测试全部改成显式 full，只能验证新调用，不能验证旧客户端。
   - **新 agent→旧服务端**：当前 MCP schema 禁止额外字段，HTTP POST 也有字段白名单；新参数不能假定旧端接受。[mcp.py:730](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:730)、[routes.py:590](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:590)。新 agent 应先依据实际 `tools/list` schema 使用参数。

   **建议采用以下分层，参数名属于设计建议：**

   | 层级 | 返回内容 | 兼容策略 |
   |---|---|---|
   | 旧版无参发现 | 保留现有字段与类型；可调整展示顺序 | 留下原样调用的回归 |
   | 显式 summary | 短导航、覆盖状态、执行限制摘要、容量快照、详情入口 | 新客户端显式选择；新版协议才改默认 |
   | 按需详情 | 单个 workflow、capability、admission/execution-policy 块 | 每块独立大小限制、版本及省略声明 |

   **特别要修 `workflow=...` 的回包范围。** 当前先构造大包，再附加 `workflow_contract`；所以即使 agent 找到入口，第二次读取仍可能截断。[routes.py:352](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:352)、[routes.py:409](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:409)。新版显式请求应只返回目标契约及必要公共引用。

   优先在现有工具内增加明确的子资源选择；如果现有兼容负担导致无法提供短入口，再增加一个职责清晰的发现工具。**不要每个大块都新建工具，也不要只返回 URI 而假定 harness 会读取。**

   **summary 应保留 capability 的精简索引，但不要把精简对象冒充原有完整 descriptor。** 建议另用 `capability_index`，保留：

   - capability ID、契约版本、可调用入口及详情请求；
   - 声明与运行时可执行状态的区别；
   - 阻止使用的关键限制或原因。

   当前代码明确区分能力声明与实际执行状态，裁剪时不能丢掉这一语义。[routes.py:357](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:357)。同理，不能把多个 enforcement 状态压成一个布尔值：预算记录、准入上限、进程超时强制不是同一件事。`capacity` 应保留 `observed_at`、`unknown` 和 admission-view 语义，不代表资源预留。[capacity.py:174](/ACPServer/AutoCalcPlatform/src/acp/api/capacity.py:174)。

   **验收调整：**

   - `<8KB` 明确定义为实际 MCP 序列化回包的 UTF-8 字节数，包含 envelope 和 MCP 后加字段；另验 harness 最终可见文本。
   - 头 2KB 放“导航与关键限制”，不要求完整索引、任务说明和容量都塞进去。JSON 键顺序只能改善展示，不能承担协议正确性。
   - 超预算返回显式省略及可执行续读入口，禁止截掉 JSON。
   - 保留旧客户端无参调用、新客户端短调用、协商后工具面三组测试。

2. **P0-b：A 可以成为几何提示；B 不能直接命名为分支切换；C 应成为证据状态。**

   **A 类阈值不能识别协同反应。**

   单坐标 relaxed scan 固定一个坐标，同时优化其他自由度；它并不意味着其他成键/断键变化不会发生。“单坐标通常只给半条路径”缺少这里所需的统计依据。ORCA 官方也把单坐标扫描作为 TS 初猜来源，并区分多坐标独立扫描与同步扫描。[ORCA Surface Scans](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_scans.html)

   具体反例和缺口：

   - 4.9→1.55 Å 可以触发几何提示，但不能据此判定 Cope、DA，或曲线必然无效。
   - 2.4→1.5 Å 的协同反应扫描不会触发；反过来，单纯接近过程也可能触发。**仅凭这些输入，无法满足提案“单键机理不触发”的负例。**
   - `2.2 Å` 不能统称“成键距离区”，其含义依赖元素对和电子结构。
   - “同步双坐标一收一放”适合某些 Cope 初猜设计，不能泛化到 DA；DA 正向加成的两个主要成键距离通常都是缩短。
   - 同步双坐标仍然是一条预设参数路径，不等于探索完整二维 PES，也不保证经过目标 TS。

   建议文案改成：

   > 当前计划只驱动一个距离坐标，从 4.90 Å 缩短至 1.55 Å。该扫描不保证覆盖目标反应的全部相关结构变化；曲线峰值仅供初猜。若已给定的反应假设涉及多个键变化，请检查其沿途演化，并读取适用的多坐标或路径指南。

   实现必须先统一实际坐标表示、单位和方向，再触发提示，不能直接检查原始 JSON 中 `coordinates` 是否缺失。PES 已有兼容单数/复数表示的 `scan_coordinates` 入口。[contracts.py:518](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/contracts.py:518)。2.5/2.2 可暂作**有版本、待校准的提示策略参数**，不能称科学判据。

   **B 类建议改名为“相邻采样连续性异常”。**

   RMSD 或能量跳变也可能来自步长变化、构象转换、未收敛、电子态变化或原子映射错误；仅凭它们不能确认优化分支切换。

   推荐先冻结“如何测量”，再校准“何时提醒”：

   | 项目 | 最小可复核定义 |
   |---|---|
   | 比较对象 | 同 attempt/profile、明确原子映射、实际相邻的有效采样点；缺点不能直接跨过去当相邻 |
   | 几何 | 固定原子集合、对齐方法及是否允许置换；输出相邻 RMSD，同时报告目标反应相关距离变化 |
   | 能量 | 相同方法、电子态和能量定义；分别报告 ΔE、实际坐标步长及局部斜率变化 |
   | 阈值 | 使用按方法、步长、体系类别分组的标注样本校准；公布误报/漏报及适用范围 |
   | 未校准情况 | 返回数值和 `not_evaluated`；不宣布“连续”或“分支切换” |

   可评估“物理量下限＋局部 median/MAD 异常度”的组合，但倍数、窗口、最低样本数仍需校准；MAD 为零或样本太少时要显式退化。**把任意固定阈值写进契约，并不会让它获得科学依据。**

   现有 `compute_neighbor_rmsds` 已计算相邻对 RMSD，但返回的是每帧左右邻居的最大值，不能直接据此精准标注“12→13”。应复用计算并保留边级证据。[path_analysis.py:297](/ACPServer/AutoCalcPlatform/src/cccp/analysis/pes/path_analysis.py:297)。

   对选点的实际帮助应是：提示不要跨异常边拟合峰值，提供边两侧帧与原因，必要时建议加密或从两侧重新优化；**不能把未触发异常的区间升级为已验证反应路径。**

   **C 类文字已有覆盖，运行时状态仍有价值。**

   当前 task 指南和驻点专题已经区分初猜、OptTS/频率验证、模式关联和 IRC 连接性。[guidance_pack.py:611](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:611)、[guidance_pack.py:716](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:716)。不要再维护第三份独立提示正文。

   建议选点回包提供结构化的“初猜状态、缺失验证、指南引用”，按缺失证据提示。提案的“BatchOptimize opt_freq＋单虚频”既不是充分科学判据，也应与平台规定的科研链流程区分；还须检查收敛、模式对应，连接性另行验证。[ORCA TS Searches](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_TS.html)

   **归属建议：**

   | 类型 | 实现归属 |
   |---|---|
   | A | 共用 preflight/submit 规范化之后的纯函数；Controller 投影提示，scheduler 不维护第二套规则 |
   | B | CCCP 科学分析产生指标和诊断；Services 管理分析/工件生命周期；Controller 读取已有结果 |
   | C | ACP 候选发布及证据投影返回状态；授权、CAS、来源绑定继续由既有服务管理 |

   只读 status/概况请求不能为生成 B 类提示而隐式启动计算或远端 fetch。

3. **P1-c：权威应是共用提交语义契约；jobs、CLI、Pydantic 都只是部分来源。**

   现有设计已明确：`submission_contract` 拥有语义，消费者 flag 表只是下界；`workflow_contracts` 负责发布。[submission_contract.py:14](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:14)。应沿这个方向补齐，而非选择 argparse 作为新的统一权威。

   | 工作流 | 必须核对的真实来源 |
   |---|---|
   | singlepoint / optimize / frequency | 共用 `method_flags` 转换、实际 level/default 解析、输入处理和 CLI 执行消费者 |
   | xtb_optimize | runner/remote 的 `levels.xtb_opt` 转换及任务请求语义，不能套用 ORCA simple 字段集 |
   | irc | API 来源引用解析、正式 TS product 验证、runner 转换及 IRC 请求契约 |
   | tsmode | API 频率来源发现、Hessian/模式映射检查、bundle 物化及执行请求契约 |

   simple 的共享转换实现实际上在 [method_flags.py:104](/ACPServer/AutoCalcPlatform/src/acp_services/runtime/method_flags.py:104)，xTB 有独立消费路径：[runner.py:1693](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1693)。现有 V1 Pydantic 模型中的 `input`、`method` 是自由字典，自动导出不能补出这些语义。[v1_schemas.py:314](/ACPServer/AutoCalcPlatform/src/acp/api/v1_schemas.py:314)。

   **两处具体修正：**

   - IRC 的公开来源引用、内部 `ts_source`、CLI `--ts-provenance-json` 不应混成一张可写字段表。`maxpoints` 当前从 `method` 或 `method.levels.irc` 消费。[v1_routes.py:2449](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2449)、[runner.py:1654](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1654)。
   - tsmode 应优先公开可选来源及模式的发现/引用契约，不能只告诉 agent 内部 bundle 长什么样。当前 API 会解析 `source_job_id`、`entry_id` 并检查 Hessian。[v1_routes.py:2136](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2136)。

   验收应沿着“**公开示例→preflight→submit 校验→生效 JobSpec/argv**”核对默认值、别名优先级、非法字段和来源绑定，并覆盖 local/remote。只验证 schema 接受样例不足以宣称 complete。

   **更深的根因是：缺少可执行的跨工作流衔接契约，而不仅是六张参数表。** 当前 task 指南仍声明自动发布未开放、IRC 提交键未契约化；preflight 的计划摘要仍是单 workflow、`steps:1`。[guidance_pack.py:740](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:740)、[v1_routes.py:8883](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:8883)。

   不必立即建一个自动决定全部科学步骤的“大编排工具”。更直接的是补齐上位计划已有的显式操作：来源发现→候选提案/合法发布→可消费引用→Batch→验证，并返回下一步所需输入及阻塞原因。多个显式命令也可以自动连续执行。[上位计划:131](/ACPServer/AutoCalcPlatform/docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md:131)。

4. **建议优先级改为：P0-a → P1-c及预检一致性 → 精简A/C → 校准后的B；P2-d改设计。**

   - **P0-a 第一**：先让已有契约确实能被读到，同时解决按 workflow 查询仍携带大包的问题。
   - **P1-c 第二**：它直接消除四次 singlepoint 猜形状和 IRC 读源码的动机，收益比未经校准的科学阈值更确定。
   - **A/C 可小批并行落地**：A 仅报告几何事实；C 复用已有指南，补证据状态。
   - **B 后置**：先定义指标和标注集，再决定阈值。
   - **P2-d 改为结构化 fallback**：返回缺失能力、受支持入口及前置条件；CLI 模板仅用于明确支持的 CLI 场景。直接复制 `acp run` 可能换成客户端本地执行，也不能补齐 Controller 的来源、授权和恢复链。上位计划明确不能用 shell/QC 弥补接口缺口。[上位计划:690](/ACPServer/AutoCalcPlatform/docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md:690)。

5. **建议补入三项高价值改进。**

   - **预检与提交共享无副作用的来源校验。** 当前提交会调用 tsmode 来源准备，而 preflight 的验证路径未做同等检查。[v1_routes.py:2655](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2655)、[v1_routes.py:8808](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:8808)。把“缺 Hessian、来源无效、模式不可解析”提前返回；冷远端缺材料时返回需要 prepare，不能假报可行。
   - **指南与契约做语义对账。** 已有具体漂移：task 指南说反应物侧压缩须为感知键，但当前 `bond_stretch` 解析允许非感知键的成键/断键距离扫描。[guidance_pack.py:718](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:718)、[atom_selection.py:87](/ACPServer/AutoCalcPlatform/src/cccp/analysis/pes/atom_selection.py:87)。应明确这是科学建议还是输入限制，并验证公开入口实际行为；仅更新版本或摘要发现不了这种矛盾。
   - **重做可归因的行为验收。** 提案自己记录 agent 已调用 `capabilities(workflow=tsmode)`，因此“完全不知道 workflow 查询通道”不是充分解释。[提案:16](/tmp/da_validation/PROPOSAL_round4.md:16)。冻结模型、harness、代码版本和任务条件，分别比较“只改发现”“再补契约”“再加 advisory”；统计首次有效预检、被丢弃字段、源码/目录探索、无变化轮询、重复计算和无依据结论。**216 次总调用数可作背景，不能单独证明改进有效。**
tokens used
118,015
我的建议是：**P0-a 优先，但必须版本化；P1-c 提前到科学启发式之前；P0-b 拆开交付；P2-d 不应把直接执行 CLI 当作默认补救。**

本次只读核查基于 HEAD `35760baf`，未修改文件、运行计算或重启服务。61,219 字符可由本地 [cap_now.json](/tmp/da_validation/cap_now.json) 核实，但该快照没有 `task_instructions`，不能代表当前源码组装的完整回包。216 次调用的归因仍应作为待复验假设。

1. **P0-a：支持有界发现，不支持“默认 summary＋保留 full 就兼容”的表述。**

   **兼容性存在两个方向的问题。**

   - **旧客户端→新服务端**：旧客户端仍发送 `{}`，不会自动补 `detail:"full"`。删除字段、改变嵌套结构，甚至把缺失值解释成“能力不存在”，都可能改变行为。现有端口明确约定省略选择参数保持冻结形状：[mcp.py:1634](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:1634)。把原测试全部改成显式 full，只能验证新调用，不能验证旧客户端。
   - **新 agent→旧服务端**：当前 MCP schema 禁止额外字段，HTTP POST 也有字段白名单；新参数不能假定旧端接受。[mcp.py:730](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:730)、[routes.py:590](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:590)。新 agent 应先依据实际 `tools/list` schema 使用参数。

   **建议采用以下分层，参数名属于设计建议：**

   | 层级 | 返回内容 | 兼容策略 |
   |---|---|---|
   | 旧版无参发现 | 保留现有字段与类型；可调整展示顺序 | 留下原样调用的回归 |
   | 显式 summary | 短导航、覆盖状态、执行限制摘要、容量快照、详情入口 | 新客户端显式选择；新版协议才改默认 |
   | 按需详情 | 单个 workflow、capability、admission/execution-policy 块 | 每块独立大小限制、版本及省略声明 |

   **特别要修 `workflow=...` 的回包范围。** 当前先构造大包，再附加 `workflow_contract`；所以即使 agent 找到入口，第二次读取仍可能截断。[routes.py:352](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:352)、[routes.py:409](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:409)。新版显式请求应只返回目标契约及必要公共引用。

   优先在现有工具内增加明确的子资源选择；如果现有兼容负担导致无法提供短入口，再增加一个职责清晰的发现工具。**不要每个大块都新建工具，也不要只返回 URI 而假定 harness 会读取。**

   **summary 应保留 capability 的精简索引，但不要把精简对象冒充原有完整 descriptor。** 建议另用 `capability_index`，保留：

   - capability ID、契约版本、可调用入口及详情请求；
   - 声明与运行时可执行状态的区别；
   - 阻止使用的关键限制或原因。

   当前代码明确区分能力声明与实际执行状态，裁剪时不能丢掉这一语义。[routes.py:357](/ACPServer/AutoCalcPlatform/src/acp/api/routes.py:357)。同理，不能把多个 enforcement 状态压成一个布尔值：预算记录、准入上限、进程超时强制不是同一件事。`capacity` 应保留 `observed_at`、`unknown` 和 admission-view 语义，不代表资源预留。[capacity.py:174](/ACPServer/AutoCalcPlatform/src/acp/api/capacity.py:174)。

   **验收调整：**

   - `<8KB` 明确定义为实际 MCP 序列化回包的 UTF-8 字节数，包含 envelope 和 MCP 后加字段；另验 harness 最终可见文本。
   - 头 2KB 放“导航与关键限制”，不要求完整索引、任务说明和容量都塞进去。JSON 键顺序只能改善展示，不能承担协议正确性。
   - 超预算返回显式省略及可执行续读入口，禁止截掉 JSON。
   - 保留旧客户端无参调用、新客户端短调用、协商后工具面三组测试。

2. **P0-b：A 可以成为几何提示；B 不能直接命名为分支切换；C 应成为证据状态。**

   **A 类阈值不能识别协同反应。**

   单坐标 relaxed scan 固定一个坐标，同时优化其他自由度；它并不意味着其他成键/断键变化不会发生。“单坐标通常只给半条路径”缺少这里所需的统计依据。ORCA 官方也把单坐标扫描作为 TS 初猜来源，并区分多坐标独立扫描与同步扫描。[ORCA Surface Scans](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_scans.html)

   具体反例和缺口：

   - 4.9→1.55 Å 可以触发几何提示，但不能据此判定 Cope、DA，或曲线必然无效。
   - 2.4→1.5 Å 的协同反应扫描不会触发；反过来，单纯接近过程也可能触发。**仅凭这些输入，无法满足提案“单键机理不触发”的负例。**
   - `2.2 Å` 不能统称“成键距离区”，其含义依赖元素对和电子结构。
   - “同步双坐标一收一放”适合某些 Cope 初猜设计，不能泛化到 DA；DA 正向加成的两个主要成键距离通常都是缩短。
   - 同步双坐标仍然是一条预设参数路径，不等于探索完整二维 PES，也不保证经过目标 TS。

   建议文案改成：

   > 当前计划只驱动一个距离坐标，从 4.90 Å 缩短至 1.55 Å。该扫描不保证覆盖目标反应的全部相关结构变化；曲线峰值仅供初猜。若已给定的反应假设涉及多个键变化，请检查其沿途演化，并读取适用的多坐标或路径指南。

   实现必须先统一实际坐标表示、单位和方向，再触发提示，不能直接检查原始 JSON 中 `coordinates` 是否缺失。PES 已有兼容单数/复数表示的 `scan_coordinates` 入口。[contracts.py:518](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/contracts.py:518)。2.5/2.2 可暂作**有版本、待校准的提示策略参数**，不能称科学判据。

   **B 类建议改名为“相邻采样连续性异常”。**

   RMSD 或能量跳变也可能来自步长变化、构象转换、未收敛、电子态变化或原子映射错误；仅凭它们不能确认优化分支切换。

   推荐先冻结“如何测量”，再校准“何时提醒”：

   | 项目 | 最小可复核定义 |
   |---|---|
   | 比较对象 | 同 attempt/profile、明确原子映射、实际相邻的有效采样点；缺点不能直接跨过去当相邻 |
   | 几何 | 固定原子集合、对齐方法及是否允许置换；输出相邻 RMSD，同时报告目标反应相关距离变化 |
   | 能量 | 相同方法、电子态和能量定义；分别报告 ΔE、实际坐标步长及局部斜率变化 |
   | 阈值 | 使用按方法、步长、体系类别分组的标注样本校准；公布误报/漏报及适用范围 |
   | 未校准情况 | 返回数值和 `not_evaluated`；不宣布“连续”或“分支切换” |

   可评估“物理量下限＋局部 median/MAD 异常度”的组合，但倍数、窗口、最低样本数仍需校准；MAD 为零或样本太少时要显式退化。**把任意固定阈值写进契约，并不会让它获得科学依据。**

   现有 `compute_neighbor_rmsds` 已计算相邻对 RMSD，但返回的是每帧左右邻居的最大值，不能直接据此精准标注“12→13”。应复用计算并保留边级证据。[path_analysis.py:297](/ACPServer/AutoCalcPlatform/src/cccp/analysis/pes/path_analysis.py:297)。

   对选点的实际帮助应是：提示不要跨异常边拟合峰值，提供边两侧帧与原因，必要时建议加密或从两侧重新优化；**不能把未触发异常的区间升级为已验证反应路径。**

   **C 类文字已有覆盖，运行时状态仍有价值。**

   当前 task 指南和驻点专题已经区分初猜、OptTS/频率验证、模式关联和 IRC 连接性。[guidance_pack.py:611](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:611)、[guidance_pack.py:716](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:716)。不要再维护第三份独立提示正文。

   建议选点回包提供结构化的“初猜状态、缺失验证、指南引用”，按缺失证据提示。提案的“BatchOptimize opt_freq＋单虚频”既不是充分科学判据，也应与平台规定的科研链流程区分；还须检查收敛、模式对应，连接性另行验证。[ORCA TS Searches](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_TS.html)

   **归属建议：**

   | 类型 | 实现归属 |
   |---|---|
   | A | 共用 preflight/submit 规范化之后的纯函数；Controller 投影提示，scheduler 不维护第二套规则 |
   | B | CCCP 科学分析产生指标和诊断；Services 管理分析/工件生命周期；Controller 读取已有结果 |
   | C | ACP 候选发布及证据投影返回状态；授权、CAS、来源绑定继续由既有服务管理 |

   只读 status/概况请求不能为生成 B 类提示而隐式启动计算或远端 fetch。

3. **P1-c：权威应是共用提交语义契约；jobs、CLI、Pydantic 都只是部分来源。**

   现有设计已明确：`submission_contract` 拥有语义，消费者 flag 表只是下界；`workflow_contracts` 负责发布。[submission_contract.py:14](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:14)。应沿这个方向补齐，而非选择 argparse 作为新的统一权威。

   | 工作流 | 必须核对的真实来源 |
   |---|---|
   | singlepoint / optimize / frequency | 共用 `method_flags` 转换、实际 level/default 解析、输入处理和 CLI 执行消费者 |
   | xtb_optimize | runner/remote 的 `levels.xtb_opt` 转换及任务请求语义，不能套用 ORCA simple 字段集 |
   | irc | API 来源引用解析、正式 TS product 验证、runner 转换及 IRC 请求契约 |
   | tsmode | API 频率来源发现、Hessian/模式映射检查、bundle 物化及执行请求契约 |

   simple 的共享转换实现实际上在 [method_flags.py:104](/ACPServer/AutoCalcPlatform/src/acp_services/runtime/method_flags.py:104)，xTB 有独立消费路径：[runner.py:1693](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1693)。现有 V1 Pydantic 模型中的 `input`、`method` 是自由字典，自动导出不能补出这些语义。[v1_schemas.py:314](/ACPServer/AutoCalcPlatform/src/acp/api/v1_schemas.py:314)。

   **两处具体修正：**

   - IRC 的公开来源引用、内部 `ts_source`、CLI `--ts-provenance-json` 不应混成一张可写字段表。`maxpoints` 当前从 `method` 或 `method.levels.irc` 消费。[v1_routes.py:2449](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2449)、[runner.py:1654](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1654)。
   - tsmode 应优先公开可选来源及模式的发现/引用契约，不能只告诉 agent 内部 bundle 长什么样。当前 API 会解析 `source_job_id`、`entry_id` 并检查 Hessian。[v1_routes.py:2136](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2136)。

   验收应沿着“**公开示例→preflight→submit 校验→生效 JobSpec/argv**”核对默认值、别名优先级、非法字段和来源绑定，并覆盖 local/remote。只验证 schema 接受样例不足以宣称 complete。

   **更深的根因是：缺少可执行的跨工作流衔接契约，而不仅是六张参数表。** 当前 task 指南仍声明自动发布未开放、IRC 提交键未契约化；preflight 的计划摘要仍是单 workflow、`steps:1`。[guidance_pack.py:740](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:740)、[v1_routes.py:8883](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:8883)。

   不必立即建一个自动决定全部科学步骤的“大编排工具”。更直接的是补齐上位计划已有的显式操作：来源发现→候选提案/合法发布→可消费引用→Batch→验证，并返回下一步所需输入及阻塞原因。多个显式命令也可以自动连续执行。[上位计划:131](/ACPServer/AutoCalcPlatform/docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md:131)。

4. **建议优先级改为：P0-a → P1-c及预检一致性 → 精简A/C → 校准后的B；P2-d改设计。**

   - **P0-a 第一**：先让已有契约确实能被读到，同时解决按 workflow 查询仍携带大包的问题。
   - **P1-c 第二**：它直接消除四次 singlepoint 猜形状和 IRC 读源码的动机，收益比未经校准的科学阈值更确定。
   - **A/C 可小批并行落地**：A 仅报告几何事实；C 复用已有指南，补证据状态。
   - **B 后置**：先定义指标和标注集，再决定阈值。
   - **P2-d 改为结构化 fallback**：返回缺失能力、受支持入口及前置条件；CLI 模板仅用于明确支持的 CLI 场景。直接复制 `acp run` 可能换成客户端本地执行，也不能补齐 Controller 的来源、授权和恢复链。上位计划明确不能用 shell/QC 弥补接口缺口。[上位计划:690](/ACPServer/AutoCalcPlatform/docs/reference/next-change/ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030.md:690)。

5. **建议补入三项高价值改进。**

   - **预检与提交共享无副作用的来源校验。** 当前提交会调用 tsmode 来源准备，而 preflight 的验证路径未做同等检查。[v1_routes.py:2655](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:2655)、[v1_routes.py:8808](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:8808)。把“缺 Hessian、来源无效、模式不可解析”提前返回；冷远端缺材料时返回需要 prepare，不能假报可行。
   - **指南与契约做语义对账。** 已有具体漂移：task 指南说反应物侧压缩须为感知键，但当前 `bond_stretch` 解析允许非感知键的成键/断键距离扫描。[guidance_pack.py:718](/ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:718)、[atom_selection.py:87](/ACPServer/AutoCalcPlatform/src/cccp/analysis/pes/atom_selection.py:87)。应明确这是科学建议还是输入限制，并验证公开入口实际行为；仅更新版本或摘要发现不了这种矛盾。
   - **重做可归因的行为验收。** 提案自己记录 agent 已调用 `capabilities(workflow=tsmode)`，因此“完全不知道 workflow 查询通道”不是充分解释。[提案:16](/tmp/da_validation/PROPOSAL_round4.md:16)。冻结模型、harness、代码版本和任务条件，分别比较“只改发现”“再补契约”“再加 advisory”；统计首次有效预检、被丢弃字段、源码/目录探索、无变化轮询、重复计算和无依据结论。**216 次总调用数可作背景，不能单独证明改进有效。**
EXIT:0
