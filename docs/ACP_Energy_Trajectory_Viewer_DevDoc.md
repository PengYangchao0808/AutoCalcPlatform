# 能量与轨迹（Energy & Trajectory）查看器设计文档

**状态**: v1.0（2026-09-07）
**范围**: PES 扫描、几何优化、构象搜索、独立扫描的统一能量与轨迹帧查看 + 帧保存候选 + 采样历史三视图

## 1. 目标与原则

Workbench 原有的"能量图"标签页升级为"能量与轨迹 / Energy & Trajectory"统一查看器。
所有能量图视图（PES 扫描、几何优化轨迹、构象能量分布、独立扫描能量剖面、反应路径）共享同一个
`TrajectoryFrame` 契约；每个曲线节点对应一个可操作的分子帧，支持锁定、导出、保存为候选。

**核心原则：**

1. **契约先行**：`TrajectoryFrame` / `TrajectoryAnnotation` 是唯一的前端数据形状；
   energy_graph.py 的所有 builder 通过 `to_node()` / `to_annotation()` 产出，不再直接拼 dict。
2. **不创建任务**：帧操作仅限"保存为候选"（物化 XYZ + 注册 manifest），
   不提供任务提交/预填入口。`energyGraphConfirmAndBatch` 和 `data-energy-action="to-batch"`
   保持前端禁止标识符（tests/test_frontend_sync.py 锁定）。
3. **渐进扩展**：IRC / NEB 视图在 `VIEW_REGISTRY` 注册占位，但不产出任何数据投影。
4. **无新依赖**：后端仅用 numpy（MDS）+ 已有 plain_rmsd；前端不引入新图表库。

## 2. TrajectoryFrame / TrajectoryAnnotation 契约

### 2.1 TrajectoryFrame（`src/acp/results/frames.py`）

```python
@dataclass(frozen=True, slots=True)
class TrajectoryFrame:
    frame_id: str
    label: str
    frame_index: int
    x: float | None
    energy: float | None
    status: str = "unknown"
    geometry_ref: str = ""
    step: int | None = None
    time_ps: float | None = None
    coordinate: float | None = None
    rms_gradient: float | None = None
    max_gradient: float | None = None
    basin_id: int | None = None
    annotations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
```

`to_node(node_type)` 发射稳定的节点 wire 形状：

| Wire key | 来源 |
|----------|------|
| `id` | frame_id |
| `label` | label |
| `type` | node_type（由 ViewSpec.node_type 提供） |
| `frame_index` | frame_index |
| `x` | x（有限数或 None） |
| `energy` | energy（有限数或 None） |
| `status` | status |
| `geometry_ref` | geometry_ref |
| `metadata` | dict 合并 step / time_ps / coordinate / rms_gradient / max_gradient / basin_id / annotations（仅非 None / 非空时写入）+ 用户 metadata |

### 2.2 TrajectoryAnnotation

