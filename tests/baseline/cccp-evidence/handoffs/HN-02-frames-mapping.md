# HN-02 — 科学产品 → frames/VIEW_REGISTRY 字段映射与实装闭包交接

| 字段 | 值 |
|---|---|
| HN 编号 | `HN-02`（C 侧 `tests/baseline/cccp-evidence/handoff_registry.md` §2 索引行） |
| owner / provider / consumer | C 路科学负责人 / C / **B（B-T28a 既有发布入口 / B-T28b 科学产品→frames/annotation adapter）** + A（显示，A04 / A-GT3） |
| tracked 位置 | `tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md`（`.omo/` 被 gitignore，交接物必须 tracked） |
| 计划来源 | `.omo/plans/acp-parallel-plan-3-cccp.md` todo 13（C02-s3）；交付=字段映射表 + 实装闭包表 + 五态 fixture + 两状态二分 |
| B 侧接收入口 | `.omo/plans/acp-parallel-plan-2-services.md:439` todo 28（B05-4，含 B28a/B28b） |
| A 侧消费入口 | `docs/reference/next-change/ACP_Parallel_Plan_1_Frontend_20261018.md` §3 A04（`消费 C 科学产品经 B 的 frames/VIEW_REGISTRY adapter`）+ A-GT3（§1 闸门表，退出=「单位/身份/缺失原因逐项一致」） |
| 契约锚点（只读） | `src/acp/results/frames.py`（`TrajectoryFrame.to_node():64`、`TrajectoryAnnotation.to_annotation():125`、`_ANNOTATION_METADATA_KEYS:90`、`VIEW_REGISTRY:175`）— **本任务零修改**（ANTI #21） |
| 产品契约 | `src/cccp/calculation/products/*`（T11/T12，`PRODUCT_SCHEMA_VERSION=1`） |
| status | `delivered`（provider 侧；**未 integrated** — B 应用并验收前不得置 `integrated`） |
| applied_commit（provider 交付提交） | `06ce0452a714ff1e389e7ed1aedc022e8640cef9`（HN-02 文档 + 五态 fixture 入库提交）；`handoff_registry.md` HN-02 行回填同 sha（第二提交） |
| 范围 | 本次声明支持的产品：`structure_trajectory` / `curve_scatter` / `vibration` / `derived`（`ProductKind` 全 4 类） |

---

## 0. 交付状态二分（R4 §3.3）— 显式声明

| 状态 | 含义 | 判定条件 | 与本 todo 的关系 |
|---|---|---|---|
| **状态 A「契约可消费」** | 映射表 + 五态 fixture（各附期望映射输出）+ 实装闭包表就绪；`src/cccp/calculation/products` 零 `acp` import | §2 表逐字段、§5 fixture 五态齐全、§6 闭包表逐产品、§8 grep 零命中 | **本 todo 完成条件 = 状态 A**。B **可立即开始 B-T28b**，不得等待状态 B。 |
| **状态 B「跨路端到端验收完成」** | 同一固定输入贯穿 **C 生产入口 → B 发布/投影 → A/Python 消费** 的联合状态，逐项一致（单位 / `atom_uid` / frame / point / 原生 mode / 缺失原因 / 块索引） | §9.3 联合验收；**仅 schema 测试不能关闭** | **单独记录**，待 B/A 接入后成立；**非**本 todo 完成条件，**非**状态 A 的前置（避免互相等待）。C 侧生产入口接线证据随对应任务（T18/T19/T20/T22 等）提交。 |

> 一句话：**本 HN 交付状态 A**；状态 B 由 B28b + A04/A-GT3 接入后另记，本文件只预置状态 B 的记录位（§9.3，当前 `pending`）。

---

## 1. 基线 revision（base_revision）

- `provider_base = 7ce58a6f8066e934baff4a61a53daa03ee201e73`（本任务开工 fresh 快照，2026-10-18T22:44:58+08:00，`branch=feat/frontend-plan1-closure`）
- `consumer_applied = pending（B 未应用；无 B 侧应用提交）`
- `repo_state = dirty_worktree`（Plan-1 前端 / Plan-2 Services 在途改动；**无一条属本 C 路**；本任务只写 `tests/baseline/cccp-evidence/{handoffs/HN-02-frames-mapping.md, handoff_registry.md}` + `tests/fixtures/science_products/frames_mapping/**`）
- 先前 C 路登记基线：write/handoff registry = `dfad6b30…`；T11 契约 = `0394393…`；T12 契约 = `962c192`（提交 `d7b71f0`）。
- 契约冻结：T11（结构/轨迹）+ T12（曲线/振动/派生）**完整交付**（todo 13 Blocked by 11,12 已满足）。

