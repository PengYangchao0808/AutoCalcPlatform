# acp-nmr-goodman-gap-remediation - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 让 NMR 立体归属工作流从"能跑但结论不可信"变为"证据有效才排名、概率带明确状态与适用范围、参数与报告端到端一致"，并补齐 DP5 分支、原始谱处理与可复现的评测框架；同时为更快的预测路线预留隔离接口。

**Why this approach:** 先阻止无效证据生成确定性结论（分子图、逐峰归属、证据门、缺失即"不适用"），再修参数与报告连通，随后与正在进行的底层架构迁移共用唯一的计算任务实现、补齐 DP5/FCHL 与原始谱，最后建立评测框架。全程保留现有概率公式与回归方向，不重训模型。

**What it will NOT do:** 不会更换概率公式或重训、替换正式 DP5；不会用未获取的外部数据集或训练神经网络来声称准确率；不会恢复已退役工作流，也不会在同一层再造一套 QC 执行实现；不会静默容忍无证据候选获胜或把缺失值当 0。

**Effort:** XL
**Risk:** High - 与进行中的架构迁移计划共享同一编排文件与唯一执行体，且部分科学结论必须由真实 QC 与外部数据支撑。
**Decisions to sanity-check:**
- 评测/研究边界：本次交付评测框架、本机小样本真实 QC 冒烟与预测器协议；外部数据集校准与神经网络训练留待后续独立计划。
- 与迁移计划的串行化门：迁移计划的 NMR 改线与执行体任务完成后本计划才开跑（首个任务即设阻塞门），避免两个会话并发改同一文件。
- `--name` 调度断点的修复取"停止发射"而非"新增解析参数"。
- 测试策略：缺陷修复先写失败复现，新契约与评测框架随后补测。

Your next move: 可直接用 `$start-work acp-nmr-goodman-gap-remediation` 在独立 worker 会话执行（可选 `--make-pr` / `--worktree`）；若希望更稳，可先要求高精度双评审。完整执行细节见下。

---

> TL;DR (machine): XL, high risk, 60 implementation todos + F1-F4; evidence-gated ranking + typed probability states + parameter/report wiring + cccp nmr_shielding coordination + SignalGroup/FCHL + spectra/assignment + benchmark harness/local smoke + ShiftPredictor protocol; cross-plan serialization gate is todo 1.

## Scope
### Must have
- **A（输入与证据保护，G01–G03）**：候选结构图（键+立体+电荷）在输入层被保存；`NmrStructureMap` 提供稳定 `atom_uid`/`source_atom_index`/`label_scheme` 与 输入→候选→构象→QC 双向映射；无可靠分子图时不再按元素合并（单原子组 + `equivalence_unknown` 标记，严格模式拒绝）；实验谱按峰保存归属状态、解析错误、歧义标签（`H32 or H33` 作为候选标签集合）；两阶段归属（先锁定已知配对，再匹配剩余峰/信号），未标注峰不再静默丢弃；每候选记录期望/实际信号数、共享观测 ID、排除原因；无有效证据候选 `invalid`，不得进入排名/成为 winner。
- **B（概率状态与数值稳定，G05/G07/G14）**：`ProbabilityResult`（`model_id/version/status/probability|null/mode/calibration_status/reasons/atom_diagnostics`）逐候选返回；DP5 资产缺失 = `unavailable` + `probability=null`；占位路径只服务测试/显式探索并改名 `diagnostic_score`；无碳 = `not_applicable`；0 构象无效、1 构象以权重 `[1.0]` 走同一几何加权管线（FCHL 可达）；`dp5_mode/fchl_kernel` 不再是共享可变状态；DP4 用稳定 log-CDF（`log(2)+log_ndtr(-|z|)`）、占位 sigmoid 稳定化；非有限值/非法权重/退化回归/长度不齐返回类型化失败。
- **C（参数与报告连通，G06/G13）**：单一 `resolve_nmr_method`（catalog method → 类型化有效配置 → 本地/远端 argv）覆盖 `functional→nmr_method`、`basis→nmr_basis`、`solvent_model`、温度、核种、TMS、能窗、构象上限；`acp run nmr` 补 `--name` 解析（修复 `runner.py`/`script_gen.py` 发射但解析器缺失的断点）；报告/图表 manifest 路径以 `relative_to(RESULT)` 生成并校验存在（含 `plots/`）；本地与远端报告经统一读取路径（远端 `{content}` 解包）且前端不把 `null` 当 0；`nmr_report.json` schema v2：`scaled_ppm`、完整有效配置、逐候选概率状态、原始/有效布居、校准 provenance；残差符号统一 `scaled-exp`，R² 区分回归相关与预测拟合；旧报告只读兼容并显示“验证状态未知”。
- **D（QC 任务与 ensemble 证据，G04/G09/G15）**：与进行中的迁移计划收敛——`cccp.calculation.tasks.nmr_shielding` 唯一执行体（若迁移 todo 42/43 已落地则审计复用，否则按冻结契约实现并更新迁移台账 row 40）；ACP NMR 改线消费 `run_nmr_shielding`，保留 ACP 的 CREST→CENSO 编排；shielding checkpoint 指纹（几何哈希+方法/基组/溶剂/核种/电荷）+ 中断续算 + 有界并发/资源预算；`NmrProtocolSpec` 六段（sampling/geometry/population_energy/shielding/reference/statistical_model）与 `exploratory|reference_validation|acp_calibrated` 模式及 `unvalidated_protocol` 判定；ensemble 统一能量层级、记录 preselection/selected/successful 布居与剔除原因、按所需核的逐构象完整性门、leave-one-conformer-out/温度敏感性标记；`requirements-node.txt`/`pyproject.toml` 依赖与 wheel 资产可加载。
- **E（DP5 完整性与解释，G08/G12/G16）**：`SignalGroup(atom_uids, coefficients, equivalence_basis, peak_capacity, experiment_ref)` 贯穿平均→归属→DP5；代表原子替换不改变结果；FCHL 分层 golden（descriptor→kernel→加权 KDE→候选概率）与 ≥86 原子碎片路径（`frag_reps.gz` 训练索引对应）；近邻支持度/域外状态；原子级 DP5 诊断、逐核 DP4 分项、leave-one-signal-out 排名变化；前端结构原子↔归属行↔实验峰联动。
- **F（原始谱与归属，G10/G11）**：采集数据/处理谱/谱线/共振四层模型与处理 provenance/质量指标；相位/数字滤波/参考失败显式化；碳谱峰拟合+溶剂排除、氢谱 multiplet 分组+总氢/甲基约束+重叠不确定性；多实验选择而非盲目拼接、2D `ser` 拒收；迭代归属（外部初标定启动，交替归属/标定收敛）并保存近优解；人工修订只重算分析（`AnalysisRevision`）不重跑 QC；有标注谱处理 fixtures。
- **G（科学基准与模型校准）**：评测 harness（数据集 manifest schema + provenance/许可/哈希、分核 MAE/RMSE、归属正确率、Top-1、Brier/log-loss/校准图、拒答率、CPU/内存、按分子聚类的配对统计、预注册阈值）；**本机 level-1 真实 QC 冒烟**（3–5 个小分子，含刚/柔、H/C、真实 ORCA 日志）；协议转移 A/B 实验 harness；数据分层 2–5 的 loader + 获取/运行 runbook；reference_validation 对照产物。
- **H（DP5q 研究分支）**：`ShiftPredictor` 协议（按原子映射的位移、预测分布、几何要求、模型 provenance）与模型隔离；stub 适配器 + 评测挂钩；**不替换**正式 DP5。
- **横切**：`docs/ACP_NMR_DP4_DevDoc.md` 按 gap 文档 §11 逐项更正；嵌套 `AGENTS.md` 更新；迁移台账逐行同步；`job_edit`/grep gates/架构不变量守护保持绿色；全部验收 agent 可执行。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不更换 DP4 高斯公式、不改 `fit_scaling_goodman` 回归方向（只修边界/证据门/R²/符号展示）；不重训、不替换正式 DP5；不把问题归因为“缺少机器学习”。
- 不做公开数据集外部校准战役、不训练 DP5q GNN、不做无数据支撑的准确率/校准声明；不伪造基准数字。
- 不恢复 retired 工作流或旧 CLI；不新建第二套 shielding executor；不在 ACP 侧新增执行实现；不绕过 `cccp.calculation` 直调 backend/runner；`cccp` 不导入 `acp`；shim 不放实现。
- 不新增 JobStatus 表达分析可信度（质量状态放在分析结果内；人工审阅只用现有 `WAITING_REVIEW` review-only 语义）。
- 不引入 `pymatgen`；不新增裸 `except Exception`；不把 `job_id`/时间戳写入磁盘路径；不静默 fallback 制造证据（元素合并、null→0、占位概率冒充正式概率）。
- 不破坏旧报告只读兼容；不把展示四舍五入值当重算源。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: 缺陷修复 TDD（每个 G 先落最小复现失败测试再修）；新契约/新模块/评测 harness tests-after + 守护测试；pytest（raw assert、tmp_path、`unittest.mock`、无 pytest-mock）
- Evidence: <attemptDir>/task-<N>-acp-nmr-goodman-gap-remediation.<ext> (attemptDir = currentAttemptDir from 'omo ulw-loop status --json', .omo/evidence/ulw/<session>/<goalId>/a<attempt>; outside ulw-loop use .omo/evidence/)
- 单元/回归门（每个 todo 必跑）：`pytest tests/test_acp_nmr_*.py tests/test_acp_workflows_nmr.py -q`
- 守护门（每个触碰对应面的 todo 必跑）：`pytest tests/test_architecture_invariants.py tests/test_grep_gates_script.py -q`（D/E 触碰 cccp 时）；`pytest tests/test_acp_job_edit.py -q`（catalog/工作流面时）；`pytest tests/test_frontend_sync.py -q`（前端时）；`pytest tests/test_acp_api_v1.py tests/test_acp_catalog.py -q`
- 数值 golden：DP4 固定上游残差集 `atol=1e-10`（float64 单纯代数）；FCHL/KDE 容差先测定真实核重复性再冻结，禁止为“最终概率接近”而放宽分层容差
- 真实 QC（门控）：level-1 冒烟用 `@requires_orca/crest/censo` + `--run-slow --run-integration`，证据 JSON/日志落 `.omo/evidence/`，不替代科学准确率结论
- 恢复/产物：中断续算测试、manifest 路径存在性测试、旧报告只读 fixture、wheel 资产清单检查
- 端到端：本地与远端（mock SFTP/bsub 或真实节点）同一有效配置输出一致；方法/几何变化使缓存失效；谱峰修改不触发 QC

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 0（前置门）**：1（协调门；未通过则整体 BLOCKED，不改产品代码）。
- **Wave 1（A：输入与证据保护，G01–G03）**：2–9。2/3 可并行；4 依赖 2/3；5 依赖 3/4；6 可与 4/5 并行；7 依赖 6；8 依赖 4–7；9 依赖 2/3。
- **Wave 2（B：概率状态与数值稳定，G05/G07/G14）**：10–16。10/11 可并行；12 依赖 10/11；13 依赖 10；14 依赖 12/13；15 依赖 14；16 依赖 11/13/14/15。
- **Wave 3（C：参数与报告连通，G06/G13）**：17–25。17 先；22 = 报告路径可与 17 并行；18 依赖 17；19 依赖 18；20 依赖 18/19；21 依赖 17；23 依赖 22；24 依赖 10/21/22；25 依赖 21。
- **Wave 4（D：QC 任务与 ensemble 证据，G04/G09/G15，与迁移计划收敛）**：26–33。26→27；28/29 依赖 27；30 依赖 28/29；31 依赖 29；32 可与 28–31 并行；33 依赖 26/27/30/31。
- **Wave 5（E：DP5 完整性与解释，G08/G12/G16）**：34–40。34 依赖 26/30；35/36 依赖 34；37 依赖 35/36；38 依赖 37；39 依赖 34/35/37/38；40 依赖 39。
- **Wave 6（F：原始谱与归属，G10/G11）**：41–48。41 先；42 依赖 41；43/44 依赖 42（可并行）；45 依赖 43/44；46 依赖 24/41；47 依赖 24/28/46；48 依赖 43–47。
- **Wave 7（G：基准框架与本地冒烟）**：49–54。49/50 可并行；51/52 依赖 49/50；53 依赖 50；54 依赖 31/36/49/52/53。
- **Wave 8（H：DP5q 协议设计 + 收口）**：55–60。55 依赖 54；56 依赖 55；57 依赖 54/55/56；58 依赖 60；59/60 依赖 2–57（可并行）。
- **Final verification wave**：F1–F4，全部通过并等待用户明确 okay 才宣告完成。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1（协调门） | none | 2-60 | - |
| 2-9（A） | 1 | 10-16,26-40 | Wave 1 内部按 todo 依赖 |
| 10-16（B） | 8（A 证据门） | 17-25,34-60 | 10/11 并行 |
| 17-25（C） | 10,21 | 26-60 | 22 与 17 并行 |
| 26-33（D） | 1,21,24；迁移计划 25/27/42/43 已完成 | 34-48 | 32 与 28-31 并行 |
| 34-40（E） | 26,30,34 等 | 49-60 | 35/36 并行 |
| 41-48（F） | 8,16,22,24 | 49-60 | 43/44 并行 |
| 49-54（G） | 31,36,39,48 | 55-60 | 49/50 并行 |
| 55-60（H+收口） | 2-57 | F1-F4 | 58/59/60 部分并行 |
| F1-F4 | 全部 | - | 四路并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. 协调门：核对 active 迁移计划状态并记录串行化决策
  What to do / Must NOT do: 读取 `.omo/boulder.json` 与 `.omo/plans/acp-cccp-architecture-remediation.md`，检查其 todos 25/27/42/43 的 checkbox 状态；写 `.omo/evidence/acp-nmr-goodman-gap-remediation/COORDINATION.md` 记录分支：(a) 若 27/42/43 全为 `[x]` → 记“proceed：D1 仅审计、D2 复用迁移 todo 27 的改线、grep-gate allowlist 由迁移 todo 29 收口”，并继续 Wave 1；(b) 否则 → 停止并输出 `BLOCKED: acp-cccp-architecture-remediation todos 25/27/42/43 未完成（列出 pending 项）；两个计划不得并发改 src/acp/workflows/nmr.py`。Must NOT：本 todo 修改任何产品代码；不得编辑迁移计划文件本身。
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2-60
  References (executor has NO interview context - be exhaustive): .omo/boulder.json:127-357；.omo/plans/acp-cccp-architecture-remediation.md:360-366,480-494；tests/baseline/refactor-evidence/migration_ledger.md:40；root AGENTS.md §MIGRATION PERIOD RULES
  Acceptance criteria (agent-executable): `test -s .omo/evidence/acp-nmr-goodman-gap-remediation/COORDINATION.md`；若 proceed：`grep -nE '^\- \[x\] (27|42|43)\.' .omo/plans/acp-cccp-architecture-remediation.md` 三项齐全；若 blocked：`git status --porcelain src/` 无输出
  QA scenarios (name the exact tool + invocation): happy = 27/42/43 已完成 → COORDINATION.md 记 proceed 且后续 wave 可执行；failure = 存在 pending → 打印 BLOCKED 且 `git status --porcelain src/` 为空，Evidence <attemptDir>/task-1-acp-nmr-goodman-gap-remediation.txt
  Commit: N | chore(nmr): record cross-plan coordination gate (no product code)

