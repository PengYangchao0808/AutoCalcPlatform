# ACP 2.0 交付差距整改执行计划

**日期**：2026-10-26  
**状态**：`PROPOSED`；尚未实施，G01–G26 全部保持 `OPEN`  
**输入**：[差距报告](GAP_REPORT.md)、[第三轮真实性核验](VERIFICATION_R3.md)、[独立计划审查](EXECUTION_PLAN_REVIEW.md)、[库状态独立评估](PLAN_ASSESSMENT_20261026.md)、[四组分主方案](../../docs/ACP_2.0_Four_Component_Collaboration_Plan_20261020.md)、[H0–H7 执行索引](../../docs/reference/next-change/ACP_2.0_Parallel_Execution_Index_20261020.md)、[H7 交接契约](../../docs/reference/next-change/acp2-contracts/science/handoff-H7.json)及其证据  
**本次修订**：吸收独立审查 R01–R12 中经源码与证据核对成立的部分；R 项归入统一任务体系，不新增平行 G 缺陷编号；2026-10-26 第二轮独立复核见 §19（事实核对、三类表述限定与六处细节补入）  
**合并后规模**：39 个任务（新增 M0-12 拆分决策闸门，M0-13 为 DSH 外部变更条件任务）；M0-02/M1-01/M1-04/M2-04/M4-06 拆分子检查点，plan-graph.json 为唯一关键路径权威  
**本轮变更摘要**：补环境/授权、三级候选、B 并发、决策冻结和机械门；进一步将 fencing/meter/资源预检列为新工作项，加入唯一 scope 表、G→H 映射、可重算 plan graph、检查点/DoD 分档与对抗性输入门；无新增 G 缺陷编号  
**审计源码基线**：`ec4a0b39b26645139c01aa9578fbedd0c3db23a2`  
**规划时 HEAD**：`0d739b2fba01cd11c8e5342396a6e085e301a89b`  
**默认目标**：完成 G01–G26 整改；交付范围、DEFERRED 与关闭口径唯一由 M0-01 的 scope 表决定。Candidate-Full 只在全部 IN_SCOPE required rows 通过后成立；DEFERRED/NOT_VERIFIED 不得被计作已实现，也不得以签字、环境缺失或缩小名称代替实现

---

## 0. 计划使用方式

本文把差距报告转换成有 owner、有先后、有生产驻点、有反例和有阶段门的执行任务。旧 A/B/C 计划继续作为领域设计背景；若旧任务已标 `[x]` 而本文对应差距仍开放，以本文的跨端关闭条件为准，不回写历史回执。

### 0.1 基线判断

已核对 `ec4a0b3..0d739b2`：`src/`、`rph/`、`frontend/`、`tests/`、`docs/`、`scripts/`、`.github/`、打包配置均无差异，仅 `.gitignore` 改变。因此报告中的代码事实可直接用于本轮规划，但每个实施批次仍须在新的干净候选上重跑反例。

规划核查另外确认：

- 一个规划会话复跑了前端模块、Recipe/science-binding、MCP/多用户/持久幂等定向测试，均通过；§19.4 另一个复核会话则因未安装本仓包而无法重跑。二者是不同环境/时点，均不是候选回执，不能互相冒充或作为正式验收证据。DAG 生产调用点仍缺失，R3 的跨层反例也不被这些绿灯覆盖。
- CI 的 RPH job 在上传前没有执行 package，却要求 `rph/dist-packages/*.tgz` 必须存在；前端 `run_all.js` 没有独立 CI job。
- 当前 B25 盘面已有部分 whl/tar.gz 可取回，但仍有历史摘要引用缺失制品或失效相对路径的情况；不是“全部制品不存在”。不得编辑旧 summary 迁就盘面，应建立统一制品库、URI 和摘要校验。
- 当前工作树有用户修改和未跟踪证据。实施必须从新的、干净的集成 checkout/worktree 开始，不在本工作树上重置或覆盖任何文件。

### 0.2 范围规则

1. **默认是完整 ACP2 整改**，不是只修 MCP，也不是只补验收材料。
2. 若决定首期只交付“本地、限定工作流、人工提交的计算工作台”，必须另发 scope-change：明确哪些 G 项为 `DEFERRED`、产品名称与能力声明同步缩窄。不得仍称完整 Agent 研究产品，也不得把未执行项写成 `PASS`。
3. CDXML 继续按主方案为 `DEFERRED`，不因本计划重新纳入。
4. P2 的固定科学问题表单与 P3 的 Schema-Driven 插件表单分别验收，互不替代。
5. 真实模型、QC、LSF、浏览器和独立安装是不同证据层；任何一层缺失都保持 `NOT_VERIFIED`，不能被其他层的 PASS 覆盖。
6. 本计划的初始 scope 如下，M0-01 将其机械化为唯一 `docs/reference/next-change/acp2-contracts/release/scope.json`；后续修改必须新候选和理由：

| scope | 本期定义 |
|---|---|
| IN_SCOPE | G01–G26 整改；P0–P2 研究/科学闭环；P3 纯分析插件与 Schema form；H6 多用户/跨领域/迁移；独立安装与证据移交 |
| DEFERRED | worker-hosted 第三方 COMPUTE、CDXML、累计 core-hours/token 硬强制；不计作已实现，若升为 required 则 Full 保持 OPEN |
| OUT_OF_SCOPE | 插件商城/GUI、通用 JSON Schema 求值器、2PC、第二 QC 引擎、公共 npm 发布、生产部署/重启、默认全库 lint 清理 |

### 0.3 不可变边界

- Controller/JobManager 是任务、attempt、CAS、提交幂等和授权的唯一权威；MCP/Bridge 不建立第二套结果重放权威。
- 新科学计算仍只落 `src/cccp/calculation/tasks/`；DAG 只组合既有 Task 核心，不创建第二 QC 引擎。
- `acp_services` 禁止导入 `acp`；旧路径保持 identity forwarder。
- ResearchRecord 保存研究事实与引用，JobStore 保存任务事实，ApprovalService 保存审批事实，Admission ledger 保存预算事实。不得再造平行任务状态机或用量账本。
- 科学 `EvidenceReport` 与研究 `ResearchDecision` 分离；返回 TaskResult、step completed、Task Completed 与 `Scientific Claim Supported` 是四个不同事实，必须依次经任务完成资格、科学 validator 和研究决策判定。
- Workbench feature 继续禁止直接 `fetch`/XHR；通过 `frontend/js/client/` 的注入式 command adapter 接平台。
- 不删除兼容面、恢复 fixtures、模型资产、vendor、公开 shim；不新增 active workflow 而漏登 edit coverage；新 scheduler 预写文件必须核对 `_SCHEDULER_MARKERS`。
- 新增 `acp run` 运行时 import 必须同步 `requirements-node.txt` 与 `pyproject.toml`；版本变化同步四处版本号；配置默认值变化同步 Python built-in 与参考 YAML。
- 科学契约修改必须同步 `cccp/analysis/research/contracts/` 与 `docs/reference/next-change/acp2-contracts/science/`；RPH 保持 `cli → dsh-bundle → bridge → research`，bridge 禁 fetch/HTTP/spawn；插件 discovery 仅 metadata、默认禁用且不直接起子进程。
- 本计划不授权重启生产 `acp.service`、访问生产 run_root、调用付费模型或提交真实 QC/LSF。真实验收只在隔离环境经负责人批准后执行。

---

## 1. 先冻结的十项技术裁决

这些裁决用于防止各轨再次各自实现、各自测试、同时通过但无法互消费。

### D01 — 删除适配层跨调用结果缓存

- 删除 MCP submit/prepare 的进程内 `IdempotencyRegistry` 重放路径；不再缓存 operation reference 或退化的 submit envelope。
- 删除 Bridge 跨调用结果重放。不得保留当前 in-flight 占位 `result: null` 的路径；占位期间再次调用还可能造成并发双调用。
- MCP/Bridge 透传客户端原始幂等键；缺键时不擅自派生。RPH 在研究轮次中负责生成稳定键并先持久化，平台只把 key 当不透明字符串，按认证身份和动作使用既有持久机制。
- 各操作不共用一套伪“幂等权威”：submit 使用 `JobStore.claim_idempotency`；prepare 使用 Data operation 的内容身份、已认证 principal/project 和可选 client key；read 是天然只读幂等；analyze 若尚无持久 operation，则明确为可重复纯分析、不得宣称提交式幂等。
- 每次写/控制调用都触达 Controller；Controller replay 必须返回完整 TaskRef、attempt 和必要身份字段。
- 若未来因性能重新引入缓存，须另做 ADR，至少覆盖 operation、完整 target、content digest、单飞、容量、撤销和完整返回信封；不是本次关闭差距的默认方案。

### D02 — 身份先于缓存修复

先修 HTTP/MCP/CLI 的认证 principal 传递，再删除缓存并透传原始 key。否则会把“同键跨用户错误重放”变成另一种冲突。

- 唯一新增值对象确定为 `acp_services/workspace/command_identity.py::CommandIdentity`，principal/project/key_id/actions 均投影既有服务端认证结果，不另建 EntryScope 或第二 identity resolver；原始 API secret 不进入对象、事件或 BOM。
- `LOCAL_PRINCIPAL_ID` 只允许真正的 local-single operator 路径显式使用。
- 提交幂等与 admission 记账使用同一 principal；`permission_scope` 是传输/权限属性，不参与替代 principal。
- API key 撤销、角色/权限变化在写和控制调用前重新校验；read 由 Controller 重新授权，不能只依赖 MCP 启动时 identity。

### D03 — 一个应用命令契约，多种入口投影

HTTP、CLI、MCP 必须进入同一应用命令与准入语义。建立 exhaustive field policy：`forward / derived / reject`，不允许未知字段静默消失。

- `execution_mode`、`target_node`、`node_tags`、命名与备注字段按类型化规则保留。
- 服务器文件系统路径等不允许 Agent 选择的字段必须显式拒绝，而非接受后删除。
- preflight 与 submit 共用执行目标校验，避免“预检通过、提交不同”。
- `V1JobCreateRequest` 新增字段而未登记策略时，CI 必须失败。

### D04 — 协议版本、应用契约版本、研究扩展版本三根轴独立

- MCP `protocolVersion` 继续支持 `2025-06-18`；按标准返回服务端支持版本，由客户端决定是否接受，不要求私有 `protocolVersions` 列表。
- `application-api.contract_version` 当前为 `1.1.0`，不拿去充当研究扩展版本。
- `acp.research.v1` 的唯一版本权威是 `p2-research-operations.v1.json::negotiation.extension_version`，默认冻结为 `1.0.0`；`application-api.v1.json::contract_version=1.1.0` 是应用契约整体版本，其 `mcp_extensions` 只是必须同步修正为 1.0.0 的投影，不是第二权威。若不兼容升级，契约、服务端、RPH 和验收矩阵同步升级并保留负例。
- 未协商、版本不匹配、未实现或权限不足时，不得宣告研究扩展可用。基础 8 工具与研究 10 工具分别按真实能力发现。
- 建立真实 ADR-006 文件和 DSH 客户端矩阵。`@modelcontextprotocol/client@2.2.0` 是 M0-10 需要在外部 `$ACP2_TOOLS` DSH 树核验的环境事实，不是本仓已有依赖声明；本仓测试依赖由 M0-02 新增。研究扩展的字段位置与协商方式必须先经 M0-09 的真实 DSH/SDK 验证，基础 MCP 握手成功不能替代该结论。

### D05 — JSON 契约是跨轨权威，真实实现必须投影一致

继续使用 `docs/reference/next-change/acp2-contracts/`，不另建第四套同名合同。

- `application-api.v1.json`、`p2-research-operations.v1.json`、`control-and-data.v1.json` 与 science/experience schemas 明确 owner 和兼容策略。
- 新增生成型 `mcp-tools` 与 `entry-fields` 快照；快照来自冻结契约，服务端 tool specs/normalizer 与 RPH builder 都受其约束。
- CI 同时检查“真实 tools/list ↔ 契约”和“真实 Bridge arguments ↔ 服务端 normalizer/Controller”，不能只比较两边各自常量。
- 删除 `chr(97)+"cp.research.v1"` 混淆。wire id 集中到显式 `entrypoints/wire_identifiers.py` 常量模块，隔离扫描仅 allowlist 该模块中经审查的常量值，不按行号/函数位置豁免；按 M1-01 同步 ANTI #29、Services 台账和绕过负例，ANTI #32 不新增例外，import/反射禁令不变。
- 每个契约**文件内部**按 operation 记录 `implementation_status=planned|delivered|verified`；总 `manifest.contracts[]` 仍只记录文件级 status/owner/digest，不塞 operation 行。G 项和阶段交付状态只在 coverage 表达，不得把含 planned 行的整份契约标为已实现。

### D06 — 科学摘要、执行摘要、审批/提案摘要不可混用

至少明确区分：

1. `plan_definition_digest`：RPH/Workbench 科学计划语义；
2. 执行 `plan_digest`：平台冻结 workflow/input/method/resources 后的提交与授权身份；
3. `proposal_digest`：审批所绑提案的规范投影；
4. 幂等 `request_digest`：全量 effective submit request（含执行目标与命名、排除 key），用于重试冲突判断；
5. worker `frozen_plan_body_digest`：冻结 payload、steps、symbols 等执行体内容的完整性校验，不是提交审批摘要；
6. `recipe_definition_digest` 与 step 定义/模板身份：freeze 时可知的科学 Recipe 和依赖结构；
7. step 执行身份：定义身份 + 实际上游产物 digest，dispatch 时才能完整物化；
8. `contract_digest`、artifact digest、release digest：各自独立用途。

审批记录需要同时可核验科学计划摘要与执行计划摘要。Workbench/RPH 从 preflight/审批响应取得平台执行摘要，不把 C 侧科学摘要直接塞进 B16 的 submit `plan_digest`。前端两张摘要都要展示，任一变化按契约使审批失效。

M1 必须为上述每个域冻结规范输入、版本、校验者与兼容策略。不可变 envelope 保存定义/模板身份，逐 step 回执和工件链保存运行时执行身份，Controller 验证完整归属；不得在执行时改写冻结 envelope，也不得用占位上游摘要通过现有相等检查。旧 wire 字段暂不能改名时，内部类型与新契约仍须显式区分并保留版本化兼容映射。

平台 `acp_services/runtime/canonical_digest.py` 是提交授权/科学计划摘要的唯一实现；幂等 request digest 只复用既有 `runtime/idempotency.py::canonical_request_digest` 这一专用域实现，不新建第三套 canonicalizer，二者输入和用途不得互用。TS 只调用平台并做冻结向量比对，不维护第二 canonicalizer；删除 round 序号补零形成的伪摘要。

### D07 — EvidenceReport、证据归属、ResearchDecision 三层分离

- CCCP `EvidenceReport` 保留 claim 状态、validator、source_refs、limitations、missing checks 和 provenance。
- 平台发布经过摘要和身份校验的研究消费投影，绑定 project/research/round/job/attempt/release/plan/artifact digests；统一 TaskRef wire 形状。
- RPH 只消费经过验证的投影，并以独立、确定性的 `ResearchDecision` 决定 accept/revise/defer/stop。模型建议不能改写 CCCP claim 状态。
- 不用 TS 再复制一套科学 validator 作为权威。跨语言测试直接把 CCCP 真实报告送入权威 adapter，再交给真实 RPH consumer。
- 错项目、错轮次、旧 attempt、错 release、错 cursor、错摘要、无可验证来源均拒绝。部分证据只能产生限定决策，不能自动 accepted。

### D08 — 研究恢复以平台记录为权威

ResearchRecord 在提交前保存 submit intent、稳定幂等键、round、plan digests；提交后保存 TaskRef；等待保存 cursor；证据与决策按 revision CAS 追加。

- 不依赖单次 60 秒 wait；无终态返回可恢复 `waiting`，不是研究终态失败。
- cursor 失效时经 snapshot 恢复；重复事件按 event key 去重。
- 不把本地 WAL/JSON 当作第二 ResearchStore。允许本地缓存或崩溃日志，但关闭恢复差距必须证明新进程能从平台 ResearchRecord 和 Runtime events 续接。
- 提交结果未知时用相同 key 对账，再查询 status；不得盲目重新提交，不得默认 attempt=1 或猜 release。

### D09 — 预算能力矩阵先于预算扩维

先统一解析、拒绝未知形状、保证调用前预留和可信回执，再逐维提升 enforcement。默认完整发行口径：

| 维度 | 目标状态 | 权威载体 | 强制/记录点 |
|---|---|---|---|
| `task_ref` / `attempt` | `enforced` | `BudgetUnit` + Admission reservation | 现有持久 reservation，派发前 |
| 每 attempt `walltime_s` / queue deadline | 真实终止/过期证据完成后才为 `enforced`；此前仅 request-bound/NOT_VERIFIED | `ResourceBounds` / `ExecutableBound` / timeout policy + 持久绝对 deadline，不伪造成 BudgetUnit | admission 校验只是前置；Local 进程组终止、LSF `-W` 与 Controller 到期控制共同验证 |
| `recipe_step` | DAG 生产接线后提升为 `enforced` | reservation 派生的 `step_dag.BudgetCaps` + 持久对账 | 每次 step dispatch 前 |
| `qc_invocation` | 最坏次数预留与任务以下逐次取额度均落地后才为 `enforced` | CCCP `worst_case_executions` + TaskContext meter port + Admission reservation | 每次后端昂贵调用前；rescue/内部重试也计入 |
| `model_request` | 在真实模型产品发行前提升为 `enforced` | Admission reservation + 生产模型 gateway | 首次规划起的每次模型调用前 |
| 累计 `core_hours` | 默认 `recorded-only`，不宣称硬上限 | metering/usage 记录 | 可信 worker receipt 结算 |
| input/output tokens、`bsub` 计数 | 默认 `recorded-only` | 对应 usage/metering 类型 | 可信 proxy/worker receipt；token 硬强制保持 `NOT_VERIFIED` |

规则：非空预算不得静默解析成 `limits={}`；`core_hours`/`walltime_s` 不得产生未捕获异常；UI 和 release statement 都由同一能力表生成。除 `UNIT_CAPABILITIES` 外新增非 BudgetUnit 的 `execution_limits` 段，区分 admission-bound、process-termination、queue-deadline；现有 `local_admission` 的 `HARD_LIMIT_ENFORCED/quota_enforced=True` 必须先拆分这些语义，不能让未经终止验证的声明直接进入 UI/BOM。任何 recorded-only→enforced 翻转必须附对应关闭证据。`BudgetCaps` 必须从持久 reservation 派生，dispatch 前对账并回写可信 usage，不能只靠 executor 内存计数宣称 enforced。删除默认 `+len(steps)` 隐式额度，恢复 allowance 必须显式授权。由 A 在 CCCP 编译器**新增**每 step 的 `worst_case_executions`（包含 rescue/方向/内部重试）及 TaskContext meter Protocol，平台一次预留并按真实回执结算；这些均不是现成能力。硬上限还要求在每个后端调用前经该新增端口取额度，CCCP 不导入 ACP。RPH 的 token/wall/queue 字段可用于客户端自动限流，但不因此成为平台 enforced 声明。若产品要求累计 core-hours 或 token 硬上限，必须新增显式 `IN_SCOPE` 工作项并先实现调用前强制，不能借本表的 recorded-only 口径关闭相应承诺。

可信 usage source 由服务端认证身份和已绑定执行回执决定，普通 project write key 不能自报 `worker-receipt`。保留现有 TaskRef 防超限语义。

