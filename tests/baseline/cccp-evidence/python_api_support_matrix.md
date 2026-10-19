# C08-s5 / T43 — Python API, examples, compatibility policy, support matrix (tracked)

- **Owner:** C 路（科学） · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 43 (Wave 4a)
- **Main plan refs:** `docs/ACP_Architecture_Adjustment_Plan_20261016.md` §18（CCCP Open Science 独立形态）、§27.4（适配接口与故障语义：缺字段/不支持版本不得默默换路径）
- **Write authority:** `C直写` — `tests/baseline/cccp-evidence/**`（write_registry.md §4.9 科学证据）
- **Base snapshot HEAD:** `29ed7969f699ab6d1832829d3432bd09283749e1`（`src/cccp` 与 `tests/fixtures` 在该提交下 clean；共享工作区另有他人未提交改动，见 §5）
- **Interpreter:** `/usr/bin/python3.11`（3.11.13）· Linux 5.14.0 el9 x86_64 · numpy 2.4.6 / rdkit 2026.03.6 / scipy 1.17.1 / matplotlib 3.11.1 / openpyxl 3.1.5 / PyYAML 6.0.3
- **Raw evidence:** `.omo/evidence/acp-parallel-plan-3-cccp/task-43-inventory.txt`、`task-43-examples.txt`（命令原文 + 原始输出）
- **Deliverables:** 本文件 + `tests/baseline/cccp-evidence/examples/`（1 个 inventory 生成器 + 3 个离线科学示例）

---

## 1. Python API 文档（公开入口清单，programmatic）

### 1.1 生成方法（可复现命令）

清单不是手写的：由 `tests/baseline/cccp-evidence/examples/gen_public_api_inventory.py`
程序化导入 13 个 C08 声明的公开入口，逐一列出 `__all__`（无 `__all__` 时回退到非下划线
`dir()`），并按 function / class / constant 分类。命令与原始输出全文见
`.omo/evidence/acp-parallel-plan-3-cccp/task-43-inventory.txt`：

```bash
PYTHONPATH=src /usr/bin/python3.11 \
    tests/baseline/cccp-evidence/examples/gen_public_api_inventory.py
# exit=0（13/13 入口导入成功，无 IMPORT FAILURES）
```

### 1.2 公开入口总表（HEAD `29ed796`，共 570 个导出名）

| 入口 | 用途（迁移任务） | 导出名数 | 来源 |
| --- | --- | --- | --- |
| `cccp` | 包根：仅 `__version__`，无 re-export（不设门面） | 1 | `dir()` |
| `cccp.analysis.structure` | 六格式结构摄入 + RDKit embedding + NMR 拓扑捕获（T16） | 32 | `__all__` |
| `cccp.analysis.identity` | 内容绑定请求指纹 v2 纯算法（T53） | 10 | `__all__` |
| `cccp.analysis.orca_parser` | ORCA `.out` 只读聚合解析（T18） | 2 | `__all__` |
| `cccp.analysis.frequency_products` | `normal_modes_v1` 频率产品构建（T19） | 2 | `__all__` |
| `cccp.analysis.sampling` | xTB-MD 采样历史/分盆/MDS/饱和度科学（T20） | 16 | `__all__` |
| `cccp.analysis.confsearch` | Boltzmann 权重 + 精修策略选择 + manifest 变换（T21/T24） | 14 | `__all__` |
| `cccp.analysis.pes` | 路径剖析/原子映射/键变/候选/校验（T22） | 47 | `__all__` |
| `cccp.analysis.tsmode` | TS-mode 源 bundle + 模式映射科学（T23） | 36 | `__all__` |
| `cccp.analysis.nmr` | NMR/DP4/DP5 全分析科学 + 模型资产入口（T25–T30、T55） | 263 | `__all__` |
| `cccp.analysis.bounded` | 有界分块/抽样/多分辨率产品（T31–T33） | 29 | `__all__` |
| `cccp.calculation.products` | 版本化科学产品契约（T11/T12） | 45 | `__all__` |
| `cccp.plugin` | 可信插件 pilot 面 + SDK（T44/T45） | 73 | `__all__` |

