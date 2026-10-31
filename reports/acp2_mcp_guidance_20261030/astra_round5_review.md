codex_consult: consult in /ACPServer/AutoCalcPlatform (timeout 1500s)
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
session id: 01a1905d-799a-74a1-8574-269e3e26df82
--------
user
... (read-only exploration transcript omitted) ...

codex
核实基线：当前 `HEAD = main = eb04ba875d4fdd2007c6686cba2c49f42df05ec6`，工作区干净。以下是只读源码评审；没有重跑真实 QC，因此区分“代码可确认的缺陷”和“仍需原始输出复现的归因”。

**总体判断：同意用户的三个修复方向，但不完全同意排序和归因。** ①应优先；⑥中参数无效及端点可信性问题应提前到③之前。⑤不是已证实的推荐算法漏峰，而是当前执行、发布路径主动禁用了推荐。

这里将 **P0** 定义为“下一轮真实计算前应止损的静默科学语义错误”，**P1** 为下一批完成的契约或闭环缺口，**P2** 为后续体验与能力增强。

1. **① 坐标静默覆盖：真实缺陷，P0；文档定位需要纠正。**

   `scan_method_flags` 处理 `scan_coordinates/coordinate`；本例 PESsearch 的 `coordinate/coordinates` 实际由 [`pes/contracts.py:519`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/contracts.py:519) 选择 `coordinates or (coordinate,)`，执行取其第一个坐标，见 [`pes/scan.py:330`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:330)。修 `scan_method_flags` 本身不能解决这个 PES 案例。文中的数组也应理解为 `atoms` 简写，实际接口要求坐标对象。

   **最小修复：规范化后检查一致性，不合并坐标。**

   - 内部只保留一份有序坐标列表。
   - 仅有 `coordinate`：转换为单元素列表。
   - 两者都有：要求规范化后的 `coordinate == coordinates[0]`，比较完整定义，包括原子顺序、范围、单位、点数和显式值；不一致即拒绝。
   - 仅有 `coordinates`：作为新增兼容能力明确开放；当前 HTTP 模型仍强制要求 `coordinate`，见 [`v1_schemas.py:1555`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_schemas.py:1555)。
   - 同时检查 scan 的 input/method、别名之间的冲突，但保留两个工作流各自的 wire 语法。

   preflight 与 submit 都应在产生副作用前返回结构化 `422 input_invalid`。历史记录照旧读取；新提交、编辑重算遇到冲突须修正，不能重写旧实验事实。

   **风险：** 自动合并会增加约束、改变科学问题；仅检查“列表包含主坐标”仍可能改变横轴和主坐标。验收至少包含：单字段、双字段一致、双字段冲突、多坐标合法同步四组。

2. **② preflight 假阳性：真实缺陷，P1；不是完全没有共享校验。**

   preflight 已调用 `validate_workflow_submission`，见 [`v1_routes.py:9312`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:9312)。缺口是 submit 还调用 `_prepare_bond_scan_input`，其中才执行必填 `coordinate` 等校验，见 [`v1_routes.py:1730`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:1730)。当前通用契约的 `required_input_any` 不能代替具体模式的必填条件。

   **最小修复：抽出共享的只读“解析—规范化—校验”函数，prepare 消费其结果。** 不要把整个 `_prepare_*` 搬进 preflight：里面混有来源解析、路径固定和材料准备。

   可以做有界、授权后的本地读取；不能触发远程抓取、快照写入、预留、任务创建或 QC。无法核实的来源返回 `not_verified/needs_prepare`，并提供 `validated_scope`、未验证项和契约版本。当前 IRC/tsmode 已有只读来源检查，应扩展复用，见 [`v1_routes.py:9053`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:9053)。

   **parity 的验收口径：** 相同请求、相同来源快照和配置下，静态错误的 `code/field/rule` 一致；不能要求两个时间点的资源容量、授权有效性和远端可用性永远一致。测试需覆盖“preflight 可通过、submit 却缺字段”的原案例，并断言错误路径没有写入或派发。

   **风险：** 复制第二套验证器，或为了“全验证”破坏 preflight 零副作用；仅增加 scope 字段不能代替补齐可静态判定的校验。

