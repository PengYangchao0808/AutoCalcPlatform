# ACP MCP 第 5 轮：执行层"最后一公里"缺口修复计划

日期：2026-10-31。状态：**计划（已经 external 评审 astra，只读核实基线 main `eb04ba87`）**。
来源：真实测试 agent 的 8 组使用吐槽 + 本机源码核实 + astra consult 评审（全文：`reports/acp2_mcp_guidance_20261030/astra_round5_review.md`）。
上一轮计划：`ACP_MCP_Discovery_Bounded_Plan_20261030.md`（A/B/C 已合并上线）。

## 1. 总判断

契约与文档层已较完整（agent 认可：任务配方指南、preflight 零副作用、`ts_validation`、`normal_modes`、错误字段规范）。剩余问题集中在**执行层与接口的接线**：静默覆盖、校验不一致、承诺路径不存在、参数被接受却无效。astra 的评级口径：

- **P0**：下一轮真实计算前应止损的静默科学语义错误；
- **P1**：下一批完成的契约或闭环缺口；
- **P2**：后续体验与能力增强。

**astro 同意用户的三个方向，但顺序为：①与⑥参数止损 → ②共享校验 → ③合法自动发布闭环 → ④⑤⑦体验与解释；⑧随前两项落最小守护。**

## 2. 逐条评级与最小修复（含 astra 的定位纠正）

| # | 问题 | 评级 | astra 的定位纠正 / 最小修复 |
|---|---|---|---|
| ① | 坐标静默覆盖（coordinate 被 coordinates[0] 顶掉） | **P0** | 不是 `scan_method_flags` 的问题：PES 由 `pes/contracts.py:519` 选择 `coordinates or (coordinate,)`、`pes/scan.py:330` 取第一个。**规范化后检查一致性、不合并**：只有 `coordinate` → 单元素列表；两者都有 → 要求规范化定义（原子/范围/单位/点数/显式值）完全一致，否则 422；只有 `coordinates` → 作为新增兼容能力明确开放（当前 HTTP 模型仍强制 `coordinate`，`v1_schemas.py:1555`）。preflight 与 submit 都须在副作用前拒绝。验收：单字段/双字段一致/双字段冲突/多坐标合法同步四组 |
| ⑥a | `step` 被契约接受但执行无效 | **P0** | 契约允许、CLI 接受（`cli.py:235` 已注明"兼容参数，无运行效果"）、ORCA 接口不消费（`orca.py:3505` warning）。最小修复：新提交契约拒绝显式 `step`；同步修 discovery 与 CLI；内部 `irc/source.py:43` 的默认注入不能一并拒掉。**不猜 `%irc Step` 映射**（ORCA 6.1 区分 Scale_Init_Displ 等，无依据等同） |
| ⑥b | 端点发现与完成判定 | **P1** | 需拆三事：发现 / 逐方向收敛 / 端点可信性。"有轨迹却 FAILED"尚不能认定误判。真实 ORCA 输出 fixture 区分：未解析到 vs 找到但未收敛。轨迹末帧保留为 `last_observed_geometry`（带方向/帧号/收敛证据），未验证不得升级为正式 endpoint。状态：JobStatus 不变，科学结果层报 partial/inconclusive 与逐方向结果；端点已收敛但关联不成立 → 科学命题 refuted，不是解析失败 |
| ② | preflight feasible 但 submit 缺字段 | **P1** | preflight 已调 `validate_workflow_submission`；缺口是 submit 的 `_prepare_bond_scan_input`（`v1_routes.py:1730`）才做必填校验。**抽出共享只读"解析—规范化—校验"函数，prepare 消费其结果**；不把整个 `_prepare_*` 搬进 preflight（含来源解析/路径固定/材料准备）。无法核实来源 → `not_verified/needs_prepare` + `validated_scope`。parity 口径：同请求同快照下静态错误的 code/field/rule 一致；不要求容量/授权/远端可用性永远一致 |
| ③ | PES review 不在 MCP 工具面 | **P1** | 不能只包一层 HTTP：writer 固定 `selection_source="manual"`（`review.py:529`），Batch loader 只接受 `manual/manual_frame`（`batch/loaders.py:102`）。**最小工具面拆两个：只读查询 + 授权发布**。发布必须验证 source task/attempt、profile/geometry digest、预期 selection revision、真实 selector/actor、服务端授权依据、持久幂等键（同键同内容返原结果、同键异内容冲突）。writer/loader/Controller/MCP 共同完成。推荐只能形成"待发布提案"。未完成前在 capability/guidance 明示"当前连接不可执行"，不放伪可用工具 |
| ④ | BatchOptimize 单结构 | **P1（发现/校验）** | 约束在 `calculations/batch/engine.py`（不是 pes/engine）；调度器固定 single_flat（`runner.py:1544`），引擎本身有多结构布局。最小修复：机器可读的每任务结构数限制、两个候选拆两任务的示例、引用输入解析后的基数检查（当前只数 items）；错误可带拆分模板但不自动提交。**多结构任务不做**（attempt/取消/恢复/计量/部分失败，留 P2 独立设计） |
| ⑤ | 推荐空转 | **P1（说明）** | 不是算法漏峰：执行路径直接 `ts_recs, int_recs = [], []`（`pes/scan.py:481`）+ 发布层再置空（`outputs.py:94`），manual-only 策略。最小修复：工件与 discovery 明确 `status=disabled` + 稳定原因码（如 `manual_only_policy`），区分 disabled/未运行/不支持/输入不足/失败/无候选。算法启用 P2（必须同时修执行与发布，推荐≠发布≠已验证 TS） |
| ⑦ | 小烦项 | **P2（部分 P1）** | 路径脱敏是设计：可加受限相对 locator（不混远端/cache）；prepare pending 是 operation 状态非"文件不存在"：本地就绪快速路径须走同一身份/digest/generation/发布检查；task_id/job_id 恒等是兼容别名：文档注明；stage_label null 先核实再判；**两个 running 与 max_running=1 归因有误：`max_running` 已标 unused，真正限制是 `registry.local.max_jobs`（`manager.py:448/5379`）→ P1 修配置发现**；幂等键由客户端生成并持久保存（不能服务端随机，也不能按内容哈希合并用户有意重复计算） |
| ⑧ | 契约键—消费者对账 | **P1** | 仅静态映射不够（step 有 runner/任务层"消费者"仍无效）。守护须追到可观察效果或明确拒绝：字段 → `execution_effect / normalization_alias / metadata_only / rejected / legacy_noop` + 条件 + 最终消费者 + 行为测试 ID；科学控制参数用非默认值探针验证真实 argv/TaskRequest/ORCA 输入；别名验冲突、拒绝项验副作用前失败。先覆盖 PES/scan 坐标与 IRC 参数 |

