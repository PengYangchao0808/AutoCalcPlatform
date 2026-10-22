# acp-cccp-architecture-remediation - Work Plan

## TL;DR (For humans)
<!-- Filled LAST, after the detailed plan, so it summarizes the REAL plan. -->

**What you'll get:** 把量子化学"简单计算能力"整理成底层库自己就能独立调用的任务接口，上层计算流程只负责编排；同时补上隔离、能力声明与依赖方向的护栏，并用真实小规模计算验证整条链路仍然正确。

**Why this approach:** 先修确定性的依赖倒置与能力选择缺陷，再把执行核心下沉为一个独立任务库、逐个能力落地，然后改接上层流程与翻译职责，最后清理文档；旧入口中真正的转发模块只做转发（不保留第二套计算或默认值），而 **backend-direct 的遗留批量面 `acp.backends.batch` 作为显式隔离的兼容面保留**（非生产、非单任务核心；生产模块不得直接依赖或执行旧批量入口，兼容根包重导出允许保留——`legacy_batch_quarantine` 门）。

**What it will NOT do:** 不做独立发行包；不恢复退役入口、独立命令行或旧机制代码；不重写科学算法；不删除平台契约与既有护栏。

**Effort:** XL
**Risk:** High - 跨包大迁移，触及执行核心、能力选择、翻译与上层流程改线；用行为锁、等价性对照与真实小样收敛回退风险。
**Decisions to sanity-check:** 任务 API 命名（`cccp.calculation`）与"统一封套 + 类型化选项"形状；独立分发暂缓另案；一次性规划完整 P0–P3；遗留底层接口本轮只标注弃用、不删除；旧批量公开入口（`acp.backends.batch`）**保留为隔离的遗留兼容执行面**（backend-direct、非生产路径、不删除；生产模块不得直接依赖或执行旧批量入口，兼容根包重导出允许保留——`legacy_batch_quarantine` 门；删除需显式声明为兼容性变化）。

Your next move: 计划已通过 momus + 独立 Oracle 双路高精度复核（r14；含 r12 基线设计、commit 重基线与用户评审 #2 修订），并经用户复核确认可进入实施；本计划不自行执行，请在独立执行会话中按波次启动（Wave 0/P0 起）。完整执行细节见下。

---

> TL;DR (machine): XL/High — cccp.calculation simple-task API (unified envelope + typed options), P0–P3 four-layer remediation, A1–A9 acceptance with isolation/goldens/real-QC evidence; r12 dual review APPROVED, implementation authorized (user 2026-10-03).

## Scope

### Must have

1. **P0 确定性修复**：消除 3 处反向导入（`cccp/qc/interfaces/orca.py:138,156`、`cccp/core/protocols.py:281`）；修正 CREST stub/能力矩阵/registry 选择；建立中立错误类型（`TaskInputError`/`UnsupportedCapabilityError`/`BackendUnavailableError`）供 P0 即可抛出，其导入不得触发任务执行模块或 backend 注册；隔离探针测试 + cccp→acp 静态依赖守护。
2. **P1 `cccp.calculation` 任务 API**：可序列化 `TaskRequest`（计算意图/输入引用/类型化 options/资源）与运行时 `TaskContext`（已解析 config/工作目录/进度 sink）**分离**；封闭判别 typed options union + 类型化 `TaskResult` 载荷 + `schema_version`/`to_dict`/`from_dict` 及序列化规则（未知字段/非法枚举/NaN/Inf）；**落地顺序：科学契约→请求/结果/上下文类型→ACP 适配器→backend 迁移→选择器/运行预检**（消除交付物依赖环）；`select_backend(request, *, context)` 两步选择（语义选择→运行预检）并记录选择结果（后端/能力/原因/最终参数/所需程序与可用性），**预检使用本次调用 context 的程序路径，不回读全局配置**，**不得因另一后端也"声明支持"而静默改变计算模型**（显式 ORCA 缺 ORCA → `BackendUnavailableError`）；七个核心任务各自独立实现，**每种能力只有一个任务执行核心**；通用批量执行器只负责同类任务的并发/缓存/进度，**未命中缓存的条目必须调用单任务核心**；旧批量公开入口（`acp.backends.batch`）**保留为隔离的遗留兼容执行面（sanctioned legacy surface）**：不删除、不改行为，但它 backend-direct、**非生产路径、非单任务核心**，**生产模块不得直接依赖或执行旧批量入口，兼容根包重导出允许保留**（`legacy_batch_quarantine` 门）；纯兼容转发模块单独约束（允许转发，禁止自选默认值或执行 QC）。生产批量路径在单任务核心落地后切换；删除公开入口须显式标注为兼容性变化。`acp/backends/*` → `cccp/backends/*`（**backends 不得导入任务执行模块**）；`acp/calculations/contracts.py` **按字段职责拆分 + 显式转换**：单结构几何/单电子态/CASSCF 参数/科学工件引用迁 `cccp`，workflow/profile/candidate 身份/状态扫描编排留 ACP，旧请求/结果形状为 ACP 兼容契约（经适配器转换，**语义已变化的类型不强求 identity**）；计划与平台类型（`StepKind`、`StepSpec`、`CalculationStep`、`CalculationPlan`、`validate_plan`、`TaskManifest`、`Checkpoint`）留 ACP；输入加载改走/迁入 `cccp.io`（不得带入 `acp.io`/`acp.core.models`）；计算参数默认值/冲突规则（含现 `_clamp_to_functional` 对 basis/dispersion/RI/aux 的实际修改语义）由 cccp `ResolvedCalculationSpec` 唯一解析，ACP 只做旧字段迁移与告警；七核心任务能力映射在封套/选择器落地时（todo 13）完成、P2 仅扩展；**热化学单一实现且单向**：共享低层 Shermo 适配 + 纯科学归一化置于 `cccp.qc`（任务层之下），新任务入口与旧 ExternalBackend 兼容入口复用同一实现，保留参数/标准态/输出路径/错误语义；频率的科学解析与平台发布分离。
3. **P2 改线与翻译收口**：P2 **七项任务**（conformer_search/md_sampling/clustering/censo_refine/nmr_shielding + **xtb_path_search**（XtbPathSearch）/ **orca_gradient**（OrcaGradient）；各自定义输入形态、backend/能力映射、成功/部分失败/空结果语义、工件与记录身份映射、独立调用测试），再接入 Confsearch/NMR，并消除新工作流（XtbPathSearch/OrcaGradient）的 `get_backend` 直构；**NMR 保留 ACP 的 CREST→CENSO 编排**（分别调用 cccp 单项任务；能窗筛选/自由能选择/Boltzmann 权重仍归 ACP）；路由行/CENSO 模板/方法默认由 cccp 翻译层唯一生成；Confsearch 四协议（含 DFT handoff、Shermo）与 PES 改接独立任务，**普通/批量/PES 三路径共享单任务核心**，旧批量公开入口保留为隔离的遗留兼容面（非生产导入）；依赖守护升级为 AST + 允许依赖表 + 转发模块责任规则 + 负例注入的行为验证。
4. **P3 清理与文档**：遗留 cccp API（pipeline/state_manager/protocols/cluster）弃用标注不删除；**Shermo 入口/实现数量说明（与 todo 31 一致）**；AGENTS/README/架构文档/版本漂移（0.1.3）同步；**Wave 0 先写入迁移期工程规则**（新驻点、过渡期双驻点、唯一实现当前驻点台账），最终再收口根 AGENTS #17。
5. **A1–A9 验收**：三组等价（历史集成→迁移后 ACP；历史集成→独立 cccp；迁移后 ACP→独立 cccp）；隔离（**覆盖全部已公开任务**：七核心 + P2 七项）/调用矩阵/能力矩阵/**跨版本恢复**/平台契约回归/真实 QC 小样（含 scan、IRC、CASSCF、Molclus MD、XtbPathSearch、OrcaGradient 的真实小样或固定真实输出 fixture）/远程 LSF 生命周期；**真实验证按 PASS/FAIL/NOT_VERIFIED 三态分列**，必需项 FAIL 即整体不通过，存在必需项 NOT_VERIFIED 时不得给出 A1–A9 全通过结论。

### Must NOT have (guardrails, anti-slop, scope boundaries)

- **不恢复退役 CLI/工作流**（ensemble/energy/mechanism/optfreq 等），不恢复 cccp 独立 CLI，**不做独立分发定义**（用户决定另案）。
- **不重写科学算法**：CREST/CENSO/DP4/DP5/扫描/IRC 语义不变；不改变现有数值参数含义。
- **不删除 ACP 平台契约**：任务目录/manifest/帧投影/checkpoint/远程读写/暂停继续取消/历史只读必须继续工作。
- **cccp 不得 import acp**（含函数内/懒加载）；cccp 任务不得写 `RESULT/result_manifest.json`、`frame_candidates` 或任何平台视图。
- **backend 层不得导入任何任务执行模块**（`cccp.calculation` 的任务执行面/`run_*`；纯类型白名单 `cccp.calculation.errors`/`contracts` 除外），不得调用任务级 calculator；**回调驱动的批量执行器**不得直接调用 backend 能力方法（须经单任务核心，生产路径）；**纯兼容转发模块单独约束**（允许转发，禁止自选默认值或执行 QC）；**backend-direct 的遗留批量面 `acp.backends.batch` 显式隔离**（非生产、非任务核心；生产模块不得直接依赖或执行旧批量入口，兼容根包重导出允许保留；删除需显式兼容性变更）；`cccp.calculation` 包初始化不得触发任务执行模块导入或 backend 注册；
- **任务 API 不得保留**：`resources.extra` 原始后端关键字直通、无算法的 `backend="auto"`、`caller` 语义通道、无类型 `metadata` 倾倒场；平台身份（`trajectory_item_id` 等）不得固化进科学 options，由 ACP 按任务事件绑定。
- **兼容层可做请求转换、错误映射与平台结果发布，但不得再次选择科学默认值、执行 QC 或解释原始科学结果**；适配器只做数据转换、publisher 只做持久化（由调用方组织两者，round-trip 测试不写文件）；平台身份/绑定信息（candidate/profile/任务关联）只存在于 ACP；每种计算能力只有一个执行核心；`StepKind` 仍拒绝 IRC 进 plan。
- **迁移期不得要求整体兼容一次性完成**：按任务逐项切换，记录每项能力唯一实现的当前驻点；`A is B` identity 只用于语义未变化的搬迁类型。
- 不引入 pymatgen；不新增裸 `except Exception`；不绕过 `acp.results.frames` 契约；不违反根 AGENTS 其余铁律。
- **不删除现有护栏**（`tests/test_architecture_invariants.py`、`test_f4_scope_audit.py` 等重定向/改写，不静默移除）。

## Verification strategy
> 自动验证全部由 agent 执行（Zero human intervention for automated checks）；最终交付仍经 Final verification wave F1–F4 与用户确认（人工验收点仅限结论确认）。
- **编号消歧（Metis 修正）**：报告发现编号为 `F1–F16`；oracle 对任务 API 提案的设计复核发现编号为 `R-1–R-14`（原写作 F-1…F-14，为免冲突改名 R）。计划正文引用时分别标注。
- **Test decision**: TDD 特征化先行（用户 2026-10-03 确认），但**基线波只提交能通过的特征化测试**；目标行为红测与对应实现 todo 做红→绿配对，不把**未跟踪**红测留在 Wave 0；**P0 隔离探针与真实 QC 骨架为例外**——以 `pytest.mark.xfail(strict=True, raises=<预期异常>, reason="pending todo <N>")` 跟踪**具体未实现断言**（限定预期异常，防止意外失败被吞；XPASS 视为需立即移除标记），不计入"非预期失败"，**对应功能落地时立即移除、不等到最终波次**；Wave 6 验收范围不得残留本计划 xfail，最终以 `--runxfail` 复核。A9 三态（真实执行轴）：必需项 FAIL 即整体不通过；`NOT_VERIFIED` = 环境不可用 **或**（允许 recorded output 的能力）真实执行未做（评审 r15 修正）。特征化固定"**集成路径**（ACP 在位）的默认值 / 显式覆盖 / 错误语义 / 序列化"，**不得把隔离缺失 bug 的现状锁成 goldens**（隔离成功是目标变更，不属回归）。框架 `pytest`（pyproject `testpaths=["tests"]`）。
- **A7 三组等价（评审修正）**：① 历史集成路径 → 迁移后 ACP（平台兼容）；② 历史集成路径 → 独立 cccp（原有科学语义保留）；③ 迁移后 ACP → 独立 cccp（适配器不新增默认值/不丢字段）。Wave 0 记录**预迁移 goldens** 到 `tests/baseline/cccp_calculation_goldens/`：具体翻译产物（ORCA route 行、解析后的 method/basis/dispersion/auxJ/auxC/solvent/grid/SCF、Hessian interval、单位字符串、错误 token）**并覆盖 optimize rescue、scan 多坐标/部分失败、IRC 单向失败、CASSCF/NEVPT2、Shermo 标准态、CENSO 记录身份/自由能、NMR atom 索引/shielding**；goldens 记录源 commit、生成脚本、输入、配置摘要与文件哈希。**另建预期行为变化表**：P0 修复导致的启动前拒绝/错误分类变化属已批准差异，不要求旧错误文本一字不变。`tests/test_cccp_calculation_equivalence.py` 断言 goldens 非空且为迁移前生成（非迁移后回填）。
- **快速边界**：`python3.11 -m compileall src tests && pytest tests/ -m "not slow" -q`（CI 等价）；隔离/依赖守护单测常驻。
- **隔离验证分两层（评审修正）**：P0 低层隔离（`tests/test_cccp_isolation.py`：新进程 `meta_path` blocker + 输入生成，Todo 10 转绿）**与 P1 任务隔离**（`cccp.calculation` 导入/执行，随 Todo 13/17 起增量转绿、Todo 35 收口）分开，避免 P0 验收要求尚未存在的 API。不满足于 `acp not in sys.modules`；静态 AST 扫描 `src/cccp/**` 的 `acp` 导入（含缩进/懒加载/`TYPE_CHECKING`）。**导入环验证在全新进程分别以两种顺序导入**（先 `cccp.backends` 后 `cccp.calculation`、反向；`cccp.calculation.errors` 同理），且 `cccp.calculation`/`errors` 的导入不得触发 `cccp.calculation.tasks` 或 backend 注册。
- **依赖与行为守护（评审修正）**：`scripts/check_grep_gates.py` 新增 cccp→acp 门、workflow 执行性调用门、路由拼接门、**backend 不得导入任务执行模块门**；运行模式命名为 **`--suite architecture-remediation`**（仅本整改范围；历史未处理门单列报告，不用 `--all` 误导）；allowlist 只减不增。**行为验证**（`tests/test_grep_gates_script.py` + AST 检查 + 模块允许依赖表 + 工作流调用探针）必须包含**负例注入**：加入反向 import、第二条 QC 执行路径、路由拼接或 backend→任务调用后，检查必须失败（不接受"删测试才变红"）。`tests/test_f4_scope_audit.py` 重定向后必须验证**实际审计到迁移文件**（不得因新路径不在旧基线而 skip 关键检查）。
- **A3/A4 调用矩阵**：产物 = `docs/ACP_CCCP_Call_Matrix.md`（**14 工作流** × 协议/分支 → 唯一任务入口）+ `tests/test_acp_workflow_task_matrix.py`（AST/运行期断言：入口只经 `cccp.calculation.run_*`；无执行性直调 backend/runner；无第二套实现），覆盖全部 14 工作流（含 Confsearch 四协议与合法精修分支、NMR GIAO、PES scan/SP、BatchOptimize 四 profile、TS/IRC、casscf/xtb_optimize/tsmode、**XtbPathSearch/OrcaGradient**）。
- **跨版本恢复（评审修正）**：Wave 0 固定恢复 fixtures（已完成+未完成步骤并存的 checkpoint、部分失败结果、历史 manifest、批量 SP 缓存、远程路径引用）；迁移后验证 `continue` 不错误重算已完成步骤、不丢工件、不复用不兼容缓存。`plan fingerprint` 依赖 `str(step.spec)`，契约/默认值/repr 变化可能改变指纹——须显式测试兼容或版本化；SP 缓存键引入 `ResolvedCalculationSpec` 后定义**缓存版本与旧缓存复用条件**（不要求"缓存键永不变化"）。
- **A8 回归清单**：定向跑 `tests/test_remote_phase1.py`、`test_acp_api_v1.py`、`test_calculation_executor.py`、`test_checkpoint_continue.py`、frames/structure-viewer 相关测试、scheduler pause/continue/rerun/purge 测试，并新增"迁移后任务目录/manifest/帧/历史只读"针对性回归；新增工作流测试（`test_acp_workflows_xtb_path.py`、`test_acp_workflows_orca_gradient.py`、`test_acp_cli_xtb_path_register.py`、`test_acp_scheduler_xtb_path.py`、`test_acp_orca_gradient_registration.py`、`test_cccp_xtb_path.py`）纳入定向套件。
- **真实计算（A9，评审修正）**：`pytest tests/test_cccp_real_qc_smoke.py --run-integration`（`requires_orca/crest/xtb/isostat/shermo` 门控）；状态三分 **PASS（真实运行且结果验证通过）/ FAIL / NOT_VERIFIED（环境不可用或未执行）**；**每项另记 `evidence_type: live | recorded_output | mock`**；**Wave 0 固定清单**：哪些能力必须 live、哪些本轮允许 recorded output、样例/软件版本/断言/容差、远程生命周期前提；**PASS 要求 `evidence_type: live`**（真实执行轴）；`recorded_output` 只证解析/适配、**不得作为『真实执行验证』PASS**；`mock` 不得用于 PASS；**回放计分（用户 2026-10-03 补定）**：固定真实输出回放通过 → 该能力『解析与适配验证』记 PASS、『真实执行验证』记 NOT_VERIFIED；A9 总结只按 Wave 0 冻结的必需 live 清单判定（允许 recorded output 的能力本轮不属必需 live）；**必需项存在 FAIL 即整体不通过**；整体报告分列"代码与离线验证状态"与"真实环境验证状态"；存在必需项 NOT_VERIFIED 时不得给出 A1–A9 全通过；**验收前清除本计划遗留 xfail 并以 `--runxfail` 复核**。真实样例须含断言（能量/坐标/频率有效性、工件存在且对应正确结构、稳定样例数值容差、原子顺序与记录身份、软件版本/输入/日志），并明确 scan、IRC、CASSCF、Molclus MD 采用真实小样还是固定真实输出 fixture。远程生命周期按 `~/.cccp.yaml` 的 LSF 节点执行。
- **证据路径**：`.omo/evidence/acp-cccp-remediation/task-<N>-<slug>.<ext>`（日志/JSON/命令输出）。