3. **③ PES review 不在 MCP：真实闭环缺口，P1；不能只包一层 HTTP。**

   基础工具确实没有 review，见 [`mcp.py:383`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:383)。但现有 writer 固定写 `selection_source="manual"`，Batch loader 只接受 `manual/manual_frame`，见 [`review.py:529`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:529)、[`batch/loaders.py:102`](/ACPServer/AutoCalcPlatform/src/acp/calculations/batch/loaders.py:102)。

   **建议最小工具面分成两个：只读查询、授权发布。** 查询包含当前 revision、候选和历史；发布负责提交选择。restore 可后续增加，或作为发布的显式变体；它应生成新 revision，而不是把版本倒退。读写分开便于权限、工具注解和重试语义保持清楚。

   发布必须携带并验证：

   - source task/attempt、profile/geometry digest、预期 selection revision；
   - 真实 selector/actor、服务端验证的授权依据；
   - 持久幂等键：同键同内容返回原结果，同键异内容冲突；回包丢失先查询原意图。

   writer、Batch loader、Controller 端口与 MCP 必须共同完成。推荐可以形成**待发布提案**，不能直接成为绕过授权的可消费结构。

   **风险：** Agent 被记成人工、旧 attempt 被消费、重复发布及远端写回部分成功。未完成前，应在 capability/guidance 明示“当前连接不可执行此发布动作”；`tools/list` 不应放一个伪可用工具。inline TS 输入可作为独立初猜，但不能冒充完成了 PES 来源发布链。

4. **④ BatchOptimize 单结构：主要是产品粒度限制，P1 的发现与校验问题；本轮不做多结构任务。**

   约束来自 **`calculations/batch/engine.py`**，不是吐槽引用的 `pes/engine.py`。引擎已有 batch 与 single_flat 两种布局，调度器固定使用 single_flat，见 [`runner.py:1544`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1544)。所以不能表述为“底层完全不支持多结构”。

   当前契约已经声明单结构，并对多个 inline items 提前拒绝，见 [`workflow_contracts.py:66`](/ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:66)、[`submission_contract.py:825`](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:825)。

   **最小修复：** 增加机器可读的每任务结构数限制、两个候选拆成两个任务的有效示例，以及引用输入解析后的基数检查。当前只数 `items`，不能保证 artifact 或多帧输入最终只有一个结构。错误可返回拆分模板，但不要自动提交。

   **风险：** 多结构任务牵涉 attempt、取消、恢复、资源计量和部分失败，不能仅移除 engine 的检查。反应物与 TS 能量比较也不要求同一任务；更需要一致的方法、能量类型和热化学校正。C 留 P2 独立设计，A/B 足以解决当前阻塞。

5. **⑤ 推荐为空：现象真实，算法归因错误。说明修复 P1，算法启用 P2。**

   当前实际路径直接执行 `ts_recs, int_recs = [], []`，并注明 exclusively manual，见 [`scan.py:481`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:481)。发布层又独立置空列表，见 [`outputs.py:94`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/outputs.py:94)。因此不能凭这个空文件判断 `_ts_recommendation_from_seed` 没覆盖 frame 15。

   **最小修复：** 在推荐工件和 discovery 中明确返回 `status=disabled`、稳定原因码，例如 `manual_only_policy`；区分 disabled、未运行、不支持、输入不足、执行失败和算法运行后无候选。“没有找到峰值”会是错误说明。

   **B/C 足以止住误导，但不足以交付自动研究链。** 后续启用算法必须同时修执行路径和发布路径，并维持“推荐不等于发布、不等于已验证 TS”。

   **风险：** 只修算法函数无效果；只解除置空可能暴露未经当前验收的旧策略。未来算法应使用连续有效邻域、收敛与约束质量、可比能量及边界信息，不能以“27 kcal/mol 全局最高”作为充分条件。

