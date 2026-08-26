# ACP S2 键长扫描 MD 实施计划

## 1. 文档目的

本文档将 S2（PESsearch）的第一项能力固化为“用户手动选键 + 一维键长松弛扫描 + 单点能复算 + 曲线分析 + TS/INT 初猜推荐”。

本文档是后续开发、联调、测试和验收的共同依据。除非后续产生新的版本文档，否则实现应以本文档中的数据契约、状态语义和验收标准为准。

## 2. 本期范围

### 2.1 必须实现

1. 从上一个任务结果、已上传结构文件或用户粘贴的 XYZ 文本获得可计算结构。
2. 在 3D 分子预览中选择两个原子，形成一个待扫描键。
3. 允许用户配置成键、断键、单键、双键或自定义扫描建议。
4. 根据键长扫描参数生成后台计算输入文件。
5. 默认使用 ORCA relaxed scan，并使用 GFN2-xTB 作为默认几何优化引擎。
6. 扫描结束后提取每个扫描点的几何结构、实际键长、优化状态和能量。
7. 按协议执行单点能复算，生成扫描曲线。
8. 使用已有 path profile 和 path selector 算法生成 TS/INT 初猜推荐。
9. 将推荐结果交给用户确认，并把最终 TS/INT 候选保存到 RESULT。
10. 保存可恢复、可审计、供后续 Lowconfirm/Highconfirm 批量任务读取的候选 manifest。

### 2.2 本期不实现

- 角度扫描；
- 二面角扫描；
- 多坐标同步扫描；
- NEB、CI-NEB、QST2、QST3；
- 自动遍历所有可能反应键；
- 自动替用户确认 TS/INT；
- 直接将扫描最高点宣称为已确认过渡态；
- 将旧的全自动 S2 逻辑继续作为新任务的默认入口。

后续可将这些能力扩展为新的扫描类型或 s2_path_v3，但不能破坏本期键长扫描契约。

## 3. 用户目标流程

~~~mermaid
flowchart TD
    A["创建 S2 键长扫描任务"] --> B["选择结构来源"]
    B --> B1["上一个任务结果"]
    B --> B2["上传 XYZ"]
    B --> B3["粘贴 XYZ 文本"]
    B1 --> C["解析结构与预览"]
    B2 --> C
    B3 --> C
    C --> D["在 3D 中选择两个原子"]
    D --> E["确认键长扫描语义"]
    E --> F["配置扫描协议"]
    F --> G["生成并校验输入文件"]
    G --> H["提交 S2 计算任务"]
    H --> I["提取扫描帧与扫描能量"]
    I --> J["按协议执行单点能"]
    J --> K["生成能量曲线与质量报告"]
    K --> L["推荐 TS / INT 初猜"]
    L --> M["用户确认候选"]
    M --> N["保存 RESULT 候选结构与 TAG"]
~~~

界面应始终明确显示当前处于：结构来源、选键、扫描协议、计算中、结果分析或候选确认。

## 4. 结构来源与输入契约

### 4.1 来源类型

前端使用 source_type 区分三类来源：

| source_type | 含义 | 必填字段 |
|---|---|---|
| task_artifact | 读取已有任务的结果结构 | source_job_id 或 artifact_path |
| structure_asset | 使用已上传到 ACP 的结构文件 | asset_id |
| xyz_text | 用户直接粘贴 XYZ | xyz_text |

对于 task_artifact，允许选择最终优化结构、指定扫描帧或结果文件中的指定 frame_index。默认使用上一个任务的最终结构，但界面必须显示实际文件路径和帧编号。

### 4.2 结构标准化

后端收到结构后必须：

1. 解析原子数、元素和坐标；
2. 统一为内部 0-based 原子索引；
3. 保留原始输入副本；
4. 计算分子式；
5. 读取或继承 charge 和 multiplicity；
6. 验证坐标全部为有限数；
7. 验证原子数大于 1；
8. 验证待扫描原子索引有效；
9. 对重复坐标、极端键长和缺失元素符号给出明确错误；
10. 写出规范化 XYZ 文件。