### D10 — 跨端门、证据索引和 BOM 从第一天建设

- 跨 Python/TS/真实 SDK 测试在 M0 建立，之后每个任务向其中添加反例；不能等第五步再补。
- `evidence_kind` 保持冻结枚举 `fixture|live|aggregate`，不新增平行标签。离线证明即使属于 live phase，也只能是 fixture 或 NOT_VERIFIED，不能写成 live 执行证据。
- 每个里程碑生成候选 BOM 和覆盖矩阵；最终签收只是冻结，不是第一次整理 SHA。
- 历史回执只读；复用必须有 changed paths、contract digest、toolchain、artifact digest 和理由，否则 `rerun_required`。

---

## 2. 组织、分支与完成定义

### 2.1 Owner

| 角色 | 职责 | 主要写入面 |
|---|---|---|
| INT | 端到端集成 owner、候选冻结、证据签收；协调但不直接改写 B-owned 契约 | 集成 checkout、CI/验收编排；契约 manifest 由 B 单写 |
| B / Platform | 身份、HTTP/MCP/CLI、审批、预算、研究 writer、worker/Data | `src/acp/**`、`src/acp_services/**`、runtime contracts |
| A / Science | 科学 descriptor、Recipe、step 身份、EvidenceReport、validator、表单 Schema | `src/cccp/**`、science contracts/fixtures |
| C-RPH | Bridge、compose、DSH、Supervisor、ResearchStore consumer | `rph/**`、experience research contracts |
| C-Web | Client commands、审批 UI、动态表单、浏览器证据 | `frontend/**`、浏览器验收 |
| REL | 制品库、RPH/静态资产打包、BOM、独立安装 | release scripts/manifests/artifact store |

同一文件仍只有一个写入 owner。跨轨问题以版本化契约和失败用例交接，不直接修改他轨实现。INT 不代替 A/B/C 签科学或模块结论，但必须拒绝不完整组合证据。

### 2.2 并发上限与 B 减载

B 是平台责任域而不是必须只有一名执行者。为免 28/34 任务形成事实单点，按以下执行角色分配；每个角色是独立 worktree/交付责任，可以一人兼任，但兼任时必须承认串行。

| 执行角色 | 任务范围 | 并发约束 |
|---|---|---|
| B1 Identity/Entry | M0-04～M0-09、M1-01、M3-01 平台侧 | M0-04→05→06 同分支串行；同一时间最多 1 个 XL |
| B2 Research/Approval/Budget | M1-02/M1-06、M2-01/M2-03、M3-03/M3-05 平台侧 | 与 B1 可并行；同一时间最多 1 个 XL |
| B3 Worker/DAG/Plugin | M1-03/M1-04 科学侧、M2-04/M2-05 | 不等 B2 全部完成才启动独立契约/adapter；同一时间最多 1 个 XL |
| B4 Contracts/Evidence/Release | M0-01b/M0-02/M0-03/M0-11、M5 | 契约 manifest 单写；不与 INT/REL 共享文件并写 |

A、C-RPH、C-Web、INT 各自同一时间最多一个 XL；每个任务/子检查点仍只有一名验收 owner。M0-10 的外部授权与 M0-02 的 CI 可与 B1 安全修复并行。若实际只有一名 B 执行者，则本计划关键路径就是单线程，必须按依赖顺序执行，不能在状态报告中宣称并行波次已完成。

### 2.3 工作区与提交纪律

1. 本计划及所需契约先形成可引用提交，记录完整 SHA。
2. 从该 SHA 建独立 A/B/C/集成 worktree；已有脏工作区和历史报告保留。
3. 每个任务先在当前基线运行新反例并保存失败证据，再实现使其通过；不得先把测试写成永远成功。
4. 每个 PR 包含：差距 ID、契约变化、生产调用点、负例、测试层级、兼容/迁移、回滚、证据路径。
5. 不在移动中的开发目录构建 release；候选要求 clean tree、持久 ref/tag、不可变制品 digest。
6. 真实服务返修后生成新候选，按影响矩阵重跑，不能把旧 PASS 直接复制到新 SHA。

### 2.4 分档 Definition of Done

**中间任务/子检查点**只要求三项：① 已实现的生产驻点或明确的契约/迁移产物，不能只定义不接线；② 对应负例与影响面同步（含架构/Services 守护）；③ 一份轻量回执，记录 candidate、命令、证据级别和 owner 复核。避免几十个子检查点都走发行级验收。

**关闭 G 项或形成 Candidate** 才走完整 DoD：
- 实现已接生产组合根，真实生产者和真实消费者跨端通过，fixture 只替代明确声明的昂贵边界；
- 对应审计反例成为常驻测试，旧 15 active workflows、兼容 import、Services 隔离和架构守护通过；
- 配置、schema、migration、Services 六列表台账、公开 shim/资产清单按影响同步；
- 回执注明 candidate/source tree、contract/config/toolchain digest、实际命令、测试级别和限制；
- 非实现者复核。未执行的真实模型/科学/浏览器/安装行仍为 `NOT_VERIFIED`。

### 2.5 最小可观测性与对抗性输入

- 结构化日志字段至少包括 command_id、principal_id/project/research/round（无 secret）、job/attempt、idempotency scope hash、digest 版本、reservation/receipt id、fencing 状态、recovery action 和错误码；不得记录 API secret、模型密钥或未经授权的科学内容。
- 指标至少覆盖 submit replay/conflict、credential denial、budget denial、QC grant/rescue、stale evidence、DAG recovery、deadline termination、fencing loss 和 late usage。M1-06 对无缓存后的 Controller 调用记录 p50/p95 基线；本计划不虚构吞吐 SLA，但 Candidate 必须报告相对回归。
- 增加恶意工件/插件文本负例：内容诱导模型省略审批、改变 plan/budget、伪造 worker receipt 或把其他 TaskRef 证据当作本轮结果。平台只能把其当不可信数据，必须零新增授权/任务/可信回执，并保留拒绝日志。M4-01 做确定性组合，M4-02 用真实模型复测一类。

### 2.6 容量估算与 Team/Solo 形态

以下是**可被 M1-06 重估挑战的初始容量模型，不是承诺工期**。按 39 任务、9 XL、23 L、6 M、1 S，估计总投入 **110–190 人日**：AI 辅助实现/测试 45–80，审查/跨端返工/迁移与并发验证 45–70，真实环境与独立验收 20–40。Candidate-Contracts 约 20–40 人日。M0-09 已升为 L，M0-13 的条件成本计入该范围。

- **Team（4–6 人、至少 3 条车道）**：环境全部就绪时，Candidate-Full 以 3–5 个月为规划区间；LSF/模型/第二操作者的等待可能主导日历。
- **Solo（1–2 人）**：全量以 6–12 个月为规划区间，且必须优先 Candidate-Contracts + Local ORCA 子集；Candidate-Full 默认 DEFERRED，不能按 Team 的并行波次汇报。
- M1-06 必须用实际完成量、返工率、真实测试耗时和外部等待重估上述范围；超出批准容量时按 go/no-go 缩窄或停止。AI 可压缩编码，不压缩 LSF、真实科学、迁移风险审查、授权和独立操作者时间。

---

## 3. 总体波次与关键路径

```text
M0 基线/跨端门/现有入口安全
  ├─ 标准握手、真实身份、原始幂等键、字段等价、可信回执
  ├─ 真实 DSH 扩展协商接线验证
  └─ CI、证据标签、BOM 骨架
        ↓
M1 共享契约冻结
  ├─ MCP/artifact/research operations
  ├─ ResearchRecord/recovery
  ├─ Evidence/TaskRef/ResearchDecision
  ├─ 多摘要/定义与执行身份 + SubmitPolicy + Budget matrix
  └─ ADR-003/008 + DA 先验科学判据
        ↓
M2 平台生产链                         可并行：可移植 profile/package 骨架
  ├─ Research writer + P2 tools
  ├─ Artifact prepare/read + vibration.analyze
  ├─ Approval/Budget enforcement
  ├─ Recipe DAG compile/freeze/dispatch/recover
  └─ 第三方纯分析插件生产装配（P3 并行线）
        ↓
M3 客户端产品装配
  ├─ 真实 Bridge/TaskRef
  ├─ RPH compose + product fail-fast
  ├─ 类型化 Planner + 可恢复 Supervisor + 证据校验
  ├─ Workbench 真实审批
  └─ P3 Schema form（可并行，不阻塞 P2 纵切）
        ↓
M4 真实验收
  ├─ 无 QC 的全链与故障矩阵
  ├─ 真 DSH/模型 + Local + Chromium
  ├─ LSF/远程约束/控制/恢复
  ├─ 限定 DA + 原生科学证据
  ├─ 第三方 capability 插件纵切
  └─ 跨领域/多项目/迁移恢复 required 行
        ↓
M5 发行与独立移交
  ├─ RPH/静态资产/Python/root 全组件制品
  ├─ 单一 BOM + G01–G26/H0–H7 覆盖矩阵
  └─ 独立操作者干净安装与最终签收
```

**关键路径**：`M0 身份/幂等 → M1 契约 → M2 writer/artifact/DAG → M3 RPH 证据与恢复 → M4 真实科学与可靠性 → M5 BOM/独立安装`。依赖图必须分两类语义：默认 **start 图为 16 个任务/3 个 XL**（`M0-01→04→05→06→11→12→M1-01→M1-06→M2-01→M2-03→M3-03→M3-04→M4-01→M5-01→M5-02→M5-03`），默认 **close 图为 19 个任务/6 个 XL**（在 M4-01 后经 M4-02→M4-03→M4-06 再回 M5）；external_change_required 的 close 图为 **20 个任务/6 个 XL**。start 图用于并行开工，close 图用于整项签收，二者都不能单独当日历工期。正式数字以 M0-01 生成器输出的 [plan-graph.json](plan-graph.json) 为准，现有文件仅为评估估算。
阶段图表示验收扇入而非所有任务的开工屏障：M2-02 在 M1-01 后即可启动，M2-05 与 P3 表单、profile/打包管线可提前开发，但不能用中间成果提前关闭 M2/M4。

| 候选 | 冻结点 | 能声明什么 | 不能声明什么 |
|---|---|---|---|
| Candidate-Contracts | M1-06 | 空项目的真实跨端契约/持久链，stub 边界清晰 | real science/model/browser、P2 完整交付 |
| Candidate-P2 | M4-02、M4-03、M4-04 按 ADR 必需行完成 | 审批/预算/恢复/证据与限定 DA 的 P2 科学闭环 | P3 插件、H6、完整独立发行 |
| Candidate-Full | M4-05/M4-06 + M5-03 | 所有 IN_SCOPE 能力、H6 与独立安装的完整 ACP2 | DEFERRED/NOT_VERIFIED 项或 worker-hosted COMPUTE 等未声明能力 |

---

## 4. M0 — 基线、跨端门与现有入口安全

**目标**：先消除现有入口会制造错误成功、错误身份或错误执行意图的问题，并建立后续不会再漏掉跨层断裂的门。  
**阶段门**：R3 的 G01、G21–G26 反例全部转为正确行为；原始 key 经 MCP/HTTP 等价且旧派生键有兼容对账；普通写 key 不能伪造 usage；跨端 CI、逐场景证据判据和 M0-09 的真实扩展协商路径可用。

### M0-01 冻结整改基线与 26 项机器可追踪台账

- **Owner**：INT（M0-01a 基线/coverage）+ B（M0-01b 契约注册）；**规模**：S；**前置**：无。两个检查点分别有独立退出和 coverage 行，B 的注册不是 INT 的隐式工作。
- M0-01a 由 INT 冻结基线、任务图和 coverage；M0-01b 由 B 注册 scope/coverage/BOM schema 与 manifest 并同步 validator/tests。任务整体只有在两个交接都完成后关闭。
- 新建唯一 `docs/reference/next-change/acp2-contracts/release/scope.json`：逐能力列 `IN_SCOPE/DEFERRED/OUT_OF_SCOPE`、理由、生效条件和对应 G/H 行；worker-hosted 第三方 COMPUTE 等延期项必须在此显式列出。Candidate-Full 只能由该表的 IN_SCOPE required rows 计算，G 项关闭不得自动等号 Full 放行。
- 新建 `scripts/acceptance/build_acp2_plan_graph.py`，从任务/子检查点与前置字段分别生成 **start** 与 **close** 两张图及各自 critical_path/XL_count/unknown_refs/cycles；M4-06a、M5-01 的提前开工边与整项关闭边必须分开。校验所有被引用子检查点已定义。当前目录已有 `plan-graph.json` 只标 `ESTIMATE_ONLY_NOT_M0_01_DELIVERY`，不得视为 M0-01 已交付；正式生成器通过后才允许覆盖并提交。
- 将主方案 H0–H7 执行索引与 handoff-H7 的版本/digest 纳入 coverage，并落实 §10 的 G→H 映射；最终放行不再依赖文件外隐含判据。
- 先执行 `git fetch origin` 对账：本地 `0d739b2` 与远端 `feat/frontend-plan1-closure=d0fcf70` 已分叉，远端 tip 本地不存在。禁止直接复用现有 A/B/C/integration worktrees 或把两套历史混合；先形成包含所需源码/KB/计划的新集成提交，经审查与授权后推送并打持久 tag，再以该 ref 建全新 worktree。M5 不接受仅本地 HEAD 作为持久候选。
- 逐项登记脏文件：对 `tests/baseline/refactor-evidence/e2e-task-root.txt` 的个人 pytest 临时路径修改，经文件 owner 确认后从真实基线恢复或明确保留理由；对 12 份 AGENTS/KB 修改和新增逐项决定纳入新基线或排除，禁止 reset/discard 覆盖用户工作。计划与 KB 必须来自同一集成提交。
- 记录报告基线、当前 HEAD、tree、工作树状态和源码等价 diff；从新的干净提交建集成分支。后续审查与施工引用任务 ID + 标题 + 计划 SHA，不引用易失效的行号。
- 新建 `docs/reference/next-change/acp2-contracts/release/evidence-coverage.schema.json`（`acp2.release.evidence-coverage.v1`）及 `scripts/acceptance/validate_acp2_gap_coverage.py`，从 M0 起登记 G01–G26、全部任务与每个 a/b/c/d 子检查点的 ID、owner 和 required evidence level；被引用未定义 ID 直接 FAIL。另加 contracts 目录 glob ↔ manifest 双向检查：未注册文件和 manifest 悬挂项均失败，防止 scope.json 自身漏登记。契约文件由 B 以 `status=planned` 注册进总 manifest（INT/REL 不是合法 contract owner），并按现有 validator 规则更新 manifest `contract_version`/digest；同步修改 `tests/test_acp2_runtime_contracts.py` 对 `1.3.0` 和 delivered_by 阶段的旧硬编码，改为本次明确评审的新版本/阶段，并保留独立预期向量，不能仅让测试从被测 manifest 自算期望值。coverage 文件自身以 canonical bytes SHA-256 标识。现有 `docs/reference/next-change/acp2-contracts/runtime/dependencies.json` 是受 `acp2.dependencies.v1` 严格校验的旧计划 DAG，保持原用途，不把 G 项硬塞进去。
- 保存原始探针输出副本；后续永久测试写“期望正确行为”，不要把原探针的缺陷断言当成验收目标。将独立审查 R01–R12 映射到本文件既有 M 任务和所属 G 项，coverage 记录其关闭证据，但不另起 G27+ 或第二套缺陷台账。
- 建立新证据根，禁止覆盖本次审计证据；历史用户修改保持不动。
- **退出**：每个 G 项至少映射一个任务和一个关闭门；无 owner、无依赖、无证据级的行由 validator 拒绝。

### M0-02 建立真实消费者 CI 门并修复 CI 自身断点

- **Owner**：INT + B + C-RPH；**规模**：L；**前置**：M0-01。
- **独立检查点**：M0-02a CI/package/frontend/contract-job 修复；M0-02b 契约导出与真实消费者反例；M0-02c Ruff ratchet。每点分别进 coverage，不允许用 CI 骨架通过替代真实交界门。
- 新增 `scripts/export_acp2_entry_contract.py`（拟新增）与契约快照检查；在 `rph` devDependencies **新增并精确锁定** `@modelcontextprotocol/client@2.2.0`。
- 新增永久跨端套件（名称可按下述实现）：
  - `tests/test_acp2_contract_export.py`、`tests/test_acp2_r3_entry_reproducers.py`；
  - `tests/test_acp2_entry_equivalence.py`、`tests/test_acp2_frontend_contract_gate.py`；
  - `tests/test_acp2_step_dispatch_adapter.py`、`tests/test_acp2_execution_identity.py`、`tests/test_acp2_budget_execution.py`、`tests/test_acp2_legacy_identity_migration.py`；
  - `rph/test/r3-bridge-repro.test.ts`、`mcp-wire.test.ts`、`p1-envelope.test.ts`、`research-identity.test.ts`。
  R3 的 `probe_bridge.mjs/probe_mcp.py/probe_http.py` 和独立审查的 `probe_plan_assumptions.py` 必须转成上述常驻、取反断言，原审计脚本保持不动。凡需要 Node/RPH dist 的 Python 跨端文件统一标 `@pytest.mark.contract`，普通 Python 矩阵排除，专用 job 必须执行。
- 真实 SDK 默认 initialize/tools/list、真实 Bridge builder→真实 normalizer/ASGI Controller、真实 HTTP response→真实 frontend validator 随各自修复 PR 进入 required 门；不需要模型或 QC。harness 可先以非阻断方式合入，但对应 G 项保持 OPEN，最后一个修复 PR 必须打开 required，不能在红门上等待后续阶段。M0-required 为 SDK/R3/entry-equivalence，frontend contract gate 随 M1-01 启用。
- `.github/workflows/ci.yml`：RPH upload 前增加 `pnpm package`；将 `run_all.js` 作为独立 job 或显式 mandatory step（保留 pytest 聚合）；新增 Python 3.11 + Node 22 的 `mcp-conformance` job，依次 checkout、setup-python、setup-node/pnpm、`pip install -e '.[dev,api,remote]' nmrglue`、`pnpm install --frozen-lockfile && pnpm build`，再以 `pytest -m contract` 全量执行跨端套件；空测试集、Python 导入失败、RPH dist 缺失或制品缺失必须失败。在 `pyproject.toml` 注册 `contract/nightly/pre_release` 等实际使用的 pytest markers，并启用 `--strict-markers`。记录普通矩阵与 contract job 的 wall-clock，单 job 超过 20 分钟必须分片而不是 skip；不得用超时隐藏慢测。
- 契约导出器只负责生成服务端/客户端共同消费的契约快照；现有 `acp2_dsh_tool_snapshot.mjs` 负责真实 DSH 观察，`validate_acp2_contracts.py` 负责 schema/交接校验，三者不得演变成第三套快照权威。SDK 用例锁定精确版本并断言默认 initialize 参数形状；DSH 外部 lock digest 在 BOM 记录。
- **独立 Ruff hygiene 流（不作为 G 关闭门）**：将 dev/pre-commit 的 Ruff 版本统一到经复核的 `0.15.22`，冻结 `tests/baseline/ruff-baseline-0.15.22.json` 的 `(file, code, location)` 基线；新增 `scripts/check_ruff_ratchet.py`，新文件和修改范围零新增诊断，`acp_services/**` 保持全量零违规，format 只检查本 PR 触碰文件且空集合安全。基线只能缩小，不在安全修复 PR 中机械重排无关文件；架构/Services 守护仍全量执行。M0-02a/b 可独立关闭，M0-02c 不阻塞 M0–M5。
- **基线来源唯一**：基线必须在集成分支的基线提交上由固定 `0.15.22` 重新生成，回执记录命令、ruff 版本、配置 digest 与计数。已有的历史计数（`ci.yml` 头注 ~678 errors/63 文件、独立审查 `ruff-check.json` 1,065 diagnostics/130 文件、182 文件待格式化）只作趋势参考，互不可比，任何一方都不得直接充当基线或关闭门；跨版本计数差异不得被解释为本计划引入的违规。
- **退出**：新增 `scripts/acceptance/check_acp2_contract_mutations.py`，在临时副本中逐项执行“删除测试、移除工具、改变 required 字段、只改客户端/服务端版本”等 mutation，并断言对应 CI 选择器失败；原有“客户端 18/服务端 8 同时绿”不能再次发生。mutation 结果机械写入 coverage，不靠签字保证。

