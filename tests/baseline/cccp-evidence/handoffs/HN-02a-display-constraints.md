# HN-02a — A 显示需求与二面角决策（display constraints & dihedral decision）

| 字段 | 值 |
|---|---|
| HN 编号 | `HN-02a`（HN-02 的 A 侧姊妹件：HN-02 = T13 的 B frames-adapter 映射；本件 = T14 的 A 显示需求；两件面向同一 consumer 域但接收任务不同，故分件交付） |
| owner / provider / consumer | C 路科学负责人 / C / **A（A04 科学产品消费与显示）** + B（参照，非接收任务） |
| tracked 位置 | `tests/baseline/cccp-evidence/handoffs/HN-02a-display-constraints.md` + `samples/`（`.omo/` 被 gitignore，交接物必须 tracked） |
| 计划来源 | `.omo/plans/acp-parallel-plan-3-cccp.md` todo 14（C02-s4） |
| 依赖 | T11（结构/轨迹契约，commit `5147e7e`）+ **T12（曲线/振动契约，commit `d7b71f0`）——按评审 R2 P1-5 修订为完整交付硬依赖，已满足**（evidence `task-12-acp-parallel-plan-3-cccp.md` 全项 PASS） |
| provider 基线快照 | `6046542`（fresh 快照 2026-10-18；dirty worktree 全部归属 Plan-1 前端 / Plan-2 Services / joint 评审在途文件，无一条属本任务） |
| status | `delivered`（provider 侧；A04 消费验收前不得记 integrated） |
| 本件不做 | 不改任何行为；不统一二面角；不写前端/JS；不承诺未定显示能力；不触碰 `docs/**`（B请求 写权） |

---

## 0. 范围

A04 消费 C 科学产品（经 B frames/VIEW_REGISTRY adapter）所需的四类显示约束 + 二面角既有差异决策记录 + 黄金显示样例。数字与样式分离：本件只定义**科学数字的形状/单位/身份/缺失语义/预算**；颜色/线型/主题/交互归 ACV 样式声明（主方案 §4.3），不进科学 JSON（T12 Must NOT 第 3 条同款约束）。

## 1. Unit table（显示单位表，按产品族）

> 来源 = T11/T12 契约（`src/cccp/calculation/products/contracts.py` 单位枚举/字面值）+ T4 数值基线 §5（`c00-numeric-baseline.md`，全部标「迁移期禁止变更」）。**单位串是契约字面值，A 原样渲染；权威值的单位换算不在 JS 做**（见 §4）。

| 产品族（ProductKind） | 显示字段 | 单位（契约字面值） | 契约定义处 | 基线 §5 对应行 |
|---|---|---|---|---|
| structure_trajectory | 原子坐标 `coordinate` | `angstrom`（合法集 `angstrom`/`bohr`/`nanometer`，每产品显式声明，无隐式默认） | `CoordinateUnits` | 几何坐标（显示/扫描）行 |
| structure_trajectory | 拓扑来源 `topology_source` | — （字符串标识，如 `orca_xyz`；3Dmol 猜键只能作标明来源的视觉回退，主方案 §4.3） | `StructureTrajectoryPayload` | — |
| curve_scatter | x 值 `SeriesRef.x_unit` | 每.series 显式；扫描坐标 = `angstrom-or-degree` | `SeriesRef` | 几何坐标（显示/扫描）行；`src/acp/results/frames.py:177,184` |
| curve_scatter | y 值 `SeriesRef.y_unit` | `hartree` / `kcal_mol` / `kj_mol`（`EnergyUnits`）；相对能须随 `reference_frame.energy_zero`（如 IRC 的 `ts` 参考态） | `SeriesRef`/`ReferenceFrame` | 能量（内部/相对）行；能量参考态（IRC TS-centered）行 |
| vibration | 频率 `frequency` | `cm-1`（合法集含 `thz`）；`imaginary` = 契约字段（frequency < 0），非前端判据 | `FrequencyUnits`/`VibrationMode` | 频率行 |
| vibration | 位移 `displacements` | `dimensionless_orca_normal_mode`；归一化约定 `mass_weighted_normalized_not_orthogonal`（两串逐字来自冻结表 `unit_strings.json`） | `DISPLACEMENT_UNITS`/`DISPLACEMENT_NORMALIZATION` | 位移（模式）行 |
| derived | 参数单位 | 逐 `ProductParameter.unit` 自带 | `ProductParameter` | 标准态修正行等 |
| （时间轴，存在时） | `time_ps` | ps（现行 frames 契约；products/v1 未含，见 §6） | `src/acp/results/frames.py:56` | 时间（MD/采样）行 |

