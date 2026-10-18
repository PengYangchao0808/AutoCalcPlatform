# C00-s5 — 写权与交接登记表（write_registry.md）

- **registry_version:** `write-registry v1`
- **tracked_path:** `tests/baseline/cccp-evidence/write_registry.md`（受版本控制；本表本身为 C 路 C直写）
- **created:** 2026-10-18
- **owner:** C 路科学负责人（渠道 `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md`）
- **base_commit:** `dfad6b30e4aa01fa3e0249ac3eb00e313263f10d`
- **计划来源:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 5（C00-s5）；消费方 T7–T10、T16–T47（登记门）
- **覆盖底座:** `.omo/evidence/acp-parallel-plan-3-cccp/c00-closure-ledger.md`（T2 闭包台账，15 工作流）

## 0. 与 B-T3 权威表的关系（不得矛盾）

本表是**准入细则**（逐文件/逐符号写权）；粗粒度「谁可以写」由 B 路权威表裁定，本表只在其下细化：

| 权威表 | 路径 | 归属 | 本表引用提交 |
|---|---|---|---|
| 单写者表 | `tests/baseline/services-evidence/single_writer_map.md` | B 路集成写者（镜像索引 §5） | `c50004304577ec88f36fb650014605d2b9b95c0f` |
| 跨路交接登记表 | `tests/baseline/services-evidence/handoff_registry.md` | B 路集成写者，**C 共同维护、引用同一版本、不复制** | `c50004304577ec88f36fb650014605d2b9b95c0f` |

> 两表由 B 路在 `8a9f1b8` 首次入库；`c50004304577ec88f36fb650014605d2b9b95c0f` 为最近一次触碰 `tests/baseline/services-evidence/` 的提交（`git log -1 -- tests/baseline/services-evidence/`）。C 侧 `tests/baseline/cccp-evidence/handoff_registry.md` 仅为 **HN 序号索引 + C 侧明细**，状态语义与本表一致，跨路事实以 B 的权威表为准。

**粗粒度映射（照抄 B 表，不改写）：**

| 范围 | 权威唯一写者 | 本表细化 |
|---|---|---|
| `src/cccp/**`；登记移交的旧 acp 科学专属文件 | **C** | → `C直写` |
| 混合的 workflows/catalog/executor/results 等旧文件 | **B 为接入写者** | → `B补丁`（C 只产补丁，永不直改） |
| 科学 schema、黄金 fixture、数值/单位/模式身份 | **C** | → `C直写`（科学测试/fixture） |
| `pyproject.toml`、`requirements-node.txt`、根 README/AGENTS、主方案、全局架构门、共享集成测试 | **B 为集成写者** | → `B请求`（C 交精确变更请求） |

## 1. 写权三态定义

- **`C直写`**：C 可直接在本工作树编辑并提交。范围 = ①`src/cccp/**`（目标根，含新增 `cccp.analysis`/产品 schema/插件 SDK）；②**登记移交的 acp 科学专属文件**（本表「移交」列标记，C03/C04/C05 迁移源；旧路径原地改为 re-export shim）；③科学专属测试/fixture；④`tests/baseline/cccp-evidence/**`。
- **`B补丁`**：**混合文件**（`workflows/`、`catalog.py`、`calculations/executor.py`、`results/`、`cli.py`、`scheduler/`、`storage/`、`core/`、`calculations/{primitives,batch}/`、`confsearch/{engine,protocols,manifest,report,result_helpers,contracts}.py` 等）。C **只产出补丁**，交 B 应用；C 永不直接编辑（禁止整文件搬迁，禁止假定直改）。
- **`B请求`**：`pyproject.toml`、`requirements-node.txt`、根 `README`/`AGENTS`、主方案、全局架构门与共享集成测试。C 提交**精确变更请求 + 证据**，由 B 集成。

## 2. 补丁生命周期（与 B-T3 `handoff_registry.md` 对齐）

状态词表与 B-T3 一致，**不得自造同义词**：

- `pending`：已登记，尚无交付提交；消费者不得据此开发真实功能。
- `delivered`：C 已把补丁/契约**提交到共享分支**（或产出补丁文件并入库），B 尚未应用并通过测试。本表 `applied_commit` 先记 provider 基线提交；状态 `delivered`。
- `integrated`：B 分支**已应用补丁且定向测试通过**（`applied_commit` 记 B 应用提交）。**仅「样例是合法 JSON」不算 integrated**。
- `rejected`：交接被驳回/撤销，须在「依据/回退」记录理由。

> `delivered`（生成）≠ `integrated`（接收分支应用 + 测试通过）。本表 `applied_commit` 列同时承载 provider 基线（C 侧）与 consumer 应用提交（B 侧，由 B 回填）。