- [x] 2. src/acp/nmr/structure_map.py：新增稳定原子身份与标签方案映射
  What to do / Must NOT do: 新建 `src/acp/nmr/structure_map.py`，定义 frozen `AtomIdentity(atom_uid, source_atom_index, element, label, label_scheme)` 与 `NmrStructureMap`（input→candidate→conformer→QC 索引双向映射；每元素编号 `C1/H1` 与 Goodman 全原子序号 `a+1` 两套 scheme；歧义标签集合解析助手）。在 `acp/nmr/__init__.py` 导出。新增 `tests/test_acp_nmr_structure_map.py`（round-trip、打乱原子顺序后恢复同一映射）。Must NOT：保留 `equivalence._build_label_index` 作为唯一标签源；不得改动位移/概率科学语义。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3,4,9 | Can parallelize with: 3
  References (executor has NO interview context - be exhaustive): src/acp/nmr/equivalence.py:157-171,174-186；src/acp/nmr/enumerate.py:456-471；src/acp/workflows/nmr.py:219；docs/ACP_NMR_Goodman_Gap_Investigation_2026-10-05.md G03
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_structure_map.py tests/test_acp_nmr_equivalence.py -q` 通过；property：对打乱顺序的同一 SDF，两套 scheme 的 `atom_uid→shift` 映射一致
  QA scenarios (name the exact tool + invocation): happy = round-trip + shuffle 测试绿；failure = 缺元素标签或 rank 越界时抛类型化错误而非静默，Evidence <attemptDir>/task-2-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | feat(nmr): add stable atom identity map and label schemes

- [x] 3. workflows/nmr.py + intake：输入层保存带键/立体/电荷的分子图与 provenance
  What to do / Must NOT do: 在 `_parse_candidates`（104-130）与 `acp/io/structures.py`/`acp/intake/parsers.py` 捕获键合 RDKit Mol：SMILES→`MolFromSmiles`；SDF/MOL→mol block；XYZ→`rdDetermineBonds.DetermineBonds`（需显式 charge/multiplicity，记录 `nmr_topology_source ∈ {smiles,sdf,xyz_inferred,xyz_unavailable}`）；用 `NmrStructureMap` 固化 source/canonical 原子序。XYZ 且无法推断键时置 `xyz_unavailable`，不再默认元素图。Must NOT：无电荷/多重度就猜键；不得静默回退元素合并。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 4,5,9 | Can parallelize with: 2
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:104-130,861-873；src/acp/io/structures.py；src/acp/intake/parsers.py；docs gap G01/G03
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_topology.py`；同一分子的 SMILES/SDF/带映射 XYZ 产生相同 `NmrStructureMap.atom_uid→element`；无键 XYZ 不合并全部碳
  QA scenarios (name the exact tool + invocation): happy = 三格式映射一致；failure = XYZ 无电荷 → `xyz_unavailable` 标记且严格模式拒绝，Evidence <attemptDir>/task-3-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | feat(nmr): preserve molecular graph and topology provenance at input

- [x] 4. src/acp/nmr/equivalence.py：移除无拓扑元素合并，改为单原子组 + equivalence_unknown
  What to do / Must NOT do: `detect_equivalence_groups` 在 `mol=None` 时返回单原子组并标记 `equivalence_unknown`（不再调 `_element_groups` 合并）；`merge_explicit_and_detected` 输出带等价依据的分组；`NmrConfig` 增 `strict_equivalence`（默认 False，严格模式在无拓扑时拒绝）；更新 `tests/test_acp_nmr_equivalence.py:13-32` 现有锁定断言。Must NOT：保留 `_element_groups` 为默认路径；不得让 `EQ:` 只覆盖部分原子时其余被错误合并。
  Parallelization: Wave 1 | Blocked by: 2,3 | Blocks: 5,8 | Can parallelize with: 6
  References (executor has NO interview context - be exhaustive): src/acp/nmr/equivalence.py:27-120,123-154；src/acp/workflows/nmr.py:523-534；tests/test_acp_nmr_equivalence.py:13-44；docs gap §12.1 `detect_equivalence_groups(["C","C","O","H","H"])`
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_equivalence.py -q`；`python -c "from acp.nmr.equivalence import detect_equivalence_groups; g=detect_equivalence_groups(['C','C','O','H','H']); assert all(len(x)==1 for x in g)"`
  QA scenarios (name the exact tool + invocation): happy = 无拓扑→5 个单原子组且标记 unknown；failure = 严格模式无 mol → 抛类型化 `EquivalenceError`，Evidence <attemptDir>/task-4-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): never merge inequivalent atoms without topology

- [x] 5. workflows/nmr.py::_try_build_rdkit_mol：候选图优先、metadata.smiles 次之
  What to do / Must NOT do: 按 `NmrStructureMap` 候选图 → `metadata["smiles"]` → `metadata["source"]` 顺序构 Mol；以 `CanonicalRankAtoms` 校验原子数与顺序对应；枚举分支（219 写 smiles）与 XYZ 推断路径均覆盖。Must NOT：只读 `source`；Mol 原子数不匹配时按元素回退。
  Parallelization: Wave 1 | Blocked by: 3,4 | Blocks: 8 | Can parallelize with: 6
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:617-642,217-224；docs gap G01
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_topology.py tests/test_acp_nmr_equivalence.py -q`；枚举自 XYZ 输入时候选图被采用（spy 断言非 None）
  QA scenarios (name the exact tool + invocation): happy = 枚举候选用 `metadata.smiles` 建图；failure = 原子序不匹配 → 类型化错误而非越界，Evidence <attemptDir>/task-5-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): build equivalence mol from candidate graph first

- [x] 6. src/acp/nmr/io.py + models.py：逐峰归属状态与解析错误可见
  What to do / Must NOT do: `ExperimentalPeak` 增 `label_candidates: tuple[str,...] | None` 与逐峰 assigned 语义；`ExperimentalNmr` 增 `parse_errors` 与逐元素 assigned 状态；解析器记录未知 token、缺失/重复标签、未匹配峰/原子、歧义标签（`H32 or H33` → 候选标签集合）；移除 `assigned = any(...)` 全局开关。Must NOT：静默丢弃无法解析 token；不得把 `H32 or H33` 只取一个。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 7,8 | Can parallelize with: 4,5
  References (executor has NO interview context - be exhaustive): src/acp/nmr/io.py:35-140；src/acp/nmr/models.py:120-161；tests/test_acp_nmr_io.py；docs gap G02/G03
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_io.py -q`；复现 `C: 10(C1), 20\nH: 1, 2` 解析出 4 个峰、1 个已知标签、3 个未标注且 `parse_errors` 可读
  QA scenarios (name the exact tool + invocation): happy = 混合归属全峰保留；failure = 重复/未知标签进入 `parse_errors` 且后续评分显式拒绝非法输入，Evidence <attemptDir>/task-6-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): track per-peak assignment state and parse errors

- [x] 7. src/acp/nmr/assignment.py：两阶段匹配（锁定已知配对 + 匹配剩余）
  What to do / Must NOT do: 新增 `AssignmentResult`（pairs/locked/unmatched/near_optimal/scale_iterations）；`match_assigned` 先锁定显式标签，未标注峰回落 Hungarian；`workflows/nmr.py::_analyze_candidate` 消费新返回；保存近优解与差值。Must NOT：静默跳过未标注峰；不得让每个候选过度自由重排归属后仍套用原概率模型。
  Parallelization: Wave 1 | Blocked by: 6 | Blocks: 8 | Can parallelize with: 4,5
  References (executor has NO interview context - be exhaustive): src/acp/nmr/assignment.py:31-166；src/acp/workflows/nmr.py:544-569；tests/test_acp_nmr_assignment.py；docs gap G02/G11
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_assignment.py -q`；混合归属复现的 4 个峰全部出现在 pairs 或 unmatched 诊断中
  QA scenarios (name the exact tool + invocation): happy = 已知配对保持 + 剩余峰被匹配；failure = 未知标签不参与匹配且记入诊断，Evidence <attemptDir>/task-7-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): two-phase assignment preserves known pairs and unmatched peaks

