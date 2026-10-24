# acp-parallel-plan-2-services - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**Who this is for and what changes for them:** 服务于把平台从"单机脚本工具"推进为"有身份、有数据角色、有执行隔离的服务平台"的团队：协调者拿到一份可逐步执行的迁移计划，前端与科学两路可按冻结接口并行推进，现有科研用户的任务、历史结果与操作流程全程不受影响。

**What you'll get:** 一份按依赖排序、分 14 个阶段（+14b）、78 项任务的工作计划：先固化基线与首片接口契约，再依次落地工作空间（身份与授权）、数据（存储/传输/准备与缓存）、任务运行（生命周期与执行协议）三个职责，随后统一命令行与初始化、机器接口与项目包、实验室多用户与核时配额；收费与多机扩展保留在明确门槛之后。

**Why this approach:** 每一步都"原地迁移、单一实现、旧入口转发"，并用现有架构护栏、跨版本恢复样本和端到端故障测试钉住行为；后置能力用显式门槛阻断，避免在依赖未就绪时假装完成。

**What it will NOT do:** 不重建已退役功能、不复制第二套科学或平台实现；不自行制定价格与收费规则；不把未验证的多用户/托管能力宣传为可用。

**Effort:** XL
**Risk:** High - 跨层平台迁移 + 三路并行协作 + 真实环境验收门（软件/LSF/生产如实标 NOT_VERIFIED）
**Decisions to sanity-check:** ① 全量 B00—B13 收敛为 14 个 wave 的单份计划，后置 wave（B06 切换/B07 完整发行/B08 启用/B09—B10/B11—B12）用显式 entry gate 阻断；② 随切片测试（先定向，再按范围非慢/compileall，真实环境三门状态）；③ 证据入 `.omo/evidence/acp-parallel-plan-2-services/`、需入库契约入 `docs/reference/next-change/`；④ 不自行制定价格/计量生产政策；⑤ 新包命名 `src/acp_services/`（不建 `acp_runtime`）。

Your next move: 本计划按用户要求经双通道高精度评审（momus + oracle）后交付；执行请开独立 worker 会话（`$ulw-execute acp-parallel-plan-2-services`）。执行不属于本规划会话。

---

> TL;DR (machine): XL / risk high; Plan_2 (B00–B13) → 14 waves (+14b), 78 todos + F1–F5; gated: B06 switch needs C01, full CLI needs B06/C08, B08→M2, B09/B10→M3, B11 price-policy, B12 demand+PoC; release (76) runs after F1–F4 approve; F5 checks post-release results; preserves execution invariants, 15 active workflows, edit coverage, frames/manifest, CAS/recovery.

## Scope
### Affected user and ideal state
**Affected user:** ① 项目协调者/服务路负责人——需要可交给 worker 会话逐 wave 执行的 decision-complete Services 计划；② 执行 worker（agent）——无面试上下文，需精确 references/验收/QA/commit；③ A 前端路——消费版本化 Access/Data/事件契约与样例，旧接口兼容窗口（F0＋S1）保持；④ C 科学路——按冻结契约对接（B06 切换 gate=C01；完整 CLI 发行门=B06+C08），不建第二科学根；⑤ 最终用户（研究者/实验室运维）——现有 15 active 工作流、CAS/恢复/目录/manifest/frames 行为不回归，新能力按门可用且不伪装完成。

| Row | Statement | Reason |
| --- | --- | --- |
| IS-1 | 存在覆盖 B00—B13 的 decision-complete 计划：wave 排序、依赖/gate、每 todo references+验收+QA+commit 齐全，worker 零再面试从 W1 开工 | 协作依赖 agent 执行，歧义会在执行期放大为返工 |
| IS-2 | 每个切片保持四项执行不变量、15 active 工作流、edit coverage、frames/manifest 契约与 CAS/恢复语义；护栏只允许"先登记修订+等效门"再切换 | 根 AGENTS EXECUTION PROTOCOL、主方案 §1.5/§14、并行索引 §7 |
| IS-3 | `src/acp_services/`（workspace/runtime/data/contracts/entrypoints/bootstrap/compatibility）成为唯一实现驻点；`src/acp` 同实现兼容入口；不复活 `acp_runtime`；新 Services 不动态转调旧 `acp.cli` | 并行计划二 §1/§4；主方案 §37.1/§10 |
| IS-4 | 输入/工件/manifest/来源/候选/传输/缓存唯一写入口经类型化 DataResourcePort；Runtime 只冻结/消费 StorageBinding；持久 prepare 取代进程内预取；旧 Client/GET 行为在兼容窗口内保持 | B02、P2-01—05；§38.6/38.7 双向兼容 |
| IS-5 | 查询只读已准备快照（无同步扫描/解析回退）；事件描述已提交事实（cursor/去重/快照重置），不是第二状态机；旧 payload 与恢复矩阵兼容 | B03/B04、P1-03/04 |
| IS-6 | 执行经 ExecutionEnvelope/Receipt：worker 只执行冻结 plan 并写回执、不连 jobs DB/不自增 attempt；controller 校验后推进；未知提交对账、无双派发、旧在途 release 接管 | B06、P4-03/04/05、执行不变量 #1 |
| IS-7 | 本地产品完整可用：`acp run`/`python -m acp` 与旧参数、init→doctor→首个科研闭环、无浏览器/Web/在线账本可完成本地摄入/计划/执行/控制/恢复/导出；缺软件 typed unavailable | B07、PR-02、§19.1 |
| IS-8 | 机器接口统一：JSON/退出码/错误 code/params/retryable；dry-run/estimate 不启动科学程序不耗核时；MCP 薄入口；归档包/证据投影/结构化日志落地且不泄露秘密 | B08、§32/33 |
| IS-9 | Lab 能力按档位可验证：开通/授权/隔离/配额/管理员/init/doctor；usage-only 与 quota-only 可独立交付（不依赖价格/Credit）；有可信采集证据 | B09/B10、MU-03/05/06/09/10a、OS-06 |
| IS-10 | 条件能力（Credit 账本、PostgreSQL/对象存储/Slurm/Podman/GPU、Hosted）只在显式门通过后推进；不自行制定价格/补偿规则；组件锁定 manifest 后才标已批准 | B11/B12、OS-04/05/10、§21/§28 |
| IS-11 | 验证分层并留证：每片定向测试→非慢/compileall→真实软件/LSF/浏览器/生产分别 PASS/FAIL/NOT_VERIFIED；台账逐项关闭；契约/AGENTS/部署/手册同步（发布前验收；发布结果另计，由 F5 核对） | B13、§14、并行索引 §7 |

| Row | Statement | Reason |
| --- | --- | --- |
| GAP-1 | 只有文档清单（B00—B13 checklist），无 worker 可执行计划（wave/依赖/references/验收） | 本计划即交付物；全部 todo 关闭 |
| GAP-2 | 不存在 `src/acp_services/`；Workspace/Runtime/Data 职责混在 src/acp（scheduler/api/calculations/init_wizard） | 6—10、11—18、25—29、30—34、35—41、72（过渡装配）、74（CLI）、77—78（独立性） 提取 todo 关闭 |
| GAP-3 | 无 principal/身份：API 无 auth 中间件/CORS，仅 trusted-network 注释（`v1_routes.py:5908/7000/7059`）；项目/成员 owner 未定 | 6—10、72—73 关闭 |
| GAP-4 | 无 DataResourcePort/持久 prepare：`_job_read_root` 同步 `fetch_catalog`（`v1_routes.py:379`、`v2_routes.py:232`）；预取为进程内队列（`manager.py:4709`） | 11—18 关闭 |
| GAP-5 | 投影存在 enrich 回退（`v2_routes.py:146`）；事件仅每任务 JSONL/SSE（`events.py:93`、`routes.py:375`），无统一授权流/cursor 重置协议；`task_views` 无 freshness | 19—24 关闭 |
| GAP-6 | Runtime 规则嵌在 manager/workflows/calculations，无归并用例与单一 owner 声明 | 25—29 关闭 |
| GAP-7 | 无 ExecutionEnvelope/Receipt；runner/script_gen 构造 `python -m acp.cli`（`script_gen.py:150`）；`script_gen.py:26` 控制端导入 `acp.catalog` | 30—34 关闭（切换 gate=C01） |
| GAP-8 | init 非"使用方式→档位→组件→计划/应用→doctor"；节点校验镜像（`flows.py:68-88` vs `remote/config.py:162-209`） | 35—41、74 关闭 |
| GAP-9 | 无机器可读输出契约/MCP/归档包/证据投影/结构化诊断导出 | 42—47 关闭 |
| GAP-10 | 无多用户身份/隔离/配额/用量/账本；无 StorageBinding/UsageRecord/CreditAccount 契约 | 12、48—65、73 关闭（含条件门） |
| GAP-11 | P0 基线证据不完整（如 `.omo/evidence/frontend-arch-20261016/` 缺失）；无 Services 符号台账/性能/备份恢复基线 | 1—5、66—71 关闭 |

### Must have
- `src/acp_services/` 包按需逐片创建（不预建空目录）：`contracts/`、`workspace/`、`data/`、`runtime/`、`entrypoints/`、`bootstrap/`、`compatibility/`；`src/acp` 旧路径同实现转发；依赖方向：`acp → acp_services` 允许，`acp_services → acp` 禁止（含懒加载/TYPE_CHECKING），新增守护测试；`pyproject.toml` include 加入 `acp_services*`。
- B00—B13 每个条目至少一个 todo；每个 todo 有 References（实测 file:line）、agent 可执行 Acceptance、happy+failure QA + Evidence、Commit、执行类别。
- 四项执行不变量、15 active 工作流、edit coverage、frames/VIEW_REGISTRY、CAS/恢复/发布顺序、目录/markers 契约全程保留；涉及护栏/契约的改动先登记修订+等效或更强检查。
- 每切片测试 + 适用 `pytest -m "not slow"`/compileall；真实软件/LSF/浏览器/生产三门状态如实记录。
- 证据与台账：`.omo/evidence/acp-parallel-plan-2-services/`；需 A/C 消费或长期生效的契约/说明落 `docs/reference/next-change/`（B 为集成写者）。
- 评审补强并入（X/B 修订）：依赖闭包与过渡装配（72）、身份生命周期（73）、完整 CLI 真实入口（74）、四组合联合验收（75）、终验后发布（76）；X1—X6 共同约束见 Execution strategy。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不重建 retired 工作流；不建第二科学任务根；不把科学算法复制进 Services 或前端。
- 不创建 `acp_runtime` 或任何第二实现根；新 Services 不动态转调旧 `acp.cli` 绕隔离；不保持两份逻辑（迁移即"唯一实现+旧路径转发"）。
- 不自行制定价格、暂停计费、失败补偿等生产账务政策（只交可配置契约+测试样例）；不宣传未验收的 Hosted/Credit/多租户能力。
- 禁止禁用/放宽/静默修改护栏（`test_architecture_invariants`、`test_f4_scope_audit`、grep gates、markers snapshot、recovery fixtures）换通过；不整目录机械搬迁 `src/acp`；不删公开 shim/历史 reader/模型资产/恢复 fixture。
- 不夹带行为变更：任务生命周期语义、目录布局契约、frames/VIEW_REGISTRY、科学数值政策如需变更须单独立切片+等效门。
- 不提前强拆发行包；不因评估清单把候选组件（PostgreSQL/对象存储/Slurm/Podman/GPU/Lago 等）加入默认依赖。
- 不改 `frontend/**` 与 `src/cccp/**` 实现（A/C 路）；混合文件只由 B 应用 C 的登记补丁；不双写/不对同时改全局配置。
- 不把本计划当执行授权：批准只授权写计划；执行走独立 worker 会话。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after-per-slice**（仓库既定纪律；非 TDD）。框架：pytest（`tests/` 平铺）+ 负例注入；真实二进制/LSF 用 `--run-slow/--run-integration` 与 `requires_*` 门控。
- 每 todo 收口命令（按范围择一）：`pytest <目标测试文件/node id> -q`；切片末 `pytest -m "not slow" -q`；适用时 `python -m compileall -q src`；新增/修改文件 `ruff check <paths> && ruff format --check <paths>`（不引入新债务，不做全仓机械格式化）。
- 契约/行为/恢复证据必须包含：命令、退出码、关键输出片段、`PASS/FAIL/NOT_VERIFIED` 标注（真实软件/集群/生产未跑即 NOT_VERIFIED，跳过不算通过）。
- 守护测试必须在受影响的切片跑通：`tests/test_architecture_invariants.py`、`tests/test_f4_scope_audit.py`、`tests/test_scheduler_markers_audit.py`、`tests/test_acp_job_edit.py`、`tests/test_cccp_isolation.py`、`tests/test_acp_frames.py`、`tests/test_result_publication.py`、`tests/test_execution_integrity_e2e.py`、`tests/test_checkpoint_resume_semantics.py`、`tests/test_recovery_fixtures_smoke.py`、`tests/test_recovery_cross_version.py`（按切片范围）。
- Evidence: `.omo/evidence/acp-parallel-plan-2-services/task-<N>-acp-parallel-plan-2-services.md`（out-of-loop 约定；若经 ulw-loop 执行则用 attemptDir）。需长期生效的契约文档：`docs/reference/next-change/ACP_Services_*`。
- 交付物版本化（评审 X5）：契约/写权/样例/交接状态/验收摘要必须受版本控制（tracked 路径，如 `tests/baseline/services-evidence/`、`docs/reference/next-change/`）；大日志可留在稳定工件位置并登记 URI/摘要/产生提交/环境/命令/退出码；不以".omo 路径存在"作为唯一验收。
- 环境隔离（评审 X6）：本路固定独立 worktree/venv/run_root/配置/端口与临时输出；集成另用一套环境并记录模块实际来源；开发切片只启动隔离测试服务；真实部署只在通过终验的集成 release 上执行（todo 76）。
- 范围状态（评审 X4）：条件项用 `IN_SCOPE / DEFERRED / NOT_APPLICABLE` 记录范围，证据只使用 PASS/FAIL/NOT_VERIFIED；延期不是通过；每个里程碑声明实际支持范围。
- 前端测试（评审 P1-4）：A 维护测试与提供 S0 证据；B 对选定集成提交执行 A 的黑盒测试（不取得 A 源码写权）；跨路科学判定以 C 输出为准。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **W1 = B00（todos 1—5，立即可执行）**：基线/场景/台账+首片契约/备份恢复样例/组件 manifest。1、2、5 可并行；3、4 需 1 先完成（3 产出的契约是 W2—W9 的输入）。
- **W2 = B01（6—10、72，立即可执行；72 先行）**：依赖闭包与过渡装配（72）、local principal 与授权接口、身份模型分层、Key 原语、LocalIdentity↔OIDC 映射契约、local-single 安全边界。
- **W3 = B02a（11—14，需 W1 契约 + todo 6）**：DataResourcePort、StorageBinding、持久 prepare、下载校验/发布。11 依赖 1—3、6；11→12→13→14 同包递进。
- **W4 = B02b（15—18，需 13；14→15 准备→16 兼容对拍+逐资源切换；18 需多租户需求 gate）**：统一读取根与只读面（15）、cache-only 兼容先行逐资源切换（16）、逐入口授权核查、租户路径扩展（gate）。
- **W5 = B03/B04（19—24，需 15）**：投影快照、freshness/revision、请求预算、事件协议、事件-状态顺序、授权流。
- **W6 = B05（25—29、77，需 15/19）**：Runtime 用例归并、CAS 等价、身份/恢复、发布入口、纯 helper 归并、过渡实现迁移收口。
- **W7 = B06（30—34、78，契约 30/31 可在 W1 后提前；32—34 切换需 C01 对应闭包、且 32 启用另需 78；78=worker 自足装配）**：Envelope/Receipt、worker 规则、整链切换、回执拒绝/对账、逐工作流启用、独立装配。
- **W8 = B07（35—41、74，35—38 依 W6；40a 需 C39a（可先行）、40b 需完整 C08；41 完整 CLI 发行门=32—34；74=完整 CLI 真实入口）**：CLI 等价、init 统一、纯校验、计划/应用/幂等、动作分离、角色打包（B40a/B40b）、Standalone 配方、完整 CLI 真实入口。
- **W9 = B08（42—47，随 M2：需 15/22/30）**：JSON/退出码、MCP、archive schema、import、证据投影、日志/诊断/i18n。
- **W10 = B09（48—52、73，gate=M3 进入；需 6—10/25—29/30—34；73=身份生命周期）**：开通、授权/预留、隔离、管理员、身份生命周期、Lab init/doctor。
- **W11 = B10（53—56，gate=M3 进入；需 30—34/48）**：核时政策冻结、UsageRecord、quota-only、解耦。
- **W12 = B11（57—61，gate=价格政策批准 + B10 通过）**：账本、持久预留闭环、并发/恢复验证、三入口同账、外部账单试点（条件）。
- **W13 = B12（62—65，gate=实际需求 + 逐组件 PoC）**：PostgreSQL、对象存储、Slurm/Podman/GPU、Hosted 组合门。
- **W14 = B13（66—71、75—76，随切片 + 收口；76 在 F1—F4 全 APPROVE 后执行，发布结果经 F5；77/78 分别在 W6/W7 收口）**：逐片测试纪律、故障覆盖、备份恢复、分层验收、四组合联合验收（75）、清理/文档、发布准备与终验后发布（71/76）。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1—5 (W1) | — | 6—78（契约/台账输入） | 相互并行 |
| 6—10、72 (W2) | 1—3 | 11+ | 72 先于 6；7/8/9/10 相互并行；10 需 6 |
| 11—14 (W3) | 1—3, 6 | 15+ | 递进（11→12→13→14） |
| 15—18 (W4) | 13 | 19+ | 14→15 准备→16 兼容对拍+逐资源切换；17 可并行；18 需 B09/B12 需求 gate |
| 19—24 (W5) | 15 | 25+ | 19—21 与 22—24 两条线；23 需 22 |
| 25—29、77 (W6) | 15、19、72 | 30+ | 25 先行；26—29 部分并行；77 收口注入迁移 |
| 30—34、78 (W7) | 1—3（30/31 可提前）；32 启用需 78；33—34 需 C01；78 需 31、77 | 33—34、41（完整 CLI 发行门） | 30/31 与 W2—W6 并行 |
| 35—41、74 (W8) | 25—29；39 需 36—38；41 需 32—34；74 需 35—38、32—34；B41b 需 74 | 42+ | 36—38 部分并行；40a 需 C39a；40b 需 C08 |
| 42—47 (W9) | 15、19、22、30、35（M2） | 48+ | 42—47 部分并行 |
| 48—52、73 (W10) | 6—10、12、25—29、30—34（M3）；73 需 48、51、36 | 53+ | 48→49；50/51 并行 |
| 53—56 (W11) | 30—34, 48 | 57+ | 53 先行 |
| 57—61 (W12) | 53—56 + 价格政策批准（61 另需真实需求） | — | gate 未过不开工 |
| 62—65 (W13) | 实际需求 + 逐组件 PoC；65 需实际启用范围（50、52、55—60） | — | gate 未过不开工 |
| 66—71、75—76 (W14) | 所选切片；75 需 16、24、46；69 需 66—68、75；76 需 71+F1—F4 全 APPROVE（发布结果经 F5） | — | 随切片持续 + 收口 |

> 外部决策门（未决项仅在对应 gate 需要用户裁决，不阻塞 W1—W9）：价格/计量生产政策（W12 前批准）、多租户需求与容量目标（W10/W13 前确认）、合规/保留要求（W10+ 前确认）。容量指标采用基线相对记录（todos 2/21），不设绝对目标。

### Common execution constraints（评审 §6.3，三路共同；本路适用）
> 1. 实施以共同基线和唯一写权登记为准；所有跨路输入均记录契约版本、提供提交、样例摘要和应用回执。
> 2. 一个可合入切片必须同时具备唯一实现、旧入口兼容、消费者调整、依赖/资产/护栏同步；未完成的跨路补丁不能算该切片已集成。
> 3. 临时端口注入可以支持迁移，但不证明独立运行；独立性用无旧实现环境和实际安装产物验证（todo 72/76）。
> 4. 新查询不创建 prepare；旧接口的过渡行为明确登记；切换前必须验证冻结旧客户端（todo 15/16）。
> 5. 任务状态、事件、工件发布、预算预留的各个持久化边界都有崩溃与重放验收（todo 23/67）。
> 6. PASS、FAIL、NOT_VERIFIED 不互相替代；范围延期单独记录（IN_SCOPE / DEFERRED / NOT_APPLICABLE）；实际合并版本重新验证，不拼接不同提交的通过记录。
> 7. 三路各用独立 worktree/venv/run_root/配置/端口与数据；真实部署集中在通过终验的集成 release 上执行（todo 76）。
> 8. 每个里程碑声明实际支持范围；未进入范围的能力保持开放，不阻塞已满足要求的独立交付，也不计作完成。

### Milestone scope（M1/M2/M3 所选任务、证据与延期；评审 P1-3）
- **M1（本地完整闭环）**：todos 1—5（基线/契约）、15—17（读取/兼容；**T18 多租户路径=条件任务，未选时记 DEFERRED**）、25—29+77（Runtime/收口）、30—34 首个闭包（T34a）+78（执行/独立装配）、35—38、74（真实 CLI）、36—41 的 init 与 B41a 配方设计、72（过渡装配）；跨路最小发行（C08 协同）。**必需证据**：安装/init/doctor/首个本地科研闭环 + 真实 CLI 全流程（74）+ 所承诺插件样板（跨路 C09）。**明确延期**：未选 NMR、完整 15 项覆盖（T34b）、大数据产品、账户/配额 UI、价格审批与后置组件。
- **M2（第二主线/可调用）**：42—47（机器接口/项目包/证据）、75（四组合联合验收）、第二主场景（Confsearch→候选→BatchOptimize）。
- **M3（实验室协作）**：48—56、73（身份/隔离/配额/核时）。
- **M4（按需）**：57—65（条件门；不因 ALL todos 自动进入收费/Hosted）。
最终门只检查本次声明范围；条件项不适用仍保留后续工作。

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE -->

### Wave 1 — B00 基线与首片契约（立即可执行）

