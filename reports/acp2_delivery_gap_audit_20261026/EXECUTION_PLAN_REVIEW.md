# ACP2 整改执行计划独立复核

审查日期：2026-10-26。审查对象：[EXECUTION_PLAN.md](EXECUTION_PLAN.md)，747 行，SHA-256 `83e1a212e48c8c210554defabd2aa2c554fcadd276450f1a40dc1aa0c236ae55`。代码基线：`0d739b2fba01cd11c8e5342396a6e085e301a89b`。

**结论：整改方向总体合理，建议有条件采纳；当前版本尚不适合直接作为全部实现任务的冻结基线。** 主要问题不是 G01–G26 是否应修，而是若干任务低估了现有组件之间的语义差异：把调用一次任务等同于一次 QC、把返回 TaskResult 等同于科学完成、把提交摘要等同于 worker 冻结摘要，以及认为复用 FrozenWorker 就自动覆盖 DAG 中间写入。这些假设会在整改过程中重新产生错误成功、预算超限或恢复不一致。

本次提出 **12 项计划审查意见 R01–R12**，其中 R01–R09 应在相关任务实现前补齐，R10–R12 应在对应证据门、CI 门和协议契约冻结前调整。它们是对整改方案的补充/修正，不另起 G27–G38，也不意味着现有未接线组件已在生产造成事故。**原执行计划与用户修改均未覆盖；本次没有实现业务修复。**

## 1. 审查依据、范围与证据强度

审查覆盖从 RPH/DSH/Workbench 到 MCP/HTTP/CLI，再到 Controller、身份、幂等、审批、Data、ResearchRecord、预算、冻结组、worker、CCCP Recipe/任务、回执、LSF、插件发现及发行验收的关键链路。不是逐行认证整个仓库，也不是一次完整 ACP2 发行验收。

- 当前 HEAD 相对 R3 审计基线 `ec4a0b39b26645139c01aa9578fbedd0c3db23a2` 仅增加 `.gitignore` 的 12 行。因此旧审计的代码事实没有因本次提交变化而自动失效；历史真实运行证据仍需按候选影响评估复用。
- 完整读取 32 个任务和 D01–D10，逐项核对实现驻点、依赖、关闭条件与现有测试；关键组件使用真实模块的隔离探针验证。
- 定向运行 `test_acp2_runtime_science_binding.py`、`test_acp2_science_recipe.py`、`test_cccp_task_optimize.py`：**71 passed，0 failed，0 skipped**。这证明已有定向回归基线通过，不证明下面缺少覆盖的组合正确。
- 全量只读 Ruff 检查：**1,065 条诊断，涉及 130 个文件；182 个文件需要格式化，940 个已符合格式**。工具版本 `ruff 0.15.22`。
- 没有调用真实 QC、付费模型、生产 Controller、生产 run_root 或 LSF；没有重启服务。探针使用临时数据库与目录，科学调用边界受控替代。不能把这些证据标为真实科学或真实模型验收。

可复核证据：[总结果](evidence/plan-review-20261026/verification-summary.json)、[探针源码](evidence/plan-review-20261026/probe_plan_assumptions.py)、[探针原始结果](evidence/plan-review-20261026/assumptions.json)、[JUnit](evidence/plan-review-20261026/focused-tests.xml)、[静态检索范围](evidence/plan-review-20261026/static-checks.json)。本报告中的“实测”仅指上述边界；“设计风险”表示由源码和拟议接线推导，尚未进行生产场景验证。

## 2. 可以保留的核心裁决