- [x] 8. 证据门：无有效证据候选不得排名/获胜
  What to do / Must NOT do: 为每个候选记录期望/实际信号数、共享实验观测 ID、排除原因（`CandidateEvidence`）；评分前校验核种与有效证据集合可比；空/不足证据候选从 DP4 归一化中排除并标 `invalid`；`NmrReport.winner` 不得选中 invalid；单候选 DP4 显示不适用；两点内部标定标 `evidence_insufficient`。Must NOT：给空证据候选更高相容性；不得简单加任意罚分或改平均 log-likelihood。
  Parallelization: Wave 1 | Blocked by: 4,5,6,7 | Blocks: 15,17 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/probability.py:25-56；src/acp/workflows/nmr.py:1062-1092,1103-1109；src/acp/nmr/models.py:285-335；docs gap §12.1 `normalize_dp4([compute_dp4({},m), compute_dp4({"13C":[2,3]},m)])`
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_evidence_gate.py`；空残差候选 DP4 状态为 invalid 且不参与归一化，非 0.934
  QA scenarios (name the exact tool + invocation): happy = 有证据候选正常归一化；failure = 全无证据 → 报告无 winner 且逐候选 reasons，Evidence <attemptDir>/task-8-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): gate invalid or insufficient evidence out of DP4 ranking

- [x] 9. EQ/OMIT/stereocenters 映射闭环 + 枚举电荷保留 + 打乱原子 fixtures
  What to do / Must NOT do: `_explicit_eq_to_indices`/`_omit_atom_indices`/`--stereocenters` 全部经 `NmrStructureMap` 解析；枚举路径保留显式 charge（移除 `charge or 0` 归零）；新增打乱 SDF/XYZ 顺序、混合元素序列、带电候选、歧义标签 fixtures。Must NOT：按每元素序号做唯一解析；不得丢失离子电荷。
  Parallelization: Wave 1 | Blocked by: 2,3 | Blocks: 12,14 | Can parallelize with: 6
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:170-232,584-614；src/acp/nmr/enumerate.py:395-519；tests/test_acp_nmr_enumerate.py；docs gap G03
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_enumerate.py tests/test_acp_nmr_structure_map.py -q`；故意打乱原子顺序后恢复相同配对与概率
  QA scenarios (name the exact tool + invocation): happy = 打乱后概率不变；failure = 歧义标签/带电候选固定 fixture 有确定行为，Evidence <attemptDir>/task-9-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): resolve labels via stable map and preserve charge

- [x] 10. models.py + report.py：新增类型化 ProbabilityResult（状态/null/mode/校准）
  What to do / Must NOT do: 新增 frozen `ProbabilityResult(model_id, model_version, status, probability: float|None, mode: str|None, calibration_status, reasons, atom_diagnostics)` 与 `CandidateProbability(dp4, dp5)`；写入 `models.py` 并在 `nmr/__init__` 导出；`CandidateResult.as_dict`/`NmrReport.as_dict` 改用该结构（DP5 不可用时 `probability=null`，禁止落成 0）。Must NOT：改变 DP4/DP5 科学公式；不得在序列化处把 null 转 0。
  Parallelization: Wave 2 | Blocked by: 8 | Blocks: 11,12,13,14,16,20 | Can parallelize with: 11
  References (executor has NO interview context - be exhaustive): src/acp/nmr/models.py:285-369；src/acp/nmr/probability.py；docs gap §8.2 建议 schema
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_probability_state.py`；`nmr_report.json` round-trip 后 `dp5.probability is None` 保持 null
  QA scenarios (name the exact tool + invocation): happy = 状态字段完整 round-trip；failure = 缺 model_id/status 时 schema 校验失败，Evidence <attemptDir>/task-10-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): add typed probability state with null/unavailable semantics

- [x] 11. error_model.py：DP4 高斯改稳定 log-CDF（log(2)+log_ndtr(-|z|)）
  What to do / Must NOT do: `GoodmanErrorModel.log_likelihood` 用 `math.log(2) + scipy.special.log_ndtr(-abs(z))` 替代 `math.log(math.erfc(z/sqrt(2)))`；对 NaN/Inf 残差返回类型化失败；σ 与双尾公式语义不变。Must NOT：改 σ、均值或双尾定义；不得用 try/except 吞掉非有限值。
  Parallelization: Wave 2 | Blocked by: 8 | Blocks: 12,16 | Can parallelize with: 10
  References (executor has NO interview context - be exhaustive): src/acp/nmr/error_model.py:136-166；tests/test_acp_nmr_probability.py；docs gap §12.1 `model.log_likelihood([100.0],"13C")`
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_probability.py -q`；100 ppm 碳残差返回有限值不抛 ValueError；常规残差与官方固定分支一致（`atol=1e-10`）
  QA scenarios (name the exact tool + invocation): happy = 常规 + 极端残差均稳定；failure = NaN/Inf → 类型化失败状态，Evidence <attemptDir>/task-11-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): use stable log-CDF for DP4 Gaussian likelihood

- [x] 12. probability.py：稳定 sigmoid + 统计前置输入校验
  What to do / Must NOT do: `dp5_log_to_probability` 改数值稳定实现（按符号分支/`expm1`，极端值不 OverflowError）；进入统计前检查 finite、权重合法、回归点数、退化实验 x、数组等长；无效输入返回类型化失败/不适用。Must NOT：改 DP4 归一化数学；不得对无效输入隐式成功。
  Parallelization: Wave 2 | Blocked by: 10,11 | Blocks: 14,16 | Can parallelize with: 13
  References (executor has NO interview context - be exhaustive): src/acp/nmr/probability.py:42-130；docs gap §12.1 `dp5_log_to_probability(-1000)`；docs gap G14
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_probability.py -q`；`dp5_log_to_probability(-1000)` 返回 [0,1] 内有限值
  QA scenarios (name the exact tool + invocation): happy = 常规残差概率与官方一致；failure = 零/负权重、不等长、退化 x → 类型化失败，Evidence <attemptDir>/task-12-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): harden DP5 numerical paths and input validation

- [x] 13. DP5 降级语义：缺资产 = unavailable/null（占位另名 diagnostic_score）
  What to do / Must NOT do: 修 `workflows/nmr.py:1076-1094`：`dp5_model_available()` 假或加载失败 → DP5 `status="unavailable"`、`probability=None`、`reasons` 记因；占位路径仅在测试/显式 `error_model=placeholder-*` 时可运行且写入独立字段 `diagnostic_score`（不再填 `dp5_probability`）；无碳 → `not_applicable`（`probability.py:117-121` 调用处判定）；告警是否 placeholder 改看 `ProbabilityResult` 而非 DP4 `error_model` 前缀。Must NOT：缺资产仍输出 0.5；不得把 unavailable 参与排序。
  Parallelization: Wave 2 | Blocked by: 10 | Blocks: 16,20 | Can parallelize with: 11,12
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:1074-1108,1192-1209；src/acp/nmr/probability.py:86-130；src/acp/nmr/error_model.py:494-521；docs gap §12.1 `completed / dp5=0.5 / note=''`
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_probability_state.py`：模拟资产缺失 → `status=="unavailable"` 且概率为 None；无碳 → `not_applicable`
  QA scenarios (name the exact tool + invocation): happy = 有模型时正式概率；failure = 缺模型/加载失败/无碳三分支可区分，Evidence <attemptDir>/task-13-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): DP5 unavailable reports null with reasons, not placeholder 0.5

- [x] 14. DP5 逐候选不可变结果：移除共享 dp5_mode/fchl_kernel 可变状态
  What to do / Must NOT do: `_compute_candidate_dp5` 返回不可变 `(probability, mode, kernel, diagnostics)`；删除 `dp5_model.dp5_mode`/`fchl_kernel` 的写入与后置读取（error_model.py:238/342/377/418 与 workflows/nmr.py:1101-1102）；报告逐候选记录 mode；候选间不同模式时 summary 写 `mixed` 并展开。Must NOT：从共享模型对象读“最后状态”；不得让后一候选覆盖前一候选的 mode。
  Parallelization: Wave 2 | Blocked by: 12,13 | Blocks: 15,24 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:653-766,1083-1102；src/acp/nmr/error_model.py:219-473；docs gap G07
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_probability_state.py`：调换候选顺序后每候选 mode 不变；混合 FCHL/fallback 时 summary=mixed
  QA scenarios (name the exact tool + invocation): happy = 逐候选 mode 正确；failure = 强制回退候选不影响其他候选 mode，Evidence <attemptDir>/task-14-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): per-candidate immutable DP5 mode without shared mutation

- [x] 15. 单/零构象：0 无效；1 构象走同一几何加权管线（FCHL 可达）
  What to do / Must NOT do: `_compute_candidate_dp5` 中 `len(conformer_shifts)<=1` 分支改为：0 个完整构象 → `invalid`；1 个构象 → 权重 `[1.0]` 调用 `probability_per_conformer`（FCHL 可用时 `probability_per_conformer_fchl`），不得直接走无几何平均残差路径。Must NOT：单构象绕过 FCHL；不得把 0 构象当作合法。
  Parallelization: Wave 2 | Blocked by: 14 | Blocks: 24,27 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:700-766；src/acp/nmr/error_model.py:347-473；tests/test_acp_nmr_fchl.py；docs gap G07
  Acceptance criteria (agent-executable): spy 测试断言单构象调用 `probability_per_conformer[_fchl]` 且权重 `[1.0]`；0 构象返回 invalid
  QA scenarios (name the exact tool + invocation): happy = 单构象 FCHL 被调用；failure = 几何缺失 → 类型化降级状态而非静默平均，Evidence <attemptDir>/task-15-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): route single/zero conformers through the geometry-weighted DP5 path

- [x] 16. 排名与 winner：invalid/unavailable 不参与 tie-break
  What to do / Must NOT do: `NmrReport.winner` 只在有效候选间取 max（现有 `(dp4, dp5)` tie-break 在 models.py:331-335 改为仅有效 DP4，DP5 不可用不参与）；单候选 DP4 显示“不适用排名”；报告 summary 携带逐候选 status。Must NOT：让 unavailable DP5 影响排名；不得在无有效候选时编造 winner。
  Parallelization: Wave 2 | Blocked by: 11,13,14,15 | Blocks: 20,29 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/models.py:320-369；src/acp/workflows/nmr.py:1114-1140；docs gap G05
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_probability_state.py tests/test_acp_workflows_nmr.py -q`；无有效候选 → `summary.winner is None`
  QA scenarios (name the exact tool + invocation): happy = winner 全有效；failure = 全候选 invalid → 无 winner 且 reasons 可见，Evidence <attemptDir>/task-16-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): exclude invalid/unavailable candidates from winner ranking

