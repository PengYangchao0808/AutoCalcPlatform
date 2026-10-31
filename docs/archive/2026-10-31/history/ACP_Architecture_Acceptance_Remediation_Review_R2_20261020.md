# ACP 架构验收整改实施计划复审（R2）

日期：2026-10-20。评审对象：[修改后的实施计划](../../../.omo/plans/acp-architecture-acceptance-remediation.md)，45 个 todo、7 个 wave、4 个最终验证任务。

- 计划 SHA-256：`e0a3fb0a6a2c0b23c07cbf906c1fcae4e2189522b07491674509aa206b07a346`。
- 代码基线：`d5d85e7c804878edd0cedb6bce1d32af2d92ad83`。
- 对照：[上一轮评审](ACP_Architecture_Acceptance_Remediation_Review_20261020.md)、[整改要求](../../../reference/next-change/ACP_Architecture_Acceptance_Remediation_Plan_20261020.md)、[原验收报告](../../../../reports/architecture_acceptance_20261019/REPORT.md)，并核对相关实现与原始探针。

**结论：主体方案合理，上轮主要意见已经落实；仍建议修正以下 3 项 P1 协议问题和 2 项 P2 验收问题，再启动整批执行。无需重写计划或增加一轮架构设计，可直接修订现有 todo。**

本次是计划复审与代码静态核对，执行了依赖图检查，没有运行产品回归、真实 QC、浏览器或集群验收。下文是对计划可执行性的意见，不是修复后代码的验收结论。仅新增本复审记录，未改写原计划和业务代码。

## 1. 上轮意见复核

| 上轮意见 | 本版结果 | 依据 |
|---|---|---|
| R1：旧回执不得把当前任务写成 FAILED | 计划层已解决 | todo 26/27 区分 stale/foreign 与当前执行确认退出后的 corrupt/missing；前者零 CAS，后者合法 CAS |
| R2：终态 CAS 后的发布崩溃窗口 | 计划层已解决 | todo 26/28 将 pending 与终态一起持久化，增加重启补发布、确认前崩溃、rerun 隔离 |
| R3：latest-row 不等于当前资源 | 计划层已解决 | todo 14 改按权威 expected identity 定位，并测试 generation 1 迟到 |
| R4：prepare 必须在发布提交点校验 | 计划层已解决 | todo 15/16 要求显式 provider、区分两类 generation、覆盖 catalog/cache/download 和提交竞争 |
| R5：singlepoint 不得提前启用全部工作流 | 计划层已解决 | todo 25 保持 inactive，32 成为 29 的前置，默认集合与科学闭包分开 |
| R6：冻结组一致性及并发执行互斥 | 方向正确，尚需补齐协议 | todo 24 增加 staging/manifest/claim；远端与归档清单、竞争失败者的写权限仍需明确，见 RR1/RR2 |
| R7：release 必须对应实际加载代码 | 计划层已解决 | todo 25/30 改为不可变 release、完整 digest、实际子进程模块路径断言 |
| R8：依赖、共享写入与候选冻结 | 主要问题已解决 | 单一 DAG；harness 开发前移到 44/45；39 等待测试及 harness；区分 code SHA 与 evidence SHA |

实际解析依赖矩阵及各 todo 正文的结果：**45 个节点、无循环、正文与矩阵的依赖差异为 0、显式并行声明与依赖冲突为 0；todo 39 的前置覆盖全部实施任务。** 这比上一版有实质改善。该检查证明的是依赖关系一致，具体文件写权仍应在执行时按计划串行管理。

## 2. 本轮需修订的内容

### RR1｜P1：冻结组协议尚未贯穿远端上传和 attempt 归档

**位置：** todo 24（第 413 行）、30（第 477 行）、32（第 497 行）。

todo 24 新增 `frozen_group.json`，并要求 worker 只接受摘要匹配的完整组；但 todo 30 的上传清单仍只有 envelope/plan/binding，todo 32 的归档清单也未列出 group manifest、commit marker 以及新增 claim 的处理规则。

当前 `remote/runner.py::_upload_frozen_envelope` 确实只逐个上传这三个文件（第 1320 行）；local continue 的 `include_science=False` 和 remote continue 的 `adopt_results=True` 都有提前返回。因此不能假定现有通用流程会自动处理新增文件。