| 裁决 | 审查判断 | 需要限定的部分 |
|---|---|---|
| D01 删除 MCP/Bridge 跨调用结果缓存 | 合理，直接对应错误重放与撤权失效 | 继续保留 Controller 持久幂等；prepare 与 submit 的身份模型分别处理；补历史迁移 R09 |
| D02 身份先于缓存修复 | 合理，顺序必须保留 | 新值对象应投影既有认证结果，不能成为另一身份权威；迁移不能只测试新数据库 |
| D03 一个应用命令、多入口投影 | 合理且必要 | 应比较应用错误码与有效 JobSpec，不强求所有传输都返回 HTTP 400 |
| D04 三根版本轴独立 | 合理 | 提前用实际 DSH/SDK 验证扩展协商路径，见 R12 |
| D05 JSON 契约与真实消费者门 | 合理 | 生成快照与真实工具列表都要验；保持 import/反射隔离门，不因线协议字符串放宽依赖方向 |
| D06 各类摘要分离 | 方向正确，当前定义还不完整 | 补 worker 冻结体摘要、Recipe 定义身份与运行时 step 身份映射，见 R04 |
| D07 科学报告、归属、研究决策分离 | 合理 | 科学完成与研究结论均不得由“存在返回值”推断；复用既有科学完成语义，见 R01 |
| D08 平台记录是研究恢复权威 | 合理 | 跨库 Saga 与每库事务性 outbox 需要实际实现；DAG 中间持久化也要纳入执行权，见 R05 |
| D09 逐维声明 enforced/recorded-only | 合理，但表中部分强制点不成立 | QC 计数、真实 deadline、规划前模型额度分别见 R02/R03/R08；可信历史 usage 见 R06 |
| D10 跨端门、证据索引、BOM 前置 | 合理 | live 判据应逐场景定义；全量 lint 硬门需历史基线策略，见 R10/R11 |

特别认可计划已纠正的边界：不删除兼容 shim、不另建 jobs 状态机、不绕过 Services 单向依赖、不把 token/core-hours 的记录能力写成硬限制、不把多个 SHA 本身认定为失败、不把 private tgz 当作必须公开发布的问题、不以截图代替平台事实，以及不把 DA 命题必须 supported 当成验收前提。这些应继续保留。

## 3. 必须补齐的实现语义

### R01 — M2-04 不能把现有 CCCP adapter 当作可直接接线的完成判定器

**优先级：高；性质：实测缺陷 + 拟议接线风险。影响：M1-03、M2-04、M4-01/M4-04。**

计划第 428 行指定 `BoundedStepDagExecutor(dispatch=cccp_compute_dispatch)`。但 [step_dag.py](../../src/acp_services/runtime/step_dag.py) 的 `DispatchFn`（752 行）只接收一个 context，`cccp_compute_dispatch`（1074 行）还要求第二个 request。直接注入得到 `TypeError: ... missing ... request`。即使补 wrapper，目前 adapter 也只序列化 TaskResult 并返回 `StepOutcome`；executor 在 938 行将正常返回的 outcome 无条件记为 completed。

实测结果如下，均经过真实 DAG 和真实 adapter；仅 `run_singlepoint` 返回值被受控替代：

| TaskResult | 既有 worker 完成判定 | 当前 DAG 判定 |
|---|---|---|
| `status=failed, complete=false` | failed | **completed** |
| `status=completed, complete=false` | failed | **completed** |
| `status=completed, complete=true`，缺 energy | failed | **completed** |
| `status=completed, complete=true, energy=-40.0` | completed | completed，正向对照 |

此外，step 声明产物为 `energy`，adapter 实际只交付名为 `result` 的整个 JSON 摘要；这不是可供下游按声明消费的科学产物映射。既有 [worker.py](../../src/acp_services/runtime/worker.py) 的 `TASK_COMPLETION_AUTHORITIES`（659 行）、`outcome_from_task_result`（1220 行）已经处理这些差异。

**建议补入 M2-04：** 明确实现“冻结 step + 已验证上游工件 → 类型化 TaskRequest/TaskContext → 任务结果资格判定 → 声明产物及摘要”的唯一 adapter。复用既有完成真值表或提取其共同权威，不再复制一套。为每 step 明确 output_dir、input_base、配置和输入角色，不能直接把计划 dict 交给 runner。失败、partial、diagnostic-only、缺必需产物均不得正常完成或释放正常下游。

**关闭证据：** 上述四个反例/正例、产物缺失与产物类型不符、真实上游文件到下游 request、必需 step blocked 时整体非 completed；同时保留发布失败仅重试发布的测试。当前组件尚未接入生产，所以本项不声称生产 DAG 已发生错误成功。

### R02 — 在 step dispatch 前扣一次额度，不能证明 qc_invocation 硬上限

**优先级：高；性质：实测计数失配。影响：D09、M1-04、M2-03/M2-04。**

[step_dag.py](../../src/acp_services/runtime/step_dag.py) 的 `StepSpec.qc_invocations` 默认 1（202 行），`_reserve_budget`（873 行）按声明次数扣减。真实 [optimize.py](../../src/cccp/calculation/tasks/optimize.py) 在首次 `_run_attempt`（464 行）失败后，可在 rescue 循环继续调用（504–533 行）。因此任务核心调用次数不是 QC 后端调用次数。