- [x] 17. 新增唯一 resolve_nmr_method：catalog method → 类型化 NmrMethodConfig
  What to do / Must NOT do: 新建 `src/acp/nmr/method_config.py`，frozen `NmrMethodConfig` 与 `resolve_nmr_method(method: Mapping, config: Mapping|None) -> NmrMethodConfig`：解析 `functional→nmr_method`、`basis→nmr_basis`、`solvent_model`、`solvent`、`nuclei`、`boltzmann_temp`、`tms_shielding_h/c`、`ewin`、`max_conformers`、`error_model`、conformer preset；`solvent_model=none` 必须保留为 none（不被默认 chloroform/cpcm 覆盖）；与模型不匹配的方法显式拒绝。Must NOT：复用 confsearch 的 `censo_solvent_from_method` 级别回退（其回退 id `refinement_sp/dft_opt` 不含 nmr `giaoa`）；不得在前端做 hoist。
  Parallelization: Wave 3 | Blocked by: 10 | Blocks: 18,19,20,21 | Can parallelize with: 22
  References (executor has NO interview context - be exhaustive): src/acp/catalog.py:3205-3270,1377-1411；src/acp/scheduler/jobs.py:137-149,352-384；src/acp/workflows/nmr.py:256-317,774-798；frontend/ACP_Workbench_v2.html:30776-30785；docs gap G06
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_method_config.py`；矩阵用例（默认 + 自定义 functional/basis/solvent_model=none/气相）解析结果确定
  QA scenarios (name the exact tool + invocation): happy = 默认 profile 与自定义字段均命中；failure = 不匹配模型组合 → 明确 `ValueError`，Evidence <attemptDir>/task-17-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): single typed method resolver for NMR parameters

- [x] 18. CLI：补 --solvent-model/--max-conformers；修 --name 断点；_handle_nmr 走 resolver
  What to do / Must NOT do: `acp run nmr` 解析器（cli.py:2394-2539）新增 `--solvent-model` 与 `--max-conformers`；**决定：移除** runner.py:1575-1576 与 script_gen.py:525-526 的 `--name` 发射（`_handle_nmr` 从未消费 `args.name`，任务目录命名由 manager 负责），并新增“构建 argv 能通过 CLI parser”回归；`_handle_nmr` 改调 `resolve_nmr_method`；`run_nmr_analysis` 新增 `solvent_model` 参数并透传到 `NmrConfig`。Must NOT：保留 `--name` 发射而不加解析器（manager.py:530-538 总会把 spec.name 设为非空，必然 parse 失败）；不得让 `solvent_model` 止步于 workflow 签名。
  Parallelization: Wave 3 | Blocked by: 17 | Blocks: 19,20 | Can parallelize with: 22
  References (executor has NO interview context - be exhaustive): src/acp/cli.py:2389-2539,3605-3690；src/acp/scheduler/runner.py:1575-1576；src/acp/scheduler/remote/script_gen.py:525-526；src/acp/scheduler/manager.py:530-538；docs gap G06 + 四路探查 C3
  Acceptance criteria (agent-executable): `python -c "from acp.cli import build_parser; p=build_parser(); p.parse_args(['run','nmr','--input','CCO','--spectrum','x','--solvent-model','none','--max-conformers','5'])"`；`pytest tests/test_acp_nmr_runner.py tests/test_acp_cli.py -q`
  QA scenarios (name the exact tool + invocation): happy = GUI 字段→argv→parser 全通过；failure = 旧 `--name` argv 不再产生且回归测试拦截，Evidence <attemptDir>/task-18-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): close CLI parameter forwarding and remove broken --name emission

- [x] 19. nmr_method_flags 改为消费 resolver（单一来源）
  What to do / Must NOT do: `jobs.py::nmr_method_flags` 改为经 `resolve_nmr_method` 生成规范 flag 组（含 `--solvent-model`、`--max-conformers`），不再读扁平的 `nmr_method/nmr_basis` 键；nuclei 逗号连接保持。Must NOT：保留两套键名读取；不得在 runner 与 script_gen 各自拼装。
  Parallelization: Wave 3 | Blocked by: 18 | Blocks: 20 | Can parallelize with: 21
  References (executor has NO interview context - be exhaustive): src/acp/scheduler/jobs.py:352-384；tests/test_acp_nmr_runner.py
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_runner.py -q`；对 `{functional, basis, solvent_model}` payload 输出非空且含对应 flag
  QA scenarios (name the exact tool + invocation): happy = 自定义字段完整发射；failure = 空/缺字段不发射且不报错，Evidence <attemptDir>/task-19-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | refactor(nmr): emit CLI flags from the single method resolver

- [x] 20. 本地/远端命令构建共用 resolver + argv 闭环测试
  What to do / Must NOT do: `runner.py::_build_nmr_cmd` 与 `script_gen.py::build_remote_nmr_cmd_tail` 均改调 resolver；测试覆盖 GUI payload → 有效配置 → 本地 argv → 远端 argv → 实际 ORCA 输入（用 mock backend 捕获输入）的闭环，默认与自定义各一组。Must NOT：本地/远端行为分叉；不得只测 cmd 子串。
  Parallelization: Wave 3 | Blocked by: 18,19 | Blocks: 22,29 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/scheduler/runner.py:1500-1594；src/acp/scheduler/remote/script_gen.py:475-537；src/acp/scheduler/jobs.py:127-149；tests/test_remote_phase*.py；docs gap G06 验收
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_runner.py tests/test_remote_phase1.py -q` + 新闭环测试；同一 payload 本地/远端有效配置一致
  QA scenarios (name the exact tool + invocation): happy = 默认 + 自定义字段闭环；failure = 不匹配方法在构建期拒绝，Evidence <attemptDir>/task-20-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): unify local and remote arg construction and add closed-loop tests

- [x] 21. NmrConfig/`_build_nmr_config`：记录完整有效配置并让 solvent_model 到达 ORCA
  What to do / Must NOT do: `NmrConfig` 增 `solvent_model`、`energy_window_kcal`、`max_conformers`、`conformer_preset`、`boltzmann_temp`、TMS、（D 期）协议指纹字段；`_build_nmr_config` 不再从 `theory.nmr` 悄悄取默认而忽略 method；`_run_giao_for_conformers` 使用 `nmr_config.solvent_model`；`solvent_model=none` 不注入 cpcm。Must NOT：分别解析默认值造成溶剂不一致；不得丢弃 method 的显式覆盖。
  Parallelization: Wave 3 | Blocked by: 17 | Blocks: 24,28 | Can parallelize with: 20
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:256-317,424-505；src/acp/nmr/models.py:163-198；docs gap G04 有效 solvent
  Acceptance criteria (agent-executable): `pytest tests/test_acp_workflows_nmr.py -q`；mock backend 断言 `solvent_model=none` 时 ORCA 输入无 cpcm
  QA scenarios (name the exact tool + invocation): happy = 显式 solvent_model 生效；failure = 气相配置不被覆盖为 chloroform，Evidence <attemptDir>/task-21-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): record effective config and honor solvent_model end to end

- [x] 22. 报告/图表 manifest 路径与存在性（含 plots/ 前缀）
  What to do / Must NOT do: 修 `workflows/nmr.py:1142-1187` 与 `write_result_summary`：product 与 manifest 路径用 `relative_to(storage.result_dir())` 生成，图表为 `reports/plots/{name}`；写入前校验文件存在（缺失则不注册或显式告警）；删除无意义 `try/except ValueError`（1157-1166）。Must NOT：硬拼 `reports/{plot.name}`；不得注册不存在的产物。
  Parallelization: Wave 3 | Blocked by: 20 | Blocks: 23,24,29 | Can parallelize with: 17
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:1110-1187；src/acp/nmr/report.py:156-166；src/acp/storage/manifest.py:98-150；docs gap §12.1 图表路径
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_report_paths.py`；每个 manifest product 的 `RESULT/<path>` 真实存在
  QA scenarios (name the exact tool + invocation): happy = 所有产物路径可打开；failure = 缺失文件不被静默注册，Evidence <attemptDir>/task-22-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): correct manifest/plot paths and verify existence

- [x] 23. 统一本地/远端报告读取 + 前端解包 + null 展示
  What to do / Must NOT do: 前端 `fetchNmrReportJson`（23254-23266）请求 `reports/nmr_report.json` 并对旧布局回退根路径；远端 preview 响应解包 `{content}`（或改用 `?mode=report` 读 `report`）；渲染处 `Number(x)||0` 改为缺值显示 `—`（winner 概率 23284、回归 23399-23401、boltzmann 23428）；统一读取服务封装本地/远端。Must NOT：把 null 显示为 0；不得只修本地路径。
  Parallelization: Wave 3 | Blocked by: 22 | Blocks: 29 | Can parallelize with: 24
  References (executor has NO interview context - be exhaustive): frontend/ACP_Workbench_v2.html:23254-23435,22912-22940；src/acp/api/v1_routes.py:5734-5754,5976-6094；docs gap G13
  Acceptance criteria (agent-executable): `pytest tests/test_frontend_sync.py tests/test_acp_api_v1.py -q`；远端 mock preview 返回可渲染报告
  QA scenarios (name the exact tool + invocation): happy = 本地/远端展示一致；failure = 缺报告显示 no_report 而非报错，Evidence <attemptDir>/task-23-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): unify report retrieval and render null states correctly

- [x] 24. 报告 schema v2：scaled_ppm/完整配置/概率状态/覆盖度/provenance + 旧报告兼容
  What to do / Must NOT do: `Assignment.as_dict` 增 `scaled_ppm`；报告顶层加 `schema_version=2`、完整有效配置、（D 期）`protocol_id`、逐候选 `analysis_status`/概率状态、`coverage`（expected/matched/selected/successful population）、校准 provenance；JSON 与 XLSX 数值一致；旧报告只读兼容并显示“历史报告：验证状态未知”。Must NOT：改变旧报告读取路径；不得用展示四舍五入值当重算源。
  Parallelization: Wave 3 | Blocked by: 10,21,22 | Blocks: 29,30 | Can parallelize with: 23
  References (executor has NO interview context - be exhaustive): src/acp/nmr/models.py:245-369；src/acp/nmr/report.py:30-165；docs gap §8.2/§12.1
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_report_schema.py`；schema v2 round-trip + 旧报告 fixture 读取不报错
  QA scenarios (name the exact tool + invocation): happy = v2 字段齐全且 JSON/XLSX 一致；failure = 旧报告 → 兼容且标注未知，Evidence <attemptDir>/task-24-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): emit versioned report schema with full provenance

- [x] 25. 图表与标定定义修正 + job_edit 字段覆盖 + catalog 帮助
  What to do / Must NOT do: `report.py` 残差图标签/符号统一为 Goodman `scaled-exp`、按核拆分有符号残差；拆分 R²（回归相关系数平方 vs 预测空间拟合优度）并修 `fit_scaling_goodman` 分子/分母定义（scaling.py:164-166）；`job_edit.py` nmr 可编辑字段同步新参数（functional/basis/solvent_model/ewin/max_conformers）；catalog NMR 字段 help 更新。Must NOT：改变拟合方向或概率；不得改 job_edit 的 review-only 语义。
  Parallelization: Wave 3 | Blocked by: 21 | Blocks: 29 | Can parallelize with: 23,24
  References (executor has NO interview context - be exhaustive): src/acp/nmr/report.py:87-153；src/acp/nmr/scaling.py:109-179；src/acp/scheduler/job_edit.py:638-700；tests/test_acp_job_edit.py:305-352
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_scaling.py tests/test_acp_job_edit.py -q`；R² 两定义可区分且图表标签为 `scaled-exp`
  QA scenarios (name the exact tool + invocation): happy = 新字段可编辑且 audit 全绿；failure = 未登记字段被 edit 校验拒绝并提示，Evidence <attemptDir>/task-25-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(nmr): correct residual/R2 definitions and sync edit coverage

