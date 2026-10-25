# node_dependency_sync_request.md — 节点依赖同步请求（HN-05 / T42，B请求）

- **owner:** C 路（plan-3 todo 42 · C08-s4）→ consumer: **B**（`pyproject.toml` / `requirements-node.txt` 集成写者；B06/B07 release·worker 对齐）
- **交付状态:** delivered（provider 交付提交见 `handoff_registry.md` HN-05 行 `applied_commit`）
- **快照:** 本文件证据基于 `.omo/evidence/acp-parallel-plan-3-cccp/task-42-*.md`（生成时 HEAD 见各证据头）；plan base `dfad6b3`
- **写权声明:** 按 `write_registry.md` §4.8，`requirements-node.txt` 与 `pyproject.toml` 均为 **B请求**——本文件只提交变更文本 + 证据，**未改动任何 live 文件**（live `requirements-node.txt` 保持 7 行原样）。
- **T54 衔接:** 本请求被 B 应用后的安装复验（候选版本发布前 `pip install -r` + 定向样例）由 **T54（C08-s6）** 消费——B 应用回执（applied_commit）填入权威表后，T54 以该提交为起点做候选复验。**应用结果 feed T54。**

---

## 1. 结论（TL;DR）

| # | 结论 | 证据 |
|---|---|---|
| 1 | **C03–C09 未引入任何新的节点第三方运行时依赖**——迁移模块（`src/cccp/analysis/**`、`src/cccp/plugin/**`、`src/cccp/calculation/products/**`、cccp 任务层）运行时 import 面 = {numpy, rdkit, pyyaml, scipy, matplotlib, openpyxl, nmrglue}（已在 node 清单）+ {qml, openbabel}（**故意不发节点**，惰性守卫 import 已验证） | 证据 §A1/A5/B1：AST 全量 + plan-base diff 双向扫描；`task-42-import-scan.md` |
| 2 | **补 1 条遗漏请求：`typing-extensions>=4.1`** → 同步加进 `requirements-node.txt` **和** `pyproject.toml` core deps（verbatim 同 pin）。这是交叉核对（QA failure 分支）发现的**先前即存在**（460ca68, 2026-09-05，早于 plan base）但从未声明的 `acp run` 节点运行时 import：模块级 `from typing_extensions import assert_never` 位于 `src/acp/calculations/plans.py:14`、`src/acp/calculations/batch/options.py:21`、`src/acp/workflows/batch_optimize.py:10`——节点上缺它则 simple/batch_optimize/pes_search/nmr/irc 五个工作流 dispatch 全部 `ModuleNotFoundError` | 证据 P2/P7/P8/P9；diff 见 §3 |
| 3 | **release/worker 对齐发现（B06 归口，非 requirements-node 行）：`src/acp_services/**` 不在 `build_sync_file_list`**（CodeSyncer + release manifest 的唯一文件集权威），而节点同步的 `acp` 树有 **45 处运行时直接 import / 17 文件**（transitive 91 前缀 / 400 模块 sweep）硬依赖它——纯同步树上 `acp.calculations.contracts`、`acp.calculations.executor`、`acp.workflows.simple` 全部 import 失败，legacy `python -m acp.cli run`（dispatch 期）与 envelope `python -m acp_services.worker_bootstrap`（模块本身缺失）**两个入口都断** | 证据 P5/P10；对齐请求见 §5 |
| 4 | 模型/notice 引用对齐：8 个 `model_assets/*`（含 `LICENSE-DP5`、`NOTICE.md`）**已在** CodeSyncer 文件集内，节点经代码同步获得，**不产生任何 pip 行** | 证据 P4 |
| 5 | 无 `python -m cccp` 新增；无 Web/IAM/账单依赖夹带（fastapi/pydantic/starlette/anyio/uvicorn/zipstream/paramiko 全部留在 api/remote extra，节点清单零变化） | 证据 A1/A2 |

---

## 2. 迁移模块运行时 import 面（逐依赖：用途 + 最小性）