---

## 2. 字段映射表（source field → target field → unit/identity rule）

> 目标形状 = **真实** `TrajectoryFrame.to_node()` / `TrajectoryAnnotation.to_annotation()` 输出（`frames.py`）。任何缺失科学数据 **不得补零/伪造**（`MissingReason` 契约，T11/T12）。所有 B 侧写实现**必须**经 `to_node()/to_annotation()`，**禁止**手写/旁路该形状（ANTI #21）。

### 2.1 `structure_trajectory` → `TrajectoryFrame` → node

| 源字段（product，file:line） | 目标字段（帧 → wire） | 单位 / 身份规则 |
|---|---|---|
| `FrameRecord.frame_id`（contracts.py:604） | `frame_id` → `node["id"]` | 逐字；available 帧必需（contracts.py:634） |
| `FrameRecord.label`（contracts.py:607） | `label` → `node["label"]` | 非空逐字；空 → `Frame {frame_index+1}`（对齐 `energy_graph.py:230`） |
| `FrameRecord.frame_index`（contracts.py:601） | `frame_index` → `node["frame_index"]` | 原生源序号，**不重编号**（contracts.py:616） |
| —（结构产品不携带） | `x` → `node["x"]` | **N/A → `null`**；x 由 `curve_scatter` 的 `PointRecord.x` 提供（§2.2） |
| —（结构产品不携带） | `energy` → `node["energy"]` | **N/A → `null`**；能量由 `curve_scatter` 的 `PointRecord.y` 提供（§2.2） |
| —（adapter 默认） | `status` → `node["status"]` | 默认 `"unknown"`（frames.py:53）；vm 生产者按任务状态置 `converged/failed/completed` |
| `FrameRecord.source.artifact`（contracts.py:245） | `geometry_ref` → `node["geometry_ref"]` | 相对工件路径（**非**绝对平台路径）；无 source → `""` |
| `FrameRecord.block_index`（contracts.py:606） | `metadata.block_index` | int；经 caller `metadata` 透传（frames.py:66） |
| `FrameRecord.topology_source`（contracts.py:603） | `metadata.topology_source` | 逐字（contracts.py:618 要求非空） |
| `FrameRecord.coordinate_units`（contracts.py:602） | `metadata.coordinate_units` | `CoordinateUnits.value`（显式，无隐式默认；contracts.py:617） |
| `FrameRecord.source`（SourceRef） | `metadata.source` | `SourceRef.to_dict()`（contracts.py:262）；保留 `native_index` 源编号空间 |
| `FrameRecord.atoms`（`atom_uid`/`element`/`coordinate`） | **无 node 字段** | 几何是独立载荷；由结构查看器端点按 `atom_uid` 消费（见 A04/结构投影）。node 只带 `geometry_ref` |
| `FrameRecord.missing_reason`（contracts.py:609） | **不发 node** | 显式跳过（`skipped_frames`），**绝不补零/伪造 atom_uid**（contracts.py:625-632） |
| `StructureTrajectoryPayload.atom_order`（contracts.py:679） | **无 node 字段** | 稳定 `atom_uid` 身份顺序；交结构端点（A04 侧按身份展示） |
| `StructureTrajectoryPayload.reference_frame`（contracts.py:680） | **无 node 字段** | axes/origin；A 侧坐标轴约定 |
| — | `node["type"]` | `VIEW_REGISTRY[view_type].node_type`（见 §4 A 选择映射） |

### 2.2 `curve_scatter` → node + annotation

