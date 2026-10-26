# ACP 仓库健康度调查报告

**日期**: 2026-10-26 · **HEAD**: `ec4a0b3`（分支 `feat/frontend-plan1-closure`）
**方法**: 5 路并行只读勘察（cccp / acp / acp_services / tests / 非源码区）+ 全仓 import 可达性分析 + git 历史与对象分析；关键论断均已交叉复核。

---

## 1. 执行摘要

**总体判断：架构没有腐坏，腐坏的是"节奏"。** 四层架构（cccp→backends→calculation→acp）与 Services 单向依赖有 AST 级硬门守护，全仓无第二实现（≥20 行函数体 AST 规范化比对 0 命中），测试是真实高密度代码（≈9,600 个测试函数、平均 3.8 条断言、仅 0.7% 无断言）。**这不是一座无序的屎山，而是一座被护栏围住、但在 85 天 1,115 次 commit 的冲刺节奏下"只增不减"快速沉积的城池。**

真正的负债集中在五处：

| # | 问题 | 量级 |
|---|---|---|
| ① | **仓库当制品库**：8 个 wheel/tar 发行制品被提交进 git（commit `324d223`，message 自述 "make HEAD self-contained"），外加 ~985MB 零引用的未跟踪验收残渣 | `.git` 因此 315MB；reports/ 磁盘 1.2GB |
| ② | **只增不减的退役面**：14 个退役工作流在 catalog/cli/script_gen/stage_tasks/前端继续维持分支；Services 层 7,000+ 行"已交付未接线"能力 | 可清理残余 ≈2,900 行；待决策 ≈9,000 行 |
| ③ | **上帝模块**：`v1_routes.py` 8,970 / `manager.py` 7,051 / `cli.py` 5,478 / `validators.py` 4,689 | 4 文件 ≈26,000 行（acp 包 14% + cccp 单文件之最） |
| ④ | **函数/常量级复制**：`HARTREE_TO_KCAL` ×4（其中 1 份截断值不同）、`boltzmann_weights` ×3、`mixing_entropy` 逐字复制、`_utc_now_iso` ×9、双解析栈 ×2 | 违反自家 ANTI #9 |
| ⑤ | **文档漂移**：AGENTS.md 的文件数/行数普遍滞后 1.4–2×（tests 声称 248 文件，实际 481） | 全部嵌套知识库受影响 |

---

## 2. 仓库全景：真实规模

### 2.1 代码体量（git tracked，实测）

| 区域 | .py 文件 | 行数 | 备注 |
|---|---:|---:|---|
| `src/cccp` | 202 | 100,525 | QC 底座（根 AGENTS.md 称"≈86 .py"，滞后 2.3×） |
| `src/acp` | 289 | 123,587 | 工作流/规划/发布层 |
| `src/acp_services` | 99 | 61,614 | 平台服务层（文档称"约 70"，滞后 1.4×） |
| **src 小计** | **590** | **285,726** | |
| `tests/` | 532 | 287,928 | 其中 481 个 `test_*.py` / 267,681 行 ≈ **全仓 Python 行数 42%** |
| `scripts/` | 49 | 48,557 | 含 16,909 行验收 harness |
| `frontend/` | — | 77,759 | 113 文件，js/workbench 模块化 v2 |
| `docs/` | — | 312,274 | 525 tracked 文件，**比 src 还多 9%** |
| `reports/` | — | 921,413 | 1,502 tracked 文件 / 194.3MB，**其中 179MB 是二进制制品** |

### 2.2 磁盘占用（`du -sh`）

```
reports/      1.2G   ← 194MB tracked + ~985MB 未跟踪验收残渣
.git/         315M   ← loose 224MB + pack 76MB
.omo/         173M   ← 本地 agent 数据（已 ignore）
build/        135M   ← 2026-09 旧 build/lib 源码副本（已 ignore，可删）
src/          103M   ← 其中 63MB 是 DP5 模型二进制资产
.codegraph/    77M   ← 本地索引，仅 .git/info/exclude 保护（换机即失效）
tests/         72M   ← 其中 54MB 是 __pycache__（真实入仓仅 13.9MB）
rph/           26M   ← 25MB node_modules
```

### 2.3 开发节奏（膨胀的直接驱动）

- 仓库始于 **2026-08-02，寿命仅 85 天**，总 commit 1,115，其中 **1,076 集中在最近 60 天**。
- tests 近 60 天 **+275,675 行**（≈4,600 行/天）；churn 之王 `frontend/ACP_Workbench_v2.html` 有 **140 个历史版本、合计 142.4MB 逻辑量**（.git 第一大非制品膨胀源）。
- 当前在特性分支 `feat/frontend-plan1-closure`，非 main。

