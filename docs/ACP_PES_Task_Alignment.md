# PES/路径类工作流 × 通用任务语义差异对照（todo 28）

**范围**：`src/acp/calculations/pes/scan.py` 的 `_run_relaxed_scan_backend`（柔性扫描执行）
与 `_run_single_points`（逐帧单点）对比 cccp 通用任务（`cccp.calculation.tasks.scan.run_scan` /
`cccp.calculation.tasks.singlepoint.run_singlepoint` + `cccp.calculation.batch.run_batch`）。
每条给出 **等价** 或 **取舍** 结论。本文先于接线写就；接线后行为以本文结论为准。

**接线后的调用链**：

* 扫描：`_run_relaxed_scan_backend` →（组装 `TaskRequest(task=scan)` + `TaskContext(capability_extras=
  {scan_plan 原始映射, 逐点遗留 kwargs, point_callback 收集器})`）→ `cccp.calculation.tasks.scan.run_scan`。
  `run_scan` 负责 `validate_request` / `build_scan_plan`（原始 plan 保真）/ `validate_atom_indices` /
  电子态校验 / 错误分类（`classify_failure`）/ 结果归一化（`ScanPayload` + `scan_profile.json` +
  `scan_frame_%03d.xyz` 科学产物）。PES 侧把 `TaskResult` 重建为 `RelaxedScanResult`（供
  `_extract_frames` 消费，其与路径分析/候选选择逻辑零改动）。
* 单点：`_run_single_points` → `BatchSinglePointExecutor` → `cccp.calculation.batch.run_batch`，
  执行函数 = `run_singlepoint`（todo 17 已接）——本 todo 仅做三路径一致性锁定测试。

---

## 一、`_run_relaxed_scan_backend` → `run_scan`（cccp relaxed-scan 任务）

### 1. 参数（有效参数面）

| 项 | 旧（backend 直构直调） | 新（run_scan） | 结论 |
|---|---|---|---|
| 能力调用形态 | `backend.relaxed_scan(coords, symbols, output_dir=scan_dir, plan=plan, charge, multiplicity, method, basis, solvent, solvent_model, route_extras, nprocs, use_scants, full_scan, geom_maxiter, opt_level, grid, scf_convergence, scf_maxiter, aux_j_basis, aux_c_basis, dispersion, retry_count, retry_strategy, failure_policy, reuse_previous_geometry, point_callback)` | 同名 kwargs 经 `capability_extras` 逐字转发（`_scan_capability_kwargs` 仅剔除 plan 资源键），`method`/`basis` 走 `TaskRequest.level` | **等价** |
| 计划（ReactionCoordinatePlan） | PES 直接构造（含 `lambda_values`/`reference_geometries`/`fixed_endpoints`/`xtb_scc_max_iterations`） | `capability_extras["scan_plan"]` 原始映射 → `build_scan_plan(raw_plan=…)`；`to_dict/from_dict` 扩展字段往返已补齐 | **等价**（需 from_dict 可选键扩展，见 §3） |
| 多坐标/同步语义 | `build_coordinate_plan` + `ReactionCoordinatePlan(coordinates=specs, points=…)` | 同一构造函数在 PES 侧完成 → dict 透传；`run_scan` 的 `points` 覆盖分支不触发（options.points 与 plan.points 相同） | **等价** |
| 坐标点数 | `scan_coordinates[0].n_points` | `plan.points` 同源；`run_scan` 额外做 `validate_atom_indices` | **等价**（多一道校验，正向） |
| 能力派生（constrained vs plain） | 无（直接调用） | `select_semantic` 依 `ScanOptions.coordinates` 数派生 `relaxed_scan`/`constrained_relaxed_scan` | **等价**（两能力在 orca/xtb 均 AVAILABLE，落到同一 `relaxed_scan` 能力方法） |