6. **⑥ IRC：必须拆成参数、端点发现、完成判定三个问题。**

   **`step` 无效已确认，P0 止损。** 契约允许它，ORCA 接口未消费；但 CLI 已明确写“兼容参数，无运行效果”，见 [`cli.py:235`](/ACPServer/AutoCalcPlatform/src/acp/cli.py:235)。问题是发现契约仍将其表现为普通数值控制项，见 [`workflow_contracts.py:838`](/ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:838)。

   最小修复是新提交契约明确拒绝未支持的显式 `step`，同步修发现和 CLI；历史兼容行为显式标为无效。注意内部 [`irc/source.py:43`](/ACPServer/AutoCalcPlatform/src/acp/calculations/irc/source.py:43) 也默认注入 step，不能只加拒绝规则而把内部正常请求全部拒掉。

   **不要猜映射为 `%irc Step`。** ORCA 6.1 文档区分 `Scale_Init_Displ`、`Scale_Displ_SD` 等控制量；没有依据把 ACP 的泛化 step 直接等同其中某个量。应先明确支持的 ORCA 版本、物理含义和单位，再增加端到端参数。[ORCA 官方 IRC 文档](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/irc.html)

   **“有轨迹却 FAILED”尚不能认定误判，P1 取样定位。** 任务先发现端点，再按逐方向完成情况过滤，之后才报 no endpoint，见 [`tasks/irc.py:253`](/ACPServer/AutoCalcPlatform/src/cccp/calculation/tasks/irc.py:253)。因此同一句错误可能意味着“没解析到”，也可能意味着“找到了但未收敛”，后者不能通过补一个文件名规则解决。

   文档“三路都要求单帧 XYZ”也不准确：`final_geometries` 路径直接接受坐标数组，见 [`tasks/irc.py:447`](/ACPServer/AutoCalcPlatform/src/cccp/calculation/tasks/irc.py:447)。

   **最小修复：** 用原始 ORCA 输出制作固定 fixture，区分 discovery、方向收敛、几何一致性和端点关联失败。轨迹末帧可保留为 `last_observed_geometry`，带来源文件、方向、帧号和收敛证据；未经验证不能发布成正式 endpoint。完整双向轨迹也不能一概取“最后一帧”代表两端。

   **状态建议：** 保持现有 JobStatus；在科学结果层报告 partial/inconclusive、逐方向结果和可用产物。若工作流承诺的端点缺失，FAILED 可以合理；若端点已收敛但与目标反应关联不成立，应记录科学命题 refuted，而不是解析失败。主要风险是为了减少 FAILED，把未收敛几何升格成有效端点。

7. **⑦ 小烦项：需要分开处理，不能全部列作 bug。**

   | 项目 | 判断、最小修复与优先级 |
   |---|---|
   | 路径脱敏 | 当前是明确设计，已有目录名投影，见 [`status_projection.py:214`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/status_projection.py:214)。P2 可增加受限相对 locator，注明所属存储位置；不能把远端/cache 路径混作本地 run-root 路径。 |
   | prepare pending/absent | 新 operation 当前确实以 QUEUED/ABSENT 初始化，见 [`operations.py:1197`](/ACPServer/AutoCalcPlatform/src/acp_services/data/operations.py:1197)。这是准备状态，不足以证明原文件不存在。P2 增加本地就绪快速路径，但必须走同一身份、digest、generation 和发布检查；不能仅凭 `exists()` 返回 ready。 |
   | task_id/job_id 冗余 | 当前投影有意恒等，见 [`status_projection.py:188`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/status_projection.py:188)。P2 文档注明兼容别名，客户端统一使用带 attempt 的 task_ref；不直接删字段。 |
   | stage_label=null | 缺阶段但有 metrics 时可以产生这种投影，见 [`job_status_view.py:112`](/ACPServer/AutoCalcPlatform/src/acp/api/job_status_view.py:112)。先核实事件/快照，再判缺陷；补未知原因和观测时间，不猜阶段。 |
   | 两个 running 与 max_running=1 | **归因有误。** `max_running` 已标为 unused，真正本地限制是 `registry.local.max_jobs`，见 [`manager.py:448`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:448)、[`manager.py:5379`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:5379)。P1 修配置发现与有效容量说明，再审计是否实际超配。 |
   | XYZ 与幂等键手填 | P2 改引用示例和客户端体验。幂等键可以由客户端生成并持久保存，一次意图生成一次；不能让服务端每次重试随机生成，也不能按内容哈希把用户有意重复计算合并掉。 |

