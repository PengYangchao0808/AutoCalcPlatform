# 结构查看器（Structure Viewer）设计文档

**状态**: v1.0（2026-09-11，计划 todos 1-44 全部交付；测试矩阵与化学正确性套件见 §10）
**范围**: Workbench「结构查看器」标签页的统一结构浏览、虚频可视化、轻量几何编辑，
以及后端 `structure_viewer_v1` 目录契约 / `normal_modes_v1` 振动产物 / 4 个 REST 端点

## 1. 概述

原「3D」与「构象集合」标签页合并为单一的 **结构查看器 / Structure Viewer** 标签页
（`frontend/ACP_Workbench_v2.html`，`data-tab="structure"`；旧 id 经 `setViewerTab()`
首行兼容映射）。选中任务即自动加载默认结构，无需手动打开 XYZ。

分阶段交付（均已上线）：

| 阶段 | 内容 |
|------|------|
| Phase A（Wave 3） | 状态店 + 目录拉取 + 自动加载 + manual_file 注入 + 能量图→查看器单向推送 |
| Phase B（Wave 5） | 虚频检查器：频率列表、位移箭头、rAF 动画、TS 证据提示、互斥与拆卸契约 |
| Phase C（Wave 6） | 轻量几何编辑：键长/键角/二面角、事务与撤销/重做、碰撞警告、另存为结构资产 |
| Phase D（Wave 7） | 统一几何加载器（共享样式/相机店）、IRC 帧投影与播放、叠合 RMSD、性能阈值、视图状态持久化、无障碍 |

**当前语义边界（有意为之，勿当缺陷）**：

- 能量图 → 结构查看器的选择推送是**单向**的；在结构列表中选择不会反向高亮能量图节点。
- 超长条目列表的虚拟化是固定 head 50 + tail 50 双窗口（**非滚动位置感知**）。
- 叠合（overlay）展示两个原始帧，不返回/施加叠合变换矩阵；RMSD 由后端 Kabsch 计算。
- 视图状态只存 `localStorage`（`acp.sv.view.*`），**绝不**写入 RESULT/ 或任务清单。

## 2. `structure_viewer_v1` 载荷契约

构建入口：`src/acp/results/structure_viewer.py::build_structure_viewer_payload(task_root,
*, job_id, workflow, job_status, item_id=None) -> StructureViewerPayload`（冻结 dataclass，
entries/warnings 为 tuple；对缺失/损坏清单**从不抛异常**，错误进 warnings）。

字段（`to_dict()` wire 形状，API 层另加 `availability`）：
`schema_version="structure_viewer_v1"`, `job_id`, `workflow`, `job_status`, `revision`,
`default_entry_id`, `groups[]`, `entries[]`, `warnings[]`。

`StructureViewerGroup`：`id` / `label` / `kind`。已知组：`final_conformers`（构象）、
`pes_confirmed` + `pes_recommendations`（严格分组，确认组在前）、`batch_items`、
`irc_forward`（正向）/ `irc_reverse`（反向）、scan 条目不分组的空 group_id。

`StructureViewerEntry` 关键字段：
- `geometry.endpoint`（形如 `/api/v1/jobs/{job}/structure-viewer/entries/{id}/geometry`）、
  `geometry.format="xyz"`
- `energy{value, unit, kind, temperature_k}`、`relative_energy_kcal`、`boltzmann_weight`
- `source.kind` 词表：`formal_result` | `algorithm_recommendation` | `manual_review` |
  `last_valid_cycle` | `calculation_input` | `manual_file`
- `source.confirmed`：仅 PES 条目携带（确认 `True` / 推荐 `False`；None 省略）。
  自动推荐**永不**变成正式结构产品——推荐条目 kind 固定 `algorithm_recommendation`
  且 `confirmed=False`，与人工确认组严格分组、同名冲突经 collision 后缀隔离。
- `source.frame_index`：多帧几何（IRC/scan/优化轨迹）的帧号；几何端点据此用
  `read_traj_frame_xyz` 提取精确帧块。
- `badges`：`selected`（rank-1）、`rank-N`、`TS`/`INT`、`未确认`、`兼容模式`、
  `failed-last-frame` 等。