XYZ 通常不携带可靠键级信息，因此“单键/双键”只能作为用户意图和参数建议，不能仅凭 XYZ 自动判定为化学事实。

### 4.3 工作目录

必须遵守当前 ACP 任务目录契约，不得新建 mechanism_study/<study_id>/ 路径。

建议目录：

~~~text
<job_work_dir>/
├── WORK/
│   ├── 02_SEARCH/
│   │   └── s2_bond_scan_001/
│   │       ├── input.xyz
│   │       ├── scan_protocol.json
│   │       ├── scan.inp
│   │       ├── scan.out
│   │       ├── scan_relaxscanact.dat
│   │       ├── scan_frames.xyz
│   │       ├── sp/
│   │       ├── profile.json
│   │       └── candidates.json
│   └── 08_ANALYSIS/
├── RESULT/
│   └── mechanism/
│       └── s2_path_manifest.json
└── input_source.json
~~~

所有中间文件必须落盘，确保失败后可诊断、可续算、可重新分析。

## 5. 3D 选键交互

### 5.1 选择规则

用户在 3D 预览中点击两个原子，前端生成：

~~~json
{
  "atom_i": 0,
  "atom_j": 1,
  "display_atom_i": 1,
  "display_atom_j": 2,
  "current_distance_angstrom": 1.43
}
~~~

- 后端字段统一使用 0-based；
- 界面显示使用 1-based；
- 两个原子高亮；
- 两原子之间显示辅助线；
- 显示当前距离、元素、原子序号和邻接关系；
- 提交前必须再次确认原子对。

### 5.2 扫描语义

界面可提供以下预设，但后端统一保存为 distance 驱动坐标：

| 预设 | 适用场景 | 建议默认值 |
|---|---|---|
| bond_forming | 成键方向扫描 | 从当前或较长距离扫描至目标短距离 |
| bond_breaking | 断键方向扫描 | 从当前距离扫描至更长距离 |
| single_bond | 已知单键拉伸 | 以当前距离为中心给出温和范围 |
| double_bond | 已知双键拉伸 | 使用更窄的短键范围 |
| custom | 用户自行指定 | 用户填写完整范围 |

预设只负责给出建议，用户可以修改起始距离、终止距离、点数、方向、每点最大优化迭代数，以及是否沿用前一点结构作为下一点初猜。

### 5.3 前端校验

提交前必须校验：

- atom_i 不等于 atom_j；
- 起始和终止距离大于 0；
- n_points 在 3 到 101 之间；
- 步长不小于 0.01 Å；
- 起止距离不能相同；
- 扫描软件、方法和基组组合有效；
- charge 和 multiplicity 为整数；
- 结构来源可追溯。

超过 101 点时必须二次确认，默认不允许提交。

## 6. 扫描协议

### 6.1 三个计算层必须分开

协议不能只保存一个 method 字段，而应拆成：

1. scan_driver：控制约束扫描和每一点的几何优化；
2. scan_optimizer：扫描过程中每一点的优化级别；
3. single_point：扫描结束后的能量复算级别。

建议数据结构：

~~~json
{
  "scan_type": "bond_length",
  "coordinate": {
    "kind": "distance",
    "atoms": [0, 1],
    "unit": "angstrom",
    "start": 3.00,
    "end": 1.50,
    "n_points": 16,
    "direction": "descending"
  },
  "scan_driver": {
    "software": "orca",
    "mode": "relaxed_scan",
    "reuse_previous_geometry": true,
    "full_scan": true
  },
  "scan_optimizer": {
    "method": "GFN2-xTB",
    "max_iterations": 250
  },
  "single_point": {
    "enabled": true,
    "software": "orca",
    "method": "B97-3c",
    "basis": null,
    "charge": 0,
    "multiplicity": 1
  }
}
~~~

