# ACP 结构查看器合并与增强方案

**状态**：历史设计草案（2026-09-10，已由 [UX 修订方案 v2.0](ACP_Structure_Viewer_UX_Revision_Plan.md) 取代）
**范围**：Workbench 主界面“构象集合”与“三维结构”合并、任务结果自动识别、轻量几何编辑、虚频方向展示  
**不在本期范围**：完整分子搭建器、原子/键增删、自动成键修复、约束优化、力场最小化、计算结果原地覆盖

## 1. 结论

将主界面的“构象集合”和“三维结构”合并为唯一的 **结构查看器 / Structure Viewer**。它负责：

1. 自动识别当前任务最值得查看的结构，并保留手动从文件树打开结构的能力；
2. 在一个界面内浏览单结构、构象集合、PES 推荐点、优化轨迹末帧和振动模式；
3. 提供键长、键角、二面角的轻量修改，支持撤销、重做、重置、导出及“另存为结构资产”；
4. 对 TS 频率结果显示虚频数量、频率值、位移箭头和往复动画；
5. 只读取正式结果或明确标注的运行中/失败快照，不把 PES 自动推荐误当作已确认的下游输入，也不改写原始结果。

“能量与轨迹”继续作为数据分析视图，但其节点选择与结构查看器共享同一个结构文档状态。中期应移除当前第二套 `energy-structure-viewer` 加载逻辑，避免同一结构在两个 3Dmol 实例中产生不一致。

## 2. 当前实现审计

### 2.1 已有能力

- `frontend/ACP_Workbench_v2.html` 已有 3Dmol 主查看器、球棍/线框等样式、原子选择、键长/键角/二面角测量、多帧 XYZ 播放、截图和标签。
- “构象集合”标签目前没有独立实现，进入后落到“功能开发中”；构象能量与 Boltzmann 权重实际已在“能量与轨迹”的 `conformer` 视图中展示。
- `RESULT/confsearch/confsearch_manifest.json` 已包含每个构象的几何引用、排名、能量、相对能量和 Boltzmann 权重。
- PES 的 `pes_profile.json` 和 `pes_recommendations.json` 已包含扫描帧及 TS/INT 推荐；推荐结果按现有约定是审计数据，只有人工确认后才成为正式 `kind: structure` 产品。
- 优化轨迹已有规范化的 cycle、能量、收敛量和 `geometry_ref`，失败任务也可能保留最后一个有效周期。
- `result_manifest.json` 已定义 `structure`、`frequency_modes`、`ensemble`、`trajectory` 等产品类型。
- `cccp.qc.interfaces.orca_ts.parse_ts_mode_vectors()` 已能解析 ORCA `NORMAL MODES` 位移矩阵，TS 优化路径也能取得最负虚频对应的 `mode_vector`。

### 2.2 主要缺口

- 选择任务后不会自动加载结果结构；主查看器仍要求用户从文件树点选 `.xyz`。
- 结构来源解析散落在文件树、energy graph、structure source 和各工作流清单中，前端无法可靠处理失败任务、批量任务和远程任务。
- 普通 frequency primitive 只保留频率数值及原始输出，没有把法向模式标准化为前端可消费产品。
- `acp.results.frequencies.build_frequency_report()` 当前固定输出 `normal_modes_available: false`。
- 测量值只能看，不能改；没有编辑事务、撤销/重做、碰撞检查和“未保存修改”状态。
- 主查看器和能量面板各维护一个 3Dmol 实例及几何加载逻辑。
- 多帧 XYZ 的能量目前靠注释中的首个浮点数猜测，不能可靠承载 Boltzmann、角色、状态和 provenance。

## 3. 产品形态

移除“构象集合”顶层标签，将“三维结构”重命名为“结构查看器”。建议布局如下：

```text
┌ 结构查看器 ─────────────────────────────────────────────────────────┐
│ [来源: 自动▼] [结构/构象搜索框] [球棍▼] [标签] [截图] [导出]       │
├───────────────┬──────────────────────────────────┬─────────────────┤
│ 结构列表       │                                  │ 检查器          │
│               │            3D 画布               │ 状态/来源/能量   │
│ PES 推荐点     │                                  │ Boltzmann       │
│ 或构象排名     │                                  │ 振动模式        │
│ 或批量项目     │                                  │ 测量/编辑       │
├───────────────┴──────────────────────────────────┴─────────────────┤
│ 多帧/振动播放：[◀] [播放] [▶] [滑杆]  速度  振幅  箭头             │
└────────────────────────────────────────────────────────────────────┘
```