隔离探针运行**真实 optimize 任务核心**，后端依次返回失败、失败、成功：预算上限 1，DAG 已计数 1，后端实际调用 **3 次**，DAG 最终 completed。真实 QC 进程启动数为 0；这里证明的是现有任务/后端调用粒度不等价，不冒充真实软件运行证据。

**建议修正强制点：** 可采用任务以下、每次昂贵执行前的额度端口，或经过证明的整个任务最坏执行次数预留。前者必须依赖倒置，不能让 CCCP 导入 ACP；后者必须覆盖 rescue、内部子任务和重试，不能随便给每 step 写 1。不要为了适配预算删减科学 rescue 算法。worker 使用由持久 reservation 授予的有限额度及可信回执，不接入 Controller jobs DB；进程重启、执行权转移和结果未知时不得重新获得一份未扣减额度。

**关闭证据：** cap=1 下第二次执行被预先阻断，或首次执行前因最坏额度不足整体拒绝；并发、重启、rescue、部分失败、回执丢失均不突破授权额度。完成该证据前只能声明任务/step 计数已约束，不能声明 QC invocation 已强制。

### R03 — ResourceBound 校验不等于运行时间与排队 deadline 已强制

**优先级：高；性质：源码确认 + LSF 参数实测。影响：D09、M2-03、M4-03。**

[local_admission.py](../../src/acp_services/runtime/local_admission.py) 的 `request_for_spec`（436 行）把 walltime 读成 AdmissionRequest.duration_s；这可以约束申请值，本身不会在运行到期后停止进程。对 `queue_wait_s/science_runtime_s` 的 Runtime/scheduler 检索只命中 [research_budget.py](../../src/acp_services/runtime/research_budget.py) 的解析/元数据路径，不能据此认定调度 deadline 已接线。

[script_gen.py](../../src/acp/scheduler/remote/script_gen.py) 的 `derive_lsf_resources`（617 行）保留函数参数 walltime，不读取请求中的 `resources.walltime_s`。实测请求 1 秒、函数配置 `24:00`，结果仍为 `24:00`。这不表示 LSF 没有自己的限时功能，而是**用户审批/准入的限时尚未被该函数映射到执行限制**。

**建议新增明确子任务：** 定义 queue deadline、science runtime、每 attempt walltime 的时钟起点与优先关系，持久保存截止时间；明确 PAUSED 是否计时、重启如何恢复剩余时间；本地到期走进程组终止并确认，远程映射 LSF `-W` 并结合当前 attempt 的控制/CAS 语义；到期后不能被迟到成功观测复活。等待超时不等于停止计算，不能用 RPH wait 超时替代本项。

**关闭证据：** 小时限的真实受控进程、队列过期、重启、pause/cancel 竞争、LSF 参数与原生终止回执。缺实际执行限制时，能力表必须保留未验证状态。

### R04 — “双摘要”不足以描述冻结组与运行时 step 身份

**优先级：高；性质：实测不同摘要域 + 契约衔接缺口。影响：D06、M1-04、M2-04、回执恢复。**

计划已经区分科学计划、执行计划、proposal、request 等摘要，方向正确。但现有代码至少还有以下不同事实，不能都用第二项“执行 plan_digest”覆盖：

| 身份域 | 当前实现 | 用途 |
|---|---|---|
| 提交/审批计划摘要 | [canonical_digest.py](../../src/acp_services/runtime/canonical_digest.py)，571/602 行，`sha256:` | 冻结 workflow/input/method/resources 的授权投影 |
| worker 冻结体摘要 | [worker.py](../../src/acp_services/runtime/worker.py)，178 行，`v2:` | 校验 payload、steps、symbols 等冻结内容未被替换 |
| Recipe 定义摘要 | [recipes/compile.py](../../src/cccp/calculation/recipes/compile.py) | 科学 Recipe 定义；相同前缀不表示相同摘要域 |
| step 执行身份 | 同文件 `materialize_step_identity`，1257 行 | 必须包含实际上游产物摘要，运行时才能完整形成 |

探针显示：缺实际 upstream digest 时 materialization 抛 `UnresolvedOutputRefError`；同一 Recipe 定义下，上游摘要 A/B 产生不同 step 身份。与此同时，[attempt_freeze.py](../../src/acp_services/runtime/attempt_freeze.py) 在 219 行要求 envelope 的 step identities 等于冻结 plan；[receipts.py](../../src/acp_services/runtime/receipts.py) 在 143 行再次要求 receipt 与 envelope 的 step identities 相等。