---

## 3. src/cccp（QC 底座，10.1 万行）

### 3.1 构成

`analysis/` 87 文件 47,979 行（占 48%，nmr 20,337 + research 12,581 + pes 4,894）· `calculation/` 50 文件 27,109 行（唯一执行驻点）· `qc/` 25 文件 13,788 行 · `plugin/` 8 文件 3,671 行（**根 AGENTS.md 完全未收录的子包**）· 其余 backends/utils/core/io/pipeline ≈9,000 行。

### 3.2 死代码与休眠代码（import 可达性 BFS：43/202 模块不可达 = 30,258 行，30%）

**真·死代码（≈2,384 行，全仓含测试零引用，已逐项核实）**

| 模块 | 行数 | 证据 |
|---|---:|---|
| `calculation/xtbmd_adapters.py` | 1,017 | 唯一命中是 `tests/_t24_patch_compat.py:49-60` 的 mock.patch 字符串靶；功能已被 `acp/workflows/xtbmd_censo_energy.py` 直连 `cccp.calculation.tasks.optimize` 取代 |
| `analysis/sampling/pipeline.py` | 500 | 全仓零引用（`grep sampling.pipeline` 唯一命中是散文注释） |
| `core/state_manager.py` | 293 | 仅 `core/__init__.py` re-export，零消费者 |
| `analysis/xtbmd/{energy_filter,trajectory}.py` | 468 | 仅被上面的死者引用（连带簇，1,485 行） |
| `pipeline/`（整包） | 106 | `__init__.py:6` 自标 "DEPRECATED … No importers"；全仓引用仅剩 docstring 散文与自身 |

**自标记 dormant（活着但无生产路径）**：`core/protocols.py` 643 行（docstring 自述 "the conformer-search pipeline this module served is dormant"；`SUPPORTED_PROTOCOLS` 里还挂着 retired 的 `benchmark`）。

**已交付未接线（非死码，但生产零消费）**：
- `plugin/` 3,671 行 — T44 插件 SDK，`pyproject.toml` **未注册**其声明的 `cccp.science_plugins` entry-point 组 → 生产环境永远扫不到插件，只有 fixture 测试在用。
- `analysis/bounded/` 1,985 行、`analysis/research/validators.py` 4,689 行 + `selectivity.py` 1,832 行 — ACP2 科学断言层，消费方只有测试 + `scripts/acceptance/acp2_science.py`（14 处），`src/` 内零调用。

**必留大资产**：`analysis/nmr/model_assets/` 63MB（DP5 模型，ANTI #31 明令保留；但 `pyproject.toml:87` 残留指向已空目录 `src/acp/nmr/models/` 的 glob，注自待 T39 删）。

### 3.3 复制与双实现（违反自家 ANTI #9）

| 项 | 位置 | 性质 |
|---|---|---|
| `HARTREE_TO_KCAL` ×4 | `utils/constants.py:6`（正版）/ `acp/core/models.py:19` / `acp/results/energy_graph.py:24` / **`analysis/pes/path_analysis.py:20 = 627.509（截断，值不同！）** | 最危险的一处 |
| k_B / R 内联 ×8 | `qc/interfaces/censo.py:76`、`analysis/confsearch/boltzmann.py:25`（截断 0.001987204258）、`acp/workflows/{energy_shared,ensemble_thermo,xtbmd_censo_energy,nmr}.py` 等 | 正版 `GAS_CONSTANT_KCAL_PER_MOL_K` 几乎无人用 |
| `mixing_entropy` | `analysis/ensemble/thermo.py:27` vs `acp/workflows/ensemble_thermo.py:45` | **逐字复制**（含 docstring） |
| `boltzmann_weights` ×3 / `select_cumulative_boltzmann` ×3 | `analysis/ensemble/selection.py` vs `acp/workflows/energy_shared.py:842-854` vs `acp/confsearch/shared/helpers.py:70`（注释自认"保持本地副本"） | 与"唯一实现根"口径相悖 |
| 双 ORCA 聚合解析 | `cccp/analysis/orca_parser.py`(242，自述唯一实现) vs `acp/results/orca_parser.py`(434，自带 regex mirror) | 两栈并存 |
| 双结构解析栈 | `io/input_handler.py`(462，旧) vs `analysis/structure/parsers.py`(850，新) | 两条都活：cli/_common 走旧，chem/intake 走新 |

### 3.4 旧风格代码（全部集中在 cccp，另两包干净）

`typing.Optional` 131 处/16 文件 · `Author: QCcalc Team` 91 文件（45%）· `====` 旧 docstring 81 行 · 裸 `except Exception:` 64 处（重灾 `analysis/structure/embedding.py` 10、`analysis/pes/atom_mapping.py` 9）· 模块级 `# pyright:` 整段压制 28 文件。

