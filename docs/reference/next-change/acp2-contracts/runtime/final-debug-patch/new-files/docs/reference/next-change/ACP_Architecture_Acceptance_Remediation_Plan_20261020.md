# ACP 架构调整验收整改计划

日期：2026-10-20。状态：**待实施**。本次交付为整改计划，未修复业务代码，未重新执行产品验收，未发布或重启服务。

本文是[架构调整主方案](../../ACP_Architecture_Adjustment_Plan_20261016.md)的验收整改附件，承接其 §7、§12、§14、§17、§29、§35、§38—45；不改变三层九模块、唯一实现根、兼容和科学政策，不另起一轮架构重建。缺陷编号沿用[验收报告](../../../reports/architecture_acceptance_20261019/REPORT.md)的 D01—D13；原有 P/PR/MU/CQ/M 编号仍是需求与里程碑索引。

## 1. 整改目标与范围

本轮先恢复已交付路径的正确性，再补齐默认执行链，最后在固定候选提交上重新验收。交付分为三个层次：

| 交付层次 | 必须达到的结果 | 可以作出的结论 |
|---|---|---|
| A：缺陷修复 | 12 项代码/测试缺陷关闭，原反例得到正确结果，正式回归与相关质量门通过 | 本报告所列缺陷已修复；不等于默认 worker、独立发行或真实科学已通过 |
| B：默认链路与发行 | D07 从普通提交自动完成冻结、派发、回执验证、CAS 与发布；独立角色和 Web 静态资源按声明范围可安装运行 | 逐工作流、逐执行环境声明默认链路与发行支持，不使用笼统的“8/15 已启用” |
| C：功能与运行验收 | 同一候选的浏览器、GUI/CLI 真实科学、历史兼容、适用远端、四组合及恢复/回退证据齐备 | 只关闭实际通过的产品或部署门；M1/M2/M3 分别判定 |

默认安排是**实施 D07 完整链路**，先本地 `singlepoint`，再逐项推广到当前具备科学闭包的工作流和受支持远端。环境未就绪时先交付 A 层，B/C 层继续保持 OPEN/NOT_VERIFIED，不能把整改范围缩写为“全部架构调整完成”。

账户/权限/准入/配额生产接线、MCP、项目包、计量接线和前端新 prepare/事件协议列入后续能力接入清单，各有进入条件。Slurm、GPU、对象存储、Credit 价格/支付等已延期目标继续按原方案管理，不因本报告扩大为本批必做需求。

## 2. 基线、证据与实施约束

### 2.1 已知基线

验收报告审查提交与编写本计划时的 HEAD 均为 `d5d85e7c804878edd0cedb6bce1d32af2d92ad83`。以下是**报告已有结果，本次未复跑**：

| 项目 | 报告结果 | 整改时的使用方式 |
|---|---|---|
| 完整非慢回归 | 9735 passed、3 failed、11 skipped、95 deselected | 3 项失败对应 D12/D13；最终须重新执行完整集合 |
| 架构/隔离/恢复/打包定向门 | 205 passed | 保留原门，不以历史计数代替修复后结果 |
| Node 正式套件 | 306 passed | 含 D08/D09 故障保持断言，绿色不表示两项功能正确 |
| 独立探针 | 12 项失败 | 11 项业务缺陷反例及 1 项 D07 交付探针，与正式回归分开统计 |
| Ruff / format | 13 条诊断 / 13 个文件需格式化 | 按原符号及实际修改范围归属，逐项处理 |
| 真实科学、LSF、生产 | NOT_VERIFIED | 只能由新候选上的实际执行关闭 |

实施前另存代码 revision、dirty/untracked 清单及必要补丁摘要、解释器/Node/依赖、配置来源、软件/解析器版本、fixture 摘要与支持矩阵。当前已有用户修改和未跟踪文档；`tests/baseline/refactor-evidence/e2e-task-root.txt` 不纳入整改提交。验收报告、复现脚本和原始 evidence 保持审计原件。

### 2.2 不变量

1. 每个 attempt 只有一个冻结执行入口、存储绑定、release 和持久提交身份；禁止同 attempt 同时派发 legacy 与新 worker。
2. 任务状态仍由既有 controller/JobManager 经 Store CAS 推进；worker 不访问 jobs DB、不自增 attempt、不成为第二状态机。旧 poll/receipt 不得覆盖 pause/cancel 意图或复活终态。
3. 科学完成、平台发布完成、远端缓存就绪分别记录；部分科学输出可以保留，必需步骤不完整不能提升为整体成功。发布失败沿用仅重试发布的语义。
4. 新只读面 GET 不传输、不排队、不解析原始 QC、不修补缓存；旧 GET 在兼容窗口内保留现有行为，不能因新接口增加就直接删除。
5. `acp_services` 不导入 `acp`，`cccp` 不导入平台；旧公开路径保持 identity forwarder，科学任务仍只有 `cccp/calculation/tasks/` 一个实现根。
6. 不增设全局 manager/事件总线承载业务，不用重复 DTO/writer 修补边界。bootstrap 只装配；冻结、身份校验与状态规则归各自 owner。
7. 科学容差、黄金值、旧恢复 fixture、模型/许可、vendor、历史只读与 retired 限制均保留。QC 接口层若实际受改动，先登记 F4 amendment 并提供等效或更强护栏。