### M0-03 修正证据标签、live harness 骨架与候选索引骨架

- **Owner**：B（`acp2_runtime.py`）+ C-RPH（research harness）+ C-Web（browser harness）+ INT/REL（证据/BOM）；**规模**：M；**前置**：M0-01。各 owner 只写自己的脚本和契约，INT/REL 不直接修改 B/C harness。M0-03a（B runtime evidence）、M0-03b（C experience/live）、M0-03c（INT/REL BOM/coverage）分别退出和登记；B 的 manifest 注册仍是显式交接。
- 修改 `scripts/acceptance/acp2_experience.py`、`acp2_runtime.py`：不得仅由 phase 推导 live；读取场景实际 transport/model/controller/artifact 观察。
- validator 改为**逐场景 required-observations**：外层 live + 内层 `offline_fixture` 仍拒绝；每个 live 场景声明 transport/controller/model/qc/lsf/browser/artifact/denial 等观察及 `present|absent` 方向，未声明即契约错误而非默认放行。`model=present` 要求真实调用与 gateway/usage 回执；`model=absent` 要求恰好零调用及真实拒绝/未触发事实。预算拒绝行还必须无 TaskRef。保留 `fixture|live|aggregate` 与 PASS/FAIL/NOT_VERIFIED 两条独立轴，零模型调用本身不再被误判为非 live。
- 修改 `scripts/acceptance/acp2_experience_scenario_research_loop_live.py`：`research-loop-live` 不得再硬编码 `model_delivered=False/controller_delivered=False` 作为最终实现；先接入可调用端口，缺实现记 `NOT_VERIFIED(implementation_missing)`，缺环境记相应 NOT_VERIFIED reason，不新增第四种 evidence kind。
- `approval_budget_live` 保留 feature 禁止直接 fetch 的合理检查，但删除“无平台传输即 PASS”的条件，改查注入 adapter 与平台事实。
- 新建 `docs/reference/next-change/acp2-contracts/release/bom.schema.json` 骨架和只读 inventory，由 B 以 `status=planned` 注册进总 manifest，并按 validator 规则同步 `contract_version`/digest；复用 M0-01 的 coverage schema，历史 summary 不改写。
- **退出**：离线 fixture 无法进入 live 行；required 场景未执行时 aggregate 不得 PASS。

### M0-04 统一 CommandIdentity，修复跨用户幂等与 admission 归属（G24）

- **Owner**：B；**规模**：M；**前置**：M0-01。
- 在 `acp_services/workspace/command_identity.py` 新增 frozen `CommandIdentity`；HTTP 从 `VerifiedRequestIdentity`、MCP 从身份 binding、CLI 从显式 local operator 派生。`IdempotencyContext` 采用 additive `principal_id/key_id` 字段，由这三处装配，不再保留第二套装配方案。
- 修改 `v1_routes.py::create_job` 的 `resolve_scope_id(LOCAL_PRINCIPAL_ID, ...)`；`IdempotencyContext`、唯一的 `_ControllerReservationPort`、`LocalAdmission.request_for_spec/admit_job` 两个入口同步透传 principal/key；不得把两个入口误写成两个端口实现。
- 核查 CLI `job_submit_command/_task_manager` 的 admission 装配，消除 CLI 与 HTTP/MCP 的准入差异；local-single 保持兼容。
- 不需要新建幂等表；复用 `JobStore.claim_idempotency/finalize_idempotent_job`。保留其跨 scope 同 action/key 的防抢占冲突守卫：本批不为了“允许不同用户同 key”而删除安全限制。
- 统一幂等 `request_digest = runtime/idempotency.py::canonical_request_digest(全量 effective submit request)`，包含 execution target、命名及其他有效字段并排除 idempotency_key；这是该模块已有的专用幂等域实现，不在 `canonical_digest.py` 另造同名函数，也不是审批用 `_plan_digest`（仅 workflow/input/method/resources）。两者不得互用，HTTP/MCP/CLI 的合法重试不能因各自拼 JSON 或丢字段而得到不同结果。
- **R09 同任务迁移**：`idempotency_records` additive 增加 identity/digest version、scope_origin、migration_state。旧 pending/committed/unknown 分别定义对账；只有历史 principal 与请求归属可验证时才允许 replay/映射，归属不明保持 typed conflict 和受控对账，不能自动认领给当前调用者或提示换 key 盲目重提。保留旧 TaskRef 与原始请求事实的可追溯性。
- **测试**：同项目 Alice/Bob 同 payload/key 时 Bob 得到显式 `idempotency_conflict`，绝不拿到 Alice 的 job，也不产生第二任务；同用户重试返回同一完整 TaskRef；同用户跨项目按既有冲突/隔离契约处理；admission principal 不合并；local-single 旧用例不变。另用旧代码生成的真实行覆盖 committed/pending/unknown、跨用户同 key 和旧 digest 版本。若产品决定允许跨 principal 同 key，须另做契约变更和防抢占评审，不在本任务暗中放宽。
- **退出**：R3 `two_principals_same_project` 反例翻转，HTTP/CLI 的真实 principal scope、冲突与 request digest 规则一致；MCP↔HTTP 的全量 digest 等价在 M0-06 字段补齐后关闭，不作为本任务的提前退出条件。

### M0-05 删除 MCP/Bridge 第二幂等权威并加入撤权重验（G21/G22/G25）

- **Owner**：B + C-RPH；**规模**：L；**前置**：M0-04。
- 修改 `entrypoints/mcp.py::IdempotencyRegistry/_tool_submit/_tool_prepare_artifact/_resolve_idempotency` 和 `services_mcp_bootstrap.py::_ControllerSubmit`：删除缓存重放、权限 scope 折键和自动派生键，透传 raw key 与完整响应。M1-01 的必填约束生效前，工具描述必须明示“缺键=非幂等，可能创建新任务”以及“raw key 在同一 action 内全局唯一，须使用高熵/命名空间键”；RPH 从一开始就必须提供稳定 key。
- **旧 MCP 派生键兼容**：`mcp.py::derive_idempotency_key` 产生的是 `auto_` 前缀，`services_mcp_bootstrap.py` 的 `op_` 仅是 operation reference，二者不得混为迁移命名空间。lookup 先查当前身份/raw key，无命中时才按版本化规则重算旧 `auto_` 派生幂等键与旧 scope；仅历史归属可验证且已 committed 的行允许 replay 并标记迁移。pending/unknown 先对账，digest 不符返回 `legacy_digest` 冲突，绝不能让老客户端原 key 重试因找不到旧行而创建第二个任务。迁移 fixture 必须由旧代码真实生成 `auto_` 行和 `op_` 引用，不能手写一个永不会命中的键。
- 修改 `rph/packages/bridge/src/index.ts`：删除跨调用结果缓存；不允许 in-flight 空结果导致第二次调用。研究重试依靠服务端幂等。
- prepare 同步修改 `data/operations.py::idempotency_key_for`，由 B 对 Data DB 做版本化迁移：增加 identity_version 与新 `(principal, project, client_key, epoch)` 索引，旧索引/旧 operation 以 v1 谓词保留，新行为写 v2；只有内容与授权归属可验证时才复用旧 prepare，不能把旧行自动认给当前用户。保留内容 digest 冲突检查；read 不缓存成功，analyze 不虚构持久 replay。
- 为写/控制工具增加每调用 credential revalidation；撤销后缓存与非缓存路径语义一致（本方案无结果缓存）。
- **测试**：prepare(K)→submit(K) 真提交且返回 job reference；submit 重试两次均有相同 TaskRef/attempt；validate→submit 两次真实调用；evidence 换 task/attempt/resource 不重放；并发同 key 仅一次平台任务；撤销 key 后拒绝且没有副作用；旧 `auto_` 派生幂等键 + 原 raw key 重试仍只有原任务；旧 prepare v1/v2 各按归属复用或显式冲突。
- **退出**：G21/G22/G25 的 R3 探针全部翻转，Controller 后端调用计数与返回信封同时正确。

### M0-06 修复执行字段与跨入口 JobSpec 等价（G23）

- **Owner**：B；**规模**：M；**前置**：M0-02、M0-05；与 M0-05 顺序合入同模块。
- 在 `application_params.py` 建立 exhaustive field policy；`services_mcp_bootstrap.py::_ControllerSubmit.submit` 不再重建删减版请求。**兼容决策冻结为 v1 直接 extra=forbid**：HTTP 返回 422 `input_invalid`，params 含 `rule=unknown_field/field`；MCP 返回同名 `input_invalid`，CLI 输出结构化错误。先盘点 frontend、CLI、RPH、`scripts/acceptance/**` 与已登记外部 SDK 的额外字段，保留明确的 legacy allowlist，发布说明列出行为变化；没有“默认忽略+warn”的隐式过渡。request digest 按 DTO `schema_version` 与 digest 版本持久化；旧行按其冻结字段表验证，无法重算或归属不明时进入对账，不把版本差异当篡改，也不盲目重提。
- preflight/submit 共用 execution target 校验；不支持的服务器路径字段显式拒绝。
- **测试**：不存在的 remote node 在 HTTP/MCP/CLI 均产生同一应用错误码与 params（不要求 stdio 也返回 HTTP 400）；合法 target/node_tags、命名/备注及其他允许字段在三入口形成相同有效 JobSpec 和全量 request digest；同 key 改合法 target 必须 conflict 而非 replay；`execution_mod` 等拼写错误在 HTTP 返回 422 `input_invalid`、MCP 返回同名 `input_invalid`；新增 DTO 字段未登记策略时测试失败。
- **退出**：跨入口有效 JobSpec、全量幂等 request digest、科学/执行身份和拒绝结果一致，无静默降级到本地。

### M0-07 可信 usage receipt 与结算绑定（G26）

- **Owner**：B；**规模**：L；**前置**：M0-04。
- 修改 `v2_budgets.py`：普通项目 write 不再可写可信用量；`trusted_source` 字符串不能作为授权事实。
- 以现有生产入口 `runtime/admission.py::record_usage_once` 为结算接线点，为其补齐 producer capability、reservation/执行身份与回执验证；`metering_decoupling.UsageIngestChannel` 仅可复用类型/解耦语义，不得把“接通零调用模块”当成 G26 修复。复用 `runtime/receipts.py` 的验证原语和 admission DB，不新建账本。
- **区分两种回执上下文**：状态推进继续使用 current-attempt guard；历史 usage 用原 attempt 的不可变 envelope、reservation、producer 和 receipt chain 验证，只入账、绝不推进当前任务状态。不能因 attempt 2 启动而丢弃 attempt 1 的真实迟到用量。
- B 同时认领 identity DB 与 admission DB migration：在 API key/service credential 上增加受服务端控制的专用 capability 元数据（不改变现有 `AccessAction` 过滤语义），service/worker/proxy 才能写相应 usage；`controller-terminal-receipt` 只作内部来源，普通项目 write 不能选择它。
- reservation/receipt/usage 行在**入账时**持久保存 attempt/submission/release/plan_identity/step identity 或执行链摘要与 receipt_id；之后历史核验不依赖 work_dir。若不能支持该持久化，则必须禁止 purge 有未结算用量的任务。相关记录按既有状态机在同一事务/投递边界更新并幂等。
- 更新 `acp2_runtime.py` 中使用普通 write key + 自报 worker 的旧验收步骤。
- **测试**：member 伪造 receipt 403；错 reservation/attempt/release 拒绝；重复 receipt 幂等；虚构用量不入账；attempt 2 已启动后 attempt 1 回执只计一次且当前状态不变；purge 后历史 usage 仍可核验；失败科学/失败发布仍正确结算；TaskRef 防超限反证继续通过。
- **退出**：无可信执行事实不能结算或修改 reservation 状态；历史 attempt 用量可晚到、可核验、只入账且不推进当前状态。

### M0-08 标准 MCP 握手与 ADR-006（G01）

- **Owner**：B；**规模**：M；**前置**：M0-02。
- 修改 `_negotiate_protocol_version` 和 initialize：不要求 `protocolVersions`；不支持请求版本时返回服务端支持版本，由客户端决定兼容性。
- 保留 init 前工具拒绝、stdout/stderr 分流、断线和无启动副作用测试。同步更新 `scripts/acceptance/acp2_mcp_negotiation_probe.py`、`tests/test_acp2_runtime_mcp_process.py:574-642` 的自定义协商矩阵、`docs/reference/next-change/acp2-contracts/experience/mcp-handshake.md` Finding F1 与同目录 `requests/B-req-18.md` R1/R2：逐项关闭旧协议/schema pin，保留拒绝原因和真实客户端证据，不能只改 harness 而遗留 OPEN 记录。
- auto 模式在 stdio 上可能先用 `server/discover` 并启动 sibling 探测进程：服务端对未知方法返回普通 JSON-RPC error 后仍须可用，探测不得产生任务、锁或端口副作用；当前现网不支持版本使用 `-32602`；`-32022` 仅是后续协议语义的前瞻禁令，不得用它表达 2025-era 版本不支持，实施者不得误以为现网已存在该错误码。
- 新建 ADR-006 实体文档，更新 client matrix，明确“SDK 握手通过不等于完整 DSH Agent 通过”。
- **测试**：未修改的 client@2.2.0 默认 `versionNegotiation:auto` initialize 成功并列出基础工具；旧版/未知版客户端行为符合规范；真正不兼容客户端自行失败且不产生任务；discover 探测与正式 initialize 两个进程都无副作用。
- **退出**：R3 `dsh.json` 的 `-32602` 反例消失；runtime process 测试、negotiation probe、mcp-handshake Finding F1、B-req-18 R1/R2 均有新证据并明确关闭，旧自定义协商 pin 被替换而非放宽其他生命周期断言。

### M0-09 真实 DSH 研究扩展协商接线验证（G02/G04，R12）

- **Owner**：C-RPH + B；**规模**：L；**前置**：M0-02、M0-08。该任务决定研究产品入口，M0-13 的条件成本必须一并纳入容量估算。
- 本任务必须在 M1-01 冻结前完成，无模型/QC；接线 spike 时间盒为 2 个工作日，超时必须形成 external_change_required 或 scope-change 决策，不能无限验证。
- 锁定实际 `@deepseek-ai/dsh-mcp-client` → `@modelcontextprotocol/client@2.2.0` 和 transport，记录到 ADR-006 client matrix。当前 DSH `capabilities:{}` 且没有配置项，不得假定它会发送 ACP 私有扩展。
- 用真实 SDK 验证扩展 wire 位置：Client/ServerCapabilities 支持 `extensions`，但 initialize result 顶层自定义 `extensions` 会被客户端丢弃。当前 DSH 硬编码 `capabilities:{}`，官方能力配置分支默认不可用；优先验证应用级握手，其次才是上游配置变更，明确谁判定 negotiated、如何传 project/identity、权限/版本失败如何表达，不得默认走官方路径或 fork DSH。
- 若需修改外部 DSH client，先形成独立依赖变更决策并写入 BOM；若官方路径不足，采用应用级握手，MCP initialize 不宣称扩展已协商。
- **测试**：扩展缺失、版本不匹配、无 P2 权限均不可见或 typed refusal，且零 task/submission；真实 SDK 能读取所选字段/握手结果，而非手写 JSON-RPC 自证。
- **退出与失败分支**：正常结果为 `path_selected`，写入 ADR-006/coverage 并作为 M1-01 输入。若三条路径均不可行，必须进入 `external_change_required`（执行条件任务 M0-13 和发行前置）或 §0.2 的 `scope_change` 决策；未选择前 M0 不得通过。选择 scope_change 时，M1-01 只冻结基础 8 工具，研究扩展标 DEFERRED，M3 Agent 研究线不启动；完整 ACP2 仍保持 NOT_VERIFIED，不能用该降级路径宣称完整放行。

### M0-10 环境、账号与授权就绪（G06/G17/G18）

- **Owner**：INT；B（QC/LSF/Controller）+ C-RPH（模型/DSH）+ C-Web（浏览器）+ REL（干净环境/制品库）分项交付；**规模**：L；**前置**：M0-01。
- 形成付费模型书面授权、调用次数/费用上限和计量口径；模型 secret 仅由生产 gateway 持有。记录 Local QC 二进制/版本、原生 run_root、测试结构与资源上限，不执行未经授权的 QC。
- 记录隔离 LSF 账号、SSH/queue/node_tags、配额、测试项目、清理责任人和允许的 submission 范围；不使用生产队列做故障注入。准备 Node/DSH lock、Chromium/Playwright、artifact store 和第二操作者/干净机器或明确隔离环境，并记录外部 DSH 树实际解析的 `@deepseek-ai/dsh-mcp-client` 与 `@modelcontextprotocol/client` 版本/lock digest。
- 以当前实测预填 readiness：Python 3.11.13、ORCA `/usr/bin/orca`、Node22/pnpm（位于 `/ACPServer/acp2-tools` 但不在默认 PATH）、Chromium cache 为可用候选项；DSH 0.2.1-rc.2 可运行；xTB 6.7.1=`/home/xieningke/xtb-dist/bin/xtb`、CREST 3.0.2=`/home/xieningke/crest/crest`；Openlava 2.0=`/opt/openlava-2.0`，设置 `LSF_ENVDIR=/opt/openlava-2.0/etc` 后 `bsub -V` 正常；最小作业 `1767` 已在 `normal/workstation` 完成（DONE），bsub→bjobs→bhist→原生输出链已实测。默认 node=20.19、`ACP2_TOOLS` 与 `LSF_ENVDIR` 未设置，isostat/molclus 仍未登记；`deepseek-official/deepseek-flash` 已完成一次最小真实调用并返回 `OK`（secret 不记录，且需轮换对话中暴露的凭据）。干净 ARM64 机器已可 SSH 访问，需在用户目录安装受支持 Python/Node；独立操作者分工另行确认。先把可用项与缺项分开，不能全部写 TODO。
- 输出脱敏 `environment-readiness.json`：每项 `PASS/FAIL/NOT_VERIFIED`、批准人、有效期、证据 digest、secret reference（不写 secret）和适用范围。默认 PATH 不含上述二进制不等于未安装；profile 必须显式加入 xtb/crest/Openlava 路径并设置 `ACP2_TOOLS`、`LSF_ENVDIR`，真实 QC 测试另按 tests/conftest 导出 `CONFSEARCH_XTB_PATH`、`CONFSEARCH_CREST_PATH`、`CONFSEARCH_ORCA_PATH` 等绝对路径。当前不再以“缺 LSF/xtb”判定 Full 不可达，但仍须验证隔离队列提交权限、模型授权、第二操作者与干净环境；M1-06 go/no-go 前完成这些裁决，不能只到 M4 才发现配置缺失。
- **退出**：M4/M5 所需环境均有明确可用/不可用决策和授权证据；M0 本身只要求决策完成，M4-02/M4-03/M4-06/M5-03 必须把实际就绪作为硬前置。