### 1.3 每入口代表符号（选读；全量见 raw evidence）

| 入口 | 代表公开符号 |
| --- | --- |
| `cccp.analysis.structure` | `detect_and_parse`、`parse_xyz_text`、`parse_gjf_text`、`parse_mol_text`、`parse_sdf_text`、`parse_orca_inp_text`、`parse_smiles_list`、`parse_qc_output_text`、`enumerate_embeddings`、`capture_nmr_topology`、`Structure`、`StructureAsset`、`StructureParseResult` |
| `cccp.analysis.identity` | `identity_fingerprint`（`"v2:"+sha256(canonical_json)[:32]`）、`content_sha256`、`config_digest`、`ScientificIdentity`、`IDENTITY_SCHEMA`、`FINGERPRINT_PREFIX` |
| `cccp.analysis.orca_parser` | `OrcaCalculation`、`OrcaOutputParser`（`parse`/`parse_text`：path in → data out，部分输出不 raise） |
| `cccp.analysis.frequency_products` | `build_normal_modes_product`、`build_frequency_report`（原生模式编号永不重排） |
| `cccp.analysis.sampling` | `load_sampling_history`、`write_sampling_history`、`read_traj_frame_xyz`、`compute_sampling_history`、`assign_basins`、`equilibration_cutoff`、`mds_2d`、`dedup_by_rmsd`、`plain_rmsd`、`SamplingHistory`、`BasinInfo` |
| `cccp.analysis.confsearch` | `boltzmann_weights`、`relative_energies_kcal`、`select_for_refinement`、`threshold_for_policy`、`REFINEMENT_POLICIES`、`ConformerEntry`、`ProtocolOutcome`、`PURE_XTB_PROTOCOLS` |
| `cccp.analysis.pes` | `build_orca_scan_profile`、`build_xtb_path_profile`、`compute_path_arclength`、`map_reactant_to_product`、`compute_bond_changes`、`select_path_seeds`、`select_candidates`、`compare_graph_topology`、`detect_risky_contacts`、`parse_functional_atom_selection`（`HARTREE_TO_KCAL` 保留截断值 `627.509`，数值基线登记禁改） |
| `cccp.analysis.tsmode` | `FrequencySourceBundle`、`MappingStatus`、`load_bundle_from_files`、`resolve_target_mode`、`compare_mode_correspondence`、`enforce_launch_gate`、判错常量 `MODE_MAPPING_AMBIGUOUS` / `SOURCE_REVISION_CONFLICT` 等 |
| `cccp.analysis.nmr` | `assets.asset_dir`/`resolve_asset`（T55 单一资产入口）、`scaling`/`probability`/`assignment`/`enumerate`/`equivalence`/`report` 模块面、DP4/DP5 科学（DP5q 与 ShiftPredictor 隔离面保持在外） |
| `cccp.analysis.bounded` | `chunk_curve_product`、`chunk_trajectory_product`、`subsample_curve_product`、`build_multiresolution_product`、`BoundedProduct.from_dict`、`reconstruct_curve_series`、`reconstruct_structure_frames`、`selected_point_mapping`、`bounded_identity` |
| `cccp.calculation.products` | `ScienceProduct`、`ProductIdentity`、`ProductKind`、`ProductState`、`MissingReason`、`build_structure_trajectory_product`、`build_curve_scatter_product`、`product_from_dict`、`product_to_dict`、`migrate_v0_to_v1`、`PRODUCT_SCHEMA_VERSION` |
| `cccp.plugin` | `PluginHost`、`PluginMetadata`、`PluginCandidate`、`discover_plugins`、`builtin_catalog`、`lock_plugin`、`invoke_capability`、`validate_result_contract`、`PluginVersionDigest`、`failure_report`、示例 `XyzReadonlyParserPlugin`/`EXAMPLE_CANDIDATE`、pilot `OrcaReadonlyParserPlugin`/`PILOT_CANDIDATE`、typed 错误族 `Plugin*Error` |