**固定语义（矩阵测试锁定，todo 43）**：条目顺序 = 文件/清单顺序，**绝不**按能量重排；
Confsearch 默认 rank-1；PES 默认链 = 最高置信 TS 推荐 → 最高分峰推荐 → 首个确认条目；
Batch 完成项按产品清单顺序、失败轨迹项追加在后、默认首个完成项；
**全部 item 失败的 Batch 任务 `default_entry_id=None`**（前端回退渲染首条，P1）；
IRC 默认 `irc_forward_0`（正向第 0 帧作为 TS/路径中心代理，仅显示默认，非 TS 认定）；
`job_status`（completed/failed/cancelled/running）不改变条目集，只参与 revision。

## 3. Revision 算法

`_compute_revision`：按**固定顺序**对每个存在的源文件计算
`SHA-256(filename.encode() + file_bytes)`，再拼接 `job_status.encode()` 后取
hexdigest 前 16 位。源顺序：`RESULT/result_manifest.json` →
`RESULT/confsearch/confsearch_manifest.json` → `RESULT/pes_search/pes_profile.json`
→ `pes_recommendations.json` → `pes_review.json`。无任何源时为 `"empty"`。
文件字节变化或 job_status 变化都会改变 revision（前端 `refreshIfChanged` 据此增量刷新；
编辑中 `state.dirty` 时只提示「有新结构可用」不覆盖坐标）。

## 4. Entry-id 方案

| 来源 | id 方案 |
|------|---------|
| Confsearch 构象 | `conf_{conf_id}`（缺 id 回退 `conf_rank_{rank}`） |
| PES | `pes_{candidate_id}` |
| BatchOptimize | `batch_{item_id}` |
| simple 工作流 | `simple_{step_kind}` |
| scan 帧 | `scan_frame_{index}` |
| IRC 帧 | `irc_{forward|reverse}_{frame_index}` |
| 手动文件 | `manual_<sha256(relpath)[:12]>`（前端 `_sha256hex` 与 Python hashlib 逐位一致） |
| 遗留产品 | `legacy_<sha256(relpath)[:12]>` |
| 冲突 | `{entry_id}_{sha256(geometry_ref)[:6]}`（`resolve_collision`） |

## 5. 自动加载矩阵（按工作流）

| workflow | 解析器行为 |
|----------|-----------|
| Confsearch（含退役 ensemble/energy/xtbmd_censo_energy 经协议引擎） | `RESULT/confsearch/confsearch_manifest.json` 每构象一条；Boltzmann 权重缺失时**只补算缺失项**（已有值原样保留、不归一化，P2），warning 提示 |
| PESsearch | pes_review.json 确认条目（manual_review）+ pes_recommendations.json 推荐（algorithm_recommendation），确认组在前 |
| BatchOptimize | 完成项（result_manifest 产品，TAG→TS/INT 角色）+ 失败项（`WORK/03_OPT/batch/{item}/optimize/optimization_trajectory.json` 末有效周期，last_valid_cycle）；`?item_id=` 过滤（未知 item 抛 `StructureViewerError`→404） |
| optimize / xtb-optimize | 正式结构产品 → 失败轨迹末帧（last_valid_cycle，默认） → 计算 input.xyz |
| singlepoint / frequency | 计算 input.xyz（几何未改变徽标） |
| scan | `RESULT/trajectories/scan_trajectory.json` 每帧一条，文件顺序，默认最低能帧 |
| irc | `RESULT/irc/irc_{forward,reverse}.xyz`（单/多帧）逐帧条目，组 irc_forward(正向)/irc_reverse(反向)，默认 `irc_forward_0`；缺一个方向时 warning |
| 其余/退役 | `_resolve_legacy`：result_manifest 结构/xyz 产品 → `result_summary.json` 回退，`兼容模式` 徽标，只读展示（**200，不用 410**） |

## 6. API 面（`src/acp/api/v1_routes.py`）

| 端点 | 语义 | 错误码 |
|------|------|--------|
| `GET /api/v1/jobs/{id}/structure-viewer` | 目录载荷（+`availability: ready/pending_fetch`，`?item_id=` Batch 过滤） | 404 未知 job / 未知 item |
| `GET .../entries/{entry_id}/geometry` | `text/plain` XYZ 精确帧（frame_index 提取）；远程未同步首访 409，`?fetch=1` 同步拉取 | 404 未知 entry / 路径逃逸 / 文件缺失；409 `pending_fetch` |
| `GET .../entries/{entry_id}/vibrations` | `available` + modes 或 `reason`（200，非 409）；`threshold_cm1` + `threshold_source: default/job_config`；`source: product/historical_projection` | 404 未知 job/entry；**从不 500** |
| `GET .../structure-viewer/overlay?entry_a=&entry_b=` | 映射 + Kabsch RMSD + 最大位移原子；reason: `identity/mcs/unproven/geometry_unreadable/failed` | 404 未知 job/entry；映射失败 → `ok=false`（不 500） |