**建议在 M1 先冻结映射：** 为每个域规定名称、规范输入、版本与校验者；明确哪些是 freeze 时可得的定义身份，哪些是 dispatch 时绑定的执行身份。可在不可变 envelope 中保存定义/模板身份，在逐 step 回执与工件链中保存运行时身份，再由 Controller 验证完整归属；具体结构应走版本化契约。不能在执行时偷偷修改冻结 envelope，也不能用占位摘要通过相等检查。既有 wire 字段若不能立即改名，至少在内部类型与新契约中消除歧义并保留兼容策略。

**关闭证据：** 多步 Recipe 上游实际产物变化只使正确下游身份失效；伪造 step 回执拒绝；审批摘要、冻结体摘要各自变化被正确检查；恢复不收养不属于当前输入/产物的步骤。

### R05 — 复用 FrozenWorker 不会自动保护 DAG 中途 checkpoint 和发布写入

**优先级：高；性质：拟议接线的设计风险。影响：M2-04、M3-04、M4-01/M4-03。**

[worker_bootstrap.py](../../src/acp_services/worker_bootstrap.py) 的 `WorkerAssembly.run`（688 行）获得执行权，并将 `before_write` 传给 FrozenWorker。[worker.py](../../src/acp_services/runtime/worker.py) 的执行顺序是 `executor.execute(scope)` 返回后，才在 1418 行附近重验执行权，再写最终 task_result/receipt。这个边界能保护已有最终写入，不能自动覆盖新 executor 内部发生的逐 step checkpoint 和 PublishFn。

计划要求持久 DAG state、逐 step 发布、接管后恢复；当前 [step_dag.py](../../src/acp_services/runtime/step_dag.py) 没有执行权/fencing 接口。若直接接入，失去执行权的旧 worker 仍可能在最终拒绝之前发布中间权威状态。这里只指出接线风险，未声称已复现生产双 worker 竞态。

**建议补入 M2-04：** 所有权威 checkpoint/产物提交都绑定 attempt、owner、fencing；每次新 step dispatch 前检查执行权及控制意图。失权结果最多保存在非权威暂存区；提交必须可原子判定其仍属于有效执行者。worker 仍通过既有端口/授权机制工作，不直接读 Controller jobs DB。cancel/pause/takeover 后禁止旧执行者继续启动后续步骤。

**关闭证据：** 两个 worker 竞争、计算返回与 checkpoint 之间失权、发布前失权、cancel 后下一步不得启动、接管后恢复同一身份。只测试最终 receipt 被拒绝不足以关闭本项。

### R06 — 可信历史用量与“推进当前任务状态”的回执验证上下文必须分开

**优先级：高；性质：实测校验边界，现有校验器本身没有错。影响：M0-07、M2-03。**

计划要求复用 `verify_receipt`，这是合理的基础，但 [receipts.py](../../src/acp_services/runtime/receipts.py)（74 行）明确要求 receipt 同时匹配**当前** attempt/submission/release/owner/fencing，服务于当前任务状态推进。探针中同一有效 receipt 在 current_attempt 增加后返回 `stale_attempt`，符合其设计。

[metering_decoupling.py](../../src/acp_services/runtime/metering_decoupling.py) 的 `UsageIngestChannel`（291 行）则支持与科学/发布状态解耦的用量入账。若结算直接照搬“当前 job”验证上下文，attempt 1 的真实迟到用量会在 attempt 2 启动后永久丢失，失败或发布失败的计算也可能漏记。

**建议补入 M0-07：** 状态推进继续采用 current-attempt guard；历史 usage 使用原 attempt 的不可变 envelope、reservation、producer 身份与回执链验证，按原身份幂等入账，绝不推进当前任务状态。可以共享验证原语，但必须显式区分上下文。入口先做专用 service/worker/proxy capability 授权，不能以 payload 的 trusted_source 替代认证。

**关闭证据：** attempt 2 已开始后收到 attempt 1 真实回执，只计费一次且不改变当前状态；伪造/错误 reservation 拒绝；失败科学、失败发布仍正确结算。保留已有“G26 未绕过 TaskRef 上限”的反证，不能扩大既有审计结论。

### R07 — P3 有表单任务和验收任务，但生产插件生命周期尚缺明确实现任务

