# ACP 2.0 三轨执行计划审阅建议

审阅日期：2026-10-21。范围：计划的执行顺序、跨轨契约、架构边界、服务器实测、证据与发行闭环。

结论：三轨的职责划分和真实环境验收方向可行，任务已经细化到文件、命令、负例与交接；但存在确定的跨轨依赖环、同名回执协议不兼容、启动验收使用尚未交付入口等问题。建议先完成本文的阻塞项，再启动三轨实施。无需整体重写三份计划。

本文接受“上一轮 debug 已完成”作为实施前提，不重新裁定旧缺陷的关闭情况。这里检查的是：新计划是否能够带着最终修复版本启动，以及其交付与验收是否自洽。

## 1. 审阅输入与证据边界

| 计划 | 执行任务 | 最终审查任务 | 本次读取版本 SHA-256 |
| --- | ---: | ---: | --- |
| [A：CCCP 科学轨](../../../.omo/plans/acp2-plan-a-cccp-science.md) | 51 | 4 | `0a3ff6ec9fb5bb7fef6675d7b242bc956286b87ec3bb150db14b9650466e5c5c` |
| [B：平台运行轨](../../../.omo/plans/acp2-parallel-plan-b-runtime.md) | 33 | 4 | `a8bac281b0519556efdecf367efa67c520d032e3b51134780401cd858ebded6d` |
| [C：RPH / Workbench 轨](../../../.omo/plans/acp2-plan-c-rph-workbench.md) | 46 | 4 | `3da1bcb20c6ef9a668df3344e4d0e312b1cadea7d10f9bf0e16d7e3ddc0ab320` |

共 130 个执行任务和 12 个最终审查任务；三份计划的数字任务编号均连续。另对照了总方案、并行总索引、原三轨方案、服务器操作手册、架构整改报告及相关源码。

审阅时 Git HEAD 为 `3670790363c7a1959ad1dbe4b4297adec518952a`，工作区包含尚未提交的业务修复。这不是修复未完成的判断，但意味着从该 HEAD 创建干净 worktree 不会自动携带这些修复。

本次未修改业务代码和三份计划，未运行科学计算、MCP / DSH 实测、LSF 提交、浏览器验收或生产重启。只读 Ruff 检查的当前快照为：`ruff check src tests --output-format json --no-cache` 返回 1，发现 **1,065 项诊断，涉及 133 个文件**。这是修改 lint 门槛建议的依据，不是 ACP 2.0 新改动的检查结论。

## 2. 执行前应修正的阻塞项

### Q01：三轨都声明同一回执协议，但字段与场景命名不一致