8. **⑧ 契约键—消费者对账：值得做，P1；仅静态映射不够。**

   `step` 已经有 runner 和任务层“消费者”，只检查 `get/pop` 或源码出现会误判通过。守护必须追到**可观察效果或明确拒绝**。

   最小形态建议如下：

   - 从现有契约枚举字段，不另维护第二份允许键表；按 workflow、backend、模式和嵌套路径登记。
   - 每项声明：`execution_effect / normalization_alias / metadata_only / rejected / legacy_noop`，以及适用条件、最终消费者和行为测试 ID。
   - 科学控制参数用非默认值做探针，验证实际生成的 argv、TaskRequest、ORCA 输入或执行计划；只验中间 kwargs 不算完成。
   - 别名验证冲突处理；拒绝项验证在副作用前失败；metadata/noop 必须在 discovery 明示用途。
   - 先对 PES/scan 坐标和 IRC 参数做行为覆盖，再扩展现有工作流；不追求通用 Python 数据流分析。

   **风险：** 声明表变成自证清白、条件消费产生误报。降低风险的方法是绑定可执行的行为证据，而不是只绑定函数名。①还说明最终范围应包括 input 和嵌套参数，不能永久局限于 method 顶层。

**优先级与 PR 边界**

我同意保留用户提出的①②③，但建议顺序为：

**①与⑥的静默错误止损 → ②共享校验 → ③合法自动发布闭环 → ④⑤⑦体验与解释。** ⑧随前两项落最小守护，不等所有功能完成再补。

下一轮可作为**一个验收批次、六个可独立审查的 PR**，不建议一个大 PR：

| PR | 边界与完成条件 |
|---|---|
| 1 | 坐标规范化、冲突拒绝、PES/scan 共享只读校验；原错误 payload 在 preflight/submit 一致拒绝。 |
| 2 | IRC step 契约止损、内部默认清理、⑧最小守护；不存在“接受科学参数但静默无效”。 |
| 3 | IRC 输出解析、端点来源与逐方向诊断；真实输出 fixture 验证，保留未收敛拒绝。 |
| 4 | PES 发布后端：来源、授权、版本、幂等、并发与 Batch loader 联动。 |
| 5 | 依赖 PR4 的 Controller/MCP 查询和发布、指南、真实 SDK 链；PR4/5 合起来才算自动闭环交付。 |
| 6 | Batch 拆分示例、推荐 disabled 原因、有效容量与状态/prepare 体验；算法启用和多结构任务另立项目。 |

**文档遗漏、我认为更根本的改进**

- **IRC 存在比“端点漏发现”更危险的首帧冒充通路。** ORCA `_read_endpoint_geometry` 调通用 `read_xyz`，后者只读第一帧；结果又进入 `final_geometries`，可绕过任务层对多帧文件的拒绝。证据链是 [`orca.py:1227`](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/orca.py:1227) → [`file_io.py:54`](/ACPServer/AutoCalcPlatform/src/cccp/utils/file_io.py:54) → [`orca.py:3567`](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/orca.py:3567)。应按 P0 加反例 fixture：多帧文件不能因中间转换而被当作正式端点。这是源码可达风险，不代表已证明本次运行走了该路径。

- **review revision 检查不等于完整事务 CAS。** 当前 writer 先读 revision、再分别写结构、manifest、review；其 `attempt` 还取 `current_revision + 1`，见 [`review.py:506`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:506)、[`review.py:541`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:541)。新 Agent 接口必须区分 source execution attempt 和 selection revision，并验证并发发布、崩溃恢复与远端部分写回。不能把“单文件原子替换”当成整个发布组原子提交。

- **缺少可核对的 requested/effective/executed 语义回执。** preflight 应展示规范化坐标、实际方法、点数、执行模式及未生效字段；执行产物回报同一身份。当前扫描使用列表首坐标，却以 `req.coordinate.n_points` 判断完成，见 [`scan.py:484`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:484)，说明双重参数权威不仅会扫错，还会影响结果解释。