> 稳定性约定：各入口以 `__all__` 为契约面（`cccp` 根除外）；`__init__` 仅 re-export、零实现（ANTI #5）。
> 契约版本常量（`PRODUCT_SCHEMA_VERSION`、`BOUNDED_SCHEMA_VERSION`、`PLUGIN_SDK_VERSION` 等）随产品/SDK 身份演进，不静默变更。

---

## 2. 离线科学示例（3 个，全部离线可跑）

三个示例脚本入库于 `tests/baseline/cccp-evidence/examples/`，**零 QC 二进制、零浏览器/平台依赖**：
只读仓库 fixture + 纯 Python 科学路径。命令与原始输出全文见
`.omo/evidence/acp-parallel-plan-3-cccp/task-43-examples.txt`（3/3 `exit=0`，各含断言块）。

### 2.1 Example A — ORCA 只读解析 + 频率产品构建

```bash
PYTHONPATH=src /usr/bin/python3.11 \
    tests/baseline/cccp-evidence/examples/example_a_orca_parse_and_frequency_product.py
```

覆盖两个真实 fixture 的部分输出语义与完整产品构建（节选，全文见 raw evidence）：

```
[parse]  fixture=tests/fixtures/orca_optfreq_real_sections.txt
[parse]  success=False energy_hartree=None (partial fixture: no FINAL SINGLE POINT ENERGY)
[parse]  native mode_frequencies count=9 indices=[0, 1, 2, 3, 4, 5, 6, 7, 8]
[parse]  product modes=0 warnings=9 (typed skip reasons, never silent)
[build]  schema_version=normal_modes_v1
[build]  modes=9 warnings=[]
[build]   mode 6: -797.72 cm-1  <-- imaginary (TS evidence)
EXAMPLE A: PASS (partial parse semantics + full product build + assertions)
exit=0
```

科学语义：截断 fixture 不伪造能量（`None`）也不静默吞模式（9 条 typed skip 原因）；
完整 fixture 经 `build_normal_modes_product` 产出 9 个原生编号模式（不重编号），虚频 -797.72 cm-1。

### 2.2 Example B — 产品契约读取 + 有界分块读取

```bash
PYTHONPATH=src /usr/bin/python3.11 \
    tests/baseline/cccp-evidence/examples/example_b_product_and_bounded_read.py
```

```
[c02]    kind=structure_trajectory state=available complete=True schema_version=1
[c02]    frames=2 coordinate_units=angstrom topology_source=orca_xyz
[c06]    bounded_schema=1 (pkg=1)
[c06]    blocks=3 items=40 max_block=16
[c06]    all block files parse via the single C02 reader
[c06]    reconstructed series: 40 points, series_id=curve_scatter:series:scan_energy
[c06]    native index coverage: 0..39 contiguous=True
[c06]    corrupt manifest rejected with BoundedSchemaError: unsupported bounded schema_version 99; current is 1
EXAMPLE B: PASS (product read + bounded chunk read + assertions)
exit=0
```

科学语义：C02 单一 reader（`product_from_dict`）读产品与分块；C06 无损重建覆盖 0..39；
损坏 manifest 得 typed `BoundedSchemaError`，绝不静默成功。

### 2.3 Example C — 插件示例（xyz）load + invoke

```bash
PYTHONPATH=src /usr/bin/python3.11 \
    tests/baseline/cccp-evidence/examples/example_c_plugin_xyz.py
```