### M0-11 身份、字段与迁移决策冻结（G02/G03/G12/G13/G20）

- **Owner**：INT 汇总，B/A/C-RPH/C-Web 各 owner 签核；**规模**：M；**前置**：M0-04、M0-05、M0-06。
- 冻结 `CommandIdentity` 唯一落点与 IdempotencyContext 装配、v1 extra=forbid/422 unknown_field/legacy allowlist、P2 `implementation_status=planned|delivered|verified`。
- 冻结 planning claim 命名空间 `plan|<project>|<research>|<session>|<round>|<call_seq>`、model receipt 命名空间、RPH key 中 project/research/round/plan_digest 的编码与变更规则、request digest 版本迁移、历史 `auto_` 派生键与 `op_` operation 引用对账。
- 新增 `scripts/acceptance/check_v1_request_field_consumers.py`，机械扫描 frontend/RPH/CLI/acceptance fixtures/docs JSON 的请求键与 V1 schema 差异，形成受影响客户端清单；不能只靠人工列举。legacy allowlist 必须有逐字段退出条件和移除版本，不得永久维护两套字段策略；v1 硬切的兼容成本与受影响客户端清单纳入风险表。
- 产出 `decision-freeze-identity.json`：decision/owner/影响面/wire 或 migration 版本/回滚/兼容客户端/独立预期向量。M1-02/M1-04 可在本任务后提前启动，不必等待扩展/CI 的全部决策。

### M0-12 扩展协商与 CI 决策冻结（G02/G04/G20）

- **Owner**：INT 汇总，B/C-RPH 签核；**规模**：M；**前置**：M0-02、M0-08、M0-09、M0-11。
- 冻结研究扩展版本权威、M0-09 的字段位置与 negotiated 来源、external_change_required/scope_change 分支、契约 mutation 与跨端 CI 的 required 范围。
- Ruff 固定 0.15.22，但只作为独立 hygiene 流：基线只在独立 lint 清理或 Ruff 升级 PR 重生成，功能 PR 不得重设；ratchet 不进入 G01–G26 关闭的 required 主链。
- 产出 `decision-freeze-integration.json`：decision/owner/影响面/wire 版本/回滚/独立预期向量。未决项不得以“实现时再选”进入对应 M1 子任务，变更须新 ADR 或新候选。
- **退出**：身份与集成两份决策清单均机械校验通过；M1-01 只消费集成清单，M1-02/M1-04 只消费身份清单，不再保留二选一或全 M0 统一闸门。

### M0-13 DSH 外部适配依赖变更（条件任务，G02/G05）

- **Owner**：C-RPH + B；**规模**：L；**前置**：M0-09、M0-12。仅当 M0-09 选择 `external_change_required` 时成为 M1-01 的强制前置；其他路径标 NOT_APPLICABLE，不进入默认关键路径。
- 将 M0-09 验证所需的上游 DSH client capability 配置或官方 adapter 固化为可审查变更，记录外部仓库 commit/patch/lock digest、维护归属、升级与回滚，不 fork 科学/平台逻辑。
- 发布包必须能取得该依赖版本；真实 DSH initialize/应用握手、扩展缺失、版本不匹配、权限失败均通过，且不产生任务。
- **退出**：外部变更进入 BOM 和安装手册，未修改的源树不再复现缺陷；若变更不可维护，返回 M0-09 的 scope-change，不允许以私有不可移交 patch 冻结 M1。

---

## 5. M1 — 冻结共享契约

**目标**：在扩产品功能之前，让平台、RPH、Workbench 对请求、身份、证据和恢复使用同一套可测试语义。  
**阶段门**：所有契约都有 producer/consumer 正负例、版本和 digest；没有 planned 功能被宣告 delivered。

### M1-01 应用/MCP/Artifact/Analysis 契约统一（G02/G03/G04）

- **Owner**：B，A/C 消费确认；**规模**：L；**前置**：M0-02、M0-06、M0-08、M0-09、M0-12。若 M0-09 选择 external_change_required，还必须先完成 M0-13。
- **独立检查点**：M1-01a 扩展协商/版本轴；M1-01b operation/artifact/状态 schema；M1-01c wire-id 去混淆与隔离治理。每点独立 coverage 退出，不能把契约功能 PR 与扫描器治理混成不可审查的一次提交。
- 以 `p2-research-operations.v1.json` 为研究扩展版本权威，以 `application-api.v1.json` 为应用命令权威，对齐 `control-and-data.v1.json` 与生成快照；把 application-api 的 `mcp_extensions` 和服务端错误 1.1.0 投影修正为契约的 1.0.0，同时保留应用整体 contract_version 的独立演进，并同步 `tests/test_acp2_runtime_contracts.py` 与 `scripts/acceptance/acp2_runtime.py` 中硬编码的扩展 1.1.0 断言。P2 每个 operation **复用现有** `implementation_status` 字段和 `planned|delivered` 值，仅 additive 增加 `verified`（表示跨端证据已签收）并同步 validator；不把 delivered 迁移为 implemented，不重复新增字段。
- 明确基础 8 工具和研究 10 工具的 advertised/implemented/negotiated 关系；只有实现集合覆盖承诺集合才可 negotiated=true。字段位置、客户端确认和应用级握手职责必须采用 M0-09 已验证路径，未定论前不得冻结这些语义或继续把 extensions 放在 initialize result 顶层。M1-01 同时负责删除 `chr(97)` wire 混淆，并把扫描例外作为显式治理变更：修订 root `AGENTS.md` **ANTI #29** 的 wire-id 例外范围，集中到 `entrypoints/wire_identifiers.py`，只允许该模块中经审查的 wire id 常量值，ANTI #32 不新增例外；同步修改 `tests/test_acp_services_isolation.py` 的扫描器，使其按常量值/模块 allowlist 区分 wire literal 与 import/反射，不按行号或函数位置豁免。在 `tests/baseline/services-evidence/migration_ledger.md` 登记理由与退出条件，并同步 `test_services_doc_sync.py` 的 summary 计数/文档投影；补负例证明 `import acp.*`、动态 import、`getattr(...,"acp.*")`、`sys.modules["acp.*"]` 仍被拒绝。未完成这些治理、扫描器和负例时不得合入去混淆。
- MCP submit 的 `idempotency_key` 设为必填，schema 收紧 `additionalProperties:false`，未知字段 typed error；这是客户端可见行为变更，必须同步 policy/schema version、迁移说明和客户端矩阵，老客户端缺键时明确报错而非静默兼容。HTTP manual legacy 若允许缺 key，必须在契约中明示“缺键=非幂等调用”并保留重复任务负例。
- 由于 store 保留跨 scope 防抢占守卫，raw key 在同一 action 内是全局命名空间。契约必须要求客户端使用高熵 UUID 或内嵌 project/research/round 的命名空间；RPH 按 M1-02 生成。增加“两租户误用同一短 key → typed conflict 而非重放”的常驻负例和可操作错误说明。
- 冻结 ArtifactRef 的 task/job/attempt/resource/artifact/generation/digest 语义；prepare 必须有 purpose，read 必须带 artifact_ref，analyze 必须有 analysis_id/input_refs/explicit。
- 区分“读取既有证据”与“执行分析”；read/status/prepare 不得暗中启动计算。
- 冻结 capability descriptor 的 `registered/executable/unavailable` 投影及 analyze 错误。
- **退出**：真实 tools/list 的 schema 与 normalizer 一致，RPH 不再靠 `additionalProperties:true` 或旧资源信封猜测。

### M1-02 ResearchRecord、提交意图、事件与恢复契约（G09/G14）

- **Owner**：B + C-RPH；**规模**：L；**前置**：M0-04、M0-11。D06/D08 是设计约束，不作为 coverage 的任务依赖 ID；研究数据契约不必等待 M1-01 的扩展协商全部关闭。
- 基于现有 experience proposal/research-record schema 与 `events-recovery.v1.json`，补齐 create/read/CAS、round、submit_intent、task_binding、wait_cursor、evidence_ref、decision、revision。研究会话在首次规划前即分配 principal/project/research/session 身份，不能等 proposal/TaskRef 出现后才建立预算或恢复身份。
- **RPH** 用 project/research/round/plan digest 确定性生成稳定幂等键，并在调用前持久化；平台只接收 opaque key，不补全、不改写。SubmitRequest 必须有 idempotency key。已持久 intent 的 plan digest 若变化，不得沿用旧 intent/key 偷换内容；应使旧审批失效并创建新的明确 revision/round 与审批意图。
- 定义 submit 前、submit 后回执前、等待中、证据读取后、record CAS 前的恢复状态和允许动作。
- 复用 `ResearchRecoveryCoordinator` 的 cursor/snapshot/对账语义；研究 resume 不调用 JobManager 的 review-only `resume()`。
- **退出**：每个崩溃点都有“已持久事实 + 恢复动作 + 禁止动作”，无本地内存或 WAL 才能成为权威的设计。

### M1-03 Evidence 投影、TaskRef 与 ResearchDecision 契约（G07/G08）

- **Owner**：A + B + C-RPH；**规模**：L；**前置**：M0-02。
- 统一平台 `{project_id, job_id/task_id, attempt, release:{id,digest}}` 与 RPH TaskRef 的显式归一，不缺省 attempt/release。
- 定义 CCCP 原件 digest、artifact refs、研究身份 envelope、无损消费投影和独立 ResearchDecision。
- 明确 completed/partial、inconclusive/not_evaluated、blocking missing check 的决策策略；模型文字不是证据。冻结 worker qualification、step outcome、publication status 的 `{complete, partial, incomplete, failed}` 映射，证据投影只消费 digest-bound task_result/receipt/artifact，不把“存在返回值”映射为 accepted。
- **新建** ADR-003 实体文档；科学报告、平台归属和研究决策分别给出正负 fixture。
- B 拥有唯一平台投影实现（拟新增 `acp_services/runtime/evidence_projection.py`，使用 CCCP 公共 parser，不 import acp）；C-RPH 在较低层 `rph/packages/research/src/evidence.ts` 定义唯一 EvidenceView/ResearchDecision 类型，bridge 只做解析/归属 adapter，`research/memory.ts` 必须复用同一类型，仅持久化投影/引用/决策，不保留另一份报告 validator 或平行字段模型。A 拥有科学 schema/validator，`runtime/science_binding.py` 也收敛到同一解析路径。
- **退出**：CCCP 正例可经唯一 adapter 被真实 RPH 消费；bridge 与 `research/memory.ts` 使用同一 EvidenceView/Decision 类型，静态守护禁止第四份报告模型；foreign project/research/round/job/attempt/release/digest 负例全部拒绝。

### M1-04 科学输入、多摘要、SubmitPolicy 与 Budget 契约（G10/G12/G13）

- **Owner**：B + A + C-RPH/C-Web；**规模**：L；**前置**：M0-04、M0-06、M0-07、M0-11。
- **独立检查点**：M1-04a 科学输入/多摘要/定义与执行身份；M1-04b SubmitPolicy/时钟/QC 扣减；M1-04c planning reservation/历史 usage/execution_limits。每点有独立契约向量和 coverage 退出，不在一个“冻结”动作里同时改变所有域。
- Planner/Interpreter 增加类型化 charge、multiplicity、electronic_state、structure provenance、constraints；缺关键科学值返回 clarification，不补 0/1。
- 按 D06 修正 `plan-identity.md`、B-req-01/11 和 approval schema；审批同时绑定科学和执行摘要。冻结顺序明确为“定义/运行时身份 → 时钟语义 → QC 扣减点 → 历史入账上下文 → 规划额度”，防止后续 wire 反复改版。
- D06 的每个摘要域各登记一条契约行：owner、canonical 输入与字段序、版本、唯一校验实现（平台 / CCCP / RPH 中的哪一侧）以及旧 wire 字段的兼容映射；未登记校验者的域不得标 frozen。
- 新增单点 `SubmitPolicy/classify_submit`，替代 `_is_research_launch` 与 `_enforce_approval` 两套字段集。Agent 审批要求来自服务端 credential scope/policy profile；不强制新增 `PrincipalKind.AGENT`。
- local-single/manual legacy 明示允许并记录 submit_class；Agent key 的 omit-all、budget-only、缺 proposal 均不能降级。
- 按 D09 统一预算形状、单位/资源 bound/timeout/recorded-only、RPH 映射和可信 usage；非空未知形状必须 typed error。明确 `wall_seconds→ResourceBounds/timeout`、`queue_seconds→queue deadline`、`input_tokens/output_tokens→recorded-only + RPH 客户端限流` 的映射，禁止这四项静默丢弃或被写成平台 enforced。
- 冻结三类时钟：queue deadline 从准入/排队开始；per-attempt wall deadline 是持久绝对时间，默认包含 PAUSED，重启只恢复剩余时间；science runtime 记录实际执行时间，不能用 RPH wait 超时代替。三者的优先关系必须写进同一契约：排队期只受 queue deadline 约束，启动后按 wall deadline 终止、science runtime 仅记录，冲突时由平台按该顺序解算，客户端/RPH 不得择一绕过。审批值经统一 resource resolver 同时映射 Local 终止、CCCP `resources.timeout_s`（仅子进程级）与 LSF `-W`，cluster 默认空 walltime 不得被当成已限时。
- 首次模型规划采用独立 planning reservation：principal/project/research/session/round/call_seq 构成稳定身份，planning budget_id 使用 project+research/session 而非尚未存在的 proposal_ref；服务端派生非空 claim_id 和 request/receipt digest，不伪造 job/attempt。该前置阶段必须作为版本化预算身份契约的 additive 例外同步 root AGENTS、Services 台账与能力投影；科学 proposal 产生后仍走既有 project+proposal_ref 的执行审批与 TaskRef reservation，并用 research/session 关联两阶段账本。unknown/stream 中断保持 reserved 挂账并对账，不能静默 release 重置额度。
- **退出**：带电/开壳层输入保留，多摘要及定义/执行身份有跨端向量；新增 `scripts/acceptance/check_execution_limits_consistency.py`，对 `UNIT_CAPABILITIES`、`execution_limits`、真实 `/api/capabilities`、release statement 和 BOM 声明做机械 diff，任何 enforced/recorded-only/NOT_VERIFIED 不一致即失败。

### M1-05 新建 ADR-008 并冻结限定 DA 科学判据（G15/G18）

- **Owner**：A，B/C-RPH/C-Web 会签；**规模**：L；**前置**：M1-03、M1-04。
- 以 `science/requests/ADR-008-input.md` 为输入，新建共享 ADR-008 实体，明确简单基元 DA fixture、输入身份/atom mapping、方法与电子态、Recipe 必需步骤、预算依据和软件/版本要求。
- 在真实计算前冻结 TS/频率/双向 IRC/端点关联/能量可比性的 supported/refuted/inconclusive/not_evaluated 判据、阈值及缺失证据降级规则。
- 固定正例、负例和“执行完成但科学无结论”样例；明确 scenario_result 与 claim_status 的分离，以及 Local/LSF 各自 required 行。
- **退出**：ADR-008、fixture、阈值、预算与验收行均有 digest 和三方会签；后续不得根据实际结果倒推科学通过标准。M4-04 此前置不可豁免。

### M1-06 早期薄纵切与 Candidate-Contracts（G02/G07/G11/G14/G20）

- **Owner**：INT；B（最小 writer/approval/submit）+ C-RPH（最小真实 MCP client）+ C-Web（真实响应 consumer）+ A（证据 adapter）；**规模**：L；**前置**：M1-01、M1-02、M1-03、M1-04、M0-05。
- 提前交付 M2-01a 的最小 ResearchResource writer、M3-01a 的真实 MCP PlatformClient 和 M3-05a 的最小 frontend consumer，后续任务扩展而不是重写。不得等 M4 才首次组合这些组件。
- 从空项目完成 proposal→approval→submit→evidence：ResearchStore/Controller/SQLite/MCP/Bridge/Supervisor 均用真实实现，科学执行只在明确的 CCCP dispatch 边界使用受控 stub；Supervisor 可显式注入 test skills，Workbench 至少用真实 contract/只读视图消费平台响应。
- 生成 Candidate-Contracts：记录 candidate tree、contract digest、数据库迁移、真实调用 trace、错误路径和 stub 边界，evidence_kind=fixture，不宣称 real science/model/browser。
- **Go/No-Go**：INT 与各 owner 在此检查已完成/返工的契约数、实际人力与模型/QC 额度消耗、plan-graph 剩余关键链、可观测性基线和未决风险。若薄纵切失败或剩余投入超出已批准容量，必须选择修复后继续、缩小到人工工作台或停止完整 Agent 线；不得无条件进入 9 个 XL 的后续实施。
- **退出**：真实空项目纵切和错身份/旧 revision/伪证据/恶意提示注入负例通过，后续 M2-01/M3-01/M3-05 复用该最小切片；go/no-go 决策、剩余成本和 scope 写入 coverage。任一环仍靠 seed store、recording client 或本地假 approved 即不通过。

---

## 6. M2 — 平台生产实现

**目标**：不用 RPH 或浏览器，也能证明平台具有真实研究 writer、审批预算、工件分析和 Recipe DAG 生产链。  
**阶段门**：从空项目经真实 HTTP/MCP 用例创建研究事实；无 seed store、STATIC/SIMULATED dispatch 或空 analysis registry 冒充生产闭环。

### M2-01 Research writer、跨库 Saga 与 P2 工具（G02/G14）

- **Owner**：B；**规模**：XL；**前置**：M1-06。M2-01a 已在早期薄纵切交付，本任务完成 CAS/outbox/Saga/P2 全量面。
- 在 `acp_services` 新增研究应用服务，复用 `ResearchResourceStore.create/read/compare_and_swap/revoke`；不得由 MCP 直接写 store。
- 扩展 `v2_research.py`：proposal/record create、CAS update、round/evidence 追加；保留 GET，409 返回当前 projection/revision。
- `ControllerClient` 增加 P2 方法，MCP 按 M1-01 注册研究 10 工具并真实协商；未协商/无权限 fail-closed。
- 分三个检查点验收：**M2-01a** 最小 ResearchResource writer（供 M1-06）；**M2-01b** 各库“业务写 + 本地 outbox”同事务 API、跨库幂等 Saga、重试责任者和 intent 对账；**M2-01c** HTTP/P2/MCP 工具与恢复。每点有独立 coverage 行。复用 `OutboxDeliveryService` 只负责已提交记录投递，不假定它提供跨库原子性；jobs/research/admission 不做 2PC。
- 研究记录只存 approval/task/budget/evidence 引用与必要投影，不复制其权威状态。
- **测试**：空项目→proposal create→approval request/decide→submit→TaskRef CAS→evidence ref→新进程读取；每一步断点重试不产生第二事实；seed fixture 不参与正例。
- **退出**：live tool + ControllerClient method + application use case 三者同交，operation 才能标 delivered/verified；G02/G14 的交付关闭仍由 coverage 的跨端证据决定。

### M2-02 Artifact 链、科学发现与真实 `vibration.analyze`（G03/G04）