按清单实现，远端可能因为缺少 manifest 被新 reader 拒绝；也可能为跑通远端而绕过组校验。continue 后残留旧 group marker/claim，则会与新 attempt 冲突。远端逐文件上传期间，也需要与本地相同的完整组可见性。

**建议直接补入 24/30/32：**

1. 在 todo 24 定义唯一的 attempt 工件清单和路径约定，区分不可变冻结输入组、执行权记录、运行后回执。不要把运行后才生成的 receipt/task_result 当成冻结输入组成员。
2. todo 30 从该清单上传冻结组至远端 staging，逐项校验后最后提交 manifest/marker；明确本地 runtime 路径与远端平铺路径如何映射。远端 reader 必须验证完整组后才允许执行。
3. todo 32 统一处理冻结组、协议记录和执行权；归档前先确认旧执行及子进程已停止，不能靠移动一个仍被持有的锁文件解除互斥。same-attempt restart 保留有效冻结组和选择；只有进入新 attempt 才归档并重建。

**补充验收：** 缺失/篡改远端 manifest 必须在执行前拒绝；任一上传阶段中断不能暴露可执行半组；local continue 与 remote adopt-results 后完整旧组可审计、新组可验证；同 attempt 重启不误归档有效组。可沿用现有 todo 的受控 transport 测试，无需增加真实 LSF 前置。

### RR2｜P1：执行 claim 还需约束 worker 原子竞争及失败回执写权

**位置：** todo 24（第 413、417—418 行），与 todo 7（第 243 行）、26/27 联动。

“controller 获取 claim，worker 验证 token”本身不能阻止两个拿到相同 envelope/token 的 worker 同时通过校验。计划已经要求测试“两 worker 同 claim 只执行一次”，建议把实现必须满足的原子竞争点写明。

更容易漏掉的是竞争失败分支：当前 `WorkerRuntime.run` 会将 `WorkerError` 交给 `_write_failure_receipt`；当尚无 receipt 时，该方法写当前身份的 failed receipt（`worker_bootstrap.py:472`）。如果第二个 worker 因 claim 正被持有而失败，并沿用这个通用兜底，它可能在第一个 worker 正常计算时发布一个身份完全合法的失败回执。仅验证“QC 调用一次”仍可能放过此问题。

**建议直接补入 7/24/26：**

- worker 在调用 executor 前必须原子取得独占执行权；验证 controller 授权与取得实际执行权应有明确区别。锁/claim 继续使用同一套 attempt 协议，worker 不访问 jobs DB。
- 只有实际执行权持有者能写该 attempt 的权威 receipt/task_result。竞争失败、重复投递或失去执行权只输出独立诊断/重试结果，不得写同路径 failed receipt，也不能触发当前 job 的失败 CAS。
- 定义有效回执复用、锁持有中、持有者失联三种处理；失联时先确认原进程及子进程状态，不以超时或锁文件存在性直接推定可重算。

**补充验收：** worker A 持锁并停在受控计算 barrier，worker B 用同组数据启动；B 退出/等待后权威 receipt 仍未被写成 failed，job 不变；A 完成后仅有一个有效结果与一次终态推进。另覆盖 QC 输出已落盘但 receipt 未写出时的进程退出及恢复判定。

### RR3｜P1：部分结果的状态、退出码和复用资格仍未形成一个确定契约

**位置：** todo 5（第 223、227—228 行），与 todo 6、26—29 的消费行为联动。

本版保留不同科学任务的部分结果政策，这个方向正确。但当前文字还有两处问题：

- 真值表允许 `completed + complete=False + required satisfied` 返回 completed；验收却笼统要求 fake backend 返回 failed 和 partial 时都得到 `exit 1/failed receipt`。两者无法确定同一 partial 案例的预期。
- `recoverable=false` 没有指定持久字段或权威消费位置。当前 `TaskExecutionOutcome` 和 `ExecutionReceipt` 均没有这个字段，`main` 只根据 `receipt.state == "completed"` 返回 0，现有 prerequisite 检查也不消费 `evidence_digests["partial_result"]`。新增这个标记不能自动改变恢复或下游资格。

todo 5 虽然写了“不得正常复用/下游消费”，仍应指定由哪个现有契约和消费者落实，避免只在转换器测试里断言一个字符串。

**建议修订：** 把每一行的 `TaskResult.status/complete/必需事实 → receipt.state/exit code/job status/正常复用资格/发布政策` 一并列出；明确“缺失必需事实”优先于 `complete=True` 声明。按任务既有科学政策确定部分结果的状态，不能由实现者临时猜测。