位置：[A 的证据协议](../../../.omo/plans/acp2-plan-a-cccp-science.md#L107)、[B01](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L178)、[C T-06](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L225)。

A 使用 `started_at/finished_at`，B 使用 `started/finished`，C 使用 `started_on/finished_on`；A/B 用 `artifacts`，C 用 `evidence`；`command` 是否包含 cwd 也不同。B 要求 A/C 场景使用 `science:*`、`experience:*` 前缀，而 A/C 的冻结枚举仍为不带前缀的名字。三者却都写 `acp2.acceptance.v1`。

建议把 B01 改成三轨共同的第一项交接，而非三个 Harness 各实现一个“同名近似协议”：

1. B 发布唯一 `acceptance-result.schema.json`，冻结 Schema 方言、必填项、错误分类、场景标识和扩展规则。
2. 统一 `command: string[]`、独立 `cwd`、`started_at/finished_at`、`artifacts[{path,size_bytes,sha256}]`。额外字段放入明确的可扩展区域。
3. 可以保留各 Harness 的短场景 CLI 参数，但回执用 `(owner, scenario, phase, run_id)` 唯一标识；前缀与 owner 字段选择一种明确方案。
4. A/B/C 各提供一个合法样例和至少一个非法样例，由同一校验器交叉验证。三家样例未全通过前不冻结协议。
5. 把“JSON 能解析”与“Schema 有效、实例满足约束、跨文件引用可解析”分开验收；`json.tool` 只能证明第一项。

验收新增：同一个汇总器读取三轨真实形状的结果；错误时间字段、损坏摘要、未知必需版本和缺失 owner 均拒绝。不要等 H3 汇总时才发现协议不兼容。

### Q02：C35 与 B26 存在确定的最终签收循环

位置：[C T-46 / C35](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L625)、[B26b](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L488)。

当前为 `C35 等待 B26`，同时 `B26b 等待 C35`；B26a 还要求 A/C 阶段签收。按任务完成状态阻塞时，两边都无法先完成。

建议改为：

```text
B25a 发行候选包
  → A27b/A28、C33/C34 的独立安装与操作验证
  → A29、C35 各签本轨结果
  → B25b 汇总安装事实
  → B26a 独立复现
  → B26b 最终汇总与 H7
```

若 B25b 本身需要 C35 之前的证据，只让它依赖 C33/C34 的原始安装结果，不依赖完整 C35 签收；C35 也不得反向等待 B26。为每个外部依赖写明“需要文件/接口可用”还是“需要整个任务完成”，禁止用模糊的 `B26` 代替产物。

### Q03：B04 的验收先使用 B05 才会创建的 MCP 启动入口

位置：[B04 验收](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L213)、[B05](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L218)、[B06](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L228)。

B04 要执行带 `--launch` 的入口探测并启动两个适配器；正式 stdio 入口和 launch 文件到依赖 B04 的 B05 才交付。B05 的探针又要求真实 `acp_capabilities` 调用，而完整后端映射在 B06。后一点可以靠提前交付最小 capabilities 映射解决，但计划未写清。

建议顺序：

```text
B04：唯一 Controller + HTTP/CLI client + 最小 capabilities
  → B05：stdio、launcher、握手和只读 capabilities
  → B06：全部正式 use-case 绑定
  → B04b / H2：跨入口拓扑的综合验收
```

B04 初次关闭只检查 Controller 独占、HTTP/CLI 和第二 Controller 拒绝；把两个 MCP 适配器启动/退出检查后移。C T-11 明确消费 B05 的 launch 和最小只读映射，完整科研调用再依赖 B06。

另外，B06 与 B12 都写了实现 prepare/analyze 路由。建议 B06 负责端口与实现交付，B12 只做真实工件和负向矩阵验收；或者把 B12 分成前置实现与后置实测，避免首次闭环已经要求 read，而 prepare 的真实实现仍在后面。

### Q04：最终 debug 基线和首个集成候选的形成时间需要前移

位置：[B00](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L168)、[B14a](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L308)、[C 候选绑定](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L79)、[C T-01](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L175)。

B00/C T-01 将 `3670790...` 写成必须满足的启动基线。若最终 debug 修复仍在当前未提交工作区，照此新建 worktree 会从旧文件内容启动。应把该 SHA 降为历史参照，另指定实际最终修复候选：完整提交 SHA，或可重建且有摘要的明确补丁清单。不能只保存 dirty 文件名就认为修复已进入新 worktree。

C 又要求所有 live 场景来自 B 冻结的集成候选，但 B14a 才首次明确执行 A/B/C 集成冻结，且它排在 B13、真实 Local/LSF 与 DSH 恢复测试之后。应增加 H2 测试候选、P1 测试候选的前置构建步骤，B14a 改为汇总/晋级候选，而不是第一次得到可测集成代码。

候选记录至少区分服务端、客户端、Harness 三者的 revision，以及契约和环境摘要。允许它们不同版本时记录实际组合；要求同一版本时检查真实加载路径。仅给结果填一个 `candidate_revision` 不能证明执行的就是该代码。

## 3. 高优先级的实施与验收修订

### Q05：B 只承诺 P0/P1，A/C 则按全程交付，需统一批次口径

位置：[B 执行承诺](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L126)、[B 最终审查](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L498)、[C 阶段门](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L99)。

B 要在 P2–P5 各门重新取得用户 go-ahead；C 则写这些阶段已经获准直接执行，A 也有完整实现承诺。B 最终审查同时要求全部任务完成。若三轨按各自正文执行，A/C 可能等待一个主动暂停的 B，P0/P1 批次也缺少独立的完成判据。

建议统一成以下一种方式并同步三轨：全程计划按证据门自动推进，把许可/发布等真正需要决策的事项单列；或者明确第一批三轨都只关闭 P0/P1，P2–P5 是后续路线图，每批有自己的范围、理想状态与审查门。不要让“进入下一阶段”和“宣布本批完成”采用不同范围。

### Q06：B08 的幂等事务还需要覆盖文件分配和提交启动窗口

位置：[B08/B09](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L248)、[实际 submit](../../../src/acp/scheduler/manager.py#L902)。

当前 submit 在 `_allocate_work_dir` 后才 `store.create`，随后启动提交线程。把幂等行和 job 行放入一个事务是必要的，但不足以证明重放没有额外目录、预算预留或漏派发任务。`ReservationLedger`/quota 也有自己的连接和事务，不能因为数据库文件相同就假设操作天然原子。

建议 B08/B09 增加一份共享提交协议，明确授权、请求归一化、幂等领取、预留、目录分配、任务持久化、持久派发意图与实际执行的顺序。可使用共享事务，或显式可恢复的分阶段协议；关键是每个跨数据库/文件系统/进程窗口都有恢复与补偿责任。

新增四类故障验收：预留后入库前崩溃；任务入库后提交线程启动前崩溃；本地启动或 bsub 已发生而响应丢失；Controller 重启后原 key 重放。除“一行 job”外，还要断言没有多余目录/预留/本地科学进程/bsub，且已接受任务不会永久停在未派发状态。继续复用已有 submission_id 对账，不另造 exactly-once 状态机。

### Q07：A-12a 的平台授权检查应移出 CCCP 生产科学模块

位置：[A todo 24](../../../.omo/plans/acp2-plan-a-cccp-science.md#L431)。

该任务允许把 job/attempt/receipt/generation/authorization 验证放进 `src/cccp/analysis/research/artifact_manifest.py`。路径安全、摘要和科学输入身份适合独立科学消费者；当前权限、撤销与“generation 是否仍为当前”则需要平台权威上下文。仅凭 manifest 自述授权无法完成后者，也不应让 CCCP 独立安装依赖平台身份系统。

建议 B Data 负责授权、冻结和导出不可变输入快照；A 的验收 Harness 检查平台绑定与导出资格；CCCP 生产模块只校验内容、角色、结构/方法/单位/映射等科学身份。平台字段可作为不透明 provenance 保存，不作为 CCCP 独立推断权限的依据。变更授权/替换工件的负例由 B 的边界测试证明。

### Q08：A 的两个内部依赖需要补边

位置：[todo 23/24](../../../.omo/plans/acp2-plan-a-cccp-science.md#L421)、[todo 43/44](../../../.omo/plans/acp2-plan-a-cccp-science.md#L621)。

todo 23 已验收真实 manifest 消费，但 manifest 消费层在 todo 24 才实现；当前二者没有前置边。增加 `24 → 23`，或先交付最小 reader 再验收真实工件。

todo 44 明确要测试 NMR 与 thermochemistry 两类 descriptor，而 NMR descriptor 来自 todo 43，二者却都只依赖 42。增加 `43 → 44 的联合测试`；如果要保留实现并行，把 thermochemistry 实现和联合验收的完成条件分开描述。

新增一份可机读的跨轨依赖清单：任务 ID、产物 ID、生产者、消费者、阶段、前置条件。由同一清单生成 Depends/Blocks/波次，检查不存在的任务、跨轨循环以及“验收时间早于前置产物”的情况。130 个任务继续手工维护三套依赖表很容易漂移。

### Q09：DA 的科学结论与 H4 的通过条件必须分开

位置：[A P2 门](../../../.omo/plans/acp2-plan-a-cccp-science.md#L193)、[A-20](../../../.omo/plans/acp2-plan-a-cccp-science.md#L561)、[C T-35](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L515)。

A 的阶段门说科学 inconclusive 可以接受，A-20 又明确负例不能代替所需真实正例。C T-35 在 3 小时 45 分钟后要求“记 inconclusive 并停止”，但 Harness 的 result 枚举只有 PASS/FAIL/NOT_VERIFIED。这里要避免执行层、科学层和阶段层混用状态。

建议冻结三项独立判据：

- 场景验收 `result`：是否完成规定操作、断言和证据交付。
- 科学 claim `status`：`supported/refuted/inconclusive/not_evaluated`，每项绑定验证器及输入。
- 阶段门：是否同时获得所需真实正例和诚实处理不足证据的反例。

“缺 IRC 时正确显示 inconclusive”可以是负例场景 PASS；它不意味着“真实有效 TS + 双向 IRC + 可比能量”的正例已通过。若预算内未得到有效正例，保存真实结果、保持对应门未关闭，允许继续无依赖工作。把首例结构、初始 TS、映射、理论水平、阈值和资源预检前移，避免把未知的科学搜索难度留到 H4 最后。

### Q10：C 的 H4 收口不要把自己的 PASS 当成生成前提

位置：[C T-36](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L525)。

T-36 通过 `p2-closeout` 生成/检查 H4，同时要求 H4 的 P2 九场景全部 PASS；九场景中包含 `p2-closeout` 自身。若收口回执及其摘要又进入自身证据，便需要不存在的先验结果或形成自引用。

建议输入只包含另外八个已完成场景及其 phase 结果；收口校验结束后单独写 `p2-closeout` 结果，最终索引再引用九个结果。任何工件都不把自己的最终摘要纳入摘要输入。

同样明确 `--phase approval` 的退出码评估该 phase，整体场景结果由 phase 聚合器生成。否则“approval 返回 PASS，但整个场景尚不得 PASS”会让命令退出码和结果文件互相矛盾。

### Q11：证据目录应统一且不可覆盖

位置：[A 证据目录](../../../.omo/plans/acp2-plan-a-cccp-science.md#L100)、[B 证据目录](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L90)、[C T-01](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L175)。

A 的多个拆分 todo 写同一个 `A-NN/result.json`，B14a/b 和 B20a/b/c 也共享阶段目录，存在覆盖风险；C 虽要求 append-only，却自行冻结 `acp2-c-<sha>` 标签和相对证据根，与 B 的统一候选根、A 的绝对路径要求不一致。

建议 B 唯一分配绝对 `$ACP2_EVIDENCE` 和集成候选 ID，采用 `owner/task/subtask/run_id/` 不可变目录；阶段汇总只记录引用，不覆写原始结果。开发 worktree 的 fixture 结果另记 producer revision，不能伪装成集成 live 结果。证据跨候选复用时登记输入/依赖/配置未变的映射，不复制旧 PASS 改标签。

### Q12：真实预算需要提交前拦截，且要统一计数单位和超时层次

位置：[C 预算表](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L101)、[C Harness](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L225)、[C LSF 场景](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L355)。

场景结束后的 `usage` 核对能审计超限，却不能阻止第 13 次模型调用或第 3 次任务提交。另有通用每场景 2 个任务、DA 总共 12 个任务、Recipe 多步骤之间的计数歧义；LSF 队列允许 24 小时，而场景超时只有 40 分钟，两种限制需协调。

建议：

1. 每次模型调用和 submit 前检查剩余额度；科学资源由 B 服务端强制执行，C 不另建权威预算账本。
2. 分开统计顶层 TaskRef、Recipe step、QC invocation、attempt、bsub、模型请求；明确失败、重放、恢复分别是否计数。
3. 明确总预算、阶段预算、场景预算及有记录的特例优先级，DA 的 12 是哪一层单位必须写清。
4. 分开队列等待、科学运行、客户端请求和整体验收期限；工具超时后保存引用并对账，不自动重提。安排有明确时间窗口的小型测试队列。
5. 提前留出证据整理和模型总结预算，科学计算停止后仍允许有界只读导出，不以“到 3h45m”为理由丢掉最终证据页。
6. 超时清理只取消本轮创建的任务，确认远端停止并保留对账记录；证据超额不能删除必需审计材料来得到 PASS。

### Q13：DSH 不绕平台执行，需验证模型实际可用工具

位置：[C T-05](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L215)、[C T-12](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L285)。

当前 boundary 脚本把 `orca|xtb|crest` 字面量也列为禁项，会误伤合法 provider 元数据和错误提示；而只扫描自有 bridge/research 代码，并不能证明 DSH profile 没有提供其他执行工具。

建议静态检查聚焦 import 与执行 API，允许科学软件名字作为数据出现；在科研 profile 中冻结模型可见工具全集，明确 shell/执行/SSH 工具策略，保存实际注册快照。新增运行时反例：研究提示或工具返回要求“直接执行 QC”时，没有可绕过平台的工具路径，且不会产生额外科学进程。保留官方 MCP 插件与八工具 allowlist 的正向测试。

## 4. 中优先级的可维护性与效率改进

### Q14：Schema 表单子集要与真实生产者约定，而非只通过 toy fixture

位置：[C T-38](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L545)、[C T-07](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L235)。

C 早期要解析 `$ref`，自动表单却将 `$ref/oneOf` 当作不支持示例；`range/nullable` 也没有说明是标准约束还是内部 UI 方言。这不意味着必须做完整 Schema 渲染器，但必须证明 A 实际输出能被 C 消费。

建议契约增加 `schema_dialect`、UI 支持子集版本与关键词清单；明确数值边界、可空、引用是否由 B/A 预展开。保留未知约束阻断提交的原则，并增加一次真实 A descriptor → B discovery → C 表单 → B 校验 → CCCP 请求的往返测试。仅对手写简化 Schema 生成表单不算完成生态接入。

### Q15：全库 lint 门槛需要先登记现状

位置：[B 每任务检查](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L93)、[B F2](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L503)。

当前审阅快照已有 1,065 项 Ruff 诊断，B 却要求每个代码 todo 全库零错误。照此要么所有任务都不能关闭，要么执行者跨 A/C 写域清理大量存量问题。

建议 B00 保存基线诊断；每任务检查自己修改的文件及不得新增诊断，阶段门仍执行现有架构/Services 护栏和约定回归。全库 lint 清零若确实要做，单列范围与负责人。不得通过关闭规则、修改 excludes 或放宽既有架构守护来使其通过。

### Q16：最终验证应减少重复真实提交，最后再做总体签收

位置：[A 最终审查](../../../.omo/plans/acp2-plan-a-cccp-science.md#L711)、[B 最终审查](../../../.omo/plans/acp2-parallel-plan-b-runtime.md#L498)、[C 最终审查](../../../.omo/plans/acp2-plan-c-rph-workbench.md#L635)。

三轨都要求最终 F1–F4 并行，又各有真实 QA。共享 Controller、DB、预算和集群测试窗口时，可能重复提交或让一个故障注入干扰另一个场景。

建议 F1/F2 对冻结候选独立并行；F3 由 B 排定资源窗口，A/C 参与同一真实运行并各审本轨证据；F4 最后读取全部审查与实测结果。独立复现仍保留，但明确哪些场景必须重新执行、哪些只验证已有不可变回执，不把复用证据误称独立运行。

### Q17：把 B 的串行关键路径和跨轨交接产物写出来

B 承担共享文件、Controller、协议、幂等、准入、真实 LSF、集成和发行，实际吞吐受 B 的串行链约束，不能按三个 Agent 估计三倍提速。A/C 等外部条件时应继续纯契约、fixture、组件和安装文档工作。

建议给每个交接增加：生产者任务、最早可消费版本、Schema/样例摘要、待决问题、接收确认、下一次集成窗口。分别登记实现耗时和模型/队列/独立复现等待时间。删除“每波必须 5–8 个任务”这类与现有串行门矛盾的模板要求；波次应由真实依赖决定。

## 5. 针对本服务器实测与操作手册的补充清单

环境配置已经包含在计划中：B07 管 Python、科学软件和 LSF，C T-04/T-08 管 Node/pnpm/DSH 与 Chromium，C T-45 负责安装和排障手册。建议不再泛增“环境说明”，而是为每个环境项补齐以下可复现闭环：

| 实测层 | 必须保留的验证 | 建议新增的边界 |
| --- | --- | --- |
| 环境启动 | 新 shell 激活、精确版本、包加载路径、隔离数据根与 Controller | 所有项目测试统一使用对应角色的冻结解释器；避免一个 editable venv 被后一次安装切到另一 worktree |
| MCP 进程 | 真实子进程、协议顺序、stdout/stderr、EOF、错误版本 | 不只测试 handler；逐个确认生产 use-case 绑定、输入输出 Schema 与副作用 |
| DSH / 模型 | 官方插件、工具注册、真实模型请求和真实工具返回 | Web/headless 各确认实际配置与工具全集；无模型忙等科学计算；错误 provider 不静默回退 |
| Local | 真实 singlepoint、optimize、frequency 各自结果 | 核数/内存/配置实际传入 worker；重启后能读到完整科学正文，而非仅存在 manifest |
| LSF | 实际提交、release/submission、远端产物与拉取摘要 | 冷缓存 prepare/read；错误远端定位；queue wait 与运行限时；确认停止后才关闭取消场景 |
| 数据读取 | 显式 prepare、只读 read、generation/digest | 同时测试缓存驱逐后的重建、跨 attempt 替换、manifest 辅助产品与撤权；不能只有热缓存正例 |
| 浏览器 | 真 Chromium、真实页面、同一任务 ID/attempt | 请求乱序和切换任务后的旧响应、失权后的缓存隔离、结构/能量/频率实际内容 |
| 发行安装 | 干净目录、无旧 checkout、实际发行包 | 不靠 PYTHONPATH 或 editable 安装通过；历史项目只读、模型资产/notice 与文档命令复跑 |

快速 singlepoint 不适合兼任所有 pause/cancel 竞争场景：B11 可专注真实远端提交与结果读取，控制竞争放到 B13 的独立、有界且单独计费 fixture，避免任务在控制调用前已经自然结束导致偶发失败。

操作手册的每组命令统一写清：执行目录、环境变量来源、完整 argv、预期输出或结构化错误、最长等待、失败后如何定位、如何停止及确认清理。最终文档使用实际测试路径或明确的参数化脚本，不用单纯扫描占位符来代替可执行性验证。

## 6. 建议修订顺序与首批启动条件

1. B00 重新登记最终 debug 基线，统一候选与绝对证据根；A00/C00 只消费该交接。
2. B01 冻结唯一回执协议，A/C 样例通过同一校验器。
3. 修正 C35/B26 环、B04/B05 验收倒置、A 两处缺失依赖；同步总索引与逻辑任务到拆分 todo 的映射。
4. 形成 H2 可测集成候选，完成真实 MCP、DSH、模型、浏览器环境验证；A 并行完成静态能力与 fixture。
5. 明确 B08/B09 崩溃与恢复协议，准备单次注册的真实 fixture 和预算；形成 P1 候选后执行 Local/LSF、工件与同任务三入口验证。
6. H3 后按统一批次口径推进 P2–P5；DA 正例、负例及总体门分别签收，最终由 B 单向汇总 H7。

首批可以启动的最低条件：最终修复基线可重建，三轨证据协议互通，跨轨依赖无环，最小入口的交付早于验收，真实测试预算/隔离路径/Controller 所有权明确。这五项属于执行准备，不需要先实现完整 ACP 2.0。
