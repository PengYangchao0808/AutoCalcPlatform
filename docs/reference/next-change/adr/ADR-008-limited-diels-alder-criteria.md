# ADR-008 — 限定 Diels–Alder 科学判据冻结（TS / 频率 / 双向 IRC / 端点关联 / 能量可比性）

- **状态**：Accepted（2026-10-27，M1-05 契约冻结）
- **Owner**：A（科学判据、validator、阈值）；**会签**：B（平台投影/预算/执行限额）、C-RPH（EvidenceView/ResearchDecision）、C-Web（证据展示）
- **来源任务**：`reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md` M1-05（G15/G18）；前置 M1-03（ADR-003）、M1-04（D06 摘要域 + D09 执行限额）
- **输入**：`docs/reference/next-change/acp2-contracts/science/requests/ADR-008-input.md`（A-14b）；冻结 fixture `tests/fixtures/acp2/science/diels_alder/v1/`
- **下游不可豁免前置**：M4-04（限定 Diels–Alder 科学纵切）、M2-04d（DA 公开入口）
- **不取代**：ADR-003（TaskRef 归一/digest-bound 证据投影/独立 ResearchDecision）与 ADR-006（MCP 协商）。本文不新增第二个证据模型、不新增 claim 词汇、不复制 TaskRef 规则
- **机器可读判据**：`src/cccp/analysis/research/science_criteria.py`（`CRITERIA` / `QUESTION_ROWS` / `DEGRADATION_RULES` / `REJECTION_RULES` / `FROZEN_THRESHOLDS`）
- **判据定义指纹**（bare sha256）：`f89f73da4178f4db35b5eb2b8f3958a4cad8bacc5c82490ccfc6777a2c3449d3`
- **样例 fixture**：`tests/fixtures/acp2/science/diels_alder/criteria/`（`fixture-manifest.json` 逐文件 sha256）
- **守护测试**：`tests/test_acp2_science_criteria.py`（本 ADR 的每张判据表与模块逐行比对）

> **冻结纪律**：本 ADR 的全部判据、阈值、降级规则与预算依据在任何真实计算**之前**冻结。真实结果出来后**不得**据此倒推、放宽或加严任何一行；若科学判据必须变化，只能新建 `criteria_version`（新 ADR 版本 + 新审批），并重新记录指纹。执行完成（`scenario_result = PASS`）永远不等于科学命题成立。

## 1. 范围与冻结候选

唯一 active 候选（简单基元示例）：

```text
buta-1,3-diene (C4H6) + ethene (C2H4) -> cyclohexene (C6H10)
```

- 16 个声明原子；一對一 atom mapping 见 §2（源自冻结 `reaction.json`）。
- `reaction_scope = net_reaction`；**单基元步声称不成立**。意图只记录为 `scope.intended_candidate_kind = "single_elementary_concerted_step"`，不构成证据。
- 仅两个问题被准入（见 §9）：`question.diels_alder.path_connectivity.v1` 与 `question.diels_alder.activation_energy.v1`。
- 候选上限：`max_candidate_paths = 1`、`max_ts_candidates = 1`、`max_conformers_per_endpoint = 1`；不做构象搜索、MD 或 Monte-Carlo 采样。
- **执行链验证 ≠ 研究候选搜索**：本 ADR 只冻结"已知、正确描述的起点被现有执行栈带通"的控制行。已知 TS（或控制行 PASS）**永远不是**搜索结果；搜索行是独立证据行，不由控制行继承。
- **起始结构来源标签**（每结构恰一个，B/C 可加展示元数据但不得丢弃）：`user_input`（本切片无）、`frozen_known_candidate`（`reactants.xyz`/`product.xyz`，以及控制行用的预冻结 TS 候选）、`generated_candidate`（本切片无）。

## 2. 输入身份与 atom mapping