如继续保留 completed-partial，必须明确资格的持久权威：例如从经 receipt 摘要验证的 `task_result.json` 读取完整性事实，并映射到现有恢复/前置条件契约；或明确一个向后兼容的类型化扩展。冻结哪些消费者必须检查它，区分“重投递返回已有执行事实”与“把部分结果作为正常完成结果复用”，无需一概禁止前者。

**补充验收：** 分别跑 failed、缺必需事实的 partial、满足必需事实的 partial 三个真实 worker 子进程；断言完整的输出矩阵。序列化、重启、controller 发布及真实 prerequisite/recovery 入口都保持同一资格，不靠内存变量或仅查看状态字符串。

### RR4｜P2：要求未修改的原始探针全部退出 0，与新契约冲突

**位置：** todo 38，第 561 行；todo 15，第 323 行；todo 29，第 467 行。

保留历史探针、迁移原始反例是正确的，但原脚本的 setup 并非新接口的永久契约：

- 原 D06 在 `reproduce_defects.py:179` 调用 `build_prepare_executor` 时没有 identity/authorization provider；todo 15 明确要求缺 provider 拒绝装配或执行。原测试随后期待取得 `result.prepared`，没有处理拒绝异常。
- 原 D07 在第 200 行直接调用 `_build_execution_cmd`，未经过输入物化、release 准备与 freeze。新计划将这些步骤放在普通 submit/launch 流程，要求这个私有底层调用仍凭空生成完整冻结链，会诱导错误的职责迁移；它的 `water.xyz` 也未实际创建。

因此，“原目录和脚本不修改”与“原 probe 必须退出 0”不应同时作为硬门。

**建议修订：** 正式测试保留原缺陷的触发语义与正确性断言，按新公开契约补齐 fixture。D06 显式注入当前身份 provider，仍验证旧 attempt 被拒绝；D07 走普通提交、真实输入和受控科学后端，证明没有预置冻结文件也能走 worker。历史脚本保留 hash 和原始结果；若复跑遇到接口前置变化，逐项记录差异及正式测试映射，不能把该运行标成 PASS，也不能把执行失败偷换为环境 NOT_VERIFIED。不为旧 probe 放宽生产拒绝规则。

### RR5｜P2：分层验收结论与“所有 shortfall 必须清零”仍然矛盾

**位置：** todo 35/43，第 527、607 行；F1，第 645 行；F4，第 666—668 行；Success criteria，第 683 行。

本版新增 A/B/C 分层和 code/environment 缺口区分是必要改进。但 F1 仍要求每个 GAP 实际关闭，F4 仍要求每个 shortfall 都新增 todo、存在任一 shortfall 就不能完成。这会把已明确允许的 packaging OPEN、缺真实集群凭据、已移交的后续能力重新拉回本批，使 todo 43 的“如实出具分层结论”失去终止条件。

**建议统一完成判据：**

- 本批范围内的代码缺陷或必须具备的默认链证据缺失：保持未完成，必须整改。
- 已明确移交的能力/打包配置缺口：记录 OPEN、owner 和下一道门；本次证据整理可以完成，对应产品门保持未通过。
- 环境缺失：记录 NOT_VERIFIED 和缺失前置，不新增无限重试的整改 todo；不能据此关闭需要真实环境证据的缺陷或产品门。
- F1/F4 的 APPROVE 表示“本批必需项满足且结论与证据一致”；它不等于所有 A/B/C、G1—G6 都 PASS，更不等于可以发布生产。

将 F4 的强制新增 todo 限定为“本批应交付但未满足的代码/验证项”，并同步 F1、Success criteria。D08/D09 若缺少所要求的真实浏览器证据，其验收仍不能冒称已关闭。

## 3. 执行建议

保留当前 45 项 DAG、`24 → 25 → 26 → 27 → 28 → 32 → 29 → 30 → 31` 主链，以及 44/45 在候选冻结前交付的安排。以上修改直接落到既有 todo 和 F1/F4，不必继续扩大任务数量。

建议先补 RR1/RR2 的工件与执行权契约、RR3 的输出真值表，再改 RR4/RR5 的验收文字。修订完成后，计划即可按当前分波方式推进；实现过程中仍以实际测试和证据决定各缺陷是否关闭。