扫描方法：`ast.walk` 收集绝对 `Import`/`ImportFrom`（相对导入剔除），stdlib 按 `sys.stdlib_module_names` 过滤；双向覆盖 = ① 当前 HEAD 全树、② plan base `dfad6b3..HEAD` 变更文件集（`src/cccp` 108 文件 / `src/acp` 103 文件）。TYPE_CHECKING-only 与运行时分列（证据 §C）。动态 import 审计（证据 §D）：仅包内相对路径 + 白名单（`spectra_registry` → `cccp.analysis.nmr.*`）+ 惰性可选 `dp5q`（typed unavailable，未声明、也不请求）。

| 依赖（node 行） | 迁移模块中的用途（file:line 样本） | 最小性说明 | C03–C09 增量 |
|---|---|---|---|
| `numpy>=2.1.0` | 数值底座：`cccp/analysis/{sampling,pes,tsmode,xtbmd,structure,ensemble,nmr}/*`、`cccp/calculation/*`（40 处新增站点，如 `analysis/pes/atom_mapping.py:17`） | 平台 floor（ANTI：永不降级）；无 numpy 则全部数组型科学代码不可运行 | 无（已有） |
| `rdkit>=2022.09.1` | 结构/立体化学：`analysis/structure/{embedding,parsers,topology}.py`、`analysis/nmr/{enumerate,equivalence,structure_map}.py`、`analysis/pes/atom_mapping.py:327` | 摄入/embedding/立体枚举唯一化学图引擎；不可用 stdlib 替代 | 无（已有） |
| `pyyaml>=6.0` | 配置加载：`cccp/config.py:17`（6 源合并权威） | 配置契约是 YAML；单点声明 | 无（已有） |
| `scipy>=1.10` | NMR 科学：`analysis/nmr/{assignment,error_model,spectra,carbon_processor,iterative_assignment}.py`（`linear_sum_assignment`/`gaussian_kde`/`log_ndtr`/`least_squares`，11 处） | DP4/DP5 + 回归所需；节点 nmr 工作流在节点整链运行（requirements-node 头注释既定政策） | 无（已有；迁移只是把调用点从 acp 搬到 cccp） |
| `matplotlib>=3.7` | 报告绘图：`analysis/nmr/report.py:254,257`（惰性 import，函数内） | XLSX/PDF 报告样式的图；惰性但缺了会在节点报告阶段失败 → 按 todo-32 政策照发 | 无（已有） |
| `openpyxl>=3.1` | XLSX writer：`analysis/nmr/report.py:199` | core dep 能力声明（test_openpyxl_is_a_core_dependency 守护，禁静默跳过） | 无（已有） |
| `nmrglue>=0.10` | Bruker 解析：`analysis/nmr/spectra.py:147,163,739` | 远程 `--bruker` stage 0a 在节点执行（`_EXTRA_DEPS_ON_NODE` 测试强制） | 无（已有；nmr extra 来源，节点按既定豁免） |
| `qml>=0.4`（**不发节点**） | FCHL kernel：`analysis/nmr/fchl.py:197,1279` —— **惰性 + 守卫**（`qml_kernel_available()` try/except；`ACP_FCHL_NUMPY=1` 纯 numpy 回退） | numpy<2 构建冲突（平台 floor numpy>=2.1）；缺了有文档化回退 + typed error | 无（已有；节点禁运不变，P3 验证无 qml 模块导入 OK） |
| `openbabel>=3.1`（**不发节点**） | ≥86 原子 fragment 路径：`fchl.py:422,424` —— **惰性**（`_import_openbabel()` 仅调用期） | fragmentation 路径未接线（requirements-node 头注释）；缺了 typed ImportError | 无（已有；节点禁运不变） |
| **`typing-extensions>=4.1`（新请求）** | `assert_never` 穷尽性检查：`acp/calculations/plans.py:14`、`acp/calculations/batch/options.py:21`、`acp/workflows/batch_optimize.py:10`（全部模块级、节点同步树内、`acp run` dispatch 必经） | 纯 Python、无编译轮子；py3.10 floor 下 `typing.assert_never` 是 3.11+，必须用 backport；P7 证明节点 7 行的**传递闭包不含它**（scipy 仅 `extra=="dev"` 才要）；minimality：仅 3 个消费文件、1 个符号（`assert_never`） | **先前即存在（460ca68→dcb1613），plan-3 diff 为空**；由 T42 QA 交叉核对发现 → 补请求（见 §3） |