| 项目 | 冻结值 |
| --- | --- |
| `reaction_id` | `reaction.diels_alder.butadiene_ethene_cyclohexene.v1` |
| 左侧端点 | `butadiene`（C4H6，10 原子）、`ethene`（C2H4，6 原子），smiles 输入 |
| 右侧端点 | `cyclohexene`（C6H10，16 原子），smiles 输入 |
| mapping | `left_to_right`/`right_to_left` 双向全映射，唯一、一对一、精确互逆、无未映射原子 |
| 键变化 | 2 形成（bd_c1–et_c2、bd_c4–et_c1）+ 4 变化；0 断裂 |
| 原子序 | XYZ 原子序与声明 `atom_ids` 逐字节一致（`product.xyz` / `reactants.xyz`） |
| 守恒 | 元素与净电荷守恒（两侧 charge 0、multiplicity 1） |
| 结构来源 | 全部 `frozen_known_candidate`（构造几何，非实验、非计算优化） |
| 立体化学 | `status: unknown`，`centers: []`，`double_bonds: []`；不得凭空补写 endo/exo |

判据评估的输入身份必须是 **digest-bound** 证据（§12）：mapping/端点结构以文件 sha256 与 `reaction.json` 绑定；任一 digest 失配即拒绝，不得"就近匹配"。

## 3. 方法与电子态（预登记，不在运行中逐候选调整）

| 参数 | 冻结值 |
| --- | --- |
| Method / Basis | `wB97X-D4` / `def2-TZVP` |
| 相位 / 溶剂 | gas phase，`solvent: null`（无隐式溶剂） |
| 电子态 | 中性闭壳层单重态，`charge 0`、`multiplicity 1`、`state_id = state.singlet.s0` |
| 温度 / 压力 | `298.15 K` / `100000 Pa` |
| 标准态 | `1_bar_ideal_gas` |
| 能量单位 | 原始能量 `hartree`；比较一律换算 `kcal_per_mol` |
| 参考态 | 无限分离的气相 butadiene + ethene，以及 cyclohexene，全部处于冻结条件 |
| 活化能定义 | `ΔE‡ = E(TS) − [E(butadiene) + E(ethene)]`；`ΔG‡` 另用冻结频率热化学（298.15 K / 1 bar）；**无隧穿校正**、无实验速率声称 |
| 后端 | `orca` 首选；`TaskRequest.backend` 保持 `null`，由 cccp 能力选择决定 |

## 4. Recipe 必需步骤（`recipe.diels_alder.limited.v1`）

| # | step_id | task_kind | 必需 | 判据输入 |
| --- | --- | --- | --- | --- |
| 1 | `endpoint_prep` | `conformer_search` | 是 | 端点结构（单构象边界） |
| 2 | `candidate_path` | `xtb_path_search` | 是 | 候选路径几何（`frozen_known_candidate` 控制） |
| 3 | `candidate_frequency` | `frequency` | 是 | 原生挤压模式索引（TS 模式来源） |
| 4 | `ts_optimize` | `optimize` (`mode=transition_state`) | 是 | 优化后 TS 几何 |
| 5 | `ts_frequency` | `frequency` | 是 | 虚频/模式向量（DA-C01/C02/C03） |
| 6 | `irc` | `irc`（`directions=[forward,reverse]`） | 是 | 双向 IRC（DA-C04） |
| 7 | `endpoint_reactants` | `singlepoint` | 是 | 反应物端关联（DA-C05/C07） |
| 8 | `endpoint_product` | `singlepoint` | 是 | 产物端关联（DA-C05） |
| 9 | `energy_thermochemistry` | `thermochemistry` | 是 | 能量可比性/活化能（DA-C06/C07） |

- 必需步骤缺失、被跳过或被 `diagnostic_only` 替代 → 对应判据 `not_evaluated`，不得以部分链路充当完整链路。
- 每一步的 validator 是冻结常量（`VALIDATOR_VERSION = 1.0.0`）；记录必须声明同一版本（`RJ-03`）。
- Recipe 定义摘要 `recipe_definition_digest` 在 D06 中仍为 **not-frozen**（A/M2-05 未注册跨端校验器）；本文不把它当作 frozen 引用，也不以本文替代该注册。

## 5. 冻结参数与容差

构造值与测量值必须区分：本节四个容差由 `cccp.analysis.research.validators` 拥有（单一来源），`activation_energy_comparison_abs_kcal_per_mol = 0.5` 是**构造的比较上限**，不来自任何实测或参考值。