```python
@dataclass(frozen=True, slots=True)
class TrajectoryAnnotation:
    id: str
    type: str
    label: str
    frame_index: int
    x: float | None
    y: float | None
    status: str = ""
    geometry_ref: str = ""
    selected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

`to_annotation()` 发射稳定 wire 形状。已知 metadata 键（`active` / `saved` / `candidate_id` /
`recommended_type` / `selection_source` / `confidence` / `reason`）提升为顶层字段；其余留在 `metadata` 嵌套。

### 2.3 ANNOTATION_TYPES

```python
ANNOTATION_TYPES = frozenset({
    "ts", "intermediate", "minimum", "maximum", "failed",
    "new_basin", "cluster_representative", "locked", "user_selected",
})
```

### 2.4 VIEW_REGISTRY（9 个视图）

| view_type | title_zh | title_en | x_label_zh | x_unit | node_type | 状态 |
|-----------|----------|----------|------------|--------|-----------|------|
| `scan` | PES 扫描能量 | PES Scan Energy | 扫描坐标 | angstrom-or-degree | frame | 已实现 |
| `scan_trajectory` | 扫描能量剖面 | Scan Energy Profile | 扫描坐标 | angstrom-or-degree | frame | 已实现 |
| `optimization` | 几何优化轨迹 | Optimization Trajectory | 优化周期 | cycle | optimization_cycle | 已实现 |
| `conformer` | 构象能量分布 | Conformer Energy Distribution | 构象排名 | rank | conformer | 已实现 |
| `sampling` | 构象搜索轨迹 | Conformer Search Trajectory | 模拟时间 | ps | frame | 已实现 |
| `reaction_path` | 反应路径能量图 | Reaction Path Energy | 反应进程 | progress | reaction_point | 已实现 |
| `irc` | IRC 能量剖面 | IRC Energy Profile | 反应坐标 | * | * | 占位（未实现） |
| `neb` | NEB 最小能量路径 | NEB Minimum Energy Path | 路径坐标 | * | * | 占位（未实现） |
| `unsupported` | 能量图不可用 | Energy Graph Unavailable | * | * | * | 兜底 |

`irc` 和 `neb` 在注册表中有条目，但没有任何 builder 产出对应投影。前端收到 `unsupported` 视图时显示不可用提示。

## 3. 数据 Schema

### 3.1 sampling_history_v1（`RESULT/confsearch/sampling_history.json`）

由 `SamplingHistory.to_dict()` 产出，`SamplingHistory.from_dict()` 读取。
坐标不持久化，几何留在 `traj.xyz`；JSON 仅携带逐帧元数据。

**顶层字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | `str` | 固定 `"sampling_history_v1"` |
| `protocol` | `str` | 协议名（`"xtb-md"` / `"xtbmd-censo"`） |
| `source_trajectory` | `str` | 来源 traj.xyz 路径 |
| `n_frames_raw` | `int` | 原始帧数（含平衡前缀） |
| `n_frames_used` | `int` | 去除平衡后帧数 |
| `equilibration_cut` | `int` | 被丢弃的前导帧数 |
| `frames[]` | `list[dict]` | 逐帧元数据（见下表） |
| `basins[]` | `list[dict]` | 盆地元数据（见下表） |
| `saturation` | `dict` | 采样饱和度指标（见下表） |
| `computed_at` | `str` | ISO 8601 UTC 时间戳 |
| `subsampled` | `bool` | 是否因帧数超过阈值而子采样 |
| `subsample_stride` | `int` | 子采样步幅（1 表示未子采样） |

**frames[] 逐帧字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | `int` | 原始轨迹中的帧索引（0-based） |
| `time_ps` | `float \| None` | 模拟时间（皮秒） |
| `step` | `int` | 序号步数 |
| `energy_kcal_mol` | `float \| None` | 势能（kcal/mol） |
| `relative_energy_kcal_mol` | `float \| None` | 相对能量（锚定最小值 = 0） |
| `basin_id` | `int` | 分配的盆地 ID |
| `is_new_basin` | `bool` | 该帧是否首次出现新盆地 |
| `mds` | `[float \| None, float \| None]` | MDS 二维坐标 |

**basins[] 盆地字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `basin_id` | `int` | 盆地编号（从 0 起） |
| `first_seen_index` | `int` | 首次出现的帧位置（切割后数组下标） |
| `first_seen_ps` | `float \| None` | 首次出现的时间（ps） |
| `visit_count` | `int` | 被采样帧访问次数 |
| `min_energy` | `float \| None` | 盆地内最低能量（kcal/mol） |
| `representative_frame` | `int` | 代表帧的位置（切割后数组下标） |

> **注意**：`first_seen_index` 和 `representative_frame` 是**切割后** `frames[]` 数组的下标，
> 不是原始轨迹中的 `TrajFrame.index`。下游消费端不能混淆两者。

**saturation 饱和度字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `unique_clusters` | `int` | 唯一盆地总数 |
| `new_clusters_last_20pct` | `int` | 最后 20% 帧中新出现的盆地数 |
| `last_new_basin_ps` | `float \| None` | 最后一个新盆地出现的时间（ps） |
| `revisit_ratio` | `float` | 重访率（已访问盆地的重复帧 / 总帧数） |
| `energy_window_kcal_mol` | `float` | 能量极差（kcal/mol） |
| `level` | `str` | 饱和度等级：`"HIGH"` / `"MEDIUM"` / `"LOW"` |
| `cumulative_unique` | `list[dict]` | 累计唯一盆地曲线 `[{time_ps, unique}, ...]` |

### 3.2 frame_candidates_v1（`RESULT/frame_candidates.json`）

由 `frame_candidate_store.py` 管理，`frame_candidates.py` 的 `save_frame_candidate` / `remove_frame_candidate` 操作。

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | `str` | 固定 `"frame_candidates_v1"` |
| `job_id` | `str` | 所属任务 ID |
| `revision` | `int` | 单调递增版本号（每次保存/删除 +1） |
| `candidates[]` | `list[dict]` | 候选列表 |

**candidates[] 条目：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `candidate_id` | `str` | 确定性 ID，格式 `{prefix}_{role}_frame_{NNN}` |
| `view_type` | `str` | 来源视图（`scan` / `optimization` / `sampling` / `conformer`） |
| `frame_index` | `int` | 原始帧索引 |
| `role` | `str` | 角色（`TS` / `INT` / `NONE`） |
| `name` | `str` | 显示名（默认 = candidate_id） |
| `structure_path` | `str` | XYZ 文件相对路径（`structures/<candidate_id>.xyz`） |
| `saved_at` | `str` | ISO 8601 时间戳 |

**candidate_id 前缀映射：**

| view_type | prefix | 示例 |
|-----------|--------|------|
| optimization | `opt` | `opt_ts_frame_003` |
| sampling | `md` | `md_int_frame_012` |
| scan | `scan` | `scan_none_frame_027` |
| conformer | `conf` | `conf_ts_frame_001` |

保存为候选时，XYZ 文件第二行 TAG 注释格式：
```
TAG: <role> | candidate_id=<id> | source=<workflow> | frame=<NNN> | selection_source=manual_frame
```

## 4. REST API 端点

### 4.1 能量图（已有端点扩展）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/jobs/{id}/energy-graph` | GET | 新增 `?view=<view_type>` 查询参数 |
| | | `view=sampling` 返回采样历史投影（需 sampling_history.json 存在） |
| | | `view` 缺省或无效时回退到默认视图（200，非 500） |
| | | Confsearch 任务 `available_views` 包含 `"conformer"` + `"sampling"`（当采样文件存在时） |