### 2. 错误（分类 / 呈现）

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| 后端抛错（OSError/RuntimeError/ValueError） | 原样向上抛（`run_pes_scan` 外层 `except Exception` 记 stage 失败后 re-raise） | `run_scan` 捕获 → `TaskResult(status=failed, error_kind=classify_failure, errors=(msg,))` → PES 重建 `success=False, message=msg` → `run_pes_scan` 抛 `RuntimeError("Relaxed scan failed: …")` | **取舍**：异常呈现从"原始异常"变为统一 `Relaxed scan failed` 门（测试与调用方都以此为契约）；`classify_failure` 分类保留在 `TaskResult.error_kind` |
| 结果类型不合法 | PES 自查 `isinstance(RelaxedScanResult)` → TypeError | `run_scan` 统一判型 → failed(error_kind=classify_failure) | **等价**（分类化） |
| `RelaxedScanResult.success` 语义（成功=扫描跑完，允许个别帧失败） | 直读 raw `success` | `run_scan` 的 `complete` 更严（全帧可用才算 completed）；PES 门按 **帧覆盖数 + message 回退标记** 重建 raw 语义：`status=="completed"` 或（`frame_count==plan.points` 且 `errors==("relaxed scan failed",)` 兜底文案）→ `success=True` | **取舍**：mark_failed_continue 逐帧失败、全帧在册 → 旧 success=True 继续抽帧（帧上 `optimization_converged=False`）→ 保持；abort 中途失败 → 帧不全 → 保持 fail-fast。残留边缘：raw `success=False` 且 message 为空且全帧在册（无已知驱动如此返回）会判为 True——记录为已知差异 |
| fail-fast 门位置 | `run_pes_scan` 的 `if not scan_result.success` | 不变（`_extract_frames`/SP/候选选择前） | **等价** |

### 3. 恢复（resume / 断点续算）

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| 扫描本体恢复 | 无（一次子进程/逐点循环） | 不变（`run_scan` 无 checkpoint） | **等价** |
| plan 往返保真 | plan 对象直传 | `scan_plan` dict 需 `ReactionCoordinatePlan.from_dict` 解析扩展字段（`lambda_values`/`reference_geometries`/`fixed_endpoints`/`xtb_scc_max_iterations`）——原 `from_dict/to_dict` 丢弃这些字段，本 todo 补齐可选键解析 | **取舍→等价**：扩展 `from_dict`（可选键、缺省行为不变）；`to_dict` 仅在非默认时补键，旧输出字节不变 |
| 逐帧失败信息 | `RelaxedScanPoint.metadata`（retry_history / frame_role / scf_converged）直读 | `point_callback` 收集器逐帧捕获 raw 点（完整 metadata）；原生 ORCA 扫描忽略 callback（旧行为一致，native 元数据本就为空）；Fake/无回调时从 `scan_profile.json` + `scan_frame_%03d.xyz` 重建（metadata 空 = native 语义） | **等价**（点回调通道 + 产物回退） |

### 4. 产物（科学文件 / 平台文件）

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| `scan_frames/frame_%03d.xyz`（PES 抽帧产物） | `_extract_frames` 写 | 不变 | **等价** |
| `scan_frame_%03d.xyz` + `scan_profile.json`（任务层科学产物） | 无 | `run_scan._frame_records` 落在 `scan_dir/` 根（`scan_frames/` 不冲突） | **取舍**：新增两个任务层科学文件（保留原始帧索引的几何 + 逐帧 progress/coordinate_values/energy 台账），是重建通道的数据源；平台产物（RESULT/、result_manifest）不受影响 |
| input.xyz / 后端自有输出 | 后端写 | 不变（kwargs 逐字透传，output_dir 同为 scan_dir） | **等价** |

### 5. 缓存键

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| 扫描本体 | 无缓存 | 无缓存（`run_scan` 无 CacheStore） | **等价** |
| 单点缓存 | `cache=sp_spec.resume`，`cache_profile="pes_scan:{optimizer_level_fingerprint}"` | 不变（`_run_single_points` 未改） | **等价**（缓存键 = 任务+后端+有效 ResolvedCalculationSpec+charge/mult+输入哈希+版本，profile 仍混入扫描级指纹——不同扫描级能量绝不复用） |

### 6. 并发 / 进度