**优先级：高；性质：生产装配缺口。影响：M2-02、M3-06、M4-05、M5-01。**

M3-06 正确限定了 Schema 子集；M4-05 又要求公开路径安装、授权启用、发现、执行、升级和卸载。当前 [plugin/host.py](../../src/cccp/plugin/host.py) 的 PluginHost/entry-point discovery 是可复用库；对 ACP/Services 的检索未发现其装配调用。[v2_science.py](../../src/acp/api/v2_science.py) 的 `_capability_descriptors`（118 行）使用 builtin/domain catalog 并缓存到 app state，没有形成安装/启用变化到生产发现的路径。

**建议明确 B+A 实现子任务，置于 M3-06/M4-05 前：** 受控安装方式、持久授权启用状态、平台 registry 合并与发现缓存失效、capability 到既有 Task/analysis 执行映射、插件 distribution/version/digest 与 worker release 的冻结、升级/卸载后的历史读取。可复用包安装工具，不要求新增插件商城或安装 GUI；首个插件也可限定为纯分析能力，但要明确如何通过公开入口执行。

**关闭证据：** 新进程从安装/启用事实发现插件；不手工 register、不改 ACP 源码；新任务冻结新版本；旧任务保持原 descriptor/artifact。只有“新增 form 后页面出现字段”不能证明这条链已实现。

### R08 — 首次规划发生在审批和 TaskRef 之前，模型预算不能依附科学任务才创建

**优先级：高；性质：生命周期设计缺口。影响：M1-02/M1-04、M2-03、M3-03。**

[supervisor.ts](../../rph/packages/dsh-bundle/skills/supervisor.ts) 的 understand/hypothesize/critique/plan 位于 approval 之前（421–564 行）。而现有 [local_admission.py](../../src/acp_services/runtime/local_admission.py) 的 `reserve_budget_for_claim`（521 行）以已有任务 claim/TaskRef/attempt 为中心。M0-07 的 reservation/job/attempt/submission/release 绑定也不能原样套到尚无科学任务的模型请求上。

**建议在 M1 增加两阶段授权语义：** 首次模型调用前，按认证 principal/project/research/session 授予规划额度；模型调用产生 proposal 后，再走科学执行审批和 TaskRef reservation。两者复用现有持久预算账本及状态机制，但身份绑定分别明确。gateway 的模型回执绑定 research/round/call/request/model，不伪造 job/attempt。明确超时、流式中断、结果未知、重试的扣减/对账规则，避免“先生成方案才能申请模型额度”的循环依赖。

**关闭证据：** 空 research 尚无 proposal/TaskRef 时，有限额度内首次调用成功；额度拒绝时零调用；变更 run_id、并发、重启或中断不重置额度；科学审批被拒绝不抹掉已发生模型用量。生产凭据只由 gateway 持有、DSH 无绕过直连的要求应保留。M3-03 中未定义的 `ACR` 也应改成明确的现有组件名或先给出定义。

### R09 — 身份、幂等摘要和 Data 索引变更，需要在 M0 就规定旧数据迁移

**优先级：高；性质：实测兼容冲突。影响：M0-04/M0-05/M0-06，不能全部推迟到 M4-06。**

保留 [store.py](../../src/acp_services/store/store.py) 的跨 scope 同 action/raw-key 防抢占守卫（1113 行附近）是正确决定。但旧错误实现已用 local principal 写入 claim：探针先以旧 local scope 建立 claim，再以真实 Alice scope、同 key/同请求 claim，结果为 **conflict**。这不是守卫缺陷，而是修身份后必须处理的历史兼容问题。全量 request_digest 的新规范也可能使旧合法重试不再匹配。

prepare 的旧部分唯一索引与 operation 身份同样不能仅靠加入 principal/project 列就获得可信历史归属。M4-06 的完整升级演练是必要的，但不能替代 M0 的迁移设计与回归 fixtures。

**建议补入 M0：** 对旧 claim 的 pending/committed/结果未知分别定义对账策略；保持既有 TaskRef 与原始请求记录可追溯。只有具备可验证历史归属才允许迁移/映射；归属不明应显式冲突并进入受控对账，不可把旧记录自动认领给当前调用者，也不可提示客户端换新 key 后盲目再提交。Data prepare 的旧 operation 复用、内容摘要冲突和旧权限信息分别规定兼容范围。迁移用 additive/versioned 方式并可恢复。