**禁止项**：A 不得为显示自算换算（如 hartree→kcal/mol 乘 `HARTREE_TO_KCAL`）；换算后的值必须是（未来的）换算派生产品，自带单位与身份。不同精度常量副本（`627.509` 截断项等）按 T4 基线保持不合并，A 不感知。

## 2. Selection mapping（选择映射与稳定 ID）

> A 可选择的对象只有三类 + 原子；选择只携带**稳定 ID + 原生索引**，不携带坐标/数值快照（防陈旧）。到能量查看器 `node_id` 的投影是 B adapter（T13/HN-02）的写权，这里只声明 A 侧锚点。

| A 可选择 | 契约稳定 ID（形状） | 原生索引 | 显示侧锚点 |
|---|---|---|---|
| 帧（结构/轨迹） | `frame_id` = `structure_trajectory:frame:<i>`（`<i>` = `frame_index` 原生序，**永不重编号**）；分块时随 `block_index` | `frame_index` | 结构查看器选帧；能量图 node 选择经 B `TrajectoryFrame.to_node()` 的 `id`（= frame_id）联动 |
| 曲线点 | `point_id` = `<series_id>:point:<i>`；`series_id` = `curve_scatter:series:<key>` | `point_index`（原 series 内序） | 能量图选点；点→帧联动经 `point.source.native_index` + `source.artifact` 回映射（不靠数组下标巧合） |
| 振动模式 | `mode_index`（ORCA 原生编号，**零模保留、不重编号、严格升序**） | 同左 | 虚频列表选择；位移动画按 `atom_order` 对齐行 |
| 原子 | `atom_uid` = `<Element>:<n>`（`atom_order` 全帧一致） | 序即 `atom_order` 位次 | 测量选择（键长/键角/二面角，见 §5） |

规则：`frame_number` 显示一律用 `frame_index` 原生值（窗口/分块后仍不变，见 §4）；`native_index` 永远指向**源**编号空间（`SourceRef` 契约），重投影不重编号。

## 3. Missing-frame semantics（缺帧语义）

> 原则：**不伪造**。任何缺失以 `MissingReason` 封闭词表显式表达；绝不用 `0`/`""`/编造 id 顶替（T11 契约构造期强制）。partial 是逐项状态，不是全局状态（`ProductState.AVAILABLE` + `complete=false`）。

| 产品状态（信封） | 载荷 | 显示行为 |
|---|---|---|
| `available` + `complete=true` | 全量 | 正常渲染 |
| `available` + `complete=false`（partial） | 可用项 + 逐项 reason | 渲染可用项；缺失项**原位占位**：缺帧 → 结构区显示占位（无原子、显示 reason 标签），帧序列不断档、后续帧不前移；缺点 → 图上留空隙（gap），不跨缝连线、不插值；缺模式 → 列表行显示 reason，无频率/位移 |
| `unavailable`（`source_missing`/`not_prepared`/`unsupported`/`parse_failure`/`partial_source`/`upstream_unavailable`） | `payload=null` | 显示明确「不可用 + reason」占位视图；不显示空图、不显示零值 |
| `failed`（errors ≥1） | null + errors | 显示失败态 + errors 文案（对接 A04 错误 code/params 协议）；不显示数据 |
| 结构损坏 | reader 抛 `ProductSchemaError` | 显示读取错误；**绝不部分渲染**（corrupt 全拒，T11 §6） |