| 容差 | 值 | 所有者 | 构造 |
| --- | --- | --- | --- |
| `atom_count_tolerance` | `0` | `validators.ATOM_COUNT_TOLERANCE` | no |
| `coordinate_abs_tolerance_angstrom` | `0.001` | `validators.COORDINATE_ABS_TOLERANCE_ANGSTROM` | no |
| `imaginary_mode_threshold_cm1` | `0` | `validators.IMAGINARY_MODE_THRESHOLD_CM1` | no |
| `low_frequency_threshold_cm1` | `50` | `validators.LOW_FREQUENCY_THRESHOLD_CM1` | no |
| `activation_energy_comparison_abs_kcal_per_mol` | `0.5` | fixture `tolerances` | yes |
| `energy_reparse_abs_hartree` | `1e-06` | fixture `tolerances` | no（解析往返） |

阈值方向性（测试守护）：`low_frequency_threshold_cm1 > imaginary_mode_threshold_cm1`；`coordinate_abs_tolerance_angstrom > 0`；`atom_count_tolerance == 0`（原子数不得用容差掩盖真实不一致）。软实频（≤ 50 cm⁻¹）只记录、不单独构成反驳。

## 6. 预算依据与执行限额（引用 M1-04 词汇，不新建单位）

预算字段沿用冻结 fixture 与 M1-04 `acp_services.runtime.execution_limits` / `research_budget` 词汇；本文不引入新单位，也不把任何 recorded-only 写成 enforced。

| 字段 | 冻结值 | 依据 / 能力 |
| --- | --- | --- |
| `science_deadline_seconds` | `21600` | 构造上界；science runtime 记录，平台不据此单独终止 |
| `export_budget_seconds` | `600` | 保留证据导出窗口（不起新科学、不延长 science deadline） |
| `aggregate_budget_seconds` | `22200` | `= science + export` |
| `started_at` | `2026-10-23T00:00:00Z` | 共享冻结纪元（等于 `frozen_at`，不是运行时间戳） |
| `max_qc_invocations` | `10` | `qc_invocation`（recorded-only） |
| `max_task_refs` | `10` | `task_ref`（enforced） |
| `max_attempts_per_step` | `2` | `attempt`（enforced） |
| `max_bsub` | `1` | `bsub`（recorded-only） |
| `budget_timing_basis` | `unmeasured` | `measured_step_timings: null`、`timing_evidence: []` |

- **三类时钟**（M1-04b `CLOCK_CONTRACT`）：排队期只受 queue deadline 约束；启动后由 wall deadline 终止；science runtime 仅记录。客户端/RPH 等待超时不是平台时钟，不得替代。
- **限额行**（`EXECUTION_LIMITS`，本文只引用，不改其能力声明）：

| 限额 | kind | 载体 | 当前能力 |
| --- | --- | --- | --- |
| `walltime_admission_bound` | admission-bound | `LocalAdmission.effective_bounds` / `AdmissionRequest.duration_s`（`wall_seconds`） | enforced |
| `walltime_process_termination` | process-termination | 本地进程组 / CCCP `resources.timeout_s`（子进程级）/ LSF `-W` | NOT_VERIFIED |
| `queue_deadline` | queue-deadline | 准入时写入的持久绝对时刻（`queue_seconds`） | NOT_VERIFIED |
| `science_runtime_recorded` | recorded-only | 终端 usage receipt 秒数 | recorded-only |
| `cumulative_core_hours` | recorded-only | usage/metering receipt（cores × seconds） | recorded-only |
| `model_tokens` | recorded-only | C 侧模型代理 + 记录（本切片不使用模型） | recorded-only |

- **排队计入**：排队等待消耗 `science_deadline_seconds` 与 `aggregate_budget_seconds`；导出窗口不计入排队。
- **在飞停止**：停止请求被接受后不得再派发新步骤或重试；在飞步骤带确认停止，其部分产物**不**作为科学证据。
- **H4 后果**：`unmeasured` 使上述上界均为构造值。在代表性步骤计时被记录并以全链预算推导之前，H4 保持 **OPEN**；执行链 PASS 不能关闭 H4。

## 7. 软件与版本要求