窄屏时结构列表和检查器改为抽屉，不压缩 3D 画布。只有集合或批量结果时显示左侧结构列表；单结构任务默认隐藏。

### 3.1 查看与编辑模式

- 默认是“查看”模式；拖动始终用于旋转分子，不与编辑抢事件。
- “测量”仍按 2/3/4 个原子生成距离、角度、二面角。
- 测量完成后，检查器显示当前值和“修改”按钮；用户输入目标值并预览。
- 每次修改是一条可撤销事务；`Ctrl+Z`/`Ctrl+Shift+Z` 撤销/重做。
- 编辑后显示“已修改，未保存”。切换结构前若存在修改，提示“放弃 / 另存为 / 取消”。
- 只允许“下载 XYZ”“另存为结构资产”“以此结构新建计算”，禁止覆盖 `RESULT/`、`WORK/` 或原文件。

## 4. 统一数据契约

新增 `src/acp/results/structure_viewer.py`，由后端聚合工作流差异。前端不读取物理路径、不按文件名猜结构，也不自行解析工作流清单。

### 4.1 StructureViewerPayload

```json
{
  "schema_version": "structure_viewer_v1",
  "job_id": "...",
  "workflow": "Confsearch",
  "job_status": "completed",
  "revision": "manifest-mtime-or-content-fingerprint",
  "default_entry_id": "conf_0001",
  "groups": [
    {"id": "final_conformers", "label": "最终构象", "kind": "ensemble"}
  ],
  "entries": [
    {
      "id": "conf_0001",
      "group_id": "final_conformers",
      "label": "构象 1",
      "role": "minimum",
      "status": "completed",
      "geometry": {"endpoint": "/api/v1/jobs/.../structure-viewer/entries/conf_0001/geometry", "format": "xyz"},
      "energy": {"value": -123.456, "unit": "hartree", "kind": "gibbs"},
      "relative_energy_kcal": 0.0,
      "boltzmann_weight": 0.73,
      "source": {"kind": "confsearch_manifest", "product_id": "...", "frame_index": null},
      "badges": ["rank-1", "selected"],
      "vibrations": {"available": false, "endpoint": null}
    }
  ],
  "warnings": []
}
```

约束：

- `entries[].id` 在一个任务及 `revision` 内稳定；不要使用数组下标作为身份。
- `geometry.endpoint` 返回文本 XYZ；路径解析必须经 `resolve_safe` 或现有 manifest 几何守卫。
- `source.kind` 明确区分 `formal_result`、`algorithm_recommendation`、`manual_review`、`last_valid_cycle`、`calculation_input` 和 `manual_file`。
- `algorithm_recommendation` 必须显示“自动推荐、尚未确认”徽标，并禁止直接作为 BatchOptimize 的正式来源。
- 所有来自能量/轨迹视图的条目继续由 `TrajectoryFrame` / `TrajectoryAnnotation` 产生或引用，不能绕过现有 frame contract 再造一套帧投影。

### 4.2 振动模式契约

新增稳定产品 `RESULT/frequencies/normal_modes.json`：

```json
{
  "schema_version": "normal_modes_v1",
  "units": {"frequency": "cm-1", "displacement": "dimensionless_orca_normal_mode"},
  "atom_count": 12,
  "geometry_product_id": "batch_opt_item_001",
  "modes": [
    {
      "mode_index": 6,
      "frequency_cm1": -797.72,
      "imaginary": true,
      "ir_intensity": 24.8,
      "vectors": [[0.01, -0.02, 0.03]]
    }
  ]
}
```

要求：