逐项 reason → 显示语义对照（结构侧）：`missing_frame_id`/`missing_atom_uid`/`missing_element`/`missing_coordinates` → 该帧不可画结构，占位显示；曲线侧 `missing_series` → series 占位、`missing_point_values` → 点位空隙；振动侧 `missing_frequency` → 模式行无频率、`missing_displacements` → 无动画。

黄金演示：`samples/display_structure_trajectory_partial.json`（帧 1 = `missing_atom_uid`、`atoms:[]`、`complete:false`，邻帧完整、帧序不变——派生脚本断言原文见 §7）。

## 4. Data budget（数据预算：有界载荷规则）

> IS-8：不得把显示抽样当全量科学结果。有界规则按产品族声明；**凡截断/抽样必须自标注**。

| 产品族 | 永不截断 | 允许的有界方式 | 标注要求 |
|---|---|---|---|
| structure_trajectory | 单帧原子集（`atom_uid`/`element`/`coordinate` = 科学身份） | **帧层窗口**（offset/limit 分页；`block_index` 分块） | `frame_window`: `{offset, limit, total_frames, subsampled, frame_indices_in_window, frame_indices_omitted}`；帧索引保持原生，绝不重编号 |
| curve_scatter | 点的 x/y/`point_id`/`source`；**关键点与原始顺序**（C06 前置约束在此预告） | 全分辨率 series 即产品；显示降采样/分块/多分辨率必须是 C06 的**有界派生产品**（派生身份 = 输入摘要+算法+范围+分辨率+schema，HN-03） | 派生产品自带身份；A 端任何「视觉抽点」只属渲染层，选择/标注必须映射回原 `point_id`；**A 不在 JS 做权威 decimation** |
| vibration | 模式列表整体（≤3N 天然有界）；每模式位移全量（与 `atom_order` 行对齐） | 无需子采样 | — |

通用规则：
1. 缺失点不静默丢弃——gap 保留为带 `missing_reason` 的点（§3）。
2. 科学 JSON 不含显示样式；样式归 A/ACV（§0）。
3. JS 不重算：单位换算、统计聚合、抽样、收敛/等价判据一律消费产品字段；`imaginary`、`complete` 等判定字段由契约提供。
4. 有界载荷必须携带 `source_product` 溯源（kind/schema_version/generator/input_digest），平台身份（job/account）不进显示载荷。

## 5. Dihedral decision record（二面角决策记录 — 既有差异原样登记，不统一）

> 证据源（READ-ONLY）：`docs/reference/next-change/ACP_Frontend_FE10_Dihedral_Verification_20261016.md`（2026-10-16/17 两轮核验，本轮未修改任何运行代码）。**这是科学语义差异，不是可合并重复**——「当去重统一」被明确禁止（T14 Must NOT / Plan-1 前端清退清单）。

### 5.1 既有差异（as-is 登记）

| # | 差异点 | `s2scanDihedral`（向导预填） | `computeMeasurementValue("dihedral")`（测量标签） |
|---|---|---|---|
| D1 | 位置 | `frontend/js/workbench/features/scan-s2.js` 的 `s2scanDihedral`（FE10 记录 `:478`；当前 HEAD 实测 def `:102-118`——行号随 Plan-1 前端在途提交漂移，**以符号名为准**）；消费点 wizard（FE10 `:1905`；当前 `wizard.js:835`） | `frontend/js/geometry_store.js` 的 `computeMeasurementValue("dihedral")`（def ≈`:326`，dihedral 分支 FE10 `:350-359`，当前 HEAD 仍在原位附近） |
| D2 | 取向约定 | A→B 投影（自定） | B→A 投影（praxeolitic/IUPAC 公式） |
| D3 | 数值关系 | **恒定 180° 平移**：`S2 = GS − 180 (mod 360)`（5 构造样例实测差值均 −180°；anti/syn 平面样例证明是语义整体错位，不是镜像/符号翻转） | 基准侧 |
| D4 | 值域/归一化 | `atan2` 原始 (−180, 180]，无显式归一化 | `normalizeDihedral` 归一 (−180, 180]（−180 → +180） |
| D5 | 退化坐标 | 返回 `NaN`；**共线中轴静默返 0**（`atan2(0,0)=0`） | 返回 `null`（退化/缺坐标） |
| D6 | 用户可见影响 | 同一组 4 原子：测量标签显示 GS 值、scan/PESsearch 向导选点预填 S2 值，可相差 180°（用户按测量标签填 start/end 会扫到关于目标构象镜像的路径） | |