| 项目 | 要求 |
| --- | --- |
| 电子结构后端 | ORCA 首选（`wB97X-D4`/`def2-TZVP`）；任何实际二进制版本必须在 dispatch 前经 `cccp.software.resolve_executable` / `detect_version` 记录 |
| 路径/构象搜索 | xTB（GFN2）、CREST（`endpoint_prep` 构造边界）；版本同样在 dispatch 前记录 |
| 版本缺失 | `software_version_missing` → 判据不可评估（`not_evaluated`），不得默认通过 |
| Validator | `cccp.analysis.research.validators`，`VALIDATOR_VERSION = 1.0.0`（记录版本不一致 = `RJ-03`） |
| 判据表 | `acp2.science.da-criteria.v1`，`criteria_version = 1.0.0`，指纹见文首 |
| 证据契约 | `evidence.schema.json` major v1（`ClaimStatus` 四值词汇与 §10 一致） |
| 发布绑定 | 证据行须绑定当次 `release_id` / `release_digest`（ADR-003 TaskRef 归一规则）；无 release digest 的证据行不得标记为可消费 |

## 8. 判据表（与模块逐行一致）

### 8.1 判据行

| Criterion id | Area | Acceptance row | Requirement | Required evidence | Validators | Thresholds | Refutable | Recipe steps | Downgrade rules |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DA-C01-ts-single-imaginary-mode` | ts | `DA-08-real-ts` | The optimized transition-state candidate has exactly one imaginary frequency at the frozen threshold; this supports only a local first-order saddle candidate. | `transition_state_geometry`, `imaginary_frequency` | `check.ts_saddle_candidate` | `imaginary_mode_threshold_cm1=0 cm-1` | yes | `ts_frequency` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-03-partial-evidence` |
| `DA-C02-ts-mode-mapping` | ts | `DA-08-real-ts` | The imaginary mode is identified by its native printed index and belongs to the optimized candidate within the frozen coordinate and atom-count tolerances. | `transition_state_geometry`, `imaginary_frequency` | `check.ts_mode_mapping`, `check.frequency_membership` | `coordinate_abs_tolerance_angstrom=0.001 angstrom`, `atom_count_tolerance=0 atoms` | yes | `candidate_frequency`, `ts_optimize`, `ts_frequency` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-03-partial-evidence` |
| `DA-C03-frequency-evidence` | frequency | `DA-08-real-ts` | The frequency result carries parsed Hessian normal-mode evidence for a complete real-mode list, and every real mode at or below the low-frequency threshold is reported as soft. | `imaginary_frequency`, `transition_state_geometry` | `check.frequency_evidence` | `low_frequency_threshold_cm1=50 cm-1`, `imaginary_mode_threshold_cm1=0 cm-1` | no | `ts_frequency` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-03-partial-evidence` |
| `DA-C04-irc-bidirectional` | irc | `DA-09-real-irc` | Both IRC directions from the optimized candidate completed with native steps and a final structure; one direction alone never proves a bidirectional path. | `irc_forward`, `irc_reverse` | `check.irc_bidirectional_completion` | — | no | `irc` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-05-single-irc-direction` |
| `DA-C05-endpoint-association` | endpoint | `DA-09-real-irc` | Each IRC direction reaches exactly one frozen endpoint under the declared atom mapping with a stable atom order; the direction label is never trusted. | `path_endpoints`, `irc_forward`, `irc_reverse` | `check.irc_endpoint_identity`, `check.irc_atom_order_stability` | `atom_count_tolerance=0 atoms`, `coordinate_abs_tolerance_angstrom=0.001 angstrom` | yes | `endpoint_reactants`, `endpoint_product` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-03-partial-evidence` |
| `DA-C06-energy-comparability` | energy | `DA-10-real-energy` | The compared energies share method, basis, electronic state, temperature, pressure, standard state, reference state, energy kind and explicit unit before any subtraction. | `energy_difference`, `thermochemistry` | `check.method_consistency`, `check.electronic_state_consistency`, `check.energy_comparability`, `check.activation_energy_comparability` | — | yes | `endpoint_reactants`, `endpoint_product`, `energy_thermochemistry` | `DG-01-evidence-absent`, `DG-02-step-not-completed`, `DG-03-partial-evidence` |
| `DA-C07-reactant-stoichiometry` | energy | `DA-10-real-energy` | Every reactant component of the frozen reference state is present with its declared coefficient, so the activation-energy subtraction is defined. | `energy_difference`, `path_endpoints` | `check.reactant_stoichiometry_completeness` | `atom_count_tolerance=0 atoms` | yes | `endpoint_reactants`, `energy_thermochemistry` | `DG-01-evidence-absent`, `DG-02-step-not-completed` |