| 源字段（product，file:line） | 目标字段 | 单位 / 身份规则 |
|---|---|---|
| `PointRecord.point_id`（contracts.py:378） | `frame_id` → `node["id"]` | 稳定 id（`point_stable_id`，contracts.py:222） |
| `PointRecord.x`（contracts.py:379） | `x` → `node["x"]` | `to_node` 的 `_number`（有限性过滤，frames.py:82）；单位 = `SeriesRef.x_unit` |
| `PointRecord.y`（contracts.py:380） | `energy` → `node["energy"]` | 能量系列；单位 = `SeriesRef.y_unit`；`_number` |
| `SeriesRef.series_id`（contracts.py:333） | `metadata.series_id` | 稳定身份 |
| `SeriesRef.x_unit`（contracts.py:334） | `metadata.x_unit` + A 侧 x 轴单位 | 显式（contracts.py:896 要求 available 系列非空）→ 映射到 `VIEW_REGISTRY[view].x_unit` |
| `SeriesRef.y_unit`（contracts.py:335） | `metadata.y_unit` | 显式；能量单位 |
| `PointRecord.source`（SourceRef） | `metadata.source` / `geometry_ref` | `artifact` → geometry_ref |
| `PointRecord.source.native_index` | `frame_index` | 源编号空间；**不重编号** |
| `PointRecord.missing_reason`（contracts.py:382） | **不发 node** | 显式跳过（`skipped_points`）；带 reason 的点不得带 x/y（contracts.py:384-392） |
| `CurveSeries.missing_reason`（contracts.py:882） | **不发 node** | 整 series 跳过；带 reason 的 series 不得带 points（contracts.py:891-895） |
| 全 series 的有限 `node.energy` 最小值 | **annotation（`type="minimum"`）** | A 侧选择：`min` over finite energy（对齐 `energy_graph.py:372-383`） |
| A/PES 选择（review/recommendation） | **annotation candidate metadata** | `candidate_id`/`active`/`saved`/`recommended_type`/`selection_source`/`confidence`/`reason`/`node_id`/`frame_number`/`scan_step`/`display_label`（frames.py:90-107；对齐 `energy_graph.py:342-365`） |

### 2.3 `vibration` → **N/A** to node/annotation（显式理由）

| 源字段 | 目标 | 理由 |
|---|---|---|
| `VibrationPayload.frequency_units` / `mode_index` / `displacements` / `atom_order` / `displacement_normalization`（contracts.py:1127-1228） | **N/A** | frames/annotation 契约**不携带** mode/位移字段；振动消费走结构查看器振动端点（`acp/results/frequencies.py::build_normal_modes_product` 形状），经 B 侧产品投影（B28b），非 energy viewer。映射表显式标 N/A，**不放松** `to_node` 契约去塞入振动字段。 |
| `VibrationMode.available/missing_reason` | **N/A** | 缺失语义由振动产品信封 + 结构端点呈现（见 A04 缺失原因可见） |

### 2.4 `derived` → **N/A** to node/annotation（显式理由）

| 源字段 | 目标 | 理由 |
|---|---|---|
| `DerivedPayload.sources` / `source_versions` / `parameters` / `raw_io`（contracts.py:1234-1297） | **N/A 直接字段** | 派生产品是**来源/版本溯源信封**；喂 B-T46 研究溯源边投影与 A 候选选择 metadata（如相对能/Boltzmann 派生的 `recommended_type`/`confidence`/`reason`），**不**直接产出 node/annotation 字段。 |

---

## 3. node 字段完整性（对 `frames.py` 真实形状逐键覆盖）

真实 `to_node()` 键（`frames.py:64-87` + 实测 key set，`t13-qa-frames-mapping.txt`）：
`id / label / type / frame_index / x / energy / status / geometry_ref / metadata`（`metadata` 内含 `step/time_ps/coordinate/rms_gradient/max_gradient/basin_id/annotations` + caller 键）。