- **Owner**：B + A；**规模**：L；**前置**：M1-01、M0-06。
- 修正 MCP prepare/read/analyze 与 Controller adapter；保留旧 attempt、digest mismatch、未 prepare、超限读取拒绝。
- `api/routes.py` 不再返回 `capability_descriptors=None`；与 `/api/v2/science/capabilities` 共用科学 catalog/projection，不重复探测。
- 在 acp 组合根通过 `analysis_capabilities.py` 的接口注入应用级 registry 并注册 `vibration.analyze`，不使用模块级可变 DEFAULT_ANALYSIS_REGISTRY 作为生产权威；handler 调用 CCCP 既有纯分析能力，只消费 prepared artifact。`vibration.compute` 仍走计算 Task，不冒充只读分析，后续与 M2-05 第三方分析共用该路径。
- advertised analyze schema 来自同一 operation contract；registered 不等于 executable。
- **测试**：真实 submit/status/prepare/operation status/read；`vibration.analyze` 经 HTTP 与 MCP 返回同 digest 产品；未 prepare/未注册/错误 input ref 均拒绝。
- **退出**：空 registry 和 `unsupported_capability` 不再被当成默认正例；至少一个真实分析完整可执行。

### M2-03 服务端审批策略与预算强制（G12/G13）

- **Owner**：B；**规模**：XL；**前置**：M1-04、M2-01、M0-07。
- 以 credential policy 和单一 `SubmitPolicy` 接管 HTTP/MCP/CLI；`_is_research_launch`、`_enforce_approval` 成为同一分类器的薄消费点。
- 审批 additive 保存科学计划投影与双摘要；审批人只来自认证 principal，浏览器/模型 payload 不可指定。Agent/model credential 不得调用 approve/decide；是否要求 requester 与 approver 分离由服务端 policy 决定，不能靠 UI 自律。
- 统一 `application_params`、`research_budget`、RPH PlanBudget 映射；包装 `UsageUnitValidationError` 为 typed budget error，禁止 500 和空 limits。解析反例必须经真实 HTTP/MCP 请求与真实 RPH PlanBudget producer，不能只向 parser 喂合成 dict。
- 复用 ApprovalService、ReservationLedger、resource bounds 和 outbox；不把预算写进科学 TaskRequest 身份。
- 分三个检查点：**M2-03a** approval policy 与双摘要；**M2-03b** task/attempt、recipe/QC 最坏额度和 planning reservation 的持久预留；**M2-03c** deadline/usage/能力投影。每点有独立 coverage 行。task/attempt 在派发前，DAG step/QC 在 M2-04 dispatch/后端调用前，model_request 从首次规划起在 M3 gateway 前预留。
- planning reservation 使用 `project/research/session/round/call_seq` 的非空稳定 claim_id、request_digest 和 model receipt_id，不能落入 `claim_id != ''` 部分索引之外。unknown、流式中断、结果未知保持 reserved 并对账；科学审批拒绝不删除已发生模型用量。
- 将审批 deadline 经统一 resource resolver 接到本地进程组终止、CCCP 子进程 timeout 与远程 `derive_lsf_resources` 的 `-W`；持久绝对 deadline、PAUSED/重启语义、队列过期与迟到成功禁止复活按 M1-04 实现。拆开 `local_admission` 当前 admission-bound 与 process-termination 的 enforced 声明。
- **测试**：omit-all、budget-only、缺 proposal、改 plan/budget、过期/撤销/跨身份 approval、Agent 尝试代审批、两用户并发、task_ref/attempt 超限、reservation API 并发/换 run_id/进程重启、空 research 无 TaskRef 的规划额度、规划 claim 唯一索引、中断不重置、科学审批拒绝保留模型用量、缺 usage 保守结算。recipe_step/qc_invocation 的真实反例由 M2-04 执行，model_request 的零调用拒绝由 M3-03 执行，真实进程/LSF 到期由 M4-03 执行。
- **退出**：Agent 不能借 legacy 绕过审批；task_ref/attempt、planning reservation 与持久额度反例通过，recorded-only 不被 UI/BOM 写成 enforced。recipe/qc 只有在 M2-04 完成后才能升级，model_request 只有在 M3-03 完成后才能升级，walltime/queue deadline 只有在 M4-03 的真实终止/过期证据后才能升级。

### M2-04 Bounded Recipe DAG 生产接线（G15）

- **Owner**：B + A；**规模**：XL；**前置**：M1-03、M1-04、M2-02、M2-03。M1-05 仅是 M2-04d 的 DA 公开入口前置，不阻塞 M2-04a/b/c。
- **M2-04a — request/result adapter**：新增 Services 侧唯一 `StepDispatchAdapter`，以 wrapper 匹配单参 `DispatchFn`，完成“冻结 step + 已验证上游工件 → 类型化 TaskRequest/TaskContext → run_* → 完成资格 → 声明产物 digest”。`StepDispatchContext` 必须带 work_dir/input_base/attempt 与上游工件路径，不能只带 digest；每个 step 另需显式 output_dir、方法/资源/软件配置解析结果、输入角色（`structure_role` 与多输入角色）、capability 与 provider 版本，禁止把计划 dict 原样交给 runner，也禁止由 runner 自行猜测输入角色或默认配置。
- 完成判定复用 `runtime/worker.py::decide_task_result` 和 `TASK_COMPLETION_AUTHORITIES`，不得在 DAG 复制真值表。`StepOutcome/StepState` 增加 qualification、publish_policy、products；failed、complete=false、缺必需事实、未知 status 不得记正常 completed，partial 不得释放 required 下游。`StepSpec.diagnostic_only=true` 必须显式映射为 diagnostic 结果，恢复时重判，不能因 TaskResult completed 就释放正常 required 下游。声明 `energy` 等 product_kind 必须映射到真实产物；现有 adapter 同时产生 `output_digests={"result": digest}` 与完整 `publish_payload`，需要分别校验产物身份和发布语义，不能只看 output_digests 或把 publish_payload 当作声明产物。
- **M2-04b — 冻结定义身份与运行时执行身份**：转换在 freeze 时完成，`BoundedStepDag` 随冻结组落盘，worker 只反序列化。envelope 的既有 `step_identities` 明确为 `step_definition_identities`（wire 可兼容保留），Runtime `step_dag` 不再维护第二指纹算法，运行时只使用 CCCP `materialize_step_identity` 和实际上游 digest。
- 逐 step `step_result.json`、DagRunState 和发布记录保存 execution identity；任务 receipt 新增 additive `step_execution_chain + chain_digest`，Controller 同时校验定义身份集与执行链。禁止运行时回写 envelope 或用占位 digest。审批 `plan_digest`（`sha256:` 域）与冻结体 `frozen_plan_body_digest`（`v2:` 域）必须分别校验：任一单独变化都使该项身份失效，二者不得互相代偿。`identity_scheme` 不明的旧 DagRunState 默认拒绝收养，迁移/重算必须显式决策并进入 recovery fixtures；收养前还须核对 plan、输入与上游产物一致，不一致按冲突处理，不按 attempt 号或完成步数猜测。
- **M2-04c — 新建额度、执行权与恢复能力**：A 在 CCCP 编译器新增 `worst_case_executions` 及 TaskContext meter Protocol（当前均不存在），B3 在 reservation/worker 新增适配，一次预留 recipe_step/QC 最坏额度、每个后端调用前取额度并按回执结算。删除 `+len(steps)` 隐式 caps 和默认兜底，恢复 allowance 单独授权。
- `execution_right.py` 当前只有 acquire/verify，且 AlwaysHeld 是过渡实现。B3 必须**新建** `FencedCommitPort/commit_if_fenced` 及持久实现：文件先写非权威 staging，所有 checkpoint、step_result、PublishFn/retry 的权威提交在提交点同时校验 attempt/owner/fencing 与当前执行权，再原子更新权威 manifest/状态；不是调用现成端口，生产不得使用 AlwaysHeld。每次 step dispatch 前另轮询控制意图。DagRunState 带 attempt/owner/fencing/schema_version/identity_scheme，worker 不直读 jobs DB。
- 三项分别设 coverage：c1 编译/计量端口与 rescue 超限，c2 fencing 原子提交与失权竞争，c3 资源预检与恢复。必需 step blocked/upstream_failed 整体不 completed，发布失败只重试发布。
- **M2-04d — 公开入口与发布**：在 `acp_services/worker_bootstrap.py` 注入 DAG TaskExecutor；一个 TaskRef/attempt 多 step。新 Recipe 工作流 `closure="integrated"`、加入 `DEFAULT_ENVELOPE_WORKFLOWS`，并为 `WorkflowExecutionCapability` **additive 新增** `requires_envelope` 字段及同步构造器/fixtures/schema，缺/坏冻结组 typed error，attempt protocol 记录 `entry=envelope`，绝不回落 legacy。
- acp 侧 `src/acp/calculations/attempt_freeze.py::FREEZE_BUILDERS` 产出 freeze spec，由 `acp_services/runtime/attempt_freeze.py::write_frozen_attempt` 落盘。step 产物与 PublishFn 在 Services；task manifest 仍由 acp 在 worker 后沿 `result_publication` 唯一发布，禁止 Services import acp。`acp/calculations/primitives/` 不是无逻辑空壳，其中 ACP 发布契约（含 `frequency.py` 的 normal_modes 发布半部）必须保留并通过现有 seam 调用；迁移时明确输入/输出/回滚和兼容 forwarder，不能误删约 2k 行发布逻辑或复制到 Services。
- `step_requirements.py` 只负责上游 StepKind/产物前置，不能冒充二进制检查。另由 B3/A 在 worker 组合根新增 `ExecutionResourcePrecheck`，用 `cccp.software.resolve_executable/detect_version`、release manifest 与 `lab_capabilities` 在 dispatch 前验证二进制/版本/资产，缺失时 blocked；不得把昂贵失败留到后端调用后。M2-04d 的 DA 公开入口另以 M1-05 为前置。DA 搜索类步骤若被 ADR-008 列为必需，必须声明其在 bounded/outer 阶段的身份与证据链；不可映射即保持 OPEN。若新增 active workflow，同步 catalog/registry/edit coverage/remote allowlist/frontend/i18n 与 `_SCHEDULER_MARKERS`。
- 新生产 DAG 使用独立 scenario id。B18 STATIC/SIMULATED 只保留 fixture 证据，不能关闭 production 行；历史回执不改写。
- **测试**：四种 TaskResult 真值反例/正例、diagnostic-only 不释放正常下游且恢复重判、产物缺失/类型不符、真实两步骤上游文件到下游 request、每 step 的 output_dir/输入角色/配置解析向量、上游 digest 改变只使正确下游失效、伪 step receipt 拒绝、审批摘要与冻结体摘要分别变化各自被拒、cap=1 第二次后端调用预阻断或最坏额度不足预先拒绝、回执丢失或进程重启后不得重新获得未扣减额度（额度只来自持久 reservation）、并发与部分失败不突破授权额度、两 worker 失权竞争、检查与提交之间失权的原子 CAS 负例、cancel 后无下一步、发布前失权、kill/接管恢复、缺 binary 预阻断、Local/remote 同 frozen group。
- **退出**：四个子检查点依次通过；公开入口到达真实 StepDispatchAdapter 和 CCCP Task 核心，多步可完成且错误结果不会成功，未经过自造 StepOutcome 的生产路径证据不得关闭。

### M2-05 第三方科学插件生产装配（G04/G16，R07）

- **Owner**：B + A；**规模**：L；**前置**：M1-04、M2-02。
- 本任务与 M2-03/M2-04 可并行，不阻塞 P2 核心纵切。
- 先选唯一权威，禁止新增第五套注册表：Services `capability_registry` 负责授权/冻结/历史快照，CCCP `PluginHost + invoke_capability` 负责默认禁用的执行与输出契约，builtin/domain catalog 提供内置能力；`adapter_registry` 只作明确的内部适配或另立 deprecate 决策。discovery 始终 metadata-only，enablement 默认 disabled。
- 建立单一发现 provider，合并 builtin/domain/已授权插件，`/api/v2/science/capabilities` 与 `/api/capabilities` 共用；以 generation/etag 失效 app.state 缓存，修正 v2_science 陈旧未挂载注释。
- 复用现有 pip 安装工具，不建商城/GUI。平台提供受授权的 enable/disable 或 CLI 路径，持久 installed/enabled/enabled_by/enabled_at；新进程从安装与启用事实发现，无测试内手工 register。
- 打通首个**纯分析**插件：只消费 prepared 工件，经现有 analysis/task binding 与 PluginHost 执行；内置 `vibration.analyze` 和第三方分析共用注入 registry/公开路径，不使用模块级可变 DEFAULT_ANALYSIS_REGISTRY 作为权威。
- 分析操作 provenance 记录 `capability_id@version + descriptor_digest + form_digest + computed_distribution_digest`。distribution digest 必须由实际安装字节计算，manifest 自述值不能替代；升级/卸载后历史经冻结快照只读。
- 本期纯分析在 Controller 内执行，不要求修改 worker release。**签收口径同步缩窄**：worker-hosted 第三方 COMPUTE/distribution 冻结为独立 `DEFERRED` 行，不用纯分析结果关闭；若完整 ACP2 的发行声明仍包含该能力，则该行保持 NOT_VERIFIED，完整产品不得放行。未来允许第三方 handler 进入 worker attempt 时，另立 `required_plugins`/冻结组成员与 worker 校验任务；不得借本项放宽 COMPUTE 禁止或允许插件直接起子进程。
- **测试**：干净 venv 只安装 fixture wheel + 授权 enable，真实服务新进程发现；未授权/未知 license/版本冲突/自述 digest 与实装不符均 fail-closed；v2 新分析使用新 pin，v1 历史不变，卸载后旧 descriptor/artifact 仍可读。
- **退出**：公开“安装→启用→发现→执行→升级→卸载历史”链成立，未改 ACP/RPH/Workbench 源码；只有表单出现字段或测试内 register 不算关闭。

---

## 7. M3 — RPH 与 Workbench 产品装配

**目标**：发布入口实际运行真实 Bridge、Supervisor、ResearchStore 和平台适配器；UI 状态全部来自平台事实。  
**阶段门**：product 模式缺 model/P2 writer/evidence path 时 fail-fast；无 fixture planner、无本地审批成功、无单次 wait 终态。

### M3-01 真实 MCP PlatformClient、Bridge 参数与 TaskRef（G02/G03/G21）

- **Owner**：C-RPH；**规模**：L；**前置**：M2-01、M2-02、M0-05。
- **子检查点**：M3-01a 为 M1-06 提供最小真实 MCP PlatformClient（initialize/submit/status/typed errors）；M3-01b 完成扩展协商、prepare/read/evidence 与恢复全量面。
- 按 M0-09 已验证选型实现官方 DSH `ctx.tools` 或官方 MCP SDK PlatformClient。**实现归属 dsh-bundle 的公开 adapter**，Bridge 只做参数/结果映射，禁止在 bridge 引入 fetch/HTTP/spawn；stdio 进程生命周期由 CLI launcher 负责，dsh-bundle 只消费注入 transport。若选择 SDK 直连，在 `rph/packages/dsh-bundle/package.json` 声明精确 `@modelcontextprotocol/client@2.2.0` 运行依赖，根 devDependency 只用于测试，不能替代包级发行依赖。
- 将真实 initialize/应用握手获得的 negotiated extensions 和真实 tools/list 注入 Bridge；不得由配置或测试常量伪造来源，版本不符或 P2 缺失在 product 模式拒绝启动。
- 按 M1-01 重写 prepare/read/evidence arguments，保留 generation、artifact_ref、analysis_id、input_refs；区分 evidence read 与 analyze。
- 实现唯一 TaskRef normalizer；submit replay 直接保留完整 TaskRef，reference 不完整时通过 status 对账，不猜默认值。
- **测试**：真实发现的 submit→status→prepare→operation status→read；跨操作同 key、并发、跨实例重试、错误 target 全部经过真实 Bridge 与真实服务端。
- **退出**：不再使用 recording client 的单边快照作为互通证据。

### M3-02 CLI 组合根、可移植 profile 与 doctor（G05/G06/G17）

- **Owner**：C-RPH；**规模**：XL；**前置**：M3-01。
- **分批关闭**：任务分为 M3-02a（compose/CLI/profile/doctor 骨架）与 M3-02b（product fail-fast 矩阵）；M3-02b 与 M3-03/M3-04 同批关闭，避免形成“组合根等待 skills、skills 又等待组合根”的文字依赖环。
- **拟新增** `rph/packages/cli/src/compose.ts` 作为唯一组合根，但 CLI 只 import `@rph/dsh-bundle`。在 dsh-bundle 新增公开 composition/port factory，由它经 `@rph/bridge` 获取 Bridge、ResearchStore、Supervisor 与 wait/persist/approval ports；bridge 再按既有 `@rph/research` 公共出口提供 ResearchStore。`check-boundaries.mjs` 只允许实际链边 `cli→dsh-bundle→bridge→research`，禁止 CLI 直接 import bridge/research，不新增 compose package，也不把 Supervisor 状态机复制进 CLI。
- CLI 增加 `research`、`resume`、`doctor` 和真正 `bin`；版本从单一 package 来源注入，不再保留第二 `0.0.0` 常量。
- profile 使用环境/配置参数，分离 dev/acceptance/release；不得包含本机绝对 Python、checkout cwd、验收 run_root 或明文 key。
- product 模式要求 model skills、P1/P2、research writer、artifact evidence path 全部存在；缺项一次性列全并非零退出。demo 显式标识，默认不得对真实 Controller 运行 fixture。
- 修改 `check-boundaries.mjs`/README：只对具名 launcher 放行必要客户端进程启动；其他 spawn/shell/QC 仍禁止。补齐 `fetch`、`undici`、`node:http/https` 等直接网络规则及负例，bridge 的禁 fetch/HTTP/spawn 必须由 gate 执行而非只写约束。不得启动隐藏的第二 Controller。
- `doctor` 分静态和 `--connect`：检查版本、路径、握手、权限、扩展、writer、预算、模型配置和秘密脱敏。
- **测试**：另一绝对目录、无源码 PYTHONPATH 的启动；缺 model/writer/extension 时 fail-fast；非 launcher spawn 仍被 gate 拒绝；profile 无验收绝对路径。
- **退出**：M3-02a 以一个受支持命令启动发布包中的 Supervisor（可显式注入 test skills）闭合，不只是导出 runSupervisor 或打印 help；M3-02b 只有在真实 model/P2 writer/evidence path 缺项矩阵全部 fail-fast 后才闭合。G05 的最终关闭仍需 M4-02 的真实首轮与反馈轮。

### M3-03 生产 Planner、科学确认与模型预算 gateway（G10/G13）

- **Owner**：C-RPH + A/B；**规模**：L；**前置**：M1-04、M2-01、M2-03。
- 将当前 deterministic planner 明确改名/隔离为 fixture；product composition 拒绝它作为 fallback。
- DSH model adapter 只产生候选 typed plan，经现有 schema/科学 validator；失败返回 invalid/clarification，不自动提交。
- 电荷、自旋、电子态、方法和输入 provenance 必须保持；不确定时向用户请求确认。
- 平台返回/确认执行 plan digest；RPH 科学 digest 与审批绑定按 D06 处理，跨端 canonical vectors 全通过。
- 首次 understand/hypothesize/critique/plan 之前即经 `acp_services.runtime` + Controller 的生产模型 gateway 预留 planning model_request，调用后记录可信 token/usage；不能用 Supervisor 内存 used 作为结算。模型凭据只能由该 gateway/proxy 持有，DSH 不得绕过直连；若仍采用直连，则该维度只能标 recorded-only，且对应预算验收行保持 OPEN。gateway 回执绑定 research/round/call/request/model，不伪造 job/attempt；unknown/retry 按 M1-04 对账。
- **测试**：charge=1/multiplicity=2、非默认方法、多输入、缺值澄清、digest 变化、平台摘要不符、模型输出非法；空 research 无 proposal/TaskRef 时限额内首调成功、额度拒绝时零模型调用；并发/重启/流式中断不重置额度；以真实 gateway 同步取代 Supervisor 中 `used.model_requests = used.model_requests` 死赋值。
- **退出**：生产启动不会落入占位 planner，科学输入和批准身份可追溯。

