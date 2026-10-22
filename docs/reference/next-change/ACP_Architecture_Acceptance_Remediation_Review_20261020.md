# ACP 架构验收整改实施计划评审

日期：2026-10-20。对象：[实施计划](../../../.omo/plans/acp-architecture-acceptance-remediation.md)（43 个 todo、7 个 wave、4 个最终验证任务）。对照：[整改要求](ACP_Architecture_Acceptance_Remediation_Plan_20261020.md)、[原验收报告](../../../reports/architecture_acceptance_20261019/REPORT.md)及当前代码 `878e3557b3b593a0c42adf842e80fe1408db395e`。

**结论：总体方向合理、缺陷覆盖完整，但建议修订以下执行语义和验收条件后再启动整批实施。** 主要问题集中于 D07 的状态推进/恢复，以及 D05/D06 的资源身份。它们不能仅靠增加测试数量解决，须先把正确行为写清楚。

本次是计划及代码静态评审，另执行了依赖矩阵一致性检查；没有重新执行产品测试、真实 QC、浏览器或集群验收。下述 R 编号是计划评审意见，不是对修复后实现新增缺陷的认定。原实施计划未改写。

## 1. 可以保留的安排

- D01—D13 均有实现位置、原始反例、正式测试和证据要求；D12/D13 提前恢复有效守护是合理的。
- 将 D05/D06 放在同一身份契约下实施，并明确后台预取、重启、缓存淘汰，覆盖了原报告的组合缺陷。
- D07 从本地 singlepoint 扩展到其他工作流和适用远端，保留七项 legacy，并逐格声明支持范围，方向正确。
- D08/D09 要求真实 Chromium 和仓库 vendor，原报告保持不可改写，避免用诊断脚本退出码或旧故障保持断言宣称通过。
- 兼容转发、科学 fixture/容差、模型/许可和旧 GET 的保留要求明确；后续账户/MCP/项目包等能力单独交接，范围控制合理。

## 2. 执行前应修改的内容

### R1｜P1：旧回执拒绝不得把当前任务写成 FAILED

**位置：** todo 26—27，计划第 422、432、436—437 行。

todo 26 要求拒绝回执时无 CAS、job 不变；todo 27 却要求旧 attempt 回执导致“FAILED without CAS”。两者矛盾，也违反项目“所有状态写入经 CAS、旧观测不得覆盖当前执行”的约束。当前 `runtime/receipts.py::advance_job_state` 明确在拒绝时不调用状态端口。

**建议改为：** 区分两类事实。旧 attempt、release、owner/fencing 的观测应被隔离/记录，当前 job 的 status/revision/attempt 保持不变；当前执行已经确认退出，但它自己的必要回执缺失或损坏，才由 controller 根据当前执行事实生成受 revision/status/attempt 保护的失败转换。无论何种情况，状态改变都走 CAS。

**补充验收：** attempt 2 正在 RUNNING、PAUSED、CANCELLING 时分别注入 attempt 1 回执，断言当前记录完全不变；另测当前进程已退出且当前回执损坏，合法失败 CAS 恰好一次。不要再使用“FAILED + no CAS”作为预期。

### R2｜P1：terminal no-op 会漏掉 CAS 后崩溃的发布恢复

**位置：** todo 26、28、32，计划第 422、442—447、482 行。

todo 26 规定已终态就 no-op，同时在 CAS 后调用 publication；todo 28 主要覆盖 publisher 抛错后重试。若进程在终态 CAS 成功之后、首次进入 publisher/写 `publication_state.json` 之前退出，重启后 terminal no-op 可能永久跳过发布。rerun 与迟到发布交错时，还可能将旧 manifest 写到复用目录。

**建议改为：** “不重复终态转换”与“继续未完成发布”分别幂等。复用现有终态副作用机制，将待发布事实与终态转换一起持久化，或提供具有同等崩溃恢复保证的持久记录；startup/reconcile 对待发布终态继续处理。发布前在既有副作用边界重新核验 attempt/owner，旧 attempt 不得触碰新目录内容。

当前 manager 已有 `terminal_side_effects_done=False`、`_reread_side_effect_scope` 和终态重试机制，应扩展这个流程，避免另设互不一致的完成器。

**补充验收：** 在 CAS 后但 publisher 调用前强制进程退出；重启后 QC 调用次数不增加、manifest 最终可用。另测 publish 成功但确认前崩溃，以及发布前发生 rerun；旧发布不得覆盖新 attempt。

### R3｜P1：“最后创建的操作”不能作为当前内容权威

**位置：** todo 12—14，尤其计划第 302—306 行。

