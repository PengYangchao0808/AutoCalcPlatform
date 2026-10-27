# M4-04 — 限定 Diels–Alder 科学纵切（ADR-008）证据目录

由 `scripts/acceptance/acp2_m404_da.py` 生成（真实 QC，隔离 run root
`/ACPServer/acp2-runs/remediation/m4-04-final`）。判据文件
`src/cccp/analysis/research/science_criteria.py` 与 ADR-008 全程**只读**；
指纹 `f89f73da…3449d3` 与 ADR 一致（`preflight.json`）。

- `scenario_result` = **FAIL**（执行链未跑完）
- `claim_status` = **inconclusive**（科学命题无结论）
- H4 = **OPEN**

## 体系 / 方法 / 依据

| 项目 | 取值 | 依据 |
| --- | --- | --- |
| 体系 | butadiene(C4H6) + ethene(C2H4) → cyclohexene(C6H10)，16 原子 | ADR-008 §1，`tests/fixtures/acp2/science/diels_alder/v1/{reaction,reactants,product}.json/xyz` |
| Method / Basis | `wB97X-D4` / `def2-TZVP`（**未降级**） | ADR-008 §3 |
| 电子态 | 中性闭壳层单重态，charge 0，multiplicity 1，`state.singlet.s0` | ADR-008 §3 |
| 温度/压力/标准态 | 298.15 K / 100000 Pa / `1_bar_ideal_gas` | ADR-008 §3 |
| 路径/构象搜索级别 | xTB GFN2（ORCA 6.1.1 / xTB 6.7.1） | ADR-008 §7 |
| 后端 | ORCA `/home/xieningke/orca611/orca`（6.1.1）、xTB `/home/xieningke/xtb-dist/bin/xtb`（6.7.1） | dispatch 前 `resolve_*` + 版本探测（`preflight.json::software`） |

> 注意：`/usr/bin/orca` 是本机 GNOME 屏幕阅读器，不是 QC 程序。脚本显式识别并拒绝它，
> 并把真正的 ORCA 路径导出为 `CONFSEARCH_ORCA_PATH`；该事实记录在
> `preflight.json::software.findings`。

## 判据逐项结果（scenario_result 与 claim_status 分列）

| 判据 | scenario_result | claim_status | validator | 说明 |
| --- | --- | --- | --- | --- |
| DA-C01-ts-single-imaginary-mode | FAIL | not_evaluated | `check.ts_saddle_candidate` | 频率步未派发（DG-01/02） |
| DA-C02-ts-mode-mapping | FAIL | not_evaluated | `check.ts_mode_mapping` | 同上 |
| DA-C03-frequency-evidence | FAIL | not_evaluated | `check.frequency_evidence` | 同上 |
| DA-C04-irc-bidirectional | FAIL | not_evaluated | `check.irc_bidirectional_completion` | IRC 步未派发 |
| DA-C05-endpoint-association | FAIL | inconclusive | `check.irc_endpoint_identity` | 无 IRC 端点证据 |
| DA-C06-energy-comparability | FAIL | inconclusive | `check.activation_energy_comparability` | 反应物能量齐全、TS 能量缺失 |
| DA-C07-reactant-stoichiometry | FAIL | supported | `check.reactant_stoichiometry_completeness` | butadiene + ethene 真实电子能完整 |

问题层：`path_connectivity` = inconclusive，`activation_energy` = inconclusive。

## 九步执行事实（`chain/`）

| ADR 步 | 入口 | 状态 | 秒 | TaskRef |
| --- | --- | --- | --- | --- |
| 1 endpoint_prep（声明替代 `alt_endpoint_prep_single_conformation`） | legacy task core（optimize ×3） | completed | 2543 | `20261027_940_endpoint_prep_{butadiene,ethene,cyclohexene}` |
| 2 candidate_path | legacy task core（`xtb_path_search`） | **failed** | 9.6 | `20261027_940_candidate_path` |
| 3 candidate_frequency | bounded recipe envelope | skipped（无 TS guess） | — | — |
| 4 ts_optimize | bounded recipe envelope | skipped | — | — |
| 5 ts_frequency | bounded recipe envelope | skipped | — | — |
| 6 irc | legacy task core | skipped | — | — |
| 7 endpoint_reactants | bounded recipe envelope | skipped | — | — |
| 8 endpoint_product | bounded recipe envelope | skipped | — | — |
| 9 energy_thermochemistry | deterministic analysis | partial | 0 | `20261027_942_energy_thermochemistry` |

**失败原因（真实）**：GFN2-xTB PATH 在默认 `path.inp`（`alp=1.2`）下未到达产物
（`xtb_path: No product! Decrease alp…`，24 帧全部停在起点几何，RMSD 5.6 Å）。
`alp=0.5/0.3` 的手工复测同样未闭合（RMSD 最低 0.92 Å）。没有任何 TS guess
⇒ 步骤 3–8 按 DG-02 记为 skipped，绝不拿任意一帧冒充候选。