后端权威勘察（FE10 §6，2026-10-17 固化）：平台内唯一实测实现 `cccp/utils/geometry_tools.py:73-102`（`GeometryUtils.calculate_dihedral`）= IUPAC ≡ GeometryStore；PES `actual_coordinate` 与前端 scan 能量图 x 轴均消费 IUPAC 值；ORCA/xtb 引擎对 `D i j k l` 的方向约定**未在代码中声明**（需引擎实验黄金样例确证）。黄金样例已冻结：`frontend/js/module_tests/fe10_dihedral_golden.test.js`（BEHAVIOR-FROZEN：5 样例双侧 pin 容差 1e-6 + 恒等式断言 + 退化语义 pin，含共线中轴 S2=0）。

### 5.2 决策

1. **本切片：保持差异原样**。不统一、不改任何一侧数值、不动黄金样例（T14 Must NOT：不统一二面角差异当去重；不改变既有行为）。
2. **未来统一 = 独立数值行为切片**（非本计划内任务；执行侧 A 路，科学约定确认归 C02/C07 面）。前置条件照 FE10 §5 全套：①引擎实验黄金样例确证方向约定 → ②若对齐 IUPAC/GS 则只改 `scan-s2.js` 的 `s2scanDihedral` 一处（FE10 记录 `:126-140`，当前 HEAD 即 def `:102-118`；消费点透传无需动） → ③同步更新 `fe10_dihedral_golden.test.js` 期望值；**不得宣称等价重构**。候选方向（勘察倾向）：对齐 IUPAC/GS——此为该未来切片的决策，本件不预定论。
3. **对 A04 显示消费的约束（本件生效）**：显示载荷中的二面角/扫描坐标数值一律来自科学产品（IUPAC/GS 侧，即 `actual_coordinate` 路径）；A **不得在 JS 里做 ±180°「修正」去对齐两套现存实现**（那等于在前端执行未确证的科学约定变更）；向导预填差异维持现状直至上述独立切片执行。
4. 展示层遇到 D5 退化语义时：契约口径取「显式缺失」——显示 `null`/缺失占位（对齐 GS/科学产品语义），不显示 S2 的静默 0；此为显示约束，不改变任何前端现行为。

## 6. 交付分层：结构子集随 T11；曲线/振动引用 T12（硬依赖已满足）

| 显示面 | 契约来源 | 状态 |
|---|---|---|
| 结构/轨迹显示字段（本件黄金样例即证） | T11 `products/v1` structure_trajectory | **可消费**（schema/序列化/fixture/测试 PASS，commit `5147e7e`） |
| 曲线/散点显示字段（x_unit/y_unit/series_id/point_id/逐点 reason/source） | T12 `curve_scatter` | **可消费**（commit `d7b71f0`；P1-5 要求的完整交付硬依赖满足） |
| 振动显示字段（mode_index 原生编号/frequency cm-1/imaginary/displacement 约定/atom_order） | T12 `vibration` | **可消费**（同上；编号对照 `tests/fixtures/structure_viewer/orca_freq_modes_full.txt` 已断言） |
| 派生产品（相对能换算、玻尔兹曼权重百分比、聚合曲线、饱和度） | T12 `derived` 骨架 | 骨架可消费；**逐工作流实装待 C04/C06**（生成入口接线），本件不承诺具体派生显示能力 |
| NMR 谱图、采样三视图等既有查看器的扩展显示面 | — | **不在本件承诺**（HN-02 只覆盖四产品族 → frames/VIEW_REGISTRY 映射；新视图类型须先注册 `VIEW_REGISTRY::ViewSpec`，ANTI #21） |