- [x] 26. cccp `run_nmr_shielding` 执行核心：审计迁移落地或按冻结契约实现，并登记台账
  What to do / Must NOT do: 先按 todo 1 门结论核对 `.omo/plans/acp-cccp-architecture-remediation.md` todo 42/43 状态。若已落地：审计 `src/cccp/calculation/tasks/nmr_shielding.py` 满足 `NmrShieldingOptions`/`NmrShieldingPayload`（requests.py:1268-1293 / results.py:1036-1100）、`atom→{symbol,isotropic}` 键形、记录身份与失败语义，补 NMR 专项测试。若未落地：按迁移 todo 43 冻结契约实现该文件并 `tasks/__init__.py` re-export。两种情况都必须：把 `run_nmr_shielding` 加入 `tests/test_architecture_invariants.py::_PRIMITIVE_DEFS`（368-376）、更新 `migration_ledger.md:40` 当前实现驻点、`tests/test_cccp_task_nmr_shielding.py` 绿。Must NOT：在 `src/acp/calculations/primitives/` 新增执行体；`cccp` 不得导入 `acp`；不得与迁移 worker 并发写同一文件（见 todo 1）。
  Parallelization: Wave 4 | Blocked by: 1 | Blocks: 27,28,34,35 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): .omo/plans/acp-cccp-architecture-remediation.md:480-494；src/cccp/calculation/requests.py:1268-1293,1501-1509,1554-1568；src/cccp/calculation/results.py:1036-1100,1257-1271；src/cccp/calculation/selection.py:258；src/cccp/backends/matrix.py:250,277；tests/baseline/refactor-evidence/migration_ledger.md:40；tests/test_architecture_invariants.py:368-376,605-616
  Acceptance criteria (agent-executable): `pytest tests/test_cccp_task_nmr_shielding.py tests/test_architecture_invariants.py tests/test_cccp_calculation_contracts.py -q`；`grep -rnE "from acp|import acp" src/cccp/calculation/tasks` 无命中；台账 row 40 指向 `src/cccp/calculation/tasks/nmr_shielding.py`
  QA scenarios (name the exact tool + invocation): happy = 契约/台账/守护全绿；failure = 第二实现体或 acp 反向导入被守护测试拦截，Evidence <attemptDir>/task-26-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | feat(cccp): land the single nmr_shielding task execution core

- [x] 27. ACP NMR 改线消费 `run_nmr_shielding` 并同步 grep-gate
  What to do / Must NOT do: `workflows/nmr.py::_run_giao_for_conformers`（424-505）改调 `cccp.calculation.run_nmr_shielding`（保留 ACP 的 CREST→CENSO 编排与能窗/自由能/Boltzmann 归属）；删除 `get_backend("orca")` 直调；同步 `scripts/check_grep_gates.py`：`workflow_executes_qc` 的 allowlist 三元组 `("workflow_executes_qc","src/acp/workflows/nmr.py","get_backend")`（769）必须在同一 todo 删除（allowlist 只减不增），并核对是否已由迁移 todo 29 收口；加 NMR 集成测试。Must NOT：把 CREST→CENSO 整体塞进 cccp；不得改变化学位移/DP4/DP5 科学语义；不得重新引入 backend 直调。
  Parallelization: Wave 4 | Blocked by: 26 | Blocks: 28,34,35 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:29,424-505；src/cccp/calculation/tasks/nmr_shielding.py；scripts/check_grep_gates.py:169-174,404-409,769；tests/test_grep_gates_script.py；tests/test_acp_workflows_nmr.py
  Acceptance criteria (agent-executable): `pytest tests/test_acp_workflows_nmr.py tests/test_grep_gates_script.py tests/test_architecture_invariants.py -q`；`grep -nE "get_backend|nmr_shielding\(" src/acp/workflows/nmr.py` 无执行性直调
  QA scenarios (name the exact tool + invocation): happy = 改线后报告键形不变且门绿；failure = 残留 backend 直调或 allowlist 未删被门拦截，Evidence <attemptDir>/task-27-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | refactor(nmr): consume cccp nmr_shielding task and retire the direct backend call

- [x] 28. Shielding checkpoint 指纹 + 中断续算 + 有界并发/资源预算
  What to do / Must NOT do: 为每个构象 GIAO 结果建立指纹（几何哈希 + method/basis/solvent/solvent_model/nuclei/charge/multiplicity + 原子映射），落 CHECKPOINT；`--resume` 只重算缺失构象；方法/几何变化使缓存失效；`候选×构象×nproc` 设上限并显式预算（避免超卖）；复用现有 Job 生命周期，质量状态放分析结果内。Must NOT：无指纹复用过期屏蔽；不得引入新 JobStatus；不得用 `prebuilt_ensembles` 冒充完整用户级恢复协议。
  Parallelization: Wave 4 | Blocked by: 27 | Blocks: 30,34 | Can parallelize with: 29
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:424-505,1017-1053；src/acp/calculations/checkpoint.py；docs/ACP_Job_File_Layout_Spec.md；docs gap G15
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_resume.py`：中断第 N 构象后 resume 只补算缺失；方法变更后全部失效重算
  QA scenarios (name the exact tool + invocation): happy = 精确复用；failure = 几何/方法变化 → 指纹不匹配显式重算，Evidence <attemptDir>/task-28-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): per-conformer shielding checkpoints with bounded concurrency

- [x] 29. NmrProtocolSpec 六段 + 三模式 + unvalidated_protocol 判定
  What to do / Must NOT do: 新增 `NmrProtocolSpec`（sampling/geometry/population_energy/shielding/reference/statistical_model + 版本与指纹）与模式枚举 `exploratory|reference_validation|acp_calibrated`；记录各段实际执行结果；`validate_error_model_binding`（error_model.py:45-70）扩展为协议级校验，不匹配返回 `unvalidated_protocol`；TMS 来源（精确匹配/气相回退）与有效 solvent 统一保存；缺失参考不得用屏蔽值当位移。Must NOT：只按 method/basis/model 名称判定整体适用；不得把“调用 CENSO”写成“已满足 DFT 优化几何”。
  Parallelization: Wave 4 | Blocked by: 27 | Blocks: 30,36,37 | Can parallelize with: 28
  References (executor has NO interview context - be exhaustive): src/acp/nmr/error_model.py:42-70；src/acp/workflows/nmr.py:256-317；src/cccp/qc/interfaces/censo.py CENSO_PRESETS；docs gap G04/§8.1
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_protocol_spec.py`；不匹配协议 → `calibration_status=="unvalidated_protocol"`；每候选协议指纹可重算
  QA scenarios (name the exact tool + invocation): happy = 三模式状态确定；failure = censo-light 未含 optimization 时输出 `exploratory` 而非校准声明，Evidence <attemptDir>/task-29-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): add six-part protocol spec with calibration modes

- [x] 30. Ensemble 质量门：真实布居/权重重算/完整性/敏感性
  What to do / Must NOT do: `_select_conformers`（375-421）在同一 ensemble 内强制统一能量定义/层级（不 G/E 混用、不把缺失当 0）；记录 preselection/selected/successful 布居、剔除原因、最终权重；累计布居门 + 资源硬上限（超限显示未覆盖质量）；每个构象先验收所需核完整性（不再按原子选不同子集，修 averaging.py:63-76 逐原子重归一化）；leave-one-conformer-out + 温度/权重敏感性标记需复核结论；报告权重=实际算法输入。Must NOT：主导构象失败仍输出正常结论；不得把相对已发现 ensemble 覆盖度当真实溶液覆盖度。
  Parallelization: Wave 4 | Blocked by: 28,29 | Blocks: 34,35,36 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:375-421,424-505,1017-1053；src/acp/nmr/averaging.py:24-142；src/acp/nmr/models.py:203-224；docs gap G09
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_ensemble_quality.py`；大布居构象失败触发质量降级；低权重尾部失败与高权重失败可区分；权重可重算
  QA scenarios (name the exact tool + invocation): happy = 报告权重与算法输入一致；failure = 部分屏蔽缺失 → 显式排除该构象而非逐原子漂移，Evidence <attemptDir>/task-30-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): record and enforce ensemble quality and population evidence

- [x] 31. reference_validation 模式：固定上游设置与参考数据并列
  What to do / Must NOT do: 固化选定论文/SI 的 opt/SP 方法与设置（从现有 DevDoc §8.0 与资产 NOTICE 归档，不照抄冲突的旧描述）；reference_validation 模式输出原始参考数据与 ACP ORCA 迁移结果并列；资产哈希与协议元数据记录；可复现校验脚本。Must NOT：仅凭 profile 名称称完全复现；不得在缺失参考时静默用屏蔽值。
  Parallelization: Wave 4 | Blocked by: 29 | Blocks: 36,37 | Can parallelize with: 28,30
  References (executor has NO interview context - be exhaustive): src/acp/nmr/models/NOTICE.md；docs/ACP_NMR_DP4_DevDoc.md:404-424；docs gap §10.3 试验 A/G04
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_reference_validation.py`；固定结构上参考/迁移结果并排且差异可计算
  QA scenarios (name the exact tool + invocation): happy = 参考数据缺省时明确报缺；failure = 设置冲突 → 显式拒绝并列出差异，Evidence <attemptDir>/task-31-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): reference-validation mode with pinned upstream settings

- [x] 32. 依赖与打包：requirements-node、openpyxl、wheel 资产与能力探测
  What to do / Must NOT do: 同步 `requirements-node.txt` 与 `pyproject.toml` 核心依赖（补 `scipy>=1.10`、`matplotlib>=3.7`；二者已在本机 acp 核心依赖但节点文件漂移，影响所有远端 scipy 工作流）；决定并声明 XLSX 依赖（`openpyxl` 加入合适 extra 或主依赖）；Bruker/FCHL 能力（nmrglue/qml）提前检测与明确报错；验证 wheel 可加载 `acp/nmr/models/` 资产（package-data）。Must NOT：降低全平台 NumPy 版本；不得让 XLSX 静默跳过而无能力声明。
  Parallelization: Wave 4 | Blocked by: 1 | Blocks: 36 | Can parallelize with: 28,29,30,31
  References (executor has NO interview context - be exhaustive): requirements-node.txt:1-22；pyproject.toml:24-61；src/acp/nmr/report.py:44-48；src/acp/nmr/fchl.py:163-183；root AGENTS.md（依赖同步规则）
  Acceptance criteria (agent-executable): `pip wheel --no-deps -w /tmp/acp-wheel . && python -c "import zipfile,glob; w=glob.glob('/tmp/acp-wheel/acp-*.whl')[0]; z=zipfile.ZipFile(w); assert any('acp/nmr/models/atomic_reps.gz' in n for n in z.namelist())"`；`pytest tests/test_acp_nmr_spectra.py -q`（缺 nmrglue 时明确 skip/报错）
  QA scenarios (name the exact tool + invocation): happy = wheel 含资产且节点依赖一致；failure = 缺能力在提交前明确报错，Evidence <attemptDir>/task-32-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | fix(packaging): sync node deps and ship NMR model assets

- [x] 33. D 收口：迁移台账/调用矩阵/DevDoc 同步
  What to do / Must NOT do: 更新 `migration_ledger.md:40` 最终驻点与验收测试；产出/更新 `docs/ACP_CCCP_Call_Matrix.md` 中 nmr 行（唯一任务入口）；在 `docs/ACP_NMR_DP4_DevDoc.md` 记录 D 阶段真实执行的优化/权重/solvent 证据。Must NOT：只改代码不改台账；不得把未执行的校准写成已证明。
  Parallelization: Wave 4 | Blocked by: 26,27,30,31 | Blocks: 36 | Can parallelize with: 32
  References (executor has NO interview context - be exhaustive): tests/baseline/refactor-evidence/migration_ledger.md:40；.omo/plans/acp-cccp-architecture-remediation.md:433-435；docs/ACP_NMR_DP4_DevDoc.md:180-284
  Acceptance criteria (agent-executable): `pytest tests/test_architecture_invariants.py tests/test_acp_workflow_task_matrix.py -q`；grep 台账 row 40 含 `src/cccp/calculation/tasks/nmr_shielding.py`
  QA scenarios (name the exact tool + invocation): happy = 台账/矩阵/文档一致；failure = 台账与实现驻点不符被 `test_current_station_ledger_matches_implementation_sites` 拦截，Evidence <attemptDir>/task-33-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | docs(nmr): sync migration ledger and call matrix