- 保留 ORCA 原始 `mode_index`，频率、IR 强度和向量按该索引对齐，不能用过滤零模后的数组位置对齐。
- 写入前验证 `len(vectors) == atom_count`、每行 3 个有限数；单个损坏模式可跳过并写 warning，不能使整个任务结果不可读。
- 模式数据与产生该 Hessian 的几何绑定。若频率计算使用的结构与当前显示结构不一致，禁用动画并提示原因。
- `result_manifest.json` 只登记一个 `kind: frequency_modes` 产品；不把每个 mode 注册成单独产品。
- 历史任务没有该 JSON 时，可对本地或已拉取的 ORCA 输出做只读即时投影；不修改历史任务目录。

### 4.3 API

新增或调整：

| 方法 | 端点 | 用途 |
|---|---|---|
| GET | `/api/v1/jobs/{id}/structure-viewer?item_id=` | 返回统一结构目录及默认选择 |
| GET | `/api/v1/jobs/{id}/structure-viewer/entries/{entry_id}/geometry` | 安全读取一个结构 |
| GET | `/api/v1/jobs/{id}/structure-viewer/entries/{entry_id}/vibrations` | 返回与该结构绑定的标准化模式 |
| POST | `/api/v1/structure-assets` | 复用现有结构资产接口，另存编辑结果 |

远程任务由服务端复用 remote fetcher 按需获取 manifest/几何/频率输出，并缓存到受控区域；浏览器不接触远程绝对路径。接口在结果尚未同步时返回明确的 `availability: pending_fetch`，前端展示重试状态而不是空白。

## 5. 各任务的自动加载规则

解析顺序必须集中在 `structure_viewer.py`，并有单元测试锁定。通用优先级为：

```text
正式 RESULT 产品
  > 工作流规范清单中的可展示结果
  > 最后一个有效轨迹帧（允许 failed/running）
  > 计算输入结构
  > 无结构
```

具体规则：

| 工作流 | 展示集合 | 默认选中 | 状态处理 |
|---|---|---|---|
| PESsearch | `pes_profile` 中 TS/INT 推荐点；另分组显示人工已确认点 | 最高置信度 TS 推荐；无 TS 时最高能峰推荐 | 自动推荐加“未确认”徽标；人工确认点单独标记 |
| Confsearch | `confsearch_manifest` 全部最终构象 | rank 1 | 显示 ΔE、G、Boltzmann 条形；可按权重/能量/排名排序 |
| BatchOptimize | 每个 batch item 的终态结构 | 当前批量项；否则第一个完成项 | completed 用正式优化结构；failed 用该项最后有效优化周期；TS 项自动关联其频率模式 |
| optimize / xtb-optimize | 正式 optimized structure 或最后有效优化周期 | 最终/最新帧 | 失败时明确显示“未收敛 · 最后有效结构”，不称为优化结构 |
| singlepoint | 计算输入结构 | 唯一结构 | 显示最终能量；文案注明几何未改变 |
| frequency | 频率计算输入结构 | 唯一结构 | 自动打开最负虚频；无虚频时默认第一个振动模式 |
| scan | 扫描帧集合 | 推荐点或最低能帧 | 复用 `TrajectoryFrame` 和扫描 frame endpoint |
| irc | IRC 路径及端点（待现有 IRC frame 投影补齐） | TS/路径中心 | 正向、反向端点分组；不得把路径顺序按能量重排 |
| 无规范结果/历史任务 | manifest 中结构；再回退到兼容只读解析 | 最可信的最终结构 | 在 UI 标出“兼容模式”及来源 |

对于运行中的任务，结构目录按 `revision` 增量刷新，但不得在用户正在编辑时替换当前坐标。出现新结果时显示“有新结构可用”，由用户选择刷新。

## 6. 轻量几何编辑

### 6.1 支持范围

第一版仅支持对 **已有共价连接** 的内部坐标修改：

- 键长：选择两个相连原子；
- 键角：选择 A-B-C，A-B 与 B-C 必须相连；
- 二面角：选择 A-B-C-D，三段连接必须存在；
- 每次只修改一个内部坐标；
- 原子元素、总电荷、自旋多重度和键拓扑不变。

不支持原子/键增删、改元素、画环、芳香键编辑、片段拼接或自动加氢。上述能力会显著扩大化学感知、价态、立体化学和清理算法的范围，不符合轻量目标。

### 6.2 移动规则