- **验收需要验证完整链中的信息没有被丢弃。** ⑤中算法函数、执行结果、发布 JSON 可以各自测试通过，却始终不给用户推荐。应增加跨入口与发布的行为验收：真实工具可达、参数实际生效、候选来源不丢失、Batch 可合法消费、科学结论仍有独立证据。测试数量和字段集合相等都不能替代这些断言。
tokens used
109,615
核实基线：当前 `HEAD = main = eb04ba875d4fdd2007c6686cba2c49f42df05ec6`，工作区干净。以下是只读源码评审；没有重跑真实 QC，因此区分“代码可确认的缺陷”和“仍需原始输出复现的归因”。

**总体判断：同意用户的三个修复方向，但不完全同意排序和归因。** ①应优先；⑥中参数无效及端点可信性问题应提前到③之前。⑤不是已证实的推荐算法漏峰，而是当前执行、发布路径主动禁用了推荐。

这里将 **P0** 定义为“下一轮真实计算前应止损的静默科学语义错误”，**P1** 为下一批完成的契约或闭环缺口，**P2** 为后续体验与能力增强。

1. **① 坐标静默覆盖：真实缺陷，P0；文档定位需要纠正。**

   `scan_method_flags` 处理 `scan_coordinates/coordinate`；本例 PESsearch 的 `coordinate/coordinates` 实际由 [`pes/contracts.py:519`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/contracts.py:519) 选择 `coordinates or (coordinate,)`，执行取其第一个坐标，见 [`pes/scan.py:330`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:330)。修 `scan_method_flags` 本身不能解决这个 PES 案例。文中的数组也应理解为 `atoms` 简写，实际接口要求坐标对象。

   **最小修复：规范化后检查一致性，不合并坐标。**

   - 内部只保留一份有序坐标列表。
   - 仅有 `coordinate`：转换为单元素列表。
   - 两者都有：要求规范化后的 `coordinate == coordinates[0]`，比较完整定义，包括原子顺序、范围、单位、点数和显式值；不一致即拒绝。
   - 仅有 `coordinates`：作为新增兼容能力明确开放；当前 HTTP 模型仍强制要求 `coordinate`，见 [`v1_schemas.py:1555`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_schemas.py:1555)。
   - 同时检查 scan 的 input/method、别名之间的冲突，但保留两个工作流各自的 wire 语法。

   preflight 与 submit 都应在产生副作用前返回结构化 `422 input_invalid`。历史记录照旧读取；新提交、编辑重算遇到冲突须修正，不能重写旧实验事实。

   **风险：** 自动合并会增加约束、改变科学问题；仅检查“列表包含主坐标”仍可能改变横轴和主坐标。验收至少包含：单字段、双字段一致、双字段冲突、多坐标合法同步四组。

2. **② preflight 假阳性：真实缺陷，P1；不是完全没有共享校验。**

   preflight 已调用 `validate_workflow_submission`，见 [`v1_routes.py:9312`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:9312)。缺口是 submit 还调用 `_prepare_bond_scan_input`，其中才执行必填 `coordinate` 等校验，见 [`v1_routes.py:1730`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:1730)。当前通用契约的 `required_input_any` 不能代替具体模式的必填条件。

   **最小修复：抽出共享的只读“解析—规范化—校验”函数，prepare 消费其结果。** 不要把整个 `_prepare_*` 搬进 preflight：里面混有来源解析、路径固定和材料准备。

   可以做有界、授权后的本地读取；不能触发远程抓取、快照写入、预留、任务创建或 QC。无法核实的来源返回 `not_verified/needs_prepare`，并提供 `validated_scope`、未验证项和契约版本。当前 IRC/tsmode 已有只读来源检查，应扩展复用，见 [`v1_routes.py:9053`](/ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:9053)。

   **parity 的验收口径：** 相同请求、相同来源快照和配置下，静态错误的 `code/field/rule` 一致；不能要求两个时间点的资源容量、授权有效性和远端可用性永远一致。测试需覆盖“preflight 可通过、submit 却缺字段”的原案例，并断言错误路径没有写入或派发。

   **风险：** 复制第二套验证器，或为了“全验证”破坏 preflight 零副作用；仅增加 scope 字段不能代替补齐可静态判定的校验。