```
[host]   catalog names=('xyz_readonly_parser',)
[host]   enabled before=False
[host]   enabled after =True
[digest] sha256=7564e154cde3bdc97cc7e4e8befd3dc2d782b13a9cad99ef92928215d115bf6a
[invoke] status=completed complete=True symbols=('O', 'H', 'H')
[invoke] units={'coordinates': 'angstrom'} atom_order=['O', 'H', 'H']
[invoke] status=failed complete=False error_kind=parse_failure symbols=('O', 'H')
[invoke] errors=('truncated: expected 3 atom(s), parsed 2',)
EXAMPLE C: PASS (plugin load + invoke + partial boundary + assertions)
exit=0
```

科学语义：插件默认关闭、`PluginHost.enable` 唯一启用路径；版本摘要 sha256 确定性锁定；
截断输入保留有效前缀 + typed `parse_failure`（不伪造、不静默替换）。

### 2.4 外部软件标注（QA 条款）

以上 3 例**不需要任何外部科学软件**。凡涉及真实计算的入口（`cccp.calculation.tasks/*`
经 ORCA/xTB/CREST/CENSO/ISOSTAT/Shermo/LSF 执行）= **需外部软件**，本任务未运行任何真实
QC 程序 → 支持矩阵一律记 `NOT_VERIFIED`（§4），不以只读解析/产品构建冒充程序运行验证。

---

## 3. 兼容 / 弃用政策

### 3.1 旧路径转发（same-object re-export，现状两类）

| 类别 | 机制 | 例（file:line 证据见 write_registry.md 与 T49 identity 探针） | 状态 |
| --- | --- | --- | --- |
| `C直写` 移交文件 | 旧 `acp.X` 原地改为 re-export **同一对象**（`acp.X.f is cccp.analysis.X.f`） | `acp.intake.parsers`（T16，same-object 探针 PASS）、`acp.confsearch.selection`、`acp.confsearch.shared.boltzmann`、`acp.nmr.{structure_map,enumerate,scaling,probability,report}`（抽样探针 5 项 PASS）、`acp.calculations.pes.{path_analysis,candidates}` 等 | 已合入（活 shim） |
| `B补丁` 混合文件 | C 产补丁交 B 应用；应用前旧路径仍是**独立旧实现**（数值等价由 T49 独立对比证明） | `acp.results.orca_parser`、`acp.results.frequencies`（T18/T19 补丁 `delivered`，B 应用后变 same-object shim）、`acp.io.structures`（T17）、`acp.confsearch.{contracts,result_helpers}`（T21）、retired 引擎文件（T24） | 补丁 `delivered`（多数）/`integrated`（以 B 回填 `applied_commit` 为准） |

消费者规则（对插件作者与下游脚本同样生效）：

1. **新代码一律 import `cccp.*`**；旧 `acp.*` 科学导入路径仅为兼容保留。
2. 兼容路径保证**同对象身份**（shim 类）或**同数值输出**（B 补丁应用前的独立旧实现，T49
   逐域 PASS）；异常类型、序列化、原生模式编号、科学 hash 不变。
3. 主方案 §27.4：缺字段/不支持版本/取消无效**不得默默换另一条执行路径**——typed 失败是契约。

### 3.2 转发窗口（forwarding window）

- 旧路径转发**至少保留至 C10-s3（T50 撤旧收口）完成并经全量定向测试全绿**；在此之前任何
  旧导入不得报 `ImportError`。
- T50 之后，公开转发仍按 §3.4 永久保留；撤的只是**旧实现体**（重复实现），不是旧导入路径。
- 插件 SDK 有独立弃用窗口：`SDK_DEPRECATIONS` + `SDK_DEPRECATION_WINDOW_MINOR_RELEASES`
  （T45 `plugin_sdk.md` §弃用政策）：破坏性变更先登记 deprecation、保留窗口内旧签名可用。

### 3.3 删除条件（per write-registry lifecycle）

撤除任何旧实现/兼容面必须**同时**满足（对齐 `write_registry.md` §2 补丁生命周期与 T50 验收）：

1. 对应 B 补丁状态 = `integrated`（B 分支已应用且定向测试通过，`applied_commit` 回填）；
   `delivered` 不足以删除。