| 项 | 旧 | 新 | 结论 |
|---|---|---|---|
| 扫描进度 | `point_callback=snapshot_writer.publish_point` 逐帧发布 | `capability_extras["point_callback"]` = 收集器→转发 publish_point；native ORCA 依旧忽略回调（与旧一致，live 读增量产物） | **等价** |
| 单点并发 | `_sp_resource_plan` 把 nproc 拆成 workers×per_job（`resources.nproc`+`executables.orca.nproc` 同步下调） | 不变 | **等价** |
| 单点回调 | `progress_callback`/`on_frame_start`/`on_frame_done`（缓存命中/失败也回放） | 不变（`run_batch` 的 `on_item_start`/`on_item_done` 钩子） | **等价** |
| 阶段进度（ProgressReporter） | prepare/validate/…/run_single_points stages | 不变（`run_pes_scan` 管线未动） | **等价** |

---

## 二、`_run_single_points` → 通用批量执行器（执行函数 `run_singlepoint`）

todo 17 已接线：`BatchSinglePointExecutor` → `prepare_frames`/`run_prepared_frames` →
`cccp.calculation.batch.run_batch`，每帧执行 = `cccp.calculation.tasks.singlepoint.run_singlepoint`
（`TaskContext(backend=…, capability_extras=…)` 运行时 seam，与 `primitives/scan.py::execute_scan`
同一契约：持有方代验 runtime precheck，任务层仍做 `validate_request`/`classify_failure`/结果归一化）。

| 维度 | 差异 | 结论 |
|---|---|---|
| 参数 | SP 级有效参数（method/basis/solvent/dispersion/…）由 `single_point_level(sp_spec)` 一次性规范化后同时喂执行器与帧台账 | **等价**（三路径同源，见 §4 测试） |
| 错误 | 单帧失败不株连（frame isolation）；`run_singlepoint` 的 `error_kind` 收敛为帧 `status="failed"` + error_message | **等价**（分类发生在任务层，批层只呈现） |
| 恢复 | `cache=sp_spec.resume` + 版本化缓存（CACHE_SCHEMA_VERSION=1，几何键旧缓存显式 miss） | **等价** |
| 产物 | 每帧独立 `output_dir`（`.batch_sp/<scope>/group_*/sp_%04d/`） | **等价** |
| 缓存键 | `cache_profile="pes_scan:{scan级指纹}"` | **等价**（保留） |
| 并发 | `max_workers` 由 `_sp_resource_plan` 拆分；回调逐帧回放 | **等价**（保留） |

---

## 三、`scan_plan` 原始映射往返缺口（本 todo 的唯一 cccp 改动）

`cccp.calculation.tasks.scan.build_scan_plan` 的 raw-plan 通道承诺"全保真（lambda_values /
explicit values / fixed endpoints …）"，但 `ReactionCoordinatePlan.from_dict` 只读
coordinates/points/coupling/start_from，`to_dict` 同样只写这四项——扩展字段在往返中被静默丢弃。
**修复**：`from_dict` 增加可选键解析（`lambda_values`、`fixed_endpoints`、`xtb_scc_max_iterations`、
`reference_geometries`），`to_dict` 仅在字段非默认时输出这些键（旧调用方输出字节不变）。
缺省行为完全不变；无扩展字段的 dict 解析结果与之前逐字节一致。

---

## 四、三路径一致性（普通 / 批量 / PES）

锁定测试：`tests/test_pes_search.py::test_sp_request_three_path_consistency`——同一 SP 请求经
（普通）`cccp.calculation.tasks.singlepoint.run_singlepoint`、（批量）`BatchSinglePointExecutor`、
（PES）`_run_single_points` 三条路径断言：

1. **校验**：同样的非法输入（坏几何）三条路径都拒绝——普通路径抛任务输入/几何错误，批量/PES 路径
   产出 `failed` 帧（批量路径同样经过 `run_singlepoint` 的 `validate_request`/`load_geometry`，不旁路）；
2. **有效参数**：抵达 `backend.single_point` 的科学参数（method/basis/charge/multiplicity 及其余非
   OFF 值）逐字相同；
3. **错误分类**：同一后端异常 → 三条路径均 `failed`（普通路径 `error_kind=classify_failure`），
   错误文案一致，且都不伪造能量；
4. **结果归一化**：成功路径能量均为同一 float、状态均为 completed。

对照过程发现并修复的差异（**取舍结论**）：