### M3-04 可恢复 Supervisor、证据归属与研究决策（G07/G08/G09）

- **Owner**：C-RPH；**规模**：XL；**前置**：M3-01、M3-03、M1-02、M1-03。
- SubmitRequest 增加稳定 idempotency key；提交前先经 Research writer 持久 submit intent，提交后绑定 TaskRef。
- wait 使用有界循环和 cursor；无终态返回 `waiting + resume_handle`。恢复经 P2 snapshot/resume 与 Runtime events，不新建任务状态机。
- 强制 EvidenceBinding/verify 步骤，不再选择“第一份非空 report”。外来 event/report、多个歧义终态、digest 不符均停止且不得 persist accepted。
- 通过唯一证据投影读取 claims/limitations/missing checks，ResearchDecision 独立确定；completed-but-inconclusive 不得 accepted。
- used budget 从平台记录/receipt 恢复，不从新进程空内存重新估计。
- **测试矩阵**：提交前、提交后回执前、TaskRef 绑定后、等待中、cursor 过期、证据已读未决策、决策后 CAS 前崩溃；另测重复事件、超一分钟等待、异项目/旧 attempt/错 release/错计划/错 cursor，以及 M2-04c 接管后旧 worker 不得再写中间 checkpoint/publish 的联动反例。
- **退出**：新进程保持同 research round/TaskRef/attempt，不重复计算、不丢失证据、不产生错误 accepted。

### M3-05 Workbench 真实审批与提交（G11）

- **Owner**：C-Web + B；**规模**：L；**前置**：M2-01、M2-03。
- **子检查点**：M3-05a 为 M1-06 提供最小真实 frontend contract/只读 consumer；M3-05b 完成 research commands、产品 mount、审批与提交全量面。
- 新增 `frontend/js/client/research-commands.js`，通过注入 v1/v2 transport 实现 request/query/decide/revoke/submit；保留 `research-query.js` 只读职责。
- 保留 `frontend/js/workbench/compat/api.js` 已有的 `ACPClient.research.init({v2})`，补 `researchCommands.init` 与 HTML script load；`research-approval.js` 无 commands 时抛 `platform_transport_required`，删除 `apr-*`/`dsp-*` 和本地成功 fallback。验收针对 commands/load/审批回写，不把已有 read init 重写一遍。
- UI 由 server record 映射 status/revision/approver/task_ref；支持 draft/pending/approved/rejected/revoked/expired；409 刷新后要求复核，不自动重放决策。
- submit 只有收到真实 job_id/attempt/TaskRef 才显示 submitted；随后刷新 approval 和 task view。网络失败显示未确认，不显示成功。
- 同页展示科学计划摘要与执行计划摘要、审批预算与当前预算；`science-question.js` 研究审批模式复用唯一 payload builder，不创建第二 submit body。
- **测试**：模块无 fetch 断言保留；新增真实 HTTP adapter 集成、跨刷新/第二客户端、pending/revoked/expired、stale revision、跨 principal/project、改 plan/budget、缺 TaskRef 负例。
- **退出**：真实浏览器批准后平台存在同 approval，提交存在同 TaskRef，另一客户端可读；fixture 仅用于明确 demo 测试。

### M3-06 P3 Schema-Driven capability form（G16）

- **Owner**：A + C-Web + B；**规模**：L；**前置**：M2-02、M2-05；不阻塞 M4 的 P2 最小纵切。
- 不把 `CapabilityDescriptor.input_schema`（当前为 SchemaReference）误当 JSON Schema，也不修改 closed capability schema 塞 UI 字段。
- 新增独立、版本化 form Schema 文档和 `acp2.ui-subset.v1`；以显表白名单逐个冻结允许的 JSON Schema 2020-12 关键字、binding 到 TaskRequest 的路径和单位表及 digest，不实现开放式通用 JSON Schema 求值器。
- 不支持 `$ref`/组合关键字/未知 major 等必须拒绝并显示 typed unavailable，不得忽略后提交。
- 新增 `frontend/js/workbench/features/capability-form.js` 和共享 subset validator；远端文本用安全 DOM API，不能注入 HTML。
- capability 发现、字段约束、preflight、审批/submit、ViewModel 全链复用既有 client；descriptor/form digest 与人工修改写 proposal provenance。
- 插件安装/启用/卸载后，历史任务通过 frozen descriptor/artifact 只读；若产品要求完整生命周期事件流，另建 append-only 记录并如实标注范围。
- **测试**：只新增第三方 form/descriptor、不改前端源码即可出现表单；权限、版本升级、未知关键字、卸载后历史读取、旧手动工作流回归。
- **退出**：第三方 form 经真实发现→表单→preflight→ViewModel 的 headless/HTTP 链通过，未知 schema 负例齐全；P2 三问题切片不再被拿来代替 P3。真实浏览器 `schema-form-live` 由 M4-05 关闭，不作为本任务的提前退出条件。

---

## 8. M4 — 分级真实验收

**目标**：实现并运行真实纵向场景，而不是继续增加 fixture 或静态回执。  
**通用规则**：测试服务、数据根、端口、身份库和科学作业均隔离；一次提交得到的 TaskRef 被 A/B/C 共同读取，不为“三入口一致”重复创建三个任务。

### M4-01 无 QC 的全产品链与故障矩阵（G20 及所有接口项）

- **Owner**：INT；**规模**：L；**前置**：M2-01～M2-04、M3-01～M3-05、M0-03。
- M2-05 属 P3 插件线，不阻塞本 P2 全链。
- 使用真实 ResearchStore/Controller/SQLite/HTTP/MCP/Bridge/Supervisor/Workbench adapter，科学执行只在明确的 CCCP dispatch 边界受控替代；模型也可用显式 test adapter，但 evidence_kind 必须为 fixture。
- 完成空项目→proposal→approval→submit→wait→prepare/read→evidence→decision→下一 round 的全链。
- 实现 live harness 的真实 Controller/model/client 调用分支，供 M4-02 使用；禁止再写死 delivered=false。
- 故障覆盖：跨工具同 key、跨用户、撤销、非法 remote target、伪 receipt、approval 绕过、预算并发、崩溃恢复、错证据身份、cursor 过期、发布重试，以及 R01–R09 的不完整 TaskResult、QC rescue 超限、运行时身份错配、失权中间写、历史 attempt 用量、无 TaskRef 规划额度、旧派生键/旧 Data 行迁移、恶意工件提示注入与伪造回执等低成本组合。重复 prepare/read 必须保持 Data 幂等，不产生额外任务、重复操作行、传输或写放大；记录删除缓存后的 Controller 调用量与 p50/p95。
- **退出**：所有低成本反例在一个组合候选上通过；该结果只关闭接口/持久语义，不宣称 real science/model/browser。

### M4-02 真 DSH/模型 + Local 科学 + Chromium（G05/G06/G11/G18）

- **Owner**：INT + C + A/B；**规模**：XL；**前置**：M4-01、M0-10。仅当 environment-readiness 中模型/Local/Chromium 必需项实际 PASS 才可启动；未获授权不得调用。
- 从发布形态的 profile 启动真 DSH/RPH，真实模型完成至少首轮和证据反馈轮；模型用量有上限与原始调用回执。加入一份含审批/预算伪造指令的不可信工件，验证模型建议不能改变平台授权或证据归属。
- 运行真实 Local singlepoint，随后按声明范围 optimize/frequency；保留原生输出、release/attempt/receipt、manifest 和 artifact digest。
- 两个独立 Chromium identity/context 完成请求、批准、撤销/拒绝负例、提交和刷新；第三只读身份不能越权。
- 实现已声明的 `same-task-browser-live`，并将现有 `approval-budget-live` 从 fixture-only 扩展为真实审批/预算浏览器场景，**复用已注册 scenario id，不新增 `research-approval-live`**；同步场景 schema、required_observations、证据根和清理。记录网络状态、console/page errors、TaskRef、attempt、工件 digest、截图；截图不能替代平台回执。
- CLI/MCP/Workbench 读取同一 TaskRef；remote read 仍走 `_job_read_root`，不得直读 record.work_dir。
- **退出**：真实模型工具事件、真实科学作业和浏览器网络/平台事实相互引用。模型不可用或浏览器未运行则相应行 NOT_VERIFIED。

### M4-03 LSF、执行约束、控制与恢复（G18/G23/G24/G25/G13）

- **Owner**：B + INT；**规模**：XL；**前置**：M4-02、M0-10。仅当 LSF 账号/队列/配额/清理责任人实际就绪才可启动。
- 使用合法 remote target/node_tags 提交，证明 HTTP/MCP 的冻结 JobSpec 和实际 bsub 目标一致；非法节点仍在入口拒绝。
- 同 TaskRef 验证 bsub/bjobs、pause/cancel 竞争、结果拉取、prepare/read、artifact digest 和 release identity。
- 做 Controller/MCP/RPH 进程重启、回执丢失、重复事件和发布失败；不重复昂贵计算，发布失败只重试发布。
- 多用户/跨项目/撤权和预算并发在远程路径重验；普通 member 仍不能写 usage。用受控真实进程验证小时限进程组终止、queue 过期、PAUSED 计时、重启剩余时间、pause/cancel 竞争和迟到成功不复活；LSF 同时核对审批值→`#BSUB -W`→原生终止回执，并覆盖 `cluster.walltime=""` 的默认无限时负例。
- **退出**：当前候选的原生 LSF submission/receipt 与工件闭合；历史其他候选 PASS 不直接迁移。审批限时、实际 bsub 参数和终止回执缺一即保持 execution_limits 未验证。

### M4-04 限定 Diels–Alder 科学纵切（G07/G08/G15/G18）

- **Owner**：A + B + C + INT；**规模**：XL；**前置**：M1-05、M2-04、M3-04、M4-02。Local DA 不必等待全部 LSF 场景；若 ADR-008 将 LSF DA 列为 required，则还必须组合 M4-03 的远程证据，未执行时该行 NOT_VERIFIED，不能用 Local 结果替代。
- 先冻结简单基元 DA fixture、atom mapping、方法/状态、所需步骤、阈值和完成/不足判据，再从结果评估，不能倒推标准。
- 公开入口提交 bounded Recipe，执行所有声明的必需步骤；TS 优化、频率、双向 IRC、端点关联和能量可比性使用原生工件及确定性 validator。
- 单虚频不等于反应连通性；单侧 IRC/缺端点/缺来源必须 inconclusive 或 not_evaluated；端点证伪可 refuted。
- EvidenceReport 经平台归属校验、RPH 决策和 Workbench 展示，保留 source_refs/limitations/missing checks/provenance。
- 回执分别记录 `scenario_result` 与 `claim_status`：执行链可按事实通过，科学命题不预设 supported；无结论不能写成科学成功。
- **退出**：同一 TaskRef/attempt/release 的 Recipe、原生 TS/频率/IRC、报告、研究决策和 UI 证据闭合；缺任一 required 行，H4 保持 OPEN。

### M4-05 第三方 capability 插件纵切（G16/G18）

- **Owner**：A + C-Web + B；**规模**：L；**前置**：M3-06、M4-02。
- 选择一个不改 ACP/RPH/Workbench 源码的第三方 capability，完成安装、授权启用、发现、表单、preflight、受控执行、结果展示。
- 验证不兼容版本、未知 license/依赖、权限不足、manifest 自述 digest 与实装字节不符安全失败；升级后新分析操作冻结新版本及 computed distribution digest，卸载后旧历史仍只读。
- 实现 `schema-form-live` 真实浏览器场景。
- **退出**：插件由真实公开注册/发现路径生效，不是测试进程内手工 register 后即宣称生态闭环。

### M4-06 跨领域、迁移与长期可靠性 required 行（G18）

- **Owner**：A + B + C + INT；**规模**：XL；**前置**：M4-01、M0-04、M0-05、M0-06、M0-10。
- **M4-06a — 迁移/升级/回滚**：由 B+INT 在 M4-01 后尽早执行，可与 M4-02/M4-03/M4-05 并行。使用“上一已发布制品 → 当前候选预发行暂存制品”组成升级对，完成数据库/schema、Controller/RPH、备份恢复与回滚；旧任务、ResearchRecord、插件快照保持只读，新任务不写旧格式。
- 复用既有 migration/recovery fixtures 和真实备份副本，整链复验 M0 的旧 local scope、旧 MCP `auto_` 派生键、旧 request digest、旧 prepare v1/v2 与历史 usage；迁移失败可恢复且不裸删 jobs。M4-06a 有独立 coverage 行，不等 P3/LSF 才开始设计或演练。
- **M4-06b — 跨领域/多租户 required 行**：由 A+B+C+INT 执行，前置 M4-06a、M4-03、M4-05。按完整 ACP2 H6 范围复验 NMR/热化学对公共 capability、预算、EvidenceReport 和 ViewModel 的复用；不得用 singlepoint/DA 证据替代跨领域行。
- 在当前候选执行多项目、多用户、多假设并发，核对研究记忆、缓存、approval、budget、TaskRef 和 artifact 的隔离；撤权后各入口一致失效。
- **退出**：M4-06a 的升级/回滚与 M4-06b 的跨领域、多项目多用户、一致性分别取得当前候选原生回执；H6 只有两部分都通过才关闭。若发行决定不含这些能力，必须按 §0.2 正式缩窄产品范围，不能仍宣称完整 ACP2。

---

## 9. M5 — 发行、BOM 与独立移交

### M5-01 全组件制品与安装入口（G06/G17）

- **Owner**：REL + C-RPH + B；**规模**：L；**前置**：M3-02、M3-06、M4-01；最终关闭需 M4 全部。
- 保留已有 CCCP/acp-worker/acp-web 与 root ACP 安装成果，不重复“修复”已通过的 OriginPolicy；将 root 构建与三角色制品纳入同一 BOM，而不是硬编码制品数量。
- `rph/scripts/package.mjs` 打包所有实际 workspace 包，生成每个 tgz 的 name/version/size/sha256；CLI bin、版本、license/notice/profile 完整。默认保留 `private:true`，以签名 tgz 制品分发，不宣称已发布到公共 npm registry；若将来公开发布另做 license/发布决策。
- **RPH 包安装缺陷为必修项**：2026-10-26 干净 ARM64 主机探测证明当前四个 tgz 虽通过构建与 SHA 校验，但 `@rph/bridge` 等内部依赖仍被 pnpm 解析到公共 registry 并返回 404（见 `reports/acp2_remediation_20261026/rph-package-install-probe.json`）。M5-01 必须先选定私有包安装权威——bundled dependencies、file dependency/私有锁清单或聚合 RPH 包——并使包集仅从 artifact store 可安装，再把该探测翻转为 PASS；禁止把“tgz 可打包”写成“独立可安装”。
- 生成 `experience/release-manifest.json` 和 `static-assets.manifest.json`；静态资产从安装后的 web 包验证，不只从源码树验证。M4-05 使用的第三方插件 wheel 及其 name/version/sha256 也纳入可取回制品，不能只保留临时构建路径。
- 静态资产服务只读、digest 启动校验、路径穿越拒绝、未知 404；release mount 不依赖 checkout。
- `check_release_artifacts.py` 等按 manifest 遍历，不再写死 3 个 wheel/sdist；package-install 覆盖 RPH、Web 研究和 root/roles 的互斥安装规则。
- **退出**：干净目录仅从制品安装后，`rph doctor`、ACP CLI、MCP、Workbench 均可启动；导入路径指向安装环境，无源码 PYTHONPATH。

### M5-02 单一 BOM、可取回制品与证据覆盖矩阵（G18/G19/G20）

- **Owner**：B（manifest/契约注册）+ REL（制品/BOM）+ INT（coverage/签收）；**规模**：L；**前置**：M0-03、M5-01、M4 全部。
- 建立 `acp2.release.bom.v1`：source_revision/tree/clean、持久 ref/tag、harness/evidence revisions、contract digest、组件 artifact URI+sha256+size、构建脚本 digest、依赖锁、DSH/Node/pnpm/Chromium、科学软件与配置摘要。发布前由 B 将 release/bom 与 release/evidence-coverage 以既有最终发行阶段 **B26** 加入 `DELIVERED_IDS_BY_STAGE`、更新文件级 manifest status 并提升 manifest `contract_version`；同步 `tests/test_acp2_runtime_contracts.py` 的 delivered_by 允许集与版本断言，禁止另造未注册阶段名。BOM 同时记录总 contract digest 与 coverage canonical bytes SHA-256，不能让 release schema 落在摘要之外。
- 组件至少覆盖 Python 三角色、root ACP、RPH 全部包、DSH external source/lock、前端静态资产、第三方插件 wheel、契约、模型资产和文档；不包含秘密。
- 制品不放 git 时，必须进入耐用 artifact store；提供按 BOM fetch 到空目录后重跑 digest/install checks 的工具。缺 bytes 或 URI 不可取回即不能签收。
- 复用并冻结 M0-01 的 `acp2.release.evidence-coverage.v1`，行覆盖 G01–G26 和主方案 H0–H7 required rows；每行记录实际 receipt、digest、candidate、evidence kind、结果、scope、逐场景 required_observations、impact_globs 和 reuse。纯分析插件与 worker-hosted 第三方 COMPUTE 必须分行，后者按 M2-05 的 DEFERRED/NOT_VERIFIED 口径不得被前者覆盖。交付状态只在 coverage 表达，contract manifest 记录契约事实，BOM 只引用 coverage digest，避免三处各写一套 delivered。
- 复用算法：完全同候选且 digest 一致为 exact；非影响 diff 且契约/工具链/制品一致才可 reused；否则 rerun_required。未重跑即 NOT_VERIFIED。
- 历史索引和 summary 保持原字节；新 BOM 用 `supersedes/reused_from` 引用。多个 SHA 本身不是失败，无法解释映射才是失败。
- **退出**：新增 `scripts/release/walk_bom.py --verify`：仅从 BOM 出发遍历组件、artifact URI、coverage 每个 required row 与 receipt，取回 bytes、重算 digest、检查 candidate/scope/evidence_kind/required_observations，并输出未闭合行；claims 与 coverage 机械计数一致。缺任何可定位证据或制品即失败，不靠人工签字代替 walker。

### M5-03 独立操作者验收与最终放行（G17/G18/G19）

- **Owner**：独立操作者 + INT；**规模**：L；**前置**：M5-02、M0-10。第二操作者与干净机器/等效隔离环境必须在 environment-readiness 中登记。
- 由未参与实现的独立操作者，在另一绝对目录及干净机器（或等效的干净隔离环境）中，仅按 BOM 和 runbook 安装；无 checkout、无源码 cwd/PYTHONPATH、无本机验收绝对路径。若只使用等效隔离环境，必须在回执说明隔离边界，不能改称已完成干净机器认证。
- 运行 doctor、默认 DSH 握手、P2 研究、人工审批、Local 支持场景、工件读取和恢复；按发行声明复验 LSF/DA/插件及 H6 跨领域/迁移恢复必需行。
- 核对 API-key scoped 部署、撤销、跨项目隔离、预算限制、日志无秘密；升级/回滚和历史只读按声明验证。
- 最终声明由 coverage 生成，明确 supported/recorded-only/NOT_VERIFIED/DEFERRED。任何 required 行 FAIL 或缺失即不放行完整 ACP2。
- 生产部署、systemd restart 和生产数据迁移是后续独立授权步骤，不属于本次验收自动动作。