## 3. astra 指出的更根本问题（必须并入各 PR）

1. **IRC 首帧冒充通路（P0 级源码可达风险）**：`_read_endpoint_geometry` 调通用 `read_xyz`（只读第一帧）→ 结果进 `final_geometries` → 绕过任务层对多帧文件的拒绝（`orca.py:1227` → `file_io.py:54` → `orca.py:3567`）。需反例 fixture：多帧文件不能因中间转换被当作正式端点。
2. **review revision ≠ 事务 CAS（PR4 必含）**：writer 先读 revision 再分别写结构/manifest/review，attempt 取 `current_revision + 1`（`review.py:506/541`）。新 Agent 接口须区分 source execution attempt 与 selection revision，并验证并发发布/崩溃恢复/远端部分写回。
3. **缺少 requested/effective/executed 回执**：preflight 应展示规范化坐标、实际方法、点数、执行模式、未生效字段；执行产物回报同一身份。当前扫描用列表首坐标却以 `req.coordinate.n_points` 判完成（`scan.py:484`）——双重权威同时影响"扫错"与"结果解释"。
4. **跨入口行为验收**：算法函数/执行结果/发布 JSON 各自通过不代表链路可用；验收须断言"真实工具可达、参数实际生效、候选来源不丢失、Batch 可合法消费、科学结论仍有独立证据"。

## 4. 六个可独立审查的 PR（astra 建议边界）

| PR | 边界与完成条件 |
|---|---|
| 1 | 坐标规范化、冲突拒绝、PES/scan 共享只读校验；原错误 payload 在 preflight/submit 一致拒绝 |
| 2 | IRC step 契约止损、内部默认清理、⑧最小守护；不存在"接受科学参数但静默无效" |
| 3 | IRC 输出解析、端点来源与逐方向诊断；真实输出 fixture 验证，保留未收敛拒绝；含首帧冒充反例 |
| 4 | PES 发布后端：来源、授权、版本、幂等、并发与 Batch loader 联动；含事务 CAS 与 remote 部分写回 |
| 5 | 依赖 PR4 的 Controller/MCP 查询与发布工具、指南、真实 SDK 链；PR4/5 合起来才算自动闭环交付 |
| 6 | Batch 拆分示例、推荐 disabled 原因、有效容量与状态/prepare 体验；算法启用与多结构任务另立项目 |

## 5. 不做项（本轮明确排除）

- 多结构 Batch 任务（架构级：attempt/取消/恢复/计量/部分失败）。
- 推荐算法启用（须同时修执行与发布路径，另立项）。
- `%irc Step` 猜测映射（缺 ORCA 版本/物理含义/单位依据）。
- 把轨迹末帧升级为正式 endpoint（只能作 `last_observed_geometry`）。

## 6. 验收总口径

1. **坐标**：单字段/双字段一致/双字段冲突/多坐标合法同步四组 preflight+submit 一致。
2. **parity**：同一 payload 的静态错误在 preflight/submit 的 code/field/rule 一致；错误路径无写入/派发。
3. **参数生效**：每个契约科学参数用非默认值探针到真实 argv/TaskRequest/ORCA 输入；未支持项在副作用前拒绝。
4. **IRC**：真实输出 fixture（发现/收敛/几何一致/关联）+ 多帧首帧冒充反例 + `last_observed_geometry` 标注。
5. **PES 发布**：幂等（同键同内容返原、异内容冲突）、并发、崩溃恢复、远端部分写回；MCP 工具可达且来源不丢。
6. **Batch/推荐**：拆分模板可执行；推荐工件 `status=disabled` + 原因码，不误导。
7. **状态/容量**：`registry.local.max_jobs` 配置发现与有效容量一致；live_status 字段缺陷先核实再改。