---

## 4. src/acp（工作流层，12.4 万行）

### 4.1 上帝模块（结构债核心，4 文件 26,088 行）

| 文件 | 行数 | 体检 |
|---|---:|---|
| `api/v1_routes.py` | 8,970 | 261 函数 / 104 端点 / 196 import；混装鉴权、job 视图、机制报告、PES 审阅、能量图、振动、结构查看器、候选 CRUD、远程文件……文件头 20 项 pyright 压制 |
| `scheduler/manager.py` | 7,051 | `JobManager` 单类 213 函数；`__init__` 197 行、`_submit_job` 260 行、`_inplace_requeue_locked` 200 行；**53 处 `except Exception:`（全仓最密）** |
| `cli.py` | 5,478 | 7 类职责混装；`build_parser` 单函数 825 行；**515 行退役 argparse 定义不可达**（`main()` 在 build 之前就拦截） |
| `catalog.py` | 5,270 | 4,100 行是静态数据表（FIELD_DEFINITIONS 1,441 + METHOD_SCHEMAS 2,110），逻辑仅 ~1,000 行；**含 690 行退役专属 schema** |

### 4.2 兼容 shim 面（≈9,000 行，**不是屎山，删了破契约**）

`acp/backends/` 719 行（13 个纯 re-export + 被隔离的 `batch.py` 492）· `calculations/primitives/` 2,802 行（转发 + `_common.py` 419 行适配胶水）· `compat/legacy/` 734 行只读 reader（**其中 `batch_loaders.py` 204 行仅测试引用**）· identity forwarder 8 文件 116 行 · `services_*bootstrap.py` 6 文件 3,731 行（**`services_data_bootstrap.py` 2,470 行已含实质编排逻辑，超出"薄装配"定位**）。全部受 `public_shim_inventory.md` + ANTI #31 保护。

### 4.3 退役工作流残余（"只增不减"的典型样本，14 retired + 1 planned）

- **catalog**：退役专属 METHOD_SCHEMAS 690 行（optfreq/optfreqsp/mech_*/low/high_confirm 440 行零引用 + censo_ensemble/censo_energy 250 行外部零引用）。
- **cli**：不可达退役解析器 ~515 行 + 26 行 stub；拦截表 `_CLI_REMOVED_WORKFLOWS` 12 项 < catalog 14 项（conformer/benchmark 漏列）。
- **引擎模块 5,531 行不是死码**：`workflows/{xtbmd_censo_energy,energy_shared,energy,ensemble,ensemble_thermo,xtbmd_md}.py` 被 Confsearch 四协议与 nmr 工作流直接复用。
- **散点**：退役 id 字面量 145 处（`energy_graph.py` 20、`cli.py` 17）；`script_gen.py:140-155` 远程白名单仍放行退役工作流；`stage_tasks._XtbmdCensoEnergyStagePlanProvider`；前端 40 处兼容分支（22 处非测试）。
- **API 历史面**：`mechanism_readonly` 179 行 + `v1_routes` 机制端点 ~388 行 + legacy S2 面 ~196 行（前端历史视图仍在调用，非纯死码）。

### 4.4 死代码候选（≈950 行）

`results/frames_adapter.py` 316 行（`project_science_product` 生产零调用，仅测试）· `storage/backend.py` 385 行（`NodeAgentStorageBackend` 注释自述 "not yet deployed"）· `compat/legacy/batch_loaders.py` 204 行（仅测试）· `nmr/{proton,carbon}_processor.py` 32 行空别名 · `primitives/_thermochemistry_support.py` 30 行。

### 4.5 其它

`init_wizard/` 4,418 行在 root/nested 两级 AGENTS.md 结构图中**双双缺席**（文档漂移实例）。`except Exception` 276 处，正当性台账只覆盖 Services 引入的 20 处，legacy 面 250+ 处无台账。`api/routes.py` 1,107 行的 `/api` 根面与 v1 重叠但前端仍用 `/api/backends` → 半 dead 不可直删。

---