| node 键 | 映射来源 | 状态 |
|---|---|---|
| `id` | `FrameRecord.frame_id` / `PointRecord.point_id` | 覆盖（§2.1/§2.2） |
| `label` | `FrameRecord.label` 或 `Frame {i+1}` | 覆盖 |
| `type` | `VIEW_REGISTRY[view_type].node_type` | 覆盖（§4） |
| `frame_index` | `FrameRecord.frame_index` / `SourceRef.native_index` | 覆盖 |
| `x` | `PointRecord.x`（结构 N/A → null） | 覆盖（N/A 有理由） |
| `energy` | `PointRecord.y`（结构 N/A → null） | 覆盖（N/A 有理由） |
| `status` | adapter 默认 `unknown` / 任务状态 | 覆盖（默认规则） |
| `geometry_ref` | `SourceRef.artifact` | 覆盖 |
| `metadata` | caller metadata（`block_index`/`topology_source`/`coordinate_units`/`source`/`series_id`/`x_unit`/`y_unit`） | 覆盖 |
| `metadata.step` | **N/A**（无科学产品来源） | adapter 可从任务元数据补；**非**本科学契约字段 |
| `metadata.time_ps` | **N/A** | 采样时间由采样产品/任务提供（T20 侧） |
| `metadata.coordinate` | **N/A** | 扫描坐标由 PES 产品提供（T22 侧） |
| `metadata.rms_gradient`/`max_gradient` | **N/A** | 优化收敛量由任务/优化产品提供 |
| `metadata.basin_id` | **N/A** | 盆指派由采样产品提供（T20 侧） |
| `metadata.annotations` | **N/A** | 标注走 `TrajectoryAnnotation`，非 node metadata |

---

## 4. annotation 字段完整性 + A 选择映射

真实 `to_annotation()` 键（`frames.py:125-146` + `_ANNOTATION_METADATA_KEYS:90-107`，实测 key set）：

| annotation 键 | 映射来源 | 状态 |
|---|---|---|
| `id` | 候选/派生 id | 覆盖（A 选择） |
| `type` | `ANNOTATION_TYPES`（ts/intermediate/minimum/failed/new_basin/cluster_representative/locked/user_selected，frames.py:149） | 覆盖 |
| `label` | `display_label` | 覆盖 |
| `frame_index` | 关联 `node["frame_index"]` | 覆盖 |
| `x` | `node["x"]` | 覆盖 |
| `y` | `node["energy"]` | 覆盖 |
| `status` | `node["status"]` | 覆盖 |
| `geometry_ref` | `node["geometry_ref"]` | 覆盖 |
| `selected` | `active` | 覆盖 |
| `active`/`saved`/`candidate_id`/`recommended_type`/`selection_source`/`confidence`/`reason` | A/PES 候选选择（review/recommendation；派生/人工 review） | 覆盖（A 选择） |
| `node_id`/`frame_number`/`scan_step`/`display_label` | node 身份（`node["id"]` + `frame_index+1` + `scan_step` + 角色标签） | 覆盖（帧身份规则，frames.py:99-106） |
| 未知键 | 落 `metadata` 嵌套 | 覆盖（`to_annotation` 规则） |

**A 选择映射（view_type → node_type，`VIEW_REGISTRY`，frames.py:175-222）**：
`scan→frame` · `scan_trajectory→frame` · `optimization→optimization_cycle` · `conformer→conformer` · `sampling→frame` · `reaction_path→reaction_point` · `irc→irc_point` · `neb→""`（未实现） · `unsupported→""`。
A 按 `view_spec(view_type)`（`frames.py:225`）选择，未知 view 落 `unsupported` 回退。

---

## 5. Fixture 集（`tests/fixtures/science_products/frames_mapping/`）

五态齐全，**每个 `.expected.json` 是真实 `to_node()/to_annotation()` 形状的映射输出**（由 `.omo/evidence/.../t13-frames-mapping-gen.py` 调用真实契约生成；非「合法 JSON」占位）。

| 状态 | 源（product） | 期望映射输出（`.expected.json`） | 断言要点 |
|---|---|---|---|
| normal | `structure_trajectory` v1，2 帧可用，view=scan，node_type=frame | `nodes=2`（`id`=`structure_trajectory:frame:N`、type=frame、metadata 带 `block_index/topology_source/coordinate_units/source`），`skipped_frames=[]`，`annotations=[]` | `complete=true`；node 键集 = §3 |
| empty | v1 `state=unavailable` + `source_missing`，`payload=null` | `nodes=[]`，`unavailable={"state":"unavailable","missing_reason":"source_missing"}` | **typed unavailable，绝不空成功** |
| partial | v1，帧 1 首原子**缺 `atom_uid`**，`complete=false` | `nodes=2`（仅帧 0/2），`skipped_frames=[{"frame_index":1,"missing_reason":"missing_atom_uid"}]` | **不伪造/不补零**（帧 1 无 node） |
| corrupt | v1，`frame_index="zero"`（字符串） | `{"error":"ProductSchemaError","stage":"product_from_dict","message":...}`，`nodes=[]` | **typed 明确失败** |
| old | legacy v0（`version:0`） | `nodes=2`（迁移后），`migrations=[v0→v1 三条决策]`，view=irc/node_type=irc_point | 旧版转换路径 + 决策可见 |
| （附加）curve_annotation | `curve_scatter` v1，3 点 | `nodes=3`（x/energy/单位 metadata）+ `annotations=[minimum]`（`type=minimum`） | 演示 **annotation 映射**（`to_annotation`） |