2. `cccp` 侧唯一实现已在该 release 范围内（无 acp 环境可导入并调用，隔离探针 PASS）。
3. 删除前核对四类残留引用：公开导出、动态注册/registry 字符串、持久格式（manifest/checkpoint）、
   恢复 fixture（`tests/baseline/recovery_fixtures/`）。
4. **发现遗漏调用者时的处置**：恢复兼容 shim 或修正调用路径，或回退整个切片；**禁止**在
   CCCP 已有唯一实现后再恢复一份 ACP 实现（评审 C9）。
5. 不删审计材料/fixture、不整目录删除 `compat/`；受影响异常/兼容/依赖文档经 B 请求/补丁同步。
6. 删除后更新 handoff registry 状态机并重跑定向测试（全绿方可关闭）。

### 3.4 永久保留（不随撤旧删除）

| 保留面 | 理由 |
| --- | --- |
| `acp.*` 公开科学导入转发（same-object shims） | 历史脚本/下游/`unittest.mock.patch` 目标兼容（T49 identity 探针锁定） |
| `acp.compat.legacy/*` 历史 manifest 只读 reader | 历史任务结果兼容（ANTI #18：compat 只读） |
| retired 工作流 catalog 条目（`status:"retired"` 只读展示） | 历史作业展示，勿重建/勿扩展 |
| NMR 模型资产 + `LICENSE-DP5`/`NOTICE.md` + vendor 许可声明 | 许可获得性义务（T41 `license_availability_statement.md`） |
| 恢复 fixtures 与科学黄金基线 | 跨版本恢复与数值回归锚点（禁止变更） |

---

## 4. 支持矩阵（tri-state：PASS / FAIL / NOT_VERIFIED）

> 规则：`PASS` 必须有当前 revision 上的命令级证据；历史 CI 通过不代表当前 revision（验证策略
> §证据可交付）；未运行 = `NOT_VERIFIED`，绝不用离线样例冒充真实软件验证。**本矩阵 0 FAIL。**

### 4.1 Python × 载体（源码树离线科学 / 独立 wheel）

| 组合 | 判定 | 证据 |
| --- | --- | --- |
| Python 3.11.13 · Linux x86_64 · 源码树（`PYTHONPATH=src`）离线科学 | **PASS** | 本任务 3 示例 + inventory（`task-43-examples.txt`/`task-43-inventory.txt`，HEAD `29ed796`，4/4 exit=0） |
| Python 3.11.13 · Linux x86_64 · 独立 wheel 干净 venv 离线安装 | **PASS** | T40a（冻结提交 `5518aa6`：wheel 构建、`find_spec("acp") is None`、16 项样例 PASS；证据 `task-40a-acp-parallel-plan-3-cccp.md`） |
| Python 3.10 | **NOT_VERIFIED** | 本机无 3.10 解释器（`/usr/bin/python3.{9,11}`）；`requires-python>=3.10` 是声明非证据；当前 revision 未复验 |
| Python 3.12 | **NOT_VERIFIED** | 同上（无 3.12 解释器；历史 CI compileall 不算当前 revision 证据） |
| Python 3.9 | **NOT_VERIFIED**（且低于声明下限） | 解释器在场但低于 `requires-python>=3.10`，未运行 |
| macOS / Windows | **NOT_VERIFIED** | 无对应环境；路径解析/资产入口按 `pathlib` 写，未验证 |

### 4.2 科学能力（离线可验证部分）