## 5. src/acp_services（服务层，6.2 万行）

**过渡状态：内核迁完，重心已从"代码迁移"转为"接线"。**

- 台账 `migration_ledger.md`：**57 CLOSED / 5 RETAINED / 10 OPEN**（79% 收口）；L1 模块验证 18/18 PASS，L2 默认入口 10/17，L3 真实环境 4/10。
- `ServicesPorts` 10 端口 **9 个仍指向旧 acp 实现**（tasks/task_views/control/planning/publication/catalog/remote×3），DEFERRED 8 条（控制/提交/编辑/CLI 登记/store 转移/plan 执行/Data writer/catalog 归一化）。
- **无双根**：同 basename + AST 函数体全等双扫描 0 命中；3 处职责分裂（读取根/数据写/任务视图）均已在台账登记。
- **7,000+ 行已交付零生产消费**：`runtime/quota.py` 1,934 · `capability_registry.py` 1,158 · `research_events.py` 1,151 · `step_dag.py` 1,099 · `provisioning.py` · `metering_decoupling.py` · `workflow_enablement.py` 311 · `adapter_bootstrap.py` 85 · `workspace/admin.py` · `data/budget.py` 367。这些是**未接线的冻结能力**，不得改写成"已接入"（ANTI #32），也不宜删。
- 唯一死码候选：`acp_services/events.py` 53 行纯 re-export（src/tests 双向 0 引用，但被 allowlist 冻结）。
- `worker_bootstrap.py` 1,037 行独立组合根完整（零 acp import、零 jobs DB），唯一跨包依赖是 cccp.analysis.identity（符合 acp-worker 角色定义）。
- **文档漂移**：closure 把已绑定的 MCP 入口（`acp mcp serve`，cli.py:2397）仍记为 NOT_VERIFIED。

---

## 6. tests/（36.4 万行）

**结论：测试是真实的，膨胀来自沉积物与测试形态。**

- 481 个 `test_*.py` / 267,681 行 / **≈9,600 个测试函数 / 36,450 条 assert（3.8/函数）/ 仅 70 个无断言（0.7%）**——无"只跑不验"空壳。
- 单 conftest（541 行），无嵌套魔法、无 sys.path hack——分层干净。

**四块膨胀源：**

1. **`baseline/refactor-evidence/` 313 文件死日志**：`ruff-baseline.txt` 7,527 行**零引用**；100 个 `NN-happy/fail.txt` 迁移快照；`e2e-task-root.txt` 47B **直接违反仓库自身 tests/AGENTS.md:44 规则**；`VERIFICATION_REPORT.md` 引用已退役的 `acp run conformer`。
2. **`baseline/cccp-evidence/` 63 文件 856KB**：handoff 文档 + walk 原始输出，无任何 .py 引用（baseline 里最大死重）。
3. **前端源码扫描型测试 13 文件 15,036 行**（`test_frontend_sync_*`，~1,000 条 `assert "字面量" in js` 断言）：断言数高、验证强度低——改变量名即红、逻辑写错仍绿。**唯一有实质瘦身空间的活代码区**（可收敛为契约表驱动，预计 -60~70% 行数）。
4. **巨型测试文件**：>2,000 行 9 个（structure viewer 族独占 3 个共 9,252 行）；>1,000 行 64 个合计 99,207 行 = 测试 LOC 的 37%。

**必须保留**（被守护测试解析，删即红）：`services-evidence/`（`test_services_doc_sync.py:43/271/479` 解析三台账）、`recovery_fixtures/` 45 文件（ANTI #31）、goldens 15、`fixtures/qc|irc`（真实 ORCA 输出，86% 引用率、`ts_opt.out` 被 7 个测试复用）。

**已知边界**：CI 从不执行真实 ORCA/CREST 二进制测试（41 处 slow 门控设计如此）；`conftest.py:205` 在导入期执行 `shutil.which` 探测，未装包时连 collect 都挂。

**文档漂移**：`tests/AGENTS.md` 声称 "248 files, ≈5.7k tests"，实际 481 文件 / ≈9.6k 测试函数（滞后 1.9×）。

---

## 7. 非源码区（**最严重的一块**）

### 7.1 reports/：1.2GB，仓库当制品库