> 五个必需状态（normal/empty/partial/corrupt/old）全部存在；`curve_annotation` 为 annotation 映射的附加演示（不替代五态）。

---

## 6. 实装闭包表（每产品一行，评审 P1-9）

> 列：真实生成入口（C） → C 产品 writer/reader → B Data 登记/读取 → B frames/annotation adapter → A 消费与选择映射 → 验收任务。
> **CCCP 不 import `acp.results`**；**平台 manifest 登记不由 CCCP 写**（B 写者）。

| 产品 kind | ① 真实生成入口（C，file:line + 生产接线任务） | ② C writer/reader（file:line） | ③ B Data 登记/读取 | ④ B frames/annotation adapter | ⑤ A 消费与选择映射 | ⑥ 验收任务 |
|---|---|---|---|---|---|---|
| `structure_trajectory` | `products.structure.build_structure_trajectory_product`（structure.py:43）；生产接线：**T20**（采样帧）、**T22**（PES scan/path）、`cccp/calculation/tasks/{irc,optimize,xtb_path_search}.py`（轨迹任务）；发布时机=result-publication | `serialize.product_to_dict`/`product_from_dict`（serialize.py:96/104）+ `migrate_v0_to_v1`（serialize.py:340） | `acp/storage/manifest.py`（result_manifest v2 写，product kind）+ `acp/results/manifest.py`（读）— **B-T28a** | **B-T28b**：产品→`TrajectoryFrame`→`to_node()`（view_type→node_type 见 §4） | A04（plan1 §3）：energy viewer；`acp/results/{energy_graph,irc_projection,sampling_graph}.py` 现有形状；node_type 经 `VIEW_REGISTRY` 选择 | A-GT3（exit=单位/身份/缺失原因逐项一致）；B-T75 |
| `curve_scatter` | `products.curve.build_curve_scatter_product`（curve.py:42）；生产接线：**T20**（sampling history）、**T22**（PES scan profile）、IRC 任务 | 同上（同一 reader/writer） | 同上（B-T28a） | **B-T28b**：产品→`TrajectoryFrame`(x/energy)→`to_node()` + 最小/候选 `TrajectoryAnnotation.to_annotation()` | A04：energy viewer scan/irc/sampling；x 轴单位经 `VIEW_REGISTRY[view].x_unit` | A-GT3 |
| `vibration` | `products.vibration.build_vibration_product`（vibration.py:51）；生产接线：**T19**（振动产品归并）+ **T18**（ORCA 只读解析） | 同上 | 同上（B-T28a） | **B-T28b**：产品→结构查看器振动投影（`acp/results/frequencies.py::build_normal_modes_product` 形状；**N/A** to node/annotation，见 §2.3） | A04：结构查看器 `GET .../vibrations`（非 energy viewer）；`mode_index`/`frequency_units`/`displacement_normalization`/`atom_order` 保真 | A-GT3 / A05 |
| `derived` | `products.derived.build_derived_product`（derived.py:45）；生产接线：**T21**（Boltzmann/selection）、**T20/T22** | 同上 | 同上（B-T28a） | **B-T28b**：溯源信封→B-T46 研究溯源边投影 + A 候选选择 metadata（`recommended_type`/`confidence`/`reason`） | A04：候选选择/溯源展示；不在 JS 重算权威判据 | A-GT3 / B-T46 |

### 6.1 B-T28a / B-T28b 分工（评审 R3/R4）

| 子项 | 范围 | 前置 | 消费本 HN |
|---|---|---|---|
| **B-T28a** | 既有 Data 发布 / markers 入口收敛（`result_publication` + `_upload_scheduler_markers`）；manifest 收敛 | 保留现有行为/时序，**不等待**整套新科学产品 | 无（与本契约解耦） |
| **B-T28b** | 科学产品→frames/annotation adapter；旧结果 reader/转换；Data 登记/读取与发布/读取接线 | **等 C-T13（本 HN）契约/fixture** + 对应生产入口 | **本 HN 全部**（§2/§5/§6） |