### 2.3 状态与证据

任务状态使用“待实施 / 实施中 / 待复验 / 已关闭”；验收结果使用 `PASS / FAIL / NOT_VERIFIED`，范围另记 `IN_SCOPE / DEFERRED / NOT_APPLICABLE`。已执行失败不得改为 NOT_VERIFIED；DEFERRED 不等于 PASS。

每项关闭记录：D 编号、修复提交、原反例及修复后结果、正式回归、实际入口、配置/fixture 摘要、影响的协议/存储兼容、剩余限制、回退办法。模块、默认入口、真实功能三层证据分别保存；同一修复影响多个层次时逐层判定。

## 3. 优先级与依赖安排

| 批次 | 工作内容 | 前置条件 | 退出条件 |
|---|---|---|---|
| 0：基线与有效守护 | 固定反例；修 D12/D13；建立逐项台账 | 无 | 两类测试真正执行目标行为，原始验收证据不变 |
| 1：执行与科学事实 | D01、D02、D03、D10；冻结 D05/D06 共用的准备身份并实施核心修复 | 批次 0 的基线 | 五项 P1 关闭；不能继续返回错误成功、错误执行/科学身份 |
| 2：Data 资源闭环 | 完成 D05/D06 联合场景，修 D04/D11，验证重启、淘汰和后台预取 | 统一准备身份与 fencing 规则 | 本地/远端 read、prepare、availability 一致，旧操作不能发布到当前资源 |
| 3：可视化功能 | D08/D09 与默认页行为验证 | 冻结现有页面/载荷；可提前独立实施 | NMR canvas 与振动冷启动正确，导出、销毁和多实例通过 |
| 4：默认 worker 闭环 | D07：本地 singlepoint → 逐工作流 → 适用远端 | 批次 1/2；相应科学闭包与发行依赖 | 普通新提交自动走完整链；按支持矩阵逐格关闭 |
| 5：质量、发行与能力交接 | 相关 Ruff/format、异常语义、大文件职责审查；独立角色发行；登记后续入口接线 | 各片随修随验；独立 worker 安装证据先于其独立交付声明 | 无新增质量债务、声明的角色可安装；未接能力不被误记完成 |
| 6：冻结候选重新验收 | 完整回归、真实浏览器/科学、四组合、适用集群与发布演练 | 所验范围代码和资源集冻结 | 重新出具分范围结论，满足对应门后才进入发布 |

D05 与 D06 共用身份、操作版本和发布守卫，宜同一功能切片交付；P2 的身份契约工作可作为 P1 修复的前置，不机械按严重级别拆开。D12/D13 提前处理是为了恢复有效回归，不能以它们转绿代替业务修复。以上为依赖安排，不承诺未经实施估算的固定工期。

## 4. 逐项修改方案

### D01（P1）：保留真实科学失败、取消与阻断

**归属与驻点：** Runtime；`src/acp_services/runtime/worker.py::_outcome_from_task_result`、`CccpTaskExecutor`、receipt 构建与 `worker_bootstrap.py` 进程入口。关联主方案 P4-04、§7.4、§14.1。

修改内容：

1. 将 `TaskResult.status × complete × 必需步骤满足情况` 写成显式映射规则；删除 `complete=False → completed` 的提升逻辑。failed/cancelled/blocked 均保留失败语义；未知状态按结构化失败处理。
2. `completed + complete=False` 依据现行任务/步骤契约判断，保留有效子项及错误证据；缺少必需完成事实时返回适用的 failed/blocked，不新建全局 PARTIAL 状态。诊断性输出不能作为正常成功恢复或供下游消费。
3. 核查 outcome、receipt、进程退出码、controller 的映射一致性。若现有回执不足以表达完成资格，按兼容策略补字段/版本与 reader，不只在日志中保留 `complete`。

**关闭测试：** 使用真实 `TaskResult` 经 `CccpTaskExecutor`，覆盖四类状态、complete 真/假、必需步骤缺失、诊断性输出与后端失败；再经序列化和真实 worker 子进程断言非成功回执及非零退出码。科学后端可用受控失败替身，但不得直接注入预制 `TaskExecutionOutcome` 绕过转换。复用 `tests/test_services_worker_contract.py`、`tests/test_services_worker_bootstrap.py` 和步骤前置条件/恢复测试。