3. **③ PES review 不在 MCP：真实闭环缺口，P1；不能只包一层 HTTP。**

   基础工具确实没有 review，见 [`mcp.py:383`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:383)。但现有 writer 固定写 `selection_source="manual"`，Batch loader 只接受 `manual/manual_frame`，见 [`review.py:529`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:529)、[`batch/loaders.py:102`](/ACPServer/AutoCalcPlatform/src/acp/calculations/batch/loaders.py:102)。

   **建议最小工具面分成两个：只读查询、授权发布。** 查询包含当前 revision、候选和历史；发布负责提交选择。restore 可后续增加，或作为发布的显式变体；它应生成新 revision，而不是把版本倒退。读写分开便于权限、工具注解和重试语义保持清楚。

   发布必须携带并验证：

   - source task/attempt、profile/geometry digest、预期 selection revision；
   - 真实 selector/actor、服务端验证的授权依据；
   - 持久幂等键：同键同内容返回原结果，同键异内容冲突；回包丢失先查询原意图。

   writer、Batch loader、Controller 端口与 MCP 必须共同完成。推荐可以形成**待发布提案**，不能直接成为绕过授权的可消费结构。

   **风险：** Agent 被记成人工、旧 attempt 被消费、重复发布及远端写回部分成功。未完成前，应在 capability/guidance 明示“当前连接不可执行此发布动作”；`tools/list` 不应放一个伪可用工具。inline TS 输入可作为独立初猜，但不能冒充完成了 PES 来源发布链。

4. **④ BatchOptimize 单结构：主要是产品粒度限制，P1 的发现与校验问题；本轮不做多结构任务。**

   约束来自 **`calculations/batch/engine.py`**，不是吐槽引用的 `pes/engine.py`。引擎已有 batch 与 single_flat 两种布局，调度器固定使用 single_flat，见 [`runner.py:1544`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/runner.py:1544)。所以不能表述为“底层完全不支持多结构”。

   当前契约已经声明单结构，并对多个 inline items 提前拒绝，见 [`workflow_contracts.py:66`](/ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:66)、[`submission_contract.py:825`](/ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:825)。

   **最小修复：** 增加机器可读的每任务结构数限制、两个候选拆成两个任务的有效示例，以及引用输入解析后的基数检查。当前只数 `items`，不能保证 artifact 或多帧输入最终只有一个结构。错误可返回拆分模板，但不要自动提交。

   **风险：** 多结构任务牵涉 attempt、取消、恢复、资源计量和部分失败，不能仅移除 engine 的检查。反应物与 TS 能量比较也不要求同一任务；更需要一致的方法、能量类型和热化学校正。C 留 P2 独立设计，A/B 足以解决当前阻塞。

5. **⑤ 推荐为空：现象真实，算法归因错误。说明修复 P1，算法启用 P2。**

   当前实际路径直接执行 `ts_recs, int_recs = [], []`，并注明 exclusively manual，见 [`scan.py:481`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:481)。发布层又独立置空列表，见 [`outputs.py:94`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/outputs.py:94)。因此不能凭这个空文件判断 `_ts_recommendation_from_seed` 没覆盖 frame 15。

   **最小修复：** 在推荐工件和 discovery 中明确返回 `status=disabled`、稳定原因码，例如 `manual_only_policy`；区分 disabled、未运行、不支持、输入不足、执行失败和算法运行后无候选。“没有找到峰值”会是错误说明。

   **B/C 足以止住误导，但不足以交付自动研究链。** 后续启用算法必须同时修执行路径和发布路径，并维持“推荐不等于发布、不等于已验证 TS”。

   **风险：** 只修算法函数无效果；只解除置空可能暴露未经当前验收的旧策略。未来算法应使用连续有效邻域、收敛与约束质量、可比能量及边界信息，不能以“27 kcal/mol 全局最高”作为充分条件。