- [x] 34. SignalGroup 契约贯穿平均→归属→缩放→DP5
  What to do / Must NOT do: 在 `models.py` 新增 frozen `SignalGroup(atom_uids, coefficients, equivalence_basis, peak_capacity, experiment_ref)`；`averaging.boltzmann_average_shieldings` 输出组（含成员列表，不只代表）；`assignment`/`scaling`/`CandidateResult` 携带 SignalGroup；`nmr/__init__` 导出。Must NOT：只保留代表原子标签；不得仅为实现方便平均 descriptor。
  Parallelization: Wave 5 | Blocked by: 26,30 | Blocks: 35,37,39 | Can parallelize with: 36
  References (executor has NO interview context - be exhaustive): src/acp/nmr/averaging.py:78-142；src/acp/nmr/assignment.py；src/acp/nmr/scaling.py:85-106；src/acp/nmr/models.py:226-263；docs gap G08/§8.1
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_signal_group.py`；显式 EQ 组在单构象屏蔽不等时 DP4/DP5 共享同一信号定义；交换代表原子结果不变
  QA scenarios (name the exact tool + invocation): happy = 组语义一致；failure = 非等价峰借 EQ 抵消残差被检测，Evidence <attemptDir>/task-34-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): thread SignalGroup through averaging, assignment and DP5

- [x] 35. DP5 构象级重建使用 SignalGroup（与参考/校准一致）
  What to do / Must NOT do: `_compute_candidate_dp5` 从 SignalGroup 成员重建每构象信号（不再代表标签反查原子，修 workflows/nmr.py:686-696）；多原子环境如何汇总（系数/权重）与固定参考方法或独立校准一致；等长/缺失显式处理。Must NOT：用代表原子原始屏蔽替代组成均值；不得改变 Goodman KDE/Bayes 语义。
  Parallelization: Wave 5 | Blocked by: 34 | Blocks: 37,39 | Can parallelize with: 36
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:653-766；src/acp/nmr/error_model.py:347-473；docs gap G08
  Acceptance criteria (agent-executable): `pytest tests/test_acp_nmr_signal_group.py tests/test_acp_nmr_probability_state.py -q`；代表原子替换前后 DP5 概率一致
  QA scenarios (name the exact tool + invocation): happy = 组平均正确进入 DP5；failure = 缺失成员 → 类型化降级而非静默，Evidence <attemptDir>/task-35-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): reconstruct DP5 signals from SignalGroup membership

- [x] 36. FCHL 分层 golden：descriptor→kernel→加权 KDE→候选概率
  What to do / Must NOT do: 针对 `dp5@b6cf559007a5d13fe79654f37daf945ee1661a23` 建立分层对照 fixture：FCHL descriptor、kernel（numpy 与可用的 qml）、加权 KDE、候选概率；容差先测真实核重复性与分层误差再冻结（禁止为最终概率接近而放宽分层），记录测量过程。Must NOT：用 stub qml 冒充真实编译核等价；不得只对照最终概率。
  Parallelization: Wave 5 | Blocked by: 34 | Blocks: 37,38,43 | Can parallelize with: 35
  References (executor has NO interview context - be exhaustive): src/acp/nmr/fchl.py:539-783；src/acp/nmr/error_model.py:197-473；src/acp/nmr/models/NOTICE.md；tests/test_acp_nmr_fchl.py；docs gap §10.2/§10.3 试验 A
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_fchl_golden.py`（分层断言 + 冻结容差文件）；每层差异可定位
  QA scenarios (name the exact tool + invocation): happy = 分层 golden 通过；failure = 某层超出冻结容差 → 明确失败不被最终概率掩盖，Evidence <attemptDir>/task-36-acp-nmr-goodman-gap-remediation.json
  Commit: Y | test(nmr): layered FCHL golden parity fixtures

- [x] 37. FCHL ≥86 原子碎片路径接入
  What to do / Must NOT do: 实现 openbabel radius-3 碎片化并接上 `frag_reps.gz`（替代当前 ≥86 强制 fallback，修 fchl.py:39/183-193 与 workflows/nmr.py:702-706）；验证碎片与残差训练索引对应（不得把原子数组直接换碎片数组）；85/86 边界测试；资源限制。Must NOT：在缺 openbabel 时静默通过；不得声称碎片与原子路径数值等价。
  Parallelization: Wave 5 | Blocked by: 35,36 | Blocks: 38,39 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/fchl.py:39,134-200,539-783；src/acp/workflows/nmr.py:700-766；pyproject.toml:42-50（openbabel/nmr extra）；docs gap G12
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_fchl_fragments.py`；85 原子走原子路径、86 走碎片路径（或明确 unavailable）；至少一个真实碎片案例
  QA scenarios (name the exact tool + invocation): happy = 边界与索引对应正确；failure = openbabel 缺失 → 显式 unavailable 而非 fallback 冒充，Evidence <attemptDir>/task-37-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): wire the >=86-atom FCHL fragment path

- [x] 38. FCHL 可用性/域外状态、近邻支持度、核分块缓存 + CI 组合
  What to do / Must NOT do: 明确 `qml`/numpy/off 三态与域外提示；输出近邻有效样本数与支持度；kernel 分块与缓存；CI/文档覆盖纯 NumPy 与真实 QML 可安装组合；weighted 与 unweighted 分别记录校准状态。Must NOT：把“无 QML 就完全不能跑”写成事实（numpy 核存在但 opt-in）；不得让域外样本静默给出正式概率。
  Parallelization: Wave 5 | Blocked by: 37 | Blocks: 39,43 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/fchl.py:678-783；src/acp/nmr/error_model.py:252-264,508-521；pyproject.toml:45-49；docs gap G12
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_fchl_availability.py`；三态可区分、支持度随域外下降并触发提示；资源预算测试
  QA scenarios (name the exact tool + invocation): happy = 三态与缓存正确；failure = 域外样本标 `out_of_domain` 不输出正式概率，Evidence <attemptDir>/task-38-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): expose FCHL availability, neighbor support and domain limits

- [x] 39. 原子级/信号级诊断与逐核 DP4 分项
  What to do / Must NOT do: 输出 `dp4_13c/dp4_1h/dp4_combined`、逐原子/信号残差与 DP5 诊断、相似训练邻域支持度、去掉单个信号的排名变化、候选间冲突核；区分“原子风险指标”与“经分子级校准的正确概率”。Must NOT：只返回候选 float；不得混用风险指标与校准概率名称。
  Parallelization: Wave 5 | Blocked by: 34,35,37,38 | Blocks: 40,43 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/error_model.py:274-345；src/acp/nmr/probability.py:25-122；src/acp/nmr/models.py:285-369；docs gap G16
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_atomic_diagnostics.py`；诊断字段齐全且 leave-one-signal-out 可复算
  QA scenarios (name the exact tool + invocation): happy = 诊断可定位问题核/原子；failure = 缺诊断字段被 schema 拒绝，Evidence <attemptDir>/task-39-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): atomic and per-nucleus probability diagnostics

- [x] 40. 前端结构原子↔归属行↔实验峰联动 + i18n
  What to do / Must NOT do: 在 NMR 报告面板增加结构原子 ↔ 归属行 ↔ 实验峰联动（复用已有结构查看器 framing 约定），展示逐候选质量状态/诊断；新增 i18n 键 zh+en 成对；复用 `TrajectoryFrame`/`VIEW_REGISTRY` 契约（若涉投影）。Must NOT：硬编码英文标签；不得绕过 frames 契约发视图投影。
  Parallelization: Wave 5 | Blocked by: 39 | Blocks: 43 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): frontend/ACP_Workbench_v2.html:6650-6664,8200-8214,23208-23435；src/acp/results/frames.py；tests/test_frontend_sync.py；root AGENTS.md ANTI-PATTERN 21
  Acceptance criteria (agent-executable): `pytest tests/test_frontend_sync.py -q` + 新联动断言；zh/en 键成对存在
  QA scenarios (name the exact tool + invocation): happy = 联动可用且双语；failure = 缺 i18n 键被 sync 测试拦截，Evidence <attemptDir>/task-40-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | feat(nmr): link structural atoms, assignments and peaks in the viewer

- [x] 41. 四层谱模型与处理 provenance/质量指标
  What to do / Must NOT do: 在 `spectra.py`/`models.py` 区分采集数据（acquisition）、处理谱、谱线（lines）、共振信号（resonances）四层；保存数字滤波/相位/基线/参考参数与质量指标；`ProcessedSpectrum` 扩展为显式层，`ExperimentalNmr` 保持向后兼容读取路径。Must NOT：把未归属原始峰直接等同 resonance；不得破坏现有 `bruker_peaks.txt` 消费者。
  Parallelization: Wave 6 | Blocked by: 22 | Blocks: 42,43,44,45 | Can parallelize with: 46
  References (executor has NO interview context - be exhaustive): src/acp/nmr/spectra.py:53-80,325-512；src/acp/nmr/models.py:120-161；docs gap G10
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_spectra_layers.py`；四层对象 round-trip 且 provenance 完整
  QA scenarios (name the exact tool + invocation): happy = 层边界清晰、旧格式仍可解析；failure = 缺 provenance 字段显式报错，Evidence <attemptDir>/task-41-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): four-layer spectrum model with processing provenance

- [x] 42. 相位/数字滤波/参考质量门（失败不再静默继续）
  What to do / Must NOT do: `_auto_phase`（195-210）失败时置质量失败/异常（不再用未相位谱继续）；数字滤波 group delay/滤波补偿专项验证（真实仪器数据 fixture）；参考峰缺失时显式降级标记而非静默；质量指标进入报告。Must NOT：相位失败继续正式概率；不得把未验证的滤波器影响当无影响。
  Parallelization: Wave 6 | Blocked by: 41 | Blocks: 43,44,45 | Can parallelize with: 46
  References (executor has NO interview context - be exhaustive): src/acp/nmr/spectra.py:173-289；docs gap G10/§15
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_spectra_quality.py`；相位失败/参考缺失各有确定状态；group delay fixture 通过
  QA scenarios (name the exact tool + invocation): happy = 质量门生效；failure = 相位失败 → `processing_quality=failed` 且不进入正式概率，Evidence <attemptDir>/task-42-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): gate phase/digital-filter/reference failures explicitly

- [x] 43. 碳谱处理器：峰拟合 + 溶剂排除 + resonance 归并
  What to do / Must NOT do: 新增碳谱处理器（峰型/曲线拟合、溶剂大峰排除、裂分/相邻线归并为 resonance），替代仅局部极大值；保留曲线与区域供人工调整；processor 注册到核种映射。Must NOT：仅用 `find_peaks` 与中点积分冒充解析；不得盲目把同核多实验峰拼接。
  Parallelization: Wave 6 | Blocked by: 42 | Blocks: 45,48 | Can parallelize with: 44
  References (executor has NO interview context - be exhaustive): src/acp/nmr/spectra.py:241-322,483-512；官方 Carbon_processing.py；docs gap G10
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_carbon_processor.py`；已知真实碳谱 fixture 的 resonance 与人工标注对齐（精确率/召回率指标可计算）
  QA scenarios (name the exact tool + invocation): happy = 溶剂峰被排除、裂分归并；failure = 低 SNR/重叠 → 标记不确定区域，Evidence <attemptDir>/task-43-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): carbon spectrum processor with fitting and solvent handling