### D02（P1）：完整身份验证后才能复用回执

**归属与驻点：** Runtime；`runtime/worker.py::_reuse_or_conflict`、`worker_bootstrap.py::WorkerRuntime.run/_write_failure_receipt`、`runtime/receipts.py`。关联 P4-04、P6-02、CQ-07。

修改内容：

1. 在复用前验证 envelope/plan 有效性及回执完整冻结身份：job、attempt、submission、release ID/digest、plan/step identities、owner/fencing 和协议版本；不以前三项相同直接短路。优先复用现有资格验证规则，避免 worker/controller 各维护一份不一致字段表。
2. 对 `ReceiptConflictError`、损坏回执和旧身份单独处理，兜底不得返回任意 existing receipt。冲突保留原件，返回结构化拒绝且退出非零；独立错误文档若需要落盘，遵守现有 runtime 布局，不覆盖冲突原件。
3. 当前回执与 rerun 归档遵守 `WORK/00_RUNTIME/attempts/<N>/` 约定；不为修复另建任务目录。验证重复投递同一完整身份只执行一次，写回执期间的并发也不能覆盖另一身份。

**关闭测试：** 同身份幂等；逐项变更 attempt/submission/release/digest/plan/step/owner/fencing；损坏 JSON、缺字段、并发及执行后崩溃。必须同时覆盖 `assemble_worker(...).run()` 与 `python -m acp_services.worker_bootstrap`，断言返回身份、退出码、执行次数及原文件摘要。controller 拒绝路径应断言 CAS 未调用、当前 job 不变。

### D03（P1）：HTTP 投影与前端使用真实执行身份

**归属与驻点：** API 投影 + Workbench/Client；`src/acp/api/v1_routes.py::_record_to_v1_model`、`v1_schemas.py`、`frontend/js/workbench/controllers/job_detail_controller.js`。关联 P1-03、§42 的任务会话要求。

修改内容：

1. 显式映射权威 `record.attempt`、`record.revision`；核查 list/detail/recovery/v2 的同字段语义。`name_revision`、组织 revision 继续独立，不用前端猜测修补后端缺值。
2. 通过实际重跑与编辑重算验证新 attempt 使详情、文件/日志、候选和结果会话失效；会话令牌继续阻止 A→B→A 的旧响应覆盖。
3. 检查重命名是否会改变通用 record revision。前端执行身份与 UI 元数据修订按实际服务契约区分，不修改 Store CAS 语义来实现“重命名不重载科学结果”。

**关闭测试：** HTTP 创建→查询→rerun→再查询，DB、响应和前端身份一致；包含 `(attempt=6, revision=42)` 原例、名称更新、迟到响应和旧服务缺版本兼容。以 `tests/test_acp_api_v1.py`、`tests/test_services_task_projection.py` 及 Node 详情/会话测试验证；只测纯 controller 不足以关闭。

### D04（P2）：从授权 ReadRoot 读取本地原件与远端已准备副本

**归属与驻点：** Data；`src/acp/services_data_bootstrap.py::_ReadRootAdapter/_ReadCacheIndexAdapter`、`acp_services/data/read_root.py` 和 prepared 路由。关联 P2-04/05。

修改内容：

1. 索引/读取统一使用已解析的 ReadRoot：本地读取受控 work_dir 内文件，远端只读受控缓存。必要的 Port 扩展由 Data 定义，兼容 adapter 只实现文件机制，不复制资源状态规则。
2. 保持资源 ID→允许相对路径、授权、attempt 和 root 的绑定。检查绝对路径、`..`、越界软链接及读取前路径替换风险；保留 bounded range/read-size 限额。
3. readiness 以目标资源事实判定，不把目录存在当成全部文件 ready。不得复制本地原件到远程缓存来绕过根选择问题，也不得在 GET 内调用 prepare。

**关闭测试：** catalog、manifest、geometry、normal_modes、frame_candidates 的本地/远端场景，覆盖 ready、缺失、路径越界、字节范围和大小上限。实际本地 root 内直接写 fixture，不预置远端副本；同时断言 fetch/SSH/SFTP/enqueue/原始解析零调用。复用 `tests/test_services_read_root.py`、`tests/test_services_cache_only_switch.py`。

### D05（P2）：准备操作按内容身份去重，并能重建失效缓存

**归属与驻点：** Data；`acp_services/data/operations.py` 的 key、enqueue、expire/重试与仓储，及相关存储迁移。关联 P2-02/03。

修改内容：