## 3. 补丁程序（具体命令序列）

> 混合文件迁移一律走此程序，**不以改工作区代替**。所有命令在共享仓库根 `<repo>` 的**隔离 worktree** 执行，避免污染 A/B 在途工作树。

```bash
# ── 变量 ────────────────────────────────────────────────────────────────
REPO=<repo>
PATCH=/abs/path/to/c00-slice-N-write-registry.patch   # 由 C 产出并入库
BASE=<provider_base_commit>                            # C 产出补丁时的基线 SHA
WT=/tmp/acp-c-apply-$$                                 # 隔离 worktree，勿落在原数据目录
TESTS="tests/<slice-targeted-tests>.py"                # 该切片定向测试

# ── 1) 语法/适用性预检（只读，不改变任何工作区）──────────────────────────
git -C "$REPO" apply --check "$PATCH"          # 失败→停止，修订补丁至 apply 干净

# ── 2) 隔离 worktree 应用（不触碰 A/B 在工作树上未提交改动）─────────────
git -C "$REPO" worktree add "$WT" "$BASE"
git -C "$WT"   apply --index "$PATCH"          # --index 暂存，便于生成 consumer 提交

# ── 3) 定向测试（隔离 worktree 内）───────────────────────────────────────
( cd "$WT" && python3.11 -m pytest $TESTS -q )
( cd "$WT" && python3.11 -m pytest tests/test_architecture_invariants.py -q )

# ── 4) 记录 provider 基线与 consumer 应用提交 ────────────────────────────
echo "provider_base_commit=$BASE"
git -C "$WT" rev-parse HEAD                    # consumer 应用提交候选（B 侧最终回填）
# B 应用后回填：applied_commit=<B branch commit>

# ── 5) 撤销应用并清理（记录后必做；隔离环境用完即弃）──────────────────────
git -C "$WT"   apply -R --index "$PATCH"       # 反向撤销
git -C "$REPO" worktree remove "$WT" --force
```

**回退语义**：`git apply -R <patch>` 撤销应用；provider 侧回退 = `git revert <C 提交>`；consumer 侧回退 = B `git revert <applied_commit>`。补丁文件与 `apply --check` 输出一并入库为证据。

**验收 shorthand（本表引用）**：

- `A-ISO` = `python3.11 scripts/verify_cccp_isolation.py`（期望 `RESULT: PASS`）+ `python3.11 -m pytest tests/test_cccp_isolation.py tests/test_architecture_invariants.py -q`。
- `A-DIR:<t>` = 该切片定向测试 `python3.11 -m pytest <t> -q` 全绿。
- `A-PATCH` = §3 补丁程序（`git apply --check` 通过 + 隔离 worktree 定向测试通过 + 记录 + `git apply -R`）。
- **删除 shorthand**：`D-NOSHIM` = B 应用补丁 + `grep -rn` 零调用者 + 定向全绿 → 删旧实现；回退 = revert provider + B revert applied。`D-KEEP` = 迁移期保留（兼容 shim/历史 reader），删除另立切片（不随迁移副作用删）。
- **转发/身份 shorthand**：`RT-SAME` = 旧路径 re-export 同一函数/同一类（`assert new is old`）；`RT-CONV` = 兼容转换（同类身份/异常/序列化保持）；`RT-N/A` = 不适用。

## 4. 登记表（按目标切片分组）

> `provider→consumer`：C 产 → B 集成（B06 worker / adapter / 旧路径接线），产品另有 A 消费。
> `applied_commit` 初始为 `pending`；C 提交时记 provider 基线，B 集成后回填 consumer 提交。