6. **⑥ IRC：必须拆成参数、端点发现、完成判定三个问题。**

   **`step` 无效已确认，P0 止损。** 契约允许它，ORCA 接口未消费；但 CLI 已明确写“兼容参数，无运行效果”，见 [`cli.py:235`](/ACPServer/AutoCalcPlatform/src/acp/cli.py:235)。问题是发现契约仍将其表现为普通数值控制项，见 [`workflow_contracts.py:838`](/ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:838)。

   最小修复是新提交契约明确拒绝未支持的显式 `step`，同步修发现和 CLI；历史兼容行为显式标为无效。注意内部 [`irc/source.py:43`](/ACPServer/AutoCalcPlatform/src/acp/calculations/irc/source.py:43) 也默认注入 step，不能只加拒绝规则而把内部正常请求全部拒掉。

   **不要猜映射为 `%irc Step`。** ORCA 6.1 文档区分 `Scale_Init_Displ`、`Scale_Displ_SD` 等控制量；没有依据把 ACP 的泛化 step 直接等同其中某个量。应先明确支持的 ORCA 版本、物理含义和单位，再增加端到端参数。[ORCA 官方 IRC 文档](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/irc.html)

   **“有轨迹却 FAILED”尚不能认定误判，P1 取样定位。** 任务先发现端点，再按逐方向完成情况过滤，之后才报 no endpoint，见 [`tasks/irc.py:253`](/ACPServer/AutoCalcPlatform/src/cccp/calculation/tasks/irc.py:253)。因此同一句错误可能意味着“没解析到”，也可能意味着“找到了但未收敛”，后者不能通过补一个文件名规则解决。

   文档“三路都要求单帧 XYZ”也不准确：`final_geometries` 路径直接接受坐标数组，见 [`tasks/irc.py:447`](/ACPServer/AutoCalcPlatform/src/cccp/calculation/tasks/irc.py:447)。

   **最小修复：** 用原始 ORCA 输出制作固定 fixture，区分 discovery、方向收敛、几何一致性和端点关联失败。轨迹末帧可保留为 `last_observed_geometry`，带来源文件、方向、帧号和收敛证据；未经验证不能发布成正式 endpoint。完整双向轨迹也不能一概取“最后一帧”代表两端。

   **状态建议：** 保持现有 JobStatus；在科学结果层报告 partial/inconclusive、逐方向结果和可用产物。若工作流承诺的端点缺失，FAILED 可以合理；若端点已收敛但与目标反应关联不成立，应记录科学命题 refuted，而不是解析失败。主要风险是为了减少 FAILED，把未收敛几何升格成有效端点。

7. **⑦ 小烦项：需要分开处理，不能全部列作 bug。**

   | 项目 | 判断、最小修复与优先级 |
   |---|---|
   | 路径脱敏 | 当前是明确设计，已有目录名投影，见 [`status_projection.py:214`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/status_projection.py:214)。P2 可增加受限相对 locator，注明所属存储位置；不能把远端/cache 路径混作本地 run-root 路径。 |
   | prepare pending/absent | 新 operation 当前确实以 QUEUED/ABSENT 初始化，见 [`operations.py:1197`](/ACPServer/AutoCalcPlatform/src/acp_services/data/operations.py:1197)。这是准备状态，不足以证明原文件不存在。P2 增加本地就绪快速路径，但必须走同一身份、digest、generation 和发布检查；不能仅凭 `exists()` 返回 ready。 |
   | task_id/job_id 冗余 | 当前投影有意恒等，见 [`status_projection.py:188`](/ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/status_projection.py:188)。P2 文档注明兼容别名，客户端统一使用带 attempt 的 task_ref；不直接删字段。 |
   | stage_label=null | 缺阶段但有 metrics 时可以产生这种投影，见 [`job_status_view.py:112`](/ACPServer/AutoCalcPlatform/src/acp/api/job_status_view.py:112)。先核实事件/快照，再判缺陷；补未知原因和观测时间，不猜阶段。 |
   | 两个 running 与 max_running=1 | **归因有误。** `max_running` 已标为 unused，真正本地限制是 `registry.local.max_jobs`，见 [`manager.py:448`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:448)、[`manager.py:5379`](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:5379)。P1 修配置发现与有效容量说明，再审计是否实际超配。 |
   | XYZ 与幂等键手填 | P2 改引用示例和客户端体验。幂等键可以由客户端生成并持久保存，一次意图生成一次；不能让服务端每次重试随机生成，也不能按内容哈希把用户有意重复计算合并掉。 |