`Acceptance row` 指向冻结 `diels_alder/v1/acceptance.json` 的 H4 行（DA-08/09/10）；静态行 DA-01…DA-07 保持 `met_by_static_fixture`，本文不重新定义它们。

### 8.2 四值状态规则

| Criterion id | supported | refuted | inconclusive | not_evaluated |
| --- | --- | --- | --- | --- |
| `DA-C01-ts-single-imaginary-mode` | exactly one mode lies below the imaginary threshold | the frequency list carries two or more imaginary modes, or none at all | frequencies exist but the list cannot be classified | no frequency result exists for the optimized candidate |
| `DA-C02-ts-mode-mapping` | the mapped mode resolves to one native printed index and the compared geometries agree within tolerance | the mapped mode is not resolved, or the frequency geometry differs beyond tolerance, or the atom count differs | the frequency or optimization result lacks the mode vectors needed to map | no mode selection or frequency result exists |
| `DA-C03-frequency-evidence` | every expected mode index has a normal-mode vector with the structure's atom count | never: a missing Hessian or mode vector is missing evidence, not a contradiction | the result carries no frequencies, no parsed analysis, or mode vectors are missing | no frequency result exists |
| `DA-C04-irc-bidirectional` | forward and reverse both succeeded, converged and carry native steps and a final structure | never: a one-sided or non-converged direction is missing evidence, not a contradiction | a direction is absent, failed, unconverged or carries no final structure | no IRC result exists |
| `DA-C05-endpoint-association` | both directions resolve to the declared endpoints with a stable atom order | the final structure affirmatively reproduces neither frozen endpoint under the mapped bond topology | the endpoint mapping is ambiguous, unresolved or the atom order is unstable | no endpoint identity or atom-order evidence exists |
| `DA-C06-energy-comparability` | every comparability identity matches on both sides and the unit is explicit | a declared method, electronic state or standard state differs, or the energy unit or kind is incompatible | an identity field or an energy value is missing on at least one side | no energy value exists for a required participant |
| `DA-C07-reactant-stoichiometry` | the supplied reactant set equals the declared required set with matching coefficients | a required reactant is missing, an unexpected component is present, or a coefficient differs | a component is declared but its energy record is incomplete | no required reactant set or no reactant energy record exists |

**'never' 行是硬约束**：`DA-C03`、`DA-C04` 标记 `Refutable = no`。由模块抛出 `RJ-08-refuted-not-allowed`：单虚频或单侧 IRC 只能让相应判据降级，绝不能把连通性"反驳"掉。

## 9. 问题聚合（判据如何合成科学命题）

| Question id | Required criteria | Refuting criteria | supported | refuted | inconclusive | not_evaluated |
| --- | --- | --- | --- | --- | --- | --- |
| `question.diels_alder.path_connectivity.v1` | `DA-C01-ts-single-imaginary-mode`, `DA-C02-ts-mode-mapping`, `DA-C03-frequency-evidence`, `DA-C04-irc-bidirectional`, `DA-C05-endpoint-association` | `DA-C05-endpoint-association` | every required criterion is supported | the endpoint-association criterion is refuted: the IRC reaches neither declared endpoint | some required criterion is refuted but not refuting, or evidence is partial | no required criterion has anything but an unevaluated verdict |
| `question.diels_alder.activation_energy.v1` | `DA-C01-ts-single-imaginary-mode`, `DA-C02-ts-mode-mapping`, `DA-C03-frequency-evidence`, `DA-C06-energy-comparability`, `DA-C07-reactant-stoichiometry` | `DA-C06-energy-comparability`, `DA-C07-reactant-stoichiometry` | every required criterion is supported | energy comparability or reactant stoichiometry is refuted for the frozen conditions and reference state | some required criterion is refuted but not refuting, or evidence is partial | no required criterion has anything but an unevaluated verdict |