1. 先冻结统一准备身份，至少覆盖权限范围/授权版本、job/attempt、resource ID/kind、源内容摘要、generation、binding，以及会影响结果的请求参数。区分“逻辑资源键”和“本次准备内容身份”。
2. 建议保留逻辑资源键供查询，用完整准备身份规范摘要形成去重身份；同身份并发合并，身份变化产生新操作。显式客户端幂等键若复用为不同参数，返回冲突，不静默复用或改写旧成功。
3. 对成功后缓存淘汰设置显式失效/重新准备路径：产生可区分的准备世代或受 CAS 保护地重开，并更新 owner generation/fencing。保留旧成功的审计含义，不能假装文件一直存在。
4. 同步 availability 的定位规则和旧操作行兼容。旧行缺少内容身份时保守校验/重新准备；迁移保留旧 operation ID 查询，不无条件把旧 succeeded 改成当前 ready。

**关闭测试：** 同身份合并；generation/digest/binding/授权版本各自变化；显式同键异参；成功后淘汰并恢复真实文件；重启、并发 enqueue/claim、旧 lease completion。断言文件、当前可用性与操作状态，不只断言记录数量。复用 `tests/test_services_prepare_operations.py`、传输及后台预取测试。

### D06（P1）：生产 prepare 执行与发布全过程保持 lease 身份

**归属与驻点：** Data；`services_data_bootstrap.py::_PrepareExecutorAdapter`、`build_transfer_executor`、`acp_services/data/transfer.py`、prepare worker 完成路径。关联 P2-02/03、P6-02。

修改内容：

1. 不再把完整 lease 降为 job_id 再读取“最新任务”。由 Runtime/装配边界验证并提供冻结资源描述，Data 按冻结 attempt、内容摘要、generation、binding、授权版本与 workflow 关联执行，不反查 Runtime 私有表承担业务。
2. 缓存命中与真正下载走同一身份守卫；开始、复用/下载完成、原子发布和提交 availability 时校验当前资格。准备中 rerun、权限撤销、storage rebind、generation 变化应拒绝旧操作。
3. 优先接入已存在的 `TransferPipeline/TransferPrepareExecutor`，补齐真实 `request_factory` 和有效授权/身份提供方；不能仅替换成 pipeline 名称却继续使用空摘要、旧 generation 或无条件允许的授权 adapter。
4. 传输先写隔离临时文件，校验后由唯一 writer 发布；当前身份检查与发布锁/fencing 协作，关闭“检查后发生 rerun、旧文件仍发布”的窗口。过期 lease 明确失效/拒绝，不报告 already_ready。

**关闭测试：** 原 attempt 1 lease 对 attempt 2 原例；命中与下载两条分支；传输中 rerun/更换 workflow、撤权、rebind、淘汰、重启和迟到 completion。使用生产 builder/adapter 与受控传输端，断言旧字节未覆盖、当前文件摘要不变、无错误 ready 事件、旧操作不可成功完成。与 D05 联合验收。

### D07（交付缺口）：让普通新提交自动走完整 worker 链

**归属与驻点：** Runtime controller/commands + Data 资源端口 + 本地/远端装配；`runtime/execution_chain.py`、`runtime/receipts.py`、`scheduler/runner.py`、`scheduler/remote/{runner,script_gen,submission,release}.py`。关联 P4-01/04/05/06、P6-02，M1/M3。

当前八项 `closure=integrated` 为 `singlepoint/optimize/frequency/scan/irc/casscf/xtb_optimize/OrcaGradient`，只代表科学闭包能力。`casscf`、`OrcaGradient` 当前为本地范围；其余六项远端仍需逐项真实验收。七项 legacy 保持兼容运行，不能靠修改能力表宣称独立。

按四个可验收子片交付：

| 子片 | 修改与接入 | 必须证明的行为 |
|---|---|---|
| 07-A：冻结与持久提交 | 普通 RuntimeCommands/JobManager 提交产生有效科学请求及冻结 plan；Data 提供确认的输入/存储绑定；绑定 release、owner/fencing、submission；原子物化 envelope/plan/binding/markers | 全新任务目录无手工预置；冻结内容可重算验证；派发前提交记录已持久化 |
| 07-B：本地完整闭环 | 由既有 controller 使用单一 dispatcher 启动 Services worker；收集当前 attempt receipt；完整资格验证后经 CAS 推进，再按既有发布阶段调用 Data | singlepoint 默认入口实际走新进程；科学失败、回执冲突、发布失败和重启均可对账 |
| 07-C：逐工作流与远端 | 按真实 payload/科学闭包逐项扩展；LSF 经既有目录分配、release 验证、上传 markers/冻结工件、持久提交和 bsub 对账链；回执读取通过 Data | 每项记录本地/远端能力及入口证据；远端不是只验证生成的命令字符串 |
| 07-D：恢复与切换纪律 | attempt 入口选择持久冻结；重启/continue/rerun 分别按现行语义处理；已选择 envelope 后遇损坏工件或未知提交转入明确拒绝/恢复 | 同 attempt 不因文件缺失改走 legacy 重提；未知 bsub 先对账；旧回执/旧 worker 不推进当前任务 |