**关闭证据：** 使用旧版真实行形状升级，包含已完成提交、未知提交结果、跨用户同 key、旧 prepare、旧摘要版本；升级后既不越权，也不重复昂贵任务。之后 M4-06 再用发布制品验证整条升级/回滚链。

## 4. 证据、CI 与依赖顺序的调整

### R10 — 不应全局拒绝 live_model_calls=0 的 live 证据

**优先级：中；影响：M0-03、M4/M5。**

M0-03 第 271 行要求 validator 拒绝“外层 live、内层 offline_fixture 或 live_model_calls=0”。拒绝 fixture 冒充 live 合理，但“零模型调用即非 live”适用范围过大：真实 HTTP/MCP、浏览器、LSF 或制品安装场景不一定需要模型；预算拒绝负例甚至必须证明实际模型调用为 0。

**建议替换为逐场景 required-observations：** research-loop-live 要求真实模型和 Controller 事件；LSF 场景要求原生 submission/receipt；浏览器场景要求网络和平台事实；预算拒绝场景要求真实拒绝且调用计数为 0。保留 `fixture|live|aggregate` 与 PASS/FAIL/NOT_VERIFIED 两条独立轴，不新增混合枚举。未执行 required observation 才阻止对应场景 PASS。

### R11 — 全量 Ruff 作为每 PR 新硬门，会被现存基线阻塞

**优先级：中；影响：M0-02、§11。**

§11 直接把全量 `ruff check src tests` 和 `ruff format --check src tests` 列为 PR 必跑门，但没有消除既存违规的任务。当前 [ci.yml](../../.github/workflows/ci.yml) 明确延后历史 lint；本次实际检查得到 1,065 条诊断、182 个格式不符文件。不能把整改 PR 的失败全部解释成该 PR 引入。

**建议二选一写入计划：** 若要求从第一天全库零违规，增加独立基线清理任务并评估变更面；否则建立固定基线与不得新增违规的比较门，对新文件/修改范围严格执行，另行逐步清债。原有架构/Services 守护仍全量执行，不允许借基线机制放宽它们。格式化大批无关代码不应隐含塞入安全/幂等修复 PR。记录 Ruff 版本，确保计数与规则可复现。

### R12 — 实际 DSH 扩展协商路径应在 M1 契约冻结前验证

**优先级：中；影响：D04、M0-08、M1-01、M3-01。**

计划把真实 PlatformClient 的 DSH `ctx.tools` / 官方 SDK 取舍留到 M3-01，但 M1-01 已要冻结研究扩展协商。当前外部 DSH [connection.ts](/ACPServer/acp2-tools/deepseek-harness/packages/mcp/mcp-client/src/connection.ts)（258 行）创建 Client 时使用 `capabilities: {}` 和自动版本协商；不能假定其自动发出 ACP 私有扩展协商参数。这并不证明 SDK 不支持某种扩展，只说明当前生产 client 路径尚不能作为该能力的证据。

**建议前置一个无模型/QC 的小型接线验证：** 锁定实际 client/transport、如何获得并协商研究扩展、身份与当前 project 如何传播、工具权限与版本不匹配如何失败。若走 application capability handshake，明确其与 MCP initialize 的职责；若走官方 SDK 的扩展机制，用真实 SDK 测试证明。保留受支持的官方路径，不默认引入 DSH 私有 fork。先验证这一选择，再冻结 M1 契约，M3 继续完成产品集成。

## 5. 对全部 32 个任务的逐项意见

“保留”表示任务目标和主要拆分合理，不表示任务已完成；“补充/拆分”表示应在执行前调整卡片，原 G 项仍为 OPEN。