聚合规则（`_combine_question`）：

1. 任一 `refuting` 判据 `refuted` → 问题 `refuted`（"端点证伪可 refuted"）。
2. 全部必需判据 `supported` → 问题 `supported`。
3. 否则：若任一必需判据已给出实质结论（`supported`/`inconclusive`/`refuted`）→ `inconclusive`。
4. 否则（所有必需判据均 `not_evaluated`）→ `not_evaluated`。

因此：**单虚频 ≠ 连通性**；DA-C01 被反驳只反驳"该候选是一阶鞍点"，不反驳"存在一条连接路径"，问题停在 `inconclusive`。**缺端点/单侧 IRC/缺来源**同样停在 `inconclusive` 或 `not_evaluated`。整片 `overall_claim_status` 按同一优先级（`refuted` > 全 `supported` > `inconclusive` > `not_evaluated`）由两个问题合成。

## 10. 缺失证据降级规则

| Rule | Condition | Result | Never | Reason |
| --- | --- | --- | --- | --- |
| `DG-01-evidence-absent` | no evidence record exists for the criterion | `not_evaluated` | `refuted` | an absent calculation is missing evidence, not a contradiction |
| `DG-02-step-not-completed` | the producing step failed, was cancelled or stopped, or never dispatched | `not_evaluated` | `refuted` | a failed step refutes that step's completion only, never the stronger proposition |
| `DG-03-partial-evidence` | a blocking missing check or partial output is declared with the verdict | `inconclusive` | `supported` | partial evidence can neither affirm nor deny the claim |
| `DG-04-source-not-digested` | a supported or refuted verdict carries no digested source | `inconclusive` | `supported`, `refuted` | an unattributed verdict is not admissible evidence |
| `DG-05-single-irc-direction` | only one IRC direction reached a terminal state | `inconclusive` | `supported` | one IRC direction never proves a bidirectional path |

降级是**单向**的：`refuted` 只能来自显式矛盾证据并带 digest；"没算"、"没跑完"、"没解析出模式向量"一律 `not_evaluated`/`inconclusive`。不收敛的优化只反驳"该优化收敛"，不反驳"该区域不存在驻点"。

## 11. 输入拒绝规则（typed error，不是 claim status）

| Rule | Condition |
| --- | --- |
| `RJ-01-unknown-criterion` | the record or query names a criterion that is not frozen in this table |
| `RJ-02-foreign-validator` | the record was produced by a validator outside the criterion's frozen set |
| `RJ-03-validator-version-drift` | the record's validator version differs from the frozen validator version |
| `RJ-04-evidence-not-digest-bound` | the evidence digest is malformed or does not match the evidence payload |
| `RJ-05-status-override` | the evidence payload carries a status/verdict override key |
| `RJ-06-unknown-field` | a closed record or bundle payload carries an undeclared field |
| `RJ-07-duplicate-record` | two records claim the same criterion |
| `RJ-08-refuted-not-allowed` | a record refutes a criterion the ADR marks as not refutable |
| `RJ-09-unreasoned-status` | an inconclusive or not_evaluated record names no missing check |
| `RJ-10-missing-field` | a closed record or bundle payload omits a required field |
| `RJ-11-invalid-shape` | a closed record or bundle payload declares a field of the wrong type |

拒绝不是"不通过"：畸形/异源/伪造记录必须变成显式错误，永远不得被读成 `refuted` 或 `supported`。

## 12. `scenario_result` 与 `claim_status` 的分离