- **tracked 1,502 文件 / 194.3MB**，其中 **179MB = 8 个 wheel/tar 发行制品**（release-fix5 的 acp/cccp/acp_web/acp_worker 0.3.0 全套）+ 4.7MB patch。系 commit `324d223`（message: "make HEAD self-contained"）等 3 次事故式提交，`.gitignore` 有 `*.log/*.out` 却**没有 `*.whl`/`*.tar.gz` 规则**。
- **危险约束**：`tests/test_acp2_runtime_h7_index.py:178-201` 对这 8 个制品做 sha256+size 断言且**无 skipif** → 直接删除制品会让测试 FAIL 而非 skip。删除须同步改测试 + 3 份台账/文档。
- **untracked ~985MB 纯验收残渣**：`acp2_final_acceptance_20261024/` 275MB + `acp2_integration_20261024/` 138MB（两者 tests/src/docs/scripts **零引用**）+ release-fix4/release/root-build 等 449MB（fix5 前身）。
- **文档指向幽灵路径**：`release-b25a-original/`（91MB，未入库）被根 AGENTS.md 与 2 份文档引用——干净 checkout 里不存在。

### 7.2 .git：315MB 成因

制品 blob 179.8MB（已压缩，gc 压不掉）+ `ACP_Workbench_v2.html` 140 版本 142.4MB 逻辑量 + 18 个失效 worktree 注册 11MB + 381 个 unreachable 对象。loose 224MB / pack 76MB。根治需 filter-repo（见 §9-E）。

### 7.3 docs/：312,274 行（比 src 还大）

- **最大重复块**：`next-change/acp2-contracts/runtime/final-debug-patch/new-files/` 144 文件 6.6MB——**整棵仓库副本**（reports 部分 40/40 抽样与 tracked reports 字节相同；docs 部分与 next-change 逐一 md5 相同；另含与 patch 语义重复的 1.55MB tar.gz）。
- 11 轮已执行完毕的并行计划评审文档（R2–R6 ×2 组）仍留在 next-change。
- `docs/archive/2026-10-16/` 6.4MB（含 2.16MB ast_inventory.json、1.54MB PNG）——已在归档层，属可接受。
- `docs/reports/` 名存实亡（只剩 2 个 pyc）。

### 7.4 根目录与杂项

`.gitignore` 缺口（`git check-ignore` 逐一验证）：`*.whl`、`*.tar.gz`、`sp.*`（根目录 ORCA 实验残留 2.1MB）、`lab.pid`、`scan_work/`（空目录）、`.codegraph/`（77MB，仅本机 exclude 保护）。`build/` 135MB 旧源码副本可删。`examples/example_inputs.txt` 教的还是已退役的 ConformerSearch 旧格式。

### 7.5 frontend 与 scripts

- `ACP_Workbench_minimal.html` 是**唯一真死页面**（无路由引用，`test_frontend_sync_shell.py:36` 明确断言不得出现）——但 `build_release.py:195` 整树打包把它和 42 个 `module_tests/*.test.js` 一起塞进了 acp_web wheel。
- `ACP_Workbench.html`（v1）仍由 `/legacy/` 路由服务，**不是死文件**；`js/vendor/3Dmol` 必留。
- `scripts/acceptance/acp2_runtime.py` 16,909 行 + `acp2_science.py` 8,317 行是"可执行验收规范"，**被测试 import，不是死码**（体大是因为 stdlib-only 约束下内联了 JSON-Schema 引擎）。

---

## 8. 分级处置清单

### A. 立即安全执行（回收 ~1.1GB 磁盘，零风险，不动 git）

| 动作 | 回收 |
|---|---:|
| 删 `reports/acp2_final_acceptance_20261024/` + `acp2_integration_20261024/`（零引用已核实） | 413MB |
| 删 `reports/acp2_implementation/.../{release-fix4,release,root-build}`（fix5 前身，零引用） | 449MB |
| 删 `build/`（旧 build/lib） | 135MB |
| 删 `__pycache__`/`.mypy_cache`/`.ruff_cache`/`.pytest_cache`（含 scripts 2.3MB） | ~48MB |
| `git worktree prune`（18 个失效注册）+ `git gc` | ~11MB |
| 清根目录残留 `sp.*`/`lab.pid`/`scan_work/`/`xtbopt.log`/`grad.engrad`/`censo.log`，并补 .gitignore 规则（`*.whl`、`*.tar.gz`、`sp.*`、`lab.pid`、`scan_work/`、`.codegraph/`） | 2.1MB + 防复发 |

### B. 低风险代码清理（~3,300 行 + 归档，每项独立小切片）