`find_latest(... ORDER BY created_at/rowid)` 只能确定请求到达顺序，不能确定资源版本。例如 generation 2 已排队，generation 1 的旧请求迟到并新建操作，latest 会再次选中 generation 1；同 attempt 的 storage rebind、授权版本变化也有同类问题。原方案要求按当前内容/generation/binding 定位，不能被 latest-row 替代。

**建议改为：** 由权威资源快照给出本次查询的完整 expected content identity/generation/binding/authorization version，按该身份定位操作和缓存。时间排序仅可用于“同一个有效内容身份”的多个重试世代；旧请求的入队和查询均不能倒退当前身份。客户端幂等键还应明确权限范围及数据库唯一约束，避免两个进程同时用同键异参各自插入。

**补充验收：** 先 enqueue generation 2，再迟到 enqueue generation 1，当前查询始终代表 generation 2；再分别测试同 attempt rebind/撤权、并发同键异参和缓存旧字节仍存在的情况。

### R4｜P1：prepare 的读前/返回前校验仍不等于发布时校验

**位置：** todo 15—16，计划第 312—326 行。

保留 catalog prefetch 可以，但必须证明其 writer 与逐文件 transfer 具有相同的发布守卫。当前 `RemoteStructureCache.fetch_catalog` 按文件逐一 fetch/publish；`TransferPipeline` 在发布时使用进入 pipeline 后读取的 `fence.generation`，不能仅凭传入字段就认定冻结 lease 的 generation/binding 已验证。默认 transfer authorization adapter 在没有 decision 时还会返回 True。

todo 15 另外同时写了“provider 缺失时保留 test-only fallback”和“删除 provider 后必须拒绝 ready”，需要统一。

**建议改为：** 生产构造器要求显式身份/授权提供方；local-single 可提供明确的本地策略，不能用 `None` 暗示允许。租约的 expected identity 必须传到发布边界，并在受锁/fencing 保护的提交点与权威状态比较；明确资源 generation 与缓存 purge generation 的对应关系，不能混用。catalog 若保留旧机制，应为每个必需文件和 catalog-ready 提交提供等效保证；做不到就改走统一传输机制。

**补充验收：** 在最后一次身份检查之后、`os.replace` 之前通过 barrier 触发 rerun/rebind/撤权/淘汰，旧字节不得成为当前可读文件，旧操作不能提交 READY。缓存命中、catalog bundle、实际下载三条分支都测；缺失 production provider 必须拒绝装配或执行。

### R5｜P1：singlepoint 子片不应提前切换全部八项工作流

**位置：** todo 25、29、32，计划第 412、452、482 行。

todo 25 只创建 `build_singlepoint_freeze_spec`，却要求按 `ENABLED_WORKFLOWS` 接入；当前该集合包含八项。其他七项 builder 到 todo 29 才出现，恢复/切换纪律到 todo 32 才补齐。按当前文字逐 todo 合入，可能让现有工作流提前进入未完成的新路径。

**建议改为：** 分开“科学闭包已具备”与“默认入口已启用”。增加显式默认启用集合/能力记录，第一片仅 singlepoint；其他工作流逐项具备请求、发布、恢复证据后再加入。把 todo 32 的同 attempt 协议冻结、归档和禁止回退规则提前成为首次默认启用的前置；在这些前置完成前，新模块只交付不激活。

**补充验收：** singlepoint 子片合入后，其他十四项仍按各自已登记路径运行；不能因 integrated 就调用 singlepoint builder，也不能因未实现 builder 直接失败。每次启用新 workflow 均运行当前 15 项兼容/编辑覆盖守护。

### R6｜P1：原子文件写入和同身份复用尚未提供并发执行互斥

**位置：** todo 6、24、32，计划第 222—227、402—407、482—486 行。

当前 worker 是“检查 receipt 不存在→执行→写 receipt”。两个进程可同时通过不存在检查并各算一次；最终 `os.replace` 原子写只能保证文件完整，不能保证科学执行次数。类似地，依次 replace envelope/plan/binding 是各文件原子，不是整组一致可见，现有文件存在性检查不足以代表冻结完成。

**建议改为：** 明确派发 claim/锁的唯一 owner、跨进程竞争和失联恢复规则；worker 在执行前验证执行权，不能把幂等 receipt 当作互斥机制。冻结工件采用受单写者保护的 staging 加最后提交标记/清单等方案，reader 只接受完整且一致的冻结组。崩溃后未知执行应先确认旧进程/子进程停止或复用有效完成事实，不盲重跑。