构建只用于编辑的邻接图；优先使用解析文件中的显式键，XYZ 则使用共价半径和距离阈值推断，并把推断来源显示给用户。

- 键长 A-B：临时切断 A-B，刚性平移 B 一侧片段，使距离达到目标值。
- 键角 A-B-C：临时切断 B-C，绕过 B 且垂直于 A-B-C 平面的轴旋转 C 一侧片段。
- 二面角 A-B-C-D：临时切断 B-C，绕 B-C 轴旋转 C/D 一侧片段。
- 默认移动切键后原子数较少的一侧；检查器允许切换“移动左侧 / 右侧”。
- 如果切键后图仍连通（典型环键），第一版拒绝修改并提示“环内坐标需要高级编辑”；不能只移动单个原子破坏环结构。
- 共线角导致旋转轴不唯一时，选择与当前主惯性轴最稳定的正交轴，同时显示警告。

实现放在独立前端模块（拆分 HTML 时建议 `frontend/js/structure_editor.js`），使用纯向量/刚体变换，不调用后端 QC，也不在修改后自动做力场优化。

### 6.3 安全提示与事务

- 输入范围：键长 `0.4–5.0 Å`、键角 `1–179°`、二面角规范到 `(-180, 180]°`；超范围拒绝。
- 修改后计算非键原子间距离，小于共价半径和的 55% 时显示严重碰撞警告，但允许用户撤销。
- 可增加轻量价态提示，但不能以推断价态为理由偷偷改变键级或加氢。
- 编辑事务记录 `{type, atom_ids, before, after, moved_atom_ids}`，撤销/重做只作用于当前 entry。
- 导出 XYZ 第二行加入 provenance：父任务、父结构 entry、编辑操作摘要；结构资产的 metadata 保存完整操作列表。

## 7. 虚频方向展示

### 7.1 交互

- 检查器列出全部频率；负值置顶并以红/橙色标识。
- TS 默认选择最负虚频，显示“虚频 1 / N、-797.72 cm⁻¹”。
- 画布可切换“箭头”“动画”“箭头 + 动画”。箭头从平衡坐标指向当前相位位移方向。
- 提供播放/暂停、速度（0.25×–2×）、振幅（建议默认最大原子位移 0.25 Å，上限 0.6 Å）和相位反转。
- 动画坐标为 `r_i(t) = r_i0 + A sin(φ) normalize(v_i)`；显示归一化只影响可视化，不改原始向量。
- 播放时禁用几何编辑；停止后恢复平衡结构。
- 提供“导出 + 位移 / - 位移结构”作为后续增强，默认不把动画帧保存成正式 TS。

### 7.2 TS 判断提示

- `1` 个显著虚频：显示“频率数量符合一阶鞍点”，但明确提示“仍需检查振动方向及 IRC”。
- `0` 个显著虚频：显示“不是一阶鞍点证据”。
- `>1` 个显著虚频：显示“高阶鞍点或未充分优化”。
- 显著虚频阈值应读取任务实际 quality-gate 配置；缺失时沿用现有 `-50 cm⁻¹` 默认，并在响应中返回阈值来源。
- 查看器只展示证据，不替代现有 BatchOptimize/IRC 的 TS 校验状态。

## 8. 目前还欠缺的功能

按优先级建议如下。

### P0：结构可信与基本可用性

1. **原子身份稳定**：跨构象/轨迹使用稳定 atom id 或映射；若原子顺序变化，禁用跨帧测量保持和振动绑定。
2. **来源与状态可见**：必须区分正式结果、推荐点、失败末帧、输入结构和手动文件，避免用户把“能显示”误认为“已收敛”。
3. **单位和能量语义**：明确 E/G/ΔE、Hartree/kcal·mol⁻¹、温度和 Boltzmann 权重来源。
4. **错误与部分结果**：文件缺失、远程尚未拉取、模式与几何不匹配时给出可行动错误，而不是空白画布。
5. **大体系性能**：按需加载几何、列表虚拟化、轨迹抽样；超过阈值默认 stick/wireframe 并关闭标签。

### P1：化学查看体验