- [x] 44. 氢谱处理器：multiplet 分组 + 总氢/甲基约束 + 重叠不确定性
  What to do / Must NOT do: 新增氢谱处理器：BIC/峰型驱动 multiplet 分组、结构相关总氢数与甲基约束、重叠区不确定性；积分与进样约束进入 assignment 的峰容量。Must NOT：仅按相邻峰中点给 multiplicity；不得忽略分子总氢数约束。
  Parallelization: Wave 6 | Blocked by: 42 | Blocks: 45,48 | Can parallelize with: 43
  References (executor has NO interview context - be exhaustive): src/acp/nmr/spectra.py:292-322；官方 Proton_processing.py；docs gap G10
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_proton_processor.py`；真实氢谱 fixture multiplet 分组与人工标注对比指标
  QA scenarios (name the exact tool + invocation): happy = 甲基/重叠正确分组；failure = 低 SNR/杂质 → 不确定标记，Evidence <attemptDir>/task-44-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): proton spectrum processor with multiplet grouping

- [x] 45. 多实验选择、2D `ser` 拒收、核种处理器注册
  What to do / Must NOT do: `process_bruker_tree`（483-512）改为按核种选择/组合用户指定实验（不再把同核多实验峰 `extend` 拼接）；检测 2D `ser`（或非 1D）明确拒收；建立 per-nucleus processor registry。Must NOT：盲目拼接同核实验；不得把 2D 数据按 1D 处理。
  Parallelization: Wave 6 | Blocked by: 43,44 | Blocks: 48 | Can parallelize with: 46
  References (executor has NO interview context - be exhaustive): src/acp/nmr/spectra.py:429-533；docs gap G10
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_spectra_selection.py`；多实验按选择生效；2D ser → 明确 errors
  QA scenarios (name the exact tool + invocation): happy = 实验选择可控；failure = 2D/未知核种 → 类型化拒绝，Evidence <attemptDir>/task-45-acp-nmr-goodman-gap-remediation.json
  Commit: Y | fix(nmr): select experiments explicitly and reject 2D input

- [x] 46. 迭代归属：初标定启动 + 交替归属/标定收敛 + 近优解
  What to do / Must NOT do: 归属与内部标定交替迭代至收敛（外部初标定启动），峰容量/等价组原子数/重叠/不匹配/交换性氢显式建模；保存若干近优归属及差值；防过拟合（不得对每候选过度自由重排后无校准套用原概率）。Must NOT：一次绝对差 Hungarian；不得把优化目标变小当作化学正确。
  Parallelization: Wave 6 | Blocked by: 24,41 | Blocks: 48 | Can parallelize with: 43,44
  References (executor has NO interview context - be exhaustive): src/acp/nmr/assignment.py；官方 Carbon_assignment.py/Proton_assignment.py；docs gap G11
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_iterative_assignment.py`；整体偏移/近重叠/甲基/缺峰/错误候选反例均有确定结果
  QA scenarios (name the exact tool + invocation): happy = 迭代收敛且近优解可见；failure = 过度自由重排触发防过拟合护栏，Evidence <attemptDir>/task-46-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): iterative assignment with calibrated convergence

- [x] 47. AnalysisRevision：谱峰人工修订只重算分析（不重跑 QC）
  What to do / Must NOT do: 新增 `AnalysisRevision`（原始证据哈希 + 实验峰修订 + 协议/模型版本 → 分析结果）；提供人工修订入口，重算仅走纯分析（复用缓存屏蔽）；旧报告保留不被覆盖；需人工审阅时用现有 `WAITING_REVIEW` review-only 语义。Must NOT：修订触发 QC 重跑；不得新增与现有生命周期冲突的恢复入口。
  Parallelization: Wave 6 | Blocked by: 24,28,46 | Blocks: 48 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/workflows/nmr.py:838-1210；src/acp/scheduler/jobs.py PAUSED/review 语义；docs gap §8.1/G10
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_analysis_revision.py`；改峰后重算不产生新 QC 日志（spy 断言）
  QA scenarios (name the exact tool + invocation): happy = 修订后统计更新且旧报告保留；failure = 修订哈希不匹配 → 显式失效，Evidence <attemptDir>/task-47-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): revision-scoped recomputation without QC rerun

- [x] 48. 有标注谱处理 fixtures + 精确率/召回率 harness 接口
  What to do / Must NOT do: 建立合成 + 小规模真实 Bruker 的标注 fixtures（相位偏差/数字滤波/溶剂大峰/杂质/低 SNR/重叠 multiplet/同核重复实验/2D），输出谱处理与归属的精确率/召回率接口（供 Wave 7 harness 消费）。Must NOT：只用合成单峰代表自动化验收；不得把 fixtures 当长期证据存储（放 `tests/fixtures/`）。
  Parallelization: Wave 6 | Blocked by: 43,44,45,46,47 | Blocks: 49,51 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): tests/fixtures/；tests/test_acp_nmr_spectra.py；docs gap §10.2/§10.3 第 4 层
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_spectra_benchmark.py`；接口对标注 fixtures 产出指标 JSON
  QA scenarios (name the exact tool + invocation): happy = 指标可计算且稳定；failure = 无标注 → 明确跳过并标记 NOT_VERIFIED，Evidence <attemptDir>/task-48-acp-nmr-goodman-gap-remediation.json
  Commit: Y | test(nmr): labeled spectra fixtures and processing metrics hooks

- [x] 49. 真实二进制门控：新增 requires_censo + CONFSEARCH_*_PATH 解析 + skip=NOT_VERIFIED 三态
  What to do / Must NOT do: 在 `tests/conftest.py` 增 `requires_censo`（照 `requires_orca/crest/xtb/isostat/shermo` 模式，`shutil.which` + `CONFSEARCH_CENSO_PATH`/`CONFSEARCH_<NAME>_PATH` 覆盖）；文档化运行真实 QC 前需 export 路径（本机二进制经 `~/.cccp.yaml` 配置而非 PATH）；新增三态证据规则：真实 QC 测试 skip 记 `NOT_VERIFIED`（禁止当绿）；若新 marker 需在 `pyproject.toml` 注册则同步。Must NOT：引用不存在的 `requires_censo`（当前 conftest 无此对象）；不得让 skip 静默通过验收。
  Parallelization: Wave 7 | Blocked by: 1 | Blocks: 51,54 | Can parallelize with: 50
  References (executor has NO interview context - be exhaustive): tests/conftest.py:1-129；tests/AGENTS.md:25,43；tests/test_cccp_real_qc_smoke.py:53-72；pyproject.toml:70-80；/home/<user>/.cccp.yaml（本机路径）
  Acceptance criteria (agent-executable): `pytest --collect-only -q tests/test_acp_nmr_qc_smoke.py` 无 ImportError；`requires_censo` 存在；skip 情形输出 `NOT_VERIFIED` 标识
  QA scenarios (name the exact tool + invocation): happy = 有 CENSO 时执行、无则 NOT_VERIFIED；failure = 直接把 skip 当通过被规则拦截，Evidence <attemptDir>/task-49-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | test(nmr): add censo gating and NOT_VERIFIED three-state rule

- [x] 50. 基准 harness：数据集 manifest schema + 分层指标 + provenance + 预注册阈值
  What to do / Must NOT do: 新增 `scripts/nmr_benchmark.py` + `tests/benchmark/nmr/` harness：数据集 manifest schema（来源/许可/哈希/版本）、指标（分核 MAE/RMSE、归属正确率、Top-1、真结构缺失行为、DP5 正负例 Brier/log-loss 与校准图、失败/拒答率、CPU/内存）、按分子/候选组聚类的配对置信区间、结果前冻结的阈值文件；对合成 fixtures 自测。Must NOT：把同一分子的多原子/多构象当独立样本；不得看结果后改阈值；不得把平衡测试集当现实分布。
  Parallelization: Wave 7 | Blocked by: 39,48 | Blocks: 51,52,53,54 | Can parallelize with: 49
  References (executor has NO interview context - be exhaustive): docs gap §10.3；docs/ACP_NMR_DP4_DevDoc.md:820-850；tests/baseline/（goldens 约定）
  Acceptance criteria (agent-executable): `pytest tests/benchmark/nmr/test_harness.py -q`；合成数据产出的指标 JSON 符合 schema 且可重算
  QA scenarios (name the exact tool + invocation): happy = harness 端到端跑通；failure = 缺 provenance/许可的数据集被 schema 拒绝，Evidence <attemptDir>/task-50-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): benchmark harness with provenance and pre-registered thresholds

- [x] 51. Level-1 真实 QC 冒烟（3–5 小分子，仅连通性/解析）
  What to do / Must NOT do: 用固定 3–5 个小分子（含刚性/柔性、H/C、多构象；建议 ethane/ethanol、n-butane、toluene 等可在本机 ORCA/CREST/CENSO 跑的体系）跑真实 GIAO 链路，验证连通性与解析；证据 JSON/日志落 `.omo/evidence/`；使用 todo 49 门控；结论只声明连通性。Must NOT：据此宣称准确率/校准；不得 mock 冒充真实运行。
  Parallelization: Wave 7 | Blocked by: 49,50 | Blocks: 52,53,54 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): docs gap §10.3 第 1 层；tests/test_cccp_real_qc_smoke.py 三态约定；/home/<user>/.cccp.yaml
  Acceptance criteria (agent-executable): `CONFSEARCH_ORCA_PATH=... pytest --run-slow --run-integration tests/test_acp_nmr_qc_smoke.py -q`；每分子证据含 ORCA 日志路径与解析计数；skip 情形记 NOT_VERIFIED
  QA scenarios (name the exact tool + invocation): happy = 真实链路产物齐全；failure = 缺二进制 → NOT_VERIFIED（不记绿），Evidence <attemptDir>/task-51-acp-nmr-goodman-gap-remediation.json
  Commit: Y | test(nmr): level-1 real-QC smoke evidence

- [x] 52. 协议转移 A/B harness（隔离几何/能量/溶剂/引擎/归属影响）
  What to do / Must NOT do: 在固定候选集与实验谱上分别切换几何优化、能量层级、溶剂模型、QC 引擎、自动归属策略，输出影响表；用合成/mock 自测，真实运行给 runbook。Must NOT：一次改动所有设置；不得把一次成功运行当模型适用性证明。
  Parallelization: Wave 7 | Blocked by: 50,51 | Blocks: 54 | Can parallelize with: 53
  References (executor has NO interview context - be exhaustive): docs gap §10.3 试验 B；src/acp/nmr/method_config.py；src/cccp/calculation
  Acceptance criteria (agent-executable): 新 `tests/benchmark/nmr/test_protocol_transfer.py`；每次只变一项且影响表可复算
  QA scenarios (name the exact tool + invocation): happy = 单变量对照可跑；failure = 多变量同时变被 harness 拒绝，Evidence <attemptDir>/task-52-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): protocol-transfer A/B experiment harness

- [x] 53. 数据分层 2–5 loader + 获取/运行 runbook（战役不在本计划）
  What to do / Must NOT do: 实现已归属统计集/立体化学集/原始谱集/边界集的 loader 与 schema；提供数据获取与运行 runbook（含许可/来源/哈希要求）；loader 用小型已提交 fixture 自测。实际外部数据集战役、准确率声明明确不在本计划（写入文档为后续计划）。Must NOT：伪造任何数据集或指标；不得联网抓取未授权数据。
  Parallelization: Wave 7 | Blocked by: 50 | Blocks: 54 | Can parallelize with: 52
  References (executor has NO interview context - be exhaustive): docs gap §10.3 第 2–5 层；docs gap §12.2 参考索引
  Acceptance criteria (agent-executable): 新 `tests/benchmark/nmr/test_dataset_loaders.py`；runbook 文件存在且列出每层字段与来源要求
  QA scenarios (name the exact tool + invocation): happy = loader 解析小 fixture；failure = 缺许可/哈希 → 拒绝加载，Evidence <attemptDir>/task-53-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): dataset loaders and acquisition runbook for benchmark layers 2-5

- [x] 54. reference_validation 对照与可复现校验
  What to do / Must NOT do: 把 todo 31 的 reference_validation 与 todo 36 的分层 golden 接入 harness，输出原始参考数据与 ACP ORCA 迁移结果并列及差异；提供可复现校验脚本与资产哈希清单。Must NOT：只凭 profile 名称称复现；不得在参考缺失时给出正式校准状态。
  Parallelization: Wave 7 | Blocked by: 31,36,49,52,53 | Blocks: 55,56 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): src/acp/nmr/models/NOTICE.md；docs gap §10.3 试验 A；tests/test_acp_nmr_fchl_golden.py
  Acceptance criteria (agent-executable): `pytest tests/benchmark/nmr/test_reference_validation.py -q`；差异表与哈希清单产出
  QA scenarios (name the exact tool + invocation): happy = 参考/迁移并排可复现；failure = 哈希不符 → 拒绝复用，Evidence <attemptDir>/task-54-acp-nmr-goodman-gap-remediation.json
  Commit: Y | test(nmr): reference-validation comparison and reproducibility receipts

- [x] 55. ShiftPredictor 协议 + 注册表 + provenance（不替换 DP5）
  What to do / Must NOT do: 定义 `ShiftPredictor` 协议：按原子映射输出位移、预测分布、几何要求、模型 provenance（model_id/version/calibrator）；独立注册表与模型 ID/校准器，与 GIAO/Goodman Gaussian 误差模型分离；导出并文档化。Must NOT：把神经网络预测残差送入现有 Gaussian/GIAO 误差模型；不得因速度替换计算链。
  Parallelization: Wave 8 | Blocked by: 54 | Blocks: 56,57 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): docs gap §7/G17；docs gap §12.2 DP5q 参考；src/acp/nmr/error_model.py（误差模型边界）
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_shift_predictor.py`；协议契约测试（stub 实现满足接口）且 `grep -rn "GaussianErrorModel" <predictor>` 无耦合
  QA scenarios (name the exact tool + invocation): happy = 协议/注册/校准状态独立；failure = 预测器写入 DP5 probability 被隔离守护拦截，Evidence <attemptDir>/task-55-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): add ShiftPredictor protocol with isolated provenance