* **批量层有效参数投影**（已修复，`batch/_singlepoint_execution.py::_level_from_params`）：
  原实现把 `ResolvedCalculationSpec.effective_values()` 整体折进 `MethodSpec`——越集值被
  theory clamp（`outside_allowed_set`，如 B97-3c 的 basis def2-SVP→mTZVP）且方法默认值
  （auxJ/dispersion 等）被渲染成显式 kwargs（普通路径不渲染，交由下游按同一方法元数据物化）。
  黄金契约（`tests/baseline/cccp_calculation_goldens`，`render_backend_input` 逐字透传 requested）
  是执行语义的真源；现改为**仅投影 explicit 来源字段的 requested 值**，三条路径渲染逐字一致。
  缓存键不动（仍 = effective 值签名，QA 场景"缓存键变化"保持不变）。
* **composite 方法 basis 锁**（取舍，保留）：PES 的 `single_point_level` 对 3c/复合方法把 basis
  锁为 None（`test_v3_composite_method_locked_before_backend` 黄金语义，方法自带基组）；
  普通/批量路径按逐字语义保留用户 basis。一致性测试使用非复合方法（wB97X-D4）；复合+越集基组
  的差异是**目录层锁定语义**，非批量层旁路。
* **OFF-token ≡ 缺省**（取舍，已验证）：`dispersion/solvent_model/ri_approximation` 的
  `"none"` 显式关闭令牌与缺省在 `ORCAInterface._build_input_blocks` 渲染逐字节一致（实测）；
  一致性比较按语义投影过滤 `None`/`""`/`"none"`。`output_name`（`sp_%04d`）为逐帧隔离命名，
  非科学参数，不参与比较。

---

## 五、新工作流改线（XtbPathSearch / OrcaGradient）

* `src/acp/workflows/xtb_path.py`：`get_backend("xtb")` 直构 + `backend.is_available()` +
  `backend.path_search(...)` 删除；改调 `cccp.calculation.tasks.xtb_path_search.run_xtb_path_search`
  （请求经 `acp.calculations.legacy_adapters.pes2ts_xtb_path_to_task_request` 映射：
  `--path-config` 的 `path_inp_text`/`extra_args` 原文走 `BackendInputFragment`，charge/multiplicity/
  gfn/uhf/seed/threads/timeout 落标准字段；`TaskRequest.output_dir=run_dir`）。`start.xyz`/`end.xyz`
  原文写入与全部 RESULT 产物（`pes_profile_v2`、`path_frames/`、`xtbpath.xyz`、result_manifest v2）
  语义不变。帧路径经 `WORK/07_PATH/xtb_path_001/path_frames/path_frame_%03d.xyz`（任务/接口的确定性
  产物位置）取回，能量取 `XtbPathSearchPayload.frames[].energy_hartree`，轨迹取 `trajectory_ref`。
  失败 → `XtbPathSearchError`（`BackendUnavailableError`/`UnsupportedCapabilityError`/`SoftwareNotFoundError`
  与任务 failed 结果统一映射为 `XTB_PATH_E_XTB`，可用性文案含 "unavailable"）。
* `src/acp/workflows/orca_gradient.py`：同理改调 `run_orca_gradient`（`pes2ts_orca_gradient_to_task_request`；
  `--gradient-config` 的 `route_extras`/`extra_blocks`/`output_name` 走 fragment；method/basis/scf 落
  `level`）。产物（`gradient.json`、energy、geometry、result_manifest v2）语义不变。
  **取舍**：`gradient_source` 不在 `TaskResult` 契约内——从产物推导：存在 `engrad` 产物 →
  `engrad_file:<name>`（与 `_engrad_artifacts` 的注册条件互逆），否则 `output_block:CARTESIAN GRADIENT`
  （与 `ORCABackend.single_point_gradient` 的 source 赋值互逆）；残余边缘：engrad 文件在产物收集前
  消失时标注降级为 output_block（数值不受影响）。
  失败 → `OrcaGradientError`（可用性类 → `ORCA_GRADIENT_E_BACKEND` 且文案含 "unavailable"；
  梯度缺失/行数不符 → `ORCA_GRADIENT_E_GRADIENT`）。
* **无第二条 backend-direct 路径**：两工作流内不再出现 `get_backend`/`is_available()`/能力直调；
  可用性判定由任务层 `precheck_runtime` + 接口 `SoftwareNotFoundError` 承担。