| 任务 | 结论 | 审查意见 |
|---|---|---|
| M0-01 基线/台账 | 保留 | 新 evidence 根、不覆盖历史；额外记录本报告 R 项到其所属任务，无需平行 G 编号体系 |
| M0-02 真实消费者 CI | 调整门策略 | 跨 Python/TS/SDK 门必要；补 R11，避免全库 lint 历史债阻塞每个功能修复 |
| M0-03 证据标签/BOM 骨架 | 修正判据 | 按 R10 逐场景定义观察条件，不能全局要求模型调用大于零 |
| M0-04 CommandIdentity | 保留并补迁移 | R09；从已有 VerifiedRequestIdentity 派生，key 字段保存 key_id 而非原始 secret |
| M0-05 删除缓存/撤权 | 保留并补迁移 | Controller 为重放权威；prepare 历史身份与索引按 R09 处理 |
| M0-06 字段/JobSpec 等价 | 保留并修措辞 | 比较应用错误码；stdio MCP 没有 HTTP 400，HTTP DTO 校验也可能为 422 |
| M0-07 可信 usage | 补身份上下文 | R06；区分当前状态回执、历史执行用量、模型 gateway 用量 |
| M0-08 MCP 握手/ADR | 保留并前置试验 | R12；基础握手与研究应用协商分别验证 |
| M1-01 应用/MCP/Artifact/Analysis 契约 | 保留 | 必须真实生产/消费闭合；扩展协商方案先有实际 client 证据 |
| M1-02 ResearchRecord/事件/恢复 | 补模型前置身份 | R08；明确尚无 proposal/TaskRef 的 research 及规划额度身份 |
| M1-03 Evidence/TaskRef/Decision | 保留 | 对接既有完成真值表与 R04 的运行时身份链，避免把完成事件直接映射 accepted |
| M1-04 输入/摘要/策略/预算 | 必须补全后冻结 | R02/R03/R04/R08；该任务承载多个全局约束，不能只冻结 TS/Python 字段名 |
| M1-05 ADR-008/DA 判据 | 保留 | 预先限定科学范围、原子映射与不充分判据，拒绝倒推阈值 |
| M2-01 Research writer/Saga | 建议拆分检查点 | 每库本地事务+outbox、跨库 intent 对账、P2 API/工具分别验收；不能假定现有 delivery service 已提供跨库事务 |
| M2-02 Artifact/发现/analyze | 补插件装配 | R07；明确 builtin/第三方能力共享公开发现入口，真实 analysis 仍独立验收 |
| M2-03 审批/预算 | 建议拆分检查点 | approval policy、额度持久预留、deadline、规划额度分别可验；R02/R03/R06/R08 |
| M2-04 DAG 生产接线 | 必须扩充且分段 | R01/R02/R04/R05；request/result adapter、身份与回执、预算/执行权、公开入口分别通过后再做 DA |
| M3-01 PlatformClient/Bridge | 保留，部分前置 | R12 的小型选择验证移到 M1 前；此任务完成正式产品 client |
| M3-02 compose/CLI/profile/doctor | 保留 | 原计划 a/b 分段已避免依赖循环；doctor 验证安装后真实端口，不依赖开发目录 |
| M3-03 Planner/gateway | 补规划额度生命周期 | R08；科学确认与模型额度是不同授权，定义 timeout/unknown/retry 记账 |
| M3-04 Supervisor/恢复/决策 | 保留 | 崩溃矩阵合理；加入 R05 接管后无旧 worker 中间权威写入的联动验证 |
| M3-05 Workbench 审批/提交 | 保留 | 注入 commands、删除虚假成功、跨客户端平台事实是正确验收方式 |
| M3-06 P3 动态表单 | 保留并增前置 | 白名单 Schema 子集合理；依赖 R07 生产插件装配，而非测试手工注册 |
| M4-01 无 QC 全链 | 保留并扩反例 | 加 R01–R09 的低成本组合；fixture 证据不得升级为真实科学 |
| M4-02 真 DSH/Local/浏览器 | 保留，需环境验证 | 真实工具/模型反馈和同一 TaskRef；本次未执行，不能预判 PASS |
| M4-03 LSF/控制/恢复 | 保留并增限时事实 | R03/R05；审批限时、实际 bsub 参数、终止回执三者一致 |
| M4-04 DA 纵切 | 保留，需环境验证 | 链路成功与 claim supported 分开；M2-04 完整性先通过 |
| M4-05 第三方插件纵切 | 保留并补实现前置 | R07；不要到验收阶段才发现缺少授权启用和 worker 制品装配 |
| M4-06 跨领域/升级/可靠性 | 保留 | 整体制品升级仍需执行；M0 自身迁移测试按 R09 提前，不等此任务才设计 |
| M5-01 全组件制品 | 保留 | root/三角色互斥、RPH tgz、安装后静态资产正确；插件版本/资产纳入可取回制品 |
| M5-02 BOM/覆盖矩阵 | 保留 | 只读历史、候选映射、可取回 bytes 和机械计算合理；应用 R10 场景观察条件 |
| M5-03 独立操作者移交 | 保留 | 独立安装与生产部署分离正确；缺 required 行不放行完整 ACP2 |

