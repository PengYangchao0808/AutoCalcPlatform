# ACP 架构调整代码质量审查、缺陷报告与验收记录

审查开始：2026-10-19；报告收口：2026-10-20。被审查提交：`d5d85e7c804878edd0cedb6bce1d32af2d92ad83`。

实施依据：[ACP_Architecture_Adjustment_Plan_20261016.md](../../docs/ACP_Architecture_Adjustment_Plan_20261016.md)，重点采用 §7、§12、§14、§17、§29、§35、§38—45 的接口、完成定义与证据纪律。审查对象为当前工作区代码；历史提交和历史 PASS 仅作参考。

## 1. 验收决定

**总体结论：FAIL，不通过“架构调整整体完成”的验收。**

本次确认 **10 项业务/契约代码缺陷、2 项测试维护缺陷，以及 1 项默认执行链交付缺口**。12 项缺陷中，5 项 P1、7 项 P2。其中，失败被改写为成功、旧执行回执被当作当前成功返回、任务详情执行身份失真、旧 attempt 的 prepare 被标记 ready、科学分块产品身份冲突，直接涉及科学与恢复事实的正确性。

项目完整非慢回归实际结果为 **9735 passed、3 failed、11 skipped、95 deselected**；选定架构/隔离/恢复/打包护栏 **205 passed**，Node 正式套件 **306 passed**。3 个现有回归失败归入 D12/D13；它们与另外复现的业务缺陷分别统计。

模块划分、科学代码迁移、兼容转发、Client/Visualization 抽取已有明显成果，可以确认这些实现已交付；当前证据不足以关闭端到端完成门。**验收工作完成与产品验收通过是两个结论：本报告完成审查并作出拒绝通过的决定，没有将未验证项目改写为通过。**

本报告不将明确按需求后置的 Slurm、GPU、对象存储、Credit 价格/支付能力认定为本期代码缺陷；它们应保留原有延期状态。独立发行、默认 worker、已交付 prepare/read 的正确性和 M1 科研闭环，则必须按实际交付范围验证。

### 严重级别

| 级别 | 本报告采用的判断标准 |
|---|---|
| P1 | 会误报科学/执行成功、混淆执行或科学身份、破坏恢复/数据一致性；相关能力启用或扩大使用前必须修复 |
| P2 | 支持功能不可用、准备状态错误或恢复机制失效；须在该功能验收关闭前修复 |
| 交付缺口 | 目标链路或入口尚未闭环；可保留兼容运行，但不能宣称该目标完成 |

### 缺陷总表

| ID | 级别 | 发现 | 当前影响范围 | 本次证据 |
|---|---|---|---|---|
| D01 | P1 | worker 将 `complete=False` 的 failed/cancelled/blocked 改成 completed | 新 worker 的科学结果转换 | 3 个真实 TaskResult 转换反例 |
| D02 | P1 | 旧 attempt 或不同 release 的成功回执可从 worker 入口返回 | worker 重投递、目录中残留回执 | 2 个组合根反例 |
| D03 | P1 | v1 详情未映射真实 attempt/revision，始终返回默认值 | 默认任务详情、前端执行身份判断 | `(6,42) → (1,0)` |
| D04 | P2 | prepared 文件读取只查远程缓存，漏掉本地任务原件 | 新 manifest/geometry 等读取面 | 本地 catalog ready、manifest not_prepared |
| D05 | P2 | prepare 去重吞掉新 generation/digest，并复用旧成功 | 同 attempt 的资源更新、缓存重建 | generation 2 请求返回 generation 1 的 succeeded |
| D06 | P1 | 生产 prepare adapter 丢弃 lease 的 attempt 等身份 | 重启恢复、rerun 与旧准备操作交错 | attempt 1 lease 消费 attempt 2 文件并报告 ready |
| D07 | 交付缺口 | 默认派发没有生成冻结 envelope/plan，8/15 是有前提的能力表 | 独立 worker/默认执行链完成声明 | 新目录的 singlepoint 仍派发 `acp.cli` |
| D08 | P2 | NMR 成功加载后删除 canvas | NMR 候选结构查看 | Chromium + 仓库 3Dmol：canvas 1 → 0 |
| D09 | P2 | 振动静态箭头冷启动存在 enabled 循环依赖 | 振动位移箭头 | 正式 Node 套件已断言并保留该故障 |
| D10 | P1 | 分块身份丢失上游算法/版本/参数身份 | CCCP bounded 产品、未来缓存/派生复用 | 不同源身份及数值 → 相同分块身份 |
| D11 | P2 | availability 默认 attempt=None 被转换为 0 | 新 availability 默认查询 | preparing 被显示为 absent |
| D12 | P2（测试） | 前端拆分后两项源码定位断言仍只读取 wizard.js | 全量非慢回归、前端兼容守护 | 2 个正式回归失败；行为代码仍在新驻点 |
| D13 | P2（测试） | 迁移回填测试仍从旧 shim 访问/patch 私有 `_MIGRATIONS` | schema 020 回填守护、全量回归 | 1 个正式回归失败，回填行为尚未进入断言 |