### 4.0 目标根 `src/cccp/**`（C直写，全部）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/cccp/calculation/tasks/*`（15 任务核：conformer_search/md_sampling/clustering/censo_refine/nmr_shielding/scan/singlepoint/optimize/frequency/thermochemistry/irc/casscf/xtb_path_search/orca_gradient/xtb_constrained_opt） | `C直写` | 四层架构唯一执行驻点（ANTI #17）；T2 台账各工作流「入口」 | RT-N/A（唯一实现） | C→B(worker/CLI) | T2 | A-ISO | D-KEEP（唯一实现不删） | pending |
| `src/cccp/calculation/{requests,results,_common,context,errors,selection,progress,batch}.py` | `C直写` | 契约/批量执行器；T2 台账逐工作流「契约」 | RT-N/A | C→B | T2 | A-ISO | D-KEEP | pending |
| `src/cccp/calculation/{irc_endpoints,irc_trajectory,optimization_trajectory,frequency_parse}.py` | `C直写` | T2 台账 irc/optimize/frequency/tsmode「科学 helper」 | RT-N/A | C→B | T2 | A-ISO | D-KEEP | pending |
| `src/cccp/qc/**`、`src/cccp/backends/**`、`src/cccp/software.py`、`src/cccp/config.py`、`src/cccp/utils/constants.py`、`src/cccp/core/candidates.py` | `C直写` | Software 物理驻点（计划 §1）；T34/T56 | RT-N/A | C→B | T2 | A-ISO | D-KEEP | pending |
| **新增** `src/cccp/analysis/structure.py`、`src/cccp/analysis/identity.py` | `C直写` | T52/T53 唯一目标裁定（Structure 族 / identity 纯算法，T2 图 A） | RT-SAME（旧 `acp.core.models`/`acp.calculations.identity` 转同对象） | C→B | T2 裁定、T52/T53 | A-ISO + A-DIR | D-KEEP | pending |
| **新增** `src/cccp/calculation/products/*`（T11/T12 版本化产品 schema）、`src/cccp/analysis/bounded/*`（T31–T33 分块/抽样/多分辨率） | `C直写` | 计划 C02/C06；科学 schema 归 C（B 表第 5 行） | RT-N/A（新契约） | C→B(T13 adapter)→A | T11/T12、T31 | A-DIR | D-KEEP | pending |
| **新增** `src/cccp/plugin/**`（T44–T47 SDK/发现元数据）、`src/cccp/py.typed` + cccp 打包输入（T39） | `C直写` | 计划 C08/C09；独立发行 | RT-N/A | C→B(共享打包集成) | T18/T56、T39 | A-DIR | D-KEEP | pending |

### 4.1 C01 首片科学闭包（T7–T9；首片目标待 T3 裁定，按默认候选序 Confsearch→NMR→PES）

> 本组文件均为**混合 acp 文件**：C 只产补丁，交 B 应用（B 为接入写者；登记移交后才转移写权）。

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/acp/confsearch/protocols/xtb_crest.py` | `B补丁` | T2 Confsearch「引用清单」lazy `:26`；C04 采样/协议闭包 | RT-CONV（协议 runner 改调 cccp 任务核） | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/protocols/censo_crest.py` | `B补丁` | T2 lazy `:39,:65`（消费 ensemble/energy） | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/protocols/xtb_md.py` | `B补丁` | T2 lazy `:32-42`（消费 xtbmd_md/xtbmd_censo_energy） | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/protocols/xtbmd_censo.py` | `B补丁` | T2 lazy `:31` | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/protocols/_common.py`、`__init__.py` | `B补丁` | T2 static `protocols/__init__:26-29`；registry-string 分发 | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/engine.py` | `B补丁` | T2 registry-string `:141`；协议分发 | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/contracts.py` | `B补丁` | T2 图 A（`PURE_XTB_PROTOCOLS`/`ConformerEntry`/`ConfsearchRequest`/`ProtocolOutcome`） | RT-CONV（数据类型身份保持） | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/confsearch/result_helpers.py` | `B补丁` | T2 图 A `:10` | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/manifest.py` | `B补丁` | T2 平台 I/O 写 `confsearch_manifest.json` `:26-29,116-129` | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/confsearch/report.py`、`profiles.py` | `B补丁` | T2 static `report:21-24`、`profiles:12`；catalog 平台面 | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/ensemble.py`、`energy.py`、`energy_shared.py`、`ensemble_thermo.py`、`xtbmd_md.py`、`xtbmd_censo_energy.py` | `B补丁` | T2 Confsearch「retired 复用」；T6 处置；退休引擎活跃闭包 | RT-CONV（保留必要入口，不复活 CLI） | C→B | T3/T6 | A-PATCH + A-DIR | D-NOSHIM（仅活跃科学闭包迁出后） | pending |
| `src/acp/workflows/__init__.py`、`_helpers.py` | `B补丁` | T2 dynamic-caller PEP 562 `:41-73`；`_helpers` 平台 I/O | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/cli.py` | `B补丁` | T2 各工作流 CLI handler（retired 拦截 `_CLI_REMOVED_WORKFLOWS`） | RT-CONV（handler 改调新闭包） | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP（CLI 唯一入口保留） | pending |
| `src/acp/workflows/registry.py` | `B补丁` | T2 registry-string `:31-156`；15 active | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |

### 4.2 C02 科学产品契约与 adapter（T11–T14）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/acp/results/frames.py`（`TrajectoryFrame`/`VIEW_REGISTRY`/`to_node`/`to_annotation`） | `B补丁` | T2 C02 引用；ANTI #21；B adapter 写权 | RT-CONV（B 写 adapter，不改 VIEW_REGISTRY 契约） | C(映射表)→B | T11/T12 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/results/manifest.py`、`structure_policy.py`、`frame_candidate_store.py`、`pes_scan_live.py` | `B补丁` | T2 各工作流「平台 I/O」；`results/` 混合文件 | RT-CONV | C→B | T11 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/core/models.py`（`Structure`/`StructureRecord`/`StructureEnsemble`/`HARTREE_TO_KCAL:16`） | `B补丁` | T2 图 A 科学基础类型；T52 前移 `cccp.analysis.structure` | RT-CONV（旧导入返回新对象） | C→B | T52 | A-PATCH + A-DIR | D-KEEP（TYPE_CHECKING 反向引用 cccp，见 `models.py:26`） | pending |
| `src/acp/calculations/contracts.py` | `B补丁` | T2 图 A identity 依赖 `:31-38` | RT-CONV | C→B | T53 | A-PATCH + A-DIR | D-KEEP | pending |

### 4.3 C03 结构摄入 / ORCA 只读解析 / 振动（T16–T19）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/acp/chem/embedding.py` | `C直写` | **移交**：T2 BatchOptimize/摄入科学 helper；C03 迁移源；计划 §1「登记移交的 acp 科学专属文件」 | RT-SAME（`acp.chem.embedding` re-export cccp 实现） | C→B(旧路径接线) | T16 | A-ISO + A-DIR | D-KEEP（旧路径保留转发） | pending |
| `src/acp/chem/composition.py` | `C直写` | **移交**：T2 BatchOptimize `normalize_recalc_hess`；`hessian_policy.py:12` 已 re-export 提示 | RT-SAME | C→B | T16 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/intake/parsers.py`（6 格式解析） | `C直写` | **移交**：T2 PESsearch 平台 I/O `scan.py:575`；C03 迁移源 | RT-SAME（坏行/多帧语义保持） | C→B | T16 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/intake/models.py` | `C直写` | **移交**：C03 | RT-SAME | C→B | T16 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/intake/storage.py` | `B补丁` | 平台路径逻辑（T16 Must NOT：不动） | RT-N/A | C→B | T16 | A-PATCH | D-KEEP | pending |
| `src/acp/results/orca_parser.py`（`OrcaCalculation`） | `B补丁` | T2 图 A `results/frequencies.py:8`；**`results/` 为混合文件**（T5 规则）；C03 迁移源 | RT-CONV（`OrcaCalculation` 同类身份） | C→B | T16 | A-PATCH + A-DIR | D-NOSHIM（B 应用后） | pending |
| `src/acp/results/frequencies.py` | `B补丁` | T2 frequency「科学 helper」`frequencies.py:13,114`；混合文件 | RT-CONV（原生模式升序/零模保留） | C→B | T16 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/io/structures.py` | `B补丁` | T2 图 A `:20`（`from acp.core.models import Structure`）；`io/` 混合 | RT-CONV | C→B | T16/T52 | A-PATCH + A-DIR | D-KEEP | pending |

### 4.4 C04 采样 / PES / IRC / TS 科学（T20–T24）