实现时把冻结、派发准备和回执接收规则放在 Services Runtime 的内聚用例内；`acp` 侧注入现有机制，API/runner 不继续堆叠完整业务分支。worker 仅消费冻结文件并调用 CCCP，独立环境中不导入 `acp`、不创建数据库。

**关闭测试：** 从真实默认提交入口启动，不调用测试 helper 预写 envelope/plan。受控后端验证整条产品路径及失败/竞争；再在真实 QC、适用 LSF 上分列验收。覆盖重复派发、提交后崩溃、旧 attempt 迟到、pause/cancel 竞争、发布重试不重算。同步 `tests/test_services_chain_switch.py`、`test_services_worker_bootstrap.py`、`test_services_workflow_enablement.py` 与执行完整性/恢复测试。

支持矩阵增加“科学闭包 / 默认提交自动冻结 / 本地派发与回执 / 远端派发与回执 / 发布恢复 / 真实科学”列，分别列证据。原八项逐格通过才更新启用声明；其余七项继续通过原 C01/HN-01 闭包任务逐项接入，15/15 门保持 OPEN 直到全量完成。legacy 保留本身不是缺陷。

### D08（P2）：NMR 提示节点与 viewer 容器分离

**归属与驻点：** Workbench NMR + Visualization；`frontend/js/workbench/features/nmr.js`、`module_tests/nmr_viewer.test.js`。关联 P3-02/03/04、§42 R5。

清理 loading/empty/error 提示应在 viewer 创建前完成，或使用独立提示节点；加载成功不得清空 canvas 的父容器。切候选、切任务与重开时使用当前会话令牌，释放被替代实例；保留 framing、PNG 导出与多实例隔离。

**关闭测试：** 将 RECORDED FINDING 改为正确行为断言；真实 Chromium + 仓库 3Dmol 下，成功后 canvas 仍 connected、原子已加载、视图非空且可导出。验证候选切换、隐藏/重开、失败后重试、dispose；组件探针与默认页面各提供证据，不能以组件通过替代全站验收。

### D09（P2）：振动箭头首次显示不依赖上一次绘制成功

**归属与驻点：** Visualization；`frontend/js/vibration_viewer.js`、`module_tests/vibration_playback.test.js`。关联 P3-03、§42 R5。

分离用户意图（arrows/animation/combo）、几何/向量是否可用和已创建的图形资源；首次绘制资格不依赖 `arrowState.enabled` 的旧成功值。切模式/卸载时清理旧箭头与动画，不改变原生模式编号、原子顺序、向量归一化和显示振幅的既有科学约定。

**关闭测试：** 从默认 false 状态，以真实几何和向量首次画出箭头；覆盖切模式、缺几何后恢复、隐藏/重新挂载、arrows/animation/combo、两个实例和导出。删除手工设置 enabled=true 的成功前置，保留能捕获原循环依赖的回归；真实浏览器验证箭头及动画可见。

### D10（P1）：派生产品身份包含完整源科学身份

**归属与驻点：** CCCP Analysis/产品契约；`src/cccp/analysis/bounded/{identity,chunking,subsampling,multiresolution,manifest}.py`、`cccp/calculation/products/`。关联 P5-01/03、§7.3。

修改内容：

1. 规范序列化完整源 `ProductIdentity`，覆盖 product_kind、schema、generator、input_digest、algorithm/version、params_digest，并计算源身份摘要。派生身份同时包含该摘要和派生算法/版本/参数、范围、分辨率与 schema。
2. 保留原 input_digest 的来源含义，可将源身份摘要纳入派生参数摘要；manifest identity 和每块 identity 使用一致规则，不能只修 `base_identity` 追溯字段。
3. 明确身份规则升级版本。若仅修改身份算法，升级其算法/参数身份并保留既有 reader；若线格式变更则升级相应 schema 并提供旧版读取路径。旧产品可读，但缺失新身份依据时不能作为新缓存/恢复结果直接收养。
4. 盘点真实缓存/派生复用调用点，按新身份重建可重建产品，不批量删除科学原件或改写历史 fixture。分块、trajectory chunk、subsample、多分辨率各层与块均接入。

**关闭测试：** 同源同参稳定；逐项改变源 generator/algorithm/version/params/schema 后，派生与块身份均变化；不同数值原反例不再碰撞。schema 变化通过显式版本迁移 fixture 验证，不绕过当前构造器版本检查手造非法对象。保留 round-trip、原点/帧映射、关键点保留、有界块和旧 reader 测试，复用 `tests/test_cccp_bounded_products.py` 与产品契约/序列化测试。