---

## 10. 差距—任务—关闭证据追踪表

| Gap | H 映射 | 关闭任务 | 最低关闭证据（不能只用模块 fixture） |
|---|---|---|---|
| G01 | H2/H7 | M0-02、M0-08、M5-03 | client@2.2.0 默认握手 + 安装后握手 |
| G02 | H2/H4 | M0-09、M0-11、M0-12、M0-13（条件）、M1-01、M1-06、M2-01、M3-01、M4-01 | 真实 SDK/DSH 扩展路径 + 协商后 18 工具/权限负例 + 全 P2 调用 |
| G03 | H3 | M0-06、M0-11、M1-01、M2-02、M3-01 | 真实 Bridge→MCP→Controller prepare/read；digest/attempt 负例 |
| G04 | H3/H5 | M0-09、M0-12、M1-01、M2-02、M2-05、M4-02 | descriptor 三态 + 内置/第三方分析共用公开路径与 digest |
| G05 | H2/H4/H7 | M0-09、M0-13（条件）、M3-01～M3-04、M4-02 | 发布包 Supervisor 的真 DSH 首轮与反馈轮 |
| G06 | H7 | M0-10、M3-02、M5-01、M5-03 | 另一目录无源码 profile/doctor/scoped key |
| G07 | H4 | M1-03、M1-06、M3-04、M4-04 | CCCP 原件→唯一 adapter→RPH；缺失检查不升级 |
| G08 | H4 | M1-03、M3-04、M4-01/M4-04 | 全身份/摘要负例 + 正确归属真实证据 |
| G09 | H4/H6 | M1-02、M3-04、M4-01～M4-03 | 超一分钟、多崩溃点、跨进程同 TaskRef 不重复 |
| G10 | H4 | M0-11、M1-04、M3-03、M4-02 | 带电/开壳层、多输入、clarification、双摘要向量 |
| G11 | H4 | M1-06、M3-05、M4-02 | 真 Chromium 网络 + 平台 approval/TaskRef + 刷新/跨端 |
| G12 | H4/H6 | M0-11、M1-04、M2-03、M4-01 | Agent omit-all/budget-only 等真实入口负例 |
| G13 | H4/H6 | M0-07、M0-11、M1-04、M2-03、M2-04、M3-03、M4-01～M4-03 | 规划/QC 调用前限额、rescue/重启/真实 deadline + 历史 attempt 可信结算；能力矩阵一致 |
| G14 | H4/H6 | M1-02、M1-06、M2-01、M3-04、M4-01 | 空项目 writer→CAS→恢复，无 seed fixture |
| G15 | H4 | M1-05、M2-04、M4-01、M4-04 | ADR-008 + 生产 DAG adapter/完成资格/声明产物/双身份/额度/fencing/恢复 + 原生 Recipe |
| G16 | H5/H7 | M2-05、M3-06、M4-05、M5-03 | 生产纯分析安装/启用/执行 + 不改前端源码的新 capability + 真浏览器/历史只读；worker-hosted COMPUTE 独立 DEFERRED/NOT_VERIFIED |
| G17 | H7 | M0-10、M3-02、M5-01～M5-03 | 全组件制品、RPH bin、静态资产、独立安装 |
| G18 | H0–H7 | M0-03、M0-10、M1-05、M4-01～M4-06、M5-02/M5-03 | ADR-008 + 当前 BOM 的真实模型/科学/LSF/浏览器/跨领域/安装证据 |
| G19 | H0/H7 | M0-01、M0-03、M5-02/M5-03 | 持久候选 ref、可取回制品、coverage/reuse 机械复核 |
| G20 | H0–H7 | M0-02、M0-11、M0-12、M1 全部、M4-01、各 CI 门 | 真实生产者→消费者常驻门，五类证据明确分层 |
| G21 | H3/H4 | M0-05、M3-01、M4-01 | 跨操作/target/并发/新实例重试，无双调用/串结果 |
| G22 | H3/H4 | M0-04、M0-05、M4-01 | prepare→submit 真提交；重试完整 TaskRef；无假成功 |
| G23 | H3/H6 | M0-06、M4-01、M4-03 | HTTP/CLI/MCP 相同有效 JobSpec/应用层拒绝；真实 remote 约束 |
| G24 | H6 | M0-04、M4-01、M4-03 | 同项目两用户不串任务（显式 conflict/隔离）+ admission principal + 同用户 replay |
| G25 | H3/H6 | M0-05、M4-01、M4-03 | 活跃会话撤销后写/控制/读一致拒绝且无副作用 |
| G26 | H4/H6 | M0-07、M2-03、M4-01～M4-03 | member 伪造拒绝、历史/当前回执上下文分离、执行身份绑定、purge 后可核验、重复回执幂等 |

**部分完成不得关闭整项。** 例如只修 budget-only 不能关闭 G12；只完成 Python wheel 不能关闭 G17；只完成 DAG 单测不能关闭 G15；只完成 offline Supervisor 不能关闭 G09。

---

## 11. 验证分层与 CI 触发

| 层级 | 内容 | 触发 | 能证明什么 |
|---|---|---|---|
| L0 静态/单元 | ruff、compileall、模块 pytest、RPH/frontend module、架构守护 | 每 PR | 局部回归，不证明跨端 |
| L1 契约/真实消费 | 真实 SDK、tools/list、Bridge↔normalizer、CCCP↔RPH、HTTP↔frontend | 每 PR，Python 3.11 + Node 22 | 接口可互消费 |
| L2 进程/持久/浏览器集成 | ASGI+SQLite、独立进程、崩溃恢复、Playwright；科学线程受控 | PR 关键子集 + main/nightly 全量 | 平台事实、恢复、UI 接线；不证明真实 QC |
| L3 安装/真模型 | 干净制品安装、doctor、真 DSH/模型（限额） | nightly 可选 + 每候选必需 | 发布入口和 Agent 行为 |
| L4 真科学/LSF | Local、DAG、DA、LSF、原生工件 | 候选/pre-release，显式环境授权 | 科学执行与集群事实 |
| L5 独立移交 | 另一操作者/目录/机器、BOM 取回与复验 | 最终候选 | 可交付与可复现 |

### PR 必须运行

先加载 M0-10 的脱敏环境清单；当前机器需显式 `export ACP2_TOOLS=/ACPServer/acp2-tools`、`export LSF_ENVDIR=/opt/openlava-2.0/etc`，将 Node22 bin、`/home/xieningke/xtb-dist/bin`、`/home/xieningke/crest` 与 `/opt/openlava-2.0/bin` 加入 PATH，并用 Corepack 激活 pnpm 11.28.5。默认 node=20.19、pnpm 未激活或 LSF 配置未加载必须前置失败，不能记为产品测试失败或静默换版本。

```bash
python3.11 -m pytest tests/ --strict-markers -m "not slow and not contract and not nightly and not pre_release" -q
python3.11 -m pytest tests/test_acp_services_isolation.py tests/test_architecture_invariants.py -q
python3.11 -m pytest tests/test_services_doc_sync.py tests/test_services_closure_record.py -q
node frontend/js/module_tests/run_all.js
pnpm --dir rph build
pnpm --dir rph test
pnpm --dir rph check
pnpm --dir rph package
python3.11 scripts/acceptance/validate_acp2_contracts.py
python3.11 scripts/acceptance/validate_acp2_gap_coverage.py
python3.11 scripts/acceptance/check_acp2_contract_mutations.py
python3.11 scripts/acceptance/check_execution_limits_consistency.py
```

新增跨端测试必须纳入上述 pytest/RPH 选择器或专门 `mcp-conformance` job；不能只存在文件而 CI 不执行。真实 SDK 依赖必须锁在 workspace，离线可安装。Ruff 固定为 `0.15.22`，ratchet 只在独立 hygiene job/PR 运行，历史全量诊断作趋势记录，不阻断本计划的差距修复门。专用交界门使用：

```bash
python3.11 -m pip install -e '.[dev,api,remote]' nmrglue
pnpm --dir rph install --frozen-lockfile
pnpm --dir rph build
python3.11 -m pytest tests/ --strict-markers -m contract -q
```

普通 Python 3.10/3.11/3.12 矩阵明确排除 `contract`，专用 job 不得排除或 skip；M5-02 的机械计数复用 `validate_acp2_gap_coverage.py`，不再另写状态计算器。候选发行门另运行 `python3.11 scripts/release/walk_bom.py --bom <candidate>/bom.json --verify`，只有真实遍历通过才可签收。

### 领域回归（按影响运行）

```bash
python3.11 -m pytest tests/test_acp2_runtime_mcp_process.py tests/test_acp2_runtime_mcp_backends.py -q
python3.11 -m pytest tests/test_acp2_runtime_multitenant.py tests/test_acp2_runtime_persistent_idempotency.py -q
python3.11 -m pytest tests/test_acp2_runtime_approvals.py tests/test_acp2_runtime_budget.py tests/test_acp2_runtime_admission.py -q
python3.11 -m pytest tests/test_acp2_runtime_science_binding.py tests/test_acp2_science_recipe.py -q
python3.11 -m pytest tests/test_acp_job_edit.py tests/test_scheduler_markers_audit.py -q
python3.11 scripts/check_grep_gates.py
```

真实验收命令使用各场景既有 harness；新场景必须先注册场景 ID、schema、evidence kind、超时、清理和 NOT_VERIFIED 原因，再运行。`--help`、`--dry-run`、`--check-only`、进程正常退出或测试 skip 均不是场景 PASS。

---

## 12. 阶段退出与停止条件

| 阶段 | 可以进入下一阶段 | 必须停止/返修 |
|---|---|---|
| M0 | 现有入口安全反例通过，M0-required 门全绿；M0-09 路径或外部变更/scope-change 已决策，M0-11/M0-12 无未决选择，M0-10 环境缺口已登记 | 仍有安全反例、旧派生键重复提交、未决 wire/兼容选择、无环境决策，或把未启用的门写成已通过 |
| M1 | 契约/多摘要/身份/预算冻结且 M1-06 Candidate-Contracts 真实薄纵切通过 | 两侧各自改常量；planned 被 declared delivered；直到 M4 才首次组合真实组件 |
| M2 | 空项目平台研究链与生产 DAG 可调用 | 只有 store/read、空 registry、STATIC/SIMULATED 或测试调用点 |
| M3 | product 组合根可启动，恢复/证据/Workbench 事实闭合 | fixture planner 静默 fallback、单 wait 终态、本地 approved/submitted |
| M4 | M0-10 必需环境实际就绪，Candidate-P2 的真实模型/科学/LSF/浏览器 required 行闭合；M4-06a 已提前完成迁移演练 | 以计划外账号/授权、fixture/live 标签、历史 SHA、截图或自然语言替代原生证据 |
| M5 | Candidate-Full 的 BOM/制品/证据经 walker 可取回，独立操作者复验 | 缺 RPH/静态资产/root、缺 bytes、只有本机绝对路径而无法移交、未验证被写 PASS |

---

## 13. 主要风险与应对

| 风险 | 应对 |
|---|---|
| 修契约时再次产生第三份 DTO/摘要/指纹 | 单一 owner、生成快照、真实消费者门；step 指纹委托 CCCP，提交摘要归平台 |
| 为兼容保留旧缓存而继续绕权 | 默认删除，不保留 feature flag；持久 Controller 幂等承担正确性 |
| Agent 审批破坏 manual legacy | 用 credential policy 区分；local-single/human 明示兼容并记录 submit_class |
| v1 extra=forbid 破坏既有扩展字段 | M0-11 客户端清单 + 422 unknown_field + 发布说明；legacy allowlist 每字段有退出日期/版本，下一 major 必删，不能永久双轨 |
| 三个 SQLite 库无法原子提交 | 本地事务 + outbox + 幂等 Saga + 对账；不宣称 2PC，不删除历史补偿记录 |
| DAG 静默丢 step 或误复用 | 未映射 fail-closed、CCCP step 身份单源、必需 step 未成功整体不 completed |
| 真实模型成本或输出不稳定 | 调用前 model_request 预算、typed output、确定性科学 validator、真实模型只在受控候选环境 |
| P3 表单把描述 Schema 当 JSON Schema | 独立受限 form 文档；未知关键字 fail-closed；服务端 preflight 仍为权威 |
| 测试通过但生产未接线 | 每个任务必须给生产组合根调用点和跨端证据，测试调用不计 |
| 证据标签污染 | 实际观察决定 kind，内外 receipt 交叉校验；fixture 不进入 live 证据 |
| 历史制品/SHA 不可取回 | 持久候选 ref + 外部 artifact store + digest fetch/check；历史回执不改写 |
| 范围逐步缩水 | 默认 recorded-only/deferred 明示；完整 ACP2 的所有 required 行未闭合即不放行 |

---

## 14. 立即开工顺序

1. **INT**：执行 M0-01，建立干净集成分支和 coverage；同时启动 M0-02/M0-03 与 M0-10 的环境授权，不能把账号和干净机器留到 M4 才寻找。
2. **B1**：M0-04→M0-05→M0-06 在同一身份/MCP 分支串行；M0-08 可独立，M0-07 复用身份成果。**B2/B3/B4** 分别推进研究/审批契约、DAG/插件预研、CI/BOM/决策清单，按 §2.2 限流，不把 B 当单人并行标签。
3. **C-RPH**：在 M0-05 删除 Bridge 结果缓存；与 B1 在 M0-08 后完成 M0-09；准备 M1-02/M1-03 消费者反例，不先写新的本地研究数据库。
4. **A**：准备 M1-03/M1-04 的 CCCP evidence、科学输入与 DA 判据；核查 DAG step 身份和不可映射步骤，并与 B3 为 M2-05 选定插件权威。
5. **C-Web**：准备真实 HTTP response→frontend validator 门和 M1-06 最小 consumer；完整审批 UI 等待 M1-04 契约。
6. **REL**：建立 BOM/artifact-store/coverage 骨架和 RPH package CI，并落实独立操作者/干净环境，不等待最终候选。
7. M0-11 后先启动 M1-02/M1-04，M0-12 后启动 M1-01；M1-06 形成 Candidate-Contracts 后再扩 M2/M3。真实模型必须在 M0-10 授权和 M3-03/M3-04 边界完成后才能进入 product 模式。

---

## 15. 本计划不声称的事项

- 本计划本身不关闭任何差距，也不改变现有“不放行完整 ACP2”的判断。
- 规划期间复跑的模块/定向测试只证明其声明范围，且未生成本候选的正式验收回执，不能替代上述跨端和真实场景。
- 未对生产实例、真实模型、QC/LSF 或独立机器作新的认证。
- 最终科学结论可以是 supported、refuted、inconclusive 或 not_evaluated；交付验收要求的是诚实、完整、可追溯的执行与证据，不是预设化学结论。

---

## 16. 独立审查 R01–R12 合并记录

本次判断依据为审查报告、其 `plan-review-20261026` 证据和当前源码复核。12 项均采纳其合理内核；下表同时记录为避免新缺陷而做的范围修正。它们不是已完成事项，仍随所属 M/G 行保持 OPEN。

| 审查项 | 采纳结果 | 并入位置与关键修正 |
|---|---|---|
| R01 dispatch 与完成判定 | 采纳并强化 | M2-04a/M4-01：单参 adapter、复用 worker 真值表、声明产物映射；另补多步 blocked 与 diagnostic-only 不释放正常下游，不只测错误成功。 |
| R02 QC 计数粒度 | 采纳并强化 | D09/M1-04/M2-03/M2-04c：新建 CCCP worst_case_executions 与 TaskContext meter，配合最坏预留和逐后端取额度；删除 `+len(steps)` 隐式 allowance，rescue 不删减。两层未完成前 qc_invocation 不得标 enforced。 |
| R03 walltime/deadline | 采纳并校正 | D09/M1-04/M2-03/M4-03：真实类型为 ResourceBounds/ExecutableBound；补当前 enforced 假阳性拆分、cluster 空默认无限时、真实终止与迟到成功负例。 |
| R04 多摘要与 step 身份 | 采纳 | D06/M1-04/M2-04b：envelope 只存定义身份，运行时在 step_result/DagRunState/执行链保存，禁止回写冻结体或占位 digest。 |
| R05 DAG 中间写执行权 | 采纳并强化 | M2-04c/M3-04/M4-01/M4-03：新建 FencedCommitPort + staging 原子提交 + control probe；现有 execution_right 只有 acquire/verify，不能当现成 commit 端口，DAG state 带 attempt/owner/fencing。 |
| R06 历史 usage 上下文 | 采纳并强化 | M0-07/M2-03：current-state 与 historical-usage 分离；入账时持久执行身份，purge 后仍可核验，迟到用量不推进状态。 |
| R07 插件生产装配 | 采纳并限定范围 | 新 M2-05、M3-06/M4-05/M5：不新增第五注册表，首个纯分析插件；computed distribution digest；worker-hosted COMPUTE 独立 DEFERRED/NOT_VERIFIED，不由纯分析关闭。 |
| R08 首次规划额度 | 采纳并强化 | M1-02/M1-04/M2-03/M3-03：proposal/TaskRef 前的 planning budget；非空稳定 claim/receipt，unknown 保持 reserved，不伪造成 job。 |
| R09 旧身份与索引迁移 | 采纳并强化 | M0-04/M0-05/M0-06/M4-06：增加旧 MCP `auto_` 派生键兼容、request digest 版本、prepare v1/v2；归属不明只对账不换 key 盲提。 |
| R10 live 判据 | 采纳 | M0-03/M5-02：逐场景 required-observations 及 present/absent；预算拒绝的零模型调用是有效 live 负例。 |
| R11 Ruff 历史债 | 采纳，选择 ratchet | M0-02/§11：固定 Ruff 0.15.22，基线只缩不增、修改范围严格、acp_services 零违规；不做无关全库格式化，不放宽架构守护。 |
| R12 DSH 扩展协商 | 采纳并强化 | 新 M0-09、M1-01/M3-01：M1 前验证真实 DSH/SDK；initialize 顶层 extensions 会被丢弃，必须选择 capabilities.extensions 或应用级握手。 |

**未扩大为新工程的部分**：不增加插件商城/GUI、通用 JSON Schema 求值器、通用分布式事务框架、第二身份/预算/任务权威、默认 token/core-hours 硬限制或全库 lint 清理运动。所有新增实现仍须按原任务的生产调用点和分层证据验收。

R01–R12 的逐项事实核对、三类表述限定（Ruff 计数、行号引用、已消解的 `ACR`）与随之补入 M0-02c/M1-04a/M2-04a/M2-04b/M2-04c 的细节见 §19；采纳范围与节点数不变。

---

## 17. 冻结前六项修订记录

针对后续“环境、纵切、并发、未决冻结项、任务粒度、机械门”的审查，六项均采纳；具体选择如下，不新增 G27+。