1. 显示/隐藏氢、原子编号、元素、部分电荷和自旋密度（有数据才显示）。
2. 按元素/残基/选择集着色，透明度和键半径设置。
3. 正交/透视投影切换、固定旋转中心、标准视角和结构对齐。
4. 两个构象叠合与 RMSD，对称映射复杂时使用已有 RDKit 能力在后端计算。
5. 按元素、编号、距离或结构角色选择原子；保存命名选择集。
6. 更可靠的截图：透明背景、分辨率倍率、图例和当前频率/构象注记。

### P1：任务结果联动

1. 能量图点选结构与主查看器双向同步，并保留相机状态。
2. PES 推荐点在 3D 中突出扫描原子/成键断键方向，显示推荐理由和置信度。
3. 优化结果显示初始/最终叠合、最大位移原子及收敛失败原因。
4. IRC 正反路径动画和端点对比；这是确认 TS 连通性的直接证据，优先级仅次于虚频动画。
5. 结构间切换保留共同原子的测量；不能证明映射时清除并提示。

### P2：可复用与协作

1. 编辑结构另存为项目级 structure asset，并记录父任务与编辑 provenance。
2. 一键把当前结构预填到“新建任务”，但仍由用户确认方法、charge 和 multiplicity。
3. 保存视图状态（相机、样式、选择、测量），但与计算结果分开存储。
4. 分享/导出会话清单：当前 entry、模式、测量和截图元数据。

明确不建议近期加入：完整 SMILES 绘制器、自由拖拽单原子、自动键级推断修复、实时 MM 优化、晶体周期边界编辑。这些应由专门编辑器或独立模块承担。

## 9. 分阶段实施

### 阶段 A：合并外壳与自动加载（建议先做）

目标：用户选中任何任务后，结构查看器立即给出正确默认结构。

- 删除 `data-tab="conformers"`，将 `data-tab="3d"` 迁移为 `data-tab="structure"`；保留一次旧 tab 名到新 tab 的兼容映射。
- 建立结构列表、检查器和统一前端 `structureViewerState`。
- 新增 `structure_viewer.py` 聚合器和三个只读 API。
- 接入 PES、Confsearch、BatchOptimize、optimize、singlepoint、frequency 的加载矩阵。
- 手动文件打开改为 `source.kind=manual_file` 注入同一 store。
- 首次仅让“能量与轨迹”向共享 store 推送选择，不急于删除其小型查看器。

验收：上述六类工作流及 completed/failed 两种状态均能自动显示正确结构和来源徽标。

### 阶段 B：虚频模式

目标：TS 结果可直接检查虚频方向。

- 把 `parse_ts_frequency_map` / `parse_ts_mode_vectors` 抽成普通 ORCA frequency 与 TS 共用的结果解析能力。
- frequency primitive 在成功或存在可解析部分输出时写 `normal_modes.json`。
- BatchOptimize 将各 item 的模式产品与对应 optimized structure 建立 metadata 绑定。
- 完成模式 API、箭头和 `requestAnimationFrame` 动画。
- 历史任务加入只读即时解析回退。

验收：一个原子数 N 的 ORCA 示例能显示正确 mode index、频率和 N×3 箭头；切换非绑定结构时动画自动禁用。

### 阶段 C：轻量编辑

目标：可靠修改非环内部坐标，并安全地产生新结构。

- 实现邻接图、片段切分、平移/旋转数学、输入校验和碰撞提示。
- 接入测量检查器、移动侧切换、undo/redo/reset。
- 复用 `/structure-assets` 另存，增加 parent entry 与 edit operations metadata。
- “新建计算”只接收当前内存 XYZ 的副本，不改动父任务。

验收：链状分子的键长、角度、二面角修改达到目标误差（距离 ≤1e-4 Å，角度 ≤1e-3°），非移动片段坐标不变；环键编辑被拒绝；撤销逐位恢复。

### 阶段 D：统一查看器与高级联动

目标：去掉重复 3D 实现并补全路径证据。

- energy graph、PES review、结构查看器共享一个 geometry loader、样式和相机 store。
- 实现 IRC frame projection 与动画。
- 加入结构叠合/RMSD、性能策略、保存视图状态和可访问性完善。

## 10. 预计修改位置