## 公开入口（bounded recipe）

冻结 `recipe.diels_alder.limited.v1` 交给 `compile_step_dag_definition` 的结果：
`unsupported_step_kind: endpoint_prep: pure-analysis/non-dispatchable steps are not
dispatchable bounded DAG steps`（`public-entry.json`）。bounded recipe 入口的
`ADR008_REQUIRED_STEPS` 仍把 `endpoint_prep` / `candidate_path` /
`energy_thermochemistry` 列为 open，且 `irc` 不在 `TASK_ENTRYPOINTS` 内 ——
**九步无法作为一个 BoundedRecipe attempt 提交**（G15 gap 如实记录）。

## 降级 / 负例（`degradation.json`，全部通过）

| 场景 | 构造/真实 | 结果 | 规则 |
| --- | --- | --- | --- |
| 单侧 IRC | 构造（本机无 TS 可跑单侧 IRC） | DA-C04 → inconclusive，绝不 refuted | DG-05 |
| 缺频率 | **真实**（频率步从未派发） | DA-C01/C03 → not_evaluated | DG-01/DG-02 |
| 端点不匹配 | 构造（稀疏化端点几何） | DA-C05 → refuted | — |
| 伪造 supported | 构造 | 拒绝（RJ-04 digest、RJ-05 override）或无源支持降级为 inconclusive | DG-04 |

## 证据链（`evidence-chain.json`）

- `EvidenceReport` 落在真实单任务 worker attempt（`projection_attempt`，ORCA
  singlepoint 46 s，回执 `state=completed`），报告 7 条 claim 带 digest 绑定来源。
- 平台归属：`project_evidence` **严格模式拒绝** —— `TaskRefError:
  task_ref_invalid_release_digest`（`LocalRelease.release_digest` 是裸 sha256，
  `TaskRef` 要求 `sha256:` 前缀且回执比较为字面比较）。**这是真实平台缺口**。
  受控重试只归一化该单一字段（回执副本 + TaskRef），其余身份/摘要/task_result digest
  均为真实字节，`controlled_digest_form_normalisation: true` 如实标注 → 归属校验通过。
- 决策：平台 `decide_evidence` = `revise`；RPH `decideFromView` = `escalate`
  （blocking missing check 存在时升级）；两者都拒绝 `accept`（
  `both_readers_reject_accept: true`），但动作不同 —— 跨端决策口径差异如实记录。
- Workbench：真实 `frontend/js/workbench/features/research-evidence-view.js` 的
  `project(view)` 无头运行，execution/evidence/claims 分三行，`verdict=needs_review`，
  blocking missing check 可见。

## H4（`h4.json`）

| 行 | required | scenario_result | claim_status | 状态 |
| --- | --- | --- | --- | --- |
| DA-ENV-LOCAL | 是 | FAIL | inconclusive | open（8 步未完成 + 缺 `walltime_process_termination`/终端 usage receipt） |
| DA-ENV-LSF | 是 | NOT_VERIFIED | not_evaluated | open（M4-03 依赖） |

H4 = **OPEN**：Local 行未闭合、LSF 行为 required 且 NOT_VERIFIED、预算计时基准
`unmeasured`。Local 结果不替代 LSF 行。

## 已知限制 / 遗留

1. xTB PATH 在本体系默认参数下不收敛 → 无 TS guess；这是 M4-04 的真实科学阻塞点，
   需要 ADR 级别的候选构造决策（新增冻结 TS 候选 fixture）或 xTB `path` 参数
   的研究（`alp`/`npoint`/`anopt`），不能把阈值或判据迁就结果。
2. 九步无法装入一个 bounded recipe attempt；bounded-DAG 无跨步几何传递，且
   `irc`/`thermochemistry` 不可 dispatch。证据闭合是"九次 attempt + 同一 release"，
   不是"一个 TaskRef"。
3. bounded-DAG attempt 只写 per-step 证据与 step-chain 回执，不写
   `task_result_digest`，平台证据投影只能绑定单任务 attempt（已在 `projection_attempt`
   中真实执行并标注）。
4. `LocalRelease.release_digest`（裸 sha256）与 `TaskRef`（要求 `sha256:`）形式不一致，
   真实本机 attempt 无法原样投影（已记录 + 受控归一化演示）。
5. 平台 `revise` 与 RPH `escalate` 的判定口径差异（都非 accept）。
6. 本机未安装 Shermo：Gibbs 活化能未计算（仅电子能比较）；`ΔG‡` 不作声称。
7. 计时：endpoint_prep 2543 s（butadiene 964 s / ethene 40 s / cyclohexene 1539 s），
   singlepoint 46 s，frequency（def2-SVP 探测）144 s；`budget_timing_basis` 仍
   `unmeasured`（代表性步骤计时尚未进入全链预算推导）。