- [x] 1. B00-1 冻结基线：revision/dirty、依赖/科学软件/解析器版本、配置来源、入口清单、现有测试证据，并接收 A00/C00 地图
  What to do / Must NOT do: 采集并写入 `.omo/evidence/acp-parallel-plan-2-services/b00-baseline.md`：(a) `git rev-parse HEAD` + `git status --porcelain`（起点应为 `main @ f410ac9dae035eb424c40c599692500fdea457cc`，不同则登记实际值）；(b) `python -V` + `pip list --format=freeze`（numpy/rdkit/scipy/matplotlib/openpyxl/nmrglue/paramiko/fastapi/uvicorn/pyyaml）；(c) 科学软件发现：orca/crest/xtb/censo/shermo 的 `shutil.which` 或 `CONFSEARCH_*_PATH` 覆盖结果（探测方式参考 `tests/conftest.py:47-57,149-218`）；(d) 配置来源清单：`~/.cccp.yaml`、`./cccp.yaml`、`--config`、env 覆盖（`src/cccp/config.py:47-80,83-169,531-583`）；(e) 公开/动态入口：`acp = acp.cli:main`（`pyproject.toml:71-72`）、`python -m acp`（`src/acp/__main__.py`）、`python -m acp.cli`、`uvicorn acp.api.server:app`（`src/acp/cli.py:3724`）、CLI dispatch dict（`src/acp/cli.py:4129-4149`）、retired 拦截（`:3768`）；(f) 测试证据：`pytest --collect-only -q | tail -3` 与一次 `pytest -m "not slow" -q` 基线（既有失败登记为 baseline defects，本计划不修复）；(g) A00/C00 地图接收状态（未产出则登记 `pending` + 索取渠道）。Must NOT：不改代码/配置/数据；不清理 dirty；不把未实测写成收益。
  Closes: GAP-11
  Parallelization: Wave 1 | Blocked by: none | Blocks: 6+（契约输入）、71（收口对照）
  References (executor has NO interview context - be exhaustive): 根 `AGENTS.md` COMMANDS/EXECUTION PROTOCOL；`docs/reference/current/ACP_Execution_Integrity_Rebaseline_Notes.md:1-36`；`docs/reference/next-change/ACP_Architecture_Progress_and_Parallel_Index_20261018.md` §1（状态口径）§6（M0）；`src/cccp/config.py:47-169,531-583`；`pyproject.toml:24-76`；`src/acp/cli.py:3724,3768,4129-4149`；`tests/conftest.py:40-57,149-218,443-461`
  Acceptance criteria (agent-executable): `b00-baseline.md` 存在且含 (a)—(g) 七个 section；每个 section 附采集命令原文与输出摘要；未采集项显式标 `未测`；`git status --porcelain` 原文附上。
  QA scenarios (name the exact tool + invocation): happy = `python -c` 断言脚本校验 7 个 section 标题齐全 → PASS；failure = 临时移除 (d) 段后重跑同一断言 → FAIL（负例证明校验有效）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-1-acp-parallel-plan-2-services.md`
  Commit: N（证据在 `.omo/`，不入库） | n/a
  Recommended task executor category: unspecified-low — 机械采集与留证，无跨模块推理。

- [x] 2. B00-2 冻结两个主科研场景（M0）与量测方法：输入/方法/机器/成功定义 + 请求/延迟/I-O/大数据/安装/恢复基线
  What to do / Must NOT do: 按并行索引 §6 固定两场景：(A) 结构→optimize/frequency→证据报告（`src/acp/workflows/simple.py:547-694`）；(B) Confsearch→人工候选→BatchOptimize（`src/acp/results/frame_candidates.py:92,340,362` → `src/acp/calculations/batch/engine.py:372,548`）。逐条记录：固定输入与 charge/multiplicity/方法、目标机器与软件版本、成功定义（完成证据与容差出处）；量测请求/延迟/I-O：API 请求数、`GET /api/jobs`、`GET /tasks/{id}`、远端调用、DB 写入、传输队列、缓存命中（无测量条件处写 `未测` 并写测量方法）；大数据样本规模（轨迹/能量产品，参考 `src/acp/results/frames.py:175` 视图种类）；安装步骤（`pip install -e '.[dev,api,remote]'`、`scripts/install_systemd.sh:32-51`）；恢复基线场景清单（`tests/baseline/recovery_fixtures/README.md`）。Must NOT：不承诺未测收益百分比；不改 fixtures/黄金值。
  Closes: GAP-11
  Parallelization: Wave 1 | Blocked by: none | Blocks: 66—71（对照基线）
  References (executor has NO interview context - be exhaustive): 并行索引 §6-M0；`docs/ACP_Architecture_Adjustment_Plan_20261016.md` §35-M0、§14.3；`src/acp/workflows/simple.py:547-694`；`src/acp/calculations/batch/engine.py:372,548`；`src/acp/results/frame_candidates.py:92,340,362`；`tests/baseline/recovery_fixtures/README.md`
  Acceptance criteria (agent-executable): `b00-scenarios.md` 含两场景定义表 + 量测方法与结果（或 `未测`）+ 安装/恢复步骤；每条指标附采集命令。
  QA scenarios (name the exact tool + invocation): happy = 用 FastAPI TestClient（`ACP_RUN_ROOT`=tmp）重放候选保存路径并记录实际/未测；failure = 删除成功定义后校验脚本报缺失 → FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-2-acp-parallel-plan-2-services.md`
  Commit: N（.omo 证据） | n/a
  Recommended task executor category: unspecified-low — 记录与轻量测量。

- [x] 3. B00-3 建逐符号迁移台账 + 公开 allowlist + 冻结首片契约（Access/Data/科学输入输出/执行身份）+ 登记共享文件单写者
  What to do / Must NOT do: (a) 建两份表（评审 P1-5：拆分，不混列数）：①`tests/baseline/services-evidence/migration_ledger.md` 沿用 **6 列格式**（能力/当前实现驻点/兼容入口/生产消费者/退出条件/验收测试，格式参照 `tests/test_architecture_invariants.py:589-640`，checker 不改）；②`tests/baseline/services-evidence/handoff_registry.md` **交接登记表**（owner/可修改范围/provider/consumer/base_commit/prerequisites/acceptance/applied_commit；与 C-T5 共同维护、A 引用同一版本）；两表均入仓受版本控制（禁止只放 `.omo/`）；迁移台账初版 ≥20 行覆盖：projects、structure_source_store、task_views、remote paths/release/submission、calculations 发布、api 读取、init_wizard、catalog 等；每个待迁符号可定位生产消费者与接收任务；补丁"已交付"与"已集成"分开登记；(b) 公开 allowlist：`acp_services` 允许公开符号与依赖方向（`acp → acp_services` 允许；`acp_services → acp` 禁止，含懒加载/TYPE_CHECKING）；(c) 契约文档 `docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`：Access（Principal/AccessScope/ResourceGrant 最小字段）、Data（DataResourcePort 操作/幂等键/expected_revision；含 availability/freshness/basis_revision 语义区分，评审 P1-8）、科学输入输出（沿用 `cccp.calculation.requests/results` + frames 映射约定）、执行身份（job/attempt/submission/release 字段白名单）；每类附正常/空/部分/损坏/旧版本样例（随交付物版本冻结；字段命名由 owner 定义，不另起 DTO；样例覆盖 X2 表六项交付物：Access 与配置边界、Data prepare/read、事件流、科学产品与帧 adapter、执行协议与科学闭包、发行与资产）；(d) 单写者表按并行索引 §5 登记。Must NOT：不实现契约代码（仅定义+样例）；不建第二套 DTO；allowlist 只增不删（删除需说明）；不用"样例是合法 JSON"代替契约语义。
  Closes: GAP-1, GAP-11
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 6—10（按契约开发）、30—31（Envelope 草案）
  References (executor has NO interview context - be exhaustive): 并行索引 §5（单写者表）§6；主方案 §7.1/§7.2/§17；`tests/test_architecture_invariants.py:589-640`；`tests/baseline/refactor-evidence/migration_ledger.md`（格式样板）；`src/cccp/calculation/requests.py:1762`、`results.py:1387`、`context.py:41`；`src/acp/results/frames.py:163-227`
  Acceptance criteria (agent-executable): 台账每行 6 列且 ≥20 行（checker 解析断言）；交接登记表扩展列齐全；两表均在 tracked 路径；契约文档四类齐备、样例（含损坏/旧版本）逐个可解析且被消费方测试引用（≥1 组）；单写者表与并行索引 §5 diff 一致。
  QA scenarios (name the exact tool + invocation): happy = `python - <<'EOF'` 解析台账/契约样例 → PASS；failure = 制造 5 列行 → FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-3-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): freeze first-slice contracts, symbol ledger and single-writer map
  Recommended task executor category: deep-high — 跨边界契约冻结，需同时满足 A/C 可消费性与护栏等效。