| 修订 | 采纳结果 | 已冻结的计划表达 |
|---|---|---|
| 环境/授权任务化 | 采纳 | M0-10 登记模型/QC/LSF/Chromium/第二操作者/干净环境与授权；M4/M5 硬前置，缺项触发 scope-change 或 NOT_VERIFIED。 |
| 早期纵切与候选 | 采纳 | M1-06 提前组合真实 ResearchStore/Controller/MCP/Bridge/Supervisor/frontend consumer；Candidate-Contracts、P2、Full 分级，不再全有或全无。 |
| B 单点与关键链 | 采纳 | B1–B4 分轨，M0-04→05→06 明确串行；每轨/每人同时最多一个 XL，一人兼任即承认单线程；保留关键链说明。 |
| M0-09 失败与未决选择 | 采纳 | path_selected/external_change_required/scope_change 三分支；M0-11/M0-12 分闸冻结身份、字段、状态、命名空间与集成策略，M0-13 承接外部变更；Ruff 独立 hygiene 流。 |
| 大任务拆分 | 采纳 | M0-02/M1-01/M1-04 拆 a/b/c，M2-04 拆 a–d，M4-06a 提前迁移，M4-06b 后做跨领域；每点独立 coverage 退出。 |
| 机械门与 v1 兼容 | 采纳并做明确选择 | mutation、execution_limits diff、BOM walker 三个脚本；v1 采用 extra=forbid + HTTP 422 unknown_field，附客户端/legacy allowlist/发布说明，不采用静默 warn 过渡。 |

编辑级同步：明确 `@modelcontextprotocol/client` 为新增锁定依赖、Bridge in-flight 为 `result: null`、现网 `-32602` 与前瞻 `-32022` 的区别；Ruff 基线只在独立清理或升级 PR 重生成；波次图仅表示验收扇入，不构成所有 M2 任务的统一开工屏障。

---

## 18. 可执行性复核的追加处置

| 审查点 | 处置 | 当前计划位置 |
|---|---|---|
| commit/meter/resource precheck 并非现成能力 | 采纳，改为显式新建工作项 | M2-04c 新建 FencedCommitPort、CCCP worst_case_executions/TaskContext meter、ExecutionResourcePrecheck；c1/c2/c3 独立 coverage。 |
| M0-07 诊断过期 | 采纳 | 主修 `admission.record_usage_once` 的来源与执行身份，不以接通 UsageIngestChannel 为目标。 |
| 关键路径不可复现 | 采纳 | M0-01 生成 plan-graph.json；默认 19/6 XL，外部 DSH 变更路径 20/6 XL，含区间和 M5 最终关闭约束。 |
| scope 与 H0–H7 不自洽 | 采纳 | §0.2 唯一 scope 初表、M0-01 scope.json、§10 G→H 映射和外部 H 文档 digest。 |
| M1-05 阻塞全部 DAG | 采纳 | 仅 M2-04d 的 DA 公开入口依赖 ADR-008，a/b/c 提前。 |
| G01/G07 关闭动作遗漏 | 采纳 | M0-08 补齐 process 测试、probe、mcp-handshake F1、B-req-18；M1-03 统一 research/memory.ts 的 EvidenceView。 |
| 检查点编号与 DoD 成本 | 采纳 | 定义 M2-01a/M3-01a/M3-05a，M2-01/M2-03 统一 a/b/c；§2.4 中间三点、G/候选完整 DoD。 |
| v1 硬切成本 | 保留选择并承担代价 | 仍选 extra=forbid + 422，但 M0-11 要求逐字段 allowlist 退出条件和下一 major 删除，不永久双轨。 |
| Ruff 与差距关闭无关 | 采纳 | M0-02c/M0-12 独立 hygiene 流，移出 required 主链。 |
| M0-09 外部变更无任务 | 采纳 | 新增条件任务 M0-13；默认 NOT_APPLICABLE，选择 external_change_required 才成为 M1 前置。 |
| 成本/pivot 缺失 | 采纳 | M1-06 记录实际消耗、剩余关键链和批准容量，作 go/no-go/缩窄/停止决策。 |
| 可观测性与恶意内容缺失 | 采纳 | §2.5 结构化日志/指标；M1-06/M4-01/M4-02 恶意工件、审批/预算伪造与外来证据负例。 |

不新增吞吐 SLA、插件商城、通用 Schema 求值器或第二执行/身份系统；这些不属于关闭现有差距的必要条件。

---

## 19. 审查报告 R01–R12 独立复核（2026-10-26）

**对象**：[EXECUTION_PLAN_REVIEW.md](EXECUTION_PLAN_REVIEW.md) 12 项意见及其 `evidence/plan-review-20261026/`。**基线**：`0d739b2`（源码等价 `ec4a0b3`，见 §0.1）。**方法**：逐项回到源码符号、生产调用点和证据文件核对，不采信报告自述结论；探针只读复核，未调用真实 QC、付费模型、LSF 或生产 run_root，未生成任何新验收回执。

**结论**：R01–R12 的事实前提全部成立，没有需要整体驳回的条目；§16 的采纳口径与范围修正维持不变，各 R 项仍随所属 M/G 行保持 OPEN。需要处理的是三类表述（历史计数、行号引用、已废弃名词）和六处执行细节，均不改任务编号、依赖或节点数（仍 39 项）。

### 19.1 事实核对

锚点一律给“文件::符号”，行号仅作参考（审查报告自身已证明行号会漂移：DSH `connection.ts` 由 258 行变为 261 行）。

| 审查项 | 复核结果 | 源码/证据锚点 |
|---|---|---|
| R01 dispatch 与完成判定 | 成立 | `acp_services/runtime/step_dag.py::DispatchFn` 为单参 `Callable[[StepDispatchContext], StepOutcome]`；`::cccp_compute_dispatch(context, request, *, task_context=None)` 需第二参；loop 在 dispatch 正常返回后无条件 `StepStatus.COMPLETED`；adapter 同时产出 `output_digests={"result": digest}` 与完整 `publish_payload`，下游按声明 product_kind 取 digest 会失配。对照真值表：`runtime/worker.py::decide_task_result` + `TASK_COMPLETION_AUTHORITIES`。证据 `assumptions.json` → `probe/dag_task_result_semantics`、`probe/direct_dispatch_signature`。 |
| R02 QC 计数粒度 | 成立 | `StepSpec.qc_invocations` 默认 1，`_reserve_budget` 按声明扣减，`BudgetCaps` 默认把 `sum(step.qc_invocations) + len(dag.steps)` 当额度；`cccp/calculation/tasks/optimize.py::run_optimize` 首次 `_run_attempt` 后还有 `max_rescue`（默认 2）次 rescue 调用 → 1 个 step 计 1、实际 3 次后端调用。证据 `probe/qc_budget_granularity`。 |
| R03 walltime/deadline | 成立 | `local_admission.py::request_for_spec` 把 `resources.walltime_s` 折成 `AdmissionRequest.duration_s`（申请值，不终止进程）；`queue_wait_s`/`science_runtime_s` 仅出现在 `research_budget.py` 的解析与默认表；`scheduler/remote/script_gen.py::derive_lsf_resources` 原样返回形参 `walltime`，其生产实参来自 `runner.py` 的 `self._config.walltime`，不读 `spec.resources.walltime_s`。证据 `probe/lsf_walltime_mapping`。`admission.py::ResourceBounds`、`metering_policy.py::ExecutableBound` 均存在，因此 §16 的“真实类型”表述成立。 |
| R04 多摘要与 step 身份 | 成立 | 四个域实测互异：提交/审批计划摘要 `sha256:`（`canonical_digest.py::plan_digest`）、冻结体 `v2:`（`worker.py::frozen_plan_body_digest`）、Recipe 定义摘要、step 执行身份（`recipes/compile.py::materialize_step_identity`，缺上游抛 `UnresolvedOutputRefError`）。`attempt_freeze.py` 用 envelope `step_identities` 与冻结身份的相等做校验，`receipts.py::verify_receipt` 又要求 receipt 与 envelope 的 `step_identities` 相等。证据 `probe/digest_domains`、`probe/late_bound_step_identity`。 |
| R05 DAG 中间写执行权 | 成立（设计风险，未复现生产竞态） | `worker.py::FrozenWorker.execute` 顺序为 `executor.execute(scope)` → `before_write()` → 写 task_result/receipt；执行权只在最终写前复验，executor 内部 checkpoint/`PublishFn` 无 fencing 接口。`execution_right.py` 仅 `acquire/verify`（`AlwaysHeldExecutionRight` 为过渡实现）。 |
| R06 历史 usage 上下文 | 成立 | `receipts.py::verify_receipt` 同时绑定 current attempt/submission/release/owner/fencing，attempt 递增后同一 receipt 判 `stale_attempt`；`metering_decoupling.py::UsageIngestChannel` 与科学/发布状态解耦。证据 `probe/receipt_verifier_scope`。 |
| R07 插件生产装配 | 成立 | `cccp/plugin/host.py::PluginHost` 是可复用库；`src/acp/**` 与 `src/acp_services/**` 无 `PluginHost`/`cccp.plugin` 引用；`api/v2_science.py::_capability_descriptors` 只用 builtin/domain catalog 并缓存到 app.state。 |
| R08 首次规划额度 | 成立 | `dsh-bundle/skills/supervisor.ts` 阶段序为 understand→hypothesize→critique→plan→approve（审批在规划之后）；`local_admission.py::reserve_budget_for_claim` 以 claim/task_ref 为中心。 |
| R09 旧身份与索引迁移 | 成立 | `store/store.py::claim_idempotency` 保留跨 scope 同 action/key 防抢占守卫；旧 local scope 已建 claim 后换真实 principal 同 key 得 `conflict`。修复身份后必须配套迁移，不能只对空库正确。 |
| R10 live 判据 | 成立 | `scripts/acceptance/acp2_experience_scenario_research_loop_live.py` 以 `live_model_calls == 0` 判非 live；该规则对无模型场景（HTTP/MCP、浏览器、LSF、制品安装、预算拒绝负例）过宽。 |
| R11 Ruff 历史债 | 成立，计数需限定 | 由报告原始证据复核：`ruff-check.json` 1,065 条 / 130 文件（E501 432、UP045 148、W293 142、UP006 128…），`ruff-format.log` 182 待格式化 / 940 已符合，与报告一致；但 `ci.yml` 头注仍写 ~678 errors / 63 文件，两套数字互不可比。 |
| R12 DSH 扩展协商 | 成立 | 外部 `deepseek-harness/packages/mcp/mcp-client/src/connection.ts` 创建 Client 时 `capabilities: {}`，无 ACP 私有扩展配置；行号已由 258 漂至 261。 |

附加核对：报告 §5 对 32 个任务的逐项意见与当前 39 项计划无冲突；其提出的新增工作（R07 生产装配、R12 前置验证）已分别落在 M2-05 与 M0-09，未产生新的 G 编号，也未把任何“已有组件”当现成能力。

### 19.2 需要限定或已消解的三类表述

1. **Ruff 计数不可跨版本/跨树互认。** 报告 1,065/130、`ci.yml` ~678/63、182 待格式化三套数字来自不同 ruff 版本与代码树，任何一套都不能直接当基线或关闭门；M0-02c 已改为“固定 0.15.22、在集成基线重新生成并随回执记录命令/版本/配置 digest”，历史数字只作趋势参考。
2. **行号引用会漂移。** 报告与本文均出现过行号；执行期一律按“任务 ID + 标题 + 计划 SHA + 符号名”定位（§2.3、M0-01 已冻结该规则），复核不得以行号不一致判定源码已变。
3. **R08 的“ACR”名词已消解。** 报告要求把 M3-03 中未定义的 `ACR` 改成现有组件名；当前计划全文已无 `ACR` 命中，该项视为已满足，不新增工作，也不再保留占位名词。

### 19.3 本轮补入的执行细节（不改编号与依赖）

| 位置 | 补入内容 | 对应审查项 |
|---|---|---|
| M0-02c | 基线来源唯一：集成基线 + 固定 0.15.22 重新生成，回执记录命令/版本/配置 digest；三套历史计数互不可比 | R11 |
| M1-04a | 三类时钟写入同一契约并规定优先关系（排队期仅 queue deadline；启动后 wall deadline 终止；science runtime 仅记录），客户端不得择一绕过 | R03 |
| M1-04a | D06 每个摘要域登记 owner、canonical 输入与字段序、版本、唯一校验实现与旧 wire 兼容映射；无校验者不得标 frozen | R04 |
| M2-04a | step 打包清单补齐 output_dir、方法/资源/软件配置解析结果、输入角色（`structure_role`/多输入）、capability 与 provider 版本；禁止原样交计划 dict 或让 runner 猜默认 | R01 |
| M2-04b | 审批 `plan_digest`（`sha256:`）与冻结体 `frozen_plan_body_digest`（`v2:`）分别校验、不得互相代偿；收养 DagRunState 前核对 plan/输入/上游产物 | R04 |
| M2-04c 测试 | 回执丢失或重启不得重新获得未扣减额度；并发与部分失败不突破授权额度；审批摘要与冻结体摘要分别变化各自被拒 | R02、R04 |

### 19.4 复核限制与可直接复用的证据

- 计数与探针证据直接取自报告原始文件（`ruff-check.json`、`assumptions.json`），未在本机重跑：当前会话环境未安装 `ruff`，且 `python3 -m pytest` 因未安装本仓包（`acp` 不可导入）在 conftest 即失败，故 71 passed 只引用报告 JUnit，未生成本会话的新运行证据。
- 报告的可运行命令保持有效；本复核不产生新候选、不移交任何 PASS，也不改变“完整 ACP2 不放行”的既有判定。
- 复核结论只覆盖“审查意见是否与当前源码相符”，不覆盖真实科学、真实模型、LSF 与独立安装行；这些仍按 §11 分层与 M4/M5 执行。

---

## 20. 库状态独立评估处置（PLAN_ASSESSMENT_20261026）

| 评估点 | 处置 | 计划位置 |
|---|---|---|
| `auto_` 与 `op_` 命名空间 | 采纳纠正 | M0-05/M0-11/M4-06：`auto_` 是旧 MCP 派生幂等键，`op_` 仅 operation reference；fixture 必须由旧代码生成。 |
| ReservationPort/adapter/research.init | 采纳纠正 | M0-04 仅一个端口实现、两个入口；M2-04a 分验 output_digests 与 publish_payload；M3-05 保留已有 read init，只补 commands/load。 |
| ACP primitives 发布逻辑 | 采纳并限定 | M2-04d 保留 frequency/normal_modes 等 ACP 发布契约与 forwarder，明确 seam/回滚，不把“任务核心唯一”误读为“ACP 无发布逻辑”。 |
| 基线未推送与 worktree 分叉 | 采纳 | M0-01 先 fetch/对账/新提交/授权推送/tag；旧 integration 不复用，KB 与脏证据逐项决定，不在当前树 reset。 |
| start/close 图语义混合 | 采纳 | M0-01 生成 start/close 双图；默认 start 16/3 XL、close 19/6 XL，外部变更 close 20/6 XL；现存 JSON 只作估算。 |
| 手工 plan-graph 与缺字段 | 采纳 | 现有文件标 ESTIMATE_ONLY_NOT_M0_01_DELIVERY，生成器通过后才覆盖；补 unknown_refs/cycles/XL_count 和子检查点校验。 |
| 当前环境天花板 | 以补测纠正原评估，不擅自永久缩窄 | M0-10 已补测 Openlava/xtb/crest/DSH 可用，缺的是 PATH/LSF_ENVDIR 配置、隔离提交授权、模型与第二操作者；不再以缺 LSF/xtb 判定 Full 不可达。 |
| M0-09 决策成本 | 采纳 | 规模升 L，优先应用级握手，spike 时间盒 2 天；M0-13 成本提前计入容量。 |
| 容量与 Team/Solo | 采纳为初始模型 | §2.6：110–190 人日分级估算，Team 3–5 月/Solo 6–12 月，M1-06 按真实速度重估；Solo 优先 Contracts+Local ORCA。 |
| 契约目录/客户端清单 | 采纳 | M0-01 glob↔manifest 双向检查；M0-11 机械扫描请求键，legacy allowlist 逐字段退出。 |
| RPH 禁网络 gate | 采纳 | M3-02 补 fetch/undici/node:http(s) 规则与负例。 |
| 重复读取与测试成本 | 采纳 | M4-01 断言重复 prepare/read 无写放大；M0-02 单 job 超 20 分钟分片，不 skip。 |
| wire-id 扫描例外 | 采纳更稳方案 | D05/M1-01 集中 `entrypoints/wire_identifiers.py` + 常量值 allowlist，不按行号/函数位置豁免。 |
| 计数与会话差异 | 采纳限定 | §0.1 区分不同复核会话；历史制品只说部分缺失/路径失配，不称全部不存在。 |

本评估只改变计划和估算，不直接执行证据还原、KB 丢弃、推送/tag、LSF/模型调用或 scope-change。

---

## 21. 环境补测与判断修正（2026-10-26）

前一轮评估把默认 PATH 中不可见误判为未安装。补测后修正如下，旧评估仅作历史输入，不再作为环境结论：

| 能力 | 实测状态 | 对执行判断的影响 |
|---|---|---|
| xTB | `/home/xieningke/xtb-dist/bin/xtb --version` 返回 6.7.1，normal termination | xTB 路线可进入环境就绪验证，不再 DEFERRED 因“未安装” |
| CREST | `/home/xieningke/crest/crest --version` 返回 3.0.2 | 构象/采样路线可进入就绪验证 |
| Openlava | `/opt/openlava-2.0`；`LSF_ENVDIR=/opt/openlava-2.0/etc` 时 `bsub -V` 返回 2.0；最小作业 `1767` 在 `normal/workstation` 完成（DONE），输出含 `Successfully completed` | 调度、执行、历史与原生输出链已实测；仍需 QC 作业、控制/取消、配额与发布恢复验收 |
| DSH | 本机 Node/npm 路径可运行 `@deepseek-ai/dsh` 0.2.1-rc.2 | M0-09 可基于真实安装执行，不再把 DSH 获取当阻塞 |

因此，“当前机器因缺 LSF/xtb 无法达到 Candidate-Full”的判断**撤回**。当前更准确的结论是：主要计算与调度底座存在，但环境 profile、隔离提交权限、模型授权、第二操作者、干净机器和真实端到端证据仍未完成；这些不再被误写成软件缺失。

---

## 22. 已确认的执行输入（非敏感）

| 项目 | 已确认输入 |
|---|---|
| 基线 | 从当前 0d739b2 建新分支，纳入当前源码、KB 和文本证据；现有 A/B/C/integration worktrees 不动，e2e-task-root 按基线恢复 |
| 制品与测试根 | 制品库 `/ACPServer/acp2-artifacts/remediation`；`ACP_RUN_ROOT=/ACPServer/acp2-runs/remediation`；Controller `127.0.0.1:18765`；生产路径与 8765 不使用 |
| 执行形态 | Team 多车道，按 B1/B2/B3/B4 + A/C/INT/REL 推进 |
| Openlava | queue=`normal`，target=`workstation`，node_tags 为空；job 1767 已完成（DONE） |
| 模型 | `deepseek-official/deepseek-flash` 一次最小调用成功；只保留结果，不保存凭据，暴露凭据需轮换 |
| 独立环境 | 一台 ARM64 干净机器可 SSH，允许在 ACPtest home 用户级安装 Python/Node/pnpm，不使用 sudo；最终独立 reviewer 待指定 |
| 安全边界 | 不把任何 API key、SSH 密码或 secret 写入计划、BOM、日志或证据；当前对话中出现过的凭据不进入 git |

这些输入允许开始 M0 实施，不等于 Candidate-P2/Full 已通过。后续真实 QC、模型研究轮次和独立安装仍按对应门验收。