## 6. 需要明确但不必扩大为新工程的细节

1. **身份对象复用。** [http_identity.py](../../src/acp_services/workspace/http_identity.py)（106 行）已有 frozen `VerifiedRequestIdentity`。新的 CommandIdentity 应是其跨入口最小投影；不能让 payload、缓存或单独的 identity resolver 再决定 principal。客户端原始 API secret 不进入持久命令、事件、BOM。
2. **Saga 的复用边界。** [research_resources.py](../../src/acp_services/data/research_resources.py) 的 ResearchResourceStore 具有自己的 SQLite/CAS；[outbox.py](../../src/acp_services/runtime/outbox.py) 的 OutboxDeliveryService 负责投递已提交记录。复用这些组件合理，但前者当前没有 outbox 集成，后者不会自动创建跨库原子性。M2-01 要明确各库“业务写+本地 outbox”同事务的 API、重试责任者和对账状态；不要新造通用分布式事务框架。
3. **计划规模。** 32 项中有 9 个 XL、18 个 L、4 个 M、1 个 S；这是架构整合与交付工程，不宜把 M2-04、M2-03、M3-04 当成一次普通接线 PR。建议保留编号，拆成有独立负例的子检查点，不凭本次审查虚估工期。
4. **不额外扩产品承诺。** 第三方插件的 GUI 安装器、通用 JSON Schema 求值器、累计 token/core-hours 硬限制都不是本报告新增要求。若选择缩窄发行范围，应按原计划的 scope 决策更新 required rows，而不是偷偷跳过。

## 7. 建议的最小计划修订顺序

1. **可先推进 M0 的基线和低成本修复。** 在 M0-01/M0-02 中纳入本报告探针的期望正确行为；同时确定 R10/R11 的证据与 CI 基线策略。原审计脚本只读保存，永久测试不要断言缺陷行为为正确。
2. **M0 身份/幂等/usage 修复前补 R06/R09。** 明确历史对账与迁移 fixtures，避免只对空数据库正确。删除缓存必须在认证身份链正确后进行，维持原有依赖顺序。
3. **M1 冻结前解决 R04/R08/R12，定义 R02/R03 强制边界。** 这些会影响 wire 契约、审批和冻结组，延迟到 M3/M4 会导致跨端返工。
4. **M2-04 分四个检查点。** 请求/结果 adapter → 定义/运行时身份与回执 → 额度/执行权/持久恢复 → 公开入口 worker 装配。先完成无 QC 的反例闭合，再进入真实科学与 DA。
5. **为 P3 增加 R07 的生产装配子任务。** 与表单开发并行准备，但插件纵切必须在真实装配路径上验收。
6. **保持 M4/M5 真实性要求。** 71 个定向测试通过、探针运行和文档审查都不能关闭真模型/浏览器/QC/LSF/独立安装行；缺环境时保留 NOT_VERIFIED。

## 8. 最终审查判定与复现说明

**可以采纳的部分：** G01–G26 的整改必要性、Controller 单一身份/幂等权威、跨入口命令一致性、科学与研究决策分离、平台持久恢复、真实消费者门、候选 BOM 与独立交付。

**需要修订后再冻结的部分：** R01–R09 的执行/恢复/预算/迁移语义，以及 R10–R12 的验收判据与依赖顺序。尤其不能以“已有 cccp_compute_dispatch”“已有 ResourceBound”“已有 verify_receipt”“已有 PluginHost”为依据直接判定生产接线工作已经充分设计。

证据目录：[plan-review-20261026](evidence/plan-review-20261026/)。探针可在仓库根用以下命令重跑（只写隔离探针输出和临时目录）：

```bash
python3.11 reports/acp2_delivery_gap_audit_20261026/evidence/plan-review-20261026/probe_plan_assumptions.py
pytest -m 'not slow' -q tests/test_acp2_runtime_science_binding.py tests/test_acp2_science_recipe.py tests/test_cccp_task_optimize.py --disable-warnings
ruff check --no-cache src tests
ruff format --check --no-cache src tests
```

Ruff 命令在本基线预期非零退出，用于重现历史存量，不代表本报告修改引入违规。逐文件摘要与校验清单分别保存在证据目录的 `source-hashes.json`、`SHA256SUMS`；计划原文摘要复核结果保存在 `review-validation.json`。工作树预存的历史 integration 回执与 baseline 路径修改保持原样，本次新增内容仅限审查报告及其证据。