### D11（P2）：默认 availability 使用权威当前 attempt

**归属与驻点：** API + Data 装配；`v1_routes.py` availability/prepared 入口、`services_data_bootstrap.py::_OperationAvailabilityAdapter`。关联 P2-04/05。

入口在授权后读取权威任务，将未提供 attempt 解析为当前 attempt，冻结到本次 read 请求并向下传递；非 HTTP builder 的默认读取也要遵守同一解析契约，禁止 `None → 0`。显式历史 attempt 按已支持的历史读取规则校验，不回退到当前原件冒充历史。

与 D05 联动，availability 按当前内容/generation/binding 定位，不能任取同逻辑键旧成功行。读取途中发生 rerun 时不得混合不同 attempt 的根、操作与响应；返回可识别的旧快照或冲突，沿用适用契约。

**关闭测试：** 默认与显式当前 attempt 的 queued/running/failed/succeeded 一致；rerun 后不匹配旧操作；显式历史读取不串数据；GET 全程零传输、零排队。覆盖实际 HTTP 与 `build_cache_only_resource_reader` 默认调用，不只测传入 attempt 的服务单元。

### D12（P2，测试）：前端兼容守护跟随真实 owner

**归属与驻点：** 前端兼容测试；`tests/test_acp_xtbmd_platform_phase5.py`、`workbench/features/wizard/{submit-edit,draft-form}.js` 及页面装配。关联 CQ-08、§38.10。

修正仅读取 `wizard.js` 的测试驻点，分别定位实际控制字段转发与 rank1 默认逻辑；保留原断言含义，避免否定匹配因目标不在文件而空过。补充从默认页面脚本装配到提交 payload 的行为断言，覆盖三类 control level、catalog 字段集合、默认值及显式 0/false。

**关闭测试：** 报告所列两项失败实际转绿，rank1 toggle 守护命中真实实现；当前 active 工作流提交不丢字段，retired 数据只走历史兼容，不能恢复退役 active 入口来通过测试。Node 行为测试与 Python 兼容守护同时保留。

### D13（P2，测试）：旧 schema 回填在唯一实现根验证

**归属与驻点：** Store 迁移测试；`tests/test_job_state_transitions.py::test_migration_backfills_legacy_attempts`、`src/acp_services/store/migrations.py`。关联 CQ-08、恢复兼容门。

将私有 `_MIGRATIONS` 的注入点改为唯一实现模块，或使用可审计的真实 pre-020 库 fixture；测试准备完成后调用公开 migrate/JobStore 验证升级。不要给旧 shim 补私有别名来掩盖 globals 仍在新模块的问题，不在 `acp` 复制迁移实现。

**关闭测试：** 确认旧库确实不含 020，再验证 `attempts=3 → attempt=3`、损坏 JSON/缺省→1、重复打开幂等及 schema version；旧公开 `migrate/get_schema_version` 与新实现仍为同一对象。原失败必须进入真实回填断言后转绿。

## 5. 报告中其他缺口的处理

下列项目与 D01—D13 分列，避免把“模块存在”升级为“产品可用”，也避免修复期间无限扩大范围。

| 项目 | 修改/交付安排 | 进入条件与关闭门 | 主方案/现有记录 |
|---|---|---|---|
| 独立科学包与角色发行 | 合入并核验现有 B40b 打包交接；分别构建 CCCP、Services worker＋CCCP、完整 Web 交付物 | 干净环境出树安装；CCCP 无平台/Web 依赖；worker 无 `acp`/jobs DB；模型/许可完整可加载；Web 的 HTML/JS/CSS/vendor 实际随交付可访问 | P4-06、P6-01；packaging receipts/B40b |
| 前端消费 prepare/availability | D04—D06/D11 通过后，按真实 capability/部署配置在 Client 接入显式 prepare 与只读查询；保留 S0 adapter | 默认页验证 queued/running/failed/ready、重启重试、终态后台预取；旧服务不收到新协议；没有 GET 隐式 prepare | P2-05；L2-11 |
| 持久事件 A04 | 延续既有 DEFERRED 范围；实施时独立交付 Client cursor/重置/重连与刷新切换 | 有真实服务协议和持久事实；只关闭事件实际覆盖的轮询；旧服务/终态可用性刷新保持 | P1-04；L2-16 |
| 身份/准入/配额 | 按部署档位把已交付模块接到 HTTP/CLI 提交与派发前置；Workspace 提供身份/授权，Runtime 负责准入 | 先 local-single，再 Lab Server；跨用户拒绝、撤权、重复请求、配额竞争与执行隔离分别实测；未通过不声明多租户上线 | MU 工作包；L2-12/L3-09 |
| 项目包 CLI/API | 接入已交付 archive/import builder，明确命令与权限、操作回执、完整性校验和幂等导入 | M1 稳定后按 M2 交付；本地导出→新环境导入→读取与证据完整；导入不自动执行任务 | 第 32 节；L2-13 |
| MCP 默认入口 | 将已有 stdio 协议接入明确入口，复用 Runtime/Data/Workspace 用例 | 至少一个真实客户端联调；身份、失败、取消、幂等与 GUI/CLI 一致，不复制调度器 | 第 33 节；L2-14 |
| 用量/计量生产接线 | 在实际 Runtime 完成/恢复及读取路径接入已交付解耦模块，单独记录用量事实 | 重复/乱序回执不重复计量，计量失败不改变科学完成或结果可读性；收费另过独立门 | 第 20 节；L2-15 |
| 巨型文件与异常语义 | 对本轮触及的 manager/API/bootstrap 分支登记 owner，业务规则下沉至对应 Services 用例；审查捕获后的返回值和身份 | 以唯一 writer/调用点/失败语义证明职责变化，不以行数目标强拆；rollback/边界兜底有明确职责和关联日志 | CQ-01/04/07 |
| Ruff/format 债务 | 核对报告中的命名、未使用导入和 13 个格式文件，按责任逐项修复；公开名变化保留兼容 | 触及文件无新增问题；声明“新驻点质量门通过”时该范围 check/format 必须实际为零；未处理历史债务列明位置与理由 | CQ-08、§29.3 |