1. `cli.py` 不可达退役 argparse ~515 行（保留 `_reject_retired_workflow` 拦截）。
2. `catalog.py` 零引用退役 schema 690 行（censo_ensemble/censo_energy/optfreq 族）。
3. cccp 死码五件套 2,384 行（`pipeline/`+`sampling/pipeline.py`+`xtbmd_adapters.py`+`state_manager.py`+`analysis/xtbmd/`；`_t24_patch_compat.py` 的 mock 靶同步改）。
4. acp 死码：`nmr/{proton,carbon}_processor.py` 32 + `_thermochemistry_support.py` 30 + `batch_loaders.py` 204。
5. `script_gen.py` 退役白名单/分支、`stage_tasks._XtbmdCensoEnergyStagePlanProvider`。
6. tests：删 `e2e-task-root.txt`（违反自订规则）；`refactor-evidence` 死日志与 `cccp-evidence` 移出 pytest 路径归档（删 `migration_ledger.md` 前先确认 `test_architecture_invariants.py:586` 的注释级引用）。
7. 常量统一：`path_analysis.py:20` 的 627.509、8 处 k_B/R 内联、`_utc_now_iso` ×9 —— 直击 ANTI #9。

### C. 归档/收敛（保留但移出主干）

- `docs/.../final-debug-patch/new-files/` 去重（留 manifest + patch，6.6MB → <200KB）。
- 11 轮已执行完的评审文档移入 archive。
- `test_frontend_sync_*` 13 文件收敛为契约清单驱动（预计 -60~70%，即 ~9,000 行）。
- `release-b25a-original/` 移出仓库进制品库，文档改指向。

### D. 需要显式决策（挂账，不宜默默处理）

| 项 | 量级 | 决策点 |
|---|---:|---|
| Services 未接线能力（quota/admission/step_dag/research_events/capability_registry…） | 7,000+ 行 | 接线 or 维持 NOT_VERIFIED 冻结 |
| `results/frames_adapter.py` + `storage/backend.py` | 701 行 | 接线 or 标注后删 |
| `cccp/plugin/` entry-point 注册 | 3,671 行 | 注册启用 or 归档 |
| 双解析栈（ORCA 聚合 / 结构解析） | ~2,000 行 | 二选一收口 |
| `acp/workflows` 侧的 ensemble/thermo 复制函数 | ~200 行 | 改 shim 对齐"唯一实现根" |
| model_assets 63MB | 63MB | 迁 LFS or 接受（需协调 wheel 构建） |

### E. 长期工程（需立项）

1. **git 历史重写**（filter-repo 移除 179.8MB 制品 blob）：必须同步改 `test_acp2_runtime_h7_index.py` 的 sha256 断言（改"若存在则校验"）+ `handoff_registry.md`/`public_shim_inventory.md`/`ACP_2.0_Release_Version_List_20261023.md` 三处指向——否则违反 ANTI #31/#32 可追溯性要求。
2. **上帝模块拆分**：`v1_routes.py` 按域拆 router 文件；`manager.py` 拆状态机/staging/清理线程；`cli.py` 拆 subcommand 模块。高 churn 文件（91/62/51 commits）拆分收益最大。
3. **文档同步机制**：AGENTS.md 的规模数字改为脚本生成（现在普遍滞后 1.4–2×）。

---

## 9. 红线清单（调查中确认**不能动**的）

`release-fix5` 8 制品（sha256 断言无 skipif）· `tests/baseline/{services-evidence,recovery_fixtures,cccp_calculation_goldens,nmr,reference}`（被守护测试解析）· `model_assets/`（ANTI #31）· `acp.backends`/`primitives`/`compat.legacy`/8 个 identity forwarder（ANTI #31 公开 shim）· 退役引擎 6 模块 5,531 行（Confsearch 协议依赖）· `frontend/js/vendor/`、`ACP_Workbench.html`（/legacy/ 活路由）、`module_tests/`（测试基座）· `scripts/acceptance/` 两大 harness（被测试 import）· `docs/archive/`（已有归档层）· `packaging/roles/`（发行骨架）· 6 列 migration_ledger 及全部 NOT_VERIFIED 状态（ANTI #32：不得改写成 PASS）。

---

## 10. 结语

这个仓库的悖论是：**守护纪律越好，沉积物越难清理**——每一层兼容面、每一份证据、每一个未接线能力都有台账和测试背书，形成了"只增不减"的合法化机制。当前状态可概括为：**架构 80 分，卫生 40 分**。优先做 A（1.1GB 零风险回收）与 B（~3,300 行死码），C/D 各立项推进，E 在下次大版本切分时执行，即可在不触碰任何红线的前提下把仓库拉回健康区间。