> **移交 C直写**（科学专属）：confsearch 采样/筛选/shared、PES 纯科学、tsmode/irc 纯科学验证。**其余混合**：engine/outputs/review/scan/contracts/profiles 及 workflows/legacy_adapters → `B补丁`。

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/acp/confsearch/sampling.py`、`sampling_models.py` | `C直写` | **移交**：T2 Confsearch 科学 helper；C04 迁移源 | RT-SAME（采样顺序/盆/饱和度语义保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/confsearch/selection.py` | `C直写` | **移交**：T2 图 A `:12`（`REFINEMENT_POLICIES`/`ConformerEntry`） | RT-SAME（筛选边界不改） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/confsearch/shared/__init__.py`、`artifacts.py`、`boltzmann.py`、`deduplication.py`、`helpers.py`、`provenance.py` | `C直写` | **移交**：T2 Confsearch 科学 helper `shared/*`；`boltzmann.py:8,10` R 常量 | RT-SAME（权重/能量参考保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/atom_mapping.py` | `C直写` | **移交**：T2 PES 科学 helper；`generated_by` 输出内容（`:588`）不计反向依赖 | RT-SAME（原子映射/拓扑来源保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/bond_changes.py` | `C直写` | **移交**：T2 PES 科学 helper | RT-SAME | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/path_analysis.py` | `C直写` | **移交**：T2 PES 科学 helper；`:20` 截断 `627.509`（迁移期禁改，见 numeric-baseline） | RT-SAME（弧长/RMSD/导数保持；常量原值） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/path_selection.py`、`candidates.py`、`validation.py`、`atom_selection.py` | `C直写` | **移交**：T2 PES 科学 helper | RT-SAME（候选身份/排序保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/tsmode/mode_mapping.py`、`source.py`、`validation.py` | `C直写` | **移交**：T2 tsmode 科学 helper；C04 明确 `{mode_mapping,source,validation}` | RT-SAME（原生模式编号/映射保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/irc/source.py`、`validation.py` | `C直写` | **移交**：T2 irc 科学 helper；C04「IRC/TS 纯科学验证」 | RT-SAME（端点分类保持） | C→B | T20 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/identity.py` | `C直写` | **移交**：T2 图 A / T53 唯一目标 `cccp.analysis.identity` | RT-SAME（fingerprint 纯算法，旧导入返回新对象） | C→B | T53 | A-ISO + A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/{engine,scan,scan_snapshot,review,outputs,contracts}.py` | `B补丁` | T2 PES 平台 I/O/manifest 写；人工 review 身份（C04 留 B） | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/calculations/tsmode/{engine,contracts}.py` | `B补丁` | T2 tsmode checkpoint/manifest 发布；`contracts.py` 平台契约 | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/calculations/irc/contracts.py` | `B补丁` | T2 irc 契约（`_publish` 边界） | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/pes_search.py` | `B补丁` | T2 PESsearch workflow 编排 | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/tsmode.py` | `B补丁` | T2 tsmode workflow 编排 | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/irc.py`、`src/acp/workflows/simple.py` | `B补丁` | T2 irc/scan/casscf/simple 编排（`simple.py:26` re-export irc；`:724 __all__`） | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/xtb_path.py` | `B补丁` | T2 XtbPathSearch workflow `:634`；消费 PES2TS 冻结 payload | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/orca_gradient.py` | `B补丁` | T2 OrcaGradient workflow `:500`；消费 PES2TS 冻结 payload | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/xtb_constrained_opt.py` | `B补丁` | T2 XtbConstrainedOpt workflow `:547`（`validate_request:276`） | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/calculations/legacy_adapters.py` | `B补丁` | T2 pes2ts payload→TaskRequest 适配（xtb_path/orca_gradient/xtb_constrained_opt） | RT-CONV | C→B | T20 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/calculations/primitives/{singlepoint,optimize,frequency,thermochemistry,scan,irc,casscf,_common,irc_trajectory,optimization_trajectory}.py` | `B补丁` | T2 各工作流转发层（纯兼容转发 → cccp.calculation 任务核，ANTI #17） | RT-CONV（纯转发，零业务逻辑） | C→B | T20 | A-PATCH + A-DIR | D-KEEP（shim 保留） | pending |
| `src/acp/calculations/batch/{engine,models,loaders,_items,_manifest,_tag,_xyz,options,profiles,effective_config,singlepoint,_singlepoint_execution,_singlepoint_models,_singlepoint_frames}.py` | `B补丁` | T2 BatchOptimize 科学 helper + 平台 I/O；BatchOptimizeEngine | RT-CONV | C→B | T22 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/workflows/batch_optimize.py` | `B补丁` | T2 BatchOptimize workflow 编排 | RT-CONV | C→B | T22 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/core/stage_labels.py`、`core/workflow.py`、`core/paths.py` | `B补丁` | T2 平台 I/O（`batch/engine.py:78` schema；paths run_root） | RT-CONV | C→B | T22 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/calculations/{result_publication,step_result,checkpoint,progress,executor}.py` | `B补丁` | T2 平台发布/checkpoint/dispatch（`executor.py` 单步 dispatch） | RT-CONV | C→B | T11 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/storage/manifest.py`、`layout.py` | `B补丁` | T2 各工作流平台 I/O（v2 manifest 写） | RT-CONV | C→B | T11 | A-PATCH + A-DIR | D-KEEP | pending |
| `src/acp/catalog.py`（`WORKFLOW_CATALOG`/`METHOD_META`/`METHOD_SCHEMAS`） | `B补丁` | T5 混合文件（catalog）；B06 平台面 | RT-CONV | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |

### 4.5 C05 NMR 科学（T25–T30）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/acp/nmr/__init__.py` + 24 模块（`probability,error_model,dp5q_stub,averaging,scaling,assignment,iterative_assignment,equivalence,fchl,spectra,spectra_registry,carbon_processor,proton_processor,report,protocol,reference_validation,analysis_revision,atomic_diagnostics,models,io,structure_map,enumerate,shift_predictor,method_config`） | `C直写` | **移交**：T2 nmr「科学 helper」25 模块；C05 迁移源；B 表第 5 行（科学 schema= C） | RT-SAME（旧公开导入同类身份/序列化；DP5q/ShiftPredictor 保持隔离） | C→B | T25/T55 | A-ISO + A-DIR | D-KEEP（旧路径保留转发；DP5q 隔离面不删） | pending |
| `src/acp/nmr/models/`（8 资产：`atomic_reps.gz`,`frag_reps.gz`,`i_w_kde_mean_s_0.025.p`,`folded_scaled_errors.p`,`c_w_kde_mean_s_0.025.p`,`tms_references.txt`,`LICENSE-DP5`,`NOTICE.md`） | `C直写` | **移交**：T2 模型资产 65,513,350 B；C05 同批迁移；T55 资产统一入口 | RT-N/A（资产路径 + 许可/notice 保持） | C→B(打包) | T25/T55 | A-DIR | D-KEEP（资产不删；许可/notice 保留） | pending |
| `src/acp/workflows/nmr.py` | `B补丁` | T2 nmr 编排（直连任务核，绕过 primitives）；平台 I/O | RT-CONV | C→B | T27 | A-PATCH + A-DIR | D-KEEP | pending |

### 4.6 C06 有界大数据产品（T31–T33）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| **新增** `src/cccp/analysis/bounded/*`（分块/抽样/多分辨率；派生身份=输入摘要+算法+范围+分辨率+schema） | `C直写` | 计划 C06；C07/T31–T33 | RT-N/A（新契约） | C→B(Data 块索引)→A | T11/T12 | A-DIR | D-KEEP | pending |
| `src/acp/confsearch/sampling_models.py`（`sampling_history_v1`）、`src/acp/results/frame_candidate_store.py` | `B补丁`/`C直写` | T2 采样历史帧身份；`sampling_models.py` 移交 C，`results/` 混合 | RT-CONV | C→B | T31 | A-PATCH/A-DIR | D-KEEP | pending |

### 4.7 C07 C0 类型/常量/配置/Software（T34–T38）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `src/cccp/utils/constants.py` | `C直写` | 唯一定义 `HARTREE_TO_KCAL`/R 同精度项（ANTI #9） | RT-SAME（旧导出名 re-export 同值对象） | C→B | T34 | A-DIR | D-KEEP | pending |
| `src/acp/core/models.py:19`（`HARTREE_TO_KCAL`）、`src/acp/results/energy_graph.py:24` | `B补丁` | T2/T4 同值常量副本；随 C04/C07 收敛 | RT-SAME（re-export 新单点） | C→B | T34 | A-PATCH + A-DIR | D-NOSHIM | pending |
| `src/acp/confsearch/shared/boltzmann.py:8,10`（R 同精度项） | `C直写`（随 4.4 移交） | T4 R 常量四精度副本；同精度项收敛 | RT-SAME | C→B | T34 | A-DIR | D-KEEP | pending |
| `src/acp/calculations/pes/path_analysis.py:20`（`627.509` 截断）、`src/acp/workflows/nmr.py:1088,1952`（R 不同精度） | `B补丁` | T4 **不同精度项禁合并**（numeric-baseline） | RT-N/A（原值保留） | C→B | T34 | A-PATCH | D-KEEP（禁止变更） | pending |
| `src/acp/core/config.py`、`src/cccp/config.py` | `B补丁`/`C直写` | C07 科学 config 与平台配置分离 | RT-CONV | C→B | T35 | A-PATCH/A-DIR | D-KEEP | pending |

### 4.8 C08/C09 独立发行与插件（T39–T47）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `pyproject.toml` | `B请求` | 共享打包（B 为集成写者）；C08 交精确打包改动 | RT-N/A | C(请求)→B | T39 | B 应用后 `pytest` + `python3.11 -m build` 证据 | D-KEEP | pending |
| `requirements-node.txt` | `B请求` | 远端节点运行时依赖同步（T42） | RT-N/A | C(请求)→B | T42 | `pip install -r` 于节点环境 PASS | D-KEEP | pending |
| `src/cccp/py.typed`、cccp `LICENSE`/`NOTICE`、发行清单 | `C直写` | C08 独立 wheel/资产/许可 | RT-N/A | C→B(集成) | T39 | A-DIR + wheel 安装证据 | D-KEEP | pending |
| `src/cccp/plugin/**`（发现元数据/启用/加载边界/SDK/示例） | `C直写` | C09 一个可信插件样板 | RT-N/A | C→B | T18/T56 | A-DIR + 契约检查 | D-KEEP | pending |

### 4.9 科学测试 / fixture / 证据（C直写）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `tests/test_cccp_*.py`（34 文件：任务核/契约/batch/isolation 等） | `C直写` | 科学专属测试（B 表第 5 行） | RT-N/A | C→B(消费) | 各切片 | A-DIR | D-KEEP | pending |
| `tests/test_pes_e2e_propylene.py`、`test_hess_file_real_fixtures.py`、`test_cccp_geometry_tools.py`、`test_acp_irc_trajectory.py`、`test_acp_frequency_modes.py` | `C直写` | T4 黄金源/数值基线 | RT-N/A | C→B | T4/T20 | A-DIR | D-KEEP（黄金值不覆盖） | pending |
| `tests/test_acp_intake*.py`、`test_acp_chem_composition.py`、`test_acp_workflows_nmr.py`、`test_acp_nmr_*.py`、`test_acp_censo_p5_acceptance.py`、`test_acp_workflows_xtbmd_censo_energy.py`、`test_acp_workflows_energy.py` | `C直写` | C03/C04/C05 迁移期定向断言（先冻结再迁移） | RT-N/A | C→B | T16/T20/T25 | A-DIR | D-KEEP | pending |
| `tests/baseline/cccp_calculation_goldens/**`、`tests/baseline/nmr/**`、`tests/baseline/recovery_fixtures/**`、`tests/fixtures/**` | `C直写` | T4 数值/容差/恢复 fixture（迁移期禁止变更） | RT-N/A | C→B/A | T4 | A-DIR | D-KEEP（不覆盖黄金值） | pending |
| `tests/baseline/cccp-evidence/**`（本表 + `handoff_registry.md`） | `C直写` | 本 T5 交付物；写权登记 | RT-N/A | C→B(引用) | T5 | 本文件完整存在 | D-KEEP | pending |

### 4.10 全局护栏 / 根文档（B请求，C 交精确请求 + 证据）

| 文件 / 符号 | 写权 | 依据 | 转发·身份·导出 | provider→consumer | 前置 | 验收 | 删除/回退 | applied_commit |
|---|---|---|---|---|---|---|---|---|
| `tests/test_architecture_invariants.py`、`tests/test_cccp_isolation.py`、`tests/test_f4_scope_audit.py`、`tests/test_grep_gates_script.py`、`tests/test_scheduler_markers_audit.py` | `B请求` | 全局架构门/共享集成测试（B 表第 7 行）；禁放宽 | RT-N/A | C(证据/修订请求)→B | T48/T50 | B 应用后门全绿；C 不静默放宽 | D-KEEP | pending |
| `scripts/check_grep_gates.py`（4 pin）、`scripts/verify_cccp_isolation.py` | `B请求` | 全局护栏脚本；pin 禁禁用 | RT-N/A | C(请求)→B | T48 | `python3.11 scripts/check_grep_gates.py` PASS | D-KEEP | pending |
| 根 `README.md`、`AGENTS.md`（root + nested）、`docs/**`（含主方案、索引、Plan_3） | `B请求` | 根文档/主方案（B 表第 7 行） | RT-N/A | C(请求)→B | 各波 | B 应用后文档一致 | D-KEEP | pending |
| `src/acp/scheduler/{stage_tasks,registration,job_edit,store,events}.py`、`scheduler/remote/script_gen.py` | `B补丁` | T2 registry-string/动态 provider/远端允许集；平台调度 | RT-CONV（保留调度预建文件全集，ANTI #11） | C→B | T3/T7 | A-PATCH + A-DIR | D-KEEP | pending |

## 5. 覆盖交叉核对（T2 台账 acp 文件 ↔ 本表行）

- T2 台账（`c00-closure-ledger.md`）中**完整限定**的 `src/acp/**/*.py` 文件 token = **42**（唯一）。本表 §5.1 逐行 verbatim 列出这 42 个文件并给写权三态 → **差集 = 0**。
- 本表另**展开**台账用包缩写引用的 acp 文件（`confsearch/shared/*`、`nmr/` 25 模块、`calculations/batch/*`、`calculations/pes/*`、`calculations/tsmode/*`、`calculations/irc/*`、`workflows/` retired 六文件、`results/*`、`core/*`、`storage/*`、`scheduler/*`、`intake/*`、`chem/*`、`io/*`），故 registry 覆盖行数 > 42（含逐包展开 + cccp 目标行）。
- **spot-check（任取 3 个台账文件）**：
  1. `src/acp/confsearch/protocols/censo_crest.py` → §5.1 #19 / §4.1 `B补丁`。
  2. `src/acp/calculations/pes/atom_mapping.py` → §5.1 #7 / §4.4 `C直写`（移交）。
  3. `src/acp/nmr/reference_validation.py` → §5.1 #29 / §4.5 `C直写`（移交，nmr 25 模块）。
- 计数器与解释由 `.omo/evidence/acp-parallel-plan-3-cccp/task-5-verify.sh` 证据脚本给出（`ledger_acp_files=42 registry_missing=0`）。

### 5.1 台账完整限定文件 → 写权逐行清单（42，verbatim）

> 机器断言：以下 42 行引用的路径与 T2 台账逐字一致；写权三态取自 §4。

| # | 台账文件（verbatim） | 写权 | 本表节 |
|---|---|---|---|
| 1 | `src/acp/calculations/batch/engine.py` | `B补丁` | §4.4 |
| 2 | `src/acp/calculations/batch/singlepoint.py` | `B补丁` | §4.4 |
| 3 | `src/acp/calculations/checkpoint.py` | `B补丁` | §4.4 |
| 4 | `src/acp/calculations/executor.py` | `B补丁` | §4.4 |
| 5 | `src/acp/calculations/identity.py` | `C直写`（移交） | §4.4 |
| 6 | `src/acp/calculations/legacy_adapters.py` | `B补丁` | §4.4 |
| 7 | `src/acp/calculations/pes/atom_mapping.py` | `C直写`（移交） | §4.4 |
| 8 | `src/acp/calculations/primitives/casscf.py` | `B补丁` | §4.4 |
| 9 | `src/acp/calculations/primitives/frequency.py` | `B补丁` | §4.4 |
| 10 | `src/acp/calculations/primitives/irc.py` | `B补丁` | §4.4 |
| 11 | `src/acp/calculations/primitives/optimize.py` | `B补丁` | §4.4 |
| 12 | `src/acp/calculations/primitives/scan.py` | `B补丁` | §4.4 |
| 13 | `src/acp/calculations/primitives/singlepoint.py` | `B补丁` | §4.4 |
| 14 | `src/acp/calculations/tsmode/engine.py` | `B补丁` | §4.4 |
| 15 | `src/acp/cli.py` | `B补丁` | §4.1 |
| 16 | `src/acp/confsearch/contracts.py` | `B补丁` | §4.1 |
| 17 | `src/acp/confsearch/engine.py` | `B补丁` | §4.1 |
| 18 | `src/acp/confsearch/protocols/__init__.py` | `B补丁` | §4.1 |
| 19 | `src/acp/confsearch/protocols/censo_crest.py` | `B补丁` | §4.1 |
| 20 | `src/acp/confsearch/protocols/xtb_crest.py` | `B补丁` | §4.1 |
| 21 | `src/acp/confsearch/protocols/xtb_md.py` | `B补丁` | §4.1 |
| 22 | `src/acp/confsearch/result_helpers.py` | `B补丁` | §4.1 |
| 23 | `src/acp/confsearch/selection.py` | `C直写`（移交） | §4.4 |
| 24 | `src/acp/core/models.py` | `B补丁` | §4.2 |
| 25 | `src/acp/io/structures.py` | `B补丁` | §4.3 |
| 26 | `src/acp/nmr/analysis_revision.py` | `C直写`（移交） | §4.5 |
| 27 | `src/acp/nmr/error_model.py` | `C直写`（移交） | §4.5 |
| 28 | `src/acp/nmr/protocol.py` | `C直写`（移交） | §4.5 |
| 29 | `src/acp/nmr/reference_validation.py` | `C直写`（移交） | §4.5 |
| 30 | `src/acp/results/frequencies.py` | `B补丁` | §4.3 |
| 31 | `src/acp/results/orca_parser.py` | `B补丁` | §4.3 |
| 32 | `src/acp/scheduler/remote/script_gen.py` | `B补丁` | §4.10 |
| 33 | `src/acp/scheduler/stage_tasks.py` | `B补丁` | §4.10 |
| 34 | `src/acp/workflows/__init__.py` | `B补丁` | §4.1 |
| 35 | `src/acp/workflows/irc.py` | `B补丁` | §4.4 |
| 36 | `src/acp/workflows/nmr.py` | `B补丁` | §4.5 |
| 37 | `src/acp/workflows/orca_gradient.py` | `B补丁` | §4.4 |
| 38 | `src/acp/workflows/registry.py` | `B补丁` | §4.1 |
| 39 | `src/acp/workflows/simple.py` | `B补丁` | §4.4 |
| 40 | `src/acp/workflows/tsmode.py` | `B补丁` | §4.4 |
| 41 | `src/acp/workflows/xtb_constrained_opt.py` | `B补丁` | §4.4 |
| 42 | `src/acp/workflows/xtb_path.py` | `B补丁` | §4.4 |

## 6. 未决与风险

- **首片目标未定**（T3 未完成）：§4.1 按默认候选序（Confsearch）预留；T3 裁定后按 §12.1 单切片切换更新该组行。
- **`results/`、`io/`、`core/` 混合文件内的科学函数**（`orca_parser.py`/`frequencies.py`/`models.py`）：本表按 T5 规则记 `B补丁`（混合文件永不直改）；若 B 按 B 表第 4 行「登记移交后才转移文件写权」将其登记移交，则改记 `C直写` 并回填 `applied_commit`。
- **`applied_commit` 全列 `pending`**：C 尚未开始 T7+ 实施；本表在 C 提交补丁时记 `delivered`（provider 基线），B 应用后记 `integrated`（consumer 提交）。
- **科学软件真实运行**一律 `NOT_VERIFIED`（T2 口径），登记表不将其计为验收通过。