判据评估输入是 **digest-bound 证据**（`CriterionEvidence`），其字段与 ADR-003 证据层一致：

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `criterion_id` | 被评估的冻结判据 | 必须在 §8.1 注册（否则 `RJ-01`） |
| `claim_id` | 任一 validator 输出的 claim 标识 | 非空；由 `criterion_evidence_from_claim` 从 `evidence.Claim` 直接转发 |
| `status` | validator 给出的四值结论 | 必须属于 `supported/refuted/inconclusive/not_evaluated` |
| `validator_id` / `validator_version` | 产生该结论的确定性 validator | 必须在该判据的冻结集合内；版本必须为 `1.0.0` |
| `evidence` | validator 细节载荷 | 顶层不得出现 verdict 覆盖键（`RJ-05`） |
| `evidence_digest` | `sha256(canonical_json(evidence))` | 必须可重算相等（`RJ-04`） |
| `source_digests` | 被判据消费的工件 digest（bare sha256） | `supported`/`refuted` 至少一个，否则 `DG-04` |
| `missing_check_ids` | 冻结 `evidence.schema.json` 缺失检查词表 | 未知 id 拒绝 |

三个记录必须分开写、分开读：

| 记录 | 取值 | 含义 | 不得推出 |
| --- | --- | --- | --- |
| `scenario_result` | `PASS` / `FAIL` / `NOT_VERIFIED` | 验证场景是否以有效工件完成 | 不推出任何 `claim_status` |
| `claim_status` | `supported` / `refuted` / `inconclusive` / `not_evaluated` | 科学命题的结论 | 不推出 H4 关闭 |
| H4 行状态 | `open` / `closed` | 全链预算与真实证据闭合 | 不由单次执行 PASS 关闭 |

`evaluate_criteria(records, scenario_result=...)` 只把 `scenario_result` 原样抄入回执；同一组记录在 `PASS`/`FAIL`/`NOT_VERIFIED` 下给出**完全相同**的判据与问题结论（测试守护）。`CriteriaEvaluation.science_claim_supported` 只读 `overall_claim_status`。

## 13. 样例 fixture 集（正例 / 负例 / 执行完成但无结论）

全部位于 `tests/fixtures/acp2/science/diels_alder/criteria/`，`constructed: true`、`real_run: false`；`evidence_digest` 均是载荷真实 sha256（可由测试重算），`fixture-manifest.json` 记录逐文件 sha256 与判据指纹。

| fixture key | 文件 | 证明什么 | 期望 |
| --- | --- | --- | --- |
| `all_supported` | `bundle.all-supported.positive.json` | 正例：七条判据全部有 digest-bound 支撑 | 两问 `supported`，整片 `supported` |
| `ts_two_imaginary_modes` | `bundle.ts-two-imaginary-modes.refuted.json` | 负例：双虚频反驳一阶鞍点候选；**不**升级为连通性反驳 | DA-C01 `refuted`，两问 `inconclusive` |
| `irc_one_direction` | `bundle.irc-one-direction.inconclusive.json` | 负例：单侧 IRC 不收敛 → 连通性 `inconclusive`；活化能不依赖 IRC 仍 `supported` | DA-C04/C05 `inconclusive`，path `inconclusive`，energy `supported` |
| `endpoint_mismatch` | `bundle.endpoint-mismatch.refuted.json` | 负例：端点证伪 → 连通性 `refuted` | DA-C05 `refuted`，path `refuted` |
| `energy_conditions_mismatch` | `bundle.energy-conditions-mismatch.refuted.json` | 负例：标准态/单位不一致 → 活化能不可比 | DA-C06 `refuted`，energy `refuted`，path `supported` |
| `missing_frequency` | `bundle.missing-frequency.not-evaluated.json` | 缺失证据：无频率证据 → 频率判据 `not_evaluated`（**非** `refuted`） | DA-C01/C02/C03 `not_evaluated`，两问 `inconclusive` |
| `no_conclusion` | `bundle.no-conclusion.completed.json` | **执行完成但科学无结论**：`scenario_result = PASS` 且无任何证据 | 全部判据/问题 `not_evaluated` |
| `supported_without_source` | `bundle.supported-without-source.inconclusive.json` | 无 digest 来源的 `supported` 被判据层降级 | DA-C01 `inconclusive` |
| `status_override` | `bundle.status-override.negative.json` | 结果参数绕过：载荷内 `criteria_passed: true` | 拒绝（`RJ-05-status-override`） |

## 14. 验收行（Local / LSF 各自 required）

两行都 required，且**互不替代**；Local 不等待 LSF，LSF 也不因 Local PASS 而降级。两行都必须分别记录 `scenario_result` 与 `claim_status`。