`src/cccp/plugin/**`（C09 样板）与 `src/cccp/calculation/products/**`（C02 产品契约）：**零第三方 import**（stdlib + 包内），天然无节点依赖增量（证据 A4/A5）。

---

## 3. 同步请求 — 精确 diff 文本（B 请求，勿直接在 C 侧应用）

### 3.1 `requirements-node.txt`（+1 行，verbatim pin 必须与 pyproject core 一致）

```diff
--- a/requirements-node.txt
+++ b/requirements-node.txt
@@ -42,4 +42,5 @@
 scipy>=1.10
 matplotlib>=3.7
 openpyxl>=3.1
 nmrglue>=0.10
+typing-extensions>=4.1
```

可选注释（放在依赖块上方空行处，`#` 注释行不影响 sync 测试解析）：

```diff
+ # assert_never exhaustiveness checks — module-level import on the
+ # `acp run` dispatch path (acp.calculations.plans / batch.options /
+ # acp.workflows.batch_optimize); NOT provided transitively by the
+ # lines below (pip --dry-run closure, T42 evidence P7).
```

### 3.2 `pyproject.toml` core `[project].dependencies`（+1 行，同 pin）

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -34,5 +34,6 @@
     # missing-openpyxl skip path is unreachable in supported installs, so
     # XLSX can never be silently skipped without a capability declaration.
     # Pure-python, no compiled wheels — safe on slim compute nodes too.
     "openpyxl>=3.1",
+    "typing-extensions>=4.1",
 ]