B adapter 字段映射（科学产品 → `to_node()`/`to_annotation()`）是 **T13/HN-02** 的交付，本件不重复；A 实装闭包行（真实生成入口→C writer→B 登记→A 消费）随 T13 实装闭包表维护。

## 7. Golden display samples（黄金样例 + 派生证据）

样例（tracked）：`tests/baseline/cccp-evidence/handoffs/samples/`

| 文件 | 派生自 | 演示 |
|---|---|---|
| `display_structure_trajectory_normal.json` | `tests/fixtures/science_products/structure_trajectory_normal.json` | normal：单位/身份/atom_order/verbatim 坐标 |
| `display_structure_trajectory_partial.json` | `structure_trajectory_partial.json` | §3 缺帧语义（帧 1 `missing_atom_uid`、atoms=[]、complete=false、邻帧完整） |
| `display_structure_trajectory_windowed.json` | normal（offset=0, limit=1） | §4 数据预算（窗口自标注、原生索引、无重编号） |

派生脚本：`.omo/evidence/acp-parallel-plan-3-cccp/t14-derive-display-sample.py`（经 `cccp.calculation.products.serialize.product_from_dict` 读取 fixture——即同一条 C reader 路径——投影为显示载荷；数字逐字复制，不重算、不补零）。

命令与原始输出（`PYTHONPATH=src /usr/bin/python3.11 .omo/evidence/acp-parallel-plan-3-cccp/t14-derive-display-sample.py`，全文存档 `t14-sample-derivation-output.txt`）：

```
== normal sample == .../samples/display_structure_trajectory_normal.json
  state=available complete=True (expect available/True): PASS
  coordinate_units=angstrom (expect angstrom): PASS
  frame_ids unique=True stable-id pattern=True (native order 0..1, never renumbered): PASS
  per-frame atom_uid sequence == payload.atom_order ['C:0', 'H:0', 'H:1', 'O:0']: PASS
  all coordinates finite floats (copied verbatim): PASS
  verbatim copy: display frame0 atom0 coordinate [0.0, 0.0, 0.0] == fixture [0.0, 0.0, 0.0]: PASS
  identity provenance (kind=structure_trajectory, schema=1, digest=sha256:abababababab...) matches fixture, no platform ids: PASS
== partial sample == .../samples/display_structure_trajectory_partial.json
  state=available complete=False (expect available/False — partial is not a global state): PASS
  frame 1 carries missing_reason=missing_atom_uid with atoms=[] (source atom_uid was null): PASS
  neighbour frames [0, 2] keep full atoms (2 each), frame kept in sequence (not dropped/renumbered): PASS
  no fabricated atom_uid / zero-fill anywhere: PASS
== windowed sample == .../samples/display_structure_trajectory_windowed.json
  window label offset=0 limit=1 total=2 subsampled=True in-window=[0] omitted=[1] (no renumbering): PASS
RESULT: PASS
```

## 8. tri-state 与未决

| 项 | 状态 | 依据 |
|---|---|---|
| 样例派生 + 单位/身份/缺帧/预算断言 | PASS | §7 原始输出 |
| T12 契约完整性（曲线/振动字段引用面） | PASS（消费其既有证据） | `task-12-...md` tri-state 表 |
| 前端实际渲染/交互（A04 消费） | NOT_VERIFIED（跨路，A04 未实施） | 本件只交约束与样例 |
| 二面角统一 | NOT_EXECUTED（决策：另立数值切片，§5.2） | FE10 §5/§6 |
| handoff_registry `HN-02a` 行回填 | 未做（有意） | T13 并行更新同一 registry 文件（HN-02 行），避免同文件并发；回填随收口方第二提交（照 HN-01 模式） |

未决（不阻塞 A04 开工，开工面=§1–§4/§6 已可消费行）：
1. 二面角统一切片的排期与归属（§5.2-2）。
2. 派生显示产品（相对能/权重）逐工作流实装（C04/C06，HN-03 有界派生身份）。
3. `time_ps` 等现行 frames 契约字段进 products/v1 的补充分版本（T13 状态 B 联合验收时一并核对）。