> B-T28b **必须**经 `TrajectoryFrame.to_node()` / `TrajectoryAnnotation.to_annotation()` 输出（consumer 验收 A-GT3）；旧结果用兼容 reader/转换，**不复制科学算法**（本契约只提供字段映射，未提供算法）。

### 6.2 C 侧生产入口归属（T18/T19/T20/T22 等）

| 生产入口任务 | 归属产品 | 说明 |
|---|---|---|
| **T18** C03-s3 ORCA 只读解析归并 | vibration（供频率解析） | 解析唯一实现下沉 cccp，不启动程序 |
| **T19** C03-s4 振动产品归并 | vibration | `build_normal_modes_product` 迁 cccp + 真实解析路径生成样例 |
| **T20** C04-s1 采样核心迁移 | structure_trajectory / curve_scatter | 采样帧 / 饱和度曲线真实生产入口 |
| **T21** C04-s2 Boltzmann/选择迁移 | derived（相对能/权重） | 派生产品来源 |
| **T22** C04-s3 PES 科学迁移 | structure_trajectory / curve_scatter | scan/path 帧 / 能量曲线真实生产入口 |

> 各任务的「生产调用链证据」随对应任务交付（评审 P1-9），**不在本 HN**；本 HN 只冻结映射与闭包行。

---

## 7. 与 B-T28a/B28b + A04/A-GT3 的接口约束

- **CCCP 零 `acp.results` 依赖**：§8 grep 证明；产品包只 import `cccp.calculation.*` + stdlib。
- **平台 manifest 登记不由 CCCP 写**：写者 = `acp/storage/manifest.py`（B-T28a）；CCCP 只产 `ScienceProduct` 信封（纯数据）。
- **不绕过 `to_node()/to_annotation()`**：B-T28b 必须调用真实契约；A 消费经 B 的 adapter（A04 条款「消费 C 科学产品经 B 的 frames/VIEW_REGISTRY adapter，保留原子/帧/模式 ID、单位/参考态和缺失原因」）。
- **A-GT3 退出**：单位/身份/缺失原因逐项一致；A04 保持有界加载、不在 JS 重算权威判据。

---

## 8. 证据（evidence）

### 8.1 零 `acp` import（required grep）

```
$ grep -rn "acp.results\|from acp" src/cccp/calculation/products --include='*.py'
EXIT=1 (1 = zero hits)
```
（另：`grep -rn "import acp\|from acp" src/cccp/calculation/products --include='*.py'` → 同样 EXIT=1。原文见 `t13-zero-acp-imports.txt`。）

### 8.2 字段完整性 + fixture 校验（`t13-qa-frames-mapping.txt`）

```
== field completeness: real to_node()/to_annotation() key sets ==
node keys            : ['annotations','basin_id','caller','coordinate','energy','frame_index',
                        'geometry_ref','id','label','max_gradient','metadata','rms_gradient',
                        'status','step','time_ps','type','x']
annotation keys       : ['active','candidate_id','confidence','display_label','frame_index',
                        'frame_number','geometry_ref','id','label','metadata','node_id','other',
                        'reason','recommended_type','saved','scan_step','selected','selection_source',
                        'status','type','x','y']
== fixture validation (5 states + annotation demo) ==
  normal           state=available   complete=True  nodes=2 annotations=0 skipped=[]
  empty            state=unavailable complete=False nodes=0 annotations=0 skipped=[]
  partial          state=available   complete=False nodes=2 annotations=0 skipped=[{'frame_index': 1, 'missing_reason': 'missing_atom_uid'}]
  old              state=available   complete=True  nodes=2 annotations=0 skipped=[]
  curve_annotation state=available   complete=True  nodes=3 annotations=1 skipped=[]
  corrupt          typed ProductSchemaError: frame_index must be an integer, got str
ALL FIXTURE ASSERTIONS PASS
```

### 8.3 QA — 真实帧样例映射（`t13-qa-real-frame.txt`）