### 4.2 帧候选 CRUD

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/jobs/{id}/frame-candidate` | POST | 保存帧为候选 |
| | | Body: `{view_type, frame_index, role, name?, expected_revision?}` |
| | | 400: 无效 role / view_type；404: 帧不存在；409: revision 冲突或任务未完成 |
| | | PESsearch 任务返回 400 + 指引 `/pes/review` |
| `/api/v1/jobs/{id}/frame-candidates` | GET | 列出当前所有帧候选 |
| `/api/v1/jobs/{id}/frame-candidate/{cid}` | DELETE | 删除候选（XYZ 文件保留） |
| | | `expected_revision?` 参数防并发覆盖 |

### 4.3 采样帧端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/jobs/{id}/sampling/frame/{n}` | GET | 返回采样轨迹第 n 帧 |
| | | 响应: `{job_id, frame_index, time_ps, step, energy_kcal_mol, relative_energy_kcal_mol, basin_id, xyz}` |
| | | 无采样历史或帧越界 → 404 |

## 5. 采样管线

采样管线在 `src/acp/confsearch/sampling.py` 中实现，由 Confsearch 引擎 finalize 钩子调用。
仅 `xtb-md` 和 `xtbmd-censo` 协议执行采样；CREST 协议跳过。

### 5.1 处理流程

```
traj.xyz 解析
  → 平衡切割（±2σ 滑窗统计检验）
    → 贪心 plain-RMSD 分盆（0.5 Å 阈值，≤1500 子采样）
      → basin 级经典 MDS（numpy eigh，二维投影）
        → 饱和度指标计算
          → SamplingHistory 序列化 → RESULT/confsearch/sampling_history.json
```

### 5.2 各步骤细节

**traj.xyz 解析**（`parse_traj_frames`）：
- 多帧 XYZ 读取，标题行匹配 xTB-MD 格式 `md: <t(ps)> <E_pot> (kcal/mol) ...`
- 支持 4 段（`C x y z`）和 5 段（`0 C x y z`）坐标行
- 格式异常的帧被跳过（不崩溃），返回 `energy_kcal_mol=None` + `time_ps=None`

**平衡切割**（`equilibration_cutoff`）：
- ±2σ 滑窗统计检验：将轨迹分为非重叠窗口，最后相邻窗口对均值差超过 `2 × 合并标准差` 处为平衡终点
- 截断比例钳制在 `[5%, 20%]`；数据不足时回退丢弃 10%

**贪心 plain-RMSD 分盆**（`assign_basins`）：
- 当帧数 > 1500 时，等间距子采样（stride = n // 1500）
- 第一帧为盆地 0 的种子；后续每帧与已有盆地代表帧比较 plain_RMSD
  - 若 RMSD < 0.5 Å，加入该盆地
  - 否则，创建新盆地
- **carry-forward**：未被子采样到的帧继承前一个被采样帧的盆地（保守近似）
- 盆地 `first_seen_index` / `representative_frame` 是切割后数组下标，不是原始轨迹 index

**basin 级经典 MDS**（`mds_2d`）：
- 构建盆地间 RMSD 距离矩阵（N×N，N = 盆地数，非帧数）
- 对平方距离矩阵做双重中心化，取 `np.linalg.eigh` 前二维特征向量 × √特征值
- 每帧的 MDS 坐标 = 其所属盆地的 MDS 坐标（帧共享盆坐标，前端加 jitter 展示）