## Execution strategy

### Parallel execution waves
> 波次由**可验证的架构状态**决定，不设"每波 5–8 个 todo"模板（评审修正）；退出条件见各波括号。
> 编号连续覆盖 1–43；Final verification wave 的 F1–F4 在**全部 43 个 todo 完成后**并行执行（彼此并行，不与验收 todo 并行）。

- **Wave 0（固定依据与迁移期规则，todos 1–5）**：基线/工作树/用例数；迁移期工程规则（新驻点、过渡期双驻点、唯一实现当前驻点台账）；恢复 fixtures；**只提交能通过的特征化测试**与预迁移 goldens；低层隔离探针；suite 守护与负例注入；真实 QC 冒烟骨架。**退出**：既有测试可用、迁移依据完整、快速套件**无非预期失败**（已知红探针以 xfail 跟踪且集合只减不增）。
- **Wave 1（P0，todos 6–10）**：Hessian 策略；METHOD_META + `ResolvedCalculationSpec` 参数解析基础；能力矩阵/registry 修正 + 中立错误类型；反向导入清零；低层隔离收口。**退出**：底层能力无 ACP 反向依赖。
- **Wave 2（契约边界与首个任务前准备，todos 11–16）**：字段级契约拆分 + 请求/结果/上下文封套 + 序列化 + ACP 适配器（含 ACP 侧绑定信息）；backends/Registry 迁移 + 通用批量执行器（**旧批量入口保留兼容**）；两步选择器/运行预检接入已迁移 registry + 七核心能力映射；热化学共享适配/归一化（`cccp.qc`）；逐任务兼容切换与冻结 gate suite 重定向；护栏行为化与 scope-audit 验证。**退出**：契约/封套冻结、适配器可往返、backend 边界冻结、选择器可用、SP 完整路径可落地。
- **Wave 3（核心任务，todos 17–23）**：singlepoint → optimize → frequency → scan → irc → casscf+thermochemistry → executor 改线。**退出**：七任务独立可执行、ACP 恢复兼容。
- **Wave 4（P2 实现与改线，todos 24–29 + 42–43）**：P2 契约(24) 与翻译层(25)（**24、25 完成后进入 42/43**）→ **七项执行实现(42/43)**；Confsearch(26)、NMR(27)、PES + **新工作流改线**(28)；依赖守护零豁免(29)。**退出**：每项 P2 任务独立可调用、**全部 14 工作流无执行旁路**。
- **Wave 5（P3 清理与文档，todos 30–34）**：遗留 API 弃用标注；**Shermo 入口/实现数量说明（todo 31）**；AGENTS/README/规则与审计工件；新架构文档；旧重构方案取代标注。
- **Wave 6（A1–A9 验收，todos 35–41）**：A1/A2、A3/A4、A5、A6、A7（三组等价）、A8（含跨版本恢复）、A9（三态）。
- **Final verification wave**：F1–F4（全部 todo 完成后并行）。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 基线/迁移期规则/恢复 fixtures | — | 2–43 | — |
| 2 绿色特征化 + goldens + 行为变化表 | 1 | 6,7,8,11,17+,39 | 3,4,5 |
| 3 低层隔离探针 | 1 | 10 | 2,4,5 |
| 4 suite 守护 + 负例注入 | 1 | 29,35 | 2,3,5 |
| 5 真实 QC 冒烟骨架 | 1 | 41 | 2,3,4 |
| 6 Hessian 迁 cccp | 2,3 | 9 | 7,8 |
| 7 METHOD_META + ResolvedCalculationSpec | 2 | 9,13 | 6,8 |
| 8 能力矩阵/registry + 中立错误类型 | 2 | 11,12,13,38 | 6,7 |
| 9 反向导入清零 | 6,7 | 10 | 8 |
| 10 P0 收口 / 低层隔离全绿 | 8,9 | 35 | 11 |
| 11 契约拆分+封套+序列化+适配器 | 2,8 | 12,13,14,15,17–23 | — |
| 12 backends/Registry 迁移 + 通用批量执行器（旧入口兼容） | 8,11,14 | 13,17–23 | — |
| 13 两步选择/预检 + 七核心能力映射 | 7,11,12 | 15,17–23 | — |
| 14 热化学共享 Shermo 适配/归一化（单一实现） | 11 | 12,22 | — |
| 15 逐任务兼容切换 + 冻结 gate suite 重定向 | 11,12,13 | 16,29 | — |
| 16 护栏行为化 + scope-audit 验证 | 15 | 17,29 | — |
| 17 singlepoint 任务（首个完整路径） | 12,13,16 | 18 | — |
| 18 optimize 任务 | 14,17 | 19 | — |
| 19 frequency 任务（科学解析/发布分离） | 18 | 20 | — |
| 20 scan 任务 | 19 | 21 | — |
| 21 irc 任务 | 20 | 22 | — |
| 22 casscf+thermo 任务（单次 Shermo、无重入） | 14,21 | 23 | — |
| 23 executor 改线 | 17–22 | 24,25,40 | — |
| 24 P2 任务契约 + 能力映射 | 23 | 42,43 | 25 |
| 25 翻译层：最小渲染 API + 上游收口 | 23 | 26,28,42,43 | 24 |
| 26 Confsearch/Shermo 改线 | 24,25,42,43 | 29 | — |
| 27 NMR 改线（保留 CREST→CENSO 编排） | 24,25,42,43 | 29 | 26,28 |
| 28 PES/路径工作流语义对齐 + 新工作流改线 | 24,25,42,43 | 29 | 26,27 |
| 29 依赖守护零豁免 | 15,16,26,27,28 | 30–41 | — |
| 30 遗留 API 弃用标注 | 29 | 35–41 | 31–34 |
| 31 Shermo 单一实现消费说明 | 29 | — | 30,32–34 |
| 32 AGENTS/README/规则/审计工件（最终收口） | 29 | 35–41 | 30,31,33,34 |
| 33 新架构 DevDoc | 29 | 35–41 | 30–32,34 |
| 34 旧重构方案取代标注 | 29 | 35–41 | 30–33 |
| 35 A1/A2 隔离独立性 | 4,10,29 | F1–F4 | 36–41 |
| 36 A3/A4 调用矩阵 | 29 | F1–F4 | 35,37–41 |
| 37 A5 翻译单源 | 25,29 | F1–F4 | 35,36,38–41 |
| 38 A6 能力矩阵 | 8,13,29 | F1–F4 | 35–37,39–41 |
| 39 A7 三组等价 | 2,29 | F1–F4 | 35–38,40,41 |
| 40 A8 平台契约 + 跨版本恢复 | 23,29 | F1–F4 | 35–39,41 |
| 41 A9 真实小样 + 远程（三态） | 5,29 | F1–F4 | 35–40 |
| 42 P2 执行实现 A：conformer_search/md_sampling/clustering/xtb_path_search | 24,25 | 26,27,28 | 43 |
| 43 P2 执行实现 B：censo_refine/nmr_shielding/orca_gradient | 24,25 | 26,27,28 | 42 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. 建立基线快照并让既有护栏适配本整改（不删护栏）
  What to do / Must NOT do: 记录 `main@2a23b93`（报告原基线 `88def44` 一并留档；特征化/goldens 在新 HEAD 生成）、工作树状态、`pytest tests/ -m "not slow" -q` 结果与用例数；静态确认新 active 工作流（`XtbPathSearch`/`OrcaGradient`）已入 catalog/registry/EDIT_ACTIVE_WORKFLOWS（本计划按 14 工作流口径纳入）；把 `tests/test_f4_scope_audit.py`（1031 行 sanctioned-scope AST 账本）的基线/范围改为本次整改起点或指向新位置，把 `tests/test_architecture_invariants.py` 的"基元唯一定义"护栏改为迁移期双驻点（`acp/calculations/primitives` 兼容 shim 与 `cccp/calculation` 实现）语义；把 `scripts/check_grep_gates.py` 中冻结的路径 pin（`unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo`）记录为待迁移重定向清单；确认权威报告 `docs/ACP_CCCP_Architecture_Report.md` 已被 git 跟踪（当前 untracked），若仍 untracked 则纳入版本控制或记录其 sha256，避免执行会话丢失依据；**写入迁移期工程规则**（根 AGENTS：新驻点、过渡期双驻点、唯一实现当前驻点台账；最终收口在 todo 32）；固定**跨版本恢复 fixtures**（已完成+未完成步骤并存的 checkpoint、部分失败结果、历史 manifest、批量 SP 缓存、远程路径引用）到 `tests/baseline/recovery_fixtures/`。**迁移台账行格式**：能力/当前实现驻点/兼容入口/生产消费者/退出条件/验收测试；**基线记录原有测试覆盖映射（测试名→覆盖点），测试数量不下降仅作次级信号**。Must NOT：删除或静默削弱任何护栏；改动产品代码。
  Parallelization: Wave 0 | Blocked by: — | Blocks: 2–43
  References: tests/test_f4_scope_audit.py; tests/test_architecture_invariants.py; scripts/check_grep_gates.py; tests/test_grep_gates_script.py; .git/refs/heads/main; src/acp/calculations/primitives/__init__.py
  Acceptance criteria (agent-executable): `pytest tests/test_f4_scope_audit.py tests/test_architecture_invariants.py tests/test_grep_gates_script.py -q` 全绿；`git status --porcelain` 基线记录；`.omo/evidence/acp-cccp-remediation/task-1-baseline.json` 含 HEAD/用例数/命令输出。
  QA scenarios (exact tool + invocation): happy = 上述 pytest 命令返回 0 且用例数≥基线；failure = 护栏被删除/静默削弱（`git diff` 检出未经计划的改动）或基线记录缺失。**"删护栏必须变红"的行为性负例注入统一由 todo 4 承载**，本 todo 不重复该操作。Evidence .omo/evidence/acp-cccp-remediation/task-1-guard-check.txt
  Commit: Y | test(guards): make architecture guards remediation-aware without weakening

- [x] 2. 绿色特征化测试、预迁移 goldens 与预期行为变化表
  What to do / Must NOT do: 锁定**当前集成路径已通过**的行为（Hessian 语义、METHOD_META 字段快照、能力选择现状），目标行为红测**留在各自实现 todo 做红→绿配对**（Wave 0 不提交红测）；记录预迁移 goldens 到 `tests/baseline/cccp_calculation_goldens/`，覆盖 route 行/解析后 method/basis/dispersion/auxJ/auxC/solvent/grid/SCF/Hessian interval/单位字符串/错误 token，以及 **optimize rescue、scan 多坐标与部分失败、IRC 单向失败、CASSCF/NEVPT2、Shermo 标准态、CENSO 记录身份/自由能、NMR atom 索引/shielding、XtbPathSearch/OrcaGradient 转换输入与产物摘要**；goldens 记录源 commit、生成脚本、输入、配置摘要与文件哈希，**排除时间戳/机器路径等非语义字段**；新建**预期行为变化表**（P0 修复导致的启动前拒绝/错误分类变化 = 已批准差异）。Must NOT：把"ACP 缺失时静默降级"的 bug 现状锁成回归金标；必须不在 Wave 0 提交会红的测试。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 6,7,8,17+,39
  References: src/acp/chem/composition.py:34-263; src/cccp/qc/interfaces/orca.py:143-172; src/acp/catalog.py:531-747; src/acp/backends/{registry.py:62-74,capabilities.py:63-162,crest.py:40-80}; src/acp/calculations/primitives/_common.py:42,238-245; tests/test_acp_chem_composition.py; tests/test_acp_backends.py; tests/test_acp_catalog.py; docs/ACP_CCCP_Architecture_Report.md:350-416
  Acceptance criteria (agent-executable): `pytest tests/test_acp_chem_composition.py tests/test_acp_catalog.py tests/test_acp_backends.py -q` 全绿；goldens 目录含上述能力/分支且带源 commit/哈希；预期行为变化表存在。
  QA scenarios (exact tool + invocation): happy = 上述 pytest 绿 + goldens 非空且带元数据；failure = golden 缺源 commit/哈希、含隔离 bug 现状、或 Wave 0 出现红测。Evidence .omo/evidence/acp-cccp-remediation/task-2-characterization.txt
  Commit: Y | test(p0): green characterization, pre-migration goldens, expected-delta table

- [x] 3. 低层隔离探针（P0 范围）与静态导入扫描
  What to do / Must NOT do: 新增 `tests/test_cccp_isolation.py`：在子进程用 `importlib.abc.MetaPathFinder` 阻断所有 `acp`/`acp.*` 导入，并 patch `subprocess.run/Popen` 为"一旦调用即报错"；**并提供"仅 cccp 源码"环境（不单一依赖同仓库 import blocker）**；断言 **P0 低层期望**（优化输入生成成功、协议解析带/不带 `recalc_hess` 均成功、DLPNO `auxJ/auxC` 在隔离与集成下一致）；**不在此 todo 断言 `cccp.calculation` 可导入**（任务层隔离随 Todo 13/17 增量、Todo 35 收口）；另加静态 AST 扫描断言 `src/cccp/**` 无 `acp` 导入（含缩进/懒加载/TYPE_CHECKING）。Must NOT：以 `acp not in sys.modules` 代替真实阻断。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 9,10
  References: docs/ACP_CCCP_Architecture_Report.md:350-416（附录 B 探针）; src/cccp/qc/interfaces/orca.py:127-172; src/cccp/core/protocols.py:273-283; tests/conftest.py
  Acceptance criteria (agent-executable): 当前为红：以 `pytest.mark.xfail(strict=True, raises=<预期异常>, reason="pending todo 10")` 跟踪**具体未实现断言**（不得整文件粗粒度 xfail），套件**无非预期失败**；**Todo 10 完成时立即移除 xfail**使 `pytest tests/test_cccp_isolation.py -q` 全绿，且断言与附录 B 的**低层**预期一致（4 值优化、带/不带覆盖协议、DLPNO 辅助基组）。
  QA scenarios (exact tool + invocation): happy = Todo 10 后 `pytest tests/test_cccp_isolation.py -q` 绿且低层断言全过；failure = 探针未真正阻断（如未 patch subprocess）导致假绿则修正。Evidence .omo/evidence/acp-cccp-remediation/task-3-isolation.txt
  Commit: Y | test(isolation): add subprocess ACP-import blocker probe for P0 layer