后续能力仍复用主方案 M1→M2→M3 的依赖，不新建彼此竞争的实施总表。无法在本轮交付的项目必须保留具体 owner、阻塞条件与下一验收入口；不能只写“后续优化”。

## 6. 验证分层与执行入口

### 6.1 将反例纳入正式回归

原 `reports/architecture_acceptance_20261019/` 内容保持审计用途；将能长期守护的正确行为反例移植到对应 `tests/test_*.py` 或正式 Node/浏览器套件，去掉对报告目录的运行依赖。迁入时保留 D 编号与原触发条件，补齐生产 DTO、adapter、默认入口和竞争窗口，不把预期改成当前错误行为。

D08/D09 将现有“记录故障”的绿色断言改成正确功能断言；保留故障触发条件。NMR 的 `browser_probe.py` 当前只输出诊断 JSON，**退出码 0 不能作为修复通过**；正式浏览器测试须断言 canvas connected、结构可见及导出结果。

各片先跑定向回归，完成后不无理由反复全量测试；合并候选最终跑一次完整集合。新增代码或失败出现后才追加受影响复验。测试环境沿用 `.[dev,api,remote]`，源码不能依赖未声明的工作机安装包；新增运行时 import 同步适用打包/节点依赖。

### 6.2 推荐命令清单

以下是**实施后的运行计划，本次未执行**；新增文件及实际收集范围在批次 0 固定。

```bash
# 原始反例单独复验；修复前预期失败，不与正式回归混算
python3.11 -m pytest reports/architecture_acceptance_20261019/reproduce_defects.py -q

# 执行、回执与默认链路
python3.11 -m pytest tests/test_services_worker_contract.py tests/test_services_worker_bootstrap.py tests/test_services_chain_switch.py tests/test_services_workflow_enablement.py -q

# Data 真实装配、持久准备与读取
python3.11 -m pytest tests/test_services_prepare_operations.py tests/test_services_transfer_verify.py tests/test_services_read_root.py tests/test_services_cache_only_switch.py -q

# 科学派生身份与原三项正式回归红项
python3.11 -m pytest tests/test_cccp_bounded_products.py tests/test_acp_xtbmd_platform_phase5.py tests/test_job_state_transitions.py -q

# 正式前端行为；浏览器另外执行有断言的候选级场景
node frontend/js/module_tests/run_all.js
python3.11 reports/architecture_acceptance_20261019/browser_probe.py

# 全量非慢、编译及选定质量范围
python3.11 -m pytest -m 'not slow' -q --disable-warnings
python3.11 -m compileall -q src
python3.11 -m ruff check src/acp_services src/cccp/analysis src/cccp/calculation/products
python3.11 -m ruff format --check src/acp_services src/cccp/analysis src/cccp/calculation/products
```

以上定向清单不替代：Services 全套、依赖方向/唯一实现、F4/grep gates、scheduler markers、edit coverage、帧契约、发布、执行完整性及跨版本恢复门。按实际变更补选对应测试，最终完整非慢结果必须保留；skip 逐项解释，不计真实环境 PASS。

### 6.3 真实环境与四组合