| 能力 | 判定 | 证据 |
| --- | --- | --- |
| ORCA `.out` 只读解析（含部分输出语义） | **PASS** | Example A + T40a §7（real fixture） |
| `normal_modes_v1` 频率产品构建（原生模式编号） | **PASS** | Example A + T40a §7（mode6=-797.72） |
| C02 产品契约读取（trajectory/curve/vibration/derived 六态 fixture） | **PASS** | Example B + T40a §7（6 fixture）+ `pytest tests/test_cccp_science_products.py tests/test_cccp_science_products_extended.py tests/test_cccp_bounded_products.py tests/test_cccp_frequency_products.py tests/test_cccp_analysis_identity.py -q` → 86 passed（HEAD `29ed796`，原始输出入 `task-43-examples.txt`） |
| C06 有界分块读取/无损重建/损坏拒绝 | **PASS** | Example B（blocks=3、覆盖 0..39、typed 拒绝） |
| 插件 SDK：发现/启用/调用/部分输出/版本摘要 | **PASS** | Example C + T45 契约套件：`pytest tests/test_cccp_plugin_sdk.py tests/test_cccp_plugin_pilot.py -q` → 44 passed（HEAD `29ed796`，原始输出入 `task-43-examples.txt`） |
| 结构摄入六格式解析 | **PASS** | `PYTHONPATH=src /usr/bin/python3.11 -m pytest tests/test_acp_intake.py tests/test_acp_intake_detect_format.py tests/test_acp_intake_qc_output.py -q` → 44 passed（HEAD `29ed796`；`acp.intake.parsers.parse_xyz_text is cccp.analysis.structure.parsers.parse_xyz_text` same-object 探针 PASS，测试打在唯一实现上） |
| 旧/新科学输出等价（NMR/PES/simple 三域） | **PASS** | T49（`c10-old-new-comparison.md`：shim same-object + 旧树 replay + 活旧实现独立对比，0 FAIL；工件 sha256 登记） |
| NMR 模型资产定位 + TMS lookup | **PASS** | T40a §7（`tms_references.txt` 4578 B；mPW1PW91/6-311G(d)/chloroform 值） |
| DP5 KDE 模型加载 + 概率 | **PASS** | T40a §7（P(good)=0.697611） |
| FCHL 原子表示加载 | **PASS** | T40a §7（shape=(53208, 5, 86)） |

### 4.3 需外部软件 / 跨路面（NOT_VERIFIED）

| 项 | 判定 | 说明 |
| --- | --- | --- |
| ORCA / xTB / CREST / CENSO / ISOSTAT / Shermo 真实运行 | **NOT_VERIFIED** | 需外部软件；本任务与 T40a 均未运行 QC 程序（conftest `requires_*` 三态规则） |
| 远程 LSF 执行 | **NOT_VERIFIED** | 跨路面（B/remote），离线范围外 |
| NMR 全流程（GIAO 计算依赖 ORCA）+ `nmr` extra（nmrglue/openbabel/qml） | **NOT_VERIFIED** | 需外部软件 + extra 未入 T40a 离线 wheelhouse（核心依赖已验证，extra 栈未装） |
| Web / API / 前端消费面 | **NOT_VERIFIED** | T49 明示 Web/MCP 未验证；属 A/B 路 |
| T40b 完整发行预验 / T54 候选版本复验 | **NOT EXECUTED（保留）** | T40a §1 范围声明；本文件不代其宣称 |

---

## 5. QA、风险与未决

- **happy**：3 示例在源码树离线跑通（exit=0 + 断言块 PASS）；inventory 13/13 导入成功；
  支持矩阵含真实 `NOT_VERIFIED` 标注；兼容政策含删除条件（§3.3 六条）。
- **failure 路径演练**：Example B 内建损坏 manifest → typed `BoundedSchemaError`；
  Example C 内建截断输入 → typed `parse_failure` + 有效前缀保留。均为断言覆盖的错误路径。
- **共享脏工作区**：HEAD `29ed796` 下 `src/cccp`/`tests/fixtures` clean；他人未提交改动
  （`docs/ACP_Architecture_Adjustment_Plan_20261016.md` 等）与本任务无交集，未触碰。
- **快照时效**：若 T54 候选提交后公开面变化（新增/删除入口），§1 清单须以同命令重新生成；
  示例脚本本身是 tracked 的，可随时复跑。
- **本任务零产品代码改动**：仅新增 tracked 证据/示例文件；无 golden 值/容差/护栏变更。