- **源样例**：`tests/fixtures/irc/h2o2_IRC_F_trj.xyz`（真实 IRC 轨迹，6 帧；注释能量 `-151.5279…`→`-151.5340…`）。
- **真实生成**：解析 6 帧 → `build_structure_trajectory_product`（真实生成函数）→ `state=available`，`identity.generator=cccp.calculation.products.structure.build_structure_trajectory_product`。
- **映射输出**：`view_type=irc` → `node_type=irc_point`，`nodes=6`（`id=structure_trajectory:frame:N`，`type=irc_point`，`metadata.topology_source=orca_irc_xyz`/`coordinate_units=angstrom`；`geometry_ref=""` 因源块无 artifact 引用 — 正确，未伪造路径）。`skipped_frames=[]`。
- **一致性**：单位（angstrom）/ 帧身份（原生 frame_index 0..5）/ 缺失原因（无缺失，未补零）与源一致 → **映射经真实 `to_node()` 成立，未放松契约、未改 `frames.py`**。原文见 `t13-qa-real-frame.txt`。
- 生成器/校验脚本：`t13-frames-mapping-gen.py`（生成 fixture expected，调用真实 `to_node/to_annotation`）、`t13-qa-frames-mapping.py`（字段完整性 + 五态断言）。二者均为 **evidence（`.omo/`，gitignored）**，**非** B 侧 adapter 实现。

### 8.4 tri-state 证据表

| 项 | 状态 | 依据 |
|---|---|---|
| 映射表逐字段（源→目标→单位/身份） | PASS | §2（file:line 接地） |
| node/annotation 字段完整性（真实形状逐键） | PASS | §3/§4 + §8.2 |
| fixture 五态齐全（各附期望映射输出） | PASS | §5 + §8.2 |
| 实装闭包表逐产品（B28a/B28b + A 引用 + C 生产入口） | PASS | §6 |
| cccp 侧零 `acp.results` import | PASS | §8.1 |
| 真实帧样例映射（IRQ fixture） | PASS | §8.3 |
| **状态 B 跨路端到端验收** | **PENDING** | 待 B28b + A04/A-GT3 接入（§9.3），非本 todo 条件 |
| 真实 QC 软件运行 | NOT_APPLICABLE | 纯数据契约/映射，无子进程 |

---

## 9. 两状态记录

### 9.1 状态 A — 契约可消费 = **DELIVERED（本 todo 完成条件）**

- 映射表（§2）、字段完整性（§3/§4）、五态 fixture（§5）、实装闭包表（§6）、零 acp import（§8.1）齐备。
- **B 可立即开始 B-T28b**；A04/A-GT3 可在 adapter 落地后接入。

### 9.2 状态 A 不依赖状态 B

状态 B 未成立**不阻碍**本 HN 交付，也**不阻碍** B28b 开工（R4 §3.3：避免互相等待）。

### 9.3 状态 B — 跨路端到端验收（**单独记录，PENDING**）

| 项 | 值 |
|---|---|
| 固定输入 | 待 B 选择（建议 `tests/fixtures/irc/h2o2_IRC_F_trj.xyz` 或 scan fixture 全链路） |
| 判据 | C 生产入口 → B 发布/投影 → A/Python 消费逐项一致：单位 / `atom_uid` / frame / point / 原生 mode / 缺失原因 / 块索引 |
| 关闭条件 | 仅 schema 测试**不能**关闭；需 B28b + A04/A-GT3 真实联调证据 |
| status | `pending`（B/A 接入后回填） |

---

## 10. 风险 / 未决

- **B-T28b 未实装**：映射为规范，非实现；B 须经 `to_node/to_annotation()` 输出，本 HN 不写 B 侧 adapter（防越权）。
- **振动/派生 N/A**：frames/annotation 契约不承载 modes/溯源；振动走结构查看器端点、派生走溯源/候选 metadata — 已在 §2.3/§2.4 显式标 N/A 并给理由（未放松契约）。
- **`metadata.step/time_ps/coordinate/rms_gradient/basin_id`**：无科学产品直接来源（§3 标 N/A）；由任务/采样/优化产品（T20/T22）或 adapter 从任务元数据补 — 待 B28b 接入时对齐来源。
- **状态 B 未定固定输入**：由 B 在联调时选择；本 HN 不预设。
- **期望输出由临时脚本生成**：脚本属 evidence（gitignored）；若 B 发现映射与真实 `to_node` 漂移 → 修订映射（不放松契约）。本 HN 已用真实 `to_node/to_annotation` 生成期望，漂移风险低。