D01—D06、D10、D11 由本目录的 [reproduce_defects.py](reproduce_defects.py) 实际执行复现。D07 使用同一脚本作完整交付要求的探针，**不将有意保留 legacy 的分支本身判为代码错误**。D08 由 [browser_probe.py](browser_probe.py) 复现；D09 的复现来自项目正式 Node 行为套件。D12/D13 使用项目现有测试的原始失败回执，不改动其预期。

## 2. 审查范围、基线与证据限制

### 2.1 本次实际工作

- 阅读主方案及对应包的 AGENTS，核对 Services、CCCP、前端的当前实施与验收记录。
- 审查 worker/回执/默认执行入口、prepare/availability/read adapter、科学产品身份、前端实例及任务身份。
- 执行项目正式 Node 套件、Python 编译检查、选定新驻点的 lint/format 检查、架构与兼容护栏，以及非慢测试复验。
- 对发现建立独立反例；使用真实 Chromium DOM 和仓库 vendor 验证 NMR 画布问题。

本次仅新增审查报告、复现脚本及证据；没有修复业务代码，没有修改现有测试预期，没有重启生产服务。初始用户工作区已有 1 个已跟踪修改和 8 个未跟踪文档，见证据中的 `baseline.json`；这些不归入本次交付。

### 2.2 测试结果

以下计数均来自本轮固定提交的实际运行；没有引用旧提交计数替代当前复验。

| 验证 | 本次结果 | 解释 |
|---|---|---|
| Node 正式入口 `node frontend/js/module_tests/run_all.js` | **PASS：306 passed, 0 failed** | 包含两个“RECORDED FINDING”故障保持断言，见 D08/D09 |
| `python3.11 -m compileall -q src` | **PASS** | 只证明 Python 源文件可编译 |
| 新驻点 Ruff：Services + CCCP Analysis + products | **FAIL：13 条，3 个文件** | Services 本身未见本次 Ruff 报错；不能据此称全仓 lint 通过 |
| 同范围 `ruff format --check` | **FAIL：13 个文件需格式化，140 个已格式化** | 见质量评估；没有为通过验收机械格式化业务代码 |
| 独立审查探针 | **FAIL：12 个不满足要求的反例** | 11 个代码缺陷反例 + 1 个交付缺口反例；它们不在项目默认 tests 收集目录内 |
| Chromium + 3Dmol NMR 组件探针 | **FAIL** | 加载成功后 canvas 被删除，无页面异常 |
| 架构/隔离/恢复/打包等选定护栏 | **PASS：205 passed，353.03 秒** | 覆盖唯一任务根、Services 隔离、F4 scope、markers、恢复 fixture、worker、文档、资产与 identity forwarder |
| 项目完整 `pytest -m 'not slow'` | **FAIL：9735 passed、3 failed、11 skipped、95 deselected，1796.29 秒** | 9749 个选中测试全部跑完；原始失败见 D12/D13 |
| 当前根配置出树 wheel 构建 | **PASS，单 wheel** | 使用已存在的本地构建工具重跑成功，核对结果见 `wheel-check.json`；不能证明独立 CCCP 发行完成 |
| 真实 QC/LSF/生产发布 | **NOT_VERIFIED** | 本次未执行真实科学计算、集群提交或生产发布 |

说明：首次在沙箱内运行 pytest 时，FastAPI TestClient 卡在 AnyIO 事件循环启动；单项探针保留了栈信息。随后按环境权限要求在沙箱外继续完整非慢测试。环境挂起不作为业务缺陷计数。

首次 wheel 构建因默认 Python 环境缺 `wheel` 构建依赖而未执行构建，随后使用已存在的 `/tmp/opencode/builddeps` 本地工具完成，无联网安装。两次构建回执均保留；首次工具缺失不算产品缺陷。非慢回归中的 11 项 skip 包括真实 qml/OpenBabel/仪器数据、live 远端场景和凭据未配置的 SSH/SFTP/code-sync，均不计入真实环境 PASS。

### 2.3 证据的含义

独立探针采用“写出正确行为预期、观察当前实现失败”的方式，故运行失败是缺陷证据，不能与项目回归失败数相加。NMR 探针使用真实浏览器与真实 vendor，但注入几何数据和外围胶水，证明组件缺陷，**不代表完成全站浏览器或真实科学端到端验收**。

## 3. 逐项缺陷

### D01 — 科学失败、取消或阻断被改写为成功（P1）

