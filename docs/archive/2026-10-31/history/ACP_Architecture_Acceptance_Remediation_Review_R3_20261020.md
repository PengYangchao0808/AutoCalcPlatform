# ACP 架构验收整改实施计划复审（R3）

日期：2026-10-20。对象：[实施计划](../../../.omo/plans/acp-architecture-acceptance-remediation.md)，45 个 todo、7 个 wave、4 个最终验证任务。

- 计划 SHA-256：`9b072cf55d56098bc2987be6e511434668f6064084283ae337f643eb45084db6`。
- 对照：[R2 评审](ACP_Architecture_Acceptance_Remediation_Review_R2_20261020.md)、相关任务/结果契约及现有 worker、恢复、前置条件代码。

**结论：上轮主要问题已得到针对性修订，计划主体可以保留。尚有 1 项实质问题：todo 5 指定的“必需完成事实权威”覆盖不全且混淆前置条件与完成判据，应在实施该任务前修正。另有两处非阻断性的文字同步建议。无需重新拆分任务或扩大整改范围。**

本次仅进行计划及相关代码静态复核、依赖图和契约覆盖检查。没有运行产品回归、真实 QC、浏览器或集群验收；没有修改实施计划或业务代码。

## 1. 上轮五项意见的结果

| R2 意见 | 本版结果 | 依据 |
|---|---|---|
| RR1：冻结组贯穿远端与归档 | 计划层已解决 | todo 24/30/32 明确冻结组成员、manifest 最后提交、远端校验、停止后归档、同 attempt 重启保留有效组 |
| RR2：执行权及竞争失败者写权限 | 计划层已解决 | todo 7 先交付注入端口，24 提供持久 claim；worker 原子取得执行权，失败竞争者不得写权威回执，包含 A/B barrier 测试 |
| RR3：部分结果完整契约 | 状态/退出码/资格消费已明确；判据来源仍需纠正 | todo 5 给出完整输出矩阵、持久资格和实际消费者；28 增加 partial 发布规则；新引用的权威缺少主要任务，见下文 |
| RR4：历史探针与新契约冲突 | 计划层已解决 | verification strategy、todo 29/38 改为保留历史证据，正式测试适配 provider/普通入口，不再强求旧私有调用退出 0 |
| RR5：分层结论与无限 shortfall | 核心完成判据已解决 | todo 43、F1/F4、Success criteria 区分 batch completion 与产品门，未验证项不冒称 PASS，也不无限扩充代码 todo |

依赖检查结果：**45 个节点，无循环；正文与矩阵的依赖差异为 0；显式并行声明与依赖冲突为 0；todo 39 的前置覆盖全部实施任务。** 当前主链和 harness 开发先于候选冻结的安排继续保留。

## 2. 唯一需先修正的实质问题

### R3-1｜P1：todo 5 的完成事实来源不能覆盖默认执行链

**计划位置：第 224 行。** 新增文字指定 `cccp.calculation.requests.P2_TASK_CONTRACTS` 加 `runtime/step_requirements.py` 为 required-facts authority。

核对实际实现发现：

- `requests.py:1611` 的 `P2_TASK_CONTRACTS` 只有 CONFORMER_SEARCH、MD_SAMPLING、CLUSTERING、CENSO_REFINE、NMR_SHIELDING、XTB_PATH_SEARCH、ORCA_GRADIENT、XTB_CONSTRAINED_OPT 八类，内容为说明性字符串。
- 默认链涉及的 **SINGLEPOINT、OPTIMIZE、FREQUENCY、SCAN、IRC、CASSCF 六类均不在该表**。`xtb_optimize` 使用 OPTIMIZE，因此受影响的不止六个工作流。
- `step_requirements.py:69` 的表描述上游依赖；`evaluate_prerequisite` 对没有前置规则的类型直接返回 SATISFIED，对没有上游几何生产者的单点步骤也可以返回 SATISFIED。这是合理的前置条件行为，不能证明当前计算已经得到能量、完成所需方向或具备收敛事实。

若直接把这两处当完整完成校验，缺少条目的任务只能报错、默认通过或在 Services 侧临时补科学规则。前两种会阻断有效任务或误收不完整结果，后一种容易复制已有科学政策。

**建议替换该句并补充对应验收：**

> 必需结果事实以各 CCCP 任务核心、typed payload 契约及已有共享完成校验器为权威；`P2_TASK_CONTRACTS` 仅补充其已登记的 P2 任务，`step_requirements` 仅负责下游前置条件，不能代替当前任务的完成判据。在实现转换器前，为默认链涉及的全部 TaskKind 列出“已有判据/实现位置—完整与部分结果政策—对应测试”的映射。缺少映射不得隐式视为 satisfied；科学判定继续复用 CCCP 实现，Services 只转换与传递资格。

可直接复用、无需重新定义的依据包括：

| 场景 | 已有权威位置 | 本任务需要证明的行为 |
|---|---|---|
| singlepoint 缺少能量 | `src/cccp/calculation/tasks/singlepoint.py:199` | 缺少必需能量不能因 completed/complete=True 声明变成有效完成；已有正常成功行为保持 |
| CASSCF 完成与恢复 | `src/cccp/calculation/tasks/casscf.py:126` 的 `validate_casscf_completion` | 复用现有收敛事实、请求工件与完整性校验；新执行与恢复不能出现两套科学判据 |
| IRC 部分方向结果 | `src/cccp/calculation/tasks/irc.py:305` 及 IrcPayload | 按请求与现有部分结果政策判定资格，保留有效部分产物；不能拿“没有上游依赖”当作结果完整 |

这不是要求新增一套科学规则表。映射用于说明现有权威及其覆盖，避免把不同用途的现有表误当通用完成验证器。todo 5 的输出矩阵、序列化/重启测试和 todo 28 的发布政策均可保留。

## 3. 两处文字同步建议

1. **todo 7，第 244 行：限定写权限，不要笼统限定非零退出。** “make main exit non-zero ONLY for a worker that actually holds the execution right”容易被读成未持权的拒绝/重试必须退出 0。建议改成“只有持权者可生成权威失败回执；竞争失败者可以等待或返回独立的拒绝/重试退出结果，controller 不得据此推进当前 job 为 FAILED”。保持非零退出与修改任务状态的区别即可，无需新增任务状态。
2. **F3，第 662 行：同步环境缺失的表示方式。** F1/F4 和最终报告已允许如实记录环境 NOT_VERIFIED，但 F3 仍只列 PASS/FAIL。建议允许 F3 记录未运行场景及缺失前置，并明确其报告审查结论与场景通过结论分开。真实浏览器没跑时，D08/D09 仍不能关闭。否则在缺 Chromium 的环境中，最终验证的措辞仍会给执行者造成歧义。

这些是已有规则的表达同步，不构成额外的架构或实现缺陷。

## 4. 建议执行方式

先纠正 todo 5 的判据来源和覆盖映射，其余部分按现有 DAG 推进。后续把评审重点转到各任务的实际实现、原始反例和竞争/恢复测试，不必继续增加计划篇幅。计划评审通过与代码验收通过分别记录。