```

> pin 选择说明：`>=4.1` = `assert_never` 首个可用版本（4.1.0 实测有、4.0.1 实测无，见任务会话探针；与既有行 `>=X.Y` 风格一致）。若 B 更愿用更保守的现网常见 pin（如 `>=4.10`）**两端同步改**即可——sync 测试要求两端逐字一致，单边改即 RED。

### 3.3 同步点（应用时逐条过）

| # | 同步点 | 说明 |
|---|---|---|
| S1 | `tests/test_packaging_assets.py::test_requirements_node_mirrors_every_core_dep` | core dep 必须在 node 清单同 pin → 两端必须**同一提交**落地 |
| S2 | `…::test_requirements_node_declares_only_pyproject_deps` | node 行必须在 pyproject（core 或 nmr extra）声明 + 禁运集（qml/openbabel）不出现 |
| S3 | `…::test_platform_numpy_pin_never_lowered` | 不触碰（numpy 行原样）——本请求不改任何既有行 |
| S4 | `NodeManager.bootstrap_node()`（`scheduler/remote/node_manager.py:452-454,576`） | 节点 `pip install --user -r requirements-node.txt` 自动吸收新行 |
| S5 | `CodeSyncer` / `release.build_release_manifest`（`sync.py:107-110`、`release.py:309 requirements_sha256`） | `requirements-node.txt` 在 release 文件集内 → 内容变化即 `requirements_sha256`/`release_id` 变化；B 应用后旧在途 release 按 B06 既有接管规则处理（C 不改动该语义） |
| S6 | plan-2 B07「与 C08 联合调整 wheel/角色依赖、模型/notice 和节点依赖」 | 本请求即 B07 该条的 C 侧输入；与 HN-04（T39 release_manifest §4 N1–N5）表同口径：N4「C03–C09 新增 acp run-reachable import」= typing-extensions（本请求），其余为 none |
| S7 | **T54** | B 应用 + 节点 `pip install -r` PASS 回执 → T54 候选版本发布前安装复验消费该回执（C 预验→B 应用回执→C 复验链条，评审 C7/R4） |

### 3.4 应用后验证命令（B 侧执行，记录入回执）

```bash
# 1) 同步守护全绿
python3.11 -m pytest tests/test_packaging_assets.py -q   # 期望：3 个 node-sync 守护 PASS
# 2) 干净环境解析（无需真节点即可证闭包）
python3.11 -m pip install --dry-run --ignore-installed -r requirements-node.txt
# 3) 节点环境（或等价 venv）安装 + import 探针
python3.11 -m pip install --user -r requirements-node.txt
python3.11 -c "import typing_extensions, numpy, rdkit, scipy, matplotlib, openpyxl, nmrglue, yaml; print('node runtime deps OK')"
# 4) 真实 acp run 冒烟（本地即可证 dispatch 路径）
acp run xtb_optimize --input <minimal.xyz> --output /tmp/t42_smoke
```

守卫模拟（live 文件未动，按 §3 diff 预演两端）：证据 P8 —— `mirrors_core=True / declared_no_forbidden=True / numpy_pin=True / capabilities=True / verbatim-match=True / ALL WOULD PASS: True`。

---

## 4. pyproject extras ↔ node 行映射（现状 + 本请求后）

| pyproject 位置 | 声明 | 发节点？ | 映射依据 |
|---|---|---|---|
| core `[project].dependencies` | numpy, rdkit, pyyaml, scipy, matplotlib, openpyxl | **是**（verbatim 同 pin） | `test_requirements_node_mirrors_every_core_dep` |
| core（**本请求新增**） | typing-extensions | **是**（同 pin） | 同上（应用后） |
| `nmr` extra | nmrglue | **是**（豁免集 `_EXTRA_DEPS_ON_NODE`） | 远程 `--bruker` stage 0a 在节点 |
| `nmr` extra | qml, openbabel | **否**（`_NODE_FORBIDDEN`） | numpy<2 冲突 / 未接线；惰性守卫 + 回退（P3 实证） |
| `api` extra | fastapi, uvicorn, python-multipart, zipstream-ng（+传递 pydantic/starlette/anyio） | **否** | `src/acp/api/` 被 CodeSyncer 排除（`_ACP_EXCLUDE_DIRS`）；服务端面 |
| `remote` extra | paramiko | **否** | `src/acp/scheduler/` 被排除；init_wizard 的 paramiko 为 per-call 守卫 import（A1：无模块级站点在 node 必经路径） |
| `dev` extra | pytest, ruff, mypy … | **否** | 工具链 |
| （未声明） | dp5q（DP5q stub 可选运行时） | **否** | 惰性 `importlib` + typed unavailable（证据 D）；从未声明、刻意如此——不夹带 |

---

## 5. release/worker 对齐（B06/B07 输入；证据见 P5/P10）

### 5.1 `src/acp_services/**` 缺席 CodeSyncer 文件集（**代码同步问题，非 pip 行**）

- **事实**（HEAD `ac57a9a` 复核仍成立）：`build_sync_file_list()`（sync.py:88，docstring 自称 “single authority”）只收集 `src/acp`（排除 api/scheduler）+ `src/cccp` + `requirements-node.txt` + `config/defaults.yaml` —— `src/acp_services` 文件数 **0**（P4）。
- **消费面**：节点同步树内 **45 处运行时直接 import / 17 文件**（P10：`acp/calculations/{contracts,step_requirements,step_result}.py`、`acp/catalog.py`、`acp/cli.py`、`acp/storage/{layout,record}.py`、`acp/services_*.py`、`acp/init_wizard/*`、`acp/results/frame_candidate_store.py`）；400 模块 blocker sweep 下 91 个前缀失败（P10）。
- **faithful 节点模拟**（P5：只放 CodeSyncer 文件集 + user site-packages，剥离全部 repo 路径）：
  ```
  find_spec(acp_services): None
  acp.calculations.contracts: FAIL -> ModuleNotFoundError: No module named 'acp_services'
  acp.calculations.executor : FAIL -> ModuleNotFoundError: No module named 'acp_services'
  acp.workflows.simple      : FAIL -> ModuleNotFoundError: No module named 'acp_services'
  ```
- **两个执行入口都断**：legacy `python -m acp.cli run …`（`acp.cli` 本身惰性导入 OK，dispatch 期 `acp.workflows.*` 失败——P2/P5）；envelope `python -m acp_services.worker_bootstrap`（script_gen.py:167,348 目标模块本身不在树上）。
- **时序**：shim 落地 `f8f4fdd`(10-18)、`0286b2b`/`1688a5a`(10-19) 晚于 sync 清单最后实质变更 `5902904`；`git log -S acp_services -- sync.py` 为空 → 从未同步过。
- **建议（B 请求，B 补丁文件）**：`sync.py::build_sync_file_list` 增加 `src/acp_services/` 段（对称第 3 段），`tests/test_remote_code_release.py` file-set parity 断言补 `src/acp_services/**`（现 parity 测试两边同源，抓不住此类缺口）。**C 不改**（write_registry §4.1：`scheduler/` 为 B补丁混合文件；且属 B06「release/节点依赖切换」职责）。
- **影响声明**：当前远端作业若在 shim 提交后重新 sync 即断；B06 整链切换前的 legacy 路径同样受影响。**这不是 requirements-node.txt 能修的**——归 B06 release/worker 对齐，故本行 status 与 HN-05 解耦：HN-05 交付 = 本请求文档；此发现随本文档一并交付 B。

### 5.2 worker 科学依赖

- `acp_services` 自身第三方 import 面 = **仅 `yaml`**（2 站点，`contracts/config_plan.py:405,411`）→ 已在节点清单（pyyaml），worker 组合根**零 pip 增量**。
- worker 执行的 cccp 任务层 import 面 = §2 表（numpy/rdkit/scipy/yaml/…）→ 与节点清单一致。
- release_manifest §4 N1–N5 逐条复核：N1 core 块 verbatim ✓（P1）；N2 nmrglue ✓；N3 qml/openbabel 禁运 ✓（P3）；**N4 = 本请求 typing-extensions（T39 时判 none，plan-3 后续批次口径以本文件为准）**；N5 `cccp==0.2.0` 为发行依赖非节点行（T39 归口，不重复）。

### 5.3 模型/notice

- 8 个 `model_assets/*`（含 `LICENSE-DP5`、`NOTICE.md`）已在 CodeSyncer 文件集（P4）→ 节点经**代码同步**获得，无 pip 行。
- wheel 内资产/许可随 T39/T56 打包请求（HN-04），本请求不重复；`python -m cccp` **不新增**（无 entry point 变更）。

---

## 6. QA（plan item 42 验收对照）

| 验收项 | 结果 |
|---|---|
| 请求含 diff 文本与同步点 | ✓ §3.1/3.2 diff + §3.3 S1–S7 |
| 逐依赖说明用途与最小性 | ✓ §2 表（9 行现状 + 1 行请求，含禁运集理由） |
| 对照已迁移模块 import 面（AST/grep）无遗漏 | ✓ 双向扫描（全树 + plan-base diff）；**failure 分支命中 1 项（typing-extensions）→ 已补入请求**（§3） |
| 失败 → 补请求并复跑打包样例 | ✓ 补请求后复跑 `tests/test_packaging_assets.py`：**3 个 node-sync 守护 PASS**；2 个 asset RED 为已知跨路窗口（release_manifest §3.4，T55/B40a repoint patch，非本任务引入，live 文件未动） |
| 不直接改 requirements-node / pyproject | ✓ 仅文档 + 证据（`.omo/evidence/…/task-42-*.md`） |
| 不夹带 Web/IAM 依赖 | ✓ §4 映射表：api/remote/dev extras 零变化 |
| 无 `python -m cccp` | ✓ 无 entry point 变更 |
| 应用结果 feed T54 | ✓ 头部 + S7 |

## 7. 证据索引

| 文件 | 内容 |
|---|---|
| `.omo/evidence/acp-parallel-plan-3-cccp/task-42-import-scan.md` | AST 全量扫描：node 树 / api·scheduler 树 / acp_services / plugin / products / plan-base diff（cccp+acp）/ TYPE_CHECKING 分列 / 动态 import 审计 |
| `.omo/evidence/acp-parallel-plan-3-cccp/task-42-node-surface-sweep.md` | P1 声明对照 · P2 typing_extensions 阻断探针 · P3 qml/openbabel 缺席导入 · P4 文件集清单 · P5 faithful 节点模拟 · P6 打包守护现状 · P7 闭包 dry-run · P8 diff 守卫预演 · P9 typing_extensions 溯源 · P10 acp_services 直接 import 表 |