**位置：** [runtime/worker.py:235](../../src/acp_services/runtime/worker.py#L235)，特别是 238—239 行。引入提交 `c5000430`。

**触发条件：** CCCP 返回 `TaskResult(status="failed", complete=False)`，或取消/阻断且不完整的结果。这不是只存在于手造对象中的状态：`cccp/calculation/tasks/singlepoint.py:165`、`:186`、`:200` 的后端异常、失败和缺少能量分支均产生 failed + complete=False。

**实际行为：** `_outcome_from_task_result` 先接受 failed/cancelled/blocked，随后只因 `complete is False`，将 state 设成 completed。本次三组输入均得到 completed。`build_receipt` 复制该 state，worker `main` 又以 completed 决定退出码 0。

**影响：** 新 worker 会把科学失败当成成功回执；错误文本仍在也不能修正错误的成功状态。下游若据回执推进或复用，将违背方案 §14.1 的完成事实与依赖门。当前默认路径多数仍 legacy，因此本报告不声称已有生产任务被误判，但新 worker 不能据此验收通过。

**根因：** 混淆“保留部分科学输出”和“执行成功”，没有对 status 与 complete 建立显式映射表。

**建议修复：** 保留失败/取消/阻断状态；对 completed + complete=False 按现有 TaskResult→平台规则处理，单独保留 partial/complete 信息，不直接提升为整体成功。

**关闭条件：** 对 status × complete × required-step 的组合测试；通过实际 `CccpTaskExecutor`、receipt 序列化和 worker 进程退出码验证失败不会变成成功，而非只给 executor 注入已完成的 `TaskExecutionOutcome`。

### D02 — 回执冲突在组合根被吞掉，或未验证 release 就复用（P1）

**位置：** [worker_bootstrap.py:447](../../src/acp_services/worker_bootstrap.py#L447)、[worker_bootstrap.py:472](../../src/acp_services/worker_bootstrap.py#L472)、[runtime/worker.py:419](../../src/acp_services/runtime/worker.py#L419)。组合根相关代码由 `71cefe52` 引入。

**触发 A：** 当前 envelope 属于 attempt 2，默认 receipt 路径仍有 attempt 1 的 completed 回执。

**实际 A：** `FrozenWorker` 正确抛 `ReceiptConflictError`，但 `WorkerRuntime.run` 捕获所有 WorkerError；`_write_failure_receipt` 看到已有回执就直接返回，完全不检查身份。因此当前运行返回 attempt 1 的 completed，而不是明确拒绝 attempt 2 的执行。

**触发 B：** job/attempt/submission 相同，但 release/plan/owner/fencing 发生不一致。

**实际 B：** `_reuse_or_conflict` 仅比较 job、attempt、submission。测试中的 release-a 成功被 release-b 运行直接返回，且在 `validate` 之前就短路。

**影响：** 新 worker 的公开进程入口可报告错误执行的成功；会把需要对账的冲突掩盖为正常完成。Controller 的 `verify_receipt` 如果实际接入仍可拒绝该回执，因此不能推断它必然污染 jobs DB；问题在于 worker 入口自身已违反拒绝旧身份的契约。

**建议修复：** receipt 冲突不能走“返回任何已有回执”的兜底；复用前验证完整冻结身份，必要时复用现有验证逻辑。按 attempt 保存回执；冲突退出非零且保留原件，不覆盖旧证据。

**关闭条件：** 通过 `assemble_worker(...).run()` 和 `python -m acp_services.worker_bootstrap` 覆盖旧 attempt、不同 release、plan、owner、fencing、损坏回执；只测底层 FrozenWorker 抛错不足以关闭。

### D03 — v1 任务详情丢失真实执行身份（P1）

**位置：** [v1_routes.py:629](../../src/acp/api/v1_routes.py#L629)、[v1_schemas.py:327](../../src/acp/api/v1_schemas.py#L327)、[job_detail_controller.js:43](../../frontend/js/workbench/controllers/job_detail_controller.js#L43)。

**触发条件：** 任意 revision 非 0 或 attempt 非 1 的任务经 `_record_to_v1_model` 转换。

**实际行为：** 模型声明了 attempt=1、revision=0 默认值，但转换器没有传入 record 的两项值。本次 record=(attempt 6, revision 42)，输出=(1,0)。

**影响：** 前端用 `job_id + attempt + revision` 判定执行身份，同一任务重跑后的详情仍可产生相同身份键；文件、日志、候选和结果会话的失效/隔离失去可靠依据。也会误导 API 调用者对当前 attempt 的观察。

**历史归属：** 转换器为旧实现；前端 Plan1 验收记录 OPEN-2 已记录过 API attempt 与 DB 不一致。本报告将其列为当前遗留缺陷，不归因于本轮新写的 worker。

**建议修复：** 显式映射 record.attempt、record.revision；核查 list/detail/recovery/v2 投影的一致性，保持 name_revision 独立。

**关闭条件：** 实际 HTTP 创建→重跑→读取详情，对照 DB 和前端身份键；重命名不误触执行身份变化，新 attempt 必须失效旧结果会话。

### D04 — 本地已准备文件被新读取面误判为缺失（P2）

**位置：** [services_data_bootstrap.py:914](../../src/acp/services_data_bootstrap.py#L914)、[services_data_bootstrap.py:956](../../src/acp/services_data_bootstrap.py#L956)、[read_root.py:393](../../src/acp_services/data/read_root.py#L393)。

**触发条件：** local 任务的 work_dir 内已有 `RESULT/result_manifest.json`，远程结构缓存中没有相同副本。

**实际行为：** root resolver 正确返回本地 work_dir，catalog 判为 ready；文件 entry 则无条件调用 RemoteStructureCache.get_cached，完全不读取本地 root。结果为 manifest not_prepared；对应 HTTP prepared 路由会返回 409。

**影响：** 新读取面无法完成最常见的 Standalone 本地文件闭环；旧 GET 仍可读取，故不是所有历史结果都不可读。geometry、normal_modes、候选等文件入口存在同一 adapter 问题。

**建议修复：** 根据已授权 ReadRoot 选择本地或远程索引；本地路径经受控相对路径及软链接边界校验后 bounded read，查询不触发 prepare/解析/复制。

**关闭条件：** 本地/远程两种 root 对五种 resource 分别验证 ready、内容、越界拒绝和零网络副作用；不能把本地 fixture 预先塞进远程缓存来代替本地路径测试。

### D05 — prepare 去重不能识别内容变化或恢复失效的成功记录（P2）

**位置：** [operations.py:189](../../src/acp_services/data/operations.py#L189)、[operations.py:707](../../src/acp_services/data/operations.py#L707)、[operations.py:1047](../../src/acp_services/data/operations.py#L1047)。

**触发条件：** 相同 permission_scope/job/attempt/resource 下，generation、digest 或 binding 发生变化；或先成功后缓存被清理，需要重新准备。

**实际行为：** 默认 key 不含 generation/digest；enqueue 找到旧行后仅对 FAILED 重置，其他状态不比较参数就返回。探针先完成 generation 1，再提交 generation 2/digest new，拿到的仍是 generation 1 的 succeeded，无法取得新 lease。expire 对终态也直接返回，不能使成功记录失效。

**影响：** 新请求被旧成功吞掉；持久操作“成功”与当前资源状态脱节。后台预取在重启时重新 enqueue 也无法保证被清理缓存恢复，仍可能依赖旧 GET/fetch 快速路径。

**建议修复：** 明确幂等键的内容身份；同键异参拒绝，或将资源身份纳入 key。为已成功但缓存失效建立显式失效/重新准备机制，保持并发 CAS 和旧持有者 fencing。

**关闭条件：** 同键同参共享、同键异参拒绝/新操作、成功后缓存淘汰、generation 更新、binding/授权版本变化与重启恢复；验证实际新文件可见，不只校验操作行数。

### D06 — 生产 prepare executor 没有守住 lease 的执行身份（P1）

**位置：** [services_data_bootstrap.py:547](../../src/acp/services_data_bootstrap.py#L547)、[services_data_bootstrap.py:587](../../src/acp/services_data_bootstrap.py#L587)、[operations.py:1225](../../src/acp_services/data/operations.py#L1225)。

**触发条件：** 数据库中尚有 attempt 1 的 queued/running prepare；任务已 rerun 到 attempt 2 并产生新 catalog。恢复线程随后执行旧 lease。

**实际行为：** `execute(lease)` 只传 lease.job_id 给 prefetch。prefetch 读取“最新”任务，既不比较 lease.attempt，也不使用 resource_digest/generation/binding/authorization_version。测试中 attempt 1 的 lease 从 attempt 2 的目录得到 already_ready。

**影响：** 旧操作可被报告为新内容的成功；completion CAS 对比的是 data_operations 行与旧 lease，不会自动重读当前任务身份，因此这道 CAS 不能弥补 adapter 丢掉的身份。这里确认的是错误的 ready 关联，没有声称本次已经发生远端旧字节覆盖。

**建议修复：** 将完整冻结准备身份传到执行器并在读取/传输/提交可用性前重新校验；使用已具备身份/摘要/generation 校验的传输链，或提供等效的生产 adapter。旧 attempt 明确 expired/rejected，不继续采纳最新结果。

**关闭条件：** 旧 lease→新 attempt、新旧工作流变化、缓存命中/真正下载两条分支、准备中 rerun、授权撤销和 storage rebind；对操作状态及发布文件同时断言。

### D07 — 默认执行链尚未交付独立 worker 闭环（交付缺口）

**位置：** [runner.py:1306](../../src/acp/scheduler/runner.py#L1306)、[execution_chain.py:225](../../src/acp_services/runtime/execution_chain.py#L225)、[tests/test_services_chain_switch.py:122](../../tests/test_services_chain_switch.py#L122)。

**事实：** 8 个工作流在能力表为 integrated，但 dispatcher 只有在目录中预先存在 envelope.json + plan.json 时才选择 Services worker。审查生产 src/acp 调用面只发现 `frozen_artifacts_present` 消费点，没有生成冻结对的接入点，也没有 `verify_receipt/advance_job_state` 的生产调用。chain-switch 测试使用 `_write_frozen_artifacts` 先写测试文件。

**本次探针：** 新建目录、singlepoint 默认 `_build_execution_cmd` 返回 `python -m acp.cli run singlepoint ...`。

**判断：** fallback 是明确兼容策略，允许保留；缺口是 controller 冻结、派发、接收验证与发布尚未组成默认链路。**8/15 能力表通过不能写成 8/15 默认生产已启用；本次没有确认任何工作流已由普通新提交自动走完整 envelope 链。**

**关闭条件：** 从真实 RuntimeCommands/JobManager 提交受支持工作流，自动冻结 envelope/plan，绑定 release/submission/storage，在新进程执行并读取回执，经资格验证与 CAS 推进，按既有顺序发布；本地/远端及旧 attempt 负例分别记录。若当前批次只交付选择器，应缩小声明并保留该门 OPEN。

### D08 — NMR 成功路径删除真实 3Dmol canvas（P2）

**位置：** [nmr.js:837](../../frontend/js/workbench/features/nmr.js#L837)、[nmr_viewer.test.js:256](../../frontend/js/module_tests/nmr_viewer.test.js#L256)。

**触发条件：** 打开 NMR 候选结构，geometry 读取与 sharedLoadGeometry 均成功。

**实际行为：** nmrEnsureViewer 已在容器内创建 canvas；成功路径随后 `container.textContent = ""`，将 canvas 从 DOM 删除。真实 Chromium 与仓库 `3Dmol-2.5.5.min.js` 的输出：canvas_before=1、canvas_after=0、atoms_loaded=3、instance_still_registered=true、page_errors=[]。

**影响：** 已加载结构显示为空；仍登记的 instance 与实际 DOM 脱节，后续重建/清理和用户判断都会受影响。这不是科学缺结果或缺 3Dmol。

**历史归属：** 删除语句来自 `8fc5c4c0`，前端验收 OPEN-5 已知。现有正式测试以 `RECORDED FINDING` 对其保留，而不是验证画布存活。

**建议修复：** loading/empty 提示使用独立节点，在 viewer 创建前清理；成功后不得清空 canvas 所在父容器。

**关闭条件：** 实际 DOM 下断言成功后 canvas 仍 connected，结构可见且可导出；切任务、候选、隐藏/重开与 dispose 无残留。将现有故障保持断言改为正确的行为验收。

### D09 — 振动箭头冷启动永远无法启用（P2）

**位置：** [vibration_viewer.js:539](../../frontend/js/vibration_viewer.js#L539)、[vibration_viewer.js:550](../../frontend/js/vibration_viewer.js#L550)、[vibration_playback.test.js:237](../../frontend/js/module_tests/vibration_playback.test.js#L237)。

**触发条件：** arrowState.enabled 初始 false，已有有效几何和振动向量，用户选择 arrows/combo。

**实际行为：** `_arrowsVisible()` 要求 enabled 为 true；enabled 仅在成功绘制后设 true。首次 refresh 无法进入绘制分支，最后再次设 false。正式测试确认首次 0 箭头、手工将 enabled=true 后同一数据得到 3 箭头。

**影响：** 合法模式的静态位移箭头不可见，影响振动/过渡态观察。此项为已登记旧故障（前端验收 OPEN-4），不计为本轮新引入回归。

**建议修复：** 分离用户显示意图和几何可用性/上次绘制成功状态；不要用后者阻止首次绘制。

**关闭条件：** 冷启动、切模式、缺几何后恢复、arrows/animation/combo 切换与重新挂载均正确；无需测试手工预置 enabled=true 才能成功。

### D10 — bounded 产品身份遗漏上游科学算法身份（P1）

**位置：** [bounded/identity.py:163](../../src/cccp/analysis/bounded/identity.py#L163)、[bounded/chunking.py:149](../../src/cccp/analysis/bounded/chunking.py#L149)；subsampling/multiresolution 使用同一派生方式。

**触发条件：** 原始输入摘要相同，上游 parser/算法版本或参数不同，产生数值不同、ProductIdentity 不同的源产品；使用相同 chunk_size/series/range 派生。

**实际行为：** bounded_identity 只保留 base.product_kind、base.input_digest，再用 chunk 算法版本和分块参数替代其余字段。块内 product 也只沿用 base.input_digest。探针构造同一原始输入、parser v1/v2、y=-1/-2，两源 identity 不同，但 bounded.identity 以及首块 identity 完全相同。

**影响：** 派生产品的公开身份不能唯一标识科学结果；以此作缓存/复用键会混用不同算法或解析版本的结果。BoundedProduct 另存 base_identity 有助追溯，但不能修复公开派生身份与块身份自身的碰撞。本报告确认契约碰撞，未声称已发现生产缓存污染。

**建议修复：** 在派生身份中纳入完整源 ProductIdentity 的规范摘要；保留原始 input_digest 的兼容字段时，可在参数摘要中包含源身份。manifest 与每块必须同样修正，并按 schema/版本策略迁移。

**关闭条件：** 同源同参数稳定；源 generator/algorithm/version/params/schema 任一变化均使派生及块身份变化；同时覆盖 chunk、trajectory chunk、subsample、multiresolution 的 reader/writer 与旧 fixture 兼容。

### D11 — availability 默认查询看不到正在准备的当前 attempt（P2）

**位置：** [v1_routes.py:7551](../../src/acp/api/v1_routes.py#L7551)、[services_data_bootstrap.py:999](../../src/acp/services_data_bootstrap.py#L999)。

**触发条件：** 远程任务缓存尚冷，当前 attempt 2 已有 queued/running prepare；请求 `/availability` 不带可选 attempt 参数。

**实际行为：** API 将 None 继续传入；adapter 用 `int(request.attempt or 0)`，按 attempt 0 构造 key 并查询。PrepareRequest 只允许 attempt>=1，所以默认查询无法匹配正常操作。本次同一 reader 的默认查询返回 absent，显式 attempt=2 返回 preparing。

**影响：** 默认接口隐藏真实准备进度/失败信息，客户端可能误判资源缺失或再次请求准备；与“缺失、尚未准备、正在准备须区分”要求不符。

**建议修复：** 在入口从权威 JobRecord 解析未指定的当前 attempt，再构造资源 key；显式 attempt 按历史读取策略校验，不能把 None 解释成不存在的 attempt 0。

**关闭条件：** 默认与显式当前 attempt 的 queued/running/failed/succeeded 状态一致；重跑后不返回旧 attempt 操作，并保持 GET 零传输/零排队副作用。

### D12 — 前端拆分没有同步两项现有兼容守护的源码驻点（P2，测试维护）

**位置：** [test_acp_xtbmd_platform_phase5.py:521](../../tests/test_acp_xtbmd_platform_phase5.py#L521)、`:542`、`:579`；实际实现为 [submit-edit.js:827](../../frontend/js/workbench/features/wizard/submit-edit.js#L827) 与 [draft-form.js:1387](../../frontend/js/workbench/features/wizard/draft-form.js#L1387)。

**本次失败：** `test_frontend_submits_xtbmd_control_group_fields`、`test_frontend_rank1_default_differs_per_workflow`。

**根因与实际情况：** `_FRONTEND` 固定为 wizard.js，拆分后的控制字段转发在 submit-edit.js，rank1 默认逻辑在 draft-form.js。审查已找到 `xtbmd_censo_energy`、energyLike、三种 control level 和原默认赋值，不能将文本定位失败直接写成产品丢字段或科学行为改变。

**影响：** 非慢 CI 门失败，前端兼容守护未随符号迁移闭合。另一个只作否定匹配的 rank1 toggle 测试可能在目标实现不在文件中时空过，更说明需要行为验证。

**建议与关闭条件：** 按真实 owner 更新测试驻点，并增加/保留从默认页面装配入口到提交载荷的等效行为断言；验证 0/false/显式参数与 catalog 控制字段集合，不删除原断言、不通过恢复 retired active 入口让测试通过。

### D13 — identity forwarder 迁移后，旧 schema 回填测试失去注入点（P2，测试维护）

**位置：** [test_job_state_transitions.py:332](../../tests/test_job_state_transitions.py#L332)、[scheduler/migrations.py](../../src/acp/scheduler/migrations.py)、[store/migrations.py:703](../../src/acp_services/store/migrations.py#L703)。

**本次失败：** `test_migration_backfills_legacy_attempts` 在访问 `acp.scheduler.migrations._MIGRATIONS` 时抛 AttributeError，还没有构造旧库或验证 attempts 回填。

**根因：** 实现迁入 acp_services 后，旧模块只转发 migrate/get_schema_version 等对象；测试仍读取并 patch 旧模块的私有列表。仅向 shim 加 `_MIGRATIONS` 别名也不充分：用 patch.object 替换旧模块变量，并不会改变 migrate 函数在新实现模块中的 globals。

**影响：** 非慢 CI 门失败，重要的旧数据回填回归没有实际执行。当前证据不能据此认定迁移算法本身错误，也不能将该测试算成恢复兼容通过。

**建议与关闭条件：** 让测试向唯一实现根注入迁移列表，或使用真实 pre-020 库 fixture；实际验证历史 attempts=3、损坏 JSON 与缺省值回填。保持旧公开函数的 identity forwarder，禁止在 acp 侧复制迁移实现。

## 4. 架构与交付验收矩阵

| 方案要求 | 当前判断 | 依据与关闭条件 |
|---|---|---|
| 三层九模块的职责与唯一实现方向 | 已有实现；定向护栏结果见 §2 | Services 包、CCCP Analysis、前端三模块已形成；不能据目录数量认定端到端完成 |
| CCCP 不反导 acp、旧路径 identity forwarder | 以本次护栏为准 | 保留 public shim、历史 reader、模型许可与恢复 fixture；没有要求为减少文件数删除它们 |
| worker 独立进程、失败与回执资格 | **FAIL** | D01、D02；可启动/可导入不等于完成事实正确 |
| 默认 worker/release/receipt 链 | **未完成** | D07；只在手工预置冻结文件后选择 worker |
| 15 active 工作流与 legacy 兼容 | 部分新协议，旧路径保留 | 能力表 8 integrated、7 legacy；全 15 新协议覆盖仍 OPEN；保留旧路径不等于实现损坏 |
| Data prepare、重启/去重/generation/attempt | **FAIL** | D05、D06、D11 |
| cache-only 本地/远程读闭环 | **FAIL** | D04；前端对新 prepared/availability 消费尚未接入，旧 GET 为兼容面 |
| 任务执行身份与前端会话 | **FAIL** | D03；不能只以纯 controller 的 mock 身份测试代替 HTTP 投影 |
| Visualization 实例与支持视图 | **部分通过，功能有缺陷** | 正式模块套件通过；NMR 和振动仍 D08/D09 |
| 科学规范产品与派生身份 | **FAIL** | D10；其他数值迁移不能因该反例而一概否定，但派生身份门未关闭 |
| 独立 CCCP wheel/最小角色发行 | **未交付/未完成独立验收** | B40b gate OPEN；当前 root pyproject 同时发现 acp/cccp，独立配置仍在补丁交接中 |
| 账户/权限/准入/配额生产接线 | **未接入** | closure L2-12 明确组件已交付、生产 submit/HTTP/UI 未接入；不宣称 Lab/Hosted 可上线 |
| MCP 默认入口、archive CLI/API | **未接入** | closure L2-13/L2-14；模块与工具 schema 不等于可用入口 |
| M1 GUI/CLI 的真实科学闭环 | **NOT_VERIFIED** | 本次未跑真实 QC；既有记录也明确分列，不以 FakeBackend 证明科学成功 |
| 四组合真实运行兼容 | **未完成真实四组合验收** | 既有“四组合”主要用 S1 内保留的旧 GET 代表 S0，并用 API 测试替身代表 F0；是表面兼容证据，不能代替实际冻结 F0/S0 bundle/服务组合的运行 |
| 科学等价、LSF/SSH/SFTP、生产回滚 | **NOT_VERIFIED** | 需要相应环境与同一候选提交证据，历史 PASS 不升级为本提交 PASS |
| 性能/资源收益 | **未完成量化验收** | 本次没有同样本前后 P50/P95、请求/I/O、帧率/内存与长期切换测量，未声称收益百分比 |

### 对“已经全部完成”的核查

主方案本身存在多代实施记录：§42.7 曾写 R0—R6 COMPLETED，§44 又明确撤回若干描述；当前前端 Plan1 记录已将后续收口、未接能力和开放门分列。Services §45、closure、packaging receipts 继续保留 B40b、完整 envelope、真实运行和入口接线缺口。**因此用户所述“完成修改”可以理解为完成当前开发批次，但不能据这些文档认定整份方案的全部交付门已关闭。**

旧 GET 保留、7 个 legacy 工作流和未选择的扩展基础设施是明确兼容/范围决定，本报告不把它们单独当回归缺陷。报告拒绝整体通过的原因是已交付路径存在实际正确性缺陷，并且默认闭环与必要验收证据仍不完整。

本次当前根配置实际构建的 `acp-0.2.0-py3-none-any.whl` 同时包含 acp、acp_services、cccp，8 个 NMR 模型/许可文件均与源文件摘要一致；没有 frontend 文件。该结果证明当前单 wheel 的科学资产收录，不能作为独立 CCCP 安装或完整 Web 静态资源发行的通过证据；后者也是现有 packaging 回执已登记的缺口。

## 5. 代码质量评价

### 已取得的改进

1. 科学任务根与 CCCP Analysis 的归属比原先清晰，平台服务建立了独立包；旧公开路径保留 identity forwarder，兼容策略有价值。
2. Client 的共享查询/取消、任务 controller、Visualization 受限实例有正式行为测试，306 项模块测试在本次提交可重复执行。
3. Store CAS、冻结契约、prepare 持久操作和回执验证具有正确的设计方向，现有恢复 fixture、许可和模型资产未按“减行数”删除。
4. 当前较新的收口文档普遍保留 NOT_VERIFIED/OPEN，对真实科学、远端和生产证据的限制已有明确记录。

### 仍影响维护与验收的问题

**最主要的质量短板是组合后的行为。** D01 绕过了 FakeExecutor 常用测试面；D02 是底层正确拒绝、组合根再次把拒绝变成旧成功；D04/D06/D11 是纯服务模块与生产 adapter 接线之间丢失语义。应把测试重心移向“默认入口 + 真实 DTO + 失败/竞争场景”，而非继续增加只检查文件/符号存在或正确形状的成功替身。

**体量没有随归属迁移自然收敛。** 本次 AST/行数统计：Services 70 个 Python 文件约 40,380 行；最大的 identity_lifecycle 2,409 行、quota 1,858 行、admission 1,596 行。acp 的 manager 6,131 行、v1_routes 7,691 行。相对冻结基线 f410ac9，manager 从 5,758 增至 6,131 行，v1_routes 从 7,394 增至 7,691 行。行数不是缺陷本身，但证明不能将“新增服务目录”直接写成旧 manager/API 已完成职责下沉。

**静态质量门未全绿。** 本次选定新驻点有 13 个 Ruff 问题（embedding/parsers 的命名、energy_filter 的 2 个未使用导入），13 个文件不满足 format。很多内容由旧实现迁移而来，本报告不把所有告警认定为本轮新增逻辑错误；但 §29.3 的“无新增债务”需要按原符号基线核对，不能直接宣称 lint/format 通过。Services 这次没有 Ruff 诊断，问题集中在所查 CCCP Analysis 驻点。

**宽泛异常应按行为审查。** AST 计数显示 Services 有 18 处 `except Exception`，其中 12 处无异常变量绑定；acp 有 262 处。事务 rollback 或入口隔离可以合理保留，不能仅凭计数一概判错。D02 展示了真正的问题：兜底改变了失败语义，需要针对捕获后的返回值与关联身份审查。

**文档一致性测试不等于事实验收。** 四组合与 closure 的 JSON checker 能约束字段、自报提交和状态格式，却不能证明实际静态 bundle/旧服务运行过，也不能发现本报告的 TaskResult 转换反例。应保留这些检查，同时把真实命令回执、默认入口探针、同提交科学/浏览器结果作为关闭门的条件。

**选定清单通过遗漏了全量回归红项。** 前端/Services 单独选择的套件可以通过，但完整非慢集合仍有 D12/D13 的 3 项失败。后续收口必须保留全量结果，不能用局部的 205/306 或历史 525/1348 计数替代。

## 6. 修复顺序与再次验收

1. 先修 D01、D02、D03、D06、D10，建立完成事实、回执资格、执行身份和科学派生身份的正确性；这些修复不需要重建架构。
2. 修 D04、D05、D11，使用真实本地 root 与生产 prepare adapter 验证准备/读取闭环。
3. 修 D08/D09，采用真实 DOM/几何和冷启动场景；保持多个实例和清理/导出行为。
4. 对 D07 作明确交付选择：实现默认闭环并验收，或将本批声明收窄为选择器/worker 模块交付，保留独立默认执行的 OPEN 门。
5. 修复 D12/D13 的等效守护及测试注入点，在冻结候选提交上执行相关定向测试、项目非慢测试和编译检查；真实 QC、LSF、独立发行包、四组合与生产验证按实际支持范围补齐，不改变原科学容差或放宽护栏。

每个缺陷关闭时应记录修复提交、原反例如何转绿、相关回归、实际入口证据。**本报告的拒绝通过结论不因代码后来改动自动转为 PASS，须对修复后的固定提交重新验收。**

## 7. 复跑与证据入口

```bash
# 当前正确性反例：在被审查提交预期失败；12 个反例不与默认回归统计混算
python3.11 -m pytest reports/architecture_acceptance_20261019/reproduce_defects.py -q

# 项目正式前端行为套件
node frontend/js/module_tests/run_all.js

# 真实 Chromium DOM + 仓库 vendor，组件级 NMR 探针
python3.11 reports/architecture_acceptance_20261019/browser_probe.py

# 项目验收入口
pytest -m 'not slow' -q --disable-warnings
python3.11 -m compileall -q src

# 本次静态质量范围
python3.11 -m ruff check src/acp_services src/cccp/analysis src/cccp/calculation/products
python3.11 -m ruff format --check src/acp_services src/cccp/analysis src/cccp/calculation/products
```

本次原始回执与摘要保存在本目录的 `evidence/`，文件摘要及最终计数见 `evidence_manifest.json`。脚本仅写入 pytest 临时目录或浏览器临时 profile；报告文件不属于业务实现或默认回归套件。