| Row | 环境 | 必须满足 | 必需预算/限额字段 | 必需回执 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| `DA-ENV-LOCAL` | Local | 9 个必需步骤在本地执行；判据 DA-C01…C07 使用原生 TS/频率/IRC/端点/能量工件 | `wall_seconds`（admission-bound + 进程终止）、science runtime 记录、`qc_invocation`/`task_ref`/`attempt` 计数 | 进程组终止回执（`walltime_process_termination`）、终端 usage receipt、每步 attempt 归属 | `NOT_VERIFIED`（未执行） |
| `DA-ENV-LSF` | LSF | 同一 Recipe 经远程提交完成；`-W`、排队、终止与终端状态闭合 | `wall_seconds` → `bsub -W`；`queue_seconds` → queue deadline；`bsub` 计数 | bsub 回执 + 终端 LSF 状态 + 队列过期/迟到成功处置 + `release_digest` 一致 | `NOT_VERIFIED`（依赖 M4-03 远程证据） |

- LSF DA 行**已列为 required**；在 M4-03 的远程提交/终止/终端状态回执出现之前保持 `NOT_VERIFIED`，**不得**用 Local 结果替代（M4-04 措辞）。
- 两行都必须绑定同一 `TaskRef`/`attempt`/`release`；缺任一 required 行，H4 保持 OPEN。
- `walltime_process_termination` 与 `queue_deadline` 在 M1-04 中仍是 `NOT_VERIFIED`；ADR 不因本文而改其能力声明。

## 15. 明确不支持的问题（out of scope）

| 问题 | 原因 |
| --- | --- |
| endo/exo 选择性 | 对称的 butadiene + ethene 示例没有 endo/exo 区分；本 fixture 无相关字段/问题/Recipe 选项 |
| 完整机理 | 只声明一条候选路径 |
| 排除其他路径 | 其他路径既未枚举也未排除；其缺失不是证据 |
| 实验速率/动力学 | 无实验速率、基准或动力学比较 |
| 全局最小 | 无全局最小或穷尽构象搜索 |
| 一般方法精度 | 无迁移性/基准精度声称 |
| 搜索能力 | 控制行 PASS 不证明研究搜索能力；已知 TS 永远不是搜索成功 |

其他限制：无真实能量/TS 几何/参考值；单构象/单路径/单 TS 是**声明边界**而非发现；计时基准 `unmeasured`；单基元步仅为意图。任何限制必须在证据报告与 UI 中保持可见。

## 16. 变更控制与后果

1. 判据、阈值、降级规则、问题聚合或拒绝规则任一变化 → 新 `criteria_version` + 新 ADR 版本 + 新审批；指纹（§文首）随之更新。禁止"看到结果后"就地放宽。
2. 新增 active 工作流/步骤 → 同步 catalog/registry/edit coverage/`_SCHEDULER_MARKERS`（root ANTI #23/#11）；本文不新增工作流。
3. 与 M4-04 的关系：本文是其**不可豁免**前置；M4-04 必须逐行消费 §8/§9/§10 并分别记录 `scenario_result` 与 `claim_status`。缺任一 required 行 → H4 OPEN。
4. 与 D06 的关系：新增 `science_criteria_digest` 登记行为 additive；`recipe_definition_digest` 保持 **not-frozen**，本文不改变其状态。
5. 真实证据生成后，不得以"结果看起来合理"替代判据；判据评估只接受 §12 的 digest-bound 记录。

## 17. 会签

| 角色 | 范围 | 状态 |
| --- | --- | --- |
| A（Owner） | 科学判据、阈值、降级规则、fixture | 已签署（2026-10-27） |
| B | 平台投影、预算/执行限额引用、`scenario_result` 分离 | 已签署（2026-10-27） |
| C-RPH | EvidenceView/ResearchDecision 消费、known-candidate ≠ search | 已签署（2026-10-27） |
| C-Web | 证据展示、limitations/missing checks 可见 | 已签署（2026-10-27） |

会签仅冻结契约与判据，**不**宣称任何真实计算、LSF 远程执行或 H4 关闭。