- [x] 56. 隔离的 DP5q stub 适配器 + 不替换守护
  What to do / Must NOT do: 提供可选依赖下的 stub 适配器（可加载但不训练），模型/版本/校准字段独立；守护测试确保无 `run_nmr_shielding` 或 DP5 路径被静默接管。Must NOT：训练 GNN；不得设为默认路径。
  Parallelization: Wave 8 | Blocked by: 55 | Blocks: 57 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): docs gap §7；src/cccp/calculation/selection.py（能力选择边界）
  Acceptance criteria (agent-executable): 新 `tests/test_acp_nmr_dp5q_isolation.py`；未安装可选依赖时明确 unavailable；默认配置下 DP5 行为不变
  QA scenarios (name the exact tool + invocation): happy = stub 可调用且不污染正式路径；failure = 默认接管被守护拦截，Evidence <attemptDir>/task-56-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): isolated DP5q stub adapter with no-replacement guard

- [x] 57. DP5q 评测挂钩：筛选/优先级 + 域外升级 + 漏检风险指标
  What to do / Must NOT do: 将 ShiftPredictor 接入评测框架（todo 50/54），用途定位为候选筛选/优先级安排；困难/域外候选升级 DFT；用独立测试集量化筛选漏掉真结构的风险；输出速度/准确率权衡。Must NOT：声明 DP5q 与 DP5 等价；不得在无独立测试集时给结论。
  Parallelization: Wave 8 | Blocked by: 54,55,56 | Blocks: 58 | Can parallelize with: -
  References (executor has NO interview context - be exhaustive): docs gap §7/G17；tests/benchmark/nmr/（todo 50）
  Acceptance criteria (agent-executable): 新 `tests/benchmark/nmr/test_shift_predictor_eval.py`；筛选漏检风险指标可计算
  QA scenarios (name the exact tool + invocation): happy = 挂钩产出权衡表；failure = 无测试集 → NOT_VERIFIED，Evidence <attemptDir>/task-57-acp-nmr-goodman-gap-remediation.json
  Commit: Y | feat(nmr): DP5q screening hooks with miss-risk metrics

- [x] 58. 文档更正：ACP_NMR_DP4_DevDoc §11 全表 + AGENTS 更新
  What to do / Must NOT do: 按 gap 文档 §11 表格逐项更正 `docs/ACP_NMR_DP4_DevDoc.md` 旧表述（CENSO 更强→协议待验证、CENSO 满足 DFT 几何→区分 light/default/zero、泛函同即系统误差消除→仍需验证、Goodman 全归属→DP4-AI 已自动、DP4 只用 Gaussian→固定上游 Python 默认、DP5 书目年份、0.025 带宽语义、FCHL 完成≠完整等价、报告字段完整→补齐、NMR 一定在 head→按实际调度命令）；同步 `src/acp/nmr/AGENTS.md`/`src/acp/workflows/AGENTS.md` 的 NMR 条目与任务矩阵。Must NOT：把未执行的校准写成已证明；不得删除历史审计痕迹。
  Parallelization: Wave 8 | Blocked by: 60 | Blocks: none | Can parallelize with: 59
  References (executor has NO interview context - be exhaustive): docs/ACP_NMR_DP4_DevDoc.md；docs gap §11；src/acp/nmr/AGENTS.md；src/acp/workflows/AGENTS.md；root AGENTS.md
  Acceptance criteria (agent-executable): `grep -c` 校验 §11 每条已更新；`pytest tests/test_frontend_sync.py -q`（文档不破坏同步测试）
  QA scenarios (name the exact tool + invocation): happy = 表述与实测一致；failure = 残留“已证明”措辞被审计标记，Evidence <attemptDir>/task-58-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | docs(nmr): correct DevDoc claims and refresh AGENTS knowledge

- [x] 59. G01–G17 覆盖台账 + 证据索引（无孤儿缺口）
  What to do / Must NOT do: 产出 `.omo/evidence/acp-nmr-goodman-gap-remediation/GAP_COVERAGE.md`：每个 G01–G17 → 任务号 + 测试 + 证据文件 + 状态（fixed/validated-harness/deferred-out-of-scope）；汇总全部 evidence task-N 文件索引。Must NOT：把 deferred（外部战役/DP5q 训练）写成 fixed；不得遗漏任一 G。
  Parallelization: Wave 8 | Blocked by: 2-57 | Blocks: none | Can parallelize with: 58
  References (executor has NO interview context - be exhaustive): docs/ACP_NMR_Goodman_Gap_Investigation_2026-10-05.md §4/§5–§7；.omo/evidence/acp-nmr-goodman-gap-remediation/
  Acceptance criteria (agent-executable): `test -s .omo/evidence/acp-nmr-goodman-gap-remediation/GAP_COVERAGE.md`；脚本断言 G01–G17 每行恰映射 ≥1 任务
  QA scenarios (name the exact tool + invocation): happy = 覆盖完整；failure = 缺任一 G → 脚本非零退出，Evidence <attemptDir>/task-59-acp-nmr-goodman-gap-remediation.md
  Commit: N | docs(nmr): gap coverage ledger and evidence index

- [x] 60. 全量回归 + 打包 + 陈旧引用清扫
  What to do / Must NOT do: 跑 `pytest -m "not slow" -q`（全量非慢）、`pytest --run-slow tests/test_acp_nmr_*.py tests/test_acp_workflows_nmr.py -q`、`ruff check src tests && ruff format --check src tests`；wheel 资产检查（todo 32）；扫描并清除陈旧引用（旧报告根路径、`hmr_method` 死键、dead allowlist、未注册 marker）。Must NOT：跳过守护测试；不得把 skip 记绿。
  Parallelization: Wave 8 | Blocked by: 2-57 | Blocks: 58 | Can parallelize with: 59
  References (executor has NO interview context - be exhaustive): pyproject.toml; requirements-node.txt; scripts/check_grep_gates.py; tests/test_architecture_invariants.py; tests/test_acp_job_edit.py
  Acceptance criteria (agent-executable): 上述命令全绿；`python -m compileall -q src` 通过
  QA scenarios (name the exact tool + invocation): happy = 全量 + 慢测（或 NOT_VERIFIED）+ lint 绿；failure = 任一守护红则修复后再交付，Evidence <attemptDir>/task-60-acp-nmr-goodman-gap-remediation.txt
  Commit: Y | test(nmr): full regression, packaging and stale-reference sweep

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Real manual QA
- [x] F4. Scope fidelity

## Commit strategy
- 每个实现 todo 一个（或 TDD 红/绿成对）conventional commit：`type(scope): summary`，type ∈ {test,refactor,feat,fix,docs,chore}，scope ∈ {nmr,cccp,packaging,guards,executor,confsearch}。
- todo 1 与 59 为 `N`（不产生产品代码变更，只写 `.omo/evidence/`）；其余为 `Y`。
- 每个 commit 必须让其对应 pytest 子集 + 守护门（architecture_invariants / grep_gates / job_edit / frontend_sync / api / catalog）绿；禁止“红着提交、下个 todo 修”。
- A/B/C 可作为当前功能的纠错版本先行合并；D 与迁移计划收敛共用 `nmr_shielding` 唯一实现体；E/F 随后；G/H 独立提交。
- 触碰 cccp 的 todo（26–33）commit 后必须重跑 `tests/test_architecture_invariants.py` + `tests/test_grep_gates_script.py`；触碰 catalog/工作流面必须重跑 `tests/test_acp_job_edit.py`。

## Success criteria
- G01–G16 每个工程缺口都有对应任务、测试与证据；G17 以 `ShiftPredictor` 协议/隔离/评测挂钩交付并明确标注后续范围。
- `pytest -m "not slow" -q` 全绿；守护套件（`test_architecture_invariants.py` / `test_grep_gates_script.py` / `test_acp_job_edit.py` / `test_frontend_sync.py` / `test_acp_api_v1.py` / `test_acp_catalog.py`）全绿；`ruff check src tests && ruff format --check src tests` 通过。
- 报告 schema v2 本地/远端展示一致、旧报告只读兼容、任何路径 `null` 不显示为 0；manifest 所有产物真实存在。
- 参数闭环：catalog 每个 NMR 可编辑字段经 GUI→有效配置→本地 argv→远端 argv→ORCA 输入真实生效；不匹配方法显式拒绝；`--name` 断点修复且回归测试守护。
- 依赖与打包：`requirements-node.txt` 与 `pyproject.toml` 同步；wheel 含 `acp/nmr/models/` 资产；Bruker/FCHL/XLSX 能力缺失提前明确报错。
- 真实 QC：level-1 冒烟产出证据，或在缺二进制时明确 `NOT_VERIFIED`（绝不冒充通过）。
- `GAP_COVERAGE.md` 覆盖 G01–G17 无孤儿；外部数据集校准战役与 DP5q GNN 训练明确记录为后续计划，本计划无任何无数据的准确率/校准声明。
- Final verification wave F1–F4 全部 APPROVE，并在用户明确 okay 前不宣告完成。