- [x] 4. B00-4 关联备份/恢复样例与在途执行对账方案（P0-04）
  What to do / Must NOT do: 在临时 run_root 演练：SQLite DB（jobs/tasks/structure_source/events 表）+ 任务目录 + manifest + 密钥版本占位的关联备份与恢复；恢复后"先对账外部作业再开放派发"步骤（对照 `src/acp/scheduler/manager.py:5477,5745` 启动恢复、`src/acp/scheduler/remote/submission.py:192` 租约语义）；输出 `b00-backup-recovery.md`：演练命令、恢复核对清单（终态不可复活/未知提交对账/lease 失效不释放）、可回退 release 方式（`src/acp/scheduler/remote/release.py:685,810,887`）。Must NOT：不动生产 run_root/DB；不宣称全量备份系统；不承诺全原子回滚。
  Closes: GAP-11
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 68（B13 备份门）
  References (executor has NO interview context - be exhaustive): 主方案 §15.2/§24.3；`src/acp/scheduler/store.py:106,681,724,747`；`src/acp/scheduler/manager.py:5477,5745`；`src/acp/scheduler/remote/submission.py:56,192`；`src/acp/scheduler/remote/release.py:685,810,887`；`scripts/migrate_run_root.py:56-123`；`docs/reference/current/ACP_Job_File_Layout_Spec.md` §2b
  Acceptance criteria (agent-executable): 演练在 tmp 环境实际执行；核对清单逐项有 PASS/FAIL 记录；含"跳过对账"的对照结果。
  QA scenarios (name the exact tool + invocation): happy = 造 2 假 job（1 终态+1 在途）→ 备份→恢复→对账 → PASS；failure = 跳过对账的对照运行 → 暴露重复派发风险（记录 FAIL 证据）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-4-acp-parallel-plan-2-services.md`
  Commit: N（.omo 证据） | n/a
  Recommended task executor category: deep-low — 复用既有 CAS/租约语义编排演练。

- [x] 5. B00-5 组件 manifest（现有/候选/试点/批准）；不新增默认依赖
  What to do / Must NOT do: 建 `docs/reference/next-change/ACP_Services_Component_Manifest.md`：逐组件 版本/许可/角色/秘密/故障/回退/状态（现有：FastAPI/Uvicorn、Paramiko、3Dmol、科学基础库；候选：pwdlib/Authlib/PyCasbin/Keycloak、PostgreSQL、Caddy、Slurm/Podman、SeaweedFS、ECharts、QCElemental/QCEngine/cclib、Lago、Prometheus/restic）；映射主方案 §27.1 九模块归属与 §27.2 基础设施边界；候选≠已批准。Must NOT：不把候选加进 `pyproject.toml`/`requirements-node.txt`；不安装候选；不宣称已验证。
  Closes: GAP-11, GAP-10（选型前置）
  Parallelization: Wave 1 | Blocked by: none | Blocks: 62—65（B12 逐组件门）
  References (executor has NO interview context - be exhaustive): 主方案 §27/§28.3；`docs/reference/next-change/ACP_Open_Source_Reuse_Assessment_20261016.md` §2/§6；`pyproject.toml:24-69`；`requirements-node.txt`
  Acceptance criteria (agent-executable): manifest 每行含批准状态与证据链接（候选=None）；`grep -Ei "pwdlib|keycloak|seaweedfs|slurm|\blago\b" pyproject.toml requirements-node.txt` 零命中。
  QA scenarios (name the exact tool + invocation): happy = 解析脚本核对状态枚举与 §27.1 模块对齐 → PASS；failure = 向 pyproject 副本加入未批准组件 → 负断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-5-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): add component manifest (existing/candidate/approved, no new deps)
  Recommended task executor category: unspecified-low — 台账整理。

### Wave 2 — B01 Workspace 访问边界与身份模型（local-single 先行）

- [x] 72. B01-0（评审修订 B1）依赖闭包与过渡装配基础：Services 最小 Protocol + 旧 acp 装配注入端口；每个注入实现登记迁移任务与移除条件
  What to do / Must NOT do: (a) 在 `src/acp_services/contracts/` 定义跨包注入的最小 Protocol（job store：db_path + schema 初始化入口；migrations；tasks/task_views；manager/controller；规划/executor/step requirements/recovery/publication；catalog/参数解析；remote paths/release/submission）——按科学/平台归属拆分，不新增 facade 冒充迁移；(b) 旧 acp 装配入口（application bootstrap）创建旧实现实例并经上述端口注入 Services；Services **不 import、不动态查找**旧实现；(c) 登记表列必须具体可核对（评审 P0-4）：`旧实现 → 目标模块 → 实际迁移任务（具体 todo/子项 ID） → 移除注入任务 → 验收`（覆盖上述九类；初始将 schema/store、控制与恢复、Data writer/cache、plan/step requirements、参数转换列到 todo 77，其余按声明工作流范围分批）；(d) 本阶段只能声明"端口收敛"，不能声明 Services 已独立；独立性证据归于实际移除旧实现后的任务（todo 77/78）。Must NOT：不复制第二份实现；不用注入端口长期替代迁移（每个注入必须对应后续迁移与移除条件）。
  Closes: GAP-2, GAP-3
  Parallelization: Wave 2 | Blocked by: 1—3 | Blocks: 6、11—12、25、31（首片提取）、77（收口）
  References (executor has NO interview context - be exhaustive): 评审 B1（含验收）; 主方案 §10/§12.1；`src/acp/scheduler/projects.py:15,16,50,56`（依赖样例）；`src/acp/scheduler/manager.py`；`src/acp/scheduler/store.py`；`src/acp/calculations/step_requirements.py`
  Acceptance criteria (agent-executable): `pytest tests/test_services_injection_contracts.py -q` PASS：端口静态禁令（Services 侧无 acp import/动态查找）+ 旧装配注入路径可运行 + 台账每注入含迁移任务与移除条件；生产入口使用注入实现（调用断言）。
  QA scenarios (name the exact tool + invocation): happy = 经注入端口跑通 ProjectManager 首片 → PASS；failure = Services 侧注入 `import acp.scheduler.store` → 隔离断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-72-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services): dependency-injection contracts for transitional assembly (no direct old-impl imports)
  Recommended task executor category: deep-high — 跨包装配边界与迁移台账。

- [x] 6. B01-1 建立 local principal 与公开授权接口；原地接管项目/成员/分组/settings owner（revision 不混写）
  What to do / Must NOT do: 新建 `src/acp_services/contracts/access.py`（`Principal`/`AccessScope`/`ResourceGrant`/`Decision` 冻结 dataclass）+ `src/acp_services/workspace/`；实现 `LocalPrincipal`（固定本地主体 ID，来源 loopback/本机上下文；不信任客户端 tenant 字段）与 `LocalSingleAccessPolicy.authorize()`（最小 allow + 显式 deny + 可测撤销位）；把 `src/acp/scheduler/projects.py::ProjectManager` 实现体迁入 `src/acp_services/workspace/projects.py`（公开符号名与 SQLite 表结构不变），旧路径改为同实现转发（`from acp_services.workspace.projects import ProjectManager, ...`）；**依赖闭包与过渡装配（前置 todo 72）**：其现有依赖（`projects.py:15` migrations、`:16` JobStore、`:56` migrate 调用）由旧 acp 装配入口创建并经 Services 最小 Protocol（db_path + schema 初始化入口）注入——Services 不 import、不动态查找旧实现；本阶段只声明"端口收敛"，不声明已独立；台账登记该注入实现的迁移任务与移除条件；任务名称/标签仍归 Runtime（`src/acp/scheduler/tasks.py:729`）、结构来源组织仍归 Data（本 todo 不触碰）；`scheduler/molecule_groups.py` 不在迁移范围（台账登记"待分类"，禁双写）；新增 `tests/test_acp_services_isolation.py`：(a) AST 扫描 `src/acp_services/**` 禁 import `acp`（含 TYPE_CHECKING/懒加载/字符串），(b) `acp.scheduler.projects.ProjectManager is acp_services.workspace.projects.ProjectManager`，(c) Services 台账 6 列校验（仿 `tests/test_architecture_invariants.py:615-640`）；`pyproject.toml` include 增加 `"acp_services*"`（`:74-76`）。Must NOT：不建第二套项目表；不改 `ProjectManager` 签名/schema；不让 `acp_services` 反向 import `acp`；不动态查找/字符串反射旧实现充当完成。
  Closes: GAP-2, GAP-3
  Parallelization: Wave 2 | Blocked by: 1—3、72 | Blocks: 7—10、11+
  References (executor has NO interview context - be exhaustive): 主方案 §5.1/§10/§12.1/§37.1；并行计划二 B01-1；`src/acp/scheduler/projects.py:1-209`；`src/acp/scheduler/manager.py`（ProjectManager 调用点）；`pyproject.toml:74-76`；`tests/test_architecture_invariants.py:55-88,615-640`；`src/acp/scheduler/molecule_groups.py`
  Acceptance criteria (agent-executable): `pytest tests/test_acp_services_isolation.py -q` PASS；`python -c "import acp_services, acp.scheduler.projects as p, acp_services.workspace.projects as n; assert p.ProjectManager is n.ProjectManager"` PASS；`pip install -e .` 后 `python -c "import acp_services"` PASS；现有 `pytest tests/test_acp_api_v1.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 上述命令组合；failure = 在 `acp_services/workspace/projects.py` 注入 `import acp.scheduler.jobs` 后重跑隔离测试 → FAIL（负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-6-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): move project ownership in-place + local principal/access contracts + package guard
  Recommended task executor category: deep-high — 跨包归属迁移 + 护栏等效门设计。

- [x] {t} 身份模型分层：user / tenant(project) / role / tier / ResourceGrant / 计费主体分离；范围由验证身份得出
  What to do / Must NOT do: 扩展 `src/acp_services/contracts/access.py`：`User`/`TenantScope`/`ProjectScope`/`Role`/`Tier`/`ResourceGrant`（核数/内存/并发/时长/存储/队列字段占位）/`BillingSubject` + `validate_scope()` 关系校验；实现 `resolve_scope(principal, requested_project)`：以 principal 为权威推导项目范围（本地档位=一个 principal/tenant 但保留其多个合法 project；旧项目按确定性接纳规则归属，未知/未授权项目显式 deny；`deployment_tier` 与 `execution_mode=local/remote` 分离——远端计算不自动代表多人托管）；权限矩阵（allow/deny/未知/禁用/降配最小集）与单测。Must NOT：不引入第二身份事实来源；tier 不赋予管理角色；不实现计费逻辑（仅契约字段）；API 调用点不得直接信任客户端 `project_id`（经 `resolve_scope` 口径）。
  Closes: GAP-3
  Parallelization: Wave 2 | Blocked by: 6 | Blocks: 8—10、48+（MU-01 复用）
  References (executor has NO interview context - be exhaustive): 主方案 §22.3/§22.4/§25.2（MU-01）；并行计划二 B01-2；`src/acp/api/v1_routes.py:5901-5942`（现状：project_id 信任 + 403 守卫、`_user_from_request` 仅审计）；`src/acp/scheduler/projects.py`
  Acceptance criteria (agent-executable): `pytest tests/test_services_identity_model.py -q` PASS；矩阵表覆盖等级/角色/授权分离；`grep -rn "requested_project\|tenant_id" src/acp_services` 证明仅经 `resolve_scope`。
  QA scenarios (name the exact tool + invocation): happy = allow(本地)/deny(伪造跨项目) 双分支断言 → PASS；failure = 请求携带他项目 project_id → 期望 403/deny。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-7-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): split identity model (user/tenant/role/tier/grant/billing)
  Recommended task executor category: deep-high — 身份/授权边界与既有 API 兼容语义设计。

- [x] {t} API Key 原语与范围/期限/撤销模型（本地档位适用部分；注册/会话后置）
  What to do / Must NOT do: `src/acp_services/workspace/keys.py`：密码学安全随机 Key（公开前缀 + 高熵秘密）、仅存摘要（不可逆保存）、常量时间校验、`scope/project/actions/expiry` 元数据、轮换/撤销 API 与 SQLite 表；测试断言日志/科学载荷无明文；注册/邀请/会话/首次管理员流程在模块 docstring + 台账标记 deferred（B09/MU-02 承接）。Must NOT：不实现明文回显 UI；不复用低熵口令哈希；Key 不进 URL/命令行/日志。
  Closes: GAP-3
  Parallelization: Wave 2 | Blocked by: 6 | Blocks: 48+（MU-04）、57+（预算字段）
  References (executor has NO interview context - be exhaustive): 主方案 §23.2/§24.1/§24.3、§25.2 MU-04；并行计划二 B01-3；`src/acp/api/v1_routes.py:5932`（现状审计头）
  Acceptance criteria (agent-executable): `pytest tests/test_services_api_keys.py -q` PASS：生成→仅摘要入库→校验通过→撤销后拒绝；日志捕获断言无明文；限权/期限边界。
  QA scenarios (name the exact tool + invocation): happy = 上述单测 → PASS；failure = 篡改摘要后校验 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-8-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): API key primitives (digest-only, scoped, revocable)
  Recommended task executor category: deep-low — 边界清晰的安全原语。

- [x] {t} LocalIdentity/OIDC 主体映射契约：固定主体 ID + issuer+subject；迁移预览/冲突/回退（不自动合并）
  What to do / Must NOT do: `src/acp_services/workspace/identity_mapping.py`：`ExternalIdentity(issuer, subject)`、`bind(principal_id, ext)`、冲突检测（重复 subject/一对多/已占用）、撤销与恢复；迁移预览 dry-run 输出受影响项目/角色/账户；回退只恢复已验证绑定、不复活吊销凭据；本地档位保持 issuer=`local`。Must NOT：不按邮箱/同名角色自动合并或升权；不实现 OIDC 网络登录（B12/OS-03 PoC）；不动会话。
  Closes: GAP-3
  Parallelization: Wave 2 | Blocked by: 6 | Blocks: 48+（MU-13/OS-03）
  References (executor has NO interview context - be exhaustive): 主方案 §26.4、§25.2 MU-13；并行计划二 B01-4；`docs/reference/next-change/ACP_Open_Source_Reuse_Assessment_20261016.md` §3.1
  Acceptance criteria (agent-executable): `pytest tests/test_services_identity_mapping.py -q` PASS：重复 subject 拒绝、一对多显式冲突、预览稳定、回退不复活吊销；dry-run 无副作用。
  QA scenarios (name the exact tool + invocation): happy = 绑定→预览→冲突矩阵断言 → PASS；failure = 同邮箱不同主体 → 不合并且记录冲突。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-9-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): external identity binding contract (issuer+subject, no auto-merge)
  Recommended task executor category: deep-low — 契约+冲突矩阵明确。

- [x] {t} local-single 安全边界：loopback/Origin/CSRF；公网档位缺认证/隔离时拒绝启用且不由请求降档
  What to do / Must NOT do: 本地档位边界：默认 loopback 绑定保持（`src/acp/api/server.py:179`）；Origin 校验 + CSRF 防护必须同时覆盖三类合法通道：旧同源页面（既有 UI 未升级也可写入）、无 Origin 的已授权 CLI/机器请求、事件（SSE）连接——不能要求尚未接入的新 UI 才能写入；请求不得降档（`ACP_EXECUTION_MODE` 等只由进程/配置设定）；`require_tier_capabilities(tier)`：hosted/lab 档位在认证/隔离缺失时明确拒绝（错误 code 化，为 B08 错误契约预留）；新增 `tests/test_services_local_boundary.py`（伪造 Origin 拒绝、跨站写拒绝、降档拒绝、三类合法通道放行）。Must NOT：不实现完整会话（B09/todo 73）；不改变同源现有页面行为（F1＋S0）；不加 CORS 放开。
  Closes: GAP-3
  Parallelization: Wave 2 | Blocked by: 6 | Blocks: 48+（Lab 安全基线）
  References (executor has NO interview context - be exhaustive): 主方案 §24.1/§25.1/§30.1；并行计划二 B01-5；`src/acp/api/server.py:157-275`（无中间件现状）；`src/acp/cli.py:3672-3730`（serve 环境变量）；`src/acp/api/routes.py`（SSE/写端点）
  Acceptance criteria (agent-executable): `pytest tests/test_services_local_boundary.py -q` PASS；伪造 Origin 写请求 403；`pytest tests/test_acp_api_v1.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 同源请求通过 + 页面加载；failure = 跨源伪造写请求 → 403。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-10-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): local-single boundary (loopback/Origin/CSRF, tier refuse)
  Recommended task executor category: unspecified-high — 中间件 + 兼容回归。

### Wave 3 — B02a DataResourcePort / StorageBinding / 持久 prepare（立即可执行）

- [x] {t} 建 `src/acp_services/data/` 与类型化 DataResourcePort：输入/工件/manifest/来源/候选/传输/缓存唯一写入口
  What to do / Must NOT do: 新建 `src/acp_services/data/{__init__,contracts}.py`：`DataResourcePort`（Protocol/显式方法，非任意 `call(name, dict)`）覆盖六域：inputs（登记/校验）、artifacts/manifest（`publish`/`read`）、sources（结构来源组织）、candidates（frame_candidates）、transfers（传输操作）、cache（可用性）；首版以 adapter 委托现有实现（`src/acp/storage/manifest.py:88-163`、`src/acp/results/frame_candidate_store.py:36,53,148`、`src/acp/scheduler/structure_source_store.py:361,412`、`src/acp/results/remote_structure_cache.py:126,372,661`），行为不变；迁移台账登记这四处的 owner 与退出条件；保持：frame_candidates 权威文件、`source_uid_for`（`structure_source_store.py:261`）、expected_revision、旧 URL/相对路径兼容（`storage/manifest.py` v2 读旧版）。Must NOT：不改 v2 manifest schema；不建第二缓存/第二来源表；不改变 GET 现有行为（本 todo 是端口与委托，不是切换）。
  Closes: GAP-4, GAP-2
  Parallelization: Wave 3 | Blocked by: 1—3、6、72 | Blocks: 12—14、19+
  References (executor has NO interview context - be exhaustive): 主方案 §5.3/§7.1/P2-01；并行计划二 B02-1；`src/acp/storage/manifest.py:21-38,88-163`；`src/acp/results/manifest.py:23-42`；`src/acp/results/frame_candidate_store.py:36,53,124,148`；`src/acp/scheduler/structure_source_store.py:261,340,361,412`；`src/acp/results/remote_structure_cache.py:126,372`；`tests/test_storage_manifest_kinds.py`；`tests/test_acp_frames.py:91-101`
  Acceptance criteria (agent-executable): `pytest tests/test_services_data_port.py -q` PASS：六域各一个 round-trip（tmp fixture）+ frame_candidates 权威不变式 + 旧 manifest 读取兼容；`grep -rn "class DataResourcePort" src/acp_services` 存在且无 `**kwargs` 任意分派。
  QA scenarios (name the exact tool + invocation): happy = round-trip 六域测试 → PASS；failure = 直写 `RESULT/frame_candidates.json` 绕过 `write_authority` → 守护断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-11-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): typed DataResourcePort over existing writers (six domains, compat preserved)
  Recommended task executor category: deep-high — 六域写入口归并 + 兼容边界。

- [x] 12. B02-2 StorageBinding 唯一分配权威：现有目录分配接入；Runtime 只冻结/消费
  What: `src/acp_services/data/storage_binding.py`：`StorageBinding`（逻辑身份：租户/项目叶子、受控根、完整相对路径、远端映射版本、端点）与分配器，包装现有分配机制（`src/acp/scheduler/remote/paths.py:46,59,85,203,217`）；Runtime 侧只读消费（冻结进 job/attempt）；保留：人类可读目录、远端映射、调度器 markers（`remote/runner.py:1533`）、rerun/continue 同目录与 `WORK/00_RUNTIME/attempts/<N>/` 回执归档（`manager.py:1616,1666`）；旧映射显式接纳（`resolve_remote_dir` 的显式→`result["remote_dir"]`→`remote.relative`→双候选探针→平铺回退链只许在接纳旧任务时使用）。Must NOT：不建第二目录分配器；不改任务目录命名（ANTI #15）；不批量迁移旧目录。
  Closes: GAP-4, GAP-10（StorageBinding 契约）
  Parallelization: Wave 3 | Blocked by: 11 | Blocks: 13—14（同包递进）；30—31 仅引用 StorageBinding 稳定字段形状（契约冻结于 todo 3），分配权威落地在 12（不阻塞 30/31）
  References (executor has NO interview context - be exhaustive): 主方案 §11.2/§23.1（StorageBinding 定义）；并行计划二 B02-2；`src/acp/scheduler/remote/paths.py:46,59,65,85,203,217`；`src/acp/scheduler/manager.py:1616,1666,1694,3341`；`src/acp/scheduler/remote/runner.py:1533`；`docs/reference/current/ACP_Job_File_Layout_Spec.md` §2/§2a/§2b；`tests/test_remote_storage_identity.py`
  Acceptance criteria (agent-executable): `pytest tests/test_services_storage_binding.py tests/test_remote_storage_identity.py -q` PASS；同项目同名/跨项目同名/rerun/continue 四场景目录复用且回执隔离；旧平铺目录只读接纳（负例：新分配不得落平铺）。
  QA scenarios (name the exact tool + invocation): happy = 四场景 + 旧映射接纳 → PASS；failure = 构造缺 markers 的同名目录 → `RemoteDirConflictError` 不删除。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-12-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): single StorageBinding authority wrapping existing dir allocation
  Recommended task executor category: deep-high — 目录/身份契约与远端映射兼容。

- [x] 13. B02-3 进程内预取升级为持久 prepare operation：身份/幂等键/状态/租约/重启恢复/重试/失败原因/同资源去重/取消/可用性提交
  What: `src/acp_services/data/operations.py` + SQLite 表（`data_operations`）：操作身份（job/attempt/资源摘要）、稳定幂等键、状态机（queued/running/succeeded/failed/canceled/expired）、lease + ownership 代次/租约 token（过期可恢复、不误判完成；发布时以 CAS 同时核对 operation/attempt/资源 generation/授权版本/受控存储绑定，旧租约持有者失去发布资格）、重试策略与失败原因、同资源去重（去重键包含权限范围；取消一个观察者不误杀其他合法消费者的操作）、取消语义、完成时提交 availability；后台执行器替代 manager 进程内预取队列（`src/acp/scheduler/manager.py:4709-4797` 的 `_queue_catalog_prefetch/_catalog_prefetch_loop/_prefetch_remote_catalog` 改为 enqueue 持久操作）；启动恢复重放未决操作。Must NOT：不丢现有"终态后台预取不依赖浏览器 fetch=1"的行为（`manager.py:5177` 终态入队）；不在日志/内存里保存操作事实为主（DB 为权威）；lease 到期本身不释放、不误判完成。
  Closes: GAP-4
  Parallelization: Wave 3 | Blocked by: 12 | Blocks: 15—16（同包递进）；26—28 复用其幂等/重试模式（软依赖）
  References (executor has NO interview context - be exhaustive): 并行计划二 B02-3；主方案 §12.3/§16（持久阶段与幂等恢复）；`src/acp/scheduler/manager.py:4709-4797,5177`；`src/acp/results/remote_structure_cache.py:212-256,372,661,724,741`；`src/acp/scheduler/remote/fetcher.py:86,237,259`
  Acceptance criteria (agent-executable): `pytest tests/test_services_prepare_operations.py -q` PASS：重启恢复（杀掉进程重放）、重复 enqueue 去重、取消不污染计算、失败原因可见、lease 过期重试；`pytest tests/test_acp_remote_catalog_prefetch.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 模拟重启 + 断网重试 → 操作收敛到 succeeded/failed 且 availability 一致；failure = 同资源双 enqueue → 仅一个操作执行（去重断言）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-13-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): persistent prepare operations (idempotency/lease/retry/recovery)
  Recommended task executor category: deep-high — 持久操作状态机与恢复语义。

- [x] 14. B02-4 下载→摘要/大小/身份/attempt/generation 校验→缓存发布→可用性记录；拒绝旧 attempt/部分文件/权限撤销/迟到传输
  What: 在 prepare 操作内实现传输管线：临时下载（`.tmp`）→ 内容 sha256/大小/身份（job/attempt/相对路径）校验 → 原子发布到 `.remote_cache/<job_id>/…`（复用 `remote_structure_cache.py:158,182,212-256` 的 traversal guard 与 fence/generation）→ 持久发布记录 + availability 提交；**预期摘要来自冻结的源 manifest/完成回执（不能以刚下载文件的自算摘要充当预期值）**；下载前与发布前都重验授权；拒绝：旧 attempt 回执、部分文件（大小/摘要不符）、权限撤销（SFTP 失败分类）、迟到传输（generation/attempt 不匹配即丢弃并记录）；文件/DB 崩溃窗口定义为可对账状态（"文件已发布 DB 未登记"/"DB 有记录文件缺失"/"旧 worker 迟到"），未确认工件不得显示 ready；`push_paths` 写回保持（`remote_structure_cache.py:499`）；**历史无摘要工件的接纳协议（评审 P1-6）**：新工件摘要放兼容 metadata 或 Data 版本化清单（旧 v2 reader 兼容）；历史工件优先利用有效既有摘要，无摘要时经受控源端快照/校验流程取得并登记基线（标明接纳来源与验证级别），不得以"下载后再算 hash"冒充预期摘要、不得让旧任务永久 pending；mutable 资源（日志/候选/组织文件）以 revision/generation/range 协议定义读取一致性，不按一次性 attempt 工件处理。Must NOT：不假定 POSIX rename 之外语义；不把未验证 ETag/大小当摘要；不污染当前 attempt 缓存；不让未确认工件可见为 ready。
  Closes: GAP-4
  Parallelization: Wave 3 | Blocked by: 13 | Blocks: 15—16、63（对象存储语义对齐）
  References (executor has NO interview context - be exhaustive): 主方案 §11.2/P2-03、§23.1（上传/完整性确认/登记发布）、§28-OS-07；并行计划二 B02-4；`src/acp/results/remote_structure_cache.py:77-80,158-256,372,398-458,499,724-741`；`src/acp/scheduler/remote/fetcher.py:237,259,283`
  Acceptance criteria (agent-executable): `pytest tests/test_services_transfer_verify.py -q` PASS：四类拒绝各一用例 + 正常发布 + availability 提交；缓存目录无 `.tmp` 残留；历史接纳用例（新回执、带摘要旧结果、无摘要旧 v2/历史结果、mutable 资源、损坏源）；冻结 F0 仍可读取原有合法任务。
  QA scenarios (name the exact tool + invocation): happy = 正常下载→校验→发布→availability → PASS；failure = 注入半截文件（截断）+ 旧 attempt 标志 → 两者均拒绝且缓存不变。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-14-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): transfer verify→publish→availability (reject stale/partial/late)
  Recommended task executor category: deep-low — 管线清晰，复用既有 fence。

### Wave 4 — B02b 统一读取根与 cache-only 过渡（15/16 需 13；18 有需求 gate）

- [x] 15. B02-5 统一读取根与新接口准备（不切行为）：新读取面只读（零 SSH/SFTP、零 enqueue、零解析写入）；显式 prepare 面才可建 operation；大文件/Range 有界
  What: 统一读取根与新接口准备（本 todo 不改旧行为、不切 cache-only）：`_job_read_root` 语义收敛（`v1_routes.py:366-380`、`v2_routes.py:220-233`、`_structure_viewer_root` `:333-363`）；新读取接口（新 GET/availability 查询）实现为只读：零 SSH/SFTP、零 enqueue、零解析写入，只返回快照/availability（含 freshness/revision）；**允许创建持久 operation 的入口只有**：显式 prepare（POST/命令）、终态后台流程、启动恢复（均走 todo 13）；大文件有界读取（Range/chunk，复用 `fetcher.py:300` 语义，禁整文件读入内存）；旧 GET 保留原行为，作为显式 legacy adapter 登记（副作用=受限过渡项：调用者/期限/退出条件入台账）；新 GET 未启用前不得声称已纯只读。（切换动作全部在 todo 16。）Must NOT：不在本 todo 改变任何资源的读取路径；新读取面不入队；不删除旧路径；不改 409/200 语义；不引入第二读取根。
  Closes: GAP-4
  Parallelization: Wave 4 | Blocked by: 13、14 | Blocks: 16、17、19—21、42+
  References (executor has NO interview context - be exhaustive): 并行计划二 B02-5；评审 B2；主方案 §5.3（GET 隐式 prepare 禁止）、§8.3；`src/acp/api/v1_routes.py:333-380,3647-3774,5413`；`src/acp/api/v2_routes.py:201-233`；`src/acp/results/remote_structure_cache.py:661-675,790-797`；`src/acp/scheduler/remote/fetcher.py:283,300`
  Acceptance criteria (agent-executable): `pytest tests/test_services_read_root.py -q` PASS：新读取面零 SSH/SFTP 调用且 operation 计数零新增（mock 断言）+ availability 形状 + Range 有界；`pytest tests/test_acp_api_v1.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 终态远端 job 经新读取面命中缓存 → 无网络、无新 operation；failure = 在新 GET 注入 enqueue/网络调用 → 断言 FAIL（负例证明只读约束）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-15-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): read-root unification + read-only availability surface (no enqueue; prepare via explicit commands)
  Recommended task executor category: deep-low — 只读边界明确，关键是兼容形状。

- [x] 16. B02-6 cache-only 逐资源切换前的兼容证据：旧 Client 显式兼容路径/版本迁移证据；不删除当前 GET 行为导致 F0＋S1 不可用
  What: cache-only 逐资源切换（兼容先行）：先冻结 F0 客户端对拍用例（原版旧客户端在冷缓存下按原流程拿到结果——只返回相同的 200/409 不算），再对每个资源（catalog/manifest/frame_candidates/frequency_modes/geometry）在**同一可合入切片内**完成：兼容 adapter 就绪 → 对拍通过 → 启用新路径 → 保留按资源回退点；切换矩阵记录四组合（F0＋S0/F1＋S0/F0＋S1/F1＋S1）与每资源证据；过渡兼容面登记（调用者/期限/退出条件，入台账）；旧 GET legacy adapter 在窗口内保留（其副作用为已登记过渡项）。Must NOT：不先删读取回退；不宣称"全量 cache-only"直到逐资源切完并有证据；不把 F1 已完成为理由删旧语义；不把"相同状态码"当作兼容通过。
  Closes: GAP-4
  Parallelization: Wave 4 | Blocked by: 15 | Blocks: 69、75（联合验收）；与 17 为顺序建议（非硬依赖）
  References (executor has NO interview context - be exhaustive): 主方案 §38.7（F0/F1×S0/S1 矩阵）/§16（过早 cache-only 风险）；并行计划二 B02-6；评审 B2/X3；`docs/reference/current/ACP_Frontend_Closure_20261017.md`（S0 语境）
  Acceptance criteria (agent-executable): 冷缓存 F0 对拍（用户无需升级即可按原流程拿到结果）；新 GET 无网络调用、无 operation 新增断言；每资源切换证据 + 回退演练一次；兼容面登记行含期限。
  QA scenarios (name the exact tool + invocation): happy = F0 旧客户端对拍 → 通过后切该资源；failure = 未做对拍直接切换的对照分支 → F0＋S1 兼容测试 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-16-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): per-resource cache-only switch matrix + old-client compat channel
  Recommended task executor category: unspecified-high — 矩阵+兼容通道+回退演练。

- [x] 17. B02-7 逐入口授权核查（下载/日志/Range/候选/来源/缓存/事件）；存储预留含字节/inode/scratch/上传/临时放大并与硬限制对账
  What: 对每个读取/写入入口挂 AccessPolicy 检查（复用 todo 7 的 `resolve_scope`）：下载、日志、Range、frame candidates、structure sources、缓存读取、事件流；越权统一 403（形状兼容现有错误）；下载前与发布前都重验授权（SFTP 拒绝不等价于 Workspace 权限撤销）；共享资源去重键含权限范围；存储预算：为 prepare/上传/缓存登记字节/inode/scratch/临时放大预算并与实际硬限制对账（无硬限制时输出能力报告 `hard_limits: unavailable`，不谎报）；**预算峰值同时计算临时文件与正式文件**；预留失败 → 拒绝新分配并告警（不删已完成结果）。Must NOT：不仅在列表页过滤；不做只在前端隐藏的授权；不宣称仅靠 prefix 就有硬配额。
  Closes: GAP-4
  Parallelization: Wave 4 | Blocked by: 15 | Blocks: 48+（MU-07/08 基础）
  References (executor has NO interview context - be exhaustive): 主方案 §23.1/P2-04/MU-07/08、§25.3-1；并行计划二 B02-7；`src/acp/api/v1_routes.py:5808-6291`（下载/日志/remote-files）；`src/acp/api/v2_structure_sources.py:127-410`；`src/acp/scheduler/structure_source_store.py:135,433,1700`
  Acceptance criteria (agent-executable): `pytest tests/test_services_entry_authz.py -q` PASS：七入口越权矩阵全 deny + 授权全 allow；预算对账报告字段齐全；越权不改任何状态（负断言）。
  QA scenarios (name the exact tool + invocation): happy = 授权用户全入口访问 → PASS；failure = 伪造 scope 读他项目缓存/候选 → 403 且无副作用。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-17-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): per-entry authorization + storage budget reconciliation
  Recommended task executor category: unspecified-high — 多入口矩阵 + 预算对账。

- [~] 18. B02-8 多租户路径扩展（需求 gate）：先修订布局/AGENTS/等效门；覆盖同名/共享或独立根/旧映射/重命名/软链接越界/并发分配；禁截末两层回退
  What: 仅当 B09/B12 实际需要多租户路径时执行；执行前先修订 `docs/reference/current/ACP_Job_File_Layout_Spec.md`（新增租户段）+ 相关 AGENTS + 等效或更强路径护栏（登记修订，再切换）；实现 `<storage_root>/<租户叶子>/<项目叶子>/<任务目录>` 扩展（`storage_binding` 版本化），测试：跨租户同项目同任务名、共享根/独立根、缺失/错误 run_root、旧映射接纳、重命名、rerun/continue、软链接逃逸、并发分配；解析失败明确拒绝、**禁止截取末两层或回退全局平铺**。Must NOT：不在 gate 未过时开工；不批量迁移/重命名旧目录；不把显示名当路径权威。
  Closes: GAP-4
  Parallelization: Wave 4 | Blocked by: 12, 15；gate=B09/B12 需求确认 | Blocks: 62+（存储扩展）
  References (executor has NO interview context - be exhaustive): 主方案 §23.1（多租户目录契约）/MU-07/OS-07；并行计划二 B02-8；`docs/reference/current/ACP_Job_File_Layout_Spec.md` §2b；`src/acp/scheduler/remote/paths.py:85-217`；`tests/test_remote_storage_identity.py`
  Acceptance criteria (agent-executable): 布局规范修订 + 等效门通过（旧用例仍绿）；`pytest tests/test_services_tenant_paths.py -q` PASS：九类场景全覆盖；末两层回退负例拒绝。
  QA scenarios (name the exact tool + invocation): happy = 九场景矩阵 → PASS；failure = 构造末两层回退输入 → 明确拒绝（负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-18-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): tenant path extension behind layout revision + equivalence gate
  Recommended task executor category: deep-high — 布局契约修订 + 安全路径边界。

### Wave 5 — B03/B04 投影快照与持久事件（需 15）

- [x] 19. B03-1 复用 task-view/tasks index 与摘要，接管后台投影更新；查询只读已准备快照，去除同步扫描/解析回退
  What to do / Must NOT do: 把 `src/acp/scheduler/task_views.py::query_project_tasks`（`:215-377`，join jobs 的权威 status/attempt/revision `:254-258`）与其服务端调用（`src/acp/api/v2_routes.py:395-497`、`_enrich_active_rows` `:146-184`）改为"投影更新在后台、查询只读"：新增 `src/acp_services/runtime/projections.py`（行投影表或快照缓存，含失效钩子），`get_task_view` 不再逐行 enrich（`_enrich_active_rows` 默认保留为兼容分支；删除条件=A 路 A04 在 `.omo/evidence/` 登记"零消费者"证据，未获证据前不得删除）；同步扫描/解析回退（`v1_routes.py:411,416` `_enrich_from_state_json/_enrich_job_snapshot` 的请求内 enrich）不得出现在查询路径。Must NOT：不改 v2 响应字段/排序/分页语义；无投影时仍可显示 jobs 权威基础数据；不得把快照写坏 jobs。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 15 | Blocks: 20—21、42+（查询类接口）
  References (executor has NO interview context - be exhaustive): 并行计划二 B03-1；主方案 P1-03/§8.2；`src/acp/scheduler/task_views.py:215-377,385-429`；`src/acp/api/v2_routes.py:140-184,395-497`；`src/acp/api/v1_routes.py:411-416`；`src/acp/api/v2_schemas.py:157,199-222`
  Acceptance criteria (agent-executable): `pytest tests/test_services_task_projection.py -q` PASS：同数据下快照结果与旧结果字段等价（对比断言）；查询路径零同步扫描（mock/计数断言）；`pytest tests/test_acp_api_v1.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 冷启动后立即查询 → 基础事实可见 + 投影随后更新；failure = 删除投影表后查询 → 仍显示 jobs 基础事实（不回退扫描）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-19-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): background task projections; queries read prepared snapshots only
  Recommended task executor category: deep-high — 投影与查询解耦、响应等价。

- [x] 20. B03-2 jobs 保持权威；无投影可显示基础事实；freshness/observed_at、availability、独立 revision 明确
  What: 在投影/响应中新增三类明确语义（当前不存在，`task_views.py:353-377` 无此字段；评审 P1-8）：**availability**=资源是否已准备（ready/preparing/unavailable，由 Data 资源状态表达）；**freshness + basis_revision**=投影是否过期（fresh/stale；投影记录 basis_revision，与 job/attempt 及对应 owner revision 比较）；**observed_at**=诊断时间，不作为跨进程/跨节点严格新旧判定。新状态已提交但投影未跟上 → jobs 基础事实仍可显示、旧投影明确 stale；job/name/catalog/组织 revision 独立比较（不互相替代）；缺失快照返回 jobs 权威基础数据（形状：在 v2 响应中**先加后迁**，旧字段全保留）。Must NOT：不用数组位置/客户端时间/observed_at 当 freshness；不同 owner revision 不相互替代；不改现有 revision 语义；不把未就绪伪装为空成功。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 19 | Blocks: 24、A04 刷新策略对接
  References (executor has NO interview context - be exhaustive): 并行计划二 B03-2；主方案 §7.4（查询语义/未就绪不伪装）；`src/acp/api/v2_schemas.py:157-236`；`src/acp/scheduler/task_views.py:353-377`；`src/acp/scheduler/tasks.py:71-73,729`
  Acceptance criteria (agent-executable): `pytest tests/test_services_freshness.py -q` PASS：组合用例——"资源 ready 但投影 stale"、"无投影但 jobs 事实可见"、"旧 attempt 投影迟到"、"时钟偏移但 revision 正确"；A 用同一组 fixture 判断 prepare/刷新/显示部分。
  QA scenarios (name the exact tool + invocation): happy = 快照就绪/过期/缺失三态呈现 → PASS；failure = 伪造 observed_at 早于 revision 更新 → 断言拒绝（单调性负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-20-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): freshness/observed_at/availability + independent revisions
  Recommended task executor category: deep-low — 字段语义明确。

- [x] 21. B03-3 固定列表/详情请求预算：列表不逐行拉详情；按授权项目/过滤/分页范围构造查询
  What: 固化预算：列表查询禁止 N+1 详情（`_enrich_active_rows` 的 cap=200 `v2_routes.py:143` 仅过渡）；按 `resolve_scope` 推导的项目/过滤/分页构造单条查询（counts/truncated/facets 全量口径保持 `task_views.py:274-286`）；新增预算测试（记录 SQL 次数/请求数上限）；大列表分页与大项目 COUNT 的性能记录（对比 todo 2 基线）。Must NOT：不为省查询改语义（total/counts 仍全量）；不隐藏分页截断（truncated 保持）。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 19 | Blocks: 42+（列表类接口）
  References (executor has NO interview context - be exhaustive): 并行计划二 B03-3；主方案 §14.3（硬性可验证目标）；`src/acp/api/v2_routes.py:140-184,395-497`；`src/acp/scheduler/task_views.py:110-186,274-286`；`docs/reference/current/ACP_Task_Organization_DevDoc.md` §5（counting contract）
  Acceptance criteria (agent-executable): `pytest tests/test_services_query_budget.py -q` PASS：列表 SQL/请求数有上限断言；counts/total 口径不变；分页边界（offset/next_offset）保持。
  QA scenarios (name the exact tool + invocation): happy = 200 行列表查询 → 请求预算内；failure = 注入 N+1 详情分支 → 预算断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-21-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): fixed query budgets (no per-row detail fetches)
  Recommended task executor category: unspecified-high — 预算与语义双保真。

- [x] 22. B04-1 持久事件协议：复用每任务 JSONL/after_seq，定义 owner/scope/schema/cursor/去重/过期重置；保留旧任务流兼容入口
  What: 在现有 `src/acp/scheduler/events.py`（JobEventLog `:93,100,130,189`）与 SSE（`src/acp/api/routes.py:375-580`：after_seq `:379`、history `:380`、Last-Event-ID `:387`）之上定义版本化事件 envelope（`src/acp_services/runtime/events.py`）：owner（Runtime/Data/Workspace）、scope（task/project/availability）、`schema_version`、持久 `cursor`、稳定事件 ID（去重键）、过期 cursor → 快照重置语义；**通用事件契约放中立的 `src/acp_services/contracts/`（Workspace/Data 不通过 import Runtime 私有模块发布自身事实）**；owner 状态事务内同写 outbox（协议见 todo 23）；保留旧 `/api/jobs/{id}/events` 与 v1 别名（`v1_routes.py:5746`）兼容入口（同实现转发）。Must NOT：不删除旧事件文件/字段；不把进程内通知当事实（事实在 outbox/JSONL/DB 之后）。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 15 | Blocks: 23—24、43+（MCP/诊断）
  References (executor has NO interview context - be exhaustive): 并行计划二 B04-1；主方案 P1-04/§7.4（事件语义）；`src/acp/scheduler/events.py:1-218`；`src/acp/api/routes.py:375-580`；`src/acp/api/v1_routes.py:5746`
  Acceptance criteria (agent-executable): `pytest tests/test_services_events.py -q` PASS：协议字段/去重/过期重置用例；旧 SSE 路径回归（`pytest tests/test_acp_remote_catalog_prefetch.py -q` 等涉及 SSE 的既有测试）。
  QA scenarios (name the exact tool + invocation): happy = after_seq 重放 + 去重 + 过期重置 → PASS；failure = 用过期 cursor 请求 → 触发快照重置而非静默丢事件。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-22-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): versioned persistent event protocol (cursor/dedup/reset)
  Recommended task executor category: deep-high — 协议+兼容+游标语义。

- [x] 23. B04-2 事件与状态持久化顺序/幂等重放衔接：不能由日志或 SSE 取代 CAS；不在进程内通知后丢失事实
  What: 事件与状态的持久化协议（事务 outbox）：**扩展 owner repository 的事务边界**——`JobStore.transition()`（`store.py:194` 起）在同一连接内完成状态 UPDATE + outbox INSERT 后一次 `commit()`（不允许"CAS 提交后再单独写事件"的丢失窗口；也不得在 Runtime facade 外包两层事务）→ 提交后异步投递到持久事件流/兼容 JSONL（按稳定事件 ID 幂等、可重放）→ 再通知 SSE；消费端按稳定事件 ID/cursor 去重；快照 revision 与事件 cursor 衔接（快照读出到订阅之间不丢更新）；审计断言：任何事件对应已提交事实、重启后不丢事件且不重复改变状态；旧每任务 `after_seq` 入口仍可读。Must NOT：不做第二任务状态机；不以 SSE/日志为状态权威；不削弱 CAS；不先通知后写库。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 22 | Blocks: 24、66（故障覆盖）
  References (executor has NO interview context - be exhaustive): 并行计划二 B04-2；评审 B4；主方案 §5.4/§8.2；`src/acp/scheduler/store.py:194-367`；`src/acp/scheduler/manager.py:3983-4131`（cancel/观测序）；`src/acp/scheduler/events.py:100`
  Acceptance criteria (agent-executable): `pytest tests/test_services_event_ordering.py -q` PASS（评审 P1-7 故障矩阵）：①CAS 守卫失败→状态不变、无对应 outbox 事件；②UPDATE 后/outbox INSERT 前/提交前崩溃→二者一起回滚、无未提交事件（**不得为未提交状态补事件**）；③同事务提交后、投递前崩溃→状态与 outbox 均存在、重启补投；④投递成功、确认前崩溃→可重放、按稳定 ID 去重、不重复改状态；⑤旧 after_seq/JSONL 兼容出口在投递重试时去重（不重复追加同一事实）；cursor 过期/权限撤销用例。
  QA scenarios (name the exact tool + invocation): happy = 正常路径事件→事实可对上；failure = 注入"提交前崩溃"→ 状态与 outbox 一起回滚（断言无未提交事件；对照：错误地"提交后补写"分支 → FAIL 证据）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-23-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): transactional outbox + idempotent event delivery (no lost-fact window)
  Recommended task executor category: deep-high — 事务边界与投递幂等。

- [x] 24. B04-3 授权复用流及快照重置：区分日志/任务进度/摘要/Data availability 覆盖范围；交 A04 刷新策略消费
  What: 单受权流复用（同 principal+scope 复用连接）、订阅授权检查（可撤销立即生效）、快照重置（过期 cursor/权限变化/服务重启）；事件 scope 分类：日志、任务进度、任务摘要、Data availability（是否触发轮询降级由 A 侧决定，本 todo 只给覆盖范围声明与样例）；输出 A04 消费契约（样例事件序列 + 覆盖矩阵）并登记。Must NOT：不逐任务各建一条流（复用）；不要求 A 同步改造即可继续旧轮询（兼容）。
  Closes: GAP-5
  Parallelization: Wave 5 | Blocked by: 22、23（可靠投递） | Blocks: A04 接入（跨路）
  References (executor has NO interview context - be exhaustive): 并行计划二 B04-3；主方案 §7.4/§8.2；并行计划一 A04（消费方）；`src/acp/api/routes.py:496-570`（生成器/心跳/终态）
  Acceptance criteria (agent-executable): `pytest tests/test_services_events_authz.py -q` PASS：复用/撤销/重置用例；覆盖矩阵文档含样例序列；与 A 的取样对齐（diff 记录）。
  QA scenarios (name the exact tool + invocation): happy = 撤销后新连接拒绝、旧连接断开；failure = 跨 scope 复用（越权）→ 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-24-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): authorized reusable event stream + snapshot reset + scope matrix
  Recommended task executor category: deep-low — 复用/撤销语义清晰。

### Wave 6 — B05 Runtime 命令/规划/恢复/完成原地归并（需 15/19）

- [x] 25. B05-1 从 manager/workflows/calculations 提取 Runtime 用例与规则：submit/pause/unpause/cancel/continue/rerun/edit 保持同一执行 owner
  What: 新建 `src/acp_services/runtime/`（commands/planning/controller/worker/recovery 分文件按需）：把 `src/acp/scheduler/manager.py`（`:813,1051,1317,2227,2393,2448,2544,2606,1759`）的用例编排提取为 Runtime 用例函数（首版经注入端口委托现有 JobManager：由旧 acp 装配入口创建并注入，Services 不 import/不动态查找旧实现；台账登记注入实现的迁移任务与移除条件；本阶段只声明"端口收敛"，不声明已独立）；`src/acp/api` 与 `src/acp/cli.py` 的提交/控制入口改经 Runtime 用例（旧路径转发）；登记"控制意图归 manager"的单一 owner（`scheduler/AGENTS.md` STATE TRANSITIONS）。Must NOT：不改状态机语义；不双写/双 owner；不在 Runtime 复制科学逻辑。
  Closes: GAP-6, GAP-2
  Parallelization: Wave 6 | Blocked by: 15、19、72 | Blocks: 26—29、30+（Envelope 消费）
  References (executor has NO interview context - be exhaustive): 并行计划二 B05-1；主方案 §5.2/P4-01；`src/acp/scheduler/manager.py` 全部控制方法；`src/acp/scheduler/AGENTS.md` STATE TRANSITIONS；`src/acp/scheduler/job_edit.py:67`
  Acceptance criteria (agent-executable): `pytest tests/test_services_runtime_usecases.py -q` PASS：七命令各自"单 owner"断言（调用栈/转发链）+ API/CLI 入口行为等价（对比既有测试）；`pytest tests/test_acp_job_edit.py tests/test_acp_job_operations.py -q` 回归 PASS。
  QA scenarios (name the exact tool + invocation): happy = 七命令端到端（fake backend）→ 状态迁移与原实现一致；failure = 绕过 Runtime 直接调 store 的注入分支 → 架构断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-25-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): extract runtime use cases (single execution owner)
  Recommended task executor category: deep-high — 编排提取与单 owner 边界。

- [x] 26. B05-2 保持 revision/status/attempt CAS、控制意图优先、停止确认、PAUSED 资源语义、终态持久化后副作用与幂等重试
  What: 提取过程中逐条保真（以测试钉死）：CAS（`store.py:194,255,309,367`、`manager.py:2132`）；控制意图不被旧观测覆盖（`_drop_stale_poll:4925`/`_persist_poll_observation:4984`）；CANCELLING 确认后 CANCELLED（`manager.py:3983,4059,4120`；`remote/monitor.py` bkill 核对）；PAUSED 资源语义（killpg SIGSTOP/SIGCONT、LSF bstop/bresume、禁删、poller 跳过）；终态副作用在持久化后且幂等（`manager.py:5041`）；in-place rerun attempt+1 与回执归档（`:1616,1666`）。Must NOT：任何提取不得改变上述时序；不得新增状态。
  Closes: GAP-6
  Parallelization: Wave 6 | Blocked by: 25 | Blocks: 27—29、66—67
  References (executor has NO interview context - be exhaustive): 并行计划二 B05-2；根 `AGENTS.md` EXECUTION PROTOCOL；`docs/reference/current/ACP_Execution_Integrity_Rebaseline_Notes.md`；`src/acp/scheduler/store.py:194-367`；`src/acp/scheduler/manager.py:1616,1666,2132,3983,4059,4925,4984,5041`；`tests/test_execution_integrity_e2e.py:533,635,1030`；`tests/test_remote_cancel_confirmation.py`
  Acceptance criteria (agent-executable): `pytest tests/test_execution_integrity_e2e.py tests/test_remote_cancel_confirmation.py tests/test_remote_submission_protocol.py -q` 全绿（不修改测试）；新增等价断言覆盖提取后调用链。
  QA scenarios (name the exact tool + invocation): happy = 上述套件；failure = 故意把终态副作用提前到持久化前 → 既有测试/新增断言 FAIL（负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-26-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): preserve CAS/control-intent/PAUSED semantics during extraction
  Recommended task executor category: deep-low — 语义已由测试钉死，保真即可。

- [x] 27. B05-3 复用 plan/step identity、完成证据、resume_source 与 recovery fixtures；发布失败只重试发布；上游 blocked 阻断依赖者；诊断继续不作正常结果收养
  What: 提取 Runtime 规划/恢复用例时复用 `src/acp/calculations/identity.py:366,374`、`step_result.py::resolve_resume_source:385`、`executor.py:802,1383`、`step_requirements.py:170`、`result_publication.py:259,294` 的既有语义；`tests/baseline/recovery_fixtures/` 场景必须原样通过（README 语义不变）；发布失败重试仅发布（`executor.py:1146-1175`）；blocked/upstream_failed/diagnostic 语义不变（不冒充 completed）。Must NOT：不改 identity/检查点格式；不更新黄金值；不把旧 v1 检查点默认收养。
  Closes: GAP-6
  Parallelization: Wave 6 | Blocked by: 26 | Blocks: 30+（Receipt 复用身份）
  References (executor has NO interview context - be exhaustive): 并行计划二 B05-3；`tests/baseline/recovery_fixtures/README.md`；`tests/test_recovery_fixtures_smoke.py:474,502,530,562,768,796`；`tests/test_checkpoint_resume_semantics.py:533,575,592`；`tests/test_result_publication.py:73,114,168`
  Acceptance criteria (agent-executable): `pytest tests/test_recovery_fixtures_smoke.py tests/test_checkpoint_resume_semantics.py tests/test_result_publication.py -q` 全绿；发布失败→重试仅发布用例保持。
  QA scenarios (name the exact tool + invocation): happy = 上述套件；failure = 注入"发布失败即重算"分支 → 测试 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-27-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): reuse identity/resume/publication semantics as-is
  Recommended task executor category: deep-low — 复用+回归。

- [x] 28. B05-4（含 B28a/B28b，评审 R4）通过 Data 公共入口发布/写 markers/读工件；科学产品 frames/annotation adapter；保持 result_publication 与 completed 时序；retired 内部算法保留必要 shim 待 C 提取
  What: Runtime 侧所有工件发布/读取改经 todo 11 的 DataResourcePort（markers 写入、`result_publication.register_result_manifest` 调用面、`RESULT/frame_candidates.json` 经 Data）；保持契约 C 顺序（①step_result→②checkpoint→③publish→④marker）与 completed 时序（`executor.py:1187-1236`）；retired 内部引擎（Confsearch 协议引擎等）暂保持现有 shim（`src/acp/workflows/` legacy 引擎），登记待 C01/C06 提取。**子项拆分（评审 R4）**：**B28a=既有 Data 发布/markers/manifest 入口收敛**（保留原有行为与时序，不等待全部新科学产品）；**B28b=本次支持产品的 Data 登记/读取 + frames/annotation adapter + 必要历史结果兼容接线**（消费 C-T13 契约/fixture 与对应生产入口；输出经 `TrajectoryFrame.to_node()`/`TrajectoryAnnotation.to_annotation()`；由 A-GT3/B-T75 消费；按产品记录 生成入口/reader-writer/Data 发布/视图映射/前端消费者；同一固定输入贯穿 单位/atom_uid/frame/point/native mode/缺失原因 与 正常/空/部分/损坏/旧版本行为；科学算法仍由 C 唯一实现）。**衔接状态（评审 R4）**：C-T13“契约可消费”即允许 B28b 开工；“跨路端到端验收完成”等 B/A 接入后成立——双方不互相等待。Must NOT：Runtime 直写 Data 私有表/私有缓存；不删除 retired 内部算法（C 未提取前）；B28b 不复制科学算法/parser、不等待全部新科学产品才启动 B28a。
  Closes: GAP-6
  Parallelization: Wave 6 | Blocked by: 25；B28a 可先行；B28b 消费 C-T13 契约/fixture（跨路） | Blocks: 31—33（软依赖：回执含工件摘要）；B28b → A-GT3/B-T75 消费
  References (executor has NO interview context - be exhaustive): 并行计划二 B05-4；`src/acp/calculations/result_publication.py:224,234,259`；`src/acp/calculations/executor.py:1170-1236`；`src/acp/scheduler/remote/runner.py:1533`；`src/acp/workflows/`（legacy 引擎驻点）
  Acceptance criteria (agent-executable): `pytest tests/test_result_publication.py tests/test_architecture_invariants.py::test_all_new_results_register_result_manifest -q` PASS（B28a）；marker 上传路径经 Data port（调用断言）；**B28b**：adapter 经帧契约输出 + 固定输入贯穿用例（C 生成→B 发布投影→A 消费；A 侧未就绪时以 B 侧消费/冻结 fixture 记录并标 NOT_VERIFIED）；B28a/B28b 证据分列；retired shim 清单登记。
  QA scenarios (name the exact tool + invocation): happy = 发布链路端到端（fake）→ marker/manifest 一致；failure = 绕过 port 直写清单，或 B28b 绕过帧契约直发投影 → 架构断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-28-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): route publication/markers through Data entry; keep retired shims
  Recommended task executor category: unspecified-high — 入口替换 + 科学产品 adapter 接线。

- [x] 29. B05-5 按等价证据归并平台时间/原子写等纯 helper；禁万能 utils 与跨 owner 私表访问（CQ-02）
  What: 归并对象（先证明等价再合并）：`_utc_now_iso()` 多份拷贝（`src/acp/scheduler/{events,jobs,manager,migrations,projects,provenance,runner,stage_tasks,store,local_cleanup}.py`）、原子写 helper（`frame_candidate_store.atomic_write_text` 等）；归并位置限定在各自 owner 内（Runtime helper 入 `acp_services/runtime/`，Data helper 入 `acp_services/data/`）；不同精度常量（HARTREE/R）不合并（C07 决策）；无跨 owner 私表访问（AST 断言）。Must NOT：不创建万能 `utils.py`；不动数值常量；不跨 owner 合并。
  Closes: GAP-6
  Parallelization: Wave 6 | Blocked by: 25 | Blocks: 66（质量收口）
  References (executor has NO interview context - be exhaustive): 并行计划二 B05-5；主方案 CQ-02/§29.2；`src/acp/scheduler/AGENTS.md` ANTI-PATTERNS（`_utc_now_iso`）；`src/acp/results/frame_candidate_store.py:124-145`
  Acceptance criteria (agent-executable): 归并清单（旧符号→新位置→调用者）全部等价证据；`pytest tests/test_services_helper_merge.py -q` + 受影响套件绿；跨 owner 私表访问 AST 断言零命中。
  QA scenarios (name the exact tool + invocation): happy = 归并后全量定向套件；failure = 复制第三份 helper 的注入分支 → 断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-29-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): merge equivalent pure helpers per-owner (no universal utils)
  Recommended task executor category: deep-low — 等价归并+守护。

- [x] 77. B05-7（评审 P0-4）过渡实现迁移与注入移除：schema/store、控制与恢复、Data writer/cache、plan/step requirements、参数转换（按声明工作流范围分批）
  What to do / Must NOT do: 按 todo 72 的登记表逐项把被注入的旧实现迁入 `src/acp_services/`（唯一实现 + 旧路径同实现转发），并**移除注入**；覆盖运行时装配与 worker 独立启动所需闭包（schema/store、控制与恢复、Data writer/cache、plan/step requirements、参数转换）；**验收（评审 R4）**：实现迁移、注入移除与**模块行为等价**（controller/store 的持久化/CAS/恢复测试使用**隔离数据库**；“不连接 jobs DB”仅约束 worker 独立性探针，不适用整个 Services）；端到端新进程装配与 Envelope 成功/失败回执**由 todo 78 统一证明**——本任务不要求、也不提前消费 T78 的全链路启动证据；保留有明确定位的旧 CLI/import shim（shim 命名空间 ≠ 旧实现）。Must NOT：不复制第二份实现；不以注入端口长期替代迁移；不把 shim 与旧实现混为一谈。
  Closes: GAP-2、GAP-3（独立性部分）
  Parallelization: Wave 6 | Blocked by: 72、6、11、25、31（首批注入点） | Blocks: 78（独立装配）、F5
  References (executor has NO interview context - be exhaustive): 评审 P0-4；`src/acp/scheduler/store.py`、`src/acp/scheduler/migrations.py`、`src/acp/scheduler/manager.py`、`src/acp/calculations/step_requirements.py`、`src/acp/scheduler/remote/script_gen.py:26`（参数转换）、`src/acp/results/remote_structure_cache.py`（Data cache）
  Acceptance criteria (agent-executable): T72 登记表在声明范围内关闭率 100%；注入移除后对应旧实现删除条件核对；**模块隔离与迁移行为等价套件（隔离数据库上的持久化/CAS/恢复等价）PASS**；端到端独立启动证据归 todo 78（本任务不提前消费）。
  QA scenarios (name the exact tool + invocation): happy = 隔离数据库上的迁移等价套件 + 注入移除静态断言 → PASS；failure = 保留注入的对照分支 → 断言 FAIL（端到端启动证据见 todo 78）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-77-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services): migrate injected implementations in-package; remove injections (worker independence)
  Recommended task executor category: deep-high — 迁移闭包与独立性验收。

### Wave 7 — B06 ExecutionEnvelope/Receipt 与执行入口整链切换（30/31 可提前；32—34 需 C01）

- [x] 30. B06-1 定义 ExecutionEnvelope/Receipt schema/兼容版本：job/attempt/submission/release、owner/fencing、冻结 plan、Data 绑定/摘要、资源预算；TaskContext 不序列化
  What to do / Must NOT do: 在 `src/acp_services/contracts/envelope.py` 定义冻结 dataclass（PEP 604，`@dataclass(frozen=True)`）：字段=`schema_version`、job/attempt/submission（`submission_id` 语义同 `remote/submission.py:56`）、release（`release_id`/摘要，`remote/release.py:110,287`）、owner token + fencing、冻结 plan（step identity 快照，`identity.py:366/374`）、Data 绑定（StorageBinding 引用 + 工件摘要）、资源预算（nproc/mem/queue/时长，复用 `resolve_job_resources`）、协议能力；**协议选择按 attempt 冻结**（attempt 记录所选执行协议与 release 快照；新 attempt 才可选新协议；恢复时不得重选入口）；入口白名单与不支持能力的明确失败语义；`Receipt`=观测/完成回执字段（schema、job/attempt/submission、release、owner/fencing、plan/step identity、StorageBinding、证据摘要、错误）；兼容版本表（v1=现状隐式、v2=envelope 显式）。TaskContext（`cccp/calculation/context.py:41`）**不序列化**。Must NOT：不把平台身份塞进 `TaskRequest`；不建第二套任务/取消回调协议；不把 task 名称/组织等 UI 身份进 envelope。
  Closes: GAP-7
  Parallelization: Wave 7 | Blocked by: 1—3（可提前） | Blocks: 31—34、42+（诊断/操作身份）
  References (executor has NO interview context - be exhaustive): 主方案 §7.2/P4-04；并行计划二 B06-1；`src/acp/scheduler/remote/submission.py:56-219`；`src/acp/scheduler/remote/release.py:110,287,685`；`src/acp/calculations/identity.py:366,374`；`src/cccp/calculation/{requests,results,context}.py:1762/1387/41`
  Acceptance criteria (agent-executable): `pytest tests/test_services_envelope.py -q` PASS：往返序列化+版本兼容（v1/v2 样例）+ TaskContext 不可序列化断言（负例）；schema 字段表入契约文档。
  QA scenarios (name the exact tool + invocation): happy = 冻结 envelope → 往返 → 字段逐一断言；failure = 注入 TaskContext 回调字段 → 校验拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-30-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): ExecutionEnvelope/Receipt contracts (versioned, TaskContext never serialized)
  Recommended task executor category: deep-high — 跨执行边界契约与兼容版本。

- [x] 31. B06-2 worker 只执行冻结 plan/依赖/CCCP 调用并写回执；不直连 jobs DB、不自增 attempt；controller 校验资格后推进状态
  What: worker 侧运行器（`src/acp_services/runtime/worker.py`）：读 envelope → 校验冻结 plan/依赖（复用 `step_requirements.py:170`：过渡期由旧 acp 装配注入（仅过渡 controller）；**独立 worker 由 Services 自足装配入口（todo 78）从冻结数据构造、不依赖父进程注入**；台账登记迁移任务与移除条件（todo 77））→ 调用 CCCP 任务（`cccp.calculation.run_*`）→ 写 receipt（原子、幂等、attempt/submission 绑定）；worker **不 import jobs 存储/不写 DB/不自增 attempt**（AST 断言）；controller 侧校验 receipt（owner/fencing/attempt/release/摘要）后经 CAS 推进（`store.py:194-367`）；本地/远端共用接口。Must NOT：worker 越权改状态；controller 盲信 worker 结果（必须校验资格）；receive 后重复执行。
  Closes: GAP-7
  Parallelization: Wave 7 | Blocked by: 30、72 | Blocks: 32—34、48+（隔离执行）、78（独立装配）
  References (executor has NO interview context - be exhaustive): 主方案 §5.2/§7.2/P4-04；并行计划二 B06-2；`src/acp/scheduler/store.py:194-367`；`src/acp/calculations/step_requirements.py:170`；`src/acp/scheduler/remote/runner.py`（现有观测/reconcile 语义 `:670`）
  Acceptance criteria (agent-executable): `pytest tests/test_services_worker_contract.py -q` PASS：worker AST 禁 DB；receipt 校验矩阵（旧 attempt/submission/release 拒绝）；controller 推进仅在资格通过后。
  QA scenarios (name the exact tool + invocation): happy = 正常路径 receipt→CAS 推进；failure = 伪造旧 submission 的 receipt → 拒绝且状态不变。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-31-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): worker executes frozen plan + receipts; controller validates before advancing
  Recommended task executor category: deep-high — worker/controller 职责分离与资格校验。

- [x] 32. B06-3 整链切换：本地 runner、远端 script_gen、catalog 参数转换、release/节点依赖/软件发现/脚本字符串入口一次切换；禁新 Services 动态转调旧 `acp.cli`
  What: 切换清单（以"某工作流及其本地/远端支持路径的完整闭包"为切换单元，无中途双派发）：单一调度器按冻结的 workflow/release 能力表选择执行协议，并在 attempt 中记录该选择（新 attempt 可启用新协议；旧在途 attempt 沿原协议/release 接管；恢复时不重选入口）；本地 `src/acp/scheduler/runner.py`（`python -m acp.cli` 构造 `:6`/subprocess `:638`）→ envelope 执行入口；远端 `src/acp/scheduler/remote/script_gen.py:150,169`（`build_remote_cli_command`）→ worker 入口；控制端 `from acp.catalog import method_levels_to_cli_flags`（`script_gen.py:26`）→ 参数转换迁入 Services 侧显式转换器；release/节点依赖/软件发现/脚本字符串入口（`remote/runner.py:1260,1450-1462,1533,1981-2092`）统一经 envelope；旧 CLI 字符串入口仅保留 worker 自足入口；新 Services 禁 `subprocess`/字符串调旧 `acp.cli`（AST+字符串门）。**顺序（评审 R4）**：脚本、转换器等开发可提前；**实际启用子项等待 todo 78 的独立启动证据＋对应工作流闭包交付（C-T10a/滚动闭包）**。Must NOT：不半切（同一工作流闭包不能只切一半）；不为通过删护栏；不破坏远端"同步代码库执行"语义（节点运行 envelope 消费者）。
  Closes: GAP-7
  Parallelization: Wave 7 | Blocked by: 30—31、78；gate=C01 对应工作流闭包（**实际启用等待 T78＋对应工作流闭包交付**；脚本/转换器开发可提前） | Blocks: 33—34、41（完整 CLI 发行门）
  References (executor has NO interview context - be exhaustive): 并行计划二 B06-3；主方案 §10（禁反向 import/字符串绕过）/P4-05；`src/acp/scheduler/runner.py:6,638,1257`；`src/acp/scheduler/remote/script_gen.py:26,150,169`；`src/acp/scheduler/remote/runner.py:1260-1462,1533,1981-2092`；`tests/test_remote_phase1.py`；`tests/test_grep_gates_script.py:926`
  Acceptance criteria (agent-executable): `pytest tests/test_remote_phase1.py tests/test_remote_phase2.py -q`（mock 链路）PASS；`grep -rn "acp.cli" src/acp_services` 零命中且 worker 入口独立测试 PASS；同一 attempt 始终仅一个执行入口（唯一调度器断言）；一个已启用工作流与一个 legacy 工作流并行运行验证；无双派发断言（同 job 仅一个执行路径）。
  QA scenarios (name the exact tool + invocation): happy = 本地+远端(fake) envelope 链路端到端；failure = 注入 `python -m acp.cli` 转发 → 字符串门 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-32-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): switch local+remote execution chain to envelope/worker (no legacy CLI hop)
  Recommended task executor category: deep-high — 整链切换、远端身份/依赖兼容。

- [x] 33. B06-4 保留 bsub 前提交意图、scheduler markers、目录/release 身份、未知提交对账与旧在途 release 接管；旧 attempt/submission/release 回执拒绝
  What: 保真清单（测试钉死）：`_persist_submit_intent` 先于 bsub（`manager.py:3402`）；markers 上传 `remote/runner.py:1533`；目录身份（`paths.py:85`）；release 绑定先于 bsub（`runner.py:2119-2128`）；未知提交对账（租约有效不判 not_accepted，`submission.py:192`）；旧在途 release 接管（`release.py:810` 验证既有 release）；回执拒绝（envelope 校验旧 attempt/submission/release）；**回执拒绝与物理写保护分别验证**：未确认旧执行停止或隔离写入前，不得让新 attempt 复用会被旧进程改写的工作文件（旧 worker 落盘隔离/generation 复核）。Must NOT：不跳过 intent 持久化；不以超时/租约过期释放或重提；不删除旧 release（只 GC 按规则）。
  Closes: GAP-7
  Parallelization: Wave 7 | Blocked by: 32 | Blocks: 34、67
  References (executor has NO interview context - be exhaustive): 并行计划二 B06-4；`docs/reference/current/ACP_Job_File_Layout_Spec.md` §2b；`src/acp/scheduler/manager.py:3402`；`src/acp/scheduler/remote/runner.py:1533,2119-2128`；`src/acp/scheduler/remote/submission.py:192`；`src/acp/scheduler/remote/release.py:810,887`；`tests/test_remote_submission_protocol.py`
  Acceptance criteria (agent-executable): `pytest tests/test_remote_submission_protocol.py tests/test_remote_code_release_binding.py tests/test_remote_execution_integrity_live.py -q`（后者发动/integration 门控）PASS；未知提交/旧回执矩阵用例。
  QA scenarios (name the exact tool + invocation): happy = 提交意图→bsub→submitted 顺序断言；failure = 删除 intent 后模拟断线重连 → 对账失败（负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-33-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-runtime): keep submit-intent/markers/release identity + reconcile; reject stale receipts
  Recommended task executor category: deep-low — 清单式保真。

- [x] 34. B06-5 按 C01 闭包逐工作流启用；未满足切片保持旧兼容路径但不声称独立；最终覆盖 15 active 与本地专用限制
  What: 启用矩阵：15 个工作流 × {执行入口: envelope|legacy, C01 闭包状态, 启用证据}；每个工作流闭包就绪（C01 交付）后逐片切换；未满足保持旧路径（仅执行入口旧、状态机同一）；matrix 与台账同步；**两级退出门分开（评审 P1-3）**：①**T34a=选定工作流启用证据**（首批范围；在分类、身份与隔离证据齐全后即可执行，不等待全路线）②**T34b=15 项最终覆盖**（全量：覆盖 15 active（`test_architecture_invariants.py:91` 的集合）+ XtbPathSearch/OrcaGradient/XtbConstrainedOpt 本地专用限制（`pes2ts_*_request_v1` 冻结 payload）；未覆盖项明确标 legacy、不计完成）。Must NOT：不为追进度假装独立；不以首片代表全覆盖。
  Closes: GAP-7
  Parallelization: Wave 7 | Blocked by: 32；每工作流 gate=C01 对应闭包 | Blocks: 41（完整 CLI 发行门）
  References (executor has NO interview context - be exhaustive): 并行计划二 B06-5/退出门；并行计划三 C01（前置）；`src/acp/workflows/registry.py:38-150`；`src/acp/scheduler/jobs.py:146,178`；根 `AGENTS.md` 工作流清单
  Acceptance criteria (agent-executable): 启用矩阵文档 + **T34a 与 T34b 证据分列**：T34a=选定工作流切换后定向测试 PASS；T34b=15 active 覆盖检查脚本 PASS（全量门）；未切换项明确标 legacy（不得声称独立）。
  QA scenarios (name the exact tool + invocation): happy = 首个闭包工作流（如 singlepoint）envelope 启用回归；failure = 对未就绪工作流强制 envelope → 明确拒绝（负例）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-34-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): per-workflow envelope enablement driven by C01 closure
  Recommended task executor category: deep-low — 矩阵驱动逐片切换。

- [x] 78. B06-6（评审 P0-4）Services 自足装配入口：worker composition root 从冻结数据构造依赖（不连 jobs DB）
  What to do / Must NOT do: 在 `src/acp_services/` 内新增 worker 装配入口（composition root）：本地/远端 worker 由 Envelope 在**新进程**启动时，仅凭冻结数据（envelope + StorageBinding + 只读工件）构造所需依赖；**不依赖父进程注入对象、不连接 jobs DB**（该约束仅用于 worker 独立性探针；controller/store 的持久化/CAS/恢复测试使用隔离数据库）；过渡 controller 仍可由旧 ACP 装配（与独立 worker 区分）；与 todo 31/77 联动。Must NOT：远端启动再加载旧 ACP 实现冒充独立；worker 接触 jobs DB；第二套任务状态机。
  Closes: GAP-7（独立性）、IS-6
  Parallelization: Wave 7 | Blocked by: 31、77 | Blocks: F1/F5（独立性证据）、76
  References (executor has NO interview context - be exhaustive): 评审 P0-4；`src/acp_services/contracts/envelope.py`（todo 30）；`src/acp/scheduler/remote/script_gen.py:150,169`（远端入口）
  Acceptance criteria (agent-executable): 新进程仅凭冻结 Envelope/StorageBinding/工件完成装配；成功+结构化失败回执均验证；远端 mock 链路；`pytest tests/test_services_worker_bootstrap.py -q` PASS（探针以 worker 为界：不连 jobs DB；controller/store 测试使用隔离数据库）。
  QA scenarios (name the exact tool + invocation): happy = 独立子进程按 Envelope 装配并执行 → PASS；failure = 依赖父进程注入的对照 → FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-78-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): self-sufficient worker composition root (from frozen envelope, no jobs DB)
  Recommended task executor category: deep-high — 独立装配与远端进程边界。

### Wave 8 — B07 完整 CLI / 独立角色 / acp init 与配置应用（35—38 依 W6；40a 需 C39a（可先行）、40b 需完整 C08；41 发行门=32—34）

- [x] 35. B07-1 保留 `acp run`/`python -m acp` 与旧参数；CLI/Web 共用 Services 用例；无前端/Web/在线账本可完成本地摄入/计划/执行/控制/恢复/导出
  What: CLI 等价迁移：`src/acp/cli.py`（dispatch `:4129-4149`、`build_parser:2283`、retired `:3768`、`serve:3672`、`doctor:4020`、`init:4093`）在保持参数/退出码/输出行为前提下，经 Runtime/Workspace/Data 用例（todo 25 等）；**保持 `cli.main()` 内 `dispatch` 字典结构**（`tests/test_architecture_invariants.py:55-88` AST 解析）或按修订流程登记新门；离线本地闭环验收：摄入（`src/acp/intake/`）→ 计划（plans）→ 执行（本地）→ 控制（pause/cancel/continue/rerun）→ 恢复（resume_source）→ 导出（结果读取），无浏览器/Web 服务/在线账本（**完整 CLI 真实入口与持久 controller 连接由 todo 74 承接**；本 todo 证明 CLI 等价与旧参数兼容，并准备公开命令）。Must NOT：不改变旧参数默认值与语义；不把 Web 依赖引入 CLI；不破坏 retired 拦截。
  Closes: GAP-2
  Parallelization: Wave 8 | Blocked by: 25—29 | Blocks: 41、42+（机器接口）
  References (executor has NO interview context - be exhaustive): 主方案 §19.1/P4-06；并行计划二 B07-1；`src/acp/cli.py:2283,3672,3768,4020,4093,4107,4129-4149`；`tests/test_architecture_invariants.py:55-88`；`tests/test_acp_cli.py`（若存在被收集）；`src/acp/__main__.py`
  Acceptance criteria (agent-executable): `pytest tests/test_architecture_invariants.py::test_each_active_workflow_has_dispatch_and_method_schema -q` PASS；本地闭环 scripted E2E（无网/无前端）PASS；旧 CLI 冒烟使用**真实安装入口**（subprocess 调 `acp` console script 或 `python -m acp`，非 mock）；完整 CLI 闭环声明归 todo 74（本 todo 不得单独宣称完整 CLI 已证明）。
  QA scenarios (name the exact tool + invocation): happy = 离线闭环脚本 → 全步骤 PASS；failure = 断网条件下执行 → 无网络依赖断言 PASS（对照：注入 Web 调用 → FAIL）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-35-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-cli): CLI shares Services use cases; offline local loop proven
  Recommended task executor category: deep-high — CLI 行为等价 + 闭环验收。

- [x] 36. B07-2 统一 init：使用方式/连接服务、部署档位、组件/能力、配置/secret 引用、计划预览/应用、doctor；不建第二向导/defaults
  What: 复用 `src/acp/init_wizard/`（`flows.py::run_init:348-385`、`persist.py:98-147`）演进为统一交互：步骤=使用方式（本机/连接已有服务）→ 档位（Standalone/Lab/Hosted，未验证不可启用）→ 已有资源（软件/Local/LSF/DB/HTTPS/存储）→ 组件组合（默认不装支付/对象存储/外部 IAM）→ 参数与秘密（secret 引用，不入计划/日志）→ 预览与应用（差异/来源/回退）→ doctor 能力报告（PASS/FAIL/NOT_VERIFIED 分列，`cli.py:4020-4090`）；配置单一来源沿用 `cccp/config.py` 与 `resolve_run_root`（`core/paths.py:72`）。Must NOT：不另建向导/defaults；不改现有 init 参数/目标配置文件兼容；Hosted 不默认升级。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: 25—29 | Blocks: 37—39、52（Lab init）
  References (executor has NO interview context - be exhaustive): 主方案 §36.1/§36.2/§36.3/IN-01；并行计划二 B07-2；`src/acp/init_wizard/flows.py:124-385`；`src/acp/cli.py:2317-2331,4093-4106`；`src/acp/init_wizard/persist.py:39-147`；`tests/test_acp_init_*.py`（8 个既有文件）
  Acceptance criteria (agent-executable): 步骤流单测 + 既有 init 测试迁移后全绿（`pytest tests/test_acp_init_cli.py tests/test_acp_init_persist.py -q` 等）；无第二 defaults（grep 断言）；非交互回答文件路径可用。
  QA scenarios (name the exact tool + invocation): happy = 非交互回答文件跑通 Standalone 配置→doctor；failure = 缺组件依赖时档位启用被拒（不静默降级）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-36-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-cli): unified acp init (usage→tier→components→plan/apply→doctor)
  Recommended task executor category: deep-high — 向导统一 + 兼容 + 配置单一来源。

- [x] 37. B07-3 提取无 Paramiko 的节点配置纯校验；交互/非交互共用 schema；本地无 remote extra 可用；无 TTY 不等待；保留未知字段与环境覆盖
  What: 把 `src/acp/init_wizard/flows.py::_node_entry_error:68-88` 的镜像校验与权威 `src/acp/scheduler/remote/config.py::RemoteNode.from_config_dict:162-209` 合并为**单一纯校验模块**（无 paramiko 依赖，固定为 `src/acp_services/contracts/config_validation.py`），交互/非交互共用；本地安装无 `[remote]` extra 可运行（导入隔离测试）；无 TTY 不阻塞（自动/明确错误码）；保留未知字段与环境覆盖语义（`cccp/config.py:531-583`）。Must NOT：不保留两份校验；不把 paramiko 引入 core 路径；不改变既有错误条目可见性（core-only 导入行为）。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: 36 | Blocks: 39、52
  References (executor has NO interview context - be exhaustive): 主方案 §36.3/CQ-02/IN-02；并行计划二 B07-3；`src/acp/init_wizard/flows.py:54-106`；`src/acp/scheduler/remote/config.py:162-209`；`tests/test_acp_init_config_model.py tests/test_acp_init_newnode_flow.py`
  Acceptance criteria (agent-executable): 镜像删除（`grep _node_entry_error` 零命中或纯转发）；`pytest tests/test_acp_init_config_model.py -q` + 新纯校验套件 PASS；无 remote extra 环境下 `python -c "from acp.init_wizard.flows import run_init"` 可导入。
  QA scenarios (name the exact tool + invocation): happy = 同输入经交互/非交互两条路径得到同一校验结论；failure = 非法节点配置 → 同一错误条目（一致性断言）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-37-acp-parallel-plan-2-services.md`
  Commit: Y | refactor(services-cli): single Paramiko-free node config validation (shared interactive/non-interactive)
  Recommended task executor category: deep-low — 提取+一致性。

- [x] 38. B07-4 配置计划带版本/摘要；应用检查并发修改、原子文件/受限备份；跨系统操作持久分阶段与幂等恢复；明确部分完成与回退，不承诺全原子回滚
  What: init 计划对象（version/digest/差异/外部动作清单/回退步骤）；应用前检查配置摘要/版本（防覆盖并发修改，复用 `persist.py:98-147` 的原子写+.bak）；跨系统动作（远端目录/系统服务/DB）持久阶段+幂等键，失败恢复重放（不重复建默认账户/命名空间）；输出"已完成/未完成"与手工恢复步骤。Must NOT：不宣称全原子回滚；不静默覆盖未知字段；不扩大远端权限。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: 36 | Blocks: 39、68
  References (executor has NO interview context - be exhaustive): 主方案 §36.4/IN-05；并行计划二 B07-4；`src/acp/init_wizard/persist.py:98-147`；`src/acp/init_wizard/newnode.py:668`
  Acceptance criteria (agent-executable): `pytest tests/test_services_config_apply.py -q` PASS：并发修改拒绝、重放幂等、部分完成报告；只读目标/损坏配置/取消用例。
  QA scenarios (name the exact tool + invocation): happy = 计划→应用→doctor（重放同一计划无副作用）；failure = 摘要变更后应用 → 拒绝并提示。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-38-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-cli): config plan/apply (versioned, concurrent-safe, idempotent staged recovery)
  Recommended task executor category: deep-low — 计划/应用语义明确。

- [x] 39. B07-5 分离配置/依赖安装/远端 bootstrap/系统服务/身份/DB 迁移动作；具体外部动作计划内审查授权；有在途任务/预留时阻止不兼容模式切换
  What: 动作分类与授权边界：纯配置写入 vs 安装依赖 vs 远端 bootstrap vs 创建系统服务/用户 vs DB 迁移（各动作在计划中显式列出、逐项授权，`§36.4`）；模式切换守卫：存在在途任务/预留时阻止不兼容切换（execution mode、存储根、身份 provider、DB、计量模式），提示先处理（复用 `manager.py:5477` 启动恢复的判定素材）。Must NOT：不借升级隐式扩大权限（不做未列出的远端/系统动作）；不在在途任务期间切换。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: 36—38 | Blocks: 52、65（Hosted 组合门）
  References (executor has NO interview context - be exhaustive): 主方案 §36.4/§20/§23.3/§26.4；并行计划二 B07-5；`src/acp/scheduler/manager.py:5477`；`src/acp/init_wizard/flows.py:254-345`
  Acceptance criteria (agent-executable): `pytest tests/test_services_action_gating.py -q` PASS：五类动作授权矩阵 + 在途任务阻断用例；未授权动作零执行断言。
  QA scenarios (name the exact tool + invocation): happy = 仅配置写入不触发外部动作；failure = 在途任务时请求切换计量模式 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-39-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-cli): action gating (config/deps/bootstrap/services/identity/DB) + in-flight switch guard
  Recommended task executor category: deep-low — 授权矩阵清晰。

- [x] 40. B07-6 与 C08 联合调整 wheel/角色依赖、模型/notice 与节点依赖；Web/IAM/账单不进科学最小安装；静态资源发行缺口单独验证
  What: 打包调整（B 为集成写者，应用 C08 输入）分子项（评审 P0-3）：①**B40a 本批打包维护**（前置=C39a 首批构建/资产变更规格（C00 登记 + 本批科学变更规格：精确构建与 package-data 补丁、资产摘要、验证命令）与对应迁移补丁就绪；交付=与科学变更组成一个集成批的应用回执；**不等待完整 C08**）：资产路径、`package-data`、节点依赖、打包测试跟随相应 C 迁移（C 请求+出树预验 → B 应用 → C 在合并提交重建/重验 → B 纳入 release 回执链），避免源码可跑而安装包漏资产的窗口；`pyproject.toml`（`include:74-76`、extras `:40-69`、console script）与 `requirements-node.txt` 漂移锁定保持；②**B40b 完整发行**（前置=声明范围内迁移完成；交付=最终发行清单、文件归属与角色依赖证据）：冻结独立 CCCP 发行配置、Services/CLI 发行归属与兼容 ACP 包的文件所有权（避免多 wheel 重复安装同一 Python 文件；单仓多构建配置由 B/C 在 T3/C39 明确）。**「补丁齐备」与「集成已应用」分开登记；B 不得在科学源码尚未准备时先改 package-data；C 不得在 B 未开工时先完成安装验收。** Web/IAM/账单组件不误入科学最小安装；**已声明可独立安装的 Web 发行所需静态资源须有交付任务（缺陷修复或明确缩小发行声明），不能仅登记缺口**。Must NOT：不改 C08 的科学依赖结论；不把候选组件加入依赖；不新增 `python -m cccp`；不在 C 迁移落地前先行调整 package-data 造成中间窗口。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: B40a: C39a 规格与对应迁移补丁就绪（可随迁移先行）；B40b: 35—37、声明范围内迁移完成（完整 C08） | Blocks: 41、65
  References (executor has NO interview context - be exhaustive): 主方案 §28.3/P6-01/PR-01；并行计划二 B07-6；并行计划三 C08；`pyproject.toml:6-76`；`requirements-node.txt:1-45`；`tests/test_packaging_assets.py`
  Acceptance criteria (agent-executable): 构建 wheel 并检查 include 内容（`python -m build --wheel` 或 `pip wheel .`）；角色依赖矩阵验证脚本 PASS；`tests/test_packaging_assets.py` PASS；**B40a 与 B40b 证据分列**（集成批应用回执 vs 最终发行清单）；滚动维护应用证据（每批 C 迁移的 apply 回执）；最终构建配置与验证命令入仓可复跑；wheel 文件归属清单无重复；`include` 匹配不证明角色隔离——独立发行由无旧实现的安装产物验证（见 todo 72/77）。
  QA scenarios (name the exact tool + invocation): happy = wheel 内 `acp_services*` 存在 + 最小角色无需多余依赖（子集安装导入测试）；failure = 从 wheel 移除 `acp_services` 后测试 FAIL（负例证明门有效）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-40-acp-parallel-plan-2-services.md`
  Commit: Y | chore(packaging): role deps + models/notice + node deps alignment (C08 joint)
  Recommended task executor category: unspecified-low — 按清单执行+验证。

- [x] 41. B07-7 可复现 Standalone 配方与 capability 报告；installed/configured/enabled/verified 分开；未验 Hosted 不展示可生产启用
  What: 产出 `docs/reference/next-change/ACP_Services_Standalone_Recipe.md`：干净环境步骤（install→init→doctor→首个本地科研闭环），每步 expected/actual 与 `NOT_VERIFIED` 标注；capability 报告四态分列（installed/configured/enabled/verified）；**退出门两级分开**：①首批范围退出门（第一主场景闭环可用）②完整发行退出门（15 工作流覆盖 + 完整发行验收；未达即保持开放，不阻塞已满足的独立交付）；**顺序修正（评审 P1-3）**：**B41a 配方设计可先做**（不依赖完整发行）；**真实安装与完整 CLI 复现（B41b）必须在 todo 74 交付后执行**（T74 不再等待本任务）。Hosted 项在未验证前不得展示为可生产启用（文案与 UI 支持矩阵部分由 A 承接，此处只交报告与门）。Must NOT：不把"向导结束"当验收；不宣称 Hosted 可用；不以首批范围代表完整发行。
  Closes: GAP-8
  Parallelization: Wave 8 | Blocked by: B41a: 35—38；B41b: 74、40、32—34（按 T34a 声明范围；T34b 为完整发行门） | Blocks: 69（分层验收）
  References (executor has NO interview context - be exhaustive): 主方案 §35-M1（退出门）/§36.2-7；并行计划二 B07-7；`scripts/install_systemd.sh:32-51`；`scripts/start_acp.sh:39`；`docs/reference/current/ACP_Function_Test_Manual.md`
  Acceptance criteria (agent-executable): 配方在干净 venv 复现（或明确标 NOT_VERIFIED 及原因）；capability 报告四态字段齐全。
  QA scenarios (name the exact tool + invocation): happy = 按配方执行全步骤记录；failure = 缺外部科学软件时 typed unavailable 呈现（不假成功）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-41-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): standalone recipe + capability report (installed/configured/enabled/verified)
  Recommended task executor category: unspecified-low — 文档+复现记录。

- [x] 74. B07-8（评审修订 B7）完整 CLI 真实入口与持久 controller/worker 连接：公开本地任务管理命令；进程重启后仍能控制/恢复原任务
  What to do / Must NOT do: 定义并实现本地任务管理公开命令（复用 Services 用例）：摄入/计划/执行/查询/暂停/继续/取消/原地 rerun/恢复/导出；可持续的 controller/worker 连接方式（CLI 不是仅内存 manager 的测试脚本——进程重启后能找到原任务并控制/恢复，复用 run_root + job 记录 + 恢复路径）；保留 `acp run`、retired 拦截与旧参数语义（todo 35 的等价前提）。本 todo 是 IS-7「完整 CLI」声明的唯一证明方。Must NOT：不依赖浏览器/Web 服务；不复制第二套任务实现；不改变既有 CLI 参数语义。
  Closes: GAP-8, IS-7
  Parallelization: Wave 8 | Blocked by: 35—38、32—34（按 T34a 声明范围；运行/worker/init 依赖；不再等待 41） | Blocks: F3、75
  References (executor has NO interview context - be exhaustive): 评审 B7；主方案 §19.1；`src/acp/cli.py:2283,4107-4166`；`src/acp/scheduler/manager.py`（恢复路径）；`src/acp/__main__.py`
  Acceptance criteria (agent-executable): 命令行（真实 `acp` 入口）完成全流程（无浏览器/Web）；进程重启后对原任务 pause/continue/cancel/rerun/recover 成功；`pytest tests/test_services_cli_full_loop.py -q` PASS；旧参数冒烟对拍。
  QA scenarios (name the exact tool + invocation): happy = 冷启动 CLI 全流程 → PASS；failure = 仅测试脚本可取内存 manager 的对照 → 重启后控制失败（负例证明持久连接）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-74-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-cli): full CLI loop via real entry + persistent controller/worker connection
  Recommended task executor category: deep-high — 真实入口闭环与持久连接。

### Wave 9 — B08 机器接口 / MCP / 项目包 / 研究证据（随 M2：需 15/22/30）

- [x] 42. B08-1 统一 JSON 输出、退出码、错误 code/params/retryable/诊断引用；dry-run/estimate 不启动科学程序、不消费核时
  What to do / Must NOT do: 在 CLI/机器接口层定义统一输出契约：`--json`（或等价）结构化结果（操作身份、状态、错误对象：稳定 code、结构化 params、可安全显示 message、retryable、诊断引用/trace 链接）、稳定退出码表（0/2/3/4… 与错误类别映射，保持旧 0/1/2 兼容语义，`src/acp/cli.py` 现状 0/1/2/130）；`dry-run/estimate` 子路径：仅计划/校验/估算（不调用 `cccp.calculation.run_*`、不启动科学软件，断言清单）；错误对象为 B08-6/24 提供引用。Must NOT：不改变现有文本输出行为（新增模式）；不让 dry-run 触发传输/计算；不泄露路径/秘密。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 35（CLI 用例）、30（操作身份） | Blocks: 43、47、60（三入口一致）
  References (executor has NO interview context - be exhaustive): 主方案 §19.2/PR-03/§33.3；并行计划二 B08-1；`src/acp/cli.py:4107-4166`（现有返回码）；`docs/reference/current/ACP_CCCP_Task_API_DevDoc.md` §10（完成/失败/部分/记录身份契约）
  Acceptance criteria (agent-executable): `pytest tests/test_services_machine_output.py -q` PASS：错误对象 schema + 退出码表 + dry-run 零副作用（mock 断言 run_* 未调用）；旧模式兼容对拍。
  QA scenarios (name the exact tool + invocation): happy = `acp run singlepoint --json --dry-run`（或等价）→ 输出 JSON + 不执行；failure = dry-run 中注入执行调用 → 断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-42-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-machine): unified JSON/exit-code/error contract + dry-run/estimate (no science, no metering)
  Recommended task executor category: deep-low — 契约明确。

- [x] 43. B08-2 MCP 薄入口：协议/客户端矩阵、工具 allowlist、身份/预算/幂等；长任务返回 operation/job；不暴露任意 shell；连接寿命不决定任务存活
  What: `src/acp_services/entrypoints/mcp.py`（薄适配，复用 CLI/API 用例）：tools/list + tools/call 按锁定协议版本；工具集=能力发现、预检/估算、受控提交、状态/取消、工件准备/读取、显式分析；身份映射 Workspace、预算上限、幂等键由服务端强制；长任务立即返回 operation/job 引用（查询式）；连接断开不影响任务；stdin/stdout 本地权限范围显式。Must NOT：不做任意 shell/函数名分派；不把长期 Key 传给工具；不让会话生命周期决定作业存活。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 42 | Blocks: 60、A06（界面）
  References (executor has NO interview context - be exhaustive): 主方案 §33.1/PR-04；并行计划二 B08-2；`src/acp/cli.py:4107`（用例复用）；`src/acp/api/v1_routes.py`（任务端点语义）
  Acceptance criteria (agent-executable): `pytest tests/test_services_mcp.py -q` PASS：协议方法/allowlist/身份/幂等/长任务引用；至少一个 MCP 客户端联调记录（真实或协议级 mock，标 NOT_VERIFIED 如未联调）。
  QA scenarios (name the exact tool + invocation): happy = 提交→operation 引用→查询→取消全链路；failure = 越权工具调用/无预算 → 服务端拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-43-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-machine): MCP thin entry (allowlist/identity/budget/idempotency)
  Recommended task executor category: unspecified-high — 协议适配+安全边界。

- [x] 44. B08-3 Data archive schema/一致快照/逐文件摘要（大小/相对路径）；完整/产品/引用包；排除凭据与可用主机路径
  What: `src/acp_services/data/archive.py`：版本化包 manifest（逐文件 sha256/大小/类型/相对路径/来源），导出类型=完整包/仅产品包/引用包（明确缺失内容，不宣称引用包自包含）；**一致快照**：固定 generation/revision 的已提交工件，或先建立受控快照再生成 manifest；导出时工件持续变化 → 重试或显式部分包（不伪装一致）；在途任务只输出明确时点事实、不输出执行权；排除密码/会话/API Key/SSH 秘密/支付信息/可直接使用的宿主路径（扫描断言）。Must NOT：不把扩展名当格式契约；不导出凭据；不接管在途执行。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 15（读写根） | Blocks: 45、A06
  References (executor has NO interview context - be exhaustive): 主方案 §32.1；并行计划二 B08-3；`src/acp/storage/manifest.py:139`（原子写素材）；`src/acp/results/frame_candidate_store.py:124`
  Acceptance criteria (agent-executable): `pytest tests/test_services_archive_export.py -q` PASS：三类包 schema + 摘要校验 + 凭据排除扫描；快照一致性用例（在途任务）。
  QA scenarios (name the exact tool + invocation): happy = 导出→逐文件校验摘要；failure = 注入凭据文件 → 排除断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-44-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): versioned archive export (digests, package classes, credential exclusion)
  Recommended task executor category: deep-low — 清单式导出。

- [x] 45. B08-4 导入限额暂存→路径/链接/压缩放大/schema/摘要校验→原子登记新平台 ID/StorageBinding；幂等；保留科学来源映射；不收养外部活跃任务/lease/计费；不自动执行
  What: `src/acp_services/data/import_.py`：限额暂存（字节/inode/压缩放大/深度/符号链接），校验 schema/摘要/路径穿越/归档炸弹；**持久 staging/import 记录**，明确 DB 提交与文件发布的恢复顺序（各阶段崩溃可恢复/可对账）；原子登记：新平台 ID + StorageBinding + source_namespace/source_id 映射（保留科学内容身份）；重复导入幂等、不覆盖已有项目/当前 attempt；外部任务状态仅作来源记录（不收养活跃任务/lease/计费，不重放）；若需要平台任务投影，由 Runtime 公共用例登记历史记录（不收养外部执行权）；导入后**不自动执行**科学程序/反序列化 Python 对象。Must NOT：不合并同摘要的不同权限；不导入执行权；不存在可见的半登记成功。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 44 | Blocks: A06、67（故障注入）
  References (executor has NO interview context - be exhaustive): 主方案 §32.1（必测清单）；并行计划二 B08-4
  Acceptance criteria (agent-executable): `pytest tests/test_services_archive_import.py -q` PASS：本地→导入→读取往返、重复导入幂等、损坏/危险归档拒绝、来源映射保留；零自动执行断言。
  QA scenarios (name the exact tool + invocation): happy = 往返完整包→新 ID 登记→读取一致；failure = 归档炸弹/路径穿越 → 拒绝且无部分登记。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-45-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-data): archive import (staged validation, atomic registration, no auto-execution)
  Recommended task executor category: deep-low — 安全校验+幂等登记。

- [x] 46. B08-5 Runtime/Data 形成只读证据投影（输入/产生/派生/选择修订/复用）；科学派生字段由 C02 提供；人工修订不覆盖原结果
  What: `src/acp_services/runtime/lineage.py` + Data 侧来源边：只读研究溯源投影（边类型：使用/产生/派生/人工选择或修订/复用；保留原子/帧/模式映射），消费 C02 科学派生字段（字段映射文档由 C02 交付；本 todo 只做平台侧边与投影）；修订版本化记录，不覆盖原科学结果。Must NOT：不建第二任务状态机；不自行发明科学字段含义；不把人工建议写回权威结果。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 19、22；字段=C02 | Blocks: A06、69
  References (executor has NO interview context - be exhaustive): 主方案 §32.2；并行计划二 B08-5；`src/acp/api/v2_routes.py:1487`（lineage 现有端点）；`src/acp/storage/manifest.py:41-85`（产品身份素材）
  Acceptance criteria (agent-executable): `pytest tests/test_services_evidence_projection.py -q` PASS：五类边 + 修订版本化 + 原结果不被覆盖断言；与 C02 字段映射 diff 记录。
  QA scenarios (name the exact tool + invocation): happy = 构造 使用→产生→修订 链 → 投影可重建；failure = 修订覆盖原结果 → 断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-46-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-runtime): read-only evidence projection (lineage edges, versioned revisions)
  Recommended task executor category: unspecified-high — 投影建模+跨路字段。

- [x] 47. B08-6 结构化日志与诊断导出先落地；OpenTelemetry 可选；业务身份与 trace 分开；秘密脱敏；中英不改变方法枚举/科学 hash
  What: 结构化日志（JSON 行：时间/级别/logger/业务关联 job_id/attempt/operation/错误 code；默认脱敏路径/密钥/URL 参数）；诊断导出（脱敏包：日志+系统能力+最近操作）；OTel 适配留可选接口（默认不启用、不常驻服务）；业务关联（持久）与 trace/span（诊断）分离、重试可新 trace 并链接既有操作；i18n：错误 code/params 稳定，中英切换不改请求 hash/计划/科学结果（枚举 token 不翻译）。Must NOT：日志/监控成为结算/恢复权威；不泄露秘密；翻译不改科学标识。
  Closes: GAP-9
  Parallelization: Wave 9 | Blocked by: 42 | Blocks: 69（运维证据）
  References (executor has NO interview context - be exhaustive): 主方案 §33.2/§33.3；并行计划二 B08-6；`src/acp/api/server.py:228-237`（现状仅异常日志）；`src/acp/cli.py:43-50`（现状 logging）
  Acceptance criteria (agent-executable): `pytest tests/test_services_logging.py -q` PASS：字段 schema + 脱敏断言 + i18n 不变性（同请求中文/英文产出相同 hash/计划）。
  QA scenarios (name the exact tool + invocation): happy = 生成诊断包并校验脱敏；failure = 注入密钥串到日志 → 脱敏断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-47-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services): structured logging + diagnostic export (redacted, i18n-invariant)
  Recommended task executor category: unspecified-high — 日志契约+脱敏+国际化不变性。

### Wave 10 — B09 Lab Server 开通/隔离/管理员（gate=M3 进入；需 6—10/25—29/30—34；身份生命周期=73）

- [x] 48. B09-1 Runtime 消费 Workspace 开通意图；幂等调用 Data.ensure_namespace；提交环境状态；失败可对账
  What to do / Must NOT do: `src/acp_services/runtime/provisioning.py`：消费 Workspace 的持久开通意图（个人空间/默认项目/命名空间），幂等调用 `Data.ensure_namespace`（新 Data 用例，原子建绑定），提交环境状态（provisioned/failed）并审计；失败重试/对账（不重复建默认空间）；只读注册接口不暗中 SSH/建系统账户。Must NOT：Workspace→Data 反向调用；重复开通建第二空间；失败静默。
  Closes: GAP-10
  Parallelization: Wave 10 | Blocked by: 6—10、12、25—29、30—34（M3 gate） | Blocks: 49、52
  References (executor has NO interview context - be exhaustive): 主方案 §22.2/MU-03；并行计划二 B09-1；`src/acp/scheduler/projects.py`（默认项目现状 `ensure_default_project`）
  Acceptance criteria (agent-executable): `pytest tests/test_services_provisioning.py -q` PASS：幂等（重复开通单空间）、失败对账、零系统账户副作用。
  QA scenarios (name the exact tool + invocation): happy = 开通→状态提交→重放无副作用；failure = 中途失败→重试→对账无重复。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-48-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-lab): idempotent provisioning (Workspace intent → Data namespace → state)
  Recommended task executor category: deep-low — 幂等编排。

- [x] 49. B09-2 版本化用户/项目/Key 授权共同约束核数/内存/并发/排队/时长/节点/队列；派发前原子预留/重验；PAUSED 保留实际占用
  What: 资源准入实现：`src/acp_services/runtime/admission.py` 消费 ResourceGrant（todo 7 契约）→ 有效资源=用户授权∧项目授权∧Key 子预算∧节点/队列限制∧实际容量；派发前原子预留/重验（并发不超用：读-预留分离禁止）；排队不预占 CPU 但可预留存储/预算；PAUSED 计实际内存/磁盘占用（不视作零资源）；拒绝理由结构化；**有效上限必须可执行**（核数/walltime/硬限制能力/政策版本）；无法给出上限时拒绝 quota-only 准入（只能显式 usage-only）。Must NOT：不给超配任何静默放宽；不在派发后才发现超限。
  Closes: GAP-10
  Parallelization: Wave 10 | Blocked by: 48 | Blocks: 53—55（核时/配额联动，软依赖）、62+（执行角色）
  References (executor has NO interview context - be exhaustive): 主方案 §22.4/MU-05/OS-06；并行计划二 B09-2；`src/acp/scheduler/manager.py`（submit 拒绝路径）；`src/acp/scheduler/store.py:194`（CAS 预留素材）；PAUSED 语义 `scheduler/AGENTS.md`
  Acceptance criteria (agent-executable): `pytest tests/test_services_admission.py -q` PASS：并发预留竞争不超用、多 Key 不叠加、PAUSED 占用、拒绝理由；原子预留断言。
  QA scenarios (name the exact tool + invocation): happy = 并发提交竞争 → 不超配；failure = 绕过准入直派发 → 架构断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-49-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-lab): resource admission (atomic pre-dispatch reserve, PAUSED occupancy)
  Recommended task executor category: deep-high — 并发预留与容量约束。

- [x] 50. B09-3 验证 Local OS/容器/资源组与 LSF 身份映射、最小权限与停止范围；不以 OMP_NUM_THREADS、同 UID 分目录或登录页代替隔离
  What: 隔离 adapter 验证（逐档）：Local=受限 OS 身份/受控 sandbox/cgroup v2 资源限制（进程组绑定 envelope）；LSF=平台身份→执行账号/队列/资源策略映射持久绑定、最小挂载、停止范围核对（bkill/bstop 只及本作业）；输出能力矩阵（支持的隔离级别/实测/未测）。Must NOT：同 UID 分目录/仅设 OMP_NUM_THREADS/登录页当隔离；无隔离能力时不启用依赖档位（拒绝而非降级）。
  Closes: GAP-10
  Parallelization: Wave 10 | Blocked by: 49 | Blocks: 65（Hosted 隔离门）
  References (executor has NO interview context - be exhaustive): 主方案 §22.5/MU-06/OS-06；并行计划二 B09-3；[cgroup v2 官方文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)（外部依据）；`src/acp/scheduler/remote/monitor.py:295-349`（bkill/bstop 匹配现状）；`AGENTS.md` ANTI（同 UID 说明）
  Acceptance criteria (agent-executable): 隔离矩阵 + 每档实测记录（无环境处 NOT_VERIFIED + 拒绝启用断言）；跨用户读写/停止范围负例测试。
  QA scenarios (name the exact tool + invocation): happy = 两用户任务资源受限且互不可见（本地实测或明确 NOT_VERIFIED）；failure = 请求无隔离能力档位 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-50-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-lab): execution isolation adapters (OS/container/LSF) + capability matrix
  Recommended task executor category: deep-high — 系统隔离与能力门。

- [x] 51. B09-4 管理用例/审计区分账户等级与角色；普通 Key 无管理权；broker 只提供受限操作；禁止任意 root shell
  What: 管理面：管理员用例清单（用户/成员/角色、等级/配额、节点/队列/活动任务、存储/使用、审计/凭据撤销）每入口重鉴权；审计记录执行人/对象/前后版本/原因/时间（复用 `organization_events` 模式）；等级≠角色（升级不获管理权）；普通 Key 管理端点 403；受控 broker 仅限白名单操作（建系统用户/quota/启动隔离环境的安装期配置），禁任意 sudo/shell 参数。Must NOT：隐藏菜单当权限控制；管理面读科学内容无审计；任意 root 通道。
  Closes: GAP-10
  Parallelization: Wave 10 | Blocked by: 49 | Blocks: 52、A06（管理界面）
  References (executor has NO interview context - be exhaustive): 主方案 §22.6/MU-09；并行计划二 B09-4；`src/acp/scheduler/structure_source_store.py:165`（审计表模式）
  Acceptance criteria (agent-executable): `pytest tests/test_services_admin.py -q` PASS：普通 Key 管理端点 403、等级提升不变管理权、审计字段完整、broker 白名单外拒绝。
  QA scenarios (name the exact tool + invocation): happy = 管理员操作全审计；failure = 普通 Key 调管理端点 → 403。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-51-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-lab): admin authorization + audit + restricted broker boundary
  Recommended task executor category: deep-low — 权限矩阵+审计。

- [x] 52. B09-5 Lab init/doctor：已验证 DB/HTTPS/LSF/存储组合；复用已有集群；Slurm 不阻塞 Lab
  What: init/doctor 的 Lab 档：组件能力检测（DB/HTTPS 证书/LSF 映射/存储 quota/隔离/密钥/备份）输出 PASS/FAIL/NOT_VERIFIED；复用机构现有 DB/OIDC/LSF；Slurm 仅按需求（不阻塞）；缺依赖拒绝启用所依赖能力（不降级匿名/无配额）；**身份能力如实声明**：OIDC 未实现时 doctor/能力报告不得标记可用（仅当 todo 73 交付对应 adapter 后按其证据启用；身份生命周期实施见 todo 73）。Must NOT：Slurm 作为交付必选；未验证组合展示为可用。
  Closes: GAP-10
  Parallelization: Wave 10 | Blocked by: 48—51、36 | Blocks: 65（Hosted 组合门）、69
  References (executor has NO interview context - be exhaustive): 主方案 §25.1/IN-04；并行计划二 B09-5；`src/acp/cli.py:4020-4090`（doctor 现状）
  Acceptance criteria (agent-executable): `pytest tests/test_services_lab_init.py -q` PASS：能力检测四态 + 拒绝降级断言；doctor 报告 schema。
  QA scenarios (name the exact tool + invocation): happy = 有 LSF 环境走通 Lab init→doctor；failure = 缺 HTTPS/quota → 明确拒绝启用 Lab 对应能力。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-52-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-lab): Lab init/doctor over validated combos (no silent downgrade)
  Recommended task executor category: unspecified-high — 检测矩阵+集成。

- [x] 73. B09-6（评审修订 B6）身份生命周期实施：账户建立/邀请、首次管理员 bootstrap、登录/会话或明确 Key-only、禁用/撤销/恢复；OIDC 仅于 Lab 范围交付否则如实不可用
  What to do / Must NOT do: 适用档位的完整身份状态流：账户建立/邀请（Lab 档）、首次管理员 bootstrap（无通用默认密码）、登录/会话（或明确声明 Key-only 支持范围及限制）、禁用/撤销（新命令/下载/既有事件流即时生效）、恢复；`deployment_tier` 与 `execution_mode` 分离；OIDC 若在本次 Lab 支持范围内 → adapter/回调/issuer+subject 验证任务；否则 doctor/能力报告不得声明可用（联动 todo 52）。依赖选型按组件策略（pwdlib/Authlib/PyCasbin/可选 Keycloak 候选——PoC 后批准，见组件 manifest）。Must NOT：不用 placeholder 充当认证；不在撤销后保留有效会话/Key；不按邮箱或同名角色自动合并/升权。
  Closes: GAP-3, GAP-10
  Parallelization: Wave 10 | Blocked by: 48、51、36 | Blocks: 65、75
  References (executor has NO interview context - be exhaustive): 评审 B6；主方案 §22.1/§22.3/§24.1/§25.2（MU-02/04/13）；todo 8（Key 原语）、todo 9（映射契约）
  Acceptance criteria (agent-executable): `pytest tests/test_services_identity_lifecycle.py -q` PASS：完整状态流（建立→登录/Key→禁用→撤销→恢复）；禁用后新命令/下载/事件流正确处理；缺认证能力档位拒绝启用；本地多项目回归（与 todo 7 联动）。
  QA scenarios (name the exact tool + invocation): happy = 全状态流（测试环境）→ PASS；failure = 撤销后旧会话/Key 再请求 → 拒绝且无副作用。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-73-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): identity lifecycle (enroll/bootstrap/session-or-keyonly/disable/revoke/recover)
  Recommended task executor category: deep-high — 身份生命周期与档位边界。

### Wave 11 — B10 共同核时与 quota-only（gate=M3 进入；首批验收以各自退出门为准，需 30—34/48；独立于价格/Credit）

- [x] 53. B10-1 冻结可审计计量口径：周期/时区/舍入、暂停/失败/重试与未知用量政策；未决政策显式配置/待确认，禁隐藏默认
  What to do / Must NOT do: 产出计量政策文档+代码化配置（`docs/reference/next-change/ACP_Services_Metering_Policy.md` + `src/acp_services/runtime/metering_policy.py`）：`metering_basis` 在 **分配核时**（Σ eligible_interval_seconds×allocated_cores/3600）与**实际 CPU 秒**间显式选择（未确认前留 `unresolved` 配置项，禁用内置默认值）；周期边界/时区、舍入规则、暂停/失败/重试/未知用量处理（未决=待对账）；排队/传输/发布不计；执行前冻结**可执行上限**（核数/walltime/硬限制能力/政策版本/PAUSED 计量）；无法给出上限时拒绝 quota-only（只能显式 usage-only）。Must NOT：不在代码里补隐藏价格/默认政策；不混用两种核时口径；不把政策写进科学身份。
  Closes: GAP-10
  Parallelization: Wave 11 | Blocked by: 30—34、48（M3 gate） | Blocks: 54—56、57+
  References (executor has NO interview context - be exhaustive): 主方案 §20.3/PR-05a；并行计划二 B10-1
  Acceptance criteria (agent-executable): `pytest tests/test_services_metering_policy.py -q` PASS：口径二选一未配置时拒绝计量（显式错误）；舍入/周期/暂停边界用例；政策版本快照固定。
  QA scenarios (name the exact tool + invocation): happy = 显式配置 quota-only 政策 → 计量记录按规则；failure = 未配置政策 → 拒绝且不产生用量记录。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-53-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-metering): frozen metering policy contract (no hidden defaults)
  Recommended task executor category: deep-high — 政策契约与可审计边界。

- [x] 54. B10-2 本地/LSF 可信采集；UsageRecord 绑定 attempt/submission/资源/证据/政策；父子不重复计量；缺证据待对账
  What: `src/acp_services/runtime/usage.py`：采集器（本地：进程组/OS 计数或政策允许的分配核时；LSF：`bjobs`/作业报告字段，映射实际分配）→ `UsageRecord`（usage_id、project/account、job/attempt/submission、资源/外部作业身份、口径/区间/核数、证据来源/版本、是否最终、排除区间、资源类别/政策、核时结果、证据摘要）；父子资源选一层不重复（LSF 作业核时 XOR 内部 ORCA 进程）；缺证据 → 待对账（不填零）。Must NOT：不以 progress/浏览器计时为用量；不重复累加；不写进科学载荷。
  Closes: GAP-10
  Parallelization: Wave 11 | Blocked by: 53 | Blocks: 55—56、58+
  References (executor has NO interview context - be exhaustive): 主方案 §20.4/PR-06/MU-10a；并行计划二 B10-2；`src/acp/scheduler/remote/monitor.py`（bjobs 字段素材）；`src/acp/scheduler/store.py:194`（关联持久化）
  Acceptance criteria (agent-executable): `pytest tests/test_services_usage.py -q` PASS：两种采集（mock LSF/本地）+ 父子不重复 + 缺证据待对账 + UsageRecord 字段完整。
  QA scenarios (name the exact tool + invocation): happy = 正常执行→记录→绑定 attempt；failure = 观测缺失 → 待对账不零填。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-54-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-metering): trusted usage records (attempt/submission bound, no double counting)
  Recommended task executor category: deep-low — 采集映射+字段契约。

- [x] 55. B10-3 usage-only 明确管理员选择；quota-only 用户/项目共同核时预留/准入/核销、跨周期分段/恢复与耗尽停止政策
  What: 模式实现：usage-only（仅记录/告警，需管理员显式选择）；quota-only（默认研究基线）：用户/项目共同约束（有效预算=min 交集）、派发前预留**可执行**上限（时长/核数硬上限；不可执行→拒绝准入）、按确认用量核销、跨周期分段（运行跨周期按规则切分）、重启恢复（未决预留重放）；**预算追加**：先原子追加授权与预留、再扩展运行额度（禁止先超用后补账）；**未知提交/未知用量保留待对账**（租约到期本身不释放）；耗尽按已批准政策（拒绝新派发，不杀在途；保留结果可读）。Must NOT：不靠事后统计防超用；模式切换不清旧政策快照；耗尽不删已产生结果；不先超用后补账。
  Closes: GAP-10
  Parallelization: Wave 11 | Blocked by: 54 | Blocks: 57—61
  References (executor has NO interview context - be exhaustive): 主方案 §23.4/MU-10a；并行计划二 B10-3
  Acceptance criteria (agent-executable): `pytest tests/test_services_quota.py -q` PASS：预留/核销/跨周期/耗尽/恢复矩阵；模式选择未显式时拒绝启动（负例）。
  QA scenarios (name the exact tool + invocation): happy = 配额耗尽 → 拒绝新派发、在途继续；failure = 未选模式 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-55-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-metering): usage-only/quota-only modes (reserve/write-off/period/exhaustion)
  Recommended task executor category: deep-high — 配额生命周期与恢复。

- [x] 56. B10-4 采集/对账与科学完成/结果读取解耦；可信用量 ≠ progress/浏览器计时/监控采样
  What: 解耦审计：UsageRecord 写入不依赖发布/完成路径（顺序独立、可重试）；结果读取不受账务/配额状态阻断（余额不足不锁已产生结果）；负断言：progress、浏览器计时、监控采样不进入用量链路。Must NOT：账务阻断科学；发布失败影响用量采集一致性；监控替代账本。
  Closes: GAP-10
  Parallelization: Wave 11 | Blocked by: 54 | Blocks: 59、69（软依赖：解耦证据供审计）
  References (executor has NO interview context - be exhaustive): 主方案 §20.6；并行计划二 B10-4；`src/acp/calculations/executor.py:1146-1175`（发布重试路径隔离素材）
  Acceptance criteria (agent-executable): `pytest tests/test_services_metering_decoupling.py -q` PASS：完成/读取与用量互不阻断；三条负断言（progress/timer/sample 不入账）。
  QA scenarios (name the exact tool + invocation): happy = 发布失败重试期间用量记录完整；failure = 余额/配额置零时读取已产生结果 → 仍可读。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-56-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-metering): decouple usage collection from science completion/result access
  Recommended task executor category: deep-low — 解耦断言。

### Wave 12 — B11 Credit / 托管预算与账单（gate=价格政策批准 + B10 通过；未过不开工）

- [~] 57. B11-1 Workspace 账本：定点最小单位、账户事务、稳定预留键/请求摘要、追加账本、结算/释放/冲正、按键查询；无第二余额权威
  What to do / Must NOT do: `src/acp_services/workspace/ledger.py`：定点/整数最小单位（禁浮点余额）；账户+预留+消费+释放+冲正记录（版本+幂等键）；预留键绑定租户/账户/操作/attempt 与请求摘要（同键异参拒绝）；追加式账本（不静默覆盖，修正=追加调整）；按键查询回执；唯一余额权威。Must NOT：不引入第二余额存储；不用异步统计代替实时预算；不实现价格（价格政策 gate）。
  Closes: GAP-10
  Parallelization: Wave 12 | Blocked by: 53—56 + 用户批准价格政策（gate） | Blocks: 58—61
  References (executor has NO interview context - be exhaustive): 主方案 §20.4/PR-07；并行计划二 B11-1
  Acceptance criteria (agent-executable): `pytest tests/test_services_ledger.py -q` PASS：并发预留不超用、重复消息不重复扣、冲正追加、按键查询幂等；钱包唯一权威断言。
  QA scenarios (name the exact tool + invocation): happy = 预留→结算→释放→冲正全链；failure = 同键异参 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-57-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-workspace): credit ledger (fixed-point, append-only, idempotent reservations)
  Recommended task executor category: deep-high — 账本事务与幂等语义。

- [~] 58. B11-2 Runtime 持久意图→原子预留→持久关联→派发→证据核对→幂等结算；响应丢失沿原键对账；提交未知/仅租约过期不提前释放
  What: 闭环实现：Runtime 先持久化操作意图+稳定预留键与请求摘要 → Workspace 原子预留 → Runtime 持久化关联 → 目录/release/身份确认 → 持久提交记录 → 执行 → 对账用量/身份 → 幂等结算与释放剩余；响应丢失按原键查询/重试（不新建键）；只有确认无外部执行才补偿释放（超时/租约过期不是释放依据）。Must NOT：关联未持久化即派发；重复 bsub 前释放额度；结算与用量证据脱钩。
  Closes: GAP-10
  Parallelization: Wave 12 | Blocked by: 57 | Blocks: 59—61、67
  References (executor has NO interview context - be exhaustive): 主方案 §20.5/PR-08；并行计划二 B11-2；`src/acp/scheduler/manager.py:3402`（提交意图素材）；`src/acp/scheduler/remote/submission.py:192`
  Acceptance criteria (agent-executable): `pytest tests/test_services_credit_flow.py -q` PASS：意图落库前后/响应丢失/关联写失败/重复恢复/取消竞争用例；提交未知禁释放断言。
  QA scenarios (name the exact tool + invocation): happy = 完整闭环；failure = 提交未知时注入释放 → 断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-58-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-credit): durable reserve→dispatch→settle closure (reconcile by key)
  Recommended task executor category: deep-high — 资金/资源准入闭环。

- [~] 59. B11-3 验证并发预留、超预算追加、取消竞争、重复/迟到回执、平台故障重试、purge 保留证据、账本恢复；科学结果不受余额锁定
  What: 验证矩阵（含故障注入）：并发预留、超预算原子追加、取消竞态、重复/迟到回执、平台故障重试、purge 后用量/冲正证据保留、账本备份恢复；科学结果读取不被余额锁定。Must NOT：跳过故障场景；purge 删账务证据；余额异常改写科学状态。
  Closes: GAP-10
  Parallelization: Wave 12 | Blocked by: 58 | Blocks: 68、69
  References (executor has NO interview context - be exhaustive): 主方案 §20.6/§21.2/PR-09；并行计划二 B11-3
  Acceptance criteria (agent-executable): `pytest tests/test_services_credit_faults.py -q` PASS：七类场景 + 恢复后对账；余额锁定下读取用例。
  QA scenarios (name the exact tool + invocation): happy = 故障注入后账本一致；failure = purge 后证据丢失 → 断言 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-59-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-credit): fault/recovery matrix (no science lockout, evidence retained)
  Recommended task executor category: deep-low — 矩阵化验证。

- [~] 60. B11-4 Web/CLI/Agent 使用同一账户/预算/账单；价格、暂停收费、失败补偿未确认不自行制定生产规则
  What: 三入口同账验证（CLI `--json` 预算/账单输出、API、MCP 同账户同规则）；未确认政策（价格、暂停计费、失败补偿）保持"显式配置/待确认"，任何代码路径不得内置生产规则；估算展示=资源类别/核数/时长假设/政策版本/不确定范围（非账单）。Must NOT：各入口各一套；估算当账单；自行定价。
  Closes: GAP-10
  Parallelization: Wave 12 | Blocked by: 58、42—43（三入口接口） | Blocks: 69、A06
  References (executor has NO interview context - be exhaustive): 主方案 §20.2/§20.3/PR-09；并行计划二 B11-4
  Acceptance criteria (agent-executable): `pytest tests/test_services_credit_entrypoints.py -q` PASS：三入口同账断言；政策未确认路径拒绝生产用法（负例）。
  QA scenarios (name the exact tool + invocation): happy = 同任务三入口查询同账；failure = 未配置价格时估算请求 → 返回待确认而非数字。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-60-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-credit): shared account across Web/CLI/Agent (no self-made price rules)
  Recommended task executor category: unspecified-low — 一致性验证。

- [~] 61. B11-5 Lago/支付仅确需时经 Workspace adapter 试点；许可/社区边界明确；支付开关不决定共同授权/配额
  What: 仅在有真实需求时：`src/acp_services/workspace/billing_provider.py` adapter 试点（隔离测试环境、许可/社区功能边界记录、唯一余额权威不变）；支付开关与授权/配额/隔离解耦（关支付不关授权与配额）。Must NOT：内外双扣费；以异步统计代替实时准入；未验证即启用。
  Closes: GAP-10
  Parallelization: Wave 12 | Blocked by: 57—60 + 真实需求（gate） | Blocks: 65、OS-10/OS-12
  References (executor has NO interview context - be exhaustive): 主方案 §27.3/OS-10；并行计划二 B11-5；`docs/reference/next-change/ACP_Open_Source_Reuse_Assessment_20261016.md` §3.2
  Acceptance criteria (agent-executable): 默认关闭时全套授权/配额测试通过（支付无关断言）；试点若启用：ADR + 隔离测试证据。
  QA scenarios (name the exact tool + invocation): happy = 关闭支付 → 授权/配额正常；failure = 试点中双写余额 → 断言 FAIL（不启动试点时记录 NOT_VERIFIED）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-61-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-credit): optional billing-provider adapter boundary (off by default)
  Recommended task executor category: unspecified-low — 边界与开关。

### Wave 13 — B12 条件扩展：数据库/存储/执行角色（gate=实际需求 + 逐组件 PoC；未过不开工）

- [~] 62. B12-1 PostgreSQL owner 仓储/迁移/事务 CAS/lease/fencing/重复回执验证；SQLite 阶段保持单 owner；不用网络盘多写
  What to do / Must NOT do: 仅需求触发：为各 owner 实现 PostgreSQL repository 试点（含迁移、事务 CAS、lease/fencing、重复回执）；对照 SQLite 语义等价（`store.py:194-367` CAS 行为、`migrations.py` 版本化迁移）；SQLite 保持单 owner、DB/WAL 不放网络共享盘多写。Must NOT：跨模块盲写；未验证即宣称多写集群；改动 §11.1 owner 表。
  Closes: GAP-10
  Parallelization: Wave 13 | Blocked by: 实际需求 + PoC（gate） | Blocks: 65、OS-04/OS-12
  References (executor has NO interview context - be exhaustive): 主方案 §11.3/P6-02/OS-04；并行计划二 B12-1
  Acceptance criteria (agent-executable): 试点脚本：迁移+事务 CAS+并发用例（隔离环境）PASS 或 NOT_VERIFIED；双写禁断言。
  QA scenarios (name the exact tool + invocation): happy = 隔离 PG 环境跑通迁移+并发 CAS；failure = 网络盘多写场景 → 拒绝配置。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-62-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-db): PostgreSQL repository pilot (CAS/lease/fencing equivalence)
  Recommended task executor category: deep-high — 存储语义等价与并发。

- [~] 63. B12-2 对象存储：上传/完整性确认/登记发布与引用/保留/回收；不假定 POSIX rename 或 ETag 即摘要；与旧目录/绑定兼容
  What: 仅容量需求触发：ArtifactStore adapter（上传→完整性确认→登记发布→引用/保留/回收），与 todo 12 StorageBinding 及 todo 14 校验管线对齐；旧目录/绑定只读兼容；社区版功能边界与容量目标记录。Must NOT：ETag 当内容摘要；对象未完成即可见；替换旧路径导致旧任务不可读。
  Closes: GAP-10
  Parallelization: Wave 13 | Blocked by: 实际需求 + PoC（gate） | Blocks: 65、OS-07/OS-12
  References (executor has NO interview context - be exhaustive): 主方案 §11.2/P6-03/OS-07；并行计划二 B12-2
  Acceptance criteria (agent-executable): 试点：上传/校验/发布/回收 + 故障恢复（隔离环境）PASS 或 NOT_VERIFIED；旧目录读取兼容用例。
  QA scenarios (name the exact tool + invocation): happy = 对象存储往返+摘要一致；failure = 未完成上传被引用 → 拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-63-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-storage): object-store adapter pilot (verify-then-publish, legacy compat)
  Recommended task executor category: deep-high — 存储协议语义。

- [~] 64. B12-3 Slurm/Podman/GPU 逐 adapter 实证；区分请求与实际分配；不支持拒绝、不静默降级；GPU 价格另批政策
  What: 逐 adapter（按需求）：Slurm 执行/用量映射；Podman/容器隔离；GPU 资源请求/分配（可见设备/隔离/取消释放/内存不足/重启对账）；能力声明区分支持/不支持（拒绝而非降级）；GPU 计量/价格独立政策未批不启用。Must NOT：照搬单一 rusage 示例；CUDA 版本代替硬件能力；静默 CPU 降级。
  Closes: GAP-10
  Parallelization: Wave 13 | Blocked by: 实际需求 + PoC（gate） | Blocks: 65、OS-06/OS-12
  References (executor has NO interview context - be exhaustive): 主方案 §31.2/P6-03/OS-06；并行计划二 B12-3
  Acceptance criteria (agent-executable): 每 adapter 能力矩阵 + 隔离环境用例 PASS 或 NOT_VERIFIED；不支持拒绝断言。
  QA scenarios (name the exact tool + invocation): happy = 支持组合提交→实际分配记录；failure = 请求不支持资源 → 明确拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-64-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-exec): per-adapter Slurm/Podman/GPU validation (request vs actual, refuse-not-degrade)
  Recommended task executor category: deep-high — 异构执行能力。

- [~] 65. B12-4 Hosted/distributed 配方仅启用完整隔离+可信用量+运维+预算门的组合；缺依赖不降级匿名/无配额
  What: 组合校验：Hosted/distributed 配方启用条件=隔离（B09-3 实测）+可信用量（B10）+运维（备份/恢复/审计）+所选模式预算门（B11 视启用）；缺任一依赖 → 拒绝启用并明示；组件锁定 manifest（todo 5 更新为已批准）；OS-12 双版本比较与冻结 ADR 输入。Must NOT：匿名/无配额降级；Lab 冒充 Hosted。
  Closes: GAP-10
  Parallelization: Wave 13 | Blocked by: 50、52、55—60（实际启用范围） | Blocks: 69、OS-12
  References (executor has NO interview context - be exhaustive): 主方案 §25.1/§28.4/OS-05/OS-12；并行计划二 B12-4
  Acceptance criteria (agent-executable): 配方门脚本：组合矩阵 PASS/拒绝 + NOT_VERIFIED 清单；manifest 状态更新记录。
  QA scenarios (name the exact tool + invocation): happy = 全门通过组合生成配方；failure = 缺隔离 → 拒绝启用（不降级）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-65-acp-parallel-plan-2-services.md`
  Commit: Y | feat(services-hosted): gated recipe enablement (no anonymous/no-quota fallback)
  Recommended task executor category: deep-low — 门控矩阵。

### Wave 14 — B13 集成/故障恢复/运维交付（随切片 + 收口）

- [x] 66. B13-1 每片先定向契约/行为/恢复测试，再按范围执行非慢与适用 compileall；共享门变更接受 A/C 证据，不删除护栏换通过
  What to do / Must NOT do: 纪律落地：每切片收口命令序列（定向 → `pytest -m "not slow"` 范围 → 适用 `python -m compileall -q src`）；涉及共享门/grep pin/架构门变更时，接收 A/C 的证据并入台账（不得自行放宽）；`scripts/check_grep_gates.py` 四 pin（`unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo`，`:300-385`）与 `tests/test_f4_scope_audit.py:1902-1909` 修订注册纪律全程执行；**对计划任务/子项与硬依赖做静态一致性检查（评审 P1-5：编号唯一、依赖引用有效、无环、产物先于消费者）**。Must NOT：删测试/放宽 grep/AST 门/更新黄金值遮差异；把 mock 通过当真实功能。
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 所选切片 | Blocks: 69—71
  References (executor has NO interview context - be exhaustive): 并行计划二 B13-1；主方案 §14.2/CQ-07/08；`scripts/check_grep_gates.py:276-385`；`tests/test_f4_scope_audit.py:15-129,1902-1909`；`tests/test_architecture_invariants.py:471-640`
  Acceptance criteria (agent-executable): 每切片证据含命令+退出码；护栏套件（架构/标记/编辑覆盖/frames/隔离）在收口时全绿；台账逐项状态更新；依赖图静态检查 PASS（编号唯一、引用有效、无环）。
  QA scenarios (name the exact tool + invocation): happy = 收口序列脚本一次跑通；failure = 放宽一个 guard 的对照提交 → F2 审计拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-66-acp-parallel-plan-2-services.md`
  Commit: Y | test(services): per-slice verification discipline (guards preserved)
  Recommended task executor category: unspecified-low — 纪律执行。

- [x] 67. B13-2 故障覆盖：未知提交、重复/乱序回执、失联/崩溃、磁盘满、传输中断、旧 worker 仍写、权限撤销、预算竞争、恢复后外部作业对账
  What: 构造注入矩阵（可用 mock/故障点）：未知提交（租约内不判 not_accepted）、重复/乱序回执、进程崩溃/失联、磁盘满（写失败保障）、传输中断（断点/重试）、旧 worker 仍在写（generation/fence + 物理写保护）、权限撤销即时生效、预算竞争（B11 启用时）、恢复后对账（先对账再派发）；**持久化边界崩溃**：CAS 后未通知（outbox 滞留补投）、outbox 投递后未标记、文件已发布 DB 未登记 / DB 有记录文件缺失、schema 扩展/收缩与代码回退组合演练。Must NOT：跳过任一场景；把"未见失败"当通过。
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 66 + 对应切片 | Blocks: 69
  References (executor has NO interview context - be exhaustive): 并行计划二 B13-2；主方案 §16/§21.2；`tests/test_execution_integrity_e2e.py:298-1133`；`tests/test_remote_phase{1..6}.py`
  Acceptance criteria (agent-executable): 故障矩阵逐个 PASS/FAIL 记录 + 修复闭环；每场景断言（不改状态/不丢事实/可对账）。
  QA scenarios (name the exact tool + invocation): happy = 九场景全部 PASS（或 NOT_VERIFIED+LIMIT 记录）；failure = 旧 worker 写入污染当前缓存 → 断言 FAIL 并修复。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-67-acp-parallel-plan-2-services.md`
  Commit: Y | test(services): fault-injection matrix (unknown/duplicate/crash/disk/permission/race)
  Recommended task executor category: deep-low — 故障矩阵执行。

- [x] 68. B13-3 验证 DB/科学原件/工件/密钥/账本一致备份恢复；HTTPS/秘密/观测/备份组件只按采用范围集成
  What: 一致性备份恢复演练（扩展 todo 4）：DB+任务目录+manifest+密钥版本+（B11 启用时）账本；恢复后对账外部在途作业再开放派发；组件集成仅按 manifest 采用范围（OS-11），未采用不安装。Must NOT：恢复旧 DB 后直接重派已有计算；对未集成组件编造证据。
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 4、66 | Blocks: 69、71
  References (executor has NO interview context - be exhaustive): 主方案 §15.2/OS-11；并行计划二 B13-3；`scripts/migrate_run_root.py:56-158`
  Acceptance criteria (agent-executable): 演练记录（恢复+对账）PASS/FAIL；账本（如启用）恢复幂等；组件清单差分。
  QA scenarios (name the exact tool + invocation): happy = 全关联恢复→对账→零重复派发；failure = 跳过对账对照 → 暴露重复派发（FAIL 证据）。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-68-acp-parallel-plan-2-services.md`
  Commit: Y | test(services): consistent backup/restore drill (DB/artifacts/secrets/ledger)
  Recommended task executor category: unspecified-high — 跨组件恢复演练。

- [x] 69. B13-4 与 A05/C10 分列模块/接入/真实功能与浏览器/科学软件/LSF/生产 PASS/FAIL/NOT_VERIFIED；不用前端模拟证明科学一致性
  What: 分层验收记录：三层（模块验证 / 默认入口接入 / 真实功能验收）分列；环境类（浏览器=A05、科学软件/LSF=C10/B、生产）分别 PASS/FAIL/NOT_VERIFIED；**消费 todo 75 的四组合结果生成最终收口记录**（组合执行与产出归 75；A 提供前端实现与浏览器场景，C 判定同一 release 下科学等价；B 执行 A 提供的黑盒测试、不取得 A 源码写权）；每份证据绑定实际提交/契约版本/配置/fixture 摘要（不能拿 S0 旧记录证明 S1）；A/C 对 B 的交付物有接收任务登记，未接入的明确延期并保持旧功能可用；科学一致性用固定输入对照（C 的黄金证据），禁前端模拟替代；汇总报告 `docs/reference/current/ACP_Services_Closure_<date>.md`（体例参照 `ACP_Frontend_Closure_20261017.md`）。Must NOT：mock 通过记真实通过；跨路证据混写；把"已交字段"写成"前端已接入"。
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 66—68、75；输入 A05/C10 | Blocks: 70
  References (executor has NO interview context - be exhaustive): 并行计划二 B13-4；并行索引 §7 联合关闭条件；`docs/reference/current/ACP_Frontend_Closure_20261017.md`（体例）
  Acceptance criteria (agent-executable): 汇总报告三分层+三门状态齐全；每项可追溯到证据路径；NOT_VERIFIED 逐项原因。
  QA scenarios (name the exact tool + invocation): happy = 报告与 A/C 记录互链核对；failure = 将 mock 证据标为真实通过 → 审计拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-69-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): layered closure record (module/access/real function, three-state)
  Recommended task executor category: unspecified-high — 证据汇总与审计。

- [x] 75. B13-7（评审修订 X3）四组合联合验收组织：F0＋S0 / F1＋S0 / F0＋S1 / F1＋S1；B 组织执行、A 提供浏览器证据、C 判定科学等价
  What to do / Must NOT do: **T75 负责组合执行与产出**（T69 消费其结果生成收口记录；T75 不依赖 T69）：在集成分支组织并运行四组合（F0＋S0 基线、F1＋S0 前端兼容、F0＋S1 旧客户端兼容、F1＋S1 集成行为）；**四组合逐项声明预期能力**（F0/S0 基线通过不代表支持新 prepare 或账户 UI；Lab 功能启用的组合需等 todo 73 身份交付；首批本地组合不被未选 Lab 能力阻塞）；C 在同一集成候选上判科学输出（不把跨版本摘要拼作当前证据）；每份证据绑定实际提交/契约版本/配置/fixture 摘要（S0 旧记录不能证明 S1）；登记 A/C 对 B 交付物的接收任务与延期范围；未启用 A04 时明确交付的是前端兼容重构。Must NOT：不取得 A 源码写权（只执行 A 提供的黑盒测试）；不以单组合代替四组合；不拼接不同提交的通过记录。
  Closes: GAP-11, IS-11
  Parallelization: Wave 14 | Blocked by: 16、24、46（环境与场景资料由早期准备子项提供；不依赖 T69） | Blocks: 69、76
  References (executor has NO interview context - be exhaustive): 评审 X3/§6.3；并行索引 §7；`docs/reference/current/ACP_Frontend_Closure_20261017.md`
  Acceptance criteria (agent-executable): 四组合记录齐全且可追溯（提交/契约/fixture 摘要）；科学等价由 C 判定记录引用；未验证项 NOT_VERIFIED 逐项原因。
  QA scenarios (name the exact tool + invocation): happy = 四组合矩阵逐个记录 → PASS；failure = 用 S0 记录冒充 S1 → 审计拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-75-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): four-combination joint acceptance records (F0/F1 × S0/S1)
  Recommended task executor category: unspecified-high — 跨路联合验收组织。

- [x] 70. B13-5 保留旧导入 shim/历史 reader/模型 vendor/notice/恢复 fixture；逐项关闭台账并同步契约/AGENTS/部署/手册
  What: 清理纪律：公开 shim（`acp.calculations.primitives`、`acp.backends`、旧 scheduler 路径）保留；`src/acp/compat/legacy` 只读保留；模型/资产/notice 随包；`tests/baseline/recovery_fixtures/` 不动；台账逐项更新状态（待核验→…→关闭）；同步：根 `AGENTS.md`（新增 acp_services 结构/ANTI 增补）、`src/acp*/AGENTS.md` 受影响叶、`docs/reference/next-change/ACP_Services_*`、部署手册、`docs/ACP_Architecture_Adjustment_Plan_20261016.md` 追加 Services 实施记录（按 §41—§44 体例）。Must NOT：删除活跃 fixtures/兼容面；为减行数删实现；不同步规则只发代码。
  Closes: GAP-11
  Parallelization: Wave 14 | Blocked by: 69 | Blocks: 71
  References (executor has NO interview context - be exhaustive): 主方案 §17/P7-01/§37.2；并行计划二 B13-5；根 `AGENTS.md`（结构/ANTI 表）；`src/acp/AGENTS.md`；`docs/ACP_Architecture_Adjustment_Plan_20261016.md:1719-1768`（§43/§44 体例）
  Acceptance criteria (agent-executable): 台账关闭状态 ≥ 已完成切片；AGENTS/契约/手册 diff 与实现一致；shim 清单与调用者审计记录。
  QA scenarios (name the exact tool + invocation): happy = 文档一致性检查脚本（路径/符号存在性）；failure = 删除一个被引用的 shim → 导入测试 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-70-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): close ledger; sync AGENTS/contracts/deploy/master plan
  Recommended task executor category: unspecified-high — 跨文档收口。

- [x] 71. B13-6 发布准备：匹配 release/静态资源与在途任务的兼容窗口 + schema 扩展/收缩与回退条件（实际发布见 todo 76）
  What: 发布前准备：确立"代码 release × 静态资源 × 在途任务"兼容矩阵（旧页面/旧 worker/旧 release 在窗口内可读可用）；数据库采用可验证的扩展/切换/收缩步骤（声明旧 reader 能读哪些新记录、何时不可回退、如何恢复；代码回退/schema 回退/数据恢复分别写清）；服务重启流程按项目规范（systemd unit 编辑 `scripts/install_systemd.sh`，勿手改 `/etc/systemd/system/acp.service`）；**实际发布与冒烟由 todo 76 在 F1—F4 全部 APPROVE 后执行**。Must NOT：在本 todo 实际发布/重启生产；在途任务破坏；手改 unit；无兼容矩阵就宣称可发布。
  Closes: GAP-11, IS-3/IS-7 收口
  Parallelization: Wave 14 | Blocked by: 70 | Blocks: 76
  References (executor has NO interview context - be exhaustive): 根 `AGENTS.md` SYSTEMD/COMMANDS；并行计划二 B13-6；评审 X6；`scripts/install_systemd.sh:32-51`；`scripts/start_acp.sh:39`
  Acceptance criteria (agent-executable): 兼容矩阵 + schema 回退步骤文档；回退演练记录一次；发布清单齐备（供 todo 76 执行）；unit 生成来源核对。
  QA scenarios (name the exact tool + invocation): happy = 按发布清单 dry-run 核对（不实际发布）；failure = 手改 unit 的对照 → 检查脚本 FAIL。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-71-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): release prep (compat window + schema expand/contract/rollback)
  Recommended task executor category: unspecified-low — 发布清单准备。

- [x] 76. B13-8（评审修订 X6）终验后发布执行与冒烟：gate=F1—F4 全部 APPROVE 后按发布清单执行（含 schema 扩展/收缩与回退核对）
  What to do / Must NOT do: 按 todo 71 发布清单实际发布（集成 release 环境；记录模块实际来源）；执行服务重启（unit 生成来源=`scripts/install_systemd.sh`）与冒烟（doctor + 默认页 + 一个闭环 + 在途任务兼容核对）；schema 扩展/收缩步骤按其文档执行并记录；发布后核对旧页面/旧 worker/旧 release 兼容窗口；**发布使用的产物须与 C-T54 所验候选产物可核对；影响产物的后续修改使原验收失效（评审 R4）**。Must NOT：在 F1—F4 未全部 APPROVE 前执行；手改 unit；破坏在途任务；无兼容矩阵发布。
  Closes: GAP-11
  Parallelization: Wave 14b | Blocked by: 71 + F1—F4 全 APPROVE（执行序在最终验证波之后；发布后结果核对=F5，不得回写 F1—F4） | Blocks: F5
  References (executor has NO interview context - be exhaustive): 评审 X6/§7；根 `AGENTS.md` SYSTEMD；`scripts/install_systemd.sh:32-51`；`scripts/start_acp.sh:39`
  Acceptance criteria (agent-executable): 发布记录（提交/构建/重启/冒烟）；schema 回退核对；在途任务兼容核对 PASS/FAIL/NOT_VERIFIED。
  QA scenarios (name the exact tool + invocation): happy = 发布后冒烟全绿 → PASS；failure = 跳过 F1—F4 的对照发布 → 门禁脚本拒绝。Evidence `.omo/evidence/acp-parallel-plan-2-services/task-76-acp-parallel-plan-2-services.md`
  Commit: Y | chore(services): post-verification release + smoke (gated on F1–F4 approval)
  Recommended task executor category: unspecified-low — 发布执行清单。

- [x] 79. B13-9（B41b 实测缺陷修复）recover 配置丢失（D2）、跨进程孤儿收尾（D1）、起步期 SIGKILL 孤儿（D3）、doctor 错误配置回落假成功（S4c）与本地-only 语义（S4）；不触及 src/cccp 实现
  What to do / Must NOT do: 依据 `docs/reference/next-change/ACP_Services_Standalone_Recipe.md` §2.1 的实测缺陷逐项修复（B 路文件范围内）：(a) D2：`acp task recover` 必须从持久 job/task 记录恢复原始方法与可执行配置，禁止生成 `! default def2-TZVPP` 占位输入、禁止 orca 回落 GNOME PATH；(b) D1：`runner.poll()` 的 untracked-RUNNING 分支在 worker 已自然退出时必须先检查 `state.json`/RESULT 完成证据，完成事实不得被收尾为 failed；CANCELLING 行跨进程终局必须是 CANCELLED（控制意图不被旧观测覆盖）；(c) D3：提交窗口内 SIGKILL 的孤儿 worker 必须可被下一控制器收养/取消（fence/attempt/进程身份核对），不得以 “refusing to start a second execution” 置 failed 后放任资源泄漏；(d) S4c：doctor 对显式配置但无效的路径必须 FAIL/NOT_VERIFIED，不得静默回落 PATH 把无关二进制（GNOME Orca）报为 PASS；PATH 解析结果须经 QC 二进制 sanity 校验；(e) S4：本地-only 档位 doctor 应报告本地能力且不因零远程节点 exit 1（保留既有远程 doctor 行为）。Must NOT：不改 `src/cccp/**`（C 路写权；resolver 回落根因在交接台账登记给 C）、不删/放宽既有护栏、不把 mock 当真实。
  Closes: B41b defects；IS-7/IS-8 真实性
  Parallelization: Wave 14 | Blocked by: 41、74 | Blocks: F3/F4（本地闭环与 doctor 真实性）
  References: `docs/reference/next-change/ACP_Services_Standalone_Recipe.md` §2.1（D1/D2/D3/S4/S4c 证据）；`src/acp/scheduler/runner.py`（poll untracked-RUNNING 分支）；`src/acp/cli.py`（doctor/task recover）；`src/acp/scheduler/manager.py`（收养/reconcile）；`src/acp_services/runtime/commands.py`；`tests/test_execution_integrity_e2e.py`；`tests/test_remote_cancel_confirmation.py`
  Acceptance criteria (agent-executable): 新增 `tests/test_services_b41b_defect_fixes.py` PASS：recover 使用持久方法/配置（无占位输入、无 GNOME 回落）；rerun/continue/cancel 跨进程收尾以 state.json/完成证据为准且 CANCELLING→CANCELLED；起步期 SIGKILL 孤儿可收养/取消且不误置 failed；doctor 显式无效路径 → FAIL/NOT_VERIFIED（不得 PATH 假 PASS）、本地-only 不 exit 1；B41b 配方相关腿（recover、跨进程 rerun/continue/cancel、doctor 负例）重跑 PASS/记录。
  QA scenarios: happy = 隔离 run_root 下 recover→completed、跨进程 rerun 无活控制器→completed、doctor 本地-only PASS；failure = 显式无效 orca 路径 → doctor FAIL/NOT_VERIFIED（负例证明不再假成功）。
  Commit: Y | fix(services): B41b defect remediation (recover config, cross-process finalization, orphan adoption, truthful doctor)

- [x] 80. B13-10 F-wave remediation A（执行终局 + worker 守卫）：本地无控制器完成事实（state.json/RESULT）在 fresh-controller 启动时不得被误置 failed；CANCELLING 跨进程终局为 CANCELLED（exit 130）；F-3 守卫按 todo 77/78 原文收窄到 worker 边界且负例仍 FAIL
  What: (a) `_requeue_active_on_startup`/runner finalization 对本地 job 读取 state.json/RESULT 完成证据（无 .exit_code 时不得 `[RESTART_FAILED]`）；(b) CANCELLING 行在确认停止后置 CANCELLED 且 exit_code=130；(c) `tests/test_services_worker_contract.py::test_runtime_package_has_no_acp_or_db_access` 的扫描面限定为 worker 执行边界（worker/worker_bootstrap/execution_chain），保留 import acp / attempt+1 / .transition( / jobs-DB 写字符串的全部检测与负例参数化；runtime/admission|quota 的独立账本与 isolation 的 deny-list 字面量不再误报。Must NOT：删除/跳过守卫、allowlist 放行、放宽 isolation 测试。
  Closes: F3-D1/D2（blocker）、F1/F2 F-3
  References: `.omo/evidence/acp-parallel-plan-2-services/f3-manual-qa.md` §1 #14、`f1-plan-compliance.md` §1、`f2-code-quality.md` 发现 #1；`src/acp/scheduler/manager.py`（requeue/finalize）、`src/acp/scheduler/runner.py`、`tests/test_services_worker_contract.py`、`tests/test_services_b41b_defect_fixes.py`
  Acceptance: `pytest tests/test_services_worker_contract.py tests/test_services_b41b_defect_fixes.py tests/test_execution_integrity_e2e.py tests/test_remote_cancel_confirmation.py -q` 全绿；新增本地“worker 完成但无控制器 → fresh controller → completed”与 “CANCELLING → cancelled(130)” 回归；worker 模块注入 `import acp`/`attempt += 1` 仍 FAIL。
  Evidence `.omo/evidence/acp-parallel-plan-2-services/task-80-acp-parallel-plan-2-services.md`
  Commit: Y | fix(services): cross-process completion + CANCELLING finalization; scope worker guard (F-3)

- [x] 81. B13-11 F-wave remediation B（IS-5 投影默认 + IS-4 生产 Data 写入口）：查询默认只读已准备快照（零同步 state.json 扫描）；生产 frame-candidate/manifest/executor/structure-source 写入经 DataResourcePort
  What: (a) `ACP_TASK_VIEW_ENRICH_COMPAT` 默认关闭；未覆盖行显示 jobs 基础事实、零 `state.json` 读取/解析与 `find_workflow_state` 调用；更新 `tests/test_acp_api_v2_task_view.py::test_active_row_enrichment` 与迁移台账行状态；(b) 生产路径 `v1_routes.py` frame-candidate 保存、`result_publication.py` fallback、`services_bootstrap.py`/`workflows/simple.py` 的 executor 构造、structure-source 写路径改经 todo 11 Data port；call-spy 证明生产路径不直接调用旧 writer；旧 GET 兼容保持。Must NOT：删除旧 GET 语义、把模块验证写成“前端已接入”、放宽查询预算/兼容测试。
  Closes: F4 IS-5/IS-4（PARTIAL）
  References: `.omo/evidence/acp-parallel-plan-2-services/f4-ideal-state-fidelity.md` IS-4/IS-5 + rows 80/81；`src/acp/api/{v1_routes,v2_routes}.py`、`src/acp/calculations/{result_publication,executor}.py`、`src/acp/workflows/simple.py`、`src/acp/services_bootstrap.py`
  Acceptance: 默认 env 下 `pytest tests/test_services_task_projection.py tests/test_services_query_budget.py tests/test_acp_api_v2_task_view.py tests/test_services_publication_data_port.py tests/test_services_data_port.py tests/test_calculation_executor.py -q` 全绿；spy 断言零同步扫描且生产写路径命中 Data port；`pytest tests/test_acp_api_v1.py -q` 回归。
  Evidence `.omo/evidence/acp-parallel-plan-2-services/task-81-acp-parallel-plan-2-services.md`
  Commit: Y | fix(services): default prepared projections + route production Data writes through the port

- [x] 82. B13-12 F-wave remediation C（检查器/文档/登记口径）：计划一致性接受 `[~]` 条件行；Recipe 与 checker 对齐并回填 todo 79 后值；补登记 todo-77 残余注入；IS-3/IS-6/IS-11 的开放门与台账状态如实收口
  What: (a) `tests/test_services_plan_consistency.py` `_HEADER_RE` 接受 `[~]`，修正 placeholder/gap 记账，保留重复/环/引用负例；(b) `ACP_Services_Standalone_Recipe.md` 恢复 `T74 不等待本任务` 声明、verdict token 可被 checker 解析，并回填 todo 79 后 recover/跨进程/孤儿/doctor 实测值（S4/S4c 缺陷注记保留）；(c) `injection_registry.md` 为控制/recovery、Data writer/cache、catalog 归一化登记 DEFERRED + 移除条件 + 验收；(d) 闭包记录/矩阵/迁移台账如实记录 IS-3 注册性延期、IS-6=8/15 envelope+7 legacy gate OPEN、IS-11 的 OPEN 行（含 L2-12/L2-15）与延期范围，禁止把“模块交付”写成“生产接线”。Must NOT：删除缺陷注记、放宽 checker、把 `[~]` 改回 `[ ]` 充数。
  Closes: F1 81/82/83、F2 81/83、F4 82/83/84/85/87
  References: `f1-plan-compliance.md` §PROPOSED、`f2-code-quality.md`、`f4-ideal-state-fidelity.md`；对应 tracked 文档与 checker。
  Acceptance: `pytest tests/test_services_plan_consistency.py tests/test_services_standalone_recipe.py tests/test_services_injection_contracts.py tests/test_services_closure_record.py tests/test_services_doc_sync.py -q` 全绿；负例（重复编号/成环/S4c 改 PASS）仍 FAIL；文档与实现状态一致。
  Evidence `.omo/evidence/acp-parallel-plan-2-services/task-82-acp-parallel-plan-2-services.md`
  Commit: Y | docs(services): reconcile checkers/recipe/registries with remediation results

- [x] 83. B13-13 F-wave remediation D（代码卫生）：消除新引入的 ruff-format 漂移；对遗留 `except Exception:` 站点给出逐点理由或收窄
  What: `ruff format` 修正 `src/acp_services/runtime/plan_contracts.py`、`src/acp_services/workspace/identity_lifecycle.py`（v2_routes 的格式化归 todo 81）；对 F2 列出的 20 个 `except Exception:` 站点记录逐点理由（事务 rollback+raise / best-effort 边界），并收窄 `data/operations.py` 的重试边界与 `services_data_bootstrap.py` 的边界处理，确保不把编程错误吞成可重试结果。Must NOT：全仓机械格式化、放宽 ruff 规则、删除异常处理改变行为。
  Closes: F2 82/84（hygiene）
  References: `f2-code-quality.md` 发现 F2-03 与 LOW `except Exception` 表。
  Acceptance: `python3.11 -m ruff format --check` 对 plan-touched 文件零新漂移；`ruff check` 零新增；受影响定向套件全绿。
  Evidence `.omo/evidence/acp-parallel-plan-2-services/task-83-acp-parallel-plan-2-services.md`
  Commit: Y | style(services): zero new format drift + recorded except-boundary justification

- [x] 84. B13-14 F-wave remediation E（F4'-B1/B2）：scan/irc primitives、workflow irc、v2 structure-source metadata 等剩余生产写路径经 DataResourcePort；刷新闭包记录 F-3 为已修复
  What: (a) `src/acp/calculations/primitives/scan.py:248`、`primitives/irc.py:236,286` 的 `register_result_manifest` 传入 Data publisher；`result_publication.py:246` 的 `manifest.write` fallback 在生产路径不得绕过 port；executor `StepKind.SCAN` dispatch（`executor.py:270,1086`）走 port；`workflows/irc.py:197` 直接 `manifest.write` 改经 port；`api/v2_structure_sources.py` metadata 写路径（`:231,:252,:257,:361,:384,:419-431,:463,:471`）改经 Data sources port。call-spy 证明生产路径命中 port 且不再直写。(b) `docs/reference/current/ACP_Services_Closure_20261019.md` 的 F-3/L1-03/§6/§7.5/JSON 记录刷新为“worker guard 已修复并全绿”，不得保留过期 FAIL。Must NOT：改纯转发语义/primitive 唯一实现门、删除旧 GET 兼容、把模块交付写成生产接线。
  Closes: F4' IS-4（PARTIAL）/IS-11（stale F-3）
  References: `.omo/evidence/acp-parallel-plan-2-services/f4-ideal-state-fidelity.md`（F4'-B1/B2）；对应文件与 `tests/test_services_production_data_paths.py`
  Acceptance: `pytest tests/test_services_production_data_paths.py tests/test_services_publication_data_port.py tests/test_services_data_port.py tests/test_calculation_executor.py tests/test_architecture_invariants.py tests/test_acp_api_v1.py -q` 全绿；新增 call-spy 覆盖 scan/irc/structure-source；闭包记录 F-3 状态与 HEAD 一致。
  Evidence `.omo/evidence/acp-parallel-plan-2-services/task-84-acp-parallel-plan-2-services.md`
  Commit: Y | fix(services): route remaining production writes through Data port; refresh closure F-3 status

## Final verification wave
> Runs in parallel after the pre-release implementation todos of the declared scope (todo 76 release is excluded by design). F1—F4 must APPROVE; then todo 76 (release) executes; F5 (post-release result check) runs after todo 76 and closes the release results. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  Read the plan file and the evidence tree; verify every todo's Acceptance ran with recorded evidence (command + exit code + output excerpt); no scope creep beyond Must-have; no guard was relaxed; unexecuted items are explicitly marked with reason. **Scope = 发布前**：todos 1—75、77—78 与 T71 发布准备；显式排除发布结果（T76/F5）与未进入本次 release 范围的条件项；发布前输入含候选版本冻结与 **C-T54 合并版本安装复验**（跨路）及本次范围证据汇总（C-T51 不得提前关闭仍等待跨路应用的项目）。Missing/undone items become new `- [ ] N.` rows; a shortfall is never a note. Evidence `.omo/evidence/acp-parallel-plan-2-services/f1-plan-compliance.md`
  Recommended task executor category: unspecified-high — 逐 todo 对照核验。
- [x] F2. Code quality review
  Review changed files for: no new bare `except Exception:`（ANTI #8）；ruff/format on touched files；shim purity（primitives/backends 零业务逻辑）；`acp_services → acp` 依赖方向零违规；docs/AGENTS 与实现一致；no test deletion/weakened gates. Evidence `.omo/evidence/acp-parallel-plan-2-services/f2-code-quality.md`
  Recommended task executor category: deep-low — 跨模块质量审查。
- [x] F3. Real manual QA
  On an isolated run_root: run the local closed loop at the actually delivered scope (real CLI entry where todo 74 has delivered it: ingest→plan→execute→query→pause/continue/cancel→in-place rerun→recover→export; Runtime/manager scripted use cases elsewhere — never claim more than delivered; no new CLI surface assumed) plus `acp doctor` and default page load; exercise pause/cancel/rerun and one recovery scenario; record PASS/FAIL/NOT_VERIFIED for real software/LSF/production separately; compare against B00 scenario metrics where measured. Evidence `.omo/evidence/acp-parallel-plan-2-services/f3-manual-qa.md`
  Recommended task executor category: unspecified-high — 端到端手工 QA。
- [x] F4. Ideal-state fidelity
  Walk IS-1…IS-11 one-by-one against delivered behavior (Success-criteria table); every IS row must have a delivering todo + proving QA scenario + evidence; a shortfall becomes new `- [ ] N.` rows, never a note. **发布前范围核验；IS-11 的发布后部分由 F5 核验。** Evidence `.omo/evidence/acp-parallel-plan-2-services/f4-ideal-state-fidelity.md`
  Recommended task executor category: deep-high — 理想状态逐行核验。
- [x] F5. Release result verification (post-release)
  After todo 76 executes, verify the release record (commit/build/restart/smoke), compat window, and schema expand/contract/rollback evidence; close IS-11's post-release part; a failed release enters recovery and re-verification — **never backdate F1—F4 conclusions**. Evidence `.omo/evidence/acp-parallel-plan-2-services/f5-release-result.md`
  Recommended task executor category: unspecified-high — 发布后结果核对。

## Commit strategy
- 约定：Conventional Commits，每个 todo 一个语义 commit（如 `feat(services-data): …`）；纯迁移（仅移动+转发）与行为变更不得混在同一 commit；`.omo/**` 不入库（证据/草稿/计划为本地工件）。
- 提交前必跑：该 todo 的定向测试 + `git status --porcelain` 检查（不留未预期文件）；切片收口再跑范围非慢与适用 compileall。
- 护栏与共享门：与实现同批提交的只允许"登记修订+等效门"（如 `tests/test_acp_services_isolation.py`、`tests/baseline/services-evidence/migration_ledger.md`）；不得单独提交放宽/删除护栏。
- 护栏/AGENTS/布局规范/部署脚本的修订随对应切片提交（不得等 todo 70 才统一更新）；`docs/reference/current/ACP_Job_File_Layout_Spec.md` 修订须先登记+等效门（评审 X6）。
- 发布拆分（评审 X6）：todo 71 只做发布准备；实际发布/重启/冒烟为 todo 76（F1—F4 全 APPROVE 后），不得提前执行；发布结果核对为 F5（不回写 F1—F4 结论）。
- 交接证据可交付（评审 X5）：跨路契约/样例/回执入 tracked 路径；`.omo` 仅作本地工作区。
- 共享文件：`pyproject.toml`/`requirements-node.txt`/根 AGENTS/主方案/共享集成测试由 B 作为集成写者提交（可含 C/A 提供的补丁与证据，需注明来源）；`src/cccp/**`、`frontend/**` 不由本计划直接提交。
- 契约文档（`docs/reference/next-change/ACP_Services_*`）随产生它的切片提交；收口文档（`docs/reference/current/ACP_Services_Closure_<date>.md`）在 todo 69 提交。
- 回退：每 commit 可独立回退；数据/在途任务回退遵循主方案 §15.2（不改 DB/任务目录，不重发命令）。

## Success criteria
> One row per IS row. The plan is complete only when every IS row has a delivering todo and a proving QA scenario; F4 checks the delivered behavior against these rows 1:1, and a shortfall becomes new `- [ ] N.` rows, never a note.
| IS | Delivering todo(s) | Proving QA scenario | Evidence |
| --- | --- | --- | --- |
| IS-1 | 1—78（除 76）+ F1；T76 经 F5 | 计划/证据逐项对照（F1，发布前范围） | `.omo/evidence/acp-parallel-plan-2-services/f1-plan-compliance.md` |
| IS-2 | 6、26—29、66 | 护栏套件全绿（架构/标记/编辑覆盖/frames/隔离/recovery） | `task-6`/`task-26`…`task-29`/`task-66` 证据 |
| IS-3 | 6、25、32、40、72、77—78 | `test_acp_services_isolation.py` + wheel include + 旧路径同类身份 + acp.cli 零转调 + 注入端口（72）+ 迁移闭包/独立装配（77—78） | `task-6`/`task-32`/`task-40`/`task-72`/`task-77`/`task-78` 证据 |
| IS-4 | 11—18 | 六域 round-trip + prepare 恢复 + cache-only 矩阵 + 越权矩阵 | `task-11`…`task-18` 证据 |
| IS-5 | 19—24 | 投影等价/预算/事件协议/顺序/授权流用例 | `task-19`…`task-24` 证据 |
| IS-6 | 30—34 | envelope 往返 + worker AST + 整链切换 + 回执拒绝 + 逐工作流矩阵 | `task-30`…`task-34` 证据 |
| IS-7 | 35—41、74 | 离线闭环 scripted E2E + CLI 对拍 + Standalone 配方 + 真实 CLI 全流程与重启恢复（74） | `task-35`/`task-41`/`task-74` 证据 |
| IS-8 | 42—47 | JSON/退出码 + dry-run 零副作用 + MCP 联调 + 归档往返 + 日志脱敏 | `task-42`…`task-47` 证据 |
| IS-9 | 48—56、73 | 开通幂等 + 准入竞争 + 隔离矩阵 + 管理权限 + 身份生命周期 + Lab doctor + 用量/配额模式与可信采集 | `task-48`…`task-56`/`task-73` 证据 |
| IS-10 | 53—65（含 gate 记录） | 政策显式化 + 用量/配额/账本矩阵 + 条件门拒绝证据 | `task-53`…`task-65` 证据 |
| IS-11 | 66—71、75、77—78 + F3/F4；发布结果=T76+F5 | 分层验收报告 + 故障矩阵 + 备份演练 + 四组合联合验收 + 兼容窗口；发布前/后分记 | `task-66`…`task-78` + `f3`/`f4`/`f5` 证据 |