### 6.2 默认协议

默认协议名称：orca_relaxed_scan_xtb_gfn2_sp_b973c_v1。

- 扫描引擎：ORCA；
- 扫描类型：一维 relaxed scan；
- 几何优化：GFN2-xTB；
- 扫描点之间沿用前一收敛点结构；
- 保留所有扫描帧；
- 单点能：ORCA B97-3c；
- 单点使用扫描帧几何，不改变坐标；
- 单点失败时保留扫描能，并将曲线标记为 sp_incomplete，不得静默替换。

ORCA 的 relaxed scan 支持键长、键角和二面角；本期只启用距离坐标。官方语法为 B atom_i atom_j = start, end, N，并可配合 FullScan true 输出扫描帧，详见 [ORCA Optimizations and Scans](https://orca-manual.mpi-muelheim.mpg.de/contents/structurereactivity/optimizations_scans.html)。

### 6.3 ORCA 输入模板

生成的输入文件应接近：

~~~text
! GFN2-xTB Opt

%pal
  nprocs 8
end

%geom
  Scan
    B 0 1 = 3.00, 1.50, 16
  end
  FullScan true
  MaxIter 250
end

* xyzfile 0 1 input.xyz
~~~

实际生成器必须从结构和协议对象写入电荷、多重度、并行数、内存、最大迭代数和路径，不得把用户原始文本直接拼入命令行或输入文件。

### 6.4 参数建议

| 场景 | 建议范围 | 建议点数 | 说明 |
|---|---:|---:|---|
| 成键探索 | 当前距离至 1.4–1.7 Å | 15–31 | 常见 C–C、C–N、C–O 成键初筛 |
| 断键探索 | 当前距离至 2.5–3.5 Å | 15–31 | 观察势垒和解离趋势 |
| 已有单键 | 当前距离 ±0.4–0.8 Å | 15–25 | 不宜默认跨越过宽 |
| 已有双键 | 当前距离 ±0.2–0.5 Å | 15–25 | 重点观察键级变化附近 |
| 自定义 | 用户输入 | 3–101 | 必须展示步长和总跨度 |

这些数值是 UI 建议，不是化学结论。用户修改后，协议中保存最终值和 suggestion_source，以便复现。

### 6.5 可选方法

本期至少支持：

- GFN2-xTB：默认扫描优化；
- B97-3c：推荐单点或较低成本 DFT 复算；
- B3LYP/def2-SVP：可选传统 DFT 组合；
- 方法目录中已登记且通过能力检查的其他组合。

方法选择器必须分别显示扫描优化级别和单点能级别。混合泛函、RIJCOSX 和辅助基组等 ORCA 细节应由现有方法目录及 ORCA 接口统一生成，而不是要求用户手写 keyword。参考 [ORCA RI and RIJCOSX](https://orca-manual.mpi-muelheim.mpg.de/contents/essentialelements/RI.html)。

## 7. 后端数据模型

### 7.1 请求模型

建议新增 BondLengthScanRequest：

~~~json
{
  "workflow": "PESsearch",
  "mode": "bond_length_scan",
  "study_id": "study_xxx",
  "source": {
    "source_type": "task_artifact",
    "source_job_id": "20260823_001_Confsearch",
    "artifact_path": "RESULT/confsearch/confsearch_manifest.json",
    "structure_selector": {
      "kind": "final_structure",
      "frame_index": null
    }
  },
  "structure": {
    "charge": 0,
    "multiplicity": 1
  },
  "coordinate": {
    "kind": "distance",
    "atoms": [0, 1],
    "start": 3.0,
    "end": 1.5,
    "n_points": 16,
    "unit": "angstrom"
  },
  "protocol": {
    "scan_software": "orca",
    "scan_method": "GFN2-xTB",
    "single_point_enabled": true,
    "single_point_software": "orca",
    "single_point_method": "B97-3c"
  }
}
~~~

### 7.2 扫描帧

每个扫描点至少保存：

~~~json
{
  "index": 0,
  "target_coordinate": 3.0,
  "actual_coordinate": 2.9987,
  "coordinate_unit": "angstrom",
  "geometry_path": "scan_frames/frame_000.xyz",
  "scan_energy_hartree": -123.456,
  "single_point_energy_hartree": -123.123,
  "optimization_converged": true,
  "single_point_status": "completed",
  "source_log": "scan.out"
}
~~~

target_coordinate 表示协议要求，actual_coordinate 用于曲线和后续判断。所有分析优先使用实际键长。

### 7.3 S2 manifest

输出固定为 RESULT/mechanism/s2_path_manifest.json，根对象至少包含：

~~~json
{
  "schema": "s2_path_v2",
  "stage": "S2",
  "mode": "bond_length_scan",
  "status": "ready_for_review",
  "stationary_point_claimed": false,
  "input": {},
  "protocol": {},
  "scan": {
    "frames": [],
    "quality": {}
  },
  "energy_profile": {},
  "recommendations": {
    "ts": [],
    "intermediates": []
  },
  "review": {
    "required": true,
    "status": "pending",
    "selected_ts": [],
    "selected_intermediates": []
  },
  "provenance": {}
}
~~~

stationary_point_claimed 必须为 false。S2 只生成初猜和推荐，不能把扫描点直接当成已确认 TS/INT。

## 8. StageTask 与状态

### 8.1 新 S2 阶段顺序

将现有 PESsearch 的单一 path_search 拆成可观察阶段：

~~~text
prepare
→ materialize_input
→ validate_protocol
→ compile_scan_input
→ run_relaxed_scan
→ extract_scan_frames
→ run_single_points
→ build_energy_profile
→ recommend_candidates
→ finalize_manifest
~~~

每一阶段必须写入 stage_tasks，并记录开始/结束时间、输入输出文件、软件和版本、命令行摘要、退出码、stderr 尾部和可恢复检查点。

### 8.2 状态语义

S2 计算完成后：

- scheduler Job：COMPLETED；
- mechanism project：s2_ready；
- 候选审查：pending；
- 用户确认后：保存 s2_review.json、候选结构和候选 manifest，项目进入 s2_confirmed；
- 后续 Lowconfirm/Highconfirm 通过统一批量任务入口读取这些 RESULT 候选，不由 PESsearch 自动创建任务。

不能把“等待用户选择候选”伪装成 scheduler 的 WAITING_REVIEW。当前 ACP 的设计是 Job 完成后项目进入下一阶段 ready 状态，由用户选择候选并显式提交下一阶段 Job，本计划沿用该语义。

### 8.3 失败与恢复

必须区分输入解析失败、输入生成失败、扫描失败、部分不收敛、单点部分失败、能量解析失败和候选分析失败。

恢复策略：

- 可从已有扫描输出重新提取；
- 单点可从最后一个完成点继续；
- 曲线分析可独立重新运行；
- 结果不完整时 manifest 为 partial，不得误报 ready_for_review。

## 9. 曲线与 TS/INT 推荐

### 9.1 能量优先级

1. 单点能完整时使用 single_point_energy_hartree；
2. 未启用单点时使用 scan_energy_hartree；
3. 单点部分失败时保留扫描能并标记该点；
4. 曲线默认显示相对能量，单位为 kcal/mol；
5. 原始 Hartree 数据一并保存。

不得静默混合不同理论级别的能量。

### 9.2 复用已有算法

优先复用：

- src/acp/mechanism/primitives/path_profile.py：扫描帧证据、能量导数、膝点、峰值和平台；
- src/acp/mechanism/primitives/path_selector.py：select_path_seeds、TS 右移、INT 平台选择。

如旧算法依赖 RPH 字段，应新增适配层，不要复制一套平行算法。

### 9.3 TS 初猜

TS 初猜综合局部峰、能量一阶导数变号、二阶变化、帧收敛状态、反应区位置、峰值两侧邻居、端点距离和边界状态。

每个推荐至少包含：

~~~json
{
  "candidate_id": "ts_guess_001",
  "kind": "ts",
  "frame_index": 9,
  "geometry_path": "scan_frames/frame_009.xyz",
  "score": 0.82,
  "confidence": "medium",
  "evidence": {
    "is_local_peak": true,
    "has_left_neighbor": true,
    "has_right_neighbor": true,
    "is_boundary": false,
    "profile_status": "usable"
  },
  "reason": "局部峰值且两侧存在收敛扫描帧，建议进入 S3 TS 优化"
}
~~~

候选必须称为 TS 初猜或 TS candidate，不能称为已确认 TS。

### 9.4 INT 初猜

INT 推荐优先来自能量低谷、平台区中部、端点附近稳定结构，以及已有 RPH 端点与扫描帧的匹配。每个 INT 推荐包含帧号、几何文件、相对能量、选择原因和置信度。

### 9.5 低置信度

最高点位于边界、只有单侧邻居、邻点不收敛、曲线单调、单点缺失过多、实际坐标偏离目标或范围不足时，不得强行给出高置信度候选。应输出 NEEDS_REVIEW，并建议扩大范围、增加点数或更换初始结构。

ORCA 的 ScanTS 能够在扫描完成后使用最高能量点及其邻近结构生成 TS 优化起点，可作为后续可选增强，但本期仍需保留用户确认和 S3 验证环节。参考 [ORCA Transition State Optimizations and ScanTS](https://orca-manual.mpi-muelheim.mpg.de/contents/structurereactivity/optimizations_TS.html)。

## 10. 前端改造

### 10.1 新建任务中的条件配置

PESsearch 是新建任务界面中的一种任务类型，不创建独立窗口或独立工作区。用户选择 PESsearch 后，当前任务提交界面自动显示结构选择区和只读任务类型标识；提交完成后关闭任务窗口并返回任务列表。

新任务只复用原有结构输入和结构解析结果。MOL.js/3D 预览同时承担原子/键选择，不再出现第二套 XYZ 输入、结构来源、上游 Job、产物路径、TS 初猜或机制项目必填项。旧字段仅用于历史任务读取和兼容。

### 10.2 结构选择与计算协议

原有结构预览增加“选择键”和“选择原子”模式。选择键时依次点击键的两个端点；选择原子时依次点击坐标原子。选择结果以 XYZ 的 0-based 原子索引保存，界面显示使用 1-based 编号，并实时同步到左下角只读任务标识。外层不再提供第二套 PES 参数表单。

协议默认使用键长扫描，同时支持键角和二面角扫描。坐标类型、键型（单键/双键/多键/芳香键）、起止值和扫描点数全部放入新建任务右侧的“计算协议”卡片；扫描驱动、每点优化、单点能、泛函/基组、收敛和断点续算等详细设置也统一在该卡片配置，外层不重复出现。结构改变或重新解析时必须清空旧原子选择，并根据协议中的坐标类型切换为 2/3/4 原子选择。

PESsearch 使用 canonical `pes_scan` 方法 schema，与其他工作流共用同一套协议弹窗和配置状态；`pes_bond_scan` 仅作为历史记录的兼容别名。提交时由 `wizardState.method` 统一序列化为 `input.coordinate`、`input.protocol.coordinate`、`input.protocol.scan_driver`、`input.protocol.scan_optimizer` 和 `input.protocol.single_point`；外层结构选择区不得直接写入扫描范围或 QC 参数。

### 10.3 提交与结果

提交沿用 `POST /api/v1/jobs`，结构来源使用当前任务已经解析的 XYZ，扫描坐标使用 MOL.js 传出的原子索引。PESsearch 不要求 `mechanism_project_id`；如用户选择普通项目，仅作为任务归档信息保存。

提交成功后回到原始任务列表。曲线、扫描帧、3D 结果和候选审查不放入提交窗口，统一在任务详情中继续完善；结果可视化交互另行设计。

## 11. API 与文件接口

建议新增或扩展：

| API | 作用 |
|---|---|
| POST /api/v1/jobs | 提交键长扫描 S2 Job |
| POST /api/v1/structure-assets | 上传 XYZ 并生成结构资源 |
| GET /api/v1/jobs/{id}/s2/profile | 获取曲线和扫描点 |
| GET /api/v1/jobs/{id}/s2/candidates | 获取 TS/INT 推荐 |
| GET /api/v1/jobs/{id}/s2/frame/{index} | 获取扫描帧 |
| POST /api/v1/mechanism-projects/{id}/s2/review | 保存项目级用户候选确认 |
| POST /api/v1/jobs/{id}/s2/review | 保存任务级候选并写入 RESULT |
| POST /api/v1/jobs | 以 `batch_structures` 提交 Lowconfirm/Highconfirm 批量任务 |

PESsearch 只负责推荐、人工编辑和候选结果持久化，不提供“创建 S3”或“保存并创建 S3”接口。后续批量任务可以选择 S2 的全部 active 候选，也可以混合任意 XYZ、任务结果或用户上传结构；未带 TAG 的结构按 INT 处理。

接口必须区分计算任务状态、分析状态、用户审查状态和是否允许进入下一阶段，不能让前端只根据一个 status 字段猜测业务含义。

## 12. 代码改造清单

### 12.1 建议新增

- src/acp/mechanism/scan_models.py：请求、协议、扫描帧和候选模型；
- src/acp/mechanism/bond_scan.py：键长扫描编排和校验；
- src/acp/mechanism/scan_manifest.py：manifest 读写、版本和迁移。

### 12.2 需要审计和复用

- src/cccp/qc/interfaces/orca.py：relaxed scan、single point、输出解析；
- src/acp/mechanism/primitives/path_profile.py；
- src/acp/mechanism/primitives/path_selector.py；
- src/acp/mechanism/stages/handoff.py；
- src/acp/scheduler/stage_tasks.py；
- src/acp/scheduler/runner.py；
- src/acp/api/v1_routes.py；
- src/acp/api/v1_schemas.py；
- frontend/ACP_Workbench_v2.html。

### 12.3 兼容策略

- 新任务写入 s2_path_v2；
- 旧 s2_path_v1 仅保留读取和历史展示；
- 后续批量读取同时兼容 v1 和 v2 历史 manifest；
- 不修改历史 manifest；
- 新任务通过 mode=bond_length_scan 路由到新实现；
- 新任务不得默认进入旧全自动路线。

## 13. 测试计划

### 13.1 单元测试

必须覆盖 XYZ 解析、task artifact 选择、索引转换、无效距离拒绝、方向和点数计算、ORCA 输入生成、电荷和多重度传递、扫描帧解析、实际键长计算、单位换算、单点部分失败、边界峰、无峰曲线、candidate schema、manifest 往返读写和 v1/v2 S3 handoff。

### 13.2 API 测试

覆盖三种结构来源、提交前校验、任务状态投影、profile 查询、frame 查询、candidate 查询、review 保存、候选 RESULT 持久化、Lowconfirm/Highconfirm 批量输入生成。

### 13.3 集成测试

至少准备：

1. 简单单键拉伸；
2. 成键扫描；
3. 断键扫描；
4. 扫描中途不收敛；
5. 单点部分失败；
6. 最高点位于边界；
7. 无明显峰；
8. 从上一个任务读取结构；
9. 上传 XYZ；
10. 粘贴 XYZ。

真实 ORCA/xTB 测试应标记为 slow；没有软件环境时必须明确 skip，而不是误报通过。

### 13.4 前端验收测试

确认两个原子能准确选中，选中状态和键长显示正确，预设修改同步，非法协议不能提交，任务完成后曲线与 3D 帧联动，候选可选中和取消，候选保存后可从 RESULT 重新载入，刷新后状态可恢复，历史任务仍可打开。

## 14. 分阶段实施里程碑

### M1：协议与输入契约

完成数据模型、三种结构来源、协议校验、manifest schema 和单元测试。

出口条件：不依赖真实量化计算即可完成请求构造和输入校验。

### M2：扫描计算链

接入 ORCA relaxed scan，生成输入、解析扫描帧、保存工作目录和 stage task，支持失败诊断和重新提取。

出口条件：一个真实样例得到完整扫描帧文件和可读取 manifest。

### M3：单点与曲线分析

接入单点任务，生成 profile，支持部分单点失败，接入已有 profile 算法并输出曲线。

出口条件：曲线反映实际扫描坐标，能区分扫描能和单点能。

### M4：候选推荐与前端审查

接入 path selector，输出 TS/INT 初猜，完成 3D 选键、曲线联动和用户确认接口。

出口条件：用户可以选择候选并保存审查结果。

### M5：候选 RESULT 交接与批量回归

让 Lowconfirm/Highconfirm 从 S2 的候选 manifest 读取全部 active 候选，完成 v1 兼容、scheduler/remote/前端回归及验收记录。

出口条件：从 PESsearch 候选编辑、RESULT 持久化到后续批量计算形成闭环。

## 15. 风险与处理

| 风险 | 处理方式 |
|---|---|
| XYZ 没有可靠键级 | 单键/双键只作为用户意图和参数建议 |
| 扫描点不收敛 | 保留部分结果并标记质量，不静默补点 |
| 最高点不是 TS | 统一称为 TS 初猜，交给 S3 验证 |
| 单点和扫描级别不同 | 分开保存并在曲线中明确标识 |
| 选键索引错位 | 前端 1-based、后端 0-based，并在摘要中同时显示 |
| 扫描范围不足 | 输出边界峰和扩大范围建议 |
| ORCA 语法变化 | 由 ORCA 接口和方法目录生成 keyword |
| 任务耗时较长 | 使用 stage task、检查点和可恢复输出 |
| 旧逻辑污染新流程 | 新任务强制 mode=bond_length_scan，旧逻辑只读兼容 |
| 用户误以为候选已确认 | UI 和 manifest 固定写入 stationary_point_claimed=false |

## 16. 完成定义

满足以下条件，才能将本项标记为完成：

1. 用户可以从三类来源加载结构；
2. 用户可以在 3D 中选择两个原子；
3. 用户可以配置并看到完整键长扫描协议；
4. 后端能够生成可复现的 ORCA 输入；
5. S2 记录每个阶段和中间文件；
6. 能够提取扫描帧和实际键长；
7. 能够执行并记录单点能；
8. 能够生成曲线和质量标记；
9. 能够给出带证据的 TS/INT 初猜；
10. 用户可以确认或拒绝候选；
11. 候选保存会写入 RESULT 下的候选结构、TAG 和 manifest；
12. Lowconfirm/Highconfirm 能读取并使用确认后的候选 RESULT；
13. 旧任务仍可只读打开；
14. 单元、API、集成和前端关键路径测试通过；
15. 真实软件不可用时测试明确为 skip。

## 17. 参考资料

- [ORCA Optimizations and Scans](https://orca-manual.mpi-muelheim.mpg.de/contents/structurereactivity/optimizations_scans.html)
- [ORCA Transition State Optimizations and ScanTS](https://orca-manual.mpi-muelheim.mpg.de/contents/structurereactivity/optimizations_TS.html)
- [ORCA NEB](https://orca-manual.mpi-muelheim.mpg.de/contents/structurereactivity/neb.html)
- [ORCA Composite Methods](https://orca-manual.mpi-muelheim.mpg.de/contents/modelchemistries/3cmethods.html)
- [ORCA RI and RIJCOSX](https://orca-manual.mpi-muelheim.mpg.de/contents/essentialelements/RI.html)