8. **⑧ 契约键—消费者对账：值得做，P1；仅静态映射不够。**

   `step` 已经有 runner 和任务层“消费者”，只检查 `get/pop` 或源码出现会误判通过。守护必须追到**可观察效果或明确拒绝**。

   最小形态建议如下：

   - 从现有契约枚举字段，不另维护第二份允许键表；按 workflow、backend、模式和嵌套路径登记。
   - 每项声明：`execution_effect / normalization_alias / metadata_only / rejected / legacy_noop`，以及适用条件、最终消费者和行为测试 ID。
   - 科学控制参数用非默认值做探针，验证实际生成的 argv、TaskRequest、ORCA 输入或执行计划；只验中间 kwargs 不算完成。
   - 别名验证冲突处理；拒绝项验证在副作用前失败；metadata/noop 必须在 discovery 明示用途。
   - 先对 PES/scan 坐标和 IRC 参数做行为覆盖，再扩展现有工作流；不追求通用 Python 数据流分析。

   **风险：** 声明表变成自证清白、条件消费产生误报。降低风险的方法是绑定可执行的行为证据，而不是只绑定函数名。①还说明最终范围应包括 input 和嵌套参数，不能永久局限于 method 顶层。

**优先级与 PR 边界**

我同意保留用户提出的①②③，但建议顺序为：

**①与⑥的静默错误止损 → ②共享校验 → ③合法自动发布闭环 → ④⑤⑦体验与解释。** ⑧随前两项落最小守护，不等所有功能完成再补。

下一轮可作为**一个验收批次、六个可独立审查的 PR**，不建议一个大 PR：

| PR | 边界与完成条件 |
|---|---|
| 1 | 坐标规范化、冲突拒绝、PES/scan 共享只读校验；原错误 payload 在 preflight/submit 一致拒绝。 |
| 2 | IRC step 契约止损、内部默认清理、⑧最小守护；不存在“接受科学参数但静默无效”。 |
| 3 | IRC 输出解析、端点来源与逐方向诊断；真实输出 fixture 验证，保留未收敛拒绝。 |
| 4 | PES 发布后端：来源、授权、版本、幂等、并发与 Batch loader 联动。 |
| 5 | 依赖 PR4 的 Controller/MCP 查询和发布、指南、真实 SDK 链；PR4/5 合起来才算自动闭环交付。 |
| 6 | Batch 拆分示例、推荐 disabled 原因、有效容量与状态/prepare 体验；算法启用和多结构任务另立项目。 |

**文档遗漏、我认为更根本的改进**

- **IRC 存在比“端点漏发现”更危险的首帧冒充通路。** ORCA `_read_endpoint_geometry` 调通用 `read_xyz`，后者只读第一帧；结果又进入 `final_geometries`，可绕过任务层对多帧文件的拒绝。证据链是 [`orca.py:1227`](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/orca.py:1227) → [`file_io.py:54`](/ACPServer/AutoCalcPlatform/src/cccp/utils/file_io.py:54) → [`orca.py:3567`](/ACPServer/AutoCalcPlatform/src/cccp/qc/interfaces/orca.py:3567)。应按 P0 加反例 fixture：多帧文件不能因中间转换而被当作正式端点。这是源码可达风险，不代表已证明本次运行走了该路径。

- **review revision 检查不等于完整事务 CAS。** 当前 writer 先读 revision、再分别写结构、manifest、review；其 `attempt` 还取 `current_revision + 1`，见 [`review.py:506`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:506)、[`review.py:541`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/review.py:541)。新 Agent 接口必须区分 source execution attempt 和 selection revision，并验证并发发布、崩溃恢复与远端部分写回。不能把“单文件原子替换”当成整个发布组原子提交。

- **缺少可核对的 requested/effective/executed 语义回执。** preflight 应展示规范化坐标、实际方法、点数、执行模式及未生效字段；执行产物回报同一身份。当前扫描使用列表首坐标，却以 `req.coordinate.n_points` 判断完成，见 [`scan.py:484`](/ACPServer/AutoCalcPlatform/src/acp/calculations/pes/scan.py:484)，说明双重参数权威不仅会扫错，还会影响结果解释。

- **验收需要验证完整链中的信息没有被丢弃。** ⑤中算法函数、执行结果、发布 JSON 可以各自测试通过，却始终不给用户推荐。应增加跨入口与发布的行为验收：真实工具可达、参数实际生效、候选来源不丢失、Batch 可合法消费、科学结论仍有独立证据。测试数量和字段集合相等都不能替代这些断言。
EXIT:0