### 5.3 饱和度等级规则

| 等级 | 条件 | 含义 |
|------|------|------|
| **HIGH** | `new_clusters_last_20pct == 0` | 最后 20% 帧无新盆地，采样已饱和 |
| **MEDIUM** | `new_clusters_last_20pct ≤ max(1, unique_clusters // 10)` | 少量新盆地，接近饱和 |
| **LOW** | 其他情况 | 持续发现新盆地，采样不充分 |

其他饱和度指标：
- **唯一盆地数**：basin_infos 的长度
- **重访率**：已访问盆地的重复帧数 / 总帧数
- **能量窗口**：所有帧势能的极差
- **累计唯一曲线**：逐帧的累计唯一次盆数，单调非递减

## 6. 前端视图与帧操作

### 6.1 三视图架构

Workbench 能量与轨迹标签页包含以下视图切换：

- **PES 扫描 / 优化 / 构象 / 反应路径**：通过 `view_type` 自动选择，单一图表
- **采样子视图**（仅 `view_type === "sampling"` 时出现）：
  - 能量轨迹：X 轴（模拟时间 ps / MD Step / 帧）× Y 轴（势能 / 相对能量），逐帧能量曲线 + new_basin 标注
  - 采样空间：盆地 MDS 二维散点图，颜色映射 basin_id，代表帧光晕
  - 覆盖度：饱和度指标卡片 + 累计唯一盆地曲线 + 盆地时间轴色带

- **优化收敛面板**（optimization 视图专用）：
  RMS/MAX 梯度 vs 阈值、RMS/MAX 位移 vs 阈值，达标/未达标徽标

### 6.2 通用帧操作

所有视图的每个帧节点共享以下操作：

| 操作 | 实现 | 说明 |
|------|------|------|
| 查看结构 | 3Dmol.js 加载 geometry_ref | 已有功能 |
| 锁定/解锁 | 客户端 localStorage | key: `acp-frame-lock:<job_id>:<revision>`；锁定帧排除角色编辑 |
| 导出结构 | GET 文件端点 → Blob 下载 | 文件名: `<job_id>_<node.id>.xyz` |
| 保存为候选 | POST frame-candidate API | 角色选择器：无 / TS / INT；成功后显示已保存徽标 |

**保存为候选的角色选择器**：使用 `prompt()`（v1 简化实现），
用户输入 TS / INT / NONE（空值默认 NONE）。
PESsearch 任务隐藏此操作（条件渲染）。

### 6.3 localStorage 约定

| Key 模式 | 用途 |
|----------|------|
| `acp-frame-lock:<job_id>:<revision>` | 帧锁定集合（JSON 数组 of frame_index） |

## 7. 已知限制

1. **CREST 协议无采样视图**：CREST（xtb-crest / censo-crest）不暴露其内部 MD 轨迹。
   这些任务的能量图仅显示构象能量分布视图，`available_views` 不包含 `"sampling"`。

2. **Basin 级 MDS 坐标共享**：MDS 在盆地代表帧上计算，同一盆地的所有帧共享相同二维坐标。
   前端在同一坐标点上有多个帧时添加随机 jitter 以避免完全重叠。

3. **BasinInfo 下标语义**：`first_seen_index` 和 `representative_frame` 是**平衡切割后**
   `frames[]` 数组的位置下标，不是原始轨迹中的 `TrajFrame.index`。
   前端从 `frames[basin.representative_frame]` 读取代表帧数据。

4. **历史任务无追溯**：在此功能上线前完成的任务不生成 `sampling_history.json`。
   缺失文件时 energy_graph 仅返回构象视图，不报错。

5. **坐标轴 kind 嗅探启发式**：前端根据数据特征自动选择 X 轴默认值（时间 / 步数 / 帧号），
   不是用户配置。xTB-MD 数据默认 `time_ps`；无时间数据时回退到 `step`。

6. **prompt() 角色选择器**：v1 使用浏览器原生 `prompt()` 获取角色名，体验较粗糙。
   后续版本应替换为模态对话框。

7. **IRC / NEB 视图未实现**：`VIEW_REGISTRY` 中注册了 `irc` 和 `neb`，但没有对应的
   builder 或数据投影。前端收到这些视图时走 `unsupported` 兜底。

8. **NO create-task 操作**：帧操作仅限"保存为候选"。任务创建统一走"新建任务"流程，
   通过"载入全部候选"发现已保存的帧候选结构。