| 验证 | 场景与要求 | 证据边界 |
|---|---|---|
| 默认页面 | 任务列表→详情→文件/日志→结果；同任务 rerun、A→B→A；候选保存、编辑重算、NMR、振动、多实例、导出、隐藏/销毁、断网恢复和历史结果 | 使用真实服务/静态入口与有断言的浏览器；组件 DOM 探针仅作补充 |
| M1 真实科学 | 至少一条已声明场景从结构→GUI/CLI 提交→真实科学程序→规范产品→可视化→证据报告；失败、取消/重启恢复另测 | 软件与预算先配置；FakeBackend 只能证明产品流程；新旧同输入比较沿用既有科学容差 |
| 适用远端 | SSH/SFTP/LSF 提交、观测、停止确认、未知 bsub 对账、release/markers、断线恢复、终态预取、旧 attempt 拒绝 | 真实节点/凭据/集群配置与隔离目录；命令生成或 mock paramiko 不算真实执行 |
| F0/S0、F1/S0、F0/S1、F1/S1 | 冻结真实旧/新静态文件集与旧/新服务及环境，分别启动运行；验证原端点/字段/错误/副作用与新能力降级 | 不用 S1 的旧路由替代 S0，不用 TestClient 替代 F0；记录每组 bundle/revision/config 摘要 |
| 性能与资源 | 同机器/样本采集请求数、P50/P95、远端 I/O、DB 写入、块大小、缓存命中、浏览器内存/帧率与长时间切换释放 | 先在批次 0 固定预算/方法；无前后实测不宣称收益比例 |
| 恢复与回退 | DB/工件关联备份；旧 release 在途任务对账；匹配静态资源回退；缓存可重建但科学原件保留 | 在隔离环境演练，记录不能只回滚代码的协议/DB 变化 |

F0/S0 指本次整改前真实基线，F1/S1 指修复候选；若还要关闭原迁移的四组合门，必须同时注明其原始基线映射并运行相应冻结版本。旧基线的已知缺陷保留，验收重点是兼容行为和新增回归，不能要求 F0 原有缺陷凭空消失。

## 7. 交付、台账与重新验收决定

### 7.1 每片交付

每个可运行切片包含实现、正式行为测试、必要协议/迁移、兼容 reader/forwarder、默认入口接线、回退及证据。受影响的 Services 能力更新现有六列 `migration_ledger.md`、public shim inventory、handoff registry；CCCP 产品/闭包、前端接管表更新原台账，不复制第二权威。

为修复候选建立独立的 `reports/architecture_remediation_20261020/` 证据目录（实施时创建），至少保存基线、D01—D13 关闭表、实际命令/退出码、正式回归摘要、浏览器/科学/集群日志、发行安装回执和带文件摘要的 evidence manifest。原 20261019 验收报告仍是旧提交的 FAIL 记录，不覆盖为 PASS。

新结论引用对应修复提交与完整候选 revision；候选有未提交内容则附可重建补丁/文件摘要。验证后继续改动影响该门的代码或资源集，须复验对应门，不借用另一车道或较早提交的 PASS。

### 7.2 关闭清单

| 门 | 必需条件 | 未满足时的结论 |
|---|---|---|
| G1 正确性 | 五项 P1 全关闭，失败与部分结果不误报成功，完整执行/资源/科学身份可验证 | 相关新能力不得扩大启用 |
| G2 正式回归 | 七项 P2 关闭；D12/D13 实际守护目标行为；Node、完整非慢、编译及受影响架构/恢复/质量门通过 | 缺陷修复批次不通过 |
| G3 默认链路 | D07 在声明的每个工作流/本地远端格上，普通提交→冻结→派发→receipt→资格校验/CAS→发布恢复均有证据 | 只能声明模块/选择器交付，相应格继续 OPEN |
| G4 独立交付 | CCCP/Services worker/Web 的声明范围出树安装和运行通过，资产及静态资源齐全 | 不宣称相应角色可独立发行 |
| G5 功能验收 | 候选级浏览器、M1 GUI/CLI 真实科学、历史兼容及真实四组合通过；其他部署范围按适用门执行 | 可分别报告代码通过，产品/部署未验收 |
| G6 发布运行 | 相应备份恢复/回退、在途任务对账、静态资源/旧标签页、适用远端与性能预算通过；满足既有发布门 | 保持未发布/NOT_VERIFIED，不声称生产验收通过 |

“本轮缺陷已修复”至少需要 G1/G2；“默认独立执行已交付”还需相应范围 G3/G4；“M1 本地科研闭环通过”需要对应 G5。“全部架构调整完成”仍须主方案 §17 的其他必需门及各部署档位条件，不能由本附件自动关闭。

发布前先完成可审查候选、测试与回退工件，再进入现有 F1—F4 审批及发布流程。文档交付不触发服务重启；后续实际发布按项目规则通过既有脚本/服务管理执行，不手改生成的 systemd unit。