**补充验收：** 两个真实 worker 进程在 receipt 不存在处同步起跑，executor 只进入一次；逐一在 envelope/plan/binding 写入之间杀进程，重启后不会使用混合工件或重复派发。增加 QC 已产生输出但 receipt 尚未写出时的恢复用例。

### R7｜P1：release 标识必须对应实际加载的代码

**位置：** todo 25、30，计划第 412、416、462—466 行。

计划规定本地 release manifest “cache per process”，但没有规定 worker 从不可变 release 目录启动。当前本地 runner 使用所配置 Python 启动新子进程，工作目录切到 task dir 并不冻结 import 来源。控制进程仍在运行时 checkout 内容改变，后续 worker 可能加载新代码而复用旧 release ID。

另有具体事实：当前 `build_release_manifest` 的 release ID 是完整 SHA-256 的前 **16** 个十六进制字符（`remote/release.py`），不能直接把它解释成完整内容摘要。

**建议改为：** 保留兼容 release_id，另明确完整 manifest digest 的规范算法；本地/远端 worker 都从已验证、不可变的执行 release 加载代码，并在派发/回执中记录相同身份。若本地暂不能提供该条件，应明确采用已有开发态非版本化声明并限制支持结论，不能只对可变 checkout 计算一次 hash 后宣称版本冻结。缓存只能绑定不可变 release。

**补充验收：** 第一次冻结后修改工作区 Services 文件，第二次执行必须使用新的 release 或拒绝启动，不能沿用旧摘要；首个已冻结执行仍加载原 release。断言子进程实际模块路径/摘要，而非仅断言 manifest 生成函数的返回值。

### R8｜P2：依赖表与冻结候选规则需要统一

**位置：** dependency matrix、todo 38—42、F1。

对表格执行依赖展开后，发现 **31 对**“可以并行”与直接/传递依赖冲突的关系，例如 2/3、28/30、28/31、39/41、40/42。todo 39 没有依赖 todo 38，可能先跑全量回归，再加入原始反例。todo 41/42 又在所谓冻结候选阶段新增 acceptance scripts，最终候选不再是 todo 39 验证的代码集合。

此外，逻辑无依赖也不表示可以共享目录并写：todo 5/6/7 同写 worker/bootstrap，todo 14—18 同写 Data adapter/reader，todo 21/23 同写浏览器测试文件，后期多个文档任务共用 closure/ledger。

**建议改为：** 以一份 DAG 为权威，Can parallelize 自动由依赖和写入文件集合导出；共享文件指定单写者，或独立 worktree 后串行集成。todo 38 改为各片随修复迁入测试、末期只检查覆盖；将 todo 41/42 的 harness 开发前移，执行留在验收阶段；todo 39 显式依赖所有测试/harness 和最终集成。区分 code candidate SHA 与之后的 evidence-only report SHA，代码有变再跑受影响门。

## 3. 建议一并改进的细节