- [x] 4. 架构守护 suite（grep + AST + 允许依赖表）与负例注入
  What to do / Must NOT do: 为脚本增加 **`--suite architecture-remediation` 运行模式：只运行本整改拥有/更新的门**（新增门 + 四个冻结门 `unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo`）并汇总退出码；`--list-gates` 仅列名，不能用于验收。**历史遗留门（`calculations_no_mechanism_terms` 等，含失效路径 `src/acp/mechanism/stages/confirm.py` 已删除、`wave6` scope 为空）单列报告、不在该 suite 内**（另案处理；Must NOT 用扩大豁免换绿）。在 `scripts/check_grep_gates.py` 增加门：`cccp_imports_acp`（scope `src/cccp/`，模式 `from acp|import acp`，排除注释行）、`workflow_executes_qc`（scope `src/acp/workflows/`、`src/acp/confsearch/`、`src/acp/nmr/`、`src/acp/calculations/`（含 pes/batch；**BatchOptimize 批量 SP 共享核心 allowlist 条目在 todo 17 移除；PES 与新增工作流（XtbPathSearch/OrcaGradient）相关条目在 todo 28 移除**），模式 `get_backend\b|require_backend\b|run_shermo\b|CensoBackend\b`）、`workflow_route_assembly`（scope `src/acp/workflows/` + `src/acp/confsearch/`，模式 `"! "\s*\+`）、`backend_imports_task_module`（scope **能力模块**：`src/cccp/backends/` + `src/acp/backends/` 的 `{matrix,base,capabilities,registry,orca,crest,xtb,censo_backend,isostat_backend,molclus_backend,external_backend,external}.py`，模式 `cccp\.calculation(?!\.(?:errors|contracts)\b)|run_singlepoint|run_optimize|run_thermochemistry` 等**任务执行**符号（覆盖 `from cccp.calculation import …` 与 `cccp.calculation.<mod>` 两种形式；**纯类型依赖白名单**：`cccp.calculation.errors` + `cccp.calculation.contracts`（纯科学类型，自身不得导入任务执行/backend 注册/接口实现），**正则须整体排除白名单两种点号形式与 `from cccp.calculation import errors|contracts` 形式**，其余 `cccp.calculation.*` 执行面禁止；**复核 `cccp/qc/interfaces/__init__.py:10-18` eager import 面**——导入纯类型不得触发接口副作用））、`compat_forwarders_are_pure`（scope 纯转发模块 `src/acp/backends/__init__.py`（及后续真正的 re-export shim）；只许 re-export/`__all__`/docstring 或单 `return` 委托；**禁止**默认值表/`subprocess`/**调用形** `get_backend\s*\(`/`require_backend\s*\(`/QC 执行调用——import/`__all__` 中的符号名字符串不算命中）、`legacy_batch_quarantine`（scope 生产 `src/acp/**`，排除 `src/acp/backends/batch.py`、`src/acp/backends/__init__.py`、tests；模式 `from acp\.backends\.batch|import acp\.backends\.batch|from acp\.backends import batch`；**语义（用户 2026-10-03 定稿）：生产模块不得直接依赖或执行旧批量入口；兼容根包 `acp/backends/__init__.py` 保留重导出、不视为违规。AST 与运行期探针覆盖根包导入、别名调用（`from acp.backends import batch as …`）、多行导入，**并识别经根包重导出的旧执行符号属性调用——负例：`import acp.backends as backends; backends.batch_single_point(...)` 必须被检出（不能只匹配 `.batch` 模块导入语句）**。**；`acp.backends.batch` 为隔离的遗留 backend-direct 兼容面，不计入单任务核心）。以**当前违例的完整 allowlist**接入（干跑逐条记录全部 file:line），新增"allowlist 只减不增"断言；**allowlist 语义**：只跟踪**阻塞命中**；门级允许（如 `COMMENT_LINE_PATTERN`）使条目不再阻塞时，必须在同一 todo 从 allowlist 移除（仍满足"只减不增"）；冻结门既有命中纳入 Wave-0 基线 allowlist（`unique_run_scan`@`simple.py:520`、`final_shermo`@`capabilities.py:118,135`）。**行为验证**：AST 导入+调用检查、模块允许依赖表、工作流调用探针，并加**负例注入测试**（注入反向 import / 第二条 QC 路径 / 路由拼接 / backend→任务调用 / 转发模块内放入默认值或 QC 调用 / 生产模块导入 `acp.backends.batch`（含根包别名 `from acp.backends import batch`、`import acp.backends.batch as …` 与多行 import 形式）以及经根包重导出的属性调用 `import acp.backends as backends; backends.batch_single_point(...)`，检查必须失败）。**护栏主检职责与依赖**（模块职责 + 解析后的依赖目标 + 运行期执行探针），grep 仅快速提示；**违规标识用稳定三元组（规则+模块+符号）而非 file:line**；allowlist 要求旧违规消失、新违规不得增加。Must NOT：把门做成永久豁免；直接全红 CI；以"删测试才变红"冒充守护有效。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 29,35
  References: scripts/check_grep_gates.py:87,106-131,134-408,346-353; tests/test_grep_gates_script.py; src/acp/workflows/{energy_shared.py:259-260,383,460,490,nmr.py:437}; src/acp/calculations/{pes/scan.py:650,primitives/_common.py:321,primitives/thermochemistry.py:73,batch/singlepoint.py:161}; src/acp/workflows/simple.py:520; src/acp/scheduler/capabilities.py:118,135; src/cccp/qc/interfaces/__init__.py:10-18
  Acceptance criteria (agent-executable): `python3.11 scripts/check_grep_gates.py --list-gates` 含全部新门（含 `backend_imports_task_module`/`compat_forwarders_are_pure`/`legacy_batch_quarantine`）；`python3.11 scripts/check_grep_gates.py --suite architecture-remediation` 0 退出；负例注入测试在注入违规时失败、未注入时通过；allowlist 计数写入测试基线且新增条目会使测试失败。
  QA scenarios (exact tool + invocation): happy = `--suite architecture-remediation` 0 退出且负例注入全部检出；failure = allowlist 外新增 `from acp` 未报错、注入违规后检查仍绿、转发模块内默认值/QC 调用未被 `compat_forwarders_are_pure` 检出、或生产模块导入 legacy batch（含根包别名/多行形式）未被 `legacy_batch_quarantine` 检出。Evidence .omo/evidence/acp-cccp-remediation/task-4-gates.txt
  Commit: Y | test(guards): architecture-remediation suite with AST/dependency-table/negative-injection

- [x] 5. 真实 QC 冒烟测试骨架与证据约定
  What to do / Must NOT do: 新增 `tests/test_cccp_real_qc_smoke.py`，用 `@requires_orca/crest/xtb/isostat/shermo` 门控，断言最终期望链路经 `cccp.calculation`（小分子：ORCA sp/opt/freq、xTB opt、CREST search、ISOSTAT/CENSO/Shermo 小样）；建立 `.omo/evidence/acp-cccp-remediation/` 证据目录约定（版本/输入/命令/结果）。Must NOT：在无二进制时失败（必须 skip）；Must NOT 用 mock 冒充真实计算。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 41
  References: tests/conftest.py:281-299（`--run-slow`/`--run-integration`）; tests/AGENTS.md; /home/xieningke/.cccp.yaml（ORCA 6.1.1/CREST/xTB/CENSO/ISOSTAT/Shermo + LSF 节点）; docs/ACP_CCCP_Architecture_Report.md:310-326（A9）
  Acceptance criteria (agent-executable): `pytest tests/test_cccp_real_qc_smoke.py -q` 默认 **skip**（无 `--run-integration`）→ 波末无非预期失败；`--run-integration` 下以 `xfail(strict=True, raises=<预期异常>, reason="pending todo 41")` 跟踪**具体未实现断言**，**todo 41 验收前必须移除**；收集无错；证据目录 README/约定存在。
  QA scenarios (exact tool + invocation): happy = `pytest tests/test_cccp_real_qc_smoke.py --collect-only -q` 列出用例；failure = 缺少二进制时未 skip 而报错则修正门控。Evidence .omo/evidence/acp-cccp-remediation/task-5-smoke-skeleton.txt
  Commit: Y | test(qc): add real-QC smoke skeleton and evidence convention

- [x] 6. 把 Hessian 策略整体迁入 cccp 并从 orca/protocols 拆除反向导入（F1）
  What to do / Must NOT do: 新建 `src/cccp/qc/hessian_policy.py`，迁移 `LIGHT_ELEMENTS/HETEROATOM_ELEMENTS/NON_LIGHT_DEFAULT_INTERVAL/MAX_RECALC_HESS_INTERVAL/AUTO_RECALC_HESS/normalize_recalc_hess/classify_symbols/default_recalc_hess_for_symbols/HessianResolution/resolve_recalc_hess`；**新增 `tests/test_cccp_hessian_policy.py`**（独立 cccp Hessian 策略解析断言，含默认/显式/边界值）；`src/acp/chem/composition.py` 改为兼容 re-export（保留全部符号与 `__all__`）；`src/cccp/qc/interfaces/orca.py` 直接 import cccp 模块并删除 `_get_resolver` 的 lazy acp 导入（保留缓存语义）；`src/cccp/core/protocols.py:281` 改 import cccp；`src/acp/catalog.py:9` 保持可用（走 re-export 或直连 cccp）。Must NOT：保留 `try/except ImportError` 的第二套默认值；Must NOT 在两端复制默认表。
  Parallelization: Wave 1 | Blocked by: 2,3 | Blocks: 9
  References: src/acp/chem/composition.py:1-263; src/cccp/qc/interfaces/orca.py:143-172,1488-1520; src/cccp/core/protocols.py:273-283; src/acp/catalog.py:9,942,1738; tests/test_acp_chem_composition.py; tests/test_cccp_hessian_policy.py
  Acceptance criteria (agent-executable): `pytest tests/test_acp_chem_composition.py tests/test_cccp_hessian_policy.py -q` 全绿；`grep -rn "acp" src/cccp/qc/interfaces/orca.py` 无 import；隔离探针的"优化输入"四项转为成功。
  QA scenarios (exact tool + invocation): happy = 上述 pytest + `pytest tests/test_cccp_isolation.py::test_optimize_input_isolated -q` 绿；failure = 在无 acp 环境调用 `resolve_recalc_hess` 抛 `ModuleNotFoundError` 则未完成。Evidence .omo/evidence/acp-cccp-remediation/task-6-hessian.txt
  Commit: Y | refactor(cccp): move Hessian policy to cccp and drop reverse imports

- [x] 7. 拆分 METHOD_META 计算语义入 cccp，ACP catalog 仅组合 UI（F5）
  What to do / Must NOT do: 新建 `src/cccp/qc/method_meta.py`：迁移 `BASIS_CATALOG`、`_BASIS_CATALOG_REF`、`_ALL_BASIS_SETS`、`_COMPOSITE_BASIS_SETS`、`METHOD_META` 计算语义字段、`_derive_registry_fields`（依赖 cccp keyword_registry）、查询 API `method_meta()`/`case_insensitive_get()`；`src/acp/catalog.py` 改为从 cccp 导入并**仅保留 UI 组合**（`FIELD_DEFINITIONS`、`METHOD_SCHEMAS`、`FUNCTIONAL_OPTIONS_MAP` 投影、`normalize_legacy_method`/`_migrate_legacy_aux_basis`/`get_method_catalog` 载荷）；`src/cccp/qc/interfaces/orca.py:127-141` 改用 cccp 查询；修正 `tests/test_cccp_keyword_registry.py:28` 的反向测试依赖（parity 断言迁到 ACP 侧 `tests/test_acp_catalog.py`）。**同时下沉计算参数解析规则**（评审修正）：把 `_clamp_to_functional`（`src/acp/catalog.py:4801`）对 basis/dispersion/RI/aux 的**实际修改语义**、以及"显式值/任务 options/配置默认/方法默认"的**唯一优先级表**实现为 cccp `ResolvedCalculationSpec`（`src/cccp/qc/resolved_spec.py`）；ACP 只保留历史字段迁移与告警，UI 摘要从同一 resolved spec 派生；`MethodSpec` 与 `OptimizeOptions` 不得各留一份 solvent/grid/SCF 而无冲突规则。**补三张表（评审修正）**：① 来源优先级表（请求字段/工作流选定 level/运行配置/方法固有规则）；② 空值语义表（omitted/None/`""`/`"none"` → 继承/清空/关闭）；③ 冲突处理表（拒绝/保持 clamp/警告）。`ResolvedCalculationSpec` 返回**字段来源与调整记录**（`requested/effective/source/adjustment_reason`），UI 摘要/执行输入/provenance/缓存签名同源消费。**边界**：ACP 决定工作流采用哪个 level/profile（编排职责），CCCP 解释该 level 的科学参数、补默认值并渲染输入；显式传入 context 后执行过程不得重读全局配置。Must NOT：`/api/v1/method-catalog` 载荷形状变化；Must NOT 两端复制默认表或修改规则。
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 9,13
  References: src/acp/catalog.py:429-747,4504-4516,4782-4905,5513-5548,4801; src/cccp/qc/interfaces/orca.py:127-141,1285-1472; src/acp/calculations/levels.py:97,267,360,444,481; src/acp/api/v1_routes.py:6508-6545; tests/test_acp_catalog.py; tests/test_cccp_keyword_registry.py:28; tests/test_api_method_catalog_shape.py
  Acceptance criteria (agent-executable): `pytest tests/test_acp_catalog.py tests/test_cccp_keyword_registry.py tests/test_api_method_catalog_shape.py -q` 全绿；METHOD_META 字段值快照与迁移前字节一致；`tests/test_cccp_keyword_registry.py` 不再 import `acp`；`ResolvedCalculationSpec` 冲突/优先级用例全绿，无第二份默认或修改规则。
  QA scenarios (exact tool + invocation): happy = pytest 全绿 + 快照 diff 为空；failure = 前端载荷缺 `method_meta`/`functional_options_map` 形状则测试失败。Evidence .omo/evidence/acp-cccp-remediation/task-7-method-meta.diff
  Commit: Y | refactor(cccp): single-source METHOD_META calculation semantics

- [x] 8. 修正能力矩阵与 registry 选择，启动前拒绝不支持能力（F13 + xtb-frequency 漏洞）
  What to do / Must NOT do: **在 `src/cccp/calculation/errors.py` 先建立中立错误类型**（`TaskInputError`/`UnsupportedCapabilityError`/`BackendUnavailableError`，P0 起即可抛出，Todo 13 扩展而不重定义）；将 `CAPABILITY_MATRIX` 的 CREST `geometry_optimization`/`single_point` 由 AVAILABLE 改为 STUBBED、`frequency` 保持 NOT_IMPLEMENTED；**把矩阵语义定义为"声明=已实现"**：`external` 的 `clustering`/`thermochemistry` 由 `MISSING_BINARY` 改为 AVAILABLE（二进制缺失改由 per-capability runtime `is_available()`（`is_isostat_available`/`is_shermo_available`）判定）；**把声明矩阵与 `BackendCapabilityStatus` 抽到无依赖模块 `backends/matrix.py`（迁移后 `cccp/backends/matrix.py`）**，`registry.require` 与 `capabilities` 均从该模块导入，消除 `registry↔capabilities` 环依赖；`BackendRegistry.require` 改为按**声明状态 == AVAILABLE** 过滤（不再 `issubclass(Protocol)` 结构匹配）；`supports` 语义不变；选择在构造 backend 前完成；修复 `xtb` 无 frequency 方法导致 `AttributeError` 逃逸的路径（改为启动前结构化拒绝）。**中立错误类型/错误模块的导入不得触发任务执行模块或 backend 注册**；`cccp/calculation/__init__.py` 的公共导出必须无环（PEP 562 懒导出或最小导出；具体导出面在 todo 11 定稿，导入顺序断言在 todo 13 补齐）。Must NOT：仅改矩阵而不改选择逻辑；Must NOT 让 stub 可被选中；Must NOT 接受同时存在 stub 与第二份实现。
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 11,12,13,38
  References: src/acp/backends/registry.py:62-98; src/acp/backends/capabilities.py:63-162,194-265; src/acp/backends/crest.py:40-80; src/acp/backends/xtb.py; src/acp/backends/external_backend.py:41-50; tests/test_acp_backends.py:86-320,94; src/acp/calculations/primitives/_common.py:42,238-245; src/cccp/calculation/errors.py（新建）
  Acceptance criteria (agent-executable): `require_backend("optimization")` 返回 `ORCABackend`；`supports("crest","geometry_optimization") is False`；`require_backend("thermochemistry")` 返回 `ExternalBackend`（声明 AVAILABLE，二进制缺失由 runtime 判定）；`supports("xtb","frequency") is False`；`python3.11 -c "import acp.backends.registry, acp.backends.capabilities; import cccp.calculation.errors"` 无环依赖错误；全新进程 `python3.11 -c "import sys, cccp.calculation.errors; assert not any(m.startswith('cccp.calculation.tasks') or m.startswith('cccp.qc.interfaces') for m in sys.modules)"`（errors 导入不触发任务模块与 `qc/interfaces` eager import 副作用）；`pytest tests/test_acp_backends.py tests/test_acp_backend_censo.py -q` 绿（含 `test_acp_backends.py:94` 的期望同步）。
  QA scenarios (exact tool + invocation): happy = 上述断言 + import 冒烟通过；failure = 仍选中 `CrestBackend`、`require("thermochemistry")` 因 MISSING_BINARY 抛错、或出现环依赖 ImportError。Evidence .omo/evidence/acp-cccp-remediation/task-8-capabilities.txt
  Commit: Y | fix(backends): declaration matrix semantics, cycle-free module, deterministic pre-launch selection

- [x] 9. 反向导入清零并让静态 AST 扫描转绿
  What to do / Must NOT do: 确认 `grep -rn "from acp\|import acp" src/cccp --include=*.py` 只剩注释/docstring；更新 `src/cccp` 相关 docstring 中"lazy import acp"的过时说明；让 `tests/test_cccp_isolation.py` 的静态 AST 断言转绿。Must NOT：用 `try/except ImportError` 假装修复。
  Parallelization: Wave 1 | Blocked by: 6,7 | Blocks: 10
  References: src/cccp/qc/interfaces/orca.py:127-172; src/cccp/core/protocols.py:273-283; tests/test_cccp_isolation.py
  Acceptance criteria (agent-executable): `grep` 无 import 命中；`pytest tests/test_cccp_isolation.py::test_no_acp_imports_in_cccp -q` 绿；`python3.11 -m compileall src tests` 通过。
  QA scenarios (exact tool + invocation): happy = grep 空 + pytest 绿；failure = 任一函数内 `from acp...` 仍命中则失败。Evidence .omo/evidence/acp-cccp-remediation/task-9-grep.txt
  Commit: Y | refactor(cccp): zero reverse imports with static guard

- [x] 10. P0 收口：隔离探针全部转绿并记录证据
  What to do / Must NOT do: 用更新后的附录 B 探针验证：优化输入 `None/"auto"/0/10` 四值均成功；无覆盖与带 `recalc_hess=0` 的协议解析均成功；DLPNO `auxJ/auxC` 在隔离与集成下一致；能力选择对不支持组合启动前拒绝。记录版本/命令/输出。Must NOT：以 import 成功代替行为验证。
  Parallelization: Wave 1 | Blocked by: 8,9 | Blocks: 35
  References: docs/ACP_CCCP_Architecture_Report.md:310-326,350-416; tests/test_cccp_isolation.py; tests/test_cccp_hessian_policy.py
  Acceptance criteria (agent-executable): `pytest tests/test_cccp_isolation.py tests/test_cccp_hessian_policy.py -q` 全绿；`.omo/evidence/acp-cccp-remediation/task-10-p0-closure.json` 含每项断言的真实输出。
  QA scenarios (exact tool + invocation): happy = 隔离探针全绿且 DLPNO aux 布尔一致；failure = 任一隔离场景仍 `ModuleNotFoundError` 或默认值变化则失败。Evidence .omo/evidence/acp-cccp-remediation/task-10-p0-closure.json
  Commit: Y | fix(cccp): close P0 isolation and capability determinism

- [x] 11. 契约层：字段级拆分 + 请求/结果/上下文封套 + 序列化 + ACP 适配器
  What to do / Must NOT do: **先类型后适配器**（评审修正：消除"适配器依赖 todo 13 类型"的交付物依赖环）：
  (a) 按**字段职责**拆分并迁入 `src/cccp/calculation/contracts.py` 的仅限——单结构几何/单电子态/单次执行参数与科学工件引用（`JsonValue/JsonObject`、`StructureRole`、`StructureArtifact`（去掉 candidate 身份）、`ArtifactRef`、`Provenance`（去掉 profile）、`OptimizationSpec`、`OptimizationMode`、`GuessSpec`/`WavefunctionPolicy`/`SpinDiagnosticsSpec`/`SpinQualityGate`/`ElectronicStateSpec`/`CASSCFSpec` 及科学校验/序列化函数）；**留在 ACP**：`StepKind`、`StepSpec`、`CalculationStep`、`CalculationPlan`、`validate_plan`、`TaskManifest`、`Checkpoint`、workflow/profile/candidate 身份、`ElectronicStateConfig` 的状态扫描编排、旧 `CalculationRequest`/`CalculationResult` 形状（作为 **ACP 兼容契约**）。
  (b) 新建 `src/cccp/calculation/{__init__,requests,results,progress,context}.py` 并**扩展 todo 8 的 `errors.py`（不重定义）**：**可序列化 `TaskRequest`**（计算意图/输入引用/类型化 options/资源）与运行时 `TaskContext`（已解析 config/工作目录/进度 sink）**分离**，入口签名 `run_*(request, *, context=None)`；封闭判别 typed options union + task→options 注册表 + `validate_request`；类型化 `TaskResult` 与每任务 payload；序列化规则（未知版本/未知字段/非法枚举/NaN/Inf 的明确行为）；资源语义（`mem` 与 `maxcore` 关系）；**平台身份（workflow/profile/candidate_id/trajectory_item_id/state_sweep）不得进入请求**；输入形态规则（单结构/ensemble/轨迹/频率日志）与字段适用性；路径规则（相对基准/工件根/远程映射）。**两张表分离**：① `task→options/payload 类型映射` 在 todo 11 完成七核心（本 todo 即完整定义七核心 options/payload 类型与判别联合，可完整验证序列化与适配器转换）、todo 24 扩展 P2；② `task→execution function 执行映射` 由 todos 17–22、42–43 逐项完成（本 todo 不宣称任务可执行）。**errors/包初始化不得触发任务执行模块或 backend 注册**（PEP 562 懒导出或最小导出）。**本 todo 定义七核心任务的 options/payload/序列化形状**（Todo 17–22 只实现执行行为与任务特有约束；Todo 24 扩展 P2 契约）；**产出唯一现行 API 规范初稿 `docs/ACP_CCCP_Task_API_DevDoc.md`**（TaskRequest/TaskContext/TaskResources/TaskResult/TaskError/ArtifactRef + 各 task options/payload）；v1–v3.2 历史仅留档，**实施与测试只引用该现行规范**；往返验收只覆盖已承诺旧字段并附映射表。**统一失败/部分结果/记录身份契约**：启动前错误抛出；QC 不收敛/超时/解析失败 → 结构化 failed + 有效部分工件；scan 部分点/IRC 单向 → 保留有效子结果 + 整体状态规则；进度回调失败与科学失败区分；发布失败由 ACP 处理并保留科学成功；内部编程错误保留可诊断异常。不新增全局 PARTIAL 状态（任务级 `complete=False` + 子项，ACP 显式映射）。**记录身份**：原子索引基准（0/1）、NMR JSON 整数键恢复、CENSO 排序/筛选对应原构象、scan/IRC 失败帧保留原索引、gradient/振动单位-形状-原子顺序；**CCCP 可拥有局部科学记录编号**（隔离的仅是 ACP 平台身份）。**TaskContext 运行规则**：context=None 时配置/工作目录获取、相对输入基准、产物清理责任、重复调用/rescue/批量输出不覆盖、资源为每任务额度 vs 批次预算、进度回调线程安全、超时作用域（整任务 vs 每次 rescue）、取消时子进程/待执行条目处理；进度接口返回**科学事件**，由 ACP 转 LiveMetric 展示指标（CCCP 不搬 label_key/priority 等 UI 字段）。
  (c) 新建 `src/acp/calculations/legacy_adapters.py`：旧请求 → `TaskRequest` + **ACP 侧绑定信息**（candidate 身份/profile/任务关联，**只存在于 ACP**：由调用方随请求传入、随结果带回）；`TaskResult` + 绑定信息 → 旧结果。适配器**只做数据转换、不写文件/不发布平台产物**（持久化由 todo 16 的 `result_publication.py` 负责，调用方组织两者；round-trip 测试不写文件）。
  语义未变化的搬迁类型保留 re-export（`A is B` 仅用于这些）；语义已变化的请求/结果一律经适配器转换，不强求 identity。
  Must NOT：把计划/清单/checkpoint/平台身份迁入 cccp；对语义已变化的类型强求 identity；让 cccp 契约出现 workflow/profile/candidate_id/state_sweep/trajectory_item_id；适配器写文件、发布平台产物或自行补默认值；此 todo 内接真实计算执行（选择器在 todo 13、backend 迁移在 todo 12）。
  Parallelization: Wave 2 | Blocked by: 2,8 | Blocks: 12,13,14,17–23
  References: src/acp/calculations/contracts.py:22-1222（StepKind:29, CalculationStep:1076, CalculationPlan:1103, validate_plan:1119, StructureArtifact:1036, CalculationRequest:1058, CalculationResult:1147, ArtifactRef:1167, Provenance:1180, TaskManifest:1191, Checkpoint:1207, ElectronicStateConfig:270）; .omo/drafts/acp-cccp-architecture-remediation.md（Task API proposal v3，最新整合契约 §2–§4）; src/acp/calculations/primitives/_common.py:43-59,328-333; tests/test_calculations_contracts.py; tests/test_calculations_plans.py; tests/test_calculation_executor.py; tests/test_acp_electronic_state.py; tests/test_batch_optimize.py
  Acceptance criteria (agent-executable): 上述 pytest 全绿；新增 `tests/test_cccp_calculation_contracts.py` 全绿（options 类型不匹配→`TaskInputError`、`to_dict/from_dict` round-trip 含未知字段/非法枚举/NaN/Inf 规则、`TaskContext`/`TaskRequest` 分离、schema_version）；**`docs/ACP_CCCP_Task_API_DevDoc.md` 初稿存在且被 Todo 17–24 引用（不引用历史草案）；七核心 options/payload 形状齐备；统一失败/部分结果/记录身份与 TaskContext 运行规则有断言**；**字段级映射表**存在（科学字段→cccp / 平台字段→ACP）；`python3.11 -c "from acp.calculations.legacy_adapters import to_task_request, to_legacy_result"` 可导入；适配器**内存** round-trip 无损（不新增默认值/不丢字段；candidate/profile 经 ACP 侧绑定恢复）且不写文件；`python3.11 -c "import cccp.calculation as m; m.TaskRequest; m.TaskResult"`；`python3.11 -c "import cccp.calculation.contracts as m; assert not hasattr(m,'CalculationPlan'); assert not hasattr(m,'CalculationRequest')"`；全新进程 `import cccp.calculation` 不加载 `cccp.calculation.tasks`；**`import sys, cccp.calculation.contracts; assert not any(m.startswith('cccp.calculation.tasks') or m.startswith('cccp.qc.interfaces') for m in sys.modules)`（contracts 纯类型隔离：不加载任务模块与 `qc/interfaces` eager import）**。
  QA scenarios (exact tool + invocation): happy = 断言通过 + 全量 `pytest -m "not slow" -q` 绿；failure = 平台身份字段出现在 cccp 契约、适配器丢字段/写文件/补默认值、或导入 errors 触发任务/backend 模块。Evidence .omo/evidence/acp-cccp-remediation/task-11-contracts-split.txt
  Commit: Y | refactor(cccp): contract split + request/result envelope + in-memory adapters

- [x] 12. 迁移能力适配层到 cccp/backends；新增通用批量执行器，旧批量入口保留兼容
  What to do / Must NOT do: 移动 `src/acp/backends/{matrix,base,capabilities,registry,orca,crest,xtb,censo_backend,isostat_backend,molclus_backend,external_backend,external}.py` → `src/cccp/backends/`（`matrix.py` 为 todo 8 抽出的无依赖声明矩阵）；把通用 `acp/core/registry.py::Registry` 迁入 `cccp/core/registry.py`（或内联等价实现），ACP 侧兼容 re-export；把 `acp/backends/base.py::QCResult` 与 `cccp/qc/interfaces/base.py::QCResult` 合并为单一 `cccp.qc.interfaces.base.QCResult` + `to_qc_result`；`src/acp/backends/__init__.py` 改兼容 re-export。
  **通用批量执行器（评审修正）**：新建 `src/cccp/calculation/batch.py`，仅负责同类任务的并发/缓存/进度，接收**单任务执行函数**（如 `run_singlepoint`）逐项调用，**不得直接调用 `backend.single_point()` 等能力方法**；多能力步骤链（opt→freq→sp→thermo）继续留在 ACP；随本 todo 交付独立测试 `tests/test_cccp_calculation_batch.py`（并发/缓存/部分失败/进度回调；执行函数为测试替身）。**缓存身份与命中（评审修正）**：顺序=校验请求→解析有效科学参数→生成缓存身份→验证缓存与工件→未命中才运行预检并调用单任务核心；缓存身份覆盖任务/后端/有效参数/电子态/输入内容/辅助输入（Hessian/轨道）内容/软件版本/schema 策略；**旧缓存无法证明完整性 → 明确未命中**（批准的兼容变化）；**完整有效缓存命中不要求二进制可用**（科学校验与运行预检分离），实际执行才要求预检；**批次资源预算与单任务资源分离**（不得把同一 `nproc` 同时当并发数与每条目核数，验证总使用量）。**版本匹配规则（用户 2026-10-03 补定）**：科学输入生成基础签名；缓存记录保存实际软件版本；调用方指定版本约束时必须匹配，未指定时按明确的缓存兼容策略判断；版本信息不足且无法证明兼容时判为未命中。**缓存准备与单任务执行必须复用同一参数解析函数**（不得 batch 解析一次、`run_singlepoint` 再解析一次）。
  **旧批量入口处置（评审 r10 修正：不能是"纯转发"）**：`src/acp/backends/batch.py` 是 backend-direct 的公开入口（`acp.backends` 亦 re-export `BatchSpFrameResult`/`BatchSpResult`/`batch_single_point`），其签名接收 backend 对象并调用能力方法，**无法在不引入第二套 backend-direct 执行路径的前提下变成纯转发**（Oracle r10 blocker）。因此本轮显式把它定为**隔离的遗留兼容执行面（sanctioned legacy surface）**：**本 todo 不删除、不改其实现与行为**，仅把 `from acp.backends.base import …` 改为 `from cccp.backends.base import …`；它**不是生产路径、不计入单任务核心**，由新门 `legacy_batch_quarantine`（todo 4）保证**生产模块不得直接依赖或执行它**（兼容根包 `acp/backends/__init__.py` 重导出允许保留，仅此与 tests 除外）。其现行调用方（`_singlepoint_execution.py:311 _run_group` 传 backend 对象、`_SignallingBackend`、缓存 helper）在 **todo 17 切换到新通用执行器**（`cccp.calculation.batch` + `run_singlepoint`），切换时移除该生产导入，使 `legacy_batch_quarantine` allowlist 在 todo 17 清零。若执行中决定**删除**该公开入口，必须在计划与变更说明中显式标注为**兼容性变化**（不得默认删除）。
  Must NOT：在 `cccp/backends/` 保留第二套批量 SP 实现；让 backends/批量执行器导入任务执行模块（`contracts`/`errors` 纯类型白名单除外）；为通过门而删除旧公开 API；把 backend-direct 遗留批量面伪装成纯转发、或声称其走单任务核心；在 `run_singlepoint` 落地前切换生产批量路径。
  Parallelization: Wave 2 | Blocked by: 8,11,14 | Blocks: 13,17–23
  References: src/acp/backends/**; src/acp/backends/batch.py:21,28-51,302; src/acp/calculations/batch/{singlepoint.py:12-13,_singlepoint_execution.py:15-16,311-440}; src/acp/core/registry.py; src/cccp/qc/interfaces/base.py:17-36; tests/test_acp_backends.py; tests/test_acp_backends_batch.py:16; tests/test_acp_backend_censo.py; tests/test_molclus_backend.py; tests/test_thermochemistry.py
  Acceptance criteria (agent-executable): `grep -rnE "from acp|import acp" src/cccp/backends src/cccp/core/registry.py` 无命中；`grep -rnE "cccp\.calculation|run_singlepoint|run_optimize|run_thermochemistry" src/cccp/backends` 仅剩纯类型白名单（`contracts`/`errors`）或执行函数**参数**（无任务执行模块导入；执行依赖由 AST 规则判定）；`grep -rnE "backend\.(single_point|optimize|frequency)\(" src/cccp/calculation/batch.py` 无命中；`tests/test_cccp_calculation_batch.py` 全绿（执行函数替身；**断言批次并发 × 单条目核数的总使用量上限，资源分离**；**无二进制、完整且版本匹配的缓存 → 成功返回且未启动进程；相同原始请求、不同有效配置 → 缓存身份不同**）；`pytest tests/test_acp_backends.py tests/test_acp_backends_batch.py tests/test_acp_backend_censo.py tests/test_molclus_backend.py -q` 绿（**旧批量入口行为不变**）；identity 断言 `acp.backends.require_backend is cccp.backends.require_backend`；QCResult 单一定义；`legacy_batch_quarantine` 的 allowlist 记录当前生产消费者（`_singlepoint_execution.py`，待 todo 17 移除）。
  QA scenarios (exact tool + invocation): happy = 上述 pytest + identity + grep；failure = 旧批量入口被删导致 `tests/test_acp_backends_batch.py` 断链、QCResult 双定义、或新执行器直调 backend 能力方法。Evidence .omo/evidence/acp-cccp-remediation/task-12-backends-move.txt
  Commit: Y | refactor(cccp): move backends; add generic batch executor; keep legacy batch entry

- [x] 13. 两步选择器与运行预检（接入已迁移 registry）+ 七核心任务能力映射
  What to do / Must NOT do: 在 `src/cccp/calculation/selection.py` 落地**两步选择**：
  ① 语义选择——**能力判定输入 = 任务种类 + 科学选项 + 方法/电子态要求 → 所需能力及约束 → 可实现后端 → 本次所需软件**（非仅 task→backend 映射；如 optimize 按结构角色需 `optimize` 或 `transition_state_opt`）；显式 ORCA 缺 ORCA → `BackendUnavailableError`，**不得因另一后端也声明"支持"而静默改变模型**；
  ② 运行预检——**使用本次调用 `context`**（已解析 config/程序路径/资源）检查所选后端与本能力所需程序/二进制可用性。入口 `select_backend(request, *, context)`（或等价拆分 `select_semantic(request)` + `precheck_runtime(selection, context)`；单函数与拆分两种形态均有测试）。
  返回**选择记录**（后端/能力/原因/最终参数/所需程序与可用性）供 provenance/调试/测试。
  扩展 `src/cccp/backends/matrix.py` 声明矩阵覆盖**七个核心任务**的完整能力名与状态（含 `casscf`/NEVPT2 相关；本 todo 只声明与映射，不实现执行）；补 orca/xtb 优先序表。**P2 能力名与歧义优先序留给 todo 24**。
  Must NOT：以全局/默认配置代替本次 `context`（不得出现"选择器检查默认配置、执行器用调用者配置"的分歧）；不得引入 `backend="auto"` 字符串或 `resources.extra`/`caller`；不得在本 todo 实现任务执行；不得从 acp import。
  Parallelization: Wave 2 | Blocked by: 7,11,12 | Blocks: 15,17–23
  References: src/cccp/calculation/{selection.py,requests.py,results.py}（todo 11）; src/cccp/backends/matrix.py（todo 8/12）; src/acp/backends/{capabilities.py:63-162,registry.py:62-98}; .omo/drafts/acp-cccp-architecture-remediation.md（Task API proposal v3 §3）
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_calculation_selection.py` 全绿（两步选择记录字段；显式 ORCA 缺 ORCA 抛 `BackendUnavailableError` 不自动切 xTB；未知能力→`UnsupportedCapabilityError`；**同一 request 两份不同 context → 各自检查本 context 指定的程序路径，不回读另一份全局配置**；七核心能力映射断言含 `casscf`）；`python3.11 -c "from cccp.calculation import TaskKind; assert {'singlepoint','optimize','frequency','scan','irc','casscf','thermochemistry'} <= set(TaskKind)"`；**全新进程两种导入顺序均无环**（先 `import cccp.backends` 后 `import cccp.calculation`、反向），且 `cccp.calculation.tasks` 不被静默加载；**验收矩阵覆盖（仅七核心）**：普通 vs TS 优化、scan 模式/约束、CASSCF±NEVPT2；**区分"backend 方法已实现"与"公共 task 已可调用"**（todo 13 完成映射不等于任务可执行）。**P2 能力（GIAO/EnGrad/CENSO）的验收矩阵行在 todo 24 落地**（本 todo 不验收 P2）。
  QA scenarios (exact tool + invocation): happy = 选择器测试全绿 + 两种导入顺序无环；failure = 选择器读全局配置、缺 ORCA 自动切 xTB、能力映射缺 `casscf`、或导入环。Evidence .omo/evidence/acp-cccp-remediation/task-13-selection.txt
  Commit: Y | feat(cccp): two-step selection with runtime context and core capability mapping

- [x] 14. 热化学单一实现：`cccp.qc` 共享 Shermo 适配 + 纯归一化，两端复用（F15）
  What to do / Must NOT do: **撤销"ExternalBackend 必须改调 `batch_process_thermo`"的指定**（评审修正：该 runner 无 `output_file`/`standard_state`/`scl_zpe`/`ilowfreq`/`imagreal`/`conc` 入口、强制 `<output_dir>/<file_stem>/` 子目录、返回原始字典且不做标准态/单位归一化，**不能等价替代旧语义**）。
  改为在**任务执行层之下**建立共享实现：
  (a) `src/cccp/qc/shermo_adapter.py`（低层适配）：解析并保留全部旧参数（`output_dir`/`output_file`/`sp_energy`/`temperature_k`/`pressure_atm`/`standard_state`/`scl_zpe`/`ilowfreq`/`imagreal`/`conc`），默认路径语义与旧一致（`output_dir or log_file.parent`；`output_file` 默认 `<stem>.sum`），是热化学任务入口与旧 backend 入口的**共享执行/归一化路径**；`batch_process_thermo` 等低层兼容包装继续调用同一 runner 不构成重复科学实现；**一次请求只启动一次 Shermo**；
  (b) `src/cccp/qc/thermo_normalize.py`（纯科学归一化，无 I/O）：标准态（`1atm|1M`）校正、单位、gibbs 选择（沿用 `select_gibbs` 语义）、metadata/units 组装。
  旧 `src/acp/backends/external_backend.py::thermochemistry()` 改为调用共享适配 + 归一化并映射回 `QCResult`，**保留全部参数、标准态、输出路径与错误语义**；**不得导入 `cccp.calculation` 任务模块或任务级 calculator**；`cccp/qc/**` 不反向依赖任务层。
  后续 todo 22 的 `run_thermochemistry` 调用**同一**共享适配 + 归一化（不新增第二条 runner 调用路径）；旧 `ThermochemistryCalculator` 在 todo 22/23 收口前记录到迁移期双驻点台账，且不得新增分叉。**阶段边界（用户 2026-10-03 定稿）**：Todo 14 建立共享实现并改接旧 backend；迁移期旧基元按台账保留，最迟在 Todo 22/23 完成委托与旧实现清除。阶段性护栏按对应迁移状态验收。
  Must NOT：在 backend 里补第二套归一化；为规避路径门把共享实现放进 `cccp.calculation` 再另建 runner 调用路径；backends import 任务执行模块；保留两份热化学实现（以科学处理逻辑/归一化路径计，**不以源码中 `run_shermo` 的调用表达式数量计**）。**该冻结门 `wave2_shermo_external` 只保证旧 backend 不直呼 runner；API 等价由本 todo 的行为验收（单次调用/六参数/基线）判定，不由"函数名字面"决定替代 API。**
  Parallelization: Wave 2 | Blocked by: 11 | Blocks: 12,22
  References: src/acp/backends/external_backend.py:10-15,77-150; src/acp/calculations/primitives/thermochemistry.py:1-133; src/acp/calculations/primitives/_thermochemistry_{input,support}.py; src/cccp/qc/runners/__init__.py:79-265（`run_shermo`/`batch_process_thermo`）; tests/test_thermochemistry.py; tests/test_acp_backends.py
  Acceptance criteria (agent-executable): `grep -rnE "acp\.calculations|cccp\.calculation" src/acp/backends/external_backend.py` 无命中；`grep -n "run_shermo" src/acp/backends/external_backend.py` 无命中；**显式 `output_file` 与旧输出目录保持一致**（断言）；**六参数全部传递/处理**（`output_file`/`standard_state`/`scl_zpe`/`ilowfreq`/`imagreal`/`conc`；spy 逐项到 runner）；**`1atm` 与 `1M` 结果与基线一致**；**旧 backend 入口经共享适配且每次计算只启动一次 Shermo**（spy 计数=1；与任务入口的复用断言在 todo 22）；`pytest tests/test_thermochemistry.py tests/test_acp_backends.py -q` 绿；隔离探针下 Shermo 路径可运行（无 acp）；**默认输出路径双入口兼容（行为回归）**：旧基元默认 `Shermo.sum`、backend 默认 `<stem>.sum` 在共享层落地后分别保持。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + grep 空 + 单次调用/基线断言；failure = 参数丢失、输出路径改变、出现第二套归一化或双次 Shermo。Evidence .omo/evidence/acp-cccp-remediation/task-14-thermo-decouple.txt
  Commit: Y | refactor(cccp): shared Shermo adapter/normalization below task layer

- [x] 15. ACP 兼容层与冻结 grep-gate 路径重定向
  What to do / Must NOT do: 建立/校验 `acp.calculations.contracts`、`acp.backends`、`acp.chem.composition` 的转发式兼容；`acp.calculations.primitives.*` **按任务逐项切换**（每个 primitive 在其任务落地时变为 forwarder；**不要求一次性整体 identity**，评审修正）；把 `scripts/check_grep_gates.py` 中冻结路径（`unique_run_scan`/`unique_run_irc`/`wave2_shermo_external`/`final_shermo`）改为**双位置允许**（acp 旧位置 + cccp 新位置；或先保留 acp 允许，硬切换到 todo 23）；**把 `unique_run_scan`/`unique_run_irc` 的 scope/allow 收敛到基元实现文件（cccp task + acp shim），避免把工作流同名包装 `src/acp/workflows/simple.py::run_scan` 误判为第二实现**；**给 `final_shermo` 加 `COMMENT_LINE_PATTERN` 允许**（清除 `src/acp/scheduler/capabilities.py:118,135` 注释行命中）；`wave2_shermo_external` scope 随 backends 迁移指向 `cccp/backends/external_backend.py`（双位置）；同步 `tests/test_grep_gates_script.py`。Must NOT：删除门或放宽到无约束；**Must NOT 在 cccp 实现尚未存在（Wave 3 之前）就移除 acp 允许路径**（否则门误红）；Must NOT 要求全部 primitive 在任务落地前即 identity。
  Parallelization: Wave 2 | Blocked by: 11,12,13 | Blocks: 16
  References: scripts/check_grep_gates.py:87,106-131,167-408; tests/test_grep_gates_script.py; src/acp/calculations/contracts.py; src/acp/backends/__init__.py; src/acp/workflows/simple.py:520; src/acp/scheduler/capabilities.py:118,135
  Acceptance criteria (agent-executable): `python3.11 scripts/check_grep_gates.py --suite architecture-remediation` 0 退出；`pytest tests/test_grep_gates_script.py -q` 绿；四个冻结门仍存在且为双位置允许（或明确记录硬切换留待 todo 23）；`unique_run_scan`/`unique_run_irc` 不再把 `simple.py::run_scan` 判为第二实现；`final_shermo` 不再命中 `src/acp/scheduler/capabilities.py` 注释行。
  QA scenarios (exact tool + invocation): happy = `--suite architecture-remediation` 0 退出 + 测试绿；failure = 门被删除、放宽到无约束，或提前移除 acp 允许导致误红。Evidence .omo/evidence/acp-cccp-remediation/task-15-gate-redirect.txt
  Commit: Y | test(guards): redirect frozen grep gates to cccp locations

- [x] 16. 护栏与测试重定向（架构不变量、scope 审计、cccp 测试去 acp 依赖）
  What to do / Must NOT do: 更新 `tests/test_architecture_invariants.py`：把 `_PRIMITIVES_DIR`/`_PRIMITIVE_DEFS` 改为**双根**（`src/acp/calculations/primitives` shim + `src/cccp/calculation/tasks`），断言跨两根**合计恰一份** `run_*` 实现体；新增"任何 ACP 模块不得包含第二份 `run_*` 实现体"断言（防止 shim 让唯一性护栏空转）；**创建/指定具名 ACP manifest 写入模块 `acp/calculations/result_publication.py`**，把 `test_all_new_results_register_result_manifest` 指向它（executor/scan/irc 产物注册全部经它）；**职责分离（评审修正）**：适配器（todo 11）只做数据转换、不写文件；`result_publication.py` 只做持久化；调用方组织两者（round-trip 测试不写文件）；**双驻点表述按每项能力的当前驻点台账验收**（不使用"ACP 无实现/shim"笼统措辞）；台账每行含六列（能力/驻点/兼容入口/生产消费者/退出条件/验收测试）；**定义发布契约（评审修正，持久化顺序）**：QC 完成 → ACP **先持久化科学结果及工件引用** → 发布 manifest/帧/产品 → 标记发布完成；科学结果记录与平台展示清单职责分离；发布以稳定标识幂等；**恢复先检查已有科学结果，再决定是否执行 QC**；发布待恢复状态为 ACP 内部状态（不新增全局 JobStatus）。**故障注入**：科学结果已存+manifest 失败；manifest 更新+完成标记失败；部分产品复制后进程退出；远程结果存在+本地发布中断——断言已有有效科学结果时**恢复只重试发布、QC 调用次数不增**。**边界**：QC 进程结束到结果持久化之间崩溃依靠原始输出恢复或明确允许重算，不承诺全局恰好一次；更新 `tests/test_f4_scope_audit.py` 的审计范围/基线以覆盖本次迁移且不误伤，并**验证它实际审计到迁移文件**（不得因新路径不在旧基线而 skip 关键检查）；确保 cccp 侧测试文件不再 import acp（含 `test_cccp_keyword_registry.py`）；`pytest -m "not slow" -q` 全绿。**硬重定向到单根 cccp 留待 todo 23**。Must NOT：删除护栏文件；Must NOT 让护栏因 shim 而空转；Must NOT 在 Wave 2 就把根指向尚不存在的 `cccp/calculation/tasks`（会因空目录 0 命中而误红）。
  Parallelization: Wave 2 | Blocked by: 15 | Blocks: 29
  References: tests/test_architecture_invariants.py:256-265,327-359; tests/test_f4_scope_audit.py:149; tests/test_cccp_keyword_registry.py:28; tests/test_acp_chem_composition.py; src/acp/storage/manifest.py
  Acceptance criteria (agent-executable): `pytest -m "not slow" -q` 全绿（Wave 2 双根状态下成立）；`_PRIMITIVES_DIR` 为双根；具名 ACP manifest 模块存在且断言指向它；存在"ACP 侧无第二份实现体"断言；发布契约（幂等/恢复/失败不重算）有测试；scope-audit 对迁移文件**实际审计**（无 skip 绕过）且有一个"故意漏审"负例被检出；`grep -rn "from acp\|import acp" tests/test_cccp_*.py tests/test_qc_*.py` 无命中（keyword registry 除外已迁）。
  QA scenarios (exact tool + invocation): happy = 全量快速套件绿（双根：acp shim + cccp task 合计恰一份实现）；failure = 硬指单根空目录导致 0 命中、护栏仍指向旧语义、或测试反向依赖残留。Evidence .omo/evidence/acp-cccp-remediation/task-16-test-redirect.txt
  Commit: Y | test(guards): redirect architecture invariants and scope audit

- [x] 17. 任务 1：singlepoint 端到端最小完整路径
  What to do / Must NOT do: 在 `src/cccp/calculation/tasks/singlepoint.py` 实现 `run_singlepoint(TaskRequest)->TaskResult`：请求校验→两步 `select_backend`→翻译（方法/基组/溶剂/SCF/电子态）→接口执行→类型化 `SinglePointPayload`；`src/acp/calculations/primitives/singlepoint.py` 改为兼容包装（调 cccp 任务 + ACP 侧产物注册/帧绑定）；`SinglePointOptions` 含 `stability_check`。**确定翻译层最小公共入口并在此使用**（评审修正）：`resolve_spec`（cccp `ResolvedCalculationSpec`）+ `render_backend_input`（全面清理留 todo 25）；**切换生产批量路径（评审修正）**：通用批量执行器（`cccp.calculation.batch`，todo 12）接收 `run_singlepoint` 作为执行函数逐项调用；`src/acp/calculations/batch/_singlepoint_execution.py::_run_group` 改为经该执行器（**显式处理** `_SignallingBackend`、缓存 helper、进度回调与旧签名适配；缓存键/记录复用条件按 A8 定义）；`src/acp/backends/batch.py` **保持为隔离的遗留兼容面**（不转纯转发、保留 backend-direct 实现与公开符号 `BatchSpFrameResult`/`BatchSpResult`/`batch_single_point`）；**移除 `_run_group` 对 `acp.backends.batch` 的生产导入**（改走通用执行器），使 `legacy_batch_quarantine` allowlist 清零。todos 28/36 验证三路径（普通/批量/PES）一致。**同时把 `load_inputs` 的 `StructureReader`（`acp.io.structures`→`acp.core.models.Structure`）替换为 `cccp.io.input_handler.MolecularInputHandler.from_source`，或把 `StructureReader`/`Structure` 迁入 `cccp.io`**，确保 cccp 任务不 import `acp.io`。**把 `acp/calculations/primitives/_common.py` 的共享管线迁入 `cccp/calculation/_common.py`**，供 todos 18–22 复用（避免 cccp 反向引用 acp）；**自本 todo 起增量创建 `tests/test_cccp_calculation_equivalence.py`**（A7 对照 goldens 的测试在此累计）。Must NOT：在 cccp 侧写 manifest/帧；Must NOT 保留第二套执行（**含第二套批量执行**）；Must NOT 在 `run_singlepoint` 可调用前切换生产批量路径。
  Parallelization: Wave 3 | Blocked by: 12,13,16 | Blocks: 18
  References: src/acp/calculations/primitives/{singlepoint.py,_common.py:17-18,38,43-59,238-405,328-333}; src/acp/io/structures.py:15,29-82; src/cccp/io/input_handler.py; src/acp/backends/orca.py:86-105; src/cccp/qc/interfaces/orca.py:2497-2605; src/cccp/calculation/contracts.py（todo 11）; src/cccp/qc/resolved_spec.py（todo 7）; tests/test_calculation_executor.py; tests/baseline/cccp_calculation_goldens/
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_singlepoint.py` 绿；`pytest tests/test_calculation_executor.py -q` 绿；独立 cccp 调用与 goldens 的有效参数一致；`grep -rnE "from acp|import acp" src/cccp/calculation/tasks/singlepoint.py src/cccp/calculation/_common.py` 无命中；批量执行器以 `run_singlepoint` 为执行函数（接口断言）且 `_run_group` 走新执行器（调用栈/替身断言）；`tests/test_acp_backends_batch.py` 保持全绿（遗留面行为不变）；**生产模块不再直接依赖或执行旧批量入口**（兼容根包重导出允许；`legacy_batch_quarantine` 绿、allowlist 清零）；缓存命中/进度回调行为不变；`tests/test_cccp_calculation_equivalence.py` 存在且 singlepoint 用例绿；`/api`/executor 路径行为不变；**故障注入（科学结果已存+发布失败→恢复只重试发布、QC 调用不增）**；**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证（不集中留到最后）**。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + `pytest tests/test_cccp_calculation_equivalence.py -k singlepoint -q` 绿；failure = 翻译默认值与 golden 不一致或 ACP 侧仍直构 backend。Evidence .omo/evidence/acp-cccp-remediation/task-17-singlepoint.txt
  Commit: Y | feat(cccp): implement singlepoint task (first minimal closure)

- [x] 18. 任务 2：optimize（rescue 内部化、轨迹记录器迁移、进度注入、options 字段清理）
  What to do / Must NOT do: 在 `src/cccp/calculation/tasks/optimize.py` 实现 `run_optimize`，**整段重写 options 字段契约（评审修正）**：
  - `OptimizeOptions(mode: OptimizationMode, level: MethodSpec|None, initial_hessian, recalc_hess, trust_radius, max_cycles, constraints: ReactionCoordinatePlan|None, ts: TsSpec|None, rescue: RescueSpec, geom_maxiter: int|None)`。
  - **删除 `trajectory_item_id`**：轨迹/进度身份是平台绑定，由 ACP 包装在 `TaskProgressSink` 回调闭包中携带；任务只用本地产物路径（现 `_finalize_trajectory(target_dir, …, item_id)` 的 item_id 语义保留在 ACP 侧适配器）。
  - **solvent/grid/SCF/basis/dispersion/RI/aux 唯一载体是 `level`（`MethodSpec`）→ `ResolvedCalculationSpec`（todo 7）解析**；`OptimizeOptions` 不得再各留一份，无 `level` 时按"显式值/options/配置默认/方法默认"唯一优先级解析。
  - **删除 `structure_kind` 独立字段**：角色由 `mode`/`TsSpec` 唯一派生为 `StructureRole`（删除现 `_structure_kind` 的 resources 覆盖分支），或由调用方 `StructureRole` 单源传入并与 mode 一致性校验；不保留第二个可独立设置的字段。
  - **`failure_type` 语义定死**：`RescueSpec.failure_type: FailureType | None` 是**可选的调用方恢复输入**（None=任务内部由错误分类推导，即现 `_failure_type` 的 override/derive 两态）；执行结果中的 `failure_type` 是**派生诊断输出**（入 `OptimizePayload`），二者命名区分、不得共用一个可写字段。
  - **删除 `ts_mode: bool|int`**：改为 `TsSpec(enabled: bool=False, mode_index: int|None=None)`（None=跟随最低虚频；显式 int=映射目标；`mode_index` 仅在 `enabled=True` 合法）；rescue 内部策略（现 `_rescue_kwargs` 的 `ts_mode: True`）不再经用户字段承载双关语义。
  把 `optimization_trajectory.py`（stdlib-only）迁入 cccp（`item_id` 由 ACP 注入）；进度经 `TaskProgressSink` 注入（不 import acp）；rescue 内部化（保留现有失败分类与 rescue 矩阵语义）；`OptimizePayload` 类型化（含派生诊断）。ACP 包装保留平台产物/帧注册与身份绑定。
  Must NOT：把 rescue 暴露为跨任务编排；用 `resources.extra` 透传；在 options 保留平台身份（`trajectory_item_id`/`candidate_id`/profile）或第二份 solvent/grid/SCF；让 `ts_mode` 一个字段同时表达开关与索引；让 `structure_kind` 与 `mode`/`StructureRole` 三者并存。
  Parallelization: Wave 3 | Blocked by: 14,17 | Blocks: 19
  References: src/acp/calculations/primitives/optimize.py:146-221,224-259,262-405,405-430,497-531; src/acp/calculations/primitives/optimization_trajectory.py:1-545; src/acp/calculations/progress.py:30-219; src/cccp/calculation/contracts.py（todo 11：`OptimizationMode`/`StructureRole`）; src/cccp/qc/resolved_spec.py（todo 7）; tests/test_orca_failure_classification.py; tests/test_optimization_trajectory.py; tests/test_calculation_executor.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_optimize.py` 绿（含：`ts.mode_index` 无 `enabled` → `TaskInputError`；`level` 单源优先级用例；`rescue.failure_type` 显式/推导两态；`mode`/`StructureRole` 一致性）；`pytest tests/test_orca_failure_classification.py tests/test_optimization_trajectory.py -q` 绿；rescue 元数据字段与 goldens 一致；隔离探针下 optimize 可运行；`grep -n "trajectory_item_id\|structure_kind" src/cccp/calculation/tasks/optimize.py` 无旧平台/重复字段（派生诊断命名 `rescue_failure_type`/`rescue_structure_kind` 与保留 str 形参的 `build_rescue_plan` 除外）；**声明 `_RESCUE_MATRIX` 的 `intermediate/precursor/product` 行在 `StructureRole` 下不可达、作为防御性/遗留行保留**（`build_rescue_plan` 仍接受 str，`tests/test_orca_failure_classification.py` 直测不受影响）；**普通 vs TS 优化能力分派断言（`mode`/`TsSpec` → `optimize`/`transition_state_opt`）；进度事件为科学事件（非 UI 字段）**。**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证。**
  QA scenarios (exact tool + invocation): happy = pytest 绿 + 隔离 optimize 成功；failure = 字段双关/平台身份进入 options/进度 sink 触发 acp import。Evidence .omo/evidence/acp-cccp-remediation/task-18-optimize.txt
  Commit: Y | feat(cccp): implement optimize task with cleaned typed options

- [x] 19. 任务 3：frequency（频率归任务，normal_modes 产物归 ACP）
  What to do / Must NOT do: `src/cccp/calculation/tasks/frequency.py` 返回 `frequencies/n_imaginary/日志工件`，并**把频率/振动向量/IR 强度的科学解析抽为 cccp 可复用能力**（`src/cccp/calculation/frequency_parse.py` 或复用/扩展 ORCA 接口解析）——任务返回科学数据/科学工件；`normal_modes.json` 产品格式、几何绑定与 manifest 注册留 ACP（原 `_try_materialize_normal_modes` 的**解析与发布两部分拆开**）。**移除**未确认后端支持的 `FrequencyOptions.numerical`。Must NOT：cccp 侧写帧/平台产品；Must NOT 让完整结果解释必须回到 ACP（独立任务即可拿到科学数据）。
  Parallelization: Wave 3 | Blocked by: 18 | Blocks: 20
  References: src/acp/calculations/primitives/frequency.py:1-150,31,67-78; src/acp/results/{frequencies.py,orca_parser.py}; src/cccp/qc/interfaces/orca.py:2606-2710; tests/test_acp_frequency_modes.py; tests/test_calculation_executor.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_frequency.py` 绿；`pytest tests/test_acp_frequency_modes.py -q` 绿；normal_modes 几何绑定行为不变；**独立 cccp 调用可拿到 frequencies/振动向量/IR 强度**（无需 ACP 解释）。**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证。**
  QA scenarios (exact tool + invocation): happy = pytest 绿 + 频率列表与 golden 一致；failure = cccp 侧仍 import `acp.results` 或产物写入平台清单。Evidence .omo/evidence/acp-cccp-remediation/task-19-frequency.txt
  Commit: Y | feat(cccp): implement frequency task; keep view products in ACP

- [x] 20. 任务 4：scan（仅 relaxed；多点内部；manifest 上移）
  What to do / Must NOT do: `src/cccp/calculation/tasks/scan.py` 实现 relaxed scan（当前唯一实现），`ScanOptions(coordinate: CoordinateSpec|ReactionCoordinatePlan, mode="relaxed", points)`；`ScanPayload(frames=[ScanFrame(index, values, energy_hartree, geometry_ref, converged)], profile_ref)`；**移除** cccp 侧 `result_manifest.json` 写入，改由 ACP 包装注册 `structures/trajectories` 产物。`rigid`/`optimizer_level`/`single_point_level` 当前不实现也不进 v1 契约（Metis 修正：不留下未定决策）。Must NOT：改变扫描数值语义。
  Parallelization: Wave 3 | Blocked by: 19 | Blocks: 21
  References: src/acp/calculations/primitives/scan.py:1-401,338-401; src/acp/storage/manifest.py; src/cccp/qc/interfaces/{constraints.py,xtb_scan.py,orca.py:1998-2496}; tests/test_scan_workflow.py; tests/test_acp_pes_scan_live.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_scan.py` 绿；`pytest tests/test_scan_workflow.py -q` 绿；扫描帧/能量曲线与 goldens 一致；cccp 侧无 ResultManifest import。**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证。**
  QA scenarios (exact tool + invocation): happy = pytest 绿 + grep cccp 无 `ResultManifest`；failure = 帧语义变化或 manifest 仍由 cccp 写。Evidence .omo/evidence/acp-cccp-remediation/task-20-scan.txt
  Commit: Y | feat(cccp): implement relaxed scan task; lift manifest writes to ACP

- [x] 21. 任务 5：irc（双向内部；签名统一；端点分类工具归 cccp）
  What to do / Must NOT do: `src/cccp/calculation/tasks/irc.py` 实现 `run_irc(TaskRequest)`，`IrcOptions(directions, maxpoints, step, initial_hessian)`（**不含 `ts_mode`**——IRC 基元当前不使用；如后续需要必须显式定义 bool/int 语义），`IrcPayload` 含每方向端点/能量/converged/steps；把纯几何/连通性端点分类（`acp/calculations/irc/validation.py`）归入 cccp；ACP 包装负责端点产物/manifest 注册与展示。Must NOT：IRC 进入 `StepKind`/plan。
  Parallelization: Wave 3 | Blocked by: 20 | Blocks: 22
  References: src/acp/calculations/primitives/irc.py:1-500,63-72,197-198,455-489; src/acp/calculations/irc/{validation.py,contracts.py,source.py}; src/acp/workflows/irc.py:125-150; src/cccp/qc/interfaces/orca_ts.py; tests/test_irc.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_irc.py` 绿；`pytest tests/test_irc.py -q` 绿；双向端点与 goldens 一致；`validate_plan` 仍拒绝 IRC。**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证。**
  QA scenarios (exact tool + invocation): happy = pytest 绿 + plan 校验拒绝 IRC；failure = `run_irc` 仍为旧签名或端点分类留在 acp。Evidence .omo/evidence/acp-cccp-remediation/task-21-irc.txt
  Commit: Y | feat(cccp): implement IRC task with unified request and pure endpoint classification

- [x] 22. 任务 6+7：casscf 与 thermochemistry（类型化载荷、单位/标准态、ExternalBackend 收口）
  What to do / Must NOT do: `src/cccp/calculation/tasks/{casscf,thermochemistry}.py`；`CasscfOptions` 补 `orbital_selection`，`CasscfPayload(root_energies, natural_occupations, nevpt2_energies, active_space)`（显式映射现状 `metadata["multireference"]`）；`ThermochemistryOptions(freq_log_path, sp_energy_hartree, temperature_k, pressure_atm, standard_state, scl_zpe, ilowfreq, imagreal, conc)`，`standard_state` 归一化契约（`1atm|1M`）与单位写入契约。**热化学单一实现且单向**：`run_thermochemistry` 调用 todo 14 的 `cccp/qc/shermo_adapter.py` + `cccp/qc/thermo_normalize.py` 共享实现（**不调用 ExternalBackend、不重入任务入口、不新增第二条 runner 调用路径**）后得到 `ThermochemistryPayload`；ACP 包装注册产物。Must NOT：cccp 侧 import acp；无视单位语义；让任务经 backend 回调任务层。
  Parallelization: Wave 3 | Blocked by: 14,21 | Blocks: 23
  References: src/acp/calculations/primitives/{casscf.py:1-160,thermochemistry.py:1-133,_thermochemistry_input.py,_thermochemistry_support.py}; src/acp/backends/external_backend.py:77-150; src/cccp/qc/{shermo_adapter,thermo_normalize}.py（todo 14）; src/acp/calculations/primitives/optimize.py:527-531; tests/test_acp_electronic_state.py; tests/test_thermochemistry.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_task_{casscf,thermochemistry}.py` 绿；`pytest tests/test_acp_electronic_state.py tests/test_thermochemistry.py -q` 绿；熵/焓/吉布斯单位与标准态校正与 goldens 一致；**单次 Shermo 调用**（spy/mock 断言恰好 1 次）且**无任务入口重入**（调用栈/依赖图断言）；**两入口（旧 backend 与 `run_thermochemistry`）复用同一共享实现、每次计算只启动一次 Shermo**（端到端 spy 计数=1），本任务不新增 runner 调用路径。**本能力恢复 fixture（Wave 0 recovery_fixtures 子集）在切换后即验证。**
  QA scenarios (exact tool + invocation): happy = pytest 绿 + unit 一致性断言；failure = casscf metadata key 未映射或 standard_state 归一化漂移。Evidence .omo/evidence/acp-cccp-remediation/task-22-casscf-thermo.txt
  Commit: Y | feat(cccp): implement CASSCF and thermochemistry tasks with typed payloads

- [x] 23. executor 改线：CalculationPlanExecutor/plans 调 cccp 任务，primitives 变 shim
  What to do / Must NOT do: `src/acp/calculations/executor.py` 的 `_PRIMITIVE_DISPATCH` 改调 `cccp.calculation.run_*`（经 ACP 包装，保留产物注册/checkpoint/handoff）；`src/acp/calculations/plans.py`、batch engine、tsmode engine 的调用点同步；删除 `acp/calculations/primitives/*` 实现体，仅保留转发 shim；**持久化顺序**：按"科学结果持久化 → 发布 → 发布完成标记"接线（todo 16 契约），恢复路径先查科学结果；**本 todo 负责剩余统一接线与收口**（各能力单项恢复已在 17–22 验证）；**完成护栏硬重定向**：`test_unique_primitive_definitions` 单根指向 `src/cccp/calculation/tasks`，`unique_run_scan`/`unique_run_irc` 允许路径切到 `cccp/calculation` 并移除 acp 旧位置允许；`test_architecture_invariants` 的唯一核心断言指 cccp。Must NOT：保留第二套执行；Must NOT 破坏 checkpoint/handoff/产品注册（A8）；Must NOT 在实现搬移前做单根硬切换。
  Parallelization: Wave 3 | Blocked by: 17–22 | Blocks: 24,25,40
  References: src/acp/calculations/executor.py:32-53,143-150,340-876; src/acp/calculations/batch/engine.py:62-68,933-1156; src/acp/calculations/tsmode/engine.py:37-69; src/acp/workflows/simple.py:373-540; tests/test_calculation_executor.py; tests/test_checkpoint_continue.py; tests/test_architecture_invariants.py
  Acceptance criteria (agent-executable): `pytest -m "not slow" -q` 全绿；`grep -rn "def run_singlepoint\|def run_optimize" src/acp` 仅剩 shim（无实现体）；`_PRIMITIVES_DIR` 单根指向 `src/cccp/calculation/tasks`；冻结门无 acp 允许；executor 走 cccp 任务（探针断言调用栈）。
  QA scenarios (exact tool + invocation): happy = 全量快速套件绿 + checkpoint resume 测试绿；failure = 第二套执行残留或 A8 契约回归失败。Evidence .omo/evidence/acp-cccp-remediation/task-23-executor-rewire.txt
  Commit: Y | refactor(acp): route plan executor through cccp calculation tasks

- [x] 24. P2 任务契约 + 能力映射（先契约后实现）
  What to do / Must NOT do: 在 `src/cccp/calculation/requests.py`+`results.py` 加入**七个** P2 任务并注册进 `TaskKind`：`ConformerSearchOptions/Result`（CREST；ensemble 工件/构象数/能量表）、`MdSamplingOptions/Result`（Molclus/xTB-MD；轨迹/帧数）、`ClusteringOptions/Result`（ISOSTAT/Molclus；簇分配/代表结构）、`CensoRefineOptions/Result`（preset/级别覆盖；能量/自由能/权重表 + refined ensemble，模板文本由翻译层生成）、`NmrShieldingOptions/Result`（GIAO；保留 atom→`{symbol,isotropic}`）、`XtbPathSearchOptions/Result`（GFN2-xTB PATH metadynamics；旧 `pes2ts_xtb_path_request_v1` 由 ACP 适配器按本 todo 转换表映射后传入；轨迹/帧/端点）、`OrcaGradientOptions/Result`（ORCA EnGrad；旧 `pes2ts_orca_gradient_request_v1` 由 ACP 适配器按本 todo 转换表映射后传入；原子梯度/能量）。**为每项明确**（评审修正）：输入形态（单结构/ensemble/轨迹/日志）、backend 与能力名称映射、成功/部分失败/空结果语义、工件与记录身份映射；**扩展能力矩阵与验收矩阵（仅 P2）**：补 `md_sampling`/`censo_refine`/`conformer_search`/`clustering`/`nmr_shielding`/`xtb_path_search`/`orca_gradient` 能力名与状态，并落地 **GIAO/EnGrad/CENSO 组合的验收矩阵行（r13 从 todo 13 移入）**；**七核心任务（含 `casscf`）的映射已在 todo 13 完成，本 todo 不重复**；补 P2 优先序（CensoBackend/CREST/Molclus 歧义）与选择记录用例。**新工作流旧请求转换表（评审修正）**：为 XtbPathSearch/OrcaGradient 定义 `pes2ts_*_request_v1` → CCCP schema 的**字段级转换**（依据 `src/acp/workflows/xtb_path.py:100` `XtbPathRequest`、`src/acp/workflows/orca_gradient.py:76` `OrcaGradientRequest`）：平台身份（`reaction_id`/`plan_sha256`/`request_sha256`/`config_digest`/`adapter_version`/`schema_version`）留 ACP 绑定信息；科学字段（`start_xyz_text`/`end_xyz_text` 或 `coordinates`/`symbols`、`charge`/`multiplicity`、`method`/`basis`、`gfn_level`/`uhf`、`seed`、`scf_convergence`、`threads`/`nproc`、`timeout_seconds`）→ CCCP 标准字段；**原始后端片段**（`path_inp_text`/`extra_args`/`route_extras`/`extra_blocks`/`output_name`）→ **范围受限、显式标记的后端输入类型**（记录内容摘要/冲突规则/来源，由翻译层规则约束，**不得换名无约束透传**）。旧 CLI schema 留 ACP，由 ACP 转换；**CCCP 任务 schema 不依赖 `pes2ts_*` 平台协议名**。Must NOT：先接线后定契约；Must NOT 让 CENSO 模板文本由工作流拼装；Must NOT 借 P2 扩展重开七核心能力映射。
  Parallelization: Wave 4 | Blocked by: 23 | Blocks: 42,43
  References: src/acp/backends/{crest.py,censo_backend.py,isostat_backend.py,molclus_backend.py,orca.py:155-176,capabilities.py:63-162}; src/acp/confsearch/**; src/acp/workflows/{nmr.py,xtb_path.py,orca_gradient.py}; src/acp/calculations/primitives/thermochemistry.py; src/cccp/qc/interfaces/{crest.py,censo.py,isostat.py,molclus.py,xtb_path.py}
  Acceptance criteria (agent-executable): `tests/test_cccp_calculation_contracts.py` 覆盖**七任务** options/result 校验与序列化 + 输入/backend/语义映射表；`tests/test_cccp_calculation_selection.py` 覆盖 P2 优先序/选择记录与 **P2 验收矩阵（GIAO/EnGrad/CENSO）**；能力矩阵含新增能力名（七核心映射来自 todo 13）；`python3.11 -c "from cccp.calculation import TaskKind; assert {'conformer_search','md_sampling','clustering','censo_refine','nmr_shielding','xtb_path_search','orca_gradient'} <= set(TaskKind)"`；**同旧请求转换前后的实际输入一致；平台身份不进入 CCCP 请求；原始片段与结构化字段冲突有确定行为；缓存签名包含这些实际影响计算的片段**。
  QA scenarios (exact tool + invocation): happy = 契约单测绿 + 映射表完整；failure = 任一 P2 任务无 result 载荷、能力名缺失、或 nmr shielding 键形状变化。Evidence .omo/evidence/acp-cccp-remediation/task-24-p2-contracts.txt
  Commit: Y | feat(cccp): define P2 capability task contracts and capability mapping before wiring

- [x] 25. 翻译层收口：路由行/CENSO 模板/默认唯一来源 + 人读摘要同源
  What to do / Must NOT do: **先交付最小渲染 API（供 42/43/26/28 使用）**：在 cccp 侧提供结构化级别→ORCA route/block 的渲染入口（复用 `cccp/qc/interfaces/route_render.py` 与 keyword_registry），CENSO 模板行由翻译层生成；**再消费该 API 收口上游构建点**（42/43 依赖整个 todo 25 完成——本轮采用简单完整依赖，减少子任务完成状态歧义）：`src/acp/workflows/energy_shared.py:258-260`、`src/acp/confsearch/shared/helpers.py:182-183`、`src/acp/calculations/batch/effective_config.py::build_orca_summary` 全部改为消费同一规范化结果（显示摘要=执行参数）；将 `_build_input_blocks/_write_input/_write_nmr_input` 的输入构建与文件写入/进程调用职责边界文档化并抽出可复用渲染函数（F7）。Must NOT：工作流内出现 `"! " + 拼接`；Must NOT 复制默认表。
  Parallelization: Wave 4 | Blocked by: 23 | Blocks: 26,28,42,43
  References: src/acp/workflows/energy_shared.py:129-321; src/acp/confsearch/shared/helpers.py:127-183; src/acp/calculations/batch/effective_config.py; src/cccp/qc/interfaces/{route_render.py,orca.py:1212-1648,3353-3456,orca_ts.py:315-524}; src/cccp/qc/keyword_registry.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_translation_single_source.py` 绿；`grep -rn '"! "\s*+' src/acp/workflows src/acp/confsearch` 无命中；摘要与实际 route 参数一致性断言通过。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + grep 空；failure = 任一默认值仍由 ACP catalog 决定（与方法元数据不一致）。Evidence .omo/evidence/acp-cccp-remediation/task-25-translation.txt
  Commit: Y | refactor(cccp): single-source route/CENSO translation and summary derivation

- [x] 26. Confsearch 四协议与 Shermo handoff 改接统一任务
  What to do / Must NOT do: `ensemble.py`/`energy.py`/`xtbmd_censo_energy.py`/`xtbmd_md.py` 与 `confsearch/protocols/*` 改为调用 `cccp.calculation` 的 conformer_search/md_sampling/clustering/censo_refine（todos 42/43 实现）与 run_optimize/run_frequency/run_singlepoint/run_thermochemistry；删除 `get_backend(...)`、`run_shermo(...)`、`CensoBackend(cfg)` 直构与调用；保留构象筛选/精修策略与产物/清单语义；批量 SP 经通用批量执行器（执行函数=run_singlepoint）。Must NOT：改变四协议的科学结果；Must NOT 让 `xtb-crest`/`xtb-md` 误入 DFT handoff。
  Parallelization: Wave 4 | Blocked by: 24,25,42,43 | Blocks: 29
  References: src/acp/confsearch/{engine.py,protocols/*.py}; src/acp/workflows/{ensemble.py:303-356,energy.py:276-690,energy_shared.py:345-525,xtbmd_censo_energy.py:453,703,1574,1743,xtbmd_md.py:127}; tests/test_acp_workflows_energy.py; tests/test_acp_confsearch.py; tests/test_acp_workflows_xtbmd_censo_energy.py
  Acceptance criteria (agent-executable): `grep -rn "run_shermo\|get_backend(\|CensoBackend(" src/acp/workflows src/acp/confsearch` 仅剩类型/解析导入（白名单为空或仅数据契约）；`pytest tests/test_acp_workflows_energy.py tests/test_acp_confsearch.py tests/test_acp_workflows_xtbmd_censo_energy.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + grep 执行性调用清零；failure = 某协议仍直构 backend 或 Shermo 直调。Evidence .omo/evidence/acp-cccp-remediation/task-26-confsearch.txt
  Commit: Y | refactor(confsearch): route all QC through cccp calculation tasks

- [x] 27. NMR 改接统一任务（censo-light conformer + GIAO shielding）
  What to do / Must NOT do: `src/acp/workflows/nmr.py:325-505` **保留 ACP 的 CREST→CENSO 编排**（评审修正）：构象生成分别调用 conformer_search 与 censo_refine 单项任务（默认 censo-light 仍运行 CENSO、censo-zero 仍按原规则跳过 CENSO），能窗筛选/自由能选择/Boltzmann 权重仍由 ACP 处理；GIAO 改调 `run_nmr_shielding` 任务；DP4/DP5、平均、缩放、FCHL、报告逻辑不动；删除 `get_backend("orca")` + `nmr_shielding` 直调。Must NOT：把 CREST→CENSO 组合流程整体塞进 cccp；Must NOT 改变化学位移/DP4/DP5 科学语义。
  Parallelization: Wave 4 | Blocked by: 24,25,42,43 | Blocks: 29
  References: src/acp/workflows/nmr.py:29,325-505,774-1027; src/acp/workflows/ensemble.py:300; src/acp/nmr/**; src/acp/backends/orca.py:155-176; src/cccp/qc/interfaces/orca.py:2907-2993; tests/test_acp_workflows_nmr.py; tests/test_acp_nmr_*.py
  Acceptance criteria (agent-executable): `grep -n "get_backend\|nmr_shielding" src/acp/workflows/nmr.py` 无执行性直调；`pytest tests/test_acp_workflows_nmr.py -q` 绿；shielding 键 `atom→{symbol,isotropic}` 不变；**默认 censo-light 仍运行 CENSO、censo-zero 仍跳过、构象数/能量来源/权重语义不变**（针对性断言）。
  QA scenarios (exact tool + invocation): happy = pytest 绿；failure = nmr 仍直调 backend 或 shielding 形状变化。Evidence .omo/evidence/acp-cccp-remediation/task-27-nmr.txt
  Commit: Y | refactor(nmr): use cccp calculation tasks for conformer search and GIAO

- [x] 28. PES/路径类工作流语义对齐（含 XtbPathSearch/OrcaGradient 改线；先差异对照，再接线，保留缓存/并发）
  What to do / Must NOT do: 先写 `docs/ACP_PES_Task_Alignment.md` 记录 PES `_run_relaxed_scan_backend`/`_run_single_points` 与通用任务在参数/错误/恢复/产物/缓存键/并发上的差异；再把 `calculations/pes/scan.py` 改接 cccp relaxed-scan 任务与**通用批量执行器**（执行函数=`run_singlepoint`，保留 batch 缓存与并发回调）；PES 路径分析/候选选择逻辑不动。**新工作流改线**：`src/acp/workflows/xtb_path.py`（:24,656 `get_backend("xtb")`）与 `src/acp/workflows/orca_gradient.py`（:509,511 `get_backend("orca")`）改为调用 todo 24/42/43 的 `xtb_path_search`/`orca_gradient` 任务（保留 `--path-config`/`--gradient-config` 请求载荷与产物语义），删除 `get_backend` 直构。Must NOT：仅替换 import 就假定等价；Must NOT 丢失缓存/并发/进度；Must NOT 让批量路径绕过单任务校验/错误分类/归一化；Must NOT 让新工作流保留第二条 backend-direct 路径。
  Parallelization: Wave 4 | Blocked by: 24,25,42,43 | Blocks: 29
  References: src/acp/calculations/pes/scan.py:48-96,250-703,945-1108; src/acp/calculations/batch/{singlepoint.py:12-171,_singlepoint_execution.py:15,311-442}; src/cccp/calculation/batch.py（todo 12）; src/acp/workflows/{xtb_path.py,orca_gradient.py}; tests/test_pes_search.py; tests/test_acp_pes_scan_live.py; tests/test_acp_pes_dft_scan.py; tests/test_acp_workflows_xtb_path.py; tests/test_acp_workflows_orca_gradient.py
  Acceptance criteria (agent-executable): 差异文档存在且逐条给出等价/取舍结论；`pytest tests/test_pes_search.py tests/test_acp_pes_scan_live.py -q` 绿；PES 不再直构 backend（`grep -n "get_backend" src/acp/calculations/pes` 空）；**同一 SP 请求经普通/批量/PES 三路径的校验、有效参数、错误分类与结果归一化一致**；新工作流不再直构 backend（`grep -n "get_backend" src/acp/workflows/xtb_path.py src/acp/workflows/orca_gradient.py` 空）；`pytest tests/test_acp_workflows_xtb_path.py tests/test_acp_workflows_orca_gradient.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + 缓存命中/并发回调测试绿；failure = 扫描数值或缓存键变化导致回归失败。Evidence .omo/evidence/acp-cccp-remediation/task-28-pes.txt
  Commit: Y | refactor(pes): align scan/SP with shared cccp task core

- [x] 29. 依赖守护零豁免（移除 allowlist 中的已修复站点）
  What to do / Must NOT do: 移除 todo 4 `--suite architecture-remediation` allowlist 中已修复的 cccp→acp、工作流执行性调用、路由拼接、backend→任务条目（预期为空）；保留并验证"只减不增"断言；更新 `FINAL_SHERMO_ALLOWED_PATHS` 为仅 cccp。Must NOT：以扩大 allowlist 换绿。
  Parallelization: Wave 4 | Blocked by: 15,16,26,27,28 | Blocks: 30–41
  References: scripts/check_grep_gates.py; tests/test_grep_gates_script.py; src/acp/workflows/energy_shared.py
  Acceptance criteria (agent-executable): `python3.11 scripts/check_grep_gates.py --suite architecture-remediation` 0 退出且无 allowlist 命中；`grep -rn "from acp\|import acp" src/cccp` 空；`final_shermo` 注释行豁免已生效（`src/acp/scheduler/capabilities.py` 无命中）；`pytest tests/test_grep_gates_script.py tests/test_cccp_isolation.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = 全门绿且 allowlist 空；failure = 任一已知违例仍在 allowlist。Evidence .omo/evidence/acp-cccp-remediation/task-29-gates-zero.txt
  Commit: Y | test(guards): enforce zero-exemption dependency gates

- [x] 30. 遗留 cccp API 弃用标注（记录证据，不删除）
  What to do / Must NOT do: 对 `cccp/core/protocols.py`、`cccp/core/state_manager.py`、`cccp/core/candidates.py`、`cccp/pipeline/**`、`cccp/qc/cluster/**` 增加弃用说明（docstring + 新架构文档章节），记录"本仓库无 ACP 生产调用/仅 re-export/仅 legacy bridge（`acp/core/models.py`，调用点仅测试）"的使用证据；给出支持/弃用范围。Must NOT：删除任何导出（外部用户不可确认）；Must NOT 恢复退役 CLI。
  Parallelization: Wave 5 | Blocked by: 29 | Blocks: 35–41
  References: src/cccp/core/{protocols.py,state_manager.py,candidates.py}; src/cccp/pipeline/__init__.py,executor.py; src/cccp/qc/cluster/__init__.py; src/acp/core/models.py:22,144-347; docs/ACP_CCCP_Architecture_Report.md:107-115,302-308
  Acceptance criteria (agent-executable): 弃用说明存在；`python3.11 -c "import cccp.core, cccp.pipeline"` 仍成功；`pytest tests/test_protocol_levels.py tests/test_conformer_search.py tests/test_acp_core_models.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = import 与旧测试绿；failure = 删除导出导致旧测试/外部兼容失败。Evidence .omo/evidence/acp-cccp-remediation/task-30-deprecation.txt
  Commit: Y | docs(cccp): document legacy API support/deprecation scope

- [x] 31. Shermo 入口与实现数量说明（单一实现、两个消费入口）
  What to do / Must NOT do: 文档化 `cccp/qc/runners/__init__.py::run_shermo` 为 QC 接口/runner 职责（目录统一非最高优先级）；明确**入口数量 ≠ 实现数量**：兼容 backend 入口（ExternalBackend→共享适配）与任务入口（`run_thermochemistry`→共享适配）都走唯一科学实现 `cccp/qc/shermo_adapter.py` + `thermo_normalize.py`；`run_shermo` 可被共享适配与 `batch_process_thermo` 等低层兼容包装调用（同一 runner，不计为重复实现）；`energy_shared` 直调已移除；`FINAL_SHERMO_ALLOWED_PATHS` 仅 `src/cccp/**`。Must NOT：把"唯一生产消费=任务"写成删除兼容 backend 调用的理由（评审修正）；迁移目录引入兼容风险而不留旧导入。
  Parallelization: Wave 5 | Blocked by: 29 | Blocks: —
  References: src/cccp/qc/runners/__init__.py:79-265; src/cccp/qc/{shermo_adapter,thermo_normalize}.py（todo 14）; src/acp/backends/external_backend.py; scripts/check_grep_gates.py:106-110; docs/ACP_CCCP_Architecture_Report.md:130-133,306
  Acceptance criteria (agent-executable): `grep -rn "run_shermo" src/acp` 无生产直呼（导入/类型除外）；`grep -rn "run_shermo" src/cccp` 仅 runner 自身（含 `batch_process_thermo` 低层包装）与共享适配；**两入口复用同一实现有测试**（todo 14/22）；`pytest tests/test_cccp_shermo_path_limit.py tests/test_thermochemistry.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = grep 无直呼 + 单实现断言 + pytest 绿；failure = 出现第二套**科学实现**（非共享归一化）或旧 backend 直呼 runner。Evidence .omo/evidence/acp-cccp-remediation/task-31-shermo.txt
  Commit: Y | docs(cccp): document Shermo single implementation across entries

- [x] 32. 同步 AGENTS/README/根规则与审计工件
  What to do / Must NOT do: 改写根 `AGENTS.md` 规则 #17（"calculations 是唯一驻点"→新四层与新驻点）、`WHERE TO LOOK` 表；修正 `src/cccp/AGENTS.md`（版本 0.1.3、utils 文件数、stale 条目）、`src/acp/AGENTS.md`、`src/acp/backends/AGENTS.md`（迁移后定位）、`src/acp/workflows/AGENTS.md`（active 列表/backend 约定，含 XtbPathSearch/OrcaGradient）；更新 `README.md` 包结构/设计原则；更新 `tests/AGENTS.md`（计数/新测试）、`docs/ACP_Function_Test_Manual.md`（12→14 工作流与新增用例；如维护期已更新则复核一致性）与 `tests/baseline/refactor-evidence/capability-evidence.md`；**确认 Wave 0 迁移期规则已收口为最终规则**（双驻点台账关闭、唯一驻点生效）。Must NOT：改版本号（除文档对齐 0.1.3）；Must NOT 留下"acp/backends 无 subprocess"等与新位置的矛盾描述。
  Parallelization: Wave 5 | Blocked by: 29 | Blocks: 35–41
  References: AGENTS.md（根）; src/acp/AGENTS.md; src/cccp/AGENTS.md; src/cccp/core/AGENTS.md; src/acp/backends/AGENTS.md; src/acp/workflows/AGENTS.md; tests/AGENTS.md; README.md; docs/ACP_Function_Test_Manual.md; tests/baseline/refactor-evidence/capability-evidence.md; tests/test_architecture_invariants.py:277
  Acceptance criteria (agent-executable): `pytest tests/test_architecture_invariants.py::test_capability_evidence_table tests/test_frontend_sync.py -q` 绿；文档中不再出现旧规则/旧版本文本（grep 断言）。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + grep 旧描述空；failure = capability-evidence 与 collect-only 不一致导致测试失败。Evidence .omo/evidence/acp-cccp-remediation/task-32-docs.txt
  Commit: Y | docs: sync AGENTS/README/rules with new four-layer architecture

- [x] 33. 新增架构开发文档（cccp.calculation API 与迁移阶段）
  What to do / Must NOT do: **在 Todo 11 初稿基础上定稿** `docs/ACP_CCCP_Task_API_DevDoc.md`：四层职责图、`cccp.calculation` 统一封套/typed options/类型化结果/选择/错误/序列化契约、翻译层边界、能力声明三分、依赖方向、迁移阶段（P0–P3）与 A1–A9 验收矩阵、与 ACP `CalculationPlan`/executor 的关系；保持**唯一现行规范**（不叠加历史草案）。Must NOT：含与代码不符的示例。
  Parallelization: Wave 5 | Blocked by: 29 | Blocks: 35–41
  References: .omo/drafts/acp-cccp-architecture-remediation.md（Task API proposal v3，最新整合契约）; docs/ACP_CCCP_Architecture_Report.md:246-328; src/cccp/calculation/**
  Acceptance criteria (agent-executable): 文档存在；示例代码可复制运行（`python3.11 -c` 或 doctest 风格验证至少 1 个 standalone 调用示例）；无未定决策遗留。
  QA scenarios (exact tool + invocation): happy = 文档示例在无 acp 环境导入成功；failure = 示例引用不存在的 API。Evidence .omo/evidence/acp-cccp-remediation/task-33-devdoc.txt
  Commit: Y | docs(cccp): add task API and migration devdoc

- [x] 34. 取代旧重构方案标注与最终基线对账
  What to do / Must NOT do: 在 `docs/ACP_Calculation_Workflow_Refactor_Cleanup_Plan.md` 顶部标注被本整改取代/修订的部分（尤其"计算基元驻留 acp/calculations"）；执行最终基线对账：`pytest -m "not slow"` 用例数、护栏状态、test_f4_scope_audit 基线。Must NOT：删除旧文档；Must NOT 放宽护栏。
  Parallelization: Wave 5 | Blocked by: 29 | Blocks: 35–41
  References: docs/ACP_Calculation_Workflow_Refactor_Cleanup_Plan.md:1-13,229-281; tests/test_f4_scope_audit.py; tests/test_architecture_invariants.py
  Acceptance criteria (agent-executable): 标注存在；`pytest -m "not slow" -q` 绿且用例数≥基线（**覆盖映射核对：原有测试均有对应或记录删改原因；数量仅次级信号**）；`pytest tests/test_f4_scope_audit.py -q` 绿。
  QA scenarios (exact tool + invocation): happy = 对账通过；failure = 用例数下降（静默删测）或护栏弱化。Evidence .omo/evidence/acp-cccp-remediation/task-34-reconcile.txt
  Commit: Y | docs: supersede prior refactor plan and reconcile baselines

- [x] 35. A1/A2：隔离独立性验收（cccp 三层在无 ACP 环境可导入可执行）
  What to do / Must NOT do: 在无 ACP 源码/安装的隔离（子进程 `meta_path` 阻断 + 无 acp 路径）环境中导入并调用 `cccp.calculation` **全部已公开任务**（七核心 + P2 七项：conformer_search/md_sampling/clustering/censo_refine/nmr_shielding/xtb_path_search/orca_gradient），含请求校验/能力选择/执行/结果归一化；验证静态导入检查覆盖函数内导入；验收范围不得残留本计划 xfail。Must NOT：以 import 成功代替执行语义；Must NOT 依赖注入 ACP 私有对象。
  Parallelization: Wave 6 | Blocked by: 4,10,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:310-317（A1/A2）; tests/test_cccp_isolation.py; src/cccp/calculation/selection.py; src/cccp/backends/**
  Acceptance criteria (agent-executable): `pytest tests/test_cccp_isolation.py -q` 全绿且无本计划遗留 `xfail`；`pytest --runxfail tests/test_cccp_isolation.py -q` 通过；`python3.11 scripts/verify_cccp_isolation.py --no-acp`（新增验收脚本）0 退出并打印每任务（七核心 + P2 七项）结果；证据含命令与输出。
  QA scenarios (exact tool + invocation): happy = 隔离脚本**全部十四项任务**（七核心 + P2 七项）全部返回结构化结果；failure = 任一任务需要 ACP 或抛 `ModuleNotFoundError`。Evidence .omo/evidence/acp-cccp-remediation/task-35-a1a2.json
  Commit: Y | test(acceptance): prove cccp task layers run without ACP (A1/A2)

- [x] 36. A3/A4：14 工作流调用矩阵与单一执行核心
  What to do / Must NOT do: 产出 `docs/ACP_CCCP_Call_Matrix.md`（**14 工作流** × 协议/合法分支 → 唯一任务入口）与 `tests/test_acp_workflow_task_matrix.py`（AST/运行期断言：入口只经 `cccp.calculation.run_*`；无执行性直调 backend/runner；无第二套优化/SP/频率/热化学实现）。覆盖 **14 工作流**：Confsearch（4 协议与合法精修分支）、PESsearch、BatchOptimize（4 profile）、irc、scan、tsmode、casscf、nmr、singlepoint、optimize、frequency、xtb_optimize、**XtbPathSearch**、**OrcaGradient**。Must NOT：只检查默认分支。
  Parallelization: Wave 6 | Blocked by: 29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:318-319（A3/A4）; src/acp/catalog.py::WORKFLOW_CATALOG; src/acp/workflows/registry.py; src/acp/confsearch/**; src/acp/workflows/{simple.py,batch_optimize.py,irc.py,tsmode.py,pes_search.py,nmr.py}
  Acceptance criteria (agent-executable): 矩阵文档覆盖全部 14 工作流与分支；`pytest tests/test_acp_workflow_task_matrix.py -q` 绿；`grep -rn "def run_singlepoint\|def run_optimize\|def run_frequency" src/acp` 无实现体。
  QA scenarios (exact tool + invocation): happy = 矩阵测试绿 + 文档分支齐全；failure = 某工作流仍直调 backend 或存在第二套实现。Evidence .omo/evidence/acp-cccp-remediation/task-36-a3a4.md
  Commit: Y | test(acceptance): workflow-to-task call matrix and single execution core (A3/A4)

- [x] 37. A5：翻译单一来源与显示摘要一致
  What to do / Must NOT do: 校验 ORCA route/block、CENSO template、xTB control/argv 的所有构建点唯一来源；关键默认值与归一化单一来源；`build_orca_summary` 显示摘要与实际执行参数一致。Must NOT：工作流拼后端输入；Must NOT 复制默认表。
  Parallelization: Wave 6 | Blocked by: 25,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:320（A5）; src/cccp/qc/interfaces/{route_render.py,orca.py,orca_ts.py}; src/acp/calculations/batch/effective_config.py; tests/test_cccp_translation_single_source.py
  Acceptance criteria (agent-executable): `pytest tests/test_cccp_translation_single_source.py -q` 绿；`grep -rn '"! "\s*+' src/acp` 空；摘要一致性断言通过。
  QA scenarios (exact tool + invocation): happy = pytest 绿；failure = 摘要与执行参数不一致或存在第二个默认来源。Evidence .omo/evidence/acp-cccp-remediation/task-37-a5.txt
  Commit: Y | test(acceptance): translation single-source and summary consistency (A5)

- [x] 38. A6：能力声明/选择/实现一致性矩阵
  What to do / Must NOT do: 对每个 backend×task 组合验证实际方法；stub 不可 AVAILABLE/不可被选择；不支持能力、缺二进制、计算失败三态分别有明确结果。Must NOT：以结构 Protocol 匹配代替声明。
  Parallelization: Wave 6 | Blocked by: 8,13,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:321（A6）; src/cccp/backends/{capabilities.py,registry.py,crest.py,xtb.py,orca.py}; src/cccp/calculation/selection.py
  Acceptance criteria (agent-executable): 新增 `tests/test_cccp_capability_matrix_acceptance.py` 绿；报告探针（`require_backend` 三能力 + `supports`）输出与声明一致；三态错误分类断言通过。
  QA scenarios (exact tool + invocation): happy = pytest 绿 + 探针输出；failure = stub 被选中或三态不可区分。Evidence .omo/evidence/acp-cccp-remediation/task-38-a6.txt
  Commit: Y | test(acceptance): capability declaration/selection/implementation matrix (A6)

- [x] 39. A7：三组等价（历史↔迁移后 ACP↔独立 cccp）
  What to do / Must NOT do: 用 `tests/test_cccp_calculation_equivalence.py` 做三组对照：① 历史集成路径 → 迁移后 ACP（平台兼容）；② 历史集成路径 → 独立 cccp（科学语义保留）；③ 迁移后 ACP → 独立 cccp（适配器不新增默认值/不丢字段）。覆盖 Wave 0 goldens 的全部能力/分支（optimize rescue、scan 多坐标/部分失败、IRC 单向失败、CASSCF/NEVPT2、Shermo 标准态、CENSO 记录身份/自由能、NMR atom 索引/shielding）与默认值/显式覆盖/恢复/失败路径；对照**预期行为变化表**：P0 修复导致的启动前拒绝/错误分类变化属已批准差异。Must NOT：仅对照迁移后同一条代码路径（同义循环）；Must NOT 要求旧错误文本一字不变。
  Parallelization: Wave 6 | Blocked by: 2,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:322（A7）; tests/baseline/cccp_calculation_goldens/; tests/test_cccp_calculation_equivalence.py; tests/test_cccp_real_qc_smoke.py; 预期行为变化表（todo 2）
  Acceptance criteria (agent-executable): 三组等价全绿；goldens 非空且带源 commit/哈希；所有差异均落在预期行为变化表内；失败路径（SCF/超时/解析失败）错误语义一致。
  QA scenarios (exact tool + invocation): happy = `pytest tests/test_cccp_calculation_equivalence.py -q` 绿；failure = 任一默认值/单位/错误语义漂移且不在行为变化表内。Evidence .omo/evidence/acp-cccp-remediation/task-39-a7.json
  Commit: Y | test(acceptance): three-way semantic equivalence (legacy↔ACP↔standalone cccp) (A7)

- [x] 40. A8：ACP 平台契约完整回归
  What to do / Must NOT do: 运行并记录：任务目录布局、manifest、帧投影、checkpoint/resume、远程读写、暂停/继续/取消、历史任务只读兼容；**跨版本恢复**：用 Wave 0 recovery fixtures 验证迁移前文件由迁移后代码读取——`continue` 不重算已完成步骤、不丢工件、不复用不兼容缓存；定义 SP 缓存版本与旧缓存复用条件；`plan fingerprint`（`str(step.spec)`）兼容性测试或显式版本化。定向套件 `test_remote_phase1.py`、`test_acp_api_v1.py`、`test_calculation_executor.py`、`test_checkpoint_continue.py`、frames/structure-viewer 测试、scheduler pause/continue/rerun/purge 测试。Must NOT：通过删除能力绕过失败。
  Parallelization: Wave 6 | Blocked by: 23,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:323（A8）; src/acp/scheduler/**; src/acp/storage/manifest.py; src/acp/results/frames.py; src/acp/calculations/executor.py:214; tests/baseline/recovery_fixtures/（todo 1）; src/cccp/calculation/batch.py（todo 12）
  Acceptance criteria (agent-executable): `pytest -m "not slow" -q` 全绿；远程定向套件 `pytest tests/test_remote_phase1.py -q`（mock）绿；跨版本恢复用例（完成+未完成 checkpoint、部分失败、历史 manifest、批量缓存、远程路径引用）全绿；缓存版本/复用条件有测试；证据记录命令与结果。
  QA scenarios (exact tool + invocation): happy = 全量快速套件 + 远程 mock 套件绿；failure = 任一平台契约回归失败。Evidence .omo/evidence/acp-cccp-remediation/task-40-a8.txt
  Commit: Y | test(acceptance): full ACP platform contract regression (A8)

- [x] 41. A9：真实 QC 小样与远程生命周期（PASS/FAIL/NOT_VERIFIED 三态）
  What to do / Must NOT do: 运行 `pytest tests/test_cccp_real_qc_smoke.py --run-integration`（ORCA sp/opt/freq、xTB opt、CREST search、ISOSTAT/CENSO/Shermo 小样；**scan/IRC/CASSCF/Molclus MD 以及新增工作流 XtbPathSearch（xTB PATH）/OrcaGradient（ORCA EnGrad）明确采用真实小样或固定真实输出 fixture**）；断言不只看退出码：能量/坐标/频率有效性、工件存在且对应正确结构、稳定样例数值容差、原子顺序与记录身份保持、软件版本/输入/日志记录。按 `~/.cccp.yaml` LSF 节点验证提交/轮询/拉取/暂停/恢复/取消。状态三分：**PASS（真实运行且结果验证通过）/ FAIL / NOT_VERIFIED（环境不可用或未执行）**；**必需项存在 FAIL 即整体不通过**；报告分列"代码与离线验证状态"与"真实环境验证状态"；存在必需项 NOT_VERIFIED 时不得给出 A1–A9 全通过结论；**todo 3/5 引入的 xfail 必须在验收范围内清零**，最终以 `--runxfail` 复核。Must NOT：用 mock 冒充真实计算；Must NOT 在无证据时声称通过；Must NOT 以 xfail 掩盖未实现断言。
  Parallelization: Wave 6 | Blocked by: 5,29 | Blocks: F1–F4
  References: docs/ACP_CCCP_Architecture_Report.md:324-326（A9）; tests/test_cccp_real_qc_smoke.py; tests/conftest.py:281-299; /home/xieningke/.cccp.yaml; src/acp/scheduler/remote/**
  Acceptance criteria (agent-executable): 三态清单完整（每项 PASS/FAIL/NOT_VERIFIED + 原因 + `evidence_type` live/recorded_output/mock，**『真实执行验证』PASS 必须 `evidence_type=live`；`NOT_VERIFIED` 原因为环境不可用或该能力允许 recorded output 而真实执行未做；`mock` 不得用于 PASS**）；并**分列『解析与适配验证』（recorded_output 回放通过可 PASS）与『真实执行验证』（通常 NOT_VERIFIED）两轴**；**必需项有 FAIL 时整体不通过**；PASS 项证据含软件版本/输入/结果/断言；`NOT_VERIFIED` 项标注其原因（环境不可用或允许 recorded output 而未做真实执行）；无 mock 冒充；覆盖范围明确（真实小样或固定真实输出 fixture）；**A9 摘要措辞为『本轮约定验收范围通过』并列出未执行 live 的能力（不得表述为所有能力均已真实运行）**；`grep -rn "xfail" tests/test_cccp_isolation.py tests/test_cccp_real_qc_smoke.py` 无本计划遗留；环境可用时 `pytest --runxfail tests/test_cccp_real_qc_smoke.py --run-integration -q` 复核通过。
  QA scenarios (exact tool + invocation): happy = 各小样按三态记录且断言齐全；failure = 环境不可用却未标注、断言缺失或用 mock 结果冒充真实。Evidence .omo/evidence/acp-cccp-remediation/task-41-a9.json
  Commit: Y | test(acceptance): real QC samples and remote LSF lifecycle with three-state reporting (A9)

- [x] 42. P2 执行实现 A：conformer_search / md_sampling / clustering / xtb_path_search
  What to do / Must NOT do: 在 `src/cccp/calculation/tasks/{conformer_search,md_sampling,clustering,xtb_path_search}.py` 分别实现执行核心：conformer_search（CREST；ensemble 工件/构象数/能量表）、md_sampling（Molclus/xTB-MD；轨迹/帧数）、clustering（ISOSTAT/Molclus；簇分配/代表结构）、xtb_path_search（GFN2-xTB PATH metadynamics；消费 CCCP 类型化任务请求，旧 `pes2ts_xtb_path_request_v1` 由 ACP 适配器按 todo 24 映射后传入；轨迹/帧/端点）；各自完成"输入形态→能力选择→翻译→接口执行→类型化结果"；成功/部分失败/空结果语义与工件/记录身份按 todo 24 映射表落地；独立调用测试（无 ACP）。Must NOT：把 CREST→CENSO 组合、能窗筛选或协议筛选策略塞进 cccp。
  Parallelization: Wave 4 | Blocked by: 24,25 | Blocks: 26,27,28
  References: src/acp/backends/{crest.py,censo_backend.py,isostat_backend.py,molclus_backend.py}; src/acp/confsearch/**; src/cccp/qc/interfaces/{crest.py,isostat.py,molclus.py,xtb_path.py}; src/cccp/calculation/{requests.py,results.py,selection.py}（todos 13/24）
  Acceptance criteria (agent-executable): `tests/test_cccp_task_{conformer_search,md_sampling,clustering,xtb_path_search}.py` 绿；独立 cccp 调用的成功/空/部分失败三态有断言；`grep -rnE "from acp|import acp" src/cccp/calculation/tasks` 无命中。
  QA scenarios (exact tool + invocation): happy = 四任务独立调用绿；failure = 需要 ACP 或组合策略被塞入任务层。Evidence .omo/evidence/acp-cccp-remediation/task-42-p2-impl-a.txt
  Commit: Y | feat(cccp): implement P2 sampling/clustering task execution cores

- [x] 43. P2 执行实现 B：censo_refine / nmr_shielding / orca_gradient
  What to do / Must NOT do: 在 `src/cccp/calculation/tasks/{censo_refine,nmr_shielding,orca_gradient}.py` 分别实现执行核心：censo_refine（preset/级别覆盖；能量/自由能/权重表 + refined ensemble，模板文本由翻译层生成）、nmr_shielding（GIAO；atom→`{symbol,isotropic}`）、orca_gradient（ORCA EnGrad；消费 CCCP 类型化任务请求，旧 `pes2ts_orca_gradient_request_v1` 由 ACP 适配器按 todo 24 映射后传入；原子梯度/能量）；成功/部分失败/空结果语义与记录身份按 todo 24 映射表；独立调用测试。Must NOT：让 ACP 工作流拼装 CENSO 模板文本；Must NOT 改动 DP4/DP5 科学语义。
  Parallelization: Wave 4 | Blocked by: 24,25 | Blocks: 26,27,28
  References: src/acp/backends/{censo_backend.py,orca.py:155-176}; src/acp/workflows/orca_gradient.py; src/cccp/qc/interfaces/{censo.py,orca.py:2907-2993}; src/cccp/calculation/{requests.py,results.py,selection.py}（todos 13/24）; tests/baseline/cccp_calculation_goldens/
  Acceptance criteria (agent-executable): `tests/test_cccp_task_{censo_refine,nmr_shielding,orca_gradient}.py` 绿；记录身份/自由能/屏蔽键形状与 goldens 一致；`grep -rnE "from acp|import acp" src/cccp/calculation/tasks` 无命中。
  QA scenarios (exact tool + invocation): happy = 三任务独立调用绿；failure = 模板文本由工作流拼装或屏蔽键形状变化。Evidence .omo/evidence/acp-cccp-remediation/task-43-p2-impl-b.txt
  Commit: Y | feat(cccp): implement CENSO refine and NMR shielding task execution cores

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [ ] F3. Real manual QA
- [ ] F4. Scope fidelity

## Commit strategy
- 每个 todo 一个（或 TDD 红/绿成对两个）conventional commit：`type(scope): summary`；type ∈ test/refactor/feat/fix/docs，scope ∈ guards/cccp/backends/contracts/executor/confsearch/nmr/pes/acceptance。
- 波次内按依赖顺序提交；每波结束前 `pytest -m "not slow" -q` 必须**无非预期失败**（`xfail(strict=True, raises=<预期异常>)`/已知红探针不计，且已知红集合只减不增），否则不进入下一波。
- 基线波（Wave 0）只提交**通过**的特征化测试；目标行为红测与对应实现同 todo 做红→绿配对提交；**无法在 Wave 0 转绿的探针以 `xfail(strict=True, raises=<预期异常>, reason="pending todo <N>")` 跟踪具体断言**，对应功能落地时**立即移除**（不等最终波次）；Wave 6 前清空本计划 xfail，最终验收以 `--runxfail` 复核（评审修正）。
- 禁止提交：`e2e-task-root.txt`、`~/.cccp.yaml` 内容或任何凭据（远程节点密码）、`.omo/evidence` 之外的临时产物。
- 兼容适配器/转发与实现放在同一个 commit，避免中间态出现双契约或第二实现。

## Success criteria
- **A1**：隔离环境（无 ACP）导入并调用 `cccp.calculation` **全部已公开任务（七核心 + P2 七项）**成功；静态导入检查覆盖函数内导入；`grep -rn "from acp\|import acp" src/cccp` 无 import 命中；无本计划遗留 xfail。
- **A2**：cccp 任务 API 具备请求校验/能力选择/执行/结果归一化，不转发 `acp.calculations`、不注入 ACP 私有对象。
- **A3**：14 工作流调用矩阵覆盖全部协议与合法分支，入口只经 `cccp.calculation.run_*`。
- **A4**：生产路径的优化/SP/频率/热化学只有一个共享执行核心，无第二套实现（`acp.backends.batch` 为显式隔离的遗留 backend-direct 面，不计入生产核心；生产模块不得直接依赖或执行旧批量入口，兼容根包重导出允许保留——`legacy_batch_quarantine`）。
- **A5**：ORCA route/block、CENSO template、xTB control/argv 构建点唯一来源；显示摘要与执行参数一致。
- **A6**：能力声明=选择=实现一致；stub 不可选；不支持/缺二进制/计算失败三态可区分。
- **A7**：三组等价（历史→迁移后 ACP；历史→独立 cccp；迁移后 ACP→独立 cccp）全绿；差异均落在预期行为变化表内。
- **A8**：任务目录/manifest/帧/checkpoint/远程/暂停继续取消/历史只读全部回归通过；跨版本恢复 fixtures（checkpoint/manifest/缓存/远程路径）与缓存版本/指纹兼容性测试通过。
- **A9**：真实 QC 小样与远程生命周期按 **PASS/FAIL/NOT_VERIFIED** 三态记录；断言（能量/坐标/频率/工件/容差/身份）齐全；NOT_VERIFIED 有原因且不得据此给出全通过结论；**结论措辞为『本轮约定验收范围通过』并列出未执行 live 的能力（不得表述为所有能力均已真实运行）**。
- **任务契约**：`TaskRequest`/`TaskContext` 分离、平台身份不入请求、两步选择 `select_backend(request, *, context)` 带选择记录、预检按本次 context 的程序路径、序列化规则明确。
- **契约顺序与适配器**：科学契约→封套类型→适配器→backend 迁移→选择器（无交付物依赖环）；适配器内存往返无损、不写文件；ACP 侧绑定信息（candidate/profile/任务关联）只存 ACP；适配器与 publisher 职责分离。
- **批量兼容**：旧 `acp.backends.batch` 公开符号保留（**隔离的遗留 backend-direct 面，不删除**）；生产模块不得直接依赖或执行旧批量入口（兼容根包重导出允许保留，`legacy_batch_quarantine`）；生产路径经单任务核心（普通/批量/PES 三路径一致）；`compat_forwarders_are_pure` 仅约束 `__init__.py` 等真转发模块。
- **热化学**：任务与旧 backend 入口复用同一 `cccp.qc` 共享适配/归一化；六参数/标准态/输出路径保持；单次 Shermo 调用；`batch_process_thermo` 等低层兼容包装继续调用同一 runner，不计为第二套科学实现。
- **导入无环**：全新进程两种导入顺序（backend↔calculation）均可；`cccp.calculation.errors` 导入不触发任务/backend 模块。
- **七核心能力映射**：todo 13 完成（含 `casscf`）；todo 24 仅扩展 P2 能力，不重开。
- **单向与复用**：热化学单向无回环且单次 Shermo；普通/批量/PES 三路径共享单任务核心且校验/错误分类/归一化一致。
- **护栏**：`check_grep_gates --suite architecture-remediation` 零豁免 + 负例注入有效 + scope-audit 实际审计迁移文件；隔离探针常驻；`test_architecture_invariants`/`test_f4_scope_audit` 未削弱。
- **既有套件**：`pytest -m "not slow" -q` 全绿且用例数≥基线。
- **文档**：根 AGENTS 规则 #17 与新架构文档同步，版本漂移修正到 0.1.3。
- **复核**：momus 与独立 Oracle 两路高精度复核（r14；含 r12 基线设计、重基线 2a23b93 与用户评审 #2 修订）均无条件通过；用户复核确认可进入实施（2026-10-03）。