**410 刻意不使用**：结构查看器只读展示退役/历史任务（200 + legacy 条目）。

vibrations 失败 reason 词表：`no_normal_modes` / `geometry_mismatch` /
`pending_fetch` / `historical_unavailable`。

## 7. `normal_modes_v1` 振动产物

生成链：`acp.results.orca_parser.OrcaOutputParser`（ORCA 5.x，`cccp.orca_ts` 优先、
本地镜像回退）→ `acp.results.frequencies.build_normal_modes_product(calc, *,
geometry_product_id, atom_count)` → frequency 基元落盘
`<step>/normal_modes.json` → executor 复制到 `RESULT/frequencies/normal_modes.json`
（单项）或 `RESULT/frequencies/{item_id}__normal_modes.json`（Batch per-item）。

```json
{
  "schema_version": "normal_modes_v1",
  "units": {"frequency": "cm-1", "displacement": "dimensionless_orca_normal_mode"},
  "atom_count": 3,
  "geometry_product_id": "batch_opt_item_001",
  "modes": [
    {"mode_index": 6, "frequency_cm1": -797.72, "imaginary": true,
     "ir_intensity": 24.8, "vectors": [[0.01, -0.02, 0.03]]}
  ],
  "warnings": []
}
```

要点：
- **ORCA 原生 mode_index 保留，绝不重排**；`mode_frequencies`/`mode_vectors` 保留
  零模（平动/转动 6 个 0.0 行），`calc.frequencies` 列表过滤精确 0.0（两套语义并存，P3）；
  多个 `VIBRATIONAL FREQUENCIES`/`NORMAL MODES` section 一律**最后一个生效**。
- 损坏 mode（行数不符/NaN）跳过 + warnings，永不致命；`ir_intensity` 缺省省略。
- FREQUENCY_MODES 产品 metadata 携带几何绑定 `{geometry_product_id, geometry_ref,
  geometry_fingerprint(sha256[:16])}`；振动端点用它做几何匹配（前端
  `geometryMismatch` 门：双 pid 非空且不同 → 禁用箭头/动画；任一侧为 null 放行
  ——历史投影无 pid 属预期）。
- **historical_projection 回退**：产品缺失时端点只读解析 `WORK/04_FREQ/*.out|*.log`
  （Batch 另探 `WORK/{item}/frequency/` 与 `WORK/04_FREQ/batch/{item}/frequency/`），
  `source="historical_projection"`，**不写任何文件**（API 快照测试 + 解析器级快照测试双重锁定）。
- 阈值：`theory.frequency.imaginary_threshold_cm1`（默认 -50.0）；任务
  `spec.method["imaginary_threshold_cm1"]` 覆盖（threshold_source=job_config）。
- TS 门一致性：Batch `_count_significant_imaginary`（≤ 阈值，at-or-below）与前端
  `tsJudgment` 逐例一致（k=0/1/2 + 精确 -50.0 边界，跨语言测试）；IRC
  `classify_ts_identity` 共享 -50 幅值门但统计**全部**负频率（输入为预过滤虚频列表），
  `[-60,-40]` 时 Batch 有效而 IRC 拒绝——设计差异，已锁定（todo 44）。

## 8. 远程缓存

`src/acp/results/remote_structure_cache.py::RemoteStructureCache`：
`run_root/.remote_cache/<job_id>/<rel_path>`（拒绝 `..`）。目录不可用且为远程任务时
catalog 返回 `availability="pending_fetch"`；geometry 首访 409，`?fetch=1` 经
`RemoteResultFetcher` 同步拉取（tmp + `os.replace` 原子写、按 path 加锁、继承
run_root 权限）。`sweep_expired(ttl_days=7)` 清理；`manager._purge_job_records`
挂接 `purge_job` 随任务级联清除。

## 9. 前端模块