| 位置 | 问题/风险 | 建议修改与验收 |
|---|---|---|
| todo 5 | 一律将 completed+complete=False 认定“必需事实缺失”，比原整改要求更强；进程验收又使用 `main([...])` 和 `_ProbeExecutor`，不能单独证明真实转换链的子进程行为 | 冻结逐任务/必需步骤真值表，证明没有改变已有科学政策；增加实际子进程经 CccpTaskExecutor 返回失败/部分 TaskResult 的测试，probe 保留为入口烟测 |
| todo 5/28/30 | `task_result.json` 引入了新的持久完成证据，但本地发布前的摘要核验、缺失/篡改处理尚不明确 | 统一 codec 与摘要规则，绑定 receipt 的 attempt/plan；本地和远端发布前同样验证。篡改/缺失不能发布为成功，也不能悄悄重跑 QC |
| todo 6 | 建议把公共 predicate 放 receipts.py 再由 worker 调用；当前 receipts.py 已导入 worker.WorkerError | 将纯身份比较和必要错误类型放到无 worker/controller 依赖的现有契约驻点，或明确消除该反向依赖；测试两种 import 顺序与独立 worker 导入 |
| todo 10/11 | `source_identity_digest` 若可由调用方独立提供，可能与 base_identity 不一致；旧 spec 缺字段默认值也可能改变旧 params 摘要 | 新身份只能由完整 base_identity 计算或必须核验一致；identity rule v1 保留原摘要规则，v2 强制源身份；增加伪造 digest 拒绝、旧 reader round-trip 和实际版本迁移 fixture |
| todo 12 | 准备服务自建表与中央 migration 的执行顺序没有明确；直接对独立 prepare DB 跑全量 scheduler migrate 会创建无关表 | 指定单一迁移实现与明确调用顺序，支持独立 DB 的受限迁移入口；新建库、旧 prepare-only 库、混合 jobs 库、先 migrate 后建表、并发初始化均测试。缺表时不得把未执行迁移记为完成 |
| todo 13 | `dedup_epoch` 如何分配、重开与新建二选一仍未冻结；线程 RLock 不覆盖多进程 | 选定一种状态转换，数据库事务中分配 epoch/执行冲突判定；两个独立 service 连接或进程验证唯一操作、旧 lease 失效与审计保留 |
| todo 17/18 | resolve/stat 与后续 open 分离仍可能发生路径/attempt 替换；显式历史 attempt 没有明确 root 选择实现 | 使用项目的 pathlib 约定与受控打开机制；冻结同一授权读取快照。覆盖检查后替换软链接、读取中 rerun、历史 attempt 原件不存在时拒绝，不能回退当前 work_dir |
| todo 3 | 只用 Node vm 加载 submit-edit.js 或新增纯函数，不能完整称为“默认页面到 payload” | 模块测试保留；另通过真实默认页面装配执行提交，截获请求体验证 0/false 和字段集。retired 默认值作为历史兼容；active 场景从实际 catalog 选取 |
| todo 24/25 | 三文件“必须产生”与 binding optional 描述不一致；TaskRequest 中仅约束 structure.path/output_dir，未列 payload/工件引用路径 | 按当前 binding 契约明确必填条件；对实际消费的全部路径类字段逐工作流验证位置、摘要与远端重定位，包含需要的波函数/Hessian/来源工件 |
| todo 30 QA | 写成“bsub 后、intent 写前杀进程”，与“intent 必须在 bsub 前持久化”冲突 | 改测 intent 落盘后/bsub 前、bsub 已接受但 remote_job_id 尚未确认、receipt 下载后/CAS 前三个窗口；不引入非法提交顺序作为正常流程 |
| todo 35 | CCCP-only 的 smoke 示例同时 import acp_services；worker `--help` 不足以证明实际独立执行 | 三个隔离环境分别检查正负 import；CCCP-only 应拒绝 acp/acp_services，worker-only 应拒绝 acp，并实际执行冻结请求；Web 从发行物启动隔离服务验证静态资源 |
| todo 35/43、F1/F4 | 允许 packaging/环境门 OPEN/NOT_VERIFIED，同时又要求所有 GAP 关闭、所有 shortfall 新增 todo，可能形成无法结束的循环 | 明确 A 缺陷修复、B 默认链/发行、C 真实功能的独立完成判据。未交付的打包配置是代码交付缺口，缺二进制/凭据才是环境缺口；任务评审完成与产品验收通过分别记录 |
| todo 39/F1/F2 | todo 33 只要求 13 个历史文件 format 通过，F2 要求所有 touched files；F1 无条件再跑一遍约半小时全量回归 | format 范围统一为受影响文件及明确清理清单；一次冻结候选的完整回归可由独立 verifier 审计原始回执并抽跑关键反例，代码变化/证据不可信时再全量复跑 |
| todo 1/39 | baseline 断言禁止 JSON 出现 e2e-task-root.txt，与“如实记录全部用户 dirty 状态”冲突；compileall 不验证 import 可解析 | baseline 记录但不打包/提交该文件内容；不删基线事实来过断言。compileall 负例使用语法错误，导入/依赖验证另用干净环境 smoke |

## 4. 建议调整后的执行顺序

保留 43 个编号也可以，重点修改任务依赖与默认启用时机：

1. 基线、D12/D13、验收 harness 的基础设施先准备；每项缺陷反例随所属修复进入正式套件。
2. D01/D02/D03/D10 与 Data 完整身份契约实施；上述 R1/R3/R4 的语义先定稿。
3. D05/D06/D04/D11 完成真实 adapter 和竞争测试；可视化在独立写权下同期处理。
4. 实现 D07 freeze、执行互斥、receipt 校验、持久发布恢复及同 attempt 协议纪律；新路径先不激活。
5. 单独启用 singlepoint 并通过普通提交/失败/重启闭环；再逐个启用另外七项，最后接适用远端。发行依赖和实际 release 来源在启用前验证。
6. 汇总受影响质量/隔离/兼容门；所有实现、正式测试及 harness 集成后冻结 code candidate。
7. 在同一候选执行完整回归、浏览器、四组合和适用真实环境；更新证据及分范围结论。实际发布继续走原发布流程。

无需为计划评审或已授权的本地实现额外增加审批；生产发布/重启沿用现有明确门。建议先修订 R1—R8，再执行，尤其不能将 todo 27 的状态预期、todo 14 的 latest-row 权威和 todo 25 的八项提前启用直接交给执行者照写。