| 文件 | 修改 |
|---|---|
| `frontend/ACP_Workbench_v2.html` | 合并标签、结构列表/检查器、共享 state、自动加载、振动 UI、编辑入口、i18n |
| `src/acp/results/structure_viewer.py` | 新增统一结构目录构建器与工作流 resolver |
| `src/acp/results/frequencies.py` | 扩展 `normal_modes_v1` 投影，不再固定 unavailable |
| `src/acp/results/orca_parser.py` | 保留 mode index，并解析/对齐 normal mode vectors |
| `src/acp/results/frames.py` | 只添加必要的来源 metadata；不建立平行帧合同 |
| `src/acp/api/v1_routes.py` | 新增 structure-viewer 目录、几何、振动只读端点 |
| `src/acp/calculations/primitives/frequency.py` | 物化标准化模式产品和部分结果 warning |
| `src/acp/calculations/executor.py` | 修正 frequency 产品登记路径/metadata，与几何产品绑定 |
| `src/acp/calculations/batch/engine.py` | 每个 item 的终态/失败末帧及 frequency binding |
| `src/cccp/qc/interfaces/orca_ts.py` | 复用并加固 ORCA mode index/vector 解析 |
| `tests/test_structure_viewer.py` | resolver 矩阵、优先级、路径安全、历史回退 |
| `tests/test_frequency_modes.py` | 索引对齐、向量形状、损坏模式、几何绑定 |
| `tests/test_frontend_sync.py` | 单一结构标签、禁用结果覆盖、兼容 tab、UI 合同 |

若开始实施，应先把 `ACP_Workbench_v2.html` 中结构查看器逻辑拆为独立 JS/CSS 文件；该单文件已经过大，继续把编辑数学与模式动画内嵌进去会显著增加回归风险。

## 11. 测试与验收矩阵

### 后端

- 每种工作流的默认 entry 和完整 entry 顺序；
- completed、failed、cancelled、running、remote pending-fetch；
- manifest 缺失/损坏、geometry 缺失、路径逃逸；
- PES 自动推荐与人工确认严格分组，自动推荐不能变成正式结构产品；
- Confsearch Boltzmann 权重和为 1 的展示容差及缺失权重降级；
- BatchOptimize 多 item、单 item 失败、频率仅部分完成；
- ORCA 分块 `NORMAL MODES`、多个频率 section、零模过滤、mode index 对齐；
- 历史任务只读回退不产生磁盘写入。

### 前端

- 选择任务自动加载且不会被慢请求的旧响应覆盖（AbortController + request token）；
- 编辑中轮询不替换坐标；
- 列表切换、能量图点选和主查看器选择同步；
- 虚频播放停止后精确恢复平衡坐标，切 tab/切 job/销毁 viewer 时取消动画；
- 编辑与动画互斥，undo/redo/reset 正确；
- 键盘操作、焦点状态、中文/英文、窄屏抽屉；
- 3Dmol 未加载、WebGL 丢失、大分子时有明确降级提示。

### 化学正确性

- 修改内部坐标达到目标值；非移动片段保持刚体不变；
- 二面角跨越 `±180°` 时选择最短旋转方向；
- 环键和断开的原子序列被拒绝；
- 振动位移不改变 atom ordering，不混用不同几何的 mode；
- TS 虚频计数展示与现有 BatchOptimize/IRC quality gate 一致。

## 12. 建议的首个开发批次

首批只完成阶段 A 与阶段 B 的数据链路，不同时上几何编辑。原因是自动结果识别和 mode/geometry 正确绑定决定了查看器展示是否可信，且可立即解决当前截图中“选中已完成任务但画布仍等待手动打开 XYZ”的核心问题。

建议首批交付边界：

1. 合并标签并完成新布局；
2. 六类主要工作流自动加载；
3. Confsearch 构象列表、能量和 Boltzmann 分布；
4. PES TS/INT 推荐点列表与未确认标识；
5. optimize/BatchOptimize 成功终态与失败末帧；
6. ORCA 虚频箭头和动画；
7. 完整 resolver/API/前端合同测试。

第二批再加入内部坐标编辑和结构资产另存。这样可以把“结果可信性”与“坐标可变性”分开验收，降低对计算结果和下游任务输入的误操作风险。