| 模块 | 命名空间 / 版本 | 职责 |
|------|----------------|------|
| `frontend/js/structure_viewer.js` | `window.ACPStructureViewer` 0.14.0 | 状态店（jobId/payload/revision/selectionToken/requestToken/dirty/displayed*）；目录拉取 + 409 重试；**共享几何加载器** `sharedLoadGeometry({canvasId, source})` + `geometryStore{currentXyz, stylePreset, cameras, loaderVersion, userPresetChosen, lastLoadDegraded}` + `registerCanvasLoader`/`saveCamera`/`restoreCamera`/`setStylePreset`；IRC 播放 `playIrcPath/stopIrcPlayback`（~4 fps，相机不跳变）；叠合 `loadOverlay/renderOverlay/clearOverlay`（第二模型 cyanCarbon + 最大位移原子高亮，unproven → 测量清除提示）；性能阈值 `LIST_VIRTUALIZE_THRESHOLD=100`（head50+tail50 双窗口 + 强制含选中/默认）、`TRAJECTORY_SAMPLE_THRESHOLD=500 → TARGET=200`（首尾保留、stride 采样，列表分组与 IRC 播放共用）、`LARGE_SYSTEM_ATOM_THRESHOLD=200`（>200 原子按次降级线框 + aria-live 通知，用户显式选样式则不降级）；视图状态 `localStorage["acp.sv.view.{job}:{entry}"]` `{version:1, camera, stylePreset, measurements, atomCount, savedAt}`（相机恢复带 atomCount 守卫；损坏/版本不符静默默认）；listbox 无障碍（role/aria-selected/aria-activedescendant + 方向键/Enter） |
| `frontend/js/vibration_viewer.js` | `window.ACPVibrationViewer` 0.6.0 | 频率检查器（负频置顶排序、IR 强度）、位移箭头（振幅 0.05-0.6、>50 原子跳氢）、rAF 动画（30fps 节流、getView/setView 相机保持、禁用内置 animate）、播放/编辑互斥（`ACPStructureEditor.setLocked`）、拆卸契约 `stopAnimationAndRestore/handleTeardown`（切 tab/切 job/销毁 viewer/切条目）、TS 证据 `tsJudgment`（仅证据展示，判定归 Batch/IRC）、`geometryMismatch` 几何绑定门 |
| `frontend/js/structure_editor.js` | `window.ACPStructureEditor` 0.8.0 | 邻接图（显式键或共价半径 1.3 推断）、键长 0.4-5.0 Å / 键角 1-179° / 二面角（(-180,180] 最短旋转）编辑、环/断开拒绝、事务引擎（undo/redo/reset 字节级还原、碰撞 <0.55·(ri+rj) 警告、entry 作用域）、dirty 同步到查看器刷新守卫、导出 XYZ + 另存为结构资产（POST `/structure-assets`，provenance 注释 + edit_operations 全量）+ 新建计算预填（**绝不**代提交） |
| `frontend/css/structure_viewer.css` | 0.8.0 | 三栏布局/抽屉/徽标/播放条/降级通知/焦点可见样式 |

合规：视图投影一律经 `TrajectoryFrame.to_node()`（反模式 #28）；查看器只读 +
绝不代提交任务（#29）；所有 viewer 装载路径复用 `scheduleViewerFraming`
（#30）；向导默认值经目录解析（#27）。

## 10. 测试地图

| 文件 | 锁定内容 |
|------|----------|
| `tests/test_acp_structure_viewer.py` | 载荷契约、revision、id 方案、损坏清单、8 类解析器、叠合 RMSD（Kabsch 精度）、**TestAcceptanceMatrix**（状态矩阵/全序/Boltzmann 混合/全失败 Batch/解析器级只读快照） |
| `tests/test_acp_api_structure_viewer.py` | 4 端点全覆盖：404/409/路径逃逸/远程 pending_fetch + fetch=1/历史投影 no-write 快照/IRC 逐帧几何/叠合端点 |
| `tests/test_acp_frequency_modes.py` | ORCA 解析矩阵（分块/多 section 末节生效/零模分流/索引对齐）、`normal_modes_v1` 产品、executor/Batch 落盘、**化学正确性**（产物不可互换、跨语言 TS 门一致性、IRC 门差异锁定） |
| `tests/test_acp_irc_projection.py` | `VIEW_REGISTRY["irc"]`、块解析、双方向路径序（非单调能量证明不重排）、无能量降级、单方向 warning |
| `tests/test_acp_sampling_graph.py` | sampling 投影 + VIEW_REGISTRY 回归（irc 注册后保持完整） |
| `tests/test_frontend_sync.py` | 前端全合同：命名空间/i18n 双语完整（含 STR 机械扫描）/禁用标识符/viewer framing/store 懒加载与陈旧守卫/共享加载器/IRC 播放/叠合/性能阈值/视图状态/listbox 无障碍/**化学正确性**（参考分子编辑序列、±180 最短路径、断开拒绝、模式不混用） |
